"""
V7 Multi-Stage Extraction Pipeline for Fiber Material Literature.

Stage 0: PDF parsing & chunking (source-aware)
Stage 1: Sample catalog generation (identify all samples first)
Stage 2: Fact candidate extraction (all numerical facts, not yet assigned)
Stage 3: Sample assignment (match facts to catalog samples)
Stage 4: Record generation (build 40-column records from assigned facts)
Stage 5: Quality validation + extraction report
Stage 6: Persistence to database

The pipeline is generic — no paper-specific rules or hardcoded sample names.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy import delete as sa_delete

from app.models.paper import Paper
from app.models.project import Project
from app.models.page_inventory import PageInventory
from app.models.candidate_record import CandidateRecord
from app.models.evidence_item import EvidenceItem
from app.models.sample_catalog import SampleCatalog
from app.models.fact_candidate import FactCandidate

from app.services.llm_client import create_llm_client, _tolerant_parse_json
from app.services.pdf_utils import extract_pdf_text, extract_pdf_tables_markdown, render_pdf_pages
from app.services.chunking import (
    chunk_pdf_text,
    chunks_for_sample_catalog,
    chunks_for_performance_extraction,
    chunks_for_composition_process,
)
from app.services.metrics_dictionary import (
    build_metrics_prompt_text,
    build_structure_prompt_text,
    build_process_prompt_text,
    find_metric_canonical,
    find_category_for_metric,
    find_structure_feature_canonical,
    find_process_parameter_canonical,
    get_common_units,
)

# ---------------------------------------------------------------------------
# Prompt templates — generic, no paper-specific content
# ---------------------------------------------------------------------------

STAGE1_SAMPLE_CATALOG_PROMPT = """You are a fiber materials scientist. Your task is to identify ALL material samples described in this paper.

Read the experimental section, figure captions, and tables carefully. Find every distinct sample name.

Rules:
1. Use the EXACT sample ID from the paper text. Do NOT invent names.
2. Look for samples in: experimental section, figure captions (Fig. X), table headers, and table row labels.
3. Common patterns: S1/S2/S3, A/B/C, PI-200/PI-300, 0%/3%/5%, Sample-1/2/3, neat/modified, Pristine/Treated.
4. If multiple variable systems exist (e.g., different filler contents AND different temperatures), assign different sample_group_id.
5. Control/neat/pristine/reference samples must be identified separately.
6. Include sample aliases: if the paper calls the same sample "PI-200" in one place and "PI-200°C" in another, list both.
7. Variable_name is what changes across samples in a group (e.g., "imidization temperature", "TiO2 content").

Output JSON format:
{
  "paper_metadata": {"paper_title": "", "doi_or_url": "", "year": 2025, "journal": ""},
  "sample_catalog": [
    {
      "sample_id": "PI-200",
      "sample_aliases": ["PI-200°C", "200°C-PI"],
      "sample_group_id": "temperature-series",
      "material_system": "Polyimide aerogel",
      "fiber_type": "electrospun nanofiber",
      "variable_name": "imidization temperature",
      "variable_value": "200",
      "variable_unit": "°C",
      "composition_expression": "BPDA/ODA polyamic acid + 2MZ-AZINE crosslinker",
      "process_route": "electrospinning → freeze-drying → thermal imidization",
      "source_location": "Section 2.2, page 3",
      "evidence_text": "short quote from paper",
      "confidence": 0.9
    }
  ]
}"""

STAGE2_FACTS_PROMPT = """You are a fiber materials data extraction specialist. Extract ALL numerical factual data from the provided text, tables, and figure captions.

Rules:
1. ONE numerical value = ONE fact. Do NOT combine multiple values into one fact.
2. A sentence with 3 numbers → produce 3 facts.
3. A table with 5 rows × 4 columns → produce up to 20 facts (one per cell with a numerical value).
4. Each fact MUST include evidence_text (quote the original text) and source_location (page/table/figure).
5. candidate_sample_ids: List the sample names mentioned near this fact. If unclear, leave as empty list [].
6. If you cannot determine the unit, leave unit as empty string "" and lower confidence.
7. metric_or_parameter: Use the standardized name if possible, otherwise use the paper's wording.

Standardized performance metrics include:
{{metrics_list}}

Standardized structure features include:
{{structure_list}}

Standardized process parameters include:
{{process_list}}

fact_type must be one of: composition, process, structure, performance.

Output JSON format:
{
  "facts": [
    {
      "fact_id": "F001",
      "fact_type": "performance",
      "subject_text": "what is being measured",
      "candidate_sample_ids": ["PI-200"],
      "metric_or_parameter": "tensile_strength",
      "value": "7.13",
      "unit": "MPa",
      "method": "universal testing machine",
      "condition": "gauge length 20 mm, speed 10 mm/min",
      "category": "mechanical",
      "evidence_text": "PI-200 exhibited a tensile strength of 7.13 MPa",
      "source_location": "Section 3.2, page 8",
      "extraction_method": "AI_text",
      "confidence": 0.9
    }
  ]
}"""

STAGE3_ASSIGNMENT_PROMPT = """You are a data matching specialist. Given a sample catalog and a list of facts, assign each fact to the correct sample(s).

Sample catalog (known samples):
{{sample_catalog_json}}

Rules:
1. If the fact's evidence_text or subject_text explicitly mentions a sample_id or alias → high confidence assignment.
2. If the fact comes from a table row labeled with a sample name → match to that sample.
3. If the fact comes from a figure caption mentioning a sample → match to that sample.
4. If the context only refers to generic terms ("the modified fiber", "the composite"), do NOT force assignment.
5. A fact can be assigned to multiple samples (candidate_sample_ids with assignment_status="multiple").
6. If no reasonable match exists, set assigned_sample_id to null and assignment_status to "unassigned".
7. Set assignment_confidence between 0.0 and 1.0.

Output JSON format:
{
  "assignments": [
    {"fact_id": "F001", "assigned_sample_id": "PI-200", "assignment_confidence": 0.95, "assignment_status": "assigned"},
    {"fact_id": "F002", "assigned_sample_id": null, "assignment_confidence": 0.0, "assignment_status": "unassigned"}
  ]
}"""


# ---------------------------------------------------------------------------
# Quality validators
# ---------------------------------------------------------------------------

def validate_sample_catalog(samples: list[dict]) -> list[str]:
    """Validate sample catalog completeness."""
    issues = []
    if not samples:
        issues.append("未识别到任何样品")
        return issues
    for s in samples:
        sid = s.get("sample_id", "").strip()
        if not sid:
            issues.append("存在 sample_id 为空的条目")
        if len(sid) > 100:
            issues.append(f"sample_id 过长: {sid[:50]}...")
        if not s.get("source_location"):
            issues.append(f"样品 {sid} 缺少来源位置")
    return issues


def validate_fact(fact: dict) -> list[str]:
    """Validate a single fact candidate."""
    issues = []
    ftype = fact.get("fact_type", "")
    metric = fact.get("metric_or_parameter", "").strip()
    value = fact.get("value", "").strip()
    unit = fact.get("unit", "").strip()
    evidence = fact.get("evidence_text", "").strip()
    source = fact.get("source_location", "").strip()
    method = fact.get("extraction_method", "").strip()
    confidence = fact.get("confidence", 0)

    if ftype == "performance":
        if not metric:
            issues.append("性能指标名称为空")
        if not value:
            issues.append("性能数值为空")
        if not metric and not value:
            issues.append("性能指标和数值均缺失")
    if not evidence:
        issues.append("缺少原文证据")
    if not source:
        issues.append("缺少来源位置")
    if not method:
        issues.append("缺少提取方式")
    if confidence is None or confidence < 0.6:
        issues.append("置信度偏低")
    return issues


def determine_review_status(
    fact: dict,
    assignment_confidence: float | None,
    issues: list[str],
) -> str:
    """Determine review_status based on assignment confidence and validation issues.

    AI MUST NOT set review_status to 'approved'. Only 'pending', 'uncertain', or 'missing'.
    """
    if any("缺失" in i for i in issues):
        return "missing"
    if assignment_confidence is not None and assignment_confidence < 0.75:
        return "uncertain"
    if len(issues) >= 2:
        return "uncertain"
    if fact.get("confidence", 0) < 0.7:
        return "uncertain"
    return "pending"


def build_extraction_report(
    paper_metadata: dict,
    sample_count: int,
    group_count: int,
    fact_count: int,
    assigned_count: int,
    unassigned_count: int,
    record_count: int,
    missing_evidence_count: int,
    uncertain_count: int,
    missing_count: int,
    pending_count: int,
    approved_count: int,
    category_counts: dict[str, int],
) -> dict:
    """Build a structured extraction quality report."""
    return {
        "文献标题": paper_metadata.get("paper_title", ""),
        "DOI": paper_metadata.get("doi_or_url", ""),
        "期刊": paper_metadata.get("journal", ""),
        "发表年份": paper_metadata.get("year", ""),
        "识别样品数": sample_count,
        "样品组数": group_count,
        "提取事实总数": fact_count,
        "成功归属数": assigned_count,
        "未归属事实数": unassigned_count,
        "生成记录数": record_count,
        "缺少证据记录数": missing_evidence_count,
        "待审核数": pending_count,
        "存疑数": uncertain_count,
        "缺失数": missing_count,
        "通过数": approved_count,
        "各性能类别记录数": category_counts,
        "推荐人工复核项": _build_review_recommendations(
            unassigned_count, missing_evidence_count, uncertain_count, missing_count
        ),
    }


def _build_review_recommendations(
    unassigned: int, missing_evidence: int, uncertain: int, missing: int,
) -> list[str]:
    """Build human-readable review recommendations."""
    recs = []
    if unassigned > 0:
        recs.append(f"有 {unassigned} 条未归属事实，建议人工检查并补充归属")
    if missing_evidence > 0:
        recs.append(f"有 {missing_evidence} 条记录缺少证据来源，建议人工补充")
    if uncertain > 0:
        recs.append(f"有 {uncertain} 条存疑记录，建议重点关注")
    if missing > 0:
        recs.append(f"有 {missing} 条缺失关键字段的记录，建议人工完善或删除")
    if not recs:
        recs.append("本次抽取质量良好，无需特殊处理")
    return recs


# ---------------------------------------------------------------------------
# V7 Extractor Service
# ---------------------------------------------------------------------------

class V7ExtractorService:
    """Multi-stage extraction service for fiber material literature."""

    # ------------------------------------------------------------------
    # Stage 1: Sample catalog
    # ------------------------------------------------------------------

    @staticmethod
    async def _stage1_sample_catalog(
        client, chunks: list[dict], paper_text_for_meta: str,
    ) -> tuple[dict, list[dict]]:
        """Generate sample catalog from document chunks."""
        catalog_text = chunks_for_sample_catalog(chunks)
        if not catalog_text.strip():
            catalog_text = paper_text_for_meta[:10000]

        parsed, _ = client.generate_json_tolerant(
            STAGE1_SAMPLE_CATALOG_PROMPT,
            catalog_text[:20000],
            max_tokens=3000,
        )
        paper_metadata = parsed.get("paper_metadata", {})
        samples = parsed.get("sample_catalog") or parsed.get("_items") or []
        if isinstance(samples, dict):
            samples = [samples]
        return paper_metadata, samples

    # ------------------------------------------------------------------
    # Stage 2: Fact candidates
    # ------------------------------------------------------------------

    @staticmethod
    async def _stage2_fact_candidates(
        client, chunks: list[dict],
    ) -> list[dict]:
        """Extract fact candidates from text, tables, and figure captions."""
        prompt = STAGE2_FACTS_PROMPT.replace(
            "{{metrics_list}}", build_metrics_prompt_text()
        ).replace(
            "{{structure_list}}", build_structure_prompt_text()
        ).replace(
            "{{process_list}}", build_process_prompt_text()
        )

        all_facts: list[dict] = []

        # Extract facts from different source types separately
        # Each source type gets its own LLM call to avoid context mixing

        # 2a: Text facts (results + experimental)
        from app.services.chunking import chunks_for_performance_extraction, chunks_for_composition_process

        perf_text = chunks_for_performance_extraction(chunks)
        if perf_text.strip():
            parsed, _ = client.generate_json_tolerant(
                prompt,
                f"Extract ALL numerical facts from the following text. Be thorough — every number with a unit is a fact.\n\n{perf_text[:25000]}",
                max_tokens=4000,
            )
            text_facts = parsed.get("facts") or parsed.get("_items") or []
            for f in text_facts:
                f["extraction_method"] = "AI_text"
            all_facts.extend(text_facts)

        # 2b: Table facts
        table_chunks = [c for c in chunks if c.get("source_type") == "table_text"]
        if table_chunks:
            table_text = "\n\n---\n\n".join(c["raw_text"] for c in table_chunks[:8])
            if table_text.strip():
                parsed, _ = client.generate_json_tolerant(
                    prompt,
                    f"Extract ALL numerical facts from these TABLES. Each cell with a number is a separate fact. Be exhaustive.\n\n{table_text[:20000]}",
                    max_tokens=3000,
                )
                table_facts = parsed.get("facts") or parsed.get("_items") or []
                for f in table_facts:
                    f["extraction_method"] = "AI_table"
                all_facts.extend(table_facts)

        # 2c: Figure caption facts
        fig_chunks = [c for c in chunks if c.get("source_type") == "figure_caption"]
        if fig_chunks:
            fig_text = "\n".join(c["raw_text"] for c in fig_chunks[:15])
            if fig_text.strip():
                parsed, _ = client.generate_json_tolerant(
                    prompt,
                    f"Extract numerical data mentioned in these figure captions. Be conservative — only extract values explicitly stated.\n\n{fig_text[:12000]}",
                    max_tokens=2000,
                )
                fig_facts = parsed.get("facts") or parsed.get("_items") or []
                for f in fig_facts:
                    f["extraction_method"] = "AI_figure"
                all_facts.extend(fig_facts)

        # Assign sequential fact_ids
        for i, f in enumerate(all_facts):
            if not f.get("fact_id"):
                f["fact_id"] = f"F{i + 1:04d}"

        return all_facts

    # ------------------------------------------------------------------
    # Stage 3: Sample assignment
    # ------------------------------------------------------------------

    @staticmethod
    async def _stage3_sample_assignment(
        client, samples: list[dict], facts: list[dict],
    ) -> list[dict]:
        """Assign facts to catalog samples using LLM matching."""
        if not facts:
            return []

        # Build compact sample catalog for prompt
        compact_catalog = []
        for s in samples:
            compact_catalog.append({
                "sample_id": s.get("sample_id", ""),
                "aliases": s.get("sample_aliases", []) if isinstance(s.get("sample_aliases"), list) else [],
                "group_id": s.get("sample_group_id", ""),
                "material_system": s.get("material_system", ""),
            })
        catalog_json = json.dumps(compact_catalog, ensure_ascii=False, indent=2)

        # Assign in batches to stay within token limits
        batch_size = 30
        all_assignments: list[dict] = []

        for batch_start in range(0, len(facts), batch_size):
            batch = facts[batch_start:batch_start + batch_size]
            compact_facts = []
            for f in batch:
                compact_facts.append({
                    "fact_id": f.get("fact_id", ""),
                    "fact_type": f.get("fact_type", ""),
                    "metric_or_parameter": f.get("metric_or_parameter", ""),
                    "value": f.get("value", ""),
                    "unit": f.get("unit", ""),
                    "subject_text": (f.get("subject_text", "") or "")[:200],
                    "candidate_sample_ids": f.get("candidate_sample_ids", []),
                    "evidence_text": (f.get("evidence_text", "") or "")[:200],
                    "source_location": f.get("source_location", ""),
                })

            prompt = STAGE3_ASSIGNMENT_PROMPT.replace(
                "{{sample_catalog_json}}", catalog_json
            )
            parsed, _ = client.generate_json_tolerant(
                prompt,
                f"Assign these facts to samples:\n{json.dumps(compact_facts, ensure_ascii=False, indent=2)}",
                max_tokens=3000,
            )
            batch_assignments = parsed.get("assignments") or parsed.get("_items") or []
            all_assignments.extend(batch_assignments)

        # Build assignment lookup by fact_id
        assignment_map: dict[str, dict] = {}
        for a in all_assignments:
            fid = a.get("fact_id", "")
            if fid:
                assignment_map[fid] = a

        # Merge assignments back into facts
        for f in facts:
            fid = f.get("fact_id", "")
            assignment = assignment_map.get(fid, {})
            f["assigned_sample_id"] = assignment.get("assigned_sample_id")
            f["assignment_confidence"] = assignment.get("assignment_confidence")
            f["assignment_status"] = assignment.get("assignment_status", "unassigned")

        return facts

    # ------------------------------------------------------------------
    # Stage 3b: Local fallback assignment for facts missed by LLM
    # ------------------------------------------------------------------

    @staticmethod
    def _local_sample_assignment(facts: list[dict], samples: list[dict]) -> list[dict]:
        """Local text-matching fallback for sample assignment.

        Matches sample_id and aliases against fact evidence_text and subject_text.
        """
        # Build lookup: normalized name → sample
        sample_lookup: dict[str, dict] = {}
        for s in samples:
            sid = s.get("sample_id", "").strip()
            if sid:
                sample_lookup[sid.lower()] = s
                for alias in (s.get("sample_aliases") or []):
                    alias = alias.strip()
                    if alias:
                        sample_lookup[alias.lower()] = s

        for f in facts:
            if f.get("assignment_status") not in ("unassigned", None, ""):
                continue
            if f.get("assigned_sample_id"):
                continue

            # Search evidence_text and subject_text for sample mentions
            search_text = (
                (f.get("evidence_text") or "") + " " +
                (f.get("subject_text") or "") + " " +
                (f.get("source_location") or "")
            ).lower()

            # Also check candidate_sample_ids from the fact
            candidates = f.get("candidate_sample_ids") or []
            if isinstance(candidates, str):
                try:
                    candidates = json.loads(candidates)
                except (json.JSONDecodeError, TypeError):
                    candidates = [candidates]

            best_match = None
            best_score = 0

            for sid_lower, sample in sample_lookup.items():
                score = 0
                if sid_lower in search_text:
                    score += 3  # Exact match in text
                # Check if any part of the sample ID appears
                parts = re.split(r"[-_/\s]+", sid_lower)
                for part in parts:
                    if len(part) >= 3 and part in search_text:
                        score += 1

                if score > best_score:
                    best_score = score
                    best_match = sample

            if best_match and best_score >= 3:
                f["assigned_sample_id"] = best_match.get("sample_id")
                f["assignment_confidence"] = min(0.7 + (best_score - 3) * 0.05, 0.9)
                f["assignment_status"] = "assigned"

        return facts

    # ------------------------------------------------------------------
    # Stage 4: Record generation
    # ------------------------------------------------------------------

    @staticmethod
    def _stage4_generate_records(
        paper_id: int,
        project_id: int,
        paper_metadata: dict,
        samples: list[dict],
        facts: list[dict],
    ) -> tuple[list[dict], dict]:
        """Generate 40-column candidate records from assigned facts.

        Returns (records, report_data).
        """
        # Build sample info lookup
        sample_info: dict[str, dict] = {}
        group_ids: set[str] = set()
        for s in samples:
            sid = s.get("sample_id", "").strip()
            if sid:
                sample_info[sid] = s
                group_ids.add(s.get("sample_group_id", "Group-A"))

        records: list[dict] = []
        record_idx = 0
        missing_evidence_count = 0

        # Process performance facts → each becomes a record
        perf_facts = [f for f in facts
                      if f.get("fact_type") == "performance"
                      and f.get("assignment_status") in ("assigned", "uncertain")]

        for f in perf_facts:
            sample_id = f.get("assigned_sample_id") or ""
            s = sample_info.get(sample_id, {})

            record_idx += 1
            metric_raw = f.get("metric_or_parameter", "")
            metric = find_metric_canonical(metric_raw) or metric_raw
            category = f.get("category") or find_category_for_metric(metric)
            evidence = f.get("evidence_text", "") or ""
            perf_evidence = evidence  # same field for performance
            source = f.get("source_location", "") or ""
            extraction_method = f.get("extraction_method", "AI_text")

            # QC checks
            validation_issues = validate_fact(f)
            review_status = determine_review_status(
                f, f.get("assignment_confidence"), validation_issues
            )

            if not evidence and not perf_evidence:
                missing_evidence_count += 1

            # Build composition expression from sample info
            comp_expr = s.get("composition_expression", "")
            if not comp_expr:
                # Infer from material_system
                comp_expr = s.get("material_system", "")

            record = {
                "project_id": project_id,
                "source_paper_id": paper_id,
                "record_id": f"V7-EXT-{paper_id}-{record_idx}",
                "paper_title": paper_metadata.get("paper_title", ""),
                "doi_or_url": paper_metadata.get("doi_or_url", ""),
                "year": str(paper_metadata.get("year", "")),
                "journal": paper_metadata.get("journal", ""),
                "sample_group_id": s.get("sample_group_id", "Unassigned"),
                "sample_id": sample_id,
                "material_system": s.get("material_system", ""),
                "fiber_type": s.get("fiber_type", ""),
                "variable_name": s.get("variable_name", ""),
                "variable_value": s.get("variable_value", ""),
                "variable_unit": s.get("variable_unit", ""),
                "composition_expression": comp_expr,
                "matrix_name": "",
                "matrix_content": "",
                "matrix_unit": "",
                "additive_expression": "",
                "solvent_or_aid": "",
                "process_route": s.get("process_route", ""),
                "spinning_method": "",
                "process_parameters": "",
                "post_treatment": "",
                "structure_methods": "",
                "structure_features": "",
                "performance_category": category,
                "performance_metric": metric,
                "performance_value": f.get("value", ""),
                "performance_unit": f.get("unit", ""),
                "performance_method": f.get("method") or "",
                "performance_condition": f.get("condition") or "",
                "performance_evidence": perf_evidence,
                "extraction_method": extraction_method,
                "evidence_text": evidence,
                "ai_confidence": f.get("confidence", 0.5),
                "review_status": review_status,
                "source_location": source,
                "_fact_id": f.get("fact_id", ""),
                "_validation_issues": validation_issues,
            }
            records.append(record)

        # Count statuses
        status_counts = defaultdict(int)
        for r in records:
            status_counts[r["review_status"]] += 1

        category_counts: dict[str, int] = defaultdict(int)
        for r in records:
            category_counts[r["performance_category"]] += 1

        report_data = {
            "sample_count": len(samples),
            "group_count": len(group_ids),
            "fact_count": len(facts),
            "assigned_count": len(perf_facts),
            "unassigned_count": len([f for f in facts if f.get("assignment_status") == "unassigned"]),
            "record_count": len(records),
            "missing_evidence_count": missing_evidence_count,
            "pending_count": status_counts.get("pending", 0),
            "uncertain_count": status_counts.get("uncertain", 0),
            "missing_count": status_counts.get("missing", 0),
            "approved_count": status_counts.get("approved", 0),
            "category_counts": dict(category_counts),
            "unassigned_facts": [
                {
                    "fact_id": f.get("fact_id"),
                    "fact_type": f.get("fact_type"),
                    "metric_or_parameter": f.get("metric_or_parameter"),
                    "value": f.get("value"),
                    "unit": f.get("unit"),
                    "evidence_text": (f.get("evidence_text") or "")[:200],
                }
                for f in facts if f.get("assignment_status") == "unassigned"
            ],
        }
        return records, report_data

    # ------------------------------------------------------------------
    # Stage 5: Vision enhancement (optional)
    # ------------------------------------------------------------------

    @staticmethod
    async def _stage5_vision_enhancement(
        client, pdf_path: str, chunks: list[dict], facts: list[dict],
    ) -> list[dict]:
        """Vision-based extraction from figure-heavy pages for missed data."""
        # Only trigger if we have few performance facts
        perf_count = sum(1 for f in facts if f.get("fact_type") == "performance")
        if perf_count >= 10:
            return facts

        # Find pages with figures
        fig_pages = list(set(
            c["page_number"] for c in chunks
            if c.get("source_type") == "figure_caption" or c.get("has_figure_image")
        ))
        if not fig_pages:
            # Use later pages (results section typically)
            result_pages = list(set(
                c["page_number"] for c in chunks
                if c.get("section_name") == "results"
            ))
            fig_pages = result_pages[:6] if result_pages else list(range(4, 12))

        try:
            rendered = render_pdf_pages(pdf_path, fig_pages[:6])
            if not rendered:
                return facts

            parsed, _ = client.generate_vision_json_tolerant(
                "You are analyzing fiber material literature figures and tables. "
                "Extract ALL performance data (tensile strength, modulus, elongation, "
                "thermal conductivity, contact angle, density, LOI, dielectric constant, "
                "EMI SE, piezoelectric coefficient, etc.) visible in the images. "
                "For each value: identify the sample name (from axis labels, legends, or captions), "
                "metric name, numerical value, and unit. "
                "Output JSON: {'vision_facts': [{'sample_id': '...', 'metric_or_parameter': '...', "
                "'value': '...', 'unit': '...', 'source_location': 'figure'}, ...]}",
                "Find ALL performance data visible in these figures and tables. "
                "Look for bar charts, stress-strain curves, TGA curves, and data tables.",
                [r["image"] for r in rendered],
                max_tokens=3000,
            )
            vision_facts = parsed.get("vision_facts") or parsed.get("_items") or []
            next_id = len(facts) + 1
            for vf in vision_facts:
                sid = vf.get("sample_id", "").strip()
                metric = vf.get("metric_or_parameter", "").strip()
                val = vf.get("value", "").strip()
                if sid and metric and val:
                    facts.append({
                        "fact_id": f"FV{next_id:04d}",
                        "fact_type": "performance",
                        "subject_text": f"{sid} {metric}",
                        "candidate_sample_ids": [sid],
                        "metric_or_parameter": metric,
                        "value": val,
                        "unit": vf.get("unit", ""),
                        "method": "",
                        "condition": "",
                        "category": find_category_for_metric(metric),
                        "evidence_text": f"Vision-extracted from figure: {metric}={val} {vf.get('unit', '')}",
                        "source_location": vf.get("source_location", "figure"),
                        "extraction_method": "AI_figure",
                        "confidence": 0.65,
                    })
                    next_id += 1
        except Exception as e:
            print(f"Warning: Vision enhancement failed: {e}")

        return facts

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    @staticmethod
    async def run_full_pipeline_for_paper(
        db: AsyncSession, paper_id: int,
        progress_callback: Callable[[str, int], Any] | None = None,
    ) -> dict[str, Any]:
        """Run the V7 multi-stage extraction pipeline."""
        def _emit(step: str, pct: int):
            if progress_callback:
                progress_callback(step, pct)

        # -- Load paper and project --
        res = await db.execute(select(Paper).where(Paper.id == paper_id))
        paper = res.scalar_one_or_none()
        if not paper:
            return {"error": "Paper not found"}

        proj_res = await db.execute(select(Project).where(Project.id == paper.project_id))
        project = proj_res.scalar_one_or_none()
        if not project:
            return {"error": "Project not found"}

        from app.core.config import settings

        pdf_path = os.path.join(settings.UPLOAD_DIR, paper.file_object_key)
        if not pdf_path or not os.path.exists(pdf_path):
            return {"error": f"PDF file not found: {paper.file_object_key}"}

        # -- Clean up old extraction data --
        await db.execute(sa_delete(EvidenceItem).where(EvidenceItem.paper_id == paper_id))
        await db.execute(sa_delete(CandidateRecord).where(CandidateRecord.source_paper_id == paper_id))
        await db.execute(sa_delete(PageInventory).where(PageInventory.paper_id == paper_id))
        await db.execute(sa_delete(SampleCatalog).where(SampleCatalog.paper_id == paper_id))
        await db.execute(sa_delete(FactCandidate).where(FactCandidate.paper_id == paper_id))
        await db.commit()

        # -- Stage 0: PDF parse + chunking --
        _emit("inventory", 5)
        tables = extract_pdf_tables_markdown(pdf_path)
        raw_text = extract_pdf_text(pdf_path)
        if not raw_text.strip():
            return {"error": "PDF 未提取到可用文本"}

        # Parse pages
        matches = list(re.finditer(r"(?m)^\[page\s+(\d+)\]\s*$", raw_text))
        if not matches:
            pages = [(1, raw_text)]
        else:
            pages = []
            for idx, match in enumerate(matches):
                start = match.end()
                end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw_text)
                pages.append((int(match.group(1)), raw_text[start:end].strip()))

        chunks = chunk_pdf_text(pages, tables)

        # Save page inventory
        await db.execute(
            update(Paper).where(Paper.id == paper_id).values(
                status="extracting", page_count=len(pages)
            )
        )
        for p_num, p_text in pages:
            db.add(PageInventory(
                paper_id=paper_id, page_number=p_num,
                text_length=len(p_text),
                has_table_signal=bool(
                    [c for c in chunks if c["page_number"] == p_num and c["source_type"] == "table_text"]
                ),
                has_figure_caption=bool(
                    [c for c in chunks if c["page_number"] == p_num and c["source_type"] == "figure_caption"]
                ),
                has_experimental_signal=bool(
                    [c for c in chunks if c["page_number"] == p_num and c["section_name"] == "experimental"]
                ),
                importance_score=1.0,
                summary=json.dumps([c["section_name"] for c in chunks if c["page_number"] == p_num][:1] or ["general"], ensure_ascii=False),
            ))
        await db.commit()

        # -- Check LLM availability --
        has_llm = bool(project.llm_api_key and project.llm_api_key.strip())
        if not has_llm:
            paper.status = "failed"
            db.add(paper)
            await db.commit()
            return {"error": "未配置 LLM API Key，无法执行 AI 抽取"}

        client = None
        try:
            client = create_llm_client(
                provider=project.llm_provider or "openai",
                api_key=project.llm_api_key,
                model=project.llm_model or "gpt-4o",
                base_url=project.llm_base_url or "https://api.openai.com/v1",
            )
        except Exception as e:
            paper.status = "failed"
            db.add(paper)
            await db.commit()
            return {"error": f"LLM 客户端创建失败: {e}"}

        if not client:
            paper.status = "failed"
            db.add(paper)
            await db.commit()
            return {"error": "LLM 客户端不可用"}

        _emit("extracting", 15)

        # -- Stage 1: Sample catalog --
        paper_metadata, samples = await V7ExtractorService._stage1_sample_catalog(
            client, chunks, raw_text[:10000]
        )
        _emit("extracting", 30)

        # Deduplicate samples by sample_id
        seen_sids: set[str] = set()
        unique_samples = []
        for s in samples:
            sid = s.get("sample_id", "").strip()
            if sid and sid not in seen_sids:
                seen_sids.add(sid)
                unique_samples.append(s)
        samples = unique_samples

        # Save sample catalog to DB
        for s in samples:
            aliases = s.get("sample_aliases", [])
            if isinstance(aliases, list):
                aliases_str = json.dumps(aliases, ensure_ascii=False)
            else:
                aliases_str = str(aliases) if aliases else None
            db.add(SampleCatalog(
                paper_id=paper_id,
                project_id=paper.project_id,
                sample_id=s.get("sample_id", ""),
                sample_aliases=aliases_str,
                sample_group_id=s.get("sample_group_id", "Group-A"),
                material_system=s.get("material_system", ""),
                fiber_type=s.get("fiber_type", ""),
                variable_name=s.get("variable_name", ""),
                variable_value=s.get("variable_value", ""),
                variable_unit=s.get("variable_unit", ""),
                composition_expression=s.get("composition_expression", ""),
                process_route=s.get("process_route", ""),
                source_location=s.get("source_location", ""),
                evidence_text=s.get("evidence_text", ""),
                confidence=float(s.get("confidence", 0.5)),
            ))
        await db.commit()

        # -- Stage 2: Fact candidates --
        facts = await V7ExtractorService._stage2_fact_candidates(client, chunks)
        _emit("extracting", 50)

        # -- Stage 3: Sample assignment --
        if samples:
            facts = await V7ExtractorService._stage3_sample_assignment(client, samples, facts)
            # Local fallback for facts missed by LLM assignment
            facts = V7ExtractorService._local_sample_assignment(facts, samples)
        _emit("extracting", 65)

        # -- Stage 5: Vision enhancement --
        try:
            facts = await V7ExtractorService._stage5_vision_enhancement(
                client, pdf_path, chunks, facts
            )
        except Exception as e:
            print(f"Warning: Vision enhancement stage failed: {e}")

        _emit("extracting", 75)

        # -- Save fact candidates to DB --
        for f in facts:
            candidate_ids = f.get("candidate_sample_ids", [])
            if isinstance(candidate_ids, list):
                candidate_ids_str = json.dumps(candidate_ids, ensure_ascii=False)
            else:
                candidate_ids_str = str(candidate_ids) if candidate_ids else None

            db.add(FactCandidate(
                paper_id=paper_id,
                project_id=paper.project_id,
                fact_id=f.get("fact_id", ""),
                fact_type=f.get("fact_type", "performance"),
                subject_text=f.get("subject_text", ""),
                candidate_sample_ids=candidate_ids_str,
                metric_or_parameter=f.get("metric_or_parameter", ""),
                value=f.get("value", ""),
                unit=f.get("unit", ""),
                method=f.get("method", ""),
                condition=f.get("condition", ""),
                category=f.get("category", ""),
                evidence_text=f.get("evidence_text", ""),
                source_location=f.get("source_location", ""),
                extraction_method=f.get("extraction_method", "AI_text"),
                confidence=float(f.get("confidence", 0.5)),
                assigned_sample_id=f.get("assigned_sample_id"),
                assignment_confidence=f.get("assignment_confidence"),
                assignment_status=f.get("assignment_status", "unassigned"),
            ))
        await db.commit()

        # -- Stage 4: Record generation --
        records, report_data = V7ExtractorService._stage4_generate_records(
            paper_id, paper.project_id, paper_metadata, samples, facts
        )
        _emit("saving", 85)

        # -- Save candidate records --
        saved_count = 0
        for r in records:
            validation_issues = r.pop("_validation_issues", [])
            fact_id = r.pop("_fact_id", "")

            rec = CandidateRecord(
                project_id=r["project_id"],
                source_paper_id=r["source_paper_id"],
                record_id=r["record_id"],
                paper_title=r["paper_title"],
                doi_or_url=r["doi_or_url"],
                year=r["year"],
                journal=r["journal"],
                sample_group_id=r["sample_group_id"],
                sample_id=r["sample_id"],
                material_system=r["material_system"],
                fiber_type=r.get("fiber_type", ""),
                variable_name=r.get("variable_name", ""),
                variable_value=r.get("variable_value", ""),
                variable_unit=r.get("variable_unit", ""),
                composition_expression=r["composition_expression"],
                matrix_name=r.get("matrix_name", ""),
                matrix_content=r.get("matrix_content", ""),
                matrix_unit=r.get("matrix_unit", ""),
                additive_expression=r.get("additive_expression", ""),
                solvent_or_aid=r.get("solvent_or_aid", ""),
                process_route=r["process_route"],
                spinning_method=r.get("spinning_method", ""),
                process_parameters=r.get("process_parameters", ""),
                post_treatment=r.get("post_treatment", ""),
                structure_methods=r.get("structure_methods", ""),
                structure_features=r.get("structure_features", ""),
                performance_category=r["performance_category"],
                performance_metric=r["performance_metric"],
                performance_value=r["performance_value"],
                performance_unit=r["performance_unit"],
                performance_method=r.get("performance_method", ""),
                performance_condition=r.get("performance_condition", ""),
                performance_evidence=r.get("performance_evidence", ""),
                extraction_method=r.get("extraction_method", ""),
                evidence_text=r.get("evidence_text", ""),
                ai_confidence=r.get("ai_confidence", 0.5),
                review_status=r["review_status"],
                source_location=r.get("source_location", ""),
                reviewer_comment=(
                    "; ".join(validation_issues) if validation_issues
                    else ""
                ),
            )
            db.add(rec)
            await db.flush()

            # Save evidence item linking back to the fact
            db.add(EvidenceItem(
                project_id=r["project_id"],
                paper_id=r["source_paper_id"],
                candidate_record_id=rec.id,
                source_type=f"fact_{fact_id}" if fact_id else "unknown",
                source_location=r.get("source_location", ""),
                evidence_text=r.get("evidence_text", "")[:2000],
                normalized_payload=json.dumps({
                    "fact_id": fact_id,
                    "metric": r["performance_metric"],
                    "value": r["performance_value"],
                    "unit": r["performance_unit"],
                }, ensure_ascii=False),
                confidence=float(r.get("ai_confidence", 0.5)),
            ))
            saved_count += 1

        _emit("saving", 92)

        # -- Update paper metadata --
        paper.paper_title = paper_metadata.get("paper_title", paper.original_filename)
        paper.doi_or_url = paper_metadata.get("doi_or_url", "")
        try:
            paper.year = int(paper_metadata.get("year", 2025))
        except (ValueError, TypeError):
            paper.year = None
        paper.journal = paper_metadata.get("journal", "")

        # -- Build and save extraction report --
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
        )

        # Store report as JSON in a paper field or separate table
        # Using paper's existing fields; store summary in a note field or just return it
        # For now, serialize to a JSON file alongside the paper
        report_path = os.path.join(
            settings.UPLOAD_DIR, str(paper.project_id),
            f"report_{paper_id}.json"
        )
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as rf:
            json.dump(extraction_report, rf, ensure_ascii=False, indent=2)

        paper.status = "review"
        db.add(paper)
        await db.commit()

        _emit("completed", 100)

        return {
            "success": True,
            "pages_processed": len(pages),
            "table_count": len(tables),
            "chunk_count": len(chunks),
            "sample_count": report_data["sample_count"],
            "fact_count": report_data["fact_count"],
            "assigned_count": report_data["assigned_count"],
            "unassigned_count": report_data["unassigned_count"],
            "candidates_created": saved_count,
            "extraction_report": extraction_report,
        }
