"""Paper delete cleanup tests."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.paper_cleanup import purge_paper


def test_purge_paper_callable():
    assert callable(purge_paper)


@pytest.mark.asyncio
async def test_delete_paper_route_registered():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        routes = {route.path for route in app.routes if hasattr(route, "path")}
        assert "/api/projects/{project_id}/papers/{paper_id}" in routes
