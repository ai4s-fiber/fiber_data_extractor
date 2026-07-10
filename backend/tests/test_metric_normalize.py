"""Generic metric normalization tests."""

from app.services.extractor_v7.metric_normalize import (
    canonicalize_metric_name,
    merge_duplicate_facts,
    normalize_metrics_in_facts,
    normalize_spectroscopy_peaks,
)


def test_canonicalize_dielectric_synonym():
    assert canonicalize_metric_name("relative permittivity") == "dielectric_constant"
    assert canonicalize_metric_name("loss tangent") == "loss_tangent"


def test_normalize_spectroscopy_peaks_numbers_ftir_bands():
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "S1",
            "metric_or_parameter": "wavenumber",
            "value": "840",
            "method": "FTIR",
            "evidence_text": "beta phase FTIR band",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "S1",
            "metric_or_parameter": "wavenumber",
            "value": "1276",
            "method": "FTIR",
            "evidence_text": "beta phase FTIR band",
        },
    ]
    out = normalize_spectroscopy_peaks(facts)
    metrics = {f["metric_or_parameter"] for f in out}
    assert "beta_phase_FTIR_band_1" in metrics
    assert "beta_phase_FTIR_band_2" in metrics


def test_merge_duplicate_facts_prefers_holistic():
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "S1",
            "metric_or_parameter": "tensile_strength",
            "value": "120",
            "extraction_method": "AI_text",
            "evidence_text": "short",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "S1",
            "metric_or_parameter": "tensile_strength",
            "value": "120",
            "extraction_method": "AI_holistic",
            "evidence_text": "longer evidence with sample S1 tensile strength 120 MPa",
        },
    ]
    merged = merge_duplicate_facts(facts)
    perf = [f for f in merged if f.get("fact_type") == "performance"]
    assert len(perf) == 1
    assert perf[0]["extraction_method"] == "AI_holistic"


def test_normalize_metrics_in_facts_applies_dictionary():
    facts = [{"fact_type": "performance", "metric_or_parameter": "open circuit voltage", "value": "5"}]
    out = normalize_metrics_in_facts(facts)
    assert out[0]["metric_or_parameter"] == "open_circuit_voltage"
