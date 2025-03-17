# !/usr/bin/python
# coding=utf-8
import unittest
import pymel.core as pm
import mayatk as mtk


class MatUtilsTest(unittest.TestCase):
    def setUp(self):
        """Set up the test scene for each test."""
        pm.mel.file(new=True, force=True)

    def test_get_mats(self):
        sphere = pm.polySphere()[0]
        lambert = pm.shadingNode("lambert", asShader=True)
        pm.select(sphere)
        pm.hyperShade(assign=lambert)
        mats = mtk.get_mats(sphere)
        self.assertIn(lambert, mats)
        face = sphere.f[0]
        face_mats = mtk.get_mats(face)
        self.assertIn(lambert, face_mats)

    def test_get_scene_mats(self):
        lambert = pm.shadingNode("lambert", asShader=True)
        scene_mats = mtk.get_scene_mats()
        self.assertIn(lambert, scene_mats)

    def test_get_fav_mats(self):
        fav_mats = mtk.get_fav_mats()
        self.assertIsNotNone(fav_mats)

    def test_create_mat(self):
        random_mat = mtk.create_mat(mat_type="random")
        self.assertIsNotNone(random_mat)
        lambert_mat = mtk.create_mat(mat_type="lambert")
        self.assertIsNotNone(lambert_mat)

    def test_assign_mat(self):
        sphere = pm.polySphere()[0]
        lambert = pm.shadingNode("lambert", asShader=True)
        mtk.assign_mat([sphere], lambert)
        mats = list(mtk.get_mats(sphere))
        self.assertIn(lambert, mats)

    def test_find_by_mat_id(self):
        pass  # TODO: Un-comment the body and update the test


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import importlib

    importlib.reload(mtk.edit_utils)
    mtk.clear_scrollfield_reporters()

    # Create a Test Suite
    suite = unittest.TestSuite()

    # Add the test case class to the suite
    suite.addTest(unittest.makeSuite(MatUtilsTest))

    # Run the suite
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
