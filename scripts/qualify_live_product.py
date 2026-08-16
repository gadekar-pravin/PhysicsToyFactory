"""Exercise the exact Phase 6 product demo and four suggested prompts live."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from physics_toy_factory.config import load_settings
from physics_toy_factory.orchestrator import classify_graph
from physics_toy_factory.qualification import (
    QualificationError,
    _parse_sse_frame,
    model_routes,
    publish_json_artifact,
    refresh_published_artifact_hashes,
    sha256_file,
    utc_timestamp,
)

SUGGESTED_PROMPTS = (
    "Rain that avoids my mouse",
    "Bouncy magnets",
    "Angry solar system",
    "Fish that follow my cursor",
)
SOLAR_PROMPT = "Create a tiny solar system."
TRAIL_PROMPT = "Make the planets leave glowing trails."


@dataclass(frozen=True)
class ScenarioResult:
    """One live run recorded through the product API."""

    name: str
    prompt: str
    run_id: str | None
    kind: str
    outcome: str
    reason: str
    sketch_sha256: str | None
    model_routes: list[dict[str, str]]
    raw_graph_name: str | None
    raw_graph_sha256: str | None
    event_tape_name: str | None
    event_tape_sha256: str | None


def parse_args() -> argparse.Namespace:
    """Parse live qualification paths and bounded timeout."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--product-base-url", default="http://127.0.0.1:8120")
    parser.add_argument("--timeout-seconds", type=float, default=900)
    parser.add_argument("--publish-dir", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--recover-dir",
        type=Path,
        help="Finalize a stopped failed qualification from its ignored artifact directory.",
    )
    mode.add_argument(
        "--solar-canary-only",
        action="store_true",
        help="Run exactly one solar-system creation canary; never run suggestions or follow-up.",
    )
    return parser.parse_args()


class LiveProductQualifier:
    """Run each required prompt once and fail on any non-ready outcome."""

    def __init__(
        self,
        *,
        product_base_url: str,
        artifact_dir: Path,
        workspace: Path,
        control_token: str,
    ) -> None:
        self._base_url = product_base_url.rstrip("/")
        self._artifact_root = artifact_dir / "live-qualification" / utc_timestamp().replace(":", "-")
        self._artifact_root.mkdir(parents=True, exist_ok=False)
        self._workspace = workspace
        self._artifact_dir = artifact_dir
        self._control_token = control_token

    async def run(self, *, publish_dir: Path | None) -> Path:
        """Record four suggestions, then leave the exact two-step demo ready."""

        timeout = httpx.Timeout(connect=5, read=None, write=10, pool=5)
        results: list[ScenarioResult] = []
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            session_payload = await self._json(client, "GET", "/api/session", expected=200)
            advertised = session_payload.get("suggested_prompts")
            if advertised != list(SUGGESTED_PROMPTS):
                raise QualificationError("product suggested prompts do not match the Phase 6 contract")
            session = session_payload.get("session")
            if not isinstance(session, dict):
                raise QualificationError("product session response is invalid")
            start_index = 0
            runs = session.get("runs")
            if isinstance(runs, list) and runs:
                latest = runs[-1]
                if (
                    isinstance(latest, dict)
                    and latest.get("kind") == "create"
                    and latest.get("user_prompt") == SUGGESTED_PROMPTS[0]
                    and isinstance(latest.get("run_id"), str)
                    and session.get("state") in {"running", "ready", "failed"}
                ):
                    results.append(
                        await self._collect(
                            client,
                            name="suggestion-1-rain-that-avoids-my-mouse",
                            prompt=SUGGESTED_PROMPTS[0],
                            run_id=latest["run_id"],
                            expected_kind="create",
                        )
                    )
                    start_index = 1
                elif session.get("state") in {"running", "modifying"}:
                    raise QualificationError("an unrelated product run is active")
            for index, prompt in enumerate(SUGGESTED_PROMPTS[start_index:], start=start_index + 1):
                await self._reset(client)
                results.append(
                    await self._execute(
                        client,
                        name=f"suggestion-{index}-{_slug(prompt)}",
                        prompt=prompt,
                        endpoint="/api/runs",
                        expected_kind="create",
                    )
                )
            await self._reset(client)
            results.append(
                await self._execute(
                    client,
                    name="demo-create-tiny-solar-system",
                    prompt=SOLAR_PROMPT,
                    endpoint="/api/runs",
                    expected_kind="create",
                )
            )
            results.append(
                await self._execute(
                    client,
                    name="demo-follow-up-glowing-trails",
                    prompt=TRAIL_PROMPT,
                    endpoint="/api/runs/follow-up",
                    expected_kind="follow_up",
                )
            )

        passed = self._write_summary(
            results,
            evidence_kind="live_product_qualification",
        )
        if publish_dir is not None:
            self._publish(publish_dir)
        if not passed:
            failures = ", ".join(
                f"{result.name}: {result.reason}"
                for result in results
                if result.outcome != "ready"
            )
            raise QualificationError(f"one or more live scenarios failed: {failures}")
        return self._artifact_root

    async def run_solar_canary(self, *, publish_dir: Path | None) -> Path:
        """Run exactly one demo-critical creation attempt and retain its real outcome."""

        timeout = httpx.Timeout(connect=5, read=None, write=10, pool=5)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            await self._reset(client)
            result = await self._execute(
                client,
                name="canary-create-tiny-solar-system",
                prompt=SOLAR_PROMPT,
                endpoint="/api/runs",
                expected_kind="create",
            )

        passed = self._write_summary(
            [result],
            evidence_kind="live_product_canary",
        )
        if publish_dir is not None:
            self._publish(publish_dir)
        if not passed:
            raise QualificationError(f"solar canary failed: {result.reason}")
        return self._artifact_root

    def _write_summary(
        self,
        results: list[ScenarioResult],
        *,
        evidence_kind: str,
    ) -> bool:
        """Write one honest summary and return whether every recorded scenario passed."""

        passed = all(result.outcome == "ready" for result in results)
        summary = {
            "schema_version": 1,
            "evidence_kind": evidence_kind,
            "recorded_at": utc_timestamp(),
            "outcome": "passed" if passed else "failed",
            "scenario_count": len(results),
            "scenarios": [result.__dict__ for result in results],
            "browser_observation": "pending screenshot capture",
        }
        summary_path = self._artifact_root / "summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return passed

    async def _reset(self, client: httpx.AsyncClient) -> None:
        payload = await self._json(client, "POST", "/api/session/reset", expected=200)
        session = payload.get("session")
        if not isinstance(session, dict) or session.get("state") != "empty":
            raise QualificationError("product reset did not produce an empty session")

    async def _execute(
        self,
        client: httpx.AsyncClient,
        *,
        name: str,
        prompt: str,
        endpoint: str,
        expected_kind: str,
    ) -> ScenarioResult:
        started = await self._json(
            client,
            "POST",
            endpoint,
            expected=202,
            json_body={"prompt": prompt},
        )
        run_id = started.get("run_id")
        if not isinstance(run_id, str) or started.get("kind") != expected_kind:
            raise QualificationError(f"{name} returned an invalid accepted run")
        return await self._collect(
            client,
            name=name,
            prompt=prompt,
            run_id=run_id,
            expected_kind=expected_kind,
        )

    async def _collect(
        self,
        client: httpx.AsyncClient,
        *,
        name: str,
        prompt: str,
        run_id: str,
        expected_kind: str,
    ) -> ScenarioResult:
        tape_path = self._artifact_root / f"{name}.events.jsonl"
        await self._retain_product_stream(client, run_id, tape_path)
        graph = await self._json(client, "GET", f"/api/runs/{run_id}", expected=200)
        readiness = classify_graph(graph)
        session_payload = await self._json(client, "GET", "/api/session", expected=200)
        session = session_payload.get("session")
        if not isinstance(session, dict):
            raise QualificationError(f"{name} returned an invalid product session")
        sketch_sha256: str | None = None
        outcome = "failed"
        reason = readiness.reason
        if readiness.ready and session.get("state") == "ready":
            code = await self._json(client, "GET", "/api/code", expected=200)
            candidate_sha256 = code.get("sha256")
            if (
                code.get("verified") is True
                and code.get("verified_run_id") == run_id
                and isinstance(candidate_sha256, str)
            ):
                outcome = "ready"
                reason = "ready"
                sketch_sha256 = candidate_sha256
            else:
                reason = "verified_revision_mismatch"
        graph_path = self._artifact_root / f"{name}.graph.json"
        graph_path.write_text(
            json.dumps(graph, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return ScenarioResult(
            name=name,
            prompt=prompt,
            run_id=run_id,
            kind=expected_kind,
            outcome=outcome,
            reason=reason,
            sketch_sha256=sketch_sha256,
            model_routes=model_routes(graph),
            raw_graph_name=graph_path.name,
            raw_graph_sha256=sha256_file(graph_path),
            event_tape_name=tape_path.name,
            event_tape_sha256=sha256_file(tape_path),
        )

    async def _retain_product_stream(
        self, client: httpx.AsyncClient, run_id: str, destination: Path
    ) -> None:
        terminal = False
        with destination.open("x", encoding="utf-8") as output:
            async with client.stream(
                "GET", f"{self._base_url}/api/runs/{run_id}/events"
            ) as response:
                if response.status_code != 200:
                    raise QualificationError(
                        f"product event stream failed with HTTP {response.status_code}"
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
                        if isinstance(data, dict) and data.get("type") == "transport_error":
                            raise QualificationError("product reported an upstream stream failure")
                        terminal = terminal or (
                            isinstance(data, dict) and data.get("type") == "RUN_FINISHED"
                        )
                    frame = []
        if not terminal:
            raise QualificationError("product event stream ended without RUN_FINISHED")

    async def _json(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        expected: int,
        json_body: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await client.request(
                method, f"{self._base_url}{path}", json=json_body
            )
        except httpx.HTTPError as exc:
            raise QualificationError(f"product request failed: {method} {path}") from exc
        if response.status_code != expected:
            raise QualificationError(
                f"product request {method} {path} returned HTTP {response.status_code}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise QualificationError(f"product request {method} {path} returned invalid JSON")
        return payload

    def _publish(self, destination: Path) -> None:
        replacements = {
            str(self._workspace): "<PTF_WORKSPACE>",
            str(self._artifact_dir): "<PTF_ARTIFACT_DIR>",
            str(Path.home()): "<HOME>",
        }
        destination.mkdir(parents=True, exist_ok=True)
        for source in sorted(self._artifact_root.iterdir()):
            if source.suffix in {".json", ".jsonl"}:
                publish_json_artifact(
                    source,
                    destination / source.name,
                    replacements=replacements,
                    forbidden_values=(self._control_token,),
                )
        refresh_published_artifact_hashes(destination / "summary.json", destination)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def recover_failed_qualification(
    *,
    artifact_root: Path,
    artifact_dir: Path,
    workspace: Path,
    control_token: str,
    publish_dir: Path | None,
) -> Path:
    """Finalize five failed create graphs and one refused linked follow-up honestly."""

    resolved_root = artifact_root.resolve(strict=True)
    allowed_root = (artifact_dir / "live-qualification").resolve(strict=True)
    if not resolved_root.is_relative_to(allowed_root):
        raise QualificationError("recovery directory is outside PTF_ARTIFACT_DIR")
    expected = [
        ("suggestion-1-rain-that-avoids-my-mouse", SUGGESTED_PROMPTS[0]),
        ("suggestion-2-bouncy-magnets", SUGGESTED_PROMPTS[1]),
        ("suggestion-3-angry-solar-system", SUGGESTED_PROMPTS[2]),
        ("suggestion-4-fish-that-follow-my-cursor", SUGGESTED_PROMPTS[3]),
        ("demo-create-tiny-solar-system", SOLAR_PROMPT),
    ]
    results: list[ScenarioResult] = []
    for name, prompt in expected:
        graph_path = resolved_root / f"{name}.graph.json"
        tape_path = resolved_root / f"{name}.events.jsonl"
        if not graph_path.is_file() or not tape_path.is_file():
            raise QualificationError(f"recovery evidence is incomplete for {name}")
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        if not isinstance(graph, dict) or not isinstance(graph.get("run_id"), str):
            raise QualificationError(f"recovery graph is invalid for {name}")
        readiness = classify_graph(graph)
        if readiness.ready:
            raise QualificationError(
                f"{name} is graph-ready; its discarded product revision cannot be reconstructed"
            )
        results.append(
            ScenarioResult(
                name=name,
                prompt=prompt,
                run_id=graph["run_id"],
                kind="create",
                outcome="failed",
                reason=readiness.reason,
                sketch_sha256=None,
                model_routes=model_routes(graph),
                raw_graph_name=graph_path.name,
                raw_graph_sha256=sha256_file(graph_path),
                event_tape_name=tape_path.name,
                event_tape_sha256=sha256_file(tape_path),
            )
        )
    results.append(
        ScenarioResult(
            name="demo-follow-up-glowing-trails",
            prompt=TRAIL_PROMPT,
            run_id=None,
            kind="follow_up",
            outcome="not_started",
            reason="solar_create_not_ready; product returned HTTP 409 and started no S17 run",
            sketch_sha256=None,
            model_routes=[],
            raw_graph_name=None,
            raw_graph_sha256=None,
            event_tape_name=None,
            event_tape_sha256=None,
        )
    )
    summary = {
        "schema_version": 1,
        "evidence_kind": "live_product_qualification",
        "recorded_at": utc_timestamp(),
        "outcome": "failed",
        "scenario_count": len(results),
        "scenarios": [result.__dict__ for result in results],
        "browser_observation": {
            "outcome": "not_run",
            "reason": "no verified linked preview existed",
        },
        "unresolved_limitations": [
            "All five live create graphs finished without answer_with_evidence.",
            "The linked follow-up was correctly refused because solar creation was not ready.",
        ],
    }
    summary_path = resolved_root / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if publish_dir is not None:
        replacements = {
            str(workspace): "<PTF_WORKSPACE>",
            str(artifact_dir): "<PTF_ARTIFACT_DIR>",
            str(Path.home()): "<HOME>",
        }
        publish_dir.mkdir(parents=True, exist_ok=True)
        for source in sorted(resolved_root.iterdir()):
            if source.suffix in {".json", ".jsonl"}:
                publish_json_artifact(
                    source,
                    publish_dir / source.name,
                    replacements=replacements,
                    forbidden_values=(control_token,),
                )
        refresh_published_artifact_hashes(publish_dir / "summary.json", publish_dir)
    return resolved_root


async def run(args: argparse.Namespace) -> int:
    """Execute the bounded live suite and print its ignored artifact directory."""

    if args.timeout_seconds <= 0:
        raise QualificationError("timeout must be positive")
    settings = load_settings(env_file=args.env_file)
    if args.recover_dir is not None:
        artifact_dir = recover_failed_qualification(
            artifact_root=args.recover_dir,
            artifact_dir=settings.artifact_dir,
            workspace=settings.workspace,
            control_token=settings.s17_control_token.get_secret_value(),
            publish_dir=args.publish_dir,
        )
        print(f"live_qualification=failed evidence_finalized={artifact_dir}")
        return 0
    qualifier = LiveProductQualifier(
        product_base_url=args.product_base_url,
        artifact_dir=settings.artifact_dir,
        workspace=settings.workspace,
        control_token=settings.s17_control_token.get_secret_value(),
    )
    async with asyncio.timeout(args.timeout_seconds):
        if args.solar_canary_only:
            artifact_dir = await qualifier.run_solar_canary(publish_dir=args.publish_dir)
        else:
            artifact_dir = await qualifier.run(publish_dir=args.publish_dir)
    outcome = "solar_canary" if args.solar_canary_only else "live_qualification"
    print(f"{outcome}=passed artifact_dir={artifact_dir}")
    return 0


def main() -> None:
    args = parse_args()
    try:
        raise SystemExit(asyncio.run(run(args)))
    except (QualificationError, TimeoutError) as exc:
        raise SystemExit(f"live_qualification=failed reason={exc}") from exc


if __name__ == "__main__":
    main()
