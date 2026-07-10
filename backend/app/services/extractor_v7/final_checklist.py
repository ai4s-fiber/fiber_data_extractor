"""Final pre-output checklist: 12-item systematic quality gate.

Every fact must pass all checks before entering clean_core_records.
Facts that fail any check get ``_checklist_failed = True`` and a list
of failure reasons in ``_checklist_failures``.

This module is called after all other post-processing steps, right
before record generation.
"""

from __future__ import annotations

import re

from app.services.grouping import normalize_for_match, normalize_sample_id
from app.services.metrics_dictionary import find_metric_canonical
from app.services.validation import (
    is_characterization_peak_metric,
    metric_unit_compatible,
    normalize_unit,
)
from app.services.extractor_v7.validators import is_background_or_reference_fact

_NUMBER_RE = re.compile(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
_PI_GENERIC_RE = re.compile(r"^PI$", re.I)
_CONDITION_IN_SID_RE = re.compile(
    r"(?:\d+(?:\.\d+)?\s*°?\s*[Cc](?:\s|$))|"
    r"(?:\d+\s*min)|"
    r"(?:\d+\s*%\s*strain)|"
    r"(?:[Xx]-?\s*band)|"
    r"(?:\d+\s*[-–]\s*\d+\s*[Gg][Hh]z)|"
    r"(?:RH\s*[=≈]?\s*\d+\s*%?)"
)


def _append_reason(existing: list[str], reason: str) -> list[str]:
    if reason not in existing:
        existing.append(reason)
    return existing


def _check_sample_id_in_evidence(fact: dict) -> str | None:
    """Check 1: sample_id must appear in evidence_text."""
    sid = str(fact.get("assigned_sample_id") or "").strip()
    evidence = str(fact.get("evidence_text") or "")
    if not sid or not evidence:
        return None  # No sample to check
    sid_norm = normalize_for_match(sid)
    ev_norm = normalize_for_match(evidence)
    # Allow partial match (e.g. "PI1" in "PI1 aerogel showed...")
    if sid_norm.replace(" ", "") in ev_norm.replace(" ", ""):
        return None
    # Try base name without form suffix
    base = re.sub(r"\s+(aerogel|nanofiber|nanofibers|film|membrane|foam|coating|powder|hydrogel|fiber|composite)s?$", "", sid, flags=re.I).strip()
    if base and normalize_for_match(base).replace(" ", "") in ev_norm.replace(" ", ""):
        return None
    return "sample_id_not_found_in_evidence"


def _check_no_generic_pi(fact: dict) -> str | None:
    """Check 2: PI1 should not be collapsed to PI."""
    sid = str(fact.get("assigned_sample_id") or "").strip()
    evidence = str(fact.get("evidence_text") or "")
    if not _PI_GENERIC_RE.fullmatch(sid):
        return None
    if re.search(r"\bPI\s*1\b|\bPI1\b", evidence, re.I):
        return "generic_PI_should_be_PI1"
    return None


def _check_no_condition_in_sample_id(fact: dict) -> str | None:
    """Check 3: temperature/frequency/time/strain not in sample_id."""
    sid = str(fact.get("assigned_sample_id") or "").strip()
    if not sid:
        return None
    # Only flag if the full sample_id with condition is NOT explicitly in evidence
    if _CONDITION_IN_SID_RE.search(sid):
        evidence = str(fact.get("evidence_text") or "")
        escaped = re.escape(sid)
        if not re.search(escaped, evidence, re.I):
            return "condition_token_in_sample_id"
    return None


def _check_metric_evidence_consistency(fact: dict) -> str | None:
    """Check 4: performance_metric should match evidence semantics."""
    metric = str(fact.get("metric_or_parameter") or "")
    evidence = str(fact.get("evidence_text") or "")
    if not metric or not evidence:
        return None
    canonical = find_metric_canonical(metric) or metric
    ev_lower = evidence.lower()
    # Specific known mismatches
    if canonical == "crystallinity_Xc" and "imidization" in ev_lower:
        return "metric_should_be_imidization_degree"
    if canonical == "surface_roughness":
        if "fiber length" in ev_lower or "average fiber length" in ev_lower:
            return "metric_should_be_fiber_length"
        if "fiber diameter" in ev_lower or "average fiber diameter" in ev_lower:
            return "metric_should_be_fiber_diameter"
    if canonical == "dielectric_constant" and "loss tangent" in ev_lower:
        if "permittivity" not in ev_lower and "dielectric constant" not in ev_lower:
            return "metric_may_be_loss_tangent_not_dielectric"
    return None


def _check_value_belongs_to_metric(fact: dict) -> str | None:
    """Check 5: value should belong to the stated metric, not a neighbor."""
    from app.services.extractor_v7.sample_value_alignment import (
        _numbers_equal,
        parse_metric_value_pairs,
    )

    evidence = str(fact.get("evidence_text") or "")
    value = fact.get("value")
    metric = find_metric_canonical(str(fact.get("metric_or_parameter") or "")) or ""
    if not evidence or value is None or not metric:
        return None
    pairs = parse_metric_value_pairs(evidence)
    if len(pairs) < 2:
        return None
    matched_metrics = [m for m, v in pairs if _numbers_equal(v, value)]
    if matched_metrics and metric not in matched_metrics:
        return f"value_belongs_to_{matched_metrics[0]}_not_{metric}"
    return None


def _check_unit_metric_match(fact: dict) -> str | None:
    """Check 6: unit must be compatible with metric."""
    metric = str(fact.get("metric_or_parameter") or "")
    unit = str(fact.get("unit") or "")
    if not metric or not unit:
        return None
    canonical = find_metric_canonical(metric) or metric
    if not metric_unit_compatible(canonical, unit):
        return f"unit_{unit}_incompatible_with_{canonical}"
    return None


def _check_condition_not_as_value(fact: dict) -> str | None:
    """Check 7: cycle count / bare temperature should not be performance_value."""
    value = str(fact.get("value") or "").strip()
    unit = str(fact.get("unit") or "").strip().lower()
    metric = find_metric_canonical(str(fact.get("metric_or_parameter") or "")) or ""
    if re.fullmatch(r"\d+", value) and ("cycle" in unit or "cycles" in unit):
        if metric in ("cyclic_compression_stability", "compression_stability"):
            return "cycle_count_as_performance_value"
    if unit in ("°c", "c") and metric not in (
        "surface_temperature", "glass_transition_temperature",
        "onset_decomposition_temperature", "Tg", "Tm", "Td5",
        "melting_point",
    ):
        return "bare_temperature_as_performance_value"
    return None


def _check_sample_form_consistency(fact: dict) -> str | None:
    """Check 8: nanofiber/film/aerogel/membrane should not be mixed."""
    from app.services.extractor_v7.quality_enhancement import (
        infer_sample_form,
        metric_conflicts_sample_form,
    )

    sid = str(fact.get("assigned_sample_id") or "")
    evidence = str(fact.get("evidence_text") or "")
    metric = str(fact.get("metric_or_parameter") or "")
    form = infer_sample_form(sid, evidence)
    if form and metric_conflicts_sample_form(metric, form):
        return f"metric_{metric}_conflicts_with_{form}_sample"
    return None


def _check_no_characterization_in_core(fact: dict) -> str | None:
    """Check 9: FTIR/XPS/XRD/Raman peaks should not be in core table."""
    metric = str(fact.get("metric_or_parameter") or "")
    method = str(fact.get("method") or "")
    evidence = str(fact.get("evidence_text") or "")
    if is_characterization_peak_metric(metric, method=method, evidence=evidence):
        return "characterization_peak_in_core_table"
    return None


def _check_no_introduction_data(fact: dict) -> str | None:
    """Check 11: Introduction/background data should not enter core."""
    if is_background_or_reference_fact(fact):
        return "background_reference_data_in_core"
    return None


def _check_paper_direction_conflict(fact: dict) -> str | None:
    """Check 12: metric contradicts paper theme (e.g. EMI SE in transparent paper)."""
    from app.services.extractor_v7.quality_enhancement import (
        should_reject_emi_shielding_fact,
    )
    # This is a simplified check; the full check requires theme context
    # We flag only if the fact already has the reject marker
    if fact.get("_reject"):
        return "paper_direction_conflict"
    return None


# Full checklist
_CHECKS = [
    _check_sample_id_in_evidence,        # 1
    _check_no_generic_pi,                # 2
    _check_no_condition_in_sample_id,    # 3
    _check_metric_evidence_consistency,  # 4
    _check_value_belongs_to_metric,      # 5
    _check_unit_metric_match,            # 6
    _check_condition_not_as_value,       # 7
    _check_sample_form_consistency,      # 8
    _check_no_characterization_in_core,  # 9
    # Check 10 (duplicates) is handled by merge_duplicate_facts
    _check_no_introduction_data,         # 11
    _check_paper_direction_conflict,     # 12
]


def run_final_checklist(facts: list[dict]) -> list[dict]:
    """Run the 12-item checklist on every performance fact.

    Facts that fail any check get:
    - ``_checklist_failed = True``
    - ``_checklist_failures = [list of reason strings]``
    - ``_export_tier`` downgraded to "B" if was "A"
    """
    for fact in facts:
        if fact.get("fact_type") != "performance":
            continue

        failures: list[str] = []
        for check_fn in _CHECKS:
            result = check_fn(fact)
            if result:
                failures.append(result)

        if failures:
            fact["_checklist_failed"] = True
            fact["_checklist_failures"] = failures
            # Downgrade tier
            current_tier = fact.get("_export_tier", "A")
            if current_tier == "A":
                fact["_export_tier"] = "B"
            # Append to reviewer comment
            existing = str(fact.get("assignment_reason") or "").strip()
            note = f"checklist_failed: {', '.join(failures)}"
            if note not in existing:
                fact["assignment_reason"] = (
                    f"{existing}; {note}".strip("; ") if existing else note
                )
        else:
            fact["_checklist_failed"] = False

    return facts
