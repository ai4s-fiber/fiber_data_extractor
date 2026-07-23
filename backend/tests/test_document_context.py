"""MinerU document normalization tests."""

from app.services.document_context import (
    DocumentBlockData,
    DocumentContext,
    _file_sha256,
    _shared_artifact_paths,
    build_document_context_from_mineru_result,
    load_shared_mineru_artifact,
    persist_shared_mineru_artifact,
    table_html_to_tsv,
)
from app.services.mineru_client import MinerUParseResult


def test_table_html_to_tsv_preserves_every_cell_with_row_ids():
    html = (
        "<table><tr><th>Sample</th><th>WPG (%)</th></tr>"
        "<tr><td>1</td><td>6.55</td></tr>"
        "<tr><td>2</td><td>8.76</td></tr></table>"
    )

    text = table_html_to_tsv(html)

    assert "[columns]\tSample\tWPG (%)" in text
    assert "[row 1]\t1\t6.55" in text
    assert "[row 2]\t2\t8.76" in text
    assert "<td>" not in text


def test_document_chunks_include_caption_and_table_body():
    context = DocumentContext(
        paper_id=1,
        job_id=1,
        parse_run_id=1,
        parser_name="mineru",
        markdown_text="",
        pages=[],
        blocks=[DocumentBlockData(
            block_id="B1",
            page_number=3,
            order_index=0,
            block_type="table",
            section_name="results",
            text="Table 1. Results",
            html="<table><tr><td>Sample</td><td>Strength</td></tr>"
                 "<tr><td>S1</td><td>12 MPa</td></tr></table>",
        )],
        tables=[],
        figures=[],
    )

    chunks = context.chunks()

    assert len(chunks) == 1
    assert chunks[0]["source_type"] == "table_text"
    assert "Table 1. Results" in chunks[0]["raw_text"]
    assert "[row 1]\tS1\t12 MPa" in chunks[0]["raw_text"]


def test_shared_parse_cache_is_keyed_by_pdf_content_and_parser_config(tmp_path, monkeypatch):
    from app.services.document_context import settings

    monkeypatch.setattr(settings, "PARSE_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    first_pdf = tmp_path / "first.pdf"
    second_pdf = tmp_path / "second.pdf"
    first_pdf.write_bytes(b"same filename is irrelevant: first content")
    second_pdf.write_bytes(b"second content")

    first_hash = _file_sha256(str(first_pdf))
    second_hash = _file_sha256(str(second_pdf))
    first_paths = _shared_artifact_paths(first_hash, "mineru_cloud")
    repeated_paths = _shared_artifact_paths(first_hash, "mineru_cloud")
    second_paths = _shared_artifact_paths(second_hash, "mineru_cloud")

    assert first_paths == repeated_paths
    assert first_paths != second_paths
    assert first_hash in str(first_paths[0])


def test_bulk_prefill_uses_normal_shared_parse_cache(tmp_path, monkeypatch):
    from app.services.document_context import settings

    monkeypatch.setattr(settings, "PARSE_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"same bytes used by the later extraction job")
    document_sha256 = _file_sha256(str(pdf))
    result = MinerUParseResult(
        task_id="batch-1",
        backend="vlm",
        version="cloud_v4",
        document_name="paper.pdf",
        md_content="# Parsed\n\nbody",
        content_list=[{"type": "text", "text": "body"}],
        content_list_v2=[],
        middle_json={},
        raw_result={"batch_id": "batch-1"},
        elapsed_seconds=1.0,
    )

    persist_shared_mineru_artifact(result, document_sha256)
    loaded = load_shared_mineru_artifact(document_sha256)

    assert loaded is not None
    assert loaded.task_id == "batch-1"
    assert loaded.md_content.startswith("# Parsed")


def test_mineru_numbered_domain_headings_update_following_block_sections():
    headings = (
        ("1. Introduction", "Introduction body", "introduction"),
        (
            "2. Geometrical Structure and Equivalent Mechanical Parameters",
            "Geometry body",
            "experimental",
        ),
        (
            "3. Mechanical Compressive Properties under Quasi-Static Conditions",
            "The peak force was 430 N.",
            "results",
        ),
        ("4. Band Structure and Vibration Isolation Characteristics", "Bandgap body", "results"),
        ("5. Energy Absorption Property under Impact Condition", "Impact body", "results"),
        ("6. Conclusion", "Conclusion body", "conclusion"),
    )
    items = []
    expected = []
    for index, (heading, body, section) in enumerate(headings):
        items.extend((
            {"type": "text", "text": heading, "text_level": 2, "page_idx": index},
            {"type": "text", "text": body, "page_idx": index},
        ))
        expected.extend((section, section))
    result = MinerUParseResult(
        task_id="test",
        backend="vlm",
        version="test",
        document_name="paper.pdf",
        md_content="",
        content_list=items,
        content_list_v2=[],
        middle_json={},
        raw_result={},
        elapsed_seconds=0.0,
    )

    context = build_document_context_from_mineru_result(1, 1, 1, result)

    assert [block.section_name for block in context.blocks] == expected
