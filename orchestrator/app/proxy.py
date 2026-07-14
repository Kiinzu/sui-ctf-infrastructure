"""Path-routing RPC reverse proxy.

Players receive `http://<public_host>:<proxy_port>/<uuid>` as their RPC URL. This
FastAPI app validates the uuid against the registry, strips the `/<uuid>` prefix,
and forwards the JSON-RPC request to that instance's Sui fullnode over the
internal Docker network. An unknown / non-running uuid gets a 404 — so the RPC is
only reachable for a live instance the player was actually issued.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from .config import Config
from .registry import Registry

log = logging.getLogger("suictf.proxy")

# Concurrency backstops so one player can't saturate the shared client / event
# loop and degrade RPC + the nc menu for everyone (PoW gates spawn, not RPC).
_MAX_INFLIGHT = int(os.environ.get("PROXY_MAX_INFLIGHT", "").strip() or 64)
_MAX_INFLIGHT_PER_UUID = int(os.environ.get("PROXY_MAX_INFLIGHT_PER_UUID", "").strip() or 8)

# Hop-by-hop headers we must not forward.
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


def create_proxy_app(config: Config, registry: Registry) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0, pool=3.0),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
        )
        app.state.inflight_total = 0
        app.state.inflight_per = {}
        try:
            yield
        finally:
            await app.state.client.aclose()

    app = FastAPI(
        title="sui-ctf-orchestrator-proxy",
        docs_url=None, redoc_url=None, lifespan=lifespan,
    )

    @app.get("/")
    async def root() -> JSONResponse:
        return JSONResponse({"service": "sui-ctf-orchestrator", "status": "ok"})

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def _forward(uuid: str, path: str, request: Request) -> Response:
        record = registry.get(uuid)
        if record is None:
            return JSONResponse({"error": "unknown instance"}, status_code=404)
        if record["status"] != "running":
            return JSONResponse(
                {"error": f"instance not ready (status: {record['status']})"},
                status_code=503,
            )
        # Concurrency backstop (single event loop → no await between check+bump).
        per = app.state.inflight_per
        if (app.state.inflight_total >= _MAX_INFLIGHT
                or per.get(uuid, 0) >= _MAX_INFLIGHT_PER_UUID):
            return JSONResponse({"error": "too many concurrent requests"}, status_code=429)
        app.state.inflight_total += 1
        per[uuid] = per.get(uuid, 0) + 1
        try:
            target = (
                f"http://{record['container_name']}:{config.instance_rpc_port}/{path}"
            )
            body = await request.body()
            fwd_headers = {
                k: v for k, v in request.headers.items()
                if k.lower() not in _HOP_BY_HOP
            }
            try:
                upstream = await app.state.client.request(
                    request.method,
                    target,
                    content=body,
                    headers=fwd_headers,
                    params=dict(request.query_params),
                )
            except httpx.HTTPError as exc:
                log.warning("proxy upstream error for %s: %s", uuid, exc)
                return JSONResponse({"error": "instance unreachable"}, status_code=502)

            resp_headers = {
                k: v for k, v in upstream.headers.items()
                if k.lower() not in _HOP_BY_HOP
            }
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                headers=resp_headers,
                media_type=upstream.headers.get("content-type"),
            )
        finally:
            app.state.inflight_total -= 1
            per[uuid] = per.get(uuid, 1) - 1
            if per[uuid] <= 0:
                per.pop(uuid, None)

    @app.api_route("/{uuid}", methods=["GET", "POST", "OPTIONS"])
    async def proxy_root(uuid: str, request: Request) -> Response:
        return await _forward(uuid, "", request)

    @app.api_route("/{uuid}/{path:path}", methods=["GET", "POST", "OPTIONS"])
    async def proxy_path(uuid: str, path: str, request: Request) -> Response:
        return await _forward(uuid, path, request)

    return app
