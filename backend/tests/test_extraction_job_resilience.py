"""Extraction job retry classification tests."""

import asyncio

import pytest

from app.services.extraction_jobs import (
    ExtractionJobBackend,
    classify_extraction_error,
    is_retryable_extraction_error,
)
from app.services.extractor_v7.exceptions import NoExtractableResults


def test_transient_upstream_errors_are_retryable():
    for message, expected in (
        ("LLM stage holistic_table timed out", "llm_timeout"),
        ("HTTP 429 too many requests", "llm_rate_limited"),
        ("HTTP 503 service unavailable", "upstream_unavailable"),
        ("MinerU status polling timed out", "mineru_timeout"),
        ("抽取超时（超过 1800 秒）", "llm_timeout"),
    ):
        assert classify_extraction_error(message) == expected
        assert is_retryable_extraction_error(message) is True


def test_configuration_and_auth_errors_are_not_retried():
    for message in (
        "HTTP 401 unauthorized API key",
        "model not found 404",
        "invalid base URL",
    ):
        assert is_retryable_extraction_error(message) is False


def test_suspicious_empty_result_is_classified_and_not_retried():
    error = NoExtractableResults("quantitative evidence produced no records")

    assert classify_extraction_error(error) == "no_extractable_results"
    assert is_retryable_extraction_error(error) is False


@pytest.mark.asyncio
async def test_shutdown_cancels_workers_and_blocks_new_work(monkeypatch):
    backend = ExtractionJobBackend(None, 2)
    started = asyncio.Event()

    async def worker():
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(worker())
    backend._tasks.add(task)
    await started.wait()
    await backend.shutdown()

    assert task.cancelled()
    assert backend._tasks == set()

    async def unexpected_queue_access(_limit):
        raise AssertionError("queue must not be accessed during shutdown")

    monkeypatch.setattr(
        "app.services.extraction_jobs.extraction_queue.available_slots",
        unexpected_queue_access,
    )
    await backend.try_start_next()
