from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.models import (
    Base,
    CandidateRecord,
    FactCandidate,
    Paper,
    Project,
    SampleCatalog,
)
from app.services.platform_material_delivery import (
    _blocked_sample_keys,
    _complete_material_chain_row,
    _externally_eligible_fact,
    _has_conflicting_measurement_pairs,
    _semantically_valid_material_row,
    _verified_sample_keys,
    build_project_material_fact_artifact,
    load_material_fact_binding,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = (
    REPOSITORY_ROOT
    / "platform_templates"
    / "ai4s-material-chain-template-v0.3.2.json"
)
DATASET_ID = 2_082_071_264_142_430_210
TEMPLATE_ID = 2_082_071_243_661_643_777


def _binding():
    digest = hashlib.sha256(TEMPLATE_PATH.read_bytes()).hexdigest()
    return load_material_fact_binding(
        template_path=str(TEMPLATE_PATH),
        expected_sha256=digest,
        expected_dataset_id=DATASET_ID,
        expected_template_id=TEMPLATE_ID,
        dataset_name="AI4S材料数据链_成分工艺结构性能_v0.3.2_20260728",
    )


def test_material_fact_binding_pins_verified_platform_ids():
    binding = _binding()

    assert binding.dataset_id == DATASET_ID
    assert binding.template_id == TEMPLATE_ID
    assert binding.template["dataset"]["_id"] == DATASET_ID
    assert binding.template["template"]["_id"] == TEMPLATE_ID
    assert binding.template["template"]["object"]["blocks"]["_ord"] == [
        "文献、样品与成分"
    ]
    assert binding.template["template"]["operations"][0]["id"] == "operation1"
    assert binding.template["template"]["results"][0]["id"] == "result1"


def test_strict_material_chain_gate_rejects_incomplete_or_blocked_samples():
    complete = {
        "文献编号": "1",
        "具体材料对象|样品编号": "S-1",
        "原料|前驱体|基体": "PAN",
        "工艺路线": "electrospinning",
        "结构指标名称": "fiber diameter",
        "性能指标名称": "tensile strength",
        "性能数值": "800 MPa",
        "结果描述|结论": "The tensile strength was 800 MPa.",
        "数据来源位置": "p.5, Figure 3",
    }
    assert _complete_material_chain_row(complete) is True

    for required_field in (
        "原料|前驱体|基体",
        "工艺路线",
        "结构指标名称",
        "性能指标名称",
        "性能数值",
        "结果描述|结论",
        "数据来源位置",
    ):
        incomplete = dict(complete)
        incomplete[required_field] = ""
        assert _complete_material_chain_row(incomplete) is False

    characterization_only = dict(complete)
    characterization_only["性能指标名称"] = "XPS peak 1；XPS peak 2"
    characterization_only["性能数值"] = "72.6 eV；456 eV"
    assert _complete_material_chain_row(characterization_only) is False

    reference_medium = dict(complete)
    reference_medium["具体材料对象|样品编号"] = "pure water"
    assert _semantically_valid_material_row(reference_medium) is False

    for reference_fragment in ("to", "its original intensity"):
        invalid_sample = dict(complete)
        invalid_sample["具体材料对象|样品编号"] = reference_fragment
        assert _semantically_valid_material_row(invalid_sample) is False

    characterization_process = dict(complete)
    characterization_process["工艺路线"] = "physical：LF-NMR"
    characterization_process["关键工艺参数"] = (
        "bound_water_relaxation_time_t2=6 ms"
    )
    assert _semantically_valid_material_row(characterization_process) is False

    process_with_real_manufacturing = dict(characterization_process)
    process_with_real_manufacturing["工艺路线"] = (
        "wet spinning → LF-NMR characterization"
    )
    assert _semantically_valid_material_row(process_with_real_manufacturing) is True

    cross_wired_control = dict(complete)
    cross_wired_control["具体材料对象|样品编号"] = "CPB@SiO2 NCs"
    cross_wired_control["成分配比|浓度"] = (
        "CPB QDs coated with SiO2 without co-reactant"
    )
    cross_wired_control["增强|填料|改性组分"] = (
        "co-reactants including DBAE and TPA"
    )
    cross_wired_control["工艺路线"] = (
        "silica coating with co-reactant incorporation"
    )
    assert _semantically_valid_material_row(cross_wired_control) is False

    cross_wired_parent = dict(complete)
    cross_wired_parent["具体材料对象|样品编号"] = "CPB QDs"
    cross_wired_parent["工艺路线"] = (
        "hot-injection synthesis followed by silica encapsulation"
    )
    assert _semantically_valid_material_row(cross_wired_parent) is False

    explicitly_coated = dict(cross_wired_parent)
    explicitly_coated["具体材料对象|样品编号"] = "silica-coated CPB QDs"
    assert _semantically_valid_material_row(explicitly_coated) is True

    design_only = dict(complete)
    design_only["工艺路线"] = "design specification"
    design_only["关键工艺参数"] = "intended_size=8 mm"
    assert _semantically_valid_material_row(design_only) is False

    for manufacturing_route in (
        "digital light processing 3D printing followed by calcination",
        "solvent casting and hot-pressing",
        "aza-Michael polymerization followed by epoxy curing",
    ):
        manufactured = dict(complete)
        manufactured["工艺路线"] = manufacturing_route
        assert _semantically_valid_material_row(manufactured) is True

    table_artifact = dict(complete)
    table_artifact["性能指标名称"] = (
        "lap shear strength；lap shear strengthd；samples；t g a"
    )
    table_artifact["性能数值"] = (
        "lap shear strength=6.4 MPa；samples=-4e100；t g a=82"
    )
    assert _semantically_valid_material_row(table_artifact) is False

    ambiguous_values = dict(complete)
    ambiguous_values["性能数值"] = (
        "water contact angle=93.4 °；water contact angle=56.6 °"
    )
    assert _semantically_valid_material_row(ambiguous_values) is False
    assert _has_conflicting_measurement_pairs(
        ambiguous_values["性能数值"]
    ) is True
    assert _has_conflicting_measurement_pairs(
        "拉伸强度=800 MPa；断裂伸长率=20 %"
    ) is False

    blocked = CandidateRecord(
        project_id=1,
        source_paper_id=1,
        sample_id="S-1",
        review_status="pending",
        reviewer_comment="qa_reason=metric_unit_mismatch",
    )
    assert _blocked_sample_keys([blocked]) == {("P0001", "S-1")}

    fact_level_checklist = CandidateRecord(
        project_id=1,
        source_paper_id=1,
        sample_id="S-2",
        review_status="pending",
        reviewer_comment=(
            "qa_reason=checklist_failed;"
            "checklist:characterization_peak_in_core_table"
        ),
    )
    assert _blocked_sample_keys([fact_level_checklist]) == set()

    verified = SampleCatalog(
        paper_id=1,
        project_id=1,
        sample_id="S-1",
        confidence=0.88,
    )
    inferred = SampleCatalog(
        paper_id=1,
        project_id=1,
        sample_id="S-inferred",
        confidence=0.45,
    )
    assert _verified_sample_keys(
        [verified, inferred],
        {1: "P0001"},
    ) == {("P0001", "S-1")}

    eligible_fact = FactCandidate(
        paper_id=1,
        project_id=1,
        fact_id="F-good",
        fact_type="performance",
        metric_or_parameter="tensile_strength",
        value="800",
        unit="MPa",
        evidence_text="S-1 reached a tensile strength of 800 MPa.",
        source_location="p.5",
        assigned_sample_id="S-1",
        assignment_status="assigned",
    )
    assert _externally_eligible_fact(eligible_fact) is True
    bad_identity_fact = FactCandidate(
        paper_id=1,
        project_id=1,
        fact_id="F-bad",
        fact_type="structure",
        metric_or_parameter="xps_peak_1",
        value="781",
        unit="eV",
        condition="checklist:sample_id_not_found_in_evidence",
        evidence_text="The peak was observed at 781 eV.",
        source_location="p.2",
        assigned_sample_id="S-1",
        assignment_status="assigned",
    )
    assert _externally_eligible_fact(bad_identity_fact) is False

    mislabeled_xps_fact = FactCandidate(
        paper_id=1,
        project_id=1,
        fact_id="F-fake-xps",
        fact_type="performance",
        metric_or_parameter="xps_peak_1",
        value="0.160",
        unit="eV",
        method="temperature-dependent conductivity",
        evidence_text=(
            "The thermal activation energy of carrier hopping was 0.160 eV."
        ),
        source_location="p.7, Table 1",
        assigned_sample_id="S-1",
        assignment_status="assigned",
    )
    assert _externally_eligible_fact(mislabeled_xps_fact) is False

    grounded_xps_fact = FactCandidate(
        paper_id=1,
        project_id=1,
        fact_id="F-real-xps",
        fact_type="structure",
        metric_or_parameter="xps_peak_1",
        value="781",
        unit="eV",
        method="XPS",
        evidence_text=(
            "X-ray photoelectron spectroscopy showed an XPS peak at 781 eV."
        ),
        source_location="p.4, Figure 2",
        assigned_sample_id="S-1",
        assignment_status="assigned",
    )
    assert _externally_eligible_fact(grounded_xps_fact) is True

    composite_only_fact = FactCandidate(
        paper_id=1,
        project_id=1,
        fact_id="F-composite-only",
        fact_type="structure",
        metric_or_parameter="raman_peak_1",
        value="98.4",
        unit="cm^-1",
        evidence_text="The Co(OH)2/Bi composite had a peak at 98.4 cm^-1.",
        source_location="p.2",
        assigned_sample_id="Co(OH)2",
        assignment_status="assigned",
    )
    assert _externally_eligible_fact(composite_only_fact) is False

    alias_fact = FactCandidate(
        paper_id=1,
        project_id=1,
        fact_id="F-alias",
        fact_type="structure",
        metric_or_parameter="particle_size",
        value="15",
        unit="nm",
        evidence_text="Sb-doped Cs4InCl7 NCs had an average size of 15 nm.",
        source_location="p.2",
        assigned_sample_id="Cs4InCl7:Sb NC",
        assignment_status="assigned",
    )
    assert _externally_eligible_fact(
        alias_fact,
        sample_names=("Cs4InCl7:Sb NC", "Sb-doped Cs4InCl7 NCs"),
    ) is True

    cross_wired_suffix_fact = FactCandidate(
        paper_id=1,
        project_id=1,
        fact_id="F-cross-wired-suffix",
        fact_type="performance",
        metric_or_parameter="surface_temperature",
        value="76",
        unit="°C",
        evidence_text=(
            "The surface temperature of 20% AF/PVA fabrics had a "
            "temperature difference of 76 °C.\n"
            "[sample card evidence] 80% AF/PVA aerogel fiber: "
            "AF content 80%."
        ),
        source_location="p.8",
        assigned_sample_id="80% AF/PVA aerogel fiber",
        assignment_status="assigned",
    )
    assert _externally_eligible_fact(
        cross_wired_suffix_fact,
        sample_names=(
            "80% AF/PVA aerogel fiber",
            "80% AF/PVA",
        ),
        paper_sample_names=(
            "20% AF/PVA aerogel fiber",
            "20% AF/PVA",
            "80% AF/PVA aerogel fiber",
            "80% AF/PVA",
        ),
    ) is False

    paired_comparison_fact = FactCandidate(
        paper_id=1,
        project_id=1,
        fact_id="F-paired-comparison",
        fact_type="performance",
        metric_or_parameter="tensile_strength",
        value="8.51",
        unit="MPa",
        evidence_text=(
            "The strengths of 20% AF/PVA and 40% AF/PVA were "
            "6.09 and 8.51 MPa, respectively."
        ),
        source_location="p.5",
        assigned_sample_id="40% AF/PVA aerogel fiber",
        assignment_status="assigned",
    )
    assert _externally_eligible_fact(
        paired_comparison_fact,
        sample_names=(
            "40% AF/PVA aerogel fiber",
            "40% AF/PVA",
        ),
        paper_sample_names=(
            "20% AF/PVA",
            "40% AF/PVA",
        ),
    ) is True

    additive_process_on_pure_control = FactCandidate(
        paper_id=1,
        project_id=1,
        fact_id="F-pure-control-cross-wire",
        fact_type="process",
        metric_or_parameter="premixing",
        value="10",
        unit="s",
        evidence_text=(
            "The HAp and PCL powders were premixed for 10 s before extrusion."
        ),
        source_location="p.2",
        assigned_sample_id="poPCL filament",
        assignment_status="assigned",
    )
    assert _externally_eligible_fact(
        additive_process_on_pure_control,
        sample_names=("poPCL filament", "poPCL"),
        sample_composition="100 wt% PCL; HAp content=0 wt%",
    ) is False


@pytest.mark.asyncio
async def test_project_material_chain_batch_is_ordered_and_deterministic():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as session:
        project = Project(name="Atomic material delivery")
        session.add(project)
        await session.flush()
        paper = Paper(
            project_id=project.id,
            original_filename="paper.pdf",
            file_object_key="1/paper.pdf",
            paper_title="Atomic material paper",
            doi_or_url="10.1000/atomic",
            year=2026,
            journal="Journal of Atomic Materials",
            status="review",
        )
        session.add(paper)
        await session.flush()
        session.add(
            CandidateRecord(
                project_id=project.id,
                source_paper_id=paper.id,
                record_id="R-1",
                paper_id_str="P-ATOMIC",
                sample_id="S-1",
                sample_group_id="G-1",
                material_system="PAN/SiO2",
                fiber_type="electrospun nanofiber",
                matrix_name="PAN",
                matrix_content="95",
                matrix_unit="wt%",
                spinning_method="electrospinning",
                process_route="solution preparation → electrospinning",
                process_parameters=(
                    "polymer_concentration=10 wt%; voltage=20 kV"
                ),
                structure_methods="SEM",
                structure_features="uniform nanofiber morphology",
                structure_evidence="SEM shows a uniform nanofiber morphology.",
                review_status="approved",
            )
        )
        session.add(
            SampleCatalog(
                paper_id=paper.id,
                project_id=project.id,
                sample_id="S-1",
                sample_group_id="G-1",
                material_system="PAN/SiO2",
                fiber_type="electrospun nanofiber",
                confidence=0.97,
            )
        )
        session.add(
            FactCandidate(
                paper_id=paper.id,
                project_id=project.id,
                fact_id="F-1",
                fact_type="performance",
                metric_or_parameter="tensile_strength",
                value="800",
                unit="MPa",
                method="uniaxial tensile test",
                condition="standard deviation=± 25 MPa",
                evidence_text="The tensile strength was 800 MPa.",
                source_location="p.5, Figure 3",
                source_page=5,
                source_block_id="B-3",
                assigned_sample_id="S-1",
                assignment_status="assigned",
                confidence=0.97,
            )
        )
        session.add(
            FactCandidate(
                paper_id=paper.id,
                project_id=project.id,
                fact_id="F-2",
                fact_type="structure",
                metric_or_parameter="fiber_diameter",
                value="500",
                unit="nm",
                method="SEM",
                evidence_text="SEM shows fibers with a diameter of 500 nm.",
                source_location="p.4, Figure 2",
                source_page=4,
                source_block_id="B-2",
                assigned_sample_id="S-1",
                assignment_status="assigned",
                confidence=0.96,
            )
        )
        await session.commit()

        first = await build_project_material_fact_artifact(
            session,
            project_id=project.id,
            binding=_binding(),
            paper_ids=None,
            include_unmapped=False,
        )
        second = await build_project_material_fact_artifact(
            session,
            project_id=project.id,
            binding=_binding(),
            paper_ids=None,
            include_unmapped=False,
        )

    await engine.dispose()
    assert first.content == second.content
    assert first.batch_sha256 == second.batch_sha256
    assert first.summary["schema_version"] == "ai4s_material_chain_v0.3.2"
    assert first.summary["dataset_id"] == str(DATASET_ID)
    assert first.summary["template_id"] == str(TEMPLATE_ID)
    assert first.summary["record_count"] == 1
    assert first.summary["sample_count"] == 1
    assert first.summary["input_sample_count"] == 1
    assert first.summary["excluded_blocked_sample_count"] == 0
    assert first.summary["excluded_unverified_sample_count"] == 0
    assert first.summary["excluded_semantic_sample_count"] == 0
    assert first.summary["excluded_incomplete_sample_count"] == 0
    assert first.summary["excluded_fact_count"] == 0
    assert first.summary["quality_gate"] == "strict_complete_material_chain_v4"
    assert first.summary["delivered_paper_ids"] == [paper.id]
    assert first.paper_ids == [paper.id]
    assert first.summary["domain_counts"] == {
        "成分": 1,
        "工艺": 1,
        "结构": 1,
        "性能": 1,
    }

    batch = json.loads(first.content)
    assert batch["dataset"]["_id"] == DATASET_ID
    assert batch["template"]["_id"] == TEMPLATE_ID
    record = batch["data"][0]
    object_group = record["content"]["object"]["文献、样品与成分"]
    process_group = record["content"]["operations"][0]["工艺"]
    result_group = record["content"]["results"][0]["结构、性能与来源"]
    assert object_group["具体材料对象或样品编号"] == "S-1"
    assert object_group["原料、前驱体或基体"] == "PAN"
    assert "electrospinning" in process_group["工艺路线"]
    assert "polymer concentration=10 wt%" in (
        process_group["关键工艺参数"]
    )
    assert "voltage=20 kV" in process_group["关键工艺参数"]
    assert result_group["性能指标名称"] == "拉伸强度"
    assert result_group["性能数值"] == "拉伸强度=800 ± 25 MPa"
    forbidden = {
        "事实类别",
        "原始值",
        "最小值",
        "最大值",
        "置信度",
        "映射版本",
        "抽取状态",
    }
    all_fields = set(object_group) | set(process_group) | set(result_group)
    assert forbidden.isdisjoint(all_fields)
