"""V6-style holistic extraction: large-context sample catalog + performance sweep.

Keeps V7 export shape (40-column rows) while restoring high-recall Results scanning.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.services.grouping import (
    is_material_sample_id,
    normalize_for_match,
    normalize_sample_id,
)
from app.services.metrics_dictionary import (
    find_category_for_metric,
    find_metric_canonical,
    find_process_parameter_canonical,
)
from app.services.extractor_v7.metric_normalize import canonicalize_metric_name

EXPERIMENTAL_SECTIONS = frozenset({"experimental", "materials", "methods", "introduction"})
RESULTS_SECTIONS = frozenset({"results", "conclusion", "discussion"})
BACKGROUND_SECTIONS = frozenset({
    "title_abstract", "references", "back_matter", "background", "introduction",
})
IGNORED_CONTEXT_BLOCK_TYPES = frozenset({
    "aside_text", "header_footer", "page_number", "ref_text",
})

_MEASUREMENT_RE = re.compile(
    r"(?i)[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\s*(?:%|MPa|GPa|kPa|Pa|"
    r"g\s*/\s*g|mg\s*/\s*g|W\s*/\s*mK|S\s*/\s*(?:m|cm)|pC\s*/\s*N|"
    r"kg\s*m\s*(?:\^?\s*-?3|⁻³)|g\s*cm\s*(?:\^?\s*-?3|⁻³)|"
    r"kN|mN|N|MHz|kHz|Hz|m\s*s\s*(?:\^?\s*-?1|⁻¹)|m/s|"
    r"V|mV|µV|μV|A|mA|µA|μA|nA|nm|µm|μm|mm|cm[⁻-]?1|eV|°C|K)(?=\s|[),.;]|$)"
)
_RESULT_TERM_RE = re.compile(
    r"(?i)\b(?:strength|modulus|elongation|conductivit|capacity|absorption|sorption|"
    r"weight\s+loss|mass\s+loss|WPG|porosity|density|contact\s+angle|temperature|"
    r"crystallinity|permittivity|dielectric|voltage|current|sensitivity|response\s+time|"
    r"degree\s+of|retention|efficiency|transmittance|reflectance|toughness|"
    r"load|force|displacement|stress|strain|stiffness|band\s*gap|frequency|"
    r"transmission|acceleration|damping|vibration|energy\s+absorption)\b"
)

SAMPLES_PROMPT = """You are an AI material data architect. Extract ALL prepared fiber/material samples from the experimental section.

For each distinct sample or specimen, output:
- sample_id: concise ID (e.g. PCF_1.0wtCNC, PI1 aerogel, 2MZ-AZINE-PI3 aerogel, PI nanofiber)
- material_system: e.g. PVDF/recycled cellulose/CNC
- composition: short composition description
- fiber_type: aerogel / nanofiber / film / bulk when identifiable
- variable_name / variable_value / variable_unit: if this sample is a variant in a series (e.g. CNC loading)

Rules:
1. Include control samples, fabrics/devices, and final composite fibers. Include a constituent or intermediate only when this paper reports that material's own result.
2. Use IDs that appear in tables/figures when possible.
3. Do NOT invent samples not supported by text.
4. Do NOT extract sample names from Introduction/background paragraphs describing prior literature.
5. Keep aerogel and nanofiber as separate specimens even when chemically related.
6. Raw/control/untreated and treated/modified variants are DISTINCT samples. Emit one row per variant.
7. Never join distinct samples into one sample_id with "and", "/", or a phrase such as "both samples".
8. A run/table label such as "sample 12" may be an alias, but do not replace the material identity with it.
9. Apparatus, reservoirs, collectors, needles, pumps, and setup boxes are NOT material samples. If a setup produces a distinct material variant, name the material plus the setup variable (for example, PAN_nanofiber_17_needles).
10. SEM/TEM sputter coating or other characterization preparation does NOT create a new material sample; record it later as characterization/post-treatment context.
11. Do NOT expand a continuous range or parameter sweep (for example, 1% to 20%) into one sample per integer. Emit an individual variant only when that exact value is separately described, labeled, simulated, or measured in the source text.
12. Include a matrix or reinforcement constituent as its own material entry when the paper reports that constituent's own physical properties. Never assign a constituent property to a composite variant.
13. Process-optimization combinations (flow rate, concentration, voltage, needle size, and similar settings) are conditions, not samples, unless the paper gives an explicit sample label and reports a separate material result.
14. Test or immersion time points are performance conditions, not new samples. Keep one base material ID across those time points.
15. Do not emit precursor or spinning solutions unless this paper measures the solution's own material property.
16. Make one-letter labels descriptive by appending the material form, for example "S BG" instead of "S".

Output JSON:
{"samples": [{"sample_id": "", "material_system": "", "composition": "", "fiber_type": "", "variable_name": "", "variable_value": "", "variable_unit": "", "aliases": []}]}"""

TREATMENT_VARIANTS_PROMPT = """Extract only material variants created by a chemical or physical treatment in this paper.

Rules:
1. If one shared treatment applies to multiple explicitly named base materials, emit one treated variant for each base material.
2. Use descriptive IDs such as "AA-treated S BG"; never return a one-letter ID.
3. Do NOT emit untreated bases, test/immersion time points, characterization preparation, cut specimens, or process-parameter combinations.
4. Do NOT infer a treated variant from cited prior work.

Return JSON only:
{"samples": [{"sample_id": "", "material_system": "", "composition": "", "fiber_type": "", "aliases": []}]}"""

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
- mechanical: tensile_strength, fracture_toughness, elongation, modulus, compressive_stress, cyclic_compression_stability, knee_strain, damage_transition_strain, stiffness_recovery_strain
- thermal: melting_temperature, crystallinity_Xc, beta_phase_crystallinity_Xbeta, thermal_conductivity, surface_temperature
- dielectric/piezoelectric: dielectric_constant, loss_tangent, piezoelectric_coefficient_d33, open_circuit_voltage, short_circuit_current
- physical/characterization: whiteness, alpha_cellulose_content, degree_of_polymerization, average_particle_size, density, porosity, shrinkage, water_contact_angle
- sensing: sensitivity_low_pressure, sensitivity_high_pressure, response_time, cyclic_stability
- mechanics/metamaterials: compressive_displacement, softening_load, load_bearing_stability_improvement, bandgap_frequency_range, transmission_attenuation_frequency_range, maximum_acceleration, acceleration_reduction, specific_energy_absorption
- any other explicit numerical material or structural performance result, even when its metric is not listed above

Rules:
1. ONE numerical value = ONE entry. If one sample has 10 metrics, output 10 entries.
2. Do NOT skip table rows or figure subpanels.
3. Include performance_method, performance_condition, source_location (page/fig/table).
4. evidence_text must contain the sample, metric and value. If the sentence uses "both samples" or a pronoun, quote the preceding sentence too.
5. Skip ALL literature-reference / Introduction / prior-work values from other papers.
6. Do NOT extract EMI shielding effectiveness unless clearly an experimental result from THIS paper.
7. Use loss_tangent for tan δ; use dielectric_loss only for ε″ / dielectric loss (not tan δ).
8. Cycle counts (e.g. 500) belong in performance_condition, NOT as the main performance_value. Use cyclic_compression_stability for the outcome.
9. Fill performance_condition with test frequency, hot-stage T, humidity, thickness, duration, strain % when stated. A bandgap, resonance, eigenfrequency, or transmission-attenuation frequency/range is the main result, not a test condition.
10. Assign tensile_strength to nanofiber samples; assign compressive/thermal/dielectric/surface_temperature to aerogel samples.
11. Bind paired values exactly: "A and B were X and Y, respectively" means X→A and Y→B; "X for A and Y for B" keeps those explicit pairs.
12. The main value must be the claimed material result. NEVER emit uncertainty/standard deviation, sample or run number, catalyst/reagent amount, concentration, temperature, time, pressure, or cycle count as the performance value.
13. sample_id must be a material/specimen identity, not a process phrase or experimental condition.
14. A strain value is a result, not merely a condition, only when the same quoted evidence explicitly binds it to knee position, a damage-index change, or stiffness recovery. A generic transition-zone boundary is not enough; never relabel one phenomenon as another.
15. Do not force a result onto the nearest Known sample when the concentration/configuration differs. Create a concise evidence-grounded sample ID for an explicitly described new configuration.
16. Copy source_block_id exactly from the input block header and source_page from its page number.
17. Do NOT output FTIR, Raman, XRD, XPS, NMR, or other peak positions here; a dedicated characterization pass handles them.

Output JSON:
{{"performances": [{{"sample_id": "", "performance_metric": "", "performance_value": "", "performance_unit": "", "performance_category": "", "performance_method": "", "performance_condition": "", "source_location": "", "source_block_id": "", "source_page": 0, "evidence_text": ""}}]}}"""

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
4. sample_id must be exactly ONE Known sample ID. Never use a peak/band/mode description, property name, or joined label such as "A/B" as sample_id.
5. When one peak explicitly applies to multiple Known samples, output one row per sample. If the source does not identify the sample unambiguously, omit that row.
6. Copy source_block_id exactly from the input block header and source_page from its page number.
7. Extract only measurements from this paper, never values cited from prior literature.

Output JSON:
{{"performances": [{{"sample_id": "", "performance_metric": "", "performance_value": "", "performance_unit": "", "performance_category": "structure", "performance_method": "", "performance_condition": "", "source_location": "", "source_block_id": "", "source_page": 0, "evidence_text": ""}}]}}"""

TABLE_PERFORMANCE_PROMPT = """Extract measured material results from one structured table.

Known material samples: {sample_ids}

Return compact JSON:
{{"rows": [{{"row": 1, "sample_id": "", "metric": "", "value": "", "unit": "", "condition": ""}}]}}

Rules:
1. Process every [row N] and every measured-result column; one measured cell = one output row.
2. Copy row, value and unit exactly. Never calculate, interpolate or invent a value.
3. Row/sample number, temperature, time, concentration, composition/loading and cycle count are conditions, not measured results.
4. Put all non-result columns needed to distinguish the row into condition.
5. Use exactly one Known material sample ID as the base and append the table row label when rows are distinct specimens/runs. Never replace a known material with generic "sample N". A reuse cycle stays a condition on the same material.
6. Skip empty cells and purely descriptive columns. A material name containing digits (for example Polyamide 6.6), instrument model, yarn specification, or test standard is not a numeric result.
7. If row labels are metrics and column labels are samples/directions, use the row label as metric and the column label as the sample/condition; never swap them.
8. SD, standard error and uncertainty columns belong in condition and are never separate measured results."""

TABLE_REPAIR_PROMPT = """Repair only the listed missing measured cells from one structured table.

Known material samples: {sample_ids}
Missing cells: {missing_cells}

Return compact JSON:
{{"rows": [{{"row": 1, "sample_id": "", "metric": "", "value": "", "unit": "", "condition": ""}}]}}

Rules:
1. Return exactly one row for each listed cell when that cell contains a measured result.
2. Copy the value from the named source cell exactly, including mean(std) notation.
3. Never return unlisted cells, uncertainty as a separate result, or process/test conditions as results.
4. Keep the source [row N] number in row and use the column header as the metric."""


@dataclass
class HolisticExtractionResult:
    samples: list[dict] = field(default_factory=list)
    background: dict[str, dict] = field(default_factory=dict)
    performance_facts: list[dict] = field(default_factory=list)
    experimental_chars: int = 0
    results_chars: int = 0
    warnings: list[str] = field(default_factory=list)
    covered_table_block_ids: list[str] = field(default_factory=list)


def _flatten_dict_rows(value: Any) -> list[dict]:
    """Flatten model list/list-of-lists drift while rejecting scalar debris."""
    if isinstance(value, dict):
        return [value]
    if not isinstance(value, list):
        return []
    rows: list[dict] = []
    for item in value:
        rows.extend(_flatten_dict_rows(item))
    return rows


def _response_rows(parsed: Any, *keys: str) -> list[dict]:
    if isinstance(parsed, dict):
        for key in keys:
            value = parsed.get(key)
            if value:
                return _flatten_dict_rows(value)
        return []
    return _flatten_dict_rows(parsed)


def _chunk_header(chunk: dict) -> str:
    page = chunk.get("page_number")
    section = chunk.get("section_name") or "unknown"
    source = chunk.get("source_type") or "text"
    loc = chunk.get("source_location") or ""
    block_id = chunk.get("source_block_id") or ""
    return f"[page {page} | {section} | {source} | block {block_id} | {loc}]"


def merge_chunks_text(
    chunks: list[dict],
    *,
    sections: frozenset[str] | None = None,
    source_types: frozenset[str] | None = None,
    exclude_source_types: frozenset[str] | None = None,
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
        if str(chunk.get("block_type") or "").lower() in IGNORED_CONTEXT_BLOCK_TYPES:
            continue
        if exclude_source_types and chunk.get("source_type") in exclude_source_types:
            continue
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


def select_performance_context_chunks(
    chunks: list[dict],
    *,
    max_chars: int,
) -> list[dict]:
    """Keep numeric result blocks across the whole paper plus local context neighbors."""
    ordered = sorted(
        chunks,
        key=lambda chunk: (chunk.get("page_number") or 0, chunk.get("order_index") or 0),
    )
    candidates = [
        chunk for chunk in ordered
        if chunk.get("source_type") != "table_text"
        and str(chunk.get("block_type") or "").lower() not in {
            "ref_text", "header_footer", "page_number",
        }
        and (chunk.get("section_name") or "").lower() not in BACKGROUND_SECTIONS
        and (
            (chunk.get("section_name") or "").lower() in RESULTS_SECTIONS
            or chunk.get("source_type") == "figure_caption"
        )
        and str(chunk.get("raw_text") or "").strip()
    ]
    scores: dict[int, int] = {}
    for index, chunk in enumerate(candidates):
        text = str(chunk.get("raw_text") or "")
        score = len(_MEASUREMENT_RE.findall(text)) * 4
        score += len(_RESULT_TERM_RE.findall(text))
        if chunk.get("source_type") == "figure_caption" and re.search(r"\d", text):
            score += 4
        if score > 0:
            scores[index] = score

    if not scores:
        return candidates

    selected_indices = set(scores)
    for index in list(scores):
        for neighbor in (index - 1, index + 1):
            if 0 <= neighbor < len(candidates):
                if candidates[neighbor].get("page_number") == candidates[index].get("page_number"):
                    selected_indices.add(neighbor)

    selected = [candidates[index] for index in sorted(selected_indices)]
    total_chars = sum(len(str(chunk.get("raw_text") or "")) + 120 for chunk in selected)
    if total_chars <= max_chars:
        return selected

    chosen: set[int] = set()
    used = 0
    best_by_page: dict[int, int] = {}
    for index, score in scores.items():
        page = int(candidates[index].get("page_number") or 0)
        current = best_by_page.get(page)
        if current is None or scores[current] < score:
            best_by_page[page] = index
    priority = list(best_by_page.values())
    priority.extend(
        index for index, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        if index not in best_by_page.values()
    )
    for index in priority:
        size = len(str(candidates[index].get("raw_text") or "")) + 120
        if chosen and used + size > max_chars:
            continue
        chosen.add(index)
        used += size
    return [candidates[index] for index in sorted(chosen)]


_SAMPLE_NAMING_RE = re.compile(
    r"(?i)\b(?:called|named|denoted|labelled|labeled|referred\s+to\s+as)\b|"
    r"\b(?:samples?|specimens?)\s*(?:no\.?|#)?\s*[A-Z]*\d+[A-Za-z0-9_.-]*\b"
)
_SAMPLE_COMPOSITION_ID_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Z][A-Za-z0-9]{0,11}\s*/\s*){1,}"
    r"[A-Z][A-Za-z0-9]{0,11}(?![A-Za-z0-9/])"
)
_SAMPLE_ACRONYM_FORM_RE = re.compile(
    r"\b[A-Z][A-Za-z0-9-]{0,11}\s+(?:BG|glass|fib(?:er|re)s?|"
    r"nanofib(?:er|re)s?|film|membrane|mat|composite|aerogel|powder)s?\b"
)
_SAMPLE_PREPARATION_RE = re.compile(
    r"(?i)\b(?:prepar(?:e|ed|ation)|synthes(?:is|ized?|ised?)|fabricat(?:e|ed|ion)|"
    r"produc(?:e|ed|tion)|manufactur(?:e|ed|ing)|electrospun|cast|cured?)\b"
)
_SAMPLE_TREATMENT_RE = re.compile(
    r"(?i)\b(?:treated|modified|functionalized|coated|doped|annealed|carbonized)\b"
)
_SAMPLE_PRIMARY_SYNTHESIS_RE = re.compile(
    r"(?i)\b(?:synthesis|synthesized|synthesised|fabrication|fabricated|"
    r"manufactured)\b"
)
_SAMPLE_MATERIAL_RE = re.compile(
    r"(?i)\b(?:samples?|specimens?|materials?|(?:nano)?fib(?:er|re)s?|filaments?|"
    r"yarns?|fabrics?|mats?|films?|membranes?|composites?|aerogels?|hydrogels?|"
    r"foams?|powders?|particles?|glasses?|laminates?|scaffolds?)\b"
)
_SAMPLE_CONTROL_RE = re.compile(
    r"(?i)\b(?:control|reference|neat|untreated|treated|modified)\s+"
    r"(?:samples?|materials?|(?:nano)?fib(?:er|re)s?|films?|membranes?|"
    r"composites?|glasses?|powders?)\b"
)
_SAMPLE_TEST_METHOD_RE = re.compile(
    r"(?i)\b(?:characteri[sz](?:ation|ed)|measurements?|spectroscop\w*|"
    r"microscop\w*|morphology|cell\s+viability|cellular\s+test|cell\s+seeding|"
    r"assays?|testing\s+machine|tensile\s+test|contact\s+angle|"
    r"different\s+(?:periods|time\s+points))\b"
)
_SAMPLE_OPTIMIZATION_RE = re.compile(
    r"(?i)\b(?:process\s+parameters?\s+(?:were\s+)?(?:optimized|varied)|"
    r"optimized\s+by\s+changing|parameter\s+sweep|screening\s+of|"
    r"flow\s+rates?.{0,80}(?:concentrations?|needle))\b"
)


def select_sample_catalog_context_chunks(
    chunks: list[dict],
    *,
    max_chars: int,
) -> list[dict]:
    """Select preparation and explicit identity blocks for the sample catalog."""
    ordered = sorted(
        chunks,
        key=lambda chunk: (
            chunk.get("page_number") or 0,
            chunk.get("order_index") or 0,
        ),
    )
    candidates = [
        chunk for chunk in ordered
        if (chunk.get("section_name") or "").lower() in EXPERIMENTAL_SECTIONS
        and (chunk.get("section_name") or "").lower() not in BACKGROUND_SECTIONS
        and str(chunk.get("block_type") or "").lower()
        not in IGNORED_CONTEXT_BLOCK_TYPES
        and str(chunk.get("raw_text") or "").strip()
    ]
    scored: list[tuple[int, int]] = []
    for index, chunk in enumerate(candidates):
        text = str(chunk.get("raw_text") or "")
        naming = bool(_SAMPLE_NAMING_RE.search(text))
        composition_id = bool(_SAMPLE_COMPOSITION_ID_RE.search(text))
        acronym_form = bool(_SAMPLE_ACRONYM_FORM_RE.search(text))
        preparation = bool(_SAMPLE_PREPARATION_RE.search(text))
        treatment = bool(_SAMPLE_TREATMENT_RE.search(text))
        material = bool(_SAMPLE_MATERIAL_RE.search(text))
        control = bool(_SAMPLE_CONTROL_RE.search(text))
        strong_identity = naming or composition_id or acronym_form

        score = 0
        if strong_identity:
            score += 8
        if control:
            score += 6
        if preparation and material:
            score += 5
        if treatment and material:
            score += 4
        if chunk.get("source_type") == "table_text":
            score += 4
        if _SAMPLE_OPTIMIZATION_RE.search(text) and not (
            naming or composition_id
        ):
            score -= 8
        if _SAMPLE_TEST_METHOD_RE.search(text) and not (
            naming
            or composition_id
            or (treatment and acronym_form)
            or (_SAMPLE_PRIMARY_SYNTHESIS_RE.search(text) and material)
        ):
            score -= 10
        if score >= 4:
            scored.append((index, score))

    if not scored:
        return candidates

    selected = {index for index, _ in scored}
    for index in tuple(selected):
        previous = index - 1
        if previous < 0:
            continue
        previous_text = str(candidates[previous].get("raw_text") or "").strip()
        if len(previous_text) <= 120 and re.search(
            r"(?i)\b(?:materials?|methods?|synthes|preparation|fabrication)\b",
            previous_text,
        ):
            selected.add(previous)

    selected_chunks = [candidates[index] for index in sorted(selected)]
    total = sum(len(str(chunk.get("raw_text") or "")) + 120 for chunk in selected_chunks)
    if total <= max_chars:
        return selected_chunks

    score_by_index = dict(scored)
    chosen: set[int] = set()
    used = 0
    for index in sorted(selected, key=lambda item: (-score_by_index.get(item, 1), item)):
        size = len(str(candidates[index].get("raw_text") or "")) + 120
        if chosen and used + size > max_chars:
            continue
        chosen.add(index)
        used += size
    return [candidates[index] for index in sorted(chosen)]


def select_treatment_variant_context_chunks(
    chunks: list[dict],
    *,
    max_chars: int = 4500,
) -> list[dict]:
    """Keep compact blocks that explicitly describe treated material variants."""
    candidates = select_sample_catalog_context_chunks(chunks, max_chars=max_chars * 2)
    selected = [
        chunk for chunk in candidates
        if _SAMPLE_TREATMENT_RE.search(str(chunk.get("raw_text") or ""))
        and _SAMPLE_MATERIAL_RE.search(str(chunk.get("raw_text") or ""))
        and (
            _SAMPLE_NAMING_RE.search(str(chunk.get("raw_text") or ""))
            or _SAMPLE_ACRONYM_FORM_RE.search(str(chunk.get("raw_text") or ""))
            or _SAMPLE_COMPOSITION_ID_RE.search(str(chunk.get("raw_text") or ""))
        )
    ]
    used = 0
    bounded: list[dict] = []
    for chunk in selected:
        size = len(str(chunk.get("raw_text") or "")) + 120
        if bounded and used + size > max_chars:
            continue
        bounded.append(chunk)
        used += size
    return bounded


def build_context_texts(chunks: list[dict], *, results_max_chars: int = 35000) -> tuple[str, str]:
    """Build experimental and results merged texts from MinerU chunks."""
    experimental = merge_chunks_text(
        chunks,
        sections=EXPERIMENTAL_SECTIONS,
        max_chars=18000,
    )
    if len(experimental) < 3000:
        experimental = merge_chunks_text(chunks, max_chars=18000)
    result_chunks = select_performance_context_chunks(
        chunks,
        max_chars=results_max_chars,
    )
    results = merge_chunks_text(
        result_chunks,
        sections=RESULTS_SECTIONS,
        source_types=frozenset({"table_text", "figure_caption", "text"}),
        exclude_source_types=frozenset({"table_text"}),
        max_chars=results_max_chars,
    )
    if len(results) < 3000:
        results = merge_chunks_text(
            chunks,
            source_types=frozenset({"table_text", "figure_caption"}),
            exclude_source_types=frozenset({"table_text"}),
            max_chars=results_max_chars,
        )
    if len(results) < 3000:
        results = merge_chunks_text(
            chunks,
            exclude_source_types=frozenset({"table_text"}),
            max_chars=results_max_chars,
        )
    return experimental, results


def split_context_windows(
    text: str,
    *,
    max_chars: int,
    overlap_blocks: int = 1,
) -> list[str]:
    """Split merged MinerU text on block headers without dropping source text."""
    normalized = (text or "").strip()
    if not normalized:
        return []
    if max_chars <= 0 or len(normalized) <= max_chars:
        return [normalized]

    blocks = [
        block.strip()
        for block in re.split(r"\n{2,}(?=\[page\s)", normalized)
        if block.strip()
    ]
    if len(blocks) <= 1:
        return [normalized]

    overlap_blocks = max(0, int(overlap_blocks or 0))
    windows: list[str] = []
    current: list[str] = []

    def _joined_length(parts: list[str]) -> int:
        return sum(len(part) for part in parts) + max(0, len(parts) - 1) * 2

    for block in blocks:
        if current and _joined_length(current + [block]) > max_chars:
            windows.append("\n\n".join(current))
            overlap = current[-overlap_blocks:] if overlap_blocks else []
            current = list(overlap)
            if current and _joined_length(current + [block]) > max_chars:
                current = []
        current.append(block)

    if current:
        candidate = "\n\n".join(current)
        if not windows or candidate != windows[-1]:
            windows.append(candidate)
    return windows


_APPARATUS_ID_RE = re.compile(
    r"(?i)\b(?:reservoir|collector|syringe|pump|spinneret|apparatus|"
    r"setup\s+box|orifice\s+plate|needle\s+array)\b"
)
_MATERIAL_FORM_RE = re.compile(
    r"(?i)\b(?:nano)?fib(?:er|re)|filament|yarn|fabric|mat|film|membrane|"
    r"composite|aerogel|hydrogel|foam|powder|solution|precursor|coating\b"
)
_CHARACTERIZATION_PREP_RE = re.compile(
    r"(?i)\b(?:SEM|TEM)\s+(?:sample|specimen)|(?:sample|specimen).{0,30}\b(?:SEM|TEM)\b|"
    r"sputter[- ]?coat(?:ed|ing)?.{0,80}(?:image|SEM|TEM)|"
    r"(?:Pt|platinum)\s*/?\s*(?:Pd|palladium).{0,60}(?:SEM|image quality)"
)
_CATALOG_MEASUREMENT_LABEL_RE = re.compile(
    r"(?i)\b(?:strength|modulus|energy|speed|rate|force|length|width|"
    r"thickness|temperature|pressure|frequency|duration|displacement|"
    r"deformation)\b.{0,40}\bin\s+"
    r"(?:[kmg]?pa|[cmkµμn]?n|[µμnmck]?m(?:/[a-z]+)?|%|j|kj/m\^?2)\s*$"
)
_TABLE_AXIS_PSEUDO_SAMPLE_RE = re.compile(
    r"(?i)^(?:warp|weft|longitudinal|transverse|sd|std\.?|standard deviation)$"
)


def _catalog_aliases(sample: dict) -> list[str]:
    value = sample.get("aliases") or sample.get("sample_aliases") or []
    if isinstance(value, list):
        return [normalize_sample_id(alias) for alias in value if alias]
    text = str(value).strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [normalize_sample_id(alias) for alias in parsed if alias]
        except json.JSONDecodeError:
            pass
    return [normalize_sample_id(alias) for alias in text.split(";") if alias.strip()]


def _catalog_sample_text(sample: dict) -> str:
    return " ".join(
        str(value or "")
        for value in (
            sample.get("sample_id"),
            " ".join(_catalog_aliases(sample)),
            sample.get("material_system"),
            sample.get("composition"),
            sample.get("composition_expression"),
            sample.get("fiber_type"),
            sample.get("variable_name"),
            sample.get("variable_value"),
            sample.get("evidence_text"),
        )
    )


def _material_prefix(sample: dict) -> str:
    material = str(sample.get("material_system") or "").strip()
    if material:
        material = re.split(r"[/,+;]", material, maxsplit=1)[0].strip()
    if not material:
        text = _catalog_sample_text(sample)
        match = re.search(r"(?i)\b([A-Z][A-Z0-9-]{1,12})\b(?=.{0,30}\bnanofib)", text)
        material = match.group(1) if match else "material"
    return re.sub(r"[^A-Za-z0-9]+", "_", material).strip("_") or "material"


def _needle_count_from_text(value: Any) -> int | None:
    text = re.sub(r"[_/]+", " ", str(value or ""))
    match = re.search(r"(?i)\b(\d+)\s*[- ]?needles?\b", text)
    return int(match.group(1)) if match else None


def _sample_needle_count(sample: dict) -> int | None:
    variable_metric = find_process_parameter_canonical(
        str(sample.get("variable_name") or "")
    )
    if variable_metric == "number_of_needles":
        value = _primary_numeric_cell_value(str(sample.get("variable_value") or ""))
        if value:
            return int(float(value))
    return _needle_count_from_text(sample.get("sample_id"))


def _catalog_id_is_measurement_label(sample_id: str) -> bool:
    normalized = re.sub(r"[_-]+", " ", str(sample_id or "")).strip()
    if not normalized:
        return False
    if _TABLE_AXIS_PSEUDO_SAMPLE_RE.fullmatch(normalized):
        return True
    if find_metric_canonical(normalized) or find_process_parameter_canonical(normalized):
        return True
    return bool(_CATALOG_MEASUREMENT_LABEL_RE.search(normalized))


def sanitize_catalog_samples(
    samples: list[dict],
    *,
    source_text: str = "",
) -> list[dict]:
    """Remove apparatus/characterization pseudo-samples and normalize setup variants."""
    from app.services.extractor_v7.sample_id_rules import sanitize_sample_id

    cleaned: list[dict] = []
    by_id: dict[str, dict] = {}
    for raw in samples:
        if not isinstance(raw, dict):
            continue
        sample = dict(raw)
        sid = normalize_sample_id(sample.get("sample_id") or "")
        sid_words = re.sub(r"[_/-]+", " ", sid)
        aliases = _catalog_aliases(sample)
        text = _catalog_sample_text(sample)
        original_sid = sid
        sid, _, _ = sanitize_sample_id(sid, text)
        if not sid:
            continue
        if normalize_for_match(original_sid) != normalize_for_match(sid):
            aliases.append(original_sid)
        sid_words = re.sub(r"[_/-]+", " ", sid)

        if _catalog_id_is_measurement_label(sid):
            continue

        needle_count = re.search(r"(?i)\b(\d+)\s*[- ]?needles?\b", text)
        explicit_needle_count = _sample_needle_count(sample)
        setup_variant_id = bool(
            re.fullmatch(r"(?i)(?:box|setup|case)\s*[-#]?\s*\d+", sid)
            or (
                explicit_needle_count is not None
                and re.search(r"(?i)\b(?:box|setup|case)\s*[-#]?\s*\d+\b", sid_words)
                and _MATERIAL_FORM_RE.search(sid_words)
            )
        )
        if setup_variant_id and (explicit_needle_count is not None or needle_count):
            count = str(
                explicit_needle_count
                if explicit_needle_count is not None
                else needle_count.group(1)
            )
            fiber_type = str(sample.get("fiber_type") or "").strip().lower()
            if not fiber_type or fiber_type == "bulk":
                fiber_type = "nanofiber" if re.search(r"(?i)\bnanofib", text) else "fiber"
            old_sid = sid
            sid = normalize_sample_id(
                f"{_material_prefix(sample)}_{fiber_type}_{count}_needles"
            )
            sid_words = re.sub(r"[_/-]+", " ", sid)
            aliases.extend([old_sid, f"{count} needles"])
            if find_process_parameter_canonical(
                str(sample.get("variable_name") or "")
            ) != "number_of_needles":
                sample["variable_name"] = "number of needles"
                sample["variable_value"] = count
                sample["variable_unit"] = ""

        if not is_material_sample_id(sid):
            continue
        if _CHARACTERIZATION_PREP_RE.search(text) and re.search(
            r"(?i)\b(?:SEM|TEM|sputter|PtPd|Pt\s*Pd)\b", sid_words
        ):
            continue
        if _APPARATUS_ID_RE.search(sid_words) and not _MATERIAL_FORM_RE.search(sid_words):
            continue
        if re.search(r"(?i)\b(?:reservoir|collector|pump|apparatus)\b", sid_words) and re.search(
            r"(?i)\b(?:polypropylene|stainless steel|equipment|setup)\b", text
        ):
            continue

        sample["sample_id"] = sid
        explicit_needle_count = _sample_needle_count(sample)
        if explicit_needle_count is not None:
            aliases = [
                alias for alias in aliases
                if (
                    _needle_count_from_text(alias) is None
                    or _needle_count_from_text(alias) == explicit_needle_count
                )
            ]
        sample["aliases"] = sorted({
            alias for alias in aliases
            if alias and normalize_for_match(alias) != normalize_for_match(sid)
        })
        key = normalize_for_match(sid)
        existing = by_id.get(key)
        if existing is None:
            by_id[key] = sample
            cleaned.append(sample)
            continue
        merged_aliases = sorted(set(_catalog_aliases(existing)) | set(sample["aliases"]))
        existing["aliases"] = merged_aliases
        for field_name in (
            "material_system", "composition", "fiber_type", "variable_name",
            "variable_value", "variable_unit",
        ):
            if not existing.get(field_name) and sample.get(field_name):
                existing[field_name] = sample[field_name]
    if not source_text:
        return cleaned

    # Models sometimes materialize every integer in a stated continuous range.
    # Keep only values that are also named outside the range expression.
    range_pattern = re.compile(
        r"(?i)(?P<start>\d+(?:\.\d+)?)\s*(?P<unit>%|wt\s*%|vol\s*%)\s*"
        r"(?:to|[-–—])\s*(?P<end>\d+(?:\.\d+)?)\s*(?P=unit)"
    )
    ranges = list(range_pattern.finditer(source_text))
    if not ranges:
        return cleaned
    source_without_ranges = range_pattern.sub(" ", source_text)
    groups: dict[tuple[str, str, str], list[tuple[dict, float, str]]] = {}
    for sample in cleaned:
        value_text = str(sample.get("variable_value") or "").strip()
        value_match = re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value_text)
        if not value_match:
            continue
        key = (
            normalize_for_match(sample.get("material_system") or ""),
            normalize_for_match(sample.get("variable_name") or ""),
            normalize_for_match(sample.get("variable_unit") or ""),
        )
        if not key[0] or not key[1]:
            continue
        groups.setdefault(key, []).append((sample, float(value_text), value_text))

    drop_ids: set[int] = set()
    for (_material, _variable, unit), members in groups.items():
        if len(members) < 6:
            continue
        percent_unit = unit in {"%", "wt%", "vol%", "wt %", "vol %"}
        if not percent_unit:
            continue
        applicable_ranges = [
            match for match in ranges
            if min(float(match.group("start")), float(match.group("end")))
            <= members[0][1] <= max(float(match.group("start")), float(match.group("end")))
            or any(
                min(float(match.group("start")), float(match.group("end"))) <= value
                <= max(float(match.group("start")), float(match.group("end")))
                for _, value, _ in members
            )
        ]
        if not applicable_ranges:
            continue
        for sample, value, value_text in members:
            in_range = any(
                min(float(match.group("start")), float(match.group("end"))) <= value
                <= max(float(match.group("start")), float(match.group("end")))
                for match in applicable_ranges
            )
            if not in_range:
                continue
            explicit = re.search(
                rf"(?<![\d.]){re.escape(value_text)}(?![\d.])\s*"
                r"(?:%|wt\s*%|vol\s*%)",
                source_without_ranges,
                re.IGNORECASE,
            )
            if not explicit:
                drop_ids.add(id(sample))
    return [sample for sample in cleaned if id(sample) not in drop_ids]


def catalog_to_mentions(samples: list[dict]) -> list[dict]:
    mentions: list[dict] = []
    for sample in samples:
        sid = normalize_sample_id(sample.get("sample_id") or "")
        if not is_material_sample_id(sid):
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
    known_sample_ids: list[str] | None = None,
) -> list[dict]:
    facts: list[dict] = []
    counter = start_index
    for row in performances:
        if not isinstance(row, dict):
            continue
        sid = normalize_sample_id(row.get("sample_id") or "")
        known_ids = list(dict.fromkeys(
            normalize_sample_id(value)
            for value in (known_sample_ids or [])
            if is_material_sample_id(value)
        ))
        if not is_material_sample_id(sid):
            if len(known_ids) == 1:
                sid = known_ids[0]
            else:
                continue
        metric = (row.get("performance_metric") or "").strip()
        value = str(row.get("performance_value") or "").strip()
        if not sid or not metric or not value:
            continue
        metric = find_metric_canonical(metric) or metric
        fact_id = f"H{counter:04d}"
        counter += 1
        category = row.get("performance_category") or find_category_for_metric(metric)
        source_page = row.get("source_page")
        try:
            source_page = int(source_page) if source_page not in (None, "") else None
        except (TypeError, ValueError):
            source_page = None
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
            "_source_block_id": str(row.get("source_block_id") or "").strip() or None,
            "_source_page": source_page,
        })
    return facts


def _table_row_map(table_text: str) -> tuple[str, dict[int, str]]:
    context_lines: list[str] = []
    rows: dict[int, str] = {}
    for line in (table_text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = re.match(r"^\[row\s+(\d+)\]\s*", stripped, flags=re.I)
        if match:
            rows[int(match.group(1))] = stripped
            continue
        if not rows:
            context_lines.append(stripped)
    return "\n".join(context_lines), rows


def _table_row_shards(
    table_header: str,
    source_rows: dict[int, str],
    *,
    row_numbers: list[int] | None = None,
    max_rows: int = 6,
    max_chars: int = 4500,
) -> list[str]:
    """Build compact row shards while preserving original MinerU row numbers."""
    selected_numbers = (
        sorted(source_rows)
        if row_numbers is None
        else sorted({row for row in row_numbers if row in source_rows})
    )
    if not selected_numbers:
        return []
    max_rows = max(1, int(max_rows or 1))
    max_chars = max(500, int(max_chars or 500))
    shards: list[str] = []
    current: list[int] = []

    def render(numbers: list[int]) -> str:
        parts = [table_header.strip()] if table_header.strip() else []
        parts.extend(source_rows[row] for row in numbers)
        return "\n".join(parts)

    for row_number in selected_numbers:
        candidate = [*current, row_number]
        if current and (
            len(candidate) > max_rows
            or len(render(candidate)) > max_chars
        ):
            shards.append(render(current))
            current = []
        current.append(row_number)
    if current:
        shards.append(render(current))
    return shards


def _table_value_looks_numeric(value: Any) -> bool:
    """Require a numeric expression, not a material name containing digits."""
    target = str(value or "").strip().replace(",", "")
    if not target or not re.search(r"\d", target) or ":" in target:
        return False
    residue = re.sub(
        r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?",
        "",
        target,
    )
    residue = re.sub(r"[\s<>≤≥~≈+\-–—−±().,%×xX/]+", "", residue)
    return not residue


def _table_value_is_grounded(value: Any, row_text: str) -> bool:
    target = str(value or "").strip().replace(",", "")
    if not _table_value_looks_numeric(target):
        return False
    try:
        target_number = float(target)
    except ValueError:
        return target.lower() in row_text.lower()
    for raw in re.findall(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", row_text.replace(",", "")):
        try:
            if float(raw) == target_number:
                return True
        except ValueError:
            continue
    return False


_TABLE_NON_RESULT_HEADER_RE = re.compile(
    r"(?i)\b(?:sample|specimen|run|cycle)\s*(?:no\.?|number|id)?\b|"
    r"\b(?:time|duration|temp(?:erature)?|solid\s+to\s+liquid\s+ratio|"
    r"catalyst|concentration|pressure|humidity|frequency|wavelength|reagent|"
    r"mixing\s+ratio|dry\s+weight|"
    r"(?:fiber|fibre|filler|reinforcement|matrix|additive|resin)\s+"
    r"(?:content|loading|fraction|ratio|amount|dosage))\b"
)
_TABLE_METRIC_STOP_WORDS = {
    "a", "an", "at", "by", "fiber", "fibers", "for", "from", "in", "of",
    "per", "the", "to", "value",
}


def _table_cells(line: str) -> list[str]:
    content = re.sub(
        r"^\[(?:columns|row\s+\d+)\]",
        "",
        line.strip(" \r\n"),
        count=1,
        flags=re.I,
    )
    if content.startswith("\t"):
        content = content[1:]
    return [cell.strip() for cell in content.split("\t")]


_PROCESS_TABLE_CAPTION_RE = re.compile(
    r"(?i)\b(?:electrospinning|spinning|processing|process|fabrication|preparation|"
    r"experimental|operating)\s+(?:parameters?|conditions?|settings?)\b|"
    r"\b(?:parameters?|conditions?)\s+(?:used|required|selected|for)\b"
)
_CONTEXT_ONLY_TABLE_CAPTION_RE = re.compile(
    r"(?i)\b(?:test|testing|instrument|equipment)\s+"
    r"(?:parameters?|conditions?|settings?)\b|"
    r"\bnorms?\s+and\s+(?:specimen\s+)?dimensions?\b|"
    r"\bproperties\s+of\s+(?:the\s+)?(?:liquids?|solvents?|reagents?)\s+"
    r"(?:for|used\s+in)\s+(?:the\s+)?(?:experiment|test)"
)
_EMPTY_TABLE_CELL_RE = re.compile(r"^(?:[-–—]+|n/?a|none|not reported|[�]+)$", re.I)
_TABLE_IDENTITY_HEADERS = frozenset({
    "fabric",
    "fabric type",
    "material",
    "material type",
    "polymer",
    "polymer type",
    "solution",
    "solution type",
    "yarn",
    "yarn type",
})


def _table_header_is_non_result(column: str) -> bool:
    """Return True for table columns that identify inputs or test conditions."""
    if not column or _TABLE_NON_RESULT_HEADER_RE.search(column):
        return True
    base = _label_without_unit(column)
    normalized = re.sub(r"[\s_-]+", " ", base.lower()).strip()
    return normalized in _TABLE_IDENTITY_HEADERS


def _table_columns_line(header_text: str) -> str:
    return next(
        (
            line.strip()
            for line in (header_text or "").splitlines()
            if line.strip().startswith("[columns]")
        ),
        "",
    )


def _table_caption_text(header_text: str) -> str:
    return " ".join(
        line.strip()
        for line in (header_text or "").splitlines()
        if line.strip() and not line.strip().startswith("[columns]")
    )


def _label_without_unit(label: str) -> str:
    return re.sub(r"\s*(?:\([^()]*\)|\[[^\[\]]*\])\s*$", "", label or "").strip()


def _process_metric_for_label(label: str) -> str | None:
    base = _label_without_unit(label)
    return find_process_parameter_canonical(base) or find_process_parameter_canonical(label)


def _unit_from_table_label(label: str) -> str:
    matches = re.findall(r"(?:\(([^()]*)\)|\[([^\[\]]*)\])", label or "")
    raw = next((left or right for left, right in reversed(matches) if (left or right)), "")
    if not raw:
        suffix = re.search(r"(?i)\bin\s+(.+?)\s*$", label or "")
        raw = suffix.group(1) if suffix else ""
    raw = re.sub(r"\s*\$?\^\{?2\}?\$?", "²", raw)
    raw = raw.replace("$", "").strip()
    compact = re.sub(r"\s+", "", raw).lower()
    aliases = {
        "ml/hr": "mL/h",
        "ml/h": "mL/h",
        "mlmin-1": "mL/min",
        "kv/cm": "kV/cm",
        "kv/mm": "kV/mm",
        "kv": "kV",
        "wt.%": "wt%",
        "wt%": "wt%",
    }
    return aliases.get(compact, raw.strip())


def _primary_numeric_cell_value(cell: str) -> str:
    value = (cell or "").strip()
    if not value or _EMPTY_TABLE_CELL_RE.fullmatch(value):
        return ""
    match = re.search(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", value.replace(",", ""))
    return match.group(0) if match else ""


def classify_table_role(table_text: str) -> str:
    """Classify a structured MinerU table before spending an LLM call on it."""
    header, source_rows = _table_row_map(table_text)
    if _CONTEXT_ONLY_TABLE_CAPTION_RE.search(_table_caption_text(header)):
        return "context"
    columns_line = _table_columns_line(header)
    columns = _table_cells(columns_line) if columns_line else []
    labels = list(columns)
    for row_text in source_rows.values():
        cells = _table_cells(row_text)
        if cells:
            labels.append(cells[0])

    process_count = 0
    result_count = 0
    for label in labels:
        if not label:
            continue
        if not re.search(r"[A-Za-z]", label):
            continue
        if _process_metric_for_label(label):
            process_count += 1
            continue
        if find_metric_canonical(_label_without_unit(label)) or find_metric_canonical(label):
            result_count += 1

    explicit_process = bool(_PROCESS_TABLE_CAPTION_RE.search(_table_caption_text(header)))
    if process_count and result_count:
        return "mixed"
    if (explicit_process and process_count) or (process_count >= 2 and not result_count):
        return "process"
    if result_count:
        return "performance"
    return "unknown"


def _variant_matches_text(metric: str, value: str, text: str) -> bool:
    words = re.sub(r"[_/-]+", " ", text or "")
    if metric == "number_of_needles":
        try:
            count = int(float(value))
        except ValueError:
            return False
        if re.search(rf"(?i)\b{count}\s*[- ]?needles?\b", words):
            return True
        return count == 1 and bool(re.search(r"(?i)\bsingle[- ]needle\b", words))
    descriptor = f"{metric.replace('_', ' ')} {value}"
    return normalize_for_match(descriptor) in normalize_for_match(words)


def _resolve_process_table_sample(
    samples: list[dict],
    metric: str,
    value: str,
) -> str:
    scored: list[tuple[int, str]] = []
    for sample in samples:
        sid = normalize_sample_id(sample.get("sample_id") or "")
        if not sid:
            continue
        if metric == "number_of_needles":
            explicit_count = _sample_needle_count(sample)
            try:
                target_count = int(float(value))
            except ValueError:
                target_count = None
            if (
                explicit_count is not None
                and target_count is not None
                and explicit_count != target_count
            ):
                continue
        score = 0
        if _variant_matches_text(metric, value, sid):
            score += 10
        if any(_variant_matches_text(metric, value, alias) for alias in _catalog_aliases(sample)):
            score += 8
        if _variant_matches_text(metric, value, str(sample.get("composition") or "")):
            score += 5
        variable_metric = find_process_parameter_canonical(str(sample.get("variable_name") or ""))
        variable_value = str(sample.get("variable_value") or "").strip()
        if variable_metric == metric and variable_value == value:
            score += 8
        if score:
            scored.append((score, sid))
    if not scored:
        return ""
    best_score = max(score for score, _ in scored)
    best = {sid for score, sid in scored if score == best_score}
    return next(iter(best)) if len(best) == 1 else ""


def _transposed_process_axis(table_text: str) -> tuple[str, list[str]] | None:
    header, _ = _table_row_map(table_text)
    columns_line = _table_columns_line(header)
    columns = _table_cells(columns_line) if columns_line else []
    if len(columns) < 3:
        return None
    metric = _process_metric_for_label(columns[0])
    values = [_primary_numeric_cell_value(cell) for cell in columns[1:]]
    if not metric or sum(bool(value) for value in values) < 2:
        return None
    return metric, values


def _catalog_material_identity(samples: list[dict]) -> tuple[str, str]:
    materials: Counter[str] = Counter()
    forms: Counter[str] = Counter()
    for sample in samples:
        text = _catalog_sample_text(sample)
        if not _MATERIAL_FORM_RE.search(text):
            continue
        material = _material_prefix(sample)
        if material.lower() not in {"material", "pp", "pt", "pd"}:
            materials[material] += 1
        form = str(sample.get("fiber_type") or "").strip().lower()
        if not form or form == "bulk":
            form = "nanofiber" if re.search(r"(?i)\bnanofib", text) else "fiber"
        forms[re.sub(r"[^a-z0-9]+", "_", form).strip("_")] += 1
    material = materials.most_common(1)[0][0] if materials else "material"
    form = forms.most_common(1)[0][0] if forms else "fiber"
    return material, form


def augment_catalog_samples_from_process_tables(
    chunks: list[dict],
    samples: list[dict],
) -> list[dict]:
    """Add only missing material variants represented by transposed process tables."""
    augmented = list(samples)
    material, form = _catalog_material_identity(augmented)
    for chunk in chunks:
        table_text = str(chunk.get("raw_text") or "")
        if chunk.get("source_type") != "table_text" or classify_table_role(table_text) != "process":
            continue
        axis = _transposed_process_axis(table_text)
        if not axis:
            continue
        metric, values = axis
        if metric != "number_of_needles":
            continue
        for value in values:
            if not value or _resolve_process_table_sample(augmented, metric, value):
                continue
            count = int(float(value))
            suffix = "single_needle" if count == 1 else f"{count}_needles"
            sid = normalize_sample_id(f"{material}_{form}_{suffix}")
            aliases = [f"{count} needle" if count == 1 else f"{count} needles"]
            if count == 1:
                aliases.append("single needle")
            augmented.append({
                "sample_id": sid,
                "aliases": aliases,
                "material_system": material,
                "composition": f"{material} {form} produced with a {count}-needle setup.",
                "fiber_type": form,
                "variable_name": "number of needles",
                "variable_value": value,
                "variable_unit": "",
                "source_location": chunk.get("source_location") or _chunk_header(chunk),
                "_table_derived": True,
            })
    return augmented


def process_table_to_facts(
    *,
    table_text: str,
    known_samples: list[dict],
    source_location: str,
    source_block_id: str | None = None,
    source_page: int | None = None,
    source_bbox: Any = None,
) -> list[dict]:
    """Deterministically extract numeric process settings from a process-only table."""
    if classify_table_role(table_text) != "process":
        return []
    header, source_rows = _table_row_map(table_text)
    columns_line = _table_columns_line(header)
    columns = _table_cells(columns_line) if columns_line else []
    axis = _transposed_process_axis(table_text)
    facts: list[dict] = []
    resolved_samples: list[str] = []

    def add_fact(
        *,
        sample_id: str,
        metric: str,
        value: str,
        unit: str,
        evidence: str,
        condition: str,
        row_number: int | None,
        column_index: int | None,
        column_name: str,
    ) -> None:
        if not metric or not value:
            return
        facts.append({
            "fact_id": f"P{len(facts) + 1:04d}",
            "fact_type": "process",
            "subject_text": metric,
            "candidate_sample_ids": [sample_id] if sample_id else [],
            "metric_or_parameter": metric,
            "value": value,
            "unit": unit,
            "method": "",
            "condition": condition,
            "category": "process",
            "evidence_text": evidence,
            "source_location": source_location,
            "extraction_method": "rule_table_process",
            "confidence": 0.99,
            "assigned_sample_id": sample_id or None,
            "assignment_status": "assigned" if sample_id else "unassigned",
            "assignment_confidence": 0.99 if sample_id else 0.0,
            "assignment_reason": "deterministic_process_table",
            "_data_source_type": "experimental_condition",
            "_source_block_id": source_block_id,
            "_source_page": source_page,
            "_source_bbox": source_bbox,
            "_source_table_row": row_number,
            "_source_table_column": column_index,
            "_source_table_column_name": column_name,
        })

    if axis:
        axis_metric, axis_values = axis
        axis_unit = _unit_from_table_label(columns[0])
        for offset, axis_value in enumerate(axis_values, start=1):
            if not axis_value:
                continue
            sample_id = _resolve_process_table_sample(known_samples, axis_metric, axis_value)
            if sample_id:
                resolved_samples.append(sample_id)
            axis_condition = f"{axis_metric}={axis_value}"
            add_fact(
                sample_id=sample_id,
                metric=axis_metric,
                value=axis_value,
                unit=axis_unit,
                evidence="\n".join(part for part in (_table_caption_text(header), columns_line) if part),
                condition=axis_condition,
                row_number=0,
                column_index=offset,
                column_name=columns[0],
            )
            for row_number, row_text in source_rows.items():
                cells = _table_cells(row_text)
                if not cells or offset >= len(cells):
                    continue
                metric = _process_metric_for_label(cells[0])
                value = _primary_numeric_cell_value(cells[offset])
                if not metric or not value:
                    continue
                add_fact(
                    sample_id=sample_id,
                    metric=metric,
                    value=value,
                    unit=_unit_from_table_label(cells[0]),
                    evidence="\n".join(
                        part for part in (_table_caption_text(header), columns_line, row_text) if part
                    ),
                    condition=axis_condition,
                    row_number=row_number,
                    column_index=offset,
                    column_name=cells[0],
                )

    caption = _table_caption_text(header)
    global_patterns = (
        (
            "tip_to_collector_distance",
            re.compile(
                r"(?i)(?:distance\s+from\s+(?:the\s+)?needle\s+to\s+(?:the\s+)?collector|"
                r"needle[- ]to[- ]collector\s+distance)\s*(?:=|is|of)?\s*"
                r"([+-]?\d+(?:\.\d+)?)\s*(mm|cm)"
            ),
        ),
        (
            "polymer_concentration",
            re.compile(
                r"(?i)(?:solution|polymer)\s+concentration\s*(?:=|is|of)?\s*"
                r"([+-]?\d+(?:\.\d+)?)\s*(wt\.?\s*%|w/v\s*%)"
            ),
        ),
    )
    for metric, pattern in global_patterns:
        match = pattern.search(caption)
        if not match:
            continue
        value = match.group(1)
        unit = re.sub(r"\s+|\.", "", match.group(2)).replace("WT", "wt")
        for sample_id in dict.fromkeys(resolved_samples):
            add_fact(
                sample_id=sample_id,
                metric=metric,
                value=value,
                unit=unit,
                evidence=caption,
                condition="all table configurations",
                row_number=None,
                column_index=None,
                column_name=metric,
            )

    unique: dict[tuple[str, str, str, str], dict] = {}
    for fact in facts:
        key = (
            normalize_for_match(fact.get("assigned_sample_id") or ""),
            str(fact.get("metric_or_parameter") or ""),
            str(fact.get("value") or ""),
            str(fact.get("unit") or ""),
        )
        unique.setdefault(key, fact)
    return list(unique.values())


def _table_value_matches_cell(value: Any, cell: str) -> bool:
    target = str(value or "").strip().replace(",", "")
    candidate = (cell or "").strip().replace(",", "")
    if not _table_value_looks_numeric(target) or not candidate:
        return False
    if target.lower() == candidate.lower():
        return True
    try:
        target_number = float(target)
    except ValueError:
        return False
    primary = re.match(
        r"^\s*[<>~≈]?\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)",
        candidate,
    )
    return bool(primary and float(primary.group(1)) == target_number)


def _table_metric_tokens(value: str) -> set[str]:
    base = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", value or "")
    tokens = set(re.findall(r"[a-z0-9]+", base.lower().replace("_", " ")))
    return {token for token in tokens if token not in _TABLE_METRIC_STOP_WORDS}


def _table_metric_matches_column(metric: str, column: str) -> bool:
    if _table_header_is_non_result(column):
        return False
    column_base = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", column).strip()
    metric_canonical = _table_metric_canonical(metric, column)
    column_canonical = _table_metric_canonical(column_base or column, column)
    if metric_canonical or column_canonical:
        return bool(
            metric_canonical
            and column_canonical
            and metric_canonical == column_canonical
        )
    metric_tokens = _table_metric_tokens(metric)
    column_tokens = _table_metric_tokens(column_base)
    return bool(
        metric_tokens
        and column_tokens
        and (
            metric_tokens.issubset(column_tokens)
            or column_tokens.issubset(metric_tokens)
        )
    )


def _table_metric_canonical(label: str, full_column: str = "") -> str | None:
    """Resolve compact mechanical table symbols using their unit-bearing header."""
    base = _label_without_unit(label)
    compact = re.sub(r"[^A-Za-z0-9]", "", base).lower()
    unit = _unit_from_table_label(full_column or label).lower()
    if compact == "uts" and unit in {"mpa", "gpa", "kpa", "pa"}:
        return "tensile_strength"
    if compact == "e" and unit in {"mpa", "gpa", "kpa", "pa"}:
        return "Youngs_modulus"
    normalized = canonicalize_metric_name(base or label)
    return find_metric_canonical(normalized)


def _table_metric_value_column_index(
    metric: str,
    value: Any,
    header_text: str,
    row_text: str,
) -> int | None:
    columns_line = next(
        (
            line.strip()
            for line in (header_text or "").splitlines()
            if line.strip().startswith("[columns]")
        ),
        "",
    )
    if not columns_line:
        return None
    columns = _table_cells(columns_line)
    cells = _table_cells(row_text)
    for index, cell in enumerate(cells):
        if index >= len(columns) or not _table_value_matches_cell(value, cell):
            continue
        if _table_metric_matches_column(metric, columns[index]):
            return index
    return None


def _table_metric_value_is_grounded(
    metric: str,
    value: Any,
    header_text: str,
    row_text: str,
) -> bool:
    return _table_metric_value_column_index(metric, value, header_text, row_text) is not None


def _table_cell_has_numeric_result(cell: str) -> bool:
    value = (cell or "").strip()
    if not value or value.lower() in {"-", "--", "n/a", "na", "none", "not reported"}:
        return False
    return bool(re.search(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", value))


def _table_expected_result_cells(
    header_text: str,
    source_rows: dict[int, str],
) -> dict[tuple[int, int], tuple[str, str]]:
    """Return numeric cells whose columns describe measured results."""
    columns_line = next(
        (
            line.strip()
            for line in (header_text or "").splitlines()
            if line.strip().startswith("[columns]")
        ),
        "",
    )
    if not columns_line:
        return {}
    columns = _table_cells(columns_line)
    expected: dict[tuple[int, int], tuple[str, str]] = {}
    for row_number, row_text in source_rows.items():
        cells = _table_cells(row_text)
        for column_index, column_name in enumerate(columns):
            if (
                not column_name
                or _table_header_is_non_result(column_name)
                or column_index >= len(cells)
                or not _table_cell_has_numeric_result(cells[column_index])
            ):
                continue
            expected[(row_number, column_index)] = (
                column_name,
                cells[column_index],
            )
    return expected


_GENERIC_NUMBERED_SAMPLE_RE = re.compile(
    r"(?i)^(?:sample|specimen|run|no\.?)\s*[-#:]?\s*\d+(?:\.\d+)?$"
)


def _table_caption_material(header_text: str) -> str:
    caption = " ".join(
        line.strip()
        for line in (header_text or "").splitlines()
        if line.strip() and not line.strip().startswith("[columns]")
    )
    for candidate in reversed(re.findall(r"\(([^()]+)\)", caption)):
        value = normalize_sample_id(candidate)
        if (
            value
            and re.search(r"[A-Za-z]", value)
            and not re.search(r"(?i)\b(?:g/g|mg/g|mpa|gpa|kpa|wt%|vol%)\b", value)
            and not re.search(r"\d", value)
        ):
            return value
    return ""


def _table_row_sample_id(
    header_text: str,
    row_text: str,
    known_sample_ids: list[str] | None = None,
    proposed_sample_id: str = "",
) -> str:
    """Resolve an explicit sample-column value against the known catalog."""
    columns_line = next(
        (
            line.strip()
            for line in (header_text or "").splitlines()
            if line.strip().startswith("[columns]")
        ),
        "",
    )
    if not columns_line:
        return ""
    columns = _table_cells(columns_line)
    cells = _table_cells(row_text)
    sample_index = next(
        (
            index
            for index, column in enumerate(columns)
            if re.search(r"(?i)\b(?:sample|specimen)\b", column)
        ),
        None,
    )
    if sample_index is None or sample_index >= len(cells):
        return ""
    source_id = normalize_sample_id(cells[sample_index])
    if not source_id:
        return ""

    known_ids = list(dict.fromkeys(
        normalize_sample_id(value)
        for value in (known_sample_ids or [])
        if is_material_sample_id(value)
    ))
    unnumbered_known_ids = [
        value for value in known_ids
        if not re.search(
            r"(?i)(?:\b(?:sample|specimen|run|no\.?)\s*[-#:]?\s*|"
            r"[\s_/]+(?:s(?:ample)?)?)\d+(?:\.\d+)?\s*$",
            value,
        )
    ]
    proposed_id = normalize_sample_id(proposed_sample_id)
    proposed_norm = normalize_for_match(proposed_id)

    def _proposed_known_base() -> str:
        matches = [
            known_id for known_id in known_ids
            if normalize_for_match(known_id) in proposed_norm
            or proposed_norm in normalize_for_match(known_id)
        ]
        return matches[0] if len(set(matches)) == 1 else ""

    if re.search(r"(?i)\b(?:mean|avg|average|median)\b", source_id):
        proposed_base = _proposed_known_base()
        if proposed_base:
            return proposed_base
        if len(unnumbered_known_ids) == 1:
            return unnumbered_known_ids[0]
        return _table_caption_material(header_text) or "aggregate"

    try:
        source_number = float(source_id)
    except ValueError:
        source_number = None
    if source_number is not None:
        matches: list[str] = []
        for normalized in known_ids:
            match = re.search(
                r"(?i)(?:\b(?:sample|specimen|run|no\.?)\s*[-#:]?\s*|"
                r"[\s_/]+(?:s(?:ample)?)?)"
                r"(\d+(?:\.\d+)?)\s*$",
                normalized,
            )
            if match and float(match.group(1)) == source_number:
                matches.append(normalized)
        if len(set(matches)) == 1:
            return matches[0]
        proposed_base = _proposed_known_base()
        if proposed_base:
            if re.search(
                rf"(?i)(?:\b(?:sample|specimen|run|no\.?)\s*[-#:]?\s*|"
                rf"[\s_/]+(?:s(?:ample)?)?){re.escape(source_id)}\s*$",
                proposed_id,
            ):
                return proposed_id
            if re.search(r"(?i)\b(?:sample|specimen|run)\s*$", proposed_base):
                return f"{proposed_base} {source_id}"
            return f"{proposed_base} specimen {source_id}"
        if len(unnumbered_known_ids) == 1:
            base_id = unnumbered_known_ids[0]
            if re.search(r"(?i)\b(?:sample|specimen|run)\s*$", base_id):
                return f"{base_id} {source_id}"
            return f"{base_id} specimen {source_id}"
        return f"sample {source_id}"
    return source_id


def _nearby_table_context(
    chunks: list[dict],
    table_chunk: dict,
    *,
    max_paragraphs: int = 2,
    max_chars: int = 1800,
) -> str:
    """Return the nearest same-page narrative blocks that ground table identity."""
    try:
        table_index = next(
            index for index, chunk in enumerate(chunks) if chunk is table_chunk
        )
    except StopIteration:
        return ""
    page = table_chunk.get("page_number")
    excluded_types = {"equation", "header_footer", "page_number", "ref_text", "table"}
    candidates: list[tuple[int, int, str]] = []
    for index, chunk in enumerate(chunks):
        if index == table_index or chunk.get("page_number") != page:
            continue
        if chunk.get("source_type") != "text":
            continue
        if str(chunk.get("block_type") or "").lower() in excluded_types:
            continue
        text = str(chunk.get("raw_text") or "").strip()
        if len(text) < 80:
            continue
        candidates.append((abs(index - table_index), index, text))
    chosen = sorted(candidates)[:max_paragraphs]
    chosen.sort(key=lambda item: item[1])
    parts: list[str] = []
    used = 0
    for _, _, text in chosen:
        remaining = max_chars - used
        if remaining <= 0:
            break
        part = text[:remaining]
        parts.append(part)
        used += len(part)
    return "\n".join(parts)


def table_rows_to_facts(
    rows: list[dict],
    *,
    table_text: str,
    table_context: str = "",
    source_location: str,
    source_block_id: str | None = None,
    source_page: int | None = None,
    source_bbox: Any = None,
    known_sample_ids: list[str] | None = None,
) -> list[dict]:
    """Convert compact table output to facts only when row/value evidence is exact."""
    header, source_rows = _table_row_map(table_text)
    performances: list[dict] = []
    row_meta: list[tuple[str | None, int | None, Any, int, int, str]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        try:
            row_number = int(item.get("row"))
        except (TypeError, ValueError):
            continue
        source_row = source_rows.get(row_number, "")
        value = item.get("value")
        if not source_row or not _table_value_is_grounded(value, source_row):
            continue
        sample_id = normalize_sample_id(item.get("sample_id") or "")
        metric = str(item.get("metric") or "").strip()
        if not sample_id or not metric:
            continue
        has_sample_column = bool(
            re.search(r"(?i)^\[columns\].*\b(?:sample|specimen)\b", header, flags=re.M)
        )
        if has_sample_column:
            source_sample_id = _table_row_sample_id(
                header,
                source_row,
                known_sample_ids=known_sample_ids,
                proposed_sample_id=sample_id,
            )
            if source_sample_id:
                sample_id = source_sample_id
        elif _GENERIC_NUMBERED_SAMPLE_RE.fullmatch(sample_id):
            caption_material = _table_caption_material(header)
            if caption_material:
                sample_id = caption_material
        source_column = _table_metric_value_column_index(metric, value, header, source_row)
        if source_column is None:
            continue
        columns_line = next(
            (
                line.strip()
                for line in header.splitlines()
                if line.strip().startswith("[columns]")
            ),
            "",
        )
        columns = _table_cells(columns_line) if columns_line else []
        source_column_name = columns[source_column] if source_column < len(columns) else ""
        column_metric = _table_metric_canonical(source_column_name, source_column_name)
        if column_metric:
            metric = column_metric
        else:
            grounded_label = _label_without_unit(source_column_name)
            if grounded_label:
                metric = grounded_label
        performances.append({
            "sample_id": sample_id,
            "performance_metric": metric,
            "performance_value": str(value).strip(),
            "performance_unit": item.get("unit") or "",
            "performance_condition": item.get("condition") or "",
            "source_location": source_location,
            "evidence_text": "\n".join(
                part for part in (table_context.strip(), header, source_row) if part
            ),
        })
        row_meta.append((
            source_block_id,
            source_page,
            source_bbox,
            row_number,
            source_column,
            source_column_name,
        ))

    facts = performances_to_facts(performances)
    for fact, (block_id, page, bbox, row_number, column, column_name) in zip(facts, row_meta):
        fact["extraction_method"] = "AI_holistic_table"
        fact["assignment_reason"] = "holistic_table_row_grounded"
        fact["_source_block_id"] = block_id
        fact["_source_page"] = page
        fact["_source_bbox"] = bbox
        fact["_source_table_row"] = row_number
        fact["_source_table_column"] = column
        fact["_source_table_column_name"] = column_name
    return facts


def deterministic_performance_table_facts(
    *,
    table_text: str,
    table_context: str = "",
    source_location: str,
    source_block_id: str | None = None,
    source_page: int | None = None,
    source_bbox: Any = None,
    known_sample_ids: list[str] | None = None,
) -> list[dict]:
    """Extract unambiguous MinerU table cells without an LLM call.

    This fast path is intentionally narrow: it requires an explicit sample or
    specimen column and a dictionary-known performance metric for every emitted
    cell. Complex or unknown columns remain available to the LLM path.
    """
    header, source_rows = _table_row_map(table_text)
    columns_line = _table_columns_line(header)
    columns = _table_cells(columns_line) if columns_line else []
    if not columns or not any(
        re.search(r"(?i)\b(?:sample|specimen)\b", column)
        for column in columns
    ):
        return []

    rows: list[dict] = []
    caption = _table_caption_text(header)
    for (row_number, column_index), (column, cell) in sorted(
        _table_expected_result_cells(header, source_rows).items()
    ):
        canonical = _table_metric_canonical(column, column)
        if not canonical:
            continue
        source_row = source_rows.get(row_number, "")
        sample_id = _table_row_sample_id(
            header,
            source_row,
            known_sample_ids=known_sample_ids,
        )
        if not is_material_sample_id(sample_id):
            continue
        rows.append({
            "row": row_number,
            "sample_id": sample_id,
            "metric": canonical,
            "value": cell,
            "unit": _unit_from_table_label(column),
            "condition": caption,
            "_source_table_column": column_index,
        })

    facts = table_rows_to_facts(
        rows,
        table_text=table_text,
        table_context=table_context,
        source_location=source_location,
        source_block_id=source_block_id,
        source_page=source_page,
        source_bbox=source_bbox,
        known_sample_ids=known_sample_ids,
    )
    for fact in facts:
        fact["extraction_method"] = "rule_table_performance"
        fact["assignment_reason"] = "mineru_table_cell_grounded"
        fact["confidence"] = 0.97
    return facts


_TABLE_STATISTIC_HEADER_RE = re.compile(
    r"(?i)^(?:sd|std\.?|standard deviation|standard error|se|uncertainty)$"
)
_TABLE_DIRECTION_HEADER_RE = re.compile(
    r"(?i)^(?:warp|weft|longitudinal|transverse|axial|radial|machine|"
    r"cross[- ]machine)(?:\s+direction)?$"
)


def _transposed_table_material_base(
    header_text: str,
    table_context: str,
) -> str:
    text = f"{_table_caption_text(header_text)}\n{table_context}"
    if re.search(r"(?i)\bfib(?:er|re)[- ]reinforced plastics?\b|\bFRPs?\b", text):
        return "FRP"
    for match in re.finditer(
        r"\b(?:of|for)\s+([A-Z][A-Z0-9/-]{1,10})(?:s)?\b",
        text,
    ):
        candidate = match.group(1)
        if candidate not in {"DIN", "ISO", "SD", "SEM", "TEM"}:
            return candidate
    return _table_caption_material(header_text)


def _transposed_table_sample_id(
    column_label: str,
    *,
    material_base: str,
    known_sample_ids: list[str] | None,
) -> str:
    label = normalize_sample_id(column_label)
    if not label or _TABLE_STATISTIC_HEADER_RE.fullmatch(label):
        return ""
    for known_id in known_sample_ids or []:
        if normalize_for_match(known_id) == normalize_for_match(label):
            return normalize_sample_id(known_id)
    if material_base:
        if normalize_for_match(material_base) in normalize_for_match(label):
            return label
        return normalize_sample_id(f"{material_base}_{label}")
    if _TABLE_DIRECTION_HEADER_RE.fullmatch(label):
        return ""
    return label if is_material_sample_id(label) else ""


def deterministic_transposed_performance_table_facts(
    *,
    table_text: str,
    table_context: str = "",
    source_location: str,
    source_block_id: str | None = None,
    source_page: int | None = None,
    source_bbox: Any = None,
    known_sample_ids: list[str] | None = None,
) -> list[dict]:
    """Extract tables whose rows are metrics and columns are samples or axes."""
    header, source_rows = _table_row_map(table_text)
    columns_line = _table_columns_line(header)
    columns = _table_cells(columns_line) if columns_line else []
    if len(columns) < 3:
        return []

    metric_rows: list[tuple[int, str, str, list[str]]] = []
    for row_number, row_text in source_rows.items():
        cells = _table_cells(row_text)
        if len(cells) < 3:
            continue
        metric_label = cells[0]
        canonical = _table_metric_canonical(metric_label, metric_label)
        if not canonical:
            continue
        metric_rows.append((row_number, row_text, canonical, cells))
    if len(metric_rows) < 2:
        return []

    value_columns = [
        index
        for index, label in enumerate(columns[1:], start=1)
        if label and not _TABLE_STATISTIC_HEADER_RE.fullmatch(label)
    ]
    if not value_columns:
        return []

    material_base = _transposed_table_material_base(header, table_context)
    facts: list[dict] = []
    for row_number, row_text, metric, cells in metric_rows:
        unit = _unit_from_table_label(cells[0])
        for column_index in value_columns:
            if column_index >= len(cells):
                continue
            value = _primary_numeric_cell_value(cells[column_index])
            if not value:
                continue
            sample_id = _transposed_table_sample_id(
                columns[column_index],
                material_base=material_base,
                known_sample_ids=known_sample_ids,
            )
            if not is_material_sample_id(sample_id):
                continue
            conditions = [f"axis={columns[column_index]}"]
            sd_index = column_index + 1
            if (
                sd_index < len(columns)
                and sd_index < len(cells)
                and _TABLE_STATISTIC_HEADER_RE.fullmatch(columns[sd_index])
            ):
                standard_deviation = _primary_numeric_cell_value(cells[sd_index])
                if standard_deviation:
                    suffix = f" {unit}" if unit else ""
                    conditions.append(
                        f"standard_deviation={standard_deviation}{suffix}"
                    )
            performances = [{
                "sample_id": sample_id,
                "performance_metric": metric,
                "performance_value": value,
                "performance_unit": unit,
                "performance_condition": "; ".join(conditions),
                "source_location": source_location,
                "evidence_text": "\n".join(
                    part
                    for part in (table_context.strip(), header, row_text)
                    if part
                ),
            }]
            converted = performances_to_facts(performances)
            if not converted:
                continue
            fact = converted[0]
            fact["extraction_method"] = "rule_table_performance"
            fact["assignment_reason"] = "deterministic_transposed_performance_table"
            fact["confidence"] = 0.99
            fact["_source_block_id"] = source_block_id
            fact["_source_page"] = source_page
            fact["_source_bbox"] = source_bbox
            fact["_source_table_row"] = row_number
            fact["_source_table_column"] = column_index
            fact["_source_table_column_name"] = columns[column_index]
            facts.append(fact)
    return facts


def _canonical_fact_metric(fact: dict) -> str:
    metric = str(fact.get("metric_or_parameter") or "")
    if fact.get("fact_type") == "process":
        return find_process_parameter_canonical(metric) or metric
    return find_metric_canonical(metric) or metric


def _fact_key(fact: dict) -> tuple[str, str, str, str]:
    sid = normalize_sample_id(fact.get("assigned_sample_id") or "")
    metric = _canonical_fact_metric(fact)
    metric = re.sub(r"\s+", "_", metric.lower().strip())
    value = str(fact.get("value") or "").strip()
    condition = re.sub(
        r"\s+", " ", str(fact.get("condition") or "").lower().strip()
    )
    return sid, metric, value, condition


def _fact_rank(fact: dict) -> int:
    score = 0
    if fact.get("extraction_method") in {
        "rule_table_process", "rule_table_performance",
    }:
        score += 7
    elif fact.get("extraction_method") == "AI_holistic_table":
        score += 6
    elif fact.get("extraction_method") == "AI_holistic":
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
    metric_value_map: dict[tuple[str, str, str, str], dict] = {}

    for fact in atomic_facts + holistic_facts:
        if fact.get("fact_type") != "performance":
            non_perf.append(fact)
            continue
        sid, metric, value, condition = _fact_key(fact)
        if not metric or not value:
            continue
        key = (sid, metric, value, condition)
        current = metric_value_map.get(key)
        if current is None or _fact_rank(fact) > _fact_rank(current):
            metric_value_map[key] = fact

    return non_perf + list(metric_value_map.values())


def _condition_number_signature(value: Any) -> set[str]:
    numbers: set[str] = set()
    for raw in re.findall(r"[+-]?\d+(?:\.\d+)?", str(value or "")):
        try:
            number = float(raw)
        except ValueError:
            continue
        numbers.add(f"{number:g}")
    return numbers


def reconcile_holistic_table_duplicates(facts: list[dict]) -> list[dict]:
    """Drop narrative restatements that map unambiguously to one table row."""
    table_methods = {
        "AI_holistic_table", "rule_table_process", "rule_table_performance",
    }
    table_facts = [
        fact for fact in facts
        if fact.get("fact_type") in {"performance", "process"}
        and fact.get("extraction_method") in table_methods
    ]
    drop_ids: set[int] = set()
    for fact in facts:
        if (
            fact.get("fact_type") not in {"performance", "process"}
            or fact.get("extraction_method") in table_methods
        ):
            continue
        metric = _canonical_fact_metric(fact)
        value = str(fact.get("value") or "").strip()
        sample = normalize_for_match(fact.get("assigned_sample_id") or "")
        summary_numbers = _condition_number_signature(fact.get("condition"))
        value_matches: list[dict] = []
        exact_identity_matches: list[dict] = []
        for table_fact in table_facts:
            if table_fact.get("fact_type") != fact.get("fact_type"):
                continue
            table_metric = _canonical_fact_metric(table_fact)
            if table_metric != metric or str(table_fact.get("value") or "").strip() != value:
                continue
            table_sample = normalize_for_match(table_fact.get("assigned_sample_id") or "")
            evidence = normalize_for_match(table_fact.get("evidence_text") or "")
            if sample and sample != table_sample and sample not in evidence:
                continue
            value_matches.append(table_fact)
            if sample and sample == table_sample:
                exact_identity_matches.append(table_fact)

        if len(exact_identity_matches) == 1:
            matches = exact_identity_matches
        else:
            matches = []
            for table_fact in value_matches:
                table_numbers = _condition_number_signature(table_fact.get("condition"))
                if summary_numbers and not summary_numbers.issubset(table_numbers):
                    continue
                matches.append(table_fact)
        if len(matches) != 1:
            continue
        table_fact = matches[0]
        fact["assigned_sample_id"] = table_fact.get("assigned_sample_id")
        fact["candidate_sample_ids"] = [table_fact.get("assigned_sample_id")]
        fact["assignment_status"] = "assigned"
        fact["assignment_confidence"] = max(
            float(fact.get("assignment_confidence") or 0), 0.95
        )
        drop_ids.add(id(fact))
        table_fact["assignment_reason"] = (
            f"{table_fact.get('assignment_reason') or ''}; narrative_duplicate_confirmed"
        ).strip("; ")
    return [fact for fact in facts if id(fact) not in drop_ids]


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
    performances = _response_rows(parsed, "performances", "_items")
    return performances_to_facts(performances, known_sample_ids=sample_ids)


def enrich_sample_cards(
    cards: list[dict],
    samples: list[dict],
    background: dict[str, dict],
) -> list[dict]:
    """Apply catalog fields and only safely shared holistic background fields."""
    comp = background.get("composition") or {}
    proc = background.get("process") or {}
    struct = background.get("structure") or {}
    catalog_by_id = {
        normalize_sample_id(s.get("sample_id") or ""): s
        for s in samples
        if s.get("sample_id")
    }
    card_by_id = {c.get("sample_id"): c for c in cards if c.get("sample_id")}
    shared_background = catalog_supports_shared_background(samples)

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
        _fill(
            card,
            "composition_expression",
            sample.get("composition")
            or (comp.get("composition_expression") if shared_background else ""),
        )
        if shared_background:
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


def _catalog_form_bucket(sample: dict) -> str:
    value = normalize_for_match(sample.get("fiber_type") or "")
    if re.search(r"\b(?:nano)?fib(?:er|re)|yarn|fibrous\b", value):
        return "fiber"
    if value in {"bulk", "powder", "particle", "particles"}:
        return "bulk"
    if value in {"solution", "precursor", "dispersion"}:
        return "solution"
    return value


def catalog_supports_shared_background(samples: list[dict]) -> bool:
    """Return false when one global background would cross material forms."""
    if not samples:
        return False
    forms = {_catalog_form_bucket(sample) for sample in samples}
    forms.discard("")
    return len(forms) <= 1


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


def select_specialized_result_context(
    chunks: list[dict], *, channel: str, max_chars: int = 12000,
) -> str:
    """Build a compact technique-specific context instead of rescanning all results."""
    if channel == "spectroscopy":
        signal = re.compile(
            r"(?i)\b(?:FT-?IR|ATR-?FTIR|Raman|XPS|XRD|NMR|spectroscop|"
            r"wavenumber|absorption\s+bands?|diffraction\s+peaks?|2theta)\b|"
            r"cm\s*(?:\^\s*)?[⁻-]?\s*1\b"
        )
    elif channel == "sensing":
        signal = re.compile(
            r"(?i)\b(?:sensor|sensing|piezoelectric|gauge\s+factor|sensitivity|"
            r"response\s+time|recovery\s+time|open\s+circuit\s+voltage|"
            r"short\s+circuit\s+current|working\s+range)\b"
        )
    else:
        raise ValueError(f"unsupported specialized context channel: {channel}")

    ordered = sorted(
        [
            chunk for chunk in chunks
            if (chunk.get("section_name") or "").lower() not in BACKGROUND_SECTIONS
            and chunk.get("source_type") != "table_text"
            and str(chunk.get("raw_text") or "").strip()
        ],
        key=lambda chunk: (chunk.get("page_number") or 0, chunk.get("order_index") or 0),
    )
    matched = {
        index for index, chunk in enumerate(ordered)
        if signal.search(str(chunk.get("raw_text") or ""))
    }
    selected = set(matched)
    for index in matched:
        for neighbor in (index - 1, index + 1):
            if (
                0 <= neighbor < len(ordered)
                and ordered[neighbor].get("page_number") == ordered[index].get("page_number")
            ):
                selected.add(neighbor)

    parts: list[str] = []
    used = 0
    for index in sorted(selected):
        chunk = ordered[index]
        block = f"{_chunk_header(chunk)}\n{str(chunk.get('raw_text') or '').strip()}"
        if parts and used + len(block) > max_chars:
            continue
        parts.append(block[: max_chars - used])
        used += len(parts[-1]) + 2
        if used >= max_chars:
            break
    return "\n\n".join(parts)


async def run_holistic_extraction(
    *,
    chunks: list[dict],
    llm_json: Callable[..., Awaitable[tuple[dict, str]]],
    llm_timeout: int,
    sample_max_chars: int = 16000,
    catalog_reasoning_effort: str | None = None,
    max_performance_tokens: int = 6000,
    performance_timeout: int | None = None,
    results_max_chars: int = 35000,
    sensing_enabled: bool = True,
    performance_window_chars: int = 18000,
    performance_window_overlap_blocks: int = 1,
    parallel_calls: int = 3,
    background_timeout: int = 60,
    background_max_chars: int = 9000,
    background_max_tokens: int = 1400,
    table_timeout: int = 75,
) -> HolisticExtractionResult:
    """Run large-context extraction with bounded, block-aware parallel sweeps."""
    experimental, results = build_context_texts(chunks, results_max_chars=results_max_chars)
    result = HolisticExtractionResult(
        experimental_chars=len(experimental),
        results_chars=len(results),
    )
    if not experimental.strip() and not results.strip():
        return result

    if experimental.strip():
        sample_chunks = select_sample_catalog_context_chunks(
            chunks,
            max_chars=sample_max_chars,
        )
        sample_context = merge_chunks_text(
            sample_chunks,
            sections=EXPERIMENTAL_SECTIONS,
            max_chars=sample_max_chars,
        )
        if len(sample_context) < 1500:
            sample_context = experimental[:sample_max_chars]
        treatment_chunks = select_treatment_variant_context_chunks(chunks)
        treatment_context = merge_chunks_text(
            treatment_chunks,
            sections=EXPERIMENTAL_SECTIONS,
            max_chars=4500,
        )

        async def _extract_catalog(
            prompt: str,
            user_text: str,
            *,
            max_tokens: int,
            stage: str,
        ) -> list[dict]:
            parsed, _ = await llm_json(
                prompt,
                user_text,
                max_tokens=max_tokens,
                timeout_seconds=llm_timeout,
                stage=stage,
                reasoning_effort=catalog_reasoning_effort,
            )
            return [
                sample
                for sample in _response_rows(parsed, "samples", "_items")
                if isinstance(sample, dict) and sample.get("sample_id")
            ]

        catalog_calls: list[tuple[str, Awaitable[list[dict]]]] = [(
            "samples",
            _extract_catalog(
                SAMPLES_PROMPT,
                f"Experimental text:\n{sample_context}",
                max_tokens=2500,
                stage="holistic_samples",
            ),
        )]
        if treatment_context:
            catalog_calls.append((
                "treatment_variants",
                _extract_catalog(
                    TREATMENT_VARIANTS_PROMPT,
                    f"Treatment text:\n{treatment_context}",
                    max_tokens=1000,
                    stage="holistic_treatment_variants",
                ),
            ))
        catalog_outcomes = await asyncio.gather(
            *(call for _, call in catalog_calls),
            return_exceptions=True,
        )
        catalog_samples: list[dict] = []
        for (kind, _), outcome in zip(catalog_calls, catalog_outcomes):
            if isinstance(outcome, BaseException):
                if kind == "samples":
                    raise outcome
                result.warnings.append(f"{kind}: {outcome}")
                continue
            catalog_samples.extend(outcome)
        result.samples = sanitize_catalog_samples(
            catalog_samples,
            source_text=experimental,
        )
        result.samples = augment_catalog_samples_from_process_tables(chunks, result.samples)

    sample_ids = [
        normalize_sample_id(s.get("sample_id") or "")
        for s in result.samples
        if s.get("sample_id")
    ]
    semaphore = asyncio.Semaphore(max(1, int(parallel_calls or 1)))
    tasks: list[tuple[str, Awaitable[Any]]] = []

    async def _run_background() -> dict[str, Any]:
        sample_hint = ", ".join(sample_ids) or "unknown"
        async with semaphore:
            parsed, _ = await llm_json(
                BACKGROUND_PROMPT,
                f"Known samples: {sample_hint}\n\nExperimental text:\n{experimental[:background_max_chars]}",
                max_tokens=background_max_tokens,
                timeout_seconds=min(llm_timeout, background_timeout),
                stage="holistic_background",
                reasoning_effort=catalog_reasoning_effort,
            )
        return parsed if isinstance(parsed, dict) else {}

    if experimental.strip() and catalog_supports_shared_background(result.samples):
        tasks.append(("background", _run_background()))

    if results.strip() and sample_ids:
        windows = split_context_windows(
            results,
            max_chars=performance_window_chars,
            overlap_blocks=performance_window_overlap_blocks,
        )
        sweep_specs: list[tuple[str, str, str, int, str]] = []
        for index, window in enumerate(windows, start=1):
            stage = (
                "holistic_performances"
                if len(windows) == 1
                else f"holistic_performances_{index}_of_{len(windows)}"
            )
            sweep_specs.append((
                "performances", PERFORMANCE_PROMPT, stage,
                max_performance_tokens, window,
            ))
        if sensing_enabled and _needs_sensing_sweep(results):
            sensing_context = select_specialized_result_context(
                chunks, channel="sensing", max_chars=min(results_max_chars, 12000)
            ) or results
            sweep_specs.append(
                (
                    "sensing",
                    SENSING_SWEEP_PROMPT,
                    "holistic_sensing",
                    min(max_performance_tokens, 4500),
                    sensing_context,
                ),
            )
        if _needs_spectroscopy_sweep(results):
            spectroscopy_context = select_specialized_result_context(
                chunks, channel="spectroscopy", max_chars=min(results_max_chars, 12000)
            ) or results
            spectroscopy_windows = split_context_windows(
                spectroscopy_context,
                max_chars=6500,
                overlap_blocks=0,
            )
            for index, window in enumerate(spectroscopy_windows, start=1):
                stage = (
                    "holistic_spectroscopy"
                    if len(spectroscopy_windows) == 1
                    else f"holistic_spectroscopy_{index}_of_{len(spectroscopy_windows)}"
                )
                sweep_specs.append((
                    "spectroscopy",
                    SPECTROSCOPY_SWEEP_PROMPT,
                    stage,
                    min(max_performance_tokens, 3000),
                    window,
                ))

        async def _run_sweep_call(
            prompt: str,
            stage: str,
            max_tokens: int,
            window: str,
        ) -> list[dict]:
            async with semaphore:
                return await _run_performance_sweep(
                    prompt=prompt,
                    results_text=window,
                    sample_ids=sample_ids,
                    llm_json=llm_json,
                    llm_timeout=min(
                        llm_timeout,
                        performance_timeout or llm_timeout,
                    ),
                    max_tokens=max_tokens,
                    stage=stage,
                    results_max_chars=results_max_chars,
                )

        async def _run_one(
            kind: str,
            prompt: str,
            stage: str,
            max_tokens: int,
            window: str,
        ) -> list[dict]:
            try:
                return await _run_sweep_call(prompt, stage, max_tokens, window)
            except Exception as exc:
                message = str(exc).lower()
                retryable = any(token in message for token in (
                    "timed out", "timeout", "unusable json", "non-json",
                ))
                if kind != "performances" or not retryable:
                    raise

                retry_window_chars = max(
                    1000,
                    min(3000, max(1, int(performance_window_chars or 1)) // 2),
                )
                retry_windows = split_context_windows(
                    window,
                    max_chars=retry_window_chars,
                    overlap_blocks=0,
                )
                if len(retry_windows) <= 1:
                    raise
                retry_tokens = max(1800, min(max_tokens, int(max_tokens * 0.65)))
                retry_outcomes = await asyncio.gather(*(
                    _run_sweep_call(
                        prompt,
                        f"{stage}_retry_{index}_of_{len(retry_windows)}",
                        retry_tokens,
                        retry_window,
                    )
                    for index, retry_window in enumerate(retry_windows, start=1)
                ), return_exceptions=True)
                failures = [
                    outcome for outcome in retry_outcomes
                    if isinstance(outcome, BaseException)
                ]
                if failures:
                    raise RuntimeError(
                        f"{stage} targeted retry failed after {exc}: {failures[0]}"
                    ) from failures[0]
                return [
                    fact
                    for outcome in retry_outcomes
                    for fact in outcome
                ]

        # Start the usually slower specialized shards first so shorter core
        # windows can fill released slots instead of creating a second long wave.
        sweep_specs.sort(
            key=lambda spec: {"spectroscopy": 0, "sensing": 1}.get(spec[0], 2)
        )
        for kind, prompt, stage, max_tokens, window in sweep_specs:
            tasks.append((kind, _run_one(kind, prompt, stage, max_tokens, window)))

    table_chunks = [
        chunk for chunk in chunks
        if chunk.get("source_type") == "table_text"
        and "[row " in str(chunk.get("raw_text") or "").lower()
    ]
    if table_chunks:
        async def _run_table(chunk: dict, index: int) -> dict[str, Any]:
            table_text = str(chunk.get("raw_text") or "")
            table_context = _nearby_table_context(chunks, chunk)
            table_header, source_rows = _table_row_map(table_text)
            table_role = classify_table_role(table_text)
            if table_role == "context":
                return {
                    "facts": [],
                    "block_id": str(chunk.get("source_block_id") or ""),
                    "covered": True,
                    "missing_cells": 0,
                    "table_role": table_role,
                }
            if table_role == "process":
                return {
                    "facts": process_table_to_facts(
                        table_text=table_text,
                        known_samples=result.samples,
                        source_location=_chunk_header(chunk),
                        source_block_id=chunk.get("source_block_id"),
                        source_page=chunk.get("page_number"),
                        source_bbox=chunk.get("source_bbox"),
                    ),
                    "block_id": str(chunk.get("source_block_id") or ""),
                    "covered": True,
                    "missing_cells": 0,
                    "table_role": table_role,
                }
            transposed_facts = deterministic_transposed_performance_table_facts(
                table_text=table_text,
                table_context=table_context,
                source_location=_chunk_header(chunk),
                source_block_id=chunk.get("source_block_id"),
                source_page=chunk.get("page_number"),
                source_bbox=chunk.get("source_bbox"),
                known_sample_ids=sample_ids,
            )
            if transposed_facts:
                return {
                    "facts": transposed_facts,
                    "block_id": str(chunk.get("source_block_id") or ""),
                    "covered": True,
                    "missing_cells": 0,
                    "table_role": "performance",
                }
            expected_cells = _table_expected_result_cells(table_header, source_rows)
            deterministic_facts = deterministic_performance_table_facts(
                table_text=table_text,
                table_context=table_context,
                source_location=_chunk_header(chunk),
                source_block_id=chunk.get("source_block_id"),
                source_page=chunk.get("page_number"),
                source_bbox=chunk.get("source_bbox"),
                known_sample_ids=sample_ids,
            )
            deterministic_cells = {
                (int(fact["_source_table_row"]), int(fact["_source_table_column"]))
                for fact in deterministic_facts
                if (
                    fact.get("_source_table_row") is not None
                    and fact.get("_source_table_column") is not None
                )
            }
            if expected_cells and not set(expected_cells).difference(deterministic_cells):
                return {
                    "facts": deterministic_facts,
                    "block_id": str(chunk.get("source_block_id") or ""),
                    "covered": True,
                    "missing_cells": 0,
                    "table_role": table_role,
                }
            unresolved_cells = set(expected_cells).difference(
                deterministic_cells
            )
            llm_row_numbers = (
                sorted({row for row, _ in unresolved_cells})
                if expected_cells
                else sorted(source_rows)
            )
            table_shards = _table_row_shards(
                table_header,
                source_rows,
                row_numbers=llm_row_numbers,
            )
            shard_warnings: list[str] = []

            async def run_table_shard(
                shard: str,
                shard_index: int,
            ) -> tuple[dict, str]:
                shard_row_count = len(_table_row_map(shard)[1])
                table_tokens = min(
                    max_performance_tokens,
                    max(1200, min(3500, 500 + shard_row_count * 180)),
                )
                stage = f"holistic_table_{index}_of_{len(table_chunks)}"
                if len(table_shards) > 1:
                    stage += (
                        f"_part_{shard_index}_of_{len(table_shards)}"
                    )
                async with semaphore:
                    return await llm_json(
                        TABLE_PERFORMANCE_PROMPT.format(
                            sample_ids=", ".join(sample_ids) or "unknown",
                        ),
                        (
                            f"Nearby table context:\n{table_context[:1600]}\n\n"
                            if table_context
                            else ""
                        ) + f"Structured table rows:\n{shard}",
                        max_tokens=table_tokens,
                        timeout_seconds=min(llm_timeout, table_timeout),
                        stage=stage,
                    )

            shard_outcomes = await asyncio.gather(*(
                run_table_shard(shard, shard_index)
                for shard_index, shard in enumerate(table_shards, start=1)
            ), return_exceptions=True)
            items: list[dict] = []
            for shard_index, outcome in enumerate(shard_outcomes, start=1):
                if isinstance(outcome, BaseException):
                    shard_warnings.append(
                        f"part {shard_index}/{len(table_shards)} failed: "
                        f"{outcome}"
                    )
                    continue
                parsed, _ = outcome
                items.extend(_response_rows(parsed, "rows", "_items"))
            facts = table_rows_to_facts(
                items,
                table_text=table_text,
                table_context=table_context,
                source_location=_chunk_header(chunk),
                source_block_id=chunk.get("source_block_id"),
                source_page=chunk.get("page_number"),
                source_bbox=chunk.get("source_bbox"),
                known_sample_ids=sample_ids,
            )
            facts_by_cell = {
                (fact.get("_source_table_row"), fact.get("_source_table_column")): fact
                for fact in facts
            }
            for fact in deterministic_facts:
                cell_key = (
                    fact.get("_source_table_row"),
                    fact.get("_source_table_column"),
                )
                facts_by_cell.setdefault(cell_key, fact)
            facts = [facts_by_cell[key] for key in sorted(facts_by_cell)]
            grounded_cells = {
                (int(fact["_source_table_row"]), int(fact["_source_table_column"]))
                for fact in facts
                if (
                    fact.get("_source_table_row") is not None
                    and fact.get("_source_table_column") is not None
                )
            }
            missing_cells = set(expected_cells).difference(grounded_cells)
            if missing_cells:
                repair_shards = _table_row_shards(
                    table_header,
                    source_rows,
                    row_numbers=sorted({row for row, _ in missing_cells}),
                    max_rows=4,
                    max_chars=3200,
                )

                async def run_repair_shard(
                    shard: str,
                    shard_index: int,
                ) -> tuple[dict, str]:
                    shard_rows = set(_table_row_map(shard)[1])
                    shard_missing = [
                        (row, column)
                        for row, column in sorted(missing_cells)
                        if row in shard_rows
                    ]
                    missing_description = "; ".join(
                        f'row {row}, column '
                        f'"{expected_cells[(row, column)][0]}", '
                        f'cell "{expected_cells[(row, column)][1]}"'
                        for row, column in shard_missing
                    )
                    repair_tokens = min(
                        max_performance_tokens,
                        max(
                            900,
                            min(2800, 400 + len(shard_missing) * 140),
                        ),
                    )
                    stage = (
                        f"holistic_table_repair_{index}_of_"
                        f"{len(table_chunks)}"
                    )
                    if len(repair_shards) > 1:
                        stage += (
                            f"_part_{shard_index}_of_{len(repair_shards)}"
                        )
                    async with semaphore:
                        return await llm_json(
                            TABLE_REPAIR_PROMPT.format(
                                sample_ids=", ".join(sample_ids) or "unknown",
                                missing_cells=missing_description,
                            ),
                            (
                                f"Nearby table context:\n"
                                f"{table_context[:1600]}\n\n"
                                if table_context
                                else ""
                            ) + f"Structured table rows:\n{shard}",
                            max_tokens=repair_tokens,
                            timeout_seconds=min(llm_timeout, table_timeout),
                            stage=stage,
                        )

                repair_outcomes = await asyncio.gather(*(
                    run_repair_shard(shard, shard_index)
                    for shard_index, shard in enumerate(
                        repair_shards,
                        start=1,
                    )
                ), return_exceptions=True)
                repair_items: list[dict] = []
                for shard_index, outcome in enumerate(
                    repair_outcomes,
                    start=1,
                ):
                    if isinstance(outcome, BaseException):
                        shard_warnings.append(
                            f"repair part {shard_index}/"
                            f"{len(repair_shards)} failed: {outcome}"
                        )
                        continue
                    repair_parsed, _ = outcome
                    repair_items.extend(
                        _response_rows(repair_parsed, "rows", "_items")
                    )
                repair_facts = table_rows_to_facts(
                    repair_items,
                    table_text=table_text,
                    table_context=table_context,
                    source_location=_chunk_header(chunk),
                    source_block_id=chunk.get("source_block_id"),
                    source_page=chunk.get("page_number"),
                    source_bbox=chunk.get("source_bbox"),
                    known_sample_ids=sample_ids,
                )
                facts_by_cell = {
                    (fact.get("_source_table_row"), fact.get("_source_table_column")): fact
                    for fact in facts
                }
                for fact in repair_facts:
                    cell_key = (
                        fact.get("_source_table_row"),
                        fact.get("_source_table_column"),
                    )
                    if cell_key in missing_cells:
                        facts_by_cell.setdefault(cell_key, fact)
                facts = [
                    facts_by_cell[key]
                    for key in sorted(facts_by_cell)
                ]
                grounded_cells = {
                    (int(fact["_source_table_row"]), int(fact["_source_table_column"]))
                    for fact in facts
                    if (
                        fact.get("_source_table_row") is not None
                        and fact.get("_source_table_column") is not None
                    )
                }
                missing_cells = set(expected_cells).difference(grounded_cells)
            return {
                "facts": facts,
                "block_id": str(chunk.get("source_block_id") or ""),
                "covered": bool(expected_cells) and not missing_cells,
                "missing_cells": len(missing_cells),
                "table_role": table_role,
                "warnings": shard_warnings,
            }

        for index, chunk in enumerate(table_chunks, start=1):
            tasks.append(("table", _run_table(chunk, index)))

    if tasks:
        outcomes = await asyncio.gather(
            *(task for _, task in tasks),
            return_exceptions=True,
        )
        merged_facts: list[dict] = []
        for (kind, _), outcome in zip(tasks, outcomes):
            if isinstance(outcome, BaseException):
                result.warnings.append(f"{kind}: {outcome}")
                continue
            if kind == "background":
                if not outcome.get("_parse_failed"):
                    result.background = {
                        "composition": outcome.get("composition") or {},
                        "process": outcome.get("process") or {},
                        "structure": outcome.get("structure") or {},
                    }
            elif kind == "table":
                table_facts = outcome.get("facts") or []
                merged_facts.extend(table_facts)
                for warning in outcome.get("warnings") or []:
                    result.warnings.append(
                        f"table: {outcome.get('block_id') or 'unknown'} "
                        f"{warning}"
                    )
                if outcome.get("covered") and outcome.get("block_id"):
                    result.covered_table_block_ids.append(outcome["block_id"])
                elif outcome.get("missing_cells"):
                    result.warnings.append(
                        f"table: {outcome.get('block_id') or 'unknown'} missing "
                        f"{outcome['missing_cells']} result cells after repair"
                    )
            else:
                merged_facts.extend(outcome)

        chunks_by_block_id = {
            str(chunk.get("source_block_id") or ""): chunk
            for chunk in chunks
            if chunk.get("source_block_id")
        }
        for fact in merged_facts:
            source_chunk = chunks_by_block_id.get(
                str(fact.get("_source_block_id") or "")
            )
            if not source_chunk:
                continue
            fact.setdefault("_chunk_section", source_chunk.get("section_name") or "")
            fact.setdefault("_chunk_source_type", source_chunk.get("source_type") or "")
            fact.setdefault("_source_page", source_chunk.get("page_number"))
            fact.setdefault("_source_bbox", source_chunk.get("source_bbox"))

        for index, fact in enumerate(merged_facts, start=1):
            fact["fact_id"] = f"H{index:04d}"
        result.performance_facts = merge_holistic_and_atomic_facts([], merged_facts)

    return result
