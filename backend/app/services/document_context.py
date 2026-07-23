"""DocumentContext construction and persistence.

The extractor consumes this normalized representation instead of raw PDF text.
MinerU-specific payloads are normalized here so the extraction stages can depend
on stable block/table/figure anchors.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import inspect
import re
import uuid
from html.parser import HTMLParser
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
from app.services.chunking import classify_section, classify_section_transition
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
            source_type = _source_type_for_block(block.block_type)
            if source_type == "table_text" and block.html:
                table_text = table_html_to_tsv(block.html)
                text = "\n".join(
                    part for part in ((block.text or "").strip(), table_text) if part
                )
            else:
                text = (block.text or block.html or "").strip()
            if not text:
                continue
            chunk = {
                "page_number": block.page_number,
                "order_index": block.order_index,
                "block_type": block.block_type,
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


@dataclass(slots=True)
class ReusableMinerUArtifact:
    result: MinerUParseResult
    raw_result_path: str
    markdown_path: str
    source_run: DocumentParseRun | None = None


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() in {"td", "th"} and self._row is not None:
            self._cell_parts = []

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower in {"td", "th"} and self._row is not None and self._cell_parts is not None:
            value = re.sub(r"\s+", " ", " ".join(self._cell_parts)).strip()
            self._row.append(value)
            self._cell_parts = None
        elif lower == "tr" and self._row is not None:
            if any(cell for cell in self._row):
                self.rows.append(self._row)
            self._row = None
            self._cell_parts = None


def table_html_to_tsv(html: str) -> str:
    """Convert MinerU table HTML into compact, row-addressable TSV text."""
    if not (html or "").strip():
        return ""
    parser = _TableHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html).strip()
    if not parser.rows:
        return re.sub(r"<[^>]+>", " ", html).strip()

    width = max(len(row) for row in parser.rows)
    rows = [row + [""] * (width - len(row)) for row in parser.rows]
    header = "\t".join(rows[0])
    lines = [f"[columns]\t{header}"]
    for index, row in enumerate(rows[1:], start=1):
        lines.append(f"[row {index}]\t" + "\t".join(row))
    return "\n".join(lines)


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
    current_section: str | None = None
    current_major_section: int | None = None

    for page_number, item in source_items:
        block_type = _normalize_block_type(item)
        text, html, caption = _block_text(item)
        bbox = item.get("bbox")
        if not isinstance(bbox, list):
            bbox = None
        block_id = f"B{order_index + 1:06d}"
        raw_heading_level = item.get("text_level")
        try:
            heading_level = int(raw_heading_level) if raw_heading_level is not None else None
        except (TypeError, ValueError):
            heading_level = None
        section, next_major_section = classify_section_transition(
            text or caption or html,
            page_number,
            current_section,
            current_major_section,
            block_type=block_type,
            heading_level=heading_level,
        )
        if block_type not in {"header_footer", "page_number"}:
            current_section = section
            current_major_section = next_major_section
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


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_sha256(path: str | Path) -> str:
    """Public streaming hash helper used by resumable ingestion tooling."""
    return _file_sha256(str(path))


def _cache_key_digest(parser_strategy: str) -> str:
    payload = json.dumps(
        _mineru_parse_cache_key(parser_strategy),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()[:20]


def _shared_artifact_paths(
    document_sha256: str,
    parser_strategy: str,
) -> tuple[Path, Path]:
    root = (
        Path(settings.PARSE_ARTIFACT_DIR)
        / "_cache"
        / document_sha256
        / _cache_key_digest(parser_strategy)
    )
    return root / "mineru_result.json", root / "mineru.md"


def _load_mineru_parse_result_from_artifacts(
    *,
    raw_result_path: str,
    markdown_path: str,
    expected_cache_key: dict[str, Any],
    expected_document_sha256: str = "",
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
    artifact_sha256 = str(artifact.get("document_sha256") or "")
    if (
        expected_document_sha256
        and artifact_sha256
        and artifact_sha256 != expected_document_sha256
    ):
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
    document_sha256: str,
) -> ReusableMinerUArtifact | None:
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
            expected_document_sha256=document_sha256,
        )
        if loaded is not None:
            return ReusableMinerUArtifact(
                result=loaded,
                raw_result_path=parse_run.raw_result_path or "",
                markdown_path=parse_run.markdown_path or "",
                source_run=parse_run,
            )

    raw_path, markdown_path = _shared_artifact_paths(
        document_sha256,
        parser_strategy,
    )
    loaded = _load_mineru_parse_result_from_artifacts(
        raw_result_path=str(raw_path),
        markdown_path=str(markdown_path),
        expected_cache_key=expected_cache_key,
        expected_document_sha256=document_sha256,
    )
    if loaded is not None:
        return ReusableMinerUArtifact(
            result=loaded,
            raw_result_path=str(raw_path),
            markdown_path=str(markdown_path),
        )
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

    document_sha256 = ""
    if settings.MINERU_REUSE_PARSE_ARTIFACTS and parser_strategy.startswith("mineru_"):
        document_sha256 = await asyncio.to_thread(_file_sha256, pdf_path)

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
                reusable = await _find_reusable_mineru_result(
                    db,
                    paper.id,
                    parser_strategy,
                    document_sha256,
                )
                if reusable is not None:
                    result = reusable.result
                    parse_run.parse_method = "artifact_reuse"
                    parse_run.mineru_task_id = (
                        reusable.source_run.mineru_task_id
                        if reusable.source_run is not None
                        else result.task_id
                    )
                    parse_run.mineru_backend = result.backend
                    parse_run.raw_result_path = reusable.raw_result_path
                    parse_run.markdown_path = reusable.markdown_path
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
                    document_sha256,
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
                reusable = await _find_reusable_mineru_result(
                    db,
                    paper.id,
                    parser_strategy,
                    document_sha256,
                )
                if reusable is not None:
                    result = reusable.result
                    parse_run.parse_method = "artifact_reuse"
                    parse_run.mineru_task_id = (
                        reusable.source_run.mineru_task_id
                        if reusable.source_run is not None
                        else result.task_id
                    )
                    parse_run.mineru_backend = result.backend
                    parse_run.raw_result_path = reusable.raw_result_path
                    parse_run.markdown_path = reusable.markdown_path
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
                        document_sha256,
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
    document_sha256: str = "",
) -> tuple[str, str]:
    if settings.MINERU_REUSE_PARSE_ARTIFACTS and document_sha256:
        raw_path, md_path = _shared_artifact_paths(document_sha256, parser_strategy)
    else:
        root = Path(settings.PARSE_ARTIFACT_DIR) / str(paper_id) / str(job_id or "manual")
        raw_path = root / "mineru_result.json"
        md_path = root / "mineru.md"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_result = dict(result.raw_result or {})
    raw_result["_fiber_extractor_mineru_artifact"] = {
        "cache_key": _mineru_parse_cache_key(parser_strategy),
        "document_sha256": document_sha256,
        "task_id": result.task_id,
        "backend": result.backend,
        "version": result.version,
        "document_name": result.document_name,
        "content_list": result.content_list,
        "content_list_v2": result.content_list_v2,
        "middle_json": result.middle_json,
    }
    # Keep temporary names short enough for default Windows path limits. The
    # content hash and parser hash already make the parent directory long.
    temp_id = uuid.uuid4().hex[:8]
    raw_temp = raw_path.with_name(f".r-{temp_id}.tmp")
    md_temp = md_path.with_name(f".m-{temp_id}.tmp")
    try:
        raw_temp.write_text(
            json.dumps(raw_result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        md_temp.write_text(result.md_content or "", encoding="utf-8")
        raw_temp.replace(raw_path)
        md_temp.replace(md_path)
    finally:
        raw_temp.unlink(missing_ok=True)
        md_temp.unlink(missing_ok=True)
    return str(raw_path), str(md_path)


def load_shared_mineru_artifact(
    document_sha256: str,
    parser_strategy: str = "mineru_cloud",
) -> MinerUParseResult | None:
    """Load a content-addressed MinerU artifact without creating database rows."""
    if not document_sha256 or not parser_strategy.startswith("mineru_"):
        return None
    raw_path, markdown_path = _shared_artifact_paths(
        document_sha256,
        parser_strategy,
    )
    return _load_mineru_parse_result_from_artifacts(
        raw_result_path=str(raw_path),
        markdown_path=str(markdown_path),
        expected_cache_key=_mineru_parse_cache_key(parser_strategy),
        expected_document_sha256=document_sha256,
    )


def persist_shared_mineru_artifact(
    result: MinerUParseResult,
    document_sha256: str,
    parser_strategy: str = "mineru_cloud",
) -> tuple[str, str]:
    """Atomically prefill the cache consumed by normal extraction jobs."""
    if not document_sha256:
        raise ValueError("document_sha256 is required for a shared MinerU artifact")
    if not parser_strategy.startswith("mineru_"):
        raise ValueError("Shared artifacts are supported only for MinerU strategies")
    return _write_parse_artifacts(
        paper_id=0,
        job_id=None,
        result=result,
        parser_strategy=parser_strategy,
        document_sha256=document_sha256,
    )
