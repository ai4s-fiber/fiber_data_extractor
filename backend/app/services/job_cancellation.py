"""Fresh DB reads for extraction job cancellation (avoids stale ORM cache)."""

from __future__ import annotations

import asyncio
from typing import Any, Coroutine, TypeVar

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.extraction_job import ExtractionJob

T = TypeVar("T")


async def is_job_cancel_requested(job_id: int | None) -> bool:
    """Return True when the user has requested cancellation for this job."""
    if job_id is None:
        return False
    async with async_session_factory() as db:
        cancel_at = await db.scalar(
            select(ExtractionJob.cancel_requested_at).where(ExtractionJob.id == job_id)
        )
        return cancel_at is not None


async def run_with_cancel_poll(
    coro: Coroutine[Any, Any, T],
    job_id: int | None,
    *,
    poll_seconds: float = 2.0,
) -> T:
    """Run *coro* but raise ExtractionCancelled when cancel is requested."""
    from app.services.extractor_v7.exceptions import ExtractionCancelled

    if job_id is None:
        return await coro

    task = asyncio.create_task(coro)
    try:
        while True:
            if await is_job_cancel_requested(job_id):
                task.cancel()
                raise ExtractionCancelled("用户取消了抽取任务")
            done, _ = await asyncio.wait({task}, timeout=poll_seconds)
            if task in done:
                return task.result()
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
