"""Async MinerU service client supporting both local and cloud extraction."""

from __future__ import annotations

import asyncio
import io
import inspect
import json
import random
import time
import uuid
import zipfile
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiofiles
import httpx

from app.core.config import settings


class MinerUError(RuntimeError):
    """Base error for MinerU integration failures."""

    error_code = "mineru_error"


class MinerUUnavailable(MinerUError):
    error_code = "mineru_unavailable"


class MinerUTaskFailed(MinerUError):
    error_code = "mineru_task_failed"


class MinerUTimeout(MinerUError):
    error_code = "mineru_timeout"


class MinerUInvalidResult(MinerUError):
    error_code = "mineru_invalid_result"


@dataclass(slots=True)
class MinerUParseResult:
    task_id: str
    backend: str
    version: str | None
    document_name: str
    md_content: str
    content_list: list[dict[str, Any]]
    content_list_v2: list[dict[str, Any]]
    middle_json: dict[str, Any]
    raw_result: dict[str, Any]
    elapsed_seconds: float


@dataclass(slots=True)
class MinerUCloudBatchOutcome:
    """One terminal item from a MinerU Cloud batch."""

    path: Path
    data_id: str
    batch_id: str
    result: MinerUParseResult | None = None
    error: MinerUError | None = None

    @property
    def ok(self) -> bool:
        return self.result is not None and self.error is None


MINERU_CLOUD_BATCH_UPLOAD_URL = "https://mineru.net/api/v4/file-urls/batch"
MINERU_CLOUD_BATCH_RESULTS_URL = "https://mineru.net/api/v4/extract-results/batch"
MINERU_CLOUD_MAX_FILES_PER_BATCH = 200
_RETRYABLE_CLOUD_CODES = {-10001, -60007, -60009}
_RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


def _base_url() -> str:
    return settings.MINERU_API_URL.rstrip("/")


def _loads_json_field(value: Any, *, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _first_result(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    results = payload.get("results")
    if not isinstance(results, dict) or not results:
        raise MinerUInvalidResult("MinerU result payload does not contain results")
    name, data = next(iter(results.items()))
    if not isinstance(data, dict):
        raise MinerUInvalidResult("MinerU result entry is not an object")
    return str(name), data


def extract_mineru_zip(zip_bytes: bytes) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    md_content = ""
    content_list = []
    content_list_v2 = []
    middle_json = {}

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for name in z.namelist():
            if name.endswith("/"):
                continue
            if name.endswith(".md"):
                md_content = z.read(name).decode("utf-8", errors="ignore")
            elif "content_list_v2.json" in name:
                try:
                    content_list_v2 = json.loads(z.read(name).decode("utf-8", errors="ignore"))
                except Exception:
                    pass
            elif "content_list.json" in name:
                try:
                    content_list = json.loads(z.read(name).decode("utf-8", errors="ignore"))
                except Exception:
                    pass
            elif "middle.json" in name:
                try:
                    middle_json = json.loads(z.read(name).decode("utf-8", errors="ignore"))
                except Exception:
                    pass

    return md_content, content_list, content_list_v2, middle_json


def build_cloud_batch_upload_payload(
    paths: Sequence[str | Path],
    data_ids: Sequence[str],
) -> dict[str, Any]:
    if len(paths) != len(data_ids):
        raise ValueError("MinerU Cloud paths and data_ids must have the same length")
    if not paths:
        raise ValueError("MinerU Cloud batch cannot be empty")
    if len(paths) > MINERU_CLOUD_MAX_FILES_PER_BATCH:
        raise ValueError(
            f"MinerU Cloud accepts at most {MINERU_CLOUD_MAX_FILES_PER_BATCH} files per batch"
        )

    files: list[dict[str, Any]] = []
    for raw_path, data_id in zip(paths, data_ids, strict=True):
        file_payload: dict[str, Any] = {
            "name": Path(raw_path).name,
            "data_id": data_id,
        }
        if settings.MINERU_CLOUD_PAGE_RANGES.strip():
            file_payload["page_ranges"] = settings.MINERU_CLOUD_PAGE_RANGES.strip()
        if settings.MINERU_CLOUD_IS_OCR:
            file_payload["is_ocr"] = True
        files.append(file_payload)

    return {
        "files": files,
        "model_version": settings.MINERU_CLOUD_MODEL_VERSION,
        "language": settings.MINERU_LANG,
        "enable_formula": settings.MINERU_CLOUD_ENABLE_FORMULA,
        "enable_table": settings.MINERU_CLOUD_ENABLE_TABLE,
    }


def build_cloud_upload_payload(path: str | Path, data_id: str) -> dict[str, Any]:
    return build_cloud_batch_upload_payload([path], [data_id])


async def _iter_file_bytes(path: Path, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
    async with aiofiles.open(path, "rb") as source:
        while True:
            chunk = await source.read(chunk_size)
            if not chunk:
                return
            yield chunk


def _retry_after_seconds(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        raw = response.headers.get("retry-after", "").strip()
        try:
            if raw:
                return min(60.0, max(0.0, float(raw)))
        except ValueError:
            pass
    base = max(0.1, float(settings.MINERU_CLOUD_RETRY_BASE_SECONDS or 0.1))
    return min(60.0, base * (2**attempt) + random.uniform(0.0, base * 0.25))


def _bool_form(value: bool) -> str:
    return "true" if value else "false"


def build_local_parse_form_data(*, response_format_zip: bool = False) -> dict[str, str]:
    data = {
        "backend": settings.MINERU_BACKEND,
        "parse_method": settings.MINERU_PARSE_METHOD,
        "lang_list": settings.MINERU_LANG,
        "formula_enable": _bool_form(settings.MINERU_FORMULA_ENABLE),
        "table_enable": _bool_form(settings.MINERU_TABLE_ENABLE),
        "image_analysis": _bool_form(settings.MINERU_IMAGE_ANALYSIS_ENABLE),
        "return_md": "true",
        "return_middle_json": "true",
        "return_model_output": "false",
        "return_content_list": "true",
        "return_images": "false",
        "response_format_zip": _bool_form(response_format_zip),
        "return_original_file": "false",
    }
    if "hybrid" in settings.MINERU_BACKEND.lower() and settings.MINERU_HYBRID_EFFORT.strip():
        data["effort"] = settings.MINERU_HYBRID_EFFORT.strip()
    return data


def _looks_like_result_entry(value: dict[str, Any]) -> bool:
    return any(
        key in value
        for key in (
            "md_content",
            "markdown",
            "md",
            "content_list",
            "content_list_v2",
            "middle_json",
        )
    )


def _normalise_local_result_payload(
    payload: dict[str, Any],
    document_name: str,
) -> dict[str, Any]:
    if "results" in payload:
        return payload

    for key in ("data", "result"):
        value = payload.get(key)
        if not isinstance(value, dict):
            continue
        if "results" in value:
            return value
        nested = value.get("result")
        if isinstance(nested, dict):
            if "results" in nested:
                return nested
            if _looks_like_result_entry(nested):
                return {
                    "results": {document_name: nested},
                    "backend": payload.get("backend") or value.get("backend"),
                    "version": payload.get("version") or value.get("version"),
                }
        if _looks_like_result_entry(value):
            return {
                "results": {document_name: value},
                "backend": payload.get("backend") or value.get("backend"),
                "version": payload.get("version") or value.get("version"),
            }

    if _looks_like_result_entry(payload):
        return {
            "results": {document_name: payload},
            "backend": payload.get("backend"),
            "version": payload.get("version"),
        }
    return payload


class MinerUClient:
    """Client for MinerU APIs (supports local /tasks and mineru.net cloud API)."""

    def __init__(
        self,
        api_url: str | None = None,
        token: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_url = (api_url or _base_url()).rstrip("/")
        self.token = token if token is not None else settings.MINERU_CLOUD_TOKEN
        self._transport = transport

    async def parse_pdf(
        self, pdf_path: str | Path, strategy: str = "mineru_local"
    ) -> MinerUParseResult:
        if strategy == "mineru_cloud":
            return await self.parse_pdf_cloud(pdf_path)
        if strategy == "mineru_local":
            return await self.parse_pdf_local(pdf_path)
        if strategy == "mineru_local_sync":
            return await self.parse_pdf_local_sync(pdf_path)
        raise ValueError(f"Unsupported MinerU parser strategy: {strategy}")

    async def parse_pdf_local(self, pdf_path: str | Path) -> MinerUParseResult:
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF file not found: {path}")

        started = time.monotonic()
        timeout = httpx.Timeout(
            connect=30.0,
            read=max(30.0, float(settings.MINERU_TASK_TIMEOUT_SECONDS)),
            write=30.0,
            pool=30.0,
        )

        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            task_id = await self._submit_task(client, path)
            await self._wait_for_task(client, task_id, started)
            result_payload = await self._fetch_result(client, task_id)

        return self._build_local_parse_result(
            result_payload,
            task_id=task_id,
            document_name=path.name,
            started=started,
        )

    def _build_local_parse_result(
        self,
        result_payload: dict[str, Any],
        *,
        task_id: str,
        document_name: str,
        started: float,
    ) -> MinerUParseResult:
        result_payload = _normalise_local_result_payload(result_payload, document_name)
        document_name, data = _first_result(result_payload)
        content_list = _loads_json_field(data.get("content_list"), fallback=[])
        content_list_v2 = _loads_json_field(data.get("content_list_v2"), fallback=[])
        middle_json = _loads_json_field(data.get("middle_json"), fallback={})

        if not isinstance(content_list, list):
            content_list = []
        if not isinstance(content_list_v2, list):
            content_list_v2 = []
        if not isinstance(middle_json, dict):
            middle_json = {}

        md_content = data.get("md_content") or data.get("markdown") or data.get("md") or ""
        if not isinstance(md_content, str):
            md_content = str(md_content)

        if not md_content.strip() and not content_list and not content_list_v2:
            raise MinerUInvalidResult("MinerU returned no usable markdown or content list")

        return MinerUParseResult(
            task_id=task_id,
            backend=str(result_payload.get("backend") or data.get("backend") or settings.MINERU_BACKEND),
            version=result_payload.get("version") or data.get("version"),
            document_name=document_name,
            md_content=md_content,
            content_list=content_list,
            content_list_v2=content_list_v2,
            middle_json=middle_json,
            raw_result=result_payload,
            elapsed_seconds=time.monotonic() - started,
        )

    async def _submit_task(self, client: httpx.AsyncClient, path: Path) -> str:
        try:
            response = await client.post(
                f"{self.api_url}/tasks",
                files={"files": (path.name, path.read_bytes(), "application/pdf")},
                data=build_local_parse_form_data(response_format_zip=False),
            )
        except httpx.HTTPError as exc:
            raise MinerUUnavailable(f"Failed to submit MinerU task: {exc}") from exc

        if response.status_code >= 500:
            raise MinerUUnavailable(
                f"MinerU task submission failed with HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            raise MinerUTaskFailed(
                f"MinerU rejected task submission with HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        payload = response.json()
        task_id = payload.get("task_id")
        if not task_id:
            raise MinerUInvalidResult("MinerU task submission did not return task_id")
        return str(task_id)

    async def parse_pdf_local_sync(self, pdf_path: str | Path) -> MinerUParseResult:
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF file not found: {path}")

        started = time.monotonic()
        timeout = httpx.Timeout(
            connect=30.0,
            read=max(30.0, float(settings.MINERU_TASK_TIMEOUT_SECONDS)),
            write=30.0,
            pool=30.0,
        )
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            try:
                response = await client.post(
                    f"{self.api_url}/file_parse",
                    files={"files": (path.name, path.read_bytes(), "application/pdf")},
                    data=build_local_parse_form_data(response_format_zip=False),
                )
            except httpx.HTTPError as exc:
                raise MinerUUnavailable(f"Failed to run MinerU synchronous parse: {exc}") from exc

        if response.status_code >= 500:
            raise MinerUUnavailable(
                f"MinerU synchronous parse failed with HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            raise MinerUTaskFailed(
                f"MinerU rejected synchronous parse with HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        content_type = response.headers.get("content-type", "").lower()
        if "zip" in content_type or response.content[:2] == b"PK":
            md_content, content_list, content_list_v2, middle_json = extract_mineru_zip(response.content)
            payload = {
                "backend": settings.MINERU_BACKEND,
                "version": None,
                "results": {
                    path.name: {
                        "md_content": md_content,
                        "content_list": content_list,
                        "content_list_v2": content_list_v2,
                        "middle_json": middle_json,
                    }
                },
                "response_format": "zip",
            }
        else:
            try:
                payload = response.json()
            except ValueError as exc:
                raise MinerUInvalidResult("MinerU synchronous parse did not return JSON or ZIP") from exc
            if not isinstance(payload, dict):
                raise MinerUInvalidResult("MinerU synchronous parse response is not an object")

        return self._build_local_parse_result(
            payload,
            task_id=str(payload.get("task_id") or "file_parse"),
            document_name=path.name,
            started=started,
        )

    async def _wait_for_task(
        self,
        client: httpx.AsyncClient,
        task_id: str,
        started: float,
    ) -> None:
        while True:
            if time.monotonic() - started > settings.MINERU_TASK_TIMEOUT_SECONDS:
                raise MinerUTimeout(
                    f"MinerU task timed out after {settings.MINERU_TASK_TIMEOUT_SECONDS}s"
                )

            try:
                response = await client.get(f"{self.api_url}/tasks/{task_id}")
            except httpx.HTTPError as exc:
                raise MinerUUnavailable(f"Failed to poll MinerU task: {exc}") from exc

            if response.status_code >= 500:
                raise MinerUUnavailable(
                    f"MinerU status polling failed with HTTP {response.status_code}"
                )
            if response.status_code >= 400:
                raise MinerUTaskFailed(
                    f"MinerU status polling failed with HTTP {response.status_code}: "
                    f"{response.text[:500]}"
                )

            payload = response.json()
            status = str(payload.get("status") or "").lower()
            if status in {"completed", "done", "success", "succeeded"}:
                return
            if status in {"failed", "error"}:
                message = payload.get("error") or payload.get("message") or "MinerU task failed"
                raise MinerUTaskFailed(str(message))

            await asyncio.sleep(max(0.2, float(settings.MINERU_POLL_INTERVAL_SECONDS)))

    async def _fetch_result(
        self,
        client: httpx.AsyncClient,
        task_id: str,
    ) -> dict[str, Any]:
        try:
            response = await client.get(f"{self.api_url}/tasks/{task_id}/result")
        except httpx.HTTPError as exc:
            raise MinerUUnavailable(f"Failed to fetch MinerU result: {exc}") from exc

        if response.status_code == 202:
            raise MinerUInvalidResult("MinerU result is not ready after completed status")
        if response.status_code >= 500:
            raise MinerUUnavailable(
                f"MinerU result fetch failed with HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            raise MinerUTaskFailed(
                f"MinerU result fetch failed with HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        payload = response.json()
        if not isinstance(payload, dict):
            raise MinerUInvalidResult("MinerU result response is not an object")
        return payload

    async def parse_pdf_cloud(self, pdf_path: str | Path) -> MinerUParseResult:
        outcome = None
        async for item in self.iter_parse_pdfs_cloud_batch([pdf_path]):
            outcome = item
        if outcome is None:
            raise MinerUInvalidResult("MinerU Cloud batch returned no outcome")
        if outcome.error is not None:
            raise outcome.error
        if outcome.result is None:
            raise MinerUInvalidResult("MinerU Cloud batch returned no parse result")
        return outcome.result

    async def _request_cloud_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        operation: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        retries = max(0, int(settings.MINERU_CLOUD_MAX_RETRIES or 0))
        last_error: BaseException | None = None
        for attempt in range(retries + 1):
            response = None
            try:
                response = await client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code == 200:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise MinerUInvalidResult(
                            f"MinerU Cloud {operation} returned invalid JSON"
                        ) from exc
                    if not isinstance(payload, dict):
                        raise MinerUInvalidResult(
                            f"MinerU Cloud {operation} response is not an object"
                        )
                    code = payload.get("code")
                    if code == 0:
                        return payload
                    message = str(payload.get("msg") or "unknown API error")
                    try:
                        numeric_code = int(code)
                    except (TypeError, ValueError):
                        numeric_code = None
                    if numeric_code not in _RETRYABLE_CLOUD_CODES:
                        raise MinerUTaskFailed(
                            f"MinerU Cloud {operation} failed ({code}): {message}"
                        )
                    last_error = MinerUUnavailable(
                        f"MinerU Cloud {operation} temporarily unavailable ({code}): {message}"
                    )
                elif response.status_code not in _RETRYABLE_HTTP_STATUSES:
                    raise MinerUTaskFailed(
                        f"MinerU Cloud {operation} failed with HTTP "
                        f"{response.status_code}: {response.text[:500]}"
                    )
                else:
                    last_error = MinerUUnavailable(
                        f"MinerU Cloud {operation} failed with HTTP {response.status_code}"
                    )

            if attempt >= retries:
                break
            await asyncio.sleep(_retry_after_seconds(response, attempt))

        if isinstance(last_error, MinerUError):
            raise last_error
        raise MinerUUnavailable(f"MinerU Cloud {operation} failed: {last_error}") from last_error

    async def _request_cloud_binary(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        operation: str,
    ) -> bytes:
        retries = max(0, int(settings.MINERU_CLOUD_MAX_RETRIES or 0))
        last_error: BaseException | None = None
        for attempt in range(retries + 1):
            response = None
            try:
                response = await client.get(url)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code == 200:
                    return response.content
                if response.status_code not in _RETRYABLE_HTTP_STATUSES:
                    raise MinerUTaskFailed(
                        f"MinerU Cloud {operation} failed with HTTP {response.status_code}"
                    )
                last_error = MinerUUnavailable(
                    f"MinerU Cloud {operation} failed with HTTP {response.status_code}"
                )

            if attempt >= retries:
                break
            await asyncio.sleep(_retry_after_seconds(response, attempt))

        if isinstance(last_error, MinerUError):
            raise last_error
        raise MinerUUnavailable(f"MinerU Cloud {operation} failed: {last_error}") from last_error

    async def _upload_cloud_file(
        self,
        client: httpx.AsyncClient,
        path: Path,
        upload_url: str,
    ) -> None:
        retries = max(0, int(settings.MINERU_CLOUD_MAX_RETRIES or 0))
        last_error: BaseException | None = None
        for attempt in range(retries + 1):
            response = None
            try:
                response = await client.put(
                    upload_url,
                    content=_iter_file_bytes(path),
                    headers={"Content-Length": str(path.stat().st_size)},
                )
            except (httpx.HTTPError, OSError) as exc:
                last_error = exc
            else:
                if response.status_code in {200, 201, 204}:
                    return
                if response.status_code not in _RETRYABLE_HTTP_STATUSES:
                    raise MinerUTaskFailed(
                        f"MinerU Cloud file upload failed with HTTP {response.status_code}"
                    )
                last_error = MinerUUnavailable(
                    f"MinerU Cloud file upload failed with HTTP {response.status_code}"
                )

            if attempt >= retries:
                break
            await asyncio.sleep(_retry_after_seconds(response, attempt))

        if isinstance(last_error, MinerUError):
            raise last_error
        raise MinerUUnavailable(f"MinerU Cloud file upload failed: {last_error}") from last_error

    async def _download_cloud_parse_result(
        self,
        client: httpx.AsyncClient,
        *,
        path: Path,
        batch_id: str,
        file_result: dict[str, Any],
        started: float,
    ) -> MinerUParseResult:
        zip_url = str(file_result.get("full_zip_url") or "").strip()
        if not zip_url:
            raise MinerUInvalidResult(
                f"MinerU Cloud returned no ZIP URL for {path.name}"
            )
        zip_bytes = await self._request_cloud_binary(
            client,
            zip_url,
            operation=f"result download for {path.name}",
        )
        try:
            md_content, content_list, content_list_v2, middle_json = extract_mineru_zip(
                zip_bytes
            )
        except (OSError, zipfile.BadZipFile) as exc:
            raise MinerUInvalidResult(
                f"MinerU Cloud returned an invalid ZIP for {path.name}"
            ) from exc
        if not md_content.strip() and not content_list and not content_list_v2:
            raise MinerUInvalidResult(
                f"MinerU Cloud returned no usable content for {path.name}"
            )
        return MinerUParseResult(
            task_id=batch_id,
            backend=settings.MINERU_CLOUD_MODEL_VERSION,
            version="cloud_v4",
            document_name=str(file_result.get("file_name") or path.name),
            md_content=md_content,
            content_list=content_list,
            content_list_v2=content_list_v2,
            middle_json=middle_json,
            raw_result={
                "batch_id": batch_id,
                "extract_result": [file_result],
            },
            elapsed_seconds=time.monotonic() - started,
        )

    async def _iter_cloud_batch_results(
        self,
        client: httpx.AsyncClient,
        *,
        batch_id: str,
        path_by_data_id: dict[str, Path],
        started: float,
    ) -> AsyncIterator[MinerUCloudBatchOutcome]:
        pending = set(path_by_data_id)
        result_url = f"{MINERU_CLOUD_BATCH_RESULTS_URL}/{batch_id}"
        poll_headers = {"Authorization": f"Bearer {self.token}"}
        while pending:
            if time.monotonic() - started > settings.MINERU_TASK_TIMEOUT_SECONDS:
                for data_id in sorted(pending):
                    yield MinerUCloudBatchOutcome(
                        path=path_by_data_id[data_id],
                        data_id=data_id,
                        batch_id=batch_id,
                        error=MinerUTimeout(
                            "MinerU Cloud batch timed out after "
                            f"{settings.MINERU_TASK_TIMEOUT_SECONDS}s"
                        ),
                    )
                return

            status_payload = await self._request_cloud_json(
                client,
                "GET",
                result_url,
                operation="batch status polling",
                headers=poll_headers,
            )
            status_data = status_payload.get("data")
            items = (
                status_data.get("extract_result", [])
                if isinstance(status_data, dict)
                else []
            )
            if not isinstance(items, list):
                raise MinerUInvalidResult(
                    "MinerU Cloud batch status has no extract_result list"
                )
            by_data_id = {
                str(item.get("data_id")): item
                for item in items
                if isinstance(item, dict) and item.get("data_id")
            }
            filename_counts: dict[str, int] = {}
            by_filename: dict[str, dict[str, Any]] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                filename = str(item.get("file_name") or "")
                if not filename:
                    continue
                filename_counts[filename] = filename_counts.get(filename, 0) + 1
                by_filename[filename] = item

            terminal: list[tuple[str, dict[str, Any]]] = []
            for data_id in tuple(pending):
                path = path_by_data_id[data_id]
                item = by_data_id.get(data_id)
                if item is None and filename_counts.get(path.name) == 1:
                    item = by_filename.get(path.name)
                if item is None:
                    continue
                state = str(item.get("state") or "").lower()
                if state in {"done", "failed"}:
                    terminal.append((data_id, item))

            async def finish_one(
                data_id: str,
                item: dict[str, Any],
            ) -> MinerUCloudBatchOutcome:
                path = path_by_data_id[data_id]
                if str(item.get("state") or "").lower() == "failed":
                    return MinerUCloudBatchOutcome(
                        path=path,
                        data_id=data_id,
                        batch_id=batch_id,
                        error=MinerUTaskFailed(
                            str(item.get("err_msg") or "MinerU Cloud task failed")
                        ),
                    )
                try:
                    result = await self._download_cloud_parse_result(
                        client,
                        path=path,
                        batch_id=batch_id,
                        file_result=item,
                        started=started,
                    )
                except MinerUError as exc:
                    return MinerUCloudBatchOutcome(
                        path=path,
                        data_id=data_id,
                        batch_id=batch_id,
                        error=exc,
                    )
                return MinerUCloudBatchOutcome(
                    path=path,
                    data_id=data_id,
                    batch_id=batch_id,
                    result=result,
                )

            if terminal:
                outcomes = await asyncio.gather(
                    *(finish_one(data_id, item) for data_id, item in terminal)
                )
                for outcome in outcomes:
                    pending.discard(outcome.data_id)
                    yield outcome
                continue

            await asyncio.sleep(
                max(1.0, float(settings.MINERU_POLL_INTERVAL_SECONDS))
            )

    async def iter_existing_cloud_batch(
        self,
        batch_id: str,
        path_by_data_id: dict[str, str | Path],
    ) -> AsyncIterator[MinerUCloudBatchOutcome]:
        """Resume polling a previously uploaded MinerU Cloud batch."""
        if not batch_id.strip() or not path_by_data_id:
            raise ValueError("batch_id and path_by_data_id are required")
        if not (self.token or "").strip():
            raise MinerUUnavailable(
                "MINERU_CLOUD_TOKEN is required when parser_strategy=mineru_cloud"
            )
        resolved = {
            str(data_id): Path(path).resolve()
            for data_id, path in path_by_data_id.items()
        }
        timeout = httpx.Timeout(
            connect=30.0,
            read=max(30.0, float(settings.MINERU_TASK_TIMEOUT_SECONDS)),
            write=max(30.0, float(settings.MINERU_TASK_TIMEOUT_SECONDS)),
            pool=30.0,
        )
        client_kwargs: dict[str, Any] = {
            "timeout": timeout,
            "trust_env": settings.MINERU_CLOUD_TRUST_ENV,
        }
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        async with httpx.AsyncClient(**client_kwargs) as client:
            async for outcome in self._iter_cloud_batch_results(
                client,
                batch_id=batch_id.strip(),
                path_by_data_id=resolved,
                started=time.monotonic(),
            ):
                yield outcome

    async def iter_parse_pdfs_cloud_batch(
        self,
        pdf_paths: Sequence[str | Path],
        *,
        upload_concurrency: int | None = None,
        on_submitted: Callable[[str, dict[str, Path]], Any] | None = None,
    ) -> AsyncIterator[MinerUCloudBatchOutcome]:
        """Submit one official MinerU Cloud batch and yield terminal file outcomes."""
        paths = [Path(path).resolve() for path in pdf_paths]
        if not paths:
            return
        if len(paths) > MINERU_CLOUD_MAX_FILES_PER_BATCH:
            raise ValueError(
                f"MinerU Cloud accepts at most {MINERU_CLOUD_MAX_FILES_PER_BATCH} files per batch"
            )
        for path in paths:
            if not path.is_file():
                raise FileNotFoundError(f"PDF file not found: {path}")
        if not (self.token or "").strip():
            raise MinerUUnavailable(
                "MINERU_CLOUD_TOKEN is required when parser_strategy=mineru_cloud"
            )

        data_ids = [uuid.uuid4().hex for _ in paths]
        path_by_data_id = dict(zip(data_ids, paths, strict=True))
        started = time.monotonic()
        timeout = httpx.Timeout(
            connect=30.0,
            read=max(30.0, float(settings.MINERU_TASK_TIMEOUT_SECONDS)),
            write=max(30.0, float(settings.MINERU_TASK_TIMEOUT_SECONDS)),
            pool=30.0,
        )
        auth_headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        client_kwargs: dict[str, Any] = {
            "timeout": timeout,
            "trust_env": settings.MINERU_CLOUD_TRUST_ENV,
        }
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        async with httpx.AsyncClient(**client_kwargs) as client:
            payload = build_cloud_batch_upload_payload(paths, data_ids)
            submission = await self._request_cloud_json(
                client,
                "POST",
                MINERU_CLOUD_BATCH_UPLOAD_URL,
                operation="batch upload URL request",
                headers=auth_headers,
                json=payload,
            )
            data = submission.get("data")
            if not isinstance(data, dict):
                raise MinerUInvalidResult("MinerU Cloud batch response has no data object")
            batch_id = str(data.get("batch_id") or "").strip()
            upload_urls = data.get("file_urls")
            if not batch_id or not isinstance(upload_urls, list):
                raise MinerUInvalidResult(
                    "MinerU Cloud batch response has no batch_id or upload URLs"
                )
            if len(upload_urls) != len(paths):
                raise MinerUInvalidResult(
                    "MinerU Cloud returned a different number of upload URLs"
                )

            limit = max(
                1,
                int(
                    upload_concurrency
                    or settings.MINERU_CLOUD_UPLOAD_CONCURRENCY
                    or 1
                ),
            )
            semaphore = asyncio.Semaphore(min(limit, len(paths)))

            async def upload_one(
                path: Path,
                data_id: str,
                upload_url: str,
            ) -> MinerUCloudBatchOutcome | None:
                try:
                    async with semaphore:
                        await self._upload_cloud_file(client, path, upload_url)
                except MinerUError as exc:
                    return MinerUCloudBatchOutcome(
                        path=path,
                        data_id=data_id,
                        batch_id=batch_id,
                        error=exc,
                    )
                return None

            upload_outcomes = await asyncio.gather(
                *(
                    upload_one(path, data_id, str(upload_url))
                    for path, data_id, upload_url in zip(
                        paths, data_ids, upload_urls, strict=True
                    )
                )
            )
            pending = set(data_ids)
            for outcome in upload_outcomes:
                if outcome is not None:
                    pending.discard(outcome.data_id)
                    yield outcome
            resumable = {
                data_id: path_by_data_id[data_id]
                for data_id in pending
            }
            if resumable and on_submitted is not None:
                callback_result = on_submitted(batch_id, dict(resumable))
                if inspect.isawaitable(callback_result):
                    await callback_result

            async for outcome in self._iter_cloud_batch_results(
                client,
                batch_id=batch_id,
                path_by_data_id=resumable,
                started=started,
            ):
                yield outcome
