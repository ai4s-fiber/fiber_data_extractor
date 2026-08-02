"""Authenticated client for the New Materials Big Data Center batch API."""

from __future__ import annotations

import asyncio
import hashlib
import math
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


class PlatformClientError(RuntimeError):
    """Raised when the target platform rejects or cannot complete a request."""


def normalize_platform_base_url(value: str) -> str:
    parsed = urlsplit((value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PlatformClientError("平台地址必须是有效的 HTTP(S) URL")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise PlatformClientError("平台地址不能包含账号、查询参数或片段")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _response_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        for key in ("msg", "message", "error", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return fallback


def _is_success_code(value: Any) -> bool:
    return value in (None, 0, 200, "0", "200")


def _extract_token(payload: dict[str, Any]) -> str:
    direct = payload.get("token")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    data = payload.get("data")
    if isinstance(data, dict):
        nested = data.get("token") or data.get("access_token")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    raise PlatformClientError("平台登录成功响应中没有 token")


def _extract_file_id(payload: dict[str, Any]) -> str:
    data = payload.get("data")
    if isinstance(data, dict):
        value = data.get("fileId") or data.get("file_id")
        if value is not None and str(value).strip():
            return str(value).strip()
    raise PlatformClientError("平台分片上传响应中没有 fileId")


class PlatformApiClient:
    """Small fail-closed wrapper around the platform's observed HTTP protocol."""

    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        request_timeout: float = 60.0,
    ) -> None:
        self.base_url = normalize_platform_base_url(base_url)
        parsed = urlsplit(self.base_url)
        self.api_origin = urlunsplit(
            (parsed.scheme, parsed.netloc, "", "", "")
        )
        self.transport = transport
        self.request_timeout = request_timeout

    def _url(self, path: str) -> str:
        # The UI is deployed below /database-code, while its Axios client uses
        # the absolute baseURL "/dynamics". Preserve that observed split.
        return f"{self.api_origin}/dynamics/{path.lstrip('/')}"

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        token: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}) or {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = await client.request(
                method,
                self._url(path),
                headers=headers,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise PlatformClientError(f"平台连接失败: {exc}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise PlatformClientError(
                f"平台返回了非 JSON 响应 (HTTP {response.status_code})"
            ) from exc
        if not isinstance(payload, dict):
            raise PlatformClientError("平台响应根节点不是 JSON 对象")
        if response.status_code >= 400:
            raise PlatformClientError(
                _response_message(payload, f"平台 HTTP {response.status_code}")
            )
        if not _is_success_code(payload.get("code")):
            raise PlatformClientError(
                _response_message(payload, f"平台业务错误 {payload.get('code')}")
            )
        return payload

    def _client(self, *, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout or self.request_timeout,
            transport=self.transport,
            trust_env=False,
            follow_redirects=False,
        )

    async def get_captcha(self) -> dict[str, Any]:
        async with self._client(timeout=20.0) as client:
            payload = await self._request_json(
                client,
                "GET",
                "captchaImage",
                params={"item": int(time.time() * 1000)},
            )
        image = payload.get("img")
        uuid = payload.get("uuid")
        data = payload.get("data")
        if isinstance(data, dict):
            image = image or data.get("img")
            uuid = uuid or data.get("uuid")
        return {
            "captcha_enabled": bool(payload.get("captchaEnabled", bool(image))),
            "image_base64": image if isinstance(image, str) else "",
            "uuid": uuid if isinstance(uuid, str) else "",
        }

    async def login(
        self,
        *,
        username: str,
        password: str,
        captcha_code: str,
        captcha_uuid: str,
    ) -> tuple[str, dict[str, Any]]:
        password_sha256 = hashlib.sha256(password.encode("utf-8")).hexdigest()
        body = {
            "username": username,
            "password": password_sha256,
            "code": captcha_code,
            "uuid": captcha_uuid,
        }
        async with self._client() as client:
            payload = await self._request_json(
                client,
                "POST",
                "login",
                json=body,
                headers={"Content-Type": "application/json;charset=utf-8"},
            )
            token = _extract_token(payload)
            info = await self._request_json(
                client,
                "GET",
                "getInfo",
                token=token,
            )
        return token, info

    async def get_info(self, token: str) -> dict[str, Any]:
        async with self._client() as client:
            return await self._request_json(
                client,
                "GET",
                "getInfo",
                token=token,
            )

    async def latest_upload_history(
        self,
        token: str,
        *,
        filename: str,
    ) -> dict[str, Any] | None:
        async with self._client() as client:
            payload = await self._request_json(
                client,
                "GET",
                "fileOperateHistory/list",
                token=token,
                params={
                    "pageNum": 1,
                    "pageSize": 20,
                    "originalFileName": filename,
                    "operateType": "0",
                },
            )
        rows = payload.get("rows")
        if not isinstance(rows, list):
            data = payload.get("data")
            if isinstance(data, dict):
                rows = data.get("rows")
        if not isinstance(rows, list):
            return None
        exact = [
            row
            for row in rows
            if isinstance(row, dict)
            and str(row.get("originalFileName") or "") == filename
        ]
        return exact[0] if exact else None

    async def dataset_records(
        self,
        token: str,
        *,
        dataset_id: int,
        page_size: int = 500,
        max_pages: int = 200,
    ) -> list[dict[str, Any]]:
        """Read all current dataset rows for record-level deduplication."""

        if dataset_id <= 0:
            raise PlatformClientError("平台数据集 ID 必须为正整数")
        if not 1 <= page_size <= 500:
            raise PlatformClientError("平台数据集分页大小必须在 1 到 500 之间")
        if max_pages < 1:
            raise PlatformClientError("平台数据集最大分页数必须大于 0")

        collected: list[dict[str, Any]] = []
        async with self._client() as client:
            for page_number in range(1, max_pages + 1):
                payload = await self._request_json(
                    client,
                    "GET",
                    f"datasetByUser/{dataset_id}",
                    token=token,
                    params={
                        "pageNum": page_number,
                        "pageSize": page_size,
                    },
                )
                rows = payload.get("rows")
                data = payload.get("data")
                if not isinstance(rows, list) and isinstance(data, dict):
                    rows = data.get("rows")
                if not isinstance(rows, list):
                    raise PlatformClientError(
                        "平台数据集查询响应中没有 rows 数组"
                    )
                collected.extend(
                    row for row in rows if isinstance(row, dict)
                )

                total_value = payload.get("total")
                if total_value is None and isinstance(data, dict):
                    total_value = data.get("total")
                try:
                    total = int(total_value)
                except (TypeError, ValueError):
                    total = None
                if (
                    len(rows) < page_size
                    or (total is not None and len(collected) >= total)
                ):
                    return collected

        raise PlatformClientError(
            f"平台数据集记录超过安全分页上限 {page_size * max_pages}"
        )

    @staticmethod
    def history_state(row: dict[str, Any] | None) -> str:
        if not row:
            return "pending"
        operate = str(row.get("operateResult", ""))
        resolve = str(row.get("resolveResult", ""))
        if operate in {"1", "3"} or resolve == "2":
            return "failed"
        if operate == "0" and resolve == "1":
            return "completed"
        return "processing"

    async def upload_batch_json(
        self,
        token: str,
        *,
        filename: str,
        content: bytes,
        dataset_id: int,
        template_id: int,
        parse_timeout_seconds: float,
        poll_interval_seconds: float,
        chunk_size: int = 10 * 1024 * 1024,
    ) -> dict[str, Any]:
        """Upload, merge and wait until both upload and parse are confirmed."""
        if not content:
            raise PlatformClientError("平台批量 JSON 不能为空")
        if chunk_size < 1:
            raise PlatformClientError("分片大小必须大于 0")
        total_chunks = max(1, math.ceil(len(content) / chunk_size))
        identifier = hashlib.md5(content, usedforsecurity=False).hexdigest()
        file_id: str | None = None

        async with self._client(timeout=120.0) as client:
            for index in range(total_chunks):
                chunk = content[index * chunk_size : (index + 1) * chunk_size]
                fields = {
                    "chunkNumber": str(index + 1),
                    "chunkSize": str(chunk_size),
                    "currentChunkSize": str(len(chunk)),
                    "fileName": filename,
                    "identifier": identifier,
                    "totalChunks": str(total_chunks),
                    "totalSize": str(len(content)),
                }
                if file_id:
                    fields["fileId"] = file_id
                payload = await self._request_json(
                    client,
                    "POST",
                    "fileUpload/upload",
                    token=token,
                    data=fields,
                    files={
                        "file": (
                            filename,
                            chunk,
                            "application/octet-stream",
                        )
                    },
                )
                returned_file_id = _extract_file_id(payload)
                if file_id and returned_file_id != file_id:
                    raise PlatformClientError("平台在分片间返回了不一致的 fileId")
                file_id = returned_file_id

            assert file_id is not None
            merge_payload = await self._request_json(
                client,
                "POST",
                "fileUpload/merge",
                token=token,
                params={
                    "fileId": file_id,
                    "fileName": filename,
                    "identifier": identifier,
                    "taskType": "dataset",
                    "totalSize": str(len(content)),
                    "templateId": str(template_id),
                    "datasetId": str(dataset_id),
                    # The platform UI always sends sample=0 for content data.
                    # Omitting it leaves the platform's NOT NULL `sample`
                    # column unset and makes every record fail after upload.
                    "sample": "0",
                },
            )

        deadline = time.monotonic() + max(0.0, parse_timeout_seconds)
        latest: dict[str, Any] | None = None
        while True:
            latest = await self.latest_upload_history(token, filename=filename)
            state = self.history_state(latest)
            if state in {"completed", "failed"}:
                return {
                    "status": state,
                    "file_id": file_id,
                    "identifier": identifier,
                    "filename": filename,
                    "merge_response": merge_payload,
                    "history": latest,
                }
            if time.monotonic() >= deadline:
                return {
                    "status": "processing",
                    "file_id": file_id,
                    "identifier": identifier,
                    "filename": filename,
                    "merge_response": merge_payload,
                    "history": latest,
                }
            await asyncio.sleep(max(0.2, poll_interval_seconds))


@dataclass
class PlatformSession:
    project_id: int
    token: str = field(repr=False)
    username: str
    user_info: dict[str, Any]
    expires_at: float


class PlatformSessionStore:
    """Opaque, process-local handles for platform bearer tokens."""

    def __init__(self) -> None:
        self._sessions: dict[str, PlatformSession] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        project_id: int,
        token: str,
        username: str,
        user_info: dict[str, Any],
        ttl_seconds: int,
    ) -> tuple[str, PlatformSession]:
        handle = secrets.token_urlsafe(32)
        session = PlatformSession(
            project_id=project_id,
            token=token,
            username=username,
            user_info=user_info,
            expires_at=time.time() + max(60, ttl_seconds),
        )
        async with self._lock:
            self._purge_expired_locked()
            self._sessions[handle] = session
        return handle, session

    async def get(self, handle: str, *, project_id: int) -> PlatformSession:
        async with self._lock:
            self._purge_expired_locked()
            session = self._sessions.get(handle)
            if session is None or session.project_id != project_id:
                raise PlatformClientError("平台连接已失效，请重新登录")
            return session

    async def revoke(self, handle: str, *, project_id: int) -> None:
        async with self._lock:
            session = self._sessions.get(handle)
            if session and session.project_id == project_id:
                self._sessions.pop(handle, None)

    def _purge_expired_locked(self) -> None:
        now = time.time()
        expired = [
            handle
            for handle, session in self._sessions.items()
            if session.expires_at <= now
        ]
        for handle in expired:
            self._sessions.pop(handle, None)


platform_session_store = PlatformSessionStore()
