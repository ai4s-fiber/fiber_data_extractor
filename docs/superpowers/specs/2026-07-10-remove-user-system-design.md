# Remove User System Design

## Context

The application is now a single open workspace for literature extraction. It must not require login, user accounts, roles, project membership, or administrator pages. Project, paper, extraction, review, export, and LLM configuration workflows stay available because they are core to the data production flow.

This change must not alter the core extraction logic in `backend/app/services/extractor_v7/`, MinerU parsing, LLM prompting, grouping, validation, or workbook export behavior except where those modules consume removed user fields.

## Goals

- Remove authentication and user management completely.
- Remove all user and role concepts from backend APIs, frontend routes, database models, tests, scripts, and docs.
- Keep projects as the organizing boundary for papers, candidates, exports, cache keys, and LLM configuration.
- Keep all business pages open without permission gates.
- Preserve extraction job lifecycle, progress streaming, cancellation, review, export, and project-level LLM settings.
- Clean obvious repository noise that is unrelated to runtime behavior.

## Non-Goals

- Do not redesign the extraction algorithm.
- Do not split `extractor_v7/service.py` in this phase.
- Do not remove the project concept.
- Do not add a replacement access-control system.
- Do not depend on the local frontend `node_modules` state being valid.

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

## Testing

Backend:

- Existing extractor, validator, grouping, workbook, health, job, paper, candidate, and export tests should remain.
- Delete or rewrite auth/RBAC-specific tests.
- Add tests that open APIs no longer require authentication.
- Add tests that project/resource mismatch still returns `404`.
- Add migration/schema tests for the removed user columns where practical.

Frontend:

- Build should pass in a clean dependency install.
- Local build failures caused by stale server-pulled `node_modules` are environment issues, not acceptance failures.
- Route-level smoke checks should confirm the app opens directly without redirecting to login.

## Acceptance Criteria

- The app has no login screen, user menu, logout action, member page, user management page, profile page, role labels, or admin-only gates.
- Backend exposes no `/auth/*`, `/users/*`, or `/projects/{project_id}/members/*` routes.
- SQLAlchemy model registry has no `User` or `ProjectMember`.
- Business tables no longer contain user foreign keys.
- Paper upload, extraction start, progress stream, cancellation, review, export, and project LLM configuration still work.
- Backend tests pass.
- CI uses correct repository-root paths.
- Core extraction logic is not behaviorally refactored in this phase.

## Implementation Order

1. Repository hygiene: `.gitattributes`, CI path correction, and obvious generated/starter cleanup.
2. Backend model/schema migration: remove user tables and user foreign key fields.
3. Backend API cleanup: remove auth/users/members routes and replace permission dependencies with resource validation.
4. Frontend cleanup: remove auth store/routes/pages/menus and expose business pages directly.
5. Tests/scripts/docs: delete or rewrite user-system tests and update README/E2E scripts.
6. Verification: run backend tests and frontend build where dependencies allow.

