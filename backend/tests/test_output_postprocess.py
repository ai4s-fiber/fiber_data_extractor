"""Tests for pre-output validation rules."""

from app.services.extractor_v7.output_postprocess import apply_pre_output_validation
from app.services.validation import (
    is_characterization_peak_metric,
    is_formula_method_parameter_fact,
    metric_unit_compatible,
)


def test_surface_roughness_incompatible_with_density_unit():
    assert not metric_unit_compatible("surface_roughness", "mg/cm3")


def test_density_compatible_with_g_cm3():
    assert metric_unit_compatible("density", "g/cm3")


def test_ftir_peak_routed_to_characterization():
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "FTIR_band_1",
        "value": "1377",
        "unit": "cm-1",
        "method": "FTIR",
        "evidence_text": "FTIR band at 1377 cm-1 for C-N stretch",
        "assigned_sample_id": "PI1",
    }]
    out = apply_pre_output_validation(facts, [])
    assert out[0]["_output_channel"] == "characterization_feature"


def test_imidization_formula_peak_not_performance():
    fact = {
        "fact_type": "performance",
        "metric_or_parameter": "wavenumber",
        "value": "1377",
        "unit": "cm-1",
        "evidence_text": "imidization degree was calculated using peaks at 1377 and 1489 cm-1",
    }
    assert is_formula_method_parameter_fact(fact)
    out = apply_pre_output_validation([fact], [])
    assert out[0]["_output_channel"] == "formula_or_method_parameter"


def test_pi1_not_collapsed_to_pi():
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "density",
        "value": "0.05",
        "unit": "g/cm3",
        "evidence_text": "PI1 aerogel showed low density",
        "assigned_sample_id": "PI aerogel",
    }]
    out = apply_pre_output_validation(facts, [{"sample_id": "PI1 aerogel"}])
    assert "1" in (out[0].get("assigned_sample_id") or "")


def test_imidization_corrected_from_crystallinity():
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "crystallinity_Xc",
        "value": "95.39",
        "unit": "%",
        "evidence_text": "achieving 95.39% imidization",
        "assigned_sample_id": "PI1",
    }]
    out = apply_pre_output_validation(facts, [])
    assert out[0]["metric_or_parameter"] == "imidization_degree"


def test_metric_unit_mismatch_corrects_density():
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "surface_roughness",
        "value": "0.12",
        "unit": "g/cm3",
        "evidence_text": "apparent density of the aerogel was 0.12 g/cm3",
        "assigned_sample_id": "PI3",
    }]
    out = apply_pre_output_validation(facts, [])
    assert out[0]["metric_or_parameter"] == "density"
    assert not out[0].get("_metric_unit_mismatch")


def test_is_characterization_peak_metric_generic():
    assert is_characterization_peak_metric(
        "peak_position", method="FTIR", evidence="FTIR spectrum peak"
    )
