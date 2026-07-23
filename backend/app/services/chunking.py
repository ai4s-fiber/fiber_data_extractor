"""
PDF document chunking with source metadata for multi-stage extraction.

Each chunk preserves: page_number, section_name, source_type, raw_text.
Tables are extracted as independent chunks.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


_SECTION_NUMBER_PREFIX = r"(?:\d+(?:\.\d+)*[.)]?\s*)?"
_SECTION_HEADING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("references", re.compile(
        rf"(?i)^\s*{_SECTION_NUMBER_PREFIX}(?:references|bibliography|参考文献)\s*$"
    )),
    ("back_matter", re.compile(
        rf"(?i)^\s*{_SECTION_NUMBER_PREFIX}(?:acknowledg(?:e)?ments?|"
        r"credit\s+authorship(?:\s+contribution\s+statement)?|"
        r"declaration\s+of\s+(?:competing|conflicting)\s+interest|"
        r"conflicts?\s+of\s+interest|data\s+availability|funding|致谢)\s*$"
    )),
    ("conclusion", re.compile(
        rf"(?i)^\s*{_SECTION_NUMBER_PREFIX}(?:conclusions?|summary|结论|总结)\s*$"
    )),
    ("results", re.compile(
        rf"(?i)^\s*{_SECTION_NUMBER_PREFIX}(?:results?(?:\s+and\s+discussion)?|"
        r"discussion|results?\s*&\s*discussion|结果与讨论|结果|讨论)\s*$"
    )),
    ("experimental", re.compile(
        rf"(?i)^\s*{_SECTION_NUMBER_PREFIX}(?:experimental(?:\s+section)?|"
        r"materials?\s+and\s+methods?|methods?|methodology|"
        r"materials?|sample\s+preparation|fabrication|实验(?:部分)?|"
        r"材料与方法|制备(?:方法)?)\s*$"
    )),
    ("introduction", re.compile(
        rf"(?i)^\s*{_SECTION_NUMBER_PREFIX}(?:introduction|background|引言|前言|背景)\s*$"
    )),
    ("title_abstract", re.compile(
        rf"(?i)^\s*{_SECTION_NUMBER_PREFIX}(?:abstract|keywords?|摘要|关键词)\s*$"
    )),
)

_NUMBERED_MAJOR_HEADING = re.compile(
    r"^\s*(?P<number>[1-9]\d?)(?:[.)])?\s+(?P<title>\S.*?)\s*$"
)
_HEADING_STOPWORDS = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on",
    "or", "the", "to", "under", "using", "via", "with",
}
_EXPERIMENTAL_HEADING_HINTS = (
    "material", "method", "experimental", "fabrication", "preparation",
    "synthesis", "manufactur", "processing", "specimen", "test setup",
    "testing setup", "characterization method", "numerical method",
    "finite element", "simulation setup", "analytical model", "theoretical",
    "geometry", "geometrical", "structural design", "model development",
)
_RESULTS_HEADING_HINTS = (
    "result", "discussion", "finding", "performance", "properties", "property",
    "characteristic", "response", "behavior", "behaviour", "effect", "influence",
    "evaluation", "comparison", "analysis", "morphology", "microstructure",
    "compressive", "tensile", "flexural", "thermal", "electrical", "dielectric",
    "vibration", "band structure", "energy absorption", "impact condition",
)


def detect_section_heading(text: str) -> str | None:
    """Return a section only for heading-like text, not ordinary prose."""
    candidate = " ".join((text or "").strip().split())
    if not candidate or len(candidate) > 180:
        return None
    for section, pattern in _SECTION_HEADING_PATTERNS:
        if pattern.fullmatch(candidate):
            return section
    return None


def _looks_like_unnormalized_heading(title: str) -> bool:
    """Conservative fallback for MinerU payloads without ``text_level``."""
    if len(title) > 160 or re.search(r"[.!?;:]\s*$", title):
        return False
    words = re.findall(r"[A-Za-z][A-Za-z0-9'-]*", title)
    cjk_chars = re.findall(r"[\u3400-\u9fff]", title)
    if cjk_chars:
        return len(cjk_chars) >= 4
    if not 2 <= len(words) <= 22:
        return False
    content_words = [word for word in words if word.lower() not in _HEADING_STOPWORDS]
    if len(content_words) < 2:
        return False
    capitalized = sum(word[0].isupper() or word.isupper() for word in content_words)
    return capitalized / len(content_words) >= 0.6


def detect_numbered_major_heading(
    text: str,
    *,
    heading_level: int | None = None,
) -> tuple[int, str] | None:
    """Detect a numbered top-level heading while rejecting subsections and lists."""
    candidate = " ".join((text or "").strip().split())
    if not candidate or len(candidate) > 180:
        return None
    match = _NUMBERED_MAJOR_HEADING.fullmatch(candidate)
    if not match:
        return None
    title = match.group("title").strip()
    if not title or (heading_level is None and not _looks_like_unnormalized_heading(title)):
        return None
    return int(match.group("number")), title


def _infer_numbered_section(
    number: int,
    title: str,
    current_section: str | None,
) -> str:
    lower = title.casefold()
    if number == 1:
        return "introduction"
    if any(hint in lower for hint in _RESULTS_HEADING_HINTS):
        return "results"
    if any(hint in lower for hint in _EXPERIMENTAL_HEADING_HINTS):
        return "experimental"
    if current_section == "results" or number >= 3:
        return "results"
    if number == 2 and current_section in {None, "title_abstract", "introduction"}:
        return "experimental"
    return current_section or ("title_abstract" if number == 0 else "introduction")


def classify_section_transition(
    text: str,
    page_number: int,
    current_section: str | None,
    current_major_section: int | None,
    *,
    block_type: str = "",
    heading_level: int | None = None,
) -> tuple[str, int | None]:
    """Classify one block and return the updated top-level section number."""
    normalized_type = (block_type or "").lower()
    if normalized_type == "ref_text":
        return "references", current_major_section

    explicit_heading = detect_section_heading(text)
    numbered_heading = detect_numbered_major_heading(text, heading_level=heading_level)
    next_major = current_major_section
    valid_major_transition = False
    if numbered_heading and current_section not in {"references", "back_matter"}:
        number, _ = numbered_heading
        valid_major_transition = (
            (current_major_section is None and number <= 3)
            or (
                current_major_section is not None
                and current_major_section < number <= current_major_section + 3
            )
        )
        if valid_major_transition:
            next_major = number

    if explicit_heading:
        if current_section in {"references", "back_matter"} and explicit_heading not in {
            "references", "back_matter",
        }:
            return current_section, current_major_section
        return explicit_heading, next_major

    if numbered_heading and valid_major_transition:
        number, title = numbered_heading
        return _infer_numbered_section(number, title, current_section), next_major

    if current_section:
        if current_section == "title_abstract" and page_number > 1:
            return "introduction", current_major_section
        return current_section, current_major_section
    return ("title_abstract" if page_number == 1 else "introduction"), current_major_section


def classify_section_in_sequence(
    text: str,
    page_number: int,
    current_section: str | None,
    *,
    block_type: str = "",
    heading_level: int | None = None,
    current_major_section: int | None = None,
) -> str:
    """Classify a MinerU block while preserving the active paper section."""
    section, _ = classify_section_transition(
        text,
        page_number,
        current_section,
        current_major_section,
        block_type=block_type,
        heading_level=heading_level,
    )
    return section


def classify_section(text: str, page_number: int) -> str:
    """Classify a block of text into a document section."""
    heading = detect_section_heading(text)
    if heading:
        return heading
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
