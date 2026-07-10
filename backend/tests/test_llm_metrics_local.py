import json

import pytest

from app.services.llm_metrics import (
    LLMCallMetric,
    get_job_summary,
    record_call,
)


@pytest.mark.asyncio
async def test_local_llm_metrics_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.llm_metrics.get_redis", lambda: _none_async())
    monkeypatch.setattr("app.services.llm_metrics.settings.LLM_METRICS_LOCAL_ENABLED", True)
    monkeypatch.setattr("app.services.llm_metrics.settings.LLM_METRICS_DIR", str(tmp_path))

    await record_call(
        LLMCallMetric(
            job_id=123,
            stage="stage1",
            model="qwen3.7-plus",
            call_type="json_tolerant",
            latency_ms=12.5,
            success=True,
            prompt_chars=100,
            response_chars=20,
            requested_max_tokens=1800,
            effective_max_tokens=1200,
            capped=True,
            prompt_tokens=50,
            completion_tokens=8,
            total_tokens=58,
        )
    )

    path = tmp_path / "job_123.jsonl"
    item = json.loads(path.read_text(encoding="utf-8").strip())
    assert item["model"] == "qwen3.7-plus"
    assert item["capped"] is True

    summary = await get_job_summary(123)
    assert summary.total_calls == 1
    assert summary.failed_calls == 0
    assert summary.calls[0]["effective_max_tokens"] == 1200
    assert summary.calls[0]["total_tokens"] == 58


async def _none_async():
    return None
