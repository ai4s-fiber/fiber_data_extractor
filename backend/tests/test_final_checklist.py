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
