"""Transactional helpers for replacing or preserving extraction results."""

from __future__ import annotations

from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate_record import CandidateRecord
from app.models.evidence_item import EvidenceItem
from app.models.fact_candidate import FactCandidate
from app.models.paper import Paper
from app.models.sample_catalog import SampleCatalog
from app.services.candidate_cleanup import purge_candidate_records


async def extraction_result_count(db: AsyncSession, paper_id: int) -> int:
    """Return the number of durable candidate rows for a paper."""
    result = await db.execute(
        select(func.count(CandidateRecord.id)).where(
            CandidateRecord.source_paper_id == paper_id
        )
    )
    return int(result.scalar() or 0)


async def restore_paper_status_after_interruption(
    db: AsyncSession,
    paper: Paper,
    *,
    empty_status: str,
) -> str:
    """Keep previous results reviewable when a rerun fails or is cancelled."""
    paper.status = (
        "review"
        if await extraction_result_count(db, paper.id)
        else empty_status
    )
    db.add(paper)
    return paper.status


async def purge_extraction_results(
    db: AsyncSession,
    project_id: int,
    paper_id: int,
) -> int:
    """Delete one paper's extraction output in foreign-key-safe order.

    The caller owns the transaction. A rollback therefore restores the complete
    previous result set if inserting the replacement fails.
    """
    await db.execute(
        sa_delete(FactCandidate).where(FactCandidate.paper_id == paper_id)
    )

    record_result = await db.execute(
        select(CandidateRecord.id).where(
            CandidateRecord.project_id == project_id,
            CandidateRecord.source_paper_id == paper_id,
        )
    )
    record_ids = [row[0] for row in record_result.all()]
    deleted_records = await purge_candidate_records(db, project_id, record_ids)

    # Remove any evidence rows not linked to a candidate record.
    await db.execute(
        sa_delete(EvidenceItem).where(EvidenceItem.paper_id == paper_id)
    )
    await db.execute(
        sa_delete(SampleCatalog).where(SampleCatalog.paper_id == paper_id)
    )
    return deleted_records
