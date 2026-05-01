# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.mat_utils.mat_transfer module.

Covers MatTransfer — material assignment collection and transfer between
objects, including namespace-stripping and fuzzy shape-name matching.
"""
import unittest

import maya.cmds as cmds

from mayatk.mat_utils.mat_transfer import MatTransfer

from base_test import MayaTkTestCase, QuickTestCase


class TestMatTransferStaticHelpers(QuickTestCase):
    """Pure-Python helpers."""

    def test_clean_namespace_name_strips_namespace(self):
        self.assertEqual(
            MatTransfer._clean_namespace_name("namespace:material"), "material"
        )

    def test_clean_namespace_name_no_namespace(self):
        self.assertEqual(MatTransfer._clean_namespace_name("material"), "material")

    def test_clean_namespace_name_nested_namespaces(self):
        # split picks the last segment after final ':'
        self.assertEqual(
            MatTransfer._clean_namespace_name("a:b:final"), "final"
        )


class TestMatTransferTypeChecks(MayaTkTestCase):
    """is_material_related_node — runtime cmds-based check."""

    def setUp(self):
        super().setUp()
        self.transfer = MatTransfer()

    def test_blinn_is_material_related(self):
        shader = cmds.shadingNode("blinn", asShader=True, name="mt_blinn")
        self.assertTrue(self.transfer.is_material_related_node(shader))

    def test_lambert_is_material_related(self):
        shader = cmds.shadingNode("lambert", asShader=True, name="mt_lambert")
        self.assertTrue(self.transfer.is_material_related_node(shader))

    def test_polymesh_is_not_material_related(self):
        cube = cmds.polyCube(name="mt_cube")[0]
        self.assertFalse(self.transfer.is_material_related_node(cube))

    def test_nonexistent_node_returns_false(self):
        self.assertFalse(self.transfer.is_material_related_node("does_not_exist"))

    def test_non_string_returns_false(self):
        self.assertFalse(self.transfer.is_material_related_node(42))


class TestMatTransferAssignments(MayaTkTestCase):
    """get_material_assignments and handle_object_materials."""

    def setUp(self):
        super().setUp()
        self.transfer = MatTransfer()

    def test_get_assignments_default_lambert(self):
        cube = cmds.polyCube(name="mt_cube_default")[0]
        # Fresh cube has the default lambert1 -> initialShadingGroup
        result = self.transfer.get_material_assignments(cube)
        self.assertIsInstance(result, dict)
        # Should contain the cube shape name with at least one shading engine
        self.assertTrue(len(result) >= 1)
        for shape_name, sgs in result.items():
            self.assertIsInstance(sgs, list)
            self.assertTrue(any("ShadingGroup" in sg or sg.endswith("SG") for sg in sgs))

    def test_get_assignments_custom_material(self):
        cube = cmds.polyCube(name="mt_cube_custom")[0]
        shader = cmds.shadingNode("blinn", asShader=True, name="mt_custom_blinn")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="mt_custom_SG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)

        result = self.transfer.get_material_assignments(cube)
        # Verify our custom shading engine appears in the assignments
        all_sgs = set()
        for sgs in result.values():
            all_sgs.update(sgs)
        self.assertIn(sg, all_sgs)

    def test_get_assignments_nonexistent_object_returns_empty(self):
        result = self.transfer.get_material_assignments("does_not_exist")
        self.assertEqual(result, {})

    def test_collect_alias_calls_get_material_assignments(self):
        cube = cmds.polyCube(name="mt_collect_cube")[0]
        a = self.transfer.collect_material_assignments(cube)
        b = self.transfer.get_material_assignments(cube)
        self.assertEqual(a, b)


class TestMatTransferFuzzyMatching(QuickTestCase):
    """_find_matching_materials — fuzzy shape-name lookup."""

    def setUp(self):
        super().setUp()
        self.transfer = MatTransfer()

    def test_direct_match(self):
        result = self.transfer._find_matching_materials(
            "pCubeShape1", {"pCubeShape1": ["sg1"]}
        )
        self.assertEqual(result, ["sg1"])

    def test_fuzzy_match_strips_shape_suffix_digits(self):
        # "pCubeShape1" -> stripped to "pCube" — should match "pCube" key
        result = self.transfer._find_matching_materials(
            "pCubeShape1", {"pCube": ["sg_cube"]}
        )
        self.assertEqual(result, ["sg_cube"])

    def test_no_match_returns_empty(self):
        result = self.transfer._find_matching_materials(
            "pCubeShape1", {"pSphere": ["sg_sphere"]}
        )
        self.assertEqual(result, [])


class TestMatTransferHandle(MayaTkTestCase):
    """handle_object_materials — copies shading network to a target."""

    def setUp(self):
        super().setUp()
        self.transfer = MatTransfer()

    def test_handle_with_no_assignments_is_noop(self):
        cube = cmds.polyCube(name="mt_handle_noop")[0]
        self.transfer.handle_object_materials(cube, {})
        # Should not raise

    def test_handle_with_nonexistent_target_is_noop(self):
        self.transfer.handle_object_materials(
            "does_not_exist", {"someShape": ["someSG"]}
        )

    def test_handle_assigns_existing_local_material(self):
        # Source mesh with custom shader
        src = cmds.polyCube(name="mt_src")[0]
        shader = cmds.shadingNode("blinn", asShader=True, name="mt_handle_blinn")
        sg = cmds.sets(
            renderable=True,
            noSurfaceShader=True,
            empty=True,
            name="mt_handle_SG",
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(src, edit=True, forceElement=sg)

        # Target mesh with same shape stem to allow matching
        tgt = cmds.polyCube(name="mt_src")[0]  # Maya will rename to mt_src1
        # Get assignments on src and apply to tgt
        assignments = self.transfer.get_material_assignments(src)
        self.transfer.handle_object_materials(tgt, assignments)


if __name__ == "__main__":
    unittest.main()
