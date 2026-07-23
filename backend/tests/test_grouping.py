"""Deterministic grouping helpers — unit tests."""

from app.services.grouping import (
    assign_fact_to_sample,
    build_sample_cards,
    fill_sample_card_variables,
    group_samples,
    infer_variable_from_sample_id,
    is_material_sample_id,
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


def test_fiber_number_suffix_is_not_misread_as_draw_ratio():
    assert infer_variable_from_sample_id("Acetylated_jute_fiber_12") == ("", "", "")
    assert infer_variable_from_sample_id("R_1.5") == ("draw ratio", "1.5", "×")


def test_group_variable_name_without_sample_value_is_not_propagated():
    cards = [{"sample_id": "S1", "variable_name": "", "variable_value": "", "variable_unit": ""}]
    groups = [{"sample_ids": ["S1"], "group_variable_name": "CNC loading", "confidence": 0.9}]
    out = fill_sample_card_variables(cards, groups)
    assert out[0]["variable_name"] == ""


def test_group_variable_without_inferred_value_is_not_copied_to_another_sample():
    cards = [
        {"sample_id": "S1", "sample_group_id": "G1", "variable_name": "time", "variable_value": "1", "variable_unit": "h"},
        {"sample_id": "S2", "sample_group_id": "G1", "variable_name": "", "variable_value": "", "variable_unit": ""},
    ]

    out = fill_sample_card_variables(cards)

    assert out[1]["variable_name"] == ""
    assert out[1]["variable_value"] == ""


def test_unsupported_process_variable_is_removed_from_control_sample():
    cards = [{
        "sample_id": "raw jute",
        "variable_name": "time",
        "variable_value": "0.5",
        "variable_unit": "h",
        "evidence_text": "Untreated raw jute fiber.",
    }]

    out = fill_sample_card_variables(cards)

    assert out[0]["variable_name"] == ""
    assert out[0]["variable_value"] == ""


def test_build_sample_cards_applies_global_experimental_process_fact():
    mentions = [{"normalized_sample_id": "PVDF-TrFE nanowire", "mention_text": "PVDF-TrFE nanowire"}]
    facts = [{
        "fact_type": "process",
        "metric_or_parameter": "annealing temperature",
        "value": "135",
        "unit": "°C",
        "evidence_text": "The nanowires were annealed at 135 °C.",
        "source_location": "p.3, experimental section",
        "_chunk_section": "experimental",
        "candidate_sample_ids": [],
    }]

    cards = build_sample_cards(mentions, [], [], facts)

    assert cards[0]["process_parameters"]
    assert cards[0]["process_route"] == "annealing"


def test_material_sample_filter_rejects_property_peak_and_joined_ids():
    assert is_material_sample_id("PCL/AA")
    assert is_material_sample_id("PCL/AA/S")
    assert is_material_sample_id("PCL/AA/SBCu")
    assert not is_material_sample_id("hydrophilicity")
    assert not is_material_sample_id("symmetric CH2 stretching")
    assert not is_material_sample_id("PCL_AA_S/PCL_AA_SBCu")
    assert not is_material_sample_id(
        "TPU and fiberreinforced materials when the compression"
    )


def test_build_sample_cards_ignores_pseudo_samples_and_deferred_background():
    mentions = [{
        "normalized_sample_id": "PCL/AA",
        "mention_text": "PCL/AA",
    }]
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "stretching peak",
            "metric_or_parameter": "FTIR_band_1",
            "value": "1722",
            "unit": "cm^-1",
        },
        {
            "fact_type": "process",
            "metric_or_parameter": "spinning_method",
            "value": "electrospinning",
            "_background_only": True,
            "_apply_to_all_fiber_samples": True,
        },
    ]

    cards = build_sample_cards(mentions, [], [], facts)

    assert [card["sample_id"] for card in cards] == ["PCL/AA"]
    assert not cards[0]["process_route"]
    assert not cards[0]["process_parameters"]


def test_spinning_method_uses_value_for_route_and_method():
    cards = build_sample_cards(
        [{"normalized_sample_id": "PCL fiber", "mention_text": "PCL fiber"}],
        [],
        [],
        [{
            "fact_type": "process",
            "assigned_sample_id": "PCL fiber",
            "metric_or_parameter": "spinning_method",
            "value": "electrospinning",
        }],
    )

    assert cards[0]["spinning_method"] == "electrospinning"
    assert cards[0]["process_route"] == "electrospinning"
