# Adaptive Evidence Routing Implementation Plan

> **For agentic workers:** Execute this plan inline in the current task. Keep the existing MinerU Cloud, GPT-5.5, evidence audit, and Stage 2 fallback contracts intact.

**Goal:** Reduce unnecessary GPT-5.5 context and repeated calls while preserving recall, source grounding, and the existing extraction result schema.

**Architecture:** Add a conservative block-level evidence router inside the existing Holistic extractor. It will rank quantitative result blocks, build compact background context from high-signal experimental blocks, and skip only clearly empty performance sweeps. Existing deterministic table parsing, Stage 2 repair, fact merging, and quality gates remain authoritative.

**Tech Stack:** Python 3.11+, Pydantic settings, asyncio, pytest, existing MinerU/GPT-5.5 adapters.

---

### Task 1: Establish the routing contract

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `backend/tests/test_config.py`

- [ ] Add bounded settings for minimum result signal, maximum selected result blocks, and neighboring context blocks.
- [ ] Keep defaults conservative and document that zero/empty values retain the existing fallback behavior.
- [ ] Add configuration tests without reading or printing credentials.

### Task 2: Improve evidence block selection

**Files:**
- Modify: `backend/app/services/extractor_v7/holistic_extract.py`
- Test: `backend/tests/test_holistic_context.py`

- [ ] Extend `select_performance_context_chunks` with configurable score threshold, block cap, and neighbor count.
- [ ] Require quantitative or strong metric evidence for the primary result blocks while retaining same-page context neighbors.
- [ ] Preserve reading order and the existing no-signal fallback.
- [ ] Add a compact background-context selector that favors composition, process, treatment, and structure evidence instead of taking the first N experimental characters.
- [ ] Test table exclusion, low-signal suppression, page-local context retention, deterministic ordering, and max-character bounds.

### Task 3: Route Holistic calls conservatively

**Files:**
- Modify: `backend/app/services/extractor_v7/holistic_extract.py`
- Modify: `backend/app/services/extractor_v7/service.py`
- Test: `backend/tests/test_holistic_context.py`
- Test: `backend/tests/test_extraction_rules.py`

- [ ] Pass the new routing settings into Holistic extraction.
- [ ] Skip the performance sweep only when there are no sample IDs or no quantitative result signal; leave Stage 2 fallback enabled.
- [ ] Use compact background context with the same timeout and output contract.
- [ ] Keep deterministic table coverage and existing targeted repair behavior unchanged.
- [ ] Emit routing metadata through existing warnings/metrics-safe paths without exposing prompts or API keys.

### Task 4: Verify regression and runtime safety

**Files:**
- Modify: `backend/tests/test_holistic_context.py` if regression coverage needs expansion.
- No production schema changes.

- [ ] Run targeted routing tests and the full backend test suite.
- [ ] Run the existing gold-set evaluator and inspect precision/recall, evidence grounding, duplicate rate, and call/token metrics.
- [ ] Run a same-PDF A/B benchmark with parse artifacts reused.
- [ ] Check `git diff`, ignored artifacts, and secret patterns before reporting completion.
