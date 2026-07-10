"""Pre-output validation: metric-unit checks, characterization routing, sample_id fixes."""

from __future__ import annotations

import re
from typing import Any

from app.services.extractor_v7.metric_normalize import _infer_technique, canonicalize_metric_name
from app.services.extractor_v7.sample_id_rules import sanitize_sample_id
from app.services.grouping import normalize_for_match, normalize_sample_id
from app.services.metrics_dictionary import find_metric_canonical
from app.services.extractor_v7.evidence_audit import is_spurious_dielectric_fact
from app.services.extractor_v7.hard_validation import infer_metric_from_evidence
from app.services.validation import (
    infer_metric_from_unit_mismatch,
    is_characterization_peak_metric,
    is_formula_method_parameter_fact,
    metric_unit_compatible,
    normalize_unit,
)

_IMIDIZATION_RE = re.compile(r"(?is)imidization|imidisation|imide|酰亚胺")
_PI1_EVIDENCE_RE = re.compile(r"(?i)\bPI\s*1\b|\bPI1\b")
_PI1_AEROGEL_RE = re.compile(r"(?i)\bPI\s*1\s+aerogel\b|\bPI1\s+aerogel\b")


def _append_reason(existing: Any, suffix: str) -> str:
    text = str(existing or "").strip()
    if suffix in text:
        return text
    return f"{text}; {suffix}".strip("; ") if text else suffix


_STRUCTURE_FEATURE_METRICS = frozenset({
    "fiber_diameter", "fiber_length", "pore_size", "pore_diameter",
    "imidization_degree", "crystallinity", "crystallinity_Xc",
    "degree_of_crystallinity", "FFV", "fractional_free_volume",
    "nanofiber_diameter", "average_pore_size", "BET_surface_area",
    "d_spacing",
})


def _classify_output_channel(fact: dict) -> str:
    """Return performance | structure_feature | characterization_feature | formula_or_method_parameter."""
    if is_formula_method_parameter_fact(fact):
        return "formula_or_method_parameter"
    metric = str(fact.get("metric_or_parameter") or "")
    method = str(fact.get("method") or "")
    evidence = str(fact.get("evidence_text") or "")
    if is_characterization_peak_metric(metric, method=method, evidence=evidence):
        return "characterization_feature"
    # Check if metric is a structural feature
    canonical = find_metric_canonical(metric) or metric
    if canonical in _STRUCTURE_FEATURE_METRICS:
        return "structure_feature"
    return "performance"


def _fix_imidization_metric(fact: dict) -> dict:
    evidence = str(fact.get("evidence_text") or "")
    metric = str(fact.get("metric_or_parameter") or "")
    canonical = find_metric_canonical(metric) or metric
    if _IMIDIZATION_RE.search(evidence):
        if canonical in ("crystallinity_Xc", "crystallinity", "degree_of_crystallinity"):
            fact["metric_or_parameter"] = "imidization_degree"
            fact["_output_channel"] = "performance"
            fact["assignment_reason"] = _append_reason(
                fact.get("assignment_reason"), "metric_corrected_imidization_degree"
            )
        elif re.search(r"(?i)\bID\b|imidization\s+degree", evidence):
            fact["metric_or_parameter"] = "imidization_degree"
    return fact


def _resolve_metric_unit(fact: dict) -> dict:
    metric = str(fact.get("metric_or_parameter") or "")
    unit = str(fact.get("unit") or "")
    evidence = str(fact.get("evidence_text") or "")
    method = str(fact.get("method") or "")
    if not metric or not unit:
        return fact

    canonical = find_metric_canonical(metric) or canonicalize_metric_name(
        metric, method=method, evidence=evidence
    )
    inferred_ev = infer_metric_from_evidence(
        evidence, unit=unit, current_metric=metric
    )
    if inferred_ev:
        fact["metric_or_parameter"] = inferred_ev
        fact["assignment_reason"] = _append_reason(
            fact.get("assignment_reason"), "metric_inferred_from_evidence"
        )
        canonical = inferred_ev

    if metric_unit_compatible(canonical, unit):
        fact["metric_or_parameter"] = canonical
        return fact

    inferred = infer_metric_from_unit_mismatch(
        metric, unit, method=method, evidence=evidence
    )
    if inferred and metric_unit_compatible(inferred, unit):
        fact["metric_or_parameter"] = inferred
        fact["assignment_reason"] = _append_reason(
            fact.get("assignment_reason"), "metric_unit_corrected"
        )
        return fact

    fact["_metric_unit_mismatch"] = True
    fact["assignment_reason"] = _append_reason(
        fact.get("assignment_reason"), "metric_unit_mismatch"
    )
    return fact


def _enforce_pi1_aerogel_sample(fact: dict, sample_cards: list[dict]) -> dict:
    """If evidence names PI1, do not keep generic PI / PI aerogel without '1'."""
    evidence = str(fact.get("evidence_text") or "")
    if not _PI1_EVIDENCE_RE.search(evidence):
        return fact

    target = "PI1 aerogel"
    aerogel_match = _PI1_AEROGEL_RE.search(evidence)
    if aerogel_match:
        target = normalize_sample_id(aerogel_match.group(0))
        target = re.sub(r"(?i)pi\s*1", "PI1", target)
        if "aerogel" not in target.lower():
            target = "PI1 aerogel"

    for card in sample_cards:
        sid = str(card.get("sample_id") or "")
        if re.search(r"(?i)\bPI\s*1\b", sid) or normalize_for_match(sid).startswith("pi1"):
            target = sid
            break

    current = str(fact.get("assigned_sample_id") or "").strip()
    cur_norm = normalize_for_match(current)
    generic_pi = cur_norm in ("pi", "piaerogel", "pi aerogel") or bool(
        re.fullmatch(r"pi(?!\d)(?:\s+aerogel)?", cur_norm)
    )
    if generic_pi:
        fact["assigned_sample_id"] = target
        fact["candidate_sample_ids"] = [target]
        fact["assignment_reason"] = _append_reason(
            fact.get("assignment_reason"), "pi1_specific_sample_enforced"
        )
    return fact


def _sanitize_fact_sample(fact: dict) -> dict:
    evidence = str(fact.get("evidence_text") or "")
    sid = str(fact.get("assigned_sample_id") or "").strip()
    if not sid:
        return fact
    cleaned, cond, notes = sanitize_sample_id(sid, evidence)
    if cond:
        existing = str(fact.get("condition") or "").strip()
        fact["condition"] = f"{existing}; {cond}".strip("; ") if existing else cond
    if cleaned != sid:
        fact["assigned_sample_id"] = cleaned or None
        for note in notes:
            fact["assignment_reason"] = _append_reason(fact.get("assignment_reason"), note)
    return fact


def apply_pre_output_validation(
    facts: list[dict], sample_cards: list[dict] | None = None,
) -> list[dict]:
    """Run all pre-output checks on facts before building result records."""
    cards = sample_cards or []
    for i, fact in enumerate(facts):
        if fact.get("fact_type") != "performance":
            facts[i] = fact
            continue
        fact = _fix_imidization_metric(fact)
        fact = _enforce_pi1_aerogel_sample(fact, cards)
        fact = _sanitize_fact_sample(fact)
        channel = _classify_output_channel(fact)
        fact["_output_channel"] = channel
        if channel == "performance":
            if is_spurious_dielectric_fact(fact):
                fact["_evidence_audit_failed"] = True
                fact["assignment_reason"] = _append_reason(
                    fact.get("assignment_reason"), "spurious_dielectric_rejected"
                )
            else:
                fact = _resolve_metric_unit(fact)
        if channel == "characterization_feature":
            fact["assignment_reason"] = _append_reason(
                fact.get("assignment_reason"), "routed_characterization_feature"
            )
        elif channel == "structure_feature":
            fact["assignment_reason"] = _append_reason(
                fact.get("assignment_reason"), "routed_structure_feature"
            )
        elif channel == "formula_or_method_parameter":
            fact["assignment_reason"] = _append_reason(
                fact.get("assignment_reason"), "routed_formula_method_parameter"
            )
        facts[i] = fact
    return facts


def format_characterization_entry(result_fact: dict) -> str:
    metric = result_fact.get("canonical_metric") or result_fact.get("raw_metric") or ""
    value = result_fact.get("clean_value") or ""
    unit = result_fact.get("clean_unit") or ""
    method = result_fact.get("performance_method") or ""
    parts = [f"{metric}={value}"]
    if unit:
        parts[0] += unit
    if method:
        parts.append(f"({method})")
    return "".join(parts)


def merge_characterization_features(existing: str, entries: list[str]) -> str:
    merged: list[str] = []
    seen: set[str] = set()
    for chunk in (existing or "").split(";"):
        chunk = chunk.strip()
        if chunk and chunk not in seen:
            seen.add(chunk)
            merged.append(chunk)
    for entry in entries:
        entry = entry.strip()
        if entry and entry not in seen:
            seen.add(entry)
            merged.append(entry)
    return "; ".join(merged)
