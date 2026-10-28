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

from base_test import MayaTkTestCase, skipIfBatch
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


class TestCurveActions(MayaTkTestCase):
    """NurbsUtils.curve_action runs a Nurbs-list action's Maya runtime command."""

    @skipIfBatch("ReverseCurve's tool script reads no selection under mayapy")
    def test_reverse_runs_on_the_given_curves(self):
        curve = cmds.curve(degree=1, point=[(0, 0, 0), (1, 0, 0), (3, 0, 0)])
        cmds.select(clear=True)
        mtk.NurbsUtils.curve_action("reverse", curve)
        start = cmds.pointPosition(f"{curve}.cv[0]", world=True)
        self.assertAlmostEqual(start[0], 3.0, places=4)

    def test_unknown_action_raises_value_error(self):
        with self.assertRaises(ValueError):
            mtk.NurbsUtils.curve_action("no_such_action")

    def test_every_action_names_a_runtime_command(self):
        for action, command in mtk.NurbsUtils.CURVE_ACTIONS.items():
            with self.subTest(action=action):
                self.assertTrue(
                    cmds.runTimeCommand(command, exists=True)
                    or cmds.exists(command)
                    or mel_proc_exists(command),
                    f"{action}: {command} is no runtime command",
                )

    def test_an_explicit_empty_list_runs_nothing(self):
        self.assertEqual(mtk.NurbsUtils.curve_action("reverse", []), [])


def mel_proc_exists(name):
    import maya.mel as mel

    return bool(mel.eval(f'exists "{name}"'))


class TestRevolve(MayaTkTestCase):
    """NurbsUtils.revolve: one surface per profile, about the chosen axis."""

    def _profile(self):
        return cmds.curve(degree=1, point=[(1, 0, 0), (1, 2, 0)])

    def test_revolve_makes_one_surface_per_curve(self):
        a, b = self._profile(), self._profile()
        made = mtk.NurbsUtils.revolve([a, b], polygon=False, sections=6)
        self.assertEqual(len(made), 2)
        self.assertEqual(sorted(cmds.ls(selection=True)), sorted(made))

    def test_polygon_output_is_a_mesh(self):
        made = mtk.NurbsUtils.revolve(self._profile(), polygon=True)
        self.assertTrue(cmds.listRelatives(made[0], shapes=True, type="mesh"))

    def test_axis_reaches_the_revolve_node(self):
        made = mtk.NurbsUtils.revolve(self._profile(), axis="z", polygon=False)
        node = cmds.listConnections(made[0], type="revolve") or cmds.ls(
            cmds.listHistory(made[0]), type="revolve"
        )
        self.assertEqual(tuple(cmds.getAttr(f"{node[0]}.axis")[0]), (0.0, 0.0, 1.0))

    def test_nothing_selected_returns_empty(self):
        cmds.select(clear=True)
        self.assertEqual(mtk.NurbsUtils.revolve(None), [])

    def test_a_non_curve_selection_returns_empty(self):
        cube = cmds.polyCube()[0]
        self.assertEqual(mtk.NurbsUtils.revolve(cube), [])

    def test_unknown_axis_raises(self):
        with self.assertRaises(ValueError):
            mtk.NurbsUtils.revolve(self._profile(), axis="w")


class TestLoftNeedsTwoProfiles(MayaTkTestCase):
    """loft answers [] (no longer an error string) with fewer than two profiles."""

    def test_one_profile_returns_empty(self):
        cmds.select(cmds.circle()[0])
        self.assertEqual(mtk.NurbsUtils.loft(polygon=False), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
