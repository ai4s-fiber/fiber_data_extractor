"""Sample-value and metric-value alignment for multi-entity evidence sentences."""

from __future__ import annotations

import copy
import re
from typing import Any

from app.services.grouping import normalize_for_match, normalize_sample_id
from app.services.metrics_dictionary import find_metric_canonical
from app.services.extractor_v7.sample_id_rules import sanitize_sample_id
from app.services.extractor_v7.value_parse import (
    parse_scientific_value,
    validate_scientific_notation,
)

_NUMBER_RE = re.compile(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")

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


def _clean_label(text: str) -> str:
    label = text.strip().strip(" ,;.")
    label = re.sub(r"^(?:and|or)\s+", "", label, flags=re.I)
    label = re.sub(r"\s+(?:compared to|compared with|than|vs\.?|versus|and)\s*$", "", label, flags=re.I)
    return normalize_sample_id(label)


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
    sci = parse_scientific_value(inner_norm)
    if sci:
        return sci
    num = _PAREN_NUMERIC_RE.search(inner_norm)
    if not num:
        return None
    return num.group(1)


def parse_sample_value_pairs(evidence: str) -> list[tuple[str, str]]:
    """Extract sample→value pairs; parentheses bind to nearest preceding sample name."""
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(name: str, value: str) -> None:
        sid = _clean_label(name)
        if not sid or not value:
            return
        key = (normalize_for_match(sid), _normalize_number(value))
        if key in seen:
            return
        seen.add(key)
        pairs.append((sid, value))

    from app.services.extractor_v7.hard_validation import refine_sample_name_before_paren

    for match in _PAREN_BLOCK_RE.finditer(evidence):
        inner = match.group(1)
        value = _value_from_paren_content(inner)
        if not value:
            continue
        name = refine_sample_name_before_paren(evidence[: match.start()])
        if not name:
            name = _legacy_sample_name_before_paren(evidence[: match.start()])
        if name:
            add(name, value)

    for match in _THAN_PAREN_RE.finditer(evidence):
        add(match.group(1), match.group(2))
    for match in _COMPARED_PAREN_RE.finditer(evidence):
        add(match.group(1), match.group(2))
    return pairs


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

    if sample_count >= 2 and value_count >= 2:
        if sample_id and not _value_linked_to_sample(evidence, sample_id, value):
            return False, "multi_sample_value_alignment_unclear"
        if not sample_id:
            return False, "multi_sample_value_alignment_unclear"

    if metric_count >= 2:
        if not _metric_matches_value(evidence, metric, value):
            return False, "metric_value_mismatch"

    if sample_id and value_count >= 2 and not _value_linked_to_sample(evidence, sample_id, value):
        return False, "sample_value_mismatch"

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

        if len(sample_pairs) >= 2:
            if has_numeric:
                matches = [pair for pair in sample_pairs if _numbers_equal(pair[1], current_value)]
                if len(matches) == 1:
                    fact["assigned_sample_id"] = matches[0][0]
                    fact["candidate_sample_ids"] = [matches[0][0]]
                    fact["assignment_status"] = "assigned"
                    fact["assignment_confidence"] = max(float(fact.get("assignment_confidence") or 0), 0.88)
                    fact["assignment_reason"] = "parenthesis_sample_value_alignment"
                    expanded.append(fact)
                    continue
                if len(matches) > 1:
                    fact["_alignment_review_required"] = True
                    expanded.append(fact)
                    continue
                fact["_alignment_review_required"] = True
                expanded.append(fact)
                continue
            for sample_name, sample_value in sample_pairs:
                clone = copy.deepcopy(fact)
                clone["value"] = sample_value
                clone["assigned_sample_id"] = sample_name
                clone["candidate_sample_ids"] = [sample_name]
                clone["assignment_status"] = "assigned"
                clone["assignment_confidence"] = max(float(clone.get("assignment_confidence") or 0), 0.88)
                clone["assignment_reason"] = "parenthesis_sample_value_alignment"
                new_id, id_counter = _next_fact_id(expanded + facts, id_counter)
                clone["fact_id"] = new_id
                expanded.append(clone)
            continue

        expanded.append(fact)

    return expanded


def _fix_metric_semantics(fact: dict) -> dict:
    """Correct metric_or_parameter when evidence semantics disagree."""
    evidence = str(fact.get("evidence_text") or "")
    metric = str(fact.get("metric_or_parameter") or "")
    canonical = find_metric_canonical(metric) or metric

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


def apply_sample_value_alignment(facts: list[dict]) -> list[dict]:
    """Expand multi-entity evidence and mark facts that fail reverse alignment."""
    facts = expand_multi_entity_facts(facts)
    facts = expand_compressive_stress_from_to(facts)
    for i, fact in enumerate(facts):
        facts[i] = _fix_metric_semantics(fact)
        facts[i] = _sanitize_fact_sample_ids(fact)
        facts[i] = _reconcile_fact_value(fact)
    from app.services.extractor_v7.hard_validation import apply_hard_validation

    facts = apply_hard_validation(facts)
    from app.services.extractor_v7.evidence_audit import apply_evidence_reverse_lookup

    facts = apply_evidence_reverse_lookup(facts)
    for fact in facts:
        ok, reason = verify_fact_alignment(fact)
        fact["_alignment_verified"] = ok
        if not ok:
            fact["_alignment_review_required"] = True
            fact["assignment_reason"] = _append_reason(fact.get("assignment_reason"), reason or "")
    return facts
