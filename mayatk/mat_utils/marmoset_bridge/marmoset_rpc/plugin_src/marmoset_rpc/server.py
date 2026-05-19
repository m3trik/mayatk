# !/usr/bin/python
# coding=utf-8
"""HTTP JSON-RPC server for the marmoset_rpc plugin.

Routes:
  GET  /health    -> liveness probe
  POST /          -> dispatch ``{"op": "<name>", "kwargs": {...}}``
  POST /describe  -> introspection ``{"op": "<name>" | ""}``

The server runs on a daemon thread so it dies with Toolbag's process;
no explicit shutdown is required for normal use, but :func:`stop_server`
is exposed for tests and hot-reload.
"""
import json
import os
import threading
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import registry
from .main_thread import run_on_main_thread


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler API)
        if self.path == "/health":
            self._respond(200, {"ok": True, "value": "alive"})
        else:
            self._respond(404, {"ok": False, "error": f"GET {self.path!r}"})

    def do_POST(self):  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length).decode("utf-8")
            req = json.loads(raw) if raw else {}
        except Exception as exc:
            self._respond(400, {"ok": False, "error": f"Bad JSON: {exc}"})
            return

        if self.path == "/describe":
            self._respond(200, {"ok": True, "value": registry.describe(req.get("op") or None)})
            return

        self._dispatch(req)

    def _dispatch(self, req):
        op_name = req.get("op")
        kwargs = req.get("kwargs") or {}
        handler = registry.get(op_name)
        if handler is None:
            self._respond(404, {
                "ok": False,
                "error": f"Unknown op: {op_name!r}",
                "available": registry.all_ops(),
            })
            return
        try:
            # Every op runs through the main-thread marshaller. Inside
            # Toolbag this trampolines onto the Qt main thread; outside
            # (tests, agent inspection), the marshaller short-circuits
            # to a direct call. Pure-Python ops like system.ping pay a
            # negligible cost for the uniformity.
            value = run_on_main_thread(handler, **kwargs)
        except Exception as exc:
            self._respond(500, {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })
            return
        self._respond(200, {"ok": True, "value": value})

    # Silence default access logs so they don't drown Toolbag's log.txt.
    def log_message(self, *_a, **_kw):
        pass

    def _respond(self, status, payload):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _ReusableServer(HTTPServer):
    # SO_REUSEADDR so a Toolbag relaunch doesn't get blocked by the
    # previous instance's socket sitting in TIME_WAIT.
    allow_reuse_address = True


_server = None
_thread = None


def start_server(port=None, host="127.0.0.1"):
    """Start the HTTP server in a daemon thread. Idempotent."""
    global _server, _thread
    if _server is not None:
        return _server.server_address

    if port is None:
        port = int(os.environ.get("MARMOSET_RPC_PORT", "8765"))

    _server = _ReusableServer((host, port), _Handler)
    _thread = threading.Thread(
        target=_server.serve_forever,
        daemon=True,
        name="MarmosetRpcServer",
    )
    _thread.start()
    print(f"[marmoset_rpc] listening on http://{host}:{port}")
    return _server.server_address


def stop_server():
    """Shut down the server (mostly useful for tests / hot-reload)."""
    global _server, _thread
    if _server is None:
        return
    try:
        _server.shutdown()
        _server.server_close()
    finally:
        _server = None
        _thread = None


def is_running():
    return _server is not None
