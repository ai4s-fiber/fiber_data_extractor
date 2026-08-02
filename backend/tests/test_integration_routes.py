import json

import pytest

from app.api.integrations import (
    _artifact_data_ids,
    _collect_platform_data_ids,
    _receipt_is_in_flight,
)
from app.main import app
from app.services.platform_delivery import PlatformDeliveryError


def test_data_pipeline_routes_are_registered_for_both_api_prefixes():
    paths = {route.path for route in app.routes}
    expected = {
        "/projects/{project_id}/integrations/nas/sources",
        "/projects/{project_id}/integrations/nas/scan",
        "/projects/{project_id}/integrations/nas/import",
        "/projects/{project_id}/integrations/platform/config",
        "/projects/{project_id}/integrations/platform/captcha",
        "/projects/{project_id}/integrations/platform/connect",
        "/projects/{project_id}/integrations/platform/preflight",
        "/projects/{project_id}/integrations/platform/export",
        "/projects/{project_id}/integrations/platform/import",
    }
    for route in expected:
        assert f"/api{route}" in paths
        assert f"/api/v1{route}" in paths


def test_in_flight_receipt_blocks_blind_reupload():
    assert _receipt_is_in_flight({"status": "uploading"}) is True
    assert _receipt_is_in_flight({"status": "processing"}) is True
    assert _receipt_is_in_flight({"status": "failed"}) is False
    assert _receipt_is_in_flight(None) is False


def test_platform_record_ids_are_collected_and_validated_per_record():
    rows = [
        {
            "id": "row-1",
            "content": {
                "object": {
                    "文献、样品与成分": {"数据ID": "MD-stable-1"}
                }
            },
        },
        {
            "id": "row-2",
            "content": {
                "object": {
                    "文献、样品与成分": {"数据ID": "MD-stable-2"}
                }
            },
        },
    ]
    content = json.dumps({"data": rows}, ensure_ascii=False).encode("utf-8")

    assert _collect_platform_data_ids(rows) == {
        "MD-stable-1",
        "MD-stable-2",
    }
    assert _artifact_data_ids(content) == {
        "MD-stable-1",
        "MD-stable-2",
    }

    duplicate = json.dumps(
        {"data": [rows[0], rows[0]]},
        ensure_ascii=False,
    ).encode("utf-8")
    with pytest.raises(PlatformDeliveryError, match="缺失或重复"):
        _artifact_data_ids(duplicate)
