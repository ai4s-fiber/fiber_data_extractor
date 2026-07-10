"""Run extraction benchmarks against local PDFs.

Usage from backend/:
  $env:DASHSCOPE_API_KEY="..."
  python scripts/benchmark/run_extraction_benchmark.py --pdf-dir ./benchmark_pdfs
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from collections import Counter

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark PDF extraction speed and proxy quality.")
    parser.add_argument("--pdf-dir", required=True, help="Directory containing benchmark PDFs.")
    parser.add_argument("--api-key-env", default="DASHSCOPE_API_KEY", help="Environment variable holding the API key.")
    parser.add_argument("--base-url", default="https://dashscope.aliyuncs.com/compatible-mode/v1")
    parser.add_argument("--model", default="qwen3.7-plus")
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--model-mode", default="weak", choices=["weak", "strong", "auto"])
    parser.add_argument("--parser-strategy", default="legacy", choices=["legacy", "mineru_local", "mineru_cloud"])
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--pdf-name", default="", help="Optional exact PDF filename to benchmark.")
    parser.add_argument("--database-url", default="sqlite+aiosqlite:///./benchmark.db")
    parser.add_argument("--report-dir", default="./reports/benchmarks")
    return parser.parse_args()


@dataclass
class BenchmarkPaperResult:
    filename: str
    ok: bool
    elapsed_ms: float
    candidate_count: int = 0
    quality_score: float = 0.0
    coverage: dict[str, int] | None = None
    error: str = ""


def _quality_score(rows: list) -> tuple[float, dict[str, int]]:
    if not rows:
        return 0.0, {
            "rows": 0,
            "with_sample": 0,
            "with_metric": 0,
            "with_value": 0,
            "with_evidence": 0,
            "with_composition": 0,
            "with_process": 0,
            "with_structure": 0,
            "with_performance": 0,
        }

    coverage = {
        "rows": len(rows),
        "with_sample": 0,
        "with_metric": 0,
        "with_value": 0,
        "with_evidence": 0,
        "with_composition": 0,
        "with_process": 0,
        "with_structure": 0,
        "with_performance": 0,
    }
    for row in rows:
        if getattr(row, "sample_id", None):
            coverage["with_sample"] += 1
        if getattr(row, "performance_metric", None):
            coverage["with_metric"] += 1
        if getattr(row, "performance_value", None):
            coverage["with_value"] += 1
        if getattr(row, "evidence_text", None):
            coverage["with_evidence"] += 1
        if any(getattr(row, field, None) for field in (
            "composition_expression", "matrix_name", "additive_expression", "composition_evidence"
        )):
            coverage["with_composition"] += 1
        if any(getattr(row, field, None) for field in (
            "process_route", "spinning_method", "process_parameters", "post_treatment", "process_evidence"
        )):
            coverage["with_process"] += 1
        if any(getattr(row, field, None) for field in (
            "structure_methods", "structure_features", "structure_evidence"
        )):
            coverage["with_structure"] += 1
        if getattr(row, "performance_metric", None) and getattr(row, "performance_value", None):
            coverage["with_performance"] += 1

    weighted = (
        coverage["with_sample"]
        + coverage["with_metric"]
        + coverage["with_value"]
        + coverage["with_evidence"]
    )
    return round(weighted / (len(rows) * 4), 4), coverage


def _metrics_rollup(metrics) -> dict[str, int | float]:
    calls = metrics.calls
    return {
        "total_calls": metrics.total_calls,
        "stage2_calls": len([c for c in calls if c.get("stage") == "stage2_facts"]),
        "failed_calls": metrics.failed_calls,
        "total_latency_ms": round(metrics.total_latency_ms, 1),
        "prompt_chars": sum(int(c.get("prompt_chars") or 0) for c in calls),
        "response_chars": sum(int(c.get("response_chars") or 0) for c in calls),
        "prompt_tokens": sum(int(c.get("prompt_tokens") or 0) for c in calls),
        "completion_tokens": sum(int(c.get("completion_tokens") or 0) for c in calls),
        "total_tokens": sum(int(c.get("total_tokens") or 0) for c in calls),
        "capped_calls": len([c for c in calls if c.get("capped")]),
    }


async def _run(args: argparse.Namespace) -> dict:
    os.environ.setdefault("DATABASE_URL", args.database_url)
    os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")
    os.environ.setdefault("REDIS_ENABLED", "false")
    os.environ.setdefault("DEFAULT_PARSER_STRATEGY", args.parser_strategy)
    os.environ.setdefault("LLM_DISABLE_THINKING", "true")
    os.environ.setdefault("LLM_METRICS_LOCAL_ENABLED", "true")
    os.environ.setdefault("LLM_METRICS_DIR", "./reports/llm_metrics")

    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        raise SystemExit(f"Missing API key environment variable: {args.api_key_env}")

    import app.models  # noqa: F401
    from sqlalchemy import select

    from app.core.config import settings
    from app.core.database import async_session_factory
    from app.core.schema_repair import ensure_runtime_schema
    from app.models.candidate_record import CandidateRecord
    from app.models.extraction_job import ExtractionJob
    from app.models.fact_candidate import FactCandidate
    from app.models.paper import Paper
    from app.models.project import Project
    from app.services.extractor_v7 import V7ExtractorService
    from app.services.llm_metrics import get_job_summary

    await ensure_runtime_schema()
    report_root = Path(args.report_dir)
    report_root.mkdir(parents=True, exist_ok=True)
    upload_root = Path(settings.UPLOAD_DIR) / "benchmark"
    upload_root.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(Path(args.pdf_dir).glob("*.pdf"))
    if args.pdf_name:
        pdfs = [pdf for pdf in pdfs if pdf.name == args.pdf_name]
    pdfs = pdfs[: args.limit]
    if not pdfs:
        raise SystemExit(f"No matching PDFs found in {args.pdf_dir}")

    results: list[BenchmarkPaperResult] = []
    started_all = time.monotonic()

    def write_summary() -> dict:
        summary = {
            "model": args.model,
            "base_url": args.base_url,
            "model_mode": args.model_mode,
            "parser_strategy": args.parser_strategy,
            "elapsed_ms": round((time.monotonic() - started_all) * 1000, 1),
            "papers": [asdict(item) for item in results],
        }
        summary_path = report_root / "summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary

    async with async_session_factory() as db:
        project = Project(
            name=f"Benchmark {args.model}",
            description="Automated extraction benchmark",
            llm_provider=args.provider,
            llm_api_key=api_key,
            llm_base_url=args.base_url,
            llm_model=args.model,
        )
        db.add(project)
        await db.flush()

        for paper_index, pdf in enumerate(pdfs, start=1):
            print(f"[{paper_index}/{len(pdfs)}] start {pdf.name}", flush=True)
            key = f"benchmark/{int(time.time())}_{pdf.name}"
            target = Path(settings.UPLOAD_DIR) / key
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(pdf, target)
            paper = Paper(
                project_id=project.id,
                original_filename=pdf.name,
                file_object_key=key,
                paper_title=pdf.stem,
                status="uploaded",
            )
            db.add(paper)
            await db.flush()
            job = ExtractionJob(
                project_id=project.id,
                paper_id=paper.id,
                requested_mode=args.model_mode,
                resolved_mode=args.model_mode,
                parser_strategy=args.parser_strategy,
                status="running",
                step="benchmark",
                percent=0,
                model_provider=args.provider,
                model_name=args.model,
            )
            db.add(job)
            await db.flush()
            await db.commit()

            events: list[dict] = []

            async def progress(step: str, percent: int, message: str = "") -> None:
                events.append({
                    "t_ms": round((time.monotonic() - paper_started) * 1000, 1),
                    "step": step,
                    "percent": percent,
                    "message": message,
                })
                print(f"[{pdf.name}] {percent:3d}% {step} {message}", flush=True)

            paper_started = time.monotonic()
            try:
                result = await V7ExtractorService.run_full_pipeline_for_paper(
                    db,
                    paper.id,
                    progress_callback=progress,
                    model_mode=args.model_mode,
                    job_id=job.id,
                )
                elapsed_ms = round((time.monotonic() - paper_started) * 1000, 1)
                if result.get("error"):
                    results.append(BenchmarkPaperResult(pdf.name, False, elapsed_ms, error=result["error"]))
                    write_summary()
                    print(f"[{paper_index}/{len(pdfs)}] failed {pdf.name}: {result['error']}", flush=True)
                    continue
                rows = (await db.execute(
                    select(CandidateRecord).where(CandidateRecord.source_paper_id == paper.id)
                )).scalars().all()
                facts = (await db.execute(
                    select(FactCandidate).where(FactCandidate.paper_id == paper.id)
                )).scalars().all()
                score, coverage = _quality_score(rows)
                metrics = await get_job_summary(job.id)
                paper_report = {
                    "paper": pdf.name,
                    "elapsed_ms": elapsed_ms,
                    "events": events,
                    "llm_metrics": asdict(metrics),
                    "llm_rollup": _metrics_rollup(metrics),
                    "fact_type_counts": dict(Counter(f.fact_type for f in facts)),
                    "candidate_count": len(rows),
                    "fact_count": len(facts),
                    "quality_score": score,
                    "coverage": coverage,
                }
                (report_root / f"{pdf.stem}.benchmark.json").write_text(
                    json.dumps(paper_report, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                results.append(BenchmarkPaperResult(pdf.name, True, elapsed_ms, len(rows), score, coverage))
                write_summary()
                print(
                    f"[{paper_index}/{len(pdfs)}] done {pdf.name}: "
                    f"{elapsed_ms / 1000:.1f}s, rows={len(rows)}, quality={score}",
                    flush=True,
                )
            except Exception as exc:
                elapsed_ms = round((time.monotonic() - paper_started) * 1000, 1)
                results.append(BenchmarkPaperResult(pdf.name, False, elapsed_ms, error=str(exc)))
                write_summary()
                print(f"[{paper_index}/{len(pdfs)}] failed {pdf.name}: {exc}", flush=True)

    return write_summary()


def main() -> None:
    args = _parse_args()
    summary = asyncio.run(_run(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
