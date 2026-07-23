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


def _title_candidates(raw_text: str) -> list[str]:
    candidates: list[str] = []
    for raw_line in (raw_text or "")[:5000].splitlines():
        line = re.sub(r"^\s*#+\s*", "", raw_line).strip()
        line = re.sub(r"\s+", " ", line)
        if not line or len(line) < 12 or len(line) > 320:
            continue
        if re.match(r"(?i)^(?:doi|https?://|www\.|abstract\b|keywords?\b)", line):
            continue
        candidates.append(line)
        if len(candidates) >= 12:
            break
    return candidates


def classify_document_type(raw_text: str, title: str = "") -> DocumentTypeResult:
    """Classify only high-confidence review articles; default to research."""
    explicit_title = " ".join((title or "").split()).strip()
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
