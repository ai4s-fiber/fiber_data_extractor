# Open Workspace Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the app into an open local literature-extraction workspace, remove the user/admin/RBAC system completely, clean redundant repository/runtime code, and improve stability and efficiency without changing extraction output semantics.

**Architecture:** Business APIs become open and enforce only project/resource existence and ownership. The extraction pipeline is protected by regression tests before service decomposition; cleanup and performance work are staged so repository hygiene, API simplification, runtime hardening, mechanical service splitting, and measured tuning can be verified independently.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, SQLite/PostgreSQL, Redis-optional queues/pubsub/cache, pytest, React, Vite, Ant Design, axios.

---

## File Structure

- Modify: `.gitattributes` to normalize source line endings.
- Modify: `.gitignore` to ignore pulled runtime data, parse artifacts, generated exports, local DBs, logs, `node_modules`, and frontend build output.
- Modify: `.github/workflows/ci.yml` to run from `backend` and `frontend` because this repository root is already `fiber_data_extractor_v6`.
- Modify: `README.md` to describe an open local/private workspace, not admin/member roles.
- Modify: `backend/requirements.txt` to remove auth-only dependencies after backend auth removal.
- Modify: `backend/app/core/config.py` to remove JWT/session settings and keep LLM/MinerU/Redis/extraction settings.
- Modify: `backend/app/core/deps.py` into resource-access helpers only.
- Modify: `backend/app/core/redis_client.py` to remove session wording and make optional Redis fallback explicit.
- Modify: `backend/app/core/schema_repair.py` to remove user/member repair and keep only temporary extraction compatibility repair.
- Modify: `backend/app/init_db.py` to stop creating default superadmin.
- Modify: `backend/app/main.py` to stop registering auth/users routers.
- Delete: `backend/app/api/auth.py`, `backend/app/api/users.py`.
- Modify: `backend/app/api/projects.py`, `backend/app/api/papers.py`, `backend/app/api/candidates.py`, `backend/app/api/exports.py` to remove auth dependencies and use resource helpers.
- Delete: `backend/app/models/user.py`, `backend/app/models/project_member.py`.
- Modify: `backend/app/models/__init__.py`, `backend/app/models/project.py`, `backend/app/models/paper.py`, `backend/app/models/extraction_job.py`, `backend/app/models/export_job.py`, `backend/app/models/candidate_record.py`, `backend/app/models/review_log.py`.
- Delete: `backend/app/schemas/auth.py`.
- Modify: `backend/app/schemas/project.py`, `backend/app/schemas/paper.py`, `backend/app/schemas/candidate.py`, `backend/app/schemas/export.py`.
- Delete: `backend/app/services/session_store.py`.
- Modify: `backend/alembic/` to add a real open-workspace migration that drops user tables and user foreign-key columns while preserving extraction data.
- Modify: `backend/scripts/ops/e2e_extraction_flow.py` to remove login/logout and call open APIs.
- Delete: `backend/scripts/ops/reset_admin_user.py`, `backend/scripts/ops/migrate_user_roles.py`, `backend/scripts/ops/test_multi_user_flow.py`.
- Modify: `frontend/src/App.tsx` to remove auth providers, protected routes, login/admin routes, and profile/member/user pages.
- Modify: `frontend/src/api/client.ts` to remove JWT injection and login redirect handling.
- Modify: `frontend/src/contexts/ExtractionContext.tsx` to remove `Authorization` from SSE requests.
- Modify: `frontend/src/pages/WorkspaceLayout.tsx` to remove user menu/logout/member/admin navigation.
- Modify: `frontend/src/pages/ProjectsPage.tsx`, `frontend/src/pages/PapersPage.tsx`, `frontend/src/pages/ReviewPage.tsx`, `frontend/src/pages/ExportPage.tsx`, `frontend/src/pages/SettingsPage.tsx` to remove role gates and admin language.
- Delete: `frontend/src/pages/LoginPage.tsx`, `frontend/src/pages/MembersPage.tsx`, `frontend/src/pages/UserManagementPage.tsx`, `frontend/src/pages/ProfilePage.tsx`, `frontend/src/stores/auth.ts`.
- Delete: `frontend/src/main.ts`, `frontend/src/counter.ts`, `frontend/src/style.css`, `frontend/src/assets/vite.svg`, `frontend/public/vite.svg` when no references remain.
- Modify: `frontend/src/index.css` to remove login/starter CSS and keep workspace CSS.
- Create/modify tests under `backend/tests/` for open APIs, resource mismatch `404`, schema cleanup, job cancellation/progress stability, and extraction-output parity.
- Modify: `backend/app/services/extraction_jobs.py`, `backend/app/services/progress_bus.py`, `backend/app/services/job_cancellation.py`, `backend/app/services/document_context.py`, `backend/app/services/mineru_client.py` only for stability hardening that preserves behavior.
- Split after parity tests: `backend/app/services/extractor_v7/service.py` into `pipeline.py`, `runtime.py`, `persistence.py`, `vision.py`, and `report_building.py` while keeping `V7ExtractorService.extract_paper` public behavior unchanged.

---

### Task 1: Baseline And Repository Hygiene

**Files:**
- Create: `.gitattributes`
- Modify: `.gitignore`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Delete tracked runtime logs if tracked: `backend_server_8000.log`, `backend_server_8000.err.log`, `frontend_vite_5173.log`, `frontend_vite_5173.err.log`

- [ ] **Step 1: Record current working tree without reverting user changes**

Run:

```powershell
git status --short
git diff --stat
```

Expected: output may be dirty. Treat unrelated existing changes as user-owned and do not revert them.

- [ ] **Step 2: Add line-ending normalization**

Write `.gitattributes`:

```gitattributes
* text=auto eol=lf

*.bat text eol=crlf
*.cmd text eol=crlf
*.ps1 text eol=crlf
*.png binary
*.jpg binary
*.jpeg binary
*.gif binary
*.ico binary
*.pdf binary
*.xlsx binary
*.docx binary
```

- [ ] **Step 3: Tighten runtime ignores**

Update `.gitignore` so it contains these project rules:

```gitignore
# Runtime data
backend/uploads/
backend/exports/
backend/parse_artifacts/
backend/reports/
backend/*.db
backend/*.sqlite
backend/*.sqlite3
*.log

# Build and dependency output
node_modules/
frontend/node_modules/
frontend/dist/
backend/.pytest_cache/
backend/htmlcov/
.coverage

# Environment
.env
.env.*
!.env.example
```

- [ ] **Step 4: Fix GitHub Actions working directories**

Replace `.github/workflows/ci.yml` working directories:

```yaml
defaults:
  run:
    working-directory: backend
```

and:

```yaml
defaults:
  run:
    working-directory: frontend
```

Expected: no `fiber_data_extractor_v6/backend` or `fiber_data_extractor_v6/frontend` remains in the workflow.

- [ ] **Step 5: Remove tracked server-pulled logs from the repository**

Delete these tracked files if present:

```text
backend_server_8000.log
backend_server_8000.err.log
frontend_vite_5173.log
frontend_vite_5173.err.log
```

Expected: `.gitignore` keeps future log files local.

- [ ] **Step 6: Update README role wording**

Replace the role table with this open-workspace description:

```markdown
## Usage Model

This project runs as an open local/private literature extraction workspace. It has no login screen, no user accounts, no member management, and no administrator-only pages. Anyone who can access the running service can manage projects, upload papers, start extraction, review candidates, export workbooks, and configure project-level LLM settings.
```

- [ ] **Step 7: Verify hygiene scan**

Run:

```powershell
rg -n "admin|管理员|login|logout|member|成员|user management|用户管理|superadmin|RBAC" README.md .github .gitignore .gitattributes
```

Expected: no role/login/admin description remains in docs or CI. Mentions inside ignored runtime logs are gone because the logs are no longer tracked.

- [ ] **Step 8: Commit hygiene changes**

Run:

```powershell
git add .gitattributes .gitignore .github/workflows/ci.yml README.md
git add -u backend_server_8000.log backend_server_8000.err.log frontend_vite_5173.log frontend_vite_5173.err.log
git commit -m "chore: clean repository baseline"
```

Expected: commit contains only hygiene/doc/CI/log cleanup.

---

### Task 2: Backend Resource Access Helpers

**Files:**
- Modify: `backend/app/core/deps.py`
- Test: `backend/tests/test_open_resource_access.py`

- [ ] **Step 1: Write resource-helper tests**

Create `backend/tests/test_open_resource_access.py`:

```python
import pytest
from fastapi import HTTPException

from app.core.deps import ensure_same_project


def test_ensure_same_project_allows_matching_project():
    ensure_same_project(1, 1, "paper")


def test_ensure_same_project_raises_404_for_mismatch():
    with pytest.raises(HTTPException) as exc:
        ensure_same_project(1, 2, "paper")
    assert exc.value.status_code == 404
    assert "paper" in str(exc.value.detail)
```

- [ ] **Step 2: Run helper tests and verify current failure**

Run:

```powershell
cd backend
pytest tests/test_open_resource_access.py -q
```

Expected: FAIL because `ensure_same_project` does not exist yet.

- [ ] **Step 3: Replace auth dependencies with resource helpers**

Rewrite `backend/app/core/deps.py` around these helpers:

```python
"""FastAPI dependencies for open workspace resource access."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate_record import CandidateRecord
from app.models.export_job import ExportJob
from app.models.paper import Paper
from app.models.project import Project


def not_found(resource: str = "resource") -> HTTPException:
    return HTTPException(status_code=404, detail=f"{resource}不存在")


def ensure_same_project(expected_project_id: int, actual_project_id: int | None, resource: str) -> None:
    if actual_project_id != expected_project_id:
        raise not_found(resource)


async def get_project_or_404(db: AsyncSession, project_id: int, *, include_archived: bool = False) -> Project:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise not_found("项目")
    if not include_archived and getattr(project, "archived", False):
        raise not_found("项目")
    return project


async def get_paper_or_404(db: AsyncSession, project_id: int, paper_id: int) -> Paper:
    result = await db.execute(select(Paper).where(Paper.id == paper_id))
    paper = result.scalar_one_or_none()
    if paper is None:
        raise not_found("文献")
    ensure_same_project(project_id, paper.project_id, "文献")
    return paper


async def get_candidate_or_404(db: AsyncSession, project_id: int, candidate_id: int) -> CandidateRecord:
    result = await db.execute(select(CandidateRecord).where(CandidateRecord.id == candidate_id))
    candidate = result.scalar_one_or_none()
    if candidate is None:
        raise not_found("候选记录")
    ensure_same_project(project_id, candidate.project_id, "候选记录")
    return candidate


async def get_export_or_404(db: AsyncSession, project_id: int, export_id: int) -> ExportJob:
    result = await db.execute(select(ExportJob).where(ExportJob.id == export_id))
    export = result.scalar_one_or_none()
    if export is None:
        raise not_found("导出任务")
    ensure_same_project(project_id, export.project_id, "导出任务")
    return export
```

- [ ] **Step 4: Run helper tests**

Run:

```powershell
cd backend
pytest tests/test_open_resource_access.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit helper change**

Run:

```powershell
git add backend/app/core/deps.py backend/tests/test_open_resource_access.py
git commit -m "refactor: replace auth deps with resource access helpers"
```

---

### Task 3: Backend Model, Schema, And Migration Cleanup

**Files:**
- Delete: `backend/app/models/user.py`
- Delete: `backend/app/models/project_member.py`
- Delete: `backend/app/schemas/auth.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/project.py`
- Modify: `backend/app/models/paper.py`
- Modify: `backend/app/models/extraction_job.py`
- Modify: `backend/app/models/export_job.py`
- Modify: `backend/app/models/candidate_record.py`
- Modify: `backend/app/models/review_log.py`
- Modify: `backend/app/schemas/project.py`
- Modify: `backend/app/schemas/paper.py`
- Modify: `backend/app/schemas/candidate.py`
- Modify: `backend/app/schemas/export.py`
- Create: `backend/alembic/versions/20260710_open_workspace.py`
- Test: `backend/tests/test_open_workspace_models.py`

- [ ] **Step 1: Write model registry regression test**

Create `backend/tests/test_open_workspace_models.py`:

```python
from app.models import Base
from app.models.candidate_record import CandidateRecord
from app.models.extraction_job import ExtractionJob
from app.models.export_job import ExportJob
from app.models.paper import Paper
from app.models.project import Project
from app.models.review_log import ReviewLog


def column_names(model) -> set[str]:
    return {column.name for column in model.__table__.columns}


def test_user_tables_removed_from_metadata():
    assert "users" not in Base.metadata.tables
    assert "project_members" not in Base.metadata.tables


def test_user_foreign_key_columns_removed():
    assert "created_by" not in column_names(Project)
    assert "uploaded_by" not in column_names(Paper)
    assert "created_by" not in column_names(ExtractionJob)
    assert "created_by" not in column_names(ExportJob)
    assert "assigned_to" not in column_names(CandidateRecord)
    assert "reviewed_by" not in column_names(CandidateRecord)
    assert "user_id" not in column_names(ReviewLog)
```

- [ ] **Step 2: Run model test and verify current failure**

Run:

```powershell
cd backend
pytest tests/test_open_workspace_models.py -q
```

Expected: FAIL while user models/columns remain.

- [ ] **Step 3: Remove user model imports**

Update `backend/app/models/__init__.py` to import only runtime models:

```python
from app.models.base import Base
from app.models.project import Project
from app.models.paper import Paper
from app.models.extraction_job import ExtractionJob
from app.models.candidate_record import CandidateRecord
from app.models.evidence_item import EvidenceItem
from app.models.fact_candidate import FactCandidate
from app.models.review_log import ReviewLog
from app.models.export_job import ExportJob
from app.models.page_inventory import PageInventory
from app.models.sample_catalog import SampleCatalog

try:
    from app.models.document_parse import DocumentParseRun, DocumentBlock
except ImportError:
    DocumentParseRun = None
    DocumentBlock = None
```

- [ ] **Step 4: Remove user columns from models**

Apply these exact model changes:

```text
Project: remove created_by
Paper: remove uploaded_by
ExtractionJob: remove created_by
ExportJob: remove created_by
CandidateRecord: remove assigned_to and reviewed_by, keep reviewed_at
ReviewLog: remove user_id, keep project_id, candidate_record_id, action, old_value, new_value, created_at
```

Expected: model fields still preserve project/paper/job/candidate/export/review relationships.

- [ ] **Step 5: Remove user fields from Pydantic schemas**

Remove these response/request fields where present:

```text
created_by
uploaded_by
reviewed_by
reviewed_by_name
assigned_to
user_id
user_name
user_username
user_email
system_role
role
member
```

Expected: schema validation no longer references user modules or role constants.

- [ ] **Step 6: Add Alembic migration for open workspace schema**

Create `backend/alembic/versions/20260710_open_workspace.py`:

```python
"""open workspace remove user system

Revision ID: 20260710_open_workspace
Revises:
Create Date: 2026-07-10
"""

from alembic import op
import sqlalchemy as sa

revision = "20260710_open_workspace"
down_revision = None
branch_labels = None
depends_on = None


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name in columns:
        op.drop_column(table_name, column_name)


def _drop_table_if_exists(table_name: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name in inspector.get_table_names():
        op.drop_table(table_name)


def upgrade() -> None:
    _drop_column_if_exists("projects", "created_by")
    _drop_column_if_exists("papers", "uploaded_by")
    _drop_column_if_exists("extraction_jobs", "created_by")
    _drop_column_if_exists("export_jobs", "created_by")
    _drop_column_if_exists("candidate_records", "assigned_to")
    _drop_column_if_exists("candidate_records", "reviewed_by")
    _drop_column_if_exists("review_logs", "user_id")
    _drop_table_if_exists("project_members")
    _drop_table_if_exists("users")


def downgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False, server_default="Local User"),
        sa.Column("hashed_password", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("system_role", sa.String(length=20), nullable=False, server_default="member"),
        sa.Column("is_superadmin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "project_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="member"),
    )
```

If SQLite cannot drop constrained columns directly in local verification, wrap the relevant tables with `op.batch_alter_table(table_name)` in this same migration instead of using runtime schema repair.

- [ ] **Step 7: Delete user model/schema files**

Delete:

```text
backend/app/models/user.py
backend/app/models/project_member.py
backend/app/schemas/auth.py
```

- [ ] **Step 8: Run model tests**

Run:

```powershell
cd backend
pytest tests/test_open_workspace_models.py tests/test_open_resource_access.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit model/schema/migration cleanup**

Run:

```powershell
git add backend/app/models backend/app/schemas backend/alembic backend/tests/test_open_workspace_models.py
git add -u backend/app/models/user.py backend/app/models/project_member.py backend/app/schemas/auth.py
git commit -m "refactor: remove user models from workspace schema"
```

---

### Task 4: Backend Open API Conversion

**Files:**
- Delete: `backend/app/api/auth.py`
- Delete: `backend/app/api/users.py`
- Delete: `backend/app/services/session_store.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/api/projects.py`
- Modify: `backend/app/api/papers.py`
- Modify: `backend/app/api/candidates.py`
- Modify: `backend/app/api/exports.py`
- Modify: `backend/app/init_db.py`
- Test: `backend/tests/test_open_workspace_api.py`

- [ ] **Step 1: Write API route regression tests**

Create `backend/tests/test_open_workspace_api.py`:

```python
from app.main import app


def registered_paths() -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def test_auth_and_user_routes_are_not_registered():
    paths = registered_paths()
    assert not any(path.startswith("/api/auth") for path in paths)
    assert not any(path.startswith("/api/users") for path in paths)
    assert not any("/members" in path for path in paths)


def test_business_routes_are_registered():
    paths = registered_paths()
    assert "/api/projects" in paths
    assert any(path.startswith("/api/projects/{project_id}/papers") for path in paths)
    assert any(path.startswith("/api/projects/{project_id}/candidates") for path in paths)
    assert any(path.startswith("/api/projects/{project_id}/exports") for path in paths)
```

- [ ] **Step 2: Run API route tests and verify current failure**

Run:

```powershell
cd backend
pytest tests/test_open_workspace_api.py -q
```

Expected: FAIL while auth/users/member routes remain.

- [ ] **Step 3: Unregister auth/users routers**

Change `backend/app/main.py` imports from:

```python
from app.api import auth, projects, papers, candidates, exports, users
```

to:

```python
from app.api import projects, papers, candidates, exports
```

Keep router registration only for:

```python
api_routers = [
    (projects.router, "/api"),
    (papers.router, "/api"),
    (candidates.router, "/api"),
    (exports.router, "/api"),
]
```

- [ ] **Step 4: Remove auth dependencies from project APIs**

In `backend/app/api/projects.py`, remove `get_current_user`, `require_superadmin`, `require_project_manage`, `ProjectMember`, and `User` usage. Project create uses:

```python
project = Project(**payload.model_dump())
```

Project list returns all unarchived projects:

```python
result = await db.execute(
    select(Project).where(Project.archived.is_(False)).order_by(Project.created_at.desc())
)
```

Remove `/projects/{project_id}/members` endpoints completely.

- [ ] **Step 5: Remove auth dependencies from paper APIs**

In `backend/app/api/papers.py`, use:

```python
project = await get_project_or_404(db, project_id)
paper = await get_paper_or_404(db, project_id, paper_id)
```

Create papers without `uploaded_by`:

```python
paper = Paper(project_id=project.id, original_filename=file.filename, file_path=str(save_path), status="uploaded")
```

Create extraction jobs without `created_by`:

```python
job = ExtractionJob(project_id=project_id, paper_id=paper_id, status="queued", step="queued", percent=0)
```

Keep upload, extraction start, progress stream, cancellation, download, report, update, delete, and summaries open.

- [ ] **Step 6: Remove auth dependencies from candidate APIs**

In `backend/app/api/candidates.py`, replace permission checks with project/resource validation. Review log creation becomes:

```python
db.add(ReviewLog(
    project_id=project_id,
    candidate_record_id=record.id,
    action="review",
    old_value=old_status,
    new_value=record.review_status,
))
```

No `reviewed_by`, `reviewed_by_name`, or user identity is returned.

- [ ] **Step 7: Remove auth dependencies from export APIs**

In `backend/app/api/exports.py`, create export jobs without `created_by`:

```python
job = ExportJob(project_id=project_id, filename=filename, status="completed", file_path=str(output_path))
```

Use `get_export_or_404(db, project_id, export_id)` for download/delete.

- [ ] **Step 8: Remove default admin initialization**

Rewrite `backend/app/init_db.py`:

```python
"""Database initialization script for the open workspace."""

import asyncio

from app.core.database import engine
from app.models import Base


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Database schema initialized for open workspace")


if __name__ == "__main__":
    asyncio.run(init_db())
```

- [ ] **Step 9: Delete auth API/session files**

Delete:

```text
backend/app/api/auth.py
backend/app/api/users.py
backend/app/services/session_store.py
```

- [ ] **Step 10: Remove auth-only dependencies**

From `backend/requirements.txt`, remove:

```text
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
```

Keep `httpx`, `openai`, and MinerU/LLM Bearer-token code because those are external-service credentials, not user login.

- [ ] **Step 11: Run API tests**

Run:

```powershell
cd backend
pytest tests/test_open_workspace_api.py tests/test_open_resource_access.py tests/test_open_workspace_models.py -q
```

Expected: PASS.

- [ ] **Step 12: Scan backend user-system remnants**

Run:

```powershell
rg -n "app.models.user|ProjectMember|session_store|decode_access_token|create_access_token|hashed_password|superadmin|require_superadmin|get_current_user|require_project_role|project_members|/auth|/users|members" backend/app backend/tests backend/scripts
```

Expected: no user-system references. External-service `Authorization: Bearer` references in LLM/MinerU clients are acceptable and should remain.

- [ ] **Step 13: Commit backend open API conversion**

Run:

```powershell
git add backend/app backend/requirements.txt backend/tests/test_open_workspace_api.py
git add -u backend/app/api/auth.py backend/app/api/users.py backend/app/services/session_store.py
git commit -m "refactor: open backend workspace APIs"
```

---

### Task 5: Schema Repair And Runtime Stability Pass

**Files:**
- Modify: `backend/app/core/schema_repair.py`
- Modify: `backend/app/core/redis_client.py`
- Modify: `backend/app/services/extraction_jobs.py`
- Modify: `backend/app/services/progress_bus.py`
- Modify: `backend/app/services/job_cancellation.py`
- Modify: `backend/app/services/document_context.py`
- Modify: `backend/app/services/mineru_client.py`
- Test: `backend/tests/test_runtime_helpers.py`
- Test: `backend/tests/test_job_cancellation.py`

- [ ] **Step 1: Add schema repair regression test**

Extend `backend/tests/test_runtime_helpers.py` with:

```python
from app.core import schema_repair


def test_schema_repair_does_not_reference_user_system():
    module_text = schema_repair.__loader__.get_source(schema_repair.__name__)
    assert "users" not in module_text
    assert "project_members" not in module_text
    assert "created_by" not in module_text
    assert "uploaded_by" not in module_text
```

- [ ] **Step 2: Remove user repair from schema_repair**

Delete any repair logic that creates or alters:

```text
users
project_members
projects.created_by
papers.uploaded_by
extraction_jobs.created_by
export_jobs.created_by
candidate_records.assigned_to
candidate_records.reviewed_by
review_logs.user_id
```

Keep repair only for extraction runtime compatibility fields such as:

```text
extraction_jobs.progress_message
extraction_jobs.cancel_requested_at
document parse tables and fields
candidate/evidence/export compatibility needed by current tests
```

- [ ] **Step 3: Make Redis optional behavior explicit**

In `backend/app/core/redis_client.py`, change comments/docstrings from "cache, sessions, queues, and pub/sub" to:

```python
"""Shared async Redis client for optional cache, queues, and pub/sub."""
```

When Redis connection fails, log one clear message and return `None`; do not raise during startup when `REDIS_ENABLED` allows fallback.

- [ ] **Step 4: Log background poller errors**

In `backend/app/services/extraction_jobs.py`, wrap queue polling loops with:

```python
try:
    ...
except asyncio.CancelledError:
    raise
except Exception:
    logger.exception("Extraction queue polling failed")
    await asyncio.sleep(1.0)
```

Expected: exceptions are visible and jobs are not silently stuck because of swallowed poller failures.

- [ ] **Step 5: Preserve cancellation behavior**

Run:

```powershell
cd backend
pytest tests/test_job_cancellation.py tests/test_runtime_helpers.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit runtime hardening**

Run:

```powershell
git add backend/app/core/schema_repair.py backend/app/core/redis_client.py backend/app/services/extraction_jobs.py backend/app/services/progress_bus.py backend/app/services/job_cancellation.py backend/app/services/document_context.py backend/app/services/mineru_client.py backend/tests/test_runtime_helpers.py
git commit -m "fix: harden open workspace runtime behavior"
```

---

### Task 6: Frontend Open Workspace Conversion

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/contexts/ExtractionContext.tsx`
- Modify: `frontend/src/pages/WorkspaceLayout.tsx`
- Modify: `frontend/src/pages/ProjectsPage.tsx`
- Modify: `frontend/src/pages/PapersPage.tsx`
- Modify: `frontend/src/pages/ReviewPage.tsx`
- Modify: `frontend/src/pages/ExportPage.tsx`
- Modify: `frontend/src/pages/SettingsPage.tsx`
- Modify: `frontend/src/index.css`
- Delete: `frontend/src/pages/LoginPage.tsx`
- Delete: `frontend/src/pages/MembersPage.tsx`
- Delete: `frontend/src/pages/UserManagementPage.tsx`
- Delete: `frontend/src/pages/ProfilePage.tsx`
- Delete: `frontend/src/stores/auth.ts`
- Delete: `frontend/src/main.ts`
- Delete: `frontend/src/counter.ts`
- Delete: `frontend/src/style.css`
- Delete: `frontend/src/assets/vite.svg`

- [ ] **Step 1: Remove auth routing from App**

Replace `frontend/src/App.tsx` with open routes:

```tsx
import { Navigate, Route, Routes } from 'react-router-dom';
import { ProjectProvider } from './stores/project';
import { ExtractionProvider } from './contexts/ExtractionContext';
import WorkspaceLayout from './pages/WorkspaceLayout';
import ProjectsPage from './pages/ProjectsPage';
import PapersPage from './pages/PapersPage';
import ReviewPage from './pages/ReviewPage';
import ExportPage from './pages/ExportPage';
import SettingsPage from './pages/SettingsPage';

function AppRoutes() {
  return (
    <ProjectProvider>
      <ExtractionProvider>
        <Routes>
          <Route path="/" element={<WorkspaceLayout />}>
            <Route index element={<ProjectsPage />} />
            <Route path="projects" element={<ProjectsPage />} />
            <Route path="papers" element={<PapersPage />} />
            <Route path="review" element={<ReviewPage />} />
            <Route path="export" element={<ExportPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </ExtractionProvider>
    </ProjectProvider>
  );
}

export default function App() {
  return <AppRoutes />;
}
```

- [ ] **Step 2: Remove JWT behavior from API client**

In `frontend/src/api/client.ts`, remove:

```text
localStorage token reads/writes
Authorization injection
401 redirect to /login
```

Keep only base URL, timeout, and generic response handling.

- [ ] **Step 3: Remove SSE Authorization header**

In `frontend/src/contexts/ExtractionContext.tsx`, remove:

```tsx
Authorization: `Bearer ${token}`,
```

Expected: progress stream connects as an open project/paper resource.

- [ ] **Step 4: Remove user/admin navigation**

In `frontend/src/pages/WorkspaceLayout.tsx`, keep only these menu items:

```tsx
const menuItems = [
  { key: '/projects', icon: <ProjectOutlined />, label: '项目' },
  { key: '/papers', icon: <FileTextOutlined />, label: '文献' },
  { key: '/review', icon: <CheckSquareOutlined />, label: '复核' },
  { key: '/export', icon: <DownloadOutlined />, label: '导出' },
  { key: '/settings', icon: <SettingOutlined />, label: '项目配置' },
];
```

Remove user name, profile, logout, members, users, role checks, and administrator labels.

- [ ] **Step 5: Relabel settings as project configuration**

In `frontend/src/pages/SettingsPage.tsx`, replace visible admin/settings language with:

```text
项目配置
LLM 配置
抽取参数
```

Expected: no "管理员", "用户", "角色", or "平台管理" wording remains.

- [ ] **Step 6: Delete auth and starter files**

Delete:

```text
frontend/src/pages/LoginPage.tsx
frontend/src/pages/MembersPage.tsx
frontend/src/pages/UserManagementPage.tsx
frontend/src/pages/ProfilePage.tsx
frontend/src/stores/auth.ts
frontend/src/main.ts
frontend/src/counter.ts
frontend/src/style.css
frontend/src/assets/vite.svg
```

- [ ] **Step 7: Remove login/starter CSS**

In `frontend/src/index.css`, delete CSS blocks for:

```text
.login-container
.login-card
Vite starter cards/buttons/logo classes
```

Keep workspace layout, Ant Design overrides, table, review, upload, progress, and export styling.

- [ ] **Step 8: Scan frontend remnants**

Run:

```powershell
rg -n "AuthProvider|useAuth|ProtectedRoute|PlatformAdminRoute|LoginPage|MembersPage|UserManagementPage|ProfilePage|logout|登录|退出|成员|用户管理|管理员|superadmin|system_role|Authorization" frontend/src
```

Expected: no frontend user-system references. External-service words in non-frontend files are irrelevant to this scan.

- [ ] **Step 9: Build frontend when dependencies are usable**

Run:

```powershell
cd frontend
npm run build
```

Expected: PASS in a clean dependency install. If local server-pulled `node_modules` fails with missing native binding, record the environment error and verify by clean reinstall when network/dependency access is available.

- [ ] **Step 10: Commit frontend open workspace**

Run:

```powershell
git add frontend/src frontend/package.json frontend/package-lock.json
git add -u frontend/src/pages/LoginPage.tsx frontend/src/pages/MembersPage.tsx frontend/src/pages/UserManagementPage.tsx frontend/src/pages/ProfilePage.tsx frontend/src/stores/auth.ts frontend/src/main.ts frontend/src/counter.ts frontend/src/style.css frontend/src/assets/vite.svg
git commit -m "refactor: remove frontend user system"
```

---

### Task 7: Scripts, Diagnostics, And Documentation Cleanup

**Files:**
- Delete: `backend/scripts/ops/reset_admin_user.py`
- Delete: `backend/scripts/ops/migrate_user_roles.py`
- Delete: `backend/scripts/ops/test_multi_user_flow.py`
- Modify: `backend/scripts/ops/e2e_extraction_flow.py`
- Modify: `docs/mineru_startup.md`
- Create: `.env.example`

- [ ] **Step 1: Rewrite E2E flow header and setup**

Change `backend/scripts/ops/e2e_extraction_flow.py` docstring to:

```python
"""End-to-end HTTP test for the open workspace: projects, papers, extraction, progress, review, and export."""
```

Remove login/logout calls and use:

```python
headers: dict[str, str] = {}
```

- [ ] **Step 2: Delete user ops scripts**

Delete:

```text
backend/scripts/ops/reset_admin_user.py
backend/scripts/ops/migrate_user_roles.py
backend/scripts/ops/test_multi_user_flow.py
```

- [ ] **Step 3: Add local env template**

Create `.env.example`:

```dotenv
DATABASE_URL=sqlite+aiosqlite:///./fiber_data.db
ALLOW_SQLITE_FALLBACK=true
REDIS_ENABLED=false
REDIS_URL=redis://localhost:6379/0
UPLOAD_DIR=./uploads
EXPORT_DIR=./exports
MINERU_MODE=local
MINERU_LOCAL_BASE_URL=http://localhost:30000
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
```

No secrets or deployment IPs should be committed.

- [ ] **Step 4: Scan scripts/docs for user system**

Run:

```powershell
rg -n "auth/login|auth/logout|auth/me|Authorization.*Bearer|reset_admin|migrate_user|multi-user|RBAC|superadmin|project_members|users" backend/scripts docs README.md .env.example
```

Expected: no user-system operational scripts remain. `Authorization: Bearer` in external LLM/MinerU diagnostics may remain only if the script tests external API keys, not app login.

- [ ] **Step 5: Commit scripts/docs cleanup**

Run:

```powershell
git add backend/scripts docs README.md .env.example
git add -u backend/scripts/ops/reset_admin_user.py backend/scripts/ops/migrate_user_roles.py backend/scripts/ops/test_multi_user_flow.py
git commit -m "chore: remove user-system operations"
```

---

### Task 8: Extraction Safety Baseline

**Files:**
- Create: `backend/tests/test_extraction_safety_baseline.py`
- Modify only if needed: `backend/tests/test_workbook_export.py`, `backend/tests/test_fact_postprocess.py`, `backend/tests/test_extraction_yield_regression.py`

- [ ] **Step 1: Add export schema baseline test**

Create `backend/tests/test_extraction_safety_baseline.py`:

```python
from app.models.candidate_record import CandidateRecord
from app.services.workbook_export import EXPORT_COLUMNS


def test_export_columns_still_cover_core_candidate_fields():
    model_columns = {column.name for column in CandidateRecord.__table__.columns}
    expected_business_fields = {
        "record_id",
        "paper_id_biz",
        "paper_title",
        "doi_or_url",
        "year",
        "journal",
        "sample_group_id",
        "sample_id",
        "material_system",
        "fiber_type",
        "performance_metric",
        "performance_value",
        "performance_unit",
        "review_status",
        "reviewer_comment",
    }
    assert expected_business_fields <= model_columns
    assert len(EXPORT_COLUMNS) >= 40
```

- [ ] **Step 2: Add extractor public contract test**

Extend `backend/tests/test_extraction_safety_baseline.py`:

```python
from app.services.extractor_v7 import V7ExtractorService


def test_extractor_public_entrypoint_is_preserved():
    assert hasattr(V7ExtractorService, "extract_paper")
    assert callable(V7ExtractorService.extract_paper)
```

- [ ] **Step 3: Run extraction unit baseline tests**

Run:

```powershell
cd backend
pytest tests/test_extraction_safety_baseline.py tests/test_validators.py tests/test_grouping_helpers.py tests/test_workbook_export.py tests/test_extraction_yield_regression.py -q
```

Expected: PASS. If environment-specific import failures occur, fix imports without changing extraction semantics.

- [ ] **Step 4: Record a sample-output baseline when a sample PDF is available**

Run the existing dry-run or E2E script against a known local sample PDF and record:

```text
candidate count
fact count
evidence item count
export workbook column count
job final status
progress final percent
```

Expected: baseline values are stored in a local note or test fixture that does not include uploaded paper contents if the file is private.

- [ ] **Step 5: Commit safety tests**

Run:

```powershell
git add backend/tests/test_extraction_safety_baseline.py backend/tests/test_workbook_export.py backend/tests/test_fact_postprocess.py backend/tests/test_extraction_yield_regression.py
git commit -m "test: protect extraction output contract"
```

---

### Task 9: Mechanical Extraction Service Decomposition

**Files:**
- Modify: `backend/app/services/extractor_v7/service.py`
- Create: `backend/app/services/extractor_v7/runtime.py`
- Create: `backend/app/services/extractor_v7/pipeline.py`
- Create: `backend/app/services/extractor_v7/persistence.py`
- Create: `backend/app/services/extractor_v7/vision.py`
- Create: `backend/app/services/extractor_v7/report_building.py`
- Modify: `backend/app/services/extractor_v7/__init__.py`
- Test: existing extractor tests plus `backend/tests/test_extraction_safety_baseline.py`

- [ ] **Step 1: Move runtime helpers without behavior changes**

Create `backend/app/services/extractor_v7/runtime.py` for:

```text
_current_job_id context var
_check_cancelled
_emit_progress helper
LLM timeout/stage call helpers
model-mode resolution helpers
```

Import these helpers back into `service.py` and keep all public method signatures unchanged.

- [ ] **Step 2: Run parity tests after runtime move**

Run:

```powershell
cd backend
pytest tests/test_extraction_safety_baseline.py tests/test_chunk_policy.py tests/test_job_cancellation.py -q
```

Expected: PASS.

- [ ] **Step 3: Move persistence helpers without behavior changes**

Create `backend/app/services/extractor_v7/persistence.py` for database write routines:

```text
delete previous candidate/fact/sample/evidence rows for a paper
save sample catalog rows
save fact candidate rows
save candidate records
save evidence items
update paper metadata/status
```

Keep the saved fields identical to the pre-move `CandidateRecord`, `FactCandidate`, `SampleCatalog`, and `EvidenceItem` construction.

- [ ] **Step 4: Run parity tests after persistence move**

Run:

```powershell
cd backend
pytest tests/test_extraction_safety_baseline.py tests/test_workbook_export.py tests/test_fact_postprocess.py -q
```

Expected: PASS.

- [ ] **Step 5: Move vision/report helpers without behavior changes**

Create:

```text
backend/app/services/extractor_v7/vision.py
backend/app/services/extractor_v7/report_building.py
```

Move optional figure/vision enhancement into `vision.py`. Move report payload assembly and report-file writing into `report_building.py`. The generated report keys must remain identical.

- [ ] **Step 6: Keep public import compatibility**

Ensure `backend/app/services/extractor_v7/__init__.py` still exposes:

```python
from app.services.extractor_v7.exceptions import ExtractionCancelled
from app.services.extractor_v7.reporting import build_extraction_report
from app.services.extractor_v7.service import V7ExtractorService
```

- [ ] **Step 7: Run full backend tests**

Run:

```powershell
cd backend
pytest -q
```

Expected: PASS. If pytest prints all tests passed but hangs on shutdown, capture the hang source separately instead of changing extraction logic.

- [ ] **Step 8: Commit mechanical service split**

Run:

```powershell
git add backend/app/services/extractor_v7 backend/tests
git commit -m "refactor: split extraction service into focused modules"
```

---

### Task 10: Measured Efficiency Improvements

**Files:**
- Modify: `backend/app/api/papers.py`
- Modify: `backend/app/api/candidates.py`
- Modify: `backend/app/api/exports.py`
- Modify: `backend/app/services/extraction_jobs.py`
- Modify: `backend/app/services/llm_metrics.py`
- Create: `backend/tests/test_api_query_efficiency.py`

- [ ] **Step 1: Add query-efficiency tests for resource mismatch and list endpoints**

Create `backend/tests/test_api_query_efficiency.py` with small unit tests around helper behavior and pagination defaults:

```python
from app.api.candidates import _normalize_pagination


def test_candidate_pagination_is_bounded():
    page, page_size = _normalize_pagination(page=1, page_size=5000)
    assert page == 1
    assert page_size <= 200
```

If `_normalize_pagination` does not exist, add it to `backend/app/api/candidates.py`:

```python
def _normalize_pagination(page: int = 1, page_size: int = 50) -> tuple[int, int]:
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    return page, page_size
```

- [ ] **Step 2: Remove repeated project/paper lookups in handlers**

In handlers that call the same lookup more than once, load once:

```python
project = await get_project_or_404(db, project_id)
paper = await get_paper_or_404(db, project.id, paper_id)
```

Pass `project` and `paper` through local logic instead of re-querying by ID.

- [ ] **Step 3: Bound candidate/export list endpoints**

Ensure candidate and export list endpoints use:

```python
page, page_size = _normalize_pagination(page, page_size)
offset = (page - 1) * page_size
query = query.offset(offset).limit(page_size)
```

Expected: large workspaces do not fetch unbounded rows by default.

- [ ] **Step 4: Add stage timing to extraction jobs without changing prompts**

In `backend/app/services/extraction_jobs.py`, record stage durations in job progress or metrics with existing `llm_metrics` patterns:

```python
started = time.perf_counter()
try:
    ...
finally:
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info("extraction_job_stage_complete", extra={"job_id": job_id, "stage": stage, "elapsed_ms": elapsed_ms})
```

Do not change prompt text, chunk caps, validation rules, or record generation in this step.

- [ ] **Step 5: Run targeted tests**

Run:

```powershell
cd backend
pytest tests/test_api_query_efficiency.py tests/test_extraction_safety_baseline.py tests/test_runtime_helpers.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit measured efficiency changes**

Run:

```powershell
git add backend/app/api backend/app/services/extraction_jobs.py backend/app/services/llm_metrics.py backend/tests/test_api_query_efficiency.py
git commit -m "perf: add bounded queries and extraction timing"
```

---

### Task 11: Final Verification And GitHub-Ready Scan

**Files:**
- Verify all touched files.

- [ ] **Step 1: Full user-system scan**

Run:

```powershell
rg -n "app.models.user|ProjectMember|project_members|session_store|/auth|/users|/members|LoginPage|MembersPage|UserManagementPage|ProfilePage|useAuth|AuthProvider|ProtectedRoute|PlatformAdminRoute|superadmin|system_role|is_superadmin|created_by|uploaded_by|reviewed_by|assigned_to|user_id" backend frontend README.md docs .github
```

Expected: no app user-system references. False positives must be external-service terms or historical design docs only; app code must be clean.

- [ ] **Step 2: External-service credential scan**

Run:

```powershell
rg -n "sk-[A-Za-z0-9]|api[_-]?key\\s*=\\s*['\"][^'\"]+|Bearer\\s+[A-Za-z0-9._-]{20,}|password\\s*=\\s*['\"][^'\"]+" .
```

Expected: no committed secrets. Empty placeholders in `.env.example` are acceptable.

- [ ] **Step 3: Backend tests**

Run:

```powershell
cd backend
$env:DATABASE_URL="sqlite+aiosqlite:///./test_open_workspace.db"
$env:ALLOW_SQLITE_FALLBACK="true"
$env:REDIS_ENABLED="false"
pytest -q
```

Expected: PASS. If pytest completes but hangs during shutdown, record the hanging fixture/background task and fix it under runtime stability, not by weakening tests.

- [ ] **Step 4: Frontend build**

Run:

```powershell
cd frontend
npm run build
```

Expected: PASS with a clean dependency install. If local native binding errors occur because `node_modules` came from the server, document the exact error and verify after reinstall.

- [ ] **Step 5: Final repository status**

Run:

```powershell
git status --short
```

Expected: only intentional uncommitted local runtime files remain, or a clean working tree if all tasks were committed.

- [ ] **Step 6: Final acceptance scan**

Confirm:

```text
No login screen
No user menu
No logout
No members page
No user management page
No profile page
No admin-only gates
No backend auth/users/member routes
No SQLAlchemy User or ProjectMember registry
No business-table user foreign keys
Paper upload, extraction, progress, cancellation, review, export, and project LLM config still work
Extraction service public entrypoint and export contract are preserved
```

- [ ] **Step 7: Commit final verification fixes**

Run if final scan required fixes:

```powershell
git add .
git commit -m "chore: verify open workspace refactor"
```

Expected: private GitHub push is blocked only by intentionally local artifacts or environment-only dependency issues.

---

## Self-Review

- Spec coverage: This plan covers open workspace, full user-system deletion, repository cleanup, CI path correction, runtime/schema stabilization, frontend cleanup, script/doc cleanup, extraction safety baseline, service decomposition, and measured efficiency work.
- Placeholder scan: No task contains unspecified placeholders. Each task names files, exact scans, expected outcomes, and the code or text shape to apply.
- Extraction safety: The plan explicitly protects `V7ExtractorService.extract_paper`, export columns, candidate/evidence linkage, job progress, cancellation, and validation behavior before service splitting or performance tuning.
