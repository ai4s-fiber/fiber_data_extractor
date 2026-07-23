"""Sample-value and metric-value alignment for multi-entity evidence sentences."""

from __future__ import annotations

import copy
import re
from collections import Counter, defaultdict
from typing import Any

from app.services.grouping import (
    is_material_sample_id,
    normalize_for_match,
    normalize_sample_id,
)
from app.services.metrics_dictionary import (
    find_metric_canonical,
    find_process_parameter_canonical,
)
from app.services.extractor_v7.sample_id_rules import sanitize_sample_id
from app.services.extractor_v7.sample_identity import (
    is_numbered_sample_variant,
    parse_sample_aliases,
)
from app.services.extractor_v7.value_parse import (
    parse_scientific_value,
    validate_scientific_notation,
)

_NUMBER_PATTERN = r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
_NUMBER_RE = re.compile(_NUMBER_PATTERN)

_PAREN_BLOCK_RE = re.compile(r"\(([^)]+)\)")
_PAREN_NUMERIC_RE = re.compile(
    r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
)

_THAN_PAREN_RE = re.compile(
    r"(?is)(?:lower|higher|greater|less|smaller|larger)\s+than\s+"
    r"([^,(]+?)\s*"
    r"\(\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"[^)]*\)"
)

_COMPARED_PAREN_RE = re.compile(
    r"(?is)(?:compared to|compared with|versus|vs\.?)\s+"
    r"([^,(]+?)\s*"
    r"\(\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"[^)]*\)"
)

_RESULT_UNIT_PATTERN = (
    r"%|MPa|GPa|kPa|Pa|g\s*/\s*g(?:\s+of\s+sorbent)?|mg\s*/\s*g|"
    r"g\s*g[⁻-]?1|W\s*/\s*mK|W\s*m[⁻-]?1\s*K[⁻-]?1|S\s*/\s*(?:m|cm)|"
    r"V|mV|µV|μV|A|mA|µA|μA|nA|nm|µm|μm|mm|cm|cm[⁻-]?1|eV|°"
)

_VALUE_FOR_SAMPLE_RE = re.compile(
    rf"(?is)(?P<value>{_NUMBER_PATTERN})\s*(?:{_RESULT_UNIT_PATTERN})?\s+"
    rf"(?:for|of)\s+(?P<sample>(?:the\s+)?[A-Za-z][A-Za-z0-9µμβγδ/\-_.+% ]{{0,80}}?)"
    rf"(?=(?:\s+(?:and|while|whereas)\s+{_NUMBER_PATTERN}\b)|"
    rf"(?:\s+at\s+{_NUMBER_PATTERN}\b)|[,.;)]|$)"
)

_SAMPLE_BEFORE_RESULT_RE = re.compile(
    rf"(?is)\b(?:of|for)\s+"
    rf"(?P<sample>(?:the\s+)?[A-Za-z][A-Za-z0-9µμβγδ/\-_.+% ]{{0,80}}?)\s+"
    rf"(?:was|were|is|are|reached|showed|exhibited)\b"
    rf"(?P<link>.{{0,90}}?)\(?\s*(?P<value>{_NUMBER_PATTERN})\s*"
    rf"(?:{_RESULT_UNIT_PATTERN})?"
)

_RESPECTIVE_TWO_LIST_RE = re.compile(
    rf"(?is)(?P<value1>{_NUMBER_PATTERN})\s*(?:{_RESULT_UNIT_PATTERN})?\s*"
    rf"(?:,\s*|\band\s+)(?P<value2>{_NUMBER_PATTERN})\s*"
    rf"(?:{_RESULT_UNIT_PATTERN})?\s+for\s+"
    rf"(?P<sample1>[A-Za-z][A-Za-z0-9µμβγδ/\-_.+% ]{{0,80}}?)\s+and\s+"
    rf"(?P<sample2>[A-Za-z][A-Za-z0-9µμβγδ/\-_.+% ]{{0,80}}?)\s*"
    rf"(?:\(\s*respectively\s*\)|,?\s+respectively)"
)

_TREATMENT_SAMPLE_RE = re.compile(
    r"(?i)\b(?:raw|untreated|unmodified|pristine|neat|control|reference|original|"
    r"treated|modified|functionalized|functionalised|acetylated|optimized|optimised|"
    r"coated|grafted|crosslinked|cross-linked)\b"
)
_SAMPLE_FORM_RE = re.compile(
    r"(?i)\b(?:sample|specimen|fiber|fibre|fabric|film|membrane|composite|aerogel|"
    r"hydrogel|foam|yarn|tow|material|sorbent)s?\b"
)
_NON_SAMPLE_PREFIX_RE = re.compile(
    r"(?i)^(?:use\s+of|as\s+(?:a|the)|at\s+|for\s+|during\s+|after\s+|"
    r"it\s+(?:was|is)|there\s+(?:was|were)|this\s+(?:was|is)|that\s+(?:was|is)|"
    r"standard\s+deviation|amount\s+of|concentration\s+of|temperature\s+of)"
)

_METRIC_VALUE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(?is)(?:dielectric constant|relative permittivity|real permittivity|"
            r"permittivity|εr|ε\s*r)\s*(?:of|was|is|were|are|=|:)?\s*"
            r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
        ),
        "dielectric_constant",
    ),
    (
        re.compile(
            r"(?is)loss tangent\s*(?:of|was|is|were|are|=|:)?\s*"
            r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
        ),
        "loss_tangent",
    ),
    (
        re.compile(
            r"(?is)(?:dielectric loss|imaginary permittivity|ε″|epsilon double prime)\s*"
            r"(?:of|was|is|were|are|=|:)?\s*"
            r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
        ),
        "dielectric_loss",
    ),
    (
        re.compile(
            r"(?is)(?:imidization|imidisation)(?:\s+degree)?\s*"
            r"(?:of|was|is|were|are|achieving|=|:)?\s*"
            r"([+-]?\d+(?:\.\d+)?)\s*%?"
        ),
        "imidization_degree",
    ),
]

_COMPRESSIVE_FROM_TO_RE = re.compile(
    r"(?is)compressive\s+stress(?:es)?\s+"
    r"(?:decreased|reduced|increased|changed|varied|dropped|rose)?\s*"
    r"(?:from\s+)?"
    r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s+to\s+"
    r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
)

_CYCLE_AFTER_RE = re.compile(r"(?is)after\s+(\d+)\s+cycles?")

_SAMPLE_TOKEN_RE = re.compile(
    r"(?i)\b(?:sample[\s-]?\d+|pi\d+|pi-\d+|2mz-azine-pi\d*|composite[\s-]?\d+|"
    r"[a-z]-\d+)\b"
)

_SAMPLE_ORDER_STOPWORDS = frozenset({
    "a", "an", "and", "based", "composite", "composites", "fabric", "fabrics",
    "fiber", "fibers", "fibre", "fibres", "film", "films", "material", "materials",
    "sample", "samples", "specimen", "specimens", "the", "with",
})
_CONTRAST_CUE_RE = re.compile(r"(?i)\b(?:whereas|while)\b")
_MODIFIED_CARD_RE = re.compile(
    r"(?i)\b(?:tpms|metamaterials?|composites?|reinforced|filled)\b"
)
_MODIFIED_EVIDENCE_RE = re.compile(
    r"(?i)\b(?:tpms|metamaterials?|composite(?:\s+material)?|"
    r"fib(?:er|re)[- ]+(?:reinforced|filled|enhanc\w*))\b"
)
_RELATIVE_CHANGE_METRIC_RE = re.compile(
    r"(?i)(?:reduction|decrease|increase|improvement|enhancement|change)"
)


def _catalog_sample_order(evidence: str, sample_cards: list[dict] | None) -> tuple[str, ...]:
    """Resolve explicitly contrasted base samples in their evidence order."""
    cards: list[tuple[str, tuple[str, ...]]] = []
    seen_ids: set[str] = set()
    for card in sample_cards or []:
        sid = normalize_sample_id(card.get("sample_id") or "")
        if not sid or sid in seen_ids or is_numbered_sample_variant(sid):
            continue
        names = [
            sid,
            *parse_sample_aliases(card.get("sample_aliases")),
            str(card.get("material_system") or ""),
        ]
        normalized_names = tuple(dict.fromkeys(
            normalize_for_match(name) for name in names if normalize_for_match(name)
        ))
        if normalized_names:
            cards.append((sid, normalized_names))
            seen_ids.add(sid)
    if len(cards) < 2:
        return ()

    name_owners: dict[str, set[str]] = defaultdict(set)
    for sid, names in cards:
        for name in names:
            if len(name) >= 2:
                name_owners[name].add(sid)

    normalized_evidence = normalize_for_match(evidence)
    exact_positions: list[tuple[int, int, str]] = []
    for sid, names in cards:
        best: tuple[int, int] | None = None
        for name in sorted(names, key=len, reverse=True):
            if len(name_owners.get(name, ())) != 1:
                continue
            match = re.search(
                rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9/_+\-])",
                normalized_evidence,
            )
            if match:
                candidate = (match.start(), -len(name))
                if best is None or candidate < best:
                    best = candidate
        if best is not None:
            exact_positions.append((best[0], best[1], sid))
    elliptical_variant_list = bool(re.search(
        r"(?i)\b(?:raw|neat|untreated|control|original)\s+and\s+"
        r"(?:treated|modified|acetylated|functionalized|coated)\b",
        evidence,
    ))
    if len(exact_positions) >= 2 and not elliptical_variant_list:
        exact_positions.sort()
        return tuple(sid for _, _, sid in exact_positions)

    token_sets: dict[str, set[str]] = {}
    token_counts: Counter[str] = Counter()
    for sid, names in cards:
        tokens = {
            token
            for name in names
            for token in re.split(r"[\s_/\-]+", name)
            if len(token) >= 2 and not token.isdigit() and token not in _SAMPLE_ORDER_STOPWORDS
        }
        token_sets[sid] = tokens
        token_counts.update(tokens)

    positions: list[tuple[int, str]] = (
        [(position, sid) for position, _length, sid in exact_positions]
        if not elliptical_variant_list
        else []
    )
    positioned_ids = {sid for _, sid in positions}
    for sid, names in cards:
        if sid in positioned_ids:
            continue
        unique_tokens = {token for token in token_sets[sid] if token_counts[token] == 1}
        if not unique_tokens:
            continue
        position: int | None = None
        for name in sorted(names, key=len, reverse=True):
            name_tokens = set(re.split(r"[\s_/\-]+", name))
            if not (name_tokens & unique_tokens):
                continue
            match = re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", normalized_evidence)
            if match and (position is None or match.start() < position):
                position = match.start()
        for token in unique_tokens:
            match = re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", normalized_evidence)
            if match and (position is None or match.start() < position):
                position = match.start()
        if position is not None:
            positions.append((position, sid))

    positioned_ids = {sid for _, sid in positions}
    modified_cards = {
        sid
        for sid, names in cards
        if _MODIFIED_CARD_RE.search(" ".join(names))
    }
    modified_match = _MODIFIED_EVIDENCE_RE.search(normalized_evidence)
    if modified_match and len(modified_cards) == 1:
        modified_sid = next(iter(modified_cards))
        if modified_sid not in positioned_ids:
            positions.append((modified_match.start(), modified_sid))

    positions.sort()
    return tuple(sid for _, sid in positions)


def _normalize_number(value: Any) -> str:
    text = str(value or "").strip().replace(",", "")
    sci = parse_scientific_value(text)
    if sci:
        text = sci
    match = _NUMBER_RE.search(text)
    if not match:
        return text.lower()
    num = float(match.group())
    if abs(num) >= 1:
        return f"{num:g}".lower()
    return f"{num:.6g}".lower()


def _value_after_metric_match(match: re.Match[str], evidence: str) -> str:
    """Read numeric value; only attach ×10^n when it immediately follows the mantissa."""
    from app.services.extractor_v7.value_parse import normalize_scientific_text

    raw = match.group(1)
    tail_start = match.end(1)
    tail = evidence[tail_start: min(len(evidence), tail_start + 14)]
    tail_norm = normalize_scientific_text(tail)
    if re.search(r"(?:×|x|\*|·)\s*10|[eE]\s*[+-]?\d", tail_norm):
        combined = parse_scientific_value(raw + tail)
        if combined:
            return combined
    parsed = parse_scientific_value(raw)
    return parsed if parsed else raw


def _numbers_equal(a: Any, b: Any) -> bool:
    return _normalize_number(a) == _normalize_number(b)


def _value_occurrences(evidence: str, value: Any) -> list[re.Match[str]]:
    return [
        match
        for match in _NUMBER_RE.finditer(evidence.replace(",", ""))
        if _numbers_equal(match.group(), value)
    ]


def _metric_terms(metric: str) -> tuple[str, ...]:
    canonical = find_metric_canonical(metric) or metric
    normalized = canonical.replace("_", " ").strip().lower()
    extras = {
        "weight_percent_gain": ("wpg", "weight percent gain", "weight percentage gain"),
        "degree_of_substitution": ("degree of substitution", "substitution"),
        "degree_of_acetylation": ("degree of acetylation", "acetyl %", "acetylation"),
        "oil_absorption_capacity": ("oil absorption", "oil sorption", "oil absorbency"),
        "weight_loss": ("weight loss", "mass loss"),
    }
    terms = [normalized] if normalized else []
    terms.extend(extras.get(canonical, ()))
    return tuple(dict.fromkeys(term for term in terms if term))


def classify_non_result_numeric_role(fact: dict) -> str | None:
    """Identify numbers that are unambiguously not the claimed material result."""
    if fact.get("fact_type") != "performance":
        return None
    evidence = str(fact.get("evidence_text") or "")
    value = fact.get("value")
    if not evidence or value is None:
        return None
    occurrences = _value_occurrences(evidence, value)
    if not occurrences:
        return None

    metric = str(fact.get("metric_or_parameter") or "")
    metric_terms = _metric_terms(metric)
    roles: list[str | None] = []
    normalized_evidence = evidence.replace(",", "")

    for match in occurrences:
        before = normalized_evidence[max(0, match.start() - 80):match.start()]
        after = normalized_evidence[match.end():min(len(normalized_evidence), match.end() + 80)]
        local = f"{before}{match.group()}{after}".lower()
        before_lower = before.lower()

        if re.search(
            r"(?i)(?:standard\s+deviation|std\.?|standard\s+error|uncertainty|"
            r"confidence\s+interval|coefficient\s+of\s+variation)\s*(?:of|=|:|was|is)?\s*$",
            before,
        ) or re.search(r"[±+]\s*$", before):
            roles.append("uncertainty_statistic")
            continue

        if re.search(r"(?i)\b(?:sample|specimen|run|trial|group)\s*(?:no\.?|number|#)?\s*$", before):
            roles.append("sample_or_run_identifier")
            continue

        actual_unit = ""
        unit_match = re.match(
            r"\s*(%|wt\.?\s*%|vol\.?\s*%|g|mg|kg|ml|mL|L|mol|mmol|M|mM|"
            r"°\s*C|°C|C\b|h|hr|hours?|min(?:ute)?s?|s|cycles?)(?=\s|[),.;]|$)",
            after,
            flags=re.I,
        )
        if unit_match:
            actual_unit = unit_match.group(1).lower().replace(" ", "")

        claimed_metric_near = any(term in local for term in metric_terms)
        if actual_unit in {"°c", "c", "h", "hr", "hour", "hours", "min", "minute", "minutes", "s", "cycle", "cycles"}:
            temperature_metrics = {
                "surface_temperature", "glass_transition_temperature",
                "melting_temperature", "decomposition_temperature",
            }
            canonical = find_metric_canonical(metric) or metric
            if canonical not in temperature_metrics:
                roles.append("experimental_condition")
                continue

        reagent_signal = re.search(
            r"(?i)\b(?:catalyst|reagent|initiator|crosslinker|cross-linker|solvent|"
            r"solution|anhydride|acid|base|salt|oxidant|reductant|curing\s+agent)\b",
            local,
        )
        direct_reagent_signal = re.search(
            r"(?i)\b(?:catalyst|reagent|initiator|crosslinker|cross-linker|solvent|"
            r"solution|anhydride|acid|base|salt|oxidant|reductant|curing\s+agent)\b",
            f"{before[-25:]}{match.group()}{after[:45]}",
        )
        amount_unit = actual_unit in {
            "%", "wt.%", "wt%", "vol.%", "vol%", "g", "mg", "kg", "ml", "l", "mol", "mmol", "m", "mm",
        }
        if reagent_signal and amount_unit and (direct_reagent_signal or not claimed_metric_near):
            roles.append("reagent_or_process_amount")
            continue

        if re.search(r"(?i)\b(?:at|for|during|after)\s*$", before_lower) and actual_unit:
            roles.append("experimental_condition")
            continue

        roles.append(None)

    non_empty = [role for role in roles if role]
    if non_empty and len(non_empty) == len(roles):
        return Counter(non_empty).most_common(1)[0][0]
    return None


def _clean_label(text: str) -> str:
    label = text.strip().strip(" ,;.")
    label = re.sub(r"^(?:the|a|an)\s+", "", label, flags=re.I)
    label = re.sub(r"^(?:and|or)\s+", "", label, flags=re.I)
    label = re.sub(r"\s+(?:compared to|compared with|than|vs\.?|versus|and)\s*$", "", label, flags=re.I)
    return normalize_sample_id(label)


def _is_plausible_sample_label(label: str, *, strict: bool = False) -> bool:
    if re.search(r"[/_+\-]\s*$", str(label or "")):
        return False
    cleaned = _clean_label(label)
    if not cleaned or len(cleaned) > 90 or _NON_SAMPLE_PREFIX_RE.search(cleaned):
        return False
    if len(cleaned.split()) > 8 or re.search(r"[.!?]\s", cleaned):
        return False
    if re.fullmatch(
        r"(?i)(?:this|that|the)\s+(?:particular\s+)?(?:material|sample|specimen)|"
        r"(?:both|all|these|those)\s+(?:materials|samples|specimens)",
        cleaned,
    ):
        return False
    if re.search(
        r"(?i)\b(?:obtained\s+with|resulted\s+in|shown\s+in\s+table|"
        r"weight\s+loss|oil\s+absorption(?:\s+capacity)?|initial\s+stage|"
        r"using\s+(?:its|various|the)|raw\s+and\s+\w+)",
        cleaned,
    ):
        return False
    if re.search(
        r"(?i)\b(?:resulted|resulting|found|reported|showed|exhibited|reached|"
        r"increased|decreased|measured|observed|was|were|is|are)\b",
        cleaned,
    ):
        return False
    if normalize_for_match(cleaned) in {
        "sample", "specimen", "fiber", "fibre", "fabric", "film", "membrane",
        "composite", "aerogel", "hydrogel", "foam", "material", "sorbent",
    }:
        return False
    if re.fullmatch(
        r"(?i)(?:both|two|all|these|those|the)\s+(?:the\s+)?"
        r"(?:samples?|specimens?|fibers?|fibres?|materials?)",
        cleaned,
    ):
        return False
    if re.fullmatch(r"[A-Za-z]", cleaned):
        return True
    if _TREATMENT_SAMPLE_RE.search(cleaned) or _SAMPLE_FORM_RE.search(cleaned):
        return True
    if re.search(r"\b[A-Za-z]{1,12}[-_]?\d+(?:\.\d+)?(?:wt|vol)?%?\b", cleaned):
        return True
    if re.search(r"\b(?:PI|PVDF|PAN|PVA|PLA|PA|PET|PEEK|CFRP|GFRP)\b", cleaned, re.I):
        return True
    if re.fullmatch(
        r"[A-Za-z][A-Za-z0-9]{0,15}(?:[/_+\-][A-Za-z][A-Za-z0-9]{0,15})+",
        cleaned,
    ):
        return True
    if strict:
        return False
    return len(cleaned.split()) <= 3 and not re.search(
        r"(?i)\b(?:value|degree|order|increase|decrease|loss|gain|capacity|result)\b",
        cleaned,
    )


def _legacy_sample_name_before_paren(before: str) -> str:
    """Take the sample token immediately preceding '(' (nearest-neighbor binding)."""
    text = before.rstrip()
    if not text:
        return ""
    segment = re.split(r"(?<=[,;])\s*|\s+\band\s+", text, flags=re.I)[-1].strip()
    for splitter in (r"\bthan\b", r"\bvs\.?\b", r"\bversus\b", r"\bcompared to\b"):
        if re.search(splitter, segment, flags=re.I):
            segment = re.split(splitter, segment, flags=re.I)[-1].strip()
    segment = re.sub(
        r"(?is)\b(?:was|is|were|are|showed|exhibited|reached|of|at|for|with|the)\s+$",
        "",
        segment,
    ).strip()
    match = re.search(
        r"([A-Za-z0-9][A-Za-z0-9\s/\-_.+%°]*?)\s*$",
        segment,
    )
    if not match:
        return ""
    return _clean_label(match.group(1))


def _value_from_paren_content(inner: str) -> str | None:
    inner_norm = inner.strip()
    if not re.match(rf"^{_NUMBER_PATTERN}\b", inner_norm):
        return None
    sci = parse_scientific_value(inner_norm)
    if sci:
        return sci
    num = _PAREN_NUMERIC_RE.search(inner_norm)
    if not num:
        return None
    return num.group(1)


def parse_sample_value_pairs(evidence: str) -> list[tuple[str, str]]:
    """Extract explicit sample→value pairs from common scientific prose forms."""
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(name: str, value: str) -> None:
        sid = _clean_label(name)
        if not sid or not value:
            return
        if not is_material_sample_id(sid) and not re.fullmatch(r"[A-Za-z]", sid):
            return
        key = (normalize_for_match(sid), _normalize_number(value))
        if key in seen:
            return
        seen.add(key)
        pairs.append((sid, value))

    from app.services.extractor_v7.hard_validation import refine_sample_name_before_paren

    for match in _RESPECTIVE_TWO_LIST_RE.finditer(evidence):
        sample1 = match.group("sample1")
        sample2 = match.group("sample2")
        if (
            _is_plausible_sample_label(sample1, strict=True)
            and _is_plausible_sample_label(sample2, strict=True)
        ):
            add(sample1, match.group("value1"))
            add(sample2, match.group("value2"))

    for match in _PAREN_BLOCK_RE.finditer(evidence):
        inner = match.group(1)
        value = _value_from_paren_content(inner)
        if not value:
            continue
        name = refine_sample_name_before_paren(evidence[: match.start()])
        if not name:
            name = _legacy_sample_name_before_paren(evidence[: match.start()])
        if name and _is_plausible_sample_label(name):
            add(name, value)

    for match in _THAN_PAREN_RE.finditer(evidence):
        add(match.group(1), match.group(2))
    for match in _COMPARED_PAREN_RE.finditer(evidence):
        add(match.group(1), match.group(2))

    for match in _VALUE_FOR_SAMPLE_RE.finditer(evidence):
        name = match.group("sample")
        if _is_plausible_sample_label(name, strict=True):
            add(name, match.group("value"))

    for match in _SAMPLE_BEFORE_RESULT_RE.finditer(evidence):
        name = match.group("sample")
        if _is_plausible_sample_label(name, strict=True):
            add(name, match.group("value"))
    return pairs


def extract_explicit_sample_names(evidence: str) -> list[str]:
    """Return high-confidence sample names grounded by a sample/value statement."""
    names: list[str] = []
    seen: set[str] = set()
    for name, _ in parse_sample_value_pairs(evidence):
        key = normalize_for_match(name)
        if key and key not in seen:
            seen.add(key)
            names.append(name)
    return names


def parse_metric_value_pairs(evidence: str) -> list[tuple[str, str]]:
    """Extract metric→value pairs from explicit metric phrases."""
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pattern, metric in _METRIC_VALUE_PATTERNS:
        for match in pattern.finditer(evidence):
            value = _value_after_metric_match(match, evidence)
            key = (metric, _normalize_number(value))
            if key in seen:
                continue
            seen.add(key)
            pairs.append((metric, value))
    return pairs


def count_alignment_entities(evidence: str) -> tuple[int, int, int]:
    sample_pairs = parse_sample_value_pairs(evidence)
    metric_pairs = parse_metric_value_pairs(evidence)
    sample_names = {normalize_for_match(s) for s, _ in sample_pairs}
    sample_names.update(_SAMPLE_TOKEN_RE.findall(evidence))
    values = {_normalize_number(v) for _, v in sample_pairs}
    values.update(_normalize_number(v) for _, v in metric_pairs)
    values.update(_normalize_number(v) for v in _NUMBER_RE.findall(evidence.replace(",", "")))
    values.discard("")
    return len(sample_names), len(values), len(metric_pairs)


def _value_linked_to_sample(evidence: str, sample_id: str, value: Any) -> bool:
    target_value = _normalize_number(value)
    if not target_value:
        return False
    sid_norm = normalize_for_match(sample_id)
    pairs = parse_sample_value_pairs(evidence)
    if pairs:
        return any(
            normalize_for_match(sid) == sid_norm and _numbers_equal(val, value)
            for sid, val in pairs
        )
    lower = evidence.lower()
    val_match = re.search(re.escape(str(value).strip()), lower.replace(",", ""))
    if not val_match:
        val_match = re.search(re.escape(target_value), lower.replace(",", ""))
    if not val_match:
        return False
    window = lower[max(0, val_match.start() - 80):val_match.start()]
    return sid_norm.replace(" ", "") in window.replace(" ", "")


def _metric_matches_value(evidence: str, metric: str, value: Any) -> bool:
    canonical = find_metric_canonical(metric) or metric
    for parsed_metric, parsed_value in parse_metric_value_pairs(evidence):
        if parsed_metric == canonical and _numbers_equal(parsed_value, value):
            return True
    blob = normalize_for_match(f"{metric} {evidence}")
    if canonical == "loss_tangent" and any(t in blob for t in ("loss tangent", "tan delta", "tan d")):
        return str(value).lower() in blob.replace(",", "") or _normalize_number(value) in blob
    if canonical == "dielectric_constant" and any(t in blob for t in ("permittivity", "dielectric constant")):
        return _normalize_number(value) in blob.replace(",", "")
    return True


def verify_fact_alignment(fact: dict) -> tuple[bool, str | None]:
    """Reverse-check sample_id / metric / value against evidence_text."""
    if fact.get("fact_type") != "performance":
        return True, None
    evidence = str(fact.get("evidence_text") or "")
    value = fact.get("value")
    if not evidence or not _NUMBER_RE.search(str(value or "")):
        return True, None

    sample_id = str(fact.get("assigned_sample_id") or "").strip()
    metric = str(fact.get("metric_or_parameter") or "")
    sample_count, value_count, metric_count = count_alignment_entities(evidence)

    if "respectively_order_inferred_from_explicit_evidence" in str(
        fact.get("assignment_reason") or ""
    ):
        return True, None

    if sample_count >= 2 and value_count >= 2:
        if sample_id and not _value_linked_to_sample(evidence, sample_id, value):
            return False, "multi_sample_value_alignment_unclear"
        if not sample_id:
            return False, "multi_sample_value_alignment_unclear"

    if metric_count >= 2:
        if not _metric_matches_value(evidence, metric, value):
            return False, "metric_value_mismatch"

    return True, None


def _next_fact_id(facts: list[dict], start: int) -> tuple[str, int]:
    counter = start
    while True:
        candidate = f"A{counter:05d}"
        if not any(f.get("fact_id") == candidate for f in facts):
            return candidate, counter + 1
        counter += 1


def expand_multi_entity_facts(facts: list[dict]) -> list[dict]:
    """Split or reassign facts from list-style / parenthesis / multi-metric evidence."""
    expanded: list[dict] = []
    id_counter = max(
        (int(re.sub(r"\D", "", f.get("fact_id", "")) or "0") for f in facts),
        default=0,
    ) + 1

    for fact in facts:
        if fact.get("fact_type") != "performance":
            expanded.append(fact)
            continue

        evidence = str(fact.get("evidence_text") or "")
        sample_pairs = parse_sample_value_pairs(evidence)
        metric_pairs = parse_metric_value_pairs(evidence)
        current_value = str(fact.get("value") or "").strip()
        has_numeric = bool(_NUMBER_RE.search(current_value))

        if len(metric_pairs) >= 2:
            matched = [mp for mp in metric_pairs if _numbers_equal(mp[1], current_value)] if has_numeric else []
            if has_numeric and len(matched) == 1:
                metric_name, _ = matched[0]
                fact["metric_or_parameter"] = metric_name
                expanded.append(fact)
                for other_metric, other_value in metric_pairs:
                    if other_metric == metric_name:
                        continue
                    clone = copy.deepcopy(fact)
                    clone["metric_or_parameter"] = other_metric
                    clone["value"] = other_value
                    clone["assignment_reason"] = "multi_metric_alignment_split"
                    new_id, id_counter = _next_fact_id(expanded + facts, id_counter)
                    clone["fact_id"] = new_id
                    expanded.append(clone)
                continue
            if not has_numeric or len(matched) != 1:
                for metric_name, metric_value in metric_pairs:
                    clone = copy.deepcopy(fact)
                    clone["metric_or_parameter"] = metric_name
                    clone["value"] = metric_value
                    clone["assignment_reason"] = "multi_metric_alignment_split"
                    new_id, id_counter = _next_fact_id(expanded + facts, id_counter)
                    clone["fact_id"] = new_id
                    expanded.append(clone)
                continue

        if sample_pairs:
            if has_numeric:
                matches = [pair for pair in sample_pairs if _numbers_equal(pair[1], current_value)]
                if len(matches) == 1:
                    fact["assigned_sample_id"] = matches[0][0]
                    fact["candidate_sample_ids"] = [matches[0][0]]
                    fact["assignment_status"] = "assigned"
                    fact["assignment_confidence"] = max(float(fact.get("assignment_confidence") or 0), 0.88)
                    fact["assignment_reason"] = _append_reason(
                        fact.get("assignment_reason"), "evidence_sample_value_alignment"
                    )
                    expanded.append(fact)
                    continue
                if len(sample_pairs) >= 2:
                    fact["_alignment_review_required"] = True
                expanded.append(fact)
                continue
                expanded.append(fact)
                continue
            if len(sample_pairs) < 2:
                expanded.append(fact)
                continue
            for sample_name, sample_value in sample_pairs:
                clone = copy.deepcopy(fact)
                clone["value"] = sample_value
                clone["assigned_sample_id"] = sample_name
                clone["candidate_sample_ids"] = [sample_name]
                clone["assignment_status"] = "assigned"
                clone["assignment_confidence"] = max(float(clone.get("assignment_confidence") or 0), 0.88)
                clone["assignment_reason"] = _append_reason(
                    clone.get("assignment_reason"), "evidence_sample_value_alignment"
                )
                new_id, id_counter = _next_fact_id(expanded + facts, id_counter)
                clone["fact_id"] = new_id
                expanded.append(clone)
            continue

        expanded.append(fact)

    return expanded


def align_partial_explicit_pairs(
    facts: list[dict], sample_cards: list[dict] | None = None,
) -> list[dict]:
    """Complete one-to-one assignments when a source block truncates one explicit pair."""
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for fact in facts:
        if fact.get("fact_type") != "performance":
            continue
        evidence = str(fact.get("evidence_text") or "")
        metric = find_metric_canonical(str(fact.get("metric_or_parameter") or "")) or str(
            fact.get("metric_or_parameter") or ""
        )
        grouped[(metric, evidence, normalize_for_match(fact.get("condition") or ""))].append(fact)

    for (_metric, evidence, _condition), group in grouped.items():
        value_facts = [fact for fact in group if _NUMBER_RE.search(str(fact.get("value") or ""))]
        if len(value_facts) < 2:
            continue
        pairs = parse_sample_value_pairs(evidence)
        ordered_samples = _catalog_sample_order(evidence, sample_cards)
        if not pairs or len(ordered_samples) != len(value_facts):
            continue

        used_samples: set[str] = set()
        unmatched_facts: list[dict] = []
        for fact in value_facts:
            pair_names = [
                sample for sample, value in pairs
                if _numbers_equal(value, fact.get("value"))
            ]
            matching_samples = [
                sample for sample in ordered_samples
                if any(
                    normalize_for_match(sample) == normalize_for_match(pair_name)
                    for pair_name in pair_names
                )
            ]
            if len(matching_samples) == 1:
                used_samples.add(matching_samples[0])
            else:
                unmatched_facts.append(fact)

        remaining_samples = [sample for sample in ordered_samples if sample not in used_samples]
        if len(unmatched_facts) != 1 or len(remaining_samples) != 1:
            continue
        fact = unmatched_facts[0]
        sample_name = remaining_samples[0]
        fact["assigned_sample_id"] = sample_name
        fact["candidate_sample_ids"] = [sample_name]
        fact["assignment_status"] = "assigned"
        fact["assignment_confidence"] = max(
            float(fact.get("assignment_confidence") or 0), 0.84
        )
        fact["assignment_reason"] = _append_reason(
            fact.get("assignment_reason"), "one_to_one_remaining_sample_alignment"
        )
        fact.pop("_alignment_review_required", None)
    return facts


def align_contrastive_sample_value_facts(
    facts: list[dict], sample_cards: list[dict] | None = None,
) -> list[dict]:
    """Bind two values to samples stated on opposite sides of a contrast cue."""
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for fact in facts:
        if fact.get("fact_type") != "performance":
            continue
        metric = find_metric_canonical(str(fact.get("metric_or_parameter") or "")) or str(
            fact.get("metric_or_parameter") or ""
        )
        evidence = str(fact.get("evidence_text") or "")
        condition = normalize_for_match(str(fact.get("condition") or ""))
        grouped[(metric, evidence, condition)].append(fact)

    for (_metric, evidence, _condition), group in grouped.items():
        cue = _CONTRAST_CUE_RE.search(evidence)
        if not cue:
            continue
        value_facts = [
            fact for fact in group
            if _NUMBER_RE.search(str(fact.get("value") or ""))
        ]
        ordered_samples = _catalog_sample_order(evidence, sample_cards)
        if len(value_facts) != 2 or len(ordered_samples) != 2:
            continue

        positioned: list[tuple[int, dict]] = []
        for fact in value_facts:
            occurrences = _value_occurrences(evidence, fact.get("value"))
            if not occurrences:
                positioned = []
                break
            positioned.append((occurrences[0].start(), fact))
        if len(positioned) != 2:
            continue
        positioned.sort(key=lambda item: item[0])
        if not (positioned[0][0] < cue.start() < positioned[1][0]):
            continue
        if _numbers_equal(
            positioned[0][1].get("value"), positioned[1][1].get("value")
        ):
            continue

        for (_, fact), sample_id in zip(positioned, ordered_samples):
            fact["assigned_sample_id"] = sample_id
            fact["candidate_sample_ids"] = [sample_id]
            fact["assignment_status"] = "assigned"
            fact["assignment_confidence"] = max(
                float(fact.get("assignment_confidence") or 0), 0.9
            )
            fact["assignment_reason"] = _append_reason(
                fact.get("assignment_reason"), "contrast_clause_value_alignment"
            )
            fact.pop("_alignment_review_required", None)
    return facts


def align_contrastive_relative_change_facts(
    facts: list[dict], sample_cards: list[dict] | None = None,
) -> list[dict]:
    """Bind a trailing relative change to the right-hand contrast sample."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for fact in facts:
        if fact.get("fact_type") == "performance":
            evidence = str(fact.get("evidence_text") or "")
            if evidence:
                grouped[evidence].append(fact)

    for evidence, group in grouped.items():
        cue = _CONTRAST_CUE_RE.search(evidence)
        ordered_samples = _catalog_sample_order(evidence, sample_cards)
        if not cue or len(ordered_samples) != 2:
            continue
        right_sample = ordered_samples[1]
        right_value_positions: list[int] = []
        for fact in group:
            metric = find_metric_canonical(
                str(fact.get("metric_or_parameter") or "")
            ) or str(fact.get("metric_or_parameter") or "")
            if _RELATIVE_CHANGE_METRIC_RE.search(metric):
                continue
            if normalize_for_match(fact.get("assigned_sample_id") or "") != normalize_for_match(
                right_sample
            ):
                continue
            right_value_positions.extend(
                occurrence.start()
                for occurrence in _value_occurrences(evidence, fact.get("value"))
                if occurrence.start() > cue.end()
            )
        if not right_value_positions:
            continue

        for fact in group:
            metric = find_metric_canonical(
                str(fact.get("metric_or_parameter") or "")
            ) or str(fact.get("metric_or_parameter") or "")
            if (
                not _RELATIVE_CHANGE_METRIC_RE.search(metric)
                or str(fact.get("unit") or "").strip() != "%"
            ):
                continue
            occurrences = [
                occurrence
                for occurrence in _value_occurrences(evidence, fact.get("value"))
                if occurrence.start() > cue.end()
            ]
            if len(occurrences) != 1:
                continue
            value_position = occurrences[0].start()
            if not any(position < value_position for position in right_value_positions):
                continue
            local = evidence[cue.end():value_position]
            if not re.search(
                r"(?i)\b(?:decreas|reduc|increas|improv|enhanc|chang)\w*\b",
                local,
            ):
                continue
            fact["assigned_sample_id"] = right_sample
            fact["candidate_sample_ids"] = [right_sample]
            fact["assignment_status"] = "assigned"
            fact["assignment_confidence"] = max(
                float(fact.get("assignment_confidence") or 0), 0.9
            )
            fact["assignment_reason"] = _append_reason(
                fact.get("assignment_reason"),
                "contrast_clause_relative_change_alignment",
            )
            fact.pop("_alignment_review_required", None)
    return facts


def align_anaphoric_respectively_facts(
    facts: list[dict], sample_cards: list[dict] | None = None,
) -> list[dict]:
    """Resolve unnamed 'both samples ... respectively' rows from explicit order evidence."""
    order_hints: dict[str, Counter[tuple[str, ...]]] = defaultdict(Counter)
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)

    for fact in facts:
        if fact.get("fact_type") != "performance":
            continue
        metric = find_metric_canonical(str(fact.get("metric_or_parameter") or "")) or str(
            fact.get("metric_or_parameter") or ""
        )
        evidence = str(fact.get("evidence_text") or "")
        pairs = parse_sample_value_pairs(evidence)
        ordered_names = tuple(name for name, _ in pairs)
        if len(ordered_names) >= 2:
            order_hints[metric][ordered_names] += 1
        condition = normalize_for_match(str(fact.get("condition") or ""))
        grouped[(metric, evidence, condition)].append(fact)

    for (metric, evidence, _condition), group in grouped.items():
        if "respectively" not in evidence.lower():
            continue
        value_facts = [
            fact for fact in group
            if _NUMBER_RE.search(str(fact.get("value") or ""))
        ]
        explicit_pairs = parse_sample_value_pairs(evidence)
        if explicit_pairs and all(
            any(_numbers_equal(pair_value, fact.get("value")) for _, pair_value in explicit_pairs)
            for fact in value_facts
        ):
            continue
        ordered_samples = _catalog_sample_order(evidence, sample_cards)
        if len(ordered_samples) != len(value_facts):
            hints = order_hints.get(metric)
            if not hints:
                continue
            ordered_samples, _ = hints.most_common(1)[0]
        if len(value_facts) != len(ordered_samples) or len(value_facts) < 2:
            continue
        value_facts.sort(
            key=lambda fact: next(
                (
                    match.start()
                    for match in _NUMBER_RE.finditer(evidence)
                    if _numbers_equal(match.group(), fact.get("value"))
                ),
                len(evidence),
            )
        )
        if any(
            not _value_occurrences(evidence, fact.get("value"))
            for fact in value_facts
        ):
            continue
        for fact, sample_name in zip(value_facts, ordered_samples):
            fact["assigned_sample_id"] = sample_name
            fact["candidate_sample_ids"] = [sample_name]
            fact["assignment_status"] = "assigned"
            fact["assignment_confidence"] = max(
                float(fact.get("assignment_confidence") or 0), 0.86
            )
            fact["assignment_reason"] = _append_reason(
                fact.get("assignment_reason"), "respectively_order_inferred_from_explicit_evidence"
            )
            fact.pop("_alignment_review_required", None)
    return facts


def mark_non_result_numeric_roles(facts: list[dict]) -> list[dict]:
    """Hard-reject facts whose selected number is an explicit condition or metadata value."""
    for fact in facts:
        if (
            fact.get("extraction_method") in {
                "AI_holistic_table", "rule_table_performance",
            }
            and fact.get("_source_table_row") is not None
        ):
            continue
        role = classify_non_result_numeric_role(fact)
        if not role:
            continue
        fact["_hard_reject"] = True
        fact["_hard_reject_reason"] = role
        fact["assignment_status"] = "rejected"
        fact["assignment_reason"] = _append_reason(
            fact.get("assignment_reason"), f"non_result_numeric_role:{role}"
        )
    return facts


def _fix_metric_semantics(fact: dict) -> dict:
    """Correct metric_or_parameter when evidence semantics disagree."""
    evidence = str(fact.get("evidence_text") or "")
    metric = str(fact.get("metric_or_parameter") or "")
    fact_type = str(fact.get("fact_type") or "performance")

    if fact_type == "process":
        process_metric = find_process_parameter_canonical(metric) or metric
        if process_metric != metric:
            fact["metric_or_parameter"] = process_metric
            fact["assignment_reason"] = _append_reason(
                fact.get("assignment_reason"), "process_metric_canonicalized"
            )
        return fact

    unit = re.sub(r"\s+", "", str(fact.get("unit") or "").lower())
    process_metric = ""
    if unit in {"ml/h", "ml/hr", "mlmin-1", "ml/min", "μl/min", "µl/min"} and re.search(
        r"(?i)\bflow\s*rate|\bflowrate\b", evidence
    ):
        if re.search(r"(?i)\b(?:per[- ]needle|flow\s*rate\s+per\s+needle)\b", evidence):
            process_metric = "flow_rate_per_needle"
        elif re.search(r"(?i)\btotal\s+flow\s*rate|\btotal\s+flowrate\b", evidence):
            process_metric = "total_flow_rate"
        else:
            process_metric = "flow_rate"
    elif unit in {"wt%", "wt.%", "w/v%"} and re.search(
        r"(?i)\b(?:solution|polymer)\s+concentration\b", evidence
    ):
        process_metric = "polymer_concentration"
    elif unit in {"kv/cm", "kv/mm", "v/m"} and re.search(
        r"(?i)\belectric\s+field\s+(?:strength|intensity)\b", evidence
    ):
        process_metric = "electric_field_strength"
    elif unit in {"kv", "v"} and re.search(r"(?i)\belectrospinn", evidence) and re.search(
        r"(?i)\b(?:applied\s+)?voltage\b", evidence
    ):
        process_metric = "voltage"

    if process_metric:
        fact["fact_type"] = "process"
        fact["metric_or_parameter"] = process_metric
        fact["category"] = "process"
        fact["assignment_reason"] = _append_reason(
            fact.get("assignment_reason"), "process_semantics_from_evidence_and_unit"
        )
        return fact

    subject_label = re.sub(
        r"\s+",
        " ",
        str(fact.get("subject_text") or "").replace("_", " ").strip().lower(),
    )
    fiber_context = " ".join([
        evidence,
        str(fact.get("condition") or ""),
    ])
    diameter_subject = bool(
        re.search(r"(?i)\b(?:average|mean)\s+(?:nano)?fiber\s+diameter\b", subject_label)
        or (
            subject_label in {
                "average diameter", "mean diameter", "fiber diameter", "nanofiber diameter",
            }
            and re.search(r"(?i)\b(?:nano)?fib(?:er|re)s?\b", fiber_context)
            and not re.search(r"(?i)\bpore\b", fiber_context)
        )
    )
    if diameter_subject and unit in {"nm", "μm", "µm", "um", "mm"}:
        if metric != "fiber_diameter":
            fact["metric_or_parameter"] = "fiber_diameter"
            fact["assignment_reason"] = _append_reason(
                fact.get("assignment_reason"), "metric_corrected_from_diameter_subject"
            )
        metric = "fiber_diameter"

    canonical = find_metric_canonical(metric) or metric

    if canonical and canonical != metric:
        fact["metric_or_parameter"] = canonical
        fact["assignment_reason"] = _append_reason(
            fact.get("assignment_reason"), "metric_canonicalized"
        )

    if re.search(r"(?is)imidization|imidisation", evidence):
        if canonical in ("crystallinity_Xc", "crystallinity", "degree_of_crystallinity"):
            fact["metric_or_parameter"] = "imidization_degree"
            fact["assignment_reason"] = _append_reason(
                fact.get("assignment_reason"), "metric_corrected_imidization_degree"
            )

    from_to = _COMPRESSIVE_FROM_TO_RE.search(evidence)
    if from_to and canonical in ("cyclic_compression_stability", "compression_stability"):
        reason = str(fact.get("assignment_reason") or "")
        if "cyclic_stability_retention" in reason:
            return fact
        fact["metric_or_parameter"] = "compressive_stress"
        fact["assignment_reason"] = _append_reason(
            fact.get("assignment_reason"), "metric_corrected_compressive_stress"
        )

    return fact


def _append_reason(existing: Any, suffix: str) -> str:
    text = str(existing or "").strip()
    if suffix in text:
        return text
    return f"{text}; {suffix}".strip("; ") if text else suffix


def _sanitize_fact_sample_ids(fact: dict) -> dict:
    evidence = str(fact.get("evidence_text") or "")
    for field in ("assigned_sample_id",):
        raw = str(fact.get(field) or "").strip()
        if not raw:
            continue
        cleaned, cond, notes = sanitize_sample_id(raw, evidence)
        if cond:
            existing = str(fact.get("condition") or "").strip()
            fact["condition"] = f"{existing}; {cond}".strip("; ") if existing else cond
        if cleaned != raw:
            fact[field] = cleaned or None
            if not cleaned:
                fact["assignment_status"] = "unassigned"
            fact["assignment_reason"] = _append_reason(
                fact.get("assignment_reason"), "sample_id_sanitized"
            )
        for note in notes:
            fact["assignment_reason"] = _append_reason(fact.get("assignment_reason"), note)

    candidates = fact.get("candidate_sample_ids")
    if isinstance(candidates, list):
        new_candidates: list[str] = []
        for cid in candidates:
            cleaned, cond, _ = sanitize_sample_id(str(cid), evidence)
            if cleaned:
                new_candidates.append(cleaned)
            elif cond and not fact.get("condition"):
                fact["condition"] = cond
        fact["candidate_sample_ids"] = new_candidates
    return fact


def sanitize_fact_sample_labels(facts: list[dict]) -> list[dict]:
    """Sanitize model-proposed sample labels before building the sample catalog."""
    return [_sanitize_fact_sample_ids(fact) for fact in facts]


def _reconcile_fact_value(fact: dict) -> dict:
    evidence = str(fact.get("evidence_text") or "")
    value = fact.get("value")
    if not evidence or value is None:
        return fact
    fixed, valid = validate_scientific_notation(value, evidence)
    if not valid:
        fact["_alignment_review_required"] = True
        fact["assignment_reason"] = _append_reason(
            fact.get("assignment_reason"), "scientific_notation_incomplete"
        )
        return fact
    if fixed != str(value).strip():
        fact["value"] = fixed
        fact["assignment_reason"] = _append_reason(
            fact.get("assignment_reason"), "scientific_notation_restored"
        )
    return fact


def _compressive_stability_value(v_before: str, v_after: str) -> tuple[str, str]:
    """Retention ratio (%) or descriptive text for cyclic_compression_stability."""
    try:
        before = float(v_before)
        after = float(v_after)
        if before != 0:
            ratio = 100.0 * after / before
            return f"{ratio:.2f}", "%"
    except ValueError:
        pass
    return f"decreased from {v_before} to {v_after}", ""


def expand_compressive_stress_from_to(facts: list[dict]) -> list[dict]:
    """Split compressive stress A→B after N cycles into stress + stability facts."""
    expanded: list[dict] = []
    id_counter = max(
        (int(re.sub(r"\D", "", f.get("fact_id", "")) or "0") for f in facts),
        default=0,
    ) + 1
    emitted: set[tuple[str, str]] = set()

    for fact in facts:
        if fact.get("fact_type") != "performance":
            expanded.append(fact)
            continue
        evidence = str(fact.get("evidence_text") or "")
        match = _COMPRESSIVE_FROM_TO_RE.search(evidence)
        if not match:
            expanded.append(fact)
            continue

        v_before, v_after = match.group(1), match.group(2)
        cycle_match = _CYCLE_AFTER_RE.search(evidence)
        cycle_cond = f"after {cycle_match.group(1)} cycles" if cycle_match else ""
        current = str(fact.get("value") or "").strip()
        canonical = find_metric_canonical(str(fact.get("metric_or_parameter") or "")) or ""
        sample_key = str(fact.get("assigned_sample_id") or "")

        relates = canonical in (
            "cyclic_compression_stability",
            "compression_stability",
            "compressive_stress",
        ) or current in {v_before, v_after} or (
            cycle_match and current == cycle_match.group(1)
        )
        if not relates:
            expanded.append(fact)
            continue

        emit_key = (evidence, sample_key)
        if emit_key in emitted:
            continue
        emitted.add(emit_key)

        stability_val, stability_unit = _compressive_stability_value(v_before, v_after)
        base = copy.deepcopy(fact)
        for metric_name, val, unit, cond_bits, reason in (
            (
                "compressive_stress",
                v_before,
                fact.get("unit") or "",
                ["before cycling"],
                "compressive_stress_before",
            ),
            (
                "compressive_stress",
                v_after,
                fact.get("unit") or "",
                [cycle_cond or "after cycling"],
                "compressive_stress_after",
            ),
            (
                "cyclic_compression_stability",
                stability_val,
                stability_unit or fact.get("unit") or "",
                [cycle_cond, f"from {v_before} to {v_after}"],
                "cyclic_stability_retention",
            ),
        ):
            clone = copy.deepcopy(base)
            clone["metric_or_parameter"] = metric_name
            clone["value"] = val
            if unit:
                clone["unit"] = unit
            cond = str(clone.get("condition") or "").strip()
            extra = "; ".join(c for c in cond_bits if c)
            clone["condition"] = f"{cond}; {extra}".strip("; ") if cond else extra
            clone["assignment_reason"] = _append_reason(
                clone.get("assignment_reason"), reason
            )
            new_id, id_counter = _next_fact_id(expanded + facts, id_counter)
            clone["fact_id"] = new_id
            expanded.append(clone)

    return expanded


_CONFIGURATION_LABEL_RE = re.compile(
    r"(?i)\b(?:case\s+(?:of\s+)?)?(?P<count>\d+)\s*[- ]?needles?\b"
)
_CONFIGURATION_VALUE_RE = re.compile(
    rf"(?is)\b(?:was|were|is|are|reached|achieved|with\s+(?:a\s+)?value\s+of)\s*"
    rf"(?P<value>{_NUMBER_PATTERN})"
)


def _configuration_value_pairs(evidence: str) -> list[tuple[str, str]]:
    matches = list(_CONFIGURATION_LABEL_RE.finditer(evidence or ""))
    pairs: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(evidence)
        segment = evidence[match.end():end]
        value_match = _CONFIGURATION_VALUE_RE.search(segment)
        if value_match:
            pairs.append((f"{int(match.group('count'))} needles", value_match.group("value")))
    return pairs


def align_explicit_configuration_variants(
    facts: list[dict],
    sample_cards: list[dict] | None,
) -> list[dict]:
    """Bind values to catalog variants explicitly named as N-needle cases."""
    cards = sample_cards or []
    for fact in facts:
        if fact.get("fact_type") != "performance":
            continue
        evidence = str(fact.get("evidence_text") or "")
        matching_labels = {
            label for label, value in _configuration_value_pairs(evidence)
            if _numbers_equal(value, fact.get("value"))
        }
        if len(matching_labels) != 1:
            continue
        label = next(iter(matching_labels))
        count = re.search(r"\d+", label).group(0)
        candidates: list[tuple[int, str]] = []
        for card in cards:
            sid = normalize_sample_id(card.get("sample_id") or "")
            if not sid:
                continue
            variable_metric = find_process_parameter_canonical(
                str(card.get("variable_name") or "")
            )
            variable_value = _normalize_number(card.get("variable_value"))
            if variable_metric == "number_of_needles" and variable_value:
                if variable_value != _normalize_number(count):
                    continue
                candidates.append((30, sid))
                continue

            sid_text = re.sub(r"[_/]", " ", sid)
            if re.search(rf"(?i)\b{re.escape(count)}\s*[- ]?needles?\b", sid_text):
                candidates.append((20, sid))
                continue

            supporting_texts = [
                *parse_sample_aliases(card.get("sample_aliases")),
                str(card.get("composition_expression") or ""),
                str(card.get("evidence_text") or ""),
            ]
            if any(
                re.search(
                    rf"(?i)\b{re.escape(count)}\s*[- ]?needles?\b",
                    re.sub(r"[_/]", " ", text),
                )
                for text in supporting_texts
            ):
                candidates.append((10, sid))
        if not candidates:
            continue
        best_score = max(score for score, _ in candidates)
        best_ids = {sid for score, sid in candidates if score == best_score}
        if len(best_ids) != 1:
            continue
        sample_id = next(iter(best_ids))
        fact["assigned_sample_id"] = sample_id
        fact["candidate_sample_ids"] = [sample_id]
        fact["assignment_status"] = "assigned"
        fact["assignment_confidence"] = max(
            float(fact.get("assignment_confidence") or 0), 0.98
        )
        fact["assignment_reason"] = _append_reason(
            fact.get("assignment_reason"), "explicit_configuration_value_pair"
        )
    return facts


def apply_sample_value_alignment(
    facts: list[dict],
    sample_cards: list[dict] | None = None,
) -> list[dict]:
    """Expand multi-entity evidence and mark facts that fail reverse alignment."""
    facts = expand_multi_entity_facts(facts)
    facts = align_contrastive_sample_value_facts(facts, sample_cards)
    facts = align_contrastive_relative_change_facts(facts, sample_cards)
    facts = align_partial_explicit_pairs(facts, sample_cards)
    facts = align_anaphoric_respectively_facts(facts, sample_cards)
    facts = expand_compressive_stress_from_to(facts)
    for i, fact in enumerate(facts):
        facts[i] = _fix_metric_semantics(fact)
        facts[i] = _sanitize_fact_sample_ids(fact)
        facts[i] = _reconcile_fact_value(fact)
    from app.services.extractor_v7.hard_validation import apply_hard_validation

    facts = apply_hard_validation(facts)
    facts = align_explicit_configuration_variants(facts, sample_cards)
    from app.services.extractor_v7.evidence_audit import apply_evidence_reverse_lookup

    facts = apply_evidence_reverse_lookup(facts)
    facts = mark_non_result_numeric_roles(facts)
    for fact in facts:
        if fact.get("_hard_reject"):
            fact["_alignment_verified"] = False
            continue
        if (
            fact.get("extraction_method") in {
                "AI_holistic_table", "rule_table_performance",
            }
            and fact.get("_source_table_row") is not None
        ):
            fact["_alignment_verified"] = True
            fact.pop("_alignment_review_required", None)
            continue
        ok, reason = verify_fact_alignment(fact)
        fact["_alignment_verified"] = ok
        if not ok:
            fact["_alignment_review_required"] = True
            fact["assignment_reason"] = _append_reason(fact.get("assignment_reason"), reason or "")
    return facts
