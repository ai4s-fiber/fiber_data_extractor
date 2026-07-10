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
from app.models.extraction_job import ExtractionJob

from app.core.config import settings
from app.services.llm_client import create_llm_client, _tolerant_parse_json
from app.services.document_context import parse_pdf_to_document_context
from app.services.pdf_utils import render_pdf_pages
from app.services.chunking import (
    chunks_for_performance_extraction,
    chunks_for_composition_process,
)
from app.services.grouping import (
    assign_fact_to_sample,
    build_sample_cards,
    fill_sample_card_variables,
    group_samples,
    infer_variable_from_sample_id,
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
    get_common_units,
    is_condition_parameter_name,
)

from app.services.extractor_v7.prompts import (
    SAMPLE_MENTIONS_PROMPT,
    VARIABLE_CANDIDATES_PROMPT,
    STAGE2_FACTS_PROMPT,
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
from app.services.extractor_v7.output_postprocess import (
    apply_pre_output_validation,
    format_characterization_entry,
    merge_characterization_features,
)
from app.services.extractor_v7.sample_value_alignment import apply_sample_value_alignment
from app.services.extractor_v7.holistic_extract import (
    catalog_to_mentions,
    enrich_sample_cards as enrich_sample_cards_holistic,
    merge_holistic_and_atomic_facts,
    run_holistic_extraction,
)
from app.services.extractor_v7.sample_identity import merge_sample_identities
from app.services.extractor_v7.validators import (
    determine_review_status,
    is_background_or_reference_fact,
    is_rough_source_location,
    validate_fact,
    _looks_like_affiliation_or_address,
    _looks_like_journal_name,
)

_current_job_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "extraction_job_id", default=None
)



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
        fig = re.search(r"(?i)\b(fig\.?|figure)\s*([0-9]+[a-z]?)", text[:300])
        table = re.search(r"(?i)\btable\s*([0-9]+[a-z]?)", text[:300])
        section = chunk.get("section_name", "")
        if source:
            return source
        if fig:
            return f"p.{page}, Fig. {fig.group(2)}"
        if table:
            return f"p.{page}, Table {table.group(1)}"
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
    def _chunks_for_prompt_multi(chunks: list[dict], limit_per_chunk: int = 3000) -> str:
        return "\n\n---\n\n".join(
            V7ExtractorService._chunk_for_prompt(chunk, limit_per_chunk)
            for chunk in chunks
        )

    @staticmethod
    async def _llm_json_tolerant(
        client,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int,
        timeout_seconds: int,
        stage: str = "llm",
    ) -> tuple[dict, str]:
        from app.services.llm_metrics import track_llm_call

        job_id = _current_job_id.get()
        model_name = getattr(client, "model", "unknown")
        prompt_chars = len(system_prompt) + len(user_prompt)
        try:
            async with track_llm_call(
                job_id=job_id,
                stage=stage,
                model=model_name,
                call_type="json_tolerant",
                prompt_chars=prompt_chars,
            ):
                return await asyncio.wait_for(
                    client.agenerate_json_tolerant(system_prompt, user_prompt, max_tokens=max_tokens),
                    timeout=timeout_seconds,
                )
        except asyncio.TimeoutError:
            return {}, ""

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
        parallel_calls = (
            settings.WEAK_LLM_PARALLEL_CALLS
            if model_mode == "weak"
            else settings.STRONG_LLM_PARALLEL_CALLS
        )
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
                items = parsed.get("sample_mentions") or parsed.get("_items") or []
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

        parallel_calls = (
            settings.WEAK_LLM_PARALLEL_CALLS
            if model_mode == "weak"
            else settings.STRONG_LLM_PARALLEL_CALLS
        )
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
                items = parsed.get("variable_candidates") or parsed.get("_items") or []
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
        paper_metadata: dict, raw_text: str, original_filename: str,
    ) -> dict:
        """Fill missing paper metadata once, then inherit it to every record."""
        metadata = dict(paper_metadata or {})
        first_page = raw_text[:5000]

        if not metadata.get("doi_or_url"):
            doi_match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", first_page, re.IGNORECASE)
            if doi_match:
                metadata["doi_or_url"] = doi_match.group(0).rstrip(".,;) ")

        if not metadata.get("year"):
            year_match = re.search(r"\b(20\d{2}|19\d{2})\b", first_page)
            if year_match:
                metadata["year"] = year_match.group(1)

        if not metadata.get("paper_title"):
            lines = [line.strip() for line in first_page.splitlines() if line.strip()]
            title_candidates = [
                line for line in lines[:30]
                if 20 <= len(line) <= 220
                and not re.search(r"^(abstract|keywords|doi|http|journal|volume)\b", line, re.IGNORECASE)
                and not re.search(r"^\[page\s+\d+\]$", line, re.IGNORECASE)
                and not _looks_like_affiliation_or_address(line)
            ]
            metadata["paper_title"] = title_candidates[0] if title_candidates else original_filename

        if not metadata.get("journal"):
            lines = [line.strip() for line in first_page.splitlines() if line.strip()]
            for line in lines[:40]:
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
        progress_callback=None,
        job_id: int | None = None,
        db: AsyncSession | None = None,
        llm_timeout: int = 90,
    ) -> list[dict]:
        """Extract atomic fact candidates with capped chunk count and per-chunk progress."""
        prompt = WEAK_FACTS_PROMPT if model_mode == "weak" else (
            STAGE2_FACTS_PROMPT.replace(
                "{{metrics_list}}", build_metrics_prompt_text()
            ).replace(
                "{{structure_list}}", build_structure_prompt_text()
            ).replace(
                "{{process_list}}", build_process_prompt_text()
            )
        )

        selected = V7ExtractorService._select_stage2_chunks(
            chunks, model_mode, holistic_fact_count=holistic_fact_count,
        )
        units = V7ExtractorService._stage2_execution_units(selected, model_mode)

        all_facts: list[dict] = []
        total = max(len(units), 1)
        parallel_calls = (
            settings.WEAK_LLM_PARALLEL_CALLS
            if model_mode == "weak"
            else settings.STRONG_LLM_PARALLEL_CALLS
        )
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
                parsed, _ = await V7ExtractorService._llm_json_tolerant(
                    client,
                    prompt,
                    prompt_text,
                    max_tokens=(
                        1400 if model_mode == "weak"
                        else (3600 if is_table else 2800)
                    ),
                    timeout_seconds=llm_timeout,
                    stage="stage2_facts",
                )
                items = parsed.get("facts") or parsed.get("_items") or []
                if isinstance(items, dict):
                    items = [items]
                extraction_method = V7ExtractorService._extraction_method_for_chunk(anchor)
                unit_facts: list[dict] = []
                for item in items:
                    fact = V7ExtractorService._normalize_fact_from_chunk(item, source, extraction_method)
                    if fact:
                        fact["_chunk_section"] = anchor.get("section_name", "")
                        fact["_chunk_source_type"] = anchor.get("source_type", "")
                        fact["_source_block_id"] = anchor.get("source_block_id")
                        fact["_source_page"] = anchor.get("page_number")
                        fact["_source_bbox"] = anchor.get("source_bbox")
                        unit_facts.append(fact)
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
                return unit_facts

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
    def _fact_chunk_priority(chunk: dict) -> tuple[int, int, int]:
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
        return (type_rank, section_rank, len_rank)

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
    ) -> list[dict]:
        """Tiered chunk selection: prioritize tables/figures, then cap total."""
        merged = merge_adjacent_table_chunks(chunks)
        base = V7ExtractorService._fact_chunks(merged)
        if model_mode == "weak":
            return V7ExtractorService._cap_chunks(base, settings.WEAK_MAX_FACT_CHUNKS)

        slim = (
            model_mode == "strong"
            and holistic_fact_count >= settings.STRONG_STAGE2_HOLISTIC_SLIM_THRESHOLD
        )
        if slim:
            base = [
                c for c in base
                if c.get("source_type") in {"table_text", "figure_caption"}
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
        cap = (
            settings.STRONG_STAGE2_HOLISTIC_SLIM_MAX_CHUNKS
            if slim
            else settings.STRONG_MAX_FACT_CHUNKS
        )
        return V7ExtractorService._cap_chunks(selected, cap)

    @staticmethod
    def _stage2_execution_units(chunks: list[dict], model_mode: str) -> list[list[dict]]:
        """Batch small text chunks; keep tables as standalone units."""
        if model_mode == "weak":
            return [[chunk] for chunk in chunks]

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
        source_type = chunk.get("source_type")
        if source_type in {"table_text", "figure_caption"}:
            return False
        if section in {"title_abstract", "introduction", "background", "references"}:
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
        }

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
            parsed, _ = await client.agenerate_json_tolerant(
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
        """Copy shared composition/process/structure fields within sample groups."""
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

        for group_cards in by_group.values():
            if len(group_cards) < 2:
                continue
            donor = max(
                group_cards,
                key=lambda card: sum(1 for field in bg_fields if card.get(field)),
            )
            for card in group_cards:
                for field in bg_fields:
                    if not card.get(field) and donor.get(field):
                        card[field] = donor[field]
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
                for alias in (s.get("sample_aliases") or []):
                    alias = alias.strip()
                    if alias:
                        sample_lookup[V7ExtractorService._normalize_for_match(alias)] = s

        for f in facts:
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

            best_match = None
            best_score = 0

            for norm_sid, sample in sample_lookup.items():
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
            for cand in candidates:
                cand_lower = cand.lower().strip()
                for sid_raw, sample in sample_ids_raw:
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
    def _normalize_unit(unit: str | None) -> str:
        unit = (unit or "").strip()
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
        from app.services.extractor_v7.value_parse import parse_scientific_value

        sci = parse_scientific_value(text)
        if sci:
            text = sci
        operator = "="
        if text.startswith(("<=", ">=")):
            operator, text = text[:2], text[2:].strip()
        elif text.startswith("<"):
            operator, text = "<", text[1:].strip()
        elif text.startswith(">"):
            operator, text = ">", text[1:].strip()
        elif text.startswith(("~", "≈")):
            operator, text = "≈", text[1:].strip()

        number = r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
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
        card_ids = {c["sample_id"] for c in sample_cards if c.get("sample_id")}
        assigned = fact.get("assigned_sample_id")
        if assigned in card_ids:
            return assigned
        candidates = V7ExtractorService._as_list(fact.get("candidate_sample_ids"))
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
            if fact.get("_evidence_audit_failed"):
                # Still include but route to QA for review
                pass  # let the export_tier logic below handle routing
            metric_raw = fact.get("metric_or_parameter", "") or ""
            is_condition_parameter = is_condition_parameter_name(metric_raw)
            metric = metric_raw if is_condition_parameter else (find_metric_canonical(metric_raw) or metric_raw)
            category = fact.get("category") or find_category_for_metric(metric)
            priority = "Secondary" if is_condition_parameter else classify_metric_priority(metric)
            qa_reasons: list[str] = []
            if ftype != "performance":
                priority = "Secondary"
                qa_reasons.append(f"fact_type={ftype}")
            if is_condition_parameter:
                qa_reasons.append("condition_parameter")
            if is_background_or_reference_fact(fact):
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
            force_qa = ftype != "performance" or any(
                reason in qa_reasons
                for reason in (
                    "background_or_reference", "rough_source_location",
                    "fact_type=process", "fact_type=structure", "fact_type=composition",
                    "alignment_review_required", "metric_unit_mismatch",
                    "characterization_feature", "formula_or_method_parameter",
                )
            )
            if output_channel == "characterization_feature":
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
        return result_facts

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
        formula_entries_by_sample: dict[str, list[str]] = {}
        for rf in result_facts:
            sid = rf.get("sample_id") or ""
            if not sid:
                continue
            if rf.get("export_target") == "Characterization_Features":
                char_entries_by_sample.setdefault(sid, []).append(
                    format_characterization_entry(rf),
                )
            elif rf.get("export_target") == "Formula_Method_Parameters":
                metric = rf.get("canonical_metric") or rf.get("raw_metric") or ""
                value = rf.get("clean_value") or ""
                unit = rf.get("clean_unit") or ""
                formula_entries_by_sample.setdefault(sid, []).append(
                    f"formula_peak:{metric}={value}{unit}",
                )
        for card in sample_cards:
            sid = card.get("sample_id") or ""
            if sid in char_entries_by_sample:
                card["characterization_features"] = merge_characterization_features(
                    card.get("characterization_features", ""),
                    char_entries_by_sample[sid],
                )
            if sid in formula_entries_by_sample:
                existing = str(card.get("process_parameters") or "").strip()
                merged = merge_characterization_features(
                    existing, formula_entries_by_sample[sid],
                )
                card["process_parameters"] = merged
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
        if _text_has_background_reference_signal(joined):
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
        if existing_core >= 8:
            return facts

        figure_chunks = [
            c for c in chunks
            if (c.get("source_type") == "figure_caption" or c.get("has_figure_image"))
            and not V7ExtractorService._is_background_chunk(c)
            and V7ExtractorService._chunk_mentions_core_performance(c)
        ]
        fig_pages = sorted({c["page_number"] for c in figure_chunks})[: settings.STRONG_VISION_MAX_PAGES]
        if not fig_pages:
            return facts

        try:
            rendered = render_pdf_pages(pdf_path, fig_pages)
            if not rendered:
                return facts

            parsed, _ = await client.agenerate_vision_json_tolerant(
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
            )
            vision_facts = parsed.get("vision_facts") or parsed.get("_items") or []
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

        # -- Clean up old extraction data --
        await db.execute(sa_delete(EvidenceItem).where(EvidenceItem.paper_id == paper_id))
        await db.execute(sa_delete(CandidateRecord).where(CandidateRecord.source_paper_id == paper_id))
        await db.execute(sa_delete(PageInventory).where(PageInventory.paper_id == paper_id))
        await db.execute(sa_delete(SampleCatalog).where(SampleCatalog.paper_id == paper_id))
        await db.execute(sa_delete(FactCandidate).where(FactCandidate.paper_id == paper_id))
        await db.commit()

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

        # Save page inventory
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

        # -- Check LLM availability --
        has_llm = bool(project.llm_api_key and project.llm_api_key.strip())
        if not has_llm:
            paper.status = "failed"
            db.add(paper)
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
                model=project.llm_model or "gpt-4o",
                base_url=project.llm_base_url or "https://api.openai.com/v1",
                timeout_seconds=llm_timeout,
                max_retries=1,
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

        # -- Auto-detect model mode --
        if model_mode == "auto":
            model_name = (project.llm_model or "").lower()
            strong_keywords = [
                "gpt-4o", "claude", "o1", "o3", "sonnet", "opus", "haiku",
                "deepseek-r1", "gemini-2", "mimo",
            ]
            if any(kw in model_name for kw in strong_keywords):
                model_mode = "strong"
            else:
                model_mode = "strong"
            print(f"Auto-detected model_mode={model_mode} for model '{project.llm_model}'")
        else:
            print(f"Using explicit model_mode={model_mode}")

        await _emit("extracting", 15, "Stage 1: 正在识别样品...")
        await V7ExtractorService._check_cancelled(db, job_id)

        # -- Stage 1: atomic sample mentions and variables --
        paper_metadata = {}
        paper_metadata = V7ExtractorService._fill_paper_metadata_fallback(
            paper_metadata, raw_text, paper.original_filename
        )
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
        await _emit("extracting", 30, f"Stage 1完成: 识别到 {len(sample_mentions)} 个样品")

        holistic_samples: list[dict] = []
        holistic_background: dict[str, dict] = {}
        holistic_performance_facts: list[dict] = []
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
                ):
                    return await V7ExtractorService._llm_json_tolerant(
                        client,
                        system_prompt,
                        user_prompt,
                        max_tokens=max_tokens,
                        timeout_seconds=timeout_seconds,
                        stage=stage,
                    )

                from app.services.job_cancellation import run_with_cancel_poll

                holistic = await run_with_cancel_poll(
                    run_holistic_extraction(
                        chunks=chunks,
                        llm_json=_holistic_llm,
                        llm_timeout=llm_timeout,
                        max_performance_tokens=settings.STRONG_HOLISTIC_PERFORMANCE_MAX_TOKENS,
                        results_max_chars=settings.STRONG_HOLISTIC_RESULTS_MAX_CHARS,
                        sensing_enabled=settings.STRONG_HOLISTIC_SENSING_ENABLED,
                    ),
                    job_id,
                )
                holistic_samples = holistic.samples
                holistic_background = holistic.background
                holistic_performance_facts = holistic.performance_facts
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

        # -- Stage 2: chunk-level atomic fact candidates --
        await V7ExtractorService._check_cancelled(db, job_id)
        atomic_facts = await V7ExtractorService._stage2_fact_candidates(
            client,
            chunks,
            model_mode=model_mode,
            holistic_fact_count=len(holistic_performance_facts),
            progress_callback=progress_callback,
            job_id=job_id,
            db=db,
            llm_timeout=llm_timeout,
        )
        facts = merge_holistic_and_atomic_facts(atomic_facts, holistic_performance_facts)
        facts = renumber_fact_ids(facts)
        facts, sample_mentions = postprocess_extracted_facts(facts, sample_mentions)
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

        sample_mentions, facts, sample_cards = merge_sample_identities(
            sample_mentions,
            facts,
            sample_cards,
            holistic_samples=holistic_samples,
        )
        sample_groups = group_samples(sample_mentions, variable_candidates)
        sample_cards = V7ExtractorService._propagate_sample_card_backgrounds(sample_cards)
        sample_cards = fill_sample_card_variables(sample_cards, sample_groups)
        for fact in facts:
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
        facts = apply_sample_value_alignment(facts)
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

        # Save deterministic sample cards to the existing sample_catalogs table.
        for s in sample_cards:
            db.add(SampleCatalog(
                paper_id=paper_id,
                project_id=paper.project_id,
                sample_id=s.get("sample_id", ""),
                sample_aliases=s.get("sample_aliases") or None,
                sample_group_id=s.get("sample_group_id", "G000"),
                material_system=s.get("material_system", ""),
                fiber_type=s.get("fiber_type", ""),
                variable_name=s.get("variable_name", ""),
                variable_value=s.get("variable_value", ""),
                variable_unit=s.get("variable_unit", ""),
                composition_expression=s.get("composition_expression", ""),
                process_route=s.get("process_route", ""),
                source_location=s.get("source_location", ""),
                evidence_text=s.get("evidence_text", ""),
                confidence=float(s.get("confidence", 0.5) or 0.5),
            ))
        await db.commit()

        await _emit("extracting", 75, "Stage 3完成: 正在保存事实候选...")
        await V7ExtractorService._check_cancelled(db, job_id)

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
                source_block_id=f.get("_source_block_id"),
                source_page=f.get("_source_page"),
                source_bbox_json=json.dumps(f.get("_source_bbox"), ensure_ascii=False)
                if f.get("_source_bbox") is not None else None,
                extraction_method=f.get("extraction_method", "AI_text"),
                confidence=float(f.get("confidence", 0.5)),
                assigned_sample_id=f.get("assigned_sample_id"),
                assignment_confidence=f.get("assignment_confidence"),
                assignment_status=f.get("assignment_status", "unassigned"),
            ))
        await db.commit()

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
        await _emit("saving", 85, "Stage 4: 正在生成候选记录...")

        # -- Save candidate records --
        saved_count = 0
        block_type_by_id = {
            block.block_id: block.block_type for block in document_context.blocks
        }
        for r in records:
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
            db.add(rec)
            await db.flush()

            # Save evidence item linking back to the fact
            db.add(EvidenceItem(
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

        await _emit("saving", 92, f"已生成 {saved_count} 条候选记录, 正在保存报告...")

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
            },
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

        await _emit("completed", 100, f"抽取完成: {saved_count} 条记录")

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
        }
