"""Deterministic grouping helpers — unit tests."""

from app.services.grouping import (
    assign_fact_to_sample,
    build_sample_cards,
    fill_sample_card_variables,
    group_samples,
    infer_variable_from_sample_id,
)


def test_infer_variable_from_wt_loading_sample_id():
    name, value, unit = infer_variable_from_sample_id("PCF_1.0wtCNC")
    assert name == "CNC loading"
    assert value == "1.0"
    assert unit == "wt%"


def test_infer_variable_from_zero_cnc():
    name, value, unit = infer_variable_from_sample_id("PCF_0CNC")
    assert name == "CNC loading"
    assert value == "0"
    assert unit == "wt%"


def test_infer_variable_from_pulp():
    name, value, unit = infer_variable_from_sample_id("recycled_cellulose_pulp")
    assert name == "raw material"
    assert "pulp" in value


def test_group_samples_by_shared_variable():
    mentions = [
        {"normalized_sample_id": "S1", "mention_text": "S1", "source_location": "p.2"},
        {"normalized_sample_id": "S2", "mention_text": "S2", "source_location": "p.2"},
    ]
    variables = [
        {"sample_id": "S1", "variable_name_raw": "CNT content", "variable_value_raw": "1", "confidence": 0.9},
        {"sample_id": "S2", "variable_name_raw": "CNT content", "variable_value_raw": "2", "confidence": 0.9},
    ]
    groups = group_samples(mentions, variables)
    assert groups
    assert groups[0]["group_variable_name"] == "CNT content"


def test_build_sample_cards_fills_inferred_variables():
    mentions = [{"normalized_sample_id": "PVDF_1.0wtCNC", "mention_text": "PVDF_1.0wtCNC"}]
    cards = build_sample_cards(mentions, [], [], [])
    card = cards[0]
    assert card["variable_name"] == "CNC loading"
    assert card["variable_value"] == "1.0"
    assert card["variable_unit"] == "wt%"


def test_assign_fact_to_sample_matches_evidence():
    mentions = [{"normalized_sample_id": "S1", "mention_text": "S1", "aliases": []}]
    groups = [{"sample_ids": ["S1"], "sample_group_id": "G001", "confidence": 0.9}]
    fact = {
        "evidence_text": "Sample S1 showed tensile strength of 100 MPa",
        "candidate_sample_ids": [],
    }
    result = assign_fact_to_sample(fact, mentions, groups)
    assert result["sample_id"] == "S1"


def test_infer_variable_from_dispersion_and_fabric():
    assert infer_variable_from_sample_id("CNC_dispersion_1.5wt") == ("CNC loading", "1.5", "wt%")
    name, value, unit = infer_variable_from_sample_id("fabric_PENG")
    assert name == "device configuration"
    assert value == "fabric_PENG"


def test_fill_sample_card_variables_uses_group_name():
    cards = [{"sample_id": "S1", "variable_name": "", "variable_value": "", "variable_unit": ""}]
    groups = [{"sample_ids": ["S1"], "group_variable_name": "CNC loading", "confidence": 0.9}]
    out = fill_sample_card_variables(cards, groups)
    assert out[0]["variable_name"] == "CNC loading"
