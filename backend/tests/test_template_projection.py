"""Sparse chemical-fiber template projection tests."""

import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import get_db
from app.main import app
from app.models import Base, FactCandidate, Paper, Project, SampleCatalog
from app.services.template_projection import (
    build_template_projection,
    parse_value_shape,
    resolve_fact_field,
    template_schema_payload,
)


def _ns(**values):
    return SimpleNamespace(**values)


def test_template_schema_is_explicit_about_external_binding():
    schema = template_schema_payload()

    assert schema["schema_version"] == "chemical_fiber_projection_v1"
    assert schema["external_schema_bound"] is False
    assert schema["rules"]["do_not_infer_missing_values"] is True
    assert "not_reported" in schema["missing_statuses"]
    assert any(
        item["field_path"] == "dynamic.performance"
        for item in schema["field_definitions"]
    )


def test_value_shape_parses_scalar_and_range_without_losing_raw_text():
    scalar = parse_value_shape(">= 100 MPa")
    value_range = parse_value_shape("200-240 °C")

    assert scalar["value_text"] == ">= 100 MPa"
    assert scalar["value_number"] == 100.0
    assert scalar["operator"] == ">="
    assert value_range["value_text"] == "200-240 °C"
    assert value_range["range_min"] == 200.0
    assert value_range["range_max"] == 240.0


def test_known_metric_maps_and_unknown_metric_gets_stable_audit_path():
    known = _ns(
        fact_type="performance",
        metric_or_parameter="断裂强度",
        category="mechanical",
    )
    unknown = _ns(
        fact_type="performance",
        metric_or_parameter="whisker instability index",
        category="custom",
    )

    known_path, _, known_mapped = resolve_fact_field(known)
    unknown_path, _, unknown_mapped = resolve_fact_field(unknown)

    assert known_path.endswith("mechanical.tensile_strength")
    assert known_mapped is True
    assert unknown_path.startswith("fiber_sample.performance.unmapped.")
    assert unknown_mapped is False
    assert resolve_fact_field(unknown)[0] == unknown_path


def test_metric_mapping_does_not_cross_template_sections():
    spinneret = _ns(
        fact_type="process",
        metric_or_parameter="spinneret hole diameter",
        category="spinning",
    )

    path, _, mapped = resolve_fact_field(spinneret)

    assert path.startswith("fiber_sample.process.unmapped.")
    assert mapped is False


def test_projection_is_sparse_grounded_and_preserves_unmapped_facts():
    paper = _ns(
        id=7,
        paper_title="Fiber paper",
        original_filename="fiber.pdf",
        doi_or_url="10.1000/example",
        year=2026,
        journal="Journal",
    )
    sample = _ns(
        sample_id="S1",
        sample_aliases=json.dumps(["A1"]),
        sample_group_id="G1",
        fiber_type="单组分长丝",
        material_system="PET",
        composition_expression="PET + 1 wt% additive",
        variable_name="additive loading",
        variable_value="1",
        variable_unit="wt%",
        process_route="melt spinning",
        source_location="p.3, experimental",
        evidence_text="S1 was melt-spun from PET with 1 wt% additive.",
        confidence=0.95,
    )
    facts = [
        _ns(
            fact_id="F1",
            fact_type="performance",
            metric_or_parameter="tensile strength",
            value="100",
            unit="MPa",
            method="single-fiber tensile test",
            condition="23 °C",
            category="mechanical",
            assigned_sample_id="S1",
            candidate_sample_ids=json.dumps(["S1"]),
            assignment_status="assigned",
            confidence=0.93,
            evidence_text="S1 reached a tensile strength of 100 MPa.",
            source_location="p.7, Table 2",
            source_block_id="b-7-2",
            source_page=7,
            source_bbox_json="[10,20,30,40]",
            evidence_item_id=None,
        ),
        _ns(
            fact_id="F2",
            fact_type="performance",
            metric_or_parameter="whisker instability index",
            value="2.4",
            unit="a.u.",
            method="custom method",
            condition="dry",
            category="custom",
            assigned_sample_id="S1",
            candidate_sample_ids=json.dumps(["S1"]),
            assignment_status="assigned",
            confidence=0.82,
            evidence_text="The whisker instability index of S1 was 2.4.",
            source_location="p.8",
            source_block_id="b-8-1",
            source_page=8,
            source_bbox_json=None,
            evidence_item_id=None,
        ),
    ]

    projection = build_template_projection(
        paper=paper,
        samples=[sample],
        facts=facts,
    )

    tensile = next(
        value
        for value in projection["values"]
        if value["field_path"].endswith("mechanical.tensile_strength")
    )
    assert tensile["entity_key"] == "paper:7:sample:S1"
    assert tensile["value_number"] == 100.0
    assert tensile["raw_value"] == "100"
    assert tensile["unit"] == "MPa"
    assert tensile["status"] == "extracted"
    assert tensile["evidence"]["source_block_id"] == "b-7-2"
    assert tensile["evidence"]["bbox"] == [10, 20, 30, 40]

    assert projection["quality"]["unmapped_fact_count"] == 1
    assert projection["unmapped_facts"][0]["fact_id"] == "F2"
    assert projection["rules"]["missing_values_are_not_inferred"] is True
    assert all(value["raw_value"] for value in projection["values"])


def test_unassigned_performance_is_retained_but_requires_review():
    paper = _ns(
        id=8,
        paper_title="Unassigned result",
        original_filename="unassigned.pdf",
        doi_or_url=None,
        year=None,
        journal=None,
    )
    fact = _ns(
        fact_id="F1",
        fact_type="performance",
        metric_or_parameter="elongation at break",
        value="25",
        unit="%",
        category="mechanical",
        assigned_sample_id=None,
        candidate_sample_ids=json.dumps(["S1", "S2"]),
        assignment_status="multiple",
        confidence=0.9,
        evidence_text="The elongation at break was 25%.",
        source_location="p.5",
        source_block_id="b5",
        source_page=5,
        source_bbox_json=None,
        evidence_item_id=None,
        method="",
        condition="",
    )

    projection = build_template_projection(paper=paper, facts=[fact])
    result = next(
        value
        for value in projection["values"]
        if value["field_path"].endswith("elongation_at_break")
    )

    assert result["entity_key"] == "paper:8:unassigned"
    assert result["status"] == "needs_review"
    assert projection["quality"]["status_counts"]["needs_review"] == 1


def test_template_projection_routes_are_registered_for_both_api_prefixes():
    paths = {route.path for route in app.routes}

    expected = "/projects/{project_id}/papers/{paper_id}/template-projection"
    assert f"/api{expected}" in paths
    assert f"/api/v1{expected}" in paths
    assert "/api/template-schema" in paths
    assert "/api/v1/template-schema" in paths


@pytest.mark.asyncio
async def test_template_projection_endpoint_reads_persisted_results():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        project = Project(name="Template test")
        session.add(project)
        await session.flush()
        paper = Paper(
            project_id=project.id,
            original_filename="paper.pdf",
            file_object_key="paper.pdf",
            paper_title="Persisted paper",
            status="review",
        )
        session.add(paper)
        await session.flush()
        session.add(
            SampleCatalog(
                paper_id=paper.id,
                project_id=project.id,
                sample_id="S1",
                sample_group_id="G1",
                material_system="PET",
                evidence_text="S1 was prepared from PET.",
                confidence=0.9,
            )
        )
        session.add(
            FactCandidate(
                paper_id=paper.id,
                project_id=project.id,
                fact_id="F1",
                fact_type="performance",
                metric_or_parameter="tensile strength",
                value="88",
                unit="MPa",
                evidence_text="S1 reached 88 MPa.",
                source_location="p.4",
                source_block_id="b4",
                source_page=4,
                extraction_method="AI_table",
                confidence=0.91,
                assigned_sample_id="S1",
                assignment_status="assigned",
            )
        )
        await session.commit()

        async def override_get_db():
            yield session

        app.dependency_overrides[get_db] = override_get_db
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get(
                    f"/api/projects/{project.id}/papers/{paper.id}/template-projection"
                )
        finally:
            app.dependency_overrides.pop(get_db, None)

    await engine.dispose()
    assert response.status_code == 200
    body = response.json()
    tensile = next(
        value
        for value in body["values"]
        if value["field_path"].endswith("mechanical.tensile_strength")
    )
    assert tensile["raw_value"] == "88"
    assert tensile["status"] == "extracted"
    assert body["quality"]["sample_count"] == 1
