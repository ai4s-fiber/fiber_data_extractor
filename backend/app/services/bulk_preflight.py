"""Preflight helpers for long-running bulk extraction."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import unicodedata
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from pypdf import PdfReader


MINERU_CLOUD_MAX_FILE_BYTES = 200 * 1024 * 1024
MINERU_CLOUD_MAX_PAGES = 600
logging.getLogger("pypdf").setLevel(logging.ERROR)


@dataclass(slots=True)
class PdfInspection:
    size_bytes: int
    page_count: int | None
    rejection_reason: str = ""
    warning: str = ""
    relevance_decision: str = "review"
    relevance_reason: str = "not_inspected"
    metadata_title: str = ""
    preview_chars: int = 0


@dataclass(slots=True)
class StoragePreflight:
    source_bytes: int
    estimated_output_bytes: int
    minimum_free_bytes: int
    available_bytes: int
    copy_bytes_reserved: int
    hardlink_supported: bool
    output_volumes: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_FIBER_SIGNAL_RE = re.compile(
    r"(?:"
    r"(?:nano|micro)?fib(?:er|re|rous|ril)"
    r"|filament|yarn|textile|woven|nonwoven"
    r"|electrospun|electrospinning|spunbond|meltblown"
    r"|melt-spun|wet-spun|dry-spun"
    r"|纤维|纳米纤|微纤|纺丝|纱线|织物|非织造|无纺"
    r")",
    re.IGNORECASE,
)
_MATERIAL_SIGNAL_RE = re.compile(
    r"(?:"
    r"\b(?:material|materials|polymer|polymers|composite|composites|resin|"
    r"matrix|reinforced|reinforcement|tensile|mechanical|thermal|electrical|"
    r"cellulose|carbon|epoxy|polyamide|polyester|polyethylene|polypropylene|"
    r"nanotube|graphene)\b"
    r"|材料|聚合物|复合材料|树脂|基体|增强|拉伸|力学|热性能|电性能"
    r")",
    re.IGNORECASE,
)
_EXCLUDED_DOCUMENT_TYPE_RE = re.compile(
    r"(?:"
    r"\b(?:book review|corrigendum|erratum|editorial|letter to the editor|"
    r"publisher correction|retraction|withdrawal notice|conference calendar|"
    r"meeting notice|meeting report|award announcement|chemInform abstract)\b"
    r"|书评|勘误|更正声明|撤稿|会议通知|获奖公告|编辑部"
    r")",
    re.IGNORECASE,
)
_CLINICAL_SIGNAL_RE = re.compile(
    r"\b(?:clinical trial|randomi[sz]ed|patients?|placebo|diagnosis|"
    r"therapy|therapeutic|survival rate|case report|hospital)\b",
    re.IGNORECASE,
)
_REVIEW_ARTICLE_LEAD_RE = re.compile(
    r"^(?:.{0,40}\s)?review(?: article)?\b",
    re.IGNORECASE,
)


def classify_document_relevance(
    title: str,
    preview_text: str,
) -> tuple[str, str]:
    """Classify only clear non-target documents; ambiguous papers remain eligible.

    This is a cost-control prefilter, not a replacement for MinerU parsing. It
    intentionally favors recall: any fiber signal passes, and weak/empty local
    text is sent onward for normal extraction.
    """
    normalized_title = " ".join(
        unicodedata.normalize("NFKC", title or "").split()
    )
    normalized_preview = " ".join(
        unicodedata.normalize("NFKC", preview_text or "").split()
    )
    searchable = f"{normalized_title}\n{normalized_preview[:16000]}".strip()
    lead = f"{normalized_title}\n{normalized_preview[:1600]}"
    if _EXCLUDED_DOCUMENT_TYPE_RE.search(lead):
        return "irrelevant", "excluded_document_type"
    if _REVIEW_ARTICLE_LEAD_RE.search(normalized_preview[:160]):
        return "irrelevant", "review_article"
    if _FIBER_SIGNAL_RE.search(searchable):
        return "eligible", "fiber_signal"
    if len(normalized_preview) < 200:
        return "review", "insufficient_local_text"

    clinical_hits = len(_CLINICAL_SIGNAL_RE.findall(searchable))
    if clinical_hits >= 2 and not _MATERIAL_SIGNAL_RE.search(searchable):
        return "irrelevant", "clinical_document_without_material_signal"

    if _MATERIAL_SIGNAL_RE.search(searchable):
        return "review", "material_without_fiber_signal"
    return "review", "no_fiber_signal"


def inspect_pdf(path: Path, *, inspect_relevance: bool = True) -> PdfInspection:
    """Inspect limits locally without replacing MinerU as the PDF parser."""
    size_bytes = path.stat().st_size
    if size_bytes <= 0:
        return PdfInspection(size_bytes, None, "empty_file")
    if size_bytes > MINERU_CLOUD_MAX_FILE_BYTES:
        return PdfInspection(size_bytes, None, "mineru_file_size_limit")

    try:
        with path.open("rb") as source:
            if b"%PDF-" not in source.read(1024):
                return PdfInspection(size_bytes, None, "invalid_pdf_header")
    except OSError as exc:
        return PdfInspection(
            size_bytes,
            None,
            "unreadable_file",
            f"{exc.__class__.__name__}: {exc}",
        )

    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            try:
                password_result = reader.decrypt("")
            except Exception as exc:
                return PdfInspection(
                    size_bytes,
                    None,
                    "encrypted_pdf",
                    f"{exc.__class__.__name__}: {exc}",
                )
            if not password_result:
                return PdfInspection(size_bytes, None, "encrypted_pdf")
        page_count = len(reader.pages)
    except Exception as exc:
        # MinerU is more tolerant than pypdf for some damaged cross-reference
        # tables, so an unavailable local page count is a warning, not rejection.
        return PdfInspection(
            size_bytes,
            None,
            warning=f"page_count_unavailable:{exc.__class__.__name__}",
        )

    if page_count > MINERU_CLOUD_MAX_PAGES:
        return PdfInspection(
            size_bytes,
            page_count,
            "mineru_page_limit",
        )

    if not inspect_relevance:
        return PdfInspection(
            size_bytes,
            page_count,
            relevance_decision="eligible",
            relevance_reason="prefilter_disabled",
        )

    metadata_title = ""
    try:
        metadata_title = str(getattr(reader.metadata, "title", "") or "").strip()
    except Exception:
        metadata_title = ""

    preview_parts: list[str] = []
    preview_warning = ""
    try:
        for page in reader.pages[: min(page_count, 2)]:
            preview_parts.append(page.extract_text() or "")
    except Exception as exc:
        preview_warning = f"preview_unavailable:{exc.__class__.__name__}"
    preview_text = "\n".join(preview_parts)
    decision, reason = classify_document_relevance(metadata_title, preview_text)
    return PdfInspection(
        size_bytes,
        page_count,
        warning=preview_warning,
        relevance_decision=decision,
        relevance_reason=reason,
        metadata_title=metadata_title[:500],
        preview_chars=len(preview_text),
    )


def stable_config_fingerprint(payload: dict[str, Any]) -> str:
    """Return a secret-free fingerprint for resume compatibility checks."""
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _quantile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    if not ordered:
        return 0
    index = round((len(ordered) - 1) * fraction)
    return ordered[max(0, min(index, len(ordered) - 1))]


def select_stratified_documents(
    documents: list[Any],
    *,
    source_root: Path,
    sample_size: int,
    seed: str,
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Select a deterministic pilot across folder, size and page-count strata."""
    if sample_size <= 0 or sample_size >= len(documents):
        selected = list(documents)
    else:
        size_values = [max(0, int(item.size_bytes or 0)) for item in documents]
        page_values = [
            int(item.page_count)
            for item in documents
            if item.page_count is not None
        ]
        size_low = _quantile(size_values, 1 / 3)
        size_high = _quantile(size_values, 2 / 3)
        page_low = _quantile(page_values, 1 / 3)
        page_high = _quantile(page_values, 2 / 3)

        def bucket(value: int | None, low: int, high: int) -> str:
            if value is None:
                return "unknown"
            if value <= low:
                return "small"
            if value <= high:
                return "medium"
            return "large"

        groups: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
        resolved_root = source_root.resolve()
        for item in documents:
            try:
                relative = item.path.resolve().relative_to(resolved_root)
                folder = relative.parts[0] if len(relative.parts) > 1 else "."
            except ValueError:
                folder = "<external>"
            key = (
                folder.casefold(),
                bucket(int(item.size_bytes or 0), size_low, size_high),
                bucket(item.page_count, page_low, page_high),
            )
            groups[key].append(item)

        def score(*parts: str) -> str:
            value = "\x1f".join((seed, *parts)).encode("utf-8")
            return hashlib.sha256(value).hexdigest()

        for items in groups.values():
            items.sort(key=lambda item: score(item.sha256, str(item.path)))
        group_order = sorted(
            groups,
            key=lambda key: score(*key),
        )

        selected = []
        while len(selected) < sample_size:
            made_progress = False
            for key in group_order:
                items = groups[key]
                if not items:
                    continue
                selected.append(items.pop(0))
                made_progress = True
                if len(selected) >= sample_size:
                    break
            if not made_progress:
                break

    manifest = []
    for rank, item in enumerate(selected, start=1):
        try:
            relative_path = str(item.path.resolve().relative_to(source_root.resolve()))
        except ValueError:
            relative_path = str(item.path)
        manifest.append({
            "rank": rank,
            "path": str(item.path),
            "relative_path": relative_path,
            "sha256": item.sha256,
            "size_bytes": int(item.size_bytes or 0),
            "page_count": item.page_count,
            "relevance_decision": getattr(item, "relevance_decision", "review"),
            "relevance_reason": getattr(item, "relevance_reason", "not_inspected"),
        })
    return selected, manifest


def ensure_writable_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / f".bulk-write-probe-{uuid.uuid4().hex}"
    try:
        probe.write_bytes(b"ok")
    finally:
        probe.unlink(missing_ok=True)


def _volume_key(path: Path) -> str:
    resolved = path.resolve()
    return (resolved.anchor or str(resolved)).casefold()


def _hardlink_supported(source: Path, destination_dir: Path) -> bool:
    probe = destination_dir / f".bulk-link-probe-{uuid.uuid4().hex}.pdf"
    try:
        os.link(source, probe)
        return True
    except OSError:
        return False
    finally:
        probe.unlink(missing_ok=True)


def validate_storage_capacity(
    *,
    source_paths: Iterable[Path],
    output_directories: Iterable[Path],
    upload_directory: Path,
    copy_mode: str,
    artifact_factor: float,
    minimum_free_bytes: int,
) -> StoragePreflight:
    """Validate writable output locations and reserve conservative disk space."""
    paths = list(source_paths)
    outputs = [path.expanduser().resolve() for path in output_directories]
    if not paths:
        raise ValueError("source_paths cannot be empty")
    if not outputs:
        raise ValueError("output_directories cannot be empty")
    if artifact_factor < 0:
        raise ValueError("artifact_factor cannot be negative")

    for directory in outputs:
        ensure_writable_directory(directory)

    upload_directory = upload_directory.expanduser().resolve()
    ensure_writable_directory(upload_directory)
    hardlink_supported = (
        copy_mode == "hardlink"
        and _hardlink_supported(paths[0], upload_directory)
    )
    source_bytes = sum(path.stat().st_size for path in paths)
    copy_bytes = 0 if hardlink_supported else source_bytes
    estimated_output_bytes = (
        int(source_bytes * artifact_factor)
        + copy_bytes
        + max(0, int(minimum_free_bytes))
    )

    volumes: dict[str, Path] = {}
    for directory in [*outputs, upload_directory]:
        volumes.setdefault(_volume_key(directory), directory)
    available_bytes = min(
        shutil.disk_usage(directory).free for directory in volumes.values()
    )
    if available_bytes < estimated_output_bytes:
        required_gib = estimated_output_bytes / (1024**3)
        available_gib = available_bytes / (1024**3)
        raise RuntimeError(
            "Insufficient free disk space for bulk extraction: "
            f"need about {required_gib:.1f} GiB, available {available_gib:.1f} GiB"
        )

    return StoragePreflight(
        source_bytes=source_bytes,
        estimated_output_bytes=estimated_output_bytes,
        minimum_free_bytes=max(0, int(minimum_free_bytes)),
        available_bytes=available_bytes,
        copy_bytes_reserved=copy_bytes,
        hardlink_supported=hardlink_supported,
        output_volumes=sorted(volumes),
    )
