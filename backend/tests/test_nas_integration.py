from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.integration import NasScanRequest, NasSelectedFile
from app.services.nas_integration import (
    NasIntegrationError,
    get_nas_source,
    parse_nas_source_roots,
    scan_nas_source,
    stage_nas_pdf,
)


def _pdf(path: Path, suffix: bytes = b"") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n" + suffix)


def test_nas_roots_scan_only_configured_pdf_files(tmp_path: Path):
    root = tmp_path / "nas-share"
    _pdf(root / "b.pdf")
    _pdf(root / "nested" / "a.PDF")
    (root / "ignore.txt").write_text("not a paper", encoding="utf-8")

    raw = f'["{str(root).replace(chr(92), chr(92) * 2)}"]'
    sources = parse_nas_source_roots(raw)
    assert len(sources) == 1
    assert sources[0].available is True

    result = scan_nas_source(
        sources[0],
        recursive=True,
        max_files=50,
    )
    assert [item.relative_path for item in result.files] == [
        "b.pdf",
        "nested/a.PDF",
    ]
    assert result.truncated is False


def test_nas_scan_filters_basename_case_insensitively_after_unicode_normalization(
    tmp_path: Path,
):
    root = tmp_path / "nas-share"
    _pdf(root / "Fiber_ALPHA.PDF")
    _pdf(root / "alpha-folder" / "unrelated.pdf")
    _pdf(root / "other.pdf")
    source = parse_nas_source_roots(str(root))[0]

    request = NasScanRequest(
        source_id=source.id,
        filename_query="  ａｌｐｈａ  ",
    )
    result = scan_nas_source(
        source,
        filename_query=request.filename_query,
    )

    assert request.filename_query == "alpha"
    assert [item.relative_path for item in result.files] == ["Fiber_ALPHA.PDF"]


def test_nas_scan_treats_blank_filename_query_as_unfiltered(tmp_path: Path):
    root = tmp_path / "nas-share"
    _pdf(root / "first.pdf")
    _pdf(root / "second.pdf")
    source = parse_nas_source_roots(str(root))[0]

    request = NasScanRequest(source_id=source.id, filename_query="   ")
    result = scan_nas_source(source, filename_query=request.filename_query)

    assert request.filename_query is None
    assert [item.filename for item in result.files] == [
        "first.pdf",
        "second.pdf",
    ]
    with pytest.raises(ValidationError):
        NasScanRequest(source_id=source.id, filename_query="x" * 201)


def test_scan_payload_preserves_nanosecond_timestamp_as_string(tmp_path: Path):
    root = tmp_path / "nas"
    _pdf(root / "paper.pdf")
    source = parse_nas_source_roots(str(root))[0]
    candidate = scan_nas_source(source).files[0]

    payload = candidate.payload()
    assert candidate.modified_ns > 2**53
    assert payload["modified_ns"] == str(candidate.modified_ns)
    selected = NasSelectedFile.model_validate(payload)
    assert int(selected.modified_ns) == candidate.modified_ns

    with pytest.raises(ValidationError):
        NasSelectedFile.model_validate({**payload, "modified_ns": candidate.modified_ns})


def test_nas_path_traversal_is_rejected(tmp_path: Path):
    root = tmp_path / "nas"
    root.mkdir()
    outside = tmp_path / "outside.pdf"
    _pdf(outside)
    source = parse_nas_source_roots(str(root))[0]

    with pytest.raises(NasIntegrationError, match="安全相对路径"):
        stage_nas_pdf(
            source,
            relative_path="../outside.pdf",
            expected_size=outside.stat().st_size,
            expected_modified_ns=outside.stat().st_mtime_ns,
            upload_root=tmp_path / "uploads",
            project_id=1,
            max_file_bytes=1_000_000,
        )


def test_stage_nas_pdf_copies_and_hashes_unchanged_file(tmp_path: Path):
    root = tmp_path / "nas"
    source_pdf = root / "paper.pdf"
    _pdf(source_pdf, b"stable")
    source = parse_nas_source_roots(str(root))[0]
    stat = source_pdf.stat()

    staged = stage_nas_pdf(
        source,
        relative_path="paper.pdf",
        expected_size=stat.st_size,
        expected_modified_ns=stat.st_mtime_ns,
        upload_root=tmp_path / "uploads",
        project_id=7,
        max_file_bytes=1_000_000,
    )

    assert staged.stored_path.read_bytes() == source_pdf.read_bytes()
    assert staged.file_object_key.startswith("7/")
    assert staged.content_sha256 == hashlib.sha256(source_pdf.read_bytes()).hexdigest()


def test_stage_nas_pdf_rejects_stale_scan_metadata(tmp_path: Path):
    root = tmp_path / "nas"
    source_pdf = root / "paper.pdf"
    _pdf(source_pdf)
    source = parse_nas_source_roots(str(root))[0]
    stat = source_pdf.stat()
    _pdf(source_pdf, b"changed")

    with pytest.raises(NasIntegrationError, match="扫描后发生变化"):
        stage_nas_pdf(
            source,
            relative_path="paper.pdf",
            expected_size=stat.st_size,
            expected_modified_ns=stat.st_mtime_ns,
            upload_root=tmp_path / "uploads",
            project_id=1,
            max_file_bytes=1_000_000,
        )


def test_unknown_nas_source_is_rejected(tmp_path: Path):
    root = tmp_path / "nas"
    root.mkdir()
    with pytest.raises(NasIntegrationError, match="未知"):
        get_nas_source(str(root), "not-a-real-source")
