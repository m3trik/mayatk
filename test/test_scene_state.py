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
            artifacts = ptk.TempArtifacts("scene_state_test")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
