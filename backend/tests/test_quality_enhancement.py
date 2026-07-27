"""Quality enhancement regression tests."""

from app.services.extractor_v7.quality_enhancement import (
    apply_fact_quality_enhancements,
    classify_export_tier,
    detect_unit_conflict,
    infer_paper_theme,
    normalize_sample_display_name,
    remap_loss_tangent_metric,
    restructure_loading_cycles_fact,
    should_reject_emi_shielding_fact,
)
from app.services.extractor_v7.metric_normalize import canonicalize_metric_name


def test_loss_tangent_canonical_name():
    assert canonicalize_metric_name("loss tangent") == "loss_tangent"
    assert canonicalize_metric_name("tan delta") == "loss_tangent"
    assert canonicalize_metric_name("dielectric loss") == "dielectric_loss"


def test_loss_tangent_remap_does_not_contaminate_neighbor_metrics():
    fact = {
        "fact_type": "performance",
        "metric_or_parameter": "cold_crystallization_temperature",
        "value": "98.02",
        "unit": "°C",
        "evidence_text": (
            "Table 2 reports Tcc=98.02 °C. The nearby paragraph also "
            "discusses tan δ behavior."
        ),
    }

    out = remap_loss_tangent_metric(fact)

    assert out["metric_or_parameter"] == "cold_crystallization_temperature"


def test_dielectric_loss_label_is_remapped_when_evidence_explicitly_says_tan_delta():
    fact = {
        "fact_type": "performance",
        "metric_or_parameter": "dielectric_loss",
        "value": "0.02",
        "unit": "dimensionless",
        "evidence_text": "The loss tangent (tan δ) was 0.02 at 1 kHz.",
    }

    out = remap_loss_tangent_metric(fact)

    assert out["metric_or_parameter"] == "loss_tangent"


def test_restructure_loading_cycles_moves_count_to_condition():
    fact = {
        "fact_type": "performance",
        "metric_or_parameter": "loading_unloading_cycles",
        "value": "500",
        "unit": "cycles",
        "evidence_text": "500 compression cycles at 50% strain with no stress decay",
        "condition": "",
    }
    out = restructure_loading_cycles_fact(fact)
    assert out["metric_or_parameter"] == "cyclic_compression_stability"
    assert out["value"] == "no stress decay"
    assert "500 compression cycles" in out["condition"]


def test_emi_se_filtered_for_transparent_paper():
    fact = {
        "fact_type": "performance",
        "metric_or_parameter": "electromagnetic_interference_shielding_effectiveness",
        "value": "47.8",
        "unit": "dB",
        "evidence_text": "reported SE values up to 47.8 dB in previous studies [12]",
        "_chunk_section": "introduction",
    }
    themes = {"low_dielectric_transparent"}
    assert should_reject_emi_shielding_fact(fact, themes)


def test_background_intro_fact_marked_tier_c():
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "density",
        "value": "15",
        "unit": "mg/cm3",
        "evidence_text": "Previously reported aerogels showed density of 15 mg cm-3 [8]",
        "_chunk_section": "introduction",
        "assigned_sample_id": "PI-200°C",
    }]
    out = apply_fact_quality_enhancements(facts, chunks=[{
        "section_name": "introduction",
        "raw_text": "Previously reported aerogels showed density of 15 mg cm-3 [8]",
    }])
    assert out[0].get("_export_tier") == "C"


def test_short_chunk_label_cannot_override_source_block_section():
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "fiber_diameter",
        "value": "296",
        "unit": "nm",
        "evidence_text": (
            "When graphene is added, the average nanofiber diameter is 296 nm."
        ),
        "assigned_sample_id": "PES_0.5G nanofiber membrane",
        "source_location": "page 5, Fig. 2b",
        "_source_block_id": "B000086",
    }]
    out = apply_fact_quality_enhancements(
        facts,
        chunks=[
            {
                "block_id": "B000001",
                "section_name": "introduction",
                "raw_text": "a",
            },
            {
                "block_id": "B000086",
                "section_name": "results",
                "raw_text": facts[0]["evidence_text"],
            },
        ],
    )

    assert out[0]["_chunk_section"] == "results"
    assert out[0]["_data_source_type"] == "paper_core_result"
    assert out[0]["_export_tier"] != "C"


def test_source_block_context_uses_document_context_source_block_id():
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "density",
        "value": "1.57",
        "unit": "g/cc",
        "evidence_text": "The measured density was 1.57 g/cc.",
        "assigned_sample_id": "Cf/PEEK filament",
        "_source_block_id": "B000030",
    }]

    out = apply_fact_quality_enhancements(
        facts,
        chunks=[{
            "source_block_id": "B000030",
            "section_name": "experimental",
            "raw_text": (
                "The Cf/PEEK filament was prepared from continuous carbon "
                "fiber tow. The measured density was 1.57 g/cc."
            ),
        }],
    )

    assert "[source block context] B000030" in out[0]["evidence_text"]
    assert "sample_id_not_found_in_evidence" not in out[0].get(
        "_checklist_failures", []
    )


def test_sample_form_mismatch_tensile_on_aerogel_is_tier_b():
    fact = {
        "fact_type": "performance",
        "metric_or_parameter": "tensile_strength",
        "value": "2.82",
        "unit": "MPa",
        "evidence_text": "2MZ-AZINE-PI3 aerogel tensile strength 2.82 MPa",
        "assigned_sample_id": "2MZ-AZINE-PI3 aerogel",
        "source_location": "p.5, Fig. 3",
    }
    assert classify_export_tier(fact) == "B"


def test_unit_conflict_for_aerogel_mpa():
    fact = {
        "metric_or_parameter": "compressive_stress",
        "value": "7.13",
        "unit": "MPa",
        "evidence_text": "PI1 aerogel compressive stress 7.13 MPa, figure axis in kPa",
        "assigned_sample_id": "PI1 aerogel",
    }
    assert detect_unit_conflict(fact)


def test_normalize_pi1_display_name():
    assert normalize_sample_display_name("PI1") == "PI1 aerogel"


def test_infer_transparent_theme_from_title():
    themes = infer_paper_theme(
        chunks=[{"section_name": "title_abstract", "raw_text": "Electromagnetic wave-transparent PI aerogels with low dielectric loss"}],
        paper_metadata={"paper_title": "Low dielectric PI aerogel"},
    )
    assert "low_dielectric_transparent" in themes


def test_unique_matching_figure_caption_rebinds_fact_to_known_sample():
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "pore_size",
        "value": "18.72",
        "unit": "nm",
        "assigned_sample_id": "PP_PVA_geopolymer_composite",
        "candidate_sample_ids": ["PP_PVA_geopolymer_composite"],
        "assignment_status": "assigned",
        "assignment_confidence": 0.62,
        "source_location": "p.9, Fig. 11",
        "evidence_text": (
            "After sulfate exposure, the most probable pore diameter was "
            "18.72 nm."
        ),
    }]
    caption = (
        "Fig. 11. Porosity and pore size distribution of geopolymer "
        "composites A3 reinforced with PVA and PP fibers."
    )

    out = apply_fact_quality_enhancements(
        facts,
        chunks=[{
            "source_type": "figure_caption",
            "section_name": "results",
            "raw_text": caption,
        }],
        sample_cards=[{"sample_id": "A3"}, {"sample_id": "B9"}],
    )

    assert out[0]["assigned_sample_id"] == "A3"
    assert out[0]["candidate_sample_ids"] == ["A3"]
    assert out[0]["assignment_confidence"] == 0.97
    assert "figure_caption_sample_anchor" in out[0]["assignment_reason"]
    assert caption in out[0]["evidence_text"]


def test_ambiguous_figure_caption_does_not_force_sample_rebinding():
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "porosity",
        "value": "20",
        "unit": "%",
        "assigned_sample_id": "geopolymer_composite",
        "source_location": "p.10, Fig. 13",
        "evidence_text": "The porosity was approximately 20%.",
    }]

    out = apply_fact_quality_enhancements(
        facts,
        chunks=[{
            "source_type": "figure_caption",
            "section_name": "results",
            "raw_text": "Fig. 13. Comparison of pore structures for A3 and B9.",
        }],
        sample_cards=[{"sample_id": "A3"}, {"sample_id": "B9"}],
    )

    assert out[0]["assigned_sample_id"] == "geopolymer_composite"


def test_collective_figure_result_expands_to_each_captioned_sample():
    facts = [{
        "fact_id": "F13",
        "fact_type": "performance",
        "metric_or_parameter": "pore_size",
        "value": "10",
        "unit": "nm",
        "assigned_sample_id": "geopolymer_samples",
        "source_location": "page 6, Fig. 13",
        "evidence_text": (
            "From the pore size distribution results shown in Fig. 13, the "
            "porosity change is mainly attributed to pores between 10 and 50 nm."
        ),
        "_checklist_failed": True,
        "_checklist_failures": ["sample_id_not_found_in_evidence"],
    }]
    caption = (
        "Fig. 13. Pore size percentage after sulfate exposure: "
        "(a) geopolymer composite A3, (b) geopolymer composite B9."
    )

    out = apply_fact_quality_enhancements(
        facts,
        chunks=[{
            "source_type": "figure_caption",
            "block_type": "chart",
            "raw_text": caption,
        }],
        sample_cards=[{"sample_id": "A3"}, {"sample_id": "B9"}],
    )

    assert {fact["assigned_sample_id"] for fact in out} == {"A3", "B9"}
    assert all(
        "collective_figure_caption_sample_anchor" in fact["assignment_reason"]
        for fact in out
    )
    assert all(fact.get("_checklist_failed") is False for fact in out)
    assert all(caption in fact["evidence_text"] for fact in out)


def test_figure_paragraph_can_anchor_a3_pore_result():
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "pore_size",
        "value": "20",
        "unit": "nm",
        "assigned_sample_id": "A3",
        "source_location": "Fig. 11; page 6",
        "evidence_text": (
            "The pore size of the sample exposed for 28 days in 20 wt% "
            "Na2SO4 solution does not exceed 20 nm."
        ),
        "_checklist_failed": True,
        "_checklist_failures": ["sample_id_not_found_in_evidence"],
    }]
    paragraph = (
        "Fig. 11 presents pore size distribution and porosity of composites "
        "A3 after 28 days of exposure to different sodium sulfate solutions."
    )

    out = apply_fact_quality_enhancements(
        facts,
        chunks=[{
            "source_type": "text",
            "block_type": "paragraph",
            "section_name": "results",
            "raw_text": paragraph,
        }],
        sample_cards=[{"sample_id": "A3"}, {"sample_id": "B9"}],
    )

    assert out[0]["assigned_sample_id"] == "A3"
    assert paragraph in out[0]["evidence_text"]
    assert out[0].get("_checklist_failed") is False


def test_composition_signature_rebinds_optimum_results_to_catalog_samples():
    evidence = (
        "The optimum compositions were 98% of MK, 1% of PP and 1% of PVA, "
        "and 83% of MK, 15% of WS and 2% of PVA; strength increased by 90% "
        "and 160%, respectively."
    )
    facts = [
        {
            "fact_type": "performance",
            "metric_or_parameter": "compressive_strength_improvement",
            "value": "90",
            "unit": "%",
            "assigned_sample_id": "two types of composites",
            "condition": "formulated as 98% MK, 1% PP and 1% PVA",
            "evidence_text": evidence,
            "_alignment_review_required": True,
        },
        {
            "fact_type": "performance",
            "metric_or_parameter": "compressive_strength_improvement",
            "value": "160",
            "unit": "%",
            "assigned_sample_id": "A7",
            "condition": "formulated as 83% MK, 15% WS and 2% PVA",
            "evidence_text": evidence,
            "_alignment_review_required": True,
        },
    ]
    cards = [
        {
            "sample_id": "A3",
            "sample_group_id": "G1",
            "composition_expression": "MK=98 wt%; PP=1 wt%; PVA=1 wt%",
            "evidence_text": "MK=98 wt%; PP=1 wt%; PVA=1 wt%",
            "confidence": 0.98,
        },
        {
            "sample_id": "B9",
            "sample_group_id": "G1",
            "composition_expression": "MK=83 wt%; WS=15 wt%; PVA=2 wt%",
            "evidence_text": "MK=83 wt%; WS=15 wt%; PVA=2 wt%",
            "confidence": 0.98,
        },
    ]

    out = apply_fact_quality_enhancements(facts, sample_cards=cards)

    assert [fact["assigned_sample_id"] for fact in out] == ["A3", "B9"]
    assert all(not fact.get("_alignment_review_required") for fact in out)
    assert "[sample card evidence] A3" in out[0]["evidence_text"]
    assert "[sample card evidence] B9" in out[1]["evidence_text"]


def test_optimum_composition_prefix_does_not_hide_a3_formula_match():
    evidence = (
        "The optimum composition of organic fiber reinforced geopolymer "
        "composites is formulated as 98% of MK, 1% of PP and 1% of PVA, "
        "and the compressive strength increases by 90%."
    )
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "compressive_strength_improvement",
        "value": "90",
        "unit": "%",
        "assigned_sample_id": "A2",
        "condition": "Optimum composition: 98% MK, 1% PP, 1% PVA",
        "evidence_text": evidence,
        "_alignment_review_required": True,
    }]
    cards = [
        {
            "sample_id": "A3",
            "composition_expression": "MK=98 wt%; PP=1 wt%; PVA=1 wt%",
            "evidence_text": "MK=98 wt%; PP=1 wt%; PVA=1 wt%",
        },
        {
            "sample_id": "B9",
            "composition_expression": "MK=83 wt%; PVA=2 wt%; WS=15 wt%",
            "evidence_text": "MK=83 wt%; PVA=2 wt%; WS=15 wt%",
        },
    ]

    out = apply_fact_quality_enhancements(facts, sample_cards=cards)

    assert out[0]["assigned_sample_id"] == "A3"
    assert not out[0].get("_alignment_review_required")
    assert out[0].get("_checklist_failed") is False
    assert "[sample card evidence] A3" in out[0]["evidence_text"]


def test_category_level_geopolymer_results_use_family_samples():
    evidence = (
        "The compressive and flexural strength of organic hybrid fiber "
        "reinforced geopolymer increased by more than 90% and 65%, and the "
        "compressive strength of mineral-organic hybrid fiber reinforced "
        "geopolymer increased by more than 160%."
    )
    facts = [
        {
            "fact_type": "performance",
            "metric_or_parameter": "compressive_strength_improvement",
            "value": "90",
            "unit": "%",
            "assigned_sample_id": "A2",
            "condition": "organic hybrid fiber reinforced geopolymer",
            "evidence_text": evidence,
        },
        {
            "fact_type": "performance",
            "metric_or_parameter": "compressive_strength_improvement",
            "value": "160",
            "unit": "%",
            "assigned_sample_id": "A2",
            "condition": "mineral-organic hybrid fiber reinforced geopolymer",
            "evidence_text": evidence,
        },
    ]
    cards = [{"sample_id": "A2", "sample_group_id": "G1", "confidence": 0.9}]

    out = apply_fact_quality_enhancements(facts, sample_cards=cards)

    assert out[0]["assigned_sample_id"] == (
        "organic_hybrid_fiber_reinforced_geopolymer"
    )
    assert out[1]["assigned_sample_id"] == (
        "mineral_organic_hybrid_fiber_reinforced_geopolymer"
    )
    assert {card["sample_id"] for card in cards} >= {
        "organic_hybrid_fiber_reinforced_geopolymer",
        "mineral_organic_hybrid_fiber_reinforced_geopolymer",
    }


def test_explicit_nanoparticle_property_moves_off_composite_sample():
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "particle_size",
        "value": "15",
        "unit": "nm",
        "assigned_sample_id": "GFRP_0wt_silica_coating",
        "evidence_text": (
            "Silica nanoparticles used for the coating have diameters between "
            "15 and 20 nm."
        ),
    }]
    cards = [{
        "sample_id": "GFRP_0wt_silica_coating",
        "sample_group_id": "G1",
        "confidence": 0.9,
    }]

    out = apply_fact_quality_enhancements(facts, sample_cards=cards)

    assert out[0]["assigned_sample_id"] == "silica_nanoparticles"
    assert any(card["sample_id"] == "silica_nanoparticles" for card in cards)
    assert out[0].get("_checklist_failed") is False


def test_unique_variant_metadata_appends_sample_card_grounding():
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "flexural_strength_improvement",
        "value": "53.33",
        "unit": "%",
        "assigned_sample_id": "A3",
        "condition": "hybrid fiber mass fraction increased from 1% to 2%",
        "evidence_text": (
            "When the mass fraction of hybrid fiber increases from 1% to 2%, "
            "flexural strength increases by 53.33%."
        ),
    }]
    cards = [
        {
            "sample_id": "A3",
            "sample_group_id": "G1",
            "variable_name": "hybrid organic fiber content",
            "variable_value": "2",
            "variable_unit": "wt%",
            "composition_expression": "MK=98 wt%; PP=1 wt%; PVA=1 wt%",
            "evidence_text": "MK=98 wt%; PP=1 wt%; PVA=1 wt%",
            "confidence": 0.98,
        },
        {
            "sample_id": "A6",
            "sample_group_id": "G1",
            "variable_name": "PP fiber content",
            "variable_value": "2",
            "variable_unit": "wt%",
            "confidence": 0.98,
        },
        {
            "sample_id": "A7",
            "sample_group_id": "G1",
            "variable_name": "PVA fiber content",
            "variable_value": "2",
            "variable_unit": "wt%",
            "confidence": 0.98,
        },
    ]

    out = apply_fact_quality_enhancements(facts, sample_cards=cards)

    assert "[sample card evidence] A3" in out[0]["evidence_text"]
    assert out[0].get("_checklist_failed") is False


def test_source_block_context_restores_specific_solvent_identity():
    evidence = (
        "The molecular weight and density of the solvent are 60.10 and "
        "0.785 g mL-1, respectively."
    )
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "molecular_weight",
        "value": "60.10",
        "unit": "",
        "assigned_sample_id": "isopropyl_alcohol_solvent",
        "candidate_sample_ids": ["isopropyl_alcohol_solvent"],
        "evidence_text": evidence,
        "source_location": "p.2, experimental section, block B000025",
        "_source_block_id": "B000025",
    }]
    source_block = (
        "Silica nanoparticles were dispersed into isopropyl alcohol solution. "
        + evidence
    )

    out = apply_fact_quality_enhancements(
        facts,
        chunks=[{
            "block_id": "B000025",
            "section_name": "experimental",
            "raw_text": source_block,
        }],
    )

    assert out[0].get("_checklist_failed") is False
    assert "[source block context] B000025" in out[0]["evidence_text"]
    assert "isopropyl alcohol solution" in out[0]["evidence_text"]


def test_respectively_grounded_variants_clear_stale_alignment_review():
    evidence = (
        "The bending modulus has been increased by 22.38 and 33.10% for "
        "specimens coated with 2 and 4 wt % silica concentrations."
    )
    facts = [
        {
            "fact_type": "performance",
            "metric_or_parameter": "flexural_modulus_improvement",
            "value": "22.38",
            "unit": "%",
            "assigned_sample_id": "GFRP_2wtSiO2",
            "condition": "compared with uncoated GFRP",
            "evidence_text": evidence,
            "_alignment_review_required": True,
        },
        {
            "fact_type": "performance",
            "metric_or_parameter": "flexural_modulus_improvement",
            "value": "33.10",
            "unit": "%",
            "assigned_sample_id": "GFRP_4wtSiO2",
            "condition": "compared with uncoated GFRP",
            "evidence_text": evidence,
            "_alignment_review_required": True,
        },
    ]
    cards = [
        {
            "sample_id": "GFRP_2wtSiO2",
            "sample_group_id": "G002",
            "variable_name": "silica coating concentration on glass fibers",
            "variable_value": "2",
            "variable_unit": "wt%",
            "evidence_text": "GFRP laminate prepared with 2 wt% silica coating",
        },
        {
            "sample_id": "GFRP_4wtSiO2",
            "sample_group_id": "G002",
            "variable_name": "silica coating concentration on glass fibers",
            "variable_value": "4",
            "variable_unit": "wt%",
            "evidence_text": "GFRP laminate prepared with 4 wt% silica coating",
        },
        {
            "sample_id": "silica-coated glass fibers",
            "sample_group_id": "G001",
            "variable_name": "silica coating concentration",
            "variable_value": "2",
            "variable_unit": "wt%",
            "evidence_text": "glass fibers coated with 2 wt% silica",
        },
    ]

    out = apply_fact_quality_enhancements(facts, sample_cards=cards)

    assert all(not fact.get("_alignment_review_required") for fact in out)
    assert all(fact.get("_checklist_failed") is False for fact in out)


def test_collective_result_is_grounded_to_complete_variant_group():
    evidence = (
        "Three coating concentrations were considered. Each of the three "
        "stress-strain curves can be divided into three phases. As the "
        "uniaxial stress approaches approximately 100 MPa, mechanical "
        "damage starts to appear."
    )
    sample_ids = [
        "GFRP_0wt_silica_coating",
        "GFRP_2wt_silica_coating",
        "GFRP_4wt_silica_coating",
    ]
    facts = [
        {
            "fact_type": "performance",
            "metric_or_parameter": "damage_onset_stress",
            "value": "100",
            "unit": "MPa",
            "assigned_sample_id": sample_id,
            "candidate_sample_ids": [sample_id],
            "evidence_text": evidence,
            "_checklist_failed": True,
            "_checklist_failures": ["sample_id_not_found_in_evidence"],
        }
        for sample_id in sample_ids
    ]
    cards = [
        {
            "sample_id": sample_id,
            "sample_group_id": "G002",
            "variable_name": "silica coating concentration on glass fibers",
            "variable_value": value,
            "variable_unit": "wt%",
            "composition_expression": f"GFRP with {value} wt% silica coating",
            "evidence_text": f"GFRP with {value} wt% silica coating",
        }
        for sample_id, value in zip(sample_ids, ("0", "2", "4"))
    ]

    out = apply_fact_quality_enhancements(facts, sample_cards=cards)

    assert all(fact.get("_checklist_failed") is False for fact in out)
    assert all(
        "collective_result_grounded_to_complete_variant_group"
        in fact["assignment_reason"]
        for fact in out
    )
    assert all("[sample card evidence]" in fact["evidence_text"] for fact in out)


def test_tufting_speed_ranges_become_process_conditions_on_family_sample():
    evidence = (
        "The optimum speed of tufting up to 3 mm thick preform was found to "
        "be 500 mm/min and 3 mm-6mm was 250 mm/min."
    )
    cases = [
        ("preform_up_to_3mm", "500", "preform thickness up to 3 mm"),
        ("preform_3mm_to_6mm", "250", "preform thickness 3-6 mm"),
    ]
    facts = [
        {
            "fact_type": "performance",
            "metric_or_parameter": "optimum_tufting_speed",
            "value": value,
            "unit": "mm/min",
            "condition": condition,
            "assigned_sample_id": sample_id,
            "candidate_sample_ids": [sample_id],
            "evidence_text": evidence,
            "_checklist_failed": True,
            "_checklist_failures": ["sample_id_not_found_in_evidence"],
        }
        for sample_id, value, condition in cases
    ]
    cards = [
        {"sample_id": sample_id, "evidence_text": evidence}
        for sample_id, _value, _condition in cases
    ]

    out = apply_fact_quality_enhancements(facts, sample_cards=cards)

    assert {fact["fact_type"] for fact in out} == {"process"}
    assert {fact["metric_or_parameter"] for fact in out} == {
        "tufting_robot_speed"
    }
    assert {fact["assigned_sample_id"] for fact in out} == {"tufted_preform"}
    assert {fact["condition"] for fact in out} == {
        condition for _sample_id, _value, condition in cases
    }
    assert all(not fact.get("_checklist_failed") for fact in out)
    assert {card["sample_id"] for card in cards} == {"tufted_preform"}


def test_tufting_test_and_observation_values_route_out_of_performance():
    cards = [
        {
            "sample_id": "4.08mm_QI_preform",
            "variable_name": "preform thickness",
            "variable_value": "4.08",
            "variable_unit": "mm",
            "material_system": "carbon fabric preform",
            "evidence_text": "4.08 mm QI preform",
        },
        {
            "sample_id": "6.8mm_QI_preform",
            "variable_name": "preform thickness",
            "variable_value": "6.8",
            "variable_unit": "mm",
            "material_system": "carbon fabric preform",
            "evidence_text": "6.8 mm QI preform",
        },
    ]
    facts = [
        {
            "fact_type": "performance",
            "metric_or_parameter": "needle_force_test_speed",
            "value": "100",
            "unit": "mm/min",
            "assigned_sample_id": "4.08mm_preform",
            "condition": "4.08 mm thick preform",
            "evidence_text": "Force exerted at 100 mm/min on a 4.08 mm preform.",
            "_checklist_failed": True,
        },
        {
            "fact_type": "performance",
            "metric_or_parameter": "needle_breakage_speed",
            "value": "750",
            "unit": "mm/min",
            "assigned_sample_id": "SN1.8_needle_on_6.8mm_preform",
            "condition": "6.8 mm thick preform",
            "evidence_text": "Needle breakage occurred at 750 mm/min during tufting.",
            "_checklist_failed": True,
        },
        {
            "fact_type": "performance",
            "metric_or_parameter": "damage_observation_magnification",
            "value": "10",
            "unit": "X",
            "assigned_sample_id": "6.8mm_QI_preform_with_K2_thread",
            "condition": "6.8 mm thick preform",
            "evidence_text": "Fabric yarn damage was observed at 10X magnification.",
            "_checklist_failed": True,
        },
    ]

    out = apply_fact_quality_enhancements(facts, sample_cards=cards)

    assert [fact["fact_type"] for fact in out] == [
        "process",
        "process",
        "structure",
    ]
    assert [fact["metric_or_parameter"] for fact in out] == [
        "tufting_test_speed",
        "tufting_test_speed",
        "observation_magnification",
    ]
    assert [fact["assigned_sample_id"] for fact in out] == [
        "4.08mm_QI_preform",
        "6.8mm_QI_preform",
        "6.8mm_QI_preform",
    ]
    assert all(not fact.get("_checklist_failed") for fact in out)


def test_named_ws_loading_is_composition_and_grounds_unique_b9_formula():
    evidence = (
        "The optimum constituents are 83 wt% of MK and 17 wt% of hybrid "
        "fiber (2 wt% of PVA and 15 wt% of WS)."
    )
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "fiber_volume_fraction",
        "value": "15",
        "unit": "wt%",
        "condition": (
            "total hybrid fiber content 17 wt% with 2 wt% PVA and 15 wt% WS"
        ),
        "assigned_sample_id": "B9",
        "candidate_sample_ids": ["B9"],
        "evidence_text": evidence,
        "_alignment_review_required": True,
        "_checklist_failed": True,
        "_checklist_failures": ["sample_id_not_found_in_evidence"],
    }]
    cards = [
        {
            "sample_id": "B8",
            "sample_group_id": "G001",
            "variable_name": "hybrid mineral-organic fiber content",
            "variable_value": "16",
            "variable_unit": "wt%",
            "composition_expression": "MK=84 wt%; PVA=1 wt%; WS=15 wt%",
            "evidence_text": "MK=84 wt%; PVA=1 wt%; WS=15 wt%",
        },
        {
            "sample_id": "B9",
            "sample_group_id": "G001",
            "variable_name": "hybrid mineral-organic fiber content",
            "variable_value": "17",
            "variable_unit": "wt%",
            "composition_expression": "MK=83 wt%; PVA=2 wt%; WS=15 wt%",
            "evidence_text": "MK=83 wt%; PVA=2 wt%; WS=15 wt%",
        },
        {
            "sample_id": "B10",
            "sample_group_id": "G001",
            "variable_name": "hybrid mineral-organic fiber content",
            "variable_value": "18",
            "variable_unit": "wt%",
            "composition_expression": "MK=82 wt%; PVA=3 wt%; WS=15 wt%",
            "evidence_text": "MK=82 wt%; PVA=3 wt%; WS=15 wt%",
        },
    ]

    out = apply_fact_quality_enhancements(facts, sample_cards=cards)

    assert out[0]["fact_type"] == "composition"
    assert out[0]["metric_or_parameter"] == "fiber_wt_ws"
    assert out[0]["assigned_sample_id"] == "B9"
    assert not out[0].get("_alignment_review_required")
    assert not out[0].get("_checklist_failed")
    assert "[sample card evidence] B9" in out[0]["evidence_text"]


def test_explicit_loading_code_mapping_is_rebound_as_final_fact_alias():
    mapping_text = (
        "The composites with different loading of F-CNCs "
        "(2%, 5%, 10% named C1, C2, C3, respectively) were prepared."
    )
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "decomposition_temperature",
        "value": "287",
        "unit": "°C",
        "assigned_sample_id": "PLA_FNC_5wt_filament",
        "candidate_sample_ids": ["PLA_FNC_5wt_filament"],
        "assignment_status": "assigned",
        "evidence_text": (
            "Table 5 Thermal profile of PLA, F-CNC, C1, C2, and C3.\n"
            "[columns]\tSubstrate\tT at 10% loss (°C)\n"
            "[row 4]\tC2\t287"
        ),
        "_source_table_row": 4,
        "_source_table_column": 1,
        "_table_assignment_conflict": True,
        "_table_conflicting_sample_ids": ["PLA_FNC_5wt_filament", "C2"],
        "_alignment_review_required": True,
    }]
    cards = [
        {
            "sample_id": "PLA_FNC_2wt_filament",
            "variable_name": "F-NC loading",
            "variable_value": "2",
            "variable_unit": "wt%",
        },
        {
            "sample_id": "PLA_FNC_5wt_filament",
            "variable_name": "F-NC loading",
            "variable_value": "5",
            "variable_unit": "wt%",
        },
        {
            "sample_id": "PLA_FNC_10wt_filament",
            "variable_name": "F-NC loading",
            "variable_value": "10",
            "variable_unit": "wt%",
        },
    ]

    out = apply_fact_quality_enhancements(
        facts,
        chunks=[{"block_id": "B000111", "raw_text": mapping_text}],
        sample_cards=cards,
    )

    assert "C2" in out[0]["_sample_aliases"]
    assert not out[0].get("_table_assignment_conflict")
    assert not out[0].get("_table_conflicting_sample_ids")
    assert not out[0].get("_alignment_review_required")
    assert out[0].get("_alignment_verified") is True
    assert not out[0].get("_checklist_failed")



def test_document_local_nc_alias_is_attached_only_with_repeated_definition():
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "tensile_strength",
        "value": "122",
        "unit": "MPa",
        "assigned_sample_id": "CNCs",
        "candidate_sample_ids": ["CNCs"],
        "evidence_text": "Functionalized NC reached a tensile strength of 122 MPa.",
    }]
    chunks = [{
        "source_block_id": "B000020",
        "raw_text": (
            "Nanocellulose was isolated from the fibers. NC preparation used "
            "acid hydrolysis, and the surface of NC was subsequently modified."
        ),
    }]
    cards = [{
        "sample_id": "CNCs",
        "material_system": "cellulose nanocrystals (nanocellulose)",
    }]

    out = apply_fact_quality_enhancements(
        facts,
        chunks=chunks,
        sample_cards=cards,
    )

    assert "NC" in out[0]["_sample_aliases"]
    assert not out[0].get("_checklist_failed")


def test_single_nc_mention_does_not_create_document_wide_material_alias():
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "tensile_strength",
        "value": "122",
        "unit": "MPa",
        "assigned_sample_id": "CNCs",
        "candidate_sample_ids": ["CNCs"],
        "evidence_text": "Functionalized NC reached a tensile strength of 122 MPa.",
    }]
    chunks = [{
        "source_block_id": "B000020",
        "raw_text": "Nanocellulose was isolated before functionalized NC was tested.",
    }]
    cards = [{
        "sample_id": "CNCs",
        "material_system": "cellulose nanocrystals (nanocellulose)",
    }]

    out = apply_fact_quality_enhancements(
        facts,
        chunks=chunks,
        sample_cards=cards,
    )

    assert "NC" not in out[0].get("_sample_aliases", [])
    assert "sample_id_not_found_in_evidence" in out[0].get(
        "_checklist_failures", []
    )

def test_loading_specific_rebind_distinguishes_compound_and_bicomponent_fiber():
    cards = [
        {
            "sample_id": "PLA/FR 4%",
            "variable_name": "FR content",
            "variable_value": "4",
            "variable_unit": "wt%",
            "composition_expression": "PLA compound with 4 wt% TPPO",
        },
        {
            "sample_id": "PLA/FR 4%)/(PLA) (5:5) fiber",
            "aliases": ["4 wt% TPPO bicomponent fiber"],
            "composition_expression": (
                "Sheath-core bicomponent fiber with 4 wt% TPPO in the sheath"
            ),
        },
    ]
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "PLA/FR",
            "candidate_sample_ids": ["PLA/FR 4%"],
            "metric_or_parameter": "complex_viscosity",
            "value": "1651.1",
            "unit": "Pa s",
            "condition": "4 wt% TPPO; angular frequency 1 rad s-1",
            "evidence_text": (
                "At this level, TPPO formed a pseudo-network within the PLA "
                "matrix, increasing viscosity to 1651.1 Pa s at 1 rad s-1."
            ),
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "PLA/FR",
            "candidate_sample_ids": ["PLA/FR 4%"],
            "metric_or_parameter": "elongation_at_break",
            "value": "25.02",
            "unit": "%",
            "condition": "4 wt% TPPO in sheath layer",
            "evidence_text": (
                "Optimized bicomponent fibers incorporating 4 wt% TPPO in "
                "the sheath layer exhibited elongation at break of 25.02%."
            ),
        },
    ]

    out = apply_fact_quality_enhancements(facts, sample_cards=cards)

    assert [fact["assigned_sample_id"] for fact in out] == [
        "PLA/FR 4%",
        "PLA/FR 4%)/(PLA) (5:5) fiber",
    ]
    assert all("[sample card evidence]" in fact["evidence_text"] for fact in out)
    assert all(not fact.get("_checklist_failed") for fact in out)


def test_source_block_grounds_descriptive_cnc_pla_filament_identity():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": (
            "CNC/PLA nanocomposite filament with moderate CNC loading"
        ),
        "metric_or_parameter": "tensile_strength_improvement",
        "value": "2.5",
        "unit": "%",
        "condition": "moderate amount of CNCs incorporated into PLA matrix phase",
        "evidence_text": (
            "Incorporating a moderate amount of CNCs into matrix phase "
            "increased the tensile strength of the filament by 2.5%."
        ),
        "_source_block_id": "B000145",
    }
    chunks = [{
        "source_block_id": "B000145",
        "raw_text": (
            "The resulting CNCs were embedded in PLA matrix to form a 3D "
            "printable nanocomposite filament. Incorporating a moderate amount "
            "of CNCs into matrix phase increased the tensile strength of the "
            "filament by 2.5%."
        ),
    }]

    out = apply_fact_quality_enhancements(
        [fact],
        chunks=chunks,
        sample_cards=[],
    )

    assert "[source block context] B000145" in out[0]["evidence_text"]
    assert not out[0].get("_checklist_failed")
