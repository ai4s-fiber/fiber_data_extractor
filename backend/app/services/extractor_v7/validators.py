"""Quality validators for V7 extraction pipeline."""

from __future__ import annotations

import re


def validate_sample_catalog(samples: list[dict]) -> list[str]:
    issues = []
    if not samples:
        issues.append("未识别到任何样品")
        return issues
    for s in samples:
        sid = s.get("sample_id", "").strip()
        if not sid:
            issues.append("存在 sample_id 为空的条目")
        if len(sid) > 100:
            issues.append(f"sample_id 过长: {sid[:50]}...")
        if not s.get("source_location"):
            issues.append(f"样品 {sid} 缺少来源位置")
    return issues


def is_rough_source_location(source: str | None) -> bool:
    source_text = (source or "").strip()
    if not source_text:
        return True
    lower = source_text.lower()
    coarse_values = {
        "results_text", "experimental", "figure_caption", "table_text",
        "results", "figure", "table", "text", "unknown",
    }
    if lower in coarse_values:
        return True
    if re.fullmatch(r"p\.?\s*\d+\s*,?\s*(text|raw text|page)?", lower):
        return True
    return not bool(
        re.search(r"\b(p\.|page|fig\.|figure|table|section|sec\.|scheme)\b", lower)
    )


def _looks_like_affiliation_or_address(line: str) -> bool:
    lower = line.strip().lower()
    if not lower:
        return False
    if re.match(r"^[a-z]\s+[a-z]", lower):
        return True
    affiliation_hints = (
        "university", "institute", "school", "college", "department",
        "laboratory", "lab ", "academy", "hospital", "company", "co.,",
        "ltd", "inc.", "corp", "technology co", "medical technology",
        "beijing", "shanghai", "china", "usa", "email", "@",
        "corresponding author", "address",
    )
    return any(hint in lower for hint in affiliation_hints)


def _looks_like_journal_name(line: str) -> bool:
    text = re.sub(r"^\W+", "", line.strip())
    lower = text.lower()
    if not 4 <= len(text) <= 120:
        return False
    if _looks_like_affiliation_or_address(text):
        return False
    if re.search(r"\b(abstract|keywords|doi|http|received|accepted|available online)\b", lower):
        return False
    journal_hints = (
        "journal of", "materials letters", "materials today", "polymer",
        "composites", "composite structures", "chemical engineering journal",
        "science and technology", "advanced functional materials",
        "acs applied", "rsc advances", "nature communications",
    )
    if any(hint in lower for hint in journal_hints):
        return True
    words = re.findall(r"[A-Za-z][A-Za-z&-]*", text)
    if 1 <= len(words) <= 6 and any(
        word.lower() in {"materials", "polymers", "composites", "nanotechnology", "fibers"}
        for word in words
    ):
        return True
    return False


_PRIMARY_RESULT_PATTERNS = (
    re.compile(r"(?i)\b(?:fig(?:ure)?\.?|table)\s*\d+[a-z]?(?:\([^)]*\))?\s+(?:shows?|reports?|presents?|summarizes?)\b"),
    re.compile(r"(?i)\bresults?\s+(?:show|shows|showed|indicate|indicates|demonstrate|reveal)\b"),
    re.compile(r"(?i)\b(?:these|the|our)\s+(?:results?|values?)\s+(?:are|were)\s+(?:shown|presented|reported|summarized)\b"),
    re.compile(r"(?i)\b(?:the\s+)?plot\s+(?:shows?|indicates?|reveals?)\b"),
    re.compile(r"(?i)\b(?:was|were|is|are)\s+(?:measured|obtained|found|observed)\b"),
)


def text_has_primary_result_signal(text: str) -> bool:
    """Whether text explicitly presents a measurement from the current paper."""
    return any(pattern.search(text or "") for pattern in _PRIMARY_RESULT_PATTERNS)


def _text_has_background_reference_signal(text: str, section: str | None = None) -> bool:
    lower = (text or "").lower()
    section_lower = (section or "").lower()
    if section_lower in {"introduction", "background", "references", "back_matter"}:
        return True
    if section_lower in {"results", "conclusion", "experimental"} and text_has_primary_result_signal(text):
        return False
    reference_hints = (
        "previously reported", "has been reported", "were reported",
        "reported by", "reported in", "literature", "prior work",
        "previous work", "other studies", "other reports",
        "ref.", "compared with literature", "compared to literature",
    )
    has_hint = any(hint in lower for hint in reference_hints)
    has_citation = bool(re.search(r"\[[0-9,\s-]{1,20}\]", lower))
    return has_hint or (has_citation and any(
        hint in lower for hint in ("reported", "literature", "previous", "prior", "reference")
    ))


def text_has_background_reference_signal(
    text: str,
    section: str | None = None,
) -> bool:
    """Public predicate used by extraction stages before accepting a fact."""
    return _text_has_background_reference_signal(text, section)


def is_background_or_reference_fact(fact: dict) -> bool:
    if (
        fact.get("extraction_method") in {
            "AI_holistic_table", "rule_table_performance",
        }
        and fact.get("_source_table_row") is not None
    ):
        return False
    text = " ".join([
        str(fact.get("evidence_text") or ""),
        str(fact.get("subject_text") or ""),
        str(fact.get("source_location") or ""),
    ])
    section = str(fact.get("_chunk_section") or "").lower()
    if not section:
        source = str(fact.get("source_location") or "").lower()
        for candidate in ("results", "conclusion", "experimental", "introduction", "references"):
            if candidate in source:
                section = candidate
                break
    return _text_has_background_reference_signal(text, section)


_COMPARISON_HINTS = (
    "compared with", "compared to", "in comparison",
    "comparison with", "in contrast", "superior to",
    "higher than those", "lower than those", "outperform",
    "better than", "worse than", "comparable to",
    "surpass", "surpassed", "exceed", "exceeded",
    "previous reports", "other reported", "reported values",
    "reported in the literature",
)

_THIS_WORK_HINTS = (
    "this work", "our work", "herein", "in this study",
    "in this paper", "we prepared", "we synthesized",
    "we fabricated", "our sample", "our aerogel",
    "prepared in this work",
)


def is_comparison_literature_fact(fact: dict) -> bool:
    """Detect facts from comparison with other literature (not this work's result)."""
    text = " ".join([
        str(fact.get("evidence_text") or ""),
        str(fact.get("subject_text") or ""),
    ]).lower()
    has_comparison = any(hint in text for hint in _COMPARISON_HINTS)
    if not has_comparison:
        return False
    has_this_work = any(hint in text for hint in _THIS_WORK_HINTS)
    if has_this_work:
        return False
    has_citation = bool(re.search(r"\[\s*\d+(?:\s*[-–,]\s*\d+)*\s*\]", text))
    return has_citation


def validate_fact(fact: dict) -> list[str]:
    issues = []
    ftype = fact.get("fact_type", "")
    metric = fact.get("metric_or_parameter", "").strip()
    value = fact.get("value", "").strip()
    evidence = fact.get("evidence_text", "").strip()
    source = fact.get("source_location", "").strip()
    method = fact.get("extraction_method", "").strip()
    confidence = fact.get("confidence", 0)

    if ftype == "performance":
        if not metric:
            issues.append("性能指标名称为空")
        if not value:
            issues.append("性能数值为空")
        if not metric and not value:
            issues.append("性能指标和数值均缺失")
    if not evidence:
        issues.append("缺少原文证据")
    if not source:
        issues.append("缺少来源位置")
    elif is_rough_source_location(source):
        issues.append("来源位置过粗")
    if not method:
        issues.append("缺少提取方式")
    if method == "AI_figure" and confidence < 0.7:
        issues.append("图中估读，需人工复核")
    if method == "AI_sample_card":
        issues.append("来自样品卡摘要，需人工复核")
    if confidence is None or confidence < 0.6:
        issues.append("置信度偏低")
    return issues


def determine_review_status(
    fact: dict,
    assignment_confidence: float | None,
    issues: list[str],
) -> str:
    if any(i in {"性能指标名称为空", "性能数值为空", "性能指标和数值均缺失"} for i in issues):
        return "缺失"
    method = (fact.get("extraction_method") or "").strip()
    evidence = (fact.get("evidence_text") or "").strip()
    holistic = method in {
        "AI_holistic", "AI_holistic_table", "rule_table_performance",
    }
    substantial_evidence = len(evidence) >= 60 and re.search(r"\d", evidence)

    hard_issues = {
        "缺少原文证据", "样品归属缺失", "样品组归属需人工确认",
        "图中估读，需人工复核", "来自样品卡摘要，需人工复核",
    }
    if any(i in hard_issues for i in issues):
        return "存疑"

    soft_issues = {"缺少来源位置", "来源位置过粗"}
    active_soft = [i for i in issues if i in soft_issues]
    if active_soft:
        if holistic and substantial_evidence and fact.get("assigned_sample_id"):
            active_soft = [i for i in active_soft if i != "来源位置过粗"]
        if active_soft:
            return "存疑"

    confidence_threshold = 0.68 if holistic else 0.75
    if assignment_confidence is not None and assignment_confidence < confidence_threshold:
        return "存疑"
    if len(issues) >= 3:
        return "存疑"
    if fact.get("confidence", 0) < 0.65 and not holistic:
        return "存疑"
    return "待审核"
