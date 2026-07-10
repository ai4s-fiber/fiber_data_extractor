"""Shared test fixtures."""

import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")
os.environ.setdefault("REDIS_ENABLED", "false")
