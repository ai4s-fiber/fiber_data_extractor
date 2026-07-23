"""Tests for pre-output validation rules."""

from app.services.extractor_v7.output_postprocess import (
    apply_pre_output_validation,
    infer_characterization_method,
    merge_characterization_features,
)
from app.services.validation import (
    is_characterization_peak_metric,
    is_formula_method_parameter_fact,
    metric_unit_compatible,
)


def test_surface_roughness_incompatible_with_density_unit():
    assert not metric_unit_compatible("surface_roughness", "mg/cm3")


def test_density_compatible_with_g_cm3():
    assert metric_unit_compatible("density", "g/cm3")


def test_table_header_brackets_do_not_break_unit_compatibility():
    assert metric_unit_compatible("Youngs_modulus", "[GPa]")
    assert metric_unit_compatible("tensile_strength", "[MPa]")
    assert metric_unit_compatible("elongation_at_break", "[%]")


def test_spaced_inverse_cubic_density_unit_is_normalized():
    assert metric_unit_compatible("density", "kg m^-3")


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


def test_serialized_characterization_features_are_merged_as_entries():
    merged = merge_characterization_features(
        "fiber_diameter=250nm",
        "FTIR_band_1=1240cm^-1; FTIR_band_2=1165cm^-1",
    )

    assert merged == (
        "fiber_diameter=250nm; FTIR_band_1=1240cm^-1; "
        "FTIR_band_2=1165cm^-1"
    )


def test_characterization_method_is_inferred_from_metric_when_missing():
    assert infer_characterization_method({"canonical_metric": "FTIR_band_3"}) == "FTIR"
    assert infer_characterization_method({"raw_metric": "Raman_peak_1"}) == "Raman"
    assert infer_characterization_method({"canonical_metric": "porosity"}) == ""


def test_registered_fiber_diameter_stays_in_main_performance_output():
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "fiber_diameter",
        "value": "74",
        "unit": "nm",
        "method": "SEM",
        "evidence_text": "The average fiber diameter was 74 nm.",
        "assigned_sample_id": "PAN_nanofiber_17_needles",
    }]

    out = apply_pre_output_validation(facts, [])

    assert out[0]["_output_channel"] == "performance"


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


def test_reference_wave_velocity_in_normalization_formula_not_performance():
    fact = {
        "fact_type": "performance",
        "metric_or_parameter": "elastic_wave_velocity",
        "value": "144",
        "unit": "m/s",
        "evidence_text": (
            "The normalized frequency is calculated using the expression "
            "Omega=fa/c0, where c0=144 m/s is the reference wave velocity."
        ),
    }

    assert is_formula_method_parameter_fact(fact)
    out = apply_pre_output_validation([fact], [])
    assert out[0]["_output_channel"] == "formula_or_method_parameter"


def test_truncated_reference_wave_velocity_definition_not_performance():
    fact = {
        "fact_type": "performance",
        "subject_text": "elastic_wave_velocity",
        "metric_or_parameter": "elastic_wave_velocity",
        "value": "144",
        "unit": "m s^-1",
        "method": "calculation",
        "evidence_text": "where c0 is the elastic wave velocity, which is approximately 144 m s^-1",
        "assigned_sample_id": "TPU matrix",
    }

    assert is_formula_method_parameter_fact(fact)
    out = apply_pre_output_validation([fact], [])
    assert out[0]["_output_channel"] == "formula_or_method_parameter"


def test_unitless_poisson_ratio_is_repaired_from_evidence_before_output():
    fact = {
        "fact_type": "performance",
        "metric_or_parameter": "surface_roughness",
        "value": "0.43",
        "unit": "",
        "evidence_text": "The matrix material has a Poisson's ratio of 0.43.",
        "assigned_sample_id": "TPU_matrix",
    }

    out = apply_pre_output_validation([fact], [])

    assert out[0]["metric_or_parameter"] == "Poissons_ratio"
    assert out[0]["_output_channel"] == "performance"


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
