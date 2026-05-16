# !/usr/bin/python
# coding=utf-8
"""Test Suite for mat_utils.shader_attribute_map.

Pure-Python data module — no Maya runtime required.
"""
import unittest

from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap, ShaderAttrs

from base_test import QuickTestCase


class TestLogicalChannels(QuickTestCase):
    def test_returns_eight_known_channels(self):
        channels = ShaderAttributeMap.logical_channels()
        self.assertEqual(
            set(channels),
            {
                "baseColor",
                "emission",
                "specular",
                "roughness",
                "metallic",
                "opacity",
                "normal",
                "ambientOcclusion",
            },
        )


class TestGetAttr(QuickTestCase):
    def test_returns_known_lambert_baseColor(self):
        self.assertEqual(
            ShaderAttributeMap.get_attr("lambert", "baseColor"),
            ("color", "outColor"),
        )

    def test_returns_none_for_unsupported_logical(self):
        # lambert has no metallic.
        self.assertIsNone(ShaderAttributeMap.get_attr("lambert", "metallic"))

    def test_returns_none_for_unknown_shader_type(self):
        self.assertIsNone(ShaderAttributeMap.get_attr("nonexistent_shader", "baseColor"))

    def test_returns_none_for_invalid_logical_channel(self):
        self.assertIsNone(ShaderAttributeMap.get_attr("lambert", "made_up_channel"))

    def test_stingray_uses_TEX_prefix(self):
        self.assertEqual(
            ShaderAttributeMap.get_attr("StingrayPBS", "baseColor"),
            ("TEX_color_map", "outColor"),
        )


class TestGetMapping(QuickTestCase):
    def test_returns_empty_for_unknown_src(self):
        self.assertEqual(
            ShaderAttributeMap.get_mapping("bogus", "lambert"),
            tuple(),
        )

    def test_returns_empty_for_unknown_dst(self):
        self.assertEqual(
            ShaderAttributeMap.get_mapping("lambert", "bogus"),
            tuple(),
        )

    def test_lambert_to_stingray_only_includes_shared_channels(self):
        # lambert has baseColor, emission, opacity (no specular/roughness/metallic/normal/AO).
        # StingrayPBS has all of those — intersection = baseColor, emission, opacity.
        pairs = ShaderAttributeMap.get_mapping("lambert", "StingrayPBS")
        src_attrs = {p[0] for p in pairs}
        self.assertEqual(src_attrs, {"color", "incandescence", "transparency"})

    def test_pair_structure_is_src_attr_src_plug_dst_attr(self):
        pairs = ShaderAttributeMap.get_mapping("aiStandardSurface", "standardSurface")
        for p in pairs:
            self.assertEqual(len(p), 3)
            for component in p:
                self.assertIsInstance(component, str)


class TestUpdateAttr(QuickTestCase):
    def setUp(self):
        # Snapshot the current state so we can restore — these are class-level
        # mutations and would leak across tests otherwise.
        self._saved = ShaderAttributeMap.SHADER_ATTRS["lambert"]

    def tearDown(self):
        ShaderAttributeMap.SHADER_ATTRS["lambert"] = self._saved

    def test_update_replaces_attr(self):
        ShaderAttributeMap.update_attr(
            "lambert", "baseColor", ("custom_attr", "custom_plug")
        )
        self.assertEqual(
            ShaderAttributeMap.get_attr("lambert", "baseColor"),
            ("custom_attr", "custom_plug"),
        )

    def test_update_to_none_clears(self):
        ShaderAttributeMap.update_attr("lambert", "baseColor", None)
        self.assertIsNone(ShaderAttributeMap.get_attr("lambert", "baseColor"))

    def test_update_ignored_for_unknown_shader(self):
        # Silent no-op (no exception) when the shader isn't registered.
        ShaderAttributeMap.update_attr("nonexistent", "baseColor", ("a", "b"))
        self.assertNotIn("nonexistent", ShaderAttributeMap.SHADER_ATTRS)


class TestAddShaderType(QuickTestCase):
    def tearDown(self):
        ShaderAttributeMap.SHADER_ATTRS.pop("__test_shader__", None)

    def test_add_then_get_attr_works(self):
        attrs = ShaderAttrs(
            baseColor=("my_base", "outColor"),
            emission=None,
            specular=None,
            roughness=None,
            metallic=None,
            opacity=None,
            normal=None,
            ambientOcclusion=None,
        )
        ShaderAttributeMap.add_shader_type("__test_shader__", attrs)
        self.assertEqual(
            ShaderAttributeMap.get_attr("__test_shader__", "baseColor"),
            ("my_base", "outColor"),
        )


class TestAsDict(QuickTestCase):
    def test_returns_dict_of_dicts_with_all_logical_channels(self):
        d = ShaderAttributeMap.as_dict()
        self.assertIn("lambert", d)
        self.assertIn("StingrayPBS", d)
        # Each inner dict must contain every logical channel key.
        for shader_type, attrs_dict in d.items():
            self.assertEqual(
                set(attrs_dict.keys()),
                set(ShaderAttributeMap.logical_channels()),
                f"{shader_type} missing channels",
            )


if __name__ == "__main__":
    unittest.main()
