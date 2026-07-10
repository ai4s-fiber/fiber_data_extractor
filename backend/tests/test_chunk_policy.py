"""Chunk selection policy tests."""

from app.services.extractor_v7.service import V7ExtractorService


def _chunk(
    section: str,
    text: str,
    source_type: str = "text",
    page: int = 1,
) -> dict:
    return {
        "section_name": section,
        "raw_text": text,
        "source_type": source_type,
        "page_number": page,
    }


def test_stage1_chunks_cap_strong_mode():
    chunks = [
        _chunk("results", f"fiber sample data {i} tensile strength {i} MPa" * 5)
        for i in range(100)
    ]
    selected = V7ExtractorService._stage1_chunks(chunks, "strong")
    assert len(selected) <= 40


def test_stage2_chunks_prioritize_tables():
    chunks = [
        _chunk("introduction", "intro text"),
        *[
            _chunk("results", f"table row {i} tensile {i} MPa", "table_text", page=i // 10 + 1)
            for i in range(80)
        ],
        _chunk("results", "caption text", "figure_caption"),
    ]
    selected = V7ExtractorService._select_stage2_chunks(chunks, "strong")
    table_count = sum(1 for c in selected if c.get("source_type") == "table_text")
    assert table_count >= 8
    assert selected[0].get("source_type") == "table_text"
    assert len(selected) <= 100


def test_fact_chunks_prioritize_tables_and_results():
    chunks = [
        _chunk("introduction", "short intro"),
        _chunk("results", "x" * 300, "figure_caption"),
        _chunk("results", "tensile strength 10 MPa"),
    ]
    ordered = V7ExtractorService._fact_chunks(chunks)
    assert ordered[0].get("source_type") == "figure_caption"
