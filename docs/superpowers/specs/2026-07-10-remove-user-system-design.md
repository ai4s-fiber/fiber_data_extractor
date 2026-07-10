# Open Workspace And Codebase Stabilization Design

## Context

The application is now a single open workspace for literature extraction. It must not require login, user accounts, roles, project membership, or administrator pages. Project, paper, extraction, review, export, and LLM configuration workflows stay available because they are core to the data production flow.

The broader refactor goal is not only to remove the user system. The repository was pulled from a server and contains deployment residue, local runtime artifacts, old scripts, starter frontend code, oversized service files, and compatibility layers that now make future development harder. This design covers both the open-workspace change and the cleanup/stabilization work needed before the project is pushed to a private GitHub repository.

This work must protect the core literature extraction behavior. MinerU parsing, LLM prompting, grouping, validation, and workbook export should be changed only through narrow, test-backed seams. The first implementation phase removes user-system coupling; later phases reduce service size, redundant code, and runtime fragility.

## Goals

- Remove authentication and user management completely.
- Remove all user and role concepts from backend APIs, frontend routes, database models, tests, scripts, and docs.
- Keep projects as the organizing boundary for papers, candidates, exports, cache keys, and LLM configuration.
- Keep all business pages open without permission gates.
- Preserve extraction job lifecycle, progress streaming, cancellation, review, export, and project-level LLM settings.
- Clean obvious repository noise that is unrelated to runtime behavior.
- Reduce redundant code and stale server/development artifacts before GitHub push.
- Improve runtime stability around schema management, background jobs, progress reporting, cancellation, and external-service failures.
- Improve maintainability and performance by splitting oversized modules into testable units after the user-system removal is stable.
- Establish clear verification baselines so extraction quality and exported data structure are not accidentally changed.

## Non-Goals

- Do not redesign the extraction algorithm.
- Do not remove the project concept.
- Do not add a replacement access-control system.
- Do not depend on the local frontend `node_modules` state being valid.
- Do not optimize by weakening validation, evidence tracking, or export completeness.
- Do not combine high-risk extraction-pipeline rewrites with the user-system removal in the same implementation step.

## Backend Design

### Removed Modules

Delete the authentication and user-management surface:

- `backend/app/api/auth.py`
- `backend/app/api/users.py`
- `backend/app/models/user.py`
- `backend/app/models/project_member.py`
- `backend/app/schemas/auth.py`
- `backend/app/services/session_store.py`

Remove the `auth` and `users` routers from `backend/app/main.py`.

### Model Changes

Remove user foreign keys and user identity fields:

- `Project.created_by`
- `Paper.uploaded_by`
- `ExtractionJob.created_by`
- `ExportJob.created_by`
- `CandidateRecord.reviewed_by`
- `ReviewLog.user_id`

Keep `ReviewLog` as an operation log tied to a candidate record. Its useful fields are project, candidate, action, old value, new value, and timestamp. It no longer records who performed the action.

Keep review status and comments on candidates:

- `review_status`
- `reviewer_comment`
- `updated_at`

Remove `reviewed_by` and `reviewed_by_name` from candidate schemas and API responses.

### API Changes

All business APIs become open. Replace auth dependencies with resource validation:

- Verify that a referenced project exists and is not archived when relevant.
- Verify that a paper, candidate, or export belongs to the requested project.
- Return `404` for missing resources and project mismatches.
- Do not return `401` or `403` for normal business APIs.

Project APIs:

- `GET /projects` returns all unarchived projects.
- `POST /projects` creates a project without `created_by`.
- `DELETE /projects/{project_id}` deletes or archives the project and related data without member cleanup.
- Remove all `/projects/{project_id}/members` endpoints.
- LLM config endpoints remain and become open project configuration endpoints.

Paper APIs:

- Upload creates `Paper` without `uploaded_by`.
- Extraction creates `ExtractionJob` without `created_by`.
- Progress, report, download, edit, delete, cancel, and summaries remain open but validate project ownership.

Candidate APIs:

- List, count, create, detail, update, delete, batch delete, review, and batch approve remain open.
- Update/review actions update `review_status`, `reviewer_comment`, timestamps, and `ReviewLog` without user IDs.

Export APIs:

- Create/list/download/delete remain open.
- `ExportJob` is created without `created_by`.

### Schema And Migration

The repository currently has an Alembic baseline with empty upgrade/downgrade and a runtime schema repair module. This phase will add a clear migration for removing user-system tables, constraints, and columns.

For existing SQLite and PostgreSQL databases:

- Drop foreign keys or rebuild tables where the database requires table rebuilds.
- Drop `users` and `project_members`.
- Drop removed columns from business tables.
- Preserve projects, papers, candidates, jobs, evidence, document parse data, exports, and review logs.

`schema_repair.py` must stop creating or repairing user-related tables and columns. It may continue to maintain extraction-specific compatibility only.

`init_db.py` must stop creating a default superadmin. It should only ensure schema readiness if still needed.

## Frontend Design

The frontend opens directly into the workspace.

Delete these user-system pages and state:

- `LoginPage.tsx`
- `MembersPage.tsx`
- `UserManagementPage.tsx`
- `ProfilePage.tsx`
- `stores/auth.ts`

Remove from routing:

- `/login`
- `/members`
- `/users`
- `/profile`
- `ProtectedRoute`
- `PlatformAdminRoute`
- `AuthProvider`

Navigation becomes business-only:

- Projects
- Papers
- Review
- Export
- Project Config

There are no administrator pages and no administrator roles. Existing `SettingsPage` is retained only as project configuration and must be renamed or relabeled so it is not presented as admin settings. Existing `ExportPage` is retained as data export, not an admin-only page.

`api/client.ts` removes JWT injection and login redirects. `ExtractionContext.tsx` removes manual `Authorization` headers for progress streams.

Remove login CSS and unused Vite starter files:

- `frontend/src/main.ts`
- `frontend/src/counter.ts`
- `frontend/src/style.css`
- unused Vite/TypeScript starter assets if no longer referenced

## Repository Cleanup

- Add `.gitattributes` to keep source files on LF and reduce Windows line-ending churn.
- Fix GitHub Actions working directories to match the actual repository root.
- Keep generated/runtime data ignored: uploads, exports, parse artifacts, local databases, logs, `node_modules`, and frontend `dist`.
- Remove or rewrite user-system operational scripts:
  - `reset_admin_user.py`
  - `migrate_user_roles.py`
  - `test_multi_user_flow.py`
- Update `e2e_extraction_flow.py` to skip login and call open APIs.
- Update README role descriptions to describe the open local workspace.

## Codebase Stabilization And Efficiency Design

This section covers the broader cleanup and optimization work that follows or accompanies the open-workspace conversion. It is part of the same refactor roadmap, but risky extraction-service changes must be staged behind tests.

### Backend Structure Cleanup

`backend/app/services/extractor_v7/service.py` is too large and mixes orchestration, model selection, LLM calls, post-processing, persistence, reporting, and progress emission. After user-system removal is stable, split it into narrower modules without changing behavior:

- `pipeline.py`: high-level extraction stage orchestration.
- `runtime.py`: model-mode resolution, job context, cancellation checks, progress helpers.
- `persistence.py`: candidate, evidence, fact, sample catalog, and report persistence.
- `vision.py`: optional vision enhancement handling.
- `report_building.py`: report file generation and summary payload preparation.

The first split should be mechanical: move functions and keep public behavior the same. Functional tuning comes only after parity tests pass.

### Runtime Stability

Replace silent failures and startup side effects with explicit behavior:

- Log background poller exceptions instead of swallowing them.
- Keep extraction queue recovery, cancellation, and progress updates observable.
- Stop relying on runtime schema repair for normal migrations; keep it only as temporary compatibility code.
- Avoid creating directories or mutating schema at module import time where possible.
- Ensure MinerU and LLM failures produce stable error codes and user-facing messages without leaving jobs stuck in `running`.
- Keep Redis optional, but make memory fallback behavior explicit and tested.

### Database And Migration Cleanup

The current Alembic baseline is empty while `schema_repair.py` performs real schema changes at startup. Stabilization requires:

- A real migration path for the open-workspace schema.
- Removal of user-system table/column repair code.
- A documented approach for existing local/server databases.
- Index review for frequently queried tables: papers by project/status, jobs by paper/status, candidates by project/paper/review status, evidence by candidate/job.

### API Cleanup

After auth removal, API handlers should be simplified:

- Replace permission checks with resource ownership validation helpers.
- Move repeated project/paper/candidate lookup logic into small reusable functions.
- Keep API responses stable for frontend consumers unless fields are explicitly removed by this design.
- Remove unused legacy role constants and role-related schemas.
- Keep LLM configuration project-scoped and open.

### Frontend Cleanup

Beyond removing auth UI, simplify the frontend:

- Remove route guards and role-based menu conditionals.
- Rename settings to project configuration.
- Delete starter files and unused assets.
- Remove duplicate or dead CSS for login/starter pages.
- Extract large page logic from `PapersPage.tsx` and `ReviewPage.tsx` into hooks/components after the open-workspace build is stable.
- Keep UI behavior focused on repeated operational workflows: upload, extract, monitor progress, review, export, configure project.

### Operational Cleanup

Before pushing to GitHub:

- Keep server-pulled runtime data local and ignored.
- Remove obsolete scripts that reset users, migrate roles, or test multi-user flows.
- Move real diagnostic scripts under a documented `backend/scripts/ops/` convention.
- Redact or template deployment-specific IPs, domains, and secrets.
- Add `.env.example` for required runtime configuration without secrets.

### Performance Guardrails

Optimization should focus on reducing avoidable work rather than changing extraction semantics:

- Avoid repeated database queries for the same project/paper/candidate inside request handlers.
- Keep candidate/export list endpoints paginable or filterable where table size can grow.
- Avoid blocking event-loop work in API handlers when parsing/exporting can be isolated.
- Keep LLM concurrency bounded by existing settings.
- Do not remove validation stages merely to shorten runtime.
- Measure extraction runtime by stage before changing pipeline behavior.

### Extraction Safety Baseline

Before and after the service split, compare:

- Candidate record count for a known sample PDF.
- Core fields in exported workbook.
- Review statuses and validation comments.
- Evidence item linkage to source paper/job/candidate.
- Job progress and final status.
- Error code behavior for MinerU/LLM failure paths.

The goal is structural cleanup first, then measured performance work.

## Testing

Backend:

- Existing extractor, validator, grouping, workbook, health, job, paper, candidate, and export tests should remain.
- Delete or rewrite auth/RBAC-specific tests.
- Add tests that open APIs no longer require authentication.
- Add tests that project/resource mismatch still returns `404`.
- Add migration/schema tests for the removed user columns where practical.
- Add regression tests around job recovery, cancellation, and progress updates.
- Add behavior-preserving tests before splitting `extractor_v7/service.py`.
- Add focused tests for any extracted persistence/reporting helpers.

Frontend:

- Build should pass in a clean dependency install.
- Local build failures caused by stale server-pulled `node_modules` are environment issues, not acceptance failures.
- Route-level smoke checks should confirm the app opens directly without redirecting to login.
- Navigation should contain only business pages.
- Project configuration and export pages should not display administrator language.

## Acceptance Criteria

- The app has no login screen, user menu, logout action, member page, user management page, profile page, role labels, or admin-only gates.
- Backend exposes no `/auth/*`, `/users/*`, or `/projects/{project_id}/members/*` routes.
- SQLAlchemy model registry has no `User` or `ProjectMember`.
- Business tables no longer contain user foreign keys.
- Paper upload, extraction start, progress stream, cancellation, review, export, and project LLM configuration still work.
- Backend tests pass.
- CI uses correct repository-root paths.
- Core extraction logic is not behaviorally refactored in this phase.
- Repository no longer tracks obvious starter files, user-system scripts, generated runtime files, or deployment-specific secret-bearing templates.
- Runtime schema changes are represented by migrations instead of hidden user-system repair code.
- Background job failures are logged or surfaced instead of silently swallowed.
- Oversized extraction service splitting has a test-backed follow-up plan and does not change output semantics without measured evidence.

## Implementation Order

1. Baseline and repository hygiene: `.gitattributes`, CI path correction, ignore/runtime artifact review, and obvious starter cleanup.
2. Backend user-system removal: models, schemas, migrations, `init_db.py`, `schema_repair.py`, auth/users/members routes, and resource validation helpers.
3. Frontend open-workspace conversion: remove auth store/routes/pages/menus, rename project configuration, and expose business pages directly.
4. Tests/scripts/docs: delete or rewrite user-system tests and scripts, update README and E2E scripts.
5. Verification: run backend tests and frontend build where dependencies allow.
6. Stability hardening: logging for queue poller failures, clearer Redis fallback behavior, stuck-job recovery checks, and migration cleanup.
7. Service decomposition: split `extractor_v7/service.py` into orchestration/runtime/persistence/reporting units using behavior-preserving tests.
8. Measured performance work: profile extraction stages, reduce repeated DB work, and tune only after output parity is proven.
