"""Holistic extraction unit tests."""

import asyncio

import pytest

from app.services.extractor_v7.holistic_extract import (
    PERFORMANCE_PROMPT,
    SENSING_SWEEP_PROMPT,
    SPECTROSCOPY_SWEEP_PROMPT,
    augment_catalog_samples_from_process_tables,
    catalog_supports_shared_background,
    classify_table_role,
    deterministic_performance_table_facts,
    enrich_sample_cards,
    merge_holistic_and_atomic_facts,
    performances_to_facts,
    process_table_to_facts,
    reconcile_holistic_table_duplicates,
    run_holistic_extraction,
    sanitize_catalog_samples,
    select_performance_context_chunks,
    select_sample_catalog_context_chunks,
    select_specialized_result_context,
    split_context_windows,
    table_rows_to_facts,
    is_material_sample_id,
)


def test_performance_prompt_format_escapes_json_braces():
    rendered = PERFORMANCE_PROMPT.format(sample_ids="PCF_1.0wtCNC")
    assert "PCF_1.0wtCNC" in rendered
    assert '{"performances":' in rendered


def test_sensing_and_spectroscopy_prompts_format():
    for prompt in (SENSING_SWEEP_PROMPT, SPECTROSCOPY_SWEEP_PROMPT):
        rendered = prompt.format(sample_ids="S1, S2")
        assert "S1, S2" in rendered
        assert '{"performances":' in rendered


def test_specialized_spectroscopy_context_excludes_unrelated_results():
    chunks = [
        {
            "source_type": "text",
            "section_name": "results",
            "raw_text": "ATR-FTIR spectra showed a band at 1722 cm^-1.",
            "page_number": 4,
            "order_index": 1,
            "source_block_id": "B1",
        },
        {
            "source_type": "text",
            "section_name": "results",
            "raw_text": "The neighboring paragraph assigns this carbonyl band to PCL.",
            "page_number": 4,
            "order_index": 2,
            "source_block_id": "B2",
        },
        {
            "source_type": "text",
            "section_name": "results",
            "raw_text": "Tensile strength was 12 MPa.",
            "page_number": 7,
            "order_index": 3,
            "source_block_id": "B3",
        },
    ]

    context = select_specialized_result_context(chunks, channel="spectroscopy")

    assert "1722 cm^-1" in context
    assert "assigns this carbonyl band" in context
    assert "12 MPa" not in context


def test_performances_to_facts_assigns_sample():
    facts = performances_to_facts([{
        "sample_id": "PCF_1.0wtCNC",
        "performance_metric": "tensile_strength",
        "performance_value": "147",
        "performance_unit": "MPa",
        "source_location": "p.6, Fig. 4",
    }])
    assert len(facts) == 1
    assert facts[0]["assigned_sample_id"] == "PCF_1.0wtCNC"
    assert facts[0]["extraction_method"] == "AI_holistic"


def test_performances_to_facts_preserves_mineru_source_anchor():
    facts = performances_to_facts([{
        "sample_id": "S1",
        "performance_metric": "bandgap frequency range",
        "performance_value": "1050-1400",
        "performance_unit": "Hz",
        "source_block_id": "B000072",
        "source_page": 5,
    }])

    assert facts[0]["metric_or_parameter"] == "bandgap_frequency_range"
    assert facts[0]["_source_block_id"] == "B000072"
    assert facts[0]["_source_page"] == 5


def test_performances_to_facts_resolves_contextual_pronoun_with_one_known_sample():
    facts = performances_to_facts(
        [{
            "sample_id": "this particular material",
            "performance_metric": "threshold load",
            "performance_value": "40",
            "performance_unit": "MPa",
        }],
        known_sample_ids=["UD FFRP specimen"],
    )

    assert facts[0]["assigned_sample_id"] == "UD FFRP specimen"
    assert facts[0]["metric_or_parameter"] == "inelastic_threshold_stress"


def test_merge_dedupes_by_sample_metric_value():
    atomic = [{
        "fact_type": "performance",
        "metric_or_parameter": "tensile_strength",
        "value": "147",
        "unit": "MPa",
        "assigned_sample_id": "S1",
        "extraction_method": "AI_text",
    }]
    holistic = performances_to_facts([{
        "sample_id": "S1",
        "performance_metric": "tensile_strength",
        "performance_value": "147",
        "performance_unit": "MPa",
    }])
    merged = merge_holistic_and_atomic_facts(atomic, holistic)
    perf = [f for f in merged if f.get("fact_type") == "performance"]
    assert len(perf) == 1
    assert perf[0]["extraction_method"] == "AI_holistic"


def test_merge_preserves_same_value_under_different_conditions():
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "S1",
            "metric_or_parameter": "oil_absorption_capacity",
            "value": "21.05",
            "condition": "140 °C; sample 13",
            "extraction_method": "AI_holistic_table",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "S1",
            "metric_or_parameter": "oil_absorption_capacity",
            "value": "21.05",
            "condition": "120 °C; sample 16",
            "extraction_method": "AI_holistic_table",
        },
    ]

    merged = merge_holistic_and_atomic_facts([], facts)

    assert len(merged) == 2


def test_enrich_sample_cards_fills_process_fields():
    cards = [{"sample_id": "PCF_1.0wtCNC", "process_route": ""}]
    background = {
        "process": {
            "process_route": "wet spinning-drawing-washing-heat setting",
            "spinning_method": "wet spinning",
            "process_parameters": "heat-setting=150°C",
        },
        "composition": {"matrix_name": "PVDF"},
        "structure": {"structure_methods": "FTIR, XRD"},
    }
    samples = [{"sample_id": "PCF_1.0wtCNC", "material_system": "PVDF/recycled cellulose/CNC"}]
    out = enrich_sample_cards(cards, samples, background)
    card = next(c for c in out if c["sample_id"] == "PCF_1.0wtCNC")
    assert card["spinning_method"] == "wet spinning"
    assert card["structure_methods"] == "FTIR, XRD"


def test_mixed_material_forms_do_not_share_one_global_background():
    samples = [
        {
            "sample_id": "PCL/AA",
            "fiber_type": "nanofiber",
            "composition": "PCL fiber",
        },
        {
            "sample_id": "SBCu_BG",
            "fiber_type": "bulk",
            "composition": "bioactive glass powder",
        },
    ]
    background = {
        "process": {
            "process_route": "sol-gel; electrospinning",
            "spinning_method": "electrospinning",
        },
        "structure": {"structure_features": "shared narrative"},
    }

    assert not catalog_supports_shared_background(samples)
    cards = enrich_sample_cards([], samples, background)

    assert all(not card["process_route"] for card in cards)
    assert all(not card["structure_features"] for card in cards)
    assert {card["composition_expression"] for card in cards} == {
        "PCL fiber", "bioactive glass powder",
    }


def test_split_context_windows_preserves_mineru_blocks_with_overlap():
    blocks = [
        f"[page {page} | results | text | Section 3]\n" + char * 30
        for page, char in ((1, "A"), (2, "B"), (3, "C"))
    ]
    windows = split_context_windows(
        "\n\n".join(blocks),
        max_chars=len(blocks[0]) + len(blocks[1]) + 2,
        overlap_blocks=1,
    )

    assert len(windows) == 2
    assert all(block in "\n\n".join(windows) for block in blocks)
    assert blocks[1] in windows[0]
    assert blocks[1] in windows[1]


def test_performance_context_keeps_data_from_late_pages_not_long_narrative():
    chunks = [{
        "page_number": page,
        "order_index": page,
        "section_name": "results",
        "source_type": "text",
        "raw_text": "General discussion without measured data. " * 80,
    } for page in range(2, 8)]
    chunks.append({
        "page_number": 20,
        "order_index": 20,
        "section_name": "results",
        "source_type": "text",
        "raw_text": "Sample S9 reached tensile strength of 147 MPa.",
    })

    selected = select_performance_context_chunks(chunks, max_chars=1200)

    assert any(chunk["page_number"] == 20 for chunk in selected)
    assert sum(len(chunk["raw_text"]) for chunk in selected) <= 1200


def test_performance_context_detects_mechanics_and_frequency_units():
    chunks = [
        {
            "page_number": 3,
            "order_index": 1,
            "section_name": "results",
            "source_type": "text",
            "raw_text": "Softening occurred at 430 N and displacement was 8.8 mm.",
        },
        {
            "page_number": 5,
            "order_index": 2,
            "section_name": "results",
            "source_type": "text",
            "raw_text": "The directional bandgap extended from 1050 to 1400 Hz.",
        },
    ]

    selected = select_performance_context_chunks(chunks, max_chars=2000)

    assert selected == chunks


def test_sample_catalog_context_keeps_identities_not_parameter_sweeps_or_timepoints():
    chunks = [
        {
            "page_number": 2,
            "order_index": 1,
            "section_name": "experimental",
            "source_type": "text",
            "block_type": "paragraph",
            "raw_text": "A control bioactive glass was prepared and called S BG.",
        },
        {
            "page_number": 2,
            "order_index": 2,
            "section_name": "experimental",
            "source_type": "text",
            "block_type": "paragraph",
            "raw_text": (
                "The process parameters were optimized by changing flow rates, "
                "concentrations, and needle sizes."
            ),
        },
        {
            "page_number": 3,
            "order_index": 3,
            "section_name": "experimental",
            "source_type": "text",
            "block_type": "paragraph",
            "raw_text": (
                "Samples were soaked for different time points of 1, 3, 7, "
                "and 14 days before characterization."
            ),
        },
        {
            "page_number": 4,
            "order_index": 4,
            "section_name": "experimental",
            "source_type": "text",
            "block_type": "paragraph",
            "raw_text": "Mechanical tests used PCL/AA, PCL/AA/S, and PCL/AA/SBCu mats.",
        },
    ]

    selected = select_sample_catalog_context_chunks(chunks, max_chars=5000)
    selected_text = " ".join(chunk["raw_text"] for chunk in selected)

    assert "called S BG" in selected_text
    assert "PCL/AA/SBCu" in selected_text
    assert "flow rates" not in selected_text
    assert "time points" not in selected_text


@pytest.mark.asyncio
async def test_holistic_windows_and_background_use_bounded_parallelism():
    chunks = [{
        "page_number": 1,
        "order_index": 0,
        "section_name": "experimental",
        "source_type": "text",
        "source_location": "Section 2",
        "raw_text": "Sample S1 was prepared by wet spinning. " * 20,
    }]
    chunks.extend({
        "page_number": page,
        "order_index": page,
        "section_name": "results",
        "source_type": "text",
        "source_location": f"Section 3.{page}",
        "raw_text": f"S1 result window {page}: tensile data. " * 12,
    } for page in range(2, 6))

    active = 0
    max_active = 0
    performance_calls = 0

    async def fake_llm_json(_system, _user, *, stage, **_kwargs):
        nonlocal active, max_active, performance_calls
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        if stage == "holistic_samples":
            return {"samples": [{"sample_id": "S1"}]}, ""
        if stage == "holistic_background":
            return {"process": {"spinning_method": "wet spinning"}}, ""
        if stage.startswith("holistic_performances"):
            performance_calls += 1
            return {"performances": [{
                "sample_id": "S1",
                "performance_metric": "tensile_strength",
                "performance_value": str(performance_calls),
                "performance_unit": "MPa",
                "evidence_text": f"window {performance_calls}",
            }]}, ""
        return {}, ""

    result = await run_holistic_extraction(
        chunks=chunks,
        llm_json=fake_llm_json,
        llm_timeout=5,
        performance_window_chars=520,
        performance_window_overlap_blocks=0,
        parallel_calls=2,
        sensing_enabled=False,
    )

    assert performance_calls >= 2
    assert len(result.performance_facts) == performance_calls
    assert result.background["process"]["spinning_method"] == "wet spinning"
    assert max_active == 2


@pytest.mark.asyncio
async def test_timed_out_core_window_retries_as_smaller_block_windows():
    chunks = [{
        "page_number": 1,
        "order_index": 0,
        "section_name": "experimental",
        "source_type": "text",
        "source_block_id": "B0",
        "raw_text": "Sample S1 was prepared as a composite.",
    }]
    chunks.extend({
        "page_number": page,
        "order_index": page,
        "section_name": "results",
        "source_type": "text",
        "source_block_id": f"B{page}",
        "raw_text": (
            f"S1 tensile strength was {10 + page} MPa. "
            + "Additional grounded result context. " * 34
        ),
    } for page in range(2, 6))
    performance_stages: list[str] = []
    initial_timeout_raised = False

    async def fake_llm_json(_system, _user, *, stage, **_kwargs):
        nonlocal initial_timeout_raised
        if stage == "holistic_samples":
            return {"samples": [{"sample_id": "S1"}]}, ""
        if stage == "holistic_background":
            return {"process": {}}, ""
        if stage.startswith("holistic_performances"):
            performance_stages.append(stage)
            if "_retry_" not in stage and not initial_timeout_raised:
                initial_timeout_raised = True
                raise RuntimeError(f"LLM stage '{stage}' timed out after 180s")
            return {"performances": [{
                "sample_id": "S1",
                "performance_metric": "tensile_strength",
                "performance_value": str(10 + len(performance_stages)),
                "performance_unit": "MPa",
                "evidence_text": "S1 had a grounded tensile strength result.",
            }]}, ""
        return {}, ""

    result = await run_holistic_extraction(
        chunks=chunks,
        llm_json=fake_llm_json,
        llm_timeout=200,
        performance_timeout=180,
        performance_window_chars=3000,
        performance_window_overlap_blocks=0,
        parallel_calls=3,
        sensing_enabled=False,
    )

    assert initial_timeout_raised is True
    assert any("_retry_" in stage for stage in performance_stages)
    assert not any(
        warning.startswith("performances:") for warning in result.warnings
    )
    assert len(result.performance_facts) >= 2


@pytest.mark.asyncio
async def test_failed_core_window_retry_is_reported_as_incomplete():
    chunks = [
        {
            "page_number": 1,
            "order_index": 0,
            "section_name": "experimental",
            "source_type": "text",
            "source_block_id": "B0",
            "raw_text": "Sample S1 was prepared as a composite.",
        },
        *[
            {
                "page_number": page,
                "order_index": page,
                "section_name": "results",
                "source_type": "text",
                "source_block_id": f"B{page}",
                "raw_text": (
                    f"S1 tensile strength was {10 + page} MPa. "
                    + "Additional grounded result context. " * 34
                ),
            }
            for page in range(2, 6)
        ],
    ]

    async def fake_llm_json(_system, _user, *, stage, **_kwargs):
        if stage == "holistic_samples":
            return {"samples": [{"sample_id": "S1"}]}, ""
        if stage.startswith("holistic_performances"):
            raise RuntimeError(f"LLM stage '{stage}' timed out after 180s")
        return {}, ""

    result = await run_holistic_extraction(
        chunks=chunks,
        llm_json=fake_llm_json,
        llm_timeout=200,
        performance_timeout=180,
        performance_window_chars=3000,
        performance_window_overlap_blocks=0,
        parallel_calls=3,
        sensing_enabled=False,
    )

    assert any(
        warning.startswith("performances:") for warning in result.warnings
    )


@pytest.mark.asyncio
async def test_treatment_variant_audit_completes_low_reasoning_catalog():
    chunks = [
        {
            "page_number": 1,
            "order_index": 1,
            "section_name": "experimental",
            "source_type": "text",
            "block_type": "paragraph",
            "source_block_id": "B1",
            "raw_text": "S BG and SBCu BG particles were synthesized.",
        },
        {
            "page_number": 1,
            "order_index": 2,
            "section_name": "experimental",
            "source_type": "text",
            "block_type": "paragraph",
            "source_block_id": "B2",
            "raw_text": (
                "Both S BG and SBCu BG powders were AA-treated before the "
                "treated materials were characterized."
            ),
        },
    ]
    calls = {}

    async def fake_llm_json(_system, _user, *, stage, **kwargs):
        calls[stage] = kwargs.get("reasoning_effort")
        if stage == "holistic_samples":
            return {"samples": [
                {"sample_id": "S BG"},
                {"sample_id": "SBCu BG"},
            ]}, ""
        if stage == "holistic_treatment_variants":
            return {"samples": [
                {"sample_id": "AA-treated S BG"},
                {"sample_id": "AA-treated SBCu BG"},
            ]}, ""
        return {}, ""

    result = await run_holistic_extraction(
        chunks=chunks,
        llm_json=fake_llm_json,
        llm_timeout=5,
        catalog_reasoning_effort="low",
        sensing_enabled=False,
    )

    assert {sample["sample_id"] for sample in result.samples} == {
        "S BG", "SBCu BG", "AA-treated S BG", "AA-treated SBCu BG",
    }
    assert calls["holistic_samples"] == "low"
    assert calls["holistic_treatment_variants"] == "low"


@pytest.mark.asyncio
async def test_spectroscopy_windows_fail_independently_from_core_performance():
    chunks = [{
        "page_number": 1,
        "order_index": 0,
        "section_name": "experimental",
        "source_type": "text",
        "raw_text": "S1 nanofiber was prepared by electrospinning.",
    }]
    chunks.extend([
        {
            "page_number": page,
            "order_index": page,
            "section_name": "results",
            "source_type": "text",
            "source_block_id": f"B{page}",
            "raw_text": (
                f"FTIR analysis for S1 showed a peak at {1200 + page} cm^-1. "
                + "Characterization context. " * 190
            ),
        }
        for page in (2, 3)
    ])
    spectroscopy_calls = 0

    async def fake_llm_json(_system, _user, *, stage, **_kwargs):
        nonlocal spectroscopy_calls
        if stage == "holistic_samples":
            return {"samples": [{"sample_id": "S1", "fiber_type": "nanofiber"}]}, ""
        if stage == "holistic_background":
            return {"process": {}}, ""
        if stage.startswith("holistic_spectroscopy"):
            spectroscopy_calls += 1
            if spectroscopy_calls == 1:
                raise TimeoutError("spectroscopy shard timeout")
            return {"performances": [{
                "sample_id": "S1",
                "performance_metric": "FTIR_band_1",
                "performance_value": "1203",
                "performance_unit": "cm^-1",
                "performance_method": "FTIR",
                "source_block_id": "B3",
                "source_page": 3,
                "evidence_text": "S1 showed a peak at 1203 cm^-1.",
            }]}, ""
        if stage.startswith("holistic_performances"):
            return {"performances": [{
                "sample_id": "S1",
                "performance_metric": "water_contact_angle",
                "performance_value": "80",
                "performance_unit": "°",
                "evidence_text": "S1 had a water contact angle of 80°.",
            }]}, ""
        return {}, ""

    result = await run_holistic_extraction(
        chunks=chunks,
        llm_json=fake_llm_json,
        llm_timeout=5,
        performance_window_chars=15000,
        parallel_calls=3,
        sensing_enabled=False,
    )

    assert spectroscopy_calls == 2
    assert any(warning.startswith("spectroscopy:") for warning in result.warnings)
    assert not any(warning.startswith("performances:") for warning in result.warnings)
    assert {fact["metric_or_parameter"] for fact in result.performance_facts} == {
        "water_contact_angle", "FTIR_band_1",
    }
    ftir = next(
        fact for fact in result.performance_facts
        if fact["metric_or_parameter"] == "FTIR_band_1"
    )
    assert ftir["_chunk_section"] == "results"


def test_table_rows_to_facts_requires_value_in_referenced_row():
    table = (
        "Table 1\n"
        "[columns]\tSample\tWPG (%)\tTemperature (°C)\n"
        "[row 1]\t1\t6.55\t80\n"
        "[row 2]\t2\t8.76\t100"
    )
    rows = [
        {"row": 1, "sample_id": "treated fiber / sample 1", "metric": "WPG", "value": "6.55", "unit": "%", "condition": "80 °C"},
        {"row": 2, "sample_id": "treated fiber / sample 2", "metric": "WPG", "value": "99.9", "unit": "%", "condition": "100 °C"},
        {"row": 1, "sample_id": "treated fiber / sample 1", "metric": "WPG", "value": "80", "unit": "%", "condition": ""},
    ]

    facts = table_rows_to_facts(
        rows,
        table_text=table,
        table_context="The acetylated jute samples were tested for oil absorption.",
        source_location="p.3, Table 1",
        source_block_id="B10",
        source_page=3,
    )

    assert len(facts) == 1
    assert facts[0]["value"] == "6.55"
    assert facts[0]["metric_or_parameter"] == "weight_percent_gain"
    assert facts[0]["_source_block_id"] == "B10"
    assert facts[0]["_source_table_row"] == 1
    assert "acetylated jute samples" in facts[0]["evidence_text"]
    assert "Table 1" in facts[0]["evidence_text"]
    assert "[columns]" in facts[0]["evidence_text"]


def test_table_rows_use_catalog_identity_from_sample_number_column():
    table = (
        "Table 1. Acetylation results\n"
        "[columns]\tSample no.\tWPG (%)\n"
        "[row 1]\t1\t6.55"
    )

    facts = table_rows_to_facts(
        [{
            "row": 1,
            "sample_id": "raw jute 1",
            "metric": "WPG",
            "value": "6.55",
            "unit": "%",
            "condition": "",
        }],
        table_text=table,
        source_location="p.3, Table 1",
        known_sample_ids=["raw jute", "acetylated jute 1", "acetylated jute 2"],
    )

    assert len(facts) == 1
    assert facts[0]["assigned_sample_id"] == "acetylated jute 1"


def test_deterministic_mechanical_table_preserves_composition_rows_and_symbols():
    table = """Table 1. Mechanical properties of neat and composite fibers.
[columns]\tSample\tTensile strain at break [%]\tUTS [MPa]\tE [MPa]
[row 1]\tPCL/AA\t255 ± 38\t4 ± 1\t33 ± 7
[row 2]\tPCL/AA/S\t138 ± 18\t2.4 ± 0.4\t9 ± 2
[row 3]\tPCL/AA/SBCu\t240 ± 26\t2 ± 1\t10 ± 5"""

    facts = deterministic_performance_table_facts(
        table_text=table,
        source_location="p.10, Table 1",
        known_sample_ids=["PCL/AA", "PCL/AA/S", "PCL/AA/SBCu"],
    )

    assert len(facts) == 9
    by_sample = {}
    for fact in facts:
        by_sample.setdefault(fact["assigned_sample_id"], set()).add(
            fact["metric_or_parameter"]
        )
    expected = {"elongation_at_break", "tensile_strength", "Youngs_modulus"}
    assert by_sample == {
        "PCL/AA": expected,
        "PCL/AA/S": expected,
        "PCL/AA/SBCu": expected,
    }


def test_characterization_descriptions_are_not_material_samples():
    assert not is_material_sample_id("stretching peak")
    assert not is_material_sample_id("symmetric CH2 stretching")


def test_table_specimen_rows_and_aggregate_use_known_material_identity():
    table = (
        "Table 1. Static tensile properties\n"
        "[columns]\tSpecimen #\tE1 [GPa]\n"
        "[row 1]\t1\t21.3 (1.15)\n"
        "[row 2]\tmean(dev)\t20.8 (0.95)"
    )

    facts = table_rows_to_facts(
        [
            {"row": 1, "sample_id": "specimen 1", "metric": "E1", "value": "21.3 (1.15)", "unit": "GPa"},
            {"row": 2, "sample_id": "mean(dev)", "metric": "E1", "value": "20.8 (0.95)", "unit": "GPa"},
        ],
        table_text=table,
        source_location="p.4, Table 1",
        known_sample_ids=["UD FFRP specimen"],
    )

    assert [fact["assigned_sample_id"] for fact in facts] == [
        "UD FFRP specimen 1",
        "UD FFRP specimen",
    ]
    assert all(fact["metric_or_parameter"] == "Youngs_modulus" for fact in facts)
    assert [fact["_source_table_column"] for fact in facts] == [1, 1]


def test_table_preserves_proposed_known_base_when_catalog_has_constituents():
    table = (
        "Table 1. Laminate tensile results\n"
        "[columns]\tSpecimen #\tE1 [GPa]\n"
        "[row 1]\t1\t20.7\n"
        "[row 2]\tmean(dev)\t21.3 (1.15)"
    )

    facts = table_rows_to_facts(
        [
            {"row": 1, "sample_id": "UD_flax_bioepoxy_laminate specimen 1", "metric": "E1", "value": "20.7", "unit": "GPa"},
            {"row": 2, "sample_id": "UD_flax_bioepoxy_laminate", "metric": "E1", "value": "21.3 (1.15)", "unit": "GPa"},
        ],
        table_text=table,
        source_location="p.3, Table 1",
        known_sample_ids=["Amplitex_UD_flax_fiber", "UD_flax_bioepoxy_laminate"],
    )

    assert [fact["assigned_sample_id"] for fact in facts] == [
        "UD_flax_bioepoxy_laminate specimen 1",
        "UD_flax_bioepoxy_laminate",
    ]


def test_table_rows_to_facts_accepts_matching_unknown_result_column():
    table = (
        "Table 2. Reusability of sorbents (acetylated jute)\n"
        "[columns]\t\tOil sorbed (g/g)\tOil remaining in fiber (g/g)\n"
        "[row 1]\tFirst cycle\t21.08\t2.940"
    )

    facts = table_rows_to_facts(
        [
            {
                "row": 1,
                "sample_id": "sample 1",
                "metric": "oil sorbed",
                "value": "21.08",
                "unit": "g/g",
                "condition": "first cycle",
            },
            {
                "row": 1,
                "sample_id": "sample 1",
                "metric": "oil remaining in fiber",
                "value": "2.940",
                "unit": "g/g",
                "condition": "first cycle",
            },
        ],
        table_text=table,
        source_location="p.5, Table 2",
    )

    assert len(facts) == 2
    assert {fact["value"] for fact in facts} == {"21.08", "2.940"}
    assert {fact["assigned_sample_id"] for fact in facts} == {"acetylated jute"}


ELECTROSPINNING_PROCESS_TABLE = (
    "Table 1. Electrospinning parameters for single and multiple needles. "
    "For all cases, distance from needle to collector=10 cm and the solution "
    "concentration is 8 wt%\n"
    "[columns]\tNo. of needles\t1\t17\t72\n"
    "[row 1]\tDistance between needles (mm)\t�\t10\t5\n"
    "[row 2]\tVoltage (kV)\t8\t20\t25\n"
    "[row 3]\tElectric field strength (kV/cm)\t0.8\t2.0\t2.5\n"
    "[row 4]\tTotal flowrate (ml/hr)\t0.6\t5\t9\n"
    "[row 5]\tFlowrate per needle (ml/hr)\t0.6\t0.294 (≈0.3)\t0.125 (≈0.13)"
)


def _electrospinning_catalog() -> list[dict]:
    return [
        {
            "sample_id": "PAN_nanofiber_single_0.2mm",
            "material_system": "PAN",
            "fiber_type": "nanofiber",
            "composition": "PAN nanofibers using a single needle with 0.2 mm ID.",
        },
        {
            "sample_id": "PAN_nanofiber_single_0.4mm",
            "material_system": "PAN",
            "fiber_type": "nanofiber",
            "composition": "PAN nanofibers using a single needle with 0.4 mm ID.",
        },
        {
            "sample_id": "box 1",
            "material_system": "PAN",
            "fiber_type": "nanofiber",
            "composition": "PAN nanofibers using box 1 containing 17 needles.",
        },
        {
            "sample_id": "box 2",
            "material_system": "PAN",
            "fiber_type": "nanofiber",
            "composition": "PAN nanofibers using box 2 containing 72 needles.",
        },
        {
            "sample_id": "PtPd_coated_PAN_nanofiber_SEM",
            "material_system": "PAN/Pt/Pd",
            "fiber_type": "nanofiber",
            "composition": "PAN nanofiber SEM specimens sputter coated with Pt/Pd.",
        },
        {
            "sample_id": "PtPd_coated_PP_orifice_reservoir",
            "material_system": "PP/Pt/Pd",
            "fiber_type": "bulk",
            "composition": "Polypropylene reservoir box used as apparatus.",
        },
    ]


def test_process_table_is_deterministic_and_does_not_emit_false_performance_metrics():
    assert classify_table_role(ELECTROSPINNING_PROCESS_TABLE) == "process"
    cleaned = sanitize_catalog_samples(_electrospinning_catalog())
    cleaned_ids = {sample["sample_id"] for sample in cleaned}
    assert "PtPd_coated_PAN_nanofiber_SEM" not in cleaned_ids
    assert "PtPd_coated_PP_orifice_reservoir" not in cleaned_ids
    assert {"PAN_nanofiber_17_needles", "PAN_nanofiber_72_needles"} <= cleaned_ids

    samples = augment_catalog_samples_from_process_tables(
        [{"source_type": "table_text", "raw_text": ELECTROSPINNING_PROCESS_TABLE}],
        cleaned,
    )
    assert any(sample["sample_id"] == "PAN_nanofiber_single_needle" for sample in samples)
    facts = process_table_to_facts(
        table_text=ELECTROSPINNING_PROCESS_TABLE,
        known_samples=samples,
        source_location="p.9, Table 1",
        source_block_id="B000096",
        source_page=9,
    )

    assert len(facts) == 23
    assert all(fact["assigned_sample_id"] for fact in facts)
    assert not {"surface_roughness", "breakdown_strength"} & {
        fact["metric_or_parameter"] for fact in facts
    }
    by_key = {
        (fact["assigned_sample_id"], fact["metric_or_parameter"]): fact
        for fact in facts
    }
    assert by_key[("PAN_nanofiber_17_needles", "flow_rate_per_needle")]["value"] == "0.294"
    assert by_key[("PAN_nanofiber_72_needles", "voltage")]["value"] == "25"
    assert by_key[("PAN_nanofiber_single_needle", "polymer_concentration")]["unit"] == "wt%"


def test_catalog_does_not_expand_continuous_range_into_integer_samples():
    samples = [
        {
            "sample_id": f"TPU_T300CF_{value}vol%",
            "material_system": "TPU/T300 carbon fiber",
            "variable_name": "fiber volume fraction",
            "variable_value": str(value),
            "variable_unit": "%",
        }
        for value in range(1, 21)
    ]
    source = (
        "The 20 points represent variation in fiber volume fraction from 1% to 20%. "
        "Separate RVE models were shown at 5% and 15% fiber contents."
    )

    cleaned = sanitize_catalog_samples(samples, source_text=source)

    assert {sample["variable_value"] for sample in cleaned} == {"5", "15"}


def test_process_table_splits_variant_hidden_in_conflicting_alias():
    merged = [{
        "sample_id": "PAN_nanofiber_17_needles_10mm_spacing",
        "aliases": [
            "PAN_nanofiber_72_needles_5mm_spacing",
            "box 1 PAN nanofibers",
            "box 2 PAN nanofibers",
        ],
        "material_system": "PAN",
        "fiber_type": "nanofiber",
        "variable_name": "number of needles",
        "variable_value": "17",
    }]

    cleaned = sanitize_catalog_samples(merged)
    assert "PAN_nanofiber_72_needles_5mm_spacing" not in cleaned[0]["aliases"]

    samples = augment_catalog_samples_from_process_tables(
        [{"source_type": "table_text", "raw_text": ELECTROSPINNING_PROCESS_TABLE}],
        cleaned,
    )
    facts = process_table_to_facts(
        table_text=ELECTROSPINNING_PROCESS_TABLE,
        known_samples=samples,
        source_location="p.9, Table 1",
    )
    sample_for_count = {
        fact["value"]: fact["assigned_sample_id"]
        for fact in facts
        if fact["metric_or_parameter"] == "number_of_needles"
    }
    assert sample_for_count["17"] == "PAN_nanofiber_17_needles_10mm_spacing"
    assert sample_for_count["72"] == "PAN_nanofiber_72_needles"


def test_prefixed_setup_box_ids_are_renamed_to_explicit_needle_variants():
    samples = [
        {
            "sample_id": "PAN_nanofiber_multiple_needle_box1",
            "aliases": ["PAN_nanofiber_multiple_needle_box2", "box 1", "box 2"],
            "material_system": "PAN",
            "fiber_type": "nanofiber",
            "variable_name": "number_of_needles",
            "variable_value": "17",
            "variable_unit": "count",
        },
        {
            "sample_id": "PAN_nanofiber_multiple_needle_box2",
            "aliases": ["PAN_nanofiber_multiple_needle_box1", "box 2"],
            "material_system": "PAN",
            "fiber_type": "nanofiber",
            "variable_name": "number_of_needles",
            "variable_value": "72",
            "variable_unit": "count",
        },
    ]

    cleaned = sanitize_catalog_samples(samples)

    assert {sample["sample_id"] for sample in cleaned} == {
        "PAN_nanofiber_17_needles",
        "PAN_nanofiber_72_needles",
    }


@pytest.mark.asyncio
async def test_process_table_is_covered_without_table_llm_calls():
    calls: list[str] = []
    chunks = [
        {
            "page_number": 3,
            "order_index": 0,
            "section_name": "experimental",
            "source_type": "text",
            "raw_text": "PAN nanofibers were produced by single and multiple needle electrospinning.",
        },
        {
            "page_number": 9,
            "order_index": 1,
            "section_name": "results",
            "source_type": "table_text",
            "source_block_id": "B000096",
            "raw_text": ELECTROSPINNING_PROCESS_TABLE,
        },
    ]

    async def fake_llm_json(_system, _user, *, stage, **_kwargs):
        calls.append(stage)
        if stage == "holistic_samples":
            return {"samples": _electrospinning_catalog()}, ""
        return {}, ""

    result = await run_holistic_extraction(
        chunks=chunks,
        llm_json=fake_llm_json,
        llm_timeout=5,
        sensing_enabled=False,
    )

    assert result.covered_table_block_ids == ["B000096"]
    assert len([fact for fact in result.performance_facts if fact["fact_type"] == "process"]) == 23
    assert not any(stage.startswith("holistic_table") for stage in calls)


def test_reconcile_drops_unique_narrative_restatement_of_table_row():
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "acetylated jute",
            "metric_or_parameter": "oil_absorption_capacity",
            "value": "21.08",
            "condition": "1 h; 120 °C; 2% catalyst",
            "extraction_method": "AI_holistic",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "sample 12",
            "metric_or_parameter": "oil_absorption_capacity",
            "value": "21.08",
            "condition": "Time=1 h; Temp=120 °C; Catalyst=2.0%; ratio=1:20",
            "evidence_text": "Acetylated jute results. [row 12] 1 120 2.0 21.08",
            "extraction_method": "AI_holistic_table",
        },
    ]

    reconciled = reconcile_holistic_table_duplicates(facts)

    assert len(reconciled) == 1
    assert reconciled[0]["assigned_sample_id"] == "sample 12"


def test_reconcile_drops_exact_sample_duplicate_with_extra_reagent_amount():
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "acetylated_jute_S12",
            "metric_or_parameter": "weight_percent_gain",
            "value": "17.01",
            "condition": "2% catalyst; 120 °C; 1 h; 2 g reagent in 100 ml",
            "extraction_method": "AI_holistic",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "acetylated_jute_S12",
            "metric_or_parameter": "weight_percent_gain",
            "value": "17.01",
            "condition": "Sample 12; 1 h; 120 °C; catalyst 2%",
            "evidence_text": "Acetylated jute [row 12] 12 1 120 2.0 17.01",
            "extraction_method": "AI_holistic_table",
        },
    ]

    reconciled = reconcile_holistic_table_duplicates(facts)

    assert len(reconciled) == 1
    assert reconciled[0]["extraction_method"] == "AI_holistic_table"


def test_reconcile_keeps_same_table_value_under_multiple_conditions():
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "S1",
            "metric_or_parameter": "oil_absorption_capacity",
            "value": "21.05",
            "condition": "140 °C",
            "extraction_method": "AI_holistic_table",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "S1",
            "metric_or_parameter": "oil_absorption_capacity",
            "value": "21.05",
            "condition": "120 °C",
            "extraction_method": "AI_holistic_table",
        },
    ]

    assert len(reconcile_holistic_table_duplicates(facts)) == 2


def test_reconcile_drops_narrative_restatement_of_deterministic_process_table():
    facts = [
        {
            "fact_type": "process",
            "assigned_sample_id": "PAN_nanofiber_72_needles",
            "metric_or_parameter": "total_flow_rate",
            "value": "9",
            "unit": "mL/h",
            "condition": "number_of_needles=72",
            "extraction_method": "rule_table_process",
        },
        {
            "fact_type": "process",
            "assigned_sample_id": "PAN_nanofiber_72_needles",
            "metric_or_parameter": "total_flow_rate",
            "value": "9",
            "unit": "mL/hr",
            "condition": "72-needle electrospinning setup",
            "extraction_method": "AI_holistic",
        },
    ]

    reconciled = reconcile_holistic_table_duplicates(facts)

    assert len(reconciled) == 1
    assert reconciled[0]["extraction_method"] == "rule_table_process"


@pytest.mark.asyncio
async def test_holistic_table_sweep_marks_structured_table_covered():
    chunks = [
        {
            "page_number": 2,
            "order_index": 0,
            "section_name": "experimental",
            "source_type": "text",
            "source_location": "Section 2",
            "raw_text": "Raw fiber was acetylated to prepare treated fiber. " * 20,
        },
        {
            "page_number": 3,
            "order_index": 1,
            "section_name": "results",
            "source_type": "table_text",
            "source_location": "Table 1",
            "source_block_id": "B10",
            "raw_text": (
                "Table 1. Results\n"
                "[columns]\tSample\tWPG (%)\n"
                "[row 1]\t1\t6.55"
            ),
        },
    ]

    async def fake_llm_json(_system, _user, *, stage, **_kwargs):
        if stage == "holistic_samples":
            return {"samples": [{"sample_id": "treated fiber"}]}, ""
        if stage == "holistic_background":
            return {"process": {}}, ""
        if stage.startswith("holistic_table"):
            return {"rows": [{
                "row": 1,
                "sample_id": "treated fiber / sample 1",
                "metric": "WPG",
                "value": "6.55",
                "unit": "%",
                "condition": "",
            }]}, ""
        return {"performances": []}, ""

    result = await run_holistic_extraction(
        chunks=chunks,
        llm_json=fake_llm_json,
        llm_timeout=5,
        sensing_enabled=False,
    )

    assert result.covered_table_block_ids == ["B10"]
    assert any(fact["value"] == "6.55" for fact in result.performance_facts)


@pytest.mark.asyncio
async def test_holistic_table_fast_path_covers_rows_without_partial_llm_output():
    chunks = [{
        "page_number": 3,
        "order_index": 1,
        "section_name": "results",
        "source_type": "table_text",
        "source_location": "Table 1",
        "source_block_id": "B10",
        "raw_text": (
            "Table 1. Results\n"
            "[columns]\tSample\tWPG (%)\n"
            "[row 1]\t1\t6.55\n"
            "[row 2]\t2\t8.76"
        ),
    }]

    calls: list[str] = []

    async def fake_llm_json(_system, _user, *, stage, **_kwargs):
        calls.append(stage)
        if stage == "holistic_samples":
            return {"samples": [{"sample_id": "treated fiber"}]}, ""
        if stage.startswith("holistic_table"):
            return {"rows": [{
                "row": 1,
                "sample_id": "treated fiber / sample 1",
                "metric": "WPG",
                "value": "6.55",
                "unit": "%",
                "condition": "",
            }]}, ""
        return {}, ""

    result = await run_holistic_extraction(
        chunks=chunks,
        llm_json=fake_llm_json,
        llm_timeout=5,
        sensing_enabled=False,
    )

    assert result.covered_table_block_ids == ["B10"]
    assert {fact["value"] for fact in result.performance_facts} == {"6.55", "8.76"}
    assert not any(stage.startswith("holistic_table") for stage in calls)


@pytest.mark.asyncio
async def test_holistic_known_metric_table_avoids_llm_and_repair_calls():
    chunks = [{
        "page_number": 4,
        "order_index": 1,
        "section_name": "results",
        "source_type": "table_text",
        "source_location": "Table 1",
        "source_block_id": "B20",
        "raw_text": (
            "Table 1. Static tensile properties\n"
            "[columns]\tSpecimen #\tE1 [GPa]\tsigma_R (sigma_u) [MPa]\t"
            "epsilon_R (epsilon_u) [%]\n"
            "[row 1]\t1\t21.3\t246.0\t1.42\n"
            "[row 2]\t2\t20.8\t238.5\t1.36"
        ),
    }]
    calls: list[str] = []

    async def fake_llm_json(_system, _user, *, stage, **_kwargs):
        calls.append(stage)
        if stage == "holistic_samples":
            return {"samples": [{"sample_id": "UD FFRP"}]}, ""
        if stage.startswith("holistic_table_repair"):
            assert 'column "epsilon_R (epsilon_u) [%]"' in _system
            return {"rows": [
                {"row": 1, "sample_id": "specimen 1", "metric": "epsilon_R", "value": "1.42", "unit": "%"},
                {"row": 2, "sample_id": "specimen 2", "metric": "epsilon_R", "value": "1.36", "unit": "%"},
            ]}, ""
        if stage.startswith("holistic_table"):
            return {"rows": [
                {"row": 1, "sample_id": "specimen 1", "metric": "E1", "value": "21.3", "unit": "GPa"},
                {"row": 1, "sample_id": "specimen 1", "metric": "sigma_R", "value": "246.0", "unit": "MPa"},
                {"row": 2, "sample_id": "specimen 2", "metric": "E1", "value": "20.8", "unit": "GPa"},
                {"row": 2, "sample_id": "specimen 2", "metric": "sigma_R", "value": "238.5", "unit": "MPa"},
            ]}, ""
        return {}, ""

    result = await run_holistic_extraction(
        chunks=chunks,
        llm_json=fake_llm_json,
        llm_timeout=5,
        sensing_enabled=False,
    )

    assert result.covered_table_block_ids == ["B20"]
    assert len(result.performance_facts) == 6
    assert {fact["metric_or_parameter"] for fact in result.performance_facts} == {
        "Youngs_modulus", "tensile_strength", "elongation_at_break",
    }
    assert not any(stage.startswith("holistic_table") for stage in calls)
    assert all(
        fact["extraction_method"] == "rule_table_performance"
        for fact in result.performance_facts
    )


def test_deterministic_table_preserves_mean_and_standard_deviation_cells():
    table_text = (
        "Table 1. Static tensile properties at room temperature\n"
        "[columns]\tSpecimen #\t$E_{1}$ [GPa]\t"
        "$\\sigma_{R} ($\\sigma_{u})$ [MPa]\t"
        "$\\varepsilon_{R} ($\\varepsilon_{u})$ [%]\n"
        "[row 1]\t1\t20.7\t264.3\t1.87\n"
        "[row 2]\tmean(dev)\t21.3 (1.15)\t221(18)\t1.61 (0.15)"
    )

    facts = deterministic_performance_table_facts(
        table_text=table_text,
        source_location="p.3, Table 1",
        source_block_id="B000050",
        source_page=3,
        known_sample_ids=[
            "UD_flax_bioepoxy_laminate",
            "UD_flax_bioepoxy_laminate specimen 1",
        ],
    )

    assert len(facts) == 6
    assert {fact["metric_or_parameter"] for fact in facts} == {
        "Youngs_modulus", "tensile_strength", "elongation_at_break",
    }
    means = [
        fact for fact in facts
        if fact["assigned_sample_id"] == "UD_flax_bioepoxy_laminate"
    ]
    assert {fact["value"] for fact in means} == {
        "21.3 (1.15)", "221(18)", "1.61 (0.15)",
    }
