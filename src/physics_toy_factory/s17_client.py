"""Authenticated, fail-closed transport adapter for the S17 control plane."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from physics_toy_factory.config import Settings
from physics_toy_factory.errors import ProductError

RUN_ID_PATTERN = re.compile(r"^run-[A-Za-z0-9._-]{1,124}$")
MAX_SSE_FRAME_BYTES = 262_144
MAX_START_RESPONSE_BYTES = 16_384
MAX_RUN_RESPONSE_BYTES = 2_000_000
PRODUCT_BUDGET_PRINCIPAL = "session:physics-toy-factory-demo"


class AmbiguousStartError(ProductError):
    """A timeout where S17 may have accepted work without returning its ID."""

    ambiguous = True

    def __init__(self) -> None:
        super().__init__(
            504,
            "s17_start_ambiguous",
            "S17 may have accepted the run, but its response timed out. Reset after operator review.",
            retryable=True,
        )


@dataclass(frozen=True)
class S17Probe:
    """A body-free upstream status suitable for product health output."""

    reachable: bool
    status_code: int | None
    ok: bool


class UpstreamEventStream:
    """An entered HTTPX stream whose lifetime belongs to the product response."""

    def __init__(self, response: httpx.Response, manager: Any, control_token: str) -> None:
        self._response = response
        self._manager = manager
        self._control_token = control_token

    async def frames(self):  # type: ignore[no-untyped-def]
        """Yield bounded, validated SSE frames without buffering the complete run."""

        lines: list[str] = []
        size = 0
        terminal = False
        async for line in self._response.aiter_lines():
            size += len(line.encode("utf-8")) + 1
            if size > MAX_SSE_FRAME_BYTES:
                raise ProductError(502, "s17_sse_invalid", "S17 emitted an oversized event frame.")
            if line:
                lines.append(line)
                continue
            if lines:
                frame, is_terminal = self._validated_frame(lines)
                terminal = terminal or is_terminal
                yield frame
            lines = []
            size = 0
        if lines:
            frame, is_terminal = self._validated_frame(lines)
            terminal = terminal or is_terminal
            yield frame
        if not terminal:
            raise ProductError(502, "s17_stream_disconnected", "S17 event transport disconnected.")

    async def close(self) -> None:
        """Close the response and release its connection promptly."""

        await self._manager.__aexit__(None, None, None)

    def _validated_frame(self, lines: list[str]) -> tuple[bytes, bool]:
        frame = "\n".join(lines) + "\n\n"
        if self._control_token and self._control_token in frame:
            raise ProductError(502, "s17_sse_invalid", "S17 emitted unsafe event data.")
        event_id = next((line[3:].strip() for line in lines if line.startswith("id:")), None)
        if event_id is not None and (not event_id.isdigit() or int(event_id) < 0):
            raise ProductError(502, "s17_sse_invalid", "S17 emitted an invalid event cursor.")
        data_lines = [line[5:].lstrip() for line in lines if line.startswith("data:")]
        terminal = False
        if data_lines:
            try:
                payload = json.loads("\n".join(data_lines))
            except json.JSONDecodeError as exc:
                raise ProductError(502, "s17_sse_invalid", "S17 emitted malformed event data.") from exc
            if not isinstance(payload, dict):
                raise ProductError(502, "s17_sse_invalid", "S17 emitted malformed event data.")
            if self._control_token in json.dumps(payload, ensure_ascii=False, separators=(",", ":")):
                raise ProductError(502, "s17_sse_invalid", "S17 emitted unsafe event data.")
            terminal = payload.get("type") == "RUN_FINISHED"
        return frame.encode("utf-8"), terminal


class S17Client:
    """Use one injected async client for bounded server-to-server calls."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client
        self._token = settings.s17_control_token.get_secret_value()
        self._base_url = str(settings.s17_base_url).rstrip("/")

    async def start_run(self, *, goal: str, allowed_side_effects: list[str]) -> str:
        """Start a durable S17 run and accept only its exact 202 contract."""

        body = {
            "tenant_id": "physics-toy-factory",
            "project_id": "demo",
            "user_id": "local-audience",
            "agent_id": "p5-builder",
            "respond_as": "text",
            "prompt": goal,
            "allowed_side_effects": allowed_side_effects,
            "budget": self._settings.s17_run_budget_usd,
            "principal": PRODUCT_BUDGET_PRINCIPAL,
        }
        try:
            response = await self._client.post(
                f"{self._base_url}/v1/agent/runs/async",
                json=body,
                headers=self._control_headers(),
            )
        except httpx.TimeoutException as exc:
            raise AmbiguousStartError() from exc
        except httpx.HTTPError as exc:
            raise ProductError(
                502, "s17_unavailable", "Could not reach S17.", retryable=True
            ) from exc
        if response.status_code != 202:
            if response.status_code == 401:
                raise ProductError(502, "s17_auth_failed", "S17 rejected product authentication.")
            if response.status_code == 503:
                raise ProductError(503, "s17_not_ready", "S17 is not ready.", retryable=True)
            raise ProductError(502, "s17_start_rejected", "S17 rejected the run request.")
        if len(response.content) > MAX_START_RESPONSE_BYTES:
            raise ProductError(502, "s17_invalid_response", "S17 returned an invalid start response.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProductError(502, "s17_invalid_response", "S17 returned an invalid start response.") from exc
        run_id = payload.get("run_id") if isinstance(payload, dict) else None
        if (
            not isinstance(run_id, str)
            or not RUN_ID_PATTERN.fullmatch(run_id)
            or payload.get("status") != "accepted"
        ):
            raise ProductError(502, "s17_invalid_response", "S17 returned an invalid start response.")
        return run_id

    async def get_run(self, run_id: str) -> dict[str, Any]:
        """Read one raw graph after the caller establishes session ownership."""

        self._validate_run_id(run_id)
        try:
            response = await self._client.get(f"{self._base_url}/v1/agent/runs/{run_id}")
        except httpx.TimeoutException as exc:
            raise ProductError(504, "s17_read_timeout", "S17 run data timed out.", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProductError(502, "s17_unavailable", "Could not reach S17.", retryable=True) from exc
        if response.status_code == 404:
            raise ProductError(
                502,
                "s17_run_inconsistent",
                "S17 no longer exposes an accepted session run.",
                retryable=True,
            )
        if response.status_code != 200:
            raise ProductError(502, "s17_read_failed", "S17 could not return the run.")
        if len(response.content) > MAX_RUN_RESPONSE_BYTES or self._token.encode() in response.content:
            raise ProductError(502, "s17_invalid_response", "S17 returned unsafe run data.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProductError(502, "s17_invalid_response", "S17 returned invalid run data.") from exc
        if self._token in json.dumps(payload, ensure_ascii=False, separators=(",", ":")):
            raise ProductError(502, "s17_invalid_response", "S17 returned unsafe run data.")
        if not isinstance(payload, dict) or payload.get("run_id") != run_id:
            raise ProductError(502, "s17_invalid_response", "S17 returned invalid run data.")
        if not isinstance(payload.get("nodes"), dict) or not isinstance(payload.get("events"), list):
            raise ProductError(502, "s17_invalid_response", "S17 returned invalid run data.")
        return payload

    async def open_events(
        self, run_id: str, *, after: int, reconnect: bool
    ) -> UpstreamEventStream:
        """Open and validate an upstream SSE response before product headers are sent."""

        self._validate_run_id(run_id)
        timeout = httpx.Timeout(
            connect=self._settings.http_connect_timeout_seconds,
            read=None,
            write=self._settings.http_connect_timeout_seconds,
            pool=self._settings.http_connect_timeout_seconds,
        )
        manager = self._client.stream(
            "GET",
            f"{self._base_url}/v1/runs/{run_id}/events",
            params={"after": after, "reconnect": int(reconnect)},
            timeout=timeout,
        )
        try:
            response = await manager.__aenter__()
        except httpx.TimeoutException as exc:
            raise ProductError(504, "s17_stream_timeout", "S17 event stream timed out.", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProductError(502, "s17_unavailable", "Could not reach S17.", retryable=True) from exc
        content_type = response.headers.get("content-type", "").lower()
        if response.status_code != 200 or not content_type.startswith("text/event-stream"):
            await manager.__aexit__(None, None, None)
            raise ProductError(502, "s17_stream_failed", "S17 could not open the event stream.")
        return UpstreamEventStream(response, manager, self._token)

    async def probe(self, path: str) -> S17Probe:
        """Return health state without forwarding arbitrary upstream bodies."""

        try:
            response = await self._client.get(f"{self._base_url}{path}")
        except httpx.HTTPError:
            return S17Probe(reachable=False, status_code=None, ok=False)
        return S17Probe(reachable=True, status_code=response.status_code, ok=response.status_code == 200)

    def _control_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    @staticmethod
    def _validate_run_id(run_id: str) -> None:
        if not RUN_ID_PATTERN.fullmatch(run_id):
            raise ProductError(404, "run_not_found", "Run does not belong to this session.")
