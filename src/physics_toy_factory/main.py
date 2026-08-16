"""FastAPI entrypoint and browser-safe product API."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from physics_toy_factory import __version__
from physics_toy_factory.config import Settings, load_settings
from physics_toy_factory.errors import ProductError
from physics_toy_factory.history import HistoryStore
from physics_toy_factory.models import BrowserErrorBody, PreviewLeaseBody, PromptBody
from physics_toy_factory.orchestrator import Orchestrator
from physics_toy_factory.s17_client import S17Client
from physics_toy_factory.session import SessionService
from physics_toy_factory.workspace import WorkspaceManager, WorkspaceSafetyError

SUGGESTED_PROMPTS = [
    "Rain that avoids my mouse",
    "Bouncy magnets",
    "Angry solar system",
    "Fish that follow my cursor",
]
log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).with_name("static")


def _preview_headers(nonce: str) -> dict[str, str]:
    return {
        "Content-Security-Policy": (
            "default-src 'none'; "
            f"script-src 'nonce-{nonce}'; "
            "connect-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; "
            "font-src 'none'; media-src 'none'; object-src 'none'; frame-src 'none'; "
            "worker-src 'none'; base-uri 'none'; form-action 'none'; navigate-to 'none'; "
            "frame-ancestors 'self'"
        ),
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "Permissions-Policy": (
            "camera=(), display-capture=(), geolocation=(), microphone=(), payment=(), usb=()"
        ),
    }


def _javascript_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    }


def create_app(
    settings: Settings | None = None, *, http_client: httpx.AsyncClient | None = None
) -> FastAPI:
    """Create the product with one settings object and one injected HTTP client."""

    configured = settings or load_settings()
    workspace = WorkspaceManager(configured)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        report = workspace.ensure_initialized()
        history = HistoryStore(
            configured.artifact_dir,
            max_sketch_bytes=configured.max_sketch_bytes,
        )
        session = SessionService(history, reset_required=workspace.reset_required())
        owns_client = http_client is None
        transport = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=configured.http_connect_timeout_seconds,
                read=configured.http_read_timeout_seconds,
                write=configured.http_connect_timeout_seconds,
                pool=configured.http_connect_timeout_seconds,
            ),
            follow_redirects=False,
        )
        s17 = S17Client(configured, transport)
        app.state.settings = configured
        app.state.workspace_report = report
        app.state.workspace = workspace
        app.state.history = history
        app.state.session = session
        app.state.s17 = s17
        app.state.orchestrator = Orchestrator(configured, workspace, session, s17, history)
        try:
            yield
        finally:
            if owns_client:
                await transport.aclose()
            history.close()

    app = FastAPI(title="Physics Toy Factory", version=__version__, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.exception_handler(ProductError)
    async def product_error(_request: Request, exc: ProductError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Request body or parameters are invalid.",
                    "retryable": False,
                }
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled product exception type=%s", type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "The product could not complete the request.",
                    "retryable": False,
                }
            },
        )

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        s17: S17Client = request.app.state.s17
        process, gateway = await asyncio.gather(s17.probe("/healthz"), s17.probe("/readyz"))
        try:
            report = request.app.state.workspace.validate_identity()
            workspace_verified = True
            asset_count = report.asset_count
        except WorkspaceSafetyError:
            workspace_verified = False
            asset_count = 0
        container_enabled = configured.s17_exec_container
        warning = (
            "Container mode is configured but not independently verified by the product."
            if container_enabled
            else "Development only: generated code is not configured for container isolation."
        )
        ready = workspace_verified and process.ok and gateway.ok
        return {
            "status": "ok" if ready else "degraded",
            "service": "physics-toy-factory",
            "version": __version__,
            "ready": ready,
            "workspace": {"verified": workspace_verified, "trusted_asset_count": asset_count},
            "s17": {
                "process": {
                    "reachable": process.reachable,
                    "status_code": process.status_code,
                    "up": process.ok,
                },
                "gateway": {
                    "reachable": gateway.reachable,
                    "status_code": gateway.status_code,
                    "ready": gateway.ok,
                },
            },
            "container_mode": {
                "configured": container_enabled,
                "image": configured.s17_exec_image if container_enabled else None,
                "secure_sandbox_claimed": False,
                "warning": warning,
            },
        }

    @app.get("/api/session")
    async def get_session(request: Request) -> dict[str, Any]:
        degraded: dict[str, str] | None = None
        try:
            await request.app.state.orchestrator.refresh_active()
        except ProductError as exc:
            degraded = {"code": exc.code, "message": exc.message}
        record = await request.app.state.session.snapshot()
        return {
            "session": record.model_dump(mode="json"),
            "suggested_prompts": SUGGESTED_PROMPTS,
            "degraded": degraded,
        }

    @app.post("/api/session/reset")
    async def reset(request: Request) -> dict[str, Any]:
        record = await request.app.state.orchestrator.reset()
        return {"session": record.model_dump(mode="json")}

    @app.post("/api/runs", status_code=202)
    async def create_run(body: PromptBody, request: Request) -> dict[str, Any]:
        result = await request.app.state.orchestrator.create(body.prompt)
        return result.model_dump(mode="json")

    @app.post("/api/runs/follow-up", status_code=202)
    async def follow_up(body: PromptBody, request: Request) -> dict[str, Any]:
        result = await request.app.state.orchestrator.follow_up(body.prompt)
        return result.model_dump(mode="json")

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str, request: Request) -> dict[str, Any]:
        return await request.app.state.orchestrator.get_run(run_id)

    @app.get("/api/history")
    async def history(
        request: Request,
        limit: int = Query(default=20, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=512),
        q: str = Query(default="", max_length=200),
    ) -> dict[str, Any]:
        page = await request.app.state.orchestrator.history_page(
            limit=limit,
            cursor=cursor,
            query=q,
        )
        return {"items": page.items, "next_cursor": page.next_cursor, "total": page.total}

    @app.get("/api/history/{history_id}")
    async def history_detail(history_id: str, request: Request) -> dict[str, Any]:
        return await request.app.state.orchestrator.history_detail(history_id)

    @app.get("/api/history/{history_id}/code")
    async def history_code(history_id: str, request: Request) -> dict[str, Any]:
        result = await request.app.state.orchestrator.history_code(history_id)
        return result.model_dump(mode="json")

    @app.post("/api/history/{history_id}/preview")
    async def history_preview(history_id: str, request: Request) -> dict[str, object]:
        return await request.app.state.orchestrator.prepare_history_preview(history_id)

    @app.delete("/api/history/{history_id}", status_code=204)
    async def delete_history(history_id: str, request: Request) -> Response:
        await request.app.state.orchestrator.delete_history(history_id)
        return Response(status_code=204)

    @app.get("/api/runs/{run_id}/events")
    async def events(
        run_id: str,
        request: Request,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        await request.app.state.session.require_owned(run_id)
        after = 0
        reconnect = last_event_id is not None
        if last_event_id is not None:
            if not last_event_id.isdigit():
                raise ProductError(422, "invalid_event_cursor", "Last-Event-ID must be nonnegative.")
            after = int(last_event_id)
        upstream = await request.app.state.s17.open_events(
            run_id, after=after, reconnect=reconnect
        )

        async def stream():  # type: ignore[no-untyped-def]
            try:
                async for frame in upstream.frames():
                    if await request.is_disconnected():
                        break
                    yield frame
            except (ProductError, httpx.HTTPError):
                payload = json.dumps(
                    {
                        "type": "transport_error",
                        "message": "The S17 event transport disconnected; reconnect to continue.",
                    },
                    separators=(",", ":"),
                )
                yield f"event: transport_error\ndata: {payload}\n\n".encode()
            finally:
                await upstream.close()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/code")
    async def code(request: Request) -> dict[str, Any]:
        result = await request.app.state.orchestrator.code()
        return result.model_dump(mode="json")

    @app.post("/api/preview")
    async def prepare_preview(body: PreviewLeaseBody, request: Request) -> dict[str, object]:
        return await request.app.state.orchestrator.prepare_preview(body.revision)

    @app.get("/preview/{verified_sha256}", include_in_schema=False)
    async def preview(
        verified_sha256: str, preview_id: str, request: Request
    ) -> HTMLResponse:
        content, nonce = await request.app.state.orchestrator.preview_shell(
            revision=verified_sha256,
            preview_id=preview_id,
        )
        return HTMLResponse(content, headers=_preview_headers(nonce))

    @app.get("/history-preview/{history_id}", include_in_schema=False)
    async def history_preview_shell(
        history_id: str, preview_id: str, request: Request
    ) -> HTMLResponse:
        content, nonce = await request.app.state.orchestrator.history_preview_shell(
            history_id=history_id,
            preview_id=preview_id,
        )
        return HTMLResponse(content, headers=_preview_headers(nonce))

    @app.get("/api/preview/p5.min.js", include_in_schema=False)
    async def preview_p5(revision: str, preview_id: str, request: Request) -> Response:
        content = await request.app.state.orchestrator.preview_javascript(
            revision=revision,
            preview_id=preview_id,
            asset="p5.min.js",
        )
        return Response(
            content,
            media_type="application/javascript",
            headers=_javascript_headers(),
        )

    @app.get("/api/preview/sketch.js", include_in_schema=False)
    async def preview_sketch(revision: str, preview_id: str, request: Request) -> Response:
        content = await request.app.state.orchestrator.preview_javascript(
            revision=revision,
            preview_id=preview_id,
            asset="sketch.js",
        )
        return Response(
            content,
            media_type="application/javascript",
            headers=_javascript_headers(),
        )

    @app.get("/api/history/{history_id}/preview/p5.min.js", include_in_schema=False)
    async def history_preview_p5(
        history_id: str, preview_id: str, request: Request
    ) -> Response:
        content = await request.app.state.orchestrator.history_preview_javascript(
            history_id=history_id,
            preview_id=preview_id,
            asset="p5.min.js",
        )
        return Response(
            content,
            media_type="application/javascript",
            headers=_javascript_headers(),
        )

    @app.get("/api/history/{history_id}/preview/sketch.js", include_in_schema=False)
    async def history_preview_sketch(
        history_id: str, preview_id: str, request: Request
    ) -> Response:
        content = await request.app.state.orchestrator.history_preview_javascript(
            history_id=history_id,
            preview_id=preview_id,
            asset="sketch.js",
        )
        return Response(
            content,
            media_type="application/javascript",
            headers=_javascript_headers(),
        )

    @app.post("/api/runs/{run_id}/browser-error")
    async def browser_error(
        run_id: str, body: BrowserErrorBody, request: Request
    ) -> dict[str, Any]:
        record = await request.app.state.orchestrator.browser_error(run_id=run_id, body=body)
        return {"session": record.model_dump(mode="json")}

    return app


def serve() -> None:
    """Run the loopback product server from the console script."""

    settings = load_settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
