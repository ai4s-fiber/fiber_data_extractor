"""Hard post-processing rules: metric semantics, sample-value binding, conditions."""

from __future__ import annotations

import copy
import re
from typing import Any

from app.services.grouping import normalize_for_match, normalize_sample_id
from app.services.metrics_dictionary import find_metric_canonical
from app.services.validation import normalize_unit
from app.services.extractor_v7.value_parse import validate_scientific_notation

_FIBER_LENGTH_RE = re.compile(
    r"(?i)average\s+fiber\s+length|mean\s+fiber\s+length|fiber\s+length|length\s+of\s+(?:the\s+)?(?:nanofiber|fiber)"
)
_FIBER_DIAMETER_RE = re.compile(
    r"(?i)average\s+(?:fiber\s+)?diameter|mean\s+(?:fiber\s+)?diameter|"
    r"fiber\s+diameter|diameter\s+of\s+(?:the\s+)?(?:nanofiber|fiber)"
)
_ROUGHNESS_RE = re.compile(
    r"(?i)surface\s+roughness|\bRa\b|\bRq\b|\bRms\b|\brms\s+roughness"
)
_NANOFIBER_RE = re.compile(r"(?i)nanofiber|nanofibers")
_AEROGEL_RE = re.compile(r"(?i)\baerogel\b")
_PI1_RE = re.compile(r"(?i)\bPI\s*1\b|\bPI1\b")
_SAMPLE_ID_TAIL_RE = re.compile(
    r"(?i)\b("
    r"2MZ-AZINE-PI-\d+%(?:\s+aerogel)?|"
    r"2MZ-AZINE-PI\d+(?:\s+aerogel)?|"
    r"2MZ-AZINE-PI(?:\s+nanofibers?|\s+aerogel)?|"
    r"PI\d+(?:\s+aerogel)?|"
    r"PI(?:\s+nanofiber|\s+nanofibers|\s+aerogel)?|"
    r"Sample[\s-]?\d+"
    r")\s*$"
)
_COMPRESSIVE_FROM_TO_RE = re.compile(
    r"(?is)compressive\s+stress(?:es)?\s+"
    r"(?:decreased|reduced|increased|changed|varied|dropped|rose|showed\s+no\s+decay)?\s*"
    r"(?:from\s+)?"
    r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s+to\s+"
    r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
)
_CYCLE_RE = re.compile(r"(?is)(\d+)\s*(?:compression\s+)?cycles?")
_STRAIN_RE = re.compile(r"(?is)(\d+(?:\.\d+)?)\s*%\s*strain")
_TEMP_COND_RE = re.compile(
    r"(?is)(?:at|@)\s*(\d+(?:\.\d+)?)\s*(?:°\s*c|°c|degrees?\s+c|c)(?![A-Za-z])"
)
_TRANSITION_VALUE_PATTERN = r"\d+(?:\.\d+)?(?:\s*[-–]\s*\d+(?:\.\d+)?)?"
_EXPLICIT_TRANSITION_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "knee_strain",
        "%",
        re.compile(
            rf"(?is)\bknee\b.{{0,40}}?\b(?:at|near|around|about|of)\b\s*"
            rf"(?:about|approximately|roughly|ca\.?|~)?\s*"
            rf"(?:(?:a|the)\s+)?(?:strain\s+(?:of|=)\s*)?"
            rf"(?P<value>{_TRANSITION_VALUE_PATTERN})\s*%"
        ),
    ),
    (
        "damage_transition_strain",
        "%",
        re.compile(
            rf"(?is)\bdamage\s+index\b.{{0,260}}?"
            rf"\b(?:decreas\w*|chang\w*|transition\w*)\b.{{0,140}}?"
            rf"\b(?:the\s+)?strain\s+(?:exceeds?|reaches?|passes?|above|beyond)\s*"
            rf"(?P<value>{_TRANSITION_VALUE_PATTERN})\s*%"
        ),
    ),
    (
        "stiffness_recovery_strain",
        "%",
        re.compile(
            rf"(?is)\b(?:beyond|above|after)\s+"
            rf"(?P<value>{_TRANSITION_VALUE_PATTERN})\s*%\s*"
            rf"(?:of\s+)?(?:applied\s+)?strain\b.{{0,180}}?"
            rf"\bstiffness\s+recover\w*\b"
        ),
    ),
    (
        "stiffness_recovery_strain",
        "%",
        re.compile(
            rf"(?is)\bstiffness\s+recover\w*\b.{{0,180}}?"
            rf"\b(?:at|beyond|above|after)\s+"
            rf"(?P<value>{_TRANSITION_VALUE_PATTERN})\s*%\s*"
            rf"(?:of\s+)?(?:applied\s+)?strain\b"
        ),
    ),
    (
        "compressive_displacement",
        "mm",
        re.compile(
            rf"(?is)\b(?:stiff|compliant)\w*\b.{{0,100}}?\b(?:up\s+to|at|around|near)\b"
            rf".{{0,35}}?\bdisplacement\s*(?:of|=)?\s*"
            rf"(?:about|approximately|roughly|ca\.?|[≈~])?\s*"
            rf"(?P<value>{_TRANSITION_VALUE_PATTERN})\s*mm\b"
        ),
    ),
)
_TRANSITION_METRICS = frozenset({
    "knee_strain",
    "damage_transition_strain",
    "stiffness_recovery_strain",
})


def _transition_value_key(value: Any) -> tuple[str, ...]:
    return tuple(
        f"{float(number):g}"
        for number in re.findall(r"\d+(?:\.\d+)?", str(value or ""))
    )


def find_explicit_transition_matches(evidence: str) -> list[dict[str, Any]]:
    """Return strictly worded material-transition measurements from source text."""
    matches: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...], int]] = set()
    for metric, unit, pattern in _EXPLICIT_TRANSITION_PATTERNS:
        for match in pattern.finditer(evidence or ""):
            value = re.sub(r"\s*[-–]\s*", "-", match.group("value").strip())
            key = (metric, _transition_value_key(value), match.start())
            if key in seen:
                continue
            seen.add(key)
            matches.append({
                "metric": metric,
                "value": value,
                "unit": unit,
                "start": match.start(),
                "end": match.end(),
            })
    return matches


def transition_fact_supported(fact: dict) -> bool:
    """Require the stated transition phenomenon to bind directly to the value."""
    metric = find_metric_canonical(str(fact.get("metric_or_parameter") or "")) or ""
    if metric not in _TRANSITION_METRICS:
        return True
    value_key = _transition_value_key(fact.get("value"))
    return any(
        match["metric"] == metric
        and _transition_value_key(match["value"]) == value_key
        for match in find_explicit_transition_matches(
            str(fact.get("evidence_text") or "")
        )
    )


def _append_reason(existing: Any, suffix: str) -> str:
    text = str(existing or "").strip()
    if suffix in text:
        return text
    return f"{text}; {suffix}".strip("; ") if text else suffix


def refine_sample_name_before_paren(before: str) -> str:
    """Extract the nearest explicit sample ID before '('."""
    text = before.rstrip()
    if not text:
        return ""
    segment = re.split(r"(?<=[,;])\s*|\s+\band\s+", text, flags=re.I)[-1].strip()
    for splitter in (r"\bthan\b", r"\bvs\.?\b", r"\bversus\b", r"\bcompared to\b"):
        if re.search(splitter, segment, flags=re.I):
            segment = re.split(splitter, segment, flags=re.I)[-1].strip()
    match = _SAMPLE_ID_TAIL_RE.search(segment)
    if match:
        sid = normalize_sample_id(match.group(1))
        window = segment.lower()
        ev_lower = (before + segment).lower()
        if _NANOFIBER_RE.search(window) and not _AEROGEL_RE.search(window):
            if sid.upper().startswith("PI") and "nanofiber" not in sid.lower():
                return "PI nanofiber" if sid.upper() in ("PI", "PI AEROGEL") else f"{sid} nanofiber"
        if _AEROGEL_RE.search(ev_lower) and "aerogel" not in sid.lower():
            if re.search(r"(?i)(?:PI\s*1|PI1|2MZ-AZINE-PI)", sid):
                return f"{sid} aerogel"
        if re.fullmatch(r"(?i)PI\s*1|PI1", sid) and _AEROGEL_RE.search(ev_lower):
            return "PI1 aerogel"
        return sid
    return ""


def infer_metric_from_evidence(
    evidence: str,
    *,
    unit: str = "",
    current_metric: str = "",
) -> str | None:
    """Evidence-first metric inference; overrides wrong LLM labels."""
    from app.services.extractor_v7.sample_value_alignment import parse_metric_value_pairs

    ev = evidence or ""
    lower = ev.lower()
    unit_norm = normalize_unit(unit)
    current = find_metric_canonical(current_metric) or current_metric

    if unit_norm == "ph" and re.search(r"(?i)\bpH\b", ev):
        return "pH"

    if (
        unit_norm in {"mpa", "gpa", "kpa", "pa"}
        and re.search(
            r"(?i)\b(?:threshold\s+(?:load|stress)|inelastic\s+strain\s+threshold|"
            r"knee\s+(?:load|stress))\b",
            ev,
        )
    ):
        return "inelastic_threshold_stress"

    if (
        unit_norm in {"%", "percent"}
        and current in {"surface_roughness", "surface roughness"}
        and re.search(r"(?i)\b(?:applied\s+)?strain\b", ev)
    ):
        if re.search(r"(?i)\bknee\b", ev):
            return "knee_strain"
        if re.search(r"(?i)\bdamage\s+index\b", ev):
            return "damage_transition_strain"
        if re.search(r"(?i)\bstiffness\s+recover(?:y|s|ed|ing)\b", ev):
            return "stiffness_recovery_strain"

    for pattern, metric in (
        (_FIBER_LENGTH_RE, "fiber_length"),
        (_FIBER_DIAMETER_RE, "fiber_diameter"),
    ):
        if pattern.search(ev):
            return metric

    if current in ("surface_roughness", "surface roughness") and not _ROUGHNESS_RE.search(ev):
        if _FIBER_LENGTH_RE.search(ev) or ("fiber" in lower and "length" in lower):
            return "fiber_length"
        if _FIBER_DIAMETER_RE.search(ev) or ("fiber" in lower and "diameter" in lower):
            return "fiber_diameter"
        if unit_norm in ("nm", "µm", "um", "μm") and "fiber" in lower:
            if unit_norm == "nm" or "diameter" in lower:
                return "fiber_diameter"
            if unit_norm in ("µm", "um", "μm") and "length" in lower:
                return "fiber_length"

    metric_pairs = parse_metric_value_pairs(ev)
    if len(metric_pairs) >= 2 and current:
        for metric, val in metric_pairs:
            if current == "dielectric_constant" and metric == "loss_tangent":
                return None
    return None


def bind_metric_to_parsed_value(fact: dict) -> dict:
    """Force dielectric_constant / loss_tangent to match parsed evidence pairs."""
    from app.services.extractor_v7.sample_value_alignment import (
        _numbers_equal,
        parse_metric_value_pairs,
    )

    evidence = str(fact.get("evidence_text") or "")
    value = fact.get("value")
    if not evidence or value is None:
        return fact
    pairs = parse_metric_value_pairs(evidence)
    if len(pairs) < 2:
        return fact

    current = find_metric_canonical(str(fact.get("metric_or_parameter") or "")) or ""
    matched = [(metric, parsed_val) for metric, parsed_val in pairs if _numbers_equal(parsed_val, value)]
    if not matched:
        return fact

    chosen_metric, parsed_val = matched[0]
    if len(matched) > 1 and current:
        for metric, parsed_val in matched:
            if metric == current:
                chosen_metric = metric
                break

    if current != chosen_metric:
        fact["metric_or_parameter"] = chosen_metric
        fact["assignment_reason"] = _append_reason(
            fact.get("assignment_reason"), "metric_value_bound_from_evidence"
        )
    fixed, _ = validate_scientific_notation(parsed_val, evidence)
    fact["value"] = fixed
    return fact


def _extract_test_conditions(evidence: str) -> list[str]:
    conds: list[str] = []
    for m in _CYCLE_RE.finditer(evidence):
        conds.append(f"{m.group(1)} compression cycles")
    for m in _STRAIN_RE.finditer(evidence):
        conds.append(f"{m.group(1)}% strain")
    for m in _TEMP_COND_RE.finditer(evidence):
        conds.append(f"at {m.group(1)} °C")
    # Humidity
    for m in re.finditer(r"(?is)RH\s*[=≈]?\s*(\d+(?:\.\d+)?)\s*%", evidence):
        conds.append(f"RH≈{m.group(1)}%")
    # Sample thickness
    for m in re.finditer(r"(?is)(?:thickness|thick)\s*(?:of|=|:)?\s*(\d+(?:\.\d+)?)\s*(mm|cm|μm|µm|um)", evidence):
        conds.append(f"thickness {m.group(1)} {m.group(2)}")
    # Frequency band
    for m in re.finditer(r"(?is)(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*(GHz|MHz|Hz)", evidence):
        conds.append(f"{m.group(1)}–{m.group(2)} {m.group(3)}")
    for m in re.finditer(r"(?is)\b(X-?band|Ku-?band|Ka-?band|S-?band|C-?band|L-?band)\b", evidence):
        conds.append(m.group(1))
    # Loading rate
    for m in re.finditer(r"(?is)(?:loading\s+rate|crosshead\s+speed|displacement\s+rate)\s*(?:of|=|:)?\s*(\d+(?:\.\d+)?)\s*(mm/min|mm\s*min|N/s)", evidence):
        conds.append(f"loading rate {m.group(1)} {m.group(2)}")
    # pH
    for m in re.finditer(r"(?is)\bpH\s*(?:=|of)?\s*(\d+(?:\.\d+)?)\b", evidence):
        conds.append(f"pH {m.group(1)}")
    # Concentration
    for m in re.finditer(r"(?is)(?:concentration)\s*(?:of|=|:)?\s*(\d+(?:\.\d+)?)\s*(mg/mL|mg/L|mol/L|mM|wt%|wt\.?\s*%)", evidence):
        conds.append(f"concentration {m.group(1)} {m.group(2)}")
    # Voltage
    for m in re.finditer(r"(?is)(?:voltage|bias)\s*(?:of|=|:)?\s*(\d+(?:\.\d+)?)\s*(V|kV|mV)", evidence):
        conds.append(f"voltage {m.group(1)} {m.group(2)}")
    # Time duration
    for m in re.finditer(r"(?is)(?:for|after|during)\s+(\d+(?:\.\d+)?)\s*(h|hr|hours?|min(?:ute)?s?)\b", evidence):
        conds.append(f"{m.group(1)} {m.group(2)}")
    return conds



def fix_condition_as_performance_value(fact: dict) -> dict:
    """Move cycle counts / strain / bare temperature out of performance_value."""
    evidence = str(fact.get("evidence_text") or "")
    metric = find_metric_canonical(str(fact.get("metric_or_parameter") or "")) or ""
    value = str(fact.get("value") or "").strip()
    unit = str(fact.get("unit") or "").strip().lower()

    existing = str(fact.get("condition") or "").strip()
    # Existing conditions are scoped by the extractor to this value. Appending
    # every condition from a multi-result paragraph corrupts that association.
    cond_bits = [] if existing else _extract_test_conditions(evidence)

    if re.fullmatch(r"\d+", value) and ("cycle" in unit or "cycles" in evidence.lower()):
        if metric in ("cyclic_compression_stability", "compression_stability"):
            from_to = _COMPRESSIVE_FROM_TO_RE.search(evidence)
            if from_to:
                v1, v2 = from_to.group(1), from_to.group(2)
                try:
                    ratio = 100.0 * float(v2) / float(v1)
                    fact["value"] = f"stress decreased from {v1} to {v2} ({ratio:.1f}% retention)"
                except ValueError:
                    fact["value"] = f"stress decreased from {v1} to {v2}"
            elif re.search(r"(?is)no\s+(?:obvious\s+)?decay|no\s+stress\s+decay|stable", evidence):
                fact["value"] = "no stress decay"
            else:
                fact["value"] = "stress retention after cycling"
            fact["unit"] = ""
            cond_bits.append(f"{value} cycles")
            fact["assignment_reason"] = _append_reason(
                fact.get("assignment_reason"), "condition_not_performance_value"
            )

    if unit in ("°c", "c", "degree") and metric in (
        "surface_temperature",
        "glass_transition_temperature",
        "Tg",
    ):
        pass  # legitimate temperature performance
    elif unit in ("°c", "c") and metric not in ("surface_temperature",):
        cond_bits.append(f"{value} °C")
        fact["assignment_reason"] = _append_reason(
            fact.get("assignment_reason"), "temperature_moved_to_condition"
        )

    if cond_bits:
        merged = existing
        for bit in cond_bits:
            if bit.lower() not in merged.lower():
                merged = f"{merged}; {bit}".strip("; ") if merged else bit
        fact["condition"] = merged
    return fact


def enforce_sample_value_from_parens(fact: dict) -> dict:
    """Reassign sample_id using nearest-neighbor parenthesis binding."""
    from app.services.extractor_v7.sample_value_alignment import _numbers_equal

    evidence = str(fact.get("evidence_text") or "")
    value = fact.get("value")
    if not evidence or value is None:
        return fact

    pairs = []
    for match in re.finditer(r"\(([^)]+)\)", evidence):
        inner = match.group(1)
        num = re.search(r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", inner)
        if not num:
            continue
        parsed_val = num.group(1)
        name = refine_sample_name_before_paren(evidence[: match.start()])
        if name and _numbers_equal(parsed_val, value):
            pairs.append((name, parsed_val))

    if len(pairs) != 1:
        return fact

    sid, _ = pairs[0]
    current = str(fact.get("assigned_sample_id") or "").strip()
    if current and normalize_for_match(current) == normalize_for_match(sid):
        return fact
    fact["assigned_sample_id"] = sid
    fact["candidate_sample_ids"] = [sid]
    fact["assignment_status"] = "assigned"
    fact["assignment_confidence"] = max(float(fact.get("assignment_confidence") or 0), 0.9)
    fact["assignment_reason"] = _append_reason(
        fact.get("assignment_reason"), "paren_nearest_neighbor_sample"
    )
    return fact


def enforce_pi1_aerogel_not_generic(fact: dict) -> dict:
    evidence = str(fact.get("evidence_text") or "")
    if not _PI1_RE.search(evidence):
        return fact
    sid = str(fact.get("assigned_sample_id") or "").strip()
    norm = normalize_for_match(sid)
    if norm in ("pi", "piaerogel") or re.fullmatch(r"pi(?!\d)[a-z]*", norm):
        fact["assigned_sample_id"] = "PI1 aerogel"
        fact["candidate_sample_ids"] = ["PI1 aerogel"]
        fact["assignment_reason"] = _append_reason(
            fact.get("assignment_reason"), "pi1_not_generic_pi"
        )
    return fact


def build_alignment_rows(evidence: str, default_metric: str = "") -> list[dict[str, str]]:
    """Build sample-metric-value rows when ≥2 samples and ≥2 values in one sentence."""
    from app.services.extractor_v7.sample_value_alignment import (
        _numbers_equal,
        parse_metric_value_pairs,
        parse_sample_value_pairs,
    )

    sample_pairs = parse_sample_value_pairs(evidence)
    if len({normalize_for_match(s) for s, _ in sample_pairs}) < 2:
        # also try refined paren parser
        refined: list[tuple[str, str]] = []
        for match in re.finditer(r"\(([^)]+)\)", evidence):
            inner = match.group(1)
            num = re.search(r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", inner)
            if not num:
                continue
            name = refine_sample_name_before_paren(evidence[: match.start()])
            if name:
                refined.append((name, num.group(1)))
        sample_pairs = refined or sample_pairs
    if len({normalize_for_match(s) for s, _ in sample_pairs}) < 2:
        return []

    values = { _normalize_key(v) for _, v in sample_pairs }
    if len(values) < 2:
        return []

    metric_pairs = parse_metric_value_pairs(evidence)
    inferred = infer_metric_from_evidence(
        evidence, unit="", current_metric=default_metric
    ) or default_metric or "performance"

    rows: list[dict[str, str]] = []
    for sid, val in sample_pairs:
        metric = inferred
        for mp, mv in metric_pairs:
            if _numbers_equal(mv, val):
                metric = mp
                break
        rows.append({"sample_id": sid, "metric": metric, "value": val})
    return rows


def _normalize_key(v: str) -> str:
    return str(v).strip().replace(",", "")


_ORDERED_SAMPLE_VALUE_RE = re.compile(
    r"(?is)"
    r"(?:(?:thermal\s+conductivit(?:y|ies)|conductivit(?:y|ies))\s+of\s+)?"
    r"(?P<samples>(?:[A-Za-z0-9][A-Za-z0-9\s/\-_.+%]*?\s*(?:,\s*|\s+and\s+))+"
    r"[A-Za-z0-9][A-Za-z0-9\s/\-_.+%]*?)"
    r"\s+(?:were|was|is|are|reached|achieved|measured\s+as)\s+"
    r"(?P<values>(?:[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\s*(?:,\s*|\s+and\s+))+"
    r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
)


def _split_list_items(text: str) -> list[str]:
    parts = re.split(r"\s*,\s*|\s+and\s+", text.strip(), flags=re.I)
    return [p.strip(" ,;.") for p in parts if p.strip(" ,;.")]


def _normalize_list_sample(sample: str, evidence: str) -> str:
    text = sample.strip().rstrip("s")  # aerogels → aerogel fragment
    match = re.search(
        r"(?i)\b(2MZ-AZINE-PI-\d+%|2MZ-AZINE-PI\d+|PI\d+|PI)\b(?:\s+aerogel)?",
        text,
    )
    if not match:
        return normalize_sample_id(text)
    sid = normalize_sample_id(match.group(0))
    if _AEROGEL_RE.search(text):
        if "aerogel" not in sid.lower():
            sid = f"{sid} aerogel"
    return sid


def parse_ordered_sample_value_list(evidence: str) -> list[tuple[str, str]]:
    """Align comma/and-separated sample names with values in list order."""
    match = _ORDERED_SAMPLE_VALUE_RE.search(evidence)
    if not match:
        return []
    samples = _split_list_items(match.group("samples"))
    values = _split_list_items(match.group("values"))
    if len(samples) < 2 or len(values) < 2 or len(samples) != len(values):
        return []
    rows: list[tuple[str, str]] = []
    for sample, value in zip(samples, values):
        sid = _normalize_list_sample(sample, evidence)
        rows.append((sid, value))
    return rows


def _fact_needs_sample_alignment_table(fact: dict, evidence: str) -> bool:
    """Only rebuild rows when sample-value binding is missing or wrong."""
    from app.services.extractor_v7.sample_value_alignment import (
        _numbers_equal,
        _value_linked_to_sample,
        parse_sample_value_pairs,
    )

    pairs = parse_sample_value_pairs(evidence)
    ordered = parse_ordered_sample_value_list(evidence)
    if len(pairs) < 2 and len(ordered) < 2:
        return False

    sample_id = str(fact.get("assigned_sample_id") or "").strip()
    value = fact.get("value")
    if not sample_id or value is None:
        return True

    if ordered:
        for sid, val in ordered:
            if _numbers_equal(val, value):
                return normalize_for_match(sid) != normalize_for_match(sample_id)
        return True

    if pairs and not _value_linked_to_sample(evidence, sample_id, value):
        return True
    return bool(fact.get("_alignment_review_required"))


def enrich_fiber_sample_id(fact: dict) -> dict:
    """Attach nanofiber context when fiber morphology metrics lack it."""
    metric = find_metric_canonical(str(fact.get("metric_or_parameter") or "")) or ""
    if metric not in ("fiber_length", "fiber_diameter"):
        return fact
    evidence = str(fact.get("evidence_text") or "")
    if not _NANOFIBER_RE.search(evidence):
        return fact
    sid = str(fact.get("assigned_sample_id") or "").strip()
    if not sid or "nanofiber" in sid.lower():
        return fact
    if sid.upper() in ("PI", "PI AEROGEL"):
        enriched = "PI nanofiber"
    elif re.search(r"(?i)2MZ-AZINE-PI", sid):
        enriched = f"{sid} nanofibers" if not sid.lower().endswith("s") else sid
    else:
        enriched = f"{sid} nanofiber"
    fact["assigned_sample_id"] = enriched
    fact["candidate_sample_ids"] = [enriched]
    fact["assignment_reason"] = _append_reason(
        fact.get("assignment_reason"), "fiber_sample_enriched"
    )
    return fact


def enforce_imidization_sample(fact: dict) -> dict:
    """Bind imidization facts to explicit -XX% aerogel names when present in evidence."""
    metric = find_metric_canonical(str(fact.get("metric_or_parameter") or "")) or ""
    if metric != "imidization_degree":
        return fact
    evidence = str(fact.get("evidence_text") or "")
    pct = re.search(r"(?i)(2MZ-AZINE-PI[-\s]?(\d+(?:\.\d+)?)\s*%)", evidence)
    if not pct:
        return fact
    sid = normalize_sample_id(pct.group(1))
    if _AEROGEL_RE.search(evidence) and "aerogel" not in sid.lower():
        sid = f"{sid} aerogel"
    fact["assigned_sample_id"] = sid
    fact["candidate_sample_ids"] = [sid]
    fact["assignment_reason"] = _append_reason(
        fact.get("assignment_reason"), "imidization_sample_from_evidence"
    )
    return fact


def expand_multi_sample_alignment_table(facts: list[dict]) -> list[dict]:
    """Replace ambiguous multi-sample facts with aligned rows from evidence table."""
    from collections import defaultdict

    from app.services.extractor_v7.sample_value_alignment import _append_reason, _next_fact_id

    by_evidence: dict[str, list[dict]] = defaultdict(list)
    passthrough: list[dict] = []
    for fact in facts:
        if fact.get("fact_type") != "performance":
            passthrough.append(fact)
            continue
        by_evidence[str(fact.get("evidence_text") or "")].append(fact)

    expanded: list[dict] = list(passthrough)
    id_counter = max(
        (int(re.sub(r"\D", "", f.get("fact_id", "")) or "0") for f in facts),
        default=0,
    ) + 1

    for evidence, group in by_evidence.items():
        if len(group) >= 2:
            expanded.extend(group)
            continue

        fact = group[0]
        if not _fact_needs_sample_alignment_table(fact, evidence):
            expanded.append(fact)
            continue

        rows_dict = build_alignment_rows(
            evidence,
            default_metric=str(fact.get("metric_or_parameter") or ""),
        )
        if len(rows_dict) < 2:
            ordered = parse_ordered_sample_value_list(evidence)
            if len(ordered) >= 2:
                default_metric = find_metric_canonical(
                    str(fact.get("metric_or_parameter") or "")
                ) or str(fact.get("metric_or_parameter") or "")
                rows_dict = [
                    {"sample_id": sid, "metric": default_metric, "value": val}
                    for sid, val in ordered
                ]
        if len(rows_dict) < 2:
            expanded.append(fact)
            continue

        for row in rows_dict:
            clone = copy.deepcopy(fact)
            clone["assigned_sample_id"] = row["sample_id"]
            clone["candidate_sample_ids"] = [row["sample_id"]]
            clone["metric_or_parameter"] = row["metric"]
            fixed, _ = validate_scientific_notation(row["value"], evidence)
            clone["value"] = fixed
            clone["assignment_status"] = "assigned"
            clone["assignment_confidence"] = max(float(clone.get("assignment_confidence") or 0), 0.9)
            clone["assignment_reason"] = _append_reason(
                clone.get("assignment_reason"), "multi_sample_alignment_table"
            )
            new_id, id_counter = _next_fact_id(expanded + facts, id_counter)
            clone["fact_id"] = new_id
            expanded.append(clone)
    return expanded


def apply_hard_validation(facts: list[dict]) -> list[dict]:
    """Run all hard post-processing rules on performance facts."""
    facts = expand_multi_sample_alignment_table(facts)
    for i, fact in enumerate(facts):
        if fact.get("fact_type") != "performance":
            continue
        fact = bind_metric_to_parsed_value(fact)
        inferred = infer_metric_from_evidence(
            str(fact.get("evidence_text") or ""),
            unit=str(fact.get("unit") or ""),
            current_metric=str(fact.get("metric_or_parameter") or ""),
        )
        if inferred:
            fact["metric_or_parameter"] = inferred
            fact["assignment_reason"] = _append_reason(
                fact.get("assignment_reason"), "metric_inferred_from_evidence"
            )
        if not transition_fact_supported(fact):
            fact["_hard_reject"] = True
            fact["_hard_reject_reason"] = "transition_value_not_bound_to_phenomenon"
            fact["assignment_reason"] = _append_reason(
                fact.get("assignment_reason"),
                "transition_value_not_bound_to_phenomenon",
            )
            facts[i] = fact
            continue
        fact = enforce_sample_value_from_parens(fact)
        fact = enforce_pi1_aerogel_not_generic(fact)
        fact = enrich_fiber_sample_id(fact)
        fact = enforce_imidization_sample(fact)
        if fact.get("extraction_method") not in {
            "AI_holistic_table", "rule_table_performance",
        }:
            fact = fix_condition_as_performance_value(fact)
        facts[i] = fact
    return facts
