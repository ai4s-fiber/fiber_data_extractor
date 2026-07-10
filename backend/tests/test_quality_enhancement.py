"""Quality enhancement regression tests."""

from app.services.extractor_v7.quality_enhancement import (
    apply_fact_quality_enhancements,
    classify_export_tier,
    detect_unit_conflict,
    infer_paper_theme,
    normalize_sample_display_name,
    restructure_loading_cycles_fact,
    should_reject_emi_shielding_fact,
)
from app.services.extractor_v7.metric_normalize import canonicalize_metric_name


def test_loss_tangent_canonical_name():
    assert canonicalize_metric_name("loss tangent") == "loss_tangent"
    assert canonicalize_metric_name("tan delta") == "loss_tangent"
    assert canonicalize_metric_name("dielectric loss") == "dielectric_loss"


def test_restructure_loading_cycles_moves_count_to_condition():
    fact = {
        "fact_type": "performance",
        "metric_or_parameter": "loading_unloading_cycles",
        "value": "500",
        "unit": "cycles",
        "evidence_text": "500 compression cycles at 50% strain with no stress decay",
        "condition": "",
    }
    out = restructure_loading_cycles_fact(fact)
    assert out["metric_or_parameter"] == "cyclic_compression_stability"
    assert out["value"] == "no stress decay"
    assert "500 compression cycles" in out["condition"]


def test_emi_se_filtered_for_transparent_paper():
    fact = {
        "fact_type": "performance",
        "metric_or_parameter": "electromagnetic_interference_shielding_effectiveness",
        "value": "47.8",
        "unit": "dB",
        "evidence_text": "reported SE values up to 47.8 dB in previous studies [12]",
        "_chunk_section": "introduction",
    }
    themes = {"low_dielectric_transparent"}
    assert should_reject_emi_shielding_fact(fact, themes)


def test_background_intro_fact_marked_tier_c():
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "density",
        "value": "15",
        "unit": "mg/cm3",
        "evidence_text": "Previously reported aerogels showed density of 15 mg cm-3 [8]",
        "_chunk_section": "introduction",
        "assigned_sample_id": "PI-200°C",
    }]
    out = apply_fact_quality_enhancements(facts, chunks=[{
        "section_name": "introduction",
        "raw_text": "Previously reported aerogels showed density of 15 mg cm-3 [8]",
    }])
    assert out[0].get("_export_tier") == "C"


def test_sample_form_mismatch_tensile_on_aerogel_is_tier_b():
    fact = {
        "fact_type": "performance",
        "metric_or_parameter": "tensile_strength",
        "value": "2.82",
        "unit": "MPa",
        "evidence_text": "2MZ-AZINE-PI3 aerogel tensile strength 2.82 MPa",
        "assigned_sample_id": "2MZ-AZINE-PI3 aerogel",
        "source_location": "p.5, Fig. 3",
    }
    assert classify_export_tier(fact) == "B"


def test_unit_conflict_for_aerogel_mpa():
    fact = {
        "metric_or_parameter": "compressive_stress",
        "value": "7.13",
        "unit": "MPa",
        "evidence_text": "PI1 aerogel compressive stress 7.13 MPa, figure axis in kPa",
        "assigned_sample_id": "PI1 aerogel",
    }
    assert detect_unit_conflict(fact)


def test_normalize_pi1_display_name():
    assert normalize_sample_display_name("PI1") == "PI1 aerogel"


def test_infer_transparent_theme_from_title():
    themes = infer_paper_theme(
        chunks=[{"section_name": "title_abstract", "raw_text": "Electromagnetic wave-transparent PI aerogels with low dielectric loss"}],
        paper_metadata={"paper_title": "Low dielectric PI aerogel"},
    )
    assert "low_dielectric_transparent" in themes
