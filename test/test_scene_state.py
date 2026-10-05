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


class TestAlphaModeSection(MayaTkTestCase):
    """A StingrayPBS opacity graph is a glTF alphaMode the FBX cannot express.

    FBX2glTF decides alphaMode from the base colour's alpha channel alone: an
    RGBA base colour becomes BLEND, never MASK, so a masked Stingray material
    (cutout, opaque queue -- the only single-material layout that survives a
    solid body) lands in WebXR as alpha blend with the sorting artifacts that
    implies. The section carries the graph's mode and its threshold.
    """

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
