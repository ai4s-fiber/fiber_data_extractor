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

_IMPLICIT_DIMENSIONLESS_METRICS = frozenset({
    "normalized_bandgap_frequency_range",
    "Poissons_ratio",
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


def canonicalize_metric_name(
    metric: str,
    *,
    method: str = "",
    evidence: str = "",
    unit: str = "",
) -> str:
    """Map raw metric labels to dictionary canonical names when possible."""
    raw = (metric or "").strip()
    if not raw:
        return raw
    raw_key = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    evidence_lower = (evidence or "").lower()
    if str(unit or "").strip().lower() == "ph" and re.search(r"\bp\s*h\b", evidence_lower):
        return "pH"
    if raw_key in {
        "orientation_factor", "poisson_ratio", "poissons_ratio", "surface_roughness",
    } and re.search(
        r"poisson(?:[’']s|s)?\s+ratio", evidence_lower,
    ):
        return "Poissons_ratio"
    if raw_key in {"orientation_factor", "fiber_content", "surface_roughness"} and re.search(
        r"fib(?:er|re)\s+(?:volume\s+)?(?:fraction|content|percentage)",
        evidence_lower,
    ):
        return "fiber_volume_fraction"
    if raw_key == "surface_roughness" and re.search(
        r"\b(?:compressive\s+)?displacement|displacement\s+deformation",
        evidence_lower,
    ):
        return "compressive_displacement"
    if (
        raw_key
        in {
            "softening_displacement",
            "re_stiffening_displacement",
            "restiffening_displacement",
            "stiffness_transition_displacement",
            "softening_transition_displacement",
        }
        and str(unit or "").strip().lower() == "mm"
        and re.search(r"\bdisplacement\b", evidence_lower)
        and re.search(r"\b(?:stiff|compliant|soften)\w*\b", evidence_lower)
    ):
        return "compressive_displacement"
    if re.search(r"\btransmission\b", evidence_lower) and re.search(
        r"\b(?:decay|attenuat|valley|reduction)\w*\b", evidence_lower,
    ):
        return "transmission_attenuation_frequency_range"
    if _is_generic_peak_metric(raw) and re.search(
        r"\b(?:directional\s+)?band\s*gap\b", evidence_lower,
    ):
        return "bandgap_frequency_range"
    if (
        raw_key == "maximum_acceleration"
        and str(unit or "").strip() == "%"
        and re.search(r"\b(?:decreas|reduc)\w*\b", evidence_lower)
    ):
        return "acceleration_reduction"
    without_unit = re.sub(r"\s*\[[^\[\]]+\]\s*$", "", raw).strip()
    latex_clean = re.sub(r"[${}]", "", without_unit).replace("\\", "")
    lookup_candidates = [raw]
    for candidate in (without_unit, latex_clean):
        if candidate and candidate not in lookup_candidates:
            lookup_candidates.append(candidate)
    for candidate in lookup_candidates:
        for resolver in (
            find_metric_canonical,
            find_structure_feature_canonical,
            find_process_parameter_canonical,
        ):
            canonical = resolver(candidate)
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
    if find_metric_canonical(metric):
        return False
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
        value_order: dict[str, int] = {}
        for fact in group_facts:
            value_key = re.sub(r"\s+", "", str(fact.get("value") or "")).lower()
            if value_key not in value_order:
                value_order[value_key] = len(value_order) + 1
            index = value_order[value_key]
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
        if fact.get("fact_type") == "process":
            base = re.sub(r"\s*(?:\([^()]*\)|\[[^\[\]]*\])\s*$", "", metric).strip()
            fact["metric_or_parameter"] = (
                find_process_parameter_canonical(base)
                or find_process_parameter_canonical(metric)
                or re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]+", "_", base.lower())).strip("_")
                or metric
            )
        else:
            canonical_metric = canonicalize_metric_name(
                metric,
                method=str(fact.get("method") or ""),
                evidence=str(fact.get("evidence_text") or ""),
                unit=str(fact.get("unit") or ""),
            )
            fact["metric_or_parameter"] = canonical_metric
            raw_unit = str(fact.get("unit") or "").strip().lower()
            if (
                canonical_metric in _IMPLICIT_DIMENSIONLESS_METRICS
                and raw_unit in {"", "-", "dimensionless", "unitless"}
            ) or (
                canonical_metric == "maximum_acceleration"
                and raw_unit in {"", "-", "dimensionless", "unitless"}
                and re.search(
                    r"\bdimensionless\s+(?:maximum\s+)?acceleration\b",
                    str(fact.get("evidence_text") or "").lower(),
                )
            ):
                fact["unit"] = "dimensionless"
            elif canonical_metric == "pH" and raw_unit in {
                "", "-", "ph", "unitless",
            }:
                fact["unit"] = "pH"
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
        if extraction_method == "rule_text_range":
            score += 20

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
        value_text = value.lower().replace("−", "-").replace("–", "-").replace("—", "-")
        operator = ""
        operator_match = re.match(r"\s*(<=|>=|<|>|~|≈)", value_text)
        if operator_match:
            operator = operator_match.group(1)
        numbers = re.findall(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", value_text)
        if numbers:
            normalized_numbers = []
            for number in numbers:
                try:
                    normalized_numbers.append(f"{float(number):g}")
                except ValueError:
                    normalized_numbers.append(number)
            value = f"{operator}|{'|'.join(normalized_numbers)}"
        else:
            value = value_text
        unit = str(fact.get("unit") or "").strip().lower()
        # Include condition in dedup key to protect different-condition records
        condition = str(fact.get("condition") or "").strip().lower()
        if not metric or not value:
            non_perf.append(fact)
            continue
        key = (sid, metric, value, unit, condition)
        current = best.get(key)
        if current is None or rank(fact) > rank(current):
            best[key] = fact

    exact_deduped = list(best.values())
    semantic_best: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)

    def normalized_evidence(fact: dict) -> str:
        return re.sub(
            r"[^a-z0-9]+",
            " ",
            str(fact.get("evidence_text") or "").lower(),
        ).strip()

    def condition_numbers(fact: dict) -> tuple[str, ...]:
        raw_condition = str(fact.get("condition") or "").replace("−", "-").replace("–", "-")
        condition = re.sub(
            r"(?i)\b[a-z]+\s*\^?\s*[-+]\s*\d+\b",
            "",
            raw_condition,
        )
        return tuple(sorted(
            f"{float(number):g}"
            for number in re.findall(
                r"[+-]?\d+(?:\.\d+)?",
                condition,
            )
        ))

    def source_page(fact: dict) -> int | None:
        try:
            if fact.get("_source_page") is not None:
                return int(fact["_source_page"])
        except (TypeError, ValueError):
            pass
        match = re.search(
            r"(?i)\b(?:p\.?|page)\s*(\d+)\b",
            str(fact.get("source_location") or ""),
        )
        return int(match.group(1)) if match else None

    for fact in exact_deduped:
        value_text = str(fact.get("value") or "").lower().replace("−", "-").replace("–", "-")
        numbers = re.findall(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", value_text)
        normalized_value = "|".join(f"{float(number):g}" for number in numbers) if numbers else value_text
        core_key = (
            str(fact.get("assigned_sample_id") or "").strip().lower(),
            str(fact.get("metric_or_parameter") or "").strip().lower(),
            normalized_value,
            str(fact.get("unit") or "").strip().lower(),
        )
        evidence = normalized_evidence(fact)
        duplicate_index: int | None = None
        for index, current in enumerate(semantic_best[core_key]):
            current_evidence = normalized_evidence(current)
            source_block_id = str(fact.get("_source_block_id") or "").strip()
            current_source_block_id = str(current.get("_source_block_id") or "").strip()
            same_source_block = bool(
                source_block_id and source_block_id == current_source_block_id
            )
            same_page_range = bool(
                core_key[1].endswith("range")
                and source_page(fact) is not None
                and source_page(fact) == source_page(current)
            )
            if not same_source_block and not same_page_range:
                if min(len(evidence), len(current_evidence)) < 30:
                    continue
                if not (evidence in current_evidence or current_evidence in evidence):
                    continue
                if condition_numbers(fact) != condition_numbers(current):
                    continue
            duplicate_index = index
            break
        if duplicate_index is None:
            semantic_best[core_key].append(fact)
            continue
        current = semantic_best[core_key][duplicate_index]
        if rank(fact) > rank(current):
            semantic_best[core_key][duplicate_index] = fact

    return non_perf + [
        fact
        for grouped_facts in semantic_best.values()
        for fact in grouped_facts
    ]
