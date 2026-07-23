"""Regression tests for durable extraction-result replacement."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - register every FK target in Base.metadata
from app.models.base import Base
from app.models.candidate_record import CandidateRecord
from app.models.evidence_item import EvidenceItem
from app.models.extraction_job import ExtractionJob
from app.models.fact_candidate import FactCandidate
from app.models.paper import Paper
from app.models.project import Project
from app.models.review_log import ReviewLog
from app.models.sample_catalog import SampleCatalog
from app.services import extraction_jobs
from app.services.extraction_jobs import ExtractionJobBackend
from app.services.extraction_results import purge_extraction_results
from app.services.extractor_v7.service import V7ExtractorService


@pytest.fixture
async def result_db(tmp_path):
    database_path = (tmp_path / "results.db").as_posix()
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed_previous_result(factory, *, with_job: bool = False):
    async with factory() as db:
        project = Project(name="transaction-test")
        db.add(project)
        await db.flush()
        paper = Paper(
            project_id=project.id,
            original_filename="paper.pdf",
            file_object_key="paper.pdf",
            status="review",
        )
        db.add(paper)
        await db.flush()

        job = None
        if with_job:
            job = ExtractionJob(
                project_id=project.id,
                paper_id=paper.id,
                status="running",
                step="extracting",
            )
            db.add(job)
            await db.flush()

        candidate = CandidateRecord(
            project_id=project.id,
            source_paper_id=paper.id,
            job_id=job.id if job else None,
            record_id="old-record",
            sample_id="old-sample",
            performance_metric="tensile_strength",
            performance_value="100",
            performance_unit="MPa",
            review_status="approved",
        )
        sample = SampleCatalog(
            project_id=project.id,
            paper_id=paper.id,
            sample_id="old-sample",
            sample_group_id="G001",
            confidence=0.9,
        )
        db.add_all([candidate, sample])
        await db.flush()
        evidence = EvidenceItem(
            project_id=project.id,
            paper_id=paper.id,
            job_id=job.id if job else None,
            candidate_record_id=candidate.id,
            source_type="fact_F001",
            evidence_text="old evidence",
        )
        db.add(evidence)
        await db.flush()
        db.add_all([
            FactCandidate(
                project_id=project.id,
                paper_id=paper.id,
                fact_id="F001",
                fact_type="performance",
                evidence_item_id=evidence.id,
                confidence=0.9,
            ),
            ReviewLog(
                project_id=project.id,
                candidate_record_id=candidate.id,
                action="approved",
            ),
        ])
        await db.commit()
        return project.id, paper.id, job.id if job else None


async def _count(db, model, criterion) -> int:
    result = await db.execute(select(func.count()).select_from(model).where(criterion))
    return int(result.scalar() or 0)


@pytest.mark.asyncio
async def test_result_purge_rolls_back_as_one_unit(result_db):
    project_id, paper_id, _ = await _seed_previous_result(result_db)

    async with result_db() as db:
        deleted = await purge_extraction_results(db, project_id, paper_id)
        assert deleted == 1
        await db.flush()
        assert await _count(
            db, CandidateRecord, CandidateRecord.source_paper_id == paper_id
        ) == 0
        await db.rollback()

    async with result_db() as db:
        assert await _count(
            db, CandidateRecord, CandidateRecord.source_paper_id == paper_id
        ) == 1
        assert await _count(db, EvidenceItem, EvidenceItem.paper_id == paper_id) == 1
        assert await _count(db, FactCandidate, FactCandidate.paper_id == paper_id) == 1
        assert await _count(db, SampleCatalog, SampleCatalog.paper_id == paper_id) == 1
        assert await _count(db, ReviewLog, ReviewLog.project_id == project_id) == 1


@pytest.mark.asyncio
async def test_preflight_failure_preserves_previous_results(
    result_db, tmp_path, monkeypatch
):
    project_id, paper_id, _ = await _seed_previous_result(result_db)
    (tmp_path / "paper.pdf").write_bytes(b"not parsed by this test")

    class FakeDocumentContext:
        page_count = 1
        blocks = []
        tables = []
        figures = []
        markdown_text = "Original research article with experimental results."
        parser_name = "test"
        parse_run_id = None

        @staticmethod
        def pages_as_tuples():
            return [(1, "Original research article with experimental results.")]

        @staticmethod
        def tables_as_legacy_blocks():
            return []

        @staticmethod
        def chunks():
            return [{
                "page_number": 1,
                "section_name": "results",
                "source_type": "text",
                "raw_text": "Original research article with experimental results.",
            }]

    async def fake_parse(*_args, **_kwargs):
        return FakeDocumentContext()

    monkeypatch.setattr(
        "app.services.extractor_v7.service.parse_pdf_to_document_context", fake_parse
    )
    monkeypatch.setattr(
        "app.services.extractor_v7.service.classify_document_type",
        lambda *_args: SimpleNamespace(kind="article", title="", reason=""),
    )
    monkeypatch.setattr(
        "app.services.extractor_v7.service.settings.UPLOAD_DIR", str(tmp_path)
    )

    async with result_db() as db:
        result = await V7ExtractorService.run_full_pipeline_for_paper(db, paper_id)
        assert result["error"] == "未配置 LLM API Key，无法执行 AI 抽取"

    async with result_db() as db:
        paper = await db.get(Paper, paper_id)
        assert paper.status == "review"
        assert await _count(
            db, CandidateRecord, CandidateRecord.source_paper_id == paper_id
        ) == 1
        assert await _count(db, FactCandidate, FactCandidate.paper_id == paper_id) == 1
        assert await _count(db, SampleCatalog, SampleCatalog.paper_id == paper_id) == 1


@pytest.mark.asyncio
async def test_failed_job_keeps_previous_results_reviewable(
    result_db, monkeypatch
):
    project_id, paper_id, job_id = await _seed_previous_result(
        result_db, with_job=True
    )
    backend = ExtractionJobBackend(result_db, 1)

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(backend, "_push_event", noop)
    monkeypatch.setattr(extraction_jobs.extraction_queue, "mark_finished", noop)
    monkeypatch.setattr(extraction_jobs, "bump_project_cache", noop)

    await backend.mark_failed(job_id, "upstream failed")

    async with result_db() as db:
        paper = await db.get(Paper, paper_id)
        job = await db.get(ExtractionJob, job_id)
        assert project_id == job.project_id
        assert job.status == "failed"
        assert paper.status == "review"
        assert await _count(
            db, CandidateRecord, CandidateRecord.source_paper_id == paper_id
        ) == 1
