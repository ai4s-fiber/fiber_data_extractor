"""Evidence reverse lookup: enforce sample-metric-value triplets before export."""

from __future__ import annotations

import re
from typing import Any

from app.services.grouping import normalize_for_match, normalize_sample_id
from app.services.metrics_dictionary import find_metric_canonical
from app.services.extractor_v7.value_parse import (
    evidence_has_scientific_notation,
    validate_scientific_notation,
)

_PI_TEMP_SAMPLE_RE = re.compile(r"(?i)^PI[-\s]?(\d+(?:\.\d+)?)\s*°?\s*C\s*$")
_PI1_RE = re.compile(r"(?i)\bPI\s*1\b|\bPI1\b")
_AEROGEL_RE = re.compile(r"(?i)\baerogel")
_NANOFIBER_RE = re.compile(r"(?i)nanofiber|nanofibers")
_GHZ_RANGE_RE = re.compile(r"(?i)\b8\s*[-–]\s*12\s*ghz\b|\bx[- ]?band\b")
_REFERENCE_PERMittivity_RE = re.compile(
    r"(?i)(?:close\s+to|near|approximately|about|approaching|almost)\s+(?:air|unity|1\.0|1\.00|\b1\b)"
)

_MAIN_AEROGEL_METRICS = frozenset({
    "thermal_conductivity",
    "compressive_stress",
    "shrinkage",
    "density",
    "surface_temperature",
    "water_contact_angle",
    "porosity",
    "dielectric_constant",
    "loss_tangent",
    "cyclic_compression_stability",
})


def _append_reason(existing: Any, suffix: str) -> str:
    text = str(existing or "").strip()
    if suffix in text:
        return text
    return f"{text}; {suffix}".strip("; ") if text else suffix


def _enrich_sample_from_evidence(sid: str, evidence: str, metric: str = "") -> str:
    """Add aerogel/nanofiber suffix when evidence context requires it."""
    from app.services.extractor_v7.hard_validation import refine_sample_name_before_paren

    text = normalize_sample_id(sid)
    if not text:
        return text
    lower_ev = evidence.lower()
    metric_canon = find_metric_canonical(metric) or metric

    if metric_canon in ("fiber_length", "fiber_diameter", "tensile_strength"):
        if _NANOFIBER_RE.search(lower_ev) and "nanofiber" not in text.lower():
            if text.upper() in ("PI", "PI AEROGEL"):
                return "PI nanofiber"
            if re.search(r"(?i)2MZ-AZINE-PI", text):
                return f"{text} nanofibers" if not text.lower().endswith("s") else text
            return f"{text} nanofiber"

    if _AEROGEL_RE.search(text):
        return text

    if re.fullmatch(r"(?i)PI\s*1|PI1", text) and _AEROGEL_RE.search(lower_ev):
        return "PI1 aerogel"

    if re.fullmatch(r"(?i)PI", text) and _PI1_RE.search(evidence) and _AEROGEL_RE.search(lower_ev):
        return "PI1 aerogel"

    if re.search(rf"(?i){re.escape(text)}\s+aerogel", evidence):
        return f"{text} aerogel"

    _ = refine_sample_name_before_paren
    return text


def is_pi_temperature_treatment_sample(sample_id: str) -> bool:
    return bool(_PI_TEMP_SAMPLE_RE.match(normalize_sample_id(sample_id or "").strip()))


def build_allowed_triplets(
    evidence: str,
    *,
    default_metric: str = "",
) -> list[dict[str, str]]:
    """Build allowed sample-metric-value rows from evidence."""
    from app.services.extractor_v7.hard_validation import (
        build_alignment_rows,
        infer_metric_from_evidence,
        parse_ordered_sample_value_list,
    )
    from app.services.extractor_v7.sample_value_alignment import (
        _numbers_equal,
        parse_metric_value_pairs,
        parse_sample_value_pairs,
    )

    if not evidence.strip():
        return []

    metric = find_metric_canonical(default_metric) or default_metric
    inferred = infer_metric_from_evidence(evidence, current_metric=metric) or metric

    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add_row(sample_id: str, row_metric: str, value: str) -> None:
        from app.services.extractor_v7.sample_value_alignment import _normalize_number

        sid = _enrich_sample_from_evidence(sample_id, evidence, row_metric)
        m = find_metric_canonical(row_metric) or row_metric or inferred or "performance"
        fixed, _ = validate_scientific_notation(value, evidence)
        key = (normalize_for_match(sid), m, _normalize_number(fixed))
        if key in seen:
            return
        seen.add(key)
        rows.append({"sample_id": sid, "metric": m, "value": fixed})

    for row in build_alignment_rows(evidence, default_metric=metric):
        add_row(row["sample_id"], row["metric"], row["value"])

    if len(rows) < 2:
        for sid, val in parse_ordered_sample_value_list(evidence):
            add_row(sid, inferred or metric, val)

    sample_pairs = parse_sample_value_pairs(evidence)
    metric_pairs = parse_metric_value_pairs(evidence)

    if sample_pairs:
        for sid, val in sample_pairs:
            row_metric = inferred or metric
            for mp, mv in metric_pairs:
                if _numbers_equal(mv, val):
                    row_metric = mp
                    break
            add_row(sid, row_metric, val)

    if metric_pairs and not sample_pairs:
        for mp, mv in metric_pairs:
            add_row("", mp, mv)

    return rows


def is_spurious_dielectric_fact(fact: dict) -> bool:
    """Reject dielectric_constant values not supported by evidence."""
    from app.services.extractor_v7.sample_value_alignment import (
        _numbers_equal,
        parse_metric_value_pairs,
    )

    metric = find_metric_canonical(str(fact.get("metric_or_parameter") or "")) or ""
    if metric != "dielectric_constant":
        return False

    evidence = str(fact.get("evidence_text") or "")
    value = fact.get("value")
    if value is None:
        return True

    pairs = parse_metric_value_pairs(evidence)
    dielectric_vals = [v for m, v in pairs if m == "dielectric_constant"]
    loss_vals = [v for m, v in pairs if m == "loss_tangent"]

    if dielectric_vals:
        if not any(_numbers_equal(v, value) for v in dielectric_vals):
            return True
        return False

    if loss_vals and any(_numbers_equal(v, value) for v in loss_vals):
        return True

    val_norm = str(value).strip()
    if _numbers_equal(val_norm, "8"):
        if evidence_has_scientific_notation(evidence) and re.search(
            r"(?i)8\s*[×x*·]\s*10", evidence
        ):
            return True
        if _GHZ_RANGE_RE.search(evidence):
            return True

    if _numbers_equal(val_norm, "1") or _numbers_equal(val_norm, "1.0"):
        if _REFERENCE_PERMittivity_RE.search(evidence):
            return True
        if re.search(r"(?i)permittivity\s+of\s+1\.0(?:\s|$|[^0-9])", evidence):
            if not re.search(r"(?i)permittivity\s+of\s+1\.00?[34]", evidence):
                return True

    return False


def _find_triplet_for_value(
    rows: list[dict[str, str]],
    value: Any,
    metric: str,
) -> dict[str, str] | None:
    from app.services.extractor_v7.sample_value_alignment import _numbers_equal

    canon = find_metric_canonical(metric) or metric
    matches = [
        r for r in rows
        if _numbers_equal(r["value"], value)
        and (not canon or r["metric"] == canon or not r.get("metric"))
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1 and canon:
        metric_matches = [r for r in matches if r["metric"] == canon]
        if len(metric_matches) == 1:
            return metric_matches[0]
    return None


def _reassign_from_triplet_table(fact: dict, rows: list[dict[str, str]]) -> dict | None:
    """Return corrected fact, or None if fact must be dropped."""
    from app.services.extractor_v7.sample_value_alignment import _numbers_equal

    if not rows:
        return fact

    value = fact.get("value")
    metric = find_metric_canonical(str(fact.get("metric_or_parameter") or "")) or ""
    sample_id = str(fact.get("assigned_sample_id") or "").strip()

    if is_spurious_dielectric_fact(fact):
        return None

    row = _find_triplet_for_value(rows, value, metric)
    if not row:
        if len(rows) >= 2 and metric in _MAIN_AEROGEL_METRICS:
            return None
        return fact

    corrected = dict(fact)
    if row["sample_id"]:
        corrected["assigned_sample_id"] = row["sample_id"]
        corrected["candidate_sample_ids"] = [row["sample_id"]]
    if row["metric"]:
        corrected["metric_or_parameter"] = row["metric"]
    corrected["value"] = row["value"]
    corrected["assignment_status"] = "assigned"
    corrected["assignment_confidence"] = max(
        float(corrected.get("assignment_confidence") or 0), 0.92
    )
    corrected["assignment_reason"] = _append_reason(
        corrected.get("assignment_reason"), "evidence_triplet_bound"
    )

    if sample_id and row["sample_id"]:
        if normalize_for_match(sample_id) != normalize_for_match(row["sample_id"]):
            corrected["assignment_reason"] = _append_reason(
                corrected.get("assignment_reason"), "sample_corrected_by_evidence_audit"
            )

    if metric and row["metric"] and metric != row["metric"]:
        if not _numbers_equal(corrected.get("value"), value):
            pass
        else:
            corrected["assignment_reason"] = _append_reason(
                corrected.get("assignment_reason"), "metric_corrected_by_evidence_audit"
            )

    return corrected


def fix_pi_temperature_sample_mismatch(fact: dict) -> dict | None:
    """PI-200 °C must not own values bound to PI1 / PI1 aerogel in evidence."""
    from app.services.extractor_v7.sample_value_alignment import (
        _numbers_equal,
        parse_sample_value_pairs,
    )

    sample_id = str(fact.get("assigned_sample_id") or "").strip()
    if not is_pi_temperature_treatment_sample(sample_id):
        return fact

    evidence = str(fact.get("evidence_text") or "")
    value = fact.get("value")
    metric = find_metric_canonical(str(fact.get("metric_or_parameter") or "")) or ""

    pairs = parse_sample_value_pairs(evidence)
    for bound_sid, bound_val in pairs:
        if not _numbers_equal(bound_val, value):
            continue
        if is_pi_temperature_treatment_sample(bound_sid):
            continue
        enriched = _enrich_sample_from_evidence(bound_sid, evidence, metric)
        fact = dict(fact)
        fact["assigned_sample_id"] = enriched
        fact["candidate_sample_ids"] = [enriched]
        fact["assignment_reason"] = _append_reason(
            fact.get("assignment_reason"), "pi_temp_sample_reassigned_from_parens"
        )
        return fact

    if metric in _MAIN_AEROGEL_METRICS and _PI1_RE.search(evidence):
        for bound_sid, bound_val in pairs:
            if _numbers_equal(bound_val, value) and re.search(r"(?i)PI\s*1|PI1", bound_sid):
                enriched = _enrich_sample_from_evidence(bound_sid, evidence, metric)
                fact = dict(fact)
                fact["assigned_sample_id"] = enriched
                fact["candidate_sample_ids"] = [enriched]
                fact["assignment_reason"] = _append_reason(
                    fact.get("assignment_reason"), "pi_temp_to_pi1_aerogel"
                )
                return fact

    return fact


def audit_fact_against_evidence(fact: dict) -> dict | None:
    """
    Reverse-check sample-metric-value against evidence.
    Returns corrected fact, or None if the record must be dropped.
    """
    if fact.get("fact_type") != "performance":
        return fact

    evidence = str(fact.get("evidence_text") or "")
    if not evidence:
        return fact

    fact = fix_pi_temperature_sample_mismatch(fact)
    if fact is None:
        return None

    if is_spurious_dielectric_fact(fact):
        return None

    rows = build_allowed_triplets(
        evidence,
        default_metric=str(fact.get("metric_or_parameter") or ""),
    )

    if not rows:
        return fact

    sample_pairs_count = len({
        normalize_for_match(r["sample_id"])
        for r in rows if r.get("sample_id")
    })
    if sample_pairs_count < 2 and len(rows) < 2:
        row = _find_triplet_for_value(
            rows,
            fact.get("value"),
            str(fact.get("metric_or_parameter") or ""),
        )
        if row and row.get("sample_id"):
            sid = str(fact.get("assigned_sample_id") or "")
            if sid and normalize_for_match(sid) != normalize_for_match(row["sample_id"]):
                fact = dict(fact)
                fact["assigned_sample_id"] = row["sample_id"]
                fact["candidate_sample_ids"] = [row["sample_id"]]
                fact["assignment_reason"] = _append_reason(
                    fact.get("assignment_reason"), "evidence_triplet_sample_fix"
                )
        return fact

    corrected = _reassign_from_triplet_table(fact, rows)
    return corrected


def apply_evidence_reverse_lookup(facts: list[dict]) -> list[dict]:
    """Final evidence audit: fix triplets or mark records that fail reverse lookup.

    Instead of silently dropping failed facts, mark them with
    ``_evidence_audit_failed = True`` and ``_evidence_rejection_reason``
    so they can enter the review table rather than being lost.
    """
    result: list[dict] = []
    for fact in facts:
        audited = audit_fact_against_evidence(fact)
        if audited is None:
            # Preserve the original fact but mark it as failed
            rejected = dict(fact)
            rejected["_evidence_audit_failed"] = True
            rejected["_evidence_rejection_reason"] = "evidence_reverse_lookup_failed"
            rejected["_export_tier"] = "C"
            rejected["assignment_reason"] = _append_reason(
                rejected.get("assignment_reason"), "evidence_audit_rejected"
            )
            result.append(rejected)
            continue
        result.append(audited)
    return result
