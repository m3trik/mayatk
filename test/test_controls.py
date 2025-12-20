# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.rig_utils.controls

Covers:
- Dynamic preset access (Controls.circle, etc.)
- Grouping behavior
- Matching to objects
- Preset registration
"""

import unittest
import sys

try:
    from PySide2.QtWidgets import QApplication
except ImportError:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        QApplication = None

# Ensure QApplication exists before any Maya imports that might need it
if QApplication and not QApplication.instance():
    app = QApplication(sys.argv)

import pymel.core as pm
import mayatk as mtk

from base_test import MayaTkTestCase


class TestControls(MayaTkTestCase):
    def tearDown(self):
        for obj in pm.ls("*_CTRL", "*_CTRL_GRP", type="transform"):
            try:
                if pm.objExists(obj):
                    pm.delete(obj)
            except Exception:
                pass
        super().tearDown()

    def test_dynamic_preset_circle(self):
        ctrl = mtk.Controls.circle(name="testCircle", size=2.0, axis="y")
        self.assertNodeExists("testCircle_CTRL")
        self.assertNodeExists("testCircle_CTRL_GRP")
        self.assertNodeType(ctrl, "transform")

    def test_dynamic_preset_square_no_group(self):
        ctrl = mtk.Controls.square(name="testSquare", offset_group=False)
        self.assertNodeExists("testSquare_CTRL")
        self.assertFalse(pm.objExists("testSquare_CTRL_GRP"))
        self.assertNodeType(ctrl, "transform")

    def test_match_to_object(self):
        cube = pm.polyCube(name="matchTarget")[0]
        pm.xform(cube, ws=True, t=(5, 6, 7), ro=(10, 20, 30))

        nodes = mtk.Controls.create(
            "circle",
            name="matchMe",
            match=cube,
            return_nodes=True,
        )
        grp = nodes.group
        self.assertIsNotNone(grp)

        pos = pm.xform(grp, q=True, ws=True, t=True)
        self.assertAlmostEqual(pos[0], 5.0, places=3)
        self.assertAlmostEqual(pos[1], 6.0, places=3)
        self.assertAlmostEqual(pos[2], 7.0, places=3)

    def test_register_preset_dynamic_access(self):
        def _build_triangle(*, name: str, axis: str = "y", **_):
            pts = [
                (-1.0, 0.0, -1.0),
                (1.0, 0.0, -1.0),
                (0.0, 0.0, 1.25),
                (-1.0, 0.0, -1.0),
            ]
            return pm.curve(name=name, p=pts, d=1)

        mtk.Controls.register_preset("triangle", _build_triangle)

        ctrl = mtk.Controls.triangle(name="testTri", offset_group=False)
        self.assertNodeExists("testTri_CTRL")
        self.assertNodeType(ctrl, "transform")

    def test_two_way_and_four_way_arrows(self):
        a = mtk.Controls.two_way_arrow(name="twoWay", offset_group=False)
        b = mtk.Controls.four_way_arrow(name="fourWay", offset_group=False)
        self.assertNodeExists("twoWay_CTRL")
        self.assertNodeExists("fourWay_CTRL")
        self.assertNodeType(a, "transform")
        self.assertNodeType(b, "transform")

    def test_attached_text_merges_into_control(self):
        if pm.about(batch=True):
            print("Skipping text test in batch mode (textCurves unavailable)")
            return

        # Text is standalone-only; combine() can merge when desired.
        ctrl = mtk.Controls.target(name="withText", offset_group=False)
        txt = mtk.Controls.text(name="withTextLabel", text="FK", offset_group=False)

        combined = mtk.Controls.combine([ctrl, txt], name="withTextCombined_CTRL")
        self.assertNodeExists("withTextCombined_CTRL")
        self.assertNodeType(combined, "transform")

    def test_text_standalone(self):
        if pm.about(batch=True):
            print("Skipping text test in batch mode (textCurves unavailable)")
            return

        txt = mtk.Controls.text(name="standalone", text="Hello", offset_group=False)
        self.assertNodeExists("standalone_CTRL")
        self.assertNodeType(txt, "transform")

    def test_all_builtin_styles_smoke(self):
        presets = [
            ("circle", {}),
            ("square", {}),
            ("diamond", {}),
            ("arrow", {}),
            ("two_way_arrow", {}),
            ("four_way_arrow", {}),
            ("target", {}),
            ("secondary", {}),
            ("box", {}),
            ("ball", {}),
            ("chevron", {}),
            ("text", {"text": "X"}),
        ]

        for preset, kwargs in presets:
            if preset == "text" and pm.about(batch=True):
                print("Skipping text preset in batch mode")
                continue

            node = mtk.Controls.create(
                preset,
                name=f"smoke_{preset}",
                offset_group=False,
                **kwargs,
            )
            self.assertNodeType(node, "transform")


class TestControlsExtended(MayaTkTestCase):
    """Extended tests for Controls parameters and edge cases."""

    def tearDown(self):
        for obj in pm.ls("*_CTRL", "*_CTRL_GRP", "test*", type="transform"):
            try:
                if pm.objExists(obj):
                    pm.delete(obj)
            except Exception:
                pass
        super().tearDown()

    def test_create_with_color_int(self):
        """Test creating a control with an integer color index."""
        # Color index 17 is usually yellow
        ctrl = mtk.Controls.circle(name="testColorInt", color=17)
        self.assertTrue(ctrl.overrideEnabled.get())
        self.assertEqual(ctrl.overrideColor.get(), 17)

    def test_create_with_color_tuple(self):
        """Test creating a control with an RGB tuple color."""
        # Red color
        color = (1.0, 0.0, 0.0)
        ctrl = mtk.Controls.circle(name="testColorTuple", color=color)

        # RGB colors use overrideRGBColors=True (Maya 2016+)
        self.assertTrue(ctrl.overrideEnabled.get())
        self.assertTrue(ctrl.overrideRGBColors.get())

        # Check color values (approximate float comparison)
        actual_color = ctrl.overrideColorRGB.get()
        self.assertAlmostEqual(actual_color[0], 1.0)
        self.assertAlmostEqual(actual_color[1], 0.0)
        self.assertAlmostEqual(actual_color[2], 0.0)

    def test_create_with_axis(self):
        """Test creating controls with different axes."""
        # Default is Y-up (normal=(0,1,0))
        # Z-up should be rotated 90 deg on X
        ctrl_z = mtk.Controls.circle(name="testAxisZ", axis="z")

        # Check rotation values after creation (assuming freeze=True by default)
        # If freeze=True, rotation should be 0, but the shape points should be modified.
        # If we want to verify orientation, we might check without freeze or check shape points.
        # Let's check with freeze=False to verify the rotation logic.
        ctrl_x_nofreeze = mtk.Controls.circle(name="testAxisX", axis="x", freeze=False)
        rot = ctrl_x_nofreeze.getRotation()
        # X axis usually means -90 on Z or similar depending on implementation
        # Implementation: x -> (0, 0, -90)
        self.assertAlmostEqual(rot[2], -90.0)

    def test_create_with_size(self):
        """Test creating a control with a specific size."""
        # Size 2.0
        ctrl = mtk.Controls.circle(name="testSize", size=2.0, freeze=False)
        # Scale should be 2.0 if not frozen
        self.assertAlmostEqual(ctrl.sx.get(), 2.0)
        self.assertAlmostEqual(ctrl.sy.get(), 2.0)
        self.assertAlmostEqual(ctrl.sz.get(), 2.0)

    def test_create_with_parent(self):
        """Test creating a control parented to another node."""
        parent_node = pm.group(em=True, n="testParent")

        # Case 1: With offset group (default)
        ctrl = mtk.Controls.circle(name="testChild", parent=parent_node)
        # ctrl is the control, its parent is the group, group's parent is parent_node
        grp = ctrl.getParent()
        self.assertEqual(grp.getParent(), parent_node)

        # Case 2: Without offset group
        ctrl2 = mtk.Controls.circle(
            name="testChild2", parent=parent_node, offset_group=False
        )
        self.assertEqual(ctrl2.getParent(), parent_node)

    def test_create_no_freeze(self):
        """Test creating a control without freezing transforms."""
        ctrl = mtk.Controls.circle(name="testNoFreeze", size=2.0, freeze=False)
        self.assertNotAlmostEqual(ctrl.sx.get(), 1.0)
        self.assertAlmostEqual(ctrl.sx.get(), 2.0)

    def test_return_nodes_dataclass(self):
        """Verify return_nodes=True returns a ControlNodes object."""
        result = mtk.Controls.circle(name="testReturnNodes", return_nodes=True)

        # Check type (it's a dataclass, so we check fields)
        self.assertTrue(hasattr(result, "control"))
        self.assertTrue(hasattr(result, "group"))

        self.assertNodeExists("testReturnNodes_CTRL")
        self.assertEqual(result.control.name(), "testReturnNodes_CTRL")
        self.assertEqual(result.group.name(), "testReturnNodes_CTRL_GRP")

    def test_combine_controls(self):
        """Test combining multiple controls into one."""
        c1 = mtk.Controls.circle(name="c1", offset_group=False)
        c2 = mtk.Controls.square(name="c2", offset_group=False)

        # Move c2 so we can distinguish shapes if needed
        pm.move(c2, 2, 0, 0)

        combined = mtk.Controls.combine([c1, c2], name="combined")

        self.assertNodeExists("combined_CTRL")
        # Should have multiple shapes (circle has 1, square has 1)
        shapes = combined.getShapes()
        self.assertGreaterEqual(len(shapes), 2)

        # Sources should be deleted by default
        self.assertFalse(pm.objExists("c1_CTRL"))
        self.assertFalse(pm.objExists("c2_CTRL"))

    def test_invalid_axis_raises_error(self):
        """Verify invalid axis raises ValueError."""
        with self.assertRaises(ValueError):
            mtk.Controls.circle(name="testInvalidAxis", axis="invalid")

    def test_empty_text_raises_error(self):
        """Verify empty text raises ValueError."""
        if pm.about(batch=True):
            return

        with self.assertRaises(ValueError):
            mtk.Controls.text(name="testEmptyText", text="")


if __name__ == "__main__":
    unittest.main(verbosity=2)
