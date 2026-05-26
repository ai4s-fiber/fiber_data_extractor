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
from app.core.deps import get_current_user, require_project_role
from app.models.user import User
from app.models.candidate_record import CandidateRecord
from app.models.export_job import ExportJob
from app.schemas.export import ExportRequest, ExportJobOut
from app.services.excel_export import generate_excel

router = APIRouter(prefix="/projects/{project_id}/exports", tags=["导出"])

EXCEL_COLUMNS = [
    "record_id", "paper_id", "paper_title", "doi_or_url", "year", "journal",
    "sample_group_id", "sample_id", "material_system", "fiber_type",
    "variable_name", "variable_value", "variable_unit",
    "composition_expression", "matrix_name", "matrix_content", "matrix_unit",
    "additive_expression", "solvent_or_aid", "composition_evidence",
    "process_route", "spinning_method", "process_parameters", "post_treatment",
    "process_evidence", "structure_methods", "structure_features",
    "structure_evidence", "performance_category", "performance_metric",
    "performance_value", "performance_unit", "performance_method",
    "performance_condition", "performance_evidence", "extraction_method",
    "evidence_text", "ai_confidence", "review_status", "reviewer_comment",
]

# Mapping from ORM field names to Excel column names
FIELD_MAP = {
    "paper_id_str": "paper_id",
}


@router.post("", response_model=ExportJobOut, status_code=201)
async def create_export(
    project_id: int, body: ExportRequest,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await require_project_role(project_id, user, db, ["admin", "reviewer"])
    statuses = body.review_status_filter or ["approved"]
    q = select(CandidateRecord).where(
        CandidateRecord.project_id == project_id,
        CandidateRecord.review_status.in_(statuses),
    ).order_by(CandidateRecord.id)
    result = await db.execute(q)
    records = result.scalars().all()

    if not records:
        raise HTTPException(400, f"没有找到状态为 {statuses} 的候选记录，请先在审核队列中审批通过。")

    # Build rows
    rows = []
    for rec in records:
        row = {}
        for col in EXCEL_COLUMNS:
            orm_field = col
            if col == "paper_id":
                orm_field = "paper_id_str"
            row[col] = getattr(rec, orm_field, None)
        rows.append(row)

    # Generate Excel
    export_dir = Path(settings.EXPORT_DIR) / str(project_id)
    export_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"数据主表_{ts}.xlsx"
    filepath = export_dir / filename
    generate_excel(rows, EXCEL_COLUMNS, str(filepath))

    job = ExportJob(
        project_id=project_id, created_by=user.id, status="completed",
        filter_json=json.dumps({"review_status": statuses}),
        file_object_key=str(filepath),
        finished_at=datetime.now(timezone.utc),
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)
    return ExportJobOut.model_validate(job)


@router.get("", response_model=list[ExportJobOut])
async def list_exports(
    project_id: int,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await require_project_role(project_id, user, db, ["admin", "reviewer"])
    result = await db.execute(
        select(ExportJob).where(ExportJob.project_id == project_id)
        .order_by(ExportJob.created_at.desc()))
    return [ExportJobOut.model_validate(j) for j in result.scalars().all()]


@router.get("/{export_id}/download")
async def download_export(
    project_id: int, export_id: int,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await require_project_role(project_id, user, db, ["admin", "reviewer"])
    result = await db.execute(
        select(ExportJob).where(
            ExportJob.id == export_id, ExportJob.project_id == project_id))
    job = result.scalar_one_or_none()
    if not job or not job.file_object_key:
        raise HTTPException(404, "导出文件不存在")
    fp = Path(job.file_object_key)
    if not fp.exists():
        raise HTTPException(404, "导出文件已删除")
    return FileResponse(str(fp), filename="数据主表.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.delete("/{export_id}", status_code=204)
async def delete_export(
    project_id: int, export_id: int,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    await require_project_role(project_id, user, db, ["admin", "reviewer"])
    result = await db.execute(
        select(ExportJob).where(
            ExportJob.id == export_id, ExportJob.project_id == project_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "导出记录不存在")
    # Delete file from disk
    if job.file_object_key:
        fp = Path(job.file_object_key)
        if fp.exists():
            fp.unlink()
    await db.delete(job)
