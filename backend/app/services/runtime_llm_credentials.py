"""Process-local LLM credentials for dedicated extraction workers."""

from __future__ import annotations

import contextvars


_runtime_llm_api_keys: contextvars.ContextVar[dict[int, str] | None] = (
    contextvars.ContextVar("runtime_llm_api_keys", default=None)
)


def bind_runtime_llm_api_key(project_id: int, api_key: str) -> contextvars.Token:
    """Bind a project key to the current async context without persisting it."""
    normalized_key = (api_key or "").strip()
    if not normalized_key:
        raise ValueError("Runtime LLM API key cannot be empty")

    current = _runtime_llm_api_keys.get() or {}
    updated = dict(current)
    updated[int(project_id)] = normalized_key
    return _runtime_llm_api_keys.set(updated)


def reset_runtime_llm_api_key(token: contextvars.Token) -> None:
    """Restore the previous runtime credential context."""
    _runtime_llm_api_keys.reset(token)


def resolve_llm_api_key(project_id: int, persisted_api_key: str | None) -> str:
    """Prefer a process-local key and retain persisted project compatibility."""
    runtime_keys = _runtime_llm_api_keys.get() or {}
    runtime_key = (runtime_keys.get(int(project_id)) or "").strip()
    if runtime_key:
        return runtime_key
    return (persisted_api_key or "").strip()
