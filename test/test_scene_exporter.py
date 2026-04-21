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
import pymel.core as pm

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
        self.cube = pm.polyCube(name="ExportCube")[0]
        self.sphere = pm.polySphere(name="ExportSphere")[0]
        self.group = pm.group(self.cube, self.sphere, name="ExportGroup")

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
        pm.select(self.cube)
        objs = self.exporter._initialize_objects(None)
        self.assertEqual(len(objs), 1)
        self.assertIn(self.cube.longName(), objs)

    def test_initialize_objects_list(self):
        """Test object initialization from list."""
        objs = self.exporter._initialize_objects([self.sphere.name()])
        self.assertEqual(len(objs), 1)
        self.assertIn(self.sphere.longName(), objs)

    def test_initialize_objects_callable(self):
        """Test object initialization from callable."""

        def get_objs():
            return [self.group.name()]

        objs = self.exporter._initialize_objects(get_objs)
        self.assertEqual(len(objs), 1)
        self.assertIn(self.group.longName(), objs)

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
        pm.renameFile(scene_path)

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
            if not pm.pluginInfo("fbxmaya", q=True, loaded=True):
                pm.loadPlugin("fbxmaya")
        except:
            print("FBX plugin not available, skipping actual export call")
            return

        result = self.exporter.perform_export(
            export_dir=self.temp_dir,
            objects=[self.cube.name()],
            file_format="FBX export",
        )
        self.assertIsNotNone(result)

    # ------------------------------------------------------------------
    # Task / check running
    # ------------------------------------------------------------------

    def test_run_tasks(self):
        """Test running tasks via the exporter."""
        tasks = {
            "set_linear_unit": "cm",
            "check_framerate": "30fps",
        }
        self.exporter.task_manager.objects = [self.cube.longName()]
        success = self.exporter.task_manager.run_tasks(tasks)
        self.assertTrue(success)

    def test_check_failure(self):
        """Test that a failing check returns False."""
        shader = pm.shadingNode("lambert", asShader=True)
        file_node = pm.shadingNode("file", asTexture=True)
        pm.connectAttr(f"{file_node}.outColor", f"{shader}.color")
        pm.setAttr(f"{file_node}.fileTextureName", "C:/absolute/path/texture.png")
        pm.select(self.cube)
        pm.hyperShade(assign=shader)

        tasks = {"check_absolute_paths": True}
        self.exporter.task_manager.objects = [self.cube.longName()]
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
        shader = pm.shadingNode("lambert", asShader=True)
        pm.select(self.cube)
        pm.hyperShade(assign=shader)

        self.exporter.task_manager.objects = [self.cube.longName()]

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
        shader = pm.shadingNode("lambert", asShader=True)
        pm.select(self.cube)
        pm.hyperShade(assign=shader)

        self.exporter.task_manager.objects = [self.cube.longName()]
        self.exporter.task_manager._get_all_materials()
        self.assertIsNotNone(self.exporter.task_manager._cached_materials)

        self.exporter.task_manager.objects = [self.sphere.longName()]
        self.assertIsNone(
            self.exporter.task_manager._cached_materials,
            "Cache should be None after objects change",
        )

    def test_task_timing_logged(self):
        """Verify per-task timing is logged at INFO level.

        Added timing instrumentation to _manage_context so each task/check
        logs its execution duration for performance diagnostics.
        Fixed: 2026-02-22
        """
        log_output = []
        handler = logging.Handler()
        handler.emit = lambda record: log_output.append(record.getMessage())
        handler.setLevel(logging.INFO)
        self.exporter.logger.addHandler(handler)
        self.exporter.logger.setLevel(logging.INFO)

        self.exporter.task_manager.objects = [self.cube.longName()]
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
        tm.objects = [self.cube.longName()]

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
        tm.objects = [self.cube.longName()]

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

        shader = pm.shadingNode("lambert", asShader=True)
        file_node = pm.shadingNode("file", asTexture=True)
        pm.connectAttr(f"{file_node}.outColor", f"{shader}.color")
        cmds.setAttr(f"{file_node}.fileTextureName", tex_path, type="string")
        pm.select(self.cube)
        pm.hyperShade(assign=shader)

        self.exporter.task_manager.objects = [self.cube.longName()]

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
        shader = pm.shadingNode("lambert", asShader=True)
        file_node = pm.shadingNode("file", asTexture=True)
        pm.connectAttr(f"{file_node}.outColor", f"{shader}.color")
        cmds.setAttr(
            f"{file_node}.fileTextureName",
            "/nonexistent/path/missing_texture.png",
            type="string",
        )
        pm.select(self.cube)
        pm.hyperShade(assign=shader)

        self.exporter.task_manager.objects = [self.cube.longName()]

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
        pm.renameFile(scene_path)

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
        pm.currentUnit(time="ntsc")
        # Create a keyframe so the check doesn't skip
        cmds.setKeyframe(self.cube.name(), attribute="translateX", time=1, value=0)

        self.exporter.task_manager.objects = [self.cube.longName()]
        success, messages = self.exporter.task_manager.check_framerate("ntsc")
        self.assertTrue(success)
        self.assertEqual(
            messages, [], "Passing framerate check should return no messages"
        )

    def test_check_framerate_fail_returns_messages(self):
        """Verify check_framerate returns (False, [...]) on mismatch."""
        pm.currentUnit(time="ntsc")
        cmds.setKeyframe(self.cube.name(), attribute="translateX", time=1, value=0)

        self.exporter.task_manager.objects = [self.cube.longName()]
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
        self.exporter.task_manager.objects = [self.cube.longName()]

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
        self.exporter.task_manager.objects = [self.cube.longName()]
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
        self.exporter.task_manager.objects = [self.cube.longName()]
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
        self.exporter.task_manager.objects = [self.cube.longName()]

        with patch("mayatk.anim_utils.smart_bake.SmartBake") as MockBaker:
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
        self.exporter.task_manager.objects = [self.cube.longName()]
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
            self.group.longName(),
            self.cube.longName(),
            self.sphere.longName(),
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
            self.group.longName(),
            self.cube.longName(),
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
            self.group.longName(),
            self.cube.longName(),
            self.sphere.longName(),
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
        self.exporter.task_manager.objects = [self.group.longName()]
        self.exporter.task_manager.export_path = export_path
        original = sorted(self.exporter.task_manager._build_full_hierarchy_set())
        with open(manifest_path, "w") as f:
            json.dump({"paths": original, "object_count": len(original)}, f)

        # Reparent everything under a new group
        new_parent = pm.group(self.group, name="NewParent")
        self.exporter.task_manager.objects = [new_parent.longName()]

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
        cmds.setAttr(f"{self.group.longName()}.translateX", 10)

        # Objects are geometry — exactly what get_visible_geometry returns
        self.exporter.task_manager.objects = [
            self.cube.longName(),
            self.sphere.longName(),
        ]

        passed, messages = self.exporter.task_manager.check_root_default_transforms()
        self.assertFalse(passed, "Should fail — root group has non-default transforms")
        found = any("ExportGroup" in m for m in messages)
        self.assertTrue(found, "ExportGroup should be flagged in messages")

    def test_root_transforms_passes_for_default_group(self):
        """Root transform check passes when root group has identity transforms."""
        self.exporter.task_manager.objects = [
            self.cube.longName(),
            self.sphere.longName(),
        ]

        passed, _ = self.exporter.task_manager.check_root_default_transforms()
        self.assertTrue(passed)

    def test_root_transforms_detects_wrapper_group(self):
        """Root transform check catches a wrapper group with non-default transforms.

        Bug: Wrapping the entire scene in a new group was undetected.
        Fixed: 2026-04-10
        """
        wrapper = pm.group(self.group, name="WrapperGroup")
        cmds.setAttr(f"{wrapper.longName()}.translateY", 5)

        self.exporter.task_manager.objects = [
            self.cube.longName(),
            self.sphere.longName(),
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
        wrapper = pm.group(self.group, name="WrapperGroup")
        self.exporter.task_manager.objects = [
            self.cube.longName(),
            self.sphere.longName(),
        ]
        self.exporter.task_manager.export_path = export_path

        passed, messages = self.exporter.task_manager.check_hierarchy_vs_existing_fbx()
        self.assertFalse(passed, "Wrapped hierarchy should differ from manifest")
        self.assertTrue(any("missing" in m.lower() for m in messages))
        self.assertTrue(any("new" in m.lower() for m in messages))


if __name__ == "__main__":
    unittest.main()
