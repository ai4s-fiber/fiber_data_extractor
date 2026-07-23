"""Validator regression tests (gold-standard logic)."""

from app.services.extractor_v7.validators import (
    determine_review_status,
    is_background_or_reference_fact,
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


def test_grounded_results_table_is_not_background_due_to_nearby_citation():
    fact = {
        "fact_type": "performance",
        "extraction_method": "AI_holistic_table",
        "_source_table_row": 1,
        "metric_or_parameter": "oil_absorption_capacity",
        "value": "21.08",
        "evidence_text": (
            "The oil absorption capacity was greater than synthetic sorbents [22,23].\n"
            "[columns]\tCycle\tOil sorbed (g/g)\n[row 1]\tFirst\t21.08"
        ),
    }

    assert is_background_or_reference_fact(fact) is False


def test_results_reference_curve_is_not_treated_as_literature():
    fact = {
        "fact_type": "performance",
        "_chunk_section": "results",
        "metric_or_parameter": "Youngs_modulus",
        "value": "21",
        "evidence_text": (
            "Fig. 6 reports the results: the raw derivative is shown as reference, "
            "while the smoothed stiffness is E1 = 21 GPa."
        ),
    }

    assert is_background_or_reference_fact(fact) is False


def test_figure_result_keeps_own_value_despite_literature_tail():
    fact = {
        "fact_type": "performance",
        "_chunk_section": "results",
        "metric_or_parameter": "knee_strain",
        "value": "0.2",
        "evidence_text": (
            "Fig. 5 reports a knee centered at 0.2% strain, a response commonly "
            "reported in the literature."
        ),
    }

    assert is_background_or_reference_fact(fact) is False
