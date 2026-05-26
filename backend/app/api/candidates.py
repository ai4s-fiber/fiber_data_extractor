"""Candidate record routes: CRUD, review actions, list for review queue."""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_project_role
from app.models.user import User
from app.models.candidate_record import CandidateRecord
from app.models.review_log import ReviewLog
from app.schemas.candidate import (
    CandidateRecordCreate, CandidateRecordUpdate,
    CandidateRecordOut, CandidateListItem, ReviewAction,
)

router = APIRouter(prefix="/projects/{project_id}/candidates", tags=["候选记录"])


@router.get("", response_model=list[CandidateListItem])
async def list_candidates(
    project_id: int,
    review_status: str | None = Query(None),
    paper_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_project_role(project_id, user, db, ["admin", "reviewer", "student"])
    q = select(CandidateRecord).where(CandidateRecord.project_id == project_id)
    if review_status:
        q = q.where(CandidateRecord.review_status == review_status)
    if paper_id:
        q = q.where(CandidateRecord.source_paper_id == paper_id)
    q = q.order_by(CandidateRecord.created_at.desc())
    q = q.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(q)
    return [CandidateListItem.model_validate(r) for r in result.scalars().all()]


@router.get("/count")
async def count_candidates(
    project_id: int,
    review_status: str | None = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_project_role(project_id, user, db, ["admin", "reviewer", "student"])
    q = select(func.count(CandidateRecord.id)).where(CandidateRecord.project_id == project_id)
    if review_status:
        q = q.where(CandidateRecord.review_status == review_status)
    result = await db.execute(q)
    return {"count": result.scalar() or 0}


@router.post("", response_model=CandidateRecordOut, status_code=201)
async def create_candidate(
    project_id: int, body: CandidateRecordCreate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await require_project_role(project_id, user, db, ["admin", "reviewer", "student"])
    record = CandidateRecord(project_id=project_id, **body.model_dump())
    db.add(record)
    await db.flush()
    await db.refresh(record)
    return CandidateRecordOut.model_validate(record)


@router.get("/{candidate_id}", response_model=CandidateRecordOut)
async def get_candidate(
    project_id: int, candidate_id: int,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await require_project_role(project_id, user, db, ["admin", "reviewer", "student"])
    result = await db.execute(
        select(CandidateRecord).where(
            CandidateRecord.id == candidate_id, CandidateRecord.project_id == project_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, "候选记录不存在")
    return CandidateRecordOut.model_validate(record)


@router.patch("/{candidate_id}", response_model=CandidateRecordOut)
async def update_candidate(
    project_id: int, candidate_id: int, body: CandidateRecordUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await require_project_role(project_id, user, db, ["admin", "reviewer", "student"])
    result = await db.execute(
        select(CandidateRecord).where(
            CandidateRecord.id == candidate_id, CandidateRecord.project_id == project_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, "候选记录不存在")
    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(record, field, value)
    log = ReviewLog(project_id=project_id, candidate_record_id=candidate_id,
                    user_id=user.id, action="modified", new_value=str(update_data))
    db.add(log)
    await db.flush()
    await db.refresh(record)
    return CandidateRecordOut.model_validate(record)


@router.delete("/{candidate_id}", status_code=204)
async def delete_candidate(
    project_id: int, candidate_id: int,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await require_project_role(project_id, user, db, ["admin", "reviewer"])
    result = await db.execute(
        select(CandidateRecord).where(
            CandidateRecord.id == candidate_id, CandidateRecord.project_id == project_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, "候选记录不存在")
    log = ReviewLog(project_id=project_id, candidate_record_id=candidate_id,
                    user_id=user.id, action="deleted")
    db.add(log)
    await db.delete(record)


@router.post("/{candidate_id}/review", response_model=CandidateRecordOut)
async def review_candidate(
    project_id: int, candidate_id: int, body: ReviewAction,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await require_project_role(project_id, user, db, ["admin", "reviewer"])
    valid = {"approved", "modified", "uncertain", "missing", "deleted"}
    if body.action not in valid:
        raise HTTPException(400, f"无效操作: {body.action}")
    result = await db.execute(
        select(CandidateRecord).where(
            CandidateRecord.id == candidate_id, CandidateRecord.project_id == project_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, "候选记录不存在")
    old_status = record.review_status
    record.review_status = body.action
    record.reviewed_by = user.id
    record.reviewed_at = datetime.now(timezone.utc)
    if body.comment:
        record.reviewer_comment = body.comment
    if body.action == "approved":
        record.candidate_status = "approved"
    elif body.action == "deleted":
        record.candidate_status = "rejected"
    else:
        record.candidate_status = "submitted"
    log = ReviewLog(project_id=project_id, candidate_record_id=candidate_id,
                    user_id=user.id, action=body.action,
                    old_value=old_status, new_value=body.action, comment=body.comment)
    db.add(log)
    await db.flush()
    await db.refresh(record)

    # Update paper status: if ALL candidates for this paper are reviewed → completed
    from app.models.paper import Paper
    paper_id = record.source_paper_id
    pending_count = await db.execute(
        select(func.count(CandidateRecord.id)).where(
            CandidateRecord.source_paper_id == paper_id,
            CandidateRecord.review_status == "pending",
        )
    )
    if pending_count.scalar() == 0:
        await db.execute(
            Paper.__table__.update()
            .where(Paper.id == paper_id)
            .values(status="completed")
        )

    return CandidateRecordOut.model_validate(record)


@router.post("/batch-approve")
async def batch_approve(
    project_id: int, ids: list[int],
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await require_project_role(project_id, user, db, ["admin", "reviewer"])
    result = await db.execute(
        select(CandidateRecord).where(
            CandidateRecord.id.in_(ids), CandidateRecord.project_id == project_id))
    records = result.scalars().all()
    count = 0
    paper_ids = set()
    for r in records:
        r.review_status = "approved"
        r.candidate_status = "approved"
        r.reviewed_by = user.id
        r.reviewed_at = datetime.now(timezone.utc)
        db.add(ReviewLog(project_id=project_id, candidate_record_id=r.id,
                         user_id=user.id, action="approved"))
        paper_ids.add(r.source_paper_id)
        count += 1
    await db.flush()

    # Update paper statuses for all affected papers
    from app.models.paper import Paper
    for pid in paper_ids:
        pending_count = await db.execute(
            select(func.count(CandidateRecord.id)).where(
                CandidateRecord.source_paper_id == pid,
                CandidateRecord.review_status == "pending",
            )
        )
        if pending_count.scalar() == 0:
            await db.execute(
                Paper.__table__.update()
                .where(Paper.id == pid)
                .values(status="completed")
            )

    return {"approved_count": count}
