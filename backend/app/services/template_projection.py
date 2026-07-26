"""Sparse, evidence-grounded projection into the chemical-fiber template.

The supplied PDF is a visual export rather than a machine-readable database
schema. This module keeps V7 facts authoritative, exposes stable local paths,
and preserves every unmapped fact until real external field IDs are available.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping


TEMPLATE_PROJECTION_VERSION = "chemical_fiber_projection_v1"
TEMPLATE_NAME = "化学纤维数据模板"
EXTERNAL_SCHEMA_BINDING = "pending_machine_schema"
EXTRACTION_STATUSES = {
    "extracted",
    "verified",
    "not_reported",
    "not_applicable",
    "extraction_pending",
    "needs_review",
}


@dataclass(frozen=True)
class TemplateFieldDefinition:
    field_path: str
    label: str
    section: str
    entity_type: str
    value_type: str = "text"
    repeat_mode: str = "container"
    dynamic: bool = False


# High-confidence overlap between the current extractor and the visual
# template. External field IDs must be bound from the database's real schema.
TEMPLATE_FIELD_DEFINITIONS: tuple[TemplateFieldDefinition, ...] = (
    TemplateFieldDefinition("paper.metadata.title", "文献标题", "文献", "paper"),
    TemplateFieldDefinition("paper.metadata.doi_or_url", "DOI或链接", "文献", "paper"),
    TemplateFieldDefinition("paper.metadata.year", "年份", "文献", "paper", "number"),
    TemplateFieldDefinition("paper.metadata.journal", "期刊", "文献", "paper"),
    TemplateFieldDefinition(
        "fiber_sample.identity.sample_id", "样品编号", "纤维样品", "fiber_sample"
    ),
    TemplateFieldDefinition(
        "fiber_sample.identity.aliases", "样品别名", "纤维样品", "fiber_sample"
    ),
    TemplateFieldDefinition(
        "fiber_sample.identity.group_id", "样品组", "纤维样品", "fiber_sample"
    ),
    TemplateFieldDefinition(
        "fiber_sample.identity.fiber_type", "纤维类型", "纤维样品", "fiber_sample"
    ),
    TemplateFieldDefinition(
        "fiber_sample.composition.material_system", "材料体系", "成分", "fiber_sample"
    ),
    TemplateFieldDefinition(
        "fiber_sample.composition.expression", "组成表达式", "成分", "fiber_sample"
    ),
    TemplateFieldDefinition(
        "fiber_sample.composition.variable_name", "变量名称", "成分", "fiber_sample"
    ),
    TemplateFieldDefinition(
        "fiber_sample.composition.variable_value",
        "变量值",
        "成分",
        "fiber_sample",
        "number_or_text",
    ),
    TemplateFieldDefinition(
        "fiber_sample.composition.variable_unit", "变量单位", "成分", "fiber_sample"
    ),
    TemplateFieldDefinition(
        "fiber_sample.process.route", "工艺路线", "加工", "fiber_sample"
    ),
    TemplateFieldDefinition(
        "fiber_sample.process.spinning_method", "纺丝方法", "加工", "fiber_sample"
    ),
    TemplateFieldDefinition(
        "fiber_sample.process.parameters",
        "工艺参数",
        "加工",
        "fiber_sample",
        "text",
        "row",
    ),
    TemplateFieldDefinition(
        "fiber_sample.process.post_treatment",
        "后处理",
        "加工",
        "fiber_sample",
        "text",
        "row",
    ),
    TemplateFieldDefinition(
        "fiber_sample.structure.methods",
        "结构表征方法",
        "结构",
        "fiber_sample",
        "text",
        "row",
    ),
    TemplateFieldDefinition(
        "fiber_sample.structure.features",
        "结构特征",
        "结构",
        "fiber_sample",
        "text",
        "row",
    ),
    TemplateFieldDefinition(
        "dynamic.composition",
        "聚合物和辅料属性",
        "成分",
        "fiber_sample",
        dynamic=True,
    ),
    TemplateFieldDefinition(
        "dynamic.process",
        "分阶段工艺参数",
        "加工",
        "fiber_sample",
        "number_or_text",
        "row",
        True,
    ),
    TemplateFieldDefinition(
        "dynamic.structure",
        "结构测试结果",
        "结构",
        "fiber_sample",
        "number_or_text",
        "row",
        True,
    ),
    TemplateFieldDefinition(
        "dynamic.performance",
        "性能测试结果",
        "性能",
        "fiber_sample",
        "number_or_text",
        "row",
        True,
    ),
)

_STATIC_DEFINITIONS = {
    item.field_path: item for item in TEMPLATE_FIELD_DEFINITIONS if not item.dynamic
}


# Matching remains conservative. Anything not matched receives a stable
# fallback path and remains visible in unmapped_facts.
_METRIC_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"(?:intrinsic\s+viscosity|特性粘度)", re.I),
        "fiber_sample.composition.polymer.intrinsic_viscosity",
        "number_or_text",
    ),
    (
        re.compile(r"(?:melt\s+flow|熔体流动速率|熔指)", re.I),
        "fiber_sample.composition.polymer.melt_flow_rate",
        "number_or_text",
    ),
    (
        re.compile(r"(?:molecular\s+weight|分子量|pdi|分子量分布)", re.I),
        "fiber_sample.composition.polymer.molecular_weight",
        "number_or_text",
    ),
    (
        re.compile(r"(?:thermal\s+decomp|热分解)", re.I),
        "fiber_sample.composition.polymer.thermal_decomposition",
        "number_or_range",
    ),
    (
        re.compile(r"(?:moisture|含水率|含水量)", re.I),
        "fiber_sample.process.pre_treatment.moisture_content",
        "number_or_text",
    ),
    (
        re.compile(r"(?:dry(?:ing)?|干燥).*(?:temperature|温度)|干燥温度", re.I),
        "fiber_sample.process.pre_treatment.drying_temperature",
        "number_or_range",
    ),
    (
        re.compile(r"(?:extrusion|挤出|螺杆).*(?:temperature|温度)|挤出温度", re.I),
        "fiber_sample.process.melt_extrusion.temperature",
        "number_or_range",
    ),
    (
        re.compile(r"(?:spinning\s+temperature|纺丝温度|箱体温度)", re.I),
        "fiber_sample.process.spinning.temperature",
        "number_or_range",
    ),
    (
        re.compile(r"(?:draw|drawing|牵伸)", re.I),
        "fiber_sample.process.drawing.parameters",
        "number_or_text",
    ),
    (
        re.compile(r"(?:heat\s*set|heat\s*treatment|热定型|热处理)", re.I),
        "fiber_sample.process.heat_setting.parameters",
        "number_or_text",
    ),
    (
        re.compile(r"(?:winding|卷绕)", re.I),
        "fiber_sample.process.winding.parameters",
        "number_or_text",
    ),
    (
        re.compile(r"(?:cooling|冷却)", re.I),
        "fiber_sample.process.cooling.parameters",
        "number_or_text",
    ),
    (
        re.compile(r"(?:oil(?:ing)?|上油|油剂)", re.I),
        "fiber_sample.process.oiling.parameters",
        "number_or_text",
    ),
    (
        re.compile(r"(?:fineness|linear\s+density|纤度)", re.I),
        "fiber_sample.fineness.linear_density",
        "number_or_text",
    ),
    (
        re.compile(r"(?:diameter|纤维直径|直径)", re.I),
        "fiber_sample.fineness.diameter",
        "number_or_text",
    ),
    (
        re.compile(r"(?:crystallinity|结晶度)", re.I),
        "fiber_sample.structure.crystallinity",
        "number_or_text",
    ),
    (
        re.compile(r"(?:orientation|取向度)", re.I),
        "fiber_sample.structure.orientation",
        "number_or_text",
    ),
    (
        re.compile(r"(?:tensile|breaking|断裂.*强度|拉伸.*强度)", re.I),
        "fiber_sample.performance.mechanical.tensile_strength",
        "number_or_range",
    ),
    (
        re.compile(r"(?:elongation|断裂伸长率|伸长率)", re.I),
        "fiber_sample.performance.mechanical.elongation_at_break",
        "number_or_range",
    ),
    (
        re.compile(r"(?:tenacity|断裂强力|比强度)", re.I),
        "fiber_sample.performance.mechanical.tenacity",
        "number_or_range",
    ),
    (
        re.compile(r"(?:modulus|模量)", re.I),
        "fiber_sample.performance.mechanical.modulus",
        "number_or_range",
    ),
    (
        re.compile(r"(?:thermal\s+stability|耐热性)", re.I),
        "fiber_sample.performance.thermal.heat_resistance",
        "number_or_text",
    ),
    (
        re.compile(r"(?:weather|耐候性)", re.I),
        "fiber_sample.performance.other.weather_resistance",
        "number_or_text",
    ),
    (
        re.compile(r"(?:ultraviolet|\buv\b|抗紫外)", re.I),
        "fiber_sample.performance.other.uv_resistance",
        "number_or_text",
    ),
    (
        re.compile(r"(?:antibacterial|抗菌)", re.I),
        "fiber_sample.performance.other.antibacterial",
        "number_or_text",
    ),
    (
        re.compile(r"(?:conductivity|electrical|电学|导电)", re.I),
        "fiber_sample.performance.electrical.conductivity",
        "number_or_text",
    ),
    (
        re.compile(r"(?:biocompatib|生物相容)", re.I),
        "fiber_sample.performance.biological.biocompatibility",
        "number_or_text",
    ),
    (
        re.compile(r"(?:degrad|降解)", re.I),
        "fiber_sample.performance.biological.degradation",
        "number_or_text",
    ),
)

_NUMBER_RE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_RANGE_RE = re.compile(
    rf"^\s*(?P<low>{_NUMBER_RE})\s*(?:-|\u2013|~|至|to)\s*"
    rf"(?P<high>{_NUMBER_RE})(?:\s*[^\d]*)?\s*$",
    re.I,
)
_SCALAR_RE = re.compile(
    rf"^\s*(?P<operator>[<>≤≥]=?)?\s*(?P<number>{_NUMBER_RE})"
    rf"(?:\s*[±+/-]\s*{_NUMBER_RE})?\s*(?:[^\d]*)?\s*$",
    re.I,
)


def _get(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value).strip()


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None


def _parse_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return value


def _stable_slug(value: str) -> str:
    normalized = (
        unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    )
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", normalized).strip("_").lower()
    if slug:
        return slug[:80]
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"metric_{digest}"


def _candidate_sample_ids(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    text = _text(value)
    if not text:
        return []
    parsed = _parse_json(text)
    if isinstance(parsed, list):
        return [_text(item) for item in parsed if _text(item)]
    return [item.strip() for item in re.split(r"[,;|]", text) if item.strip()]


def _entity_key(paper_id: int | str, sample_id: str | None) -> str:
    if sample_id:
        return f"paper:{paper_id}:sample:{sample_id}"
    return f"paper:{paper_id}:unassigned"


def _fact_entity(paper_id: int | str, fact: Any) -> tuple[str, str | None]:
    assigned = _text(_get(fact, "assigned_sample_id"))
    if assigned:
        return _entity_key(paper_id, assigned), assigned
    candidates = _candidate_sample_ids(_get(fact, "candidate_sample_ids"))
    assignment = _text(_get(fact, "assignment_status"))
    if len(candidates) == 1 and assignment not in {"multiple", "uncertain"}:
        return _entity_key(paper_id, candidates[0]), candidates[0]
    return _entity_key(paper_id, None), None


def _metric_text(fact: Any) -> str:
    return " ".join(
        item
        for item in (
            _text(_get(fact, "metric_or_parameter")),
            _text(_get(fact, "performance_metric")),
            _text(_get(fact, "category")),
        )
        if item
    )


def _rule_is_compatible(fact_type: str, field_path: str) -> bool:
    """Prevent cross-section keyword collisions such as spinneret diameter."""
    if fact_type == "process":
        return ".process." in field_path
    if fact_type == "composition":
        return ".composition." in field_path
    if fact_type == "structure":
        return ".structure." in field_path or ".fineness." in field_path
    if fact_type == "performance":
        return ".performance." in field_path
    return True


def resolve_fact_field(fact: Any) -> tuple[str, str, bool]:
    """Return field path, value type and mapping status for one fact."""
    metric = _metric_text(fact)
    fact_type = _text(_get(fact, "fact_type")) or "other"
    for pattern, field_path, value_type in _METRIC_RULES:
        if pattern.search(metric) and _rule_is_compatible(fact_type, field_path):
            return field_path, value_type, True
    section = {
        "composition": "composition",
        "process": "process",
        "structure": "structure",
        "performance": "performance",
    }.get(fact_type, "other")
    return (
        f"fiber_sample.{section}.unmapped.{_stable_slug(metric or fact_type)}",
        "number_or_text",
        False,
    )


def parse_value_shape(raw_value: Any) -> dict[str, Any]:
    """Parse scalar/range metadata while always retaining the raw value."""
    value_text = _text(raw_value)
    result: dict[str, Any] = {
        "value_text": value_text,
        "value_number": None,
        "range_min": None,
        "range_max": None,
        "operator": None,
    }
    if not value_text:
        return result
    range_match = _RANGE_RE.match(value_text)
    if range_match:
        result["range_min"] = float(range_match.group("low"))
        result["range_max"] = float(range_match.group("high"))
        return result
    scalar_match = _SCALAR_RE.match(value_text)
    if scalar_match:
        result["value_number"] = float(scalar_match.group("number"))
        result["operator"] = scalar_match.group("operator") or None
    return result


def _evidence_payload(source: Any, evidence: Any | None = None) -> dict[str, Any]:
    page = _get(source, "source_page")
    if page is None and evidence is not None:
        page = _get(evidence, "page_start")
    payload = {
        "evidence_item_id": _get(source, "evidence_item_id")
        or _get(evidence, "id"),
        "source_block_id": _get(source, "source_block_id")
        or _get(evidence, "block_id"),
        "source_page": page,
        "source_location": _text(
            _get(source, "source_location")
            or _get(evidence, "source_location")
        ),
        "evidence_text": _text(
            _get(source, "evidence_text") or _get(evidence, "evidence_text")
        ),
        "source_type": _text(_get(evidence, "source_type"))
        if evidence is not None
        else "",
        "bbox": _parse_json(
            _get(source, "source_bbox_json") or _get(evidence, "bbox_json")
        ),
    }
    return {
        key: value for key, value in payload.items() if value not in (None, "", [])
    }


def _confidence(source: Any) -> float | None:
    value = _float_or_none(_get(source, "confidence"))
    return value if value is not None else _float_or_none(_get(source, "ai_confidence"))


def _status_for_value(
    source: Any,
    evidence: dict[str, Any],
    *,
    raw_value: Any,
    sample_id: str | None,
    fact_type: str,
) -> str:
    if not _text(raw_value):
        return "needs_review"
    if _text(_get(source, "assignment_status")) in {"uncertain", "multiple"}:
        return "needs_review"
    if fact_type in {"performance", "structure"} and sample_id is None:
        return "needs_review"
    if not evidence.get("evidence_text"):
        return "needs_review"
    confidence = _confidence(source)
    if confidence is not None and confidence < 0.55:
        return "needs_review"
    return "extracted"


def _value_record(
    *,
    field_path: str,
    field_label: str,
    entity_key: str,
    source: Any,
    evidence: dict[str, Any],
    raw_value: Any,
    value_type: str,
    mapped: bool,
    sample_id: str | None,
    fact_type: str,
    source_kind: str,
) -> dict[str, Any]:
    return {
        "field_path": field_path,
        "field_label": field_label,
        "entity_type": "fiber_sample",
        "entity_key": entity_key,
        "sample_id": sample_id,
        "value_type": value_type,
        **parse_value_shape(raw_value),
        "unit": _text(_get(source, "unit") or _get(source, "performance_unit")),
        "raw_value": _text(raw_value),
        "status": _status_for_value(
            source,
            evidence,
            raw_value=raw_value,
            sample_id=sample_id,
            fact_type=fact_type,
        ),
        "confidence": _confidence(source),
        "method": _text(_get(source, "method") or _get(source, "performance_method")),
        "condition": _text(
            _get(source, "condition") or _get(source, "performance_condition")
        ),
        "mapping_status": "mapped" if mapped else "unmapped",
        "source_kind": source_kind,
        "evidence": evidence,
    }


def _append_unique(values: list[dict[str, Any]], value: dict[str, Any]) -> None:
    key = (
        value["field_path"],
        value["entity_key"],
        value["raw_value"],
        value.get("evidence", {}).get("evidence_text", ""),
    )
    for existing in values:
        existing_key = (
            existing["field_path"],
            existing["entity_key"],
            existing["raw_value"],
            existing.get("evidence", {}).get("evidence_text", ""),
        )
        if existing_key == key:
            return
    values.append(value)


def _definition_label(field_path: str) -> str:
    definition = _STATIC_DEFINITIONS.get(field_path)
    return definition.label if definition else ""


_SAMPLE_FIELDS = (
    ("sample_id", "fiber_sample.identity.sample_id", "text"),
    ("sample_aliases", "fiber_sample.identity.aliases", "text"),
    ("sample_group_id", "fiber_sample.identity.group_id", "text"),
    ("fiber_type", "fiber_sample.identity.fiber_type", "text"),
    ("material_system", "fiber_sample.composition.material_system", "text"),
    ("composition_expression", "fiber_sample.composition.expression", "text"),
    ("variable_name", "fiber_sample.composition.variable_name", "text"),
    ("variable_value", "fiber_sample.composition.variable_value", "number_or_text"),
    ("variable_unit", "fiber_sample.composition.variable_unit", "text"),
    ("process_route", "fiber_sample.process.route", "text"),
)

_RECORD_FIELDS = (
    ("spinning_method", "fiber_sample.process.spinning_method", "text", "process"),
    ("process_parameters", "fiber_sample.process.parameters", "text", "process"),
    ("post_treatment", "fiber_sample.process.post_treatment", "text", "process"),
    ("structure_methods", "fiber_sample.structure.methods", "text", "structure"),
    ("structure_features", "fiber_sample.structure.features", "text", "structure"),
    ("matrix_name", "fiber_sample.composition.matrix.name", "text", "composition"),
    (
        "matrix_content",
        "fiber_sample.composition.matrix.content",
        "number_or_text",
        "composition",
    ),
    ("matrix_unit", "fiber_sample.composition.matrix.unit", "text", "composition"),
    (
        "additive_expression",
        "fiber_sample.composition.additives.expression",
        "text",
        "composition",
    ),
    (
        "solvent_or_aid",
        "fiber_sample.composition.solvent_or_aid",
        "text",
        "composition",
    ),
)


def _paper_values(paper: Any) -> list[dict[str, Any]]:
    paper_id = _get(paper, "id")
    entity_key = f"paper:{paper_id}"
    fields = (
        ("paper_title", "paper.metadata.title", "文献标题", "text"),
        ("doi_or_url", "paper.metadata.doi_or_url", "DOI或链接", "text"),
        ("year", "paper.metadata.year", "年份", "number"),
        ("journal", "paper.metadata.journal", "期刊", "text"),
    )
    values: list[dict[str, Any]] = []
    for source_field, field_path, label, value_type in fields:
        raw_value = _get(paper, source_field)
        if not _text(raw_value):
            continue
        values.append(
            {
                "field_path": field_path,
                "field_label": label,
                "entity_type": "paper",
                "entity_key": entity_key,
                "sample_id": None,
                "value_type": value_type,
                **parse_value_shape(raw_value),
                "unit": "",
                "raw_value": _text(raw_value),
                "status": "extracted",
                "confidence": None,
                "method": "paper_metadata",
                "condition": "",
                "mapping_status": "mapped",
                "source_kind": "paper_metadata",
                "evidence": {"source_type": "metadata"},
            }
        )
    return values


def template_schema_payload() -> dict[str, Any]:
    """Return the local projection contract exposed to API clients."""
    return {
        "schema_version": TEMPLATE_PROJECTION_VERSION,
        "template_name": TEMPLATE_NAME,
        "schema_binding": EXTERNAL_SCHEMA_BINDING,
        "external_schema_bound": False,
        "field_definitions": [asdict(item) for item in TEMPLATE_FIELD_DEFINITIONS],
        "missing_statuses": sorted(
            EXTRACTION_STATUSES - {"extracted", "verified"}
        ),
        "rules": {
            "do_not_infer_missing_values": True,
            "preserve_raw_value": True,
            "preserve_evidence": True,
            "reported_field_coverage_only": True,
            "external_field_ids_require_machine_schema": True,
        },
    }


def build_template_projection(
    *,
    paper: Any,
    samples: Iterable[Any] = (),
    facts: Iterable[Any] = (),
    records: Iterable[Any] = (),
    evidence_items: Iterable[Any] = (),
    include_unmapped: bool = True,
) -> dict[str, Any]:
    """Build a sparse projection from persisted extraction results."""
    paper_id = _get(paper, "id")
    sample_list = list(samples)
    fact_list = list(facts)
    record_list = list(records)
    evidence_by_id = {
        _get(item, "id"): item
        for item in evidence_items
        if _get(item, "id") is not None
    }
    values = _paper_values(paper)
    entities: list[dict[str, Any]] = [
        {
            "entity_type": "paper",
            "entity_key": f"paper:{paper_id}",
            "paper_id": paper_id,
        }
    ]
    entity_keys = {f"paper:{paper_id}"}
    unmapped_facts: list[dict[str, Any]] = []
    pending_facts: list[dict[str, Any]] = []

    def ensure_sample_entity(
        sample_id: str | None, source: Any | None = None
    ) -> str:
        key = _entity_key(paper_id, sample_id)
        if key not in entity_keys:
            entity_keys.add(key)
            entities.append(
                {
                    "entity_type": "fiber_sample",
                    "entity_key": key,
                    "paper_id": paper_id,
                    "sample_id": sample_id,
                    "sample_group_id": (
                        _text(_get(source, "sample_group_id")) if source else ""
                    ),
                }
            )
        return key

    for sample in sample_list:
        sample_id = _text(_get(sample, "sample_id")) or None
        entity_key = ensure_sample_entity(sample_id, sample)
        evidence = {
            "source_location": _text(_get(sample, "source_location")),
            "evidence_text": _text(_get(sample, "evidence_text")),
            "source_type": "sample_catalog",
        }
        evidence = {key: value for key, value in evidence.items() if value}
        for source_field, field_path, value_type in _SAMPLE_FIELDS:
            raw_value = _get(sample, source_field)
            if not _text(raw_value):
                continue
            _append_unique(
                values,
                _value_record(
                    field_path=field_path,
                    field_label=_definition_label(field_path),
                    entity_key=entity_key,
                    source=sample,
                    evidence=evidence,
                    raw_value=raw_value,
                    value_type=value_type,
                    mapped=True,
                    sample_id=sample_id,
                    fact_type=(
                        "process" if ".process." in field_path else "composition"
                    ),
                    source_kind="sample_catalog",
                ),
            )

    for fact in fact_list:
        entity_key, sample_id = _fact_entity(paper_id, fact)
        ensure_sample_entity(sample_id, fact)
        evidence_item = evidence_by_id.get(_get(fact, "evidence_item_id"))
        evidence = _evidence_payload(fact, evidence_item)
        field_path, value_type, mapped = resolve_fact_field(fact)
        raw_value = _get(fact, "value")
        if raw_value is None:
            raw_value = _get(fact, "performance_value")
        if not _text(raw_value):
            pending_facts.append(
                {
                    "fact_id": _text(_get(fact, "fact_id")),
                    "fact_type": _text(_get(fact, "fact_type")),
                    "metric_or_parameter": _text(
                        _get(fact, "metric_or_parameter")
                    ),
                    "status": "needs_review",
                    "sample_id": sample_id,
                    "evidence": evidence,
                }
            )
            continue
        _append_unique(
            values,
            _value_record(
                field_path=field_path,
                field_label=_metric_text(fact),
                entity_key=entity_key,
                source=fact,
                evidence=evidence,
                raw_value=raw_value,
                value_type=value_type,
                mapped=mapped,
                sample_id=sample_id,
                fact_type=_text(_get(fact, "fact_type")),
                source_kind="fact_candidate",
            ),
        )
        if not mapped:
            unmapped_facts.append(
                {
                    "fact_id": _text(_get(fact, "fact_id")),
                    "fact_type": _text(_get(fact, "fact_type")),
                    "metric_or_parameter": _text(
                        _get(fact, "metric_or_parameter")
                    ),
                    "value": _text(raw_value),
                    "unit": _text(_get(fact, "unit")),
                    "sample_id": sample_id,
                    "field_path": field_path,
                    "evidence": evidence,
                }
            )

    # CandidateRecord remains a compatibility view. It supplies older fields
    # and is deduplicated against FactCandidate values.
    for record in record_list:
        sample_id = _text(_get(record, "sample_id")) or None
        entity_key = ensure_sample_entity(sample_id, record)
        evidence = {
            "source_location": _text(_get(record, "source_location")),
            "evidence_text": _text(
                _get(record, "performance_evidence")
                or _get(record, "evidence_text")
            ),
            "source_type": "candidate_record",
        }
        evidence = {key: value for key, value in evidence.items() if value}
        for source_field, field_path, value_type, fact_type in _RECORD_FIELDS:
            raw_value = _get(record, source_field)
            if not _text(raw_value):
                continue
            _append_unique(
                values,
                _value_record(
                    field_path=field_path,
                    field_label=_definition_label(field_path) or source_field,
                    entity_key=entity_key,
                    source=record,
                    evidence=evidence,
                    raw_value=raw_value,
                    value_type=value_type,
                    mapped=True,
                    sample_id=sample_id,
                    fact_type=fact_type,
                    source_kind="candidate_record",
                ),
            )

        metric = _text(_get(record, "performance_metric"))
        raw_value = _get(record, "performance_value")
        if metric and _text(raw_value):
            synthetic_fact = {
                "fact_type": "performance",
                "performance_metric": metric,
                "value": raw_value,
                "unit": _get(record, "performance_unit"),
                "method": _get(record, "performance_method"),
                "condition": _get(record, "performance_condition"),
                "confidence": _get(record, "ai_confidence"),
                "assignment_status": "assigned" if sample_id else "unassigned",
            }
            field_path, value_type, mapped = resolve_fact_field(synthetic_fact)
            _append_unique(
                values,
                _value_record(
                    field_path=field_path,
                    field_label=metric,
                    entity_key=entity_key,
                    source=synthetic_fact,
                    evidence=evidence,
                    raw_value=raw_value,
                    value_type=value_type,
                    mapped=mapped,
                    sample_id=sample_id,
                    fact_type="performance",
                    source_kind="candidate_record",
                ),
            )

    status_counts = Counter(value["status"] for value in values)
    mapping_counts = Counter(value["mapping_status"] for value in values)
    evidence_eligible = [
        value for value in values if value["entity_type"] != "paper"
    ]
    evidence_count = sum(
        1
        for value in evidence_eligible
        if value.get("evidence", {}).get("evidence_text")
    )
    quality = {
        "value_count": len(values),
        "reported_field_count": len({value["field_path"] for value in values}),
        "sample_count": sum(
            1 for item in entities if item["entity_type"] == "fiber_sample"
        ),
        "mapped_value_count": mapping_counts.get("mapped", 0),
        "unmapped_value_count": mapping_counts.get("unmapped", 0),
        "unmapped_fact_count": len(unmapped_facts),
        "pending_fact_count": len(pending_facts),
        "evidence_value_count": evidence_count,
        "evidence_coverage": (
            round(evidence_count / len(evidence_eligible), 4)
            if evidence_eligible
            else 0.0
        ),
        "status_counts": dict(sorted(status_counts.items())),
        "coverage_definition": (
            "reported_field_coverage_only; unreported template fields are not "
            "counted as extraction failures"
        ),
    }
    returned_values = (
        values
        if include_unmapped
        else [
            value for value in values if value["mapping_status"] == "mapped"
        ]
    )
    quality["returned_value_count"] = len(returned_values)

    return {
        "schema_version": TEMPLATE_PROJECTION_VERSION,
        "template_name": TEMPLATE_NAME,
        "schema_binding": EXTERNAL_SCHEMA_BINDING,
        "external_schema_bound": False,
        "paper": {
            "paper_id": paper_id,
            "title": _text(_get(paper, "paper_title")),
            "original_filename": _text(_get(paper, "original_filename")),
        },
        "entities": entities,
        "values": returned_values,
        "unmapped_facts": unmapped_facts if include_unmapped else [],
        "pending_facts": pending_facts,
        "quality": quality,
        "rules": {
            "missing_values_are_not_inferred": True,
            "raw_values_are_preserved": True,
            "evidence_is_required_for_extracted_status": True,
            "external_write_enabled": False,
        },
    }
