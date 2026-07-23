"""Fact post-processing yield tests."""

from app.services.extractor_v7.fact_postprocess import (
    promote_measurable_facts,
    renumber_fact_ids,
    sanitize_assigned_sample_ids,
)
from app.services.extractor_v7.service import V7ExtractorService


def test_renumber_fact_ids_makes_unique_ids():
    facts = [
        {"fact_id": "F0001", "metric_or_parameter": "a"},
        {"fact_id": "F0001", "metric_or_parameter": "b"},
        {"fact_id": "F0003", "metric_or_parameter": "c"},
    ]
    renumber_fact_ids(facts)
    assert [f["fact_id"] for f in facts] == ["F0001", "F0002", "F0003"]


def test_promote_measurable_process_fact_to_performance():
    facts = [{
        "fact_type": "process",
        "metric_or_parameter": "tensile_strength",
        "value": "45",
        "unit": "MPa",
    }]
    promote_measurable_facts(facts)
    assert facts[0]["fact_type"] == "performance"


def test_promote_measurable_facts_preserves_known_electrospinning_parameters():
    facts = [
        {"fact_type": "process", "metric_or_parameter": "voltage", "value": "25"},
        {"fact_type": "process", "metric_or_parameter": "electric_field_strength", "value": "2.5"},
    ]

    promote_measurable_facts(facts)

    assert [fact["fact_type"] for fact in facts] == ["process", "process"]


def test_sanitize_invalid_assigned_sample_id():
    facts = [{
        "assigned_sample_id": "composite fibers with a mass ratio of PVDF and recycled cellulose of 8:10",
        "assignment_status": "assigned",
    }]
    cards = [{"sample_id": "PCF-PENG", "sample_aliases": []}]
    sanitize_assigned_sample_ids(facts, cards, [])
    assert facts[0]["assigned_sample_id"] is None
    assert facts[0]["assignment_status"] == "unassigned"


def test_sanitize_drops_unknown_candidate_sample_ids():
    facts = [{
        "assigned_sample_id": "stretching peak",
        "candidate_sample_ids": ["stretching peak", "PCL_AA_S"],
        "assignment_status": "assigned",
    }]
    cards = [{"sample_id": "PCL/AA/S", "sample_aliases": '["PCL_AA_S"]'}]

    sanitize_assigned_sample_ids(facts, cards, [])

    assert facts[0]["assigned_sample_id"] is None
    assert facts[0]["candidate_sample_ids"] == ["PCL/AA/S"]


def test_sanitize_json_alias_to_canonical_sample_id():
    facts = [{
        "assigned_sample_id": "raw jute",
        "assignment_status": "assigned",
    }]
    cards = [{
        "sample_id": "raw jute fiber",
        "sample_aliases": '["jute fiber", "raw jute"]',
    }]

    sanitize_assigned_sample_ids(facts, cards, [])

    assert facts[0]["assigned_sample_id"] == "raw jute fiber"
    assert facts[0]["assignment_status"] == "assigned"


def test_explicit_run_reference_upgrades_base_to_numbered_catalog_sample():
    facts = [{
        "assigned_sample_id": "acetylated jute",
        "assignment_status": "assigned",
        "condition": "2% NBS; sample 12",
        "evidence_text": "Sample 12 resulted in a WPG of 17.01%.",
    }]
    cards = [
        {"sample_id": "acetylated jute fiber", "sample_aliases": '["acetylated jute"]'},
        {"sample_id": "acetylated jute fiber sample 11", "sample_aliases": ""},
        {"sample_id": "acetylated jute fiber sample 12", "sample_aliases": ""},
    ]

    sanitize_assigned_sample_ids(facts, cards, [])

    assert facts[0]["assigned_sample_id"] == "acetylated jute fiber sample 12"


def test_build_result_facts_exports_numeric_process_facts():
    facts = [{
        "fact_id": "F0001",
        "fact_type": "process",
        "metric_or_parameter": "cooking_temperature",
        "value": "160",
        "unit": "°C",
        "evidence_text": "cooked at 160 C",
        "source_location": "p.2",
        "confidence": 0.8,
    }]
    cards = [{"sample_id": "S1", "sample_group_id": "G1"}]
    result = V7ExtractorService._build_result_facts(facts, cards)
    assert len(result) == 1
    assert result[0]["export_target"] == "Result_Facts_QA"
    assert result[0]["sample_id"] == "S1"


def test_background_reference_fact_stays_out_of_deliverable_records():
    facts = [{
        "fact_id": "F-BG-1",
        "fact_type": "performance",
        "metric_or_parameter": "tensile_strength",
        "value": "250",
        "unit": "MPa",
        "assigned_sample_id": "S1",
        "assignment_status": "assigned",
        "evidence_text": "Previous studies reported a tensile strength of 250 MPa.",
        "source_location": "Introduction",
        "confidence": 0.9,
        "_data_source_type": "background_reference",
        "_explicit_background_reference": True,
    }]
    cards = [{"sample_id": "S1", "sample_group_id": "G1"}]

    result = V7ExtractorService._build_result_facts(facts, cards)
    records, report = V7ExtractorService._stage4_generate_records(
        paper_id=1,
        project_id=1,
        paper_metadata={},
        sample_cards=cards,
        facts=facts,
    )

    assert result[0]["export_target"] == "Not exported"
    assert records == []
    assert report["result_fact_count"] == 1


def test_process_voltage_is_not_canonicalized_as_dielectric_breakdown_strength():
    result = V7ExtractorService._build_result_facts(
        [{
            "fact_id": "F0001",
            "fact_type": "process",
            "metric_or_parameter": "Voltage (kV)",
            "value": "25",
            "unit": "kV",
            "assigned_sample_id": "PAN_nanofiber_72_needles",
            "assignment_status": "assigned",
            "evidence_text": "Electrospinning voltage was 25 kV.",
            "source_location": "p.9, Table 1",
            "confidence": 0.99,
        }],
        [{"sample_id": "PAN_nanofiber_72_needles", "sample_group_id": "G1"}],
    )

    assert result[0]["canonical_metric"] == "voltage"
    assert result[0]["performance_category"] == "process"
