"""Phase 1 health endpoint test."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from physics_toy_factory.config import Settings
from physics_toy_factory.main import create_app


@pytest.mark.asyncio
async def test_health_reports_verified_workspace_without_secret(tmp_path: Path) -> None:
    settings = Settings(
        s17_control_token="never-return-this-token",
        workspace=tmp_path / "workspace",
        artifact_dir=tmp_path / "artifacts",
    )

    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "physics-toy-factory",
        "version": "0.1.0",
        "workspace_verified": True,
        "trusted_asset_count": 6,
    }
    assert "never-return-this-token" not in response.text
