"""Export routes: trigger export, download, list history."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, select
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
from app.services.workbook_export import generate_structured_workbook

router = APIRouter(prefix="/projects/{project_id}/exports", tags=["导出"])
MAX_WEB_EXPORT_PAPERS = 200
MAX_WEB_EXPORT_RECORDS = 50_000
MAX_WEB_EXPORT_PARSE_BLOCKS = 250_000

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


def _id_chunks(values: list[int], size: int = 500):
    for index in range(0, len(values), size):
        yield values[index : index + size]


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
    filters = (
        CandidateRecord.project_id == project_id,
        CandidateRecord.review_status.in_(status_values),
    )
    count_result = await db.execute(
        select(
            func.count(CandidateRecord.id),
            func.count(func.distinct(CandidateRecord.source_paper_id)),
        ).where(*filters)
    )
    record_count, paper_count = count_result.one()
    if not record_count:
        raise HTTPException(400, f"没有找到状态为 {statuses} 的候选记录，请先在审核队列中审批通过。")
    if (
        int(paper_count or 0) > MAX_WEB_EXPORT_PAPERS
        or int(record_count) > MAX_WEB_EXPORT_RECORDS
    ):
        raise HTTPException(
            400,
            "Web 单次导出规模过大。请使用 "
            "scripts/ops/export_project_workbooks.py 按论文原子导出并续传。",
        )

    query = (
        select(CandidateRecord)
        .where(*filters)
        .order_by(CandidateRecord.id)
    )
    result = await db.execute(query)
    records = result.scalars().all()

    record_ids = [record.id for record in records]
    paper_ids = sorted({record.source_paper_id for record in records})
    block_count_result = await db.execute(
        select(func.count(DocumentBlock.id)).where(
            DocumentBlock.paper_id.in_(paper_ids)
        )
    )
    block_count = int(block_count_result.scalar() or 0)
    if block_count > MAX_WEB_EXPORT_PARSE_BLOCKS:
        raise HTTPException(
            400,
            "Web 单次导出的解析块过多。请使用 "
            "scripts/ops/export_project_workbooks.py 按论文原子导出并续传。",
        )

    papers = []
    evidence_items = []
    document_blocks = []
    for record_id_chunk in _id_chunks(record_ids):
        evidence_result = await db.execute(
            select(EvidenceItem).where(
                EvidenceItem.candidate_record_id.in_(record_id_chunk)
            )
        )
        evidence_items.extend(evidence_result.scalars().all())
    for paper_id_chunk in _id_chunks(paper_ids):
        papers_result = await db.execute(
            select(Paper).where(Paper.id.in_(paper_id_chunk))
        )
        papers.extend(papers_result.scalars().all())

        blocks_result = await db.execute(
            select(DocumentBlock).where(
                DocumentBlock.paper_id.in_(paper_id_chunk)
            )
        )
        document_blocks.extend(blocks_result.scalars().all())
    papers.sort(key=lambda item: item.id)
    evidence_items.sort(key=lambda item: item.id)
    document_blocks.sort(
        key=lambda item: (
            item.paper_id,
            item.page_number,
            item.order_index,
            item.id,
        )
    )

    export_dir = Path(settings.EXPORT_DIR) / str(project_id)
    export_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    filename = f"数据主表_{timestamp}.xlsx"
    filepath = export_dir / filename
    await asyncio.to_thread(
        generate_structured_workbook,
        records=list(records),
        papers=list(papers),
        evidence_items=list(evidence_items),
        document_blocks=list(document_blocks),
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

    await redis_cache.bump_project_cache(project_id)

    return ExportCreateResult(
        **ExportJobOut.model_validate(job).model_dump(),
        exported_record_count=len(records),
        cleared_record_count=0,
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
