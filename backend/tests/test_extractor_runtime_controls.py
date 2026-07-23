import json

import pytest

from app.core.config import settings
from app.services.extractor_v7.service import V7ExtractorService


def _chunk(source_type: str, text: str, section: str = "results") -> dict:
    return {
        "source_type": source_type,
        "raw_text": text,
        "section_name": section,
        "page_number": 1,
    }


def test_paper_metadata_scans_late_first_page_publisher_blocks():
    raw_text = "\n".join([
        "# Fiber-Reinforced Mechanical Metamaterials",
        "Abstract text " + ("x" * 6000),
    ])
    chunks = [
        {
            "page_number": 1,
            "source_type": "aside_text",
            "raw_text": "© 2024 Wiley-VCH GmbH",
        },
        {
            "page_number": 1,
            "source_type": "aside_text",
            "raw_text": "DOI: 10.1002/adem.202400017",
        },
        {
            "page_number": 1,
            "source_type": "header_footer",
            "raw_text": "Adv. Eng. Mater. 2024, 26, 2400017",
        },
    ]

    metadata = V7ExtractorService._fill_paper_metadata_fallback(
        {}, raw_text, "fiber__10.1002_adem.202400017.pdf", chunks
    )

    assert metadata == {
        "paper_title": "Fiber-Reinforced Mechanical Metamaterials",
        "doi_or_url": "10.1002/adem.202400017",
        "year": "2024",
        "journal": "Adv. Eng. Mater.",
    }


def test_paper_metadata_ignores_leading_mineru_image_markdown():
    title = (
        "Development of an adaptive morphing wing based on "
        "fiber-reinforced plastics and shape memory alloys"
    )
    chunks = [
        {
            "page_number": 1,
            "source_type": "figure",
            "section_name": "title_abstract",
            "raw_text": "![](images/cover.jpg)",
        },
        {
            "page_number": 1,
            "source_type": "paragraph",
            "section_name": "title_abstract",
            "raw_text": title,
        },
    ]

    metadata = V7ExtractorService._fill_paper_metadata_fallback(
        {"paper_title": "![](images/cover.jpg)"},
        f"![](images/cover.jpg)\n{title}\nAbstract",
        "fiber__10.1177_1528083718823295.pdf",
        chunks,
    )

    assert metadata["paper_title"] == title


def test_redundant_intrinsic_qa_restatement_is_dropped():
    base = {
        "sample_id": "TPMS_10vol%",
        "canonical_metric": "Youngs_modulus",
        "clean_value": "26",
        "clean_unit": "MPa",
    }
    clean = {**base, "export_target": "Core_Final_Records", "qa_reason": ""}
    restatement = {
        **base,
        "export_target": "Result_Facts_QA",
        "qa_reason": "export_tier_B_review;checklist_failed",
    }
    distinct = {
        **base,
        "canonical_metric": "tensile_strength",
        "export_target": "Result_Facts_QA",
        "qa_reason": "export_tier_B_review;checklist_failed",
    }

    out = V7ExtractorService._drop_redundant_intrinsic_qa_results(
        [clean, restatement, distinct]
    )

    assert out == [clean, distinct]


def test_repeated_intrinsic_qa_restatements_keep_one_evidence_row():
    base = {
        "sample_id": "TPMS_10vol%",
        "canonical_metric": "density",
        "clean_value": "1257",
        "clean_unit": "kg m^-3",
        "export_target": "Result_Facts_QA",
        "qa_reason": "export_tier_B_review;checklist_failed",
    }
    first = {**base, "source_location": "page 3"}
    restatement = {**base, "source_location": "page 5"}
    distinct_value = {**base, "clean_value": "1260", "source_location": "page 6"}
    substantive_qa = {
        **base,
        "source_location": "page 7",
        "qa_reason": "evidence_value_mismatch",
    }
    non_intrinsic = {
        **base,
        "canonical_metric": "tensile_strength",
        "source_location": "page 8",
    }
    non_intrinsic_restatement = {**non_intrinsic, "source_location": "page 9"}

    out = V7ExtractorService._drop_redundant_intrinsic_qa_results([
        first,
        restatement,
        distinct_value,
        substantive_qa,
        non_intrinsic,
        non_intrinsic_restatement,
    ])

    assert out == [
        first,
        distinct_value,
        substantive_qa,
        non_intrinsic,
        non_intrinsic_restatement,
    ]


def test_result_restatements_collapse_subset_conditions_but_keep_distinct_tests():
    base = {
        "sample_id": "adaptive_morphing_wing",
        "canonical_metric": "maximum_deformation",
        "clean_value": "2.8",
        "clean_unit": "mm",
        "export_target": "Result_Facts_QA",
        "metric_priority": "Secondary",
        "performance_method": "cyclic deformation test",
        "qa_reason": "checklist_failed",
        "ai_confidence": 0.88,
    }
    detailed = {
        **base,
        "source_location": "page 11",
        "performance_condition": (
            "at 0.8 A during a 0.5 A to 1.2 A cycle for 60 s; checklist_failed"
        ),
    }
    conclusion_restatement = {
        **base,
        "source_location": "page 13",
        "performance_condition": "cyclic activation for 60 s; checklist_failed",
    }
    distinct_temperature = {
        **base,
        "source_location": "page 14",
        "performance_condition": "tested at 80 C; checklist_failed",
    }

    out = V7ExtractorService._dedupe_result_restatements([
        detailed,
        conclusion_restatement,
        distinct_temperature,
    ])

    assert out == [detailed, distinct_temperature]


def test_result_restatements_merge_mode_paraphrases_but_keep_distinct_modes():
    base = {
        "sample_id": "CF/EP",
        "canonical_metric": "impact_strength",
        "clean_value": "1097",
        "clean_unit": "J/m^2",
        "export_target": "Core_Final_Records",
        "metric_priority": "Core",
        "performance_method": "",
        "qa_reason": "",
        "ai_confidence": 0.9,
    }
    mode_ii_curve = {
        **base,
        "source_location": "page 6",
        "performance_condition": "mode II propagation R-curve",
    }
    mode_ii_fracture = {
        **base,
        "source_location": "page 8",
        "performance_condition": "mode II fracture test",
    }
    mode_i = {
        **base,
        "source_location": "page 9",
        "performance_condition": "mode I fracture test",
    }

    out = V7ExtractorService._dedupe_result_restatements([
        mode_ii_curve,
        mode_ii_fracture,
        mode_i,
    ])

    assert out == [mode_ii_curve, mode_i]


def test_result_restatements_keep_distinct_surface_treatments():
    base = {
        "sample_id": "jute/epoxy",
        "canonical_metric": "tensile_strength",
        "clean_value": "120",
        "clean_unit": "MPa",
        "export_target": "Core_Final_Records",
        "metric_priority": "Core",
        "performance_method": "tensile test",
        "qa_reason": "",
        "ai_confidence": 0.9,
    }
    acid = {
        **base,
        "source_location": "page 6",
        "performance_condition": "acid-treated fibers",
    }
    alkali = {
        **base,
        "source_location": "page 7",
        "performance_condition": "alkali-treated fibers",
    }

    out = V7ExtractorService._dedupe_result_restatements([acid, alkali])

    assert out == [acid, alkali]


def test_repeated_fact_context_recovers_unique_fiber_volume_fraction():
    cards = [{
        "sample_id": "TPU/T300_CF_P-type_TPMS",
        "variable_name": "",
        "variable_value": "",
        "variable_unit": "",
    }]
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPU/T300_CF_P-type_TPMS",
            "condition": "fiber reinforcement volume fraction parameter 10%",
            "evidence_text": "",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPU/T300_CF_P-type_TPMS",
            "condition": "fiber reinforcement volume ratio of 10%",
            "evidence_text": "",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPU/T300_CF_P-type_TPMS",
            "condition": "",
            "evidence_text": "Supporting models used fiber contents of 4% and 8%.",
        },
    ]

    out = V7ExtractorService._enrich_sample_cards_from_repeated_fact_variants(
        cards, facts
    )

    assert out[0]["variable_name"] == "fiber volume fraction"
    assert out[0]["variable_value"] == "10"
    assert out[0]["variable_unit"] == "vol%"


def test_weak_stage2_batches_text_chunks_and_keeps_tables_standalone(monkeypatch):
    monkeypatch.setattr(settings, "WEAK_STAGE2_BATCH_SIZE", 3)
    monkeypatch.setattr(settings, "WEAK_STAGE2_BATCH_MAX_CHARS", 9000)

    chunks = [
        _chunk("text", "PVDF-1 tensile strength 10 MPa"),
        _chunk("figure_caption", "Fig. 1 PVDF-2 modulus 20 MPa"),
        _chunk("table_text", "sample,value\nPVDF-3,30 MPa"),
        _chunk("text", "PVDF-4 conductivity 1 S/cm"),
        _chunk("text", "PVDF-5 diameter 500 nm"),
    ]

    units = V7ExtractorService._stage2_execution_units(chunks, "weak")

    assert [len(unit) for unit in units] == [2, 1, 2]
    assert units[1][0]["source_type"] == "table_text"


def test_chunk_source_location_extracts_fig_and_table_labels():
    assert (
        V7ExtractorService._chunk_source_location(_chunk("figure_caption", "Fig. 1 PVDF-2 modulus 20 MPa"))
        == "p.1, Fig. 1"
    )
    assert (
        V7ExtractorService._chunk_source_location(_chunk("table_text", "Table 2a sample,value\nPVDF-3,30 MPa"))
        == "p.1, Table 2a"
    )


def test_fact_candidates_backfill_sample_mentions_filters_conditions():
    facts = [
        {
            "candidate_sample_ids": [
                "PVDF-1.0wtCNC",
                "200 °C",
                "50% strain",
                "fiber",
            ],
            "source_location": "p.3, results section",
            "evidence_text": "PVDF-1.0wtCNC reached 12 MPa at 50% strain.",
        }
    ]

    mentions = V7ExtractorService._sample_mentions_from_fact_candidates(facts)

    sample_ids = {m["normalized_sample_id"] for m in mentions}
    assert "PVDF-1.0wtCNC" in sample_ids
    assert "200 °C" not in sample_ids
    assert "50% strain" not in sample_ids
    assert "fiber" not in sample_ids


def test_fact_candidates_reject_aggregate_and_fraction_phrases_as_samples():
    facts = [{
        "candidate_sample_ids": [
            "UD FFRP",
            "fibers volume fraction",
            "Unidirectional FFRP mean(dev)",
            "this particular material",
            "acetylated jute obtained with NBS as a catalyst using its various concentrations",
            "WPG. The oil absorption capacity of raw jute fiber",
            "modified jute samples",
        ],
        "source_location": "p.4, Table 1",
        "evidence_text": "UD FFRP specimens were tested.",
    }]

    mentions = V7ExtractorService._sample_mentions_from_fact_candidates(facts)

    assert {mention["normalized_sample_id"] for mention in mentions} == {"UD FFRP"}


def test_characterization_peak_subjects_do_not_create_sample_mentions():
    facts = [{
        "fact_type": "performance",
        "candidate_sample_ids": ["symmetric CH2 stretching", "PCL/AA"],
        "assigned_sample_id": "stretching peak",
        "metric_or_parameter": "FTIR_band_1",
        "value": "1722",
        "unit": "cm^-1",
        "method": "FTIR",
        "source_location": "p.7 block B116",
        "evidence_text": "PCL/AA showed a carbonyl stretching peak at 1722 cm^-1.",
    }]

    assert V7ExtractorService._sample_mentions_from_fact_candidates(facts) == []


def test_mean_standard_deviation_value_is_preserved_as_condition():
    cleaned = V7ExtractorService._clean_value_variants("21.3 (1.15)", "GPa")

    assert cleaned == [{
        "raw_value": "21.3 (1.15)",
        "value_operator": "=",
        "clean_value": "21.3",
        "clean_unit": "GPa",
        "standard_deviation": "1.15",
    }]

    rows = V7ExtractorService._build_result_facts(
        [{
            "fact_id": "H1",
            "fact_type": "performance",
            "assigned_sample_id": "UD FFRP",
            "assignment_status": "assigned",
            "metric_or_parameter": "Youngs_modulus",
            "value": "21.3 (1.15)",
            "unit": "GPa",
            "evidence_text": "UD FFRP modulus was 21.3 (1.15) GPa.",
            "source_location": "p.4, Table 1",
            "extraction_method": "AI_holistic_table",
        }],
        [{"sample_id": "UD FFRP"}],
    )

    assert rows[0]["clean_value"] == "21.3"
    assert "standard_deviation=1.15" in rows[0]["performance_condition"]


def test_table_header_brackets_are_removed_from_export_unit():
    cleaned = V7ExtractorService._clean_value_variants("20.7", "[GPa]")

    assert cleaned[0]["clean_unit"] == "GPa"


def test_strain_percent_unit_is_normalized_for_export():
    cleaned = V7ExtractorService._clean_value_variants("0.2", "% strain")

    assert cleaned[0]["clean_unit"] == "%"


def test_checklist_failure_is_never_routed_to_core_records():
    rows = V7ExtractorService._build_result_facts(
        [{
            "fact_id": "H1",
            "fact_type": "performance",
            "assigned_sample_id": "PAN_nanofiber_multiple_needle",
            "assignment_status": "assigned",
            "metric_or_parameter": "fiber_diameter",
            "value": "<100",
            "unit": "nm",
            "evidence_text": "Nanofibers with less than 100 nm diameters were produced.",
            "source_location": "conclusion",
            "extraction_method": "AI_holistic",
            "_export_tier": "B",
            "_checklist_failed": True,
            "_checklist_failures": ["sample_id_not_found_in_evidence"],
        }],
        [{"sample_id": "PAN_nanofiber_multiple_needle"}],
    )

    assert rows[0]["export_target"] == "Result_Facts_QA"
    assert "checklist_failed" in rows[0]["qa_reason"]
    assert "checklist:sample_id_not_found_in_evidence" in rows[0]["qa_reason"]


def test_vision_fact_rejects_background_and_accepts_grounded_core_value():
    valid = {
        "sample_id": "UD FFRP",
        "metric_or_parameter": "tensile_strength",
        "value": "246",
        "source_location": "p.4, Fig. 3",
        "evidence_text": "UD FFRP tensile strength 246 MPa",
    }
    background = {
        **valid,
        "evidence_text": "Previous work reported UD FFRP tensile strength 246 MPa [12].",
    }

    assert V7ExtractorService._is_allowed_vision_fact(valid)
    assert not V7ExtractorService._is_allowed_vision_fact(background)


def test_stage2_signal_score_prioritizes_data_rich_chunks():
    table = _chunk("table_text", "Sample PVDF-1 tensile strength 12 MPa")
    plain = _chunk("text", "This section discusses the general motivation.", "conclusion")

    assert V7ExtractorService._stage2_signal_score(table) > V7ExtractorService._stage2_signal_score(plain)


def test_stage2_skips_tables_already_grounded_by_holistic_sweep():
    chunks = [
        {
            **_chunk("table_text", "[columns]\tSample\tStrength\n[row 1]\tS1\t12 MPa"),
            "source_block_id": "B1",
        },
        _chunk("figure_caption", "Fig. 2. S1 reached 12 MPa."),
    ]

    selected = V7ExtractorService._select_stage2_chunks(
        chunks,
        "strong",
        holistic_fact_count=20,
        holistic_covered_table_ids={"B1"},
    )

    assert all(chunk["source_type"] != "table_text" for chunk in selected)
    assert any(chunk["source_type"] == "figure_caption" for chunk in selected)


def test_stage2_keeps_only_uncovered_tables_for_fallback():
    chunks = [
        {**_chunk("table_text", "Table one"), "source_block_id": "B1"},
        {**_chunk("text", "separator"), "source_block_id": "T1"},
        {**_chunk("table_text", "Table two"), "source_block_id": "B2"},
    ]

    selected = V7ExtractorService._select_stage2_chunks(
        chunks,
        "strong",
        holistic_covered_table_ids={"B1"},
    )

    selected_ids = {chunk.get("source_block_id") for chunk in selected}
    assert "B1" not in selected_ids
    assert "B2" in selected_ids


def test_complete_low_yield_holistic_sweep_repairs_quantitative_text_and_table():
    chunks = [
        {**_chunk("table_text", "Table one"), "source_block_id": "B1"},
        {**_chunk("table_text", "Table two"), "source_block_id": "B2"},
        {
            **_chunk("figure_caption", "Fig. 3. Strength reached 20 MPa."),
            "source_block_id": "B3",
        },
        {
            **_chunk("text", "The modulus was 12 GPa.", "results"),
            "source_block_id": "B4",
        },
        {
            **_chunk("text", "General qualitative discussion without values.", "results"),
            "source_block_id": "B5",
        },
    ]

    selected = V7ExtractorService._select_stage2_chunks(
        chunks,
        "strong",
        holistic_fact_count=2,
        holistic_covered_table_ids={"B1"},
        holistic_performance_complete=True,
    )

    assert [chunk.get("source_block_id") for chunk in selected] == ["B2", "B3", "B4"]


def test_partial_low_yield_holistic_sweep_stays_on_targeted_repair():
    chunks = [
        {
            **_chunk("text", "The softening load was 430 N.", "results"),
            "source_block_id": "B1",
        },
        {
            **_chunk("text", "General discussion without numerical results.", "results"),
            "source_block_id": "B2",
        },
    ]

    selected = V7ExtractorService._select_stage2_chunks(
        chunks,
        "strong",
        holistic_fact_count=5,
        holistic_performance_complete=False,
        holistic_performance_attempted=True,
    )

    assert [chunk.get("source_block_id") for chunk in selected] == ["B1"]


def test_partial_high_yield_holistic_sweep_still_repairs_dense_text():
    chunks = [
        {
            **_chunk("text", "The pH increased to 7.60 after 14 days.", "results"),
            "source_block_id": "B1",
        },
        {
            **_chunk("figure_caption", "Figure 4. Qualitative morphology.", "results"),
            "source_block_id": "B2",
        },
    ]

    selected = V7ExtractorService._select_stage2_chunks(
        chunks,
        "strong",
        holistic_fact_count=30,
        holistic_performance_complete=False,
        holistic_performance_attempted=True,
    )

    assert [chunk.get("source_block_id") for chunk in selected] == ["B1"]


def test_holistic_core_count_excludes_characterization_peaks():
    facts = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "S1",
            "metric_or_parameter": "FTIR_band_1",
            "value": "1722",
            "unit": "cm^-1",
            "evidence_text": "FTIR peak at 1722 cm^-1",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "S1",
            "metric_or_parameter": "tensile_strength",
            "value": "12",
            "unit": "MPa",
            "evidence_text": "S1 reached 12 MPa.",
        },
    ]

    assert V7ExtractorService._holistic_core_fact_count(facts) == 1


def test_incomplete_core_holistic_sweep_requests_stage2_fallback():
    failures = V7ExtractorService._guard_incomplete_holistic_performance([
        "performances: LLM stage timed out after 180s",
    ])

    assert failures == ["performances: LLM stage timed out after 180s"]


def test_specialized_holistic_warning_does_not_fail_core_output():
    failures = V7ExtractorService._guard_incomplete_holistic_performance([
        "spectroscopy: LLM stage timed out after 75s",
    ])

    assert failures == []


@pytest.mark.asyncio
async def test_strong_repair_prompt_is_compact_and_filters_setup_values(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_llm(_client, system_prompt, _user_prompt, **kwargs):
        captured["system_prompt"] = system_prompt
        captured["max_tokens"] = kwargs["max_tokens"]
        return {"facts": [
            {
                "fact_type": "performance",
                "candidate_sample_ids": ["Composite_10vol%"],
                "metric_or_parameter": "fiber_content",
                "value": "10",
                "unit": "%",
                "evidence_text": "At 10% fiber content, the softening load was 430 N.",
                "source_block_id": "B1",
                "source_page": 3,
            },
            {
                "fact_type": "performance",
                "candidate_sample_ids": ["Composite_10vol%"],
                "metric_or_parameter": "softening_load",
                "value": "430",
                "unit": "N",
                "evidence_text": "At 10% fiber content, the softening load was 430 N.",
                "source_block_id": "B1",
                "source_page": 3,
            },
        ]}, ""

    monkeypatch.setattr(V7ExtractorService, "_llm_json_tolerant", fake_llm)
    facts = await V7ExtractorService._stage2_fact_candidates(
        object(),
        [{
            **_chunk(
                "text",
                "At 10% fiber content, the softening load was 430 N.",
                "results",
            ),
            "source_block_id": "B1",
            "page_number": 3,
        }],
        model_mode="strong",
        holistic_fact_count=5,
        holistic_performance_attempted=True,
        known_sample_ids=["Composite_10vol%"],
    )

    assert captured["max_tokens"] == 1800
    assert "Known sample IDs:\nComposite_10vol%" in str(captured["system_prompt"])
    assert [fact["metric_or_parameter"] for fact in facts] == ["softening_load"]


def test_computational_figure_page_is_not_sent_to_vision():
    chunks = [
        {
            **_chunk("figure_caption", "Figure 3. Static properties and modulus."),
            "page_number": 3,
        },
        {
            **_chunk(
                "text",
                "The finite element simulation was performed in ABAQUS and the "
                "calculated result had an elastic modulus of 26 MPa.",
            ),
            "page_number": 3,
        },
    ]

    assert V7ExtractorService._is_computational_figure_page(chunks, 3)


@pytest.mark.asyncio
async def test_strong_stage2_table_uses_bounded_timeout(monkeypatch):
    captured: list[int] = []

    async def fake_llm(*_args, **kwargs):
        captured.append(kwargs["timeout_seconds"])
        return {"facts": []}, ""

    monkeypatch.setattr(settings, "STRONG_TABLE_LLM_TIMEOUT_SECONDS", 7)
    monkeypatch.setattr(V7ExtractorService, "_llm_json_tolerant", fake_llm)
    await V7ExtractorService._stage2_fact_candidates(
        object(),
        [{
            **_chunk("table_text", "[columns]\tSample\tStrength\n[row 1]\tS1\t12"),
            "source_block_id": "B1",
        }],
        model_mode="strong",
        holistic_fact_count=20,
        llm_timeout=180,
    )

    assert captured == [7]


@pytest.mark.asyncio
async def test_strong_stage2_table_uses_compact_fallback_prompt(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_llm(_client, system_prompt, user_prompt, **kwargs):
        captured.update({
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "stage": kwargs["stage"],
            "max_tokens": kwargs["max_tokens"],
        })
        return {"rows": [{
            "row": 1,
            "sample_id": "S1",
            "metric": "tensile strength",
            "value": "12",
            "unit": "MPa",
        }]}, ""

    monkeypatch.setattr(V7ExtractorService, "_llm_json_tolerant", fake_llm)
    facts = await V7ExtractorService._stage2_fact_candidates(
        object(),
        [{
            **_chunk(
                "table_text",
                "[columns]\tSample\tTensile strength [MPa]\n[row 1]\tS1\t12",
            ),
            "source_block_id": "B1",
        }],
        model_mode="strong",
        holistic_fact_count=20,
        known_sample_ids=["S1"],
    )

    assert captured["stage"] == "stage2_table_fallback"
    assert captured["max_tokens"] == 3000
    assert "Structured table:" in str(captured["user_prompt"])
    assert "metrics_list" not in str(captured["system_prompt"])
    assert len(facts) == 1
    assert facts[0]["metric_or_parameter"] == "tensile_strength"


@pytest.mark.asyncio
async def test_batched_stage2_fact_is_anchored_to_its_evidence_block(monkeypatch):
    async def fake_llm(*_args, **_kwargs):
        return {"facts": [{
            "fact_type": "performance",
            "candidate_sample_ids": ["S2"],
            "metric_or_parameter": "Youngs_modulus",
            "value": "20",
            "unit": "MPa",
            "evidence_text": "S2 had an elastic modulus of 20 MPa.",
            "source_location": "results section",
        }]}, ""

    monkeypatch.setattr(V7ExtractorService, "_llm_json_tolerant", fake_llm)
    facts = await V7ExtractorService._stage2_fact_candidates(
        object(),
        [
            {
                **_chunk("text", "S1 had an elastic modulus of 10 MPa."),
                "source_block_id": "B000010",
                "order_index": 10,
            },
            {
                **_chunk("text", "S2 had an elastic modulus of 20 MPa."),
                "source_block_id": "B000020",
                "order_index": 20,
            },
        ],
        model_mode="strong",
    )

    assert facts[0]["_source_block_id"] == "B000020"
    assert facts[0]["source_location"].endswith("block B000020")


@pytest.mark.asyncio
async def test_stage2_local_failure_keeps_existing_holistic_results(monkeypatch):
    async def timeout(*_args, **_kwargs):
        raise RuntimeError("LLM stage timed out")

    warnings: list[str] = []
    monkeypatch.setattr(V7ExtractorService, "_llm_json_tolerant", timeout)
    facts = await V7ExtractorService._stage2_fact_candidates(
        object(),
        [{
            **_chunk(
                "table_text",
                "[columns]\tSample\tUnknown result\n[row 1]\tS1\t12",
            ),
            "source_block_id": "B-timeout",
        }],
        model_mode="strong",
        holistic_fact_count=20,
        allow_partial_failures=True,
        warnings=warnings,
    )

    assert facts == []
    assert len(warnings) == 1
    assert "B-timeout" in warnings[0]
    assert "timed out" in warnings[0]


@pytest.mark.asyncio
async def test_stage2_retries_timeout_once_and_keeps_recovered_fact(monkeypatch):
    stages: list[str] = []

    async def timeout_then_succeed(*_args, **kwargs):
        stages.append(kwargs["stage"])
        if len(stages) == 1:
            raise TimeoutError
        return {"facts": [{
            "fact_type": "performance",
            "candidate_sample_ids": ["S1"],
            "metric_or_parameter": "tensile_strength",
            "value": "12",
            "unit": "MPa",
            "evidence_text": "S1 had a tensile strength of 12 MPa.",
            "source_location": "results section",
        }]}, ""

    monkeypatch.setattr(
        V7ExtractorService,
        "_llm_json_tolerant",
        timeout_then_succeed,
    )
    facts = await V7ExtractorService._stage2_fact_candidates(
        object(),
        [{
            **_chunk("text", "S1 had a tensile strength of 12 MPa."),
            "source_block_id": "B-retry",
        }],
        model_mode="strong",
    )

    assert stages == ["stage2_facts", "stage2_facts_retry"]
    assert len(facts) == 1
    assert facts[0]["metric_or_parameter"] == "tensile_strength"
    assert facts[0]["_source_block_id"] == "B-retry"


@pytest.mark.asyncio
async def test_stage2_failure_remains_fatal_without_prior_results(monkeypatch):
    async def timeout(*_args, **_kwargs):
        raise RuntimeError("LLM stage timed out")

    monkeypatch.setattr(V7ExtractorService, "_llm_json_tolerant", timeout)
    with pytest.raises(RuntimeError, match="timed out"):
        await V7ExtractorService._stage2_fact_candidates(
            object(),
            [{
                **_chunk(
                    "table_text",
                    "[columns]\tSample\tUnknown result\n[row 1]\tS1\t12",
                ),
                "source_block_id": "B-timeout",
            }],
            model_mode="strong",
            holistic_fact_count=0,
            allow_partial_failures=False,
        )


@pytest.mark.asyncio
async def test_stage1_sample_scan_keeps_normal_timeout(monkeypatch):
    captured: list[int] = []

    async def fake_llm(*_args, **kwargs):
        captured.append(kwargs["timeout_seconds"])
        return {"sample_mentions": []}, ""

    monkeypatch.setattr(V7ExtractorService, "_llm_json_tolerant", fake_llm)
    await V7ExtractorService._stage1_sample_mentions(
        object(),
        [_chunk("text", "S1 was prepared and tested.", "experimental")],
        "strong",
        llm_timeout=13,
    )

    assert captured == [13]


def test_slim_stage2_skips_figure_number_without_quantitative_result():
    chunks = [
        _chunk("figure_caption", "Figure 4. Relations between acetylation and WPG."),
    ]

    selected = V7ExtractorService._select_stage2_chunks(
        chunks,
        "strong",
        holistic_fact_count=20,
    )

    assert selected == []


def test_clean_value_variants_normalizes_written_inequality():
    assert V7ExtractorService._clean_value_variants("more than 46", "%") == [{
        "raw_value": "more than 46",
        "value_operator": ">",
        "clean_value": "46",
        "clean_unit": "%",
    }]


def test_complete_holistic_sweep_still_repairs_intrinsic_constituent_properties():
    chunks = [
        {
            **_chunk(
                "text",
                "TPU has a density of 1200 kg/m3, a Young's modulus of 20 MPa, "
                "and a Poisson's ratio of 0.43. T300 carbon fiber has a density "
                "of 1770 kg/m3 and a Young's modulus of 230 GPa.",
                "experimental",
            ),
            "source_block_id": "B000026",
        },
        {
            **_chunk(
                "text",
                "The reinforced structure reached a maximum load of 430 N.",
                "results",
            ),
            "source_block_id": "B000080",
        },
    ]

    selected = V7ExtractorService._select_stage2_chunks(
        chunks,
        "strong",
        holistic_fact_count=18,
        holistic_performance_complete=True,
        holistic_performance_attempted=True,
    )

    assert [chunk["source_block_id"] for chunk in selected] == ["B000026"]


def test_quantitative_result_guard_flags_silent_zero_output():
    chunks = [
        {
            **_chunk(
                "text",
                "The peak load increased from 350 N to 430 N, while displacement "
                "at 350 N changed from 16.7 mm to 8.8 mm.",
            ),
            "source_block_id": "B0042",
        },
        {
            **_chunk(
                "text",
                "The vibration bandgap extended from 1050 Hz to 1400 Hz.",
            ),
            "source_block_id": "B0055",
        },
    ]

    warning = V7ExtractorService._guard_suspicious_empty_records(
        chunks,
        [],
        fact_count=0,
    )

    assert "B0042" in warning
    assert "已保留样品卡和中间事实" in warning


def test_quantitative_result_guard_ignores_conditions_and_non_result_sections():
    chunks = [
        _chunk("text", "The specimen was tested at 25 °C.", "results"),
        _chunk("text", "Prior work reported a tensile strength of 30 MPa.", "introduction"),
    ]

    warning = V7ExtractorService._guard_suspicious_empty_records(
        chunks,
        [],
        fact_count=0,
    )
    assert warning == ""


def test_quantitative_result_guard_accepts_nonempty_records():
    chunks = [_chunk("text", "The tensile strength was 30 MPa.")]

    warning = V7ExtractorService._guard_suspicious_empty_records(
        chunks,
        [{"performance_metric": "tensile_strength"}],
        fact_count=1,
    )
    assert warning == ""


def test_non_material_setup_fact_filter_keeps_real_material_metrics():
    assert V7ExtractorService._is_non_material_setup_fact({
        "metric_or_parameter": "test_mass_weight",
        "value": "43",
        "unit": "mg",
        "evidence_text": "These experiments use a PDMS test mass of 43 mg.",
    })
    assert V7ExtractorService._is_non_material_setup_fact({
        "metric_or_parameter": "imidization_degree",
        "value": "1",
        "unit": "μV",
        "evidence_text": "Voltage recorded outside of the Peltier stage.",
    })
    assert not V7ExtractorService._is_non_material_setup_fact({
        "metric_or_parameter": "imidization_degree",
        "value": "95",
        "unit": "%",
        "evidence_text": "The imidization degree reached 95%.",
    })


def test_deterministic_process_fallback_extracts_template_wetting():
    chunks = [_chunk(
        "text",
        "Template-wetting and self-poling were used to fabricate P(VDF-TrFE) nanowires.",
        "title_abstract",
    )]

    facts = V7ExtractorService._deterministic_process_facts(chunks)

    by_metric = {fact["metric_or_parameter"]: fact for fact in facts}
    assert by_metric["fabrication_method"]["value"] == "template-wetting"
    assert by_metric["poling_method"]["value"] == "self-poling"


def test_deterministic_electrospinning_process_fallback_extracts_fixed_settings():
    chunks = [
        _chunk(
            "text",
            "The ES process used PCL dissolved at 20 w/v% in acetic acid.",
            "experimental",
        ),
        _chunk(
            "text",
            "Applied voltage and working distance were kept fixed at 15 kV and "
            "11 cm, respectively. The spinning time was adjusted in the range "
            "of 7-15 min.",
            "experimental",
        ),
    ]

    facts = V7ExtractorService._deterministic_process_facts(chunks)
    by_metric = {fact["metric_or_parameter"]: fact for fact in facts}

    assert by_metric["spinning_method"]["value"] == "electrospinning"
    assert (by_metric["polymer_concentration"]["value"], by_metric["polymer_concentration"]["unit"]) == ("20", "w/v%")
    assert (by_metric["voltage"]["value"], by_metric["voltage"]["unit"]) == ("15", "kV")
    assert (
        by_metric["tip_to_collector_distance"]["value"],
        by_metric["tip_to_collector_distance"]["unit"],
    ) == ("11", "cm")
    assert (by_metric["spinning_time"]["value"], by_metric["spinning_time"]["unit"]) == ("7-15", "min")
    assert all(fact["_background_only"] for fact in facts)
    assert not by_metric["spinning_time"]["_apply_to_all_fiber_samples"]


def test_process_background_enriches_fiber_cards_only():
    facts = V7ExtractorService._deterministic_process_facts([
        _chunk(
            "text",
            "The ES process used PCL solution. Applied voltage and working "
            "distance were kept fixed at 15 kV and 11 cm, respectively.",
            "experimental",
        )
    ])
    cards = [
        {"sample_id": "PCL/AA", "fiber_type": "nanofiber"},
        {"sample_id": "SBCu_BG", "fiber_type": "bulk"},
        {"sample_id": "PCL_AA_solution", "fiber_type": "solution"},
    ]

    out = V7ExtractorService._enrich_sample_cards_from_process_facts(cards, facts)

    assert out[0]["spinning_method"] == "electrospinning"
    assert "voltage=15 kV" in out[0]["process_parameters"]
    assert "tip_to_collector_distance=11 cm" in out[0]["process_parameters"]
    assert not out[1].get("process_route")
    assert not out[2].get("process_route")


def test_background_propagation_does_not_cross_sample_forms_or_g000():
    cards = [
        {
            "sample_id": "PCL fiber",
            "sample_group_id": "G001",
            "fiber_type": "nanofiber",
            "process_route": "electrospinning",
        },
        {
            "sample_id": "PCL powder",
            "sample_group_id": "G001",
            "fiber_type": "bulk",
            "process_route": "",
        },
        {
            "sample_id": "unknown A",
            "sample_group_id": "G000",
            "fiber_type": "nanofiber",
            "structure_features": "feature A",
        },
        {
            "sample_id": "unknown B",
            "sample_group_id": "G000",
            "fiber_type": "nanofiber",
            "structure_features": "",
        },
    ]

    out = V7ExtractorService._propagate_sample_card_backgrounds(cards)

    assert not out[1]["process_route"]
    assert not out[3]["structure_features"]


def test_local_assignment_does_not_turn_peak_phrase_into_sample():
    facts = [{
        "fact_type": "performance",
        "metric_or_parameter": "FTIR_band_1",
        "value": "1722",
        "unit": "cm^-1",
        "assigned_sample_id": None,
        "candidate_sample_ids": [],
        "assignment_status": "unassigned",
        "evidence_text": (
            "Both the neat PCL and composite fibers showed the carbonyl "
            "stretching peak (1722 cm^-1)."
        ),
    }]
    cards = [
        {"sample_id": "PCL/AA", "sample_aliases": []},
        {"sample_id": "PCL/AA/S", "sample_aliases": []},
        {"sample_id": "PCL/AA/SBCu", "sample_aliases": []},
    ]

    out = V7ExtractorService._local_sample_assignment(facts, cards)

    assert out[0]["assigned_sample_id"] is None


def test_variable_context_overrides_generic_filler_assignment():
    samples = [
        {
            "sample_id": "PES_0.5wtG_CF_EP",
            "variable_name": "graphene loading",
            "variable_value": "0.5",
            "variable_unit": "wt%",
        },
        {
            "sample_id": "PES_1.5wtG_CF_EP",
            "variable_name": "graphene loading",
            "variable_value": "1.5",
            "variable_unit": "wt%",
        },
    ]
    facts = [{
        "fact_type": "performance",
        "assigned_sample_id": "graphene",
        "candidate_sample_ids": ["graphene"],
        "condition": "1.5 % graphene in the PES nanofiber membrane",
        "evidence_text": "The average nanofiber diameter was 319 nm.",
    }]

    out = V7ExtractorService._repair_sample_assignment_from_variable_context(
        facts,
        samples,
    )

    assert out[0]["assigned_sample_id"] == "PES_1.5wtG_CF_EP"
    assert out[0]["assignment_confidence"] == 0.92
    assert "sample_bound_from_variable_context" in out[0]["assignment_reason"]


def test_variable_context_does_not_confuse_weight_loss_with_filler_loading():
    samples = [{
        "sample_id": "PES_5wtG_CF_EP",
        "variable_name": "graphene loading",
        "variable_value": "5",
        "variable_unit": "wt%",
    }]
    facts = [{
        "fact_type": "performance",
        "assigned_sample_id": "pure PES membrane",
        "condition": "5 wt% weight loss; decomposition at 445 °C",
    }]

    out = V7ExtractorService._repair_sample_assignment_from_variable_context(
        facts,
        samples,
    )

    assert out[0]["assigned_sample_id"] == "pure PES membrane"


def test_sample_card_sanitizer_merges_cleaned_anaphoric_identity():
    cards = V7ExtractorService._sanitize_sample_cards([
        {
            "sample_id": "that of epoxy resin matrix",
            "evidence_text": "The value was lower than that of epoxy resin matrix.",
        },
        {
            "sample_id": "epoxy resin matrix",
            "material_system": "epoxy",
            "evidence_text": "The epoxy resin matrix was used as the control.",
        },
    ])

    assert len(cards) == 1
    assert cards[0]["sample_id"] == "epoxy resin matrix"
    assert cards[0]["material_system"] == "epoxy"
    assert "that of epoxy resin matrix" in cards[0]["sample_aliases"]


def test_sample_aliases_are_serialized_before_database_binding():
    encoded = V7ExtractorService._serialize_sample_aliases([
        "PES nanofiber film",
        "pure PES nanofiber membrane",
    ])

    assert json.loads(encoded) == [
        "PES nanofiber film",
        "pure PES nanofiber membrane",
    ]
    assert V7ExtractorService._serialize_sample_aliases([]) is None


def test_deterministic_transition_fallback_recovers_explicit_strains():
    chunks = [
        {
            **_chunk(
                "text",
                "The response has a distinct knee centered at about 0.2% strain.",
            ),
            "source_block_id": "B1",
            "page_number": 3,
        },
        {
            **_chunk(
                "text",
                "The damage index later decreases as the strain exceeds 0.35%.",
            ),
            "source_block_id": "B2",
            "page_number": 5,
        },
        {
            **_chunk(
                "text",
                "Beyond 0.7-0.8% of applied strain, the sample shows a stiffness recovery.",
            ),
            "source_block_id": "B3",
            "page_number": 6,
        },
    ]
    existing = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "UD_flax_bioepoxy_laminate",
            "metric_or_parameter": "Youngs_modulus",
            "value": "21",
            "unit": "GPa",
            "extraction_method": "AI_holistic",
            "source_location": "page 4 | results | text",
            "evidence_text": "The flax composite had E1 = 21 GPa.",
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "UD_flax_bioepoxy_laminate",
            "metric_or_parameter": "Youngs_modulus",
            "value": "16",
            "unit": "GPa",
            "extraction_method": "AI_holistic",
            "source_location": "page 6 | results | text",
            "evidence_text": "The flax composite had E2 = 16 GPa.",
        },
    ]

    facts = V7ExtractorService._deterministic_transition_facts(chunks, existing)

    by_metric = {fact["metric_or_parameter"]: fact for fact in facts}
    assert by_metric["knee_strain"]["value"] == "0.2"
    assert by_metric["damage_transition_strain"]["value"] == "0.35"
    assert by_metric["stiffness_recovery_strain"]["value"] == "0.7-0.8"
    assert all(
        fact["assigned_sample_id"] == "UD_flax_bioepoxy_laminate"
        for fact in facts
    )


def test_deterministic_transition_fallback_recovers_behavior_displacement():
    chunks = [{
        **_chunk(
            "text",
            "The stress-strain curve showed an initially stiff response up to a "
            "displacement of approximately 17 mm. It then became compliant. "
            "The softening load of the fiber-reinforced structure was 430 N.",
            "results",
        ),
        "source_block_id": "B43",
        "page_number": 3,
        "order_index": 43,
    }]
    existing = [{
        "fact_type": "performance",
        "assigned_sample_id": "fiber-reinforced structure",
        "metric_or_parameter": "softening_load",
        "value": "430",
        "unit": "N",
        "_source_block_id": "B43",
        "_source_page": 3,
        "evidence_text": "The softening load was 430 N.",
    }]

    facts = V7ExtractorService._deterministic_transition_facts(chunks, existing)

    assert len(facts) == 1
    assert facts[0]["metric_or_parameter"] == "compressive_displacement"
    assert facts[0]["value"] == "17"
    assert facts[0]["unit"] == "mm"
    assert facts[0]["assigned_sample_id"] == "fiber-reinforced structure"


def test_deterministic_transition_corrects_existing_displacement_metric():
    chunks = [{
        **_chunk(
            "text",
            "The curve showed an initially stiff response up to a displacement "
            "of approximately 17 mm, then became compliant.",
            "results",
        ),
        "source_block_id": "B43",
        "page_number": 3,
    }]
    existing = [{
        "fact_type": "performance",
        "assigned_sample_id": "fiber-reinforced structure",
        "metric_or_parameter": "softening_displacement",
        "value": "17",
        "unit": "mm",
        "evidence_text": chunks[0]["raw_text"],
        "_source_block_id": "B43",
        "_source_page": 3,
    }]

    recovered = V7ExtractorService._deterministic_transition_facts(chunks, existing)

    assert recovered == []
    assert existing[0]["metric_or_parameter"] == "compressive_displacement"
    assert existing[0]["unit"] == "mm"


def test_deterministic_transition_does_not_use_wrong_fact_as_its_own_sample_hint():
    text = (
        "The fiber-reinforced material structure has better deformation resistance. "
        "The curve showed an initially stiff response up to a displacement of "
        "approximately 17 mm, then became compliant. The softening load of the "
        "fiber-reinforced structure was 430 N, whereas TPU softened at 350 N."
    )
    chunks = [{
        **_chunk("text", text, "results"),
        "source_block_id": "B43",
        "page_number": 3,
        "order_index": 43,
    }]
    existing = [
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPU",
            "metric_or_parameter": "compressive_displacement",
            "value": "17",
            "unit": "mm",
            "evidence_text": "The transition occurred at 17 mm.",
            "_source_block_id": "B43",
            "_source_page": 3,
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPMS composite",
            "metric_or_parameter": "softening_load",
            "value": "430",
            "unit": "N",
            "evidence_text": "The composite softened at 430 N.",
            "_source_block_id": "B43",
            "_source_page": 3,
        },
        {
            "fact_type": "performance",
            "assigned_sample_id": "TPU",
            "metric_or_parameter": "softening_load",
            "value": "350",
            "unit": "N",
            "evidence_text": "TPU softened at 350 N.",
            "_source_block_id": "B43",
            "_source_page": 3,
        },
    ]

    recovered = V7ExtractorService._deterministic_transition_facts(chunks, existing)

    assert recovered == []
    assert existing[0]["assigned_sample_id"] == "TPMS composite"
    assert existing[0]["candidate_sample_ids"] == ["TPMS composite"]


def test_deterministic_transition_fallback_skips_existing_correctable_fact():
    chunks = [_chunk(
        "text",
        "The curve has a distinct knee centered at about 0.2% strain.",
    )]
    existing = [{
        "fact_type": "performance",
        "metric_or_parameter": "surface_roughness",
        "value": "0.2",
        "unit": "%",
        "evidence_text": chunks[0]["raw_text"],
    }]

    assert V7ExtractorService._deterministic_transition_facts(chunks, existing) == []


def test_deterministic_transition_restores_full_source_evidence():
    chunks = [{
        **_chunk(
            "text",
            "Fig. 10 shows the damage index of a flax-based composite sample. "
            "The damage index later decreases as the strain exceeds 0.35%.",
        ),
        "source_block_id": "B10",
        "page_number": 5,
    }]
    existing = [{
        "fact_type": "performance",
        "assigned_sample_id": "UD_flax_bioepoxy_laminate",
        "metric_or_parameter": "damage_transition_strain",
        "value": "0.35",
        "unit": "%",
        "extraction_method": "AI_holistic",
        "source_location": "page 5 | results | text",
        "evidence_text": "The damage index ... exceeds 0.35%.",
    }]

    recovered = V7ExtractorService._deterministic_transition_facts(chunks, existing)

    assert recovered == []
    assert "flax-based composite sample" in existing[0]["evidence_text"]
    assert existing[0]["_source_block_id"] == "B10"


def test_deterministic_transition_uses_nearby_grounded_sample_context():
    chunks = [
        {
            **_chunk(
                "text",
                "The response has a distinct knee centered at about 0.2% strain.",
            ),
            "source_block_id": "B57",
            "page_number": 3,
            "order_index": 56,
        },
        {
            **_chunk(
                "figure_caption",
                "Fig. 5. Monotonic tensile test on a flax fiber reinforced composite.",
            ),
            "source_block_id": "B61",
            "page_number": 4,
            "order_index": 60,
        },
    ]
    existing = [{
        "fact_type": "performance",
        "assigned_sample_id": "UD_flax_bioepoxy_laminate",
        "metric_or_parameter": "knee_strain",
        "value": "0.2",
        "unit": "%",
        "extraction_method": "AI_holistic",
        "source_location": "page 3 | results | text",
        "evidence_text": "The knee was centered at 0.2% strain.",
    }]

    recovered = V7ExtractorService._deterministic_transition_facts(chunks, existing)

    assert recovered == []
    assert "flax fiber reinforced composite" in existing[0]["evidence_text"]
    assert existing[0]["_context_source_block_id"] == "B61"


def test_deterministic_transition_fallback_ignores_test_conditions_and_background():
    chunks = [
        _chunk("text", "The specimen was tested at 0.5% strain."),
        _chunk(
            "text",
            "Prior work reported a knee centered at 0.2% strain.",
            "introduction",
        ),
    ]

    assert V7ExtractorService._deterministic_transition_facts(chunks, []) == []


def test_grounded_table_material_is_not_reassigned_to_numbered_sample():
    facts = [{
        "assigned_sample_id": "acetylated jute",
        "candidate_sample_ids": ["acetylated jute"],
        "assignment_status": "assigned",
        "extraction_method": "AI_holistic_table",
        "_source_table_row": 1,
        "evidence_text": (
            "Table 2. Reusability of sorbents (acetylated jute)\n"
            "[columns]\t\tOil sorbed (g/g)\n[row 1]\tFirst cycle\t21.08"
        ),
    }]
    samples = [
        {"sample_id": "acetylated jute"},
        {"sample_id": "acetylated jute 1"},
    ]

    repaired = V7ExtractorService._repair_sample_assignment_specificity(facts, samples)

    assert repaired[0]["assigned_sample_id"] == "acetylated jute"


def test_alias_resolution_does_not_drift_to_numbered_table_sample():
    samples = [
        {
            "sample_id": "acetylated jute fiber",
            "sample_aliases": '["acetylated jute", "modified jute"]',
        },
        {"sample_id": "acetylated jute fiber sample 1", "sample_aliases": ""},
        {"sample_id": "acetylated jute fiber sample 10", "sample_aliases": ""},
    ]
    fact = {
        "assigned_sample_id": "acetylated jute",
        "candidate_sample_ids": ["acetylated jute"],
        "evidence_text": "The acetylated jute showed a weight loss of 56.26%.",
    }

    assert V7ExtractorService._resolve_fact_sample_id(fact, samples) == "acetylated jute fiber"


def test_fuzzy_assignment_requires_explicit_numbered_variant():
    facts = [{
        "assignment_status": "unassigned",
        "candidate_sample_ids": ["acetylated jute"],
        "evidence_text": "The acetylated jute showed a weight loss of 56.26%.",
    }]
    samples = [
        {"sample_id": "acetylated jute fiber", "sample_aliases": ["acetylated jute"]},
        {"sample_id": "acetylated jute fiber sample 1", "sample_aliases": []},
        {"sample_id": "acetylated jute fiber sample 10", "sample_aliases": []},
    ]

    assigned = V7ExtractorService._local_sample_assignment(facts, samples)

    assert assigned[0]["assigned_sample_id"] == "acetylated jute fiber"
