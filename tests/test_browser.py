"""Phase 3-5 browser journeys against the product and deterministic fake S17."""

from __future__ import annotations

import asyncio
import copy
import json
import re
import socket
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import uvicorn
from playwright.async_api import Page, Route, async_playwright, expect

from physics_toy_factory.main import create_app
from tests.fake_s17 import FakeS17, follow_up_graph, sse_for_graph

SKETCH_WITH_UNTRUSTED_TEXT = """let ball;
function setup() { createCanvas(640, 420); ball = 20; }
function draw() { background(15); circle(ball, 210, 30); }
// <img id=\"code-attack\" src=x onerror=alert(1)>
"""

SECURITY_SKETCH = """let clicks = 0;
window.securityResults = {
  networkBlocked: null,
  storageBlocked: false,
  parentBlocked: false,
  navigationBlocked: false,
  popupBlocked: false,
};

function setup() {
  createCanvas(640, 420);
  try { localStorage.setItem("ptf", "escape"); }
  catch (_error) { window.securityResults.storageBlocked = true; }
  try { void parent.document.body; }
  catch (_error) { window.securityResults.parentBlocked = true; }
  try { top.location.href = "https://example.invalid/escape"; }
  catch (_error) { window.securityResults.navigationBlocked = true; }
  window.securityResults.popupBlocked = window.open("https://example.invalid/popup") === null;
  fetch("https://example.invalid/network")
    .then(() => { window.securityResults.networkBlocked = false; })
    .catch(() => { window.securityResults.networkBlocked = true; });
  window.triggerPreviewFailure = () => setTimeout(() => {
    throw new TypeError('<img id="preview-error-attack">');
  }, 0);
  window.triggerPreviewRejection = () => Promise.reject(
    new RangeError('<img id="preview-rejection-attack">')
  );
}

function draw() {
  background(10, 18, 30);
  fill(245, 151, 39);
  circle(320, 210, 70 + clicks * 12);
}

function mousePressed() { clicks += 1; }
"""

INITIAL_FOLLOW_UP_SKETCH = """let angle = 0;
function setup() { createCanvas(640, 420); }
function draw() {
  background(10, 18, 30);
  circle(320 + cos(angle) * 90, 210 + sin(angle) * 90, 34);
  angle += 0.02;
}
function mousePressed() { angle = 0; }
"""

MODIFIED_FOLLOW_UP_SKETCH = """let trailsEnabled = true;
let angle = 0;
function setup() { createCanvas(640, 420); }
function draw() {
  background(10, 18, 30, trailsEnabled ? 22 : 255);
  circle(320 + cos(angle) * 90, 210 + sin(angle) * 90, 34);
  angle += 0.02;
}
function mousePressed() { angle = 0; }
"""


@pytest.fixture
def recorded_graph() -> dict:
    path = Path(__file__).parent / "fixtures" / "s17_run_red_green.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest_asyncio.fixture
async def browser_page() -> AsyncIterator[Page]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1100})
        try:
            yield page
        finally:
            await browser.close()


@pytest_asyncio.fixture
async def live_product(settings, fake_s17: FakeS17) -> AsyncIterator[str]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_s17.app), base_url="http://s17.test"
    ) as upstream:
        app = create_app(settings, http_client=upstream)
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(("127.0.0.1", 0))
        server_socket.listen(128)
        port = server_socket.getsockname()[1]
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
            lifespan="on",
        )
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve(sockets=[server_socket]))
        for _attempt in range(200):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        else:
            server.should_exit = True
            await task
            pytest.fail("Product server did not start")
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=5)


def complete_recorded_run(
    fake_s17: FakeS17,
    settings,
    graph_template: dict,
    *,
    configure_stream=None,
    sketch: str = SKETCH_WITH_UNTRUSTED_TEXT,
) -> None:
    def finish(run_id: str) -> None:
        settings.workspace.joinpath("sketch.js").write_text(sketch, encoding="utf-8")
        graph = copy.deepcopy(graph_template)
        graph["run_id"] = run_id
        graph["nodes"]["answer"]["result"]["answer"] = (
            '<img id="run-attack" src=x onerror=alert(1)>'
        )
        fake_s17.complete(run_id, graph)
        if configure_stream is not None:
            configure_stream(run_id, graph)

    fake_s17.on_start = finish


@pytest.mark.browser
@pytest.mark.activity
@pytest.mark.asyncio
async def test_recorded_red_green_activity_and_safe_dialogs(
    browser_page: Page,
    live_product: str,
    fake_s17: FakeS17,
    settings,
    recorded_graph: dict,
) -> None:
    complete_recorded_run(fake_s17, settings, recorded_graph)
    await browser_page.goto(live_product)
    await expect(browser_page.locator("#app")).to_have_attribute("data-state", "landing")
    await expect(browser_page.locator("#telemetry-cage")).to_have_text("Locked")
    await expect(browser_page.locator("#telemetry-revision")).to_have_text("—")
    await expect(browser_page.locator("#telemetry-run")).to_have_text("—")
    await expect(browser_page.locator("#telemetry-sequence")).to_have_text("—")
    await expect(browser_page.locator("#telemetry-watchdog")).to_have_text("Idle")
    await expect(browser_page.locator("#header-run-status")).to_be_hidden()

    suggestion = browser_page.get_by_role("button", name="Rain that avoids my mouse")
    await suggestion.click()
    await expect(browser_page.locator("#prompt")).to_have_value("Rain that avoids my mouse")
    assert fake_s17.starts == []

    await browser_page.locator("#create-button").click()
    await expect(browser_page.locator("#app")).to_have_attribute("data-state", "ready")

    rows = browser_page.locator(".activity-item")
    row_text = await rows.all_text_contents()
    visible = "\n".join(row_text)
    for expected in (
        "Starting the factory",
        "Planning the next step",
        "Writing sketch.js",
        "Judging the simulation",
        "Found a problem (checker exit 1)",
        "Repairing sketch.js",
        "Check passed",
        "Simulation ready",
    ):
        assert expected in visible
    assert "Still thinking" not in visible
    assert "P5CHECK FAIL" in visible
    await expect(browser_page.locator("#simulation-stage")).to_have_attribute(
        "data-preview-state", "ready"
    )
    await expect(browser_page.locator("#telemetry-cage")).to_have_text("Active")
    await expect(browser_page.locator("#telemetry-revision")).to_have_text(
        re.compile(r"^[0-9a-f]{10}…$")
    )
    await expect(browser_page.locator("#telemetry-run")).to_have_text("run-fake-1")
    await expect(browser_page.locator("#telemetry-sequence")).to_have_text("13")
    await expect(browser_page.locator("#telemetry-watchdog")).to_have_text("Passed")
    await expect(browser_page.locator("#header-run-status")).to_have_text(
        "RUN run-fake-1 · SEQ 13"
    )
    assert await browser_page.locator("iframe").count() == 1

    provenance = await rows.evaluate_all(
        "items => items.map(item => ({run: item.dataset.runId, seq: item.dataset.sequence, "
        "kind: item.dataset.sourceKind}))"
    )
    assert provenance
    assert all(item["run"].startswith("run-fake-") for item in provenance)
    assert all(item["seq"].isdigit() and item["kind"] for item in provenance)
    assert len({item["seq"] for item in provenance}) == len(provenance)

    await browser_page.locator("#view-code").click()
    await expect(browser_page.locator("#code-dialog")).to_be_visible()
    await expect(browser_page.locator("#code-content")).to_contain_text("code-attack")
    assert await browser_page.locator("#code-dialog img").count() == 0
    await browser_page.get_by_role("button", name="Close code dialog").click()

    await browser_page.locator("#view-run").click()
    await expect(browser_page.locator("#run-dialog")).to_be_visible()
    await expect(browser_page.locator("#run-content")).to_contain_text("run-attack")
    assert await browser_page.locator("#run-dialog img").count() == 0


@pytest.mark.browser
@pytest.mark.activity
@pytest.mark.asyncio
async def test_run_evidence_overview_graph_raw_and_degraded_shapes(
    browser_page: Page,
    live_product: str,
    fake_s17: FakeS17,
    settings,
    recorded_graph: dict,
) -> None:
    graph = copy.deepcopy(recorded_graph)
    hostile_node_id = '<img id="node-attack" src=x onerror=alert(1)>'
    graph["nodes"]["repair"]["state"] = "failed"
    graph["nodes"][hostile_node_id] = {
        "skill": "custom_untrusted_step",
        "input": {"prompt": '<script id="input-attack">alert(1)</script>'},
        "metadata": {"side_effect": False},
    }
    graph["edges"] = [
        ["write", "check_red"],
        ["check_red", "repair"],
        ["repair", "check_green"],
        ["check_green", "answer"],
        ["write", "missing-node"],
    ]
    complete_recorded_run(fake_s17, settings, graph)

    await browser_page.goto(live_product)
    await browser_page.locator("#prompt").fill("A graph evidence fixture")
    await browser_page.locator("#create-button").click()
    await expect(browser_page.locator("#app")).to_have_attribute("data-state", "ready")
    await browser_page.locator("#view-run").click()

    dialog = browser_page.locator("#run-dialog")
    await expect(dialog).to_be_visible()
    await expect(browser_page.locator("#run-tab-overview")).to_have_attribute(
        "aria-selected", "true"
    )
    await expect(browser_page.locator("#run-panel-overview")).to_be_visible()
    summary = browser_page.locator(".run-summary-grid")
    for expected in ("run-fake-1", "Completed", "6", "5", "12"):
        await expect(summary).to_contain_text(expected)
    await expect(browser_page.locator(".run-status-counts")).to_contain_text("Succeeded 4")
    await expect(browser_page.locator(".run-status-counts")).to_contain_text("Failed 1")
    await expect(browser_page.locator(".run-status-counts")).to_contain_text("Unknown 1")
    steps = browser_page.locator(".run-step")
    await expect(steps).to_have_count(6)
    await expect(steps.last.locator("summary")).to_contain_text("Custom untrusted step")
    await steps.last.locator("summary").click()
    await expect(steps.last).to_contain_text("input-attack")
    assert await dialog.locator("img").count() == 0
    assert await dialog.locator("script#input-attack").count() == 0

    overview_tab = browser_page.locator("#run-tab-overview")
    await overview_tab.focus()
    await overview_tab.press("ArrowRight")
    await expect(browser_page.locator("#run-tab-graph")).to_have_attribute(
        "aria-selected", "true"
    )
    await expect(browser_page.locator("#run-panel-graph")).to_be_visible()
    graph_nodes = browser_page.locator(".run-graph-node")
    await expect(graph_nodes).to_have_count(6)
    await expect(browser_page.locator("#run-graph-edges > path")).to_have_count(4)
    await expect(browser_page.locator("#run-graph-note")).to_contain_text(
        "1 malformed edge was not drawn"
    )
    hostile_node = graph_nodes.filter(has_text="Custom untrusted step")
    await hostile_node.click()
    await expect(browser_page.locator("#run-inspector")).to_contain_text(hostile_node_id)
    await expect(browser_page.locator("#run-inspector")).to_contain_text("input-attack")
    assert await dialog.locator("img").count() == 0

    await browser_page.locator("#run-tab-raw").click()
    await expect(browser_page.locator("#run-panel-raw")).to_be_visible()
    expected_raw = await browser_page.evaluate(
        "fetch('/api/runs/run-fake-1').then(response => response.json())"
        ".then(graph => JSON.stringify(graph, null, 2))"
    )
    assert await browser_page.locator("#run-content").text_content() == expected_raw
    assert await dialog.locator("img").count() == 0

    await browser_page.set_viewport_size({"width": 390, "height": 844})
    bounds = await dialog.bounding_box()
    assert bounds is not None
    assert bounds["x"] >= 0 and bounds["y"] >= 0
    assert bounds["x"] + bounds["width"] <= 391
    assert bounds["y"] + bounds["height"] <= 845
    await browser_page.keyboard.press("Escape")
    await expect(dialog).to_be_hidden()

    async def in_progress_graph(route: Route) -> None:
        await route.fulfill(
            json={
                "run_id": "run-fake-1",
                "finished": False,
                "nodes": {
                    "active": {"skill": "read_code", "state": "running"},
                    "unknown": {"result": None},
                },
                "edges": [["active", "unknown"], ["unknown", "active"], ["active"], "bad"],
            }
        )

    await browser_page.route("**/api/runs/run-fake-1", in_progress_graph)
    await browser_page.locator("#view-run").click()
    await expect(summary).to_contain_text("In progress")
    await expect(summary).to_contain_text("Events—")
    await browser_page.locator("#run-tab-graph").click()
    await expect(browser_page.locator("#run-graph-note")).to_contain_text(
        "2 malformed edges were not drawn"
    )
    await expect(browser_page.locator("#run-graph-note")).to_contain_text(
        "2 nodes are shown in an unresolved dependency group"
    )
    await expect(graph_nodes).to_have_count(2)
    await expect(browser_page.locator("#run-graph-edges > path")).to_have_count(2)
    assert await dialog.locator("img").count() == 0


@pytest.mark.browser
@pytest.mark.activity
@pytest.mark.asyncio
async def test_reconnect_snapshot_and_replayed_sequence_add_no_rows(
    browser_page: Page,
    live_product: str,
    fake_s17: FakeS17,
    settings,
    recorded_graph: dict,
) -> None:
    def reconnecting_stream(run_id: str, graph: dict) -> None:
        initial = {**graph, "finished": False, "events": graph["events"][:6]}
        fake_s17.streams[run_id] = b"retry: 250\n" + sse_for_graph(
            initial, include_terminal=False
        )
        snapshot = {
            "type": "STATE_SNAPSHOT",
            "seq": 6,
            "source_kind": "snapshot",
            "state": {"results": {"check_red": {}}, "patches": []},
        }
        replayed = {**graph, "finished": False, "events": graph["events"][5:6]}
        remaining = {**graph, "events": graph["events"][6:]}
        fake_s17.reconnect_streams[run_id] = (
            f"data: {json.dumps(snapshot, separators=(',', ':'))}\n\n".encode()
            + sse_for_graph(replayed, include_terminal=False)
            + sse_for_graph(remaining, include_terminal=True)
        )

    complete_recorded_run(
        fake_s17,
        settings,
        recorded_graph,
        configure_stream=reconnecting_stream,
    )
    await browser_page.goto(live_product)
    await browser_page.locator("#prompt").fill("Bouncy magnets")
    await browser_page.locator("#create-button").click()
    await expect(browser_page.locator("#app")).to_have_attribute(
        "data-state", "reconnecting"
    )
    await expect(browser_page.locator("#telemetry-sequence")).to_have_text("6")
    await expect(browser_page.locator("#app")).to_have_attribute("data-state", "ready")
    await expect(browser_page.locator("#telemetry-sequence")).to_have_text("13")
    await expect(browser_page.locator("#telemetry-watchdog")).to_have_text("Passed")

    provenance = await browser_page.locator(".activity-item").evaluate_all(
        "items => items.map(item => item.dataset.sequence)"
    )
    assert len(provenance) == len(set(provenance))
    assert provenance.count("6") == 1
    assert all(
        "STATE_SNAPSHOT" not in text
        for text in await browser_page.locator(".activity-item").all_text_contents()
    )
    assert any(query.get("reconnect") == "1" for query in fake_s17.event_queries)


@pytest.mark.browser
@pytest.mark.activity
@pytest.mark.asyncio
async def test_degraded_health_is_explicit_and_disables_creation(
    browser_page: Page,
    live_product: str,
    fake_s17: FakeS17,
) -> None:
    fake_s17.ready_status = 503
    await browser_page.goto(live_product)
    await expect(browser_page.locator("#app")).to_have_attribute("data-state", "degraded")
    await expect(browser_page.locator("#system-banner")).to_be_visible()
    await expect(browser_page.locator("#create-button")).to_be_disabled()
    await expect(browser_page.locator("#telemetry-cage")).to_have_text("Locked")
    await expect(browser_page.locator("#telemetry-run")).to_have_text("—")
    await expect(browser_page.locator("#telemetry-watchdog")).to_have_text("Idle")


@pytest.mark.browser
@pytest.mark.activity
@pytest.mark.asyncio
async def test_terminal_run_failure_is_honest_and_safely_rendered(
    browser_page: Page,
    live_product: str,
    fake_s17: FakeS17,
) -> None:
    def fail(run_id: str) -> None:
        graph = {
            "run_id": run_id,
            "finished": True,
            "nodes": {},
            "edges": [],
            "events": [
                {"sequence": 1, "kind": "run_started", "node_id": None, "payload": {}},
                {
                    "sequence": 2,
                    "kind": "run_failed",
                    "node_id": None,
                    "payload": {
                        "error": '<img id="failure-attack" src=x onerror=alert(1)>',
                        "error_type": "RuntimeError",
                    },
                },
            ],
        }
        fake_s17.complete(run_id, graph)

    fake_s17.on_start = fail
    await browser_page.goto(live_product)
    await browser_page.locator("#prompt").fill("A failing toy")
    await browser_page.locator("#create-button").click()
    await expect(browser_page.locator("#app")).to_have_attribute("data-state", "failed")
    activity = browser_page.locator("#activity-list")
    await expect(activity).to_contain_text("Run failed")
    await expect(activity).to_contain_text("Run incomplete")
    await expect(activity).to_contain_text("failure-attack")
    await expect(browser_page.locator("#telemetry-cage")).to_have_text("Locked")
    await expect(browser_page.locator("#telemetry-run")).to_have_text("run-fake-1")
    await expect(browser_page.locator("#telemetry-sequence")).to_have_text("3")
    await expect(browser_page.locator("#telemetry-watchdog")).to_have_text("Idle")
    assert await activity.locator("img").count() == 0


@pytest.mark.browser
@pytest.mark.preview
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("trigger", "expected_name", "expected_text"),
    [
        ("window.triggerPreviewFailure()", "TypeError", "preview-error-attack"),
        ("void window.triggerPreviewRejection()", "RangeError", "preview-rejection-attack"),
    ],
)
async def test_verified_preview_cage_is_interactive_and_rejects_hostile_capabilities(
    browser_page: Page,
    live_product: str,
    fake_s17: FakeS17,
    settings,
    recorded_graph: dict,
    trigger: str,
    expected_name: str,
    expected_text: str,
) -> None:
    complete_recorded_run(
        fake_s17,
        settings,
        recorded_graph,
        sketch=SECURITY_SKETCH,
    )
    await browser_page.goto(live_product)
    await browser_page.locator("#prompt").fill("A securely caged interactive orbit")
    await browser_page.locator("#create-button").click()
    stage = browser_page.locator("#simulation-stage")
    await expect(stage).to_have_attribute("data-preview-state", "ready")

    iframe = browser_page.locator("#preview-host iframe")
    await expect(iframe).to_have_attribute("sandbox", "allow-scripts")
    await expect(iframe).to_have_attribute("referrerpolicy", "no-referrer")
    assert await iframe.get_attribute("allow") is None
    frame = browser_page.frame(url=re.compile(r"/preview/[0-9a-f]{64}"))
    assert frame is not None
    await frame.wait_for_function("window.securityResults?.networkBlocked !== null")
    security = await frame.evaluate("window.securityResults")
    assert security == {
        "networkBlocked": True,
        "storageBlocked": True,
        "parentBlocked": True,
        "navigationBlocked": True,
        "popupBlocked": True,
    }
    assert browser_page.url == live_product + "/"

    await frame.locator("canvas").click(position={"x": 320, "y": 210})
    await frame.wait_for_function("clicks === 1")

    await browser_page.evaluate(
        "window.postMessage({type: 'preview_error', preview_id: 'wrong', "
        "name: 'Error', message: 'wrong window', line: 0, column: 0}, '*')"
    )
    await frame.evaluate(
        "parent.postMessage({type: 'preview_error', preview_id: 'wrong', "
        "name: 'Error', message: 'wrong id', line: 0, column: 0}, '*')"
    )
    await frame.evaluate(
        "parent.postMessage({type: 'preview_error', preview_id: 'wrong', "
        "name: 'Error', message: 'extra field', line: 0, column: 0, extra: true}, '*')"
    )
    await browser_page.evaluate(
        "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    )
    await expect(stage).to_have_attribute("data-preview-state", "ready")
    assert len(fake_s17.starts) == 1

    await frame.evaluate(trigger)
    await expect(browser_page.locator("#app")).to_have_attribute("data-state", "failed")
    await expect(stage).to_have_attribute("data-preview-state", "error")
    await expect(browser_page.locator("#telemetry-cage")).to_have_text("Stopped")
    await expect(browser_page.locator("#telemetry-watchdog")).to_have_text("Stopped")
    await expect(browser_page.locator("#stage-description")).to_contain_text(expected_text)
    assert await browser_page.locator("#simulation-stage img").count() == 0
    assert await browser_page.locator("#preview-host iframe").count() == 0
    session = await browser_page.evaluate("fetch('/api/session').then(response => response.json())")
    assert session["session"]["state"] == "failed"
    assert session["session"]["browser_error"]["name"] == expected_name
    assert expected_text in session["session"]["browser_error"]["message"]
    assert len(fake_s17.starts) == 1


@pytest.mark.browser
@pytest.mark.preview
@pytest.mark.asyncio
async def test_preview_watchdog_destroys_unresponsive_frame_and_records_failure(
    browser_page: Page,
    live_product: str,
    fake_s17: FakeS17,
    settings,
    recorded_graph: dict,
) -> None:
    complete_recorded_run(fake_s17, settings, recorded_graph)

    async def shorten_lease(route: Route) -> None:
        response = await route.fetch()
        payload = await response.json()
        payload["ready_timeout_ms"] = 100
        await route.fulfill(response=response, json=payload)

    async def silent_preview(route: Route) -> None:
        response = await route.fetch()
        await route.fulfill(
            response=response,
            content_type="text/html",
            body="<!doctype html><title>silent preview</title>",
        )

    await browser_page.route("**/api/preview", shorten_lease)
    await browser_page.route(re.compile(r".*/preview/[0-9a-f]{64}\?.*"), silent_preview)
    await browser_page.goto(live_product)
    await browser_page.locator("#prompt").fill("An unresponsive preview fixture")
    await browser_page.locator("#create-button").click()

    await expect(browser_page.locator("#simulation-stage")).to_have_attribute(
        "data-preview-state", "timeout"
    )
    await expect(browser_page.locator("#telemetry-cage")).to_have_text("Stopped")
    await expect(browser_page.locator("#telemetry-watchdog")).to_have_text("Stopped")
    await expect(browser_page.locator("#stage-description")).to_have_text(
        "Preview did not become responsive."
    )
    assert await browser_page.locator("#preview-host iframe").count() == 0
    session = await browser_page.evaluate("fetch('/api/session').then(response => response.json())")
    assert session["session"]["state"] == "failed"
    assert session["session"]["browser_error"]["name"] == "PreviewTimeout"
    assert len(fake_s17.starts) == 1


@pytest.mark.browser
@pytest.mark.follow_up
@pytest.mark.asyncio
async def test_one_linked_follow_up_replaces_preview_after_read_edit_check(
    browser_page: Page,
    live_product: str,
    fake_s17: FakeS17,
    settings,
    recorded_graph: dict,
) -> None:
    def finish(run_id: str) -> None:
        if len(fake_s17.starts) == 1:
            settings.workspace.joinpath("sketch.js").write_text(
                INITIAL_FOLLOW_UP_SKETCH, encoding="utf-8"
            )
            graph = copy.deepcopy(recorded_graph)
            graph["run_id"] = run_id
            fake_s17.complete(run_id, graph)
            return
        assert settings.workspace.joinpath("sketch.js").read_text(
            encoding="utf-8"
        ) == INITIAL_FOLLOW_UP_SKETCH
        settings.workspace.joinpath("sketch.js").write_text(
            MODIFIED_FOLLOW_UP_SKETCH, encoding="utf-8"
        )
        fake_s17.complete(run_id, follow_up_graph(run_id))

    fake_s17.on_start = finish
    await browser_page.goto(live_product)
    await browser_page.locator("#prompt").fill("A planet that orbits when I click")
    await browser_page.locator("#create-button").click()
    await expect(browser_page.locator("#simulation-stage")).to_have_attribute(
        "data-preview-state", "ready"
    )
    await expect(browser_page.locator("#telemetry-cage")).to_have_text("Active")
    await expect(browser_page.locator("#telemetry-watchdog")).to_have_text("Passed")
    initial_revision = await browser_page.locator("#telemetry-revision").text_content()
    assert initial_revision is not None and re.fullmatch(r"[0-9a-f]{10}…", initial_revision)
    await expect(browser_page.locator("#follow-up-panel")).to_be_visible()
    initial_frame_element = browser_page.locator("#preview-host iframe")
    initial_src = await initial_frame_element.get_attribute("src")
    assert initial_src is not None
    initial_frame = browser_page.frame(url=re.compile(r"/preview/[0-9a-f]{64}"))
    assert initial_frame is not None
    assert await initial_frame.evaluate("typeof mousePressed === 'function'") is True
    assert await initial_frame.evaluate("angle = 1; mousePressed(); angle") == 0

    request_seen = asyncio.Event()
    release_request = asyncio.Event()

    async def hold_follow_up(route: Route) -> None:
        request_seen.set()
        await release_request.wait()
        await route.continue_()

    await browser_page.route("**/api/runs/follow-up", hold_follow_up)
    await browser_page.locator("#follow-up-prompt").fill(
        "Make the planet leave a glowing trail"
    )
    await browser_page.locator("#follow-up-button").click()
    await asyncio.wait_for(request_seen.wait(), timeout=1)
    assert await browser_page.locator("#preview-host iframe").count() == 0
    await expect(browser_page.locator("#follow-up-button")).to_be_disabled()
    await expect(browser_page.locator("#follow-up-panel")).to_be_hidden()
    await expect(browser_page.locator("#app")).to_have_attribute("data-state", "modifying")
    await expect(browser_page.locator("#telemetry-cage")).to_have_text("Locked")
    await expect(browser_page.locator("#telemetry-revision")).to_have_text(initial_revision)
    await expect(browser_page.locator("#telemetry-watchdog")).to_have_text("Idle")
    release_request.set()

    await expect(browser_page.locator("#app")).to_have_attribute("data-state", "ready")
    await expect(browser_page.locator("#simulation-stage")).to_have_attribute(
        "data-preview-state", "ready"
    )
    await expect(browser_page.locator("#telemetry-cage")).to_have_text("Active")
    await expect(browser_page.locator("#telemetry-revision")).to_have_text(
        re.compile(r"^[0-9a-f]{10}…$")
    )
    await expect(browser_page.locator("#telemetry-run")).to_have_text("run-fake-2")
    await expect(browser_page.locator("#telemetry-watchdog")).to_have_text("Passed")
    modified_revision = await browser_page.locator("#telemetry-revision").text_content()
    assert modified_revision is not None and modified_revision != initial_revision
    await expect(browser_page.locator("#follow-up-panel")).to_be_hidden()
    row_text = "\n".join(await browser_page.locator(".activity-item").all_text_contents())
    for expected in (
        "Starting the factory",
        "Reading the current sketch",
        "Updating sketch.js",
        "Judging the simulation",
        "Check passed",
        "Simulation ready",
    ):
        assert expected in row_text
    assert "Writing sketch.js" not in row_text

    modified_frame_element = browser_page.locator("#preview-host iframe")
    modified_src = await modified_frame_element.get_attribute("src")
    assert modified_src is not None
    assert modified_src != initial_src
    modified_frame = browser_page.frame(url=re.compile(r"/preview/[0-9a-f]{64}"))
    assert modified_frame is not None
    assert await modified_frame.evaluate("trailsEnabled") is True
    assert await modified_frame.evaluate("angle = 1; mousePressed(); angle") == 0

    assert len(fake_s17.starts) == 2
    assert fake_s17.starts[1]["allowed_side_effects"] == ["edit_code", "run_command"]
    assert "create_file" not in fake_s17.starts[1]["allowed_side_effects"]
    session = await browser_page.evaluate("fetch('/api/session').then(response => response.json())")
    runs = session["session"]["runs"]
    assert session["session"]["follow_up_used"] is True
    assert runs[1]["kind"] == "follow_up"
    assert runs[1]["parent_run_id"] == runs[0]["run_id"]

    code = await browser_page.evaluate("fetch('/api/code').then(response => response.json())")
    assert code["verified"] is True
    assert "function mousePressed() { angle = 0; }" in code["content"]
    assert "trailsEnabled = true" in code["content"]
