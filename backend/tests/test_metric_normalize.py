"""Generic metric normalization tests."""

from app.services.extractor_v7.metric_normalize import (
    canonicalize_metric_name,
    merge_duplicate_facts,
    normalize_metrics_in_facts,
    normalize_spectroscopy_peaks,
)
from app.services.metrics_dictionary import (
    find_metric_canonical,
    find_process_parameter_canonical,
)


def test_canonicalize_dielectric_synonym():
    assert canonicalize_metric_name("relative permittivity") == "dielectric_constant"
    assert canonicalize_metric_name("loss tangent") == "loss_tangent"


def test_ph_unit_and_evidence_override_unrelated_metric_label():
    assert canonicalize_metric_name(
        "beta_phase_crystallinity_Xbeta",
        evidence="The pH trend increased up to 7.60 after immersion.",
        unit="pH",
    ) == "pH"


def test_short_metric_symbol_does_not_use_substring_lookup():
    assert find_metric_canonical("E") is None
    assert find_metric_canonical("UTS") == "tensile_strength"


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


def test_duplicate_spectroscopy_values_receive_same_metric_before_merge():
    base = {
        "fact_type": "performance",
        "assigned_sample_id": "PCL/AA",
        "metric_or_parameter": "wavenumber",
        "value": "1722",
        "unit": "cm^-1",
        "method": "FTIR",
        "evidence_text": "The carbonyl stretching peak was 1722 cm^-1.",
        "source_location": "p.7 block B116",
    }
    facts = normalize_spectroscopy_peaks([
        {**base, "fact_id": "F1", "extraction_method": "AI_text"},
        {**base, "fact_id": "F2", "extraction_method": "AI_holistic"},
    ])

    assert facts[0]["metric_or_parameter"] == facts[1]["metric_or_parameter"]
    assert len(merge_duplicate_facts(facts)) == 1


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


def test_same_page_range_restatement_is_merged_across_wording_variants():
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPMS composite",
            "metric_or_parameter": "normalized_bandgap_frequency_range",
            "value": "0.145 to 0.194",
            "unit": "dimensionless",
            "condition": "directional bandgap; c0=144 m/s",
            "source_location": "page 5, Figure 4c",
            "evidence_text": "The normalized frequency is 0.145 to 0.194.",
            "extraction_method": "AI_holistic",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPMS composite",
            "metric_or_parameter": "normalized_bandgap_frequency_range",
            "value": "0.145-0.194",
            "unit": "dimensionless",
            "condition": "",
            "source_location": "page 5, block B000072",
            "evidence_text": "Corresponding normalized frequency: 0.145-0.194.",
            "extraction_method": "rule_text_range",
        },
    ]

    merged = merge_duplicate_facts(facts)

    assert len(merged) == 1
    assert merged[0]["extraction_method"] == "rule_text_range"


def test_normalize_metrics_in_facts_applies_dictionary():
    facts = [{"fact_type": "performance", "metric_or_parameter": "open circuit voltage", "value": "5"}]
    out = normalize_metrics_in_facts(facts)
    assert out[0]["metric_or_parameter"] == "open_circuit_voltage"


def test_normalize_metrics_keeps_process_namespace_separate_from_performance():
    facts = [
        {"fact_type": "process", "metric_or_parameter": "voltage", "value": "25"},
        {"fact_type": "process", "metric_or_parameter": "total_flow_rate", "value": "9"},
    ]

    out = normalize_metrics_in_facts(facts)

    assert [fact["metric_or_parameter"] for fact in out] == ["voltage", "total_flow_rate"]


def test_oil_sorption_and_absorption_share_one_canonical_metric():
    assert find_metric_canonical("oil sorption capacity") == "oil_absorption_capacity"
    assert find_metric_canonical("oil_sorption_capacity") == "oil_absorption_capacity"


def test_process_parameter_matching_prefers_specific_flow_and_electrospinning_names():
    assert find_process_parameter_canonical("Total flowrate (mL/hr)") == "total_flow_rate"
    assert find_process_parameter_canonical("Flowrate per needle (mL/hr)") == "flow_rate_per_needle"
    assert find_process_parameter_canonical("Distance between needles (mm)") == "needle_spacing"
    assert find_process_parameter_canonical("Electric field strength (kV/cm)") == "electric_field_strength"


def test_mechanical_table_symbols_map_to_canonical_metrics():
    assert find_metric_canonical("E1") == "Youngs_modulus"
    assert find_metric_canonical("modulus_E2") == "Youngs_modulus"
    assert find_metric_canonical("sigma_R (sigma_u)") == "tensile_strength"
    assert find_metric_canonical("epsilon_R (epsilon_u)") == "elongation_at_break"
    assert find_metric_canonical("varepsilon_r_varepsilon_u") == "elongation_at_break"
    assert find_metric_canonical(r"\varepsilon_R (\varepsilon_u)") == "elongation_at_break"
    assert find_metric_canonical("threshold_load") == "inelastic_threshold_stress"
    assert find_metric_canonical("modulus_e_gamma_star") == "Youngs_modulus"


def test_table_metric_normalization_strips_latex_and_unit_suffix():
    assert canonicalize_metric_name(r"$E_{1}$ [GPa]") == "Youngs_modulus"
    assert canonicalize_metric_name("e_1_gpa") == "Youngs_modulus"
    assert canonicalize_metric_name("modulus_Egamma_star") == "Youngs_modulus"


def test_known_bandgap_metric_is_not_rewritten_as_spectroscopy_peak():
    facts = [{
        "fact_type": "performance",
        "assigned_sample_id": "TPMS composite",
        "metric_or_parameter": "bandgap frequency range",
        "value": "1050-1400",
        "unit": "Hz",
        "evidence_text": "A directional bandgap occurred from 1050 to 1400 Hz.",
    }]

    out = normalize_metrics_in_facts(facts)

    assert out[0]["metric_or_parameter"] == "bandgap_frequency_range"


def test_evidence_repairs_poisson_fraction_and_displacement_mislabels():
    assert canonicalize_metric_name(
        "orientation_factor",
        evidence="The Poisson's ratio was 0.42.",
    ) == "Poissons_ratio"
    assert canonicalize_metric_name(
        "orientation_factor",
        evidence="The fiber volume fraction was 10%.",
    ) == "fiber_volume_fraction"
    assert canonicalize_metric_name(
        "surface_roughness",
        evidence="The compressive displacement was 8.8 mm at 350 N.",
    ) == "compressive_displacement"
    assert canonicalize_metric_name(
        "surface_roughness",
        evidence="The Poisson’s ratio was 0.42.",
    ) == "Poissons_ratio"


def test_transition_displacement_alias_uses_compressive_displacement():
    assert canonicalize_metric_name(
        "re_stiffening_displacement",
        evidence=(
            "The response changed from a compliant regime to a stiff response "
            "at a displacement of approximately 17 mm."
        ),
        unit="mm",
    ) == "compressive_displacement"


def test_transition_displacement_alias_merges_with_deterministic_recovery():
    source_block_id = "B000117"
    facts = normalize_metrics_in_facts([
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPMS composite",
            "metric_or_parameter": "re_stiffening_displacement",
            "value": "17",
            "unit": "mm",
            "evidence_text": (
                "The response changed from compliant to stiff behavior at a "
                "displacement of 17 mm."
            ),
            "extraction_method": "AI_holistic",
            "_source_block_id": source_block_id,
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPMS composite",
            "metric_or_parameter": "compressive_displacement",
            "value": "≈17",
            "unit": "mm",
            "evidence_text": (
                "At a displacement of approximately 17 mm, the specimen "
                "transitioned from compliant behavior to a re-stiffening regime."
            ),
            "extraction_method": "rule_text_transition",
            "_source_block_id": source_block_id,
        },
    ])

    merged = merge_duplicate_facts(facts)

    assert len(merged) == 1
    assert merged[0]["metric_or_parameter"] == "compressive_displacement"
    assert merged[0]["extraction_method"] == "rule_text_transition"


def test_compressive_load_displacement_alias_maps_to_canonical_metric():
    assert (
        find_metric_canonical("displacement_at_compressive_load")
        == "compressive_displacement"
    )


def test_evidence_repairs_transmission_range_and_acceleration_reduction():
    assert canonicalize_metric_name(
        "directional bandgap",
        evidence=(
            "The transmission spectrum showed a clear decay in transmission "
            "efficiency from 1250 to 1500 Hz due to the directional bandgap."
        ),
        unit="Hz",
    ) == "transmission_attenuation_frequency_range"
    assert canonicalize_metric_name(
        "maximum_acceleration",
        evidence="The maximum acceleration decreased by more than 46%.",
        unit="%",
    ) == "acceleration_reduction"


def test_merge_duplicate_facts_normalizes_dash_ranges_but_keeps_units_distinct():
    base = {
        "fact_type": "performance",
        "assigned_sample_id": "S1",
        "metric_or_parameter": "stiffness_recovery_strain",
        "condition": "",
        "evidence_text": "S1 shows stiffness recovery beyond 0.7-0.8% strain.",
    }
    facts = [
        {**base, "value": "0.7-0.8", "unit": "%", "extraction_method": "AI_text"},
        {**base, "value": "0.7–0.8", "unit": "%", "extraction_method": "AI_holistic"},
        {**base, "value": "0.7-0.8", "unit": "fraction", "extraction_method": "AI_text"},
    ]

    merged = merge_duplicate_facts(facts)

    assert len(merged) == 2
    assert any(fact.get("extraction_method") == "AI_holistic" for fact in merged)


def test_merge_duplicate_facts_collapses_equivalent_condition_wording():
    evidence = "S1 reached a maximum acceleration of 37 at an impact velocity of 5 m/s."
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "S1",
            "metric_or_parameter": "maximum_acceleration",
            "value": "37",
            "unit": "dimensionless",
            "condition": "impact velocity was 5 m/s",
            "evidence_text": evidence,
            "extraction_method": "AI_text",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "S1",
            "metric_or_parameter": "maximum_acceleration",
            "value": "37",
            "unit": "dimensionless",
            "condition": "impact velocity 5 m s^-1; dimensionless conditions",
            "evidence_text": f"Figure 5. {evidence}",
            "extraction_method": "AI_holistic",
        },
    ]

    merged = merge_duplicate_facts(facts)

    assert len(merged) == 1
    assert merged[0]["extraction_method"] == "AI_holistic"


def test_same_block_range_recovery_deduplicates_and_prefers_rule():
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPU_TPMS_10vol%",
            "metric_or_parameter": "normalized_bandgap_frequency_range",
            "value": "0.145-0.194",
            "unit": "dimensionless",
            "condition": "normalized frequency expression using c0=144 m/s",
            "evidence_text": "A normalized bandgap of 0.145-0.194 was obtained.",
            "extraction_method": "AI_holistic",
            "source_location": "page 5, Figure 4c",
            "_source_block_id": "B000071",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPU_TPMS_10vol%",
            "metric_or_parameter": "normalized_bandgap_frequency_range",
            "value": "0.145–0.194",
            "unit": "dimensionless",
            "condition": "",
            "evidence_text": (
                "The directional bandgap corresponds to a normalized frequency "
                "range from 0.145 to 0.194."
            ),
            "extraction_method": "rule_text_range",
            "_source_block_id": "B000071",
        },
    ]

    merged = merge_duplicate_facts(facts)

    assert len(merged) == 1
    assert merged[0]["extraction_method"] == "rule_text_range"


def test_dimensionless_range_empty_unit_merges_with_deterministic_recovery():
    source_block_id = "B000072"
    facts = normalize_metrics_in_facts([
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPMS composite",
            "metric_or_parameter": "normalized_bandgap_frequency_range",
            "value": "0.145",
            "unit": "",
            "condition": "corresponding to bandgap 1050 to 1400 Hz; range_min",
            "evidence_text": "The corresponding normalized frequency is 0.145 to 0.194.",
            "extraction_method": "AI_holistic",
            "_source_block_id": source_block_id,
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPMS composite",
            "metric_or_parameter": "normalized_bandgap_frequency_range",
            "value": "0.145",
            "unit": "dimensionless",
            "condition": "range_min",
            "evidence_text": (
                "The normalized bandgap frequency range extends from "
                "0.145 to 0.194."
            ),
            "extraction_method": "rule_text_range",
            "_source_block_id": source_block_id,
        },
    ])

    assert {fact["unit"] for fact in facts} == {"dimensionless"}

    merged = merge_duplicate_facts(facts)

    assert len(merged) == 1
    assert merged[0]["unit"] == "dimensionless"
    assert merged[0]["extraction_method"] == "rule_text_range"


def test_explicit_dimensionless_acceleration_empty_unit_merges():
    evidence = (
        "The composite had a dimensionless maximum acceleration of 37 "
        "at an impact velocity of 5 m/s."
    )
    facts = normalize_metrics_in_facts([
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPMS composite",
            "metric_or_parameter": "maximum_acceleration",
            "value": "37",
            "unit": "",
            "condition": "impact velocity 5 m/s",
            "evidence_text": evidence,
            "extraction_method": "AI_text",
            "_source_block_id": "B000100",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPMS composite",
            "metric_or_parameter": "maximum_acceleration",
            "value": "37",
            "unit": "dimensionless",
            "condition": "impact velocity 5 m/s",
            "evidence_text": evidence,
            "extraction_method": "AI_holistic",
            "_source_block_id": "B000100",
        },
    ])

    assert {fact["unit"] for fact in facts} == {"dimensionless"}
    assert len(merge_duplicate_facts(facts)) == 1
