"""Sanitize sample_id: keep conditions out of names, forbid inferred suffixes."""

from __future__ import annotations

import re

from app.services.grouping import normalize_for_match, normalize_sample_id

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

    if is_condition_only_label(sid):
        notes.append("sample_id_was_condition_only")
        return "", sid, notes

    sid, temp_notes = strip_inferred_temperature_suffix(sid, evidence)
    notes.extend(temp_notes)
    sid, load_notes = strip_inferred_loading_suffix(sid, evidence)
    notes.extend(load_notes)

    trailing = _TRAILING_CONDITION_RE.search(sid)
    if trailing and not is_explicit_sample_name_in_evidence(sid, evidence):
        condition_bit = trailing.group(0).strip()
        sid = _TRAILING_CONDITION_RE.sub("", sid).strip(" -_")
        notes.append("stripped_trailing_condition_from_sample_id")
        return normalize_sample_id(sid), condition_bit, notes

    if not is_explicit_sample_name_in_evidence(sid, evidence) and is_condition_only_label(
        sid.split()[-1] if " " in sid else sid
    ):
        notes.append("sample_id_not_explicit_in_evidence")
        return "", sid, notes

    return sid, "", notes
