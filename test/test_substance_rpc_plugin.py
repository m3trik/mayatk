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
-> (fake) substance_painter.project.reload_mesh -> status callback; the
script runner (``script.*``); and the instance record the plugin announces,
written under a ``ptk.TempArtifacts`` folder (``PYTHONTK_CACHE_ROOT``), never
the user's cache.
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

import pythontk as ptk  # noqa: E402

_RECORDS = ptk.TempArtifacts("rpcrec", policy="scoped")  # short: records sit deep under it
_CACHE_ENV = "PYTHONTK_CACHE_ROOT"
_cache_before = os.environ.get(_CACHE_ENV)


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base_test import PainterPluginsSandbox  # noqa: E402

_PLUGINS = PainterPluginsSandbox()


def setUpModule():
    os.environ[_CACHE_ENV] = str(_RECORDS.dir_path())
    _PLUGINS.start()


def tearDownModule():
    _PLUGINS.stop()
    if _cache_before is None:
        os.environ.pop(_CACHE_ENV, None)
    else:
        os.environ[_CACHE_ENV] = _cache_before
    _RECORDS.cleanup()


from mayatk.mat_utils.substance_bridge.substance_rpc import PainterRpcClient  # noqa: E402


class _LiveTokenClient(PainterRpcClient):
    """A client sending the plugin's CURRENT token: each serving gets a new
    one, and a test that stops and restarts the server keeps its client."""

    module = None

    @property
    def token(self):
        return self.module.PLUGIN.token

    @token.setter
    def token(self, _value):
        pass


def _client(module=None, port=None):
    """A client of the served plugin, carrying its instance's token."""
    module = module or substance_rpc
    client = type("_Client", (_LiveTokenClient,), {"module": module})
    return client(port=port or module.PLUGIN.address[1], timeout=5.0)

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
        project.file_path = lambda: outer.file_path
        project.needs_saving = lambda: False
        project.name = lambda: "test"
        project.get_uuid = lambda: outer.uuid
        project.last_imported_mesh_path = lambda: "C:/meshes/test.fbx"
        project.is_busy = lambda: outer.busy
        self.file_path = "C:/projects/test.spp"
        self.uuid = "11111111-2222-3333-4444-555555555555"
        self.busy = False
        #: Callbacks held while Painter is busy (``execute_when_not_busy``).
        self.held = []

        def execute_when_not_busy(fn):
            if outer.busy:
                outer.held.append(fn)
            else:
                fn()

        project.execute_when_not_busy = execute_when_not_busy
        #: What project.create does: Painter goes on loading after it returns.
        project.start_loading = lambda: setattr(outer, "busy", True)

        layerstack = types.ModuleType("substance_painter.layerstack")
        self.undo_scopes = []

        class ScopedModification:
            def __init__(self, name):
                self.name = name

            def __enter__(self):
                outer.undo_scopes.append(self.name)
                return self

            def __exit__(self, *exc):
                return False

        layerstack.ScopedModification = ScopedModification

        js = types.ModuleType("substance_painter.js")
        js.evaluate = lambda script: f"js:{script}"

        # -- texture sets / baking / events (project-setup ops) ----------
        self.resolutions = []  # [(set_name, (w, h)), ...]
        self.baking_writes = []  # [(set_name, url), ...]
        self.subscriptions = []  # [(event, callback), ...]
        self.disconnections = []  # [(event, callback), ...]
        self.mesh_map_writes = []  # [(set_name, usage, resource_id), ...]
        self.deprecation_warnings = []  # one entry per deprecated call
        self.imported_resources = []  # [path, ...]
        #: Key Painter uses for the high-poly entry in ``common()``. Tests
        #: rewrite it to prove the op matches case-insensitively.
        self.high_poly_key = "HipolyMesh"

        class Resolution:
            def __init__(self, width, height):
                self.width, self.height = width, height

        class _CallableName(str):
            """Painter 10.1+'s ``make_callable``: a str that warns when called."""

            def __call__(self):
                outer.deprecation_warnings.append("TextureSet.name()")
                return self

        class FakeTextureSet:
            def __init__(self, name):
                self._name = name

            @property
            def name(self):
                return _CallableName(self._name)

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
            FakeTextureSet("body"),
            FakeTextureSet("props"),
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
                        str(self.texture_set.name), outer.high_poly_key
                    ),
                    "OutputSize": _Property(str(self.texture_set.name), "OutputSize"),
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
        root.layerstack = layerstack

        self.modules = {
            "substance_painter": root,
            "substance_painter.project": project,
            "substance_painter.js": js,
            "substance_painter.textureset": textureset,
            "substance_painter.resource": resource,
            "substance_painter.baking": baking,
            "substance_painter.event": event,
            "substance_painter.layerstack": layerstack,
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
        cls.client = _client(port=cls.port)

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
        for expected in (
            "mesh.reload",
            "mesh.reload_status",
            "project.info",
            "script.start",
            "script.status",
            "script.cancel",
        ):
            self.assertIn(expected, ops)
        # One way in to run code: the runner. No op evaluates a string.
        self.assertNotIn("system.eval", ops)
        self.assertNotIn("js.evaluate", ops)

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
        ((called_path, settings),) = self.painter.reload_calls
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

    def test_project_info(self):
        info = self.client.project_info()
        self.assertTrue(info["is_open"])
        self.assertEqual(info["file_path"], "C:/projects/test.spp")
        self.assertEqual(info["uuid"], self.painter.uuid)
        self.assertEqual(info["name"], "test")


class TestScriptRunner(unittest.TestCase):
    """``script.*``: a script runs in Painter as a job, through the wire."""

    @classmethod
    def setUpClass(cls):
        os.environ["SUBSTANCE_RPC_DISABLE_MAIN_THREAD"] = "1"
        substance_rpc.start_server(port=_free_port())
        cls.client = _client()

    @classmethod
    def tearDownClass(cls):
        substance_rpc.stop_server()
        os.environ.pop("SUBSTANCE_RPC_DISABLE_MAIN_THREAD", None)

    def setUp(self):
        self.painter = _FakePainter()
        sys.modules.update(self.painter.modules)
        for name in self.painter.modules:
            self.addCleanup(sys.modules.pop, name, None)

    def _run(self, source, **kwargs):
        job = self.client.run_script(source, path="probe.py", label="Probe", **kwargs)
        return self.client.wait(job, timeout=10, poll=0.05)

    def test_a_script_gets_its_args_and_reports_back(self):
        status = self._run(
            "print('working')\nargs.report('made ' + args['NAME'])\nargs.progress(1.0, 'all')",
            args={"NAME": "it"},
        )
        self.assertEqual(status["state"], "done", status)
        self.assertEqual(status["report"], ["made it"])
        self.assertEqual(status["console"], ["working"])
        self.assertEqual(status["progress"], {"fraction": 1.0, "text": "all"})
        self.assertNotIn("args", status)

    def test_the_run_is_one_undo_entry_named_for_the_script(self):
        self._run("x = 1")
        self.assertEqual(self.painter.undo_scopes, ["Probe"])

    def test_no_project_open_runs_without_an_undo_entry(self):
        self.painter.project_open = False
        self.assertEqual(self._run("x = 1")["state"], "done")
        self.assertEqual(self.painter.undo_scopes, [])

    def test_a_failing_script_is_its_jobs_failure(self):
        status = self._run("raise ValueError('no such layer')")
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["error"], "ValueError: no such layer")
        self.assertIn("probe.py", status["traceback"])
        self.assertTrue(self.client.ping(), "Painter's server is unharmed")

    def test_a_script_that_does_not_compile_is_refused_before_it_queues(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.client.run_script("def broken(:\n", path="probe.py")
        self.assertIn("SyntaxError", str(ctx.exception))

    def test_a_run_meant_for_another_project_is_refused(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.client.run_script("x = 1", expect={"uuid": "another", "path": "C:/projects/test.spp"})
        self.assertIn("meant for", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.client.run_script("x = 1", expect={"path": "C:/projects/other.spp"})
        self.assertEqual(self._run("x = 1", expect={"uuid": self.painter.uuid, "path": "C:/projects/test.spp"})["state"], "done")

    def test_a_job_is_refused_if_the_project_changed_while_it_waited(self):
        """The project is checked when the job runs, not only when it queued:
        a person may open another while Painter is busy."""
        self.painter.busy = True
        job = self.client.run_script("args.report('ran')", path="probe.py", expect={"uuid": self.painter.uuid})
        self.painter.uuid = "99999999-0000-0000-0000-000000000000"
        self.painter.busy = False
        self.painter.held.pop(0)()
        status = self.client.job(job)
        self.assertEqual((status["state"], status["report"]), ("failed", []))
        self.assertIn("meant for", status["error"])

    def test_a_busy_painter_holds_the_job_and_a_cancel_stops_it_before_it_starts(self):
        self.painter.busy = True
        job = self.client.run_script("args.report('ran')", path="probe.py")
        self.assertEqual(self.client.job(job)["state"], "queued")
        self.assertEqual(self.client.cancel(job)["state"], "queued")
        self.painter.busy = False
        for fn in self.painter.held:
            fn()
        status = self.client.job(job)
        self.assertEqual(status["state"], "cancelled")
        self.assertEqual(status["report"], [])

    def _pause(self):
        from substance_rpc.ops import script_ops

        script_ops.set_paused(True)
        self.addCleanup(script_ops.set_paused, False)
        return script_ops

    def test_paused_in_painter_a_run_is_refused_saying_where_to_resume(self):
        script_ops = self._pause()
        with self.assertRaises(RuntimeError) as ctx:
            self.client.run_script("args.report('ran')", path="probe.py")
        self.assertIn("paused in Painter", str(ctx.exception))
        self.assertIn("M3trik Scripts", str(ctx.exception))
        script_ops.set_paused(False)
        self.assertEqual(self._run("x = 1")["state"], "done")

    def test_a_job_still_queued_when_painter_pauses_never_runs(self):
        self.painter.busy = True
        job = self.client.run_script("args.report('ran')", path="probe.py")
        self._pause()
        self.painter.busy = False
        self.painter.held.pop(0)()
        status = self.client.job(job)
        self.assertEqual((status["state"], status["report"]), ("cancelled", []))
        self.assertIn("paused in Painter", status["error"])

    def test_a_job_waits_for_painters_shelves_to_finish_loading(self):
        """A Painter just launched is still crawling its shelves (its export
        presets among them): a script naming one would not find it yet."""
        from unittest import mock

        from substance_rpc.ops import script_ops

        crawling = [True]
        shelf = types.SimpleNamespace(is_crawling=lambda: crawling[0])
        sys.modules["substance_painter.resource"].Shelves = types.SimpleNamespace(all=lambda: [shelf])
        later = []
        with mock.patch.object(script_ops, "_after", lambda ms, fn: later.append(fn)):
            job = self.client.run_script("args.report('ran')", path="probe.py")
            for _ in range(3):
                later.pop(0)()
            self.assertEqual(self.client.job(job)["state"], "queued", "held while a shelf crawls")
            crawling[0] = False
            while later:
                later.pop(0)()
        self.assertEqual(self.client.job(job)["report"], ["ran"])

    def test_a_running_script_stops_at_its_check(self):
        script = (
            "from substance_rpc.ops import script_ops\n"
            "script_ops._JOBS[[k for k in script_ops._JOBS][-1]]['cancel'] = True\n"
            "args.check_cancel()\n"
            "args.report('not reached')\n"
        )
        status = self._run(script)
        self.assertEqual(status["state"], "cancelled")
        self.assertEqual(status["report"], [])

    def _drain(self):
        """Run what Painter would once idle: its held callbacks, and a Qt
        timer's (Painter's own loop runs those; nothing pumps one here)."""
        try:
            from PySide6 import QtCore
        except ImportError:
            QtCore = None
        for _ in range(50):
            if QtCore is not None and QtCore.QCoreApplication.instance() is not None:
                QtCore.QCoreApplication.processEvents()
            if self.painter.busy or not self.painter.held:
                if not (QtCore and QtCore.QCoreApplication.instance()):
                    return
                continue
            self.painter.held.pop(0)()

    def test_a_step_painter_finishes_later_runs_when_it_is_idle(self):
        """``project.create`` returns before Painter finished loading; a save
        right after is refused ("Painter is busy"). A script's ``when_idle``
        step runs once Painter is idle, and the job ends after its last step."""
        self.painter.busy = True
        job = self.client.run_script(
            "import substance_painter.project as project\n"
            "project.start_loading()\n"  # as project.create: Painter loads on
            "args.report('created')\nargs.when_idle(lambda: args.report('saved'))",
            path="probe.py",
        )
        self.assertEqual(self.client.job(job)["state"], "queued")
        self.painter.busy = False
        self.painter.held.pop(0)()  # the script, once Painter was idle
        self._drain()  # still loading what it made: the step waits
        status = self.client.job(job)
        self.assertEqual((status["state"], status["report"]), ("running", ["created"]))
        self.painter.busy = False
        self._drain()
        status = self.client.job(job)
        self.assertEqual((status["state"], status["report"]), ("done", ["created", "saved"]))

    def test_a_failing_idle_step_fails_the_job(self):
        job = self.client.run_script("args.when_idle(lambda: 1 / 0)", path="probe.py")
        self._drain()
        status = self.client.job(job)
        self.assertEqual(status["state"], "failed")
        self.assertIn("ZeroDivisionError", status["error"])

    def test_an_unknown_job_is_none(self):
        self.assertIsNone(self.client.job("nope"))

    def test_new_project_on_a_mesh_painter_reads_nothing_from_fails_saying_so(self):
        # Painter's importer may read no mesh from a file (it logs "No meshes
        # in scene"): the project then has no texture set to work on.
        project = self.painter.modules["substance_painter.project"]
        project.Settings = lambda **options: options
        project.create = lambda **kwargs: setattr(self.painter, "project_open", True)
        self.painter.project_open = False
        self.painter.modules["substance_painter.textureset"].all_texture_sets = lambda: []
        template = _PLUGIN_SRC.parents[1] / "templates" / "new_project.py"
        mesh = Path(__file__)  # any file on disk: the stand-in reads nothing from it
        job = self.client.run_script(
            template.read_text(encoding="utf-8"), path=str(template), args={"MESH": str(mesh), "MESH_MAP_FILES": []}
        )
        self._drain()
        status = self.client.job(job)
        self.assertEqual(status["state"], "failed", status)
        self.assertIn("read no mesh", status["error"])


class TestInstanceRecord(unittest.TestCase):
    """The plugin announces itself: found by what it is, reached by its token."""

    def setUp(self):
        os.environ["SUBSTANCE_RPC_DISABLE_MAIN_THREAD"] = "1"
        self.addCleanup(os.environ.pop, "SUBSTANCE_RPC_DISABLE_MAIN_THREAD", None)
        self.painter = _FakePainter()
        sys.modules.update(self.painter.modules)
        for name in self.painter.modules:
            self.addCleanup(sys.modules.pop, name, None)
        substance_rpc.start_server(port=0)
        self.addCleanup(substance_rpc.stop_server)

    def test_the_record_names_this_painter_and_what_it_holds(self):
        substance_rpc.publish_state()
        (record,) = PainterRpcClient.painters()
        self.assertEqual(record["pid"], os.getpid())
        self.assertEqual(record["port"], substance_rpc.PLUGIN.address[1])
        project = record["state"]["project"]
        self.assertEqual(project["uuid"], self.painter.uuid)
        self.assertEqual(project["mesh_path"], "C:/meshes/test.fbx")
        self.assertIs(record["state"]["busy"], False)
        self.assertEqual(PainterRpcClient.holding("C:/projects/test.spp")["id"], record["id"])
        self.assertIsNone(PainterRpcClient.holding("C:/projects/other.spp"))

    def test_a_call_without_the_token_is_refused(self):
        with self.assertRaises(RuntimeError):
            PainterRpcClient(port=substance_rpc.PLUGIN.address[1], timeout=5.0).invoke("project.info")
        (record,) = PainterRpcClient.painters()
        self.assertTrue(PainterRpcClient.for_instance(record).project_info()["is_open"])

    def test_pause_is_in_the_record_and_the_status_line(self):
        from substance_rpc.ops import script_ops

        substance_rpc.publish_state()
        self.assertEqual(substance_rpc.status_line(), "Serving test")
        script_ops.set_paused(True)
        self.addCleanup(script_ops.set_paused, False)
        (record,) = PainterRpcClient.painters()
        self.assertIs(record["state"]["paused"], True)
        self.assertEqual(substance_rpc.status_line(), "Paused: scripts sent to Painter are refused")
        script_ops.set_paused(False)
        self.assertIs(PainterRpcClient.painters()[0]["state"]["paused"], False)

    def test_the_port_is_the_oss_and_the_record_goes_with_the_server(self):
        self.assertNotEqual(substance_rpc.PLUGIN.address[1], 8090)
        substance_rpc.stop_server()
        self.assertEqual(PainterRpcClient.painters(), [])


class TestProjectSetupOps(unittest.TestCase):
    """Resolution + high-poly ops, including the deferral that makes them
    work on a fresh launch (dispatched while the New Project wizard is
    still open, applied once the project exists)."""

    @classmethod
    def setUpClass(cls):
        os.environ["SUBSTANCE_RPC_DISABLE_MAIN_THREAD"] = "1"
        cls.port = _free_port()
        substance_rpc.start_server(port=cls.port)
        cls.client = _client(port=cls.port)

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
        for expected in (
            "project.set_resolution",
            "bake.set_high_poly",
            "bake.pending_setup",
        ):
            self.assertIn(expected, ops)

    # -- resolution -----------------------------------------------------

    def test_resolution_applies_to_every_texture_set(self):
        result = self.client.invoke("project.set_resolution", size=4096)
        self.assertTrue(result["applied"])
        self.assertEqual(
            self.painter.resolutions,
            [("body", (4096, 4096)), ("props", (4096, 4096))],
        )

    def test_texture_set_names_read_without_a_deprecation_warning(self):
        # Painter 10.1+ logs "This method is deprecated, use the property with
        # the same name instead." for every ``TextureSet.name()`` call.
        self.client.invoke("project.set_resolution", size=1024)
        self.client.invoke("bake.set_high_poly", mesh_path=self._high_poly_file())
        self.assertEqual(self.painter.resolutions[0][0], "body")
        self.assertEqual(self.painter.deprecation_warnings, [])

    def test_texture_set_name_falls_back_to_the_pre_property_method(self):
        class _LegacyTextureSet:
            def name(self):
                return "legacy"

        self.assertEqual(
            self.setup_ops._texture_set_name(_LegacyTextureSet()), "legacy"
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
            json.dump({"mesh_maps": {"body": {"normal": "C:/nope/x_Normal.png"}}}, fh)
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
        self.client.invoke("bake.set_high_poly", mesh_path=self._high_poly_file())

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


class TestInstallerLaunchAtStart(unittest.TestCase):
    """The person's *Install in Painter* turns on Painter's switch to start the
    plugin at launch (Painter starts a ``plugins/`` module only when
    ``python_plugins/<name>/launch_at_start`` reads ``on``, default off); a
    send's refresh of an install never touches it, nor an off the person set."""

    def setUp(self):
        import tempfile
        from unittest import mock

        from qtpy import QtCore

        from mayatk.mat_utils.substance_bridge.substance_rpc import installer

        self.installer = installer
        self.dir = tempfile.mkdtemp(prefix="substance_rpc_install_")
        self.addCleanup(__import__("shutil").rmtree, self.dir, True)
        self.settings = QtCore.QSettings(
            os.path.join(self.dir, "painter.ini"), QtCore.QSettings.IniFormat
        )
        for target, value in (
            ("painter_settings", lambda: self.settings),
            ("user_plugin_dir", lambda: Path(self.dir) / "plugins"),
        ):
            patch = mock.patch.object(installer.Installer, target, staticmethod(value))
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(
            installer.PluginInstaller,
            "install_plugin",
            return_value=Path(self.dir) / "plugins" / "substance_rpc",
        )
        patch.start()
        self.addCleanup(patch.stop)

    def _install_in_painter(self):
        from mayatk.mat_utils.substance_bridge._substance_bridge import SubstanceBridge

        bridge = SubstanceBridge()
        bridge.logger.setLevel("CRITICAL")
        return bridge.install_rpc_plugin()

    def test_install_in_painter_turns_the_switch_on(self):
        self.assertTrue(self._install_in_painter())
        self.assertEqual(self.settings.value(self.installer.LAUNCH_AT_START_KEY), "on")

    def test_a_sends_refresh_never_touches_the_switch(self):
        older = Path(self.dir) / "plugins" / "substance_rpc"
        older.mkdir(parents=True)
        (older / "__init__.py").write_text("# an older copy", encoding="utf-8")
        self.assertTrue(self.installer.Installer.refresh())
        self.assertFalse(self.settings.contains(self.installer.LAUNCH_AT_START_KEY))

    def test_a_users_off_stands(self):
        self.settings.setValue(self.installer.LAUNCH_AT_START_KEY, "off")
        self._install_in_painter()
        self.assertEqual(self.settings.value(self.installer.LAUNCH_AT_START_KEY), "off")


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


class TestTheLaunchedCopy(unittest.TestCase):
    """A Painter a bridge launches loads the plugin under its own name (the
    launch root's ``substance_rpc_launched``): a person's install must not
    shadow it, nor serve beside it. In its own process: it rebinds
    ``substance_rpc``, which this module imported."""

    def test_it_answers_to_the_plugins_name_and_hides_a_persons_install(self):
        import shutil
        import subprocess
        import tempfile

        root = Path(tempfile.mkdtemp(prefix="srl"))
        self.addCleanup(shutil.rmtree, root, True)
        ignore = shutil.ignore_patterns("__pycache__")
        shutil.copytree(_PLUGIN_SRC / "substance_rpc", root / "launch" / "startup" / "substance_rpc_launched", ignore=ignore)
        shutil.copytree(_PLUGIN_SRC / "substance_rpc", root / "user" / "plugins" / "substance_rpc", ignore=ignore)
        probe = (
            "import importlib, importlib.util, sys\n"
            # Painter's sys.path order: the user folder's plugins/ ahead of the launch root's startup/.
            f"sys.path[:0] = [{str(root / 'user' / 'plugins')!r}, {str(root / 'launch' / 'startup')!r}]\n"
            "launched = importlib.import_module('substance_rpc_launched')\n"
            "from substance_rpc.ops import script_ops\n"
            "print(sys.modules['substance_rpc'] is launched)\n"
            "print(script_ops is launched.ops.script_ops)\n"
            "print(importlib.util.find_spec('substance_rpc').origin)\n"
        )
        from base_test import child_python  # a Python, not maya.exe, under a GUI-hosted run

        out = subprocess.run([child_python(), "-c", probe], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        same, same_ops, origin = out.stdout.split()[-3:]
        self.assertEqual((same, same_ops), ("True", "True"))
        self.assertIn("substance_rpc_launched", origin, "Painter's plugins scan finds the launched copy, not the install")


class TestPluginMenu(unittest.TestCase):
    """The plugin shows in Painter's menu bar (what it serves) and is paused
    there: the connection is visible and switchable off in the app."""

    def setUp(self):
        try:
            from PySide6 import QtWidgets
        except ImportError:
            self.skipTest("no PySide6")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.painter = _FakePainter()
        ui = types.ModuleType("substance_painter.ui")
        self.menus, self.deleted = [], []
        ui.add_menu = self.menus.append
        ui.delete_ui_element = self.deleted.append
        self.painter.modules["substance_painter.ui"] = ui
        self.painter.modules["substance_painter"].ui = ui
        sys.modules.update(self.painter.modules)
        for name in self.painter.modules:
            self.addCleanup(sys.modules.pop, name, None)
        os.environ["SUBSTANCE_RPC_DISABLE_MAIN_THREAD"] = "1"
        self.addCleanup(os.environ.pop, "SUBSTANCE_RPC_DISABLE_MAIN_THREAD", None)
        substance_rpc.start_server(port=0)
        self.addCleanup(substance_rpc.stop_server)

    def test_the_menu_says_what_it_serves_and_pauses_and_goes_with_the_plugin(self):
        from substance_rpc.ops import script_ops

        self.addCleanup(script_ops.set_paused, False)
        substance_rpc.publish_state()
        substance_rpc.add_menu()
        (menu,) = self.menus
        self.assertEqual(menu.title(), "M3trik Scripts")
        status, pause = menu.actions()
        menu.aboutToShow.emit()
        self.assertEqual(status.text(), "Serving test")
        self.assertFalse(status.isEnabled(), "a status line, not a command")
        pause.setChecked(True)
        self.assertTrue(script_ops.is_paused())
        menu.aboutToShow.emit()
        self.assertTrue(status.text().startswith("Paused"))
        substance_rpc.close_plugin()
        self.assertEqual(self.deleted, [menu])
        self.assertFalse(script_ops.is_paused(), "a plugin started again serves")


class TestReloadServesTheFullSurface(unittest.TestCase):
    """A reloaded plugin must still answer every op, over the real wire.

    Observed in a live Painter: ``/health`` alive, ``system.ping`` fine, and
    every bridge op -- ``project.set_resolution``, ``bake.set_high_poly``,
    ``textures.apply_mesh_maps``, ``mesh.reload`` -- coming back
    ``Unknown op``. The registry is rebuilt when the package body re-runs
    (*Python > Reload Plugins Folder*, a disable/re-enable), but the ops
    submodules stay cached, so the import that is supposed to register them
    is a no-op. Nothing about the server looks wrong; the features just
    silently stop happening. Asserted here through the client, not against
    the registry object, because that is the surface the bridge sees.
    """

    #: What the bridge dispatches. A reload that drops any of these is the bug.
    BRIDGE_OPS = (
        "project.set_resolution",
        "bake.set_high_poly",
        "textures.apply_mesh_maps",
        "bake.pending_setup",
        "mesh.reload",
        "mesh.reload_status",
        "project.info",
        "script.start",
        "script.status",
        "script.cancel",
    )

    def setUp(self):
        os.environ["SUBSTANCE_RPC_DISABLE_MAIN_THREAD"] = "1"
        self.addCleanup(os.environ.pop, "SUBSTANCE_RPC_DISABLE_MAIN_THREAD", None)
        # Restore the module-scope import the rest of this file shares.
        cached = {
            name: mod
            for name, mod in sys.modules.items()
            if name == "substance_rpc" or name.startswith("substance_rpc.")
        }
        self.addCleanup(sys.modules.update, cached)

    def _fresh_copy(self):
        """Import a NEW ``substance_rpc`` module object, leaving the shared one be.

        The rest of this file holds the module-scope import, and its server is
        owned by ``TestSubstanceRpcPlugin.setUpClass``. Re-executing *that*
        object (which ``importlib.reload`` does in place) would swap the
        ``PLUGIN`` its ``tearDownClass`` stops, leaking the running server --
        and no ``sys.modules`` restore can undo an in-place mutation.
        """
        del sys.modules["substance_rpc"]
        import substance_rpc as fresh

        return fresh

    def _serve_and_list(self, module):
        port = _free_port()
        module.start_server(port=port)
        self.addCleanup(module.stop_server)
        return _client(module, port).invoke("system.list_ops")

    def _assert_full_surface(self, module, how):
        ops = self._serve_and_list(module)
        for op in self.BRIDGE_OPS:
            self.assertIn(op, ops, f"{op} lost across {how}")

    def test_a_reloaded_plugin_still_serves_every_bridge_op(self):
        self._assert_full_surface(self._fresh_copy(), "a host reload")

    def test_importlib_reload_is_equivalent(self):
        import importlib

        self._assert_full_surface(
            importlib.reload(self._fresh_copy()), "importlib.reload"
        )


class TestInstallerDocumentsDir(unittest.TestCase):
    """Painter finds its user plugin folder through Qt's Documents location,
    which on Linux is the XDG user-dirs entry (a localized desktop names it
    e.g. ``~/Dokumente``), not a hard-coded ``~/Documents``."""

    def test_linux_follows_the_xdg_user_dirs_entry(self):
        import tempfile
        from unittest.mock import patch

        from mayatk.mat_utils.substance_bridge.substance_rpc import installer

        with tempfile.TemporaryDirectory() as home:
            docs = os.path.join(home, "Dokumente")
            os.makedirs(docs)
            os.makedirs(os.path.join(home, "Documents"))
            config = os.path.join(home, ".config")
            os.makedirs(config)
            with open(os.path.join(config, "user-dirs.dirs"), "w") as f:
                f.write("# written by xdg-user-dirs-update\n")
                f.write('XDG_DOCUMENTS_DIR="$HOME/Dokumente"\n')
            real_expanduser = os.path.expanduser
            with patch.object(installer.sys, "platform", "linux"), patch.dict(
                os.environ, {"XDG_CONFIG_HOME": ""}
            ), patch.object(
                installer.os.path,
                "expanduser",
                lambda p: home + p[1:] if p.startswith("~") else real_expanduser(p),
            ):
                got = installer._InstallerInternal._documents_dir()
        self.assertEqual(os.path.normpath(str(got)), os.path.normpath(docs))


if __name__ == "__main__":
    unittest.main()
