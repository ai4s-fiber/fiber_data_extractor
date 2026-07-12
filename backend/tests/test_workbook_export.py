"""Workbook export schema tests."""

from app.models.candidate_record import CandidateRecord
from app.services.workbook_export import (
    MAIN_DATA_COLUMNS,
    _excel_safe_value,
    _main_row,
    validate_main_data_row,
)


def test_main_row_matches_export_schema():
    record = CandidateRecord(
        record_id="V7-EXT-1-1",
        source_paper_id=1,
        paper_id_str="P0001",
        sample_id="S1",
        sample_group_id="G001",
        variable_name="CNC loading",
        variable_value="1.0",
        variable_unit="wt%",
        performance_metric="tensile_strength",
        performance_value="100",
        performance_unit="MPa",
        review_status="pending",
    )
    row = _main_row(record, None)
    validate_main_data_row(row)
    assert list(row.keys()) == MAIN_DATA_COLUMNS


def test_excel_safe_value_removes_control_characters():
    assert _excel_safe_value("20 kV m\u00011") == "20 kV m1"
