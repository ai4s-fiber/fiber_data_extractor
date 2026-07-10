"""Hard validation regression tests for user-reported error patterns."""

from app.services.extractor_v7.hard_validation import (
    apply_hard_validation,
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
