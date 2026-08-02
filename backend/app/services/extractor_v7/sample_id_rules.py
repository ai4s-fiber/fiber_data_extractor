"""Sanitize sample_id: keep conditions out of names, forbid inferred suffixes."""

from __future__ import annotations

import re

from app.services.grouping import (
    is_narrative_sample_phrase,
    is_property_only_sample_label,
    normalize_for_match,
    normalize_sample_id,
    strip_nonmaterial_sample_prefix,
)
from app.services.metrics_dictionary import (
    find_metric_canonical,
    find_structure_feature_canonical,
)

# Entire sample_id must not be only a test/process condition token.
_CONDITION_ONLY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\d+(?:\.\d+)?\s*°?\s*c$", re.I),
    re.compile(r"^\d+(?:\.\d+)?\s*%\s*strain$", re.I),
    re.compile(r"^\d+\s*min(?:ute)?s?$", re.I),
    re.compile(r"^x[- ]?band$", re.I),
    re.compile(r"^\d+\s*[-–]\s*\d+\s*ghz$", re.I),
    re.compile(r"^rh\s*[=≈]?\s*\d+\s*%?$", re.I),
    re.compile(r"^strain$", re.I),
    re.compile(r"^frequency$", re.I),
)

# Trailing condition fragments to strip unless explicitly part of a sample name in evidence.
_TRAILING_CONDITION_RE = re.compile(
    r"(?i)\s+(?:at\s+)?(?:\d+(?:\.\d+)?\s*°?\s*c|\d+\s*min(?:ute)?s?|"
    r"x[- ]?band|\d+\s*[-–]\s*\d+\s*ghz|rh\s*[=≈]?\s*\d+\s*%?|"
    r"\d+(?:\.\d+)?\s*%\s*strain)\s*$"
)

_EXPLICIT_SAMPLE_SUFFIX_RE = re.compile(
    r"(?i)\b(sample|film|aerogel|aerogels|nanofiber|nanofibers|membrane|"
    r"specimen|fiber|fibers|composite|composites|powder|foam|hydrogel|coating)\b"
)

_KNOWN_SAMPLE_PREFIX_RE = re.compile(
    r"(?i)^(?:sample[\s-]?\d+|pi\d+|pi-\d+|2mz-azine-pi\d*|[a-z]{1,6}-\d+)"
)

_INFERRED_LOADING_RE = re.compile(
    r"(?i)(?:^|[-\s])(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>wt\.?%|wt%|vol\.?%|mol\.?%)(?:\s|$|[-])"
)
_INFERRED_PLAIN_PERCENT_RE = re.compile(
    r"(?i)[-\s](\d+(?:\.\d+)?)\s*%(?!\s*strain)"
)
_SEMICOLON_RUN_SUFFIX_RE = re.compile(
    r"(?i)^(?:sample|specimen|run|no\.?)\s*[-#:]?\s*\d+(?:\.\d+)?$"
)
_SEMICOLON_CONTEXT_SUFFIX_RE = re.compile(
    r"(?i)^(?:(?:optimum|optimal|optimized|optimised|best|selected|"
    r"representative|average|mean)\b.*\b(?:sample|specimen|material|fibers?)s?|"
    r"sample\s+(?:showing|with|at|under)\b.+)$"
)
_ANAPHORIC_SAMPLE_PREFIX_RE = re.compile(
    r"(?i)^(?:that|those|this|these)\s+of\s+"
)
_TABLE_ROW_SUFFIX_RE = re.compile(
    r"(?i)\s*[\[(]?\s*row\s+\d+\s*[\])]?[\s,;:]*$"
)
_DEVICE_ONLY_SAMPLE_RE = re.compile(
    r"(?i)^(?:(?:[a-z0-9]{1,12}\s+)?(?:tufting|sewing)\s+needles?|"
    r"(?:[a-z0-9]{1,12}\s+)?tufting\s+machine|"
    r"(?:tn|sn)\s*\d+(?:\.\d+)?\s+needles?|"
    r"needles?(?:\s+(?:type|code|diameter|specification))?)$"
)
_GENERIC_COLLECTION_SAMPLE_RE = re.compile(
    r"(?i)^(?:(?:one|two|three|four|five|six)|\d+)\s+"
    r"(?:types?|kinds?|groups?)\s+of\s+.+\b"
    r"(?:materials?|samples?|specimens?|composites?)$"
)
_MEASUREMENT_LABEL_SAMPLE_RE = re.compile(
    r"(?i)^.{0,60}\b(?:channel\s+width|width|diameter|length|thickness|"
    r"speed|rate|force|temperature|pressure)\s+"
    r"(?:mm|cm|m|um|nm|mpa|gpa|kpa|pa|n|kn|%|wt\s*%)$"
)
_INCOMPLETE_SAMPLE_ACTION_RE = re.compile(
    r"(?i)^(?:specimens?|samples?)\s+"
    r"(?:coated|treated|reinforced|modified|aged|conditioned)\s+"
    r"(?:with|at|by)\s+\d+(?:\.\d+)?(?:\s*(?:wt|vol|mol)?\s*%?)?$"
)
_PREPARATION_ONLY_SAMPLE_RE = re.compile(
    r"(?i)^(?:crushed|ground|cut|chopped|polished)\s+"
    r"(?:specimens?|samples?|materials?)$"
)
_LOADING_OF_FRAGMENT_RE = re.compile(
    r"(?i)^\d+(?:\.\d+)?\s*(?:(?:wt|vol|mol)\s*)?%\s+of\s+.+$"
)


def _nonmaterial_sample_reason(sample_id: str) -> str:
    words = re.sub(r"[_/-]+", " ", normalize_for_match(sample_id)).strip()
    if not words:
        return ""
    if is_property_only_sample_label(words):
        return "sample_id_was_measurement_label"
    if _DEVICE_ONLY_SAMPLE_RE.fullmatch(words):
        return "sample_id_was_apparatus"
    if _GENERIC_COLLECTION_SAMPLE_RE.fullmatch(words):
        return "sample_id_was_generic_collection"
    if _MEASUREMENT_LABEL_SAMPLE_RE.fullmatch(words):
        return "sample_id_was_measurement_label"
    if _INCOMPLETE_SAMPLE_ACTION_RE.fullmatch(words):
        return "sample_id_was_incomplete_action_phrase"
    if _PREPARATION_ONLY_SAMPLE_RE.fullmatch(words):
        return "sample_id_was_preparation_only"
    if _LOADING_OF_FRAGMENT_RE.fullmatch(words):
        return "sample_id_was_loading_fragment"
    if re.match(r"(?i)^(?:of|from|with|under|during)\b", words):
        return "sample_id_was_incomplete_prepositional_phrase"
    if re.search(r"(?i)\bobtained\s+in\s+this\s+study\b", words):
        return "sample_id_was_narrative_phrase"
    if re.search(r"(?i)\ball\s+(?:coating\s+)?concentrations?\b", words):
        return "sample_id_was_aggregate_condition_phrase"
    return ""


def is_condition_only_label(text: str) -> bool:
    cleaned = normalize_for_match(text).replace(" ", "")
    if not cleaned:
        return True
    for pattern in _CONDITION_ONLY_PATTERNS:
        if pattern.fullmatch(normalize_for_match(text).strip()):
            return True
    return False


def is_explicit_sample_name_in_evidence(sample_id: str, evidence: str) -> bool:
    """True when evidence explicitly names this specimen (e.g. PI-200°C sample)."""
    sid = normalize_sample_id(sample_id)
    if not sid:
        return False
    ev = evidence or ""
    escaped = re.escape(sid).replace(r"\ ", r"[\s_\-/]*")
    if re.search(
        rf"(?<![a-z0-9]){escaped}\s+"
        r"(?:sample|film|aerogel|aerogels|nanofiber|nanofibers|membrane|"
        r"specimen|fiber|fibers|composite|composites|powder|foam|hydrogel|coating)\b",
        ev,
        re.I,
    ):
        return True
    if _KNOWN_SAMPLE_PREFIX_RE.match(sid):
        return bool(re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", ev, re.I))
    return False


def _loading_tokens_in_text(text: str) -> set[str]:
    def token(match: re.Match[str]) -> str:
        unit = re.sub(r"\.", "", match.group("unit").lower())
        return f"{match.group('value')}_{unit}"

    return {
        token(m)
        for m in _INFERRED_LOADING_RE.finditer(text or "")
    }


_INFERRED_TEMP_SUFFIX_RE = re.compile(r"(?i)[-\s](\d+(?:\.\d+)?)\s*°?\s*c\s*$")
_EMBEDDED_CONDITION_TOKEN_RE = re.compile(
    r"(?i)^(?:(?P<temperature>[+-]?\d+(?:\.\d+)?\s*°?\s*c)|"
    r"(?P<minutes>\d+(?:\.\d+)?\s*min(?:ute)?s?))$"
)


def _canonical_embedded_condition(token: str) -> str:
    match = _EMBEDDED_CONDITION_TOKEN_RE.fullmatch(token.strip())
    if not match:
        return ""
    if match.group("temperature"):
        value = re.search(r"[+-]?\d+(?:\.\d+)?", match.group("temperature"))
        return f"{value.group(0)} °C" if value else ""
    value = re.search(r"\d+(?:\.\d+)?", match.group("minutes") or "")
    return f"{value.group(0)} min" if value else ""


def _strip_embedded_underscore_conditions(
    sample_id: str,
    evidence: str,
) -> tuple[str, str, list[str]]:
    """Move underscore-delimited temperature/time tokens into condition."""
    if "_" not in sample_id or is_explicit_sample_name_in_evidence(
        sample_id, evidence
    ):
        return sample_id, "", []

    parts = sample_id.split("_")
    kept: list[str] = []
    conditions: list[str] = []
    index = 0
    while index < len(parts):
        current = _canonical_embedded_condition(parts[index])
        if (
            current
            and index + 2 < len(parts)
            and parts[index + 1].lower() == "to"
        ):
            end = _canonical_embedded_condition(parts[index + 2])
            if end:
                conditions.append(f"{current} to {end}")
                index += 3
                continue
        if current:
            conditions.append(current)
        else:
            kept.append(parts[index])
        index += 1

    if not conditions or not any(part.strip() for part in kept):
        return sample_id, "", []
    cleaned = normalize_sample_id("_".join(part for part in kept if part.strip()))
    return cleaned, "; ".join(conditions), [
        "moved_embedded_condition_to_condition"
    ]


def strip_inferred_temperature_suffix(sample_id: str, evidence: str) -> tuple[str, list[str]]:
    """Remove -200°C style suffix if that full label never appears in evidence."""
    sid = normalize_sample_id(sample_id)
    if not sid or not _INFERRED_TEMP_SUFFIX_RE.search(sid):
        return sid, []
    if re.search(re.escape(sid), evidence or "", re.I):
        return sid, []
    base = _INFERRED_TEMP_SUFFIX_RE.sub("", sid).strip(" -_")
    if base and re.search(re.escape(base), evidence or "", re.I):
        return normalize_sample_id(base), ["removed_inferred_temperature_from_sample_id"]
    return sid, []


def strip_inferred_loading_suffix(sample_id: str, evidence: str) -> tuple[str, list[str]]:
    """Remove wt%/vol% or plain -20% suffixes from sample_id if not in evidence."""
    sid = normalize_sample_id(sample_id)
    if not sid:
        return sid, []
    evidence_loadings = _loading_tokens_in_text(evidence)
    ev_lower = (evidence or "").lower()
    notes: list[str] = []

    match = _INFERRED_LOADING_RE.search(sid)
    if match:
        unit = re.sub(r"\.", "", match.group("unit").lower())
        token = f"{match.group('value')}_{unit}"
        if token not in evidence_loadings:
            sid = _INFERRED_LOADING_RE.sub("", sid).strip(" -_")
            notes.append("removed_inferred_loading_from_sample_id")

    pct = _INFERRED_PLAIN_PERCENT_RE.search(sid)
    if pct:
        pct_label = f"{pct.group(1)}%"
        if pct_label.lower() not in ev_lower.replace(" ", ""):
            sid = _INFERRED_PLAIN_PERCENT_RE.sub("", sid).strip(" -_")
            notes.append("removed_inferred_percent_from_sample_id")

    sid = re.sub(r"\s+", " ", sid).strip()
    return normalize_sample_id(sid), notes


def sanitize_sample_id(sample_id: str, evidence: str = "") -> tuple[str, str, list[str]]:
    """Return (sample_id, condition_appendix, fix_notes)."""
    notes: list[str] = []
    sid = normalize_sample_id(sample_id)
    if not sid:
        return "", "", notes

    cleaned_row_suffix = _TABLE_ROW_SUFFIX_RE.sub("", sid).strip(" ,;:.()[]")
    if cleaned_row_suffix != sid:
        sid = normalize_sample_id(cleaned_row_suffix)
        notes.append("removed_table_row_suffix_from_sample_id")
    if not sid:
        return "", "", notes

    sid, reference_note = strip_nonmaterial_sample_prefix(sid)
    if reference_note:
        notes.append(reference_note)
    if not sid:
        return "", "", notes

    if is_narrative_sample_phrase(sid):
        notes.append("sample_id_was_narrative_phrase")
        return "", "", notes

    nonmaterial_reason = _nonmaterial_sample_reason(sid)
    if nonmaterial_reason:
        notes.append(nonmaterial_reason)
        return "", "", notes

    if is_condition_only_label(sid):
        notes.append("sample_id_was_condition_only")
        return "", sid, notes

    anaphoric = _ANAPHORIC_SAMPLE_PREFIX_RE.match(sid)
    if anaphoric:
        sid = normalize_sample_id(sid[anaphoric.end():])
        notes.append("stripped_anaphoric_sample_prefix")

    if not sid:
        notes.append("sample_id_was_empty_after_cleanup")
        return "", "", notes

    if " of " in sid.lower():
        prefix, remainder = re.split(r"(?i)\s+of\s+", sid, maxsplit=1)
        if remainder and (
            find_metric_canonical(prefix)
            or find_structure_feature_canonical(prefix)
        ):
            sid = normalize_sample_id(remainder)
            notes.append("stripped_metric_prefix_from_sample_id")

    condition_appendix = ""
    if ";" in sid and not is_explicit_sample_name_in_evidence(sid, evidence):
        base, suffix = (part.strip() for part in sid.split(";", 1))
        if base and _SEMICOLON_RUN_SUFFIX_RE.fullmatch(suffix):
            sid = normalize_sample_id(f"{base} {suffix}")
            notes.append("normalized_semicolon_run_sample_id")
        elif base and _SEMICOLON_CONTEXT_SUFFIX_RE.fullmatch(suffix):
            sid = normalize_sample_id(base)
            condition_appendix = suffix
            notes.append("moved_contextual_sample_suffix_to_condition")

    sid, embedded_condition, embedded_notes = _strip_embedded_underscore_conditions(
        sid, evidence
    )
    if embedded_condition:
        condition_appendix = "; ".join(
            value for value in (condition_appendix, embedded_condition) if value
        )
    notes.extend(embedded_notes)

    sid, temp_notes = strip_inferred_temperature_suffix(sid, evidence)
    notes.extend(temp_notes)
    sid, load_notes = strip_inferred_loading_suffix(sid, evidence)
    notes.extend(load_notes)

    numeric_tail = re.search(r"(?<!\d)(\d+)$", sid)
    if numeric_tail and evidence:
        escaped = re.escape(sid).replace("_", r"[\s_/-]*")
        exact = re.search(
            rf"(?<![a-z0-9]){escaped}(?![\d.a-z])",
            evidence,
            re.IGNORECASE,
        )
        decimal_extension = re.search(
            rf"(?<![a-z0-9]){escaped}\.\d",
            evidence,
            re.IGNORECASE,
        )
        if decimal_extension and not exact:
            notes.append("sample_id_was_decimal_prefix_only")
            return "", condition_appendix, notes

    trailing = _TRAILING_CONDITION_RE.search(sid)
    if trailing and not is_explicit_sample_name_in_evidence(sid, evidence):
        condition_bit = trailing.group(0).strip()
        sid = _TRAILING_CONDITION_RE.sub("", sid).strip(" -_")
        notes.append("stripped_trailing_condition_from_sample_id")
        merged_condition = "; ".join(
            value for value in (condition_appendix, condition_bit) if value
        )
        return normalize_sample_id(sid), merged_condition, notes

    if not is_explicit_sample_name_in_evidence(sid, evidence) and is_condition_only_label(
        sid.split()[-1] if " " in sid else sid
    ):
        notes.append("sample_id_not_explicit_in_evidence")
        merged_condition = "; ".join(value for value in (condition_appendix, sid) if value)
        return "", merged_condition, notes

    return sid, condition_appendix, notes
