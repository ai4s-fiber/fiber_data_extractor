# GPT-5.6 Channel Hardening Implementation Plan

> **For agentic workers:** Execute this plan inline in the current task. Preserve the MinerU Cloud parser, the existing strong-extraction schema, and all user-authored working-tree changes.

**Goal:** Select and enable the most reliable GPT-5.6 channel for production strong extraction while reducing avoidable retries and preserving evidence recall on the real paper corpus.

**Architecture:** Benchmark `gpt-5.6-luna`, `gpt-5.6-terra`, and `gpt-5.6-sol` through the existing MinerU Cloud plus holistic/Stage 2 pipeline on the same research papers and reused parse artifacts. Choose the default by evidence coverage and empty-result rate first, then update every user-facing and batch entry point. Add only bounded runtime safeguards: resumable per-stage telemetry, failure-stage retry isolation, and quality-triggered targeted repair where the current contracts already provide hooks.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy, Pydantic settings, asyncio, existing MinerU Cloud adapter, OpenAI-compatible LLM client, pytest, Ruff.

---

### Task 1: Establish a reproducible GPT-5.6 comparison

**Files:**
- Modify: `backend/scripts/benchmark/run_extraction_benchmark.py`
- Create: `backend/scripts/benchmark/summarize_model_comparison.py`
- Test: `backend/tests/test_benchmark_reporting.py`
- Create: `docs/benchmarks/gpt56-channel-selection.md`

- [x] Accept an explicit model name without changing the existing parser or database contracts.
- [x] Record per-paper call, token, latency, empty-result, evidence, QA, and candidate metrics while redacting credentials.
- [x] Summarize all three channels using identical paper selection and runtime settings; do not select a model from a single review article.

### Task 2: Switch the selected default consistently

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/models/project.py`
- Modify: `backend/scripts/benchmark/run_extraction_benchmark.py`
- Modify: `backend/scripts/ops/run_bulk_extraction.py`
- Modify: `frontend/src/pages/SettingsPage.tsx`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `README.md`
- Modify: tests that assert the default model

- [x] Set the default to the selected concrete channel identifier, not an unsupported bare alias.
- [x] Keep existing projects and explicitly configured models unchanged.
- [x] Update examples and tests without changing fixtures whose model value is intentional.

### Task 3: Harden long-running extraction

**Files:**
- Modify: `backend/app/services/extractor_v7/service.py`
- Modify: `backend/app/services/llm_client.py` only if required by an observed failure
- Modify: `backend/scripts/ops/run_bulk_extraction.py` only if required by an observed failure
- Test: affected backend tests

- [x] Retry only the failed LLM stage after a transient timeout, 429, or 5xx; never repeat successful MinerU parsing.
- [x] Preserve persistent progress and metrics across process interruption.
- [x] Keep concurrency bounded and expose enough telemetry to tune it from real runs.
- [x] Add targeted repair only when core facts are empty or evidence coverage is materially below the quality gate.
- [x] Add one bounded compact-context retry for an empty holistic sample catalog before atomic Stage 1 fallback.

### Task 4: Verify before publishing

**Files:**
- No unrelated file changes.

- [x] Run targeted tests, full backend tests, Ruff, compile checks, and `git diff --check`.
- [x] Re-run the selected model on a small real-paper sample using `E:\数据\创智的paper` and inspect raw evidence paths and quality metrics.
- [x] Review secret scans and ignored runtime artifacts; do not delete user data or print credentials.
