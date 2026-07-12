# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.mat_utils.substance_bridge.connection.

No Maya runtime required — covers SubstanceConnection and PainterRpcClient
against in-process fixtures. The generic stream primitives the connection
composes (OutputStream / ProcessReader / LogTailer) live in
pythontk.core_utils.process_stream and are tested in
pythontk/test/test_process_stream.py.
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


class _StubRpcHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        envelope = json.loads(body.decode("utf-8"))
        response = {
            "jsonrpc": "2.0",
            "id": envelope.get("id"),
            "result": {"echoed_method": envelope.get("method")},
        }
        payload = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args, **kwargs):
        pass  # silence the stub server


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestPainterRpcClient(unittest.TestCase):
    def test_ping_returns_false_for_closed_port(self):
        port = _free_port()
        client = PainterRpcClient(port=port)
        self.assertFalse(client.ping(timeout=0.5))

    def test_concurrent_ids_are_unique(self):
        client = PainterRpcClient(port=1)  # never actually called

        ids = []
        ids_lock = threading.Lock()

        def hammer():
            local = []
            for _ in range(500):
                local.append(client._build_envelope("noop", None)["id"])
            with ids_lock:
                ids.extend(local)

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(ids), 2000)
        self.assertEqual(len(set(ids)), 2000, "RPC ids must be unique across threads")

    def test_attach_to_dead_port_raises(self):
        port = _free_port()
        with self.assertRaises(ConnectionRefusedError):
            SubstanceConnection.attach(
                port=port, log_path=None, verify_timeout=0.5
            )

    def test_attach_without_verify_returns_conn(self):
        port = _free_port()
        conn = SubstanceConnection.attach(
            port=port, log_path=None, verify_alive=False
        )
        try:
            self.assertIsNotNone(conn.rpc)
            self.assertIsNone(conn.process)
            self.assertFalse(conn.is_alive())  # port is dead, infer from RPC
        finally:
            conn.close()

    def test_attach_to_stub_server(self):
        port = _free_port()
        server = HTTPServer(("127.0.0.1", port), _StubRpcHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = SubstanceConnection.attach(
                port=port, log_path=None, verify_timeout=2.0
            )
            try:
                self.assertTrue(conn.is_alive())
                # Round-trip through the attached RPC client.
                response = conn.rpc.eval_js("alg.log('hi')")
                self.assertEqual(response["result"]["echoed_method"], "eval")
            finally:
                conn.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_call_against_stub_server(self):
        port = _free_port()
        server = HTTPServer(("127.0.0.1", port), _StubRpcHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = PainterRpcClient(port=port, timeout=5.0)
            self.assertTrue(client.wait_until_ready(timeout=3.0))
            response = client.eval_js("alg.log('hi')")
            self.assertEqual(response["result"]["echoed_method"], "eval")
            self.assertEqual(response["jsonrpc"], "2.0")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
