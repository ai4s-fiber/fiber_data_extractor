"""Workbook sheet definitions stay aligned between backend and frontend."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.workbook_export import WORKBOOK_SHEET_COLUMNS

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_EXPORT_FIELDS = ROOT / "frontend" / "src" / "data" / "exportFieldReference.ts"


def _frontend_source() -> str:
    if not FRONTEND_EXPORT_FIELDS.exists():
        pytest.skip(f"Frontend source not found: {FRONTEND_EXPORT_FIELDS}")
    return FRONTEND_EXPORT_FIELDS.read_text(encoding="utf-8")


def _parse_sheet_columns(sheet_key: str) -> list[str]:
    match = re.search(
        rf"['\"]{re.escape(sheet_key)}['\"]\s*:\s*\[(.*?)\]",
        _frontend_source(),
        re.DOTALL,
    )
    if not match:
        pytest.fail(f"Cannot locate sheet {sheet_key} in exportFieldReference.ts")
    return re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))


def test_workbook_sheet_keys_match_frontend():
    source = _frontend_source()
    frontend_keys = re.findall(
        r"^\s*['\"](\d{2}_[^'\"]+)['\"]\s*:\s*\[",
        source,
        re.MULTILINE,
    )
    assert frontend_keys == list(WORKBOOK_SHEET_COLUMNS)


@pytest.mark.parametrize(
    ("sheet_key", "backend_columns"),
    list(WORKBOOK_SHEET_COLUMNS.items()),
)
def test_workbook_sheet_columns_match_frontend(
    sheet_key: str,
    backend_columns: list[str],
):
    assert _parse_sheet_columns(sheet_key) == backend_columns
