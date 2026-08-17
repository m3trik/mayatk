# !/usr/bin/python
# coding=utf-8
"""Tests for ``display_utils.color_id`` — the Color ID engine.

Pins the reset contract shared with the blendertk twin (whose suite asserts
"reset keeps non-ID materials"): a Material-channel reset sweeps only the
tool's own ``ID_*`` materials, never the user's, and an ID material still
assigned to another object survives.
"""
import unittest

try:
    import maya.cmds as cmds
except ImportError as exc:
    raise RuntimeError(
        "These tests must run inside a Maya session (standalone or GUI)."
    ) from exc

from base_test import MayaTkTestCase
from mayatk.display_utils.color_id import ColorId

RED = (1.0, 0.0, 0.0)
RED_MAT = "ID_FF_00_00"


def _assign_user_material(obj: str, name: str) -> str:
    mat = cmds.shadingNode("lambert", asShader=True, name=name)
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG")
    cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
    cmds.sets(obj, edit=True, forceElement=sg)
    return mat


class TestResetMaterialScope(MayaTkTestCase):
    def test_reset_keeps_non_id_materials(self):
        """A user's own material must survive a Material-channel reset."""
        cube = cmds.polyCube(name="colorid_keep_cube")[0]
        user_mat = _assign_user_material(cube, "KeepMe")

        ColorId.apply_color([cube], RED, apply_to_material=True)
        ColorId.reset_colors([cube])

        self.assertTrue(
            cmds.objExists(user_mat),
            "reset_colors deleted the user's material — only ID_* may be swept",
        )

    def test_reset_keeps_user_material_without_id_color(self):
        """Reset on an object that never had an ID color is non-destructive."""
        cube = cmds.polyCube(name="colorid_plain_cube")[0]
        user_mat = _assign_user_material(cube, "KeepMePlain")

        ColorId.reset_colors([cube])

        self.assertTrue(cmds.objExists(user_mat))

    def test_reset_sweeps_orphaned_id_materials(self):
        """The tool's own ID material (and its SG) is removed once no object
        uses it — its shading group stays wired to ``outColor`` even with no
        members, so a connection-based sweep can never fire and orphaned
        ``ID_*`` networks accumulated one per applied color."""
        cube = cmds.polyCube(name="colorid_sweep_cube")[0]
        ColorId.apply_color([cube], RED, apply_to_material=True)
        self.assertTrue(cmds.objExists(RED_MAT), "fixture: ID material missing")

        ColorId.reset_colors([cube])

        self.assertFalse(
            cmds.objExists(RED_MAT),
            "reset left the tool's own orphaned ID material in the scene",
        )

    def test_reset_keeps_id_material_still_assigned_elsewhere(self):
        """An ID material another object still uses must NOT be swept."""
        cube_a = cmds.polyCube(name="colorid_shared_a")[0]
        cube_b = cmds.polyCube(name="colorid_shared_b")[0]
        ColorId.apply_color([cube_a, cube_b], RED, apply_to_material=True)

        ColorId.reset_colors([cube_a])

        self.assertTrue(
            cmds.objExists(RED_MAT),
            "reset swept an ID material that another object still uses",
        )


if __name__ == "__main__":
    unittest.main()
