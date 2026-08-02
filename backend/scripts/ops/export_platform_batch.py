"""Export one AI4S project as an upload-ready platform batch JSON file.

This command deliberately composes the existing projection exporter and the
strict platform adapter.  It does not call the external platform, read browser
cookies, or persist authentication material.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.platform_batch_adapter import (  # noqa: E402
    build_platform_batch,
    dumps_platform_batch,
    validate_platform_batch,
    validate_platform_binding,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export a project through chemical_fiber_projection_v1 into the "
            "target platform's validated batch-upload JSON format."
        )
    )
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument(
        "--batch-template",
        type=Path,
        required=True,
        help="JSON downloaded from the target platform dataset",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--expected-dataset-id",
        type=int,
        required=True,
        help="Required fail-closed check for the target dataset ID.",
    )
    parser.add_argument(
        "--expected-template-id",
        type=int,
        required=True,
        help="Required fail-closed check for the target template ID.",
    )
    parser.add_argument(
        "--batch-template-sha256",
        required=True,
        help="Required fail-closed SHA-256 check for the downloaded template.",
    )
    parser.add_argument(
        "--projection-dir",
        type=Path,
        help=(
            "Optional audit directory for per-paper projections. When omitted, "
            "a temporary directory is removed after the validated batch is built."
        ),
    )
    parser.add_argument(
        "--exported-at",
        default="",
        help="Audit timestamp; defaults to the current UTC ISO-8601 time.",
    )
    return parser.parse_args()


def _run_projection_export(
    args: argparse.Namespace,
    projection_dir: Path,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).with_name("export_template_projections.py")),
        "--project-id",
        str(args.project_id),
        "--output-dir",
        str(projection_dir),
        "--overwrite",
    ]
    if args.database_url:
        command.extend(["--database-url", args.database_url])
    if args.limit > 0:
        command.extend(["--limit", str(args.limit)])
    completed = subprocess.run(
        command,
        cwd=BACKEND_ROOT,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "template projection export failed with exit code "
            f"{completed.returncode}"
        )

    manifest_path = projection_dir / "template_projection_manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("template projection manifest was not created")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("healthy"):
        raise RuntimeError("template projection manifest contains failed papers")
    return manifest


def _load_manifest_projections(
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    projections: list[dict[str, Any]] = []
    for entry in manifest.get("papers", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("status") not in {"completed", "resumed"}:
            continue
        projection_path = Path(str(entry.get("projection_path") or ""))
        if not projection_path.is_file():
            raise RuntimeError(
                f"projection file is missing for paper {entry.get('paper_id')}"
            )
        payload = json.loads(projection_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError(f"{projection_path} must contain a JSON object")
        projections.append(payload)
    if not projections:
        raise RuntimeError(
            "no review/completed papers with projections were found for the project"
        )
    return projections


def _write_atomic(path: Path, content: str) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _build(args: argparse.Namespace, projection_dir: Path) -> dict[str, Any]:
    batch_template_path = args.batch_template.expanduser().resolve()
    batch_template_bytes = batch_template_path.read_bytes()
    batch_template_sha256 = hashlib.sha256(batch_template_bytes).hexdigest()
    expected_sha256 = args.batch_template_sha256.strip().lower()
    if expected_sha256 and batch_template_sha256 != expected_sha256:
        raise RuntimeError(
            "batch template SHA-256 mismatch: expected "
            f"{expected_sha256}, got {batch_template_sha256}"
        )
    batch_template = json.loads(batch_template_bytes.decode("utf-8"))
    if not isinstance(batch_template, dict):
        raise RuntimeError("--batch-template must contain a JSON object")
    binding = validate_platform_binding(batch_template)
    if (
        args.expected_dataset_id is not None
        and binding["dataset_id"] != args.expected_dataset_id
    ):
        raise RuntimeError(
            "batch template dataset ID mismatch: expected "
            f"{args.expected_dataset_id}, got {binding['dataset_id']}"
        )
    if (
        args.expected_template_id is not None
        and binding["template_id"] != args.expected_template_id
    ):
        raise RuntimeError(
            "batch template template ID mismatch: expected "
            f"{args.expected_template_id}, got {binding['template_id']}"
        )

    manifest = _run_projection_export(args, projection_dir)
    projections = _load_manifest_projections(manifest)
    exported_at = args.exported_at or datetime.now(timezone.utc).isoformat()
    batch = build_platform_batch(
        batch_template,
        projections,
        exported_at=exported_at,
    )
    _write_atomic(args.output, dumps_platform_batch(batch))
    summary = validate_platform_batch(batch)
    return {
        **summary,
        "project_id": args.project_id,
        "paper_count": len(projections),
        "output": str(args.output.expanduser().resolve()),
        "exported_at": exported_at,
        "batch_template_sha256": batch_template_sha256,
    }


def main() -> int:
    args = _parse_args()
    try:
        if args.projection_dir:
            projection_dir = args.projection_dir.expanduser().resolve()
            projection_dir.mkdir(parents=True, exist_ok=True)
            summary = _build(args, projection_dir)
        else:
            with tempfile.TemporaryDirectory(
                prefix="ai4s-platform-projections-"
            ) as raw:
                summary = _build(args, Path(raw))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
