"""SQLite runtime configuration tests."""

import sqlite3

from app.core.database import _ensure_sqlite_wal


def test_ensure_sqlite_wal_enables_persistent_wal_mode(tmp_path):
    database_path = tmp_path / "runtime.db"
    url = f"sqlite+aiosqlite:///{database_path.as_posix()}"

    assert _ensure_sqlite_wal(url) == "wal"

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_ensure_sqlite_wal_skips_in_memory_database():
    assert _ensure_sqlite_wal("sqlite+aiosqlite:///:memory:") == "memory"
