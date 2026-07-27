"""Post-extraction quality rules: filtering, sample form, export tiering."""

from __future__ import annotations

import copy
import re

from app.services.extractor_v7.validators import is_background_or_reference_fact
from app.services.extractor_v7.sample_identity import parse_sample_aliases
from app.services.grouping import normalize_for_match, normalize_sample_id
from app.services.metrics_dictionary import (
    find_metric_canonical,
    find_process_parameter_canonical,
)
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
_FIGURE_LABEL_RE = re.compile(
    r"(?i)\bfig(?:ure)?\.?\s*(\d+)(?:\s*[a-z])?\b"
)
_GENERIC_CAPTION_SAMPLE_IDS = frozenset({
    "control", "reference", "sample", "specimen", "composite", "material",
})
_VARIANT_MAPPING_VALUE = (
    r"\d+(?:\.\d+)?\s*(?:(?:wt|vol|mol)\s*)?%"
)
_VARIANT_MAPPING_CODE = r"[A-Za-z]{1,6}\d{1,3}"
_VARIANT_CODE_MAPPING_RE = re.compile(
    rf"(?is)(?P<values>{_VARIANT_MAPPING_VALUE}"
    rf"(?:\s*(?:,\s*(?:and\s+)?|\s+and\s+){_VARIANT_MAPPING_VALUE}){{1,10}})"
    r".{0,100}?\b(?:named|called|denoted|label(?:ed|led)|designated|"
    r"coded(?:\s+as)?)\b\s*"
    rf"(?P<codes>{_VARIANT_MAPPING_CODE}"
    rf"(?:\s*(?:,\s*(?:and\s+)?|\s+and\s+){_VARIANT_MAPPING_CODE}){{1,10}})"
    r"\s*,?\s*respectively"
)
_VARIANT_LOADING_NAME_RE = re.compile(
    r"(?i)\b(?:loading|content|fraction|concentration|dosage)\b"
)


def _figure_numbers(text: str) -> set[str]:
    return {match.group(1) for match in _FIGURE_LABEL_RE.finditer(text or "")}


def _caption_mentions_sample_id(caption: str, sample_id: str) -> bool:
    normalized = normalize_for_match(sample_id)
    if not normalized or normalized in _GENERIC_CAPTION_SAMPLE_IDS:
        return False
    tokens = re.findall(r"[A-Za-z0-9]+", sample_id)
    if not tokens:
        return False
    pattern = r"[\s_./-]*".join(re.escape(token) for token in tokens)
    return bool(re.search(
        rf"(?<![A-Za-z0-9]){pattern}(?![A-Za-z0-9])",
        caption or "",
        re.IGNORECASE,
    ))


def _append_assignment_reason(existing: str | None, addition: str) -> str:
    parts = [part.strip() for part in str(existing or "").split(";") if part.strip()]
    if addition not in parts:
        parts.append(addition)
    return "; ".join(parts)


def _apply_figure_caption_sample_anchors(
    facts: list[dict],
    chunks: list[dict] | None,
    sample_cards: list[dict] | None,
) -> list[dict]:
    known_sample_ids = list(dict.fromkeys(
        normalize_sample_id(card.get("sample_id") or "")
        for card in sample_cards or []
        if normalize_sample_id(card.get("sample_id") or "")
    ))
    if not known_sample_ids:
        return facts

    captions_by_figure: dict[str, list[tuple[list[str], str]]] = {}
    for chunk in chunks or []:
        caption = str(chunk.get("raw_text") or "").strip()
        figure_numbers = _figure_numbers(caption)
        if not caption or not figure_numbers:
            continue
        source_type = str(chunk.get("source_type") or "").lower()
        block_type = str(chunk.get("block_type") or "").lower()
        caption_like = (
            source_type == "figure_caption"
            or block_type in {"chart", "figure", "image"}
            or bool(re.match(r"(?i)^\s*fig(?:ure)?\.?\s*\d+", caption))
        )
        if not caption_like:
            continue
        matches = [
            sample_id
            for sample_id in known_sample_ids
            if _caption_mentions_sample_id(caption, sample_id)
        ]
        for figure_number in figure_numbers:
            captions_by_figure.setdefault(figure_number, []).append((matches, caption))

    anchors: dict[str, tuple[str, str]] = {}
    for figure_number, entries in captions_by_figure.items():
        if any(len(matches) > 1 for matches, _ in entries):
            continue
        matched_ids = {
            matches[0]
            for matches, _ in entries
            if len(matches) == 1
        }
        if len(matched_ids) != 1:
            continue
        sample_id = next(iter(matched_ids))
        caption_text = "\n".join(dict.fromkeys(
            caption for matches, caption in entries if matches == [sample_id]
        ))
        anchors[figure_number] = (sample_id, caption_text)

    for fact in facts:
        if fact.get("fact_type") != "performance":
            continue
        source_figures = _figure_numbers(str(fact.get("source_location") or ""))
        matched_anchors = {
            anchors[figure_number]
            for figure_number in source_figures
            if figure_number in anchors
        }
        sample_ids = {sample_id for sample_id, _ in matched_anchors}
        if len(sample_ids) != 1:
            continue
        sample_id = next(iter(sample_ids))
        captions = "\n".join(dict.fromkeys(
            caption for anchored_id, caption in matched_anchors
            if anchored_id == sample_id
        ))
        fact["assigned_sample_id"] = sample_id
        fact["candidate_sample_ids"] = [sample_id]
        fact["assignment_status"] = "assigned"
        fact["assignment_confidence"] = max(
            float(fact.get("assignment_confidence") or 0),
            0.97,
        )
        fact["assignment_reason"] = _append_assignment_reason(
            fact.get("assignment_reason"),
            "figure_caption_sample_anchor",
        )
        evidence = str(fact.get("evidence_text") or "").strip()
        if captions and normalize_for_match(captions) not in normalize_for_match(evidence):
            fact["evidence_text"] = "\n".join(
                part for part in (evidence, f"[figure caption] {captions}") if part
            )
    return facts


_COLLECTIVE_FIGURE_SCOPE_RE = re.compile(
    r"(?i)\b(?:results?|data|distributions?|curves?|profiles?)\s+"
    r"(?:shown|presented|reported|plotted)\s+in\s+fig(?:ure)?\.?\s*\d+\b|"
    r"\bfig(?:ure)?\.?\s*\d+\s+(?:shows?|presents?|compares?)\b.*"
    r"\b(?:samples?|composites?|specimens?|groups?)\b"
)
_SUBFIGURE_SCOPE_RE = re.compile(
    r"(?i)\bfig(?:ure)?\.?\s*\d+\s*\(?[a-z]\)|"
    r"\b(?:panel|subfigure)\s*\(?[a-z]\)?"
)


def _expand_collective_figure_caption_anchors(
    facts: list[dict],
    chunks: list[dict] | None,
    sample_cards: list[dict] | None,
) -> list[dict]:
    """Expand a figure-level result that explicitly covers several subfigures."""
    known_sample_ids = list(dict.fromkeys(
        normalize_sample_id(card.get("sample_id") or "")
        for card in sample_cards or []
        if normalize_sample_id(card.get("sample_id") or "")
    ))
    if not known_sample_ids:
        return facts

    anchors: dict[str, tuple[list[str], str]] = {}
    for chunk in chunks or []:
        caption = str(chunk.get("raw_text") or "").strip()
        figure_numbers = _figure_numbers(caption)
        if not caption or not figure_numbers:
            continue
        source_type = str(chunk.get("source_type") or "").lower()
        block_type = str(chunk.get("block_type") or "").lower()
        if not (
            source_type == "figure_caption"
            or block_type in {"chart", "figure", "image"}
            or re.match(r"(?i)^\s*fig(?:ure)?\.?\s*\d+", caption)
        ):
            continue
        matches = [
            sample_id
            for sample_id in known_sample_ids
            if _caption_mentions_sample_id(caption, sample_id)
        ]
        if not 2 <= len(matches) <= 4:
            continue
        for figure_number in figure_numbers:
            existing = anchors.get(figure_number)
            if existing and set(existing[0]) != set(matches):
                anchors.pop(figure_number, None)
                continue
            anchors[figure_number] = (matches, caption)

    if not anchors:
        return facts

    existing_keys = {
        (
            str(fact.get("_source_block_id") or fact.get("source_block_id") or ""),
            str(fact.get("metric_or_parameter") or ""),
            _normalized_number(fact.get("value")),
            str(fact.get("unit") or "").lower(),
            normalize_sample_id(fact.get("assigned_sample_id") or ""),
        )
        for fact in facts
    }
    expanded: list[dict] = []
    for fact in facts:
        if fact.get("fact_type") != "performance":
            expanded.append(fact)
            continue
        source_text = str(fact.get("source_location") or "")
        evidence = str(fact.get("evidence_text") or "")
        source_figures = _figure_numbers(source_text)
        matched = [
            (figure_number, *anchors[figure_number])
            for figure_number in source_figures
            if figure_number in anchors
        ]
        if len(matched) != 1:
            expanded.append(fact)
            continue
        figure_number, sample_ids, caption = matched[0]
        if (
            figure_number not in _figure_numbers(evidence)
            or not _COLLECTIVE_FIGURE_SCOPE_RE.search(evidence)
            or _SUBFIGURE_SCOPE_RE.search(f"{source_text} {evidence}")
            or any(
                _caption_mentions_sample_id(evidence, sample_id)
                for sample_id in sample_ids
            )
        ):
            expanded.append(fact)
            continue
        current = normalize_sample_id(fact.get("assigned_sample_id") or "")
        if current in sample_ids:
            expanded.append(fact)
            continue

        base_id = str(fact.get("fact_id") or "F")
        created = 0
        for index, sample_id in enumerate(sample_ids, start=1):
            key = (
                str(fact.get("_source_block_id") or fact.get("source_block_id") or ""),
                str(fact.get("metric_or_parameter") or ""),
                _normalized_number(fact.get("value")),
                str(fact.get("unit") or "").lower(),
                sample_id,
            )
            if key in existing_keys:
                continue
            clone = copy.deepcopy(fact)
            clone["fact_id"] = f"{base_id}.fig{figure_number}.{index}"
            clone["assigned_sample_id"] = sample_id
            clone["candidate_sample_ids"] = [sample_id]
            clone["assignment_status"] = "assigned"
            clone["assignment_confidence"] = max(
                float(clone.get("assignment_confidence") or 0),
                0.96,
            )
            clone["assignment_reason"] = _append_assignment_reason(
                clone.get("assignment_reason"),
                "collective_figure_caption_sample_anchor",
            )
            if normalize_for_match(caption) not in normalize_for_match(evidence):
                clone["evidence_text"] = "\n".join(
                    part
                    for part in (evidence, f"[figure caption] {caption}")
                    if part
                )
            expanded.append(clone)
            existing_keys.add(key)
            created += 1
        if not created and not all(
            (
                str(fact.get("_source_block_id") or fact.get("source_block_id") or ""),
                str(fact.get("metric_or_parameter") or ""),
                _normalized_number(fact.get("value")),
                str(fact.get("unit") or "").lower(),
                sample_id,
            ) in existing_keys
            for sample_id in sample_ids
        ):
            expanded.append(fact)
    return expanded


_COMPOSITION_COMPONENT_FIRST_RE = re.compile(
    r"(?i)\b(?P<component>[A-Za-z][A-Za-z0-9-]{0,20})\s*(?:=|:)\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?:(?:wt|vol|mol)\s*)?%"
)
_COMPOSITION_VALUE_FIRST_RE = re.compile(
    r"(?i)(?P<value>\d+(?:\.\d+)?)\s*(?:(?:wt|vol|mol)\s*)?%\s*"
    r"(?:of\s+)?(?P<component>[A-Za-z][A-Za-z0-9-]{0,20})\b"
)
_COMPOSITION_COMPONENT_STOPWORDS = frozenset({
    "and", "by", "composite", "composition", "content", "fiber", "fibers",
    "increase",
    "hybrid", "increased", "material", "organic", "respectively", "sample",
    "strength", "the", "total", "with",
})
_VARIABLE_TOKEN_STOPWORDS = frozenset({
    "amount", "concentration", "content", "fiber", "fibers", "fibre",
    "fibres", "fraction", "level", "loading", "mass", "of", "on",
    "percentage", "the", "total", "weight", "wt",
})


def _normalized_number(value: object) -> str:
    match = re.search(r"[+-]?\d+(?:\.\d+)?", str(value or ""))
    if not match:
        return ""
    try:
        return f"{float(match.group()):g}"
    except ValueError:
        return ""


def _composition_signature(text: str) -> dict[str, str]:
    signature: dict[str, str] = {}
    for pattern in (
        _COMPOSITION_COMPONENT_FIRST_RE,
        _COMPOSITION_VALUE_FIRST_RE,
    ):
        for match in pattern.finditer(text or ""):
            component = normalize_for_match(match.group("component"))
            if component in _COMPOSITION_COMPONENT_STOPWORDS:
                continue
            value = _normalized_number(match.group("value"))
            if component and value:
                signature.setdefault(component, value)
    return signature


def _append_sample_card_evidence(fact: dict, card: dict) -> None:
    sample_id = normalize_sample_id(card.get("sample_id") or "")
    if not sample_id:
        return
    details: list[str] = []
    variable = " ".join(
        str(card.get(field) or "").strip()
        for field in ("variable_name", "variable_value", "variable_unit")
    ).strip()
    if variable:
        details.append(variable)
    composition = str(card.get("composition_expression") or "").strip()
    if composition:
        details.append(composition)
    card_evidence = str(card.get("evidence_text") or "").strip()
    if card_evidence and card_evidence not in details:
        details.append(card_evidence)
    marker = f"[sample card evidence] {sample_id}"
    evidence = str(fact.get("evidence_text") or "").strip()
    if marker in evidence:
        return
    addition = f"{marker}: {'; '.join(details)}".rstrip(": ")
    fact["evidence_text"] = "\n".join(part for part in (evidence, addition) if part)


def _assign_fact_sample(fact: dict, sample_id: str, reason: str) -> None:
    fact["assigned_sample_id"] = sample_id
    fact["candidate_sample_ids"] = [sample_id]
    fact["assignment_status"] = "assigned"
    fact["assignment_confidence"] = max(
        float(fact.get("assignment_confidence") or 0),
        0.95,
    )
    fact["assignment_reason"] = _append_assignment_reason(
        fact.get("assignment_reason"),
        reason,
    )
    fact.pop("_alignment_review_required", None)


_LOADING_FORM_TOKENS = frozenset({
    "aerogel", "bicomponent", "compound", "composite", "core", "fiber",
    "fibers", "filament", "filaments", "film", "foam", "laminate",
    "membrane", "nanocomposite", "nanofiber", "preform", "sheath", "yarn",
})


def _local_fact_value_evidence(fact: dict) -> str:
    evidence = str(fact.get("evidence_text") or "")
    target = _normalized_number(fact.get("value"))
    if not target:
        return evidence
    target_pattern = re.compile(
        rf"(?<![\d.]){re.escape(target)}(?![\d.])"
    )
    for clause in re.split(r"(?<=[.!?])\s+|[\r\n]+", evidence):
        if target_pattern.search(clause):
            return clause
    return evidence


def _sample_card_form_tokens(card: dict) -> set[str]:
    text = " ".join([
        str(card.get("sample_id") or ""),
        " ".join(parse_sample_aliases(
            card.get("sample_aliases") or card.get("aliases")
        )),
        str(card.get("fiber_type") or ""),
        str(card.get("material_system") or ""),
        str(card.get("composition_expression") or ""),
    ]).lower()
    return set(re.findall(r"[a-z]+", text)) & _LOADING_FORM_TOKENS


def _rebind_loading_specific_samples(
    facts: list[dict],
    sample_cards: list[dict],
) -> list[dict]:
    """Apply an explicit loading to the final sample, preserving material form."""
    from app.services.extractor_v7.sample_value_alignment import (
        _prefer_loading_specific_sample,
    )

    cards_by_id = {
        normalize_for_match(card.get("sample_id") or ""): card
        for card in sample_cards
        if normalize_sample_id(card.get("sample_id") or "")
    }
    for fact in facts:
        if fact.get("fact_type") != "performance":
            continue
        current = normalize_sample_id(fact.get("assigned_sample_id") or "")
        condition = str(fact.get("condition") or "")
        if not current or not re.search(
            r"(?i)(?<![\d.])\d+(?:\.\d+)?\s*(?:wt|vol|mol)\.?\s*%",
            condition,
        ):
            continue
        preferred = _prefer_loading_specific_sample(
            current,
            condition,
            sample_cards,
        )
        local_evidence = _local_fact_value_evidence(fact)
        local_forms = (
            set(re.findall(r"[a-z]+", local_evidence.lower()))
            & _LOADING_FORM_TOKENS
        )
        if local_forms:
            form_cards = [
                card
                for card in sample_cards
                if _sample_card_form_tokens(card) & local_forms
            ]
            form_preferred = _prefer_loading_specific_sample(
                current,
                condition,
                form_cards,
            )
            if normalize_for_match(form_preferred) != normalize_for_match(current):
                preferred = form_preferred
        if normalize_for_match(preferred) == normalize_for_match(current):
            continue
        card = cards_by_id.get(normalize_for_match(preferred))
        if not card:
            continue
        _assign_fact_sample(
            fact,
            normalize_sample_id(card.get("sample_id") or preferred),
            "loading_specific_sample_rebound",
        )
        _append_sample_card_evidence(fact, card)
    return facts


def _ensure_sample_card(
    sample_cards: list[dict],
    *,
    sample_id: str,
    evidence: str,
    material_system: str,
    fiber_type: str = "",
) -> dict:
    normalized = normalize_for_match(sample_id)
    for card in sample_cards:
        if normalize_for_match(card.get("sample_id") or "") == normalized:
            return card
    card = {
        "sample_id": sample_id,
        "sample_aliases": [],
        "sample_group_id": "G000",
        "material_system": material_system,
        "fiber_type": fiber_type,
        "composition_expression": material_system,
        "source_location": "deterministic evidence repair",
        "evidence_text": evidence,
        "confidence": 0.95,
    }
    sample_cards.append(card)
    return card


def _repair_composition_formula_assignments(
    facts: list[dict],
    sample_cards: list[dict],
) -> list[dict]:
    card_signatures = [
        (
            card,
            _composition_signature(
                str(card.get("composition_expression") or "")
            ),
        )
        for card in sample_cards
    ]
    for fact in facts:
        if fact.get("fact_type") != "performance":
            continue
        signature = _composition_signature(str(fact.get("condition") or ""))
        if len(signature) < 2:
            continue
        matches = [
            card
            for card, card_signature in card_signatures
            if signature.items() <= card_signature.items()
        ]
        if len(matches) != 1:
            continue
        card = matches[0]
        sample_id = normalize_sample_id(card.get("sample_id") or "")
        if not sample_id:
            continue
        _assign_fact_sample(fact, sample_id, "composition_signature_sample_match")
        _append_sample_card_evidence(fact, card)
    return facts


_COMPONENT_LOADING_METRICS = {
    "mk": "mk_wt",
    "pp": "fiber_wt_pp",
    "pva": "fiber_wt_pva",
    "ws": "fiber_wt_ws",
}


def _repair_component_loading_semantics(facts: list[dict]) -> list[dict]:
    """Route an explicitly named wt% constituent away from performance metrics."""
    for fact in facts:
        if fact.get("fact_type") != "performance":
            continue
        unit = re.sub(r"\s+", "", str(fact.get("unit") or "").lower())
        if unit not in {"wt%", "wt.%"}:
            continue
        target = _normalized_number(fact.get("value"))
        if not target:
            continue
        context = " ".join([
            str(fact.get("evidence_text") or ""),
            str(fact.get("condition") or ""),
        ])
        direct_pattern = (
            rf"(?i)(?<![\d.]){re.escape(target)}(?![\d.])\s*wt\s*%\s*"
            r"(?:of\s+)?(?P<component>MK|PP|PVA|WS)\b"
        )
        components = {
            match.group("component").lower()
            for match in re.finditer(direct_pattern, context)
        }
        if not components:
            reverse_pattern = (
                r"(?i)\b(?P<component>MK|PP|PVA|WS)\b\s*"
                r"(?:content\s*)?(?:of\s*)?[:=]?\s*"
                rf"(?<![\d.]){re.escape(target)}(?![\d.])\s*wt\s*%"
            )
            components = {
                match.group("component").lower()
                for match in re.finditer(reverse_pattern, context)
            }
        if len(components) != 1:
            continue
        component = next(iter(components))
        fact["fact_type"] = "composition"
        fact["category"] = "composition"
        fact["metric_or_parameter"] = _COMPONENT_LOADING_METRICS[component]
        fact["assignment_reason"] = _append_assignment_reason(
            fact.get("assignment_reason"),
            "explicit_component_loading_routed_to_composition",
        )
        fact.pop("_alignment_review_required", None)
        fact.pop("_checklist_failed", None)
        fact.pop("_checklist_failures", None)
        fact["_alignment_verified"] = True
    return facts


_FAMILY_SAMPLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "mineral_organic_hybrid_fiber_reinforced_geopolymer",
        re.compile(
            r"(?i)\bmineral[- ]organic\s+hybrid\s+fib(?:er|re)\s+"
            r"reinforced\s+geopolymers?\b"
        ),
    ),
    (
        "organic_hybrid_fiber_reinforced_geopolymer",
        re.compile(
            r"(?i)(?<!mineral-)(?<!mineral )\borganic\s+hybrid\s+"
            r"fib(?:er|re)\s+reinforced\s+geopolymers?\b"
        ),
    ),
)
_NANOPARTICLE_SUBJECT_RE = re.compile(
    r"(?i)\b(?P<material>[A-Za-z][A-Za-z0-9-]{1,30})\s+nanoparticles?\b"
    r".{0,140}?\b(?:diameters?|particle\s+sizes?|sizes?)\b"
)


def _explicit_sample_code_appears(sample_id: str, evidence: str) -> bool:
    if not re.fullmatch(r"[A-Za-z]{1,4}\d{1,3}", sample_id or ""):
        return False
    return bool(re.search(
        rf"(?i)(?<![A-Za-z0-9]){re.escape(sample_id)}(?![A-Za-z0-9])",
        evidence or "",
    ))


def _family_sample_for_fact(fact: dict) -> tuple[str, str] | None:
    condition = str(fact.get("condition") or "")
    evidence = str(fact.get("evidence_text") or "")
    current = normalize_sample_id(fact.get("assigned_sample_id") or "")
    if len(_composition_signature(condition)) >= 2:
        return None
    if _explicit_sample_code_appears(current, evidence):
        return None

    condition_matches = [
        (sample_id, match.group(0))
        for sample_id, pattern in _FAMILY_SAMPLE_PATTERNS
        if (match := pattern.search(condition))
    ]
    if len(condition_matches) == 1:
        return condition_matches[0]

    target = _normalized_number(fact.get("value"))
    value_positions = [
        match.start()
        for match in re.finditer(r"[+-]?\d+(?:\.\d+)?", evidence)
        if _normalized_number(match.group()) == target
    ]
    if not value_positions:
        return None
    candidates: list[tuple[int, str, str]] = []
    for sample_id, pattern in _FAMILY_SAMPLE_PATTERNS:
        for match in pattern.finditer(evidence):
            distances = [
                (value_position - match.end())
                if match.end() <= value_position
                else 10000 + match.start() - value_position
                for value_position in value_positions
            ]
            candidates.append((min(distances), sample_id, match.group(0)))
    if not candidates:
        return None
    _, sample_id, phrase = min(candidates)
    return sample_id, phrase


def _repair_family_level_assignments(
    facts: list[dict],
    sample_cards: list[dict],
) -> list[dict]:
    for fact in facts:
        if fact.get("fact_type") != "performance":
            continue
        family = _family_sample_for_fact(fact)
        if not family:
            continue
        sample_id, phrase = family
        _ensure_sample_card(
            sample_cards,
            sample_id=sample_id,
            evidence=phrase,
            material_system=phrase,
        )
        _assign_fact_sample(fact, sample_id, "family_level_material_assignment")
    return facts


def _repair_explicit_component_assignments(
    facts: list[dict],
    sample_cards: list[dict],
) -> list[dict]:
    for fact in facts:
        if fact.get("fact_type") != "performance":
            continue
        metric = find_metric_canonical(str(fact.get("metric_or_parameter") or "")) or ""
        if metric != "particle_size":
            continue
        evidence = str(fact.get("evidence_text") or "")
        match = _NANOPARTICLE_SUBJECT_RE.search(evidence)
        if not match:
            continue
        material = match.group("material")
        sample_id = normalize_sample_id(f"{material.lower()}_nanoparticles")
        card = _ensure_sample_card(
            sample_cards,
            sample_id=sample_id,
            evidence=match.group(0),
            material_system=f"{material} nanoparticles",
            fiber_type="nanoparticle",
        )
        _assign_fact_sample(fact, sample_id, "explicit_component_property_assignment")
        _append_sample_card_evidence(fact, card)
    return facts


def _percentage_values(text: str) -> set[str]:
    values = {
        _normalized_number(match.group(1))
        for match in re.finditer(
            r"(?i)(?<![\d.])(\d+(?:\.\d+)?)\s*"
            r"(?:(?:wt|vol|mol)\s*)?%",
            text or "",
        )
    }
    for match in re.finditer(
        r"(?i)(?P<values>\d+(?:\.\d+)?(?:\s*(?:,|and|to)\s*"
        r"\d+(?:\.\d+)?)+)\s*(?:(?:wt|vol|mol)\s*)?%",
        text or "",
    ):
        values.update(
            _normalized_number(value)
            for value in re.findall(r"\d+(?:\.\d+)?", match.group("values"))
        )
    return {value for value in values if value}


def _variable_tokens(card: dict) -> set[str]:
    return {
        token
        for token in re.findall(
            r"[a-z][a-z0-9]*",
            normalize_for_match(card.get("variable_name") or ""),
        )
        if token not in _VARIABLE_TOKEN_STOPWORDS
    }


def _append_unique_variant_grounding(
    facts: list[dict],
    sample_cards: list[dict],
) -> list[dict]:
    from app.services.extractor_v7.final_checklist import (
        sample_id_supported_by_evidence,
    )

    cards_by_id = {
        normalize_sample_id(card.get("sample_id") or ""): card
        for card in sample_cards
        if normalize_sample_id(card.get("sample_id") or "")
    }
    for fact in facts:
        if fact.get("fact_type") != "performance":
            continue
        sample_id = normalize_sample_id(fact.get("assigned_sample_id") or "")
        card = cards_by_id.get(sample_id)
        evidence = str(fact.get("evidence_text") or "")
        if not card:
            continue
        already_supported = sample_id_supported_by_evidence(sample_id, evidence)
        context = " ".join([evidence, str(fact.get("condition") or "")])
        percentages = _percentage_values(context)
        variable_value = _normalized_number(card.get("variable_value"))
        variable_unit = str(card.get("variable_unit") or "")
        context_tokens = set(re.findall(r"[a-z][a-z0-9]*", normalize_for_match(context)))
        if not variable_value or variable_value not in percentages or "%" not in variable_unit:
            continue
        candidates = []
        target_group = str(card.get("sample_group_id") or "").strip()
        target_variable = normalize_for_match(card.get("variable_name") or "")
        for candidate in sample_cards:
            if target_group and str(candidate.get("sample_group_id") or "").strip() != target_group:
                continue
            if (
                target_variable
                and normalize_for_match(candidate.get("variable_name") or "")
                != target_variable
            ):
                continue
            candidate_value = _normalized_number(candidate.get("variable_value"))
            candidate_unit = str(candidate.get("variable_unit") or "")
            tokens = _variable_tokens(candidate)
            if (
                candidate_value == variable_value
                and "%" in candidate_unit
                and tokens
                and tokens & context_tokens
            ):
                candidates.append(normalize_sample_id(candidate.get("sample_id") or ""))
        if set(candidates) != {sample_id}:
            continue
        if not already_supported:
            _append_sample_card_evidence(fact, card)
        fact["assignment_reason"] = _append_assignment_reason(
            fact.get("assignment_reason"),
            "unique_variant_sample_card_grounding",
        )
        fact.pop("_alignment_review_required", None)
        fact["_alignment_verified"] = True
    return facts


_PSEUDO_PREFORM_RANGE_RE = re.compile(
    r"(?i)^preform(?:[_\s-]+thickness)?[_\s-]+"
    r"(?:up[_\s-]+to|above|beyond|\d)"
)
_PREFORM_THICKNESS_CONTEXT_RES = (
    re.compile(
        r"(?i)(?<![\d.])(\d+(?:\.\d+)?)\s*mm[_\s-]*"
        r"(?:[a-z]{2}[_\s-]*)?preform\b"
    ),
    re.compile(
        r"(?i)(?<![\d.])(\d+(?:\.\d+)?)\s*mm\s+"
        r"(?:thick(?:ness)?\s+)?preform\b"
    ),
)
_TUFTING_PROCESS_METRICS = frozenset({
    "tufting_robot_speed",
    "tufting_test_speed",
})


def _matching_preform_card(fact: dict, sample_cards: list[dict]) -> dict | None:
    identity_context = " ".join([
        str(fact.get("assigned_sample_id") or ""),
        str(fact.get("condition") or ""),
    ])
    thicknesses = {
        _normalized_number(match.group(1))
        for pattern in _PREFORM_THICKNESS_CONTEXT_RES
        for match in pattern.finditer(identity_context)
    }
    thicknesses.discard("")
    if len(thicknesses) != 1:
        return None
    thickness = next(iter(thicknesses))
    matches: list[dict] = []
    for card in sample_cards:
        card_text = " ".join([
            str(card.get("sample_id") or ""),
            str(card.get("material_system") or ""),
            str(card.get("fiber_type") or ""),
            str(card.get("variable_name") or ""),
        ])
        if "preform" not in normalize_for_match(card_text):
            continue
        variable_name = normalize_for_match(card.get("variable_name") or "")
        if "preform thickness" not in variable_name:
            continue
        if _normalized_number(card.get("variable_value")) == thickness:
            matches.append(card)
    return matches[0] if len(matches) == 1 else None


def _repair_tufting_speed_process_facts(
    facts: list[dict],
    sample_cards: list[dict],
) -> list[dict]:
    """Keep preform thickness ranges as conditions on one process family."""
    pseudo_ids: set[str] = set()
    for fact in facts:
        evidence = str(fact.get("evidence_text") or "")
        raw_metric = str(fact.get("metric_or_parameter") or "")
        process_metric = find_process_parameter_canonical(raw_metric) or ""
        observation_metric = process_metric == "observation_magnification"
        tufting_metric = process_metric in _TUFTING_PROCESS_METRICS
        if not observation_metric and not tufting_metric and not (
            re.search(r"(?i)\brobot\s+speed\b", evidence)
            and re.search(r"(?i)\btufting\b", evidence)
            and re.search(r"(?i)\bmm\s*/\s*min\b", evidence)
        ):
            continue
        if not process_metric:
            process_metric = "tufting_robot_speed"

        current = normalize_sample_id(fact.get("assigned_sample_id") or "")
        if current and _PSEUDO_PREFORM_RANGE_RE.match(current):
            pseudo_ids.add(normalize_for_match(current))

        card = _matching_preform_card(fact, sample_cards)
        if card is None:
            card = _ensure_sample_card(
                sample_cards,
                sample_id="tufted_preform",
                evidence=(
                    "Optimum robot speed for tufting of preform of various "
                    "thicknesses."
                ),
                material_system="tufted carbon fabric preform",
                fiber_type="fabric/preform",
            )
        card["process_route"] = card.get("process_route") or "robotic tufting"
        fact["fact_type"] = "structure" if observation_metric else "process"
        fact["category"] = "structure" if observation_metric else "process"
        fact["metric_or_parameter"] = process_metric
        target_id = normalize_sample_id(card.get("sample_id") or "tufted_preform")
        if current and current != target_id and re.search(
            r"(?i)\b(?:needle|thread)\b",
            normalize_for_match(current),
        ):
            condition = str(fact.get("condition") or "").strip()
            detail = f"process configuration={current}"
            if detail not in condition:
                fact["condition"] = f"{condition}; {detail}".strip("; ")
        fact.pop("_checklist_failed", None)
        fact.pop("_checklist_failures", None)
        _assign_fact_sample(
            fact,
            target_id,
            (
                "observation_parameter_routed_to_structure_method"
                if observation_metric
                else "tufting_speed_routed_to_process_parameter"
            ),
        )
        _append_sample_card_evidence(fact, card)

    if pseudo_ids:
        sample_cards[:] = [
            card
            for card in sample_cards
            if normalize_for_match(card.get("sample_id") or "") not in pseudo_ids
        ]
    return facts


def _collective_scope_supports_count(evidence: str, count: int) -> bool:
    words = {
        2: "two",
        3: "three",
        4: "four",
        5: "five",
        6: "six",
    }
    tokens = [str(count)]
    if count in words:
        tokens.append(words[count])
    alternatives = "|".join(re.escape(token) for token in tokens)
    if re.search(
        rf"(?i)\beach\s+of\s+(?:the\s+)?(?:{alternatives})\b",
        evidence or "",
    ):
        return True
    return count == 2 and bool(re.search(r"(?i)\bboth\b", evidence or ""))


def _append_shared_variant_grounding(
    facts: list[dict],
    sample_cards: list[dict],
) -> list[dict]:
    """Ground one collective result that explicitly applies to every variant."""
    cards_by_id = {
        normalize_sample_id(card.get("sample_id") or ""): card
        for card in sample_cards
        if normalize_sample_id(card.get("sample_id") or "")
    }
    grouped: dict[tuple[str, str, str, str], list[dict]] = {}
    for fact in facts:
        if fact.get("fact_type") != "performance":
            continue
        evidence = str(fact.get("evidence_text") or "")
        key = (
            normalize_for_match(evidence),
            str(fact.get("metric_or_parameter") or "").strip(),
            _normalized_number(fact.get("value")),
            str(fact.get("unit") or "").strip().lower(),
        )
        grouped.setdefault(key, []).append(fact)

    for (evidence, _metric, _value, _unit), group in grouped.items():
        sample_ids = {
            normalize_sample_id(fact.get("assigned_sample_id") or "")
            for fact in group
        }
        sample_ids.discard("")
        if len(group) < 2 or len(sample_ids) != len(group):
            continue
        if not _collective_scope_supports_count(evidence, len(group)):
            continue
        group_cards = [cards_by_id.get(sample_id) for sample_id in sample_ids]
        if any(card is None for card in group_cards):
            continue
        variant_keys = {
            (
                str(card.get("sample_group_id") or "").strip(),
                normalize_for_match(card.get("variable_name") or ""),
                str(card.get("variable_unit") or "").strip().lower(),
            )
            for card in group_cards
        }
        if len(variant_keys) != 1:
            continue
        variant_key = next(iter(variant_keys))
        if not variant_key[0] or not variant_key[1]:
            continue
        catalog_variant_ids = {
            normalize_sample_id(card.get("sample_id") or "")
            for card in sample_cards
            if (
                str(card.get("sample_group_id") or "").strip(),
                normalize_for_match(card.get("variable_name") or ""),
                str(card.get("variable_unit") or "").strip().lower(),
            ) == variant_key
        }
        if catalog_variant_ids != sample_ids:
            continue
        variant_values = {
            _normalized_number(card.get("variable_value"))
            for card in group_cards
        }
        if "" in variant_values or len(variant_values) != len(group_cards):
            continue
        for fact in group:
            sample_id = normalize_sample_id(fact.get("assigned_sample_id") or "")
            card = cards_by_id[sample_id]
            _append_sample_card_evidence(fact, card)
            fact["assignment_reason"] = _append_assignment_reason(
                fact.get("assignment_reason"),
                "collective_result_grounded_to_complete_variant_group",
            )
            fact.pop("_alignment_review_required", None)
            fact["_alignment_verified"] = True
    return facts


def _normalized_variant_loading_unit(value: object) -> str:
    compact = re.sub(r"[\s._-]+", "", str(value or "").lower())
    if "wt" in compact:
        return "wt%"
    if "vol" in compact:
        return "vol%"
    if "mol" in compact:
        return "mol%"
    return "%" if "%" in compact else ""


def _explicit_variant_code_mappings(
    chunks: list[dict] | None,
) -> dict[str, tuple[str, str]]:
    mappings: dict[str, set[tuple[str, str]]] = {}
    for chunk in chunks or []:
        text = str(chunk.get("raw_text") or chunk.get("text") or "")
        for match in _VARIANT_CODE_MAPPING_RE.finditer(text):
            context = text[max(0, match.start() - 140):match.end()]
            if not _VARIANT_LOADING_NAME_RE.search(context):
                continue
            values = [
                (
                    _normalized_number(number),
                    _normalized_variant_loading_unit(unit or "%"),
                )
                for number, unit in re.findall(
                    r"(\d+(?:\.\d+)?)\s*((?:wt|vol|mol)\s*)?%",
                    match.group("values"),
                    re.IGNORECASE,
                )
            ]
            codes = re.findall(
                rf"\b{_VARIANT_MAPPING_CODE}\b",
                match.group("codes"),
            )
            if len(values) != len(codes):
                continue
            for code, loading in zip(codes, values):
                if loading[0]:
                    mappings.setdefault(code, set()).add(loading)
    return {
        code: next(iter(loadings))
        for code, loadings in mappings.items()
        if len(loadings) == 1
    }


def _variant_loading_units_compatible(mapped: str, catalog: str) -> bool:
    mapped_unit = _normalized_variant_loading_unit(mapped)
    catalog_unit = _normalized_variant_loading_unit(catalog)
    return bool(
        mapped_unit
        and catalog_unit
        and (mapped_unit == "%" or mapped_unit == catalog_unit)
    )


def _attach_explicit_variant_code_aliases(
    facts: list[dict],
    chunks: list[dict] | None,
    sample_cards: list[dict],
) -> list[dict]:
    """Rebind document-defined codes such as C2 to the final sample variant."""
    mappings = _explicit_variant_code_mappings(chunks)
    if not mappings:
        return facts
    cards_by_id = {
        normalize_sample_id(card.get("sample_id") or ""): card
        for card in sample_cards
        if normalize_sample_id(card.get("sample_id") or "")
    }
    for fact in facts:
        if fact.get("fact_type") != "performance":
            continue
        sample_id = normalize_sample_id(fact.get("assigned_sample_id") or "")
        card = cards_by_id.get(sample_id)
        if not card or not _VARIANT_LOADING_NAME_RE.search(
            str(card.get("variable_name") or "")
        ):
            continue
        variable_value = _normalized_number(card.get("variable_value"))
        variable_unit = str(card.get("variable_unit") or "")
        evidence = str(fact.get("evidence_text") or "")
        matching_codes = [
            code
            for code, (mapped_value, mapped_unit) in mappings.items()
            if mapped_value == variable_value
            and _variant_loading_units_compatible(mapped_unit, variable_unit)
            and re.search(
                rf"(?i)(?<![A-Za-z0-9]){re.escape(code)}(?![A-Za-z0-9])",
                evidence,
            )
        ]
        if len(matching_codes) != 1:
            continue
        aliases = parse_sample_aliases(fact.get("_sample_aliases"))
        code = matching_codes[0]
        if code not in aliases:
            aliases.append(code)
        fact["_sample_aliases"] = aliases
        conflict_samples = {
            normalize_sample_id(conflict)
            for conflict in fact.get("_table_conflicting_sample_ids") or []
            if normalize_sample_id(conflict)
        }
        if (
            fact.get("_table_assignment_conflict")
            and conflict_samples == {sample_id, normalize_sample_id(code)}
        ):
            fact.pop("_table_assignment_conflict", None)
            fact.pop("_table_conflicting_sample_ids", None)
            fact.pop("_alignment_review_required", None)
            fact["_alignment_verified"] = True
            fact["assignment_reason"] = _append_assignment_reason(
                fact.get("assignment_reason"),
                "explicit_variant_code_conflict_resolved",
            )
        fact["assignment_reason"] = _append_assignment_reason(
            fact.get("assignment_reason"),
            "explicit_variant_code_alias_rebound",
        )
    return facts


_LOCAL_NANOCELLULOSE_NC_RE = re.compile(
    r"(?i)\b(?:NC\s+(?:yield|preparation|suspension)|"
    r"functionalized\s+NC|surface\s+of\s+NC|production\s+of\s+NC)\b"
)


def _attach_document_local_material_aliases(
    facts: list[dict],
    chunks: list[dict] | None,
    sample_cards: list[dict],
) -> list[dict]:
    """Attach an abbreviation only when one document uses it unambiguously."""
    document_text = "\n".join(
        str(chunk.get("raw_text") or chunk.get("text") or "")
        for chunk in chunks or []
    )
    if (
        "nanocellulose" not in normalize_for_match(document_text)
        or len(_LOCAL_NANOCELLULOSE_NC_RE.findall(document_text)) < 2
    ):
        return facts

    nanocellulose_ids = {
        sample_id
        for card in sample_cards
        if (sample_id := normalize_sample_id(card.get("sample_id") or ""))
        and re.fullmatch(r"(?i)CNCs?", sample_id)
        and "nanocellulose" in normalize_for_match(" ".join([
            sample_id,
            " ".join(parse_sample_aliases(
                card.get("sample_aliases") or card.get("aliases")
            )),
            str(card.get("material_system") or ""),
            str(card.get("evidence_text") or ""),
        ]))
    }
    if len(nanocellulose_ids) != 1:
        return facts
    sample_id = next(iter(nanocellulose_ids))

    for fact in facts:
        if (
            normalize_sample_id(fact.get("assigned_sample_id") or "") != sample_id
            or not _LOCAL_NANOCELLULOSE_NC_RE.search(
                str(fact.get("evidence_text") or "")
            )
        ):
            continue
        aliases = parse_sample_aliases(fact.get("_sample_aliases"))
        if "NC" not in aliases:
            aliases.append("NC")
        fact["_sample_aliases"] = aliases
        fact["assignment_reason"] = _append_assignment_reason(
            fact.get("assignment_reason"),
            "document_local_nanocellulose_abbreviation",
        )
    return facts


def _source_context_excerpt(source_text: str, evidence: str) -> str:
    source_text = str(source_text or "").strip()
    if len(source_text) <= 4000:
        return source_text
    evidence = str(evidence or "").strip()
    position = source_text.find(evidence) if evidence else -1
    if position < 0:
        return source_text[:4000]
    start = max(0, position - 900)
    return source_text[start:start + 4000]


def _append_source_block_identity_grounding(
    facts: list[dict],
    chunks: list[dict] | None,
) -> list[dict]:
    """Restore source-block context when a model returned only one sentence."""
    from app.services.extractor_v7.final_checklist import (
        sample_id_supported_by_evidence,
    )

    chunk_rows: list[tuple[str, str, str]] = []
    for chunk in chunks or []:
        text = str(chunk.get("raw_text") or chunk.get("text") or "").strip()
        if not text:
            continue
        chunk_rows.append((
            str(chunk.get("source_block_id") or chunk.get("block_id") or ""),
            text,
            normalize_for_match(text),
        ))

    for fact in facts:
        sample_id = normalize_sample_id(fact.get("assigned_sample_id") or "")
        evidence = str(fact.get("evidence_text") or "").strip()
        if (
            not sample_id
            or not evidence
            or sample_id_supported_by_evidence(sample_id, evidence)
        ):
            continue
        source_block_id = str(
            fact.get("_source_block_id") or fact.get("source_block_id") or ""
        )
        evidence_key = normalize_for_match(evidence)[:240]
        candidates = [
            (block_id, text)
            for block_id, text, normalized_text in chunk_rows
            if (
                (source_block_id and block_id == source_block_id)
                or (len(evidence_key) >= 40 and evidence_key in normalized_text)
            )
            and sample_id_supported_by_evidence(sample_id, text)
        ]
        unique_candidates = list(dict.fromkeys(candidates))
        if len(unique_candidates) != 1:
            continue
        block_id, source_text = unique_candidates[0]
        marker = f"[source block context] {block_id or 'matched block'}"
        if marker in evidence:
            continue
        fact["evidence_text"] = "\n".join([
            evidence,
            f"{marker}: {_source_context_excerpt(source_text, evidence)}",
        ])
        fact["assignment_reason"] = _append_assignment_reason(
            fact.get("assignment_reason"),
            "source_block_identity_context_restored",
        )
    return facts


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
    raw_canonical = find_metric_canonical(raw) or raw.replace(" ", "_")
    if raw_canonical not in {"dielectric_loss", "loss_tangent"}:
        return fact
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
    sample_cards: list[dict] | None = None,
) -> list[dict]:
    """Apply generic quality rules before record generation."""
    cards = sample_cards if sample_cards is not None else []
    facts = _rebind_loading_specific_samples(facts, cards)
    facts = _repair_tufting_speed_process_facts(facts, cards)
    facts = _repair_composition_formula_assignments(facts, cards)
    facts = _repair_component_loading_semantics(facts)
    facts = _repair_family_level_assignments(facts, cards)
    facts = _repair_explicit_component_assignments(facts, cards)
    facts = _append_unique_variant_grounding(facts, cards)
    facts = _append_shared_variant_grounding(facts, cards)
    facts = _append_source_block_identity_grounding(facts, chunks)
    facts = _apply_figure_caption_sample_anchors(facts, chunks, cards)
    facts = _expand_collective_figure_caption_anchors(facts, chunks, cards)
    themes = infer_paper_theme(chunks, paper_metadata)
    chunk_section_by_id: dict[str, str] = {}
    searchable_chunks: list[tuple[str, str]] = []
    for chunk in chunks or []:
        section = str(chunk.get("section_name") or "")
        block_id = str(
            chunk.get("source_block_id") or chunk.get("block_id") or ""
        )
        if block_id:
            chunk_section_by_id[block_id] = section
        searchable_text = re.sub(
            r"\s+",
            " ",
            str(chunk.get("raw_text") or "").strip(),
        )
        if len(searchable_text) >= 40:
            searchable_chunks.append((searchable_text, section))

    kept: list[dict] = []
    for fact in facts:
        if fact.get("fact_type") != "performance":
            kept.append(fact)
            continue

        evidence = str(fact.get("evidence_text") or "")
        source_block_id = str(
            fact.get("_source_block_id") or fact.get("source_block_id") or ""
        )
        if source_block_id and source_block_id in chunk_section_by_id:
            fact["_chunk_section"] = chunk_section_by_id[source_block_id]
        else:
            evidence_key = re.sub(r"\s+", " ", evidence.strip())[:160]
            if len(evidence_key) >= 40:
                for chunk_text, section in searchable_chunks:
                    if evidence_key in chunk_text:
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
    kept = _attach_document_local_material_aliases(kept, chunks, cards)
    kept = _attach_explicit_variant_code_aliases(kept, chunks, cards)
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
