"""Export column definitions stay aligned between backend and frontend."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.workbook_export import (
    EVIDENCE_COLUMNS,
    MAIN_DATA_COLUMNS,
    PAPER_COLUMNS,
    PARSE_BLOCK_COLUMNS,
    QUALITY_COLUMNS,
)

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_EXPORT_FIELDS = ROOT / "frontend" / "src" / "data" / "exportFieldReference.ts"


def _frontend_source() -> str:
    if not FRONTEND_EXPORT_FIELDS.exists():
        pytest.skip(f"Frontend source not found: {FRONTEND_EXPORT_FIELDS}")
    return FRONTEND_EXPORT_FIELDS.read_text(encoding="utf-8")


def _parse_main_data_columns_from_frontend() -> list[str]:
    match = re.search(
        r"export const MAIN_DATA_COLUMN_NAMES\s*=\s*\[(.*?)\]\s*as const;",
        _frontend_source(),
        re.DOTALL,
    )
    if not match:
        pytest.fail("Cannot locate MAIN_DATA_COLUMN_NAMES in exportFieldReference.ts")
    return re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))


def _parse_sheet_field_names(sheet_key: str) -> list[str]:
    sheet_match = re.search(
        rf"key:\s*['\"]{re.escape(sheet_key)}['\"].*?fields:\s*\[(.*?)\n\s*\],",
        _frontend_source(),
        re.DOTALL,
    )
    if not sheet_match:
        pytest.fail(f"Cannot locate sheet {sheet_key} in exportFieldReference.ts")
    return re.findall(r"en:\s*['\"]([^'\"]+)['\"]", sheet_match.group(1))


def test_main_data_column_count_is_stable():
    assert len(MAIN_DATA_COLUMNS) == 32
    assert len(_parse_main_data_columns_from_frontend()) == 32


def test_main_data_columns_match_frontend_exactly():
    assert _parse_main_data_columns_from_frontend() == MAIN_DATA_COLUMNS
    assert _parse_sheet_field_names("Main_Data") == MAIN_DATA_COLUMNS


@pytest.mark.parametrize(
    ("sheet_key", "backend_cols"),
    [
        ("Papers", PAPER_COLUMNS),
        ("Evidence", EVIDENCE_COLUMNS),
        ("Parse_Blocks", PARSE_BLOCK_COLUMNS),
        ("Quality_Report", QUALITY_COLUMNS),
    ],
)
def test_workbook_sheet_fields_match_frontend(sheet_key: str, backend_cols: list[str]):
    frontend_cols = _parse_sheet_field_names(sheet_key)
    assert frontend_cols == backend_cols, (
        f"{sheet_key} mismatch\n"
        f"Backend only: {set(backend_cols) - set(frontend_cols)}\n"
        f"Frontend only: {set(frontend_cols) - set(backend_cols)}"
    )
