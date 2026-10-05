# !/usr/bin/python
# coding=utf-8
"""Test Suite for mat_utils.shader_converter.

The motivating case is a decal wall: a `blinn` whose colour and transparency
both come from one texture, assigned to a dozen planes. Converting it for FBX
export has to keep the texture, invert the transparency into opacity, and leave
every plane still assigned.
"""

import math
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
        self.normal_map = self._png(
            self.artifacts.path(extension=".png"), "DIRT_Normal"
        )

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
            plane = cmds.polyPlane(
                name=f"DIRT_STAIN_{i:02d}", constructionHistory=False
            )[0]
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


class TestTargetSpec(unittest.TestCase):
    """The target table and its display names travel together.

    Panels (the Material Updater's Shader Type, the UV Transfer's Shader row)
    build their combos from ``TARGETS`` and label them from ``TARGET_LABELS``,
    so a target added with no label would show up as a raw Maya node type.
    """

    def test_every_target_has_a_label(self):
        self.assertEqual(
            set(ShaderConverter.TARGET_LABELS), set(ShaderConverter.TARGETS)
        )

    def test_every_target_is_a_type_the_converter_can_also_read(self):
        """A retype has to be reversible in principle -- a target it cannot read
        back would be a one-way door out of the conversion set."""
        for node_type in ShaderConverter.TARGETS.values():
            self.assertIn(node_type, ShaderConverter.CONVERTIBLE)


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

    def test_leaves_no_orphan_shading_group(self):
        """Deleting a shader does NOT take its shading engine with it, and
        ``_transfer_assignments`` has already emptied the source's -- so without
        the retirement an empty ``<name>SG`` survives, holding the name the new
        group then has to uniquify around (``<name>SG1``)."""
        groups = [
            sg for sg in cmds.ls(type="shadingEngine") if sg.startswith("decal_blinn")
        ]
        self.assertEqual(groups, ["decal_blinnSG"], groups)
        self.assertTrue(cmds.sets(groups[0], query=True, noIntermediate=True))
        self.assertIn(
            self.new_mat, cmds.listConnections(f"{groups[0]}.surfaceShader") or []
        )

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
                cmds.listConnections(f"{node}.{attr}{suffix}", source=True, plugs=True)
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

        The decal's transparency comes from its COLOUR texture's alpha. On
        the ShaderFX graphs that alpha is read through the ``use_opacity_map``
        selector (1) -- never by binding the colour file as the mask map,
        whose sampler reads RED (``ShaderAttributeMap.select_color_alpha``).
        So "driven" here is: the colour map bound, the selector on its alpha,
        the mask sampler free. (Caught on a real scene before that rule: the
        masked graph has no scalar ``opacity``, the declared slot missed, and
        the channel was dropped while every other check still passed.)
        """
        colour = self._slot_sources(self.new_mat, "TEX_color_map")
        self.assertEqual({p.split(".")[0] for p in colour}, {self.color_file})
        self.assertEqual(cmds.getAttr(f"{self.new_mat}.use_opacity_map"), 1)
        self.assertFalse(
            self._slot_sources(self.new_mat, "TEX_mask_map"),
            "the colour file must not be bound as a red-channel mask",
        )

    def test_opacity_toggle_is_enabled(self):
        """The selector must point at the colour map's alpha (1), or the
        cutout renders fully opaque with every connection looking right."""
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
        self.assertFalse(cmds.attributeQuery("TEX_mask_map", node=new_mat, exists=True))

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


class TestConvertOpaqueMaterials(MayaTkTestCase, _DecalSceneMixin):
    """An opaque material carries opacity only as its DEFAULT value.

    ``read_channels`` reports a literal for every undriven slot, so an untouched
    ``standardSurface.opacity`` (1, 1, 1) -- or a classic shader's
    ``transparency`` (0, 0, 0), the same "opaque" in inverted terms -- used to
    read as an opacity channel. A Stingray retype then picked the masked graph,
    whose unbound mask discards every fragment (the retyped mesh rendered
    invisible), and a classic -> PBR retype copied ``transparency`` 0 straight
    into ``opacity`` 0.
    """

    def setUp(self):
        super().setUp()
        self.artifacts = ptk.TempArtifacts("mtk_shader_opaque", policy="scoped")
        self.addCleanup(self.artifacts.cleanup)
        self.base_map = self._png(
            self.artifacts.path(extension=".png"), "OPAQUE_Base_Color"
        )

    def _material(self, node_type, name):
        mat = cmds.shadingNode(node_type, asShader=True, name=name)
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        color = self._file_node(self.base_map, f"{name}_color")
        attr = ShaderAttributeMap.get_attr(node_type, "baseColor")[0]
        cmds.connectAttr(f"{color}.outColor", f"{mat}.{attr}", force=True)
        plane = cmds.polyPlane(name=f"{name}_geo", constructionHistory=False)[0]
        cmds.sets(plane, edit=True, forceElement=sg)
        return mat

    def test_an_opaque_pbr_material_gets_the_plain_stingray_graph(self):
        mat = self._material("standardSurface", "opaque_ss")
        self.assertNotIn("opacity", ShaderConverter.read_channels(mat))
        new_mat = ShaderConverter.convert(mat, target="stingray")[mat]
        self.assertFalse(
            cmds.attributeQuery("TEX_mask_map", node=new_mat, exists=True),
            "an opaque material was given the masked (cutout) graph",
        )

    def test_an_opaque_classic_shader_stays_opaque_as_pbr(self):
        mat = self._material("blinn", "opaque_blinn")
        new_mat = ShaderConverter.convert(mat, target="standard_surface")[mat]
        for got in cmds.getAttr(f"{new_mat}.opacity")[0]:
            self.assertAlmostEqual(got, 1.0, places=5)
        self.assertFalse(cmds.getAttr(f"{new_mat}.thinWalled"))

    def test_a_constant_transparency_carries_across_as_opacity(self):
        """Partial transparency is real and survives -- in OPACITY terms."""
        mat = self._material("blinn", "glass_blinn")
        cmds.setAttr(f"{mat}.transparency", 0.25, 0.25, 0.25, type="double3")
        value = ShaderConverter.read_channels(mat)["opacity"]["value"]
        for got in value:
            self.assertAlmostEqual(got, 0.75, places=5)
        new_mat = ShaderConverter.convert(mat, target="stingray")[mat]
        self.assertFalse(cmds.attributeQuery("TEX_mask_map", node=new_mat, exists=True))
        self.assertAlmostEqual(cmds.getAttr(f"{new_mat}.opacity"), 0.75, places=5)

    def _stingray(self, name, opacity_mode="none"):
        from mayatk.mat_utils._mat_utils import MatUtils

        try:
            mat = MatUtils.create_stingray_shader(name, opacity_mode=opacity_mode)
        except RuntimeError as error:  # no shaderFX plugin on this install
            self.skipTest(f"StingrayPBS unavailable: {error}")
        plane = cmds.polyPlane(name=f"{name}_geo", constructionHistory=False)[0]
        cmds.select(plane)
        cmds.hyperShade(assign=mat)
        return mat

    def test_an_unmapped_masked_stingray_carries_no_opacity(self):
        """The masked graph's ``TEX_mask_map`` is a SAMPLER: undriven, its
        literal (0, 0, 0) is no opacity at all -- read as one, the retype
        picked the transparent graph at opacity 0 and the mesh vanished."""
        mat = self._stingray("masked_bare", opacity_mode="masked")
        self.assertNotIn("opacity", ShaderConverter.read_channels(mat))

    def test_an_untextured_stingray_keeps_its_colour_as_pbr(self):
        """A StingrayPBS keeps its constants in uniforms (``base_color``), not
        on its ``TEX_*`` samplers -- whose (0, 0, 0) turned it black."""
        mat = self._stingray("bare_sr")
        cmds.setAttr(f"{mat}.base_color", 0.2, 0.4, 0.6, type="double3")
        new_mat = ShaderConverter.convert(mat, target="standard_surface")[mat]
        for got, want in zip(cmds.getAttr(f"{new_mat}.baseColor")[0], (0.2, 0.4, 0.6)):
            self.assertAlmostEqual(got, want, places=5)

    def test_an_untextured_classic_shader_keeps_its_colour_as_stingray(self):
        """...and the way in: the literal lands on the uniform, not the sampler."""
        mat = cmds.shadingNode("blinn", asShader=True, name="bare_blinn")
        cmds.setAttr(f"{mat}.color", 0.2, 0.4, 0.6, type="double3")
        plane = cmds.polyPlane(name="bare_blinn_geo", constructionHistory=False)[0]
        cmds.select(plane)
        cmds.hyperShade(assign=mat)
        new_mat = ShaderConverter.convert(mat, target="stingray")[mat]
        for got, want in zip(cmds.getAttr(f"{new_mat}.base_color")[0], (0.2, 0.4, 0.6)):
            self.assertAlmostEqual(got, want, places=5)

    def test_a_scalar_literal_fills_every_channel_of_a_colour_slot(self):
        """Stingray's transparent ``opacity`` is a scalar; standardSurface's a
        float3 -- a bare ``setAttr`` of one into the other raises, and the
        partial opacity was dropped on the way back."""
        mat = self._material("blinn", "glass_rt")
        cmds.setAttr(f"{mat}.transparency", 0.25, 0.25, 0.25, type="double3")
        stingray = ShaderConverter.convert(mat, target="stingray")[mat]
        back = ShaderConverter.convert(stingray, target="standard_surface")[stingray]
        for got in cmds.getAttr(f"{back}.opacity")[0]:
            self.assertAlmostEqual(got, 0.75, places=5)


class TestLiveSlots(MayaTkTestCase, _DecalSceneMixin):
    """The slot the NODE has (``ShaderAttributeMap.resolve_live_slot``), read
    and carried: openPBR's normal on Maya 2025's ``normalCamera`` (the spec's
    ``geometryNormal`` is what it declares), a masked StingrayPBS's opacity on
    ``TEX_mask_map``. Read off the declaration alone, both were dropped."""

    def setUp(self):
        super().setUp()
        self.artifacts = ptk.TempArtifacts("mtk_shader_live_slots", policy="scoped")
        self.addCleanup(self.artifacts.cleanup)

    def _map(self, stem):
        path = self._png(self.artifacts.path(extension=".png"), stem)
        return self._file_node(path, stem.lower())

    def test_reads_the_openpbr_normal_on_the_slot_this_maya_has(self):
        try:
            shader = cmds.shadingNode("openPBRSurface", asShader=True, name="ls_opbr")
        except RuntimeError as error:
            self.skipTest(f"openPBRSurface unavailable: {error}")
        if cmds.nodeType(shader) != "openPBRSurface":  # an `unknown` placeholder
            self.skipTest("openPBRSurface unavailable in this session")
        slot = next(
            a
            for a in ("geometryNormal", "normalCamera")
            if cmds.attributeQuery(a, node=shader, exists=True)
        )
        normal = self._map("LIVE_Normal")
        cmds.connectAttr(f"{normal}.outColor", f"{shader}.{slot}", force=True)
        self.assertEqual(
            ShaderConverter.read_channels(shader)["normal"]["file"], normal
        )

    def test_a_masked_stingrays_mask_becomes_the_opacity(self):
        from mayatk.mat_utils._mat_utils import MatUtils

        try:
            mat = MatUtils.create_stingray_shader("ls_masked", opacity_mode="masked")
        except RuntimeError as error:  # no shaderFX plugin on this install
            self.skipTest(f"StingrayPBS unavailable: {error}")
        mask = self._map("LIVE_Opacity")
        cmds.connectAttr(f"{mask}.outColor", f"{mat}.TEX_mask_map", force=True)
        plane = cmds.polyPlane(name="ls_masked_geo", constructionHistory=False)[0]
        cmds.select(plane)
        cmds.hyperShade(assign=mat)
        self.assertEqual(ShaderConverter.read_channels(mat)["opacity"]["file"], mask)

        new_mat = ShaderConverter.convert(mat, target="standard_surface")[mat]
        drivers = {
            node
            for plug in ("opacity", "opacityR", "opacityG", "opacityB")
            for node in cmds.listConnections(
                f"{new_mat}.{plug}", source=True, destination=False
            )
            or []
        }
        self.assertEqual(drivers, {mask})


class TestConstantsInChannelTerms(MayaTkTestCase):
    """An unmapped literal crosses in the CHANNEL's terms
    (``ShaderAttributeMap.read_constant``), not in its source attribute's."""

    def _stingray_from(self, node_type, name):
        mat = cmds.shadingNode(node_type, asShader=True, name=name)
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        plane = cmds.polyPlane(name=f"{name}_geo", constructionHistory=False)[0]
        cmds.sets(plane, edit=True, forceElement=sg)
        try:
            return ShaderConverter.convert(mat, target="stingray")[mat]
        except RuntimeError as error:  # no shaderFX plugin on this install
            self.skipTest(f"StingrayPBS unavailable: {error}")

    def test_an_untouched_standard_surface_emits_nothing_as_stingray(self):
        """``emissionColor`` is white behind an ``emission`` weight of 0:
        carried alone, an untouched standardSurface retyped to Stingray
        glowed white."""
        new_mat = self._stingray_from("standardSurface", "dark_ss")
        for got in cmds.getAttr(f"{new_mat}.emissive")[0]:
            self.assertAlmostEqual(got, 0.0, places=5)

    def test_a_phong_exponent_lands_as_a_roughness(self):
        """phong's ``cosinePower`` (default 20) landed on Stingray's 0..1
        ``roughness`` uniform as 20 -- fully rough."""
        new_mat = self._stingray_from("phong", "shiny_phong")
        self.assertAlmostEqual(
            cmds.getAttr(f"{new_mat}.roughness"), math.sqrt(2.0 / 22.0), places=4
        )


class TestEmissionLandsLit(MayaTkTestCase):
    """A carried emission lands on the target's colour slot, but standardSurface
    / aiStandardSurface keep a separate weight that defaults to 0 (openPBR a
    luminance): written alone, an emissive material retyped to one of them
    rendered black. Fixed: 2026-10-04."""

    def _convert(self, mat, target="standard_surface"):
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{mat}SG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        plane = cmds.polyPlane(name=f"{mat}_geo", constructionHistory=False)[0]
        cmds.sets(plane, edit=True, forceElement=sg)
        return ShaderConverter.convert(mat, target=target)[mat]

    def test_an_emissive_lambert_lands_lit_as_standard_surface(self):
        mat = cmds.shadingNode("lambert", asShader=True, name="glow_lambert")
        cmds.setAttr(f"{mat}.incandescence", 0.2, 0.6, 1.0, type="double3")
        new_mat = self._convert(mat)
        self.assertEqual(cmds.nodeType(new_mat), "standardSurface")
        self.assertAlmostEqual(cmds.getAttr(f"{new_mat}.emission"), 1.0, places=5)
        for got, want in zip(
            cmds.getAttr(f"{new_mat}.emissionColor")[0], (0.2, 0.6, 1.0)
        ):
            self.assertAlmostEqual(got, want, places=4)

    def test_a_source_that_emits_nothing_leaves_the_weight_off(self):
        mat = cmds.shadingNode("lambert", asShader=True, name="dark_lambert")
        new_mat = self._convert(mat)
        self.assertAlmostEqual(cmds.getAttr(f"{new_mat}.emission"), 0.0, places=5)

    def test_an_emissive_lambert_lands_lit_as_open_pbr(self):
        mat = cmds.shadingNode("lambert", asShader=True, name="glow_lambert_opbr")
        cmds.setAttr(f"{mat}.incandescence", 0.5, 0.5, 0.5, type="double3")
        try:
            new_mat = self._convert(mat, target="open_pbr")
        except RuntimeError as error:
            self.skipTest(f"openPBRSurface unavailable: {error}")
        if cmds.nodeType(new_mat) != "openPBRSurface":  # an `unknown` placeholder
            self.skipTest("openPBRSurface unavailable in this session")
        self.assertGreater(cmds.getAttr(f"{new_mat}.emissionLuminance"), 0.0)


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
        self.assertEqual(
            ShaderAttributeMap.map_toggle_attr("opacity"), "use_opacity_map"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
