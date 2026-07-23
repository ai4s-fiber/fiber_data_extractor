"""Database initialization script for the open workspace."""

import asyncio

import app.models  # noqa: F401
from app.core.database import close_database
from app.core.schema_repair import ensure_runtime_schema


async def init_db():
    try:
        await ensure_runtime_schema()
        print("Database schema ready for open workspace.")
    finally:
        await close_database()


if __name__ == "__main__":
    asyncio.run(init_db())
