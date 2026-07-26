"""Chemical-fiber template schema and sparse projection endpoints."""

import json

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_paper_or_404
from app.models.candidate_record import CandidateRecord
from app.models.evidence_item import EvidenceItem
from app.models.fact_candidate import FactCandidate
from app.models.sample_catalog import SampleCatalog
from app.services.template_projection import (
    build_template_projection,
    template_schema_payload,
)


router = APIRouter(tags=["模板投影"])


@router.get("/template-schema")
async def get_template_schema():
    """Return the local projection contract and external binding status."""
    return template_schema_payload()


@router.get("/projects/{project_id}/papers/{paper_id}/template-projection")
async def get_template_projection(
    project_id: int,
    paper_id: int,
    include_unmapped: bool = Query(default=True),
    download: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
):
    """Project one paper's persisted facts into the sparse fiber template."""
    paper = await get_paper_or_404(db, project_id, paper_id)

    samples_result = await db.execute(
        select(SampleCatalog)
        .where(
            SampleCatalog.project_id == project_id,
            SampleCatalog.paper_id == paper_id,
        )
        .order_by(SampleCatalog.id)
    )
    facts_result = await db.execute(
        select(FactCandidate)
        .where(
            FactCandidate.project_id == project_id,
            FactCandidate.paper_id == paper_id,
        )
        .order_by(FactCandidate.id)
    )
    records_result = await db.execute(
        select(CandidateRecord)
        .where(
            CandidateRecord.project_id == project_id,
            CandidateRecord.source_paper_id == paper_id,
        )
        .order_by(CandidateRecord.id)
    )
    evidence_result = await db.execute(
        select(EvidenceItem)
        .where(
            EvidenceItem.project_id == project_id,
            EvidenceItem.paper_id == paper_id,
        )
        .order_by(EvidenceItem.id)
    )

    projection = build_template_projection(
        paper=paper,
        samples=samples_result.scalars().all(),
        facts=facts_result.scalars().all(),
        records=records_result.scalars().all(),
        evidence_items=evidence_result.scalars().all(),
        include_unmapped=include_unmapped,
    )
    if not download:
        return projection

    content = json.dumps(projection, ensure_ascii=False, indent=2)
    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="paper_{paper_id}_template_projection.json"'
            )
        },
    )
