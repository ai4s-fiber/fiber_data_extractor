"""Export routes: trigger export, download, list history."""

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_export_or_404, get_project_or_404
from app.models.candidate_record import CandidateRecord
from app.models.document_parse import DocumentBlock
from app.models.evidence_item import EvidenceItem
from app.models.export_job import ExportJob
from app.models.paper import Paper
from app.schemas.export import ExportCreateResult, ExportJobOut, ExportRequest
from app.services import redis_cache
from app.services.candidate_cleanup import purge_candidate_records
from app.services.workbook_export import generate_structured_workbook

router = APIRouter(prefix="/projects/{project_id}/exports", tags=["导出"])

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


def _expand_review_statuses(statuses: list[str]) -> list[str]:
    values: list[str] = []
    for status in statuses:
        values.extend(REVIEW_STATUS_ALIASES.get(status, [status]))
    return sorted(set(values))


@router.post("", response_model=ExportCreateResult, status_code=201)
async def create_export(
    project_id: int,
    body: ExportRequest,
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    statuses = body.review_status_filter or ["approved"]
    status_values = _expand_review_statuses(statuses)
    query = (
        select(CandidateRecord)
        .where(
            CandidateRecord.project_id == project_id,
            CandidateRecord.review_status.in_(status_values),
        )
        .order_by(CandidateRecord.id)
    )
    result = await db.execute(query)
    records = result.scalars().all()

    if not records:
        raise HTTPException(400, f"没有找到状态为 {statuses} 的候选记录，请先在审核队列中审批通过。")

    record_ids = [record.id for record in records]
    paper_ids = sorted({record.source_paper_id for record in records})

    papers_result = await db.execute(select(Paper).where(Paper.id.in_(paper_ids)))
    papers = papers_result.scalars().all()

    evidence_result = await db.execute(
        select(EvidenceItem)
        .where(EvidenceItem.candidate_record_id.in_(record_ids))
        .order_by(EvidenceItem.id)
    )
    evidence_items = evidence_result.scalars().all()

    blocks_result = await db.execute(
        select(DocumentBlock)
        .where(DocumentBlock.paper_id.in_(paper_ids))
        .order_by(DocumentBlock.paper_id, DocumentBlock.page_number, DocumentBlock.order_index)
    )
    document_blocks = blocks_result.scalars().all()

    export_dir = Path(settings.EXPORT_DIR) / str(project_id)
    export_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"数据主表_{timestamp}.xlsx"
    filepath = export_dir / filename
    generate_structured_workbook(
        records=records,
        papers=papers,
        evidence_items=evidence_items,
        document_blocks=document_blocks,
        filepath=str(filepath),
    )

    job = ExportJob(
        project_id=project_id,
        status="completed",
        filter_json=json.dumps({"review_status": statuses}, ensure_ascii=False),
        file_object_key=str(filepath),
        finished_at=datetime.now(timezone.utc),
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)

    cleared_count = await purge_candidate_records(db, project_id, record_ids)
    await redis_cache.bump_project_cache(project_id)

    return ExportCreateResult(
        **ExportJobOut.model_validate(job).model_dump(),
        exported_record_count=len(records),
        cleared_record_count=cleared_count,
    )


@router.get("", response_model=list[ExportJobOut])
async def list_exports(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    result = await db.execute(
        select(ExportJob)
        .where(ExportJob.project_id == project_id)
        .order_by(ExportJob.created_at.desc())
    )
    return [ExportJobOut.model_validate(job) for job in result.scalars().all()]


@router.get("/{export_id}/download")
async def download_export(
    project_id: int,
    export_id: int,
    db: AsyncSession = Depends(get_db),
):
    job = await get_export_or_404(db, project_id, export_id)
    if not job.file_object_key:
        raise HTTPException(404, "导出文件不存在")
    filepath = Path(job.file_object_key)
    if not filepath.exists():
        raise HTTPException(404, "导出文件已删除")
    return FileResponse(
        str(filepath),
        filename="数据主表.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.delete("/{export_id}", status_code=204)
async def delete_export(
    project_id: int,
    export_id: int,
    db: AsyncSession = Depends(get_db),
):
    job = await get_export_or_404(db, project_id, export_id)
    if job.file_object_key:
        filepath = Path(job.file_object_key)
        if filepath.exists():
            filepath.unlink()
    await db.delete(job)
