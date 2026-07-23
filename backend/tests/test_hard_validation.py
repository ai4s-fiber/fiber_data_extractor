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
