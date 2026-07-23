"""Hard validation regression tests for user-reported error patterns."""

from app.services.extractor_v7.hard_validation import (
    apply_hard_validation,
    find_explicit_transition_matches,
    infer_metric_from_evidence,
    refine_sample_name_before_paren,
)
from app.services.extractor_v7.sample_value_alignment import apply_sample_value_alignment


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
