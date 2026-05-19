# !/usr/bin/python
# coding=utf-8
"""JSON-RPC 2.0 client for a Painter-side Python plugin.

Kept separate from the bridge's stdio/log machinery (see the parent
``substance_bridge/connection.py``) so the RPC concern can evolve
independently. Today this client speaks the JSON-RPC 2.0 envelope; the
plan is to switch it to :class:`pythontk.RpcClient`'s ``{op, kwargs}``
shape once the Painter plugin lands.
"""
import itertools
import json
import socket
import time
import urllib.error
import urllib.request
from typing import Optional


# Painter's pythonjsonserver default; override via constructor.
DEFAULT_RPC_PORT = 8090


class PainterRpcClient:
    """JSON-RPC 2.0 client for a Painter-side JSON server.

    .. warning::

       Stock Adobe Substance 3D Painter (10.x) does **not** auto-bind a
       JSON-RPC port on launch. The bundled ``qrc:/plugins/pythonjsonserver.qml``
       plugin loads but does not open a TCP listener, even with the
       ``--enable-remote-scripting`` CLI flag (verified empirically on
       2026-05-18: scanning the common port range during a 75s startup
       window returned zero listeners).

       To use this client against Painter you must first stand up a
       Painter Python plugin that exposes an HTTP JSON-RPC endpoint on a
       known port. Painter Python plugins live in
       ``%USERPROFILE%\\Documents\\Adobe\\Adobe Substance 3D Painter\\python\\plugins``;
       the plugin can use ``substance_painter.*`` APIs and any standard
       Python HTTP server to surface them.

       The client itself is correct (verified against an HTTP stub server
       in :mod:`test.test_substance_connection`); it just needs a real
       endpoint to talk to.

    Wire format: POST a JSON-RPC envelope to ``http://<host>:<port>/``.
    If a plugin uses a different shape, subclass and override
    :meth:`_build_envelope` / :meth:`_parse_response`.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = DEFAULT_RPC_PORT,
        timeout: float = 30.0,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        # itertools.count is atomic at the C level on CPython -- safe to share
        # across threads without an explicit lock.
        self._id_counter = itertools.count(1)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def ping(self, timeout: float = 1.0) -> bool:
        """Return True if a TCP connection succeeds."""
        try:
            with socket.create_connection((self.host, self.port), timeout=timeout):
                return True
        except OSError:
            return False

    def wait_until_ready(
        self, timeout: float = 60.0, poll_interval: float = 0.5
    ) -> bool:
        """Poll the port until it accepts connections, or *timeout* expires."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.ping(timeout=0.5):
                return True
            time.sleep(poll_interval)
        return False

    def _build_envelope(self, method: str, params: Optional[dict]) -> dict:
        return {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": next(self._id_counter),
        }

    def _parse_response(self, raw: bytes) -> dict:
        return json.loads(raw.decode("utf-8"))

    def call(self, method: str, params: Optional[dict] = None) -> dict:
        """Send a JSON-RPC method call. Returns the parsed response dict."""
        envelope = self._build_envelope(method, params)
        body = json.dumps(envelope).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.URLError as e:
            raise RuntimeError(f"RPC call to {self.url} failed: {e}") from e
        try:
            return self._parse_response(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Invalid JSON response: {raw[:200]!r}") from e

    def eval_js(self, script: str) -> dict:
        """Convenience: execute a JavaScript snippet via ``eval``."""
        return self.call("eval", {"script": script})
