# !/usr/bin/python
# coding=utf-8
"""
Test Suite for MaterialUpdater Diagnostics

Tests specifically for the logging and error reporting logic in MaterialUpdater.
"""
import logging
import os
import shutil
import unittest
from unittest.mock import MagicMock, patch
import maya.cmds as cmds
import pythontk as ptk
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

    def _run_with_texture(self, mat_name, verbose):
        """Run the updater over one material carrying a real texture path."""
        tmp = os.path.join(os.environ["TEMP"], f"matupd_diag_{mat_name}")
        os.makedirs(tmp, exist_ok=True)
        self.addCleanup(shutil.rmtree, tmp, True)
        tex = os.path.join(tmp, "thing_BaseColor.png").replace("\\", "/")
        with open(tex, "w") as f:
            f.write("dummy")

        mat = cmds.shadingNode("standardSurface", asShader=True, name=mat_name)
        fn = cmds.shadingNode("file", asTexture=True, name=f"{mat_name}_file")
        cmds.setAttr(f"{fn}.fileTextureName", tex, type="string")
        cmds.connectAttr(f"{fn}.outColor", f"{mat}.baseColor")

        with patch("mayatk.mat_utils.mat_updater.MatUpdater.logger") as mock_logger:
            mock_logger.log_link.side_effect = lambda text, *a, **kw: text
            # The gate reads the live level; the mock has to answer it.
            mock_logger.isEnabledFor.side_effect = lambda lvl: lvl >= (
                logging.INFO if verbose else logging.WARNING
            )
            self.updater.update_materials(materials=[mat], verbose=verbose)
        return mock_logger

    def test_non_verbose_run_emits_no_raw_report_blocks(self):
        """``log_box``/``log_group`` write through ``log_raw``, which bypasses
        level filtering — so a non-verbose run has to gate them itself. Both
        DCCs' Scene Exporter calls update_materials as a task with the default
        verbose=False, and would otherwise get the whole report in its log."""
        mock_logger = self._run_with_texture("quiet_mat", verbose=False)

        self.assertEqual(
            mock_logger.log_box.call_args_list,
            [],
            "A non-verbose run must not emit banner/summary boxes.",
        )
        # A WARNING-level group is still allowed through; the INFO ones are not.
        info_groups = [
            c
            for c in mock_logger.log_group.call_args_list
            if c.kwargs.get("level", "INFO").upper() != "WARNING"
        ]
        self.assertEqual(
            info_groups, [], "A non-verbose run must not emit INFO log groups."
        )

    def test_verbose_run_emits_the_report_blocks(self):
        """The same gate must not suppress the panel's own (verbose) run."""
        mock_logger = self._run_with_texture("loud_mat", verbose=True)

        titles = [c.args[0] for c in mock_logger.log_box.call_args_list if c.args]
        self.assertIn("MATERIAL UPDATE", titles)
        self.assertIn("UPDATE COMPLETE", titles)
        self.assertTrue(
            any(
                c.args and c.args[0] == "Run Settings"
                for c in mock_logger.log_group.call_args_list
            ),
            "Verbose run should emit the Run Settings group.",
        )

    def test_unwireable_node_type_reports_nothing_connected(self):
        """A material with no connector must not report maps as connected.

        ``aiStandardSurface`` sat in the supported-type list with no branch in
        ``update_network``, which returned the *planned* inventory regardless --
        so every map was reported connected while nothing was wired, and with an
        output folder set the textures were moved out from under file nodes that
        were never repathed.
        """
        # A stock shader with no connector -- deliberately NOT aiStandardSurface,
        # which would load mtoa into this mayapy process and leak the plugin into
        # every module chunked after it (that leak has already broken one suite).
        mat = cmds.shadingNode("lambert", asShader=True, name="unwireable")
        self.assertNotIn(cmds.nodeType(mat), MatUpdater.CONNECTORS)

        # A path that DOES resolve to a map type, so the inventory is non-empty
        # -- with an empty one the old code returned {} anyway and this would
        # pass against the bug it is meant to pin.
        paths = ["/nonexistent/thing_BaseColor.png"]
        self.assertTrue(
            ptk.MapFactory.resolve_map_type(paths[0]),
            "fixture must resolve to a map type or the test proves nothing",
        )

        with patch("mayatk.mat_utils.mat_updater.MatUpdater.logger") as mock_logger:
            mock_logger.log_link.side_effect = lambda text, *a, **kw: text
            connected = MatUpdater.update_network(mat, paths, {})

        self.assertEqual(connected, {})
        self.assertTrue(
            any(
                "no connector" in str(c.args[0])
                for c in mock_logger.warning.call_args_list
                if c.args
            ),
            "An unwireable node type must say so.",
        )

    def test_supported_types_are_derived_from_the_connectors(self):
        """The advertised list cannot drift from what can actually be wired."""
        from mayatk.mat_utils.mat_updater import MatUpdaterSlots

        self.assertEqual(
            tuple(MatUpdater.CONNECTORS), MatUpdater.SUPPORTED_MAT_TYPES
        )
        # The panel must not carry its own parallel copy.
        self.assertEqual(
            MatUpdaterSlots.SUPPORTED_MAT_TYPES, MatUpdater.SUPPORTED_MAT_TYPES
        )

    def test_a_slot_the_graph_lacks_is_not_reported_as_connected(self):
        """Connectors return False for a missing slot; that map is not 'connected'.

        Stingray slots are graph-dependent, so probe-and-skip is by design --
        but the skipped map was still counted and listed in the run report.
        """
        mat = cmds.shadingNode("standardSurface", asShader=True, name="slotless_mat")

        with patch("mayatk.mat_utils.mat_updater.MatUpdater.logger") as mock_logger:
            mock_logger.log_link.side_effect = lambda text, *a, **kw: text
            with patch(
                "mayatk.mat_utils.mat_updater.GameShader.connect_standard_surface_nodes",
                return_value=False,
            ):
                connected = MatUpdater.update_network(
                    mat, ["/nonexistent/thing_BaseColor.png"], {}
                )

        self.assertEqual(
            connected, {}, "A refused connection was reported as connected."
        )

    @patch("pythontk.MapFactory.prepare_maps")
    def test_unwireable_material_is_dropped_before_any_file_is_touched(self, mock_prep):
        """No factory pass, and no move, for a material that cannot be wired.

        update_network's guard keeps the *report* honest, but by then the
        textures have been processed and (with an output folder) moved out from
        under file nodes this tool never repaths.
        """
        tmp = os.path.join(os.environ["TEMP"], "matupd_unwireable")
        os.makedirs(tmp, exist_ok=True)
        self.addCleanup(shutil.rmtree, tmp, True)
        tex = os.path.join(tmp, "thing_BaseColor.png").replace("\\", "/")
        with open(tex, "w") as f:
            f.write("dummy")

        mat = cmds.shadingNode("lambert", asShader=True, name="unwireable_files")
        fn = cmds.shadingNode("file", asTexture=True, name="unwireable_file")
        cmds.setAttr(f"{fn}.fileTextureName", tex, type="string")
        cmds.connectAttr(f"{fn}.outColor", f"{mat}.color")

        with patch("mayatk.mat_utils.mat_updater.MatUpdater.logger"):
            results = MatUpdater.update_materials(materials=[mat], verbose=False)

        self.assertEqual(results, {})
        mock_prep.assert_not_called()
        self.assertTrue(os.path.isfile(tex), "the texture was touched anyway")
