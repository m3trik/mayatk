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
import shutil
import unittest
import tempfile
import logging
from unittest.mock import patch
import maya.cmds as cmds

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
from base_test import MayaTkTestCase


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
        # setUp opens a fresh untitled scene, so sceneName is empty here.
        self.assertEqual(cmds.file(query=True, sceneName=True), "")

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
        cmds.setAttr(f"{file_node}.fileTextureName", "C:/absolute/path/texture.png", type="string")
        cmds.select(self.cube)
        cmds.hyperShade(assign=shader)

        tasks = {"check_absolute_paths": True}
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
        cmds.select(self.cube)
        cmds.hyperShade(assign=shader)

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
        cmds.select(self.cube)
        cmds.hyperShade(assign=shader)

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

    def test_optimize_keys_enabled_attribute_set_by_run_tasks(self):
        """Verify _optimize_keys_enabled is set from the optimize_keys task value
        before tasks execute.

        When optimize_keys is present and True in the task dict,
        _execute_tasks_and_checks must set _optimize_keys_enabled = True
        so SmartBake can read it.
        Fixed: 2026-03-04
        """
        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        # Run with optimize_keys=True
        tm.run_tasks({"optimize_keys": True})
        self.assertTrue(
            getattr(tm, "_optimize_keys_enabled", None),
            "_optimize_keys_enabled should be True when optimize_keys task is True",
        )

    def test_optimize_keys_disabled_attribute_set_by_run_tasks(self):
        """Verify _optimize_keys_enabled is False when optimize_keys is not in tasks.

        When the user unchecks optimize_keys, it won't appear in the filtered
        task dict (b000 filters out falsy values).  _execute_tasks_and_checks
        should set _optimize_keys_enabled = False.
        Fixed: 2026-03-04
        """
        tm = self.exporter.task_manager
        tm.objects = [cmds.ls(str(self.cube), l=True)[0]]

        # Run without optimize_keys in the dict (simulates unchecked)
        tm.run_tasks({"set_linear_unit": "cm"})
        self.assertFalse(
            getattr(tm, "_optimize_keys_enabled", True),
            "_optimize_keys_enabled should be False when optimize_keys absent",
        )

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
        cmds.select(self.cube)
        cmds.hyperShade(assign=shader)

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
        cmds.select(self.cube)
        cmds.hyperShade(assign=shader)

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
        cmds.select(node_path)
        cmds.hyperShade(assign=shader)
        return file_node

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
        """check_texture_file_size is a ComboBox check defaulting to 16 MB.

        Added: 2026-06-19
        """
        defs = self.exporter.task_manager.check_definitions
        self.assertIn("check_texture_file_size", defs)
        entry = defs["check_texture_file_size"]
        self.assertEqual(entry["widget_type"], "ComboBox")
        # setCurrentIndex must point at the 16 MB option.
        options = list(self.exporter.task_manager._texture_size_options.values())
        self.assertEqual(options[entry["setCurrentIndex"]], 16)

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
        from mayatk.env_utils.hierarchy_manager.hierarchy_sidecar import (
            HierarchySidecar,
        )

        result = HierarchySidecar.manifest_path_for("/assets/hero.fbx")
        self.assertTrue(result.endswith(".hero.hierarchy.json"))

    def test_diff_report_path_for(self):
        """Verify sidecar diff report path derivation."""
        from mayatk.env_utils.hierarchy_manager.hierarchy_sidecar import (
            HierarchySidecar,
        )

        result = HierarchySidecar.diff_report_path_for("/assets/hero.fbx")
        self.assertTrue(result.endswith(".hero.hierarchy_diff.txt"))

    def test_build_clean_path_set_strips_namespace(self):
        """Verify namespace stripping and leading pipe removal."""
        from mayatk.env_utils.hierarchy_manager.hierarchy_sidecar import (
            HierarchySidecar,
        )

        objects = ["|ns:group|ns:child", "|group2|child2"]
        result = HierarchySidecar.build_clean_path_set(objects)
        self.assertEqual(result, {"group|child", "group2|child2"})

    def test_get_top_level_collapses_children(self):
        """Verify that children are collapsed under their top-level parent."""
        from mayatk.env_utils.hierarchy_manager.hierarchy_sidecar import (
            HierarchySidecar,
        )

        paths = ["group", "group|child", "group|child|grandchild", "other"]
        result = HierarchySidecar.get_top_level(paths)
        self.assertEqual(sorted(result), ["group", "other"])

    def test_get_top_level_preserves_siblings(self):
        """Verify that siblings with similar prefix names are NOT collapsed."""
        from mayatk.env_utils.hierarchy_manager.hierarchy_sidecar import (
            HierarchySidecar,
        )

        paths = ["group", "group_alt", "group|child"]
        result = HierarchySidecar.get_top_level(paths)
        self.assertEqual(sorted(result), ["group", "group_alt"])

    def test_detect_reparenting_finds_moved_subtree(self):
        """detect_reparenting recognises a subtree moved under a new parent."""
        from mayatk.env_utils.hierarchy_manager.hierarchy_sidecar import (
            HierarchySidecar,
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
        result = HierarchySidecar.detect_reparenting(missing, extra)
        self.assertEqual(len(result), 1)
        root, parent, count = result[0]
        self.assertEqual(root, "GRP")
        self.assertEqual(parent, "new")
        self.assertEqual(count, 5)

    def test_detect_reparenting_returns_empty_on_unrelated_changes(self):
        """detect_reparenting returns empty when changes are not reparenting."""
        from mayatk.env_utils.hierarchy_manager.hierarchy_sidecar import (
            HierarchySidecar,
        )

        missing = ["OldNode", "OldNode|Child"]
        extra = ["CompletelyDifferent"]
        result = HierarchySidecar.detect_reparenting(missing, extra)
        self.assertEqual(result, [])

    def test_hierarchy_check_no_manifest(self):
        """Check passes when no manifest exists yet."""
        self.exporter.task_manager.objects = [cmds.ls(str(self.cube), l=True)[0]]
        self.exporter.task_manager.export_path = os.path.join(self.temp_dir, "test.fbx")
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
        wrapper = cmds.group(self.group, name="WrapperGroup")
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


if __name__ == "__main__":
    unittest.main()
