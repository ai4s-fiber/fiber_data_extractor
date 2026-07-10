"""Generic post-processing for extracted facts (all papers)."""

from __future__ import annotations

import copy
import re
from typing import Any

from app.services.extractor_v7.metric_normalize import (
    merge_duplicate_facts,
    normalize_metrics_in_facts,
)
from app.services.extractor_v7.sample_value_alignment import expand_multi_entity_facts
from app.services.grouping import normalize_sample_id

_PLACEHOLDER_VALUES = {
    "various", "varied", "different", "several", "multiple",
    "n/a", "na", "none", "unknown", "not reported", "not available",
    "see figure", "see fig", "see table", "as shown", "as shown in",
    "increased", "decreased", "higher", "lower", "similar", "comparable",
}

_COUPLED_LIST_RE = re.compile(
    r"(?is)\b(?:values?|coefficients?|constants?|results?|properties?)\s+of\s+"
    r"(.+?)\s+"
    r"(?:composites?|samples?|fibers?|fibres?|films?|fabrics?|specimens?|materials?|coatings?)\s+"
    r"(?:were|was|are|is)\s+"
    r"(?:about|approximately|around|~|ca\.?|c\.?)?\s*"
)

_VALUE_TAIL_RE = re.compile(
    r"(?is)(?:were|was|are|is)\s+"
    r"(?:about|approximately|around|~|ca\.?|c\.?)?\s*"
)

_NUMBER_RE = re.compile(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")


def is_placeholder_performance_value(value: Any) -> bool:
    text = "" if value is None else str(value).strip().lower()
    if not text:
        return True
    if text in _PLACEHOLDER_VALUES:
        return True
    if any(token in text for token in ("see fig", "see figure", "see table", "not given")):
        return True
    return not bool(_NUMBER_RE.search(text))


def _split_list_items(text: str) -> list[str]:
    cleaned = re.sub(r"\s+and\s+", ", ", text.strip(), flags=re.IGNORECASE)
    return [part.strip() for part in cleaned.split(",") if part.strip()]


def _extract_numeric_values(text: str) -> list[str]:
    return _NUMBER_RE.findall(text.replace(",", ""))


def _is_numeric_value(value: Any) -> bool:
    text = "" if value is None else str(value).strip()
    return bool(_NUMBER_RE.fullmatch(text))


def _next_fact_id(facts: list[dict], start: int) -> tuple[str, int]:
    counter = start
    while True:
        candidate = f"F{counter:06d}"
        if not any(f.get("fact_id") == candidate for f in facts):
            return candidate, counter + 1
        counter += 1


def _parse_coupled_lists(evidence: str) -> tuple[list[str], list[str]] | None:
    match = _COUPLED_LIST_RE.search(evidence)
    if not match:
        return None
    sample_names = [_clean_sample_label(name) for name in _split_list_items(match.group(1))]
    sample_names = [name for name in sample_names if name]
    tail_region = evidence[match.start():]
    tail_match = _VALUE_TAIL_RE.search(tail_region)
    if not tail_match:
        return None
    value_region = tail_region[tail_match.end():]
    respectively_pos = value_region.lower().find("respectively")
    if respectively_pos > 0:
        value_region = value_region[:respectively_pos]
    values = _extract_numeric_values(value_region)
    if len(sample_names) < 2 or len(sample_names) != len(values):
        return None
    return sample_names, values


def expand_coupled_list_facts(facts: list[dict]) -> list[dict]:
    """Split or assign facts from coupled sample/value list sentences.

  Example: "values of A, B, C composites were about 1, 2, 3 MPa"
  → one fact per sample/value pair.
    """
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
        parsed = _parse_coupled_lists(evidence)
        if not parsed:
            expanded.append(fact)
            continue

        # Facts that already carry one numeric value are handled by positional assignment.
        if _is_numeric_value(fact.get("value")) and not is_placeholder_performance_value(fact.get("value")):
            expanded.append(fact)
            continue

        sample_names, values = parsed
        for sample_name, value in zip(sample_names, values):
            clone = copy.deepcopy(fact)
            clone["value"] = value
            sid = normalize_sample_id(sample_name)
            clone["candidate_sample_ids"] = [sid]
            clone["assigned_sample_id"] = sid
            clone["assignment_status"] = "assigned"
            clone["assignment_confidence"] = max(
                float(clone.get("assignment_confidence") or 0), 0.85
            )
            clone["assignment_reason"] = "coupled_list_expansion"
            new_id, id_counter = _next_fact_id(expanded + facts, id_counter)
            clone["fact_id"] = new_id
            expanded.append(clone)

    return expanded


def assign_positional_fact_groups(facts: list[dict]) -> list[dict]:
    """Assign sample IDs to facts that share evidence but carry one value each."""
    groups: dict[tuple[str, str], list[dict]] = {}
    order: list[tuple[str, str]] = []

    for fact in facts:
        if fact.get("fact_type") != "performance":
            continue
        evidence = str(fact.get("evidence_text") or "").strip()
        metric = str(fact.get("metric_or_parameter") or "").strip().lower()
        if not evidence or not metric:
            continue
        key = (metric, evidence)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(fact)

    for key in order:
        group = groups[key]
        if len(group) < 2:
            continue
        if any(f.get("assigned_sample_id") for f in group):
            continue
        evidence = key[1]
        parsed = _parse_coupled_lists(evidence)
        if not parsed:
            continue
        sample_names, values = parsed
        if len(group) != len(values):
            continue
        value_to_sample = {value: sample for value, sample in zip(values, sample_names)}
        for fact in group:
            fact_value = str(fact.get("value") or "").strip()
            if not _is_numeric_value(fact_value):
                continue
            sid = normalize_sample_id(value_to_sample.get(fact_value, ""))
            if not sid:
                continue
            fact["assigned_sample_id"] = sid
            fact["candidate_sample_ids"] = [sid]
            fact["assignment_status"] = "assigned"
            fact["assignment_confidence"] = max(
                float(fact.get("assignment_confidence") or 0), 0.82
            )
            fact["assignment_reason"] = "positional_list_assignment"
    return facts


def enrich_sample_mentions_from_facts(
    facts: list[dict],
    sample_mentions: list[dict],
) -> list[dict]:
    """Add sample mentions discovered in coupled-list evidence."""
    known = {
        normalize_sample_id(
            m.get("normalized_sample_id") or m.get("mention_text") or ""
        )
        for m in sample_mentions
    }
    extra: list[dict] = []
    for fact in facts:
        evidence = str(fact.get("evidence_text") or "")
        parsed = _parse_coupled_lists(evidence)
        if not parsed:
            continue
        sample_names, _ = parsed
        for raw_name in sample_names:
            sid = normalize_sample_id(_clean_sample_label(raw_name))
            if not sid or sid in known:
                continue
            known.add(sid)
            extra.append({
                "mention_text": sid,
                "normalized_sample_id": sid,
                "aliases": [],
                "context_text": evidence[:300],
                "source_location": fact.get("source_location", ""),
                "source_type": "text",
                "confidence": 0.72,
            })
    return sample_mentions + extra


def _clean_sample_label(name: str) -> str:
    text = name.strip().strip("()[]")
    text = re.sub(r"\s+", " ", text)
    return text


def merge_adjacent_table_chunks(chunks: list[dict], *, max_chars: int = 12000) -> list[dict]:
    """Merge consecutive table_text chunks from the same page for richer context."""
    merged: list[dict] = []
    buffer: dict | None = None

    def flush() -> None:
        nonlocal buffer
        if buffer is not None:
            merged.append(buffer)
            buffer = None

    for chunk in chunks:
        if chunk.get("source_type") != "table_text":
            flush()
            merged.append(chunk)
            continue

        page = chunk.get("page_number")
        section = chunk.get("section_name")
        text = chunk.get("raw_text") or ""
        if buffer is None:
            buffer = copy.deepcopy(chunk)
            continue

        same_source = (
            buffer.get("page_number") == page
            and buffer.get("section_name") == section
        )
        combined_len = len(buffer.get("raw_text") or "") + len(text) + 2
        # Keep merged tables bounded so Stage 2 still gets multiple units.
        if same_source and combined_len <= max_chars and len(buffer.get("raw_text") or "") < 4000:
            buffer["raw_text"] = f"{buffer.get('raw_text', '')}\n{text}".strip()
            continue

        flush()
        buffer = copy.deepcopy(chunk)

    flush()
    return merged


def renumber_fact_ids(facts: list[dict]) -> list[dict]:
    """Ensure fact_id values are unique after multi-chunk Stage 2 merges."""
    for index, fact in enumerate(facts, 1):
        fact["fact_id"] = f"F{index:04d}"
    return facts


_MEASURABLE_PROPERTY_RE = re.compile(
    r"(?i)(tensile|compressive|flexural|modulus|strength|elongation|"
    r"piezo|voltage|current|power|sensitivity|d33|d31|g33|"
    r"conductivity|resistivity|dielectric|permittivity|"
    r"whiteness|crystallinity|diameter|porosity|density|thermal|"
    r"contact\s*angle|shrinkage|loading|content|mass\s*ratio|"
    r"stability|molecular\s*weight|degree\s*of\s*polymerization|"
    r"alpha.?cellulose|cellulose\s*content|fabric\s*size)"
)


def promote_measurable_facts(facts: list[dict]) -> list[dict]:
    """Reclassify mislabeled process/structure facts that are material properties."""
    for fact in facts:
        if fact.get("fact_type") == "performance":
            continue
        metric = str(fact.get("metric_or_parameter") or "")
        if not metric:
            continue
        if not _is_numeric_value(fact.get("value")):
            continue
        if _MEASURABLE_PROPERTY_RE.search(metric):
            fact["fact_type"] = "performance"
    return facts


def sanitize_assigned_sample_ids(
    facts: list[dict],
    sample_cards: list[dict],
    sample_mentions: list[dict] | None = None,
) -> list[dict]:
    """Drop invalid assigned_sample_id values (e.g. full-sentence aliases)."""
    valid_ids: set[str] = set()
    for card in sample_cards:
        sid = normalize_sample_id(card.get("sample_id") or "")
        if sid:
            valid_ids.add(sid)
        for alias in card.get("sample_aliases") or []:
            alias_id = normalize_sample_id(str(alias))
            if alias_id:
                valid_ids.add(alias_id)
    for mention in sample_mentions or []:
        sid = normalize_sample_id(
            mention.get("normalized_sample_id") or mention.get("mention_text") or ""
        )
        if sid:
            valid_ids.add(sid)

    for fact in facts:
        assigned = fact.get("assigned_sample_id")
        if not assigned:
            continue
        normalized = normalize_sample_id(str(assigned))
        if normalized in valid_ids:
            if normalized != assigned:
                fact["assigned_sample_id"] = normalized
            continue
        fact["assigned_sample_id"] = None
        if fact.get("assignment_status") == "assigned":
            fact["assignment_status"] = "unassigned"
            fact["assignment_confidence"] = None
    return facts


def postprocess_extracted_facts(
    facts: list[dict],
    sample_mentions: list[dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Run generic fact post-processing before sample assignment."""
    mentions = list(sample_mentions or [])
    facts = promote_measurable_facts(facts)
    facts = expand_coupled_list_facts(facts)
    facts = expand_multi_entity_facts(facts)
    mentions = enrich_sample_mentions_from_facts(facts, mentions)
    facts = assign_positional_fact_groups(facts)
    facts = expand_multi_entity_facts(facts)
    facts = normalize_metrics_in_facts(facts)
    facts = merge_duplicate_facts(facts)
    return facts, mentions
