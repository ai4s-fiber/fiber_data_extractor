"""Process-local concurrency guard for outbound LLM calls."""

from __future__ import annotations

import asyncio
import weakref
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from app.core.config import settings


@dataclass(slots=True)
class _LoopLimiter:
    limit: int
    semaphore: asyncio.Semaphore


_limiters: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopLimiter] = (
    weakref.WeakKeyDictionary()
)


def configured_llm_limit() -> int:
    return max(1, int(settings.LLM_GLOBAL_MAX_CONCURRENT_CALLS or 1))


def configured_batch_llm_limit() -> int:
    """Return the batch budget after preserving configured interactive capacity."""
    global_limit = configured_llm_limit()
    concurrent_jobs = max(1, int(settings.EXTRACTION_MAX_CONCURRENT_JOBS or 1))
    configured_batch = max(
        1, int(settings.LLM_BATCH_MAX_CONCURRENT_CALLS or global_limit)
    )
    requested_reserve = max(0, int(settings.LLM_INTERACTIVE_RESERVED_CALLS or 0))
    max_reserve = max(0, global_limit - min(concurrent_jobs, global_limit))
    available_to_batch = global_limit - min(requested_reserve, max_reserve)
    return min(configured_batch, max(1, available_to_batch))


def per_job_llm_parallel_limit(requested: int) -> int:
    requested = max(1, int(requested or 1))
    batch_budget = configured_batch_llm_limit()
    concurrent_jobs = max(1, int(settings.EXTRACTION_MAX_CONCURRENT_JOBS or 1))
    per_job_budget = max(1, batch_budget // concurrent_jobs)
    return min(requested, per_job_budget)


def _limiter_for_current_loop() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    limit = configured_llm_limit()
    limiter = _limiters.get(loop)
    if limiter is None or limiter.limit != limit:
        limiter = _LoopLimiter(limit=limit, semaphore=asyncio.Semaphore(limit))
        _limiters[loop] = limiter
    return limiter.semaphore


@asynccontextmanager
async def llm_call_slot() -> AsyncIterator[None]:
    semaphore = _limiter_for_current_loop()
    async with semaphore:
        yield
