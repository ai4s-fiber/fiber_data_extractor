"""Export clears review queue tests."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_paper_download_route_registered():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test"):
        routes = {route.path for route in app.routes if hasattr(route, "path")}
        assert "/api/projects/{project_id}/papers/{paper_id}/download" in routes
