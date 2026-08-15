"""FastAPI entrypoint for the Physics Toy Factory product."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request

from physics_toy_factory import __version__
from physics_toy_factory.config import Settings, load_settings
from physics_toy_factory.workspace import WorkspaceManager


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an app with one validated settings object injected at startup."""

    configured = settings or load_settings()
    workspace = WorkspaceManager(configured)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        report = workspace.ensure_initialized()
        app.state.settings = configured
        app.state.workspace_report = report
        yield

    app = FastAPI(title="Physics Toy Factory", version=__version__, lifespan=lifespan)

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        report = request.app.state.workspace_report
        return {
            "status": "ok",
            "service": "physics-toy-factory",
            "version": __version__,
            "workspace_verified": True,
            "trusted_asset_count": report.asset_count,
        }

    return app


def serve() -> None:
    """Run the loopback product server from the console script."""

    settings = load_settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
