"""Candidate delete API tests."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_batch_delete_route_registered():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        routes = {route.path for route in app.routes if hasattr(route, "path")}
        assert "/api/projects/{project_id}/candidates/batch-delete" in routes
        assert "/api/projects/{project_id}/candidates/batch-delete-by-paper" in routes
