"""Export resumable, per-paper sparse chemical-fiber template projections."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export sparse, evidence-grounded template JSON per paper."
    )
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--mapped-only",
        action="store_true",
        help="Exclude facts that do not yet have a local mapping rule.",
    )
    return parser.parse_args()


def _configure_environment(args: argparse.Namespace) -> None:
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
    os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")
    os.environ.setdefault("REDIS_ENABLED", "false")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _safe_filename_stem(value: str, *, max_length: int = 120) -> str:
    cleaned = _INVALID_FILENAME_RE.sub("_", value).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = "paper"
    if cleaned.upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:max_length].rstrip(" .") or "paper"


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _source_signature(paper, samples, facts, records, evidence_items) -> str:
    payload = {
        "paper": [paper.id, _iso(paper.updated_at), paper.status],
        "samples": [[item.id, _iso(item.created_at)] for item in samples],
        "facts": [
            [item.id, _iso(item.created_at), item.assignment_status, item.confidence]
            for item in facts
        ],
        "records": [
            [item.id, _iso(item.updated_at), item.review_status]
            for item in records
        ],
        "evidence": [[item.id, _iso(item.created_at)] for item in evidence_items],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


async def _run(args: argparse.Namespace) -> dict:
    import app.models  # noqa: F401
    from sqlalchemy import select

    from app.core.database import async_session_factory, close_database
    from app.models.candidate_record import CandidateRecord
    from app.models.evidence_item import EvidenceItem
    from app.models.fact_candidate import FactCandidate
    from app.models.paper import Paper
    from app.models.project import Project
    from app.models.sample_catalog import SampleCatalog
    from app.services.template_projection import build_template_projection

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "template_projection_manifest.json"
    previous: dict[str, dict] = {}
    if manifest_path.is_file():
        try:
            previous_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            previous = {
                str(item.get("paper_id")): item
                for item in previous_payload.get("papers", [])
                if isinstance(item, dict)
            }
        except (OSError, json.JSONDecodeError):
            previous = {}

    report = {
        "project_id": args.project_id,
        "output_dir": str(output_dir),
        "include_unmapped": not args.mapped_only,
        "started_at": _utcnow().isoformat(),
        "updated_at": _utcnow().isoformat(),
        "completed": 0,
        "resumed": 0,
        "failed": 0,
        "papers": [],
    }
    entries: dict[str, dict] = {}

    try:
        async with async_session_factory() as db:
            project = await db.get(Project, args.project_id)
            if project is None or project.archived_at is not None:
                raise RuntimeError(f"Project {args.project_id} does not exist")

            paper_result = await db.execute(
                select(Paper)
                .where(
                    Paper.project_id == args.project_id,
                    Paper.status.in_(["review", "completed"]),
                )
                .order_by(Paper.id)
            )
            papers = list(paper_result.scalars().all())
            if args.limit > 0:
                papers = papers[: args.limit]

            for index, paper in enumerate(papers, start=1):
                try:
                    sample_result = await db.execute(
                        select(SampleCatalog)
                        .where(
                            SampleCatalog.project_id == args.project_id,
                            SampleCatalog.paper_id == paper.id,
                        )
                        .order_by(SampleCatalog.id)
                    )
                    fact_result = await db.execute(
                        select(FactCandidate)
                        .where(
                            FactCandidate.project_id == args.project_id,
                            FactCandidate.paper_id == paper.id,
                        )
                        .order_by(FactCandidate.id)
                    )
                    record_result = await db.execute(
                        select(CandidateRecord)
                        .where(
                            CandidateRecord.project_id == args.project_id,
                            CandidateRecord.source_paper_id == paper.id,
                        )
                        .order_by(CandidateRecord.id)
                    )
                    evidence_result = await db.execute(
                        select(EvidenceItem)
                        .where(
                            EvidenceItem.project_id == args.project_id,
                            EvidenceItem.paper_id == paper.id,
                        )
                        .order_by(EvidenceItem.id)
                    )
                    samples = list(sample_result.scalars().all())
                    facts = list(fact_result.scalars().all())
                    records = list(record_result.scalars().all())
                    evidence_items = list(evidence_result.scalars().all())
                    signature = _source_signature(
                        paper, samples, facts, records, evidence_items
                    )
                    source_stem = _safe_filename_stem(
                        Path(paper.original_filename).stem
                    )
                    output_path = (
                        output_dir / f"P{paper.id:06d}_{source_stem}.json"
                    )
                    previous_entry = previous.get(str(paper.id), {})
                    if (
                        not args.overwrite
                        and output_path.is_file()
                        and previous_entry.get("source_signature") == signature
                    ):
                        entry = {
                            **previous_entry,
                            "status": "resumed",
                            "validated_at": _utcnow().isoformat(),
                        }
                        report["resumed"] += 1
                        entries[str(paper.id)] = entry
                        print(
                            f"[template] {index}/{len(papers)} resume "
                            f"{output_path.name}",
                            flush=True,
                        )
                        continue

                    projection = build_template_projection(
                        paper=paper,
                        samples=samples,
                        facts=facts,
                        records=records,
                        evidence_items=evidence_items,
                        include_unmapped=not args.mapped_only,
                    )
                    await asyncio.to_thread(
                        _write_json_atomic,
                        output_path,
                        projection,
                    )
                    entry = {
                        "paper_id": paper.id,
                        "filename": paper.original_filename,
                        "projection_path": str(output_path),
                        "status": "completed",
                        "source_signature": signature,
                        "quality": projection["quality"],
                        "exported_at": _utcnow().isoformat(),
                    }
                    report["completed"] += 1
                    entries[str(paper.id)] = entry
                    print(
                        f"[template] {index}/{len(papers)} ok "
                        f"{output_path.name} ({projection['quality']['value_count']} values)",
                        flush=True,
                    )
                except Exception as exc:
                    entry = {
                        "paper_id": paper.id,
                        "filename": paper.original_filename,
                        "status": "failed",
                        "error": f"{exc.__class__.__name__}: {exc}"[:2000],
                    }
                    report["failed"] += 1
                    entries[str(paper.id)] = entry
                    print(
                        f"[template] {index}/{len(papers)} failed "
                        f"{paper.original_filename}: {exc}",
                        flush=True,
                    )
                report["papers"] = [
                    entries[key]
                    for key in sorted(entries, key=lambda value: int(value))
                ]
                report["updated_at"] = _utcnow().isoformat()
                await asyncio.to_thread(_write_json_atomic, manifest_path, report)
    finally:
        await close_database()

    report["papers"] = [
        entries[key] for key in sorted(entries, key=lambda value: int(value))
    ]
    report["healthy"] = report["failed"] == 0
    report["finished_at"] = _utcnow().isoformat()
    report["updated_at"] = report["finished_at"]
    await asyncio.to_thread(_write_json_atomic, manifest_path, report)
    return report


def main() -> None:
    args = _parse_args()
    _configure_environment(args)
    report = asyncio.run(_run(args))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["healthy"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
