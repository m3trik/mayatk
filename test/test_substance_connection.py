# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.mat_utils.substance_bridge.connection.

No Maya runtime required -- covers SubstanceConnection and PainterRpcClient
against an in-process stub that speaks the substance_rpc plugin's wire
format (``GET /health`` + ``POST / {"op", "kwargs"}``), and instance records
written under a ``ptk.TempArtifacts`` folder (``PYTHONTK_CACHE_ROOT``), never
the user's cache. The real plugin server + ops are covered in
test_substance_rpc_plugin.py.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# Ensure package is importable when running standalone
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pythontk as ptk  # noqa: E402

from mayatk.mat_utils.substance_bridge.connection import SubstanceConnection  # noqa: E402
from mayatk.mat_utils.substance_bridge.substance_rpc import Installer, PainterRpcClient  # noqa: E402

_CACHE_ENV = "PYTHONTK_CACHE_ROOT"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base_test import PainterPluginsSandbox  # noqa: E402

_PLUGINS = PainterPluginsSandbox()


def setUpModule():
    _PLUGINS.start()


def tearDownModule():
    _PLUGINS.stop()


class _StubPluginHandler(BaseHTTPRequestHandler):
    """Minimal double of the substance_rpc plugin server: echoes the op, and
    answers ``script.status`` as a finished job."""

    #: The token a request must carry (``None``: any).
    token = None

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._respond(200, {"ok": True, "value": "alive"})
        else:
            self._respond(404, {"ok": False, "error": "not found"})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        if self.token and self.headers.get("Authorization") != f"Bearer {self.token}":
            self._respond(401, {"ok": False, "error": "no token"})
            return
        op, kwargs = body.get("op"), body.get("kwargs") or {}
        if op == "script.start":
            value = {"job": "j1", "state": "queued"}
        elif op == "script.status":
            value = {"job": kwargs.get("job"), "state": "done", "report": ["made it"]}
        else:
            value = {"echoed_op": op, "echoed_kwargs": kwargs}
        self._respond(200, {"ok": True, "value": value})

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
    """Spin the stub plugin server up/down around each test, with the
    instance records in a test folder."""

    def setUp(self):
        temp = ptk.TempArtifacts("subconn", policy="scoped")
        self.addCleanup(temp.cleanup)
        before = os.environ.get(_CACHE_ENV)
        os.environ[_CACHE_ENV] = str(temp.dir_path())
        self.addCleanup(lambda: os.environ.__setitem__(_CACHE_ENV, before) if before is not None else os.environ.pop(_CACHE_ENV, None))

    def _start_stub(self, token=None):
        port = _free_port()
        handler = type("_Handler", (_StubPluginHandler,), {"token": token})
        server = HTTPServer(("127.0.0.1", port), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return port

    def _record(self, port, pid=None, token=None, project=None, rid=None):
        record = {
            "id": rid or f"rec{port}",
            "label": PainterRpcClient.LABEL,
            "pid": pid or os.getpid(),
            "host": "127.0.0.1",
            "port": port,
            "token": token,
            "started": 0,
            "state": {"project": project},
        }
        ptk.RpcInstances.write(record)
        return record


class TestPainterRpcClient(_StubServerMixin, unittest.TestCase):
    def test_ping_returns_false_for_closed_port(self):
        self.assertFalse(PainterRpcClient(port=_free_port()).ping(timeout=0.5))

    def test_reload_mesh_routes_to_mesh_reload_op(self):
        client = PainterRpcClient(port=self._start_stub(), timeout=5.0)
        value = client.reload_mesh("C:/tmp/scene.fbx")
        self.assertEqual(value["echoed_op"], "mesh.reload")
        self.assertEqual(
            value["echoed_kwargs"],
            {"mesh_path": "C:/tmp/scene.fbx", "preserve_strokes": True, "import_cameras": False},
        )

    def test_a_script_runs_as_a_job_and_is_waited_for(self):
        client = PainterRpcClient(port=self._start_stub(), timeout=5.0)
        job = client.run_script("x = 1", path="a.py", args={"K": 1})
        self.assertEqual(job, "j1")
        seen = []
        status = client.wait(job, timeout=5, poll=0.01, on_status=seen.append)
        self.assertEqual(status["state"], "done")
        self.assertEqual(seen[-1]["report"], ["made it"])

    def test_painters_are_read_from_their_records_and_held_projects_found(self):
        port = self._start_stub()
        mine = self._record(port, project={"file_path": "C:/p/a.spp"})
        self._record(port + 1, project=None)
        self.assertEqual({r["id"] for r in PainterRpcClient.painters()}, {mine["id"], f"rec{port + 1}"})
        self.assertEqual(PainterRpcClient.holding("c:\\p\\A.spp" if os.name == "nt" else "C:/p/a.spp")["id"], mine["id"])
        self.assertIsNone(PainterRpcClient.holding("C:/p/b.spp"))

    def test_a_client_made_from_a_record_carries_its_token(self):
        port = self._start_stub(token="t0k")
        record = self._record(port, token="t0k")
        self.assertEqual(PainterRpcClient.for_instance(record).invoke("project.info")["echoed_op"], "project.info")
        with self.assertRaises(RuntimeError):
            PainterRpcClient(port=port).invoke("project.info")


class TestSubstanceConnection(_StubServerMixin, unittest.TestCase):
    def test_attach_to_a_painter_that_no_longer_answers_raises(self):
        with self.assertRaises(ConnectionRefusedError):
            SubstanceConnection.attach(self._record(_free_port()), log_path="")

    def test_attach_by_record(self):
        record = self._record(self._start_stub(), project={"name": "a", "uuid": "u", "file_path": "C:/p/a.spp"})
        conn = SubstanceConnection.attach(record, log_path="")
        try:
            self.assertTrue(conn.is_alive())
            self.assertFalse(conn.launched, "an attached Painter is never ended")
            self.assertIs(conn.record, record)
        finally:
            conn.close(terminate=True)

    def test_a_launch_is_found_by_its_own_pid(self):
        """The launched process's record, not another Painter's that answers."""
        port = self._start_stub()
        self._record(port, pid=os.getpid())  # another Painter, serving
        from base_test import child_python  # a Python, not maya.exe, under a GUI-hosted run

        stand_in = subprocess.Popen([child_python(), "-c", "import time; time.sleep(30)"])
        self.addCleanup(stand_in.wait, 10)
        self.addCleanup(stand_in.kill)
        conn = SubstanceConnection(log_path="")
        conn.process = stand_in
        self.assertFalse(conn.wait_ready(timeout=0.5, poll=0.05))
        self._record(port, pid=stand_in.pid, rid="own")
        self.assertTrue(conn.wait_ready(timeout=5, poll=0.05))
        self.assertEqual(conn.record["pid"], stand_in.pid)

    def test_a_launch_loads_the_plugin_from_the_launch_root(self):
        env = SubstanceConnection(gui=False, log_path="").launch_env()
        root = Path(env["SUBSTANCE_PAINTER_PLUGINS_PATH"].split(os.pathsep)[0])
        self.assertEqual(root, Installer.launch_root())
        self.assertTrue((root / "startup" / "substance_rpc_launched" / "__init__.py").is_file())
        self.assertEqual(env["QT_QPA_PLATFORM"], "offscreen")
        self.assertNotEqual(os.environ.get("SUBSTANCE_PAINTER_PLUGINS_PATH"), env["SUBSTANCE_PAINTER_PLUGINS_PATH"], "this process's environment is left alone")

    def test_the_launch_roots_copy_has_its_own_name(self):
        """Painter puts a person's plugins folder ahead of the launch root, so a
        copy named ``substance_rpc`` there was shadowed by their install (and,
        unticked, the launched Painter never served). The old copy goes."""
        stale = Installer.launch_root() / "startup" / "substance_rpc"
        stale.mkdir(parents=True, exist_ok=True)
        (stale / "__init__.py").write_text("", encoding="utf-8")
        root = Installer.launch_root()
        self.assertEqual(sorted(p.name for p in (root / "startup").iterdir()), ["substance_rpc_launched"])

    def test_a_set_startup_list_starts_the_launched_copy_first(self):
        env = Installer.launch_env({"SUBSTANCE_PAINTER_STARTUP_PLUGINS": "painter_plugins_ui:other"})
        self.assertEqual(env["SUBSTANCE_PAINTER_STARTUP_PLUGINS"], "substance_rpc_launched:painter_plugins_ui:other")


if __name__ == "__main__":
    unittest.main()
