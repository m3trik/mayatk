# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.xform_utils.matrices module.

Covers Matrices public surface — pure-math helpers, DAG transform helpers,
and node-graph builders for matrix-based rigging.
"""
import unittest

import maya.cmds as cmds
from maya.api.OpenMaya import MMatrix

from mayatk.xform_utils import matrices as mx
from mayatk.xform_utils.matrices import Matrices, get_matrix, set_matrix

from base_test import MayaTkTestCase


IDENTITY_FLAT = [
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
]


def _flatten(m: "MMatrix"):
    return [m.getElement(r, c) for r in range(4) for c in range(4)]


class TestMatrixModuleHelpers(MayaTkTestCase):
    """Tests for module-level get_matrix / set_matrix dispatch."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="mxh_cube")[0]

    def test_get_matrix_singular_attr_no_index(self):
        """``matrix`` and ``offsetParentMatrix`` are singular plugs — read without [index]."""
        cmds.move(2, 3, 4, self.cube)
        flat = get_matrix(self.cube, "matrix")
        self.assertEqual(len(flat), 16)
        # translation lives in the last row (Maya is row-major)
        self.assertAlmostEqual(flat[12], 2.0, places=4)
        self.assertAlmostEqual(flat[13], 3.0, places=4)
        self.assertAlmostEqual(flat[14], 4.0, places=4)

    def test_get_matrix_multi_instance_uses_index(self):
        """``worldMatrix`` is multi-instance and must be indexed.

        A freshly-created cube at the origin should have an identity world
        matrix — verify both length and values, not just length (the prior
        length-only assertion missed the case where get_matrix returned a
        zero matrix from a misindexed plug).
        """
        flat = get_matrix(self.cube, "worldMatrix", index=0)
        self.assertEqual(len(flat), 16)
        for a, b in zip(flat, IDENTITY_FLAT):
            self.assertAlmostEqual(a, b, places=5)

    def test_set_matrix_from_list(self):
        """Round-trip a flat list through set_matrix / get_matrix."""
        target = list(IDENTITY_FLAT)
        target[12], target[13], target[14] = 7.0, 8.0, 9.0
        set_matrix(self.cube, "offsetParentMatrix", target)
        got = get_matrix(self.cube, "offsetParentMatrix")
        for a, b in zip(got, target):
            self.assertAlmostEqual(a, b, places=4)

    def test_set_matrix_from_mmatrix(self):
        """MMatrix inputs are flattened automatically."""
        m = MMatrix(IDENTITY_FLAT)
        set_matrix(self.cube, "offsetParentMatrix", m)
        got = get_matrix(self.cube, "offsetParentMatrix")
        for a, b in zip(got, IDENTITY_FLAT):
            self.assertAlmostEqual(a, b, places=6)

    def test_set_matrix_wrong_length_raises(self):
        with self.assertRaises(ValueError):
            set_matrix(self.cube, "offsetParentMatrix", [1, 2, 3])


class TestMatrixMath(MayaTkTestCase):
    """Tests for pure-math members of Matrices (no Maya nodes required beyond identity)."""

    def test_identity(self):
        m = Matrices.identity()
        self.assertTrue(Matrices.is_identity(m))

    def test_to_mmatrix_from_list(self):
        m = Matrices.to_mmatrix(IDENTITY_FLAT)
        self.assertTrue(Matrices.is_identity(m))

    def test_to_mmatrix_from_mmatrix(self):
        src = MMatrix(IDENTITY_FLAT)
        m = Matrices.to_mmatrix(src)
        self.assertIs(m, src)

    def test_to_mmatrix_from_node(self):
        cube = cmds.polyCube(name="mxm_cube")[0]
        cmds.move(1, 2, 3, cube)
        m = Matrices.to_mmatrix(cube)
        t = Matrices.extract_translation(m)
        self.assertAlmostEqual(t[0], 1.0, places=4)
        self.assertAlmostEqual(t[1], 2.0, places=4)
        self.assertAlmostEqual(t[2], 3.0, places=4)

    def test_to_mmatrix_invalid_type_raises(self):
        with self.assertRaises(TypeError):
            Matrices.to_mmatrix(42)

    def test_local_matrix(self):
        cube = cmds.polyCube(name="mxm_local")[0]
        cmds.move(5, 0, 0, cube)
        m = Matrices.local_matrix(cube)
        t = Matrices.extract_translation(m)
        self.assertAlmostEqual(t[0], 5.0, places=4)

    def test_from_srt_translation_only(self):
        m = Matrices.from_srt(translate=(10.0, 20.0, 30.0))
        t, r, s = Matrices.decompose(m)
        self.assertAlmostEqual(t[0], 10.0, places=4)
        self.assertAlmostEqual(t[1], 20.0, places=4)
        self.assertAlmostEqual(t[2], 30.0, places=4)
        for v in r:
            self.assertAlmostEqual(v, 0.0, places=4)
        for v in s:
            self.assertAlmostEqual(v, 1.0, places=4)

    def test_from_srt_round_trip_translation(self):
        m = Matrices.from_srt(translate=(1.0, 2.0, 3.0))
        t, r, s = Matrices.decompose(m)
        self.assertAlmostEqual(t[0], 1.0, places=4)
        self.assertAlmostEqual(t[1], 2.0, places=4)
        self.assertAlmostEqual(t[2], 3.0, places=4)
        for v in s:
            self.assertAlmostEqual(v, 1.0, places=4)

    def test_from_srt_round_trip_rotation_only(self):
        m = Matrices.from_srt(rotate_euler_deg=(0.0, 45.0, 0.0))
        t, r, s = Matrices.decompose(m)
        # Y-axis rotation; X and Z should remain near zero
        self.assertAlmostEqual(r[1], 45.0, places=2)

    def test_from_srt_round_trip_uniform_scale(self):
        m = Matrices.from_srt(scale=(2.0, 2.0, 2.0))
        t, r, s = Matrices.decompose(m)
        for v in s:
            self.assertAlmostEqual(v, 2.0, places=3)

    def test_inverse_round_trip(self):
        m = Matrices.from_srt(translate=(7.0, 0.0, 0.0))
        m_inv = Matrices.inverse(m)
        product = Matrices.mult(m, m_inv)
        self.assertTrue(Matrices.is_identity(product, tolerance=1e-6))

    def test_mult_empty_returns_identity(self):
        m = Matrices.mult()
        self.assertTrue(Matrices.is_identity(m))

    def test_mult_single_passthrough(self):
        a = Matrices.from_srt(translate=(2.0, 0.0, 0.0))
        m = Matrices.mult(a)
        self.assertEqual(_flatten(m), _flatten(a))

    def test_world_to_local_inverse(self):
        parent_world = Matrices.from_srt(translate=(5.0, 0.0, 0.0))
        child_world = Matrices.from_srt(translate=(8.0, 0.0, 0.0))
        local = Matrices.world_to_local(child_world, parent_world)
        # local * parent_world should reconstruct child_world
        reconstructed = Matrices.local_to_world(local, parent_world)
        for a, b in zip(_flatten(reconstructed), _flatten(child_world)):
            self.assertAlmostEqual(a, b, places=5)

    def test_extract_translation(self):
        m = Matrices.from_srt(translate=(11.0, 22.0, 33.0))
        t = Matrices.extract_translation(m)
        self.assertAlmostEqual(t[0], 11.0, places=4)
        self.assertAlmostEqual(t[1], 22.0, places=4)
        self.assertAlmostEqual(t[2], 33.0, places=4)

    def test_is_identity_tolerance(self):
        m = Matrices.identity()
        self.assertTrue(Matrices.is_identity(m, tolerance=1e-6))

        nearly = list(IDENTITY_FLAT)
        nearly[12] = 1e-12
        self.assertTrue(Matrices.is_identity(MMatrix(nearly), tolerance=1e-9))

        clearly_not = Matrices.from_srt(translate=(1.0, 0.0, 0.0))
        self.assertFalse(Matrices.is_identity(clearly_not))


class TestDagTransforms(MayaTkTestCase):
    """Tests for the DAG-transform helpers."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="dag_cube")[0]

    def test_set_offset_parent_matrix(self):
        m = Matrices.from_srt(translate=(4.0, 0.0, 0.0))
        Matrices.set_offset_parent_matrix(self.cube, m)
        got = get_matrix(self.cube, "offsetParentMatrix")
        # translation row stores it in elements 12..14
        self.assertAlmostEqual(got[12], 4.0, places=4)

    def test_bake_world_matrix_to_transform(self):
        target = Matrices.from_srt(translate=(3.0, 4.0, 5.0))
        Matrices.bake_world_matrix_to_transform(self.cube, target)
        pos = cmds.xform(self.cube, q=True, ws=True, t=True)
        self.assertAlmostEqual(pos[0], 3.0, places=4)
        self.assertAlmostEqual(pos[1], 4.0, places=4)
        self.assertAlmostEqual(pos[2], 5.0, places=4)

    def test_freeze_to_offset_parent_matrix_zeros_local_trs(self):
        cmds.move(6, 7, 8, self.cube)
        cmds.rotate(0, 30, 0, self.cube)

        Matrices.freeze_to_offset_parent_matrix(self.cube)

        t = cmds.getAttr(f"{self.cube}.translate")[0]
        r = cmds.getAttr(f"{self.cube}.rotate")[0]
        s = cmds.getAttr(f"{self.cube}.scale")[0]
        self.assertEqual(tuple(t), (0.0, 0.0, 0.0))
        self.assertEqual(tuple(r), (0.0, 0.0, 0.0))
        self.assertEqual(tuple(s), (1.0, 1.0, 1.0))

        # World position should be preserved
        pos = cmds.xform(self.cube, q=True, ws=True, t=True)
        self.assertAlmostEqual(pos[0], 6.0, places=3)
        self.assertAlmostEqual(pos[1], 7.0, places=3)
        self.assertAlmostEqual(pos[2], 8.0, places=3)


class TestNodeBuilders(MayaTkTestCase):
    """Tests for matrix-graph node builders."""

    def test_ensure_node_with_name(self):
        node = Matrices.ensure_node("multMatrix", name="my_mmx")
        self.assertNodeExists(node)
        self.assertNodeType(node, "multMatrix")

    def test_ensure_node_without_name(self):
        node = Matrices.ensure_node("decomposeMatrix")
        self.assertNodeExists(node)
        self.assertNodeType(node, "decomposeMatrix")

    def test_build_mult_matrix_chain_creates_nodes_and_connects(self):
        a = cmds.polyCube(name="bmc_a")[0]
        b = cmds.polyCube(name="bmc_b")[0]
        cmds.move(2, 0, 0, a)

        mmx, dcmp = Matrices.build_mult_matrix_chain(
            [f"{a}.worldMatrix[0]", f"{b}.parentInverseMatrix[0]"],
            name="bmc_chain",
        )
        self.assertNodeType(mmx, "multMatrix")
        self.assertNodeType(dcmp, "decomposeMatrix")

        # matrixSum should be feeding the decomposeMatrix
        connections = cmds.listConnections(f"{dcmp}.inputMatrix", source=True, plugs=True) or []
        self.assertTrue(any(f"{mmx}.matrixSum" == c for c in connections))

    def test_drive_with_offset_parent_matrix(self):
        driver = cmds.spaceLocator(name="drive_loc")[0]
        ctl = cmds.spaceLocator(name="ctl_loc")[0]
        mmx = Matrices.drive_with_offset_parent_matrix(driver, ctl, name="drv")
        self.assertNodeType(mmx, "multMatrix")

        # offsetParentMatrix should be driven by mmx.matrixSum
        connections = cmds.listConnections(
            f"{ctl}.offsetParentMatrix", source=True, plugs=True
        ) or []
        self.assertTrue(any(f"{mmx}.matrixSum" == c for c in connections))

    def test_build_space_switch_creates_attribute_and_blend(self):
        ctl = cmds.spaceLocator(name="ss_ctl")[0]
        space_a = cmds.spaceLocator(name="ss_a")[0]
        space_b = cmds.spaceLocator(name="ss_b")[0]

        blnd = Matrices.build_space_switch(
            ctl, [space_a, space_b], attr_name="space", name="ss"
        )
        self.assertNodeType(blnd, "blendMatrix")
        self.assertTrue(cmds.attributeQuery("space", node=ctl, exists=True))

        # offsetParentMatrix should be driven by blendMatrix output
        connections = cmds.listConnections(
            f"{ctl}.offsetParentMatrix", source=True, plugs=True
        ) or []
        self.assertTrue(any(f"{blnd}.outputMatrix" == c for c in connections))

    def test_build_aim_matrix(self):
        src = cmds.spaceLocator(name="aim_src")[0]
        tgt = cmds.spaceLocator(name="aim_tgt")[0]
        cmds.move(5, 0, 0, tgt)

        aim = Matrices.build_aim_matrix(src, tgt, name="aim_n")
        self.assertNodeType(aim, "aimMatrix")

        # primaryTargetMatrix should be driven by tgt's worldMatrix
        self.assertTrue(
            cmds.isConnected(f"{tgt}.worldMatrix[0]", f"{aim}.primaryTargetMatrix")
        )

    def test_build_ikfk_blend(self):
        ik_chain = cmds.createNode("multMatrix", name="ik_chain_MMX")
        fk_chain = cmds.createNode("multMatrix", name="fk_chain_MMX")
        wrist = cmds.spaceLocator(name="wrist_CTL")[0]
        settings = cmds.spaceLocator(name="settings_CTL")[0]

        blnd = Matrices.build_ikfk_blend(
            ik_mx_attr=f"{ik_chain}.matrixSum",
            fk_mx_attr=f"{fk_chain}.matrixSum",
            parent_inv_attr=f"{wrist}.parentInverseMatrix[0]",
            out_target_ctl=wrist,
            switch_attr_owner=settings,
            switch_attr="ikFk",
            name="ikfk",
        )
        self.assertNodeType(blnd, "blendMatrix")
        self.assertTrue(cmds.attributeQuery("ikFk", node=settings, exists=True))


if __name__ == "__main__":
    unittest.main()
