"""Rebuild final candidate records from persisted V7 sample/fact tables.

This script does not call the LLM. It is useful after changing Stage 4
normalization or export rules while keeping the latest extracted facts.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import app.models  # noqa: F401
from sqlalchemy import delete as sa_delete, select

from app.core.config import settings
from app.core.database import async_session_factory, close_database
from app.models.candidate_record import CandidateRecord
from app.models.evidence_item import EvidenceItem
from app.models.fact_candidate import FactCandidate
from app.models.paper import Paper
from app.models.sample_catalog import SampleCatalog
from app.services.extractor_v7 import V7ExtractorService, build_extraction_report


def _parse_json_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return [str(value)]
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return [str(parsed)]


def _sample_to_dict(sample: SampleCatalog) -> dict:
    fields = [
        "sample_id", "sample_aliases", "sample_group_id", "material_system",
        "fiber_type", "variable_name", "variable_value", "variable_unit",
        "composition_expression", "process_route", "source_location",
        "evidence_text", "confidence",
    ]
    return {field: getattr(sample, field, "") or "" for field in fields}


def _fact_to_dict(fact: FactCandidate) -> dict:
    return {
        "fact_id": fact.fact_id,
        "fact_type": fact.fact_type,
        "subject_text": fact.subject_text or "",
        "candidate_sample_ids": _parse_json_list(fact.candidate_sample_ids),
        "metric_or_parameter": fact.metric_or_parameter or "",
        "value": fact.value or "",
        "unit": fact.unit or "",
        "method": fact.method or "",
        "condition": fact.condition or "",
        "category": fact.category or "",
        "evidence_text": fact.evidence_text or "",
        "source_location": fact.source_location or "",
        "extraction_method": fact.extraction_method or "AI_text",
        "confidence": fact.confidence,
        "assigned_sample_id": fact.assigned_sample_id,
        "assignment_confidence": fact.assignment_confidence,
        "assignment_status": fact.assignment_status,
    }


async def rebuild(paper_id: int) -> dict:
    async with async_session_factory() as db:
        paper = await db.get(Paper, paper_id)
        if not paper:
            raise RuntimeError(f"paper {paper_id} not found")

        sample_rows = (
            await db.execute(
                select(SampleCatalog)
                .where(SampleCatalog.paper_id == paper_id)
                .order_by(SampleCatalog.id)
            )
        ).scalars().all()
        fact_rows = (
            await db.execute(
                select(FactCandidate)
                .where(FactCandidate.paper_id == paper_id)
                .order_by(FactCandidate.id)
            )
        ).scalars().all()

        samples = [_sample_to_dict(row) for row in sample_rows]
        facts = [_fact_to_dict(row) for row in fact_rows]
        paper_metadata = {
            "paper_id_biz": f"P{paper_id:04d}",
            "paper_title": paper.paper_title or paper.original_filename,
            "doi_or_url": paper.doi_or_url or "",
            "year": paper.year,
            "journal": paper.journal or "",
        }

        records, report_data = V7ExtractorService._stage4_generate_records(
            paper_id, paper.project_id, paper_metadata, samples, facts
        )

        await db.execute(sa_delete(EvidenceItem).where(EvidenceItem.paper_id == paper_id))
        await db.execute(sa_delete(CandidateRecord).where(CandidateRecord.source_paper_id == paper_id))
        await db.flush()

        saved_count = 0
        for record in records:
            validation_issues = record.pop("_validation_issues", [])
            fact_id = record.pop("_fact_id", "")
            rec = CandidateRecord(
                project_id=record["project_id"],
                source_paper_id=record["source_paper_id"],
                record_id=record["record_id"],
                paper_id_str=record.get("paper_id_str", ""),
                paper_title=record["paper_title"],
                doi_or_url=record["doi_or_url"],
                year=record["year"],
                journal=record["journal"],
                sample_group_id=record["sample_group_id"],
                sample_id=record["sample_id"],
                material_system=record["material_system"],
                fiber_type=record.get("fiber_type", ""),
                variable_name=record.get("variable_name", ""),
                variable_value=record.get("variable_value", ""),
                variable_unit=record.get("variable_unit", ""),
                composition_expression=record["composition_expression"],
                matrix_name=record.get("matrix_name", ""),
                matrix_content=record.get("matrix_content", ""),
                matrix_unit=record.get("matrix_unit", ""),
                additive_expression=record.get("additive_expression", ""),
                solvent_or_aid=record.get("solvent_or_aid", ""),
                composition_evidence=record.get("composition_evidence", ""),
                process_route=record["process_route"],
                spinning_method=record.get("spinning_method", ""),
                process_parameters=record.get("process_parameters", ""),
                post_treatment=record.get("post_treatment", ""),
                process_evidence=record.get("process_evidence", ""),
                structure_methods=record.get("structure_methods", ""),
                structure_features=record.get("structure_features", ""),
                structure_evidence=record.get("structure_evidence", ""),
                performance_category=record["performance_category"],
                performance_metric=record["performance_metric"],
                performance_value=record["performance_value"],
                performance_unit=record["performance_unit"],
                performance_method=record.get("performance_method", ""),
                performance_condition=record.get("performance_condition", ""),
                performance_evidence=record.get("performance_evidence", ""),
                extraction_method=record.get("extraction_method", ""),
                evidence_text=record.get("evidence_text", ""),
                ai_confidence=record.get("ai_confidence", 0.5),
                review_status=record["review_status"],
                reviewer_comment="; ".join([
                    item for item in [
                        record.get("reviewer_comment", ""),
                        "; ".join(validation_issues),
                    ]
                    if item
                ]),
                source_location=record.get("source_location", ""),
            )
            db.add(rec)
            await db.flush()
            db.add(EvidenceItem(
                project_id=record["project_id"],
                paper_id=record["source_paper_id"],
                candidate_record_id=rec.id,
                source_type=f"fact_{fact_id}" if fact_id else "unknown",
                source_location=record.get("source_location", ""),
                evidence_text=record.get("evidence_text", "")[:2000],
                normalized_payload=json.dumps({
                    "fact_id": fact_id,
                    "metric": record["performance_metric"],
                    "value": record["performance_value"],
                    "unit": record["performance_unit"],
                }, ensure_ascii=False),
                confidence=float(record.get("ai_confidence", 0.5)),
            ))
            saved_count += 1

        extraction_report = build_extraction_report(
            paper_metadata=paper_metadata,
            sample_count=report_data["sample_count"],
            group_count=report_data["group_count"],
            fact_count=report_data["fact_count"],
            assigned_count=report_data["assigned_count"],
            unassigned_count=report_data["unassigned_count"],
            record_count=report_data["record_count"],
            missing_evidence_count=report_data["missing_evidence_count"],
            uncertain_count=report_data["uncertain_count"],
            missing_count=report_data["missing_count"],
            pending_count=report_data["pending_count"],
            approved_count=report_data["approved_count"],
            category_counts=report_data["category_counts"],
            extra_metrics={
                "样品卡数量": report_data["sample_count"],
                "结果事实数量": report_data["result_fact_count"],
                "核心记录数": report_data["core_record_count"],
                "补充记录数": report_data["secondary_record_count"],
                "文献信息缺失率": report_data["paper_metadata_missing_rate"],
                "文献信息缺失字段": report_data["paper_metadata_missing_fields"],
                "样品卡字段填充率": report_data["sample_card_field_fill_rate"],
                "最终记录字段填充率": report_data["final_record_field_fill_rate"],
                "来源位置过粗数量": report_data["rough_source_location_count"],
                "字段错位检测结果": report_data["schema_alignment_status"],
                "字段缺失自动补齐数": report_data["schema_missing_field_count"],
                "Core指标覆盖率": report_data["core_metric_coverage"],
                "指标优先级分布": report_data["metric_priority_counts"],
                "sample_cards": report_data["sample_cards"],
                "result_facts": report_data["result_facts"],
            },
        )

        report_path = os.path.join(settings.UPLOAD_DIR, str(paper.project_id), f"report_{paper_id}.json")
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as report_file:
            json.dump(extraction_report, report_file, ensure_ascii=False, indent=2)

        paper.status = "review"
        db.add(paper)
        await db.commit()
        return {
            "paper_id": paper_id,
            "samples": len(samples),
            "facts": len(facts),
            "records": saved_count,
            "core_records": report_data["core_record_count"],
            "secondary_records": report_data["secondary_record_count"],
            "report_path": report_path,
        }


async def main() -> None:
    try:
        paper_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
        result = await rebuild(paper_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await close_database()


if __name__ == "__main__":
    asyncio.run(main())
