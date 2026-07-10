import asyncio
import sys
from pathlib import Path

# Add app to path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import async_session_factory
from app.core.schema_repair import ensure_runtime_schema
from sqlalchemy import text

async def main():
    await ensure_runtime_schema()
    async with async_session_factory() as db:

        print("\n=== Projects in DB ===")
        projects = await db.execute(text("SELECT id, name, llm_provider, llm_model, llm_base_url FROM projects"))
        for p in projects:
            print(f"Project ID: {p[0]}, Name: {p[1]}, LLM Provider: {p[2]}, Model: {p[3]}, Base URL: {p[4]}")

        print("\n=== Papers in DB ===")
        papers = await db.execute(text("SELECT id, project_id, original_filename, file_object_key, status FROM papers"))
        for paper in papers:
            print(f"Paper ID: {paper[0]}, Project ID: {paper[1]}, Filename: {paper[2]}, Key: {paper[3]}, Status: {paper[4]}")

        print("\n=== Extraction Jobs in DB ===")
        jobs = await db.execute(text("SELECT id, paper_id, status, parser_strategy, requested_mode FROM extraction_jobs ORDER BY id DESC LIMIT 5"))
        for job in jobs:
            print(f"Job ID: {job[0]}, Paper ID: {job[1]}, Status: {job[2]}, Strategy: {job[3]}, Requested Mode: {job[4]}")


if __name__ == "__main__":
    asyncio.run(main())
