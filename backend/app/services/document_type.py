"""High-confidence document-type checks used before expensive LLM extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DocumentTypeResult:
    kind: str
    confidence: float
    reason: str
    title: str = ""


_REVIEW_TITLE_RE = re.compile(
    r"(?i)(?:^a\s+review\s+of\b|^review\s+of\b|"
    r"^systematic\s+review\b|:\s*(?:a\s+)?(?:systematic\s+|critical\s+|"
    r"comprehensive\s+|state-of-the-art\s+)?review\s*$|"
    r"\b(?:a\s+)?(?:systematic|critical|comprehensive|state-of-the-art)\s+review\s*$)"
)
_REVIEW_LABEL_RE = re.compile(
    r"(?im)^\s*#*\s*(?:review|review article|systematic review)\s*$"
)
_REVIEW_DECLARATION_RE = re.compile(
    r"(?i)\b(?:herein|in\s+this\s+(?:paper|article|work))\s*,?\s+"
    r"we\s+(?:systematically\s+|critically\s+)?review\b"
)


def is_plausible_paper_title(value: str) -> bool:
    """Reject parser artifacts and front-matter labels before title selection."""
    line = re.sub(r"^\s*#+\s*", "", value or "").strip()
    line = re.sub(r"\s+", " ", line)
    if len(line) < 12 or len(line) > 320:
        return False
    if re.search(r"!\[[^\]]*\]\([^)]*\)|<img\b|(?:^|[/\\])images?[/\\]", line, re.I):
        return False
    if re.match(r"(?i)^(?:fiber__)?10\.\d{4,9}[_/]", line) or line.lower().endswith(
        ".pdf"
    ):
        return False
    if re.match(
        r"(?i)^(?:doi\b|https?://|www\.|abstract\b|keywords?\b|"
        r"research article\b|original article\b|article\b|journal\b|"
        r"volume\b|vol\.?\s*\d|copyright\b|received\b|accepted\b|\d{4}\b)",
        line,
    ):
        return False
    if "@" in line or line.count("|") >= 2:
        return False
    if len(re.findall(r"[^\W\d_]+", line, re.UNICODE)) < 3:
        return False
    return sum(character.isalpha() for character in line) >= 10


def _title_candidates(raw_text: str) -> list[str]:
    candidates: list[str] = []
    for raw_line in (raw_text or "")[:5000].splitlines():
        line = re.sub(r"^\s*#+\s*", "", raw_line).strip()
        line = re.sub(r"\s+", " ", line)
        if not is_plausible_paper_title(line):
            continue
        candidates.append(line)
        if len(candidates) >= 12:
            break
    return candidates


def classify_document_type(raw_text: str, title: str = "") -> DocumentTypeResult:
    """Classify only high-confidence review articles; default to research."""
    explicit_title = " ".join((title or "").split()).strip()
    if not is_plausible_paper_title(explicit_title):
        explicit_title = ""
    candidates = ([explicit_title] if explicit_title else []) + _title_candidates(raw_text)
    for candidate in candidates:
        if _REVIEW_TITLE_RE.search(candidate):
            return DocumentTypeResult(
                kind="review",
                confidence=0.99,
                reason="review_title",
                title=candidate,
            )

    head = (raw_text or "")[:8000]
    if _REVIEW_LABEL_RE.search(head):
        return DocumentTypeResult(
            kind="review",
            confidence=0.98,
            reason="review_article_label",
            title=candidates[0] if candidates else explicit_title,
        )
    if _REVIEW_DECLARATION_RE.search(head):
        return DocumentTypeResult(
            kind="review",
            confidence=0.94,
            reason="review_abstract_declaration",
            title=candidates[0] if candidates else explicit_title,
        )
    return DocumentTypeResult(
        kind="research",
        confidence=0.8,
        reason="no_high_confidence_review_signal",
        title=candidates[0] if candidates else explicit_title,
    )
