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


def test_sanitize_invalid_assigned_sample_id():
    facts = [{
        "assigned_sample_id": "composite fibers with a mass ratio of PVDF and recycled cellulose of 8:10",
        "assignment_status": "assigned",
    }]
    cards = [{"sample_id": "PCF-PENG", "sample_aliases": []}]
    sanitize_assigned_sample_ids(facts, cards, [])
    assert facts[0]["assigned_sample_id"] is None
    assert facts[0]["assignment_status"] == "unassigned"


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
