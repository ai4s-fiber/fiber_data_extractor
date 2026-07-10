"""Shared async Redis client for cache, queues, and pub/sub."""

from __future__ import annotations

from typing import Any

from app.core.config import settings

_redis: Any = None
_redis_checked = False
_redis_ok = False


async def get_redis() -> Any | None:
    global _redis, _redis_checked, _redis_ok
    if not settings.REDIS_ENABLED:
        return None
    if _redis_checked:
        return _redis if _redis_ok else None
    _redis_checked = True
    try:
        from redis.asyncio import Redis

        client = Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=5,
        )
        await client.ping()
        _redis = client
        _redis_ok = True
        print(f"Redis connected: {settings.REDIS_URL}")
    except Exception as exc:
        print(f"Redis unavailable: {exc}")
        _redis = None
        _redis_ok = False
    return _redis if _redis_ok else None


async def ping_redis() -> bool:
    redis = await get_redis()
    if redis is None:
        return False
    try:
        return bool(await redis.ping())
    except Exception:
        return False


async def close_redis() -> None:
    global _redis, _redis_checked, _redis_ok
    if _redis is not None and _redis_ok:
        await _redis.aclose()
    _redis = None
    _redis_ok = False
    _redis_checked = False
