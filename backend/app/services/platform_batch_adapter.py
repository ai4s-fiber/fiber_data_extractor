"""Deterministic adapter from AI4S projections to platform batch JSON.

The target platform's batch format is not the same as the template-import
format.  A batch document must retain the downloaded ``dataset`` and
``template`` envelopes and replace only the top-level ``data`` array.

The platform identifiers currently exceed JavaScript's safe integer range.
This module deliberately uses Python's JSON implementation so those integers
are never rounded by a JavaScript parse/stringify round trip.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


SUPPORTED_PROJECTION_VERSION = "chemical_fiber_projection_v1"
PLATFORM_BINDING_VERSION = "nmbdc_platform_batch_v1"
JAVASCRIPT_MAX_SAFE_INTEGER = 9_007_199_254_740_991


class PlatformBatchError(ValueError):
    """Raised when a projection or platform batch document is incompatible."""


STATUS_MAP = {
    "extracted": "已抽取",
    "verified": "已验证",
    "needs_review": "待复核",
    "extraction_pending": "抽取中",
    "not_reported": "未报告",
    "not_applicable": "不适用",
}

STATUS_PRIORITY = {
    "verified": 6,
    "extracted": 5,
    "not_applicable": 4,
    "needs_review": 3,
    "extraction_pending": 2,
    "not_reported": 1,
}

SOURCE_PRIORITY = {
    "paper_metadata": 5,
    "sample_catalog": 5,
    "fact_candidate": 4,
    "candidate_record": 3,
}

EXPECTED_OBJECT_FIELDS = {
    "数据记录键",
    "投影版本",
    "文献编号",
    "数据状态",
    "文献标题",
    "DOI或链接",
    "发表年份",
    "期刊",
    "原始文件名",
    "样品编号",
    "样品组编号",
    "样品别名",
    "材料体系",
    "纤维类型",
    "成分表达式",
    "变量名称",
    "变量原始值",
    "变量单位",
    "基体名称",
    "基体含量原始值",
    "基体单位",
    "填料或改性组分",
    "溶剂或助剂",
}

EXPECTED_OPERATION_FIELDS = {
    "operation1": {
        "工艺路线",
        "纺丝方法",
        "工艺参数摘要",
        "后处理摘要",
        "成分证据",
        "工艺证据",
    },
    "operation2": {
        "记录审核状态",
        "原始论文PDF",
        "补充材料或原始测试文件",
        "关键图像",
    },
}

EXPECTED_RESULT_FIELDS = {
    "result1": {"成分与配方明细"},
    "result2": {"工艺参数明细"},
    "result3": {"结构测试结果"},
    "result4": {"性能测试结果"},
    "result5": {"其他未映射指标", "数据质量与溯源"},
}

FIXED_FIELD_PATHS = {
    "paper.metadata.title",
    "paper.metadata.doi_or_url",
    "paper.metadata.year",
    "paper.metadata.journal",
    "fiber_sample.identity.sample_id",
    "fiber_sample.identity.aliases",
    "fiber_sample.identity.group_id",
    "fiber_sample.identity.fiber_type",
    "fiber_sample.composition.material_system",
    "fiber_sample.composition.expression",
    "fiber_sample.composition.variable_name",
    "fiber_sample.composition.variable_value",
    "fiber_sample.composition.variable_unit",
    "fiber_sample.composition.matrix.name",
    "fiber_sample.composition.matrix.content",
    "fiber_sample.composition.matrix.unit",
    "fiber_sample.composition.additives.expression",
    "fiber_sample.composition.solvent_or_aid",
    "fiber_sample.process.route",
    "fiber_sample.process.spinning_method",
    "fiber_sample.process.parameters",
    "fiber_sample.process.post_treatment",
}

OBJECT_PATH_MAP = {
    "paper.metadata.title": "文献标题",
    "paper.metadata.doi_or_url": "DOI或链接",
    "paper.metadata.journal": "期刊",
    "fiber_sample.identity.sample_id": "样品编号",
    "fiber_sample.identity.group_id": "样品组编号",
    "fiber_sample.identity.aliases": "样品别名",
    "fiber_sample.identity.fiber_type": "纤维类型",
    "fiber_sample.composition.material_system": "材料体系",
    "fiber_sample.composition.expression": "成分表达式",
    "fiber_sample.composition.variable_name": "变量名称",
    "fiber_sample.composition.variable_value": "变量原始值",
    "fiber_sample.composition.variable_unit": "变量单位",
    "fiber_sample.composition.matrix.name": "基体名称",
    "fiber_sample.composition.matrix.content": "基体含量原始值",
    "fiber_sample.composition.matrix.unit": "基体单位",
    "fiber_sample.composition.additives.expression": "填料或改性组分",
    "fiber_sample.composition.solvent_or_aid": "溶剂或助剂",
}

OPERATION_PATH_MAP = {
    "fiber_sample.process.route": "工艺路线",
    "fiber_sample.process.spinning_method": "纺丝方法",
    "fiber_sample.process.parameters": "工艺参数摘要",
    "fiber_sample.process.post_treatment": "后处理摘要",
}


def _is_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PlatformBatchError(f"{path} must be an object")
    return value


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise PlatformBatchError(f"{path} must be an array")
    return value


def _require_platform_id(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PlatformBatchError(
            f"{path} must be a positive JSON integer; do not parse it as float"
        )
    return value


def _ordered_field_names(blocks: Mapping[str, Any], order_key: str) -> list[str]:
    raw = blocks.get(order_key)
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise PlatformBatchError(f"template blocks require string array {order_key}")
    return raw


def validate_platform_binding(batch_template: Mapping[str, Any]) -> dict[str, Any]:
    """Validate that a downloaded platform batch template matches this adapter."""
    root = _require_mapping(batch_template, "batch_template")
    dataset = _require_mapping(root.get("dataset"), "batch_template.dataset")
    template = _require_mapping(root.get("template"), "batch_template.template")
    dataset_id = _require_platform_id(
        dataset.get("_id"), "batch_template.dataset._id"
    )
    template_id = _require_platform_id(
        template.get("_id"), "batch_template.template._id"
    )

    object_section = _require_mapping(
        template.get("object"), "batch_template.template.object"
    )
    object_blocks = _require_mapping(
        object_section.get("blocks"), "batch_template.template.object.blocks"
    )
    object_names = set(_ordered_field_names(object_blocks, "_ord"))
    missing_object = sorted(EXPECTED_OBJECT_FIELDS - object_names)
    if missing_object:
        raise PlatformBatchError(
            "platform object schema is missing expected fields: "
            + ", ".join(missing_object)
        )

    operations = _require_list(
        template.get("operations"), "batch_template.template.operations"
    )
    operation_index = {
        str(item.get("id")): item
        for item in operations
        if isinstance(item, Mapping) and item.get("id")
    }
    for section_id, expected_fields in EXPECTED_OPERATION_FIELDS.items():
        section = _require_mapping(
            operation_index.get(section_id),
            f"batch_template.template.operations[{section_id}]",
        )
        blocks = _require_mapping(
            section.get("blocks"),
            f"batch_template.template.operations[{section_id}].blocks",
        )
        present = set(_ordered_field_names(blocks, "_ord"))
        missing = sorted(expected_fields - present)
        if missing:
            raise PlatformBatchError(
                f"{section_id} is missing expected fields: {', '.join(missing)}"
            )

    results = _require_list(
        template.get("results"), "batch_template.template.results"
    )
    result_index = {
        str(item.get("id")): item
        for item in results
        if isinstance(item, Mapping) and item.get("id")
    }
    for section_id, expected_fields in EXPECTED_RESULT_FIELDS.items():
        section = _require_mapping(
            result_index.get(section_id),
            f"batch_template.template.results[{section_id}]",
        )
        blocks = _require_mapping(
            section.get("blocks"),
            f"batch_template.template.results[{section_id}].blocks",
        )
        present = set(_ordered_field_names(blocks, "_ord"))
        missing = sorted(expected_fields - present)
        if missing:
            raise PlatformBatchError(
                f"{section_id} is missing expected fields: {', '.join(missing)}"
            )

    return {
        "dataset_id": dataset_id,
        "template_id": template_id,
        "ids_exceed_javascript_safe_integer": (
            dataset_id > JAVASCRIPT_MAX_SAFE_INTEGER
            or template_id > JAVASCRIPT_MAX_SAFE_INTEGER
        ),
    }


def _raw_value(value: Mapping[str, Any]) -> Any:
    raw = value.get("raw_value")
    if _is_nonempty(raw):
        return raw
    return value.get("value_text")


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        if float(value).is_integer():
            return int(value)
        return float(value)
    return None


def _field_path(value: Mapping[str, Any]) -> str:
    return str(value.get("field_path") or "")


def _canonical_entity_key(
    paper_id: Any,
    entity_type: str,
    sample_id: str | None = None,
) -> str:
    if entity_type == "paper":
        return f"paper:{paper_id}"
    if sample_id:
        return f"paper:{paper_id}:sample:{sample_id}"
    return f"paper:{paper_id}:unassigned"


def validate_projection_contract(
    projection: Mapping[str, Any],
    *,
    path: str = "projection",
) -> dict[str, int]:
    """Reject incomplete projections before they can become plausible bad data."""
    root = _require_mapping(projection, path)
    version = root.get("schema_version")
    if version != SUPPORTED_PROJECTION_VERSION:
        raise PlatformBatchError(
            f"{path}.schema_version must be "
            f"{SUPPORTED_PROJECTION_VERSION!r}, got {version!r}"
        )

    paper = _require_mapping(root.get("paper"), f"{path}.paper")
    paper_id = paper.get("paper_id")
    if not _is_nonempty(paper_id):
        raise PlatformBatchError(f"{path}.paper.paper_id is required")

    entities = _require_list(root.get("entities"), f"{path}.entities")
    entity_types: dict[str, str] = {}
    for index, raw_entity in enumerate(entities):
        entity_path = f"{path}.entities[{index}]"
        entity = _require_mapping(raw_entity, entity_path)
        entity_type = str(entity.get("entity_type") or "").strip()
        if entity_type not in {"paper", "fiber_sample"}:
            raise PlatformBatchError(
                f"{entity_path}.entity_type must be 'paper' or 'fiber_sample'"
            )
        entity_key = str(entity.get("entity_key") or "").strip()
        if not entity_key:
            raise PlatformBatchError(f"{entity_path}.entity_key is required")
        if entity_key in entity_types:
            raise PlatformBatchError(
                f"{path}.entities contains duplicate entity_key {entity_key!r}"
            )
        sample_id = str(entity.get("sample_id") or "").strip() or None
        expected_key = _canonical_entity_key(
            paper_id,
            entity_type,
            sample_id,
        )
        if entity_key != expected_key:
            raise PlatformBatchError(
                f"{entity_path}.entity_key must be {expected_key!r}, "
                f"got {entity_key!r}"
            )
        entity_types[entity_key] = entity_type

    paper_key = _canonical_entity_key(paper_id, "paper")
    if entity_types.get(paper_key) != "paper":
        raise PlatformBatchError(
            f"{path}.entities must contain paper entity {paper_key!r}"
        )

    values = _require_list(root.get("values"), f"{path}.values")
    for index, raw_value in enumerate(values):
        value_path = f"{path}.values[{index}]"
        value = _require_mapping(raw_value, value_path)
        entity_type = str(value.get("entity_type") or "").strip()
        if entity_type not in {"paper", "fiber_sample"}:
            raise PlatformBatchError(
                f"{value_path}.entity_type must be 'paper' or 'fiber_sample'"
            )
        entity_key = str(value.get("entity_key") or "").strip()
        if entity_types.get(entity_key) != entity_type:
            raise PlatformBatchError(
                f"{value_path}.entity_key {entity_key!r} does not identify "
                f"a declared {entity_type} entity"
            )
        field_path = _field_path(value)
        expected_prefix = (
            "paper." if entity_type == "paper" else "fiber_sample."
        )
        if not field_path.startswith(expected_prefix):
            raise PlatformBatchError(
                f"{value_path}.field_path must start with {expected_prefix!r}"
            )
        if entity_type == "paper" and field_path not in FIXED_FIELD_PATHS:
            raise PlatformBatchError(
                f"{value_path}.field_path {field_path!r} is not supported; "
                "unknown paper-level values cannot be dropped"
            )
        mapping_status = str(value.get("mapping_status") or "").strip()
        if mapping_status not in {"mapped", "unmapped"}:
            raise PlatformBatchError(
                f"{value_path}.mapping_status must be 'mapped' or 'unmapped'"
            )
        status = value.get("status")
        if not isinstance(status, str) or not status.strip():
            raise PlatformBatchError(f"{value_path}.status is required")
        if not _is_nonempty(_raw_value(value)):
            raise PlatformBatchError(
                f"{value_path} must preserve a non-empty raw_value or "
                "value_text; missing-value facts belong in pending_facts"
            )

    pending = root.get("pending_facts", [])
    pending_ids: set[str] = set()
    if pending is not None:
        for index, raw_fact in enumerate(
            _require_list(pending, f"{path}.pending_facts")
        ):
            fact_path = f"{path}.pending_facts[{index}]"
            fact = _require_mapping(raw_fact, fact_path)
            fact_id = str(fact.get("fact_id") or "").strip()
            if not fact_id:
                raise PlatformBatchError(f"{fact_path}.fact_id is required")
            if fact_id in pending_ids:
                raise PlatformBatchError(
                    f"{path}.pending_facts contains duplicate fact_id "
                    f"{fact_id!r}"
                )
            pending_ids.add(fact_id)
            status = fact.get("status")
            if not isinstance(status, str) or not status.strip():
                raise PlatformBatchError(f"{fact_path}.status is required")

    return {
        "entity_count": len(entities),
        "value_count": len(values),
        "pending_fact_count": len(pending_ids),
    }


def _normalized_internal_status(value: Mapping[str, Any]) -> str:
    local = str(value.get("status") or "").strip()
    return local if local in STATUS_MAP else "needs_review"


def _scalar_raw_key(value: Mapping[str, Any]) -> str:
    raw = _raw_value(value)
    if isinstance(raw, str):
        return raw.strip()
    return _canonical_json(raw)


def _scalar_candidate_rank(value: Mapping[str, Any]) -> tuple[Any, ...]:
    confidence = _number(value.get("confidence"))
    return (
        STATUS_PRIORITY[_normalized_internal_status(value)],
        SOURCE_PRIORITY.get(str(value.get("source_kind") or ""), 0),
        bool(_evidence_text(value)),
        bool(_source_location(value)),
        confidence if confidence is not None else -1,
        _canonical_json(value),
    )


def _select_scalar_by_path(
    values: Sequence[Mapping[str, Any]],
    field_path: str,
) -> tuple[Mapping[str, Any] | None, list[Mapping[str, Any]]]:
    """Select one stable scalar and return distinct conflicting candidates.

    Repeated evidence for the same raw value is collapsed. Different raw
    values are never silently discarded: their best representatives are
    returned so the caller can retain them in the platform long-tail table.
    """
    candidates = [
        value
        for value in values
        if _field_path(value) == field_path and _is_nonempty(_raw_value(value))
    ]
    if not candidates:
        return None, []

    representatives: dict[str, Mapping[str, Any]] = {}
    for candidate in candidates:
        raw_key = _scalar_raw_key(candidate)
        existing = representatives.get(raw_key)
        if existing is None or _scalar_candidate_rank(
            candidate
        ) > _scalar_candidate_rank(existing):
            representatives[raw_key] = candidate

    winner = max(representatives.values(), key=_scalar_candidate_rank)
    winner_key = _scalar_raw_key(winner)
    conflicts = sorted(
        (
            candidate
            for raw_key, candidate in representatives.items()
            if raw_key != winner_key
        ),
        key=lambda candidate: (
            _scalar_candidate_rank(candidate),
            _scalar_raw_key(candidate),
        ),
        reverse=True,
    )
    return winner, conflicts


def _set_if_nonempty(target: dict[str, Any], key: str, value: Any) -> None:
    if _is_nonempty(value):
        target[key] = value


def _aliases_for_platform(raw_value: Any) -> str:
    if not isinstance(raw_value, str):
        return str(raw_value)
    stripped = raw_value.strip()
    if not stripped:
        return ""
    try:
        parsed = json.loads(stripped)
    except (TypeError, ValueError, json.JSONDecodeError):
        return stripped
    if isinstance(parsed, list):
        return "、".join(str(item).strip() for item in parsed if str(item).strip())
    return stripped


def _status(value: Mapping[str, Any]) -> str:
    return STATUS_MAP[_normalized_internal_status(value)]


def _record_status(values: Sequence[Mapping[str, Any]]) -> str:
    statuses = {_normalized_internal_status(value) for value in values}
    if "needs_review" in statuses:
        return "待复核"
    if "extraction_pending" in statuses:
        return "抽取中"
    if "extracted" in statuses:
        return "已抽取"
    if statuses and statuses <= {"verified"}:
        return "已验证"
    if statuses and statuses <= {"not_applicable"}:
        return "不适用"
    return "未报告"


def _evidence(value: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = value.get("evidence")
    return raw if isinstance(raw, Mapping) else {}


def _evidence_text(value: Mapping[str, Any]) -> str:
    return str(_evidence(value).get("evidence_text") or "").strip()


def _source_location(value: Mapping[str, Any]) -> str:
    evidence = _evidence(value)
    explicit = str(evidence.get("source_location") or "").strip()
    if explicit:
        return explicit
    parts: list[str] = []
    if _is_nonempty(evidence.get("source_page")):
        parts.append(f"p.{evidence['source_page']}")
    if _is_nonempty(evidence.get("source_block_id")):
        parts.append(f"block:{evidence['source_block_id']}")
    return "; ".join(parts)


def _join_evidence(values: Iterable[Mapping[str, Any]]) -> str:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        text = _evidence_text(value)
        if text and text not in seen:
            seen.add(text)
            ordered.append(text)
    return "\n".join(ordered)


def _label(value: Mapping[str, Any]) -> str:
    label = str(value.get("field_label") or "").strip()
    if label:
        return label
    path = _field_path(value)
    return path.rsplit(".", 1)[-1] if path else "未命名指标"


def _add_numeric_shape(
    target: dict[str, Any],
    value: Mapping[str, Any],
    *,
    number_field: str,
    range_field: str,
) -> None:
    low = _number(value.get("range_min"))
    high = _number(value.get("range_max"))
    if low is not None and high is not None:
        target[range_field] = {"lb": low, "ub": high}
        return
    scalar = _number(value.get("value_number"))
    if scalar is not None:
        target[number_field] = scalar


def _add_common_detail_fields(
    target: dict[str, Any],
    value: Mapping[str, Any],
    *,
    unit_field: str,
    evidence_field: str,
    location_field: str,
    confidence_field: str,
    status_field: str,
    method_field: str | None = None,
    condition_field: str | None = None,
) -> None:
    _set_if_nonempty(target, unit_field, value.get("unit"))
    if method_field:
        _set_if_nonempty(target, method_field, value.get("method"))
    if condition_field:
        _set_if_nonempty(target, condition_field, value.get("condition"))
    _set_if_nonempty(target, evidence_field, _evidence_text(value))
    _set_if_nonempty(target, location_field, _source_location(value))
    confidence = _number(value.get("confidence"))
    if confidence is not None:
        target[confidence_field] = confidence
    target[status_field] = _status(value)


def _composition_role(field_path: str) -> str:
    if ".matrix." in field_path:
        return "基体"
    if ".polymer." in field_path:
        return "聚合物"
    if ".additive" in field_path:
        return "填料或改性组分"
    if ".solvent" in field_path or ".aid" in field_path:
        return "溶剂或助剂"
    return "组分"


def _process_stage(field_path: str) -> str:
    stages = (
        ("pre_treatment", "预处理"),
        ("melt_extrusion", "熔融挤出"),
        ("spinning", "纺丝"),
        ("drawing", "牵伸"),
        ("heat_setting", "热定型"),
        ("winding", "卷绕"),
        ("cooling", "冷却"),
        ("oiling", "上油"),
    )
    return next((label for token, label in stages if token in field_path), "工艺")


def _performance_category(field_path: str) -> str:
    categories = (
        (".mechanical.", "力学性能"),
        (".thermal.", "热性能"),
        (".electrical.", "电学性能"),
        (".biological.", "生物性能"),
        (".other.", "其他性能"),
    )
    return next(
        (label for token, label in categories if token in field_path),
        "性能",
    )


def _fact_type(field_path: str) -> str:
    if ".composition." in field_path:
        return "成分"
    if ".process." in field_path:
        return "工艺"
    if ".structure." in field_path or ".fineness." in field_path:
        return "结构"
    if ".performance." in field_path:
        return "性能"
    return "其他"


def _composition_row(value: Mapping[str, Any]) -> dict[str, Any]:
    row = {
        "组分角色": _composition_role(_field_path(value)),
        "组分名称": _label(value),
        "含量原始值": str(_raw_value(value)),
    }
    _add_numeric_shape(
        row,
        value,
        number_field="含量数值",
        range_field="含量范围",
    )
    _add_common_detail_fields(
        row,
        value,
        unit_field="成分单位",
        evidence_field="成分证据原文",
        location_field="成分来源位置",
        confidence_field="成分置信度",
        status_field="成分抽取状态",
        condition_field="条件或说明",
    )
    return row


def _process_row(value: Mapping[str, Any]) -> dict[str, Any]:
    row = {
        "工艺阶段": _process_stage(_field_path(value)),
        "参数名称": _label(value),
        "工艺参数原始值": str(_raw_value(value)),
    }
    _add_numeric_shape(
        row,
        value,
        number_field="工艺参数数值",
        range_field="工艺参数范围",
    )
    _add_common_detail_fields(
        row,
        value,
        unit_field="工艺参数单位",
        evidence_field="工艺证据原文",
        location_field="工艺来源位置",
        confidence_field="工艺置信度",
        status_field="工艺抽取状态",
        method_field="方法或设备",
        condition_field="工艺条件",
    )
    return row


def _structure_row(value: Mapping[str, Any]) -> dict[str, Any]:
    row = {
        "结构指标名称": _label(value),
        "结构原始值": str(_raw_value(value)),
    }
    _add_numeric_shape(
        row,
        value,
        number_field="结构数值",
        range_field="结构范围",
    )
    _add_common_detail_fields(
        row,
        value,
        unit_field="结构单位",
        evidence_field="结构证据原文",
        location_field="结构来源位置",
        confidence_field="结构置信度",
        status_field="结构抽取状态",
        method_field="表征方法",
        condition_field="结构测试条件",
    )
    return row


def _performance_row(value: Mapping[str, Any]) -> dict[str, Any]:
    row = {
        "性能类别": _performance_category(_field_path(value)),
        "性能指标名称": _label(value),
        "性能原始值": str(_raw_value(value)),
    }
    _add_numeric_shape(
        row,
        value,
        number_field="性能数值",
        range_field="性能范围",
    )
    _add_common_detail_fields(
        row,
        value,
        unit_field="性能单位",
        evidence_field="性能证据原文",
        location_field="性能来源位置",
        confidence_field="性能置信度",
        status_field="性能抽取状态",
        method_field="性能测试方法",
        condition_field="性能测试条件",
    )
    row["性能审核状态"] = (
        "存疑" if row["性能抽取状态"] == "待复核" else "待审核"
    )
    return row


def _unmapped_row(
    value: Mapping[str, Any], sample_id: str | None
) -> dict[str, Any]:
    row = {
        "事实类型": _fact_type(_field_path(value)),
        "长尾指标或参数名称": _label(value),
        "长尾原始值": str(_raw_value(value)),
    }
    _set_if_nonempty(row, "长尾单位", value.get("unit"))
    _set_if_nonempty(row, "长尾方法", value.get("method"))
    _set_if_nonempty(row, "长尾条件", value.get("condition"))
    _set_if_nonempty(row, "长尾样品编号", sample_id)
    _set_if_nonempty(row, "长尾证据原文", _evidence_text(value))
    _set_if_nonempty(row, "长尾来源位置", _source_location(value))
    confidence = _number(value.get("confidence"))
    if confidence is not None:
        row["长尾置信度"] = confidence
    row["长尾抽取状态"] = _status(value)
    return row


def _scalar_conflict_row(
    value: Mapping[str, Any],
    winner: Mapping[str, Any],
    sample_id: str | None,
) -> dict[str, Any]:
    candidate = dict(value)
    candidate["field_label"] = f"标量冲突候选：{_label(value)}"
    candidate["status"] = "needs_review"
    existing_condition = str(candidate.get("condition") or "").strip()
    audit_note = (
        "同一标量字段存在多个不同候选值；"
        f"主记录采用：{_raw_value(winner)}"
    )
    candidate["condition"] = (
        f"{existing_condition}；{audit_note}"
        if existing_condition
        else audit_note
    )
    return _unmapped_row(candidate, sample_id)


def _pending_facts_for_record(
    projection: Mapping[str, Any],
    sample_id: str | None,
) -> list[Mapping[str, Any]]:
    raw_pending = projection.get("pending_facts", [])
    if raw_pending is None:
        return []
    pending = _require_list(raw_pending, "projection.pending_facts")
    matched: list[Mapping[str, Any]] = []
    for index, item in enumerate(pending):
        fact = _require_mapping(item, f"projection.pending_facts[{index}]")
        fact_sample_id = str(fact.get("sample_id") or "").strip() or None
        if fact_sample_id == sample_id:
            matched.append(fact)
    return matched


def _entity_keys(projection: Mapping[str, Any]) -> tuple[list[str], dict[str, Any]]:
    keys: list[str] = []
    entities_by_key: dict[str, Any] = {}
    paper = _require_mapping(projection.get("paper"), "projection.paper")
    paper_id = paper.get("paper_id")
    entities = projection.get("entities")
    if isinstance(entities, list):
        for entity in entities:
            if not isinstance(entity, Mapping):
                continue
            if entity.get("entity_type") != "fiber_sample":
                continue
            key = str(entity.get("entity_key") or "").strip()
            if key and key not in entities_by_key:
                keys.append(key)
                entities_by_key[key] = entity

    values = projection.get("values")
    if isinstance(values, list):
        for value in values:
            if not isinstance(value, Mapping):
                continue
            if value.get("entity_type") != "fiber_sample":
                continue
            key = str(value.get("entity_key") or "").strip()
            if key and key not in entities_by_key:
                keys.append(key)
                entities_by_key[key] = {}

    raw_pending = projection.get("pending_facts", [])
    if raw_pending is not None:
        for index, item in enumerate(
            _require_list(raw_pending, "projection.pending_facts")
        ):
            fact = _require_mapping(
                item,
                f"projection.pending_facts[{index}]",
            )
            sample_id = str(fact.get("sample_id") or "").strip() or None
            key = (
                f"paper:{paper_id}:sample:{sample_id}"
                if sample_id
                else f"paper:{paper_id}:unassigned"
            )
            if key not in entities_by_key:
                keys.append(key)
                entities_by_key[key] = {"sample_id": sample_id}

    if not keys:
        fallback = f"paper:{paper_id}:unassigned"
        keys.append(fallback)
        entities_by_key[fallback] = {}
    return keys, entities_by_key


def _record_digest(projection: Mapping[str, Any], entity_key: str) -> str:
    paper = _require_mapping(projection.get("paper"), "projection.paper")
    identity = {
        "schema_version": projection.get("schema_version"),
        "paper_id": paper.get("paper_id"),
        "entity_key": entity_key,
    }
    return _sha256(identity)[:24]


def _build_record(
    projection: Mapping[str, Any],
    entity_key: str,
    entity: Mapping[str, Any],
    *,
    exported_at: str | None,
) -> dict[str, Any]:
    paper = _require_mapping(projection.get("paper"), "projection.paper")
    paper_id = paper.get("paper_id")
    if not _is_nonempty(paper_id):
        raise PlatformBatchError("projection.paper.paper_id is required")

    all_values = [
        value
        for value in _require_list(projection.get("values"), "projection.values")
        if isinstance(value, Mapping)
    ]
    paper_values = [
        value for value in all_values if value.get("entity_type") == "paper"
    ]
    entity_values = [
        value
        for value in all_values
        if value.get("entity_type") == "fiber_sample"
        and str(value.get("entity_key") or "") == entity_key
    ]
    scalar_values: dict[str, Mapping[str, Any]] = {}
    scalar_conflicts: list[
        tuple[str, Mapping[str, Any], Mapping[str, Any]]
    ] = []
    for field_path in sorted(FIXED_FIELD_PATHS):
        candidates = (
            paper_values if field_path.startswith("paper.") else entity_values
        )
        winner, conflicts = _select_scalar_by_path(candidates, field_path)
        if winner is None:
            continue
        scalar_values[field_path] = winner
        scalar_conflicts.extend(
            (field_path, conflict, winner) for conflict in conflicts
        )

    sample_id = str(entity.get("sample_id") or "").strip() or None
    if sample_id is None:
        sample_id_value = scalar_values.get(
            "fiber_sample.identity.sample_id"
        )
        if sample_id_value:
            sample_id = str(_raw_value(sample_id_value)).strip() or None

    retained_entity_values = [
        value
        for value in entity_values
        if _field_path(value) not in FIXED_FIELD_PATHS
        or scalar_values.get(_field_path(value)) is value
    ]
    pending_facts = _pending_facts_for_record(projection, sample_id)

    digest = _record_digest(projection, entity_key)
    object_payload: dict[str, Any] = {
        "数据记录键": f"AI4S-{digest}",
        "投影版本": str(projection.get("schema_version")),
        "文献编号": str(paper_id),
        "数据状态": _record_status(retained_entity_values),
    }

    for field_path, platform_name in OBJECT_PATH_MAP.items():
        value = scalar_values.get(field_path)
        if not value:
            continue
        raw = _raw_value(value)
        if field_path == "fiber_sample.identity.aliases":
            raw = _aliases_for_platform(raw)
        _set_if_nonempty(object_payload, platform_name, raw)

    year_value = scalar_values.get("paper.metadata.year")
    if year_value:
        year = _number(year_value.get("value_number"))
        if year is None:
            try:
                year = int(str(_raw_value(year_value)).strip())
            except (TypeError, ValueError):
                year = None
        if year is not None:
            object_payload["发表年份"] = year

    _set_if_nonempty(object_payload, "原始文件名", paper.get("original_filename"))
    _set_if_nonempty(object_payload, "样品编号", sample_id)
    _set_if_nonempty(
        object_payload,
        "样品组编号",
        entity.get("sample_group_id"),
    )

    operation1: dict[str, Any] = {"id": "operation1"}
    for field_path, platform_name in OPERATION_PATH_MAP.items():
        value = scalar_values.get(field_path)
        if value:
            _set_if_nonempty(operation1, platform_name, _raw_value(value))
    _set_if_nonempty(
        operation1,
        "成分证据",
        _join_evidence(
            value
            for value in entity_values
            if ".composition." in _field_path(value)
        ),
    )
    _set_if_nonempty(
        operation1,
        "工艺证据",
        _join_evidence(
            value for value in entity_values if ".process." in _field_path(value)
        ),
    )
    operation2: dict[str, Any] = {
        "id": "operation2",
        "记录审核状态": "待审核",
    }

    composition_rows: list[dict[str, Any]] = []
    process_rows: list[dict[str, Any]] = []
    structure_rows: list[dict[str, Any]] = []
    performance_rows: list[dict[str, Any]] = []
    unmapped_rows: list[dict[str, Any]] = []
    exported_unmapped_ids: set[int] = set()
    for value in retained_entity_values:
        path = _field_path(value)
        if path in FIXED_FIELD_PATHS:
            continue
        if str(value.get("mapping_status") or "") == "unmapped":
            unmapped_rows.append(_unmapped_row(value, sample_id))
            exported_unmapped_ids.add(id(value))
        elif ".composition." in path:
            composition_rows.append(_composition_row(value))
        elif ".process." in path:
            process_rows.append(_process_row(value))
        elif ".structure." in path or ".fineness." in path:
            structure_rows.append(_structure_row(value))
        elif ".performance." in path:
            performance_rows.append(_performance_row(value))
        else:
            unmapped_rows.append(_unmapped_row(value, sample_id))
            exported_unmapped_ids.add(id(value))

    for field_path, conflict, winner in scalar_conflicts:
        conflict_sample_id = (
            sample_id if field_path.startswith("fiber_sample.") else None
        )
        unmapped_rows.append(
            _scalar_conflict_row(conflict, winner, conflict_sample_id)
        )

    scalar_conflict_count = len(scalar_conflicts)
    pending_count = len(pending_facts)
    evidence_values = [
        value
        for value in retained_entity_values
        if value.get("status") not in {"not_reported", "not_applicable"}
    ]
    evidence_count = sum(1 for value in evidence_values if _evidence_text(value))
    mapped_count = sum(
        1
        for value in retained_entity_values
        if value.get("mapping_status") == "mapped"
        and id(value) not in exported_unmapped_ids
    )
    unmapped_count = len(unmapped_rows)
    review_count = sum(
        1
        for value in retained_entity_values
        if _normalized_internal_status(value)
        in {"needs_review", "extraction_pending"}
    )
    review_count += pending_count + scalar_conflict_count
    requires_manual_review = bool(
        pending_count or scalar_conflict_count or unmapped_count
    )
    if requires_manual_review:
        object_payload["数据状态"] = "待复核"
    operation2["记录审核状态"] = (
        "存疑" if object_payload["数据状态"] == "待复核" else "待审核"
    )
    quality: dict[str, Any] = {
        "抽取方式": "AI4S evidence-grounded extraction",
        "映射版本": PLATFORM_BINDING_VERSION,
        "源数据签名": _sha256(projection),
        "证据覆盖率": (
            round(evidence_count / len(evidence_values), 4)
            if evidence_values
            else 0
        ),
        "已映射值数量": mapped_count,
        "未映射事实数量": unmapped_count,
        "待复核数量": review_count,
    }
    _set_if_nonempty(quality, "导出时间", exported_at)
    if review_count or unmapped_count:
        quality["质量审核意见"] = "含待复核或未映射事实，需人工复核"
        audit_details: list[str] = []
        if pending_count:
            audit_details.append(f"{pending_count} 个待补值事实未写入数据行")
        if scalar_conflict_count:
            audit_details.append(
                f"{scalar_conflict_count} 个标量冲突候选已保留至长尾表"
            )
        if audit_details:
            quality["质量审核意见"] += "；" + "；".join(audit_details)

    return {
        "meta": {"数据ID": f"data-{digest}"},
        "content": {
            "object": object_payload,
            "operations": [operation1, operation2],
            "results": [
                {
                    "id": "result1",
                    "成分与配方明细": composition_rows,
                },
                {
                    "id": "result2",
                    "工艺参数明细": process_rows,
                },
                {
                    "id": "result3",
                    "结构测试结果": structure_rows,
                },
                {
                    "id": "result4",
                    "性能测试结果": performance_rows,
                },
                {
                    "id": "result5",
                    "其他未映射指标": unmapped_rows,
                    "数据质量与溯源": quality,
                },
            ],
        },
    }


def build_platform_batch(
    batch_template: Mapping[str, Any],
    projections: Iterable[Mapping[str, Any]],
    *,
    exported_at: str | None = None,
) -> dict[str, Any]:
    """Build an upload-ready platform batch document.

    ``batch_template`` must be the JSON downloaded from the target dataset's
    batch-upload page.  Its example ``data`` entries are intentionally ignored.
    """
    validate_platform_binding(batch_template)
    projection_list = list(projections)
    if not projection_list:
        raise PlatformBatchError("at least one projection is required")

    records: list[dict[str, Any]] = []
    for index, projection in enumerate(projection_list):
        if not isinstance(projection, Mapping):
            raise PlatformBatchError(f"projections[{index}] must be an object")
        validate_projection_contract(
            projection,
            path=f"projections[{index}]",
        )
        keys, entities = _entity_keys(projection)
        for entity_key in keys:
            records.append(
                _build_record(
                    projection,
                    entity_key,
                    _require_mapping(
                        entities.get(entity_key, {}),
                        f"projections[{index}].entities[{entity_key}]",
                    ),
                    exported_at=exported_at,
                )
            )

    result = {
        "dataset": copy.deepcopy(batch_template["dataset"]),
        "template": copy.deepcopy(batch_template["template"]),
        "data": records,
    }
    validate_platform_batch(result)
    return result


def _allowed_candidate_values(field_schema: Mapping[str, Any]) -> set[str]:
    misc = field_schema.get("misc")
    if not isinstance(misc, Mapping):
        return set()
    values = {
        str(value)
        for value in misc.get("opt", [])
        if isinstance(value, (str, int, float))
    }
    groups = misc.get("grp")
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, Mapping):
                continue
            values.update(
                str(value)
                for value in group.get("items", [])
                if isinstance(value, (str, int, float))
            )
    return values


def _validate_nested_payload(
    payload: Any,
    blocks: Mapping[str, Any],
    *,
    order_key: str,
    path: str,
) -> None:
    obj = _require_mapping(payload, path)
    fields = _ordered_field_names(blocks, order_key)
    allowed = set(fields)
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise PlatformBatchError(
            f"{path} contains fields not present in the platform template: "
            + ", ".join(unknown)
        )
    for name in fields:
        schema = _require_mapping(blocks.get(name), f"{path} schema[{name}]")
        if schema.get("r") is True and not _is_nonempty(obj.get(name)):
            raise PlatformBatchError(f"{path}.{name} is required")
        if name in obj:
            _validate_field_value(obj[name], schema, f"{path}.{name}")
            if name == "发表年份":
                year = obj[name]
                if (
                    isinstance(year, bool)
                    or not isinstance(year, int)
                    or year < 1000
                    or year > 9999
                ):
                    raise PlatformBatchError(
                        f"{path}.{name} must be a four-digit integer"
                    )
            if name.endswith("置信度") or name == "证据覆盖率":
                confidence = obj[name]
                _validate_number(confidence, f"{path}.{name}")
                if not 0 <= float(confidence) <= 1:
                    raise PlatformBatchError(
                        f"{path}.{name} must be between 0 and 1"
                    )


def _validate_number(value: Any, path: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise PlatformBatchError(f"{path} must be a finite JSON number")


def _validate_attachment(
    value: Any, schema: Mapping[str, Any], path: str
) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PlatformBatchError(f"{path} must be a non-empty relative path string")
    misc = schema.get("misc")
    if not isinstance(misc, Mapping):
        return
    items = [item.strip() for item in value.split("、")]
    if any(not item for item in items):
        raise PlatformBatchError(f"{path} contains an empty attachment path")
    if len(items) > 1 and misc.get("multi") is not True:
        raise PlatformBatchError(f"{path} does not allow multiple attachments")
    for item in items:
        attachment_path = Path(item)
        windows_path = PureWindowsPath(item)
        posix_path = PurePosixPath(item)
        if (
            attachment_path.is_absolute()
            or bool(attachment_path.drive)
            or windows_path.is_absolute()
            or bool(windows_path.drive)
            or posix_path.is_absolute()
            or ".." in windows_path.parts
            or ".." in posix_path.parts
        ):
            raise PlatformBatchError(
                f"{path} must use a safe relative attachment path"
            )

    format_key = "imageFormats" if schema.get("t") == 4 else "fileFormats"
    allowed = {
        str(item).lower()
        for item in misc.get(format_key, [])
        if isinstance(item, str)
    }
    if not allowed:
        return
    for item in items:
        suffix = Path(item).suffix.lower()
        if suffix not in allowed:
            raise PlatformBatchError(
                f"{path} uses unsupported extension {suffix!r}; "
                f"allowed: {sorted(allowed)}"
            )


def _validate_field_value(
    value: Any, schema: Mapping[str, Any], path: str
) -> None:
    field_type = schema.get("t")
    if field_type == 1:
        if not isinstance(value, str) or not value.strip():
            raise PlatformBatchError(f"{path} must be a non-empty string")
        return
    if field_type == 2:
        _validate_number(value, path)
        return
    if field_type == 3:
        range_value = _require_mapping(value, path)
        misc = schema.get("misc")
        range_type = misc.get("type", 0) if isinstance(misc, Mapping) else 0
        if range_type not in {0, 1}:
            raise PlatformBatchError(
                f"{path} uses unsupported range type {range_type!r}"
            )
        required_keys = ("lb", "ub") if range_type == 0 else ("val", "err")
        if set(range_value) != set(required_keys):
            raise PlatformBatchError(
                f"{path} must contain exactly range keys {required_keys}"
            )
        for key in required_keys:
            _validate_number(range_value[key], f"{path}.{key}")
        if range_type == 0 and float(range_value["lb"]) > float(range_value["ub"]):
            raise PlatformBatchError(f"{path}.lb must not exceed {path}.ub")
        return
    if field_type in {4, 5}:
        _validate_attachment(value, schema, path)
        return
    if field_type == 6:
        if not isinstance(value, str) or not value.strip():
            raise PlatformBatchError(f"{path} must be a candidate string")
        allowed = _allowed_candidate_values(schema)
        if value not in allowed:
            raise PlatformBatchError(
                f"{path}={value!r} is not in the platform candidates: "
                + ", ".join(sorted(allowed))
            )
        return
    if field_type == 7:
        rows = _require_list(value, path)
        container = _require_mapping(schema.get("misc"), f"{path} schema")
        if container.get("t") != 9:
            raise PlatformBatchError(f"{path} array item schema must be t=9")
        item_blocks = _require_mapping(
            container.get("misc"), f"{path} item schema"
        )
        for index, row in enumerate(rows):
            _validate_nested_payload(
                row,
                item_blocks,
                order_key="_ord",
                path=f"{path}[{index}]",
            )
        return
    if field_type == 8:
        rows = _require_list(value, path)
        columns = _require_mapping(schema.get("misc"), f"{path} table schema")
        for index, row in enumerate(rows):
            _validate_nested_payload(
                row,
                columns,
                order_key="_head",
                path=f"{path}[{index}]",
            )
        return
    if field_type == 9:
        blocks = _require_mapping(schema.get("misc"), f"{path} container schema")
        _validate_nested_payload(
            value,
            blocks,
            order_key="_ord",
            path=path,
        )
        return
    raise PlatformBatchError(f"{path} uses unsupported platform field type {field_type}")


def _section_index(
    sections: Any,
    path: str,
) -> dict[str, Mapping[str, Any]]:
    items = _require_list(sections, path)
    result: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(items):
        section = _require_mapping(item, f"{path}[{index}]")
        section_id = str(section.get("id") or "").strip()
        if not section_id:
            raise PlatformBatchError(f"{path}[{index}].id is required")
        if section_id in result:
            raise PlatformBatchError(f"{path} contains duplicate id {section_id}")
        result[section_id] = section
    return result


def validate_platform_batch(batch: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a generated batch against its embedded platform schema."""
    binding = validate_platform_binding(batch)
    template = _require_mapping(batch.get("template"), "batch.template")
    object_template = _require_mapping(template.get("object"), "batch.template.object")
    object_blocks = _require_mapping(
        object_template.get("blocks"), "batch.template.object.blocks"
    )
    operation_templates = _section_index(
        template.get("operations"), "batch.template.operations"
    )
    result_templates = _section_index(
        template.get("results"), "batch.template.results"
    )
    records = _require_list(batch.get("data"), "batch.data")
    if not records:
        raise PlatformBatchError("batch.data must contain at least one record")

    data_ids: set[str] = set()
    record_keys: set[str] = set()
    for index, raw_record in enumerate(records):
        record = _require_mapping(raw_record, f"batch.data[{index}]")
        meta = _require_mapping(record.get("meta"), f"batch.data[{index}].meta")
        data_id = str(meta.get("数据ID") or "").strip()
        if not data_id:
            raise PlatformBatchError(f"batch.data[{index}].meta.数据ID is required")
        if data_id in data_ids:
            raise PlatformBatchError(f"duplicate meta 数据ID: {data_id}")
        data_ids.add(data_id)

        content = _require_mapping(
            record.get("content"), f"batch.data[{index}].content"
        )
        object_payload = _require_mapping(
            content.get("object"), f"batch.data[{index}].content.object"
        )
        _validate_nested_payload(
            object_payload,
            object_blocks,
            order_key="_ord",
            path=f"batch.data[{index}].content.object",
        )
        record_key = str(object_payload.get("数据记录键") or "")
        if record_key in record_keys:
            raise PlatformBatchError(f"duplicate 数据记录键: {record_key}")
        record_keys.add(record_key)

        operation_payloads = _section_index(
            content.get("operations"),
            f"batch.data[{index}].content.operations",
        )
        if set(operation_payloads) != set(operation_templates):
            raise PlatformBatchError(
                f"batch.data[{index}].content.operations ids do not match template"
            )
        for section_id, payload in operation_payloads.items():
            section = operation_templates[section_id]
            blocks = _require_mapping(
                section.get("blocks"), f"operation template {section_id}.blocks"
            )
            nested = {key: value for key, value in payload.items() if key != "id"}
            _validate_nested_payload(
                nested,
                blocks,
                order_key="_ord",
                path=f"batch.data[{index}].content.operations[{section_id}]",
            )

        result_payloads = _section_index(
            content.get("results"),
            f"batch.data[{index}].content.results",
        )
        if set(result_payloads) != set(result_templates):
            raise PlatformBatchError(
                f"batch.data[{index}].content.results ids do not match template"
            )
        for section_id, payload in result_payloads.items():
            section = result_templates[section_id]
            blocks = _require_mapping(
                section.get("blocks"), f"result template {section_id}.blocks"
            )
            nested = {key: value for key, value in payload.items() if key != "id"}
            _validate_nested_payload(
                nested,
                blocks,
                order_key="_ord",
                path=f"batch.data[{index}].content.results[{section_id}]",
            )

    return {
        **binding,
        "record_count": len(records),
        "unique_data_id_count": len(data_ids),
        "unique_record_key_count": len(record_keys),
    }


def dumps_platform_batch(batch: Mapping[str, Any], *, indent: int = 2) -> str:
    """Serialize without ever coercing large platform IDs to floating point."""
    validate_platform_batch(batch)
    return json.dumps(batch, ensure_ascii=False, indent=indent) + "\n"


def _load_projections(paths: Sequence[Path]) -> list[Mapping[str, Any]]:
    projections: list[Mapping[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            projections.extend(
                _require_mapping(item, f"{path}[{index}]")
                for index, item in enumerate(payload)
            )
        elif isinstance(payload, Mapping) and isinstance(
            payload.get("projections"), list
        ):
            projections.extend(
                _require_mapping(item, f"{path}.projections[{index}]")
                for index, item in enumerate(payload["projections"])
            )
        else:
            projections.append(_require_mapping(payload, str(path)))
    return projections


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Convert chemical_fiber_projection_v1 JSON into the platform's "
            "downloaded batch-upload JSON format."
        )
    )
    parser.add_argument(
        "--batch-template",
        type=Path,
        required=True,
        help="JSON downloaded from the target dataset's batch-upload page",
    )
    parser.add_argument(
        "--projection",
        type=Path,
        action="append",
        required=True,
        help="AI4S projection JSON; repeat for multiple files",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--exported-at",
        help="Optional audit timestamp written verbatim to 导出时间",
    )
    args = parser.parse_args(argv)

    batch_template = _require_mapping(
        json.loads(args.batch_template.read_text(encoding="utf-8")),
        str(args.batch_template),
    )
    projections = _load_projections(args.projection)
    batch = build_platform_batch(
        batch_template,
        projections,
        exported_at=args.exported_at,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(dumps_platform_batch(batch), encoding="utf-8")
    summary = validate_platform_batch(batch)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
