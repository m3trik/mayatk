# !/usr/bin/python
# coding=utf-8
import unittest
import pymel.core as pm
import mayatk as mtk


class XformUtilsTest(unittest.TestCase):
    """Unit tests for the XformUtils class"""

    def setUp(self):
        """Set up test scene"""
        pm.mel.file(new=True, force=True)
        self.cube1 = pm.polyCube(name="cube1")[0]
        self.cube2 = pm.polyCube(name="cube2")[0]
        self.sph = pm.polySphere(name="sph")[0]

    def tearDown(self):
        """Clean up test scene"""
        pm.delete(self.cube1, self.cube2, self.sph)

    def test_move_to(self):
        self.assertEqual(mtk.move_to(self.cube1, self.cube2), None)

    def test_drop_to_grid(self):
        self.assertEqual(
            mtk.drop_to_grid(
                self.cube1,
                align="Min",
                origin=True,
                center_pivot=True,
                freeze_transforms=True,
            ),
            None,
        )

    def test_reset_translation(self):
        self.assertEqual(mtk.reset_translation(self.cube1), None)

    def test_set_translation_to_pivot(self):
        self.assertEqual(mtk.set_translation_to_pivot(self.cube1), None)

    def test_align_pivot_to_selection(self):
        self.assertEqual(mtk.align_pivot_to_selection(self.cube1, self.cube2), None)

    def test_aim_object_at_point(self):
        self.assertEqual(
            mtk.aim_object_at_point([self.cube1, self.cube2], (0, 15, 15)), None
        )

    def test_rotate_axis(self):
        self.assertEqual(mtk.rotate_axis([self.cube1, self.cube2], (0, 15, 15)), None)

    def test_get_orientation(self):
        self.assertEqual(
            mtk.get_orientation(self.cube1), ([1, 0, 0], [0, 1, 0], [0, 0, 1])
        )

    def test_get_dist_between_two_objects(self):
        mtk.drop_to_grid([self.cube1, self.cube2], origin=True, center_pivot=True)
        pm.move(self.cube2, 0, 0, 15)
        self.assertEqual(mtk.get_dist_between_two_objects(self.cube1, self.cube2), 15)

    def test_get_center_point(self):
        self.assertEqual(mtk.get_center_point(self.sph), (0, 0, 0))

    def test_get_bounding_box(self):
        self.assertEqual(
            mtk.get_bounding_box(self.sph, "size"),
            (2.000000238418579, 2.0, 2.0000005960464478),
        )

    def test_sort_by_bounding_box_value(self):
        self.assertEqual(
            str(mtk.sort_by_bounding_box_value(["sph.vtx[0]", "sph.f[0]"])),
            "[MeshVertex('sphShape.vtx[0]'), MeshFace('sphShape.f[0]')]",
        )

    def test_match_scale(self):
        self.assertEqual(
            mtk.match_scale(self.cube1, self.cube2, scale=False), [1.0, 1.0, 1.0]
        )

    def test_bake_custom_pivot(self):
        with self.subTest(msg="No arguments"):
            result = mtk.bake_pivot("sph")
            self.assertEqual(result, None)
        with self.subTest(msg="Position argument"):
            result = mtk.bake_pivot("sph", position=True)
            self.assertEqual(result, None)
        with self.subTest(msg="Orientation argument"):
            result = mtk.bake_pivot("sph", orientation=True)
            self.assertEqual(result, None)

    def test_reset_pivot_transforms(self):
        result = mtk.reset_pivot_transforms("sph")
        self.assertEqual(result, None)

    def test_align_using_three_points(self):
        pass
        # self.assertEqual(mtk.align_using_three_points(), None)

    def test_is_overlapping(self):
        pass
        # self.assertEqual(mtk.is_overlapping(), None)

    def test_align_vertices(self):
        pass
        # self.assertEqual(mtk.align_vertices(), None)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    mtk.clear_scroll_field_reporters()

    # Create a Test Suite
    suite = unittest.TestSuite()

    # Add the test case class to the suite
    suite.addTest(unittest.makeSuite(XformUtilsTest))

    # Run the suite
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
