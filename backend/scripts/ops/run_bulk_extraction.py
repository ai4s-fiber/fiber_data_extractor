"""Run resumable MinerU Cloud + GPT extraction for a directory of PDFs.

Run this as the only extraction worker for the selected database. The command
uses MinerU's official batch-upload API, atomically prefills the normal parse
cache, and then feeds persistent strong-mode extraction jobs to the existing
bounded worker.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@dataclass(slots=True)
class SourceDocument:
    path: Path
    sha256: str


@dataclass(slots=True)
class PreparedDocument:
    source: SourceDocument
    paper_id: int
    job_id: int | None
    state: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resumable bulk extraction with MinerU Cloud and GPT strong mode."
    )
    parser.add_argument("--pdf-dir", required=True, help="Root directory containing PDFs.")
    parser.add_argument("--project-id", type=int, default=0)
    parser.add_argument("--project-name", default="Bulk literature extraction")
    parser.add_argument("--api-key-env", default="AIGW_API_KEY")
    parser.add_argument("--base-url", default="https://aigw.sotatts.online/v1")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--limit", type=int, default=0, help="0 means all PDFs.")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--upload-concurrency", type=int, default=8)
    parser.add_argument("--hash-concurrency", type=int, default=4)
    parser.add_argument("--max-jobs", type=int, default=3)
    parser.add_argument(
        "--copy-mode",
        choices=["hardlink", "copy"],
        default="hardlink",
        help="Hard links save disk space when source and uploads are on one volume.",
    )
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--reextract-completed", action="store_true")
    parser.add_argument("--no-preparse", action="store_true")
    parser.add_argument("--no-recursive", action="store_true")
    parser.add_argument("--report-dir", default="./reports/bulk")
    return parser.parse_args()


def _configure_environment(args: argparse.Namespace) -> None:
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
    os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")
    os.environ.setdefault("REDIS_ENABLED", "false")
    os.environ["DEFAULT_PARSER_STRATEGY"] = "mineru_cloud"
    os.environ["EXTRACTION_MAX_CONCURRENT_JOBS"] = str(max(1, args.max_jobs))
    os.environ.setdefault("LLM_GLOBAL_MAX_CONCURRENT_CALLS", "16")
    os.environ.setdefault("LLM_BATCH_MAX_CONCURRENT_CALLS", "12")
    os.environ.setdefault("LLM_INTERACTIVE_RESERVED_CALLS", "4")
    os.environ["LLM_METRICS_DIR"] = str(
        (Path(args.report_dir).expanduser().resolve() / "llm_metrics")
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _chunks(items: list[PreparedDocument], size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


async def _hash_documents(
    paths: list[Path],
    concurrency: int,
) -> list[SourceDocument]:
    from app.services.document_context import file_sha256

    semaphore = asyncio.Semaphore(max(1, concurrency))
    completed = 0
    lock = asyncio.Lock()

    async def hash_one(path: Path) -> SourceDocument:
        nonlocal completed
        async with semaphore:
            digest = await asyncio.to_thread(file_sha256, path)
        async with lock:
            completed += 1
            if completed == len(paths) or completed % 100 == 0:
                print(f"[hash] {completed}/{len(paths)}", flush=True)
        return SourceDocument(path=path, sha256=digest)

    hashed = await asyncio.gather(*(hash_one(path) for path in paths))
    unique: dict[str, SourceDocument] = {}
    for item in hashed:
        unique.setdefault(item.sha256, item)
    return list(unique.values())


def _materialize_pdf(
    source: SourceDocument,
    project_id: int,
    copy_mode: str,
) -> str:
    from app.core.config import settings
    from app.services.document_context import file_sha256

    relative = Path("bulk") / str(project_id) / source.sha256[:2] / f"{source.sha256}.pdf"
    target = Path(settings.UPLOAD_DIR) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if file_sha256(target) != source.sha256:
            raise RuntimeError(f"Bulk target hash mismatch: {target}")
        return relative.as_posix()

    if copy_mode == "hardlink":
        try:
            os.link(source.path, target)
        except OSError:
            shutil.copy2(source.path, target)
    else:
        shutil.copy2(source.path, target)
    return relative.as_posix()


async def _get_or_create_project(args: argparse.Namespace, api_key: str) -> int:
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.project import Project

    async with async_session_factory() as db:
        project = None
        if args.project_id:
            project = await db.get(Project, args.project_id)
            if project is None or project.archived_at is not None:
                raise RuntimeError(f"Project {args.project_id} does not exist")
        else:
            result = await db.execute(
                select(Project)
                .where(
                    Project.name == args.project_name,
                    Project.archived_at.is_(None),
                )
                .order_by(Project.id.asc())
                .limit(1)
            )
            project = result.scalar_one_or_none()
        if project is None:
            project = Project(name=args.project_name)
            db.add(project)
            await db.flush()

        project.llm_provider = args.provider
        project.llm_api_key = api_key
        project.llm_base_url = args.base_url
        project.llm_model = args.model
        project.updated_at = _utcnow()
        await db.commit()
        return project.id


async def _backfill_project_hashes(project_id: int) -> int:
    from sqlalchemy import select

    from app.core.config import settings
    from app.core.database import async_session_factory
    from app.models.paper import Paper
    from app.services.document_context import file_sha256

    updated = 0
    async with async_session_factory() as db:
        result = await db.execute(
            select(Paper).where(
                Paper.project_id == project_id,
                Paper.content_sha256.is_(None),
            )
        )
        for paper in result.scalars().all():
            path = Path(settings.UPLOAD_DIR) / paper.file_object_key
            if not path.is_file():
                continue
            paper.content_sha256 = await asyncio.to_thread(file_sha256, path)
            updated += 1
        if updated:
            await db.commit()
    return updated


async def _prepare_document(
    source: SourceDocument,
    *,
    project_id: int,
    copy_mode: str,
    retry_failed: bool,
    reextract_completed: bool,
) -> PreparedDocument:
    from sqlalchemy import select

    from app.core.config import settings
    from app.core.database import async_session_factory
    from app.models.extraction_job import ExtractionJob
    from app.models.paper import Paper
    from app.services.extraction_jobs import ACTIVE_JOB_STATUSES

    async with async_session_factory() as db:
        result = await db.execute(
            select(Paper)
            .where(
                Paper.project_id == project_id,
                Paper.content_sha256 == source.sha256,
            )
            .order_by(Paper.id.asc())
            .limit(1)
        )
        paper = result.scalar_one_or_none()
        if paper is None:
            file_key = await asyncio.to_thread(
                _materialize_pdf,
                source,
                project_id,
                copy_mode,
            )
            paper = Paper(
                project_id=project_id,
                original_filename=source.path.name,
                file_object_key=file_key,
                content_sha256=source.sha256,
                paper_title=source.path.stem,
                status="uploaded",
            )
            db.add(paper)
            await db.flush()
        else:
            stored_path = Path(settings.UPLOAD_DIR) / paper.file_object_key
            if not stored_path.is_file():
                paper.file_object_key = await asyncio.to_thread(
                    _materialize_pdf,
                    source,
                    project_id,
                    copy_mode,
                )

        latest_result = await db.execute(
            select(ExtractionJob)
            .where(ExtractionJob.paper_id == paper.id)
            .order_by(ExtractionJob.created_at.desc(), ExtractionJob.id.desc())
            .limit(1)
        )
        latest = latest_result.scalar_one_or_none()
        await db.commit()

        if latest is not None and latest.status in ACTIVE_JOB_STATUSES:
            return PreparedDocument(source, paper.id, latest.id, "resume")
        if paper.status in {"review", "completed"} and not reextract_completed:
            return PreparedDocument(source, paper.id, None, "skip_completed")
        if latest is not None and latest.status == "failed" and not retry_failed:
            return PreparedDocument(source, paper.id, None, "skip_failed")
        return PreparedDocument(source, paper.id, None, "ready")


async def _queue_document(item: PreparedDocument, project_id: int) -> int:
    from app.core.database import async_session_factory
    from app.models.extraction_job import ExtractionJob
    from app.models.paper import Paper
    from app.services.extraction_jobs import extraction_job_backend

    job_id = item.job_id
    if job_id is None:
        async with async_session_factory() as db:
            paper = await db.get(Paper, item.paper_id)
            if paper is None:
                raise RuntimeError(f"Paper {item.paper_id} disappeared before queueing")
            paper.status = "queued"
            paper.updated_at = _utcnow()
            job = ExtractionJob(
                project_id=project_id,
                paper_id=paper.id,
                requested_mode="strong",
                parser_strategy="mineru_cloud",
                status="queued",
                step="starting",
                percent=0,
            )
            db.add(job)
            await db.flush()
            job_id = job.id
            await db.commit()
    await extraction_job_backend.enqueue(job_id)
    return job_id


async def _record_preparse_failure(
    item: PreparedDocument,
    project_id: int,
    error: BaseException,
) -> int:
    from app.core.database import async_session_factory
    from app.models.extraction_job import ExtractionJob
    from app.models.paper import Paper

    async with async_session_factory() as db:
        job = await db.get(ExtractionJob, item.job_id) if item.job_id else None
        if job is None:
            job = ExtractionJob(
                project_id=project_id,
                paper_id=item.paper_id,
                requested_mode="strong",
                parser_strategy="mineru_cloud",
            )
            db.add(job)
            await db.flush()
        job.status = "failed"
        job.step = "failed"
        job.error_code = str(getattr(error, "error_code", "mineru_error"))[:50]
        job.error_message = str(error)[:2000]
        job.finished_at = _utcnow()
        job.updated_at = _utcnow()
        paper = await db.get(Paper, item.paper_id)
        if paper is not None:
            paper.status = "failed"
            paper.updated_at = _utcnow()
        await db.commit()
        return job.id


async def _terminal_counts(job_ids: set[int]) -> dict[str, int]:
    from collections import Counter

    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.extraction_job import ExtractionJob

    if not job_ids:
        return {}
    async with async_session_factory() as db:
        result = await db.execute(
            select(ExtractionJob.status).where(ExtractionJob.id.in_(job_ids))
        )
        return dict(Counter(str(row[0]) for row in result.fetchall()))


async def _collect_result_summary(job_ids: set[int]) -> dict:
    """Audit persisted outputs for this run without loading result payloads."""
    from collections import defaultdict

    from sqlalchemy import and_, case, func, select

    from app.core.database import async_session_factory
    from app.models.candidate_record import CandidateRecord
    from app.models.extraction_job import ExtractionJob
    from app.models.fact_candidate import FactCandidate
    from app.models.paper import Paper
    from app.models.sample_catalog import SampleCatalog

    empty = {
        "healthy": True,
        "papers": [],
        "total_samples": 0,
        "total_facts": 0,
        "total_candidates": 0,
        "qa_flagged_candidates": 0,
        "missing_evidence_candidates": 0,
        "exact_duplicate_rows": 0,
        "failed_jobs": 0,
        "completed_zero_candidate_papers": 0,
        "attention_required_papers": 0,
    }
    if not job_ids:
        return empty

    async with async_session_factory() as db:
        jobs_result = await db.execute(
            select(ExtractionJob, Paper)
            .join(Paper, Paper.id == ExtractionJob.paper_id)
            .where(ExtractionJob.id.in_(job_ids))
            .order_by(ExtractionJob.id.asc())
        )
        jobs = list(jobs_result.all())
        paper_ids = {paper.id for _, paper in jobs}
        if not paper_ids:
            return empty

        async def grouped_count(model, paper_column) -> dict[int, int]:
            result = await db.execute(
                select(paper_column, func.count(model.id))
                .where(paper_column.in_(paper_ids))
                .group_by(paper_column)
            )
            return {int(paper_id): int(count) for paper_id, count in result.all()}

        sample_counts = await grouped_count(SampleCatalog, SampleCatalog.paper_id)
        fact_counts = await grouped_count(FactCandidate, FactCandidate.paper_id)

        no_evidence = and_(
            func.trim(func.coalesce(CandidateRecord.evidence_text, "")) == "",
            func.trim(func.coalesce(CandidateRecord.performance_evidence, "")) == "",
        )
        candidate_result = await db.execute(
            select(
                CandidateRecord.source_paper_id,
                func.count(CandidateRecord.id),
                func.sum(case((
                    CandidateRecord.reviewer_comment.like("%qa_reason=%"), 1
                ), else_=0)),
                func.sum(case((no_evidence, 1), else_=0)),
            )
            .where(CandidateRecord.source_paper_id.in_(paper_ids))
            .group_by(CandidateRecord.source_paper_id)
        )
        candidate_counts: dict[int, int] = {}
        qa_counts: dict[int, int] = {}
        missing_evidence_counts: dict[int, int] = {}
        for paper_id, count, qa_count, missing_count in candidate_result.all():
            candidate_counts[int(paper_id)] = int(count or 0)
            qa_counts[int(paper_id)] = int(qa_count or 0)
            missing_evidence_counts[int(paper_id)] = int(missing_count or 0)

        duplicate_result = await db.execute(
            select(
                CandidateRecord.source_paper_id,
                func.count(CandidateRecord.id).label("copies"),
            )
            .where(CandidateRecord.source_paper_id.in_(paper_ids))
            .group_by(
                CandidateRecord.source_paper_id,
                CandidateRecord.sample_id,
                CandidateRecord.performance_metric,
                CandidateRecord.performance_value,
                CandidateRecord.performance_unit,
            )
            .having(func.count(CandidateRecord.id) > 1)
        )
        duplicate_counts: dict[int, int] = defaultdict(int)
        for paper_id, copies in duplicate_result.all():
            duplicate_counts[int(paper_id)] += int(copies) - 1

    papers = []
    for job, paper in jobs:
        status = str(job.status or "")
        candidate_count = candidate_counts.get(paper.id, 0)
        missing_count = missing_evidence_counts.get(paper.id, 0)
        duplicate_count = duplicate_counts.get(paper.id, 0)
        needs_attention = (
            status != "completed"
            or missing_count > 0
            or duplicate_count > 0
        )
        papers.append({
            "paper_id": paper.id,
            "job_id": job.id,
            "filename": paper.original_filename,
            "status": status,
            "error_code": job.error_code or "",
            "error_message": job.error_message or "",
            "samples": sample_counts.get(paper.id, 0),
            "facts": fact_counts.get(paper.id, 0),
            "candidates": candidate_count,
            "qa_flagged_candidates": qa_counts.get(paper.id, 0),
            "missing_evidence_candidates": missing_count,
            "exact_duplicate_rows": duplicate_count,
            "needs_attention": needs_attention,
        })

    summary = dict(empty)
    summary.update({
        "papers": papers,
        "total_samples": sum(item["samples"] for item in papers),
        "total_facts": sum(item["facts"] for item in papers),
        "total_candidates": sum(item["candidates"] for item in papers),
        "qa_flagged_candidates": sum(
            item["qa_flagged_candidates"] for item in papers
        ),
        "missing_evidence_candidates": sum(
            item["missing_evidence_candidates"] for item in papers
        ),
        "exact_duplicate_rows": sum(
            item["exact_duplicate_rows"] for item in papers
        ),
        "failed_jobs": sum(item["status"] != "completed" for item in papers),
        "completed_zero_candidate_papers": sum(
            item["status"] == "completed" and item["candidates"] == 0
            for item in papers
        ),
        "attention_required_papers": sum(
            item["needs_attention"] for item in papers
        ),
    })
    summary["healthy"] = summary["attention_required_papers"] == 0
    return summary


async def _wait_for_jobs(job_ids: set[int], report, report_path: Path) -> None:
    while True:
        counts = await _terminal_counts(job_ids)
        active = counts.get("queued", 0) + counts.get("running", 0)
        report["job_status_counts"] = counts
        report["updated_at"] = _utcnow().isoformat()
        await asyncio.to_thread(_write_json_atomic, report_path, report)
        print(f"[jobs] {counts}", flush=True)
        if active == 0:
            return
        await asyncio.sleep(10)


async def _run(args: argparse.Namespace) -> dict:
    import app.models  # noqa: F401

    from app.core.config import settings
    from app.core.database import close_database
    from app.core.redis_client import close_redis
    from app.core.schema_repair import ensure_runtime_schema
    from app.services.document_context import (
        load_shared_mineru_artifact,
        persist_shared_mineru_artifact,
    )
    from app.services.extraction_jobs import extraction_job_backend
    from app.services.mineru_client import MinerUClient

    started = time.monotonic()
    api_key = (os.environ.get(args.api_key_env) or "").strip()
    if not api_key:
        raise SystemExit(f"Missing API key environment variable: {args.api_key_env}")
    if not args.no_preparse and not settings.MINERU_CLOUD_TOKEN.strip():
        raise SystemExit("MINERU_CLOUD_TOKEN is required for MinerU Cloud preparse")
    if not 1 <= args.batch_size <= 200:
        raise SystemExit("--batch-size must be between 1 and 200")

    pdf_root = Path(args.pdf_dir).expanduser().resolve()
    pattern = "*.pdf" if args.no_recursive else "**/*.pdf"
    paths = sorted(path for path in pdf_root.glob(pattern) if path.is_file())
    if args.limit > 0:
        paths = paths[: args.limit]
    if not paths:
        raise SystemExit(f"No PDF files found under {pdf_root}")

    report_path = Path(args.report_dir) / "bulk_summary.json"
    report = {
        "source_root": str(pdf_root),
        "model": args.model,
        "model_mode": "strong",
        "parser_strategy": "mineru_cloud",
        "started_at": _utcnow().isoformat(),
        "scanned_files": len(paths),
        "unique_files": 0,
        "duplicate_files": 0,
        "backfilled_hashes": 0,
        "cache_hits": 0,
        "mineru_parsed": 0,
        "mineru_failed": 0,
        "mineru_batches_resumed": 0,
        "queued_jobs": 0,
        "resumed_jobs": 0,
        "skipped_completed": 0,
        "skipped_failed": 0,
        "job_status_counts": {},
    }

    try:
        await ensure_runtime_schema()
        project_id = await _get_or_create_project(args, api_key)
        report_path = (
            Path(args.report_dir) / f"bulk_project_{project_id}_summary.json"
        )
        previous_report = {}
        if report_path.is_file():
            try:
                previous_report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous_report = {}
        report["project_id"] = project_id
        report["backfilled_hashes"] = await _backfill_project_hashes(project_id)
        await extraction_job_backend.recover_interrupted_jobs()

        documents = await _hash_documents(paths, args.hash_concurrency)
        report["unique_files"] = len(documents)
        report["duplicate_files"] = len(paths) - len(documents)

        prepared = []
        for index, source in enumerate(documents, start=1):
            item = await _prepare_document(
                source,
                project_id=project_id,
                copy_mode=args.copy_mode,
                retry_failed=args.retry_failed,
                reextract_completed=args.reextract_completed,
            )
            if item.state == "skip_completed":
                report["skipped_completed"] += 1
            elif item.state == "skip_failed":
                report["skipped_failed"] += 1
            else:
                prepared.append(item)
            if index == len(documents) or index % 100 == 0:
                print(f"[prepare] {index}/{len(documents)}", flush=True)

        from app.services.mineru_client import MinerUTaskFailed

        tracked_jobs: set[int] = set()
        client = MinerUClient()

        async def checkpoint() -> None:
            report["updated_at"] = _utcnow().isoformat()
            await asyncio.to_thread(_write_json_atomic, report_path, report)

        async def queue_item(item: PreparedDocument) -> None:
            job_id = await _queue_document(item, project_id)
            tracked_jobs.add(job_id)
            report["resumed_jobs" if item.job_id else "queued_jobs"] += 1

        async def consume_outcome(outcome, by_path) -> PreparedDocument:
            item = by_path[outcome.path.resolve()]
            if outcome.ok and outcome.result is not None:
                await asyncio.to_thread(
                    persist_shared_mineru_artifact,
                    outcome.result,
                    item.source.sha256,
                    "mineru_cloud",
                )
                report["mineru_parsed"] += 1
                await queue_item(item)
                print(
                    f"[mineru] ok {item.source.path.name} "
                    f"({report['mineru_parsed']} parsed)",
                    flush=True,
                )
            else:
                report["mineru_failed"] += 1
                error = outcome.error or RuntimeError(
                    "MinerU Cloud returned an empty batch outcome"
                )
                job_id = await _record_preparse_failure(
                    item,
                    project_id,
                    error,
                )
                tracked_jobs.add(job_id)
                print(
                    f"[mineru] failed {item.source.path.name}: {error}",
                    flush=True,
                )
            await checkpoint()
            return item

        previous_active = previous_report.get("active_mineru_batch")
        if (
            isinstance(previous_active, dict)
            and previous_report.get("source_root") == str(pdf_root)
            and not args.no_preparse
        ):
            prepared_by_sha = {item.source.sha256: item for item in prepared}
            resume_mapping = {}
            resume_by_path = {}
            handled_hashes = set()
            for entry in previous_active.get("items", []):
                if not isinstance(entry, dict):
                    continue
                item = prepared_by_sha.get(str(entry.get("sha256") or ""))
                data_id = str(entry.get("data_id") or "")
                if item is None or not data_id:
                    continue
                artifact = await asyncio.to_thread(
                    load_shared_mineru_artifact,
                    item.source.sha256,
                    "mineru_cloud",
                )
                if artifact is not None:
                    report["cache_hits"] += 1
                    await queue_item(item)
                    handled_hashes.add(item.source.sha256)
                    continue
                resume_mapping[data_id] = item.source.path
                resume_by_path[item.source.path.resolve()] = item

            batch_id = str(previous_active.get("batch_id") or "")
            if batch_id and resume_mapping:
                report["active_mineru_batch"] = {
                    "batch_id": batch_id,
                    "items": [
                        {
                            "data_id": data_id,
                            "sha256": resume_by_path[Path(path).resolve()].source.sha256,
                            "path": str(path),
                        }
                        for data_id, path in resume_mapping.items()
                    ],
                }
                await checkpoint()
                try:
                    async for outcome in client.iter_existing_cloud_batch(
                        batch_id,
                        resume_mapping,
                    ):
                        item = await consume_outcome(outcome, resume_by_path)
                        handled_hashes.add(item.source.sha256)
                except MinerUTaskFailed as exc:
                    if "-60012" not in str(exc) and "not found" not in str(exc).lower():
                        raise
                    print(
                        f"[mineru] previous batch expired; resubmitting unfinished files: {exc}",
                        flush=True,
                    )
                else:
                    report["mineru_batches_resumed"] += 1
            report.pop("active_mineru_batch", None)
            prepared = [
                item for item in prepared if item.source.sha256 not in handled_hashes
            ]
            await checkpoint()

        for batch_index, batch in enumerate(
            _chunks(prepared, args.batch_size),
            start=1,
        ):
            cached = []
            missing = []
            for item in batch:
                artifact = await asyncio.to_thread(
                    load_shared_mineru_artifact,
                    item.source.sha256,
                    "mineru_cloud",
                )
                if artifact is None and not args.no_preparse:
                    missing.append(item)
                else:
                    cached.append(item)

            for item in cached:
                if not args.no_preparse:
                    report["cache_hits"] += 1
                await queue_item(item)

            if missing:
                by_path = {item.source.path.resolve(): item for item in missing}

                async def remember_batch(
                    batch_id: str,
                    path_by_data_id: dict[str, Path],
                ) -> None:
                    report["active_mineru_batch"] = {
                        "batch_id": batch_id,
                        "items": [
                            {
                                "data_id": data_id,
                                "sha256": by_path[path.resolve()].source.sha256,
                                "path": str(path),
                            }
                            for data_id, path in path_by_data_id.items()
                        ],
                    }
                    await checkpoint()

                async for outcome in client.iter_parse_pdfs_cloud_batch(
                    [item.source.path for item in missing],
                    upload_concurrency=args.upload_concurrency,
                    on_submitted=remember_batch,
                ):
                    await consume_outcome(outcome, by_path)
                report.pop("active_mineru_batch", None)

            report["last_batch"] = batch_index
            await checkpoint()

        await extraction_job_backend.try_start_next()
        await _wait_for_jobs(tracked_jobs, report, report_path)
        report["result_summary"] = await _collect_result_summary(tracked_jobs)
        report["elapsed_seconds"] = round(time.monotonic() - started, 1)
        report["finished_at"] = _utcnow().isoformat()
        await asyncio.to_thread(_write_json_atomic, report_path, report)
        return report
    finally:
        await extraction_job_backend.shutdown()
        await close_redis()
        await close_database()


def main() -> None:
    args = _parse_args()
    _configure_environment(args)
    summary = asyncio.run(_run(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
