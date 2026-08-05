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

        # -- texture sets / baking / events (project-setup ops) ----------
        self.resolutions = []          # [(set_name, (w, h)), ...]
        self.baking_writes = []        # [(set_name, url), ...]
        self.subscriptions = []        # [(event, callback), ...]
        self.disconnections = []       # [(event, callback), ...]
        self.mesh_map_writes = []      # [(set_name, usage, resource_id), ...]
        self.imported_resources = []   # [path, ...]
        #: Key Painter uses for the high-poly entry in ``common()``. Tests
        #: rewrite it to prove the op matches case-insensitively.
        self.high_poly_key = "HipolyMesh"

        class Resolution:
            def __init__(self, width, height):
                self.width, self.height = width, height

        class FakeTextureSet:
            def __init__(self, name):
                self._name = name

            def name(self):
                return self._name

            def set_resolution(self, resolution):
                outer.resolutions.append(
                    (self._name, (resolution.width, resolution.height))
                )

            def set_mesh_map_resource(self, usage, resource_id):
                outer.mesh_map_writes.append((self._name, usage, resource_id))

        class MeshMapUsage:
            # Deliberately NOT an exhaustive list: the op has to resolve our
            # usage keys against whatever a given Painter exposes, and skip
            # the ones it doesn't (Curvature is absent here on purpose).
            AmbientOcclusion = "AmbientOcclusion"
            Normal = "Normal"
            Thickness = "Thickness"

        textureset = types.ModuleType("substance_painter.textureset")
        textureset.Resolution = Resolution
        textureset.MeshMapUsage = MeshMapUsage
        textureset.all_texture_sets = lambda: [
            FakeTextureSet("body"), FakeTextureSet("props")
        ]

        class _Imported:
            def __init__(self, path):
                self._path = path

            def identifier(self):
                return f"resource::{os.path.basename(self._path)}"

        class _Usage:
            TEXTURE = "TEXTURE"

        resource = types.ModuleType("substance_painter.resource")
        resource.Usage = _Usage

        def _import_project_resource(path, usage, name):
            outer.imported_resources.append(path)
            return _Imported(path)

        resource.import_project_resource = _import_project_resource

        class _Property:
            """Stand-in for Painter's opaque baking Property handle."""

            def __init__(self, texture_set, key):
                self.texture_set, self.key = texture_set, key

        class BakingParameters:
            def __init__(self, texture_set):
                self.texture_set = texture_set

            @staticmethod
            def from_texture_set(texture_set):
                return BakingParameters(texture_set)

            def common(self):
                return {
                    outer.high_poly_key: _Property(
                        self.texture_set.name(), outer.high_poly_key
                    ),
                    "OutputSize": _Property(self.texture_set.name(), "OutputSize"),
                }

            @staticmethod
            def set(mapping):
                for prop, value in mapping.items():
                    outer.baking_writes.append((prop.texture_set, value))

        baking = types.ModuleType("substance_painter.baking")
        baking.BakingParameters = BakingParameters

        class _Dispatcher:
            @staticmethod
            def connect(event, callback):
                outer.subscriptions.append((event, callback))

            @staticmethod
            def disconnect(event, callback):
                outer.disconnections.append((event, callback))
                outer.subscriptions.remove((event, callback))

        event = types.ModuleType("substance_painter.event")
        event.DISPATCHER = _Dispatcher
        event.ProjectEditionEntered = "ProjectEditionEntered"
        event.ProjectOpened = "ProjectOpened"

        root = types.ModuleType("substance_painter")
        root.project = project
        root.js = js
        root.textureset = textureset
        root.resource = resource
        root.baking = baking
        root.event = event

        self.modules = {
            "substance_painter": root,
            "substance_painter.project": project,
            "substance_painter.js": js,
            "substance_painter.textureset": textureset,
            "substance_painter.resource": resource,
            "substance_painter.baking": baking,
            "substance_painter.event": event,
        }

    def fire_project_ready(self):
        """Invoke every callback the plugin registered for project-open."""
        self.project_open = True
        for _event, callback in self.subscriptions:
            callback()


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


class TestProjectSetupOps(unittest.TestCase):
    """Resolution + high-poly ops, including the deferral that makes them
    work on a fresh launch (dispatched while the New Project wizard is
    still open, applied once the project exists)."""

    @classmethod
    def setUpClass(cls):
        os.environ["SUBSTANCE_RPC_DISABLE_MAIN_THREAD"] = "1"
        cls.port = _free_port()
        substance_rpc.start_server(port=cls.port)
        cls.client = PainterRpcClient(port=cls.port, timeout=5.0)

    @classmethod
    def tearDownClass(cls):
        substance_rpc.stop_server()
        os.environ.pop("SUBSTANCE_RPC_DISABLE_MAIN_THREAD", None)

    def setUp(self):
        from substance_rpc.ops import setup_ops

        self.setup_ops = setup_ops
        # Module-level pending state, the one-shot listener flag and the
        # recorded subscription all survive between tests; each case needs a
        # clean slate. Leaving ``_subscription`` behind would point a later
        # ``teardown()`` at the PREVIOUS case's fake dispatcher.
        setup_ops._pending.update(resolution=None, high_poly=None)
        setup_ops._listener_connected = False
        setup_ops._subscription = None
        self.addCleanup(
            lambda: setup_ops._pending.update(resolution=None, high_poly=None)
        )

        self.painter = _FakePainter()
        sys.modules.update(self.painter.modules)
        for name in self.painter.modules:
            self.addCleanup(sys.modules.pop, name, None)

    def _high_poly_file(self):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix="_high.fbx", delete=False) as fh:
            path = fh.name
        self.addCleanup(os.unlink, path)
        return path

    # -- registration ---------------------------------------------------

    def test_ops_are_registered(self):
        ops = self.client.invoke("system.list_ops")
        for expected in ("project.set_resolution", "bake.set_high_poly",
                         "bake.pending_setup"):
            self.assertIn(expected, ops)

    # -- resolution -----------------------------------------------------

    def test_resolution_applies_to_every_texture_set(self):
        result = self.client.invoke("project.set_resolution", size=4096)
        self.assertTrue(result["applied"])
        self.assertEqual(
            self.painter.resolutions,
            [("body", (4096, 4096)), ("props", (4096, 4096))],
        )

    def test_zero_resolution_is_a_no_op(self):
        result = self.client.invoke("project.set_resolution", size=0)
        self.assertFalse(result["applied"])
        self.assertTrue(result["skipped"])
        self.assertEqual(self.painter.resolutions, [])

    def test_resolution_defers_until_a_project_opens(self):
        self.painter.project_open = False
        result = self.client.invoke("project.set_resolution", size=2048)
        self.assertFalse(result["applied"])
        self.assertTrue(result["deferred"])
        self.assertEqual(self.painter.resolutions, [])

        self.painter.fire_project_ready()
        self.assertEqual(
            self.painter.resolutions,
            [("body", (2048, 2048)), ("props", (2048, 2048))],
        )

    def test_pending_value_is_consumed_not_replayed(self):
        # A second project must not silently inherit the first one's setup.
        self.painter.project_open = False
        self.client.invoke("project.set_resolution", size=1024)
        self.painter.fire_project_ready()
        self.painter.resolutions.clear()
        self.painter.fire_project_ready()
        self.assertEqual(self.painter.resolutions, [])

    def test_last_write_wins_while_pending(self):
        self.painter.project_open = False
        self.client.invoke("project.set_resolution", size=1024)
        self.client.invoke("project.set_resolution", size=8192)
        self.painter.fire_project_ready()
        self.assertEqual(
            [size for _name, size in self.painter.resolutions],
            [(8192, 8192), (8192, 8192)],
        )

    # -- high poly ------------------------------------------------------

    def test_high_poly_sets_every_texture_set(self):
        path = self._high_poly_file()
        result = self.client.invoke("bake.set_high_poly", mesh_path=path)
        self.assertTrue(result["applied"])
        self.assertEqual(
            [name for name, _url in self.painter.baking_writes], ["body", "props"]
        )
        for _name, url in self.painter.baking_writes:
            self.assertTrue(url.startswith("file:///"), url)
            self.assertIn(os.path.basename(path), url)

    def test_high_poly_key_matched_case_insensitively(self):
        # Painter has never guaranteed the spelling; a differently-cased
        # key must still resolve rather than raise.
        self.painter.high_poly_key = "hipolyMesh"
        path = self._high_poly_file()
        self.client.invoke("bake.set_high_poly", mesh_path=path)
        self.assertEqual(len(self.painter.baking_writes), 2)

    def test_unknown_high_poly_key_reports_what_painter_offers(self):
        self.painter.high_poly_key = "SomethingElse"
        path = self._high_poly_file()
        with self.assertRaises(RuntimeError) as ctx:
            self.client.invoke("bake.set_high_poly", mesh_path=path)
        self.assertIn("SomethingElse", str(ctx.exception))

    def test_high_poly_missing_file_errors(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.client.invoke("bake.set_high_poly", mesh_path="C:/nope/x_high.fbx")
        self.assertIn("not found", str(ctx.exception))

    def test_empty_high_poly_is_a_no_op(self):
        result = self.client.invoke("bake.set_high_poly", mesh_path="")
        self.assertTrue(result["skipped"])
        self.assertEqual(self.painter.baking_writes, [])

    def test_high_poly_defers_until_a_project_opens(self):
        self.painter.project_open = False
        path = self._high_poly_file()
        result = self.client.invoke("bake.set_high_poly", mesh_path=path)
        self.assertFalse(result["applied"])
        self.assertTrue(result["deferred"])
        self.assertEqual(self.painter.baking_writes, [])

        self.painter.fire_project_ready()
        self.assertEqual(len(self.painter.baking_writes), 2)

    def test_deferral_prefers_the_edition_entered_event(self):
        # Baking parameters only exist once the project is editable.
        self.painter.project_open = False
        self.client.invoke("project.set_resolution", size=512)
        self.assertEqual(
            [event for event, _cb in self.painter.subscriptions],
            ["ProjectEditionEntered"],
        )

    def test_pending_setup_reports_queued_values(self):
        self.painter.project_open = False
        self.client.invoke("project.set_resolution", size=2048)
        pending = self.client.invoke("bake.pending_setup")
        self.assertEqual(pending["resolution"], 2048)
        self.assertIsNone(pending["high_poly"])
        self.assertTrue(pending["listener"])

    # -- per-texture-set mesh maps --------------------------------------

    def _manifest(self, mesh_maps):
        """Write a manifest whose mesh-map paths all exist on disk."""
        import json
        import tempfile

        resolved = {}
        for material, usages in mesh_maps.items():
            resolved[material] = {}
            for usage, name in usages.items():
                with tempfile.NamedTemporaryFile(
                    suffix=f"_{name}.png", delete=False
                ) as fh:
                    path = fh.name
                self.addCleanup(os.unlink, path)
                resolved[material][usage] = path
        with tempfile.NamedTemporaryFile(
            suffix=".materials.json", delete=False, mode="w", encoding="utf-8"
        ) as fh:
            json.dump({"mesh_maps": resolved}, fh)
            manifest = fh.name
        self.addCleanup(os.unlink, manifest)
        return manifest, resolved

    def test_mesh_maps_land_on_the_matching_texture_set_only(self):
        # The whole point: --mesh-map applies to every set, this doesn't.
        manifest, resolved = self._manifest(
            {"body": {"ambient_occlusion": "AO"}, "nonexistent": {"normal": "Normal"}}
        )
        result = self.client.invoke("textures.apply_mesh_maps", manifest_path=manifest)
        self.assertTrue(result["applied"])
        self.assertEqual(result["texture_sets"], {"body": ["ambient_occlusion"]})
        self.assertEqual(
            [(name, usage) for name, usage, _rid in self.painter.mesh_map_writes],
            [("body", "AmbientOcclusion")],
        )
        self.assertEqual(
            self.painter.imported_resources,
            [resolved["body"]["ambient_occlusion"]],
        )

    def test_texture_set_without_an_entry_is_untouched(self):
        manifest, _ = self._manifest({"body": {"normal": "Normal"}})
        self.client.invoke("textures.apply_mesh_maps", manifest_path=manifest)
        self.assertEqual(
            [name for name, _usage, _rid in self.painter.mesh_map_writes], ["body"]
        )

    def test_resource_usage_is_resolved_not_hard_coded(self):
        # A build that spells the enum member differently must still import;
        # a hard-coded ``Usage.TEXTURE`` would AttributeError mid-assignment.
        painter_resource = sys.modules["substance_painter.resource"]
        del painter_resource.Usage.TEXTURE
        painter_resource.Usage.MESH_MAP = "MESH_MAP"
        self.addCleanup(setattr, painter_resource.Usage, "TEXTURE", "TEXTURE")
        self.addCleanup(delattr, painter_resource.Usage, "MESH_MAP")

        manifest, _ = self._manifest({"body": {"ambient_occlusion": "AO"}})
        result = self.client.invoke("textures.apply_mesh_maps", manifest_path=manifest)
        self.assertTrue(result["applied"])

    def test_no_usable_resource_usage_reports_what_exists(self):
        painter_resource = sys.modules["substance_painter.resource"]
        del painter_resource.Usage.TEXTURE
        painter_resource.Usage.SOMETHING_ELSE = "SOMETHING_ELSE"
        self.addCleanup(setattr, painter_resource.Usage, "TEXTURE", "TEXTURE")
        self.addCleanup(delattr, painter_resource.Usage, "SOMETHING_ELSE")

        manifest, _ = self._manifest({"body": {"normal": "Normal"}})
        with self.assertRaises(RuntimeError) as ctx:
            self.client.invoke("textures.apply_mesh_maps", manifest_path=manifest)
        self.assertIn("somethingelse", str(ctx.exception).lower())

    def test_usage_unknown_to_this_painter_is_skipped_not_fatal(self):
        manifest, _ = self._manifest({"body": {"curvature": "Curvature"}})
        # The fake exposes no Curvature member.
        result = self.client.invoke("textures.apply_mesh_maps", manifest_path=manifest)
        self.assertFalse(result["applied"])
        self.assertEqual(self.painter.mesh_map_writes, [])

    def test_missing_map_file_is_skipped_not_fatal(self):
        import json
        import tempfile

        with tempfile.NamedTemporaryFile(
            suffix=".materials.json", delete=False, mode="w", encoding="utf-8"
        ) as fh:
            json.dump(
                {"mesh_maps": {"body": {"normal": "C:/nope/x_Normal.png"}}}, fh
            )
            manifest = fh.name
        self.addCleanup(os.unlink, manifest)
        result = self.client.invoke("textures.apply_mesh_maps", manifest_path=manifest)
        self.assertFalse(result["applied"])
        self.assertEqual(self.painter.mesh_map_writes, [])

    def test_manifest_without_a_wiring_section_is_a_no_op(self):
        manifest, _ = self._manifest({})
        result = self.client.invoke("textures.apply_mesh_maps", manifest_path=manifest)
        self.assertFalse(result["applied"])

    def test_missing_manifest_errors(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.client.invoke(
                "textures.apply_mesh_maps", manifest_path="C:/nope/a.json"
            )
        self.assertIn("not found", str(ctx.exception))

    def test_empty_manifest_path_is_a_no_op(self):
        result = self.client.invoke("textures.apply_mesh_maps", manifest_path="")
        self.assertTrue(result["skipped"])

    def test_mesh_maps_defer_until_a_project_opens(self):
        self.painter.project_open = False
        manifest, _ = self._manifest({"body": {"ambient_occlusion": "AO"}})
        result = self.client.invoke("textures.apply_mesh_maps", manifest_path=manifest)
        self.assertTrue(result["deferred"])
        self.assertEqual(self.painter.mesh_map_writes, [])

        self.painter.fire_project_ready()
        self.assertEqual(
            [name for name, _usage, _rid in self.painter.mesh_map_writes], ["body"]
        )

    def test_one_failing_knob_does_not_block_the_others(self):
        # All three deferred knobs replay in one callback; an exception in
        # the middle must not swallow the rest.
        self.painter.project_open = False
        self.client.invoke("project.set_resolution", size=1024)
        self.client.invoke(
            "textures.apply_mesh_maps",
            manifest_path=self._manifest({"body": {"ambient_occlusion": "AO"}})[0],
        )
        self.painter.high_poly_key = "SomethingElse"  # makes high poly raise
        self.client.invoke(
            "bake.set_high_poly", mesh_path=self._high_poly_file()
        )

        self.painter.fire_project_ready()  # must not raise
        self.assertTrue(self.painter.resolutions)
        self.assertTrue(self.painter.mesh_map_writes)

    # -- teardown -------------------------------------------------------

    def test_teardown_unsubscribes_and_drops_pending(self):
        # A disabled plugin must stop rewriting the next project that opens.
        self.painter.project_open = False
        self.client.invoke("project.set_resolution", size=2048)
        self.setup_ops.teardown()

        self.assertEqual(
            self.painter.disconnections,
            [("ProjectEditionEntered", self.setup_ops._on_project_ready)],
        )
        pending = self.client.invoke("bake.pending_setup")
        self.assertIsNone(pending["resolution"])
        self.assertFalse(pending["listener"])

    def test_teardown_is_safe_when_nothing_was_armed(self):
        self.setup_ops.teardown()  # must not raise
        self.assertEqual(self.painter.disconnections, [])

    def test_close_plugin_tears_the_listener_down(self):
        # The lifecycle hook Painter calls on disable owns the cleanup --
        # a caller shouldn't have to know setup_ops exists.
        self.painter.project_open = False
        self.client.invoke("bake.set_high_poly", mesh_path=self._high_poly_file())
        self.assertTrue(self.setup_ops._listener_connected)

        substance_rpc.close_plugin()
        self.addCleanup(substance_rpc.start_server, port=self.port)

        self.assertFalse(self.setup_ops._listener_connected)
        self.assertEqual(self.setup_ops._pending["high_poly"], None)


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
