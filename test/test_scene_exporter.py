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
from unittest.mock import patch
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
from mayatk.env_utils.scene_exporter._scene_exporter import (
    SceneExporter,
    SceneExporterSlots,
)
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

    # ------------------------------------------------------------------
    # Export path generation
    # ------------------------------------------------------------------

    def test_generate_export_path(self):
        """Test export path generation."""
        self.exporter.export_dir = self.temp_dir
        self.exporter.output_name = None
        self.exporter.name_regex = None
        self.exporter.timestamp = False

        scene_path = os.path.join(self.temp_dir, "test_scene.ma")
        _pm_rename_file(scene_path)

        path = self.exporter.generate_export_path()
        self.assertTrue(path.endswith("test_scene.fbx"))

        self.exporter.output_name = "CustomName"
        path = self.exporter.generate_export_path()
        self.assertTrue(path.endswith("CustomName.fbx"))

        self.exporter.timestamp = True
        path = self.exporter.generate_export_path()
        self.assertRegex(path, r"CustomName_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.fbx")

    def test_generate_export_path_wildcard(self):
        """Test export path generation with wildcard.

        Verify that using wildcards in output_name finds existing files to overwrite.
        """
        self.exporter.export_dir = self.temp_dir
        self.exporter.timestamp = False
        self.exporter.name_regex = None

        existing_file = os.path.join(self.temp_dir, "existing_file_v001.fbx")
        with open(existing_file, "w") as f:
            f.write("dummy")

        self.exporter.output_name = "existing_file_*"
        path = self.exporter.generate_export_path()
        self.assertEqual(os.path.normpath(path), os.path.normpath(existing_file))

        latest_file = os.path.join(self.temp_dir, "existing_file_v002.fbx")
        with open(latest_file, "w") as f:
            f.write("dummy")

        path = self.exporter.generate_export_path()
        self.assertEqual(os.path.normpath(path), os.path.normpath(latest_file))

    def test_format_export_name_regex(self):
        """Test regex name formatting."""
        self.exporter.name_regex = "test_->prod_"
        result = self.exporter.format_export_name("test_scene")
        self.assertEqual(result, "prod_scene")

        self.exporter.name_regex = "scene|asset"
        result = self.exporter.format_export_name("test_scene")
        self.assertEqual(result, "test_asset")

    # ------------------------------------------------------------------
    # Export execution
    # ------------------------------------------------------------------

    def test_perform_export_basic(self):
        """Test basic export execution."""
        try:
            if not cmds.pluginInfo("fbxmaya", q=True, loaded=True):
                cmds.loadPlugin("fbxmaya")
        except:
            print("FBX plugin not available, skipping actual export call")
            return

        result = self.exporter.perform_export(
            export_dir=self.temp_dir,
            objects=[self.cube],
            file_format="FBX export",
        )
        self.assertIsNotNone(result)

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

    def test_env_separator_removed(self):
        """Verify the Environment separator section is removed from task_definitions.

        The sep_env separator was the only entry in the Environment section
        and should have been removed with delete_env_nodes.
        Fixed: 2026-03-04
        """
        defs = self.exporter.task_manager.task_definitions
        self.assertNotIn("sep_env", defs, "sep_env separator should be removed")

    # ------------------------------------------------------------------
    # optimize_keys forwarding to SmartBake
    # ------------------------------------------------------------------

    def test_optimize_keys_task_runs_when_requested(self):
        """The optimize_keys task is dispatched when present+True in the task dict.

        The shots migration to pythontk's TaskFactory dropped the old
        ``_optimize_keys_enabled`` proxy flag; tasks are now dispatched by name
        (``TaskFactory._manage_context`` -> ``getattr(self, name)()``), so the
        current, observable contract is that the ``optimize_keys`` method is
        invoked. (The flag existed because on a keyframe-less object the task
        early-returns, leaving no effect to assert.)
        """
        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        calls = []
        tm.optimize_keys = lambda *a, **k: calls.append(True)
        tm.run_tasks({"optimize_keys": True})
        self.assertTrue(calls, "optimize_keys task should run when present and True")

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
    # convert_to_relative_paths — copy externals into sourceimages first
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

    def test_convert_to_relative_copies_external_textures(self):
        """External textures must be copied into sourceimages before remap.

        Bug: convert_to_relative_paths rewrote an absolute external path to a
        project-relative one without first copying the file in, so the
        relative path pointed at a file that wasn't there — breaking the link.
        Added: 2026-06-16
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

        # File was copied into sourceimages ...
        copied = os.path.join(sourceimages, "wood_ext.png")
        self.assertTrue(
            os.path.isfile(copied),
            "external texture should be copied into sourceimages",
        )
        # ... and the node's (now relative) path resolves to a real file.
        new_path = cmds.getAttr(f"{file_node}.fileTextureName")
        resolved = (
            new_path
            if os.path.isabs(new_path)
            else os.path.join(self.temp_dir, new_path)
        )
        self.assertTrue(
            os.path.isfile(resolved),
            f"converted path '{new_path}' must resolve to an existing file",
        )

    def test_convert_to_relative_does_not_clobber_name_collision(self):
        """A different texture with the same basename in sourceimages is kept.

        Same-name + different-size is a collision: copying would overwrite a
        different texture (and silently rebind other materials to the wrong
        file).  The existing sourceimages file must be left untouched.
        Added: 2026-06-16
        """
        sourceimages = self._set_project(self.temp_dir)

        # Pre-existing, DIFFERENT texture already in sourceimages.
        existing = os.path.join(sourceimages, "shared.png")
        with open(existing, "wb") as f:
            f.write(b"ORIGINAL-SOURCEIMAGES-CONTENT")

        external_dir = os.path.join(self.temp_dir, "external")
        os.makedirs(external_dir, exist_ok=True)
        external_tex = os.path.join(external_dir, "shared.png")
        with open(external_tex, "wb") as f:
            f.write(b"DIFFERENT")  # different size → collision

        self._assign_texture(self.cube, external_tex)
        self.exporter.task_manager.objects = [cmds.ls(str(self.cube), l=True)[0]]
        self.exporter.task_manager.convert_to_relative_paths()

        with open(existing, "rb") as f:
            self.assertEqual(
                f.read(),
                b"ORIGINAL-SOURCEIMAGES-CONTENT",
                "name collision must not overwrite the existing sourceimages texture",
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
        self.assertFalse(
            passed, "checkbox-True must use 0.5 tolerance, so -0.75 fails"
        )
        # The header reports the effective tolerance used.
        self.assertTrue(any("0.500" in m for m in messages))

    def test_below_floor_none_is_strict_zero(self):
        """An explicit None means a strict 0.0 tolerance (preserved contract).

        Added: 2026-06-19
        """
        cube_long = cmds.ls(str(self.cube), l=True)[0]
        ymin = cmds.xform(cube_long, query=True, ws=True, bb=True)[1]
        cmds.setAttr(f"{cube_long}.translateY", -0.1 - ymin)

        tm = self.exporter.task_manager
        tm.objects = [cube_long]

        passed, _ = tm.check_objects_below_floor(None)
        self.assertFalse(passed, "None → 0.0 tolerance, so any dip fails")

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
        write time, but ``TaskFactory``'s ``set_``/``revert_`` pair fires when
        ``run_tasks`` returns — *before* the write — so pairing this task made
        it inert. It uses ``TaskFactory.stage_deferred_restore`` instead.
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
            "mayatk.env_utils.scene_exporter.task_manager.MatUtils.reassign_duplicate_materials"
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
            "mayatk.env_utils.scene_exporter.task_manager.MatUtils.reassign_duplicate_materials"
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
        """Verify namespace stripping and leading pipe removal."""
        from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import (
            SceneDataSidecar,
        )

        objects = ["|ns:group|ns:child", "|group2|child2"]
        result = SceneDataSidecar.build_clean_path_set(objects)
        self.assertEqual(result, {"group|child", "group2|child2"})

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
        self.exporter.task_manager.export_path = os.path.join(self.temp_dir, "test.fbx")
        passed, messages = self.exporter.task_manager.check_hierarchy_vs_existing_fbx()
        self.assertTrue(passed)

    def test_hierarchy_check_falls_back_to_prev_backup(self):
        """A deleted manifest compares against its .prev backup instead of silently passing."""
        import json

        export_path = os.path.join(self.temp_dir, "test.fbx")
        prev_path = os.path.join(self.temp_dir, ".test.hierarchy.json.prev")

        previous = ["ExportGroup", "ExportGroup|Gone"]
        with open(prev_path, "w") as f:
            json.dump({"paths": previous, "object_count": len(previous)}, f)

        self.exporter.task_manager.objects = []
        self.exporter.task_manager.export_path = export_path

        passed, messages = self.exporter.task_manager.check_hierarchy_vs_existing_fbx()
        self.assertFalse(passed)
        self.assertTrue(any(".prev backup" in m for m in messages))

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
        self.exporter.task_manager.export_path = export_path
        current = sorted(self.exporter.task_manager._build_full_hierarchy_set())
        current.append("ExportGroup|ExtraNode")
        with open(manifest_path, "w") as f:
            json.dump({"paths": current, "object_count": len(current)}, f)

        passed, messages = self.exporter.task_manager.check_hierarchy_vs_existing_fbx()
        self.assertFalse(passed)
        self.assertTrue(any("missing" in m.lower() for m in messages))

    def test_hierarchy_check_writes_diff_report(self):
        """Verify sidecar .hierarchy_diff.txt is created on failure."""
        import json

        export_path = os.path.join(self.temp_dir, "test.fbx")
        manifest_path = os.path.join(self.temp_dir, ".test.hierarchy.json")
        diff_path = os.path.join(self.temp_dir, ".test.hierarchy_diff.txt")

        # Build manifest from actual hierarchy, then add a node that will be "missing"
        self.exporter.task_manager.objects = [
            cmds.ls(str(self.group), l=True)[0],
            cmds.ls(str(self.cube), l=True)[0],
        ]
        self.exporter.task_manager.export_path = export_path
        current = sorted(self.exporter.task_manager._build_full_hierarchy_set())
        current.append("ExportGroup|Gone")
        with open(manifest_path, "w") as f:
            json.dump({"paths": current, "object_count": len(current)}, f)

        self.exporter.task_manager.check_hierarchy_vs_existing_fbx()
        self.assertTrue(os.path.exists(diff_path))

        with open(diff_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("ExportGroup|Gone", content)

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
        self.exporter.task_manager.export_path = export_path
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
        self.exporter.task_manager.export_path = export_path

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
        self.exporter.task_manager.export_path = export_path
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
        self.exporter.task_manager.export_path = export_path

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

    def test_option_runs_before_takes_in_order(self):
        order = self.tm.TASK_ORDER
        self.assertIn("export_data_node", order)
        self.assertLess(
            order.index("export_data_node"), order.index("apply_declared_takes")
        )

    def test_includes_carrier_and_publishes_with_shots(self):
        from mayatk.anim_utils.shots._shots import ShotStore
        from mayatk.node_utils.data_nodes import DataNodes

        store = ShotStore()
        ShotStore.set_active(store)
        store.define_shot("Intro", 1, 50, description="opening")

        self.tm.export_data_node()

        self.assertNodeExists(DataNodes.EXPORT)
        self.assertTrue(any(o.endswith(DataNodes.EXPORT) for o in self.tm.objects))
        self.assertIn("opening", DataNodes.get_export_string(DataNodes.SHOT_METADATA))

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
        self.assertIn(
            "footstep", cmds.getAttr(f"{DataNodes.EXPORT}.audio_manifest")
        )

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

        DataNodes.set_export_string("test_channel", '{"version": 1}')
        self.tm.export_data_node()  # folds the carrier into the export set
        with tempfile.TemporaryDirectory() as d:
            self.tm.export_path = os.path.join(d, "dn.fbx")
            self.tm.write_scene_data_sidecar()
            data = SceneDataSidecar.read_data(self.tm.export_path)
            self.assertIsNotNone(data)
            self.assertEqual(data.get("test_channel"), {"version": 1})
            paths = SceneDataSidecar.read_manifest(self.tm.export_path)
            self.assertTrue(any("dnCube" in p for p in paths))

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

        DataNodes.set_export_string("test_channel", '{"version": 1}')
        with tempfile.TemporaryDirectory() as d:
            self.tm.export_path = os.path.join(d, "dn.fbx")
            self.tm.write_scene_data_sidecar()
            self.assertIsNone(SceneDataSidecar.read_manifest(self.tm.export_path))

    def test_no_sidecar_without_metadata_or_check(self):
        import tempfile
        from mayatk.env_utils.hierarchy_sync.scene_data_sidecar import (
            SceneDataSidecar,
        )

        with tempfile.TemporaryDirectory() as d:
            self.tm.export_path = os.path.join(d, "dn.fbx")
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
        """snap_keys_to_frame must invalidate _key_times before tie runs.

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
        # _has_keyframes gate populates _key_times with pre-snap times).
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

    def test_objects_setter_resets_hierarchy_check_marker(self):
        """One hierarchy-checked export must not leak baseline writes into
        later runs — the objects setter (per-run reseed) clears the marker."""
        self.tm._hierarchy_check_ran = True
        self.tm.objects = [self.cube_long]
        self.assertFalse(self.tm._hierarchy_check_ran)

    def test_run_tasks_sets_optimize_keys_flag_for_smart_bake(self):
        """run_tasks forwards the optimize_keys toggle to the flag smart_bake
        reads for its internal override-layer optimization (the UI documents
        that coupling; blendertk uses the same idiom)."""
        self.tm.objects = [self.cube_long]
        self.tm.run_tasks({"optimize_keys": True})
        self.assertTrue(self.tm._optimize_keys_enabled)
        self.tm.run_tasks({"set_linear_unit": "cm"})
        self.assertFalse(self.tm._optimize_keys_enabled)

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
    scratch/mangled node names (regression: VDATS_module.ma shipped shapes
    like 'vdat____Shape702__uninst_tmp____Shape' in scene_data.json)."""

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
        self._mangle_shape("vdatShape1__uninst_tmpShape380")
        ok, messages = self.tm.check_mangled_names()
        self.assertFalse(ok)
        self.assertTrue(any("uninst" in m for m in messages))

    def test_check_flags_underscore_run(self):
        self._mangle_shape("vdat____Shape702")
        ok, _ = self.tm.check_mangled_names()
        self.assertFalse(ok)

    def test_check_passes_clean_names(self):
        ok, messages = self.tm.check_mangled_names()
        self.assertTrue(ok, messages)

    def test_check_empty_export_set_passes(self):
        """No objects → pass, without falling back to the live selection."""
        self._mangle_shape("vdatShape1__uninst_tmpShape380")
        cmds.select(self.cube)  # a selection fallback would wrongly flag it
        self.tm.objects = []
        ok, messages = self.tm.check_mangled_names()
        self.assertTrue(ok, messages)

    def test_check_is_registered(self):
        self.assertIn("check_mangled_names", self.tm.check_definitions)

    def test_conform_task_repairs_shape(self):
        self._mangle_shape("vdat____Shape702__uninst_tmp____Shape")
        self.tm.conform_shape_names()
        leaf = cmds.listRelatives(self.cube, shapes=True)[0].split("|")[-1]
        self.assertEqual(leaf, "GuardCubeShape")
        ok, messages = self.tm.check_mangled_names()
        self.assertTrue(ok, messages)

    def test_conform_task_is_registered(self):
        self.assertIn("conform_shape_names", self.tm.task_definitions)


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

    def test_locator_check_survives_conform(self):
        self.tm.conform_shape_names()
        ok, messages = self.tm.check_duplicate_locator_names()
        self.assertTrue(ok, messages)

    def test_export_selection_survives_conform(self):
        self.tm.conform_shape_names()
        cmds.select(self.tm.objects, replace=True)
        self.assertEqual(len(cmds.ls(selection=True)), 3)

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
        for check in (
            self.tm.check_duplicate_locator_names,
            self.tm.check_mangled_names,
            self.tm.check_geometry_lod_suffix,
            self.tm.check_hidden_geometry,
        ):
            with self.subTest(check=check.__name__):
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
        ok, messages = self.tm.check_duplicate_locator_names()
        self.assertFalse(ok)
        self.assertTrue(any("SNAP" in m for m in messages), messages)


class TestTexturePathPipeline(MayaTkTestCase):
    """stage_textures_relative + reworked path/geometry/anim checks.

    Added: 2026-08-01 (scene-exporter robustness audit, implementation pass).
    """

    def setUp(self):
        super().setUp()
        self.exporter = SceneExporter(log_level="DEBUG")
        self.tm = self.exporter.task_manager
        self.temp_dir = tempfile.mkdtemp()
        self.cube = cmds.polyCube(name="PipelineCube")[0]
        self.cube_long = cmds.ls(self.cube, long=True)[0]
        self.ws_src = os.path.join(
            cmds.workspace(query=True, rootDirectory=True), "sourceimages"
        )
        os.makedirs(self.ws_src, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
        super().tearDown()

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
                cmds.getAttr(f"{node}.fileTextureName"), "sourceimages/pipe_reuse_1.png"
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

        Added: 2026-08-12
        """
        from mayatk.mat_utils._mat_utils import MatUtils

        resident = os.path.join(self.ws_src, "pipe_tile.1001.png")
        with open(resident, "w") as f:
            f.write("resident tile")
        self.addCleanup(os.remove, resident)
        for tile in ("1001", "1002"):
            landed = os.path.join(self.ws_src, f"pipe_tile.{tile}_1.png")
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
            "sourceimages/pipe_tile.<UDIM>_1.png",
        )
        # Stat, not read — see the sharing-violation note in the test above.
        for tile in ("1001", "1002"):
            landed = os.path.join(self.ws_src, f"pipe_tile.{tile}_1.png")
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
            self.tm.convert_textures("glTF 2.0")
            updater.assert_called_once()
            kwargs = updater.call_args.kwargs
            self.assertEqual(kwargs.get("config"), "glTF 2.0")
            self.assertTrue(kwargs.get("materials"), "export materials must be passed")
        self.assertIsNone(
            self.tm._cached_materials,
            "conversion must invalidate the material caches",
        )

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
        with patch.object(
            mat_updater.MatUpdater,
            "update_materials",
            side_effect=RuntimeError("unreadable texture"),
        ):
            self.tm.convert_textures("glTF 2.0")  # must not raise
        self.assertIsNone(
            self.tm._cached_materials,
            "caches must invalidate even when the conversion failed",
        )

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
        self.tm.export_path = "C:/" + ("dir/" * 40) + "asset.fbx"
        try:
            status, msgs = self.tm.check_path_length(60)
            self.assertFalse(status)
            self.assertTrue(any("export path" in m for m in msgs))
        finally:
            self.tm.export_path = original

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


if __name__ == "__main__":
    unittest.main()
