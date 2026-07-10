"""LLM metrics service tests."""

import inspect

from app.services import llm_metrics


def test_record_call_awaits_get_redis():
    source = inspect.getsource(llm_metrics.record_call)
    assert "await get_redis()" in source
    assert "= get_redis()" not in source.replace("await get_redis()", "")


def test_get_job_summary_awaits_get_redis():
    source = inspect.getsource(llm_metrics.get_job_summary)
    assert "await get_redis()" in source
