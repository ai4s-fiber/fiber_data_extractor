"""Classify each extracted fact into one of seven data-source types.

Categories (from the post-processing audit specification):
- paper_core_result
- background_reference
- comparison_literature
- experimental_condition
- characterization_feature
- method_parameter
- ambiguous_or_unverified
"""

from __future__ import annotations

import re

from app.services.grouping import normalize_for_match
from app.services.metrics_dictionary import find_metric_canonical
from app.services.validation import (
    is_characterization_peak_metric,
    is_formula_method_parameter_fact,
    normalize_unit,
)

# ---- Signal-word lists ----

_BACKGROUND_HINTS = (
    "previously reported", "has been reported", "were reported",
    "reported by", "reported in", "literature", "prior work",
    "previous work", "other studies", "other reports", "ref.",
    "compared with literature", "compared to literature",
    "in previous studies", "earlier studies", "earlier work",
    "have demonstrated", "has demonstrated",
)

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
    "prepared in this work", "obtained in this study",
    "this study", "present work", "present study",
)

_INTRO_SECTIONS = frozenset({
    "introduction", "background", "title_abstract", "references",
    "related work", "literature review",
})

_CONDITION_METRICS = frozenset({
    "temperature", "time", "humidity", "thickness",
    "frequency", "strain", "cycle_number", "loading_rate",
    "current_density", "voltage", "pH", "concentration",
    "pressure", "speed", "distance",
})

_CONDITION_UNITS = frozenset({
    "°c", "k", "min", "h", "s", "hz", "ghz", "mhz",
    "mm", "cm", "m", "rpm", "v", "a", "mol/l",
})

_CITATION_RE = re.compile(r"\[\s*\d+(?:\s*[-–,]\s*\d+)*\s*\]")


def _text_has_this_work_signal(text: str) -> bool:
    lower = normalize_for_match(text)
    return any(hint in lower for hint in _THIS_WORK_HINTS)


def _text_has_background_signal(text: str, section: str = "") -> bool:
    lower = normalize_for_match(text)
    section_lower = (section or "").lower().strip()
    if section_lower in _INTRO_SECTIONS:
        return True
    if any(hint in lower for hint in _BACKGROUND_HINTS):
        return True
    has_citation = bool(_CITATION_RE.search(text))
    if has_citation and any(
        hint in lower
        for hint in ("reported", "literature", "previous", "prior", "reference")
    ):
        return True
    return False


def _text_has_comparison_signal(text: str) -> bool:
    lower = normalize_for_match(text)
    return any(hint in lower for hint in _COMPARISON_HINTS)


def classify_data_source_type(fact: dict) -> str:
    """Return one of the seven data-source categories for a fact.

    The result is written to ``fact['_data_source_type']`` and also returned.
    """
    evidence = str(fact.get("evidence_text") or "")
    subject = str(fact.get("subject_text") or "")
    section = str(fact.get("_chunk_section") or "")
    metric = str(fact.get("metric_or_parameter") or "")
    unit = str(fact.get("unit") or "")
    method = str(fact.get("method") or "")
    combined = f"{evidence} {subject}"

    # 1. Method parameter (formulas, reference peaks, calibration)
    if is_formula_method_parameter_fact(fact):
        return "method_parameter"

    # 2. Characterization feature (FTIR, XPS, XRD, Raman, NMR peaks)
    if is_characterization_peak_metric(metric, method=method, evidence=evidence):
        return "characterization_feature"

    # 3. Experimental condition (test parameters, not performance)
    canonical = find_metric_canonical(metric) or metric
    unit_norm = normalize_unit(unit)
    if canonical.lower() in _CONDITION_METRICS:
        return "experimental_condition"
    if unit_norm in _CONDITION_UNITS and canonical not in (
        "surface_temperature", "glass_transition_temperature",
        "thermal_conductivity", "melting_point", "Tg", "Tm",
        "onset_decomposition_temperature", "Td5",
    ):
        # Bare temperature/time/frequency as value without a property metric
        if not canonical or canonical == metric:
            return "experimental_condition"

    # 4. Background reference (Introduction / prior work)
    is_bg = _text_has_background_signal(combined, section)
    is_this = _text_has_this_work_signal(combined)

    if is_bg and not is_this:
        # 5. Comparison literature (explicit comparison with other work)
        if _text_has_comparison_signal(combined):
            return "comparison_literature"
        return "background_reference"

    # 6. Ambiguous / unverified
    sample_id = str(fact.get("assigned_sample_id") or "").strip()
    value = str(fact.get("value") or "").strip()
    if not sample_id and not value:
        return "ambiguous_or_unverified"
    if fact.get("_metric_unit_mismatch"):
        return "ambiguous_or_unverified"
    if fact.get("_alignment_review_required"):
        return "ambiguous_or_unverified"

    # 7. Paper core result (default for validated facts)
    return "paper_core_result"


def apply_data_source_classification(facts: list[dict]) -> list[dict]:
    """Tag every fact with ``_data_source_type``."""
    for fact in facts:
        if fact.get("fact_type") != "performance":
            continue
        source_type = classify_data_source_type(fact)
        fact["_data_source_type"] = source_type
    return facts
