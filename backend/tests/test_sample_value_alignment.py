"""Sample-value alignment tests."""

from app.services.extractor_v7.sample_value_alignment import (
    align_explicit_configuration_variants,
    apply_sample_value_alignment,
    classify_non_result_numeric_role,
    expand_multi_entity_facts,
    parse_metric_value_pairs,
    parse_sample_value_pairs,
    verify_fact_alignment,
)


def test_parenthesis_nearest_neighbor_temperature():
    evidence = "2MZ-AZINE-PI3 (117.8 °C) and PI1 (150.2 °C) were compared"
    pairs = parse_sample_value_pairs(evidence)
    mapping = {sid: val for sid, val in pairs}
    assert mapping.get("2MZ-AZINE-PI3") == "117.8"
    assert mapping.get("PI1") == "150.2"


def test_parenthesis_sample_value_pairs():
    evidence = "PI1 (12.38 mg/cm3), PI2 (10.2 mg/cm3) and PI3 (4.74 mg/cm3)"
    pairs = parse_sample_value_pairs(evidence)
    assert ("PI1", "12.38") in pairs
    assert any(sid == "PI3" and val == "4.74" for sid, val in pairs)


def test_characterization_peak_phrase_is_not_parsed_as_sample_value_pair():
    pairs = parse_sample_value_pairs(
        "The carbonyl stretching peak (1722 cm^-1) was observed by FTIR."
    )

    assert pairs == []


def test_respective_value_list_before_composition_samples_is_bound_positionally():
    evidence = (
        "The pH increased up to 7.60 and 7.64 for PCL/AA/S and "
        "PCL/AA/SBCu (respectively) after 14 days in SBF."
    )

    assert parse_sample_value_pairs(evidence) == [
        ("PCL/AA/S", "7.60"),
        ("PCL/AA/SBCu", "7.64"),
    ]


def test_partial_value_pair_uses_only_remaining_explicit_catalog_sample():
    evidence = (
        "The CA of both PCL/AA/SBCu and control PCL/AA/S decreased; "
        "values were 56.6° for PCL/AA/S and 73.77° for PCL/AA/"
    )
    facts = [
        {
            "fact_id": "F1",
            "fact_type": "performance",
            "metric_or_parameter": "water_contact_angle",
            "value": "56.6",
            "unit": "°",
            "assigned_sample_id": "PCL/AA/SBCu",
            "assignment_status": "assigned",
            "evidence_text": evidence,
        },
        {
            "fact_id": "F2",
            "fact_type": "performance",
            "metric_or_parameter": "water_contact_angle",
            "value": "73.77",
            "unit": "°",
            "assigned_sample_id": "PCL/AA/SBCu",
            "assignment_status": "assigned",
            "evidence_text": evidence,
        },
    ]
    cards = [
        {"sample_id": "PCL/AA/S", "sample_aliases": ""},
        {"sample_id": "PCL/AA/SBCu", "sample_aliases": ""},
    ]

    out = apply_sample_value_alignment(facts, cards)

    assert [(fact["value"], fact["assigned_sample_id"]) for fact in out] == [
        ("56.6", "PCL/AA/S"),
        ("73.77", "PCL/AA/SBCu"),
    ]


def test_contrast_clause_aligns_each_value_to_its_sample():
    evidence = (
        "The stress at which softening occurred in the fiber-filled composite "
        "material was 430 N, whereas that in the TPU material was 350 N."
    )
    facts = [
        {
            "fact_id": "F1",
            "fact_type": "performance",
            "metric_or_parameter": "softening_load",
            "value": "430",
            "unit": "N",
            "condition": "compression",
            "assigned_sample_id": "10% fiber-reinforced TPMS structure",
            "evidence_text": evidence,
        },
        {
            "fact_id": "F2",
            "fact_type": "performance",
            "metric_or_parameter": "softening_load",
            "value": "350",
            "unit": "N",
            "condition": "compression",
            "assigned_sample_id": "10% fiber-reinforced TPMS structure",
            "evidence_text": evidence,
        },
    ]
    cards = [
        {
            "sample_id": "TPU_matrix",
            "sample_aliases": '["TPU material structure", "matrix material"]',
            "material_system": "TPU",
        },
        {
            "sample_id": "TPU_T300_CF_P-type_TPMS_10vol%",
            "sample_aliases": (
                '["TPU TPMS material", '
                '"fiber-reinforced structure material"]'
            ),
            "material_system": "TPU/T300 carbon fiber composite",
        },
    ]

    out = apply_sample_value_alignment(facts, cards)

    assert {fact["value"]: fact["assigned_sample_id"] for fact in out} == {
        "430": "TPU_T300_CF_P-type_TPMS_10vol%",
        "350": "TPU_matrix",
    }
    assert all(
        "contrast_clause_value_alignment" in fact["assignment_reason"]
        for fact in out
    )


def test_contrast_clause_aligns_trailing_reduction_to_right_hand_sample():
    evidence = (
        "The TPU had a dimensionless maximum acceleration of 69, whereas the "
        "fiber enhancement had a dimensionless maximum acceleration of 37, "
        "which is a decrease of more than 46%."
    )
    facts = [
        {
            "fact_type": "performance",
            "metric_or_parameter": "maximum_acceleration",
            "value": "69",
            "unit": "dimensionless",
            "condition": "impact velocity 5 m/s",
            "assigned_sample_id": "TPU",
            "evidence_text": evidence,
        },
        {
            "fact_type": "performance",
            "metric_or_parameter": "maximum_acceleration",
            "value": "37",
            "unit": "dimensionless",
            "condition": "impact velocity 5 m/s",
            "assigned_sample_id": "TPU",
            "evidence_text": evidence,
        },
        {
            "fact_type": "performance",
            "metric_or_parameter": "acceleration_reduction",
            "value": "more than 46",
            "unit": "%",
            "condition": "impact velocity 5 m/s; compared with TPU",
            "assigned_sample_id": "TPU",
            "evidence_text": evidence,
        },
    ]
    cards = [
        {
            "sample_id": "TPU",
            "sample_aliases": '["TPU material"]',
            "material_system": "TPU",
        },
        {
            "sample_id": "10% fiber-reinforced TPMS",
            "sample_aliases": '["fiber-reinforced structure material"]',
            "material_system": "TPU/T300 carbon fiber composite",
        },
    ]

    out = apply_sample_value_alignment(facts, cards)
    by_metric_value = {
        (fact["metric_or_parameter"], fact["value"]): fact
        for fact in out
    }

    assert by_metric_value[("maximum_acceleration", "69")]["assigned_sample_id"] == "TPU"
    assert by_metric_value[("maximum_acceleration", "37")]["assigned_sample_id"] == (
        "10% fiber-reinforced TPMS"
    )
    reduction = by_metric_value[("acceleration_reduction", "more than 46")]
    assert reduction["assigned_sample_id"] == "10% fiber-reinforced TPMS"
    assert "contrast_clause_relative_change_alignment" in reduction["assignment_reason"]


def test_contextual_material_pronoun_is_not_promoted_to_sample_id():
    evidence = (
        "A threshold load of approximately 40 MPa exists for this particular "
        "material, above which inelastic strain accumulates."
    )

    assert parse_sample_value_pairs(evidence) == []


def test_explicit_needle_cases_bind_each_diameter_to_the_matching_catalog_variant():
    evidence = (
        "The average diameter for the case 17 needles was 74±28 nm, which was "
        "slightly higher than the case of 72 needles with value of 66 ±26 nm."
    )
    facts = [
        {
            "fact_type": "performance",
            "metric_or_parameter": "fiber_diameter",
            "value": "74",
            "assigned_sample_id": "wrong sample",
            "evidence_text": evidence,
        },
        {
            "fact_type": "performance",
            "metric_or_parameter": "fiber_diameter",
            "value": "66",
            "assigned_sample_id": "wrong sample",
            "evidence_text": evidence,
        },
    ]
    cards = [
        {"sample_id": "PAN_nanofiber_17_needles"},
        {"sample_id": "PAN_nanofiber_72_needles"},
    ]

    aligned = align_explicit_configuration_variants(facts, cards)

    assert [fact["assigned_sample_id"] for fact in aligned] == [
        "PAN_nanofiber_17_needles",
        "PAN_nanofiber_72_needles",
    ]


def test_explicit_needle_alignment_prefers_explicit_variable_over_conflicting_alias():
    evidence = (
        "The average diameter for the case 17 needles was 74 nm, while the "
        "case of 72 needles was 66 nm."
    )
    facts = [
        {
            "fact_type": "performance",
            "value": "74",
            "evidence_text": evidence,
            "assigned_sample_id": "PAN_nanofiber_17_needles",
        },
        {
            "fact_type": "performance",
            "value": "66",
            "evidence_text": evidence,
            "assigned_sample_id": "PAN_nanofiber_17_needles",
        },
    ]
    cards = [
        {
            "sample_id": "PAN_nanofiber_17_needles",
            "sample_aliases": '["PAN_nanofiber_72_needles"]',
            "variable_name": "number of needles",
            "variable_value": "17",
        },
        {
            "sample_id": "PAN_nanofiber_72_needles",
            "variable_name": "number of needles",
            "variable_value": "72",
        },
    ]

    aligned = align_explicit_configuration_variants(facts, cards)

    assert [fact["assigned_sample_id"] for fact in aligned] == [
        "PAN_nanofiber_17_needles",
        "PAN_nanofiber_72_needles",
    ]


def test_flow_rate_with_volume_per_time_unit_is_reclassified_as_process():
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "surface_roughness",
        "value": "9",
        "unit": "mL/hr",
        "assigned_sample_id": "PAN_nanofiber_72_needles",
        "evidence_text": "The total flow rate of the 72 needle setup was 9 ml/hr.",
    }]

    aligned = apply_sample_value_alignment(
        facts,
        [{"sample_id": "PAN_nanofiber_72_needles"}],
    )

    assert aligned[0]["fact_type"] == "process"
    assert aligned[0]["metric_or_parameter"] == "total_flow_rate"


def test_average_nanofiber_diameter_subject_corrects_roughness_drift():
    facts = [{
        "fact_type": "performance",
        "subject_text": "average_diameter",
        "metric_or_parameter": "surface_roughness",
        "value": "66",
        "unit": "nm",
        "condition": "deposited nanofibers; 72 needles",
        "assigned_sample_id": "PAN_nanofiber_72_needles",
        "evidence_text": "the case of 72 needles with value of 66 +/- 26 nm",
    }]

    aligned = apply_sample_value_alignment(
        facts,
        [{"sample_id": "PAN_nanofiber_72_needles"}],
    )

    assert aligned[0]["metric_or_parameter"] == "fiber_diameter"
    assert "metric_corrected_from_diameter_subject" in aligned[0]["assignment_reason"]


def test_result_sentence_fragment_is_not_promoted_to_sample_id():
    evidence = "WPG. The oil absorption capacity of raw jute fiber was 2.58 g/g."

    assert all("WPG" not in sample for sample, _ in parse_sample_value_pairs(evidence))


def test_compared_to_assigns_value_to_second_sample():
    evidence = "2MZ-AZINE-PI3 aerogel is lower than PI1 aerogel (30.63%)"
    pairs = parse_sample_value_pairs(evidence)
    assert any("PI1" in sid and _eq(val, "30.63") for sid, val in pairs)


def _eq(a, b):
    return str(a).startswith(str(b).split(".")[0]) or str(a) == str(b)


def test_multi_metric_split():
    evidence = (
        "The real permittivity of 2MZ-AZINE-PI3 aerogel was 1.004 and "
        "loss tangent was 8e-4 in X-band"
    )
    pairs = parse_metric_value_pairs(evidence)
    metrics = {m for m, _ in pairs}
    assert "dielectric_constant" in metrics
    assert "loss_tangent" in metrics


def test_expand_parenthesis_splits_facts():
    facts = [{
        "fact_id": "F1",
        "fact_type": "performance",
        "metric_or_parameter": "density",
        "value": "various",
        "unit": "mg/cm3",
        "evidence_text": "A (10), B (20), C (30) mg/cm3",
    }]
    out = expand_multi_entity_facts(facts)
    values = sorted(f.get("value") for f in out)
    assert values == ["10", "20", "30"]
    assert {f.get("assigned_sample_id") for f in out} == {"A", "B", "C"}


def test_verify_flags_multi_sample_mismatch():
    fact = {
        "fact_type": "performance",
        "metric_or_parameter": "density",
        "value": "20",
        "assigned_sample_id": "A",
        "evidence_text": "A (10), B (20), C (30) mg/cm3",
    }
    ok, reason = verify_fact_alignment(fact)
    assert not ok
    assert reason == "multi_sample_value_alignment_unclear"


def test_apply_alignment_fixes_single_match():
    facts = [{
        "fact_id": "F1",
        "fact_type": "performance",
        "metric_or_parameter": "density",
        "value": "4.74",
        "unit": "mg/cm3",
        "assigned_sample_id": "PI1",
        "evidence_text": "PI1 (12.38 mg/cm3), PI3 (4.74 mg/cm3)",
    }]
    out = apply_sample_value_alignment(facts)
    assert out[0]["assigned_sample_id"] == "PI3"
    assert out[0].get("_alignment_verified") is True


def test_explicit_value_for_sample_pairs_are_aligned():
    evidence = (
        "The weight loss was 44.36 % for raw jute and "
        "56.26 % for acetylated jute at 350 °C."
    )

    assert parse_sample_value_pairs(evidence) == [
        ("raw jute", "44.36"),
        ("acetylated jute", "56.26"),
    ]


def test_single_explicit_sample_statement_overrides_wrong_assignment():
    facts = [{
        "fact_id": "F1",
        "fact_type": "performance",
        "metric_or_parameter": "oil_absorption_capacity",
        "value": "2.58",
        "unit": "g/g",
        "assigned_sample_id": "acetylated jute",
        "evidence_text": "The oil absorption capacity of raw jute fiber was 2.58 g/g.",
    }]

    out = apply_sample_value_alignment(facts)

    assert out[0]["assigned_sample_id"] == "raw jute fiber"
    assert out[0]["_alignment_verified"] is True


def test_respectively_uses_order_from_explicit_same_metric_evidence():
    facts = [
        {
            "fact_id": "F1",
            "fact_type": "performance",
            "metric_or_parameter": "weight_loss",
            "value": "44.36",
            "unit": "%",
            "assigned_sample_id": "acetylated jute",
            "evidence_text": "Weight loss was 44.36 % for raw jute and 56.26 % for acetylated jute.",
        },
        {
            "fact_id": "F2",
            "fact_type": "performance",
            "metric_or_parameter": "weight_loss",
            "value": "56.26",
            "unit": "%",
            "assigned_sample_id": "acetylated jute",
            "evidence_text": "Weight loss was 44.36 % for raw jute and 56.26 % for acetylated jute.",
        },
        {
            "fact_id": "F3",
            "fact_type": "performance",
            "metric_or_parameter": "weight_loss",
            "value": "13.30",
            "unit": "%",
            "assigned_sample_id": "acetylated jute",
            "evidence_text": "Weight loss of both samples was 13.30 % and 11.98 %, respectively.",
        },
        {
            "fact_id": "F4",
            "fact_type": "performance",
            "metric_or_parameter": "weight_loss",
            "value": "11.98",
            "unit": "%",
            "assigned_sample_id": "acetylated jute",
            "evidence_text": "Weight loss of both samples was 13.30 % and 11.98 %, respectively.",
        },
    ]

    out = apply_sample_value_alignment(facts)
    by_value = {fact["value"]: fact["assigned_sample_id"] for fact in out}

    assert by_value["13.30"] == "raw jute"
    assert by_value["11.98"] == "acetylated jute"


def test_respectively_uses_catalog_order_and_keeps_conditions_separate():
    evidence = (
        "Figure 3 shows the thermogram of raw and acetylated jute. "
        "Weight loss was 13.30% and 11.98% at 250 °C, respectively; "
        "it was 44.36% for raw jute and 56.26% for acetylated jute at 350 °C."
    )
    facts = [
        {
            "fact_id": f"F{index}",
            "fact_type": "performance",
            "metric_or_parameter": "weight_loss",
            "value": value,
            "unit": "%",
            "condition": condition,
            "assigned_sample_id": "acetylated jute fiber",
            "candidate_sample_ids": ["acetylated jute"],
            "evidence_text": evidence,
        }
        for index, (value, condition) in enumerate(
            [("13.30", "250 °C"), ("11.98", "250 °C"),
             ("44.36", "350 °C"), ("56.26", "350 °C")],
            1,
        )
    ]
    cards = [
        {
            "sample_id": "raw jute fiber",
            "sample_aliases": '["raw jute", "original jute"]',
        },
        {
            "sample_id": "acetylated jute fiber",
            "sample_aliases": '["acetylated jute"]',
        },
        {"sample_id": "acetylated jute fiber sample 10", "sample_aliases": ""},
    ]

    out = apply_sample_value_alignment(facts, cards)
    by_value = {fact["value"]: fact["assigned_sample_id"] for fact in out}

    assert by_value == {
        "13.30": "raw jute fiber",
        "11.98": "acetylated jute fiber",
        "44.36": "raw jute",
        "56.26": "acetylated jute",
    }


def test_non_result_numeric_roles_are_hard_rejected():
    catalyst_evidence = (
        "Use of 2 % catalyst (2.0 g catalyst in 100 ml solvent) at 120 °C for 1 h "
        "(sample 12) resulted in WPG of 17.01 %."
    )
    cases = [
        ("0.2", "WPG values were within the standard deviation of 0.2 %.", "uncertainty_statistic"),
        ("12", catalyst_evidence, "sample_or_run_identifier"),
        ("2.0", catalyst_evidence, "reagent_or_process_amount"),
    ]

    for value, evidence, expected in cases:
        fact = {
            "fact_type": "performance",
            "metric_or_parameter": "weight_percent_gain",
            "value": value,
            "unit": "%",
            "evidence_text": evidence,
        }
        assert classify_non_result_numeric_role(fact) == expected
        assert apply_sample_value_alignment([fact])[0]["_hard_reject"] is True


def test_wavenumber_is_not_moved_to_temperature_condition():
    fact = {
        "fact_id": "F1",
        "fact_type": "performance",
        "metric_or_parameter": "FTIR_band_1",
        "value": "1750",
        "unit": "cm⁻¹",
        "assigned_sample_id": "acetylated jute",
        "evidence_text": "The acetylated jute showed a peak at 1750 cm⁻¹.",
    }

    out = apply_sample_value_alignment([fact])

    assert "1750 °C" not in str(out[0].get("condition") or "")


def test_grounded_table_fact_keeps_row_condition_and_alignment():
    fact = {
        "fact_id": "F1",
        "fact_type": "performance",
        "metric_or_parameter": "weight_percent_gain",
        "value": "6.55",
        "unit": "%",
        "assigned_sample_id": "acetylated jute 1",
        "assignment_status": "assigned",
        "extraction_method": "AI_holistic_table",
        "_source_table_row": 1,
        "condition": "Time=0.5 h; Temp=80 °C",
        "evidence_text": (
            "Acetylated jute was tested for oil absorption after 1 h.\n"
            "[columns]\tSample no.\tTime (h)\tWPG (%)\n"
            "[row 1]\t1\t0.5\t6.55"
        ),
    }

    out = apply_sample_value_alignment([fact])[0]

    assert out["condition"] == "Time=0.5 h; Temp=80 °C"
    assert out["_alignment_verified"] is True
    assert out.get("_alignment_review_required") is not True
