# !/usr/bin/python
# coding=utf-8
"""Test Suite for mat_utils.mat_updater.

The texture pipeline itself is pinned upstream (pythontk's MapFactory) and the
wiring by ``test_game_shader``; what is this class's own is *which materials a
run acts on* -- and, since the Shader Type option, the retype it runs before
wiring them. A legacy blinn has no connector, so it used to be dropped by the
run that most needed it.
"""

import os
import unittest

import maya.cmds as cmds
import pythontk as ptk

from mayatk.mat_utils.mat_updater import MatUpdater, MatUpdaterSlots
from mayatk.mat_utils.game_shader import GameShader
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.mat_utils.shader_converter import ShaderConverter

from base_test import MayaTkTestCase


class _MaterialSceneMixin:
    """A textured blinn on a plane -- the legacy material the retype targets."""

    def build_blinn(self, name="legacy_blinn"):
        self.artifacts = ptk.TempArtifacts("mtk_mat_updater", policy="scoped")
        self.base_map = self._png(self.artifacts.dir_path(), "WALL_Base_Color")

        mat = cmds.shadingNode("blinn", asShader=True, name=name)
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        node = cmds.shadingNode("file", asTexture=True, name=f"{name}_color")
        cmds.setAttr(f"{node}.fileTextureName", self.base_map, type="string")
        cmds.connectAttr(f"{node}.outColor", f"{mat}.color", force=True)

        self.plane = cmds.polyPlane(name="WALL", constructionHistory=False)[0]
        cmds.sets(self.plane, edit=True, forceElement=sg)
        return mat

    @staticmethod
    def _png(directory, stem):
        """A real 8x8 RGBA PNG, named so the map-type resolver classifies it."""
        import os
        import struct
        import zlib

        path = os.path.join(directory, f"{stem}.png").replace("\\", "/")

        def chunk(tag, data):
            return (
                struct.pack(">I", len(data))
                + tag
                + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            )

        raw = (b"\x00" + bytes((128, 128, 128, 255)) * 8) * 8
        ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 6, 0, 0, 0)
        with open(path, "wb") as fh:
            fh.write(
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(raw))
                + chunk(b"IEND", b"")
            )
        return path


class TestRetypeMaterials(MayaTkTestCase, _MaterialSceneMixin):
    """``MatUpdater._retype_materials`` -- the pick, not the conversion.

    The conversion is ``ShaderConverter``'s and is pinned by
    ``test_shader_converter``; what this owns is which materials go into it and
    what the caller gets back to carry forward.
    """

    def setUp(self):
        super().setUp()
        self.mat = self.build_blinn()

    def tearDown(self):
        self.artifacts.cleanup()
        super().tearDown()

    def test_a_converted_material_is_replaced_in_the_list(self):
        # By UUID, not by name: the converter RECLAIMS the source's name, so
        # the entry that comes back reads identical while naming a new node.
        was = cmds.ls(self.mat, uuid=True)[0]
        out = MatUpdater._retype_materials([self.mat], "standard_surface")
        self.assertEqual(len(out), 1)
        self.assertEqual(cmds.nodeType(out[0]), "standardSurface")
        self.assertNotEqual(cmds.ls(out[0], uuid=True)[0], was)
        self.assertFalse(cmds.ls(was, uuid=True), "the blinn should be retired")
        # And the geometry came with it -- a retyped material nothing wears
        # would be a silent loss of the assignment. Read through
        # get_shading_assignments: a shading engine connects to the SHAPE, so
        # querying the transform finds nothing whether or not the move worked.
        from mayatk.mat_utils._mat_utils import MatUtils

        shaders = [
            (cmds.listConnections(f"{sg}.surfaceShader") or [None])[0]
            for sg in MatUtils.get_shading_assignments(self.plane)
        ]
        self.assertIn(out[0], shaders)

    def test_a_material_already_of_the_target_type_is_left_alone(self):
        already = cmds.shadingNode("standardSurface", asShader=True, name="pbr_mat")
        out = MatUpdater._retype_materials([already], "standard_surface")
        self.assertEqual(out, [already])
        self.assertTrue(cmds.objExists(already))

    def test_dry_run_retypes_nothing(self):
        out = MatUpdater._retype_materials([self.mat], "stingray", dry_run=True)
        self.assertEqual(out, [self.mat])
        self.assertEqual(cmds.nodeType(self.mat), "blinn")

    def test_an_unknown_target_is_refused_before_anything_is_touched(self):
        """Including on a dry run, which never reaches the conversion's own
        validation -- so it would otherwise report a plan that cannot run."""
        for dry in (False, True):
            with self.assertRaises(ValueError):
                MatUpdater._retype_materials([self.mat], "not_a_shader", dry_run=dry)
        self.assertEqual(cmds.nodeType(self.mat), "blinn")

    def test_an_unconvertible_material_keeps_its_place(self):
        """``convert`` reports None for a type it cannot read; the caller must
        carry the original forward rather than a hole in the list."""
        surface = cmds.shadingNode("surfaceShader", asShader=True, name="raw_surface")
        out = MatUpdater._retype_materials([surface], "standard_surface")
        self.assertEqual(out, [surface])
        self.assertTrue(cmds.objExists(surface))


class TestUpdateMaterialsRetype(MayaTkTestCase, _MaterialSceneMixin):
    """The integration claim: a retype run WIRES the material it converted.

    Without the retype the blinn is dropped by the connector filter -- so this
    pins the ordering (retype first, filter second), not just the conversion.
    """

    def setUp(self):
        super().setUp()
        self.mat = self.build_blinn()
        # Reconfiguration only: the image factory is off, so the run wires the
        # textures already on the material instead of processing files. Keeps
        # the test about the routing, and off pythontk's pipeline.
        self.config = {
            "convert": False,
            "optimize": False,
            "convert_format": False,
            "convert_type": False,
            "resize": False,
            "pack": False,
        }

    def tearDown(self):
        self.artifacts.cleanup()
        super().tearDown()

    def test_a_legacy_material_is_retyped_then_updated(self):
        was = cmds.ls(self.mat, uuid=True)[0]
        results = MatUpdater.update_materials(
            materials=[self.mat],
            config=dict(self.config),
            shader_type="standard_surface",
        )
        # The blinn NODE is gone; its name lives on, reclaimed by the shader
        # that replaced it (ShaderConverter._claim_name), which is why this
        # checks the uuid rather than objExists.
        self.assertFalse(cmds.ls(was, uuid=True), "the blinn should be retired")
        updated = list(results)
        self.assertEqual(len(updated), 1, results)
        self.assertEqual(cmds.nodeType(updated[0]), "standardSurface")
        self.assertTrue(
            cmds.listConnections(f"{updated[0]}.baseColor", type="file"),
            "the retyped material reached the wiring stage",
        )

    def test_without_a_shader_type_the_legacy_material_is_skipped(self):
        """The pre-existing behaviour, pinned so the widening above is visibly
        the thing that changes it."""
        results = MatUpdater.update_materials(
            materials=[self.mat], config=dict(self.config)
        )
        self.assertEqual(results, {})
        self.assertEqual(cmds.nodeType(self.mat), "blinn")


class TestUpdateNetworkKeepsOpacity(MayaTkTestCase):
    """``update_network`` has to settle the opacity the way a build does.

    StingrayPBS has no sampler for a SEPARATE opacity map -- the alpha rides
    the colour map (`GameShader.OPACITY_SLOTS`). A build packs the two before
    wiring; the rewire did not, so a set the factory had split into a
    `Base_Color` + an `Opacity` (what every preset with ``albedo_transparency``
    off produces) disconnected the material's working alpha and reported the
    replacement as "no slot for Opacity; skipped". The transparency was gone,
    and the run reported success.
    """

    def setUp(self):
        super().setUp()
        from PIL import Image

        self.artifacts = ptk.TempArtifacts("mtk_updater_opacity", policy="scoped")
        directory = self.artifacts.dir_path()

        self.base_map = os.path.join(directory, "DECAL_Base_Color.png")
        Image.new("RGB", (16, 16), (200, 40, 40)).save(self.base_map)
        self.opacity_map = os.path.join(directory, "DECAL_Opacity.png")
        alpha = Image.new("L", (16, 16), 255)
        alpha.putpixel((0, 0), 40)
        alpha.save(self.opacity_map)

        # The material the user's run hit: the transparent graph, already
        # wearing an alpha, being re-pointed at a freshly processed set.
        self.mat = MatUtils.create_stingray_shader(
            "DECAL_MAT", opacity_mode="transparent"
        )
        self.assertEqual(
            MatUtils.get_stingray_opacity_mode(self.mat),
            "transparent",
            "premise: the graph under test is the one with no opacity sampler",
        )

    def test_a_standalone_opacity_map_reaches_the_colour_map_alpha(self):
        connected = MatUpdater.update_network(
            self.mat, [self.base_map, self.opacity_map], {}
        )

        self.assertNotIn(
            "Opacity",
            connected,
            "a separate Opacity map has no slot here -- it must be folded in, "
            "not reported as connected",
        )
        self.assertIn(
            "Albedo_Transparency",
            connected,
            "the Base_Color and the Opacity must arrive as one packed map",
        )
        self.assertEqual(
            cmds.getAttr(f"{self.mat}.use_opacity_map"),
            1.0,
            "the selector must point the graph at the colour map's alpha",
        )
        wired = cmds.listConnections(
            f"{self.mat}.TEX_color_map", source=True, destination=False
        )
        self.assertTrue(wired, "a colour map must be wired")
        packed = cmds.getAttr(f"{wired[0]}.fileTextureName")
        self.assertTrue(
            GameShader()._carries_alpha(packed),
            f"the wired colour map must carry the opacity in its alpha: {packed}",
        )

    def test_a_uniformly_opaque_opacity_map_is_retired_not_wired(self):
        """The other half of the shared resolution: an inert source.

        Painter's default templates ship a solid-white ``_Opacity`` beside
        every opaque set. Packing it would rewrite the colour map and put the
        meshes through the transparent queue for nothing.
        """
        from PIL import Image

        inert = os.path.join(self.artifacts.dir_path(), "SOLID_Opacity.png")
        Image.new("L", (16, 16), 255).save(inert)
        base = os.path.join(self.artifacts.dir_path(), "SOLID_Base_Color.png")
        Image.new("RGB", (16, 16), (40, 90, 200)).save(base)

        connected = MatUpdater.update_network(self.mat, [base, inert], {})

        self.assertNotIn("Opacity", connected)
        self.assertNotIn(
            "Albedo_Transparency",
            connected,
            "nothing to make transparent -- the colour map must not be rewritten",
        )
        self.assertEqual(connected.get("Base_Color"), base)


class _FakeCombo:
    def __init__(self, data):
        self._data = data

    def currentData(self):
        return self._data


class _FakeUi:
    pass


class TestPanelAcceptsWhatItCanRetype(MayaTkTestCase):
    """``MatUpdaterSlots`` filters the selection BEFORE the engine sees it.

    So the panel's own notion of "supported" has to widen with the Shader Type
    or the option is unreachable for every material it exists for.
    """

    def _slots(self, shader_type):
        instance = MatUpdaterSlots.__new__(MatUpdaterSlots)
        ui = _FakeUi()
        ui.header = _FakeUi()
        ui.header.menu = _FakeUi()
        ui.header.menu.cmb_shader_type = _FakeCombo(shader_type)
        instance.ui = ui
        return instance

    def test_keep_current_type_accepts_only_the_wireable_types(self):
        instance = self._slots(None)
        self.assertEqual(
            instance.acceptable_types, tuple(sorted(MatUpdater.SUPPORTED_MAT_TYPES))
        )
        blinn = cmds.shadingNode("blinn", asShader=True)
        self.assertEqual(instance._filter_supported([blinn]), [])

    def test_a_retype_target_accepts_what_the_converter_can_read(self):
        instance = self._slots("stingray")
        self.assertIn("blinn", instance.acceptable_types)
        for node_type in ShaderConverter.CONVERTIBLE:
            self.assertIn(node_type, instance.acceptable_types)
        blinn = cmds.shadingNode("blinn", asShader=True)
        self.assertEqual(instance._filter_supported([blinn]), [blinn])


if __name__ == "__main__":
    unittest.main()
