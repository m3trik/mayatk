"""Tests for ``env_utils.scene_state`` — the sidecar's DCC-side reader column.

First coverage for the module (its behaviour previously rode consumer tests
only). Focused on the ``metallic_roughness`` section, added after a measured
production failure: FBX2glTF packs a solid-white ORM when it cannot resolve the
real maps, glTF reads metallic from the blue channel, and a lightmapped viewer
(scene lights off) compounds that to a black room.
"""

import os
import unittest

import maya.cmds as cmds

from base_test import MayaTkTestCase


class _StingrayMixin:
    """A StingrayPBS on a cube, its graph chosen by *opacity_mode*."""

    def _stingray(self, name, opacity_mode):
        from mayatk.mat_utils._mat_utils import MatUtils

        cmds.loadPlugin("shaderFXPlugin", quiet=True)
        mat = MatUtils.create_stingray_shader(
            name, opacity=opacity_mode != "none", opacity_mode=opacity_mode
        )
        cube = cmds.polyCube(name=f"{name}_geo")[0]
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)
        return cube, mat


class TestAlphaModeSection(_StingrayMixin, MayaTkTestCase):
    """A StingrayPBS opacity graph is a glTF alphaMode the FBX cannot express.

    FBX2glTF decides alphaMode from the base colour's alpha channel alone: an
    RGBA base colour becomes BLEND, never MASK, so a masked Stingray material
    (cutout, opaque queue -- the only single-material layout that survives a
    solid body) lands in WebXR as alpha blend with the sorting artifacts that
    implies. The section carries the graph's mode and its threshold.
    """

    def test_masked_carries_mask_and_its_threshold(self):
        from mayatk.env_utils.scene_state import SceneState

        cube, mat = self._stingray("cutoutMat", "masked")
        cmds.setAttr(f"{mat}.mask_threshold", 0.25)
        entry = (SceneState.read([cube]).get("alpha_mode") or {}).get("cutoutMat")
        self.assertEqual(entry, {"mode": "MASK", "cutoff": 0.25})

    def test_transparent_carries_blend(self):
        from mayatk.env_utils.scene_state import SceneState

        cube, _ = self._stingray("glassMat", "transparent")
        entry = (SceneState.read([cube]).get("alpha_mode") or {}).get("glassMat")
        self.assertEqual(entry, {"mode": "BLEND"})

    def test_opaque_graph_contributes_nothing(self):
        """The converter's own OPAQUE is right; re-asserting it is noise."""
        from mayatk.env_utils.scene_state import SceneState

        cube, _ = self._stingray("solidMat", "none")
        self.assertNotIn("solidMat", SceneState.read([cube]).get("alpha_mode") or {})

    def _standard_surface(self, name, connect_alpha=False, opacity=None):
        mat = cmds.shadingNode("standardSurface", asShader=True, name=name)
        cube = cmds.polyCube(name=f"{name}_geo")[0]
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)
        if connect_alpha:
            # The shadow rig's wiring: a file's alpha through a multiplier.
            tex = cmds.shadingNode("file", asTexture=True, name=f"{name}_file")
            mult = cmds.shadingNode(
                "multiplyDivide", asUtility=True, name=f"{name}_mult"
            )
            cmds.connectAttr(f"{tex}.outAlpha", f"{mult}.input1X")
            cmds.connectAttr(f"{mult}.output", f"{mat}.opacity")
        if opacity is not None:
            cmds.setAttr(f"{mat}.opacity", *opacity, type="double3")
        return cube, mat

    def test_standard_surface_driven_opacity_is_blend(self):
        """A standardSurface whose opacity is connected IS alpha blend, and
        the FBX cannot say so: the shadow rig's material reached the GLB
        OPAQUE with its silhouette embedded (measured 2026-09-02)."""
        from mayatk.env_utils.scene_state import SceneState

        cube, _ = self._standard_surface("shadowMat", connect_alpha=True)
        entry = (SceneState.read([cube]).get("alpha_mode") or {}).get("shadowMat")
        self.assertEqual(entry, {"mode": "BLEND"})

    def test_standard_surface_constant_opacity_is_blend(self):
        """A constant opacity below 1.0 is a blend too (glass, a tint)."""
        from mayatk.env_utils.scene_state import SceneState

        cube, _ = self._standard_surface("tintMat", opacity=(0.5, 0.5, 0.5))
        entry = (SceneState.read([cube]).get("alpha_mode") or {}).get("tintMat")
        self.assertEqual(entry, {"mode": "BLEND"})

    def test_standard_surface_opaque_contributes_nothing(self):
        """Maya's default material must not be flagged: an unconnected,
        fully-opaque channel is the converter's own OPAQUE."""
        from mayatk.env_utils.scene_state import SceneState

        cube, _ = self._standard_surface("solidStd")
        self.assertNotIn("solidStd", SceneState.read([cube]).get("alpha_mode") or {})

    def _open_pbr(self, name, opacity=None, connect=False):
        try:
            mat = cmds.shadingNode("openPBRSurface", asShader=True, name=name)
        except RuntimeError as error:  # openPBR needs a recent 2025+
            self.skipTest(str(error))
        cube = cmds.polyCube(name=f"{name}_geo")[0]
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)
        if opacity is not None:
            cmds.setAttr(f"{mat}.geometryOpacity", opacity)
        if connect:
            tex = cmds.shadingNode("file", asTexture=True, name=f"{name}_file")
            cmds.connectAttr(f"{tex}.outAlpha", f"{mat}.geometryOpacity")
        return cube, mat

    def test_open_pbr_opacity_is_blend(self):
        """openPBR's opacity is the SCALAR ``geometryOpacity``: the reader knew
        only standardSurface's colour ``opacity``, so an openPBR cutout or tint
        reached the GLB opaque."""
        from mayatk.env_utils.scene_state import SceneState

        for name, kwargs in (
            ("opbrTint", {"opacity": 0.5}),
            ("opbrCutout", {"connect": True}),
        ):
            with self.subTest(name):
                cube, _ = self._open_pbr(name, **kwargs)
                entry = (SceneState.read([cube]).get("alpha_mode") or {}).get(name)
                self.assertEqual(entry, {"mode": "BLEND"})

    def test_open_pbr_opaque_contributes_nothing(self):
        from mayatk.env_utils.scene_state import SceneState

        cube, _ = self._open_pbr("solidOpbr")
        self.assertNotIn("solidOpbr", SceneState.read([cube]).get("alpha_mode") or {})

    def _legacy(self, name, node_type="phong", transparency=None, connect=False):
        mat = cmds.shadingNode(node_type, asShader=True, name=name)
        cube = cmds.polyCube(name=f"{name}_geo")[0]
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)
        if transparency is not None:
            cmds.setAttr(f"{mat}.transparency", *transparency, type="double3")
        if connect:
            tex = cmds.shadingNode("file", asTexture=True, name=f"{name}_file")
            cmds.connectAttr(f"{tex}.outTransparency", f"{mat}.transparency")
        return cube, mat

    def test_a_transparent_legacy_shader_is_blend(self):
        """REGRESSION (2026-10-03): a Phong lens at 89% transparency reached
        the GLB OPAQUE. The FBX carries its transparency and FBX2glTF writes
        it into baseColorFactor's alpha (0.105) -- but judges the MODE from a
        texture's alpha alone, so every viewer ignored the alpha and drew a
        solid grey disc in the magnifier."""
        from mayatk.env_utils.scene_state import SceneState

        cube, _ = self._legacy("lensMat", transparency=(0.89, 0.89, 0.89))
        entry = (SceneState.read([cube]).get("alpha_mode") or {}).get("lensMat")
        self.assertEqual(entry, {"mode": "BLEND"})
        cube, _ = self._legacy("mappedLensMat", node_type="lambert", connect=True)
        entry = (SceneState.read([cube]).get("alpha_mode") or {}).get("mappedLensMat")
        self.assertEqual(entry, {"mode": "BLEND"}, "a mapped transparency too")

    def test_an_opaque_legacy_shader_contributes_nothing(self):
        """Transparency 0 is opaque: the converter's own OPAQUE stands."""
        from mayatk.env_utils.scene_state import SceneState

        cube, _ = self._legacy("solidPhong")
        self.assertNotIn("solidPhong", SceneState.read([cube]).get("alpha_mode") or {})


class TestMetallicRoughnessSection(MayaTkTestCase):
    def _material_with_maps(self, name="mrMat", roughness=True, metallic=True):
        """A standardSurface with file textures on the lossy-in-FBX slots."""
        mat = cmds.shadingNode("standardSurface", asShader=True, name=name)
        cube = cmds.polyCube(name=f"{name}_geo")[0]
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG"
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)

        paths = {}
        for slot, attr, enabled in (
            ("roughness", "specularRoughness", roughness),
            ("metallic", "metalness", metallic),
        ):
            if not enabled:
                continue
            node = cmds.shadingNode("file", asTexture=True, name=f"{name}_{slot}")
            path = os.path.join(self.temp_dir(), f"{name}_{slot}.png").replace(
                "\\", "/"
            )
            cmds.setAttr(f"{node}.fileTextureName", path, type="string")
            cmds.connectAttr(f"{node}.outAlpha", f"{mat}.{attr}", force=True)
            paths[slot] = path
        return cube, paths

    def temp_dir(self):
        import pythontk as ptk

        if not hasattr(self, "_tmp"):
            # TempArtifacts, not raw tempfile: only its age-gated sweep reclaims
            # the dir if the process dies before cleanup (repo temp rule).
            artifacts = ptk.TempArtifacts("scene_state_test", policy="scoped")
            self._tmp = artifacts.dir_path()
            self.addCleanup(artifacts.cleanup)
        return self._tmp

    def test_reads_both_maps(self):
        from mayatk.env_utils.scene_state import SceneState

        cube, paths = self._material_with_maps()
        sections = SceneState.read([cube])
        entry = (sections.get("metallic_roughness") or {}).get("mrMat")
        self.assertIsNotNone(entry, f"section missing; got {sorted(sections)}")
        # normpath both sides: the manifest resolves through Maya and returns
        # OS separators; the equality is about the FILE, not the spelling.
        self.assertEqual(
            os.path.normpath(entry.get("roughness")),
            os.path.normpath(paths["roughness"]),
        )
        self.assertEqual(
            os.path.normpath(entry.get("metallic")),
            os.path.normpath(paths["metallic"]),
        )

    def test_single_map_still_carries(self):
        """One lost map is the same translation failure as two."""
        from mayatk.env_utils.scene_state import SceneState

        cube, paths = self._material_with_maps(
            name="roughOnly", roughness=True, metallic=False
        )
        entry = (SceneState.read([cube]).get("metallic_roughness") or {}).get(
            "roughOnly"
        )
        self.assertIsNotNone(entry)
        self.assertEqual(
            os.path.normpath(entry.get("roughness")),
            os.path.normpath(paths["roughness"]),
        )
        self.assertNotIn("metallic", entry)

    def test_unmapped_material_contributes_nothing(self):
        """Scalar-only shaders survive FBX; re-asserting them is not a repair."""
        from mayatk.env_utils.scene_state import SceneState

        cube, _ = self._material_with_maps(
            name="plain", roughness=False, metallic=False
        )
        self.assertNotIn(
            "plain", SceneState.read([cube]).get("metallic_roughness") or {}
        )


class TestStingrayConstants(_StingrayMixin, MayaTkTestCase):
    """An untextured StingrayPBS keeps its colours in uniforms (``base_color``,
    ``emissive``), apart from its ``TEX_*`` samplers -- whose own literal reads
    (0, 0, 0). Read from the sampler, a light grey metal lock reached WebXR
    with a BLACK base colour and rendered as a black hole (production
    SOL_REPLACE_ASSEMBLY, 2026-10-15)."""

    def test_base_color_is_the_uniform_not_the_sampler(self):
        from mayatk.env_utils.scene_state import SceneState

        cube, mat = self._stingray("greyMetal", "none")
        cmds.setAttr(f"{mat}.base_color", 0.5, 0.25, 0.75, type="double3")
        entry = (SceneState.read([cube]).get("base_color") or {}).get("greyMetal")
        for got, want in zip(entry["color"], (0.5, 0.25, 0.75)):
            self.assertAlmostEqual(got, want, places=5)

    def test_emission_is_the_uniform_not_the_sampler(self):
        from mayatk.env_utils.scene_state import SceneState

        cube, mat = self._stingray("glowPanel", "none")
        if not cmds.attributeQuery("emissive", node=mat, exists=True):
            self.skipTest("this StingrayPBS graph has no emissive uniform")
        cmds.setAttr(f"{mat}.emissive", 0.2, 0.4, 0.6, type="double3")
        if cmds.attributeQuery("emissive_intensity", node=mat, exists=True):
            cmds.setAttr(f"{mat}.emissive_intensity", 1.0)
        entry = (SceneState.read([cube]).get("emissive") or {}).get("glowPanel")
        self.assertIsNotNone(entry, "a constant emissive must not be dropped")
        for got, want in zip(entry["color"], (0.2, 0.4, 0.6)):
            self.assertAlmostEqual(got, want, places=5)


class TestEmissionWeight(MayaTkTestCase):
    """The emission weight is the SHADER map's: a material's constant emission
    (``ShaderAttributeMap.read_constant``) needs it too, below this layer."""

    def test_the_weight_is_the_shader_maps(self):
        from mayatk.env_utils.scene_state import SceneState
        from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap

        self.assertIs(
            SceneState.EMISSION_WEIGHT_ATTRS, ShaderAttributeMap.EMISSION_WEIGHT_ATTRS
        )
        ss = cmds.shadingNode("standardSurface", asShader=True)
        self.assertEqual(SceneState.emission_weight(ss), 0.0)
        cmds.setAttr(f"{ss}.emission", 0.25)
        self.assertAlmostEqual(SceneState.emission_weight(ss), 0.25)

    def test_an_unweighted_emissive_colour_is_carried_as_nothing(self):
        """``emissionColor`` defaults to white behind a weight of 0."""
        from mayatk.env_utils.scene_state import SceneState

        cube = cmds.polyCube(name="emit_geo")[0]
        mat = cmds.shadingNode("standardSurface", asShader=True, name="emitMat")
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)
        self.assertNotIn("emitMat", SceneState.read([cube]).get("emissive") or {})
        cmds.setAttr(f"{mat}.emission", 0.5)
        self.assertEqual(
            SceneState.read([cube])["emissive"]["emitMat"]["color"], [0.5, 0.5, 0.5]
        )

    def test_a_mapped_emission_switched_off_ships_off(self):
        """The FBX carries the map, so the GLB glows unless told otherwise.

        Omitted at weight 0, a production room's ceiling panels (StingrayPBS,
        ``emissive_intensity`` 0) reached WebXR glowing at 1.0 -- the converter's
        default -- while Maya showed them off (2026-10-17).
        """
        import pythontk as ptk

        from mayatk.env_utils.scene_state import SceneState

        cube = cmds.polyCube(name="panel_geo")[0]
        mat = cmds.shadingNode("standardSurface", asShader=True, name="panelMat")
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)
        artifacts = ptk.TempArtifacts("scene_state_test", policy="scoped")
        self.addCleanup(artifacts.cleanup)
        path = os.path.join(artifacts.dir_path(), "panel_Emissive.png").replace("\\", "/")
        node = cmds.shadingNode("file", asTexture=True, name="panel_emissive")
        cmds.setAttr(f"{node}.fileTextureName", path, type="string")
        cmds.connectAttr(f"{node}.outColor", f"{mat}.emissionColor", force=True)
        cmds.setAttr(f"{mat}.emission", 0.0)
        entry = (SceneState.read([cube]).get("emissive") or {}).get("panelMat")
        self.assertIsNotNone(entry, "a mapped emission at weight 0 must ship as off")
        self.assertEqual(entry["color"], [0.0, 0.0, 0.0])
        self.assertEqual(os.path.normpath(entry["texture"]), os.path.normpath(path))


class TestBaseWeight(MayaTkTestCase):
    """The diffuse albedo is ``base`` x ``baseColor`` on standardSurface and
    aiStandardSurface (OpenPBR's ``baseWeight`` likewise). The FBX maps neither,
    and a reader of the colour alone shipped a lowered weight as if it were 1
    -- brighter than it renders, and than unitytk's importer, which folds the
    weight in. (All three default to 1.0, over a 0.8 colour.)"""

    def _cube(self, name, shader_type="standardSurface"):
        cube = cmds.polyCube(name=f"{name}_geo")[0]
        mat = cmds.shadingNode(shader_type, asShader=True, name=name)
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)
        return cube, mat

    def test_the_weight_is_the_shader_maps(self):
        from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap

        ss = cmds.shadingNode("standardSurface", asShader=True)
        self.assertEqual(ShaderAttributeMap.base_weight(ss), 1.0, "the default")
        cmds.setAttr(f"{ss}.base", 0.8)
        self.assertAlmostEqual(ShaderAttributeMap.base_weight(ss), 0.8)
        lam = cmds.shadingNode("lambert", asShader=True)
        self.assertEqual(ShaderAttributeMap.base_weight(lam), 1.0, "unweighted")

    def test_a_constant_colour_carries_its_weight(self):
        from mayatk.env_utils.scene_state import SceneState

        cube, mat = self._cube("weighted")
        cmds.setAttr(f"{mat}.baseColor", 0.5, 0.25, 1.0, type="double3")
        cmds.setAttr(f"{mat}.base", 0.5)
        color = SceneState.read([cube])["base_color"][mat]["color"]
        for got, want in zip(color, [0.25, 0.125, 0.5]):
            self.assertAlmostEqual(got, want, places=5)

    def test_a_driven_weight_reads_as_one(self):
        """A map on ``base``: the FBX writes the scalar as 1, and so does
        unitytk's reader -- the GLB matches it rather than a frame's sample."""
        from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap

        _cube, mat = self._cube("drivenWeight")
        ramp = cmds.shadingNode("ramp", asTexture=True)
        cmds.connectAttr(f"{ramp}.outAlpha", f"{mat}.base", force=True)
        self.assertEqual(ShaderAttributeMap.base_weight(mat), 1.0)


class TestOpacityFactor(MayaTkTestCase):
    """The base-colour section's ``alpha``: the scalar part of a modern
    shader's opacity, which glTF states as ``baseColorFactor[3]`` and the FBX
    drops. A shadow plane held at a static 0.836 fade (texture alpha x the
    plane's ``opacity``) reached WebXR at alpha 1.0 -- solid black where VP2
    drew it half-transparent (2026-10-15)."""

    _standard_surface = TestAlphaModeSection._standard_surface

    def _alpha(self, cube, mat):
        from mayatk.env_utils.scene_state import SceneState

        return (
            (SceneState.read([cube]).get("base_color") or {}).get(mat, {}).get("alpha")
        )

    def test_texture_alpha_times_a_fade_carries_the_fade(self):
        cube, mat = self._standard_surface("fadeMat", connect_alpha=True)
        fader = cmds.createNode("transform", name="fader")
        cmds.addAttr(fader, longName="opacity", attributeType="float", defaultValue=1)
        cmds.setAttr(f"{fader}.opacity", 0.836)
        cmds.connectAttr(f"{fader}.opacity", "fadeMat_mult.input2X")
        self.assertAlmostEqual(self._alpha(cube, mat), 0.836, places=5)

    def test_a_constant_opacity_carries_itself(self):
        cube, mat = self._standard_surface("tint", opacity=(0.5, 0.5, 0.5))
        self.assertAlmostEqual(self._alpha(cube, mat), 0.5, places=5)

    def test_a_texture_alone_states_nothing(self):
        """The texture's own alpha carries it; a factor of 1 is noise."""
        cube, mat = self._standard_surface("bare", connect_alpha=True)
        self.assertIsNone(self._alpha(cube, mat))

    def test_an_opaque_shader_states_nothing(self):
        cube, mat = self._standard_surface("solid")
        self.assertIsNone(self._alpha(cube, mat))


if __name__ == "__main__":
    unittest.main(verbosity=2)
