# !/usr/bin/python
# coding=utf-8
"""
Test Suite for MaterialUpdater Diagnostics

Tests specifically for the logging and error reporting logic in MaterialUpdater.
"""
import os
import unittest
from unittest.mock import MagicMock, patch
import pymel.core as pm

from base_test import MayaTkTestCase
from mayatk.mat_utils.mat_updater import MatUpdater


class TestMatUpdaterDiagnostics(MayaTkTestCase):
    """Tests for MatUpdater diagnostic logging."""

    def setUp(self):
        super().setUp()
        self.updater = MatUpdater()

    @patch("mayatk.mat_utils.mat_updater.MatUpdater.logger")
    def test_no_file_nodes_warning(self, mock_logger):
        """Test warning when a material has no file nodes connected."""
        # Create a material with no connections
        mat = pm.shadingNode("standardSurface", asShader=True, name="empty_mat")

        self.updater.update_materials(materials=[mat], verbose=False)

        # Verify the specific warning was logged
        expected_msg = "No file nodes found connected to empty_mat."

        # Check if warning was called with the expected message
        found = False
        for call in mock_logger.info.call_args_list:
            args, kwargs = call
            if args and expected_msg in args[0]:
                found = True
                break

        self.assertTrue(found, f"Expected info '{expected_msg}' not found in logs.")

    @patch("mayatk.mat_utils.material_updater.MaterialUpdater.logger")
    def test_invalid_paths_warning(self, mock_logger):
        """Test warning when file nodes exist but paths cannot be resolved."""
        # Create a material
        mat = pm.shadingNode("standardSurface", asShader=True, name="broken_mat")

        # Create a file node with a non-existent path
        file_node = pm.shadingNode("file", asTexture=True, name="broken_file")
        file_node.fileTextureName.set("Z:/non_existent/path/texture.png")

        # Connect it
        if hasattr(mat, "baseColor"):
            pm.connectAttr(file_node.outColor, mat.baseColor)

        # Run updater
        self.updater.update_materials(materials=[mat], verbose=False)

        # Verify the specific warning was logged
        # The message format is: "Found 1 file nodes on broken_mat, but no valid paths could be resolved."
        expected_msg_part1 = "Found 1 file nodes on broken_mat"
        expected_msg_part2 = "but no valid paths could be resolved"

        found = False
        for call in mock_logger.warning.call_args_list:
            args, kwargs = call
            if args and expected_msg_part1 in args[0] and expected_msg_part2 in args[0]:
                found = True
                break

        self.assertTrue(
            found, f"Expected warning about invalid paths not found in logs."
        )
