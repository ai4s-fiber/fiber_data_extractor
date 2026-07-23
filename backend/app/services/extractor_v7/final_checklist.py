"""Final pre-output checklist: 12-item systematic quality gate.

Every fact must pass all checks before entering clean_core_records.
Facts that fail any check get ``_checklist_failed = True`` and a list
of failure reasons in ``_checklist_failures``.

This module is called after all other post-processing steps, right
before record generation.
"""

from __future__ import annotations

import re

from app.services.grouping import normalize_for_match
from app.services.metrics_dictionary import find_metric_canonical
from app.services.validation import (
    is_characterization_peak_metric,
    metric_unit_compatible,
)
from app.services.extractor_v7.validators import is_background_or_reference_fact

_NUMBER_RE = re.compile(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
_PI_GENERIC_RE = re.compile(r"^PI$", re.I)
_CONDITION_IN_SID_RE = re.compile(
    r"(?:\d+(?:\.\d+)?\s*°?\s*[Cc](?:\s|$))|"
    r"(?:\d+\s*min)|"
    r"(?:\d+\s*%\s*strain)|"
    r"(?:[Xx]-?\s*band)|"
    r"(?:\d+\s*[-–]\s*\d+\s*[Gg][Hh]z)|"
    r"(?:RH\s*[=≈]?\s*\d+\s*%?)"
)


def _append_reason(existing: list[str], reason: str) -> list[str]:
    if reason not in existing:
        existing.append(reason)
    return existing


def _identity_appears_in_evidence(identity: str, evidence: str) -> bool:
    """Match a sample identity across common separator variants.

    Material IDs often use ``/``, ``_`` and ``-`` interchangeably.  Keep the
    match boundary-aware so a short component or a shorter composition chain
    cannot match inside a different sample ID.
    """
    identity_norm = normalize_for_match(identity)
    evidence_norm = normalize_for_match(evidence)
    parts = [part for part in re.split(r"[\s_/-]+", identity_norm) if part]
    if not parts or not evidence_norm:
        return False
    pattern = (
        r"(?<![a-z0-9_/-])"
        + r"[\s_/-]+".join(re.escape(part) for part in parts)
        + r"(?![a-z0-9]|\s*[_/-]\s*[a-z0-9])"
    )
    return re.search(pattern, evidence_norm) is not None


def _loosely_spaced_identity_appears(identity: str, evidence: str) -> bool:
    """Match MinerU/LaTeX-spaced composition IDs without prefix collisions."""
    chars = re.findall(r"[a-z0-9]", identity.lower())
    if len(chars) < 6:
        return False
    separator = r"[\s_{}^./\\-]*"
    pattern = (
        r"(?<![a-z0-9])"
        + separator.join(re.escape(char) for char in chars)
        + r"(?![a-z0-9]|\s*[_/-]\s*[a-z0-9])"
    )
    return re.search(pattern, evidence, re.I) is not None


def _composition_loading_identity_appears(identity: str, evidence: str) -> bool:
    """Match canonical loading IDs against source forms such as PES_0.5-CF/EP."""
    loading = re.search(
        r"(?i)(?<![\d.])(\d+(?:\.\d+)?)\s*"
        r"(?:wt(?:%|[a-z]+)|vol%|mol%|%)",
        identity,
    )
    if not loading:
        return False
    stripped = identity[:loading.start()] + " " + identity[loading.end():]
    family_tokens = {
        token
        for token in re.findall(r"[a-z][a-z0-9]*", stripped.lower())
        if token not in {
            "based", "composite", "control", "fiber", "fibers", "fibre",
            "fibres", "film", "laminate", "material", "membrane",
            "nanofiber", "nanofibers", "sample", "specimen",
        }
    }
    if not family_tokens:
        return False
    for value_match in re.finditer(
        rf"(?<![\d.]){re.escape(loading.group(1))}(?![\d.])",
        evidence,
        re.IGNORECASE,
    ):
        window = evidence[
            max(0, value_match.start() - 80): value_match.end() + 80
        ]
        window_tokens = set(re.findall(r"[a-z][a-z0-9]*", window.lower()))
        if family_tokens <= window_tokens:
            return True
    return False


def _check_sample_id_in_evidence(fact: dict) -> str | None:
    """Check 1: sample_id must appear in evidence_text."""
    sid = str(fact.get("assigned_sample_id") or "").strip()
    evidence = str(fact.get("evidence_text") or "")
    if not sid or not evidence:
        return None  # No sample to check
    aliases = fact.get("_sample_aliases") or []
    if isinstance(aliases, str):
        try:
            import json

            parsed_aliases = json.loads(aliases)
            aliases = parsed_aliases if isinstance(parsed_aliases, list) else []
        except (json.JSONDecodeError, TypeError):
            aliases = [aliases]
    if any(
        _identity_appears_in_evidence(str(alias), evidence)
        for alias in aliases
        if normalize_for_match(str(alias))
    ):
        return None
    sid_fractions = list(re.finditer(
        r"(?i)(?<![\d.])(\d+(?:\.\d+)?)\s*(?:(?:wt|vol|mol)\s*%?|%)",
        sid,
    ))
    sid_fraction = sid_fractions[-1] if sid_fractions else None
    if sid_fraction and re.search(
        rf"(?i)(?<![\d.]){re.escape(sid_fraction.group(1))}(?![\d.])\s*%"
        r".{0,50}\b(?:fib(?:er|re)|filler|reinforcement|loading|fraction|content)\b|"
        r"\b(?:fib(?:er|re)|filler|reinforcement|loading|fraction|content)\b"
        rf".{{0,50}}(?<![\d.]){re.escape(sid_fraction.group(1))}(?![\d.])\s*%",
        evidence,
    ):
        return None
    sid_display = re.sub(r"[_/-]+", " ", sid)
    ev_norm = normalize_for_match(evidence)
    if _identity_appears_in_evidence(sid, evidence):
        return None
    if _loosely_spaced_identity_appears(sid, evidence):
        return None
    if _composition_loading_identity_appears(sid, evidence):
        return None
    control_base = re.sub(
        r"(?i)[\s_/-]+(?:control|reference|baseline)\s*$",
        "",
        sid,
    ).strip()
    if control_base != sid and _identity_appears_in_evidence(control_base, evidence):
        return None
    # Try base name without form suffix
    base = re.sub(r"\s+(aerogel|nanofiber|nanofibers|film|membrane|foam|coating|powder|hydrogel|fiber|composite|laminate)s?$", "", sid_display, flags=re.I).strip()
    if base:
        base_norm = normalize_for_match(base)
        if _identity_appears_in_evidence(base, evidence):
            return None
        base_tokens = base_norm.split()
        if len(base_tokens) >= 2:
            modifier = r"\s+".join(re.escape(token) for token in base_tokens[:-1])
            head = re.escape(base_tokens[-1])
            if re.search(
                rf"(?<![a-z0-9]){modifier}\s+(?:and|or)\s+"
                rf"(?:[a-z0-9]+\s+){{0,3}}{head}(?![a-z0-9])",
                ev_norm,
            ):
                return None
    sid_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", sid_display.lower())
        if len(token) >= 3 and token not in {
            "based", "bioepoxy", "composite", "fiber", "fibers", "fibre",
            "fibres", "laminate", "material", "nanofiber", "nanofibers",
            "sample", "specimen",
        }
    }
    evidence_tokens = set(re.findall(r"[a-z0-9]+", evidence.lower()))
    shared_material_tokens = sid_tokens & evidence_tokens
    matrix_material_tokens = sid_tokens - {"matrix"}
    if (
        re.fullmatch(r"(?i)[a-z0-9]+[\s_-]+matrix", sid_display)
        and matrix_material_tokens
        and matrix_material_tokens <= evidence_tokens
    ):
        return None
    sid_is_composite = bool(re.search(r"(?i)\b(?:composite|laminate)\b", sid_display))
    evidence_is_composite = bool(re.search(
        r"(?i)\b(?:composites?|laminates?|"
        r"fibers?[- ](?:reinforced|based)|fibres?[- ](?:reinforced|based))\b",
        evidence,
    ))
    sid_is_fiber = bool(re.search(r"(?i)\b(?:nano)?fib(?:er|re)s?\b", sid_display))
    evidence_is_fiber = bool(re.search(r"(?i)\b(?:nano)?fib(?:er|re)s?\b", evidence))
    if shared_material_tokens and (
        (sid_is_composite and evidence_is_composite)
        or (sid_is_fiber and evidence_is_fiber)
    ):
        return None
    if shared_material_tokens and re.search(r"(?i)\bmatrix\b", sid_display) and re.search(
        r"(?i)\b(?:matrix|material)\b", evidence,
    ):
        return None
    needle_variant = re.search(r"(?i)\b(\d+)\s*[- ]?needles?\b", sid_display)
    if needle_variant and re.search(
        rf"(?i)\b{re.escape(needle_variant.group(1))}\s*[- ]?needles?\b",
        evidence,
    ):
        material_context = " ".join([
            evidence,
            str(fact.get("condition") or ""),
        ])
        form_match = re.search(
            r"(?i)\b(nanofib(?:er|re)|fib(?:er|re)|filament|yarn|fabric|mat|"
            r"film|membrane|composite|aerogel|hydrogel|foam)s?\b",
            sid_display,
        )
        if form_match and re.search(
            rf"(?i)\b{re.escape(form_match.group(1))}s?\b",
            material_context,
        ):
            return None
    if fact.get("extraction_method") in {
        "AI_holistic_table", "rule_table_performance",
    }:
        grounded_row = fact.get("_source_table_row")
        grounded_column = fact.get("_source_table_column")
        axis_match = re.search(
            r"(?i)(?:^|;\s*)axis\s*=\s*([^;]+)",
            str(fact.get("condition") or ""),
        )
        if (
            fact.get("extraction_method") == "rule_table_performance"
            and grounded_row is not None
            and grounded_column is not None
            and axis_match
        ):
            axis = axis_match.group(1).strip()
            base = re.sub(
                rf"(?i)[\s_/-]+{re.escape(axis)}\s*$",
                "",
                sid,
            ).strip()
            if (
                base
                and _identity_appears_in_evidence(base, evidence)
                and _identity_appears_in_evidence(axis, evidence)
            ):
                return None
        has_sample_column = bool(
            re.search(r"(?i)\[columns\].*\b(?:sample|specimen)\b", evidence)
        )
        grounded_row_text = ""
        if grounded_row is not None:
            row_match = re.search(
                rf"(?im)^\[row\s+{re.escape(str(grounded_row))}\]\s*(.*)$",
                evidence,
            )
            grounded_row_text = row_match.group(1) if row_match else ""
        if (
            grounded_row is not None
            and grounded_column is not None
            and has_sample_column
            and re.match(r"(?i)\s*(?:mean|average|avg\.?)\b", grounded_row_text)
        ):
            return None
        run_match = re.search(
            r"(?i)(?:\b(?:sample|specimen|run|no\.?)\s*[-#:]?\s*|"
            r"[\s_/-]+(?:s(?:ample)?)?)(\d+(?:\.\d+)?)\s*$",
            sid_display,
        )
        if not run_match:
            run_match = re.search(r"(?i)(\d+(?:\.\d+)?)\s*$", sid_display)
        if run_match:
            run_number = run_match.group(1)
            material = sid_display[: run_match.start()].strip(" -_/()")
            material = re.sub(
                r"\s+(?:aerogel|nanofibers?|film|membrane|foam|coating|powder|"
                r"hydrogel|fiber|composite|laminate)s?$",
                "",
                material,
                flags=re.I,
            ).strip()
            material_norm = normalize_for_match(material).replace(" ", "")
            has_material = bool(material_norm and material_norm in ev_norm.replace(" ", ""))
            has_row = bool(re.search(rf"(?i)\[row\s+{re.escape(run_number)}\]", evidence))
            has_sample_column = bool(
                re.search(r"(?i)\[columns\].*\b(?:sample|specimen)\b", evidence)
            )
            bare_run_label = bool(re.fullmatch(
                r"(?i)(?:sample|specimen|run|no\.?)\s*[-#:]?\s*\d+(?:\.\d+)?",
                sid_display,
            ))
            if (
                grounded_row is not None
                and grounded_column is not None
                and str(grounded_row) == run_number
                and has_row
                and has_sample_column
            ):
                return None
            if has_row and has_sample_column and (bare_run_label or has_material):
                return None
    return "sample_id_not_found_in_evidence"


def sample_id_supported_by_evidence(sample_id: str, evidence: str) -> bool:
    """Public evidence predicate shared by deterministic grounding stages."""
    return _check_sample_id_in_evidence({
        "assigned_sample_id": sample_id,
        "evidence_text": evidence,
    }) is None


def _check_no_generic_pi(fact: dict) -> str | None:
    """Check 2: PI1 should not be collapsed to PI."""
    sid = str(fact.get("assigned_sample_id") or "").strip()
    evidence = str(fact.get("evidence_text") or "")
    if not _PI_GENERIC_RE.fullmatch(sid):
        return None
    if re.search(r"\bPI\s*1\b|\bPI1\b", evidence, re.I):
        return "generic_PI_should_be_PI1"
    return None


def _check_no_condition_in_sample_id(fact: dict) -> str | None:
    """Check 3: temperature/frequency/time/strain not in sample_id."""
    sid = str(fact.get("assigned_sample_id") or "").strip()
    if not sid:
        return None
    # Only flag if the full sample_id with condition is NOT explicitly in evidence
    if _CONDITION_IN_SID_RE.search(sid):
        evidence = str(fact.get("evidence_text") or "")
        escaped = re.escape(sid)
        if not re.search(escaped, evidence, re.I):
            return "condition_token_in_sample_id"
    return None


def _check_metric_evidence_consistency(fact: dict) -> str | None:
    """Check 4: performance_metric should match evidence semantics."""
    metric = str(fact.get("metric_or_parameter") or "")
    evidence = str(fact.get("evidence_text") or "")
    if not metric or not evidence:
        return None
    canonical = find_metric_canonical(metric) or metric
    ev_lower = evidence.lower()
    # Specific known mismatches
    if canonical == "crystallinity_Xc" and "imidization" in ev_lower:
        return "metric_should_be_imidization_degree"
    if canonical == "surface_roughness":
        if "fiber length" in ev_lower or "average fiber length" in ev_lower:
            return "metric_should_be_fiber_length"
        if "fiber diameter" in ev_lower or "average fiber diameter" in ev_lower:
            return "metric_should_be_fiber_diameter"
    if canonical == "dielectric_constant" and "loss tangent" in ev_lower:
        if "permittivity" not in ev_lower and "dielectric constant" not in ev_lower:
            return "metric_may_be_loss_tangent_not_dielectric"
    return None


def _check_value_belongs_to_metric(fact: dict) -> str | None:
    """Check 5: value should belong to the stated metric, not a neighbor."""
    from app.services.extractor_v7.sample_value_alignment import (
        _numbers_equal,
        parse_metric_value_pairs,
    )

    evidence = str(fact.get("evidence_text") or "")
    value = fact.get("value")
    metric = find_metric_canonical(str(fact.get("metric_or_parameter") or "")) or ""
    if not evidence or value is None or not metric:
        return None
    pairs = parse_metric_value_pairs(evidence)
    if len(pairs) < 2:
        return None
    matched_metrics = [m for m, v in pairs if _numbers_equal(v, value)]
    if matched_metrics and metric not in matched_metrics:
        return f"value_belongs_to_{matched_metrics[0]}_not_{metric}"
    return None


def _check_unit_metric_match(fact: dict) -> str | None:
    """Check 6: unit must be compatible with metric."""
    metric = str(fact.get("metric_or_parameter") or "")
    unit = str(fact.get("unit") or "")
    if not metric or not unit:
        return None
    canonical = find_metric_canonical(metric) or metric
    if not metric_unit_compatible(canonical, unit):
        return f"unit_{unit}_incompatible_with_{canonical}"
    return None


def _check_condition_not_as_value(fact: dict) -> str | None:
    """Check 7: cycle count / bare temperature should not be performance_value."""
    value = str(fact.get("value") or "").strip()
    unit = str(fact.get("unit") or "").strip().lower()
    metric = find_metric_canonical(str(fact.get("metric_or_parameter") or "")) or ""
    if re.fullmatch(r"\d+", value) and ("cycle" in unit or "cycles" in unit):
        if metric in ("cyclic_compression_stability", "compression_stability"):
            return "cycle_count_as_performance_value"
    if unit in ("°c", "c") and metric not in (
        "surface_temperature", "glass_transition_temperature",
        "decomposition_temperature", "onset_decomposition_temperature",
        "austenite_start_temperature", "austenite_finish_temperature",
        "martensite_start_temperature", "martensite_finish_temperature",
        "Tg", "Tm", "Td5", "melting_point", "melting_temperature",
    ):
        return "bare_temperature_as_performance_value"
    return None


def _check_sample_form_consistency(fact: dict) -> str | None:
    """Check 8: nanofiber/film/aerogel/membrane should not be mixed."""
    from app.services.extractor_v7.quality_enhancement import (
        infer_sample_form,
        metric_conflicts_sample_form,
    )

    sid = str(fact.get("assigned_sample_id") or "")
    evidence = str(fact.get("evidence_text") or "")
    metric = str(fact.get("metric_or_parameter") or "")
    form = infer_sample_form(sid, evidence)
    if form and metric_conflicts_sample_form(metric, form):
        return f"metric_{metric}_conflicts_with_{form}_sample"
    return None


def _check_no_characterization_in_core(fact: dict) -> str | None:
    """Check 9: FTIR/XPS/XRD/Raman peaks should not be in core table."""
    metric = str(fact.get("metric_or_parameter") or "")
    method = str(fact.get("method") or "")
    evidence = str(fact.get("evidence_text") or "")
    if is_characterization_peak_metric(metric, method=method, evidence=evidence):
        return "characterization_peak_in_core_table"
    return None


def _check_no_introduction_data(fact: dict) -> str | None:
    """Check 11: Introduction/background data should not enter core."""
    if is_background_or_reference_fact(fact):
        return "background_reference_data_in_core"
    return None


def _check_paper_direction_conflict(fact: dict) -> str | None:
    """Check 12: metric contradicts paper theme (e.g. EMI SE in transparent paper)."""
    # This is a simplified check; the full check requires theme context
    # We flag only if the fact already has the reject marker
    if fact.get("_reject"):
        return "paper_direction_conflict"
    return None


# Full checklist
_CHECKS = [
    _check_sample_id_in_evidence,        # 1
    _check_no_generic_pi,                # 2
    _check_no_condition_in_sample_id,    # 3
    _check_metric_evidence_consistency,  # 4
    _check_value_belongs_to_metric,      # 5
    _check_unit_metric_match,            # 6
    _check_condition_not_as_value,       # 7
    _check_sample_form_consistency,      # 8
    _check_no_characterization_in_core,  # 9
    # Check 10 (duplicates) is handled by merge_duplicate_facts
    _check_no_introduction_data,         # 11
    _check_paper_direction_conflict,     # 12
]


def run_final_checklist(facts: list[dict]) -> list[dict]:
    """Run the 12-item checklist on every performance fact.

    Facts that fail any check get:
    - ``_checklist_failed = True``
    - ``_checklist_failures = [list of reason strings]``
    - ``_export_tier`` downgraded to "B" if was "A"
    """
    for fact in facts:
        if fact.get("fact_type") != "performance":
            continue

        failures: list[str] = []
        for check_fn in _CHECKS:
            result = check_fn(fact)
            if result:
                failures.append(result)

        if failures:
            fact["_checklist_failed"] = True
            fact["_checklist_failures"] = failures
            # Downgrade tier
            current_tier = fact.get("_export_tier", "A")
            if current_tier == "A":
                fact["_export_tier"] = "B"
            # Append to reviewer comment
            existing = str(fact.get("assignment_reason") or "").strip()
            note = f"checklist_failed: {', '.join(failures)}"
            if note not in existing:
                fact["assignment_reason"] = (
                    f"{existing}; {note}".strip("; ") if existing else note
                )
        else:
            fact["_checklist_failed"] = False

    return facts
