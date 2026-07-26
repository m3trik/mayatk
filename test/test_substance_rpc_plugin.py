# !/usr/bin/python
# coding=utf-8
"""Tests for the Painter-side substance_rpc plugin.

No Painter (or Maya) runtime required — the real plugin package
(``substance_bridge/substance_rpc/plugin_src/substance_rpc``) is
imported off sys.path exactly as Painter's plugin loader would, its
HTTP server is started in-process, and ``substance_painter`` is faked in
``sys.modules`` so the project ops execute their real code paths.

Covers the full reimport transport end-to-end:
PainterRpcClient -> HTTP -> server dispatch -> registry -> mesh.reload
-> (fake) substance_painter.project.reload_mesh -> status callback.
"""
import os
import socket
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_PLUGIN_SRC = (
    Path(__file__).resolve().parent.parent
    / "mayatk"
    / "mat_utils"
    / "substance_bridge"
    / "substance_rpc"
    / "plugin_src"
)
sys.path.insert(0, str(_PLUGIN_SRC))

import substance_rpc  # noqa: E402  -- the plugin package itself

from mayatk.mat_utils.substance_bridge.substance_rpc import (  # noqa: E402
    PainterRpcClient,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _FakePainter:
    """Builds fake ``substance_painter`` modules and tracks calls."""

    def __init__(self):
        self.reload_calls = []
        self.project_open = True
        self.reload_succeeds = True

        project = types.ModuleType("substance_painter.project")

        class MeshReloadingSettings:
            def __init__(self, import_cameras=False, preserve_strokes=True):
                self.import_cameras = import_cameras
                self.preserve_strokes = preserve_strokes

        class ReloadMeshStatus:
            SUCCESS = "SUCCESS"
            FAILURE = "FAILURE"

        outer = self

        def is_open():
            return outer.project_open

        def reload_mesh(mesh_path, settings, on_done):
            outer.reload_calls.append((mesh_path, settings))
            on_done(
                ReloadMeshStatus.SUCCESS
                if outer.reload_succeeds
                else ReloadMeshStatus.FAILURE
            )

        project.MeshReloadingSettings = MeshReloadingSettings
        project.ReloadMeshStatus = ReloadMeshStatus
        project.is_open = is_open
        project.reload_mesh = reload_mesh
        project.file_path = lambda: "C:/projects/test.spp"
        project.needs_saving = lambda: False

        js = types.ModuleType("substance_painter.js")
        js.evaluate = lambda script: f"js:{script}"

        root = types.ModuleType("substance_painter")
        root.project = project
        root.js = js

        self.modules = {
            "substance_painter": root,
            "substance_painter.project": project,
            "substance_painter.js": js,
        }


class TestSubstanceRpcPlugin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Direct-call the ops instead of QTimer-marshalling (no pumped
        # Qt event loop in the test process -- marshalling would hang).
        os.environ["SUBSTANCE_RPC_DISABLE_MAIN_THREAD"] = "1"
        cls.port = _free_port()
        substance_rpc.start_server(port=cls.port)
        cls.client = PainterRpcClient(port=cls.port, timeout=5.0)

    @classmethod
    def tearDownClass(cls):
        substance_rpc.stop_server()
        os.environ.pop("SUBSTANCE_RPC_DISABLE_MAIN_THREAD", None)

    def setUp(self):
        self.painter = _FakePainter()
        sys.modules.update(self.painter.modules)
        for name in self.painter.modules:
            self.addCleanup(sys.modules.pop, name, None)

    # -- transport ------------------------------------------------------

    def test_health_and_ping_op(self):
        self.assertTrue(self.client.ping(timeout=2.0))
        self.assertEqual(self.client.invoke("system.ping"), "pong")

    def test_list_ops_covers_reimport_surface(self):
        ops = self.client.invoke("system.list_ops")
        for expected in ("mesh.reload", "mesh.reload_status", "project.info",
                         "js.evaluate", "system.eval"):
            self.assertIn(expected, ops)

    def test_unknown_op_raises(self):
        with self.assertRaises(RuntimeError):
            self.client.invoke("no.such_op")

    def test_describe_returns_signatures(self):
        desc = self.client.describe("mesh.reload")
        self.assertEqual(desc["name"], "mesh.reload")
        param_names = [p["name"] for p in desc["params"]]
        self.assertIn("mesh_path", param_names)

    # -- ops ------------------------------------------------------------

    def test_mesh_reload_happy_path(self):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".fbx", delete=False) as fh:
            mesh_path = fh.name
        self.addCleanup(os.unlink, mesh_path)

        value = self.client.reload_mesh(mesh_path, preserve_strokes=True)
        self.assertTrue(value["started"])
        # The fake reload completed synchronously via its callback.
        status = self.client.reload_status()
        self.assertEqual(status["status"], "success")
        self.assertEqual(status["mesh_path"], mesh_path)
        # Settings made it through with the right knobs.
        (called_path, settings), = self.painter.reload_calls
        self.assertEqual(called_path, mesh_path)
        self.assertTrue(settings.preserve_strokes)
        self.assertFalse(settings.import_cameras)

    def test_mesh_reload_missing_file_errors(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.client.reload_mesh("C:/definitely/not/here.fbx")
        self.assertIn("not found", str(ctx.exception))

    def test_mesh_reload_without_open_project_errors(self):
        import tempfile

        self.painter.project_open = False
        with tempfile.NamedTemporaryFile(suffix=".fbx", delete=False) as fh:
            mesh_path = fh.name
        self.addCleanup(os.unlink, mesh_path)
        with self.assertRaises(RuntimeError) as ctx:
            self.client.reload_mesh(mesh_path)
        self.assertIn("No project is open", str(ctx.exception))

    def test_js_evaluate_routes_to_painter_js(self):
        self.assertEqual(
            self.client.eval_js("alg.log('hi')"), "js:alg.log('hi')"
        )

    def test_eval_py_returns_result_variable(self):
        self.assertEqual(self.client.eval_py("result = 40 + 2"), 42)

    def test_project_info(self):
        info = self.client.project_info()
        self.assertTrue(info["is_open"])
        self.assertEqual(info["file_path"], "C:/projects/test.spp")


class TestPluginImportSafety(unittest.TestCase):
    def test_import_binds_no_port(self):
        # The package was imported at module scope above; the server must
        # only run because setUpClass started it explicitly. autostart()
        # outside Painter is a no-op.
        self.assertIsNone(substance_rpc.autostart())

    def test_lifecycle_hooks_exist(self):
        # Painter's loader calls these on enable/disable.
        self.assertTrue(callable(substance_rpc.start_plugin))
        self.assertTrue(callable(substance_rpc.close_plugin))


if __name__ == "__main__":
    unittest.main()
