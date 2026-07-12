"""Tests for sample_id, scientific notation, and metric semantic rules."""

from app.services.extractor_v7.sample_id_rules import sanitize_sample_id
from app.services.extractor_v7.sample_value_alignment import (
    apply_sample_value_alignment,
    expand_multi_entity_facts,
)
from app.services.extractor_v7.value_parse import parse_scientific_value, reconcile_value_with_evidence


def test_scientific_notation_parsing():
    assert parse_scientific_value("8 × 10^-4") == "8e-4"
    assert parse_scientific_value("8x10-4") == "8e-4"
    assert parse_scientific_value("8e-4") == "8e-4"
    assert parse_scientific_value("1.5×10^3") == "1.5e3"


def test_reconcile_mantissa_from_evidence():
    ev = "loss tangent of 8 × 10^-4"
    fixed, changed = reconcile_value_with_evidence("8", ev)
    assert changed
    assert fixed == "8e-4"


def test_condition_not_sample_id():
    sid, cond, notes = sanitize_sample_id("200 °C", "")
    assert sid == ""
    assert "200" in cond.lower() or "°c" in cond.lower()
    assert "sample_id_was_condition_only" in notes


def test_explicit_pi200_sample_kept():
    ev = "The PI-200°C sample showed high strength."
    sid, _, _ = sanitize_sample_id("PI-200°C", ev)
    assert "200" in sid.lower() or "pi" in sid.lower()


def test_strip_inferred_loading():
    ev = "2MZ-AZINE-PI nanofibers were prepared."
    sid, _, notes = sanitize_sample_id("2MZ-AZINE-PI-20% nanofiber", ev)
    assert "20" not in sid or "removed_inferred" in str(notes)


def test_loading_suffix_with_unit_does_not_crash_or_strip_when_evidenced():
    ev = "The PVDF composite with 10 wt% CNT showed increased modulus."
    sid, _, notes = sanitize_sample_id("PVDF-10wt%", ev)
    assert sid == "PVDF-10wt%"
    assert "removed_inferred_loading_from_sample_id" not in notes


def test_strip_inferred_temperature():
    ev = "2MZ-AZINE-PI3 aerogel was tested."
    sid, _, notes = sanitize_sample_id("2MZ-AZINE-PI-200 °C", ev)
    assert "200" not in sid or "removed_inferred_temperature" in str(notes)


def test_imidization_not_crystallinity():
    facts = [{
        "fact_id": "F001",
        "fact_type": "performance",
        "metric_or_parameter": "crystallinity_Xc",
        "value": "95.39",
        "unit": "%",
        "evidence_text": "achieving 95.39% imidization at 300 °C",
        "assigned_sample_id": "PI1",
    }]
    out = apply_sample_value_alignment(facts)
    assert out[0]["metric_or_parameter"] == "imidization_degree"


def test_compressive_stress_from_to_split():
    facts = [{
        "fact_id": "F001",
        "fact_type": "performance",
        "metric_or_parameter": "cyclic_compression_stability",
        "value": "500",
        "unit": "cycles",
        "evidence_text": "compressive stress decreased from 7.13 to 6.14 after 500 cycles",
        "assigned_sample_id": "PI1",
    }]
    out = apply_sample_value_alignment(facts)
    values = {str(f["value"]) for f in out}
    assert "7.13" in values
    assert "6.14" in values
    stress = [f for f in out if f["metric_or_parameter"] == "compressive_stress"]
    assert len(stress) == 2


def test_expand_multi_metric_direct():
    facts = [{
        "fact_id": "F001",
        "fact_type": "performance",
        "metric_or_parameter": "dielectric_constant",
        "value": "1.004",
        "evidence_text": "permittivity of 1.004 and loss tangent of 8 × 10^-4",
        "assigned_sample_id": "PI1",
    }]
    out = expand_multi_entity_facts(facts)
    metrics = {f["metric_or_parameter"] for f in out}
    assert "loss_tangent" in metrics


def test_permittivity_loss_tangent_split():
    facts = [{
        "fact_id": "F001",
        "fact_type": "performance",
        "metric_or_parameter": "dielectric_constant",
        "value": "1.004",
        "evidence_text": "permittivity of 1.004 and loss tangent of 8 × 10^-4",
        "assigned_sample_id": "PI1",
    }]
    out = apply_sample_value_alignment(facts)
    metrics = {f["metric_or_parameter"] for f in out}
    assert "loss_tangent" in metrics
    lt = next(f for f in out if f["metric_or_parameter"] == "loss_tangent")
    assert lt["value"] in ("8e-4", "0.0008")
    assert not any(
        f.get("metric_or_parameter") == "dielectric_constant" and f.get("value") in ("8e-4", "0.0008")
        for f in out
    )
