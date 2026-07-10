"""V6-style holistic extraction: large-context sample catalog + performance sweep.

Keeps V7 export shape (40-column rows) while restoring high-recall Results scanning.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.services.grouping import normalize_sample_id
from app.services.metrics_dictionary import find_category_for_metric, find_metric_canonical

EXPERIMENTAL_SECTIONS = frozenset({"experimental", "materials", "methods", "introduction"})
RESULTS_SECTIONS = frozenset({"results", "conclusion", "discussion"})
BACKGROUND_SECTIONS = frozenset({"title_abstract", "references", "background", "introduction"})

SAMPLES_PROMPT = """You are an AI material data architect. Extract ALL prepared fiber/material samples from the experimental section.

For each distinct sample or specimen, output:
- sample_id: concise ID (e.g. PCF_1.0wtCNC, PI1 aerogel, 2MZ-AZINE-PI3 aerogel, PI nanofiber)
- material_system: e.g. PVDF/recycled cellulose/CNC
- composition: short composition description
- fiber_type: aerogel / nanofiber / film / bulk when identifiable
- variable_name / variable_value / variable_unit: if this sample is a variant in a series (e.g. CNC loading)

Rules:
1. Include control samples, pulp/intermediates, fabrics/devices, and final composite fibers.
2. Use IDs that appear in tables/figures when possible.
3. Do NOT invent samples not supported by text.
4. Do NOT extract sample names from Introduction/background paragraphs describing prior literature.
5. Keep aerogel and nanofiber as separate specimens even when chemically related.

Output JSON:
{"samples": [{"sample_id": "", "material_system": "", "composition": "", "fiber_type": "", "variable_name": "", "variable_value": "", "variable_unit": "", "aliases": []}]}"""

BACKGROUND_PROMPT = """Extract composition, fabrication process, and structure characterization context for fiber material samples.

Output JSON:
{
  "composition": {
    "composition_expression": "",
    "matrix_name": "",
    "matrix_content": "",
    "matrix_unit": "",
    "additive_expression": "",
    "solvent_or_aid": "",
    "composition_evidence": ""
  },
  "process": {
    "process_route": "",
    "spinning_method": "",
    "process_parameters": "",
    "post_treatment": "",
    "process_evidence": ""
  },
  "structure": {
    "structure_methods": "",
    "structure_features": "",
    "structure_evidence": ""
  }
}

Use detailed prose in process_parameters (temperatures, times, concentrations, equipment).
Quote evidence locations like p.2, Section 2.2 when visible."""

PERFORMANCE_PROMPT = """You are a fiber material data scientist. Extract ALL numerical material properties from the results section.

Known sample IDs (use these when matching; you may add aliases seen in text):
{sample_ids}

Extract EVERY sample+metric pair with exact value and unit. Include:
- mechanical: tensile_strength, fracture_toughness, elongation, modulus, compressive_stress, cyclic_compression_stability
- thermal: melting_temperature, crystallinity_Xc, beta_phase_crystallinity_Xbeta, thermal_conductivity, surface_temperature
- dielectric/piezoelectric: dielectric_constant, loss_tangent, piezoelectric_coefficient_d33, open_circuit_voltage, short_circuit_current
- physical/characterization: whiteness, alpha_cellulose_content, degree_of_polymerization, average_particle_size, density, porosity, shrinkage, water_contact_angle
- sensing: sensitivity_low_pressure, sensitivity_high_pressure, response_time, cyclic_stability
- spectroscopy peaks: alpha_phase_XRD_peak_1, beta_phase_FTIR_band_1, etc. when numeric

Rules:
1. ONE numerical value = ONE entry. If one sample has 10 metrics, output 10 entries.
2. Do NOT skip table rows or figure subpanels.
3. Include performance_method, performance_condition, source_location (page/fig/table).
4. Include evidence_text quoting the source sentence.
5. Skip ALL literature-reference / Introduction / prior-work values from other papers.
6. Do NOT extract EMI shielding effectiveness unless clearly an experimental result from THIS paper.
7. Use loss_tangent for tan δ; use dielectric_loss only for ε″ / dielectric loss (not tan δ).
8. Cycle counts (e.g. 500) belong in performance_condition, NOT as the main performance_value. Use cyclic_compression_stability for the outcome.
9. Fill performance_condition with frequency band, hot-stage T, humidity, thickness, duration, strain % when stated.
10. Assign tensile_strength to nanofiber samples; assign compressive/thermal/dielectric/surface_temperature to aerogel samples.

Output JSON:
{{"performances": [{{"sample_id": "", "performance_metric": "", "performance_value": "", "performance_unit": "", "performance_category": "", "performance_method": "", "performance_condition": "", "source_location": "", "evidence_text": ""}}]}}"""

SENSING_SWEEP_PROMPT = """You are a wearable sensor data scientist. Extract ALL numerical sensing and piezoelectric output data.

Known sample IDs:
{sample_ids}

Focus on device/fabric/sensor demonstrations in Results. Extract EVERY distinct numeric reading:
- open_circuit_voltage, short_circuit_current, output_power_density
- sensitivity_low_pressure, sensitivity_high_pressure, gauge_factor, sensing_sensitivity
- response_time, recovery_time, detection_limit, working_range
- cyclic_stability, linearity_R2 (low/high pressure if split)
- finger_bending_fast_current, finger_bending_slow_current, running_output_current
- maximum_tested_force, detection_limit_force

Rules:
1. ONE numeric value = ONE entry. Separate each sample, each pressure range, each cycle count.
2. Include performance_method, performance_condition (pressure, frequency, bending angle).
3. Quote evidence_text and source_location (Fig./Table/page).
4. Skip values cited only from other papers.

Output JSON:
{{"performances": [{{"sample_id": "", "performance_metric": "", "performance_value": "", "performance_unit": "", "performance_category": "sensing", "performance_method": "", "performance_condition": "", "source_location": "", "evidence_text": ""}}]}}"""

SPECTROSCOPY_SWEEP_PROMPT = """You are a materials characterization specialist. Extract ALL numbered spectroscopy/diffraction peak positions.

Known sample IDs:
{sample_ids}

For XRD, FTIR, Raman, XPS peaks: output ONE row per numeric peak position.
Use performance_metric names like:
- alpha_phase_XRD_peak_1, alpha_phase_XRD_peak_2, beta_phase_XRD_peak_1
- beta_phase_FTIR_band_1, beta_phase_FTIR_band_2
- XPS_binding_energy_peak_1 (when applicable)

If phase (alpha/beta/gamma) is unclear, still number peaks sequentially per technique per sample.

Rules:
1. Each peak wavelength/wavenumber/2θ/binding energy = separate entry.
2. Include performance_method (XRD, FTIR, Raman, XPS) and performance_unit (°, cm⁻¹, eV).
3. Do not merge multiple peaks into one row.

Output JSON:
{{"performances": [{{"sample_id": "", "performance_metric": "", "performance_value": "", "performance_unit": "", "performance_category": "structure", "performance_method": "", "performance_condition": "", "source_location": "", "evidence_text": ""}}]}}"""


@dataclass
class HolisticExtractionResult:
    samples: list[dict] = field(default_factory=list)
    background: dict[str, dict] = field(default_factory=dict)
    performance_facts: list[dict] = field(default_factory=list)
    experimental_chars: int = 0
    results_chars: int = 0


def _chunk_header(chunk: dict) -> str:
    page = chunk.get("page_number")
    section = chunk.get("section_name") or "unknown"
    source = chunk.get("source_type") or "text"
    loc = chunk.get("source_location") or ""
    return f"[page {page} | {section} | {source} | {loc}]"


def merge_chunks_text(
    chunks: list[dict],
    *,
    sections: frozenset[str] | None = None,
    source_types: frozenset[str] | None = None,
    max_chars: int = 28000,
) -> str:
    """Merge chunk texts in reading order up to max_chars."""
    ordered = sorted(
        chunks,
        key=lambda c: (c.get("page_number") or 0, c.get("order_index") or 0),
    )
    parts: list[str] = []
    total = 0
    for chunk in ordered:
        section = (chunk.get("section_name") or "").lower()
        if section in BACKGROUND_SECTIONS:
            continue
        if sections and section not in sections:
            if not (source_types and chunk.get("source_type") in source_types):
                continue
        if source_types and chunk.get("source_type") not in source_types:
            if not (sections and section in sections):
                continue
        text = (chunk.get("raw_text") or "").strip()
        if not text:
            continue
        block = f"{_chunk_header(chunk)}\n{text}"
        if total + len(block) > max_chars:
            remaining = max_chars - total
            if remaining > 500:
                parts.append(block[:remaining])
            break
        parts.append(block)
        total += len(block) + 2
    return "\n\n".join(parts)


def build_context_texts(chunks: list[dict], *, results_max_chars: int = 35000) -> tuple[str, str]:
    """Build experimental and results merged texts from MinerU chunks."""
    experimental = merge_chunks_text(
        chunks,
        sections=EXPERIMENTAL_SECTIONS,
        max_chars=18000,
    )
    if len(experimental) < 3000:
        experimental = merge_chunks_text(chunks, max_chars=18000)
    results = merge_chunks_text(
        chunks,
        sections=RESULTS_SECTIONS,
        source_types=frozenset({"table_text", "figure_caption", "text"}),
        max_chars=results_max_chars,
    )
    if len(results) < 3000:
        results = merge_chunks_text(
            chunks,
            source_types=frozenset({"table_text", "figure_caption"}),
            max_chars=results_max_chars,
        )
    if len(results) < 3000:
        results = merge_chunks_text(chunks, max_chars=results_max_chars)
    return experimental, results


def catalog_to_mentions(samples: list[dict]) -> list[dict]:
    mentions: list[dict] = []
    for sample in samples:
        sid = normalize_sample_id(sample.get("sample_id") or "")
        if not sid:
            continue
        mentions.append({
            "mention_text": sid,
            "normalized_sample_id": sid,
            "aliases": sample.get("aliases") or [],
            "context_text": (
                sample.get("composition")
                or sample.get("material_system")
                or ""
            )[:300],
            "source_location": "holistic_catalog",
            "source_type": "text",
            "confidence": 0.88,
        })
    return mentions


def performances_to_facts(
    performances: list[dict],
    *,
    start_index: int = 1,
) -> list[dict]:
    facts: list[dict] = []
    counter = start_index
    for row in performances:
        sid = normalize_sample_id(row.get("sample_id") or "")
        metric = (row.get("performance_metric") or "").strip()
        value = str(row.get("performance_value") or "").strip()
        if not sid or not metric or not value:
            continue
        metric = find_metric_canonical(metric) or metric
        fact_id = f"H{counter:04d}"
        counter += 1
        category = row.get("performance_category") or find_category_for_metric(metric)
        facts.append({
            "fact_id": fact_id,
            "fact_type": "performance",
            "subject_text": metric,
            "candidate_sample_ids": [sid],
            "metric_or_parameter": metric,
            "value": value,
            "unit": row.get("performance_unit") or "",
            "method": row.get("performance_method") or "",
            "condition": row.get("performance_condition") or "",
            "category": category,
            "evidence_text": row.get("evidence_text") or "",
            "source_location": row.get("source_location") or "results_text",
            "extraction_method": "AI_holistic",
            "confidence": 0.88,
            "assigned_sample_id": sid,
            "assignment_status": "assigned",
            "assignment_confidence": 0.9,
            "assignment_reason": "holistic_performance_sweep",
        })
    return facts


def _fact_key(fact: dict) -> tuple[str, str, str]:
    sid = normalize_sample_id(fact.get("assigned_sample_id") or "")
    metric = find_metric_canonical(fact.get("metric_or_parameter") or "") or (
        fact.get("metric_or_parameter") or ""
    )
    metric = re.sub(r"\s+", "_", metric.lower().strip())
    value = str(fact.get("value") or "").strip()
    return sid, metric, value


def _fact_rank(fact: dict) -> int:
    score = 0
    if fact.get("extraction_method") == "AI_holistic":
        score += 4
    if fact.get("_source_block_id"):
        score += 2
    if fact.get("assigned_sample_id"):
        score += 2
    if fact.get("evidence_text"):
        score += 1
    return score


def merge_holistic_and_atomic_facts(
    atomic_facts: list[dict],
    holistic_facts: list[dict],
) -> list[dict]:
    """Merge facts; prefer rows with sample assignment and holistic extraction."""
    non_perf: list[dict] = []
    metric_value_map: dict[tuple[str, str], dict] = {}

    for fact in atomic_facts + holistic_facts:
        if fact.get("fact_type") != "performance":
            non_perf.append(fact)
            continue
        sid, metric, value = _fact_key(fact)
        if not metric or not value:
            continue
        key = (sid, metric, value)
        current = metric_value_map.get(key)
        if current is None or _fact_rank(fact) > _fact_rank(current):
            metric_value_map[key] = fact

    return non_perf + list(metric_value_map.values())


async def _run_performance_sweep(
    *,
    prompt: str,
    results_text: str,
    sample_ids: list[str],
    llm_json: Callable[..., Awaitable[tuple[dict, str]]],
    llm_timeout: int,
    max_tokens: int,
    stage: str,
    results_max_chars: int,
) -> list[dict]:
    if not results_text.strip() or not sample_ids:
        return []
    parsed, _ = await llm_json(
        prompt.format(sample_ids=", ".join(sample_ids)),
        f"Results text:\n{results_text[:results_max_chars]}",
        max_tokens=max_tokens,
        timeout_seconds=llm_timeout,
        stage=stage,
    )
    performances = parsed.get("performances") or parsed.get("_items") or []
    return performances_to_facts(performances)


def enrich_sample_cards(
    cards: list[dict],
    samples: list[dict],
    background: dict[str, dict],
) -> list[dict]:
    """Apply holistic catalog + shared background fields to sample cards."""
    comp = background.get("composition") or {}
    proc = background.get("process") or {}
    struct = background.get("structure") or {}
    catalog_by_id = {
        normalize_sample_id(s.get("sample_id") or ""): s
        for s in samples
        if s.get("sample_id")
    }
    card_by_id = {c.get("sample_id"): c for c in cards if c.get("sample_id")}

    for sid, sample in catalog_by_id.items():
        if sid not in card_by_id:
            card_by_id[sid] = {
                "sample_id": sid,
                "sample_aliases": "",
                "sample_group_id": "G000",
                "material_system": "",
                "fiber_type": "",
                "variable_name": "",
                "variable_value": "",
                "variable_unit": "",
                "composition_expression": "",
                "matrix_name": "",
                "matrix_content": "",
                "matrix_unit": "",
                "additive_expression": "",
                "solvent_or_aid": "",
                "composition_evidence": "",
                "process_route": "",
                "spinning_method": "",
                "process_parameters": "",
                "post_treatment": "",
                "process_evidence": "",
                "structure_methods": "",
                "structure_features": "",
                "structure_evidence": "",
                "source_location": "holistic_catalog",
                "evidence_text": "",
                "confidence": 0.85,
            }

    def _fill(card: dict, key: str, value: Any) -> None:
        if value and not card.get(key):
            card[key] = value

    for sid, card in card_by_id.items():
        sample = catalog_by_id.get(sid, {})
        _fill(card, "material_system", sample.get("material_system"))
        _fill(card, "fiber_type", sample.get("fiber_type"))
        _fill(card, "variable_name", sample.get("variable_name"))
        _fill(card, "variable_value", sample.get("variable_value"))
        _fill(card, "variable_unit", sample.get("variable_unit"))
        _fill(card, "composition_expression", sample.get("composition") or comp.get("composition_expression"))
        _fill(card, "matrix_name", comp.get("matrix_name"))
        _fill(card, "matrix_content", comp.get("matrix_content"))
        _fill(card, "matrix_unit", comp.get("matrix_unit"))
        _fill(card, "additive_expression", comp.get("additive_expression"))
        _fill(card, "solvent_or_aid", comp.get("solvent_or_aid"))
        _fill(card, "composition_evidence", comp.get("composition_evidence"))
        _fill(card, "process_route", proc.get("process_route"))
        _fill(card, "spinning_method", proc.get("spinning_method"))
        _fill(card, "process_parameters", proc.get("process_parameters"))
        _fill(card, "post_treatment", proc.get("post_treatment"))
        _fill(card, "process_evidence", proc.get("process_evidence"))
        _fill(card, "structure_methods", struct.get("structure_methods"))
        _fill(card, "structure_features", struct.get("structure_features"))
        _fill(card, "structure_evidence", struct.get("structure_evidence"))
        if not card.get("material_system"):
            card["material_system"] = card.get("composition_expression") or card.get("matrix_name") or ""

    return list(card_by_id.values())


_SENSING_HINTS = (
    "sensor", "sensing", "peng", "piezoelectric", "gauge factor",
    "pressure sensing", "wearable", "detection limit", "working range",
    "open circuit voltage", "short circuit current", "finger bending",
)

_SPECTROSCOPY_HINTS = (
    "xrd", "ftir", "raman", "xps", "binding energy", "2theta", "2θ",
    "wavenumber", "diffraction peak", "spectroscop",
)


def _needs_sensing_sweep(results_text: str) -> bool:
    lower = (results_text or "").lower()
    return any(hint in lower for hint in _SENSING_HINTS)


def _needs_spectroscopy_sweep(results_text: str) -> bool:
    lower = (results_text or "").lower()
    return any(hint in lower for hint in _SPECTROSCOPY_HINTS)


async def run_holistic_extraction(
    *,
    chunks: list[dict],
    llm_json: Callable[..., Awaitable[tuple[dict, str]]],
    llm_timeout: int,
    max_performance_tokens: int = 6000,
    results_max_chars: int = 35000,
    sensing_enabled: bool = True,
) -> HolisticExtractionResult:
    """Run V6-style large-context catalog + performance sweep."""
    experimental, results = build_context_texts(chunks, results_max_chars=results_max_chars)
    result = HolisticExtractionResult(
        experimental_chars=len(experimental),
        results_chars=len(results),
    )
    if not experimental.strip() and not results.strip():
        return result

    if experimental.strip():
        parsed, _ = await llm_json(
            SAMPLES_PROMPT,
            f"Experimental text:\n{experimental[:16000]}",
            max_tokens=2500,
            timeout_seconds=llm_timeout,
            stage="holistic_samples",
        )
        result.samples = [
            s for s in (parsed.get("samples") or parsed.get("_items") or [])
            if isinstance(s, dict) and s.get("sample_id")
        ]

        sample_hint = ", ".join(
            normalize_sample_id(s.get("sample_id") or "") for s in result.samples
        ) or "unknown"
        bg_parsed, _ = await llm_json(
            BACKGROUND_PROMPT,
            f"Known samples: {sample_hint}\n\nExperimental text:\n{experimental[:14000]}",
            max_tokens=2000,
            timeout_seconds=llm_timeout,
            stage="holistic_background",
        )
        if not bg_parsed.get("_parse_failed"):
            result.background = {
                "composition": bg_parsed.get("composition") or {},
                "process": bg_parsed.get("process") or {},
                "structure": bg_parsed.get("structure") or {},
            }

    sample_ids = [
        normalize_sample_id(s.get("sample_id") or "")
        for s in result.samples
        if s.get("sample_id")
    ]
    if results.strip() and sample_ids:
        sweep_specs: list[tuple[str, str, int]] = [
            (PERFORMANCE_PROMPT, "holistic_performances", max_performance_tokens),
        ]
        if sensing_enabled and _needs_sensing_sweep(results):
            sweep_specs.append(
                (SENSING_SWEEP_PROMPT, "holistic_sensing", min(max_performance_tokens, 4500)),
            )
        if _needs_spectroscopy_sweep(results):
            sweep_specs.append(
                (SPECTROSCOPY_SWEEP_PROMPT, "holistic_spectroscopy", min(max_performance_tokens, 4500)),
            )

        async def _run_one(prompt: str, stage: str, max_tokens: int) -> list[dict]:
            return await _run_performance_sweep(
                prompt=prompt,
                results_text=results,
                sample_ids=sample_ids,
                llm_json=llm_json,
                llm_timeout=llm_timeout,
                max_tokens=max_tokens,
                stage=stage,
                results_max_chars=results_max_chars,
            )

        batches = await asyncio.gather(*[
            _run_one(prompt, stage, max_tokens)
            for prompt, stage, max_tokens in sweep_specs
        ])
        start_index = 1
        merged_facts: list[dict] = []
        for batch in batches:
            for fact in batch:
                fact["fact_id"] = f"H{start_index:04d}"
                start_index += 1
            merged_facts.extend(batch)
        result.performance_facts = merge_holistic_and_atomic_facts([], merged_facts)

    return result
