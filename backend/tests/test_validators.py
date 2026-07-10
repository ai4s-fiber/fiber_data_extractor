"""Validator regression tests (gold-standard logic)."""

from app.services.extractor_v7.validators import (
    determine_review_status,
    is_rough_source_location,
    validate_fact,
)


def test_rough_source_location_detects_coarse_values():
    assert is_rough_source_location("results")
    assert is_rough_source_location("")
    assert not is_rough_source_location("p.3, Fig. 2")


def test_validate_fact_flags_missing_performance_value():
    issues = validate_fact({
        "fact_type": "performance",
        "metric_or_parameter": "tensile_strength",
        "value": "",
        "evidence_text": "some evidence",
        "source_location": "p.2, Table 1",
        "extraction_method": "AI_text",
        "confidence": 0.9,
    })
    assert "性能数值为空" in issues


def test_review_status_missing_for_empty_performance():
    issues = validate_fact({
        "fact_type": "performance",
        "metric_or_parameter": "",
        "value": "",
        "evidence_text": "",
        "source_location": "",
        "extraction_method": "AI_text",
        "confidence": 0.5,
    })
    status = determine_review_status({}, None, issues)
    assert status == "缺失"


def test_holistic_with_substantial_evidence_not_uncertain_for_rough_source_only():
    issues = ["来源位置过粗"]
    fact = {
        "extraction_method": "AI_holistic",
        "evidence_text": "The open circuit voltage reached 12.5 V under 5 kPa pressure as shown in Fig. 8.",
        "assigned_sample_id": "Sensor-1",
        "confidence": 0.88,
    }
    status = determine_review_status(fact, 0.9, issues)
    assert status == "待审核"
