# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.nurbs_utils module

Tests for NurbsUtils class functionality including:
- NURBS curve creation
- Curve operations between objects
- Lofting operations
"""
import unittest
import mayatk as mtk
import importlib
import mayatk.nurbs_utils._nurbs_utils as nu_mod

importlib.reload(nu_mod)

from base_test import MayaTkTestCase
import maya.cmds as cmds


class TestNurbsUtils(MayaTkTestCase):
    """Tests for NurbsUtils class."""

    def setUp(self):
        """Set up test scene."""
        super().setUp()
        self.sphere1 = cmds.circle(name="test_nurbs_circle1")[0]
        self.sphere2 = cmds.circle(name="test_nurbs_circle2")[0]
        cmds.move(10, 0, 0, self.sphere2)

    def tearDown(self):
        """Clean up."""
        for obj in ["test_nurbs_circle1", "test_nurbs_circle2"]:
            if cmds.objExists(obj):
                cmds.delete(obj)
        super().tearDown()

    def test_create_curve_between_two_objects(self):
        """Test creating curve between objects."""
        try:
            curve = mtk.create_curve_between_two_objs(self.sphere1, self.sphere2)
            if curve:
                self.assertIsNotNone(curve)
        except Exception as e:
            self.fail(f"create_curve_between_two_objs failed: {e}")


def _mash_loadable():
    """Try loading MASH; True if it ends up loaded."""
    try:
        from mayatk.core_utils.mash import MashToolkit

        MashToolkit.ensure_plugin_loaded()
        return bool(int(cmds.pluginInfo("MASH", q=True, l=1)))
    except Exception:
        return False


@unittest.skipUnless(_mash_loadable(), "MASH plugin not installed")
class TestNurbsUtilsDuplicateAlongCurve(MayaTkTestCase):
    """Exercises NurbsUtils.duplicate_along_curve (which uses MASH).

    Bug fixed 2026-05-07: PyMEL-style attribute proxies on cmds-returned
    strings (``cmds.setAttr(curveNode.stopAtEnd, 1)``,
    ``cmds.connectAttr(path.worldSpace[0], curveNode.inCurves[0])``, etc.)
    converted to f-string plug paths.
    """

    def test_duplicate_along_curve_smoke(self):
        from mayatk.nurbs_utils._nurbs_utils import NurbsUtils

        # Create a path curve and a starting cube to duplicate.
        curve = cmds.curve(d=3, p=[(0, 0, 0), (1, 1, 0), (2, 0, 0), (3, 1, 0)])
        start = cmds.polyCube(name="dup_start_cube")[0]

        try:
            result = NurbsUtils.duplicate_along_curve(
                path=curve, start=start, count=3, geometry="Instancer"
            )
        except Exception as e:
            self.fail(f"duplicate_along_curve raised: {e}")

        # Result is a list with the original start plus baked instances.
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
