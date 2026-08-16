"""Product state transitions, S17 policy, and run-readiness classification."""

from __future__ import annotations

import asyncio
import hashlib
import json
import posixpath
import secrets
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from physics_toy_factory.config import Settings
from physics_toy_factory.errors import ProductError, conflict
from physics_toy_factory.history import ArchivedSketch, HistoryPage, HistoryStore
from physics_toy_factory.models import (
    BrowserErrorBody,
    CodeResponse,
    RunKind,
    SessionRecord,
    SessionState,
    StartEnvelope,
)
from physics_toy_factory.prompts import creation_goal, follow_up_goal
from physics_toy_factory.s17_client import S17Client
from physics_toy_factory.session import SessionService
from physics_toy_factory.workspace import WorkspaceManager, WorkspaceSafetyError

CREATE_AUTHORITY = ["create_file", "edit_code", "run_command"]
FOLLOW_UP_AUTHORITY = ["edit_code", "run_command"]
SHELL_PLACEHOLDERS = (
    "__PTF_NONCE__",
    "__PTF_PREVIEW_ID_JSON__",
    "__PTF_P5_URL__",
    "__PTF_SKETCH_URL__",
)


@dataclass(frozen=True)
class Readiness:
    """A bounded product interpretation of a terminal raw S17 graph."""

    ready: bool
    reason: str


def normalizes_to_checker(command: object) -> bool:
    """Accept only the exact checker and target, with harmless leading ``./``."""

    if not isinstance(command, str):
        return False
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if len(argv) != 3 or Path(argv[0]).name != "node":
        return False
    return _normal_relative(argv[1]) == "p5check.js" and _normal_relative(argv[2]) == "sketch.js"


def _normal_relative(value: str) -> str | None:
    if not value or "\\" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    normalized = posixpath.normpath(value)
    return normalized if normalized not in {".", ""} else None


def classify_graph(graph: dict[str, Any]) -> Readiness:
    """Apply the graph-only portion of the normative readiness predicate."""

    if graph.get("finished") is not True:
        return Readiness(False, "run_not_finished")
    nodes = graph.get("nodes")
    events = graph.get("events")
    if not isinstance(nodes, dict) or not isinstance(events, list):
        return Readiness(False, "invalid_graph")
    if not any(
        isinstance(node, dict)
        and node.get("skill") == "answer_with_evidence"
        and node.get("state") == "succeeded"
        for node in nodes.values()
    ):
        return Readiness(False, "answer_missing")

    checkers: list[tuple[str, dict[str, Any], int, int]] = []
    sequence_by_node: dict[str, int] = {}
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("sequence"), int):
            continue
        node_id = event.get("node_id")
        if isinstance(node_id, str):
            sequence_by_node[node_id] = max(sequence_by_node.get(node_id, -1), event["sequence"])
    for insertion, (node_id, node) in enumerate(nodes.items()):
        if not isinstance(node_id, str) or not isinstance(node, dict):
            continue
        input_data = node.get("input")
        command = input_data.get("command") if isinstance(input_data, dict) else None
        if node.get("skill") == "run_command" and normalizes_to_checker(command):
            checkers.append((node_id, node, sequence_by_node.get(node_id, -1), insertion))
    if not checkers:
        return Readiness(False, "checker_missing")
    _node_id, latest, _sequence, _insertion = max(checkers, key=lambda item: (item[2], item[3]))
    result = latest.get("result")
    if not isinstance(result, dict):
        return Readiness(False, "checker_result_missing")
    if result.get("timed_out") is not False:
        return Readiness(False, "checker_timed_out")
    exit_code = result.get("exit_code")
    if isinstance(exit_code, bool) or exit_code != 0:
        return Readiness(False, "checker_failed")
    return Readiness(True, "ready")


class Orchestrator:
    """Coordinate the single session, trusted workspace, and S17 adapter."""

    def __init__(
        self,
        settings: Settings,
        workspace: WorkspaceManager,
        session: SessionService,
        s17: S17Client,
        history: HistoryStore,
    ) -> None:
        self._settings = settings
        self._workspace = workspace
        self._session = session
        self._s17 = s17
        self._history = history
        self._history_preview_lock = asyncio.Lock()
        self._history_preview: _HistoryPreviewBinding | None = None

    async def create(self, prompt: str) -> StartEnvelope:
        """Start one least-privilege creation after all local preconditions pass."""

        normalized = self._validate_prompt(prompt)
        self._validate_workspace()
        if (self._workspace.root / "sketch.js").exists():
            raise conflict("reset_required", "Reset the workspace before creating a toy.")
        return await self._session.start(
            kind=RunKind.CREATE,
            prompt=normalized,
            starter=lambda: self._s17.start_run(
                goal=creation_goal(normalized), allowed_side_effects=CREATE_AUTHORITY
            ),
        )

    async def follow_up(self, prompt: str) -> StartEnvelope:
        """Start the one linked anchored-edit run with narrower authority."""

        normalized = self._validate_prompt(prompt)
        snapshot = await self._session.snapshot()
        if snapshot.active_run_id is not None:
            raise conflict("run_active", "A run is already active.")
        if snapshot.state is not SessionState.READY:
            raise conflict("follow_up_not_ready", "Create and verify a toy before modifying it.")
        if snapshot.follow_up_used:
            raise conflict("follow_up_used", "The one follow-up has already been used.")
        self._validate_workspace()
        current = self._read_sketch()
        if (
            snapshot.state is not SessionState.READY
            or snapshot.current_sketch_sha256 is None
            or current.sha256 != snapshot.current_sketch_sha256
        ):
            raise conflict("sketch_changed", "The verified sketch changed; reset before continuing.")
        return await self._session.start(
            kind=RunKind.FOLLOW_UP,
            prompt=normalized,
            starter=lambda: self._s17.start_run(
                goal=follow_up_goal(normalized), allowed_side_effects=FOLLOW_UP_AUTHORITY
            ),
        )

    async def get_run(self, run_id: str) -> dict[str, Any]:
        """Proxy a session-owned raw graph and fold terminal state."""

        await self._session.require_owned(run_id)
        graph = await self._s17.get_run(run_id)
        await self._observe(run_id, graph)
        return graph

    async def history_page(
        self, *, limit: int, cursor: str | None, query: str
    ) -> HistoryPage:
        """List only product-owned saved runs without consulting arbitrary upstream IDs."""

        return self._history.list_runs(limit=limit, cursor=cursor, query=query.strip())

    async def history_detail(self, history_id: str) -> dict[str, Any]:
        """Return one saved graph, refreshing only a still-current owned run."""

        detail = self._history.detail(history_id)
        summary = detail["history"]
        degraded: dict[str, object] | None = None
        if summary.get("outcome") == "running" and await self._session.owns(summary["run_id"]):
            try:
                await self.get_run(summary["run_id"])
                detail = self._history.detail(history_id)
            except ProductError as exc:
                degraded = {
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                }
        detail["degraded"] = degraded
        return detail

    async def history_code(self, history_id: str) -> CodeResponse:
        """Return the exact hash-validated archived sketch for one saved run."""

        sketch = self._history.archived_sketch(history_id)
        detail = self._history.detail(history_id)["history"]
        return CodeResponse(
            content=sketch.content,
            bytes=sketch.bytes,
            sha256=sketch.sha256,
            verified=True,
            verified_run_id=detail["run_id"],
        )

    async def prepare_history_preview(self, history_id: str) -> dict[str, object]:
        """Issue a server-owned preview identity for immutable archived bytes."""

        self._validate_workspace()
        sketch = self._history.archived_sketch(history_id)
        preview_id = secrets.token_urlsafe(32)
        async with self._history_preview_lock:
            self._history_preview = _HistoryPreviewBinding(
                history_id=history_id,
                preview_id=preview_id,
                revision=sketch.sha256,
            )
        return {
            "preview_id": preview_id,
            "history_id": history_id,
            "revision": sketch.sha256,
            "url": f"/history-preview/{history_id}?preview_id={preview_id}",
            "ready_timeout_ms": round(self._settings.preview_ready_timeout_seconds * 1000),
        }

    async def history_preview_shell(
        self, *, history_id: str, preview_id: str
    ) -> tuple[str, str]:
        """Consume one historical preview shell lease and bind its local asset URLs."""

        binding = await self._consume_history_preview(history_id=history_id, preview_id=preview_id)
        self._validate_workspace()
        sketch = self._history.archived_sketch(history_id)
        if sketch.sha256 != binding.revision:
            raise conflict("preview_not_ready", "The saved preview failed integrity validation.")
        query = f"preview_id={preview_id}"
        return self._render_preview_shell(
            preview_id=preview_id,
            p5_url=f"/api/history/{history_id}/preview/p5.min.js?{query}",
            sketch_url=f"/api/history/{history_id}/preview/sketch.js?{query}",
        )

    async def history_preview_javascript(
        self, *, history_id: str, preview_id: str, asset: str
    ) -> bytes:
        """Serve only trusted p5.js or the exact archived sketch for a bound lease."""

        binding = await self._require_history_preview(
            history_id=history_id, preview_id=preview_id
        )
        self._validate_workspace()
        sketch = self._history.archived_sketch(history_id)
        if sketch.sha256 != binding.revision:
            raise conflict("preview_not_ready", "The saved preview failed integrity validation.")
        if asset == "sketch.js":
            return sketch.content.encode("utf-8")
        if asset == "p5.min.js":
            return self._read_trusted_bytes("shell/p5.min.js")
        raise ProductError(404, "preview_asset_not_found", "Preview asset does not exist.")

    async def delete_history(self, history_id: str) -> None:
        """Remove one non-current local archive without touching S17 journals."""

        current = await self._session.snapshot()
        self._history.delete(history_id, current)
        async with self._history_preview_lock:
            if self._history_preview and self._history_preview.history_id == history_id:
                self._history_preview = None

    async def refresh_active(self) -> None:
        """Fold a terminal active graph when session state is requested."""

        snapshot = await self._session.snapshot()
        if snapshot.active_run_id is None:
            return
        graph = await self._s17.get_run(snapshot.active_run_id)
        await self._observe(snapshot.active_run_id, graph)

    async def code(self) -> CodeResponse:
        """Read only fixed ``sketch.js`` and report exact verification linkage."""

        self._validate_workspace()
        sketch = self._read_sketch()
        snapshot = await self._session.snapshot()
        verified_run_id = next(
            (
                link.run_id
                for link in reversed(snapshot.runs)
                if link.verified_sketch_sha256 == sketch.sha256
            ),
            None,
        )
        verified = snapshot.current_sketch_sha256 == sketch.sha256 and verified_run_id is not None
        return CodeResponse(
            content=sketch.content,
            bytes=sketch.bytes,
            sha256=sketch.sha256,
            verified=verified,
            verified_run_id=verified_run_id if verified else None,
        )

    async def prepare_preview(self, revision: str) -> dict[str, object]:
        """Issue a random, server-bound identity for one verified iframe load."""

        await self._require_verified_sketch(revision)
        preview_id = secrets.token_urlsafe(32)
        binding = await self._session.bind_preview(preview_id=preview_id, revision=revision)
        return {
            "preview_id": preview_id,
            "run_id": binding.run_id,
            "revision": revision,
            "url": f"/preview/{revision}?preview_id={preview_id}",
            "ready_timeout_ms": round(self._settings.preview_ready_timeout_seconds * 1000),
        }

    async def preview_shell(self, *, revision: str, preview_id: str) -> tuple[str, str]:
        """Render the trusted shell with a fresh nonce after rechecking the preview gate."""

        await self._session.consume_preview_shell(preview_id=preview_id, revision=revision)
        await self._require_verified_sketch(revision)
        query = f"revision={revision}&preview_id={preview_id}"
        return self._render_preview_shell(
            preview_id=preview_id,
            p5_url=f"/api/preview/p5.min.js?{query}",
            sketch_url=f"/api/preview/sketch.js?{query}",
        )

    async def preview_javascript(
        self, *, revision: str, preview_id: str, asset: str
    ) -> bytes:
        """Serve only the fixed p5 runtime or exact verified sketch bytes."""

        sketch = await self._require_preview_asset(revision=revision, preview_id=preview_id)
        if asset == "sketch.js":
            return sketch.content.encode("utf-8")
        if asset == "p5.min.js":
            return self._read_trusted_bytes("shell/p5.min.js")
        raise ProductError(404, "preview_asset_not_found", "Preview asset does not exist.")

    async def browser_error(
        self, *, run_id: str, body: BrowserErrorBody
    ) -> SessionRecord:
        """Record one bounded error only for the currently bound iframe."""

        await self._session.require_owned(run_id)
        error = body.model_dump(mode="json")
        return await self._session.record_browser_error(
            run_id=run_id,
            preview_id=body.preview_id,
            error=error,
        )

    async def reset(self):  # type: ignore[no-untyped-def]
        """Reset the dedicated workspace without touching S17 journals."""

        async def resetter() -> None:
            try:
                await asyncio.to_thread(self._workspace.reset, idle=True)
            except WorkspaceSafetyError as exc:
                raise ProductError(
                    409, "workspace_invalid", "Trusted workspace validation failed."
                ) from exc

        return await self._session.reset(resetter)

    async def _observe(self, run_id: str, graph: dict[str, Any]) -> None:
        if graph.get("finished") is not True:
            await self._session.observe_graph(run_id, graph)
            return
        readiness = classify_graph(graph)
        archived: ArchivedSketch | None = None
        if readiness.ready:
            try:
                self._validate_workspace()
                sketch = self._read_sketch()
                archived = ArchivedSketch(sketch.content, sketch.bytes, sketch.sha256)
            except ProductError:
                readiness = Readiness(False, "sketch_invalid")
        await self._session.finish(
            run_id,
            ready=readiness.ready,
            graph=graph,
            sketch=archived,
        )

    async def _consume_history_preview(
        self, *, history_id: str, preview_id: str
    ) -> _HistoryPreviewBinding:
        async with self._history_preview_lock:
            binding = self._history_preview
            if (
                binding is None
                or binding.history_id != history_id
                or binding.preview_id != preview_id
                or binding.shell_served
            ):
                raise conflict("preview_not_ready", "Only the selected saved preview is available.")
            opened = _HistoryPreviewBinding(
                history_id=binding.history_id,
                preview_id=binding.preview_id,
                revision=binding.revision,
                shell_served=True,
            )
            self._history_preview = opened
            return opened

    async def _require_history_preview(
        self, *, history_id: str, preview_id: str
    ) -> _HistoryPreviewBinding:
        async with self._history_preview_lock:
            binding = self._history_preview
            if (
                binding is None
                or binding.history_id != history_id
                or binding.preview_id != preview_id
                or not binding.shell_served
            ):
                raise conflict("preview_not_ready", "Only the selected saved preview is available.")
            return binding

    def _render_preview_shell(
        self, *, preview_id: str, p5_url: str, sketch_url: str
    ) -> tuple[str, str]:
        template = self._read_trusted_text("shell/index.html")
        if any(template.count(placeholder) == 0 for placeholder in SHELL_PLACEHOLDERS):
            raise ProductError(409, "workspace_invalid", "Trusted preview shell is invalid.")
        nonce = secrets.token_urlsafe(32)
        rendered = (
            template.replace("__PTF_NONCE__", nonce)
            .replace("__PTF_PREVIEW_ID_JSON__", _json_string(preview_id))
            .replace("__PTF_P5_URL__", p5_url)
            .replace("__PTF_SKETCH_URL__", sketch_url)
        )
        if any(placeholder in rendered for placeholder in SHELL_PLACEHOLDERS):
            raise ProductError(409, "workspace_invalid", "Trusted preview shell is invalid.")
        return rendered, nonce

    def _validate_prompt(self, prompt: str) -> str:
        normalized = prompt.strip()
        if not normalized:
            raise ProductError(422, "prompt_empty", "Prompt must not be empty.")
        if len(normalized) > self._settings.max_prompt_chars:
            raise ProductError(422, "prompt_too_long", "Prompt exceeds the configured limit.")
        return normalized

    def _validate_workspace(self) -> None:
        try:
            self._workspace.validate_identity()
        except WorkspaceSafetyError as exc:
            raise ProductError(409, "workspace_invalid", "Trusted workspace validation failed.") from exc

    def _read_sketch(self) -> _Sketch:
        path = self._workspace.root / "sketch.js"
        try:
            stat = path.lstat()
        except FileNotFoundError as exc:
            raise ProductError(404, "sketch_not_found", "No sketch has been generated.") from exc
        if path.is_symlink() or not path.is_file():
            raise ProductError(409, "sketch_invalid", "Generated sketch is not a regular file.")
        if stat.st_size < 1 or stat.st_size > self._settings.max_sketch_bytes:
            raise ProductError(409, "sketch_invalid", "Generated sketch has an invalid size.")
        payload = path.read_bytes()
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProductError(409, "sketch_invalid", "Generated sketch is not UTF-8.") from exc
        return _Sketch(content, len(payload), hashlib.sha256(payload).hexdigest())

    async def _require_verified_sketch(self, revision: str) -> _Sketch:
        self._validate_workspace()
        snapshot = await self._session.snapshot()
        if (
            snapshot.state is not SessionState.READY
            or snapshot.current_sketch_sha256 != revision
        ):
            raise conflict("preview_not_ready", "Only the current verified sketch can be previewed.")
        sketch = self._read_sketch()
        if sketch.sha256 != revision:
            raise conflict("preview_not_ready", "The verified sketch changed; preview is blocked.")
        return sketch

    async def _require_preview_asset(self, *, revision: str, preview_id: str) -> _Sketch:
        await self._session.require_preview(preview_id=preview_id, revision=revision)
        return await self._require_verified_sketch(revision)

    def _read_trusted_bytes(self, relative_path: str) -> bytes:
        path = self._workspace.root / relative_path
        try:
            if path.is_symlink() or not path.is_file():
                raise OSError
            return path.read_bytes()
        except OSError as exc:
            raise ProductError(409, "workspace_invalid", "Trusted preview asset is unavailable.") from exc

    def _read_trusted_text(self, relative_path: str) -> str:
        try:
            return self._read_trusted_bytes(relative_path).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProductError(409, "workspace_invalid", "Trusted preview asset is invalid.") from exc


@dataclass(frozen=True)
class _Sketch:
    content: str
    bytes: int
    sha256: str


@dataclass(frozen=True)
class _HistoryPreviewBinding:
    history_id: str
    preview_id: str
    revision: str
    shell_served: bool = False


def _json_string(value: str) -> str:
    """Encode a token for a JavaScript string literal without HTML-significant bytes."""

    return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
