"""Lightweight dependency health probes for /api/health."""

from __future__ import annotations

from sqlalchemy import text

from app.core.config import settings
from app.core.database import async_session_factory


async def check_database() -> bool:
    try:
        async with async_session_factory() as db:
            await db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def check_mineru_cloud_configured() -> bool:
    if not settings.MINERU_ENABLED:
        return True
    token = (settings.MINERU_CLOUD_TOKEN or "").strip()
    return bool(token)
