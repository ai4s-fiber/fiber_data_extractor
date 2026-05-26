"""Paper (literature) routes: upload, list, update, delete, extraction status."""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status, BackgroundTasks
from sqlalchemy import select, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user, require_project_role
from app.models.user import User
from app.models.paper import Paper
from app.models.candidate_record import CandidateRecord
from app.models.evidence_item import EvidenceItem
from app.models.page_inventory import PageInventory
from app.schemas.paper import PaperOut, PaperUpdate
from app.services.extractor import V6ExtractorService

router = APIRouter(prefix="/projects/{project_id}/papers", tags=["文献"])

# In-memory progress tracking for active extraction tasks
_extraction_progress: dict[int, dict] = {}


async def run_extraction_task(paper_id: int):
    """Background task to run high-fidelity V6 extraction pipeline."""
    from app.core.database import async_session_factory

    _extraction_progress[paper_id] = {"step": "starting", "percent": 0}
    async with async_session_factory() as db:
        try:

            async def progress_callback(step: str, percent: int):
                _extraction_progress[paper_id] = {"step": step, "percent": percent}

            await V6ExtractorService.run_full_pipeline_for_paper(
                db, paper_id, progress_callback=progress_callback
            )
            _extraction_progress[paper_id] = {"step": "completed", "percent": 100}
        except Exception as e:
            _extraction_progress[paper_id] = {
                "step": "failed",
                "percent": 0,
                "error": str(e),
            }
            print(f"Background extraction failed for paper {paper_id}: {str(e)}")


@router.get("/{paper_id}/extraction-status")
async def get_extraction_status(
    project_id: int,
    paper_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取文献抽取进度。"""
    await require_project_role(project_id, user, db, ["admin", "reviewer", "student"])
    result = await db.execute(
        select(Paper).where(Paper.id == paper_id, Paper.project_id == project_id)
    )
    paper = result.scalar_one_or_none()
    if paper is None:
        raise HTTPException(status_code=404, detail="文献不存在")

    progress = _extraction_progress.get(paper_id, {})
    return {
        "paper_id": paper_id,
        "paper_status": paper.status,
        "extraction_step": progress.get("step", ""),
        "extraction_percent": progress.get("percent", 0),
        "error": progress.get("error", ""),
    }


@router.get("", response_model=list[PaperOut])
async def list_papers(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出项目中的所有文献。"""
    await require_project_role(project_id, user, db, ["admin", "reviewer", "student"])
    result = await db.execute(
        select(Paper)
        .where(Paper.project_id == project_id)
        .order_by(Paper.created_at.desc())
    )
    return [PaperOut.model_validate(p) for p in result.scalars().all()]


@router.post("", response_model=PaperOut, status_code=status.HTTP_201_CREATED)
async def upload_paper(
    project_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传 PDF 文件并在后台异步启动 V6 精准抽取流水线。"""
    await require_project_role(project_id, user, db, ["admin", "reviewer", "student"])

    # Validate file type
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="只支持 PDF 文件")

    # Validate file size (100 MB limit)
    contents = await file.read()
    if len(contents) > 100 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件大小不能超过 100 MB")

    # Save file
    file_key = f"{project_id}/{uuid.uuid4().hex}.pdf"
    file_path = Path(settings.UPLOAD_DIR) / file_key
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "wb") as f:
        f.write(contents)

    paper = Paper(
        project_id=project_id,
        uploaded_by=user.id,
        original_filename=file.filename,
        file_object_key=file_key,
        paper_title=Path(file.filename).stem,  # Use filename stem as initial title
        status="uploaded",
    )
    db.add(paper)
    await db.flush()
    await db.refresh(paper)
    
    # Enqueue background extraction task
    background_tasks.add_task(run_extraction_task, paper.id)
    
    return PaperOut.model_validate(paper)


@router.post("/{paper_id}/extract", status_code=status.HTTP_202_ACCEPTED)
async def trigger_extraction(
    project_id: int,
    paper_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """手动触发/重新执行精准抽取流水线。"""
    await require_project_role(project_id, user, db, ["admin", "reviewer"])
    result = await db.execute(
        select(Paper).where(Paper.id == paper_id, Paper.project_id == project_id)
    )
    paper = result.scalar_one_or_none()
    if paper is None:
        raise HTTPException(status_code=404, detail="文献不存在")
        
    paper.status = "extracting"
    db.add(paper)
    await db.flush()
    
    background_tasks.add_task(run_extraction_task, paper.id)
    return {"message": "已将抽取任务添加至后台队列"}


@router.get("/{paper_id}", response_model=PaperOut)
async def get_paper(
    project_id: int,
    paper_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取文献详情。"""
    await require_project_role(project_id, user, db, ["admin", "reviewer", "student"])
    result = await db.execute(
        select(Paper).where(Paper.id == paper_id, Paper.project_id == project_id)
    )
    paper = result.scalar_one_or_none()
    if paper is None:
        raise HTTPException(status_code=404, detail="文献不存在")
    return PaperOut.model_validate(paper)


@router.patch("/{paper_id}", response_model=PaperOut)
async def update_paper(
    project_id: int,
    paper_id: int,
    body: PaperUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新文献信息。"""
    await require_project_role(project_id, user, db, ["admin", "reviewer"])
    result = await db.execute(
        select(Paper).where(Paper.id == paper_id, Paper.project_id == project_id)
    )
    paper = result.scalar_one_or_none()
    if paper is None:
        raise HTTPException(status_code=404, detail="文献不存在")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(paper, field, value)
    await db.flush()
    await db.refresh(paper)
    return PaperOut.model_validate(paper)


@router.delete("/{paper_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_paper(
    project_id: int,
    paper_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除文献及其关联的所有候选记录、证据和数据（仅管理员/审核员）。"""
    await require_project_role(project_id, user, db, ["admin", "reviewer"])
    result = await db.execute(
        select(Paper).where(Paper.id == paper_id, Paper.project_id == project_id)
    )
    paper = result.scalar_one_or_none()
    if paper is None:
        raise HTTPException(status_code=404, detail="文献不存在")
    # Cascade delete related records
    await db.execute(sa_delete(EvidenceItem).where(EvidenceItem.paper_id == paper_id))
    await db.execute(sa_delete(CandidateRecord).where(CandidateRecord.source_paper_id == paper_id))
    await db.execute(sa_delete(PageInventory).where(PageInventory.paper_id == paper_id))
    # Delete PDF file
    if paper.file_object_key:
        fp = Path(settings.UPLOAD_DIR) / paper.file_object_key
        if fp.exists():
            fp.unlink()
    await db.delete(paper)
