import asyncio
import sys
from pathlib import Path

# Add app to path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import async_session_factory
from sqlalchemy import text

async def check():
    async with async_session_factory() as db:
        job = await db.execute(text(
            "SELECT id, status, step, percent, error_code, error_message FROM extraction_jobs ORDER BY id DESC LIMIT 1"
        ))
        j = job.fetchone()
        if j:
            print(f"Current Job ID: {j[0]}")
            print(f"  - Status: {j[1]}")
            print(f"  - Step: {j[2]}")
            print(f"  - Percent: {j[3]}%")
            print(f"  - Error Code: {j[4]}")
            print(f"  - Error Msg: {j[5]}")
        else:
            print("No jobs found!")

        cands = await db.execute(text("SELECT COUNT(*) FROM candidate_records WHERE source_paper_id=1"))
        print(f"Candidate records count for Paper 1: {cands.scalar()}")

        evs = await db.execute(text("SELECT COUNT(*) FROM evidence_items WHERE paper_id=1"))
        print(f"Evidence items count for Paper 1: {evs.scalar()}")

if __name__ == "__main__":
    asyncio.run(check())
