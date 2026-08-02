"""Generate the pinned exporter-safe material-chain template.

This small generator keeps the repetitive platform field descriptors
mechanical while the ordered field lists remain easy to review.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.app.services.platform_material_chain_adapter import (
    OBJECT_GROUP,
    PLATFORM_OBJECT_FIELDS,
    PLATFORM_PROCESS_FIELDS,
    PLATFORM_RESULT_FIELDS,
    PROCESS_GROUP,
    RESULT_GROUP,
)


OUTPUT = Path(__file__).with_name(
    "ai4s-material-chain-template-v0.3.2.json"
)


def text_field(*, required: bool = False) -> dict:
    return {
        "r": required,
        "t": 1,
        "misc": {
            "grp": [],
            "opt": [],
            "type": 0,
            "multi": False,
            "stats": "0",
            "imageFormats": [],
        },
        "stats": "0",
    }


def ordered_group(
    names: tuple[str, ...],
    *,
    required: set[str] | None = None,
) -> dict:
    required = required or set()
    misc = {"_ord": list(names)}
    misc.update(
        {
            name: text_field(required=name in required)
            for name in names
        }
    )
    return {
        "r": False,
        "t": 9,
        "misc": misc,
        "stats": "0",
    }


def build_template() -> dict:
    return {
        "template": {
            "_id": 20_260_728_032,
            "object": {
                "id": "object",
                "label": OBJECT_GROUP,
                "blocks": {
                    "_ord": [OBJECT_GROUP],
                    OBJECT_GROUP: ordered_group(
                        PLATFORM_OBJECT_FIELDS,
                        required={
                            "数据ID",
                            "具体材料对象或样品编号",
                        },
                    ),
                },
            },
            "operations": [
                {
                    "id": "operation1",
                    "label": PROCESS_GROUP,
                    "blocks": {
                        "_ord": [PROCESS_GROUP],
                        PROCESS_GROUP: ordered_group(
                            PLATFORM_PROCESS_FIELDS
                        ),
                    },
                }
            ],
            "results": [
                {
                    "id": "result1",
                    "label": RESULT_GROUP,
                    "blocks": {
                        "_ord": [RESULT_GROUP],
                        RESULT_GROUP: ordered_group(
                            PLATFORM_RESULT_FIELDS
                        ),
                    },
                }
            ],
        },
        "data": [],
    }


if __name__ == "__main__":
    OUTPUT.write_text(
        json.dumps(
            build_template(),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(OUTPUT)
