"""S17 adapter contracts, including secrecy and bounded failure mapping."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from physics_toy_factory.errors import ProductError
from physics_toy_factory.s17_client import AmbiguousStartError, S17Client
from tests.conftest import CONTROL_TOKEN


@pytest.mark.asyncio
async def test_start_uses_fixed_scope_exact_authority_and_bearer(settings) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(202, json={"run_id": "run-contract-1", "status": "accepted"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        run_id = await S17Client(settings, transport).start_run(
            goal="fixed goal", allowed_side_effects=["edit_code", "run_command"]
        )

    assert run_id == "run-contract-1"
    assert captured["authorization"] == f"Bearer {CONTROL_TOKEN}"
    assert captured["body"] == {
        "tenant_id": "physics-toy-factory",
        "project_id": "demo",
        "user_id": "local-audience",
        "agent_id": "p5-builder",
        "respond_as": "text",
        "prompt": "fixed goal",
        "allowed_side_effects": ["edit_code", "run_command"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "product_status", "code"),
    [(401, 502, "s17_auth_failed"), (503, 503, "s17_not_ready"), (418, 502, "s17_start_rejected")],
)
async def test_start_rejections_do_not_forward_upstream_body(
    settings, status: int, product_status: int, code: str
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="control-token=upstream-secret")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        with pytest.raises(ProductError) as caught:
            await S17Client(settings, transport).start_run(goal="x", allowed_side_effects=[])

    assert caught.value.status == product_status
    assert caught.value.code == code
    assert "upstream-secret" not in caught.value.message
    assert CONTROL_TOKEN not in caught.value.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"run_id": "not-a-run", "status": "accepted"},
        {"run_id": "run-ok", "status": "wrong"},
        {"status": "accepted"},
        ["run-ok"],
    ],
)
async def test_start_rejects_malformed_success(settings, payload: object) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(202, json=payload))
    ) as transport:
        with pytest.raises(ProductError, match="invalid start response"):
            await S17Client(settings, transport).start_run(goal="x", allowed_side_effects=[])


@pytest.mark.asyncio
async def test_start_timeout_is_explicitly_ambiguous(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private timeout detail", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        with pytest.raises(AmbiguousStartError) as caught:
            await S17Client(settings, transport).start_run(goal="x", allowed_side_effects=[])

    assert caught.value.status == 504
    assert caught.value.retryable is True
    assert caught.value.ambiguous is True
    assert "private timeout detail" not in caught.value.message


@pytest.mark.asyncio
async def test_get_run_rejects_mismatched_or_missing_shape(settings) -> None:
    responses = iter(
        [
            httpx.Response(200, json={"run_id": "run-other", "nodes": {}, "events": []}),
            httpx.Response(200, json={"run_id": "run-owned", "nodes": []}),
        ]
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: next(responses))
    ) as transport:
        client = S17Client(settings, transport)
        for _ in range(2):
            with pytest.raises(ProductError) as caught:
                await client.get_run("run-owned")
            assert caught.value.code == "s17_invalid_response"


@pytest.mark.asyncio
async def test_sse_preserves_id_data_and_keepalive_frames(settings) -> None:
    body = (
        'id: 7\ndata: {"type":"RUN_STARTED","seq":7}\n\n'
        ": keepalive\n\n"
        'data: {"type":"RUN_FINISHED","seq":8}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["after"] == "7"
        assert request.url.params["reconnect"] == "1"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        stream = await S17Client(settings, transport).open_events("run-owned", after=7, reconnect=True)
        frames = [frame async for frame in stream.frames()]
        await stream.close()

    joined = b"".join(frames)
    assert b"id: 7" in joined
    assert b": keepalive" in joined
    assert b'"RUN_FINISHED"' in joined


@pytest.mark.asyncio
async def test_recorded_sse_stream_contract(settings) -> None:
    records = [
        json.loads(line)
        for line in (Path(__file__).parent / "fixtures" / "s17_events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    body = ""
    for record in records:
        if record.get("keepalive"):
            body += ": keepalive\n\n"
            continue
        if "id" in record:
            body += f"id: {record['id']}\n"
        body += f"data: {json.dumps(record['data'], separators=(',', ':'))}\n\n"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200, text=body, headers={"content-type": "text/event-stream"}
            )
        )
    ) as transport:
        stream = await S17Client(settings, transport).open_events("run-recorded", after=0, reconnect=False)
        frames = [frame async for frame in stream.frames()]
        await stream.close()
    assert len(frames) == len(records)
    assert b"RUN_STARTED" in frames[0]
    assert any(b"STEP_STARTED" in frame for frame in frames)
    assert b"RUN_FINISHED" in frames[-1]


@pytest.mark.asyncio
async def test_sse_rejects_token_in_upstream_payload(settings) -> None:
    body = f'data: {{"type":"RUN_FINISHED","detail":"{CONTROL_TOKEN}"}}\n\n'
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200, text=body, headers={"content-type": "text/event-stream"}
            )
        )
    ) as transport:
        stream = await S17Client(settings, transport).open_events("run-owned", after=0, reconnect=False)
        with pytest.raises(ProductError) as caught:
            _ = [frame async for frame in stream.frames()]
        await stream.close()

    assert caught.value.code == "s17_sse_invalid"
    assert CONTROL_TOKEN not in caught.value.message


@pytest.mark.asyncio
async def test_probes_never_return_upstream_body(settings) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(503, text="private detail"))
    ) as transport:
        probe = await S17Client(settings, transport).probe("/readyz")
    assert probe.reachable is True
    assert probe.status_code == 503
    assert probe.ok is False
    assert not hasattr(probe, "body")
