"""Persistent extraction job runner with bounded local concurrency and SSE progress."""

from __future__ import annotations

import asyncio
import json
import time
import traceback
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.database import async_session_factory
from app.models.candidate_record import CandidateRecord
from app.models.extraction_job import ExtractionJob
from app.models.paper import Paper
from app.models.project import Project
from app.services.extractor_v7 import V7ExtractorService, ExtractionCancelled
from app.services.extraction_results import restore_paper_status_after_interruption
from app.services.progress_bus import progress_bus
from app.services import extraction_queue
from app.services.redis_cache import bump_project_cache


ACTIVE_JOB_STATUSES = ("queued", "running")
VALID_MODEL_MODES = ("auto", "weak", "strong")
RETRYABLE_ERROR_CODES = frozenset({
    "llm_timeout",
    "llm_rate_limited",
    "llm_non_json_response",
    "upstream_unavailable",
    "mineru_unavailable",
    "mineru_timeout",
})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_model_mode(model_mode: str | None) -> str:
    mode = (model_mode or "auto").strip().lower()
    if mode not in VALID_MODEL_MODES:
        raise ValueError("model_mode must be one of: auto, weak, strong")
    return mode


def resolve_model_mode(project: Project, requested_mode: str) -> str:
    requested_mode = normalize_model_mode(requested_mode)
    if requested_mode != "auto":
        return requested_mode

    model_name = (project.llm_model or "").lower()
    weak_keywords = [
        "deepseek-chat",
        "deepseek-v3",
        "gpt-3.5",
        "gpt-4o-mini",
        "qwen-turbo",
        "qwen-plus",
        "qwen2-plus",
        "qwen2.5-plus",
        "qwen3-plus",
        "qwen3.7-plus",
    ]
    strong_keywords = [
        "gpt-5",
        "gpt-4o",
        "claude",
        "o1",
        "o3",
        "sonnet",
        "opus",
        "haiku",
        "deepseek-r1",
        "gemini-2",
        "mimo",
    ]
    if any(keyword in model_name for keyword in weak_keywords):
        return "weak"
    if any(keyword in model_name for keyword in strong_keywords):
        return "strong"
    # During MinerU validation, quality is the default priority. Unknown models
    # can still explicitly request weak mode from the UI/API.
    return "strong"


def classify_extraction_error(error: BaseException | str) -> str:
    if hasattr(error, "error_code"):
        return error.error_code
    message = str(error).lower()
    if "api key" in message or "unauthorized" in message or "401" in message:
        return "llm_auth_failed"
    if "model" in message and ("not found" in message or "404" in message):
        return "llm_model_not_found"
    if "base url" in message or "invalid url" in message or "unsupported protocol" in message:
        return "llm_invalid_base_url"
    if "mineru" in message and ("unavailable" in message or "connect" in message):
        return "mineru_unavailable"
    if "mineru" in message and ("timeout" in message or "timed out" in message):
        return "mineru_timeout"
    if "mineru" in message:
        return "mineru_task_failed"
    if "429" in message or "rate limit" in message or "too many requests" in message:
        return "llm_rate_limited"
    if "timeout" in message or "timed out" in message or "超时" in message:
        return "llm_timeout"
    if "json" in message or "expecting value" in message:
        return "llm_non_json_response"
    if any(code in message for code in ("502", "503", "504")) or any(
        hint in message for hint in ("connection reset", "connection refused", "service unavailable")
    ):
        return "upstream_unavailable"
    if "pdf" in message or "未提取到可用文本" in message:
        return "pdf_parse_failed"
    return "unknown_error"


def is_retryable_extraction_error(error: BaseException | str) -> bool:
    return classify_extraction_error(error) in RETRYABLE_ERROR_CODES


class ExtractionJobBackend:
    """Replaceable runner facade for local in-process extraction jobs."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        max_concurrent_jobs: int,
    ) -> None:
        self.session_factory = session_factory
        self.max_concurrent_jobs = max(1, int(max_concurrent_jobs or 1))
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._last_db_write: dict[int, tuple[float, str, int]] = {}
        self._shutting_down = False

    async def _persist_progress(
        self,
        job_id: int,
        step: str,
        percent: int,
        message: str,
    ) -> None:
        async with self.session_factory() as db:
            job = await db.get(ExtractionJob, job_id)
            if job is None or job.status not in ACTIVE_JOB_STATUSES:
                return
            job.step = step
            job.percent = percent
            job.progress_message = (message or step)[:500]
            job.updated_at = utcnow()
            await db.commit()

    # ── Progress queue / SSE support ──────────────────────────────

    async def _push_event(self, job_id: int, event: str, data: dict[str, Any]) -> None:
        await progress_bus.push(job_id, event, data)

    def subscribe(self, job_id: int) -> asyncio.Queue[dict[str, Any]]:
        return progress_bus.subscribe_local(job_id)

    def unsubscribe(self, job_id: int) -> None:
        progress_bus.unsubscribe_local(job_id)

    # ── Runner control ────────────────────────────────────────────

    async def enqueue(self, job_id: int) -> None:
        progress_bus.subscribe_local(job_id)
        await extraction_queue.push_job(job_id)
        await self.try_start_next()

    async def _claim_job(self, db: AsyncSession, job_id: int) -> ExtractionJob | None:
        result = await db.execute(
            select(ExtractionJob).where(ExtractionJob.id == job_id).with_for_update()
        )
        job = result.scalar_one_or_none()
        if job is None or job.status != "queued":
            return None
        job.status = "running"
        job.step = "starting"
        job.percent = max(0, job.percent or 0)
        job.started_at = utcnow()
        job.updated_at = utcnow()
        paper = await db.get(Paper, job.paper_id)
        if paper:
            paper.status = "extracting"
            paper.updated_at = utcnow()
        await extraction_queue.mark_running(job.id)
        return job

    async def try_start_next(self) -> None:
        if self._shutting_down:
            return
        async with self._lock:
            if self._shutting_down:
                return
            slots = await extraction_queue.available_slots(self.max_concurrent_jobs)
            if slots <= 0:
                local_running = await self._local_running_count()
                slots = self.max_concurrent_jobs - local_running
            if slots <= 0:
                return

            started_ids: list[int] = []
            project_ids: set[int] = set()
            async with self.session_factory() as db:
                for _ in range(slots):
                    job_id = await extraction_queue.pop_job(timeout=0)
                    if job_id is None:
                        break
                    job = await self._claim_job(db, job_id)
                    if job is not None:
                        started_ids.append(job.id)
                        project_ids.add(job.project_id)

                if len(started_ids) < slots:
                    remaining = slots - len(started_ids)
                    running_count_result = await db.execute(
                        select(func.count(ExtractionJob.id)).where(
                            ExtractionJob.status == "running"
                        )
                    )
                    local_slots = self.max_concurrent_jobs - (running_count_result.scalar() or 0)
                    if local_slots > 0:
                        queued_result = await db.execute(
                            select(ExtractionJob)
                            .where(ExtractionJob.status == "queued")
                            .order_by(ExtractionJob.created_at.asc(), ExtractionJob.id.asc())
                            .limit(min(remaining, local_slots))
                        )
                        for job in queued_result.scalars().all():
                            if job.id in started_ids:
                                continue
                            claimed = await self._claim_job(db, job.id)
                            if claimed is not None:
                                started_ids.append(claimed.id)
                                project_ids.add(claimed.project_id)

                if started_ids:
                    await db.commit()
                    for pid in project_ids:
                        await bump_project_cache(pid)

            for job_id in started_ids:
                task = asyncio.create_task(self._run_job(job_id))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        """Stop accepting work and wait for local workers to release resources."""
        self._shutting_down = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._last_db_write.clear()

    async def _local_running_count(self) -> int:
        async with self.session_factory() as db:
            result = await db.execute(
                select(func.count(ExtractionJob.id)).where(ExtractionJob.status == "running")
            )
            return int(result.scalar() or 0)

    async def mark_progress(self, job_id: int, step: str, percent: int, message: str = "") -> None:
        """Push progress to SSE and persist throttled snapshots for refresh/reconnect."""
        from app.services.job_cancellation import is_job_cancel_requested

        # Completion is the commit point: a cancellation arriving afterwards must
        # not relabel successfully persisted output as cancelled.
        if step != "completed" and await is_job_cancel_requested(job_id):
            raise ExtractionCancelled("用户取消了抽取任务")

        pct = max(0, min(100, int(percent)))
        msg = message or step
        await self._push_event(job_id, "progress", {
            "step": step,
            "percent": pct,
            "message": msg,
            "timestamp": utcnow().isoformat(),
        })

        now = time.monotonic()
        last = self._last_db_write.get(job_id)
        should_persist = (
            last is None
            or now - last[0] >= 2.0
            or last[1] != step
            or abs(last[2] - pct) >= 2
            or pct >= 100
        )
        if should_persist:
            self._last_db_write[job_id] = (now, step, pct)
            await self._persist_progress(job_id, step, pct, msg)

    async def mark_completed(self, job_id: int) -> None:
        project_id = None
        candidate_count = 0
        async with self.session_factory() as db:
            job = await db.get(ExtractionJob, job_id)
            if job is None:
                return
            project_id = job.project_id
            count_result = await db.execute(
                select(func.count(CandidateRecord.id)).where(
                    CandidateRecord.source_paper_id == job.paper_id,
                    CandidateRecord.job_id == job_id,
                )
            )
            candidate_count = int(count_result.scalar() or 0)
            if not candidate_count:
                paper_count = await db.execute(
                    select(func.count(CandidateRecord.id)).where(
                        CandidateRecord.source_paper_id == job.paper_id,
                    )
                )
                candidate_count = int(paper_count.scalar() or 0)
            job.status = "completed"
            job.step = "completed"
            job.percent = 100
            job.progress_message = f"抽取完成: {candidate_count} 条记录"
            job.finished_at = utcnow()
            job.updated_at = utcnow()
            paper = await db.get(Paper, job.paper_id)
            if paper and paper.status == "extracting":
                paper.status = "review"
                paper.updated_at = utcnow()
            await db.commit()

        await self._push_event(job_id, "done", {
            "job_id": job_id,
            "status": "completed",
            "candidate_count": candidate_count,
            "message": f"抽取完成: {candidate_count} 条记录",
        })
        self.unsubscribe(job_id)
        self._last_db_write.pop(job_id, None)
        await extraction_queue.mark_finished(job_id)
        if project_id:
            await bump_project_cache(project_id)

    async def mark_failed(
        self,
        job_id: int,
        error: BaseException | str,
        *,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        project_id = None
        async with self.session_factory() as db:
            job = await db.get(ExtractionJob, job_id)
            if job is None:
                return
            project_id = job.project_id
            job.status = "failed"
            job.step = "failed"
            job.error_code = error_code or classify_extraction_error(error)
            job.error_message = str(error)[:2000]
            job.error_detail = error_detail
            job.finished_at = utcnow()
            job.updated_at = utcnow()
            paper = await db.get(Paper, job.paper_id)
            if paper:
                await restore_paper_status_after_interruption(
                    db, paper, empty_status="failed"
                )
                paper.updated_at = utcnow()
            await db.commit()

        await self._push_event(job_id, "error", {
            "error_code": error_code or classify_extraction_error(error),
            "error_message": str(error)[:2000],
        })
        self.unsubscribe(job_id)
        self._last_db_write.pop(job_id, None)
        await extraction_queue.mark_finished(job_id)
        if project_id:
            await bump_project_cache(project_id)

    async def request_cancel(self, job_id: int) -> dict[str, Any]:
        """Request cancellation of a queued or running job."""
        async with self.session_factory() as db:
            job = await db.get(ExtractionJob, job_id)
            if job is None:
                return {"success": False, "message": "任务不存在"}

            if job.status == "queued":
                job.status = "cancelled"
                job.step = "cancelled"
                job.finished_at = utcnow()
                job.updated_at = utcnow()
                paper = await db.get(Paper, job.paper_id)
                if paper:
                    await restore_paper_status_after_interruption(
                        db, paper, empty_status="uploaded"
                    )
                    paper.updated_at = utcnow()
                await db.commit()
                await self._push_event(job_id, "cancelled", {
                    "message": "抽取已被取消",
                    "job_id": job_id,
                })
                self.unsubscribe(job_id)
                self._last_db_write.pop(job_id, None)
                return {"success": True, "message": "已取消排队中的任务"}

            if job.status == "running":
                job.cancel_requested_at = utcnow()
                job.updated_at = utcnow()
                await db.commit()
                return {"success": True, "message": "已发出取消信号，任务将在当前阶段完成后停止"}

            return {"success": False, "message": f"任务状态为 {job.status}，无法取消"}

    async def recover_interrupted_jobs(self) -> None:
        """Re-queue jobs that were running when the worker restarted."""
        await extraction_queue.reset_running_set()
        queued_ids: list[int] = []
        async with self.session_factory() as db:
            result = await db.execute(
                select(ExtractionJob).where(ExtractionJob.status == "running")
            )
            jobs = result.scalars().all()
            for job in jobs:
                job.status = "queued"
                job.step = "queued"
                job.error_code = None
                job.error_message = None
                job.cancel_requested_at = None
                job.finished_at = None
                job.updated_at = utcnow()
                paper = await db.get(Paper, job.paper_id)
                if paper:
                    paper.status = "extracting"
                    paper.updated_at = utcnow()
                queued_ids.append(job.id)

            queued_result = await db.execute(
                select(ExtractionJob.id).where(ExtractionJob.status == "queued")
            )
            for row in queued_result.fetchall():
                if row[0] not in queued_ids:
                    queued_ids.append(row[0])
            await db.commit()

        await extraction_queue.requeue_queued_jobs(queued_ids)

    async def _run_job(self, job_id: int) -> None:
        try:
            max_attempts = max(1, int(settings.EXTRACTION_MAX_ATTEMPTS or 1))
            for attempt in range(1, max_attempts + 1):
                failure: BaseException | str | None = None
                error_detail = ""
                try:
                    async with self.session_factory() as db:
                        job = await db.get(ExtractionJob, job_id)
                        if job is None:
                            return
                        paper = await db.get(Paper, job.paper_id)
                        project = await db.get(Project, job.project_id)
                        if paper is None or project is None:
                            raise RuntimeError("文献或项目不存在，无法执行抽取")

                        resolved_mode = resolve_model_mode(project, job.requested_mode)
                        job.resolved_mode = resolved_mode
                        job.model_provider = project.llm_provider
                        job.model_name = project.llm_model
                        job.updated_at = utcnow()
                        paper.status = "extracting"
                        paper.updated_at = utcnow()
                        await db.commit()

                        attempt_text = f", 第 {attempt}/{max_attempts} 次" if max_attempts > 1 else ""
                        await self.mark_progress(
                            job_id,
                            "starting",
                            1,
                            (
                                f"准备开始抽取 (模式: {resolved_mode}, "
                                f"模型: {project.llm_model or '未配置'}{attempt_text})"
                            ),
                        )

                        async def progress_callback(
                            step: str, percent: int, message: str = "",
                        ) -> None:
                            await self.mark_progress(job_id, step, percent, message)

                        result = await asyncio.wait_for(
                            V7ExtractorService.run_full_pipeline_for_paper(
                                db,
                                paper.id,
                                progress_callback=progress_callback,
                                model_mode=resolved_mode,
                                job_id=job_id,
                            ),
                            timeout=max(
                                60,
                                int(settings.EXTRACTION_PIPELINE_TIMEOUT_SECONDS or 1800),
                            ),
                        )
                        if result.get("error"):
                            failure = str(result["error"])
                            error_detail = json.dumps(result, ensure_ascii=False)
                        else:
                            await self.mark_completed(job_id)
                            return
                except asyncio.TimeoutError:
                    timeout_seconds = max(
                        60,
                        int(settings.EXTRACTION_PIPELINE_TIMEOUT_SECONDS or 1800),
                    )
                    failure = RuntimeError(f"抽取超时（超过 {timeout_seconds} 秒）")
                    error_detail = f"Pipeline watchdog timeout after {timeout_seconds}s"
                except ExtractionCancelled:
                    raise
                except Exception as exc:
                    failure = exc
                    error_detail = traceback.format_exc()

                if failure is None:
                    return
                error_code = classify_extraction_error(failure)
                if attempt < max_attempts and is_retryable_extraction_error(failure):
                    delay = min(
                        60.0,
                        max(0.0, float(settings.EXTRACTION_RETRY_BASE_SECONDS or 0))
                        * (2 ** (attempt - 1)),
                    )
                    await self.mark_progress(
                        job_id,
                        "retrying",
                        1,
                        f"上游暂时失败 ({error_code})，{delay:g} 秒后重试 {attempt + 1}/{max_attempts}",
                    )
                    if delay:
                        await asyncio.sleep(delay)
                    continue

                await self.mark_failed(
                    job_id,
                    failure,
                    error_code=error_code,
                    error_detail=error_detail,
                )
                return
        except ExtractionCancelled:
            async with self.session_factory() as db:
                job = await db.get(ExtractionJob, job_id)
                if job:
                    job.status = "cancelled"
                    job.step = "cancelled"
                    job.error_code = "cancelled_by_user"
                    job.error_message = "用户取消了抽取任务"
                    job.finished_at = utcnow()
                    job.updated_at = utcnow()
                    paper = await db.get(Paper, job.paper_id)
                    if paper:
                        await restore_paper_status_after_interruption(
                            db, paper, empty_status="uploaded"
                        )
                        paper.updated_at = utcnow()
                    await db.commit()
            await self._push_event(job_id, "cancelled", {
                "message": "抽取已被用户取消",
                "job_id": job_id,
            })
            self.unsubscribe(job_id)
            self._last_db_write.pop(job_id, None)
            await extraction_queue.mark_finished(job_id)
        except Exception as exc:
            await self.mark_failed(
                job_id,
                exc,
                error_code=classify_extraction_error(exc),
                error_detail=traceback.format_exc(),
            )
        finally:
            await extraction_queue.mark_finished(job_id)
            await self.try_start_next()


extraction_job_backend = ExtractionJobBackend(
    async_session_factory,
    settings.EXTRACTION_MAX_CONCURRENT_JOBS,
)
