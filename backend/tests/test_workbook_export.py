"""Workbook export schema tests."""

from openpyxl import load_workbook
import pytest

from app.models.candidate_record import CandidateRecord
from app.models.paper import Paper
from app.services.workbook_export import (
    MAIN_DATA_COLUMNS,
    _excel_safe_value,
    _main_row,
    generate_structured_workbook,
    validate_main_data_row,
)


def test_main_row_matches_export_schema():
    record = CandidateRecord(
        record_id="V7-EXT-1-1",
        source_paper_id=1,
        paper_id_str="P0001",
        paper_title="Fiber paper",
        doi_or_url="10.1000/example",
        year="2026",
        journal="Journal",
        sample_id="S1",
        sample_group_id="G001",
        variable_name="CNC loading",
        variable_value="1.0",
        variable_unit="wt%",
        performance_metric="tensile_strength",
        performance_value="100",
        performance_unit="MPa",
        performance_evidence="S1 reached 100 MPa.",
        extraction_method="mineru_cloud+llm_strong",
        evidence_text="S1 reached 100 MPa.",
        ai_confidence=0.97,
        review_status="pending",
    )
    row = _main_row(record, None)
    validate_main_data_row(row)
    assert list(row.keys()) == MAIN_DATA_COLUMNS
    assert len(row) == 40
    assert row["evidence_text"] == "S1 reached 100 MPa."


def test_excel_safe_value_removes_control_characters():
    assert _excel_safe_value("20 kV m\u00011") == "20 kV m1"


@pytest.mark.parametrize(
    "value",
    [
        "=HYPERLINK(\"https://example.invalid\")",
        "+SUM(1,2)",
        "-1+2",
        "@SUM(1,2)",
        "  =1+2",
    ],
)
def test_excel_safe_value_neutralizes_formula_injection(value):
    assert _excel_safe_value(value) == f"'{value}"


def test_structured_workbook_writes_complete_main_data_atomically(tmp_path):
    record = CandidateRecord(
        id=1,
        project_id=1,
        source_paper_id=1,
        record_id="R1",
        paper_id_str="P0001",
        paper_title="Fiber paper",
        sample_id="S1",
        performance_metric="tensile_strength",
        performance_value="100",
        performance_unit="MPa",
        evidence_text="S1 reached 100 MPa.",
        ai_confidence=0.95,
        review_status="pending",
    )
    paper = Paper(
        id=1,
        project_id=1,
        original_filename="paper.pdf",
        file_object_key="paper.pdf",
        paper_title="Fiber paper",
        status="review",
    )
    output = tmp_path / "result.xlsx"

    generate_structured_workbook(
        records=[record],
        papers=[paper],
        evidence_items=[],
        document_blocks=[],
        filepath=str(output),
    )

    workbook = load_workbook(output, read_only=True, data_only=True)
    try:
        sheet = workbook["Main_Data"]
        header = [cell.value for cell in next(sheet.iter_rows(max_row=1))]
        data = [cell.value for cell in next(sheet.iter_rows(min_row=2, max_row=2))]
    finally:
        workbook.close()
    assert header == MAIN_DATA_COLUMNS
    assert data[MAIN_DATA_COLUMNS.index("evidence_text")] == "S1 reached 100 MPa."
    assert not list(tmp_path.glob("*.tmp.xlsx"))
