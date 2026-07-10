"""FastAPI dependencies for open workspace resource access."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate_record import CandidateRecord
from app.models.export_job import ExportJob
from app.models.paper import Paper
from app.models.project import Project


def not_found(resource: str = "资源") -> HTTPException:
    return HTTPException(status_code=404, detail=f"{resource}不存在")


def ensure_same_project(expected_project_id: int, actual_project_id: int | None, resource: str) -> None:
    if actual_project_id != expected_project_id:
        raise not_found(resource)


async def get_project_or_404(
    db: AsyncSession,
    project_id: int,
    *,
    include_archived: bool = False,
) -> Project:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise not_found("项目")
    if not include_archived and project.archived_at is not None:
        raise not_found("项目")
    return project


async def get_paper_or_404(db: AsyncSession, project_id: int, paper_id: int) -> Paper:
    result = await db.execute(select(Paper).where(Paper.id == paper_id))
    paper = result.scalar_one_or_none()
    if paper is None:
        raise not_found("文献")
    ensure_same_project(project_id, paper.project_id, "文献")
    return paper


async def get_candidate_or_404(
    db: AsyncSession,
    project_id: int,
    candidate_id: int,
) -> CandidateRecord:
    result = await db.execute(select(CandidateRecord).where(CandidateRecord.id == candidate_id))
    candidate = result.scalar_one_or_none()
    if candidate is None:
        raise not_found("候选记录")
    ensure_same_project(project_id, candidate.project_id, "候选记录")
    return candidate


async def get_export_or_404(db: AsyncSession, project_id: int, export_id: int) -> ExportJob:
    result = await db.execute(select(ExportJob).where(ExportJob.id == export_id))
    export = result.scalar_one_or_none()
    if export is None:
        raise not_found("导出任务")
    ensure_same_project(project_id, export.project_id, "导出任务")
    return export
