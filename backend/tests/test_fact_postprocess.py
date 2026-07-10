"""Tests for generic fact post-processing."""

from app.services.extractor_v7.fact_postprocess import (
    assign_positional_fact_groups,
    expand_coupled_list_facts,
    is_placeholder_performance_value,
    postprocess_extracted_facts,
)


SHIELDING_EVIDENCE = (
    "the shielding effectiveness (SE) values of CFs/CFs, PI/CFs, CNT/CFs, "
    "and CFs/MWCNTs composites were about 20.8, 32.2, 40.6, and 51.6 dB, "
    "respectively, in the frequency range of 2-18 GHz."
)


def test_placeholder_values_are_rejected():
    assert is_placeholder_performance_value("various")
    assert is_placeholder_performance_value("see figure")
    assert not is_placeholder_performance_value("20.8")


def test_positional_assignment_for_shared_evidence():
    facts = [
        {
            "fact_id": "F001",
            "fact_type": "performance",
            "metric_or_parameter": "electromagnetic_interference_shielding_effectiveness",
            "value": "20.8",
            "unit": "dB",
            "evidence_text": SHIELDING_EVIDENCE,
        },
        {
            "fact_id": "F002",
            "fact_type": "performance",
            "metric_or_parameter": "electromagnetic_interference_shielding_effectiveness",
            "value": "32.2",
            "unit": "dB",
            "evidence_text": SHIELDING_EVIDENCE,
        },
        {
            "fact_id": "F003",
            "fact_type": "performance",
            "metric_or_parameter": "electromagnetic_interference_shielding_effectiveness",
            "value": "40.6",
            "unit": "dB",
            "evidence_text": SHIELDING_EVIDENCE,
        },
        {
            "fact_id": "F004",
            "fact_type": "performance",
            "metric_or_parameter": "electromagnetic_interference_shielding_effectiveness",
            "value": "51.6",
            "unit": "dB",
            "evidence_text": SHIELDING_EVIDENCE,
        },
    ]
    assign_positional_fact_groups(facts)
    assigned = {f["value"]: f["assigned_sample_id"] for f in facts}
    assert assigned["20.8"] == "CFs/CFs"
    assert assigned["32.2"] == "PI/CFs"
    assert assigned["40.6"] == "CNT/CFs"
    assert assigned["51.6"] == "CFs/MWCNTs"


def test_expand_coupled_list_splits_single_fact():
    facts = [{
        "fact_id": "F1",
        "fact_type": "performance",
        "metric_or_parameter": "tensile_strength",
        "value": "various",
        "unit": "MPa",
        "evidence_text": (
            "The tensile strength values of Sample-A, Sample-B and Sample-C "
            "composites were about 10, 20 and 30 MPa."
        ),
    }]
    expanded = expand_coupled_list_facts(facts)
    assert len(expanded) == 3
    assert {f["value"] for f in expanded} == {"10", "20", "30"}


def test_postprocess_enriches_sample_mentions():
    facts = [{
        "fact_id": "F1",
        "fact_type": "performance",
        "metric_or_parameter": "tensile_strength",
        "value": "various",
        "unit": "MPa",
        "evidence_text": (
            "The values of Fiber-X and Fiber-Y fibers were about 10 and 12 MPa."
        ),
    }]
    processed, mentions = postprocess_extracted_facts(facts, [])
    assert len(processed) >= 2
    assert any(m.get("normalized_sample_id") == "Fiber-X" for m in mentions)
