from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from app.services.platform_client import (
    PlatformApiClient,
    PlatformClientError,
    PlatformSessionStore,
)


@pytest.mark.asyncio
async def test_platform_login_hashes_password_and_verifies_token():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/login"):
            body = json.loads(request.content)
            assert body["password"] == hashlib.sha256(b"secret").hexdigest()
            assert body["code"] == "1234"
            return httpx.Response(200, json={"code": 200, "token": "platform-token"})
        if request.url.path.endswith("/getInfo"):
            assert request.headers["Authorization"] == "Bearer platform-token"
            return httpx.Response(
                200,
                json={"code": 200, "user": {"userName": "tester"}},
            )
        raise AssertionError(request.url)

    client = PlatformApiClient(
        "http://platform.local/database-code/",
        transport=httpx.MockTransport(handler),
    )
    token, info = await client.login(
        username="tester",
        password="secret",
        captcha_code="1234",
        captcha_uuid="uuid-1",
    )
    assert token == "platform-token"
    assert info["user"]["userName"] == "tester"
    assert [request.url.path for request in seen] == [
        "/dynamics/login",
        "/dynamics/getInfo",
    ]


@pytest.mark.asyncio
async def test_platform_chunk_upload_merge_and_parse_confirmation():
    chunk_calls = 0
    second_chunk_had_file_id = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal chunk_calls, second_chunk_had_file_id
        assert request.headers["Authorization"] == "Bearer token"
        if request.url.path.endswith("/fileUpload/upload"):
            chunk_calls += 1
            body = request.content
            if chunk_calls == 2:
                second_chunk_had_file_id = b'name="fileId"' in body
                assert b"file-99" in body
            return httpx.Response(
                200,
                json={"code": 200, "data": {"fileId": "file-99"}},
            )
        if request.url.path.endswith("/fileUpload/merge"):
            params = request.url.params
            assert params["taskType"] == "dataset"
            assert params["datasetId"] == "2081660157305163778"
            assert params["templateId"] == "2081658374180704257"
            assert params["sample"] == "0"
            return httpx.Response(200, json={"code": 200, "data": {"ok": True}})
        if request.url.path.endswith("/fileOperateHistory/list"):
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "rows": [
                        {
                            "originalFileName": "batch.json",
                            "operateResult": 0,
                            "resolveResult": 1,
                        }
                    ],
                },
            )
        raise AssertionError(request.url)

    client = PlatformApiClient(
        "http://platform.local/database-code",
        transport=httpx.MockTransport(handler),
    )
    result = await client.upload_batch_json(
        "token",
        filename="batch.json",
        content=b"abcdefgh",
        dataset_id=2_081_660_157_305_163_778,
        template_id=2_081_658_374_180_704_257,
        parse_timeout_seconds=1,
        poll_interval_seconds=0.01,
        chunk_size=4,
    )
    assert chunk_calls == 2
    assert second_chunk_had_file_id is True
    assert result["status"] == "completed"
    assert result["file_id"] == "file-99"


@pytest.mark.asyncio
async def test_platform_dataset_records_paginates_until_reported_total():
    seen_pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer token"
        assert request.url.path.endswith("/datasetByUser/123")
        page_number = int(request.url.params["pageNum"])
        seen_pages.append(page_number)
        rows = (
            [{"id": "row-1", "content": {"数据ID": "MD-1"}}]
            if page_number == 1
            else [{"id": "row-2", "content": {"数据ID": "MD-2"}}]
        )
        return httpx.Response(
            200,
            json={"code": 200, "rows": rows, "total": "2"},
        )

    client = PlatformApiClient(
        "http://platform.local/database-code",
        transport=httpx.MockTransport(handler),
    )
    rows = await client.dataset_records(
        "token",
        dataset_id=123,
        page_size=1,
    )

    assert seen_pages == [1, 2]
    assert [row["id"] for row in rows] == ["row-1", "row-2"]


@pytest.mark.asyncio
async def test_platform_session_handles_are_project_bound():
    store = PlatformSessionStore()
    handle, _ = await store.create(
        project_id=7,
        token="secret-token",
        username="tester",
        user_info={},
        ttl_seconds=60,
    )
    assert (await store.get(handle, project_id=7)).token == "secret-token"
    with pytest.raises(PlatformClientError, match="失效"):
        await store.get(handle, project_id=8)
