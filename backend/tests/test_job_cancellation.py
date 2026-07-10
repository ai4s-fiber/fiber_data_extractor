"""Cancellation helper tests."""

import pytest

from app.services.extractor_v7.exceptions import ExtractionCancelled
from app.services.job_cancellation import run_with_cancel_poll


@pytest.mark.asyncio
async def test_run_with_cancel_poll_raises_when_flagged(monkeypatch):
    async def slow_work():
        import asyncio
        await asyncio.sleep(60)
        return "done"

    async def fake_cancel(job_id):
        return job_id == 42

    monkeypatch.setattr(
        "app.services.job_cancellation.is_job_cancel_requested",
        fake_cancel,
    )

    with pytest.raises(ExtractionCancelled):
        await run_with_cancel_poll(slow_work(), 42, poll_seconds=0.01)
