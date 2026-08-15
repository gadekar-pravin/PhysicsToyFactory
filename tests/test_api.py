"""Phase 2 browser-facing API and fake-S17 integration contracts."""

from __future__ import annotations

import asyncio
import hashlib
import re

import httpx
import pytest

from physics_toy_factory.main import create_app
from physics_toy_factory.workspace import WorkspaceManager
from tests.conftest import CONTROL_TOKEN
from tests.fake_s17 import FakeS17, sse_for_graph, terminal_graph

SKETCH = "function setup(){createCanvas(120,80)}\nfunction draw(){background(20);circle(50,40,20)}\n"


async def _create(product, prompt: str = "Create a small orbit") -> str:
    response = await product.client.post("/api/runs", json={"prompt": prompt})
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


async def _make_ready(product, *, sketch: str = SKETCH) -> str:
    run_id = await _create(product)
    product.settings.workspace.joinpath("sketch.js").write_text(sketch, encoding="utf-8")
    graph = terminal_graph(run_id)
    product.fake.complete(run_id, graph)
    response = await product.client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200
    return run_id


@pytest.mark.asyncio
async def test_health_distinguishes_process_gateway_workspace_and_container(product) -> None:
    response = await product.client.get("/api/health")
    body = response.json()
    assert response.status_code == 200
    assert body["ready"] is True
    assert body["workspace"] == {"verified": True, "trusted_asset_count": 6}
    assert body["s17"]["process"]["up"] is True
    assert body["s17"]["gateway"]["ready"] is True
    assert body["container_mode"]["configured"] is False
    assert "Development only" in body["container_mode"]["warning"]
    assert CONTROL_TOKEN not in response.text


@pytest.mark.asyncio
async def test_main_ui_and_static_assets_are_served_without_preview_execution(product) -> None:
    page = await product.client.get("/")
    script = await product.client.get("/static/app.js")
    styles = await product.client.get("/static/styles.css")
    assert page.status_code == script.status_code == styles.status_code == 200
    assert "Physics Toy Factory" in page.text
    assert "Factory activity" in page.text
    assert "<iframe" not in page.text
    assert "EventSource" in script.text
    assert "innerHTML" not in script.text
    assert CONTROL_TOKEN not in page.text + script.text + styles.text


@pytest.mark.asyncio
async def test_health_is_degraded_when_s17_is_up_but_gateway_is_not_ready(product) -> None:
    product.fake.ready_status = 503
    body = (await product.client.get("/api/health")).json()
    assert body["status"] == "degraded"
    assert body["ready"] is False
    assert body["s17"]["process"]["up"] is True
    assert body["s17"]["gateway"]["ready"] is False


@pytest.mark.asyncio
async def test_create_returns_id_before_fake_execution_completes_with_exact_authority(product) -> None:
    run_id = await _create(product, "  Rain around my mouse  ")
    assert product.fake.runs[run_id]["finished"] is False
    body = product.fake.starts[0]
    assert body["allowed_side_effects"] == ["create_file", "edit_code", "run_command"]
    assert body["tenant_id"] == "physics-toy-factory"
    assert body["project_id"] == "demo"
    assert body["user_id"] == "local-audience"
    assert body["agent_id"] == "p5-builder"
    assert "Rain around my mouse" in body["prompt"]
    session = (await product.client.get("/api/session")).json()["session"]
    assert session["state"] == "running"
    assert session["active_run_id"] == run_id
    assert session["runs"][0]["outcome"] == "running"


@pytest.mark.asyncio
async def test_concurrent_create_and_reset_are_rejected_while_run_active(product) -> None:
    responses = await asyncio.gather(
        product.client.post("/api/runs", json={"prompt": "one"}),
        product.client.post("/api/runs", json={"prompt": "two"}),
    )
    assert sorted(response.status_code for response in responses) == [202, 409]
    sentinel = product.settings.artifact_dir / "journal-sentinel.jsonl"
    sentinel.write_text("retain", encoding="utf-8")
    reset = await product.client.post("/api/session/reset")
    assert reset.status_code == 409
    assert reset.json()["error"]["code"] == "run_active"
    assert sentinel.read_text(encoding="utf-8") == "retain"


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", ["", "   ", "x" * 4001])
async def test_prompt_bounds_fail_with_stable_errors(product, prompt: str) -> None:
    response = await product.client.post("/api/runs", json={"prompt": prompt})
    assert response.status_code == 422
    assert response.json()["error"]["code"] in {"prompt_empty", "prompt_too_long"}
    assert not product.fake.starts


@pytest.mark.asyncio
async def test_request_validation_does_not_echo_untrusted_values(product) -> None:
    attack = "<script>secret-value</script>"
    response = await product.client.post(
        "/api/runs", json={"prompt": "ok", "unexpected": attack}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert attack not in response.text


@pytest.mark.asyncio
async def test_start_rejection_creates_no_product_run_link(product) -> None:
    product.fake.start_status = 503
    response = await product.client.post("/api/runs", json={"prompt": "try"})
    assert response.status_code == 503
    assert "secret upstream body" not in response.text
    session = (await product.client.get("/api/session")).json()["session"]
    assert session["state"] == "empty"
    assert session["runs"] == []


@pytest.mark.asyncio
async def test_checker_exit_one_is_failed_even_when_node_succeeded(product) -> None:
    run_id = await _create(product)
    product.settings.workspace.joinpath("sketch.js").write_text(SKETCH, encoding="utf-8")
    graph = terminal_graph(
        run_id, checker_results=(("node p5check.js sketch.js", 1, False),)
    )
    product.fake.complete(run_id, graph)
    await product.client.get(f"/api/runs/{run_id}")
    session = (await product.client.get("/api/session")).json()["session"]
    assert session["state"] == "failed"
    assert session["runs"][0]["outcome"] == "failed"
    code = (await product.client.get("/api/code")).json()
    assert code["verified"] is False


@pytest.mark.asyncio
async def test_terminal_pass_links_exact_sketch_hash_and_code(product) -> None:
    run_id = await _make_ready(product)
    expected = hashlib.sha256(SKETCH.encode()).hexdigest()
    session = (await product.client.get("/api/session")).json()["session"]
    assert session["state"] == "ready"
    assert session["active_run_id"] is None
    assert session["current_sketch_sha256"] == expected
    assert session["runs"][0]["verified_sketch_sha256"] == expected
    code = (await product.client.get("/api/code")).json()
    assert code == {
        "path": "sketch.js",
        "content": SKETCH,
        "bytes": len(SKETCH.encode()),
        "sha256": expected,
        "verified": True,
        "verified_run_id": run_id,
    }


@pytest.mark.asyncio
async def test_changed_sketch_after_pass_is_never_reported_verified(product) -> None:
    await _make_ready(product)
    product.settings.workspace.joinpath("sketch.js").write_text(SKETCH + "// changed\n", encoding="utf-8")
    code = (await product.client.get("/api/code")).json()
    assert code["verified"] is False
    assert code["verified_run_id"] is None


@pytest.mark.asyncio
async def test_preview_is_blocked_until_exact_revision_is_verified(product) -> None:
    revision = hashlib.sha256(SKETCH.encode()).hexdigest()
    before = await product.client.post("/api/preview", json={"revision": revision})
    assert before.status_code == 409
    assert before.json()["error"]["code"] == "preview_not_ready"

    await _make_ready(product)
    mismatch = await product.client.post("/api/preview", json={"revision": "0" * 64})
    malformed = await product.client.post("/api/preview", json={"revision": "not-a-hash"})
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "preview_not_ready"
    assert malformed.status_code == 422


@pytest.mark.asyncio
async def test_preview_shell_and_scripts_are_nonce_bound_local_and_uncached(product) -> None:
    run_id = await _make_ready(product)
    revision = hashlib.sha256(SKETCH.encode()).hexdigest()
    lease_response = await product.client.post("/api/preview", json={"revision": revision})
    lease = lease_response.json()
    assert lease_response.status_code == 200
    assert lease["run_id"] == run_id
    assert lease["revision"] == revision
    assert lease["ready_timeout_ms"] == 8000
    assert re.fullmatch(r"[A-Za-z0-9_-]{32,128}", lease["preview_id"])

    shell = await product.client.get(lease["url"])
    assert shell.status_code == 200
    assert shell.headers["cache-control"] == "no-store"
    assert shell.headers["referrer-policy"] == "no-referrer"
    assert shell.headers["x-content-type-options"] == "nosniff"
    csp = shell.headers["content-security-policy"]
    nonce_match = re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)'", csp)
    assert nonce_match is not None
    assert f'nonce="{nonce_match.group(1)}"' in shell.text
    assert "connect-src 'none'" in csp
    assert "frame-ancestors 'self'" in csp
    assert "navigate-to 'none'" in csp
    assert "script-src 'unsafe-inline'" not in csp
    assert "https://" not in shell.text
    assert "http://" not in shell.text
    assert "__PTF_" not in shell.text
    assert lease["preview_id"] in shell.text

    repeated_shell = await product.client.get(lease["url"])
    assert repeated_shell.status_code == 409
    assert repeated_shell.json()["error"]["code"] == "preview_not_ready"

    params = {"revision": revision, "preview_id": lease["preview_id"]}
    p5 = await product.client.get("/api/preview/p5.min.js", params=params)
    sketch = await product.client.get("/api/preview/sketch.js", params=params)
    assert p5.status_code == sketch.status_code == 200
    assert len(p5.content) == 989034
    assert sketch.content == SKETCH.encode()
    for response in (p5, sketch):
        assert response.headers["content-type"].startswith("application/javascript")
        assert response.headers["access-control-allow-origin"] == "*"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_preview_assets_fail_closed_after_sketch_changes_or_id_is_stale(product) -> None:
    await _make_ready(product)
    revision = hashlib.sha256(SKETCH.encode()).hexdigest()
    first = (await product.client.post("/api/preview", json={"revision": revision})).json()
    second = (await product.client.post("/api/preview", json={"revision": revision})).json()

    stale = await product.client.get(first["url"])
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "preview_not_ready"

    product.settings.workspace.joinpath("sketch.js").write_text(
        SKETCH + "// changed after pass\n", encoding="utf-8"
    )
    changed_shell = await product.client.get(second["url"])
    changed_script = await product.client.get(
        "/api/preview/sketch.js",
        params={"revision": revision, "preview_id": second["preview_id"]},
    )
    assert changed_shell.status_code == changed_script.status_code == 409
    assert changed_shell.json()["error"]["code"] == "preview_not_ready"
    assert changed_script.json()["error"]["code"] == "preview_not_ready"


@pytest.mark.asyncio
async def test_browser_error_requires_bound_run_and_preview_and_starts_no_repair(product) -> None:
    run_id = await _make_ready(product)
    revision = hashlib.sha256(SKETCH.encode()).hexdigest()
    lease = (await product.client.post("/api/preview", json={"revision": revision})).json()
    assert (await product.client.get(lease["url"])).status_code == 200
    error = {
        "preview_id": lease["preview_id"],
        "name": "TypeError",
        "message": "draw failed safely",
        "line": 42,
        "column": 7,
    }
    start_count = len(product.fake.starts)

    stranger = await product.client.post("/api/runs/run-stranger/browser-error", json=error)
    wrong_id = await product.client.post(
        f"/api/runs/{run_id}/browser-error",
        json={**error, "preview_id": "x" * 43},
    )
    assert stranger.status_code == 404
    assert wrong_id.status_code == 409

    response = await product.client.post(f"/api/runs/{run_id}/browser-error", json=error)
    assert response.status_code == 200
    session = response.json()["session"]
    assert session["state"] == "failed"
    assert session["browser_error"] == error
    assert len(product.fake.starts) == start_count

    repeated = await product.client.post(f"/api/runs/{run_id}/browser-error", json=error)
    assert repeated.status_code == 409
    assert len(product.fake.starts) == start_count


@pytest.mark.asyncio
async def test_browser_error_payload_is_bounded_and_never_echoed(product) -> None:
    run_id = await _make_ready(product)
    revision = hashlib.sha256(SKETCH.encode()).hexdigest()
    lease = (await product.client.post("/api/preview", json={"revision": revision})).json()
    assert (await product.client.get(lease["url"])).status_code == 200
    attack = "<img id=error-attack>" * 100
    response = await product.client.post(
        f"/api/runs/{run_id}/browser-error",
        json={
            "preview_id": lease["preview_id"],
            "name": "TypeError",
            "message": attack,
            "line": 1,
            "column": 1,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert attack not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("asset", ["shell/index.html", "shell/p5.min.js"])
async def test_tampered_preview_shell_or_runtime_blocks_preview(product, asset: str) -> None:
    await _make_ready(product)
    revision = hashlib.sha256(SKETCH.encode()).hexdigest()
    product.settings.workspace.joinpath(asset).write_text("tampered", encoding="utf-8")
    response = await product.client.post("/api/preview", json={"revision": revision})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "workspace_invalid"


@pytest.mark.asyncio
async def test_follow_up_requires_ready_hash_and_uses_narrow_authority_once(product) -> None:
    early = await product.client.post("/api/runs/follow-up", json={"prompt": "trails"})
    assert early.status_code == 409
    create_id = await _make_ready(product)
    response = await product.client.post("/api/runs/follow-up", json={"prompt": "Add trails"})
    assert response.status_code == 202
    follow_id = response.json()["run_id"]
    assert product.fake.starts[-1]["allowed_side_effects"] == ["edit_code", "run_command"]
    assert "Read sketch.js with read_code" in product.fake.starts[-1]["prompt"]
    session = (await product.client.get("/api/session")).json()["session"]
    assert session["state"] == "modifying"
    assert session["follow_up_used"] is True
    assert session["runs"][-1]["parent_run_id"] == create_id

    product.fake.complete(follow_id, terminal_graph(follow_id))
    await product.client.get(f"/api/runs/{follow_id}")
    second = await product.client.post("/api/runs/follow-up", json={"prompt": "again"})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "follow_up_used"


@pytest.mark.asyncio
async def test_follow_up_rejects_changed_verified_file_without_starting_s17(product) -> None:
    await _make_ready(product)
    product.settings.workspace.joinpath("sketch.js").write_text(SKETCH + "// mutation", encoding="utf-8")
    before = len(product.fake.starts)
    response = await product.client.post("/api/runs/follow-up", json={"prompt": "change"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "sketch_changed"
    assert len(product.fake.starts) == before


@pytest.mark.asyncio
async def test_only_session_owned_ids_can_be_read_or_streamed(product) -> None:
    raw = await product.client.get("/api/runs/run-stranger")
    events = await product.client.get("/api/runs/run-stranger/events")
    assert raw.status_code == 404
    assert events.status_code == 404
    assert product.fake.event_queries == []


@pytest.mark.asyncio
async def test_event_proxy_forwards_reconnect_cursor_and_preserves_frames(product) -> None:
    run_id = await _create(product)
    graph = terminal_graph(run_id)
    product.fake.complete(run_id, graph)
    product.fake.streams[run_id] = sse_for_graph(graph)
    response = await product.client.get(
        f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "7"}
    )
    assert response.status_code == 200
    assert product.fake.event_queries[-1] == {"after": "7", "reconnect": "1"}
    assert "id: 1" in response.text
    assert ": keepalive" in response.text
    assert "RUN_FINISHED" in response.text
    assert CONTROL_TOKEN not in response.text


@pytest.mark.asyncio
async def test_event_proxy_rejects_invalid_cursor_before_upstream(product) -> None:
    run_id = await _create(product)
    response = await product.client.get(
        f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "-1"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_event_cursor"
    assert product.fake.event_queries == []


@pytest.mark.asyncio
async def test_premature_sse_end_is_labeled_transport_not_agent_failure(product) -> None:
    run_id = await _create(product)
    response = await product.client.get(f"/api/runs/{run_id}/events")
    assert response.status_code == 200
    assert "event: transport_error" in response.text
    assert "agent failure" not in response.text.lower()


@pytest.mark.asyncio
async def test_upstream_raw_404_retains_link_and_exposes_degraded_state(product) -> None:
    run_id = await _create(product)
    product.fake.raw_status = 404
    response = await product.client.get(f"/api/runs/{run_id}")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "s17_run_inconsistent"
    session = (await product.client.get("/api/session")).json()
    assert session["degraded"]["code"] == "s17_run_inconsistent"
    assert session["session"]["runs"][0]["run_id"] == run_id


@pytest.mark.asyncio
async def test_raw_graph_cannot_reflect_control_token_to_browser(product) -> None:
    run_id = await _create(product)
    graph = terminal_graph(run_id)
    graph["nodes"]["answer"]["result"]["answer"] = CONTROL_TOKEN
    product.fake.complete(run_id, graph)
    response = await product.client.get(f"/api/runs/{run_id}")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "s17_invalid_response"
    assert CONTROL_TOKEN not in response.text


@pytest.mark.asyncio
async def test_reset_restores_fixture_new_session_and_preserves_artifacts(product) -> None:
    run_id = await _create(product)
    product.settings.workspace.joinpath("sketch.js").write_text(SKETCH, encoding="utf-8")
    product.fake.complete(
        run_id, terminal_graph(run_id, checker_results=(("node p5check.js sketch.js", 1, False),))
    )
    await product.client.get(f"/api/runs/{run_id}")
    old_session = (await product.client.get("/api/session")).json()["session"]["session_id"]
    sentinel = product.settings.artifact_dir / "journal.jsonl"
    sentinel.write_text("durable", encoding="utf-8")
    response = await product.client.post("/api/session/reset")
    assert response.status_code == 200
    session = response.json()["session"]
    assert session["state"] == "empty"
    assert session["session_id"] != old_session
    assert session["runs"] == []
    assert not product.settings.workspace.joinpath("sketch.js").exists()
    assert sentinel.read_text(encoding="utf-8") == "durable"


@pytest.mark.asyncio
async def test_tampered_trusted_asset_blocks_start(product) -> None:
    product.settings.workspace.joinpath("p5check.js").write_text("tampered", encoding="utf-8")
    response = await product.client.post("/api/runs", json={"prompt": "build"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "workspace_invalid"
    assert not product.fake.starts


@pytest.mark.asyncio
async def test_startup_with_existing_sketch_requires_explicit_reset(settings, fake_s17: FakeS17) -> None:
    manager = WorkspaceManager(settings)
    manager.ensure_initialized()
    settings.workspace.joinpath("sketch.js").write_text(SKETCH, encoding="utf-8")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_s17.app), base_url="http://s17.test"
    ) as upstream:
        app = create_app(settings, http_client=upstream)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://product.test"
            ) as client:
                session = (await client.get("/api/session")).json()["session"]
                create = await client.post("/api/runs", json={"prompt": "new"})
                reset = await client.post("/api/session/reset")
    assert session["state"] == "reset_required"
    assert create.status_code == 409
    assert reset.status_code == 200


@pytest.mark.asyncio
async def test_ambiguous_start_timeout_sets_reset_required_without_inventing_run(settings) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/agent/runs/async":
            raise httpx.ReadTimeout("private", request=request)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as upstream:
        app = create_app(settings, http_client=upstream)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://product.test"
            ) as client:
                response = await client.post("/api/runs", json={"prompt": "ambiguous"})
                session = (await client.get("/api/session")).json()["session"]
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "s17_start_ambiguous"
    assert session["state"] == "reset_required"
    assert session["runs"] == []
