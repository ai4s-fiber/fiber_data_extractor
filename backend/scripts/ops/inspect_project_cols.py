import asyncio
import sys
from pathlib import Path

# Add app to path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import async_session_factory
from sqlalchemy import text

async def main():
    async with async_session_factory() as db:
        print("\n=== Project 1 Columns ===")
        res = await db.execute(text("SELECT * FROM projects WHERE id=1"))
        row = res.fetchone()
        if row:
            # Get columns keys
            keys = res.keys()
            for key, val in zip(keys, row):
                # Mask key if it's api_key
                val_str = str(val)
                if "key" in key.lower() or "token" in key.lower() or "pass" in key.lower():
                    val_str = val_str[:5] + "..." if val_str else "None"
                print(f"  {key}: {val_str}")

if __name__ == "__main__":
    asyncio.run(main())
