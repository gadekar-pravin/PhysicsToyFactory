"""Phase 2 product fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from physics_toy_factory.config import Settings
from physics_toy_factory.main import create_app
from tests.fake_s17 import FakeS17

CONTROL_TOKEN = "phase-two-private-control-token"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        s17_control_token=CONTROL_TOKEN,
        s17_base_url="http://s17.test",
        workspace=tmp_path / "workspace",
        artifact_dir=tmp_path / "artifacts",
    )


@pytest.fixture
def fake_s17() -> FakeS17:
    return FakeS17(CONTROL_TOKEN)


@dataclass
class ProductHarness:
    client: httpx.AsyncClient
    app: object
    fake: FakeS17
    settings: Settings


@pytest_asyncio.fixture
async def product(settings: Settings, fake_s17: FakeS17) -> AsyncIterator[ProductHarness]:
    transport = httpx.ASGITransport(app=fake_s17.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://s17.test") as upstream:
        app = create_app(settings, http_client=upstream)
        async with app.router.lifespan_context(app):
            product_transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
            async with httpx.AsyncClient(
                transport=product_transport, base_url="http://product.test"
            ) as client:
                yield ProductHarness(client, app, fake_s17, settings)
