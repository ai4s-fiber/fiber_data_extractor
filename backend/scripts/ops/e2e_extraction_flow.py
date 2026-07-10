#!/usr/bin/env python3
"""End-to-end HTTP test for the open workspace extraction flow."""

from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
PROJECT_ID = int(sys.argv[2]) if len(sys.argv) > 2 else 1
POLL_SECONDS = int(sys.argv[3]) if len(sys.argv) > 3 else 600


async def main() -> int:
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
        print("== 1. Health ==")
        h = await client.get(f"{BASE}/api/health")
        h.raise_for_status()
        health = h.json()
        print(json.dumps(health, ensure_ascii=False))
        if not health.get("redis"):
            errors.append("redis not connected")

        print("\n== 2. List papers ==")
        papers = await client.get(f"{BASE}/api/projects/{PROJECT_ID}/papers")
        papers.raise_for_status()
        paper_list = papers.json()
        print(f"papers: {len(paper_list)}")
        if not paper_list:
            errors.append("no papers to extract")
            return report(errors)

        paper = paper_list[0]
        paper_id = paper["id"]
        print(f"target paper_id={paper_id} status={paper.get('status')}")

        print("\n== 3. Reset paper to uploaded if failed ==")
        if paper.get("status") == "failed":
            # no direct API; re-extract with confirm_wipe if needed
            pass

        print("\n== 4. Trigger extraction ==")
        extract_body = {
            "model_mode": "weak",
            "parser_strategy": "mineru_cloud",
            "confirm_wipe": True,
        }
        ex = await client.post(
            f"{BASE}/api/projects/{PROJECT_ID}/papers/{paper_id}/extract",
            json=extract_body,
        )
        if ex.status_code not in (200, 202):
            errors.append(f"extract failed: {ex.status_code} {ex.text[:300]}")
            return report(errors)
        job = ex.json()
        job_id = job.get("job_id")
        print(f"job queued: id={job_id} status={job.get('status')}")

        print("\n== 5. Poll extraction-status ==")
        started = time.monotonic()
        last_percent = -1
        final_status = None
        while time.monotonic() - started < POLL_SECONDS:
            st = await client.get(
                f"{BASE}/api/projects/{PROJECT_ID}/papers/{paper_id}/extraction-status",
            )
            st.raise_for_status()
            data = st.json()
            status = data.get("status")
            step = data.get("step") or data.get("extraction_step")
            percent = data.get("percent") or data.get("extraction_percent") or 0
            msg = data.get("progress_message") or ""
            if percent != last_percent or step != final_status:
                print(f"  [{int(time.monotonic()-started):4d}s] {status:8s} {step:12s} {percent:3d}% {msg[:60]}")
                last_percent = percent
            final_status = status
            if status in ("completed", "failed", "cancelled"):
                if status == "failed":
                    errors.append(f"extraction failed: {data.get('error_message')}")
                break
            await asyncio.sleep(5)
        else:
            errors.append(f"extraction timed out after {POLL_SECONDS}s")

        print("\n== 6. Papers list after extraction ==")
        papers2 = await client.get(f"{BASE}/api/projects/{PROJECT_ID}/papers")
        papers2.raise_for_status()
        p2 = papers2.json()[0]
        print(f"paper status={p2.get('status')} job_status={p2.get('latest_job_status')}")

        if final_status == "completed":
            print("\n== 7. Candidates count ==")
            c = await client.get(
                f"{BASE}/api/projects/{PROJECT_ID}/candidates/count",
                params={"review_status": "pending"},
            )
            c.raise_for_status()
            count = c.json().get("count", 0)
            print(f"pending candidates: {count}")
            if count == 0:
                errors.append("extraction completed but no candidates")

    return report(errors)


def report(errors: list[str]) -> int:
    print("\n" + "=" * 50)
    if errors:
        print("FAILED:")
        for e in errors:
            print(" -", e)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
