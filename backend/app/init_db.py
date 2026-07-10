"""Database initialization script for the open workspace."""

import asyncio

import app.models  # noqa: F401


async def init_db():
    from app.core.schema_repair import ensure_runtime_schema
    await ensure_runtime_schema()
    print("Database schema ready for open workspace.")


if __name__ == "__main__":
    asyncio.run(init_db())
