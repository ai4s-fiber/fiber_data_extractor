"""Shared helpers for removing candidate records and dependent rows."""

from sqlalchemy import select, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate_record import CandidateRecord
from app.models.evidence_item import EvidenceItem
from app.models.review_log import ReviewLog


async def purge_candidate_records(
    db: AsyncSession,
    project_id: int,
    record_ids: list[int],
) -> int:
    """Hard-delete candidate rows and dependent review/evidence links."""
    if not record_ids:
        return 0
    result = await db.execute(
        select(CandidateRecord).where(
            CandidateRecord.id.in_(record_ids),
            CandidateRecord.project_id == project_id,
        )
    )
    records = list(result.scalars().all())
    if not records:
        return 0
    ids = [record.id for record in records]
    await db.execute(sa_delete(ReviewLog).where(ReviewLog.candidate_record_id.in_(ids)))
    await db.execute(
        sa_delete(EvidenceItem).where(EvidenceItem.candidate_record_id.in_(ids))
    )
    for record in records:
        await db.delete(record)
    return len(records)
