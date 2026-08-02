"""Ordered material-chain adapter for the New Materials Data Center.

The platform's native Excel exporter ignores ``_ord`` for sibling blocks and
iterates them through a Java ``HashMap``.  The verified v0.3.2 schema avoids
that failure mode by using exactly one ordered ``t=9`` group in each platform
section:

``文献、样品与成分`` → ``工艺`` → ``结构、性能与来源``.

Each platform record represents one actual material sample.  The fields inside
the three groups follow the user's original research workbook order and exclude
extraction, mapping, upload, and review-pipeline bookkeeping.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable, Mapping
from typing import Any


MATERIAL_CHAIN_SCHEMA_VERSION = "ai4s_material_chain_v0.3.2"
JAVASCRIPT_MAX_SAFE_INTEGER = 9_007_199_254_740_991

OBJECT_GROUP = "文献、样品与成分"
PROCESS_GROUP = "工艺"
RESULT_GROUP = "结构、性能与来源"

LOCAL_OBJECT_FIELDS = (
    "数据ID",
    "文献编号",
    "论文题目",
    "DOI|URL",
    "年份",
    "期刊|会议",
    "材料类别",
    "具体材料对象|样品编号",
    "纤维形态",
    "原料|前驱体|基体",
    "增强|填料|改性组分",
    "成分配比|浓度",
    "溶剂|助剂",
)
LOCAL_PROCESS_FIELDS = (
    "工艺路线",
    "关键工艺参数",
    "后处理条件",
)
LOCAL_STRUCTURE_FIELDS = (
    "结构表征方法",
    "结构指标名称",
    "结构数值",
    "结构单位",
)
LOCAL_PERFORMANCE_FIELDS = (
    "性能测试方法|标准",
    "性能指标名称",
    "性能数值",
    "性能单位",
    "测试条件",
)
LOCAL_EVIDENCE_FIELDS = (
    "结果描述|结论",
    "数据来源位置",
    "原文图表编号",
    "是否完整",
    "缺失信息说明",
    "备注",
)
LOCAL_RESULT_FIELDS = (
    *LOCAL_STRUCTURE_FIELDS,
    *LOCAL_PERFORMANCE_FIELDS,
    *LOCAL_EVIDENCE_FIELDS,
)
LOCAL_FIELD_ORDER = (
    *LOCAL_OBJECT_FIELDS,
    *LOCAL_PROCESS_FIELDS,
    *LOCAL_RESULT_FIELDS,
)

LOCAL_TO_PLATFORM = {
    "数据ID": "数据ID",
    "文献编号": "文献编号",
    "论文题目": "论文题目",
    "DOI|URL": "DOI或URL",
    "年份": "年份",
    "期刊|会议": "期刊或会议",
    "材料类别": "材料类别",
    "具体材料对象|样品编号": "具体材料对象或样品编号",
    "纤维形态": "纤维形态",
    "原料|前驱体|基体": "原料、前驱体或基体",
    "增强|填料|改性组分": "增强、填料或改性组分",
    "成分配比|浓度": "成分配比或浓度",
    "溶剂|助剂": "溶剂或助剂",
    "工艺路线": "工艺路线",
    "关键工艺参数": "关键工艺参数",
    "后处理条件": "后处理条件",
    "结构表征方法": "结构表征方法",
    "结构指标名称": "结构指标名称",
    "结构数值": "结构数值",
    "结构单位": "结构单位",
    "性能测试方法|标准": "性能测试方法或标准",
    "性能指标名称": "性能指标名称",
    "性能数值": "性能数值",
    "性能单位": "性能单位",
    "测试条件": "测试条件",
    "结果描述|结论": "结果描述或结论",
    "数据来源位置": "数据来源位置",
    "原文图表编号": "原文图表编号",
    "是否完整": "是否完整",
    "缺失信息说明": "缺失信息说明",
    "备注": "备注",
}

PLATFORM_OBJECT_FIELDS = tuple(
    LOCAL_TO_PLATFORM[name] for name in LOCAL_OBJECT_FIELDS
)
PLATFORM_PROCESS_FIELDS = tuple(
    LOCAL_TO_PLATFORM[name] for name in LOCAL_PROCESS_FIELDS
)
PLATFORM_RESULT_FIELDS = tuple(
    LOCAL_TO_PLATFORM[name] for name in LOCAL_RESULT_FIELDS
)

FORBIDDEN_BUSINESS_FIELDS = {
    "学生姓名",
    "分工方向",
    "提取方式",
    "清洗状态",
    "质检人",
    "质检意见",
    "完整性评分",
    "投影版本",
    "数据状态",
    "映射版本",
    "源数据签名",
    "导出时间",
    "已映射值数量",
    "未映射事实数量",
    "抽取状态",
    "审核流水",
}


class MaterialChainAdapterError(ValueError):
    """Raised when the v0.3.2 template or batch violates the pinned schema."""


def validate_material_chain_template(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the exact exporter-safe v0.3.2 platform template."""

    root = _mapping(payload, "template root")
    template = _mapping(root.get("template"), "template")
    template_id = _positive_json_integer(template.get("_id"), "template._id")

    object_section = _mapping(template.get("object"), "template.object")
    if object_section.get("id") != "object":
        raise MaterialChainAdapterError("template.object.id must be 'object'")
    _validate_group_section(
        object_section,
        section_name="object",
        expected_group=OBJECT_GROUP,
        expected_fields=PLATFORM_OBJECT_FIELDS,
    )

    operations = _sequence(template.get("operations"), "template.operations")
    if len(operations) != 1:
        raise MaterialChainAdapterError(
            "template.operations must contain exactly operation1"
        )
    operation = _mapping(operations[0], "template.operations[0]")
    if operation.get("id") != "operation1":
        raise MaterialChainAdapterError(
            "template.operations[0].id must be 'operation1'"
        )
    _validate_group_section(
        operation,
        section_name="operation1",
        expected_group=PROCESS_GROUP,
        expected_fields=PLATFORM_PROCESS_FIELDS,
    )

    results = _sequence(template.get("results"), "template.results")
    if len(results) != 1:
        raise MaterialChainAdapterError(
            "template.results must contain exactly result1"
        )
    result = _mapping(results[0], "template.results[0]")
    if result.get("id") != "result1":
        raise MaterialChainAdapterError(
            "template.results[0].id must be 'result1'"
        )
    _validate_group_section(
        result,
        section_name="result1",
        expected_group=RESULT_GROUP,
        expected_fields=PLATFORM_RESULT_FIELDS,
    )

    all_fields = (
        *PLATFORM_OBJECT_FIELDS,
        *PLATFORM_PROCESS_FIELDS,
        *PLATFORM_RESULT_FIELDS,
    )
    forbidden = sorted(set(all_fields) & FORBIDDEN_BUSINESS_FIELDS)
    if forbidden:
        raise MaterialChainAdapterError(
            "template contains extraction bookkeeping fields: "
            + ", ".join(forbidden)
        )
    if len(set(all_fields)) != len(all_fields):
        raise MaterialChainAdapterError(
            "platform field names must be unique across the material chain"
        )
    return {
        "schema_version": MATERIAL_CHAIN_SCHEMA_VERSION,
        "template_id": template_id,
        "field_count": len(all_fields),
        "ids_exceed_javascript_safe_integer": (
            template_id > JAVASCRIPT_MAX_SAFE_INTEGER
        ),
    }


def build_material_chain_batch(
    template_payload: Mapping[str, Any],
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build one ordered platform record per actual sample."""

    validate_material_chain_template(template_payload)
    root = _mapping(template_payload, "template root")
    dataset = _mapping(root.get("dataset"), "dataset")
    dataset_id = _positive_json_integer(dataset.get("_id"), "dataset._id")

    template = copy.deepcopy(_mapping(root.get("template"), "template"))
    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_row in enumerate(rows):
        row = _mapping(raw_row, f"rows[{index}]")
        data_id = _text(row.get("数据ID"))
        sample_id = _text(row.get("具体材料对象|样品编号"))
        if not data_id:
            raise MaterialChainAdapterError(
                f"rows[{index}].数据ID must be non-empty"
            )
        if not sample_id:
            raise MaterialChainAdapterError(
                f"rows[{index}].具体材料对象|样品编号 must be non-empty"
            )
        if data_id in seen_ids:
            raise MaterialChainAdapterError(
                f"duplicate 数据ID in material chain: {data_id}"
            )
        seen_ids.add(data_id)

        platform_row = {
            LOCAL_TO_PLATFORM[name]: _text(row.get(name))
            for name in LOCAL_FIELD_ORDER
            if _text(row.get(name))
        }
        records.append(
            {
                "meta": {"数据ID": data_id},
                "content": {
                    "object": {
                        OBJECT_GROUP: _pick(
                            platform_row,
                            PLATFORM_OBJECT_FIELDS,
                        )
                    },
                    "operations": [
                        {
                            "id": "operation1",
                            PROCESS_GROUP: _pick(
                                platform_row,
                                PLATFORM_PROCESS_FIELDS,
                            ),
                        }
                    ],
                    "results": [
                        {
                            "id": "result1",
                            RESULT_GROUP: _pick(
                                platform_row,
                                PLATFORM_RESULT_FIELDS,
                            ),
                        }
                    ],
                },
            }
        )

    if not records:
        raise MaterialChainAdapterError(
            "material chain batch must contain at least one sample"
        )
    batch = {
        "dataset": copy.deepcopy(dataset),
        "template": template,
        "data": records,
    }
    summary = validate_material_chain_batch(batch)
    summary["dataset_id"] = dataset_id
    return batch


def validate_material_chain_batch(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail closed on malformed or reordered v0.3.2 delivery records."""

    root = _mapping(payload, "batch")
    dataset = _mapping(root.get("dataset"), "batch.dataset")
    template = _mapping(root.get("template"), "batch.template")
    dataset_id = _positive_json_integer(dataset.get("_id"), "dataset._id")
    template_id = _positive_json_integer(template.get("_id"), "template._id")
    validate_material_chain_template({"template": template})

    data = _sequence(root.get("data"), "batch.data")
    if not data:
        raise MaterialChainAdapterError("batch.data must not be empty")
    seen: set[str] = set()
    for index, item in enumerate(data):
        record = _mapping(item, f"batch.data[{index}]")
        meta = _mapping(record.get("meta"), f"batch.data[{index}].meta")
        data_id = _text(meta.get("数据ID"))
        if not data_id or data_id in seen:
            raise MaterialChainAdapterError(
                f"batch.data[{index}] has missing or duplicate 数据ID"
            )
        seen.add(data_id)

        content = _mapping(
            record.get("content"),
            f"batch.data[{index}].content",
        )
        object_payload = _mapping(
            content.get("object"),
            f"batch.data[{index}].content.object",
        )
        object_group = _mapping(
            object_payload.get(OBJECT_GROUP),
            f"batch.data[{index}].content.object.{OBJECT_GROUP}",
        )
        _validate_payload_fields(
            object_group,
            PLATFORM_OBJECT_FIELDS,
            f"batch.data[{index}].object",
        )
        if _text(object_group.get("数据ID")) != data_id:
            raise MaterialChainAdapterError(
                f"batch.data[{index}] meta/object 数据ID mismatch"
            )
        if not _text(object_group.get("具体材料对象或样品编号")):
            raise MaterialChainAdapterError(
                f"batch.data[{index}] has no sample identifier"
            )

        operations = _sequence(
            content.get("operations"),
            f"batch.data[{index}].content.operations",
        )
        if len(operations) != 1:
            raise MaterialChainAdapterError(
                f"batch.data[{index}] must contain exactly operation1"
            )
        operation = _mapping(operations[0], f"batch.data[{index}].operation1")
        if operation.get("id") != "operation1":
            raise MaterialChainAdapterError(
                f"batch.data[{index}] operation id mismatch"
            )
        _validate_payload_fields(
            _mapping(
                operation.get(PROCESS_GROUP),
                f"batch.data[{index}].operation1.{PROCESS_GROUP}",
            ),
            PLATFORM_PROCESS_FIELDS,
            f"batch.data[{index}].operation1",
        )

        results = _sequence(
            content.get("results"),
            f"batch.data[{index}].content.results",
        )
        if len(results) != 1:
            raise MaterialChainAdapterError(
                f"batch.data[{index}] must contain exactly result1"
            )
        result = _mapping(results[0], f"batch.data[{index}].result1")
        if result.get("id") != "result1":
            raise MaterialChainAdapterError(
                f"batch.data[{index}] result id mismatch"
            )
        _validate_payload_fields(
            _mapping(
                result.get(RESULT_GROUP),
                f"batch.data[{index}].result1.{RESULT_GROUP}",
            ),
            PLATFORM_RESULT_FIELDS,
            f"batch.data[{index}].result1",
        )

    return {
        "schema_version": MATERIAL_CHAIN_SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "template_id": template_id,
        "record_count": len(data),
        "ids_exceed_javascript_safe_integer": (
            dataset_id > JAVASCRIPT_MAX_SAFE_INTEGER
            or template_id > JAVASCRIPT_MAX_SAFE_INTEGER
        ),
    }


def dumps_material_chain_batch(payload: Mapping[str, Any]) -> str:
    """Serialize without rounding the platform's 19-digit integer IDs."""

    validate_material_chain_batch(payload)
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ) + "\n"


def _validate_group_section(
    section: Mapping[str, Any],
    *,
    section_name: str,
    expected_group: str,
    expected_fields: tuple[str, ...],
) -> None:
    blocks = _mapping(section.get("blocks"), f"{section_name}.blocks")
    order = _sequence(blocks.get("_ord"), f"{section_name}.blocks._ord")
    if tuple(order) != (expected_group,):
        raise MaterialChainAdapterError(
            f"{section_name}.blocks._ord must be [{expected_group!r}]"
        )
    group = _mapping(
        blocks.get(expected_group),
        f"{section_name}.blocks.{expected_group}",
    )
    if group.get("t") != 9:
        raise MaterialChainAdapterError(
            f"{section_name}.{expected_group} must use t=9"
        )
    misc = _mapping(
        group.get("misc"),
        f"{section_name}.{expected_group}.misc",
    )
    fields = _sequence(
        misc.get("_ord"),
        f"{section_name}.{expected_group}.misc._ord",
    )
    if tuple(fields) != expected_fields:
        raise MaterialChainAdapterError(
            f"{section_name}.{expected_group} field order mismatch"
        )
    allowed = {"_ord", *expected_fields}
    unknown = sorted(set(misc) - allowed)
    if unknown:
        raise MaterialChainAdapterError(
            f"{section_name}.{expected_group} has unknown fields: "
            + ", ".join(unknown)
        )
    for field in expected_fields:
        descriptor = _mapping(
            misc.get(field),
            f"{section_name}.{expected_group}.{field}",
        )
        if descriptor.get("t") != 1:
            raise MaterialChainAdapterError(
                f"{section_name}.{expected_group}.{field} must use t=1"
            )


def _validate_payload_fields(
    payload: Mapping[str, Any],
    allowed_fields: tuple[str, ...],
    label: str,
) -> None:
    unknown = sorted(set(payload) - set(allowed_fields))
    if unknown:
        raise MaterialChainAdapterError(
            f"{label} has unknown fields: " + ", ".join(unknown)
        )
    for field, value in payload.items():
        if isinstance(value, (dict, list)):
            raise MaterialChainAdapterError(
                f"{label}.{field} must be a scalar text value"
            )


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MaterialChainAdapterError(f"{label} must be an object")
    return value


def _sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise MaterialChainAdapterError(f"{label} must be an array")
    return value


def _positive_json_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MaterialChainAdapterError(
            f"{label} must be a positive JSON integer; do not parse it as float"
        )
    return value


def _pick(
    row: Mapping[str, str],
    field_order: tuple[str, ...],
) -> dict[str, str]:
    return {field: row[field] for field in field_order if row.get(field)}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value).strip()
