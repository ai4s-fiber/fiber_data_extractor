"""Test that export column definitions stay aligned between backend and frontend."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

from app.services.workbook_export import (  # noqa: E402
    EVIDENCE_COLUMNS,
    MAIN_DATA_COLUMNS,
    PARSE_BLOCK_COLUMNS,
    PAPER_COLUMNS,
    QUALITY_COLUMNS,
)


def _parse_main_data_columns_from_frontend() -> list[str]:
    ts_path = ROOT / "frontend" / "src" / "data" / "exportFieldReference.ts"
    if not ts_path.exists():
        pytest.skip(f"Frontend source not found: {ts_path}")
    source = ts_path.read_text(encoding="utf-8")

    match = re.search(
        r"export const MAIN_DATA_COLUMN_NAMES\s*=\s*\[(.*?)\]\s*as const;",
        source,
        re.DOTALL,
    )
    if not match:
        pytest.fail("Cannot locate MAIN_DATA_COLUMN_NAMES in exportFieldReference.ts")

    return re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))


def _parse_sheet_field_names(sheet_key: str) -> list[str]:
    ts_path = ROOT / "frontend" / "src" / "data" / "exportFieldReference.ts"
    source = ts_path.read_text(encoding="utf-8")

    sheet_match = re.search(
        rf"key:\s*['\"]{re.escape(sheet_key)}['\"].*?fields:\s*\[(.*?)\n\s*\],",
        source,
        re.DOTALL,
    )
    if not sheet_match:
        pytest.fail(f"Cannot locate sheet {sheet_key} in exportFieldReference.ts")
    return re.findall(r"en:\s*['\"]([^'\"]+)['\"]", sheet_match.group(1))


class TestMainDataColumnConsistency:
    def test_count_is_32(self):
        assert len(MAIN_DATA_COLUMNS) == 32

    def test_frontend_count_is_32(self):
        fe_cols = _parse_main_data_columns_from_frontend()
        assert len(fe_cols) == 32

    def test_columns_match_exactly(self):
        fe_cols = _parse_main_data_columns_from_frontend()
        assert fe_cols == MAIN_DATA_COLUMNS

    def test_main_data_sheet_fields_match(self):
        sheet_cols = _parse_sheet_field_names("Main_Data")
        assert sheet_cols == MAIN_DATA_COLUMNS


class TestWorkbookSheetColumnConsistency:
    @pytest.mark.parametrize(
        ("sheet_key", "backend_cols"),
        [
            ("Papers", PAPER_COLUMNS),
            ("Evidence", EVIDENCE_COLUMNS),
            ("Parse_Blocks", PARSE_BLOCK_COLUMNS),
            ("Quality_Report", QUALITY_COLUMNS),
        ],
    )
    def test_sheet_fields_match_backend(self, sheet_key: str, backend_cols: list[str]):
        fe_cols = _parse_sheet_field_names(sheet_key)
        assert fe_cols == backend_cols, (
            f"{sheet_key} mismatch!\n"
            f"  Backend only: {set(backend_cols) - set(fe_cols)}\n"
            f"  Frontend only: {set(fe_cols) - set(backend_cols)}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
