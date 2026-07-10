import asyncio
import sys
import time
from pathlib import Path

# Add app to path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.mineru_client import MinerUClient
from app.services.document_context import build_legacy_document_context
from app.core.config import settings


async def test_legacy_parser(pdf_path: Path):
    print("\n=========================================")
    print("1. Test Legacy Parser Strategy (Plain Text)")
    print("=========================================")
    start_time = time.monotonic()
    try:
        # call build_legacy_document_context directly
        context = build_legacy_document_context(
            paper_id=1,
            job_id=None,
            parse_run_id=None,
            pdf_path=str(pdf_path)
        )
        elapsed = time.monotonic() - start_time
        print(f"[OK] Completed Legacy text extraction! Time: {elapsed:.2f}s")
        print(f"  - Total Pages: {len(context.pages)}")
        print(f"  - Total Blocks: {len(context.blocks)}")
        print(f"  - Total Chunks: {len(context.chunks())}")
        if context.chunks():
            print(f"  - First Chunk Preview (first 150 chars):\n{context.chunks()[0]['raw_text'][:150]}...")
    except Exception as e:
        print(f"[ERROR] Legacy extraction failed: {e}")
        import traceback
        traceback.print_exc()

async def test_cloud_token_connection():
    print("\n=========================================")
    print("2. Test MinerU Cloud Token Connection (Cloud VLM)")
    print("=========================================")
    client = MinerUClient()
    token_preview = f"{client.token[:15]}...{client.token[-15:]}" if client.token else "None"
    print(f"Current Configured Token: {token_preview}")

    import httpx
    import uuid

    headers = {
        "Authorization": f"Bearer {client.token}",
        "Content-Type": "application/json",
    }

    payload = {
        "files": [{"name": "test_auth_check.pdf", "data_id": uuid.uuid4().hex}],
        "model_version": "vlm"
    }

    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as http_client:
            response = await http_client.post(
                "https://mineru.net/api/v4/file-urls/batch",
                headers=headers,
                json=payload
            )
            print(f"Request HTTP Status: {response.status_code}")
            if response.status_code == 200:
                res_json = response.json()
                print(f"API Response Code: {res_json.get('code')} ({res_json.get('msg')})")
                if res_json.get("code") == 0:
                    print("[OK] Cloud MinerU API Token is valid and connection works perfectly!")
                    print(f"  - Batch ID: {res_json['data']['batch_id']}")
                    print(f"  - Upload URL: {res_json['data']['file_urls'][0][:80]}...")
                else:
                    print(f"[ERROR] API returned error: {res_json.get('msg')}")
            else:
                print(f"[ERROR] HTTP Error response: {response.text}")
    except Exception as e:
        print(f"[ERROR] Cloud connection error: {e}")

async def test_local_mineru_availability():
    print("\n=========================================")
    print("3. Test Local MinerU Connection (Local MinerU)")
    print("=========================================")
    client = MinerUClient()
    print(f"Current Configured Local URL: {client.api_url}")

    import httpx
    try:
        async with httpx.AsyncClient(timeout=3.0, trust_env=False) as http_client:
            response = await http_client.get(f"{client.api_url}/tasks")
            print(f"Request HTTP Status: {response.status_code}")
            print("[OK] Local MinerU service is active and responsive!")
    except Exception as e:
        print(f"[INFO] Local MinerU service is offline or not running: {e}")
        print("This is normal, since local service requires GPU node deployment.")


async def main():
    # Find a PDF file to run legacy parse
    uploads_dir = Path(__file__).resolve().parents[1] / "uploads"
    pdf_files = list(uploads_dir.glob("**/*.pdf"))

    if not pdf_files:
        print("没有在 uploads 中找到任何 PDF 测试文件")
        return

    test_pdf = pdf_files[0]
    print(f"使用测试文件: {test_pdf} (大小: {test_pdf.stat().st_size / 1024:.2f} KB)")

    await test_legacy_parser(test_pdf)
    await test_cloud_token_connection()
    await test_local_mineru_availability()

if __name__ == "__main__":
    asyncio.run(main())
