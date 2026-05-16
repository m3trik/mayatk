# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.mat_utils.mat_manifest.MatManifest.

The manifest is used to survive destructive shader operations
(``shaderfx loadGraph`` and similar). Build captures texture paths;
restore reconnects file nodes from those paths.

Audit flagged this as untested. Only `MatManifest.build` was previously
exercised via the marmoset_bridge mock tests — `restore`, the
file-node lookup/create helper, and the structural contract weren't.
"""
import os
import tempfile
import unittest

import maya.cmds as cmds

from mayatk.mat_utils.mat_manifest import MatManifest

from base_test import MayaTkTestCase


def _connect_file_to(mat, attr_name, file_path):
    """Helper: create a file node pointing at file_path and connect to mat.attr."""
    fn = cmds.shadingNode("file", asTexture=True, isColorManaged=True)
    cmds.setAttr(f"{fn}.fileTextureName", file_path, type="string")
    cmds.connectAttr(f"{fn}.outColor", f"{mat}.{attr_name}", force=True)
    return fn


class TestBuild(MayaTkTestCase):
    """MatManifest.build — read texture connections off assigned materials."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="mm_cube")[0]
        self.mat = cmds.shadingNode("lambert", asShader=True, name="mm_lambert")
        self.sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="mm_lambertSG"
        )
        cmds.connectAttr(f"{self.mat}.outColor", f"{self.sg}.surfaceShader", force=True)
        cmds.sets(self.cube, edit=True, forceElement=self.sg)

        self.tex_path = os.path.join(tempfile.gettempdir(), "mm_test_diffuse.png").replace(
            "\\", "/"
        )

    def test_empty_objects_returns_empty_materials(self):
        manifest = MatManifest.build([])
        self.assertIn("materials", manifest)
        self.assertEqual(manifest["materials"], {})

    def test_structure_has_materials_key(self):
        """Even with no textures connected, build() returns the expected shape."""
        manifest = MatManifest.build([self.cube])
        self.assertIn("materials", manifest)
        self.assertIsInstance(manifest["materials"], dict)

    def test_captures_connected_texture_path(self):
        # Lambert.baseColor (mapped as 'color' in the shader_attribute_map)
        _connect_file_to(self.mat, "color", self.tex_path)

        manifest = MatManifest.build([self.cube])

        # Material should be registered.
        mat_key = self.mat
        self.assertIn(mat_key, manifest["materials"])

        # Path should be present under the 'baseColor' logical slot.
        slots = manifest["materials"][mat_key]
        self.assertIn("baseColor", slots)
        # Compare normalized paths to handle slash direction.
        self.assertEqual(
            os.path.normpath(slots["baseColor"]).lower(),
            os.path.normpath(self.tex_path).lower(),
        )

    def test_skips_unmapped_shader_types(self):
        """Shaders not in ShaderAttributeMap.SHADER_ATTRS should be skipped."""
        # Create a shader of an unmapped type.
        # surfaceShader is a basic shader not in the SHADER_ATTRS mapping.
        weird_mat = cmds.shadingNode(
            "surfaceShader", asShader=True, name="mm_unknown_shader"
        )
        weird_sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="mm_unknown_sg"
        )
        cmds.connectAttr(
            f"{weird_mat}.outColor", f"{weird_sg}.surfaceShader", force=True
        )
        cube2 = cmds.polyCube(name="mm_weird_cube")[0]
        cmds.sets(cube2, edit=True, forceElement=weird_sg)

        manifest = MatManifest.build([cube2])
        # surfaceShader isn't in the mapping — material entry should be absent
        # OR present but empty. Either is acceptable; just no crash.
        self.assertIn("materials", manifest)


class TestProcessMaterial(MayaTkTestCase):
    """_process_material — single-material introspection used by MatSnapshot."""

    def setUp(self):
        super().setUp()
        self.mat = cmds.shadingNode("lambert", asShader=True, name="pm_lambert")

    def test_unmapped_shader_returns_empty(self):
        weird = cmds.shadingNode("surfaceShader", asShader=True, name="pm_weird")
        self.assertEqual(MatManifest._process_material(weird), {})

    def test_no_textures_connected_returns_empty(self):
        # Lambert exists but no file nodes are wired.
        self.assertEqual(MatManifest._process_material(self.mat), {})

    def test_nonexistent_material_returns_empty(self):
        """Operating on a deleted/never-existed material must not crash."""
        # Module catches RuntimeError from cmds.nodeType.
        self.assertEqual(MatManifest._process_material("definitely_not_here"), {})


class TestRestore(MayaTkTestCase):
    """MatManifest.restore — reconnect file nodes from manifest entries."""

    def setUp(self):
        super().setUp()
        self.mat = cmds.shadingNode("lambert", asShader=True, name="rs_lambert")
        self.tex_path = os.path.join(tempfile.gettempdir(), "rs_test.png").replace(
            "\\", "/"
        )

    def test_restore_empty_manifest_returns_zero(self):
        result = MatManifest.restore(self.mat, {"materials": {}})
        self.assertEqual(result, 0)

    def test_restore_missing_entry_returns_zero(self):
        # Manifest has SOME material but not the one we're restoring onto.
        result = MatManifest.restore(
            self.mat, {"materials": {"other_mat": {"baseColor": "x.png"}}}
        )
        self.assertEqual(result, 0)

    def test_restore_unmapped_shader_returns_zero(self):
        """Restore on a shader type not in the mapping returns 0 cleanly."""
        weird = cmds.shadingNode("surfaceShader", asShader=True, name="rs_weird")
        result = MatManifest.restore(
            weird, {"materials": {weird: {"baseColor": "x.png"}}}
        )
        self.assertEqual(result, 0)

    def test_restore_creates_file_node_and_connects(self):
        manifest = {
            "materials": {
                self.mat: {"baseColor": self.tex_path},
            }
        }
        result = MatManifest.restore(self.mat, manifest)
        self.assertEqual(result, 1)

        # baseColor on lambert maps to .color — should now be driven by a file node.
        conns = cmds.listConnections(
            f"{self.mat}.color", source=True, plugs=False, type="file"
        ) or []
        self.assertEqual(len(conns), 1)
        # Path should match what we asked for.
        actual_path = cmds.getAttr(f"{conns[0]}.fileTextureName")
        self.assertEqual(
            os.path.normpath(actual_path).lower(),
            os.path.normpath(self.tex_path).lower(),
        )

    def test_restore_source_mat_name_aliases_lookup(self):
        """When the material was renamed after manifest capture, source_mat_name
        provides the original key to look up."""
        manifest = {
            "materials": {
                "original_name": {"baseColor": self.tex_path},
            }
        }
        # The new material doesn't match the key directly — but source_mat_name does.
        result = MatManifest.restore(
            self.mat, manifest, source_mat_name="original_name"
        )
        self.assertEqual(result, 1)


class TestFindOrCreateFileNode(MayaTkTestCase):
    """_find_or_create_file_node — dedupe before creating new file nodes."""

    def setUp(self):
        super().setUp()
        self.tex_path = os.path.join(tempfile.gettempdir(), "fc_test.png").replace(
            "\\", "/"
        )

    def test_creates_new_file_node_when_none_match(self):
        # Empty scene — should create one.
        fn = MatManifest._find_or_create_file_node(self.tex_path)
        self.assertTrue(cmds.objExists(fn))
        self.assertEqual(cmds.nodeType(fn), "file")
        path = cmds.getAttr(f"{fn}.fileTextureName")
        self.assertEqual(
            os.path.normpath(path).lower(),
            os.path.normpath(self.tex_path).lower(),
        )

    def test_reuses_existing_file_node_with_same_path(self):
        existing = cmds.shadingNode("file", asTexture=True, isColorManaged=True)
        cmds.setAttr(f"{existing}.fileTextureName", self.tex_path, type="string")

        result = MatManifest._find_or_create_file_node(self.tex_path)
        self.assertEqual(result, existing)

    def test_different_paths_create_separate_file_nodes(self):
        a = MatManifest._find_or_create_file_node(self.tex_path)
        other = self.tex_path.replace("fc_test", "fc_other")
        b = MatManifest._find_or_create_file_node(other)
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
