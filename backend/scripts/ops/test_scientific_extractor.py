import asyncio
import os
import sys
from sqlalchemy import select
from app.core.database import async_session_factory
from app.models.paper import Paper
from app.models.candidate_record import CandidateRecord
from app.models.evidence_item import EvidenceItem
from app.services.legacy.v6_extractor import V6ExtractorService

async def main():
    print("======================================================================")
    print("[SCIENCE] AI4S Fiber Materials Literature V6 Extractor - Academic Test Runner")
    print("======================================================================")
    
    # 1. Ensure we have fallback SQLite database
    db_path = "local_dev_fallback.db"
    if not os.path.exists(db_path):
        print("[ERROR] Error: local_dev_fallback.db not found. Run python -m app.init_db first.")
        sys.exit(1)
        
    async with async_session_factory() as db:
        # 2. Get the Seeded Paper
        res = await db.execute(select(Paper).where(Paper.id == 1))
        paper = res.scalar_one_or_none()
        if not paper:
            print("[ERROR] Seed paper id=1 not found. Re-run python -m app.init_db.")
            return

        print(f"\n[FILE] Step 1: Found seed paper [{paper.original_filename}] in Project {paper.project_id}")
        
        # 3. Simulate creating a temporary PDF mock text to extract from
        # In reality V6ExtractorService reads pdf_path, we will mock the PDF reading internally
        # by checking if we have the file or writing a mock text file.
        # But wait! Let's check what paper.pdf_path points to:
        print(f"[INFO] Paper File Key: {paper.file_object_key}")
        
        # Let's run the extraction pipeline asynchronously!
        print("\n[PROCESS] Step 2: Triggering V6 Academic Extraction Engine...")
        print("   -> Running: Inventory Classification")
        print("   -> Running: Local Rule-based Regex sliding-window extraction")
        print("   -> Running: Component grouping & nearest neighbor association")
        print("   -> Running: 40-column flat row synthesis")
        print("   -> Running: V6 multi-rule Systematic Quality Control validation")
        
        result = await V6ExtractorService.run_full_pipeline_for_paper(db, paper_id=1)
        
        print("\n[SUCCESS] Step 3: Extraction Pipeline Completed Successfully!")
        print(f"[SUMMARY] Result Summary: {result}")
        
        # 4. Fetch the resulting Evidence Items
        res_evidence = await db.execute(
            select(EvidenceItem).where(EvidenceItem.paper_id == 1)
        )
        evidence_items = res_evidence.scalars().all()
        
        print(f"\n[EVIDENCE] Saved [{len(evidence_items)}] academic traceable Evidence Cards (Evidence Items):")
        print("-" * 120)
        print(f"{'TYPE':<12} | {'LOCATION':<20} | {'CONFIDENCE':<10} | {'EVIDENCE TEXT / PHRASE EXCERPT'}")
        print("-" * 120)
        for item in evidence_items[:10]:
            excerpt = item.evidence_text.replace("\n", " ")[:70] + "..." if item.evidence_text else "None"
            print(f"{item.source_type:<12} | {item.source_location or 'unknown':<20} | {item.confidence:<10.2f} | {excerpt}")
        print("-" * 120)
        
        # 5. Fetch the resulting Candidate Records
        res_candidates = await db.execute(
            select(CandidateRecord).where(CandidateRecord.source_paper_id == 1)
        )
        candidates = res_candidates.scalars().all()
        
        print(f"\n[CANDIDATE] Synthesized [{len(candidates)}] 40-Column Candidate Records (Audit Queue):")
        print("=" * 120)
        print(f"{'RECORD ID':<15} | {'SAMPLE ID':<15} | {'METRIC':<20} | {'VALUE':<10} | {'UNIT':<8} | {'QC STATUS':<10} | {'SYSTEM QC DIAGNOSTIC SUGGESTION'}")
        print("=" * 120)
        for rec in candidates:
            comment = rec.reviewer_comment or "No remarks"
            print(f"{rec.record_id:<15} | {rec.sample_id:<15} | {rec.performance_metric:<20} | {rec.performance_value:<10} | {rec.performance_unit:<8} | {rec.review_status:<10} | {comment}")
        print("=" * 120)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
