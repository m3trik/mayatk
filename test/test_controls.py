# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.rig_utils.controls

Covers:
- Dynamic preset access (Controls.diamond, etc.)
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

import maya.cmds as cmds
import mayatk as mtk

from base_test import MayaTkTestCase


class TestControls(MayaTkTestCase):
    def tearDown(self):
        for obj in cmds.ls("*_CTRL", "*_CTRL_GRP", type="transform"):
            try:
                if cmds.objExists(obj):
                    cmds.delete(obj)
            except Exception:
                pass
        super().tearDown()

    def test_dynamic_preset_diamond(self):
        ctrl = mtk.Controls.diamond(name="testDiamond", size=2.0, axis="y")
        self.assertNodeExists("testDiamond_CTRL")
        self.assertNodeExists("testDiamond_CTRL_GRP")
        self.assertNodeType(ctrl, "transform")

    def test_dynamic_preset_box_no_group(self):
        ctrl = mtk.Controls.box(name="testBox", offset_group=False)
        self.assertNodeExists("testBox_CTRL")
        self.assertFalse(cmds.objExists("testBox_CTRL_GRP"))
        self.assertNodeType(ctrl, "transform")

    def test_match_to_object(self):
        cube = cmds.polyCube(name="matchTarget")[0]
        cmds.xform(cube, ws=True, t=(5, 6, 7), ro=(10, 20, 30))

        nodes = mtk.Controls.create(
            "diamond",
            name="matchMe",
            match=cube,
            return_nodes=True,
        )
        grp = nodes.group
        self.assertIsNotNone(grp)

        pos = cmds.xform(grp, q=True, ws=True, t=True)
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
            return cmds.curve(name=name, p=pts, d=1)

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
        if cmds.about(batch=True):
            print("Skipping text test in batch mode (textCurves unavailable)")
            return

        # Text is standalone-only; combine() can merge when desired.
        ctrl = mtk.Controls.target(name="withText", offset_group=False)
        txt = mtk.Controls.text(name="withTextLabel", text="FK", offset_group=False)

        combined = mtk.Controls.combine([ctrl, txt], name="withTextCombined_CTRL")
        self.assertNodeExists("withTextCombined_CTRL")
        self.assertNodeType(combined, "transform")

    def test_text_standalone(self):
        if cmds.about(batch=True):
            print("Skipping text test in batch mode (textCurves unavailable)")
            return

        txt = mtk.Controls.text(name="standalone", text="Hello", offset_group=False)
        self.assertNodeExists("standalone_CTRL")
        self.assertNodeType(txt, "transform")

    def test_all_builtin_styles_smoke(self):
        presets = [
            ("diamond", {}),
            ("arrow", {}),
            ("two_way_arrow", {}),
            ("four_way_arrow", {}),
            ("target", {}),
            ("box", {}),
            ("beveled_cube", {}),
            ("ball", {}),
            ("chevron", {}),
            ("torus", {}),
            ("helix", {}),
            ("geosphere", {}),
            ("pyramid", {}),
            ("star", {}),
            ("text", {"text": "X"}),
        ]

        for preset, kwargs in presets:
            if preset == "text" and cmds.about(batch=True):
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
        for obj in cmds.ls("*_CTRL", "*_CTRL_GRP", "test*", type="transform"):
            try:
                if cmds.objExists(obj):
                    cmds.delete(obj)
            except Exception:
                pass
        super().tearDown()

    def test_create_with_color_int(self):
        """Test creating a control with an integer color index."""
        ctrl = mtk.Controls.diamond(name="testColorInt", color=17)
        self.assertTrue(cmds.getAttr(f"{ctrl}.overrideEnabled"))
        self.assertEqual(cmds.getAttr(f"{ctrl}.overrideColor"), 17)

    def test_create_with_color_tuple(self):
        """Test creating a control with an RGB tuple color."""
        color = (1.0, 0.0, 0.0)
        ctrl = mtk.Controls.diamond(name="testColorTuple", color=color)

        # RGB colors use overrideRGBColors=True (Maya 2016+)
        self.assertTrue(cmds.getAttr(f"{ctrl}.overrideEnabled"))
        self.assertTrue(cmds.getAttr(f"{ctrl}.overrideRGBColors"))

        # Check color values (approximate float comparison)
        actual_color = cmds.getAttr(f"{ctrl}.overrideColorRGB")[0]
        self.assertAlmostEqual(actual_color[0], 1.0)
        self.assertAlmostEqual(actual_color[1], 0.0)
        self.assertAlmostEqual(actual_color[2], 0.0)

    def test_create_with_axis(self):
        """Test creating controls with different axes."""
        ctrl_z = mtk.Controls.diamond(name="testAxisZ", axis="z")

        ctrl_x_nofreeze = mtk.Controls.diamond(name="testAxisX", axis="x", freeze=False)
        rot = cmds.xform(ctrl_x_nofreeze, q=True, ro=True)
        # X axis usually means -90 on Z or similar depending on implementation
        # Implementation: x -> (0, 0, -90)
        self.assertAlmostEqual(rot[2], -90.0)

    def test_create_with_size(self):
        """Test creating a control with a specific size."""
        ctrl = mtk.Controls.diamond(name="testSize", size=2.0, freeze=False)
        # Scale should be 2.0 if not frozen
        self.assertAlmostEqual(cmds.getAttr(f"{ctrl}.sx"), 2.0)
        self.assertAlmostEqual(cmds.getAttr(f"{ctrl}.sy"), 2.0)
        self.assertAlmostEqual(cmds.getAttr(f"{ctrl}.sz"), 2.0)

    def test_create_with_parent(self):
        """Test creating a control parented to another node."""
        parent_node = cmds.group(em=True, n="testParent")

        # Case 1: With offset group (default)
        ctrl = mtk.Controls.diamond(name="testChild", parent=parent_node)
        # ctrl is the control, its parent is the group, group's parent is parent_node
        grp = (cmds.listRelatives(ctrl, parent=True, fullPath=True) or [None])[0]
        grp_parent = (cmds.listRelatives(grp, parent=True, fullPath=True) or [None])[0]
        self.assertEqual(str(grp_parent).split("|")[-1], str(parent_node).split("|")[-1])

        # Case 2: Without offset group
        ctrl2 = mtk.Controls.diamond(
            name="testChild2", parent=parent_node, offset_group=False
        )
        ctrl2_parent = (cmds.listRelatives(ctrl2, parent=True, fullPath=True) or [None])[0]
        self.assertEqual(str(ctrl2_parent).split("|")[-1], str(parent_node).split("|")[-1])

    def test_create_no_freeze(self):
        """Test creating a control without freezing transforms."""
        ctrl = mtk.Controls.diamond(name="testNoFreeze", size=2.0, freeze=False)
        self.assertNotAlmostEqual(cmds.getAttr(f"{ctrl}.sx"), 1.0)
        self.assertAlmostEqual(cmds.getAttr(f"{ctrl}.sx"), 2.0)

    def test_return_nodes_dataclass(self):
        """Verify return_nodes=True returns a ControlNodes object."""
        result = mtk.Controls.diamond(name="testReturnNodes", return_nodes=True)

        # Check type (it's a dataclass, so we check fields)
        self.assertTrue(hasattr(result, "control"))
        self.assertTrue(hasattr(result, "group"))

        self.assertNodeExists("testReturnNodes_CTRL")
        self.assertEqual(result.control, "testReturnNodes_CTRL")
        self.assertEqual(result.group, "testReturnNodes_CTRL_GRP")

    def test_combine_controls(self):
        """Test combining multiple controls into one."""
        c1 = mtk.Controls.diamond(name="c1", offset_group=False)
        c2 = mtk.Controls.arrow(name="c2", offset_group=False)

        # Move c2 so we can distinguish shapes if needed
        cmds.move(2, 0, 0, c2)

        combined = mtk.Controls.combine([c1, c2], name="combined")

        self.assertNodeExists("combined_CTRL")
        # Should have multiple shapes (circle has 1, square has 1)
        shapes = cmds.listRelatives(combined, shapes=True, ni=True) or []
        self.assertGreaterEqual(len(shapes), 2)

        # Sources should be deleted by default
        self.assertFalse(cmds.objExists("c1_CTRL"))
        self.assertFalse(cmds.objExists("c2_CTRL"))

    def test_shapes_lists_presets(self):
        """shapes() returns sorted preset names (parity with blendertk.Controls.shapes)."""
        shapes = mtk.Controls.shapes()
        for expected in ("circle", "square", "diamond", "box", "cube", "ball", "arrow"):
            self.assertIn(expected, shapes)
        self.assertEqual(shapes, sorted(shapes))

    def test_dir_exposes_presets(self):
        """Dynamic presets should be discoverable via dir() for tab-completion."""
        listing = dir(mtk.Controls)
        self.assertIn("diamond", listing)
        self.assertIn("circle", listing)

    def test_circle_and_square_presets(self):
        c = mtk.Controls.circle(name="testCircle", offset_group=False)
        s = mtk.Controls.square(name="testSquare", offset_group=False)
        self.assertNodeExists("testCircle_CTRL")
        self.assertNodeExists("testSquare_CTRL")
        for node in (c, s):
            shapes = cmds.listRelatives(node, shapes=True, type="nurbsCurve") or []
            self.assertGreaterEqual(len(shapes), 1)

    def test_cube_alias(self):
        """'cube' is an alias of 'box' (blendertk parity)."""
        ctrl = mtk.Controls.create("cube", name="testCubeAlias", offset_group=False)
        self.assertNodeExists("testCubeAlias_CTRL")
        self.assertNodeType(ctrl, "transform")

    def test_register_custom_preset_before_builtins(self):
        """Registering a custom preset first must not block builtin registration."""
        saved = dict(mtk.Controls._PRESETS)
        saved_flag = getattr(mtk.Controls, "_builtins_registered", False)
        try:
            mtk.Controls._PRESETS.clear()
            mtk.Controls._builtins_registered = False

            def _build_tri(*, name, axis="y", **_):
                pts = [(0, 0, 0), (1, 0, 0), (0, 0, 1), (0, 0, 0)]
                return cmds.curve(name=name, p=pts, d=1)

            mtk.Controls.register_preset("tri_custom", _build_tri)
            # Builtins must still be reachable after a custom preset was added first.
            ctrl = mtk.Controls.create(
                "diamond", name="testAfterCustom", offset_group=False
            )
            self.assertNodeExists("testAfterCustom_CTRL")
            self.assertNodeType(ctrl, "transform")
        finally:
            mtk.Controls._PRESETS.clear()
            mtk.Controls._PRESETS.update(saved)
            mtk.Controls._builtins_registered = saved_flag

    def test_combine_preserves_world_positions(self):
        """Merged shapes keep their world placement, not just the first source's."""
        c1 = mtk.Controls.diamond(name="cwp1", offset_group=False)
        c2 = mtk.Controls.diamond(name="cwp2", offset_group=False)
        cmds.move(5, 0, 0, c2)

        combined = mtk.Controls.combine([c1, c2], name="cwpCombined")
        bb = cmds.exactWorldBoundingBox(combined)
        # xmin still covers c1 at the origin; xmax reaches c2 at x=5.
        self.assertLess(bb[0], -0.5)
        self.assertGreater(bb[3], 4.0)

    def test_combine_keep_sources_intact(self):
        """delete_sources=False leaves the originals untouched (shapes + placement)."""
        c1 = mtk.Controls.diamond(name="keep1", offset_group=False)
        c2 = mtk.Controls.diamond(name="keep2", offset_group=False)
        cmds.move(3, 0, 0, c2)

        combined = mtk.Controls.combine(
            [c1, c2], name="keepCombined", delete_sources=False
        )

        for src in ("keep1_CTRL", "keep2_CTRL"):
            self.assertTrue(cmds.objExists(src))
            shapes = cmds.listRelatives(src, shapes=True) or []
            self.assertGreaterEqual(len(shapes), 1, f"{src} lost its shapes")
        pos = cmds.xform("keep2_CTRL", q=True, ws=True, t=True)
        self.assertAlmostEqual(pos[0], 3.0, places=3)

        self.assertNodeExists("keepCombined_CTRL")
        merged = cmds.listRelatives(combined, shapes=True) or []
        self.assertGreaterEqual(len(merged), 2)

    def test_invalid_axis_raises_error(self):
        """Verify invalid axis raises ValueError."""
        with self.assertRaises(ValueError):
            mtk.Controls.diamond(name="testInvalidAxis", axis="invalid")

    def test_empty_text_raises_error(self):
        """Verify empty text raises ValueError."""
        if cmds.about(batch=True):
            return

        with self.assertRaises(ValueError):
            mtk.Controls.text(name="testEmptyText", text="")


if __name__ == "__main__":
    unittest.main(verbosity=2)
