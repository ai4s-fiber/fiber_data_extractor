"""LLM call metrics for cost and reliability observability."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.config import settings
from app.core.redis_client import get_redis


@dataclass
class LLMCallMetric:
    job_id: int | None
    stage: str
    model: str
    call_type: str
    latency_ms: float
    success: bool
    error: str = ""
    prompt_chars: int = 0
    response_chars: int = 0
    requested_max_tokens: int = 0
    effective_max_tokens: int = 0
    capped: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMJobSummary:
    job_id: int
    total_calls: int = 0
    failed_calls: int = 0
    total_latency_ms: float = 0.0
    calls: list[dict[str, Any]] = field(default_factory=list)


def _redis_key(job_id: int) -> str:
    return f"fiber:llm_metrics:{job_id}"


async def record_call(metric: LLMCallMetric) -> None:
    if metric.job_id is None:
        return
    client = await get_redis()
    payload = json.dumps(asdict(metric), ensure_ascii=False)
    if client is None:
        if settings.LLM_METRICS_LOCAL_ENABLED:
            await _record_call_local(metric.job_id, payload)
        return
    key = _redis_key(metric.job_id)
    await client.rpush(key, payload)
    await client.expire(key, 60 * 60 * 24 * 7)


async def _record_call_local(job_id: int, payload: str) -> None:
    import asyncio

    def _write() -> None:
        root = Path(settings.LLM_METRICS_DIR)
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"job_{job_id}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(payload)
            fh.write("\n")

    await asyncio.to_thread(_write)


async def get_job_summary(job_id: int) -> LLMJobSummary:
    client = await get_redis()
    summary = LLMJobSummary(job_id=job_id)
    if client is None:
        if settings.LLM_METRICS_LOCAL_ENABLED:
            return await _get_job_summary_local(job_id)
        return summary
    raw_items = await client.lrange(_redis_key(job_id), 0, -1)
    for raw in raw_items:
        try:
            item = json.loads(raw)
            summary.calls.append(item)
            summary.total_calls += 1
            summary.total_latency_ms += float(item.get("latency_ms") or 0)
            if not item.get("success"):
                summary.failed_calls += 1
        except json.JSONDecodeError:
            continue
    return summary


async def _get_job_summary_local(job_id: int) -> LLMJobSummary:
    import asyncio

    def _read() -> LLMJobSummary:
        summary = LLMJobSummary(job_id=job_id)
        path = Path(settings.LLM_METRICS_DIR) / f"job_{job_id}.jsonl"
        if not path.exists():
            return summary
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                summary.calls.append(item)
                summary.total_calls += 1
                summary.total_latency_ms += float(item.get("latency_ms") or 0)
                if not item.get("success"):
                    summary.failed_calls += 1
            except json.JSONDecodeError:
                continue
        return summary

    return await asyncio.to_thread(_read)


@asynccontextmanager
async def track_llm_call(
    *,
    job_id: int | None,
    stage: str,
    model: str,
    call_type: str,
    prompt_chars: int = 0,
    requested_max_tokens: int = 0,
    effective_max_tokens: int = 0,
    capped: bool = False,
):
    started = time.monotonic()
    metric = LLMCallMetric(
        job_id=job_id,
        stage=stage,
        model=model,
        call_type=call_type,
        latency_ms=0.0,
        success=False,
        prompt_chars=prompt_chars,
        requested_max_tokens=requested_max_tokens,
        effective_max_tokens=effective_max_tokens,
        capped=capped,
    )
    try:
        yield metric
        metric.success = True
    except BaseException as exc:
        metric.error = (
            "request cancelled or timed out"
            if isinstance(exc, asyncio.CancelledError)
            else str(exc)[:500]
        )
        raise
    finally:
        metric.latency_ms = round((time.monotonic() - started) * 1000, 1)
        await record_call(metric)
