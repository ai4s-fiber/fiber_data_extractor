"""Redis-backed distributed extraction job queue."""

from __future__ import annotations

from app.core.redis_client import get_redis

QUEUE_KEY = "fiber:queue:extraction"
RUNNING_KEY = "fiber:extraction:running"


async def push_job(job_id: int) -> bool:
    redis = await get_redis()
    if redis is None:
        return False
    await redis.rpush(QUEUE_KEY, str(job_id))
    return True


async def pop_job(timeout: float = 0.0) -> int | None:
    redis = await get_redis()
    if redis is None:
        return None
    if timeout > 0:
        result = await redis.brpop(QUEUE_KEY, timeout=timeout)
        if not result:
            return None
        return int(result[1])
    raw = await redis.lpop(QUEUE_KEY)
    return int(raw) if raw else None


async def running_count() -> int:
    redis = await get_redis()
    if redis is None:
        return 0
    return int(await redis.scard(RUNNING_KEY) or 0)


async def available_slots(max_concurrent: int) -> int:
    running = await running_count()
    return max(0, max_concurrent - running)


async def mark_running(job_id: int) -> None:
    redis = await get_redis()
    if redis is None:
        return
    await redis.sadd(RUNNING_KEY, str(job_id))


async def mark_finished(job_id: int) -> None:
    redis = await get_redis()
    if redis is None:
        return
    await redis.srem(RUNNING_KEY, str(job_id))


async def requeue_queued_jobs(job_ids: list[int]) -> None:
    redis = await get_redis()
    if redis is None or not job_ids:
        return
    await redis.rpush(QUEUE_KEY, *[str(job_id) for job_id in job_ids])


async def clear_running_marker(job_id: int) -> None:
    await mark_finished(job_id)


async def reset_running_set() -> None:
    redis = await get_redis()
    if redis is None:
        return
    await redis.delete(RUNNING_KEY)
