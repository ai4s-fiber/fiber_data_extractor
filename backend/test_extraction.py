"""Quick extraction test script."""
import asyncio
import sys
import app.models  # noqa: F401
from app.core.database import async_session_factory
from sqlalchemy import text
from app.services.extractor_v7 import V7ExtractorService


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"
    print(f"Using model_mode={mode}")

    async with async_session_factory() as db:
        await db.execute(text("DELETE FROM candidate_records WHERE source_paper_id=1"))
        await db.execute(text("DELETE FROM evidence_items WHERE paper_id=1"))
        await db.execute(text("DELETE FROM page_inventory WHERE paper_id=1"))
        await db.execute(text("DELETE FROM sample_catalogs WHERE paper_id=1"))
        await db.execute(text("DELETE FROM fact_candidates WHERE paper_id=1"))
        await db.execute(text("UPDATE papers SET status='uploaded' WHERE id=1"))
        await db.commit()
        print("Cleanup done, starting extraction...")

        def progress(step, pct):
            print(f"  Progress: {step} ({pct}%)")

        try:
            result = await V7ExtractorService.run_full_pipeline_for_paper(
                db, 1, progress_callback=progress, model_mode=mode,
            )
            print("Result:", result)

            # Show candidates
            rows = await db.execute(text(
                "SELECT id, sample_id, performance_metric, performance_value, performance_unit, review_status FROM candidate_records"
            ))
            print("\n=== Candidates ===")
            for r in rows:
                print(f"  id={r[0]} sample={r[1]} metric={r[2]} value={r[3]} {r[4]} status={r[5]}")

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"FAILED: {e}")


if __name__ == "__main__":
    asyncio.run(main())
