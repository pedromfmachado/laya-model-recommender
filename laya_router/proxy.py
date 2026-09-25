import asyncio
from contextlib import asynccontextmanager, suppress
import json
import logging
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from .classifier import LayaClassifier
from .routing import Router

LOG = logging.getLogger("laya_router")
DEFAULT_UPSTREAM = "https://litellm.data.odyssey.preview.nwse.cloud/v1/responses"
RESPONSE_HEADERS = {"content-type", "cache-control", "retry-after", "x-request-id", "request-id",
                    "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests", "x-ratelimit-reset-requests"}


def validate_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Upstream must be a complete HTTP(S) Responses URL without credentials, query, or fragment")
    return url


def error(message, status, code):
    return JSONResponse({"error": {"message": message, "type": "invalid_request_error" if status < 500 else "api_error",
                                   "code": code}}, status_code=status)


class ResponseTracker:
    """Inspect only bounded SSE framing to retain response ancestry; bytes stay untouched."""
    def __init__(self, router, turn):
        self.router = router
        self.turn = turn
        self.buffer = b""

    def feed(self, chunk):
        self.buffer += chunk
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            if line.startswith(b"data:"):
                try:
                    value = json.loads(line[5:])
                    response = value.get("response", {})
                    response_id = response.get("id")
                    if isinstance(response_id, str):
                        self.router.remember(response_id, self.turn)
                except (ValueError, AttributeError):
                    pass
        if len(self.buffer) > 1024 * 1024:
            self.buffer = b""


def create_app(settings, upstream_url=DEFAULT_UPSTREAM, *, classifier=None, transport=None, api_key=None):
    upstream_url = validate_url(upstream_url)
    classifier = classifier or LayaClassifier()
    router = Router(settings, classifier)

    @asynccontextmanager
    async def lifespan(app):
        await classifier.start(settings)
        LOG.info("startup mode=%s classifier_ready=%s", classifier.mode, classifier.ready)
        async with httpx.AsyncClient(transport=transport, follow_redirects=False,
                                    timeout=httpx.Timeout(300, connect=15), trust_env=False) as client:
            app.state.client = client
            yield
        for task in list(router.pending.values()):
            task.cancel()
        await classifier.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.router = router

    @app.get("/healthz")
    async def health():
        return {"status": "ok" if classifier.ready else "degraded", "mode": classifier.mode,
                "classifier_ready": classifier.ready,
                "fallback": None if classifier.ready else "general", "upstream_checked": False}

    @app.post("/v1/responses")
    async def responses(request: Request):
        try:
            body = await request.json()
        except (ValueError, UnicodeError):
            return error("Request body must be valid JSON", 400, "invalid_json")
        if not isinstance(body, dict):
            return error("Request body must be an object", 400, "invalid_request")
        if body.get("model") != "laya-auto":
            return error("Only model 'laya-auto' is supported", 400, "model_not_found")
        if "input" in body and not isinstance(body["input"], (str, list)):
            return error("input must be text or a list of Responses items", 400, "invalid_input")
        if body.get("reasoning") is not None and not isinstance(body["reasoning"], dict):
            return error("reasoning must be an object", 400, "invalid_reasoning")
        if "stream" in body and not isinstance(body["stream"], bool):
            return error("stream must be a boolean", 400, "invalid_stream")
        upstream = None

        async def disconnected():
            while True:
                message = await request.receive()
                if message["type"] == "http.disconnect":
                    return

        async def prepare():
            nonlocal upstream
            LOG.info("route_evaluation_started requested_model=%s", body["model"])
            turn = await router.route(body, request.headers)
            decision = turn.decision
            LOG.info("route_selected category=%s alias=%s effort=%s duration_ms=%.2f fallback=%s",
                     decision.route.category, decision.route.model, decision.route.effort,
                     decision.duration_ms, decision.fallback)
            payload = {**body, "model": decision.route.model,
                       "reasoning": {**(body.get("reasoning") or {}), "effort": decision.route.effort}}
            headers = {"accept": "text/event-stream" if body.get("stream") else "application/json"}
            if api_key:
                headers["authorization"] = "Bearer " + api_key
            req = app.state.client.build_request("POST", upstream_url, json=payload, headers=headers)
            try:
                upstream = await app.state.client.send(req, stream=True)
                LOG.info("upstream_response status=%s", upstream.status_code)
                response_headers = {key: value for key, value in upstream.headers.items() if key in RESPONSE_HEADERS}
                if "text/event-stream" in upstream.headers.get("content-type", ""):
                    tracker = ResponseTracker(router, turn)

                    async def relay():
                        try:
                            async for chunk in upstream.aiter_bytes():
                                tracker.feed(chunk)
                                yield chunk
                        finally:
                            await upstream.aclose()
                    return StreamingResponse(relay(), status_code=upstream.status_code,
                                             headers=response_headers, background=BackgroundTask(upstream.aclose))
                content = await upstream.aread()
                await upstream.aclose()
                try:
                    response_id = json.loads(content).get("id")
                    if isinstance(response_id, str):
                        router.remember(response_id, turn)
                except (ValueError, AttributeError):
                    pass
                return Response(content, upstream.status_code, headers=response_headers)
            except httpx.HTTPError:
                if upstream:
                    await upstream.aclose()
                LOG.warning("upstream_unavailable category=%s alias=%s effort=%s duration_ms=%.2f fallback=%s upstream_status=unavailable",
                            decision.route.category, decision.route.model, decision.route.effort,
                            decision.duration_ms, decision.fallback)
                return error("LiteLLM upstream is unavailable. Use the explicit demo command for an offline fixture.", 502, "upstream_unavailable")

        watcher = asyncio.create_task(disconnected())
        work = asyncio.create_task(prepare())
        try:
            done, _ = await asyncio.wait({watcher, work}, return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                work.cancel()
                with suppress(asyncio.CancelledError):
                    await work
                if upstream:
                    await upstream.aclose()
                return Response(status_code=499)
            return await work
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
            if not work.done():
                work.cancel()
                with suppress(asyncio.CancelledError):
                    await work
                if upstream:
                    await upstream.aclose()

    return app
