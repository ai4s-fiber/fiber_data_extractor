"""Delete a paper and all dependent database rows in FK-safe order."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.document_parse import (
    DocumentBlock,
    DocumentFigure,
    DocumentParseRun,
    DocumentTable,
)
from app.models.extraction_job import ExtractionJob
from app.models.page_inventory import PageInventory
from app.models.paper import Paper
from app.services.extraction_results import purge_extraction_results


async def purge_paper(
    db: AsyncSession,
    project_id: int,
    paper: Paper,
) -> None:
    """Remove all rows and files tied to one paper."""
    paper_id = paper.id

    await purge_extraction_results(db, project_id, paper_id)
    await db.execute(sa_delete(PageInventory).where(PageInventory.paper_id == paper_id))

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
