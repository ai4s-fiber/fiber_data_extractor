"""DocumentContext construction and persistence.

The extractor consumes this normalized representation instead of raw PDF text.
MinerU-specific payloads are normalized here so the extraction stages can depend
on stable block/table/figure anchors.
"""

from __future__ import annotations

import asyncio
import json
import inspect
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.extraction_job import ExtractionJob

from app.core.config import settings
from app.models.document_parse import (
    DocumentBlock,
    DocumentFigure,
    DocumentParseRun,
    DocumentTable,
)
from app.models.paper import Paper
from app.services.chunking import classify_section
from app.services.extractor_v7.exceptions import ExtractionCancelled
from app.services.job_cancellation import run_with_cancel_poll
from app.services.mineru_client import (
    MinerUClient,
    MinerUError,
    MinerUParseResult,
    MinerUUnavailable,
)
from app.services.pdf_utils import (
    extract_pdf_tables_markdown,
    extract_pdf_text,
    parse_pages_from_text,
)


ProgressCallback = Callable[[str, int, str], Any]
VALID_DOCUMENT_PARSER_STRATEGIES = {
    "mineru_cloud",
    "mineru_local",
    "mineru_local_sync",
    "legacy",
}


@dataclass(slots=True)
class DocumentPageData:
    page_number: int
    text: str
    block_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DocumentBlockData:
    block_id: str
    page_number: int
    order_index: int
    block_type: str
    section_name: str
    text: str = ""
    html: str = ""
    bbox: list[Any] | None = None
    parent_block_id: str | None = None
    related_block_ids: list[str] = field(default_factory=list)
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DocumentTableData:
    table_id: str
    block_id: str
    page_number: int
    caption: str = ""
    html: str = ""
    markdown: str = ""
    bbox: list[Any] | None = None


@dataclass(slots=True)
class DocumentFigureData:
    figure_id: str
    block_id: str
    page_number: int
    figure_type: str = "figure"
    caption: str = ""
    image_path: str = ""
    bbox: list[Any] | None = None


@dataclass(slots=True)
class DocumentContext:
    paper_id: int
    job_id: int | None
    parse_run_id: int | None
    parser_name: str
    markdown_text: str
    pages: list[DocumentPageData]
    blocks: list[DocumentBlockData]
    tables: list[DocumentTableData]
    figures: list[DocumentFigureData]
    raw_result: dict[str, Any] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def pages_as_tuples(self) -> list[tuple[int, str]]:
        return [(page.page_number, page.text) for page in self.pages]

    def tables_as_legacy_blocks(self) -> list[dict[str, str]]:
        tables: list[dict[str, str]] = []
        for table in self.tables:
            text = table.markdown or table.html or table.caption
            if not text.strip():
                continue
            tables.append({
                "source_location": f"page {table.page_number} / {table.table_id}",
                "text": text,
                "block_id": table.block_id,
            })
        return tables

    def chunks(self) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        for block in self.blocks:
            text = (block.text or block.html or "").strip()
            if not text:
                continue
            source_type = _source_type_for_block(block.block_type)
            chunk = {
                "page_number": block.page_number,
                "section_name": block.section_name,
                "source_type": source_type,
                "raw_text": text,
                "source_block_id": block.block_id,
                "source_bbox": block.bbox,
            }
            if source_type == "table_text":
                chunk["table_source"] = f"page {block.page_number} / {block.block_id}"
            if source_type == "figure_caption":
                chunk["has_figure_image"] = True
            chunks.append(chunk)
        return chunks


def _source_type_for_block(block_type: str) -> str:
    if block_type in {"table", "table_caption"}:
        return "table_text"
    if block_type in {"figure", "figure_caption", "chart"}:
        return "figure_caption"
    return "text"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _text_from_list_or_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        return " ".join(
            str(item).strip() for item in value.values() if str(item).strip()
        )
    return str(value).strip()


def _normalize_block_type(item: dict[str, Any]) -> str:
    raw = str(item.get("type") or item.get("sub_type") or "").lower().strip()
    if raw in {"text", "paragraph", "para", "body_text"}:
        return "paragraph"
    if raw in {"title", "doc_title", "heading"}:
        return "title"
    if raw in {"table", "table_body"}:
        return "table"
    if raw in {"table_caption"}:
        return "table_caption"
    if raw in {"image", "figure"}:
        return "figure"
    if raw in {"image_caption", "figure_caption"}:
        return "figure_caption"
    if raw == "chart":
        return "chart"
    if "equation" in raw:
        return "equation"
    if "reference" in raw:
        return "reference"
    if raw in {"header", "footer", "page_footnote"}:
        return "header_footer"
    if raw == "list":
        return "list"
    return raw or "unknown"


def _content_from_v2_item(item: dict[str, Any]) -> dict[str, Any]:
    content = item.get("content")
    return content if isinstance(content, dict) else item


def _block_text(item: dict[str, Any]) -> tuple[str, str, str]:
    content = _content_from_v2_item(item)
    text_parts = [
        content.get("text"),
        content.get("paragraph_content"),
        content.get("title_content"),
        content.get("math_content"),
        content.get("code_content"),
        content.get("algorithm_content"),
    ]
    caption_parts = [
        content.get("table_caption"),
        content.get("image_caption"),
        content.get("chart_caption"),
        content.get("code_caption"),
        content.get("algorithm_caption"),
    ]
    html = _text_from_list_or_string(content.get("table_body"))
    if not html:
        html = _text_from_list_or_string(content.get("html"))
    text = " ".join(_text_from_list_or_string(part) for part in text_parts).strip()
    caption = " ".join(_text_from_list_or_string(part) for part in caption_parts).strip()
    if not text and caption:
        text = caption
    if not text and html:
        text = html
    return text, html, caption


def _iter_content_list_v2_items(
    content_list_v2: list[dict[str, Any]],
) -> list[tuple[int, dict[str, Any]]]:
    items: list[tuple[int, dict[str, Any]]] = []
    for page_index, page_payload in enumerate(content_list_v2):
        if not isinstance(page_payload, dict):
            continue
        page_number = int(page_payload.get("page_idx", page_index) or page_index) + 1
        page_items = (
            page_payload.get("items")
            or page_payload.get("blocks")
            or page_payload.get("content")
            or []
        )
        if isinstance(page_items, dict):
            page_items = [page_items]
        if not isinstance(page_items, list):
            continue
        for item in page_items:
            if isinstance(item, dict):
                items.append((page_number, item))
    return items


def _iter_content_list_items(
    content_list: list[dict[str, Any]],
) -> list[tuple[int, dict[str, Any]]]:
    items: list[tuple[int, dict[str, Any]]] = []
    for item in content_list:
        if not isinstance(item, dict):
            continue
        page_number = int(item.get("page_idx", 0) or 0) + 1
        items.append((page_number, item))
    return items


def _build_pages_from_blocks(blocks: list[DocumentBlockData]) -> list[DocumentPageData]:
    by_page: dict[int, list[DocumentBlockData]] = {}
    for block in blocks:
        by_page.setdefault(block.page_number, []).append(block)

    pages: list[DocumentPageData] = []
    for page_number in sorted(by_page):
        page_blocks = sorted(by_page[page_number], key=lambda block: block.order_index)
        text = "\n\n".join(block.text for block in page_blocks if block.text.strip())
        pages.append(
            DocumentPageData(
                page_number=page_number,
                text=text,
                block_ids=[block.block_id for block in page_blocks],
            )
        )
    return pages


def build_document_context_from_mineru_result(
    paper_id: int,
    job_id: int | None,
    parse_run_id: int | None,
    result: MinerUParseResult,
) -> DocumentContext:
    source_items = _iter_content_list_v2_items(result.content_list_v2)
    if not source_items:
        source_items = _iter_content_list_items(result.content_list)

    blocks: list[DocumentBlockData] = []
    tables: list[DocumentTableData] = []
    figures: list[DocumentFigureData] = []
    order_index = 0

    for page_number, item in source_items:
        block_type = _normalize_block_type(item)
        text, html, caption = _block_text(item)
        bbox = item.get("bbox")
        if not isinstance(bbox, list):
            bbox = None
        block_id = f"B{order_index + 1:06d}"
        section = classify_section(text or caption or html, page_number)
        blocks.append(
            DocumentBlockData(
                block_id=block_id,
                page_number=page_number,
                order_index=order_index,
                block_type=block_type,
                section_name=section,
                text=text,
                html=html,
                bbox=bbox,
                raw_payload=item,
            )
        )

        if block_type == "table":
            table_id = f"T{len(tables) + 1:04d}"
            tables.append(
                DocumentTableData(
                    table_id=table_id,
                    block_id=block_id,
                    page_number=page_number,
                    caption=caption,
                    html=html,
                    markdown=html,
                    bbox=bbox,
                )
            )
        elif block_type in {"figure", "figure_caption", "chart"}:
            figure_id = f"F{len(figures) + 1:04d}"
            figures.append(
                DocumentFigureData(
                    figure_id=figure_id,
                    block_id=block_id,
                    page_number=page_number,
                    figure_type=block_type,
                    caption=caption or text,
                    image_path=str(item.get("img_path") or item.get("image_path") or ""),
                    bbox=bbox,
                )
            )
        order_index += 1

    if not blocks and result.md_content.strip():
        blocks = _blocks_from_markdown(result.md_content)

    pages = _build_pages_from_blocks(blocks)

    return DocumentContext(
        paper_id=paper_id,
        job_id=job_id,
        parse_run_id=parse_run_id,
        parser_name="mineru",
        markdown_text=result.md_content,
        pages=pages,
        blocks=blocks,
        tables=tables,
        figures=figures,
        raw_result=result.raw_result,
    )


def _mineru_parse_cache_key(parser_strategy: str) -> dict[str, Any]:
    if parser_strategy == "mineru_cloud":
        return {
            "parser_strategy": parser_strategy,
            "model_version": settings.MINERU_CLOUD_MODEL_VERSION,
            "language": settings.MINERU_LANG,
            "page_ranges": settings.MINERU_CLOUD_PAGE_RANGES.strip(),
            "enable_formula": settings.MINERU_CLOUD_ENABLE_FORMULA,
            "enable_table": settings.MINERU_CLOUD_ENABLE_TABLE,
            "is_ocr": settings.MINERU_CLOUD_IS_OCR,
        }
    return {
        "parser_strategy": parser_strategy,
        "backend": settings.MINERU_BACKEND,
        "parse_method": settings.MINERU_PARSE_METHOD,
        "language": settings.MINERU_LANG,
        "formula_enable": settings.MINERU_FORMULA_ENABLE,
        "table_enable": settings.MINERU_TABLE_ENABLE,
        "image_analysis": settings.MINERU_IMAGE_ANALYSIS_ENABLE,
        "hybrid_effort": settings.MINERU_HYBRID_EFFORT.strip()
        if "hybrid" in settings.MINERU_BACKEND.lower()
        else "",
    }


def _load_mineru_parse_result_from_artifacts(
    *,
    raw_result_path: str,
    markdown_path: str,
    expected_cache_key: dict[str, Any],
) -> MinerUParseResult | None:
    raw_path = Path(raw_result_path)
    md_path = Path(markdown_path)
    if not raw_path.exists() or not md_path.exists():
        return None
    try:
        raw_result = json.loads(raw_path.read_text(encoding="utf-8"))
        md_content = md_path.read_text(encoding="utf-8")
    except Exception:
        return None
    if not isinstance(raw_result, dict):
        return None

    artifact = raw_result.get("_fiber_extractor_mineru_artifact")
    if not isinstance(artifact, dict):
        return None
    if artifact.get("cache_key") != expected_cache_key:
        return None

    content_list = artifact.get("content_list")
    content_list_v2 = artifact.get("content_list_v2")
    middle_json = artifact.get("middle_json")
    return MinerUParseResult(
        task_id=str(artifact.get("task_id") or ""),
        backend=str(artifact.get("backend") or ""),
        version=artifact.get("version"),
        document_name=str(artifact.get("document_name") or raw_path.parent.name),
        md_content=md_content,
        content_list=content_list if isinstance(content_list, list) else [],
        content_list_v2=content_list_v2 if isinstance(content_list_v2, list) else [],
        middle_json=middle_json if isinstance(middle_json, dict) else {},
        raw_result=raw_result,
        elapsed_seconds=0.0,
    )


async def _find_reusable_mineru_result(
    db: AsyncSession,
    paper_id: int,
    parser_strategy: str,
) -> tuple[DocumentParseRun, MinerUParseResult] | None:
    if not settings.MINERU_REUSE_PARSE_ARTIFACTS:
        return None
    if not parser_strategy.startswith("mineru_"):
        return None

    expected_cache_key = _mineru_parse_cache_key(parser_strategy)
    result = await db.execute(
        select(DocumentParseRun)
        .where(
            DocumentParseRun.paper_id == paper_id,
            DocumentParseRun.parser_name == parser_strategy,
            DocumentParseRun.status == "completed",
            DocumentParseRun.raw_result_path.is_not(None),
            DocumentParseRun.markdown_path.is_not(None),
        )
        .order_by(DocumentParseRun.id.desc())
    )
    for parse_run in result.scalars().all():
        loaded = _load_mineru_parse_result_from_artifacts(
            raw_result_path=parse_run.raw_result_path or "",
            markdown_path=parse_run.markdown_path or "",
            expected_cache_key=expected_cache_key,
        )
        if loaded is not None:
            return parse_run, loaded
    return None


def _blocks_from_markdown(markdown_text: str) -> list[DocumentBlockData]:
    chunks = [chunk.strip() for chunk in re.split(r"\n{2,}", markdown_text) if chunk.strip()]
    blocks: list[DocumentBlockData] = []
    for idx, chunk in enumerate(chunks):
        page_number = 1
        section = classify_section(chunk, page_number)
        blocks.append(
            DocumentBlockData(
                block_id=f"B{idx + 1:06d}",
                page_number=page_number,
                order_index=idx,
                block_type="paragraph",
                section_name=section,
                text=chunk,
            )
        )
    return blocks


def build_legacy_document_context(
    paper_id: int,
    job_id: int | None,
    parse_run_id: int | None,
    pdf_path: str,
) -> DocumentContext:
    raw_text = extract_pdf_text(pdf_path)
    pages = parse_pages_from_text(raw_text)
    tables = extract_pdf_tables_markdown(pdf_path)

    blocks: list[DocumentBlockData] = []
    table_data: list[DocumentTableData] = []
    order = 0
    for page_number, text in pages:
        blocks.append(
            DocumentBlockData(
                block_id=f"B{order + 1:06d}",
                page_number=page_number,
                order_index=order,
                block_type="paragraph",
                section_name=classify_section(text, page_number),
                text=text,
            )
        )
        order += 1

    for table in tables:
        match = re.search(r"page\s+(\d+)", table.get("source_location", ""))
        page_number = int(match.group(1)) if match else 1
        block_id = f"B{order + 1:06d}"
        text = table.get("text", "")
        blocks.append(
            DocumentBlockData(
                block_id=block_id,
                page_number=page_number,
                order_index=order,
                block_type="table",
                section_name=classify_section(text, page_number),
                text=text,
                html=text,
            )
        )
        table_data.append(
            DocumentTableData(
                table_id=f"T{len(table_data) + 1:04d}",
                block_id=block_id,
                page_number=page_number,
                markdown=text,
            )
        )
        order += 1

    return DocumentContext(
        paper_id=paper_id,
        job_id=job_id,
        parse_run_id=parse_run_id,
        parser_name="legacy_pdf",
        markdown_text=raw_text,
        pages=_build_pages_from_blocks(blocks),
        blocks=blocks,
        tables=table_data,
        figures=[],
        raw_result={},
    )


async def persist_document_context(db: AsyncSession, context: DocumentContext) -> None:
    if context.parse_run_id is None:
        return

    await db.execute(
        sa_delete(DocumentBlock).where(DocumentBlock.parse_run_id == context.parse_run_id)
    )
    await db.execute(
        sa_delete(DocumentTable).where(DocumentTable.parse_run_id == context.parse_run_id)
    )
    await db.execute(
        sa_delete(DocumentFigure).where(DocumentFigure.parse_run_id == context.parse_run_id)
    )

    for block in context.blocks:
        db.add(
            DocumentBlock(
                parse_run_id=context.parse_run_id,
                paper_id=context.paper_id,
                job_id=context.job_id,
                block_id=block.block_id,
                page_number=block.page_number,
                order_index=block.order_index,
                block_type=block.block_type,
                section_name=block.section_name,
                text=block.text,
                html=block.html,
                bbox_json=_safe_json(block.bbox) if block.bbox is not None else None,
                parent_block_id=block.parent_block_id,
                related_block_ids_json=_safe_json(block.related_block_ids),
                raw_payload_json=_safe_json(block.raw_payload) if block.raw_payload else None,
            )
        )

    for table in context.tables:
        db.add(
            DocumentTable(
                parse_run_id=context.parse_run_id,
                paper_id=context.paper_id,
                job_id=context.job_id,
                table_id=table.table_id,
                block_id=table.block_id,
                page_number=table.page_number,
                caption=table.caption,
                html=table.html,
                markdown=table.markdown,
                bbox_json=_safe_json(table.bbox) if table.bbox is not None else None,
            )
        )

    for figure in context.figures:
        db.add(
            DocumentFigure(
                parse_run_id=context.parse_run_id,
                paper_id=context.paper_id,
                job_id=context.job_id,
                figure_id=figure.figure_id,
                block_id=figure.block_id,
                page_number=figure.page_number,
                figure_type=figure.figure_type,
                caption=figure.caption,
                image_path=figure.image_path,
                bbox_json=_safe_json(figure.bbox) if figure.bbox is not None else None,
            )
        )

    await db.commit()


async def parse_pdf_to_document_context(
    db: AsyncSession,
    paper: Paper,
    pdf_path: str,
    *,
    job_id: int | None,
    progress_callback: ProgressCallback | None = None,
) -> DocumentContext:
    parser_strategy = settings.DEFAULT_PARSER_STRATEGY
    if job_id:
        job_res = await db.execute(
            select(ExtractionJob).where(ExtractionJob.id == job_id)
        )
        job = job_res.scalar_one_or_none()
        if job and getattr(job, "parser_strategy", None):
            parser_strategy = job.parser_strategy
    if parser_strategy not in VALID_DOCUMENT_PARSER_STRATEGIES:
        raise ValueError(f"Unsupported document parser strategy: {parser_strategy}")

    parse_run = DocumentParseRun(
        paper_id=paper.id,
        job_id=job_id,
        parser_name=parser_strategy,
        mineru_backend=(
            settings.MINERU_CLOUD_MODEL_VERSION
            if parser_strategy == "mineru_cloud"
            else settings.MINERU_BACKEND if parser_strategy.startswith("mineru_") else None
        ),
        parse_method=settings.MINERU_PARSE_METHOD,
        status="running",
        started_at=_now(),
    )
    db.add(parse_run)
    await db.flush()
    await db.commit()

    try:
        if parser_strategy == "mineru_cloud":
            artifact_strategy = parser_strategy
            try:
                reusable = await _find_reusable_mineru_result(db, paper.id, parser_strategy)
                if reusable is not None:
                    cached_run, result = reusable
                    parse_run.parse_method = "artifact_reuse"
                    parse_run.mineru_task_id = cached_run.mineru_task_id
                    parse_run.mineru_backend = result.backend
                    parse_run.raw_result_path = cached_run.raw_result_path
                    parse_run.markdown_path = cached_run.markdown_path
                    if progress_callback:
                        await _maybe_await(progress_callback("inventory", 12, "复用已有 MinerU Cloud 解析产物，跳过重新解析..."))
                else:
                    if progress_callback:
                        await _maybe_await(progress_callback("inventory", 3, "正在使用 VLM 智能排版解析 (MinerU Cloud)..."))

                    result = await run_with_cancel_poll(
                        MinerUClient().parse_pdf(pdf_path, strategy="mineru_cloud"),
                        job_id,
                    )
            except MinerUError as cloud_exc:
                if not settings.MINERU_CLOUD_FALLBACK_LOCAL:
                    raise
                if progress_callback:
                    await _maybe_await(progress_callback("inventory", 5, f"VLM 智能解析失败 ({cloud_exc.error_code})，正在尝试退回到本地 MinerU 解析..."))
                # Fallback to local
                try:
                    result = await run_with_cancel_poll(
                        MinerUClient().parse_pdf(pdf_path, strategy="mineru_local"),
                        job_id,
                    )
                    artifact_strategy = "mineru_local"
                except MinerUError as local_exc:
                    raise MinerUUnavailable(
                        "MinerU Cloud 解析失败，且本地 MinerU 解析也失败。"
                        f"Cloud 错误: {cloud_exc}; 本地错误: {local_exc}"
                    ) from local_exc

            if parse_run.parse_method != "artifact_reuse":
                # If successful with either cloud or local
                parse_run.mineru_task_id = result.task_id
                parse_run.mineru_backend = result.backend
                parse_run.raw_result_path, parse_run.markdown_path = await asyncio.to_thread(
                    _write_parse_artifacts,
                    paper.id,
                    job_id,
                    result,
                    artifact_strategy,
                )
            context = await asyncio.to_thread(
                build_document_context_from_mineru_result,
                paper.id,
                job_id,
                parse_run.id,
                result,
            )

        elif parser_strategy in {"mineru_local", "mineru_local_sync"}:
            try:
                if progress_callback:
                    message = (
                        "正在使用本地 MinerU 同步解析 (/file_parse)..."
                        if parser_strategy == "mineru_local_sync"
                        else "正在提交本地 MinerU 异步解析任务..."
                    )
                    await _maybe_await(progress_callback("inventory", 3, message))
                reusable = await _find_reusable_mineru_result(db, paper.id, parser_strategy)
                if reusable is not None:
                    cached_run, result = reusable
                    parse_run.parse_method = "artifact_reuse"
                    parse_run.mineru_task_id = cached_run.mineru_task_id
                    parse_run.mineru_backend = result.backend
                    parse_run.raw_result_path = cached_run.raw_result_path
                    parse_run.markdown_path = cached_run.markdown_path
                    if progress_callback:
                        await _maybe_await(progress_callback("inventory", 12, "复用已有 MinerU 解析产物，跳过重新解析..."))
                else:
                    result = await run_with_cancel_poll(
                        MinerUClient().parse_pdf(pdf_path, strategy=parser_strategy),
                        job_id,
                    )
                if parse_run.parse_method != "artifact_reuse":
                    parse_run.mineru_task_id = result.task_id
                    parse_run.mineru_backend = result.backend
                    parse_run.raw_result_path, parse_run.markdown_path = await asyncio.to_thread(
                        _write_parse_artifacts,
                        paper.id,
                        job_id,
                        result,
                        parser_strategy,
                    )
                context = await asyncio.to_thread(
                    build_document_context_from_mineru_result,
                    paper.id,
                    job_id,
                    parse_run.id,
                    result,
                )
            except MinerUError:
                raise

        else:
            # Legacy parser strategy
            if progress_callback:
                await _maybe_await(progress_callback("inventory", 3, "正在使用传统解析 (Plain Text)..."))
            context = await asyncio.to_thread(
                build_legacy_document_context,
                paper.id, job_id, parse_run.id, pdf_path
            )
    except ExtractionCancelled as exc:
        parse_run.status = "cancelled"
        parse_run.error_code = "cancelled_by_user"
        parse_run.error_message = str(exc)[:2000]
        parse_run.finished_at = _now()
        await db.commit()
        raise
    except MinerUError as exc:
        parse_run.status = "failed"
        parse_run.error_code = exc.error_code
        parse_run.error_message = str(exc)[:2000]
        parse_run.finished_at = _now()
        await db.commit()
        # Non-interactive fallback: only auto fallback to legacy if specifically allowed by setting
        if not settings.MINERU_FALLBACK_LEGACY_PARSER:
            raise
        parse_run = DocumentParseRun(
            paper_id=paper.id,
            job_id=job_id,
            parser_name="legacy_pdf",
            parse_method="fallback",
            status="running",
            started_at=_now(),
            error_code=exc.error_code,
            error_message=f"MinerU fallback: {str(exc)[:1800]}",
        )
        db.add(parse_run)
        await db.flush()
        await db.commit()
        try:
            context = await asyncio.to_thread(
                build_legacy_document_context, paper.id, job_id, parse_run.id, pdf_path
            )
        except Exception as fallback_exc:
            parse_run.status = "failed"
            parse_run.error_code = "legacy_parser_failed"
            parse_run.error_message = str(fallback_exc)[:2000]
            parse_run.finished_at = _now()
            await db.commit()
            raise
    except Exception as exc:
        parse_run.status = "failed"
        parse_run.error_code = "document_context_parse_failed"
        parse_run.error_message = str(exc)[:2000]
        parse_run.finished_at = _now()
        await db.commit()
        raise


    if not context.blocks and not context.markdown_text.strip():
        parse_run.status = "failed"
        parse_run.error_code = "document_context_empty"
        parse_run.error_message = "DocumentContext has no usable blocks or markdown"
        parse_run.finished_at = _now()
        await db.commit()
        raise RuntimeError("DocumentContext has no usable blocks or markdown")

    parse_run.status = "completed"
    parse_run.finished_at = _now()
    await db.commit()
    await persist_document_context(db, context)
    return context


async def _maybe_await(value: Any) -> None:
    if inspect.isawaitable(value):
        await value


def _write_parse_artifacts(
    paper_id: int,
    job_id: int | None,
    result: MinerUParseResult,
    parser_strategy: str,
) -> tuple[str, str]:
    root = Path(settings.PARSE_ARTIFACT_DIR) / str(paper_id) / str(job_id or "manual")
    root.mkdir(parents=True, exist_ok=True)
    raw_path = root / "mineru_result.json"
    md_path = root / "mineru.md"
    raw_result = dict(result.raw_result or {})
    raw_result["_fiber_extractor_mineru_artifact"] = {
        "cache_key": _mineru_parse_cache_key(parser_strategy),
        "task_id": result.task_id,
        "backend": result.backend,
        "version": result.version,
        "document_name": result.document_name,
        "content_list": result.content_list,
        "content_list_v2": result.content_list_v2,
        "middle_json": result.middle_json,
    }
    raw_path.write_text(json.dumps(raw_result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(result.md_content or "", encoding="utf-8")
    return str(raw_path), str(md_path)
