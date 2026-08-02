"""Build deterministic ordered material-chain batches for the platform.

The platform's exporter ignores field order for sibling flat blocks and drops
some sibling result sections.  The verified v0.3.2 path therefore uploads one
record per actual sample through three ordered ``t=9`` groups.  The platform's
native Excel then follows the same literature/sample → composition → process →
structure → performance → evidence order as the readable AI4S workbook.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate_record import CandidateRecord
from app.models.document_parse import DocumentBlock
from app.models.evidence_item import EvidenceItem
from app.models.fact_candidate import FactCandidate
from app.models.paper import Paper
from app.models.sample_catalog import SampleCatalog
from app.services.grouping import is_material_sample_id
from app.services.material_data_model import (
    DOMAIN_COMPOSITION,
    DOMAIN_PERFORMANCE,
    DOMAIN_PROCESS,
    DOMAIN_STRUCTURE,
    MaterialDataset,
    build_material_dataset,
)
from app.services.platform_delivery import (
    ELIGIBLE_PAPER_STATUSES,
    LoadedPlatformBinding,
    PlatformDeliveryError,
    ProjectBatchArtifact,
)
from app.services.platform_material_chain_adapter import (
    LOCAL_OBJECT_FIELDS,
    LOCAL_EVIDENCE_FIELDS,
    LOCAL_PERFORMANCE_FIELDS,
    LOCAL_PROCESS_FIELDS,
    LOCAL_STRUCTURE_FIELDS,
    MATERIAL_CHAIN_SCHEMA_VERSION,
    MaterialChainAdapterError,
    build_material_chain_batch,
    dumps_material_chain_batch,
    validate_material_chain_template,
)
from app.services.validation import is_characterization_peak_metric
from app.services.workbook_export import build_material_chain_rows


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DELIVERABLE_REVIEW_STATUSES = {
    "approved",
    "pending",
    "uncertain",
    "modified",
    "通过",
    "待审核",
    "存疑",
    "已修改",
}
_BLOCKING_QA_TOKENS = {
    "alignment_review_required",
    "condition_parameter",
    "metric_unit_mismatch",
    "rough_source_location",
    "sample_id_not_found_in_evidence",
}
_BLOCKING_REVIEW_STATUSES = {"uncertain", "存疑"}
_BLOCKING_FACT_TOKENS = {
    "alignment_review_required",
    "condition_parameter",
    "metric_unit_mismatch",
    "rough_source_location",
    "sample_id_not_found_in_evidence",
}
_MIN_PLATFORM_SAMPLE_CONFIDENCE = 0.70
_TECHNIQUE_REQUIREMENTS = (
    (
        re.compile(r"(?:^|[_\s-])xps(?:[_\s-]|$)", re.IGNORECASE),
        re.compile(
            r"\bxps\b|x[-\s]?ray\s+photoelectron|"
            r"photoelectron\s+spectroscop",
            re.IGNORECASE,
        ),
    ),
    (
        re.compile(r"(?:^|[_\s-])xrd(?:[_\s-]|$)", re.IGNORECASE),
        re.compile(
            r"\bxrd\b|x[-\s]?ray\s+diffraction",
            re.IGNORECASE,
        ),
    ),
    (
        re.compile(r"(?:^|[_\s-])ftir(?:[_\s-]|$)", re.IGNORECASE),
        re.compile(
            r"\bftir\b|fourier[-\s]+transform\s+infrared",
            re.IGNORECASE,
        ),
    ),
    (
        re.compile(r"(?:^|[_\s-])raman(?:[_\s-]|$)", re.IGNORECASE),
        re.compile(r"\braman\b", re.IGNORECASE),
    ),
    (
        re.compile(r"(?:^|[_\s-])nmr(?:[_\s-]|$)", re.IGNORECASE),
        re.compile(
            r"\bnmr\b|nuclear\s+magnetic\s+resonance",
            re.IGNORECASE,
        ),
    ),
)
_REFERENCE_OR_MEDIUM_SAMPLE_RE = re.compile(
    r"^(?:"
    r"pure\s+water|water|seawater|deionized\s+water|distilled\s+water|"
    r"milli[-\s]?q\s+water|di\s+water|blank|control|reference|air|"
    r"纯水|水|海水|去离子水|蒸馏水|空白|对照|参比|空气|缓冲液|溶剂"
    r")(?:\s*[\(\（][^\)\）]*[\)\）])?$",
    re.IGNORECASE,
)
_CHARACTERIZATION_PROCESS_RE = re.compile(
    r"\b(?:lf[-\s]?nmr|nmr|dsc|xrd|xps|ftir|raman|sem|tem|afm|"
    r"tga|dma|spectroscopy|microscopy|characteri[sz]ation|measurement|"
    r"tensile\s+test|electrical\s+test)\b|"
    r"核磁|差示扫描量热|衍射|光谱|显微|表征|测试|测量",
    re.IGNORECASE,
)
_MANUFACTURING_PROCESS_RE = re.compile(
    r"\b(?:spin(?:ning)?|electrospin(?:ning)?|wet[-\s]?spin(?:ning)?|"
    r"melt[-\s]?spin(?:ning)?|extrud(?:e|ing|ed)|draw(?:ing|n)?|"
    r"coagulat(?:e|ion|ing)|anneal(?:ing|ed)?|heat[-\s]?treat(?:ment|ed)?|"
    r"calcination|carboni[sz]ation|pyrolysis|deposit(?:ion|ed|ing)?|"
    r"coat(?:ing|ed)?|spray(?:ing|ed)?|etch(?:ing|ed)?|filtrat(?:ion|ed)|"
    r"papermaking|weav(?:e|ing)|knit(?:ting|ted)?|polymeri[sz]ation|"
    r"crosslink(?:ing|ed)?|cur(?:e|ing|ed)|dry(?:ing|ied)?|"
    r"hydrothermal|solvothermal|mix(?:ing|ed)?|stirr(?:ing|ed)?|"
    r"dissolv(?:e|ing|ed)|synthesi[sz](?:e|ed|ing)?|fabricat(?:e|ion|ed)|"
    r"assembl(?:y|e|ed|ing)|infiltrat(?:e|ion|ed)|impregnat(?:e|ion|ed)|"
    r"press(?:ing|ed)?|wash(?:ing|ed)?|reduc(?:e|tion|ed)|"
    r"oxidi[sz](?:e|ation|ed)|print(?:ing|ed)?|cast(?:ing|ed)?|"
    r"mold(?:ing|ed)?|grind(?:ing|ed)?|mill(?:ing|ed)?|"
    r"sonicat(?:e|ion|ed)|hydroly[sz](?:e|is|ed)|"
    r"evaporat(?:e|ion|ed)|sinter(?:ing|ed)?|freeze[-\s]?dry(?:ing|ied)?)\b|"
    r"纺丝|挤出|拉伸|凝固|退火|热处理|煅烧|碳化|热解|沉积|涂覆|喷涂|"
    r"刻蚀|过滤|造纸|编织|针织|聚合|交联|固化|干燥|水热|溶剂热|混合|"
    r"搅拌|溶解|合成|制备|组装|浸渍|压制|清洗|还原|氧化|打印|浇铸|"
    r"模塑|研磨|球磨|超声|水解|蒸发|烧结|冻干",
    re.IGNORECASE,
)
_TABLE_ARTIFACT_RE = re.compile(
    r"(?:^|[；;\s])samples(?:$|[；;\s])|"
    r"\b(?:debonding[\s_]+temperature|lap[\s_]+shear[\s_]+strength|"
    r"soluble[\s_]+fraction|swelling[\s_]+degree)[a-e]\b|"
    r"\bt\s+g\s+[a-e]\b|"
    r"(?:^|[；;\s])[+-]?\d+(?:\.\d+)?e100(?:$|[；;\s])",
    re.IGNORECASE,
)
_NEGATED_COMPONENT_RE = re.compile(
    r"\b(?:without|in\s+the\s+absence\s+of|absence\s+of|no)\s+"
    r"(?P<target>co[-\s]?reactants?|cor|additives?|fillers?|dopants?|"
    r"crosslinkers?|coatings?|shells?)\b",
    re.IGNORECASE,
)
_BASE_PARTICLE_SAMPLE_RE = re.compile(
    r"\b(?:qds?|nanoparticles?|particles?)\s*$",
    re.IGNORECASE,
)
_DERIVATIVE_PROCESS_RE = re.compile(
    r"\bfollowed\s+by\b.{0,240}\b(?:encapsulat(?:e|ed|ion)|"
    r"coat(?:ing|ed)?|shell|crosslink(?:ing|ed)?|"
    r"functionali[sz](?:e|ed|ation)|dop(?:e|ed|ing))\b",
    re.IGNORECASE,
)
_DERIVATIVE_SAMPLE_MARKER_RE = re.compile(
    r"@|/|\b(?:coat(?:ed|ing)?|encapsulat(?:ed|ion)|core[-\s]?shell|"
    r"shell|crosslink(?:ed|ing)?|functionali[sz](?:ed|ation)|"
    r"dop(?:ed|ing))\b",
    re.IGNORECASE,
)


def load_material_fact_binding(
    *,
    template_path: str,
    expected_sha256: str,
    expected_dataset_id: int,
    expected_template_id: int,
    dataset_name: str,
) -> LoadedPlatformBinding:
    """Load and pin the ordered schema to verified platform identifiers."""

    path = Path(template_path).expanduser().resolve(strict=False)
    if not path.is_file():
        raise PlatformDeliveryError(f"材料数据链模板不存在: {path}")
    content = path.read_bytes()
    actual_sha256 = hashlib.sha256(content).hexdigest()
    expected = expected_sha256.strip().lower()
    if not _SHA256_RE.fullmatch(expected):
        raise PlatformDeliveryError("材料数据链模板 SHA-256 配置无效")
    if actual_sha256 != expected:
        raise PlatformDeliveryError(
            "材料数据链模板 SHA-256 不匹配，已拒绝继续导入"
        )
    if expected_dataset_id <= 0 or expected_template_id <= 0:
        raise PlatformDeliveryError("平台数据集 ID 和模板 ID 必须为正整数")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlatformDeliveryError(
            "材料数据链模板不是有效的 UTF-8 JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise PlatformDeliveryError("材料数据链模板根节点必须是 JSON 对象")
    try:
        validate_material_chain_template(payload)
    except MaterialChainAdapterError as exc:
        raise PlatformDeliveryError(str(exc)) from exc

    template = payload.get("template")
    if not isinstance(template, dict):
        raise PlatformDeliveryError("材料数据链模板缺少 template")
    template["_id"] = expected_template_id
    payload["dataset"] = {
        "_id": expected_dataset_id,
        "name": dataset_name,
    }
    return LoadedPlatformBinding(
        template=payload,
        template_path=path,
        template_sha256=actual_sha256,
        dataset_id=expected_dataset_id,
        template_id=expected_template_id,
    )


async def build_project_material_fact_artifact(
    db: AsyncSession,
    *,
    project_id: int,
    binding: LoadedPlatformBinding,
    paper_ids: list[int] | None,
    include_unmapped: bool,
) -> ProjectBatchArtifact:
    """Build an upload-ready sample-wide material-chain batch."""

    papers = await _selected_papers(
        db,
        project_id=project_id,
        paper_ids=paper_ids,
    )
    selected_ids = [paper.id for paper in papers]

    records = list(
        (
            await db.execute(
                select(CandidateRecord)
                .where(
                    CandidateRecord.project_id == project_id,
                    CandidateRecord.source_paper_id.in_(selected_ids),
                    CandidateRecord.review_status.in_(
                        sorted(_DELIVERABLE_REVIEW_STATUSES)
                    ),
                )
                .order_by(CandidateRecord.id)
            )
        )
        .scalars()
        .all()
    )
    if not records:
        raise PlatformDeliveryError(
            "所选论文没有可交付的成分、工艺、结构或性能数据"
        )

    fact_candidates = await _ordered_rows(
        db,
        select(FactCandidate)
        .where(FactCandidate.paper_id.in_(selected_ids))
        .order_by(FactCandidate.id),
    )
    sample_catalogs = await _ordered_rows(
        db,
        select(SampleCatalog)
        .where(SampleCatalog.paper_id.in_(selected_ids))
        .order_by(SampleCatalog.id),
    )
    sample_names = _sample_names_by_key(sample_catalogs)
    sample_composition_by_key = {
        (int(catalog.paper_id), str(catalog.sample_id or "").strip()): " ".join(
            value
            for value in (
                str(catalog.composition_expression or "").strip(),
                (
                    f"{str(catalog.variable_name or '').strip()}="
                    f"{str(catalog.variable_value or '').strip()} "
                    f"{str(catalog.variable_unit or '').strip()}"
                ).strip("= "),
            )
            if value
        )
        for catalog in sample_catalogs
        if str(catalog.sample_id or "").strip()
    }
    paper_sample_names: dict[int, tuple[str, ...]] = {}
    for (paper_id, _sample_id), names in sample_names.items():
        paper_sample_names[paper_id] = tuple(
            dict.fromkeys((*paper_sample_names.get(paper_id, ()), *names))
        )
    eligible_fact_candidates = [
        fact
        for fact in fact_candidates
        if _externally_eligible_fact(
            fact,
            sample_names=sample_names.get(
                (
                    int(fact.paper_id),
                    str(fact.assigned_sample_id or "").strip(),
                ),
                (),
            ),
            paper_sample_names=paper_sample_names.get(
                int(fact.paper_id),
                (),
            ),
            sample_composition=sample_composition_by_key.get(
                (
                    int(fact.paper_id),
                    str(fact.assigned_sample_id or "").strip(),
                ),
                "",
            ),
        )
    ]
    excluded_fact_count = (
        len(fact_candidates) - len(eligible_fact_candidates)
    )
    evidence_items = await _ordered_rows(
        db,
        select(EvidenceItem)
        .where(EvidenceItem.paper_id.in_(selected_ids))
        .order_by(EvidenceItem.id),
    )
    document_blocks = await _ordered_rows(
        db,
        select(DocumentBlock)
        .where(DocumentBlock.paper_id.in_(selected_ids))
        .order_by(
            DocumentBlock.paper_id,
            DocumentBlock.page_number,
            DocumentBlock.order_index,
            DocumentBlock.id,
        ),
    )

    material_dataset = build_material_dataset(
        records=records,
        papers=papers,
        fact_candidates=eligible_fact_candidates,
        sample_catalogs=sample_catalogs,
        evidence_items=evidence_items,
        document_blocks=document_blocks,
    )
    material_rows = build_material_chain_rows(material_dataset)
    raw_sample_count = len(material_rows)
    blocked_sample_keys = _blocked_sample_keys(records)
    unblocked_rows = [
        row
        for row in material_rows
        if _material_row_key(row) not in blocked_sample_keys
    ]
    blocked_sample_count = raw_sample_count - len(unblocked_rows)
    paper_business_id_by_db = {
        paper.id: _paper_business_id(paper, records)
        for paper in papers
    }
    verified_sample_keys = _verified_sample_keys(
        sample_catalogs,
        paper_business_id_by_db,
    )
    verified_rows = [
        row
        for row in unblocked_rows
        if _material_row_key(row) in verified_sample_keys
    ]
    unverified_sample_count = len(unblocked_rows) - len(verified_rows)
    semantic_rows = [
        row for row in verified_rows if _semantically_valid_material_row(row)
    ]
    semantic_sample_count = len(verified_rows) - len(semantic_rows)
    material_rows = [
        row for row in semantic_rows if _complete_material_chain_row(row)
    ]
    incomplete_sample_count = len(semantic_rows) - len(material_rows)
    if not material_rows:
        raise PlatformDeliveryError(
            "严格质量闸门未发现可交付样品：每条平台记录必须同时具备"
            "成分、工艺、结构、性能、证据与来源，且不能包含阻断级 QA；"
            "低置信度样品、介质、空白对照、表格脚注污染和非制备工艺"
            "不会作为材料样品交付；"
            f"候选样品 {raw_sample_count}，阻断 {blocked_sample_count}，"
            f"样品身份未核验 {unverified_sample_count}，"
            f"非材料或伪工艺 {semantic_sample_count}，"
            f"材料链不完整 {incomplete_sample_count}，"
            f"证据或归属不合格事实 {excluded_fact_count}"
        )

    paper_id_by_business_id = {
        business_id: paper_id
        for paper_id, business_id in paper_business_id_by_db.items()
    }
    delivered_paper_ids = sorted({
        paper_id_by_business_id[business_id]
        for row in material_rows
        if (
            (business_id := str(row.get("文献编号") or "").strip())
            in paper_id_by_business_id
        )
    })
    if not delivered_paper_ids:
        raise PlatformDeliveryError("严格质量闸门无法确定可交付记录所属论文")
    try:
        batch = build_material_chain_batch(
            binding.template,
            material_rows,
        )
        text = dumps_material_chain_batch(batch)
    except MaterialChainAdapterError as exc:
        raise PlatformDeliveryError(str(exc)) from exc

    content = text.encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    domain_counts = {
        "成分": _rows_with_any(material_rows, LOCAL_OBJECT_FIELDS[9:]),
        "工艺": _rows_with_any(material_rows, LOCAL_PROCESS_FIELDS),
        "结构": _rows_with_any(material_rows, LOCAL_STRUCTURE_FIELDS),
        "性能": _rows_with_any(material_rows, LOCAL_PERFORMANCE_FIELDS),
    }
    summary = {
        "schema_version": MATERIAL_CHAIN_SCHEMA_VERSION,
        "project_id": project_id,
        "paper_count": len(delivered_paper_ids),
        "delivered_paper_ids": delivered_paper_ids,
        "input_paper_count": len(selected_ids),
        "sample_count": len(material_rows),
        "input_sample_count": raw_sample_count,
        "excluded_blocked_sample_count": blocked_sample_count,
        "excluded_unverified_sample_count": unverified_sample_count,
        "excluded_semantic_sample_count": semantic_sample_count,
        "excluded_incomplete_sample_count": incomplete_sample_count,
        "excluded_fact_count": excluded_fact_count,
        "quality_gate": "strict_complete_material_chain_v4",
        "record_count": len(batch["data"]),
        "domain_counts": domain_counts,
        "domain_count_semantics": "sample_coverage",
        # Platform Snowflake-like IDs exceed JavaScript's safe integer range.
        # Keep them as decimal strings in API summaries so the browser cannot
        # silently round the identifiers.
        "dataset_id": str(binding.dataset_id),
        "template_id": str(binding.template_id),
        "batch_template_sha256": binding.template_sha256,
        "batch_sha256": digest,
        "bytes": len(content),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return ProjectBatchArtifact(
        content=content,
        batch_sha256=digest,
        filename=(
            f"ai4s_material_chain_v032_project_{project_id}_{digest[:12]}.json"
        ),
        summary=summary,
        # Keep the original selection scope so preflight → import rebuilds the
        # exact same artifact even when only a subset contributes valid rows.
        paper_ids=selected_ids,
    )


def _rows_with_any(
    rows: list[dict[str, Any]],
    fields: tuple[str, ...],
) -> int:
    return sum(
        any(str(row.get(field) or "").strip() for field in fields)
        for row in rows
    )


def _material_row_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("文献编号") or "").strip(),
        str(row.get("具体材料对象|样品编号") or "").strip(),
    )


def _blocked_sample_keys(
    records: Iterable[CandidateRecord],
) -> set[tuple[str, str]]:
    blocked: set[tuple[str, str]] = set()
    for record in records:
        paper_id = (
            str(record.paper_id_str or "").strip()
            or f"P{int(record.source_paper_id):04d}"
        )
        sample_id = str(record.sample_id or "").strip()
        if not paper_id or not sample_id:
            continue
        review_status = str(record.review_status or "").strip().lower()
        comment = str(record.reviewer_comment or "").lower()
        if (
            review_status in _BLOCKING_REVIEW_STATUSES
            or any(token in comment for token in _BLOCKING_QA_TOKENS)
        ):
            blocked.add((paper_id, sample_id))
    return blocked


def _verified_sample_keys(
    catalogs: Iterable[SampleCatalog],
    paper_business_id_by_db: dict[int, str],
) -> set[tuple[str, str]]:
    """Return sample identities that are reliable enough for external delivery."""

    verified: set[tuple[str, str]] = set()
    for catalog in catalogs:
        paper_id = paper_business_id_by_db.get(int(catalog.paper_id), "")
        sample_id = str(catalog.sample_id or "").strip()
        try:
            confidence = float(catalog.confidence)
        except (TypeError, ValueError):
            confidence = 0.0
        if (
            paper_id
            and sample_id
            and confidence >= _MIN_PLATFORM_SAMPLE_CONFIDENCE
        ):
            verified.add((paper_id, sample_id))
    return verified


def _sample_names_by_key(
    catalogs: Iterable[SampleCatalog],
) -> dict[tuple[int, str], tuple[str, ...]]:
    names_by_key: dict[tuple[int, str], tuple[str, ...]] = {}
    for catalog in catalogs:
        sample_id = str(catalog.sample_id or "").strip()
        if not sample_id:
            continue
        names = [sample_id]
        raw_aliases = str(catalog.sample_aliases or "").strip()
        if raw_aliases:
            try:
                aliases = json.loads(raw_aliases)
            except json.JSONDecodeError:
                aliases = [raw_aliases]
            if isinstance(aliases, list):
                names.extend(
                    str(alias).strip()
                    for alias in aliases
                    if str(alias).strip()
                )
        names_by_key[(int(catalog.paper_id), sample_id)] = tuple(
            dict.fromkeys(names)
        )
    return names_by_key


def _sample_name_in_evidence(sample_name: str, evidence: str) -> bool:
    escaped = re.escape(sample_name).replace(r"\ ", r"\s+")
    return bool(
        re.search(
            rf"(?<![\w/]){escaped}(?![\w/])",
            evidence,
            flags=re.IGNORECASE,
        )
    )


def _sample_name_occurs(sample_name: str, evidence: str) -> bool:
    escaped = re.escape(sample_name).replace(r"\ ", r"\s+")
    return bool(re.search(escaped, evidence, flags=re.IGNORECASE))


def _metric_technique_is_grounded(
    metric: str,
    *,
    method: str,
    evidence: str,
) -> bool:
    """Reject technique-specific placeholder metrics without that technique."""

    support_text = " ".join((method, evidence))
    for metric_pattern, evidence_pattern in _TECHNIQUE_REQUIREMENTS:
        if metric_pattern.search(metric):
            return bool(evidence_pattern.search(support_text))
    return True


def _externally_eligible_fact(
    fact: FactCandidate,
    *,
    sample_names: Iterable[str] = (),
    paper_sample_names: Iterable[str] = (),
    sample_composition: str = "",
) -> bool:
    """Drop facts whose identity or evidence is unsafe for external delivery."""

    assigned_sample_id = str(fact.assigned_sample_id or "").strip()
    assignment_status = str(fact.assignment_status or "").strip().casefold()
    evidence = str(fact.evidence_text or "").strip()
    source_location = str(fact.source_location or "").strip()
    if (
        not assigned_sample_id
        or assignment_status != "assigned"
        or not evidence
        or not source_location
    ):
        return False
    names = tuple(sample_names) or (assigned_sample_id,)
    has_exact_name = any(
        _sample_name_in_evidence(sample_name, evidence)
        for sample_name in names
    )
    # A table cell may inherit its sample identity from a nearby header, so
    # absence of the name alone is not a blocker.  However, if the assigned
    # name appears only as the prefix of a different composite identity
    # (Co(OH)2 inside Co(OH)2/Bi), the attribution is demonstrably unsafe.
    if not has_exact_name and any(
        _sample_name_occurs(sample_name, evidence)
        for sample_name in names
    ):
        return False
    # Holistic facts sometimes append a synthetic ``sample card evidence``
    # suffix after the actual source quote.  That suffix must not be allowed
    # to mask a cross-wired source sentence that explicitly names another
    # sample.  Comparisons remain valid when the primary quote also names the
    # assigned sample (for example "20% and 40% were 6.09 and 8.51 MPa").
    primary_evidence = re.split(
        r"\[\s*sample\s+card\s+evidence\s*\]",
        evidence,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    current_in_primary = any(
        _sample_name_in_evidence(sample_name, primary_evidence)
        for sample_name in names
    )
    current_keys = {
        re.sub(r"\s+", " ", sample_name).strip().casefold()
        for sample_name in names
        if str(sample_name).strip()
    }
    foreign_in_primary = any(
        (
            re.sub(r"\s+", " ", other_name).strip().casefold()
            not in current_keys
        )
        and _sample_name_in_evidence(other_name, primary_evidence)
        for other_name in paper_sample_names
        if str(other_name).strip()
    )
    if foreign_in_primary and not current_in_primary:
        return False
    if str(fact.fact_type or "").strip().casefold() == "process":
        zero_components = {
            match.group("component").casefold()
            for match in re.finditer(
                r"(?<![\w])"
                r"(?P<component>[A-Za-z][A-Za-z0-9.+-]{1,24})\s+"
                r"(?:content|loading|fraction)\s*=\s*"
                r"0(?:\.0+)?\s*(?:wt\.?\s*%|vol\.?\s*%|at\.?\s*%|%)?",
                sample_composition,
                flags=re.IGNORECASE,
            )
        }
        if any(
            re.search(
                rf"(?<![\w]){re.escape(component)}(?![\w])",
                primary_evidence,
                flags=re.IGNORECASE,
            )
            for component in zero_components
        ):
            return False
    if not _metric_technique_is_grounded(
        str(fact.metric_or_parameter or ""),
        method=str(fact.method or ""),
        evidence=evidence,
    ):
        return False
    quality_text = " ".join(
        (
            str(fact.condition or ""),
            str(fact.method or ""),
        )
    ).casefold()
    return not any(token in quality_text for token in _BLOCKING_FACT_TOKENS)


def _paper_business_id(
    paper: Paper,
    records: Iterable[CandidateRecord],
) -> str:
    for record in records:
        if (
            record.source_paper_id == paper.id
            and str(record.paper_id_str or "").strip()
        ):
            return str(record.paper_id_str).strip()
    return f"P{paper.id:04d}"


def _semantically_valid_material_row(row: dict[str, Any]) -> bool:
    sample_id = str(row.get("具体材料对象|样品编号") or "").strip()
    if (
        not sample_id
        or not is_material_sample_id(sample_id)
        or _REFERENCE_OR_MEDIUM_SAMPLE_RE.fullmatch(sample_id)
    ):
        return False
    process_text = "；".join(
        str(row.get(field) or "").strip()
        for field in LOCAL_PROCESS_FIELDS
        if str(row.get(field) or "").strip()
    )
    if not process_text or not _MANUFACTURING_PROCESS_RE.search(process_text):
        return False
    if (
        _CHARACTERIZATION_PROCESS_RE.search(process_text)
        and not _MANUFACTURING_PROCESS_RE.search(process_text)
    ):
        return False
    if not _sample_scope_is_consistent(
        row,
        sample_id=sample_id,
        process_text=process_text,
    ):
        return False
    artifact_text = "；".join(
        str(row.get(field) or "").strip()
        for field in (
            "结构指标名称",
            "结构数值",
            "性能指标名称",
            "性能数值",
        )
        if str(row.get(field) or "").strip()
    )
    if _TABLE_ARTIFACT_RE.search(artifact_text):
        return False
    if any(
        _has_conflicting_measurement_pairs(
            str(row.get(field) or ""),
        )
        for field in ("结构数值", "性能数值")
    ):
        return False
    return True


def _sample_scope_is_consistent(
    row: dict[str, Any],
    *,
    sample_id: str,
    process_text: str,
) -> bool:
    """Reject visibly cross-wired composition/process projections.

    Strict delivery prefers omitting a record over presenting a derivative
    coating or additive route as if it belonged to the unmodified parent
    sample.  The local review data remains unchanged.
    """

    composition_summary = str(row.get("成分配比|浓度") or "")
    positive_projection = "；".join(
        str(row.get(field) or "").strip()
        for field in (
            "原料|前驱体|基体",
            "增强|填料|改性组分",
            "溶剂|助剂",
        )
        if str(row.get(field) or "").strip()
    )
    positive_projection = f"{positive_projection}；{process_text}"
    for match in _NEGATED_COMPONENT_RE.finditer(composition_summary):
        target = match.group("target").casefold()
        if target == "cor" or target.startswith("co-") or target.startswith("co "):
            positive_pattern = re.compile(
                r"\b(?:co[-\s]?reactants?|cor)\b",
                re.IGNORECASE,
            )
        else:
            stem = re.sub(r"(?:s|ings?)$", "", target)
            positive_pattern = re.compile(
                rf"\b{re.escape(stem)}(?:s|ing)?\b",
                re.IGNORECASE,
            )
        if positive_pattern.search(positive_projection):
            return False

    if (
        _BASE_PARTICLE_SAMPLE_RE.search(sample_id)
        and not _DERIVATIVE_SAMPLE_MARKER_RE.search(sample_id)
        and _DERIVATIVE_PROCESS_RE.search(process_text)
    ):
        return False
    return True


def _has_conflicting_measurement_pairs(value: str) -> bool:
    """Reject repeated metric labels whose values cannot be aligned safely."""

    values_by_metric: dict[str, set[str]] = {}
    for item in re.split(r"[;；]", value):
        if "=" not in item:
            continue
        metric, raw_value = item.split("=", 1)
        normalized_metric = re.sub(
            r"[\W_]+",
            "",
            metric,
            flags=re.UNICODE,
        ).casefold()
        normalized_value = re.sub(r"\s+", "", raw_value).casefold()
        if not normalized_metric or not normalized_value:
            continue
        values_by_metric.setdefault(normalized_metric, set()).add(
            normalized_value
        )
    return any(len(values) > 1 for values in values_by_metric.values())


def _complete_material_chain_row(row: dict[str, Any]) -> bool:
    def has_any(fields: tuple[str, ...]) -> bool:
        return any(str(row.get(field) or "").strip() for field in fields)

    performance_metrics = [
        item.strip()
        for item in re.split(
            r"[;；]",
            str(row.get("性能指标名称") or ""),
        )
        if item.strip()
    ]
    has_real_performance = any(
        not is_characterization_peak_metric(metric)
        for metric in performance_metrics
    )
    return all((
        bool(str(row.get("具体材料对象|样品编号") or "").strip()),
        has_any(LOCAL_OBJECT_FIELDS[9:]),
        has_any(LOCAL_PROCESS_FIELDS),
        bool(str(row.get("结构指标名称") or "").strip()),
        has_real_performance,
        bool(str(row.get("性能数值") or "").strip()),
        bool(str(row.get("结果描述|结论") or "").strip()),
        bool(str(row.get("数据来源位置") or "").strip()),
        has_any(LOCAL_EVIDENCE_FIELDS),
    ))


async def _selected_papers(
    db: AsyncSession,
    *,
    project_id: int,
    paper_ids: list[int] | None,
) -> list[Paper]:
    query = select(Paper).where(Paper.project_id == project_id)
    if paper_ids is None:
        query = query.where(Paper.status.in_(sorted(ELIGIBLE_PAPER_STATUSES)))
    else:
        query = query.where(Paper.id.in_(paper_ids))
    papers = list(
        (await db.execute(query.order_by(Paper.id))).scalars().all()
    )
    if paper_ids is not None:
        found = {paper.id for paper in papers}
        missing = sorted(set(paper_ids) - found)
        if missing:
            raise PlatformDeliveryError(
                "以下论文不属于当前项目: " + ", ".join(map(str, missing))
            )
        ineligible = [
            paper.id
            for paper in papers
            if paper.status not in ELIGIBLE_PAPER_STATUSES
        ]
        if ineligible:
            raise PlatformDeliveryError(
                "以下论文尚未完成抽取，不能导入平台: "
                + ", ".join(map(str, ineligible))
            )
    if not papers:
        raise PlatformDeliveryError(
            "项目中没有状态为 review/completed 的已处理论文"
        )
    return papers


async def _ordered_rows(
    db: AsyncSession,
    query: Any,
) -> list[Any]:
    return list((await db.execute(query)).scalars().all())


def material_inputs_from_dataset(
    dataset: MaterialDataset,
    *,
    include_unmapped: bool,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Convert the clean workbook projection into the flat platform inputs."""

    papers = [
        {
            "paper_id": row.get("文献ID"),
            "title": row.get("文献标题"),
            "doi_or_url": row.get("DOI"),
            "year": row.get("发表年份"),
            "journal": row.get("期刊"),
        }
        for row in dataset.papers
    ]
    samples = [
        {
            "id": f"{row.get('文献ID')}::{row.get('样品ID')}",
            "paper_id": row.get("文献ID"),
            "sample_id": row.get("样品ID"),
            "sample_group_id": row.get("样品组"),
            "aliases": row.get("样品别名"),
            "material_system": row.get("材料体系"),
            "material_form": row.get("材料形态"),
        }
        for row in dataset.samples
    ]
    quality_by_fact = _quality_index(dataset.quality)
    facts: list[dict[str, Any]] = []

    for row in dataset.composition:
        raw_value = row.get("原始含量") or "存在"
        facts.append(
            _fact_input(
                row,
                quality_by_fact,
                domain=DOMAIN_COMPOSITION,
                metric=row.get("组分名称"),
                raw_value=raw_value,
                value_number=row.get("数值"),
                range_min=row.get("下限"),
                range_max=row.get("上限"),
                unit=row.get("单位"),
                component_role=row.get("组分角色"),
                condition=_join_text(
                    row.get("条件或说明"),
                    (
                        f"计量基准：{row.get('计量基准')}"
                        if row.get("计量基准")
                        else ""
                    ),
                ),
            )
        )

    for row in dataset.process:
        facts.append(
            _fact_input(
                row,
                quality_by_fact,
                domain=DOMAIN_PROCESS,
                metric=row.get("参数名称"),
                raw_value=row.get("原始值"),
                value_number=row.get("数值"),
                range_min=row.get("下限"),
                range_max=row.get("上限"),
                unit=row.get("单位"),
                process_stage=row.get("工艺阶段"),
                method=row.get("工艺方法"),
                condition=row.get("设备或条件"),
            )
        )

    for rows, domain, category_name, method_name, condition_name in (
        (
            dataset.structure,
            DOMAIN_STRUCTURE,
            "结构类别",
            "表征方法",
            "测试条件",
        ),
        (
            dataset.performance,
            DOMAIN_PERFORMANCE,
            "性能类别",
            "测试方法",
            "测试条件",
        ),
    ):
        for row in rows:
            facts.append(
                _fact_input(
                    row,
                    quality_by_fact,
                    domain=domain,
                    metric=row.get("指标名称"),
                    raw_value=row.get("原始值"),
                    value_number=row.get("数值"),
                    range_min=row.get("下限"),
                    range_max=row.get("上限"),
                    unit=row.get("单位"),
                    indicator_category=row.get(category_name),
                    method=row.get(method_name),
                    condition=row.get(condition_name),
                )
            )

    if not include_unmapped:
        facts = [
            fact
            for fact in facts
            if str(fact.get("sample_id") or "").strip() not in {"", "未指定"}
        ]
    if not facts:
        raise PlatformDeliveryError("没有可转换为原子材料事实的数据")
    return papers, samples, facts


def _quality_index(
    rows: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        fact_id = str(row.get("事实ID") or "").strip()
        if not fact_id or fact_id in result:
            continue
        result[fact_id] = row
    return result


def _fact_input(
    row: dict[str, Any],
    quality_by_fact: dict[str, dict[str, Any]],
    *,
    domain: str,
    metric: Any,
    raw_value: Any,
    value_number: Any,
    range_min: Any,
    range_max: Any,
    unit: Any,
    component_role: Any = "",
    process_stage: Any = "",
    indicator_category: Any = "",
    method: Any = "",
    condition: Any = "",
) -> dict[str, Any]:
    fact_id = str(row.get("事实ID") or "").strip()
    quality = quality_by_fact.get(fact_id, {})
    fact = {
        "fact_id": fact_id,
        "paper_id": row.get("文献ID"),
        "sample_id": row.get("样品ID"),
        "domain": domain,
        "metric": metric,
        "raw_value": raw_value,
        "value_number": value_number,
        "range_min": range_min,
        "range_max": range_max,
        "unit": unit,
        "component_role": component_role,
        "process_stage": process_stage,
        "indicator_category": indicator_category,
        "method": method,
        "condition": condition,
        "evidence_text": quality.get("证据原文"),
        "source_location": _source_location(quality),
        "confidence": quality.get("置信度"),
    }
    return {
        key: value
        for key, value in fact.items()
        if value is not None and value != ""
    }


def _source_location(row: dict[str, Any]) -> str:
    return _join_text(
        row.get("来源位置"),
        f"p.{row.get('页码')}" if row.get("页码") not in (None, "") else "",
        row.get("来源块"),
    )


def _join_text(*values: Any) -> str:
    parts = [str(value).strip() for value in values if str(value or "").strip()]
    return "；".join(dict.fromkeys(parts))
