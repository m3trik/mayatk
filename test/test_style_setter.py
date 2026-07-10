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
CHANGELOG): Maya's live colors are never snapshotted. The revert story is instead the shipped
``Maya`` style (added 2026-07-09): a static factory-values counterpart of ``Blender.json``
covering exactly the same keys, captured from a virgin-``MAYA_APP_DIR`` Maya 2025.3 (note:
``displayRGBColor(resetToFactory=True)`` does NOT restore ``backgroundTop``/``backgroundBottom``,
so a factory-prefs capture is the only reliable source — see the style's ``_meta``). This suite
still mutates the SAME live, persistent Maya instance every test method runs against, though, so
it captures + restores every key the shipped styles can touch (derived from the style files, so a
new style key can't silently leak), purely for test hygiene — not by resurrecting the removed
production API.
"""
import unittest

import mayatk as mtk
from mayatk.ui_utils.style_setter import _style_setter as ss

from base_test import QuickTestCase
import maya.cmds as cmds


def _close(a, b, tol=0.01):
    return all(abs(x - y) < tol for x, y in zip(a, b))


def _shipped_key_surface():
    """Union of the keys the shipped styles touch: (rgb names, {(name, state)}).

    Derived from the style JSONs themselves so the hygiene snapshot (and the
    restore-counterpart contract test) can never drift from what ships.
    """
    rgb, display = set(), set()
    for name in ss.list_styles():
        style = ss._load_style(name)
        rgb |= set(style.get("rgb", {}))
        for dc_name, spec in style.get("display_color", {}).items():
            display |= {(dc_name, state) for state in spec}
    return rgb, display


class TestStyleSetter(QuickTestCase):
    def setUp(self):
        super().setUp()
        # Local snapshot of every key any shipped style can touch (test hygiene only — the
        # production module deliberately has no backup/restore of its own any more).
        rgb_keys, display_keys = _shipped_key_surface()
        self._orig_rgb = {
            name: cmds.displayRGBColor(name, query=True, a=True) for name in rgb_keys
        }
        self._orig_slots = {}
        for name, state in display_keys:
            idx = int(cmds.displayColor(name, query=True, **{state: True}))
            self._orig_slots[idx] = cmds.colorIndex(idx, query=True)

    def tearDown(self):
        for name, rgba in self._orig_rgb.items():
            cmds.displayRGBColor(name, *rgba)
        for idx, rgb in self._orig_slots.items():
            cmds.colorIndex(idx, *rgb)

    def test_registered_as_just_the_class(self):
        """Mirrors blendertk's StyleSetter convention: registered as the class, not sprayed
        generic-named helpers into the flat mtk.* namespace."""
        self.assertIs(getattr(mtk, "StyleSetter", None), ss.StyleSetter)
        self.assertFalse(hasattr(mtk, "set_style"))
        self.assertFalse(hasattr(mtk, "list_styles"))

    def test_list_styles_ships_blender_and_maya(self):
        self.assertEqual(ss.list_styles(), ["Blender", "Maya"])

    def test_unknown_style_raises(self):
        with self.assertRaises(FileNotFoundError):
            ss.set_style("ZZNoSuchStyle")

    def test_no_backup_restore_api(self):
        """Backup/restore was removed 2026-07-05 — Maya's own colors are never snapshotted; the
        shipped 'Maya' factory style is the revert target, not a live snapshot mechanism."""
        for name in ("BACKUP_NAME", "ensure_backup", "backup_current", "has_backup", "backup_dir", "backup_path", "restore_default_style"):
            self.assertFalse(hasattr(ss, name), f"ss.{name} should be gone")
            self.assertFalse(hasattr(mtk.StyleSetter, name), f"StyleSetter.{name} should be gone")

    def test_maya_style_mirrors_blender_key_surface(self):
        """The 'Maya' style is the factory-restore counterpart of 'Blender': it must define
        exactly the same rgb keys and display_color name/state entries, or switching between the
        two leaves some keys un-restored (the whole point of shipping it)."""
        blender = ss._load_style("Blender")
        maya = ss._load_style("Maya")
        self.assertEqual(set(maya["rgb"]), set(blender["rgb"]))
        self.assertEqual(set(maya["display_color"]), set(blender["display_color"]))
        for name, spec in blender["display_color"].items():
            self.assertEqual(
                set(maya["display_color"][name]), set(spec), f"states differ for {name}"
            )

    def test_list_templates_and_apply_template(self):
        """list_templates() offers just the shipped styles; apply_template forwards a name to
        set_style (uniform surface with btk.StyleSetter, though Maya has no native dropdown)."""
        templates = mtk.StyleSetter.list_templates()
        self.assertEqual(templates, {"Blender": "Blender", "Maya": "Maya"})

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

    def test_maya_style_restores_after_blender(self):
        """The Blender→Maya round-trip: applying 'Maya' after 'Blender' must land every key on
        the Maya style's stored (factory) values — the in-repo version of the live capture that
        produced the file (verified against a virgin-prefs Maya 2025.3, 2026-07-09)."""
        maya_style = ss._load_style("Maya")

        mtk.StyleSetter.set_style("Blender")
        mtk.StyleSetter.set_style("Maya")

        for name, rgba in maya_style["rgb"].items():
            live = cmds.displayRGBColor(name, query=True, a=True)
            self.assertTrue(_close(live[:3], rgba[:3]), f"{name}: {live} != {rgba}")

        for name, spec in maya_style["display_color"].items():
            for state, rgb in spec.items():
                idx = cmds.displayColor(name, query=True, **{state: True})
                live = cmds.colorIndex(int(idx), query=True)
                self.assertTrue(_close(live, rgb), f"{name}.{state}: {live} != {rgb}")


if __name__ == "__main__":
    unittest.main()
