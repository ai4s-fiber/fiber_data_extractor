"""Async MinerU service client supporting both local and cloud extraction."""

from __future__ import annotations

import asyncio
import io
import json
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


class MinerUFallbackRequired(MinerUError):
    error_code = "mineru_failed_need_legacy_fallback"



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


class MinerUClient:
    """Client for MinerU APIs (supports local /tasks and mineru.net cloud API)."""

    def __init__(self, api_url: str | None = None, token: str | None = None) -> None:
        self.api_url = (api_url or _base_url()).rstrip("/")
        self.token = token or settings.MINERU_CLOUD_TOKEN

    async def parse_pdf(self, pdf_path: str | Path, strategy: str = "mineru_local") -> MinerUParseResult:
        if strategy == "mineru_cloud":
            return await self.parse_pdf_cloud(pdf_path)
        else:
            return await self.parse_pdf_local(pdf_path)

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

        md_content = data.get("md_content") or ""
        if not isinstance(md_content, str):
            md_content = str(md_content)

        if not md_content.strip() and not content_list and not content_list_v2:
            raise MinerUInvalidResult("MinerU returned no usable markdown or content list")

        return MinerUParseResult(
            task_id=task_id,
            backend=str(result_payload.get("backend") or settings.MINERU_BACKEND),
            version=result_payload.get("version"),
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
                data={
                    "backend": settings.MINERU_BACKEND,
                    "parse_method": settings.MINERU_PARSE_METHOD,
                    "lang_list": settings.MINERU_LANG,
                    "formula_enable": "true",
                    "table_enable": "true",
                    "image_analysis": "true",
                    "return_md": "true",
                    "return_middle_json": "true",
                    "return_model_output": "false",
                    "return_content_list": "true",
                    "return_images": "false",
                    "response_format_zip": "false",
                    "return_original_file": "false",
                },
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

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            # 1. Apply for upload URL
            data_id = uuid.uuid4().hex
            payload = {
                "files": [{"name": path.name, "data_id": data_id}],
                "model_version": "vlm"
            }

            try:
                response = await client.post(
                    "https://mineru.net/api/v4/file-urls/batch",
                    headers=headers,
                    json=payload
                )
            except httpx.HTTPError as exc:
                raise MinerUUnavailable(f"Failed to apply for MinerU Cloud upload URL: {exc}") from exc

            if response.status_code != 200:
                raise MinerUTaskFailed(
                    f"MinerU Cloud URL application failed with HTTP {response.status_code}: {response.text[:500]}"
                )

            res_json = response.json()
            if res_json.get("code") != 0:
                raise MinerUTaskFailed(f"MinerU Cloud API error: {res_json.get('msg')}")

            batch_id = res_json["data"]["batch_id"]
            upload_url = res_json["data"]["file_urls"][0]

            # 2. PUT upload file
            try:
                upload_response = await client.put(upload_url, content=path.read_bytes())
            except httpx.HTTPError as exc:
                raise MinerUUnavailable(f"Failed to upload file to MinerU Cloud OSS: {exc}") from exc

            if upload_response.status_code not in (200, 201):
                raise MinerUTaskFailed(f"File upload failed with HTTP {upload_response.status_code}")

            # 3. Poll for completion
            await self._wait_for_cloud_task(client, batch_id, path.name, started)

            # 4. Fetch results
            result_payload = await self._fetch_cloud_result(client, batch_id)

            file_result = None
            for item in result_payload.get("extract_result", []):
                if item.get("file_name") == path.name:
                    file_result = item
                    break

            if not file_result:
                raise MinerUInvalidResult(f"Could not find result for file {path.name} in batch")

            zip_url = file_result.get("full_zip_url")
            if not zip_url:
                raise MinerUInvalidResult("MinerU Cloud returned no zip download URL")

            # 5. Download ZIP
            try:
                zip_response = await client.get(zip_url)
            except httpx.HTTPError as exc:
                raise MinerUUnavailable(f"Failed to download MinerU Cloud ZIP output: {exc}") from exc

            if zip_response.status_code != 200:
                raise MinerUTaskFailed(f"Failed to download ZIP: HTTP {zip_response.status_code}")

            # 6. Extract Zip Contents
            md_content, content_list, content_list_v2, middle_json = extract_mineru_zip(zip_response.content)

            if not md_content.strip() and not content_list and not content_list_v2:
                raise MinerUInvalidResult("MinerU Cloud returned no usable markdown or content list")

            return MinerUParseResult(
                task_id=batch_id,
                backend="vlm",
                version="cloud_v4",
                document_name=path.name,
                md_content=md_content,
                content_list=content_list,
                content_list_v2=content_list_v2,
                middle_json=middle_json,
                raw_result=result_payload,
                elapsed_seconds=time.monotonic() - started,
            )

    async def _wait_for_cloud_task(
        self,
        client: httpx.AsyncClient,
        batch_id: str,
        filename: str,
        started: float,
    ) -> None:
        headers = {"Authorization": f"Bearer {self.token}"}
        while True:
            if time.monotonic() - started > settings.MINERU_TASK_TIMEOUT_SECONDS:
                raise MinerUTimeout(
                    f"MinerU Cloud task timed out after {settings.MINERU_TASK_TIMEOUT_SECONDS}s"
                )

            try:
                response = await client.get(
                    f"https://mineru.net/api/v4/extract-results/batch/{batch_id}",
                    headers=headers
                )
            except httpx.HTTPError as exc:
                raise MinerUUnavailable(f"Failed to poll MinerU Cloud task: {exc}") from exc

            if response.status_code != 200:
                raise MinerUUnavailable(
                    f"MinerU Cloud status polling failed with HTTP {response.status_code}"
                )

            payload = response.json()
            if payload.get("code") != 0:
                raise MinerUTaskFailed(f"MinerU Cloud API error during status poll: {payload.get('msg')}")

            extract_result = payload.get("data", {}).get("extract_result", [])
            file_result = None
            for item in extract_result:
                if item.get("file_name") == filename:
                    file_result = item
                    break

            if not file_result:
                await asyncio.sleep(max(1.0, float(settings.MINERU_POLL_INTERVAL_SECONDS)))
                continue

            state = str(file_result.get("state") or "").lower()
            if state == "done":
                return
            if state == "failed":
                err_msg = file_result.get("err_msg") or "MinerU Cloud task failed"
                raise MinerUTaskFailed(str(err_msg))

            await asyncio.sleep(max(1.0, float(settings.MINERU_POLL_INTERVAL_SECONDS)))

    async def _fetch_cloud_result(
        self,
        client: httpx.AsyncClient,
        batch_id: str,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            response = await client.get(
                f"https://mineru.net/api/v4/extract-results/batch/{batch_id}",
                headers=headers
            )
        except httpx.HTTPError as exc:
            raise MinerUUnavailable(f"Failed to fetch MinerU Cloud result: {exc}") from exc

        if response.status_code != 200:
            raise MinerUUnavailable(
                f"MinerU Cloud result fetch failed with HTTP {response.status_code}"
            )

        payload = response.json()
        if payload.get("code") != 0:
            raise MinerUTaskFailed(f"MinerU Cloud API error fetching result: {payload.get('msg')}")

        return payload.get("data", {})
