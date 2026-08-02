"""Contract tests for the AI4S-to-platform batch JSON adapter."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.services.platform_batch_adapter import (
    PLATFORM_BINDING_VERSION,
    PlatformBatchError,
    build_platform_batch,
    main,
    validate_platform_batch,
    validate_platform_binding,
)


DATASET_ID = 1_955_774_416_894_095_361
TEMPLATE_ID = 1_955_774_416_894_095_363
ENTITY_KEY = "paper:73:sample:S-01"
EXTRACTION_STATUSES = [
    "已抽取",
    "已验证",
    "待复核",
    "抽取中",
    "未报告",
    "不适用",
]
REVIEW_STATUSES = ["待审核", "已修改", "通过", "存疑", "缺失", "已删除"]


def _field(
    field_type: int,
    *,
    required: bool = False,
    options: list[str] | None = None,
) -> dict[str, Any]:
    misc: dict[str, Any] = {}
    if field_type == 3:
        misc["type"] = 0
    if options is not None:
        misc.update({"opt": options, "grp": []})
    return {"r": required, "t": field_type, "misc": misc, "stats": "0"}


def _blocks(fields: dict[str, dict[str, Any]], order_key: str = "_ord"):
    return {order_key: list(fields), **fields}


def _array_field(fields: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "r": False,
        "t": 7,
        "misc": {
            "r": False,
            "t": 9,
            "misc": _blocks(fields),
            "stats": "0",
        },
        "stats": "0",
    }


def _table_field(fields: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "r": False,
        "t": 8,
        "misc": _blocks(fields, "_head"),
        "stats": "0",
    }


def _container_field(fields: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "r": False,
        "t": 9,
        "misc": _blocks(fields),
        "stats": "0",
    }


@pytest.fixture
def platform_batch_template() -> dict[str, Any]:
    object_names = [
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
    ]
    object_fields = {name: _field(1) for name in object_names}
    for required_name in ("数据记录键", "投影版本", "文献编号"):
        object_fields[required_name]["r"] = True
    object_fields["数据状态"] = _field(
        6,
        required=True,
        options=EXTRACTION_STATUSES,
    )
    object_fields["发表年份"] = _field(2)

    operation1_fields = {
        name: _field(1)
        for name in (
            "工艺路线",
            "纺丝方法",
            "工艺参数摘要",
            "后处理摘要",
            "成分证据",
            "工艺证据",
        )
    }
    operation2_fields = {
        "记录审核状态": _field(6, required=True, options=REVIEW_STATUSES),
        "原始论文PDF": {
            **_field(5),
            "misc": {"fileFormats": [".pdf"]},
        },
        "补充材料或原始测试文件": {
            **_field(5),
            "misc": {"fileFormats": [".pdf", ".csv", ".json"]},
        },
        "关键图像": {
            **_field(4),
            "misc": {"imageFormats": [".png", ".jpg"]},
        },
    }

    composition_fields = {
        "组分角色": _field(1, required=True),
        "组分名称": _field(1, required=True),
        "含量原始值": _field(1, required=True),
        "含量数值": _field(2),
        "含量范围": _field(3),
        "成分单位": _field(1),
        "条件或说明": _field(1),
        "成分证据原文": _field(1),
        "成分来源位置": _field(1),
        "成分置信度": _field(2),
        "成分抽取状态": _field(6, required=True, options=EXTRACTION_STATUSES),
    }
    process_fields = {
        "工艺阶段": _field(1, required=True),
        "参数名称": _field(1, required=True),
        "工艺参数原始值": _field(1, required=True),
        "工艺参数数值": _field(2),
        "工艺参数范围": _field(3),
        "工艺参数单位": _field(1),
        "方法或设备": _field(1),
        "工艺条件": _field(1),
        "工艺证据原文": _field(1),
        "工艺来源位置": _field(1),
        "工艺置信度": _field(2),
        "工艺抽取状态": _field(6, required=True, options=EXTRACTION_STATUSES),
    }
    structure_fields = {
        "结构指标名称": _field(1, required=True),
        "结构原始值": _field(1, required=True),
        "结构数值": _field(2),
        "结构范围": _field(3),
        "结构单位": _field(1),
        "表征方法": _field(1),
        "结构测试条件": _field(1),
        "结构证据原文": _field(1),
        "结构来源位置": _field(1),
        "结构置信度": _field(2),
        "结构抽取状态": _field(6, required=True, options=EXTRACTION_STATUSES),
    }
    performance_fields = {
        "性能类别": _field(1, required=True),
        "性能指标名称": _field(1, required=True),
        "性能原始值": _field(1, required=True),
        "性能数值": _field(2),
        "性能范围": _field(3),
        "性能单位": _field(1),
        "性能测试方法": _field(1),
        "性能测试条件": _field(1),
        "性能证据原文": _field(1),
        "性能来源位置": _field(1),
        "性能置信度": _field(2),
        "性能抽取状态": _field(6, required=True, options=EXTRACTION_STATUSES),
        "性能审核状态": _field(6, required=True, options=REVIEW_STATUSES),
        "性能审核意见": _field(1),
    }
    unmapped_fields = {
        "事实类型": _field(1, required=True),
        "长尾指标或参数名称": _field(1, required=True),
        "长尾原始值": _field(1, required=True),
        "长尾单位": _field(1),
        "长尾方法": _field(1),
        "长尾条件": _field(1),
        "长尾样品编号": _field(1),
        "长尾证据原文": _field(1),
        "长尾来源位置": _field(1),
        "长尾置信度": _field(2),
        "长尾抽取状态": _field(6, required=True, options=EXTRACTION_STATUSES),
    }
    quality_fields = {
        "抽取方式": _field(1, required=True),
        "映射版本": _field(1, required=True),
        "源数据签名": _field(1, required=True),
        "证据覆盖率": _field(2, required=True),
        "已映射值数量": _field(2, required=True),
        "未映射事实数量": _field(2, required=True),
        "待复核数量": _field(2, required=True),
        "导出时间": _field(1),
        "质量审核意见": _field(1),
    }

    return {
        "dataset": {
            "_id": DATASET_ID,
            "name": "AI4S私有兼容性测试数据集",
        },
        "template": {
            "_id": TEMPLATE_ID,
            "name": "文献数据抽取",
            "object": {
                "id": "object",
                "blocks": _blocks(object_fields),
            },
            "operations": [
                {
                    "id": "operation1",
                    "blocks": _blocks(operation1_fields),
                },
                {
                    "id": "operation2",
                    "blocks": _blocks(operation2_fields),
                },
            ],
            "results": [
                {
                    "id": "result1",
                    "blocks": _blocks(
                        {"成分与配方明细": _array_field(composition_fields)}
                    ),
                },
                {
                    "id": "result2",
                    "blocks": _blocks(
                        {"工艺参数明细": _array_field(process_fields)}
                    ),
                },
                {
                    "id": "result3",
                    "blocks": _blocks(
                        {"结构测试结果": _array_field(structure_fields)}
                    ),
                },
                {
                    "id": "result4",
                    "blocks": _blocks(
                        {"性能测试结果": _array_field(performance_fields)}
                    ),
                },
                {
                    "id": "result5",
                    "blocks": _blocks(
                        {
                            "其他未映射指标": _table_field(unmapped_fields),
                            "数据质量与溯源": _container_field(quality_fields),
                        }
                    ),
                },
            ],
        },
        # The platform download contains an example record. It is not user data
        # and must never leak into the generated upload.
        "data": [
            {
                "meta": {"数据ID": "下载模板示例"},
                "content": {"默认字段": ["xxx_item_1"]},
            }
        ],
    }


@pytest.fixture
def chemical_fiber_projection() -> dict[str, Any]:
    sample_id_value = {
        "entity_type": "fiber_sample",
        "entity_key": ENTITY_KEY,
        "field_path": "fiber_sample.identity.sample_id",
        "field_label": "样品编号",
        "raw_value": "S-01",
        "mapping_status": "mapped",
        "status": "verified",
        "evidence": {
            "evidence_text": "The specimen was labelled S-01.",
            "source_page": 2,
            "source_block_id": "sample-block",
        },
    }
    performance_range = {
        "entity_type": "fiber_sample",
        "entity_key": ENTITY_KEY,
        "field_path": "fiber_sample.performance.mechanical.tensile_strength",
        "field_label": "拉伸强度",
        "raw_value": "100–120 MPa",
        "range_min": 100.0,
        "range_max": 120.0,
        "unit": "MPa",
        "method": "single-fibre tensile test",
        "condition": "23 °C, 50% RH",
        "confidence": 0.91,
        "mapping_status": "mapped",
        "status": "needs_review",
        "evidence": {
            "evidence_text": "S-01 showed a tensile strength of 100–120 MPa.",
            "source_page": 7,
            "source_block_id": "table-2",
        },
    }
    unmapped = {
        "entity_type": "fiber_sample",
        "entity_key": ENTITY_KEY,
        "field_path": "fiber_sample.performance.unmapped.whisker_index",
        "field_label": "whisker instability index",
        "raw_value": "2.4",
        "value_number": 2.4,
        "unit": "a.u.",
        "method": "custom image analysis",
        "condition": "dry",
        "confidence": 0.82,
        "mapping_status": "unmapped",
        "status": "extracted",
        "evidence": {
            "evidence_text": "The whisker instability index was 2.4.",
            "source_location": "p.8, Fig. 5",
        },
    }
    return {
        "schema_version": "chemical_fiber_projection_v1",
        "paper": {
            "paper_id": 73,
            "original_filename": "fiber-paper.pdf",
        },
        "entities": [
            {
                "entity_type": "paper",
                "entity_key": "paper:73",
                "paper_id": 73,
            },
            {
                "entity_type": "fiber_sample",
                "entity_key": ENTITY_KEY,
                "sample_id": "S-01",
                "sample_group_id": "G-01",
            }
        ],
        "values": [
            {
                "entity_type": "paper",
                "entity_key": "paper:73",
                "field_path": "paper.metadata.title",
                "field_label": "文献标题",
                "raw_value": "Evidence-grounded fibre paper",
                "mapping_status": "mapped",
                "status": "verified",
            },
            {
                "entity_type": "paper",
                "entity_key": "paper:73",
                "field_path": "paper.metadata.year",
                "field_label": "发表年份",
                "raw_value": "2026",
                "value_number": 2026,
                "mapping_status": "mapped",
                "status": "verified",
            },
            sample_id_value,
            performance_range,
            unmapped,
        ],
    }


def _result(record: dict[str, Any], section_id: str) -> dict[str, Any]:
    return next(
        section
        for section in record["content"]["results"]
        if section["id"] == section_id
    )


def test_builds_natural_platform_shapes_with_evidence_and_quality(
    platform_batch_template: dict[str, Any],
    chemical_fiber_projection: dict[str, Any],
):
    binding = validate_platform_binding(platform_batch_template)
    assert binding == {
        "dataset_id": DATASET_ID,
        "template_id": TEMPLATE_ID,
        "ids_exceed_javascript_safe_integer": True,
    }

    batch = build_platform_batch(
        platform_batch_template,
        [chemical_fiber_projection],
        exported_at="2026-07-27T17:00:00+08:00",
    )

    assert batch["dataset"] == platform_batch_template["dataset"]
    assert batch["template"] == platform_batch_template["template"]
    assert len(batch["data"]) == 1
    assert batch["data"][0]["meta"]["数据ID"] != "下载模板示例"

    record = batch["data"][0]
    object_payload = record["content"]["object"]
    assert object_payload["文献标题"] == "Evidence-grounded fibre paper"
    assert object_payload["发表年份"] == 2026
    assert object_payload["样品编号"] == "S-01"
    assert object_payload["样品组编号"] == "G-01"
    assert object_payload["数据状态"] == "待复核"

    performance_rows = _result(record, "result4")["性能测试结果"]
    assert isinstance(performance_rows, list)
    assert performance_rows == [
        {
            "性能类别": "力学性能",
            "性能指标名称": "拉伸强度",
            "性能原始值": "100–120 MPa",
            "性能范围": {"lb": 100, "ub": 120},
            "性能单位": "MPa",
            "性能测试方法": "single-fibre tensile test",
            "性能测试条件": "23 °C, 50% RH",
            "性能证据原文": "S-01 showed a tensile strength of 100–120 MPa.",
            "性能来源位置": "p.7; block:table-2",
            "性能置信度": 0.91,
            "性能抽取状态": "待复核",
            "性能审核状态": "存疑",
        }
    ]

    unmapped_rows = _result(record, "result5")["其他未映射指标"]
    assert isinstance(unmapped_rows, list)
    assert unmapped_rows[0] == {
        "事实类型": "性能",
        "长尾指标或参数名称": "whisker instability index",
        "长尾原始值": "2.4",
        "长尾单位": "a.u.",
        "长尾方法": "custom image analysis",
        "长尾条件": "dry",
        "长尾样品编号": "S-01",
        "长尾证据原文": "The whisker instability index was 2.4.",
        "长尾来源位置": "p.8, Fig. 5",
        "长尾置信度": 0.82,
        "长尾抽取状态": "已抽取",
    }

    quality = _result(record, "result5")["数据质量与溯源"]
    assert quality["映射版本"] == PLATFORM_BINDING_VERSION
    assert quality["证据覆盖率"] == 1
    assert quality["已映射值数量"] == 2
    assert quality["未映射事实数量"] == 1
    assert quality["待复核数量"] == 1
    assert quality["导出时间"] == "2026-07-27T17:00:00+08:00"
    assert quality["质量审核意见"] == "含待复核或未映射事实，需人工复核"
    expected_signature = hashlib.sha256(
        json.dumps(
            chemical_fiber_projection,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert quality["源数据签名"] == expected_signature

    expected_digest = hashlib.sha256(
        json.dumps(
            {
                "schema_version": "chemical_fiber_projection_v1",
                "paper_id": 73,
                "entity_key": ENTITY_KEY,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:24]
    assert record["meta"]["数据ID"] == f"data-{expected_digest}"
    assert object_payload["数据记录键"] == f"AI4S-{expected_digest}"
    assert validate_platform_batch(batch)["record_count"] == 1

    rebuilt = build_platform_batch(
        platform_batch_template,
        [chemical_fiber_projection],
        exported_at="a later audit timestamp",
    )
    assert rebuilt["data"][0]["meta"] == record["meta"]
    assert (
        rebuilt["data"][0]["content"]["object"]["数据记录键"]
        == object_payload["数据记录键"]
    )


def test_preserves_scalar_conflicts_and_audits_pending_facts(
    platform_batch_template: dict[str, Any],
    chemical_fiber_projection: dict[str, Any],
):
    projection = copy.deepcopy(chemical_fiber_projection)
    projection["values"] = [
        value
        for value in projection["values"]
        if value["field_path"]
        in {
            "paper.metadata.title",
            "fiber_sample.identity.sample_id",
        }
    ]
    projection["values"].extend(
        [
            {
                "entity_type": "fiber_sample",
                "entity_key": ENTITY_KEY,
                "field_path": "fiber_sample.composition.material_system",
                "field_label": "材料体系",
                "raw_value": "PAN",
                "mapping_status": "mapped",
                "status": "verified",
                "confidence": 0.8,
                "source_kind": "sample_catalog",
            },
            {
                "entity_type": "fiber_sample",
                "entity_key": ENTITY_KEY,
                "field_path": "fiber_sample.composition.material_system",
                "field_label": "材料体系",
                "raw_value": "PAN/CNT",
                "mapping_status": "mapped",
                "status": "extracted",
                "confidence": 0.99,
                "source_kind": "fact_candidate",
                "condition": "reported in a second passage",
                "evidence": {
                    "evidence_text": "A PAN/CNT composite was also reported.",
                    "source_location": "p.4",
                },
            },
        ]
    )
    projection["pending_facts"] = [
        {
            "fact_id": "F-PENDING",
            "fact_type": "performance",
            "metric_or_parameter": "modulus",
            "status": "needs_review",
            "sample_id": "S-01",
            "evidence": {
                "evidence_text": "The modulus was discussed without a value."
            },
        }
    ]

    batch = build_platform_batch(
        platform_batch_template,
        [projection],
    )

    record = batch["data"][0]["content"]
    assert record["object"]["材料体系"] == "PAN"
    assert record["object"]["数据状态"] == "待复核"
    assert record["operations"][1]["记录审核状态"] == "存疑"

    unmapped_rows = _result(
        batch["data"][0],
        "result5",
    )["其他未映射指标"]
    assert len(unmapped_rows) == 1
    assert unmapped_rows[0] == {
        "事实类型": "成分",
        "长尾指标或参数名称": "标量冲突候选：材料体系",
        "长尾原始值": "PAN/CNT",
        "长尾条件": (
            "reported in a second passage；"
            "同一标量字段存在多个不同候选值；主记录采用：PAN"
        ),
        "长尾样品编号": "S-01",
        "长尾证据原文": "A PAN/CNT composite was also reported.",
        "长尾来源位置": "p.4",
        "长尾置信度": 0.99,
        "长尾抽取状态": "待复核",
    }

    quality = _result(batch["data"][0], "result5")["数据质量与溯源"]
    assert quality["已映射值数量"] == 2
    assert quality["未映射事实数量"] == 1
    assert quality["待复核数量"] == 2
    assert quality["质量审核意见"] == (
        "含待复核或未映射事实，需人工复核；"
        "1 个待补值事实未写入数据行；"
        "1 个标量冲突候选已保留至长尾表"
    )
    assert validate_platform_batch(batch)["record_count"] == 1


def test_pending_fact_without_value_creates_unassigned_review_record(
    platform_batch_template: dict[str, Any],
):
    projection = {
        "schema_version": "chemical_fiber_projection_v1",
        "paper": {
            "paper_id": 99,
            "original_filename": "pending-only.pdf",
        },
        "entities": [
            {
                "entity_type": "paper",
                "entity_key": "paper:99",
            }
        ],
        "values": [],
        "pending_facts": [
            {
                "fact_id": "F-NO-VALUE",
                "fact_type": "structure",
                "metric_or_parameter": "crystallinity",
                "sample_id": None,
                "status": "needs_review",
            }
        ],
    }

    batch = build_platform_batch(platform_batch_template, [projection])

    assert len(batch["data"]) == 1
    content = batch["data"][0]["content"]
    assert content["object"]["文献编号"] == "99"
    assert content["object"]["数据状态"] == "待复核"
    assert content["operations"][1]["记录审核状态"] == "存疑"
    assert _result(batch["data"][0], "result3")["结构测试结果"] == []
    quality = _result(batch["data"][0], "result5")["数据质量与溯源"]
    assert quality["待复核数量"] == 1
    assert "1 个待补值事实未写入数据行" in quality["质量审核意见"]
    assert validate_platform_batch(batch)["record_count"] == 1


def test_real_canary_covers_all_four_detail_arrays():
    repository_root = Path(__file__).resolve().parents[2]
    batch_template = json.loads(
        (
            repository_root
            / "platform_templates/canary/platform-batch-canary.json"
        ).read_text(encoding="utf-8")
    )
    projection = json.loads(
        (
            repository_root
            / "platform_templates/canary/chemical-fiber-projection-canary.json"
        ).read_text(encoding="utf-8")
    )

    batch = build_platform_batch(batch_template, [projection])
    record = batch["data"][0]

    composition = _result(record, "result1")["成分与配方明细"]
    process = _result(record, "result2")["工艺参数明细"]
    structure = _result(record, "result3")["结构测试结果"]
    performance = _result(record, "result4")["性能测试结果"]
    assert composition[0]["组分角色"] == "聚合物"
    assert process[0]["工艺参数范围"] == {"lb": 260, "ub": 270}
    assert structure[0]["结构数值"] == 48
    assert performance[0]["性能范围"] == {"lb": 800, "ub": 900}
    assert validate_platform_batch(batch)["record_count"] == 1


def test_rejects_non_object_and_missing_raw_projection_values(
    platform_batch_template: dict[str, Any],
    chemical_fiber_projection: dict[str, Any],
):
    non_object = copy.deepcopy(chemical_fiber_projection)
    non_object["values"].append("not-an-object")
    with pytest.raises(PlatformBatchError, match="must be an object"):
        build_platform_batch(platform_batch_template, [non_object])

    missing_raw = copy.deepcopy(chemical_fiber_projection)
    performance = next(
        value
        for value in missing_raw["values"]
        if value["field_path"].endswith("tensile_strength")
    )
    performance.pop("raw_value")
    with pytest.raises(PlatformBatchError, match="must preserve a non-empty"):
        build_platform_batch(platform_batch_template, [missing_raw])

    unsupported_paper_field = copy.deepcopy(chemical_fiber_projection)
    unsupported_paper_field["values"].append(
        {
            "entity_type": "paper",
            "entity_key": "paper:73",
            "field_path": "paper.metadata.future_field",
            "raw_value": "must not disappear",
            "mapping_status": "unmapped",
            "status": "extracted",
        }
    )
    with pytest.raises(
        PlatformBatchError,
        match="unknown paper-level values cannot be dropped",
    ):
        build_platform_batch(
            platform_batch_template,
            [unsupported_paper_field],
        )


def test_unknown_internal_status_degrades_to_review(
    platform_batch_template: dict[str, Any],
    chemical_fiber_projection: dict[str, Any],
):
    projection = copy.deepcopy(chemical_fiber_projection)
    projection["values"] = [
        value
        for value in projection["values"]
        if value.get("mapping_status") != "unmapped"
    ]
    performance = next(
        value
        for value in projection["values"]
        if value["field_path"].endswith("tensile_strength")
    )
    performance["status"] = "future_unrecognized_status"

    batch = build_platform_batch(platform_batch_template, [projection])

    record = batch["data"][0]
    assert record["content"]["object"]["数据状态"] == "待复核"
    assert (
        _result(record, "result4")["性能测试结果"][0]["性能抽取状态"]
        == "待复核"
    )
    quality = _result(record, "result5")["数据质量与溯源"]
    assert quality["待复核数量"] == 1


def test_rejects_duplicate_pending_fact_ids(
    platform_batch_template: dict[str, Any],
    chemical_fiber_projection: dict[str, Any],
):
    projection = copy.deepcopy(chemical_fiber_projection)
    projection["pending_facts"] = [
        {
            "fact_id": "F-DUPLICATE",
            "status": "needs_review",
            "sample_id": "S-01",
        },
        {
            "fact_id": "F-DUPLICATE",
            "status": "needs_review",
            "sample_id": "S-01",
        },
    ]

    with pytest.raises(PlatformBatchError, match="duplicate fact_id"):
        build_platform_batch(platform_batch_template, [projection])


def test_rejects_unsafe_ranges_attachments_and_numeric_domains(
    platform_batch_template: dict[str, Any],
    chemical_fiber_projection: dict[str, Any],
):
    batch = build_platform_batch(
        platform_batch_template,
        [chemical_fiber_projection],
    )
    performance = _result(batch["data"][0], "result4")["性能测试结果"][0]
    performance["性能范围"]["unexpected"] = 999
    with pytest.raises(PlatformBatchError, match="exactly range keys"):
        validate_platform_batch(batch)

    performance["性能范围"] = {"lb": 120, "ub": 100}
    with pytest.raises(PlatformBatchError, match="lb must not exceed"):
        validate_platform_batch(batch)

    performance["性能范围"] = {"lb": 100, "ub": 120}
    batch["data"][0]["content"]["operations"][1]["原始论文PDF"] = (
        "..\\secret.pdf"
    )
    with pytest.raises(PlatformBatchError, match="safe relative"):
        validate_platform_batch(batch)

    del batch["data"][0]["content"]["operations"][1]["原始论文PDF"]
    batch["data"][0]["content"]["object"]["发表年份"] = 26
    with pytest.raises(PlatformBatchError, match="four-digit integer"):
        validate_platform_batch(batch)

    batch["data"][0]["content"]["object"]["发表年份"] = 2026
    performance["性能置信度"] = 1.2
    with pytest.raises(PlatformBatchError, match="between 0 and 1"):
        validate_platform_batch(batch)


def test_rejects_downloaded_default_field_placeholder_shape(
    platform_batch_template: dict[str, Any],
    chemical_fiber_projection: dict[str, Any],
):
    batch = build_platform_batch(
        platform_batch_template,
        [chemical_fiber_projection],
    )
    _result(batch["data"][0], "result1")["成分与配方明细"] = {
        "默认字段": ["xxx_item_1"]
    }

    with pytest.raises(PlatformBatchError, match="must be an array"):
        validate_platform_batch(batch)


def test_rejects_illegal_candidate_value(
    platform_batch_template: dict[str, Any],
    chemical_fiber_projection: dict[str, Any],
):
    batch = build_platform_batch(
        platform_batch_template,
        [chemical_fiber_projection],
    )
    batch["data"][0]["content"]["object"]["数据状态"] = "平台未定义状态"

    with pytest.raises(PlatformBatchError, match="not in the platform candidates"):
        validate_platform_batch(batch)


def test_rejects_float_platform_id(
    platform_batch_template: dict[str, Any],
    chemical_fiber_projection: dict[str, Any],
):
    invalid_template = copy.deepcopy(platform_batch_template)
    invalid_template["dataset"]["_id"] = 9_007_199_254_740_994.0

    with pytest.raises(
        PlatformBatchError,
        match="positive JSON integer; do not parse it as float",
    ):
        build_platform_batch(invalid_template, [chemical_fiber_projection])


def test_rejects_duplicate_record_key_even_when_meta_id_is_unique(
    platform_batch_template: dict[str, Any],
    chemical_fiber_projection: dict[str, Any],
):
    batch = build_platform_batch(
        platform_batch_template,
        [chemical_fiber_projection],
    )
    duplicate = copy.deepcopy(batch["data"][0])
    duplicate["meta"]["数据ID"] = "data-a-different-id"
    batch["data"].append(duplicate)

    with pytest.raises(PlatformBatchError, match="duplicate 数据记录键"):
        validate_platform_batch(batch)


def test_cli_preserves_large_integer_ids_exactly(
    tmp_path,
    capsys,
    platform_batch_template: dict[str, Any],
    chemical_fiber_projection: dict[str, Any],
):
    template_path = tmp_path / "downloaded-platform-batch.json"
    projection_path = tmp_path / "projection.json"
    output_path = tmp_path / "upload-ready.json"
    template_path.write_text(
        json.dumps(platform_batch_template, ensure_ascii=False),
        encoding="utf-8",
    )
    projection_path.write_text(
        json.dumps(chemical_fiber_projection, ensure_ascii=False),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--batch-template",
            str(template_path),
            "--projection",
            str(projection_path),
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    serialized = output_path.read_text(encoding="utf-8")
    assert f'"_id": {DATASET_ID}' in serialized
    assert f'"_id": {TEMPLATE_ID}' in serialized
    parsed = json.loads(serialized)
    assert parsed["dataset"]["_id"] == DATASET_ID
    assert parsed["template"]["_id"] == TEMPLATE_ID
    summary = json.loads(capsys.readouterr().out)
    assert summary["ids_exceed_javascript_safe_integer"] is True
    assert summary["record_count"] == 1
