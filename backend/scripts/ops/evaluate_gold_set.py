"""Evaluate one extraction project against a versioned gold JSON file."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate extracted candidates against a curated gold set."
    )
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--gold-file", required=True)
    parser.add_argument("--report-file", required=True)
    parser.add_argument("--no-fail-exit", action="store_true")
    return parser.parse_args()


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


async def _load_project_snapshot(project_id: int) -> list[dict]:
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.candidate_record import CandidateRecord
    from app.models.paper import Paper

    async with async_session_factory() as db:
        paper_result = await db.execute(
            select(Paper)
            .where(Paper.project_id == project_id)
            .order_by(Paper.id.asc())
        )
        papers = list(paper_result.scalars().all())
        candidates_by_paper: dict[int, list[dict]] = {
            paper.id: [] for paper in papers
        }
        paper_ids = [paper.id for paper in papers]
        for index in range(0, len(paper_ids), 500):
            chunk = paper_ids[index : index + 500]
            candidate_result = await db.execute(
                select(CandidateRecord)
                .where(CandidateRecord.source_paper_id.in_(chunk))
                .order_by(CandidateRecord.id.asc())
            )
            for record in candidate_result.scalars().all():
                if str(record.review_status or "").casefold() in {"deleted", "已删除"}:
                    continue
                if str(record.candidate_status or "").casefold() == "rejected":
                    continue
                candidates_by_paper.setdefault(record.source_paper_id, []).append({
                    "sample_id": record.sample_id or "",
                    "metric": record.performance_metric or "",
                    "value": record.performance_value or "",
                    "unit": record.performance_unit or "",
                    "evidence": (
                        record.evidence_text
                        or record.performance_evidence
                        or ""
                    ),
                })

        return [
            {
                "paper_id": paper.id,
                "filename": paper.original_filename,
                "sha256": paper.content_sha256 or "",
                "document_type": paper.document_type or "",
                "candidates": candidates_by_paper.get(paper.id, []),
            }
            for paper in papers
        ]


async def _run(args: argparse.Namespace) -> dict:
    import app.models  # noqa: F401

    from app.core.database import close_database
    from app.core.schema_repair import ensure_runtime_schema
    from app.services.gold_evaluation import evaluate_gold_set

    try:
        await ensure_runtime_schema()
        gold_path = Path(args.gold_file).expanduser().resolve()
        gold_payload = json.loads(gold_path.read_text(encoding="utf-8"))
        actual_papers = await _load_project_snapshot(args.project_id)
        result = evaluate_gold_set(gold_payload, actual_papers)
        result.update({
            "project_id": args.project_id,
            "gold_file": str(gold_path),
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        })
        report_path = Path(args.report_file).expanduser().resolve()
        await asyncio.to_thread(_write_json_atomic, report_path, result)
        result["report_file"] = str(report_path)
        return result
    finally:
        await close_database()


def main() -> None:
    args = _parse_args()
    os.environ["DATABASE_URL"] = args.database_url
    os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")
    result = asyncio.run(_run(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["gate_passed"] and not args.no_fail_exit:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
