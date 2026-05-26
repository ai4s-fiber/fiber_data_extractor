"""Material science physical quantity validation — migrated from V5.

Provides:
- Metric name normalization (Chinese/English → standard snake_case)
- Unit normalization (various notations → canonical form)
- Metric-unit compatibility checking (e.g. tensile_strength ↔ MPa/GPa, not %)
- Sample ID quality assessment
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Metric name ↔ standard snake_case mapping
# ---------------------------------------------------------------------------

METRIC_MAP: dict[str, str] = {
    # Tensile / mechanical
    "tensile strength": "tensile_strength",
    "拉伸强度": "tensile_strength",
    "breaking strength": "tensile_strength",
    "ultimate tensile strength": "tensile_strength",
    # Compressive
    "compressive strength": "compressive_strength",
    "compression strength": "compressive_strength",
    "压缩强度": "compressive_strength",
    # Elongation
    "elongation at break": "elongation_at_break",
    "elongation": "elongation_at_break",
    "断裂伸长率": "elongation_at_break",
    "断裂伸长": "elongation_at_break",
    "strain at break": "elongation_at_break",
    # Modulus
    "young''s modulus": "Youngs_modulus",
    "young's modulus": "Youngs_modulus",
    "youngs modulus": "Youngs_modulus",
    "young modulus": "Youngs_modulus",
    "杨氏模量": "Youngs_modulus",
    "elastic modulus": "Youngs_modulus",
    "modulus": "Youngs_modulus",
    # Electrical conductivity
    "electrical conductivity": "electrical_conductivity",
    "electric conductivity": "electrical_conductivity",
    "conductivity": "electrical_conductivity",
    "电导率": "electrical_conductivity",
    # Thermal conductivity
    "thermal conductivity": "thermal_conductivity",
    "热导率": "thermal_conductivity",
    # Contact angle
    "water contact angle": "water_contact_angle",
    "contact angle": "water_contact_angle",
    "wca": "water_contact_angle",
    "接触角": "water_contact_angle",
    # LOI
    "limiting oxygen index": "limiting_oxygen_index",
    "loi": "limiting_oxygen_index",
    "极限氧指数": "limiting_oxygen_index",
    # UL-94
    "ul-94": "UL94_rating",
    "ul94": "UL94_rating",
    # Density
    "density": "density",
    "密度": "density",
    "apparent density": "density",
    # Other common metrics
    "porosity": "porosity",
    "孔隙率": "porosity",
    "shrinkage": "shrinkage",
    "收缩率": "shrinkage",
    "dielectric constant": "dielectric_constant",
    "permittivity": "dielectric_constant",
    "介电常数": "dielectric_constant",
    "dielectric loss": "dielectric_loss",
    "loss tangent": "dielectric_loss",
    "tan delta": "dielectric_loss",
    "介电损耗": "dielectric_loss",
    "surface temperature": "surface_temperature",
    "filtration efficiency": "filtration_efficiency",
    "过滤效率": "filtration_efficiency",
    "fiber diameter": "fiber_diameter",
    "纤维直径": "fiber_diameter",
    "fiber length": "fiber_length",
    "纤维长度": "fiber_length",
    "imidization degree": "imidization_degree",
    "酰亚胺化程度": "imidization_degree",
}


def normalize_metric_name(metric: str) -> str:
    """Map raw metric text to standard English snake_case name."""
    metric = metric.strip()
    lower = metric.lower()
    for key, value in METRIC_MAP.items():
        if key in lower:
            return value
    # Fallback: replace non-alphanumeric with underscores
    normalized = re.sub(r"[^0-9A-Za-z]+", "_", metric).strip("_")
    return normalized or metric


# ---------------------------------------------------------------------------
# Unit normalization
# ---------------------------------------------------------------------------


def normalize_unit(unit: str) -> str:
    """Normalize unit strings to canonical forms for comparison."""
    text = str(unit or "").strip()
    lower = text.lower().replace(" ", "")
    lower = lower.replace("·", "").replace("−", "-").replace("–", "-")
    mapping = {
        "mpa": "mpa",
        "gpa": "gpa",
        "kpa": "kpa",
        "pa": "pa",
        "%": "%",
        "s/m": "s/m",
        "sm-1": "s/m",
        "s/cm": "s/cm",
        "scm-1": "s/cm",
        "ms/m": "ms/m",
        "w/(mk)": "w/mk",
        "w/mk": "w/mk",
        "wm-1k-1": "w/mk",
        "mw/mk": "mw/mk",
        "mwm-1k-1": "mw/mk",
        "degree": "degree",
        "degrees": "degree",
        "deg": "degree",
        "°": "degree",
        "mgcm-3": "mg/cm3",
        "mg/cm3": "mg/cm3",
        "g/cm3": "g/cm3",
        "g/cm³": "g/cm3",
        "kg/m3": "kg/m3",
        "°c": "°c",
        "℃": "°c",
        "cn/dtex": "cn/dtex",
        "n/mm2": "mpa",
        "n/mm²": "mpa",
    }
    return mapping.get(lower, lower)


# ---------------------------------------------------------------------------
# Metric-unit compatibility matrix
# ---------------------------------------------------------------------------

UNIT_RULES: dict[str, set[str]] = {
    "tensile_strength": {"mpa", "gpa", "kpa", "pa", "cn/dtex"},
    "compressive_strength": {"mpa", "gpa", "kpa", "pa"},
    "breaking_strength": {"mpa", "gpa", "kpa", "pa", "cn/dtex"},
    "Youngs_modulus": {"mpa", "gpa", "kpa", "pa"},
    "elongation_at_break": {"%"},
    "electrical_conductivity": {"s/m", "s/cm", "ms/m"},
    "thermal_conductivity": {"w/mk", "mw/mk"},
    "water_contact_angle": {"degree"},
    "density": {"mg/cm3", "g/cm3", "kg/m3"},
    "limiting_oxygen_index": {"%"},
    "porosity": {"%"},
    "shrinkage": {"%"},
    "filtration_efficiency": {"%"},
    "dielectric_constant": {"-", "dimensionless"},
    "dielectric_loss": {"-", "dimensionless"},
}

# Structural metrics that should NOT appear as performance
STRUCTURAL_METRIC_PATTERNS = (
    "imidization",
    "酰亚胺",
    "ftir",
    "peak",
    "stretching vibration",
    "crystallinity",
    "晶度",
    "beta phase",
    "β",
    "pore size",
    "porosity",
    "fiber diameter",
    "fiber length",
    "nanofiber diameter",
)


def metric_unit_compatible(metric: str, unit: str) -> bool:
    """Check if a metric-unit pair is physically plausible."""
    normalized_metric = normalize_metric_name(metric)
    normalized_unit = normalize_unit(unit)
    if not normalized_metric or not normalized_unit:
        return False

    # Check structural metrics first — they shouldn't be treated as performance
    if any(p in normalized_metric.lower() for p in STRUCTURAL_METRIC_PATTERNS):
        return False

    for metric_key, allowed_units in UNIT_RULES.items():
        if metric_key.lower() == normalized_metric.lower():
            return normalized_unit in allowed_units

    # Temperature readings are not performance metrics
    if normalized_unit in ("°c",):
        return False

    return True  # Unknown metrics: allow through with lenient check


def looks_like_structure_metric(metric: str, method: str = "", category: str = "") -> bool:
    """Detect if a metric is actually a structure characterization, not performance."""
    combined = f"{metric} {method} {category}".lower()
    return any(pattern in combined for pattern in STRUCTURAL_METRIC_PATTERNS)


# ---------------------------------------------------------------------------
# Sample ID quality
# ---------------------------------------------------------------------------

SAMPLE_STOPWORDS = {
    "abstract", "addressing", "advanced", "aladdin", "alpha", "article",
    "beijing", "biochemical", "characterization", "characterizations",
    "chemical", "chen", "china", "co", "college", "compass",
    "correspondingly", "current", "experimental", "figure", "ftir",
    "key", "laboratory", "ltd", "materials", "photograph", "photographs",
    "raman", "scientific", "sem", "shanghai", "shengli", "sigma",
    "specifically", "state", "synthesis", "table", "university",
    "waxs", "xps", "xrd",
}


def looks_like_candidate_sample_token(value: str) -> bool:
    """Heuristic: does this token look like a real material sample ID?"""
    token = value.strip(" ,.;:()[]")
    if not (2 <= len(token) <= 36):
        return False
    lower = token.lower()
    if lower in SAMPLE_STOPWORDS:
        return False
    # Single proper-cased word (e.g. "University") is not a sample
    if re.fullmatch(r"[A-Z][a-z]{2,}", token):
        return False
    # Purely alphabetic without separator is not a sample
    if re.fullmatch(r"[A-Za-z]+", token) and "-" not in token and "/" not in token:
        return False
    # Must contain a number or separator pattern
    if re.search(r"\d", token):
        return True
    if "-" in token or "/" in token or "_" in token:
        parts = re.split(r"[-_/]", token)
        if any(part.lower() in SAMPLE_STOPWORDS for part in parts):
            return False
        uppercase_like = sum(1 for part in parts if re.search(r"[A-Z0-9]{2,}", part))
        return uppercase_like >= 2
    # Known base polymer abbreviations
    return token in {"PI", "PAA", "PET", "PVDF", "PAN", "PP", "PVA", "PLA", "PA6"}


def looks_like_reviewable_sample_id(
    sample_id: str, payload: dict[str, str] | None = None, confidence: float = 0.0
) -> bool:
    """Stricter: should this sample ID enter a review table?"""
    sample_id = sample_id.strip()
    if not sample_id:
        return False
    if looks_like_candidate_sample_token(sample_id):
        # Base abbreviations need extra context
        if sample_id in {"PI", "PAA", "PET", "PVDF", "PAN", "PP", "PVA", "PLA", "PA6"}:
            context = " ".join((payload or {}).values()).lower()
            return confidence >= 0.65 or any(
                kw in context for kw in ("sample", "aerogel", "fiber", "composition")
            )
        return True
    return False


# ---------------------------------------------------------------------------
# Row-level quality check utilities
# ---------------------------------------------------------------------------


def check_value_range(metric: str, value_str: str) -> str:
    """Return a warning string if the value is suspicious for the given metric."""
    try:
        value = float(re.sub(r"[^\d.]", "", value_str))
    except ValueError:
        return ""
    metric_lower = metric.lower()
    if "elongation" in metric_lower and value > 800:
        return f"断裂伸长率过大 ({value}%)，请人工确认"
    if "tensile" in metric_lower and "strength" in metric_lower and value > 5000:
        return f"拉伸强度数值极大 ({value} MPa)，超出常见聚合物纤维范畴"
    if "thermal_conductivity" in metric_lower and value > 100:
        return f"热导率数值异常 ({value})，常见聚合物 < 1 W/mK"
    if "density" in metric_lower and value > 20:
        return f"密度数值异常 ({value} g/cm³)，常见聚合物 < 2.5"
    if "contact_angle" in metric_lower and (value < 0 or value > 180):
        return f"接触角超出 [0, 180] 范围: {value}"
    return ""
