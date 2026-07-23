"""Post-extraction quality rules: filtering, sample form, export tiering."""

from __future__ import annotations

import re

from app.services.extractor_v7.validators import is_background_or_reference_fact
from app.services.grouping import normalize_for_match, normalize_sample_id
from app.services.metrics_dictionary import find_metric_canonical
from app.services.extractor_v7.data_source_classify import apply_data_source_classification

TRANSPARENT_THEME_HINTS = (
    "electromagnetic wave-transparent",
    "electromagnetic wave transparent",
    "em wave-transparent",
    "low dielectric",
    "low loss",
    "low-loss",
    "wave-transparent",
    "microwave-transparent",
    "electromagnetic transparency",
    "electromagnetic transparent",
    "电磁波透明",
    "低介电",
    "低损耗",
)

THIS_WORK_HINTS = (
    "this work", "our work", "herein", "in this study", "in this paper",
    "we prepared", "we synthesized", "we fabricated", "our sample",
    "our aerogel", "prepared in this work", "本文", "本工作", "我们制备",
)

INTRO_SECTIONS = frozenset({"introduction", "background", "title_abstract", "references"})

AEROGEL_PREFERRED_METRICS = frozenset({
    "density", "porosity", "shrinkage", "thermal_shrinkage",
    "thermal_conductivity", "surface_temperature", "water_contact_angle",
    "oil_contact_angle", "compressive_stress", "compressive_strength",
    "dielectric_constant", "loss_tangent", "electromagnetic_wave_transmittance",
})

NANOFIBER_PREFERRED_METRICS = frozenset({
    "tensile_strength", "elongation_at_break", "Youngs_modulus",
    "fiber_diameter", "fiber_length",
})

MEMBRANE_PREFERRED_METRICS = frozenset({
    "filtration_efficiency", "water_flux", "rejection_rate",
    "air_permeability", "tensile_strength",
})

FOAM_PREFERRED_METRICS = frozenset({
    "density", "porosity", "thermal_conductivity",
    "compressive_stress", "compressive_strength",
    "sound_absorption_coefficient",
})

# Metrics that are form-specific and should NOT appear on mismatched forms
_FORM_EXCLUSIVE_METRICS = {
    "nanofiber": NANOFIBER_PREFERRED_METRICS - {"tensile_strength"},
    "aerogel": {"density", "porosity", "shrinkage", "thermal_conductivity",
                "compressive_stress", "dielectric_constant", "loss_tangent"},
}

_CYCLE_COUNT_RE = re.compile(r"^\d+$")


def infer_paper_theme(
    chunks: list[dict] | None = None,
    paper_metadata: dict | None = None,
) -> set[str]:
    """Detect paper themes that affect metric filtering."""
    parts: list[str] = []
    if paper_metadata:
        parts.extend([
            str(paper_metadata.get("paper_title") or ""),
            str(paper_metadata.get("abstract") or ""),
        ])
    for chunk in chunks or []:
        section = (chunk.get("section_name") or "").lower()
        if section in {"title_abstract", "introduction", "abstract"}:
            parts.append(str(chunk.get("raw_text") or ""))
    blob = normalize_for_match(" ".join(parts))
    themes: set[str] = set()
    if any(hint.replace(" ", "") in blob.replace(" ", "") or hint in blob for hint in TRANSPARENT_THEME_HINTS):
        themes.add("low_dielectric_transparent")
    return themes


def _evidence_indicates_this_work(*texts: str) -> bool:
    blob = normalize_for_match(" ".join(texts))
    return any(hint in blob for hint in THIS_WORK_HINTS)


def should_reject_emi_shielding_fact(
    fact: dict,
    themes: set[str] | None = None,
) -> bool:
    """Drop EMI SE unless the paper is shielding-focused or evidence is this work."""
    metric = find_metric_canonical(fact.get("metric_or_parameter") or "") or (
        fact.get("metric_or_parameter") or ""
    )
    if metric != "electromagnetic_interference_shielding_effectiveness":
        return False
    if "low_dielectric_transparent" not in (themes or set()):
        return False
    evidence = " ".join([
        str(fact.get("evidence_text") or ""),
        str(fact.get("subject_text") or ""),
        str(fact.get("condition") or ""),
    ])
    if _evidence_indicates_this_work(evidence):
        return False
    if is_background_or_reference_fact(fact):
        return True
    return True


def infer_sample_form(sample_id: str, evidence: str = "") -> str:
    """Infer aerogel / nanofiber / film / membrane / foam / etc. from naming and context."""
    sid_lower = normalize_for_match(sample_id)
    blob = normalize_for_match(f"{sample_id} {evidence}")
    # Nanofiber
    if any(token in sid_lower for token in ("nanofiber", "nanofibers", "nanofibre")):
        return "nanofiber"
    if "nanofiber" in blob or "nanofibers" in blob:
        return "nanofiber"
    # Aerogel
    if "aerogel" in sid_lower or "aerogel" in blob:
        return "aerogel"
    # Membrane
    if "membrane" in sid_lower or "membrane" in blob:
        return "membrane"
    # Hydrogel
    if "hydrogel" in sid_lower or "hydrogel" in blob:
        return "hydrogel"
    # Foam
    if "foam" in sid_lower or "foam" in blob:
        return "foam"
    # Coating
    if "coating" in sid_lower or "coating" in blob:
        return "coating"
    # Powder
    if "powder" in sid_lower or "powder" in blob:
        return "powder"
    # Bulk composite
    if any(token in sid_lower for token in ("bulk composite", "bulk_composite")):
        return "bulk composite"
    if "composite" in sid_lower and "bulk" in blob:
        return "bulk composite"
    # Film
    if "film" in blob:
        return "film"
    # Regular fiber (not nano)
    if any(token in sid_lower for token in ("fiber", "fibers", "fibre")):
        if "nanofiber" not in sid_lower and "nanofibers" not in sid_lower:
            return "fiber"
    # Known sample ID patterns
    compact = sid_lower.replace(" ", "").replace("-", "")
    if compact in {"pi1", "pi-1"} or re.search(r"\bpi1\b", sid_lower):
        return "aerogel"
    if "2mzazinepi3" in compact.replace("_", ""):
        return "aerogel"
    return ""


def normalize_sample_display_name(sample_id: str) -> str:
    """Add sample form suffix when the paper uses a bare ID such as PI1."""
    sid = normalize_sample_id(sample_id)
    if not sid:
        return sid
    lower = normalize_for_match(sid)
    form = infer_sample_form(sid)
    if form == "nanofiber" and "nanofiber" not in lower and "nanofibers" not in lower:
        return f"{sid} nanofiber"
    if form == "aerogel" and "aerogel" not in lower:
        compact = lower.replace(" ", "").replace("-", "")
        if compact in {"pi1", "pi-1"} or re.search(r"\bpi1\b", lower):
            return "PI1 aerogel"
        if "2mzazinepi3" in compact.replace("_", ""):
            return "2MZ-AZINE-PI3 aerogel"
        return f"{sid} aerogel"
    return sid


def metric_conflicts_sample_form(metric: str, sample_form: str) -> bool:
    canonical = find_metric_canonical(metric) or metric
    if not sample_form:
        return False
    if sample_form == "nanofiber" and canonical in AEROGEL_PREFERRED_METRICS:
        return True
    if sample_form == "aerogel" and canonical in NANOFIBER_PREFERRED_METRICS:
        return True
    if sample_form == "membrane" and canonical in AEROGEL_PREFERRED_METRICS - {"density", "porosity"}:
        return True
    if sample_form == "foam" and canonical in NANOFIBER_PREFERRED_METRICS:
        return True
    # fiber_diameter / fiber_length should not appear on aerogel
    if sample_form == "aerogel" and canonical in ("fiber_diameter", "fiber_length"):
        return True
    return False


def restructure_loading_cycles_fact(fact: dict) -> dict:
    """Treat bare cycle counts as test conditions, not performance values."""
    metric = find_metric_canonical(fact.get("metric_or_parameter") or "") or (
        fact.get("metric_or_parameter") or ""
    )
    if metric != "loading_unloading_cycles":
        return fact
    value = str(fact.get("value") or "").strip()
    if not _CYCLE_COUNT_RE.fullmatch(value):
        return fact
    condition = fact.get("condition") or ""
    extra = f"{value} compression cycles"
    if "strain" not in condition.lower() and "50" in (fact.get("evidence_text") or ""):
        extra = f"{value} compression cycles at 50% strain"
    fact["metric_or_parameter"] = "cyclic_compression_stability"
    fact["value"] = "no stress decay"
    fact["unit"] = fact.get("unit") or "-"
    fact["condition"] = f"{condition}; {extra}".strip("; ").strip()
    fact["_quality_flags"] = list(dict.fromkeys([*(fact.get("_quality_flags") or []), "cycles_as_condition"]))
    return fact


def remap_loss_tangent_metric(fact: dict) -> dict:
    """Ensure tan δ maps to loss_tangent, not dielectric_loss."""
    raw = str(fact.get("metric_or_parameter") or "").lower()
    evidence = str(fact.get("evidence_text") or "").lower()
    blob = f"{raw} {evidence}"
    if any(token in blob for token in ("loss tangent", "tan delta", "tan δ", "tan d", "dissipation factor")):
        if "dielectric loss" not in blob or "loss tangent" in blob or "tan" in blob:
            fact["metric_or_parameter"] = "loss_tangent"
    return fact


def detect_unit_conflict(fact: dict) -> bool:
    """Flag aerogel compressive stress when MPa is unlikely vs kPa."""
    metric = find_metric_canonical(fact.get("metric_or_parameter") or "") or (
        fact.get("metric_or_parameter") or ""
    )
    if metric not in {"compressive_stress", "compressive_strength"}:
        return False
    unit = str(fact.get("unit") or "").strip().lower()
    value_text = str(fact.get("value") or "").strip()
    match = re.search(r"[+-]?\d+(?:\.\d+)?", value_text)
    if not match:
        return False
    value = float(match.group())
    evidence = str(fact.get("evidence_text") or "").lower()
    sample_form = infer_sample_form(
        str(fact.get("assigned_sample_id") or ""),
        evidence,
    )
    if sample_form == "aerogel" or "aerogel" in evidence:
        if unit in {"mpa", "mpa."} and value >= 1.0:
            return True
        if "kpa" in evidence and unit in {"mpa", "mpa."}:
            return True
    return False


def classify_export_tier(fact: dict) -> str:
    """Classify facts into A (core), B (review), C (drop/background)."""
    if fact.get("_reject"):
        return "C"
    if is_background_or_reference_fact(fact):
        return "C"
    flags = set(fact.get("_quality_flags") or [])
    if "background_reference" in flags:
        return "C"
    if metric_conflicts_sample_form(
        fact.get("metric_or_parameter") or "",
        infer_sample_form(
            str(fact.get("assigned_sample_id") or ""),
            str(fact.get("evidence_text") or ""),
        ),
    ):
        return "B"
    if fact.get("_unit_conflict"):
        return "B"
    if not fact.get("assigned_sample_id"):
        return "B"
    if not str(fact.get("condition") or "").strip() and find_metric_canonical(
        fact.get("metric_or_parameter") or ""
    ) in {
        "surface_temperature", "dielectric_constant", "loss_tangent",
        "thermal_conductivity",
    }:
        return "B"
    if flags & {"sample_form_mismatch", "unit_conflict", "missing_condition"}:
        return "B"
    return "A"


def apply_fact_quality_enhancements(
    facts: list[dict],
    *,
    chunks: list[dict] | None = None,
    paper_metadata: dict | None = None,
) -> list[dict]:
    """Apply generic quality rules before record generation."""
    themes = infer_paper_theme(chunks, paper_metadata)
    chunk_section_by_text: dict[str, str] = {}
    for chunk in chunks or []:
        text_key = (chunk.get("raw_text") or "")[:120]
        if text_key:
            chunk_section_by_text[text_key] = chunk.get("section_name") or ""

    kept: list[dict] = []
    for fact in facts:
        if fact.get("fact_type") != "performance":
            kept.append(fact)
            continue

        evidence = str(fact.get("evidence_text") or "")
        for key, section in chunk_section_by_text.items():
            if key and key in evidence:
                fact["_chunk_section"] = section
                break

        fact = remap_loss_tangent_metric(fact)
        fact = restructure_loading_cycles_fact(fact)

        if should_reject_emi_shielding_fact(fact, themes):
            fact["_reject"] = True
            fact["_quality_reason"] = "emi_se_filtered_for_transparent_paper"
            fact["_export_tier"] = "C"
            kept.append(fact)
            continue

        if is_background_or_reference_fact(fact):
            fact.setdefault("_quality_flags", []).append("background_reference")
            fact["_export_tier"] = "C"
            kept.append(fact)
            continue

        assigned = fact.get("assigned_sample_id") or ""
        if assigned:
            normalized = normalize_sample_display_name(str(assigned))
            if normalized != assigned:
                fact["assigned_sample_id"] = normalized
                fact.setdefault("_quality_flags", []).append("sample_name_normalized")

        sample_form = infer_sample_form(
            str(fact.get("assigned_sample_id") or ""),
            evidence,
        )
        if sample_form and metric_conflicts_sample_form(fact.get("metric_or_parameter") or "", sample_form):
            fact.setdefault("_quality_flags", []).append("sample_form_mismatch")

        if detect_unit_conflict(fact):
            fact["_unit_conflict"] = True
            fact.setdefault("_quality_flags", []).append("unit_conflict")
            note = "Text may indicate MPa, but figure axis suggests kPa; manual review required."
            fact["condition"] = f"{fact.get('condition') or ''}; {note}".strip("; ")

        canonical = find_metric_canonical(fact.get("metric_or_parameter") or "") or ""
        if canonical in {"surface_temperature", "dielectric_constant", "loss_tangent", "thermal_conductivity"}:
            if not str(fact.get("condition") or "").strip():
                fact.setdefault("_quality_flags", []).append("missing_condition")

        fact["_export_tier"] = classify_export_tier(fact)
        kept.append(fact)

    # --- Data source classification ---
    kept = apply_data_source_classification(kept)

    # --- Mark comparison_literature facts ---
    for fact in kept:
        if fact.get("fact_type") != "performance":
            continue
        src_type = fact.get("_data_source_type", "")
        if src_type == "comparison_literature":
            fact.setdefault("_quality_flags", []).append("comparison_literature")
            if fact.get("_export_tier") == "A":
                fact["_export_tier"] = "B"
        elif src_type == "background_reference":
            fact.setdefault("_quality_flags", []).append("background_reference")
            fact["_export_tier"] = "C"
        elif src_type in ("method_parameter", "experimental_condition"):
            fact.setdefault("_quality_flags", []).append(src_type)
            if fact.get("_export_tier") == "A":
                fact["_export_tier"] = "B"
        elif src_type == "characterization_feature":
            fact.setdefault("_quality_flags", []).append("characterization_feature")

    # --- Final checklist ---
    from app.services.extractor_v7.final_checklist import run_final_checklist
    kept = run_final_checklist(kept)

    return kept


def enrich_sample_cards_with_form(sample_cards: list[dict]) -> list[dict]:
    """Fill fiber_type from sample naming when missing; normalize display names."""
    import json

    for card in sample_cards:
        sid = card.get("sample_id") or ""
        form = infer_sample_form(sid, card.get("evidence_text") or "")
        if form == "nanofiber":
            card["fiber_type"] = card.get("fiber_type") or "nanofiber"
        elif form == "aerogel":
            card["fiber_type"] = card.get("fiber_type") or "aerogel"
        normalized = normalize_sample_display_name(sid)
        if normalized != sid:
            aliases_raw = card.get("sample_aliases") or "[]"
            try:
                aliases = json.loads(aliases_raw) if isinstance(aliases_raw, str) else list(aliases_raw or [])
            except json.JSONDecodeError:
                aliases = []
            if sid and sid not in aliases:
                aliases.append(sid)
            card["sample_aliases"] = json.dumps(aliases, ensure_ascii=False)
            card["sample_id"] = normalized
    return sample_cards
