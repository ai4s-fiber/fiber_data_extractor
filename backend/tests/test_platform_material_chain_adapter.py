from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from app.services.platform_material_chain_adapter import (
    LOCAL_FIELD_ORDER,
    MATERIAL_CHAIN_SCHEMA_VERSION,
    MaterialChainAdapterError,
    build_material_chain_batch,
    dumps_material_chain_batch,
    validate_material_chain_batch,
    validate_material_chain_template,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = (
    REPOSITORY_ROOT
    / "platform_templates"
    / "ai4s-material-chain-template-v0.3.2.json"
)
DATASET_ID = 2_082_071_264_142_430_210
TEMPLATE_ID = 2_082_071_243_661_643_777


def _template() -> dict:
    payload = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    payload["dataset"] = {
        "_id": DATASET_ID,
        "name": "AI4S材料数据链_成分工艺结构性能_v0.3.2_20260728",
    }
    payload["template"]["_id"] = TEMPLATE_ID
    return payload


def _row() -> dict:
    return {
        "数据ID": "MD-P0001-0001",
        "文献编号": "P0001",
        "论文题目": "Ordered material chain",
        "DOI|URL": "10.1000/ordered",
        "年份": 2026,
        "期刊|会议": "Journal of Ordered Materials",
        "材料类别": "PAN/SiO2",
        "具体材料对象|样品编号": "S-01",
        "纤维形态": "nanofiber",
        "原料|前驱体|基体": "PAN",
        "增强|填料|改性组分": "SiO2",
        "成分配比|浓度": "PAN=95 wt.%；SiO2=5 wt.%",
        "溶剂|助剂": "DMF",
        "工艺路线": "溶液配制；静电纺丝",
        "关键工艺参数": "电压=18 kV；距离=15 cm",
        "后处理条件": "80 °C 真空干燥",
        "结构表征方法": "SEM",
        "结构指标名称": "纤维平均直径",
        "结构数值": "350 nm",
        "结构单位": "nm",
        "性能测试方法|标准": "单轴拉伸",
        "性能指标名称": "拉伸强度",
        "性能数值": "800 MPa",
        "性能单位": "MPa",
        "测试条件": "23 °C, 50% RH",
        "结果描述|结论": "结构均匀；拉伸强度为 800 MPa",
        "数据来源位置": "p.5；Table 2",
        "原文图表编号": "Table 2",
        "是否完整": "完整",
        "缺失信息说明": "",
        "备注": "",
    }


def test_template_matches_original_business_order_and_is_pinned():
    payload = _template()
    summary = validate_material_chain_template(payload)

    assert summary == {
        "schema_version": MATERIAL_CHAIN_SCHEMA_VERSION,
        "template_id": TEMPLATE_ID,
        "field_count": 31,
        "ids_exceed_javascript_safe_integer": True,
    }
    assert (
        hashlib.sha256(TEMPLATE_PATH.read_bytes()).hexdigest()
        == "5ca1f62e96681fe30e5936738daed802d67c6fa6034e81cbbd90bab4ac4fa279"
    )
    assert LOCAL_FIELD_ORDER[:5] == (
        "数据ID",
        "文献编号",
        "论文题目",
        "DOI|URL",
        "年份",
    )
    assert LOCAL_FIELD_ORDER[13:21] == (
        "工艺路线",
        "关键工艺参数",
        "后处理条件",
        "结构表征方法",
        "结构指标名称",
        "结构数值",
        "结构单位",
        "性能测试方法|标准",
    )


def test_builds_one_nested_record_per_sample_in_exporter_safe_shape():
    batch = build_material_chain_batch(_template(), [_row()])
    summary = validate_material_chain_batch(batch)

    assert summary["record_count"] == 1
    assert summary["dataset_id"] == DATASET_ID
    assert summary["template_id"] == TEMPLATE_ID
    record = batch["data"][0]
    assert record["meta"] == {"数据ID": "MD-P0001-0001"}
    assert list(record["content"]["object"]) == ["文献、样品与成分"]
    assert list(record["content"]["operations"][0]) == ["id", "工艺"]
    assert list(record["content"]["results"][0]) == [
        "id",
        "结构、性能与来源",
    ]
    result = record["content"]["results"][0]["结构、性能与来源"]
    assert list(result)[:5] == [
        "结构表征方法",
        "结构指标名称",
        "结构数值",
        "结构单位",
        "性能测试方法或标准",
    ]
    assert list(result)[-4:] == [
        "结果描述或结论",
        "数据来源位置",
        "原文图表编号",
        "是否完整",
    ]
    serialized = dumps_material_chain_batch(batch)
    assert f'"_id": {DATASET_ID}' in serialized
    assert f'"_id": {TEMPLATE_ID}' in serialized


def test_rejects_flat_or_reordered_templates_and_duplicate_samples():
    flat = _template()
    flat["template"]["object"]["blocks"] = {
        "_ord": ["数值", "最小值"],
        "数值": {"t": 2},
        "最小值": {"t": 2},
    }
    with pytest.raises(MaterialChainAdapterError, match="blocks._ord"):
        validate_material_chain_template(flat)

    reordered = _template()
    result_misc = reordered["template"]["results"][0]["blocks"][
        "结构、性能与来源"
    ]["misc"]
    result_misc["_ord"][0], result_misc["_ord"][4] = (
        result_misc["_ord"][4],
        result_misc["_ord"][0],
    )
    with pytest.raises(MaterialChainAdapterError, match="field order mismatch"):
        validate_material_chain_template(reordered)

    with pytest.raises(MaterialChainAdapterError, match="duplicate 数据ID"):
        build_material_chain_batch(_template(), [_row(), copy.deepcopy(_row())])
