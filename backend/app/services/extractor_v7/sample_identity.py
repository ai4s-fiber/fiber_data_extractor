"""Generic sample ID alias clustering and canonicalization (all papers)."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from app.services.grouping import normalize_for_match, normalize_sample_id

_GENERIC_STOPWORDS = frozenset({
    "sample", "samples", "fiber", "fibers", "film", "films", "composite",
    "composites", "material", "materials", "specimen", "specimens", "the",
    "with", "and", "based", "prepared", "fabric", "fabrics", "device",
    "particle", "particles", "powder", "powders",
})

_LOADING_RE = re.compile(
    r"(?i)(\d+(?:\.\d+)?)\s*(?:wt\.?%|wt%|vol\.?%|mol\.?%)\s*([a-z0-9][\w\-]*)"
)
_EMBEDDED_LOADING_RE = re.compile(
    r"(?i)(?:^|[_\-\s])(\d+(?:\.\d+)?)\s*wt\s*([a-z0-9][\w\-]*)"
)
_RATIO_RE = re.compile(r"(?i)(\d+)\s*:\s*(\d+)")
_RUN_VARIANT_RE = re.compile(
    r"(?i)(?:\b(?:sample|specimen|run|no\.?)\s*[-#:]?\s*(\d+(?:\.\d+)?)\b|"
    r"(?:^|[\s_\-/])([0-9]+(?:\.[0-9]+)?)\s*$)"
)
_NEEDLE_COUNT_IN_ID_RE = re.compile(
    r"(?i)(?:^|[_\s/-])(\d+(?:\.\d+)?)(?:[_\s/-]*needles?\b|(?=[_\s/-]+\d+(?:\.\d+)?\s*mm\b))"
)
_NEEDLE_SPACING_IN_ID_RE = re.compile(r"(?i)(\d+(?:\.\d+)?)\s*mm\b")
_GENERIC_MULTI_NEEDLE_SUFFIX_RE = re.compile(
    r"(?i)[_\s/-]+(?:multi(?:ple)?[_\s/-]*needle|multineedle)"
    r"(?:[_\s/-]*box[_\s/-]*\d+)?$"
)
_CONFIGURATION_ID_TOKENS = frozenset({
    "apparatus", "box", "case", "configuration", "count", "electrospin",
    "electrospinning", "guided", "multi", "multiple", "needle", "needles",
    "orifice", "reservoir", "setup", "single", "system",
})
_COMPOSITION_FRACTION_RE = re.compile(
    r"(?i)(?<![\d.])(\d+(?:\.\d+)?)\s*(?:(wt|vol|mol)\.?\s*%?|%)"
)
_MODIFIED_MATERIAL_RE = re.compile(
    r"(?i)\b(?:composites?|reinforced|filled|enhancement|modified|treated|"
    r"hybrids?|tpms|metamaterials?)\b"
)
_GENERIC_MODIFIED_REFERENCE_TOKENS = frozenset({
    "composite", "composites", "enhanced", "enhancement", "fiber", "fibers",
    "fibre", "fibres", "filled", "material", "materials", "metamaterial",
    "metamaterials", "modified", "reinforced", "reinforcement", "structure",
    "structures", "tpms", "treated",
})
_TREATED_VARIANT_RE = re.compile(
    r"(?i)\b(?:treated|modified|functionalized|functionalised|acetylated|"
    r"coated|grafted|cross[- ]?linked|annealed|carbonized|oxidized|reduced)\b"
)


def _token_set(text: str) -> set[str]:
    norm = normalize_for_match(text)
    tokens = {t for t in re.split(r"[\s_/\-]+", norm) if len(t) >= 2}
    return tokens - _GENERIC_STOPWORDS


def _explicit_composition_chain(text: str) -> tuple[str, ...]:
    """Return a compact material chain such as PCL/AA/S without losing one-letter parts."""
    raw = normalize_sample_id(text)
    slash_matches = re.findall(
        r"[A-Za-z][A-Za-z0-9]{0,15}(?:/[A-Za-z][A-Za-z0-9]{0,15})+",
        raw,
    )
    if slash_matches:
        parts = max(slash_matches, key=lambda value: value.count("/"))
        return tuple(part.lower() for part in parts.split("/"))

    # Common machine-friendly aliases preserve composition components with
    # underscores (for example PCL_AA_SBCu). Stop at descriptive/lower-case
    # suffixes so ordinary identifiers are not treated as chemical chains.
    parts = raw.split("_")
    chain: list[str] = []
    for part in parts:
        if not re.fullmatch(r"[A-Z][A-Za-z0-9]{0,15}", part):
            break
        chain.append(part.lower())
    return tuple(chain) if len(chain) >= 2 else ()


def _has_slash_composition_chain(text: str) -> bool:
    return bool(re.search(
        r"[A-Za-z][A-Za-z0-9]{0,15}(?:/[A-Za-z][A-Za-z0-9]{0,15})+",
        normalize_sample_id(text),
    ))


def _is_bare_slash_composition_chain(text: str) -> bool:
    return bool(re.fullmatch(
        r"[A-Za-z][A-Za-z0-9]{0,15}(?:/[A-Za-z][A-Za-z0-9]{0,15})+",
        normalize_sample_id(text).strip(),
    ))


def _preferred_composition_chains(
    sample_id: str,
    aliases: Any = None,
) -> set[tuple[str, ...]]:
    """Resolve the most reliable explicit composition identity for one sample."""
    sid = normalize_sample_id(sample_id)
    direct = _explicit_composition_chain(sid)
    alias_values = parse_sample_aliases(aliases)

    # Slash-delimited source IDs are stronger than model-proposed aliases. This
    # also rejects an alias that accidentally names a neighbouring composition.
    if direct and _has_slash_composition_chain(sid):
        return {direct}

    slash_aliases = {
        chain
        for alias in alias_values
        if _has_slash_composition_chain(alias)
        if (chain := _explicit_composition_chain(alias))
    }
    if slash_aliases:
        return slash_aliases

    alias_chains = {
        chain for alias in alias_values
        if (chain := _explicit_composition_chain(alias))
    }
    if len(alias_chains) == 1:
        alias_chain = next(iter(alias_chains))
        if not direct or set(direct).issubset(alias_chain):
            return {alias_chain}
    return {direct} if direct else alias_chains


def _composition_chains_conflict(a: str, b: str) -> bool:
    chain_a = _explicit_composition_chain(a)
    chain_b = _explicit_composition_chain(b)
    return bool(chain_a and chain_b and chain_a != chain_b)


def _loading_signature(text: str) -> str | None:
    fraction = _COMPOSITION_FRACTION_RE.search(text or "")
    if fraction:
        basis = (fraction.group(2) or "percent").lower()
        try:
            value = f"{float(fraction.group(1)):g}"
        except ValueError:
            value = fraction.group(1)
        return f"fraction_{value}_{basis}"
    for pattern in (_LOADING_RE, _EMBEDDED_LOADING_RE):
        match = pattern.search(text or "")
        if match:
            filler = normalize_for_match(match.group(2)).rstrip("s")
            return f"{match.group(1)}wt_{filler}"
    return None


def _ratio_signature(text: str) -> str | None:
    match = _RATIO_RE.search(text or "")
    if not match:
        return None
    return f"ratio_{match.group(1)}_{match.group(2)}"


def _run_variant_signature(text: str) -> str | None:
    match = _RUN_VARIANT_RE.search(text or "")
    if not match:
        return None
    value = match.group(1) or match.group(2)
    return f"run_{value}"


def is_numbered_sample_variant(sample_id: str | None) -> bool:
    """Whether a sample ID carries an explicit run/specimen suffix."""
    return bool(_run_variant_signature(sample_id or ""))


def _alias_variants(sample_id: str) -> set[str]:
    sid = normalize_sample_id(sample_id)
    if not sid:
        return set()
    variants = {sid}
    norm = normalize_for_match(sid)
    variants.add(norm)
    loading = _loading_signature(sid)
    if loading:
        variants.add(loading)
    ratio = _ratio_signature(sid)
    if ratio:
        variants.add(ratio)
    compact = re.sub(r"[^a-z0-9]+", "", norm)
    if compact:
        variants.add(compact)
    return variants


def parse_sample_aliases(value: Any) -> list[str]:
    """Return sample aliases from either runtime lists or persisted JSON text."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(v) for v in parsed if v]
        except json.JSONDecodeError:
            pass
    return [part.strip() for part in text.split(";") if part.strip()]


def _fraction_values(text: Any) -> set[str]:
    values: set[str] = set()
    for match in _COMPOSITION_FRACTION_RE.finditer(str(text or "")):
        try:
            values.add(f"{float(match.group(1)):g}")
        except ValueError:
            continue
    return values


def _source_key(fact: dict) -> str:
    block_id = str(
        fact.get("_source_block_id") or fact.get("source_block_id") or ""
    ).strip()
    if block_id:
        return f"block:{block_id.lower()}"
    return f"location:{normalize_for_match(fact.get('source_location') or '')}"


def _is_generic_modified_reference(sample_id: str) -> bool:
    normalized = normalize_for_match(sample_id)
    tokens = set(re.findall(r"[a-z0-9]+", normalized))
    return bool(
        tokens
        and _MODIFIED_MATERIAL_RE.search(normalized)
        and tokens <= _GENERIC_MODIFIED_REFERENCE_TOKENS
    )


def _is_treated_variant(sample_id: str) -> bool:
    return bool(_TREATED_VARIANT_RE.search(normalize_for_match(sample_id)))


def _augment_contextual_aliases(
    alias_map: dict[str, str],
    *,
    facts: list[dict],
    holistic_samples: list[dict] | None,
    sample_cards: list[dict],
) -> dict[str, str]:
    """Resolve generic material references only when the paper gives one target."""
    if not alias_map or not facts:
        return alias_map

    canonical_ids = set(alias_map.values())
    variants_by_value: dict[str, set[str]] = defaultdict(set)
    for sid in canonical_ids:
        for value in _fraction_values(sid):
            variants_by_value[value].add(sid)
    for sample in [*(holistic_samples or []), *sample_cards]:
        sid = alias_map.get(
            normalize_sample_id(sample.get("sample_id") or ""),
            normalize_sample_id(sample.get("sample_id") or ""),
        )
        variable_name = normalize_for_match(sample.get("variable_name") or "")
        variable_value = str(sample.get("variable_value") or "").strip()
        if (
            sid in canonical_ids
            and variable_value
            and any(term in variable_name for term in ("fraction", "content", "loading"))
        ):
            try:
                variants_by_value[f"{float(variable_value):g}"].add(sid)
            except ValueError:
                pass

    reliable_base_ids = {
        alias_map.get(normalize_sample_id(sample.get("sample_id") or ""), "")
        for sample in holistic_samples or []
        if (
            sample.get("sample_id")
            and not _fraction_values(sample.get("sample_id"))
            and not _MODIFIED_MATERIAL_RE.search(str(sample.get("sample_id") or ""))
        )
    }
    reliable_base_ids.discard("")

    source_explicit_targets: dict[str, set[str]] = defaultdict(set)
    source_fraction_values: dict[str, set[str]] = defaultdict(set)
    active_variants: set[str] = set()
    sources_by_generic: dict[str, set[str]] = defaultdict(set)
    for fact in facts:
        raw_sid = normalize_sample_id(
            fact.get("assigned_sample_id")
            or next(iter(fact.get("candidate_sample_ids") or []), "")
        )
        canonical = alias_map.get(raw_sid, raw_sid)
        source = _source_key(fact)
        evidence = " ".join([
            str(fact.get("evidence_text") or ""),
            str(fact.get("condition") or ""),
        ])
        source_fraction_values[source].update(_fraction_values(evidence))
        if canonical and _fraction_values(canonical):
            source_explicit_targets[source].add(canonical)
            if fact.get("fact_type") == "performance":
                active_variants.add(canonical)
        if canonical and not _fraction_values(canonical):
            sources_by_generic[canonical].add(source)

    contextual_targets: dict[str, str] = {}
    specific_modified_ids = {
        sid
        for sid in canonical_ids
        if _MODIFIED_MATERIAL_RE.search(normalize_for_match(sid))
        and not _is_generic_modified_reference(sid)
    }
    holistic_modified_ids = {
        alias_map.get(
            normalize_sample_id(sample.get("sample_id") or ""),
            normalize_sample_id(sample.get("sample_id") or ""),
        )
        for sample in holistic_samples or []
        if _MODIFIED_MATERIAL_RE.search(
            normalize_for_match(sample.get("sample_id") or "")
        )
        and not _is_generic_modified_reference(
            normalize_sample_id(sample.get("sample_id") or "")
        )
    }
    holistic_modified_ids.discard("")
    preferred_modified_ids = (
        holistic_modified_ids
        if len(holistic_modified_ids) == 1
        else specific_modified_ids
    )
    if len(preferred_modified_ids) == 1:
        specific_target = next(iter(preferred_modified_ids))
        target_distinctive_tokens = (
            _token_set(specific_target) - _GENERIC_MODIFIED_REFERENCE_TOKENS
        )
        target_fractions = _fraction_values(specific_target)
        for sid in canonical_ids:
            if (
                sid == specific_target
                or not _MODIFIED_MATERIAL_RE.search(normalize_for_match(sid))
                or _has_slash_composition_chain(sid)
            ):
                continue
            source_fractions = _fraction_values(sid)
            if source_fractions and source_fractions != target_fractions:
                continue
            source_distinctive_tokens = (
                _token_set(sid) - _GENERIC_MODIFIED_REFERENCE_TOKENS
            )
            if (
                _is_generic_modified_reference(sid)
                or (
                    source_distinctive_tokens
                    and source_distinctive_tokens <= target_distinctive_tokens
                )
            ):
                contextual_targets[sid] = specific_target

    for generic, sources in sources_by_generic.items():
        normalized = normalize_for_match(generic)
        if not normalized:
            continue

        # A source table identifier such as PCL/AA/S is already an exact
        # composition identity. Treating it as a generic base reference can
        # collapse neighbouring formulations into PCL/AA.
        if _has_slash_composition_chain(generic):
            continue

        if not _MODIFIED_MATERIAL_RE.search(normalized):
            generic_base_tokens = _token_set(generic) - {"matrix", "structure"}
            base_matches = {
                base for base in reliable_base_ids
                if (_token_set(base) - {"matrix", "structure"})
                and (_token_set(base) - {"matrix", "structure"}) <= generic_base_tokens
            }
            if len(base_matches) == 1:
                contextual_targets[generic] = next(iter(base_matches))
            continue

        targets: set[str] = set()
        for source in sources:
            targets.update(source_explicit_targets.get(source, set()))
            for value in source_fraction_values.get(source, set()):
                value_targets = variants_by_value.get(value, set())
                if len(value_targets) == 1:
                    targets.update(value_targets)
        if len(targets) == 1:
            contextual_targets[generic] = next(iter(targets))
        elif not targets and len(active_variants) == 1:
            contextual_targets[generic] = next(iter(active_variants))

    if not contextual_targets:
        return alias_map
    updated = dict(alias_map)
    for alias, canonical in list(updated.items()):
        target = contextual_targets.get(canonical) or contextual_targets.get(alias)
        if target and not _composition_chains_conflict(alias, target):
            updated[alias] = target
    return updated


_SYSTEM_LEVEL_RESULT_METRICS = frozenset({
    "eigenfrequency",
    "bandgap_frequency_range",
    "normalized_bandgap_frequency_range",
    "transmission_attenuation_frequency_range",
})
_STRUCTURE_LEVEL_MATERIAL_METRICS = frozenset({
    "density",
    "Youngs_modulus",
    "Poissons_ratio",
})
_STRUCTURE_LEVEL_CONTEXT_RE = re.compile(
    r"(?i)\b(?:TPMS|metamaterials?|composites?|fib(?:er|re)[- ]reinforced|"
    r"reinforced\s+structures?)\b"
)


def _sample_name_explicitly_supported(
    name: str,
    normalized_evidence: str,
    *,
    allow_short: bool,
) -> bool:
    normalized_name = normalize_for_match(name)
    if not normalized_name:
        return False
    compact = re.sub(r"[^a-z0-9]+", "", normalized_name)
    if len(compact) < 3 and not allow_short:
        return False
    return bool(re.search(
        rf"(?<![a-z0-9]){re.escape(normalized_name)}(?![a-z0-9])",
        normalized_evidence,
    ))


def repair_contextual_fact_assignments(
    facts: list[dict],
    cards: list[dict],
) -> list[dict]:
    """Move unsupported base assignments to one evidenced active variant."""
    variant_ids = {
        normalize_sample_id(card.get("sample_id") or "")
        for card in cards
        if _fraction_values(card.get("sample_id"))
    }
    active_variants = {
        normalize_sample_id(fact.get("assigned_sample_id") or "")
        for fact in facts
        if (
            fact.get("fact_type") == "performance"
            and normalize_sample_id(fact.get("assigned_sample_id") or "") in variant_ids
        )
    }
    aliases_by_id = {
        normalize_sample_id(card.get("sample_id") or ""): parse_sample_aliases(
            card.get("sample_aliases")
        )
        for card in cards
    }
    system_candidates = {
        sid
        for sid, aliases in aliases_by_id.items()
        if not _is_generic_modified_reference(sid)
        and _MODIFIED_MATERIAL_RE.search(normalize_for_match(" ".join([sid, *aliases])))
    }
    if len(active_variants) == 1:
        target = next(iter(active_variants))
    elif len(system_candidates) == 1:
        target = next(iter(system_candidates))
    else:
        return facts
    base_ids = {
        sid for sid in aliases_by_id
        if sid and sid not in variant_ids and not _MODIFIED_MATERIAL_RE.search(
            normalize_for_match(sid)
        )
    }
    for fact in facts:
        metric = str(fact.get("metric_or_parameter") or "").strip()
        current = normalize_sample_id(fact.get("assigned_sample_id") or "")
        if current and current not in base_ids:
            continue
        # subject_text is model-generated and must not validate its own assignment.
        evidence = normalize_for_match(str(fact.get("evidence_text") or ""))
        structure_property = (
            metric in _STRUCTURE_LEVEL_MATERIAL_METRICS
            and bool(_STRUCTURE_LEVEL_CONTEXT_RE.search(evidence))
        )
        if metric not in _SYSTEM_LEVEL_RESULT_METRICS and not structure_property:
            continue
        supported_names = [current, *aliases_by_id.get(current, [])] if current else []
        current_explicitly_supported = any(
            _sample_name_explicitly_supported(
                name,
                evidence,
                allow_short=index == 0,
            )
            for index, name in enumerate(supported_names)
        )
        modified_system_context = bool(
            metric in _SYSTEM_LEVEL_RESULT_METRICS
            and current != target
            and _MODIFIED_MATERIAL_RE.search(evidence)
        )
        if current_explicitly_supported and not modified_system_context:
            continue
        fact["assigned_sample_id"] = target
        fact["candidate_sample_ids"] = [target]
        fact["assignment_status"] = "assigned"
        fact["assignment_confidence"] = max(
            float(fact.get("assignment_confidence") or 0), 0.78
        )
        reason = str(fact.get("assignment_reason") or "").strip()
        fact["assignment_reason"] = (
            f"{reason}; unique_active_variant_for_system_result".strip("; ")
        )
    return facts


def _attach_aliases_to_facts(facts: list[dict], cards: list[dict]) -> list[dict]:
    aliases_by_id = {
        normalize_sample_id(card.get("sample_id") or ""): parse_sample_aliases(
            card.get("sample_aliases")
        )
        for card in cards
        if card.get("sample_id")
    }
    for fact in facts:
        sid = normalize_sample_id(fact.get("assigned_sample_id") or "")
        aliases = aliases_by_id.get(sid, [])
        if aliases:
            fact["_sample_aliases"] = aliases
    return facts


def _pair_similarity(a: str, b: str) -> float:
    na, nb = normalize_for_match(a), normalize_for_match(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if _composition_chains_conflict(a, b):
        return 0.0
    la, lb = _loading_signature(a), _loading_signature(b)
    if bool(la) != bool(lb) or (la and lb and la != lb):
        return 0.0
    if la and lb and la == lb:
        return 0.96
    ra, rb = _ratio_signature(a), _ratio_signature(b)
    if bool(ra) != bool(rb) or (ra and rb and ra != rb):
        return 0.0
    if ra and rb and ra == rb:
        return 0.92
    va, vb = _run_variant_signature(a), _run_variant_signature(b)
    if va != vb and (va or vb):
        return 0.0
    ta, tb = _token_set(a), _token_set(b)
    if not ta or not tb:
        return 0.0
    overlap = len(ta & tb)
    union = len(ta | tb)
    return overlap / union if union else 0.0


def _canonical_score(sample_id: str, mention_count: int, has_aliases: bool) -> float:
    sid = normalize_sample_id(sample_id)
    score = mention_count * 2.0
    if has_aliases:
        score += 3.0
    if _has_slash_composition_chain(sid):
        score += 8.0
        if _is_bare_slash_composition_chain(sid):
            score += 4.0
    elif _explicit_composition_chain(sid):
        score += 2.0
    if _loading_signature(sid):
        score += 2.0
    if re.search(r"(?i)\b(?:reinforced|composite|tpms|metamaterial)\b", normalize_for_match(sid)):
        score += 1.5
    if re.search(r"(?i)(?:^|[_\s/-])\d+(?:\.\d+)?[_\s/-]*needles?\b", sid):
        score += 2.0
    if re.search(r"(?i)\bbox\s*\d+\b|\bmultineedle\b|\bmultiple[-_ ]needle\b", sid):
        score -= 2.0
    if len(sid) <= 40:
        score += 1.0
    if len(sid) > 80:
        score -= 4.0
    if sid.count(" ") > 6:
        score -= 3.0
    return score


def _variant_metadata(sample: dict) -> tuple[str, str] | None:
    name = normalize_for_match(
        str(sample.get("variable_name") or "").replace("_", " ")
    )
    value = normalize_for_match(sample.get("variable_value"))
    if not name or not value or value in {"n/a", "na", "none", "unknown"}:
        return None
    if "needle" in name and any(
        token in name for token in ("number", "count", "per box", "quantity")
    ):
        name = "number of needles"
    elif "needle" in name and "spacing" in name:
        name = "needle spacing"
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value):
        value = f"{float(value):g}"
    unit = normalize_for_match(sample.get("variable_unit"))
    if name == "number of needles":
        unit = ""
    return name, f"{value}|{unit}"


def _configuration_metadata_from_id(sample_id: str) -> dict[str, str]:
    normalized = normalize_for_match(sample_id)
    count_match = _NEEDLE_COUNT_IN_ID_RE.search(normalized)
    if not count_match:
        return {}
    metadata = {"number of needles": f"{float(count_match.group(1)):g}|"}
    if re.search(r"(?i)multi(?:ple)?\s*needle|multineedle|\bneedles?\b", normalized):
        spacing_matches = _NEEDLE_SPACING_IN_ID_RE.findall(normalized)
        if spacing_matches:
            metadata["needle spacing"] = f"{float(spacing_matches[-1]):g}|mm"
    return metadata


def _material_identity_tokens(sample_id: str) -> set[str]:
    tokens = _token_set(sample_id)
    return {
        token for token in tokens
        if not re.fullmatch(r"\d+(?:\.\d+)?(?:mm)?", token)
        and not re.fullmatch(r"box\d+", token)
        and token != "multineedle"
        and token not in _CONFIGURATION_ID_TOKENS
    }


def _material_form_bucket(sample: dict) -> str:
    """Return a coarse physical form used as a hard identity boundary."""
    text = normalize_for_match(" ".join(
        str(sample.get(field) or "")
        for field in (
            "fiber_type", "sample_id", "material_system", "composition",
            "composition_expression",
        )
    ))
    if re.search(r"\b(?:(?:nano)?fib(?:er|re)s?|filaments?|yarns?|fabrics?|mats?)\b", text):
        return "fiber"
    if re.search(r"\b(?:solutions?|dispersions?|suspensions?|precursors?)\b", text):
        return "solution"
    if re.search(r"\b(?:films?|membranes?|coatings?)\b", text):
        return "film"
    if re.search(r"\baerogels?\b", text):
        return "aerogel"
    if re.search(r"\bhydrogels?\b", text):
        return "hydrogel"
    if re.search(r"\bfoams?\b", text):
        return "foam"
    if re.search(r"\b(?:bulk|powders?|particles?|glasses?|bioactive glass|bg)\b", text):
        return "bulk"
    return ""


def _canonical_needle_sample_id(sample_id: str, needle_count: str) -> str:
    """Replace an opaque multi-needle box suffix with its evidenced count."""
    sid = normalize_sample_id(sample_id)
    match = _GENERIC_MULTI_NEEDLE_SUFFIX_RE.search(sid)
    if not match:
        return sid
    prefix = sid[:match.start()].rstrip(" _-/")
    if not _material_identity_tokens(prefix):
        return sid
    separator = "_" if "_" in sid else " "
    return f"{prefix}{separator}{needle_count}{separator}needles"


def build_sample_alias_map(
    sample_mentions: list[dict],
    holistic_samples: list[dict] | None = None,
    sample_cards: list[dict] | None = None,
    *,
    similarity_threshold: float = 0.88,
) -> dict[str, str]:
    """Cluster similar sample IDs and return alias -> canonical_id mapping."""
    mention_counts: dict[str, int] = defaultdict(int)
    alias_sets: dict[str, set[str]] = defaultdict(set)
    variant_values: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    needle_alias_owners: dict[str, set[str]] = defaultdict(set)
    alias_owners: dict[str, set[str]] = defaultdict(set)
    material_forms: dict[str, set[str]] = defaultdict(set)
    composition_chains: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    treatment_states: dict[str, set[bool]] = defaultdict(set)
    all_ids: set[str] = set()

    def register(
        sample_id: str,
        aliases: Any = None,
        variant: tuple[str, str] | None = None,
        material_form: str = "",
    ) -> None:
        sid = normalize_sample_id(sample_id)
        if not sid or len(sid) < 2:
            return
        parsed_aliases = parse_sample_aliases(aliases)
        preferred_chains = _preferred_composition_chains(sid, parsed_aliases)
        all_ids.add(sid)
        mention_counts[sid] += 1
        treatment_states[sid].add(_is_treated_variant(sid))
        if material_form:
            material_forms[sid].add(material_form)
        composition_chains[sid].update(preferred_chains)
        if variant:
            name, value = variant
            variant_values[sid][name].add(value)
        for name, value in _configuration_metadata_from_id(sid).items():
            variant_values[sid][name].add(value)
        for alias in parsed_aliases:
            alias_id = normalize_sample_id(alias)
            if alias_id and alias_id != sid:
                all_ids.add(alias_id)
                alias_sets[sid].add(alias_id)
                alias_owners[alias_id].add(sid)
                mention_counts[alias_id] += 1
                treatment_states[alias_id].add(_is_treated_variant(alias_id))
                alias_chains = _preferred_composition_chains(alias_id)
                composition_chains[alias_id].update(
                    alias_chains or preferred_chains
                )
                if material_form:
                    material_forms[alias_id].add(material_form)
                alias_metadata = _configuration_metadata_from_id(alias_id)
                for name, value in alias_metadata.items():
                    variant_values[sid][name].add(value)
                    variant_values[alias_id][name].add(value)
                    if name == "number of needles":
                        needle_alias_owners[value.split("|", 1)[0]].add(sid)

    for mention in sample_mentions:
        sid = normalize_sample_id(
            mention.get("normalized_sample_id") or mention.get("mention_text") or ""
        )
        register(sid, mention.get("aliases") or [])

    for sample in holistic_samples or []:
        register(
            sample.get("sample_id") or "",
            sample.get("aliases") or [],
            _variant_metadata(sample),
            _material_form_bucket(sample),
        )

    for card in sample_cards or []:
        register(
            card.get("sample_id") or "",
            parse_sample_aliases(card.get("sample_aliases")),
            _variant_metadata(card),
            _material_form_bucket(card),
        )

    ids = sorted(all_ids, key=lambda s: (-mention_counts[s], len(s)))
    parent: dict[str, str] = {sid: sid for sid in ids}
    cluster_variants: dict[str, dict[str, set[str]]] = {
        sid: {
            name: set(values)
            for name, values in variant_values.get(sid, {}).items()
        }
        for sid in ids
    }
    cluster_forms: dict[str, set[str]] = {
        sid: set(material_forms.get(sid, set())) for sid in ids
    }
    cluster_chains: dict[str, set[tuple[str, ...]]] = {
        sid: set(composition_chains.get(sid, set())) for sid in ids
    }
    cluster_treatment_states: dict[str, set[bool]] = {
        sid: set(treatment_states.get(sid, {_is_treated_variant(sid)}))
        for sid in ids
    }

    def find(sid: str) -> str:
        while parent[sid] != sid:
            parent[sid] = parent[parent[sid]]
            sid = parent[sid]
        return sid

    def union(a: str, b: str) -> bool:
        ra, rb = find(a), find(b)
        if ra == rb:
            return True
        if len(cluster_forms[ra] | cluster_forms[rb]) > 1:
            return False
        if len(cluster_chains[ra] | cluster_chains[rb]) > 1:
            return False
        if len(cluster_treatment_states[ra] | cluster_treatment_states[rb]) > 1:
            return False
        shared_names = set(cluster_variants[ra]) & set(cluster_variants[rb])
        if any(
            cluster_variants[ra][name].isdisjoint(cluster_variants[rb][name])
            for name in shared_names
        ):
            return False
        score_a = _canonical_score(ra, mention_counts[ra], bool(alias_sets[ra]))
        score_b = _canonical_score(rb, mention_counts[rb], bool(alias_sets[rb]))
        if score_a >= score_b:
            parent[rb] = ra
            target, source = ra, rb
        else:
            parent[ra] = rb
            target, source = rb, ra
        for name, values in cluster_variants[source].items():
            cluster_variants[target].setdefault(name, set()).update(values)
        cluster_forms[target].update(cluster_forms[source])
        cluster_chains[target].update(cluster_chains[source])
        cluster_treatment_states[target].update(cluster_treatment_states[source])
        return True

    def same_needle_configuration(a: str, b: str) -> bool:
        a_values = variant_values.get(a, {}).get("number of needles", set())
        b_values = variant_values.get(b, {}).get("number of needles", set())
        a_counts = {value.split("|", 1)[0] for value in a_values}
        b_counts = {value.split("|", 1)[0] for value in b_values}
        if not a_counts or not b_counts or a_counts != b_counts:
            return False
        a_tokens = _material_identity_tokens(a)
        b_tokens = _material_identity_tokens(b)
        if a_tokens and b_tokens:
            overlap = len(a_tokens & b_tokens)
            return overlap / min(len(a_tokens), len(b_tokens)) >= 0.75
        if not a_tokens and not b_tokens or len(a_counts) != 1:
            return False
        material_id = a if a_tokens else b
        needle_count = next(iter(a_counts))
        owners = needle_alias_owners.get(needle_count, set())
        return owners == {material_id}

    id_list = list(ids)
    for i, sid_a in enumerate(id_list):
        for sid_b in id_list[i + 1:]:
            if same_needle_configuration(sid_a, sid_b):
                union(sid_a, sid_b)
                continue
            if _pair_similarity(sid_a, sid_b) >= similarity_threshold:
                union(sid_a, sid_b)
                continue
            linked = False
            for alias in alias_sets.get(sid_a, ()):
                if (
                    len(alias_owners.get(alias, ())) == 1
                    and _pair_similarity(alias, sid_b) >= similarity_threshold
                ):
                    union(sid_a, sid_b)
                    linked = True
                    break
            if linked:
                continue
            for alias in alias_sets.get(sid_b, ()):
                if (
                    len(alias_owners.get(alias, ())) == 1
                    and _pair_similarity(sid_a, alias) >= similarity_threshold
                ):
                    union(sid_a, sid_b)
                    break

    clusters: dict[str, set[str]] = defaultdict(set)
    for sid in ids:
        clusters[find(sid)].add(sid)

    mapping: dict[str, str] = {}
    for canonical, members in clusters.items():
        best = max(
            members,
            key=lambda s: _canonical_score(s, mention_counts[s], bool(alias_sets[s])),
        )
        count_values = cluster_variants[find(canonical)].get("number of needles", set())
        counts = {value.split("|", 1)[0] for value in count_values}
        if len(counts) == 1:
            needle_count = next(iter(counts))
            owners = needle_alias_owners.get(needle_count, set()) & members
            if len(owners) == 1:
                owner = next(iter(owners))
                best = _canonical_needle_sample_id(owner, needle_count)
        for member in members:
            mapping[member] = best

    # Models sometimes emit a descriptive card (PCL_S_BG_fiber) while a table
    # uses an exact composition ID (PCL/AA/S). Join them only when the first and
    # terminal composition components identify one unique, form-compatible
    # slash-delimited target. Ambiguous matches remain separate.
    target_chains = {
        canonical: _explicit_composition_chain(canonical)
        for canonical in set(mapping.values())
        if _is_bare_slash_composition_chain(canonical)
        and len(_explicit_composition_chain(canonical)) >= 3
    }
    forms_by_canonical: dict[str, set[str]] = defaultdict(set)
    for member, canonical in mapping.items():
        forms_by_canonical[canonical].update(material_forms.get(member, set()))

    for source in list(set(mapping.values())):
        if source in target_chains or _has_slash_composition_chain(source):
            continue
        source_chain = _explicit_composition_chain(source)
        if len(source_chain) < 2:
            continue
        matches = {
            target
            for target, target_chain in target_chains.items()
            if target_chain[0] == source_chain[0]
            and target_chain[-1] in source_chain[1:]
            and len(
                forms_by_canonical.get(source, set())
                | forms_by_canonical.get(target, set())
            ) <= 1
        }
        if len(matches) != 1:
            continue
        target = next(iter(matches))
        for member, canonical in list(mapping.items()):
            if canonical == source:
                mapping[member] = target
    return mapping


def apply_sample_alias_map(
    alias_map: dict[str, str],
    *,
    sample_mentions: list[dict] | None = None,
    facts: list[dict] | None = None,
    sample_cards: list[dict] | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Rewrite sample IDs using alias_map across pipeline artifacts."""

    def remap_id(value: str | None) -> str:
        sid = normalize_sample_id(value or "")
        if not sid:
            return ""
        return alias_map.get(sid, sid)

    def remapped_aliases(value: Any, canonical: str) -> set[str]:
        return {
            alias_id
            for alias in parse_sample_aliases(value)
            if (alias_id := normalize_sample_id(alias))
            and alias_id != canonical
            and remap_id(alias_id) == canonical
        }

    mentions = []
    for mention in sample_mentions or []:
        item = dict(mention)
        raw = item.get("normalized_sample_id") or item.get("mention_text") or ""
        canonical = remap_id(raw)
        if canonical:
            item["normalized_sample_id"] = canonical
            aliases = remapped_aliases(item.get("aliases"), canonical)
            if raw and remap_id(raw) != normalize_sample_id(raw):
                aliases.add(normalize_sample_id(raw))
            item["aliases"] = sorted(aliases)
        mentions.append(item)

    updated_facts = []
    for fact in facts or []:
        item = dict(fact)
        if item.get("assigned_sample_id"):
            item["assigned_sample_id"] = remap_id(item["assigned_sample_id"])
        candidates = item.get("candidate_sample_ids") or []
        if isinstance(candidates, list):
            item["candidate_sample_ids"] = [remap_id(c) for c in candidates if remap_id(c)]
        updated_facts.append(item)

    cards_by_id: dict[str, dict] = {}
    for card in sample_cards or []:
        sid = remap_id(card.get("sample_id"))
        if not sid:
            continue
        if sid not in cards_by_id:
            new_card = dict(card)
            new_card["sample_id"] = sid
            existing_aliases = remapped_aliases(card.get("sample_aliases"), sid)
            raw_sid = normalize_sample_id(card.get("sample_id") or "")
            if raw_sid and raw_sid != sid:
                existing_aliases.add(raw_sid)
            new_card["sample_aliases"] = json.dumps(sorted(existing_aliases), ensure_ascii=False) if existing_aliases else ""
            cards_by_id[sid] = new_card
        else:
            target = cards_by_id[sid]
            for field in (
                "material_system", "fiber_type", "composition_expression", "matrix_name",
                "process_route", "spinning_method", "process_parameters", "structure_methods",
                "structure_features", "evidence_text",
            ):
                if not target.get(field) and card.get(field):
                    target[field] = card[field]
            extra_aliases = remapped_aliases(card.get("sample_aliases"), sid)
            raw_sid = normalize_sample_id(card.get("sample_id") or "")
            if raw_sid:
                extra_aliases.add(raw_sid)
            current = set(parse_sample_aliases(target.get("sample_aliases")))
            merged = sorted(current | extra_aliases - {sid})
            target["sample_aliases"] = json.dumps(merged, ensure_ascii=False) if merged else ""

    return mentions, updated_facts, list(cards_by_id.values())


def merge_sample_identities(
    sample_mentions: list[dict],
    facts: list[dict],
    sample_cards: list[dict],
    holistic_samples: list[dict] | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Build alias clusters and apply canonical sample IDs."""
    alias_map = build_sample_alias_map(
        sample_mentions,
        holistic_samples=holistic_samples,
        sample_cards=sample_cards,
    )
    if not alias_map:
        return sample_mentions, facts, sample_cards
    alias_map = _augment_contextual_aliases(
        alias_map,
        facts=facts,
        holistic_samples=holistic_samples,
        sample_cards=sample_cards,
    )
    mentions, updated_facts, updated_cards = apply_sample_alias_map(
        alias_map,
        sample_mentions=sample_mentions,
        facts=facts,
        sample_cards=sample_cards,
    )
    updated_facts = repair_contextual_fact_assignments(updated_facts, updated_cards)
    return mentions, _attach_aliases_to_facts(updated_facts, updated_cards), updated_cards
