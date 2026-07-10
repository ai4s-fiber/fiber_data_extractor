"""Sample-value alignment tests."""

from app.services.extractor_v7.sample_value_alignment import (
    apply_sample_value_alignment,
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
