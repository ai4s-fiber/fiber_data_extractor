"""High-precision deterministic recovery for explicit quantitative result ranges."""

from __future__ import annotations

import re
from typing import Any

from app.services.grouping import is_material_sample_id, normalize_sample_id
from app.services.extractor_v7.metric_normalize import canonicalize_metric_name

_NUMBER = r"[+-]?\d+(?:\.\d+)?"
_FREQUENCY_RANGE_RE = re.compile(
    rf"(?is)(?:\bfrom\b|\bbetween\b|\brange\s+(?:of|from)\b)\s*"
    rf"(?P<low>{_NUMBER})\s*(?:to|and|[-–—])\s*(?P<high>{_NUMBER})\s*"
    r"(?P<unit>GHz|MHz|kHz|Hz)\b"
)
_NORMALIZED_RANGE_RE = re.compile(
    rf"(?is)\b(?:corresponding\s+)?normalized\s+frequenc(?:y|ies)\b"
    rf".{{0,100}}?(?:is|are|of|from|range\s+(?:is|of|from))?\s*"
    rf"(?P<low>{_NUMBER})\s*(?:to|and|[-–—])\s*(?P<high>{_NUMBER})"
)
_SAMPLE_LABEL = r"[A-Za-z][A-Za-z0-9/_.+%()\-–—\s]{0,90}?"
_DISPLACEMENT_CONTRAST_RE = re.compile(
    rf"(?is)\bdisplacement(?:\s+deformation)?\s+of\s+(?:the\s+)?"
    rf"(?P<left>{_SAMPLE_LABEL})\s+was\s+(?P<left_value>{_NUMBER})\s*mm\s*,?\s*"
    rf"(?:whereas|while)\s+(?:that\s+of\s+)?(?:the\s+)?"
    rf"(?P<right>{_SAMPLE_LABEL})\s+was\s+(?P<right_value>{_NUMBER})\s*mm\b"
)
_SOFTENING_LOAD_CONTRAST_RE = re.compile(
    rf"(?is)\b(?:stress|load)\s+at\s+which\s+softening\s+occurred\s+in\s+"
    rf"(?:the\s+)?(?P<left>{_SAMPLE_LABEL})\s+was\s+"
    rf"(?P<left_value>{_NUMBER})\s*N\s*,?\s*(?:whereas|while)\s+"
    rf"(?:that\s+in\s+)?(?:the\s+)?(?P<right>{_SAMPLE_LABEL})\s+was\s+"
    rf"(?P<right_value>{_NUMBER})\s*N\b"
)
_ACCELERATION_CONTRAST_RE = re.compile(
    rf"(?is)(?:^|[.!?]\s+)(?:the\s+)?(?P<left>{_SAMPLE_LABEL})\s+had\s+a\s+"
    rf"dimensionless\s+maximum\s+acceleration\s+of\s+"
    rf"(?P<left_value>{_NUMBER})\s*,?\s*(?:whereas|while)\s+(?:the\s+)?"
    rf"(?P<right>{_SAMPLE_LABEL})\s+had\s+a\s+dimensionless\s+maximum\s+"
    rf"acceleration\s+of\s+(?P<right_value>{_NUMBER})"
    rf"(?:\s*,?\s*which\s+is\s+a\s+(?P<direction>decrease|reduction)\s+of\s+"
    rf"(?P<operator>more\s+than|over|approximately|about|≈|~)?\s*"
    rf"(?P<change_value>{_NUMBER})\s*%)?"
)
_LOAD_STABILITY_RE = re.compile(
    rf"(?is)\bload[-\s]+bearing\s+stability\s+of\s+(?:the\s+)?"
    rf"(?P<sample>{_SAMPLE_LABEL})\s+(?:increased|improved)\s+by\s+"
    rf"(?P<value>{_NUMBER})\s*%"
)


def _number_key(value: Any) -> tuple[str, ...]:
    return tuple(
        f"{float(number):g}"
        for number in re.findall(_NUMBER, str(value or ""))
    )


def _evidence_window(text: str, start: int, end: int) -> str:
    sentence_start = max(
        text.rfind(".", 0, start),
        text.rfind("?", 0, start),
        text.rfind("!", 0, start),
    ) + 1
    previous_start = max(
        text.rfind(".", 0, max(0, sentence_start - 1)),
        text.rfind("?", 0, max(0, sentence_start - 1)),
        text.rfind("!", 0, max(0, sentence_start - 1)),
    ) + 1
    stops = [
        position
        for position in (
            text.find(".", end),
            text.find("?", end),
            text.find("!", end),
        )
        if position >= 0
    ]
    evidence_end = min(stops) + 1 if stops else len(text)
    return re.sub(r"\s+", " ", text[previous_start:evidence_end].strip())[:1400]


def _source_location(chunk: dict) -> str:
    page = chunk.get("page_number")
    block_id = chunk.get("source_block_id") or ""
    return f"page {page}, block {block_id}".strip(", ")


def _sample_id(value: str) -> str:
    sample_id = normalize_sample_id(re.sub(r"^(?:the|a|an)\s+", "", value.strip(), flags=re.I))
    return sample_id if is_material_sample_id(sample_id) else ""


def recover_explicit_contrast_result_facts(
    chunks: list[dict],
    existing_facts: list[dict],
) -> list[dict]:
    """Recover strict sample/value pairs from explicit contrast result prose."""
    recovered: list[dict] = []

    def add_or_repair(
        *,
        chunk: dict,
        metric: str,
        value: str,
        unit: str,
        sample_id: str,
        evidence: str,
        condition: str = "",
    ) -> None:
        if not sample_id:
            return
        block_id = str(chunk.get("source_block_id") or "")
        matching = [
            fact
            for fact in existing_facts
            if fact.get("fact_type") == "performance"
            and canonicalize_metric_name(
                str(fact.get("metric_or_parameter") or ""),
                evidence=str(fact.get("evidence_text") or ""),
                unit=str(fact.get("unit") or ""),
            ) == metric
            and _number_key(fact.get("value")) == _number_key(value)
            and str(fact.get("_source_block_id") or fact.get("source_block_id") or "")
            == block_id
        ]
        if matching:
            for fact in matching:
                fact["metric_or_parameter"] = metric
                fact["unit"] = unit
                fact["assigned_sample_id"] = sample_id
                fact["candidate_sample_ids"] = [sample_id]
                fact["assignment_status"] = "assigned"
                fact["assignment_confidence"] = max(
                    float(fact.get("assignment_confidence") or 0), 0.97
                )
                fact["assignment_reason"] = (
                    f"{fact.get('assignment_reason') or ''}; "
                    "explicit_contrast_sample_value_binding"
                ).strip("; ")
                fact["evidence_text"] = evidence
                fact["source_location"] = _source_location(chunk)
                fact["condition"] = condition or fact.get("condition") or ""
                fact["_chunk_section"] = chunk.get("section_name", "")
                fact["_chunk_source_type"] = chunk.get("source_type", "")
                fact["_source_block_id"] = chunk.get("source_block_id")
                fact["_source_page"] = chunk.get("page_number")
                fact["_source_bbox"] = chunk.get("source_bbox")
            return
        recovered.append({
            "fact_id": "",
            "fact_type": "performance",
            "subject_text": sample_id,
            "candidate_sample_ids": [sample_id],
            "metric_or_parameter": metric,
            "value": value,
            "unit": unit,
            "method": "",
            "condition": condition,
            "category": "mechanical",
            "evidence_text": evidence,
            "source_location": _source_location(chunk),
            "extraction_method": "rule_text_contrast",
            "confidence": 0.97,
            "assigned_sample_id": sample_id,
            "assignment_confidence": 0.97,
            "assignment_status": "assigned",
            "assignment_reason": "explicit_contrast_sample_value_binding",
            "_chunk_section": chunk.get("section_name", ""),
            "_chunk_source_type": chunk.get("source_type", ""),
            "_source_block_id": chunk.get("source_block_id"),
            "_source_page": chunk.get("page_number"),
            "_source_bbox": chunk.get("source_bbox"),
        })

    for chunk in chunks:
        if str(chunk.get("section_name") or "").lower() not in {
            "results", "conclusion",
        }:
            continue
        text = str(chunk.get("raw_text") or "")
        if not text:
            continue

        for match in _DISPLACEMENT_CONTRAST_RE.finditer(text):
            evidence = _evidence_window(text, match.start(), match.end())
            load = re.search(
                rf"(?i)applied\s+load\s+was\s+({_NUMBER})\s*N",
                evidence,
            )
            condition = f"applied load {load.group(1)} N" if load else ""
            for side in ("left", "right"):
                add_or_repair(
                    chunk=chunk,
                    metric="compressive_displacement",
                    value=match.group(f"{side}_value"),
                    unit="mm",
                    sample_id=_sample_id(match.group(side)),
                    evidence=evidence,
                    condition=condition,
                )

        for match in _SOFTENING_LOAD_CONTRAST_RE.finditer(text):
            evidence = _evidence_window(text, match.start(), match.end())
            for side in ("left", "right"):
                add_or_repair(
                    chunk=chunk,
                    metric="softening_load",
                    value=match.group(f"{side}_value"),
                    unit="N",
                    sample_id=_sample_id(match.group(side)),
                    evidence=evidence,
                )

        for match in _ACCELERATION_CONTRAST_RE.finditer(text):
            evidence = _evidence_window(text, match.start(), match.end())
            velocity = re.search(
                rf"(?i)impact\s+velocity\s+was\s+({_NUMBER})\s*m\s*s(?:\^?\s*-?1|⁻¹)",
                evidence,
            )
            condition = f"impact velocity {velocity.group(1)} m/s" if velocity else ""
            left_sample = _sample_id(match.group("left"))
            right_sample = _sample_id(match.group("right"))
            for sample_id, value in (
                (left_sample, match.group("left_value")),
                (right_sample, match.group("right_value")),
            ):
                add_or_repair(
                    chunk=chunk,
                    metric="maximum_acceleration",
                    value=value,
                    unit="dimensionless",
                    sample_id=sample_id,
                    evidence=evidence,
                    condition=condition,
                )
            if match.group("change_value"):
                operator = re.sub(r"\s+", " ", str(match.group("operator") or "").strip())
                change_value = " ".join(
                    part for part in (operator, match.group("change_value")) if part
                )
                add_or_repair(
                    chunk=chunk,
                    metric="acceleration_reduction",
                    value=change_value,
                    unit="%",
                    sample_id=right_sample,
                    evidence=evidence,
                    condition=(
                        f"{condition}; compared with {left_sample}".strip("; ")
                    ),
                )

        for match in _LOAD_STABILITY_RE.finditer(text):
            evidence = _evidence_window(text, match.start(), match.end())
            add_or_repair(
                chunk=chunk,
                metric="load_bearing_stability_improvement",
                value=match.group("value"),
                unit="%",
                sample_id=_sample_id(match.group("sample")),
                evidence=evidence,
            )
    return recovered


def recover_explicit_frequency_range_facts(
    chunks: list[dict],
    existing_facts: list[dict],
) -> list[dict]:
    """Recover only explicitly stated bandgap/transmission frequency ranges."""
    existing_keys: set[tuple[str, tuple[str, ...], str]] = set()
    for fact in existing_facts:
        if fact.get("fact_type") != "performance":
            continue
        metric = canonicalize_metric_name(
            str(fact.get("metric_or_parameter") or ""),
            evidence=str(fact.get("evidence_text") or ""),
            unit=str(fact.get("unit") or ""),
        )
        existing_keys.add((
            metric,
            _number_key(fact.get("value")),
            str(fact.get("_source_block_id") or fact.get("source_block_id") or ""),
        ))

    recovered: list[dict] = []

    def add_fact(
        *,
        chunk: dict,
        metric: str,
        low: str,
        high: str,
        unit: str,
        evidence: str,
    ) -> None:
        block_id = str(chunk.get("source_block_id") or "")
        value = f"{low}-{high}"
        key = (metric, _number_key(value), block_id)
        if key in existing_keys:
            return
        existing_keys.add(key)
        recovered.append({
            "fact_id": "",
            "fact_type": "performance",
            "subject_text": metric,
            "candidate_sample_ids": [],
            "metric_or_parameter": metric,
            "value": value,
            "unit": unit,
            "method": "",
            "condition": "",
            "category": "mechanical",
            "evidence_text": evidence,
            "source_location": _source_location(chunk),
            "extraction_method": "rule_text_range",
            "confidence": 0.96,
            "assigned_sample_id": None,
            "assignment_confidence": None,
            "assignment_status": "unassigned",
            "assignment_reason": "explicit_result_range",
            "_chunk_section": chunk.get("section_name", ""),
            "_chunk_source_type": chunk.get("source_type", ""),
            "_source_block_id": chunk.get("source_block_id"),
            "_source_page": chunk.get("page_number"),
            "_source_bbox": chunk.get("source_bbox"),
        })

    for chunk in chunks:
        if str(chunk.get("section_name") or "").lower() not in {
            "results", "conclusion",
        }:
            continue
        text = str(chunk.get("raw_text") or "")
        if not text:
            continue
        for match in _FREQUENCY_RANGE_RE.finditer(text):
            evidence = _evidence_window(text, match.start(), match.end())
            lower = evidence.lower()
            if "transmission" in lower and re.search(
                r"\b(?:decay|attenuat|valley|reduc)\w*\b", lower,
            ):
                metric = "transmission_attenuation_frequency_range"
            elif re.search(r"\b(?:directional\s+)?band\s*gap\b", lower):
                metric = "bandgap_frequency_range"
            else:
                continue
            add_fact(
                chunk=chunk,
                metric=metric,
                low=match.group("low"),
                high=match.group("high"),
                unit=match.group("unit"),
                evidence=evidence,
            )

        for match in _NORMALIZED_RANGE_RE.finditer(text):
            evidence = _evidence_window(text, match.start(), match.end())
            add_fact(
                chunk=chunk,
                metric="normalized_bandgap_frequency_range",
                low=match.group("low"),
                high=match.group("high"),
                unit="dimensionless",
                evidence=evidence,
            )
    return recovered
