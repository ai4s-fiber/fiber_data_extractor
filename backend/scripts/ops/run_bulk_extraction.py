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
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@dataclass(slots=True)
class SourceDocument:
    path: Path
    sha256: str
    size_bytes: int = 0
    page_count: int | None = None
    rejection_reason: str = ""
    warning: str = ""
    relevance_decision: str = "review"
    relevance_reason: str = "not_inspected"
    metadata_title: str = ""
    preview_chars: int = 0
    duplicate_paths: list[Path] = field(default_factory=list)


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
    parser.add_argument(
        "--pdf-name",
        action="append",
        default=[],
        help="Process only this exact filename; repeat to select multiple papers.",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=0,
        help="Deterministic stratified pilot size after hashing; 0 means all.",
    )
    parser.add_argument("--sample-seed", default="fiber-bulk-v1")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--upload-concurrency", type=int, default=8)
    parser.add_argument("--hash-concurrency", type=int, default=4)
    parser.add_argument("--max-jobs", type=int, default=3)
    parser.add_argument(
        "--max-pending-jobs",
        type=int,
        default=24,
        help="Maximum queued/running extraction jobs before ingestion pauses.",
    )
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
    parser.add_argument(
        "--skip-remote-preflight",
        action="store_true",
        help="Skip read-only MinerU and low-token LLM connectivity checks.",
    )
    parser.add_argument(
        "--disk-artifact-factor",
        type=float,
        default=2.0,
        help="Reserve this multiple of source PDF bytes for parse/database artifacts.",
    )
    parser.add_argument("--minimum-free-gb", type=float, default=2.0)
    parser.add_argument(
        "--allow-resume-config-change",
        action="store_true",
        help="Allow an interrupted run to resume with a different model/runtime config.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inventory, validate and hash PDFs without using remote APIs or the database.",
    )
    parser.add_argument(
        "--relevance-prefilter",
        choices=["conservative", "off"],
        default="conservative",
        help=(
            "Use local metadata/first-page text to skip only clearly irrelevant "
            "documents before MinerU."
        ),
    )
    parser.add_argument(
        "--include-prefilter-rejected",
        action="store_true",
        help="Process documents marked irrelevant by the conservative prefilter.",
    )
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


def _chunks(items: list, size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _id_chunks(items: set[int], size: int = 500):
    ordered = sorted(items)
    yield from _chunks(ordered, max(1, size))


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
    *,
    inspect_relevance: bool = True,
) -> list[SourceDocument]:
    from app.services.bulk_preflight import inspect_pdf
    from app.services.file_integrity import file_sha256

    completed = 0
    lock = asyncio.Lock()

    async def hash_one(path: Path) -> SourceDocument:
        nonlocal completed

        def inspect_one() -> SourceDocument:
            digest = file_sha256(path)
            inspection = inspect_pdf(
                path,
                inspect_relevance=inspect_relevance,
            )
            return SourceDocument(
                path=path,
                sha256=digest,
                size_bytes=inspection.size_bytes,
                page_count=inspection.page_count,
                rejection_reason=inspection.rejection_reason,
                warning=inspection.warning,
                relevance_decision=inspection.relevance_decision,
                relevance_reason=inspection.relevance_reason,
                metadata_title=inspection.metadata_title,
                preview_chars=inspection.preview_chars,
            )

        document = await asyncio.to_thread(inspect_one)
        async with lock:
            completed += 1
            if completed == len(paths) or completed % 100 == 0:
                print(f"[hash] {completed}/{len(paths)}", flush=True)
        return document

    # Bound task allocation for corpora much larger than the current collection.
    hashed: list[SourceDocument] = []
    parallelism = max(1, concurrency)
    window = max(16, parallelism * 4)
    for batch in _chunks(paths, window):
        semaphore = asyncio.Semaphore(parallelism)

        async def bounded(path: Path) -> SourceDocument:
            async with semaphore:
                return await hash_one(path)

        hashed.extend(await asyncio.gather(*(bounded(path) for path in batch)))

    unique: dict[str, SourceDocument] = {}
    for item in hashed:
        existing = unique.get(item.sha256)
        if existing is None:
            unique[item.sha256] = item
        else:
            existing.duplicate_paths.append(item.path)
    return list(unique.values())


def _materialize_pdf(
    source: SourceDocument,
    project_id: int,
    copy_mode: str,
) -> str:
    from app.core.config import settings
    from app.services.file_integrity import file_sha256

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


async def _get_or_create_project(
    args: argparse.Namespace,
    api_key: str,
    *,
    update_configuration: bool = True,
) -> int:
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

        if update_configuration:
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
    from app.services.file_integrity import file_sha256

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
        if latest is not None and latest.status == "failed":
            if retry_failed:
                return PreparedDocument(source, paper.id, None, "ready")
            return PreparedDocument(source, paper.id, latest.id, "skip_failed")
        if paper.status in {"review", "completed"} and not reextract_completed:
            completed_result = await db.execute(
                select(ExtractionJob.id)
                .where(
                    ExtractionJob.paper_id == paper.id,
                    ExtractionJob.status == "completed",
                )
                .order_by(ExtractionJob.created_at.desc(), ExtractionJob.id.desc())
                .limit(1)
            )
            completed_job_id = completed_result.scalar_one_or_none()
            return PreparedDocument(
                source,
                paper.id,
                int(completed_job_id) if completed_job_id is not None else None,
                "skip_completed",
            )
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
    counts: Counter[str] = Counter()
    async with async_session_factory() as db:
        for job_id_chunk in _id_chunks(job_ids):
            result = await db.execute(
                select(ExtractionJob.status).where(
                    ExtractionJob.id.in_(job_id_chunk)
                )
            )
            counts.update(str(row[0]) for row in result.fetchall())
    return dict(counts)


async def _collect_result_summary(
    job_ids: set[int],
    *,
    expected_model: str = "",
    expected_parser: str = "",
) -> dict:
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
        "quality_gate_passed": True,
        "papers": [],
        "total_samples": 0,
        "total_facts": 0,
        "total_candidates": 0,
        "qa_flagged_candidates": 0,
        "missing_evidence_candidates": 0,
        "exact_duplicate_rows": 0,
        "failed_jobs": 0,
        "missing_job_ids": [],
        "completed_zero_candidate_papers": 0,
        "unexpected_zero_candidate_papers": 0,
        "intentional_zero_candidate_papers": 0,
        "skipped_review_papers": 0,
        "review_required_papers": 0,
        "quality_failed_papers": 0,
        "attention_required_papers": 0,
    }
    if not job_ids:
        return empty

    async with async_session_factory() as db:
        jobs = []
        for job_id_chunk in _id_chunks(job_ids):
            jobs_result = await db.execute(
                select(ExtractionJob, Paper)
                .join(Paper, Paper.id == ExtractionJob.paper_id)
                .where(ExtractionJob.id.in_(job_id_chunk))
            )
            jobs.extend(jobs_result.all())
        jobs.sort(key=lambda row: row[0].id)
        found_job_ids = {job.id for job, _ in jobs}
        missing_job_ids = sorted(job_ids - found_job_ids)
        paper_ids = {paper.id for _, paper in jobs}
        if not paper_ids:
            missing_only = dict(empty)
            missing_only.update({
                "healthy": not missing_job_ids,
                "quality_gate_passed": not missing_job_ids,
                "missing_job_ids": missing_job_ids,
            })
            return missing_only

        async def grouped_count(model, paper_column) -> dict[int, int]:
            counts: dict[int, int] = {}
            for paper_id_chunk in _id_chunks(paper_ids):
                result = await db.execute(
                    select(paper_column, func.count(model.id))
                    .where(paper_column.in_(paper_id_chunk))
                    .group_by(paper_column)
                )
                counts.update(
                    (int(paper_id), int(count))
                    for paper_id, count in result.all()
                )
            return counts

        sample_counts = await grouped_count(SampleCatalog, SampleCatalog.paper_id)
        fact_counts = await grouped_count(FactCandidate, FactCandidate.paper_id)

        no_evidence = and_(
            func.trim(func.coalesce(CandidateRecord.evidence_text, "")) == "",
            func.trim(func.coalesce(CandidateRecord.performance_evidence, "")) == "",
        )
        candidate_counts: dict[int, int] = {}
        qa_counts: dict[int, int] = {}
        missing_evidence_counts: dict[int, int] = {}
        duplicate_counts: dict[int, int] = defaultdict(int)
        for paper_id_chunk in _id_chunks(paper_ids):
            candidate_result = await db.execute(
                select(
                    CandidateRecord.source_paper_id,
                    func.count(CandidateRecord.id),
                    func.sum(case((
                        CandidateRecord.reviewer_comment.like("%qa_reason=%"), 1
                    ), else_=0)),
                    func.sum(case((no_evidence, 1), else_=0)),
                )
                .where(CandidateRecord.source_paper_id.in_(paper_id_chunk))
                .group_by(CandidateRecord.source_paper_id)
            )
            for paper_id, count, qa_count, missing_count in candidate_result.all():
                candidate_counts[int(paper_id)] = int(count or 0)
                qa_counts[int(paper_id)] = int(qa_count or 0)
                missing_evidence_counts[int(paper_id)] = int(missing_count or 0)

            duplicate_result = await db.execute(
                select(
                    CandidateRecord.source_paper_id,
                    func.count(CandidateRecord.id).label("copies"),
                )
                .where(CandidateRecord.source_paper_id.in_(paper_id_chunk))
                .group_by(
                    CandidateRecord.source_paper_id,
                    CandidateRecord.sample_id,
                    CandidateRecord.performance_metric,
                    CandidateRecord.performance_value,
                    CandidateRecord.performance_unit,
                    CandidateRecord.performance_method,
                    CandidateRecord.performance_condition,
                )
                .having(func.count(CandidateRecord.id) > 1)
            )
            for paper_id, copies in duplicate_result.all():
                duplicate_counts[int(paper_id)] += int(copies) - 1

    papers = []
    for job, paper in jobs:
        status = str(job.status or "")
        candidate_count = candidate_counts.get(paper.id, 0)
        qa_count = qa_counts.get(paper.id, 0)
        missing_count = missing_evidence_counts.get(paper.id, 0)
        duplicate_count = duplicate_counts.get(paper.id, 0)
        document_type = str(getattr(paper, "document_type", "") or "")
        skip_reason = str(getattr(paper, "extraction_skip_reason", "") or "")
        quality_reasons: list[str] = []
        if status != "completed":
            quality_reasons.append(f"job_{status or 'unknown'}")
        if missing_count:
            quality_reasons.append("missing_evidence")
        if duplicate_count:
            quality_reasons.append("exact_duplicates")

        hard_failure = bool(quality_reasons)
        intentional_zero_skip = (
            status == "completed"
            and candidate_count == 0
            and (
                (
                    document_type == "review"
                    and skip_reason == "review_article"
                )
                or skip_reason == "non_record_outputs_only"
            )
        )
        if hard_failure:
            quality_status = "fail"
        elif intentional_zero_skip:
            quality_status = "skipped"
        else:
            if candidate_count == 0:
                quality_reasons.append("unexpected_zero_candidates")
            if qa_count:
                quality_reasons.append("qa_review_required")
            if expected_model and str(job.model_name or "") != expected_model:
                quality_reasons.append("model_mismatch")
            if expected_parser and str(job.parser_strategy or "") != expected_parser:
                quality_reasons.append("parser_mismatch")
            quality_status = "review" if quality_reasons else "pass"

        needs_attention = quality_status in {"review", "fail"}
        papers.append({
            "paper_id": paper.id,
            "job_id": job.id,
            "filename": paper.original_filename,
            "status": status,
            "quality_status": quality_status,
            "quality_reasons": quality_reasons,
            "document_type": document_type,
            "extraction_skip_reason": skip_reason,
            "model": job.model_name or "",
            "parser_strategy": job.parser_strategy or "",
            "error_code": job.error_code or "",
            "error_message": job.error_message or "",
            "samples": sample_counts.get(paper.id, 0),
            "facts": fact_counts.get(paper.id, 0),
            "candidates": candidate_count,
            "qa_flagged_candidates": qa_count,
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
        "missing_job_ids": missing_job_ids,
        "completed_zero_candidate_papers": sum(
            item["status"] == "completed" and item["candidates"] == 0
            for item in papers
        ),
        "unexpected_zero_candidate_papers": sum(
            "unexpected_zero_candidates" in item["quality_reasons"]
            for item in papers
        ),
        "intentional_zero_candidate_papers": sum(
            item["quality_status"] == "skipped" for item in papers
        ),
        "skipped_review_papers": sum(
            item["quality_status"] == "skipped"
            and item["document_type"] == "review"
            for item in papers
        ),
        "review_required_papers": sum(
            item["quality_status"] == "review" for item in papers
        ),
        "quality_failed_papers": sum(
            item["quality_status"] == "fail" for item in papers
        ),
        "attention_required_papers": sum(
            item["needs_attention"] for item in papers
        ),
    })
    summary["healthy"] = (
        summary["quality_failed_papers"] == 0
        and not summary["missing_job_ids"]
    )
    summary["quality_gate_passed"] = (
        summary["attention_required_papers"] == 0
        and not summary["missing_job_ids"]
    )
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


def _runtime_config_payload(args: argparse.Namespace, settings, pdf_root: Path) -> dict:
    return {
        "source_root": str(pdf_root),
        "selected_pdf_names": sorted(set(args.pdf_name)),
        "provider": args.provider,
        "base_url": args.base_url.rstrip("/"),
        "model": args.model,
        "model_mode": "strong",
        "parser_strategy": "mineru_cloud",
        "mineru_model_version": settings.MINERU_CLOUD_MODEL_VERSION,
        "mineru_enable_formula": settings.MINERU_CLOUD_ENABLE_FORMULA,
        "mineru_enable_table": settings.MINERU_CLOUD_ENABLE_TABLE,
        "mineru_page_ranges": settings.MINERU_CLOUD_PAGE_RANGES,
        "batch_size": args.batch_size,
        "upload_concurrency": args.upload_concurrency,
        "max_jobs": args.max_jobs,
        "max_pending_jobs": args.max_pending_jobs,
        "sample_size": args.sample_size,
        "sample_seed": args.sample_seed,
        "relevance_prefilter": args.relevance_prefilter,
        "include_prefilter_rejected": args.include_prefilter_rejected,
        "llm_global_limit": settings.LLM_GLOBAL_MAX_CONCURRENT_CALLS,
        "llm_batch_limit": settings.LLM_BATCH_MAX_CONCURRENT_CALLS,
        "llm_reserved_calls": settings.LLM_INTERACTIVE_RESERVED_CALLS,
        "strong_parallel_calls": settings.STRONG_LLM_PARALLEL_CALLS,
        "strong_window_chars": settings.STRONG_HOLISTIC_PERFORMANCE_WINDOW_CHARS,
    }


def _resume_checkpoint_conflicts(
    previous_report: dict,
    *,
    config_fingerprint: str,
    selection_fingerprint: str,
) -> list[str]:
    conflicts = []
    previous_config = str(previous_report.get("config_fingerprint") or "")
    previous_selection = str(
        previous_report.get("selection_manifest_fingerprint") or ""
    )
    if previous_config and previous_config != config_fingerprint:
        conflicts.append("runtime configuration")
    if previous_selection and previous_selection != selection_fingerprint:
        conflicts.append("source manifest")
    return conflicts


async def _preflight_remote_services(
    args: argparse.Namespace,
    *,
    api_key: str,
    mineru_token: str,
    mineru_trust_env: bool,
) -> dict:
    """Validate both paid upstreams before submitting any extraction work."""
    import httpx

    from app.services.llm_client import create_llm_client
    from app.services.mineru_client import MINERU_CLOUD_BATCH_RESULTS_URL

    client = create_llm_client(
        provider=args.provider,
        api_key=api_key,
        model=args.model,
        base_url=args.base_url,
        timeout_seconds=60,
        max_retries=1,
    )
    parsed, _ = await asyncio.wait_for(
        client.agenerate_json_tolerant(
            "Return one JSON object only.",
            'Connectivity check. Return exactly {"status":"ok"}.',
            max_tokens=64,
            reasoning_effort="low",
        ),
        timeout=90,
    )
    if str(parsed.get("status") or "").strip().lower() != "ok":
        raise RuntimeError(
            "LLM preflight returned an unexpected response; no jobs were submitted"
        )

    probe_id = f"fiber-preflight-{uuid.uuid4().hex}"
    timeout = httpx.Timeout(30.0, connect=15.0)
    async with httpx.AsyncClient(
        timeout=timeout,
        trust_env=mineru_trust_env,
        follow_redirects=True,
    ) as http:
        response = await http.get(
            f"{MINERU_CLOUD_BATCH_RESULTS_URL}/{probe_id}",
            headers={
                "Authorization": f"Bearer {mineru_token}",
                "Accept": "application/json",
            },
        )
    if response.status_code in {401, 403}:
        raise RuntimeError(
            "MinerU Cloud token was rejected; no jobs were submitted"
        )
    response.raise_for_status()
    try:
        mineru_payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "MinerU Cloud preflight returned non-JSON content"
        ) from exc
    mineru_code = int(mineru_payload.get("code", -1))
    if mineru_code not in {0, -60012}:
        raise RuntimeError(
            "MinerU Cloud preflight failed: "
            f"code={mineru_code}, message={mineru_payload.get('msg', '')}"
        )
    return {
        "llm": {
            "ok": True,
            "model": args.model,
            "provider": args.provider,
        },
        "mineru": {
            "ok": True,
            "authenticated_result_probe_code": mineru_code,
        },
    }


async def _database_preflight() -> dict:
    from app.core.database import engine
    from app.services.bulk_preflight import ensure_writable_directory

    result = {
        "dialect": engine.dialect.name,
        "url": engine.url.render_as_string(hide_password=True),
    }
    if engine.dialect.name == "sqlite":
        raw_database_path = str(engine.url.database or "")
        if raw_database_path and raw_database_path != ":memory:":
            database_path = Path(raw_database_path).expanduser().resolve()
            await asyncio.to_thread(
                ensure_writable_directory,
                database_path.parent,
            )
            result["database_path"] = str(database_path)
    async with engine.connect() as connection:
        if engine.dialect.name == "sqlite":
            journal = await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            result["journal_mode"] = str(journal.scalar_one()).lower()
            check = await connection.exec_driver_sql("PRAGMA quick_check")
            result["quick_check"] = str(check.scalar_one()).lower()
            if result["quick_check"] != "ok":
                raise RuntimeError(
                    f"SQLite quick_check failed: {result['quick_check']}"
                )
        else:
            await connection.exec_driver_sql("SELECT 1")
    return result


async def _active_project_job_count(project_id: int) -> int:
    from sqlalchemy import func, select

    from app.core.database import async_session_factory
    from app.models.extraction_job import ExtractionJob
    from app.services.extraction_jobs import ACTIVE_JOB_STATUSES

    async with async_session_factory() as db:
        result = await db.execute(
            select(func.count(ExtractionJob.id)).where(
                ExtractionJob.project_id == project_id,
                ExtractionJob.status.in_(ACTIVE_JOB_STATUSES),
            )
        )
        return int(result.scalar() or 0)


async def _wait_for_project_capacity(
    project_id: int,
    max_pending_jobs: int,
    *,
    on_wait=None,
) -> None:
    from app.services.extraction_jobs import extraction_job_backend

    last_reported = -1
    while True:
        await extraction_job_backend.try_start_next()
        active = await _active_project_job_count(project_id)
        if active < max_pending_jobs:
            return
        if active != last_reported:
            print(
                f"[backpressure] {active} active jobs; "
                f"waiting for limit {max_pending_jobs}",
                flush=True,
            )
            last_reported = active
            if on_wait is not None:
                await on_wait(active)
        await asyncio.sleep(5)


async def _run(args: argparse.Namespace) -> dict:
    from app.core.config import settings
    from app.services.bulk_preflight import (
        select_stratified_documents,
        stable_config_fingerprint,
        validate_storage_capacity,
    )

    started = time.monotonic()
    extraction_job_backend = None
    close_database = None
    close_redis = None
    api_key = (os.environ.get(args.api_key_env) or "").strip()
    if not args.dry_run and not api_key:
        raise SystemExit(f"Missing API key environment variable: {args.api_key_env}")
    if not args.dry_run and not settings.MINERU_CLOUD_TOKEN.strip():
        raise SystemExit("MINERU_CLOUD_TOKEN is required for MinerU Cloud extraction")
    if not 1 <= args.batch_size <= 200:
        raise SystemExit("--batch-size must be between 1 and 200")
    if args.max_jobs < 1:
        raise SystemExit("--max-jobs must be at least 1")
    if args.sample_size < 0:
        raise SystemExit("--sample-size cannot be negative")
    if args.max_pending_jobs < args.max_jobs:
        raise SystemExit("--max-pending-jobs must be at least --max-jobs")
    if args.hash_concurrency < 1 or args.upload_concurrency < 1:
        raise SystemExit("Hash and upload concurrency must be at least 1")
    if args.disk_artifact_factor < 0 or args.minimum_free_gb < 0:
        raise SystemExit("Disk reserve settings cannot be negative")

    pdf_root = Path(args.pdf_dir).expanduser().resolve()
    if not pdf_root.is_dir():
        raise SystemExit(f"PDF directory does not exist: {pdf_root}")
    pattern = "*.pdf" if args.no_recursive else "**/*.pdf"
    paths = sorted(path for path in pdf_root.glob(pattern) if path.is_file())
    if args.pdf_name:
        requested_names = {name.casefold() for name in args.pdf_name}
        paths = [
            path for path in paths
            if path.name.casefold() in requested_names
        ]
    if args.limit > 0:
        paths = paths[: args.limit]
    if not paths:
        raise SystemExit(f"No PDF files found under {pdf_root}")

    report_root = Path(args.report_dir).expanduser().resolve()
    report_path = report_root / (
        "bulk_dry_run_summary.json" if args.dry_run else "bulk_summary.json"
    )
    config_payload = _runtime_config_payload(args, settings, pdf_root)
    config_fingerprint = stable_config_fingerprint(config_payload)
    report = {
        "run_id": (
            f"{_utcnow().strftime('%Y%m%dT%H%M%SZ')}-"
            f"{uuid.uuid4().hex[:8]}"
        ),
        "run_state": "preflight",
        "source_root": str(pdf_root),
        "model": args.model,
        "model_mode": "strong",
        "parser_strategy": "mineru_cloud",
        "config": config_payload,
        "config_fingerprint": config_fingerprint,
        "started_at": _utcnow().isoformat(),
        "scanned_files": len(paths),
        "unique_files": 0,
        "duplicate_files": 0,
        "duplicate_groups": [],
        "eligible_files": 0,
        "selected_files": 0,
        "selection_manifest_path": "",
        "selection_manifest_fingerprint": "",
        "preflight_rejected_files": [],
        "relevance_prefilter": {
            "mode": args.relevance_prefilter,
            "included_rejected": bool(args.include_prefilter_rejected),
            "eligible": 0,
            "review": 0,
            "irrelevant": 0,
        },
        "relevance_skipped_files": [],
        "relevance_review_files": [],
        "pdf_inspection_warnings": [],
        "backfilled_hashes": 0,
        "cache_hits": 0,
        "mineru_parsed": 0,
        "mineru_failed": 0,
        "mineru_batches_resumed": 0,
        "queued_jobs": 0,
        "resumed_jobs": 0,
        "skipped_completed": 0,
        "skipped_failed": 0,
        "untracked_existing_papers": [],
        "job_status_counts": {},
    }

    try:
        storage = await asyncio.to_thread(
            validate_storage_capacity,
            source_paths=paths,
            output_directories=[
                report_root,
                Path(settings.UPLOAD_DIR),
                Path(settings.PARSE_ARTIFACT_DIR),
            ],
            upload_directory=Path(settings.UPLOAD_DIR),
            copy_mode=args.copy_mode,
            artifact_factor=args.disk_artifact_factor,
            minimum_free_bytes=int(args.minimum_free_gb * 1024**3),
        )
        report["storage_preflight"] = storage.as_dict()

        if not args.dry_run:
            import app.models  # noqa: F401

            from app.core.database import close_database as close_database_impl
            from app.core.redis_client import close_redis as close_redis_impl
            from app.core.schema_repair import ensure_runtime_schema
            from app.services.document_context import (
                load_shared_mineru_artifact,
                persist_shared_mineru_artifact,
            )
            from app.services.extraction_jobs import (
                extraction_job_backend as extraction_backend_impl,
            )
            from app.services.mineru_client import MinerUClient

            close_database = close_database_impl
            close_redis = close_redis_impl
            extraction_job_backend = extraction_backend_impl
            report["database_preflight"] = await _database_preflight()
            await ensure_runtime_schema()
            if args.skip_remote_preflight:
                report["remote_preflight"] = {"skipped": True}
            else:
                report["remote_preflight"] = await _preflight_remote_services(
                    args,
                    api_key=api_key,
                    mineru_token=settings.MINERU_CLOUD_TOKEN,
                    mineru_trust_env=settings.MINERU_CLOUD_TRUST_ENV,
                )

        documents = await _hash_documents(
            paths,
            args.hash_concurrency,
            inspect_relevance=args.relevance_prefilter != "off",
        )
        report["unique_files"] = len(documents)
        report["duplicate_files"] = len(paths) - len(documents)
        report["duplicate_groups"] = [
            {
                "sha256": item.sha256,
                "canonical_path": str(item.path),
                "duplicate_paths": [str(path) for path in item.duplicate_paths],
            }
            for item in documents
            if item.duplicate_paths
        ]
        rejected = [item for item in documents if item.rejection_reason]
        warnings = [item for item in documents if item.warning]
        report["preflight_rejected_files"] = [
            {
                "path": str(item.path),
                "sha256": item.sha256,
                "size_bytes": item.size_bytes,
                "page_count": item.page_count,
                "reason": item.rejection_reason,
            }
            for item in rejected
        ]
        report["pdf_inspection_warnings"] = [
            {
                "path": str(item.path),
                "sha256": item.sha256,
                "warning": item.warning,
            }
            for item in warnings
        ]
        documents = [item for item in documents if not item.rejection_reason]
        relevance_counts = {
            decision: sum(
                item.relevance_decision == decision for item in documents
            )
            for decision in ("eligible", "review", "irrelevant")
        }
        report["relevance_prefilter"].update(relevance_counts)
        report["relevance_review_files"] = [
            {
                "path": str(item.path),
                "sha256": item.sha256,
                "reason": item.relevance_reason,
                "metadata_title": item.metadata_title,
                "preview_chars": item.preview_chars,
            }
            for item in documents
            if item.relevance_decision == "review"
        ]
        prefilter_rejected = [
            item for item in documents
            if item.relevance_decision == "irrelevant"
        ]
        report["relevance_skipped_files"] = [
            {
                "path": str(item.path),
                "sha256": item.sha256,
                "reason": item.relevance_reason,
                "metadata_title": item.metadata_title,
                "preview_chars": item.preview_chars,
            }
            for item in prefilter_rejected
        ]
        if not args.include_prefilter_rejected:
            documents = [
                item for item in documents
                if item.relevance_decision != "irrelevant"
            ]
        report["eligible_files"] = len(documents)
        documents, selection_manifest = select_stratified_documents(
            documents,
            source_root=pdf_root,
            sample_size=args.sample_size,
            seed=args.sample_seed,
        )
        report["selected_files"] = len(documents)
        selection_fingerprint = stable_config_fingerprint({
            "source_root": str(pdf_root),
            "documents": [
                {
                    "sha256": item["sha256"],
                    "path": item["relative_path"],
                }
                for item in selection_manifest
            ],
        })
        selection_manifest_path = (
            report_root
            / "manifests"
            / f"selection-{selection_fingerprint[:20]}.json"
        )
        await asyncio.to_thread(
            _write_json_atomic,
            selection_manifest_path,
            {
                "source_root": str(pdf_root),
                "fingerprint": selection_fingerprint,
                "selected_files": len(selection_manifest),
                "documents": selection_manifest,
            },
        )
        report["selection_manifest_path"] = str(selection_manifest_path)
        report["selection_manifest_fingerprint"] = selection_fingerprint
        report["run_state"] = "dry_run_completed" if args.dry_run else "running"
        await asyncio.to_thread(_write_json_atomic, report_path, report)

        if args.dry_run:
            report["healthy"] = not rejected
            report["elapsed_seconds"] = round(time.monotonic() - started, 1)
            report["finished_at"] = _utcnow().isoformat()
            await asyncio.to_thread(_write_json_atomic, report_path, report)
            return report
        if not documents:
            report["run_state"] = "failed_preflight"
            report["healthy"] = False
            report["fatal_error"] = "No PDFs passed MinerU Cloud preflight limits"
            report["elapsed_seconds"] = round(time.monotonic() - started, 1)
            report["finished_at"] = _utcnow().isoformat()
            await asyncio.to_thread(_write_json_atomic, report_path, report)
            return report

        project_id = await _get_or_create_project(
            args,
            api_key,
            update_configuration=False,
        )
        report_path = (
            report_root / f"bulk_project_{project_id}_summary.json"
        )
        previous_report = {}
        if report_path.is_file():
            try:
                previous_report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous_report = {}
        previous_is_unfinished = bool(
            previous_report
            and previous_report.get("source_root") == str(pdf_root)
            and not previous_report.get("finished_at")
        )
        if previous_is_unfinished:
            previous_fingerprint = str(
                previous_report.get("config_fingerprint") or ""
            )
            resume_conflicts = _resume_checkpoint_conflicts(
                previous_report,
                config_fingerprint=config_fingerprint,
                selection_fingerprint=selection_fingerprint,
            )
            if resume_conflicts and not args.allow_resume_config_change:
                report["resume_rejected"] = {
                    "checkpoint_path": str(report_path),
                    "conflicts": resume_conflicts,
                }
                report_path = (
                    report_root
                    / "runs"
                    / f"{report['run_id']}-resume-rejected.json"
                )
                raise RuntimeError(
                    "Interrupted bulk run conflicts with the current "
                    f"{' and '.join(resume_conflicts)}. "
                    "Resume with the original options or pass "
                    "--allow-resume-config-change explicitly."
                )
            report["run_id"] = str(
                previous_report.get("run_id") or report["run_id"]
            )
            report["started_at"] = str(
                previous_report.get("started_at") or report["started_at"]
            )
            report["resumed_from_checkpoint"] = True
            if not previous_fingerprint:
                report.setdefault("warnings", []).append(
                    "Previous checkpoint predates config fingerprints; "
                    "resume compatibility could not be proven."
                )
        else:
            previous_report = {}
        await _get_or_create_project(
            args,
            api_key,
            update_configuration=True,
        )
        report["project_id"] = project_id
        report["backfilled_hashes"] = await _backfill_project_hashes(project_id)
        await extraction_job_backend.recover_interrupted_jobs()
        await extraction_job_backend.try_start_next()

        prepared = []
        tracked_jobs: set[int] = set()
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
                if item.job_id is not None:
                    tracked_jobs.add(item.job_id)
                else:
                    report["untracked_existing_papers"].append({
                        "paper_id": item.paper_id,
                        "path": str(item.source.path),
                        "reason": "completed_without_completed_job",
                    })
            elif item.state == "skip_failed":
                report["skipped_failed"] += 1
                if item.job_id is not None:
                    tracked_jobs.add(item.job_id)
            else:
                prepared.append(item)
            if index == len(documents) or index % 100 == 0:
                print(f"[prepare] {index}/{len(documents)}", flush=True)

        from app.services.mineru_client import MinerUTaskFailed

        client = MinerUClient()

        async def checkpoint() -> None:
            report["updated_at"] = _utcnow().isoformat()
            await asyncio.to_thread(_write_json_atomic, report_path, report)

        async def queue_item(item: PreparedDocument) -> None:
            if item.job_id is not None:
                tracked_jobs.add(item.job_id)
                report["resumed_jobs"] += 1
                await extraction_job_backend.try_start_next()
                return

            async def record_backpressure(active: int) -> None:
                report["queue_backpressure"] = {
                    "active_jobs": active,
                    "limit": args.max_pending_jobs,
                    "observed_at": _utcnow().isoformat(),
                }
                await checkpoint()

            await _wait_for_project_capacity(
                project_id,
                args.max_pending_jobs,
                on_wait=record_backpressure,
            )
            job_id = await _queue_document(item, project_id)
            tracked_jobs.add(job_id)
            report["queued_jobs"] += 1

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
        report["result_summary"] = await _collect_result_summary(
            tracked_jobs,
            expected_model=args.model,
            expected_parser="mineru_cloud",
        )
        has_inventory_failures = bool(
            report["preflight_rejected_files"]
            or report["untracked_existing_papers"]
        )
        report["healthy"] = (
            report["result_summary"]["healthy"] and not has_inventory_failures
        )
        report["quality_gate_passed"] = (
            report["result_summary"]["quality_gate_passed"]
            and not has_inventory_failures
        )
        report["run_state"] = (
            "completed"
            if report["healthy"]
            else "completed_with_failures"
        )
        report["elapsed_seconds"] = round(time.monotonic() - started, 1)
        report["finished_at"] = _utcnow().isoformat()
        immutable_report_path = (
            report_root / "runs" / f"{report['run_id']}.json"
        )
        report["immutable_report_path"] = str(immutable_report_path)
        await asyncio.to_thread(_write_json_atomic, report_path, report)
        await asyncio.to_thread(
            _write_json_atomic,
            immutable_report_path,
            report,
        )
        return report
    except BaseException as exc:
        report["run_state"] = "interrupted"
        report["last_error"] = {
            "type": exc.__class__.__name__,
            "message": str(exc)[:2000],
        }
        report["interrupted_at"] = _utcnow().isoformat()
        report["elapsed_seconds"] = round(time.monotonic() - started, 1)
        try:
            await asyncio.to_thread(_write_json_atomic, report_path, report)
        except Exception:
            pass
        raise
    finally:
        if extraction_job_backend is not None:
            await extraction_job_backend.shutdown()
        if close_redis is not None:
            await close_redis()
        if close_database is not None:
            await close_database()


def main() -> None:
    args = _parse_args()
    _configure_environment(args)
    summary = asyncio.run(_run(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary.get("healthy", False):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
