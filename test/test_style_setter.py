# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.ui_utils.style_setter

Covers the ``StyleSetter`` "Colors" preferences overlay (background / grid / dormant polygon-edge
color) — the mayatk counterpart to blendertk's ``StyleSetter``, scoped to what Maya actually
exposes scriptably (see the module docstring in ``ui_utils/style_setter/_style_setter.py``).

Session-safety note: ``cmds.displayRGBColor`` / ``colorIndex`` / ``displayColor`` all raise in
batch mode ("unavailable in batch mode"), so this suite only passes when run the normal
``run_tests.py`` way (a real, interactive Maya launched via ``MayaConnection`` port mode) — never
under plain ``mayapy`` standalone.

``StyleSetter`` itself has no backup/restore of its own (removed 2026-07-05 — see the package
CHANGELOG): reverting Maya's colors is out of scope for this tool. Unlike blendertk (where
Blender's own built-in themes stay in ``list_templates()`` regardless, so a revert target always
exists), Maya has no built-in "theme" to fall back on — removing the backup genuinely leaves this
tool with no revert path at all, an accepted asymmetry, not an oversight. This suite still mutates
the SAME live, persistent Maya instance every test method runs against, though, so it captures +
restores the handful of keys ``set_style("Blender")`` touches itself, locally, purely for test
hygiene — not by resurrecting the removed production API.
"""
import unittest

import mayatk as mtk
from mayatk.ui_utils.style_setter import _style_setter as ss

from base_test import QuickTestCase
import maya.cmds as cmds


def _close(a, b, tol=0.01):
    return all(abs(x - y) < tol for x, y in zip(a, b))


class TestStyleSetter(QuickTestCase):
    def setUp(self):
        super().setUp()
        # Local snapshot of just the keys any shipped style can touch (test hygiene only — the
        # production module deliberately has no backup/restore of its own any more).
        self._orig_bg = cmds.displayRGBColor("background", query=True, a=True)
        self._orig_grid_idx = cmds.displayColor("grid", query=True, dormant=True)
        self._orig_grid_rgb = cmds.colorIndex(int(self._orig_grid_idx), query=True)
        self._orig_edge_idx = cmds.displayColor("polyEdge", query=True, dormant=True)
        self._orig_edge_rgb = cmds.colorIndex(int(self._orig_edge_idx), query=True)

    def tearDown(self):
        cmds.displayRGBColor("background", *self._orig_bg)
        cmds.colorIndex(int(self._orig_grid_idx), *self._orig_grid_rgb)
        cmds.colorIndex(int(self._orig_edge_idx), *self._orig_edge_rgb)

    def test_registered_as_just_the_class(self):
        """Mirrors blendertk's StyleSetter convention: registered as the class, not sprayed
        generic-named helpers into the flat mtk.* namespace."""
        self.assertIs(getattr(mtk, "StyleSetter", None), ss.StyleSetter)
        self.assertFalse(hasattr(mtk, "set_style"))
        self.assertFalse(hasattr(mtk, "list_styles"))

    def test_list_styles_ships_blender(self):
        self.assertEqual(ss.list_styles(), ["Blender"])

    def test_unknown_style_raises(self):
        with self.assertRaises(FileNotFoundError):
            ss.set_style("ZZNoSuchStyle")

    def test_no_backup_restore_api(self):
        """Backup/restore was removed 2026-07-05 — Maya's own colors are never snapshotted, and
        there is no 'Default' entry to revert to (unlike blendertk, Maya has nothing native to
        defer reverting to, so the feature was dropped outright rather than kept half-working)."""
        for name in ("BACKUP_NAME", "ensure_backup", "backup_current", "has_backup", "backup_dir", "backup_path", "restore_default_style"):
            self.assertFalse(hasattr(ss, name), f"ss.{name} should be gone")
            self.assertFalse(hasattr(mtk.StyleSetter, name), f"StyleSetter.{name} should be gone")

    def test_list_templates_and_apply_template(self):
        """list_templates() offers just the shipped styles; apply_template forwards a name to
        set_style (uniform surface with btk.StyleSetter, though Maya has no native dropdown)."""
        templates = mtk.StyleSetter.list_templates()
        self.assertEqual(templates, {"Blender": "Blender"})

        mtk.StyleSetter.apply_template("Blender")
        bg = cmds.displayRGBColor("background", query=True, a=True)
        self.assertTrue(_close(bg[:3], (0.188, 0.188, 0.188)), bg)

    def test_set_style_applies_full_overlay(self):
        """set_style applies every key the style JSON defines: viewport bg + dormant grid/edge."""
        mtk.StyleSetter.set_style("Blender")

        bg = cmds.displayRGBColor("background", query=True, a=True)
        self.assertTrue(_close(bg[:3], (0.188, 0.188, 0.188)), bg)

        grid_idx = cmds.displayColor("grid", query=True, dormant=True)
        grid_rgb = cmds.colorIndex(int(grid_idx), query=True)
        self.assertTrue(_close(grid_rgb, (0.329, 0.329, 0.329)), grid_rgb)

        edge_idx = cmds.displayColor("polyEdge", query=True, dormant=True)
        edge_rgb = cmds.colorIndex(int(edge_idx), query=True)
        self.assertTrue(_close(edge_rgb, (0.0, 0.0, 0.0)), edge_rgb)


if __name__ == "__main__":
    unittest.main()
