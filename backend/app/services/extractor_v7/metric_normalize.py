"""Generic metric name canonicalization and spectroscopy peak numbering."""

from __future__ import annotations

import copy
import re
from collections import defaultdict

from app.services.metrics_dictionary import (
    find_metric_canonical,
    find_process_parameter_canonical,
    find_structure_feature_canonical,
)
from app.services.validation import normalize_unit

_GENERIC_PEAK_METRICS = frozenset({
    "wavenumber", "peak_position", "peak_position_2theta", "2theta", "two_theta",
    "binding_energy", "peak_intensity", "absorbance", "transmittance_peak",
    "raman_shift", "chemical_shift", "diffraction_angle",
})

_IMPLICIT_DIMENSIONLESS_METRICS = frozenset({
    "normalized_bandgap_frequency_range",
    "Poissons_ratio",
})

_SPECIFIC_TENSILE_STRENGTH_UNITS = frozenset({
    "cn/dtex",
    "g/denier",
    "n/tex",
    "n\u00b7m/g",
})
_SPECIFIC_TENSILE_MODULUS_UNITS = frozenset({"kn\u00b7m/g"})
_STRESS_UNITS = frozenset({"pa", "kpa", "mpa", "gpa"})
_STRESS_UNIT_DISPLAY = {
    "pa": "Pa",
    "kpa": "kPa",
    "mpa": "MPa",
    "gpa": "GPa",
}
_DUAL_TENSILE_VALUE_RE = re.compile(
    r"^\s*(?P<first>[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*"
    r"\(\s*(?P<second>[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*\)\s*$"
)
_TABLE_UNIT_TOKEN_RE = re.compile(
    r"(?i)g\s*/\s*denier|g\s+denier\s*(?:\^\s*)?-?1|"
    r"cN\s*/\s*dtex|N\s*/\s*tex|"
    r"k?N\s*(?:[.*]|\s)\s*m\s*(?:[.*]|\s)\s*g\s*(?:\^\s*)?-?1|"
    r"\b(?:MPa|GPa|kPa|Pa)\b"
)

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

_RELATIVE_CHANGE_RE = re.compile(
    r"(?i)\b(?:increas|improv|enhanc|growth|gain|higher|lower|"
    r"decreas|reduc|drop|rais|rose|fall)\w*\b|\bmore\s+than\b"
)
_TGA_CONTEXT_RE = re.compile(
    r"(?i)\b(?:tga|dtg|thermogravimetric|mass\s+loss|weight\s+loss|"
    r"thermal\s+decomposition|decomposition)\b"
)
_TGA_TEMPERATURE_LABEL_RE = re.compile(
    r"(?i)\b(?:t[_ -]?onset|t[_ -]?max|tmax|max[_ -]?t|"
    r"td\s*\d{0,2}\s*%?)\b"
)


def _compact_scientific_metric_label(value: str) -> str:
    text = str(value or "").lower().replace("$", "")
    text = re.sub(r"\\(?:mathrm|text)\s*\{([^{}]*)\}", r"\1", text)
    text = text.replace("\\", "")
    return re.sub(r"[\s{}_^]+", "", text)


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
    scientific_key = _compact_scientific_metric_label(raw)
    if scientific_key == "tg":
        return "glass_transition_temperature"
    if scientific_key == "tcc":
        return "cold_crystallization_temperature"
    if re.fullmatch(r"tm\d*", scientific_key):
        return "melting_temperature"
    if scientific_key in {"deltahcc", "dhcc"}:
        return "cold_crystallization_enthalpy"
    if scientific_key in {"deltahm", "dhm"}:
        return "melting_enthalpy"
    if scientific_key == "xc":
        return "crystallinity_Xc"
    if re.fullmatch(r"(?:td|t-?)\d+(?:\.\d+)?%?", scientific_key):
        return "decomposition_temperature"
    if raw_key == "specific_tensile_strength":
        return "specific_tensile_strength"
    if raw_key == "specific_tensile_modulus":
        return "specific_tensile_modulus"
    evidence_lower = (evidence or "").lower()
    unit_key = normalize_unit(unit)
    context_lower = f"{raw} {method} {evidence}".lower()
    relative_change = unit_key in {
        "%", "times", "fold", "dimensionless", "-",
    } and bool(
        _RELATIVE_CHANGE_RE.search(context_lower)
    )
    registered_metric = find_metric_canonical(raw)
    known_metric = registered_metric or raw_key

    if unit_key in _SPECIFIC_TENSILE_STRENGTH_UNITS and (
        known_metric in {"tensile_strength", "specific_tensile_strength"}
        or re.search(r"(?i)\btensile\s+strength\b", raw)
    ):
        return "specific_tensile_strength"
    if unit_key in _SPECIFIC_TENSILE_MODULUS_UNITS and (
        known_metric in {"Youngs_modulus", "specific_tensile_modulus"}
        or re.search(r"(?i)\b(?:young(?:'s|s)?|tensile)\s+modulus\b", raw)
    ):
        return "specific_tensile_modulus"
    if unit_key in {"mm", "cm", "m"} and re.search(
        r"(?i)\b(?:elongation|extension)\s+at\s+(?:break|fracture)\b",
        context_lower,
    ):
        return "extension_at_break"

    if relative_change:
        if re.search(r"(?i)\b(?:flexural|bending)\s+modulus\b", context_lower):
            return "flexural_modulus_improvement"
        if known_metric in {
            "compressive_strength", "compressive_strength_improvement",
            "compressive_stress",
        }:
            return "compressive_strength_improvement"
        if known_metric in {
            "flexural_strength", "flexural_strength_improvement",
        }:
            return "flexural_strength_improvement"
        if known_metric in {
            "Youngs_modulus", "Youngs_modulus_improvement",
        }:
            return "Youngs_modulus_improvement"
        if known_metric in {
            "thermal_conductivity", "thermal_conductivity_improvement",
        }:
            return "thermal_conductivity_improvement"

    strain_metric = (
        known_metric in {
            "elongation_at_break",
            "ultimate_strain",
            "ultimate_strain_improvement",
        }
        or bool(re.search(r"(?i)\b(?:strain|elongation)\b", raw))
    )
    ultimate_strain_context = strain_metric and bool(re.search(
        r"(?i)\bultimate(?:\s+tensile)?\s+strain\b|"
        r"\bstrain\s+at\s+failure\b|\bstrain\s+capacity\b",
        context_lower,
    ))
    if unit_key == "microstrain" and re.search(
        r"(?i)\bepsilon[_\s]*u\b|\bstrain\b|\belongation\b",
        context_lower,
    ):
        ultimate_strain_context = True
    if ultimate_strain_context:
        return (
            "ultimate_strain_improvement"
            if relative_change
            else "ultimate_strain"
        )

    fracture_metric = (
        known_metric in {
            "fracture_energy",
            "fracture_energy_improvement",
            "fracture_toughness",
            "fracture_toughness_improvement",
        }
        or bool(re.search(r"(?i)\bfracture\b", raw))
    )
    fracture_energy_context = fracture_metric and bool(re.search(
        r"(?i)\bfracture\s+energy\b",
        context_lower,
    ))
    if fracture_energy_context and (
        unit_key in {"j/m3", "kj/m3", ""}
        or raw_key in {"fracture_energy", "fracture_energy_improvement"}
    ):
        return (
            "fracture_energy_improvement"
            if relative_change
            else "fracture_energy"
        )

    if relative_change and (
        known_metric in {
            "fracture_toughness",
            "fracture_toughness_improvement",
            "interlaminar_fracture_toughness",
            "mode_I_interlaminar_fracture_toughness",
            "mode_II_interlaminar_fracture_toughness",
            "mode_I_interlaminar_fracture_toughness_improvement",
            "mode_II_interlaminar_fracture_toughness_improvement",
        }
        or (
            registered_metric is None
            and re.search(
                r"(?i)\b(?:interlaminar\s+)?fracture\s+toughness\b",
                context_lower,
            )
        )
    ):
        if re.search(r"(?i)\bmode[\s_-]*(?:ii|2)\b", context_lower):
            return "mode_II_interlaminar_fracture_toughness_improvement"
        if re.search(r"(?i)\bmode[\s_-]*(?:i|1)\b", context_lower):
            return "mode_I_interlaminar_fracture_toughness_improvement"
        return "fracture_toughness_improvement"

    if relative_change and (
        known_metric in {"tensile_strength", "tensile strength"}
        or re.search(r"(?i)\btensile\s+strength\b", raw)
    ):
        return "tensile_strength_improvement"

    if unit_key in {"mm", "cm", "m"} and re.search(
        r"(?i)\b(?:displacement|deformation)\b",
        context_lower,
    ):
        if re.search(
            r"(?i)\b(?:maximum|ultimate)\s+"
            r"(?:displacement|deformation)\b",
            context_lower,
        ):
            return "maximum_deformation"
        if re.search(
            r"(?i)\b(?:compress|soften|stiff|compliant|loading)\w*\b",
            context_lower,
        ):
            return "compressive_displacement"

    if (
        unit_key in {"°c", "k"}
        and _TGA_CONTEXT_RE.search(context_lower)
        and _TGA_TEMPERATURE_LABEL_RE.search(context_lower)
    ):
        return "decomposition_temperature"

    if str(unit or "").strip().lower() == "ph" and re.search(r"\bp\s*h\b", evidence_lower):
        return "pH"
    if raw_key in {
        "orientation_factor", "poisson_ratio", "poissons_ratio", "poisson_s_ratio", "surface_roughness",
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


def _dual_tensile_column_units(
    fact: dict,
) -> tuple[tuple[str, str], tuple[str, str]] | None:
    label = str(
        fact.get("_source_table_column_name") or fact.get("unit") or ""
    )
    if not re.search(r"(?i)\btensile\s+strength\b", label):
        return None
    tokens = [
        (match.start(), normalize_unit(match.group(0)))
        for match in _TABLE_UNIT_TOKEN_RE.finditer(label)
    ]
    specific = [
        (position, unit)
        for position, unit in tokens
        if unit in _SPECIFIC_TENSILE_STRENGTH_UNITS
    ]
    stress = [
        (position, unit)
        for position, unit in tokens
        if unit in _STRESS_UNITS
    ]
    if len(specific) != 1 or len(stress) != 1:
        return None
    if specific[0][0] < stress[0][0]:
        return ("specific", specific[0][1]), ("stress", stress[0][1])
    return ("stress", stress[0][1]), ("specific", specific[0][1])


def _expand_dual_tensile_table_facts(facts: list[dict]) -> list[dict]:
    expanded: list[dict] = []
    for fact in facts:
        if fact.get("fact_type") != "performance" or fact.get(
            "extraction_method"
        ) not in {"AI_holistic_table", "rule_table_performance"}:
            expanded.append(fact)
            continue
        value_match = _DUAL_TENSILE_VALUE_RE.fullmatch(
            str(fact.get("value") or "")
        )
        units = _dual_tensile_column_units(fact)
        if not value_match or not units:
            expanded.append(fact)
            continue

        base_fact_id = str(fact.get("fact_id") or "").strip()
        for index, (kind, unit) in enumerate(units, start=1):
            clone = copy.deepcopy(fact)
            clone["value"] = value_match.group(
                "first" if index == 1 else "second"
            )
            if kind == "specific":
                clone["metric_or_parameter"] = "specific_tensile_strength"
                clone["unit"] = unit
            else:
                clone["metric_or_parameter"] = "tensile_strength"
                clone["unit"] = _STRESS_UNIT_DISPLAY[unit]
            if base_fact_id:
                clone["fact_id"] = f"{base_fact_id}.{kind}"
            expanded.append(clone)
    return expanded


def normalize_metrics_in_facts(facts: list[dict]) -> list[dict]:
    """Apply canonical metric names and spectroscopy peak numbering."""
    facts = _expand_dual_tensile_table_facts(facts)
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
                "", "-", "ph", "ph units", "unitless",
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
