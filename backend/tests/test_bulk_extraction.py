"""Bulk-ingestion filesystem and deduplication tests."""

from argparse import Namespace
import os
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - register all metadata dependencies
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
    _write_json_atomic,
)


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


def test_bulk_materialization_is_content_addressed_and_non_destructive(
    tmp_path,
    monkeypatch,
):
    from app.core.config import settings
    from app.services.document_context import file_sha256

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
        await db.commit()
        job_ids = {completed_job.id, failed_job.id}

    monkeypatch.setattr(
        "app.core.database.async_session_factory",
        bulk_result_db,
    )
    summary = await _collect_result_summary(job_ids)

    assert summary["healthy"] is False
    assert summary["total_samples"] == 1
    assert summary["total_facts"] == 1
    assert summary["total_candidates"] == 2
    assert summary["qa_flagged_candidates"] == 1
    assert summary["missing_evidence_candidates"] == 0
    assert summary["exact_duplicate_rows"] == 1
    assert summary["failed_jobs"] == 1
    assert summary["attention_required_papers"] == 2
    failed = next(
        paper for paper in summary["papers"] if paper["status"] == "failed"
    )
    assert failed["error_code"] == "llm_timeout"
