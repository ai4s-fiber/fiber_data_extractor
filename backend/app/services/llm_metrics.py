"""LLM call metrics for cost and reliability observability."""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from typing import Any

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
    if client is None:
        return
    payload = json.dumps(asdict(metric), ensure_ascii=False)
    key = _redis_key(metric.job_id)
    await client.rpush(key, payload)
    await client.expire(key, 60 * 60 * 24 * 7)


async def get_job_summary(job_id: int) -> LLMJobSummary:
    client = await get_redis()
    summary = LLMJobSummary(job_id=job_id)
    if client is None:
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


@asynccontextmanager
async def track_llm_call(
    *,
    job_id: int | None,
    stage: str,
    model: str,
    call_type: str,
    prompt_chars: int = 0,
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
    )
    try:
        yield metric
        metric.success = True
    except Exception as exc:
        metric.error = str(exc)[:500]
        raise
    finally:
        metric.latency_ms = round((time.monotonic() - started) * 1000, 1)
        await record_call(metric)
