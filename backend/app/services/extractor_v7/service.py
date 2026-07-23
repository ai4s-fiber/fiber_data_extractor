"""
V7 Multi-Stage Extraction Pipeline for Fiber Material Literature.

Stage 0: PDF parsing & chunking (source-aware)
Stage 1: Atomic sample mentions and variable candidates
Stage 2: Atomic fact candidate extraction
Stage 3: Deterministic sample grouping and fact assignment
Stage 4: Sample-card synthesis and 40-column record generation
Stage 5: Risk-oriented quality report
Stage 6: Persistence to database

The pipeline is generic — no paper-specific rules or hardcoded sample names.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import os
import re
import inspect
import tempfile
from collections import Counter, defaultdict
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

from app.core.config import settings
from app.services.llm_client import create_llm_client
from app.services.llm_budget import clamp_max_tokens
from app.services.llm_concurrency import llm_call_slot, per_job_llm_parallel_limit
from app.services.document_context import parse_pdf_to_document_context
from app.services.document_type import (
    classify_document_type,
    is_plausible_paper_title,
)
from app.services.extraction_results import (
    purge_extraction_results,
    restore_paper_status_after_interruption,
)
from app.services.pdf_utils import render_pdf_pages
from app.services.grouping import (
    assign_fact_to_sample,
    build_sample_cards,
    fill_sample_card_variables,
    group_samples,
    infer_variable_from_sample_id,
    is_material_sample_id,
    normalize_for_match,
    normalize_sample_id,
)
from app.services.metrics_dictionary import (
    build_metrics_prompt_text,
    build_structure_prompt_text,
    build_process_prompt_text,
    find_metric_canonical,
    find_category_for_metric,
    classify_metric_priority,
    find_structure_feature_canonical,
    find_process_parameter_canonical,
    is_condition_parameter_name,
)

from app.services.extractor_v7.prompts import (
    SAMPLE_MENTIONS_PROMPT,
    VARIABLE_CANDIDATES_PROMPT,
    STAGE2_FACTS_PROMPT,
    STAGE2_PERFORMANCE_REPAIR_PROMPT,
    WEAK_FACTS_PROMPT,
    STAGE3_ASSIGNMENT_PROMPT,
)
from app.services.extractor_v7.exceptions import ExtractionCancelled
from app.services.extractor_v7.reporting import build_extraction_report
from app.services.extractor_v7.fact_postprocess import (
    is_placeholder_performance_value,
    merge_adjacent_table_chunks,
    postprocess_extracted_facts,
    renumber_fact_ids,
    sanitize_assigned_sample_ids,
)
from app.services.extractor_v7.metric_normalize import (
    merge_duplicate_facts,
    normalize_metrics_in_facts,
)
from app.services.extractor_v7.deterministic_results import (
    recover_explicit_contrast_result_facts,
    recover_explicit_frequency_range_facts,
)
from app.services.extractor_v7.output_postprocess import (
    apply_pre_output_validation,
    format_characterization_entry,
    infer_characterization_method,
    merge_characterization_features,
)
from app.services.extractor_v7.sample_value_alignment import (
    apply_sample_value_alignment,
    extract_explicit_sample_names,
)
from app.services.extractor_v7.holistic_extract import (
    TABLE_PERFORMANCE_PROMPT,
    _response_rows,
    catalog_to_mentions,
    enrich_sample_cards as enrich_sample_cards_holistic,
    merge_holistic_and_atomic_facts,
    reconcile_holistic_table_duplicates,
    run_holistic_extraction,
    table_rows_to_facts,
)
from app.services.extractor_v7.sample_identity import (
    is_numbered_sample_variant,
    merge_sample_identities,
    parse_sample_aliases,
    repair_contextual_fact_assignments,
)
from app.services.extractor_v7.validators import (
    determine_review_status,
    is_background_or_reference_fact,
    is_rough_source_location,
    text_has_background_reference_signal,
    validate_fact,
    _looks_like_affiliation_or_address,
    _looks_like_journal_name,
)
from app.services.validation import is_characterization_peak_metric

_current_job_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "extraction_job_id", default=None
)


def _write_json_atomic(path: str, payload: dict[str, Any]) -> None:
    """Replace a JSON report without exposing a partially written file."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    temporary_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{os.path.basename(path)}.",
            suffix=".tmp",
            dir=directory,
            delete=False,
        ) as report_file:
            temporary_path = report_file.name
            json.dump(payload, report_file, ensure_ascii=False, indent=2)
            report_file.flush()
            os.fsync(report_file.fileno())
        os.replace(temporary_path, path)
        temporary_path = ""
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)

_NUMBERED_SAMPLE_SUFFIX_RE = re.compile(
    r"(?i)(?:\b(?P<label>sample|specimen|run|no)\s*[-#:]?\s*(?P<label_num>\d+(?:\.\d+)?)\s*$|"
    r"(?:^|[\s_/-])(?P<bare_num>\d+(?:\.\d+)?)\s*$)"
)


def _numbered_sample_is_explicit(
    sample_id: str | None,
    search_text: str,
    candidates: list[Any] | None = None,
) -> bool:
    """Keep numbered variants out of fuzzy matching unless the number is explicit."""
    if not is_numbered_sample_variant(sample_id):
        return True
    normalized_id = normalize_for_match(sample_id)
    normalized_candidates = [normalize_for_match(str(value)) for value in candidates or [] if value]
    combined = " ".join([normalize_for_match(search_text), *normalized_candidates])
    if normalized_id and re.search(
        rf"(?<![a-z0-9]){re.escape(normalized_id)}(?![a-z0-9])", combined
    ):
        return True
    suffix = _NUMBERED_SAMPLE_SUFFIX_RE.search(normalized_id)
    if not suffix or not suffix.group("label"):
        return False
    label = suffix.group("label")
    number = suffix.group("label_num")
    return bool(re.search(
        rf"(?<![a-z0-9]){re.escape(label)}\s*[-#:]?\s*{re.escape(number)}(?![0-9])",
        combined,
    ))



class V7ExtractorService:
    """Multi-stage extraction service for fiber material literature."""

    FINAL_RECORD_FIELDS = [
        "record_id", "paper_id_str", "paper_title", "doi_or_url", "year", "journal",
        "sample_group_id", "sample_id", "material_system", "fiber_type",
        "variable_name", "variable_value", "variable_unit",
        "composition_expression", "matrix_name", "matrix_content", "matrix_unit",
        "additive_expression", "solvent_or_aid", "composition_evidence",
        "process_route", "spinning_method", "process_parameters", "post_treatment",
        "process_evidence", "structure_methods", "structure_features",
        "characterization_features",
        "structure_evidence", "performance_category", "performance_metric",
        "performance_value", "performance_unit", "performance_method",
        "performance_condition", "performance_evidence", "extraction_method",
        "evidence_text", "ai_confidence", "review_status", "reviewer_comment",
    ]

    SAMPLE_CARD_FIELDS = [
        "sample_id", "sample_aliases", "sample_group_id", "material_system",
        "fiber_type", "variable_name", "variable_value", "variable_unit",
        "composition_expression", "matrix_name", "matrix_content", "matrix_unit",
        "additive_expression", "solvent_or_aid", "composition_evidence",
        "process_route", "spinning_method", "process_parameters", "post_treatment",
        "process_evidence", "structure_methods", "structure_features",
        "characterization_features",
        "structure_evidence", "source_location", "evidence_text", "confidence",
    ]

    # ------------------------------------------------------------------
    # Stage 1: Atomic sample mentions and variables
    # ------------------------------------------------------------------

    @staticmethod
    def _chunk_source_location(chunk: dict) -> str:
        page = chunk.get("page_number", "")
        source_type = chunk.get("source_type", "text")
        text = chunk.get("raw_text", "") or ""
        source = chunk.get("table_source") or ""
        block_id = chunk.get("source_block_id")
        fig = re.search(r"(?i)\b(?:fig\.?|figure)\s*(?P<label>[0-9]+[a-z]?)", text[:300])
        table = re.search(r"(?i)\btable\s*(?P<label>[0-9]+[a-z]?)", text[:300])
        section = chunk.get("section_name", "")
        if source:
            return source
        if fig:
            return f"p.{page}, Fig. {fig.group('label')}"
        if table:
            return f"p.{page}, Table {table.group('label')}"
        if section:
            base = f"p.{page}, {section} section"
            return f"{base}, block {block_id}" if block_id else base
        if block_id:
            return f"p.{page}, {source_type}, block {block_id}"
        return f"p.{page}, {source_type}"

    @staticmethod
    def _chunk_for_prompt(chunk: dict, limit: int = 5000) -> str:
        source = V7ExtractorService._chunk_source_location(chunk)
        source_type = chunk.get("source_type", "text")
        section = chunk.get("section_name", "")
        block_id = chunk.get("source_block_id", "")
        bbox = chunk.get("source_bbox") or ""
        return (
            f"[source_location: {source}]\n"
            f"[block_id: {block_id}]\n"
            f"[bbox: {bbox}]\n"
            f"[source_type: {source_type}]\n"
            f"[section: {section}]\n"
            f"{(chunk.get('raw_text') or '')[:limit]}"
        )

    @staticmethod
    def _priority_chunks(chunks: list[dict]) -> list[dict]:
        priority = []
        for chunk in chunks:
            source_type = chunk.get("source_type")
            section = chunk.get("section_name")
            text = chunk.get("raw_text") or ""
            if source_type in {"table_text", "figure_caption"}:
                priority.append(chunk)
            elif section in {"title_abstract", "experimental"}:
                priority.append(chunk)
            elif section == "results":
                # Results 章节块很多；只保留含数据线索的段落，避免 Stage1 爆炸。
                if (
                    len(text) >= 150
                    or re.search(r"\d", text)
                    or re.search(
                        r"(?i)\b(MPa|GPa|kPa|mN|%|pC/N|μm|mm|°C|W/m|cycles?|strength|modulus|conductivity)\b",
                        text,
                    )
                ):
                    priority.append(chunk)
            elif re.search(
                r"(?i)\b(sample|specimen|fiber|aerogel|film|composite)\b",
                text[:2000],
            ):
                priority.append(chunk)
        return priority or chunks

    @staticmethod
    def _stage1_chunks(chunks: list[dict], model_mode: str) -> list[dict]:
        """Priority chunks for Stage1, capped and sorted for throughput."""
        priority = V7ExtractorService._priority_chunks(chunks)
        priority.sort(key=V7ExtractorService._fact_chunk_priority)
        cap = (
            settings.WEAK_MAX_PRIORITY_CHUNKS
            if model_mode == "weak"
            else settings.STRONG_MAX_PRIORITY_CHUNKS
        )
        return V7ExtractorService._cap_chunks(priority, cap)

    @staticmethod
    def _stage1_batches(chunks: list[dict], model_mode: str) -> list[list[dict]]:
        batch_size = (
            6 if model_mode == "weak" else settings.STRONG_STAGE1_BATCH_SIZE
        )
        return V7ExtractorService._batch_chunks_for_prompt(
            chunks,
            max_chars=16000 if model_mode == "strong" else 14000,
            max_chunks_per_batch=batch_size,
        )

    @staticmethod
    def _cap_chunks(chunks: list[dict], max_n: int) -> list[dict]:
        if max_n <= 0 or len(chunks) <= max_n:
            return chunks
        return chunks[:max_n]

    @staticmethod
    def _batch_chunks_for_prompt(
        chunks: list[dict],
        *,
        max_chars: int = 14000,
        max_chunks_per_batch: int = 6,
    ) -> list[list[dict]]:
        batches: list[list[dict]] = []
        current: list[dict] = []
        current_len = 0
        for chunk in chunks:
            chunk_len = len(chunk.get("raw_text") or "") + 500
            if current and (
                current_len + chunk_len > max_chars
                or len(current) >= max_chunks_per_batch
            ):
                batches.append(current)
                current = []
                current_len = 0
            current.append(chunk)
            current_len += chunk_len
        if current:
            batches.append(current)
        return batches

    @staticmethod
    def _stage2_signal_score(chunk: dict) -> int:
        """Rank chunks by local evidence density before spending LLM calls."""
        source_type = chunk.get("source_type")
        section = (chunk.get("section_name") or "").lower()
        text = (chunk.get("raw_text") or "")[:5000]
        lower = text.lower()
        score = 0
        if source_type == "table_text":
            score += 12
        elif source_type == "figure_caption":
            score += 8
        if section == "results":
            score += 6
        elif section == "experimental":
            score += 5
        elif section == "conclusion":
            score += 2
        score += min(5, len(re.findall(r"\d", text)) // 10)
        if re.search(r"(?i)\b(MPa|GPa|kPa|pC/N|S/cm|W/m|mAh|wt%|vol%|nm|μm|um|°C|cycles?)\b", text):
            score += 4
        if re.search(
            r"(?i)\b(tensile|strength|modulus|strain|conductivity|dielectric|"
            r"thermal|porosity|diameter|roughness|load|force|displacement|"
            r"band\s*gap|eigenfrequency|transmission|acceleration|damping|"
            r"energy\s+absorption|pH|contact\s+angle|wettability)\b",
            lower,
        ):
            score += 3
        if re.search(r"(?i)\b(electrospin|spinning|anneal|curing|pyrolysis|calcination|solvent|concentration|flow rate|voltage)\b", lower):
            score += 3
        if re.search(r"(?i)\b(sem|tem|xrd|ftir|raman|xps|crystallinity|morpholog|fiber diameter|pore)\b", lower):
            score += 3
        if re.search(r"(?i)\b(PVDF|PAN|PCL|PLA|PVA|PEO|PI|PAA|CNT|CNC|GO|MXene|nanofiber|aerogel|film|composite)\b", text):
            score += 2
        return score

    @staticmethod
    def _has_quantitative_result_signal(chunk: dict) -> bool:
        text = str(chunk.get("raw_text") or "")
        has_value_unit = bool(re.search(
            r"(?i)[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\s*"
            r"(?:%|GPa|MPa|kPa|Pa|kN|mN|N|pC/N|S/cm|S/m|W/mK|mW/mK|"
            r"kHz|MHz|Hz|g/g|mg/g|kg/m3|g/cm3|J/g|kJ/m2|mJ|J|"
            r"nm|[µμu]m|mm|cm|m/s2|m/s|cm[⁻^-]?1|eV|°C|K|V|mV|[µμ]A|nA)"
            r"(?![A-Za-z0-9])",
            text,
        ))
        has_ph_result = bool(re.search(
            r"(?i)\bpH\b.{0,100}\b(?:increase\w*|decrease\w*|reach\w*|"
            r"rose|fell|was|were|value\w*)\b.{0,80}?\d+(?:\.\d+)?",
            text,
        ))
        return has_value_unit or has_ph_result

    @staticmethod
    def _has_intrinsic_material_property_signal(chunk: dict) -> bool:
        """Keep compact experimental blocks that define constituent properties."""
        if chunk.get("section_name") != "experimental":
            return False
        text = str(chunk.get("raw_text") or "")
        number = r"(?<![\d.])[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\d.])"
        explicit_property_patterns = (
            rf"(?i)\bdensity\b.{{0,60}}?{number}\s*(?:kg|g)\b",
            rf"(?i)\b(?:young(?:'s|s)?\s+|elastic\s+)?modulus\b"
            rf".{{0,60}}?{number}\s*(?:GPa|MPa|kPa|Pa)\b",
            rf"(?i)\bpoisson(?:[’']s|s)?\s+ratio\b\s*(?:of|=|:|was|is)?"
            rf"\s*{number}",
            rf"(?i)\b(?:thermal|electrical)\s+conductivity\b.{{0,60}}?"
            rf"{number}\s*(?:S\s*/\s*m|S\s*/\s*cm|W\s*/\s*m\s*K?)\b",
            rf"(?i)\bspecific\s+heat\b.{{0,60}}?{number}\s*(?:J|kJ)\s*/",
            rf"(?i)\b(?:coefficient\s+of\s+thermal\s+expansion|CTE)\b"
            rf".{{0,60}}?{number}\s*(?:K|°C)\s*(?:\^-?1|[-⁻]1)",
        )
        return any(re.search(pattern, text) for pattern in explicit_property_patterns)

    @staticmethod
    def _quantitative_result_signal_blocks(chunks: list[dict]) -> list[str]:
        """Return grounded result blocks that strongly imply extractable data."""
        metric_pattern = re.compile(
            r"(?i)\b(?:strength|modulus|stress|strain|force|load|displacement|"
            r"acceleration|frequency|band\s*gap|transmission|energy|absorption|"
            r"toughness|density|diameter|porosity|conductivity|resistivity|"
            r"permittivity|elongation|hardness|roughness|efficiency|capacity|"
            r"stability|loss|damping|thermal|mechanical|compressive|tensile|"
            r"flexural|impact|pH|contact\s+angle|wettability)\b"
        )
        signal_blocks: list[str] = []
        for chunk in chunks:
            if chunk.get("section_name") not in {"results", "conclusion"}:
                continue
            text = str(chunk.get("raw_text") or "")
            if not V7ExtractorService._has_quantitative_result_signal(chunk):
                continue
            if chunk.get("source_type") != "table_text" and not metric_pattern.search(text):
                continue
            block_id = str(chunk.get("source_block_id") or "").strip()
            signal_blocks.append(
                block_id or V7ExtractorService._chunk_source_location(chunk)
            )
        return list(dict.fromkeys(signal_blocks))

    @staticmethod
    def _guard_suspicious_empty_records(
        chunks: list[dict],
        records: list[dict],
        *,
        fact_count: int,
    ) -> str:
        """Describe suspicious zero output so intermediate facts can be retained."""
        if records:
            return ""
        signal_blocks = V7ExtractorService._quantitative_result_signal_blocks(chunks)
        if not signal_blocks:
            return ""
        preview = ", ".join(signal_blocks[:8])
        return (
            "结果章节存在明确的定量性能证据，但抽取后没有生成可用候选记录"
            f"（中间事实 {fact_count} 条；证据块: {preview}）。"
            "已保留样品卡和中间事实并标记人工复核，请检查章节识别、"
            "模型响应或事实到记录的转换。"
        )

    @staticmethod
    def _chunks_for_prompt_multi(chunks: list[dict], limit_per_chunk: int = 3000) -> str:
        return "\n\n---\n\n".join(
            V7ExtractorService._chunk_for_prompt(chunk, limit_per_chunk)
            for chunk in chunks
        )

    @staticmethod
    def _sentence_around(text: str, pattern: str, max_chars: int = 420) -> str:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return text[:max_chars].strip()
        start = max(0, text.rfind(".", 0, match.start()) + 1)
        end_pos = text.find(".", match.end())
        end = len(text) if end_pos == -1 else end_pos + 1
        return re.sub(r"\s+", " ", text[start:end].strip())[:max_chars]

    @staticmethod
    def _deterministic_process_facts(chunks: list[dict]) -> list[dict]:
        facts: list[dict] = []
        seen: set[str] = set()

        def add_fact(
            chunk: dict,
            metric: str,
            value: str,
            unit: str,
            pattern: str,
            *,
            apply_to_all_fiber_samples: bool = False,
        ) -> None:
            if len(facts) >= 8 or metric in seen:
                return
            text = chunk.get("raw_text") or ""
            evidence = V7ExtractorService._sentence_around(text, pattern)
            if not evidence:
                return
            seen.add(metric)
            facts.append({
                "fact_id": "",
                "fact_type": "process",
                "subject_text": metric,
                "candidate_sample_ids": [],
                "metric_or_parameter": metric,
                "value": value,
                "unit": unit,
                "method": "",
                "condition": "",
                "category": "process",
                "evidence_text": evidence,
                "source_location": V7ExtractorService._chunk_source_location(chunk),
                "extraction_method": "rule_text_process",
                "confidence": 0.92,
                "_chunk_section": chunk.get("section_name", ""),
                "_chunk_source_type": chunk.get("source_type", ""),
                "_source_block_id": chunk.get("source_block_id"),
                "_source_page": chunk.get("page_number"),
                "_source_bbox": chunk.get("source_bbox"),
                "_apply_to_all_fiber_samples": apply_to_all_fiber_samples,
                "_background_only": True,
            })

        for chunk in chunks:
            text = chunk.get("raw_text") or ""
            lower = text.lower()
            if V7ExtractorService._is_background_chunk(chunk) and "template" not in lower:
                continue
            section = (chunk.get("section_name") or "").lower()
            experimental = section in {"experimental", "materials", "methods"}
            if re.search(r"template[-\s]?wetting", lower):
                add_fact(
                    chunk,
                    "fabrication_method",
                    "template-wetting",
                    "",
                    r"template[-\s]?wetting",
                    apply_to_all_fiber_samples=True,
                )
            if re.search(r"self[-\s]?poling", lower):
                add_fact(
                    chunk,
                    "poling_method",
                    "self-poling",
                    "",
                    r"self[-\s]?poling",
                    apply_to_all_fiber_samples=True,
                )
            if experimental and re.search(
                r"(?i)\b(?:electrospinn(?:ing|ed)|ES\s+(?:process|experiments?|"
                r"procedure|setup))\b",
                text,
            ):
                add_fact(
                    chunk,
                    "spinning_method",
                    "electrospinning",
                    "",
                    r"\b(?:electrospinn(?:ing|ed)|ES\s+(?:process|experiments?|procedure|setup))\b",
                    apply_to_all_fiber_samples=True,
                )
            if re.search(r"\b(?:anneal|annealed|annealing)\b", lower):
                temp = re.search(r"(\d+(?:\.\d+)?)\s*(?:°\s*C|°C|C)\b", text)
                add_fact(
                    chunk,
                    "annealing_temperature" if temp else "annealing",
                    temp.group(1) if temp else "annealing",
                    "°C" if temp else "",
                    r"\b(?:anneal|annealed|annealing)\b",
                )
            voltage = re.search(r"(\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?)\s*kV", text, re.IGNORECASE)
            if voltage and any(term in lower for term in ("fabrication", "electrospinning", "process")):
                add_fact(chunk, "fabrication_voltage", voltage.group(1), "kV", r"\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?\s*kV")
            if not experimental:
                continue

            paired_voltage_distance = re.search(
                r"(?is)applied\s+voltage\s+and\s+(?:the\s+)?working\s+distance"
                r".{0,80}?at\s+(?P<voltage>\d+(?:\.\d+)?)\s*kV\s+and\s+"
                r"(?P<distance>\d+(?:\.\d+)?)\s*(?P<distance_unit>cm|mm)\b",
                text,
            )
            if paired_voltage_distance:
                add_fact(
                    chunk, "voltage", paired_voltage_distance.group("voltage"), "kV",
                    r"applied\s+voltage",
                    apply_to_all_fiber_samples=True,
                )
                add_fact(
                    chunk,
                    "tip_to_collector_distance",
                    paired_voltage_distance.group("distance"),
                    paired_voltage_distance.group("distance_unit"),
                    r"working\s+distance",
                    apply_to_all_fiber_samples=True,
                )

            polymer_concentration = re.search(
                r"(?is)\b(?:polymer|PCL|PAN|PVDF|PVA|PLA|PEO)\b.{0,60}?"
                r"(?:dissolv\w*|solution).{0,40}?(?P<value>\d+(?:\.\d+)?)\s*"
                r"(?P<unit>w\s*/\s*v\s*%)",
                text,
            )
            if polymer_concentration:
                add_fact(
                    chunk,
                    "polymer_concentration",
                    polymer_concentration.group("value"),
                    re.sub(r"\s+", "", polymer_concentration.group("unit")),
                    r"\b(?:polymer|PCL|PAN|PVDF|PVA|PLA|PEO)\b",
                    apply_to_all_fiber_samples=True,
                )

            spinning_time = re.search(
                r"(?is)spinning\s+time.{0,50}?(?:range\s+of|at|was|=)?\s*"
                r"(?P<value>\d+(?:\.\d+)?\s*[-–]\s*\d+(?:\.\d+)?)\s*"
                r"(?P<unit>min(?:ute)?s?|h(?:ours?)?|s(?:econds?)?)\b",
                text,
            )
            if spinning_time:
                add_fact(
                    chunk,
                    "spinning_time",
                    re.sub(r"\s+", "", spinning_time.group("value")).replace("–", "-"),
                    spinning_time.group("unit"),
                    r"spinning\s+time",
                )
        return facts

    @staticmethod
    def _enrich_sample_cards_from_process_facts(
        sample_cards: list[dict], facts: list[dict],
    ) -> list[dict]:
        """Apply explicit shared fabrication settings to material fiber cards."""
        process_facts = [
            fact for fact in facts
            if fact.get("fact_type") == "process"
            and not fact.get("_hard_reject")
            and fact.get("_apply_to_all_fiber_samples")
        ]
        if not process_facts:
            return sample_cards

        for card in sample_cards:
            fiber_type = normalize_for_match(card.get("fiber_type") or "")
            sid = normalize_for_match(card.get("sample_id") or "")
            is_fiber = bool(
                fiber_type not in {"", "bulk", "powder", "solution"}
                or re.search(r"\b(?:nanofiber|fiber|fibre|fibrous\s+mat|membrane|yarn)\b", sid)
            )
            if not is_fiber:
                continue

            parameters = str(card.get("process_parameters") or "").strip()
            evidence = str(card.get("process_evidence") or "").strip()
            for fact in process_facts:
                metric = find_process_parameter_canonical(
                    str(fact.get("metric_or_parameter") or "")
                ) or str(fact.get("metric_or_parameter") or "")
                value = str(fact.get("value") or "").strip()
                unit = str(fact.get("unit") or "").strip()
                if metric == "spinning_method" and value:
                    if not card.get("spinning_method"):
                        card["spinning_method"] = value
                    if not card.get("process_route"):
                        card["process_route"] = value
                elif value and re.search(r"\d", value):
                    entry = f"{metric}={value}{(' ' + unit) if unit else ''}"
                    parameters = V7ExtractorService._append_unique(parameters, entry)
                fact_evidence = str(fact.get("evidence_text") or "").strip()
                if fact_evidence and fact_evidence not in evidence:
                    evidence = f"{evidence} {fact_evidence}".strip()
            card["process_parameters"] = parameters
            card["process_evidence"] = evidence[:1600]
        return sample_cards

    @staticmethod
    def _enrich_sample_cards_from_repeated_fact_variants(
        sample_cards: list[dict],
        facts: list[dict],
    ) -> list[dict]:
        """Recover a sample variable only when repeated facts agree on it."""
        fraction_re = re.compile(
            r"(?i)\b(?P<label>(?:fib(?:er|re)[-\s]*(?:reinforcement)?\s*)?"
            r"(?:volume\s+)?(?:fraction|ratio|contents?|loading)"
            r"(?:\s+parameter)?)\b.{0,20}?"
            r"(?P<value>\d+(?:\.\d+)?)\s*%"
        )
        counts_by_sample: dict[str, Counter[tuple[str, str, str]]] = defaultdict(Counter)
        for fact in facts:
            sid = normalize_sample_id(fact.get("assigned_sample_id") or "")
            if not sid or fact.get("fact_type") != "performance":
                continue
            text = " ".join([
                str(fact.get("condition") or ""),
                str(fact.get("evidence_text") or ""),
            ])
            seen_in_fact: set[tuple[str, str, str]] = set()
            for match in fraction_re.finditer(text):
                label = normalize_for_match(match.group("label"))
                if "fiber" not in label and "fibre" not in label:
                    continue
                value = f"{float(match.group('value')):g}"
                name = (
                    "fiber volume fraction"
                    if "volume" in label
                    else "fiber content"
                )
                unit = "vol%" if "volume" in label else "%"
                seen_in_fact.add((name, value, unit))
            counts_by_sample[sid].update(seen_in_fact)

        for card in sample_cards:
            if card.get("variable_value"):
                continue
            sid = normalize_sample_id(card.get("sample_id") or "")
            ranked = counts_by_sample.get(sid, Counter()).most_common(2)
            if not ranked or ranked[0][1] < 2:
                continue
            if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
                continue
            (name, value, unit), _ = ranked[0]
            card["variable_name"] = name
            card["variable_value"] = value
            card["variable_unit"] = unit
        return sample_cards

    @staticmethod
    def _deterministic_transition_facts(
        chunks: list[dict],
        existing_facts: list[dict],
    ) -> list[dict]:
        """Recover explicit behavior-transition strains omitted by the LLM sweep."""
        from app.services.extractor_v7.hard_validation import (
            find_explicit_transition_matches,
            infer_metric_from_evidence,
        )

        def value_key(value: Any) -> tuple[str, ...]:
            return tuple(
                f"{float(number):g}"
                for number in re.findall(r"\d+(?:\.\d+)?", str(value or ""))
            )

        def fact_page(fact: dict) -> int | None:
            page = fact.get("_source_page")
            if page is not None:
                try:
                    return int(page)
                except (TypeError, ValueError):
                    pass
            match = re.search(
                r"(?i)\b(?:p\.?|page)\s*(\d+)\b",
                str(fact.get("source_location") or ""),
            )
            return int(match.group(1)) if match else None

        def effective_metric(fact: dict) -> str:
            evidence = str(fact.get("evidence_text") or "")
            fact_value_key = value_key(fact.get("value"))
            for match in find_explicit_transition_matches(evidence):
                if value_key(match["value"]) == fact_value_key:
                    return str(match["metric"])
            metric = find_metric_canonical(
                str(fact.get("metric_or_parameter") or "")
            ) or str(fact.get("metric_or_parameter") or "")
            inferred = infer_metric_from_evidence(
                evidence,
                unit=str(fact.get("unit") or ""),
                current_metric=metric,
            )
            return inferred or metric

        def nearby_parent_sample(page: int | None) -> str:
            if page is None:
                return ""
            candidates: set[str] = set()
            for fact in existing_facts:
                if fact.get("fact_type") != "performance":
                    continue
                if fact.get("extraction_method") in {
                    "AI_holistic_table", "AI_table", "rule_table_process",
                    "rule_table_performance",
                }:
                    continue
                sid = normalize_sample_id(fact.get("assigned_sample_id") or "")
                if not sid or is_numbered_sample_variant(sid):
                    continue
                source_page = fact_page(fact)
                if source_page is not None and abs(source_page - page) <= 1:
                    candidates.add(sid)
            return next(iter(candidates)) if len(candidates) == 1 else ""

        def nearest_following_result_sample(
            text: str,
            end: int,
            page: int | None,
            block_id: str,
            excluded_value_key: tuple[str, ...],
        ) -> str:
            """Use the nearest following grounded result as a local subject hint."""
            tail = text[end:min(len(text), end + 600)]
            ranked: list[tuple[int, str]] = []
            for fact in existing_facts:
                if fact.get("fact_type") != "performance":
                    continue
                sid = normalize_sample_id(fact.get("assigned_sample_id") or "")
                if not sid:
                    continue
                source_block = str(
                    fact.get("_source_block_id") or fact.get("source_block_id") or ""
                )
                if source_block and block_id and source_block != block_id:
                    continue
                if not source_block and fact_page(fact) != page:
                    continue
                numbers = value_key(fact.get("value"))
                if not numbers or numbers == excluded_value_key:
                    continue
                match = re.search(
                    rf"(?<![\d.]){re.escape(numbers[0])}(?![\d.])",
                    tail,
                )
                if match:
                    ranked.append((match.start(), sid))
            if not ranked:
                return ""
            nearest = min(position for position, _ in ranked)
            samples = {sid for position, sid in ranked if position == nearest}
            return next(iter(samples)) if len(samples) == 1 else ""

        def source_evidence(text: str, start: int, end: int) -> str:
            current_start = max(
                text.rfind(".", 0, start),
                text.rfind("?", 0, start),
                text.rfind("!", 0, start),
            ) + 1
            previous_start = max(
                text.rfind(".", 0, max(0, current_start - 1)),
                text.rfind("?", 0, max(0, current_start - 1)),
                text.rfind("!", 0, max(0, current_start - 1)),
            ) + 1
            evidence_start = previous_start if current_start > 0 else 0
            stops = [
                pos for pos in (
                    text.find(".", end),
                    text.find("?", end),
                    text.find("!", end),
                )
                if pos >= 0
            ]
            evidence_end = min(stops) + 1 if stops else len(text)
            return re.sub(r"\s+", " ", text[evidence_start:evidence_end].strip())[:800]

        def add_nearby_sample_context(
            evidence: str,
            chunk: dict,
            sample_id: str,
        ) -> tuple[str, str]:
            if not sample_id:
                return evidence, ""
            from app.services.extractor_v7.final_checklist import (
                sample_id_supported_by_evidence,
            )

            if sample_id_supported_by_evidence(sample_id, evidence):
                return evidence, ""
            try:
                current_order = int(chunk.get("order_index"))
                current_page = int(chunk.get("page_number"))
            except (TypeError, ValueError):
                return evidence, ""
            candidates: list[tuple[int, int, int, dict]] = []
            for context_chunk in chunks:
                if context_chunk is chunk or V7ExtractorService._is_background_chunk(context_chunk):
                    continue
                context_text = re.sub(
                    r"\s+", " ", str(context_chunk.get("raw_text") or "").strip()
                )
                if not context_text or len(context_text) > 900:
                    continue
                try:
                    context_order = int(context_chunk.get("order_index"))
                    context_page = int(context_chunk.get("page_number"))
                except (TypeError, ValueError):
                    continue
                order_distance = abs(context_order - current_order)
                page_distance = abs(context_page - current_page)
                if order_distance > 6 or page_distance > 1:
                    continue
                combined = f"{context_text} {evidence}".strip()
                if not sample_id_supported_by_evidence(sample_id, combined):
                    continue
                caption_priority = 0 if context_chunk.get("source_type") in {
                    "figure_caption", "figure_image", "chart",
                } else 1
                candidates.append((order_distance, page_distance, caption_priority, context_chunk))
            if not candidates:
                return evidence, ""
            _, _, _, context_chunk = min(candidates, key=lambda item: item[:3])
            context_text = re.sub(
                r"\s+", " ", str(context_chunk.get("raw_text") or "").strip()
            )
            combined = f"{context_text} {evidence}".strip()[:1200]
            return combined, str(context_chunk.get("source_block_id") or "")

        recovered: list[dict] = []
        recovered_keys: set[tuple[str, tuple[str, ...], str]] = set()
        for chunk in chunks:
            if V7ExtractorService._is_background_chunk(chunk):
                continue
            section = str(chunk.get("section_name") or "").lower()
            if section not in {"results", "conclusion"}:
                continue
            text = str(chunk.get("raw_text") or "")
            if not text:
                continue
            for match in find_explicit_transition_matches(text):
                    metric = str(match["metric"])
                    value = str(match["value"])
                    page = chunk.get("page_number")
                    try:
                        page_number = int(page) if page is not None else None
                    except (TypeError, ValueError):
                        page_number = None
                    sample_id = nearby_parent_sample(page_number)
                    if not sample_id and metric == "compressive_displacement":
                        sample_id = nearest_following_result_sample(
                            text,
                            int(match["end"]),
                            page_number,
                            str(chunk.get("source_block_id") or ""),
                            value_key(value),
                        )
                    key = (metric, value_key(value), normalize_for_match(sample_id))
                    if key in recovered_keys:
                        continue
                    evidence = source_evidence(
                        text,
                        int(match["start"]),
                        int(match["end"]),
                    )
                    evidence, context_block_id = add_nearby_sample_context(
                        evidence,
                        chunk,
                        sample_id,
                    )
                    matching_existing = []
                    for fact in existing_facts:
                        if fact.get("fact_type") != "performance":
                            continue
                        if effective_metric(fact) != metric:
                            continue
                        if value_key(fact.get("value")) != value_key(value):
                            continue
                        fact_sid = normalize_sample_id(
                            fact.get("assigned_sample_id") or ""
                        )
                        if (
                            sample_id
                            and fact_sid
                            and normalize_for_match(fact_sid)
                            != normalize_for_match(sample_id)
                        ):
                            fact_block_id = str(
                                fact.get("_source_block_id")
                                or fact.get("source_block_id")
                                or ""
                            )
                            chunk_block_id = str(chunk.get("source_block_id") or "")
                            if not fact_block_id or fact_block_id != chunk_block_id:
                                continue
                        matching_existing.append(fact)
                    if matching_existing:
                        target = min(
                            matching_existing,
                            key=lambda fact: (
                                0 if normalize_for_match(
                                    fact.get("assigned_sample_id") or ""
                                ) == normalize_for_match(sample_id) else 1,
                                abs((fact_page(fact) or page_number or 0) - (page_number or 0)),
                                -len(str(fact.get("evidence_text") or "")),
                            ),
                        )
                        target["evidence_text"] = evidence
                        target["metric_or_parameter"] = metric
                        target["unit"] = str(match.get("unit") or target.get("unit") or "%")
                        target["source_location"] = V7ExtractorService._chunk_source_location(chunk)
                        target["_chunk_section"] = section
                        target["_chunk_source_type"] = chunk.get("source_type", "")
                        target["_source_block_id"] = chunk.get("source_block_id")
                        target["_source_page"] = page
                        target["_source_bbox"] = chunk.get("source_bbox")
                        if context_block_id:
                            target["_context_source_block_id"] = context_block_id
                        if sample_id and normalize_for_match(
                            target.get("assigned_sample_id") or ""
                        ) != normalize_for_match(sample_id):
                            target["assigned_sample_id"] = sample_id
                            target["candidate_sample_ids"] = [sample_id]
                            target["assignment_confidence"] = 0.78
                            target["assignment_status"] = "assigned"
                        target["assignment_reason"] = (
                            f"{target.get('assignment_reason') or ''}; "
                            "transition_metric_and_evidence_restored_from_source"
                        ).strip("; ")
                        recovered_keys.add(key)
                        continue
                    recovered.append({
                        "fact_id": "",
                        "fact_type": "performance",
                        "subject_text": metric,
                        "candidate_sample_ids": [sample_id] if sample_id else [],
                        "metric_or_parameter": metric,
                        "value": value,
                        "unit": str(match.get("unit") or "%"),
                        "method": "",
                        "condition": "",
                        "category": "mechanical",
                        "evidence_text": evidence,
                        "source_location": V7ExtractorService._chunk_source_location(chunk),
                        "extraction_method": "rule_text_transition",
                        "confidence": 0.9,
                        "assigned_sample_id": sample_id or None,
                        "assignment_confidence": 0.78 if sample_id else None,
                        "assignment_status": "assigned" if sample_id else "unassigned",
                        "assignment_reason": (
                            "deterministic_transition_neighbor_sample" if sample_id
                            else "deterministic_transition_unassigned"
                        ),
                        "_chunk_section": section,
                        "_chunk_source_type": chunk.get("source_type", ""),
                        "_source_block_id": chunk.get("source_block_id"),
                        "_source_page": page,
                        "_source_bbox": chunk.get("source_bbox"),
                        "_context_source_block_id": context_block_id or None,
                    })
                    recovered_keys.add(key)
        return recovered

    @staticmethod
    async def _llm_json_tolerant(
        client,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int,
        timeout_seconds: int,
        stage: str = "llm",
        reasoning_effort: str | None = None,
    ) -> tuple[dict, str]:
        from app.services.llm_metrics import track_llm_call

        job_id = _current_job_id.get()
        model_name = getattr(client, "model", "unknown")
        prompt_chars = len(system_prompt) + len(user_prompt)
        budget = clamp_max_tokens(
            requested_max_tokens=max_tokens,
            prompt_chars=prompt_chars,
            global_cap=settings.LLM_MAX_OUTPUT_TOKENS_PER_CALL,
        )
        try:
            async with llm_call_slot():
                async with track_llm_call(
                    job_id=job_id,
                    stage=stage,
                    model=model_name,
                    call_type="json_tolerant",
                    prompt_chars=prompt_chars,
                    requested_max_tokens=budget.requested_max_tokens,
                    effective_max_tokens=budget.max_tokens,
                    capped=budget.was_capped,
                ) as metric:
                    parsed, raw = await asyncio.wait_for(
                        client.agenerate_json_tolerant(
                            system_prompt,
                            user_prompt,
                            max_tokens=budget.max_tokens,
                            reasoning_effort=reasoning_effort,
                        ),
                        timeout=timeout_seconds,
                    )
                    metric.response_chars = len(raw or "")
                    usage = getattr(client, "last_usage", {}) or {}
                    metric.prompt_tokens = int(usage.get("prompt_tokens") or 0)
                    metric.completion_tokens = int(usage.get("completion_tokens") or 0)
                    metric.total_tokens = int(usage.get("total_tokens") or 0)
                    if isinstance(parsed, dict) and parsed.get("_parse_failed"):
                        raise RuntimeError(
                            f"LLM stage '{stage}' returned unusable JSON"
                        )
                    return parsed, raw
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"LLM stage '{stage}' timed out after {timeout_seconds}s"
            )

    @staticmethod
    async def _llm_vision_json_tolerant(
        client,
        system_prompt: str,
        user_prompt: str,
        images: list[bytes],
        *,
        max_tokens: int,
        timeout_seconds: int,
        stage: str = "vision_llm",
    ) -> tuple[dict, str]:
        from app.services.llm_metrics import track_llm_call

        job_id = _current_job_id.get()
        model_name = getattr(client, "model", "unknown")
        prompt_chars = len(system_prompt) + len(user_prompt)
        budget = clamp_max_tokens(
            requested_max_tokens=max_tokens,
            prompt_chars=prompt_chars,
            global_cap=settings.LLM_MAX_OUTPUT_TOKENS_PER_CALL,
        )
        try:
            async with llm_call_slot():
                async with track_llm_call(
                    job_id=job_id,
                    stage=stage,
                    model=model_name,
                    call_type="vision_json_tolerant",
                    prompt_chars=prompt_chars,
                    requested_max_tokens=budget.requested_max_tokens,
                    effective_max_tokens=budget.max_tokens,
                    capped=budget.was_capped,
                ) as metric:
                    parsed, raw = await asyncio.wait_for(
                        client.agenerate_vision_json_tolerant(
                            system_prompt,
                            user_prompt,
                            images,
                            max_tokens=budget.max_tokens,
                        ),
                        timeout=timeout_seconds,
                    )
                    metric.response_chars = len(raw or "")
                    usage = getattr(client, "last_usage", {}) or {}
                    metric.prompt_tokens = int(usage.get("prompt_tokens") or 0)
                    metric.completion_tokens = int(usage.get("completion_tokens") or 0)
                    metric.total_tokens = int(usage.get("total_tokens") or 0)
                    if isinstance(parsed, dict) and parsed.get("_parse_failed"):
                        raise RuntimeError(
                            f"Vision LLM stage '{stage}' returned unusable JSON"
                        )
                    return parsed, raw
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"Vision LLM stage '{stage}' timed out after {timeout_seconds}s"
            )

    @staticmethod
    def _llm_parallel_calls_for_mode(model_mode: str) -> int:
        requested = (
            settings.WEAK_LLM_PARALLEL_CALLS
            if model_mode == "weak"
            else settings.STRONG_LLM_PARALLEL_CALLS
        )
        return per_job_llm_parallel_limit(requested)

    @staticmethod
    async def _stage1_sample_mentions(
        client,
        chunks: list[dict],
        model_mode: str,
        *,
        progress_callback=None,
        job_id: int | None = None,
        db: AsyncSession | None = None,
        llm_timeout: int = 90,
    ) -> list[dict]:
        """Extract atomic sample mentions with batched calls (weak + strong)."""
        priority = V7ExtractorService._stage1_chunks(chunks, model_mode)
        batches = V7ExtractorService._stage1_batches(priority, model_mode)
        parallel_calls = V7ExtractorService._llm_parallel_calls_for_mode(model_mode)
        semaphore = asyncio.Semaphore(max(1, parallel_calls))
        mentions: list[dict] = []
        total = max(len(batches), 1)
        completed = 0
        lock = asyncio.Lock()

        async def process_batch(idx: int, batch: list[dict]) -> list[dict]:
            nonlocal completed
            async with semaphore:
                await V7ExtractorService._check_cancelled(db, job_id)
                prompt_text = (
                    V7ExtractorService._chunks_for_prompt_multi(batch, 3000)
                    if len(batch) > 1
                    else V7ExtractorService._chunk_for_prompt(batch[0], 4500)
                )
                parsed, _ = await V7ExtractorService._llm_json_tolerant(
                    client,
                    SAMPLE_MENTIONS_PROMPT,
                    prompt_text,
                    max_tokens=1200 if model_mode == "weak" else 1800,
                    timeout_seconds=llm_timeout,
                )
                batch_mentions: list[dict] = []
                items = _response_rows(parsed, "sample_mentions", "_items")
                if isinstance(items, dict):
                    items = [items]
                anchor = batch[0]
                for item in items:
                    mention_text = normalize_sample_id(item.get("mention_text", ""))
                    normalized = normalize_sample_id(item.get("normalized_sample_id") or mention_text)
                    if not mention_text or not normalized:
                        continue
                    batch_mentions.append({
                        "mention_text": mention_text,
                        "normalized_sample_id": normalized,
                        "aliases": item.get("aliases") if isinstance(item.get("aliases"), list) else [],
                        "context_text": (item.get("context_text") or "")[:500],
                        "source_location": V7ExtractorService._chunk_source_location(anchor),
                        "source_type": "table" if anchor.get("source_type") == "table_text" else anchor.get("source_type", "text"),
                        "confidence": float(item.get("confidence", 0.6) or 0.6),
                    })
                async with lock:
                    completed += 1
                    if progress_callback:
                        result = progress_callback(
                            "extracting",
                            15 + int(7 * completed / total),
                            f"Stage 1: 识别样品 ({completed}/{total})",
                        )
                        if inspect.isawaitable(result):
                            await result
                return batch_mentions

        if parallel_calls > 1 and len(batches) > 1:
            results = await asyncio.gather(
                *[process_batch(idx, batch) for idx, batch in enumerate(batches)]
            )
            for batch_mentions in results:
                mentions.extend(batch_mentions)
        else:
            for idx, batch in enumerate(batches):
                mentions.extend(await process_batch(idx, batch))
        return V7ExtractorService._dedupe_sample_mentions(mentions)

    @staticmethod
    def _dedupe_sample_mentions(mentions: list[dict]) -> list[dict]:
        deduped: dict[tuple[str, str], dict] = {}
        for mention in mentions:
            key = (
                normalize_for_match(mention.get("normalized_sample_id")),
                str(mention.get("source_location") or ""),
            )
            current = deduped.get(key)
            if current is None or mention.get("confidence", 0) > current.get("confidence", 0):
                deduped[key] = mention
        return list(deduped.values())

    @staticmethod
    def _looks_like_sample_candidate(sample_id: str) -> bool:
        sid = normalize_sample_id(sample_id)
        norm = normalize_for_match(sid)
        if not is_material_sample_id(sid):
            return False
        if not norm or len(norm) < 2 or len(sid) > 80:
            return False
        if len(sid.split()) > 8 or re.search(r"[.!?]\s", sid):
            return False
        generic = {
            "sample", "samples", "fiber", "fibers", "nanofiber", "nanofibers",
            "film", "films", "aerogel", "aerogels", "composite", "composites",
            "material", "materials", "optimized sample", "modified sample",
        }
        if norm in generic:
            return False
        if re.fullmatch(
            r"(?i)(?:this|that|the)\s+(?:particular\s+)?(?:material|sample|specimen)|"
            r"(?:both|all|these|those)\s+(?:materials|samples|specimens)",
            sid,
        ):
            return False
        if re.search(
            r"(?i)\b(?:obtained\s+with|resulted\s+in|shown\s+in\s+table|"
            r"weight\s+loss|oil\s+absorption(?:\s+capacity)?|initial\s+stage|"
            r"using\s+(?:its|various|the)|raw\s+and\s+\w+)",
            sid,
        ):
            return False
        if re.search(
            r"(?i)\b(?:volume|weight|mass)\s+fraction\b|"
            r"\b(?:mean|average)\s*\(?(?:dev|std)\)?\b|"
            r"\bstandard\s+deviation\b|"
            r"^(?:modified|treated|untreated|optimized)\s+.+\s+samples?$",
            sid,
        ):
            return False
        if not re.search(r"[A-Za-z]", sid):
            return False
        if re.match(
            r"^\d+(?:\.\d+)?\s*(?:%|wt%|vol%|mpa|gpa|kpa|hz|khz|mhz|ghz|"
            r"min|s|h|cycles?|cycle|v|kv|ma|a|c|k|nm|um|μm|mm|cm)\b",
            norm,
        ):
            return False
        if re.match(r"^\d+(?:\.\d+)?\s*%?\s*(?:strain|rh|humidity|frequency|temperature)\b", norm):
            return False
        if norm in {"control", "reference", "blank"}:
            return False
        return True

    @staticmethod
    def _sample_mentions_from_fact_candidates(facts: list[dict]) -> list[dict]:
        mentions: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for fact in facts:
            source = fact.get("source_location") or ""
            evidence = (fact.get("evidence_text") or fact.get("subject_text") or "")[:500]
            if is_characterization_peak_metric(
                str(fact.get("metric_or_parameter") or ""),
                method=str(fact.get("method") or ""),
                evidence=evidence,
            ):
                continue
            candidates = V7ExtractorService._as_list(fact.get("candidate_sample_ids"))
            if fact.get("assigned_sample_id"):
                candidates = [fact.get("assigned_sample_id"), *candidates]
            explicit_candidates = extract_explicit_sample_names(evidence)
            candidates = [*explicit_candidates, *candidates]
            explicit_keys = {normalize_for_match(sid) for sid in explicit_candidates}
            for candidate in candidates:
                sid = normalize_sample_id(str(candidate))
                if not V7ExtractorService._looks_like_sample_candidate(sid):
                    continue
                key = (normalize_for_match(sid), source)
                if key in seen:
                    continue
                seen.add(key)
                mentions.append({
                    "mention_text": sid,
                    "normalized_sample_id": sid,
                    "aliases": [],
                    "context_text": evidence,
                    "source_location": source,
                    "source_type": "fact_candidate",
                    "confidence": 0.78 if normalize_for_match(sid) in explicit_keys else 0.45,
                })
        return mentions

    @staticmethod
    async def _stage1_variable_candidates(
        client,
        chunks: list[dict],
        sample_mentions: list[dict],
        model_mode: str,
        *,
        progress_callback=None,
        job_id: int | None = None,
        db: AsyncSession | None = None,
        llm_timeout: int = 90,
    ) -> list[dict]:
        """Extract explicit variable candidates near sample mentions."""
        if not sample_mentions:
            return []

        source_to_samples: dict[str, list[str]] = defaultdict(list)
        for mention in sample_mentions:
            source = mention.get("source_location", "")
            sid = mention.get("normalized_sample_id") or mention.get("mention_text")
            if sid and sid not in source_to_samples[source]:
                source_to_samples[source].append(sid)

        priority = V7ExtractorService._stage1_chunks(chunks, model_mode)
        if model_mode == "weak":
            relevant = [
                chunk for chunk in priority
                if source_to_samples.get(V7ExtractorService._chunk_source_location(chunk))
            ][:4]
            if not relevant:
                relevant = priority[:3]
            batches = [relevant] if relevant else []
        else:
            relevant = [
                chunk for chunk in priority
                if source_to_samples.get(V7ExtractorService._chunk_source_location(chunk))
            ]
            if not relevant:
                relevant = priority[:8]
            batches = V7ExtractorService._stage1_batches(relevant, model_mode)

        parallel_calls = V7ExtractorService._llm_parallel_calls_for_mode(model_mode)
        semaphore = asyncio.Semaphore(max(1, parallel_calls))
        variables: list[dict] = []
        total = max(len(batches), 1)
        completed = 0
        lock = asyncio.Lock()

        async def process_batch(idx: int, batch: list[dict]) -> list[dict]:
            nonlocal completed
            async with semaphore:
                await V7ExtractorService._check_cancelled(db, job_id)
                anchor = batch[0]
                source = V7ExtractorService._chunk_source_location(anchor)
                sample_hint = source_to_samples.get(source) or [
                    m.get("normalized_sample_id", "")
                    for m in sample_mentions
                    if m.get("normalized_sample_id")
                ][:40]
                if not sample_hint:
                    return []
                body = (
                    V7ExtractorService._chunks_for_prompt_multi(batch, 3000)
                    if len(batch) > 1
                    else V7ExtractorService._chunk_for_prompt(anchor, 4500)
                )
                prompt_text = (
                    f"Known sample names in/near this chunk: {json.dumps(sample_hint, ensure_ascii=False)}\n\n"
                    f"{body}"
                )
                parsed, _ = await V7ExtractorService._llm_json_tolerant(
                    client,
                    VARIABLE_CANDIDATES_PROMPT,
                    prompt_text,
                    max_tokens=1200 if model_mode == "weak" else 1800,
                    timeout_seconds=llm_timeout,
                )
                batch_vars: list[dict] = []
                items = _response_rows(parsed, "variable_candidates", "_items")
                if isinstance(items, dict):
                    items = [items]
                for item in items:
                    sid = normalize_sample_id(item.get("sample_id", ""))
                    if not sid:
                        continue
                    batch_vars.append({
                        "sample_id": sid,
                        "variable_name_raw": item.get("variable_name_raw", "") or "",
                        "variable_value_raw": item.get("variable_value_raw", "") or "",
                        "variable_unit_raw": item.get("variable_unit_raw", "") or "",
                        "context_text": (item.get("context_text") or "")[:500],
                        "source_location": source,
                        "confidence": float(item.get("confidence", 0.55) or 0.55),
                    })
                async with lock:
                    completed += 1
                    if progress_callback:
                        result = progress_callback(
                            "extracting",
                            22 + int(8 * completed / total),
                            f"Stage 1: 提取变量 ({completed}/{total})",
                        )
                        if inspect.isawaitable(result):
                            await result
                return batch_vars

        if parallel_calls > 1 and len(batches) > 1:
            results = await asyncio.gather(
                *[process_batch(idx, batch) for idx, batch in enumerate(batches)]
            )
            for batch_vars in results:
                variables.extend(batch_vars)
        else:
            for idx, batch in enumerate(batches):
                variables.extend(await process_batch(idx, batch))
        return variables

    @staticmethod
    def _fill_paper_metadata_fallback(
        paper_metadata: dict,
        raw_text: str,
        original_filename: str,
        chunks: list[dict] | None = None,
    ) -> dict:
        """Fill missing paper metadata once, then inherit it to every record."""
        metadata = dict(paper_metadata or {})
        # MinerU often places publisher/DOI sidebars after the abstract and
        # introduction blocks, beyond the first 5k characters of page one.
        parsed_front_matter = "\n".join(
            str(chunk.get("raw_text") or "")
            for chunk in chunks or []
            if int(chunk.get("page_number") or 0) <= 2
            and str(chunk.get("source_type") or "").lower() != "ref_text"
        )
        front_matter = f"{raw_text[:16000]}\n{parsed_front_matter}"[:30000]

        def clean_line(value: str) -> str:
            value = re.sub(r"^\s*#{1,6}\s*", "", value or "")
            return re.sub(r"\s+", " ", value).strip()

        journal_citation = re.search(
            r"(?im)^\s*([A-Z][A-Za-z0-9 .,&'\-]{2,100}?)\s+"
            r"((?:19|20)\d{2}),\s*\d+\b",
            front_matter,
        )

        if not metadata.get("doi_or_url"):
            doi_match = re.search(
                r"(?i)\b(?:doi\s*:\s*|doi\.org/)?"
                r"(10\.\d{4,9}/[-._;()/:A-Z0-9]+)",
                front_matter,
            )
            if doi_match:
                metadata["doi_or_url"] = doi_match.group(1).rstrip(".,;) ")
            else:
                filename_doi = re.search(
                    r"(?i)(10\.\d{4,9})[_/]([A-Z0-9][-._;()A-Z0-9]+)",
                    os.path.splitext(original_filename)[0],
                )
                if filename_doi:
                    metadata["doi_or_url"] = (
                        f"{filename_doi.group(1)}/{filename_doi.group(2)}"
                    )

        if not metadata.get("year"):
            year_match = re.search(
                r"(?:©|&copy;|copyright)\s*((?:19|20)\d{2})\b",
                front_matter,
                re.IGNORECASE,
            )
            if not year_match and journal_citation:
                year_match = journal_citation
                metadata["year"] = journal_citation.group(2)
            if not year_match and metadata.get("doi_or_url"):
                year_match = re.search(
                    r"[./_-]((?:19|20)\d{2})\d{3,}\b",
                    str(metadata["doi_or_url"]),
                )
            if year_match:
                metadata.setdefault("year", year_match.group(1))
            if not metadata.get("year"):
                year_match = re.search(r"\b((?:19|20)\d{2})\b", front_matter[:2000])
                if year_match:
                    metadata["year"] = year_match.group(1)

        if not is_plausible_paper_title(
            str(metadata.get("paper_title") or "")
        ):
            metadata.pop("paper_title", None)
            chunk_lines = [
                clean_line(str(chunk.get("raw_text") or ""))
                for chunk in chunks or []
                if int(chunk.get("page_number") or 0) <= 2
                and str(chunk.get("section_name") or "") == "title_abstract"
            ]
            lines = [
                *chunk_lines,
                *[
                    clean_line(line)
                    for line in front_matter.splitlines()
                    if clean_line(line)
                ],
            ]
            title_candidates = [
                line for line in lines[:60]
                if 20 <= len(line) <= 220
                and is_plausible_paper_title(line)
                and not re.search(r"^\[page\s+\d+\]$", line, re.IGNORECASE)
                and not _looks_like_affiliation_or_address(line)
            ]
            metadata["paper_title"] = title_candidates[0] if title_candidates else original_filename

        if not metadata.get("journal"):
            if journal_citation:
                metadata["journal"] = clean_line(journal_citation.group(1)).rstrip(",")
            lines = [clean_line(line) for line in front_matter.splitlines() if clean_line(line)]
            for line in lines[:100]:
                if metadata.get("journal"):
                    break
                if _looks_like_journal_name(line):
                    metadata["journal"] = line
                    break

        return metadata

    # ------------------------------------------------------------------
    # Stage 2: Fact candidates
    # ------------------------------------------------------------------

    @staticmethod
    async def _stage2_fact_candidates(
        client,
        chunks: list[dict],
        model_mode: str = "strong",
        *,
        holistic_fact_count: int = 0,
        holistic_covered_table_ids: set[str] | None = None,
        holistic_performance_complete: bool = False,
        holistic_performance_attempted: bool = False,
        known_sample_ids: list[str] | None = None,
        allow_partial_failures: bool = False,
        warnings: list[str] | None = None,
        progress_callback=None,
        job_id: int | None = None,
        db: AsyncSession | None = None,
        llm_timeout: int = 90,
    ) -> list[dict]:
        """Extract atomic fact candidates with capped chunk count and per-chunk progress."""
        repair_mode = model_mode == "strong" and (
            holistic_performance_attempted or holistic_performance_complete
        )
        if model_mode == "weak":
            prompt = WEAK_FACTS_PROMPT
        elif repair_mode:
            prompt = STAGE2_PERFORMANCE_REPAIR_PROMPT.format(
                sample_ids=", ".join(known_sample_ids or []) or "unknown",
            )
        else:
            prompt = (
                STAGE2_FACTS_PROMPT.replace(
                    "{{metrics_list}}", build_metrics_prompt_text()
                ).replace(
                    "{{structure_list}}", build_structure_prompt_text()
                ).replace(
                    "{{process_list}}", build_process_prompt_text()
                )
            )

        selected = V7ExtractorService._select_stage2_chunks(
            chunks,
            model_mode,
            holistic_fact_count=holistic_fact_count,
            holistic_covered_table_ids=holistic_covered_table_ids,
            holistic_performance_complete=holistic_performance_complete,
            holistic_performance_attempted=holistic_performance_attempted,
        )
        units = V7ExtractorService._stage2_execution_units(selected, model_mode)

        all_facts: list[dict] = []
        total = max(len(units), 1)
        parallel_calls = V7ExtractorService._llm_parallel_calls_for_mode(model_mode)
        parallel = parallel_calls > 1 and len(units) > 1
        semaphore = asyncio.Semaphore(max(1, parallel_calls))
        completed = 0
        progress_lock = asyncio.Lock()

        async def process_unit(idx: int, unit: list[dict]) -> list[dict]:
            nonlocal completed
            async with semaphore:
                await V7ExtractorService._check_cancelled(db, job_id)
                anchor = unit[0]
                source = V7ExtractorService._chunk_source_location(anchor)
                prompt_text = (
                    V7ExtractorService._chunks_for_prompt_multi(unit, 4500)
                    if len(unit) > 1
                    else V7ExtractorService._chunk_for_prompt(anchor, 6500)
                )
                is_table = any(c.get("source_type") == "table_text" for c in unit)

                async def call_stage2_with_retry(
                    system_prompt: str,
                    user_prompt: str,
                    *,
                    max_tokens: int,
                    timeout_seconds: int,
                    stage: str,
                    retry_user_prompt: str | None = None,
                ):
                    try:
                        return await V7ExtractorService._llm_json_tolerant(
                            client,
                            system_prompt,
                            user_prompt,
                            max_tokens=max_tokens,
                            timeout_seconds=timeout_seconds,
                            stage=stage,
                        )
                    except ExtractionCancelled:
                        raise
                    except Exception as first_exc:
                        message = str(first_exc).lower()
                        exception_name = type(first_exc).__name__.lower()
                        retryable = isinstance(first_exc, TimeoutError) or any(
                            token in f"{exception_name} {message}"
                            for token in (
                                "timed out",
                                "timeout",
                                "unusable json",
                                "non-json",
                                "no json",
                            )
                        )
                        if not retryable:
                            raise
                        try:
                            return await V7ExtractorService._llm_json_tolerant(
                                client,
                                system_prompt,
                                retry_user_prompt or user_prompt,
                                max_tokens=max(900, int(max_tokens * 0.8)),
                                timeout_seconds=min(timeout_seconds, 120),
                                stage=f"{stage}_retry",
                            )
                        except ExtractionCancelled:
                            raise
                        except Exception as retry_exc:
                            raise RuntimeError(
                                f"{first_exc}; retry failed: {retry_exc}"
                            ) from retry_exc

                try:
                    if is_table and model_mode == "strong":
                        table_text = str(anchor.get("raw_text") or "")
                        parsed, _ = await call_stage2_with_retry(
                            TABLE_PERFORMANCE_PROMPT.format(
                                sample_ids=", ".join(known_sample_ids or [])
                                or "Use the exact material/specimen labels in the table",
                            ),
                            f"Structured table:\n{table_text[:6500]}",
                            max_tokens=3000,
                            timeout_seconds=min(
                                llm_timeout,
                                settings.STRONG_TABLE_LLM_TIMEOUT_SECONDS,
                            ),
                            stage="stage2_table_fallback",
                        )
                        items = _response_rows(parsed, "rows", "_items")
                        unit_facts = table_rows_to_facts(
                            items,
                            table_text=table_text,
                            source_location=source,
                            source_block_id=anchor.get("source_block_id"),
                            source_page=anchor.get("page_number"),
                            source_bbox=anchor.get("source_bbox"),
                            known_sample_ids=known_sample_ids,
                        )
                    else:
                        parsed, _ = await call_stage2_with_retry(
                            prompt,
                            prompt_text,
                            max_tokens=(
                                (
                                    max(1400, settings.WEAK_STAGE2_BATCH_MAX_TOKENS)
                                    if len(unit) > 1 or is_table
                                    else 1400
                                ) if model_mode == "weak" else (
                                    1800 if repair_mode else 2800
                                )
                            ),
                            timeout_seconds=llm_timeout,
                            stage="stage2_facts",
                            retry_user_prompt=prompt_text[:4500],
                        )
                        items = _response_rows(parsed, "facts", "_items")
                        extraction_method = V7ExtractorService._extraction_method_for_chunk(anchor)
                        unit_facts = []
                        for item in items:
                            fact = V7ExtractorService._normalize_fact_from_chunk(
                                item, source, extraction_method
                            )
                            if (
                                fact
                                and not V7ExtractorService._is_non_material_setup_fact(fact)
                                and (
                                    not repair_mode
                                    or V7ExtractorService._is_performance_repair_fact(fact)
                                )
                            ):
                                unit_facts.append(fact)

                    for fact in unit_facts:
                        source_chunk = V7ExtractorService._resolve_fact_source_chunk(
                            fact, unit
                        )
                        fact["_chunk_section"] = source_chunk.get("section_name", "")
                        fact["_chunk_source_type"] = source_chunk.get("source_type", "")
                        fact["_source_block_id"] = source_chunk.get("source_block_id")
                        fact["_source_page"] = source_chunk.get("page_number")
                        fact["_source_bbox"] = source_chunk.get("source_bbox")
                        fact["extraction_method"] = (
                            V7ExtractorService._extraction_method_for_chunk(source_chunk)
                        )
                        if (
                            is_rough_source_location(fact.get("source_location") or "")
                            or not re.search(
                                r"(?i)\bB\d{3,}\b",
                                str(fact.get("source_location") or ""),
                            )
                        ):
                            fact["source_location"] = (
                                V7ExtractorService._chunk_source_location(source_chunk)
                            )
                    return unit_facts
                except ExtractionCancelled:
                    raise
                except Exception as exc:
                    if not allow_partial_failures:
                        raise
                    warning = (
                        f"stage2 unit {idx + 1}/{len(units)} "
                        f"({anchor.get('source_block_id') or source}) failed: {exc}"
                    )
                    if warnings is not None:
                        warnings.append(warning)
                    print(f"Warning: {warning}")
                    return []
                finally:
                    async with progress_lock:
                        completed += 1
                        if progress_callback:
                            result = progress_callback(
                                "extracting",
                                30 + int(20 * completed / total),
                                f"Stage 2: 提取事实 ({completed}/{total})",
                            )
                            if inspect.isawaitable(result):
                                await result

        if parallel:
            results = await asyncio.gather(
                *[process_unit(idx, unit) for idx, unit in enumerate(units)]
            )
            for unit_facts in results:
                all_facts.extend(unit_facts)
        else:
            for idx, unit in enumerate(units):
                all_facts.extend(await process_unit(idx, unit))

        for i, fact in enumerate(all_facts):
            if not fact.get("fact_id"):
                fact["fact_id"] = f"F{i + 1:04d}"
        return renumber_fact_ids(all_facts)

    @staticmethod
    def _fact_chunk_priority(chunk: dict) -> tuple[int, int, int, int]:
        source_type = chunk.get("source_type")
        section = (chunk.get("section_name") or "").lower()
        type_rank = {
            "table_text": 0,
            "figure_caption": 1,
            "text": 2,
        }.get(source_type, 3)
        section_rank = {
            "results": 0,
            "conclusion": 1,
            "experimental": 2,
        }.get(section, 4)
        # Prefer data-rich blocks (tables/charts) over short headings.
        text_len = len(chunk.get("raw_text") or "")
        len_rank = 0 if text_len >= 200 else 1
        signal_rank = -V7ExtractorService._stage2_signal_score(chunk)
        return (type_rank, section_rank, signal_rank, len_rank)

    @staticmethod
    def _fact_chunks(chunks: list[dict]) -> list[dict]:
        selected = []
        for chunk in chunks:
            source_type = chunk.get("source_type")
            section = chunk.get("section_name")
            if V7ExtractorService._is_background_chunk(chunk):
                continue
            if source_type in {"table_text", "figure_caption"}:
                selected.append(chunk)
            elif section in {"experimental", "results", "conclusion"}:
                selected.append(chunk)
        if not selected:
            selected = [
                chunk for chunk in chunks
                if not V7ExtractorService._is_background_chunk(chunk)
            ]
        if not selected:
            selected = list(chunks)
        selected.sort(key=V7ExtractorService._fact_chunk_priority)
        return selected

    @staticmethod
    def _select_stage2_chunks(
        chunks: list[dict],
        model_mode: str,
        *,
        holistic_fact_count: int = 0,
        holistic_covered_table_ids: set[str] | None = None,
        holistic_performance_complete: bool = False,
        holistic_performance_attempted: bool = False,
    ) -> list[dict]:
        """Tiered chunk selection: prioritize tables/figures, then cap total."""
        covered_ids = {str(block_id) for block_id in (holistic_covered_table_ids or set())}
        fallback_chunks = [
            chunk for chunk in chunks
            if not (
                chunk.get("source_type") == "table_text"
                and str(chunk.get("source_block_id") or "") in covered_ids
            )
        ]
        merged = merge_adjacent_table_chunks(fallback_chunks)
        base = V7ExtractorService._fact_chunks(merged)
        if model_mode == "weak":
            return V7ExtractorService._cap_chunks(base, settings.WEAK_MAX_FACT_CHUNKS)

        threshold_slim = (
            model_mode == "strong"
            and holistic_fact_count >= settings.STRONG_STAGE2_HOLISTIC_SLIM_THRESHOLD
        )
        complete_slim = holistic_performance_complete and threshold_slim
        partial_holistic = (
            holistic_performance_attempted and not holistic_performance_complete
        )
        slim = threshold_slim and not partial_holistic
        if slim:
            if complete_slim:
                base = [
                    c for c in base
                    if c.get("source_type") == "table_text"
                    or V7ExtractorService._has_intrinsic_material_property_signal(c)
                ]
            else:
                base = [
                    c for c in base
                    if c.get("source_type") == "table_text"
                    or (
                        c.get("source_type") == "figure_caption"
                        and V7ExtractorService._has_quantitative_result_signal(c)
                    )
                ]
        elif holistic_performance_attempted or holistic_performance_complete:
            # A successful API call is not proof of semantic coverage. When the
            # holistic sweep is low-yield or partially times out, repair only
            # high-density numeric blocks instead of reopening the full paper.
            base = [
                c for c in base
                if c.get("source_type") == "table_text"
                or (
                    c.get("section_name") in {"experimental", "results", "conclusion"}
                    and V7ExtractorService._has_quantitative_result_signal(c)
                    and V7ExtractorService._stage2_signal_score(c) >= 7
                )
            ]

        tables = [c for c in base if c.get("source_type") == "table_text"]
        figures = [c for c in base if c.get("source_type") == "figure_caption"]
        others = [
            c for c in base
            if c.get("source_type") not in {"table_text", "figure_caption"}
        ]
        selected: list[dict] = []
        selected.extend(tables[: settings.STRONG_MAX_TABLE_CHUNKS])
        for chunk in figures + others:
            if chunk not in selected:
                selected.append(chunk)
        targeted_repair = (
            holistic_performance_attempted or holistic_performance_complete
        ) and not slim
        cap = (
            settings.STRONG_STAGE2_HOLISTIC_SLIM_MAX_CHUNKS
            if slim or targeted_repair
            else settings.STRONG_MAX_FACT_CHUNKS
        )
        return V7ExtractorService._cap_chunks(selected, cap)

    @staticmethod
    def _holistic_core_fact_count(facts: list[dict]) -> int:
        """Count unique non-characterization results for Stage 2 coverage decisions."""
        unique: set[tuple[str, str, str, str]] = set()
        for fact in facts:
            if fact.get("fact_type") != "performance" or fact.get("_hard_reject"):
                continue
            if is_characterization_peak_metric(
                str(fact.get("metric_or_parameter") or ""),
                method=str(fact.get("method") or ""),
                evidence=str(fact.get("evidence_text") or ""),
            ):
                continue
            unique.add((
                normalize_for_match(fact.get("assigned_sample_id") or ""),
                normalize_for_match(fact.get("metric_or_parameter") or ""),
                str(fact.get("value") or "").strip(),
                str(fact.get("_source_block_id") or fact.get("source_location") or ""),
            ))
        return len(unique)

    @staticmethod
    def _guard_incomplete_holistic_performance(warnings: list[str]) -> list[str]:
        """Return core sweep failures that require full Stage 2 fallback."""
        return [
            warning for warning in warnings
            if warning.startswith("performances:")
        ]

    @staticmethod
    def _stage2_execution_units(chunks: list[dict], model_mode: str) -> list[list[dict]]:
        """Batch small text chunks; keep tables as standalone units."""
        chunks = sorted(
            chunks,
            key=lambda chunk: (
                int(chunk.get("page_number") or 0),
                int(chunk.get("order_index") or 0),
            ),
        )
        if model_mode == "weak":
            units: list[list[dict]] = []
            batch: list[dict] = []
            batch_chars = 0
            batch_size = max(1, settings.WEAK_STAGE2_BATCH_SIZE)
            max_chars = max(2000, settings.WEAK_STAGE2_BATCH_MAX_CHARS)
            for chunk in chunks:
                if chunk.get("source_type") == "table_text":
                    if batch:
                        units.append(batch)
                        batch = []
                        batch_chars = 0
                    units.append([chunk])
                    continue
                chunk_len = min(len(chunk.get("raw_text") or ""), 4500) + 400
                if batch and (
                    len(batch) >= batch_size
                    or batch_chars + chunk_len > max_chars
                ):
                    units.append(batch)
                    batch = []
                    batch_chars = 0
                batch.append(chunk)
                batch_chars += chunk_len
            if batch:
                units.append(batch)
            return units

        units: list[list[dict]] = []
        batch: list[dict] = []
        batch_chars = 0
        batch_size = max(1, settings.STRONG_STAGE2_BATCH_SIZE)
        for chunk in chunks:
            if chunk.get("source_type") == "table_text":
                if batch:
                    units.append(batch)
                    batch = []
                    batch_chars = 0
                units.append([chunk])
                continue
            chunk_len = len(chunk.get("raw_text") or "") + 400
            if batch and (
                len(batch) >= batch_size
                or batch_chars + chunk_len > 10000
            ):
                units.append(batch)
                batch = []
                batch_chars = 0
            batch.append(chunk)
            batch_chars += chunk_len
        if batch:
            units.append(batch)
        return units

    @staticmethod
    def _is_background_chunk(chunk: dict) -> bool:
        section = (chunk.get("section_name") or "").lower()
        if section in {
            "title_abstract", "introduction", "background", "references", "back_matter",
        }:
            return True
        return False

    @staticmethod
    def _extraction_method_for_chunk(chunk: dict) -> str:
        source_type = chunk.get("source_type")
        if source_type == "table_text":
            return "AI_table"
        if source_type in {"figure_caption", "figure_image"}:
            return "AI_figure"
        return "AI_text"

    @staticmethod
    def _normalize_fact_from_chunk(item: dict, source: str, extraction_method: str) -> dict | None:
        metric = (item.get("metric_or_parameter") or item.get("performance_metric") or "").strip()
        value = str(item.get("value") or item.get("performance_value") or "").strip()
        ftype = (item.get("fact_type") or "performance").strip()
        if not metric and not value:
            return None
        if ftype == "performance" and is_placeholder_performance_value(value):
            return None
        candidates = item.get("candidate_sample_ids") or item.get("sample_ids") or []
        if item.get("sample_id"):
            candidates = [item.get("sample_id"), *V7ExtractorService._as_list(candidates)]
        if not isinstance(candidates, list):
            candidates = V7ExtractorService._as_list(candidates)
        category = item.get("category") or (find_category_for_metric(metric) if ftype == "performance" else ftype)
        confidence = float(item.get("confidence", 0.6) or 0.6)
        if extraction_method == "AI_figure":
            confidence = min(confidence, 0.68)
        item_source = item.get("source_location") or ""
        source_location = item_source if item_source and not is_rough_source_location(item_source) else source
        source_page = item.get("source_page")
        try:
            source_page = int(source_page) if source_page not in (None, "") else None
        except (TypeError, ValueError):
            source_page = None
        return {
            "fact_id": item.get("fact_id", ""),
            "fact_type": ftype if ftype in {"composition", "process", "structure", "performance"} else "performance",
            "subject_text": item.get("subject_text", "") or metric,
            "candidate_sample_ids": [normalize_sample_id(str(c)) for c in candidates if str(c).strip()],
            "metric_or_parameter": metric,
            "value": value,
            "unit": item.get("unit") or item.get("performance_unit") or "",
            "method": item.get("method") or item.get("performance_method") or "",
            "condition": item.get("condition") or item.get("performance_condition") or "",
            "category": category,
            "evidence_text": item.get("evidence_text") or "",
            "source_location": source_location,
            "extraction_method": extraction_method,
            "confidence": confidence,
            "_source_block_id": str(item.get("source_block_id") or "").strip() or None,
            "_source_page": source_page,
        }

    @staticmethod
    def _resolve_fact_source_chunk(fact: dict, unit: list[dict]) -> dict:
        """Resolve a batched fact back to the MinerU block containing its evidence."""
        if not unit:
            return {}
        by_id = {
            str(chunk.get("source_block_id") or ""): chunk
            for chunk in unit
            if chunk.get("source_block_id")
        }
        explicit_id = str(fact.get("_source_block_id") or "").strip()
        if explicit_id in by_id:
            return by_id[explicit_id]
        location_match = re.search(
            r"(?i)\b(?:block\s*)?(B\d{3,})\b",
            str(fact.get("source_location") or ""),
        )
        if location_match and location_match.group(1).upper() in by_id:
            return by_id[location_match.group(1).upper()]

        evidence = normalize_for_match(fact.get("evidence_text") or "")
        if len(evidence) >= 20:
            matches = [
                chunk for chunk in unit
                if evidence in normalize_for_match(chunk.get("raw_text") or "")
            ]
            if len(matches) == 1:
                return matches[0]
        return unit[0]

    @staticmethod
    def _is_non_material_setup_fact(fact: dict) -> bool:
        metric = normalize_for_match(fact.get("metric_or_parameter") or fact.get("subject_text") or "")
        unit = normalize_for_match(fact.get("unit") or "")
        evidence = normalize_for_match(fact.get("evidence_text") or "")
        setup_terms = (
            "test mass", "test mass material", "test mass weight",
            "effective length of test mass", "bending test equipment",
            "equipment model", "xrd scan type", "azimuthal rotation angle",
            "scan type", "rotation angle",
        )
        if any(term in metric for term in setup_terms):
            return True
        if "pdms test mass" in evidence or "ipc flexural endurance tester" in evidence:
            return True
        if "peltier stage" in evidence and unit in {"uv", "v"}:
            return True
        if metric == "imidization degree" and unit not in {"%", "percent"}:
            return True
        if metric == "breakdown strength" and unit in {"uv", "mv", "v"}:
            return True
        return False

    @staticmethod
    def _is_performance_repair_fact(fact: dict) -> bool:
        """Keep only quantitative outcomes during the strong repair pass."""
        if fact.get("fact_type") != "performance":
            return False
        metric_raw = str(fact.get("metric_or_parameter") or "").strip()
        metric = normalize_for_match(metric_raw).replace(" ", "_")
        if not metric or is_condition_parameter_name(metric_raw):
            return False
        if find_metric_canonical(metric_raw):
            return True
        setup_patterns = (
            r"(?:fiber|filler|matrix|additive|resin)_(?:content|loading|fraction)",
            r"(?:volume|weight|mass|molar)_fraction",
            r"(?:loading|test|impact|crosshead)_(?:speed|rate|velocity)",
            r"(?:period|cell|mesh|element|specimen|sample)_(?:size|count|number)",
            r"(?:level_set|geometry|model|formula|simulation)_(?:constant|parameter|coefficient)",
            r"number_of_(?:points|rows|cycles|samples)",
        )
        return not any(re.search(pattern, metric) for pattern in setup_patterns)

    # ------------------------------------------------------------------
    # Stage 2 (weak model): Direct extraction with sample context
    # ------------------------------------------------------------------

    @staticmethod
    async def _stage2_fact_candidates_weak(
        client, chunks: list[dict], samples: list[dict],
    ) -> list[dict]:
        """Weak mode atomic extraction; no sample catalog/card/group/final output."""
        _ = samples
        return await V7ExtractorService._stage2_fact_candidates(client, chunks, model_mode="weak")

    # ------------------------------------------------------------------
    # Stage 3: Sample assignment
    # ------------------------------------------------------------------

    @staticmethod
    async def _stage3_sample_assignment(
        client, samples: list[dict], facts: list[dict],
    ) -> list[dict]:
        """Deprecated: LLM catalog assignment. Pipeline uses deterministic assign_fact_to_sample."""
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
            parsed, _ = await V7ExtractorService._llm_json_tolerant(
                client,
                prompt,
                f"Assign these facts to samples:\n{json.dumps(compact_facts, ensure_ascii=False, indent=2)}",
                max_tokens=3000,
                timeout_seconds=settings.STRONG_LLM_TIMEOUT_SECONDS,
                stage="stage3_assignment",
            )
            batch_assignments = _response_rows(parsed, "assignments", "_items")
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
    def _normalize_for_match(text: str) -> str:
        """Normalize text for fuzzy matching: strip Unicode artifacts."""
        t = text.lower().strip()
        # Normalize common Unicode variants
        t = t.replace("°", " ").replace("℃", " ")  # ° and ℃
        t = t.replace("⁻", "").replace("⁹", "").replace("³", "")  # superscripts
        t = t.replace("–", "-").replace("—", "-")  # dashes
        t = re.sub(r"\s+", " ", t).strip()
        return t

    @staticmethod
    def _propagate_sample_card_backgrounds(sample_cards: list[dict]) -> list[dict]:
        """Copy only unanimous background fields within compatible sample forms."""
        bg_fields = (
            "material_system", "fiber_type", "composition_expression", "matrix_name",
            "matrix_content", "matrix_unit", "additive_expression", "solvent_or_aid",
            "process_route", "spinning_method", "process_parameters", "post_treatment",
            "structure_methods", "structure_features",
        )
        by_group: dict[str, list[dict]] = defaultdict(list)
        for card in sample_cards:
            group_id = card.get("sample_group_id") or "G000"
            by_group[group_id].append(card)

        def form_bucket(card: dict) -> str:
            value = normalize_for_match(card.get("fiber_type") or "")
            if re.search(r"\b(?:nano)?fib(?:er|re)|yarn|fibrous\b", value):
                return "fiber"
            if value in {"bulk", "powder", "particle", "particles"}:
                return "bulk"
            if value in {"solution", "precursor", "dispersion"}:
                return "solution"
            return value

        for group_id, group_cards in by_group.items():
            if group_id == "G000":
                continue
            if len(group_cards) < 2:
                continue
            for card in group_cards:
                target_form = form_bucket(card)
                known_forms = {
                    form_bucket(candidate)
                    for candidate in group_cards
                    if form_bucket(candidate)
                }
                compatible = [
                    candidate
                    for candidate in group_cards
                    if (
                        not target_form
                        and len(known_forms) <= 1
                        or target_form
                        and form_bucket(candidate) in {"", target_form}
                    )
                ]
                for field in bg_fields:
                    if card.get(field):
                        continue
                    values: dict[str, str] = {}
                    for candidate in compatible:
                        value = str(candidate.get(field) or "").strip()
                        if value:
                            values.setdefault(normalize_for_match(value), value)
                    if len(values) == 1:
                        card[field] = next(iter(values.values()))
        return sample_cards

    @staticmethod
    def _local_sample_assignment(facts: list[dict], samples: list[dict]) -> list[dict]:
        """Local text-matching fallback for sample assignment.

        Matches sample_id and aliases against fact evidence_text and subject_text.
        Uses fuzzy normalization for Unicode variants (°C, superscripts, etc).
        """
        # Build lookup: normalized name → sample
        sample_lookup: dict[str, dict] = {}
        sample_ids_raw: list[tuple[str, dict]] = []  # (normalized_sid, sample)

        for s in samples:
            sid = s.get("sample_id", "").strip()
            if sid:
                norm_sid = V7ExtractorService._normalize_for_match(sid)
                sample_lookup[norm_sid] = s
                sample_ids_raw.append((norm_sid, s))
                for alias in parse_sample_aliases(s.get("sample_aliases")):
                    alias = alias.strip()
                    if alias:
                        sample_lookup[V7ExtractorService._normalize_for_match(alias)] = s

        for f in facts:
            if f.get("_background_only"):
                continue
            if f.get("assignment_status") not in ("unassigned", None, ""):
                continue
            if f.get("assigned_sample_id"):
                continue

            # Search evidence_text and subject_text for sample mentions
            search_text = V7ExtractorService._normalize_for_match(
                (f.get("evidence_text") or "") + " " +
                (f.get("subject_text") or "") + " " +
                (f.get("source_location") or "")
            )

            # Also check candidate_sample_ids from the fact
            candidates = f.get("candidate_sample_ids") or []
            if isinstance(candidates, str):
                try:
                    candidates = json.loads(candidates)
                except (json.JSONDecodeError, TypeError):
                    candidates = [candidates]
            candidates_norm = [V7ExtractorService._normalize_for_match(c) for c in candidates if c]

            if is_characterization_peak_metric(
                str(f.get("metric_or_parameter") or ""),
                method=str(f.get("method") or ""),
                evidence=str(f.get("evidence_text") or ""),
            ):
                explicit_samples = {
                    sample.get("sample_id")
                    for norm_name, sample in sample_lookup.items()
                    if len(norm_name) >= 3 and norm_name in search_text
                }
                explicit_samples.discard(None)
                if len(explicit_samples) == 1:
                    f["assigned_sample_id"] = next(iter(explicit_samples))
                    f["assignment_confidence"] = 0.82
                    f["assignment_status"] = "assigned"
                continue

            best_match = None
            best_score = 0

            for norm_sid, sample in sample_lookup.items():
                if not _numbered_sample_is_explicit(
                    sample.get("sample_id"), search_text, candidates_norm
                ):
                    continue
                score = 0
                specificity = min(len(norm_sid) // 8, 4)
                # Exact normalized match in search text
                if norm_sid in search_text:
                    score += 5 + specificity
                # Check if candidate_sample_ids match this sample
                for cn in candidates_norm:
                    if cn == norm_sid:
                        score += 5 + specificity
                    elif cn.startswith(norm_sid):
                        score += 3
                    elif norm_sid.startswith(cn):
                        score += 2 + specificity
                    elif cn in norm_sid or norm_sid in cn:
                        score += 3
                # Check parts of sample ID in text (skip very short parts)
                parts = re.split(r"[-_/\s]+", norm_sid)
                for part in parts:
                    if len(part) >= 3 and part in search_text:
                        score += 1

                best_len = len(V7ExtractorService._normalize_for_match(best_match.get("sample_id", ""))) if best_match else 0
                if score > best_score or (score == best_score and len(norm_sid) > best_len):
                    best_score = score
                    best_match = sample

            if best_match and best_score >= 2:
                f["assigned_sample_id"] = best_match.get("sample_id")
                f["assignment_confidence"] = min(0.6 + best_score * 0.05, 0.9)
                f["assignment_status"] = "assigned"

        # Second pass: assign remaining unassigned facts by candidate_sample_ids
        # If a fact has candidate_sample_ids that partially match a sample, assign it
        for f in facts:
            if f.get("_background_only"):
                continue
            if is_characterization_peak_metric(
                str(f.get("metric_or_parameter") or ""),
                method=str(f.get("method") or ""),
                evidence=str(f.get("evidence_text") or ""),
            ):
                continue
            if f.get("assignment_status") not in ("unassigned", None, ""):
                continue
            if f.get("assigned_sample_id"):
                continue

            candidates = f.get("candidate_sample_ids") or []
            if isinstance(candidates, str):
                try:
                    candidates = json.loads(candidates)
                except (json.JSONDecodeError, TypeError):
                    candidates = [candidates]
            if not candidates:
                continue

            # Try substring matching on raw sample IDs
            best_match = None
            best_score = 0
            search_text = V7ExtractorService._normalize_for_match(
                (f.get("evidence_text") or "") + " "
                + (f.get("subject_text") or "") + " "
                + (f.get("source_location") or "")
            )
            for cand in candidates:
                cand_lower = cand.lower().strip()
                for sid_raw, sample in sample_ids_raw:
                    if not _numbered_sample_is_explicit(
                        sample.get("sample_id"), search_text, candidates
                    ):
                        continue
                    sid_lower = sid_raw.lower()
                    # Check if candidate is a prefix of sample_id or vice versa
                    specificity = min(len(sid_lower) // 8, 4)
                    if cand_lower == sid_lower:
                        score = 5 + specificity
                    elif cand_lower in sid_lower:
                        score = 3 + specificity
                    elif sid_lower in cand_lower:
                        score = 3
                    else:
                        # Check word overlap
                        cand_words = set(re.split(r"[-_/\s]+", cand_lower))
                        sid_words = set(re.split(r"[-_/\s]+", sid_lower))
                        overlap = cand_words & sid_words - {"", " "}
                        score = len(overlap)
                    best_len = len(V7ExtractorService._normalize_for_match(best_match.get("sample_id", ""))) if best_match else 0
                    if score > best_score or (score == best_score and len(sid_lower) > best_len):
                        best_score = score
                        best_match = sample

            if best_match and best_score >= 2:
                f["assigned_sample_id"] = best_match.get("sample_id")
                f["assignment_confidence"] = 0.55
                f["assignment_status"] = "assigned"

        return facts

    @staticmethod
    def _as_list(value: Any) -> list:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, list) else [parsed]
            except (json.JSONDecodeError, TypeError):
                return [value] if value.strip() else []
        return [value]

    @staticmethod
    def _serialize_sample_aliases(value: Any) -> str | None:
        aliases = parse_sample_aliases(value)
        return (
            json.dumps(aliases, ensure_ascii=False)
            if aliases
            else None
        )

    @staticmethod
    def _sanitize_sample_cards(sample_cards: list[dict]) -> list[dict]:
        """Apply the shared sample-ID sanitizer before catalog persistence."""
        from app.services.extractor_v7.sample_id_rules import sanitize_sample_id

        cleaned: list[dict] = []
        by_id: dict[str, dict] = {}
        for raw_card in sample_cards:
            card = dict(raw_card)
            original = str(card.get("sample_id") or "").strip()
            sample_id, _, _ = sanitize_sample_id(
                original,
                str(card.get("evidence_text") or ""),
            )
            if not sample_id or not is_material_sample_id(sample_id):
                continue
            aliases = set(parse_sample_aliases(card.get("sample_aliases")))
            if normalize_for_match(original) != normalize_for_match(sample_id):
                aliases.add(original)
            card["sample_id"] = sample_id
            card["sample_aliases"] = sorted(
                alias
                for alias in aliases
                if alias
                and normalize_for_match(alias) != normalize_for_match(sample_id)
            )
            key = normalize_for_match(sample_id)
            existing = by_id.get(key)
            if existing is None:
                by_id[key] = card
                cleaned.append(card)
                continue
            existing_aliases = set(
                parse_sample_aliases(existing.get("sample_aliases"))
            )
            existing["sample_aliases"] = sorted(existing_aliases | aliases)
            for field in (
                "sample_group_id", "material_system", "fiber_type",
                "variable_name", "variable_value", "variable_unit",
                "composition_expression", "process_route",
                "source_location", "evidence_text",
            ):
                if not existing.get(field) and card.get(field):
                    existing[field] = card[field]
        return cleaned

    @staticmethod
    def _append_unique(existing: str | None, addition: str | None) -> str:
        addition = (addition or "").strip()
        if not addition:
            return existing or ""
        existing = existing or ""
        existing_parts = [p.strip() for p in existing.split(";") if p.strip()]
        normalized = {V7ExtractorService._normalize_for_match(p) for p in existing_parts}
        if V7ExtractorService._normalize_for_match(addition) in normalized:
            return existing
        return "; ".join(existing_parts + [addition])

    @staticmethod
    def _format_fact_value(fact: dict) -> str:
        metric = (fact.get("metric_or_parameter") or fact.get("subject_text") or "").strip()
        value = (fact.get("value") or "").strip()
        unit = (fact.get("unit") or "").strip()
        if metric and value:
            return f"{metric}={value} {unit}".strip()
        if value:
            return f"{value} {unit}".strip()
        return metric

    @staticmethod
    def _best_sample_match(
        text: str,
        candidates: list,
        samples: list[dict],
    ) -> tuple[str | None, float]:
        search_text = V7ExtractorService._normalize_for_match(text)
        candidate_text = " ".join(str(c) for c in candidates if c)
        candidate_norm = V7ExtractorService._normalize_for_match(candidate_text)
        best_sid = None
        best_score = 0.0

        for sample in samples:
            if not _numbered_sample_is_explicit(
                sample.get("sample_id"), search_text, candidates
            ):
                continue
            names = [sample.get("sample_id", "")]
            names.extend(V7ExtractorService._as_list(sample.get("sample_aliases")))
            for name in names:
                norm_name = V7ExtractorService._normalize_for_match(str(name))
                if not norm_name:
                    continue
                specificity = min(len(norm_name) / 8, 4)
                score = 0.0
                if norm_name in search_text:
                    score += 5 + specificity
                if norm_name and norm_name in candidate_norm:
                    score += 5 + specificity
                for cand in candidates:
                    norm_cand = V7ExtractorService._normalize_for_match(str(cand))
                    if not norm_cand:
                        continue
                    if norm_cand == norm_name:
                        score += 5 + specificity
                    elif norm_name.startswith(norm_cand):
                        score += 2 + specificity
                    elif norm_cand.startswith(norm_name):
                        score += 2
                parts = [p for p in re.split(r"[-_/\s]+", norm_name) if len(p) >= 3]
                score += len([p for p in parts if p in search_text]) * 0.5
                if score > best_score or (
                    score == best_score and best_sid and len(norm_name) > len(best_sid)
                ):
                    best_sid = sample.get("sample_id")
                    best_score = score

        return best_sid, best_score

    @staticmethod
    def _repair_sample_assignment_specificity(
        facts: list[dict], samples: list[dict],
    ) -> list[dict]:
        """Prefer longer explicit sample names over generic control names."""
        for fact in facts:
            if fact.get("_background_only"):
                continue
            if (
                fact.get("extraction_method") in {
                    "AI_holistic_table", "rule_table_performance",
                }
                and fact.get("_source_table_row") is not None
            ):
                continue
            candidates = V7ExtractorService._as_list(fact.get("candidate_sample_ids"))
            text = " ".join([
                str(fact.get("evidence_text") or ""),
                str(fact.get("subject_text") or ""),
                str(fact.get("source_location") or ""),
            ])
            best_sid, best_score = V7ExtractorService._best_sample_match(text, candidates, samples)
            if not best_sid or best_score < 5:
                continue

            current = fact.get("assigned_sample_id")
            current_norm = V7ExtractorService._normalize_for_match(current or "")
            best_norm = V7ExtractorService._normalize_for_match(best_sid)
            if not current or current_norm != best_norm:
                # Only override when the new match is more specific or the old match is absent.
                if not current_norm or current_norm in best_norm or len(best_norm) > len(current_norm) + 3:
                    fact["assigned_sample_id"] = best_sid
                    fact["assignment_confidence"] = max(float(fact.get("assignment_confidence") or 0), 0.8)
                    fact["assignment_status"] = "assigned"
        return facts

    @staticmethod
    def _repair_sample_assignment_from_variable_context(
        facts: list[dict],
        samples: list[dict],
    ) -> list[dict]:
        """Bind scoped composition/load conditions to the matching sample card."""
        generic_variable_words = {
            "amount", "content", "fraction", "level", "loading",
            "concentration", "ratio", "percentage",
        }
        variable_samples: list[tuple[dict, str, str, tuple[str, ...]]] = []
        for sample in samples:
            value_match = re.fullmatch(
                r"[+-]?\d+(?:\.\d+)?",
                str(sample.get("variable_value") or "").strip(),
            )
            unit = re.sub(
                r"[\s.]",
                "",
                str(sample.get("variable_unit") or "").lower(),
            )
            if not value_match or unit not in {"%", "wt%", "vol%", "mol%"}:
                continue
            concepts = tuple(
                token
                for token in re.findall(
                    r"[a-z0-9]+",
                    str(sample.get("variable_name") or "").lower(),
                )
                if token not in generic_variable_words and len(token) >= 2
            )
            if concepts:
                variable_samples.append(
                    (sample, value_match.group(0), unit, concepts)
                )

        for fact in facts:
            if fact.get("_background_only"):
                continue
            if (
                fact.get("extraction_method") in {
                    "AI_holistic_table", "rule_table_performance",
                }
                and fact.get("_source_table_row") is not None
            ):
                continue
            condition = str(fact.get("condition") or "").strip()
            if not condition:
                continue
            condition_lower = condition.lower()
            matches: dict[str, dict] = {}
            for sample, value, unit, concepts in variable_samples:
                if not any(
                    re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
                              condition_lower)
                    for term in concepts
                ):
                    continue
                unit_pattern = {
                    "%": r"%",
                    "wt%": r"(?:wt\.?\s*%|%)",
                    "vol%": r"(?:vol\.?\s*%|%)",
                    "mol%": r"(?:mol\.?\s*%|%)",
                }[unit]
                if not re.search(
                    rf"(?<![\d.]){re.escape(value)}(?![\d.])\s*{unit_pattern}",
                    condition,
                    re.IGNORECASE,
                ):
                    continue
                sid = str(sample.get("sample_id") or "").strip()
                if sid:
                    matches[V7ExtractorService._normalize_for_match(sid)] = sample

            if len(matches) != 1:
                continue
            matched_sample = next(iter(matches.values()))
            sample_id = str(matched_sample.get("sample_id") or "")
            if (
                V7ExtractorService._normalize_for_match(
                    fact.get("assigned_sample_id") or ""
                )
                == V7ExtractorService._normalize_for_match(sample_id)
            ):
                continue
            fact["assigned_sample_id"] = sample_id
            fact["candidate_sample_ids"] = [sample_id]
            fact["assignment_confidence"] = max(
                float(fact.get("assignment_confidence") or 0),
                0.92,
            )
            fact["assignment_status"] = "assigned"
            fact["assignment_reason"] = V7ExtractorService._append_unique(
                fact.get("assignment_reason"),
                "sample_bound_from_variable_context",
            )
        return facts

    @staticmethod
    def _normalize_unit(unit: str | None) -> str:
        unit = (unit or "").strip()
        bracketed = re.fullmatch(r"\[\s*([^\[\]]+?)\s*\]", unit)
        if bracketed:
            unit = bracketed.group(1)
        if re.fullmatch(r"(?i)(?:%|percent)\s*(?:strain)?", unit) or re.fullmatch(
            r"(?i)strain\s*%", unit
        ):
            return "%"
        replacements = {
            "deg": "°",
            "degree": "°",
            "degrees": "°",
            "C": "°C",
            "mg/cm3": "mg cm^-3",
            "mg/cm^3": "mg cm^-3",
            "mW/m-K": "mW m^-1 K^-1",
            "mW/mK": "mW m^-1 K^-1",
        }
        return replacements.get(unit, unit)

    @staticmethod
    def _clean_value_variants(raw_value: Any, raw_unit: Any) -> list[dict]:
        """Return cleaned value variants; ranges are expanded into two rows."""
        original = "" if raw_value is None else str(raw_value).strip()
        unit = V7ExtractorService._normalize_unit(str(raw_unit or "").strip())
        text = original.replace("−", "-").replace("–", "-").replace("—", "-")
        text = text.replace(",", "").strip()
        operator = "="
        for pattern, normalized_operator in (
            (r"^(?:at\s+most|no\s+more\s+than)\s+", "<="),
            (r"^(?:at\s+least|no\s+less\s+than)\s+", ">="),
            (r"^(?:less\s+than|below|under)\s+", "<"),
            (r"^(?:more\s+than|greater\s+than|above|over)\s+", ">"),
            (r"^(?:about|approximately|roughly|around)\s+", "≈"),
        ):
            normalized = re.sub(pattern, "", text, count=1, flags=re.IGNORECASE)
            if normalized != text:
                operator = normalized_operator
                text = normalized.strip()
                break
        from app.services.extractor_v7.value_parse import parse_scientific_value

        sci = parse_scientific_value(text)
        if sci:
            text = sci
        if text.startswith(("<=", ">=")):
            operator, text = text[:2], text[2:].strip()
        elif text.startswith("<"):
            operator, text = "<", text[1:].strip()
        elif text.startswith(">"):
            operator, text = ">", text[1:].strip()
        elif text.startswith(("~", "≈")):
            operator, text = "≈", text[1:].strip()

        number = r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
        mean_std_match = re.match(
            rf"^\s*({number})\s*(?:\(\s*({number})\s*\)|(?:±|\+/-)\s*({number}))\s*([^\d]*)\s*$",
            text,
        )
        if mean_std_match:
            suffix_unit = V7ExtractorService._normalize_unit(mean_std_match.group(4).strip())
            return [{
                "raw_value": original,
                "value_operator": operator,
                "clean_value": mean_std_match.group(1),
                "clean_unit": unit or suffix_unit,
                "standard_deviation": mean_std_match.group(2) or mean_std_match.group(3),
            }]

        range_match = re.match(
            rf"^\s*({number})\s*(?:-|to|~)\s*({number})\s*([^\d]*)\s*$",
            text,
            flags=re.IGNORECASE,
        )
        if range_match:
            suffix_unit = V7ExtractorService._normalize_unit(range_match.group(3).strip())
            clean_unit = unit or suffix_unit
            return [
                {
                    "raw_value": original,
                    "value_operator": "range_min" if operator == "=" else f"{operator} range_min",
                    "clean_value": range_match.group(1),
                    "clean_unit": clean_unit,
                },
                {
                    "raw_value": original,
                    "value_operator": "range_max" if operator == "=" else f"{operator} range_max",
                    "clean_value": range_match.group(2),
                    "clean_unit": clean_unit,
                },
            ]

        single_match = re.match(rf"^\s*({number})\s*([^\d%]*)\s*$", text)
        if single_match:
            suffix_unit = V7ExtractorService._normalize_unit(single_match.group(2).strip())
            clean_unit = unit or suffix_unit
            return [{
                "raw_value": original,
                "value_operator": operator,
                "clean_value": single_match.group(1),
                "clean_unit": clean_unit,
            }]

        percent_match = re.match(rf"^\s*({number})\s*%+\s*$", text)
        if percent_match:
            return [{
                "raw_value": original,
                "value_operator": operator,
                "clean_value": percent_match.group(1),
                "clean_unit": unit or "%",
            }]

        return [{
            "raw_value": original,
            "value_operator": operator,
            "clean_value": text,
            "clean_unit": unit,
        }]

    @staticmethod
    def _is_numeric_clean_value(value: Any) -> bool:
        text = "" if value is None else str(value).strip()
        return bool(re.fullmatch(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", text))

    @staticmethod
    def _resolve_fact_sample_id(fact: dict, sample_cards: list[dict]) -> str:
        def exact_catalog_match(value: Any) -> str:
            key = normalize_for_match(str(value or ""))
            if not key:
                return ""
            exact = {
                str(card.get("sample_id"))
                for card in sample_cards
                if card.get("sample_id") and normalize_for_match(card.get("sample_id")) == key
            }
            if len(exact) == 1:
                return next(iter(exact))
            aliases = {
                str(card.get("sample_id"))
                for card in sample_cards
                if card.get("sample_id") and any(
                    normalize_for_match(alias) == key
                    for alias in parse_sample_aliases(card.get("sample_aliases"))
                )
            }
            if len(aliases) == 1:
                return next(iter(aliases))
            base_aliases = {
                sample_id for sample_id in aliases
                if not is_numbered_sample_variant(sample_id)
            }
            return next(iter(base_aliases)) if len(base_aliases) == 1 else ""

        assigned = fact.get("assigned_sample_id")
        assigned_match = exact_catalog_match(assigned)
        if assigned_match:
            return assigned_match
        candidates = V7ExtractorService._as_list(fact.get("candidate_sample_ids"))
        candidate_matches = {
            match for match in (exact_catalog_match(value) for value in candidates) if match
        }
        if len(candidate_matches) == 1:
            return next(iter(candidate_matches))
        best_sid, best_score = V7ExtractorService._best_sample_match(
            " ".join([
                str(fact.get("evidence_text") or ""),
                str(fact.get("subject_text") or ""),
                str(fact.get("source_location") or ""),
            ]),
            candidates,
            sample_cards,
        )
        if best_sid and best_score >= 3:
            return best_sid
        if len(sample_cards) == 1:
            only_sid = sample_cards[0].get("sample_id")
            if only_sid:
                return only_sid
        return assigned or ""

    @staticmethod
    def _build_result_facts(facts: list[dict], sample_cards: list[dict]) -> list[dict]:
        exportable_types = {"performance", "process", "structure", "composition"}
        result_facts: list[dict] = []
        for fact in facts:
            ftype = fact.get("fact_type", "performance")
            if ftype not in exportable_types:
                continue
            if fact.get("_background_only"):
                continue
            if fact.get("_hard_reject"):
                continue
            if fact.get("_evidence_audit_failed"):
                # Still include but route to QA for review
                pass  # let the export_tier logic below handle routing
            metric_raw = fact.get("metric_or_parameter", "") or ""
            is_condition_parameter = is_condition_parameter_name(metric_raw)
            if ftype == "process":
                metric = find_process_parameter_canonical(metric_raw) or metric_raw
                category = "process"
            elif ftype == "structure":
                metric = find_structure_feature_canonical(metric_raw) or metric_raw
                category = "structure"
            elif ftype == "composition":
                metric = metric_raw
                category = "composition"
            else:
                metric = (
                    metric_raw
                    if is_condition_parameter
                    else (find_metric_canonical(metric_raw) or metric_raw)
                )
                category = fact.get("category") or find_category_for_metric(metric)
            priority = "Secondary" if is_condition_parameter else classify_metric_priority(metric)
            qa_reasons: list[str] = []
            if ftype != "performance":
                priority = "Secondary"
                qa_reasons.append(f"fact_type={ftype}")
            if is_condition_parameter:
                qa_reasons.append("condition_parameter")
            background_or_reference = is_background_or_reference_fact(fact)
            if background_or_reference:
                priority = "Narrative"
                qa_reasons.append("background_or_reference")
            if fact.get("_alignment_review_required"):
                qa_reasons.append("alignment_review_required")
            if fact.get("_metric_unit_mismatch"):
                qa_reasons.append("metric_unit_mismatch")
            if is_rough_source_location(fact.get("source_location")):
                qa_reasons.append("rough_source_location")
            output_channel = fact.get("_output_channel") or "performance"
            if output_channel == "characterization_feature":
                priority = "Secondary"
                qa_reasons.append("characterization_feature")
            elif output_channel == "structure_feature":
                priority = "Secondary"
                qa_reasons.append("structure_feature")
            elif output_channel == "formula_or_method_parameter":
                priority = "Secondary"
                qa_reasons.append("formula_or_method_parameter")
            # Downgrade based on data source type
            data_src = fact.get("_data_source_type") or ""
            if data_src in ("background_reference", "comparison_literature"):
                priority = "Narrative"
                qa_reasons.append(data_src)
            elif data_src in ("method_parameter", "experimental_condition"):
                if data_src not in qa_reasons:
                    qa_reasons.append(data_src)
            # Downgrade based on export tier
            export_tier = fact.get("_export_tier") or "A"
            if export_tier == "C":
                priority = "Narrative"
                qa_reasons.append("export_tier_C")
            elif export_tier == "B":
                if priority == "Core":
                    qa_reasons.append("export_tier_B_review")
            # Handle evidence audit failure
            if fact.get("_evidence_audit_failed"):
                priority = "Narrative"
                qa_reasons.append("evidence_audit_failed")
            # Handle checklist failure
            if fact.get("_checklist_failed"):
                qa_reasons.append("checklist_failed")
                checklist_failures = fact.get("_checklist_failures") or []
                if isinstance(checklist_failures, str):
                    checklist_failures = [checklist_failures]
                for failure in checklist_failures:
                    qa_reasons.append(f"checklist:{failure}")
            force_qa = ftype != "performance" or any(
                reason in qa_reasons
                for reason in (
                    "background_or_reference", "rough_source_location",
                    "fact_type=process", "fact_type=structure", "fact_type=composition",
                    "alignment_review_required", "metric_unit_mismatch",
                    "characterization_feature", "formula_or_method_parameter",
                    "checklist_failed", "export_tier_B_review",
                )
            )
            explicit_background_reference = bool(
                fact.get("_explicit_background_reference")
            )
            if explicit_background_reference:
                # Explicitly cited external values stay in FactCandidate only.
                export_target = "Not exported"
            elif output_channel == "characterization_feature":
                export_target = "Characterization_Features"
            elif output_channel == "structure_feature":
                export_target = "Characterization_Features"  # Structure features also go to characterization
            elif output_channel == "formula_or_method_parameter":
                export_target = "Formula_Method_Parameters"
            elif fact.get("_evidence_audit_failed"):
                export_target = "Result_Facts_QA"  # Rejected but kept for review
            else:
                export_target = (
                    "Core_Final_Records" if priority == "Core" and not force_qa
                    else "Result_Facts_QA"
                )
            sample_id = V7ExtractorService._resolve_fact_sample_id(fact, sample_cards)
            assignment_status = fact.get("assignment_status", "unassigned")
            assignment_confidence = fact.get("assignment_confidence")
            if sample_id and assignment_status in ("unassigned", None, ""):
                assignment_status = "uncertain"
                assignment_confidence = assignment_confidence or 0.55
            if not sample_id:
                assignment_status = fact.get("assignment_status", "unassigned")
            cleaned_values = V7ExtractorService._clean_value_variants(
                fact.get("value", ""), fact.get("unit", ""),
            )
            for idx, cleaned in enumerate(cleaned_values, 1):
                is_numeric = V7ExtractorService._is_numeric_clean_value(cleaned.get("clean_value"))
                current_priority = priority if is_numeric else "Narrative"
                if not is_numeric:
                    continue
                if not sample_id:
                    current_export_target = "Result_Facts_QA"
                else:
                    current_export_target = (
                        export_target if is_numeric else "Not exported"
                    )
                if fact.get("extraction_method") == "AI_sample_card":
                    current_priority = "Secondary"
                    current_export_target = "Result_Facts_QA" if is_numeric and sample_id else "Not exported"
                    fact["confidence"] = min(float(fact.get("confidence", 0.45) or 0.45), 0.45)
                condition = fact.get("condition") or ""
                if is_condition_parameter:
                    condition = V7ExtractorService._append_unique(
                        condition, "condition_parameter",
                    )
                if fact.get("extraction_method") == "AI_figure":
                    condition = V7ExtractorService._append_unique(
                        condition, "figure_estimated",
                    )
                    if current_priority != "Core":
                        current_export_target = "Result_Facts_QA"
                if len(cleaned_values) > 1:
                    condition = V7ExtractorService._append_unique(
                        condition, cleaned["value_operator"],
                    )
                if cleaned.get("standard_deviation"):
                    condition = V7ExtractorService._append_unique(
                        condition,
                        f"standard_deviation={cleaned['standard_deviation']}",
                    )
                for reason in qa_reasons:
                    condition = V7ExtractorService._append_unique(condition, reason)
                result_facts.append({
                    "fact_id": fact.get("fact_id", ""),
                    "sample_id": sample_id,
                    "assigned_sample_id": fact.get("assigned_sample_id") or sample_id,
                    "assignment_status": assignment_status,
                    "assignment_confidence": assignment_confidence,
                    "metric_priority": current_priority,
                    "raw_metric": metric_raw,
                    "canonical_metric": metric,
                    "performance_category": category,
                    "raw_value": cleaned["raw_value"],
                    "value_operator": cleaned["value_operator"],
                    "clean_value": cleaned["clean_value"],
                    "clean_unit": cleaned["clean_unit"],
                    "performance_method": fact.get("method") or "",
                    "performance_condition": condition,
                    "performance_evidence": fact.get("evidence_text") or "",
                    "evidence_text": fact.get("evidence_text") or "",
                    "source_location": fact.get("source_location") or "",
                    "source_block_id": fact.get("_source_block_id"),
                    "source_page": fact.get("_source_page"),
                    "source_bbox": fact.get("_source_bbox"),
                    "extraction_method": fact.get("extraction_method", "AI_text"),
                    "ai_confidence": fact.get("confidence", 0.5),
                    "export_target": current_export_target,
                    "qa_reason": ";".join(qa_reasons),
                    "range_part": idx if len(cleaned_values) > 1 else None,
                    "_source_fact": fact,
                })
        result_facts = V7ExtractorService._drop_redundant_intrinsic_qa_results(
            result_facts
        )
        return V7ExtractorService._dedupe_result_restatements(result_facts)

    @staticmethod
    def _dedupe_result_restatements(result_facts: list[dict]) -> list[dict]:
        """Collapse repeated prose mentions while retaining distinct conditions."""

        def base_key(result: dict) -> tuple[str, str, str, str]:
            return (
                normalize_for_match(result.get("sample_id") or ""),
                normalize_for_match(result.get("canonical_metric") or ""),
                normalize_for_match(result.get("clean_value") or ""),
                normalize_for_match(result.get("clean_unit") or ""),
            )

        def condition_text(result: dict) -> str:
            qa_reasons = {
                reason.strip()
                for reason in str(result.get("qa_reason") or "").split(";")
                if reason.strip()
            }
            return "; ".join(
                part
                for part in (
                    segment.strip()
                    for segment in str(
                        result.get("performance_condition") or ""
                    ).split(";")
                )
                if part and part not in qa_reasons
            )

        def numeric_context(result: dict) -> set[str]:
            return set(re.findall(
                r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)",
                condition_text(result).replace(",", ""),
            ))

        def text_tokens(value: str) -> set[str]:
            return set(re.findall(r"[a-z0-9]+", normalize_for_match(value)))

        def categorical_context(value: str) -> dict[str, str]:
            normalized = " ".join(
                re.findall(r"[a-z0-9]+", normalize_for_match(value))
            )
            groups = {
                "mode": ("mode iii", "mode ii", "mode i"),
                "moisture": ("dry", "wet"),
                "direction": (
                    "longitudinal",
                    "transverse",
                    "warp",
                    "weft",
                    "axial",
                    "radial",
                ),
                "treatment": (
                    "acid treated",
                    "alkali treated",
                    "silane treated",
                    "plasma treated",
                    "heat treated",
                    "untreated",
                    "treated",
                ),
                "sequence": ("before", "after"),
                "cycle": ("loading", "unloading"),
                "mechanics": ("tension", "compression"),
            }
            context = {}
            for group, variants in groups.items():
                for variant in variants:
                    if re.search(
                        rf"(?<![a-z0-9]){re.escape(variant)}(?![a-z0-9])",
                        normalized,
                    ):
                        context[group] = variant
                        break
            return context

        def compatible(first: dict, second: dict) -> bool:
            first_numbers = numeric_context(first)
            second_numbers = numeric_context(second)
            if first_numbers and second_numbers:
                return (
                    first_numbers.issubset(second_numbers)
                    or second_numbers.issubset(first_numbers)
                )
            first_condition = text_tokens(condition_text(first))
            second_condition = text_tokens(condition_text(second))
            if first_condition and second_condition:
                first_categories = categorical_context(condition_text(first))
                second_categories = categorical_context(condition_text(second))
                shared_groups = set(first_categories) & set(second_categories)
                if any(
                    first_categories[group] != second_categories[group]
                    for group in shared_groups
                ):
                    return False
                # With no numeric or categorical conflict, identical
                # sample/metric/value/unit rows are prose restatements even
                # when one condition uses different explanatory wording.
                return True
            first_method = text_tokens(
                str(first.get("performance_method") or "")
            )
            second_method = text_tokens(
                str(second.get("performance_method") or "")
            )
            return (
                not first_method
                or not second_method
                or first_method.issubset(second_method)
                or second_method.issubset(first_method)
            )

        def quality_score(result: dict) -> tuple[int, int, int, float]:
            target_score = {
                "Core_Final_Records": 2,
                "Result_Facts_QA": 1,
            }.get(str(result.get("export_target") or ""), 0)
            priority_score = {
                "Core": 2,
                "Secondary": 1,
            }.get(str(result.get("metric_priority") or ""), 0)
            specificity = len(numeric_context(result)) + len(
                text_tokens(condition_text(result))
            )
            confidence = float(result.get("ai_confidence") or 0.0)
            return target_score, priority_score, specificity, confidence

        kept: list[dict] = []
        indexes_by_key: dict[tuple[str, str, str, str], list[int]] = defaultdict(
            list
        )
        for result in result_facts:
            key = base_key(result)
            duplicate_index = next(
                (
                    index
                    for index in indexes_by_key[key]
                    if compatible(kept[index], result)
                ),
                None,
            )
            if duplicate_index is None:
                indexes_by_key[key].append(len(kept))
                kept.append(result)
                continue
            if quality_score(result) > quality_score(kept[duplicate_index]):
                kept[duplicate_index] = result
        return kept

    @staticmethod
    def _drop_redundant_intrinsic_qa_results(result_facts: list[dict]) -> list[dict]:
        """Drop uncertain restatements when the same material constant is already clean."""
        intrinsic_metrics = {"density", "youngs modulus", "poissons ratio"}
        harmless_qa_reasons = {"checklist_failed", "export_tier_B_review"}

        def key(result: dict) -> tuple[str, str, str, str]:
            return (
                normalize_for_match(result.get("sample_id") or ""),
                normalize_for_match(result.get("canonical_metric") or ""),
                normalize_for_match(result.get("clean_value") or ""),
                normalize_for_match(result.get("clean_unit") or ""),
            )

        clean_keys = {
            key(result)
            for result in result_facts
            if result.get("export_target") == "Core_Final_Records"
            and normalize_for_match(result.get("canonical_metric") or "")
            in intrinsic_metrics
        }
        kept: list[dict] = []
        seen_harmless_qa_keys: set[tuple[str, str, str, str]] = set()
        for result in result_facts:
            result_key = key(result)
            is_intrinsic = result_key[1] in intrinsic_metrics
            reasons = {
                reason.strip()
                for reason in str(result.get("qa_reason") or "").split(";")
                if reason.strip()
            }
            is_harmless_intrinsic_qa = (
                result.get("export_target") == "Result_Facts_QA"
                and is_intrinsic
                and bool(reasons)
                and reasons.issubset(harmless_qa_reasons)
            )
            if is_harmless_intrinsic_qa and (
                result_key in clean_keys or result_key in seen_harmless_qa_keys
            ):
                continue
            kept.append(result)
            if is_harmless_intrinsic_qa:
                seen_harmless_qa_keys.add(result_key)
        return kept

    @staticmethod
    def _ensure_final_record_schema(record: dict) -> tuple[dict, list[str]]:
        missing = []
        for field in V7ExtractorService.FINAL_RECORD_FIELDS:
            if field not in record:
                record[field] = ""
                missing.append(field)
        return record, missing

    @staticmethod
    def _fill_rate(rows: list[dict], fields: list[str]) -> float:
        if not rows or not fields:
            return 0.0
        total = len(rows) * len(fields)
        filled = 0
        for row in rows:
            for field in fields:
                value = row.get(field)
                if value not in (None, ""):
                    filled += 1
        return round(filled / total, 4)

    # ------------------------------------------------------------------
    # Stage 4: Record generation
    # ------------------------------------------------------------------

    @staticmethod
    def _stage4_generate_records(
        paper_id: int,
        project_id: int,
        paper_metadata: dict,
        sample_cards: list[dict],
        facts: list[dict],
        sample_mentions: list[dict] | None = None,
        variable_candidates: list[dict] | None = None,
        sample_groups: list[dict] | None = None,
    ) -> tuple[list[dict], dict]:
        """Generate 40-column candidate records from assigned facts.

        Returns (records, report_data).
        """
        sample_mentions = sample_mentions or []
        variable_candidates = variable_candidates or []
        sample_groups = sample_groups or []
        sample_info: dict[str, dict] = {
            s.get("sample_id", ""): s for s in sample_cards if s.get("sample_id")
        }
        group_ids: set[str] = {
            s.get("sample_group_id", "Group-A") for s in sample_cards
        }
        result_facts = V7ExtractorService._build_result_facts(facts, sample_cards)
        char_entries_by_sample: dict[str, list[str]] = {}
        char_methods_by_sample: dict[str, list[str]] = {}
        for rf in result_facts:
            sid = rf.get("sample_id") or ""
            if not sid:
                continue
            if rf.get("export_target") == "Characterization_Features":
                char_entries_by_sample.setdefault(sid, []).append(
                    format_characterization_entry(rf),
                )
                method = infer_characterization_method(rf)
                if method:
                    char_methods_by_sample.setdefault(sid, []).append(method)
        for card in sample_cards:
            sid = card.get("sample_id") or ""
            if sid in char_entries_by_sample:
                card["characterization_features"] = merge_characterization_features(
                    card.get("characterization_features", ""),
                    char_entries_by_sample[sid],
                )
            if sid in char_methods_by_sample:
                card["structure_methods"] = merge_characterization_features(
                    card.get("structure_methods", ""),
                    char_methods_by_sample[sid],
                )
        records: list[dict] = []
        record_idx = 0
        missing_evidence_count = 0
        rough_source_count = 0
        schema_missing_total = 0

        for result_fact in result_facts:
            export_target = result_fact.get("export_target")
            if export_target in ("Characterization_Features", "Formula_Method_Parameters"):
                continue
            if export_target not in ("Core_Final_Records", "Result_Facts_QA"):
                continue

            source_fact = result_fact.get("_source_fact", {})
            sample_id = result_fact.get("sample_id") or ""
            s = sample_info.get(sample_id, {})

            record_idx += 1
            metric = result_fact.get("canonical_metric", "")
            category = result_fact.get("performance_category", "") or find_category_for_metric(metric)
            evidence = result_fact.get("evidence_text", "") or ""
            perf_evidence = result_fact.get("performance_evidence", "") or evidence
            source = result_fact.get("source_location", "") or ""
            extraction_method = result_fact.get("extraction_method", "AI_text")

            # QC checks
            validation_issues = validate_fact(source_fact)
            if not sample_id:
                validation_issues.append("样品归属缺失")
            if s.get("_group_provisional"):
                validation_issues.append("样品组归属需人工确认")
            if is_rough_source_location(source):
                rough_source_count += 1
                if "来源位置过粗" not in validation_issues:
                    validation_issues.append("来源位置过粗")
            review_status = determine_review_status(
                source_fact, result_fact.get("assignment_confidence"), validation_issues
            )

            if not evidence and not perf_evidence:
                missing_evidence_count += 1

            comp_expr = s.get("composition_expression", "")
            var_name = s.get("variable_name", "") or ""
            var_value = s.get("variable_value", "") or ""
            var_unit = s.get("variable_unit", "") or ""
            if not var_name:
                inferred_name, inferred_value, inferred_unit = infer_variable_from_sample_id(sample_id)
                var_name = inferred_name or var_name
                var_value = var_value or inferred_value
                var_unit = var_unit or inferred_unit
            comment_parts = [
                f"metric_priority={result_fact.get('metric_priority')}",
                f"raw_value={result_fact.get('raw_value')}",
                f"value_operator={result_fact.get('value_operator')}",
                f"export_target={result_fact.get('export_target')}",
                f"source_location={source}",
            ]
            if result_fact.get("qa_reason"):
                comment_parts.append(f"qa_reason={result_fact.get('qa_reason')}")
            if result_fact.get("value_operator") not in ("=", None, ""):
                comment_parts.append("范围/近似/不等号数值，需按原文复核")
            if extraction_method == "AI_figure":
                comment_parts.append("图中估读，需人工复核")
            if extraction_method == "AI_sample_card":
                comment_parts.append("来自样品卡摘要，需人工复核")
            if s.get("_group_evidence"):
                comment_parts.append(f"group_evidence={s.get('_group_evidence')}")

            record = {
                "project_id": project_id,
                "source_paper_id": paper_id,
                "record_id": f"V7-EXT-{paper_id}-{record_idx}",
                "paper_id_str": paper_metadata.get("paper_id_biz") or f"P{paper_id:04d}",
                "paper_title": paper_metadata.get("paper_title", ""),
                "doi_or_url": paper_metadata.get("doi_or_url", ""),
                "year": str(paper_metadata.get("year") or ""),
                "journal": paper_metadata.get("journal", ""),
                "sample_group_id": s.get("sample_group_id", "Unassigned"),
                "sample_id": sample_id,
                "material_system": s.get("material_system", ""),
                "fiber_type": s.get("fiber_type", ""),
                "variable_name": var_name,
                "variable_value": var_value,
                "variable_unit": var_unit,
                "composition_expression": comp_expr,
                "matrix_name": s.get("matrix_name", ""),
                "matrix_content": s.get("matrix_content", ""),
                "matrix_unit": s.get("matrix_unit", ""),
                "additive_expression": s.get("additive_expression", ""),
                "solvent_or_aid": s.get("solvent_or_aid", ""),
                "composition_evidence": s.get("composition_evidence", ""),
                "process_route": s.get("process_route", ""),
                "spinning_method": s.get("spinning_method", ""),
                "process_parameters": s.get("process_parameters", ""),
                "post_treatment": s.get("post_treatment", ""),
                "process_evidence": s.get("process_evidence", ""),
                "structure_methods": s.get("structure_methods", ""),
                "structure_features": s.get("structure_features", ""),
                "characterization_features": s.get("characterization_features", ""),
                "structure_evidence": s.get("structure_evidence", ""),
                "performance_category": category,
                "performance_metric": metric,
                "performance_value": result_fact.get("clean_value", ""),
                "performance_unit": result_fact.get("clean_unit", ""),
                "performance_method": result_fact.get("performance_method") or "",
                "performance_condition": result_fact.get("performance_condition") or "",
                "performance_evidence": perf_evidence,
                "extraction_method": extraction_method,
                "evidence_text": evidence,
                "ai_confidence": result_fact.get("ai_confidence", 0.5),
                "review_status": review_status,
                "reviewer_comment": "; ".join(comment_parts),
                "source_location": source,
                "_source_block_id": result_fact.get("source_block_id"),
                "_source_page": result_fact.get("source_page"),
                "_source_bbox": result_fact.get("source_bbox"),
                "_fact_id": result_fact.get("fact_id", ""),
                "_metric_priority": result_fact.get("metric_priority"),
                "_export_target": result_fact.get("export_target"),
                "_validation_issues": validation_issues,
            }
            record, missing_fields = V7ExtractorService._ensure_final_record_schema(record)
            schema_missing_total += len(missing_fields)
            records.append(record)

        # Count statuses
        status_counts = defaultdict(int)
        for r in records:
            status_counts[r["review_status"]] += 1

        category_counts: dict[str, int] = defaultdict(int)
        for r in records:
            category_counts[r["performance_category"]] += 1

        priority_counts: dict[str, int] = defaultdict(int)
        for rf in result_facts:
            priority_counts[rf.get("metric_priority", "Secondary")] += 1

        serializable_result_facts = [
            {k: v for k, v in rf.items() if not k.startswith("_")}
            for rf in result_facts
        ]
        paper_meta_fields = ["paper_title", "doi_or_url", "year", "journal"]
        paper_metadata_missing = [
            field for field in paper_meta_fields if not paper_metadata.get(field)
        ]
        sample_card_fill_fields = [
            "material_system", "fiber_type", "composition_expression", "matrix_name",
            "additive_expression", "solvent_or_aid", "process_route",
            "spinning_method", "process_parameters", "post_treatment",
            "structure_methods", "structure_features",
        ]
        final_fill_fields = [
            field for field in V7ExtractorService.FINAL_RECORD_FIELDS
            if field not in {"record_id", "paper_id_str", "reviewer_comment"}
        ]
        sample_card_fill_rate = V7ExtractorService._fill_rate(sample_cards, sample_card_fill_fields)
        final_record_fill_rate = V7ExtractorService._fill_rate(records, final_fill_fields)
        core_metrics_present = sorted({
            rf.get("canonical_metric", "")
            for rf in result_facts
            if rf.get("metric_priority") == "Core" and rf.get("canonical_metric")
        })
        common_core_targets = [
            "density", "porosity", "shrinkage", "thermal_shrinkage",
            "fiber_diameter", "fiber_length", "thermal_conductivity",
            "surface_temperature", "water_contact_angle", "dielectric_constant",
            "dielectric_loss", "tensile_strength", "compressive_strength",
            "compressive_stress", "electrical_conductivity",
        ]
        common_core_found = [m for m in common_core_targets if m in core_metrics_present]
        evidence_records = len([r for r in records if r.get("evidence_text")])
        figure_estimated_count = len([
            r for r in records
            if r.get("extraction_method") == "AI_figure"
            or "图中估读" in (r.get("reviewer_comment") or "")
        ])
        provisional_group_count = len([g for g in sample_groups if g.get("is_provisional")])
        unassigned_facts = [
            f for f in facts
            if f.get("fact_type") == "performance"
            and (f.get("assignment_status") == "unassigned" or not f.get("assigned_sample_id"))
        ]
        missing_metric_count = len([r for r in records if not r.get("performance_metric")])
        missing_value_count = len([r for r in records if not r.get("performance_value")])
        missing_unit_count = len([r for r in records if not r.get("performance_unit")])
        field_alignment_status = "pass" if schema_missing_total == 0 else "filled_missing_fields"

        report_data = {
            "sample_count": len(sample_cards),
            "group_count": len(group_ids),
            "sample_mentions_count": len(sample_mentions),
            "variable_candidates_count": len(variable_candidates),
            "provisional_group_count": provisional_group_count,
            "fact_count": len(facts),
            "result_fact_count": len(result_facts),
            "assigned_count": len([rf for rf in result_facts if rf.get("sample_id")]),
            "unassigned_count": len(unassigned_facts),
            "record_count": len(records),
            "core_record_count": len([r for r in records if r.get("_metric_priority") == "Core"]),
            "secondary_record_count": len([rf for rf in result_facts if rf.get("metric_priority") == "Secondary"]),
            "qa_result_fact_count": len([rf for rf in result_facts if rf.get("export_target") != "Core_Final_Records"]),
            "missing_evidence_count": missing_evidence_count,
            "rough_source_location_count": rough_source_count,
            "figure_estimated_count": figure_estimated_count,
            "pending_count": status_counts.get("待审核", 0) + status_counts.get("pending", 0),
            "uncertain_count": status_counts.get("存疑", 0) + status_counts.get("uncertain", 0),
            "missing_count": status_counts.get("缺失", 0) + status_counts.get("missing", 0),
            "approved_count": status_counts.get("approved", 0),
            "evidence_text_record_ratio": round(evidence_records / len(records), 4) if records else 0.0,
            "performance_metric_missing_count": missing_metric_count,
            "performance_value_missing_count": missing_value_count,
            "performance_unit_missing_count": missing_unit_count,
            "category_counts": dict(category_counts),
            "metric_priority_counts": dict(priority_counts),
            "paper_metadata_missing_rate": round(len(paper_metadata_missing) / len(paper_meta_fields), 4),
            "paper_metadata_complete_rate": round(1 - len(paper_metadata_missing) / len(paper_meta_fields), 4),
            "paper_metadata_missing_fields": paper_metadata_missing,
            "sample_card_field_fill_rate": sample_card_fill_rate,
            "final_record_field_fill_rate": final_record_fill_rate,
            "schema_alignment_status": field_alignment_status,
            "schema_missing_field_count": schema_missing_total,
            "core_metric_coverage": {
                "present": core_metrics_present,
                "common_core_found": common_core_found,
                "common_core_missing": [m for m in common_core_targets if m not in core_metrics_present],
                "common_core_coverage_rate": round(len(common_core_found) / len(common_core_targets), 4),
            },
            "sample_mentions": sample_mentions,
            "variable_candidates": variable_candidates,
            "sample_groups": sample_groups,
            "sample_cards": sample_cards,
            "result_facts": serializable_result_facts,
            "unassigned_facts": [
                {
                    "fact_id": f.get("fact_id"),
                    "fact_type": f.get("fact_type"),
                    "metric_or_parameter": f.get("metric_or_parameter"),
                    "value": f.get("value"),
                    "unit": f.get("unit"),
                    "evidence_text": (f.get("evidence_text") or "")[:200],
                }
                for f in unassigned_facts
            ],
        }
        report_data["quality_conclusions"] = V7ExtractorService._build_quality_conclusions(report_data)
        report_data["manual_review_recommendations"] = V7ExtractorService._build_manual_review_recommendations(report_data)
        return records, report_data

    @staticmethod
    def _build_quality_conclusions(report_data: dict) -> list[str]:
        conclusions: list[str] = []
        if report_data.get("sample_card_field_fill_rate", 0) < 0.6:
            conclusions.append("不完整，需要人工复核")
        if report_data.get("missing_evidence_count", 0) > 0:
            conclusions.append("证据不足")
        if report_data.get("provisional_group_count", 0) > 0:
            conclusions.append("样品组存疑")
        if report_data.get("unassigned_count", 0) > 0:
            conclusions.append("需人工复核")
        if report_data.get("rough_source_location_count", 0) > 0:
            conclusions.append("证据定位不足")
        if report_data.get("record_count", 0) < 3 and report_data.get("figure_estimated_count", 0) > 0:
            conclusions.append("覆盖不足")
        if report_data.get("figure_estimated_count", 0) and report_data.get("record_count", 0):
            if report_data["figure_estimated_count"] / report_data["record_count"] > 0.4:
                conclusions.append("图表数据需人工复核")
        if not conclusions:
            conclusions.append("可入库")
        return sorted(set(conclusions))

    @staticmethod
    def _build_manual_review_recommendations(report_data: dict) -> list[str]:
        recs: list[str] = []
        if report_data.get("provisional_group_count", 0):
            recs.append("检查 Gxxx 样品组，确认 provisional group 是否正确")
        if report_data.get("unassigned_count", 0):
            recs.append("处理 Unassigned_Facts 中未归属的性能事实")
        if report_data.get("missing_evidence_count", 0):
            recs.append("补充缺失 evidence_text 的记录")
        if report_data.get("rough_source_location_count", 0):
            recs.append("细化 source_location 到页码、图号、表号或章节")
        if report_data.get("figure_estimated_count", 0):
            recs.append("复核 AI_figure 估读值")
        if report_data.get("sample_card_field_fill_rate", 1) < 0.6:
            recs.append("补全 sample_card 中成分、工艺、结构背景字段")
        return recs or ["无强制复核项，仍建议抽查 evidence_text 与 source_location"]

    # ------------------------------------------------------------------
    # Stage 5: Vision enhancement (optional)
    # ------------------------------------------------------------------

    @staticmethod
    def _chunk_mentions_core_performance(chunk: dict) -> bool:
        text = (chunk.get("raw_text") or "").lower()
        core_hints = (
            "density", "porosity", "shrinkage", "thermal conductivity",
            "surface temperature", "contact angle", "water contact angle",
            "tensile", "strength", "modulus", "elongation", "compressive",
            "stress", "dielectric", "permittivity", "loss tangent",
            "conductivity", "filtration",
        )
        secondary_hints = (
            "xps", "ftir", "raman", "xrd", "binding energy", "simulation",
            "reaction pathway", "reaction fraction", "ffv",
        )
        return (
            any(hint in text for hint in core_hints)
            and not any(hint in text for hint in secondary_hints)
        )

    @staticmethod
    def _is_computational_figure_page(chunks: list[dict], page_number: int) -> bool:
        page_text = " ".join(
            str(chunk.get("raw_text") or "")
            for chunk in chunks
            if chunk.get("page_number") == page_number
        )
        computational = re.search(
            r"(?i)\b(?:finite\s+element|simulation|simulated|numerical\s+model|"
            r"COMSOL|ABAQUS|eigenfrequency|calculated\s+(?:result|band|spectrum))\b",
            page_text,
        )
        experimentally_measured = re.search(
            r"(?i)\b(?:measured\s+experimentally|experimental\s+measurement|"
            r"fabricated\s+specimen|tested\s+using|test\s+machine|photograph)\b",
            page_text,
        )
        return bool(computational and not experimentally_measured)

    @staticmethod
    def _generic_vision_sample_name(sample_id: str) -> bool:
        lower = normalize_sample_id(sample_id).lower().strip()
        lower = re.sub(r"\s+", " ", lower)
        generic_names = {
            "sample", "samples", "fiber", "fibers", "nanofiber", "nanofibers",
            "aerogel", "aerogels", "film", "films", "composite", "composites",
            "pi aerogel", "pi aerogels", "reference", "literature",
        }
        return lower in generic_names

    @staticmethod
    def _is_allowed_vision_fact(vf: dict) -> bool:
        sid = str(vf.get("sample_id") or "").strip()
        metric = str(vf.get("metric_or_parameter") or "").strip()
        value = str(vf.get("value") or "").strip()
        source = str(vf.get("source_location") or "").strip()
        evidence = str(vf.get("evidence_text") or "").strip()
        if not sid or not metric or not value:
            return False
        if V7ExtractorService._generic_vision_sample_name(sid):
            return False
        if is_condition_parameter_name(metric):
            return False
        if classify_metric_priority(metric) != "Core":
            return False
        joined = " ".join([sid, metric, value, source, evidence])
        if text_has_background_reference_signal(joined):
            return False
        if source and not re.search(r"(?i)\b(fig\.?|figure|table)\b", source):
            return False
        return True

    @staticmethod
    async def _stage5_vision_enhancement(
        client, pdf_path: str, chunks: list[dict], facts: list[dict],
    ) -> list[dict]:
        """Vision-based extraction from figure-heavy pages.

        This stage is triggered by figure/page evidence rather than by a low
        fact-count threshold. Vision values are treated as estimates and must be
        reviewed.
        """
        existing_core = sum(
            1 for f in facts
            if f.get("fact_type") == "performance"
            and classify_metric_priority(f.get("metric_or_parameter", "")) == "Core"
            and not is_background_or_reference_fact(f)
        )
        existing_quantitative = sum(
            1 for fact in facts
            if fact.get("fact_type") == "performance"
            and re.search(r"[+-]?\d+(?:\.\d+)?", str(fact.get("value") or ""))
            and str(fact.get("evidence_text") or "").strip()
            and not is_background_or_reference_fact(fact)
        )
        if existing_core >= 8 or existing_quantitative >= 12:
            return facts

        figure_chunks = [
            c for c in chunks
            if (c.get("source_type") == "figure_caption" or c.get("has_figure_image"))
            and not V7ExtractorService._is_background_chunk(c)
            and V7ExtractorService._chunk_mentions_core_performance(c)
            and not V7ExtractorService._is_computational_figure_page(
                chunks, int(c.get("page_number") or 0)
            )
        ]
        fig_pages = sorted({c["page_number"] for c in figure_chunks})[: settings.STRONG_VISION_MAX_PAGES]
        if not fig_pages:
            return facts

        try:
            rendered = render_pdf_pages(pdf_path, fig_pages)
            if not rendered:
                return facts

            parsed, _ = await V7ExtractorService._llm_vision_json_tolerant(
                client,
                "You are analyzing fiber material literature figures and tables. "
                "Extract only directly labeled, readable target-paper core performance data "
                "(for example tensile strength, modulus, density, porosity, shrinkage, "
                "thermal conductivity, contact angle, dielectric properties, EMI SE). "
                "Do not extract XPS/FTIR/Raman/XRD, binding energies, simulation values, "
                "reaction-pathway fractions, axis ticks, test conditions, or literature "
                "comparison/background values. "
                "For each value: identify the sample name (from axis labels, legends, or captions), "
                "metric name, numerical value, and unit. "
                "Output JSON: {'vision_facts': [{'sample_id': '...', 'metric_or_parameter': '...', "
                "'value': '...', 'unit': '...', 'source_location': 'p.X, Fig. Y', "
                "'evidence_text': 'visible label or caption text', 'confidence': 0.0}, ...]}",
                "Return a value only when the sample label, metric, and number are readable. "
                "Skip estimates based only on axis interpolation unless a data label is visible.",
                [r["image"] for r in rendered],
                max_tokens=1400,
                timeout_seconds=settings.STRONG_LLM_TIMEOUT_SECONDS,
                stage="vision_enhancement",
            )
            vision_facts = _response_rows(parsed, "vision_facts", "_items")
            next_id = len(facts) + 1
            for vf in vision_facts:
                sid = vf.get("sample_id", "").strip()
                metric = vf.get("metric_or_parameter", "").strip()
                val = vf.get("value", "").strip()
                if V7ExtractorService._is_allowed_vision_fact(vf):
                    facts.append({
                        "fact_id": f"FV{next_id:04d}",
                        "fact_type": "performance",
                        "subject_text": f"{sid} {metric}",
                        "candidate_sample_ids": [sid],
                        "metric_or_parameter": metric,
                        "value": val,
                        "unit": vf.get("unit", ""),
                        "method": "",
                        "condition": "figure_estimated",
                        "category": find_category_for_metric(metric),
                        "evidence_text": vf.get("evidence_text") or f"Vision-estimated figure value: {metric}={val} {vf.get('unit', '')}",
                        "source_location": vf.get("source_location") or f"p.{rendered[0]['page']}, figure image",
                        "extraction_method": "AI_figure",
                        "confidence": min(float(vf.get("confidence", 0.65) or 0.65), 0.65),
                    })
                    next_id += 1
        except Exception as e:
            print(f"Warning: Vision enhancement failed: {e}")

        return facts

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    @staticmethod
    async def _check_cancelled(db: AsyncSession, job_id: int | None) -> None:
        """Raise ExtractionCancelled if the job has been cancelled."""
        del db  # cancellation uses a fresh session to see committed flags
        from app.services.job_cancellation import is_job_cancel_requested

        if await is_job_cancel_requested(job_id):
            raise ExtractionCancelled("用户取消了抽取任务")

    @staticmethod
    async def run_full_pipeline_for_paper(
        db: AsyncSession, paper_id: int,
        progress_callback: Callable[[str, int, str], Any] | None = None,
        model_mode: str = "auto",
        job_id: int | None = None,
    ) -> dict[str, Any]:
        """Run the V7 multi-stage extraction pipeline.

        model_mode: "weak" | "strong" | "auto"
          - weak:  V6-style direct prompts, single-pass extraction (better for small/cheap models)
          - strong: Multi-stage pipeline with intermediate tables (needs GPT-4o-class models)
          - auto:  Detect from model name; defaults to weak unless model contains "gpt-4o", "claude", "o1", "o3"
        """
        async def _emit(step: str, pct: int, message: str = ""):
            if progress_callback:
                result = progress_callback(step, pct, message)
                if inspect.isawaitable(result):
                    await result

        token = _current_job_id.set(job_id)
        try:
            return await V7ExtractorService._run_full_pipeline_body(
                db, paper_id, progress_callback, model_mode, job_id, _emit
            )
        finally:
            _current_job_id.reset(token)

    @staticmethod
    async def _run_full_pipeline_body(
        db: AsyncSession,
        paper_id: int,
        progress_callback: Callable[[str, int, str], Any] | None,
        model_mode: str,
        job_id: int | None,
        _emit,
    ) -> dict[str, Any]:
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

        # -- Stage 0: PDF parse + chunking --
        await V7ExtractorService._check_cancelled(db, job_id)
        await _emit("inventory", 2, "正在提交 MinerU 文档解析任务...")
        document_context = await parse_pdf_to_document_context(
            db,
            paper,
            pdf_path,
            job_id=job_id,
            progress_callback=progress_callback,
        )
        await _emit(
            "inventory",
            12,
            (
                f"MinerU解析完成: {document_context.page_count}页, "
                f"{len(document_context.blocks)}个块, {len(document_context.tables)}个表格"
            ),
        )
        raw_text = document_context.markdown_text or "\n\n".join(
            f"[page {page_number}]\n{text}"
            for page_number, text in document_context.pages_as_tuples()
        )
        if not raw_text.strip():
            return {"error": "PDF 未提取到可用文本"}

        pages = document_context.pages_as_tuples()
        tables = document_context.tables_as_legacy_blocks()
        chunks = document_context.chunks()
        if not chunks:
            chunks = [
                {
                    "page_number": page_number,
                    "section_name": "results",
                    "source_type": "text",
                    "raw_text": text,
                }
                for page_number, text in pages
                if text.strip()
            ]

        # Refresh parse-derived inventory without touching the last good extraction.
        await db.execute(
            sa_delete(PageInventory).where(PageInventory.paper_id == paper_id)
        )
        await db.execute(
            update(Paper).where(Paper.id == paper_id).values(
                status="extracting", page_count=len(pages)
            )
        )
        for p_num, p_text in pages:
            page_blocks = [
                block for block in document_context.blocks if block.page_number == p_num
            ]
            db.add(PageInventory(
                paper_id=paper_id, page_number=p_num,
                job_id=job_id,
                text_length=len(p_text),
                image_count=len([
                    fig for fig in document_context.figures if fig.page_number == p_num
                ]),
                has_table_signal=bool(
                    [c for c in chunks if c["page_number"] == p_num and c["source_type"] == "table_text"]
                ),
                has_figure_caption=bool(
                    [c for c in chunks if c["page_number"] == p_num and c["source_type"] == "figure_caption"]
                ),
                has_experimental_signal=bool(
                    [c for c in chunks if c["page_number"] == p_num and c["section_name"] == "experimental"]
                ),
                has_supplementary_signal=bool(
                    re.search(r"(?i)\b(supplementary|supporting information|table s\d+|fig\.?\s*s\d+)\b", p_text)
                ),
                importance_score=1.0,
                summary=json.dumps({
                    "sections": [c["section_name"] for c in chunks if c["page_number"] == p_num][:3] or ["general"],
                    "block_types": [block.block_type for block in page_blocks[:8]],
                    "parser": document_context.parser_name,
                }, ensure_ascii=False),
            ))
        await db.commit()

        document_type = classify_document_type(raw_text, paper.paper_title or "")
        paper.document_type = document_type.kind
        paper.extraction_skip_reason = None
        if document_type.kind == "review" and not settings.EXTRACT_REVIEW_ARTICLES:
            # A confirmed review intentionally has no primary-study extraction output.
            await purge_extraction_results(db, paper.project_id, paper_id)
            paper_metadata = V7ExtractorService._fill_paper_metadata_fallback(
                {}, raw_text, paper.original_filename, chunks
            )
            paper.paper_title = document_type.title or paper_metadata.get(
                "paper_title", paper.original_filename
            )
            paper.doi_or_url = paper_metadata.get("doi_or_url", "")
            paper.journal = paper_metadata.get("journal", "")
            paper.extraction_skip_reason = "review_article"
            paper.status = "review"
            db.add(paper)
            await db.commit()
            extraction_report = {
                "文献标题": paper.paper_title,
                "文献类型": "review",
                "抽取状态": "skipped",
                "跳过原因": document_type.reason,
                "生成记录数": 0,
                "质量结论": ["高置信度识别为综述，默认不抽取被引用研究的数据。"],
            }
            report_path = os.path.join(
                settings.UPLOAD_DIR,
                str(paper.project_id),
                f"report_{paper_id}.json",
            )
            report_warning = ""
            try:
                _write_json_atomic(report_path, extraction_report)
            except Exception as exc:
                report_warning = f"综述状态已保存，但报告文件写入失败: {exc}"
                print(f"Warning: {report_warning}")
            try:
                await _emit("completed", 100, "检测为综述论文，已跳过引用数据抽取")
            except Exception as exc:
                print(f"Warning: completion progress notification failed: {exc}")
            return {
                "success": True,
                "skipped": True,
                "skip_reason": "review_article",
                "document_type": "review",
                "pages_processed": len(pages),
                "table_count": len(tables),
                "chunk_count": len(chunks),
                "sample_count": 0,
                "fact_count": 0,
                "candidates_created": 0,
                "resolved_model_mode": model_mode,
                "extraction_report": extraction_report,
                "warnings": [report_warning] if report_warning else [],
            }

        # -- Check LLM availability --
        has_llm = bool(project.llm_api_key and project.llm_api_key.strip())
        if not has_llm:
            await restore_paper_status_after_interruption(
                db, paper, empty_status="failed"
            )
            await db.commit()
            return {"error": "未配置 LLM API Key，无法执行 AI 抽取"}

        client = None
        llm_timeout = (
            settings.WEAK_LLM_TIMEOUT_SECONDS
            if model_mode == "weak"
            else settings.STRONG_LLM_TIMEOUT_SECONDS
        )
        try:
            client = create_llm_client(
                provider=project.llm_provider or "openai",
                api_key=project.llm_api_key,
                model=project.llm_model or settings.DEFAULT_LLM_MODEL,
                base_url=project.llm_base_url or settings.DEFAULT_LLM_BASE_URL,
                timeout_seconds=llm_timeout,
                max_retries=settings.LLM_REQUEST_MAX_RETRIES,
            )
        except Exception as e:
            await restore_paper_status_after_interruption(
                db, paper, empty_status="failed"
            )
            await db.commit()
            return {"error": f"LLM 客户端创建失败: {e}"}

        if not client:
            await restore_paper_status_after_interruption(
                db, paper, empty_status="failed"
            )
            await db.commit()
            return {"error": "LLM 客户端不可用"}

        # -- Auto-detect model mode --
        if model_mode == "auto":
            model_name = (project.llm_model or "").lower()
            strong_keywords = [
                "gpt-5", "gpt-4o", "claude", "o1", "o3", "sonnet", "opus", "haiku",
                "deepseek-r1", "gemini-2", "mimo",
            ]
            if any(kw in model_name for kw in strong_keywords):
                model_mode = "strong"
            else:
                model_mode = "strong"
            print(f"Auto-detected model_mode={model_mode} for model '{project.llm_model}'")
        else:
            print(f"Using explicit model_mode={model_mode}")

        holistic_primary = model_mode == "strong" and settings.STRONG_HOLISTIC_ENABLED
        await _emit(
            "extracting",
            15,
            "Holistic: 正在建立样品目录..." if holistic_primary else "Stage 1: 正在识别样品...",
        )
        await V7ExtractorService._check_cancelled(db, job_id)

        # -- Stage 1: atomic sample mentions and variables --
        paper_metadata = {}
        paper_metadata = V7ExtractorService._fill_paper_metadata_fallback(
            paper_metadata, raw_text, paper.original_filename, chunks
        )
        sample_mentions: list[dict] = []
        variable_candidates: list[dict] = []
        if not holistic_primary:
            sample_mentions = await V7ExtractorService._stage1_sample_mentions(
                client,
                chunks,
                model_mode,
                progress_callback=progress_callback,
                job_id=job_id,
                db=db,
                llm_timeout=llm_timeout,
            )
            await V7ExtractorService._check_cancelled(db, job_id)
            variable_candidates = await V7ExtractorService._stage1_variable_candidates(
                client,
                chunks,
                sample_mentions,
                model_mode,
                progress_callback=progress_callback,
                job_id=job_id,
                db=db,
                llm_timeout=llm_timeout,
            )
        sample_groups = group_samples(sample_mentions, variable_candidates)
        if not holistic_primary:
            await _emit("extracting", 30, f"Stage 1完成: 识别到 {len(sample_mentions)} 个样品")

        holistic_samples: list[dict] = []
        holistic_background: dict[str, dict] = {}
        holistic_performance_facts: list[dict] = []
        holistic_covered_table_ids: set[str] = set()
        holistic_performance_incomplete = False
        holistic_performance_complete = False
        holistic_performance_attempted = False
        pipeline_warnings: list[str] = []
        if model_mode == "strong" and settings.STRONG_HOLISTIC_ENABLED:
            await _emit("extracting", 32, "Holistic: 大上下文样品目录与性能扫表...")
            await V7ExtractorService._check_cancelled(db, job_id)
            try:
                async def _holistic_llm(
                    system_prompt: str,
                    user_prompt: str,
                    *,
                    max_tokens: int,
                    timeout_seconds: int,
                    stage: str,
                    reasoning_effort: str | None = None,
                ):
                    return await V7ExtractorService._llm_json_tolerant(
                        client,
                        system_prompt,
                        user_prompt,
                        max_tokens=max_tokens,
                        timeout_seconds=timeout_seconds,
                        stage=stage,
                        reasoning_effort=reasoning_effort,
                    )

                from app.services.job_cancellation import run_with_cancel_poll

                holistic = await run_with_cancel_poll(
                    run_holistic_extraction(
                        chunks=chunks,
                        llm_json=_holistic_llm,
                        llm_timeout=llm_timeout,
                        sample_max_chars=settings.STRONG_HOLISTIC_SAMPLE_MAX_CHARS,
                        catalog_reasoning_effort=(
                            settings.STRONG_HOLISTIC_CATALOG_REASONING_EFFORT
                        ),
                        max_performance_tokens=settings.STRONG_HOLISTIC_PERFORMANCE_MAX_TOKENS,
                        performance_timeout=settings.STRONG_HOLISTIC_PERFORMANCE_TIMEOUT_SECONDS,
                        results_max_chars=settings.STRONG_HOLISTIC_RESULTS_MAX_CHARS,
                        performance_window_chars=settings.STRONG_HOLISTIC_PERFORMANCE_WINDOW_CHARS,
                        performance_window_overlap_blocks=settings.STRONG_HOLISTIC_WINDOW_OVERLAP_BLOCKS,
                        parallel_calls=per_job_llm_parallel_limit(
                            settings.STRONG_HOLISTIC_PARALLEL_CALLS
                        ),
                        background_timeout=settings.STRONG_HOLISTIC_BACKGROUND_TIMEOUT_SECONDS,
                        background_max_chars=settings.STRONG_HOLISTIC_BACKGROUND_MAX_CHARS,
                        background_max_tokens=settings.STRONG_HOLISTIC_BACKGROUND_MAX_TOKENS,
                        table_timeout=settings.STRONG_TABLE_LLM_TIMEOUT_SECONDS,
                        sensing_enabled=settings.STRONG_HOLISTIC_SENSING_ENABLED,
                    ),
                    job_id,
                )
                holistic_samples = holistic.samples
                holistic_background = holistic.background
                holistic_performance_facts = holistic.performance_facts
                holistic_covered_table_ids = set(holistic.covered_table_block_ids)
                holistic_performance_attempted = True
                for warning in holistic.warnings:
                    print(f"Warning: Holistic branch failed: {warning}")
                    pipeline_warnings.append(f"holistic: {warning}")
                holistic_performance_incomplete = any(
                    warning.startswith("performances:")
                    for warning in holistic.warnings
                )
                holistic_performance_complete = not holistic_performance_incomplete
                if holistic_samples:
                    sample_mentions = sample_mentions + catalog_to_mentions(holistic_samples)
                    for sample in holistic_samples:
                        var_name = (sample.get("variable_name") or "").strip()
                        var_value = sample.get("variable_value")
                        if var_name or var_value not in (None, ""):
                            variable_candidates.append({
                                "sample_id": normalize_sample_id(sample.get("sample_id") or ""),
                                "variable_name_raw": var_name,
                                "variable_value_raw": str(var_value or ""),
                                "variable_unit_raw": sample.get("variable_unit") or "",
                                "source_location": "holistic_catalog",
                                "confidence": 0.85,
                            })
                    sample_groups = group_samples(sample_mentions, variable_candidates)
                await _emit(
                    "extracting",
                    35,
                    (
                        f"Holistic: 样品 {len(holistic_samples)} 个, "
                        f"性能 {len(holistic_performance_facts)} 条"
                    ),
                )
            except Exception as exc:
                print(f"Warning: Holistic extraction failed: {exc}")
                pipeline_warnings.append(f"holistic: {exc}")

        if holistic_performance_incomplete:
            failures = V7ExtractorService._guard_incomplete_holistic_performance(
                holistic.warnings
            )
            pipeline_warnings.append(
                "quality_recovery: holistic performance sweep incomplete; "
                f"running full Stage 2 block fallback ({len(failures)} failure(s))"
            )

        if holistic_primary and not sample_mentions:
            await _emit("extracting", 22, "Holistic 样品目录为空，正在执行原子回退扫描...")
            sample_mentions = await V7ExtractorService._stage1_sample_mentions(
                client,
                chunks,
                model_mode,
                progress_callback=progress_callback,
                job_id=job_id,
                db=db,
                llm_timeout=llm_timeout,
            )
            await V7ExtractorService._check_cancelled(db, job_id)
            variable_candidates = await V7ExtractorService._stage1_variable_candidates(
                client,
                chunks,
                sample_mentions,
                model_mode,
                progress_callback=progress_callback,
                job_id=job_id,
                db=db,
                llm_timeout=llm_timeout,
            )
            sample_groups = group_samples(sample_mentions, variable_candidates)
        await _emit("extracting", 36, f"样品目录完成: {len(sample_mentions)} 个提及")

        # -- Stage 2: chunk-level atomic fact candidates --
        await V7ExtractorService._check_cancelled(db, job_id)
        holistic_core_fact_count = V7ExtractorService._holistic_core_fact_count(
            holistic_performance_facts
        )
        atomic_facts = await V7ExtractorService._stage2_fact_candidates(
            client,
            chunks,
            model_mode=model_mode,
            holistic_fact_count=holistic_core_fact_count,
            holistic_covered_table_ids=holistic_covered_table_ids,
            holistic_performance_complete=holistic_performance_complete,
            holistic_performance_attempted=holistic_performance_attempted,
            known_sample_ids=[
                normalize_sample_id(sample.get("sample_id") or "")
                for sample in holistic_samples
                if sample.get("sample_id")
            ],
            allow_partial_failures=(
                holistic_performance_attempted
                and holistic_core_fact_count
                >= settings.STRONG_STAGE2_PARTIAL_FAILURE_MIN_FACTS
            ),
            warnings=pipeline_warnings,
            progress_callback=progress_callback,
            job_id=job_id,
            db=db,
            llm_timeout=llm_timeout,
        )
        facts = merge_holistic_and_atomic_facts(atomic_facts, holistic_performance_facts)
        facts.extend(recover_explicit_contrast_result_facts(chunks, facts))
        facts.extend(V7ExtractorService._deterministic_transition_facts(chunks, facts))
        facts.extend(recover_explicit_frequency_range_facts(chunks, facts))
        facts.extend(V7ExtractorService._deterministic_process_facts(chunks))
        facts = renumber_fact_ids(facts)
        facts, sample_mentions = postprocess_extracted_facts(facts, sample_mentions)
        sample_mentions = [
            mention
            for mention in sample_mentions
            if is_material_sample_id(
                str(
                    mention.get("normalized_sample_id")
                    or mention.get("mention_text")
                    or ""
                )
            )
        ]
        fact_sample_mentions = V7ExtractorService._sample_mentions_from_fact_candidates(facts)
        if fact_sample_mentions:
            sample_mentions = V7ExtractorService._dedupe_sample_mentions(
                sample_mentions + fact_sample_mentions
            )
        sample_groups = group_samples(sample_mentions, variable_candidates)
        await _emit("extracting", 50, f"Stage 2: 提取到 {len(facts)} 条事实")
        await _emit("extracting", 65, "Stage 3: 正在分配样品...")
        await V7ExtractorService._check_cancelled(db, job_id)

        # -- Stage 3: figure-level enhancement, then deterministic assignment --
        try:
            facts = await V7ExtractorService._stage5_vision_enhancement(
                client, pdf_path, chunks, facts
            )
        except Exception as e:
            print(f"Warning: Vision enhancement stage failed: {e}")

        for fact in facts:
            if fact.get("_background_only"):
                fact["assigned_sample_id"] = None
                fact["candidate_sample_ids"] = []
                fact["assignment_status"] = "unassigned"
                fact["assignment_confidence"] = None
                continue
            if (
                fact.get("extraction_method") in {
                    "AI_holistic", "AI_holistic_table", "rule_table_performance",
                }
                and fact.get("assigned_sample_id")
                and fact.get("assignment_status") == "assigned"
            ):
                continue
            assignment = assign_fact_to_sample(fact, sample_mentions, sample_groups)
            fact["assigned_sample_id"] = assignment.get("sample_id") or None
            fact["assignment_confidence"] = assignment.get("confidence")
            fact["assignment_status"] = assignment.get("status", "unassigned")
            fact["assignment_reason"] = assignment.get("reason", "")

        sample_cards = build_sample_cards(
            sample_mentions, variable_candidates, sample_groups, facts
        )
        if holistic_samples or holistic_background:
            sample_cards = enrich_sample_cards_holistic(
                sample_cards, holistic_samples, holistic_background,
            )
        sample_cards = V7ExtractorService._sanitize_sample_cards(sample_cards)

        sample_mentions, facts, sample_cards = merge_sample_identities(
            sample_mentions,
            facts,
            sample_cards,
            holistic_samples=holistic_samples,
        )
        sample_cards = V7ExtractorService._sanitize_sample_cards(sample_cards)
        sample_groups = group_samples(sample_mentions, variable_candidates)
        sample_cards = V7ExtractorService._propagate_sample_card_backgrounds(sample_cards)
        sample_cards = V7ExtractorService._enrich_sample_cards_from_repeated_fact_variants(
            sample_cards, facts
        )
        sample_cards = fill_sample_card_variables(sample_cards, sample_groups)
        sample_cards = V7ExtractorService._enrich_sample_cards_from_process_facts(
            sample_cards, facts
        )
        sample_mentions, facts, sample_cards = merge_sample_identities(
            sample_mentions,
            facts,
            sample_cards,
            holistic_samples=holistic_samples,
        )
        sample_cards = V7ExtractorService._sanitize_sample_cards(sample_cards)
        sample_groups = group_samples(sample_mentions, variable_candidates)
        for fact in facts:
            if fact.get("_background_only"):
                continue
            if fact.get("assigned_sample_id") and fact.get("assignment_status") == "assigned":
                continue
            assignment = assign_fact_to_sample(fact, sample_mentions, sample_groups)
            if assignment.get("sample_id"):
                fact["assigned_sample_id"] = assignment.get("sample_id")
                fact["assignment_confidence"] = assignment.get("confidence")
                fact["assignment_status"] = assignment.get("status", "unassigned")
                fact["assignment_reason"] = assignment.get("reason", "")
        facts = sanitize_assigned_sample_ids(facts, sample_cards, sample_mentions)
        facts = V7ExtractorService._local_sample_assignment(facts, sample_cards)
        facts = V7ExtractorService._repair_sample_assignment_specificity(facts, sample_cards)
        facts = V7ExtractorService._repair_sample_assignment_from_variable_context(
            facts,
            sample_cards,
        )
        facts = apply_sample_value_alignment(facts, sample_cards)
        facts = normalize_metrics_in_facts(facts)
        facts = repair_contextual_fact_assignments(facts, sample_cards)
        facts = sanitize_assigned_sample_ids(facts, sample_cards, sample_mentions)
        facts = V7ExtractorService._local_sample_assignment(facts, sample_cards)
        facts = V7ExtractorService._repair_sample_assignment_from_variable_context(
            facts,
            sample_cards,
        )
        facts = reconcile_holistic_table_duplicates(facts)
        facts = merge_duplicate_facts(facts)
        facts = renumber_fact_ids(facts)
        facts = apply_pre_output_validation(facts, sample_cards)
        from app.services.extractor_v7.quality_enhancement import (
            apply_fact_quality_enhancements,
            enrich_sample_cards_with_form,
        )
        facts = apply_fact_quality_enhancements(
            facts,
            chunks=chunks,
            paper_metadata=paper_metadata,
        )
        sample_cards = enrich_sample_cards_with_form(sample_cards)

        if len(sample_cards) == 1:
            only_sid = sample_cards[0].get("sample_id")
            if only_sid:
                for fact in facts:
                    if not fact.get("assigned_sample_id"):
                        fact["assigned_sample_id"] = only_sid
                        fact["assignment_status"] = "uncertain"
                        fact["assignment_confidence"] = fact.get("assignment_confidence") or 0.55
                        fact["assignment_reason"] = (
                            (fact.get("assignment_reason") or "") + "; single_sample_fallback"
                        ).strip("; ")

        await _emit("extracting", 75, "Stage 3完成: 正在生成候选记录...")
        await V7ExtractorService._check_cancelled(db, job_id)

        # -- Stage 4: Record generation --
        records, report_data = V7ExtractorService._stage4_generate_records(
            paper_id,
            paper.project_id,
            paper_metadata,
            sample_cards,
            facts,
            sample_mentions=sample_mentions,
            variable_candidates=variable_candidates,
            sample_groups=sample_groups,
        )
        empty_record_warning = V7ExtractorService._guard_suspicious_empty_records(
            chunks,
            records,
            fact_count=len(facts),
        )
        if empty_record_warning:
            pipeline_warnings.append(f"quality_gate: {empty_record_warning}")
            report_data["suspicious_empty_records"] = True
            report_data["quality_conclusions"] = sorted(set([
                *report_data["quality_conclusions"],
                "定量证据未形成候选，需人工复核",
            ]))
            report_data["manual_review_recommendations"] = sorted(set([
                *report_data["manual_review_recommendations"],
                "复核中间事实到候选记录的数值、样品归属和输出通道",
            ]))
        else:
            report_data["suspicious_empty_records"] = False
        non_record_outputs_only = bool(report_data["result_facts"]) and all(
            result.get("export_target") in {
                "Characterization_Features",
                "Formula_Method_Parameters",
            }
            for result in report_data["result_facts"]
        )
        report_data["non_record_outputs_only"] = (
            not records
            and not empty_record_warning
            and non_record_outputs_only
        )
        if report_data["non_record_outputs_only"]:
            report_data["quality_conclusions"] = sorted(set([
                *report_data["quality_conclusions"],
                "仅含表征或方法参数，无目标性能候选",
            ]))
        await _emit("saving", 85, "Stage 4完成: 正在准备原子保存...")

        sample_models = [
            SampleCatalog(
                paper_id=paper_id,
                project_id=paper.project_id,
                sample_id=sample.get("sample_id", ""),
                sample_aliases=V7ExtractorService._serialize_sample_aliases(
                    sample.get("sample_aliases")
                ),
                sample_group_id=sample.get("sample_group_id", "G000"),
                material_system=sample.get("material_system", ""),
                fiber_type=sample.get("fiber_type", ""),
                variable_name=sample.get("variable_name", ""),
                variable_value=sample.get("variable_value", ""),
                variable_unit=sample.get("variable_unit", ""),
                composition_expression=sample.get("composition_expression", ""),
                process_route=sample.get("process_route", ""),
                source_location=sample.get("source_location", ""),
                evidence_text=sample.get("evidence_text", ""),
                confidence=float(sample.get("confidence", 0.5) or 0.5),
            )
            for sample in sample_cards
        ]

        fact_models: list[FactCandidate] = []
        for fact in facts:
            candidate_ids = fact.get("candidate_sample_ids", [])
            if isinstance(candidate_ids, list):
                candidate_ids_str = json.dumps(candidate_ids, ensure_ascii=False)
            else:
                candidate_ids_str = str(candidate_ids) if candidate_ids else None
            fact_models.append(FactCandidate(
                paper_id=paper_id,
                project_id=paper.project_id,
                fact_id=fact.get("fact_id", ""),
                fact_type=fact.get("fact_type", "performance"),
                subject_text=fact.get("subject_text", ""),
                candidate_sample_ids=candidate_ids_str,
                metric_or_parameter=fact.get("metric_or_parameter", ""),
                value=fact.get("value", ""),
                unit=fact.get("unit", ""),
                method=fact.get("method", ""),
                condition=fact.get("condition", ""),
                category=fact.get("category", ""),
                evidence_text=fact.get("evidence_text", ""),
                source_location=fact.get("source_location", ""),
                source_block_id=fact.get("_source_block_id"),
                source_page=fact.get("_source_page"),
                source_bbox_json=json.dumps(fact.get("_source_bbox"), ensure_ascii=False)
                if fact.get("_source_bbox") is not None else None,
                extraction_method=fact.get("extraction_method", "AI_text"),
                confidence=float(fact.get("confidence", 0.5)),
                assigned_sample_id=fact.get("assigned_sample_id"),
                assignment_confidence=fact.get("assignment_confidence"),
                assignment_status=fact.get("assignment_status", "unassigned"),
            ))

        block_type_by_id = {
            block.block_id: block.block_type for block in document_context.blocks
        }
        candidate_models: list[CandidateRecord] = []
        candidate_evidence: list[tuple[CandidateRecord, dict[str, Any], str, Any, Any]] = []
        for source_record in records:
            r = dict(source_record)
            validation_issues = r.pop("_validation_issues", [])
            fact_id = r.pop("_fact_id", "")
            source_block_id = r.get("_source_block_id")
            source_bbox = r.get("_source_bbox")

            rec = CandidateRecord(
                project_id=r["project_id"],
                source_paper_id=r["source_paper_id"],
                job_id=job_id,
                record_id=r["record_id"],
                paper_id_str=r.get("paper_id_str", ""),
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
                composition_evidence=r.get("composition_evidence", ""),
                process_route=r["process_route"],
                spinning_method=r.get("spinning_method", ""),
                process_parameters=r.get("process_parameters", ""),
                post_treatment=r.get("post_treatment", ""),
                process_evidence=r.get("process_evidence", ""),
                structure_methods=r.get("structure_methods", ""),
                structure_features=merge_characterization_features(
                    r.get("structure_features", ""),
                    r.get("characterization_features", ""),
                ),
                structure_evidence=r.get("structure_evidence", ""),
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
                    "; ".join([x for x in [r.get("reviewer_comment", ""), "; ".join(validation_issues)] if x])
                ),
            )
            candidate_models.append(rec)
            candidate_evidence.append((rec, r, fact_id, source_block_id, source_bbox))
        saved_count = len(candidate_models)

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
            provisional_groups=report_data["provisional_group_count"],
            sample_card_fill_rate=report_data["sample_card_field_fill_rate"],
            rough_source_count=report_data["rough_source_location_count"],
            extra_metrics={
                "样品卡数量": report_data["sample_count"],
                "样品提及数量": report_data["sample_mentions_count"],
                "变量候选数量": report_data["variable_candidates_count"],
                "provisional样品组数量": report_data["provisional_group_count"],
                "结果事实数量": report_data["result_fact_count"],
                "核心记录数": report_data["core_record_count"],
                "补充记录数": report_data["secondary_record_count"],
                "QA事实数量": report_data["qa_result_fact_count"],
                "候选为空质量告警": report_data["suspicious_empty_records"],
                "仅非记录输出": report_data["non_record_outputs_only"],
                "有证据记录比例": report_data["evidence_text_record_ratio"],
                "文献信息缺失率": report_data["paper_metadata_missing_rate"],
                "文献信息完整率": report_data["paper_metadata_complete_rate"],
                "文献信息缺失字段": report_data["paper_metadata_missing_fields"],
                "样品卡字段填充率": report_data["sample_card_field_fill_rate"],
                "最终记录字段填充率": report_data["final_record_field_fill_rate"],
                "来源位置过粗数量": report_data["rough_source_location_count"],
                "AI_figure估读记录数": report_data["figure_estimated_count"],
                "performance_metric缺失数量": report_data["performance_metric_missing_count"],
                "performance_value缺失数量": report_data["performance_value_missing_count"],
                "performance_unit缺失数量": report_data["performance_unit_missing_count"],
                "字段错位检测结果": report_data["schema_alignment_status"],
                "字段缺失自动补齐数": report_data["schema_missing_field_count"],
                "Core指标覆盖率": report_data["core_metric_coverage"],
                "指标优先级分布": report_data["metric_priority_counts"],
                "质量结论": report_data["quality_conclusions"],
                "人工复核建议": report_data["manual_review_recommendations"],
                "sample_mentions": report_data["sample_mentions"],
                "variable_candidates": report_data["variable_candidates"],
                "sample_groups": report_data["sample_groups"],
                "sample_cards": report_data["sample_cards"],
                "result_facts": report_data["result_facts"],
                "unassigned_facts": report_data["unassigned_facts"],
                "抽取告警": pipeline_warnings,
            },
        )

        report_path = os.path.join(
            settings.UPLOAD_DIR, str(paper.project_id),
            f"report_{paper_id}.json"
        )

        # The final replacement is one short transaction. Until this point a
        # failed rerun leaves the previous extraction untouched.
        await V7ExtractorService._check_cancelled(db, job_id)
        try:
            await purge_extraction_results(db, paper.project_id, paper_id)
            db.add_all(sample_models)
            db.add_all(fact_models)
            db.add_all(candidate_models)
            if candidate_models:
                # Assign all candidate IDs in one round trip instead of one flush
                # per row; evidence rows can then reference those IDs.
                await db.flush()
                db.add_all([
                    EvidenceItem(
                        project_id=r["project_id"],
                        paper_id=r["source_paper_id"],
                        job_id=job_id,
                        candidate_record_id=rec.id,
                        parse_run_id=document_context.parse_run_id,
                        block_id=source_block_id,
                        bbox_json=json.dumps(source_bbox, ensure_ascii=False)
                        if source_bbox is not None else None,
                        mineru_block_type=block_type_by_id.get(source_block_id or ""),
                        source_type=f"fact_{fact_id}" if fact_id else "unknown",
                        page_start=r.get("_source_page"),
                        page_end=r.get("_source_page"),
                        source_location=r.get("source_location", ""),
                        evidence_text=r.get("evidence_text", ""),
                        normalized_payload=json.dumps({
                            "fact_id": fact_id,
                            "metric": r["performance_metric"],
                            "value": r["performance_value"],
                            "unit": r["performance_unit"],
                        }, ensure_ascii=False),
                        confidence=float(r.get("ai_confidence", 0.5)),
                    )
                    for rec, r, fact_id, source_block_id, source_bbox in candidate_evidence
                ])

            paper.paper_title = paper_metadata.get(
                "paper_title", paper.original_filename
            )
            paper.doi_or_url = paper_metadata.get("doi_or_url", "")
            raw_year = paper_metadata.get("year")
            try:
                paper.year = int(raw_year) if str(raw_year or "").strip() else None
            except (ValueError, TypeError):
                paper.year = None
            paper.journal = paper_metadata.get("journal", "")
            paper.document_type = document_type.kind
            if empty_record_warning:
                paper.extraction_skip_reason = (
                    "quantitative_evidence_without_candidates"
                )
            elif report_data["non_record_outputs_only"]:
                paper.extraction_skip_reason = "non_record_outputs_only"
            else:
                paper.extraction_skip_reason = None
            paper.status = "review"
            db.add(paper)
            await db.commit()
        except BaseException:
            await db.rollback()
            raise

        report_warning = ""
        try:
            _write_json_atomic(report_path, extraction_report)
        except Exception as exc:
            report_warning = f"结果已保存，但报告文件写入失败: {exc}"
            print(f"Warning: {report_warning}")

        try:
            await _emit("completed", 100, f"抽取完成: {saved_count} 条记录")
        except Exception as exc:
            print(f"Warning: completion progress notification failed: {exc}")

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
            "resolved_model_mode": model_mode,
            "extraction_report": extraction_report,
            "warnings": pipeline_warnings + (
                [report_warning] if report_warning else []
            ),
        }
