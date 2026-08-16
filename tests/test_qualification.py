"""Deterministic Phase 6 proof analysis and publication tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from dotenv import load_dotenv

from physics_toy_factory.qualification import (
    REPAIR_AUTHORITY,
    REPAIR_GOAL,
    QualificationError,
    RepairProofRunner,
    analyze_repair_graph,
    model_routes,
    publish_json_artifact,
    refresh_published_artifact_hashes,
    sanitize_for_publication,
)
from scripts.qualify_live_product import LiveProductQualifier, ScenarioResult


def repair_graph(*, latest_exit: int = 0, anchored: bool = True) -> dict:
    """Build a minimal raw S17 graph whose ordering comes only from its journal."""

    return {
        "run_id": "run-live-proof",
        "finished": True,
        "nodes": {
            "read": {
                "skill": "read_code",
                "state": "succeeded",
                "input": {"path": "sketch.js"},
                "result": {"path": "sketch.js"},
            },
            "red": {
                "skill": "run_command",
                "state": "succeeded",
                "input": {"command": "node p5check.js sketch.js"},
                "result": {"exit_code": 1, "timed_out": False},
            },
            "edit": {
                "skill": "edit_code",
                "state": "succeeded",
                "input": {
                    "path": "sketch.js",
                    "old_string": "blendMdoe(ADD);",
                    "new_string": "blendMode(ADD);",
                },
                "result": {
                    "replaced": 1 if anchored else 0,
                    "occurrences_found": 1 if anchored else 2,
                },
            },
            "green": {
                "skill": "run_command",
                "state": "succeeded",
                "input": {"command": "node ./p5check.js ./sketch.js"},
                "result": {"exit_code": latest_exit, "timed_out": False},
            },
        },
        "edges": [],
        "events": [
            {"sequence": 1, "kind": "run_started", "node_id": None, "payload": {}},
            {"sequence": 2, "kind": "task_succeeded", "node_id": "read", "payload": {}},
            {"sequence": 3, "kind": "task_succeeded", "node_id": "red", "payload": {}},
            {"sequence": 4, "kind": "task_succeeded", "node_id": "edit", "payload": {}},
            {"sequence": 5, "kind": "task_succeeded", "node_id": "green", "payload": {}},
        ],
    }


def test_analyze_repair_graph_requires_journal_ordered_red_edit_latest_green() -> None:
    chain = analyze_repair_graph(repair_graph())

    assert chain.red_checker_node_id == "red"
    assert chain.red_exit_code == 1
    assert chain.edit_node_id == "edit"
    assert chain.green_checker_node_id == "green"
    assert (chain.red_sequence, chain.edit_sequence, chain.green_sequence) == (3, 4, 5)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda graph: graph.update(finished=False), "did not finish"),
        (lambda graph: graph["nodes"]["green"]["result"].update(exit_code=1), "latest checker"),
        (
            lambda graph: graph["nodes"]["edit"]["result"].update(
                replaced=0, occurrences_found=2
            ),
            "no red, anchored-edit",
        ),
        (
            lambda graph: graph["events"][3].update(sequence=2),
            "no red, anchored-edit",
        ),
    ],
)
def test_analyze_repair_graph_fails_without_required_chain(mutation, message: str) -> None:
    graph = repair_graph()
    mutation(graph)

    with pytest.raises(QualificationError, match=message):
        analyze_repair_graph(graph)


def test_sanitize_replaces_reviewed_paths_and_rejects_secrets_or_unknown_paths() -> None:
    value = {"command": "docker -v /safe/workspace:/workspace", "nested": ["ok"]}

    sanitized = sanitize_for_publication(
        value,
        replacements={"/safe/workspace": "<PTF_WORKSPACE>"},
        forbidden_values=("private-token",),
    )

    assert sanitized["command"] == "docker -v <PTF_WORKSPACE>:/workspace"
    with pytest.raises(QualificationError, match="secret"):
        sanitize_for_publication(
            {"authorization": "Bearer private-token"},
            replacements={},
            forbidden_values=("private-token",),
        )
    with pytest.raises(QualificationError, match="machine path"):
        sanitize_for_publication(
            {"path": "/Users/example/unreviewed/file"},
            replacements={},
        )


def test_publish_jsonl_is_sanitized_and_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "events.jsonl"
    source.write_text(
        '{"path":"/safe/workspace/sketch.js","seq":2}\n'
        '{"seq":1,"message":"ready"}\n',
        encoding="utf-8",
    )
    destination = tmp_path / "published" / "events.jsonl"

    publish_json_artifact(
        source,
        destination,
        replacements={"/safe/workspace": "<PTF_WORKSPACE>"},
        forbidden_values=("private-token",),
    )

    assert destination.read_text(encoding="utf-8") == (
        '{"path": "<PTF_WORKSPACE>/sketch.js", "seq": 2}\n'
        '{"message": "ready", "seq": 1}\n'
    )


def test_model_routes_include_planner_metering_and_node_results() -> None:
    graph = repair_graph()
    graph["events"][0]["payload"] = {
        "metered_calls": [{"provider": "openrouter", "model": "google/model"}]
    }
    graph["nodes"]["read"]["result"].update(
        provider="gemini_2", model="gemini-model"
    )

    assert model_routes(graph) == [
        {"provider": "gemini_2", "model": "gemini-model"},
        {"provider": "openrouter", "model": "google/model"},
    ]


def test_refresh_published_artifact_hashes_uses_selected_bytes(tmp_path: Path) -> None:
    graph = tmp_path / "graph.json"
    tape = tmp_path / "events.jsonl"
    graph.write_text('{"path":"<PTF_WORKSPACE>/sketch.js"}\n', encoding="utf-8")
    tape.write_text('{"event":"ready"}\n', encoding="utf-8")
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "scenarios": [
                    {
                        "raw_graph_name": graph.name,
                        "raw_graph_sha256": "source-graph-hash",
                        "event_tape_name": tape.name,
                        "event_tape_sha256": "source-tape-hash",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    refresh_published_artifact_hashes(summary, tmp_path)

    refreshed = json.loads(summary.read_text(encoding="utf-8"))
    scenario = refreshed["scenarios"][0]
    assert scenario["raw_graph_sha256"] != "source-graph-hash"
    assert scenario["event_tape_sha256"] != "source-tape-hash"


@pytest.mark.asyncio
async def test_repair_start_uses_exact_goal_authority_and_budget(settings) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer phase-two-private-control-token"
        return httpx.Response(202, json={"run_id": "run-proof-1", "status": "accepted"})

    runner = RepairProofRunner(
        settings,
        product_base_url="http://product.test",
        timeout_seconds=10,
        budget_usd=0.25,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        run_id = await runner._start(client)

    assert run_id == "run-proof-1"
    assert captured["prompt"] == REPAIR_GOAL
    assert "before any edit" in captured["prompt"]
    assert captured["allowed_side_effects"] == REPAIR_AUTHORITY
    assert "create_file" not in captured["allowed_side_effects"]
    assert captured["budget"] == 0.25


def test_phase6_container_recipe_is_pinned_and_non_root() -> None:
    dockerfile = (
        Path(__file__).parents[1] / "containers" / "phase6-node.Dockerfile"
    ).read_text(encoding="utf-8")

    assert "FROM node:22.20.0-alpine" in dockerfile
    assert "FROM node:latest" not in dockerfile
    assert "USER node" in dockerfile


def test_phase6_runbook_blocks_s17_dotenv_root_repopulation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runbook = (Path(__file__).parents[1] / "docs" / "PHASE6_RUNBOOK.md").read_text(
        encoding="utf-8"
    )

    assert "export S17_SANDBOX_ROOT=" in runbook
    assert "export S17_SKILLS_DIR=" in runbook
    assert "unset S17_SANDBOX_ROOT" not in runbook
    assert "unset S17_SKILLS_DIR" not in runbook

    s17_env = tmp_path / ".env"
    s17_env.write_text(
        "S17_SANDBOX_ROOT=/generic/sandbox\nS17_SKILLS_DIR=/generic/skills\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("S17_SANDBOX_ROOT", "")
    monkeypatch.setenv("S17_SKILLS_DIR", "")

    load_dotenv(s17_env)

    assert os.environ["S17_SANDBOX_ROOT"] == ""
    assert os.environ["S17_SKILLS_DIR"] == ""


@pytest.mark.asyncio
async def test_solar_canary_mode_starts_exactly_one_creation(tmp_path: Path, monkeypatch) -> None:
    qualifier = LiveProductQualifier(
        product_base_url="http://product.test",
        artifact_dir=tmp_path / "artifacts",
        workspace=tmp_path / "workspace",
        control_token="private-token",
    )
    reset = AsyncMock()
    execute = AsyncMock(
        return_value=ScenarioResult(
            name="canary-create-tiny-solar-system",
            prompt="Create a tiny solar system.",
            run_id="run-canary",
            kind="create",
            outcome="ready",
            reason="ready",
            sketch_sha256="a" * 64,
            model_routes=[{"provider": "openrouter", "model": "frontier-model"}],
            raw_graph_name="canary.graph.json",
            raw_graph_sha256="b" * 64,
            event_tape_name="canary.events.jsonl",
            event_tape_sha256="c" * 64,
        )
    )
    monkeypatch.setattr(qualifier, "_reset", reset)
    monkeypatch.setattr(qualifier, "_execute", execute)

    artifact_dir = await qualifier.run_solar_canary(publish_dir=None)

    reset.assert_awaited_once()
    execute.assert_awaited_once()
    assert execute.await_args.kwargs == {
        "name": "canary-create-tiny-solar-system",
        "prompt": "Create a tiny solar system.",
        "endpoint": "/api/runs",
        "expected_kind": "create",
    }
    summary = json.loads((artifact_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["evidence_kind"] == "live_product_canary"
    assert summary["scenario_count"] == 1


@pytest.mark.asyncio
async def test_live_qualifier_stream_uses_configured_product_origin(tmp_path: Path) -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'id: 1\ndata: {"type":"RUN_STARTED","seq":1}\n\n'
                'data: {"type":"RUN_FINISHED","seq":2}\n\n'
            ),
        )

    qualifier = LiveProductQualifier(
        product_base_url="http://product.test",
        artifact_dir=tmp_path / "artifacts",
        workspace=tmp_path / "workspace",
        control_token="private-token",
    )
    destination = tmp_path / "events.jsonl"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await qualifier._retain_product_stream(client, "run-existing", destination)

    assert seen_urls == ["http://product.test/api/runs/run-existing/events"]
    assert len(destination.read_text(encoding="utf-8").splitlines()) == 2
