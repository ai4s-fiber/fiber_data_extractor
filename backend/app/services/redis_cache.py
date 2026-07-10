"""Project-scoped Redis cache with version-based invalidation."""

from __future__ import annotations

import json
from typing import Any

from app.core.config import settings
from app.core.redis_client import get_redis

PROJECT_VERSION_PREFIX = "fiber:cache:project:"


def _version_key(project_id: int) -> str:
    return f"{PROJECT_VERSION_PREFIX}{project_id}:v"


async def get_project_version(project_id: int) -> int:
    redis = await get_redis()
    if redis is None:
        return 0
    raw = await redis.get(_version_key(project_id))
    return int(raw or 0)


async def bump_project_cache(project_id: int) -> None:
    redis = await get_redis()
    if redis is None:
        return
    await redis.incr(_version_key(project_id))


def _cache_key(project_id: int, version: int, namespace: str, suffix: str) -> str:
    return f"fiber:cache:project:{project_id}:v{version}:{namespace}:{suffix}"


async def get_json(project_id: int, namespace: str, suffix: str) -> Any | None:
    redis = await get_redis()
    if redis is None:
        return None
    version = await get_project_version(project_id)
    key = _cache_key(project_id, version, namespace, suffix)
    raw = await redis.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        await redis.delete(key)
        return None


async def set_json(
    project_id: int,
    namespace: str,
    suffix: str,
    value: Any,
    *,
    ttl: int | None = None,
) -> None:
    redis = await get_redis()
    if redis is None:
        return
    version = await get_project_version(project_id)
    key = _cache_key(project_id, version, namespace, suffix)
    ttl_seconds = ttl or settings.REDIS_CACHE_TTL_SECONDS
    await redis.setex(key, ttl_seconds, json.dumps(value, ensure_ascii=False, default=str))
