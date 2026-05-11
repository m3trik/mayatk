# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.uv_utils.rizom_bridge.

Regression coverage for the Maya-side of the bridge -- export path only.
RizomUV invocation is exercised by the standalone smoketest under
``temp_tests/`` because it needs the external executable.

Tests here run inside a live Maya session via ``run_tests.py`` and catch
the failure modes the standalone smoketest cannot:

- ``fbxmaya`` plugin not pre-loaded in interactive Maya.
- Multiple duplicates collapsing to the same leaf name (different parents)
  causing ``cmds.select`` ambiguity.
"""
import os
import unittest
import tempfile
from pathlib import Path

import maya.cmds as cmds

from mayatk.uv_utils.rizom_bridge._rizom_bridge import RizomUVBridge

from base_test import MayaTkTestCase


class TestRizomBridgeExport(MayaTkTestCase):
    """Maya-only: validates the export half of the bridge end-to-end."""

    def setUp(self):
        super().setUp()
        # Force the bridge to a temp path each test so we can assert on it.
        fd, path = tempfile.mkstemp(suffix=".fbx", prefix="rizom_test_")
        os.close(fd)
        # The file must NOT exist when the export runs (mtime check is permissive
        # but we only care about the post-state here).
        Path(path).unlink(missing_ok=True)
        self.export_path = path

        # Construct a bridge but do not require RizomUV on disk -- we never
        # invoke the executable from these tests.
        self.bridge = RizomUVBridge(rizom_path="not-used.exe")
        self.bridge.export_path = self.export_path

    def tearDown(self):
        Path(self.export_path).unlink(missing_ok=True)
        super().tearDown()

    def test_export_loads_fbx_plugin_when_unloaded(self):
        """Bridge must load fbxmaya itself; live Maya doesn't pre-load it."""
        if cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            try:
                cmds.unloadPlugin("fbxmaya", force=True)
            except RuntimeError:
                self.skipTest("fbxmaya cannot be unloaded in this session.")

        cube = cmds.polyCube(name="rizom_plugin_test")[0]
        self.bridge._export_objects([cube])

        self.assertTrue(
            cmds.pluginInfo("fbxmaya", query=True, loaded=True),
            "Bridge should have loaded fbxmaya before exporting.",
        )
        self.assertTrue(
            Path(self.export_path).exists(),
            f"FBX not written to {self.export_path}",
        )
        self.assertGreater(
            Path(self.export_path).stat().st_size, 0, "FBX is empty."
        )

    def test_export_handles_name_collisions_under_different_parents(self):
        """Two duplicates may share a leaf name -- bridge must use long paths.

        Reproduces the 29-object failure: when ``cmds.duplicate`` produces
        nodes whose post-rename leaf names collide (e.g. one at world root
        and one under another parent), ``cmds.select`` raises
        'More than one object matches name'. The bridge must resolve to
        full DAG paths before selecting.
        """
        # Parent group whose child collides with a world-root sibling.
        parent = cmds.group(empty=True, name="OUTPUT_CTRL")
        inside = cmds.polyCube(name="SWITCH_GEO")[0]
        cmds.parent(inside, parent)
        # Need its long path for export -- short name "SWITCH_GEO" exists twice.
        inside_long = cmds.ls(inside, long=True)[0]

        outside = cmds.polyCube(name="SWITCH_GEO")[0]  # world root, same leaf
        outside_long = cmds.ls(outside, long=True)[0]

        # Add a few unrelated cubes so the test mirrors the bulk-export shape.
        extras = [cmds.polyCube(name=f"extra_{i}")[0] for i in range(5)]

        # Should not raise.
        self.bridge._export_objects([inside_long, outside_long] + extras)

        self.assertTrue(
            Path(self.export_path).exists(),
            f"FBX not written to {self.export_path}",
        )
        self.assertGreater(
            Path(self.export_path).stat().st_size, 0, "FBX is empty."
        )


class TestRizomBridgeUiResize(MayaTkTestCase):
    """The window must shrink/grow when the active script's parameters change."""

    def test_window_height_tracks_visible_param_rows(self):
        """Switching scripts hides/shows rows and the window follows."""
        from qtpy import QtWidgets
        from uitk import Switchboard
        from mayatk.uv_utils.rizom_bridge.rizom_bridge_slots import (
            RizomBridgeSlots,
        )
        from mayatk.uv_utils.rizom_bridge import _rizom_bridge as bridge_mod
        from mayatk.uv_utils.rizom_bridge import parameters as _params

        sb = Switchboard(
            ui_source=str(bridge_mod._PKG_DIR),
            slot_source=RizomBridgeSlots,
        )
        ui = sb.loaded_ui.rizom_bridge
        # Don't load (or persist) saved geometry -- we want a controlled height.
        ui.restore_window_size = False
        ui.show()
        QtWidgets.QApplication.processEvents()
        ui.is_initialized = True

        scripts = sorted(p.stem for p in bridge_mod._SCRIPT_DIR.glob("*.lua"))

        def row_count(stem):
            path = bridge_mod._SCRIPT_DIR / f"{stem}.lua"
            return len(_params.referenced_keys(path.read_text(encoding="utf-8")))

        if len(scripts) < 2:
            self.skipTest("Need at least two bundled scripts to compare heights.")
        sorted_by_rows = sorted(scripts, key=row_count)
        few, many = sorted_by_rows[0], sorted_by_rows[-1]
        if row_count(few) == row_count(many):
            self.skipTest("All bundled scripts reference the same param count.")

        cmb = ui.cmb000
        items_by_text = {cmb.itemText(i): i for i in range(cmb.count())}

        # Start with the wider preset and force the window taller than
        # whatever fit would compute, so we can observe a shrink delta.
        cmb.setCurrentIndex(items_by_text[many])
        QtWidgets.QApplication.processEvents()
        ui.resize(ui.width(), 800)
        QtWidgets.QApplication.processEvents()
        height_many = ui.height()

        cmb.setCurrentIndex(items_by_text[few])
        # Drain the event queue enough for the deferred fit (QTimer.singleShot)
        # to fire AND its resize() to settle.
        for _ in range(5):
            QtWidgets.QApplication.processEvents()
        height_few = ui.height()

        ui.close()
        ui.deleteLater()

        self.assertLess(
            height_few,
            height_many,
            f"Window did not shrink: '{many}' ({row_count(many)} rows) "
            f"@ {height_many}px -> '{few}' ({row_count(few)} rows) "
            f"@ {height_few}px.",
        )


if __name__ == "__main__":
    unittest.main()
