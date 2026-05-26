"""
PDF document chunking with source metadata for multi-stage extraction.

Each chunk preserves: page_number, section_name, source_type, raw_text.
Tables are extracted as independent chunks.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


def classify_section(text: str, page_number: int) -> str:
    """Classify a block of text into a document section."""
    lower = text.lower()[:500]

    if page_number == 1:
        return "title_abstract"

    title_signals = ["title", "abstract", "keywords", "摘要", "关键词"]
    if any(s in lower for s in title_signals) and page_number <= 2:
        return "title_abstract"

    intro_signals = ["introduction", "引言", "background", "前言"]
    if any(s in lower for s in intro_signals):
        return "introduction"

    experimental_signals = [
        "experimental", "preparation", "synthesis", "materials",
        "characterization", "fabrication", "spinning", "实验",
        "制备", "合成", "表征", "材料与方法", "实验部分",
    ]
    if any(s in lower for s in experimental_signals):
        return "experimental"

    results_signals = ["results and discussion", "results", "discussion",
                       "结果与讨论", "结果", "讨论"]
    if any(s in lower for s in results_signals):
        return "results"

    conclusion_signals = ["conclusion", "summary", "结论", "总结"]
    if any(s in lower for s in conclusion_signals):
        return "conclusion"

    return "results"


def chunk_pdf_text(pages: list[tuple[int, str]],
                   tables: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    """
    Split PDF pages and extracted tables into labeled chunks.

    Args:
        pages: List of (page_number, page_text) tuples from PDF extraction.
        tables: Optional list of {"source_location": "...", "text": "markdown"}.

    Returns:
        List of chunks, each with: page_number, section_name, source_type, raw_text.
    """
    chunks: list[dict[str, Any]] = []
    tables = tables or []

    # Build table lookup by page
    table_by_page: dict[int, list[dict[str, str]]] = defaultdict(list)
    for t in tables:
        m = re.search(r"page\s+(\d+)", t.get("source_location", ""))
        if m:
            page_num = int(m.group(1))
            table_by_page[page_num].append(t)

    for page_num, page_text in pages:
        section = classify_section(page_text, page_num)
        text_lower = page_text.lower()

        # Detect figure captions within page text
        fig_captions = re.findall(
            r"(?i)(fig\.\s*\d+[a-z]?[.,:;].*?)(?=\n\s*(?:fig\.|table|scheme|$))",
            page_text
        )
        if not fig_captions:
            # Alternative: lines starting with Fig./Figure
            for line in page_text.split("\n"):
                if re.match(r"(?i)^\s*(fig\.|figure)\s+\d+", line.strip()):
                    fig_captions.append(line.strip())

        # Detect if page has figures (even without captions)
        has_figures = bool(fig_captions) or (
            "fig." in text_lower or "figure" in text_lower
        )

        # -- Main text chunk --
        chunks.append({
            "page_number": page_num,
            "section_name": section,
            "source_type": "text",
            "raw_text": page_text,
        })

        # -- Figure caption chunks --
        for cap in fig_captions:
            chunks.append({
                "page_number": page_num,
                "section_name": section,
                "source_type": "figure_caption",
                "raw_text": cap.strip(),
                "has_figure_image": True,
            })

        # -- Table chunks --
        for t in table_by_page.get(page_num, []):
            chunks.append({
                "page_number": page_num,
                "section_name": section,
                "source_type": "table_text",
                "raw_text": t["text"],
                "table_source": t.get("source_location", f"page {page_num}"),
            })

    return chunks


def chunks_by_type(chunks: list[dict[str, Any]], source_type: str) -> list[dict[str, Any]]:
    """Filter chunks by source_type."""
    return [c for c in chunks if c.get("source_type") == source_type]


def chunks_for_sample_catalog(chunks: list[dict[str, Any]]) -> str:
    """Build a concatenated text for sample catalog extraction.

    Prioritizes: title_abstract + experimental + figure_captions.
    """
    priority = ["title_abstract", "experimental", "introduction"]
    texts = []
    for chunk in chunks:
        if chunk.get("section_name") in priority or chunk.get("source_type") == "figure_caption":
            texts.append(f"[page {chunk['page_number']}][{chunk.get('section_name', '')}]\n{chunk['raw_text']}")
    return "\n\n---\n\n".join(texts)


def chunks_for_performance_extraction(chunks: list[dict[str, Any]]) -> str:
    """Build text for performance data extraction.

    Prioritizes: results + table_text + figure_caption chunks.
    """
    priority_sections = ["results", "conclusion"]
    priority_types = ["table_text", "figure_caption"]

    texts = []
    for chunk in chunks:
        sec = chunk.get("section_name", "")
        src = chunk.get("source_type", "")
        if sec in priority_sections or src in priority_types:
            tag = f"[page {chunk['page_number']}][{src}]"
            texts.append(f"{tag}\n{chunk['raw_text']}")

    return "\n\n---\n\n".join(texts)


def chunks_for_composition_process(chunks: list[dict[str, Any]]) -> str:
    """Build text for composition and process extraction.

    Prioritizes: experimental section + table_text.
    """
    texts = []
    for chunk in chunks:
        sec = chunk.get("section_name", "")
        src = chunk.get("source_type", "")
        if sec == "experimental" or src == "table_text":
            tag = f"[page {chunk['page_number']}][{sec}/{src}]"
            texts.append(f"{tag}\n{chunk['raw_text']}")
    return "\n\n---\n\n".join(texts)
