# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.mat_utils.substance_bridge._substance_bridge.

Also the home of the vendored ``_substance_engine`` (``SubstanceEngine``, which
``SubstanceBridge`` subclasses): its behavior is exercised through the bridge.

No Maya runtime required -- covers template discovery, metadata parsing,
type validation, and mode filtering. The full bridge.send() flow needs
Maya for FBX export and is covered separately by the Maya test suite.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

import pythontk as ptk

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mayatk.mat_utils.substance_bridge._substance_bridge import (
    SEND_TO,
    ROUND_TRIP,
    PAINTER_AUTO,
    PAINTER_NEW,
    SubstanceBridge,
    _TEMPLATE_DEFAULTS,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base_test import PainterPluginsSandbox  # noqa: E402

_PLUGINS = PainterPluginsSandbox()


def setUpModule():
    _PLUGINS.start()


def tearDownModule():
    _PLUGINS.stop()


def _args(manifest_built=False, mesh_maps=None, high_poly_path=None, works_on="new", params=None, project=None):
    """The ``args`` a script gets for a send that made these files."""
    payload = ptk.Payload(
        primary="C:/out/a.fbx",
        extras={
            "meta": {"WORKS_ON": works_on},
            "manifest_path": "C:/out/a.materials.json",
            "output_dir": "C:/out",
            "staged_textures": [],
            "high_poly_path": high_poly_path,
            "manifest_built": manifest_built,
            "mesh_maps": mesh_maps or {},
        },
    )
    request = ptk.HandoffRequest(
        template="new_project", mode=SEND_TO, params=params or {}, extras={"project": project} if project else {}
    )
    return SubstanceBridge().script_args(payload, request)


def _record(rid, project=None, pid=1):
    return {"id": rid, "pid": pid, "port": 1, "state": {"project": project}}


class TestBuiltInScripts(unittest.TestCase):
    """The scripts the bridge ships, run in Painter: what each runs on, and
    that each is Python Painter can run (no legacy JS left)."""

    WORKS_ON = {
        "new_project": "new",
        "update_mesh": "mesh",
        "bake_lighting": "project",
        "bake_mesh_maps": "project",
        "export_textures": "project",
    }

    def _meta(self, stem):
        return SubstanceBridge.parse_template(SubstanceBridge.template_path(stem))

    def test_the_bundled_set(self):
        self.assertEqual(sorted(p.stem for p in SubstanceBridge.list_templates()), sorted(self.WORKS_ON))

    def test_each_compiles_and_says_what_it_works_on(self):
        for stem, works_on in self.WORKS_ON.items():
            with self.subTest(stem):
                path = SubstanceBridge.template_path(stem)
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
                self.assertEqual(self._meta(stem)["WORKS_ON"], works_on)
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("js.evaluate", text)
                self.assertNotIn("alg.", text)

    def test_new_project_builds_the_manifest_and_embeds_textures(self):
        meta = self._meta("new_project")
        self.assertTrue(meta["BUILD_MANIFEST"])
        self.assertTrue(meta["FBX_OPTIONS"].get("FBXExportEmbeddedTextures"))

    def test_update_mesh_overwrites_the_recorded_export(self):
        self.assertTrue(self._meta("update_mesh")["REUSE_RECORDED_EXPORT"])

    def test_bake_lighting_declares_its_own_rows(self):
        rows = {r["key"]: r for r in ptk.ScriptTemplate.declared_rows(SubstanceBridge.template_path("bake_lighting"))}
        for key in ("BAKE_RESOLUTION", "AO_RAYS", "AO_MAX_DISTANCE", "AO_SPREAD", "INCLUDE_CURVATURE"):
            self.assertIn(key, rows)
        self.assertEqual((rows["AO_RAYS"]["kind"], rows["AO_RAYS"]["default"], rows["AO_RAYS"]["maximum"]), ("int", 64, 256))


class TestParseScriptHeader(unittest.TestCase):
    """parse_template defends against every shape of broken header."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="substance_header_test_")
        self.addCleanup(__import__("shutil").rmtree, self.tmpdir, True)

    def _write(self, name, body, encoding="utf-8"):
        path = Path(self.tmpdir) / name
        path.write_text(body, encoding=encoding)
        return path

    def test_missing_constants_fall_back_to_defaults(self):
        meta = SubstanceBridge.parse_template(self._write("blank.py", '"""empty"""\n'))
        expected = dict(_TEMPLATE_DEFAULTS, BRIDGE_MODES=(SEND_TO,))
        self.assertEqual(meta, expected)
        self.assertEqual(meta["WORKS_ON"], "project", "a person's plain script runs on the open project")

    def test_a_script_saved_with_a_bom_is_parsed(self):
        """A UTF-8 BOM (Windows PowerShell 5.1 writes one) is not content."""
        path = self._write("bom.py", 'WORKS_ON = "new"\n', encoding="utf-8-sig")
        self.assertEqual(SubstanceBridge.parse_template(path)["WORKS_ON"], "new")

    def test_modes_are_filtered_and_folded(self):
        self.assertEqual(SubstanceBridge.parse_template(self._write("a.py", 'BRIDGE_MODES = ("send_to", "garbage")\n'))["BRIDGE_MODES"], (SEND_TO,))
        self.assertEqual(SubstanceBridge.parse_template(self._write("b.py", 'BRIDGE_MODES = ("invalid",)\n'))["BRIDGE_MODES"], (SEND_TO,))
        self.assertEqual(SubstanceBridge.parse_template(self._write("c.py", 'BRIDGE_MODES = ["send_to", "roundtrip"]\n'))["BRIDGE_MODES"], (SEND_TO, ROUND_TRIP))

    def test_an_unknown_works_on_falls_back(self):
        self.assertEqual(SubstanceBridge.parse_template(self._write("w.py", 'WORKS_ON = "everything"\n'))["WORKS_ON"], "project")

    def test_wrong_types_and_non_literals_fall_back(self):
        meta = SubstanceBridge.parse_template(self._write("t.py", 'WORKS_ON = 3\nBUILD_MANIFEST = "yes"\nFBX_OPTIONS = dict(a=1)\n'))
        self.assertEqual((meta["WORKS_ON"], meta["BUILD_MANIFEST"], meta["FBX_OPTIONS"]), ("project", False, {}))

    def test_a_dict_of_fbx_options_parses(self):
        meta = SubstanceBridge.parse_template(self._write("o.py", 'FBX_OPTIONS = {"FBXExportTriangulate": True}\n'))
        self.assertEqual(meta["FBX_OPTIONS"], {"FBXExportTriangulate": True})

    def test_a_syntax_error_or_no_file_falls_back(self):
        expected = dict(_TEMPLATE_DEFAULTS, BRIDGE_MODES=(SEND_TO,))
        self.assertEqual(SubstanceBridge.parse_template(self._write("s.py", "this is not python {{[\n")), expected)
        self.assertEqual(SubstanceBridge.parse_template(Path(self.tmpdir) / "nope.py"), expected)


class _OwnConnection:
    """A Painter this bridge launched, as its registry holds it."""

    def __init__(self, record):
        self.record = record

    def is_alive(self):
        return True


class TestPickPainter(unittest.TestCase):
    """Which Painter a script runs in: by what it works on, and the Painter row."""

    def setUp(self):
        from unittest import mock

        self.bridge = SubstanceBridge()
        self.bridge.logger.setLevel("CRITICAL")
        self.records = []
        for target, value in (
            ("painters", lambda: list(self.records)),
            ("_connection_for", lambda record: ("attached", record["id"])),
            ("_launch_new", lambda exe=None, gui=True, hidden=False: "launched"),
        ):
            patcher = mock.patch.object(self.bridge, target, side_effect=value) if target != "painters" else mock.patch.object(type(self.bridge), target, staticmethod(value))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_new_project_takes_a_painter_with_none_open_else_launches(self):
        self.records = [_record("busy", {"file_path": "C:/p/a.spp"}), _record("free")]
        self.assertEqual(self.bridge.pick_painter("new"), ("attached", "free"))
        self.records = [_record("busy", {"file_path": "C:/p/a.spp"})]
        self.assertEqual(self.bridge.pick_painter("new"), "launched")

    def test_a_painter_paused_in_painter_is_never_taken_as_a_free_one(self):
        paused = dict(_record("paused"), state={"project": None, "paused": True})
        self.records = [paused]
        self.assertEqual(self.bridge.pick_painter("new"), "launched")
        self.assertEqual(self.bridge.pick_painter("project", project="C:/p/b.spp"), "launched")
        self.assertIn("paused", SubstanceBridge.painter_label(paused))

    def test_a_painter_this_bridge_launched_comes_first(self):
        self.records = [_record("theirs"), _record("mine")]
        self.bridge._instances = [_OwnConnection(self.records[1])]
        self.assertEqual(self.bridge.pick_painter("new"), ("attached", "mine"))

    def test_an_explicit_painter_for_update_mesh_must_hold_the_meshs_project(self):
        self.records = [_record("other", {"mesh_path": "C:/m/b.fbx"})]
        self.assertIsNone(self.bridge.pick_painter("mesh", choice="other", mesh="C:/m/a.fbx"))

    def test_update_mesh_needs_the_painter_holding_the_meshs_project(self):
        self.records = [_record("other", {"mesh_path": "C:/m/b.fbx"}), _record("it", {"mesh_path": "C:/m/a.fbx"})]
        self.assertEqual(self.bridge.pick_painter("mesh", mesh="C:/m/a.fbx"), ("attached", "it"))
        self.assertIsNone(self.bridge.pick_painter("mesh", mesh="C:/m/c.fbx"), "never a launch: no project to update")

    def test_a_named_project_runs_where_it_is_open_else_in_a_free_painter(self):
        self.records = [_record("free"), _record("holder", {"file_path": "C:/p/a.spp"})]
        self.assertEqual(self.bridge.pick_painter("project", project="C:/p/a.spp"), ("attached", "holder"))
        self.assertEqual(self.bridge.pick_painter("project", project="C:/p/b.spp"), ("attached", "free"))
        self.records = [_record("holder", {"file_path": "C:/p/a.spp"})]
        self.assertEqual(self.bridge.pick_painter("project", project="C:/p/b.spp"), "launched")

    def test_the_open_project_runs_in_the_only_painter_and_never_guesses(self):
        self.records = [_record("one", {"file_path": "C:/p/a.spp"})]
        self.assertEqual(self.bridge.pick_painter("project"), ("attached", "one"))
        self.records = []
        self.assertIsNone(self.bridge.pick_painter("project"))
        self.records = [_record("a"), _record("b")]
        self.assertIsNone(self.bridge.pick_painter("project"), "several: the person picks")

    def test_the_painter_row_names_one(self):
        self.records = [_record("a"), _record("b", {"file_path": "C:/p/b.spp"})]
        self.assertEqual(self.bridge.pick_painter("project", choice="b"), ("attached", "b"))
        self.assertEqual(self.bridge.pick_painter("project", choice=PAINTER_NEW), "launched")
        self.assertIsNone(self.bridge.pick_painter("project", choice="gone"))
        self.assertIsNone(self.bridge.pick_painter("new", choice="b"), "a new project never replaces an open one")


class _FakeRpc:
    def __init__(self, conn, refuse=False):
        self.conn, self.refuse, self.runs, self.opened = conn, refuse, [], []

    def run_script(self, source, path="", args=None, expect=None, label=""):
        if self.refuse:
            raise RuntimeError("Painter now has another project open")
        self.runs.append({"source": source, "path": path, "args": args, "expect": expect, "label": label})
        return "job1"

    def invoke(self, op, timeout=None, **kwargs):
        assert op == "project.open", op
        self.opened.append(kwargs["path"])
        self.conn.held = {"name": "b", "uuid": "u-b", "file_path": kwargs["path"]}

    def wait(self, job, timeout=3600.0, poll=0.5, on_status=None):
        return {"job": job, "state": "done", "report": ["baked"], "label": "Bake Lighting"}


class _FakeConnection:
    def __init__(self, held=None, refuse=False):
        self.held, self.record = held, {"id": "p1", "pid": 7, "state": {}}
        self.rpc = _FakeRpc(self, refuse)

    def project(self):
        return self.held


class TestDeliver(unittest.TestCase):
    """The send's last step: the Painter's project checked, the script queued."""

    def setUp(self):
        from unittest import mock

        self.bridge = SubstanceBridge()
        self.bridge.logger.setLevel("CRITICAL")
        self.conn = _FakeConnection(held={"name": "a", "uuid": "u-a", "file_path": "C:/p/a.spp"})
        for target, value in (("pick_painter", lambda *a, **k: self.conn), ("refresh_rpc_plugin", lambda: None)):
            patcher = mock.patch.object(self.bridge, target, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _deliver(self, stem, mode=SEND_TO, params=None):
        path = SubstanceBridge.template_path(stem)
        meta = SubstanceBridge.parse_template(path)
        payload = ptk.Payload(
            primary=str(path),  # a file that exists, standing in for the mesh
            extras={"meta": meta, "manifest_path": "C:/out/a.materials.json", "output_dir": "C:/out", "staged_textures": []},
        )
        request = ptk.HandoffRequest(
            template=stem, mode=mode, params=params or {}, extras={"_meta": meta, "_template_path": path}
        )
        return self.bridge._deliver(payload, request)

    def test_a_new_project_is_queued_with_no_project_expected(self):
        result = self._deliver("new_project")
        (run,) = self.conn.rpc.runs
        self.assertIsNone(run["expect"])
        self.assertEqual(run["label"], "New Project")
        self.assertTrue(run["args"]["MESH"].endswith("new_project.py"))
        self.assertIn("import substance_painter", run["source"])
        self.assertEqual((result["job"], result["delivered"]), ("job1", True))

    def test_a_project_script_is_refused_by_painter_if_its_project_changed(self):
        self._deliver("bake_lighting")
        (run,) = self.conn.rpc.runs
        self.assertEqual(run["expect"], {"uuid": "u-a", "path": "C:/p/a.spp"})
        self.assertEqual(run["args"]["MESH"], "", "a project script gets no mesh")

    def test_a_named_project_is_opened_in_a_painter_with_none(self):
        self.conn.held = None
        self._deliver("bake_lighting", params={"PAINTER_PROJECT": "C:/p/b.spp"})
        self.assertEqual(self.conn.rpc.opened, ["C:/p/b.spp"])
        self.assertEqual(self.conn.rpc.runs[0]["expect"], {"uuid": "u-b", "path": "C:/p/b.spp"})

    def test_a_named_project_is_never_opened_over_another(self):
        """Opening would close the project Painter holds, unsaved edits and all."""
        result = self._deliver("bake_lighting", params={"PAINTER_PROJECT": "C:/p/b.spp"})
        self.assertEqual((self.conn.rpc.opened, self.conn.rpc.runs), ([], []))
        self.assertFalse(result["delivered"])

    def test_a_script_declaring_only_round_trip_still_queues(self):
        from unittest import mock

        with mock.patch.object(SubstanceBridge, "parse_template", return_value=dict(_TEMPLATE_DEFAULTS, BRIDGE_MODES=(ROUND_TRIP,))):
            request = ptk.HandoffRequest(template="bake_lighting", mode=SEND_TO, params={}, extras={})
            self.assertTrue(self.bridge._preflight([], request))

    def test_no_project_open_stops_before_painter(self):
        self.conn.held = None
        result = self._deliver("export_textures")
        self.assertEqual(self.conn.rpc.runs, [])
        self.assertFalse(result["delivered"])

    def test_a_refused_run_is_said_not_raised(self):
        self.conn.rpc.refuse = True
        self.assertFalse(self._deliver("bake_lighting")["delivered"])

    def test_a_round_trip_waits_for_the_jobs_end(self):
        result = self._deliver("bake_lighting", mode=ROUND_TRIP)
        self.assertEqual(result["status"]["state"], "done")
        self.assertTrue(result["delivered"])

    def test_no_painter_for_update_mesh_still_leaves_the_mesh_updated(self):
        from unittest import mock

        with mock.patch.object(self.bridge, "pick_painter", return_value=None):
            result = self._deliver("update_mesh")
        self.assertFalse(result["delivered"])
        self.assertIsNone(result["job"])


class TestReport(unittest.TestCase):
    """How a finished job is said: what it printed, and on a failure where."""

    def setUp(self):
        self.bridge = SubstanceBridge()
        self.bridge.logger.setLevel("INFO")

    def _said(self, status):
        with self.assertLogs(self.bridge.logger, "INFO") as logs:
            ran = self.bridge.report(status)
        return ran, "\n".join(logs.output)

    def test_a_failed_job_says_where_in_the_script_it_failed(self):
        """A person's own script failing showed ``KeyError: 'x'`` and nothing
        else: the traceback and what it printed came back and were dropped."""
        status = {
            "job": "j1",
            "label": "Probe",
            "path": "C:/s/probe.py",
            "state": "failed",
            "error": "KeyError: 'x'",
            "report": ["made it"],
            "console": ["working on it"],
            "traceback": (
                "Traceback (most recent call last):\n"
                '  File "C:/painter/substance_rpc/ops/script_ops.py", line 227, in _step\n    fn()\n'
                '  File "C:/s/probe.py", line 3, in <module>\n'
                "KeyError: 'x'\n"
            ),
        }
        ran, said = self._said(status)
        self.assertFalse(ran)
        self.assertIn("Probe failed in Painter: KeyError: 'x'", said)
        self.assertIn('File "C:/s/probe.py", line 3', said)
        self.assertNotIn("script_ops.py", said, "the runner's own frames say nothing a person can act on")
        self.assertIn("working on it", said)
        self.assertIn("made it", said)

    def test_a_cancel_says_why_when_painter_gave_a_reason(self):
        _, said = self._said({"job": "j1", "label": "Probe", "state": "cancelled", "error": "Scripts are paused in Painter."})
        self.assertIn("Probe was cancelled: Scripts are paused in Painter.", said)
        _, said = self._said({"job": "j1", "label": "Probe", "state": "cancelled", "error": None})
        self.assertIn("Probe was cancelled.", said)

    def test_a_wait_that_raised_still_names_its_job(self):
        """The job was queued, and may still be running: a wait on it that
        raised was reported as a script that "did not run"."""
        import types

        def gone():
            raise RuntimeError("the connection closed")

        with self.assertLogs(self.bridge.logger, "INFO") as logs:
            status = self.bridge.conclude(types.SimpleNamespace(result=gone), job="j1")
        self.assertEqual((status["job"], status["state"]), ("j1", "failed"))
        said = "\n".join(logs.output)
        self.assertIn("failed in Painter: the connection closed", said)
        self.assertNotIn("did not run", said)

    def test_a_run_that_never_reached_painter_is_not_a_failure_in_painter(self):
        _, said = self._said(SubstanceBridge.follow({}))
        self.assertIn("did not run", said)
        self.assertNotIn("failed in Painter", said)


class TestDeliverInBackground(unittest.TestCase):
    """A panel's send: the waits (a launch serving, a project opening) run in
    the panel's background, silently; the queue and every word on its thread."""

    def setUp(self):
        from unittest import mock

        self.bridge = SubstanceBridge()
        self.bridge.logger.setLevel("CRITICAL")
        self.conn = _FakeConnection(held={"name": "a", "uuid": "u-a", "file_path": "C:/p/a.spp"})
        self.picks, self.served, self.deferred, self.queued = [], [], [], []
        patches = {
            "pick_painter": lambda *a, **k: self.picks.append(k) or self.pick,
            "refresh_rpc_plugin": lambda: None,
            "_start_painter": lambda *a: self.conn,
            "_served": lambda conn: self.served.append(conn) or "",
        }
        for target, value in patches.items():
            patcher = mock.patch.object(self.bridge, target, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.pick = self.conn

    def _send(self, stem, params=None):
        path = SubstanceBridge.template_path(stem)
        meta = SubstanceBridge.parse_template(path)
        payload = ptk.Payload(primary=str(path), extras={"meta": meta, "output_dir": "C:/out", "staged_textures": []})
        extras = {
            "_meta": meta,
            "_template_path": path,
            "background": lambda work, then: self.deferred.append((work, then)),
            "on_queued": self.queued.append,
        }
        return self.bridge._deliver(payload, ptk.HandoffRequest(template=stem, mode=SEND_TO, params=params or {}, extras=extras))

    def _finish(self):
        ((work, then),) = self.deferred
        then(work())

    def test_a_launch_waits_in_the_background_and_queues_after(self):
        self.pick = PAINTER_NEW
        result = self._send("new_project")
        self.assertIs(self.picks[0]["launch"], False, "the panel's send never launches inline")
        self.assertTrue(result["pending"])
        self.assertEqual((self.conn.rpc.runs, self.served), ([], []), "nothing waited for yet")
        self._finish()
        self.assertEqual(self.served, [self.conn])
        (queued,) = self.queued
        self.assertEqual((queued["job"], queued["connection"]), ("job1", self.conn))

    def test_a_project_opens_in_the_background(self):
        self.conn.held = None
        self.assertTrue(self._send("bake_lighting", params={"PAINTER_PROJECT": "C:/p/b.spp"})["pending"])
        self.assertEqual(self.conn.rpc.opened, [])
        self._finish()
        self.assertEqual(self.conn.rpc.opened, ["C:/p/b.spp"])
        self.assertEqual(self.queued[0]["job"], "job1")

    def test_a_painter_serving_with_the_project_open_queues_at_once(self):
        result = self._send("bake_lighting")
        self.assertEqual((result["job"], self.deferred, self.queued), ("job1", [], []))

    def test_a_launch_that_never_serves_ends_the_run_saying_so(self):
        self.pick = PAINTER_NEW
        self.bridge._served.side_effect = lambda conn: "The launched Painter never served (see its log)."
        self._send("new_project")
        self._finish()
        (queued,) = self.queued
        self.assertEqual((queued["job"], queued["delivered"]), (None, False))

    def test_the_panel_always_hears_how_the_wait_ended(self):
        """A panel refuses a second Run until ``on_queued``: a Painter that
        died between serving and the queue left it refusing every run."""
        from unittest import mock

        self.conn.held = None
        self._send("bake_lighting", params={"PAINTER_PROJECT": "C:/p/b.spp"})
        with mock.patch.object(self.conn.rpc, "run_script", side_effect=RuntimeError("gone")):
            with mock.patch.object(self.conn, "project", side_effect=OSError("the socket closed")):
                self._finish()
        (queued,) = self.queued
        self.assertEqual((queued["job"], queued["delivered"]), (None, False))

    def test_a_busy_painter_is_said_not_raised(self):
        """Mid-save or mid-export its main thread answers nothing: the run
        stops with a sentence, never a traceback in the panel's log."""
        from unittest import mock

        self.bridge.logger.setLevel("INFO")
        with mock.patch.object(self.conn, "project", side_effect=ConnectionError("timed out")):
            with self.assertLogs(self.bridge.logger, "ERROR") as logs:
                result = self._send("bake_lighting")
        self.assertFalse(result["delivered"])
        self.assertIn("is busy and did not answer", "\n".join(logs.output))


class TestScriptArgs(unittest.TestCase):
    def test_rows_default_under_the_request(self):
        args = _args(params={"PAINTER_RESOLUTION": 2048})
        self.assertEqual(args["PAINTER_RESOLUTION"], 2048)
        self.assertIs(args["PAINTER_SAVE"], False)
        self.assertEqual(args["PAINTER"], PAINTER_AUTO)

    def test_the_mesh_only_for_a_script_on_a_mesh(self):
        self.assertEqual(_args(works_on="new")["MESH"], "C:/out/a.fbx")
        self.assertEqual(_args(works_on="project")["MESH"], "")

    def test_the_project_from_the_row_or_the_caller(self):
        self.assertEqual(_args(params={"PAINTER_PROJECT": "C:\\p\\a.spp"})["PROJECT"], "C:/p/a.spp")
        self.assertEqual(_args(project="C:/p/b.spp")["PROJECT"], "C:/p/b.spp")


class TestPreflightRefusals(unittest.TestCase):
    def setUp(self):
        self.bridge = SubstanceBridge()
        self.bridge.logger.setLevel("CRITICAL")

    def _request(self, template, **extras):
        return ptk.HandoffRequest(template=template, mode=SEND_TO, params={}, extras=dict(extras))

    def test_a_script_that_does_not_compile_never_reaches_painter(self):
        from unittest import mock

        root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, root, True)
        with mock.patch.dict(os.environ, {ptk.UserConfig.CONFIG_ROOT_ENV_VAR: str(root)}):
            mine = ptk.ScriptTemplate.user_dir("substance")
            mine.mkdir(parents=True)
            (mine / "broken.py").write_text("def broken(:\n", encoding="utf-8")
            self.assertFalse(self.bridge._preflight([], self._request("broken")))

    def test_a_missing_script_or_mesh_is_refused(self):
        self.assertFalse(self.bridge._preflight([], self._request("no_such_script")))
        self.assertFalse(self.bridge._preflight([], self._request("new_project", mesh="C:/no/such.fbx")))
        self.assertTrue(self.bridge._preflight([], self._request("bake_lighting")))


class TestThePluginIsNeverInstalledUnasked(unittest.TestCase):
    """A send refreshes the person's install; it never makes one."""

    def test_the_engine_declares_its_plugin_for_the_shared_install(self):
        """Every panel's *Install in Painter* runs the shared installer on this
        spec, which follows ``SUBSTANCE_PAINTER_PLUGINS_PATH`` at call time."""
        spec = SubstanceBridge.RPC_PLUGIN
        self.assertEqual((spec.name, spec.app), ("substance_rpc", "Painter"))
        self.assertIn(os.path.normcase(os.environ[PainterPluginsSandbox.ENV]), os.path.normcase(str(spec.destination())))

    def test_a_send_only_refreshes_an_install_that_is_there(self):
        """In the module's sandboxed plugins folder (never the person's)."""
        from mayatk.mat_utils.substance_bridge.substance_rpc.installer import Installer

        dest = Installer.user_plugin_dir() / "substance_rpc"
        self.assertIn(os.path.normcase(os.environ[PainterPluginsSandbox.ENV]), os.path.normcase(str(dest)))
        self.assertIsNone(Installer.refresh())
        self.assertFalse(dest.exists(), "nothing there: a refresh installs nothing")
        dest.mkdir(parents=True)
        (dest / "__init__.py").write_text("# an older copy", encoding="utf-8")
        self.addCleanup(Installer.uninstall)
        self.assertEqual(Installer.refresh(), dest)
        self.assertTrue(Installer.is_current())


class TestTemplateDiscovery(unittest.TestCase):
    def test_list_templates_finds_new_project(self):
        stems = [p.stem for p in SubstanceBridge.list_templates()]
        self.assertIn("new_project", stems)

    def test_list_templates_skips_underscore_prefixed(self):
        # Sanity: __init__.py is in templates/ but starts with underscore
        # and must not be reported as a user template.
        stems = [p.stem for p in SubstanceBridge.list_templates()]
        self.assertNotIn("__init__", stems)

    def test_list_template_modes_returns_pairs(self):
        pairs = SubstanceBridge.list_template_modes()
        self.assertIn(("new_project", SEND_TO), pairs)

    def test_a_persons_own_template_shadows_the_bundled_one(self):
        """Their folder (``ptk.ScriptTemplate.user_dir("substance")``) comes
        first: a template named like a bundled one is the one listed and run."""
        from unittest import mock

        root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, root, True)
        with mock.patch.dict(os.environ, {ptk.UserConfig.CONFIG_ROOT_ENV_VAR: str(root)}):
            mine = ptk.ScriptTemplate.user_dir("substance")
            mine.mkdir(parents=True)
            (mine / "new_project.py").write_text("BRIDGE_MODES = ('send_to',)\n", encoding="utf-8")
            (mine / "my_bake.py").write_text("BRIDGE_MODES = ('send_to',)\n", encoding="utf-8")
            self.assertEqual(SubstanceBridge.template_dirs()[0], mine)
            self.assertEqual(SubstanceBridge.template_path("new_project"), mine / "new_project.py")
            stems = [p.stem for p in SubstanceBridge.list_templates()]
            self.assertIn("my_bake", stems)
            self.assertEqual(stems.count("new_project"), 1)
            self.assertIn(("my_bake", SEND_TO), SubstanceBridge.list_template_modes())


class TestFbxOptionsField(unittest.TestCase):
    """FBX_OPTIONS template field parsing + merge precedence."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="substance_fbxopts_test_")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, name, body):
        path = Path(self.tmpdir) / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_missing_field_defaults_to_empty_dict(self):
        path = self._write("blank.py", '"""empty"""\n')
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["FBX_OPTIONS"], {})

    def test_dict_value_parsed(self):
        path = self._write(
            "with_opts.py",
            'FBX_OPTIONS = {"FBXExportEmbeddedTextures": True, '
            '"FBXExportTriangulate": True}\n',
        )
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(
            meta["FBX_OPTIONS"],
            {"FBXExportEmbeddedTextures": True, "FBXExportTriangulate": True},
        )

    def test_wrong_type_falls_back(self):
        path = self._write("bad.py", 'FBX_OPTIONS = "not a dict"\n')
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["FBX_OPTIONS"], {})

    def test_non_literal_falls_back(self):
        path = self._write("expr.py", "FBX_OPTIONS = dict(a=1)\n")
        meta = SubstanceBridge.parse_template(path)
        self.assertEqual(meta["FBX_OPTIONS"], {})


class TestAssignedTextureStaging(unittest.TestCase):
    """_stage_assigned_textures copies textures from MatUtils into the output dir."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="substance_textures_test_")
        # Create two fake texture files in a 'src' subdir.
        src_dir = Path(self.tmpdir) / "src"
        src_dir.mkdir()
        self.src_ao = src_dir / "obj_ao.png"
        self.src_normal = src_dir / "obj_normal.png"
        self.src_ao.write_bytes(b"fake-ao")
        self.src_normal.write_bytes(b"fake-normal")
        self.out_dir = Path(self.tmpdir) / "out"
        self.out_dir.mkdir()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _patch_mat_utils(self, paths):
        """Replace MatUtils.get_texture_paths with a stub returning *paths*.

        Patched via ``mock.patch.object`` rather than ``setattr`` + a saved
        reference: reading a classmethod off the class yields a *bound* method,
        so restoring it would leave a plain attribute where a descriptor was.
        The runner shares a process across modules, so that outlives this file.
        """
        from unittest.mock import patch

        from mayatk.mat_utils import _mat_utils

        patcher = patch.object(
            _mat_utils.MatUtils,
            "get_texture_paths",
            classmethod(lambda cls, **kw: list(paths)),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_copies_textures_into_output_dir(self):
        self._patch_mat_utils([str(self.src_ao), str(self.src_normal)])
        bridge = SubstanceBridge()
        staged = bridge._stage_assigned_textures(["dummy_obj"], str(self.out_dir))
        self.assertEqual(len(staged), 2)
        for dst in staged:
            self.assertTrue(Path(dst).is_file(), f"staged file missing: {dst}")
            self.assertEqual(Path(dst).parent, self.out_dir)

    def test_missing_source_is_skipped(self):
        self._patch_mat_utils(
            [
                str(self.src_ao),
                str(Path(self.tmpdir) / "does_not_exist.png"),
            ]
        )
        bridge = SubstanceBridge()
        staged = bridge._stage_assigned_textures(["dummy_obj"], str(self.out_dir))
        self.assertEqual(len(staged), 1)

    def test_no_textures_resolves_to_empty_list(self):
        self._patch_mat_utils([])
        bridge = SubstanceBridge()
        staged = bridge._stage_assigned_textures(["dummy_obj"], str(self.out_dir))
        self.assertEqual(staged, [])

    def test_prefix_is_prepended_to_basename(self):
        self._patch_mat_utils([str(self.src_ao)])
        bridge = SubstanceBridge()
        staged = bridge._stage_assigned_textures(
            ["dummy_obj"], str(self.out_dir), prefix="char_"
        )
        self.assertEqual(len(staged), 1)
        self.assertEqual(Path(staged[0]).name, "char_obj_ao.png")
        self.assertTrue(Path(staged[0]).is_file())

    def test_prefix_is_idempotent_when_already_present(self):
        """A source filename that already starts with *prefix* must not get
        the prefix doubled -- the staged file is named ``<prefix><tail>``
        whether or not the source already had it."""
        src = Path(self.tmpdir) / "src" / "char_body_diff.png"
        src.write_bytes(b"already-prefixed")
        self._patch_mat_utils([str(src)])
        bridge = SubstanceBridge()
        staged = bridge._stage_assigned_textures(
            ["dummy_obj"], str(self.out_dir), prefix="char_"
        )
        self.assertEqual(len(staged), 1)
        self.assertEqual(Path(staged[0]).name, "char_body_diff.png")

    def test_empty_prefix_is_no_op(self):
        self._patch_mat_utils([str(self.src_ao)])
        bridge = SubstanceBridge()
        staged = bridge._stage_assigned_textures(
            ["dummy_obj"], str(self.out_dir), prefix=""
        )
        self.assertEqual(Path(staged[0]).name, "obj_ao.png")

    def _patch_unpacker(self, produced_names):
        """Stub MapFactory's ORM split to emit *produced_names* in the output dir."""
        from unittest.mock import patch

        import pythontk as ptk

        def _fake(src, output_dir=None, save=True):
            out = []
            for name in produced_names:
                path = Path(output_dir) / name
                path.write_bytes(b"unpacked-" + name.encode())
                out.append(str(path))
            return out

        patcher = patch.object(
            ptk.MapFactory, "unpack_orm_texture", staticmethod(_fake)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_unpacked_channel_never_replaces_an_authored_map(self):
        """A dedicated ``_AO`` map and an ``_ORM`` share one destination name.

        The ORM's occlusion channel is a by-product of a packed file; the
        authored AO is the real thing. Whichever order the shading network
        happens to resolve them in, the file Painter reads must be the
        authored one -- letting the unpack land last silently downgrades it.
        """
        src_dir = Path(self.tmpdir) / "src"
        orm = src_dir / "obj_ORM.png"
        orm.write_bytes(b"fake-orm")
        authored_ao = src_dir / "obj_AO.png"
        authored_ao.write_bytes(b"authored-ao")
        # ORM resolves FIRST here and LAST in the reversed case below.
        self._patch_unpacker(["obj_AO.png", "obj_Roughness.png"])

        for order in ([orm, authored_ao], [authored_ao, orm]):
            with self.subTest(order=[p.name for p in order]):
                for stale in self.out_dir.iterdir():
                    stale.unlink()
                self._patch_mat_utils([str(p) for p in order])
                staged = SubstanceBridge()._stage_assigned_textures(
                    ["dummy_obj"], str(self.out_dir)
                )
                self.assertEqual(
                    (self.out_dir / "obj_AO.png").read_bytes(), b"authored-ao"
                )
                self.assertIn(str(self.out_dir / "obj_Roughness.png"), staged)

    def test_staged_paths_are_unique(self):
        """A duplicated destination becomes a duplicated ``--mesh-map`` argv pair."""
        src_dir = Path(self.tmpdir) / "src"
        orm = src_dir / "obj_ORM.png"
        orm.write_bytes(b"fake-orm")
        authored_ao = src_dir / "obj_AO.png"
        authored_ao.write_bytes(b"authored-ao")
        self._patch_unpacker(["obj_AO.png", "obj_Roughness.png"])
        self._patch_mat_utils([str(authored_ao), str(orm)])

        staged = SubstanceBridge()._stage_assigned_textures(
            ["dummy_obj"], str(self.out_dir)
        )
        self.assertEqual(len(staged), len(set(staged)), f"duplicates in {staged}")

    def test_a_suffix_lands_after_a_set_name_that_ends_in_a_lobe_word(self):
        """``Hero_Coat`` is the set: read alone, ``Hero_Coat_Roughness`` is a
        coat roughness of ``Hero`` and the suffix split the set name."""
        src_dir = Path(self.tmpdir) / "src"
        sources = []
        for name in ("Hero_Coat_BaseColor.png", "Hero_Coat_Roughness.png"):
            (src_dir / name).write_bytes(b"fake")
            sources.append(str(src_dir / name))
        self._patch_mat_utils(sources)
        staged = SubstanceBridge()._stage_assigned_textures(
            ["dummy_obj"], str(self.out_dir), suffix="_hero"
        )
        self.assertEqual(
            sorted(Path(p).name for p in staged),
            ["Hero_Coat_hero_BaseColor.png", "Hero_Coat_hero_Roughness.png"],
        )


class TestParamsPopulated(unittest.TestCase):
    """The infrastructure was previously scaffolded but PARAMS was empty.
    Guard against regressing back to an empty registry."""

    def test_params_dict_not_empty(self):
        from mayatk.mat_utils.substance_bridge.parameters import PARAMS

        self.assertGreater(
            len(PARAMS),
            0,
            "parameters.PARAMS must expose at least one knob "
            "or the slot UI shows an empty panel",
        )

    def test_new_project_references_params(self):
        """new_project.py must reference at least one registered PARAM key so
        the slot panel surfaces the matching widgets (PAINTER_INCLUDE_TEXTURES
        and PAINTER_SPLIT_BY_UDIM are wired post-render rather than baked
        into LAUNCH_ARGS, but appear in the file as comment references)."""
        from mayatk.mat_utils.substance_bridge import parameters as _params

        path = next(
            (p for p in SubstanceBridge.list_templates() if p.stem == "new_project"),
            None,
        )
        self.assertIsNotNone(path)
        used = _params.Parameters.referenced_keys(path.read_text(encoding="utf-8"))
        self.assertGreater(
            len(used),
            0,
            "new_project.py should reference at least one PARAMS key so the "
            "slot panel exposes a user-tunable knob",
        )


class TestPanelSurfacesAllPainterDialogOptions(unittest.TestCase):
    """Each registered PARAM must be referenced by at least one bundled
    template -- otherwise the widget is defined but never visible in the
    panel for any template selection."""

    def test_every_param_referenced_by_some_template(self):
        from mayatk.mat_utils.substance_bridge import parameters as _params

        referenced = set()
        for path in SubstanceBridge.list_templates():
            referenced |= _params.Parameters.referenced_keys(
                path.read_text(encoding="utf-8")
            )
        missing = set(_params.PARAMS.keys()) - referenced
        self.assertFalse(
            missing,
            "Every PARAM must be referenced by at least one bundled "
            f"template; the panel will never surface: {sorted(missing)}",
        )


class TestPainterLogResolution(unittest.TestCase):
    def test_returns_string_or_none(self):
        # No assertion on existence -- LOCALAPPDATA may or may not have the
        # file. We only verify the function returns the right shape.
        result = SubstanceBridge.resolve_painter_log_path()
        self.assertTrue(result is None or isinstance(result, str))


class TestReimportSupportFields(unittest.TestCase):
    """REUSE_RECORDED_EXPORT parsing."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="substance_reimport_fields_")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, name, body):
        path = Path(self.tmpdir) / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_defaults(self):
        path = self._write("blank.py", '"""empty"""\n')
        meta = SubstanceBridge.parse_template(path)
        self.assertFalse(meta["REUSE_RECORDED_EXPORT"])

    def test_explicit_values_parse(self):
        path = self._write(
            "r.py",
            "REUSE_RECORDED_EXPORT = True\n",
        )
        meta = SubstanceBridge.parse_template(path)
        self.assertTrue(meta["REUSE_RECORDED_EXPORT"])


class TestExportPathRecording(unittest.TestCase):
    """_record_export_path / _recorded_export_path round-trip through the
    scene's fileInfo -- the mechanism that lets reimport overwrite the
    exact file Painter's project points at, across Maya sessions.

    ``cmds`` is faked on the *bridge module's* namespace only (create=True
    -- the venv has no maya), never on the maya package itself.
    """

    def _fake_cmds(self, store):
        class FakeCmds:
            @staticmethod
            def fileInfo(key, value=None, query=False):
                if query:
                    return [store[key]] if key in store else []
                store[key] = value

        return FakeCmds()

    def _patched(self, store):
        from unittest.mock import patch
        from mayatk.mat_utils.substance_bridge import _substance_bridge as sb

        return patch.object(sb, "cmds", self._fake_cmds(store), create=True)

    def test_round_trip_normalizes_backslashes(self):
        store = {}
        with self._patched(store):
            SubstanceBridge._record_export_path("C:\\tmp\\scene.fbx")
            self.assertEqual(
                SubstanceBridge._recorded_export_path(), "C:/tmp/scene.fbx"
            )
        # Stored under the documented key, forward-slashed at write time.
        self.assertEqual(store[SubstanceBridge.EXPORT_RECORD_KEY], "C:/tmp/scene.fbx")

    def test_no_record_returns_none(self):
        with self._patched({}):
            self.assertIsNone(SubstanceBridge._recorded_export_path())

    def test_helpers_survive_missing_maya(self):
        # Both helpers must degrade to a no-op rather than raise when the
        # module-level ``from maya import cmds`` failed (recording is
        # best-effort by contract). The absence is staged explicitly: the
        # ambient environment can't be relied on to supply it -- conftest
        # injects a MagicMock ``maya.cmds`` under plain pytest, and mayapy
        # has the real thing, so neither run ever reaches this path on its
        # own. Asserting against the ambient state made the case pass only
        # when it happened to run before anything touched the name.
        from mayatk.mat_utils.substance_bridge import _substance_bridge as sb

        missing = object()
        original = getattr(sb, "cmds", missing)
        if original is not missing:
            del sb.cmds
            self.addCleanup(setattr, sb, "cmds", original)

        self.assertIsNone(SubstanceBridge._recorded_export_path())
        SubstanceBridge._record_export_path("C:/tmp/x.fbx")  # must not raise


class TestHighPolyPath(unittest.TestCase):
    """The companion high-poly file is derived from the main export path."""

    def test_suffix_inserted_before_extension(self):
        self.assertEqual(
            SubstanceBridge.source_model_path_for("C:/tmp/asset.fbx"),
            "C:/tmp/asset_source.fbx",
        )

    def test_dotted_stem_keeps_only_the_real_extension(self):
        self.assertEqual(
            SubstanceBridge.source_model_path_for("C:/tmp/asset.v002.fbx"),
            "C:/tmp/asset.v002_source.fbx",
        )

    def test_the_high_poly_rides_in_the_scripts_args(self):
        args = _args(high_poly_path="C:\\tmp\\a_source.fbx")
        self.assertEqual(args["HIGH_POLY"], "C:/tmp/a_source.fbx")
        # Without one the slot is empty, never a path to a file not written.
        self.assertEqual(_args()["HIGH_POLY"], "")


class TestTemplatesClaimSetupParams(unittest.TestCase):
    """The panel only shows a widget whose ``__KEY__`` appears in the
    template, so the claim has to be present in the file itself."""

    def _referenced(self, stem):
        from mayatk.mat_utils.substance_bridge import parameters as params
        from mayatk.mat_utils.substance_bridge._substance_bridge import _TEMPLATE_DIR

        text = (_TEMPLATE_DIR / f"{stem}.py").read_text(encoding="utf-8")
        return params.Parameters.referenced_keys(text)

    def test_new_project_claims_resolution_and_bake_source(self):
        keys = self._referenced("new_project")
        self.assertIn("PAINTER_RESOLUTION", keys)
        self.assertIn("BAKE_SOURCE_SET", keys)

    def test_update_mesh_claims_resolution_and_bake_source(self):
        keys = self._referenced("update_mesh")
        self.assertIn("PAINTER_RESOLUTION", keys)
        self.assertIn("BAKE_SOURCE_SET", keys)

    def test_a_script_on_a_project_claims_neither(self):
        # It exports nothing: neither knob belongs on its panel.
        for stem in ("bake_lighting", "bake_mesh_maps", "export_textures"):
            with self.subTest(stem):
                keys = self._referenced(stem)
                self.assertNotIn("PAINTER_RESOLUTION", keys)
                self.assertNotIn("BAKE_SOURCE_SET", keys)

    def test_the_export_bake_source_checkbox_is_gone(self):
        """The set's contents are the switch; a second control could disagree.

        Registered nowhere and claimed by no template -- a stale ``__KEY__``
        in one template would put a dead widget back on that panel.
        """
        from mayatk.mat_utils.substance_bridge import parameters as params

        self.assertNotIn("PAINTER_HIGH_POLY", params.PARAMS)
        for stem in ("new_project", "update_mesh", "bake_lighting", "bake_mesh_maps", "export_textures"):
            with self.subTest(template=stem):
                self.assertNotIn("PAINTER_HIGH_POLY", self._referenced(stem))

    def test_the_unpack_maps_checkbox_is_gone(self):
        """Unpacking is unconditional -- shipping a file Painter cannot read
        is not a mode anyone picks on purpose."""
        from mayatk.mat_utils.substance_bridge import parameters as params

        self.assertNotIn("PAINTER_UNPACK_MAPS", params.PARAMS)
        self.assertNotIn("PAINTER_UNPACK_MAPS", self._referenced("new_project"))

    def test_new_project_claims_the_texture_affix(self):
        self.assertIn("PAINTER_TEXTURE_AFFIX", self._referenced("new_project"))

    def test_setup_params_are_registered(self):
        from mayatk.mat_utils.substance_bridge import parameters as params

        self.assertEqual(params.PARAMS["PAINTER_RESOLUTION"].default, 4096)
        self.assertEqual(params.PARAMS["BAKE_SOURCE_SET"].kind, "action")
        # "Project default" must exist as the inert choice.
        values = [v for _label, v in params.PARAMS["PAINTER_RESOLUTION"].choices]
        self.assertIn(0, values)


class TestMeshMapClassification(unittest.TestCase):
    """``--mesh-map`` only accepts baked-geometry maps; a base color or
    roughness handed to it has no slot to land in."""

    def test_only_mesh_maps_survive_the_filter(self):
        paths = [
            "C:/t/body_AO.png",
            "C:/t/body_Normal.png",
            "C:/t/body_BaseColor.png",
            "C:/t/body_Roughness.png",
            "C:/t/body_Metallic.png",
            "C:/t/body_Thickness.png",
        ]
        self.assertEqual(
            SubstanceBridge.mesh_map_files(paths),
            [
                "C:/t/body_AO.png",
                "C:/t/body_Normal.png",
                "C:/t/body_Thickness.png",
            ],
        )

    def test_height_is_not_a_mesh_map(self):
        # Painter bakes normal / world-space normal / ID / AO / curvature /
        # position / thickness. There is no height mesh map, so shipping one
        # as a mesh map is the same mistake as shipping a base color.
        self.assertEqual(
            SubstanceBridge.mesh_map_files(
                ["C:/t/body_Height.png", "C:/t/body_Displacement.png"]
            ),
            [],
        )

    def test_unclassifiable_names_are_not_mesh_maps(self):
        # A file whose suffix means nothing to the map registry means
        # nothing to Painter's detection either -- don't ship it as one.
        self.assertEqual(SubstanceBridge.mesh_map_files(["C:/t/body.png"]), [])

    def test_a_set_named_for_a_lobe_ships_its_normal(self):
        # Read alone `Hero_Coat_Normal` is a coat normal (no mesh map); beside
        # its set's base colour it is the set `Hero_Coat`'s own normal.
        self.assertEqual(
            SubstanceBridge.mesh_map_files(
                ["C:/t/Hero_Coat_BaseColor.png", "C:/t/Hero_Coat_Normal.png"]
            ),
            ["C:/t/Hero_Coat_Normal.png"],
        )
        # An OpenPBR set keeps its coat normal off the base normal's slot.
        self.assertEqual(
            SubstanceBridge.mesh_map_files(
                ["C:/t/rock_BaseColor.png", "C:/t/rock_Coat_Normal.png"]
            ),
            [],
        )

    def test_both_normal_conventions_map_to_one_usage(self):
        self.assertEqual(
            SubstanceBridge.MESH_MAP_TYPES["Normal_DirectX"],
            SubstanceBridge.MESH_MAP_TYPES["Normal_OpenGL"],
        )


class TestMeshMapAssignments(unittest.TestCase):
    """The manifest's ``mesh_maps`` section -- the per-texture-set wiring
    ``--mesh-map`` can't express."""

    def test_maps_are_keyed_by_material(self):
        materials = {
            "MAT_Body": {
                "baseColor": "C:/maya/body_BaseColor.png",
                "normal": "C:/maya/body_Normal.png",
            },
            "MAT_Head": {"normal": "C:/maya/head_Normal.png"},
        }
        staged = ["C:/out/body_Normal.png", "C:/out/head_Normal.png"]
        self.assertEqual(
            SubstanceBridge._mesh_map_assignments(materials, staged),
            {
                "MAT_Body": {"normal": "C:/out/body_Normal.png"},
                "MAT_Head": {"normal": "C:/out/head_Normal.png"},
            },
        )

    def test_unpacked_component_resolves_to_its_source_material(self):
        # The manifest points at the packed ORM; what shipped is body_AO.png.
        # Matching on the base texture name is what bridges the two.
        materials = {"MAT_Body": {"roughness": "C:/maya/body_ORM.png"}}
        staged = ["C:/out/body_AO.png", "C:/out/body_Roughness.png"]
        self.assertEqual(
            SubstanceBridge._mesh_map_assignments(materials, staged),
            {"MAT_Body": {"ambient_occlusion": "C:/out/body_AO.png"}},
        )

    def test_a_set_named_for_a_lobe_keys_by_its_whole_name(self):
        materials = {
            "MAT_Coat": {
                "baseColor": "C:/maya/Hero_Coat_BaseColor.png",
                "normal": "C:/maya/Hero_Coat_Normal.png",
            }
        }
        staged = ["C:/out/Hero_Coat_BaseColor.png", "C:/out/Hero_Coat_Normal.png"]
        self.assertEqual(
            SubstanceBridge._mesh_map_assignments(materials, staged),
            {"MAT_Coat": {"normal": "C:/out/Hero_Coat_Normal.png"}},
        )

    def test_material_with_no_mesh_maps_is_omitted(self):
        materials = {"MAT_Body": {"baseColor": "C:/maya/body_BaseColor.png"}}
        self.assertEqual(
            SubstanceBridge._mesh_map_assignments(
                materials, ["C:/out/body_BaseColor.png"]
            ),
            {},
        )

    def test_prefixed_staging_still_matches(self):
        # The texture prefix renames the staged copy, so the manifest's
        # Maya-side name and the shipped filename no longer agree. Without
        # stripping it the whole wiring section would come back empty -- the
        # feature silently off for anyone who set a prefix.
        materials = {"MAT_Body": {"normal": "C:/maya/body_Normal.png"}}
        staged = ["C:/out/hero_body_Normal.png"]
        self.assertEqual(
            SubstanceBridge._mesh_map_assignments(materials, staged, prefix="hero_"),
            {"MAT_Body": {"normal": "C:/out/hero_body_Normal.png"}},
        )

    def test_prefixed_unpacked_component_also_matches(self):
        # Both transforms at once: packed source AND a prefix.
        materials = {"MAT_Body": {"roughness": "C:/maya/body_ORM.png"}}
        staged = ["C:/out/hero_body_AO.png"]
        self.assertEqual(
            SubstanceBridge._mesh_map_assignments(materials, staged, prefix="hero_"),
            {"MAT_Body": {"ambient_occlusion": "C:/out/hero_body_AO.png"}},
        )

    def test_assignment_is_deterministic_across_hash_seeds(self):
        # A material whose slots span two base names must not resolve a
        # usage to a different file per run (set iteration is seed-ordered).
        materials = {
            "MAT_Body": {
                "normal": "C:/maya/alpha_Normal.png",
                "baseColor": "C:/maya/beta_BaseColor.png",
            }
        }
        staged = ["C:/out/alpha_Normal.png", "C:/out/beta_Normal.png"]
        results = {
            tuple(
                sorted(
                    SubstanceBridge._mesh_map_assignments(materials, staged)[
                        "MAT_Body"
                    ].items()
                )
            )
            for _ in range(5)
        }
        self.assertEqual(len(results), 1, results)
        self.assertEqual(
            SubstanceBridge._mesh_map_assignments(materials, staged),
            {"MAT_Body": {"normal": "C:/out/alpha_Normal.png"}},
        )

    def test_paths_are_forward_slashed_for_the_wire(self):
        materials = {"MAT_Body": {"normal": r"C:\maya\body_Normal.png"}}
        out = SubstanceBridge._mesh_map_assignments(
            materials, [r"C:\out\body_Normal.png"]
        )
        self.assertEqual(out["MAT_Body"]["normal"], "C:/out/body_Normal.png")

    def test_empty_inputs_produce_no_section(self):
        self.assertEqual(SubstanceBridge._mesh_map_assignments({}, ["a_AO.png"]), {})
        self.assertEqual(
            SubstanceBridge._mesh_map_assignments({"M": {"normal": "n.png"}}, []), {}
        )

    def test_the_wiring_reaches_the_script_only_with_a_manifest(self):
        self.assertEqual(_args()["MANIFEST"], "")
        args = _args(manifest_built=True, mesh_maps={"M": {"normal": "C:/n.png"}})
        self.assertEqual(args["MANIFEST"], "C:/out/a.materials.json")
        self.assertEqual(args["MESH_MAPS"], {"M": {"normal": "C:/n.png"}})


class TestTextureAffixNaming(unittest.TestCase):
    """``_affix_basename`` -- where the Texture Affix lands on a filename.

    Painter classifies a map by the LAST token of its name, so a suffix has
    to sit before the map-type token. A suffix appended to the whole stem
    (``body_Normal_hero``) is a map Painter can no longer recognise, which
    would make the affix silently break the thing staging exists to do.
    """

    CASES = [
        # (basename, prefix, suffix, expected)
        ("body_Normal.png", "", "", "body_Normal.png"),
        ("body_Normal.png", "hero_", "", "hero_body_Normal.png"),
        ("body_Normal.png", "", "_hero", "body_hero_Normal.png"),
        ("body_Normal.png", "a_", "_b", "a_body_b_Normal.png"),
        # idempotent: an affix already present is not stacked
        ("hero_body_Normal.png", "hero_", "", "hero_body_Normal.png"),
        ("body_hero_Normal.png", "", "_hero", "body_hero_Normal.png"),
        # a name with no map-type token still takes the affix
        ("plate.png", "hero_", "", "hero_plate.png"),
        ("plate.png", "", "_hero", "plate_hero.png"),
    ]

    def test_affix_placement(self):
        for basename, prefix, suffix, expected in self.CASES:
            with self.subTest(name=basename, prefix=prefix, suffix=suffix):
                self.assertEqual(
                    SubstanceBridge._affix_basename(basename, prefix, suffix),
                    expected,
                )

    def test_a_suffix_never_follows_the_map_type_token(self):
        import pythontk as ptk

        out = SubstanceBridge._affix_basename("body_Normal.png", "", "_hero")
        self.assertEqual(ptk.MapFactory.resolve_map_type(out), "Normal")

    def test_a_udim_tile_token_stays_last(self):
        """A tile token distinguishes two files; moving it would collide them."""
        self.assertEqual(
            SubstanceBridge._affix_basename("body_Normal.1001.png", "", "_hero"),
            "body_hero_Normal.1001.png",
        )

    def test_apply_affix_renames_on_disk_and_is_idempotent(self):
        art = ptk.TempArtifacts("mtk_affix_rename", policy="scoped")
        self.addCleanup(art.cleanup)
        out = art.dir_path("out")
        src = Path(out) / "body_Normal.png"
        src.write_bytes(b"x")
        first = SubstanceBridge._apply_affix(str(src), "", "_hero")
        self.assertEqual(Path(first).name, "body_hero_Normal.png")
        second = SubstanceBridge._apply_affix(first, "", "_hero")
        self.assertEqual(second, first)

    def test_empty_affix_is_a_no_op(self):
        self.assertEqual(SubstanceBridge._apply_affix("C:/t/a.png"), "C:/t/a.png")


class _SelectionStub:
    """Minimal ``maya.cmds`` stand-in: just the selection save/restore calls."""

    def __init__(self):
        self.selection = []

    def ls(self, *_args, **_kwargs):
        return list(self.selection)

    def select(self, *args, **kwargs):
        if kwargs.get("clear"):
            self.selection = []
        elif args:
            self.selection = list(args[0]) if isinstance(args[0], list) else [args[0]]


class TestBakeSourceGate(unittest.TestCase):
    """The bake-source set's CONTENTS are the switch -- no companion checkbox.

    The pairing this replaced (a set plus an ``Export Bake Source`` tick) had
    two ways to spell "off" and one silent failure mode -- a set defined, the
    box left clear -- which is the state a user reads as the tool ignoring
    them. Marmoset never had the second control; substance now matches.
    """

    def _export(self, referenced, members):
        from unittest.mock import patch

        import mayatk.mat_utils.substance_bridge._substance_bridge as mod

        bridge = SubstanceBridge()
        exported = []
        # ``cmds`` only appears here to save/restore the selection the export
        # leg disturbs; a stub keeps this a Maya-free unit test.
        with (
            patch.object(mod, "cmds", _SelectionStub()),
            patch.object(
                mod.BakeSourceSet, "members", classmethod(lambda cls: members)
            ),
            patch.object(
                SubstanceBridge,
                "_export_model",
                lambda self, path, objs, req, opts: exported.append((path, objs)),
            ),
        ):
            result = bridge._export_bake_source(
                "C:/out/asset.fbx", {}, set(referenced), None
            )
        return result, exported

    def test_a_populated_set_exports_with_no_checkbox(self):
        result, exported = self._export(["BAKE_SOURCE_SET"], ["|hi_poly", "|hi_poly2"])
        self.assertEqual(
            result, SubstanceBridge.source_model_path_for("C:/out/asset.fbx")
        )
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0][1], ["|hi_poly", "|hi_poly2"])

    def test_an_empty_set_exports_nothing(self):
        result, exported = self._export(["BAKE_SOURCE_SET"], [])
        self.assertIsNone(result)
        self.assertEqual(exported, [])

    def test_a_template_that_does_not_claim_the_row_exports_nothing(self):
        """``render.py`` acts on the open project; a scene set must not leak."""
        result, exported = self._export(["PAINTER_RESOLUTION"], ["|hi_poly"])
        self.assertIsNone(result)
        self.assertEqual(exported, [])

    def test_the_bake_source_is_exported_without_embedded_textures(self):
        """The bake source is geometry; embedding bloats a dense mesh."""
        from unittest.mock import patch

        import mayatk.mat_utils.substance_bridge._substance_bridge as mod

        seen = {}
        with (
            patch.object(mod, "cmds", _SelectionStub()),
            patch.object(
                mod.BakeSourceSet, "members", classmethod(lambda cls: ["|hi"])
            ),
            patch.object(
                SubstanceBridge,
                "_export_model",
                lambda self, path, objs, req, opts: seen.update(opts),
            ),
        ):
            SubstanceBridge()._export_bake_source(
                "C:/out/asset.fbx",
                {"FBXExportEmbeddedTextures": True},
                {"BAKE_SOURCE_SET"},
                None,
            )
        self.assertIs(seen["FBXExportEmbeddedTextures"], False)


class TestUnpackPackedMaps(unittest.TestCase):
    """Packed sources are split into the component maps Painter can read."""

    def setUp(self):
        from PIL import Image

        self.artifacts = ptk.TempArtifacts("mtk_substance_unpack", policy="scoped")
        self.out = self.artifacts.dir_path()
        self.src = os.path.join(self.out, "body_ORM.png")
        Image.new("RGB", (4, 4), (10, 120, 240)).save(self.src)
        self.bridge = SubstanceBridge()

    def tearDown(self):
        self.artifacts.cleanup()

    def test_packed_source_yields_its_components(self):
        produced = self.bridge._unpack_packed_map(self.src, self.out)
        self.assertEqual(
            sorted(os.path.basename(p) for p in produced),
            ["body_AO.png", "body_Metallic.png", "body_Roughness.png"],
        )
        for path in produced:
            self.assertTrue(os.path.isfile(path))

    def test_unpacked_ao_is_a_mesh_map_but_the_packed_source_is_not(self):
        # The point of unpacking: an ORM has no Painter slot, its AO does.
        self.assertEqual(SubstanceBridge.mesh_map_files([self.src]), [])
        produced = self.bridge._unpack_packed_map(self.src, self.out)
        self.assertEqual(
            [os.path.basename(p) for p in SubstanceBridge.mesh_map_files(produced)],
            ["body_AO.png"],
        )

    def test_unpacked_components_carry_the_prefix(self):
        produced = self.bridge._unpack_packed_map(self.src, self.out, prefix="hero_")
        self.assertTrue(
            all(os.path.basename(p).startswith("hero_") for p in produced), produced
        )
        for path in produced:
            self.assertTrue(os.path.isfile(path))

    def test_unpacked_prefix_is_idempotent(self):
        from PIL import Image

        src = os.path.join(self.out, "hero_body_ORM.png")
        Image.new("RGB", (4, 4), (1, 2, 3)).save(src)
        produced = self.bridge._unpack_packed_map(src, self.out, prefix="hero_")
        self.assertTrue(
            all(not os.path.basename(p).startswith("hero_hero_") for p in produced),
            produced,
        )

    def test_unpacked_component_type_round_trips(self):
        produced = self.bridge._unpack_packed_map(self.src, self.out)
        types = {ptk.MapFactory.resolve_map_type(p) for p in produced}
        self.assertEqual(types, {"Ambient_Occlusion", "Roughness", "Metallic"})

    def test_non_packed_source_is_left_to_the_caller(self):
        # None == "not packed, stage it verbatim".
        self.assertIsNone(
            self.bridge._unpack_packed_map(
                os.path.join(self.out, "body_BaseColor.png"), self.out
            )
        )

    def test_every_registered_unpacker_exists_on_map_factory(self):
        for map_type, name in SubstanceBridge._UNPACKERS.items():
            self.assertTrue(
                hasattr(ptk.MapFactory, name),
                f"{map_type} -> MapFactory.{name} is missing",
            )

    def test_unreadable_packed_file_falls_back_to_a_plain_copy(self):
        # A corrupt ORM must not sink the whole staging pass.
        broken = os.path.join(self.out, "broken_ORM.png")
        with open(broken, "wb") as fh:
            fh.write(b"not a png")
        self.assertIsNone(self.bridge._unpack_packed_map(broken, self.out))


if __name__ == "__main__":
    unittest.main()
