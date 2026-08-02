"""Validate the conservative AI4S external-platform template artifact.

This validator intentionally checks only the platform JSON shapes confirmed by
the downloaded reference template and the platform UI screenshots.  It does
not validate platform record payloads because v0.1 keeps ``data`` empty.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


TEMPLATE_FILENAME = "ai4s-chemical-fiber-template-v0.1.json"
COMMON_LEAF_MISC_KEYS = {
    "grp",
    "opt",
    "type",
    "multi",
    "stats",
    "imageFormats",
}
RESERVED_ORDER_KEYS = {"_ord", "_head", "_opt"}
SUPPORTED_V01_TYPES = set(range(1, 10))
OBJECT_REQUIRED_FIELDS = {
    "数据记录键",
    "投影版本",
    "文献编号",
    "数据状态",
}
EXTRACTION_STATUS_OPTIONS = [
    "已抽取",
    "已验证",
    "待复核",
    "抽取中",
    "未报告",
    "不适用",
]
REVIEW_STATUS_OPTIONS = [
    "待审核",
    "已修改",
    "通过",
    "存疑",
    "缺失",
    "已删除",
]
SECTION_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ValidationError(ValueError):
    """Raised when the template violates a confirmed platform invariant."""


def fail(path: str, message: str) -> None:
    raise ValidationError(f"{path}: {message}")


def require_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(path, "must be an object")
    return value


def require_string_list(
    value: Any,
    path: str,
    *,
    nonempty: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        fail(path, "must be an array")
    if nonempty and not value:
        fail(path, "must not be empty")
    if any(not isinstance(item, str) or not item for item in value):
        fail(path, "must contain non-empty strings")
    if len(value) != len(set(value)):
        fail(path, "must not contain duplicates")
    return value


def validate_extension_list(value: Any, path: str) -> None:
    extensions = require_string_list(value, path, nonempty=True)
    if any(not extension.startswith(".") for extension in extensions):
        fail(path, "every extension must start with '.'")


def validate_leaf_misc(misc: dict[str, Any], field_type: int, path: str) -> None:
    expected_keys = set(COMMON_LEAF_MISC_KEYS)
    if field_type == 5:
        expected_keys.add("fileFormats")
    if set(misc) != expected_keys:
        fail(
            path,
            f"keys must be exactly {sorted(expected_keys)}, got {sorted(misc)}",
        )

    if not isinstance(misc["grp"], list):
        fail(f"{path}.grp", "must be an array")
    if not isinstance(misc["opt"], list):
        fail(f"{path}.opt", "must be an array")
    if misc["type"] != 0:
        fail(f"{path}.type", "v0.1 only uses the confirmed default value 0")
    if not isinstance(misc["multi"], bool):
        fail(f"{path}.multi", "must be a boolean")
    if misc["stats"] != "0":
        fail(f"{path}.stats", "must be the confirmed string value '0'")
    if not isinstance(misc["imageFormats"], list):
        fail(f"{path}.imageFormats", "must be an array")

    for index, group in enumerate(misc["grp"]):
        group_path = f"{path}.grp[{index}]"
        group_mapping = require_mapping(group, group_path)
        if set(group_mapping) != {"name", "type", "items"}:
            fail(group_path, "must contain only name, type, and items")
        if (
            not isinstance(group_mapping["name"], str)
            or not group_mapping["name"]
        ):
            fail(f"{group_path}.name", "must be a non-empty string")
        if group_mapping["type"] != 2:
            fail(f"{group_path}.type", "must be 2")
        require_string_list(
            group_mapping["items"],
            f"{group_path}.items",
            nonempty=True,
        )

    require_string_list(misc["opt"], f"{path}.opt")

    if field_type in {1, 2, 3}:
        if misc["grp"] or misc["opt"]:
            fail(path, "string, number, and range fields cannot define options")
        if misc["multi"]:
            fail(path, "multi is unsupported for string, number, and range fields")
    elif field_type in {4, 5}:
        if misc["grp"] or misc["opt"]:
            fail(path, "image and file fields cannot define options")
    elif field_type == 6:
        if misc["multi"]:
            fail(path, "candidate multi-select is not confirmed for v0.1")

    if field_type == 4:
        validate_extension_list(misc["imageFormats"], f"{path}.imageFormats")
    elif misc["imageFormats"]:
        fail(f"{path}.imageFormats", "must be empty for non-image fields")

    if field_type == 5:
        validate_extension_list(misc["fileFormats"], f"{path}.fileFormats")

    if field_type == 6:
        if not misc["opt"] and not misc["grp"]:
            fail(path, "candidate fields need at least one option or option group")
        if misc["imageFormats"]:
            fail(f"{path}.imageFormats", "must be empty for candidate fields")


def validate_ordered_children(
    misc: dict[str, Any],
    order_key: str,
    path: str,
    counters: dict[str, int],
) -> None:
    order = require_string_list(
        misc.get(order_key),
        f"{path}.{order_key}",
        nonempty=True,
    )
    child_keys = [key for key in misc if key != order_key]
    if set(order) != set(child_keys) or len(order) != len(child_keys):
        fail(
            path,
            f"{order_key} must reference every child exactly once",
        )
    for child_name in order:
        if child_name in RESERVED_ORDER_KEYS:
            fail(path, f"{child_name!r} is a reserved field name")
        validate_field_node(
            misc[child_name],
            f"{path}.{child_name}",
            counters,
            child_name,
        )


def validate_field_node(
    value: Any,
    path: str,
    counters: dict[str, int],
    field_name: str | None = None,
) -> None:
    node = require_mapping(value, path)
    if set(node) != {"r", "t", "misc", "stats"}:
        fail(path, "field nodes must contain exactly r, t, misc, and stats")
    if not isinstance(node["r"], bool):
        fail(f"{path}.r", "must be a boolean")
    if isinstance(node["t"], bool) or not isinstance(node["t"], int):
        fail(f"{path}.t", "must be an integer")
    if node["t"] not in SUPPORTED_V01_TYPES:
        fail(
            f"{path}.t",
            "v0.1 supports only confirmed types 1 through 9; "
            "generator type 10 is intentionally excluded",
        )
    if node["stats"] != "0":
        fail(f"{path}.stats", "must be the confirmed string value '0'")

    field_type = node["t"]
    misc = require_mapping(node["misc"], f"{path}.misc")
    counters["nodes"] += 1
    counters[f"type_{field_type}"] = counters.get(f"type_{field_type}", 0) + 1

    if field_type in range(1, 7):
        validate_leaf_misc(misc, field_type, f"{path}.misc")
    elif field_type == 7:
        if misc.get("t") != 9:
            fail(
                f"{path}.misc",
                "v0.1 arrays must use a container (t=9) as their item selector",
            )
        validate_field_node(misc, f"{path}.misc", counters)
    elif field_type == 8:
        validate_ordered_children(misc, "_head", f"{path}.misc", counters)
    elif field_type == 9:
        validate_ordered_children(misc, "_ord", f"{path}.misc", counters)

    if field_name == "数据状态" or (
        field_name is not None and field_name.endswith("抽取状态")
    ):
        if field_type != 6 or misc["opt"] != EXTRACTION_STATUS_OPTIONS:
            fail(
                path,
                "status fields must be candidates with the confirmed "
                f"options {EXTRACTION_STATUS_OPTIONS}",
            )
    elif field_name is not None and field_name.endswith("审核状态"):
        if field_type != 6 or misc["opt"] != REVIEW_STATUS_OPTIONS:
            fail(
                path,
                "review status fields must be candidates with the confirmed "
                f"options {REVIEW_STATUS_OPTIONS}",
            )


def validate_blocks(
    blocks_value: Any,
    path: str,
    counters: dict[str, int],
) -> list[str]:
    blocks = require_mapping(blocks_value, path)
    order = require_string_list(
        blocks.get("_ord"),
        f"{path}._ord",
        nonempty=True,
    )
    block_keys = [key for key in blocks if key != "_ord"]
    if set(order) != set(block_keys) or len(order) != len(block_keys):
        fail(path, "_ord must reference every block exactly once")
    for field_name in order:
        if field_name in RESERVED_ORDER_KEYS:
            fail(path, f"{field_name!r} is a reserved field name")
        validate_field_node(
            blocks[field_name],
            f"{path}.{field_name}",
            counters,
            field_name,
        )
    return order


def validate_section(
    value: Any,
    path: str,
    seen_ids: set[str],
    counters: dict[str, int],
) -> list[str]:
    section = require_mapping(value, path)
    if set(section) != {"id", "label", "blocks"}:
        fail(path, "sections must contain exactly id, label, and blocks")
    section_id = section["id"]
    if not isinstance(section_id, str) or not SECTION_ID_RE.fullmatch(section_id):
        fail(f"{path}.id", "must be a stable lowercase ASCII identifier")
    if section_id in seen_ids:
        fail(f"{path}.id", f"duplicate section id {section_id!r}")
    seen_ids.add(section_id)
    if not isinstance(section["label"], str) or not section["label"]:
        fail(f"{path}.label", "must be a non-empty string")
    return validate_blocks(section["blocks"], f"{path}.blocks", counters)


def iter_nested_field_names(
    node: dict[str, Any],
    path: str,
):
    field_type = node["t"]
    misc = node["misc"]
    if field_type == 7:
        yield from iter_nested_field_names(misc, f"{path}[]")
    elif field_type in {8, 9, 10}:
        order_key = {8: "_head", 9: "_ord", 10: "_opt"}[field_type]
        for field_name in misc[order_key]:
            child_path = f"{path}.{field_name}"
            yield field_name, child_path
            yield from iter_nested_field_names(misc[field_name], child_path)


def iter_block_field_names(
    blocks: dict[str, Any],
    path: str,
):
    for field_name in blocks["_ord"]:
        field_path = f"{path}.{field_name}"
        yield field_name, field_path
        yield from iter_nested_field_names(blocks[field_name], field_path)


def validate_global_field_name_uniqueness(template: dict[str, Any]) -> None:
    seen: dict[str, str] = {}
    sections = [
        ("$.template.object.blocks", template["object"]["blocks"]),
        *[
            (f"$.template.operations[{index}].blocks", section["blocks"])
            for index, section in enumerate(template["operations"])
        ],
        *[
            (f"$.template.results[{index}].blocks", section["blocks"])
            for index, section in enumerate(template["results"])
        ],
    ]
    for section_path, blocks in sections:
        for field_name, field_path in iter_block_field_names(
            blocks,
            section_path,
        ):
            previous_path = seen.get(field_name)
            if previous_path is not None:
                fail(
                    field_path,
                    f"global field name {field_name!r} duplicates "
                    f"{previous_path}",
                )
            seen[field_name] = field_path


def validate_template(payload: Any) -> dict[str, int]:
    root = require_mapping(payload, "$")
    if set(root) != {"template", "data"}:
        fail("$", "root keys must be exactly template and data")
    if root["data"] != []:
        fail("$.data", "v0.1 is a template-only artifact and must keep data empty")

    template = require_mapping(root["template"], "$.template")
    if set(template) != {"_id", "object", "operations", "results"}:
        fail(
            "$.template",
            "keys must be exactly _id, object, operations, and results",
        )
    template_id = template["_id"]
    if (
        isinstance(template_id, bool)
        or not isinstance(template_id, int)
        or template_id <= 0
    ):
        fail("$.template._id", "must be a positive integer")

    operations = template["operations"]
    results = template["results"]
    if not isinstance(operations, list) or not operations:
        fail("$.template.operations", "must be a non-empty array")
    if not isinstance(results, list) or not results:
        fail("$.template.results", "must be a non-empty array")

    counters = {"nodes": 0}
    seen_ids: set[str] = set()
    object_fields = validate_section(
        template["object"],
        "$.template.object",
        seen_ids,
        counters,
    )
    object_required = {
        field_name
        for field_name in object_fields
        if template["object"]["blocks"][field_name]["r"]
    }
    if object_required != OBJECT_REQUIRED_FIELDS:
        fail(
            "$.template.object.blocks",
            "required object fields must be exactly "
            f"{sorted(OBJECT_REQUIRED_FIELDS)}, got {sorted(object_required)}",
        )

    for index, section in enumerate(operations):
        section_fields = validate_section(
            section,
            f"$.template.operations[{index}]",
            seen_ids,
            counters,
        )
        required = [
            field_name
            for field_name in section_fields
            if section["blocks"][field_name]["r"]
        ]
        if required:
            fail(
                f"$.template.operations[{index}].blocks",
                f"top-level operation fields must be optional, got {required}",
            )

    for index, section in enumerate(results):
        section_fields = validate_section(
            section,
            f"$.template.results[{index}]",
            seen_ids,
            counters,
        )
        required = [
            field_name
            for field_name in section_fields
            if section["blocks"][field_name]["r"]
        ]
        if required:
            fail(
                f"$.template.results[{index}].blocks",
                f"top-level result fields must be optional, got {required}",
            )

    status_node = template["object"]["blocks"]["数据状态"]
    if status_node["t"] != 6:
        fail("$.template.object.blocks.数据状态", "must be a candidate field")
    if status_node["misc"]["opt"] != EXTRACTION_STATUS_OPTIONS:
        fail(
            "$.template.object.blocks.数据状态.misc.opt",
            f"must be exactly {EXTRACTION_STATUS_OPTIONS}",
        )

    expected_ids = {
        "object",
        "operation1",
        "operation2",
        "result1",
        "result2",
        "result3",
        "result4",
        "result5",
    }
    if seen_ids != expected_ids:
        fail(
            "$.template",
            f"section ids must be exactly {sorted(expected_ids)}",
        )
    validate_global_field_name_uniqueness(template)
    return counters


def main() -> int:
    source = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(__file__).with_name(TEMPLATE_FILENAME)
    )
    try:
        raw = source.read_text(encoding="utf-8")
        payload = json.loads(raw)
        counters = validate_template(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        print(f"INVALID: {source.name}: {exc}", file=sys.stderr)
        return 1

    type_summary = ", ".join(
        f"t{field_type}={counters.get(f'type_{field_type}', 0)}"
        for field_type in sorted(SUPPORTED_V01_TYPES)
        if counters.get(f"type_{field_type}", 0)
    )
    print(
        f"VALID: {source.name} "
        f"({counters['nodes']} nodes; {type_summary})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
