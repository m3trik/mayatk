# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.env_utils.scene_exporter module

Tests for SceneExporter class functionality including:
- Initialization and configuration
- Object collection and validation
- Task execution
- Check validation
- Export workflow
- Removed-task verification
"""

import os
import base64
import shutil
import unittest
import tempfile
import logging
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import maya.cmds as cmds
import pythontk as ptk

# --- pymel migration shims (auto-injected by _convert_pm_to_cmds.py) ---
from contextlib import contextmanager as _contextmanager


def _pm_open_file(*args, **kw):
    kw.setdefault("open", True)
    return cmds.file(*args, **kw)


def _pm_new_file(**kw):
    kw.setdefault("new", True)
    return cmds.file(**kw)


def _pm_rename_file(path):
    return cmds.file(rename=path)


@_contextmanager
def _pm_undo_chunk():
    cmds.undoInfo(openChunk=True)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)


# --- end shims ---
from mayatk.env_utils.scene_exporter._scene_exporter import SceneExporter
from mayatk.env_utils.scene_exporter.scene_exporter_slots import SceneExporterSlots
from base_test import MayaTkTestCase, QuickTestCase


def _assign_shader(objects, shader):
    """Assign *shader* via its shading-engine set.

    Reliable in bare mayapy, where ``cmds.hyperShade(assign=...)`` silently
    no-ops (connectWindow.mel ``addContextHelpProc`` error) and leaves the
    geometry on initialShadingGroup — making texture-scoped tests pass
    vacuously (no file nodes found → nothing exercised).
    """
    sgs = cmds.listConnections(shader, type="shadingEngine") or []
    if sgs:
        sg = sgs[0]
    else:
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{shader}SG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
    cmds.sets(objects, edit=True, forceElement=sg)


class TestSceneExporter(MayaTkTestCase):
    """Comprehensive tests for SceneExporter class."""

    def setUp(self):
        """Set up test environment."""
        super().setUp()
        self.exporter = SceneExporter(log_level="DEBUG")
        self.temp_dir = tempfile.mkdtemp()

        # Create some test geometry
        self.cube = cmds.polyCube(name="ExportCube")[0]
        self.sphere = cmds.polySphere(name="ExportSphere")[0]
        self.group = cmds.group(self.cube, self.sphere, name="ExportGroup")

    def tearDown(self):
        """Clean up test environment."""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
        super().tearDown()

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def test_initialization(self):
        """Test SceneExporter initialization."""
        self.assertIsInstance(self.exporter, SceneExporter)
        self.assertIsNotNone(self.exporter.task_manager)

    def test_initialize_objects_selection(self):
        """Test object initialization from selection."""
        cmds.select(self.cube)
        objs = self.exporter._initialize_objects(None)
        self.assertEqual(len(objs), 1)
        self.assertIn(cmds.ls(str(self.cube), l=True)[0], objs)

    def test_initialize_objects_list(self):
        """Test object initialization from list."""
        objs = self.exporter._initialize_objects([self.sphere])
        self.assertEqual(len(objs), 1)
        self.assertIn(cmds.ls(str(self.sphere), l=True)[0], objs)

    def test_initialize_objects_callable(self):
        """Test object initialization from callable."""

        def get_objs():
            return [self.group]

        objs = self.exporter._initialize_objects(get_objs)
        self.assertEqual(len(objs), 1)
        self.assertIn(cmds.ls(str(self.group), l=True)[0], objs)

    def test_an_early_abort_closes_the_run_log(self):
        """The run's ``.log`` handler opened before the export set was resolved
        and the FBX preset loaded, and both exits there passed the ``finally``
        that closes it: an empty set returned, a preset that would not load
        raised. Left open, the next export added a second handler -- every line
        written twice -- and Windows kept the file locked (2026-09-15)."""
        logger = logging.getLogger(type(self.exporter).__name__)

        def file_handlers():
            return [h for h in logger.handlers if isinstance(h, logging.FileHandler)]

        try:
            with patch.object(self.exporter, "_initialize_objects", return_value=[]):
                result = self.exporter.perform_export(
                    export_dir=self.temp_dir, objects=[self.cube], create_log_file=True
                )
            self.assertFalse(result)
            self.assertEqual(file_handlers(), [], "the empty export set left it open")

            with patch.object(
                self.exporter,
                "load_fbx_export_preset",
                side_effect=RuntimeError("Failed to load FBX export preset"),
            ):
                with self.assertRaises(RuntimeError):
                    self.exporter.perform_export(
                        export_dir=self.temp_dir,
                        objects=[self.cube],
                        preset_file=os.path.join(self.temp_dir, "bad.fbxexportpreset"),
                        create_log_file=True,
                    )
            self.assertEqual(file_handlers(), [], "the failed preset load left it open")
        finally:
            self.exporter.close_file_handlers()

    def test_export_path_is_read_only(self):
        """``TaskManager.export_path`` reads the run in flight. Assigning it was
        a deprecated alias for ``run.replace(export_path=...)`` from 2026-09-15
        and was retired 2026-09-21 with no caller, matching blendertk's
        read-only property; the run carries the path."""
        tm = self.exporter.task_manager
        path = os.path.join(self.temp_dir, "assigned.fbx")
        with self.assertRaises(AttributeError):
            tm.export_path = path
        tm.run = tm.run.replace(export_path=path)
        self.assertEqual(tm.export_path, path)

    # ------------------------------------------------------------------
    # Export path generation
    # ------------------------------------------------------------------

    def test_generate_export_path(self):
        """Test export path generation."""
        self.exporter.export_dir = self.temp_dir
        self.exporter.output_name = None

        scene_path = os.path.join(self.temp_dir, "test_scene.ma")
        _pm_rename_file(scene_path)

        path = self.exporter.generate_export_path()
        self.assertTrue(path.endswith("test_scene.fbx"))

        self.exporter.output_name = "CustomName"
        path = self.exporter.generate_export_path()
        self.assertTrue(path.endswith("CustomName.fbx"))

        # The retired Timestamp checkbox, spelled in the name itself.
        self.exporter.output_name = "CustomName_{date}_{time}"
        path = self.exporter.generate_export_path()
        self.assertRegex(path, r"CustomName_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.fbx")
        # The naming state perform_export used to stamp is gone (2026-09-23):
        # the retired inputs fold into output_name instead.
        for retired in ("timestamp", "name_regex"):
            self.assertFalse(hasattr(SceneExporter, retired), retired)

    def _stem_for(self, output_name, **retired):
        """The bare export stem the panel would write for *output_name*.

        *retired* takes the retired naming inputs (``name_regex``,
        ``version_format``, ``timestamp``): each must warn and still fold in
        until its removal release."""
        self.exporter.export_dir = self.temp_dir
        self.exporter.output_name = output_name
        if not retired:
            path = self.exporter.generate_export_path()
        else:
            with self.assertWarns(DeprecationWarning):
                path = self.exporter.resolve_export_path(
                    output_name, self.temp_dir, **retired
                )["path"]
        return os.path.splitext(os.path.basename(path))[0]

    def test_wildcard_stands_in_for_the_scene_name(self):
        """'*' is the default name, so it composes a prefix, a suffix, or both."""
        _pm_rename_file(os.path.join(self.temp_dir, "test_scene.ma"))

        for output_name, expected in (
            (None, "test_scene"),
            ("", "test_scene"),
            ("*", "test_scene"),
            ("*_export", "test_scene_export"),
            ("WIP_*", "WIP_test_scene"),
            ("WIP_*_export", "WIP_test_scene_export"),
            ("asset", "asset"),  # no wildcard — a literal name
        ):
            with self.subTest(output_name=output_name):
                self.assertEqual(self._stem_for(output_name), expected)

    def test_placeholders_fill_from_the_scene_and_the_clock(self):
        """{tokens} resolve; an unsupported one is left in the name as typed."""
        _pm_rename_file(os.path.join(self.temp_dir, "test_scene.ma"))
        folder = os.path.basename(self.temp_dir)

        self.assertEqual(self._stem_for("{folder}_{scene}"), f"{folder}_test_scene")
        self.assertRegex(self._stem_for("*_{date}"), r"test_scene_\d{4}-\d{2}-\d{2}$")
        self.assertEqual(self._stem_for("{nope}_x"), "{nope}_x")

    def test_the_regex_shapes_the_scene_name_wherever_the_pattern_uses_it(self):
        """The RegEx is a transform of the SCENE NAME, so every token spelling
        that name carries it -- '{scene}_my text' used to resolve the raw name
        in the export and in the tooltip's "writes" line alike. Typed text is
        literal, as the field promises."""
        _pm_rename_file(os.path.join(self.temp_dir, "test_scene.ma"))

        for pattern, expected in (
            ("WIP_*", "WIP_prod_scene"),
            ("{name}_x", "prod_scene_x"),
            ("{scene}_my text", "prod_scene_my text"),
            ("test_asset", "test_asset"),  # a literal name is the user's choice
        ):
            with self.subTest(pattern=pattern):
                self.assertEqual(
                    self._stem_for(pattern, name_regex="test_->prod_"), expected
                )

    def test_a_counter_in_the_filename_versions_the_export(self):
        """{n} is the next version this name has in the output folder."""
        _pm_rename_file(os.path.join(self.temp_dir, "test_scene.ma"))

        self.assertEqual(self._stem_for("*_v{n:03d}"), "test_scene_v001")
        open(os.path.join(self.temp_dir, "test_scene_v003.fbx"), "w").close()
        self.assertEqual(self._stem_for("*_v{n:03d}"), "test_scene_v004")
        # Anywhere in the name -- and another name's versions never count.
        self.assertEqual(self._stem_for("v{n:02d}_*"), "v01_test_scene")

    def test_versioning_counts_every_file_the_output_format_writes(self):
        """A GLB-only export leaves no .fbx behind, so scanning for one numbered
        every export v001 and overwrote the previous GLB. FBX + GLB versions as
        one pair."""
        _pm_rename_file(os.path.join(self.temp_dir, "test_scene.ma"))
        open(os.path.join(self.temp_dir, "test_scene_v002.glb"), "w").close()
        open(os.path.join(self.temp_dir, "test_scene_v005.fbx"), "w").close()

        def resolve(output_format):
            return self.exporter.resolve_export_path(
                "*_v{n:03d}", self.temp_dir, output_format=output_format
            )

        def names(resolved):
            return [os.path.basename(p) for p in resolved["paths"]]

        self.assertEqual(names(resolve("glb")), ["test_scene_v003.glb"])
        self.assertEqual(names(resolve("fbx")), ["test_scene_v006.fbx"])
        self.assertEqual(
            names(resolve("fbx_glb")), ["test_scene_v006.fbx", "test_scene_v006.glb"]
        )
        # GLB-only still names the export path .fbx: its temp FBX and the
        # sidecar key off it.
        self.assertTrue(resolve("glb")["path"].endswith("test_scene_v003.fbx"))
        self.assertEqual(resolve("glb")["n"], 3)
        self.assertIsNone(
            self.exporter.resolve_export_path("*", self.temp_dir, report=False)["n"]
        )

    def test_version_and_timestamp_are_spelled_in_the_filename(self):
        """The Version row and the Timestamp checkbox each appended to the name
        the Output Filename already builds: the field spells both itself now
        (*_v{n:03d}, *_{date}_{time})."""
        import inspect

        self.assertNotIn("version", self.exporter.task_manager.task_definitions)
        layout = [n for _, names in SceneExporterSlots._SETTINGS_LAYOUT for n in names]
        self.assertNotIn("version", layout)
        # Named only as a retired preset key (see the next test), never built.
        self.assertIn("chk004", SceneExporterSlots._RETIRED_NAMING_KEYS)
        self.assertNotIn(
            'setObjectName="chk004"', inspect.getsource(SceneExporterSlots)
        )
        self.assertIn("n", SceneExporter.NAME_TOKENS)

    def test_retired_version_and_timestamp_inputs_resolve_for_one_release(self):
        """A headless caller's Version pattern still lands the same file -- its
        {stem} IS the filename pattern -- and the log names the replacement."""
        _pm_rename_file(os.path.join(self.temp_dir, "test_scene.ma"))

        legacy = self._stem_for("WIP_*", version_format="{stem}_v{n:03d}")
        self.assertEqual(legacy, self._stem_for("WIP_*_v{n:03d}"))

        # perform_export's tasks["version"] -- stamped before the early abort,
        # folded once at the entry point (retired 2026-09-23, it warns), and
        # the log names the pattern that says the same thing.
        with (
            self.assertWarns(DeprecationWarning) as retired,
            self.assertLogs(self.exporter.logger, level="WARNING") as caught,
        ):
            self.exporter.perform_export(
                export_dir=self.temp_dir,
                objects=[],
                tasks={"version": "{stem}_v{n:03d}"},
            )
        self.assertIn("tasks['version']", str(retired.warning))
        self.assertTrue(
            any("{scene}_v{n:03d}" in r.getMessage() for r in caught.records),
            [r.getMessage() for r in caught.records],
        )
        self.assertEqual(
            os.path.basename(self.exporter.export_path), "test_scene_v001.fbx"
        )
        self.assertTrue(self.exporter.task_manager.run.versioned)

    def test_retired_perform_export_naming_inputs_warn_and_fold_once(self):
        """Retired 2026-09-23: perform_export(timestamp=, name_regex=) each warn
        and fold into the name ONCE, at the entry point, so output_name -- which
        everything after reads -- states the whole rule."""
        _pm_rename_file(os.path.join(self.temp_dir, "test_scene.ma"))

        for kwargs, retired in (
            ({"name_regex": "test_->prod_"}, "'name_regex'"),
            ({"timestamp": True}, "'timestamp'"),
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertWarns(DeprecationWarning) as caught:
                    self.exporter.perform_export(
                        export_dir=self.temp_dir,
                        objects=[],
                        output_name="WIP_*",
                        **kwargs,
                    )
                self.assertIn(retired, str(caught.warning))
                stem = os.path.splitext(os.path.basename(self.exporter.export_path))[0]
                if "name_regex" in kwargs:
                    self.assertEqual(stem, "WIP_prod_scene")
                    self.assertEqual(
                        self.exporter.output_name, "WIP_{scene:test_->prod_}"
                    )
                else:
                    self.assertRegex(
                        stem, r"^WIP_test_scene_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$"
                    )

    def test_characters_illegal_in_a_filename_are_dropped(self):
        """A '?' the user typed cannot reach the write — nothing else emits one."""
        _pm_rename_file(os.path.join(self.temp_dir, "test_scene.ma"))
        self.assertEqual(self._stem_for("*_a?b"), "test_scene_ab")

    def test_no_typed_pattern_can_abort_the_export_or_name_a_file_nothing(self):
        """The field takes free text, so every shape of it has to resolve to SOME
        legal name: a stray brace pair used to raise IndexError out of the
        formatter, and a pattern whose every character is dropped would have
        written a file that was only an extension.
        """
        _pm_rename_file(os.path.join(self.temp_dir, "test_scene.ma"))

        for pattern, expected in (
            ("?", "test_scene"),  # nothing survives — fall back to the default
            ('"', "test_scene"),
            ("{}", "{0}"),  # positional field: a typo, kept visible
            ("{0}_v", "{0}_v"),
            ("{bad", "{bad"),  # malformed — used verbatim, logged
            ("  *  ", "test_scene"),  # padding is not part of a filename
        ):
            with self.subTest(pattern=pattern):
                self.assertEqual(self._stem_for(pattern), expected)

    def test_the_field_tooltip_previews_the_path_the_export_would_write(self):
        """The hover resolves through the same call as the write, so the two
        cannot drift — and it resolves QUIETLY, since it renders the diagnostics
        itself and a hover must not file a warning per mouse-over."""
        _pm_rename_file(os.path.join(self.temp_dir, "test_scene.ma"))

        slots = self._preview_slots("WIP_*_{nope}")
        with self.assertLogs(slots.logger, level="WARNING") as caught:
            slots.logger.warning("only this one")  # assertLogs needs a record
            html = slots.output_name_preview()
        self.assertEqual(len(caught.records), 1)

        # Every token is taught, the wildcard reads first, and the resolved
        # path is the one generate_export_path builds from the same field.
        self.assertIn(">*</td>", html)
        for token in slots.NAME_TOKENS:
            self.assertIn("{" + token + "}", html)
        self.assertIn("unknown", html)  # {nope} is flagged, not silently kept
        self.assertIn(os.path.join(self.temp_dir, "WIP_test_scene_{nope}.fbx"), html)

    def _preview_slots(self, pattern, output_format="fbx"):
        """A panel stand-in whose fields hold *pattern* and a format."""
        from uitk.widgets.mixins.tooltip_mixin import TooltipFormat

        slots = SceneExporterSlots.__new__(SceneExporterSlots)
        slots.sb = SimpleNamespace(tooltip=TooltipFormat)
        slots.ui = SimpleNamespace(
            txt000=SimpleNamespace(text=lambda: self.temp_dir),
            txt001=SimpleNamespace(text=lambda: pattern),
            cmb004=SimpleNamespace(currentData=lambda: output_format),
        )
        return slots

    def test_the_preview_writes_line_is_the_file_the_export_writes(self):
        """The regex on {scene}, the resolved counter and every file the format
        ships -- the "writes" line names exactly what the next export writes."""
        _pm_rename_file(os.path.join(self.temp_dir, "test_scene.ma"))
        open(os.path.join(self.temp_dir, "prod_scene_v004.glb"), "w").close()

        slots = self._preview_slots(
            "{scene:test_->prod_}_v{n:03d}", output_format="fbx_glb"
        )
        html = slots.output_name_preview()
        self.assertIn(
            os.path.join(self.temp_dir, "prod_scene_v005.fbx") + " + .glb", html
        )
        self.assertNotIn("unknown", html)  # {n} is a token the field knows

    def test_format_export_name_regex(self):
        """The retired RegEx field's own formatter: still honoured, warning,
        until its removal release (retired 2026-09-23 with the field's
        plumbing)."""
        for regex, expected in (
            ("test_->prod_", "prod_scene"),
            ("scene|asset", "test_asset"),
        ):
            with self.assertWarns(DeprecationWarning):
                result = self.exporter.format_export_name("test_scene", regex)
            self.assertEqual(result, expected)

    # ------------------------------------------------------------------
    # Export execution
    # ------------------------------------------------------------------

    def test_perform_export_basic(self):
        """Test basic export execution."""
        try:
            if not cmds.pluginInfo("fbxmaya", q=True, loaded=True):
                cmds.loadPlugin("fbxmaya")
        except Exception:
            print("FBX plugin not available, skipping actual export call")
            return

        result = self.exporter.perform_export(
            export_dir=self.temp_dir,
            objects=[self.cube],
            file_format="FBX export",
        )
        self.assertIsNotNone(result)

    def test_an_export_leaves_the_undo_queue_as_it_found_it(self):
        """The run records nothing: it reverses its own edits, so the next undo
        reverts the edit before the export, not something the export did."""
        try:
            if not cmds.pluginInfo("fbxmaya", q=True, loaded=True):
                cmds.loadPlugin("fbxmaya")
        except Exception:
            self.skipTest("FBX plugin not available")
        cmds.undoInfo(state=True, infinity=True)
        cmds.setAttr(f"{self.sphere}.translateZ", 9.0)

        self.exporter.perform_export(
            export_dir=self.temp_dir,
            objects=[self.cube],
            file_format="FBX export",
        )

        self.assertTrue(cmds.undoInfo(query=True, state=True), "the queue stayed off")
        cmds.undo()
        self.assertEqual(
            cmds.getAttr(f"{self.sphere}.translateZ"),
            0.0,
            "the undo reverted something the export recorded",
        )

    def test_perform_export_keeps_constructor_log_level(self):
        """``perform_export`` used to default ``log_level`` to WARNING and
        re-apply it, silently downgrading ``SceneExporter(log_level="DEBUG")``
        so every per-task line the caller asked for vanished. Omitted now
        means "keep the level"; an explicit level still applies.
        Added: 2026-09-02
        """
        self.assertEqual(self.exporter.logger.level, logging.DEBUG)
        self.exporter.perform_export(export_dir="", objects=[])  # aborts early
        self.assertEqual(self.exporter.logger.level, logging.DEBUG)
        self.exporter.perform_export(export_dir="", objects=[], log_level="ERROR")
        self.assertEqual(self.exporter.logger.level, logging.ERROR)

    def test_the_key_gate_and_the_range_never_list_the_keys(self):
        """``_has_keyframes`` is a count and ``_keyframe_range`` reads the
        curves' ends: neither marshals the key times -- 12 s of a production
        export after a bake, paid by the framerate check for a yes/no
        (2026-09-14). The consumers that need only the ends (the bake range,
        the shear scan's grids) read the range, and it is cached."""
        cmds.setKeyframe(self.cube, attribute="translateY", t=1, value=0)
        cmds.setKeyframe(self.cube, attribute="translateY", t=9.5, value=2)
        tm = self.exporter.task_manager
        tm.objects = cmds.ls(self.cube, long=True)
        with patch(
            "mayatk.env_utils.scene_exporter._task_data.AnimUtils.get_keyframe_times"
        ) as listing:
            self.assertTrue(tm._has_keyframes)
            self.assertEqual(tm._keyframe_range(), (1.0, 9.5))
            self.assertEqual(tm._bake_range_from_keys(), (1, 10))
            self.assertEqual(tm._shear_sample_frames(limit=3), [1.0, 5.25, 9.5])
            self.assertEqual(tm._shear_dense_frames(), [float(f) for f in range(1, 11)])
            listing.assert_not_called()
        with patch(
            "mayatk.env_utils.scene_exporter._task_data.AnimUtils.keyframe_range"
        ) as ends:
            self.assertEqual(tm._keyframe_range(), (1.0, 9.5))
            ends.assert_not_called()  # served from the cache
        tm._invalidate_keyframe_cache()
        cmds.setKeyframe(self.cube, attribute="translateY", t=17, value=4)
        self.assertEqual(tm._keyframe_range(), (1.0, 17.0))
        tm.objects = []
        self.assertFalse(tm._has_keyframes)
        self.assertIsNone(tm._keyframe_range())

    def test_perform_export_defaults_to_scene_dir(self):
        """No export_dir → export the FBX alongside the current scene file.

        Added: 2026-06-16
        """
        try:
            if not cmds.pluginInfo("fbxmaya", q=True, loaded=True):
                cmds.loadPlugin("fbxmaya")
        except Exception:
            self.skipTest("FBX plugin not available")

        scene_path = os.path.join(self.temp_dir, "fallback_scene.ma")
        _pm_rename_file(scene_path)

        result = self.exporter.perform_export(
            export_dir="",
            objects=[self.cube],
            file_format="FBX export",
        )
        self.assertTrue(result)
        self.assertEqual(
            os.path.normpath(self.exporter.export_dir),
            os.path.normpath(self.temp_dir),
        )
        self.assertTrue(
            os.path.exists(os.path.join(self.temp_dir, "fallback_scene.fbx")),
            "FBX should be written next to the scene file when no dir is given",
        )

    def test_perform_export_restores_the_users_selection(self):
        """The write selects the export set (``exportSelected``) and the
        deferred restores re-select as they reparent and delete, so the run
        handed back whatever was selected LAST: a production room shell left
        selected under the highlight wire read as a scene rendered solid
        green. blendertk's ``FbxUtils.export`` already put the prior selection
        back; this is the parity. Added: 2026-09-13
        """
        try:
            if not cmds.pluginInfo("fbxmaya", q=True, loaded=True):
                cmds.loadPlugin("fbxmaya")
        except Exception:
            self.skipTest("FBX plugin not available")

        cmds.select(self.sphere, replace=True)
        before = cmds.ls(selection=True, long=True)
        self.assertTrue(
            self.exporter.perform_export(
                export_dir=self.temp_dir, objects=[self.cube], file_format="FBX export"
            )
        )
        self.assertEqual(cmds.ls(selection=True, long=True), before)

        # An empty selection stays empty -- not left on the export set.
        cmds.select(clear=True)
        self.assertTrue(
            self.exporter.perform_export(
                export_dir=self.temp_dir, objects=[self.cube], file_format="FBX export"
            )
        )
        self.assertEqual(cmds.ls(selection=True, long=True) or [], [])

    def test_perform_export_no_dir_unsaved_scene_aborts(self):
        """No export_dir + unsaved scene → abort (no directory to fall back to).

        Added: 2026-06-16
        """
        # setUp opens a fresh untitled scene — no real scene name to fall back
        # to. The GUI reports "" here; batch/standalone reports a phantom,
        # extensionless "<project>/untitled" path — both must abort.
        scene = cmds.file(query=True, sceneName=True)
        self.assertFalse(
            scene and os.path.splitext(scene)[1],
            f"Fresh untitled scene unexpectedly has a real scene name: {scene!r}",
        )

        result = self.exporter.perform_export(
            export_dir="",
            objects=[self.cube],
            file_format="FBX export",
        )
        self.assertFalse(result)

    # ------------------------------------------------------------------
    # Progress reporting (what drives the panel footer's bar and spinner)
    # ------------------------------------------------------------------

    def _require_fbx(self):
        try:
            if not cmds.pluginInfo("fbxmaya", q=True, loaded=True):
                cmds.loadPlugin("fbxmaya")
        except Exception:
            self.skipTest("FBX plugin not available")

    def _capture_log(self, level=logging.INFO):
        log_output = []
        handler = logging.Handler()
        handler.emit = lambda record: log_output.append(record.getMessage())
        handler.setLevel(level)
        self.exporter.logger.addHandler(handler)
        self.addCleanup(self.exporter.logger.removeHandler, handler)
        return log_output

    def test_perform_export_reports_one_progress_stream_for_the_run(self):
        """``progress_callback(current, total, message)`` is the ONE stream the
        panel footer's bar is driven from: the task manager's per-entry ticks
        and the post-pipeline phases (write, sidecar, ...) share a count, so a
        determinate bar can be driven from the first tick without the caller
        knowing the pipeline. Added: 2026-09-04
        """
        self._require_fbx()
        events = []
        result = self.exporter.perform_export(
            export_dir=self.temp_dir,
            objects=[self.cube],
            output_name="Progress",
            tasks={"set_linear_unit": "cm"},
            progress_callback=lambda c, t, m: events.append((c, t, m)),
        )
        self.assertTrue(result)
        self.assertTrue(events, "no progress was reported at all")
        currents = [c for c, _, _ in events]
        self.assertEqual(currents, sorted(currents), f"current went back: {events}")
        self.assertEqual({t for _, t, _ in events}, {3}, "one task + write + sidecar")
        self.assertEqual(events[0][0], 0)
        self.assertEqual(events[-1][:2], (3, 3), "the last tick snaps to total")
        messages = [m for _, _, m in events if m]
        self.assertTrue(any("set_linear_unit" in m for m in messages), messages)
        self.assertTrue(any(m.startswith("Writing FBX") for m in messages), messages)
        self.assertTrue(
            any(m.startswith("Writing scene sidecar") for m in messages), messages
        )

    def test_a_false_from_the_progress_callback_cancels_before_the_write(self):
        """Esc held over the footer reaches the exporter as ``False`` from its
        callback: the run stops before the next step, writes nothing, says so,
        and still unwinds its staged state. Added: 2026-09-04
        """
        self._require_fbx()
        log_output = self._capture_log(logging.WARNING)
        before = cmds.currentUnit(q=True, linear=True)
        other = "m" if before != "m" else "cm"
        result = self.exporter.perform_export(
            export_dir=self.temp_dir,
            objects=[self.cube],
            output_name="Cancelled",
            tasks={"set_linear_unit": other},
            progress_callback=lambda c, t, m: not (m or "").startswith("Writing"),
        )
        self.assertFalse(result)
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir, "Cancelled.fbx")))
        self.assertTrue(any("cancelled" in m.lower() for m in log_output), log_output)
        self.assertEqual(
            cmds.currentUnit(q=True, linear=True),
            before,
            "the staged working unit must be restored on a cancel",
        )
        self.assertFalse(
            any("remain in the scene" in m or "Kept in" in m for m in log_output),
            f"the only task was staged, and it was restored: {log_output}",
        )

    def test_a_cancel_after_the_write_began_finishes_the_deliverable(self):
        """Past the write a stop request is reported, not honoured: a GLB
        abandoned between its conversion and its texture pass is a file that
        looks complete and is not. Added: 2026-09-04
        """
        self._require_fbx()
        log_output = self._capture_log(logging.WARNING)
        result = self.exporter.perform_export(
            export_dir=self.temp_dir,
            objects=[self.cube],
            output_name="TooLate",
            progress_callback=lambda c, t, m: (
                not (m or "").startswith("Writing scene sidecar")
            ),
        )
        self.assertTrue(result)
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir, "TooLate.fbx")))
        self.assertTrue(
            any("after the write began" in m for m in log_output), log_output
        )

    def _export_blocked_by_duplicate_locators(self, bad_shape_name):
        """perform_export with two same-named locators, which
        check_duplicate_names fails directly after conform_shape_names -- the
        one task it reads. With *bad_shape_name* the sphere's shape is renamed
        first, so the conform has a real repair to make; set_linear_unit is a
        staged edit either way. Returns (result, WARNING messages).
        """
        self._require_fbx()
        roots = []
        for name in ("BlockA", "BlockB"):
            root = cmds.group(empty=True, name=name)
            locator = cmds.spaceLocator(name=f"{name}_loc")[0]
            cmds.parent(locator, root)
            # Renamed under its parent: two same-named roots would be renamed.
            cmds.rename(f"|{root}|{locator}", "dupLoc")
            roots.append(root)
        if bad_shape_name:
            shape = cmds.listRelatives(self.sphere, shapes=True, fullPath=True)[0]
            cmds.rename(shape, "notConformed")
        descendants = cmds.listRelatives(
            roots, allDescendents=True, type="transform", fullPath=True
        )
        objects = cmds.ls(roots + [self.sphere], long=True) + (descendants or [])
        log_output = self._capture_log(logging.WARNING)
        self.exporter.confirm = lambda question: False
        result = self.exporter.perform_export(
            export_dir=self.temp_dir,
            objects=objects,
            output_name="Blocked",
            tasks={
                "set_linear_unit": "m",
                "conform_shape_names": True,
                "check_duplicate_names": "locators",
            },
        )
        return result, log_output

    def test_a_blocked_export_names_the_repairs_its_tasks_kept(self):
        """What a blocked run leaves is what a finished export keeps -- the
        repairs; every staged edit unwinds on this exit too. The warning named
        a fixed list instead, "key snapping/tying" among it, after runs whose
        key edits had all been restored (measured 2026-09-15). It now names
        what the tasks recorded as they made it.
        Added: 2026-09-15
        """
        result, log_output = self._export_blocked_by_duplicate_locators(True)
        self.assertFalse(result)
        blocked = [m for m in log_output if "Export blocked" in m]
        self.assertEqual(len(blocked), 1, log_output)
        self.assertIn("repaired node and shape names", blocked[0])
        self.assertNotIn("key edits", blocked[0], "no key task ran")
        shape = cmds.listRelatives(self.sphere, shapes=True)[0]
        self.assertEqual(shape, "ExportSphereShape", "the repair itself is kept")

    def test_a_blocked_export_that_kept_nothing_claims_nothing(self):
        """A conform that found nothing to repair, and a staged unit change
        that was restored: nothing stays, so nothing is named.
        Added: 2026-09-15
        """
        result, log_output = self._export_blocked_by_duplicate_locators(False)
        self.assertFalse(result)
        self.assertTrue(any("Export blocked" in m for m in log_output), log_output)
        self.assertFalse(
            any("remain in the scene" in m or "Kept in" in m for m in log_output),
            log_output,
        )

    def test_key_edits_are_recorded_as_kept_only_in_write_back_mode(self):
        """Animation Output at Scene Keys (In Place) keeps every key edit, so a
        run that stops before its write names them; Export Copies restores
        them, and names nothing.
        Added: 2026-09-15
        """
        tm = self.exporter.task_manager
        tm.objects = cmds.ls(self.cube, long=True)
        tm.run = tm.run.replace(animation_write_back=True)
        try:
            self.assertFalse(tm._protect_scene_animation())
            self.assertEqual(tm.kept_edits, ["key edits"])
        finally:
            tm.run_deferred_restores()
            tm.run = tm.run.replace(animation_write_back=False)
        self.assertTrue(tm._protect_scene_animation())
        self.assertEqual(tm.kept_edits, [])
        tm.run_deferred_restores()

    # ------------------------------------------------------------------
    # Task / check running
    # ------------------------------------------------------------------

    def test_run_tasks(self):
        """Test running tasks via the exporter."""
        tasks = {
            "set_linear_unit": "cm",
            "check_framerate": "30fps",
        }
        self.exporter.task_manager.objects = [cmds.ls(str(self.cube), l=True)[0]]
        success = self.exporter.task_manager.run_tasks(tasks)
        self.assertTrue(success)

    # ------------------------------------------------------------------
    # Override Checks (the per-run escape hatch)
    # ------------------------------------------------------------------

    def test_failed_checks_offer_the_override_in_the_same_run(self):
        """A failed check used to abort outright, leaving "arm Override Checks
        and export again" as the only way through -- a second full pipeline
        (re-bake, re-optimize textures, re-rewrite paths) over a scene the
        first run had already mutated. The override is now offered at the
        failure point, so accepting it continues the SAME run: the tasks
        dispatch exactly once and the deliverable is written.
        Added: 2026-09-03
        """
        try:
            if not cmds.pluginInfo("fbxmaya", q=True, loaded=True):
                cmds.loadPlugin("fbxmaya")
        except Exception:
            self.skipTest("FBX plugin not available")

        runs = []

        def _fail_once(tasks):
            runs.append(dict(tasks))
            self.exporter.task_manager._last_failed_checks = ["check_path_length"]
            return False

        asked = []
        self.exporter.task_manager.run_tasks = _fail_once
        self.exporter.confirm = lambda question: (asked.append(question), True)[1]

        result = self.exporter.perform_export(
            export_dir=self.temp_dir,
            objects=[self.cube],
            output_name="OverrideAccepted",
            tasks={"check_path_length": 60},
        )

        self.assertTrue(result, "an accepted override must write the file")
        self.assertEqual(len(runs), 1, "the task pipeline must not run twice")
        self.assertEqual(len(asked), 1, "the override must be offered once")
        self.assertIn("check_path_length", asked[0])
        self.assertEqual(
            self.exporter._overridden_checks,
            ["check_path_length"],
            "the run must record what it shipped past, for the banner",
        )
        self.assertTrue(
            os.path.exists(os.path.join(self.temp_dir, "OverrideAccepted.fbx"))
        )

    def test_an_override_runs_the_tasks_the_failed_check_had_stopped(self):
        """The runner stops dispatching tasks at the first failed check --
        everything below it is work an aborted write would throw away. An
        override turns that write back on, so those tasks must run before it:
        without this an overridden export silently shipped a file that skipped
        (say) the texture conversion the user asked for. Only the SKIPPED names
        re-dispatch; re-running the ones above would repeat their mutation.
        Added: 2026-09-03
        """
        tm = self.exporter.task_manager
        dispatched = []
        real_dispatch = tm._execute_tasks_and_checks

        # The resume goes to the dispatcher directly, never through run_tasks:
        # run_tasks re-derives the run's task-driven modes from what it is
        # handed, and a subset would zero the Optimize Keys level mid-run.
        def _record(tasks_only, checks_only):
            dispatched.append(dict(tasks_only))
            self.assertEqual(checks_only, {})
            return True

        # The state the aborted first pass leaves behind: one task never ran.
        tm._last_skipped_tasks = ["convert_to_relative_paths"]
        tm._execute_tasks_and_checks = _record
        try:
            self.exporter._resume_skipped_tasks(
                {"convert_to_relative_paths": True, "set_linear_unit": "cm"}
            )
        finally:
            tm._execute_tasks_and_checks = real_dispatch

        self.assertEqual(len(dispatched), 1)
        self.assertEqual(
            dispatched[0],
            {"convert_to_relative_paths": True},
            "only the skipped task re-dispatches, never the ones that already ran",
        )

        # A run the gate never cut short must not dispatch a second pass at all.
        dispatched.clear()
        tm._last_skipped_tasks = []
        tm._execute_tasks_and_checks = _record
        try:
            self.exporter._resume_skipped_tasks({"set_linear_unit": "cm"})
        finally:
            tm._execute_tasks_and_checks = real_dispatch
        self.assertEqual(dispatched, [])

    def test_resuming_skipped_tasks_keeps_the_banner_counts(self):
        """The second pass re-stamps the run counters the success banner reads.
        The first pass already counted every REQUESTED task, so its numbers are
        the ones that describe the run -- letting the resume zero them made the
        banner drop its "Checks Passed" line entirely.
        Added: 2026-09-03
        """
        tm = self.exporter.task_manager
        tm._last_task_count, tm._last_check_count = 7, 4
        tm._last_skipped_tasks = ["convert_to_relative_paths"]
        tm._last_skipped_checks = ["check_valid_paths"]

        def _second_pass(tasks_only, checks_only):
            tm._last_task_count, tm._last_check_count = 1, 0
            tm._last_skipped_checks = []  # a tasks-only pass skips no check
            return True

        real = tm._execute_tasks_and_checks
        tm._execute_tasks_and_checks = _second_pass
        try:
            self.exporter._resume_skipped_tasks({"convert_to_relative_paths": True})
        finally:
            tm._execute_tasks_and_checks = real
        self.assertEqual((tm._last_task_count, tm._last_check_count), (7, 4))
        # The checks the abort dropped never ran: the banner reads this list to
        # keep them out of "Checks Passed" (added 2026-09-12).
        self.assertEqual(tm._last_skipped_checks, ["check_valid_paths"])

    def test_the_override_prompt_survives_the_panel_rich_text_engine(self):
        """``sb.message_box`` hands its string to Qt's rich-text engine, which
        collapses a newline to a space -- so the seam's plain text (documented
        as "newlines allowed") arrived as one run-on paragraph, the KTX2
        install prompt included. The panel's ``confirm`` translates instead of
        making every caller author HTML.
        Added: 2026-09-03
        """
        seen = {}

        class _SB:
            def message_box(self, string, *buttons):
                seen["string"] = string
                seen["buttons"] = buttons
                return "Yes"

        slots = SceneExporterSlots.__new__(SceneExporterSlots)
        slots.sb = _SB()
        self.assertTrue(slots.confirm("line one\n\nline two & <three>"))
        self.assertIn("<br><br>", seen["string"])
        self.assertNotIn("\n", seen["string"])
        self.assertIn("&amp;", seen["string"])
        self.assertNotIn("<three>", seen["string"])
        self.assertEqual(seen["buttons"], ("Yes", "No"))

    def test_declining_the_override_still_aborts_the_export(self):
        """The offer is consent, never an automatic pass: declining keeps the
        pre-existing abort, and nothing is written.
        Added: 2026-09-03
        """

        def _fail(tasks):
            self.exporter.task_manager._last_failed_checks = ["check_path_length"]
            return False

        self.exporter.task_manager.run_tasks = _fail
        self.exporter.confirm = lambda question: False

        result = self.exporter.perform_export(
            export_dir=self.temp_dir,
            objects=[self.cube],
            output_name="OverrideDeclined",
            tasks={"check_path_length": 60},
        )

        self.assertFalse(result)
        self.assertEqual(self.exporter._overridden_checks, [])
        self.assertFalse(
            os.path.exists(os.path.join(self.temp_dir, "OverrideDeclined.fbx"))
        )

    def test_override_button_never_restores_its_armed_state(self):
        """Override Checks is a per-run escape hatch, so it must not ride
        QSettings into the next session: a registered widget persists by
        default and its restore runs AFTER the slots ``__init__``, which used
        to re-arm the toggle right over the ``setChecked(False)`` there --
        silently disabling every validation check on the next launch.
        Added: 2026-09-03
        """

        class _Button:
            def __init__(self):
                self.restore_state = True
                self.checked = True
                self.enabled = False
                self.style = ""

            def setEnabled(self, value):
                self.enabled = value

            def setChecked(self, value):
                self.checked = value

            def setStyleSheet(self, value):
                self.style = value

        button = _Button()
        SceneExporterSlots._init_override_button(button)
        self.assertFalse(button.restore_state)
        self.assertFalse(button.checked)
        self.assertTrue(button.enabled)

    def test_check_failure(self):
        """Test that a failing check returns False."""
        shader = cmds.shadingNode("lambert", asShader=True)
        file_node = cmds.shadingNode("file", asTexture=True)
        cmds.connectAttr(f"{file_node}.outColor", f"{shader}.color")
        long_path = "C:/absolute/path/" + ("d/" * 40) + "texture.png"
        cmds.setAttr(f"{file_node}.fileTextureName", long_path, type="string")
        _assign_shader(self.cube, shader)

        tasks = {"check_path_length": 60}
        self.exporter.task_manager.objects = [cmds.ls(str(self.cube), l=True)[0]]
        success = self.exporter.task_manager.run_tasks(tasks)
        self.assertFalse(success)

    # ------------------------------------------------------------------
    # Caching
    # ------------------------------------------------------------------

    def test_materials_cache_populated(self):
        """Verify _get_all_materials caches results after first call.

        Bug: _get_all_materials was called 4 times per export with zero caching,
        each time re-walking all shape->shadingEngine->material connections.
        Fixed: 2026-02-22
        """
        shader = cmds.shadingNode("lambert", asShader=True)
        _assign_shader(self.cube, shader)

        self.exporter.task_manager.objects = [cmds.ls(str(self.cube), l=True)[0]]

        mats1 = self.exporter.task_manager._get_all_materials()
        self.assertGreater(len(mats1), 0)
        self.assertIsNotNone(self.exporter.task_manager._cached_materials)

        mats2 = self.exporter.task_manager._get_all_materials()
        self.assertIs(mats1, mats2, "Second call should return cached result")

    def test_materials_cache_invalidated_on_objects_change(self):
        """Verify materials cache is invalidated when objects list changes.

        The objects property setter must clear _cached_materials so stale
        material data from a previous object set isn't reused.
        Fixed: 2026-02-22
        """
        shader = cmds.shadingNode("lambert", asShader=True)
        _assign_shader(self.cube, shader)

        self.exporter.task_manager.objects = [cmds.ls(str(self.cube), l=True)[0]]
        self.exporter.task_manager._get_all_materials()
        self.assertIsNotNone(self.exporter.task_manager._cached_materials)

        self.exporter.task_manager.objects = [cmds.ls(str(self.sphere), l=True)[0]]
        self.assertIsNone(
            self.exporter.task_manager._cached_materials,
            "Cache should be None after objects change",
        )

    def test_task_timing_logged(self):
        """Verify per-task completion+timing is logged at SUCCESS level.

        _manage_context logs each task/check's execution duration. The line
        was promoted INFO -> SUCCESS so a completed task reads as a success
        and the redundant trailing "Check passed" lines could be dropped.
        SUCCESS (25) is above INFO (20), so an INFO-level handler still
        captures it.
        Fixed: 2026-02-22 (timing), 2026-06-27 (level promoted to SUCCESS)
        """
        log_output = []
        handler = logging.Handler()
        handler.emit = lambda record: log_output.append(record.getMessage())
        handler.setLevel(logging.INFO)
        self.exporter.logger.addHandler(handler)
        self.exporter.logger.setLevel(logging.INFO)

        self.exporter.task_manager.objects = [cmds.ls(str(self.cube), l=True)[0]]
        tasks = {"set_linear_unit": "cm"}
        self.exporter.task_manager.run_tasks(tasks)

        timing_msgs = [m for m in log_output if "Completed" in m and "in" in m]
        self.assertGreater(
            len(timing_msgs), 0, "Expected timing log messages from task execution"
        )
        self.exporter.logger.removeHandler(handler)

    # ------------------------------------------------------------------
    # Removed tasks — verify they no longer exist
    # ------------------------------------------------------------------

    def test_deleted_tasks_not_in_definitions(self):
        """Verify removed tasks are absent from task_definitions.

        Removed: check_and_delete_visibility_keys, delete_unused_materials,
        delete_env_nodes.  These were removed as non-export-scoped or
        undesired destructive behaviour.
        Fixed: 2026-03-04
        """
        defs = self.exporter.task_manager.task_definitions
        removed = [
            "check_and_delete_visibility_keys",
            "delete_unused_materials",
            "delete_env_nodes",
        ]
        for name in removed:
            self.assertNotIn(
                name, defs, f"{name} should be removed from task_definitions"
            )

    def test_the_glb_rows_are_the_ones_the_preview_mirrors(self):
        """The WebXR preview offers these rows by the label and table pythontk
        declares (``ExportProfile.GLB_ROWS`` / the combo tables), so a row
        renamed or re-tabled here without it would leave the two panels naming
        the same setting differently. Baked Reflections also starts where the
        lighting recipe itself stands, so an untouched row publishes it
        unchanged. Added: 2026-09-21
        """
        defs = self.exporter.task_manager.task_definitions
        tables = {
            "texture_file_type": ptk.ExportProfile.texture_file_type_options(),
            "optimize_textures": ptk.ExportProfile.optimize_textures_options(),
            "secondary_max_size": ptk.ExportProfile.SECONDARY_MAX_SIZE_OPTIONS,
            "uastc_rdo": ptk.ExportProfile.UASTC_RDO_OPTIONS,
            "baked_reflections": ptk.ExportProfile.BAKED_REFLECTIONS_OPTIONS,
        }
        self.assertEqual(set(tables), set(ptk.ExportProfile.GLB_ROWS))
        for row, label in ptk.ExportProfile.GLB_ROWS.items():
            with self.subTest(row=row):
                self.assertEqual(defs[row]["set_row_label"], label)
                self.assertEqual(defs[row]["add"], tables[row])
        reflections = defs["baked_reflections"]
        self.assertEqual(
            list(reflections["add"].values())[reflections["setCurrentIndex"]],
            ptk.ExportProfile.baked_reflections_default(),
        )

    def test_deleted_tasks_not_in_task_order(self):
        """Verify removed tasks are absent from TASK_ORDER.

        Fixed: 2026-03-04
        """
        order = self.exporter.task_manager.TASK_ORDER
        removed = ["delete_unused_materials", "delete_env_nodes"]
        for name in removed:
            self.assertNotIn(name, order, f"{name} should be removed from TASK_ORDER")

    def test_captionless_rows_have_a_row_label(self):
        """Every definition whose widget carries no text of its own must supply
        a ``set_row_label`` caption.

        A QCheckBox labels itself via ``setText`` and a Separator via ``title``,
        but a ComboBox, QLineEdit or spin-box row renders as a bare control —
        the user sees "16" with no indication it is a texture size budget. A
        placeholder does not cover this: these fields ship with a default
        value, so the placeholder is never visible.
        """
        defs = {
            **self.exporter.task_manager.task_definitions,
            **self.exporter.task_manager.check_definitions,
        }
        captionless = {
            "ComboBox",
            "QLineEdit",
            "SpinBox",
            "DoubleSpinBox",
            "QSpinBox",
            "QDoubleSpinBox",
        }
        missing = [
            name
            for name, params in defs.items()
            if params.get("widget_type") in captionless
            and not params.get("set_row_label")
        ]
        self.assertEqual(
            missing,
            [],
            f"definitions render as unlabelled rows: {missing}",
        )

    def test_definitions_are_grouped_by_tag_not_by_separator_rows(self):
        """Every definition is a real control tagged with the section it
        renders under: ``group`` (Tasks / Checks popups) or ``panel: settings``
        (the Settings popup, whose order is ``_SETTINGS_LAYOUT``). Hand-placed
        ``sep_*`` Separator entries are gone — the slots emit a titled
        Separator wherever the group tag changes, so section membership and
        section order have one source. Changed: 2026-08-17
        """
        for kind, defs in (
            ("task", self.exporter.task_manager.task_definitions),
            ("check", self.exporter.task_manager.check_definitions),
        ):
            for name, params in defs.items():
                self.assertNotEqual(
                    params.get("widget_type"), "Separator", f"{kind} {name}"
                )
                self.assertFalse(name.startswith("sep_"), f"{kind} {name}")
                self.assertTrue(
                    params.get("group") or params.get("panel") == "settings",
                    f"{kind} {name} has neither a group nor a settings tag",
                )

    def test_settings_layout_covers_every_settings_tagged_definition(self):
        """A task tagged ``panel: settings`` leaves the Tasks popup — so it must
        have a slot in ``_SETTINGS_LAYOUT`` or it renders nowhere; and every
        layout name resolves to a widget spec or a definition (a name a DCC
        lacks is skipped, which is how blendertk shares the layout)."""
        defs = self.exporter.task_manager.task_definitions
        laid_out = {
            name
            for _group, names in SceneExporterSlots._SETTINGS_LAYOUT
            for name in names
        }
        settings_tagged = {n for n, p in defs.items() if p.get("panel") == "settings"}
        self.assertEqual(settings_tagged - laid_out, set())
        for name in laid_out:
            self.assertTrue(
                name in SceneExporterSlots._SETTINGS_WIDGETS or name in defs, name
            )
        # The FBX preset and output format rows keep the objectNames saved
        # export presets already carry. The GLB-textures row (cmb006) folded
        # into the general texture_file_type dial, and the texture template
        # (cmb005) moved to the Tasks combo as ``convert_textures`` — both
        # keeping their objectName, so old templates still restore.
        self.assertEqual(
            set(SceneExporterSlots._SETTINGS_WIDGETS), {"cmb000", "cmb004"}
        )

    def test_export_data_node_row_gets_a_viewer_action(self):
        """The Export Scene Data Node row's option box carries one viewer
        action, added once (a repeat init must not stack a second button).
        Qt-free, like the row-builder test below; that a real QCheckBox takes an
        option box at all is uitk's ``TestCheckBoxOptionBox``."""
        actions = []
        widget = SimpleNamespace(
            is_initialized=False,
            option_box=SimpleNamespace(add_action=lambda **kw: actions.append(kw)),
        )
        slots = SceneExporterSlots.__new__(SceneExporterSlots)
        slots.export_data_node_init(widget)
        widget.is_initialized = True
        slots.export_data_node_init(widget)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["callback"], slots._show_data_node)
        icons = os.path.join(os.path.dirname(__import__("uitk").__file__), "icons")
        self.assertTrue(
            os.path.isfile(os.path.join(icons, f"{actions[0]['icon']}.svg"))
        )

    def test_show_data_node_hands_every_shipped_carrier_to_the_shared_viewer(self):
        """The button opens the shared data viewer (``sb.data_view_dialog``,
        tentacle's Scene Metadata viewer) on every data_export carrier the
        export ships -- a referenced module's included -- decoded, and never
        the private data_internal records."""
        from mayatk.node_utils.data_nodes import DataNodes

        shown = []
        slots = SceneExporterSlots.__new__(SceneExporterSlots)
        slots.sb = SimpleNamespace(
            data_view_dialog=lambda data, **kw: shown.append((data, kw))
        )
        slots._show_data_node()
        self.assertEqual(shown[-1][0], {})  # the viewer reports "empty" itself
        self.assertFalse(cmds.objExists(DataNodes.EXPORT), "a view must not create it")

        DataNodes.write(ptk.Scope.DELIVERABLE, "probe_channel", '{"a": [1, 2]}')
        DataNodes.write(ptk.Scope.PRIVATE, "private_probe", "internal-only")
        cmds.namespace(add="MODULE")
        module = cmds.createNode("transform", name="MODULE:data_export")
        cmds.addAttr(module, longName="module_channel", dataType="string")
        cmds.setAttr(f"{module}.module_channel", '{"b": 3}', type="string")
        slots._show_data_node()
        data, kwargs = shown[-1]
        self.assertEqual(
            data,
            {
                "|data_export": {"probe_channel": {"a": [1, 2]}},
                "|MODULE:data_export": {"module_channel": {"b": 3}},
            },
        )
        self.assertTrue(kwargs["save_path"].endswith("_data_export.json"))
        self.assertTrue(kwargs["empty_message"])

    def test_definition_rows_emit_one_separator_per_group_change(self):
        """The row builder: a titled Separator precedes each new group, rows
        keep definition order, settings-tagged entries are filtered out of the
        default panel, and captions come from row labels — the option text no
        longer repeats them ("Scope  Export: All Visible Objects").

        Qt-free on purpose: mayapy standalone owns a QGuiApplication, so a real
        QWidget crashes the process (GUI tests get their own pass). The builder
        only needs a class per ``widget_type`` and ``ui.set_attributes``, so
        recording stubs cover its logic exactly.
        """

        class _Stub:
            def __init__(self, **kwargs):
                self.attrs = dict(kwargs)

        class _Separator(_Stub):
            pass

        registry = SimpleNamespace(
            Separator=_Separator, ComboBox=_Stub, SpinBox=_Stub, Header=_Stub
        )
        qt = SimpleNamespace(QCheckBox=_Stub, QLineEdit=_Stub)
        slots = SceneExporterSlots.__new__(SceneExporterSlots)
        slots.task_manager = self.exporter.task_manager
        slots.sb = SimpleNamespace(
            QtWidgets=qt,
            registered_widgets=registry,
            convert_to_legal_name=lambda n: n,
        )
        slots.ui = SimpleNamespace(set_attributes=lambda w, **kw: w.attrs.update(kw))

        rows = slots._definition_rows(self.exporter.task_manager.task_definitions)
        labels = [label for _w, label in rows]
        seps = [w.attrs["title"] for w, _l in rows if isinstance(w, _Separator)]
        self.assertEqual(
            seps, ["Materials", "Textures", "Lighting", "Animation", "Hierarchy"]
        )
        self.assertEqual(labels[0], "Materials")  # a group opens with its caption
        self.assertNotIn("export_visible_objects", labels)  # settings-tagged
        self.assertIn("smart_bake", labels)
        by_label = {label: w for w, label in rows}
        # Meta keys never reach the widget; the objectName always does.
        for meta in ("widget_type", "panel", "group", "value_method"):
            self.assertNotIn(meta, by_label["smart_bake"].attrs)
        self.assertEqual(by_label["smart_bake"].attrs["setObjectName"], "smart_bake")
        # A row-labelled combo carries its caption once, on the row.
        size = by_label["optimize_textures"]
        self.assertEqual(size.attrs["set_row_label"], "Optimize Textures")
        self.assertEqual(next(iter(size.attrs["add"])), "OFF")
        # The Textures group: the Texture Output gate first, its three
        # dependants directly beneath — the gate and the gated read as one
        # block (2026-08-20 move out of the Settings combo).
        tex_start = labels.index("Textures")
        self.assertEqual(
            labels[tex_start : tex_start + 5],
            [
                "Textures",
                "texture_write_back",
                "convert_textures",
                "optimize_textures",
                "texture_file_type",
            ],
        )

        checks = slots._definition_rows(self.exporter.task_manager.check_definitions)
        check_seps = [w.attrs["title"] for w, _l in checks if isinstance(w, _Separator)]
        self.assertEqual(
            check_seps,
            [
                "General",
                "Hierarchy & Naming",
                "Geometry",
                "Materials & Paths",
                "Animation",
                # The post-write pass sits under its own heading, last: every
                # section above it aborts the write, this one reads the file
                # the write produced.
                "Deliverable (after the write)",
            ],
        )
        check_labels = [label for _w, label in checks]
        self.assertGreater(
            check_labels.index("check_framerate"), check_labels.index("Animation")
        )

    def test_wire_dependencies_hides_irrelevant_settings(self):
        """Every "irrelevant unless" relationship is ONE ``sb.show_when`` rule
        declared in ``_wire_dependencies`` — hidden rather than greyed
        (2026-09-14), no per-trigger slot, no ``_sync_*`` helper. Pins the
        set of dependants, their triggers and the conditions; the rule engine
        itself is covered by uitk's ``test_switchboard_toggle.py``."""
        calls = []

        class _SB:
            def show_when(self, ui, targets, trigger, condition=True, **kw):
                calls.append((targets, trigger, condition))

            def enable_when(self, *args, **kw):
                raise AssertionError("greyed out where it should be hidden")

        slots = SceneExporterSlots.__new__(SceneExporterSlots)
        slots.sb = _SB()
        slots.ui = object()
        slots._wire_dependencies()
        by_target = {t: (trig, cond) for t, trig, cond in calls}
        # No size-dial rule any more: the ceiling rides the Optimize Textures
        # combo itself ("Optimize + Max …"), so a ceiling with nothing to
        # apply it is unrepresentable rather than hidden (2026-08-20).
        self.assertNotIn("texture_max_size", by_target)
        # Texture File Type is NOT gated on Optimize Textures: a GLB
        # deliverable is re-encoded to it whether or not the scene pass runs.
        self.assertNotIn("texture_file_type", by_target)
        self.assertEqual(
            by_target["texture_write_back"][0], ["texture_optimize", "cmb005"]
        )
        self.assertEqual(by_target["exclude_hdr"][0], "export_visible_objects")
        # A USD deliverable: the FBX-only knobs, the FBX/GLB verifier and the
        # rig-helper pass (it edits a written FBX) go.
        trigger, usd = by_target[
            "cmb000,animation_clips,bake_range,verify_deliverables,drop_rig_apparatus"
        ]
        self.assertEqual(trigger, "cmb004")
        self.assertEqual((usd("fbx"), usd("glb"), usd("usd")), (True, True, False))
        # The GLB-only dials.
        self.assertEqual(
            by_target["secondary_max_size"], ("cmb004", {"glb", "fbx_glb"})
        )
        trigger, rdo = by_target["uastc_rdo"]
        self.assertEqual(trigger, ["cmb004", "texture_file_type"])
        self.assertEqual(
            (rdo("glb", "ktx2"), rdo("fbx_glb", "ktx2+fallback"), rdo("fbx", "ktx2")),
            (True, True, False),
        )
        self.assertFalse(rdo("glb", "png"), "RDO is a KTX2 encode dial")
        trigger, keys = by_target["glb_key_tolerance"]
        self.assertEqual(trigger, ["cmb004", "optimize_level"])
        self.assertEqual(
            (keys("glb", "extremes"), keys("glb", None), keys("fbx", "extremes")),
            (True, False, False),
        )
        # The retired hand-rolled pair is gone for good.
        for name in ("cmb004", "_sync_glb_texture_combo"):
            self.assertFalse(hasattr(SceneExporterSlots, name), name)

    def test_preset_manager_adopts_the_panel_logger(self):
        """``cmb007_init`` hands the slots logger to the window's PresetManager.

        The manager's class-shared logger never reaches the panel's txt003
        sink (``setup_logging_redirect`` wires the SLOTS logger only), so its
        user-facing schema-drift warning -- "preset doesn't cover N new panel
        settings" -- was console-only. Pins the instance-scoped adoption AND
        its ordering: it must precede ``wire_combo``, whose active-preset
        restore is exactly the load that warns.
        """
        events = []

        class _Mgr:
            def use_logger(self, logger):
                events.append(("use_logger", logger))

            def setup(self, **kw):
                events.append(("setup", None))

            def exclude(self, *names):
                events.append(("exclude", names))

            def wire_combo(self, widget, placeholder=None):
                events.append(("wire_combo", widget))

        class _UI:
            presets = _Mgr()

        slots = SceneExporterSlots.__new__(SceneExporterSlots)
        slots.ui = _UI()
        slots.cmb007_init(_StubPresetSelector())

        kinds = [k for k, _ in events]
        self.assertIn("use_logger", kinds)
        self.assertLess(kinds.index("use_logger"), kinds.index("wire_combo"))
        self.assertIs(events[kinds.index("use_logger")][1], SceneExporterSlots.logger)

    def test_a_preset_asking_for_a_retired_naming_row_says_so(self):
        """A preset saved while the Version row and the Timestamp checkbox
        existed loads with both keys ignored -- no widget takes them -- and the
        Output Filename is per-export, so a series it versioned stopped
        versioning without a word. Picking it warns and names the spelling that
        replaces each; a preset storing them empty stays quiet (2026-09-15)."""
        stored = {
            "legacy": {"version": "{stem}_v{n:03d}", "chk004": True, "cmb004": 1},
            "current": {"version": "", "chk004": False, "cmb004": 1},
        }

        class _Mgr:
            def use_logger(self, logger):
                pass

            def setup(self, **kw):
                pass

            def exclude(self, *names):
                pass

            def wire_combo(self, widget, placeholder=None):
                pass

            def read(self, name):
                return stored.get(name)

        slots = SceneExporterSlots.__new__(SceneExporterSlots)
        slots.ui = SimpleNamespace(presets=_Mgr())
        combo = _StubPresetSelector(stored)
        slots.cmb007_init(combo)
        with self.assertLogs(slots.logger, level="WARNING") as caught:
            combo.activated.emit(0)
        said = "\n".join(caught.output)
        for expected in ("'legacy'", "*_v{n:03d}", "*_{date}_{time}"):
            self.assertIn(expected, said)
        with self.assertNoLogs(slots.logger, level="WARNING"):
            combo.activated.emit(1)

    # ------------------------------------------------------------------
    # optimize_keys forwarding to SmartBake
    # ------------------------------------------------------------------

    def test_optimize_keys_task_runs_when_requested(self):
        """The optimize_keys task is dispatched when present+True in the task dict.

        Tasks are dispatched by name (``TaskFactory._manage_context`` ->
        ``getattr(self, name)(value)``), so the observable contract here is
        that the ``optimize_keys`` method is invoked at all -- on a
        keyframe-less object it early-returns, leaving no effect to assert.
        What LEVEL it was invoked at is covered by TestOptimizeKeysLevels.
        """
        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        calls = []
        tm.optimize_keys = lambda *a, **k: calls.append(True)
        tm.run_tasks({"optimize_keys": True})
        self.assertTrue(calls, "optimize_keys task should run when present and True")

    def test_the_exports_key_tasks_leave_a_layer_to_its_owner(self):
        """The export's Optimize Keys and Snap Keys work base-layer curves only.

        SmartBake optimizes the override layer it creates, and a production
        bake holds millions of keys the tasks would otherwise re-scan for
        nothing. ``objects_to_curves`` walks layers by default, so the tasks
        opt out explicitly: a layer key at 4.5 is left where it is while the
        base curve beside it is still processed.
        Added: 2026-09-15
        """
        cube = cmds.polyCube(name="LayeredExportCube")[0]
        cmds.setKeyframe(cube, attribute="rotateY", time=1, value=0)
        cmds.setKeyframe(cube, attribute="rotateY", time=7.5, value=90)
        layer = cmds.animLayer("export_owned_layer")
        cmds.animLayer(layer, edit=True, attribute=f"{cube}.translateX")
        cmds.setKeyframe(cube, attribute="translateX", time=1, value=0, animLayer=layer)
        cmds.setKeyframe(
            cube, attribute="translateX", time=4.5, value=5, animLayer=layer
        )
        (layer_curve,) = cmds.animLayer(layer, query=True, animCurves=True)
        base_curve = cmds.listConnections(f"{cube}.rotateY", type="animCurve")[0]
        tm = self.exporter.task_manager
        tm.objects = cmds.ls(cube, long=True)
        try:
            tm.snap_keys_to_frame()
            self.assertEqual(
                cmds.keyframe(base_curve, query=True, timeChange=True), [1.0, 8.0]
            )
            self.assertEqual(
                cmds.keyframe(layer_curve, query=True, timeChange=True), [1.0, 4.5]
            )
        finally:
            tm.run_deferred_restores()

    def test_optimize_keys_task_skipped_when_absent(self):
        """The optimize_keys task is not dispatched when absent from the dict.

        b000 filters out falsy checkbox values, so an unchecked optimize_keys
        never reaches run_tasks — the method must not be invoked.
        """
        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        calls = []
        tm.optimize_keys = lambda *a, **k: calls.append(True)
        tm.run_tasks({"set_linear_unit": "cm"})
        self.assertFalse(calls, "optimize_keys task should not run when absent")

    # ------------------------------------------------------------------
    # resolve_invalid_texture_paths
    # ------------------------------------------------------------------

    def test_resolve_invalid_texture_paths_in_definitions(self):
        """Verify resolve_invalid_texture_paths exists in task_definitions.

        New task added to resolve missing texture paths using
        MatUtils.resolve_path() before export.
        Added: 2026-03-04
        """
        defs = self.exporter.task_manager.task_definitions
        self.assertIn(
            "resolve_invalid_texture_paths",
            defs,
            "resolve_invalid_texture_paths should be in task_definitions",
        )

    def test_resolve_invalid_texture_paths_in_task_order(self):
        """Verify resolve_invalid_texture_paths is in TASK_ORDER between
        reassign_duplicate_materials and convert_to_relative_paths.
        Added: 2026-03-04
        """
        order = self.exporter.task_manager.TASK_ORDER
        self.assertIn("resolve_invalid_texture_paths", order)
        idx_resolve = order.index("resolve_invalid_texture_paths")
        idx_reassign = order.index("reassign_duplicate_materials")
        idx_convert = order.index("convert_to_relative_paths")
        self.assertGreater(idx_resolve, idx_reassign)
        self.assertLess(idx_resolve, idx_convert)

    def test_resolve_invalid_texture_paths_valid_paths_noop(self):
        """Verify resolve_invalid_texture_paths is a no-op when all paths are valid.

        When every texture path already exists on disk, no remapping should
        occur and no warnings should be logged.
        Added: 2026-03-04
        """
        # Create a real texture file
        tex_path = os.path.join(self.temp_dir, "valid_texture.png")
        with open(tex_path, "w") as f:
            f.write("dummy")

        shader = cmds.shadingNode("lambert", asShader=True)
        file_node = cmds.shadingNode("file", asTexture=True)
        cmds.connectAttr(f"{file_node}.outColor", f"{shader}.color")
        cmds.setAttr(f"{file_node}.fileTextureName", tex_path, type="string")
        _assign_shader(self.cube, shader)

        self.exporter.task_manager.objects = [cmds.ls(str(self.cube), l=True)[0]]

        # Capture warnings
        log_output = []
        handler = logging.Handler()
        handler.emit = lambda record: log_output.append(record)
        handler.setLevel(logging.WARNING)
        self.exporter.logger.addHandler(handler)

        self.exporter.task_manager.resolve_invalid_texture_paths()

        warnings = [r for r in log_output if r.levelno >= logging.WARNING]
        self.assertEqual(
            len(warnings), 0, "No warnings expected for valid texture paths"
        )
        self.exporter.logger.removeHandler(handler)

    def test_resolve_invalid_texture_paths_warns_on_missing(self):
        """Verify resolve_invalid_texture_paths logs a warning for unresolvable paths.

        When a texture path cannot be resolved by MatUtils.resolve_path,
        the task should log a warning with the file node name and broken path.
        Added: 2026-03-04
        """
        shader = cmds.shadingNode("lambert", asShader=True)
        file_node = cmds.shadingNode("file", asTexture=True)
        cmds.connectAttr(f"{file_node}.outColor", f"{shader}.color")
        cmds.setAttr(
            f"{file_node}.fileTextureName",
            "/nonexistent/path/missing_texture.png",
            type="string",
        )
        _assign_shader(self.cube, shader)

        self.exporter.task_manager.objects = [cmds.ls(str(self.cube), l=True)[0]]

        log_output = []
        handler = logging.Handler()
        handler.emit = lambda record: log_output.append(record)
        handler.setLevel(logging.WARNING)
        self.exporter.logger.addHandler(handler)

        self.exporter.task_manager.resolve_invalid_texture_paths()

        warnings = [r for r in log_output if r.levelno >= logging.WARNING]
        self.assertGreater(
            len(warnings), 0, "Expected warning for unresolvable texture path"
        )
        # Verify we mention the path
        all_msgs = " ".join(r.getMessage() for r in warnings)
        self.assertIn("missing_texture", all_msgs)
        self.exporter.logger.removeHandler(handler)

    # ------------------------------------------------------------------
    # convert_to_relative_paths — scoped to sourceimages; externals untouched
    # ------------------------------------------------------------------

    def _set_project(self, root):
        """Point the Maya project at ``root`` and restore it on teardown."""
        original_ws = cmds.workspace(q=True, rd=True)
        self.addCleanup(lambda: cmds.workspace(original_ws, openWorkspace=True))
        cmds.workspace(root, openWorkspace=True)
        sourceimages = os.path.join(root, "sourceimages")
        os.makedirs(sourceimages, exist_ok=True)
        return sourceimages

    def _assign_texture(self, node_path, tex_path):
        """Create a lambert+file driven by ``tex_path`` and assign to ``node``."""
        shader = cmds.shadingNode("lambert", asShader=True)
        file_node = cmds.shadingNode("file", asTexture=True)
        cmds.connectAttr(f"{file_node}.outColor", f"{shader}.color")
        cmds.setAttr(
            f"{file_node}.fileTextureName", tex_path.replace("\\", "/"), type="string"
        )
        _assign_shader(node_path, shader)
        return file_node

    @staticmethod
    def _set_ftn_verbatim(file_node, path):
        """Store ``fileTextureName`` verbatim via MPlug.setString.

        ``cmds.setAttr`` auto-expands a workspace-resolvable relative path to
        absolute (probe-proven); the MPlug route bypasses that — it is how
        ``stage_textures_relative`` writes the relative paths production
        scenes actually carry.
        """
        import maya.api.OpenMaya as om

        sel = om.MSelectionList()
        sel.add(file_node)
        om.MFnDependencyNode(sel.getDependNode(0)).findPlug(
            "fileTextureName", False
        ).setString(path)

    def test_convert_to_relative_leaves_external_textures_alone(self):
        """An external texture keeps its absolute path — never copied, never
        rewritten.

        The task is scoped to what already lives under sourceimages: an
        external reference is usually deliberate (a shared library, another
        project's published maps), so consolidating it into this project would
        silently relocate the user's asset. Relativizing it in place is not an
        alternative either — the path would resolve to a file that isn't under
        sourceimages and would break the material on import.
        (Was: the task copied externals in. Changed 2026-08-20.)
        """
        sourceimages = self._set_project(self.temp_dir)

        external_dir = os.path.join(self.temp_dir, "external")
        os.makedirs(external_dir, exist_ok=True)
        external_tex = os.path.join(external_dir, "wood_ext.png")
        with open(external_tex, "wb") as f:
            f.write(b"PNGDATA")

        file_node = self._assign_texture(self.cube, external_tex)
        self.exporter.task_manager.objects = [cmds.ls(str(self.cube), l=True)[0]]
        self.exporter.task_manager.convert_to_relative_paths()

        self.assertFalse(
            os.path.isfile(os.path.join(sourceimages, "wood_ext.png")),
            "an external texture must not be copied into sourceimages",
        )
        self.assertEqual(
            os.path.normpath(cmds.getAttr(f"{file_node}.fileTextureName")),
            os.path.normpath(external_tex),
            "the external link must survive the task unchanged",
        )
        self.assertTrue(
            os.path.isfile(external_tex), "and the source file stays where it was"
        )

    def test_convert_to_relative_still_relativizes_in_project_textures(self):
        """The other half of the scope: a texture already under sourceimages IS
        rewritten, subfolder preserved."""
        sourceimages = self._set_project(self.temp_dir)
        sub = os.path.join(sourceimages, "wood")
        os.makedirs(sub, exist_ok=True)
        tex = os.path.join(sub, "bark.png")
        with open(tex, "wb") as f:
            f.write(b"PNGDATA")

        file_node = self._assign_texture(self.cube, tex)
        self.exporter.task_manager.objects = [cmds.ls(str(self.cube), l=True)[0]]
        self.exporter.task_manager.convert_to_relative_paths()

        self.assertEqual(
            cmds.getAttr(f"{file_node}.fileTextureName"),
            "sourceimages/wood/bark.png",
        )

    def test_convert_to_relative_never_touches_a_same_named_sourceimages_file(self):
        """A same-named file in sourceimages is not a collision any more —
        nothing is copied, so the existing texture cannot be clobbered and the
        node cannot be rebound to it.

        (Was a copy-collision guard; the copy is gone, but the invariant it
        protected — never silently rebind a material to a different file that
        happens to share a basename — still needs pinning. Changed 2026-08-20.)
        """
        sourceimages = self._set_project(self.temp_dir)

        existing = os.path.join(sourceimages, "shared.png")
        with open(existing, "wb") as f:
            f.write(b"ORIGINAL-SOURCEIMAGES-CONTENT")

        external_dir = os.path.join(self.temp_dir, "external")
        os.makedirs(external_dir, exist_ok=True)
        external_tex = os.path.join(external_dir, "shared.png")
        with open(external_tex, "wb") as f:
            f.write(b"DIFFERENT")

        file_node = self._assign_texture(self.cube, external_tex)
        self.exporter.task_manager.objects = [cmds.ls(str(self.cube), l=True)[0]]
        self.exporter.task_manager.convert_to_relative_paths()

        with open(existing, "rb") as f:
            self.assertEqual(
                f.read(),
                b"ORIGINAL-SOURCEIMAGES-CONTENT",
                "the existing sourceimages texture must be untouched",
            )
        self.assertEqual(
            os.path.normpath(cmds.getAttr(f"{file_node}.fileTextureName")),
            os.path.normpath(external_tex),
            "and the node must not be rebound to the same-named project file",
        )

    def test_copy_textures_skips_file_already_in_sourceimages_subfolder(self):
        """A texture already in a sourceimages SUBFOLDER is left in place, not
        copied to the root.

        Guards the "already under sourceimages" check (must be any-depth, not
        root-only) — the same duplicate-copy bug fixed in the HDR Manager add
        flow.  Added: 2026-06-16
        """
        from mayatk.mat_utils._mat_utils import MatUtils

        sourceimages = self._set_project(self.temp_dir)
        sub = os.path.join(sourceimages, "textures")
        os.makedirs(sub, exist_ok=True)
        tex = os.path.join(sub, "wood.png")
        with open(tex, "wb") as f:
            f.write(b"SUBFOLDER-TEX")

        node = self._assign_texture(self.cube, tex)
        result = MatUtils.copy_textures_to_sourceimages(file_nodes=[node])

        # Nothing copied — the file is already under sourceimages.
        self.assertEqual(result, [])
        # Not duplicated into the root.
        self.assertFalse(os.path.isfile(os.path.join(sourceimages, "wood.png")))
        # Original subfolder file untouched.
        self.assertTrue(os.path.isfile(tex))

    def test_copy_textures_skips_within_batch_basename_collision(self):
        """Two different externals sharing a basename must not both be copied.

        The copy into sourceimages is flat (by basename), so queuing both would
        land them on one destination — a silent (threaded) clobber and
        wrong-file rebind.  Only the first is copied; the other is skipped.
        Added: 2026-06-16
        """
        from mayatk.mat_utils._mat_utils import MatUtils

        sourceimages = self._set_project(self.temp_dir)

        ext_a = os.path.join(self.temp_dir, "a")
        ext_b = os.path.join(self.temp_dir, "b")
        os.makedirs(ext_a, exist_ok=True)
        os.makedirs(ext_b, exist_ok=True)
        tex_a = os.path.join(ext_a, "tex.png")
        tex_b = os.path.join(ext_b, "tex.png")
        with open(tex_a, "wb") as f:
            f.write(b"AAAA")  # size 4
        with open(tex_b, "wb") as f:
            f.write(b"BBBBBBBB")  # size 8 → different, a real collision

        node_a = self._assign_texture(self.cube, tex_a)
        node_b = self._assign_texture(self.sphere, tex_b)

        result = MatUtils.copy_textures_to_sourceimages(file_nodes=[node_a, node_b])

        # Only one of the colliding basenames was copied ...
        self.assertEqual(
            len(result), 1, "only one same-basename texture should be copied"
        )
        self.assertTrue(os.path.isfile(os.path.join(sourceimages, "tex.png")))
        # ... and both originals are intact (copy, not move; no clobber).
        self.assertTrue(os.path.isfile(tex_a))
        self.assertTrue(os.path.isfile(tex_b))

    # ------------------------------------------------------------------
    # Texture file-size check
    # ------------------------------------------------------------------

    def test_check_texture_file_size_in_definitions(self):
        """check_texture_file_size is a SpinBox check defaulting to 16 MB.

        Added: 2026-06-19.  Changed 2026-08-04: ComboBox (fixed size steps) →
        QLineEdit (free MB value).  Changed 2026-08-06: QLineEdit → SpinBox —
        a bounded MB budget is a number, and 0 displays as "OFF" instead of
        relying on an empty free-text field to disable the check.
        """
        defs = self.exporter.task_manager.check_definitions
        self.assertIn("check_texture_file_size", defs)
        entry = defs["check_texture_file_size"]
        self.assertEqual(entry["widget_type"], "SpinBox")
        self.assertEqual(entry["value_method"], "value")
        self.assertEqual(entry["setValue"], 16)
        # 0 is the OFF position, so it must be reachable and labelled as such.
        self.assertEqual(entry["set_limits"][0], 0)
        self.assertEqual(entry["setCustomDisplayValues"], {0: "OFF"})

    def test_check_texture_file_size_accepts_numeric_text(self):
        """The limit may arrive as a number or as numeric text.

        The spin box hands over an int, but the check is also driven from
        saved templates and direct calls, so '1' must still behave as 1 MB and
        a non-numeric value must skip the check with a warning rather than
        raising.
        Added: 2026-08-04
        """
        tex_path = os.path.join(self.temp_dir, "big_text_limit.png")
        with open(tex_path, "wb") as f:
            f.write(b"\0" * (2 * 1024 * 1024))  # 2 MB

        self._assign_texture(self.cube, tex_path)
        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        for limit in (1, "1"):
            passed, messages = tm.check_texture_file_size(limit)
            self.assertFalse(passed, f"{limit!r} must be applied as a 1 MB limit")
            self.assertTrue(any("big_text_limit.png" in m for m in messages))

        passed, _ = tm.check_texture_file_size("abc")
        self.assertTrue(passed, "non-numeric text must skip the check, not raise")

    def test_check_texture_file_size_off_passes(self):
        """OFF (None / 0) disables the check.

        Added: 2026-06-19
        """
        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]
        self.assertEqual(tm.check_texture_file_size(None), (True, []))
        self.assertEqual(tm.check_texture_file_size(0), (True, []))

    def test_check_texture_file_size_fails_on_oversized(self):
        """A texture larger than the limit fails the check.

        Added: 2026-06-19
        """
        tex_path = os.path.join(self.temp_dir, "big.png")
        with open(tex_path, "wb") as f:
            f.write(b"\0" * (2 * 1024 * 1024))  # 2 MB

        self._assign_texture(self.cube, tex_path)
        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        # 1 MB limit → the 2 MB texture is an offender.
        passed, messages = tm.check_texture_file_size(1)
        self.assertFalse(passed)
        self.assertTrue(any("big.png" in m for m in messages))

    def test_check_texture_file_size_passes_under_limit(self):
        """A texture under the limit passes the check with no messages.

        Added: 2026-06-19
        """
        tex_path = os.path.join(self.temp_dir, "small.png")
        with open(tex_path, "wb") as f:
            f.write(b"\0" * (512 * 1024))  # 0.5 MB

        self._assign_texture(self.cube, tex_path)
        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        passed, messages = tm.check_texture_file_size(16)
        self.assertTrue(passed)
        self.assertEqual(messages, [])

    def test_check_texture_file_size_ignores_missing_files(self):
        """Missing texture files are left to check_valid_paths, not failed here.

        Added: 2026-06-19
        """
        self._assign_texture(self.cube, "/nonexistent/huge_texture.png")
        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        passed, _ = tm.check_texture_file_size(1)
        self.assertTrue(passed)

    def test_check_texture_file_size_names_the_optimize_remedy(self):
        """An over-limit failure says what would fix it, by the state of the
        Optimize Textures dial the run used.

        Regression (production, 2026-09-13): the check failed right after an
        "Optimize" run and read as if optimization had not happened. It had,
        but without a size ceiling the pass never resamples, so nothing
        could bring a 57 MB map under the limit.

        Added: 2026-09-13
        """
        tex_path = os.path.join(self.temp_dir, "big_remedy.png")
        with open(tex_path, "wb") as f:
            f.write(b"\0" * (2 * 1024 * 1024))  # 2 MB
        self._assign_texture(self.cube, tex_path)
        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        def text(optimize, max_size):
            tm.run = tm.run.replace(optimize_textures=optimize)
            tm.run = tm.run.replace(texture_max_size=max_size)
            passed, messages = tm.check_texture_file_size(1)
            self.assertFalse(passed)
            return "\n".join(messages)

        try:
            self.assertIn("Optimize Textures is OFF", text(False, None))
            self.assertIn("no size ceiling", text(True, None))
            self.assertIn("clamped to 1024 px", text(True, 1024))
        finally:
            tm.run = tm.run.replace(optimize_textures=False)
            tm.run = tm.run.replace(texture_max_size=None)

    def test_check_texture_file_size_measures_what_the_deliverable_carries(self):
        """A GLB-only export ships no scene map, so the check steps aside.

        Regression (production, 2026-09-13): the check failed on a 57 MB source
        PNG that the GLB pass re-encodes to a 3.12 MB KTX2 -- for a GLB-only
        export the source bytes reach nothing that ships. The check hands its limit to the
        post-write image-bytes gate instead. FBX + GLB still gates, and names
        the FBX as the file carrying the maps.

        Added: 2026-09-13
        """
        tex_path = os.path.join(self.temp_dir, "big_carrier.png")
        with open(tex_path, "wb") as f:
            f.write(b"\0" * (2 * 1024 * 1024))  # 2 MB
        self._assign_texture(self.cube, tex_path)
        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        tm.run = tm.run.replace(output_format="glb")
        self.assertEqual(tm.check_texture_file_size(1), (True, []))
        # The limit perform_export hands to the post-write image-bytes gate,
        # parsed by the same rule the check applies to its row.
        limit_bytes = ptk.ExportProfile.texture_size_limit_bytes
        self.assertEqual(limit_bytes(1), 1024 * 1024)
        self.assertEqual(limit_bytes("16"), 16 * 1024 * 1024)
        for off in (None, 0, "", "OFF", "off", "abc"):
            self.assertIsNone(limit_bytes(off), repr(off))

        tm.run = tm.run.replace(output_format="fbx_glb")
        passed, messages = tm.check_texture_file_size(1)
        self.assertFalse(passed)
        self.assertIn("the FBX", messages[0])

        tm.run = tm.run.replace(output_format="fbx")
        passed, messages = tm.check_texture_file_size(1)
        self.assertFalse(passed)
        self.assertNotIn("the FBX", messages[0], "an FBX-only header needs no carrier")

    def test_check_texture_file_size_resolves_relative_paths(self):
        """Project-relative texture paths must be resolved, not skipped.

        The default-on convert_to_relative_paths task rewrites texture paths to
        workspace-relative form before checks run; a bare os.path.isfile would
        miss them (resolving against the CWD) and silently pass every texture.
        Added: 2026-06-19
        """
        sourceimages = self._set_project(self.temp_dir)
        # 2 MB texture in sourceimages, referenced by a RELATIVE path.
        big = os.path.join(sourceimages, "rel_big.png")
        with open(big, "wb") as f:
            f.write(b"\0" * (2 * 1024 * 1024))

        self._assign_texture(self.cube, "sourceimages/rel_big.png")
        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        passed, messages = tm.check_texture_file_size(1)
        self.assertFalse(passed, "relative path must be resolved and size-checked")
        self.assertTrue(any("rel_big.png" in m for m in messages))

    # ------------------------------------------------------------------
    # optimize_textures / check_texture_optimization — the Optimize pair
    # ------------------------------------------------------------------

    def _make_png(self, name, size=(256, 256), mode="RGB"):
        """Write a real PNG fixture (converted to *mode*) and return its path."""
        from PIL import Image

        path = os.path.join(self.temp_dir, name)
        img = Image.new("RGB", size, (128, 128, 128))
        if mode != "RGB":
            img = img.convert(mode)
        img.save(path)
        return path

    def test_optimize_textures_in_definitions_and_order(self):
        """The Optimize Textures combo + the Texture Output combo (the ONE
        "modify the scene's textures or not" control, read by convert_textures
        AND optimize_textures); the texture-processing pair is ordered LAST
        in the material phase (staged absolute paths must never be seen by
        convert_to_relative_paths).

        Added: 2026-08-14; combined with the old Max Texture Size row
        2026-08-20 — the pass switch and its ceiling are ONE combo, so a
        ceiling with nothing to apply it is unrepresentable.
        """
        tm = self.exporter.task_manager
        defs = tm.task_definitions
        self.assertIn("optimize_textures", defs)
        self.assertEqual(defs["optimize_textures"]["widget_type"], "ComboBox")
        # A fresh objectName ON PURPOSE: letting an old preset's bool restore
        # onto this combo would silently drop the preset's size ceiling; the
        # rename trips the PresetManager's uncovered-keys warning instead.
        self.assertEqual(defs["optimize_textures"]["object_name"], "texture_optimize")
        self.assertEqual(defs["texture_write_back"]["widget_type"], "ComboBox")
        # Data IS the write-back flag; index 0 (the default) is the
        # non-destructive mode.
        self.assertEqual(
            list(defs["texture_write_back"]["add"].values()), [False, True]
        )
        order = tm.TASK_ORDER
        self.assertLess(
            order.index("convert_to_relative_paths"), order.index("convert_textures")
        )
        self.assertLess(
            order.index("convert_textures"), order.index("optimize_textures")
        )
        # The write-back row is a mode flag popped by perform_export, never a
        # dispatched task.
        self.assertNotIn("texture_write_back", order)

    def test_optimize_textures_combo_values(self):
        """The combined combo's data decomposes into the two engine inputs:
        OFF first (index 0, falsy so the task filter drops the pass), plain
        True second (optimize, no resize), the pixel ceilings, then the
        template-budget sentinel LAST (combos persist by index). The old
        separate texture_max_size row is gone — its key survives only as the
        perform_export input b000 decomposes the choice into.

        Added: 2026-08-17 (as the Max Texture Size row); merged 2026-08-20.
        """
        tm = self.exporter.task_manager
        defs = tm.task_definitions
        self.assertNotIn("texture_max_size", defs)
        values = list(defs["optimize_textures"]["add"].values())
        self.assertEqual(values[0], 0)
        self.assertIs(values[1], True)
        self.assertEqual(values[-1], tm.TEXTURE_MAX_SIZE_TEMPLATE)
        self.assertEqual(values[2:-1], [512, 1024, 2048, 4096, 8192])
        self.assertNotIn("texture_max_size", tm.TASK_ORDER)
        # The Textures group order: the Texture Output gate row first, its
        # dependants beneath it, the two GLB-only dials last.
        keys = [k for k in defs if defs[k].get("group") == "Textures"]
        self.assertEqual(
            keys,
            [
                "texture_write_back",
                "convert_textures",
                "optimize_textures",
                "texture_file_type",
                "secondary_max_size",
                "uastc_rdo",
            ],
        )
        # The GLB key tolerance is the GLB half of Optimize Keys, so it sits
        # directly under it (2026-09-14) and defaults to the measured 1e-4.
        animation = [k for k in defs if defs[k].get("group") == "Animation"]
        self.assertEqual(
            animation[animation.index("optimize_keys") + 1], "glb_key_tolerance"
        )
        self.assertEqual(defs["glb_key_tolerance"]["setCurrentIndex"], 2)
        self.assertEqual(list(defs["glb_key_tolerance"]["add"].values())[2], 1e-4)

    def test_texture_size_clamp_resolution(self):
        """_texture_size_clamp maps the combo's data to MapOptimizer kwargs:
        unset/0/'OFF'/bool = no clamp, a pixel ceiling = max_size, the
        sentinel = enforce the template's size budget WITHOUT its POT rule
        (per-axis snapping breaks aspect) — a no-op with no template.

        Added: 2026-08-17
        """
        tm = self.exporter.task_manager
        template = next(iter(ptk.MapRegistry.instance().get_workflow_presets()))
        for off in (None, 0, "OFF", "off", "garbage", True, False):
            tm.run = tm.run.replace(texture_max_size=off)
            self.assertEqual(tm._texture_size_clamp(template), {}, repr(off))
        tm.run = tm.run.replace(texture_max_size=1024)
        self.assertEqual(tm._texture_size_clamp(None), {"max_size": 1024})
        tm.run = tm.run.replace(
            texture_max_size="2048"
        )  # a hand-edited template can send a str
        self.assertEqual(tm._texture_size_clamp(template)["max_size"], 2048)
        tm.run = tm.run.replace(texture_max_size=tm.TEXTURE_MAX_SIZE_TEMPLATE)
        self.assertEqual(
            tm._texture_size_clamp(template),
            {"enforce_budget": True, "force_pot": False},
        )
        self.assertEqual(tm._texture_size_clamp(None), {})
        tm.run = tm.run.replace(texture_max_size=None)

    def test_optimize_textures_max_size_clamps_staged_copy(self):
        """With Max Texture Size set the pass downsamples the staged copy
        (longest edge at the ceiling, aspect kept) — still non-destructive:
        the scene's source keeps its dimensions, and the paired check judges
        the staged state through the same clamp (fails before, passes after).

        Added: 2026-08-17
        """
        from PIL import Image

        tex = self._make_png("clamp_src_Normal.png", size=(512, 256))
        file_node = self._assign_texture(self.cube, tex)

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]
        tm.run = tm.run.replace(output_format="glb")
        tm.run = tm.run.replace(texture_write_back=False)
        tm.run = tm.run.replace(texture_max_size=128)
        try:
            passed, msgs = tm.check_texture_optimization(True)
            self.assertFalse(passed, "over-size source must fail the gate")
            self.assertTrue(any("clamp_src_Normal.png" in m for m in msgs))

            tm.optimize_textures(True)
            staged = cmds.getAttr(f"{file_node}.fileTextureName")
            self.assertNotEqual(
                os.path.normcase(staged), os.path.normcase(tex.replace("\\", "/"))
            )
            with Image.open(staged) as img:
                self.assertEqual(img.size, (128, 64), "longest edge clamped")
            with Image.open(tex) as img:
                self.assertEqual(img.size, (512, 256), "source never touched")
            passed, msgs = tm.check_texture_optimization(True)
            self.assertTrue(passed, msgs)
            tm.run_deferred_restores()
            self.assertEqual(
                os.path.normcase(cmds.getAttr(f"{file_node}.fileTextureName")),
                os.path.normcase(tex.replace("\\", "/")),
            )
        finally:
            tm.run = tm.run.replace(texture_max_size=None)

    def test_optimize_textures_template_budget_clamps_without_pot(self):
        """'Template Budget' enforces the template's size ceiling — and ONLY
        the ceiling: glTF 2.0's budget is 2048 + force_pot, but the exporter
        drops the POT rule (per-axis snapping would turn 3000x1000 into
        2048x512, breaking aspect), so the staged copy is 2048x683.

        Added: 2026-08-17
        """
        from PIL import Image

        tex = self._make_png("budget_src_Normal.png", size=(3000, 1000))
        file_node = self._assign_texture(self.cube, tex)
        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]
        tm.run = tm.run.replace(output_format="glb")
        tm.run = tm.run.replace(texture_write_back=False)
        tm.run = tm.run.replace(texture_max_size=tm.TEXTURE_MAX_SIZE_TEMPLATE)
        try:
            tm.optimize_textures("glTF 2.0")
            staged = cmds.getAttr(f"{file_node}.fileTextureName")
            with Image.open(staged) as img:
                self.assertEqual(img.size, (2048, 683), "ceiling only, aspect kept")
            passed, msgs = tm.check_texture_optimization("glTF 2.0")
            self.assertTrue(passed, msgs)
            tm.run_deferred_restores()
        finally:
            tm.run = tm.run.replace(texture_max_size=None)

    def test_optimize_textures_max_size_never_grows(self):
        """A ceiling above the source's size is a no-op: an already-optimal
        map under the clamp is not re-encoded or repathed.

        Added: 2026-08-17
        """
        tex = self._make_png("small_src.png", size=(64, 64))
        file_node = self._assign_texture(self.cube, tex)
        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]
        tm.run = tm.run.replace(output_format="glb")
        tm.run = tm.run.replace(texture_write_back=False)
        tm.run = tm.run.replace(texture_max_size=2048)
        try:
            tm.optimize_textures(True)
            self.assertEqual(
                os.path.normcase(cmds.getAttr(f"{file_node}.fileTextureName")),
                os.path.normcase(tex.replace("\\", "/")),
            )
            self.assertNotIn("optimize_textures", tm._deferred_restores)
        finally:
            tm.run = tm.run.replace(texture_max_size=None)

    def test_optimize_textures_stages_without_touching_scene_sources(self):
        """The generic pass fixes a map-type violation non-destructively: the
        palette-mode normal map is staged as RGB, the file node repointed for
        the write, dimensions NEVER resampled, and the deferred restore
        (post-write) puts the original path back and deletes the staging.

        Added: 2026-08-14
        """
        from PIL import Image

        # A palette-mode normal map — the per-map-type pass must coerce P->RGB
        # (palette transparency reads as alpha downstream).
        tex = self._make_png("opt_src_Normal.png", mode="P")
        size_before = os.path.getsize(tex)
        file_node = self._assign_texture(self.cube, tex)

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]
        tm.run = tm.run.replace(
            output_format="glb"
        )  # temp staging; also skips the mel embed query
        tm.run = tm.run.replace(texture_write_back=False)

        tm.optimize_textures(True)

        staged = cmds.getAttr(f"{file_node}.fileTextureName")
        self.assertNotEqual(
            os.path.normcase(staged), os.path.normcase(tex.replace("\\", "/"))
        )
        self.assertTrue(os.path.isfile(staged), "staged copy must exist")
        with Image.open(staged) as img:
            self.assertEqual(img.mode, "RGB", "map-type pass must coerce P->RGB")
            self.assertEqual(
                img.size, (256, 256), "the optimization pass must NEVER resize"
            )
        # Source untouched.
        self.assertEqual(os.path.getsize(tex), size_before)
        with Image.open(tex) as img:
            self.assertEqual(img.mode, "P")

        # Post-write: original path restored, temp staging deleted.
        tm.run_deferred_restores()
        self.assertEqual(
            os.path.normcase(cmds.getAttr(f"{file_node}.fileTextureName")),
            os.path.normcase(tex.replace("\\", "/")),
        )
        self.assertFalse(os.path.exists(staged), "temp staged copy must be cleaned up")

    def test_optimize_textures_template_never_resamples(self):
        """With a template selected the pass adopts the template's per-map-type
        spec — but its DeliveryBudget stays ADVISORY: dimensions are never
        resampled, whatever the template's budget says. The size dial lives in
        the Map Converter, deliberately not here.

        Added: 2026-08-14
        """
        from PIL import Image

        template = next(iter(ptk.MapRegistry.instance().get_workflow_presets()))
        tex = self._make_png("tpl_src_Normal.png", mode="P")
        file_node = self._assign_texture(self.cube, tex)

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]
        tm.run = tm.run.replace(output_format="glb")
        tm.run = tm.run.replace(texture_write_back=False)

        tm.optimize_textures(template)

        staged = cmds.getAttr(f"{file_node}.fileTextureName")
        self.assertTrue(os.path.isfile(staged))
        with Image.open(staged) as img:
            self.assertEqual(
                img.size,
                (256, 256),
                "a template's budget is advisory — the pass must never resample",
            )
        tm.run_deferred_restores()

    def test_optimize_textures_write_back_archives_and_overwrites(self):
        """Write-back mode persists: the source is optimized in place and the
        original is archived beside it in original_textures/.

        Added: 2026-08-14
        """
        from PIL import Image

        tex = self._make_png("writeback_src_Normal.png", mode="P")
        file_node = self._assign_texture(self.cube, tex)

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]
        tm.run = tm.run.replace(texture_write_back=True)

        tm.optimize_textures(True)

        with Image.open(tex) as img:
            self.assertEqual(img.mode, "RGB", "source must be optimized in place")
            self.assertEqual(img.size, (256, 256), "never resampled")
        archived = os.path.join(
            self.temp_dir, "original_textures", "writeback_src_Normal.png"
        )
        self.assertTrue(os.path.isfile(archived), "original must be archived")
        with Image.open(archived) as img:
            self.assertEqual(img.mode, "P")
        # Same name, same place — the node's path still resolves unchanged.
        self.assertEqual(
            os.path.normcase(cmds.getAttr(f"{file_node}.fileTextureName")),
            os.path.normcase(tex.replace("\\", "/")),
        )
        # Nothing staged → nothing to restore.
        self.assertNotIn("optimize_textures", tm._deferred_restores)

    def test_optimize_textures_leaves_already_optimal_sources_alone(self):
        """A map the pass would not change is not re-encoded, staged, or
        repathed. Re-encoding it would be pure churn — for a JPEG source a
        lossy generational copy — and in write-back mode a re-run would
        re-archive the already-optimized file over its true original.

        Added: 2026-08-14
        """
        tex = self._make_png("plain_src.png")  # RGB, no map-type suffix
        mtime = os.path.getmtime(tex)
        file_node = self._assign_texture(self.cube, tex)

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]
        tm.run = tm.run.replace(output_format="glb")
        tm.run = tm.run.replace(texture_write_back=False)

        tm.optimize_textures(True)
        self.assertEqual(
            os.path.normcase(cmds.getAttr(f"{file_node}.fileTextureName")),
            os.path.normcase(tex.replace("\\", "/")),
            "an already-optimal map must not be repathed",
        )
        self.assertEqual(os.path.getmtime(tex), mtime, "source must not be rewritten")
        self.assertNotIn("optimize_textures", tm._deferred_restores)

        # Write-back on an already-optimal map must not archive anything.
        tm.run = tm.run.replace(texture_write_back=True)
        tm.optimize_textures(True)
        self.assertFalse(
            os.path.exists(os.path.join(self.temp_dir, "original_textures")),
            "no archive may appear for a map the pass would not change",
        )

    def test_optimize_textures_recompresses_a_bloated_png_the_fbx_carries(self):
        """An inefficiently encoded PNG is re-encoded, even with nothing to change.

        Regression (production, 2026-09-13): a 57.34 MB normal map shipped
        as-is under Optimize -- already RGB and 8-bit, so its plan was empty --
        while a plain re-encode wrote the same pixels at 24.10 MB. When the
        deliverable carries the scene's maps (not GLB-only, whose GLB pass
        re-encodes every map itself) a PNG is re-encoded, and the copy ships
        only when it saves ``RECOMPRESS_MIN_SAVING``. Write-back never
        recompresses: a re-run would archive the re-encode over the original.

        Added: 2026-09-13
        """
        from PIL import Image

        pixels = Image.new("RGB", (256, 256), (128, 128, 128))
        bloated = os.path.join(self.temp_dir, "bloated_src.png")
        pixels.save(bloated, compress_level=0)
        tight = os.path.join(self.temp_dir, "tight_src.png")
        ptk.ImgUtils.save_image(pixels, tight, optimize=True)
        bloated_node = self._assign_texture(self.cube, bloated)
        tight_node = self._assign_texture(self.sphere, tight)
        mtime = os.path.getmtime(bloated)

        def path_of(node):
            return os.path.normcase(cmds.getAttr(f"{node}.fileTextureName"))

        tm = self.exporter.task_manager
        tm.objects = cmds.ls([str(self.cube), str(self.sphere)], long=True)
        tm.run = tm.run.replace(
            export_path=""
        )  # nothing durable to stage beside: temp staging
        tm.run = tm.run.replace(output_format="fbx")
        tm.run = tm.run.replace(texture_write_back=False)
        try:
            tm.optimize_textures(True)
            staged = cmds.getAttr(f"{bloated_node}.fileTextureName")
            self.assertNotEqual(os.path.normcase(staged), os.path.normcase(bloated))
            self.assertLess(os.path.getsize(staged), os.path.getsize(bloated) / 2)
            with Image.open(staged) as img:
                self.assertEqual((img.size, img.mode), ((256, 256), "RGB"))
            self.assertEqual(
                path_of(tight_node),
                os.path.normcase(tight.replace("\\", "/")),
                "a re-encode that saves nothing must ship the source",
            )
            self.assertEqual(os.path.getmtime(bloated), mtime, "source untouched")
            passed, messages = tm.check_texture_optimization(True)
            self.assertTrue(passed, messages)
            tm.run_deferred_restores()
            self.assertEqual(
                path_of(bloated_node), os.path.normcase(bloated.replace("\\", "/"))
            )

            tm.run = tm.run.replace(output_format="glb")
            tm.optimize_textures(True)
            self.assertEqual(
                path_of(bloated_node),
                os.path.normcase(bloated.replace("\\", "/")),
                "GLB-only: the GLB pass re-encodes every map itself",
            )
            self.assertNotIn("optimize_textures", tm._deferred_restores)

            tm.run = tm.run.replace(output_format="fbx")
            tm.run = tm.run.replace(texture_write_back=True)
            tm.optimize_textures(True)
            self.assertEqual(os.path.getmtime(bloated), mtime, "write-back skips it")
            self.assertFalse(
                os.path.exists(os.path.join(self.temp_dir, "original_textures"))
            )
        finally:
            tm.run_deferred_restores()

    def test_check_texture_optimization_gates_and_clears_after_task(self):
        """The paired check fails on an unoptimized source, passes once the
        task has staged the fix (checks run after tasks and read the CURRENT
        node paths), and skips cleanly when off.

        Added: 2026-08-14
        """
        tex = self._make_png("gate_src_Normal.png", mode="P")
        self._assign_texture(self.cube, tex)

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]
        tm.run = tm.run.replace(output_format="glb")
        tm.run = tm.run.replace(texture_write_back=False)

        self.assertEqual(tm.check_texture_optimization(None), (True, []))
        self.assertEqual(tm.check_texture_optimization(False), (True, []))

        passed, messages = tm.check_texture_optimization(True)
        self.assertFalse(passed)
        self.assertTrue(any("gate_src_Normal.png" in m for m in messages))

        tm.optimize_textures(True)
        passed, messages = tm.check_texture_optimization(True)
        self.assertTrue(passed, f"staged state must satisfy the pass: {messages}")
        tm.run_deferred_restores()

    def test_optimize_textures_predicted_name_collision_stages_distinct_files(self):
        """Two DIFFERENT source basenames that a container change collapses
        onto the SAME predicted output name must never overwrite one
        another on disk.

        Bug: the collision guard keyed the alt-subdir decision on the
        SOURCE basename (``collide_src.jpg`` vs ``collide_src.tga`` never
        collide by that key), so both landed in the SAME staging dir; the
        SECOND ``optimize_map`` call then silently overwrote the FIRST's
        already-written file — after the first entry's node had already
        been repointed at it — so the export shipped the second texture's
        pixels under the first texture's file node.

        Added: 2026-08-14
        """
        from PIL import Image

        template = next(iter(ptk.MapRegistry.instance().get_workflow_presets()))

        # Same stem, different source containers -- the template's per-map
        # default container (png) unifies both onto "collide_src.png".
        jpg_path = os.path.join(self.temp_dir, "collide_src.jpg")
        Image.new("RGB", (256, 256), (200, 30, 30)).save(jpg_path, format="JPEG")
        tga_path = os.path.join(self.temp_dir, "collide_src.tga")
        Image.new("RGB", (256, 256), (30, 30, 200)).save(tga_path, format="TGA")

        jpg_node = self._assign_texture(self.cube, jpg_path)
        tga_node = self._assign_texture(self.sphere, tga_path)

        tm = self.exporter.task_manager
        tm.objects = cmds.ls([str(self.cube), str(self.sphere)], long=True)
        tm.run = tm.run.replace(output_format="glb")
        tm.run = tm.run.replace(texture_write_back=False)

        tm.optimize_textures(template)

        jpg_staged = cmds.getAttr(f"{jpg_node}.fileTextureName")
        tga_staged = cmds.getAttr(f"{tga_node}.fileTextureName")

        self.assertNotEqual(
            os.path.normcase(jpg_staged),
            os.path.normcase(tga_staged),
            "the two colliding sources must not end up repointed at the "
            "SAME staged file",
        )
        self.assertTrue(os.path.isfile(jpg_staged))
        self.assertTrue(os.path.isfile(tga_staged))

        with Image.open(jpg_staged) as img:
            r, _g, b = img.convert("RGB").getpixel((0, 0))
        self.assertGreater(
            r,
            b,
            "the JPG-sourced staged file must carry the JPG's OWN (red) "
            "pixels, not the TGA's — the overwritten-after-repathing bug "
            "would put the TGA's (blue) pixels here instead",
        )
        with Image.open(tga_staged) as img:
            r, _g, b = img.convert("RGB").getpixel((0, 0))
        self.assertGreater(
            b, r, "the TGA-sourced staged file must carry the TGA's OWN (blue) pixels"
        )

        tm.run_deferred_restores()

    def test_optimize_textures_write_back_predicted_collision_skips_before_archiving(
        self,
    ):
        """Write-back mode has no alt-subdir escape hatch (it writes into
        each source's own folder by design), so a predicted-name collision
        must be caught BEFORE ``optimize_map`` runs: the loser is skipped
        outright rather than having its original archived into
        ``original_textures/`` while its node keeps pointing at the
        now-moved path (a broken reference the export would ship).

        Added: 2026-08-14
        """
        from PIL import Image

        template = next(iter(ptk.MapRegistry.instance().get_workflow_presets()))

        jpg_path = os.path.join(self.temp_dir, "wb_collide.jpg")
        Image.new("RGB", (256, 256), (200, 30, 30)).save(jpg_path, format="JPEG")
        tga_path = os.path.join(self.temp_dir, "wb_collide.tga")
        Image.new("RGB", (256, 256), (30, 30, 200)).save(tga_path, format="TGA")

        jpg_node = self._assign_texture(self.cube, jpg_path)
        tga_node = self._assign_texture(self.sphere, tga_path)

        tm = self.exporter.task_manager
        tm.objects = cmds.ls([str(self.cube), str(self.sphere)], long=True)
        tm.run = tm.run.replace(texture_write_back=True)

        tm.optimize_textures(template)

        jpg_ftn = cmds.getAttr(f"{jpg_node}.fileTextureName")
        tga_ftn = cmds.getAttr(f"{tga_node}.fileTextureName")

        # Whichever wins the predicted name, BOTH nodes must resolve to a
        # real file — never a path an archive step moved out from under an
        # unrepathed node.
        self.assertTrue(
            os.path.isfile(jpg_ftn), f"jpg node points at a missing file: {jpg_ftn}"
        )
        self.assertTrue(
            os.path.isfile(tga_ftn), f"tga node points at a missing file: {tga_ftn}"
        )

    def test_tiled_representative_udim_and_uvtile_use_distinct_first_tiles(self):
        """``<udim>`` and ``<uvtile>`` are NOT interchangeable — folding both
        onto "1001" pointed a ``<uvtile>`` set at a tile name that was never
        written (Blender's uvtile numbering is ``u1_v1``, not "1001").

        Added: 2026-08-14
        """
        tm = self.exporter.task_manager
        udim_path = os.path.join(self.temp_dir, "tex.<UDIM>.png")
        self.assertEqual(
            os.path.normcase(tm._tiled_representative(udim_path)),
            os.path.normcase(os.path.join(self.temp_dir, "tex.1001.png")),
        )
        uvtile_path = os.path.join(self.temp_dir, "tex.<uvtile>.png")
        self.assertEqual(
            os.path.normcase(tm._tiled_representative(uvtile_path)),
            os.path.normcase(os.path.join(self.temp_dir, "tex.u1_v1.png")),
        )

    def test_tiled_representative_frame_token_globs_first_existing_frame(self):
        """``<f>`` has no fixed "first" value — it must glob for whatever
        frame actually exists on disk, and report None (not a fabricated
        path) when none do.

        Added: 2026-08-14
        """
        tm = self.exporter.task_manager
        for frame in ("0003", "0004"):
            with open(os.path.join(self.temp_dir, f"seq.{frame}.exr"), "wb") as f:
                f.write(b"EXRDATA")

        found = tm._tiled_representative(os.path.join(self.temp_dir, "seq.<f>.exr"))
        self.assertEqual(
            os.path.normcase(found),
            os.path.normcase(os.path.join(self.temp_dir, "seq.0003.exr")),
            "must glob for the first frame actually on disk, not assume 1001",
        )

        missing = tm._tiled_representative(os.path.join(self.temp_dir, "nope.<f>.exr"))
        self.assertIsNone(missing, "no frame file on disk must report None, not a path")

    def test_export_texture_sources_frame_token_resolves_and_logs_missing(self):
        """Integration: a ``<f>`` file node with frames on disk resolves to the
        first one; a ``<f>`` file node with none is skipped and logged (never
        silently dropped, and never collapsed onto "1001" like a UDIM would
        be).

        ``MatUtils.resolve_path`` is patched to pass the raw (token-bearing)
        path straight through — it only special-cases ``<UDIM>`` existence
        today (a separate, backlogged gap: ``resolve_path(search=False)``
        returns None for a literal ``<f>``/``<uvtile>`` path, dropping the
        node before this method's tiled handling ever runs), and this test's
        job is the wiring in THIS method, not that upstream gap.

        Added: 2026-08-14
        """
        sourceimages = self._set_project(self.temp_dir)
        for frame in ("0010", "0011"):
            with open(os.path.join(sourceimages, f"found_seq.{frame}.exr"), "wb") as f:
                f.write(b"EXRDATA")

        self._assign_texture(self.cube, os.path.join(sourceimages, "found_seq.<f>.exr"))
        missing_node = self._assign_texture(
            self.sphere, os.path.join(sourceimages, "missing_seq.<f>.exr")
        )

        tm = self.exporter.task_manager
        tm.objects = cmds.ls([str(self.cube), str(self.sphere)], long=True)

        with patch(
            "mayatk.env_utils.scene_exporter._task_data.MatUtils.resolve_path",
            side_effect=lambda path, search=True: os.path.expandvars(path),
        ):
            with self.assertLogs(tm.logger, level="INFO") as cm:
                sources = tm._export_texture_sources(include_tiled=True)

        resolved_paths = {os.path.normcase(e["path"]) for e in sources.values()}
        self.assertIn(
            os.path.normcase(os.path.join(sourceimages, "found_seq.0010.exr")),
            resolved_paths,
        )
        self.assertTrue(
            all("missing_seq" not in p for p in resolved_paths),
            f"the no-frame-on-disk source must not appear: {resolved_paths}",
        )
        self.assertTrue(
            any(missing_node in m for m in cm.output),
            f"the skipped <f>-with-no-frame node must be logged: {cm.output}",
        )

    def test_export_texture_sources_resolves_every_tile_and_frame_token(self):
        """Integration with NOTHING mocked: ``<UDIM>``, ``<uvtile>`` and
        ``<f>`` file nodes all survive the ``if not resolved: continue`` gate
        and land on their own representative file.

        Bug: ``MatUtils._texture_exists`` -- the primitive behind
        ``resolve_path(search=False)`` -- substituted only the literal
        ``<UDIM>`` before ``os.path.exists``, so every other token failed that
        check and the node was dropped HERE, before ``_tiled_representative``
        ever ran. ``<f>`` frame-sequence nodes are the real Maya case this
        silently starved. The sibling test above deliberately patches
        ``resolve_path`` to pin this method's wiring in isolation; this one
        must not, because that upstream gate is exactly what it covers.

        Covers all SIX tokens since 2026-08-25. This method used to carry a
        private tiled-detection regex listing three of them, so ``<u>_<v>``
        and ``<frame>`` resolved upstream and then arrived here UNTILED --
        past the representative collapse, into the single-file path, and out
        again unclassified. Detection and collapse both read the one token
        table now (``MatUtils.has_path_token`` / ``probe_texture_path``), so
        the set a token denotes cannot depend on which of the two asked.

        Added: 2026-08-17
        """
        sourceimages = self._set_project(self.temp_dir)
        for name in (
            "tex.1001.png",
            "tex.u1_v1.png",
            "seq.0010.exr",
            "seq.0011.exr",
            # Distinct bases for the two spellings added 2026-08-25: <u>_<v>
            # denotes the SAME tile name as <uvtile>, so sharing a base would
            # collapse both nodes onto one source entry and prove nothing.
            "pair.u1_v1.png",
            "anim.0020.exr",
        ):
            with open(os.path.join(sourceimages, name), "wb") as f:
                f.write(b"DATA")

        nodes = {
            label: self._assign_texture(
                cmds.polyCube(name=f"TokenGeo_{label}")[0],
                os.path.join(sourceimages, pattern),
            )
            for label, pattern in (
                ("udim", "tex.<UDIM>.png"),
                ("uvtile", "tex.<uvtile>.png"),
                ("frame", "seq.<f>.exr"),
                ("missing_frame", "gone.<f>.exr"),
                # The two the private regex did not list (BACKLOG item (c)).
                ("uv_pair", "pair.<u>_<v>.png"),
                ("frame_alt", "anim.<frame>.exr"),
            )
        }

        tm = self.exporter.task_manager
        tm.objects = cmds.ls(cmds.ls("TokenGeo_*", type="transform"), long=True)

        sources = tm._export_texture_sources(include_tiled=True)
        by_name = {os.path.basename(e["path"]): e for e in sources.values()}

        self.assertIn("tex.1001.png", by_name, f"<UDIM> regressed: {list(by_name)}")
        self.assertFalse(tm._is_tiled_path("plain.png"), "premise")
        self.assertIn(
            "tex.u1_v1.png",
            by_name,
            f"<uvtile> must resolve to its OWN first tile: {list(by_name)}",
        )
        self.assertIn(
            "seq.0010.exr",
            by_name,
            f"<f> must glob to the first frame on disk: {list(by_name)}",
        )
        for label, name in (
            ("udim", "tex.1001.png"),
            ("uvtile", "tex.u1_v1.png"),
            ("frame", "seq.0010.exr"),
            ("uv_pair", "pair.u1_v1.png"),
            ("frame_alt", "anim.0020.exr"),
        ):
            self.assertEqual(by_name[name]["nodes"], [nodes[label]])
            self.assertTrue(by_name[name]["tiled"])
        self.assertTrue(
            all("gone" not in n for n in by_name),
            f"a <f> pattern with no frame on disk stays out: {list(by_name)}",
        )

    # ------------------------------------------------------------------
    # check_valid_paths — scoped to the textures that actually ship
    # ------------------------------------------------------------------

    def test_check_valid_paths_ignores_unassigned_file_nodes(self):
        """File nodes outside the export materials must not be reported.

        Bug: the check scanned every ``file`` node in the scene, so it flagged
        the Arnold skydome's HDR (already dropped by exclude_hdr) and the
        orphaned file nodes left behind when reassign_duplicate_materials
        deletes a duplicate shader — neither ever reaches the FBX.
        Added: 2026-07-29
        """
        sourceimages = self._set_project(self.temp_dir)
        good = os.path.join(sourceimages, "assigned.png")
        with open(good, "wb") as f:
            f.write(b"PNGDATA")
        self._assign_texture(self.cube, good)

        # A stray file node with a broken path, connected to nothing.
        stray = cmds.shadingNode("file", asTexture=True, name="stray_hdr_file")
        cmds.setAttr(
            f"{stray}.fileTextureName", "/nonexistent/machine_shop.hdr", type="string"
        )

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        passed, messages = tm.check_valid_paths()
        self.assertTrue(
            passed, f"unassigned file node must not fail the check: {messages}"
        )
        self.assertFalse(any(stray in m for m in messages))

    def test_check_valid_paths_flags_missing_export_texture(self):
        """A missing texture on an export material still fails the check.

        Added: 2026-07-29
        """
        self._set_project(self.temp_dir)
        file_node = self._assign_texture(self.cube, "/nonexistent/wood_missing.png")

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        passed, messages = tm.check_valid_paths()
        self.assertFalse(passed)
        self.assertTrue(any("wood_missing.png" in m for m in messages))
        self.assertTrue(any(file_node in m for m in messages))

    def test_check_valid_paths_groups_nodes_sharing_a_path(self):
        """Several file nodes on one missing path collapse into one message.

        Bug: a material carrying duplicate file nodes for the same map emitted
        one identical ERROR line per node, flooding the export log.
        Added: 2026-07-29
        """
        self._set_project(self.temp_dir)
        missing = "/nonexistent/shared_map.png"
        shader = cmds.shadingNode("lambert", asShader=True)
        nodes = []
        for attr in ("color", "transparency"):
            file_node = cmds.shadingNode("file", asTexture=True)
            cmds.setAttr(f"{file_node}.fileTextureName", missing, type="string")
            cmds.connectAttr(f"{file_node}.outColor", f"{shader}.{attr}")
            nodes.append(file_node)
        _assign_shader(self.cube, shader)

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        passed, messages = tm.check_valid_paths()
        self.assertFalse(passed)
        entries = [m for m in messages if "shared_map.png" in m]
        self.assertEqual(len(entries), 1, f"expected one grouped entry: {messages}")
        for node in nodes:
            self.assertIn(node, entries[0])

    def test_check_valid_paths_rejects_a_basename_only_match(self):
        """A node pointing at a stale directory is missing, even if the basename
        exists under sourceimages.

        MatUtils.resolve_path defaults to hunting for the texture by basename —
        correct for the repair task that writes the result back, wrong for a
        validity gate, which would then pass a link the FBX still ships broken.
        Added: 2026-07-29
        """
        sourceimages = self._set_project(self.temp_dir)
        decoy = os.path.join(sourceimages, "stale.png")
        with open(decoy, "wb") as f:
            f.write(b"PNGDATA")

        stale_ref = os.path.join(self.temp_dir, "gone_dir", "stale.png")
        self._assign_texture(self.cube, stale_ref)
        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        passed, messages = tm.check_valid_paths()
        self.assertFalse(
            passed, f"basename-only match must not validate the path: {messages}"
        )

    def test_export_file_node_cache_clears_with_the_materials_cache(self):
        """The two derived caches must never describe different material sets.

        _get_export_file_nodes is derived from _get_all_materials, so a lone
        `_cached_materials = None` would leave the file-node cache pinned to
        materials that no longer exist.
        Added: 2026-07-29
        """
        sourceimages = self._set_project(self.temp_dir)
        tex = os.path.join(sourceimages, "cached.png")
        with open(tex, "wb") as f:
            f.write(b"PNGDATA")
        file_node = self._assign_texture(self.cube, tex)

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]
        self.assertIn(file_node, tm._get_export_file_nodes())  # populates the cache

        # Reassigning objects must drop BOTH caches, not just the materials one.
        tm.objects = []
        self.assertIsNone(tm._cached_materials)
        self.assertIsNone(tm._cached_export_file_nodes)
        self.assertEqual(tm._get_export_file_nodes(), [])

    def test_check_valid_paths_resolves_udim_tokens(self):
        """A <UDIM> path whose first tile exists must pass.

        Bug: the hand-rolled lookup compared the literal ``<UDIM>`` path against
        disk, so every tiled texture was reported missing.
        Added: 2026-07-29
        """
        sourceimages = self._set_project(self.temp_dir)
        tile = os.path.join(sourceimages, "tiled.1001.png")
        with open(tile, "wb") as f:
            f.write(b"PNGDATA")

        self._assign_texture(self.cube, os.path.join(sourceimages, "tiled.<UDIM>.png"))
        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        passed, messages = tm.check_valid_paths()
        self.assertTrue(passed, f"UDIM path must resolve via tile 1001: {messages}")

    def test_check_valid_paths_accepts_a_rule_relative_path(self):
        """A bare name resolving through the sourceImages RULE is not missing.

        Maya resolves a relative .ftn against the project ROOT first and the
        rule second; the gate only ever asked the root
        (``cmds.workspace(expandName=...)``), so the rule-relative form the
        Texture Path Editor emitted from 2026-08-18 was reported as a missing
        texture on every export of a normalized scene.
        Added: 2026-08-25
        """
        sourceimages = self._set_project(self.temp_dir)
        tex = os.path.join(sourceimages, "rule_rel.png")
        with open(tex, "wb") as f:
            f.write(b"PNGDATA")
        file_node = self._assign_texture(self.cube, tex)
        self._set_ftn_verbatim(file_node, "rule_rel.png")

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        original_cwd = os.getcwd()
        os.chdir(self.temp_dir)  # the project root, as set_workspace leaves it
        try:
            _passed, messages = tm.check_valid_paths()
        finally:
            os.chdir(original_cwd)

        self.assertFalse(
            any("Missing Texture" in m for m in messages),
            f"a rule-relative path is one Maya loads, not a missing one: {messages}",
        )

    def test_resolve_invalid_texture_paths_leaves_a_rule_relative_path_alone(self):
        """The repair task must not rebind a path that already resolves.

        It shares ``check_valid_paths``' gate, so a rule-relative path read as
        broken and was rebound by basename to an ABSOLUTE path -- undoing the
        panel's normalization on every export, with a WARNING per texture.
        Added: 2026-08-25
        """
        sourceimages = self._set_project(self.temp_dir)
        tex = os.path.join(sourceimages, "rebind_me.png")
        with open(tex, "wb") as f:
            f.write(b"PNGDATA")
        file_node = self._assign_texture(self.cube, tex)
        self._set_ftn_verbatim(file_node, "rebind_me.png")

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]
        tm.resolve_invalid_texture_paths()

        self.assertEqual(
            cmds.getAttr(f"{file_node}.fileTextureName"),
            "rebind_me.png",
            "a resolving path was rebound by name -- the normalization is lost",
        )

    def test_convert_to_relative_paths_upgrades_a_rule_relative_path(self):
        """The stored form is ROOT-relative: ``sourceimages/foo.png``.

        That is how Maya itself spells a relative texture path and the only
        form the FBX plug-in can locate at write time (it resolves relative
        paths against the process CWD, which ``set_workspace`` aligns with
        the project root -- probe-proven: a bare rule-relative name is NOT
        embedded, the root-relative one is).
        Added: 2026-08-25
        """
        sourceimages = self._set_project(self.temp_dir)
        tex = os.path.join(sourceimages, "upgrade_me.png")
        with open(tex, "wb") as f:
            f.write(b"PNGDATA")
        file_node = self._assign_texture(self.cube, tex)
        self._set_ftn_verbatim(file_node, "upgrade_me.png")

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]
        tm.convert_to_relative_paths()

        self.assertEqual(
            cmds.getAttr(f"{file_node}.fileTextureName"),
            "sourceimages/upgrade_me.png",
        )

    def test_check_valid_paths_accepts_a_udim_set_not_starting_at_1001(self):
        """A tile set is not required to start at 1001.

        The Maya-side gate probes the FIXED stand-in, so a set running
        1002-1005 -- routine -- was reported as a missing texture even though
        every tile is on disk and the Texture Path Editor shows it as fine.
        Added: 2026-08-25
        """
        sourceimages = self._set_project(self.temp_dir)
        for tile in ("1002", "1003"):
            with open(os.path.join(sourceimages, f"late.{tile}.png"), "wb") as f:
                f.write(b"PNGDATA")
        pattern = os.path.join(sourceimages, "late.<UDIM>.png").replace("\\", "/")
        self._assign_texture(self.cube, pattern)

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        original_cwd = os.getcwd()
        os.chdir(self.temp_dir)
        try:
            _passed, messages = tm.check_valid_paths()
        finally:
            os.chdir(original_cwd)

        self.assertFalse(
            any("late." in m for m in messages),
            f"a tile set present on disk is not missing: {messages}",
        )

    def test_resolve_invalid_texture_paths_leaves_a_late_udim_set_alone(self):
        """The repair task shares that gate, so it rebound a healthy set."""
        sourceimages = self._set_project(self.temp_dir)
        for tile in ("1002", "1003"):
            with open(os.path.join(sourceimages, f"keep.{tile}.png"), "wb") as f:
                f.write(b"PNGDATA")
        pattern = os.path.join(sourceimages, "keep.<UDIM>.png").replace("\\", "/")
        file_node = self._assign_texture(self.cube, pattern)

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]
        tm.resolve_invalid_texture_paths()

        self.assertEqual(
            cmds.getAttr(f"{file_node}.fileTextureName").replace("\\", "/"),
            pattern,
            "a resolving tile set was rebound by name",
        )

    def test_check_valid_paths_flags_fbx_unlocatable_relative_path(self):
        """A relative path Maya resolves via the workspace still fails when the
        FBX plug-in would not locate it at write time.

        The fbxmaya exporter locates textures with plain OS path resolution —
        relative paths against the process CWD, NOT the workspace (probe-proven
        2026-08-04: embedding succeeded only when the CWD was the project root,
        regardless of the active workspace).  A green check followed by "The
        following texture(s) will not be embedded" after the write is exactly
        what this check exists to prevent.
        Added: 2026-08-04
        """
        sourceimages = self._set_project(self.temp_dir)
        tex = os.path.join(sourceimages, "ws_only.png")
        with open(tex, "wb") as f:
            f.write(b"PNGDATA")
        file_node = self._assign_texture(self.cube, "sourceimages/ws_only.png")
        # Keep the path relative — cmds.setAttr already expanded it.
        self._set_ftn_verbatim(file_node, "sourceimages/ws_only.png")

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        # try/finally, not addCleanup: cleanups run AFTER tearDown, whose
        # rmtree cannot delete a directory the process still has as its CWD.
        original_cwd = os.getcwd()
        elsewhere = os.path.join(self.temp_dir, "elsewhere")
        os.makedirs(elsewhere, exist_ok=True)
        os.chdir(elsewhere)  # anywhere that is NOT the project root
        try:
            passed, messages = tm.check_valid_paths()
        finally:
            os.chdir(original_cwd)

        self.assertFalse(
            passed, "workspace-resolvable but FBX-unlocatable path must fail"
        )
        self.assertTrue(any("ws_only.png" in m for m in messages))

    def test_check_valid_paths_passes_relative_path_with_cwd_at_project_root(self):
        """The same relative path passes once the CWD sits at the project root
        — the state the set_workspace task now establishes for the write.

        Added: 2026-08-04
        """
        sourceimages = self._set_project(self.temp_dir)
        tex = os.path.join(sourceimages, "cwd_ok.png")
        with open(tex, "wb") as f:
            f.write(b"PNGDATA")
        file_node = self._assign_texture(self.cube, "sourceimages/cwd_ok.png")
        # Keep the path relative — cmds.setAttr already expanded it.
        self._set_ftn_verbatim(file_node, "sourceimages/cwd_ok.png")

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        original_cwd = os.getcwd()
        os.chdir(self.temp_dir)  # the project root
        try:
            passed, messages = tm.check_valid_paths()
        finally:
            os.chdir(original_cwd)

        self.assertTrue(passed, f"CWD at project root must pass: {messages}")

    def test_check_valid_paths_flags_a_relative_tiled_set_as_fbx_unlocatable(self):
        """A RELATIVE tiled path is the working-directory case, not a dead pattern.

        Bug: the tile/frame verdict was decided at the FBX gate on the RAW
        stored value, so ``sourceimages/tiled.<UDIM>.png`` -- the normal
        storage form, resolvable only through the workspace -- probed relative
        to the CWD, missed, and was reported as "no workspace setting fixes
        it": the exact remedy that does fix it, actively negated. Tile 1001 is
        on disk here; only the CWD is wrong.
        Added: 2026-08-18
        """
        sourceimages = self._set_project(self.temp_dir)
        tile = os.path.join(sourceimages, "rel_tiled.1001.png")
        with open(tile, "wb") as f:
            f.write(b"PNGDATA")
        file_node = self._assign_texture(self.cube, tile)
        self._set_ftn_verbatim(file_node, "sourceimages/rel_tiled.<UDIM>.png")

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        original_cwd = os.getcwd()
        elsewhere = os.path.join(self.temp_dir, "elsewhere_tiled")
        os.makedirs(elsewhere, exist_ok=True)
        os.chdir(elsewhere)  # anywhere that is NOT the project root
        try:
            passed, messages = tm.check_valid_paths()
        finally:
            os.chdir(original_cwd)

        self.assertFalse(passed, f"the FBX plug-in cannot locate it: {messages}")
        self.assertTrue(
            any("Not locatable at write time" in m for m in messages),
            f"must read as the working-directory case: {messages}",
        )
        self.assertFalse(
            any("tile/frame token" in m for m in messages),
            f"the pattern DOES resolve -- Auto Set Workspace fixes it: {messages}",
        )

    def test_check_valid_paths_names_a_tile_pattern_that_resolves_to_nothing(self):
        """A tokened path Maya cannot resolve reads as a pattern, not a filename.

        "Missing Texture: ... -> tex.<uvtile>.png" points the user at a name
        that never exists as written; the actionable question is whether the
        tile the pattern denotes is on disk. Absolute here, so the workspace
        is not in play and no CWD can rescue it.
        Added: 2026-08-18
        """
        sourceimages = self._set_project(self.temp_dir)
        # Nothing written to disk -- <uvtile> collapses to u1_v1, which is absent.
        self._assign_texture(
            self.cube, os.path.join(sourceimages, "no_tiles.<uvtile>.png")
        )

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        passed, messages = tm.check_valid_paths()
        self.assertFalse(passed)
        self.assertTrue(
            any("Unresolved tile/frame pattern" in m for m in messages),
            f"a dead token must get the tile/frame verdict: {messages}",
        )
        self.assertFalse(
            any("Missing Texture" in m for m in messages),
            f"one verdict per path, and this one is about the pattern: {messages}",
        )

    # ------------------------------------------------------------------
    # Objects-below-floor tolerance
    # ------------------------------------------------------------------

    def test_below_floor_checkbox_true_uses_default_tolerance(self):
        """Enabling the check (checkbox → True) applies the documented 0.5
        default, not float(True) == 1.0.

        The UI registers this check as a QCheckBox, so b000 passes True when
        enabled; coercing that to 1.0 silently doubled the advertised tolerance.
        Added: 2026-06-19
        """
        # Sink the cube 0.75 below the floor: inside a 1.0 tolerance (old, would
        # pass) but outside the documented 0.5 (should fail).
        cube_long = cmds.ls(str(self.cube), l=True)[0]
        ymin = cmds.xform(cube_long, query=True, ws=True, bb=True)[1]
        cmds.setAttr(f"{cube_long}.translateY", -0.75 - ymin)

        tm = self.exporter.task_manager
        tm.objects = [cube_long]

        passed, messages = tm.check_objects_below_floor(True)
        self.assertFalse(passed, "checkbox-True must use 0.5 tolerance, so -0.75 fails")
        # The header reports the effective tolerance used.
        self.assertTrue(any("0.500" in m for m in messages))

    def test_below_floor_zero_or_none_is_off(self):
        """The spin box's 0 reads OFF (2026-09-13: the check is a depth, not a
        checkbox), and None / False agree with it. A strict check is a small
        depth, not zero -- the contract None once carried.
        """
        cube_long = cmds.ls(str(self.cube), l=True)[0]
        ymin = cmds.xform(cube_long, query=True, ws=True, bb=True)[1]
        cmds.setAttr(f"{cube_long}.translateY", -0.1 - ymin)

        tm = self.exporter.task_manager
        tm.objects = [cube_long]

        for off in (0, 0.0, None, False):
            with self.subTest(value=off):
                passed, messages = tm.check_objects_below_floor(off)
                self.assertTrue(passed, f"{off!r} must disable the check: {messages}")
        passed, _ = tm.check_objects_below_floor(0.01)
        self.assertFalse(passed, "a small depth is the strict check")

    def test_below_floor_numeric_tolerance_respected(self):
        """A real numeric tolerance still passes things within it.

        Added: 2026-06-19
        """
        cube_long = cmds.ls(str(self.cube), l=True)[0]
        ymin = cmds.xform(cube_long, query=True, ws=True, bb=True)[1]
        cmds.setAttr(f"{cube_long}.translateY", -0.75 - ymin)

        tm = self.exporter.task_manager
        tm.objects = [cube_long]

        passed, _ = tm.check_objects_below_floor(2.0)
        self.assertTrue(passed, "-0.75 is within a 2.0 tolerance")

    # ------------------------------------------------------------------
    # set_workspace warning
    # ------------------------------------------------------------------

    def test_set_workspace_warns_when_no_workspace_found(self):
        """Verify set_workspace logs a warning when no workspace.mel is found.

        When find_workspace_using_path() returns None, the task should
        emit a WARNING rather than a silent DEBUG message.
        Fixed: 2026-03-04
        """
        log_output = []
        handler = logging.Handler()
        handler.emit = lambda record: log_output.append(record)
        handler.setLevel(logging.WARNING)
        self.exporter.logger.addHandler(handler)
        self.exporter.logger.setLevel(logging.DEBUG)

        # Save to temp dir (no workspace.mel ancestor)
        scene_path = os.path.join(self.temp_dir, "no_workspace_scene.ma")
        _pm_rename_file(scene_path)

        self.exporter.task_manager.set_workspace(enable=True)

        warnings = [
            r
            for r in log_output
            if r.levelno >= logging.WARNING and "workspace" in r.getMessage().lower()
        ]
        self.assertGreater(
            len(warnings),
            0,
            "Expected a warning about missing workspace.mel",
        )
        self.exporter.logger.removeHandler(handler)

    def _make_workspace_scene(self):
        """Create a workspace.mel + scenes/ under temp_dir and rename the
        scene into it; returns the workspace root."""
        ws_root = self.temp_dir
        with open(os.path.join(ws_root, "workspace.mel"), "w") as f:
            f.write('workspace -fr "sourceImages" "sourceimages";\n')
        scenes = os.path.join(ws_root, "scenes")
        os.makedirs(scenes, exist_ok=True)
        _pm_rename_file(os.path.join(scenes, "cwd_scene.ma"))
        return ws_root

    @staticmethod
    def _same_dir(a, b):
        return os.path.normcase(os.path.normpath(a)) == os.path.normcase(
            os.path.normpath(b)
        )

    def test_set_workspace_aligns_cwd_with_workspace_root(self):
        """set_workspace leaves the process CWD at the workspace root and
        stages a deferred restore back to the original CWD.

        The FBX plug-in locates relative texture paths against the CWD at
        write time (never the workspace) — aligning it is what makes the
        default relative-path pipeline actually embed/reference textures.
        Added: 2026-08-04
        """
        ws_root = self._make_workspace_scene()
        original_ws = cmds.workspace(q=True, rd=True)
        self.addCleanup(lambda: cmds.workspace(original_ws, openWorkspace=True))

        # try/finally, not addCleanup: cleanups run AFTER tearDown, whose
        # rmtree cannot delete a directory the process still has as its CWD.
        original_cwd = os.getcwd()
        elsewhere = os.path.join(self.temp_dir, "elsewhere")
        os.makedirs(elsewhere, exist_ok=True)
        os.chdir(elsewhere)
        tm = self.exporter.task_manager
        try:
            tm.set_workspace(enable=True)

            self.assertTrue(
                self._same_dir(os.getcwd(), cmds.workspace(q=True, rd=True)),
                f"CWD {os.getcwd()} must sit at the workspace root after the task",
            )
            self.assertTrue(self._same_dir(os.getcwd(), ws_root))

            # The staged restore puts the original CWD back after the write.
            tm.run_deferred_restores()
            self.assertTrue(self._same_dir(os.getcwd(), elsewhere))
        finally:
            os.chdir(original_cwd)

    def test_set_workspace_aligns_cwd_when_workspace_already_matches(self):
        """Even when the workspace needs no switch, a foreign CWD must still
        be aligned — GUI Maya never chdirs on Set Project.

        Added: 2026-08-04
        """
        ws_root = self._make_workspace_scene()
        original_ws = cmds.workspace(q=True, rd=True)
        self.addCleanup(lambda: cmds.workspace(original_ws, openWorkspace=True))
        cmds.workspace(ws_root, openWorkspace=True)  # already correct

        original_cwd = os.getcwd()
        elsewhere = os.path.join(self.temp_dir, "elsewhere")
        os.makedirs(elsewhere, exist_ok=True)
        os.chdir(elsewhere)
        tm = self.exporter.task_manager
        try:
            tm.set_workspace(enable=True)

            self.assertTrue(
                self._same_dir(os.getcwd(), ws_root),
                f"CWD {os.getcwd()} must be aligned even without a workspace switch",
            )
            tm.run_deferred_restores()
        finally:
            os.chdir(original_cwd)

    # ------------------------------------------------------------------
    # Export-transient state — must SURVIVE the write, not revert before it
    # ------------------------------------------------------------------

    def test_set_linear_unit_survives_run_tasks(self):
        """The working unit must still be applied when the FBX is written.

        Maya's FBX plugin stamps the file's unit from the working unit at
        write time; the task stages its restore through
        ``TaskFactory.stage_deferred_restore``, which the exporter unwinds
        after the write. (The ``set_``/``revert_`` pair that fired when
        ``run_tasks`` returned — *before* the write — was retired 2026-09-13.)
        Fixed: 2026-07-28
        """
        tm = self.exporter.task_manager
        original = cmds.currentUnit(query=True, linear=True)
        target = "m" if original != "m" else "cm"
        try:
            self.assertTrue(tm.run_tasks({"set_linear_unit": target}))
            self.assertEqual(
                cmds.currentUnit(query=True, linear=True),
                target,
                "unit was reverted before the export write (task is inert)",
            )
            self.assertIn("linear_unit", tm._deferred_restores)

            tm.run_deferred_restores()
            self.assertEqual(cmds.currentUnit(query=True, linear=True), original)
            self.assertFalse(tm._deferred_restores)
        finally:
            cmds.currentUnit(linear=original)

    def test_set_linear_unit_off_stages_nothing(self):
        """An OFF / empty selection must not stage a restore at all."""
        tm = self.exporter.task_manager
        original = cmds.currentUnit(query=True, linear=True)
        tm.run_tasks({"set_linear_unit": "OFF"})
        self.assertEqual(cmds.currentUnit(query=True, linear=True), original)
        self.assertFalse(tm._deferred_restores)

    # ------------------------------------------------------------------
    # Framerate check — quiet on pass
    # ------------------------------------------------------------------

    def test_check_framerate_pass_returns_no_messages(self):
        """Verify check_framerate returns (True, []) on a successful match.

        Previously the check returned a verbose message even on pass,
        causing a full box display.  Now it returns empty messages.
        Fixed: 2026-03-04
        """
        # Set framerate to ntsc and check for ntsc
        cmds.currentUnit(time="ntsc")
        # Create a keyframe so the check doesn't skip
        cmds.setKeyframe(str(self.cube), attribute="translateX", time=1, value=0)

        self.exporter.task_manager.objects = [cmds.ls(str(self.cube), l=True)[0]]
        success, messages = self.exporter.task_manager.check_framerate("ntsc")
        self.assertTrue(success)
        self.assertEqual(
            messages, [], "Passing framerate check should return no messages"
        )

    def test_check_framerate_fail_returns_messages(self):
        """Verify check_framerate returns (False, [...]) on mismatch."""
        cmds.currentUnit(time="ntsc")
        cmds.setKeyframe(str(self.cube), attribute="translateX", time=1, value=0)

        self.exporter.task_manager.objects = [cmds.ls(str(self.cube), l=True)[0]]
        success, messages = self.exporter.task_manager.check_framerate("pal")
        self.assertFalse(success)
        self.assertGreater(
            len(messages), 0, "Failed framerate check should return messages"
        )

    # ------------------------------------------------------------------
    # reassign_duplicate_materials deletes duplicates
    # ------------------------------------------------------------------

    def test_reassign_duplicate_materials_passes_delete_true(self):
        """Verify reassign_duplicate_materials calls MatUtils with delete=True.

        Bug: The task called reassign_duplicate_materials with delete=False
        (default), leaving orphaned duplicate material nodes in the scene.
        The subsequent check_duplicate_materials then found those nodes and
        reported a failure even though geometry was correctly reassigned.
        Fixed: 2026-03-05
        """
        self.exporter.task_manager.objects = [cmds.ls(str(self.cube), l=True)[0]]

        with patch(
            "mayatk.env_utils.scene_exporter._task_textures.MatUtils.reassign_duplicate_materials"
        ) as mock_reassign:
            self.exporter.task_manager.reassign_duplicate_materials()
            mock_reassign.assert_called_once()
            _, kwargs = mock_reassign.call_args
            self.assertTrue(
                kwargs.get("delete", False),
                "reassign_duplicate_materials must pass delete=True to clean up duplicates",
            )

    def test_reassign_duplicate_materials_invalidates_cache(self):
        """Verify reassign_duplicate_materials invalidates the materials cache.

        Bug: After deleting duplicate materials, _cached_materials still
        contained the deleted node names. The next task
        (resolve_invalid_texture_paths) called cmds.listHistory with the
        stale list, causing ValueError: No object matches name.
        Fixed: 2026-03-05
        """
        self.exporter.task_manager.objects = [cmds.ls(str(self.cube), l=True)[0]]
        # Prime the cache
        self.exporter.task_manager._get_all_materials()
        self.assertIsNotNone(self.exporter.task_manager._cached_materials)

        with patch(
            "mayatk.env_utils.scene_exporter._task_textures.MatUtils.reassign_duplicate_materials"
        ):
            self.exporter.task_manager.reassign_duplicate_materials()

        self.assertIsNone(
            self.exporter.task_manager._cached_materials,
            "Materials cache must be invalidated after reassign_duplicate_materials",
        )

    def test_resolve_invalid_texture_paths_survives_deleted_materials(self):
        """Verify resolve_invalid_texture_paths skips non-existent materials.

        Bug: If _get_all_materials returned stale names (e.g. after deletion),
        cmds.listHistory crashed with ValueError.
        Fixed: 2026-03-05
        """
        self.exporter.task_manager.objects = [cmds.ls(str(self.cube), l=True)[0]]
        # Inject a fake deleted material into the cache
        real = self.exporter.task_manager._get_all_materials()
        self.exporter.task_manager._cached_materials = list(real) + [
            "NONEXISTENT_MATERIAL_NODE"
        ]
        # Should not raise
        self.exporter.task_manager.resolve_invalid_texture_paths()

    def test_smart_bake_does_not_double_optimize(self):
        """Verify smart_bake passes optimize_keys=False to SmartBake.

        Bug: SmartBake internally optimized baked curves, then the
        standalone optimize_keys task ran a second pass on ALL curves.
        The double processing caused additional tangent distortion at
        flat-to-animated boundaries.
        Fixed: 2026-03-05
        """
        self.exporter.task_manager.objects = [cmds.ls(str(self.cube), l=True)[0]]

        with patch("mayatk.anim_utils.smart_bake._smart_bake.SmartBake") as MockBaker:
            mock_instance = MockBaker.return_value
            mock_analysis = {}
            mock_instance.analyze.return_value = mock_analysis

            self.exporter.task_manager.smart_bake()

            MockBaker.assert_called_once()
            _, kwargs = MockBaker.call_args
            self.assertFalse(
                kwargs.get("optimize_keys", True),
                "SmartBake must receive optimize_keys=False; standalone task handles optimization",
            )

    def test_smart_bake_skips_its_layer_reduction_where_the_write_resamples_it(self):
        """A write that splits the declared takes forces
        ``FBXExportBakeComplexAnimation`` over the shot union, re-sampling the
        override layer per frame, so a key reduction inside the layer never
        ships: ~50 s of a production run for ``static``/``flat``, up to 1 %
        injected error for ``extremes``/``simplify``. Everywhere else the level
        reaches SmartBake. Added: 2026-09-19
        """
        from mayatk.node_utils.data_nodes import DataNodes

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]
        ptk.SceneRecords.SHOTS.save(
            DataNodes,
            {"shots": [{"clip": "Shot_1", "start": 1, "end": 10, "objects": []}]},
        )

        def level_reaching_the_baker(run):
            tm.run = run
            with patch(
                "mayatk.anim_utils.smart_bake._smart_bake.SmartBake"
            ) as MockBaker:
                MockBaker.return_value.analyze.return_value = {}
                tm.smart_bake()
            return MockBaker.call_args[1]["optimize_keys"]

        run = ptk.ExportRun.from_tasks({"optimize_keys": "extremes"})[0]
        splits = run.with_tasks(
            {"optimize_keys": "extremes", "apply_declared_takes": "shots"}
        )
        whole = run.with_tasks(
            {"optimize_keys": "extremes", "apply_declared_takes": "full"}
        )
        self.assertFalse(level_reaching_the_baker(splits))
        self.assertEqual(level_reaching_the_baker(whole), "extremes")
        self.assertEqual(
            level_reaching_the_baker(splits.replace(animation_write_back=True)),
            "extremes",
            "a write-back keeps the layer's keys in the scene",
        )
        ptk.SceneRecords.SHOTS.clear(DataNodes)
        self.assertEqual(
            level_reaching_the_baker(splits),
            "extremes",
            "a scene that declares no takes splits nothing",
        )

    def test_smart_bake_stages_its_own_restore(self):
        """The bake session unwinds through the deferred registry, staged by
        the task right after the bake -- LIFO then puts it FIRST, before the
        flatten restore whose rewrap node its matrix records reconnect to.
        It used to be a hand-rolled block in perform_export's ``finally``
        that relied on its position in the source. Added: 2026-09-13
        """
        from types import SimpleNamespace

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]
        result = SimpleNamespace(
            session_id="bake_s1",
            override_layer="bakeLayer1",
            baked_count=1,
            time_range=(1, 10),
            optimized=[],
            object_time_ranges={},
        )
        with patch("mayatk.anim_utils.smart_bake._smart_bake.SmartBake") as Baker:
            Baker.return_value.analyze.return_value = {
                "c": SimpleNamespace(requires_bake=True)
            }
            Baker.return_value.bake.return_value = result
            tm.smart_bake()
            self.assertIn("smart_bake", tm._deferred_restores)
            self.assertEqual(tm._bake_session_id, "bake_s1")
            staged = list(tm._deferred_restores)
            self.assertLess(
                staged.index("animation"),
                staged.index("smart_bake"),
                "the curve snapshot is staged first, so it restores AFTER the bake",
            )
            tm.run_deferred_restores()
            Baker.restore.assert_called_once_with("bake_s1")
            Baker.restore_matrix_wiring.assert_not_called()
            self.assertIsNone(tm._bake_session_id)
            self.assertIsNone(tm._bake_override_layer)

        # Scene Keys (In Place): the bake stays; only the matrix wiring is
        # handed back to its live drivers.
        tm.run = tm.run.replace(animation_write_back=True)
        with patch("mayatk.anim_utils.smart_bake._smart_bake.SmartBake") as Baker:
            Baker.return_value.analyze.return_value = {
                "c": SimpleNamespace(requires_bake=True)
            }
            Baker.return_value.bake.return_value = result
            tm.smart_bake()
            tm.run_deferred_restores()
            Baker.restore.assert_not_called()
            Baker.restore_matrix_wiring.assert_called_once_with("bake_s1")
        tm.run = tm.run.replace(animation_write_back=False)

    # ------------------------------------------------------------------
    # Hierarchy manifest & diff check
    # ------------------------------------------------------------------

    def test_manifest_path_for(self):
        """Verify sidecar manifest path derivation."""
        from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import (
            SceneDataSidecar,
        )

        result = SceneDataSidecar.manifest_path_for("/assets/hero.fbx")
        self.assertTrue(result.endswith(".hero.scene_data.json"))

    def test_diff_report_path_for(self):
        """Verify sidecar diff report path derivation."""
        from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import (
            SceneDataSidecar,
        )

        result = SceneDataSidecar.diff_report_path_for("/assets/hero.fbx")
        self.assertTrue(result.endswith(".hero.hierarchy_diff.txt"))

    def test_build_clean_path_set_strips_namespace(self):
        """Verify namespace stripping and leading pipe removal.

        The set is closed under ancestors (the parent chain ships), so the
        bare group entries are expected alongside the leaves."""
        from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import (
            SceneDataSidecar,
        )

        objects = ["|ns:group|ns:child", "|group2|child2"]
        result = SceneDataSidecar.build_clean_path_set(objects)
        self.assertEqual(result, {"group", "group|child", "group2", "group2|child2"})

    def test_get_top_level_collapses_children(self):
        """Verify that children are collapsed under their top-level parent."""
        from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import (
            SceneDataSidecar,
        )

        paths = ["group", "group|child", "group|child|grandchild", "other"]
        result = SceneDataSidecar.get_top_level(paths)
        self.assertEqual(sorted(result), ["group", "other"])

    def test_get_top_level_preserves_siblings(self):
        """Verify that siblings with similar prefix names are NOT collapsed."""
        from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import (
            SceneDataSidecar,
        )

        paths = ["group", "group_alt", "group|child"]
        result = SceneDataSidecar.get_top_level(paths)
        self.assertEqual(sorted(result), ["group", "group_alt"])

    def test_detect_reparenting_finds_moved_subtree(self):
        """detect_reparenting recognises a subtree moved under a new parent."""
        from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import (
            SceneDataSidecar,
        )

        missing = [
            "GRP",
            "GRP|LOC",
            "GRP|LOC|GEO",
            "GRP|LOC|GEOShape",
            "GRP|LOC|LOCShape",
        ]
        extra = [
            "new",
            "new|GRP",
            "new|GRP|LOC",
            "new|GRP|LOC|GEO",
            "new|GRP|LOC|GEOShape",
            "new|GRP|LOC|LOCShape",
        ]
        result = SceneDataSidecar.detect_reparenting(missing, extra)
        self.assertEqual(len(result), 1)
        root, parent, count = result[0]
        self.assertEqual(root, "GRP")
        self.assertEqual(parent, "new")
        self.assertEqual(count, 5)

    def test_detect_reparenting_returns_empty_on_unrelated_changes(self):
        """detect_reparenting returns empty when changes are not reparenting."""
        from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import (
            SceneDataSidecar,
        )

        missing = ["OldNode", "OldNode|Child"]
        extra = ["CompletelyDifferent"]
        result = SceneDataSidecar.detect_reparenting(missing, extra)
        self.assertEqual(result, [])

    def test_hierarchy_check_no_manifest(self):
        """Check passes when no manifest exists yet."""
        self.exporter.task_manager.objects = [cmds.ls(str(self.cube), l=True)[0]]
        self.exporter.task_manager.run = self.exporter.task_manager.run.replace(
            export_path=os.path.join(self.temp_dir, "test.fbx")
        )
        passed, messages = self.exporter.task_manager.check_hierarchy_vs_existing_fbx()
        self.assertTrue(passed)

    def test_hierarchy_check_detects_missing_node(self):
        """Check fails when a node from the manifest is missing.

        Bug: Hierarchy tests were not exercised at all.
        Fixed: 2026-04-10
        """
        import json

        export_path = os.path.join(self.temp_dir, "test.fbx")
        manifest_path = os.path.join(self.temp_dir, ".test.hierarchy.json")

        # Build manifest from actual scene hierarchy, then add an extra node
        self.exporter.task_manager.objects = [
            cmds.ls(str(self.group), l=True)[0],
            cmds.ls(str(self.cube), l=True)[0],
            cmds.ls(str(self.sphere), l=True)[0],
        ]
        self.exporter.task_manager.run = self.exporter.task_manager.run.replace(
            export_path=export_path
        )
        current = sorted(self.exporter.task_manager._build_full_hierarchy_set())
        current.append("ExportGroup|ExtraNode")
        with open(manifest_path, "w") as f:
            json.dump({"paths": current, "object_count": len(current)}, f)

        passed, messages = self.exporter.task_manager.check_hierarchy_vs_existing_fbx()
        self.assertFalse(passed)
        self.assertTrue(any("missing" in m.lower() for m in messages))

    def test_hierarchy_check_reports_to_temp_not_export_dir(self):
        """A failed check stashes the diff and reports to temp — the export
        folder stays clean (v3 single-file sidecar contract)."""
        import json
        import tempfile

        export_path = os.path.join(self.temp_dir, "test.fbx")
        manifest_path = os.path.join(self.temp_dir, ".test.hierarchy.json")
        diff_path = os.path.join(self.temp_dir, ".test.hierarchy_diff.txt")

        # Build manifest from actual hierarchy, then add a node that will be "missing"
        self.exporter.task_manager.objects = [
            cmds.ls(str(self.group), l=True)[0],
            cmds.ls(str(self.cube), l=True)[0],
        ]
        self.exporter.task_manager.run = self.exporter.task_manager.run.replace(
            export_path=export_path
        )
        current = sorted(self.exporter.task_manager._build_full_hierarchy_set())
        current.append("ExportGroup|Gone")
        with open(manifest_path, "w") as f:
            json.dump({"paths": current, "object_count": len(current)}, f)

        self.exporter.task_manager.check_hierarchy_vs_existing_fbx()

        # Nothing lands beside the deliverable.
        self.assertFalse(os.path.exists(diff_path))
        # The structured diff is stashed for the post-export sidecar write.
        stash = self.exporter.task_manager._hierarchy_last_diff
        self.assertIsNotNone(stash)
        self.assertIn("ExportGroup|Gone", stash["missing"])
        # The human-readable report went to the temp artifact (deterministic
        # per-stem name: hierarchy_diff_<stem>.txt).
        temp_report = os.path.join(tempfile.gettempdir(), "hierarchy_diff_test.txt")
        self.assertTrue(os.path.exists(temp_report))
        with open(temp_report, encoding="utf-8") as f:
            self.assertIn("ExportGroup|Gone", f.read())

    def _check_with_output(self, filename):
        """Run the hierarchy check + baseline write for one output name."""
        tm = self.exporter.task_manager
        tm.run = tm.run.replace(export_path=os.path.join(self.temp_dir, filename))
        passed, messages = tm.check_hierarchy_vs_existing_fbx()
        from mayatk.env_utils.hierarchy_sync.hierarchy_baseline import (
            HierarchyBaseline,
        )

        HierarchyBaseline.write(tm._build_full_hierarchy_set())
        return passed, messages

    def test_baseline_survives_an_output_rename(self):
        """THE reported bug: the baseline was keyed by the output file's stem, so
        renaming the Output Filename pointed the next export at a different
        sidecar and silently started over — the first export after any rename
        passed no matter what had changed. It is keyed by the SCENE now."""
        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.group), l=True)[0]]

        self._check_with_output("asset.fbx")  # seeds the baseline

        cmds.delete(str(self.sphere))  # a real structural change
        tm.objects = [cmds.ls(str(self.group), l=True)[0]]

        # ...and now export under a completely different name.
        passed, messages = self._check_with_output("WIP_prod_thing_v007.fbx")
        self.assertFalse(passed, "a rename must not reset the hierarchy baseline")
        self.assertTrue(any("missing" in m.lower() for m in messages))

    def test_baseline_is_per_scene_not_per_export_name(self):
        """One record serves every export a scene makes: exporting asset B must
        not report asset A as missing, and recording B must not forget A."""
        from mayatk.env_utils.hierarchy_sync.hierarchy_baseline import (
            HierarchyBaseline,
        )

        cube_b = cmds.polyCube(name="OtherCube")[0]
        group_b = cmds.group(cube_b, name="OtherGroup")
        tm = self.exporter.task_manager

        tm.objects = [cmds.ls(str(self.group), l=True)[0]]
        self._check_with_output("assetA.fbx")

        # A different asset, same scene: a clean pass, not "all of A is gone".
        tm.objects = [cmds.ls(str(group_b), l=True)[0]]
        passed, _ = self._check_with_output("assetB.fbx")
        self.assertTrue(passed, "exporting B must not report A as missing")

        # A is still recorded, so its own change is still caught.
        recorded = HierarchyBaseline.read()
        self.assertTrue(any(p.startswith("ExportGroup") for p in recorded))
        self.assertTrue(any(p.startswith("OtherGroup") for p in recorded))

        cmds.delete(str(self.sphere))
        tm.objects = [cmds.ls(str(self.group), l=True)[0]]
        passed, messages = self._check_with_output("assetA.fbx")
        self.assertFalse(passed, "B's export must not have erased A's baseline")
        self.assertTrue(any("ExportSphere" in m for m in messages))

    def _save_scene_as(self, name):
        """Save the open scene as *name* in the temp dir -- a Save As: a file
        saved before stays on disk."""
        path = os.path.join(self.temp_dir, name)
        cmds.file(rename=path)
        cmds.file(save=True, type="mayaAscii", force=True)
        return path

    def test_a_save_as_copy_does_not_inherit_its_sources_baseline(self):
        """THE reported bug (2026-09-24): a module scene saved as a new module
        carried its source's baseline in data_internal, and the copy's first
        export failed against the SOURCE's hierarchy (``INTERACTIVE|
        MULTIMETER_GRP`` missing, ``INTERACTIVE|SOLDERING_TABLE_GRP`` new)
        although the copy had never exported. A baseline recorded by another
        scene file that is still on disk is that scene's, not this one's."""
        tm = self.exporter.task_manager
        self._save_scene_as("source_module.ma")
        tm.objects = [cmds.ls(str(self.group), l=True)[0]]
        self._check_with_output("source.fbx")

        self._save_scene_as("copy_module.ma")  # the source stays on disk
        cmds.delete(str(self.sphere))  # ...and the copy becomes another module
        cmds.parent(cmds.polyTorus(name="CopyTorus")[0], str(self.group))
        tm.objects = [cmds.ls(str(self.group), l=True)[0]]

        with self.assertLogs(self.exporter.logger, level="WARNING") as captured:
            passed, messages = self._check_with_output("copy.fbx")
        self.assertTrue(passed, messages)
        self.assertTrue(
            any("source_module.ma" in m for m in captured.output),
            "the user must be told whose baseline was set aside",
        )

        # The export recorded the copy's OWN baseline, so its changes are caught.
        cmds.delete("CopyTorus")
        tm.objects = [cmds.ls(str(self.group), l=True)[0]]
        passed, messages = self._check_with_output("copy.fbx")
        self.assertFalse(passed, "the copy's own baseline must be compared")
        self.assertTrue(any("CopyTorus" in m for m in messages))

    def test_a_version_up_still_diffs_the_deliverable_it_continues(self):
        """A version-up (v001 kept, v002 saved) is a Save As copy too, so the
        record is v001's -- but v002 exports the deliverable v001 exported, and
        what that deliverable last shipped (its sidecar) is the baseline."""
        tm = self.exporter.task_manager
        self._save_scene_as("asset_v001.ma")
        tm.objects = [cmds.ls(str(self.group), l=True)[0]]
        tm.run = tm.run.replace(export_path=os.path.join(self.temp_dir, "asset.fbx"))
        tm.check_hierarchy_vs_existing_fbx()
        tm.write_scene_data_sidecar()

        self._save_scene_as("asset_v002.ma")
        cmds.delete(str(self.sphere))
        tm.objects = [cmds.ls(str(self.group), l=True)[0]]
        passed, messages = tm.check_hierarchy_vs_existing_fbx()
        self.assertFalse(passed, "a version-up must still diff its deliverable")
        self.assertTrue(any("ExportSphere" in m for m in messages))

    def test_a_renamed_scene_keeps_its_baseline(self):
        """Renamed or moved -- the old file gone -- the record is still the
        scene's own: nothing can open the file it names any more."""
        tm = self.exporter.task_manager
        old = self._save_scene_as("module_old.ma")
        tm.objects = [cmds.ls(str(self.group), l=True)[0]]
        self._check_with_output("module.fbx")

        self._save_scene_as("module_new.ma")
        os.remove(old)  # a rename, not a copy
        cmds.delete(str(self.sphere))
        tm.objects = [cmds.ls(str(self.group), l=True)[0]]
        passed, messages = self._check_with_output("module_renamed.fbx")
        self.assertFalse(passed, "a renamed scene must keep its baseline")
        self.assertTrue(any("ExportSphere" in m for m in messages))

    def test_a_scene_never_adopts_another_deliverables_sidecar(self):
        """Every module of a production exports into ONE shared folder, and the
        upgrade adoption merged EVERY sidecar there into a scene with no record
        of its own: a new module's first export was diffed against the other
        modules' deliverables wherever they shared a top group
        (``INTERACTIVE``). Only the deliverable being exported is this scene's
        history."""
        import json
        from mayatk.env_utils.hierarchy_sync.hierarchy_baseline import (
            HierarchyBaseline,
        )

        other = os.path.join(self.temp_dir, ".other_module.scene_data.json")
        with open(other, "w") as f:
            json.dump(
                {
                    "format": 3,
                    "hierarchy": {"paths": ["ExportGroup", "ExportGroup|OtherPart"]},
                },
                f,
            )
        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.group), l=True)[0]]
        tm.run = tm.run.replace(
            export_path=os.path.join(self.temp_dir, "this_module.fbx")
        )
        passed, messages = tm.check_hierarchy_vs_existing_fbx()
        self.assertTrue(passed, messages)
        tm.write_scene_data_sidecar()
        self.assertNotIn("ExportGroup|OtherPart", HierarchyBaseline.read())

    def _save_unstamped_baseline(self, paths):
        """Store *paths* as a baseline recorded before records named their
        scene -- what every scene exported before 2026-09-24 carries."""
        from mayatk.node_utils.data_nodes import DataNodes

        ptk.SceneRecords.HIERARCHY_BASELINE.save(
            DataNodes, ptk.HierarchyBaseline.encode(paths)
        )

    def test_an_unstamped_baseline_is_set_aside_for_a_new_deliverable(self):
        """The reported scene's own state: its record predates the stamp, so
        nothing can say which scene recorded it -- a copy carries its source's
        verbatim. With no sidecar for the deliverable either, the export is a
        first one, said out loud."""
        tm = self.exporter.task_manager
        self._save_scene_as("forked_module.ma")
        self._save_unstamped_baseline({"ExportGroup", "ExportGroup|SourcePart"})
        tm.objects = [cmds.ls(str(self.group), l=True)[0]]
        tm.run = tm.run.replace(export_path=os.path.join(self.temp_dir, "forked.fbx"))

        with self.assertLogs(self.exporter.logger, level="WARNING") as captured:
            passed, messages = tm.check_hierarchy_vs_existing_fbx()
        self.assertTrue(passed, messages)
        self.assertTrue(any("set aside" in m for m in captured.output))

    def test_an_unstamped_baseline_defers_to_its_deliverables_sidecar(self):
        """...while a scene that exported the deliverable before keeps being
        diffed: its sidecar holds what the record held for it."""
        import json

        tm = self.exporter.task_manager
        self._save_scene_as("module.ma")
        tm.objects = [cmds.ls(str(self.group), l=True)[0]]
        recorded = sorted(tm._build_full_hierarchy_set()) + ["ExportGroup|Gone"]
        self._save_unstamped_baseline(recorded)
        sidecar = os.path.join(self.temp_dir, ".module.scene_data.json")
        with open(sidecar, "w") as f:
            json.dump({"format": 3, "hierarchy": {"paths": recorded}}, f)
        tm.run = tm.run.replace(export_path=os.path.join(self.temp_dir, "module.fbx"))

        passed, messages = tm.check_hierarchy_vs_existing_fbx()
        self.assertFalse(passed, "a pre-stamp scene must keep being diffed")
        self.assertTrue(any("Gone" in m for m in messages))

    def test_an_unstamped_baseline_still_diffs_every_deliverable_it_covered(self):
        """A pre-stamp record covering TWO deliverables is set aside, and the
        first export adopts its own sidecar and records that scope alone. The
        adoption ran only into an EMPTY record, so the second deliverable's
        sidecar was never read: its next export had nothing to diff and
        passed a deleted child (reading the legacy record, HEAD caught it)."""
        import json

        blade = cmds.polyCube(name="BladeCube")[0]
        hilt = cmds.polyCube(name="HiltCube")[0]
        group_b = cmds.group(blade, hilt, name="BladeGroup")
        tm = self.exporter.task_manager
        self._save_scene_as("module.ma")
        tm.objects = [cmds.ls(str(self.group), l=True)[0]]
        paths_a = sorted(tm._build_full_hierarchy_set())
        tm.objects = [cmds.ls(str(group_b), l=True)[0]]
        paths_b = sorted(tm._build_full_hierarchy_set())
        self._save_unstamped_baseline(paths_a + paths_b)
        for stem, paths in (("asset_a", paths_a), ("asset_b", paths_b)):
            sidecar = os.path.join(self.temp_dir, f".{stem}.scene_data.json")
            with open(sidecar, "w") as f:
                json.dump({"format": 3, "hierarchy": {"paths": paths}}, f)

        tm.objects = [cmds.ls(str(self.group), l=True)[0]]
        passed, messages = self._check_with_output("asset_a.fbx")
        self.assertTrue(passed, messages)

        cmds.delete(blade)
        tm.objects = [cmds.ls(str(group_b), l=True)[0]]
        passed, messages = self._check_with_output("asset_b.fbx")
        self.assertFalse(passed, "the second deliverable must still be diffed")
        self.assertTrue(any("BladeCube" in m for m in messages))

    def test_hierarchy_check_unreadable_baseline_warns(self):
        """A baseline that exists but cannot be read must be SEEN, not silently
        replaced: the export went structurally unchecked either way, and a fresh
        baseline written over the broken one hides that it ever happened."""
        from mayatk.env_utils.hierarchy_sync.hierarchy_baseline import (
            HierarchyBaseline,
        )
        from mayatk.node_utils.data_nodes import DataNodes

        DataNodes.write(ptk.Scope.PRIVATE, HierarchyBaseline.ATTR_NAME, "{not json")
        self.exporter.task_manager.objects = [cmds.ls(str(self.cube), l=True)[0]]
        self.exporter.task_manager.run = self.exporter.task_manager.run.replace(
            export_path=os.path.join(self.temp_dir, "test.fbx")
        )

        with self.assertLogs(self.exporter.logger, level="WARNING") as captured:
            passed, messages = (
                self.exporter.task_manager.check_hierarchy_vs_existing_fbx()
            )
        self.assertTrue(passed)
        self.assertTrue(any("unreadable" in m.lower() for m in captured.output))
        self.assertTrue(any("unreadable" in m.lower() for m in messages))

    def test_sidecar_records_accepted_diff_then_clears_it(self):
        """A failed-then-accepted check lands in hierarchy.last_diff; the
        next clean export drops it (and the stash never leaks across runs)."""
        import json

        export_path = os.path.join(self.temp_dir, "test.fbx")
        manifest_path = os.path.join(self.temp_dir, ".test.scene_data.json")

        tm = self.exporter.task_manager
        tm.objects = [
            cmds.ls(str(self.group), l=True)[0],
            cmds.ls(str(self.cube), l=True)[0],
        ]
        tm.run = tm.run.replace(export_path=export_path)
        current = sorted(tm._build_full_hierarchy_set())
        baseline = current + ["ExportGroup|Gone"]
        with open(manifest_path, "w") as f:
            json.dump({"format": 3, "hierarchy": {"paths": baseline}}, f)

        passed, _ = tm.check_hierarchy_vs_existing_fbx()
        self.assertFalse(passed)

        # User accepts and the export completes → the write records the diff.
        tm.write_scene_data_sidecar()
        with open(manifest_path, encoding="utf-8") as f:
            raw = json.load(f)
        self.assertEqual(raw["hierarchy"]["last_diff"]["missing"], ["ExportGroup|Gone"])
        # The stash's routing tag is an absolute authoring path — the
        # sidecar ships beside the deliverable and records no machine
        # paths, so the tag must never reach disk.
        self.assertNotIn("export_path", raw["hierarchy"]["last_diff"])
        self.assertNotIn(self.temp_dir.replace("\\", "/").split("/")[-1], str(raw))
        self.assertIsNone(tm._hierarchy_last_diff)

        # Next export: check now matches the recorded baseline → the clean
        # write drops the record.
        passed, _ = tm.check_hierarchy_vs_existing_fbx()
        self.assertTrue(passed)
        tm.write_scene_data_sidecar()
        with open(manifest_path, encoding="utf-8") as f:
            raw = json.load(f)
        self.assertNotIn("last_diff", raw["hierarchy"])

    def test_cancelled_export_diff_never_attaches_to_another_asset(self):
        """A flagged-then-cancelled export's diff must not ride into the next
        asset's manifest when the check doesn't re-run (stale check_ran flag)."""
        import json

        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]
        tm.run = tm.run.replace(export_path=os.path.join(self.temp_dir, "assetA.fbx"))
        # In the export's OWN scope: the baseline is per-scene now, so a path
        # under an unrelated root is a different scope rather than a missing
        # node. The subject of this test is the last_diff leak, not the diff.
        with open(os.path.join(self.temp_dir, ".assetA.scene_data.json"), "w") as f:
            json.dump(
                {
                    "format": 3,
                    "hierarchy": {
                        "paths": ["ExportGroup|ExportCube", "ExportGroup|Phantom"]
                    },
                },
                f,
            )

        passed, _ = tm.check_hierarchy_vs_existing_fbx()
        self.assertFalse(passed)
        self.assertIsNotNone(tm._hierarchy_last_diff)

        # Export A is cancelled; a different asset exports next with the
        # check off (_hierarchy_check_ran deliberately survives — existing
        # behavior — so the write proceeds).
        tm.run = tm.run.replace(export_path=os.path.join(self.temp_dir, "assetB.fbx"))
        tm.write_scene_data_sidecar()

        with open(
            os.path.join(self.temp_dir, ".assetB.scene_data.json"),
            encoding="utf-8",
        ) as f:
            raw = json.load(f)
        self.assertNotIn("last_diff", raw["hierarchy"])
        self.assertIsNone(tm._hierarchy_last_diff)

    def test_hierarchy_set_excludes_intermediate_shapes(self):
        """The recorded hierarchy is what FBX ships: no ``ShapeOrig``.

        Bug: a mesh that picked up a deformer (or history on a history-free
        mesh) between two exports grew an intermediate ``…ShapeOrig`` shape, and the check
        reported it as ``+ GRP|mesh|meshShapeOrig`` although FBX never writes
        an intermediate as a node.  Fixed: 2026-08-17
        """
        tm = self.exporter.task_manager
        # A deformer parks the pre-deformation mesh on an intermediate shape.
        cmds.cluster(self.cube)
        orig = [
            s
            for s in cmds.listRelatives(self.cube, shapes=True, fullPath=True)
            if cmds.getAttr(f"{s}.intermediateObject")
        ]
        self.assertEqual(len(orig), 1, "fixture: an Orig shape must exist")

        # The primitive: only the intermediate goes; the transform and the
        # live shape pass through (ancestor closure would re-add the
        # transform path anyway, so assert on the primitive, not the set).
        from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import (
            SceneDataSidecar,
        )

        cube_long = cmds.ls(str(self.cube), l=True)[0]
        live = cmds.ls(f"{cube_long}|ExportCubeShape", l=True)[0]
        self.assertEqual(
            SceneDataSidecar.drop_intermediate([cube_long, live, orig[0]]),
            [cube_long, live],
        )

        # Leaves-only set (visible mode) …
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]
        paths = tm._build_full_hierarchy_set()
        self.assertNotIn("ExportGroup|ExportCube|ExportCubeShapeOrig", paths)
        self.assertIn("ExportGroup|ExportCube|ExportCubeShape", paths)
        # … and the intermediate handed over as a first-class object ("all"
        # mode lists it directly) is dropped just the same.
        tm.objects = [cmds.ls(str(self.cube), l=True)[0], orig[0]]
        self.assertNotIn(
            "ExportGroup|ExportCube|ExportCubeShapeOrig",
            tm._build_full_hierarchy_set(),
        )

    def test_hierarchy_check_report_replay_group_vs_leaves_with_new_orig(self):
        """Replay of the field report: baseline written from a group-selected
        export (bare group entry, no Orig), re-export leaves-only after an
        edit left construction history on ONE mesh — must pass clean."""
        import json

        tm = self.exporter.task_manager
        export_path = os.path.join(self.temp_dir, "HOOKS_PINS.fbx")
        tm.run = tm.run.replace(export_path=export_path)
        baseline = [
            "ExportGroup",
            "ExportGroup|ExportCube",
            "ExportGroup|ExportCube|ExportCubeShape",
            "ExportGroup|ExportSphere",
            "ExportGroup|ExportSphere|ExportSphereShape",
        ]
        with open(os.path.join(self.temp_dir, ".HOOKS_PINS.scene_data.json"), "w") as f:
            json.dump({"format": 3, "hierarchy": {"paths": baseline}}, f)

        cmds.cluster(self.cube)  # leaves an ExportCubeShapeOrig behind
        tm.objects = [
            cmds.ls(str(self.cube), l=True)[0],
            cmds.ls(str(self.sphere), l=True)[0],
        ]
        passed, messages = tm.check_hierarchy_vs_existing_fbx()
        self.assertTrue(passed, messages)
        self.assertIsNone(tm._hierarchy_last_diff)

    def test_hierarchy_check_cleans_stale_diff(self):
        """Verify stale diff report is removed when check passes."""
        import json

        export_path = os.path.join(self.temp_dir, "test.fbx")
        manifest_path = os.path.join(self.temp_dir, ".test.hierarchy.json")
        diff_path = os.path.join(self.temp_dir, ".test.hierarchy_diff.txt")

        with open(diff_path, "w") as f:
            f.write("stale")

        # Build manifest from actual expanded hierarchy so check passes
        self.exporter.task_manager.objects = [
            cmds.ls(str(self.group), l=True)[0],
            cmds.ls(str(self.cube), l=True)[0],
            cmds.ls(str(self.sphere), l=True)[0],
        ]
        self.exporter.task_manager.run = self.exporter.task_manager.run.replace(
            export_path=export_path
        )
        current = sorted(self.exporter.task_manager._build_full_hierarchy_set())
        with open(manifest_path, "w") as f:
            json.dump({"paths": current, "object_count": len(current)}, f)

        passed, _ = self.exporter.task_manager.check_hierarchy_vs_existing_fbx()
        self.assertTrue(passed)
        self.assertFalse(os.path.exists(diff_path))

    def test_hierarchy_check_top_level_rollup(self):
        """Verify log messages show top-level parents, not every child."""
        import json

        export_path = os.path.join(self.temp_dir, "test.fbx")
        manifest_path = os.path.join(self.temp_dir, ".test.hierarchy.json")

        # Manifest with a deep hierarchy that won't match empty objects
        previous = [
            "group",
            "group|childA",
            "group|childA|grandchild",
            "group|childB",
        ]
        with open(manifest_path, "w") as f:
            json.dump({"paths": previous, "object_count": len(previous)}, f)

        # Empty objects → _build_full_hierarchy_set returns empty set
        self.exporter.task_manager.objects = []
        self.exporter.task_manager.run = self.exporter.task_manager.run.replace(
            export_path=export_path
        )

        passed, messages = self.exporter.task_manager.check_hierarchy_vs_existing_fbx()
        self.assertFalse(passed)
        # 4 missing nodes rolled up to 1 top-level
        self.assertTrue(any("1 top-level" in m for m in messages))
        detail_lines = [m for m in messages if m.strip().startswith("−")]
        self.assertEqual(len(detail_lines), 1)
        self.assertIn("group", detail_lines[0])

    def test_hierarchy_check_detects_reparenting(self):
        """Check fails when scene contents are grouped under a new parent.

        Bug: self.objects only contained selected roots, not descendants.
        _build_clean_path_set produced a shallow manifest that missed
        structural changes below the selected level.
        Fixed: 2026-04-10
        """
        import json

        export_path = os.path.join(self.temp_dir, "test.fbx")
        manifest_path = os.path.join(self.temp_dir, ".test.hierarchy.json")

        # Write manifest from current hierarchy (before reparenting)
        self.exporter.task_manager.objects = [cmds.ls(str(self.group), l=True)[0]]
        self.exporter.task_manager.run = self.exporter.task_manager.run.replace(
            export_path=export_path
        )
        original = sorted(self.exporter.task_manager._build_full_hierarchy_set())
        with open(manifest_path, "w") as f:
            json.dump({"paths": original, "object_count": len(original)}, f)

        # Reparent everything under a new group
        new_parent = cmds.group(self.group, name="NewParent")
        self.exporter.task_manager.objects = [cmds.ls(str(new_parent), l=True)[0]]

        passed, messages = self.exporter.task_manager.check_hierarchy_vs_existing_fbx()
        self.assertFalse(
            passed,
            "Hierarchy check must detect reparenting under a new group",
        )

    def test_root_transforms_detects_offset_group(self):
        """Root transform check finds group ancestors of geometry objects.

        Bug: check_root_default_transforms used cmds.ls(self.objects,
        assemblies=True) but self.objects only contained geometry
        transforms (never assemblies), so the check always passed.
        Fixed: 2026-04-10
        """
        cmds.setAttr(f"{cmds.ls(str(self.group), l=True)[0]}.translateX", 10)

        # Objects are geometry — exactly what get_visible_geometry returns
        self.exporter.task_manager.objects = [
            cmds.ls(str(self.cube), l=True)[0],
            cmds.ls(str(self.sphere), l=True)[0],
        ]

        passed, messages = self.exporter.task_manager.check_root_default_transforms()
        self.assertFalse(passed, "Should fail — root group has non-default transforms")
        found = any("ExportGroup" in m for m in messages)
        self.assertTrue(found, "ExportGroup should be flagged in messages")

    def test_root_transforms_passes_for_default_group(self):
        """Root transform check passes when root group has identity transforms."""
        self.exporter.task_manager.objects = [
            cmds.ls(str(self.cube), l=True)[0],
            cmds.ls(str(self.sphere), l=True)[0],
        ]

        passed, _ = self.exporter.task_manager.check_root_default_transforms()
        self.assertTrue(passed)

    def test_root_transforms_reports_a_frozen_root_without_failing(self):
        """A frozen root reads identity, so the live channels alone cannot tell
        "authored at identity" from "identity because someone froze it" — and
        the second still carries a transform in its bake history. Report it,
        but don't fail: the scene as it stands really is at identity."""
        import mayatk as mtk

        root = cmds.ls(str(self.group), l=True)[0]
        cmds.setAttr(f"{root}.translateX", 10)
        mtk.XformUtils.freeze_transforms(root, force=True)

        self.exporter.task_manager.objects = [
            cmds.ls(str(self.cube), l=True)[0],
            cmds.ls(str(self.sphere), l=True)[0],
        ]

        passed, messages = self.exporter.task_manager.check_root_default_transforms()
        self.assertTrue(passed, "a frozen root is at identity — it must not fail")
        self.assertTrue(
            any("FROZEN" in m for m in messages),
            "the frozen root must be reported distinctly",
        )

    def test_root_transforms_stays_silent_for_a_genuinely_default_root(self):
        self.exporter.task_manager.objects = [
            cmds.ls(str(self.cube), l=True)[0],
            cmds.ls(str(self.sphere), l=True)[0],
        ]

        passed, messages = self.exporter.task_manager.check_root_default_transforms()
        self.assertTrue(passed)
        self.assertFalse(
            any("FROZEN" in m for m in messages),
            "an unfrozen identity root must produce no frozen-root note",
        )

    def test_root_transforms_detects_wrapper_group(self):
        """Root transform check catches a wrapper group with non-default transforms.

        Bug: Wrapping the entire scene in a new group was undetected.
        Fixed: 2026-04-10
        """
        wrapper = cmds.group(self.group, name="WrapperGroup")
        cmds.setAttr(f"{cmds.ls(str(wrapper), l=True)[0]}.translateY", 5)

        self.exporter.task_manager.objects = [
            cmds.ls(str(self.cube), l=True)[0],
            cmds.ls(str(self.sphere), l=True)[0],
        ]

        passed, messages = self.exporter.task_manager.check_root_default_transforms()
        self.assertFalse(passed, "Wrapper group with offset should be caught")
        found = any("WrapperGroup" in m for m in messages)
        self.assertTrue(found, "WrapperGroup should be flagged")

    def test_hierarchy_check_detects_wrapper_group(self):
        """Hierarchy diff check catches a new wrapper group.

        Bug: Wrapping the entire scene in a new group was undetected.
        Fixed: 2026-04-10
        """
        import json

        export_path = os.path.join(self.temp_dir, "test.fbx")
        manifest_path = os.path.join(self.temp_dir, ".test.hierarchy.json")

        # Manifest from a previous export (no wrapper)
        previous = ["ExportGroup|ExportCube", "ExportGroup|ExportSphere"]
        with open(manifest_path, "w") as f:
            json.dump({"paths": previous, "object_count": len(previous)}, f)

        # Now wrap everything — long paths gain a prefix
        cmds.group(self.group, name="WrapperGroup")  # side effect: wraps the hierarchy
        self.exporter.task_manager.objects = [
            cmds.ls(str(self.cube), l=True)[0],
            cmds.ls(str(self.sphere), l=True)[0],
        ]
        self.exporter.task_manager.run = self.exporter.task_manager.run.replace(
            export_path=export_path
        )

        passed, messages = self.exporter.task_manager.check_hierarchy_vs_existing_fbx()
        self.assertFalse(passed, "Wrapped hierarchy should differ from manifest")
        # The new diff summarises wrapping as "Reparenting detected"; the
        # legacy "missing"/"new" wording only surfaces for items that
        # *aren't* explained by reparenting.
        joined = " ".join(m.lower() for m in messages)
        self.assertTrue(
            "reparenting" in joined or "missing" in joined,
            f"Expected reparenting/missing diff, got: {messages}",
        )


class TestExportDataNodeOption(MayaTkTestCase):
    """The global default-on 'Export Scene Data Node' exporter option.

    Ensures the shared ``data_export`` carrier ships regardless of export mode,
    for ANY metadata producer (shots or audio) — not gated on shots like the
    older takes task was.
    """

    def setUp(self):
        super().setUp()
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager
        from mayatk.anim_utils.shots._shots import ShotStore
        from mayatk.env_utils.fbx_utils import FbxUtils

        FbxUtils.reset_takes()
        ShotStore.clear_active()
        self.tm = TaskManager(logging.getLogger("test_export_data_node"))
        self.cube = self.create_test_cube("dnCube")
        self.tm.objects = cmds.ls(self.cube, long=True)

    def tearDown(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        ShotStore.clear_active()
        super().tearDown()

    def test_option_is_default_on(self):
        defs = self.tm.task_definitions
        self.assertIn("export_data_node", defs)
        self.assertEqual(defs["export_data_node"]["widget_type"], "QCheckBox")
        self.assertTrue(defs["export_data_node"]["setChecked"])

    def test_folding_the_carrier_in_keeps_the_clips_choice(self):
        """A "shots" choice survives the carrier joining the export set.

        Regression: ``apply_declared_takes`` records the Animation Clips
        choice, then folds the carrier in -- which assigns ``self.objects``,
        whose setter resets the choice to "both" as per-run hygiene. With
        "Export Scene Data Node" off (so nothing had added the carrier yet),
        "Shots Only" shipped both halves of the animation.
        Added: 2026-09-13
        """
        from mayatk.env_utils.fbx_utils import FbxUtils
        from mayatk.node_utils.data_nodes import DataNodes

        with (
            patch.object(FbxUtils, "apply_takes_from_node", return_value=2),
            patch.object(FbxUtils, "bake_range", return_value=(1, 50)),
            patch.object(DataNodes, "get_export_nodes", return_value=["|phantom"]),
        ):
            self.tm.apply_declared_takes("shots")
        self.assertIn("|phantom", self.tm.objects, "the carrier joined the set")
        self.assertEqual(self.tm._clip_mode, "shots")
        self.assertEqual(self.tm._required_range_coverage, (1, 50))

    def test_option_runs_before_takes_in_order(self):
        order = self.tm.TASK_ORDER
        self.assertIn("export_data_node", order)
        self.assertLess(
            order.index("export_data_node"), order.index("apply_declared_takes")
        )

    def test_takes_are_default_on_beside_the_carrier(self):
        """The two default TOGETHER, or the deliverable contradicts itself.

        The carrier ships ``shot_metadata`` naming one clip per shot; with the
        split off, the FBX (and the GLB converted from it) carries that
        metadata and none of the clips it names. The row is a ComboBox now --
        the sequence became a CHOICE rather than an always-on extra -- so the
        default is the entry that ships both, which is what the retired
        checkbox did when ticked.
        """
        defs = self.tm.task_definitions
        row = defs["apply_declared_takes"]
        options = list(self.tm._animation_clips_options.values())
        self.assertEqual(
            options[row["setCurrentIndex"]],
            "both",
            "shots would export as metadata describing clips the file lacks",
        )
        self.assertNotEqual(
            row.get("object_name"),
            "apply_declared_takes",
            "a preset holding the old checkbox's BOOL must not restore onto a "
            "combo that persists by index",
        )

    def test_the_legacy_checkbox_values_still_mean_what_they_did(self):
        """A headless caller (or an old script) still passes True/False.

        The row became a combo, but the TASK key did not change -- so the
        booleans that row used to carry have to keep naming the same
        deliverable: ticked kept the sequence beside the shots, unticked split
        nothing and shipped the sequence alone.
        """
        self.assertEqual(ptk.ExportRun.clip_mode(True), "both")
        self.assertEqual(ptk.ExportRun.clip_mode(False), "full")
        self.assertEqual(ptk.ExportRun.clip_mode(None), "full")
        self.assertEqual(ptk.ExportRun.clip_mode("shots"), "shots")
        with self.assertRaises(ValueError):
            ptk.ExportRun.clip_mode("everything")

    def test_every_offered_mode_is_one_the_converter_accepts(self):
        """The row's values and pythontk's vocabulary are one contract.

        The labels are the panel's business; the VALUES are the converter's,
        and a row offering a mode ``apply_glb_clips`` rejects would raise mid
        conversion -- after the FBX is already written.
        """

        self.assertEqual(
            set(self.tm._animation_clips_options.values()),
            set(ptk.MeshConvert.ANIMATION_CLIP_MODES),
        )

    def test_a_second_export_does_not_inherit_the_first_ones_clips(self):
        """``create_glb`` reads the choice AFTER the write, so it is per-run.

        Left standing, an export whose panel no longer carries the row would
        convert against the previous run's choice and silently ship half the
        animation. ``begin_run`` is the reset; assigning ``objects`` is not
        (tasks do that mid-run -- the carrier fold-in used to flip a "shots"
        choice back to "both" through the setter).
        """
        self.tm.apply_declared_takes("full")
        self.assertEqual(self.tm._clip_mode, "full")

        self.tm.objects = list(self.tm.objects or [])
        self.assertEqual(self.tm._clip_mode, "full")

        self.tm.begin_run(self.tm.run)
        self.assertEqual(self.tm._clip_mode, "both")

    def test_full_sequence_mode_splits_nothing_and_tells_the_conversion(self):
        """The GLB half is decided on the deliverable, not in the scene.

        The sequence has to be CUT before it can be dropped, so the task
        records the choice and ``create_glb`` carries it to the converter.
        """
        self.tm.apply_declared_takes("full")

        self.assertEqual(self.tm._clip_mode, "full")

    def test_a_full_sequence_export_declares_its_mode_on_the_shot_metadata(self):
        """Full Sequence Only ships one sequence while the shot record still
        declares every shot's take (each clip's range), which the deliverable
        gate cannot tell from a split that silently failed -- so the export
        DECLARES its mode on the ``shot_metadata`` envelope and the gate reads
        it. Measured before: the ``fbx_takes`` gate failed a correct full-mode
        export "declared but absent".
        Added: 2026-09-15
        """
        import json
        import shutil
        import tempfile

        from mayatk.anim_utils.shots._shots import ShotStore
        from mayatk.env_utils.scene_exporter._scene_exporter import SceneExporter

        try:
            cmds.loadPlugin("fbxmaya", quiet=True)
        except RuntimeError:
            self.skipTest("FBX plugin not available")
        cmds.setKeyframe(self.cube, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(self.cube, attribute="translateX", time=40, value=10)
        store = ShotStore()
        ShotStore.set_active(store)
        store.define_shot("ShotA", 1, 20, objects=[self.cube])
        store.define_shot("ShotB", 21, 40, objects=[self.cube])
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        exporter = SceneExporter(log_level="WARNING")
        exporter.confirm = lambda question: False
        self.assertTrue(
            exporter.perform_export(
                export_dir=out,
                objects=[self.cube],
                output_name="full_mode",
                tasks={"export_data_node": True, "apply_declared_takes": "full"},
            )
        )
        with open(
            os.path.join(out, ".full_mode.scene_data.json"), encoding="utf-8"
        ) as f:
            data = json.load(f)["data_export"]
        meta = data["shot_metadata"]
        self.assertEqual(meta.get(ptk.MeshConvert.SHOT_CLIP_MODE_KEY), "full")
        # One record: each clip carries its range, and no take list rides
        # beside it.
        self.assertEqual(
            [(s["clip"], s["start"], s["end"]) for s in meta["shots"]],
            [("ShotA", 1, 20), ("ShotB", 21, 40)],
        )
        self.assertNotIn(ptk.SceneRecords.FBX_TAKES.key, data)
        report = ptk.ExportVerifier(fbx=os.path.join(out, "full_mode.fbx")).run()
        gate = [row.status for row in report.rows if row.check == "fbx_takes"]
        self.assertEqual(gate, ["SKIP"], report.summary())

    def test_includes_carrier_and_publishes_with_shots(self):
        from mayatk.anim_utils.shots._shots import ShotStore
        from mayatk.node_utils.data_nodes import DataNodes

        store = ShotStore()
        ShotStore.set_active(store)
        store.define_shot("Intro", 1, 50, description="opening")

        self.tm.export_data_node()

        self.assertNodeExists(DataNodes.EXPORT)
        self.assertTrue(any(o.endswith(DataNodes.EXPORT) for o in self.tm.objects))
        self.assertIn(
            "opening", DataNodes.read(ptk.Scope.DELIVERABLE, DataNodes.SHOT_METADATA)
        )

    def test_includes_carrier_with_audio_and_no_shots(self):
        # Audio but NO shots — the old shots-gated takes task skipped this case
        # entirely, so the audio manifest never shipped.
        from mayatk.audio_utils._audio_utils import AudioUtils
        from mayatk.node_utils.data_nodes import DataNodes

        AudioUtils.write_key("footstep", frame=10, value=1)
        AudioUtils.write_key("footstep", frame=15, value=0)

        self.tm.export_data_node()

        self.assertNodeExists(DataNodes.EXPORT)
        self.assertTrue(any(o.endswith(DataNodes.EXPORT) for o in self.tm.objects))
        attrs = cmds.listAttr(DataNodes.EXPORT, userDefined=True) or []
        self.assertIn("audio_manifest", attrs)
        self.assertIn("footstep", cmds.getAttr(f"{DataNodes.EXPORT}.audio_manifest"))

    def test_a_run_stopped_after_the_publish_unstages_the_write(self):
        """``export_data_node`` stages the write (the curve proxies; a preview
        stands down) so the checks after it see what ships, and only the
        bracket's ``end_export`` finished that staging. A run that stopped
        before the bracket -- a declined failed check, an empty export set, a
        cancel, a raising task -- left the proxies in the scene and the
        preview detached.
        Added: 2026-09-18
        """
        from mayatk.env_utils.fbx_utils import FbxUtils
        from mayatk.mat_utils.render_opacity.render_effects import RenderEffects

        def proxies():
            return (
                cmds.ls(
                    f"*.{RenderEffects.PROXY_MARKER}",
                    objectsOnly=True,
                    recursive=True,
                )
                or []
            )

        RenderEffects.key_pulse([self.cube], start=1, end=10, period=10)
        finished = []  # a session stager: the shadow preview's re-attach
        FbxUtils.register_export_stager(
            "preview_probe", finish=lambda: finished.append(True)
        )
        self.addCleanup(FbxUtils.unregister_export_stager, "preview_probe")
        exporter = SceneExporter(log_level="WARNING")
        exporter.confirm = lambda question: False  # decline the override
        tm = exporter.task_manager

        def _publish_then_fail(tasks):
            tm.export_data_node()
            self.assertTrue(proxies(), "precondition: the task staged the write")
            tm._last_failed_checks = ["check_path_length"]
            return False

        tm.run_tasks = _publish_then_fail
        self.assertFalse(
            exporter.perform_export(
                export_dir=os.path.dirname(self.temp_path("stopped_run")),
                objects=[self.cube],
                output_name="stopped_run",
                tasks={"export_data_node": True},
            )
        )
        self.assertEqual(proxies(), [], "the curve proxies outlived the run")
        self.assertEqual(finished, [True], "the session stager never finished")

    def test_a_run_without_the_carrier_task_still_finishes_the_session_stagers(self):
        """Export Scene Data Node off: the takes task publishes instead, which
        PREPARES the session stagers too -- and it staged no finish, so a run
        stopped before its write left a shadow preview detached (blendertk's
        mirror already staged it; restore-point audit, 2026-09-24)."""
        from mayatk.env_utils.fbx_utils import FbxUtils

        prepared, finished = [], []
        FbxUtils.register_export_stager(
            "preview_probe_takes",
            prepare=lambda: prepared.append(True),
            finish=lambda: finished.append(True),
        )
        self.addCleanup(FbxUtils.unregister_export_stager, "preview_probe_takes")
        exporter = SceneExporter(log_level="WARNING")
        exporter.confirm = lambda question: False  # decline the override
        tm = exporter.task_manager

        def _takes_then_fail(tasks):
            tm.apply_declared_takes("both")
            self.assertTrue(prepared, "precondition: the publish staged the write")
            tm._last_failed_checks = ["check_path_length"]
            return False

        tm.run_tasks = _takes_then_fail
        self.assertFalse(
            exporter.perform_export(
                export_dir=os.path.dirname(self.temp_path("stopped_takes_run")),
                objects=[self.cube],
                output_name="stopped_takes_run",
                tasks={"apply_declared_takes": "both"},
            )
        )
        self.assertTrue(finished, "the session stager never finished")

    def test_noop_without_metadata(self):
        from mayatk.node_utils.data_nodes import DataNodes

        before = list(self.tm.objects)
        self.tm.export_data_node()
        # No producer wrote anything → carrier never created, selection untouched.
        self.assertFalse(cmds.objExists(DataNodes.EXPORT))
        self.assertEqual(self.tm.objects, before)

    def test_sidecar_written_when_carrier_ships(self):
        # New with the scene-data sidecar: a metadata-carrying export leaves
        # the record even when the hierarchy check never ran.  Uses a channel
        # no producer owns — export_data_node's refresh clears stale
        # producer-owned channels (e.g. lightmap_metadata with no bake).
        import tempfile
        from mayatk.node_utils.data_nodes import DataNodes
        from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import (
            SceneDataSidecar,
        )

        DataNodes.write(ptk.Scope.DELIVERABLE, "test_channel", '{"version": 1}')
        self.tm.export_data_node()  # folds the carrier into the export set
        with tempfile.TemporaryDirectory() as d:
            self.tm.run = self.tm.run.replace(export_path=os.path.join(d, "dn.fbx"))
            self.tm.write_scene_data_sidecar()
            data = SceneDataSidecar.read_data(self.tm.export_path)
            self.assertIsNotNone(data)
            self.assertEqual(data.get("test_channel"), {"version": 1})
            paths = SceneDataSidecar.read_manifest(self.tm.export_path)
            self.assertTrue(any("dnCube" in p for p in paths))

    def test_after_a_glb_the_sidecar_records_the_lightmaps_it_ships(self):
        """The GLB pass corrects the lightmap manifest the GLB carries -- the
        encoded map, the scalar that restores the bake range -- so a sidecar
        written from the scene restated the pre-encode .exr @ 1.0 beside a GLB
        saying otherwise. With a GLB written the record is the GLB's copy; an
        FBX-only run keeps the scene's.
        Added: 2026-09-15
        """
        import json
        import struct
        import tempfile

        from mayatk.node_utils.data_nodes import DataNodes
        from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import (
            SceneDataSidecar,
        )

        entry = {"name": "dnCube", "map": "room_Lightmap.exr", "intensity": 1.0}
        scene_copy = {"version": 1, "objects": [entry]}
        shipped = {
            "version": 1,
            "objects": [dict(entry, map="room_Lightmap.png", intensity=0.5)],
        }
        DataNodes.write(
            ptk.Scope.DELIVERABLE, "lightmap_metadata", json.dumps(scene_copy)
        )
        self.tm.export_data_node()  # folds the carrier in (and refreshes it)
        DataNodes.write(
            ptk.Scope.DELIVERABLE, "lightmap_metadata", json.dumps(scene_copy)
        )
        with tempfile.TemporaryDirectory() as d:
            gltf = {
                "asset": {"version": "2.0"},
                "nodes": [
                    {
                        "name": "data_export",
                        "extras": {"lightmap_metadata": json.dumps(shipped)},
                    }
                ],
            }
            chunk = json.dumps(gltf).encode("utf-8")
            chunk += b" " * (-len(chunk) % 4)
            glb = os.path.join(d, "dn.glb")
            with open(glb, "wb") as f:
                f.write(struct.pack("<4sII", b"glTF", 2, 20 + len(chunk)))
                f.write(struct.pack("<I4s", len(chunk), b"JSON") + chunk)
            self.tm.run = self.tm.run.replace(export_path=os.path.join(d, "dn.fbx"))

            self.tm.write_scene_data_sidecar(glb_path=glb)
            data = SceneDataSidecar.read_data(self.tm.export_path)
            self.assertEqual(data.get("lightmap_metadata"), shipped)

            self.tm.write_scene_data_sidecar()
            data = SceneDataSidecar.read_data(self.tm.export_path)
            self.assertEqual(data.get("lightmap_metadata"), scene_copy)

    def test_data_not_recorded_when_carrier_excluded(self):
        # The carrier exists in the scene but is NOT in the export set (e.g.
        # 'selected' mode with the export_data_node task off): its channels
        # did not ship, so the record must not claim them — and with nothing
        # else to record, no sidecar is written at all.
        import tempfile
        from mayatk.node_utils.data_nodes import DataNodes
        from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import (
            SceneDataSidecar,
        )

        DataNodes.write(ptk.Scope.DELIVERABLE, "test_channel", '{"version": 1}')
        with tempfile.TemporaryDirectory() as d:
            self.tm.run = self.tm.run.replace(export_path=os.path.join(d, "dn.fbx"))
            self.tm.write_scene_data_sidecar()
            self.assertIsNone(SceneDataSidecar.read_manifest(self.tm.export_path))

    def test_no_sidecar_without_metadata_or_check(self):
        import tempfile
        from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import (
            SceneDataSidecar,
        )

        with tempfile.TemporaryDirectory() as d:
            self.tm.run = self.tm.run.replace(export_path=os.path.join(d, "dn.fbx"))
            self.tm.write_scene_data_sidecar()
            self.assertIsNone(SceneDataSidecar.read_manifest(self.tm.export_path))

    def test_summary_logs_embedded_shot_count(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        ShotStore.set_active(store)
        store.define_shot("Intro", 1, 50, description="opening")
        store.define_shot("Outro", 51, 100)

        with self.assertLogs("test_export_data_node", level="INFO") as cm:
            self.tm.export_data_node()
        self.assertTrue(
            any("shot_metadata (2 entries)" in m for m in cm.output),
            f"post-export summary missing shot count: {cm.output}",
        )

    def test_summary_logs_audio_event_count(self):
        from mayatk.audio_utils._audio_utils import AudioUtils

        AudioUtils.write_key("footstep", frame=10, value=1)
        AudioUtils.write_key("footstep", frame=15, value=0)
        AudioUtils.write_key("jump", frame=30, value=1)

        with self.assertLogs("test_export_data_node", level="INFO") as cm:
            self.tm.export_data_node()
        self.assertTrue(
            any("audio_manifest (2 entries)" in m for m in cm.output),
            f"post-export summary missing audio count: {cm.output}",
        )

    def test_carrier_ships_in_selected_mode_real_export(self):
        """Regression: the hidden carrier must reach the FBX even in 'selected'
        export mode.  That mode exports the live selection and never re-selects
        from self.objects, so appending the carrier there is not enough — it has
        to join the actual export selection or it silently never ships.
        """
        try:
            if not cmds.pluginInfo("fbxmaya", q=True, loaded=True):
                cmds.loadPlugin("fbxmaya")
        except Exception:
            self.skipTest("FBX plugin not available")

        from mayatk.anim_utils.shots._shots import ShotStore
        from mayatk.node_utils.data_nodes import DataNodes

        store = ShotStore()
        ShotStore.set_active(store)
        store.define_shot("Intro", 1, 50, objects=[self.cube], description="opening")

        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)

        exporter = SceneExporter(log_level="DEBUG")
        cmds.select(self.cube, replace=True)  # carrier is hidden, NOT selected
        result = exporter.perform_export(
            export_dir=temp_dir,
            objects=lambda: cmds.ls(selection=True, long=True),
            file_format="FBX export",
            export_visible=False,  # 'selected' mode
            output_name="selmode_carrier",
            tasks={"export_data_node": True},
        )
        self.assertTrue(result)

        # Re-import into a fresh scene and confirm the carrier traveled along.
        out = exporter.export_path
        cmds.file(new=True, force=True)
        cmds.file(out, i=True, type="FBX", ignoreVersion=True)
        self.assertTrue(
            cmds.ls(f"*{DataNodes.EXPORT}*"),
            "data_export carrier missing from FBX exported in 'selected' mode",
        )


def _arnold_available() -> bool:
    """Return True if mtoa can be loaded (plugin installed and loadable)."""
    try:
        if cmds.pluginInfo("mtoa", query=True, loaded=True):
            return True
        cmds.loadPlugin("mtoa")
        return True
    except Exception:
        return False


class TestExcludeHdrOption(MayaTkTestCase):
    """The 'Exclude HDR Environment' exporter task strips aiSkyDomeLight nodes.

    Feature (2026-06-18): the HDR skydome is image-based scene lighting, not
    deliverable geometry, so it should not ride into a game-engine FBX — in
    'All Scene Objects' mode it is otherwise picked up by cmds.ls(transforms=).
    """

    def setUp(self):
        super().setUp()
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        self.tm = TaskManager(logging.getLogger("test_exclude_hdr"))
        self.cube = self.create_test_cube("hdrCube")
        self.tm.objects = cmds.ls(self.cube, long=True)

    def test_option_is_default_on(self):
        defs = self.tm.task_definitions
        self.assertIn("exclude_hdr", defs)
        self.assertEqual(defs["exclude_hdr"]["widget_type"], "QCheckBox")
        self.assertTrue(defs["exclude_hdr"]["setChecked"])

    def test_in_task_order_after_ignore_groups(self):
        order = self.tm.TASK_ORDER
        self.assertIn("exclude_hdr", order)
        self.assertGreater(order.index("exclude_hdr"), order.index("ignore_groups"))

    def test_noop_without_skydome(self):
        before = list(self.tm.objects)
        self.tm.exclude_hdr()
        self.assertEqual(self.tm.objects, before)

    def test_noop_with_empty_objects(self):
        self.tm.objects = []
        self.tm.exclude_hdr()  # must not raise
        self.assertEqual(self.tm.objects, [])

    @unittest.skipUnless(_arnold_available(), "Arnold (mtoa) plugin not available")
    def test_removes_skydome_keeps_geometry(self):
        from mayatk.light_utils.hdr_manager import HdrManager

        mgr = HdrManager()
        skydome = mgr.create_network(hdrMap="C:/tmp/x.exr")
        self.assertIsNotNone(skydome)
        self.addCleanup(mgr.clear)

        # Use the same full-path transform the task computes internally.
        skydome_transform = cmds.listRelatives(skydome, parent=True, fullPath=True)[0]
        cube_long = cmds.ls(self.cube, long=True)[0]
        self.tm.objects = [cube_long, skydome_transform]

        self.tm.exclude_hdr()

        self.assertIn(cube_long, self.tm.objects)
        self.assertNotIn(skydome_transform, self.tm.objects)


class TestExcludeRigHelpersOption(MayaTkTestCase):
    """The 'Exclude Rig Helpers' row drops a baked rig's apparatus from the FBX.

    Feature (2026-09-19): a production assembly shipped ~600 of ~2480 GLB nodes
    that drove nothing a skin references and held no mesh -- half its
    animation data, each node baked again by FBX2glTF at every frame. The row
    is a post-write mode (like Verify The Written File): the census names the
    rig's apparatus in the scene (``RigGraphExtractor.machinery``) and
    ``FbxUtils.drop_rig_apparatus`` removes it from the written file, before
    the GLB conversion reads it.
    """

    def setUp(self):
        super().setUp()
        from test_rig_graph_extract import _apparatus_scene

        try:
            if not cmds.pluginInfo("fbxmaya", q=True, loaded=True):
                cmds.loadPlugin("fbxmaya")
        except Exception:
            self.skipTest("FBX plugin not available")
        _apparatus_scene()
        self.exporter = SceneExporter(log_level="WARNING")
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)

    def _export(self, name, **tasks):
        roots = cmds.ls(
            ["skel_root", "body", "rig", "artist_null", "marker_loc"], long=True
        )
        self.assertTrue(
            self.exporter.perform_export(
                export_dir=self.temp_dir,
                objects=roots,
                output_name=name,
                tasks=tasks,
            )
        )
        path = os.path.join(self.temp_dir, f"{name}.fbx")
        return set(ptk.FbxFile.load(path, raw_payloads=False).object_names("Model"))

    def test_the_row_is_a_default_on_settings_mode(self):
        spec = self.exporter.task_manager.task_definitions["drop_rig_apparatus"]
        self.assertEqual(spec["widget_type"], "QCheckBox")
        self.assertEqual(spec["panel"], "settings")
        self.assertTrue(spec["setChecked"])
        # A mode, popped before dispatch -- never a task the pipeline orders.
        self.assertNotIn("drop_rig_apparatus", self.exporter.task_manager.TASK_ORDER)
        self.assertIn("drop_rig_apparatus", ptk.ExportRun.MODE_KEYS)

    def test_the_written_file_ships_without_the_rig(self):
        kept = self._export("with_rig")
        dropped = self._export("without_rig", drop_rig_apparatus=True)
        helpers = {"rig", "ctrl_GRP", "ctrl", "driver_jnt", "ik_curve", "up_loc"}
        self.assertLessEqual(helpers, kept, "off: the file is what the scene is")
        self.assertTrue(helpers.isdisjoint(dropped), sorted(dropped))
        self.assertLessEqual(
            {"skel_root", "skel_tip", "body", "artist_null", "marker_loc"}, dropped
        )
        # The scene is never edited: the next export needs the rig again.
        self.assertTrue(cmds.objExists("|rig|ctrl_GRP|ctrl"))

    def test_a_usd_run_leaves_it_inert(self):
        run, _tasks, notes = ptk.ExportRun.from_tasks(
            {"output_format": "usd", "drop_rig_apparatus": True}
        )
        self.assertFalse(run.drop_rig_apparatus)
        self.assertTrue(any("Exclude Rig Helpers" in m for _l, m in notes), notes)

    def test_the_preview_payload_drops_what_the_row_drops(self):
        """The row is on by default, so the WebXR preview's payload drops the
        same helpers: the page shows the nodes the deliverable ships. Opt-in on
        the mixin -- a DCC hand-off may rebuild the rig from those very nodes."""
        from mayatk.env_utils.handoff_export import MayaExportMixin
        from mayatk.env_utils.webxr_preview import WebXrPreview

        self.assertFalse(MayaExportMixin.drop_rig_apparatus)
        preview = WebXrPreview()
        fbx = os.path.join(self.temp_dir, "preview.fbx")
        preview._export_fbx(
            cmds.ls(["skel_root", "body", "rig"], long=True),
            fbx,
            dict(preview.params_defaults()),
        )
        names = set(ptk.FbxFile.load(fbx, raw_payloads=False).object_names("Model"))
        self.assertTrue({"rig", "ctrl", "driver_jnt"}.isdisjoint(names), sorted(names))
        self.assertLessEqual({"skel_root", "skel_tip", "body"}, names)


class TestTaskStateHygiene(MayaTkTestCase):
    """Per-run task-state regressions: stale caches and cross-run markers.

    Added: 2026-08-01 (scene-exporter robustness audit).
    """

    def setUp(self):
        super().setUp()
        self.exporter = SceneExporter(log_level="DEBUG")
        self.tm = self.exporter.task_manager
        self.cube = cmds.polyCube(name="StateHygieneCube")[0]
        self.cube_long = cmds.ls(self.cube, long=True)[0]

    def test_snap_then_tie_does_not_recreate_fractional_keys(self):
        """snap_keys_to_frame must invalidate the key-range cache before tie runs.

        Repro: fractional bookend keys are snapped to whole frames, then
        tie_all_keyframes read the STALE cached range and re-inserted keys at
        the exact fractional times the snap just removed — the pipeline then
        failed its own check_floating_point_keys.  Must fail pre-fix.
        """
        cmds.setKeyframe(self.cube, attribute="translateX", time=0.4, value=0)
        cmds.setKeyframe(self.cube, attribute="translateX", time=99.6, value=5)
        # A second, inner-range curve so the tie task has bookends to insert.
        cmds.setKeyframe(self.cube, attribute="translateY", time=10, value=0)
        cmds.setKeyframe(self.cube, attribute="translateY", time=90, value=2)

        self.tm.objects = [self.cube_long]
        # Seed the cache the way the real pipeline does (snap's own
        # _has_keyframes gate reads the cache with pre-snap ends).
        self.assertTrue(self.tm._has_keyframes)

        self.tm.snap_keys_to_frame()
        self.tm.tie_all_keyframes()

        times = cmds.keyframe(self.cube, query=True, timeChange=True) or []
        fractional = [t for t in times if abs(t - round(t)) > 1e-4]
        self.assertEqual(
            fractional,
            [],
            f"tie re-created fractional keys from a stale cache: {fractional}",
        )
        status, _ = self.tm.check_floating_point_keys()
        self.assertTrue(status, "pipeline failed its own floating-point check")

    def test_tie_and_snap_hand_back_a_shapes_curve_untouched(self):
        """The key tasks edit the export's whole DAG subtree -- a camera's
        focalLength and a light's intensity are keyed on SHAPES -- while the
        Animation Output snapshot probed descendant TRANSFORMS only, so such a
        curve kept the snap's moved keys and the tie's bookends after an export
        that promises the scene back (restore-point audit, 2026-09-24)."""
        cam, cam_shape = cmds.camera(name="restoreCam")
        cmds.setKeyframe(cam_shape, attribute="focalLength", time=10.4, value=35)
        cmds.setKeyframe(cam_shape, attribute="focalLength", time=40, value=50)
        # A transform keyed wider, so the tie has bookends to add on the shape.
        cmds.setKeyframe(self.cube, attribute="translateX", time=0, value=0)
        cmds.setKeyframe(self.cube, attribute="translateX", time=100, value=5)
        group = cmds.group(self.cube, cam, name="restoreGrp")
        plug = f"{cam_shape}.focalLength"
        before = cmds.keyframe(plug, query=True, timeChange=True)

        self.tm.objects = [cmds.ls(group, long=True)[0]]
        self.tm.run = self.tm.run.replace(animation_write_back=False)
        self.tm.snap_keys_to_frame()
        self.tm.tie_all_keyframes()
        self.assertNotEqual(
            cmds.keyframe(plug, query=True, timeChange=True),
            before,
            "the key tasks must have reached the shape's curve",
        )
        self.tm.run_deferred_restores()

        self.assertEqual(cmds.keyframe(plug, query=True, timeChange=True), before)

    def test_begin_run_resets_the_per_run_markers(self):
        """One hierarchy-checked export must not leak baseline writes into
        later runs -- begin_run (the ONE per-run reset) clears the marker.
        Assigning ``objects`` no longer does: tasks assign it mid-run (the
        carrier fold-in, a filter), and a reset there silently dropped a
        "Shots Only" choice made one task earlier (2026-09-13)."""
        self.tm._hierarchy_check_ran = True
        self.tm._clip_mode = "shots"
        self.tm.objects = [self.cube_long]
        self.assertTrue(self.tm._hierarchy_check_ran)
        self.assertEqual(self.tm._clip_mode, "shots")
        run = ptk.ExportRun(export_path="C:/out/asset.fbx", output_format="glb")
        self.tm.begin_run(run)
        self.assertFalse(self.tm._hierarchy_check_ran)
        self.assertEqual(self.tm._clip_mode, "both")
        self.assertIs(self.tm.run, run)
        self.assertEqual(self.tm.export_path, "C:/out/asset.fbx")
        self.assertTrue(self.tm.run.glb_only)

    def test_run_tasks_sets_optimize_keys_level_for_smart_bake(self):
        """run_tasks forwards the optimize_keys LEVEL to the run mode smart_bake
        reads for its internal override-layer optimization (the UI documents
        that coupling; blendertk uses the same idiom).

        The token is forwarded UNRESOLVED -- SmartBake resolves it against
        AnimUtils.OPTIMIZE_LEVELS -- so there is one table and no second
        translation to drift out of step with it. Derived from the FULL task
        dict by run_tasks, never by the dispatcher an override's resume hands
        a subset to (the level would come back False mid-run).
        """
        self.tm.objects = [self.cube_long]
        self.tm.run_tasks({"optimize_keys": "extremes"})
        self.assertEqual(self.tm.run.optimize_keys_level, "extremes")
        # A legacy bool still forwards as-is; SmartBake reads it as the default
        # level, exactly as it did when this was a checkbox.
        self.tm.run_tasks({"optimize_keys": True})
        self.assertIs(self.tm.run.optimize_keys_level, True)
        self.tm.run_tasks({"set_linear_unit": "cm"})
        self.assertFalse(self.tm.run.optimize_keys_level)
        # An override's resume dispatches a SUBSET directly: the level stands.
        self.tm.run_tasks({"optimize_keys": "extremes", "set_linear_unit": "cm"})
        self.tm._execute_tasks_and_checks({"set_linear_unit": "cm"}, {})
        self.assertEqual(self.tm.run.optimize_keys_level, "extremes")

    def test_resolve_invalid_texture_paths_keeps_valid_relative_paths(self):
        """A workspace-relative texture path that resolves must be left untouched.

        The old "already valid" guard was a bare os.path.exists, which
        resolves relative paths against the process CWD — a valid
        workspace-relative path failed the guard and was rewritten (via the
        basename hunt) on every run.  The path is set while the file does not
        exist yet: Maya's file node stores a non-resolving relative path
        verbatim, but auto-expands a resolving one to absolute at setAttr time
        (verified in mayapy) — the stored-relative shape is the production
        case (path authored under one workspace, exported under another).
        Must fail pre-fix.
        """
        rel_path = "sourceimages/state_hygiene_rel.png"
        shader = cmds.shadingNode("lambert", asShader=True)
        file_node = cmds.shadingNode("file", asTexture=True)
        cmds.connectAttr(f"{file_node}.outColor", f"{shader}.color")
        cmds.setAttr(f"{file_node}.fileTextureName", rel_path, type="string")

        ws = cmds.workspace(query=True, rootDirectory=True)
        src_dir = os.path.join(ws, "sourceimages")
        os.makedirs(src_dir, exist_ok=True)
        tex_abs = os.path.join(src_dir, "state_hygiene_rel.png")
        with open(tex_abs, "w") as f:
            f.write("dummy")
        self.addCleanup(os.remove, tex_abs)
        _assign_shader(self.cube, shader)

        self.tm.objects = [self.cube_long]
        # Guard against a vacuous pass: the task must actually see the node.
        self.assertIn(file_node, self.tm._get_export_file_nodes())
        self.tm.resolve_invalid_texture_paths()

        self.assertEqual(
            cmds.getAttr(f"{file_node}.fileTextureName"),
            rel_path,
            "valid workspace-relative path was rewritten",
        )


class TestMangledNameGuards(MayaTkTestCase):
    """check_mangled_names + conform_shape_names guard the export set against
    scratch/mangled node names (regression: PROPS_module.ma shipped shapes
    like 'prop____Shape702__uninst_tmp____Shape' in scene_data.json)."""

    def setUp(self):
        super().setUp()
        self.exporter = SceneExporter(log_level="DEBUG")
        self.cube = cmds.polyCube(name="GuardCube")[0]
        self.tm = self.exporter.task_manager
        self.tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

    def _mangle_shape(self, name):
        shape = cmds.listRelatives(self.cube, shapes=True, fullPath=True)[0]
        return cmds.rename(shape, name)

    def test_check_flags_uninst_scratch_name(self):
        self._mangle_shape("propShape1__uninst_tmpShape380")
        ok, messages = self.tm.check_mangled_names()
        self.assertFalse(ok)
        self.assertTrue(any("uninst" in m for m in messages))

    def test_check_flags_underscore_run(self):
        self._mangle_shape("prop____Shape702")
        ok, _ = self.tm.check_mangled_names()
        self.assertFalse(ok)

    def test_check_passes_clean_names(self):
        ok, messages = self.tm.check_mangled_names()
        self.assertTrue(ok, messages)

    def test_check_empty_export_set_passes(self):
        """No objects → pass, without falling back to the live selection."""
        self._mangle_shape("propShape1__uninst_tmpShape380")
        cmds.select(self.cube)  # a selection fallback would wrongly flag it
        self.tm.objects = []
        ok, messages = self.tm.check_mangled_names()
        self.assertTrue(ok, messages)

    def test_check_is_registered(self):
        self.assertIn("check_mangled_names", self.tm.check_definitions)

    def test_conform_task_repairs_shape(self):
        self._mangle_shape("prop____Shape702__uninst_tmp____Shape")
        self.tm.conform_shape_names()
        leaf = cmds.listRelatives(self.cube, shapes=True)[0].split("|")[-1]
        self.assertEqual(leaf, "GuardCubeShape")
        ok, messages = self.tm.check_mangled_names()
        self.assertTrue(ok, messages)

    def test_a_repaired_member_keeps_its_hold_shot_declared(self):
        """A hold shot keys nothing in its window, so only its members say it
        is live -- and the repair renamed them (``door___handle`` collapses to
        ``door_handle``): the shot named no object any more, was judged stale,
        and the export whose names were being repaired dropped its take. The
        shots follow the rename as the export set does."""
        from mayatk.anim_utils.shots._shots import ShotStore
        from mayatk.node_utils.data_nodes import DataNodes

        handle = cmds.polyCube(name="door___handle")[0]
        group = cmds.group(handle, name="DoorGroup")
        ShotStore.clear_active()
        self.addCleanup(ShotStore.clear_active)
        store = ShotStore()
        ShotStore.set_active(store)
        store.define_shot("Hold", 1, 20, objects=[cmds.ls(handle, long=True)[0]])
        self.tm.objects = [cmds.ls(group, long=True)[0]]

        self.tm.conform_shape_names()
        self.tm.export_data_node()
        self.tm.run_deferred_restores()

        self.assertEqual(store.shots[0].objects, ["|DoorGroup|door_handle"])
        meta = ptk.SceneRecords.SHOTS.load(DataNodes) or {}
        self.assertEqual([s["clip"] for s in meta.get("shots", [])], ["Hold"])

    def test_conform_task_is_registered(self):
        self.assertIn("conform_shape_names", self.tm.task_definitions)


class TestIgnoreGroupsCaseMode(MayaTkTestCase):
    """``ignore_groups`` match mode — the Ignore row's option-box "Aa" toggle.

    Insensitive is the default and the behavior the task shipped with, so the
    first test pins the contract every existing caller relies on. The dict form
    is how the panel arms the toggle: TaskFactory unpacks a dict value into the
    method's kwargs, so the payload key must stay ``names`` (blendertk's mirror
    was renamed from ``value`` for exactly this).
    """

    def setUp(self):
        super().setUp()
        self.exporter = SceneExporter(log_level="DEBUG")
        self.tm = self.exporter.task_manager

        self.temp_grp = cmds.group(em=True, name="TEMP")
        self.keep_grp = cmds.group(em=True, name="KEEP")
        self.ignored = self._cube("SCRATCH", self.temp_grp)
        self.kept = self._cube("HERO", self.keep_grp)

    @staticmethod
    def _cube(name, parent):
        cube = cmds.polyCube(name=name)[0]
        return cmds.ls(cmds.parent(cube, parent)[0], long=True)[0]

    def test_default_ignores_case(self):
        """A bare string keeps the case-insensitive default: 'temp' drops TEMP."""
        self.tm.objects = [self.ignored, self.kept]
        self.tm.ignore_groups("temp")
        self.assertEqual(self.tm.objects, [self.kept])

    def test_case_sensitive_requires_an_exact_match(self):
        self.tm.objects = [self.ignored, self.kept]
        self.tm.ignore_groups("temp", case_sensitive=True)
        self.assertEqual(self.tm.objects, [self.ignored, self.kept])

        self.tm.ignore_groups("TEMP", case_sensitive=True)
        self.assertEqual(self.tm.objects, [self.kept])

    def test_dict_payload_carries_the_mode_through_the_dispatcher(self):
        """The exact shape ``b000`` builds — TaskFactory must unpack it as kwargs.

        Guards the payload key: renaming the parameter would make the panel's
        dict raise TypeError at dispatch instead of silently ignoring the mode.
        """
        self.tm.objects = [self.ignored, self.kept]
        self.tm.run_tasks({"ignore_groups": {"names": "temp", "case_sensitive": True}})
        self.assertEqual(self.tm.objects, [self.ignored, self.kept])

        self.tm.run_tasks({"ignore_groups": {"names": "TEMP", "case_sensitive": True}})
        self.assertEqual(self.tm.objects, [self.kept])

    def test_a_bare_string_still_dispatches_at_the_insensitive_default(self):
        """The branch every pre-existing caller takes, pinned at the dispatcher.

        ``TaskFactory._execute_task_method`` chooses ``method(value)`` vs
        ``method(**value)`` from the method's POSITIONAL parameter COUNT — so
        adding ``case_sensitive`` is precisely the kind of change that could
        push a plain string onto the kwargs branch and raise instead of run.
        """
        self.tm.objects = [self.ignored, self.kept]
        self.tm.run_tasks({"ignore_groups": "temp"})
        self.assertEqual(self.tm.objects, [self.kept])

    def test_row_definition_is_a_line_edit_with_a_text_value(self):
        """The option box hangs off this row, so it has to stay a QLineEdit."""
        spec = self.tm.task_definitions["ignore_groups"]
        self.assertEqual(spec["widget_type"], "QLineEdit")
        self.assertEqual(spec["value_method"], "text")
        self.assertEqual(spec["panel"], "settings")


class TestIgnoreGroupsWildcards(MayaTkTestCase):
    """``ignore_groups`` patterns are shell-style globs, not exact names.

    The field shipped as exact set-membership, so "temp*" matched nothing and
    every ``temp_01``/``temp_02`` group had to be listed by hand. Matching now
    runs through ``ptk.filter_list``, which owns the glob, the comma split and
    the case fold for every filter field in the ecosystem.
    """

    def setUp(self):
        super().setUp()
        self.exporter = SceneExporter(log_level="DEBUG")
        self.tm = self.exporter.task_manager

        self.groups = {
            n: cmds.group(em=True, name=n)
            for n in ("temp_01", "temp_02", "hull_proxy", "HERO")
        }
        self.objs = {n: self._cube(f"{n}_geo", g) for n, g in self.groups.items()}
        self.tm.objects = list(self.objs.values())

    @staticmethod
    def _cube(name, parent):
        cube = cmds.polyCube(name=name)[0]
        return cmds.ls(cmds.parent(cube, parent)[0], long=True)[0]

    def test_trailing_star_matches_a_name_prefix(self):
        """The reported gap: 'temp*' must drop temp_01 and temp_02."""
        self.tm.ignore_groups("temp*")
        self.assertEqual(self.tm.objects, [self.objs["hull_proxy"], self.objs["HERO"]])

    def test_leading_star_matches_a_name_suffix(self):
        self.tm.ignore_groups("*_proxy")
        self.assertEqual(
            self.tm.objects,
            [self.objs["temp_01"], self.objs["temp_02"], self.objs["HERO"]],
        )

    def test_question_mark_matches_exactly_one_character(self):
        self.tm.ignore_groups("temp_0?")
        self.assertEqual(self.tm.objects, [self.objs["hull_proxy"], self.objs["HERO"]])

    def test_wildcards_compose_with_the_comma_split(self):
        self.tm.ignore_groups("temp*, *_proxy")
        self.assertEqual(self.tm.objects, [self.objs["HERO"]])

    def test_a_pattern_without_a_wildcard_still_matches_only_that_name(self):
        """The contract every pre-existing caller relies on: 'temp' is not 'temp*'."""
        self.tm.ignore_groups("temp")
        self.assertEqual(self.tm.objects, list(self.objs.values()))

    def test_wildcards_honor_the_case_toggle(self):
        self.tm.ignore_groups("TEMP*", case_sensitive=True)
        self.assertEqual(self.tm.objects, list(self.objs.values()))

        self.tm.ignore_groups("TEMP*")
        self.assertEqual(self.tm.objects, [self.objs["hull_proxy"], self.objs["HERO"]])

    def test_an_all_separator_field_excludes_nothing(self):
        """The footgun the early return guards.

        ``filter_list`` with an empty pattern list is a no-op that returns the
        list unfiltered — which on this code path would mean every root
        "matched" and the whole scene dropped out of the export.
        """
        self.tm.ignore_groups(" , ,  ")
        self.assertEqual(self.tm.objects, list(self.objs.values()))


class TestExportSetStalePaths(MayaTkTestCase):
    """A renamed export node must not leave a stale DAG path in the task set.

    Regression: conform_shape_names ("Fix Mangled Names") renames transforms
    but left TaskManager.objects holding the pre-rename long names.  The first
    check that hands the whole list to cmds (alphabetically
    check_duplicate_locator_names) then died with
    ``ValueError: No object matches name: [<every export object>]`` — naming
    every object except the offender — and aborted the export.
    """

    def setUp(self):
        super().setUp()
        self.exporter = SceneExporter(log_level="DEBUG")
        self.tm = self.exporter.task_manager

        self.static = cmds.group(em=True, name="STATIC")
        self.clean = self._cube("FLOOR", self.static)
        # FBXASC032 is an escaped space: repair_mangled_names rewrites this to
        # "DIRT_STAIN_03", which already exists, so Maya uniquifies the name —
        # either way the stored path goes stale.
        self.taken = self._cube("DIRT_STAIN_03", self.static)
        self.mangled = self._cube("DIRT_STAIN_FBXASC03203", self.static)
        self.tm.objects = [self.clean, self.taken, self.mangled]

    @staticmethod
    def _cube(name, parent):
        cube = cmds.polyCube(name=name)[0]
        return cmds.ls(cmds.parent(cube, parent)[0], long=True)[0]

    def test_conform_refreshes_objects_to_the_new_paths(self):
        self.tm.conform_shape_names()
        self.assertNotIn(self.mangled, self.tm.objects)
        # Renamed, not dropped — the node must still ship.
        self.assertEqual(len(self.tm.objects), 3)
        # Every stored path still resolves.
        self.assertEqual(len(cmds.ls(self.tm.objects, long=True)), 3)
        # Order is preserved (UUID snapshot order, not scene order).
        self.assertEqual(self.tm.objects[0], self.clean)
        self.assertEqual(self.tm.objects[1], self.taken)

    def test_deleted_object_drops_out_of_the_refresh(self):
        """A node a task removed must not linger — nor be resurrected.

        Deleting DIRT_STAIN_03 frees the name, so the mangled node cleans
        straight onto its path: an existence test would keep the dead entry
        and hand back the SAME path twice.
        """
        deleted_uuid = cmds.ls(self.taken, uuid=True)[0]
        cmds.delete(self.taken)
        self.tm.conform_shape_names()

        refreshed = list(self.tm.objects)
        self.assertEqual(len(refreshed), 2, refreshed)
        self.assertEqual(len(set(refreshed)), 2, refreshed)
        self.assertEqual(cmds.ls(deleted_uuid, long=True), [])
        # The survivor that moved onto the freed name is the mangled one.
        self.assertEqual(len(cmds.ls(refreshed, long=True)), 2)

    def test_a_conformed_SHAPE_path_is_refreshed_too(self):
        """Conforming a shape renames it just as surely as repairing a transform.

        Reported from a production run: a control's shape named
        ``..._settings_CTRL_xzShape`` was conformed here, and eleven tasks
        later ``smart_bake`` walked the export set node by node and died on the
        stale path — ``RuntimeError: No object ... matches name`` — aborting the
        export after two minutes of texture work. The refresh existed but was
        armed only when a TRANSFORM had been renamed.
        """
        ctrl = cmds.group(em=True, name="settings_CTRL")
        curve = cmds.circle(name="settings_CTRL_xz", constructionHistory=False)[0]
        shape = cmds.listRelatives(curve, shapes=True, fullPath=True)[0]
        cmds.parent(shape, ctrl, shape=True, relative=True)
        cmds.delete(curve)
        shape = cmds.listRelatives(ctrl, shapes=True, fullPath=True)[0]
        self.tm.objects = [self.clean, shape]

        self.tm.conform_shape_names()

        self.assertEqual(
            len(cmds.ls(self.tm.objects, long=True)),
            2,
            f"a stored path no longer resolves: {self.tm.objects}",
        )

    def test_smart_bake_survives_a_path_an_earlier_task_invalidated(self):
        """The task that walks every node one at a time must not be the one that dies.

        Any task can invalidate a path (a rename, a delete); ``smart_bake``
        took the raw set while every other bulk consumer goes through
        ``_live_objects``.
        """
        doomed = self._cube("SMART_BAKE_DOOMED", self.static)
        self.tm.objects = [self.clean, doomed]
        cmds.delete(doomed)  # whatever an earlier task did to it

        self.tm.smart_bake()  # must not raise

    def test_locator_check_survives_conform(self):
        self.tm.conform_shape_names()
        ok, messages = self.tm.check_duplicate_names("locators")
        self.assertTrue(ok, messages)

    def test_export_selection_survives_conform(self):
        self.tm.conform_shape_names()
        cmds.select(self.tm.objects, replace=True)
        self.assertEqual(len(cmds.ls(selection=True)), 3)

    def test_texture_advisories_fold_to_one_line_per_warning(self):
        """Live report (2026-09-13): a 49-map set over a 2K budget logged the
        same sentence 49 times. One line per advisory now, naming the count
        and the first few maps."""
        from unittest.mock import patch

        self.exporter.logger.setLevel(logging.INFO)
        budget = "Over delivery budget: 4096x4096 exceeds the profile's advisory max_size of 2048"
        sources = {
            f"k{i}": {"path": f"/maps/ITA_{i:02d}.png", "nodes": [], "tiled": False}
            for i in range(12)
        }
        verdict = {"needed": False, "reasons": [], "warnings": [budget]}
        with (
            patch.object(self.tm, "_export_texture_sources", return_value=sources),
            patch.object(self.tm, "_assess_optimization", return_value=verdict),
            self.assertLogs(self.tm.logger, level="INFO") as cm,
        ):
            ok, messages = self.tm.check_texture_optimization("unity")
        self.assertTrue(ok, messages)
        text = "\n".join(cm.output)
        self.assertEqual(text.count(budget), 1, text)
        self.assertIn("12 texture(s): ITA_00.png", text)
        self.assertIn("(+4 more)", text)
        self.assertIn("Texture optimization notes (1)", text)

    def test_reporting_branches_render_at_info(self):
        """The grouped-report branches of ignore_groups / the LOD check must run.

        Both emit through ``logger.log_group``, gated on ``isEnabledFor(INFO)``
        — and both branches were previously unreachable from any test:
        ``ignore_groups`` was never called at all, and
        ``check_geometry_lod_suffix`` was only ever called on objects with no
        LOD suffix, so its ``if matches:`` block never ran. A gated report is
        dead code under a suite that never enables the level or the branch,
        which is how a bad attribute reference ships unnoticed. Drive both
        with the data that reaches the report.
        """
        self.exporter.logger.setLevel(logging.INFO)
        self.assertTrue(self.exporter.logger.isEnabledFor(logging.INFO))

        lod = self._cube("PROP_LOD0", self.static)
        self.tm.objects = [self.clean, lod]
        ok, messages = self.tm.check_geometry_lod_suffix()
        self.assertTrue(ok)
        self.assertTrue(
            any("PROP_LOD0" in m for m in messages),
            f"LOD match missing from messages: {messages}",
        )

        # ignore_groups: STATIC is the top-level parent of every fixture cube,
        # so naming it empties the export list.
        self.tm.ignore_groups("static")
        self.assertEqual(self.tm.objects, [])

        # verify_fbx_preset's settings report is gated the same way, and runs
        # on every preset-driven export (load_fbx_export_preset(..., verify=True)).
        cmds.loadPlugin("fbxmaya", quiet=True)
        settings = self.exporter.verify_fbx_preset()
        self.assertTrue(settings, "verify_fbx_preset returned no settings")

    def test_checks_tolerate_a_stale_path(self):
        """Read-side guard: a path that vanished must not abort the run."""
        self.tm.objects = [self.clean, "|STATIC|DELETED_BY_A_TASK"]
        for name, check in (
            (
                "check_duplicate_names",
                lambda: self.tm.check_duplicate_names("locators"),
            ),
            ("check_mangled_names", self.tm.check_mangled_names),
            ("check_geometry_lod_suffix", self.tm.check_geometry_lod_suffix),
            ("check_hidden_geometry", self.tm.check_hidden_geometry),
        ):
            with self.subTest(check=name):
                ok, messages = check()
                self.assertTrue(ok, messages)

    def test_locator_check_still_flags_duplicates(self):
        loc_a = cmds.ls(cmds.spaceLocator(name="SNAP")[0], long=True)[0]
        grp = cmds.group(em=True, name="NESTED", parent=self.static)
        # Same short name is only legal under a different parent.
        loc_b = cmds.parent(cmds.spaceLocator(name="SNAP_TMP")[0], grp)[0]
        cmds.rename(loc_b, "SNAP")
        loc_b = "|STATIC|NESTED|SNAP"
        self.tm.objects = [loc_a, loc_b]
        ok, messages = self.tm.check_duplicate_names("locators")
        self.assertFalse(ok)
        self.assertTrue(any("SNAP" in m for m in messages), messages)
        # Both colliding paths are reported, not just the first one seen.
        self.assertTrue(any("NESTED" in m for m in messages), messages)


class TestDuplicateNameScope(MayaTkTestCase):
    """The Duplicate Names dial: one check, four widths.

    Added 2026-08-29 — the check was locator-only, so a duplicate bone name
    (which breaks skinning and retargeting on import) or a duplicate mesh name
    (which the receiving engine silently renames) shipped unreported. Each
    tier must catch what the narrower one cannot, and catch nothing more.
    """

    def setUp(self):
        super().setUp()
        self.exporter = SceneExporter(log_level="DEBUG")
        self.tm = self.exporter.task_manager
        self.a = cmds.group(em=True, name="A")
        self.b = cmds.group(em=True, name="B")

    def _twin(self, make, name):
        """*make* a node called *name* under A and another under B.

        Same short name under different parents — legal in Maya, a collision
        in the FBX, which is exactly what the check exists to report.
        """
        cmds.select(clear=True)
        cmds.parent(make(name), self.a)
        cmds.select(clear=True)
        cmds.rename(cmds.parent(make(f"{name}_TMP"), self.b)[0], name)
        return [
            cmds.ls(f"|A|{name}", long=True)[0],
            cmds.ls(f"|B|{name}", long=True)[0],
        ]

    def test_locators_tier_is_the_old_behavior(self):
        objs = self._twin(lambda n: cmds.spaceLocator(name=n)[0], "SNAP")
        self.tm.objects = objs
        ok, messages = self.tm.check_duplicate_names("locators")
        self.assertFalse(ok)
        # Both colliding paths are named, so the row says WHICH pair collided.
        self.assertTrue(any("%7CA%7CSNAP" in m for m in messages), messages)
        self.assertTrue(any("%7CB%7CSNAP" in m for m in messages), messages)
        # The pre-dial key (``check_duplicate_locator_names``) shipped its
        # one-release window 13 times over and was retired 2026-09-21.
        self.assertFalse(hasattr(self.tm, "check_duplicate_locator_names"))

    def test_joint_collision_needs_the_joints_tier(self):
        self.tm.objects = self._twin(lambda n: cmds.joint(name=n), "SPINE")
        self.assertTrue(self.tm.check_duplicate_names("locators")[0])
        ok, messages = self.tm.check_duplicate_names("joints")
        self.assertFalse(ok)
        self.assertTrue(any("SPINE" in m for m in messages), messages)

    def test_connected_tier_catches_a_constrained_pair(self):
        objs = self._twin(lambda n: cmds.polyCube(name=n)[0], "PROP")
        driver = cmds.spaceLocator(name="DRIVER")[0]
        for obj in objs:
            cmds.pointConstraint(driver, obj, maintainOffset=True)
        self.tm.objects = objs

        self.assertTrue(self.tm.check_duplicate_names("locators")[0])
        self.assertTrue(self.tm.check_duplicate_names("joints")[0])
        ok, messages = self.tm.check_duplicate_names("connected")
        self.assertFalse(ok)
        self.assertTrue(any("PROP" in m for m in messages), messages)

    def test_all_tier_is_the_only_one_that_flags_inert_geometry(self):
        objs = self._twin(lambda n: cmds.polyCube(name=n)[0], "CRATE")
        # The 'all' EXPORT scope lists geometry as well as transforms, and a
        # transform rename leaves the shape's own name behind — so give both
        # shapes the colliding name the export set would really carry.
        shapes = [
            cmds.ls(
                cmds.rename(
                    cmds.listRelatives(obj, shapes=True, fullPath=True)[0],
                    "CRATEShape",
                ),
                long=True,
            )[0]
            for obj in objs
        ]
        self.tm.objects = objs + shapes
        for scope in ("locators", "joints", "connected"):
            with self.subTest(scope=scope):
                self.assertTrue(self.tm.check_duplicate_names(scope)[0])
        ok, messages = self.tm.check_duplicate_names("all")
        self.assertFalse(ok)
        self.assertTrue(any("CRATE" in m for m in messages), messages)
        # One row, not two: the shapes ride the same export set and collide
        # identically, but CRATEShape is not a name the consumer resolves.
        self.assertNotIn("Shape", "".join(messages))
        self.assertTrue(any("1 duplicate short name" in m for m in messages), messages)

    def test_off_short_circuits_before_touching_the_scene(self):
        self.tm.objects = self._twin(lambda n: cmds.spaceLocator(name=n)[0], "SNAP")
        for scope in (None, False, "", "OFF"):
            with self.subTest(scope=scope):
                self.assertEqual(self.tm.check_duplicate_names(scope), (True, []))

    def test_an_unknown_scope_fails_loudly_instead_of_narrowing(self):
        """The resolver's widest branch is its fallthrough, so a typo'd scope
        would otherwise scan a NARROWER tier than asked for and pass on it."""
        self.tm.objects = self._twin(lambda n: cmds.polyCube(name=n)[0], "CRATE")
        # "al" would have fallen through to the connected tier — which these
        # inert cubes are not in — and reported a clean export.
        ok, messages = self.tm.check_duplicate_names("al")
        self.assertFalse(ok)
        self.assertTrue(any("Unknown duplicate-name scope" in m for m in messages))
        self.assertTrue(any("all" in m for m in messages), messages)

    def test_only_ambiguous_names_reach_the_per_node_probe(self):
        """The connected tier costs 2 cmds calls per node it looks at, so the
        pool is narrowed to names that could possibly collide — a node nobody
        shares a name with can never be half of a collision."""
        objs = self._twin(lambda n: cmds.polyCube(name=n)[0], "PROP")
        unique = [
            cmds.ls(cmds.polyCube(name=f"UNIQUE_{i}")[0], long=True)[0]
            for i in range(3)
        ]
        self.tm.objects = objs + unique
        self.assertEqual(self.tm._ambiguous_leaf_names(self.tm.objects), objs)

        probed = []
        real = self.tm._connected_transforms

        def _spy(nodes):
            probed.extend(nodes)
            return real(nodes)

        self.tm._connected_transforms = _spy
        try:
            self.tm.check_duplicate_names("connected")
        finally:
            del self.tm._connected_transforms
        self.assertEqual(probed, objs, probed)

    def test_definition_is_a_scope_combo_that_opens_on_locators(self):
        defs = self.tm.check_definitions
        self.assertNotIn("check_duplicate_locator_names", defs)
        spec = defs["check_duplicate_names"]
        self.assertEqual(spec["widget_type"], "ComboBox")
        self.assertEqual(spec["set_row_label"], "Duplicate Names")
        self.assertEqual(
            list(spec["add"]),
            [
                "OFF",
                "Locators",
                "Locators & Joints",
                "Connected & Animated",
                "All Export Objects",
            ],
        )
        # OFF is falsy, so b000's filter drops the row before dispatch.
        self.assertIsNone(spec["add"]["OFF"])
        # Every non-OFF option has to be a scope the check resolves.
        for token in spec["add"].values():
            if token:
                with self.subTest(token=token):
                    self.assertIsInstance(self.tm._duplicate_name_scope(token), list)
        # ``add`` lands the combo on index 0 (OFF), so the default index must
        # be applied AFTER it — set_attributes walks the dict in order.
        keys = list(spec)
        self.assertLess(keys.index("add"), keys.index("setCurrentIndex"))
        self.assertEqual(list(spec["add"])[spec["setCurrentIndex"]], "Locators")


class TestTexturePathPipeline(MayaTkTestCase):
    """stage_textures_relative + reworked path/geometry/anim checks.

    Added: 2026-08-01 (scene-exporter robustness audit, implementation pass).
    """

    def setUp(self):
        super().setUp()
        self.exporter = SceneExporter(log_level="DEBUG")
        self.tm = self.exporter.task_manager
        self.temp_dir = tempfile.mkdtemp()
        # Registered FIRST so it runs LAST: the per-test file removals and the
        # workspace restore below are cleanups too, and unittest runs them
        # after tearDown -- an rmtree there pulled the project out from under
        # them (8 FileNotFoundError cleanups, measured).
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)
        self.cube = cmds.polyCube(name="PipelineCube")[0]
        self.cube_long = cmds.ls(self.cube, long=True)[0]
        # A project of this test's own. The live workspace is whatever the
        # user last opened -- on the maintainer's box a synced production
        # folder -- and two suites sharing it collide on the probe files
        # (PermissionError, 2026-09-05, a GUI check beside the full run).
        project = os.path.join(self.temp_dir, "project")
        self.ws_src = os.path.join(project, "sourceimages")
        os.makedirs(self.ws_src, exist_ok=True)
        original_ws = cmds.workspace(query=True, rootDirectory=True)
        self.addCleanup(lambda: cmds.workspace(original_ws, openWorkspace=True))
        cmds.workspace(project, openWorkspace=True)

    def _textured_shader(self, tex_path, name="pipeMat"):
        from mayatk.mat_utils._mat_utils import MatUtils  # noqa: F401

        shader = cmds.shadingNode("lambert", asShader=True, name=name)
        file_node = cmds.shadingNode("file", asTexture=True, name=f"{name}_file")
        cmds.connectAttr(f"{file_node}.outColor", f"{shader}.color")
        cmds.setAttr(f"{file_node}.fileTextureName", tex_path, type="string")
        _assign_shader(self.cube, shader)
        return shader, file_node

    # -- stage_textures_relative ---------------------------------------

    def test_stage_external_texture_copies_and_stores_relative(self):
        """External absolute path → copied into sourceimages, node stores a
        genuinely RELATIVE path (om-write past Maya's setAttr auto-expand)."""
        from mayatk.mat_utils._mat_utils import MatUtils

        tex = os.path.join(self.temp_dir, "pipe_ext.png").replace("\\", "/")
        with open(tex, "w") as f:
            f.write("external payload")
        staged = os.path.join(self.ws_src, "pipe_ext.png")
        self.addCleanup(lambda: os.path.exists(staged) and os.remove(staged))

        _, file_node = self._textured_shader(tex)
        results = MatUtils.stage_textures_relative([file_node])

        self.assertEqual(results[file_node], "copied+relativized")
        self.assertEqual(
            cmds.getAttr(f"{file_node}.fileTextureName"), "sourceimages/pipe_ext.png"
        )
        self.assertTrue(os.path.isfile(staged))

    def test_stage_name_collision_stages_a_variant(self):
        """A DIFFERENT same-named file in sourceimages must stage the node's own
        texture under a disambiguated name — never rebind it to the wrong file,
        and never abandon it on an absolute path.

        Skipping the node used to leave a cross-project absolute path in the
        scene AND in the export (field report: 'file3' → another project's
        sourceimages/ibl_brdf_lut.png).  Added: 2026-08-12
        """
        from mayatk.mat_utils._mat_utils import MatUtils

        staged = os.path.join(self.ws_src, "pipe_coll.png")
        with open(staged, "w") as f:
            f.write("resident content")
        self.addCleanup(os.remove, staged)
        variant = os.path.join(self.ws_src, "pipe_coll_1.png")
        self.addCleanup(lambda: os.path.exists(variant) and os.remove(variant))
        tex = os.path.join(self.temp_dir, "pipe_coll.png").replace("\\", "/")
        with open(tex, "w") as f:
            f.write("completely different external content")

        _, file_node = self._textured_shader(tex, name="pipeMatColl")
        results = MatUtils.stage_textures_relative([file_node])

        self.assertEqual(results[file_node], "variant+relativized")
        self.assertEqual(
            cmds.getAttr(f"{file_node}.fileTextureName"), "sourceimages/pipe_coll_1.png"
        )
        # The resident file keeps its own content ...
        with open(staged) as f:
            self.assertEqual(f.read(), "resident content")
        # ... and the node resolves to ITS texture, not the resident one.
        # Compared by stat, not by reading: a read issued microseconds after
        # shutil.copy2 intermittently hits a Windows sharing violation
        # (PermissionError) while the scanner still holds the new file.
        self.assertTrue(os.path.isfile(variant))
        self.assertEqual(os.path.getsize(variant), os.path.getsize(tex))
        self.assertNotEqual(os.path.getsize(variant), os.path.getsize(staged))

    def test_stage_variant_is_reused_not_multiplied(self):
        """A second node with the same colliding content reuses the existing
        variant instead of stacking _1, _2, _3 …

        Without this the LUT-style collision re-stages on every export.
        Added: 2026-08-12
        """
        from mayatk.mat_utils._mat_utils import MatUtils

        staged = os.path.join(self.ws_src, "pipe_reuse.png")
        with open(staged, "w") as f:
            f.write("resident content")
        self.addCleanup(os.remove, staged)
        variant = os.path.join(self.ws_src, "pipe_reuse_1.png")
        self.addCleanup(lambda: os.path.exists(variant) and os.remove(variant))

        made = []
        for i in (1, 2):
            sub = os.path.join(self.temp_dir, f"src{i}")
            os.makedirs(sub, exist_ok=True)
            tex = os.path.join(sub, "pipe_reuse.png").replace("\\", "/")
            with open(tex, "w") as f:
                f.write("identical foreign content")
            made.append(self._textured_shader(tex, name=f"pipeMatReuse{i}")[1])

        results = MatUtils.stage_textures_relative(made)

        for node in made:
            self.assertEqual(results[node], "variant+relativized")
            self.assertEqual(
                cmds.getAttr(f"{node}.fileTextureName"),
                "sourceimages/pipe_reuse_1.png",
            )
        self.assertFalse(
            os.path.exists(os.path.join(self.ws_src, "pipe_reuse_2.png")),
            "identical content must not stack a second variant",
        )

    def test_stage_within_batch_collision_gets_distinct_variants(self):
        """Two nodes whose externals share a basename but not their content
        each get their own staged file — the second must not silently land on
        the first's destination.  Added: 2026-08-12
        """
        from mayatk.mat_utils._mat_utils import MatUtils

        for name in ("pipe_batch.png", "pipe_batch_1.png"):
            path = os.path.join(self.ws_src, name)
            self.addCleanup(lambda p=path: os.path.exists(p) and os.remove(p))

        nodes, payloads = [], ("short", "a considerably longer payload")
        for i, payload in enumerate(payloads):
            sub = os.path.join(self.temp_dir, f"batch{i}")
            os.makedirs(sub, exist_ok=True)
            tex = os.path.join(sub, "pipe_batch.png").replace("\\", "/")
            with open(tex, "w") as f:
                f.write(payload)
            nodes.append(self._textured_shader(tex, name=f"pipeMatBatch{i}")[1])

        results = MatUtils.stage_textures_relative(nodes)

        self.assertEqual(results[nodes[0]], "copied+relativized")
        self.assertEqual(results[nodes[1]], "variant+relativized")
        stored = [cmds.getAttr(f"{n}.fileTextureName") for n in nodes]
        self.assertEqual(
            stored, ["sourceimages/pipe_batch.png", "sourceimages/pipe_batch_1.png"]
        )
        for name, payload in zip(("pipe_batch.png", "pipe_batch_1.png"), payloads):
            self.assertEqual(
                os.path.getsize(os.path.join(self.ws_src, name)), len(payload)
            )

    def test_variant_index_goes_on_the_base_name_not_after_the_map_type(self):
        """A staged variant must still classify as the map type it is.

        ``rock_Base_Color_1.png`` ends in ``_1``, not in any registry alias, so
        every consumer of the taxonomy reads it as "not a texture map" and the
        shader builder silently leaves it unwired. Putting the index on the
        base name keeps the type token trailing.  Added: 2026-08-18
        """

        from mayatk.mat_utils._mat_utils import MatUtils

        resident = os.path.join(self.ws_src, "pipe_Base_Color.png")
        with open(resident, "w") as f:
            f.write("resident content")
        self.addCleanup(os.remove, resident)
        variant = os.path.join(self.ws_src, "pipe_1_Base_Color.png")
        self.addCleanup(lambda: os.path.exists(variant) and os.remove(variant))
        tex = os.path.join(self.temp_dir, "pipe_Base_Color.png").replace("\\", "/")
        with open(tex, "w") as f:
            f.write("a different image entirely")

        _, file_node = self._textured_shader(tex, name="pipeMatTyped")
        results = MatUtils.stage_textures_relative([file_node])

        self.assertEqual(results[file_node], "variant+relativized")
        stored = cmds.getAttr(f"{file_node}.fileTextureName")
        self.assertEqual(stored, "sourceimages/pipe_1_Base_Color.png")
        self.assertTrue(os.path.isfile(variant))
        # The whole reason for the placement:
        self.assertEqual(
            ptk.MapFactory.resolve_map_type(stored),
            "Base_Color",
            "a staged variant must still resolve to its map type",
        )

    def test_variant_warning_names_a_resident_nothing_reads(self):
        """The ``_N`` says WHY it happened and how to get the clean name back.

        A resident file no file node reads is almost always the previous export
        of the same texture; without naming it, the variant is silent and the
        stale copies pile up unnoticed. Reported only — this scene not reading
        a file is no proof another scene doesn't, so it is never overwritten.
        Added: 2026-08-18
        """
        from mayatk.mat_utils._mat_utils import MatUtils

        resident = os.path.join(self.ws_src, "pipe_stale.png")
        with open(resident, "w") as f:
            f.write("last export's content")
        self.addCleanup(os.remove, resident)
        variant = os.path.join(self.ws_src, "pipe_stale_1.png")
        self.addCleanup(lambda: os.path.exists(variant) and os.remove(variant))
        tex = os.path.join(self.temp_dir, "pipe_stale.png").replace("\\", "/")
        with open(tex, "w") as f:
            f.write("this export's content")

        _, file_node = self._textured_shader(tex, name="pipeMatStale")
        warnings = []
        with patch.object(cmds, "warning", side_effect=warnings.append):
            results = MatUtils.stage_textures_relative([file_node])

        self.assertEqual(results[file_node], "variant+relativized")
        joined = " ".join(warnings)
        self.assertIn("pipe_stale.png", joined)
        self.assertIn("NOTHING in this scene reads", joined)
        # Reported, never acted on.
        with open(resident) as f:
            self.assertEqual(f.read(), "last export's content")

    def test_variant_warning_says_in_use_when_a_node_reads_the_resident(self):
        """A resident another node reads is a real clash, not stale residue.

        Added: 2026-08-18
        """
        from mayatk.mat_utils._mat_utils import MatUtils

        resident = os.path.join(self.ws_src, "pipe_live.png")
        with open(resident, "w") as f:
            f.write("in-use content")
        self.addCleanup(os.remove, resident)
        variant = os.path.join(self.ws_src, "pipe_live_1.png")
        self.addCleanup(lambda: os.path.exists(variant) and os.remove(variant))

        # A second material genuinely reads the resident file.
        self._textured_shader(resident.replace("\\", "/"), name="pipeMatHolder")

        tex = os.path.join(self.temp_dir, "pipe_live.png").replace("\\", "/")
        with open(tex, "w") as f:
            f.write("a different image entirely")
        _, file_node = self._textured_shader(tex, name="pipeMatLive")

        warnings = []
        with patch.object(cmds, "warning", side_effect=warnings.append):
            results = MatUtils.stage_textures_relative([file_node])

        self.assertEqual(results[file_node], "variant+relativized")
        joined = " ".join(warnings)
        self.assertIn("still in use", joined)
        self.assertNotIn("NOTHING in this scene reads", joined)

    def test_stage_unverifiable_content_never_reuses_the_resident_file(self):
        """When neither file yields a content id (locked, or a cloud placeholder
        that won't hydrate), two unknowns must NOT count as a match.

        Reusing the resident file on a failed pair of reads is the wrong-texture
        rebind the collision guard exists to prevent.  Added: 2026-08-12
        """
        from mayatk.mat_utils._mat_utils import MatUtils

        resident = os.path.join(self.ws_src, "pipe_unver.png")
        with open(resident, "w") as f:
            f.write("resident content")
        self.addCleanup(os.remove, resident)
        variant = os.path.join(self.ws_src, "pipe_unver_1.png")
        self.addCleanup(lambda: os.path.exists(variant) and os.remove(variant))
        tex = os.path.join(self.temp_dir, "pipe_unver.png").replace("\\", "/")
        with open(tex, "w") as f:
            f.write("foreign content")

        _, file_node = self._textured_shader(tex, name="pipeMatUnver")
        with patch.object(MatUtils, "_texture_content_id", return_value=None):
            results = MatUtils.stage_textures_relative([file_node])

        self.assertEqual(results[file_node], "variant+relativized")
        self.assertEqual(
            cmds.getAttr(f"{file_node}.fileTextureName"),
            "sourceimages/pipe_unver_1.png",
        )
        self.assertEqual(os.path.getsize(variant), len("foreign content"))

    def test_stage_udim_collision_suffixes_every_tile(self):
        """A colliding UDIM set stages ALL tiles under one consistent variant
        name, and the stored token path matches the tiles on disk.

        The index sits on the BASE name, leaving the tile token where it
        belongs (``pipe_tile_1.<UDIM>.png``, not ``pipe_tile.<UDIM>_1.png``):
        a suffix appended last would hide a map-type token from the resolver,
        and the rule is applied to every staged name so there is one form to
        reason about.  Added: 2026-08-12, retargeted 2026-08-18
        """
        from mayatk.mat_utils._mat_utils import MatUtils

        resident = os.path.join(self.ws_src, "pipe_tile.1001.png")
        with open(resident, "w") as f:
            f.write("resident tile")
        self.addCleanup(os.remove, resident)
        for tile in ("1001", "1002"):
            landed = os.path.join(self.ws_src, f"pipe_tile_1.{tile}.png")
            self.addCleanup(lambda p=landed: os.path.exists(p) and os.remove(p))

        sub = os.path.join(self.temp_dir, "udim")
        os.makedirs(sub, exist_ok=True)
        for tile in ("1001", "1002"):
            with open(os.path.join(sub, f"pipe_tile.{tile}.png"), "w") as f:
                f.write(f"foreign tile {tile}")

        token_path = os.path.join(sub, "pipe_tile.<UDIM>.png").replace("\\", "/")
        _, file_node = self._textured_shader(token_path, name="pipeMatUdim")
        cmds.setAttr(f"{file_node}.uvTilingMode", 3)

        results = MatUtils.stage_textures_relative([file_node])

        self.assertEqual(results[file_node], "variant+relativized")
        self.assertEqual(
            cmds.getAttr(f"{file_node}.fileTextureName"),
            "sourceimages/pipe_tile_1.<UDIM>.png",
        )
        # Stat, not read — see the sharing-violation note in the test above.
        for tile in ("1001", "1002"):
            landed = os.path.join(self.ws_src, f"pipe_tile_1.{tile}.png")
            self.assertTrue(os.path.isfile(landed), f"tile {tile} not staged")
            self.assertEqual(
                os.path.getsize(landed),
                os.path.getsize(os.path.join(sub, f"pipe_tile.{tile}.png")),
            )
        with open(resident) as f:
            self.assertEqual(f.read(), "resident tile")

    def test_stage_preserves_sourceimages_subfolder(self):
        """Absolute path into sourceimages/sub → relativized IN PLACE with the
        subfolder kept (the old remap flattened it to the root)."""
        from mayatk.mat_utils._mat_utils import MatUtils

        sub = os.path.join(self.ws_src, "pipesub")
        os.makedirs(sub, exist_ok=True)
        staged = os.path.join(sub, "pipe_sub.png").replace("\\", "/")
        with open(staged, "w") as f:
            f.write("sub payload")
        self.addCleanup(shutil.rmtree, sub)

        _, file_node = self._textured_shader(staged, name="pipeMatSub")
        results = MatUtils.stage_textures_relative([file_node])

        self.assertEqual(results[file_node], "relativized")
        self.assertEqual(
            cmds.getAttr(f"{file_node}.fileTextureName"),
            "sourceimages/pipesub/pipe_sub.png",
        )

    # -- check_path_length ----------------------------------------------

    def test_check_material_compatibility_is_keyed_by_the_template(self):
        """A mask is judged against the CHOSEN template, not a hardcoded ORM.

        The check is the validation half of the Texture Template combobox: a
        residual MSAO fails a glTF template, an ORM passes it -- and the same
        MSAO passes an HDRP template, where it is the native packing. The
        verdict itself is pythontk's (``MeshConvert.sidecar_foreign_packings``
        keyed by workflow); patched at the scene read so the test pins THIS
        layer -- the keying, the pass-through default, and the message naming
        the offending file.
        """
        from mayatk.env_utils import scene_state

        def _sections(mask_path):
            return {"metallic_roughness": {"MAT_probe": {"metallic": mask_path}}}

        with patch.object(
            scene_state.SceneState,
            "read",
            return_value=_sections("C:/tex/probe_MSAO.png"),
        ):
            status, msgs = self.tm.check_material_compatibility("glTF 2.0")
            self.assertFalse(status, "a residual MSAO must fail a glTF template")
            self.assertTrue(any("MSAO" in m for m in msgs), msgs)
            self.assertTrue(any("probe_MSAO.png" in m for m in msgs), msgs)

            status, msgs = self.tm.check_material_compatibility("Unity HDRP")
            self.assertTrue(
                status, f"MSAO is NATIVE to an HDRP template, must pass: {msgs}"
            )

        with patch.object(
            scene_state.SceneState,
            "read",
            return_value=_sections("C:/tex/probe_ORM.png"),
        ):
            status, msgs = self.tm.check_material_compatibility("glTF 2.0")
        self.assertTrue(status, f"an ORM mask must pass a glTF template: {msgs}")

        # A loose, ordinary source set must never trip it -- an AO or emissive
        # map declares no packing workflow and is not a foreign PACKING.
        with patch.object(
            scene_state.SceneState,
            "read",
            return_value={
                "metallic_roughness": {
                    "MAT_probe": {
                        "metallic": "C:/tex/probe_Metallic.png",
                        "roughness": "C:/tex/probe_Roughness.png",
                        "occlusion": "C:/tex/probe_AO.png",
                    }
                },
                "emissive": {"MAT_probe": {"texture": "C:/tex/probe_Emissive.png"}},
            },
        ):
            status, msgs = self.tm.check_material_compatibility("glTF 2.0")
        self.assertTrue(status, f"a loose source set must pass the gate: {msgs}")

    def test_check_material_compatibility_disarmed_without_a_template(self):
        """'As Authored' (falsy template) passes without even reading the scene
        -- the combobox is the one definition, and unset means neither hook."""
        from mayatk.env_utils import scene_state

        with patch.object(
            scene_state.SceneState, "read", side_effect=AssertionError("must not read")
        ):
            self.assertEqual(self.tm.check_material_compatibility(None), (True, []))
            self.assertEqual(self.tm.check_material_compatibility(""), (True, []))

    def test_check_material_compatibility_survives_a_scene_read_failure(self):
        """A reader failure must not block an export -- it degrades to a pass."""
        from mayatk.env_utils import scene_state

        with patch.object(
            scene_state.SceneState, "read", side_effect=RuntimeError("boom")
        ):
            status, msgs = self.tm.check_material_compatibility("glTF 2.0")
        self.assertTrue(status)
        self.assertEqual(msgs, [])

    def test_convert_textures_updates_export_materials_to_the_template(self):
        """The task half: delegates to MatUpdater with the template as config,
        scoped to the export materials, and invalidates the material caches so
        the post-conversion check reads fresh state. Patched at MatUpdater --
        the conversion engine has its own suite; this pins the delegation, the
        no-template no-op, and the cache invalidation."""
        from mayatk.mat_utils import mat_updater

        self._textured_shader("sourceimages/probe_ct.png", name="probeCtMat")
        self.tm.objects = [self.cube_long]
        with patch.object(mat_updater.MatUpdater, "update_materials") as updater:
            self.tm.convert_textures(None)
            updater.assert_not_called()  # 'As Authored' must not touch anything

            self.assertTrue(self.tm._get_all_materials())  # prime the cache
            self.assertIsNotNone(self.tm._cached_materials)
            self.tm.run = self.tm.run.replace(
                texture_write_back=True
            )  # in-place migration
            self.tm.convert_textures("glTF 2.0")
            updater.assert_called_once()
            kwargs = updater.call_args.kwargs
            self.assertEqual(kwargs.get("config"), "glTF 2.0")
            self.assertTrue(kwargs.get("materials"), "export materials must be passed")
            self.assertNotIn(
                "convert_textures",
                self.tm._deferred_restores,
                "write-back is permanent: no restore staged",
            )
        self.assertIsNone(
            self.tm._cached_materials,
            "conversion must invalidate the material caches",
        )

    def test_convert_textures_stages_and_restores_the_network_by_default(self):
        """Texture Output at "Export Copies": the Map Updater runs in COPY mode
        into this run's staging dir (sources never moved), and the deferred
        restore reverses the rewrite verbatim -- the file node the conversion
        added is gone, the original connection is back, and a temp staging
        dir is deleted.

        Added: 2026-08-16
        """
        from mayatk.mat_utils import mat_updater

        tex = os.path.join(self.ws_src, "probe_stage.png").replace("\\", "/")
        with open(tex, "w") as f:
            f.write("payload")
        self.addCleanup(lambda: os.path.exists(tex) and os.remove(tex))
        shader, file_node = self._textured_shader(tex, name="probeStageMat")
        cmds.setAttr(f"{shader}.glowIntensity", 0.25)
        self.tm.objects = [self.cube_long]
        self.tm.run = self.tm.run.replace(
            output_format="glb"
        )  # temp staging; skips the mel embed query
        self.tm.run = self.tm.run.replace(texture_write_back=False)

        seen = {}

        def _fake_rewire(materials=None, config=None, **_):
            seen["config"] = config
            # What a conversion does: a NEW file node into a slot, the old one
            # unplugged and repointed -- and material VALUES beside the wiring
            # (a connector sets an emission weight for its new map, adds an
            # MSAO_Map), which "Export Copies" must put back too.
            new = cmds.shadingNode("file", asTexture=True, name="probeStage_ORM")
            cmds.setAttr(
                f"{new}.fileTextureName",
                os.path.join(config["move_to_folder"], "probe_ORM.png"),
                type="string",
            )
            cmds.disconnectAttr(f"{file_node}.outColor", f"{shader}.color")
            cmds.connectAttr(f"{new}.outColor", f"{shader}.color")
            cmds.setAttr(f"{file_node}.fileTextureName", "moved.png", type="string")
            cmds.setAttr(f"{shader}.glowIntensity", 1.0)
            cmds.addAttr(shader, longName="MSAO_Map", attributeType="bool")
            seen["new"] = new
            return {}

        with patch.object(
            mat_updater.MatUpdater, "update_materials", side_effect=_fake_rewire
        ):
            self.tm.convert_textures("glTF 2.0")

        cfg = seen["config"]
        self.assertEqual(cfg["preset"], "glTF 2.0")
        self.assertEqual(cfg["transfer_mode"], "copy", "sources must never move")
        staging = cfg["move_to_folder"]
        self.assertTrue(os.path.isdir(staging))
        self.assertTrue(os.path.isfile(tex), "the source must still be on disk")
        # Rewired for the write ...
        self.assertTrue(cmds.isConnected(f"{seen['new']}.outColor", f"{shader}.color"))
        self.assertIn("convert_textures", self.tm._deferred_restores)

        # ... and put back verbatim afterwards.
        self.tm.run_deferred_restores()
        self.assertFalse(cmds.objExists(seen["new"]), "created node must be deleted")
        self.assertTrue(cmds.isConnected(f"{file_node}.outColor", f"{shader}.color"))
        self.assertEqual(cmds.getAttr(f"{file_node}.fileTextureName"), tex)
        self.assertAlmostEqual(cmds.getAttr(f"{shader}.glowIntensity"), 0.25, 5)
        self.assertFalse(cmds.attributeQuery("MSAO_Map", node=shader, exists=True))
        self.assertFalse(os.path.exists(staging), "temp staging must be removed")

    def test_convert_textures_failure_defers_to_the_check(self):
        """A MatUpdater exception must not abort the export pipeline.

        TaskFactory re-raises task exceptions, so unguarded, one unreadable
        texture kills the whole export with a traceback -- while the designed
        failure path is the paired check, which validates the actual post-task
        state and fails cleanly with the residuals named. The guard is what
        makes the check's own message ("see the Map Updater log above") true.
        """
        from mayatk.mat_utils import mat_updater

        self._textured_shader("sourceimages/probe_cf.png", name="probeCfMat")
        self.tm.objects = [self.cube_long]
        shader, file_node = self._textured_shader(
            "sourceimages/probe_cf2.png", name="probeCfMat2"
        )
        self.tm.run = self.tm.run.replace(output_format="glb")
        self.tm.run = self.tm.run.replace(texture_write_back=False)

        def _half_done_then_boom(materials=None, config=None, **_):
            # The rewrite got partway (a new node in, the old one unplugged)
            # before failing — the staged scope must still put it back.
            new = cmds.shadingNode("file", asTexture=True, name="probeCf_half")
            cmds.disconnectAttr(f"{file_node}.outColor", f"{shader}.color")
            cmds.connectAttr(f"{new}.outColor", f"{shader}.color")
            raise RuntimeError("unreadable texture")

        with patch.object(
            mat_updater.MatUpdater, "update_materials", side_effect=_half_done_then_boom
        ):
            self.tm.convert_textures("glTF 2.0")  # must not raise
        self.assertIsNone(
            self.tm._cached_materials,
            "caches must invalidate even when the conversion failed",
        )
        # Staged BEFORE the mutation, so the half-done rewrite is undone by
        # the same deferred restore perform_export runs from its finally.
        self.assertIn("convert_textures", self.tm._deferred_restores)
        self.tm.run_deferred_restores()
        self.assertFalse(cmds.objExists("probeCf_half"))
        self.assertTrue(cmds.isConnected(f"{file_node}.outColor", f"{shader}.color"))

    def test_texture_check_links_survive_the_staged_conversion(self):
        """A texture check run after a staged conversion links nodes that
        outlive the restore, and lists a byte-identical staged copy once.

        Regression (production, 2026-09-13): check_texture_file_size listed
        ``ROOM_ENV_Base_color`` AND ``ROOM_ENV_Base_color1`` for the same
        map. The ``1`` node was the conversion's rewire, which the aborted
        run's restore deleted, so its link selected nothing.

        Added: 2026-09-13
        """
        import re
        from mayatk.mat_utils import mat_updater

        tex = os.path.join(self.ws_src, "pipe_big.png")
        with open(tex, "wb") as f:
            f.write(b"\0" * (2 * 1024 * 1024))  # 2 MB
        shader, file_node = self._textured_shader(
            tex.replace("\\", "/"), name="pipeBigMat"
        )
        # A second material keeps the ORIGINAL node in the export history.
        cube2 = cmds.polyCube(name="PipelineCube2")[0]
        shader2 = cmds.shadingNode("lambert", asShader=True, name="pipeBigMat2")
        cmds.connectAttr(f"{file_node}.outColor", f"{shader2}.color")
        _assign_shader(cube2, shader2)
        self.tm.objects = [self.cube_long, cmds.ls(cube2, long=True)[0]]
        # The production shape, FBX + GLB: the FBX carries the scene's maps,
        # so the size check gates (GLB-only steps aside). No export path, so
        # the conversion still stages into a temp dir.
        self.tm.run = self.tm.run.replace(output_format="fbx_glb")
        self.tm.run = self.tm.run.replace(export_path="")
        self.tm.run = self.tm.run.replace(texture_write_back=False)

        seen = {}

        def _fake_rewire(materials=None, config=None, **_):
            # Copy mode: an untouched map is copied into staging and a NEW
            # node (Maya uniquifies the name to `<name>1`) takes the slot.
            staged = os.path.join(config["move_to_folder"], "pipe_big.png")
            shutil.copyfile(tex, staged)
            new = cmds.shadingNode("file", asTexture=True, name=file_node)
            cmds.setAttr(
                f"{new}.fileTextureName", staged.replace("\\", "/"), type="string"
            )
            cmds.connectAttr(f"{new}.outColor", f"{shader}.color", force=True)
            seen["new"] = new
            return {}

        # Registered before the rewire, so a failed assertion still drops the
        # staged network and its temp dir (the explicit restore below is part
        # of the assertion; running it twice is a no-op).
        self.addCleanup(self.tm.run_deferred_restores)
        with patch.object(
            mat_updater.MatUpdater, "update_materials", side_effect=_fake_rewire
        ):
            self.tm.convert_textures("glTF 2.0")
        self.assertNotEqual(seen["new"], file_node)

        node_links = re.compile(r"node=([^\"'&>]+)")
        passed, msgs = self.tm.check_texture_file_size(1)
        self.assertFalse(passed)
        lines = [m for m in msgs if "pipe_big.png" in m]
        self.assertEqual(len(lines), 1, f"one line per identical map: {msgs}")
        linked = set(node_links.findall(lines[0]))
        self.assertEqual(linked, {file_node}, lines[0])
        # The other texture checks link through the same resolver.
        _, path_msgs = self.tm.check_path_length(10)
        path_linked = {n for m in path_msgs for n in node_links.findall(m)}
        self.assertEqual(path_linked, {file_node}, path_msgs)

        self.tm.run_deferred_restores()
        self.assertFalse(cmds.objExists(seen["new"]))
        for node in linked:
            self.assertTrue(cmds.objExists(node), f"dead link: {node}")

    def test_check_path_length_flags_over_long_texture_paths(self):
        """A texture path over the budget fails; the same path under it passes."""
        _, file_node = self._textured_shader(
            "sourceimages/pipe_len.png", name="pipeMatLen"
        )
        self.tm.objects = [self.cube_long]

        status, _msgs = self.tm.check_path_length(4096)
        self.assertTrue(status, "a short path must pass a generous budget")

        long_path = "C:/" + ("dir/" * 40) + "pipe_len.png"
        cmds.setAttr(f"{file_node}.fileTextureName", long_path, type="string")
        self.tm._invalidate_material_caches()
        status, msgs = self.tm.check_path_length(60)
        self.assertFalse(status, "a path over the budget must fail")
        self.assertTrue(any("exceed" in m for m in msgs))
        self.assertTrue(any("pipe_len.png" in m for m in msgs))

    def test_check_path_length_resolves_relatives_against_the_project_root(self):
        """A short RELATIVE path is measured as MAYA resolves it — against the
        project root, not the process CWD (``os.path.abspath``'s base), which
        is only the same directory when the set_workspace task happened to run.
        """
        rel = "sourceimages/pipe_rel_len.png"
        _, _file_node = self._textured_shader(rel, name="pipeMatRelLen")
        self.tm.objects = [self.cube_long]

        root = cmds.workspace(query=True, rootDirectory=True)
        expected = os.path.normpath(os.path.join(root, rel)).replace("\\", "/")
        self.assertLess(len(rel), len(expected))

        # Under a budget that fits the resolved path, it passes ...
        self.assertTrue(self.tm.check_path_length(len(expected))[0])
        # ... and one character tighter, it fails and reports THAT length.
        status, msgs = self.tm.check_path_length(len(expected) - 1)
        self.assertFalse(status, "measured the stored path, not the resolved one")
        self.assertTrue(any(f"({len(expected)} chars)" in m for m in msgs), msgs)

        # A CWD that is not the project root must not change the verdict.
        cwd = os.getcwd()
        try:
            os.chdir(self.temp_dir)
            self.assertTrue(self.tm.check_path_length(len(expected))[0])
        finally:
            os.chdir(cwd)

    def test_check_path_length_off_and_default(self):
        """0/'OFF' disables; None falls back to this OS's limit."""
        long_path = "C:/" + ("dir/" * 40) + "pipe_off.png"
        _, _file_node = self._textured_shader(long_path, name="pipeMatOff")
        self.tm.objects = [self.cube_long]

        self.assertTrue(self.tm.check_path_length(0)[0])
        self.assertTrue(self.tm.check_path_length("OFF")[0])
        # ~170 chars — over MAX_PATH, under a long-paths-enabled limit, so the
        # verdict must follow whatever THIS machine reports.
        over = len(long_path) > ptk.FileUtils.path_length_limit()
        self.assertEqual(self.tm.check_path_length()[0], not over)

    def test_check_path_length_flags_the_export_destination(self):
        """The destination is the path most likely to blow the limit."""
        self.tm.objects = [self.cube_long]
        original = getattr(self.tm, "export_path", None)
        self.tm.run = self.tm.run.replace(
            export_path="C:/" + ("dir/" * 40) + "asset.fbx"
        )
        try:
            status, msgs = self.tm.check_path_length(60)
            self.assertFalse(status)
            self.assertTrue(any("export path" in m for m in msgs))
        finally:
            self.tm.run = self.tm.run.replace(export_path=original)

    # -- GLB-only sidecar ordering --------------------------------------

    def test_glb_only_failed_conversion_writes_no_sidecar(self):
        """A failed FBX→GLB conversion has no deliverable — the hierarchy
        baseline must NOT roll forward (old ordering wrote it first)."""
        try:
            if not cmds.pluginInfo("fbxmaya", q=True, loaded=True):
                cmds.loadPlugin("fbxmaya")
        except Exception:
            self.skipTest("FBX plugin not available")
        from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import (
            SceneDataSidecar,
        )

        self.tm.create_glb = lambda **kw: None
        result = self.exporter.perform_export(
            export_dir=self.temp_dir,
            objects=[self.cube],
            file_format="FBX export",
            tasks={"output_format": "glb"},
        )
        self.assertFalse(result)
        manifest = SceneDataSidecar.manifest_path_for(self.exporter.export_path)
        self.assertFalse(
            os.path.exists(manifest),
            "failed GLB-only export must not roll the sidecar baseline forward",
        )

    # -- USD output format ---------------------------------------------------

    def test_usd_output_format_writes_a_usd_layer_through_mayausd(self):
        """``output_format="usd"`` ships a real USD layer: same pipeline, USD write."""
        try:
            cmds.loadPlugin("mayaUsdPlugin", quiet=True)
        except Exception:
            self.skipTest("mayaUsdPlugin not available")
        result = self.exporter.perform_export(
            export_dir=self.temp_dir,
            objects=[self.cube],
            output_name="usd_format.fbx",  # a typed .fbx must not leak into the name
            tasks={"output_format": "usd"},
        )
        self.assertTrue(result)
        path = self.exporter.export_path
        self.assertEqual(os.path.basename(path), "usd_format.usd")
        self.assertTrue(os.path.isfile(path))
        self.assertTrue(ptk.UsdFile.is_usd_file(path))
        self.assertFalse(os.path.exists(os.path.splitext(path)[0] + ".fbx"))
        # The cube is a real prim in the layer.
        from pxr import Usd  # bundled with mayaUsd

        stage = Usd.Stage.Open(path)
        self.assertIsNotNone(stage)
        names = {prim.GetName() for prim in stage.Traverse()}
        self.assertIn(self.cube.split("|")[-1], names)

    def test_usd_format_reports_the_fbx_only_knobs_as_inert(self):
        """A preset / takes / bake-range still selected are FBX-only -- said, not hidden."""
        try:
            cmds.loadPlugin("mayaUsdPlugin", quiet=True)
        except Exception:
            self.skipTest("mayaUsdPlugin not available")
        with patch.object(self.exporter, "load_fbx_export_preset") as m_preset:
            with self.assertLogs(self.exporter.logger, level="WARNING") as logs:
                result = self.exporter.perform_export(
                    export_dir=self.temp_dir,
                    objects=[self.cube],
                    output_name="usd_inert",
                    preset_file="C:/nowhere/x.fbxexportpreset",
                    tasks={"output_format": "usd", "set_bake_animation_range": True},
                )
        self.assertTrue(result)
        m_preset.assert_not_called()
        text = "\n".join(logs.output)
        self.assertIn("preset", text.lower())
        self.assertIn("set_bake_animation_range", text)

    def test_usd_format_samples_only_the_animated_span(self):
        """frameRange is a cost multiplier: keys at 5..12 sample 5..12, static samples nothing."""
        try:
            cmds.loadPlugin("mayaUsdPlugin", quiet=True)
        except Exception:
            self.skipTest("mayaUsdPlugin not available")
        from mayatk.env_utils.usd import UsdUtils

        cmds.setKeyframe(self.cube, attribute="translateY", time=5, value=0)
        cmds.setKeyframe(self.cube, attribute="translateY", time=12, value=3)
        with patch.object(UsdUtils, "export", return_value="x.usd") as m_export:
            self.exporter.perform_export(
                export_dir=self.temp_dir,
                objects=[self.cube],
                output_name="usd_anim",
                tasks={"output_format": "usd"},
            )
        opts = m_export.call_args.kwargs["options"]
        self.assertEqual(tuple(opts["frameRange"]), (5.0, 12.0))
        self.assertEqual(opts["convertMaterialsTo"], ["UsdPreviewSurface"])
        self.assertEqual(opts["defaultMeshScheme"], "none")

    # -- Texture File Type: the GLB half (texture_file_type) --------------

    def test_ktx2_file_type_inert_without_glb_output(self):
        """KTX2 can only ship inside a GLB: FBX-only output clears it and
        never runs the encoder gate."""
        try:
            if not cmds.pluginInfo("fbxmaya", q=True, loaded=True):
                cmds.loadPlugin("fbxmaya")
        except Exception:
            self.skipTest("FBX plugin not available")
        with patch.object(
            ptk.ImgUtils,
            "resolve_ktx2_encoder",
            side_effect=AssertionError("gate must not run for FBX-only output"),
        ):
            self.exporter.perform_export(
                export_dir=self.temp_dir,
                objects=[self.cube],
                file_format="FBX export",
                tasks={"output_format": "fbx", "texture_file_type": "ktx2"},
            )
        self.assertIsNone(self.tm.run.texture_file_type)

    def test_unknown_texture_file_type_aborts(self):
        """A template typo must abort loudly at parse, not fail per-image at
        encode time behind warning noise."""
        unknown_dir = os.path.join(self.temp_dir, "unknown_fmt")
        os.makedirs(unknown_dir, exist_ok=True)
        result = self.exporter.perform_export(
            export_dir=unknown_dir,
            objects=[self.cube],
            file_format="FBX export",
            tasks={"output_format": "glb", "texture_file_type": "pngg"},
        )
        self.assertFalse(result)
        self.assertEqual(
            os.listdir(unknown_dir), [], "config error must precede export"
        )

    def test_ktx2_gate_aborts_before_any_export_work(self):
        """Missing toktx fails the run in second zero — nothing gets written."""
        gate_dir = os.path.join(self.temp_dir, "ktx2_gate")
        os.makedirs(gate_dir, exist_ok=True)
        with patch.object(
            ptk.ImgUtils,
            "resolve_ktx2_encoder",
            side_effect=FileNotFoundError("toktx missing (test)"),
        ):
            result = self.exporter.perform_export(
                export_dir=gate_dir,
                objects=[self.cube],
                file_format="FBX export",
                tasks={"output_format": "glb", "texture_file_type": "ktx2"},
            )
        self.assertFalse(result)
        self.assertEqual(
            os.listdir(gate_dir), [], "gate must fire before any file is written"
        )

    def _fake_toktx_discovery(self, installed):
        """``Ktx2Encoder.resolve_toktx`` honouring the auto_install/prompt
        contract with no binary, catalog, or network: consent "installs"
        *installed*; anything else is the fix-shaped error."""
        from pythontk.img_utils.ktx2_encoder import Ktx2Encoder

        def resolve_toktx(required=False, auto_install=False, prompt=True):
            if auto_install and ptk.AppInstaller.consent(
                prompt, "KTX-Software (toktx) is not installed. Download it now?"
            ):
                return installed
            if required:
                raise FileNotFoundError(
                    "KTX2 encoding requires 'toktx' (KTX-Software). Install it "
                    "from https://github.com/KhronosGroup/KTX-Software/releases"
                )
            return None

        return patch.multiple(
            Ktx2Encoder, available=lambda: False, resolve_toktx=resolve_toktx
        )

    def test_ktx2_gate_offers_the_install_and_continues_on_consent(self):
        """A missing toktx is offered through :meth:`confirm` (the panel's
        dialog seam); a yes installs via the managed path and the run carries
        on -- the stamped file type is the gate having passed."""
        gate_dir = os.path.join(self.temp_dir, "ktx2_consent")
        os.makedirs(gate_dir, exist_ok=True)
        asked = []

        def consent(question):
            asked.append(question)
            return True

        with (
            self._fake_toktx_discovery(os.path.join(gate_dir, "toktx.exe")),
            patch.object(self.exporter, "confirm", side_effect=consent),
            # Stop at the first seam past the gate: no scene work needed.
            patch.object(self.exporter, "_initialize_objects", return_value=[]),
        ):
            result = self.exporter.perform_export(
                export_dir=gate_dir,
                objects=[self.cube],
                file_format="FBX export",
                tasks={"output_format": "glb", "texture_file_type": "ktx2"},
            )
        self.assertFalse(result, "stopped at the object seam, past the gate")
        self.assertEqual(len(asked), 1)
        self.assertIn("KTX-Software", asked[0])
        self.assertEqual(self.tm.run.texture_file_type, "ktx2")

    def test_ktx2_with_fallback_is_the_ktx2_container_plus_the_twin_flag(self):
        """``KTX2 + PNG/JPEG`` reaches every consumer as ``ktx2``, plus the one
        flag the GLB pass forwards (``ktx2_fallback``). Plain KTX2 and Original
        are stamped per run, so neither inherits the previous run's twins."""
        gate_dir = os.path.join(self.temp_dir, "ktx2_fallback")
        os.makedirs(gate_dir, exist_ok=True)
        seen = []
        for file_type in (self.tm.KTX2_WITH_FALLBACK, "ktx2", ""):
            with (
                patch.object(ptk.ImgUtils, "ktx2_available", return_value=True),
                patch.object(ptk.ImgUtils, "ensure_ktx2_encoder", return_value=None),
                # Stop at the first seam past the parse: no scene work needed.
                patch.object(self.exporter, "_initialize_objects", return_value=[]),
            ):
                self.exporter.perform_export(
                    export_dir=gate_dir,
                    objects=[self.cube],
                    file_format="FBX export",
                    tasks={"output_format": "glb", "texture_file_type": file_type},
                )
            seen.append(
                (
                    self.tm.run.texture_file_type,
                    self.tm.run.ktx2_fallback,
                    self.tm.run.glb_texture_params()["ktx2_fallback"],
                )
            )
        self.assertEqual(
            seen, [("ktx2", True, True), ("ktx2", False, False), (None, False, False)]
        )

    def test_ktx2_gate_declined_install_aborts_without_downloading(self):
        """A "no" never touches the network and aborts in second zero."""
        gate_dir = os.path.join(self.temp_dir, "ktx2_declined")
        os.makedirs(gate_dir, exist_ok=True)
        with (
            self._fake_toktx_discovery("unused"),
            patch.object(self.exporter, "confirm", return_value=False) as confirm,
            patch.object(ptk.AppInstaller, "ensure") as ensure,
        ):
            result = self.exporter.perform_export(
                export_dir=gate_dir,
                objects=[self.cube],
                file_format="FBX export",
                tasks={"output_format": "glb", "texture_file_type": "ktx2"},
            )
        self.assertFalse(result)
        confirm.assert_called_once()
        ensure.assert_not_called()
        self.assertEqual(os.listdir(gate_dir), [])

    def test_create_glb_tells_the_converter_where_the_maps_live_now(self):
        """The conversion must offer the host's live texture folders.

        The lightmap manifest riding the FBX names its EXRs against the folder
        the bake was COMMITTED from. That is history, not a contract:
        reorganise the project (measured -- maps moved from
        ``production/maya/sourceimages`` to ``production/sourceimages``) and
        every lookup misses, so the GLB ships unlit while the bake sits one
        folder away. The exporter is the one participant that knows where they
        are today.
        """
        fake_glb = os.path.join(self.temp_dir, "lightmapped.glb")
        with open(fake_glb, "wb") as fh:
            fh.write(b"GLBDATA")
        # A REAL folder: the search list keeps existing folders only
        # (``ptk.FileDependencies.search_dirs``), so a made-up drive is dropped.
        maps = os.path.join(self.temp_dir, "maps")
        os.makedirs(maps, exist_ok=True)
        import mayatk as mtk

        seen = {}

        def fake_convert(src, **kw):
            seen.update(kw)
            return fake_glb

        with (
            patch.object(ptk.MeshConvert, "fbx_to_glb", side_effect=fake_convert),
            patch.object(mtk.EnvUtils, "texture_search_dirs", return_value=[maps]),
        ):
            self.tm.create_glb(fbx_path="ignored.fbx")
        self.assertEqual(seen.get("lightmap_dirs"), [maps])

    def test_create_glb_runs_texture_delivery_last(self):
        """The stamped format overrides the shared web-delivery container while
        leaving its ceiling in place; a delivery failure fails the deliverable
        (no silent fallback)."""
        fake_glb = os.path.join(self.temp_dir, "delivery.glb")
        with open(fake_glb, "wb") as fh:
            fh.write(b"GLBDATA")
        self.tm.run = self.tm.run.replace(optimize_textures=False)

        delivered = {}

        def fake_optimize(path, **kw):
            delivered.update(kw, path=path)
            return {"images": 1, "bytes_before": 2e6, "bytes_after": 1e6}

        self.tm.run = self.tm.run.replace(texture_file_type="webp")
        with (
            patch.object(ptk.MeshConvert, "fbx_to_glb", return_value=fake_glb),
            patch.object(
                ptk.MeshConvert, "optimize_glb_textures", side_effect=fake_optimize
            ),
        ):
            result = self.tm.create_glb(fbx_path="ignored.fbx")
        self.assertEqual(result, fake_glb)
        self.assertEqual(delivered["path"], fake_glb)
        self.assertEqual(
            delivered["max_size"],
            0,
            "Optimize Textures OFF resizes nothing (2026-09-21)",
        )
        self.assertEqual(delivered["image_format"], "WEBP")

        # A failed delivery must fail the deliverable, not ship unencoded.
        self.tm.run = self.tm.run.replace(texture_file_type="ktx2")
        with (
            patch.object(ptk.MeshConvert, "fbx_to_glb", return_value=fake_glb),
            patch.object(
                ptk.MeshConvert,
                "optimize_glb_textures",
                side_effect=RuntimeError("encode failed (test)"),
            ),
        ):
            self.assertIsNone(self.tm.create_glb(fbx_path="ignored.fbx"))

        # Original + no optimize STILL runs the pass, in the shared policy's
        # container: this panel's GLB is the web deliverable, and the
        # byte-stable default it used to have shipped 280.13 MB of PNG where
        # the preview showed 8.71 MB of the same production assembly. OFF
        # keeps every pixel (2026-09-21): it resizes nothing.
        self.tm.run = self.tm.run.replace(texture_file_type=None)
        delivered.clear()
        with (
            patch.object(ptk.MeshConvert, "fbx_to_glb", return_value=fake_glb),
            patch.object(
                ptk.MeshConvert, "optimize_glb_textures", side_effect=fake_optimize
            ),
        ):
            self.assertEqual(self.tm.create_glb(fbx_path="ignored.fbx"), fake_glb)
        policy = ptk.MeshConvert.web_delivery_texture_params(max_size=0)
        self.assertEqual({key: delivered.get(key) for key in policy}, policy)

    # -- SDK (unitless) curve exclusion ----------------------------------

    def _make_sdk_cube(self):
        driver = cmds.polyCube(name="SdkDriver")[0]
        cmds.setKeyframe(self.cube, attribute="translateX", time=0, value=0)
        cmds.setKeyframe(self.cube, attribute="translateX", time=10, value=1)
        for drv_val, driven_val in ((0.25, 0.0), (0.75, 5.0)):
            cmds.setAttr(f"{driver}.translateX", drv_val)
            cmds.setAttr(f"{self.cube}.translateY", driven_val)
            cmds.setDrivenKeyframe(
                f"{self.cube}.translateY", currentDriver=f"{driver}.translateX"
            )
        return driver

    def test_keyframe_checks_ignore_set_driven_keys(self):
        """SDK driver values (0.25/0.75) are not frame times — neither check
        may flag them (both false-positived pre-fix)."""
        self._make_sdk_cube()
        self.tm.objects = [self.cube_long]

        status, msgs = self.tm.check_floating_point_keys()
        self.assertTrue(status, f"SDK inbetweens flagged as fractional: {msgs}")
        status, msgs = self.tm.check_untied_keyframes()
        self.assertTrue(status, f"SDK curve flagged as untied: {msgs}")

    def test_snap_keys_leaves_sdk_driver_values(self):
        """snap_keys_to_frames must not rewrite driven-key driver values —
        that permanently corrupts the rig mapping."""
        from mayatk.anim_utils._anim_utils import AnimUtils

        self._make_sdk_cube()
        sdk_curve = cmds.listConnections(
            f"{self.cube}.translateY", source=True, destination=False, type="animCurve"
        )[0]
        before = cmds.keyframe(sdk_curve, query=True, floatChange=True)

        AnimUtils.snap_keys_to_frames([self.cube])

        after = cmds.keyframe(sdk_curve, query=True, floatChange=True)
        self.assertEqual(before, after, "SDK driver values were rewritten")

    def test_tie_keyframes_survives_sdk_curves(self):
        """tie_keyframes crashed outright on unitless curves pre-fix (om2's
        MFnAnimCurve.input() returns a bare float there) and must now skip
        them, leaving the driven-key mapping untouched."""
        from mayatk.anim_utils._anim_utils import AnimUtils

        self._make_sdk_cube()
        sdk_curve = cmds.listConnections(
            f"{self.cube}.translateY", source=True, destination=False, type="animCurve"
        )[0]
        before = cmds.keyframe(sdk_curve, query=True, floatChange=True)

        AnimUtils.tie_keyframes([self.cube], absolute=True)  # must not raise

        after = cmds.keyframe(sdk_curve, query=True, floatChange=True)
        self.assertEqual(before, after, "tie touched the SDK curve")

    # -- hidden geometry / below floor -----------------------------------

    def test_check_hidden_geometry_sees_display_layers(self):
        """Display-layer hiding was invisible to the check — layer-hidden
        geometry shipped unflagged in every mode."""
        layer = cmds.createDisplayLayer(name="pipeHideLayer", empty=True)
        cmds.editDisplayLayerMembers(layer, self.cube)
        cmds.setAttr(f"{layer}.visibility", 0)

        self.tm.objects = [self.cube_long]
        status, msgs = self.tm.check_hidden_geometry()
        self.assertFalse(status)
        self.assertTrue(any("display layer" in m for m in msgs))

    def test_check_hidden_geometry_skips_animated_visibility(self):
        """Animated visibility is deliberate export content (the 'visible'
        mode includes it for baking) — currently-off must NOT flag."""
        cmds.setKeyframe(self.cube, attribute="visibility", time=1, value=0)

        self.tm.objects = [self.cube_long]
        status, msgs = self.tm.check_hidden_geometry()
        self.assertTrue(status, f"animated-visibility object flagged: {msgs}")

    # -- leftover UV snapshots -------------------------------------------

    def test_check_uv_snapshots_names_a_leftover_snapshot_set(self):
        """An auto-unwrap backup set left behind ships as a real UV set, and
        the second one is TEXCOORD_1, the lightmap channel. The check reports
        it and leaves it: an export never edits what it reads.
        Added: 2026-09-15
        """
        shape = cmds.listRelatives(self.cube, shapes=True, fullPath=True)[0]
        cmds.polyUVSet(shape, create=True, uvSet="_uv_snap_0ef03239")
        self.tm.objects = [self.cube_long]
        status, msgs = self.tm.check_uv_snapshots()
        self.assertFalse(status)
        self.assertTrue(any("_uv_snap_0ef03239" in m for m in msgs), msgs)
        sets = cmds.polyUVSet(shape, query=True, allUVSets=True)
        self.assertIn("_uv_snap_0ef03239", sets, "a check must not delete")

    def test_check_uv_snapshots_passes_a_mesh_without_one(self):
        self.tm.objects = [self.cube_long]
        self.assertEqual(self.tm.check_uv_snapshots(), (True, []))

    # -- default materials -----------------------------------------------

    def test_check_default_materials_flags_the_unassigned_mesh(self):
        """A mesh nobody assigned ships as an untextured 'Default_Material'.

        Found by auditing a production deliverable: 54 of its 55 GLB materials
        carried their normal map and the 55th was Maya's fallback, on one mesh.
        Nothing about that mesh is missing or malformed, so no other check sees
        it -- it simply renders wrong, and only in the exported file.
        """
        self.tm.objects = [self.cube_long]  # a fresh cube is on lambert1
        status, msgs = self.tm.check_default_materials()

        self.assertFalse(status, "the fallback shader was not flagged")
        self.assertTrue(any("default shader" in m for m in msgs), msgs)

    def test_check_default_materials_descends_into_the_export_roots(self):
        """The export set is ROOTS, so a direct-shapes walk finds nothing.

        This is how the first cut of the check passed clean on a production
        assembly whose deliverable carried the very material it looks for: the
        scene is exported by its top groups, and ``_live_objects`` re-resolves
        those groups rather than the hierarchy beneath them.
        """
        group = cmds.ls(cmds.group(self.cube, name="pipe_default_grp"), long=True)[0]

        self.tm.objects = [group]  # a GROUP, the way a real export is scoped
        status, msgs = self.tm.check_default_materials()

        self.assertFalse(status, "the check never looked below the export root")
        self.assertTrue(any("pipe" in m for m in msgs), msgs)

    def _deformed_cube_orig(self, tag):
        """*self.cube* with a cluster on it; returns its orig shape.

        A DEFORMER is what creates an orig shape -- construction history alone
        does not, which is what made the first version of these tests skip.
        """
        from mayatk.mat_utils._mat_utils import MatUtils
        from mayatk.node_utils._node_utils import NodeUtils

        MatUtils.assign_mat(
            self.cube, MatUtils.create_mat("lambert", name=f"pipe_{tag}_mat")
        )
        cmds.select(self.cube, replace=True)
        cmds.cluster(self.cube, name=f"pipe_{tag}_cluster")
        orig = [
            s
            for s in cmds.listRelatives(
                self.cube_long, shapes=True, fullPath=True, noIntermediate=False
            )
            or []
            if NodeUtils.is_intermediate(s)
        ]
        self.assertTrue(orig, "fixture produced no orig shape")
        return orig[0]

    def test_check_default_materials_flags_an_orig_riding_other_geometry(self):
        """The case that actually shipped: an orig shape parented under a
        transform that is not its mesh reaches the FBX, carries no shading
        group, and WINS over that transform's real shape -- so the deliverable
        got the wrong geometry, untextured."""
        orig = self._deformed_cube_orig("riding")
        # A host with its OWN, different geometry -- that is what makes this
        # corruption rather than instancing.
        host = cmds.ls(cmds.polySphere(name="pipe_orig_host")[0], long=True)[0]
        cmds.parent(orig, host, shape=True, addObject=True, relative=True)

        self.tm.objects = [self.cube_long, host]
        status, msgs = self.tm.check_default_materials()
        self.assertFalse(status, "an orig riding other geometry was not flagged")
        self.assertTrue(any("orig shape riding" in m for m in msgs), msgs)

    def test_check_default_materials_allows_an_instanced_deformed_mesh(self):
        """Instancing a deformed mesh shares its orig across every instance.

        So "intermediate with more than one parent" is not a defect: on the
        production scene 5 of the 6 multi-parent orig shapes were ordinary
        instancing (one at 276 parents), and flagging them would have buried
        the single real offender in noise.
        """
        orig = self._deformed_cube_orig("instanced")
        # A true instance: the SAME real shape, so both parents agree.
        instance = cmds.ls(cmds.instance(self.cube_long)[0], long=True)[0]
        self.assertIn(instance, cmds.ls(instance, long=True))

        self.tm.objects = [self.cube_long, instance]
        status, msgs = self.tm.check_default_materials()
        self.assertTrue(status, f"ordinary instancing was flagged: {msgs}")
        self.assertNotIn(orig.split("|")[-1], " ".join(msgs))

    def test_check_default_materials_ignores_an_ordinary_orig_shape(self):
        """Every deformed mesh has one and it never exports."""
        self._deformed_cube_orig("plain")

        self.tm.objects = [self.cube_long]
        status, msgs = self.tm.check_default_materials()
        self.assertTrue(status, f"an ordinary orig shape was flagged: {msgs}")

    def test_check_default_materials_passes_an_assigned_mesh(self):
        """Assigning any real material is what makes it pass."""
        from mayatk.mat_utils._mat_utils import MatUtils

        mat = MatUtils.create_mat("lambert", name="pipe_assigned_mat")
        MatUtils.assign_mat(self.cube, mat)

        self.tm.objects = [self.cube_long]
        status, msgs = self.tm.check_default_materials()
        self.assertTrue(status, f"an assigned mesh was flagged: {msgs}")

    def test_check_default_materials_ignores_objects_outside_the_export_set(self):
        """An unassigned mesh that never ships is not this export's problem."""
        stray = cmds.ls(cmds.polyCube(name="pipe_stray_default")[0], long=True)[0]
        from mayatk.mat_utils._mat_utils import MatUtils

        mat = MatUtils.create_mat("lambert", name="pipe_scoped_mat")
        MatUtils.assign_mat(self.cube, mat)

        self.tm.objects = [self.cube_long]  # deliberately NOT the stray
        status, msgs = self.tm.check_default_materials()
        self.assertTrue(status, f"flagged something outside the set: {msgs}")
        self.assertFalse(any(stray.split("|")[-1] in m for m in msgs), msgs)

    def test_the_default_material_check_is_offered_in_the_ui(self):
        """A check the panel cannot turn off is not an optional check."""
        definition = self.tm.check_definitions["check_default_materials"]
        self.assertEqual(definition["widget_type"], "QCheckBox")
        self.assertIn("Default", definition["setText"])

    def test_the_below_floor_check_is_a_depth_spin_box_with_off_at_zero(self):
        """A depth, not a checkbox (2026-09-13): the spin box IS how far
        geometry may reach below the floor, 0 is OFF, and it carries a fresh
        objectName so a checkbox-era template's bool trips the uncovered-keys
        warning instead of restoring as a depth of 1.0."""
        definition = self.tm.check_definitions["check_objects_below_floor"]
        self.assertEqual(definition["widget_type"], "SpinBox")
        self.assertEqual(definition["object_name"], "floor_depth")
        self.assertEqual(definition["setCustomDisplayValues"], {0: "OFF"})
        self.assertEqual(definition["setValue"], self.tm._DEFAULT_FLOOR_TOLERANCE)
        self.assertEqual(definition["value_method"], "value")
        self.assertEqual(definition["set_limits"][3], 2, "a depth has decimals")

    def test_check_objects_below_floor_ignores_curves(self):
        """A control curve below Y=0 is not 'geometry below floor'."""
        circle = cmds.circle(name="pipeFloorCurve")[0]
        cmds.setAttr(f"{circle}.translateY", -5)
        cmds.setAttr(f"{self.cube}.translateY", 5)

        self.tm.objects = cmds.ls([self.cube, circle], long=True)
        status, msgs = self.tm.check_objects_below_floor()
        self.assertTrue(status, f"non-surface shape flagged below floor: {msgs}")


class _StubPresetCombo:
    """Stand-in for cmb000 — the preset slots only touch these three members.

    ``init_slot`` records what ``cmb000_init`` would repopulate the combo with, so a
    test can assert on the list the user ends up seeing.
    """

    def __init__(self, slots, data=None):
        self._slots = slots
        self._data = data
        self.items = None
        self.current_text = None

    def currentData(self):
        return self._data

    def setCurrentText(self, text):
        self.current_text = text

    def init_slot(self):
        self.items = dict(self._slots.presets)


class _StubPresetSelector:
    """Stand-in for cmb007 — ``cmb007_init`` wires its ``activated`` signal and
    reads the picked item's name; ``activated.emit(index)`` is a user's pick."""

    def __init__(self, names=()):
        self.names = list(names)
        self._slots = []
        self.activated = SimpleNamespace(connect=self._slots.append, emit=self._pick)

    def _pick(self, index):
        for slot in self._slots:
            slot(index)

    def itemText(self, index):
        return self.names[index]


class TestUnconfiguredFbxWrite(MayaTkTestCase):
    """A run that names no preset must not be shaped by the session it runs in.

    This path writes the FBX with ``cmds.file(...)`` directly and applies no
    options, so the deliverable took whatever the last FBX operation left
    behind. Probed 2026-08-29 (Maya 2025 / FBX 2020.3.6), the factory state it
    falls back to is instancing OFF, smoothing groups OFF and embedded media
    OFF -- so "deterministic" and "correct" are two different fixes, and only
    doing the first would ship a GLB with no textures in it.
    """

    def setUp(self):
        super().setUp()
        self.exporter = SceneExporter()
        self.out = tempfile.mkdtemp(prefix="unconfigured_fbx_")
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)

    def _keyed_export_set(self, name):
        """A group whose CHILD carries keys — the shape of a real export set."""
        group = cmds.group(empty=True, name=f"{name}_root")
        cube = cmds.polyCube(name=f"{name}_child")[0]
        cmds.parent(cube, group)
        plug = f"{group}|{cube}.translateX"
        for t, v in ((1, 0.0), (6.5, 4.0), (12, 0.0)):
            cmds.setKeyframe(plug, time=t, value=v)
        return group, plug

    def _key_times(self, plug):
        return cmds.keyframe(plug, query=True, timeChange=True) or []

    def _run_export(self, group, **tasks):
        base = {
            "output_format": "fbx",
            "smart_bake": False,
            "snap_keys_to_frame": True,
            "optimize_keys": False,
            "tie_all_keyframes": False,
        }
        base.update(tasks)
        return self.exporter.perform_export(
            export_dir=self.out,
            objects=[group],
            output_name=f"anim_gate_{len(os.listdir(self.out))}",
            export_visible=True,
            tasks=base,
        )

    def test_an_export_does_not_rewrite_the_artists_keys_by_default(self):
        """Animation Output defaults to Export Copies, so the scene is handed back.

        ``snap_keys_to_frame`` MOVES every fractional key — 6.5 becomes 7 —
        and it, ``optimize_keys`` and ``tie_all_keyframes`` were all permanent
        and default-on before this gate existed, so exporting silently
        rewrote the curves an artist was still working on.
        """
        group, plug = self._keyed_export_set("gate_default")
        before = self._key_times(plug)
        self.assertIn(6.5, before, "fixture has no fractional key to snap")

        self.assertTrue(self._run_export(group))

        self.assertEqual(self._key_times(plug), before)

    def test_the_deliverable_still_gets_the_edited_keys(self):
        """Non-destructive must not mean inert: the WRITE sees the snapped curve.

        Asserted at the moment of the write, because that is the only point
        where "the export got the edit" and "the scene kept it" differ.
        """
        group, plug = self._keyed_export_set("gate_write")
        at_write = {}

        original = SceneExporter._warn_if_animation_excluded

        def spy(exporter_self):
            at_write["times"] = self._key_times(plug)
            return original(exporter_self)

        with patch.object(SceneExporter, "_warn_if_animation_excluded", spy):
            self.assertTrue(self._run_export(group))

        # 6.5 snaps to 6.0 — "nearest" rounds half to even, which is Maya's
        # and Python's behaviour and not the point of the test.
        self.assertEqual(
            at_write.get("times"), [1.0, 6.0, 12.0], "the write saw unsnapped keys"
        )
        self.assertEqual(
            self._key_times(plug), [1.0, 6.5, 12.0], "the scene was not restored"
        )

    def test_in_place_keeps_the_edits(self):
        """The other half of the gate has to actually be the other half."""
        group, plug = self._keyed_export_set("gate_in_place")

        self.assertTrue(self._run_export(group, animation_write_back=True))

        self.assertEqual(self._key_times(plug), [1.0, 6.0, 12.0])

    def test_a_glb_run_matches_the_preview_mixin_flag_for_flag(self):
        """The parity requirement, expressed against the writer it must match.

        Measured on a production assembly, the last difference between the two
        deliverables was a scene camera (`USER_POS_GEO`) that reached the
        exporter's GLB and not the preview's -- 1926 nodes against 1925 -- and
        it was there because the factory value for `FBXExportCameras` is ON
        while the mixin pins it off.
        """
        from mayatk.env_utils.handoff_export import MayaExportMixin

        options = self.exporter._default_fbx_options(glb_deliverable=True)
        self.assertEqual(
            options,
            {
                "FBXExportInstances": True,
                "FBXExportSmoothingGroups": True,
                "FBXExportEmbeddedTextures": True,
                "FBXExportTangents": True,
                "FBXExportCameras": False,
                "FBXExportLights": False,
            },
        )
        mixin = MayaExportMixin()._fbx_options({"EMBED_TEXTURES": True})
        for flag, value in options.items():
            self.assertEqual(
                value, mixin[flag], f"{flag} disagrees with the preview's writer"
            )

    def test_fbx_glb_shapes_the_shared_write_for_the_GLB(self):
        """One FBX write serves both deliverables in that mode, so the flags
        follow the GLB -- deliberately, because a GLB must not depend on which
        formats happen to ship beside it. `perform_export` passes
        `create_glb_enabled`, which covers `glb` AND `fbx_glb`."""
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        cube = cmds.polyCube(name="fbx_glb_probe")[0]
        seen = {}

        def _record(glb_deliverable):
            seen["glb"] = glb_deliverable
            return {}

        with (
            patch.object(SceneExporter, "_default_fbx_options", side_effect=_record),
            patch.object(TaskManager, "run_tasks", return_value=False),
        ):
            self.exporter.perform_export(
                export_dir=self.out,
                objects=[cube],
                tasks={"output_format": "fbx_glb", "smart_bake": False},
            )
        self.assertIs(seen.get("glb"), True)

    def test_a_loose_media_fbx_run_keeps_its_content_choices(self):
        """An FBX deliverable legitimately ships its maps beside itself -- that
        is what ``convert_to_relative_paths`` is for -- and its cameras are
        ordinary content. Only the GLB, whose viewer owns the camera and lights
        the asset by its published recipe rather than by scene lights, makes
        those losses rather than choices."""
        options = self.exporter._default_fbx_options(glb_deliverable=False)
        self.assertIs(options["FBXExportEmbeddedTextures"], False)
        self.assertIs(options["FBXExportInstances"], True)
        self.assertNotIn("FBXExportCameras", options)
        self.assertNotIn("FBXExportLights", options)

    def test_the_pins_survive_a_poisoned_session(self):
        """The end-to-end property: whatever ran before, the live plugin state
        after this call is the pinned one."""
        import maya.mel as mel

        mel.eval("FBXExportInstances -v false")  # what a prior bridge leaves
        mel.eval("FBXExportSmoothingGroups -v false")
        self.exporter._apply_default_fbx_options(glb_deliverable=True)
        self.assertTrue(mel.eval("FBXExportInstances -q"))
        self.assertTrue(mel.eval("FBXExportSmoothingGroups -q"))
        self.assertTrue(mel.eval("FBXExportEmbeddedTextures -q"))

    def test_an_animated_export_that_will_carry_no_animation_says_so(self):
        """The half ``apply_takes`` cannot own: no shots, or the takes task off.

        A preset carrying ``Export|IncludeGrp|Animation`` off bypasses the
        default-pinning entirely and makes the plugin write zero
        AnimationStacks -- silently, on a scene full of keys. Measured on a
        production assembly (2026-08-30) as a 70 MB FBX with no animation and
        nothing in the log about it.

        The export set is a GROUP whose CHILD carries the keys -- the shape of
        every hierarchy export, and the shape that made the first version of
        this guard dead on the production scene (it asked the roots, which have
        no keys of their own).
        """
        from mayatk.env_utils.fbx_utils import FbxUtils

        group = cmds.group(empty=True, name="anim_excluded_root")
        cube = cmds.polyCube(name="anim_excluded_probe")[0]
        cmds.parent(cube, group)
        cmds.setKeyframe(f"{group}|{cube}.translateX", t=1, v=0)
        cmds.setKeyframe(f"{group}|{cube}.translateX", t=20, v=5)
        self.exporter.task_manager.objects = [group]
        self.addCleanup(FbxUtils.set_animation_export, True)

        FbxUtils.set_animation_export(False)
        with patch.object(self.exporter, "logger") as log:
            fired = self.exporter._warn_if_animation_excluded()
        self.assertTrue(fired)
        self.assertTrue(log.warning.called)
        self.assertIn("NO animation", log.warning.call_args[0][0])

        # ... and stays quiet on the same scene once animation is included,
        # which is what keeps it from crying wolf on every static export.
        FbxUtils.set_animation_export(True)
        with patch.object(self.exporter, "logger") as log:
            self.assertFalse(self.exporter._warn_if_animation_excluded())
        self.assertFalse(log.warning.called)

    def test_a_keyless_export_never_warns_about_animation(self):
        """A static asset exported with a static preset is not a defect."""
        from mayatk.env_utils.fbx_utils import FbxUtils

        cube = cmds.polyCube(name="static_probe")[0]
        self.exporter.task_manager.objects = [cube]
        self.addCleanup(FbxUtils.set_animation_export, True)

        FbxUtils.set_animation_export(False)
        with patch.object(self.exporter, "logger") as log:
            self.assertFalse(self.exporter._warn_if_animation_excluded())
        self.assertFalse(log.warning.called)

    def test_the_bake_range_is_measured_over_the_exported_subtree(self):
        """A hierarchy export names roots; the animation is on their children.

        Measured on PROPS_ASSEMBLY (5 roots / 2717 transforms): the export set
        answers 0 keyframe times and its subtree answers 84 (frames 0-1778).
        Asking the shallow scope made this task skip itself on a fully animated
        assembly, leaving the plugin's factory 1-48 range to ship -- animation
        truncated to 48 frames, with only a debug line to say so.
        """
        import maya.mel as mel

        group = cmds.group(empty=True, name="bake_range_root")
        cube = cmds.polyCube(name="bake_range_child")[0]
        cmds.parent(cube, group)
        cmds.setKeyframe(f"{group}|{cube}.translateX", t=12, v=0)
        cmds.setKeyframe(f"{group}|{cube}.translateX", t=97.5, v=5)
        self.exporter.task_manager.objects = [group]

        mel.eval("FBXExportBakeComplexAnimation -v true")
        mel.eval("FBXExportBakeComplexStart -v 1")
        mel.eval("FBXExportBakeComplexEnd -v 48")
        # "keys" explicitly: this test is about the SCOPE the extent is measured
        # over, not about which source the dial picks, and the default ("auto")
        # would consult the ShotStore -- class state another test could leave
        # populated.
        self.exporter.task_manager.set_bake_animation_range("keys")

        # Start floored, end ceiled -- the task's own contract.
        self.assertEqual(mel.eval("FBXExportBakeComplexStart -q"), 12)
        self.assertEqual(mel.eval("FBXExportBakeComplexEnd -q"), 98)

    def test_a_named_preset_is_never_overridden(self):
        """The preset IS the user's configuration. A preset that deliberately
        disables instancing must not be silently corrected.

        ``run_tasks`` is stubbed to fail so the run stops immediately after the
        preset decision -- the branch under test -- without paying for a real
        FBX write and GLB conversion.
        """
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        cube = cmds.polyCube(name="preset_probe")[0]
        preset = os.path.join(self.out, "p.fbxexportpreset")
        with open(preset, "w") as fh:
            fh.write("; preset\n")

        def _run(preset_file=None):
            with (
                patch.object(SceneExporter, "_apply_default_fbx_options") as pinned,
                patch.object(SceneExporter, "load_fbx_export_preset") as loaded,
                patch.object(TaskManager, "run_tasks", return_value=False),
            ):
                self.exporter.perform_export(
                    export_dir=self.out,
                    objects=[cube],
                    preset_file=preset_file,
                    tasks={"output_format": "glb", "smart_bake": False},
                )
            return pinned.called, loaded.called

        self.assertEqual(
            _run(preset_file=None), (True, False), "no preset must pin the defaults"
        )
        self.assertEqual(
            _run(preset_file=preset), (False, True), "a preset must win outright"
        )


class TestPresetDirectoryScan(QuickTestCase):
    """``SceneExporterSlots.presets`` — the dict backing the FBX preset combo (cmb000).

    It is cached (``cmb000_init`` re-runs on every panel show and the scan is recursive
    over the whole Maya user app dir), so the cache has to notice the directory's
    *contents* changing, not just its path: a deleted preset left the combo showing
    the preset that no longer existed.
    """

    def setUp(self):
        super().setUp()
        self.preset_dir = tempfile.mkdtemp()
        # Bypass __init__ — the preset slots need `_get_preset_dir` and `ui.cmb000`,
        # not a live switchboard.
        self.slots = SceneExporterSlots.__new__(SceneExporterSlots)
        self.slots._get_preset_dir = lambda: self.preset_dir

    def tearDown(self):
        shutil.rmtree(self.preset_dir, ignore_errors=True)
        super().tearDown()

    def _write_preset(self, name):
        path = os.path.join(self.preset_dir, f"{name}.fbxexportpreset")
        with open(path, "w") as f:
            f.write("; fbx preset\n")
        return path

    def _attach_combo(self, data=None):
        combo = _StubPresetCombo(self.slots, data)
        self.slots.ui = SimpleNamespace(cmb000=combo)
        return combo

    def _frozen_dir_mtime(self):
        """Context manager pinning the preset dir's mtime (other paths stat normally).

        Without it a writer test could pass on the mtime half of the cache key
        instead of the writer's own `_invalidate_preset_cache` call — and mtime is
        exactly what cannot be relied on here, since a filesystem timestamp is
        quantized to the system clock tick (~15ms on Windows) and a write plus the
        refresh that follows it land inside one. Frozen, only the explicit
        invalidation can produce a fresh scan.
        """
        real_stat = os.stat
        frozen = real_stat(self.preset_dir)
        target = os.path.normcase(os.path.normpath(self.preset_dir))

        def fake_stat(path, *args, **kwargs):
            try:
                same = os.path.normcase(os.path.normpath(path)) == target
            except TypeError:  # fd or bytes path — never the preset dir
                same = False
            return frozen if same else real_stat(path, *args, **kwargs)

        return patch("os.stat", side_effect=fake_stat)

    def _age_dir_mtime(self):
        """Push the preset dir's mtime forward a second, monotonically.

        Stands in for the time that elapses before the next panel show: a
        filesystem timestamp is coarse (~15ms on Windows), so back-to-back
        writes in a test can share a tick where a user's edit-then-reopen
        never would.

        The advance is tracked rather than recomputed from the live stat each
        call, because ``current + 1s`` is not monotonic: two agings whose
        intervening filesystem op landed in the same coarse tick read the same
        ``st_mtime_ns`` and therefore write the same aged value. The cache is
        keyed on exactly that number, so the second aging left the key
        unchanged and the rescan never happened -- a write-then-delete pair
        would flakily report the deleted preset as still present (~1 run in 3).
        Advancing past whichever is later, the real mtime or our last stamp,
        guarantees every aging yields a distinct key.
        """
        st = os.stat(self.preset_dir)
        aged = max(st.st_mtime_ns, getattr(self, "_aged_dir_mtime_ns", 0)) + 10**9
        self._aged_dir_mtime_ns = aged
        os.utime(self.preset_dir, ns=(st.st_atime_ns, aged))

    def test_presets_lists_files_in_the_directory(self):
        self._write_preset("alpha")
        self.assertEqual(sorted(self.slots.presets), ["None", "alpha"])

    def test_no_add_or_delete_preset_slots(self):
        """Adding and deleting presets is done in the preset directory itself.

        The option box's "Add New Preset" (b003) and "Delete Current Preset"
        (b004) one-shots were dropped in favour of b007 "Open Preset
        Directory" — a .fbxexportpreset is a plain file, so the file browser
        already does both, better. Changed: 2026-08-06
        """
        for name in ("b003", "b004"):
            self.assertFalse(
                hasattr(SceneExporterSlots, name),
                f"{name} preset button handler should be removed",
            )

    def test_restored_embedded_preset_appears_in_the_refreshed_combo(self):
        """A scene template carrying an embedded FBX preset writes it to disk and
        refreshes — the third writer that has to invalidate the scan."""
        self._write_preset("alpha")
        self.assertNotIn("beta", self.slots.presets)  # populate the cache

        combo = self._attach_combo()
        with self._frozen_dir_mtime():
            self.slots._on_fbx_preset_metadata_loaded(
                {
                    "fbx_preset_name": "beta",
                    "fbx_preset_data": base64.b64encode(b"; fbx preset\n").decode(
                        "ascii"
                    ),
                }
            )

        self.assertIn("beta", combo.items)

    def _write_preset_in(self, subdir, name, body=b"; fbx preset\n"):
        """A preset in a SUBFOLDER — where Maya's own editor saves them."""
        folder = os.path.join(self.preset_dir, subdir)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{name}.fbxexportpreset")
        with open(path, "wb") as f:
            f.write(body)
        return path

    def test_a_preset_found_in_a_subfolder_is_not_duplicated_at_the_root(self):
        """Reported: the exporter "recreates a copy of the preset at root".

        Maya's own preset editor saves into a versioned subfolder
        (``Presets/2020.3.6/export/``) and the scan is RECURSIVE, so such a
        preset is already available under its name. The restore's existence
        check looked only at ``<preset_dir>/<name>.fbxexportpreset``, so
        loading a template wrote a second copy at the root -- and since the
        scan is a ``{name: path}`` dict, the two collapse to whichever it
        reached last. The root copy is frozen at template-save time, so the
        panel could then export with stale FBX settings while the artist
        edited the real preset.
        """
        live = self._write_preset_in("2020.3.6/export", "shared", b"; the real one\n")
        self.assertIn("shared", self.slots.presets)  # the scan already finds it

        self._attach_combo()
        with self._frozen_dir_mtime():
            self.slots._on_fbx_preset_metadata_loaded(
                {
                    "fbx_preset_name": "shared",
                    "fbx_preset_data": base64.b64encode(b"; stale snapshot\n").decode(
                        "ascii"
                    ),
                }
            )

        self.assertFalse(
            os.path.exists(os.path.join(self.preset_dir, "shared.fbxexportpreset")),
            "a duplicate was written at the root, shadowing the real preset",
        )
        with open(live, "rb") as f:
            self.assertEqual(f.read(), b"; the real one\n")

    def test_a_preset_no_copy_of_which_exists_is_still_restored(self):
        """The guard must not cost the feature it guards.

        And it lands in Maya's own FBX preset folder rather than loose at the
        top of the user app directory, which is the directory the SCAN walks —
        full of prefs, scripts and projects.
        """
        self._attach_combo()
        with self._frozen_dir_mtime():
            self.slots._on_fbx_preset_metadata_loaded(
                {
                    "fbx_preset_name": "brand_new",
                    "fbx_preset_data": base64.b64encode(b"; fbx preset\n").decode(
                        "ascii"
                    ),
                }
            )

        written = os.path.join(
            self.preset_dir, "FBX", "Presets", "brand_new.fbxexportpreset"
        )
        self.assertTrue(os.path.exists(written), "not written to Maya's preset folder")
        self.assertFalse(
            os.path.exists(os.path.join(self.preset_dir, "brand_new.fbxexportpreset")),
            "written loose at the scan root",
        )
        # ... and the scan still finds it there, which is what makes the
        # placement free rather than a trade.
        self.slots._invalidate_preset_cache()
        self.assertIn("brand_new", self.slots.presets)

    def test_two_presets_sharing_a_name_are_reported(self):
        """One of them silently decides every export; the combo shows one row."""
        self._write_preset("twin")
        self._write_preset_in("2020.3.6/export", "twin")
        self.slots.logger = MagicMock()

        self.slots.presets

        warnings = [str(c.args[0]) for c in self.slots.logger.warning.call_args_list]
        self.assertTrue(
            any("Two FBX presets are named 'twin'" in w for w in warnings), warnings
        )

    def test_external_change_invalidates_the_cache(self):
        """Presets added or removed outside the panel (Maya's preset editor, the
        file browser b007 opens) have no invalidation hook — the mtime in the
        cache key is what picks them up on the next show.

        This is now the ONLY add/delete path: the option box's own Add/Delete
        buttons were dropped in favour of managing the directory directly.
        """
        self._write_preset("alpha")
        self.assertNotIn("beta", self.slots.presets)  # populate the cache

        beta = self._write_preset("beta")
        self._age_dir_mtime()
        self.assertIn("beta", self.slots.presets)

        os.remove(beta)  # deleted in the file browser, not through the panel
        self._age_dir_mtime()
        self.assertNotIn("beta", self.slots.presets)
        self.assertIn("alpha", self.slots.presets)

    def test_missing_directory_yields_only_none(self):
        """A directory that has gone away must not keep serving its old scan."""
        self._write_preset("alpha")
        self.assertIn("alpha", self.slots.presets)

        shutil.rmtree(self.preset_dir)
        self.assertEqual(list(self.slots.presets), ["None"])

    def test_refresh_button_rescans_regardless_of_the_mtime(self):
        """``_refresh_presets`` — the cmb000 option-box refresh button.

        Its whole job is to pick up a directory change made while the panel sat
        open, so it must not be gated on the cache's mtime half: that key is a
        filesystem timestamp, and a preset dropped in then a refresh clicked can
        land in one tick. Frozen mtime here, so only the explicit invalidation
        can produce the fresh scan.
        """
        self._write_preset("alpha")
        self.assertNotIn("beta", self.slots.presets)  # populate the cache

        combo = self._attach_combo()
        # Write INSIDE the frozen context, so the dir's mtime never moves off
        # the one already in the cache key -- the write and the refresh sharing
        # one clock tick, which is the case the button has to survive.
        with self._frozen_dir_mtime():
            self._write_preset("beta")
            self.slots._refresh_presets()

        self.assertIn("beta", combo.items)
        self.assertIn("alpha", combo.items)

    def test_refresh_button_drops_a_preset_removed_outside_the_panel(self):
        """The other half: a refresh has to lose what the directory lost."""
        self._write_preset("alpha")
        beta = self._write_preset("beta")
        self.assertIn("beta", self.slots.presets)  # populate the cache

        combo = self._attach_combo()
        with self._frozen_dir_mtime():
            os.remove(beta)
            self.slots._refresh_presets()

        self.assertNotIn("beta", combo.items)
        self.assertIn("alpha", combo.items)


class TestGeneralTextureFileType(MayaTkTestCase):
    """Texture File Type — ONE container dial for every texture the export ships.

    Replaces the GLB-only carrier combo (``cmb006`` -> ``glb_texture_format``)
    and its companion "Optimize GLB Textures" checkbox, which duplicated the
    general Optimize Textures pass. The scene's own maps and a GLB's embedded
    copies now read the same dial, and each destination clamps what it cannot
    carry: no scene file node or FBX importer reads KTX2, and a GLB can only
    embed what glTF accepts.

    BACKLOG 2026-08-12 (why the resize half exists at all): the exporter
    converted to GLB and stopped, so a deliverable shipped its authored
    4096-square PNGs — 51.5 MB on one measured scene, 99.3% of it texture,
    against the WebXR preview's 1.2 MB of the SAME scene through
    ``ptk.MeshConvert.optimize_glb_textures``.
    """

    def setUp(self):
        super().setUp()
        self.exporter = SceneExporter(log_level="DEBUG")
        self.tm = self.exporter.task_manager
        self.cube = cmds.polyCube(name="GlbOptimizeCube")[0]
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)
        self.fake_glb = os.path.join(self.temp_dir, "optimize.glb")
        with open(self.fake_glb, "wb") as fh:
            fh.write(b"GLBDATA")

    def _run_create_glb(self, file_type, optimize, max_size=None):
        """``create_glb`` with the pass mocked -> (result, kwargs it received)."""
        self.tm.run = self.tm.run.replace(texture_file_type=file_type)
        self.tm.run = self.tm.run.replace(optimize_textures=optimize)
        self.tm.run = self.tm.run.replace(texture_max_size=max_size)
        seen = {}

        def fake_optimize(path, **kw):
            seen.update(kw, path=path)
            return {"images": 3, "bytes_before": 51.5e6, "bytes_after": 1.2e6}

        with (
            patch.object(ptk.MeshConvert, "fbx_to_glb", return_value=self.fake_glb),
            patch.object(
                ptk.MeshConvert, "optimize_glb_textures", side_effect=fake_optimize
            ),
        ):
            result = self.tm.create_glb(fbx_path="ignored.fbx")
        return result, seen

    def _parse_only(self, tasks):
        """Drive ``perform_export`` far enough to parse + stamp, then abort."""
        seen = {}

        def capture(task_dict):
            seen.update(task_dict)
            return False

        with patch.object(self.tm, "run_tasks", side_effect=capture):
            result = self.exporter.perform_export(
                export_dir=self.temp_dir,
                objects=[self.cube],
                file_format="FBX export",
                tasks=tasks,
            )
        return result, seen

    # -- the row ---------------------------------------------------------

    def test_row_is_a_textures_dial_never_a_dispatched_task(self):
        defs = self.tm.task_definitions
        self.assertIn("texture_file_type", defs)
        spec = defs["texture_file_type"]
        self.assertEqual(spec["group"], "Textures")
        self.assertEqual(spec["widget_type"], "ComboBox")
        self.assertNotIn(
            "texture_file_type",
            self.tm.TASK_ORDER,
            "a UI-only dial perform_export pops, never a dispatched task",
        )
        self.assertNotIn(
            "glb_optimize_textures",
            defs,
            "the GLB-only resize checkbox is gone — Optimize Textures covers it",
        )

    def test_original_is_index_zero_and_falsy(self):
        """Templates persist combos by INDEX, so the sentinel cannot move."""
        options = list(self.tm._texture_file_type_options.items())
        self.assertEqual(options[0][0], "Original")
        self.assertFalse(options[0][1])
        self.assertIn("ktx2", self.tm._texture_file_type_options.values())

    def test_texture_template_moved_to_the_tasks_combo(self):
        """cmb005 arms two pipeline steps, so it belongs with the tasks."""
        defs = self.tm.task_definitions
        self.assertIn("convert_textures", defs)
        self.assertEqual(defs["convert_textures"]["object_name"], "cmb005")
        self.assertEqual(defs["convert_textures"]["group"], "Textures")
        self.assertNotEqual(
            defs["convert_textures"].get("panel"),
            "settings",
            "a Tasks-combo row, not a Settings row",
        )
        # The Settings combo has no Textures section at all any more — the
        # whole texture block (Texture Output included) lives in the Tasks
        # combo's Textures group (2026-08-20).
        settings = dict(SceneExporterSlots._SETTINGS_LAYOUT)
        self.assertNotIn("Textures", settings)

    # -- the GLB half ----------------------------------------------------

    def test_untouched_dials_ship_the_web_container_at_full_resolution(self):
        """CONTRACT CHANGE (2026-08-29): this used to run no pass at all.

        Measured through every leg on one production assembly in one session:
        the WebXR preview published 8.71 MB of WebP and this path published
        280.13 MB of full-resolution PNG from the same scene, with nothing in
        either log saying they differed. A GLB written by this panel is the WEB
        deliverable, so untouched dials take the shared policy's container.

        CONTRACT CHANGE (2026-09-21): and not its ceiling. Optimize Textures at
        OFF resizes nothing -- the preview now runs these same rows, so the
        parity the 2048 ceiling once bought no longer needs it.
        """
        result, seen = self._run_create_glb(file_type=None, optimize=False)
        self.assertEqual(result, self.fake_glb)
        expected = ptk.MeshConvert.web_delivery_texture_params(max_size=0)
        self.assertEqual(
            {key: seen.get(key) for key in expected},
            expected,
            "the default must BE the shared policy's container, every pixel kept",
        )

    def test_file_type_alone_overrides_only_the_container(self):
        _result, seen = self._run_create_glb(file_type="webp", optimize=False)
        self.assertEqual(seen.get("image_format"), "WEBP")
        self.assertEqual(
            seen.get("max_size"),
            0,
            "naming a container leaves Optimize Textures OFF: nothing resampled",
        )

    def test_optimize_alone_overrides_only_the_ceiling(self):
        _result, seen = self._run_create_glb(
            file_type=None, optimize=True, max_size=1024
        )
        self.assertEqual(seen.get("max_size"), 1024)
        self.assertEqual(
            seen.get("image_format"),
            ptk.MeshConvert.WEB_DELIVERY_FORMAT,
            "naming a ceiling must not silently drop the container",
        )

    def test_glb_honors_the_general_max_texture_size_dial(self):
        """ONE size policy: the GLB reads the same dial the scene maps do,
        rather than a second ceiling hidden in the GLB pass."""
        _result, seen = self._run_create_glb(
            file_type="webp", optimize=True, max_size=1024
        )
        self.assertEqual(seen.get("max_size"), 1024)
        _result, off = self._run_create_glb(
            file_type="webp", optimize=True, max_size=None
        )
        self.assertEqual(
            off.get("max_size"),
            ptk.MeshConvert.WEB_DELIVERY_MAX_SIZE,
            "a dial naming no ceiling takes the policy's, never 'keep every "
            "pixel' -- that resolution is how Optimize Textures + WEBP still "
            "shipped 22.06 MB against the preview's 8.71 on a real assembly",
        )

    def test_the_glb_publishes_the_runs_lighting_choices(self):
        """The Baked Reflections row reaches the GLB's lighting recipe: an input
        to the envelope the build embeds, so the deliverable carries the look
        it was approved in wherever it is opened. Added: 2026-09-21"""
        self.tm.run = self.tm.run.replace(baked_reflections=0.5)
        seen = {}

        def fake_build(src, **kwargs):
            seen.update(kwargs)
            raise RuntimeError("captured")

        with patch.object(ptk.GlbPipeline, "build", side_effect=fake_build):
            self.tm.create_glb(fbx_path="ignored.fbx")
        recipe = seen["sidecar"]["handoff"]["rendering"]["lightmappedMaterials"]
        self.assertEqual(recipe["envMapIntensity"], 0.5)

    def test_the_fbx_handoff_publishes_the_runs_lighting_choices(self):
        """And the FBX's handoff record, through the run's export context: one
        decision, both carriers. Added: 2026-09-21"""
        from mayatk.env_utils.fbx_utils import FbxUtils

        self.tm.run = self.tm.run.replace(baked_reflections=0.0)
        with patch.object(FbxUtils, "publish", return_value=None) as publish:
            self.tm._publish_scene_records()
        ctx = publish.call_args[0][0]
        self.assertEqual(
            ctx.rendering, {"lightmappedMaterials": {"envMapIntensity": 0.0}}
        )

    def test_jpg_is_handed_to_the_encoder_as_jpeg(self):
        """REGRESSION: the dial's value is a file EXTENSION, but
        ``optimize_glb_textures`` passes ``image_format`` straight to Pillow and
        builds the glTF mime as ``image/<lowercased>``. ``JPG`` raises
        ``KeyError`` in Pillow and would be an invalid glTF mime if it didn't."""
        _result, seen = self._run_create_glb(file_type="jpg", optimize=False)
        self.assertEqual(seen.get("image_format"), "JPEG")
        _result, seen = self._run_create_glb(file_type="jpeg", optimize=False)
        self.assertEqual(seen.get("image_format"), "JPEG")

    def test_a_container_gltf_cannot_embed_falls_back_to_the_policy(self):
        """TGA is a fine scene container and an invalid glTF one — the GLB
        clamps rather than writing an unloadable payload, and clamps to the
        web-delivery container rather than to raw PNG: the fallback for a
        deliverable nobody can stream is not a bigger deliverable."""
        with self.assertLogs(self.tm.logger, level="INFO") as cm:
            _result, seen = self._run_create_glb(file_type="tga", optimize=False)
        self.assertEqual(seen.get("image_format"), ptk.MeshConvert.WEB_DELIVERY_FORMAT)
        self.assertTrue(
            any("TGA" in message for message in cm.output),
            f"the clamp must be said out loud: {cm.output}",
        )

    def test_logs_what_the_pass_cost_and_saved(self):
        with self.assertLogs(self.tm.logger, level="INFO") as cm:
            self._run_create_glb(file_type=None, optimize=True, max_size=2048)
        self.assertTrue(
            any("51.5 MB -> 1.2 MB" in message for message in cm.output),
            f"the pass must report what it cost and saved: {cm.output}",
        )

    def test_a_pass_that_changed_nothing_says_so(self):
        """An empty summary means the pass ran and replaced nothing.  Reported,
        so "asked for and got nothing" differs from "never ran"."""
        self.tm.run = self.tm.run.replace(texture_file_type=None)
        self.tm.run = self.tm.run.replace(optimize_textures=True)
        self.tm.run = self.tm.run.replace(texture_max_size=2048)
        with (
            patch.object(ptk.MeshConvert, "fbx_to_glb", return_value=self.fake_glb),
            patch.object(ptk.MeshConvert, "optimize_glb_textures", return_value={}),
            self.assertLogs(self.tm.logger, level="INFO") as cm,
        ):
            result = self.tm.create_glb(fbx_path="ignored.fbx")
        self.assertEqual(result, self.fake_glb)
        self.assertTrue(
            any("changed nothing" in message for message in cm.output), cm.output
        )

    # -- the scene half --------------------------------------------------

    def test_chosen_container_outranks_the_templates_per_map_spec(self):
        """``OutputTemplates.resolve_selection``'s rule, applied to scene maps."""
        self.tm.run = self.tm.run.replace(texture_file_type="tga")
        self.assertEqual(
            self.tm._resolved_output_type("C:/tex/rock_Base_color.png", "glTF 2.0"),
            "tga",
        )

    def test_delivery_only_container_never_reaches_a_scene_file_node(self):
        """KTX2 ships inside the GLB; the scene's own map keeps its container."""
        self.tm.run = self.tm.run.replace(texture_file_type="ktx2")
        self.assertEqual(
            self.tm._resolved_output_type("C:/tex/rock_Base_color.png", None), "png"
        )

    def test_webp_never_reaches_a_scene_file_node_or_the_fbx(self):
        """WebP is a GLB/web container — nothing on the FBX side reads it.

        Measured 2026-08-25: a Maya ``file`` node pointed at a 64x64 ``.webp``
        reports ``outSize`` 0x0 (png / tga / jpg all report 64x64), and a
        shipped hand-off exported with Texture File Type = WEBP embedded webp
        maps in its FBX — a model whose textures bind nowhere, with nothing in
        the log saying so. Same clamp as KTX2: the scene's own map keeps its
        container. The GLB half still carries webp — that is
        :meth:`pythontk.ExportRun.glb_texture_params`, already pinned by
        ``test_file_type_alone_is_container_only``.
        """
        self.tm.run = self.tm.run.replace(texture_file_type="webp")
        self.assertEqual(
            self.tm._resolved_output_type("C:/tex/rock_Base_color.png", None), "png"
        )
        self.assertEqual(
            self.tm._resolved_output_type("C:/tex/rock_Base_color.tga", "glTF 2.0"),
            "tga",
            "the clamp keeps the SOURCE container, not the template's",
        )

    def test_original_defers_to_the_template(self):
        self.tm.run = self.tm.run.replace(texture_file_type=None)
        self.assertIsNone(
            self.tm._resolved_output_type("C:/tex/rock_Base_color.png", None)
        )

    # -- parsing ---------------------------------------------------------

    def test_dial_is_popped_and_stamped_never_dispatched(self):
        result, seen = self._parse_only(
            {
                "output_format": "glb",
                "texture_file_type": "webp",
                "smart_bake": False,
            }
        )
        self.assertFalse(result)
        self.assertNotIn("texture_file_type", seen)
        self.assertEqual(self.tm.run.texture_file_type, "webp")

    def test_the_pass_state_cannot_go_stale_between_runs(self):
        """REGRESSION: ``run_tasks`` returns early on an empty task dict, so a
        run with nothing checked never reaches the dispatcher. Stamping the
        dials there let the PREVIOUS run's Optimize Textures survive and
        re-encode the next GLB behind the user; ``perform_export`` hands the
        manager a fresh ``ExportRun`` (``begin_run``) instead, which every run
        goes through."""
        self._parse_only(
            {"output_format": "glb", "optimize_textures": True, "smart_bake": False}
        )
        self.assertTrue(self.tm.run.optimize_textures)
        self._parse_only({"output_format": "glb"})  # nothing checked
        self.assertFalse(
            self.tm.run.optimize_textures,
            "a run with no tasks must not inherit the prior run's texture pass",
        )
        self.assertEqual(
            self.tm.run.glb_texture_params(),
            ptk.MeshConvert.web_delivery_texture_params(max_size=0),
            "and so falls back to the untouched rows (the policy's container, "
            "every pixel kept), not to the prior ceiling",
        )

    def test_the_glb_optimisation_dials_reach_the_pipeline(self):
        """Secondary map size and UASTC RDO ride the same policy call as the
        other GLB dials (unset = the policy, i.e. off); the key-reduction bound
        reaches ``GlbPipeline.build`` as ``key_tolerance``. Added: 2026-09-13"""
        self.tm.run = self.tm.run.replace(
            secondary_max_size=2048, uastc_rdo=1.0, glb_key_tolerance=1e-4
        )
        params = self.tm.run.glb_texture_params()
        self.assertEqual(
            (params["secondary_max_size"], params["uastc_rdo"]), (2048, 1.0)
        )
        with (
            patch.object(
                ptk.GlbPipeline, "build", return_value={"glb": "x.glb"}
            ) as build,
            patch.object(ptk.GlbPipeline, "envelope", return_value={}),
        ):
            self.assertEqual(self.tm.create_glb("x.fbx", announce=False), "x.glb")
        self.assertEqual(build.call_args.kwargs["key_tolerance"], 1e-4)
        self.tm.run = self.tm.run.replace(
            secondary_max_size=None, uastc_rdo=None, glb_key_tolerance=None
        )
        params = self.tm.run.glb_texture_params()
        self.assertEqual((params["secondary_max_size"], params["uastc_rdo"]), (0, None))

    def test_the_template_carrier_follows_the_selected_template(self):
        self._parse_only(
            {
                "output_format": "glb",
                "convert_textures": "glTF 2.0",
                "optimize_textures": "glTF 2.0",
            }
        )
        self.assertEqual(self.tm.run.texture_template, "glTF 2.0")

    def test_legacy_glb_texture_format_still_loads(self):
        """A template saved before the unification keeps working."""
        result, _seen = self._parse_only(
            {
                "output_format": "glb",
                "glb_texture_format": "WEBP",
                "glb_optimize_textures": True,
                "smart_bake": False,
            }
        )
        self.assertFalse(result)
        self.assertEqual(self.tm.run.texture_file_type, "webp")

    def test_new_key_wins_over_the_legacy_one(self):
        result, _seen = self._parse_only(
            {
                "output_format": "glb",
                "texture_file_type": "png",
                "glb_texture_format": "WEBP",
                "smart_bake": False,
            }
        )
        self.assertFalse(result)
        self.assertEqual(self.tm.run.texture_file_type, "png")


class TestSidecarWriteOrdering(MayaTkTestCase):
    """The scene-data sidecar must be the LAST step of every export mode.

    Written before the FBX->GLB conversion, nothing it records could describe
    the deliverable that actually shipped. Moving it also has to preserve two
    contracts that pull in opposite directions, so both are pinned here: a
    FAILED GLB must still leave the sidecar written (the FBX shipped), while a
    GLB-ONLY export that produced nothing must still write none -- rolling the
    hierarchy baseline forward for a phantom makes the next run's diff compare
    against it.
    """

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)
        self.cube = cmds.polyCube(name="OrderCube")[0]

    def _run(self, output_format, glb_result):
        """perform_export with create_glb stubbed; returns (result, call order)."""
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        calls = []

        def fake_create_glb(self_tm, *args, **kwargs):
            calls.append("glb")
            return glb_result

        def fake_sidecar(self_tm, *args, **kwargs):
            calls.append("sidecar")

        exporter = SceneExporter(log_level="WARNING")
        with (
            patch.object(TaskManager, "create_glb", fake_create_glb),
            patch.object(TaskManager, "write_scene_data_sidecar", fake_sidecar),
        ):
            result = exporter.perform_export(
                export_dir=self.temp_dir,
                objects=[self.cube],
                file_format="FBX export",
                output_name="ordering",
                # output_format is popped from the tasks dict, not a kwarg.
                tasks={"output_format": output_format},
            )
        return result, calls

    def test_fbx_glb_writes_sidecar_after_the_glb(self):
        """FBX+GLB: the GLB is converted BEFORE the sidecar is written."""
        result, calls = self._run("fbx_glb", "ok.glb")
        self.assertTrue(result)
        self.assertEqual(
            calls,
            ["glb", "sidecar"],
            "sidecar must be the last step so it can describe the GLB",
        )

    def test_fbx_glb_still_writes_sidecar_when_the_glb_fails(self):
        """A failed conversion must not cost the FBX its sidecar.

        create_glb never raises -- every failure path inside it logs and
        returns None -- which is what makes the reordering safe.
        """
        result, calls = self._run("fbx_glb", None)
        self.assertTrue(result, "the FBX still shipped, so the export succeeded")
        self.assertIn("sidecar", calls, "a failed GLB must not skip the sidecar")

    def test_glb_only_failure_writes_no_sidecar(self):
        """A GLB-only export that produced nothing must roll no baseline."""
        result, calls = self._run("glb", None)
        self.assertFalse(result)
        self.assertNotIn(
            "sidecar", calls, "nothing shipped, so no baseline may move forward"
        )


class TestPostWriteVerification(MayaTkTestCase):
    """ExportVerifier runs automatically over the files that actually shipped.

    Every other check in the exporter reads the SCENE; these read the written
    bytes back, which is the only way to catch what the write itself got wrong
    (a truncated container, a take the FBX dropped, a NaN in an accessor). Two
    contracts pull against each other and are both pinned here: the pass must
    run AFTER the sidecar, because two of its gates read that file -- and it
    must open ONLY what a consumer receives, because parsing a 163 MB FBX
    costs ~2.8 s and ~326 MB of heap inside an already-heavy Maya session, and
    a GLB-only export's FBX is a temp file nobody will ever see.
    """

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)
        self.cube = cmds.polyCube(name="VerifyCube")[0]

    @staticmethod
    def _ok_report():
        return SimpleNamespace(
            ok=True,
            rows=[],
            counts=lambda: {"PASS": 3, "WARN": 0, "FAIL": 0, "SKIP": 1},
        )

    @classmethod
    def _recorder(cls, report=None):
        """Stand-in ExportVerifier recording the kwargs it was built with."""
        seen = {}
        result = report if report is not None else cls._ok_report()

        class _Fake:
            def __init__(self, **kwargs):
                seen.clear()
                seen.update(kwargs)

            def run(self, checks=None):
                return result

        return _Fake, seen

    def _run(self, output_format, verifier, verify=True, tasks=None):
        """perform_export with the GLB + sidecar stubbed; returns call order.

        *verify* arms the "Verify The Written File" row -- the pass is opt-in,
        so every test that expects it to run has to ask for it. *tasks* adds
        rows to the run.
        """
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        calls = []

        def fake_create_glb(self_tm, fbx_path=None, announce=True):
            calls.append("glb")
            path = os.path.join(
                os.path.dirname(fbx_path or self_tm.export_path), "made.glb"
            )
            with open(path, "wb") as handle:
                handle.write(b"glTF-stub")
            return path

        def fake_sidecar(self_tm, *args, **kwargs):
            calls.append("sidecar")

        real_verify = TaskManager.verify_deliverables

        def spy_verify(self_tm, *paths, **kwargs):
            calls.append("verify")
            return real_verify(self_tm, *paths, **kwargs)

        exporter = SceneExporter(log_level="WARNING")
        with (
            patch.object(TaskManager, "create_glb", fake_create_glb),
            patch.object(TaskManager, "write_scene_data_sidecar", fake_sidecar),
            patch.object(TaskManager, "verify_deliverables", spy_verify),
            patch.object(ptk, "ExportVerifier", verifier),
        ):
            result = exporter.perform_export(
                export_dir=self.temp_dir,
                objects=[self.cube],
                file_format="FBX export",
                output_name="verified",
                tasks={
                    "output_format": output_format,
                    "verify_deliverables": verify,
                    **(tasks or {}),
                },
            )
        return result, calls

    def test_the_size_check_row_bounds_the_image_bytes_gate(self):
        """The Max Texture Size row reaches the verifier as ``max_image_bytes``.

        A GLB-only export's size check steps aside (nothing it measures ships),
        so the row's limit has to arrive here to mean anything for that format.
        Added: 2026-09-13
        """
        fake, seen = self._recorder()
        result, _calls = self._run("glb", fake, tasks={"check_texture_file_size": 16})
        self.assertTrue(result)
        self.assertEqual(seen.get("max_image_bytes"), 16 * 1024 * 1024, seen)

        fake, seen = self._recorder()
        self._run("glb", fake, tasks={"check_texture_file_size": "OFF"})
        self.assertNotIn("max_image_bytes", seen, "OFF sets no bound")

    def test_verification_runs_after_the_sidecar(self):
        """Two gates read the sidecar, so it has to be on disk already."""
        fake, seen = self._recorder()
        result, calls = self._run("fbx_glb", fake)
        self.assertTrue(result)
        self.assertEqual(
            calls,
            ["glb", "sidecar", "verify"],
            "verification is the last step -- its gates read the sidecar",
        )
        self.assertTrue(
            (seen.get("fbx") or "").endswith("verified.fbx"),
            f"the shipped FBX must be verified, got {seen!r}",
        )
        self.assertTrue(
            (seen.get("glb") or "").endswith(".glb"),
            f"the GLB written alongside must be verified, got {seen!r}",
        )

    def test_glb_only_never_opens_the_discarded_temp_fbx(self):
        """GLB-only ships one file; parsing the temp FBX is pure cost."""
        fake, seen = self._recorder()
        result, calls = self._run("glb", fake)
        self.assertTrue(result)
        self.assertIn("verify", calls)
        self.assertIsNone(
            seen.get("fbx"),
            "the temp FBX is discarded -- parsing it costs seconds and "
            "hundreds of MB for a file nobody receives",
        )
        self.assertTrue(
            (seen.get("glb") or "").endswith("verified.glb"),
            f"the shipped GLB must be verified, got {seen!r}",
        )

    def test_verification_is_opt_in_and_off_by_default(self):
        """The pass is the only one whose cost scales with the FBX rather than
        the scene, and it runs at the END of a long export -- so a run that did
        not ask for it must not pay for it.
        """
        fake, seen = self._recorder()
        result, calls = self._run("fbx_glb", fake, verify=False)
        self.assertTrue(result)
        self.assertEqual(
            calls,
            ["glb", "sidecar"],
            "an unarmed run must not open the written file at all",
        )
        self.assertFalse(seen, f"no verifier may be built, got {seen!r}")

    def test_the_verify_row_is_exposed_in_the_checks_panel(self):
        """It is a check in the user's sense -- a Checks-panel row that
        "Override Checks" switches off with the rest -- just not a dispatched
        one: a ``check_`` name would run before the file it reads exists.
        """
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        tm = TaskManager(logging.getLogger("test_verify_row"))
        row = tm.check_definitions.get("verify_deliverables")
        self.assertIsNotNone(row, "the post-write pass needs a UI row to arm it")
        self.assertIs(row["setChecked"], False)
        self.assertFalse("verify_deliverables".startswith("check_"))

    def test_missing_files_are_dropped_before_the_verifier_opens_anything(self):
        """A path that never made it to disk must not reach the verifier."""
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        tm = TaskManager(logging.getLogger("test_verify_missing"))
        built = []

        class _Boom:
            def __init__(self, **kwargs):
                built.append(kwargs)
                raise AssertionError("must not build a verifier for missing files")

        with patch.object(ptk, "ExportVerifier", _Boom):
            self.assertIsNone(
                tm.verify_deliverables(os.path.join(self.temp_dir, "nope.fbx"))
            )
        self.assertEqual(built, [])

    def test_an_oversized_fbx_is_skipped_rather_than_parsed(self):
        """The FBX record tree costs ~2x the file in heap -- bound it.

        Blowing a Maya session's memory at the very END of a long export is
        the worst possible failure, so the FBX gates step aside above the
        bound instead. The GLB is read JSON-chunk-only and needs no bound.
        """
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        tm = TaskManager(logging.getLogger("test_verify_bound"))
        fbx = os.path.join(self.temp_dir, "huge.fbx")
        with open(fbx, "wb") as handle:
            handle.write(b"x" * 4096)
        # A GLB rides along so the verifier is actually built: without it an
        # empty input set returns early and the assertion below would pass
        # for the wrong reason.
        glb = os.path.join(self.temp_dir, "small.glb")
        with open(glb, "wb") as handle:
            handle.write(b"glTF-stub")
        fake, seen = self._recorder()
        with patch.object(ptk, "ExportVerifier", fake):
            tm.verify_deliverables(fbx, glb, max_fbx_bytes=1024)
        self.assertTrue(seen, "the verifier must still run for the GLB")
        self.assertIsNone(
            seen.get("fbx"), f"an oversized FBX must not be parsed, got {seen!r}"
        )
        self.assertEqual(seen.get("glb"), glb, "the GLB gates must still run")

    def test_a_versioned_export_hands_over_its_real_sidecar(self):
        """The verifier's auto-discovery cannot find a versioned sidecar.

        With versioning on, ``write_scene_data_sidecar`` writes
        ``.{base_stem}.scene_data.json`` so a series shares one manifest,
        while ``ExportVerifier._sidecar_beside`` looks beside the file for
        ``.{stem}.scene_data.json``. Left to guess, ``clips_vs_takes`` and
        ``fbx_takes`` -- the two gates that catch a dropped take -- would SKIP
        silently on every versioned export. The exporter knows the real path.
        """
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager
        from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import (
            SceneDataSidecar,
        )

        tm = TaskManager(logging.getLogger("test_verify_sidecar"))
        tm.run = tm.run.replace(
            export_path=os.path.join(self.temp_dir, "asset_v003.fbx")
        )
        tm.run = tm.run.replace(
            versioned=True
        )  # what SceneExporter sets when the name has a counter
        glb = os.path.join(self.temp_dir, "asset_v003.glb")
        with open(glb, "wb") as handle:
            handle.write(b"glTF-stub")
        expected = SceneDataSidecar.manifest_path_for(tm.export_path, base_stem=True)
        with open(expected, "w", encoding="utf-8") as handle:
            handle.write("{}")
        self.assertNotEqual(
            os.path.basename(expected),
            ".asset_v003.scene_data.json",
            "fixture is pointless unless the stems actually differ",
        )

        fake, seen = self._recorder()
        with patch.object(ptk, "ExportVerifier", fake):
            tm.verify_deliverables(glb)
        self.assertEqual(
            seen.get("sidecar"),
            expected,
            f"the real sidecar must be handed over, not guessed; got {seen!r}",
        )

    def test_a_failing_report_is_logged_per_gate_without_failing_the_export(self):
        """The file already shipped; post-hoc QA reports, it does not unwrite."""
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        logger = logging.getLogger("test_verify_fail")
        tm = TaskManager(logger)
        glb = os.path.join(self.temp_dir, "real.glb")
        with open(glb, "wb") as handle:
            handle.write(b"glTF-stub")
        report = SimpleNamespace(
            ok=False,
            rows=[
                SimpleNamespace(
                    status="FAIL", check="glb_container", detail="truncated"
                ),
                SimpleNamespace(status="PASS", check="glb_images", detail="fine"),
            ],
            counts=lambda: {"PASS": 1, "WARN": 0, "FAIL": 1, "SKIP": 0},
        )
        fake, _ = self._recorder(report)
        with patch.object(ptk, "ExportVerifier", fake):
            with self.assertLogs(logger, level="ERROR") as captured:
                got = tm.verify_deliverables(glb)
        self.assertIs(got, report)
        self.assertTrue(
            any("glb_container" in line for line in captured.output),
            f"the failing gate must be named in the log, got {captured.output}",
        )

    def test_a_warned_gate_is_named_at_info_when_the_report_passes(self):
        """A headline counting warnings nobody can read is noise.

        Measured on a production 4K export: every run logged "1 warned" and
        nothing more -- the gate (``glb_skins``, FBX2glTF's unreferenced stub
        skins, harmless by the verifier's own word) surfaced only by running
        the verifier by hand. Named at INFO, not WARNING: a WARN does not fail
        the report, and an alarm nobody can act on trains readers to skip the
        ones they can.
        Added: 2026-09-12
        """
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        logger = logging.getLogger("test_verify_warn")
        tm = TaskManager(logger)
        glb = os.path.join(self.temp_dir, "warned.glb")
        with open(glb, "wb") as handle:
            handle.write(b"glTF-stub")
        report = SimpleNamespace(
            ok=True,
            rows=[
                SimpleNamespace(status="WARN", check="glb_skins", detail="255 stubs"),
                SimpleNamespace(status="PASS", check="glb_images", detail="fine"),
            ],
            counts=lambda: {"PASS": 1, "WARN": 1, "FAIL": 0, "SKIP": 0},
        )
        fake, _ = self._recorder(report)
        with patch.object(ptk, "ExportVerifier", fake):
            with self.assertLogs(logger, level="INFO") as captured:
                tm.verify_deliverables(glb)
        warned = [r for r in captured.records if "glb_skins" in r.getMessage()]
        self.assertEqual(
            len(warned), 1, f"the warned gate must be named: {captured.output}"
        )
        self.assertIn("255 stubs", warned[0].getMessage())
        self.assertEqual(warned[0].levelno, logging.INFO)
        self.assertFalse(
            any("glb_images" in line for line in captured.output),
            "a passing gate stays out of the log",
        )

    def test_the_texture_size_limit_reaches_the_image_bytes_gate(self):
        """The GLB's image bytes are measured against the texture size limit.

        ``check_texture_file_size`` steps aside for a GLB-only export (its
        source maps ship in nothing); perform_export hands the row's limit to
        the post-write verifier instead, which measures the images the GLB
        actually carries against it. The ``glb_image_bytes`` row is a
        measurement, so it is logged even when it passes -- every other
        passing gate stays out of the log.
        Added: 2026-09-13
        """
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        logger = logging.getLogger("test_verify_image_bytes")
        tm = TaskManager(logger)
        glb = os.path.join(self.temp_dir, "sized.glb")
        with open(glb, "wb") as handle:
            handle.write(b"glTF-stub")
        report = SimpleNamespace(
            ok=True,
            rows=[
                SimpleNamespace(
                    status="PASS", check="glb_image_bytes", detail="2 image(s), 3.4 MB"
                ),
                SimpleNamespace(status="PASS", check="glb_images", detail="fine"),
            ],
            counts=lambda: {"PASS": 2, "WARN": 0, "FAIL": 0, "SKIP": 0},
        )
        fake, seen = self._recorder(report)
        with patch.object(ptk, "ExportVerifier", fake):
            with self.assertLogs(logger, level="INFO") as captured:
                tm.verify_deliverables(glb, max_image_bytes=16 * 1024 * 1024)
        self.assertEqual(seen.get("max_image_bytes"), 16 * 1024 * 1024)
        self.assertTrue(
            any(
                "glb_image_bytes" in line and "3.4 MB" in line
                for line in captured.output
            ),
            captured.output,
        )
        self.assertFalse(any("glb_images:" in line for line in captured.output))

    def test_a_broken_verifier_cannot_fail_an_export_that_shipped(self):
        """Verification is QA over a written file -- it never raises upward."""
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        tm = TaskManager(logging.getLogger("test_verify_raises"))
        glb = os.path.join(self.temp_dir, "boom.glb")
        with open(glb, "wb") as handle:
            handle.write(b"glTF-stub")

        class _Raiser:
            def __init__(self, **kwargs):
                raise RuntimeError("verifier exploded")

        with patch.object(ptk, "ExportVerifier", _Raiser):
            self.assertIsNone(tm.verify_deliverables(glb))


class TestCheckScheduling(QuickTestCase):
    """``CHECK_DEPENDENCIES`` -- what each check reads, and what it therefore
    lets the runner skip.

    ``TaskFactory._schedule`` hoists every check above the tasks it does NOT
    read, so a gate that was always going to fail fails before the texture and
    animation phases have spent minutes on a deliverable that will not be
    written.  That only holds while the map stays honest, which is what the
    invariants below pin: an UNDER-declared check judges a scene the pipeline
    has not finished preparing, and a typo'd dependency silently hoists a check
    to the very front (an unknown task name matches nothing, which reads as
    "no dependency is running").

    Qt-free: this reads class attributes and the pure scheduler, so it needs no
    scene and no widgets.
    """

    def _manager(self):
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        # A mock logger, not logging.getLogger: the runner logs through
        # LoggingMixin's extra levels (``success``/``notice``/``log_box``),
        # which a stdlib Logger does not have.
        return TaskManager(MagicMock())

    def test_the_tables_are_the_shared_ones_and_nothing_is_scoped_away(self):
        """``TASK_ORDER`` / ``CHECK_DEPENDENCIES`` are ``ptk.ExportProfile``'s,
        scoped to this class by its decorator. This is the reference
        implementation: every shared name has a method, so the scoped tables
        ARE the shared ones and ``PARITY_GAPS`` is empty (blendertk declares
        its gaps there; a mayatk task added without a method would show up
        here as an undeclared gap). Added: 2026-09-13
        """
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        self.assertEqual(TaskManager.TASK_ORDER, ptk.ExportProfile.TASK_ORDER)
        self.assertEqual(
            TaskManager.CHECK_DEPENDENCIES, ptk.ExportProfile.CHECK_DEPENDENCIES
        )
        gaps = ptk.ExportProfile.unimplemented(TaskManager)
        self.assertEqual(gaps, {"tasks": [], "checks": []})
        self.assertEqual(TaskManager.PARITY_GAPS, {})

    def test_every_check_declares_what_it_reads(self):
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        methods = {
            name
            for name in dir(TaskManager)
            if name.startswith("check_") and name != "check_definitions"
        }
        missing = sorted(methods - set(TaskManager.CHECK_DEPENDENCIES))
        self.assertFalse(
            missing,
            "an undeclared check falls back to running after EVERY task, "
            f"forfeiting the early abort it could have had: {missing}",
        )

    def test_no_entry_names_a_check_that_does_not_exist(self):
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        stale = sorted(
            name
            for name in TaskManager.CHECK_DEPENDENCIES
            # verify_deliverables is the one intentional non-``check_`` row.
            if name.startswith("check_") and not hasattr(TaskManager, name)
        )
        self.assertFalse(stale, f"dependency entries for absent checks: {stale}")

    def test_no_dependency_names_a_task_that_does_not_exist(self):
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        known = set(TaskManager.TASK_ORDER)
        bad = {
            check: [t for t in tasks if t not in known]
            for check, tasks in TaskManager.CHECK_DEPENDENCIES.items()
        }
        bad = {k: v for k, v in bad.items() if v}
        self.assertFalse(
            bad,
            "a dependency the scheduler cannot resolve is read as 'not running' "
            f"and hoists its check in front of every task: {bad}",
        )

    def test_a_scene_wide_check_is_decided_before_the_scene_is_touched(self):
        """check_referenced_objects reads the scene's references, which no task
        creates or removes -- so it must abort before the first mutation, not
        after a full pipeline whose output the abort throws away."""
        tm = self._manager()
        tasks = {name: True for name in tm.TASK_ORDER}
        schedule = list(tm._schedule(tasks, {"check_referenced_objects": True}))
        self.assertEqual(schedule[0], "check_referenced_objects")

    def test_a_path_check_is_decided_before_the_animation_phase(self):
        """The texture/path gates read no anim task, so a broken texture path
        must not cost a smart_bake first."""
        tm = self._manager()
        tasks = {name: True for name in tm.TASK_ORDER}
        schedule = list(tm._schedule(tasks, {"check_valid_paths": True}))
        after = schedule[schedule.index("check_valid_paths") :]
        for costly in ("smart_bake", "optimize_keys", "tie_all_keyframes"):
            self.assertIn(
                costly,
                after,
                f"{costly} must still be ahead of the gate that can cancel it",
            )

    def test_a_failing_early_check_never_reaches_the_costly_tasks(self):
        """The whole point, end to end: a gate decided before the animation
        phase must cancel that phase, not be reported after it has run.
        """
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        ran = []

        def spy(name):
            def _task(self_tm):
                ran.append(name)

            return _task

        def failing_check(self_tm):
            ran.append("check_referenced_objects")
            return False, ["a reference is in the scene"]

        tm = self._manager()
        with (
            patch.object(TaskManager, "conform_shape_names", spy("conform")),
            patch.object(TaskManager, "smart_bake", spy("smart_bake")),
            patch.object(TaskManager, "check_referenced_objects", failing_check),
        ):
            passed = tm.run_tasks(
                {
                    "conform_shape_names": True,
                    "smart_bake": True,
                    "check_referenced_objects": True,
                }
            )

        self.assertFalse(passed)
        self.assertEqual(
            ran,
            ["check_referenced_objects"],
            "the check reads neither task, so both are work the aborted write "
            f"would have thrown away -- got {ran}",
        )

    def test_a_check_that_a_task_feeds_still_waits_for_it(self):
        """The flip side: hoisting must never let a check judge a scene the
        task it reads has not touched yet.
        """
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        ran = []

        def spy_task(self_tm):
            ran.append("conform_shape_names")

        def spy_check(self_tm):
            ran.append("check_mangled_names")
            return True, []

        tm = self._manager()
        with (
            patch.object(TaskManager, "conform_shape_names", spy_task),
            patch.object(TaskManager, "check_mangled_names", spy_check),
        ):
            tm.run_tasks({"conform_shape_names": True, "check_mangled_names": True})

        self.assertEqual(ran, ["conform_shape_names", "check_mangled_names"])

    def test_task_order_is_never_reordered_by_scheduling(self):
        """Only checks move.  TASK_ORDER encodes which task must see another's
        output, and no dependency graph describes that."""
        tm = self._manager()
        tasks = {name: True for name in tm.TASK_ORDER}
        checks = {
            name: True
            for name in tm.CHECK_DEPENDENCIES
            if name != "verify_deliverables"
        }
        schedule = [n for n in tm._schedule(tasks, checks) if n in tasks]
        self.assertEqual(schedule, list(tm.TASK_ORDER))


class TestOverrideChecksDisarm(QuickTestCase):
    """The Override Checks button (b009) is a per-run escape hatch, not a mode.

    Nothing else resets it -- ``__init__`` clears it once, at panel build --
    so an export forced past a failing check used to leave every later export
    in the session unvalidated too, silently. ``b000`` disarms it once the
    deliverable actually shipped; a failed export leaves it armed so a retry
    does not have to re-arm it by hand.

    Qt-free by the same rule as the other slot tests here: mayapy standalone
    owns a QGuiApplication, so a real QWidget crashes the process. The slot
    only calls ``isChecked``/``setChecked``, which a recording stub covers
    exactly.
    """

    class _StubWidget:
        """Stands in for every widget b000 reads -- one accessor per role."""

        def __init__(self, value=None):
            self._value = value
            self._checked = False

        def text(self):
            return self._value or ""

        def currentData(self):
            return self._value

        def isChecked(self):
            return self._checked

        def setChecked(self, state):
            self._checked = bool(state)

        def clear(self):
            pass

    _WIDGETS = (
        "txt000",  # output dir
        "txt001",  # output name
        "txt003",  # log panel
        "b009",  # Override Checks
        "b011",  # create log file
        "cmb000",  # fbx preset
        "cmb003",  # log level
        "cmb004",  # output format
    )

    def _slots(self, export_result, armed=True):
        """A panel with the override *armed* and the export stubbed to *export_result*."""
        slots = SceneExporterSlots.__new__(SceneExporterSlots)
        # No definitions: the payload-collection loops are covered elsewhere;
        # this test is about what happens after perform_export returns.
        import contextlib

        slots.task_manager = SimpleNamespace(task_definitions={}, check_definitions={})
        slots.sb = SimpleNamespace(
            convert_to_legal_name=lambda n: n,
            # The real Switchboard's footer-progress seam is a no-op on a UI
            # without a footer (this one has none); the stub mirrors it.
            progress=lambda **kw: contextlib.nullcontext(lambda *a: True),
            progress_adapter=lambda update: update,
        )
        slots.ui = SimpleNamespace(
            **{name: self._StubWidget() for name in self._WIDGETS}
        )
        slots.ui.b009.setChecked(armed)
        slots.export_calls = []
        slots.perform_export = lambda **kw: (
            slots.export_calls.append(kw) or export_result
        )
        slots.save_output_dir = lambda *a: None
        slots.save_output_name = lambda *a: None
        return slots

    def test_a_successful_export_disarms_the_override(self):
        slots = self._slots(True)
        slots.b000()
        self.assertEqual(len(slots.export_calls), 1)
        self.assertFalse(
            slots.ui.b009.isChecked(),
            "the next export must be validated again",
        )

    def test_a_failed_export_leaves_the_override_armed(self):
        slots = self._slots(False)
        slots.b000()
        self.assertEqual(len(slots.export_calls), 1, "the export must have run")
        self.assertTrue(
            slots.ui.b009.isChecked(),
            "mid-troubleshooting: a retry must not need a re-arm",
        )

    def _with_one_check(self, armed):
        """A panel whose only enabled row is the framerate check."""
        slots = self._slots(True, armed=armed)
        slots.task_manager.check_definitions = {
            "check_framerate": {"object_name": "check_framerate"}
        }
        slots.ui.check_framerate = self._StubWidget()
        slots.ui.check_framerate.setChecked(True)
        slots.b000()
        return slots

    def test_the_override_still_reaches_the_payload_as_a_check_skip(self):
        """Disarming happens AFTER the run -- the run itself still overrides.

        The disarmed control is what makes the armed assertion mean anything:
        without it, a payload missing the check proves only that the stub
        never collected one.
        """
        control = self._with_one_check(armed=False)
        self.assertIn(
            "check_framerate",
            control.export_calls[0]["tasks"],
            "unarmed, the enabled check must ride the payload",
        )

        slots = self._with_one_check(armed=True)
        self.assertNotIn("check_framerate", slots.export_calls[0]["tasks"])
        self.assertFalse(slots.ui.b009.isChecked())


class TestCheckValidPathsLightmaps(MayaTkTestCase):
    """``check_valid_paths`` covers the lightmaps the bake markers name.

    They have no file node, so the two texture gates never saw them: a scene
    migrated with all its textures passed the check and shipped its GLB unlit
    (reported 2026-08-26). Resolution mirrors the GLB applier's -- the
    recorded folder, the project's texture folders, then the sourceimages
    walk -- so what passes here is what the conversion will bind.
    """

    def setUp(self):
        super().setUp()
        self.exporter = SceneExporter(log_level="DEBUG")
        self.temp_dir = tempfile.mkdtemp(prefix="lm_check_")
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)
        original_ws = cmds.workspace(q=True, rd=True)
        self.addCleanup(lambda: cmds.workspace(original_ws, openWorkspace=True))
        cmds.workspace(self.temp_dir, openWorkspace=True)
        os.makedirs(os.path.join(self.temp_dir, "sourceimages"), exist_ok=True)
        self.cube = cmds.ls(cmds.polyCube(name="LitCube")[0], long=True)[0]
        self.tm = self.exporter.task_manager
        self.tm.objects = [self.cube]

    @staticmethod
    def _commit(obj, path):
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        LightmapRecords.commit({obj: path})

    def _touch(self, *parts):
        path = os.path.join(self.temp_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "wb").close()
        return path

    def _gone(self, name):
        return os.path.join(self.temp_dir, "gone", name)

    def test_a_missing_lightmap_fails_the_check_and_names_the_object(self):
        self._commit(self.cube, self._gone("LitCube_LightMap.exr"))

        passed, messages = self.tm.check_valid_paths()

        self.assertFalse(passed)
        entry = next(m for m in messages if "Missing Lightmap" in m)
        self.assertIn("LitCube_LightMap.exr", entry)
        self.assertIn("LitCube", entry)

    def test_a_lightmap_in_its_recorded_folder_passes(self):
        self._commit(self.cube, self._touch("bake", "LitCube_LightMap.exr"))

        passed, messages = self.tm.check_valid_paths()

        self.assertTrue(passed, messages)
        self.assertFalse(any("Lightmap" in m for m in messages), messages)

    def test_a_lightmap_found_elsewhere_passes_and_says_so(self):
        """Found by the walk: it ships -- the conversion is handed that folder
        -- but the FBX manifest's hint is stale until the resolve task
        rewrites it, and the check says exactly that."""
        self._commit(self.cube, self._gone("LitCube_LightMap.exr"))
        found = self._touch("sourceimages", "lightmaps", "LitCube_LightMap.exr")

        passed, messages = self.tm.check_valid_paths()

        self.assertTrue(passed, messages)
        note = next(m for m in messages if "recorded folder" in m)
        self.assertIn("LitCube_LightMap.exr", note)
        folder = os.path.normcase(os.path.abspath(os.path.dirname(found)))
        self.assertIn(
            folder,
            [
                os.path.normcase(os.path.abspath(d))
                for d in self.tm._lightmap_search_dirs()
            ],
        )

    def test_a_lightmap_outside_the_export_set_is_not_reported(self):
        other = cmds.ls(cmds.polyCube(name="NotShipping")[0], long=True)[0]
        self._commit(other, self._gone("NotShipping_LightMap.exr"))

        passed, messages = self.tm.check_valid_paths()

        self.assertTrue(passed, messages)

    def test_the_resolve_task_heals_a_stale_hint(self):
        import json

        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        # The folder is spelled from the project the scene FILE lives in
        # (2026-09-23), so the scene is saved into the test project first.
        with open(os.path.join(self.temp_dir, "workspace.mel"), "w") as fh:
            fh.write("//Maya 2025 Project Definition\n")
        os.makedirs(os.path.join(self.temp_dir, "scenes"), exist_ok=True)
        cmds.file(rename=os.path.join(self.temp_dir, "scenes", "heal.ma"))
        cmds.file(save=True, type="mayaAscii", force=True)
        self.addCleanup(cmds.file, new=True, force=True)  # off the file first

        self._commit(self.cube, self._gone("LitCube_LightMap.exr"))
        found = self._touch("sourceimages", "lm", "LitCube_LightMap.exr")

        self.tm.resolve_invalid_texture_paths()

        marker = json.loads(
            cmds.getAttr(f"{self.cube}.{LightmapRecords.LIGHTMAP_INFO_ATTR}")
        )
        # The healed folder lives in the private record, never on the marker
        # (a marker rides the FBX); stored in the portable spelling (inside
        # the project -> relative), compared by what it resolves to here.
        folder = LightmapRecords._folder_hint(marker, LightmapRecords._folder_hints())
        self.assertNotIn("dir", marker)
        self.assertEqual(folder, "sourceimages/lm")
        self.assertEqual(
            os.path.normcase(
                os.path.abspath(LightmapRecords._resolved_dir(folder, marker["map"]))
            ),
            os.path.normcase(os.path.abspath(os.path.dirname(found))),
        )
        passed, messages = self.tm.check_valid_paths()
        self.assertTrue(passed, messages)
        self.assertFalse(any("recorded folder" in m for m in messages), messages)


class TestFlattenShearedChains(unittest.TestCase):
    """The auto-fix for what ``check_sheared_local_transforms`` detects.

    Flattens each flagged node under its nearest similarity ancestor with the
    live offsetParentMatrix rewrap — worlds are preserved at every frame, the
    parent-relative matrices become exactly TRS-representable, and the staged
    deferred restore puts the hierarchy and wiring back after the write.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from maya import standalone

            try:
                standalone.initialize(name="python")
            except (RuntimeError, TypeError):
                pass
            cls.maya_available = True
        except ImportError:
            cls.maya_available = False

    def setUp(self):
        if not self.maya_available:
            self.skipTest("Maya not available")
        cmds.file(new=True, force=True)

    def tearDown(self):
        cmds.file(new=True, force=True)

    def _manager(self, objects):
        import logging

        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        tm = TaskManager.__new__(TaskManager)
        tm.objects = objects
        tm._live_objects = lambda: tm.objects
        tm.logger = logging.getLogger("test_flatten")
        tm._deferred_restores = {}
        return tm

    def test_the_bake_grid_is_dense_where_the_scan_grid_strides(self):
        """The sample cap is a SCAN budget. Reusing that strided grid to bake
        the world-fitted keys leaves every skipped frame to interpolation, and
        a fast-moving basis does not interpolate: measured on a 3436-frame
        production scene (so stride 2), the flattened wire looms came back
        exact to 1e-13 on the frames sampled and up to 2.0 of world-basis
        error on the frames between."""
        node = cmds.spaceLocator(name="span_LOC")[0]
        cmds.setKeyframe(node, attribute="translateX", time=0, value=0)
        cmds.setKeyframe(node, attribute="translateX", time=5000, value=10)
        manager = self._manager([node])

        scan = manager._shear_dense_frames()
        bake = manager._shear_dense_frames(max_samples=None)

        self.assertLessEqual(len(scan), 2001, "the scan must honour its cap")
        self.assertGreater(scan[1] - scan[0], 1, "fixture: the scan must stride")
        self.assertEqual(len(bake), 5001, "the bake needs every frame")
        self.assertEqual(bake[1] - bake[0], 1)
        self.assertEqual((bake[0], bake[-1]), (scan[0], scan[-1]))

    def _chain(self):
        """SSC joint chain under an ANIMATED ancestor — both shear sources."""
        top = cmds.group(empty=True, name="asm_GRP")
        cmds.setKeyframe(top, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(top, attribute="translateX", time=30, value=10)
        rig = cmds.group(empty=True, name="rig_GRP", parent=top)
        parent = rig
        joints = []
        for i in range(5):
            cmds.select(parent)
            j = cmds.joint(name=f"fl_jnt_{i + 1}")
            cmds.setAttr(f"{j}.translateX", 0 if i == 0 else 5)
            cmds.setAttr(f"{j}.rotateZ", 25)
            cmds.setAttr(f"{j}.scale", 1.5, 0.8, 0.8)
            joints.append(j)
            parent = j
        cmds.select(clear=True)
        return top, rig, joints

    def _worlds(self, nodes, frames=(1, 15, 30)):
        out = {}
        for t in frames:
            cmds.currentTime(t)
            for n in nodes:
                out[(n, t)] = cmds.xform(
                    cmds.ls(n, long=True)[0],
                    query=True,
                    worldSpace=True,
                    matrix=True,
                )
        return out

    def test_flatten_fixes_what_the_check_flags(self):
        top, rig, joints = self._chain()
        tm = self._manager([top])

        status, _ = tm.check_sheared_local_transforms()
        self.assertFalse(status, "fixture failed to trip the check")

        before = self._worlds(joints)
        ok, messages = tm.flatten_sheared_chains()
        self.assertTrue(ok, messages)

        after = self._worlds(joints)
        for key, m in before.items():
            dev = max(abs(a - b) for a, b in zip(m, after[key]))
            self.assertLess(dev, 1e-4, f"{key} drifted by {dev}")

        status, messages = tm.check_sheared_local_transforms()
        self.assertTrue(status, f"check still fails after flatten: {messages}")

    def test_a_write_back_flatten_records_no_kept_key_edit(self):
        """The flatten protects the curves like every key task, but its own
        restore reverses its fitted curves in every mode -- so in write-back
        mode it keeps nothing, and a run stopped after it must not name key
        edits as kept.
        Added: 2026-09-15
        """
        top, _rig, _joints = self._chain()
        tm = self._manager([top])
        tm.run = tm.run.replace(animation_write_back=True)
        try:
            ok, messages = tm.flatten_sheared_chains()
            self.assertTrue(ok, messages)
            self.assertEqual(tm.kept_edits, [])
        finally:
            tm.run_deferred_restores()

    def test_the_check_reuses_the_flattens_scan(self):
        """The check after the flatten re-ran the same scan over every node
        (31 s of a production export, 2026-09-13) to learn what the flatten
        had just measured. With the flatten's verdict standing it reports
        what the flatten could not place, re-verifies the re-anchored nodes
        on the coarse grid, and never scans the rest again. A different
        tolerance, or a new run, is the full scan."""
        top, rig, joints = self._chain()
        tm = self._manager([top])
        ok, messages = tm.flatten_sheared_chains()
        self.assertTrue(ok, messages)
        with patch.object(
            type(tm),
            "_sheared_offenders",
            side_effect=AssertionError("the full scan ran again"),
        ):
            status, messages = tm.check_sheared_local_transforms()
        self.assertTrue(status, messages)
        with patch.object(type(tm), "_sheared_offenders", return_value={}) as scan:
            tm.check_sheared_local_transforms(0.01)
        scan.assert_called_once()
        tm.begin_run(tm.run)
        with patch.object(type(tm), "_sheared_offenders", return_value={}) as scan:
            tm.check_sheared_local_transforms()
        scan.assert_called_once()

    def test_the_export_restores_leave_no_flatten_curve_behind(self):
        """The flatten keys each node on NEW curves and its restore deletes
        them by UUID. The animation snapshot was taken by the first key task
        AFTER the flatten, so it stashed those fitted curves too, and its
        restore -- which runs first (LIFO) and swaps a stash in for the live
        curve -- handed each a new UUID: the flatten restore found nothing to
        delete and the fitted keys stayed wired to the artist's rig
        (2026-09-14). The flatten protects the scene's curves before it
        touches them, and a swapped curve keeps its UUID."""
        top, rig, joints = self._chain()
        tm = self._manager([top])
        uuids = cmds.ls(joints, uuid=True)

        def state():
            out = {}
            for uuid in uuids:
                node = cmds.ls(uuid, long=True)[0]
                out[uuid] = (
                    cmds.listRelatives(node, parent=True, fullPath=True),
                    [
                        round(v, 6)
                        for attr in ("translate", "rotate", "scale", "jointOrient")
                        for v in cmds.getAttr(f"{node}.{attr}")[0]
                    ],
                    sorted(
                        cmds.listConnections(node, source=True, destination=False) or []
                    ),
                )
            return out

        curves_before, state_before = set(cmds.ls(type="animCurve")), state()
        ok, messages = tm.flatten_sheared_chains()
        self.assertTrue(ok and messages, f"fixture: nothing flattened {messages}")
        tm._protect_scene_animation()  # smart_bake, the first key task after it
        cmds.setKeyframe(  # a key edit on a fitted curve (optimize/snap/tie)
            cmds.ls(uuids[-1], long=True)[0], attribute="rotateZ", time=15, value=90
        )
        tm.run_deferred_restores()

        self.assertEqual(
            set(cmds.ls(type="animCurve")),
            curves_before,
            "a fitted curve outlived the restores",
        )
        self.assertEqual(state(), state_before)

    def test_what_the_flatten_could_not_place_is_still_reported(self):
        """No similarity ancestor to flatten under: the flatten leaves the
        chain in place and the check reports it -- from the flatten's own
        measurement, not a second scan."""
        top, rig, joints = self._chain()
        for node in (top, rig):
            cmds.setAttr(f"{node}.scale", 1.0, 2.0, 1.0)  # no clean ancestor left
        tm = self._manager([top])
        ok, messages = tm.flatten_sheared_chains()
        self.assertTrue(ok)
        self.assertTrue(any("no similarity ancestor" in m for m in messages), messages)
        with patch.object(
            type(tm),
            "_sheared_offenders",
            side_effect=AssertionError("the full scan ran again"),
        ):
            status, messages = tm.check_sheared_local_transforms()
        self.assertFalse(status, "the unplaced chain must still fail the check")
        self.assertTrue(any("cannot represent" in m for m in messages), messages)

    def test_deferred_restore_puts_the_scene_back(self):
        top, rig, joints = self._chain()
        tm = self._manager([top])
        before = self._worlds(joints)

        tm.flatten_sheared_chains()
        tm.run_deferred_restores()

        for i, j in enumerate(joints):
            expected = "rig_GRP" if i == 0 else joints[i - 1]
            self.assertEqual(
                cmds.listRelatives(j, parent=True)[0],
                expected,
                f"{j} not reparented back",
            )
        after = self._worlds(joints)
        for key, m in before.items():
            dev = max(abs(a - b) for a, b in zip(m, after[key]))
            self.assertLess(dev, 1e-4, f"{key} drifted after restore by {dev}")
        self.assertFalse(
            cmds.ls("*_flattenRewrap_MMX"),
            "restore left rewrap multMatrix nodes behind",
        )

    def test_checkbox_true_is_not_a_tolerance(self):
        """The UI hands parameterized methods the RAW widget value.

        A QCheckBox delivers ``True``, and ``True == 1.0`` — silently the
        loosest possible tolerance, making the check pass everything and the
        task fix nothing. Both must read ``True`` as "on, use the default",
        the same way ``check_duplicate_names`` reads its pre-dial ``True``.
        """
        top, rig, joints = self._chain()
        tm = self._manager([top])

        status, _ = tm.check_sheared_local_transforms(True)
        self.assertFalse(
            status,
            "check_sheared_local_transforms(True) went inert — the checkbox "
            "value was used as a cosine tolerance of 1.0",
        )

        ok, messages = tm.flatten_sheared_chains(True)
        self.assertTrue(ok)
        self.assertTrue(
            messages,
            "flatten_sheared_chains(True) flattened nothing — the checkbox "
            "value was used as a cosine tolerance of 1.0",
        )
        status, _ = tm.check_sheared_local_transforms(True)
        self.assertTrue(status, "flatten did not clear the check at UI values")

    def test_clean_scene_is_a_no_op(self):
        cube = cmds.polyCube(name="fl_clean")[0]
        tm = self._manager([cube])
        ok, messages = tm.flatten_sheared_chains()
        self.assertTrue(ok)
        self.assertEqual(messages, [])

    def test_ik_driven_chain_survives_flatten(self):
        """Flattening an IK-spanned chain must not change the solve.

        The production failure (PROPS wire looms, third report): the live
        offsetParentMatrix rewrap preserves ``matrix x OPM x parentWorld``
        only while ``matrix`` is parent-independent -- but an IK solver
        WRITES the joints' locals from the chain's parent structure, so
        reparenting mid-chain joints changes the solve itself. The looms
        matched at rest and drifted 15.9 cm exactly during their IK-active
        shot, with direct jumps equal to sequential evaluation (no lag --
        a different answer). The flatten must therefore bake world-fitted
        TRS keys sampled from the UNTOUCHED scene instead of leaving a
        live rewrap for the solver to re-solve.
        """
        rig = cmds.group(empty=True, name="rig_GRP")
        parent = rig
        joints = []
        for i in range(4):
            cmds.select(parent)
            j = cmds.joint(name=f"ikf_jnt_{i + 1}")
            cmds.setAttr(f"{j}.translateX", 0 if i == 0 else 4)
            cmds.setAttr(f"{j}.scale", 1.4, 0.85, 0.85)
            joints.append(j)
            parent = j
        cmds.setAttr(f"{joints[1]}.preferredAngleZ", 15)
        cmds.setAttr(f"{joints[2]}.preferredAngleZ", 15)
        handle = cmds.ikHandle(
            startJoint=joints[0],
            endEffector=joints[3],
            solver="ikRPsolver",
        )[0]
        handle = cmds.parent(handle, rig)[0]
        for t, (ty, tx) in ((1, (0.0, 12.0)), (30, (6.0, 9.0))):
            cmds.setKeyframe(handle, attribute="translateY", time=t, value=ty)
            cmds.setKeyframe(handle, attribute="translateX", time=t, value=tx)
        cmds.select(clear=True)
        tm = self._manager([rig])

        status, _ = tm.check_sheared_local_transforms()
        self.assertFalse(status, "IK fixture failed to trip the shear check")

        before = self._worlds(joints, frames=(1, 15, 30))
        ok, messages = tm.flatten_sheared_chains()
        self.assertTrue(ok, messages)

        after = self._worlds(joints, frames=(1, 15, 30))
        for key, m in before.items():
            dev = max(abs(a - b) for a, b in zip(m, after[key]))
            self.assertLess(
                dev,
                1e-3,
                f"{key} changed by {dev} -- the flatten altered the IK solve",
            )
        status, messages = tm.check_sheared_local_transforms()
        self.assertTrue(status, f"check still fails after flatten: {messages}")

        self.assertEqual(
            cmds.getAttr(f"{handle}.ikBlend"),
            0.0,
            "the IK handle was left solving against the flattened chain",
        )

        tm.run_deferred_restores()
        self.assertEqual(
            cmds.getAttr(f"{handle}.ikBlend"),
            1.0,
            "restore did not re-enable the IK handle",
        )
        for i, j in enumerate(joints):
            expected_parent = "rig_GRP" if i == 0 else joints[i - 1]
            self.assertEqual(
                cmds.listRelatives(j, parent=True)[0],
                expected_parent,
                f"{j} not reparented back",
            )
        restored = self._worlds(joints, frames=(1, 15, 30))
        for key, m in before.items():
            dev = max(abs(a - b) for a, b in zip(m, restored[key]))
            self.assertLess(dev, 1e-3, f"{key} wrong after restore by {dev}")

    def test_opm_nonsimilar_chain_is_flagged_and_flattened(self):
        """A connected offsetParentMatrix carrying NON-UNIFORM scale must
        flag for the flatten even when every local shear sits under
        tolerance per node.

        The production failure (PROPS _01 wire looms, fifth report): the
        tweak-follow networks feed each joint's offsetParentMatrix ~3%
        non-uniform scale. The export folds ``TRS x OPM`` onto the plugs
        (the network never reaches FBX), and the folded local's SHEAR is
        dropped by TRS-only formats -- per link under any sane tolerance,
        compounding to 0.65 cm by the chain tip. Severity therefore
        accumulates down OPM-connected chains, and the world-fitted
        flatten (similarity-ancestor refit) is the shipping fix.
        """
        rig = cmds.group(empty=True, name="rig_GRP")
        parent = rig
        joints = []
        composes = []
        for i in range(20):
            cmds.select(parent)
            j = cmds.joint(name=f"opm_jnt_{i + 1}")
            cmds.setAttr(f"{j}.translateX", 0 if i == 0 else 5)
            cmds.setAttr(f"{j}.rotateZ", 2)  # sub-tolerance shear only
            cmp_node = cmds.createNode("composeMatrix", name=f"opm_CMP_{i + 1}")
            # Production scale: ~3% non-uniform spread, ~1 degree -- the
            # worlds stay near-orthogonal (shear is second-order) and the
            # loss comes from CHAIN LENGTH, exactly the _01 loom shape.
            cmds.setAttr(f"{cmp_node}.inputScale", 1.03, 0.985, 0.985)
            cmds.setAttr(f"{cmp_node}.inputRotate", 0, 0, 1)
            cmds.connectAttr(f"{cmp_node}.outputMatrix", f"{j}.offsetParentMatrix")
            joints.append(j)
            composes.append(cmp_node)
            parent = j
        # One animated input: gives the dense scan its frame range and
        # models the production drivers moving over time.
        cmds.setKeyframe(composes[1], attribute="inputScaleX", time=1, value=1.03)
        cmds.setKeyframe(composes[1], attribute="inputScaleX", time=30, value=1.05)
        cmds.select(clear=True)
        tm = self._manager([rig])

        # Document the mechanism: recomposing from T/R/S alone (what
        # FBX/glTF keep -- the folded local's shear is dropped) already
        # disagrees with Maya's worlds on the untouched fixture.
        import maya.api.OpenMaya as om2

        def naive_tip_error(frame):
            cmds.currentTime(frame, edit=True)
            actual = om2.MMatrix(cmds.getAttr(f"{joints[-1]}.worldMatrix[0]"))
            world = None
            for j in joints:
                xf = om2.MTransformationMatrix(om2.MMatrix(cmds.getAttr(f"{j}.matrix")))
                rebuilt = om2.MTransformationMatrix()
                rebuilt.setTranslation(
                    xf.translation(om2.MSpace.kWorld), om2.MSpace.kWorld
                )
                rebuilt.setRotation(xf.rotation(asQuaternion=True))
                rebuilt.setScale(xf.scale(om2.MSpace.kWorld), om2.MSpace.kWorld)
                m = rebuilt.asMatrix()
                world = m if world is None else m * world
            return max(
                abs(world.getElement(3, c) - actual.getElement(3, c)) for c in range(3)
            )

        self.assertGreater(
            naive_tip_error(30),
            0.05,
            "fixture does not exhibit the shear-drop loss",
        )

        status, _ = tm.check_sheared_local_transforms()
        self.assertFalse(
            status,
            "OPM-nonsimilar chain passed the check -- per-node shear alone "
            "cannot see the cumulative fold loss",
        )

        def positions(frames=(1, 15, 30)):
            out = {}
            for t in frames:
                cmds.currentTime(t)
                for n in joints:
                    p = cmds.ls(n, long=True)[0]
                    out[(n, t)] = cmds.getAttr(p + ".worldMatrix[0]")[12:15]
            return out

        before = positions()
        ok, messages = tm.flatten_sheared_chains()
        self.assertTrue(ok, messages)
        after = positions()
        # World POSITIONS are the deliverable metric and the refit keeps
        # them exact (measured 0.00000 on this fixture). The world-matrix
        # AXIS elements are deliberately NOT compared: they shed the
        # worlds' own accumulated non-representable shear -- that IS the
        # repair, and it grows with chain length by construction.
        for key, m in before.items():
            dev = max(abs(a - b) for a, b in zip(m, after[key]))
            self.assertLess(dev, 1e-3, f"{key} position changed by {dev}")
        for j in cmds.ls("opm_jnt_*", type="joint"):
            self.assertFalse(
                cmds.listConnections(
                    f"{j}.offsetParentMatrix", source=True, destination=False
                ),
                f"{j} still has a connected offsetParentMatrix after flatten",
            )
        status, messages = tm.check_sheared_local_transforms()
        self.assertTrue(status, f"check still fails after flatten: {messages}")

    def test_ssc_scale_chain_is_flagged_and_flattened(self):
        """SSC + non-unit parent scale must be flagged even with no shear.

        The production failure (PROPS wire looms, fourth report): the _01
        loom chains ship with segmentScaleCompensate ON and animated
        non-uniform scale. Maya cancels each parent's scale before the
        child's transform; FBX/glTF export the T/R/S ATTRIBUTE values and
        recompose by plain matrix products, so the parent scale compounds
        down the chain -- measured +17.8%% bone stretch by jnt_11 in the
        delivered GLB. With small inter-joint rotations the LOCAL shear
        stays under tolerance, so the shear metric alone never fires: the
        check needs the SSC rule, and the flatten (which neutralises SSC
        and bakes world-fitted locals) is the shipping fix.
        """
        rig = cmds.group(empty=True, name="rig_GRP")
        parent = rig
        joints = []
        for i in range(6):
            cmds.select(parent)
            j = cmds.joint(name=f"ssc_jnt_{i + 1}")
            cmds.setAttr(f"{j}.translateX", 0 if i == 0 else 5)
            cmds.setAttr(f"{j}.rotateZ", 3)  # sub-tolerance shear only
            cmds.setAttr(f"{j}.scale", 0.8, 0.75, 0.75)
            joints.append(j)
            parent = j
        # One animated scale axis: the production drivers stretch over time.
        cmds.setKeyframe(joints[1], attribute="scaleY", time=1, value=0.8)
        cmds.setKeyframe(joints[1], attribute="scaleY", time=30, value=1.2)
        cmds.select(clear=True)
        tm = self._manager([rig])

        # Document the mechanism: naive TRS recomposition (what glTF does)
        # already disagrees with Maya's SSC worlds on the untouched fixture.
        import maya.api.OpenMaya as om2

        def naive_tip_error(frame):
            # glTF recomposes plain per-node TRS -- exactly Maya's .matrix
            # with segmentScaleCompensate OFF. Toggle it off to read the
            # shipped local, compose down the chain, restore.
            cmds.currentTime(frame, edit=True)
            actual = om2.MMatrix(cmds.getAttr(f"{joints[-1]}.worldMatrix[0]"))
            for j in joints:
                cmds.setAttr(f"{j}.segmentScaleCompensate", False)
            try:
                world = None
                for j in joints:
                    local = om2.MMatrix(cmds.getAttr(f"{j}.matrix"))
                    world = local if world is None else local * world
            finally:
                for j in joints:
                    cmds.setAttr(f"{j}.segmentScaleCompensate", True)
            return max(
                abs(world.getElement(3, c) - actual.getElement(3, c)) for c in range(3)
            )

        self.assertGreater(
            naive_tip_error(30),
            1.0,
            "fixture does not exhibit the SSC compounding loss",
        )

        status, _ = tm.check_sheared_local_transforms()
        self.assertFalse(
            status,
            "SSC chain under non-unit parent scale passed the check -- "
            "the shear metric alone cannot see attribute-level scale "
            "compounding",
        )

        before = self._worlds(joints, frames=(1, 15, 30))
        ok, messages = tm.flatten_sheared_chains()
        self.assertTrue(ok, messages)
        after = self._worlds(joints, frames=(1, 15, 30))
        for key, m in before.items():
            dev = max(abs(a - b) for a, b in zip(m, after[key]))
            self.assertLess(dev, 1e-3, f"{key} changed by {dev}")
        for j in cmds.ls("ssc_jnt_*", type="joint"):
            self.assertFalse(
                cmds.getAttr(f"{j}.segmentScaleCompensate"),
                f"{j} still has segmentScaleCompensate on after flatten",
            )
        status, messages = tm.check_sheared_local_transforms()
        self.assertTrue(status, f"check still fails after flatten: {messages}")

    def test_ssc_offenders_sees_connection_driven_parent_scale(self):
        """The dense branch: a CONNECTION-driven parent scale that only
        leaves 1.0 mid-range. No keys sit on the parent's own scale plugs
        (the production shape -- drivers feed scale through utility nodes),
        and the value is 1.0 at the current frame, so both the static read
        and the keyed-extremes read see nothing: only evaluation over the
        dense frames can flag the child.
        """
        rig = cmds.group(empty=True, name="rig_GRP")
        parent = rig
        joints = []
        for i in range(3):
            cmds.select(parent)
            j = cmds.joint(name=f"sscd_jnt_{i + 1}")
            cmds.setAttr(f"{j}.translateX", 0 if i == 0 else 5)
            joints.append(j)
            parent = j
        # Inside the export subtree, like production drivers -- the dense
        # frame range comes from the set's own keys.
        driver = cmds.parent(cmds.spaceLocator(name="sscd_driver")[0], rig)[0]
        cmds.setKeyframe(driver, attribute="translateX", time=1, value=1.0)
        cmds.setKeyframe(driver, attribute="translateX", time=15, value=0.7)
        cmds.setKeyframe(driver, attribute="translateX", time=30, value=1.0)
        md = cmds.createNode("multiplyDivide", name="sscd_md")
        cmds.connectAttr(f"{driver}.translateX", f"{md}.input1X")
        cmds.connectAttr(f"{md}.outputX", f"{joints[1]}.scaleY")
        cmds.currentTime(1, edit=True)  # driven value exactly 1.0 here
        cmds.select(clear=True)
        tm = self._manager([rig])

        offenders = tm._ssc_offenders(0.05)
        self.assertTrue(
            any(p.endswith("sscd_jnt_3") for p in offenders),
            f"dense evaluation missed the driven parent scale: {offenders}",
        )

    def test_matrix_bake_after_flatten_leaves_no_shear(self):
        """The matrix bake must not re-shear what the flatten just fixed.

        Production sequence: flatten reparents SSC joints (inverseScale
        deliberately preserved), then SmartBake's matrix pass bakes their
        effective locals via xform(matrix=) + t/r/s keys. With SSC live,
        the solver folds the compensation into the SHEAR channel -- which
        is never keyed, so the last-written value sticks statically and
        the post-bake scan finds sheared locals again (the exact residue
        that blocked the production export at 0.10-0.35 skew). The bake
        has to neutralise segmentScaleCompensate together with the OPM.
        """
        from mayatk.anim_utils.smart_bake._smart_bake import SmartBake
        from mayatk.core_utils.diagnostics.transform_diag import (
            TransformDiagnostics,
        )

        top, rig, joints = self._chain()
        tm = self._manager([top])
        ok, _ = tm.flatten_sheared_chains()
        self.assertTrue(ok)

        before = self._worlds(joints)
        SmartBake(objects=joints, use_override_layer=True).execute()

        residue = TransformDiagnostics.get_non_orthogonal_local(
            joints, tolerance=0.05, frames=(1, 15, 30)
        )
        self.assertEqual(
            residue,
            {},
            "matrix bake re-sheared flattened locals (static shear channel): "
            f"{residue}",
        )
        after = self._worlds(joints)
        for key, m in before.items():
            dev = max(abs(a - b) for a, b in zip(m, after[key]))
            self.assertLess(dev, 1e-3, f"{key} drifted through the bake by {dev}")

    def test_shear_between_coarse_samples_is_detected(self):
        """A stretch spike BETWEEN the 5-frame scan grid must still be caught.

        Production failure mode (PROPS wire looms, second report): the scan
        sampled 5 evenly-spread frames; rigs whose stretch peaked between
        samples were never flagged, never flattened, and the export dropped
        their shear exactly where they animate. Keys at 0/25/50/75/100 keep
        every grid frame uniform; the non-uniform spike lives only at f37.
        """
        top = cmds.group(empty=True, name="asm_GRP")
        cmds.setKeyframe(top, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(top, attribute="translateX", time=30, value=10)
        rig = cmds.group(empty=True, name="rig_GRP", parent=top)
        parent = rig
        joints = []
        for i in range(5):
            cmds.select(parent)
            j = cmds.joint(name=f"sp_jnt_{i + 1}")
            cmds.setAttr(f"{j}.translateX", 0 if i == 0 else 5)
            cmds.setAttr(f"{j}.rotateZ", 25)
            for attr, spike in (("scaleX", 1.5), ("scaleY", 0.8), ("scaleZ", 0.8)):
                for t in (0, 25, 50, 75, 100):
                    cmds.setKeyframe(j, attribute=attr, time=t, value=1.0)
                cmds.setKeyframe(j, attribute=attr, time=37, value=spike)
            joints.append(j)
            parent = j
        cmds.select(clear=True)
        tm = self._manager([top])

        status, _ = tm.check_sheared_local_transforms()
        self.assertFalse(
            status,
            "shear at f37 (between the coarse 0/25/50/75/100 grid) was "
            "not detected -- the scan is blind between its sample frames",
        )

        before = self._worlds(joints, frames=(1, 37, 100))
        ok, messages = tm.flatten_sheared_chains()
        self.assertTrue(ok, messages)
        self.assertTrue(messages, "flatten found nothing to fix at f37")
        after = self._worlds(joints, frames=(1, 37, 100))
        for key, m in before.items():
            dev = max(abs(a - b) for a, b in zip(m, after[key]))
            self.assertLess(dev, 1e-4, f"{key} drifted by {dev}")

        status, messages = tm.check_sheared_local_transforms()
        self.assertTrue(status, f"check still fails after flatten: {messages}")

    def test_flatten_takes_the_whole_chain(self):
        """One flagged joint must flatten its ENTIRE chain, not just itself.

        Sub-tolerance members of a flagged chain each leak up to the
        tolerance in dropped shear, and 20+ of them compound to visible
        drift -- the mixed chains the first production fix shipped. Here
        only sp2 trips the tolerance (25 deg against its parent); sp3..sp6
        turn 1 deg each and stay under it, but must be flattened with sp2.
        """
        rig = cmds.group(empty=True, name="rig_GRP")
        parent = rig
        joints = []
        for i, turn in enumerate((10, 25, 1, 1, 1, 1)):
            cmds.select(parent)
            j = cmds.joint(name=f"cc_jnt_{i + 1}")
            cmds.setAttr(f"{j}.translateX", 0 if i == 0 else 5)
            cmds.setAttr(f"{j}.rotateZ", turn)
            cmds.setAttr(f"{j}.scale", 1.5, 0.8, 0.8)
            joints.append(j)
            parent = j
        cmds.select(clear=True)
        tm = self._manager([rig])

        before = self._worlds(joints, frames=(1,))
        ok, messages = tm.flatten_sheared_chains()
        self.assertTrue(ok, messages)

        for j in joints[1:]:
            self.assertEqual(
                cmds.listRelatives(j, parent=True)[0],
                "rig_GRP",
                f"{j} was left chained -- a flagged chain must flatten "
                "in full, sub-tolerance members included",
            )
        after = self._worlds(joints, frames=(1,))
        for key, m in before.items():
            dev = max(abs(a - b) for a, b in zip(m, after[key]))
            self.assertLess(dev, 1e-4, f"{key} drifted by {dev}")


class TestShearedLocalTransformCheck(unittest.TestCase):
    """A squash/stretch joint chain loses shape through an FBX/glTF export.

    Reported from a production GLB: a wire loom's tube pulled away from the
    plug it is anchored to.  Every joint's WORLD matrix was perfectly
    orthogonal, so nothing looked wrong -- but each joint carried the same
    non-uniform scale (an offsetParentMatrix cancels the chain cascade), and
    the LOCAL matrix between two differently-oriented joints is then
    ``S . R_child . R_parent^-1 . S^-1``, which is sheared.  FBX and glTF
    store animated nodes as TRS, so the shear was dropped and the residual
    compounded down the chain: 47% stretch put the last joint 7.5 cm out --
    four times its true distance from the plug.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from maya import standalone

            try:
                standalone.initialize(name="python")
            except (RuntimeError, TypeError):
                pass
            cls.maya_available = True
        except ImportError:
            cls.maya_available = False

    def setUp(self):
        if not self.maya_available:
            self.skipTest("Maya not available")
        cmds.file(new=True, force=True)

    def tearDown(self):
        cmds.file(new=True, force=True)

    def _manager(self, objects):
        import logging

        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        tm = TaskManager.__new__(TaskManager)
        tm.objects = objects
        tm._live_objects = lambda: tm.objects
        tm.logger = logging.getLogger("test_sheared")
        return tm

    def _chain(self, scale, turn=25.0):
        """A joint chain whose joints share *scale* but differ in orientation.

        Built with ``cmds.joint``, not ``createNode``: only the former wires
        ``inverseScale`` so segmentScaleCompensate actually cancels the
        parent's scale. That cancellation is the whole point -- it is what
        keeps every joint's WORLD scale identical and pushes the discrepancy
        into the LOCAL matrices, reproducing what an offsetParentMatrix rig
        does. A createNode chain shears nowhere and silently passes.
        """
        root = cmds.group(empty=True, name="rig_GRP")
        parent = root
        joints = []
        for i in range(6):
            cmds.select(parent)
            j = cmds.joint(name=f"chain_jnt_{i + 1}")
            cmds.setAttr(f"{j}.translateX", 0 if i == 0 else 5)
            cmds.setAttr(f"{j}.rotateZ", turn)  # each joint turns => shear
            cmds.setAttr(f"{j}.scale", *scale)
            joints.append(j)
            parent = j
        cmds.select(clear=True)
        return root, joints

    def test_uniform_scale_chain_passes(self):
        """A chain with uniform scale has no shear to lose."""
        root, _ = self._chain((1.0, 1.0, 1.0))
        status, messages = self._manager([root]).check_sheared_local_transforms()
        self.assertTrue(status, messages)

    def test_non_uniform_scale_chain_is_flagged(self):
        """Non-uniform scale + differing orientation => sheared local matrix."""
        root, _ = self._chain((1.5, 0.8, 0.8))
        status, messages = self._manager([root]).check_sheared_local_transforms()
        self.assertFalse(status, "sheared chain passed the check")
        self.assertTrue(any("rig_GRP" in m for m in messages), messages)

    def test_world_matrices_stay_orthogonal(self):
        """The premise: world matrices look clean; only the locals shear.

        Guards against anyone 'simplifying' the check to test world matrices,
        which finds nothing on exactly the rigs that need catching.
        """
        import math

        root, joints = self._chain((1.5, 0.8, 0.8))
        for j in joints:
            m = cmds.xform(j, query=True, worldSpace=True, matrix=True)
            axes = [m[0:3], m[4:7], m[8:11]]
            lengths = [math.sqrt(sum(v * v for v in a)) for a in axes]
            unit = [[v / ln for v in a] for a, ln in zip(axes, lengths)]
            for a, b in ((0, 1), (0, 2), (1, 2)):
                dot = sum(x * y for x, y in zip(unit[a], unit[b]))
                self.assertAlmostEqual(dot, 0.0, places=5)
        status, _ = self._manager([root]).check_sheared_local_transforms()
        self.assertFalse(status)

    def test_shear_only_away_from_current_frame_is_caught(self):
        """The check samples the keyed range, not just the current frame.

        A stretch rig sits unsheared at rest and shears everywhere else; a
        single-frame scan passes the scene and the export ships it broken.
        """
        root, joints = self._chain((1.0, 1.0, 1.0))
        for j in joints:
            cmds.setKeyframe(j, attribute=["scaleX", "scaleY", "scaleZ"], time=1)
            cmds.setKeyframe(j, attribute="scaleX", time=30, value=1.5)
            cmds.setKeyframe(j, attribute="scaleY", time=30, value=0.8)
            cmds.setKeyframe(j, attribute="scaleZ", time=30, value=0.8)
        cmds.currentTime(1)

        status, messages = self._manager([root]).check_sheared_local_transforms()

        self.assertFalse(status, "shear away from the current frame went unseen")
        self.assertTrue(any("rig_GRP" in m for m in messages), messages)

    def test_tolerance_zero_skips(self):
        root, _ = self._chain((1.5, 0.8, 0.8))
        status, messages = self._manager([root]).check_sheared_local_transforms(
            tolerance=0
        )
        self.assertTrue(status)
        self.assertEqual(messages, [])


class TestBakeRangeModes(MayaTkTestCase):
    """The Bake Range dial -- the one task that owns the FBX bake range.

    It used to share the range with ``apply_declared_takes``, which set a shot
    union as an undeclared side effect of SPLITTING: clamping an export to its
    shots meant arming a take split you might not want, and which of the two
    won was decided by TASK_ORDER rather than by anything visible in the panel.
    """

    def setUp(self):
        super().setUp()
        import maya.mel as mel
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager
        from mayatk.anim_utils.shots._shots import ShotStore
        from mayatk.env_utils.fbx_utils import FbxUtils

        FbxUtils.reset_takes()
        ShotStore.clear_active()
        self.mel = mel
        self.tm = TaskManager(logging.getLogger("test_bake_range"))

        self.group = cmds.group(empty=True, name="br_root")
        cube = cmds.polyCube(name="br_child")[0]
        cmds.parent(cube, self.group)
        self.child = f"{self.group}|{cube}"
        cmds.setKeyframe(f"{self.child}.translateX", t=10, v=0)
        cmds.setKeyframe(f"{self.child}.translateX", t=200, v=5)
        self.tm.objects = cmds.ls(self.group, long=True)

        mel.eval("FBXExportBakeComplexAnimation -v true")
        self._set_range(1, 48)

    def tearDown(self):
        from mayatk.anim_utils.shots._shots import ShotStore
        from mayatk.env_utils.fbx_utils import FbxUtils

        FbxUtils.reset_takes()
        ShotStore.clear_active()
        super().tearDown()

    # -- helpers ------------------------------------------------------------
    def _set_range(self, start, end):
        self.mel.eval(f"FBXExportBakeComplexStart -v {start}")
        self.mel.eval(f"FBXExportBakeComplexEnd -v {end}")

    def _range(self):
        return (
            self.mel.eval("FBXExportBakeComplexStart -q"),
            self.mel.eval("FBXExportBakeComplexEnd -q"),
        )

    def _declare_shots(self, *spans):
        from mayatk.anim_utils.shots._shots import ShotStore

        store = ShotStore()
        ShotStore.set_active(store)
        for i, (start, end) in enumerate(spans):
            store.define_shot(f"Shot_{i}", start, end)
        return store

    # -- the modes ----------------------------------------------------------
    def test_auto_clamps_to_the_shot_union(self):
        """The point of the whole change: shots authored inside a longer
        timeline must not ship the frames outside them.

        Keys span 10-200; the shots span 20-120. The old panel could only get
        this by arming a take SPLIT -- which a GLB deliverable never wants,
        since its clips are rebuilt from the whole-timeline stack anyway.
        """
        self._declare_shots((20, 60), (80, 120))

        self.tm.set_bake_animation_range("auto")

        self.assertEqual(self._range(), (20, 120))

    def test_auto_falls_back_to_the_keyframe_extent_without_shots(self):
        """A shotless scene must not be left on the preset's range.

        Skipping is not "no range" -- it is whatever the preset carries, and
        the plugin's factory value is 1-48, which is not the scene's anything.
        That fallback is what makes Auto safe as the default row.
        """
        self.tm.set_bake_animation_range("auto")

        self.assertEqual(self._range(), (10, 200))

    def test_keys_measures_the_keyframe_extent(self):
        self._declare_shots((20, 60))
        self.tm.set_bake_animation_range("keys")
        self.assertEqual(self._range(), (10, 200))

    def test_scene_reads_the_authored_range_not_the_slider(self):
        """``animationStartTime``/``animationEndTime``, never ``minTime``/
        ``maxTime`` -- the slider is where the artist happened to scrub."""
        cmds.playbackOptions(animationStartTime=5, animationEndTime=310)
        cmds.playbackOptions(minTime=100, maxTime=110)

        self.tm.set_bake_animation_range("scene")

        self.assertEqual(self._range(), (5, 310))

    def test_off_keeps_the_preset_range(self):
        self.tm.set_bake_animation_range(None)
        self.assertEqual(self._range(), (1, 48))

    def test_the_restore_puts_back_the_range_from_before_the_takes(self):
        """With the auto-export hook installed (any session producer or
        stager), its after-export ``reset_takes`` runs DURING the write and
        consumes the pre-takes capture -- and the range restore, captured
        after ``apply_takes`` had set the union, wrote the union back for
        every later export in the session (restore-point audit, 2026-09-24).
        """
        from mayatk.env_utils.fbx_utils import FbxUtils

        self._declare_shots((20, 60), (80, 120))
        self.tm.apply_declared_takes("both")
        self.tm.set_bake_animation_range("auto")
        FbxUtils.reset_takes()  # the hook's after-export, mid-write

        self.tm.run_deferred_restores()

        self.assertEqual(self._range(), (1, 48))

    def test_declared_takes_load_the_fbx_plugin_they_query(self):
        """A USD route applies no FBX options, so nothing may have loaded
        fbxmaya before the takes task stages the bake range's restore -- and
        that capture queries fbxmaya's own command, which raised "Cannot find
        procedure" and aborted the export (review, 2026-09-26)."""
        if cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            cmds.unloadPlugin("fbxmaya", force=True)
        self.addCleanup(cmds.loadPlugin, "fbxmaya", quiet=True)

        self.tm.apply_declared_takes("both")

        self.assertTrue(cmds.pluginInfo("fbxmaya", query=True, loaded=True))

    def test_legacy_true_reads_as_the_keyframe_extent(self):
        """A headless caller's pre-combo bool keeps doing what it did."""
        self._declare_shots((20, 60))
        self.tm.set_bake_animation_range(True)
        self.assertEqual(self._range(), (10, 200))

    # -- the published clip origin -----------------------------------------
    #
    # The origin is a DIFFERENT number from the range, and conflating the two
    # shipped a production assembly whose every shot played the tail of the
    # shot before it. The bake range bounds what the plugin RE-BAKES; an
    # authored curve is written whole (measured on Maya 2025 / FBX 2020.3.6:
    # a curve keyed 0-100 exports as 0-100 under a 20-80 range, with
    # FBXExportBakeResampleAnimation off AND on). So the stack carries the
    # KEY extent, and that is what every GLB clip has to be cut against.

    def _published_origin(self):
        """The clip span the run's publish hands the producers (the export
        context's ``clip_span``, the ``*`` origin every GLB clip is cut
        against), captured instead of published."""
        from mayatk.env_utils.fbx_utils import FbxUtils

        seen = {}

        def capture(ctx=None, only=None):
            seen["span"] = ctx.clip_span if ctx is not None else None

        patcher = patch.object(FbxUtils, "publish", staticmethod(capture))
        patcher.start()
        self.addCleanup(patcher.stop)
        return seen

    def test_clip_origin_is_the_key_extent_not_the_bake_range(self):
        """Auto clamps the RANGE to the shots but the stack still carries 10-200.

        Publishing 20-120 as the origin would slide every clip cut from that
        stack by 10 frames -- the exact defect measured on the PROPS assembly,
        where a stack carrying 80-4281 was published as 161-4275 and all 18
        shots played 81 frames early.
        """
        self._declare_shots((20, 60), (80, 120))
        self.tm.set_bake_animation_range("auto")
        seen = self._published_origin()

        self.tm._publish_scene_records()

        self.assertEqual(self._range(), (20, 120))  # the range still clamps
        self.assertEqual(seen.get("span"), (10, 200))  # the origin does not

    def test_the_range_task_does_not_publish_the_origin(self):
        """The range task owns the RANGE. Nothing else -- and that is the fix.

        It used to publish the origin too, on the reasoning that it runs last
        in TASK_ORDER. It does; but the export BRACKET re-ran every producer
        after the last task, and the visibility producer republished the whole
        channel, so the value never survived to the write. Three PROPS exports
        shipped 18 shots cut 81 frames early while logging the right number.
        The origin is now an INPUT: ``export_data_node`` measures it and hands
        it to the producers as the export context's ``clip_span``.
        """
        self._declare_shots((20, 60), (80, 120))
        seen = self._published_origin()

        self.tm.set_bake_animation_range("auto")

        self.assertEqual(self._range(), (20, 120))
        self.assertIsNone(
            seen.get("span"),
            "the range task must not publish the origin -- export_data_node "
            "hands it to the producers as the export context's clip_span",
        )

    def test_clip_origin_is_published_when_baking_is_disabled(self):
        """No bake still means a stack: the curves ship as authored."""
        self.mel.eval("FBXExportBakeComplexAnimation -v false")
        seen = self._published_origin()

        self.tm._publish_scene_records()

        self.assertEqual(seen.get("span"), (10, 200))

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            self.tm.set_bake_animation_range("widest")

    def test_skipped_when_baking_is_disabled(self):
        """With no bake there is no single range to name, so the task stands off.

        The baseline is read AFTER the flag is cleared, not before: measured on
        Maya 2025, ``FBXExportBakeComplexAnimation -v false`` re-derives the
        stored range from the scene on its own (1-48 became 1-200, the new
        scene's animation range). Asserting against the pre-flag value would
        credit this task with the plugin's own edit.
        """
        self.mel.eval("FBXExportBakeComplexAnimation -v false")
        self.addCleanup(self.mel.eval, "FBXExportBakeComplexAnimation -v true")
        untouched = self._range()

        self.tm.set_bake_animation_range("keys")

        self.assertEqual(self._range(), untouched)

    # -- the widen rule -----------------------------------------------------
    def test_every_mode_widens_to_cover_a_realized_take(self):
        """A shot may outrun the last keyframe -- a hold authored on the
        sequencer -- and a raw override would then write a range that CLIPS a
        clip the same export declared. Metadata describing animation the file
        does not contain is wrong in the FBX and in the GLB converted from it
        at once, so no mode is allowed to produce it.
        """
        self.tm._required_range_coverage = (5, 260)

        self.tm.set_bake_animation_range("keys")

        self.assertEqual(self._range(), (5, 260))

    def test_widen_never_narrows_a_wider_source(self):
        self.tm._required_range_coverage = (50, 60)
        self.tm.set_bake_animation_range("keys")
        self.assertEqual(self._range(), (10, 200))

    def test_coverage_claims_union_rather_than_overwrite(self):
        """Several tasks can claim a span; the range must cover all of them.

        The seam exists so a task that stages animation the write has to carry
        registers a claim instead of writing the range itself -- and so a
        future claimant needs no edit to the range task.
        """
        self.tm._require_range_coverage(100, 150)
        self.tm._require_range_coverage(40, 120)

        self.assertEqual(self.tm._required_range_coverage, (40, 150))

        self.tm.set_bake_animation_range("keys")
        self.assertEqual(self._range(), (10, 200))  # source already covers it

    def test_realized_range_is_cleared_per_run(self):
        """Left standing, a run with no takes would widen its range to cover
        the PREVIOUS export's shots."""
        self.tm._required_range_coverage = (5, 260)
        self.tm.begin_run(self.tm.run)  # the per-run reset
        self.assertIsNone(self.tm._required_range_coverage)

    # -- ordering + restore -------------------------------------------------
    def test_the_range_task_runs_after_the_split(self):
        """It can only honor "never clip a declared take" once the takes exist."""
        order = self.tm.TASK_ORDER
        self.assertLess(
            order.index("apply_declared_takes"),
            order.index("set_bake_animation_range"),
        )

    def test_the_prior_range_is_restored_after_the_write(self):
        """The range is sticky global exporter state. Without this, one export
        left its measurement armed for every later export in the session --
        including hand-driven ones through Maya's own dialog."""
        self.tm.set_bake_animation_range("keys")
        self.assertEqual(self._range(), (10, 200))

        self.tm.run_deferred_restores()

        self.assertEqual(self._range(), (1, 48))

    # -- the widget contract ------------------------------------------------
    def test_the_combo_defaults_to_auto_and_keeps_off_at_index_zero(self):
        """Templates persist combos by INDEX, so row 0 is a contract."""
        spec = self.tm.task_definitions["set_bake_animation_range"]
        rows = list(self.tm._bake_range_options.items())

        self.assertEqual(spec["widget_type"], "ComboBox")
        self.assertEqual(rows[0], ("OFF", None))
        self.assertEqual(rows[spec["setCurrentIndex"]][1], "auto")

    def test_the_combo_takes_a_fresh_object_name(self):
        """A template saved before the merge carries a BOOL under the old name.
        Restored onto a combo it would select index 1 -- a mode nobody chose --
        so the old name must not resolve to this widget at all.
        """
        spec = self.tm.task_definitions["set_bake_animation_range"]
        self.assertEqual(spec["object_name"], "bake_range")

    def test_every_offered_mode_is_one_the_task_accepts(self):
        offered = [v for v in self.tm._bake_range_options.values() if v]
        self.assertEqual(sorted(offered), sorted(self.tm.BAKE_RANGE_MODES))


class TestOptimizeKeysLevels(MayaTkTestCase):
    """The Optimize Keys dial -- the pass and its aggressiveness in one combo."""

    def setUp(self):
        super().setUp()
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        self.tm = TaskManager(logging.getLogger("test_optimize_level"))
        self.cube = cmds.polyCube(name="ok_cube")[0]
        # translateX carries real motion; translateY is authored but STATIC --
        # keyed at the attribute's DEFAULT value, which is what makes it safe
        # to delete: AnimUtils.get_static_curves deliberately KEEPS a constant
        # curve holding a non-default value, because dropping it would change
        # the object's resting pose (a constraint-baked constant position
        # would snap back to zero).
        cmds.setKeyframe(f"{self.cube}.translateX", t=1, v=0)
        cmds.setKeyframe(f"{self.cube}.translateX", t=10, v=5)
        cmds.setKeyframe(f"{self.cube}.translateX", t=20, v=5)
        cmds.setKeyframe(f"{self.cube}.translateX", t=30, v=5)
        cmds.setKeyframe(f"{self.cube}.translateX", t=40, v=9)
        for t in (1, 20, 40):
            cmds.setKeyframe(f"{self.cube}.translateY", t=t, v=0)
        self.tm.objects = cmds.ls(self.cube, long=True)

    def _keys(self, attr):
        return cmds.keyframe(f"{self.cube}.{attr}", q=True, keyframeCount=True) or 0

    def test_static_only_drops_the_static_curve_and_keeps_every_flat_key(self):
        """The conservative rung the panel had no way to ask for: a hand-animated
        curve's flat section can be a deliberate hold."""
        self.tm.optimize_keys("static")

        self.assertEqual(self._keys("translateY"), 0)
        self.assertEqual(self._keys("translateX"), 5)

    def test_flat_also_drops_the_redundant_interior_key(self):
        """The old checked box's behavior, now an explicit row."""
        self.tm.optimize_keys("flat")

        self.assertEqual(self._keys("translateY"), 0)
        self.assertEqual(self._keys("translateX"), 4)  # the t=20 hold interior

    def test_off_touches_nothing(self):
        self.tm.optimize_keys(None)
        self.assertEqual(self._keys("translateY"), 3)
        self.assertEqual(self._keys("translateX"), 5)

    def test_legacy_true_is_the_default_level(self):
        from mayatk.anim_utils._anim_utils import AnimUtils

        self.tm.optimize_keys(True)
        self.assertEqual(AnimUtils.DEFAULT_OPTIMIZE_LEVEL, "flat")
        self.assertEqual(self._keys("translateX"), 4)

    def test_unknown_level_raises(self):
        with self.assertRaises(ValueError):
            self.tm.optimize_keys("aggressive")

    def test_the_combo_defaults_to_the_old_checkbox_behavior(self):
        spec = self.tm.task_definitions["optimize_keys"]
        rows = list(self.tm._optimize_keys_options.items())

        self.assertEqual(spec["widget_type"], "ComboBox")
        self.assertEqual(spec["object_name"], "optimize_level")
        self.assertEqual(rows[0], ("OFF", None))
        self.assertEqual(rows[spec["setCurrentIndex"]][1], "flat")

    def test_every_offered_level_is_one_AnimUtils_knows(self):
        from mayatk.anim_utils._anim_utils import AnimUtils

        offered = [v for v in self.tm._optimize_keys_options.values() if v]
        self.assertEqual(sorted(offered), sorted(AnimUtils.OPTIMIZE_LEVELS))


class TestCheckOutputWritable(unittest.TestCase):
    """The pre-flight that keeps a held-open deliverable from costing a run.

    The export writes its file LAST, so a viewer holding the destination open
    used to surface minutes later as ``[WinError 32]`` -- reported, worse, as
    "Failed to export objects" over objects that had exported fine.
    """

    def setUp(self):
        import logging
        import tempfile

        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        self.tm = TaskManager(logging.getLogger("test_output_writable"))
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.fbx = os.path.join(self.dir, "asset.fbx")
        self.glb = os.path.join(self.dir, "asset.glb")
        self.tm.run = self.tm.run.replace(export_path=self.fbx)

    def _write(self, path):
        with open(path, "wb") as fh:
            fh.write(b"payload")
        return path

    def _hold(self, path):
        """Hold *path* the way a viewer does; released at teardown."""
        import ctypes
        from ctypes import wintypes

        create = ctypes.windll.kernel32.CreateFileW
        create.restype = wintypes.HANDLE
        create.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        # GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING.
        handle = create(path, 0x80000000, 0x00000001, None, 3, 0, None)
        self.assertNotEqual(handle, wintypes.HANDLE(-1).value, "could not hold it")
        self.addCleanup(ctypes.windll.kernel32.CloseHandle, wintypes.HANDLE(handle))

    # -- _deliverable_paths ----------------------------------------------

    def test_fbx_only_writes_just_the_fbx(self):
        self.assertEqual(self.tm._deliverable_paths(), [self.fbx])

    def test_fbx_plus_glb_writes_both(self):
        self.tm.run = self.tm.run.replace(output_format="fbx_glb")
        self.assertEqual(self.tm._deliverable_paths(), [self.fbx, self.glb])

    def test_glb_only_writes_just_the_glb(self):
        """Its FBX goes to a temp dir, so the FBX path is not a destination."""
        self.tm.run = self.tm.run.replace(output_format="glb")
        self.assertEqual(self.tm._deliverable_paths(), [self.glb])

    def test_no_export_path_has_no_destinations(self):
        self.tm.run = self.tm.run.replace(export_path="")
        self.assertEqual(self.tm._deliverable_paths(), [])

    # -- check_output_writable -------------------------------------------

    def test_passes_when_the_destination_does_not_exist_yet(self):
        """A first export has nothing to replace, so nothing can hold it."""
        status, msgs = self.tm.check_output_writable()
        self.assertTrue(status)
        self.assertEqual(msgs, [])

    def test_passes_when_the_destination_exists_and_is_free(self):
        self._write(self.fbx)
        self.assertTrue(self.tm.check_output_writable()[0])

    @unittest.skipUnless(os.name == "nt", "file locking is a Windows behavior")
    def test_fails_and_names_the_held_deliverable(self):
        self._write(self.fbx)
        self._hold(self.fbx)

        status, msgs = self.tm.check_output_writable()
        self.assertFalse(status, "a held destination must fail the export")
        self.assertTrue(any("asset.fbx" in m for m in msgs), msgs)
        self.assertTrue(any("in use by" in m for m in msgs), msgs)

    @unittest.skipUnless(os.name == "nt", "file locking is a Windows behavior")
    def test_a_held_glb_is_caught_only_when_a_glb_will_be_written(self):
        """The exact production failure: the preview held the .glb open.

        And its converse -- an FBX-only run must not fail over a .glb it is
        never going to touch.
        """
        self._write(self.fbx)
        self._write(self.glb)
        self._hold(self.glb)

        self.assertTrue(
            self.tm.check_output_writable()[0],
            "an FBX-only run must ignore a .glb it does not write",
        )

        self.tm.run = self.tm.run.replace(output_format="fbx_glb")
        status, msgs = self.tm.check_output_writable()
        self.assertFalse(status)
        self.assertTrue(any("asset.glb" in m for m in msgs), msgs)

    def test_it_is_scheduled_before_every_task(self):
        """Declaring no dependencies is what makes it fail FAST.

        Asserted on the ORDER the scheduler actually produces, not just on the
        declaration: a dependency here would let the pipeline run first, which
        is the entire cost this check exists to avoid.
        """
        self.assertEqual(self.tm.CHECK_DEPENDENCIES["check_output_writable"], ())

        tasks = {
            "set_workspace": True,
            "smart_bake": True,
            "optimize_textures": True,
            "convert_textures": "glTF 2.0",
        }
        checks = {
            "check_output_writable": True,
            "check_path_length": 4096,
            "check_valid_paths": True,
        }
        order = list(self.tm._schedule(tasks, checks).keys())
        cutoff = order.index("check_output_writable")
        self.assertTrue(
            all(name.startswith("check_") for name in order[:cutoff]),
            f"a task runs before the writability gate: {order}",
        )
        for task in tasks:
            self.assertGreater(
                order.index(task), cutoff, f"{task} runs before the gate: {order}"
            )

    def test_the_panel_offers_it(self):
        self.assertIn("check_output_writable", self.tm.check_definitions)

    def test_an_older_pythontk_skips_the_check_instead_of_aborting(self):
        """mayatk and pythontk update independently.

        Measured in mayapy against the INSTALLED pythontk: without this the
        check raises AttributeError, which aborts the very export it exists to
        protect. Skipping loses the gate, not the deliverable.
        """

        self._write(self.fbx)
        self._hold(self.fbx) if os.name == "nt" else None

        with patch.object(ptk.FileUtils, "describe_lock", None, create=True):
            del_target = ptk.FileUtils.describe_lock
            self.assertIsNone(del_target)
            status, msgs = self.tm.check_output_writable()

        self.assertTrue(status, "a missing primitive must not fail the export")
        self.assertEqual(msgs, [])


class TestRegistryDerivedCombosPersistByValue(unittest.TestCase):
    """A combo built from an upstream registry must not persist by INDEX.

    ``texture_file_type`` and ``optimize_textures`` are built from pythontk's
    container/format registry. Templates persist a combo by index, so inserting
    a format upstream shifts every index after it and a template that stored
    "JPG" silently starts selecting its neighbour after a pythontk upgrade --
    with no warning, because the uncovered-keys check cannot see it: the KEY is
    still covered, only its meaning moved.

    ``restore_by = "text"`` is the fix the FBX Preset combo already proved in
    this same file, and ``StateManager._legacy_combo_index`` migrates the
    indices already on disk. This pins the declaration so the opt-in cannot be
    dropped when the rows are next edited.
    """

    def _defs(self):
        from mayatk.env_utils.scene_exporter.task_manager import TaskManager

        return TaskManager(MagicMock()).task_definitions

    def test_the_registry_derived_combos_declare_value_persistence(self):
        defs = self._defs()
        for key in ("texture_file_type", "optimize_textures"):
            with self.subTest(row=key):
                self.assertEqual(
                    defs[key].get("restore_by"),
                    "text",
                    f"{key} is registry-derived; an index would drift upstream",
                )

    def test_the_declaration_actually_reaches_the_widget(self):
        """The declaration is worthless if the widget factory drops it.

        ``_make_definition_widget`` strips ``_DEFINITION_META_KEYS`` before
        handing the rest to ``set_attributes``; a key added to the wrong side of
        that split is silently discarded, and a test that only inspects the
        definition dict would still pass. This drives the real factory and
        asserts the key arrives at the setter.
        """
        from types import SimpleNamespace

        from mayatk.env_utils.scene_exporter.scene_exporter_slots import (
            SceneExporterSlots,
        )

        class _Stub:
            def __init__(self, **kwargs):
                self.attrs = dict(kwargs)

        slots = SceneExporterSlots.__new__(SceneExporterSlots)
        slots.sb = SimpleNamespace(
            QtWidgets=SimpleNamespace(QCheckBox=_Stub),
            registered_widgets=SimpleNamespace(ComboBox=_Stub, SpinBox=_Stub),
            convert_to_legal_name=lambda n: n,
        )
        slots.ui = SimpleNamespace(set_attributes=lambda w, **kw: w.attrs.update(kw))

        defs = self._defs()
        for key in ("texture_file_type", "optimize_textures"):
            with self.subTest(row=key):
                widget = slots._make_definition_widget(key, defs[key])
                self.assertEqual(
                    widget.attrs.get("restore_by"),
                    "text",
                    f"{key}: restore_by never reached set_attributes",
                )

    def test_static_combos_are_left_on_index_persistence(self):
        """The opt-in is deliberate, not blanket.

        A row whose items are a fixed literal list has a stable index, so
        switching it would be churn with a migration cost and no benefit. If a
        row here ever becomes registry-derived, this is the test that should
        fail and send someone to add the opt-in.
        """
        defs = self._defs()
        for key in ("export_visible_objects", "set_linear_unit", "optimize_keys"):
            with self.subTest(row=key):
                self.assertIsNone(defs[key].get("restore_by"))


if __name__ == "__main__":
    unittest.main()
