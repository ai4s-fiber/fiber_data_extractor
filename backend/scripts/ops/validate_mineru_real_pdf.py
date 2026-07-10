"""Validate a real PDF against the local MinerU async API.

This script intentionally prints only status and aggregate counts. It does not
read or write API tokens.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx


COMPLETED_STATUSES = {"completed", "done", "success", "succeeded"}
FAILED_STATUSES = {"failed", "error"}


def _decode_json_field(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:8001")
    parser.add_argument("--timeout-seconds", type=float, default=1800)
    parser.add_argument("--poll-seconds", type=float, default=2)
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    api_url = args.api_url.rstrip("/")
    started = time.time()

    timeout = httpx.Timeout(
        args.timeout_seconds,
        connect=30,
        read=args.timeout_seconds,
        write=300,
        pool=30,
    )

    with httpx.Client(trust_env=False, timeout=timeout) as client:
        with pdf_path.open("rb") as handle:
            response = client.post(
                f"{api_url}/tasks",
                files={"files": (pdf_path.name, handle, "application/pdf")},
                data={
                    "backend": "pipeline",
                    "parse_method": "auto",
                    "lang_list": "ch",
                    "formula_enable": "true",
                    "table_enable": "true",
                    "image_analysis": "true",
                    "return_md": "true",
                    "return_middle_json": "true",
                    "return_model_output": "false",
                    "return_content_list": "true",
                    "return_images": "false",
                    "response_format_zip": "false",
                    "return_original_file": "false",
                },
            )

        print("submit_status", response.status_code)
        print(response.text[:800])
        response.raise_for_status()
        task_id = str(response.json()["task_id"])

        while True:
            status_response = client.get(f"{api_url}/tasks/{task_id}")
            print("poll_status", status_response.status_code, status_response.text[:500])
            status_response.raise_for_status()
            status_payload = status_response.json()
            status = str(status_payload.get("status") or "").lower()

            if status in FAILED_STATUSES:
                raise RuntimeError(status_payload)
            if status in COMPLETED_STATUSES:
                break
            if time.time() - started > args.timeout_seconds:
                raise TimeoutError("MinerU task did not finish before timeout")
            time.sleep(args.poll_seconds)

        result_response = client.get(f"{api_url}/tasks/{task_id}/result")
        print("result_status", result_response.status_code)
        print(result_response.text[:1000])
        result_response.raise_for_status()

    result = result_response.json()
    results = result.get("results")
    if not isinstance(results, dict) or not results:
        raise RuntimeError("MinerU result payload does not contain results")

    document_name, data = next(iter(results.items()))
    if not isinstance(data, dict):
        raise RuntimeError("MinerU result entry is not an object")

    content_list = _decode_json_field(data.get("content_list"), [])
    content_list_v2 = _decode_json_field(data.get("content_list_v2"), [])
    middle_json = _decode_json_field(data.get("middle_json"), {})
    md_content = data.get("md_content") or ""

    summary = {
        "task_id": task_id,
        "elapsed_seconds": round(time.time() - started, 1),
        "document": document_name,
        "md_chars": len(md_content),
        "content_list_items": len(content_list) if isinstance(content_list, list) else 0,
        "content_list_v2_items": len(content_list_v2) if isinstance(content_list_v2, list) else 0,
        "middle_json_keys": sorted(middle_json.keys()) if isinstance(middle_json, dict) else [],
        "top_level_keys": sorted(result.keys()),
        "result_entry_keys": sorted(data.keys()),
    }
    print("summary", json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
