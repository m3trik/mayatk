# !/usr/bin/python
# coding=utf-8
"""Test Suite for shader_attribute_map and shader_remapper modules."""
import unittest

import maya.cmds as cmds

from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap, ShaderAttrs
from mayatk.mat_utils.shader_remapper import ShaderRemapper

from base_test import MayaTkTestCase, QuickTestCase


class TestShaderAttributeMap(QuickTestCase):
    """ShaderAttributeMap is pure Python — no Maya needed."""

    def test_logical_channels(self):
        channels = ShaderAttributeMap.logical_channels()
        self.assertIsInstance(channels, tuple)
        self.assertIn("baseColor", channels)
        self.assertIn("normal", channels)
        self.assertIn("metallic", channels)
        self.assertIn("opacity", channels)

    def test_get_attr_lambert_baseColor(self):
        attr = ShaderAttributeMap.get_attr("lambert", "baseColor")
        self.assertEqual(attr, ("color", "outColor"))

    def test_get_attr_lambert_specular_is_none(self):
        # Lambert has no specular slot
        self.assertIsNone(ShaderAttributeMap.get_attr("lambert", "specular"))

    def test_get_attr_unknown_shader_returns_none(self):
        self.assertIsNone(ShaderAttributeMap.get_attr("nonexistent", "baseColor"))

    def test_get_attr_unknown_channel_returns_none(self):
        self.assertIsNone(ShaderAttributeMap.get_attr("lambert", "bogus"))

    def test_get_mapping_lambert_to_blinn(self):
        mapping = ShaderAttributeMap.get_mapping("lambert", "blinn")
        # Should at least have baseColor and opacity in common
        attrs = {triple[0] for triple in mapping}
        self.assertIn("color", attrs)

    def test_get_mapping_unknown_returns_empty(self):
        self.assertEqual(ShaderAttributeMap.get_mapping("bogus", "blinn"), tuple())
        self.assertEqual(ShaderAttributeMap.get_mapping("blinn", "bogus"), tuple())

    def test_as_dict_includes_known_shaders(self):
        d = ShaderAttributeMap.as_dict()
        for shader in ("lambert", "blinn", "aiStandardSurface", "StingrayPBS"):
            self.assertIn(shader, d)

    def test_add_and_remove_shader_type(self):
        custom = ShaderAttrs(
            baseColor=("custom_color", "outColor"),
            emission=None,
            specular=None,
            roughness=None,
            metallic=None,
            opacity=None,
            normal=None,
            ambientOcclusion=None,
        )
        ShaderAttributeMap.add_shader_type("_test_custom", custom)
        try:
            self.assertIn("_test_custom", ShaderAttributeMap.SHADER_ATTRS)
            attr = ShaderAttributeMap.get_attr("_test_custom", "baseColor")
            self.assertEqual(attr, ("custom_color", "outColor"))
        finally:
            ShaderAttributeMap.SHADER_ATTRS.pop("_test_custom", None)

    def test_update_attr_modifies_existing(self):
        original = ShaderAttributeMap.get_attr("lambert", "baseColor")
        try:
            ShaderAttributeMap.update_attr(
                "lambert", "baseColor", ("xxx", "outColor")
            )
            self.assertEqual(
                ShaderAttributeMap.get_attr("lambert", "baseColor"),
                ("xxx", "outColor"),
            )
        finally:
            # Restore
            ShaderAttributeMap.update_attr("lambert", "baseColor", original)


class TestShaderRemapper(MayaTkTestCase):
    """ShaderRemapper — creates new shader nodes wired from old ones."""

    def setUp(self):
        super().setUp()
        self.remapper = ShaderRemapper(
            attr_map=ShaderAttributeMap, name_suffix="remapped", assign=False
        )

    def test_remap_unmappable_returns_empty(self):
        # No mapping available between two unrelated types
        shader = cmds.shadingNode("lambert", asShader=True, name="srm_lambert")
        result = self.remapper.remap_shaders([shader], "bogus")
        self.assertEqual(result, {})

    def test_remap_lambert_to_blinn_creates_new_shader(self):
        shader = cmds.shadingNode("lambert", asShader=True, name="srm_lambert_src")
        # Connect a file node so the remap has work to do
        f = cmds.shadingNode("file", asTexture=True, name="srm_file")
        cmds.connectAttr(f"{f}.outColor", f"{shader}.color", force=True)

        result = self.remapper.remap_shaders([shader], "blinn")
        self.assertIn(shader, result)
        new_shader = result[shader]
        self.assertTrue(cmds.objExists(new_shader))
        self.assertEqual(cmds.nodeType(new_shader), "blinn")

    def test_create_shader_naming(self):
        shader = cmds.shadingNode("lambert", asShader=True, name="srm_naming")
        new_shader = self.remapper._create_shader(shader, "blinn")
        self.assertTrue(cmds.objExists(new_shader))
        # Suffix appears in the new name
        self.assertIn("remapped", new_shader)


if __name__ == "__main__":
    unittest.main()
