"""Delete a paper and all dependent database rows in FK-safe order."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.candidate_record import CandidateRecord
from app.models.document_parse import (
    DocumentBlock,
    DocumentFigure,
    DocumentParseRun,
    DocumentTable,
)
from app.models.evidence_item import EvidenceItem
from app.models.extraction_job import ExtractionJob
from app.models.fact_candidate import FactCandidate
from app.models.page_inventory import PageInventory
from app.models.paper import Paper
from app.models.sample_catalog import SampleCatalog
from app.services.candidate_cleanup import purge_candidate_records


async def purge_paper(
    db: AsyncSession,
    project_id: int,
    paper: Paper,
) -> None:
    """Remove all rows and files tied to one paper."""
    paper_id = paper.id

    record_result = await db.execute(
        select(CandidateRecord.id).where(
            CandidateRecord.project_id == project_id,
            CandidateRecord.source_paper_id == paper_id,
        )
    )
    record_ids = [row[0] for row in record_result.all()]
    await purge_candidate_records(db, project_id, record_ids)

    await db.execute(sa_delete(EvidenceItem).where(EvidenceItem.paper_id == paper_id))
    await db.execute(sa_delete(PageInventory).where(PageInventory.paper_id == paper_id))
    await db.execute(sa_delete(SampleCatalog).where(SampleCatalog.paper_id == paper_id))
    await db.execute(sa_delete(FactCandidate).where(FactCandidate.paper_id == paper_id))

    await db.execute(sa_delete(DocumentBlock).where(DocumentBlock.paper_id == paper_id))
    await db.execute(sa_delete(DocumentTable).where(DocumentTable.paper_id == paper_id))
    await db.execute(sa_delete(DocumentFigure).where(DocumentFigure.paper_id == paper_id))
    await db.execute(sa_delete(DocumentParseRun).where(DocumentParseRun.paper_id == paper_id))
    await db.execute(sa_delete(ExtractionJob).where(ExtractionJob.paper_id == paper_id))

    if paper.file_object_key:
        fp = Path(settings.UPLOAD_DIR) / paper.file_object_key
        if fp.exists():
            fp.unlink()
    report_fp = Path(settings.UPLOAD_DIR) / str(project_id) / f"report_{paper_id}.json"
    if report_fp.exists():
        report_fp.unlink()

    await db.delete(paper)
