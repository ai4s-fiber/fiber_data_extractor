"""LLM endpoint normalization and connection diagnostics."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings


def normalize_openai_base_url_candidates(raw_base_url: str | None) -> list[str]:
    """Return candidate OpenAI-compatible base URLs to test."""
    submitted = (raw_base_url or settings.DEFAULT_LLM_BASE_URL).strip().rstrip("/")
    if not submitted:
        submitted = settings.DEFAULT_LLM_BASE_URL

    for suffix in ("/chat/completions", "/v1/chat/completions"):
        if submitted.endswith(suffix):
            submitted = submitted[: -len(suffix)]
            break

    candidates = [submitted]
    if not submitted.endswith("/v1"):
        candidates.append(f"{submitted}/v1")

    unique: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in unique:
            unique.append(candidate)
    return unique


def _preview_response(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "")
    text = response.text.strip()
    if "text/html" in content_type.lower():
        text = text.replace("\n", " ")
    return text[:500]


def _message_for_failure(attempts: list[dict[str, Any]]) -> str:
    if not attempts:
        return "未发起任何连接测试"
    statuses = [a.get("http_status") for a in attempts if a.get("http_status")]
    if 401 in statuses or 403 in statuses:
        return "API Key 无效或没有调用权限"
    if 404 in statuses:
        return "接口地址或模型名称不正确；已尝试自动追加 /v1"
    if 429 in statuses:
        return "服务限流或余额不足，请稍后重试或检查账户额度"
    if any(status and status >= 500 for status in statuses):
        return "模型服务暂时不可用或网关返回服务器错误"
    for attempt in attempts:
        if attempt.get("error"):
            return f"请求失败：{attempt['error']}"
    return "连接失败，请检查 Base URL、模型名称或网络代理"


async def test_openai_compatible_connection(
    api_key: str,
    model: str,
    raw_base_url: str | None,
    timeout_seconds: float = 8.0,
) -> dict[str, Any]:
    """Test submitted OpenAI-compatible settings with normalized base URL candidates."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": "Hello! Reply with 'OK' only to confirm API accessibility.",
            }
        ],
        "max_tokens": 10,
    }

    attempts: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        for base_url in normalize_openai_base_url_candidates(raw_base_url):
            request_url = f"{base_url.rstrip('/')}/chat/completions"
            attempt: dict[str, Any] = {
                "base_url": base_url,
                "request_url": request_url,
                "http_status": None,
                "json_response": False,
                "response_preview": "",
            }
            try:
                response = await client.post(request_url, headers=headers, json=payload)
                attempt["http_status"] = response.status_code
                attempt["response_preview"] = _preview_response(response)
                try:
                    data = response.json()
                    attempt["json_response"] = True
                except ValueError:
                    data = None

                if response.status_code == 200 and isinstance(data, dict) and data.get("choices"):
                    reply = (
                        (data.get("choices") or [{}])[0]
                        .get("message", {})
                        .get("content", "")
                    )
                    return {
                        "success": True,
                        "working_base_url": base_url,
                        "attempts": attempts + [attempt],
                        "message": f"连接成功，使用 {base_url}",
                        "model_reply": str(reply).strip(),
                    }
            except Exception as exc:
                attempt["error"] = str(exc)
                attempt["response_preview"] = str(exc)[:500]
            attempts.append(attempt)

    return {
        "success": False,
        "working_base_url": None,
        "attempts": attempts,
        "message": _message_for_failure(attempts),
    }
