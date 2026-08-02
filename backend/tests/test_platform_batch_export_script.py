"""End-to-end test for the project-level platform batch export command."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.models import Base, FactCandidate, Paper, Project, SampleCatalog
from app.services.platform_batch_adapter import validate_platform_batch


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _seed_project(database_url: str) -> int:
    async def seed() -> int:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            project = Project(name="Platform export integration")
            session.add(project)
            await session.flush()
            paper = Paper(
                project_id=project.id,
                original_filename="integration.pdf",
                file_object_key="integration.pdf",
                paper_title="Integrated platform export",
                year=2026,
                journal="AI4S Test",
                status="review",
            )
            session.add(paper)
            await session.flush()
            session.add(
                SampleCatalog(
                    paper_id=paper.id,
                    project_id=project.id,
                    sample_id="S-INTEGRATION-1",
                    sample_group_id="G-INTEGRATION-1",
                    material_system="PAN",
                    process_route="solution spinning",
                    confidence=0.95,
                )
            )
            session.add(
                FactCandidate(
                    paper_id=paper.id,
                    project_id=project.id,
                    fact_id="F-INTEGRATION-1",
                    fact_type="performance",
                    metric_or_parameter="tensile strength",
                    value="800-900 MPa",
                    unit="MPa",
                    method="single-fiber tensile test",
                    condition="23 °C",
                    evidence_text="Tensile strength ranged from 800 to 900 MPa.",
                    source_location="p.5, Table 2",
                    extraction_method="AI_table",
                    confidence=0.95,
                    assigned_sample_id="S-INTEGRATION-1",
                    assignment_status="assigned",
                )
            )
            await session.commit()
            project_id = project.id
        await engine.dispose()
        return project_id

    return asyncio.run(seed())


def test_project_export_command_builds_valid_upload_ready_json(tmp_path: Path):
    database_path = tmp_path / "platform-export.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    project_id = _seed_project(database_url)
    output_path = tmp_path / "platform-batch.json"
    batch_template_path = (
        REPOSITORY_ROOT
        / "platform_templates/canary/platform-batch-canary.json"
    )
    batch_template_sha256 = hashlib.sha256(
        batch_template_path.read_bytes()
    ).hexdigest()
    command = [
        sys.executable,
        str(REPOSITORY_ROOT / "backend/scripts/ops/export_platform_batch.py"),
        "--project-id",
        str(project_id),
        "--database-url",
        database_url,
        "--batch-template",
        str(batch_template_path),
        "--output",
        str(output_path),
        "--expected-dataset-id",
        "2081660157305163778",
        "--expected-template-id",
        "2081658374180704257",
        "--batch-template-sha256",
        batch_template_sha256,
        "--exported-at",
        "2026-07-27T16:45:00+08:00",
    ]

    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    batch = json.loads(output_path.read_text(encoding="utf-8"))
    summary = validate_platform_batch(batch)
    assert summary["dataset_id"] == 2_081_660_157_305_163_778
    assert summary["template_id"] == 2_081_658_374_180_704_257
    assert summary["record_count"] == 1
    record = batch["data"][0]["content"]
    assert record["object"]["样品编号"] == "S-INTEGRATION-1"
    performance = record["results"][3]["性能测试结果"]
    assert performance[0]["性能指标名称"] == "tensile strength"
    assert performance[0]["性能范围"] == {"lb": 800, "ub": 900}


def test_project_export_command_fails_closed_on_wrong_dataset_id(
    tmp_path: Path,
):
    output_path = tmp_path / "must-not-exist.json"
    batch_template_path = (
        REPOSITORY_ROOT
        / "platform_templates/canary/platform-batch-canary.json"
    )
    batch_template_sha256 = hashlib.sha256(
        batch_template_path.read_bytes()
    ).hexdigest()
    command = [
        sys.executable,
        str(REPOSITORY_ROOT / "backend/scripts/ops/export_platform_batch.py"),
        "--project-id",
        "1",
        "--batch-template",
        str(batch_template_path),
        "--output",
        str(output_path),
        "--expected-dataset-id",
        "2081660157305163779",
        "--expected-template-id",
        "2081658374180704257",
        "--batch-template-sha256",
        batch_template_sha256,
    ]

    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 2
    assert "dataset ID mismatch" in completed.stderr
    assert not output_path.exists()


def test_project_export_command_requires_binding_pins(tmp_path: Path):
    output_path = tmp_path / "must-not-exist.json"
    command = [
        sys.executable,
        str(REPOSITORY_ROOT / "backend/scripts/ops/export_platform_batch.py"),
        "--project-id",
        "1",
        "--batch-template",
        str(
            REPOSITORY_ROOT
            / "platform_templates/canary/platform-batch-canary.json"
        ),
        "--output",
        str(output_path),
    ]

    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 2
    assert "--expected-dataset-id" in completed.stderr
    assert "--expected-template-id" in completed.stderr
    assert "--batch-template-sha256" in completed.stderr
    assert not output_path.exists()


def test_project_export_command_fails_closed_on_wrong_template_hash(
    tmp_path: Path,
):
    output_path = tmp_path / "must-not-exist.json"
    command = [
        sys.executable,
        str(REPOSITORY_ROOT / "backend/scripts/ops/export_platform_batch.py"),
        "--project-id",
        "1",
        "--batch-template",
        str(
            REPOSITORY_ROOT
            / "platform_templates/canary/platform-batch-canary.json"
        ),
        "--output",
        str(output_path),
        "--expected-dataset-id",
        "2081660157305163778",
        "--expected-template-id",
        "2081658374180704257",
        "--batch-template-sha256",
        "0" * 64,
    ]

    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 2
    assert "SHA-256 mismatch" in completed.stderr
    assert not output_path.exists()
