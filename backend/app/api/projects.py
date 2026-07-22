"""Project routes for the open workspace."""

from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_project_or_404
from app.models.candidate_record import CandidateRecord
from app.models.extraction_job import ExtractionJob
from app.models.export_job import ExportJob
from app.models.paper import Paper
from app.models.project import Project
from app.schemas.project import (
    ProjectCreate,
    ProjectLLMConfigOut,
    ProjectLLMConfigUpdate,
    ProjectOut,
    ProjectUpdate,
)
from app.services.llm_diagnostics import test_openai_compatible_connection

router = APIRouter(prefix="/projects", tags=["项目"])


async def _project_stats(db: AsyncSession, project_id: int) -> dict[str, int]:
    paper_count = await db.scalar(select(func.count(Paper.id)).where(Paper.project_id == project_id))
    pending = await db.scalar(
        select(func.count(CandidateRecord.id)).where(
            CandidateRecord.project_id == project_id,
            CandidateRecord.review_status == "pending",
        )
    )
    approved = await db.scalar(
        select(func.count(CandidateRecord.id)).where(
            CandidateRecord.project_id == project_id,
            CandidateRecord.review_status == "approved",
        )
    )
    return {
        "paper_count": int(paper_count or 0),
        "pending_count": int(pending or 0),
        "approved_count": int(approved or 0),
    }


async def _build_project_out(db: AsyncSession, project: Project) -> ProjectOut:
    payload = ProjectOut.model_validate(project)
    stats = await _project_stats(db, project.id)
    payload.paper_count = stats["paper_count"]
    payload.pending_count = stats["pending_count"]
    payload.approved_count = stats["approved_count"]
    return payload


def _masked_api_key(raw_key: str | None) -> str:
    key = raw_key or ""
    if len(key) > 8:
        return f"{key[:6]}...{key[-4:]}"
    return "******" if key else ""


@router.get("", response_model=list[ProjectOut])
async def list_projects(db: AsyncSession = Depends(get_db)):
    """List all active projects in the open workspace."""
    result = await db.execute(
        select(Project).where(Project.archived_at.is_(None)).order_by(Project.updated_at.desc())
    )
    return [await _build_project_out(db, project) for project in result.scalars().all()]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a project without user ownership."""
    project = Project(
        name=body.name,
        description=body.description,
        llm_provider=settings.DEFAULT_LLM_PROVIDER,
        llm_base_url=settings.DEFAULT_LLM_BASE_URL,
        llm_model=settings.DEFAULT_LLM_MODEL,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)
    return await _build_project_out(db, project)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    project = await get_project_or_404(db, project_id)
    return await _build_project_out(db, project)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: int,
    body: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
):
    project = await get_project_or_404(db, project_id)
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    await db.flush()
    await db.refresh(project)
    return await _build_project_out(db, project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    project = await get_project_or_404(db, project_id, include_archived=True)
    project.archived_at = datetime.now(timezone.utc)
    await db.execute(sa_delete(ExtractionJob).where(ExtractionJob.project_id == project_id))
    await db.execute(sa_delete(ExportJob).where(ExportJob.project_id == project_id))


@router.get("/{project_id}/llm-config", response_model=ProjectLLMConfigOut)
async def get_project_llm_config(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    project = await get_project_or_404(db, project_id)
    return ProjectLLMConfigOut(
        llm_provider=project.llm_provider,
        llm_api_key_masked=_masked_api_key(project.llm_api_key),
        llm_base_url=project.llm_base_url,
        llm_model=project.llm_model,
    )


@router.put("/{project_id}/llm-config", response_model=ProjectLLMConfigOut)
async def update_project_llm_config(
    project_id: int,
    body: ProjectLLMConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    project = await get_project_or_404(db, project_id)
    if body.llm_provider is not None:
        project.llm_provider = body.llm_provider
    if body.llm_api_key is not None and not ("..." in body.llm_api_key or body.llm_api_key == "******"):
        project.llm_api_key = body.llm_api_key
    if body.llm_base_url is not None:
        project.llm_base_url = body.llm_base_url
    if body.llm_model is not None:
        project.llm_model = body.llm_model
    await db.flush()
    await db.refresh(project)
    return ProjectLLMConfigOut(
        llm_provider=project.llm_provider,
        llm_api_key_masked=_masked_api_key(project.llm_api_key),
        llm_base_url=project.llm_base_url,
        llm_model=project.llm_model,
    )


@router.post("/{project_id}/llm-config/test")
async def test_llm_connection(
    project_id: int,
    body: ProjectLLMConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    project = await get_project_or_404(db, project_id)
    api_key = body.llm_api_key
    if not api_key or "..." in api_key or api_key == "******":
        api_key = project.llm_api_key
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key 不能为空")
    provider = body.llm_provider or project.llm_provider or settings.DEFAULT_LLM_PROVIDER
    base_url = body.llm_base_url or project.llm_base_url or settings.DEFAULT_LLM_BASE_URL
    model = body.llm_model or project.llm_model or settings.DEFAULT_LLM_MODEL
    if not provider.lower().startswith("anthropic"):
        diagnostic = await test_openai_compatible_connection(
            api_key=api_key,
            model=model,
            raw_base_url=base_url,
        )
        if diagnostic.get("success") and diagnostic.get("working_base_url"):
            project.llm_base_url = diagnostic["working_base_url"]
            project.llm_provider = provider
            project.llm_model = model
            if body.llm_api_key and "..." not in body.llm_api_key and body.llm_api_key != "******":
                project.llm_api_key = body.llm_api_key
            await db.flush()
        return diagnostic

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Hello! Reply with 'OK' only."}],
        "max_tokens": 10,
    }
    anthropic_base = base_url.rstrip("/")
    url = f"{anthropic_base}/messages" if anthropic_base.endswith("/v1") else f"{anthropic_base}/v1/messages"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code == 200:
                project.llm_base_url = base_url
                project.llm_provider = provider
                project.llm_model = model
                if body.llm_api_key and "..." not in body.llm_api_key and body.llm_api_key != "******":
                    project.llm_api_key = body.llm_api_key
                await db.flush()
                return {"success": True, "message": "连接成功"}
            return {"success": False, "message": f"HTTP {response.status_code}"}
    except Exception as exc:
        return {"success": False, "message": str(exc)}
