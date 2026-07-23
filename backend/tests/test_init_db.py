"""Database initialization lifecycle tests."""

import pytest

import app.init_db as init_db_module


@pytest.mark.asyncio
async def test_init_db_closes_database_after_schema_ready(monkeypatch):
    events = []

    async def ensure_schema():
        events.append("schema")

    async def close_database():
        events.append("close")

    monkeypatch.setattr(init_db_module, "ensure_runtime_schema", ensure_schema)
    monkeypatch.setattr(init_db_module, "close_database", close_database)

    await init_db_module.init_db()

    assert events == ["schema", "close"]


@pytest.mark.asyncio
async def test_init_db_closes_database_when_schema_repair_fails(monkeypatch):
    events = []

    async def ensure_schema():
        events.append("schema")
        raise RuntimeError("schema failed")

    async def close_database():
        events.append("close")

    monkeypatch.setattr(init_db_module, "ensure_runtime_schema", ensure_schema)
    monkeypatch.setattr(init_db_module, "close_database", close_database)

    with pytest.raises(RuntimeError, match="schema failed"):
        await init_db_module.init_db()

    assert events == ["schema", "close"]
