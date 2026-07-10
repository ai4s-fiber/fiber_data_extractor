from app.core.config import settings
from app.services.extractor_v7.service import V7ExtractorService


def _chunk(source_type: str, text: str, section: str = "results") -> dict:
    return {
        "source_type": source_type,
        "raw_text": text,
        "section_name": section,
        "page_number": 1,
    }


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


def test_stage2_signal_score_prioritizes_data_rich_chunks():
    table = _chunk("table_text", "Sample PVDF-1 tensile strength 12 MPa")
    plain = _chunk("text", "This section discusses the general motivation.", "conclusion")

    assert V7ExtractorService._stage2_signal_score(table) > V7ExtractorService._stage2_signal_score(plain)


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
