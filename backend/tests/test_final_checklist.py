"""Final extraction checklist tests."""

from app.services.extractor_v7.final_checklist import run_final_checklist


def test_grounded_table_run_combines_material_context_and_row_label():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "acetylated jute 1",
        "metric_or_parameter": "weight_percent_gain",
        "value": "6.55",
        "unit": "%",
        "condition": "Time=0.5 h; Temp=80 °C",
        "extraction_method": "AI_holistic_table",
        "evidence_text": (
            "The WPG of acetylated jute was measured.\n"
            "[columns]\tSample no.\tTime (h)\tWPG (%)\n"
            "[row 1]\t1\t0.5\t6.55"
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get("_checklist_failures", [])


def test_conflicting_table_assignments_are_always_routed_to_review():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "CNCs",
        "metric_or_parameter": "yield_error",
        "value": "2.6",
        "unit": "%",
        "evidence_text": "CNCs had a yield error of 2.6%.",
        "_table_assignment_conflict": True,
    }

    checked = run_final_checklist([fact])[0]

    assert "conflicting_table_sample_assignments" in checked[
        "_checklist_failures"
    ]


def test_relative_multi_metric_values_match_improvement_metrics():
    evidence = (
        "The BF-1.2 % specimen exhibited the highest reinforcing efficiency, "
        "with tensile strength, ultimate strain, and fracture energy increased "
        "by 119 %, 104 %, and 349 %, respectively, relative to the plain "
        "geopolymer composite."
    )
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "BF-1.2%",
            "metric_or_parameter": metric,
            "value": value,
            "unit": "%",
            "evidence_text": evidence,
        }
        for metric, value in (
            ("tensile_strength_improvement", "119"),
            ("ultimate_strain_improvement", "104"),
            ("fracture_energy_improvement", "349"),
        )
    ]

    checked = run_final_checklist(facts)

    for fact in checked:
        assert not any(
            failure.startswith("value_belongs_to_")
            for failure in fact.get("_checklist_failures", [])
        )


def test_relative_multi_metric_check_still_rejects_wrong_improvement_metric():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "BF-1.2%",
        "metric_or_parameter": "ultimate_strain_improvement",
        "value": "119",
        "unit": "%",
        "evidence_text": (
            "The BF-1.2 % specimen exhibited tensile strength and ultimate strain "
            "increased by 119 % and 104 %, respectively."
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert (
        "value_belongs_to_tensile_strength_improvement_not_ultimate_strain_improvement"
        in checked["_checklist_failures"]
    )


def test_grounded_bare_table_sample_uses_sample_column_and_row_label():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "sample 12",
        "metric_or_parameter": "weight_percent_gain",
        "value": "17.01",
        "unit": "%",
        "extraction_method": "AI_holistic_table",
        "evidence_text": (
            "[columns]\tSample no.\tWPG (%)\n"
            "[row 12]\t12\t17.01"
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get("_checklist_failures", [])


def test_transposed_table_axis_sample_is_grounded_by_base_and_axis():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "FRP_Warp",
        "metric_or_parameter": "tensile_strength",
        "value": "170.42",
        "unit": "MPa",
        "condition": "axis=Warp; standard_deviation=10.18 MPa",
        "extraction_method": "rule_table_performance",
        "_source_table_row": 1,
        "_source_table_column": 1,
        "evidence_text": (
            "The mechanical test results of FRP are shown in Table 4.\n"
            "[columns]\t\tWarp\tSD\tWeft\tSD\n"
            "[row 1]\tTensile strength in MPa\t170.42\t10.18\t80.62\t10.06"
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get(
        "_checklist_failures", []
    )


def test_latex_spaced_sample_identity_is_grounded():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "PES_0.5-CF/EP",
        "metric_or_parameter": "mode_I_interlaminar_fracture_toughness",
        "value": "289",
        "unit": "J/m²",
        "evidence_text": (
            "The G_IC of P E S _ { 0 . 5 ^ { - } } C F / E P "
            "was 289 J / m ^ 2."
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get(
        "_checklist_failures", []
    )


def test_loading_canonical_identity_matches_source_order_variant():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "PES_CF_EP_0.5wtG",
        "metric_or_parameter": "mode_I_interlaminar_fracture_toughness",
        "value": "289",
        "unit": "J/m²",
        "evidence_text": "The G_IC of PES_0.5-CF/EP was 289 J/m².",
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get(
        "_checklist_failures", []
    )


def test_loading_identity_does_not_match_when_loading_is_absent():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "PES_CF_EP_1wtG",
        "metric_or_parameter": "mode_I_interlaminar_fracture_toughness",
        "value": "351",
        "unit": "J/m²",
        "evidence_text": "The G_IC of PES-CF/EP was 351 J/m².",
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" in checked["_checklist_failures"]


def test_incorporated_material_identity_is_grounded_by_bound_phrase_parts():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "TPPO-incorporated FR PLA compound chips",
        "metric_or_parameter": "decomposition_temperature",
        "value": "349.18",
        "unit": "°C",
        "evidence_text": (
            "Figure 5 illustrates the thermal stability of FR PLA compound chips. "
            "Upon TPPO incorporation, T-5% decreases to 349.18 °C."
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get(
        "_checklist_failures", []
    )


def test_incorporated_material_identity_requires_treatment_binding():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "TPPO-incorporated FR PLA compound chips",
        "metric_or_parameter": "decomposition_temperature",
        "value": "349.18",
        "unit": "°C",
        "evidence_text": (
            "The FR PLA compound chips decomposed at 349.18 °C. "
            "TPPO was screened in a separate experiment."
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" in checked["_checklist_failures"]


def test_cf_c_notation_grounds_carbon_carbon_composite_only():
    evidence = (
        "The Cf-C samples were porous, and the porosity was measured "
        "to be about 40 vol.%."
    )
    carbon_carbon = {
        "fact_type": "performance",
        "assigned_sample_id": "C/C_composite",
        "metric_or_parameter": "porosity",
        "value": "40",
        "unit": "%",
        "evidence_text": evidence,
    }
    silicon_carbide = {
        **carbon_carbon,
        "assigned_sample_id": "C/C-SiC_composite",
    }

    checked_carbon, checked_silicon = run_final_checklist([
        carbon_carbon,
        silicon_carbide,
    ])

    assert "sample_id_not_found_in_evidence" not in checked_carbon.get(
        "_checklist_failures", []
    )
    assert "sample_id_not_found_in_evidence" in checked_silicon[
        "_checklist_failures"
    ]


def test_control_suffix_is_supported_by_explicit_base_identity():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "CF_EP_control",
        "metric_or_parameter": "flexural_strength",
        "value": "1663",
        "unit": "MPa",
        "evidence_text": "The flexural strength of CF/EP was 1663 MPa.",
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get(
        "_checklist_failures", []
    )


def test_grounded_s_prefixed_table_run_uses_material_and_row_label():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "acetylated_jute_S12",
        "metric_or_parameter": "weight_percent_gain",
        "value": "17.01",
        "unit": "%",
        "extraction_method": "AI_holistic_table",
        "evidence_text": (
            "Table 1. Acetylated jute results\n"
            "[columns]\tSample no.\tWPG (%)\n"
            "[row 12]\t12\t17.01"
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get("_checklist_failures", [])


def test_grounded_specimen_table_run_uses_specimen_column():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "UD FFRP specimen 2",
        "metric_or_parameter": "Youngs_modulus",
        "value": "20.8",
        "unit": "GPa",
        "extraction_method": "AI_holistic_table",
        "evidence_text": (
            "Table 1. UD FFRP static properties\n"
            "[columns]\tSpecimen #\tE1 [GPa]\n"
            "[row 2]\t2\t20.8"
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get("_checklist_failures", [])


def test_grounded_underscored_material_run_matches_table_context():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "Acetylated_jute_fiber_12",
        "metric_or_parameter": "weight_percent_gain",
        "value": "17.01",
        "unit": "%",
        "extraction_method": "AI_holistic_table",
        "evidence_text": (
            "The WPG values of acetylated jute are shown in Table 1.\n"
            "[columns]\tSample no.\tWPG (%)\n"
            "[row 12]\t12\t17.01"
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get("_checklist_failures", [])


def test_coordinated_material_name_counts_as_sample_evidence():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "raw jute fiber",
        "metric_or_parameter": "weight_loss",
        "value": "13.30",
        "unit": "%",
        "evidence_text": (
            "Figure 3 shows the thermogram of raw and acetylated jute. "
            "Their weight losses were 13.30% and 11.98%, respectively."
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get("_checklist_failures", [])


def test_grounded_table_coordinates_validate_catalog_sample_identity():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "UD_flax_bioepoxy_specimen_2",
        "metric_or_parameter": "Youngs_modulus",
        "value": "19.90",
        "unit": "GPa",
        "extraction_method": "AI_holistic_table",
        "_source_table_row": 2,
        "_source_table_column": 1,
        "evidence_text": (
            "Table 1. Static properties\n"
            "[columns]\tSpecimen #\tE1 [GPa]\n[row 2]\t2\t19.90"
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get("_checklist_failures", [])


def test_explicit_needle_configuration_validates_material_variant_identity():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "PAN_nanofiber_72_needles_5mm_spacing",
        "metric_or_parameter": "fiber_diameter",
        "value": "66",
        "unit": "nm",
        "condition": "deposited nanofibers measured at four target locations",
        "evidence_text": "the case of 72 needles with value of 66 +/- 26 nm",
    }

    checked = run_final_checklist([fact])[0]

    assert checked.get("_checklist_failed") is False


def test_material_token_and_equivalent_composite_form_validate_sample_identity():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "UD_flax_bioepoxy_laminate",
        "metric_or_parameter": "Youngs_modulus",
        "value": "21",
        "unit": "GPa",
        "evidence_text": (
            "Stiffness evolution for a flax fiber reinforced composite gave "
            "E1 = 21 GPa."
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert checked.get("_checklist_failed") is False


def test_matrix_catalog_id_accepts_explicit_base_material_evidence():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "TPU_matrix",
        "metric_or_parameter": "maximum_acceleration",
        "value": "69",
        "unit": "dimensionless",
        "evidence_text": "The maximum acceleration of TPU is approximately 69.",
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get("_checklist_failures", [])


def test_plural_fiber_based_composite_context_validates_sample_identity():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "UD_flax_bioepoxy_laminate",
        "metric_or_parameter": "Youngs_modulus",
        "value": "21",
        "unit": "GPa",
        "evidence_text": (
            "The RPL test on unidirectional flax fiber-based composites "
            "showed an initial modulus of 21 GPa."
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert checked.get("_checklist_failed") is False


def test_grounded_table_summary_row_validates_parent_sample_identity():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "UD_flax_bioepoxy_laminate",
        "metric_or_parameter": "Youngs_modulus",
        "value": "21.3 (1.15)",
        "unit": "[GPa]",
        "extraction_method": "AI_holistic_table",
        "_source_table_row": 9,
        "_source_table_column": 1,
        "evidence_text": (
            "[columns]\tSpecimen#\tE1 [GPa]\n"
            "[row 9]\tmean(dev)\t21.3 (1.15)"
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert checked.get("_checklist_failed") is False


def test_cold_crystallization_temperature_is_not_flagged_as_bare_condition():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "PLA/FR 4%",
        "metric_or_parameter": "cold_crystallization_temperature",
        "value": "101.63",
        "unit": "°C",
        "extraction_method": "rule_table_performance",
        "_source_table_row": 3,
        "evidence_text": (
            "[columns]\tSample\tTcc (°C)\n"
            "[row 3]\tPLA/FR 4%\t101.63"
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert "bare_temperature_as_performance_value" not in checked.get(
        "_checklist_failures", []
    )


def test_evidence_grounded_alias_validates_canonical_sample_identity():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "TPU_CF_10vol%",
        "_sample_aliases": ["fiber-reinforced composite material"],
        "metric_or_parameter": "softening_load",
        "value": "430",
        "unit": "N",
        "evidence_text": "The fiber-reinforced composite material softened at 430 N.",
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get("_checklist_failures", [])


def test_plural_alias_matches_singular_table_identity():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "CNCs",
        "_sample_aliases": ["F-CNCs"],
        "metric_or_parameter": "activation_energy",
        "value": "145.50",
        "unit": "kJ/mol",
        "evidence_text": (
            "[columns]\tModel\tF-CNC / E (kJ/mol)\tF-CNC / R2\n"
            "[row 1]\t1\t145.50\t0.9962"
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get(
        "_checklist_failures", []
    )


def test_composition_id_matches_across_slash_and_underscore_separators():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "PCL_AA_SBCu",
        "metric_or_parameter": "water_contact_angle",
        "value": "97.5",
        "unit": "degree",
        "evidence_text": (
            "The contact angles were 93.4 and 97.5 degrees for PCL/AA/S and "
            "PCL/AA/SBCu, respectively."
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get("_checklist_failures", [])


def test_shorter_composition_does_not_match_longer_composition_chain():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "PCL/AA/S",
        "metric_or_parameter": "water_contact_angle",
        "value": "97.5",
        "unit": "degree",
        "evidence_text": "PCL/AA/SBCu had a contact angle of 97.5 degrees.",
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" in checked.get("_checklist_failures", [])


def test_single_letter_sample_does_not_match_component_of_composition_id():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "S",
        "metric_or_parameter": "tensile_strength",
        "value": "2",
        "unit": "MPa",
        "evidence_text": "PCL/AA/SBCu had a tensile strength of 2 MPa.",
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" in checked.get("_checklist_failures", [])


def test_explicit_fraction_supports_compact_variant_identity():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "TPU_fiber_reinforced_10vol",
        "metric_or_parameter": "density",
        "value": "1257",
        "unit": "kg/m3",
        "evidence_text": (
            "The TPMS structure with a fiber reinforcement volume fraction of "
            "10% had a density of 1257 kg/m3."
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get("_checklist_failures", [])


def test_thread_suffix_is_supported_by_table_column_identity():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "C1_thread",
        "metric_or_parameter": "tensile_strength",
        "value": "4100",
        "unit": "MPa",
        "extraction_method": "AI_holistic_table",
        "evidence_text": (
            "[column identity] Carbon / T300 / C1\n"
            "[row 7] Tensile strength [MPa]\t4100"
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get(
        "_checklist_failures", []
    )


def test_material_code_and_thread_suffix_match_unordered_table_axis():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "C1_carbon_thread",
        "metric_or_parameter": "tensile_strength",
        "value": "4100",
        "unit": "MPa",
        "condition": "axis=Carbon / T300 / C1",
        "extraction_method": "AI_holistic_table",
        "evidence_text": (
            "[columns]\tThread type\tCarbon\tCarbon\n"
            "[row 1]\tThread Specification\tT300\tT300\n"
            "[row 2]\tThread Code\tC1\tC2\n"
            "[column identity]\tCarbon / T300 / C1\n"
            "[row 7]\tTensile strength [MPa]\t4100\t2900"
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get(
        "_checklist_failures", []
    )


def test_shared_loading_unit_supports_specific_silica_variant():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "GFRP_2wt_silica_coating",
        "metric_or_parameter": "water_diffusion_coefficient_reduction",
        "value": "11.79",
        "unit": "%",
        "evidence_text": (
            "The water diffusion coefficient decreased by 11.79 and 19.27% "
            "for specimens treated with 2 and 4 wt % silica coating, "
            "respectively."
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get(
        "_checklist_failures", []
    )


def test_zero_loading_variant_accepts_uncoated_control_wording():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "GFRP_0wt_silica_coating",
        "metric_or_parameter": "equilibrium_water_content_change",
        "value": "802.86",
        "unit": "%",
        "evidence_text": (
            "For GFRP composite laminates containing uncoated glass fibers, "
            "the equilibrium water content increased by 802.86%."
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get(
        "_checklist_failures", []
    )


def test_tufting_wording_supports_tufted_laminate_family():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "tufted_composite_laminate",
        "metric_or_parameter": "in_plane_property_reduction",
        "value": "15",
        "unit": "%",
        "evidence_text": (
            "Tufting resulted in reduction in the in-plane properties like "
            "TS, FS and IPS by 15-20%."
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get(
        "_checklist_failures", []
    )


def test_mineru_html_and_filament_suffix_preserve_sample_grounding():
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "Cf-PEEK",
            "metric_or_parameter": "tensile_strength",
            "value": "2050",
            "unit": "MPa",
            "evidence_text": (
                "<html><body><b>Cf-PEEK</b> reached 2050 MPa.</body></html>"
            ),
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "FBR-MP0007_filament",
            "metric_or_parameter": "tensile_strength",
            "value": "78",
            "unit": "MPa",
            "evidence_text": "FBR-MP0007 reached a tensile strength of 78 MPa.",
        },
    ]

    checked = run_final_checklist(facts)

    assert all(
        "sample_id_not_found_in_evidence" not in fact.get(
            "_checklist_failures", []
        )
        for fact in checked
    )


def test_component_form_identity_requires_every_material_component():
    grounded = {
        "fact_type": "performance",
        "assigned_sample_id": "CNC/PLA_nanocomposite_filament",
        "metric_or_parameter": "tensile_strength",
        "value": "65",
        "unit": "MPa",
        "evidence_text": (
            "CNCs were dispersed in PLA to produce nanocomposite filaments "
            "with a tensile strength of 65 MPa."
        ),
    }
    wrong_matrix = {
        **grounded,
        "evidence_text": (
            "CNCs were dispersed in PVA to produce nanocomposite filaments "
            "with a tensile strength of 65 MPa."
        ),
    }

    grounded_checked = run_final_checklist([grounded])[0]
    wrong_matrix_checked = run_final_checklist([wrong_matrix])[0]

    assert "sample_id_not_found_in_evidence" not in grounded_checked.get(
        "_checklist_failures", []
    )
    assert "sample_id_not_found_in_evidence" in wrong_matrix_checked.get(
        "_checklist_failures", []
    )

def test_composite_identity_accepts_filament_form_with_exact_components():
    grounded = {
        "fact_type": "performance",
        "assigned_sample_id": "Cf-PEEK_composite",
        "metric_or_parameter": "density",
        "value": "1.57",
        "unit": "g/cc",
        "evidence_text": (
            "This filament consists of continuous Cf tow impregnated with "
            "PEEK to a density of 1.57 g/cc."
        ),
    }
    wrong_matrix = {
        **grounded,
        "evidence_text": (
            "This filament consists of continuous Cf tow impregnated with "
            "PPS to a density of 1.57 g/cc."
        ),
    }

    grounded_checked = run_final_checklist([grounded])[0]
    wrong_matrix_checked = run_final_checklist([wrong_matrix])[0]

    assert "sample_id_not_found_in_evidence" not in grounded_checked.get(
        "_checklist_failures", []
    )
    assert "sample_id_not_found_in_evidence" in wrong_matrix_checked.get(
        "_checklist_failures", []
    )


def test_coded_composite_identity_accepts_semantic_material_aliases_and_ratio():
    evidence = (
        "When the doping ratio of cobalt ferrite to carbon fiber powder is "
        "0:3, the cobalt ferrite/carbon fiber-coated PANI-based "
        "polyester-cotton fabric reaches a minimum reflection loss of -21.4 dB."
    )
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "PANI_PC_fabric_CoFe2O4-CF_0-3",
        "metric_or_parameter": "minimum_reflection_loss",
        "value": "-21.4",
        "unit": "dB",
        "condition": "doping ratio 0:3",
        "evidence_text": evidence,
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" not in checked.get(
        "_checklist_failures", []
    )


def test_coded_composite_identity_rejects_wrong_ratio():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "PANI_PC_fabric_CoFe2O4-CF_0-3",
        "metric_or_parameter": "minimum_reflection_loss",
        "value": "-5.32",
        "unit": "dB",
        "condition": "doping ratio 1:2",
        "evidence_text": (
            "When the doping ratio of cobalt ferrite to carbon fiber powder is "
            "1:2, the cobalt ferrite/carbon fiber-coated PANI-based "
            "polyester-cotton fabric reaches -5.32 dB."
        ),
    }

    checked = run_final_checklist([fact])[0]

    assert "sample_id_not_found_in_evidence" in checked.get(
        "_checklist_failures", []
    )


def test_crystallization_temperature_is_not_flagged_as_bare_condition():
    fact = {
        "fact_type": "performance",
        "assigned_sample_id": "PHBV/PBAT blend",
        "metric_or_parameter": "crystallization_temperature",
        "value": "60",
        "unit": "°C",
        "evidence_text": "The crystallization temperature Tc2 rose to 60 °C.",
    }

    checked = run_final_checklist([fact])[0]

    assert "bare_temperature_as_performance_value" not in checked.get(
        "_checklist_failures", []
    )
