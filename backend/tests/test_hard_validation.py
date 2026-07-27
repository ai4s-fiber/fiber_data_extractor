"""Hard validation regression tests for user-reported error patterns."""

from app.services.extractor_v7.hard_validation import (
    apply_hard_validation,
    find_explicit_transition_matches,
    infer_metric_from_evidence,
    refine_sample_name_before_paren,
)
from app.services.extractor_v7.sample_value_alignment import apply_sample_value_alignment
from app.services.validation import metric_unit_compatible


def test_fiber_length_not_surface_roughness():
    ev = "The average fiber length of 2MZ-AZINE-PI nanofibers was 40.5 μm"
    assert infer_metric_from_evidence(ev, unit="μm", current_metric="surface_roughness") == "fiber_length"
    facts = [{
        "fact_id": "F1",
        "fact_type": "performance",
        "metric_or_parameter": "surface_roughness",
        "value": "40.5",
        "unit": "μm",
        "evidence_text": ev,
        "assigned_sample_id": "2MZ-AZINE-PI",
    }]
    out = apply_hard_validation(facts)
    assert out[0]["metric_or_parameter"] == "fiber_length"


def test_fiber_diameter_not_surface_roughness_nm():
    ev = "average fiber diameter of PI nanofibers was 462.2 nm"
    assert infer_metric_from_evidence(ev, unit="nm", current_metric="surface_roughness") == "fiber_diameter"


def test_evidence_corrects_storage_modulus_and_interlaminar_toughness():
    assert metric_unit_compatible("fracture_toughness", "J/m^2")
    assert infer_metric_from_evidence(
        "The energy storage modulus was 133 GPa at 25 °C.",
        unit="GPa",
        current_metric="Youngs_modulus",
    ) == "storage_modulus"
    assert infer_metric_from_evidence(
        "The mode I interlaminar fracture toughness GIC reached 407 J/m².",
        unit="J/m²",
        current_metric="impact_strength",
    ) == "mode_I_interlaminar_fracture_toughness"
    assert infer_metric_from_evidence(
        "The mode II interlaminar fracture toughness GIIC reached 2505 J/m².",
        unit="J/m²",
        current_metric="impact_strength",
    ) == "mode_II_interlaminar_fracture_toughness"

    corrected = apply_hard_validation([{
        "fact_type": "performance",
        "metric_or_parameter": "fracture_toughness",
        "value": "407",
        "unit": "J/m^2",
        "condition": "mode I interlaminar fracture toughness, average G_IC",
        "evidence_text": "The average G_IC reached 407 J/m^2.",
    }])
    assert (
        corrected[0]["metric_or_parameter"]
        == "mode_I_interlaminar_fracture_toughness"
    )


def test_power_law_indices_are_not_absolute_storage_or_loss_moduli():
    evidence = (
        "For neat PLA, the fitted powerlaw indices were 1.82 for G' and "
        "0.92 for G″, slightly deviating from the theoretical values."
    )

    assert infer_metric_from_evidence(
        evidence,
        unit="dimensionless",
        current_metric="storage_modulus",
        value="1.82",
    ) == "storage_modulus_power_law_index"
    assert infer_metric_from_evidence(
        evidence,
        unit="dimensionless",
        current_metric="loss_modulus",
        value="0.92",
    ) == "loss_modulus_power_law_index"

    corrected = apply_hard_validation([{
        "fact_type": "performance",
        "metric_or_parameter": "storage_modulus",
        "value": "1.82",
        "unit": "dimensionless",
        "evidence_text": evidence,
    }])

    assert (
        corrected[0]["metric_or_parameter"]
        == "storage_modulus_power_law_index"
    )
    assert metric_unit_compatible(
        "storage_modulus_power_law_index", "dimensionless"
    )
    assert metric_unit_compatible(
        "loss_modulus_power_law_index", "dimensionless"
    )


def test_evidence_corrects_relative_strength_metrics():
    assert infer_metric_from_evidence(
        "The flexural strength increased by 8% compared with CF/EP.",
        unit="%",
        current_metric="flexural_strength",
    ) == "flexural_strength_improvement"
    assert infer_metric_from_evidence(
        "The ILSS increased from 83 MPa to 89 MPa, an increase of 7%.",
        unit="%",
        current_metric="interlaminar_shear_strength",
    ) == "interlaminar_shear_strength_growth_rate"


def test_evidence_resolves_gic_giic_absolute_and_relative_metrics():
    assert infer_metric_from_evidence(
        "The average G_IC of CF/EP is 189 J/m^2.",
        unit="J/m^2",
        current_metric="fracture_toughness",
        value="189",
    ) == "mode_I_interlaminar_fracture_toughness"
    assert infer_metric_from_evidence(
        "G_IC reached 407 J/m^2, an increase of 115.3%.",
        unit="%",
        current_metric="fracture_toughness",
        value="115.3",
    ) == "mode_I_interlaminar_fracture_toughness_improvement"
    assert infer_metric_from_evidence(
        "G_IIC increased to 2505 J/m^2, an increase of 128%.",
        unit="%",
        current_metric="fracture_toughness",
        value="128",
    ) == "mode_II_interlaminar_fracture_toughness_improvement"
    assert infer_metric_from_evidence(
        "The storage modulus is 133 GPa, 13% higher than the pure system.",
        unit="%",
        current_metric="storage_modulus",
        value="13",
    ) == "storage_modulus_improvement"
    assert infer_metric_from_evidence(
        "The mode I vibration frequency was 120 Hz.",
        unit="Hz",
        current_metric="eigenfrequency",
        value="120",
    ) is None
    assert infer_metric_from_evidence(
        (
            r"The low $\mathrm { G } _ { \mathrm { I C } } "
            r"(189 \mathrm { J } / \mathrm { m } ^ { 2 } )$ was observed."
        ),
        unit="J/m^2",
        current_metric="fracture_toughness",
        value="189",
    ) == "mode_I_interlaminar_fracture_toughness"


def test_evidence_uses_value_proximity_when_flexural_strength_and_ilss_cooccur():
    evidence = (
        "The bending strength and ILSS are evaluated. The flexural strength "
        "increased from 1663 MPa to 1796 MPa. The ILSS increased from "
        "83 MPa to 89 MPa."
    )
    assert infer_metric_from_evidence(
        evidence,
        unit="MPa",
        current_metric="interlaminar_shear_strength",
        value="1663",
    ) == "flexural_strength"
    assert infer_metric_from_evidence(
        evidence,
        unit="MPa",
        current_metric="flexural_strength",
        value="83",
    ) == "interlaminar_shear_strength"


def test_clause_local_metric_inference_separates_tufting_tradeoffs():
    evidence = (
        "Tufting resulted in reduction in the in-plane properties like TS, "
        "FS & IPS by 15%-20% whereas mode-1 interlaminar fracture toughness "
        "was enhanced by about 10 times."
    )

    for value in ("15", "20"):
        assert infer_metric_from_evidence(
            evidence,
            unit="%",
            current_metric="fracture_toughness_improvement",
            value=value,
        ) == "in_plane_property_reduction"
    assert infer_metric_from_evidence(
        evidence,
        unit="times",
        current_metric="interlaminar_fracture_toughness",
        value="10",
    ) == "mode_I_interlaminar_fracture_toughness_improvement"


def test_ordered_strength_changes_bind_each_value_to_its_metric():
    evidence = (
        "The compressive and flexural strength of organic hybrid fiber "
        "reinforced geopolymer increased by more than 90% and 65%, and the "
        "compressive strength of mineral-organic hybrid fiber reinforced "
        "geopolymer increased by more than 160%."
    )

    assert infer_metric_from_evidence(
        evidence,
        unit="%",
        current_metric="flexural_strength_improvement",
        value="90",
    ) == "compressive_strength_improvement"
    assert infer_metric_from_evidence(
        evidence,
        unit="%",
        current_metric="flexural_strength_improvement",
        value="65",
    ) == "flexural_strength_improvement"
    assert infer_metric_from_evidence(
        evidence,
        unit="%",
        current_metric="flexural_strength_improvement",
        value="160",
    ) == "compressive_strength_improvement"


def test_relative_water_parameters_use_change_metrics():
    evidence = (
        "These two parameters increase by 802.86 and 176.80%, respectively, "
        "as the water temperature increases from 30 to 90 C."
    )

    assert infer_metric_from_evidence(
        evidence,
        unit="%",
        current_metric="equilibrium_water_content",
        value="802.86",
    ) == "equilibrium_water_content_change"
    assert infer_metric_from_evidence(
        evidence,
        unit="%",
        current_metric="water_diffusion_coefficient",
        value="176.80",
    ) == "water_diffusion_coefficient_change"


def test_composition_percentages_are_not_expanded_as_performance_rows():
    evidence = (
        "The optimum compositions were organic composite (formulated as 98% "
        "of MK, 1% of PP and 1% of PVA) and hybrid composite (formulated as "
        "83% of MK, 15% of WS and 2% of PVA), whose compressive strength "
        "increased by 90% and 160%, respectively."
    )
    fact = {
        "fact_id": "F1",
        "fact_type": "performance",
        "metric_or_parameter": "compressive_strength_improvement",
        "value": "90",
        "unit": "%",
        "assigned_sample_id": "organic composite",
        "condition": "formulated as 98% MK, 1% PP and 1% PVA",
        "evidence_text": evidence,
        "_alignment_review_required": True,
    }

    out = apply_hard_validation([fact])

    assert len(out) == 1
    assert out[0]["value"] == "90"
    assert out[0]["assigned_sample_id"] == "organic composite"


def test_characteristic_strains_are_not_surface_roughness():
    cases = [
        ("The knee was centered at about 0.2% strain.", "knee_strain"),
        ("The damage index decreased as strain exceeded 0.35%.", "damage_transition_strain"),
        ("Beyond 0.8% applied strain, the sample shows stiffness recovery.", "stiffness_recovery_strain"),
    ]

    for evidence, expected in cases:
        assert infer_metric_from_evidence(
            evidence, unit="%", current_metric="surface_roughness"
        ) == expected


def test_transition_validation_rejects_unbound_zone_boundaries():
    facts = [
        {
            "fact_type": "performance",
            "metric_or_parameter": "knee_strain",
            "value": "0.15",
            "unit": "% strain",
            "evidence_text": (
                "Between 0.15% and 0.3% strain a transition zone was detected."
            ),
        },
        {
            "fact_type": "performance",
            "metric_or_parameter": "stiffness_recovery_strain",
            "value": "0.2",
            "unit": "% strain",
            "evidence_text": "Such a transition still occurs around 0.2% strain.",
        },
    ]

    out = apply_hard_validation(facts)

    assert all(fact.get("_hard_reject") for fact in out)
    assert all(
        fact.get("_hard_reject_reason")
        == "transition_value_not_bound_to_phenomenon"
        for fact in out
    )


def test_transition_matcher_requires_direct_knee_binding():
    assert find_explicit_transition_matches(
        "The distinct knee was centered at about 0.2% strain."
    )[0]["value"] == "0.2"
    assert not find_explicit_transition_matches(
        "A knee lies between points A and B (0.15% and 0.3% strain)."
    )


def test_transition_matcher_recovers_explicit_behavior_displacement():
    matches = find_explicit_transition_matches(
        "The curve showed an initially stiff response up to a displacement of "
        "approximately 17 mm, then became more compliant."
    )

    assert len(matches) == 1
    assert matches[0]["metric"] == "compressive_displacement"
    assert matches[0]["value"] == "17"
    assert matches[0]["unit"] == "mm"


def test_loss_tangent_not_dielectric_constant():
    facts = [{
        "fact_id": "F1",
        "fact_type": "performance",
        "metric_or_parameter": "dielectric_constant",
        "value": "8e-4",
        "evidence_text": "permittivity of 1.004 and loss tangent of 8 × 10^-4",
        "assigned_sample_id": "2MZ-AZINE-PI3 aerogel",
    }]
    out = apply_sample_value_alignment(facts)
    lt = [f for f in out if f.get("metric_or_parameter") == "loss_tangent"]
    assert lt
    assert lt[0]["value"] in ("8e-4", "0.0008")
    assert not any(
        f.get("metric_or_parameter") == "dielectric_constant" and f.get("value") in ("8e-4", "0.0008")
        for f in out
    )


def test_paren_nearest_neighbor_tg():
    ev = "2MZ-AZINE-PI3 (117.8 °C) showed higher Tg than PI1 (150.2 °C)"
    assert refine_sample_name_before_paren(ev.split("(")[0]) == "2MZ-AZINE-PI3"
    idx = ev.index("PI1 (150.2")
    assert refine_sample_name_before_paren(ev[: idx + len("PI1 ")]) == "PI1"


def test_pi3_not_pi1_for_623():
    facts = [{
        "fact_id": "F1",
        "fact_type": "performance",
        "metric_or_parameter": "surface_temperature",
        "value": "62.3",
        "unit": "°C",
        "assigned_sample_id": "PI1 aerogel",
        "evidence_text": "2MZ-AZINE-PI3 (62.3 °C) and PI1 (150.2 °C)",
    }]
    out = apply_hard_validation(facts)
    assert out[0]["assigned_sample_id"] == "2MZ-AZINE-PI3"


def test_cycles_not_stability_value():
    facts = [{
        "fact_id": "F1",
        "fact_type": "performance",
        "metric_or_parameter": "cyclic_compression_stability",
        "value": "500",
        "unit": "cycles",
        "evidence_text": "no obvious stress decay after 500 compression cycles at 50% strain",
        "assigned_sample_id": "2MZ-AZINE-PI3 aerogel",
    }]
    out = apply_hard_validation(facts)
    assert out[0]["value"] != "500"
    assert "500" in (out[0].get("condition") or "")


def test_thermal_conductivity_ordered_list():
    ev = (
        "The thermal conductivities of 2MZ-AZINE-PI1, 2MZ-AZINE-PI2, "
        "2MZ-AZINE-PI3 and PI1 aerogels were 26.2, 25.9, 25.5 and 24.8 mW/m·K"
    )
    facts = [{
        "fact_id": "F1",
        "fact_type": "performance",
        "metric_or_parameter": "thermal_conductivity",
        "value": "25.9",
        "unit": "mW/m·K",
        "assigned_sample_id": "2MZ-AZINE-PI",
        "evidence_text": ev,
    }]
    out = apply_sample_value_alignment(facts)
    by_sample = {f["assigned_sample_id"]: f["value"] for f in out}
    assert by_sample.get("2MZ-AZINE-PI2") == "25.9"
    assert by_sample.get("2MZ-AZINE-PI1") == "26.2"


def test_fiber_sample_enriched():
    ev = "The average fiber length of 2MZ-AZINE-PI nanofibers was 40.5 μm"
    facts = [{
        "fact_id": "F1",
        "fact_type": "performance",
        "metric_or_parameter": "surface_roughness",
        "value": "40.5",
        "unit": "μm",
        "evidence_text": ev,
        "assigned_sample_id": "2MZ-AZINE-PI",
    }]
    out = apply_hard_validation(facts)
    assert out[0]["metric_or_parameter"] == "fiber_length"
    assert "nanofiber" in out[0]["assigned_sample_id"].lower()


def test_existing_condition_is_not_polluted_by_other_result_conditions():
    facts = [{
        "fact_id": "F1",
        "fact_type": "performance",
        "metric_or_parameter": "weight_loss",
        "value": "13.30",
        "unit": "%",
        "condition": "at 250 °C",
        "evidence_text": (
            "Weight loss was 13.30% at 250 °C; beyond this temperature it "
            "reached 44.36% at 350 °C."
        ),
        "assigned_sample_id": "raw jute",
    }]

    out = apply_hard_validation(facts)

    assert out[0]["condition"] == "at 250 °C"


def test_temperature_performance_is_not_relabelled_as_test_condition():
    facts = [{
        "fact_id": "F1",
        "fact_type": "performance",
        "metric_or_parameter": "decomposition_temperature",
        "value": "350",
        "unit": "°C",
        "evidence_text": "The decomposition temperature reached 350 °C.",
        "assigned_sample_id": "S1",
    }]

    out = apply_hard_validation(facts)

    assert out[0]["metric_or_parameter"] == "decomposition_temperature"
    assert "temperature_moved_to_condition" not in (
        out[0].get("assignment_reason") or ""
    )


def test_cold_crystallization_temperature_remains_a_performance_value():
    facts = [{
        "fact_id": "F1",
        "fact_type": "performance",
        "metric_or_parameter": "cold_crystallization_temperature",
        "value": "101.63",
        "unit": "°C",
        "evidence_text": "Table 2 reports Tcc = 101.63 °C for PLA/FR 4%.",
        "assigned_sample_id": "PLA/FR 4%",
    }]

    out = apply_hard_validation(facts)

    assert out[0]["value"] == "101.63"
    assert out[0]["unit"] == "°C"
    assert "temperature_moved_to_condition" not in (
        out[0].get("assignment_reason") or ""
    )

def test_absolute_loss_modulus_is_not_a_power_law_index():
    evidence = (
        "At an angular frequency of 100 rad s-1, G″ increased from "
        "15496 Pa to 55990 Pa."
    )

    assert infer_metric_from_evidence(
        evidence,
        unit="Pa",
        current_metric="loss_modulus_power_law_index",
        value="15496",
    ) == "loss_modulus"


def test_storage_modulus_temperature_drop_maps_to_glass_transition_range():
    evidence = (
        "A noticeable drop in G' was observed between 50 °C and 70 °C "
        "across all samples, indicating the occurrence of the glass transition."
    )

    for value in ("50", "70"):
        assert infer_metric_from_evidence(
            evidence,
            unit="°C",
            current_metric="storage_modulus",
            value=value,
        ) == "glass_transition_temperature"
