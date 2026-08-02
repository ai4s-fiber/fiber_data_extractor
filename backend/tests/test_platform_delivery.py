from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.models import Base, FactCandidate, Paper, Project, SampleCatalog
from app.services.platform_delivery import (
    PlatformDeliveryError,
    build_project_batch_artifact,
    load_pinned_platform_binding,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BATCH_TEMPLATE = (
    REPOSITORY_ROOT
    / "platform_templates"
    / "canary"
    / "platform-batch-canary.json"
)


def _binding():
    digest = hashlib.sha256(BATCH_TEMPLATE.read_bytes()).hexdigest()
    return load_pinned_platform_binding(
        batch_template_path=str(BATCH_TEMPLATE),
        expected_sha256=digest,
        expected_dataset_id=2_081_660_157_305_163_778,
        expected_template_id=2_081_658_374_180_704_257,
    )


def test_platform_binding_fails_closed_on_wrong_hash():
    with pytest.raises(PlatformDeliveryError, match="SHA-256 不匹配"):
        load_pinned_platform_binding(
            batch_template_path=str(BATCH_TEMPLATE),
            expected_sha256="0" * 64,
            expected_dataset_id=2_081_660_157_305_163_778,
            expected_template_id=2_081_658_374_180_704_257,
        )


@pytest.mark.asyncio
async def test_project_batch_is_deterministic_for_unchanged_data():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as session:
        project = Project(name="Direct platform delivery")
        session.add(project)
        await session.flush()
        paper = Paper(
            project_id=project.id,
            original_filename="paper.pdf",
            file_object_key="1/paper.pdf",
            paper_title="Deterministic delivery",
            status="review",
        )
        session.add(paper)
        await session.flush()
        session.add(
            SampleCatalog(
                paper_id=paper.id,
                project_id=project.id,
                sample_id="S-1",
                material_system="PAN",
            )
        )
        session.add(
            FactCandidate(
                paper_id=paper.id,
                project_id=project.id,
                fact_id="F-1",
                fact_type="performance",
                metric_or_parameter="tensile strength",
                value="800 MPa",
                unit="MPa",
                evidence_text="The strength was 800 MPa.",
                assigned_sample_id="S-1",
                assignment_status="assigned",
            )
        )
        await session.commit()

        first = await build_project_batch_artifact(
            session,
            project_id=project.id,
            binding=_binding(),
            paper_ids=None,
            include_unmapped=True,
        )
        second = await build_project_batch_artifact(
            session,
            project_id=project.id,
            binding=_binding(),
            paper_ids=None,
            include_unmapped=True,
        )

    await engine.dispose()
    assert first.content == second.content
    assert first.batch_sha256 == second.batch_sha256
    assert first.summary["record_count"] == 1
