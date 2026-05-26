"""PDF utilities migrated from V5.

Provides:
- Dual-engine PDF text extraction (PyMuPDF → pdfplumber fallback)
- Table extraction via pdfplumber → Markdown conversion
- Page rendering for multimodal vision (PyMuPDF → PNG bytes)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def extract_pdf_text(pdf_path: str, max_pages: int | None = None) -> str:
    """Extract text from PDF, preferring PyMuPDF, falling back to pdfplumber.

    Output is annotated with [page N] markers.
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF 不存在：{path}")

    # Primary: PyMuPDF (better text extraction, more robust)
    try:
        import fitz

        chunks: list[str] = []
        with fitz.open(path) as document:
            page_count = (
                len(document)
                if max_pages is None
                else min(len(document), max_pages)
            )
            for page_index in range(page_count):
                page = document.load_page(page_index)
                chunks.append(f"[page {page_index + 1}]\n{page.get_text()}")
        return "\n\n".join(chunks).strip()
    except Exception:
        pass

    # Fallback: pdfplumber
    try:
        import pdfplumber

        chunks: list[str] = []
        with pdfplumber.open(path) as document:
            pages = (
                document.pages
                if max_pages is None
                else document.pages[:max_pages]
            )
            for page_index, page in enumerate(pages, start=1):
                chunks.append(f"[page {page_index}]\n{page.extract_text() or ''}")
        return "\n\n".join(chunks).strip()
    except Exception as exc:
        raise RuntimeError(f"PDF 文本提取失败：{exc}") from exc


# ---------------------------------------------------------------------------
# Table extraction
# ---------------------------------------------------------------------------


def _table_to_markdown(table: list[list[Any]]) -> str:
    """Convert a 2D table (list of rows) to a GitHub-flavored Markdown table."""
    rows = [
        ["" if cell is None else str(cell).replace("\n", " ").strip() for cell in row]
        for row in table
        if row
    ]
    if not rows:
        return ""
    # Pad rows to uniform width
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = rows[0]
    body = rows[1:] if len(rows) > 1 else []
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def extract_pdf_tables_markdown(pdf_path: str) -> list[dict[str, str]]:
    """Extract embedded tables from a PDF and convert each to Markdown.

    Returns a list of {"source_location": "page N / table M", "text": markdown}
    """
    try:
        import pdfplumber
    except ImportError:
        return []

    blocks: list[dict[str, str]] = []
    try:
        with pdfplumber.open(pdf_path) as document:
            for page_index, page in enumerate(document.pages, start=1):
                tables = page.extract_tables() or []
                for table_index, table in enumerate(tables, start=1):
                    markdown = _table_to_markdown(table)
                    if markdown.strip():
                        blocks.append({
                            "source_location": f"page {page_index} / table {table_index}",
                            "text": markdown,
                        })
    except Exception:
        return blocks
    return blocks


# ---------------------------------------------------------------------------
# Page rendering for multimodal vision
# ---------------------------------------------------------------------------


def render_pdf_pages(
    pdf_path: str, pages: list[int], zoom: float = 1.4
) -> list[dict[str, Any]]:
    """Render specific PDF pages as PNG images (PyMuPDF).

    Args:
        pdf_path: Path to the PDF file.
        pages: 1-indexed page numbers to render.
        zoom: Rendering resolution multiplier (default 1.4x).

    Returns:
        [{"page": int, "image": bytes(png)}, ...]
    """
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("未安装 PyMuPDF，无法渲染 PDF 页面。") from exc

    rendered: list[dict[str, Any]] = []
    with fitz.open(pdf_path) as document:
        for page_number in pages:
            if page_number < 1 or page_number > len(document):
                continue
            page = document.load_page(page_number - 1)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            rendered.append({
                "page": page_number,
                "image": pixmap.tobytes("png"),
            })
    return rendered


# ---------------------------------------------------------------------------
# Page-level parsing helpers
# ---------------------------------------------------------------------------


def parse_pages_from_text(pdf_text: str) -> list[tuple[int, str]]:
    """Split `[page N]`-annotated text into (page_number, page_text) pairs."""
    matches = list(re.finditer(r"(?m)^\[page\s+(\d+)\]\s*$", pdf_text))
    if not matches:
        return [(1, pdf_text)]
    pages: list[tuple[int, str]] = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = (
            matches[idx + 1].start() if idx + 1 < len(matches) else len(pdf_text)
        )
        pages.append((int(match.group(1)), pdf_text[start:end].strip()))
    return pages
