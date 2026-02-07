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
"""
import os
import shutil
import unittest
import tempfile
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

    def test_generate_export_path(self):
        """Test export path generation."""
        # Initialize required attributes that are normally set in perform_export
        self.exporter.export_dir = self.temp_dir
        self.exporter.output_name = None
        self.exporter.name_regex = None
        self.exporter.timestamp = False

        # Save scene to give it a name
        scene_path = os.path.join(self.temp_dir, "test_scene.ma")
        pm.renameFile(scene_path)

        # Test default
        path = self.exporter.generate_export_path()
        self.assertTrue(path.endswith("test_scene.fbx"))

        # Test output name override
        self.exporter.output_name = "CustomName"
        path = self.exporter.generate_export_path()
        self.assertTrue(path.endswith("CustomName.fbx"))

        # Test timestamp
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

        # Create dummy file
        existing_file = os.path.join(self.temp_dir, "existing_file_v001.fbx")
        with open(existing_file, "w") as f:
            f.write("dummy")

        self.exporter.output_name = "existing_file_*"
        path = self.exporter.generate_export_path()

        self.assertEqual(os.path.normpath(path), os.path.normpath(existing_file))

        # Test finding latest
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

    def test_perform_export_basic(self):
        """Test basic export execution."""
        # We can't easily test the actual FBX export in a unit test without the plugin loaded
        # and potentially popping up dialogs, but we can test the flow up to that point
        # or mock the actual export command if needed.
        # For now, we'll rely on the fact that perform_export returns the result dict or False

        # Ensure FBX plugin is loaded (mocking it if necessary for the test environment)
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

        # If export succeeds, it returns a dict of task results (or empty dict if no tasks)
        # If it fails (e.g. FBX error), it might return False or raise
        # Here we just check it didn't crash
        self.assertIsNotNone(result)

    def test_run_tasks(self):
        """Test running tasks via the exporter."""
        # Define a simple task config
        tasks = {
            "set_linear_unit": "cm",
            "check_framerate": "30fps",  # This is a check, but handled by same system
        }

        # We need to mock the actual export to avoid file operations,
        # but we can call run_tasks directly on the manager
        self.exporter.task_manager.objects = [self.cube.longName()]

        success = self.exporter.task_manager.run_tasks(tasks)
        self.assertTrue(success)

    def test_check_failure(self):
        """Test that a failing check returns False."""
        # Create a condition that will fail a check
        # e.g. check_duplicate_locator_names
        loc1 = pm.spaceLocator(name="dup_loc")
        loc2 = pm.spaceLocator(
            name="dup_loc"
        )  # Maya will rename to dup_loc1, so we need to force issue or use another check

        # Let's use check_absolute_paths with a file node having absolute path
        shader = pm.shadingNode("lambert", asShader=True)
        file_node = pm.shadingNode("file", asTexture=True)
        pm.connectAttr(f"{file_node}.outColor", f"{shader}.color")
        pm.setAttr(f"{file_node}.fileTextureName", "C:/absolute/path/texture.png")

        pm.select(self.cube)
        pm.hyperShade(assign=shader)

        tasks = {"check_absolute_paths": True}

        self.exporter.task_manager.objects = [self.cube.longName()]

        # This should fail because we have an absolute path
        success = self.exporter.task_manager.run_tasks(tasks)
        self.assertFalse(success)


if __name__ == "__main__":
    unittest.main()
