"""Structured workbook export for MinerU-backed extraction results."""

from __future__ import annotations

from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from app.models.candidate_record import CandidateRecord
from app.models.document_parse import DocumentBlock
from app.models.evidence_item import EvidenceItem
from app.models.paper import Paper


MAIN_DATA_COLUMNS = [
    "record_id",
    "paper_id",
    "sample_id",
    "sample_group_id",
    "material_system",
    "fiber_type",
    "variable_name",
    "variable_value",
    "variable_unit",
    "composition_expression",
    "matrix_name",
    "matrix_content",
    "matrix_unit",
    "additive_expression",
    "solvent_or_aid",
    "process_route",
    "spinning_method",
    "process_parameters",
    "post_treatment",
    "structure_methods",
    "structure_features",
    "performance_category",
    "performance_metric",
    "performance_value",
    "performance_unit",
    "performance_method",
    "performance_condition",
    "evidence_id",
    "source_page",
    "confidence",
    "review_status",
    "reviewer_comment",
]

PAPER_COLUMNS = [
    "paper_id",
    "source_paper_db_id",
    "original_filename",
    "paper_title",
    "doi_or_url",
    "year",
    "journal",
    "authors",
    "publisher",
    "abstract",
    "supplementary_url",
]

EVIDENCE_COLUMNS = [
    "evidence_id",
    "paper_id",
    "record_id",
    "sample_id",
    "block_id",
    "page_number",
    "bbox",
    "source_type",
    "mineru_block_type",
    "source_location",
    "evidence_text",
    "confidence",
]

PARSE_BLOCK_COLUMNS = [
    "block_id",
    "paper_id",
    "page_number",
    "order_index",
    "block_type",
    "section_name",
    "bbox",
    "text_preview",
    "related_block_ids",
]

QUALITY_COLUMNS = ["metric", "value"]


def paper_export_id(record_or_paper: CandidateRecord | Paper) -> str:
    if isinstance(record_or_paper, CandidateRecord):
        return record_or_paper.paper_id_str or f"P{record_or_paper.source_paper_id:04d}"
    return f"P{record_or_paper.id:04d}"


def validate_main_data_row(row: dict[str, Any]) -> None:
    """Assert Main_Data row uses exactly the allowed 32 export columns in order."""
    if list(row.keys()) != MAIN_DATA_COLUMNS:
        raise ValueError(
            f"Main_Data column order mismatch: expected {MAIN_DATA_COLUMNS}, got {list(row.keys())}"
        )
    internal_fields = {
        "candidate_status", "source_location", "raw_value", "value_operator",
        "export_target", "fact_id", "source_block_id",
    }
    leaked = internal_fields & set(row.keys())
    if leaked:
        raise ValueError(f"Internal fields leaked into Main_Data: {sorted(leaked)}")


def generate_structured_workbook(
    *,
    records: list[CandidateRecord],
    papers: list[Paper],
    evidence_items: list[EvidenceItem],
    document_blocks: list[DocumentBlock],
    filepath: str,
) -> None:
    evidence_by_record: dict[int, EvidenceItem] = {}
    for evidence in evidence_items:
        if evidence.candidate_record_id is None:
            continue
        evidence_by_record.setdefault(evidence.candidate_record_id, evidence)

    paper_by_id = {paper.id: paper for paper in papers}
    paper_export_ids = {
        paper_id: _paper_id_for_records(paper_id, records)
        for paper_id in paper_by_id
    }

    main_rows = [
        _main_row(record, evidence_by_record.get(record.id))
        for record in records
    ]
    for row in main_rows:
        validate_main_data_row(row)
    paper_rows = [
        _paper_row(paper, paper_export_ids.get(paper.id, f"P{paper.id:04d}"))
        for paper in papers
    ]
    evidence_rows = [
        _evidence_row(evidence, paper_export_ids, records)
        for evidence in evidence_items
    ]
    block_rows = [
        _block_row(block, paper_export_ids)
        for block in document_blocks
    ]
    quality_rows = _quality_rows(records, evidence_items, document_blocks)

    wb = Workbook()
    default = wb.active
    wb.remove(default)
    _write_sheet(wb, "Main_Data", MAIN_DATA_COLUMNS, main_rows, "1F4E79")
    _write_sheet(wb, "Papers", PAPER_COLUMNS, paper_rows, "2F855A")
    _write_sheet(wb, "Evidence", EVIDENCE_COLUMNS, evidence_rows, "7C3AED")
    _write_sheet(wb, "Parse_Blocks", PARSE_BLOCK_COLUMNS, block_rows, "92400E")
    _write_sheet(wb, "Quality_Report", QUALITY_COLUMNS, quality_rows, "374151")
    wb.save(filepath)


def _paper_id_for_records(paper_id: int, records: list[CandidateRecord]) -> str:
    for record in records:
        if record.source_paper_id == paper_id and record.paper_id_str:
            return record.paper_id_str
    return f"P{paper_id:04d}"


def _main_row(record: CandidateRecord, evidence: EvidenceItem | None) -> dict[str, Any]:
    return {
        "record_id": record.record_id,
        "paper_id": paper_export_id(record),
        "sample_id": record.sample_id,
        "sample_group_id": record.sample_group_id,
        "material_system": record.material_system,
        "fiber_type": record.fiber_type,
        "variable_name": record.variable_name,
        "variable_value": record.variable_value,
        "variable_unit": record.variable_unit,
        "composition_expression": record.composition_expression,
        "matrix_name": record.matrix_name,
        "matrix_content": record.matrix_content,
        "matrix_unit": record.matrix_unit,
        "additive_expression": record.additive_expression,
        "solvent_or_aid": record.solvent_or_aid,
        "process_route": record.process_route,
        "spinning_method": record.spinning_method,
        "process_parameters": record.process_parameters,
        "post_treatment": record.post_treatment,
        "structure_methods": record.structure_methods,
        "structure_features": record.structure_features,
        "performance_category": record.performance_category,
        "performance_metric": record.performance_metric,
        "performance_value": record.performance_value,
        "performance_unit": record.performance_unit,
        "performance_method": record.performance_method,
        "performance_condition": record.performance_condition,
        "evidence_id": evidence.id if evidence else "",
        "source_page": evidence.page_start if evidence else "",
        "confidence": record.ai_confidence,
        "review_status": record.review_status,
        "reviewer_comment": record.reviewer_comment,
    }


def _paper_row(paper: Paper, paper_id: str) -> dict[str, Any]:
    return {
        "paper_id": paper_id,
        "source_paper_db_id": paper.id,
        "original_filename": paper.original_filename,
        "paper_title": paper.paper_title,
        "doi_or_url": paper.doi_or_url,
        "year": paper.year,
        "journal": paper.journal,
        "authors": "",
        "publisher": "",
        "abstract": "",
        "supplementary_url": "",
    }


def _record_lookup(records: list[CandidateRecord]) -> dict[int, CandidateRecord]:
    return {record.id: record for record in records}


def _evidence_row(
    evidence: EvidenceItem,
    paper_export_ids: dict[int, str],
    records: list[CandidateRecord],
) -> dict[str, Any]:
    records_by_id = _record_lookup(records)
    record = records_by_id.get(evidence.candidate_record_id or -1)
    return {
        "evidence_id": evidence.id,
        "paper_id": paper_export_ids.get(evidence.paper_id, f"P{evidence.paper_id:04d}"),
        "record_id": record.record_id if record else "",
        "sample_id": record.sample_id if record else "",
        "block_id": evidence.block_id,
        "page_number": evidence.page_start,
        "bbox": evidence.bbox_json,
        "source_type": evidence.source_type,
        "mineru_block_type": evidence.mineru_block_type,
        "source_location": evidence.source_location,
        "evidence_text": evidence.evidence_text,
        "confidence": evidence.confidence,
    }


def _block_row(
    block: DocumentBlock,
    paper_export_ids: dict[int, str],
) -> dict[str, Any]:
    text = block.text or block.html or ""
    return {
        "block_id": block.block_id,
        "paper_id": paper_export_ids.get(block.paper_id, f"P{block.paper_id:04d}"),
        "page_number": block.page_number,
        "order_index": block.order_index,
        "block_type": block.block_type,
        "section_name": block.section_name,
        "bbox": block.bbox_json,
        "text_preview": text[:500],
        "related_block_ids": block.related_block_ids_json,
    }


def _quality_rows(
    records: list[CandidateRecord],
    evidence_items: list[EvidenceItem],
    document_blocks: list[DocumentBlock],
) -> list[dict[str, Any]]:
    with_evidence = len({item.candidate_record_id for item in evidence_items if item.candidate_record_id})
    approved = len([record for record in records if record.review_status in ("approved", "通过")])
    uncertain = len([record for record in records if record.review_status in ("uncertain", "存疑")])
    return [
        {"metric": "main_data_rows", "value": len(records)},
        {"metric": "paper_count", "value": len({record.source_paper_id for record in records})},
        {"metric": "evidence_rows", "value": len(evidence_items)},
        {"metric": "rows_with_evidence", "value": with_evidence},
        {"metric": "document_blocks", "value": len(document_blocks)},
        {"metric": "approved_rows", "value": approved},
        {"metric": "uncertain_rows", "value": uncertain},
    ]


def _write_sheet(
    wb: Workbook,
    title: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    color: str,
) -> None:
    ws = wb.create_sheet(title)
    header_font = Font(name="微软雅黑", bold=True, size=11, color="FFFFFF")
    header_fill = PatternFill(start_color=color, end_color=color, fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    data_font = Font(name="微软雅黑", size=10)
    data_alignment = Alignment(vertical="top", wrap_text=True)
    border = Border(
        left=Side(style="thin", color="CCCCCC"),
        right=Side(style="thin", color="CCCCCC"),
        top=Side(style="thin", color="CCCCCC"),
        bottom=Side(style="thin", color="CCCCCC"),
    )

    for col_idx, column in enumerate(columns, 1):
        cell = ws.cell(row=1, column=col_idx, value=column)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = border

    for row_idx, row in enumerate(rows, 2):
        for col_idx, column in enumerate(columns, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=row.get(column))
            cell.font = data_font
            cell.alignment = data_alignment
            cell.border = border

    for col_idx, column in enumerate(columns, 1):
        max_len = len(column)
        for row_idx in range(2, len(rows) + 2):
            value = ws.cell(row=row_idx, column=col_idx).value
            if value not in (None, ""):
                max_len = max(max_len, min(len(str(value)), 60))
        ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = max_len + 3
    ws.freeze_panes = "A2"
