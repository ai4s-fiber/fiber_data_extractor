"""Material science physical quantity validation — migrated from V5.

Provides:
- Metric name normalization (Chinese/English → standard snake_case)
- Unit normalization (various notations → canonical form)
- Metric-unit compatibility checking (e.g. tensile_strength ↔ MPa/GPa, not %)
- Sample ID quality assessment
"""

from __future__ import annotations

import re

from app.services.metrics_dictionary import find_metric_canonical

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
    "compressive stress": "compressive_stress",
    "compression stress": "compressive_stress",
    "压缩强度": "compressive_strength",
    "压缩应力": "compressive_stress",
    # Elongation
    "elongation at break": "elongation_at_break",
    "elongation": "elongation_at_break",
    "断裂伸长率": "elongation_at_break",
    "断裂伸长": "elongation_at_break",
    "strain at break": "elongation_at_break",
    "knee strain": "knee_strain",
    "strain at knee": "knee_strain",
    "damage transition strain": "damage_transition_strain",
    "stiffness recovery strain": "stiffness_recovery_strain",
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
    "ph": "pH",
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
    "loss tangent": "loss_tangent",
    "tan delta": "loss_tangent",
    "tan δ": "loss_tangent",
    "dissipation factor": "loss_tangent",
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
    bracketed = re.fullmatch(r"\[\s*([^\[\]]+?)\s*\]", text)
    if bracketed:
        text = bracketed.group(1)
    lower = text.lower().replace(" ", "")
    lower = lower.replace("·", "").replace("−", "-").replace("–", "-")
    mapping = {
        "mpa": "mpa",
        "gpa": "gpa",
        "kpa": "kpa",
        "pa": "pa",
        "%": "%",
        "%strain": "%",
        "strain%": "%",
        "percentstrain": "%",
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
        "ph": "ph",
        "mgcm-3": "mg/cm3",
        "mg/cm3": "mg/cm3",
        "g/cm3": "g/cm3",
        "g/cm³": "g/cm3",
        "kg/m3": "kg/m3",
        "kg/m^3": "kg/m3",
        "kg/m³": "kg/m3",
        "kgm^-3": "kg/m3",
        "kgm-3": "kg/m3",
        "kgm⁻³": "kg/m3",
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
    "compressive_stress": {"mpa", "gpa", "kpa", "pa"},
    "breaking_strength": {"mpa", "gpa", "kpa", "pa", "cn/dtex"},
    "Youngs_modulus": {"mpa", "gpa", "kpa", "pa"},
    "Poissons_ratio": {"-", "dimensionless"},
    "inelastic_threshold_stress": {"mpa", "gpa", "kpa", "pa"},
    "compressive_modulus": {"mpa", "gpa", "kpa", "pa"},
    "elongation_at_break": {"%"},
    "knee_strain": {"%"},
    "damage_transition_strain": {"%"},
    "stiffness_recovery_strain": {"%"},
    "electrical_conductivity": {"s/m", "s/cm", "ms/m"},
    "thermal_conductivity": {"w/mk", "mw/mk"},
    "water_contact_angle": {"degree"},
    "pH": {"ph", "-", "dimensionless"},
    "density": {"mg/cm3", "g/cm3", "kg/m3"},
    "limiting_oxygen_index": {"%"},
    "porosity": {"%"},
    "shrinkage": {"%"},
    "filtration_efficiency": {"%"},
    "dielectric_constant": {"-", "dimensionless"},
    "dielectric_loss": {"-", "dimensionless"},
    "loss_tangent": {"-", "dimensionless"},
    "surface_roughness": {"nm", "μm", "um", "µm", "å", "a", "angstrom", "angström"},
    "fiber_diameter": {"nm", "μm", "um", "µm"},
    "fiber_length": {"nm", "μm", "um", "µm", "mm"},
    "surface_temperature": {"°c", "k"},
    "glass_transition_temperature": {"°c", "k"},
    "imidization_degree": {"%"},
    "orientation_factor": {"-", "dimensionless"},
    "compressive_displacement": {"mm", "cm", "m", "μm", "um", "µm"},
    "softening_load": {"n", "kn", "mn"},
    "load_bearing_stability_improvement": {"%"},
    "bandgap_frequency_range": {"hz", "khz", "mhz"},
    "normalized_bandgap_frequency_range": {"-", "dimensionless"},
    "transmission_attenuation_frequency_range": {"hz", "khz", "mhz"},
    "eigenfrequency": {"hz", "khz", "mhz"},
    "maximum_acceleration": {"-", "dimensionless", "m/s2", "m/s²"},
    "acceleration_reduction": {"%"},
}

_DENSITY_UNITS = {"mg/cm3", "g/cm3", "kg/m3", "mg cm^-3", "g cm^-3", "kg m^-3"}
_LENGTH_UNITS = {"nm", "μm", "um", "µm", "å", "a", "angstrom", "angström", "mm", "μm"}
_TEMPERATURE_UNITS = {"°c", "k", "℃"}
_THERMAL_COND_UNITS = {"w/mk", "mw/mk", "w m^-1 k^-1", "mw m^-1 k^-1"}
_WAVENUMBER_UNITS = {"cm-1", "cm⁻¹", "1/cm", "cm^-1"}
_BINDING_ENERGY_UNITS = {"ev"}

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

    lower_metric = normalized_metric.lower()

    # Spectroscopy peak metrics: wavenumber or eV only
    if _looks_like_spectroscopy_peak_metric(lower_metric):
        if normalized_unit in _WAVENUMBER_UNITS:
            return True
        if normalized_unit in _BINDING_ENERGY_UNITS and "xps" in lower_metric:
            return True
        return False

    for metric_key, allowed_units in UNIT_RULES.items():
        if metric_key.lower() == lower_metric:
            return normalized_unit in allowed_units

    if lower_metric == "density":
        return normalized_unit in _DENSITY_UNITS
    if lower_metric == "surface_roughness":
        return normalized_unit in _LENGTH_UNITS
    if lower_metric == "thermal_conductivity":
        return normalized_unit in _THERMAL_COND_UNITS

    # Temperature readings: allow only for temperature-based metrics
    if normalized_unit in _TEMPERATURE_UNITS:
        return any(
            token in lower_metric
            for token in ("temperature", "tg", "tm", "td", "melting", "glass_transition", "decomposition")
        )

    # Unknown metric with known unit family — reject obvious cross-family pairs
    if normalized_unit in _DENSITY_UNITS:
        return "density" in lower_metric or "porosity" in lower_metric
    if normalized_unit in _LENGTH_UNITS:
        return any(
            token in lower_metric
            for token in ("roughness", "diameter", "thickness", "size", "width", "length", "fiber")
        )
    if normalized_unit in _WAVENUMBER_UNITS:
        return False
    if normalized_unit in _BINDING_ENERGY_UNITS:
        return "xps" in lower_metric or "binding" in lower_metric

    return True  # Unknown pairs: allow with lenient check


def _looks_like_spectroscopy_peak_metric(metric_lower: str) -> bool:
    if any(
        token in metric_lower
        for token in (
            "ftir_band", "raman_peak", "xps_peak", "xrd_peak", "nmr_shift",
            "wavenumber", "binding_energy", "peak_position", "raman_shift",
            "chemical_shift", "2theta", "diffraction_angle",
        )
    ):
        return True
    return bool(re.search(r"(?:_peak_|_band_|peak_\d|band_\d)", metric_lower))


def is_characterization_peak_metric(
    metric: str, *, method: str = "", evidence: str = "",
) -> bool:
    """FTIR/Raman/XPS/XRD/NMR peak positions are characterization, not core performance."""
    combined = f"{metric} {method} {evidence}".lower()
    if _looks_like_spectroscopy_peak_metric(
        (find_metric_canonical(metric) or metric).lower().replace(" ", "_")
    ):
        return True
    technique_hints = ("ftir", "raman", "xps", "xrd", "nmr", "wavenumber", "cm-1", "cm⁻¹", "2θ", "2theta", "binding energy")
    if any(h in combined for h in technique_hints):
        metric_lower = (metric or "").lower()
        if any(t in metric_lower for t in ("peak", "band", "wavenumber", "shift", "2theta")):
            return True
    return False


_IMIDIZATION_RE = re.compile(r"(?is)imidization|imidisation|imide|酰亚胺")
_IMIDIZATION_FORMULA_WAVENUMBERS = {1377.0, 1489.0, 1515.0, 1780.0}


def is_formula_method_parameter_fact(fact: dict) -> bool:
    """Reference peaks used in formulas (e.g. imidization ID), not performance values."""
    evidence = str(fact.get("evidence_text") or "").lower()
    metric = str(fact.get("metric_or_parameter") or "").strip().lower()
    if metric in {"elastic_wave_velocity", "reference_wave_velocity"}:
        combined = " ".join([
            evidence,
            str(fact.get("subject_text") or "").lower(),
            str(fact.get("method") or "").lower(),
            str(fact.get("condition") or "").lower(),
        ])
        symbol_definition = bool(re.search(
            r"\bwhere\s+c\s*_?\s*0\s*(?:is|=|denotes|represents)\b"
            r".{0,100}\b(?:elastic|reference)?\s*wave\s+velocity\b|"
            r"\bwhere\s+c\s*_?\s*0\s*(?:is|=)\s*(?:the\s+)?"
            r"(?:elastic|reference)?\s*wave\s+velocity\b",
            combined,
        ))
        if symbol_definition:
            return True
        has_formula_context = bool(re.search(
            r"\b(?:normalized?\s+frequency|equation|formula|expression)\b",
            combined,
        ))
        has_definition_context = bool(re.search(
            r"\b(?:where|used\s+to\s+(?:compute|calculate|normalize)|"
            r"(?:compute|calculate|normalize)[sd]?\s+(?:the\s+)?)\b",
            combined,
        ))
        if has_formula_context and has_definition_context:
            return True
    unit = normalize_unit(str(fact.get("unit") or ""))
    value_text = str(fact.get("value") or "").replace(",", "")
    if unit not in _WAVENUMBER_UNITS:
        return False
    try:
        value = float(re.search(r"[+-]?\d+(?:\.\d+)?", value_text).group())  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        return False

    formula_hints = (
        "imidization", "imidisation", "calculate", "calculated", "calculation",
        "reference peak", "reference band", "ratio", "equation", "formula",
        "used to determine", "used for determining", "degree of imidization",
    )
    if not any(h in evidence for h in formula_hints):
        return False

    if abs(value - 1377.0) < 2 or abs(value - 1489.0) < 2:
        return True
    if "reference" in evidence and unit in _WAVENUMBER_UNITS:
        return True
    return False


def infer_metric_from_unit_mismatch(
    metric: str, unit: str, *, method: str = "", evidence: str = "",
) -> str | None:
    """Re-infer metric name when unit contradicts the current label."""
    normalized_unit = normalize_unit(unit)
    lower_metric = (find_metric_canonical(metric) or metric).lower()
    combined = f"{method} {evidence}".lower()

    if normalized_unit in _DENSITY_UNITS:
        if lower_metric in ("surface_roughness", "fiber_diameter", "orientation_factor"):
            return "density"
        if "density" in combined or "apparent density" in combined:
            return "density"
    if normalized_unit in _LENGTH_UNITS and lower_metric == "density":
        if "roughness" in combined or re.search(r"\bra\b|\brq\b", combined):
            return "surface_roughness"
        if "diameter" in combined and "fiber" in combined:
            return "fiber_diameter"
        if "length" in combined and "fiber" in combined:
            return "fiber_length"
        return None
    if normalized_unit in _THERMAL_COND_UNITS and "thermal" not in lower_metric:
        return "thermal_conductivity"
    if normalized_unit in _WAVENUMBER_UNITS:
        technique = "ftir"
        if "raman" in combined:
            technique = "raman"
        elif "xrd" in combined or "2theta" in combined or "2θ" in combined:
            technique = "xrd"
        if technique == "ftir":
            return "FTIR_band_1"
        if technique == "raman":
            return "Raman_peak_1"
        if technique == "xrd":
            return "XRD_peak_1"
    if normalized_unit in _BINDING_ENERGY_UNITS:
        return "XPS_peak_1"
    if normalized_unit in {"%", "percent"} and _IMIDIZATION_RE.search(combined):
        return "imidization_degree"
    return None


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
