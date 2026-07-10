import asyncio
import sys
import time
from pathlib import Path

# Add app to path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import async_session_factory
from app.core.schema_repair import ensure_runtime_schema
from app.models.extraction_job import ExtractionJob
from app.services.extractor_v7 import V7ExtractorService
from sqlalchemy import text, select

async def run_e2e_extraction():
    print("==================================================")
    print("  Starting V7 End-to-End Extraction Pipeline Test ")
    print("==================================================")


    # 1. Ensure runtime database schema is complete
    await ensure_runtime_schema()

    async with async_session_factory() as db:
        # 2. Setup a mock extraction job in DB
        # This allows us to pass a specific parser strategy and mode.
        job = ExtractionJob(
            project_id=1,
            paper_id=1,
            status="running",
            parser_strategy="mineru_cloud",
            requested_mode="weak",
            step="starting",
            percent=0
        )

        db.add(job)
        await db.flush()
        job_id = job.id
        await db.commit()

        print(f"[OK] Created Extraction Job in database: ID={job_id}")
        print("  - Parser Strategy: mineru_cloud (MinerU Cloud Layout VLM)")
        print("  - Extraction Mode: weak (Single-pass)")
        print("Starting pipeline execution...\n")


        start_time = time.monotonic()

        # 3. Progress callback to monitor execution live
        def on_progress(step: str, percent: int, message: str = ""):
            elapsed = time.monotonic() - start_time
            print(f"[{elapsed:5.1f}s] [{percent:3d}%] [{step:12s}] {message}")

        try:
            # 4. Trigger full V7 extraction pipeline!
            result = await V7ExtractorService.run_full_pipeline_for_paper(
                db=db,
                paper_id=1,
                progress_callback=on_progress,
                model_mode="weak",
                job_id=job_id
            )

            elapsed_total = time.monotonic() - start_time
            print(f"\n[OK] Pipeline completed in {elapsed_total:.2f}s!")

            # Print result summary safely
            result_str = str(result)
            safe_result = result_str.encode(sys.stdout.encoding or 'gbk', errors='replace').decode(sys.stdout.encoding or 'gbk')
            print(f"Result summary: {safe_result[:500]}...")

            # Update job status in DB
            job.status = "completed"
            job.percent = 100
            job.step = "completed"
            await db.commit()

            # 5. Query and display the generated structure records
            print("\n==========================================")
            print("  Displaying Generated Candidate Records  ")
            print("==========================================")

            # Query candidate records
            rows = await db.execute(text(
                "SELECT id, sample_id, performance_metric, performance_value, performance_unit, review_status, performance_evidence "
                "FROM candidate_records WHERE source_paper_id = 1"
            ))
            candidates = rows.fetchall()
            print(f"Total Candidate Records Saved: {len(candidates)}")
            for idx, r in enumerate(candidates[:20]):
                # Build a safe print string
                line = f"  [{idx+1:02d}] ID: {r[0]} | Sample: {r[1]:8s} | Metric: {r[2]:25s} | Value: {r[3]:10s} {r[4]:5s} | Status: {r[5]}"
                safe_line = line.encode(sys.stdout.encoding or 'gbk', errors='replace').decode(sys.stdout.encoding or 'gbk')
                print(safe_line)

            # Query sample catalogs
            samples = await db.execute(text(
                "SELECT sample_id, alias_list_json, composition_summary FROM sample_catalogs WHERE paper_id = 1"
            ))
            print("\n=== Extracted Samples ===")
            for s in samples.fetchall():
                line = f"  - Sample ID: {s[0]} | Aliases: {s[1]} | Compositions: {s[2]}"
                safe_line = line.encode(sys.stdout.encoding or 'gbk', errors='replace').decode(sys.stdout.encoding or 'gbk')
                print(safe_line)


        except Exception as e:
            job.status = "failed"
            job.error_code = "pipeline_failed"
            job.error_detail = str(e)
            await db.commit()
            print(f"\n[ERROR] Pipeline failed: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run_e2e_extraction())
