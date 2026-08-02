# AI4S Platform Template v0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a platform-importable AI4S chemical-fiber template v0.1, with a standalone validator and concise Windows import instructions, without changing extraction or database behavior.

**Architecture:** Treat the external platform JSON as a derived adapter artifact. Keep AI4S canonical paths and sparse evidence semantics authoritative, encode only platform structures confirmed by the downloaded reference JSON and screenshots, and leave `data` empty until the platform's record payload is known.

**Tech Stack:** JSON, Python 3 standard library, PowerShell usage instructions.

---

### Task 1: Define the import artifact

**Files:**
- Create: `platform_templates/ai4s-chemical-fiber-template-v0.1.json`

- [x] **Step 1: Define stable template sections**

Use `object.id = "object"`, ASCII section IDs, and unique Chinese labels. Preserve platform ordering with `_ord`, `_head`, and `_opt`.

- [x] **Step 2: Encode the sparse required-field policy**

Set only `数据记录键`, `投影版本`, `文献编号`, and `数据状态` as required. Keep scientific fields optional because literature does not report every field.

- [x] **Step 3: Encode repeated scientific facts**

Use `t=7` arrays with `t=9` container items for composition, process, structure, and performance facts. Preserve raw values, optional parsed scalar/range values, units, conditions, evidence, source locations, and confidence.

- [x] **Step 4: Keep external data disabled**

Emit:

```json
"data": []
```

Do not invent platform record encoding.

### Task 2: Add a standalone structural validator

**Files:**
- Create: `platform_templates/validate_platform_template.py`

- [x] **Step 1: Validate the root contract**

Require exactly one JSON object containing `template` and `data`; require `data` to be an empty list for v0.1.

- [x] **Step 2: Validate every block recursively**

Check `r` is boolean, `t` is an integer from 1 through 10, `stats` is a string, and each type uses the confirmed `misc` shape.

- [x] **Step 3: Validate ordering references**

Require `blocks._ord`, table `_head`, container `_ord`, and generator `_opt` to contain unique names that exactly match their sibling field definitions.

- [x] **Step 4: Validate safe required fields**

Require the four stable keys to be mandatory and reject any other top-level scientific field accidentally marked mandatory.

- [x] **Step 5: Run the validator**

Run:

```powershell
python platform_templates\validate_platform_template.py `
  platform_templates\ai4s-chemical-fiber-template-v0.1.json
```

Expected output:

```text
VALID: ai4s-chemical-fiber-template-v0.1.json
```

### Task 3: Document field mapping and import

**Files:**
- Create: `platform_templates/README.md`

- [x] **Step 1: Document the record grain**

State that one platform record represents one AI4S sample entity and that `数据记录键` is the idempotent external key.

- [x] **Step 2: Document exact platform actions**

Describe JSON parsing, file selection, preview checks, draft save, quantity-rule review, and a one-record canary before production use.

- [x] **Step 3: Document known limitations**

State that descriptions, platform data payload encoding, API writes, generator data semantics, and quantity-rule serialization remain unbound.

### Task 4: Verify scope and hand off

**Files:**
- Verify: `platform_templates/*`
- Verify: `docs/superpowers/plans/2026-07-27-platform-template-v01.md`

- [x] **Step 1: Check JSON parsing**

Run:

```powershell
Get-Content -Raw -Encoding UTF8 `
  platform_templates\ai4s-chemical-fiber-template-v0.1.json |
  ConvertFrom-Json | Out-Null
```

Expected: exit code `0`.

- [x] **Step 2: Run the standalone validator**

Run the command from Task 2 and require `VALID`.

- [x] **Step 3: Check whitespace and worktree scope**

Run:

```powershell
git diff --check
git status --short
```

Confirm no pre-existing extraction files were modified by this task.
