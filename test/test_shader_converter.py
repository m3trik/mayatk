# !/usr/bin/python
# coding=utf-8
"""Test Suite for mat_utils.shader_converter.

The motivating case is a decal wall: a `blinn` whose colour and transparency
both come from one texture, assigned to a dozen planes. Converting it for FBX
export has to keep the texture, invert the transparency into opacity, and leave
every plane still assigned.
"""
import os
import unittest

import maya.cmds as cmds
import pythontk as ptk

from mayatk.mat_utils.shader_converter import ShaderConverter
from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap

from base_test import MayaTkTestCase


class _DecalSceneMixin:
    """A blinn decal rig: one texture into colour + transparency, plus a bump."""

    def build_decal_material(self, name="decal_blinn", planes=3):
        self.artifacts = ptk.TempArtifacts("mtk_shader_convert", policy="scoped")
        self.base_map = self._png(
            self.artifacts.path(extension=".png"), "DIRT_Base_Color"
        )
        self.normal_map = self._png(self.artifacts.path(extension=".png"), "DIRT_Normal")

        mat = cmds.shadingNode("blinn", asShader=True, name=name)
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)

        color_file = self._file_node(self.base_map, "decal_color")
        cmds.connectAttr(f"{color_file}.outColor", f"{mat}.color", force=True)
        # The real scene's wiring: transparency straight off the file node.
        cmds.connectAttr(
            f"{color_file}.outTransparency", f"{mat}.transparency", force=True
        )

        normal_file = self._file_node(self.normal_map, "decal_normal")
        bump = cmds.shadingNode("bump2d", asUtility=True, name="decal_bump")
        cmds.setAttr(f"{bump}.bumpInterp", 1)  # tangent-space normal
        cmds.connectAttr(f"{normal_file}.outAlpha", f"{bump}.bumpValue", force=True)
        cmds.connectAttr(f"{bump}.outNormal", f"{mat}.normalCamera", force=True)

        self.planes = []
        for i in range(planes):
            plane = cmds.polyPlane(name=f"DIRT_STAIN_{i:02d}", constructionHistory=False)[0]
            cmds.sets(plane, edit=True, forceElement=sg)
            self.planes.append(plane)

        self.color_file = color_file
        self.normal_file = normal_file
        return mat

    @staticmethod
    def _file_node(path, name):
        node = cmds.shadingNode("file", asTexture=True, name=name)
        cmds.setAttr(f"{node}.fileTextureName", path, type="string")
        return node

    @staticmethod
    def _png(path, stem):
        """A real 8x8 RGBA PNG named so the map-type resolver classifies it."""
        import struct
        import zlib

        path = os.path.join(os.path.dirname(path), f"{stem}.png").replace("\\", "/")

        def chunk(tag, data):
            return (
                struct.pack(">I", len(data))
                + tag
                + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            )

        raw = (b"\x00" + bytes((128, 128, 128, 128)) * 8) * 8
        ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 6, 0, 0, 0)
        with open(path, "wb") as fh:
            fh.write(
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(raw))
                + chunk(b"IEND", b"")
            )
        return path


class TestReadChannels(MayaTkTestCase, _DecalSceneMixin):
    """Channel extraction — including the hop through bump2d."""

    def setUp(self):
        super().setUp()
        self.mat = self.build_decal_material()

    def tearDown(self):
        self.artifacts.cleanup()
        super().tearDown()

    def test_finds_the_textured_channels(self):
        channels = ShaderConverter.read_channels(self.mat)
        self.assertEqual(channels["baseColor"]["file"], self.color_file)
        self.assertEqual(channels["opacity"]["file"], self.color_file)

    def test_traces_normal_through_bump2d(self):
        """blinn.normalCamera is driven by a bump2d, never by the file."""
        channels = ShaderConverter.read_channels(self.mat)
        self.assertEqual(channels["normal"]["file"], self.normal_file)

    def test_undriven_channel_carries_its_literal(self):
        cmds.setAttr(f"{self.mat}.specularColor", 0.25, 0.5, 0.75, type="double3")
        channels = ShaderConverter.read_channels(self.mat)
        self.assertIsNone(channels["specular"]["file"])
        for got, want in zip(channels["specular"]["value"], (0.25, 0.5, 0.75)):
            self.assertAlmostEqual(got, want, places=5)

    def test_unknown_shader_type_yields_nothing(self):
        surface = cmds.shadingNode("surfaceShader", asShader=True)
        self.assertEqual(ShaderConverter.read_channels(surface), {})


class TestConvertToStingray(MayaTkTestCase, _DecalSceneMixin):
    """The decal-wall case end to end."""

    def setUp(self):
        super().setUp()
        self.mat = self.build_decal_material()
        # By name, the source is indistinguishable from its replacement — the
        # converted material claims the name. Identity has to come from the UUID.
        self.source_uuid = cmds.ls(self.mat, uuid=True)[0]
        self.result = ShaderConverter.convert(self.mat, target="stingray")
        self.new_mat = self.result[self.mat]

    def tearDown(self):
        self.artifacts.cleanup()
        super().tearDown()

    def test_produces_a_stingray_material(self):
        self.assertIsNotNone(self.new_mat)
        self.assertEqual(cmds.nodeType(self.new_mat), "StingrayPBS")

    @staticmethod
    def _slot_sources(node, attr):
        """Source plugs on ``node.attr`` — parent's, else its children's.

        StingrayPBS compounds use X/Y/Z children, not R/G/B; checking only one
        naming reports "undriven" for a slot that is in fact driven.
        """
        plugs = []
        for suffix in ("", "R", "G", "B", "X", "Y", "Z"):
            if not cmds.attributeQuery(f"{attr}{suffix}", node=node, exists=True):
                continue
            plugs += (
                cmds.listConnections(
                    f"{node}.{attr}{suffix}", source=True, plugs=True
                )
                or []
            )
        return plugs

    def test_opacity_material_gets_a_graph_that_has_an_opacity_slot(self):
        """Standard.sfx has none, so defaulting to it would drop the channel."""
        self.assertTrue(
            cmds.attributeQuery("use_opacity_map", node=self.new_mat, exists=True)
        )

    def test_opacity_is_actually_driven(self):
        """The slot EXISTING is not the same as it being wired.

        Caught on a real scene: the masked graph has no scalar `opacity`, so
        the declared slot missed and the channel was dropped while every other
        check still passed.
        """
        driven = [
            attr
            for attr in ("opacity", "TEX_mask_map")
            if self._slot_sources(self.new_mat, attr)
        ]
        self.assertTrue(
            driven, "no opacity slot on the converted material is driven at all"
        )
        sources = self._slot_sources(self.new_mat, driven[0])
        self.assertEqual({p.split(".")[0] for p in sources}, {self.color_file})

    def test_opacity_toggle_is_enabled(self):
        """A wired-but-untoggled cutout renders fully opaque."""
        self.assertTrue(cmds.getAttr(f"{self.new_mat}.use_opacity_map"))

    def test_converted_material_takes_the_source_name(self):
        """Not `blinn2` — the scratch name must be released before renaming."""
        self.assertEqual(cmds.nodeType(self.new_mat), "StingrayPBS")
        self.assertNotIn("CONVERTING", self.new_mat)
        self.assertEqual(self.new_mat, "decal_blinn")

    def test_base_color_is_carried_over(self):
        sources = (
            cmds.listConnections(
                f"{self.new_mat}.TEX_color_map", source=True, plugs=True
            )
            or []
        )
        self.assertEqual([s.split(".")[0] for s in sources], [self.color_file])

    def test_color_map_toggle_is_enabled(self):
        """A ShaderFX slot is inert until its use_* companion is set."""
        self.assertTrue(cmds.getAttr(f"{self.new_mat}.use_color_map"))

    def test_geometry_stays_assigned(self):
        sg = cmds.listConnections(self.new_mat, type="shadingEngine")[0]
        members = set(cmds.sets(sg, query=True, noIntermediate=True) or [])
        for plane in self.planes:
            shape = cmds.listRelatives(plane, shapes=True, fullPath=False)[0]
            self.assertIn(shape, members, f"{plane} lost its material assignment")

    def test_source_shader_is_removed(self):
        self.assertEqual(cmds.ls(self.source_uuid), [])
        self.assertEqual(cmds.ls(type="blinn"), [])


class TestConvertPreservesSource(MayaTkTestCase, _DecalSceneMixin):
    def setUp(self):
        super().setUp()
        self.mat = self.build_decal_material()

    def tearDown(self):
        self.artifacts.cleanup()
        super().tearDown()

    def test_delete_source_false_keeps_the_original(self):
        ShaderConverter.convert(self.mat, target="stingray", delete_source=False)
        self.assertTrue(cmds.objExists(self.mat))

    def test_explicit_transparent_mode_gets_the_scalar_opacity_slot(self):
        result = ShaderConverter.convert(
            self.mat, target="stingray", opacity_mode="transparent"
        )
        new_mat = result[self.mat]
        self.assertTrue(cmds.attributeQuery("opacity", node=new_mat, exists=True))
        self.assertFalse(
            cmds.attributeQuery("TEX_mask_map", node=new_mat, exists=True)
        )

    def test_masked_mode_gets_the_cutout_slot(self):
        result = ShaderConverter.convert(
            self.mat, target="stingray", opacity_mode="masked"
        )
        new_mat = result[self.mat]
        self.assertTrue(cmds.attributeQuery("TEX_mask_map", node=new_mat, exists=True))


class TestConvertToStandardSurface(MayaTkTestCase, _DecalSceneMixin):
    def setUp(self):
        super().setUp()
        self.mat = self.build_decal_material()
        self.new_mat = ShaderConverter.convert(self.mat, target="standard_surface")[
            self.mat
        ]

    def tearDown(self):
        self.artifacts.cleanup()
        super().tearDown()

    def test_produces_a_standard_surface(self):
        self.assertEqual(cmds.nodeType(self.new_mat), "standardSurface")

    def test_opacity_is_driven_by_alpha(self):
        """standardSurface.opacity is a float3 fed from the file's outAlpha."""
        plugs = []
        for suffix in ("", "R", "G", "B"):
            plugs += (
                cmds.listConnections(
                    f"{self.new_mat}.opacity{suffix}", source=True, plugs=True
                )
                or []
            )
        self.assertTrue(plugs, "opacity was never driven")
        self.assertEqual({p.split(".")[0] for p in plugs}, {self.color_file})

    def test_thin_walled_is_set_for_cutout_behavior(self):
        self.assertTrue(cmds.getAttr(f"{self.new_mat}.thinWalled"))


class TestConvertSkips(MayaTkTestCase, _DecalSceneMixin):
    def setUp(self):
        super().setUp()
        self.mat = self.build_decal_material()

    def tearDown(self):
        self.artifacts.cleanup()
        super().tearDown()

    def test_same_type_is_skipped(self):
        new_mat = ShaderConverter.convert(self.mat, target="stingray")[self.mat]
        again = ShaderConverter.convert(new_mat, target="stingray")
        self.assertIsNone(again[new_mat])

    def test_unknown_target_raises(self):
        with self.assertRaises(ValueError):
            ShaderConverter.convert(self.mat, target="not_a_shader")


class TestMaskedGraphToggle(MayaTkTestCase):
    """The masked graph's toggle breaks the TEX_ naming rule.

    Probed live on Maya 2025: `Standard_Masked.sfx` exposes `TEX_mask_map` but
    gates it behind `use_opacity_map`; the derived `use_mask_map` does not
    exist, so wiring it would leave the cutout connected and inert.
    """

    def test_mask_map_toggle_is_use_opacity_map(self):
        self.assertEqual(
            ShaderAttributeMap.map_toggle_attr("TEX_mask_map"), "use_opacity_map"
        )

    def test_tex_rule_still_holds_for_other_slots(self):
        self.assertEqual(
            ShaderAttributeMap.map_toggle_attr("TEX_color_map"), "use_color_map"
        )
        self.assertEqual(ShaderAttributeMap.map_toggle_attr("opacity"), "use_opacity_map")


if __name__ == "__main__":
    unittest.main(verbosity=2)
