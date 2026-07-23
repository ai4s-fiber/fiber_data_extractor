"""LLM prompt templates for the V7 extraction pipeline (English instructions)."""

SAMPLE_MENTIONS_PROMPT = """Extract sample names mentioned in this single chunk.

Rules:
1. mention_text must be copied from the chunk.
2. normalized_sample_id may only lightly normalize spaces or symbols.
3. Keep labels such as S1/S2, A/B, 0.5 wt%, R=1.5, Sample-1.
4. Do not invent names. Do not create sample groups.
5. Do not treat generic phrases such as "composite fiber", "optimized sample", or "modified sample" as samples unless the chunk explicitly maps them to a concrete label.
6. Distinguish sample form when stated: aerogel vs nanofiber vs film are different specimens. Example: "PI nanofiber" is NOT "PI1 aerogel".

Output JSON:
{"sample_mentions":[{"mention_text":"","normalized_sample_id":"","aliases":[],"context_text":"","confidence":0.0}]}"""

VARIABLE_CANDIDATES_PROMPT = """Extract explicit sample variables from this single chunk.

Rules:
1. Use only variables explicitly stated near a sample name.
2. Do not infer missing variables.
3. Do not use performance metrics as variable names.
4. A sample may have multiple variables.
5. Do not create sample groups or sample cards.

Output JSON:
{"variable_candidates":[{"sample_id":"","variable_name_raw":"","variable_value_raw":"","variable_unit_raw":"","context_text":"","confidence":0.0}]}"""

STAGE2_FACTS_PROMPT = """Extract atomic facts from this single chunk.

Rules:
1. ONE numerical value = ONE fact. Do NOT combine multiple values into one fact.
2. A sentence with 3 numbers → produce 3 facts.
3. A table with 5 rows × 4 columns → produce up to 20 facts (one per cell with a numerical value).
4. Sample-value alignment (mandatory):
   - If evidence_text contains multiple sample names AND multiple numeric values, produce ONE fact per aligned sample-value pair.
   - Parenthesis form "A (10), B (20), C (30)" → A=10, B=20, C=30. The value in parentheses belongs to the nearest sample before it.
   - "A is lower/higher than B (value)" → the parenthesized value belongs to B, not A.
   - "A compared to B (value)" → value belongs to B.
   - "A1 (v1), A2 (v2), A3 (v3) and B (v4)" → four separate facts; do NOT merge all values onto A1/A2/A3.
   - "permittivity of 1.004 and loss tangent of 8e-4" → two facts with dielectric_constant=1.004 and loss_tangent=8e-4.
   - Target sample vs control sample in one sentence → separate facts; never attach control values to target sample.
5. Each fact MUST include evidence_text (quote the original text), source_location (page/table/figure), source_block_id, and source_page copied from the exact input block header.
6. candidate_sample_ids: sample names aligned with this fact's value only.
7. Keep full sample IDs (Sample-1, PI1, PI-200°C, 2MZ-AZINE-PI3). Do NOT truncate PI1→PI.
8. Skip Introduction/background/literature-reference values unless clearly this paper's own experimental result.
9. Use loss_tangent for tan δ; dielectric_constant for permittivity/ε′; dielectric_loss for ε″ only.
10. Cycle counts, ordinary test frequency, strain, thickness, and humidity are conditions, not standalone performance values. A bandgap, resonance, eigenfrequency, or transmission-attenuation frequency/range is a performance result.
11. Fill performance_condition when stated. Do not invent missing conditions.
12. Do not create sample groups, sample cards, final records, or Excel rows.
13. sample_id / candidate_sample_ids must come from explicit names in the chunk. NEVER put test/process conditions into sample_id (200°C, 300°C, 20 min, 50% strain, X-band, RH≈100%, 8–12 GHz) unless the chunk explicitly names a specimen that way (e.g. "PI-200°C sample"). Put those tokens in condition / performance_condition instead.
14. Do NOT infer sample suffixes: if the chunk says "2MZ-AZINE-PI nanofibers", do NOT add "-20%"; if it says "2MZ-AZINE-PI3 aerogel", do NOT rename to "PI-200°C".
15. Metric-value binding: permittivity/εr → dielectric_constant; loss tangent/tan δ → loss_tangent; imidization % → imidization_degree (NOT crystallinity_Xc).
16. Parenthesis nearest-neighbor: "Sample A (v1) ... Sample B (v2)" → v1 binds to A, v2 binds to B only; never attach all values to one sample.
17. Compressive cycling: "compressive stress decreased from A to B after N cycles" → compressive_stress before=A and after=B; cyclic_compression_stability = retention ratio (B/A×100%) or "decreased from A to B", NOT value B alone and NOT N cycles.
18. Scientific notation mandatory: evidence with ×10^ / 10⁻ / 10^- / E- must preserve full exponent (8×10⁻⁴ → 8e-4, never 8).
19. Metric-unit binding: density ↔ g/cm³ or mg/cm³; surface_roughness ↔ nm/μm (never mg/cm³); thermal_conductivity ↔ W/m·K; FTIR/Raman peaks ↔ cm⁻¹; XPS binding energy ↔ eV.
20. FTIR_band / Raman_peak / XPS_peak / XRD_peak / NMR_shift are characterization peaks (structure proof), NOT core performance metrics.
21. FTIR reference peaks used only in imidization formulas (e.g. 1377 & 1489 cm⁻¹) are method parameters, not performance.
22. If evidence says PI1 or PI1 aerogel, sample_id must be PI1 aerogel — never collapse to generic PI.
23. Explicit material-behavior transition strains are performance facts only when the same quoted evidence binds value and phenomenon: knee → knee_strain; damage-index change → damage_transition_strain; stiffness recovery → stiffness_recovery_strain. A generic transition-zone endpoint or ordinary test strain is not a standalone result.
24. Extract unfamiliar but explicit material/structure results even when absent from the standardized list. Examples include displacement at a fixed load, softening load, load-stability improvement, bandgap range, transmission-attenuation range, maximum acceleration, acceleration reduction, and specific energy absorption.

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
      "condition": "gauge length 20 mm, speed 10 mm/min; nanofiber tensile test",
      "category": "mechanical",
      "evidence_text": "PI-200 exhibited a tensile strength of 7.13 MPa",
      "source_location": "Section 3.2, page 8",
      "source_block_id": "B000123",
      "source_page": 8,
      "extraction_method": "AI_text",
      "confidence": 0.9
    }
  ]
}"""

STAGE2_PERFORMANCE_REPAIR_PROMPT = """Repair missing quantitative material-performance facts from these selected high-information blocks.

Known sample IDs:
{sample_ids}

Rules:
1. Extract only this paper's explicit measured or calculated material/structure PERFORMANCE RESULTS.
2. Keep intrinsic constituent properties (for example density, Young's modulus, or Poisson's ratio) when explicitly reported. Assign them to the constituent, never to a composite variant.
3. Skip composition/loading values, fabrication parameters, test conditions, geometry/model constants, formula variables, point/row counts, temperatures, speeds, cycle counts, and figure labels as standalone results. Put relevant test settings in condition.
4. A bandgap, eigenfrequency, resonance, attenuation-frequency range, displacement at fixed load, softening load, acceleration, or energy absorption is a result, not a test condition.
5. Preserve exact sample-value binding. Resolve generic phrases such as "the reinforced composite" to an explicit composition/configuration stated in the same input block. Do not guess across blocks.
6. Use a Known sample ID when the evidence supports it. Create one concise new ID only when the block explicitly describes a different composition/configuration.
7. ONE scalar result = ONE fact. Keep a scientifically meaningful range A-B as one range fact. Never emit uncertainty, a range endpoint, or a comparison baseline as an extra metric.
8. Copy evidence_text verbatim and copy source_block_id/source_page from the exact block header containing the value.
9. Skip prior-work/reference values and qualitative claims. Do not calculate or infer unstated values.
10. Use a precise metric name. Unknown but explicit performance metrics are allowed.

Return compact JSON only:
{{"facts":[{{"fact_type":"performance","subject_text":"","candidate_sample_ids":[],"metric_or_parameter":"","value":"","unit":"","method":"","condition":"","category":"","evidence_text":"","source_location":"","source_block_id":"","source_page":0,"confidence":0.0}}]}}"""

WEAK_FACTS_PROMPT = """Extract atomic material facts from this text chunk or batched chunk group.

Rules:
1. Extract composition, process, structure, and performance facts when they are this paper's own material data.
2. ONE value/parameter = ONE fact. A table row with 4 measured values should produce up to 4 facts.
3. fact_type:
   - composition: polymer/matrix/additive/filler/solvent/content/loading/concentration.
   - process: electrospinning/spinning voltage, flow rate, tip-collector distance, annealing, drawing, poling, drying, curing, heat treatment.
   - structure: fiber diameter, crystallinity, beta phase, morphology, porosity, roughness, XRD/FTIR/Raman/SEM/TEM observations.
   - performance: mechanical, thermal, electrical, piezoelectric, dielectric, sensing, filtration, adsorption, electrochemical metrics.
4. Fill metric_or_parameter, value, unit, method, and condition as completely as possible.
5. candidate_sample_ids: sample names appearing in the chunk and aligned with the value. Leave [] only for paper-level background facts that clearly apply to all samples.
6. Do not summarize or infer beyond the chunk text.
7. Skip Introduction/background/literature-reference values unless clearly this paper's own experimental result.
8. Distinguish aerogel vs nanofiber vs film vs device specimens when assigning sample names.
9. When multiple chunks are provided, keep evidence_text and source_location tied to the exact chunk header that contains the value.

Output JSON:
{"facts":[{"fact_type":"composition|process|structure|performance","candidate_sample_ids":[],"metric_or_parameter":"","value":"","unit":"","method":"","condition":"","category":"","evidence_text":"","source_location":"","confidence":0.0}]}"""

STAGE3_ASSIGNMENT_PROMPT = """You are a data matching specialist. Given a sample catalog and a list of facts, assign each fact to the correct sample(s).

Sample catalog (known samples):
{{sample_catalog_json}}

Rules:
1. If the fact's evidence_text or subject_text explicitly mentions a sample_id or alias → high confidence assignment.
2. If the fact comes from a table row labeled with a sample name → match to that sample.
3. If the fact comes from a figure caption mentioning a sample → match to that sample.
4. Prefer the most specific sample ID. Do not assign "2MZ-AZINE-PAA" data to generic "PAA", or "sample-200-modified" data to "sample-200".
5. If a figure caption lists multiple subpanels/samples, only assign a fact to the sample named in the same subpanel/table row/context.
6. If the context only refers to generic terms ("the modified fiber", "the composite"), do NOT force assignment.
7. A fact can be assigned to multiple samples (candidate_sample_ids with assignment_status="multiple").
8. If no reasonable match exists, set assigned_sample_id to null and assignment_status to "unassigned".
9. Set assignment_confidence between 0.0 and 1.0.
10. Sample form matters: PI nanofiber ≠ PI1 aerogel ≠ 2MZ-AZINE-PI3 aerogel. Tensile strength from nanofiber tests must NOT go to aerogel IDs. Aerogel compression/thermal/dielectric data must NOT go to nanofiber IDs.
11. PI-200°C (or PI-200) is a low-temperature treated PI specimen, not the core 2MZ-AZINE-PI3 aerogel unless explicitly stated.

Output JSON format:
{
  "assignments": [
    {"fact_id": "F001", "assigned_sample_id": "PI-200", "assignment_confidence": 0.95, "assignment_status": "assigned"},
    {"fact_id": "F002", "assigned_sample_id": null, "assignment_confidence": 0.0, "assignment_status": "unassigned"}
  ]
}"""
