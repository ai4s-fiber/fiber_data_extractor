"""Bulk-ingestion filesystem and deduplication tests."""

from argparse import Namespace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - register all metadata dependencies
from app.services.extraction_jobs import extraction_job_backend
from app.models.base import Base
from app.models.candidate_record import CandidateRecord
from app.models.extraction_job import ExtractionJob
from app.models.fact_candidate import FactCandidate
from app.models.paper import Paper
from app.models.project import Project
from app.models.sample_catalog import SampleCatalog

from scripts.ops.run_bulk_extraction import (
    SourceDocument,
    _collect_result_summary,
    _configure_environment,
    _hash_documents,
    _materialize_pdf,
    _resume_checkpoint_conflicts,
    _terminal_counts,
    _wait_for_project_capacity,
    _write_json_atomic,
)
from scripts.ops import run_bulk_extraction as bulk_runner


@pytest.fixture
async def bulk_result_db(tmp_path):
    path = (tmp_path / "bulk-summary.db").as_posix()
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def test_bulk_metrics_are_isolated_under_report_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_METRICS_DIR", raising=False)
    args = Namespace(
        database_url="sqlite+aiosqlite:///./bulk.db",
        max_jobs=3,
        report_dir=str(tmp_path / "run"),
    )

    _configure_environment(args)

    assert Path(os.environ["LLM_METRICS_DIR"]) == (
        tmp_path / "run" / "llm_metrics"
    ).resolve()


@pytest.mark.asyncio
async def test_bulk_hashing_deduplicates_identical_pdf_bytes(tmp_path):
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    third = tmp_path / "third.pdf"
    first.write_bytes(b"same-pdf")
    second.write_bytes(b"same-pdf")
    third.write_bytes(b"different-pdf")

    documents = await _hash_documents([first, second, third], concurrency=2)

    assert len(documents) == 2
    assert {item.path.name for item in documents} == {"first.pdf", "third.pdf"}
    duplicate = next(item for item in documents if item.path.name == "first.pdf")
    assert [path.name for path in duplicate.duplicate_paths] == ["second.pdf"]


@pytest.mark.asyncio
async def test_bulk_retry_uses_latest_failed_job_over_older_completed_job(
    bulk_result_db,
    tmp_path,
    monkeypatch,
):
    from app.core.config import settings

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    stored_pdf = uploads / "paper.pdf"
    stored_pdf.write_bytes(b"paper")
    digest = "a" * 64
    source = SourceDocument(stored_pdf, digest)
    now = datetime.now(timezone.utc)

    async with bulk_result_db() as db:
        project = Project(name="retry-latest")
        db.add(project)
        await db.flush()
        paper = Paper(
            project_id=project.id,
            original_filename="paper.pdf",
            file_object_key="paper.pdf",
            content_sha256=digest,
            status="review",
        )
        db.add(paper)
        await db.flush()
        db.add_all([
            ExtractionJob(
                project_id=project.id,
                paper_id=paper.id,
                status="completed",
                step="completed",
                created_at=now - timedelta(minutes=1),
            ),
            ExtractionJob(
                project_id=project.id,
                paper_id=paper.id,
                status="failed",
                step="failed",
                created_at=now,
            ),
        ])
        await db.commit()
        project_id = project.id

    monkeypatch.setattr(
        "app.core.database.async_session_factory",
        bulk_result_db,
    )
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(uploads))

    skipped = await bulk_runner._prepare_document(
        source,
        project_id=project_id,
        copy_mode="copy",
        retry_failed=False,
        reextract_completed=False,
    )
    retried = await bulk_runner._prepare_document(
        source,
        project_id=project_id,
        copy_mode="copy",
        retry_failed=True,
        reextract_completed=False,
    )

    assert skipped.state == "skip_failed"
    assert retried.state == "ready"
    assert retried.job_id is None


def test_bulk_materialization_is_content_addressed_and_non_destructive(
    tmp_path,
    monkeypatch,
):
    from app.core.config import settings
    from app.services.file_integrity import file_sha256

    uploads = tmp_path / "uploads"
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(uploads))
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"source-pdf")
    digest = file_sha256(source_path)
    source = SourceDocument(source_path, digest)

    object_key = _materialize_pdf(source, project_id=7, copy_mode="hardlink")
    stored = uploads / Path(object_key)

    assert stored.is_file()
    assert file_sha256(stored) == digest
    assert source_path.read_bytes() == b"source-pdf"
    assert object_key.endswith(f"{digest}.pdf")
    assert _materialize_pdf(source, project_id=7, copy_mode="copy") == object_key


def test_bulk_summary_write_is_atomic(tmp_path):
    target = tmp_path / "reports" / "summary.json"

    _write_json_atomic(target, {"status": "running", "count": 2})
    _write_json_atomic(target, {"status": "completed", "count": 3})

    assert target.read_text(encoding="utf-8") == (
        '{\n  "status": "completed",\n  "count": 3\n}'
    )
    assert list(target.parent.glob("*.tmp")) == []


def test_resume_checkpoint_conflicts_only_on_known_fingerprints():
    assert _resume_checkpoint_conflicts(
        {
            "config_fingerprint": "config-a",
            "selection_manifest_fingerprint": "selection-a",
        },
        config_fingerprint="config-b",
        selection_fingerprint="selection-b",
    ) == ["runtime configuration", "source manifest"]
    assert _resume_checkpoint_conflicts(
        {},
        config_fingerprint="config-b",
        selection_fingerprint="selection-b",
    ) == []


@pytest.mark.asyncio
async def test_bulk_result_summary_audits_failures_qa_and_duplicates(
    bulk_result_db,
    monkeypatch,
):
    async with bulk_result_db() as db:
        project = Project(name="bulk-audit")
        db.add(project)
        await db.flush()
        completed_paper = Paper(
            project_id=project.id,
            original_filename="completed.pdf",
            file_object_key="completed.pdf",
            status="review",
        )
        failed_paper = Paper(
            project_id=project.id,
            original_filename="failed.pdf",
            file_object_key="failed.pdf",
            status="failed",
        )
        db.add_all([completed_paper, failed_paper])
        await db.flush()
        completed_job = ExtractionJob(
            project_id=project.id,
            paper_id=completed_paper.id,
            status="completed",
            step="completed",
        )
        failed_job = ExtractionJob(
            project_id=project.id,
            paper_id=failed_paper.id,
            status="failed",
            step="failed",
            error_code="llm_timeout",
            error_message="timed out",
        )
        db.add_all([completed_job, failed_job])
        await db.flush()
        db.add_all([
            SampleCatalog(
                project_id=project.id,
                paper_id=completed_paper.id,
                sample_id="S1",
                sample_group_id="G001",
            ),
            FactCandidate(
                project_id=project.id,
                paper_id=completed_paper.id,
                fact_id="F001",
                fact_type="performance",
            ),
        ])
        for reviewer_comment in ("qa_reason=checklist_failed", ""):
            db.add(CandidateRecord(
                project_id=project.id,
                source_paper_id=completed_paper.id,
                job_id=completed_job.id,
                sample_id="S1",
                performance_metric="tensile_strength",
                performance_value="12",
                performance_unit="MPa",
                evidence_text="S1 reached 12 MPa.",
                reviewer_comment=reviewer_comment,
            ))
        db.add(CandidateRecord(
            project_id=project.id,
            source_paper_id=completed_paper.id,
            job_id=completed_job.id,
            sample_id="S1",
            performance_metric="tensile_strength",
            performance_value="12",
            performance_unit="MPa",
            performance_condition="tested at 80 C",
            evidence_text="S1 reached 12 MPa at 80 C.",
        ))
        await db.commit()
        job_ids = {completed_job.id, failed_job.id}

    monkeypatch.setattr(
        "app.core.database.async_session_factory",
        bulk_result_db,
    )
    summary = await _collect_result_summary(job_ids)

    assert summary["healthy"] is False
    assert summary["quality_gate_passed"] is False
    assert summary["total_samples"] == 1
    assert summary["total_facts"] == 1
    assert summary["total_candidates"] == 3
    assert summary["qa_flagged_candidates"] == 1
    assert summary["missing_evidence_candidates"] == 0
    assert summary["exact_duplicate_rows"] == 1
    assert summary["failed_jobs"] == 1
    assert summary["attention_required_papers"] == 2
    failed = next(
        paper for paper in summary["papers"] if paper["status"] == "failed"
    )
    assert failed["error_code"] == "llm_timeout"


@pytest.mark.asyncio
async def test_bulk_summary_chunks_more_than_sqlite_parameter_limit(
    bulk_result_db,
    monkeypatch,
):
    job_ids: set[int] = set()
    async with bulk_result_db() as db:
        project = Project(name="bulk-scale-audit")
        db.add(project)
        await db.flush()
        papers = [
            Paper(
                project_id=project.id,
                original_filename=f"review-{index:04d}.pdf",
                file_object_key=f"review-{index:04d}.pdf",
                document_type="review",
                extraction_skip_reason="review_article",
                status="review",
            )
            for index in range(1105)
        ]
        db.add_all(papers)
        await db.flush()
        jobs = [
            ExtractionJob(
                project_id=project.id,
                paper_id=paper.id,
                status="completed",
                step="completed",
                parser_strategy="mineru_cloud",
                model_name="gpt-5.5",
            )
            for paper in papers
        ]
        db.add_all(jobs)
        await db.flush()
        job_ids.update(job.id for job in jobs)
        await db.commit()

    monkeypatch.setattr(
        "app.core.database.async_session_factory",
        bulk_result_db,
    )

    counts = await _terminal_counts(job_ids)
    summary = await _collect_result_summary(
        job_ids,
        expected_model="gpt-5.5",
        expected_parser="mineru_cloud",
    )

    assert counts == {"completed": 1105}
    assert len(summary["papers"]) == 1105
    assert summary["skipped_review_papers"] == 1105
    assert summary["unexpected_zero_candidate_papers"] == 0
    assert summary["attention_required_papers"] == 0
    assert summary["healthy"] is True
    assert summary["quality_gate_passed"] is True


@pytest.mark.asyncio
async def test_bulk_summary_accepts_intentional_non_record_only_output(
    bulk_result_db,
    monkeypatch,
):
    async with bulk_result_db() as db:
        project = Project(name="characterization-only")
        db.add(project)
        await db.flush()
        paper = Paper(
            project_id=project.id,
            original_filename="ftir-only.pdf",
            file_object_key="ftir-only.pdf",
            document_type="research",
            extraction_skip_reason="non_record_outputs_only",
            status="review",
        )
        db.add(paper)
        await db.flush()
        job = ExtractionJob(
            project_id=project.id,
            paper_id=paper.id,
            status="completed",
            step="completed",
            parser_strategy="mineru_cloud",
            model_name="gpt-5.5",
        )
        db.add(job)
        await db.flush()
        db.add(FactCandidate(
            project_id=project.id,
            paper_id=paper.id,
            fact_id="F001",
            fact_type="performance",
            metric_or_parameter="FTIR_band",
            value="1722",
            unit="cm^-1",
        ))
        await db.commit()
        job_id = job.id

    monkeypatch.setattr(
        "app.core.database.async_session_factory",
        bulk_result_db,
    )
    summary = await _collect_result_summary({job_id})

    assert summary["healthy"] is True
    assert summary["quality_gate_passed"] is True
    assert summary["intentional_zero_candidate_papers"] == 1
    assert summary["unexpected_zero_candidate_papers"] == 0
    assert summary["papers"][0]["quality_status"] == "skipped"


@pytest.mark.asyncio
async def test_bulk_summary_flags_missing_model_identity(
    bulk_result_db,
    monkeypatch,
):
    async with bulk_result_db() as db:
        project = Project(name="missing-runtime-identity")
        db.add(project)
        await db.flush()
        paper = Paper(
            project_id=project.id,
            original_filename="missing-runtime.pdf",
            file_object_key="missing-runtime.pdf",
            document_type="research",
            status="completed",
        )
        db.add(paper)
        await db.flush()
        job = ExtractionJob(
            project_id=project.id,
            paper_id=paper.id,
            status="completed",
            step="completed",
        )
        db.add(job)
        await db.flush()
        db.add(CandidateRecord(
            project_id=project.id,
            source_paper_id=paper.id,
            job_id=job.id,
            sample_id="S1",
            performance_metric="tensile_strength",
            performance_value="12",
            performance_unit="MPa",
            evidence_text="S1 reached 12 MPa.",
        ))
        await db.commit()
        job_id = job.id

    monkeypatch.setattr(
        "app.core.database.async_session_factory",
        bulk_result_db,
    )
    summary = await _collect_result_summary(
        {job_id},
        expected_model="gpt-5.5",
        expected_parser="mineru_cloud",
    )

    assert summary["quality_gate_passed"] is False
    assert summary["review_required_papers"] == 1
    assert summary["papers"][0]["quality_reasons"] == ["model_mismatch"]


@pytest.mark.asyncio
async def test_bulk_summary_fails_when_tracked_job_disappears(
    bulk_result_db,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.core.database.async_session_factory",
        bulk_result_db,
    )

    summary = await _collect_result_summary({987654})

    assert summary["healthy"] is False
    assert summary["quality_gate_passed"] is False
    assert summary["missing_job_ids"] == [987654]


@pytest.mark.asyncio
async def test_bulk_backpressure_waits_until_project_has_capacity(monkeypatch):
    active_counts = iter((5, 5, 2))
    waits = []
    starts = []

    async def active_count(_project_id):
        return next(active_counts)

    async def start_next():
        starts.append(True)

    async def no_sleep(_seconds):
        return None

    async def on_wait(active):
        waits.append(active)

    monkeypatch.setattr(bulk_runner, "_active_project_job_count", active_count)
    monkeypatch.setattr(extraction_job_backend, "try_start_next", start_next)
    monkeypatch.setattr(bulk_runner.asyncio, "sleep", no_sleep)

    await _wait_for_project_capacity(7, 3, on_wait=on_wait)

    assert waits == [5]
    assert len(starts) == 3
