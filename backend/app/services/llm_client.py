"""LLM Client abstraction layer migrated from V5.

Provides:
- OpenAI-compatible API via the openai SDK (with response_format=json_object)
- Anthropic Messages API via httpx (with content block parsing)
- Multi-layer JSON recovery (strict parsing, json-repair, regex KV)
- Automatic retry with exponential backoff
- Graceful fallback when response_format is unsupported (e.g. DeepSeek)
- Vision/multimodal support for both providers
- Connection test utility
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import inspect
import json
import logging
import random
import re
import time
import weakref
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from json_repair import repair_json

from app.core.config import settings
from app.services.llm_budget import openai_compatible_extra_body

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OpenAI-compatible gateway retry coordination
# ---------------------------------------------------------------------------


_RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass(slots=True)
class _GatewayBackoffState:
    cooldown_until: float = 0.0
    consecutive_failures: int = 0


_gateway_states: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[str, _GatewayBackoffState]
] = weakref.WeakKeyDictionary()


def _gateway_key(base_url: str) -> str:
    normalized = (base_url or "").strip().rstrip("/").lower()
    return normalized or "openai-default"


def _gateway_state(base_url: str) -> _GatewayBackoffState:
    loop = asyncio.get_running_loop()
    states = _gateway_states.setdefault(loop, {})
    return states.setdefault(_gateway_key(base_url), _GatewayBackoffState())


def _exception_status_code(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _is_retryable_ai_exception(exc: Exception) -> bool:
    status = _exception_status_code(exc)
    if status is not None:
        return status in _RETRYABLE_STATUS_CODES

    chain = _exception_chain(exc)
    if any(isinstance(item, httpx.ReadTimeout) for item in chain):
        # A long generation timeout should be handled by the stage's smaller-window
        # fallback. Replaying the same paid request usually wastes time and tokens.
        return False
    retryable_types = (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.PoolTimeout,
        httpx.RemoteProtocolError,
    )
    if any(isinstance(item, retryable_types) for item in chain):
        return True

    names = {item.__class__.__name__.lower() for item in chain}
    if names & {
        "apiconnectionerror",
        "ratelimiterror",
        "internalservererror",
    }:
        return True
    message = " ".join(str(item).lower() for item in chain)
    return any(
        marker in message
        for marker in (
            "connection reset",
            "connection refused",
            "connection error",
            "server disconnected",
            "remote protocol error",
            "bad gateway",
            "service unavailable",
            "too many requests",
        )
    )


def _retry_after_seconds(exc: Exception, attempt: int) -> float:
    configured_max = max(0.1, float(settings.LLM_RETRY_MAX_SECONDS or 20.0))
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        retry_after_ms = str(headers.get("retry-after-ms", "")).strip()
        if retry_after_ms:
            try:
                return min(configured_max, max(0.0, float(retry_after_ms) / 1000.0))
            except ValueError:
                pass
        retry_after = str(headers.get("retry-after", "")).strip()
        if retry_after:
            try:
                return min(configured_max, max(0.0, float(retry_after)))
            except ValueError:
                try:
                    target = parsedate_to_datetime(retry_after)
                    if target.tzinfo is None:
                        target = target.replace(tzinfo=timezone.utc)
                    seconds = (target - datetime.now(timezone.utc)).total_seconds()
                    return min(configured_max, max(0.0, seconds))
                except (TypeError, ValueError, OverflowError):
                    pass

    base = max(0.1, float(settings.LLM_RETRY_BASE_SECONDS or 1.0))
    exponential = min(configured_max, base * (2 ** max(0, attempt)))
    return min(configured_max, exponential * random.uniform(0.85, 1.25))


async def _wait_for_gateway_cooldown(base_url: str) -> None:
    state = _gateway_state(base_url)
    while True:
        remaining = state.cooldown_until - time.monotonic()
        if remaining <= 0:
            return
        await asyncio.sleep(remaining + random.uniform(0.0, min(0.35, remaining * 0.1)))


def _record_gateway_failure(base_url: str, delay: float) -> float:
    state = _gateway_state(base_url)
    state.consecutive_failures = min(10, state.consecutive_failures + 1)
    configured_max = max(0.1, float(settings.LLM_RETRY_MAX_SECONDS or 20.0))
    pressure_factor = 1.0 + min(3, state.consecutive_failures - 1) * 0.5
    effective_delay = min(configured_max, max(0.0, delay) * pressure_factor)
    state.cooldown_until = max(
        state.cooldown_until,
        time.monotonic() + effective_delay,
    )
    return effective_delay


def _record_gateway_success(base_url: str) -> None:
    state = _gateway_state(base_url)
    state.consecutive_failures = max(0, state.consecutive_failures - 1)


# ---------------------------------------------------------------------------
# JSON recovery utilities
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

    # Layer 3: repair malformed or truncated LLM JSON with a dedicated parser.
    try:
        repaired = repair_json(
            cleaned,
            return_objects=True,
            skip_json_loads=True,
            ensure_ascii=False,
        )
        if isinstance(repaired, dict) and repaired:
            return repaired
        if isinstance(repaired, list) and repaired:
            return {"_items": repaired}
    except Exception:
        pass

    # Layer 4: regex key-value extraction
    kv = _regex_extract_items(cleaned)
    if kv:
        return kv

    # Layer 5: return raw text
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
            "response format",
        )
    )


def _should_retry_without_reasoning_effort(exc: Exception) -> bool:
    message = str(exc).lower()
    return "reasoning_effort" in message or "reasoning effort" in message


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


def _usage_to_dict(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    values: dict[str, int] = {}
    for source, target in (
        ("prompt_tokens", "prompt_tokens"),
        ("completion_tokens", "completion_tokens"),
        ("total_tokens", "total_tokens"),
        ("input_tokens", "prompt_tokens"),
        ("output_tokens", "completion_tokens"),
    ):
        value = usage.get(source) if isinstance(usage, dict) else getattr(usage, source, None)
        if isinstance(value, int):
            values[target] = value
    return values


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
        self._last_usage_var: contextvars.ContextVar[dict[str, int]] = (
            contextvars.ContextVar(f"llm_usage_{id(self)}", default={})
        )

    @property
    def last_usage(self) -> dict[str, int]:
        return dict(self._last_usage_var.get())

    @last_usage.setter
    def last_usage(self, value: dict[str, int]) -> None:
        self._last_usage_var.set(dict(value or {}))

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
        """Tolerate malformed JSON while preserving upstream request failures."""
        raw = self.generate_text(
            system_prompt, user_prompt, max_tokens=max_tokens, json_mode=True
        )
        parsed = _tolerant_parse_json(raw)
        return parsed, raw

    async def agenerate_json_tolerant(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        request_timeout_seconds: float | None = None,
    ) -> tuple[dict[str, Any], str]:
        del reasoning_effort, request_timeout_seconds
        # Not every provider exposes reasoning controls or per-request timeouts.
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
        request_timeout_seconds: float | None = None,
    ) -> tuple[dict[str, Any], str]:
        del request_timeout_seconds
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

    async def aclose(self) -> None:
        """Release async network resources held by this client."""


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
            from openai import AsyncOpenAI, OpenAI
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
        self._async_client_factory = AsyncOpenAI
        self._async_client_kwargs = {**kwargs, "max_retries": 0}
        self._async_client: Any | None = None
        self._async_client_loop: asyncio.AbstractEventLoop | None = None
        self.extra_body = openai_compatible_extra_body(
            model=model,
            base_url=base_url,
            disable_thinking=settings.LLM_DISABLE_THINKING,
        )
        self._response_format_supported: bool | None = None
        self._reasoning_effort_supported: bool | None = None

    @staticmethod
    def _reasoning_effort(model: str, requested: str | None) -> str | None:
        effort = str(
            requested
            if requested is not None
            else settings.LLM_DEFAULT_REASONING_EFFORT
        ).strip().lower()
        if effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
            return None
        model_name = (model or "").strip().lower()
        if model_name.startswith(("gpt-5", "o1", "o3", "o4")):
            return effort
        return None

    @staticmethod
    def _chat_kwargs(
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        *,
        json_mode: bool,
        extra_body: dict[str, Any] | None,
        reasoning_effort: str | None = None,
        request_timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        if json_mode and "json" not in f"{system_prompt}\n{user_prompt}".lower():
            system_prompt = f"{system_prompt}\nReturn json only."
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if extra_body:
            kwargs["extra_body"] = extra_body
        resolved_effort = OpenAICompatibleClient._reasoning_effort(
            model, reasoning_effort
        )
        if resolved_effort:
            kwargs["reasoning_effort"] = resolved_effort
        if request_timeout_seconds is not None:
            kwargs["timeout"] = max(1.0, float(request_timeout_seconds))
        return kwargs

    def _async_client_for_current_loop(self):
        loop = asyncio.get_running_loop()
        if self._async_client is not None:
            if self._async_client_loop is not loop:
                raise RuntimeError("同一个 LLM 客户端不能跨事件循环复用。")
            return self._async_client
        self._async_client = self._async_client_factory(**self._async_client_kwargs)
        self._async_client_loop = loop
        return self._async_client

    async def _achat_completion(self, kwargs: dict[str, Any]):
        client = self._async_client_for_current_loop()
        max_retries = max(0, int(self.max_retries or 0))
        gateway = _gateway_key(self.base_url)
        for attempt in range(max_retries + 1):
            await _wait_for_gateway_cooldown(self.base_url)
            try:
                response = await client.chat.completions.create(**kwargs)
            except Exception as exc:
                if attempt >= max_retries or not _is_retryable_ai_exception(exc):
                    raise
                delay = _retry_after_seconds(exc, attempt)
                delay = _record_gateway_failure(self.base_url, delay)
                status = _exception_status_code(exc)
                host = urlsplit(gateway).netloc or gateway
                logger.warning(
                    "Transient LLM gateway failure host=%s status=%s "
                    "attempt=%s/%s retry_in=%.2fs",
                    host,
                    status or exc.__class__.__name__,
                    attempt + 1,
                    max_retries + 1,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            _record_gateway_success(self.base_url)
            return response
        raise RuntimeError("LLM retry loop exited unexpectedly")

    async def _achat_completion_compatible(self, kwargs: dict[str, Any]):
        for _ in range(3):
            try:
                return await self._achat_completion(kwargs)
            except Exception as exc:
                if (
                    "reasoning_effort" in kwargs
                    and _should_retry_without_reasoning_effort(exc)
                ):
                    kwargs.pop("reasoning_effort", None)
                    self._reasoning_effort_supported = False
                    continue
                if (
                    "response_format" in kwargs
                    and _should_retry_without_response_format(exc)
                ):
                    kwargs.pop("response_format", None)
                    self._response_format_supported = False
                    continue
                raise
        raise RuntimeError("LLM parameter compatibility fallback was exhausted")

    async def aclose(self) -> None:
        client = self._async_client
        self._async_client = None
        self._async_client_loop = None
        if client is not None:
            close = getattr(client, "close", None) or getattr(client, "aclose", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result
            else:
                exit_method = getattr(client, "__aexit__", None)
                if exit_method is not None:
                    await exit_method(None, None, None)
        sync_close = getattr(self.client, "close", None)
        if sync_close is not None:
            sync_close()

    async def _agenerate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        *,
        json_mode: bool,
        reasoning_effort: str | None = None,
        request_timeout_seconds: float | None = None,
    ) -> str:
        use_response_format = (
            json_mode and self._response_format_supported is not False
        )
        kwargs = self._chat_kwargs(
            self.model,
            system_prompt,
            user_prompt,
            max_tokens,
            json_mode=use_response_format,
            extra_body=self.extra_body,
            reasoning_effort=reasoning_effort,
            request_timeout_seconds=request_timeout_seconds,
        )
        if self._reasoning_effort_supported is False:
            kwargs.pop("reasoning_effort", None)
        try:
            response = await self._achat_completion_compatible(kwargs)
        except Exception as exc:
            raise RuntimeError(_format_ai_exception(exc)) from exc
        else:
            if use_response_format and "response_format" in kwargs:
                self._response_format_supported = True
            if "reasoning_effort" in kwargs:
                self._reasoning_effort_supported = True
        self.last_usage = _usage_to_dict(getattr(response, "usage", None))
        return response.choices[0].message.content or ""

    async def agenerate_json_tolerant(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        request_timeout_seconds: float | None = None,
    ) -> tuple[dict[str, Any], str]:
        raw = await self._agenerate_text(
            system_prompt,
            user_prompt,
            max_tokens,
            json_mode=True,
            reasoning_effort=reasoning_effort,
            request_timeout_seconds=request_timeout_seconds,
        )
        return _tolerant_parse_json(raw), raw

    async def agenerate_vision_json_tolerant(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[bytes],
        max_tokens: int = 4096,
        request_timeout_seconds: float | None = None,
    ) -> tuple[dict[str, Any], str]:
        content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for image in images:
            encoded = base64.b64encode(image).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded}"},
            })
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        if request_timeout_seconds is not None:
            kwargs["timeout"] = max(1.0, float(request_timeout_seconds))
        try:
            response = await self._achat_completion(kwargs)
        except Exception as exc:
            raise RuntimeError(_format_ai_exception(exc)) from exc
        self.last_usage = _usage_to_dict(getattr(response, "usage", None))
        raw = response.choices[0].message.content or ""
        return _tolerant_parse_json(raw), raw

    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
        if json_mode and "json" not in f"{system_prompt}\n{user_prompt}":
            system_prompt = f"{system_prompt}\nReturn json only."
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        use_response_format = (
            json_mode and self._response_format_supported is not False
        )
        if use_response_format:
            kwargs["response_format"] = {"type": "json_object"}
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if use_response_format and _should_retry_without_response_format(exc):
                self._response_format_supported = False
                kwargs.pop("response_format", None)
                try:
                    response = self.client.chat.completions.create(**kwargs)
                except Exception as retry_exc:
                    raise RuntimeError(
                        _format_ai_exception(retry_exc)
                    ) from retry_exc
            else:
                raise RuntimeError(_format_ai_exception(exc)) from exc
        else:
            if use_response_format:
                self._response_format_supported = True
        self.last_usage = _usage_to_dict(getattr(response, "usage", None))
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
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
                "temperature": 0,
                "max_tokens": max_tokens,
            }
            if self.extra_body:
                kwargs["extra_body"] = self.extra_body
            response = self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise RuntimeError(_format_ai_exception(exc)) from exc
        self.last_usage = _usage_to_dict(getattr(response, "usage", None))
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


_active_llm_clients: contextvars.ContextVar[list[LLMClient] | None] = (
    contextvars.ContextVar("active_llm_clients", default=None)
)


@asynccontextmanager
async def llm_client_session():
    """Close all LLM clients created during one extraction pipeline."""
    existing = _active_llm_clients.get()
    if existing is not None:
        yield
        return

    clients: list[LLMClient] = []
    token = _active_llm_clients.set(clients)
    try:
        yield
    finally:
        results = await asyncio.gather(
            *(client.aclose() for client in reversed(clients)),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                logger.warning("Failed to close LLM client cleanly: %s", result)
        _active_llm_clients.reset(token)


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
        client: LLMClient = AnthropicMessagesClient(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
    else:
        client = OpenAICompatibleClient(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
    active = _active_llm_clients.get()
    if active is not None:
        active.append(client)
    return client
