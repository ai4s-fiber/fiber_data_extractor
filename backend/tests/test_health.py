"""Health endpoint smoke test."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/api/health", "/api/v1/health"):
            resp = await client.get(path)
            assert resp.status_code == 200
            body = resp.json()
            assert "status" in body
            assert "version" in body
