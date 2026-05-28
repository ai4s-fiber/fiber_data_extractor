# Extraction Stability Design

## Goal

Make the upload→extract→review flow reliable enough for daily use. Fix the
three root causes of instability: (1) progress state is lost on page
navigation/refresh, (2) stalled LLM calls hang forever with no timeout or
cancel, and (3) progress granularity is too coarse for users to know what is
happening.

## Scope

- Server-Sent Events (SSE) endpoint for real-time extraction progress.
- Job cancellation API (cancel queued, signal running, handle cleanly).
- LLM-level timeout (300 s) and pipeline-level watchdog (30 min).
- ExtractionContext at App layer so progress survives route changes.
- Inline progress bar in the paper table with expandable detail.
- Recover from page refresh via REST fallback + SSE reconnection.

## Non-Goals

- Do not add a re-extraction diff/comparison page in this phase. That is
  scoped separately.
- Do not change the extraction quality pipeline itself.
- Do not introduce WebSocket or polling-based real-time progress.

---

## Backend Design

### SSE Progress Stream

```
GET /api/projects/{project_id}/papers/{paper_id}/extraction-progress-stream

Response: text/event-stream

event: progress
data: {"step":"extracting","percent":45,"message":"Stage 2: 正在提取事实 (5/8 segments)","timestamp":"..."}

event: error
data: {"error_code":"llm_timeout","error_message":"LLM 请求超时，已重试 2 次"}

event: cancelled
data: {"message":"抽取已被用户取消","job_id":123}

event: done
data: {"job_id":123,"status":"completed","candidate_count":58}
```

**Implementation notes:**

- Each active job gets an `asyncio.Queue(maxsize=64)`. When the extractor's
  `progress_callback` fires, it writes a dict to the queue. If the queue is
  full, drop the oldest message.
- The SSE endpoint reads from the queue in a loop and yields SSE-formatted
  bytes. Connection close or cancel event breaks the loop.
- When a job completes or fails, a final `done` or `error` event is pushed,
  then the queue is closed.
- If the job is already finished when the SSE connection is established,
  return a single `done` or `error` event and close the stream immediately.

**Progress message granularity** (callbacks inserted at each stage boundary):

| Step | Message Example |
|------|----------------|
| pdf | "正在读取 PDF 文本 (12 页)..." |
| inventory | "正在分析页面结构..." |
| catalog | "Stage 1: 正在识别样品..." |
| extracting | "Stage 2: 正在提取事实 3/8 segments" |
| assigning | "Stage 3: LLM 正在分配样品..." |
| saving | "Stage 4: 正在生成候选记录..." |

### Cancel Endpoint

```
POST /api/projects/{project_id}/papers/{paper_id}/extract/cancel
```

**Logic:**

- **Job status = `queued`**: set `job.status = "cancelled"`, `paper.status = "uploaded"`. Runner's `try_start_next` skips non-queued jobs.
- **Job status = `running`**: set `job.cancel_requested_at = now()`. The
  extractor checks `cancel_requested_at` at each stage boundary (before each
  LLM call) via `await _check_cancelled(job_id)`. If set, raise
  `ExtractionCancelled`. The runner catches it and calls
  `mark_failed(exc, error_code="cancelled_by_user")`.
- Push `event: cancelled` to the job's SSE queue so connected frontends
  receive the notification immediately.

**New fields on ExtractionJob:**

- `cancel_requested_at` (DATETIME, nullable)

### Timeout and Watchdog

Two-layer protection, both handled in `extraction_jobs._run_job`:

1. **LLM-level timeout**: The OpenAI-compatible client created inside
   `extractor_v7` uses `httpx.Timeout(connect=30, read=300, write=30,
   pool=10)`. Read timeout covers the full LLM response window.
2. **Pipeline watchdog**: `_run_job` wraps the extractor call in
   `asyncio.wait_for(..., timeout=1800)` (30 minutes). If the pipeline
   exceeds this, `asyncio.TimeoutError` is caught and classified as
   `unknown_error` with message "抽取超时（超过 30 分钟）".

### Cancel Check in Extractor

Add a helper to `extractor_v7.py`:

```python
@staticmethod
async def _check_cancelled(db: AsyncSession, job_id: int | None) -> None:
    if job_id is None:
        return
    job = await db.get(ExtractionJob, job_id)
    if job and job.cancel_requested_at is not None:
        raise ExtractionCancelled("用户取消了抽取任务")
```

Insert `await self._check_cancelled(db, job_id)` calls at:
- After `run_full_pipeline_for_paper` starts, before each stage call
- After each LLM call returns (before processing results)
- After each batch of DB writes

### Error Classification (add to existing)

- `cancelled_by_user` — user clicked cancel while job was running
- `worker_interrupted` — already exists for restart recovery

---

## Frontend Design

### ExtractionContext (Mount at App Layer)

```typescript
interface ExtractionContextValue {
  state: ExtractionState;
  startExtraction(paperId: number, mode: string): Promise<{ jobId: number }>;
  cancelExtraction(paperId: number): Promise<void>;
  subscribe(paperId: number): void;
  unsubscribe(): void;
}

interface ExtractionState {
  paperId: number | null;
  status: 'idle' | 'connecting' | 'streaming' | 'done' | 'error' | 'cancelled';
  step: string;
  percent: number;
  message: string;
  error: { code: string; message: string } | null;
  result: { candidateCount: number; jobId: number } | null;
}
```

**Key behaviors:**

- SSE connection is opened by `subscribe(paperId)`. Closing is done by
  `unsubscribe()` or when a terminal event (`done`, `error`, `cancelled`)
  arrives.
- Component unmount (route change) does NOT call `unsubscribe`. The SSE
  connection stays alive. On re-mount, component calls `subscribe(paperId)`
  to re-attach listener callbacks.
- On page refresh: context is lost, but `PapersPage` detects active jobs
  via REST `/extraction-status` polling (every 5 seconds) and offers
  "reconnect" if there is an active SSE-pushable job.
- `startExtraction` POSTs to `/extract`, then immediately calls
  `subscribe(paperId)` to open the SSE stream.

### Paper Table Row — Inline Progress

Each paper row in the table shows:

```
[上传] [文件名]  [weak] [▓▓▓▓▓░░░░░ 45%] [取消]
                        Stage 2: 提取事实
```

- Progress bar is an Ant Design `<Progress percent={...} />` component,
  mini size, inline in the table cell.
- Hover on the progress bar shows tooltip with full message + elapsed time.
- Cancel button (`<Button danger size="small">取消</Button>`) visible only
  when job is `queued` or `running`.
- Completed jobs show a green checkmark instead of the progress bar.

### Page Refresh Recovery Flow

1. User refreshes page → SSE connection lost.
2. `PapersPage` mounts → `useEffect` fetches `/papers` list.
3. Each paper has `latest_job_status`. If any paper has `queued` or
   `running`, the row shows "reconnecting..." state.
4. `useEffect` calls `context.subscribe(paperId)` for the first active job
   found, which opens a new SSE connection.
5. SSE stream picks up from current progress (the queue may have lost older
   messages, but the next `progress_callback` will push a fresh one).

---

## Database Changes

Add to `extraction_jobs`:

- `cancel_requested_at` (DATETIME, nullable)

`schema_repair.py` must add this column on startup.

---

## API Summary

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `.../extraction-progress-stream` | SSE stream for job progress |
| `POST` | `.../extract/cancel` | Cancel queued or running job |
| `POST` | `.../extract` | Already exists |
| `GET` | `.../extraction-status` | Already exists (REST fallback) |

---

## Validation

Before marking this phase complete:

1. Upload a PDF and start weak extraction → SSE stream shows progress
   messages progressing from "pdf" to "done".
2. Start extraction, navigate away to Settings page, navigate back →
   progress bar is still updating.
3. Refresh page during extraction → paper row shows reconnecting, then
   restores progress.
4. Click Cancel while job is running → job status becomes "cancelled",
   paper status becomes "uploaded", event sent to frontend.
5. Force a 30+ second hang (simulate by pausing LLM) → watchdog fires,
   job marked failed with timeout error.
6. Start a second extraction for a paper that already has an active job →
   returns the active job without creating a duplicate.

---

## Rollout

All work goes in a single phase. No incremental rollout needed since this
is a stability fix on top of the existing runtime.

1. Backend: SSE endpoint, cancel endpoint, timeout, cancel checks in
   extractor.
2. Frontend: ExtractionContext, inline progress, cancel button, page-refresh
   recovery.
3. DB: cancel_requested_at column + schema repair.
4. Integration test the validation checklist above.
