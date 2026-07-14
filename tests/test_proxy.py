import dataclasses
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from fastapi.testclient import TestClient

from app.config import Config
from app.proxy import create_proxy_app

_captured = {}


class _Upstream(BaseHTTPRequestHandler):
    def _handle(self):
        _captured["path"] = self.path
        _captured["method"] = self.command
        body = b'{"jsonrpc":"2.0","result":"ok"}'
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _handle
    do_POST = _handle

    def log_message(self, *a):
        pass


class _FakeReg:
    def __init__(self, record):
        self.record = record

    def get(self, uuid):
        return self.record if uuid == "good" else None


def _app_and_srv():
    srv = HTTPServer(("127.0.0.1", 0), _Upstream)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg = dataclasses.replace(Config(), instance_rpc_port=port)
    rec = {"status": "running", "container_name": "127.0.0.1"}
    app = create_proxy_app(cfg, _FakeReg(rec))
    return app, srv


def test_health_and_root():
    app, srv = _app_and_srv()
    try:
        with TestClient(app) as client:
            assert client.get("/healthz").status_code == 200
            assert client.get("/").json()["service"] == "sui-ctf-orchestrator"
    finally:
        srv.shutdown()


def test_unknown_uuid_404():
    app, srv = _app_and_srv()
    try:
        with TestClient(app) as client:
            assert client.post("/nope", json={"a": 1}).status_code == 404
    finally:
        srv.shutdown()


def test_forward_root_strips_uuid():
    app, srv = _app_and_srv()
    try:
        with TestClient(app) as client:
            r = client.post("/good", json={"jsonrpc": "2.0"})
        assert r.status_code == 200
        assert _captured["path"] == "/"
        assert _captured["method"] == "POST"
    finally:
        srv.shutdown()


def test_forward_subpath_strips_uuid():
    app, srv = _app_and_srv()
    try:
        with TestClient(app) as client:
            client.get("/good/sub/thing")
        assert _captured["path"] == "/sub/thing"
    finally:
        srv.shutdown()
