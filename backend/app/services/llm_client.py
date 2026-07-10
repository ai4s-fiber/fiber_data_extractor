"""LLM Client abstraction layer migrated from V5.

Provides:
- OpenAI-compatible API via the openai SDK (with response_format=json_object)
- Anthropic Messages API via httpx (with content block parsing)
- Multi-layer JSON recovery (fence stripping, brace extraction, regex KV)
- Automatic retry with exponential backoff
- Graceful fallback when response_format is unsupported (e.g. DeepSeek)
- Vision/multimodal support for both providers
- Connection test utility
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# JSON recovery utilities (portable, no dependencies)
# ---------------------------------------------------------------------------


def _fix_common_json_issues(text: str) -> str:
    """Fix trailing commas and single-quote strings."""
    text = re.sub(r",\s*([}\]])", r"\1", text)
    if '"' not in text and "'" in text:
        text = text.replace("'", '"')
    return text


def _regex_extract_items(text: str) -> dict[str, Any] | None:
    """Recover structured items from semi-structured text via key-value pairs."""
    kv_pattern = re.compile(r'"?(\\w+)"?\\s*[:=]\\s*"([^"]*)"')
    matches = kv_pattern.findall(text)
    if len(matches) < 3:
        return None
    items: list[dict[str, str]] = []
    current: dict[str, str] = {}
    seen: set[str] = set()
    for key, value in matches:
        if key in seen and current:
            items.append(current)
            current = {}
            seen = set()
        current[key] = value
        seen.add(key)
    if current:
        items.append(current)
    return {"_items": items} if items else None


def _tolerant_parse_json(text: str) -> dict[str, Any]:
    """Multi-layer JSON recovery. Never raises — always returns a dict."""
    text = (text or "").strip()
    if not text:
        return {}

    cleaned = text
    # Strip markdown fences
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    # Layer 1: direct parse
    for attempt_text in (cleaned, text):
        try:
            result = json.loads(attempt_text)
            if isinstance(result, dict):
                return result
            if isinstance(result, list):
                return {"_items": result}
        except (json.JSONDecodeError, ValueError):
            pass

    # Layer 2: find outermost braces / brackets
    for start_ch, end_ch in ("{", "}"), ("[", "]"):
        s = cleaned.find(start_ch)
        e = cleaned.rfind(end_ch)
        if s >= 0 and e > s:
            fragment = cleaned[s : e + 1]
            for fix_fn in (lambda t: t, _fix_common_json_issues):
                try:
                    result = json.loads(fix_fn(fragment))
                    if isinstance(result, dict):
                        return result
                    if isinstance(result, list):
                        return {"_items": result}
                except (json.JSONDecodeError, ValueError):
                    pass

    # Layer 3: regex key-value extraction
    kv = _regex_extract_items(cleaned)
    if kv:
        return kv

    # Layer 4: return raw text
    return {"_raw_text": text[:4000], "_parse_failed": True}


def _extract_json(text: str) -> dict[str, Any]:
    """Strict JSON extraction — strips fences and raises on failure."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        text = text[start : end + 1]
    return json.loads(text)


# ---------------------------------------------------------------------------
# Error formatting (Chinese-language user messages)
# ---------------------------------------------------------------------------


def _format_ai_exception(exc: Exception) -> str:
    message = str(exc) or exc.__class__.__name__
    lower = message.lower()
    if "socks proxy" in lower or "socksio" in lower:
        return (
            "检测到当前网络环境正在使用 SOCKS 代理，但缺少 socksio/httpx[socks] 支持。\n"
            "请使用包含 socksio 的新版软件包；如果从源码运行，请执行：\n"
            'python -m pip install "httpx[socks]" socksio\n\n'
            f"原始错误：{message}"
        )
    if "timed out" in lower or "timeout" in lower:
        return (
            "请求超时。建议增大超时，检查代理或 Base URL，或换用更快的模型。\n\n"
            f"原始错误：{message}"
        )
    if "401" in message or "unauthorized" in lower or "api key" in lower:
        return f"API Key 可能无效或没有权限。\n\n原始错误：{message}"
    if "404" in message or "not found" in lower:
        return f"模型名或接口地址可能不正确。\n\n原始错误：{message}"
    if "json" in lower:
        return f"模型返回内容不是合法 JSON，请重试或换模型。\n\n原始错误：{message}"
    return message


def _should_retry_without_response_format(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        item in message
        for item in (
            "response_format",
            "json_object",
            "unsupported",
            "not support",
            "invalid parameter",
            "400",
        )
    )


# ---------------------------------------------------------------------------
# Anthropic text extraction from content blocks
# ---------------------------------------------------------------------------


def _anthropic_text(data: dict[str, Any]) -> str:
    content = data.get("content", [])
    if isinstance(content, str):
        return content
    parts: list[str] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, dict) and "text" in item:
                parts.append(str(item.get("text", "")))
    return "\n".join(part for part in parts if part).strip()


# ---------------------------------------------------------------------------
# LLM Client base class
# ---------------------------------------------------------------------------


class LLMClient:
    """Abstract base for LLM API clients (OpenAI-compatible and Anthropic)."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "",
        timeout_seconds: int = 90,
        max_retries: int = 2,
    ) -> None:
        if not api_key.strip():
            raise RuntimeError("尚未配置 API Key。")
        if not model.strip():
            raise RuntimeError("尚未配置模型名称。")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    def test_connection(self) -> str:
        content = self.generate_text(
            "You are a connection test assistant.",
            "请只回复 OK，用于测试 API 连通性。",
            max_tokens=32,
        )
        return f"连接成功。模型返回：{content.strip() or '空响应'}"

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        content = self.generate_text(
            system_prompt, user_prompt, max_tokens=max_tokens, json_mode=True
        )
        try:
            return _extract_json(content)
        except Exception:
            preview = content.strip().replace("\n", " ")[:800] or "空响应"
            if content.strip():
                try:
                    repaired = self.generate_text(
                        "You repair invalid JSON responses. Return valid JSON only.",
                        (
                            "The previous model response was not valid JSON. "
                            "Convert it into one valid JSON object. Do not add explanations.\n\n"
                            f"Invalid response:\n{content[:4000]}"
                        ),
                        max_tokens=max_tokens,
                        json_mode=True,
                    )
                    return _extract_json(repaired)
                except Exception:
                    pass
            raise RuntimeError(
                f"模型返回内容不是合法 JSON。返回预览：{preview}"
            )

    def generate_json_tolerant(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
    ) -> tuple[dict[str, Any], str]:
        """Like generate_json but never raises on parse failure."""
        raw = ""
        try:
            raw = self.generate_text(
                system_prompt, user_prompt, max_tokens=max_tokens, json_mode=True
            )
        except Exception:
            try:
                raw = self.generate_text(
                    system_prompt, user_prompt, max_tokens=max_tokens, json_mode=False
                )
            except Exception:
                pass
        parsed = _tolerant_parse_json(raw)
        return parsed, raw

    async def agenerate_json_tolerant(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
    ) -> tuple[dict[str, Any], str]:
        return await asyncio.to_thread(
            self.generate_json_tolerant,
            system_prompt,
            user_prompt,
            max_tokens,
        )

    async def agenerate_vision_json_tolerant(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[bytes],
        max_tokens: int = 4096,
    ) -> tuple[dict[str, Any], str]:
        return await asyncio.to_thread(
            self.generate_vision_json_tolerant,
            system_prompt,
            user_prompt,
            images,
            max_tokens,
        )

    def generate_vision_json_tolerant(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[bytes],
        max_tokens: int = 4096,
    ) -> tuple[dict[str, Any], str]:
        raw = ""
        try:
            raw = self.generate_vision_text(
                system_prompt, user_prompt, images, max_tokens=max_tokens
            )
        except Exception:
            pass
        parsed = _tolerant_parse_json(raw)
        return parsed, raw

    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
        raise NotImplementedError

    def generate_vision_text(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[bytes],
        max_tokens: int = 4096,
    ) -> str:
        raise RuntimeError("当前接口类型不支持多模态图片输入。")


# ---------------------------------------------------------------------------
# OpenAI-compatible client
# ---------------------------------------------------------------------------


class _DummyAsyncResponse:
    """Compatibility shim for the OpenAI SDK's async response."""
    pass


class OpenAICompatibleClient(LLMClient):
    """OpenAI SDK wrapper with response_format auto-degradation."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "",
        timeout_seconds: int = 90,
        max_retries: int = 2,
    ) -> None:
        super().__init__(api_key, model, base_url, timeout_seconds, max_retries)
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("未安装 openai 包，请先安装 requirements.txt。") from exc
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": timeout_seconds,
            "max_retries": max_retries,
        }
        if base_url.strip():
            kwargs["base_url"] = base_url.strip()
        self.client = OpenAI(**kwargs)

    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if json_mode and _should_retry_without_response_format(exc):
                kwargs.pop("response_format", None)
                try:
                    response = self.client.chat.completions.create(**kwargs)
                except Exception as retry_exc:
                    raise RuntimeError(
                        _format_ai_exception(retry_exc)
                    ) from retry_exc
            else:
                raise RuntimeError(_format_ai_exception(exc)) from exc
        return response.choices[0].message.content or ""

    def generate_vision_text(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[bytes],
        max_tokens: int = 4096,
    ) -> str:
        content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for image in images:
            encoded = base64.b64encode(image).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded}"},
            })
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
                temperature=0,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            raise RuntimeError(_format_ai_exception(exc)) from exc
        return response.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Anthropic Messages client
# ---------------------------------------------------------------------------


class AnthropicMessagesClient(LLMClient):
    """Anthropic Messages API client via httpx."""

    def _url(self) -> str:
        base = self.base_url.strip() or "https://api.anthropic.com/v1/messages"
        base = base.rstrip("/")
        if base.endswith("/v1/messages") or base.endswith("/messages"):
            return base
        if base.endswith("/v1"):
            return f"{base}/messages"
        return f"{base}/v1/messages"

    def _headers(self) -> dict[str, str]:
        return {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "authorization": f"Bearer {self.api_key}",
            "anthropic-version": "2023-06-01",
        }

    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(
                        self._url(), headers=self._headers(), json=payload
                    )
                    response.raise_for_status()
                    data = response.json()
                return _anthropic_text(data)
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(min(1.5 * (attempt + 1), 4))
                    continue
        raise RuntimeError(
            _format_ai_exception(last_error or RuntimeError("Anthropic 请求失败。"))
        )

    def generate_vision_text(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[bytes],
        max_tokens: int = 4096,
    ) -> str:
        content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for image in images:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(image).decode("ascii"),
                },
            })
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": content}],
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(
                        self._url(), headers=self._headers(), json=payload
                    )
                    response.raise_for_status()
                    data = response.json()
                return _anthropic_text(data)
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(min(1.5 * (attempt + 1), 4))
                    continue
        raise RuntimeError(
            _format_ai_exception(
                last_error or RuntimeError("Anthropic 多模态请求失败。")
            )
        )


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------


def create_llm_client(
    provider: str,
    api_key: str,
    model: str,
    base_url: str = "",
    timeout_seconds: int = 90,
    max_retries: int = 2,
) -> LLMClient:
    """Factory: returns the correct LLM client based on provider type."""
    provider_lower = provider.strip().lower()
    if provider_lower == "anthropic messages" or provider_lower.startswith("anthropic"):
        return AnthropicMessagesClient(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
    return OpenAICompatibleClient(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )
