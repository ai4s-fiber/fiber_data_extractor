"""Generic metric name canonicalization and spectroscopy peak numbering."""

from __future__ import annotations

import re
from collections import defaultdict

from app.services.metrics_dictionary import (
    find_metric_canonical,
    find_process_parameter_canonical,
    find_structure_feature_canonical,
)

_GENERIC_PEAK_METRICS = frozenset({
    "wavenumber", "peak_position", "peak_position_2theta", "2theta", "two_theta",
    "binding_energy", "peak_intensity", "absorbance", "transmittance_peak",
    "raman_shift", "chemical_shift", "diffraction_angle",
})

_TECHNIQUE_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("xrd", ("xrd", "x-ray diffraction", "diffract", "2theta", "2θ", "bragg")),
    ("ftir", ("ftir", "ft-ir", "infrared", "ir spectrum", "wavenumber", "cm-1", "cm⁻¹")),
    ("raman", ("raman", "raman shift")),
    ("xps", ("xps", "x-ray photoelectron", "binding energy")),
    ("dsc", ("dsc", "differential scanning calorimetry")),
    ("tga", ("tga", "thermogravimetric")),
]

_PHASE_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("alpha", ("alpha", "α", "alpha-phase", "α-phase")),
    ("beta", ("beta", "β", "beta-phase", "β-phase")),
    ("gamma", ("gamma", "γ", "gamma-phase")),
]


def _infer_technique(*texts: str) -> str:
    blob = " ".join(texts).lower()
    for technique, hints in _TECHNIQUE_HINTS:
        if any(hint in blob for hint in hints):
            return technique
    return "spectroscopy"


def _infer_phase(*texts: str) -> str:
    blob = " ".join(texts).lower()
    for phase, hints in _PHASE_HINTS:
        if any(hint in blob for hint in hints):
            return phase
    return ""


def canonicalize_metric_name(metric: str, *, method: str = "", evidence: str = "") -> str:
    """Map raw metric labels to dictionary canonical names when possible."""
    raw = (metric or "").strip()
    if not raw:
        return raw
    for resolver in (
        find_metric_canonical,
        find_structure_feature_canonical,
        find_process_parameter_canonical,
    ):
        canonical = resolver(raw)
        if canonical:
            return canonical
    lower = raw.lower().replace(" ", "_")
    if lower in _GENERIC_PEAK_METRICS or any(token in lower for token in ("peak", "band", "2theta")):
        return raw
    normalized = re.sub(r"[^a-z0-9_]+", "_", lower)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or raw


def _is_generic_peak_metric(metric: str) -> bool:
    lower = (metric or "").strip().lower().replace(" ", "_")
    if lower in _GENERIC_PEAK_METRICS:
        return True
    return bool(re.search(r"(peak|band|2theta|wavenumber|binding_energy)", lower))


def _numbered_peak_name(technique: str, phase: str, index: int) -> str:
    if phase:
        if technique == "ftir":
            return f"{phase}_phase_FTIR_band_{index}"
        if technique == "xrd":
            return f"{phase}_phase_XRD_peak_{index}"
        return f"{phase}_phase_{technique}_peak_{index}"
    if technique == "ftir":
        return f"FTIR_band_{index}"
    if technique == "xrd":
        return f"XRD_peak_{index}"
    return f"{technique}_peak_{index}"


def normalize_spectroscopy_peaks(facts: list[dict]) -> list[dict]:
    """Rename generic peak metrics into numbered technique-specific names per sample."""
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)

    for fact in facts:
        if fact.get("fact_type") != "performance":
            continue
        metric = fact.get("metric_or_parameter") or ""
        if not _is_generic_peak_metric(metric):
            continue
        sid = (fact.get("assigned_sample_id") or "").strip()
        technique = _infer_technique(
            metric,
            fact.get("method") or "",
            fact.get("evidence_text") or "",
            fact.get("source_location") or "",
        )
        phase = _infer_phase(
            metric,
            fact.get("evidence_text") or "",
            fact.get("condition") or "",
        )
        groups[(sid, technique, phase)].append(fact)

    for group_facts in groups.values():
        group_facts.sort(
            key=lambda f: (
                float(re.search(r"[+-]?\d+(?:\.\d+)?", str(f.get("value") or "0")).group())
                if re.search(r"[+-]?\d+(?:\.\d+)?", str(f.get("value") or ""))
                else 0.0
            )
        )
        for index, fact in enumerate(group_facts, 1):
            technique = _infer_technique(
                fact.get("metric_or_parameter") or "",
                fact.get("method") or "",
                fact.get("evidence_text") or "",
            )
            phase = _infer_phase(
                fact.get("metric_or_parameter") or "",
                fact.get("evidence_text") or "",
            )
            fact["metric_or_parameter"] = _numbered_peak_name(technique, phase, index)

    return facts


def normalize_metrics_in_facts(facts: list[dict]) -> list[dict]:
    """Apply canonical metric names and spectroscopy peak numbering."""
    for fact in facts:
        metric = fact.get("metric_or_parameter") or ""
        if not metric:
            continue
        fact["metric_or_parameter"] = canonicalize_metric_name(
            metric,
            method=str(fact.get("method") or ""),
            evidence=str(fact.get("evidence_text") or ""),
        )
    return normalize_spectroscopy_peaks(facts)


def merge_duplicate_facts(facts: list[dict]) -> list[dict]:
    """Deduplicate facts by sample + canonical metric + value + condition, keeping best evidence.

    Evidence priority: Figure/Table > Results text > Conclusion > Abstract.
    Facts with different conditions are NOT de-duped even if they share sample/metric/value.
    """
    # Section priority weights (lower = higher priority)
    _section_rank = {
        "results": 0,
        "conclusion": 1,
        "experimental": 2,
        "title_abstract": 3,
        "introduction": 4,
        "background": 5,
    }

    best: dict[tuple[str, str, str, str], dict] = {}

    def rank(fact: dict) -> int:
        score = 0
        extraction_method = fact.get("extraction_method") or ""
        source_location = str(fact.get("source_location") or "").lower()
        section = str(fact.get("_chunk_section") or "").lower()

        # Method bonuses
        if extraction_method == "AI_holistic":
            score += 8
        if extraction_method == "AI_table":
            score += 6
        if extraction_method == "AI_figure":
            score += 5

        # Source location bonuses
        if "table" in source_location:
            score += 6
        if "fig" in source_location:
            score += 5

        # Section priority (results > conclusion > abstract)
        section_score = _section_rank.get(section, 3)
        score += max(0, 5 - section_score)  # results=5, conclusion=4, ...

        if fact.get("assigned_sample_id"):
            score += 2
        if fact.get("evidence_text"):
            score += min(len(str(fact.get("evidence_text"))), 200)
        return score

    non_perf: list[dict] = []
    for fact in facts:
        if fact.get("fact_type") != "performance":
            non_perf.append(fact)
            continue
        sid = (fact.get("assigned_sample_id") or "").strip().lower()
        metric = (fact.get("metric_or_parameter") or "").strip().lower()
        value = str(fact.get("value") or "").strip()
        # Include condition in dedup key to protect different-condition records
        condition = str(fact.get("condition") or "").strip().lower()
        if not metric or not value:
            non_perf.append(fact)
            continue
        key = (sid, metric, value, condition)
        current = best.get(key)
        if current is None or rank(fact) > rank(current):
            best[key] = fact

    return non_perf + list(best.values())
