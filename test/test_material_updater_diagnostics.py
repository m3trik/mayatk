# !/usr/bin/python
# coding=utf-8
"""
Test Suite for MaterialUpdater Diagnostics

Tests specifically for the logging and error reporting logic in MaterialUpdater.
"""
import os
import unittest
from unittest.mock import MagicMock, patch
import maya.cmds as cmds
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
        mat = cmds.shadingNode("standardSurface", asShader=True, name="empty_mat")
        # Capture the actual created name (Maya may auto-suffix on conflict).
        target = str(mat).split("|")[-1].split(":")[-1]
        # log_link is invoked on the mocked logger and returns the raw text
        # so the assertion below can match the material name.
        mock_logger.log_link.side_effect = lambda text, *a, **kw: text

        self.updater.update_materials(materials=[mat], verbose=False)

        # The logger embeds an HTML <a> link around the material name, so
        # match on the prefix and material short name independently.
        prefix = "No file nodes found connected to"

        found = False
        all_msgs = []
        for call in mock_logger.info.call_args_list:
            args, _ = call
            if args:
                all_msgs.append(args[0])
                if prefix in args[0] and target in args[0]:
                    found = True
                    break

        self.assertTrue(
            found,
            f"Expected info containing '{prefix} ... {target}' not found.\n"
            f"All info messages: {all_msgs}",
        )

    @patch("mayatk.mat_utils.mat_updater.MatUpdater.logger")
    def test_invalid_paths_warning(self, mock_logger):
        """Test warning when file nodes exist but paths cannot be resolved."""
        # Create a material
        mat = cmds.shadingNode("standardSurface", asShader=True, name="broken_mat")
        target = str(mat).split("|")[-1].split(":")[-1]
        mock_logger.log_link.side_effect = lambda text, *a, **kw: text

        # Create a file node with a non-existent path
        file_node = cmds.shadingNode("file", asTexture=True, name="broken_file")
        cmds.setAttr(f"{file_node}.fileTextureName", "Z:/non_existent/path/texture.png", type="string")

        # Connect it
        if cmds.attributeQuery("baseColor", node=str(mat), exists=True):
            cmds.connectAttr(f"{file_node}.outColor", f"{mat}.baseColor")

        # Run updater
        self.updater.update_materials(materials=[mat], verbose=False)

        # The logger wraps the material name in an HTML link, so check the
        # message prefix, the material short name, and the suffix separately.
        expected_prefix = "Found 1 file nodes on"
        expected_suffix = "but no valid paths could be resolved"

        found = False
        all_msgs = []
        for call in mock_logger.warning.call_args_list:
            args, _ = call
            if args:
                all_msgs.append(args[0])
                if (
                    expected_prefix in args[0]
                    and target in args[0]
                    and expected_suffix in args[0]
                ):
                    found = True
                    break

        self.assertTrue(
            found,
            "Expected warning about invalid paths not found.\n"
            f"All warning messages: {all_msgs}",
        )
