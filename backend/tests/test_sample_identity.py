"""Generic sample identity clustering tests."""

from app.services.extractor_v7.sample_identity import (
    apply_sample_alias_map,
    build_sample_alias_map,
    merge_sample_identities,
    parse_sample_aliases,
    repair_contextual_fact_assignments,
)


def test_merge_similar_loading_variants():
    mentions = [
        {"normalized_sample_id": "1.0 wt% CNCs", "mention_text": "1.0 wt% CNCs", "aliases": []},
        {"normalized_sample_id": "PCF_1.0wtCNC", "mention_text": "PCF_1.0wtCNC", "aliases": []},
        {"normalized_sample_id": "PVDF powder", "mention_text": "PVDF powder", "aliases": []},
        {"normalized_sample_id": "recycled cellulose pulp", "mention_text": "recycled cellulose pulp", "aliases": []},
    ]
    alias_map = build_sample_alias_map(mentions)
    assert alias_map["1.0 wt% CNCs"] == alias_map["PCF_1.0wtCNC"]
    assert alias_map["PVDF powder"] == "PVDF powder"
    assert alias_map["recycled cellulose pulp"] == "recycled cellulose pulp"


def test_apply_alias_map_rewrites_facts_and_cards():
    alias_map = {"1.0 wt% CNCs": "PCF_1.0wtCNC"}
    facts = [{"assigned_sample_id": "1.0 wt% CNCs", "candidate_sample_ids": ["1.0 wt% CNCs"]}]
    cards = [{"sample_id": "1.0 wt% CNCs", "sample_aliases": ""}]
    mentions = [{"normalized_sample_id": "1.0 wt% CNCs", "aliases": []}]
    mentions, facts, cards = apply_sample_alias_map(
        alias_map, sample_mentions=mentions, facts=facts, sample_cards=cards,
    )
    assert facts[0]["assigned_sample_id"] == "PCF_1.0wtCNC"
    assert cards[0]["sample_id"] == "PCF_1.0wtCNC"


def test_merge_sample_identities_is_noop_for_distinct_samples():
    mentions = [
        {"normalized_sample_id": "Sample-A", "aliases": []},
        {"normalized_sample_id": "Sample-B", "aliases": []},
    ]
    facts = [{"assigned_sample_id": "Sample-A"}]
    cards = [{"sample_id": "Sample-A"}, {"sample_id": "Sample-B"}]
    out_mentions, out_facts, out_cards = merge_sample_identities(mentions, facts, cards)
    assert len(out_cards) == 2
    assert out_facts[0]["assigned_sample_id"] == "Sample-A"


def test_numbered_run_variants_are_not_merged_with_each_other_or_base():
    mentions = [
        {"normalized_sample_id": "acetylated jute", "aliases": []},
        {"normalized_sample_id": "acetylated jute 1", "aliases": []},
        {"normalized_sample_id": "acetylated jute 2", "aliases": []},
    ]

    alias_map = build_sample_alias_map(mentions)

    assert alias_map["acetylated jute"] == "acetylated jute"
    assert alias_map["acetylated jute 1"] == "acetylated jute 1"
    assert alias_map["acetylated jute 2"] == "acetylated jute 2"


def test_conflicting_explicit_variables_block_cross_alias_merge():
    samples = [
        {
            "sample_id": "PAN_nanofiber_multiple_needle_box1",
            "aliases": ["PAN_nanofiber_multiple_needle_box2", "box 1", "box 2"],
            "variable_name": "number_of_needles",
            "variable_value": "17",
            "variable_unit": "count",
        },
        {
            "sample_id": "PAN_nanofiber_multiple_needle_box2",
            "aliases": ["PAN_nanofiber_multiple_needle_box1", "box 2"],
            "variable_name": "number of needles",
            "variable_value": "72",
            "variable_unit": "count",
        },
    ]
    mentions = [
        {"normalized_sample_id": sample["sample_id"], "aliases": sample["aliases"]}
        for sample in samples
    ]
    cards = [
        {
            "sample_id": sample["sample_id"],
            "sample_aliases": sample["aliases"],
            "variable_name": sample["variable_name"],
            "variable_value": sample["variable_value"],
            "variable_unit": sample["variable_unit"],
        }
        for sample in samples
    ]

    alias_map = build_sample_alias_map(
        mentions,
        holistic_samples=samples,
        sample_cards=cards,
    )

    assert alias_map[samples[0]["sample_id"]] == samples[0]["sample_id"]
    assert alias_map[samples[1]["sample_id"]] == samples[1]["sample_id"]


def test_same_needle_configuration_merges_verbose_setup_alias():
    samples = [
        {
            "sample_id": "PAN_nanofiber_17_needles",
            "aliases": ["17 needles"],
            "variable_name": "number of needles",
            "variable_value": "17",
            "variable_unit": "",
        },
        {
            "sample_id": "PAN_nanofiber_multineedle_box1_17_10mm",
            "aliases": ["multiple-needle PAN nanofiber box 1"],
            "variable_name": "needles per box",
            "variable_value": "17",
            "variable_unit": "count",
        },
        {
            "sample_id": "PAN_nanofiber_72_needles",
            "aliases": ["72 needles"],
            "variable_name": "number of needles",
            "variable_value": "72",
            "variable_unit": "",
        },
    ]

    alias_map = build_sample_alias_map([], holistic_samples=samples)

    assert alias_map[samples[0]["sample_id"]] == alias_map[samples[1]["sample_id"]]
    assert alias_map[samples[0]["sample_id"]] == "PAN_nanofiber_17_needles"
    assert alias_map[samples[2]["sample_id"]] != alias_map[samples[0]["sample_id"]]


def test_same_needle_count_with_conflicting_spacing_does_not_collapse():
    samples = [
        {
            "sample_id": "PAN_nanofiber_multineedle_box1_17_10mm",
            "variable_name": "needles per box",
            "variable_value": "17",
            "variable_unit": "count",
        },
        {
            "sample_id": "PAN_nanofiber_multineedle_box2_17_5mm",
            "variable_name": "needles per box",
            "variable_value": "17",
            "variable_unit": "count",
        },
    ]

    alias_map = build_sample_alias_map([], holistic_samples=samples)

    assert alias_map[samples[0]["sample_id"]] != alias_map[samples[1]["sample_id"]]


def test_needle_count_aliases_merge_real_setup_facts_without_cross_assignment():
    holistic_samples = [
        {
            "sample_id": "PAN_nanofiber_multi_needle_box1",
            "aliases": ["17 needles", "box 1", "multiple-needle electrospinning"],
            "variable_name": "minimum_needle_spacing",
            "variable_value": "10",
            "variable_unit": "mm",
        },
        {
            "sample_id": "PAN_nanofiber_multi_needle_box2",
            "aliases": ["72 needles", "box 2", "multiple-needle electrospinning"],
            "variable_name": "minimum_needle_spacing",
            "variable_value": "5",
            "variable_unit": "mm",
        },
    ]
    cards = [
        {
            "sample_id": sample["sample_id"],
            "sample_aliases": sample["aliases"],
            "variable_name": sample["variable_name"],
            "variable_value": sample["variable_value"],
            "variable_unit": sample["variable_unit"],
            "material_system": "PAN",
            "fiber_type": "nanofiber",
        }
        for sample in holistic_samples
    ] + [
        {"sample_id": "17-needle electrospinning setup", "sample_aliases": ""},
        {"sample_id": "72-needle electrospinning setup", "sample_aliases": ""},
    ]
    facts = [
        {
            "assigned_sample_id": "17-needle electrospinning setup",
            "candidate_sample_ids": ["17-needle electrospinning setup"],
            "metric_or_parameter": "fiber_diameter",
            "value": "74",
            "unit": "nm",
        },
        {
            "assigned_sample_id": "72-needle electrospinning setup",
            "candidate_sample_ids": ["72-needle electrospinning setup"],
            "metric_or_parameter": "fiber_diameter",
            "value": "66",
            "unit": "nm",
        },
    ]

    _, merged_facts, merged_cards = merge_sample_identities(
        [], facts, cards, holistic_samples=holistic_samples,
    )

    assert [fact["assigned_sample_id"] for fact in merged_facts] == [
        "PAN_nanofiber_17_needles",
        "PAN_nanofiber_72_needles",
    ]
    assert [fact["candidate_sample_ids"] for fact in merged_facts] == [
        ["PAN_nanofiber_17_needles"],
        ["PAN_nanofiber_72_needles"],
    ]
    cards_by_id = {card["sample_id"]: card for card in merged_cards}
    assert set(cards_by_id) == {
        "PAN_nanofiber_17_needles",
        "PAN_nanofiber_72_needles",
    }
    assert cards_by_id["PAN_nanofiber_17_needles"]["variable_value"] == "10"
    assert cards_by_id["PAN_nanofiber_72_needles"]["variable_value"] == "5"


def test_contextual_generic_composite_aliases_resolve_to_unique_result_variant():
    holistic = [
        {"sample_id": "TPU", "aliases": ["TPU material"]},
        {
            "sample_id": "TPMS_TPU_CF_5vol%",
            "variable_name": "fiber volume fraction",
            "variable_value": "5",
            "variable_unit": "%",
        },
    ]
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPU_CF_10vol%",
            "candidate_sample_ids": ["TPU_CF_10vol%"],
            "metric_or_parameter": "density",
            "value": "1257",
            "unit": "kg/m3",
            "evidence_text": "At 10% fiber content, the density was 1257 kg/m3.",
            "_source_block_id": "B43",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "fiber-reinforced composite material",
            "candidate_sample_ids": ["fiber-reinforced composite material"],
            "metric_or_parameter": "softening_load",
            "value": "430",
            "unit": "N",
            "evidence_text": "The fiber-reinforced composite material softened at 430 N.",
            "_source_block_id": "B43",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPU material structure",
            "candidate_sample_ids": ["TPU material structure"],
            "metric_or_parameter": "softening_load",
            "value": "350",
            "unit": "N",
            "evidence_text": "The TPU material structure softened at 350 N.",
            "_source_block_id": "B43",
        },
    ]
    cards = [
        {"sample_id": "TPU", "sample_aliases": ""},
        {"sample_id": "TPMS_TPU_CF_5vol%", "sample_aliases": ""},
        {"sample_id": "TPU_CF_10vol%", "sample_aliases": ""},
        {"sample_id": "fiber-reinforced composite material", "sample_aliases": ""},
        {"sample_id": "TPU material structure", "sample_aliases": ""},
    ]

    _, merged_facts, merged_cards = merge_sample_identities(
        [], facts, cards, holistic_samples=holistic,
    )

    assert [fact["assigned_sample_id"] for fact in merged_facts] == [
        "TPU_CF_10vol%",
        "TPU_CF_10vol%",
        "TPU",
    ]
    cards_by_id = {card["sample_id"]: card for card in merged_cards}
    assert set(cards_by_id) == {"TPU", "TPMS_TPU_CF_5vol%", "TPU_CF_10vol%"}
    assert "fiber-reinforced composite material" in cards_by_id["TPU_CF_10vol%"]["sample_aliases"]
    assert "fiber-reinforced composite material" in merged_facts[1]["_sample_aliases"]


def test_unique_active_fraction_variant_absorbs_unsuffixed_structure_id():
    samples = [
        {
            "sample_id": "TPU_T300_CF_P-type_TPMS",
            "aliases": ["P-type TPMS mechanical metamaterial structure"],
            "material_system": "TPU/T300 carbon fiber composite",
            "fiber_type": "bulk",
        },
        {
            "sample_id": "TPU_T300_CF_P-type_TPMS_10vol%",
            "aliases": ["fiber-reinforced structure material"],
            "variable_name": "loading",
            "variable_value": "10",
            "variable_unit": "vol%",
        },
    ]
    cards = [
        {**sample, "sample_aliases": sample["aliases"]}
        for sample in samples
    ]
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": samples[1]["sample_id"],
            "candidate_sample_ids": [samples[1]["sample_id"]],
            "metric_or_parameter": "density",
            "value": "1257",
            "unit": "kg m^-3",
            "evidence_text": "At 10vol% the TPMS density was 1257 kg m^-3.",
            "_source_block_id": "B43",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": samples[0]["sample_id"],
            "candidate_sample_ids": [samples[0]["sample_id"]],
            "metric_or_parameter": "softening_load",
            "value": "430",
            "unit": "N",
            "evidence_text": "The fiber-reinforced TPMS softened at 430 N.",
            "_source_block_id": "B43",
        },
    ]

    _, merged_facts, merged_cards = merge_sample_identities(
        [], facts, cards, holistic_samples=samples,
    )

    target = "TPU_T300_CF_P-type_TPMS_10vol%"
    assert {fact["assigned_sample_id"] for fact in merged_facts} == {target}
    assert {card["sample_id"] for card in merged_cards} == {target}


def test_unique_tpms_variant_absorbs_shorter_contextual_composition_reference():
    base = {
        "sample_id": "TPU_T300_CF_TPMS",
        "aliases": ["P-type TPMS mechanical metamaterial structure"],
        "material_system": "TPU/T300 carbon fiber composite",
    }
    variant = {
        "sample_id": "TPU_T300_CF_TPMS_10vol%",
        "aliases": ["fiber-reinforced structure material"],
        "variable_name": "loading",
        "variable_value": "10",
        "variable_unit": "vol%",
    }
    generic = "TPU_TPMS_mechanical_metamaterial"
    cards = [
        {**base, "sample_aliases": base["aliases"]},
        {**variant, "sample_aliases": variant["aliases"]},
        {"sample_id": generic, "sample_aliases": []},
    ]
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": variant["sample_id"],
            "candidate_sample_ids": [variant["sample_id"]],
            "metric_or_parameter": "density",
            "value": "1257",
            "unit": "kg m^-3",
            "evidence_text": "At 10vol% the TPMS density was 1257 kg m^-3.",
            "_source_block_id": "B43",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": generic,
            "candidate_sample_ids": [generic],
            "metric_or_parameter": "bandgap_frequency_range",
            "value": "1050-1400",
            "unit": "Hz",
            "evidence_text": "The TPMS structure has a bandgap from 1050 to 1400 Hz.",
            "_source_block_id": "B76",
        },
    ]

    _, merged_facts, merged_cards = merge_sample_identities(
        [],
        facts,
        cards,
        holistic_samples=[base],
    )

    target = variant["sample_id"]
    assert {fact["assigned_sample_id"] for fact in merged_facts} == {target}
    assert generic not in {card["sample_id"] for card in merged_cards}


def test_multiple_active_fraction_variants_remain_distinct():
    base = "TPU_T300_CF_P-type_TPMS"
    five = f"{base}_5vol%"
    ten = f"{base}_10vol%"
    cards = [
        {"sample_id": base, "sample_aliases": ""},
        {"sample_id": five, "sample_aliases": ""},
        {"sample_id": ten, "sample_aliases": ""},
    ]
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": sid,
            "candidate_sample_ids": [sid],
            "metric_or_parameter": "density",
            "value": value,
            "unit": "kg m^-3",
            "evidence_text": f"The {fraction} structure had density {value} kg m^-3.",
            "_source_block_id": f"B{index}",
        }
        for index, (sid, fraction, value) in enumerate(
            [(five, "5vol%", "1230"), (ten, "10vol%", "1257")],
            1,
        )
    ]

    _, merged_facts, merged_cards = merge_sample_identities([], facts, cards)

    assert {fact["assigned_sample_id"] for fact in merged_facts} == {five, ten}
    assert {card["sample_id"] for card in merged_cards} == {base, five, ten}


def test_compact_vol_variants_merge_and_prefer_descriptive_identity():
    mentions = [
        {"normalized_sample_id": "TPU_fiber_reinforced_10vol", "aliases": []},
        {"normalized_sample_id": "TPU_fiber_10vol", "aliases": []},
    ]

    alias_map = build_sample_alias_map(mentions)

    assert alias_map["TPU_fiber_reinforced_10vol"] == "TPU_fiber_reinforced_10vol"
    assert alias_map["TPU_fiber_10vol"] == "TPU_fiber_reinforced_10vol"


def test_explicit_composition_chains_do_not_merge_through_bad_aliases():
    samples = [
        {"sample_id": "PCL/AA", "aliases": ["PCL_AA_fibers", "PCL/AA/S"]},
        {"sample_id": "PCL/AA/S", "aliases": ["PCL_AA_S", "PCL/AA"]},
        {"sample_id": "PCL/AA/SBCu", "aliases": ["PCL_AA_SBCu", "PCL/AA/S"]},
    ]

    alias_map = build_sample_alias_map([], holistic_samples=samples)

    assert alias_map["PCL/AA"] != alias_map["PCL/AA/S"]
    assert alias_map["PCL/AA/S"] != alias_map["PCL/AA/SBCu"]
    assert alias_map["PCL/AA"] != alias_map["PCL/AA/SBCu"]


def test_descriptive_fiber_ids_merge_only_with_their_unique_composition_alias():
    samples = [
        {
            "sample_id": "PCL_fiber",
            "aliases": ["PCL/AA"],
            "fiber_type": "nanofiber",
        },
        {
            "sample_id": "PCL_S_composite_fiber",
            "aliases": ["PCL/AA/S"],
            "fiber_type": "nanofiber",
        },
        {
            "sample_id": "PCL_SBCu_composite_fiber",
            "aliases": ["PCL/AA/SBCu"],
            "fiber_type": "nanofiber",
        },
    ]

    alias_map = build_sample_alias_map([], holistic_samples=samples)

    assert alias_map["PCL_fiber"] == "PCL/AA"
    assert alias_map["PCL_S_composite_fiber"] == "PCL/AA/S"
    assert alias_map["PCL_SBCu_composite_fiber"] == "PCL/AA/SBCu"
    assert len({alias_map[sample["sample_id"]] for sample in samples}) == 3


def test_explicit_table_compositions_survive_contextual_alias_repair():
    holistic = [{
        "sample_id": "PCL/AA",
        "aliases": ["neat PCL fibers", "PCL/AA/S", "PCL/AA/SBCu"],
        "fiber_type": "nanofiber",
    }]
    mentions = [
        {"normalized_sample_id": "PCL/AA", "aliases": ["PCL/AA/S", "PCL/AA/SBCu"]},
        {"normalized_sample_id": "PCL/AA/S", "aliases": []},
        {"normalized_sample_id": "PCL/AA/SBCu", "aliases": []},
        {"normalized_sample_id": "PCL_S_BG_fiber", "aliases": []},
        {"normalized_sample_id": "PCL_SBCu_BG_fiber", "aliases": []},
    ]
    cards = [
        {
            "sample_id": "PCL/AA",
            "sample_aliases": ["PCL/AA/S", "PCL/AA/SBCu"],
            "fiber_type": "nanofiber",
        },
        {"sample_id": "PCL/AA/S", "sample_aliases": ""},
        {"sample_id": "PCL/AA/SBCu", "sample_aliases": ""},
        {
            "sample_id": "PCL_S_BG_fiber",
            "sample_aliases": "",
            "fiber_type": "nanofiber",
            "material_system": "PCL/S bioactive glass composite",
        },
        {
            "sample_id": "PCL_SBCu_BG_fiber",
            "sample_aliases": "",
            "fiber_type": "nanofiber",
            "material_system": "PCL/SBCu bioactive glass composite",
        },
    ]
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "PCL/AA/S",
            "candidate_sample_ids": ["PCL/AA/S"],
            "metric_or_parameter": "tensile_strength",
            "value": "2.4",
            "unit": "MPa",
            "evidence_text": "[row 2] PCL/AA/S 2.4 MPa",
            "_source_block_id": "B1",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "PCL/AA/SBCu",
            "candidate_sample_ids": ["PCL/AA/SBCu"],
            "metric_or_parameter": "tensile_strength",
            "value": "2",
            "unit": "MPa",
            "evidence_text": "[row 3] PCL/AA/SBCu 2 MPa",
            "_source_block_id": "B1",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "PCL_S_BG_fiber",
            "candidate_sample_ids": ["PCL_S_BG_fiber"],
            "metric_or_parameter": "water_contact_angle",
            "value": "93.4",
            "unit": "degree",
            "evidence_text": "PCL/AA/S had a contact angle of 93.4 degrees.",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "PCL_SBCu_BG_fiber",
            "candidate_sample_ids": ["PCL_SBCu_BG_fiber"],
            "metric_or_parameter": "water_contact_angle",
            "value": "97.5",
            "unit": "degree",
            "evidence_text": "PCL/AA/SBCu had a contact angle of 97.5 degrees.",
        },
    ]

    _, merged_facts, merged_cards = merge_sample_identities(
        mentions, facts, cards, holistic_samples=holistic,
    )

    assert [fact["assigned_sample_id"] for fact in merged_facts] == [
        "PCL/AA/S",
        "PCL/AA/SBCu",
        "PCL/AA/S",
        "PCL/AA/SBCu",
    ]
    cards_by_id = {card["sample_id"]: card for card in merged_cards}
    assert set(cards_by_id) == {"PCL/AA", "PCL/AA/S", "PCL/AA/SBCu"}
    assert cards_by_id["PCL/AA/S"]["material_system"] == (
        "PCL/S bioactive glass composite"
    )
    assert cards_by_id["PCL/AA/SBCu"]["material_system"] == (
        "PCL/SBCu bioactive glass composite"
    )


def test_bare_source_composition_id_beats_form_suffixed_alias():
    samples = [{
        "sample_id": "PCL/AA/S fibers",
        "aliases": ["PCL/AA/S"],
        "fiber_type": "nanofiber",
    }]

    alias_map = build_sample_alias_map([], holistic_samples=samples)

    assert alias_map["PCL/AA/S fibers"] == "PCL/AA/S"


def test_apply_alias_map_drops_aliases_owned_by_another_composition():
    samples = [
        {"sample_id": "PCL/AA", "aliases": ["PCL/AA/S"]},
        {"sample_id": "PCL/AA/S", "aliases": ["PCL/AA"]},
    ]
    alias_map = build_sample_alias_map([], holistic_samples=samples)

    _, _, cards = apply_sample_alias_map(
        alias_map,
        sample_cards=[
            {"sample_id": "PCL/AA", "sample_aliases": ["PCL/AA/S"]},
            {"sample_id": "PCL/AA/S", "sample_aliases": ["PCL/AA"]},
        ],
    )

    assert {card["sample_id"] for card in cards} == {"PCL/AA", "PCL/AA/S"}
    assert all(not card["sample_aliases"] for card in cards)


def test_bulk_glass_does_not_merge_with_composite_fiber_through_short_alias():
    samples = [
        {
            "sample_id": "SBCu_BG",
            "aliases": ["SBCu"],
            "fiber_type": "bulk",
            "material_system": "bioactive glass powder",
        },
        {
            "sample_id": "PCL_AA_SBCu_fibers",
            "aliases": ["SBCu", "SBCu-composite fibers"],
            "fiber_type": "nanofiber",
            "material_system": "PCL/SBCu bioactive glass",
        },
        {
            "sample_id": "PCL/AA/SBCu",
            "aliases": ["PCL_AA_SBCu_fibers"],
            "fiber_type": "nanofiber",
            "material_system": "PCL/SBCu bioactive glass",
        },
    ]

    alias_map = build_sample_alias_map([], holistic_samples=samples)

    assert alias_map["SBCu_BG"] != alias_map["PCL/AA/SBCu"]
    assert alias_map["PCL_AA_SBCu_fibers"] == alias_map["PCL/AA/SBCu"]


def test_generic_reinforced_structure_merges_into_unique_specific_metamaterial():
    holistic = [{
        "sample_id": "TPU/T300_CF_P-type_TPMS",
        "aliases": ["P-type TPMS mechanical metamaterial"],
        "material_system": "TPU/T300 carbon fiber",
        "fiber_type": "bulk",
    }]
    cards = [
        {**holistic[0], "sample_aliases": holistic[0]["aliases"]},
        {
            "sample_id": "fiber-reinforced structure material",
            "sample_aliases": [],
        },
    ]
    facts = [{
        "fact_type": "performance",
        "assigned_sample_id": "fiber-reinforced structure material",
        "metric_or_parameter": "compressive_displacement",
        "value": "8.8",
        "unit": "mm",
        "evidence_text": "The fiber-reinforced structure material displaced 8.8 mm.",
    }]

    _, merged_facts, merged_cards = merge_sample_identities(
        [], facts, cards, holistic_samples=holistic
    )

    assert {card["sample_id"] for card in merged_cards} == {
        "TPU/T300_CF_P-type_TPMS"
    }
    assert merged_facts[0]["assigned_sample_id"] == "TPU/T300_CF_P-type_TPMS"


def test_partial_fallback_aliases_merge_into_unique_holistic_metamaterial():
    target = "TPU/T300 carbon fiber TPMS metamaterial"
    holistic = [
        {"sample_id": "TPU", "aliases": ["TPU material"]},
        {"sample_id": "T300 carbon fiber", "aliases": ["T300 CF"]},
        {
            "sample_id": target,
            "aliases": ["P-type TPMS mechanical metamaterial"],
            "material_system": "TPU/T300 carbon fiber",
        },
    ]
    cards = [
        *[{**sample, "sample_aliases": sample.get("aliases", [])} for sample in holistic],
        {"sample_id": "fiber-reinforced structure material", "sample_aliases": []},
        {"sample_id": "TPU-fiber-reinforced material", "sample_aliases": []},
    ]
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "fiber-reinforced structure material",
            "metric_or_parameter": "compressive_displacement",
            "value": "8.8",
            "unit": "mm",
            "evidence_text": "The fiber-reinforced structure displaced 8.8 mm.",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPU-fiber-reinforced material",
            "metric_or_parameter": "maximum_acceleration",
            "value": "37",
            "unit": "dimensionless",
            "evidence_text": "The TPU-fiber-reinforced material reached 37.",
        },
    ]

    _, merged_facts, merged_cards = merge_sample_identities(
        [], facts, cards, holistic_samples=holistic
    )

    assert {card["sample_id"] for card in merged_cards} == {
        "TPU", "T300 carbon fiber", target,
    }
    assert {fact["assigned_sample_id"] for fact in merged_facts} == {target}


def test_model_owned_treated_alias_does_not_merge_with_untreated_base():
    holistic = [
        {
            "sample_id": "S_BG",
            "aliases": ["S BG", "AA-treated S BG"],
            "material_system": "77S bioactive glass",
        },
        {
            "sample_id": "SBCu_BG",
            "aliases": ["SBCu BG", "AA-treated SBCu BG"],
            "material_system": "B- and Cu-doped bioactive glass",
        },
        {"sample_id": "AA-treated S BG", "aliases": ["AA_treated_S_BG"]},
        {"sample_id": "AA-treated SBCu BG", "aliases": ["AA_treated_SBCu_BG"]},
    ]
    cards = [
        {**sample, "sample_aliases": sample.get("aliases", [])}
        for sample in holistic
    ]

    _, _, merged_cards = merge_sample_identities(
        [], [], cards, holistic_samples=holistic
    )

    assert {card["sample_id"] for card in merged_cards} == {
        "S_BG",
        "SBCu_BG",
        "AA-treated S BG",
        "AA-treated SBCu BG",
    }
    base_aliases = {
        card["sample_id"]: set(parse_sample_aliases(card.get("sample_aliases")))
        for card in merged_cards
    }
    assert "AA-treated S BG" not in base_aliases["S_BG"]
    assert "AA-treated SBCu BG" not in base_aliases["SBCu_BG"]


def test_treated_particle_name_order_variants_merge_without_merging_base():
    cards = [
        {"sample_id": "S BG particles", "sample_aliases": ["S BG"]},
        {
            "sample_id": "S BG particles AA-treated",
            "sample_aliases": ["S AA-treated"],
        },
        {"sample_id": "AA-treated S BG", "sample_aliases": []},
        {"sample_id": "SBCu BG particles", "sample_aliases": ["SBCu BG"]},
        {
            "sample_id": "SBCu BG particles AA-treated",
            "sample_aliases": ["SBCu AA-treated"],
        },
        {"sample_id": "AA-treated SBCu BG", "sample_aliases": []},
    ]

    _, _, merged_cards = merge_sample_identities([], [], cards)
    merged_ids = {card["sample_id"] for card in merged_cards}

    assert len(merged_ids) == 4
    assert any("S BG" in sample_id and "treated" not in sample_id.lower() for sample_id in merged_ids)
    assert any("SBCu BG" in sample_id and "treated" not in sample_id.lower() for sample_id in merged_ids)
    treated_cards = [
        card for card in merged_cards if "treated" in card["sample_id"].lower()
    ]
    assert len(treated_cards) == 2
    treated_aliases = {
        alias
        for card in treated_cards
        for alias in parse_sample_aliases(card.get("sample_aliases"))
    }
    assert "AA-treated S BG" in treated_aliases or any(
        card["sample_id"] == "AA-treated S BG" for card in treated_cards
    )
    assert "AA-treated SBCu BG" in treated_aliases or any(
        card["sample_id"] == "AA-treated SBCu BG" for card in treated_cards
    )


def test_system_results_move_to_unique_metamaterial_without_fraction_id():
    cards = [
        {"sample_id": "TPU", "sample_aliases": ["TPU material"]},
        {"sample_id": "T300 carbon fiber", "sample_aliases": ["T300 CF"]},
        {
            "sample_id": "TPU/T300_CF_P-type_TPMS",
            "sample_aliases": ["P-type TPMS mechanical metamaterial"],
        },
    ]
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "",
            "metric_or_parameter": "bandgap_frequency_range",
            "value": "1050-1400",
            "unit": "Hz",
            "evidence_text": "The TPMS structure has a bandgap from 1050 to 1400 Hz.",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPU",
            "metric_or_parameter": "transmission_attenuation_frequency_range",
            "value": "1250-1500",
            "unit": "Hz",
            "evidence_text": "The TPU-fiber-reinforced material attenuated 1250-1500 Hz.",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPU",
            "metric_or_parameter": "maximum_acceleration",
            "value": "69",
            "unit": "dimensionless",
            "evidence_text": "The TPU had a maximum acceleration of 69.",
        },
    ]

    repaired = repair_contextual_fact_assignments(facts, cards)

    assert repaired[0]["assigned_sample_id"] == "TPU/T300_CF_P-type_TPMS"
    assert repaired[1]["assigned_sample_id"] == "TPU/T300_CF_P-type_TPMS"
    assert repaired[2]["assigned_sample_id"] == "TPU"


def test_system_result_moves_from_unsupported_base_to_unique_active_variant():
    holistic = [{"sample_id": "TPU_matrix", "aliases": ["matrix material"]}]
    cards = [
        {"sample_id": "TPU_matrix", "sample_aliases": ["matrix material"]},
        {"sample_id": "TPU_fiber_reinforced_10vol", "sample_aliases": []},
    ]
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPU_fiber_reinforced_10vol",
            "metric_or_parameter": "density",
            "value": "1257",
            "unit": "kg/m3",
            "evidence_text": "The 10% reinforced structure had a density of 1257 kg/m3.",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPU_matrix",
            "metric_or_parameter": "eigenfrequency",
            "value": "919",
            "unit": "Hz",
            "evidence_text": "The eigenfrequency of mode 1 was 919 Hz.",
        },
    ]

    _, merged_facts, _ = merge_sample_identities(
        [], facts, cards, holistic_samples=holistic,
    )

    assert merged_facts[1]["assigned_sample_id"] == "TPU_fiber_reinforced_10vol"
    assert "unique_active_variant_for_system_result" in merged_facts[1]["assignment_reason"]


def test_contextual_repair_ignores_short_formula_aliases_and_moves_structure_properties():
    cards = [
        {"sample_id": "TPU_matrix", "sample_aliases": ["Em", "matrix material"]},
        {
            "sample_id": "T300_carbon_fiber",
            "sample_aliases": ["Ef", "reinforcing phase material"],
        },
        {"sample_id": "TPU_T300_CF_TPMS_5vol", "sample_aliases": []},
        {"sample_id": "TPU_T300_CF_TPMS_15vol", "sample_aliases": []},
        {"sample_id": "TPU_fiber_reinforced_TPMS_10vol", "sample_aliases": []},
    ]
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPU_fiber_reinforced_TPMS_10vol",
            "metric_or_parameter": "softening_load",
            "value": "430",
            "unit": "N",
            "evidence_text": "The 10% fiber-reinforced TPMS softened at 430 N.",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "T300_carbon_fiber",
            "subject_text": "T300 carbon fiber",
            "metric_or_parameter": "bandgap_frequency_range",
            "value": "1050-1400",
            "unit": "Hz",
            "evidence_text": (
                "Therefore, we mark the eigenfrequencies and observe a directional "
                "bandgap from 1050 to 1400 Hz in the mechanical metamaterial."
            ),
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPU_matrix",
            "subject_text": "TPU matrix",
            "metric_or_parameter": "density",
            "value": "1257",
            "unit": "kg/m3",
            "evidence_text": "The density of the TPMS structure is 1257 kg/m3.",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPU_matrix",
            "metric_or_parameter": "density",
            "value": "1200",
            "unit": "kg/m3",
            "evidence_text": "The matrix material has a density of 1200 kg/m3.",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "T300_carbon_fiber",
            "metric_or_parameter": "Youngs_modulus",
            "value": "230",
            "unit": "GPa",
            "evidence_text": "The reinforcing phase material has a modulus of 230 GPa.",
        },
    ]

    repaired = repair_contextual_fact_assignments(facts, cards)

    target = "TPU_fiber_reinforced_TPMS_10vol"
    assert repaired[1]["assigned_sample_id"] == target
    assert repaired[2]["assigned_sample_id"] == target
    assert repaired[3]["assigned_sample_id"] == "TPU_matrix"
    assert repaired[4]["assigned_sample_id"] == "T300_carbon_fiber"
