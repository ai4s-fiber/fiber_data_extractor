"""Tests for generic fact post-processing."""

from app.services.extractor_v7.fact_postprocess import (
    assign_positional_fact_groups,
    expand_coupled_list_facts,
    is_placeholder_performance_value,
    postprocess_extracted_facts,
    restore_unique_uncertainty_from_evidence,
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


def test_postprocess_sanitizes_contextual_sample_before_creating_mentions():
    facts = [{
        "fact_id": "F1",
        "fact_type": "performance",
        "metric_or_parameter": "degree_of_acetylation",
        "value": "17.85",
        "unit": "%",
        "assigned_sample_id": "acetylated jute; optimum WPG sample",
        "candidate_sample_ids": ["acetylated jute; optimum WPG sample"],
        "evidence_text": "The optimum WPG sample of acetylated jute reached 17.85% acetylation.",
    }]

    processed, mentions = postprocess_extracted_facts(facts, [])

    assert processed[0]["assigned_sample_id"] == "acetylated jute"
    assert processed[0]["candidate_sample_ids"] == ["acetylated jute"]
    assert "optimum WPG sample" in processed[0]["condition"]
    assert not any(";" in (mention.get("normalized_sample_id") or "") for mention in mentions)


def test_restore_unique_uncertainty_for_each_value_in_shared_evidence():
    evidence = (
        "The average diameter for 17 needles was 74±28 nm, while the case of "
        "72 needles had a value of 66 ±26 nm."
    )
    facts = [
        {"fact_type": "performance", "value": "74", "unit": "nm", "evidence_text": evidence},
        {"fact_type": "performance", "value": "66", "unit": "nm", "evidence_text": evidence},
    ]

    restored = restore_unique_uncertainty_from_evidence(facts)

    assert [fact["value"] for fact in restored] == ["74 ± 28", "66 ± 26"]


def test_restore_uncertainty_keeps_ambiguous_or_wrong_unit_values_unchanged():
    facts = [
        {
            "fact_type": "performance",
            "value": "74",
            "unit": "nm",
            "evidence_text": "Values were 74 ± 28 nm and 74 ± 31 nm.",
        },
        {
            "fact_type": "performance",
            "value": "66",
            "unit": "MPa",
            "evidence_text": "The diameter was 66 ± 26 nm.",
        },
    ]

    restored = restore_unique_uncertainty_from_evidence(facts)

    assert [fact["value"] for fact in restored] == ["74", "66"]
