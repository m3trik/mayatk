# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.mat_utils.substance_bridge.connection.

No Maya runtime required — covers SubstanceConnection and PainterRpcClient
against an in-process stub that speaks the substance_rpc plugin's wire
format (``GET /health`` + ``POST / {"op", "kwargs"}``). The generic
stream primitives the connection composes (OutputStream / ProcessReader /
LogTailer) live in pythontk.core_utils.process_stream and are tested in
pythontk/test/test_process_stream.py. The real plugin server + ops are
covered in test_substance_rpc_plugin.py.
"""
import os
import sys
import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

# Ensure package is importable when running standalone
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mayatk.mat_utils.substance_bridge.connection import SubstanceConnection
from mayatk.mat_utils.substance_bridge.substance_rpc import PainterRpcClient


class _StubPluginHandler(BaseHTTPRequestHandler):
    """Minimal double of the substance_rpc plugin server: echoes the op."""

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._respond(200, {"ok": True, "value": "alive"})
        else:
            self._respond(404, {"ok": False, "error": "not found"})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        self._respond(
            200,
            {
                "ok": True,
                "value": {
                    "echoed_op": body.get("op"),
                    "echoed_kwargs": body.get("kwargs") or {},
                },
            },
        )

    def log_message(self, *args, **kwargs):
        pass  # silence the stub server

    def _respond(self, status, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _StubServerMixin:
    """Spin the stub plugin server up/down around each test."""

    def _start_stub(self):
        port = _free_port()
        server = HTTPServer(("127.0.0.1", port), _StubPluginHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return port


class TestPainterRpcClient(_StubServerMixin, unittest.TestCase):
    def test_ping_returns_false_for_closed_port(self):
        port = _free_port()
        client = PainterRpcClient(port=port)
        self.assertFalse(client.ping(timeout=0.5))

    def test_invoke_against_stub_server(self):
        port = self._start_stub()
        client = PainterRpcClient(port=port, timeout=5.0)
        self.assertTrue(client.wait_until_ready(timeout=3.0))
        value = client.invoke("system.ping")
        self.assertEqual(value["echoed_op"], "system.ping")

    def test_eval_js_routes_to_js_evaluate_op(self):
        port = self._start_stub()
        client = PainterRpcClient(port=port, timeout=5.0)
        value = client.eval_js("alg.log('hi')")
        self.assertEqual(value["echoed_op"], "js.evaluate")
        self.assertEqual(value["echoed_kwargs"], {"script": "alg.log('hi')"})

    def test_reload_mesh_routes_to_mesh_reload_op(self):
        port = self._start_stub()
        client = PainterRpcClient(port=port, timeout=5.0)
        value = client.reload_mesh("C:/tmp/scene.fbx")
        self.assertEqual(value["echoed_op"], "mesh.reload")
        self.assertEqual(
            value["echoed_kwargs"],
            {
                "mesh_path": "C:/tmp/scene.fbx",
                "preserve_strokes": True,
                "import_cameras": False,
            },
        )


class TestSubstanceConnectionAttach(_StubServerMixin, unittest.TestCase):
    def test_attach_to_dead_port_raises(self):
        port = _free_port()
        with self.assertRaises(ConnectionRefusedError):
            SubstanceConnection.attach(port=port, log_path=None, verify_timeout=0.5)

    def test_attach_without_verify_returns_conn(self):
        port = _free_port()
        conn = SubstanceConnection.attach(port=port, log_path=None, verify_alive=False)
        try:
            self.assertIsNotNone(conn.rpc)
            self.assertIsNone(conn.process)
            self.assertFalse(conn.is_alive())  # port is dead, infer from RPC
        finally:
            conn.close()

    def test_attach_to_stub_server(self):
        port = self._start_stub()
        conn = SubstanceConnection.attach(port=port, log_path=None, verify_timeout=2.0)
        try:
            self.assertTrue(conn.is_alive())
            # Round-trip through the attached RPC client.
            value = conn.rpc.eval_js("alg.log('hi')")
            self.assertEqual(value["echoed_op"], "js.evaluate")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
