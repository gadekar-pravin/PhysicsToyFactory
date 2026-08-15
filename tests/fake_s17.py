"""Deterministic in-process S17 contract double for product integration tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse


def unfinished_graph(run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "finished": False,
        "nodes": {},
        "edges": [],
        "events": [{"sequence": 1, "kind": "run_started", "node_id": None, "payload": {}}],
    }


def checker_node(command: str, exit_code: int, *, timed_out: bool = False) -> dict[str, Any]:
    return {
        "skill": "run_command",
        "state": "succeeded",
        "input": {"command": command},
        "result": {"exit_code": exit_code, "timed_out": timed_out, "stdout": "", "stderr": ""},
    }


def terminal_graph(
    run_id: str,
    *,
    checker_results: tuple[tuple[str, int, bool], ...] = (("node p5check.js sketch.js", 0, False),),
    answer: bool = True,
) -> dict[str, Any]:
    nodes: dict[str, Any] = {}
    events: list[dict[str, Any]] = [
        {"sequence": 1, "kind": "run_started", "node_id": None, "payload": {}}
    ]
    sequence = 2
    for index, (command, exit_code, timed_out) in enumerate(checker_results):
        node_id = f"check_{index}"
        nodes[node_id] = checker_node(command, exit_code, timed_out=timed_out)
        events.append(
            {"sequence": sequence, "kind": "task_succeeded", "node_id": node_id, "payload": {}}
        )
        sequence += 1
    if answer:
        nodes["answer"] = {
            "skill": "answer_with_evidence",
            "state": "succeeded",
            "input": {"query": "finish"},
            "result": {"answer": "done"},
        }
        events.append(
            {"sequence": sequence, "kind": "task_succeeded", "node_id": "answer", "payload": {}}
        )
    return {"run_id": run_id, "finished": True, "nodes": nodes, "edges": [], "events": events}


def sse_for_graph(graph: dict[str, Any], *, include_terminal: bool | None = None) -> bytes:
    frames = []
    for event in graph["events"]:
        payload = _agui_event(event)
        frames.append(
            f"id: {event['sequence']}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
        )
    frames.append(": keepalive\n\n")
    terminal = graph["finished"] if include_terminal is None else include_terminal
    if terminal:
        last_sequence = graph["events"][-1]["sequence"] if graph["events"] else 0
        frames.append(
            "data: "
            + json.dumps(
                {"type": "RUN_FINISHED", "seq": last_sequence + 1, "source_kind": "derived"},
                separators=(",", ":"),
            )
            + "\n\n"
        )
    return "".join(frames).encode()


def _agui_event(event: dict[str, Any]) -> dict[str, Any]:
    kind = event["kind"]
    sequence = event["sequence"]
    node_id = event.get("node_id")
    payload = event.get("payload") or {}
    base: dict[str, Any] = {"seq": sequence, "source_kind": kind}
    if kind == "run_started":
        return {**base, "type": "RUN_STARTED"}
    if kind == "graph_patched":
        return {
            **base,
            "type": "STATE_DELTA",
            "delta": {
                "op": "graph_patched",
                "reason": payload.get("reason", ""),
                "trigger": payload.get("trigger_event"),
            },
        }
    if kind == "task_started":
        return {**base, "type": "STEP_STARTED", "stepName": node_id}
    if kind == "task_succeeded":
        return {
            **base,
            "type": "STEP_FINISHED",
            "stepName": node_id,
            "delta": {"op": "add", "path": f"/results/{node_id}", "value": payload},
        }
    if kind == "task_failed":
        return {
            **base,
            "type": "STEP_FINISHED",
            "stepName": node_id,
            "error": payload.get("error", "task failed"),
        }
    if kind == "run_failed":
        return {
            **base,
            "type": "RUN_ERROR",
            "error": payload.get("error", "run failed"),
            "errorType": payload.get("error_type", "RuntimeError"),
        }
    return {**base, "type": "CUSTOM"}


@dataclass
class FakeS17:
    """Mutable fake whose execution is completed explicitly by each test."""

    token: str
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    streams: dict[str, bytes] = field(default_factory=dict)
    reconnect_streams: dict[str, bytes] = field(default_factory=dict)
    starts: list[dict[str, Any]] = field(default_factory=list)
    event_queries: list[dict[str, str]] = field(default_factory=list)
    start_status: int = 202
    start_body: dict[str, Any] | None = None
    process_status: int = 200
    ready_status: int = 200
    raw_status: int | None = None
    on_start: Callable[[str], None] | None = None

    def __post_init__(self) -> None:
        self.app = FastAPI()
        self._install_routes()

    def _install_routes(self) -> None:
        @self.app.get("/healthz")
        async def health() -> JSONResponse:
            return JSONResponse(
                status_code=self.process_status, content={"ok": self.process_status == 200}
            )

        @self.app.get("/readyz")
        async def ready() -> JSONResponse:
            return JSONResponse(
                status_code=self.ready_status, content={"ok": self.ready_status == 200}
            )

        @self.app.post("/v1/agent/runs/async")
        async def start(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
            if authorization != f"Bearer {self.token}":
                return JSONResponse(status_code=401, content={"detail": "secret upstream body"})
            body = await request.json()
            self.starts.append(body)
            if self.start_status != 202:
                return JSONResponse(
                    status_code=self.start_status, content={"detail": "secret upstream body"}
                )
            if self.start_body is not None:
                return JSONResponse(status_code=202, content=self.start_body)
            run_id = f"run-fake-{len(self.starts)}"
            self.runs[run_id] = unfinished_graph(run_id)
            self.streams[run_id] = sse_for_graph(self.runs[run_id])
            if self.on_start is not None:
                self.on_start(run_id)
            return JSONResponse(
                status_code=202, content={"run_id": run_id, "status": "accepted"}
            )

        @self.app.get("/v1/agent/runs/{run_id}")
        async def raw_run(run_id: str) -> JSONResponse:
            if self.raw_status is not None:
                return JSONResponse(
                    status_code=self.raw_status, content={"detail": "secret raw body"}
                )
            graph = self.runs.get(run_id)
            if graph is None:
                raise HTTPException(404, "missing")
            return JSONResponse(status_code=200, content=graph)

        @self.app.get("/v1/runs/{run_id}/events")
        async def events(run_id: str, request: Request) -> StreamingResponse:
            self.event_queries.append(dict(request.query_params))
            if run_id not in self.runs:
                raise HTTPException(404, "missing")

            async def body():  # type: ignore[no-untyped-def]
                if request.query_params.get("reconnect") == "1" and run_id in self.reconnect_streams:
                    yield self.reconnect_streams[run_id]
                else:
                    yield self.streams.get(run_id, sse_for_graph(self.runs[run_id]))

            return StreamingResponse(body(), media_type="text/event-stream")

    def complete(self, run_id: str, graph: dict[str, Any]) -> None:
        self.runs[run_id] = graph
        self.streams[run_id] = sse_for_graph(graph)
