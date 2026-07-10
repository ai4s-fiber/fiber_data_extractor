"""Deterministic sample grouping, assignment, and card construction.

The functions in this module do not call an LLM. They convert atomic
sample mentions, variable candidates, and fact candidates into stable sample
groups and sample cards used by the V7 extraction pipeline.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from typing import Any

from app.services.metrics_dictionary import find_metric_canonical


SAMPLE_CARD_FIELDS = [
    "sample_id", "sample_aliases", "sample_group_id", "material_system",
    "fiber_type", "variable_name", "variable_value", "variable_unit",
    "composition_expression", "matrix_name", "matrix_content", "matrix_unit",
    "additive_expression", "solvent_or_aid", "composition_evidence",
    "process_route", "spinning_method", "process_parameters", "post_treatment",
    "process_evidence", "structure_methods", "structure_features",
    "structure_evidence", "source_location", "evidence_text", "confidence",
    "_group_confidence", "_group_evidence", "_group_provisional",
]

GENERIC_SAMPLE_TERMS = {
    "sample", "samples", "fiber", "fibers", "film", "films", "aerogel",
    "aerogels", "composite", "composites", "material", "materials",
    "optimized sample", "modified sample", "composite fiber",
}

PERFORMANCE_LIKE_TERMS = {
    "conductivity", "strength", "modulus", "elongation", "contact angle",
    "permittivity", "loss", "density", "porosity", "shrinkage",
    "temperature", "stress", "strain", "efficiency", "transmittance",
}

_WT_LOADING_RE = re.compile(
    r"(?i)(?:^|[_\-\s])(\d+(?:\.\d+)?)\s*wt\.?%?"
)
_EMBEDDED_WT_RE = re.compile(
    r"(?i)(?:^|[_\-\s])(\d+(?:\.\d+)?)\s*wt\s*([a-z0-9]*)"
)
_VOL_LOADING_RE = re.compile(r"(?i)(\d+(?:\.\d+)?)\s*vol\.?%")
_DRAW_RATIO_RE = re.compile(r"(?i)r[_\s=]?(\d+(?:\.\d+)?)")
_ZERO_CNC_RE = re.compile(r"(?i)(?:^|[_\-\s])(?:0|0\.0)\s*(?:wt)?%?\s*cnc|0cnc")
_DISPERSION_WT_RE = re.compile(r"(?i)dispersion[_\s]*(\d+(?:\.\d+)?)\s*wt")
_DEVICE_RE = re.compile(r"(?i)fabric|peng|sensor|device")


def normalize_sample_id(text: str | None) -> str:
    """Lightly normalize a sample name while preserving paper wording."""
    value = (text or "").strip()
    value = value.replace("◦", "°").replace("℃", "°C")
    value = re.sub(r"\s+", " ", value)
    value = value.strip(" ,;:.()[]")
    return value


def normalize_for_match(text: str | None) -> str:
    value = normalize_sample_id(text).lower()
    value = value.replace("°", " ").replace("−", "-").replace("–", "-").replace("—", "-")
    value = re.sub(r"[^a-z0-9.%+\-/ ]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _append_unique(existing: str | None, addition: str | None) -> str:
    addition = (addition or "").strip()
    if not addition:
        return existing or ""
    existing = existing or ""
    parts = [p.strip() for p in existing.split(";") if p.strip()]
    seen = {normalize_for_match(p) for p in parts}
    if normalize_for_match(addition) not in seen:
        parts.append(addition)
    return "; ".join(parts)


def _is_generic_sample_name(sample_id: str) -> bool:
    normalized = normalize_for_match(sample_id)
    if not normalized or len(normalized) < 2:
        return True
    if normalized in GENERIC_SAMPLE_TERMS:
        return True
    if normalized.startswith(("the ", "this ")):
        return True
    return False


def _source_bucket(source_location: str | None) -> str:
    source = (source_location or "").strip()
    lower = source.lower()
    fig = re.search(r"(?:fig\.?|figure)\s*([0-9]+[a-z]?)", lower)
    if fig:
        return f"figure:{fig.group(1)}"
    table = re.search(r"table\s*([0-9]+[a-z]?)", lower)
    if table:
        return f"table:{table.group(1)}"
    page = re.search(r"(?:p\.|page)\s*([0-9]+)", lower)
    section = re.search(r"(experimental|preparation|fabrication|materials|results|discussion)", lower)
    if page and section:
        return f"page:{page.group(1)}:{section.group(1)}"
    if source:
        return lower
    return "unknown"


def _name_pattern(sample_id: str) -> str:
    normalized = normalize_for_match(sample_id)
    normalized = re.sub(r"\b\d+(?:\.\d+)?\s*(?:wt%|vol%|%|deg|c|k|h|min|s)?\b", "#", normalized)
    normalized = re.sub(r"\b[a-z]?[-_ ]?\d+[a-z]?\b", "#", normalized)
    normalized = re.sub(r"#+", "#", normalized)
    return normalized.strip(" -_/")


def _sample_ids_from_mentions(sample_mentions: list[dict]) -> list[str]:
    ids: dict[str, str] = {}
    for mention in sample_mentions:
        sid = normalize_sample_id(
            mention.get("normalized_sample_id") or mention.get("mention_text")
        )
        if _is_generic_sample_name(sid):
            continue
        ids.setdefault(normalize_for_match(sid), sid)
    return list(ids.values())


def _candidate_group_key(sample_ids: list[str]) -> tuple[str, ...]:
    return tuple(sorted(normalize_for_match(s) for s in sample_ids if s))


def group_samples(
    sample_mentions: list[dict],
    variable_candidates: list[dict],
) -> list[dict]:
    """Generate deterministic G001/G002 sample groups.

    Rules combine shared variable names, shared source locations, and similar
    sample-name patterns. Low-confidence or single-sample groups are marked
    provisional.
    """
    sample_ids = _sample_ids_from_mentions(sample_mentions)
    mention_by_source: dict[str, set[str]] = defaultdict(set)
    sources_by_sample: dict[str, set[str]] = defaultdict(set)
    for mention in sample_mentions:
        sid = normalize_sample_id(
            mention.get("normalized_sample_id") or mention.get("mention_text")
        )
        if sid not in sample_ids:
            continue
        bucket = _source_bucket(mention.get("source_location"))
        mention_by_source[bucket].add(sid)
        if mention.get("source_location"):
            sources_by_sample[sid].add(str(mention.get("source_location")))

    group_candidates: list[dict] = []

    vars_by_name: dict[str, list[dict]] = defaultdict(list)
    for candidate in variable_candidates:
        sid = normalize_sample_id(candidate.get("sample_id"))
        if sid not in sample_ids:
            continue
        variable_name = (candidate.get("variable_name_raw") or "").strip()
        if not variable_name:
            continue
        if _looks_like_performance_metric(variable_name):
            continue
        vars_by_name[normalize_for_match(variable_name)].append(candidate)

    for variable_key, candidates in vars_by_name.items():
        ids = sorted({normalize_sample_id(c.get("sample_id")) for c in candidates})
        values = {
            normalize_for_match(c.get("variable_value_raw"))
            for c in candidates if c.get("variable_value_raw")
        }
        if len(ids) >= 2 and len(values) >= 2:
            raw_name = Counter(c.get("variable_name_raw", "") for c in candidates).most_common(1)[0][0]
            sources = sorted({c.get("source_location", "") for c in candidates if c.get("source_location")})
            group_candidates.append({
                "sample_ids": ids,
                "group_variable_name": raw_name,
                "group_evidence": f"shared variable '{raw_name}' with distinct values",
                "source_locations": sources,
                "confidence": 0.9,
                "is_provisional": False,
            })

    for bucket, ids_set in mention_by_source.items():
        ids = sorted(ids_set)
        if len(ids) >= 2:
            group_candidates.append({
                "sample_ids": ids,
                "group_variable_name": "",
                "group_evidence": f"samples co-mentioned in {bucket}",
                "source_locations": sorted({src for sid in ids for src in sources_by_sample.get(sid, set())}),
                "confidence": 0.72 if bucket != "unknown" else 0.55,
                "is_provisional": bucket == "unknown",
            })

    by_pattern: dict[str, set[str]] = defaultdict(set)
    for sid in sample_ids:
        pattern = _name_pattern(sid)
        if pattern and pattern != normalize_for_match(sid):
            by_pattern[pattern].add(sid)
    for pattern, ids_set in by_pattern.items():
        ids = sorted(ids_set)
        if len(ids) >= 2:
            group_candidates.append({
                "sample_ids": ids,
                "group_variable_name": "",
                "group_evidence": f"similar sample naming pattern '{pattern}'",
                "source_locations": sorted({src for sid in ids for src in sources_by_sample.get(sid, set())}),
                "confidence": 0.68,
                "is_provisional": False,
            })

    selected: list[dict] = []
    seen_keys: set[tuple[str, ...]] = set()
    for group in sorted(group_candidates, key=lambda g: (-g["confidence"], -len(g["sample_ids"]))):
        key = _candidate_group_key(group["sample_ids"])
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        selected.append(group)

    covered = {sid for group in selected for sid in group["sample_ids"]}
    for sid in sample_ids:
        if sid not in covered:
            selected.append({
                "sample_ids": [sid],
                "group_variable_name": "",
                "group_evidence": "insufficient grouping evidence; provisional single-sample group",
                "source_locations": sorted(sources_by_sample.get(sid, set())),
                "confidence": 0.45,
                "is_provisional": True,
            })

    for idx, group in enumerate(selected, 1):
        group["sample_group_id"] = f"G{idx:03d}"
    return selected


def _looks_like_performance_metric(name: str) -> bool:
    lower = normalize_for_match(name)
    if find_metric_canonical(name):
        return True
    return any(term in lower for term in PERFORMANCE_LIKE_TERMS)


def infer_variable_from_sample_id(sample_id: str) -> tuple[str, str, str]:
    """Infer (variable_name, variable_value, variable_unit) from sample_id wording.

    Generic heuristics for series labels such as 1.0 wt% CNC, PCF_1.0wtCNC, R=1.5.
    """
    sid = normalize_sample_id(sample_id)
    lower = normalize_for_match(sid)
    if not sid:
        return "", "", ""

    if _ZERO_CNC_RE.search(sid):
        return "CNC loading", "0", "wt%"

    match = _EMBEDDED_WT_RE.search(sid)
    if match:
        value, tail = match.group(1), (match.group(2) or "").lower()
        name = "CNC loading" if "cnc" in lower or tail.startswith("cnc") else "loading"
        if tail and tail not in {"cnc", "cncs", "wt"}:
            name = f"{tail} loading"
        return name, value, "wt%"

    match = _WT_LOADING_RE.search(sid)
    if match:
        name = "CNC loading" if "cnc" in lower else "loading"
        return name, match.group(1), "wt%"

    match = _VOL_LOADING_RE.search(sid)
    if match:
        return "loading", match.group(1), "vol%"

    match = _DRAW_RATIO_RE.search(sid)
    if match:
        return "draw ratio", match.group(1), "×"

    match = _DISPERSION_WT_RE.search(sid)
    if match:
        return "CNC loading", match.group(1), "wt%"

    if "loaded" in lower and "cnc" in lower:
        return "CNC loading", "", "wt%"

    if _DEVICE_RE.search(sid):
        return "device configuration", sid, ""

    if "pulp" in lower or ("recycled" in lower and "cellulose" in lower):
        return "raw material", sid, ""

    if re.fullmatch(r"cncs?", lower):
        return "preparation parameter", "", ""

    if "powder" in lower:
        return "reference material", sid, ""

    return "", "", ""


def fill_sample_card_variables(
    sample_cards: list[dict],
    sample_groups: list[dict] | None = None,
) -> list[dict]:
    """Fill missing variable_name/value/unit on sample cards."""
    group_by_sample: dict[str, dict] = {}
    for group in sample_groups or []:
        for sid in group.get("sample_ids") or []:
            current = group_by_sample.get(sid)
            if current is None or group.get("confidence", 0) > current.get("confidence", 0):
                group_by_sample[sid] = group

    for card in sample_cards:
        sid = card.get("sample_id") or ""
        group = group_by_sample.get(sid, {})
        if not card.get("variable_name") and group.get("group_variable_name"):
            card["variable_name"] = group["group_variable_name"]

        inferred_name, inferred_value, inferred_unit = infer_variable_from_sample_id(sid)
        if not card.get("variable_name") and inferred_name:
            card["variable_name"] = inferred_name
        if not card.get("variable_value") and inferred_value:
            card["variable_value"] = inferred_value
        if not card.get("variable_unit") and inferred_unit:
            card["variable_unit"] = inferred_unit

        if card.get("variable_name") and not card.get("variable_value"):
            _, inferred_value, inferred_unit = infer_variable_from_sample_id(sid)
            if inferred_value:
                card["variable_value"] = inferred_value
            if inferred_unit and not card.get("variable_unit"):
                card["variable_unit"] = inferred_unit

    # Propagate variable_name (not value) within groups for device/fabric rows.
    donor_by_group: dict[str, dict] = {}
    for card in sample_cards:
        if card.get("variable_name") and card.get("sample_group_id"):
            donor_by_group.setdefault(card["sample_group_id"], card)
    for card in sample_cards:
        if card.get("variable_name"):
            continue
        donor = donor_by_group.get(card.get("sample_group_id") or "")
        if not donor:
            continue
        card["variable_name"] = donor.get("variable_name")
        inferred_name, inferred_value, inferred_unit = infer_variable_from_sample_id(card.get("sample_id") or "")
        if inferred_value:
            card["variable_value"] = inferred_value
        elif not card.get("variable_value"):
            card["variable_value"] = donor.get("variable_value")
        if inferred_unit:
            card["variable_unit"] = inferred_unit
        elif not card.get("variable_unit"):
            card["variable_unit"] = donor.get("variable_unit")

    return sample_cards


def _known_sample_names(sample_mentions: list[dict]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for mention in sample_mentions:
        sid = normalize_sample_id(
            mention.get("normalized_sample_id") or mention.get("mention_text")
        )
        if _is_generic_sample_name(sid):
            continue
        lookup[normalize_for_match(sid)] = sid
        for alias in mention.get("aliases") or []:
            alias_text = normalize_sample_id(alias)
            if alias_text and not _is_generic_sample_name(alias_text):
                lookup[normalize_for_match(alias_text)] = sid
    return lookup


def assign_fact_to_sample(
    fact: dict,
    sample_mentions: list[dict],
    sample_groups: list[dict],
) -> dict:
    """Assign one fact to a sample using deterministic local evidence first."""
    lookup = _known_sample_names(sample_mentions)
    if not lookup:
        return {"sample_id": "", "confidence": 0.0, "status": "unassigned", "reason": "no known samples"}

    evidence = " ".join([
        str(fact.get("sample_id") or ""),
        str(fact.get("subject_text") or ""),
        str(fact.get("evidence_text") or ""),
        str(fact.get("source_location") or ""),
    ])
    evidence_norm = normalize_for_match(evidence)

    candidates = fact.get("candidate_sample_ids") or []
    if isinstance(candidates, str):
        try:
            candidates = json.loads(candidates)
        except json.JSONDecodeError:
            candidates = [candidates]

    for candidate in [fact.get("sample_id"), *candidates]:
        cand_norm = normalize_for_match(str(candidate or ""))
        if cand_norm in lookup:
            return {
                "sample_id": lookup[cand_norm],
                "confidence": 0.95,
                "status": "assigned",
                "reason": "explicit candidate sample",
            }

    best_sid = ""
    best_score = 0.0
    for norm_name, sid in lookup.items():
        if not norm_name:
            continue
        score = 0.0
        specificity = min(len(norm_name) / 10, 4)
        if re.search(rf"(?<![a-z0-9]){re.escape(norm_name)}(?![a-z0-9])", evidence_norm):
            score += 8 + specificity
        elif norm_name in evidence_norm:
            score += 5 + specificity
        for part in re.split(r"[-_/ ]+", norm_name):
            if len(part) >= 3 and part in evidence_norm:
                score += 0.5
        if score > best_score:
            best_score = score
            best_sid = sid

    if best_sid and best_score >= 4:
        return {
            "sample_id": best_sid,
            "confidence": min(0.55 + best_score * 0.05, 0.9),
            "status": "assigned",
            "reason": "sample mentioned in evidence",
        }

    source_bucket = _source_bucket(fact.get("source_location"))
    source_mentions = [
        normalize_sample_id(m.get("normalized_sample_id") or m.get("mention_text"))
        for m in sample_mentions
        if _source_bucket(m.get("source_location")) == source_bucket
    ]
    source_mentions = [sid for sid in source_mentions if sid in lookup.values()]
    unique_source_mentions = sorted(set(source_mentions))
    if len(unique_source_mentions) == 1:
        return {
            "sample_id": unique_source_mentions[0],
            "confidence": 0.7,
            "status": "assigned",
            "reason": "only sample in same source location",
        }

    _ = sample_groups  # reserved for future order-aware table/figure assignment
    return {"sample_id": "", "confidence": 0.0, "status": "unassigned", "reason": "no deterministic match"}


def build_sample_cards(
    sample_mentions: list[dict],
    variable_candidates: list[dict],
    sample_groups: list[dict],
    fact_candidates: list[dict],
) -> list[dict]:
    """Build sample cards from deterministic intermediate artifacts."""
    sample_ids = _sample_ids_from_mentions(sample_mentions)
    for fact in fact_candidates:
        sid = normalize_sample_id(fact.get("assigned_sample_id") or fact.get("sample_id"))
        if sid and sid not in sample_ids and not _is_generic_sample_name(sid):
            sample_ids.append(sid)

    group_by_sample: dict[str, dict] = {}
    for group in sample_groups:
        for sid in group.get("sample_ids") or []:
            current = group_by_sample.get(sid)
            if current is None or group.get("confidence", 0) > current.get("confidence", 0):
                group_by_sample[sid] = group

    aliases_by_sample: dict[str, set[str]] = defaultdict(set)
    mention_meta: dict[str, dict] = {}
    for mention in sample_mentions:
        sid = normalize_sample_id(mention.get("normalized_sample_id") or mention.get("mention_text"))
        if sid not in sample_ids:
            continue
        for alias in mention.get("aliases") or []:
            alias_text = normalize_sample_id(alias)
            if alias_text and alias_text != sid:
                aliases_by_sample[sid].add(alias_text)
        mention_meta.setdefault(sid, mention)

    cards: dict[str, dict] = {}
    for sid in sample_ids:
        group = group_by_sample.get(sid, {})
        mention = mention_meta.get(sid, {})
        cards[sid] = {field: "" for field in SAMPLE_CARD_FIELDS}
        cards[sid].update({
            "sample_id": sid,
            "sample_aliases": json.dumps(sorted(aliases_by_sample[sid]), ensure_ascii=False) if aliases_by_sample[sid] else "",
            "sample_group_id": group.get("sample_group_id", "G000"),
            "source_location": mention.get("source_location", ""),
            "evidence_text": mention.get("context_text", ""),
            "confidence": min(float(mention.get("confidence", 0.55) or 0.55), float(group.get("confidence", 0.55) or 0.55)),
            "_group_confidence": group.get("confidence", 0.0),
            "_group_evidence": group.get("group_evidence", ""),
            "_group_provisional": group.get("is_provisional", True),
        })

    best_var_by_sample: dict[str, dict] = {}
    for candidate in variable_candidates:
        sid = normalize_sample_id(candidate.get("sample_id"))
        if sid not in cards:
            continue
        name = candidate.get("variable_name_raw", "")
        if _looks_like_performance_metric(name):
            continue
        current = best_var_by_sample.get(sid)
        if current is None or candidate.get("confidence", 0) > current.get("confidence", 0):
            best_var_by_sample[sid] = candidate
    for sid, candidate in best_var_by_sample.items():
        cards[sid]["variable_name"] = candidate.get("variable_name_raw", "") or ""
        cards[sid]["variable_value"] = candidate.get("variable_value_raw", "") or ""
        cards[sid]["variable_unit"] = candidate.get("variable_unit_raw", "") or ""

    for fact in fact_candidates:
        if fact.get("fact_type") == "performance":
            continue
        sid = normalize_sample_id(fact.get("assigned_sample_id") or fact.get("sample_id"))
        if not sid or sid not in cards:
            continue
        _merge_background_fact(cards[sid], fact)

    global_background_facts = [
        fact for fact in fact_candidates
        if _is_global_background_fact(fact)
    ]
    if global_background_facts and cards:
        for card in cards.values():
            for fact in global_background_facts:
                _merge_background_fact(card, fact)

    for card in cards.values():
        if not card.get("material_system"):
            card["material_system"] = card.get("composition_expression") or card.get("matrix_name") or ""
        if not card.get("composition_evidence"):
            card["composition_evidence"] = card.get("evidence_text", "")
        if not card.get("process_evidence"):
            card["process_evidence"] = ""
        if not card.get("structure_evidence"):
            card["structure_evidence"] = ""

    return fill_sample_card_variables(list(cards.values()), sample_groups)


def _is_global_background_fact(fact: dict) -> bool:
    ftype = fact.get("fact_type")
    if ftype not in {"composition", "process", "structure"}:
        return False
    if normalize_sample_id(fact.get("assigned_sample_id") or fact.get("sample_id")):
        return False
    candidates = fact.get("candidate_sample_ids") or []
    if isinstance(candidates, str):
        try:
            candidates = json.loads(candidates)
        except json.JSONDecodeError:
            candidates = [candidates]
    if any(normalize_sample_id(str(candidate)) for candidate in candidates):
        return False
    section = normalize_for_match(fact.get("_chunk_section") or fact.get("section_name") or "")
    source = normalize_for_match(fact.get("source_location") or "")
    text = normalize_for_match(" ".join([
        str(fact.get("metric_or_parameter") or ""),
        str(fact.get("subject_text") or ""),
        str(fact.get("evidence_text") or ""),
    ]))
    if any(term in section or term in source for term in ("experimental", "method", "materials")):
        return True
    if ftype == "process" and any(term in text for term in (
        "electrospinning", "spinning", "anneal", "poling", "drying", "curing",
        "voltage", "flow rate", "collector", "distance", "heat treatment",
    )):
        return True
    return False


def _merge_background_fact(card: dict, fact: dict) -> None:
    ftype = fact.get("fact_type")
    metric = str(fact.get("metric_or_parameter") or fact.get("subject_text") or "")
    value = str(fact.get("value") or "").strip()
    unit = str(fact.get("unit") or "").strip()
    evidence = fact.get("evidence_text") or fact.get("source_location") or ""
    text_value = f"{metric}={value} {unit}".strip() if value else metric
    lower = normalize_for_match(" ".join([metric, value]))

    if ftype == "composition":
        card["composition_expression"] = _append_unique(card.get("composition_expression"), text_value)
        if any(term in lower for term in ("matrix", "polymer", "precursor", "base")):
            card["matrix_name"] = card.get("matrix_name") or value or metric
        if any(term in lower for term in ("content", "loading", "concentration", "wt", "vol")):
            if value:
                card["matrix_content"] = card.get("matrix_content") or value
                card["matrix_unit"] = card.get("matrix_unit") or unit
        if any(term in lower for term in ("additive", "filler", "catalyst", "crosslink", "dopant")):
            card["additive_expression"] = _append_unique(card.get("additive_expression"), text_value)
        if any(term in lower for term in ("solvent", "dmf", "water", "ethanol", "aid", "tea", "tba")):
            card["solvent_or_aid"] = _append_unique(card.get("solvent_or_aid"), text_value)
        card["composition_evidence"] = _append_unique(card.get("composition_evidence"), evidence)
    elif ftype == "process":
        card["process_parameters"] = _append_unique(card.get("process_parameters"), text_value)
        if any(term in lower for term in ("spinning", "electrospinning", "wet spinning", "dry spinning")):
            card["spinning_method"] = card.get("spinning_method") or metric
        if any(term in lower for term in ("dry", "anneal", "heat", "freeze", "imidization", "carbonization")):
            card["post_treatment"] = _append_unique(card.get("post_treatment"), text_value)
        card["process_route"] = _append_unique(card.get("process_route"), metric)
        card["process_evidence"] = _append_unique(card.get("process_evidence"), evidence)
    elif ftype == "structure":
        if fact.get("method"):
            card["structure_methods"] = _append_unique(card.get("structure_methods"), fact.get("method"))
        card["structure_features"] = _append_unique(card.get("structure_features"), text_value)
        card["structure_evidence"] = _append_unique(card.get("structure_evidence"), evidence)
