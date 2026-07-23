"""Deterministic evaluation of extraction results against a curated gold set."""

from __future__ import annotations

import math
import re
from typing import Any


DEFAULT_THRESHOLDS = {
    "precision": 0.97,
    "recall": 0.90,
    "sample_assignment_accuracy": 0.95,
    "unit_accuracy": 0.98,
    "evidence_coverage": 1.0,
    "document_type_accuracy": 1.0,
}


def _norm_text(value: Any) -> str:
    text = str(value or "").strip()
    text = (
        text.replace("’", "'")
        .replace("‘", "'")
        .replace("−", "-")
        .replace("–", "-")
    )
    return re.sub(r"\s+", " ", text).casefold()


def _norm_identifier(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _norm_text(value))


def _norm_unit(value: Any) -> str:
    normalized = _norm_text(value)
    normalized = normalized.replace("−", "-").replace("–", "-")
    normalized = normalized.replace("⁻", "-").replace("³", "3").replace("²", "2")
    normalized = normalized.replace("·", "").replace(" ", "")
    return normalized


def _numeric(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "")
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _value_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    expected_value = expected.get("value")
    actual_value = actual.get("value")
    expected_number = _numeric(expected_value)
    actual_number = _numeric(actual_value)
    if expected_number is None or actual_number is None:
        return _norm_text(expected_value) == _norm_text(actual_value)
    tolerance = expected.get("value_tolerance")
    if tolerance is None:
        tolerance = max(1e-9, abs(expected_number) * 1e-6)
    return math.isclose(
        expected_number,
        actual_number,
        rel_tol=0.0,
        abs_tol=float(tolerance),
    )


def _metric_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    aliases = [expected.get("metric"), *(expected.get("metric_aliases") or [])]
    actual_metric = _norm_identifier(actual.get("metric"))
    return actual_metric in {
        _norm_identifier(alias) for alias in aliases if str(alias or "").strip()
    }


def _sample_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    aliases = [expected.get("sample_id"), *(expected.get("sample_aliases") or [])]
    actual_sample = _norm_identifier(actual.get("sample_id"))
    return actual_sample in {
        _norm_identifier(alias) for alias in aliases if str(alias or "").strip()
    }


def _unit_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    aliases = [expected.get("unit"), *(expected.get("unit_aliases") or [])]
    actual_unit = _norm_unit(actual.get("unit"))
    return actual_unit in {
        _norm_unit(alias) for alias in aliases if str(alias or "").strip()
    }


def _evidence_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    evidence = _norm_text(actual.get("evidence"))
    required = expected.get("evidence_contains") or []
    if isinstance(required, str):
        required = [required]
    if required:
        return all(_norm_text(fragment) in evidence for fragment in required)
    return bool(evidence)


def _fact_label(fact: dict[str, Any]) -> dict[str, Any]:
    return {
        "sample_id": str(fact.get("sample_id") or ""),
        "metric": str(fact.get("metric") or ""),
        "value": str(fact.get("value") or ""),
        "unit": str(fact.get("unit") or ""),
    }


def _find_actual_paper(
    expected: dict[str, Any],
    actual_papers: list[dict[str, Any]],
) -> dict[str, Any] | None:
    expected_sha = _norm_text(expected.get("sha256"))
    expected_filename = _norm_text(expected.get("filename"))
    if expected_sha:
        for paper in actual_papers:
            if _norm_text(paper.get("sha256")) == expected_sha:
                return paper
    if expected_filename:
        for paper in actual_papers:
            if _norm_text(paper.get("filename")) == expected_filename:
                return paper
    return None


def _safe_ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator


def evaluate_gold_set(
    gold_payload: dict[str, Any],
    actual_papers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate one project snapshot and return an auditable gate result."""
    if int(gold_payload.get("schema_version") or 0) != 1:
        raise ValueError("Gold set schema_version must be 1")
    expected_papers = gold_payload.get("papers")
    if not isinstance(expected_papers, list) or not expected_papers:
        raise ValueError("Gold set must contain at least one paper")

    thresholds = dict(DEFAULT_THRESHOLDS)
    configured_thresholds = gold_payload.get("thresholds") or {}
    for key in thresholds:
        if key in configured_thresholds:
            thresholds[key] = float(configured_thresholds[key])
        if not 0 <= thresholds[key] <= 1:
            raise ValueError(f"Gold threshold {key} must be between 0 and 1")

    per_paper = []
    total_expected = 0
    total_actual_exhaustive = 0
    total_matched = 0
    total_aligned = 0
    total_sample_matched = 0
    total_unit_matched = 0
    total_evidence_matched = 0
    document_type_matches = 0
    found_papers = 0

    for expected_paper in expected_papers:
        actual_paper = _find_actual_paper(expected_paper, actual_papers)
        expected_facts = expected_paper.get("facts") or []
        if not isinstance(expected_facts, list):
            raise ValueError("Gold paper facts must be a list")
        exhaustive = bool(expected_paper.get("exhaustive", True))
        total_expected += len(expected_facts)

        if actual_paper is None:
            per_paper.append({
                "filename": expected_paper.get("filename", ""),
                "sha256": expected_paper.get("sha256", ""),
                "found": False,
                "document_type_match": False,
                "expected_facts": len(expected_facts),
                "actual_candidates": 0,
                "matched_facts": 0,
                "missing_facts": [_fact_label(fact) for fact in expected_facts],
                "unexpected_candidates": [],
            })
            continue

        found_papers += 1
        expected_type = _norm_text(expected_paper.get("document_type"))
        actual_type = _norm_text(actual_paper.get("document_type"))
        type_match = not expected_type or expected_type == actual_type
        document_type_matches += int(type_match)

        candidates = list(actual_paper.get("candidates") or [])
        paper_sample_aliases = expected_paper.get("sample_aliases") or {}
        normalized_paper_aliases = {
            _norm_identifier(sample_id): list(aliases or [])
            for sample_id, aliases in paper_sample_aliases.items()
        }
        if exhaustive:
            total_actual_exhaustive += len(candidates)
        unmatched_indexes = set(range(len(candidates)))
        missing_facts = []
        matched_details = []

        for source_expected_fact in expected_facts:
            expected_fact = dict(source_expected_fact)
            expected_fact["sample_aliases"] = [
                *(expected_fact.get("sample_aliases") or []),
                *normalized_paper_aliases.get(
                    _norm_identifier(expected_fact.get("sample_id")),
                    [],
                ),
            ]
            metric_value_matches = [
                index
                for index in unmatched_indexes
                if _metric_matches(expected_fact, candidates[index])
                and _value_matches(expected_fact, candidates[index])
            ]
            if metric_value_matches:
                aligned_index = max(
                    metric_value_matches,
                    key=lambda index: (
                        int(_sample_matches(expected_fact, candidates[index]))
                        + int(_unit_matches(expected_fact, candidates[index])),
                        int(_evidence_matches(expected_fact, candidates[index])),
                    ),
                )
                aligned = candidates[aligned_index]
                total_aligned += 1
                total_sample_matched += int(
                    _sample_matches(expected_fact, aligned)
                )
                total_unit_matched += int(_unit_matches(expected_fact, aligned))
                total_evidence_matched += int(
                    _evidence_matches(expected_fact, aligned)
                )
            full_matches = [
                index
                for index in metric_value_matches
                if _sample_matches(expected_fact, candidates[index])
                and _unit_matches(expected_fact, candidates[index])
            ]
            if not full_matches:
                missing_facts.append(_fact_label(expected_fact))
                continue

            selected_index = max(
                full_matches,
                key=lambda index: int(
                    _evidence_matches(expected_fact, candidates[index])
                ),
            )
            actual = candidates[selected_index]
            unmatched_indexes.remove(selected_index)
            evidence_match = _evidence_matches(expected_fact, actual)
            total_matched += 1
            matched_details.append({
                "expected": _fact_label(expected_fact),
                "actual": _fact_label(actual),
                "evidence_match": evidence_match,
            })

        unexpected = (
            [_fact_label(candidates[index]) for index in sorted(unmatched_indexes)]
            if exhaustive
            else []
        )
        per_paper.append({
            "filename": actual_paper.get("filename", ""),
            "sha256": actual_paper.get("sha256", ""),
            "found": True,
            "document_type_match": type_match,
            "expected_facts": len(expected_facts),
            "actual_candidates": len(candidates),
            "matched_facts": len(matched_details),
            "missing_facts": missing_facts,
            "unexpected_candidates": unexpected,
            "matched": matched_details,
        })

    precision = _safe_ratio(total_matched, total_actual_exhaustive)
    recall = _safe_ratio(total_matched, total_expected)
    if total_expected == 0:
        sample_accuracy = 1.0
        unit_accuracy = 1.0
        evidence_coverage = 1.0
    else:
        sample_accuracy = (
            total_sample_matched / total_aligned if total_aligned else 0.0
        )
        unit_accuracy = (
            total_unit_matched / total_aligned if total_aligned else 0.0
        )
        evidence_coverage = (
            total_evidence_matched / total_aligned if total_aligned else 0.0
        )
    document_type_accuracy = _safe_ratio(
        document_type_matches,
        len(expected_papers),
    )
    metrics = {
        "precision": precision,
        "recall": recall,
        "sample_assignment_accuracy": sample_accuracy,
        "unit_accuracy": unit_accuracy,
        "evidence_coverage": evidence_coverage,
        "document_type_accuracy": document_type_accuracy,
    }
    failures = [
        {
            "metric": key,
            "actual": round(metrics[key], 6),
            "required": threshold,
        }
        for key, threshold in thresholds.items()
        if metrics[key] < threshold
    ]
    if found_papers != len(expected_papers):
        failures.append({
            "metric": "paper_coverage",
            "actual": found_papers,
            "required": len(expected_papers),
        })

    return {
        "gold_name": str(gold_payload.get("name") or ""),
        "schema_version": 1,
        "gate_passed": not failures,
        "thresholds": thresholds,
        "metrics": {key: round(value, 6) for key, value in metrics.items()},
        "counts": {
            "expected_papers": len(expected_papers),
            "found_papers": found_papers,
            "expected_facts": total_expected,
            "actual_candidates_on_exhaustive_papers": total_actual_exhaustive,
            "matched_facts": total_matched,
        },
        "gate_failures": failures,
        "papers": per_paper,
    }
