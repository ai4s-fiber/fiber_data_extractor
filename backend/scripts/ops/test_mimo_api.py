import asyncio
import sys
from pathlib import Path
import httpx
import time

# Add app to path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import async_session_factory
from sqlalchemy import text

async def main():
    async with async_session_factory() as db:
        res = await db.execute(text("SELECT llm_api_key, llm_base_url, llm_model FROM projects WHERE id=1"))
        row = res.fetchone()
        if not row:
            print("Project 1 not found!")
            return
        api_key, base_url, model = row
        print(f"Testing LLM API:")
        print(f"  - Base URL: {base_url}")
        print(f"  - Model: {model}")
        print(f"  - API Key: {api_key[:8]}...")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Say hello in 5 words."}],
        "temperature": 0.0,
        "max_tokens": 10
    }

    url = f"{base_url.rstrip('/')}/chat/completions"
    start_time = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            response = await client.post(url, headers=headers, json=payload)
            elapsed = time.monotonic() - start_time
            print(f"Request status: {response.status_code}")
            print(f"Time taken: {elapsed:.2f}s")
            if response.status_code == 200:
                print(f"Full JSON Response: {response.json()}")
            else:
                print(f"Error Response: {response.text}")

    except Exception as e:
        print(f"HTTP Request failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
