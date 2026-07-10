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
    mineru_cloud_required = (
        settings.MINERU_ENABLED
        and settings.DEFAULT_PARSER_STRATEGY == "mineru_cloud"
    )
    if not mineru_cloud_required:
        return True
    token = (settings.MINERU_CLOUD_TOKEN or "").strip()
    return bool(token)
