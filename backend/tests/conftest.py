"""Shared test fixtures."""

import asyncio
import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")
os.environ.setdefault("REDIS_ENABLED", "false")


def pytest_sessionfinish(session, exitstatus):
    """Dispose the shared async engine so pytest exits without SQLite threads."""
    database_module = sys.modules.get("app.core.database")
    if database_module is not None:
        asyncio.run(database_module.close_database())
