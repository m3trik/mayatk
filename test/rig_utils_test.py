# !/usr/bin/python
# coding=utf-8
import unittest
import pymel.core as pm
import mayatk as mtk


class RigUtilsTest(unittest.TestCase):
    def setUp(self):
        """Set up test scene for each test."""
        pm.mel.file(new=True, force=True)
        self.loc = pm.spaceLocator(name="loc")
        self.cyl = (
            pm.polyCylinder(
                radius=5,
                height=10,
                subdivisionsX=6,
                subdivisionsY=1,
                subdivisionsZ=1,
                name="cyl",
            )[0]
            if not pm.objExists("cyl")
            else pm.PyNode("cyl")
        )

    def test_create_locator(self):
        result = mtk.create_locator("_loc")
        self.assertEqual(result, "_loc")

    def test_remove_locator(self):
        result = mtk.remove_locator("loc")
        self.assertEqual(result, None)

    def test_set_attr_lock_state(self):
        result = mtk.set_attr_lock_state("cyl")
        self.assertEqual(result, None)

    def test_create_group(self):
        result = mtk.create_group(name="emptyGrp").name()
        self.assertEqual(result, "emptyGrp")

    def test_create_locator_at_object(self):
        result = mtk.create_locator_at_object("cyl")
        self.assertEqual(result, None)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import importlib

    importlib.reload(mtk.edit_utils)
    mtk.clear_scrollfield_reporters()

    # Create a Test Suite
    suite = unittest.TestSuite()

    # Add the test case class to the suite
    suite.addTest(unittest.makeSuite(RigUtilsTest))

    # Run the suite
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
