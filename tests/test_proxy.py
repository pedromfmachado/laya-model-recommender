import asyncio
from contextlib import asynccontextmanager
import json
import socket

import httpx
import pytest
import uvicorn

from laya_router.classifier import DemoClassifier
from laya_router.config import load_settings
from laya_router.demo import demo_upstream
from laya_router.proxy import create_app


@asynccontextmanager
async def client_for(handler=demo_upstream, **kwargs):
    app = create_app(load_settings("routes.example.toml"), classifier=DemoClassifier(),
                     transport=httpx.MockTransport(handler), **kwargs)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
            yield client, app


async def test_json_forwarding_credentials_and_effort(caplog):
    observed = []
    async def upstream(request):
        observed.append(request)
        return httpx.Response(201, json={"id": "resp1", "output": [], "secret": "upstream body"},
                              headers={"x-request-id": "request1", "set-cookie": "secret_cookie", "retry-after": "5"})
    body = {"model": "laya-auto", "input": [{"role": "user", "content": "Implement secret feature"},
            {"type": "reasoning", "encrypted_content": "opaque"}, {"type": "function_call_output", "call_id": "call1", "output": "result"}],
            "instructions": "private instructions", "tools": [{"type": "function", "name": "test"}],
            "reasoning": {"effort": "low", "summary": "auto"}, "metadata": {"private": "data"}}
    async with client_for(upstream, api_key="upstream-secret") as (client, app):
        with caplog.at_level("INFO", logger="laya_router"):
            response = await client.post("/v1/responses", json=body, headers={"authorization": "Bearer caller-secret"})
    forwarded = json.loads(observed[0].content)
    assert forwarded == {**body, "model": "gpt-5.6-sol", "reasoning": {"effort": "medium", "summary": "auto"}}
    assert observed[0].headers["authorization"] == "Bearer upstream-secret"
    assert response.status_code == 201 and response.json()["secret"] == "upstream body"
    assert response.headers["x-request-id"] == "request1"
    assert "set-cookie" not in response.headers
    for secret in ("upstream-secret", "caller-secret", "secret feature", "private instructions"):
        assert secret not in caplog.text
    assert "category=coding" in caplog.text


async def test_sse_tool_roundtrip_and_followup():
    async with client_for() as (client, app):
        body = {"model": "laya-auto", "input": "Implement a function", "stream": True,
                "tools": [{"type": "function", "name": "demo_echo", "parameters": {"type": "object"}}]}
        response = await client.post("/v1/responses", json=body)
        events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
        assert events[0]["type"] == "response.created"
        completed = events[-1]["response"]
        call = completed["output"][0]
        assert call["type"] == "function_call" and completed["model"] == "gpt-5.6-sol"
        result = await client.post("/v1/responses", json={"model": "laya-auto", "previous_response_id": completed["id"],
            "input": [{"type": "function_call_output", "call_id": call["call_id"], "output": "echo success"}]})
        assert "echo success" in result.json()["output"][0]["content"][0]["text"]
        assert result.json()["model"] == "gpt-5.6-sol"
        followup = await client.post("/v1/responses", json={"model": "laya-auto", "previous_response_id": result.json()["id"],
                                                          "input": "Explain this in a README"})
        assert followup.json()["model"] == "gpt-5.6-luna"


@pytest.mark.parametrize("status", [400, 401, 429, 500])
async def test_upstream_errors_relay_verbatim(status):
    async def upstream(request):
        return httpx.Response(status, content=b'{"error":{"message":"gateway error"}}',
                              headers={"content-type": "application/json", "retry-after": "1"})
    async with client_for(upstream) as (client, _):
        response = await client.post("/v1/responses", json={"model": "laya-auto", "input": "hi"})
    assert response.status_code == status
    assert response.content == b'{"error":{"message":"gateway error"}}'
    assert response.headers["retry-after"] == "1"


async def test_disconnected_upstream_explicit_error_not_fake_answer():
    async def upstream(request):
        raise httpx.ConnectError("secret endpoint", request=request)
    async with client_for(upstream) as (client, _):
        response = await client.post("/v1/responses", json={"model": "laya-auto", "input": "hi"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_unavailable"
    assert "secret endpoint" not in response.text


@pytest.mark.parametrize("body", [{"model": "unknown"}, [], {"model": "laya-auto", "reasoning": "invalid"},
                                  {"model": "laya-auto", "input": 123}, {"model": "laya-auto", "stream": "yes"}])
async def test_invalid_requests(body):
    async with client_for() as (client, _):
        assert (await client.post("/v1/responses", json=body)).status_code == 400


async def test_degraded_health():
    classifier = DemoClassifier()
    classifier.ready = False
    classifier.mode = "fallback"
    app = create_app(load_settings("routes.example.toml"), classifier=classifier)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
            health = (await client.get("/healthz")).json()
    assert health["status"] == "degraded" and health["fallback"] == "general"


@asynccontextmanager
async def socket_server(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(200):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(.01)
        assert server.started
        yield port
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()


@pytest.mark.parametrize("phase", ["headers", "json", "sse"])
async def test_real_socket_client_disconnect_cancels_upstream(phase):
    reached = asyncio.Event()
    canceled = asyncio.Event()
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            try:
                reached.set()
                if phase == "sse":
                    yield b'event: response.created\ndata: {"response":{"id":"cancel-me"}}\n\n'
                await asyncio.sleep(60)
                yield b"unreachable"
            finally:
                canceled.set()
        async def aclose(self):
            canceled.set()
    async def upstream(request):
        if phase == "headers":
            reached.set()
            try:
                await asyncio.sleep(60)
            finally:
                canceled.set()
        return httpx.Response(200, stream=SlowStream(), headers={"content-type": "text/event-stream" if phase == "sse" else "application/json"})
    app = create_app(load_settings("routes.example.toml"), classifier=DemoClassifier(), transport=httpx.MockTransport(upstream))
    async with socket_server(app) as port:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        payload = json.dumps({"model": "laya-auto", "input": "hi", "stream": phase == "sse"}).encode()
        writer.write(f"POST /v1/responses HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n".encode() + payload)
        await writer.drain()
        await asyncio.wait_for(reached.wait(), 5)
        if phase == "sse":
            await asyncio.wait_for(reader.read(2048), 5)
        writer.close()
        await writer.wait_closed()
        await asyncio.wait_for(canceled.wait(), 5)
