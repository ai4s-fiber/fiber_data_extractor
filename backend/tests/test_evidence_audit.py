"""Evidence reverse lookup tests for user-reported failure patterns."""

from app.services.extractor_v7.evidence_audit import (
    apply_evidence_reverse_lookup,
    audit_fact_against_evidence,
    is_spurious_dielectric_fact,
)
from app.services.extractor_v7.sample_value_alignment import apply_sample_value_alignment


def _perf(**kwargs):
    base = {
        "fact_id": "F1",
        "fact_type": "performance",
        "evidence_text": "",
        "assigned_sample_id": "",
    }
    base.update(kwargs)
    return base


def test_drop_dielectric_constant_8_from_loss_tangent():
    ev = "permittivity of 1.004 and loss tangent of 8 × 10^-4 in X-band (8–12 GHz)"
    fact = _perf(
        metric_or_parameter="dielectric_constant",
        value="8",
        assigned_sample_id="2MZ-AZINE-PI3 aerogel",
        evidence_text=ev,
    )
    assert is_spurious_dielectric_fact(fact)
    out = apply_evidence_reverse_lookup([fact])
    # Rejected facts are now preserved with audit failure flag instead of being dropped
    assert len(out) == 1
    assert out[0].get("_evidence_audit_failed") is True
    assert out[0].get("_export_tier") == "C"


def test_drop_dielectric_constant_10_reference():
    ev = "The permittivity is close to air (1.0) while 2MZ-AZINE-PI3 aerogel showed 1.004"
    fact = _perf(
        metric_or_parameter="dielectric_constant",
        value="1.0",
        assigned_sample_id="2MZ-AZINE-PI3 aerogel",
        evidence_text=ev,
    )
    assert is_spurious_dielectric_fact(fact)


def test_keep_valid_dielectric_and_loss_tangent():
    ev = "permittivity of 1.004 and loss tangent of 8 × 10^-4"
    facts = [
        _perf(
            metric_or_parameter="dielectric_constant",
            value="1.004",
            assigned_sample_id="2MZ-AZINE-PI3 aerogel",
            evidence_text=ev,
        ),
        _perf(
            fact_id="F2",
            metric_or_parameter="dielectric_constant",
            value="8e-4",
            assigned_sample_id="2MZ-AZINE-PI3 aerogel",
            evidence_text=ev,
        ),
    ]
    out = apply_sample_value_alignment(facts)
    metrics = {f["metric_or_parameter"]: f["value"] for f in out}
    assert metrics.get("dielectric_constant") == "1.004"
    assert metrics.get("loss_tangent") in ("8e-4", "0.0008")
    assert not any(
        f.get("metric_or_parameter") == "dielectric_constant" and str(f.get("value")) == "8"
        for f in out
    )


def test_fiber_length_comparison_not_on_pi():
    ev = "2MZ-AZINE-PI (40.5 μm) was greater than PI (22.8 μm) after 30 min homogenization"
    fact = _perf(
        metric_or_parameter="fiber_length",
        value="40.5",
        unit="μm",
        assigned_sample_id="PI",
        evidence_text=ev,
    )
    out = audit_fact_against_evidence(fact)
    assert out is not None
    assert "2MZ-AZINE-PI" in out["assigned_sample_id"]
    assert normalize_not_pi(out["assigned_sample_id"])


def normalize_not_pi(sid: str) -> bool:
    from app.services.grouping import normalize_for_match
    return normalize_for_match(sid) != "pi"


def test_surface_temperature_pi200_reassigned_to_pi1():
    ev = "PI1 (150.2 °C) on the 400 °C hot stage compared with 2MZ-AZINE-PI3 aerogel (117.8 °C)"
    fact = _perf(
        metric_or_parameter="surface_temperature",
        value="150.2",
        unit="°C",
        assigned_sample_id="PI-200 °C",
        evidence_text=ev,
    )
    out = audit_fact_against_evidence(fact)
    assert out is not None
    assert "PI1" in out["assigned_sample_id"]
    assert "200" not in out["assigned_sample_id"]


def test_surface_temperature_117_not_on_pi1():
    ev = "2MZ-AZINE-PI3 aerogel (117.8 °C) and PI1 (150.2 °C) on 400 °C hot stage"
    fact = _perf(
        metric_or_parameter="surface_temperature",
        value="117.8",
        unit="°C",
        assigned_sample_id="2MZ-AZINE-PI1",
        evidence_text=ev,
    )
    out = audit_fact_against_evidence(fact)
    assert out is not None
    assert "PI3" in out["assigned_sample_id"]


def test_pi200_thermal_conductivity_to_pi1():
    ev = "thermal conductivities of 2MZ-AZINE-PI1, 2MZ-AZINE-PI2, 2MZ-AZINE-PI3 and PI1 were 26.2, 25.9, 25.3 and 26.9 mW/m·K"
    fact = _perf(
        metric_or_parameter="thermal_conductivity",
        value="26.9",
        unit="mW/m·K",
        assigned_sample_id="PI-200 °C",
        evidence_text=ev,
    )
    out = audit_fact_against_evidence(fact)
    assert out is not None
    assert "PI1" in out["assigned_sample_id"]


def test_drop_compressive_stress_on_wrong_sample():
    ev = "PI1 aerogel compressive stress decreased from 7.13 to 6.14 after 500 cycles at 50% strain"
    fact = _perf(
        metric_or_parameter="compressive_stress",
        value="7.13",
        unit="MPa",
        assigned_sample_id="2MZ-AZINE-PI1",
        evidence_text=ev,
    )
    out = audit_fact_against_evidence(fact)
    assert out is not None
    assert "PI1" in out["assigned_sample_id"]


def test_density_pi_to_pi1_aerogel():
    ev = "PI1 (12.38 mg/cm3), PI3 (4.74 mg/cm3)"
    fact = _perf(
        metric_or_parameter="density",
        value="12.38",
        unit="mg/cm3",
        assigned_sample_id="PI",
        evidence_text=ev,
    )
    out = apply_sample_value_alignment([fact])
    assert out[0]["assigned_sample_id"] == "PI1"
