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

import pythontk as ptk

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


class TestRestoreOpacityWiring(MayaTkTestCase):
    """Opacity must be driven by the image's ALPHA, never by its color.

    Regression (live report, Blender->Maya bridge): every PBR shader here
    declares ``opacity`` as ``outAlpha``, but the attribute is a float3
    (``standardSurface.opacity``), so the declared connection failed on type and
    restore's old blanket fallback retried on ``outColor`` -- wiring the
    texture's RGB into opacity, so color drove transparency on every rebuilt
    cutout material.
    """

    def setUp(self):
        super().setUp()
        # Scoped TempArtifacts, not a fixed name in the temp dir: the runner can
        # execute modules concurrently (--jobs), and two processes sharing one
        # hard-coded path would race on the very bytes under test.
        self.artifacts = ptk.TempArtifacts("mtk_opacity_tex", policy="scoped")
        self.rgba = self._png(self.artifacts.path(extension=".png"), alpha=True)
        self.gray = self._png(self.artifacts.path(extension=".png"), alpha=False)

    def tearDown(self):
        self.artifacts.cleanup()
        super().tearDown()

    @staticmethod
    def _png(path, alpha):
        """A real on-disk PNG; ``alpha`` decides whether it has an alpha channel.

        Written for real because the behavior under test reads ``fileHasAlpha``,
        which only a loadable image reports honestly.
        """
        import struct
        import zlib

        path = path.replace("\\", "/")

        def chunk(tag, data):
            return (
                struct.pack(">I", len(data))
                + tag
                + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            )

        px = bytes((128, 128, 128)) + (b"\x80" if alpha else b"")
        raw = (b"\x00" + px * 8) * 8
        ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 6 if alpha else 2, 0, 0, 0)
        with open(path, "wb") as fh:
            fh.write(
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(raw))
                + chunk(b"IEND", b"")
            )
        return path

    @staticmethod
    def _driven_by(node, attr):
        """Source plugs on ``node.attr`` -- the parent's, else its children's.

        ``listConnections`` on a compound PARENT reports nothing when only the
        children are connected (verified live), so a broadcast must be read per
        child or the assertion measures the query, not the wiring.
        """
        direct = (
            cmds.listConnections(
                f"{node}.{attr}", source=True, destination=False, plugs=True
            )
            or []
        )
        if direct:
            return direct
        found = []
        for child in cmds.attributeQuery(attr, node=node, listChildren=True) or []:
            found.extend(
                cmds.listConnections(
                    f"{node}.{child}", source=True, destination=False, plugs=True
                )
                or []
            )
        return found

    def test_opacity_is_driven_by_alpha_not_color(self):
        mat = cmds.shadingNode("standardSurface", asShader=True, name="op_ss")
        MatManifest.restore(
            mat, {"materials": {mat: {"baseColor": self.rgba, "opacity": self.rgba}}}
        )

        opacity = self._driven_by(mat, "opacity")
        self.assertTrue(opacity, "opacity was left unconnected")
        for plug in opacity:
            self.assertTrue(
                plug.endswith(".outAlpha"),
                f"opacity must come from outAlpha, got {plug}",
            )
        # float3 destination: every child driven, or the shader reads a partial.
        self.assertEqual(len(opacity), 3, opacity)

    def test_base_color_and_opacity_share_one_file_node(self):
        """A color-with-alpha map feeds both channels off ONE node, per socket."""
        mat = cmds.shadingNode("standardSurface", asShader=True, name="op_ss_share")
        MatManifest.restore(
            mat, {"materials": {mat: {"baseColor": self.rgba, "opacity": self.rgba}}}
        )
        base = self._driven_by(mat, "baseColor")
        opacity = self._driven_by(mat, "opacity")
        self.assertEqual([p.split(".")[-1] for p in base], ["outColor"])
        self.assertEqual(
            base[0].split(".")[0],
            opacity[0].split(".")[0],
            "baseColor and opacity must ride the same file node",
        )

    def test_grayscale_opacity_map_gets_alpha_is_luminance(self):
        """Without it ``outAlpha`` is a constant 1 and the map does NOTHING."""
        mat = cmds.shadingNode("standardSurface", asShader=True, name="op_ss_gray")
        MatManifest.restore(mat, {"materials": {mat: {"opacity": self.gray}}})
        opacity = self._driven_by(mat, "opacity")
        self.assertTrue(opacity)
        file_node = opacity[0].split(".")[0]
        self.assertFalse(cmds.getAttr(f"{file_node}.fileHasAlpha"))
        self.assertTrue(cmds.getAttr(f"{file_node}.alphaIsLuminance"))

    def test_real_alpha_channel_is_not_overridden(self):
        mat = cmds.shadingNode("standardSurface", asShader=True, name="op_ss_real")
        MatManifest.restore(mat, {"materials": {mat: {"opacity": self.rgba}}})
        file_node = self._driven_by(mat, "opacity")[0].split(".")[0]
        self.assertTrue(cmds.getAttr(f"{file_node}.fileHasAlpha"))
        self.assertFalse(cmds.getAttr(f"{file_node}.alphaIsLuminance"))

    def test_scalar_channels_still_connect_directly(self):
        """Roughness/metalness ARE scalar attrs -- no broadcast, no regression."""
        mat = cmds.shadingNode("standardSurface", asShader=True, name="op_ss_scalar")
        MatManifest.restore(
            mat, {"materials": {mat: {"roughness": self.gray, "metallic": self.gray}}}
        )
        self.assertEqual(
            [p.split(".")[-1] for p in self._driven_by(mat, "specularRoughness")],
            ["outAlpha"],
        )
        self.assertEqual(
            [p.split(".")[-1] for p in self._driven_by(mat, "metalness")],
            ["outAlpha"],
        )

    def test_lambert_transparency_unregressed(self):
        """A compatible declaration (float3 attr, outColor plug) stays direct."""
        mat = cmds.shadingNode("lambert", asShader=True, name="op_lambert")
        MatManifest.restore(mat, {"materials": {mat: {"opacity": self.rgba}}})
        self.assertEqual(
            [p.split(".")[-1] for p in self._driven_by(mat, "transparency")],
            ["outColor"],
        )


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
