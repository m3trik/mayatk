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
        # Text is standalone-only; combine() can merge when desired.
        ctrl = mtk.Controls.target(name="withText", offset_group=False)
        txt = mtk.Controls.text(name="withTextLabel", text="FK", offset_group=False)

        combined = mtk.Controls.combine([ctrl, txt], name="withTextCombined_CTRL")
        self.assertNodeExists("withTextCombined_CTRL")
        self.assertNodeType(combined, "transform")

    def test_text_standalone(self):
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
            node = mtk.Controls.create(
                preset,
                name=f"smoke_{preset}",
                offset_group=False,
                **kwargs,
            )
            self.assertNodeType(node, "transform")


if __name__ == "__main__":
    unittest.main(verbosity=2)
