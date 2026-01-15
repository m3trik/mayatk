# !/usr/bin/python
# coding=utf-8
import unittest
import maya.cmds as cmds
import pymel.core as pm
import sys
import os

# Add mayatk to path if needed (might be handled by runner)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))
from mayatk.env_utils.scene_exporter.task_manager import TaskManager
from base_test import MayaTkTestCase


class TestSceneExporterRootTransforms(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.task_manager = TaskManager()

    def test_ignore_cameras(self):
        """Test that default cameras with non-default transforms are ignored."""
        # Ensure cameras exist (they usually do in new scenes)
        # Move persp camera to non-zero
        cmds.setAttr("persp.translate", 10, 20, 30)

        # Run check
        # We don't set self.objects, so it checks checks all assemblies
        self.task_manager.objects = []

        # This returns (success, messages)
        success, messages = self.task_manager.check_root_default_transforms()

        # Verify result
        # If it failed, check if it was due to persp camera
        if not success:
            for msg in messages:
                if "|persp" in msg or "persp" in msg:
                    self.fail(
                        f"Camera 'persp' should be ignored but was flagged: {msg}"
                    )

    def test_flag_group(self):
        """Test that actual groups with non-default transforms are flagged."""
        # Create a group at root
        grp = cmds.group(em=True, name="TestRootGroup")
        cmds.setAttr(f"{grp}.translateX", 10)

        self.task_manager.objects = []

        success, messages = self.task_manager.check_root_default_transforms()

        self.assertFalse(success, "Should fail due to root group with transform")
        found = False
        for msg in messages:
            if "TestRootGroup" in msg:
                found = True
                break
        self.assertTrue(found, "TestRootGroup should be flagged")


if __name__ == "__main__":
    unittest.main()
