# !/usr/bin/python
# coding=utf-8
"""Test Suite for display_utils extras (color_id, exploded_view).

Covers:
    - ColorUtils / ColorId (color_id.py)
    - ExplodedView (exploded_view.py)
"""
import unittest

import maya.cmds as cmds

from mayatk.display_utils.color_id import ColorUtils, ColorId
from mayatk.display_utils.exploded_view import ExplodedView

from base_test import MayaTkTestCase, QuickTestCase


class TestColorUtilsStaticHelpers(QuickTestCase):
    """Pure helpers — no Maya needed."""

    def test_get_color_difference_zero(self):
        self.assertAlmostEqual(
            ColorUtils.get_color_difference((1, 0, 0), (1, 0, 0)), 0.0
        )

    def test_get_color_difference_max(self):
        self.assertAlmostEqual(
            ColorUtils.get_color_difference((0, 0, 0), (1, 1, 1)), 1.0
        )

    def test_get_color_difference_partial(self):
        # Average of |0.5-0|, |0.5-0|, |0.5-1| = (0.5+0.5+0.5)/3 = 0.5
        self.assertAlmostEqual(
            ColorUtils.get_color_difference((0.5, 0.5, 0.5), (0, 0, 1)), 0.5
        )


class TestDefaultSwatchColors(QuickTestCase):
    """The DEFAULT_SWATCH_COLORS palette on ColorId — pure data, no Maya."""

    def test_palette_has_12_colors(self):
        self.assertEqual(len(ColorId.DEFAULT_SWATCH_COLORS), 12)

    def test_palette_colors_are_valid_rgb_tuples(self):
        for color in ColorId.DEFAULT_SWATCH_COLORS:
            self.assertIsInstance(color, tuple)
            self.assertEqual(len(color), 3)
            for ch in color:
                self.assertGreaterEqual(ch, 0)
                self.assertLessEqual(ch, 255)

    def test_palette_colors_are_all_distinct(self):
        colors = ColorId.DEFAULT_SWATCH_COLORS
        self.assertEqual(len(colors), len(set(colors)))

    def test_palette_colors_are_desaturated(self):
        """Default colors should be muted (not fully saturated primaries)."""
        import colorsys

        for r, g, b in ColorId.DEFAULT_SWATCH_COLORS:
            _, sat, _ = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            # HSV saturation < 200/255 (~0.784) == muted; matches the
            # Qt QColor.saturation() < 200 threshold the palette was tuned for.
            self.assertLess(
                sat,
                200 / 255.0,
                f"Color {(r, g, b)} is too saturated ({sat:.3f})",
            )


class TestColorUtilsMaterial(MayaTkTestCase):
    """ColorUtils.assign_material — creates lambert + assigns."""

    def test_assign_material_creates_lambert(self):
        cube = cmds.polyCube(name="col_cube")[0]
        material = ColorUtils.assign_material(cube, (1.0, 0.0, 0.0))
        self.assertTrue(cmds.objExists(material))
        self.assertEqual(cmds.nodeType(material), "lambert")

    def test_assign_material_reuses_existing(self):
        cube_a = cmds.polyCube(name="col_a")[0]
        cube_b = cmds.polyCube(name="col_b")[0]
        m1 = ColorUtils.assign_material(cube_a, (1.0, 0.0, 0.0))
        m2 = ColorUtils.assign_material(cube_b, (1.0, 0.0, 0.0))
        # Same color should reuse the material
        self.assertEqual(m1, m2)

    def test_get_material_color_after_assignment(self):
        cube = cmds.polyCube(name="col_get")[0]
        material = ColorUtils.assign_material(cube, (0.5, 0.7, 0.9))

        # Read color directly from the lambert that was created
        rgb = cmds.getAttr(f"{material}.color")[0]
        self.assertAlmostEqual(rgb[0], 0.5, places=2)
        self.assertAlmostEqual(rgb[1], 0.7, places=2)
        self.assertAlmostEqual(rgb[2], 0.9, places=2)


class TestColorUtilsWireframe(MayaTkTestCase):
    """Wireframe overrides via overrideEnabled / overrideColorRGB."""

    def test_wireframe_color_initially_none(self):
        cube = cmds.polyCube(name="wire_cube")[0]
        self.assertIsNone(ColorUtils.get_wireframe_color(cube))

    def test_set_wireframe_color_via_color_attribute(self):
        cube = cmds.polyCube(name="wire_set_cube")[0]
        ColorUtils.set_color_attribute(
            cube, (1.0, 0.0, 0.0), attr_type="wireframe", force=True
        )
        color = ColorUtils.get_wireframe_color(cube, normalize=True)
        self.assertIsNotNone(color)
        self.assertAlmostEqual(color[0], 1.0, places=3)


class TestColorUtilsVertex(MayaTkTestCase):
    """Vertex color application."""

    def test_set_vertex_color(self):
        cube = cmds.polyCube(name="vtx_cube")[0]
        ColorUtils.set_vertex_color([cube], (0.0, 1.0, 0.0))
        # Vertex 0 should now have green color
        color = ColorUtils.get_vertex_color(cube, 0)
        self.assertIsNotNone(color)


class TestColorId(MayaTkTestCase):
    """ColorId.apply_color and reset_colors."""

    def test_apply_color_to_outliner(self):
        cube = cmds.polyCube(name="cm_out_cube")[0]
        ColorId.apply_color(
            [cube], color=(0.0, 1.0, 0.0), apply_to_outliner=True
        )
        # useOutlinerColor should be True
        self.assertTrue(cmds.getAttr(f"{cube}.useOutlinerColor"))

    def test_apply_color_random_when_none(self):
        cube = cmds.polyCube(name="cm_rand_cube")[0]
        # Should not raise — uses random color
        ColorId.apply_color([cube], apply_to_outliner=True)

    def test_reset_colors_runs(self):
        cube = cmds.polyCube(name="cm_reset_cube")[0]
        ColorId.apply_color(
            [cube], color=(1.0, 0.0, 0.0), apply_to_outliner=True
        )
        # Reset shouldn't raise
        ColorId.reset_colors([cube])


class TestExplodedView(MayaTkTestCase):
    """ExplodedView — basic explode/un_explode flow."""

    def setUp(self):
        super().setUp()
        # Reset class-level cache between tests
        ExplodedView.exploded_objects = {}

    def test_default_objects_none_falls_back_to_selection(self):
        ev = ExplodedView()
        cube = cmds.polyCube(name="ev_sel")[0]
        cmds.select(cube)
        # objects property falls back to current selection when not set
        self.assertIn(cube, ev.objects)

    def test_explicit_objects_assigned(self):
        cube = cmds.polyCube(name="ev_exp")[0]
        ev = ExplodedView(objects=[cube])
        self.assertEqual(ev.objects, [cube])

    def test_objects_setter(self):
        ev = ExplodedView()
        cube = cmds.polyCube(name="ev_set")[0]
        ev.objects = [cube]
        self.assertEqual(ev.objects, [cube])

    def test_get_target_objects_no_objects_returns_empty(self):
        ev = ExplodedView(objects=[])
        result = ev._get_target_objects()
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
