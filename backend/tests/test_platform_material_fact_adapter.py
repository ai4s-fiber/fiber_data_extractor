"""Tests for the independent flat material-fact platform adapter."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.services.platform_material_fact_adapter import (
    DOMAIN_LABELS,
    JAVASCRIPT_MAX_SAFE_INTEGER,
    MATERIAL_FACT_FIELDS,
    MaterialFactAdapterError,
    build_material_fact_batch,
    build_material_fact_records,
    dumps_material_fact_batch,
    validate_material_fact_batch,
    validate_material_fact_records,
    validate_material_fact_template,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = (
    REPOSITORY_ROOT
    / "platform_templates"
    / "ai4s-material-facts-template-v0.2.json"
)
PROJECTION_CANARY_PATH = (
    REPOSITORY_ROOT
    / "platform_templates"
    / "canary"
    / "chemical-fiber-projection-canary.json"
)


def _template_document() -> dict[str, Any]:
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def _batch_template() -> dict[str, Any]:
    document = _template_document()
    document["dataset"] = {
        "_id": 20_816_601_573_051_637_778,
        "name": "AI4S 原子材料事实",
    }
    document["template"]["_id"] = 20_816_583_741_807_042_580
    return document


@pytest.fixture
def generic_inputs():
    papers = [
        {
            "id": 7,
            "paper_title": "A material paper",
            "doi": "10.1000/example",
            "publication_year": 2026,
            "journal": "Journal of Materials",
        }
    ]
    samples = [
        {
            "id": 101,
            "paper_id": 7,
            "sample_id": "S-01",
            "sample_group_id": "G-01",
            "aliases": ["control", "untreated"],
            "material_system": "PAN/SiO2",
            "fiber_type": "electrospun nanofiber",
        }
    ]
    facts = [
        {
            "fact_id": "C-1",
            "paper_id": 7,
            "assigned_sample_id": 101,
            "fact_type": "composition",
            "metric": "SiO2 content",
            "raw_value": "5 wt%",
            "value_number": 5,
            "unit": "wt%",
            "component_role": "填料",
            "evidence": {
                "evidence_text": "The SiO2 content was 5 wt%.",
                "source_page": 2,
                "source_block_id": "table-1",
            },
            "confidence": 0.97,
        },
        {
            "fact_id": "P-1",
            "paper_id": 7,
            "assigned_sample_id": 101,
            "fact_type": "process",
            "metric": "spinning temperature",
            "raw_value": "260–270 °C",
            "range_min": 260,
            "range_max": 270,
            "unit": "°C",
            "process_stage": "纺丝",
            "condition": "melt spinning",
        },
        {
            "fact_id": "S-1",
            "paper_id": 7,
            "assigned_sample_id": 101,
            # This mirrors legacy records that were promoted to performance.
            "fact_type": "performance",
            "metric": "fiber_diameter",
            "raw_value": "611 nm",
            "value_number": 611,
            "unit": "nm",
            "method": "SEM",
        },
        {
            "fact_id": "M-1",
            "paper_id": 7,
            "assigned_sample_id": 101,
            "fact_type": "performance",
            "metric": "tensile_strength",
            "raw_value": "5.51 MPa",
            "value_number": 5.51,
            "unit": "MPa",
            "method": "uniaxial tensile test",
        },
    ]
    return papers, samples, facts


def test_template_is_flat_and_contains_only_business_fact_fields():
    document = _template_document()

    summary = validate_material_fact_template(document)

    template = document["template"]
    assert summary == {
        "schema_version": "ai4s_material_fact_v0.2",
        "template_id": 2026072802,
        "field_count": 26,
        "flat": True,
    }
    assert tuple(template["object"]["blocks"]["_ord"]) == MATERIAL_FACT_FIELDS
    assert template["operations"] == []
    assert template["results"] == []
    assert document["data"] == []
    forbidden = {"抽取方式", "映射版本", "源数据签名", "导出时间"}
    assert forbidden.isdisjoint(MATERIAL_FACT_FIELDS)


def test_generic_inputs_build_one_flat_record_per_material_fact(generic_inputs):
    papers, samples, facts = generic_inputs

    records = build_material_fact_records(papers, samples, facts)
    summary = validate_material_fact_records(_template_document(), records)

    assert summary["record_count"] == 4
    assert {record["content"]["object"]["事实类别"] for record in records} == set(
        DOMAIN_LABELS
    )
    assert all(record["content"]["operations"] == [] for record in records)
    assert all(record["content"]["results"] == [] for record in records)
    assert all(
        not isinstance(value, (dict, list))
        for record in records
        for value in record["content"]["object"].values()
    )

    diameter = next(
        record["content"]["object"]
        for record in records
        if record["content"]["object"]["指标或参数名称"] == "fiber_diameter"
    )
    assert diameter["事实类别"] == "结构"
    assert diameter["指标类别"] == "形貌"
    assert diameter["材料体系"] == "PAN/SiO2"
    assert diameter["样品编号"] == "S-01"


def test_record_keys_and_output_are_stable_when_input_order_changes(
    generic_inputs,
):
    papers, samples, facts = generic_inputs

    first = build_material_fact_records(papers, samples, facts)
    second = build_material_fact_records(
        list(reversed(papers)),
        list(reversed(samples)),
        list(reversed(facts)),
    )

    assert first == second


def test_duplicate_semantic_fact_merges_evidence_deterministically():
    papers = [{"paper_id": "P-1", "title": "Paper"}]
    samples = [{"paper_id": "P-1", "sample_id": "S-1"}]
    base = {
        "paper_id": "P-1",
        "sample_id": "S-1",
        "domain": "性能",
        "metric": "tensile strength",
        "raw_value": "100 MPa",
        "value_number": 100,
        "unit": "MPa",
    }

    records = build_material_fact_records(
        papers,
        samples,
        [
            {**base, "evidence_text": "Evidence B", "confidence": 0.8},
            {**base, "evidence_text": "Evidence A", "confidence": 0.9},
        ],
    )

    assert len(records) == 1
    payload = records[0]["content"]["object"]
    assert payload["证据原文"] == "Evidence A；Evidence B"
    assert payload["置信度"] == 0.9


def test_same_fact_id_with_conflicting_content_is_rejected():
    papers = [{"paper_id": "P-1", "title": "Paper"}]
    samples = [{"paper_id": "P-1", "sample_id": "S-1"}]
    common = {
        "fact_id": "F-1",
        "paper_id": "P-1",
        "sample_id": "S-1",
        "domain": "性能",
        "metric": "tensile strength",
        "unit": "MPa",
    }

    with pytest.raises(MaterialFactAdapterError, match="冲突内容"):
        build_material_fact_records(
            papers,
            samples,
            [
                {**common, "raw_value": "100 MPa", "value_number": 100},
                {**common, "raw_value": "110 MPa", "value_number": 110},
            ],
        )


def test_current_projection_canary_becomes_flat_four_domain_records():
    projection = json.loads(PROJECTION_CANARY_PATH.read_text(encoding="utf-8"))

    records = build_material_fact_records(projections=[projection])
    summary = validate_material_fact_records(_template_document(), records)

    assert summary["record_count"] == 6
    assert {record["content"]["object"]["事实类别"] for record in records} == set(
        DOMAIN_LABELS
    )
    assert sum(
        record["content"]["object"]["指标或参数名称"]
        == "whisker instability index"
        for record in records
    ) == 1


def test_build_and_dump_batch_preserve_large_platform_ids(generic_inputs):
    papers, samples, facts = generic_inputs

    batch = build_material_fact_batch(
        _batch_template(),
        papers,
        samples,
        facts,
    )
    summary = validate_material_fact_batch(batch)
    serialized = dumps_material_fact_batch(batch)

    assert summary["record_count"] == 4
    assert summary["ids_exceed_javascript_safe_integer"] is True
    assert summary["dataset_id"] > JAVASCRIPT_MAX_SAFE_INTEGER
    assert '"_id": 20816601573051637778' in serialized
    assert '"_id": 20816583741807042580' in serialized
    assert json.loads(serialized)["data"] == batch["data"]


def test_validation_rejects_nested_or_out_of_schema_payload(generic_inputs):
    papers, samples, facts = generic_inputs
    records = build_material_fact_records(papers, samples, facts)
    invalid = copy.deepcopy(records)
    invalid[0]["content"]["object"]["抽取方式"] = "LLM"

    with pytest.raises(MaterialFactAdapterError, match="模板外字段"):
        validate_material_fact_records(_template_document(), invalid)


@pytest.mark.parametrize(
    "fact, message",
    [
        (
            {
                "paper_id": "P-1",
                "sample_id": "S-1",
                "metric": "unknown metric",
                "raw_value": "1",
            },
            "事实缺少可识别的类别",
        ),
        (
            {
                "paper_id": "P-1",
                "sample_id": "S-1",
                "domain": "性能",
                "metric": "tensile strength",
            },
            "必须保留非空原始值",
        ),
    ],
)
def test_ambiguous_or_value_less_facts_fail_closed(fact, message):
    with pytest.raises(MaterialFactAdapterError, match=message):
        build_material_fact_records(
            [{"paper_id": "P-1", "title": "Paper"}],
            [{"paper_id": "P-1", "sample_id": "S-1"}],
            [fact],
        )
