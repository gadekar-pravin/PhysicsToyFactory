"""Live-qualification evidence collection and fail-closed proof analysis."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

import httpx

from physics_toy_factory.config import Settings
from physics_toy_factory.orchestrator import normalizes_to_checker
from physics_toy_factory.workspace import WorkspaceManager

REPAIR_AUTHORITY = ["edit_code", "run_command"]
REPAIR_GOAL = """Repair the known-broken Physics Toy Factory sketch in sketch.js.

Required process:
- Read sketch.js before changing it.
- Run exactly: node p5check.js sketch.js before any edit and treat its nonzero exit as evidence.
- Repair the defect with edit_code using an exact unique anchor; do not recreate or overwrite the file.
- Preserve the intended canvas, background, and circle behavior.
- Run exactly: node p5check.js sketch.js after the edit.
- Finish only after the latest checker result exits 0.
"""
RUN_ID_PATTERN = re.compile(r"^run-[A-Za-z0-9._-]{1,124}$")
MACHINE_PATH_PATTERN = re.compile(r"(?:/Users/|/home/|/private/(?:tmp|var)/)[^\s\"']+")


class QualificationError(RuntimeError):
    """Raised when live evidence is missing, inconsistent, or unsafe to publish."""


@dataclass(frozen=True)
class RepairChain:
    """The ordered red, anchored-edit, and green nodes proven by a raw graph."""

    red_checker_node_id: str
    red_exit_code: int
    edit_node_id: str
    green_checker_node_id: str
    red_sequence: int
    edit_sequence: int
    green_sequence: int


@dataclass(frozen=True)
class ProofResult:
    """Paths and identifiers retained for one successful repair proof."""

    run_id: str
    artifact_dir: Path
    sketch_sha256: str
    chain: RepairChain


def utc_timestamp() -> str:
    """Return an unambiguous, second-resolution evidence timestamp."""

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    """Hash one retained file without loading it twice at call sites."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65_536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def analyze_repair_graph(graph: Mapping[str, Any]) -> RepairChain:
    """Require a journal-ordered red, anchored edit, green chain with latest green."""

    if graph.get("finished") is not True:
        raise QualificationError("repair run did not finish")
    nodes = graph.get("nodes")
    events = graph.get("events")
    if not isinstance(nodes, dict) or not isinstance(events, list):
        raise QualificationError("repair graph has an invalid shape")

    succeeded_sequence: dict[str, int] = {}
    for event in events:
        if not isinstance(event, dict) or event.get("kind") != "task_succeeded":
            continue
        node_id = event.get("node_id")
        sequence = event.get("sequence")
        if isinstance(node_id, str) and isinstance(sequence, int):
            succeeded_sequence[node_id] = max(succeeded_sequence.get(node_id, -1), sequence)

    checkers: list[tuple[int, str, int]] = []
    edits: list[tuple[int, str]] = []
    for node_id, node in nodes.items():
        if not isinstance(node_id, str) or not isinstance(node, dict):
            continue
        sequence = succeeded_sequence.get(node_id)
        if sequence is None or node.get("state") != "succeeded":
            continue
        input_data = node.get("input")
        result = node.get("result")
        if node.get("skill") == "run_command" and isinstance(input_data, dict):
            if not normalizes_to_checker(input_data.get("command")) or not isinstance(result, dict):
                continue
            exit_code = result.get("exit_code")
            if isinstance(exit_code, bool) or not isinstance(exit_code, int):
                raise QualificationError(f"checker {node_id} has no integer exit code")
            if result.get("timed_out") is not False:
                raise QualificationError(f"checker {node_id} timed out")
            checkers.append((sequence, node_id, exit_code))
        if node.get("skill") == "edit_code":
            if not isinstance(result, dict):
                continue
            if result.get("replaced") != 1 or result.get("occurrences_found") != 1:
                continue
            edits.append((sequence, node_id))

    checkers.sort()
    edits.sort()
    if not checkers:
        raise QualificationError("repair run contains no qualifying checker")
    if checkers[-1][2] != 0:
        raise QualificationError("latest checker did not pass")

    for red_sequence, red_id, red_exit in checkers:
        if red_exit == 0:
            continue
        for edit_sequence, edit_id in edits:
            if edit_sequence <= red_sequence:
                continue
            green = next(
                (
                    item
                    for item in checkers
                    if item[0] > edit_sequence and item[2] == 0
                ),
                None,
            )
            if green is None:
                continue
            green_sequence, green_id, _green_exit = green
            if green_id != checkers[-1][1]:
                continue
            return RepairChain(
                red_checker_node_id=red_id,
                red_exit_code=red_exit,
                edit_node_id=edit_id,
                green_checker_node_id=green_id,
                red_sequence=red_sequence,
                edit_sequence=edit_sequence,
                green_sequence=green_sequence,
            )
    raise QualificationError("no red, anchored-edit, latest-green chain exists")


def model_routes(graph: Mapping[str, Any]) -> list[dict[str, str]]:
    """Extract distinct provider/model routes from nodes and metered planner evidence."""

    routes: set[tuple[str, str]] = set()

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        provider = value.get("provider")
        model = value.get("model")
        if isinstance(provider, str) and isinstance(model, str):
            routes.add((provider, model))
        for item in value.values():
            visit(item)

    visit(graph)
    return [{"provider": provider, "model": model} for provider, model in sorted(routes)]


def sanitize_for_publication(
    value: Any,
    *,
    replacements: Mapping[str, str],
    forbidden_values: Iterable[str] = (),
) -> Any:
    """Replace known machine paths and reject secrets or unknown host paths."""

    forbidden = tuple(item for item in forbidden_values if item)

    def sanitize(item: Any) -> Any:
        if isinstance(item, dict):
            return {str(key): sanitize(nested) for key, nested in item.items()}
        if isinstance(item, list):
            return [sanitize(nested) for nested in item]
        if not isinstance(item, str):
            return item
        if any(secret in item for secret in forbidden):
            raise QualificationError("selected evidence contains a configured secret")
        cleaned = item
        for source, marker in sorted(replacements.items(), key=lambda pair: len(pair[0]), reverse=True):
            if source:
                cleaned = cleaned.replace(source, marker)
        if MACHINE_PATH_PATTERN.search(cleaned):
            raise QualificationError("selected evidence contains an unreviewed machine path")
        return cleaned

    return sanitize(value)


def publish_json_artifact(
    source: Path,
    destination: Path,
    *,
    replacements: Mapping[str, str],
    forbidden_values: Iterable[str],
) -> None:
    """Publish one reviewed JSON or JSONL artifact with deterministic formatting."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix == ".jsonl":
        output: list[str] = []
        for line in source.read_text(encoding="utf-8").splitlines():
            if line.strip():
                sanitized = sanitize_for_publication(
                    json.loads(line), replacements=replacements, forbidden_values=forbidden_values
                )
                output.append(json.dumps(sanitized, ensure_ascii=False, sort_keys=True))
        destination.write_text("\n".join(output) + "\n", encoding="utf-8")
        return
    sanitized = sanitize_for_publication(
        json.loads(source.read_text(encoding="utf-8")),
        replacements=replacements,
        forbidden_values=forbidden_values,
    )
    destination.write_text(
        json.dumps(sanitized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def refresh_published_artifact_hashes(summary_path: Path, artifact_root: Path) -> None:
    """Make a selected summary hash the sanitized files committed beside it."""

    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise QualificationError("published summary has an invalid shape")

    def selected_hash(name: Any) -> str | None:
        if not isinstance(name, str) or Path(name).name != name:
            return None
        candidate = artifact_root / name
        if not candidate.is_file() or candidate.is_symlink():
            return None
        return sha256_file(candidate)

    artifacts = payload.get("artifacts")
    if isinstance(artifacts, dict):
        for artifact in artifacts.values():
            if not isinstance(artifact, dict):
                continue
            digest = selected_hash(artifact.get("name"))
            if digest is not None:
                artifact["sha256"] = digest

    scenarios = payload.get("scenarios")
    if isinstance(scenarios, list):
        for scenario in scenarios:
            if not isinstance(scenario, dict):
                continue
            graph_digest = selected_hash(scenario.get("raw_graph_name"))
            if graph_digest is not None:
                scenario["raw_graph_sha256"] = graph_digest
            tape_digest = selected_hash(scenario.get("event_tape_name"))
            if tape_digest is not None:
                scenario["event_tape_sha256"] = tape_digest

    summary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class RepairProofRunner:
    """Reset an idle product workspace and retain one genuine live repair chain."""

    def __init__(
        self,
        settings: Settings,
        *,
        product_base_url: str,
        timeout_seconds: float,
        budget_usd: float | None,
    ) -> None:
        self._settings = settings
        self._product_base_url = product_base_url.rstrip("/")
        self._s17_base_url = str(settings.s17_base_url).rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._budget_usd = budget_usd
        self._workspace = WorkspaceManager(settings)

    async def run(self, *, publish_dir: Path | None = None) -> ProofResult:
        """Execute and retain the proof, raising instead of manufacturing success."""

        timeout = httpx.Timeout(connect=5, read=None, write=10, pool=5)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            await self._require_idle_and_reset(client)
            self._install_broken_fixture()
            run_id = await self._start(client)
            artifact_dir = self._settings.artifact_dir / "repair-proof" / run_id
            artifact_dir.mkdir(parents=True, exist_ok=False)
            event_path = artifact_dir / "event-tape.jsonl"
            await self._retain_stream(client, run_id, event_path)
            graph = await self._get_graph(client, run_id)

        graph_path = artifact_dir / "raw-graph.json"
        graph_path.write_text(
            json.dumps(graph, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        chain = analyze_repair_graph(graph)
        sketch_path = self._settings.workspace / "sketch.js"
        self._workspace.validate_identity()
        if not sketch_path.is_file() or sketch_path.is_symlink():
            raise QualificationError("repair run did not leave a regular sketch.js")
        sketch_sha256 = sha256_file(sketch_path)
        summary = {
            "schema_version": 1,
            "evidence_kind": "repair_proof",
            "outcome": "passed",
            "run_id": run_id,
            "recorded_at": utc_timestamp(),
            "sketch_sha256": sketch_sha256,
            "repair_chain": asdict(chain),
            "model_routes": model_routes(graph),
            "environment": {
                "s17_base_url": self._s17_base_url,
                "container_mode_configured": self._settings.s17_exec_container,
                "container_image": self._settings.s17_exec_image,
            },
            "artifacts": {
                "raw_graph": {"name": graph_path.name, "sha256": sha256_file(graph_path)},
                "event_tape": {"name": event_path.name, "sha256": sha256_file(event_path)},
            },
        }
        summary_path = artifact_dir / "summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if publish_dir is not None:
            self._publish(artifact_dir, publish_dir)
        return ProofResult(run_id, artifact_dir, sketch_sha256, chain)

    async def _require_idle_and_reset(self, client: httpx.AsyncClient) -> None:
        try:
            response = await client.get(f"{self._product_base_url}/api/session")
        except httpx.HTTPError as exc:
            raise QualificationError("product session is unreachable; idle state is unproven") from exc
        if response.status_code != 200:
            raise QualificationError("product session could not be inspected")
        payload = response.json()
        session = payload.get("session") if isinstance(payload, dict) else None
        if not isinstance(session, dict):
            raise QualificationError("product session response is invalid")
        if session.get("active_run_id") is not None or session.get("state") in {"running", "modifying"}:
            raise QualificationError("scratch workspace is not idle")
        reset = await client.post(f"{self._product_base_url}/api/session/reset")
        if reset.status_code != 200:
            raise QualificationError("product refused the idle workspace reset")
        reset_payload = reset.json()
        reset_session = reset_payload.get("session") if isinstance(reset_payload, dict) else None
        if not isinstance(reset_session, dict) or reset_session.get("state") != "empty":
            raise QualificationError("product reset did not produce an empty session")
        self._workspace.reset(idle=True)

    def _install_broken_fixture(self) -> None:
        fixture = resources.files("physics_toy_factory").joinpath(
            "demo_fixtures", "broken_sketch.js"
        )
        payload = fixture.read_bytes()
        sketch_path = self._settings.workspace / "sketch.js"
        sketch_path.write_bytes(payload)
        completed = subprocess.run(
            ["node", "p5check.js", "sketch.js"],
            cwd=self._settings.workspace,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode == 0:
            raise QualificationError("known-broken fixture unexpectedly passed before the live run")

    async def _start(self, client: httpx.AsyncClient) -> str:
        body: dict[str, Any] = {
            "tenant_id": "physics-toy-factory",
            "project_id": "demo",
            "user_id": "local-audience",
            "agent_id": "p5-builder",
            "respond_as": "text",
            "prompt": REPAIR_GOAL,
            "allowed_side_effects": REPAIR_AUTHORITY,
        }
        if self._budget_usd is not None:
            body["budget"] = self._budget_usd
            body["principal"] = "session:physics-toy-factory-repair-proof"
        response = await client.post(
            f"{self._s17_base_url}/v1/agent/runs/async",
            json=body,
            headers={
                "Authorization": (
                    f"Bearer {self._settings.s17_control_token.get_secret_value()}"
                )
            },
        )
        if response.status_code != 202:
            raise QualificationError(f"S17 repair-proof start failed with HTTP {response.status_code}")
        payload = response.json()
        run_id = payload.get("run_id") if isinstance(payload, dict) else None
        if (
            not isinstance(run_id, str)
            or not RUN_ID_PATTERN.fullmatch(run_id)
            or payload.get("status") != "accepted"
        ):
            raise QualificationError("S17 repair-proof start returned an invalid acceptance")
        return run_id

    async def _retain_stream(
        self, client: httpx.AsyncClient, run_id: str, destination: Path
    ) -> None:
        terminal = False
        with destination.open("x", encoding="utf-8") as output:
            try:
                async with client.stream(
                    "GET",
                    f"{self._s17_base_url}/v1/runs/{run_id}/events",
                    params={"after": 0, "reconnect": 0},
                ) as response:
                    if response.status_code != 200:
                        raise QualificationError(
                            f"S17 event stream failed with HTTP {response.status_code}"
                        )
                    frame: list[str] = []
                    async for line in response.aiter_lines():
                        if line:
                            frame.append(line)
                            continue
                        if frame:
                            record = _parse_sse_frame(frame)
                            output.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                            output.flush()
                            data = record.get("data")
                            terminal = terminal or (
                                isinstance(data, dict) and data.get("type") == "RUN_FINISHED"
                            )
                        frame = []
            except httpx.HTTPError as exc:
                raise QualificationError("S17 event stream disconnected") from exc
        if not terminal:
            raise QualificationError("S17 event stream ended without RUN_FINISHED")

    async def _get_graph(self, client: httpx.AsyncClient, run_id: str) -> dict[str, Any]:
        response = await client.get(f"{self._s17_base_url}/v1/agent/runs/{run_id}")
        if response.status_code != 200:
            raise QualificationError(f"S17 raw graph failed with HTTP {response.status_code}")
        if len(response.content) > 4_000_000:
            raise QualificationError("S17 raw graph exceeded the proof limit")
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("run_id") != run_id:
            raise QualificationError("S17 raw graph response is invalid")
        return payload

    def _publish(self, artifact_dir: Path, destination: Path) -> None:
        replacements = {
            str(self._settings.workspace): "<PTF_WORKSPACE>",
            str(self._settings.artifact_dir): "<PTF_ARTIFACT_DIR>",
            str(Path.home()): "<HOME>",
        }
        forbidden = (self._settings.s17_control_token.get_secret_value(),)
        destination.mkdir(parents=True, exist_ok=True)
        for name in ("raw-graph.json", "event-tape.jsonl", "summary.json"):
            publish_json_artifact(
                artifact_dir / name,
                destination / name,
                replacements=replacements,
                forbidden_values=forbidden,
            )
        refresh_published_artifact_hashes(destination / "summary.json", destination)


def _parse_sse_frame(lines: list[str]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    data_lines: list[str] = []
    comments: list[str] = []
    for line in lines:
        if line.startswith("id:"):
            record["id"] = line[3:].strip()
        elif line.startswith("event:"):
            record["event"] = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif line.startswith(":"):
            comments.append(line[1:].strip())
    if data_lines:
        text = "\n".join(data_lines)
        try:
            record["data"] = json.loads(text)
        except json.JSONDecodeError as exc:
            raise QualificationError("S17 event stream emitted malformed JSON") from exc
    if comments:
        record["comments"] = comments
    if not record:
        raise QualificationError("S17 event stream emitted an empty frame")
    return record
