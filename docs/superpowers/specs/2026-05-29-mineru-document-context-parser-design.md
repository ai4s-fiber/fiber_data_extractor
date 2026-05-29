# MinerU Document Context Parser Design

## Goal

Use MinerU as the dedicated PDF/document parsing service and upgrade the
extraction input model from flattened PDF text to a structured document context.

The current V7 material extraction layer stays as the product workflow owner:
job runner, SSE progress, weak/strong modes, sample/fact extraction, review, and
export remain in this application. The old PyMuPDF/pdfplumber PDF parsing layer
is no longer the main path. It may stay as a temporary emergency fallback while
MinerU integration is validated.

The final user-facing main table must still keep material composition, process,
structure, and performance together in one row. We only split paper metadata,
long evidence detail, and MinerU block trace data into separate sheets/tables.

## Decisions

- Use MinerU through its service API, not by copying core files into this repo.
- Submit parsing jobs through `POST /tasks`, then poll `/tasks/{task_id}` and
  fetch `/tasks/{task_id}/result`.
- Treat MinerU output as the canonical document source for extraction.
- Normalize MinerU output into an internal `DocumentContext` before prompting.
- Do not send raw full MinerU JSON directly to the LLM. Build smaller
  stage-specific context packs from `DocumentContext`.
- Keep `mimo-v2.5` as the default model. It can run both weak and strong modes.
  Do not assume `opus-4-6` is available.
- Change export shape from a single 40-column sheet to a narrower main data
  sheet plus supporting sheets.

## Non-Goals

- Do not replace the material extraction logic with MinerU. MinerU parses
  document structure; this app extracts material science facts.
- Do not make the old 40-column table the internal source of truth.
- Do not split sample composition/process/structure away from the main material
  data row.
- Do not implement a distributed queue for MinerU in this phase. Use the current
  extraction job runner and call MinerU as an external service.

## Current Problems

The current PDF layer produces:

- page text from PyMuPDF/pdfplumber,
- markdown tables from pdfplumber,
- simple chunks classified by page text heuristics.

This loses layout, reading order, table and caption relationships, image/chart
blocks, and precise locations. V7 then has to infer too much from flattened
text, which causes missing paper metadata, sparse composition/process fields,
rough evidence locations, and repeated or mis-bound structure evidence.

The current 40-column candidate table also mixes paper metadata, sample
attributes, material facts, evidence, and review state. It is useful as an old
export format, but too wide and too flat for the upgraded extraction model.

## Architecture

```text
ExtractionJob
  -> MinerUClient POST /tasks
  -> poll MinerU task
  -> fetch result
  -> MinerUDocumentAdapter
  -> DocumentContext
  -> context packs
  -> V7 weak/strong extraction
  -> Main_Data rows + Evidence + Papers + Parse_Blocks + Quality_Report
```

### MinerU Service Call

The backend adds a MinerU client with configurable settings:

- `MINERU_ENABLED`
- `MINERU_API_URL`
- `MINERU_BACKEND`, default `pipeline` or deployment-specific
- `MINERU_PARSE_METHOD`, default `auto`
- `MINERU_LANG`, default `ch`
- `MINERU_TASK_TIMEOUT_SECONDS`
- `MINERU_POLL_INTERVAL_SECONDS`
- `MINERU_FALLBACK_LEGACY_PARSER`, temporary validation-only option

Stage 0 of extraction submits:

```http
POST {MINERU_API_URL}/tasks
```

with:

- `files=@paper.pdf`
- `backend`
- `parse_method=auto`
- `return_md=true`
- `return_content_list=true`
- `return_middle_json=true`
- `return_images=false` initially
- `response_format_zip=false`

The app polls:

```http
GET {MINERU_API_URL}/tasks/{task_id}
GET {MINERU_API_URL}/tasks/{task_id}/result
```

MinerU task id, backend, status, timing, and errors are recorded in our database.

## DocumentContext

`DocumentContext` is the normalized internal representation. It insulates V7
from MinerU schema churn and gives every extracted fact a stable source anchor.

```text
DocumentContext
- paper_id
- job_id
- parse_run_id
- markdown_text
- page_count
- pages[]
  - page_number
  - width
  - height
  - text
  - block_ids[]
- blocks[]
  - block_id
  - page_number
  - order_index
  - type
  - section_name
  - text
  - html
  - bbox
  - parent_block_id
  - related_block_ids[]
  - source_payload_ref
- tables[]
  - table_id
  - block_id
  - page_number
  - caption
  - html
  - markdown
  - bbox
- figures[]
  - figure_id
  - block_id
  - page_number
  - figure_type
  - caption
  - image_path
  - bbox
- source_map
```

Block types use a controlled vocabulary:

- `title`
- `paragraph`
- `table`
- `table_caption`
- `figure`
- `figure_caption`
- `chart`
- `equation`
- `list`
- `reference`
- `header_footer`
- `discarded`
- `unknown`

`content_list_v2` is preferred. `content_list`, `middle_json`, and markdown are
fallback or enrichment sources. If MinerU produces a field that does not map
cleanly, store it in the raw parse artifact and keep the normalized block stable.

## Database Design

### New Tables

`document_parse_runs`

- `id`
- `paper_id`
- `job_id`
- `parser_name`, value `mineru`
- `mineru_task_id`
- `mineru_backend`
- `parse_method`
- `status`
- `error_code`
- `error_message`
- `raw_result_path`
- `markdown_path`
- `started_at`
- `finished_at`
- `created_at`

`document_blocks`

- `id`
- `parse_run_id`
- `paper_id`
- `job_id`
- `block_id`
- `page_number`
- `order_index`
- `block_type`
- `section_name`
- `text`
- `html`
- `bbox_json`
- `parent_block_id`
- `related_block_ids_json`
- `raw_payload_json`

`document_tables`

- `id`
- `parse_run_id`
- `paper_id`
- `job_id`
- `table_id`
- `block_id`
- `page_number`
- `caption`
- `html`
- `markdown`
- `bbox_json`

`document_figures`

- `id`
- `parse_run_id`
- `paper_id`
- `job_id`
- `figure_id`
- `block_id`
- `page_number`
- `figure_type`
- `caption`
- `image_path`
- `bbox_json`

### Upgraded Existing Tables

`page_inventory` remains, but is generated from `DocumentContext`:

- text block count
- table count
- figure/chart count
- caption count
- experimental/materials signal
- result/discussion signal
- supplementary signal
- importance score

`fact_candidates` should gain source anchors:

- `source_block_id`
- `source_page`
- `source_bbox_json`
- `evidence_item_id`

`evidence_items` should gain MinerU anchors:

- `parse_run_id`
- `block_id`
- `bbox_json`
- `mineru_block_type`

`candidate_records` can remain for compatibility and review migration, but the
new export should be generated from upgraded fact/sample/evidence data rather
than treating the old 40-column row as the only truth.

## Extraction Flow

### Stage 0: Parse

Call MinerU, persist parse run, normalize `DocumentContext`, persist
`document_blocks`, `document_tables`, and `document_figures`.

Emit SSE progress:

- `inventory 2%`: submitting MinerU task
- `inventory 5-12%`: waiting for MinerU parse
- `inventory 15%`: normalizing DocumentContext
- `inventory 18%`: building page inventory

### Stage 1: Paper Metadata

Use title/header/abstract/first-page blocks and DOI patterns. Store metadata in
`papers`. It later exports to the `Papers` sheet, not repeated in every main row.

### Stage 2: Sample Catalog

Build sample candidates from experimental/materials blocks, sample naming
patterns, table captions, figure captions, and result paragraphs. The sample
catalog remains an internal extraction layer, but it is not exported as a
separate primary sheet unless needed for debugging.

### Stage 3: Composition, Process, Structure Context

Extract reusable sample-level context:

- material system
- fiber type
- variable name/value/unit
- composition expression
- matrix/additive/solvent fields
- process route
- spinning method
- process parameters
- post treatment
- structure methods and features

Each field must cite one or more `block_id`s. Missing values are explicit
missing values, not silent hallucinations.

### Stage 4: Performance and Other Measurement Facts

Extract measurement facts from result paragraphs, tables, charts, figures, and
captions. Each fact includes:

- sample candidates
- metric
- raw value
- cleaned value
- unit
- method
- condition
- category
- source block/page/bbox
- evidence text
- confidence

Tables are handled as table-aware context packs, not plain paragraphs. Captions
and nearby result paragraphs are included together when available.

### Stage 5: Assignment and Row Synthesis

Assign facts to samples. Deterministic rules handle exact aliases and nearby
sample mentions. LLM only resolves ambiguous cases.

Generate `Main_Data` rows by combining:

- paper id,
- sample id and sample group,
- sample composition/process/structure context,
- one measurement fact,
- evidence id and review fields.

This keeps each main row complete for material data use.

## Export Workbook

The workbook replaces the old single-sheet 40-column export as the primary
format.

### Main_Data

One row is one complete material data row:

```text
record_id
paper_id
sample_id
sample_group_id
material_system
fiber_type
variable_name
variable_value
variable_unit
composition_expression
matrix_name
matrix_content
matrix_unit
additive_expression
solvent_or_aid
process_route
spinning_method
process_parameters
post_treatment
structure_methods
structure_features
performance_category
performance_metric
performance_value
performance_unit
performance_method
performance_condition
evidence_id
source_page
confidence
review_status
reviewer_comment
```

Paper title, DOI, year, journal, long evidence text, and full block metadata are
not repeated here.

### Papers

```text
paper_id
original_filename
paper_title
doi_or_url
year
journal
authors
publisher
abstract
supplementary_url
```

### Evidence

```text
evidence_id
paper_id
record_id
sample_id
block_id
page_number
bbox
source_type
mineru_block_type
evidence_text
confidence
```

### Parse_Blocks

Optional by default, useful for debugging and audit:

```text
block_id
paper_id
page_number
order_index
block_type
section_name
bbox
text_preview
related_block_ids
```

### Quality_Report

Includes:

- parse status and MinerU task id,
- page/block/table/figure counts,
- sample count,
- fact count,
- generated row count,
- missing field rates,
- evidence coverage,
- low confidence rows,
- supplementary material hints,
- parser/extractor error summary.

### Legacy_40_Columns

Optional compatibility projection only. It is not the primary data model.

## Weak and Strong Modes with mimo-v2.5

`mimo-v2.5` can be used in both modes:

- `weak`: fewer stages and smaller context packs for faster first-pass output.
- `strong`: full DocumentContext packs, strict evidence anchors, table-aware and
  figure-aware extraction, stronger quality report.
- `auto`: default to `strong` while we validate extraction quality.

Model resolution should no longer classify strong mode by matching Opus/GPT
model names. Explicit user/project mode and `mimo-v2.5` compatibility drive the
choice.

## Error Handling

New error codes:

- `mineru_unavailable`
- `mineru_task_failed`
- `mineru_timeout`
- `mineru_invalid_result`
- `document_context_empty`

If the user cancels our extraction job while MinerU is running, our job is marked
cancelled and stops waiting. MinerU may continue server-side because the current
API does not expose a cancel endpoint. Concurrency limits and task timeouts are
therefore mandatory.

## Validation

Use the PI insulation film PDF as the primary acceptance case.

Validation checks:

1. MinerU parse produces non-empty `DocumentContext`.
2. Page inventory reports title/abstract, experimental, result, table, figure,
   caption, and supplementary signals.
3. `Papers` contains title, DOI, year, and journal when present in the PDF.
4. `Main_Data` contains composition, process, structure, and performance fields
   together in the same row.
5. `Main_Data` captures more core facts than the old parser, especially density,
   thermal conductivity, surface temperature, dielectric values, shrinkage,
   water contact angle, porosity, and control-sample comparisons.
6. Each generated main row has an `evidence_id`.
7. Evidence rows include `block_id`, page, bbox when available, and evidence text.
8. Strong mode with `mimo-v2.5` completes without Opus/GPT-specific assumptions.
9. Old 40-column export remains available only as compatibility output if still
   needed by existing workflows.

## Rollout

1. Add settings and MinerU client.
2. Add parse-run and document-block models plus startup schema repair.
3. Build MinerU result adapter and `DocumentContext`.
4. Refactor Stage 0 of V7 to consume `DocumentContext`.
5. Add source anchors to facts and evidence.
6. Refactor context-pack generation for metadata, samples, process/structure,
   tables/figures, and performance facts.
7. Add new workbook export shape.
8. Validate against PI insulation film PDF.
9. Disable legacy PDF parser fallback after MinerU results are accepted.

