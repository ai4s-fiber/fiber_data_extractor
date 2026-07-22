"""Paper (literature) routes: upload, list, update, delete, extraction status."""

import asyncio
import json as _json_module
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, UploadFile, File, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db, async_session_factory
from app.core.deps import get_paper_or_404, get_project_or_404
from app.models.paper import Paper
from app.models.candidate_record import CandidateRecord
from app.models.extraction_job import ExtractionJob
from app.schemas.paper import ExtractionJobOut, PaperExtractRequest, PaperOut, PaperUpdate
from app.services.extraction_jobs import (
    ACTIVE_JOB_STATUSES,
    extraction_job_backend,
    normalize_model_mode,
)
from app.services.progress_bus import progress_bus
from app.services import redis_cache
from app.services.paper_cleanup import purge_paper

router = APIRouter(prefix="/projects/{project_id}/papers", tags=["文献"])
VALID_PARSER_STRATEGIES = {"mineru_cloud", "mineru_local", "mineru_local_sync", "legacy"}


async def _candidate_count_for_job(
    db: AsyncSession,
    *,
    paper_id: int,
    job_id: int | None,
) -> int:
    """Count saved candidate records for a completed extraction job."""
    if job_id:
        result = await db.execute(
            select(func.count(CandidateRecord.id)).where(
                CandidateRecord.source_paper_id == paper_id,
                CandidateRecord.job_id == job_id,
            )
        )
        count = int(result.scalar() or 0)
        if count:
            return count
    result = await db.execute(
        select(func.count(CandidateRecord.id)).where(
            CandidateRecord.source_paper_id == paper_id,
        )
    )
    return int(result.scalar() or 0)


def _candidate_count_from_report(project_id: int, paper_id: int) -> int | None:
    report_path = Path(settings.UPLOAD_DIR) / str(project_id) / f"report_{paper_id}.json"
    if not report_path.exists():
        return None
    try:
        report = _json_module.loads(report_path.read_text(encoding="utf-8"))
        value = report.get("生成记录数")
        return int(value) if value is not None else None
    except Exception:
        return None


def _job_payload(job: ExtractionJob) -> ExtractionJobOut:
    return ExtractionJobOut(
        job_id=job.id,
        paper_id=job.paper_id,
        requested_mode=job.requested_mode,
        resolved_mode=job.resolved_mode,
        parser_strategy=getattr(job, "parser_strategy", settings.DEFAULT_PARSER_STRATEGY),
        status=job.status,
        step=job.step,
        percent=job.percent,
        error_code=job.error_code,
        error_message=job.error_message,
    )


def _paper_out(paper: Paper, latest_job: ExtractionJob | None = None) -> PaperOut:
    out = PaperOut.model_validate(paper)
    if latest_job:
        out.latest_job_id = latest_job.id
        out.latest_requested_mode = latest_job.requested_mode
        out.latest_resolved_mode = latest_job.resolved_mode
        out.latest_job_status = latest_job.status
        out.latest_job_step = latest_job.step
        out.latest_job_percent = latest_job.percent
        out.latest_job_message = getattr(latest_job, "progress_message", None)
        out.latest_error_message = latest_job.error_message
    return out


async def _latest_jobs_for_papers(
    db: AsyncSession, paper_ids: list[int]
) -> dict[int, ExtractionJob]:
    if not paper_ids:
        return {}
    result = await db.execute(
        select(ExtractionJob)
        .where(ExtractionJob.paper_id.in_(paper_ids))
        .order_by(ExtractionJob.paper_id.asc(), ExtractionJob.created_at.desc(), ExtractionJob.id.desc())
    )
    latest: dict[int, ExtractionJob] = {}
    for job in result.scalars().all():
        latest.setdefault(job.paper_id, job)
    return latest


async def _latest_job_for_paper(
    db: AsyncSession, project_id: int, paper_id: int
) -> ExtractionJob | None:
    result = await db.execute(
        select(ExtractionJob)
        .where(ExtractionJob.project_id == project_id, ExtractionJob.paper_id == paper_id)
        .order_by(ExtractionJob.created_at.desc(), ExtractionJob.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _active_job_for_paper(
    db: AsyncSession, project_id: int, paper_id: int
) -> ExtractionJob | None:
    result = await db.execute(
        select(ExtractionJob)
        .where(
            ExtractionJob.project_id == project_id,
            ExtractionJob.paper_id == paper_id,
            ExtractionJob.status.in_(ACTIVE_JOB_STATUSES),
        )
        .order_by(ExtractionJob.created_at.desc(), ExtractionJob.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


@router.get("/{paper_id}/extraction-status")
async def get_extraction_status(
    project_id: int,
    paper_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取文献抽取进度。"""
    paper = await get_paper_or_404(db, project_id, paper_id)

    job = await _latest_job_for_paper(db, project_id, paper_id)
    result = {
        "paper_id": paper_id,
        "paper_status": paper.status,
        "job_id": job.id if job else None,
        "requested_mode": job.requested_mode if job else None,
        "resolved_mode": job.resolved_mode if job else None,
        "status": job.status if job else None,
        "step": job.step if job else "",
        "percent": job.percent if job else 0,
        "error_code": job.error_code if job else None,
        "error_message": job.error_message if job else None,
        "error_detail": job.error_detail if job else None,
        # Backward-compatible fields for older clients.
        "extraction_step": job.step if job else "",
        "extraction_percent": job.percent if job else 0,
        "progress_message": getattr(job, "progress_message", None) if job else None,
        "error": job.error_message if job else "",
    }
    # Include V7 extraction report summary if available
    import json as _json
    from pathlib import Path as _Path
    report_path = _Path(settings.UPLOAD_DIR) / str(project_id) / f"report_{paper_id}.json"
    if report_path.exists():
        try:
            report = _json.loads(report_path.read_text(encoding="utf-8"))
            result["extraction_summary"] = {
                "样品数": report.get("识别样品数", 0),
                "提取事实数": report.get("提取事实总数", 0),
                "生成记录数": report.get("生成记录数", 0),
                "未归属数": report.get("未归属事实数", 0),
                "待审核数": report.get("待审核数", 0),
                "存疑数": report.get("存疑数", 0),
                "缺失数": report.get("缺失数", 0),
                "推荐复核": report.get("推荐人工复核项", []),
            }
        except Exception:
            pass
    if job and job.id:
        from app.services.llm_metrics import get_job_summary

        summary = await get_job_summary(job.id)
        if summary.total_calls:
            result["llm_metrics"] = {
                "total_calls": summary.total_calls,
                "failed_calls": summary.failed_calls,
                "total_latency_ms": round(summary.total_latency_ms, 1),
                "avg_latency_ms": round(
                    summary.total_latency_ms / summary.total_calls, 1
                ) if summary.total_calls else 0,
            }
        if job.status == "completed":
            candidate_count = await _candidate_count_for_job(
                db, paper_id=paper_id, job_id=job.id,
            )
            if not candidate_count:
                candidate_count = _candidate_count_from_report(project_id, paper_id) or 0
            result["candidate_count"] = candidate_count
    return result


@router.get("", response_model=list[PaperOut])
async def list_papers(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """列出项目中的所有文献。"""
    await get_project_or_404(db, project_id)

    cached = await redis_cache.get_json(project_id, "papers", "list")
    if cached is not None:
        return [PaperOut.model_validate(item) for item in cached]

    result = await db.execute(
        select(Paper)
        .where(Paper.project_id == project_id)
        .order_by(Paper.created_at.desc())
    )
    papers = result.scalars().all()
    latest_jobs = await _latest_jobs_for_papers(db, [paper.id for paper in papers])
    outputs = [_paper_out(paper, latest_jobs.get(paper.id)) for paper in papers]

    has_active = any(
        paper.status in ("extracting", "queued")
        or (latest_jobs.get(paper.id) and latest_jobs[paper.id].status in ACTIVE_JOB_STATUSES)
        for paper in papers
    )
    if not has_active:
        await redis_cache.set_json(
            project_id,
            "papers",
            "list",
            [item.model_dump(mode="json") for item in outputs],
        )
    return outputs


@router.post("", response_model=PaperOut, status_code=status.HTTP_201_CREATED)
async def upload_paper(
    project_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """上传 PDF 文件，仅保存文献记录，不自动启动抽取。"""
    await get_project_or_404(db, project_id)

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
        original_filename=file.filename,
        file_object_key=file_key,
        paper_title=Path(file.filename).stem,  # Use filename stem as initial title
        status="uploaded",
    )
    db.add(paper)
    await db.flush()
    await db.refresh(paper)

    await redis_cache.bump_project_cache(project_id)
    return _paper_out(paper)


@router.post("/{paper_id}/extract", response_model=ExtractionJobOut, status_code=status.HTTP_202_ACCEPTED)
async def trigger_extraction(
    project_id: int,
    paper_id: int,
    body: PaperExtractRequest | None = Body(default=None),
    model_mode: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """手动触发/重新执行精准抽取流水线。model_mode: auto|weak|strong"""
    result = await db.execute(
        select(Paper).where(Paper.id == paper_id, Paper.project_id == project_id)
        .with_for_update()
    )
    paper = result.scalar_one_or_none()
    if paper is None:
        raise HTTPException(status_code=404, detail="文献不存在")

    try:
        requested_mode = normalize_model_mode(
            model_mode or (body.model_mode if body else "auto")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    active_job = await _active_job_for_paper(db, project_id, paper_id)
    if active_job:
        await extraction_job_backend.try_start_next()
        return _job_payload(active_job)

    existing_candidates = await db.scalar(
        select(func.count())
        .select_from(CandidateRecord)
        .where(
            CandidateRecord.project_id == project_id,
            CandidateRecord.source_paper_id == paper_id,
        )
    )
    confirm_wipe = bool(body and body.confirm_wipe)
    if existing_candidates and not confirm_wipe:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"该文献已有 {existing_candidates} 条候选记录，重新抽取将清空审核数据。"
                "请在请求体中传入 confirm_wipe=true 后重试。"
            ),
        )

    paper.status = "queued"
    parser_strategy = (body.parser_strategy if body and body.parser_strategy else None) or settings.DEFAULT_PARSER_STRATEGY
    if parser_strategy not in VALID_PARSER_STRATEGIES:
        raise HTTPException(
            status_code=400,
            detail=(
                "parser_strategy 必须是 mineru_cloud、mineru_local、mineru_local_sync 或 legacy，"
                f"不能使用: {parser_strategy}"
            ),
        )

    job = ExtractionJob(
        project_id=project_id,
        paper_id=paper_id,
        requested_mode=requested_mode,
        parser_strategy=parser_strategy,
        status="queued",
        step="starting",
        percent=0,
    )
    db.add(job)
    db.add(paper)
    await db.flush()
    await db.refresh(job)
    await db.commit()

    await redis_cache.bump_project_cache(project_id)
    await extraction_job_backend.enqueue(job.id)
    return _job_payload(job)


@router.get("/{paper_id}/extraction-report")
async def get_extraction_report(
    project_id: int,
    paper_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取 V7 抽取质量报告。"""
    import json as _json
    from pathlib import Path as _Path

    paper = await get_paper_or_404(db, project_id, paper_id)

    report_path = _Path(settings.UPLOAD_DIR) / str(project_id) / f"report_{paper_id}.json"
    if report_path.exists():
        return _json.loads(report_path.read_text(encoding="utf-8"))
    return {"message": "报告尚未生成", "paper_status": paper.status}


@router.get("/{paper_id}", response_model=PaperOut)
async def get_paper(
    project_id: int,
    paper_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取文献详情。"""
    paper = await get_paper_or_404(db, project_id, paper_id)
    latest_job = await _latest_job_for_paper(db, project_id, paper_id)
    return _paper_out(paper, latest_job)


@router.get("/{paper_id}/download")
async def download_paper_pdf(
    project_id: int,
    paper_id: int,
    db: AsyncSession = Depends(get_db),
):
    """下载文献原始 PDF（供审核人员对照原文）。"""
    paper = await get_paper_or_404(db, project_id, paper_id)
    if not paper.file_object_key:
        raise HTTPException(status_code=404, detail="文献文件不存在")

    file_path = Path(settings.UPLOAD_DIR) / paper.file_object_key
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="PDF 文件已丢失")

    filename = paper.original_filename or f"paper_{paper_id}.pdf"
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"
    return FileResponse(
        str(file_path),
        filename=filename,
        media_type="application/pdf",
    )


@router.patch("/{paper_id}", response_model=PaperOut)
async def update_paper(
    project_id: int,
    paper_id: int,
    body: PaperUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新文献信息。"""
    paper = await get_paper_or_404(db, project_id, paper_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(paper, field, value)
    await db.flush()
    await db.refresh(paper)
    latest_job = await _latest_job_for_paper(db, project_id, paper_id)
    return _paper_out(paper, latest_job)


@router.delete("/{paper_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_paper(
    project_id: int,
    paper_id: int,
    db: AsyncSession = Depends(get_db),
):
    """删除文献及其关联的所有候选记录、证据和数据。"""
    paper = await get_paper_or_404(db, project_id, paper_id)
    active_job = await _active_job_for_paper(db, project_id, paper_id)
    if active_job:
        raise HTTPException(status_code=409, detail="该文献仍有抽取任务在排队或运行，完成后再删除")
    await purge_paper(db, project_id, paper)
    await redis_cache.bump_project_cache(project_id)


@router.get("/extraction-reports/summary")
async def get_extraction_reports_summary(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取项目所有文献的抽取报告摘要。"""
    await get_project_or_404(db, project_id)
    import json as _json
    from pathlib import Path as _Path

    result = await db.execute(
        select(Paper).where(Paper.project_id == project_id)
    )
    papers = result.scalars().all()
    summaries = {}
    for paper in papers:
        report_path = _Path(settings.UPLOAD_DIR) / str(project_id) / f"report_{paper.id}.json"
        if report_path.exists():
            try:
                summaries[str(paper.id)] = _json.loads(report_path.read_text(encoding="utf-8"))
            except Exception:
                summaries[str(paper.id)] = {"error": "报告读取失败"}
    return summaries


@router.get("/{paper_id}/extraction-progress-stream")
async def extraction_progress_stream(
    project_id: int,
    paper_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """SSE endpoint that streams extraction progress events."""
    await get_paper_or_404(db, project_id, paper_id)

    job = await _latest_job_for_paper(db, project_id, paper_id)
    # Release the DB session before streaming to avoid holding it open
    job_id = job.id if job else None
    job_status = job.status if job else None
    job_step = job.step if job else ""
    job_percent = job.percent if job else 0
    job_message = getattr(job, "progress_message", None) if job else None
    job_error_code = job.error_code if job else None
    job_error_message = job.error_message if job else None
    job_updated_at = job.updated_at.isoformat() if job and job.updated_at else ""

    async def _done_payload() -> dict:
        async with async_session_factory() as count_db:
            candidate_count = await _candidate_count_for_job(
                count_db, paper_id=paper_id, job_id=job_id,
            )
        if not candidate_count:
            candidate_count = _candidate_count_from_report(project_id, paper_id) or 0
        return {
            "job_id": job_id,
            "status": "completed",
            "candidate_count": candidate_count,
            "message": job_message or f"抽取完成: {candidate_count} 条记录",
        }

    async def event_generator():
        if job_id is None:
            yield f"event: error\ndata: {_json_module.dumps({'error_message': '没有找到抽取任务'})}\n\n"
            return

        if job_status in ("completed",):
            done_data = await _done_payload()
            yield f"event: done\ndata: {_json_module.dumps(done_data, ensure_ascii=False)}\n\n"
            return
        if job_status in ("failed",):
            yield f"event: error\ndata: {_json_module.dumps({'error_code': job_error_code, 'error_message': job_error_message})}\n\n"
            return
        if job_status in ("cancelled",):
            yield f"event: cancelled\ndata: {_json_module.dumps({'message': '抽取已被取消', 'job_id': job_id})}\n\n"
            return

        # For queued or running jobs, subscribe and wait for progress
        extraction_job_backend.subscribe(job_id)
        try:
            # Send initial snapshot
            initial_message = (
                job_message
                or ("任务排队中..." if job_status == "queued" else (job_step or "启动中"))
            )
            yield f"event: progress\ndata: {_json_module.dumps({'step': job_step or 'queued', 'percent': job_percent or 0, 'message': initial_message, 'timestamp': job_updated_at})}\n\n"

            last_snapshot = (job_step, job_percent, job_message)
            async for msg in progress_bus.stream_events(job_id, timeout=30.0):
                if await request.is_disconnected():
                    break
                if msg is not None:
                    event_type = msg.get("event", "progress")
                    yield f"event: {event_type}\ndata: {_json_module.dumps(msg['data'], ensure_ascii=False)}\n\n"
                    if event_type in ("done", "error", "cancelled"):
                        break
                    continue

                # Heartbeat + DB fallback so reconnecting clients still get progress.
                async with async_session_factory() as hb_db:
                    live_job = await hb_db.get(ExtractionJob, job_id)
                    if live_job is None:
                        break
                    if live_job.status in ("completed", "failed", "cancelled"):
                        if live_job.status == "completed":
                            done_data = await _done_payload()
                            yield f"event: done\ndata: {_json_module.dumps(done_data, ensure_ascii=False)}\n\n"
                        elif live_job.status == "failed":
                            yield f"event: error\ndata: {_json_module.dumps({'error_code': live_job.error_code, 'error_message': live_job.error_message})}\n\n"
                        else:
                            yield f"event: cancelled\ndata: {_json_module.dumps({'message': '抽取已被取消', 'job_id': job_id})}\n\n"
                        break
                    snapshot = (
                        live_job.step,
                        live_job.percent,
                        getattr(live_job, "progress_message", None),
                    )
                    if snapshot != last_snapshot:
                        last_snapshot = snapshot
                        yield f"event: progress\ndata: {_json_module.dumps({'step': live_job.step or 'starting', 'percent': live_job.percent or 0, 'message': live_job.progress_message or live_job.step, 'timestamp': live_job.updated_at.isoformat() if live_job.updated_at else ''})}\n\n"
                yield f": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            extraction_job_backend.unsubscribe(job_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{paper_id}/extract/cancel")
async def cancel_extraction(
    project_id: int,
    paper_id: int,
    db: AsyncSession = Depends(get_db),
):
    """取消排队中或运行中的抽取任务."""
    await get_paper_or_404(db, project_id, paper_id)

    active_job = await _active_job_for_paper(db, project_id, paper_id)
    if active_job is None:
        raise HTTPException(status_code=404, detail="没有运行中或排队中的任务")

    cancel_result = await extraction_job_backend.request_cancel(active_job.id)
    return cancel_result
