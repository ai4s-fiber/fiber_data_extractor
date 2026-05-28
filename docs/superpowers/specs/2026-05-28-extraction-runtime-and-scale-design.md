# Extraction Runtime And Scale Design

## Goal

Make the extraction workflow reliable enough for an initial group of student
users and structured enough to grow into a server deployment for a broader
audience.

The core workflow changes from "upload immediately starts extraction" to
"upload stores the PDF, then the user explicitly starts an extraction job with a
chosen mode." Every extraction run becomes a persisted job with visible mode,
progress, error details, and safe retry behavior.

## Context

The current backend already accepts `model_mode=auto|weak|strong` on the manual
paper extraction endpoint, but the frontend does not expose that choice. Upload
also starts extraction automatically with the default `auto` mode. Users cannot
see which logic is being used, and failed background tasks are not reliably
persisted beyond in-memory progress state.

LLM configuration testing is also too brittle. Some OpenAI-compatible providers
document a root URL such as `https://how88.top`, while the actual chat
completion endpoint is under `/v1/chat/completions`. The current test endpoint
uses the submitted base URL directly and can surface low-level JSON parse errors
such as `Expecting value`.

## Scope

- Upload PDF files without starting extraction automatically.
- Add explicit extraction mode selection in the document library.
- Persist every extraction attempt as an `extraction_jobs` row.
- Store requested mode, resolved mode, progress, status, timestamps, and error
  details.
- Prevent concurrent runs for the same paper.
- Add a bounded job runner that supports queued/running/failed/completed states.
- Keep the runner interface replaceable so Redis/Celery/RQ can be introduced
  later without changing frontend contracts.
- Improve LLM base URL normalization and connection diagnostics.
- Update frontend copy and actions so users know whether they are running
  `weak`, `strong`, or `auto`.
- Clean obsolete scripts and documents after the runtime flow is stable.

## Non-Goals

- Do not change the final candidate record schema as part of this runtime work.
- Do not tune extraction quality rules in this phase. Quality tuning will follow
  after reviewing the current extraction output.
- Do not introduce a distributed queue in the first implementation unless local
  bounded execution proves insufficient.
- Do not remove V6 or legacy files until references are checked and the runtime
  change is verified.

## User Workflow

### Upload

1. User uploads a PDF.
2. Backend saves the file and creates a `papers` row with `status='uploaded'`.
3. No extraction job starts automatically.
4. The document library shows the paper as "Uploaded" with an enabled "Extract"
   action.

### Extract

1. User clicks "Extract" on a paper.
2. Frontend opens a mode selection dialog:
   - `Strong`: multi-stage, higher quality, slower, higher cost.
   - `Weak`: faster and cheaper, lower quality.
   - `Auto`: backend resolves mode from project model configuration and records
     the resolved mode.
3. If the paper already has extraction results, the dialog warns that rerunning
   will replace the existing candidate rows, sample catalog, fact candidates,
   page inventory, and evidence rows for that paper.
4. Backend creates an extraction job and returns the job id.
5. Frontend displays job status, requested mode, resolved mode when available,
   current step, percent, and error details.

### Retry

Failed papers show "Retry extraction." Retry opens the same mode selection
dialog and creates a new job. Old failed job records remain for audit.

## Backend Design

### Job Model

Use `extraction_jobs` as the source of truth for active and historical runs.

Required fields:

- `id`
- `project_id`
- `paper_id`
- `created_by`
- `requested_mode`: `auto|weak|strong`
- `resolved_mode`: nullable, filled once `auto` is resolved
- `status`: `queued|running|completed|failed|cancelled`
- `step`: short machine value such as `starting`, `inventory`, `extracting`,
  `assigning`, `saving`, `completed`, `failed`
- `percent`: integer 0-100
- `error_code`: nullable short code
- `error_message`: nullable human-readable message
- `error_detail`: nullable diagnostic text or JSON
- `started_at`
- `finished_at`
- `created_at`
- `updated_at`

The `papers.status` field remains the paper-level state:

- `uploaded`
- `queued`
- `extracting`
- `review`
- `failed`
- `completed`

### API Contracts

Upload:

```http
POST /api/projects/{project_id}/papers
```

Creates the paper only. It does not enqueue extraction.

Start extraction:

```http
POST /api/projects/{project_id}/papers/{paper_id}/extract
{
  "model_mode": "strong"
}
```

Response:

```json
{
  "job_id": 123,
  "paper_id": 45,
  "requested_mode": "auto",
  "resolved_mode": null,
  "status": "queued"
}
```

Status:

```http
GET /api/projects/{project_id}/papers/{paper_id}/extraction-status
```

Returns the latest job plus paper state:

```json
{
  "paper_id": 45,
  "paper_status": "extracting",
  "job_id": 123,
  "requested_mode": "auto",
  "resolved_mode": "strong",
  "status": "running",
  "step": "extracting",
  "percent": 50,
  "error_code": null,
  "error_message": null
}
```

The existing endpoint can keep the same path for compatibility, but its data
should come from the persisted job, not only from in-memory progress.

### Concurrency

Initial deployment supports around 10-20 student users. Use a bounded local
runner with a small global concurrency limit, configurable by environment:

- `EXTRACTION_MAX_CONCURRENT_JOBS`, default `2`
- `EXTRACTION_JOB_POLL_INTERVAL_SECONDS`, default `2`

Rules:

- A single paper can have only one `queued` or `running` job.
- Different papers can run concurrently up to the global limit.
- Jobs above the limit remain `queued`.
- If the app restarts, jobs left as `running` are marked `failed` with
  `error_code='worker_interrupted'`. Users can retry explicitly. This avoids
  silently rerunning an expensive LLM extraction after a crash or deploy.

The runner should be hidden behind an interface:

```text
ExtractionJobBackend.enqueue(job_id)
ExtractionJobBackend.try_start_next()
ExtractionJobBackend.mark_progress(job_id, step, percent)
ExtractionJobBackend.mark_completed(job_id)
ExtractionJobBackend.mark_failed(job_id, error)
```

The first implementation can use in-process async tasks plus the database. A
future Redis/Celery implementation should replace only this backend, not the
API, UI, or extractor service.

## LLM Configuration Diagnostics

### Base URL Normalization

When testing or saving an OpenAI-compatible provider, normalize candidate URLs:

1. Submitted URL as-is.
2. Submitted URL plus `/v1` when it does not already end in `/v1`.
3. Optional provider-specific fallback candidates can be added later.

For each candidate, test:

```text
{candidate_base_url}/chat/completions
```

On success, save the working base URL, not necessarily the raw submitted value.

### Diagnostic Response

The test endpoint should return structured diagnostics:

```json
{
  "success": false,
  "working_base_url": null,
  "attempts": [
    {
      "base_url": "https://how88.top",
      "request_url": "https://how88.top/chat/completions",
      "http_status": 404,
      "json_response": false,
      "response_preview": "<html>..."
    },
    {
      "base_url": "https://how88.top/v1",
      "request_url": "https://how88.top/v1/chat/completions",
      "http_status": 200,
      "json_response": true
    }
  ],
  "message": "Connected using https://how88.top/v1"
}
```

Frontend should show the friendly message and optionally expose details in a
diagnostics drawer. Raw Python exceptions such as JSON parser errors should not
be the main user-facing message.

## Frontend Design

### Document Library

Actions by paper state:

- `uploaded`: `Extract`
- `queued`: disabled action, show queued state
- `extracting`: disabled action, show progress
- `failed`: `Retry extraction`, show error detail
- `review`: `Re-extract`, with confirmation
- `completed`: `Re-extract`, with confirmation

The table should include:

- paper status
- latest requested mode
- latest resolved mode
- latest job status
- latest error message when failed

### Extraction Dialog

The dialog asks for mode and shows cost/quality tradeoff. It also shows the
current project model and provider so users can detect mismatches before
starting.

Default mode can come from project settings later, but the user must still see
the selected mode before starting a run.

## Error Handling

Errors should be classified where possible:

- `llm_auth_failed`
- `llm_model_not_found`
- `llm_invalid_base_url`
- `llm_timeout`
- `llm_non_json_response`
- `pdf_parse_failed`
- `worker_interrupted`
- `unknown_error`

Each failed job must write:

- `job.status='failed'`
- `job.error_code`
- `job.error_message`
- `job.error_detail`
- `paper.status='failed'`

## Cleanup Plan

After the runtime changes pass verification:

1. Search references to legacy V6 services and scripts.
2. Remove obsolete hardcoded export scripts and stale report scripts.
3. Keep only one supported review workbook export command.
4. Move retained developer scripts under a clear `backend/scripts/` directory.
5. Remove temporary logs and SQLite sidecar files from the working tree.
6. Update docs to describe the current V7 runtime and extraction quality layers.

No legacy code should be removed until a reference scan confirms it is unused by
routes, tests, scripts, or packaging.

## Validation

Minimum verification before release:

- Upload a PDF and confirm no extraction starts.
- Start weak mode and confirm the job records `requested_mode='weak'`.
- Start strong mode and confirm the job records `requested_mode='strong'`.
- Start auto mode and confirm `resolved_mode` is stored.
- Force an LLM config failure and confirm `paper.status='failed'` and job error
  details persist after page refresh.
- Attempt two concurrent runs for the same paper and confirm the second is
  rejected or returns the active job.
- Start multiple papers and confirm no more than the configured concurrency
  limit run at once.
- Test `https://how88.top` and confirm diagnostics discover
  `https://how88.top/v1` when it is the working endpoint.

## Rollout

Phase 1 implements the persistent job model, explicit mode selection, upload
without auto extraction, and LLM diagnostics.

Phase 2 adds the bounded queue and stale job recovery.

Phase 3 cleans the project structure and updates legacy docs.

Phase 4 can replace the local runner with Redis/Celery/RQ if public deployment
requires multiple backend workers.
