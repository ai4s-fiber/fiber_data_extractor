"""Candidate record routes: CRUD, review actions, list for review queue."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_candidate_or_404, get_paper_or_404, get_project_or_404
from app.models.candidate_record import CandidateRecord
from app.models.review_log import ReviewLog
from app.schemas.candidate import (
    CandidateListItem,
    CandidateRecordCreate,
    CandidateRecordOut,
    CandidateRecordUpdate,
    ReviewAction,
)
from app.services import redis_cache
from app.services.candidate_cleanup import purge_candidate_records

router = APIRouter(prefix="/projects/{project_id}/candidates", tags=["候选记录"])

REVIEW_STATUS_ALIASES = {
    "pending": ["pending", "待审核"],
    "待审核": ["pending", "待审核"],
    "approved": ["approved", "通过"],
    "通过": ["approved", "通过"],
    "modified": ["modified", "已修改"],
    "已修改": ["modified", "已修改"],
    "uncertain": ["uncertain", "存疑"],
    "存疑": ["uncertain", "存疑"],
    "missing": ["missing", "缺失"],
    "缺失": ["missing", "缺失"],
    "deleted": ["deleted", "已删除"],
    "已删除": ["deleted", "已删除"],
}


class BatchIdsBody(BaseModel):
    ids: list[int] = Field(default_factory=list)


def _review_status_values(status_value: str) -> list[str]:
    return REVIEW_STATUS_ALIASES.get(status_value, [status_value])


def _normalize_pagination(page: int = 1, page_size: int = 50) -> tuple[int, int]:
    return max(1, page), min(max(1, page_size), 200)


def _normalize_ts(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.replace(microsecond=0).isoformat()


def _ensure_record_not_stale(record: CandidateRecord, expected_updated_at: datetime | None) -> None:
    if expected_updated_at is None:
        return
    current = _normalize_ts(record.updated_at)
    expected = _normalize_ts(expected_updated_at)
    if current and expected and current != expected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "该记录已被修改，请刷新后重试",
                "current_updated_at": record.updated_at.isoformat() if record.updated_at else None,
                "review_status": record.review_status,
            },
        )


def _to_list_item(record: CandidateRecord) -> CandidateListItem:
    return CandidateListItem(
        id=record.id,
        source_paper_id=record.source_paper_id,
        sample_id=record.sample_id,
        performance_category=record.performance_category,
        performance_metric=record.performance_metric,
        performance_value=record.performance_value,
        performance_unit=record.performance_unit,
        review_status=record.review_status,
        ai_confidence=record.ai_confidence,
        evidence_text=record.evidence_text,
        reviewer_comment=record.reviewer_comment,
        candidate_status=record.candidate_status,
        source_location=record.source_location,
        paper_title=record.paper_title,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


async def _candidate_out(record: CandidateRecord) -> CandidateRecordOut:
    return CandidateRecordOut.model_validate(record)


async def _complete_paper_if_reviewed(db: AsyncSession, paper_id: int) -> None:
    from app.models.paper import Paper

    pending_count = await db.execute(
        select(func.count(CandidateRecord.id)).where(
            CandidateRecord.source_paper_id == paper_id,
            CandidateRecord.review_status.in_(_review_status_values("pending")),
        )
    )
    if pending_count.scalar() == 0:
        await db.execute(
            Paper.__table__.update()
            .where(Paper.id == paper_id)
            .values(status="completed")
        )


@router.get("", response_model=list[CandidateListItem])
async def list_candidates(
    project_id: int,
    review_status: str | None = Query(None),
    paper_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    page, page_size = _normalize_pagination(page, page_size)
    cache_suffix = f"list:{review_status or 'all'}:{paper_id or 'all'}:{page}:{page_size}"
    cached = await redis_cache.get_json(project_id, "candidates", cache_suffix)
    if cached is not None:
        return [CandidateListItem.model_validate(item) for item in cached]

    query = select(CandidateRecord).where(CandidateRecord.project_id == project_id)
    if review_status:
        query = query.where(CandidateRecord.review_status.in_(_review_status_values(review_status)))
    if paper_id:
        await get_paper_or_404(db, project_id, paper_id)
        query = query.where(CandidateRecord.source_paper_id == paper_id)
    query = query.order_by(CandidateRecord.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = [_to_list_item(record) for record in result.scalars().all()]
    await redis_cache.set_json(
        project_id,
        "candidates",
        cache_suffix,
        [item.model_dump(mode="json") for item in items],
    )
    return items


@router.get("/count")
async def count_candidates(
    project_id: int,
    review_status: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    query = select(func.count(CandidateRecord.id)).where(CandidateRecord.project_id == project_id)
    if review_status:
        query = query.where(CandidateRecord.review_status.in_(_review_status_values(review_status)))
    result = await db.execute(query)
    return {"count": result.scalar() or 0}


@router.post("", response_model=CandidateRecordOut, status_code=201)
async def create_candidate(
    project_id: int,
    body: CandidateRecordCreate,
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    await get_paper_or_404(db, project_id, body.source_paper_id)
    record = CandidateRecord(project_id=project_id, **body.model_dump())
    db.add(record)
    await db.flush()
    await db.refresh(record)
    await redis_cache.bump_project_cache(project_id)
    return await _candidate_out(record)


@router.get("/{candidate_id}", response_model=CandidateRecordOut)
async def get_candidate(
    project_id: int,
    candidate_id: int,
    db: AsyncSession = Depends(get_db),
):
    record = await get_candidate_or_404(db, project_id, candidate_id)
    return await _candidate_out(record)


@router.patch("/{candidate_id}", response_model=CandidateRecordOut)
async def update_candidate(
    project_id: int,
    candidate_id: int,
    body: CandidateRecordUpdate,
    db: AsyncSession = Depends(get_db),
):
    record = await get_candidate_or_404(db, project_id, candidate_id)
    _ensure_record_not_stale(record, body.expected_updated_at)
    update_data = body.model_dump(exclude_unset=True, exclude={"expected_updated_at"})
    for field, value in update_data.items():
        setattr(record, field, value)
    record.reviewed_at = datetime.now(timezone.utc)
    db.add(
        ReviewLog(
            project_id=project_id,
            candidate_record_id=candidate_id,
            action="modified",
            new_value=str(update_data),
        )
    )
    await db.flush()
    await db.refresh(record)
    await redis_cache.bump_project_cache(project_id)
    return await _candidate_out(record)


@router.delete("/{candidate_id}", status_code=204)
async def delete_candidate(
    project_id: int,
    candidate_id: int,
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    deleted = await purge_candidate_records(db, project_id, [candidate_id])
    if deleted == 0:
        raise HTTPException(404, "候选记录不存在")
    await redis_cache.bump_project_cache(project_id)


@router.post("/batch-delete")
async def batch_delete_candidates(
    project_id: int,
    body: BatchIdsBody,
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete multiple candidate records."""
    await get_project_or_404(db, project_id)
    deleted = await purge_candidate_records(db, project_id, body.ids)
    if deleted == 0:
        raise HTTPException(404, "未找到可删除的候选记录")
    await redis_cache.bump_project_cache(project_id)
    return {"deleted_count": deleted}


@router.post("/batch-delete-by-paper")
async def batch_delete_candidates_by_paper(
    project_id: int,
    paper_id: int = Query(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete all candidate records for one paper."""
    await get_paper_or_404(db, project_id, paper_id)
    result = await db.execute(
        select(CandidateRecord.id).where(
            CandidateRecord.project_id == project_id,
            CandidateRecord.source_paper_id == paper_id,
        )
    )
    ids = [row[0] for row in result.all()]
    deleted = await purge_candidate_records(db, project_id, ids)
    await redis_cache.bump_project_cache(project_id)
    return {"deleted_count": deleted, "paper_id": paper_id}


@router.post("/{candidate_id}/review", response_model=CandidateRecordOut)
async def review_candidate(
    project_id: int,
    candidate_id: int,
    body: ReviewAction,
    db: AsyncSession = Depends(get_db),
):
    valid = {"approved", "modified", "uncertain", "missing", "deleted"}
    if body.action not in valid:
        raise HTTPException(400, f"无效操作: {body.action}")
    record = await get_candidate_or_404(db, project_id, candidate_id)
    _ensure_record_not_stale(record, body.expected_updated_at)
    old_status = record.review_status
    record.review_status = body.action
    record.reviewed_at = datetime.now(timezone.utc)
    if body.comment:
        record.reviewer_comment = body.comment
    if body.action == "approved":
        record.candidate_status = "approved"
    elif body.action == "deleted":
        record.candidate_status = "rejected"
    else:
        record.candidate_status = "submitted"
    db.add(
        ReviewLog(
            project_id=project_id,
            candidate_record_id=candidate_id,
            action=body.action,
            old_value=old_status,
            new_value=body.action,
            comment=body.comment,
        )
    )
    await db.flush()
    await db.refresh(record)
    await _complete_paper_if_reviewed(db, record.source_paper_id)
    await redis_cache.bump_project_cache(project_id)
    return await _candidate_out(record)


@router.post("/batch-approve")
async def batch_approve(
    project_id: int,
    ids: list[int],
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    result = await db.execute(
        select(CandidateRecord).where(
            CandidateRecord.id.in_(ids),
            CandidateRecord.project_id == project_id,
        )
    )
    records = result.scalars().all()
    count = 0
    paper_ids = set()
    for record in records:
        record.review_status = "approved"
        record.candidate_status = "approved"
        record.reviewed_at = datetime.now(timezone.utc)
        db.add(
            ReviewLog(
                project_id=project_id,
                candidate_record_id=record.id,
                action="approved",
            )
        )
        paper_ids.add(record.source_paper_id)
        count += 1
    await db.flush()
    for paper_id in paper_ids:
        await _complete_paper_if_reviewed(db, paper_id)
    await redis_cache.bump_project_cache(project_id)
    return {"approved_count": count}
