"""End-to-end NAS ingestion and New Materials platform delivery routes."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_project_or_404
from app.models.extraction_job import ExtractionJob
from app.models.paper import Paper
from app.schemas.integration import (
    NasImportRequest,
    NasScanRequest,
    PlatformBatchRequest,
    PlatformConnectRequest,
    PlatformImportRequest,
)
from app.services import redis_cache
from app.services.extraction_jobs import extraction_job_backend
from app.services.nas_integration import (
    NasIntegrationError,
    discard_staged_pdf,
    get_nas_source,
    parse_nas_source_roots,
    scan_nas_source,
    stage_nas_pdf,
)
from app.services.platform_client import (
    PlatformApiClient,
    PlatformClientError,
    PlatformSession,
    platform_session_store,
)
from app.services.platform_delivery import (
    PlatformDeliveryError,
    PlatformReceiptStore,
    load_pinned_platform_binding,
)
from app.services.platform_material_delivery import (
    build_project_material_fact_artifact,
    load_material_fact_binding,
)


router = APIRouter(
    prefix="/projects/{project_id}/integrations",
    tags=["数据管道"],
)
_platform_import_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def _receipt_is_in_flight(receipt: dict[str, Any] | None) -> bool:
    return bool(
        receipt
        and receipt.get("status") in {"uploading", "processing"}
    )


def _collect_platform_data_ids(value: Any) -> set[str]:
    data_ids: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "数据ID" and isinstance(child, (str, int)):
                normalized = str(child).strip()
                if normalized:
                    data_ids.add(normalized)
            data_ids.update(_collect_platform_data_ids(child))
    elif isinstance(value, list):
        for child in value:
            data_ids.update(_collect_platform_data_ids(child))
    return data_ids


def _artifact_data_ids(content: bytes) -> set[str]:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlatformDeliveryError("平台批次不是有效的 UTF-8 JSON") from exc
    records = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(records, list) or not records:
        raise PlatformDeliveryError("平台批次没有可核对的数据记录")
    data_ids = _collect_platform_data_ids(records)
    if len(data_ids) != len(records):
        raise PlatformDeliveryError(
            "平台批次的数据ID缺失或重复，已拒绝上传"
        )
    return data_ids


def _platform_client() -> PlatformApiClient:
    return PlatformApiClient(settings.PLATFORM_BASE_URL)


def _load_platform_binding():
    return load_pinned_platform_binding(
        batch_template_path=settings.PLATFORM_BATCH_TEMPLATE_PATH,
        expected_sha256=settings.PLATFORM_BATCH_TEMPLATE_SHA256,
        expected_dataset_id=settings.PLATFORM_EXPECTED_DATASET_ID,
        expected_template_id=settings.PLATFORM_EXPECTED_TEMPLATE_ID,
    )


def _load_material_fact_binding():
    return load_material_fact_binding(
        template_path=settings.PLATFORM_MATERIAL_FACT_TEMPLATE_PATH,
        expected_sha256=settings.PLATFORM_MATERIAL_FACT_TEMPLATE_SHA256,
        expected_dataset_id=settings.PLATFORM_MATERIAL_FACT_DATASET_ID,
        expected_template_id=settings.PLATFORM_MATERIAL_FACT_TEMPLATE_ID,
        dataset_name=settings.PLATFORM_MATERIAL_FACT_DATASET_NAME,
    )


def _safe_platform_user(info: dict[str, Any], fallback: str) -> dict[str, str]:
    data = info.get("data")
    source = data if isinstance(data, dict) else info
    user = source.get("user") if isinstance(source, dict) else None
    if not isinstance(user, dict):
        user = source if isinstance(source, dict) else {}
    username = str(
        user.get("userName")
        or user.get("username")
        or fallback
    )
    display_name = str(
        user.get("nickName")
        or user.get("realName")
        or username
    )
    return {"username": username, "display_name": display_name}


async def _require_platform_session(
    project_id: int,
    handle: str | None,
) -> PlatformSession:
    if not handle:
        raise HTTPException(status_code=401, detail="请先连接新材料大数据中心")
    try:
        return await platform_session_store.get(handle, project_id=project_id)
    except PlatformClientError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/nas/sources")
async def list_nas_sources(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    try:
        sources = parse_nas_source_roots(settings.NAS_SOURCE_ROOTS)
    except NasIntegrationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "configured": bool(sources),
        "sources": [source.public_payload() for source in sources],
        "configuration_key": "NAS_SOURCE_ROOTS",
        "message": (
            ""
            if sources
            else "尚未配置 NAS 根目录，请在后端 .env 设置 NAS_SOURCE_ROOTS"
        ),
    }


@router.post("/nas/scan")
async def scan_nas_files(
    project_id: int,
    body: NasScanRequest,
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    try:
        source = get_nas_source(settings.NAS_SOURCE_ROOTS, body.source_id)
        result = await asyncio.to_thread(
            scan_nas_source,
            source,
            relative_directory=body.relative_directory,
            filename_query=body.filename_query,
            recursive=body.recursive,
            extensions=(".pdf",),
            max_files=settings.NAS_SCAN_MAX_FILES,
        )
    except NasIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "source": source.public_payload(),
        "relative_directory": body.relative_directory,
        "filename_query": body.filename_query or "",
        "recursive": body.recursive,
        "files": [item.payload() for item in result.files],
        "file_count": len(result.files),
        "truncated": result.truncated,
        "skipped_unreadable": result.skipped_unreadable,
    }


@router.post("/nas/import", status_code=201)
async def import_nas_files(
    project_id: int,
    body: NasImportRequest,
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    try:
        source = get_nas_source(settings.NAS_SOURCE_ROOTS, body.source_id)
    except NasIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    imported: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    staged_to_keep = []
    jobs: list[ExtractionJob] = []
    try:
        for selected in body.files:
            try:
                staged = await asyncio.to_thread(
                    stage_nas_pdf,
                    source,
                    relative_path=selected.relative_path,
                    expected_size=selected.size,
                    expected_modified_ns=int(selected.modified_ns),
                    upload_root=Path(settings.UPLOAD_DIR),
                    project_id=project_id,
                    max_file_bytes=settings.NAS_MAX_FILE_BYTES,
                )
            except (NasIntegrationError, OSError) as exc:
                failures.append(
                    {
                        "relative_path": selected.relative_path,
                        "reason": str(exc),
                    }
                )
                continue

            duplicate_result = await db.execute(
                select(Paper)
                .where(
                    Paper.project_id == project_id,
                    Paper.content_sha256 == staged.content_sha256,
                )
                .order_by(Paper.id)
                .limit(1)
            )
            duplicate = duplicate_result.scalar_one_or_none()
            if duplicate:
                await asyncio.to_thread(discard_staged_pdf, staged)
                duplicates.append(
                    {
                        "relative_path": selected.relative_path,
                        "paper_id": duplicate.id,
                        "filename": duplicate.original_filename,
                        "content_sha256": staged.content_sha256,
                    }
                )
                continue

            paper = Paper(
                project_id=project_id,
                original_filename=staged.original_filename,
                file_object_key=staged.file_object_key,
                content_sha256=staged.content_sha256,
                paper_title=Path(staged.original_filename).stem,
                status="queued" if body.start_extraction else "uploaded",
            )
            db.add(paper)
            await db.flush()
            staged_to_keep.append(staged)
            imported.append(
                {
                    "relative_path": selected.relative_path,
                    "paper_id": paper.id,
                    "filename": paper.original_filename,
                    "content_sha256": staged.content_sha256,
                    "size": staged.size,
                }
            )
            if body.start_extraction:
                job = ExtractionJob(
                    project_id=project_id,
                    paper_id=paper.id,
                    requested_mode=body.model_mode,
                    parser_strategy=body.parser_strategy,
                    status="queued",
                    step="starting",
                    percent=0,
                )
                db.add(job)
                jobs.append(job)

        if jobs:
            await db.flush()
        await db.commit()
    except Exception:
        await db.rollback()
        for staged in staged_to_keep:
            await asyncio.to_thread(discard_staged_pdf, staged)
        raise

    await redis_cache.bump_project_cache(project_id)
    for job in jobs:
        await extraction_job_backend.enqueue(job.id)

    job_by_paper = {job.paper_id: job.id for job in jobs}
    for item in imported:
        item["job_id"] = job_by_paper.get(item["paper_id"])
    return {
        "source_id": source.id,
        "requested_count": len(body.files),
        "imported_count": len(imported),
        "duplicate_count": len(duplicates),
        "failed_count": len(failures),
        "extraction_started": body.start_extraction,
        "imported": imported,
        "duplicates": duplicates,
        "failures": failures,
    }


@router.get("/platform/config")
async def get_platform_config(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    try:
        binding = await asyncio.to_thread(_load_material_fact_binding)
    except PlatformDeliveryError as exc:
        return {
            "ready": False,
            "base_url": settings.PLATFORM_BASE_URL,
            "message": str(exc),
        }
    return {
        "ready": True,
        "base_url": settings.PLATFORM_BASE_URL,
        "dataset_id": str(binding.dataset_id),
        "template_id": str(binding.template_id),
        "batch_template_sha256": binding.template_sha256,
        "schema_version": "ai4s_material_chain_v0.3.2",
        "delivery_mode": "ordered_sample_material_chain",
        "dataset_name": settings.PLATFORM_MATERIAL_FACT_DATASET_NAME,
        "session_ttl_seconds": settings.PLATFORM_SESSION_TTL_SECONDS,
    }


@router.get("/platform/captcha")
async def get_platform_captcha(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    try:
        return await _platform_client().get_captcha()
    except PlatformClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/platform/connect")
async def connect_platform(
    project_id: int,
    body: PlatformConnectRequest,
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    try:
        token, info = await _platform_client().login(
            username=body.username,
            password=body.password.get_secret_value(),
            captcha_code=body.captcha_code,
            captcha_uuid=body.captcha_uuid,
        )
        handle, session = await platform_session_store.create(
            project_id=project_id,
            token=token,
            username=body.username,
            user_info=info,
            ttl_seconds=settings.PLATFORM_SESSION_TTL_SECONDS,
        )
    except PlatformClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "connected": True,
        "session_id": handle,
        "expires_at": datetime.fromtimestamp(
            session.expires_at,
            tz=timezone.utc,
        ).isoformat(),
        "user": _safe_platform_user(info, body.username),
    }


@router.get("/platform/session")
async def check_platform_session(
    project_id: int,
    x_ai4s_platform_session: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    session = await _require_platform_session(
        project_id,
        x_ai4s_platform_session,
    )
    try:
        info = await _platform_client().get_info(session.token)
    except PlatformClientError as exc:
        await platform_session_store.revoke(
            x_ai4s_platform_session or "",
            project_id=project_id,
        )
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {
        "connected": True,
        "expires_at": datetime.fromtimestamp(
            session.expires_at,
            tz=timezone.utc,
        ).isoformat(),
        "user": _safe_platform_user(info, session.username),
    }


@router.delete("/platform/session")
async def disconnect_platform(
    project_id: int,
    x_ai4s_platform_session: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    if x_ai4s_platform_session:
        await platform_session_store.revoke(
            x_ai4s_platform_session,
            project_id=project_id,
        )
    return {"connected": False}


@router.post("/platform/preflight")
async def preflight_platform_batch(
    project_id: int,
    body: PlatformBatchRequest,
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    try:
        binding = await asyncio.to_thread(_load_material_fact_binding)
        artifact = await build_project_material_fact_artifact(
            db,
            project_id=project_id,
            binding=binding,
            paper_ids=body.paper_ids,
            include_unmapped=body.include_unmapped,
        )
        store = PlatformReceiptStore(Path(settings.EXPORT_DIR), project_id)
        receipt = await asyncio.to_thread(store.read, artifact.batch_sha256)
    except PlatformDeliveryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ready": True,
        **artifact.summary,
        "filename": artifact.filename,
        "paper_ids": artifact.paper_ids,
        "previous_delivery": receipt,
    }


@router.post("/platform/export")
async def export_platform_batch(
    project_id: int,
    body: PlatformBatchRequest,
    db: AsyncSession = Depends(get_db),
):
    """Download the exact validated batch as a no-login recovery path."""
    await get_project_or_404(db, project_id)
    try:
        binding = await asyncio.to_thread(_load_material_fact_binding)
        artifact = await build_project_material_fact_artifact(
            db,
            project_id=project_id,
            binding=binding,
            paper_ids=body.paper_ids,
            include_unmapped=body.include_unmapped,
        )
        store = PlatformReceiptStore(Path(settings.EXPORT_DIR), project_id)
        await asyncio.to_thread(store.write_batch, artifact)
    except PlatformDeliveryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=artifact.content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{artifact.filename}"',
            "X-AI4S-Batch-SHA256": artifact.batch_sha256,
        },
    )


@router.post("/platform/import")
async def import_platform_batch(
    project_id: int,
    body: PlatformImportRequest,
    x_ai4s_platform_session: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    await get_project_or_404(db, project_id)
    session = await _require_platform_session(
        project_id,
        x_ai4s_platform_session,
    )
    lock = _platform_import_locks[project_id]
    async with lock:
        try:
            binding = await asyncio.to_thread(_load_material_fact_binding)
            artifact = await build_project_material_fact_artifact(
                db,
                project_id=project_id,
                binding=binding,
                paper_ids=body.paper_ids,
                include_unmapped=body.include_unmapped,
            )
            store = PlatformReceiptStore(Path(settings.EXPORT_DIR), project_id)
            previous = await asyncio.to_thread(
                store.read,
                artifact.batch_sha256,
            )
        except PlatformDeliveryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if previous and previous.get("status") == "completed" and not body.force:
            return {
                "status": "already_confirmed",
                "idempotent": True,
                "receipt": previous,
                **artifact.summary,
            }

        client = _platform_client()
        try:
            live_history = await client.latest_upload_history(
                session.token,
                filename=artifact.filename,
            )
        except PlatformClientError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        live_state = client.history_state(live_history)
        if live_state == "completed" and not body.force:
            receipt = {
                "status": "completed",
                "reconciled": True,
                "confirmed_at": datetime.now(timezone.utc).isoformat(),
                "filename": artifact.filename,
                "paper_ids": artifact.paper_ids,
                "summary": artifact.summary,
                "platform_history": live_history,
            }
            await asyncio.to_thread(
                store.write_receipt,
                artifact.batch_sha256,
                receipt,
            )
            return {
                "status": "already_confirmed",
                "idempotent": True,
                "receipt": receipt,
                **artifact.summary,
            }
        if live_state == "processing" and not body.force:
            return {
                "status": "processing",
                "idempotent": True,
                "message": "平台中已有同一批次正在处理，未重复提交",
                "platform_history": live_history,
                **artifact.summary,
            }
        if (
            _receipt_is_in_flight(previous)
            and not body.force
        ):
            return {
                "status": "processing",
                "idempotent": True,
                "message": (
                    "本地回执显示同一批次已提交，但平台历史暂未返回；"
                    "为避免重复数据，本次未重传"
                ),
                "receipt": previous,
                "platform_history": live_history,
                **artifact.summary,
            }

        expected_data_ids = _artifact_data_ids(artifact.content)
        try:
            existing_rows = await client.dataset_records(
                session.token,
                dataset_id=binding.dataset_id,
            )
        except PlatformClientError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        existing_data_ids = _collect_platform_data_ids(existing_rows)
        duplicate_data_ids = sorted(expected_data_ids & existing_data_ids)
        if duplicate_data_ids:
            preview = "、".join(duplicate_data_ids[:5])
            suffix = "…" if len(duplicate_data_ids) > 5 else ""
            raise HTTPException(
                status_code=409,
                detail=(
                    "平台数据集已存在本批稳定数据ID，已拒绝跨批次重复插入："
                    f"{preview}{suffix}。如需更新已有样品，应使用专门的更新流程。"
                ),
            )

        batch_path = await asyncio.to_thread(store.write_batch, artifact)
        pending_receipt = {
            "status": "uploading",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "filename": artifact.filename,
            "batch_path": str(batch_path),
            "paper_ids": artifact.paper_ids,
            "summary": artifact.summary,
        }
        await asyncio.to_thread(
            store.write_receipt,
            artifact.batch_sha256,
            pending_receipt,
        )

        try:
            result = await client.upload_batch_json(
                session.token,
                filename=artifact.filename,
                content=artifact.content,
                dataset_id=binding.dataset_id,
                template_id=binding.template_id,
                parse_timeout_seconds=settings.PLATFORM_PARSE_TIMEOUT_SECONDS,
                poll_interval_seconds=settings.PLATFORM_POLL_INTERVAL_SECONDS,
            )
        except PlatformClientError as exc:
            failed_receipt = {
                **pending_receipt,
                "status": "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
            }
            await asyncio.to_thread(
                store.write_receipt,
                artifact.batch_sha256,
                failed_receipt,
            )
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        receipt = {
            **pending_receipt,
            "status": result["status"],
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "platform_result": result,
        }
        await asyncio.to_thread(
            store.write_receipt,
            artifact.batch_sha256,
            receipt,
        )
        return {
            "idempotent": False,
            "receipt": receipt,
            **artifact.summary,
            **result,
        }
