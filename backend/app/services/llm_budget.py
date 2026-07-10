"""Cost controls for OpenAI-compatible LLM calls."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LLMRequestBudget:
    requested_max_tokens: int
    max_tokens: int
    prompt_chars: int
    was_capped: bool


def is_qwen_model(model: str) -> bool:
    return (model or "").strip().lower().startswith("qwen")


def is_dashscope_base_url(base_url: str) -> bool:
    return "dashscope.aliyuncs.com" in (base_url or "").strip().lower()


def should_disable_thinking(model: str, base_url: str, enabled: bool) -> bool:
    if not enabled:
        return False
    return is_qwen_model(model) or is_dashscope_base_url(base_url)


def dashscope_extra_body(model: str, base_url: str, disable_thinking: bool) -> dict | None:
    if should_disable_thinking(model, base_url, disable_thinking):
        return {"enable_thinking": False}
    return None


def clamp_max_tokens(
    *,
    requested_max_tokens: int,
    prompt_chars: int,
    global_cap: int,
) -> LLMRequestBudget:
    requested = max(1, int(requested_max_tokens or 1))
    cap = max(1, int(global_cap or requested))
    max_tokens = min(requested, cap)
    return LLMRequestBudget(
        requested_max_tokens=requested,
        max_tokens=max_tokens,
        prompt_chars=max(0, int(prompt_chars or 0)),
        was_capped=max_tokens < requested,
    )
