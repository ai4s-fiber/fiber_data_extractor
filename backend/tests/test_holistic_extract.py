"""Holistic extraction unit tests."""

from app.services.extractor_v7.holistic_extract import (
    PERFORMANCE_PROMPT,
    SENSING_SWEEP_PROMPT,
    SPECTROSCOPY_SWEEP_PROMPT,
    enrich_sample_cards,
    merge_holistic_and_atomic_facts,
    performances_to_facts,
)


def test_performance_prompt_format_escapes_json_braces():
    rendered = PERFORMANCE_PROMPT.format(sample_ids="PCF_1.0wtCNC")
    assert "PCF_1.0wtCNC" in rendered
    assert '{"performances":' in rendered


def test_sensing_and_spectroscopy_prompts_format():
    for prompt in (SENSING_SWEEP_PROMPT, SPECTROSCOPY_SWEEP_PROMPT):
        rendered = prompt.format(sample_ids="S1, S2")
        assert "S1, S2" in rendered
        assert '{"performances":' in rendered


def test_performances_to_facts_assigns_sample():
    facts = performances_to_facts([{
        "sample_id": "PCF_1.0wtCNC",
        "performance_metric": "tensile_strength",
        "performance_value": "147",
        "performance_unit": "MPa",
        "source_location": "p.6, Fig. 4",
    }])
    assert len(facts) == 1
    assert facts[0]["assigned_sample_id"] == "PCF_1.0wtCNC"
    assert facts[0]["extraction_method"] == "AI_holistic"


def test_merge_dedupes_by_sample_metric_value():
    atomic = [{
        "fact_type": "performance",
        "metric_or_parameter": "tensile_strength",
        "value": "147",
        "unit": "MPa",
        "assigned_sample_id": "S1",
        "extraction_method": "AI_text",
    }]
    holistic = performances_to_facts([{
        "sample_id": "S1",
        "performance_metric": "tensile_strength",
        "performance_value": "147",
        "performance_unit": "MPa",
    }])
    merged = merge_holistic_and_atomic_facts(atomic, holistic)
    perf = [f for f in merged if f.get("fact_type") == "performance"]
    assert len(perf) == 1
    assert perf[0]["extraction_method"] == "AI_holistic"


def test_enrich_sample_cards_fills_process_fields():
    cards = [{"sample_id": "PCF_1.0wtCNC", "process_route": ""}]
    background = {
        "process": {
            "process_route": "wet spinning-drawing-washing-heat setting",
            "spinning_method": "wet spinning",
            "process_parameters": "heat-setting=150°C",
        },
        "composition": {"matrix_name": "PVDF"},
        "structure": {"structure_methods": "FTIR, XRD"},
    }
    samples = [{"sample_id": "PCF_1.0wtCNC", "material_system": "PVDF/recycled cellulose/CNC"}]
    out = enrich_sample_cards(cards, samples, background)
    card = next(c for c in out if c["sample_id"] == "PCF_1.0wtCNC")
    assert card["spinning_method"] == "wet spinning"
    assert card["structure_methods"] == "FTIR, XRD"
