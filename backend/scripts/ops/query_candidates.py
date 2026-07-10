import asyncio
import sys
from pathlib import Path

# Add app to path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import async_session_factory
from sqlalchemy import text

async def query_cands():
    async with async_session_factory() as db:
        rows = await db.execute(text(
            "SELECT id, sample_id, variable_name, variable_value, variable_unit, composition_evidence, performance_metric, performance_value, performance_unit, review_status "
            "FROM candidate_records WHERE source_paper_id = 1"
        ))
        candidates = rows.fetchall()
        print(f"Total Candidates Found in DB: {len(candidates)}")
        print("\n| Index | ID | Sample ID | Variable Name | Variable Value | Metric | Value | Unit | Status |")
        print("|---|---|---|---|---|---|---|---|---|")
        for idx, r in enumerate(candidates):
            # Format nicely
            line = f"| {idx+1} | {r[0]} | {r[1]} | {r[2]} | {r[3]} {r[4]} | {r[6]} | {r[7]} | {r[8]} | {r[9]} |"
            # Safe print
            safe_line = line.encode('utf-8', errors='replace').decode('utf-8')
            print(safe_line)

        # Also query sample catalog
        print("\n=== Sample Catalog ===")
        samples = await db.execute(text(
            "SELECT sample_id, sample_aliases, material_system, composition_expression FROM sample_catalogs WHERE paper_id = 1"
        ))
        for s in samples.fetchall():
            line = f"  - Sample ID: {s[0]} | Aliases: {s[1]} | Material: {s[2]} | Composition: {s[3]}"
            safe_line = line.encode('utf-8', errors='replace').decode('utf-8')
            print(safe_line)


if __name__ == "__main__":
    asyncio.run(query_cands())
