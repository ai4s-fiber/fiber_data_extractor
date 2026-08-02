"""Controlled, server-side NAS discovery and PDF staging.

The browser can select only paths below roots configured by an administrator.
Absolute paths, drive changes, parent traversal and symlink/junction escapes
are rejected before any file is opened.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable


class NasIntegrationError(ValueError):
    """Raised when a NAS source or selected file is unsafe or unavailable."""


@dataclass(frozen=True)
class NasSource:
    id: str
    label: str
    path: Path
    available: bool

    def public_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "path": str(self.path),
            "available": self.available,
        }


@dataclass(frozen=True)
class NasFileCandidate:
    id: str
    relative_path: str
    filename: str
    size: int
    modified_ns: int

    def payload(self) -> dict[str, object]:
        payload = asdict(self)
        # Nanosecond timestamps exceed JavaScript's safe integer range. Keep the
        # exact value across the browser round trip by serializing it as text.
        payload["modified_ns"] = str(self.modified_ns)
        return payload


@dataclass(frozen=True)
class NasScanResult:
    files: list[NasFileCandidate]
    truncated: bool
    skipped_unreadable: int


@dataclass(frozen=True)
class StagedNasPdf:
    source_path: Path
    stored_path: Path
    file_object_key: str
    original_filename: str
    content_sha256: str
    size: int


def _normalized_root_key(path: Path) -> str:
    absolute = path.expanduser().resolve(strict=False)
    return os.path.normcase(os.path.normpath(str(absolute)))


def _root_label(path: Path) -> str:
    name = path.name.strip()
    if name:
        return name
    anchor = path.anchor.strip("\\/")
    return anchor or str(path)


def parse_nas_source_roots(raw: str) -> list[NasSource]:
    """Parse JSON-array or semicolon-separated NAS roots with stable IDs."""
    value = (raw or "").strip()
    if not value:
        return []

    items: Iterable[object]
    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise NasIntegrationError(
                "NAS_SOURCE_ROOTS 不是有效的 JSON 数组"
            ) from exc
        if not isinstance(parsed, list):
            raise NasIntegrationError("NAS_SOURCE_ROOTS 必须是 JSON 数组")
        items = parsed
    else:
        items = value.split(";")

    sources: list[NasSource] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, str) or not item.strip():
            continue
        path = Path(item.strip()).expanduser().resolve(strict=False)
        key = _normalized_root_key(path)
        if key in seen:
            continue
        seen.add(key)
        source_id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        sources.append(
            NasSource(
                id=source_id,
                label=_root_label(path),
                path=path,
                available=path.is_dir(),
            )
        )
    return sources


def get_nas_source(raw_roots: str, source_id: str) -> NasSource:
    for source in parse_nas_source_roots(raw_roots):
        if source.id == source_id:
            if not source.available:
                raise NasIntegrationError(f"NAS 根目录当前不可访问: {source.path}")
            return source
    raise NasIntegrationError("未知的 NAS 数据源")


def _validate_relative_path(value: str, *, allow_empty: bool) -> Path:
    raw = (value or "").strip()
    if not raw:
        if allow_empty:
            return Path(".")
        raise NasIntegrationError("必须选择一个相对文件路径")

    windows = PureWindowsPath(raw)
    posix = PurePosixPath(raw.replace("\\", "/"))
    if (
        windows.is_absolute()
        or bool(windows.drive)
        or posix.is_absolute()
        or ".." in windows.parts
        or ".." in posix.parts
    ):
        raise NasIntegrationError("NAS 路径必须是根目录内的安全相对路径")
    return Path(*posix.parts)


def _resolve_inside_root(
    source: NasSource,
    relative_path: str,
    *,
    expect_directory: bool,
) -> Path:
    root = source.path.resolve(strict=True)
    relative = _validate_relative_path(
        relative_path,
        allow_empty=expect_directory,
    )
    try:
        target = (root / relative).resolve(strict=True)
        common = os.path.commonpath(
            [
                os.path.normcase(str(root)),
                os.path.normcase(str(target)),
            ]
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise NasIntegrationError("NAS 路径不存在或无法解析") from exc
    if os.path.normcase(common) != os.path.normcase(str(root)):
        raise NasIntegrationError("NAS 路径越过了已配置根目录")
    if expect_directory and not target.is_dir():
        raise NasIntegrationError("所选 NAS 路径不是目录")
    if not expect_directory and not target.is_file():
        raise NasIntegrationError("所选 NAS 路径不是文件")
    return target


def scan_nas_source(
    source: NasSource,
    *,
    relative_directory: str = "",
    filename_query: str | None = None,
    recursive: bool = True,
    extensions: Iterable[str] = (".pdf",),
    max_files: int = 5_000,
) -> NasScanResult:
    """Discover files deterministically without reading their contents."""
    if max_files < 1:
        raise NasIntegrationError("NAS 扫描上限必须大于 0")
    directory = _resolve_inside_root(
        source,
        relative_directory,
        expect_directory=True,
    )
    root = source.path.resolve(strict=True)
    allowed = {
        item.lower() if item.startswith(".") else f".{item.lower()}"
        for item in extensions
        if str(item).strip()
    }
    if not allowed:
        allowed = {".pdf"}
    normalized_query = unicodedata.normalize(
        "NFKC",
        filename_query or "",
    ).strip()
    if len(normalized_query) > 200:
        raise NasIntegrationError("NAS 文件名筛选词不能超过 200 个字符")
    folded_query = normalized_query.casefold()

    iterator = directory.rglob("*") if recursive else directory.iterdir()
    files: list[NasFileCandidate] = []
    skipped = 0
    truncated = False
    for candidate in iterator:
        try:
            if not candidate.is_file() or candidate.suffix.lower() not in allowed:
                continue
            if folded_query and folded_query not in unicodedata.normalize(
                "NFKC",
                candidate.name,
            ).casefold():
                continue
            resolved = candidate.resolve(strict=True)
            common = os.path.commonpath(
                [
                    os.path.normcase(str(root)),
                    os.path.normcase(str(resolved)),
                ]
            )
            if os.path.normcase(common) != os.path.normcase(str(root)):
                skipped += 1
                continue
            stat = resolved.stat()
            relative = resolved.relative_to(root).as_posix()
        except (OSError, RuntimeError, ValueError):
            skipped += 1
            continue

        if len(files) >= max_files:
            truncated = True
            break
        fingerprint = (
            f"{source.id}\0{relative}\0{stat.st_size}\0{stat.st_mtime_ns}"
        )
        files.append(
            NasFileCandidate(
                id=hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24],
                relative_path=relative,
                filename=resolved.name,
                size=int(stat.st_size),
                modified_ns=int(stat.st_mtime_ns),
            )
        )

    files.sort(key=lambda item: item.relative_path.casefold())
    return NasScanResult(
        files=files,
        truncated=truncated,
        skipped_unreadable=skipped,
    )


def stage_nas_pdf(
    source: NasSource,
    *,
    relative_path: str,
    expected_size: int,
    expected_modified_ns: int,
    upload_root: Path,
    project_id: int,
    max_file_bytes: int,
) -> StagedNasPdf:
    """Copy one unchanged PDF into managed storage and compute its SHA-256."""
    source_path = _resolve_inside_root(
        source,
        relative_path,
        expect_directory=False,
    )
    if source_path.suffix.lower() != ".pdf":
        raise NasIntegrationError("当前批量导入只支持 PDF 文件")

    before = source_path.stat()
    if before.st_size != expected_size or before.st_mtime_ns != expected_modified_ns:
        raise NasIntegrationError("文件在扫描后发生变化，请重新扫描")
    if before.st_size <= 0:
        raise NasIntegrationError("PDF 文件为空")
    if before.st_size > max_file_bytes:
        raise NasIntegrationError(
            f"PDF 超过大小限制 ({max_file_bytes} bytes)"
        )

    project_dir = (upload_root / str(project_id)).resolve(strict=False)
    project_dir.mkdir(parents=True, exist_ok=True)
    basename = f"{uuid.uuid4().hex}.pdf"
    stored_path = project_dir / basename
    temporary = project_dir / f".{basename}.part"
    digest = hashlib.sha256()
    copied = 0
    try:
        with source_path.open("rb") as source_handle, temporary.open("xb") as target:
            first = source_handle.read(1024 * 1024)
            if not first.startswith(b"%PDF-"):
                raise NasIntegrationError("文件扩展名为 PDF，但内容不是有效 PDF")
            target.write(first)
            digest.update(first)
            copied += len(first)
            while True:
                chunk = source_handle.read(1024 * 1024)
                if not chunk:
                    break
                target.write(chunk)
                digest.update(chunk)
                copied += len(chunk)
            target.flush()
            os.fsync(target.fileno())

        after = source_path.stat()
        if (
            after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or copied != before.st_size
        ):
            raise NasIntegrationError("复制过程中源文件发生变化，请重新扫描")
        os.replace(temporary, stored_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        stored_path.unlink(missing_ok=True)
        raise

    relative_key = stored_path.relative_to(upload_root.resolve(strict=False))
    return StagedNasPdf(
        source_path=source_path,
        stored_path=stored_path,
        file_object_key=relative_key.as_posix(),
        original_filename=source_path.name,
        content_sha256=digest.hexdigest(),
        size=copied,
    )


def discard_staged_pdf(staged: StagedNasPdf) -> None:
    """Remove a staged copy that was rejected as a duplicate."""
    staged.stored_path.unlink(missing_ok=True)


def copy_nas_file(source: Path, target: Path) -> None:
    """Small test/helper hook retained for controlled non-PDF attachments."""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
