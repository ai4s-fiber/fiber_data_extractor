"""Smoke-check configured MinerU parser backends without printing secrets."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(BACKEND_ROOT))

from app.services.mineru_client import MinerUClient  # noqa: E402


async def check_cloud_token_connection() -> None:
    print("\n=========================================")
    print("1. MinerU Cloud token and endpoint")
    print("=========================================")
    client = MinerUClient()
    if not (client.token or "").strip():
        print("[ERROR] MINERU_CLOUD_TOKEN is not configured")
        return
    print("[OK] MINERU_CLOUD_TOKEN is configured")

    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as http_client:
            response = await http_client.get(
                "https://mineru.net/api/v4/extract-results/noop",
                headers={"Authorization": f"Bearer {client.token}"},
            )
            print(f"Request HTTP Status: {response.status_code}")
            if response.status_code in {400, 404}:
                print("[OK] MinerU Cloud endpoint is reachable; token was not rejected with 401")
            elif response.status_code == 401:
                print("[ERROR] MinerU Cloud rejected the configured token")
            else:
                print(f"[INFO] MinerU Cloud responded with status {response.status_code}")
    except Exception as exc:
        print(f"[ERROR] MinerU Cloud connection error: {exc}")


async def check_local_mineru_availability() -> None:
    print("\n=========================================")
    print("2. Local MinerU service")
    print("=========================================")
    client = MinerUClient()
    print(f"Configured Local URL: {client.api_url}")

    try:
        async with httpx.AsyncClient(timeout=3.0, trust_env=False) as http_client:
            response = await http_client.get(f"{client.api_url}/tasks")
            print(f"Request HTTP Status: {response.status_code}")
            print("[OK] Local MinerU service is reachable")
    except Exception as exc:
        print(f"[INFO] Local MinerU service is offline or not running: {exc}")


async def run_cloud_parse(pdf_path: Path) -> None:
    print("\n=========================================")
    print("3. MinerU Cloud PDF parse")
    print("=========================================")
    client = MinerUClient()
    result = await client.parse_pdf(pdf_path, strategy="mineru_cloud")
    print(f"[OK] Cloud parse completed: task_id={result.task_id}")
    print(f"Pages: {len(result.pages)}")
    print(f"Blocks: {len(result.blocks)}")
    print(f"Markdown chars: {len(result.markdown)}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pdf",
        type=Path,
        help="Optional PDF path. When provided, performs a real MinerU Cloud parse.",
    )
    args = parser.parse_args()

    await check_cloud_token_connection()
    await check_local_mineru_availability()

    if args.pdf:
        if not args.pdf.exists():
            raise FileNotFoundError(args.pdf)
        await run_cloud_parse(args.pdf)


if __name__ == "__main__":
    asyncio.run(main())
