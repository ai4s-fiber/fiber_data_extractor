"""LLM response parsing regression tests."""

import asyncio
from types import SimpleNamespace

import pytest

from app.services.llm_client import (
    LLMClient,
    OpenAICompatibleClient,
    _tolerant_parse_json,
)


def test_tolerant_parser_recovers_complete_rows_from_truncated_json():
    raw = (
        '{"performances":['
        '{"sample_id":"S1","performance_metric":"tensile_strength",'
        '"performance_value":"147","performance_unit":"MPa"},'
        '{"sample_id":"S2","performance_metric":"modulus",'
        '"performance_value":"'
    )

    parsed = _tolerant_parse_json(raw)

    assert parsed["performances"][0] == {
        "sample_id": "S1",
        "performance_metric": "tensile_strength",
        "performance_value": "147",
        "performance_unit": "MPa",
    }


def test_tolerant_json_does_not_hide_upstream_request_failure():
    class FailingClient(LLMClient):
        def generate_text(self, *_args, **_kwargs):
            raise RuntimeError("upstream timeout")

    client = FailingClient(api_key="test", model="test")

    with pytest.raises(RuntimeError, match="upstream timeout"):
        client.generate_json_tolerant("return json", "payload")


@pytest.mark.asyncio
async def test_openai_async_request_is_actually_cancelled_on_timeout():
    state = {"request_cancelled": False, "client_closed": False}

    class Completions:
        async def create(self, **_kwargs):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                state["request_cancelled"] = True
                raise

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=Completions())

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            state["client_closed"] = True

    client = OpenAICompatibleClient(
        api_key="test",
        model="test",
        base_url="https://example.test/v1",
    )
    client._async_client_factory = FakeAsyncClient

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            client.agenerate_json_tolerant("return json", "payload"),
            timeout=0.01,
        )

    assert state == {"request_cancelled": True, "client_closed": True}


@pytest.mark.asyncio
async def test_openai_async_request_records_task_local_usage():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
        usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    )

    class Completions:
        async def create(self, **_kwargs):
            return response

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=Completions())

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    client = OpenAICompatibleClient(
        api_key="test",
        model="test",
        base_url="https://example.test/v1",
    )
    client._async_client_factory = FakeAsyncClient

    parsed, raw = await client.agenerate_json_tolerant("return json", "payload")

    assert parsed == {"ok": True}
    assert raw == '{"ok": true}'
    assert client.last_usage == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }


@pytest.mark.asyncio
async def test_openai_async_request_passes_gpt5_reasoning_effort():
    captured = {}
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
        usage={},
    )

    class Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return response

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=Completions())

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    client = OpenAICompatibleClient(api_key="test", model="gpt-5.5")
    client._async_client_factory = FakeAsyncClient

    await client.agenerate_json_tolerant(
        "return json", "payload", reasoning_effort="low"
    )

    assert captured["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_openai_response_format_failure_is_cached():
    requests = []
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
        usage={},
    )

    class Completions:
        async def create(self, **kwargs):
            requests.append(dict(kwargs))
            if len(requests) == 1:
                raise RuntimeError("400 unsupported response_format json_object")
            return response

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=Completions())

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    client = OpenAICompatibleClient(api_key="test", model="gpt-5.5")
    client._async_client_factory = FakeAsyncClient

    await client.agenerate_json_tolerant("return json", "first")
    await client.agenerate_json_tolerant("return json", "second")

    assert len(requests) == 3
    assert "response_format" in requests[0]
    assert "response_format" not in requests[1]
    assert "response_format" not in requests[2]
