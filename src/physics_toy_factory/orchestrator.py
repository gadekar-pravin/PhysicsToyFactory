"""Product state transitions, S17 policy, and run-readiness classification."""

from __future__ import annotations

import asyncio
import hashlib
import posixpath
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from physics_toy_factory.config import Settings
from physics_toy_factory.errors import ProductError, conflict
from physics_toy_factory.models import CodeResponse, RunKind, SessionState, StartEnvelope
from physics_toy_factory.prompts import creation_goal, follow_up_goal
from physics_toy_factory.s17_client import S17Client
from physics_toy_factory.session import SessionService
from physics_toy_factory.workspace import WorkspaceManager, WorkspaceSafetyError

CREATE_AUTHORITY = ["create_file", "edit_code", "run_command"]
FOLLOW_UP_AUTHORITY = ["edit_code", "run_command"]


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
    ) -> None:
        self._settings = settings
        self._workspace = workspace
        self._session = session
        self._s17 = s17

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
            return
        readiness = classify_graph(graph)
        sketch_sha256: str | None = None
        if readiness.ready:
            try:
                self._validate_workspace()
                sketch_sha256 = self._read_sketch().sha256
            except ProductError:
                readiness = Readiness(False, "sketch_invalid")
        await self._session.finish(run_id, ready=readiness.ready, sketch_sha256=sketch_sha256)

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


@dataclass(frozen=True)
class _Sketch:
    content: str
    bytes: int
    sha256: str
