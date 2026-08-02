"""Build auditable project batches and persist non-secret delivery receipts."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate_record import CandidateRecord
from app.models.evidence_item import EvidenceItem
from app.models.fact_candidate import FactCandidate
from app.models.paper import Paper
from app.models.sample_catalog import SampleCatalog
from app.services.platform_batch_adapter import (
    PlatformBatchError,
    build_platform_batch,
    dumps_platform_batch,
    validate_platform_binding,
)
from app.services.template_projection import build_template_projection


ELIGIBLE_PAPER_STATUSES = {"review", "completed"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PlatformDeliveryError(ValueError):
    """Raised when project data cannot be safely delivered to the platform."""


@dataclass(frozen=True)
class LoadedPlatformBinding:
    template: dict[str, Any]
    template_path: Path
    template_sha256: str
    dataset_id: int
    template_id: int


@dataclass(frozen=True)
class ProjectBatchArtifact:
    content: bytes
    batch_sha256: str
    filename: str
    summary: dict[str, Any]
    paper_ids: list[int]


def load_pinned_platform_binding(
    *,
    batch_template_path: str,
    expected_sha256: str,
    expected_dataset_id: int,
    expected_template_id: int,
) -> LoadedPlatformBinding:
    path = Path(batch_template_path).expanduser().resolve(strict=False)
    if not path.is_file():
        raise PlatformDeliveryError(f"平台批量模板不存在: {path}")
    content = path.read_bytes()
    actual_sha256 = hashlib.sha256(content).hexdigest()
    expected = expected_sha256.strip().lower()
    if not _SHA256_RE.fullmatch(expected):
        raise PlatformDeliveryError("平台批量模板 SHA-256 配置无效")
    if actual_sha256 != expected:
        raise PlatformDeliveryError(
            "平台批量模板 SHA-256 不匹配，已拒绝继续导入"
        )
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlatformDeliveryError("平台批量模板不是有效的 UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise PlatformDeliveryError("平台批量模板根节点必须是 JSON 对象")
    try:
        binding = validate_platform_binding(payload)
    except PlatformBatchError as exc:
        raise PlatformDeliveryError(str(exc)) from exc
    if binding["dataset_id"] != expected_dataset_id:
        raise PlatformDeliveryError(
            "平台数据集 ID 与固定绑定不一致，已拒绝继续导入"
        )
    if binding["template_id"] != expected_template_id:
        raise PlatformDeliveryError(
            "平台模板 ID 与固定绑定不一致，已拒绝继续导入"
        )
    return LoadedPlatformBinding(
        template=payload,
        template_path=path,
        template_sha256=actual_sha256,
        dataset_id=binding["dataset_id"],
        template_id=binding["template_id"],
    )


async def load_project_projections(
    db: AsyncSession,
    *,
    project_id: int,
    paper_ids: list[int] | None,
    include_unmapped: bool,
) -> tuple[list[dict[str, Any]], list[int]]:
    paper_query = select(Paper).where(Paper.project_id == project_id)
    if paper_ids is None:
        paper_query = paper_query.where(
            Paper.status.in_(sorted(ELIGIBLE_PAPER_STATUSES))
        )
    else:
        paper_query = paper_query.where(Paper.id.in_(paper_ids))
    paper_query = paper_query.order_by(Paper.id)
    papers = list((await db.execute(paper_query)).scalars().all())

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

    selected_ids = [paper.id for paper in papers]
    sample_rows = list(
        (
            await db.execute(
                select(SampleCatalog)
                .where(SampleCatalog.paper_id.in_(selected_ids))
                .order_by(SampleCatalog.id)
            )
        )
        .scalars()
        .all()
    )
    fact_rows = list(
        (
            await db.execute(
                select(FactCandidate)
                .where(FactCandidate.paper_id.in_(selected_ids))
                .order_by(FactCandidate.id)
            )
        )
        .scalars()
        .all()
    )
    record_rows = list(
        (
            await db.execute(
                select(CandidateRecord)
                .where(CandidateRecord.source_paper_id.in_(selected_ids))
                .order_by(CandidateRecord.id)
            )
        )
        .scalars()
        .all()
    )
    evidence_rows = list(
        (
            await db.execute(
                select(EvidenceItem)
                .where(EvidenceItem.paper_id.in_(selected_ids))
                .order_by(EvidenceItem.id)
            )
        )
        .scalars()
        .all()
    )

    samples_by_paper: dict[int, list[Any]] = defaultdict(list)
    facts_by_paper: dict[int, list[Any]] = defaultdict(list)
    records_by_paper: dict[int, list[Any]] = defaultdict(list)
    evidence_by_paper: dict[int, list[Any]] = defaultdict(list)
    for item in sample_rows:
        samples_by_paper[item.paper_id].append(item)
    for item in fact_rows:
        facts_by_paper[item.paper_id].append(item)
    for item in record_rows:
        records_by_paper[item.source_paper_id].append(item)
    for item in evidence_rows:
        evidence_by_paper[item.paper_id].append(item)

    projections = [
        build_template_projection(
            paper=paper,
            samples=samples_by_paper[paper.id],
            facts=facts_by_paper[paper.id],
            records=records_by_paper[paper.id],
            evidence_items=evidence_by_paper[paper.id],
            include_unmapped=include_unmapped,
        )
        for paper in papers
    ]
    return projections, selected_ids


async def build_project_batch_artifact(
    db: AsyncSession,
    *,
    project_id: int,
    binding: LoadedPlatformBinding,
    paper_ids: list[int] | None,
    include_unmapped: bool,
    exported_at: str | None = None,
) -> ProjectBatchArtifact:
    projections, selected_ids = await load_project_projections(
        db,
        project_id=project_id,
        paper_ids=paper_ids,
        include_unmapped=include_unmapped,
    )
    # Keep the batch deterministic when the source data is unchanged. The
    # actual delivery timestamp belongs in the receipt; embedding "now" in the
    # batch would defeat retry de-duplication.
    audit_time = exported_at
    try:
        batch = build_platform_batch(
            binding.template,
            projections,
            exported_at=audit_time,
        )
        text = dumps_platform_batch(batch)
    except PlatformBatchError as exc:
        raise PlatformDeliveryError(str(exc)) from exc
    content = text.encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    summary = {
        "project_id": project_id,
        "paper_count": len(selected_ids),
        "record_count": len(batch["data"]),
        "dataset_id": binding.dataset_id,
        "template_id": binding.template_id,
        "batch_template_sha256": binding.template_sha256,
        "batch_sha256": digest,
        "bytes": len(content),
        "exported_at": audit_time,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return ProjectBatchArtifact(
        content=content,
        batch_sha256=digest,
        filename=f"ai4s_project_{project_id}_{digest[:12]}.json",
        summary=summary,
        paper_ids=selected_ids,
    )


class PlatformReceiptStore:
    """Filesystem audit trail that intentionally never stores bearer tokens."""

    def __init__(self, export_root: Path, project_id: int) -> None:
        self.root = (
            export_root.expanduser().resolve(strict=False)
            / str(project_id)
            / "platform_delivery"
        )

    def _validate_sha256(self, value: str) -> str:
        normalized = (value or "").strip().lower()
        if not _SHA256_RE.fullmatch(normalized):
            raise PlatformDeliveryError("无效的批次 SHA-256")
        return normalized

    def receipt_path(self, batch_sha256: str) -> Path:
        return self.root / "receipts" / f"{self._validate_sha256(batch_sha256)}.json"

    def batch_path(self, filename: str) -> Path:
        safe_name = Path(filename).name
        if safe_name != filename or not safe_name.lower().endswith(".json"):
            raise PlatformDeliveryError("无效的平台批次文件名")
        return self.root / "batches" / safe_name

    def read(self, batch_sha256: str) -> dict[str, Any] | None:
        path = self.receipt_path(batch_sha256)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def write_batch(self, artifact: ProjectBatchArtifact) -> Path:
        path = self.batch_path(artifact.filename)
        self._write_atomic_bytes(path, artifact.content)
        return path

    def write_receipt(
        self,
        batch_sha256: str,
        payload: Mapping[str, Any],
    ) -> Path:
        path = self.receipt_path(batch_sha256)
        content = (
            json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        self._write_atomic_bytes(path, content)
        return path

    @staticmethod
    def _write_atomic_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
