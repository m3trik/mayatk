# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.env_utils.usd module.

Covers UsdUtils — plugin loading, export (usd/usda/usdz, selection and
whole-scene), and the namespace-isolated import round-trip over the native
``mayaUsd`` runtime.
"""

import os
import shutil
import tempfile
import unittest

import maya.cmds as cmds

import pythontk as ptk
from mayatk.env_utils.usd import UsdUtils

from base_test import MayaTkTestCase


class TestUsdPlugin(MayaTkTestCase):
    """load_plugin should be idempotent."""

    def test_load_plugin_idempotent(self):
        UsdUtils.load_plugin()
        self.assertTrue(cmds.pluginInfo("mayaUsdPlugin", query=True, loaded=True))
        UsdUtils.load_plugin()
        self.assertTrue(cmds.pluginInfo("mayaUsdPlugin", query=True, loaded=True))

    def test_is_usd_file_delegates(self):
        self.assertTrue(UsdUtils.is_usd_file("anything.usdz"))
        self.assertFalse(UsdUtils.is_usd_file("anything.fbx"))
        self.assertEqual(UsdUtils.EXTENSIONS, ptk.USD_EXTENSIONS)


class TestUsdExportImport(MayaTkTestCase):
    """End-to-end export + namespace-isolated import round-trip."""

    def setUp(self):
        super().setUp()
        UsdUtils.load_plugin()
        self.tempdir = tempfile.mkdtemp(prefix="usd_test_")

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)
        super().tearDown()

    def test_materials_are_named_after_their_shader_and_bindings_follow(self):
        """mayaUSDExport names a Material after the SHADING GROUP; every other
        carrier and consumer names it after the shader. The export renames the
        prim and fixes up what points at it (Sdf's rename fixes nothing)."""
        from pxr import Usd, UsdShade

        cube = cmds.polyCube(name="usd_mat_cube")[0]
        shader = cmds.shadingNode("standardSurface", asShader=True, name="crate_mat")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="crate_matSG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)
        out = os.path.join(self.tempdir, "mat_names.usda")
        UsdUtils.export(out, objects=[cube])
        stage = Usd.Stage.Open(out)
        materials = [p for p in stage.Traverse() if p.GetTypeName() == "Material"]
        self.assertEqual([p.GetName() for p in materials], ["crate_mat"])
        bound = UsdShade.MaterialBindingAPI(stage.GetPrimAtPath("/usd_mat_cube"))
        self.assertEqual(
            bound.GetDirectBinding().GetMaterialPath(), materials[0].GetPath()
        )
        surface = UsdShade.Material(materials[0]).GetSurfaceOutput()
        source = surface.GetConnectedSource()
        self.assertTrue(
            source and str(source[0].GetPath()).startswith(str(materials[0].GetPath()))
        )
        # opt out keeps mayaUSDExport's own spelling
        out2 = os.path.join(self.tempdir, "mat_sg.usda")
        UsdUtils.export(out2, objects=[cube], material_names="shading_group")
        stage2 = Usd.Stage.Open(out2)  # held: a temporary stage expires mid-traversal
        self.assertEqual(
            [p.GetName() for p in stage2.Traverse() if p.GetTypeName() == "Material"],
            ["crate_matSG"],
        )

    def test_namespaced_materials_rename_as_the_exporter_spells_them(self):
        """``ns:crate_matSG`` exports as ``ns_crate_matSG`` (the namespace sanitized,
        not stripped) -- the mapping must be spelled the same way to match; and two
        shaders that sanitize alike keep the SG name rather than failing the pass."""
        from pxr import Usd

        cmds.namespace(add="ns")
        cube = cmds.polyCube(name="ns:usd_ns_cube")[0]
        shader = cmds.shadingNode("standardSurface", asShader=True, name="ns:crate_mat")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="ns:crate_matSG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)
        out = os.path.join(self.tempdir, "ns_mat.usda")
        UsdUtils.export(out, objects=[cube])
        stage = Usd.Stage.Open(out)
        self.assertEqual(
            [p.GetName() for p in stage.Traverse() if p.GetTypeName() == "Material"],
            ["ns_crate_mat"],
        )
        # a mapping that would send two prims to one name: second one skipped, not fatal
        out2 = os.path.join(self.tempdir, "clash.usda")
        UsdUtils.export(out2, objects=[cube], material_names="shading_group")
        renamed = UsdUtils.name_materials_after_shaders(
            out2, {"ns_crate_matSG": "taken", "never_there": "taken"}
        )
        self.assertEqual(renamed, 1)

    def _cube(self, name="usd_export_cube"):
        cube = cmds.polyCube(name=name)[0]
        cmds.select(cube)
        return cube

    def test_export_selection_with_no_selection_raises(self):
        cmds.select(clear=True)
        with self.assertRaises(RuntimeError):
            UsdUtils.export(
                os.path.join(self.tempdir, "noselection.usd"),
                selection_only=True,
            )

    def test_export_appends_usd_extension(self):
        cube = self._cube()
        result = UsdUtils.export(os.path.join(self.tempdir, "noext"), objects=[cube])
        self.assertTrue(result.lower().endswith(".usd"))
        self.assertTrue(os.path.isfile(result))
        self.assertGreater(os.path.getsize(result), 0)

    def test_export_usda_is_text(self):
        cube = self._cube()
        result = UsdUtils.export(
            os.path.join(self.tempdir, "layer.usda"), objects=[cube]
        )
        self.assertEqual(ptk.UsdFile.sniff(result), "usda")

    def test_import_round_trip_returns_new_nodes(self):
        cube = self._cube("usd_rt_cube")
        out = UsdUtils.export(os.path.join(self.tempdir, "rt.usdc"), objects=[cube])
        cmds.delete(cube)
        new_nodes = UsdUtils.import_scene(out)
        self.assertTrue(new_nodes)
        transforms = cmds.ls(new_nodes, type="transform")
        self.assertTrue(
            any("usd_rt_cube" in t for t in transforms),
            f"expected the exported cube among {transforms}",
        )

    def test_import_into_namespace_isolates_nodes(self):
        cube = self._cube("usd_ns_cube")
        out = UsdUtils.export(os.path.join(self.tempdir, "ns.usdc"), objects=[cube])
        cmds.delete(cube)
        new_nodes = UsdUtils.import_scene(out, namespace="usd_test_ns")
        self.assertTrue(new_nodes)
        namespaced = [
            n for n in cmds.ls(new_nodes, type="transform") if "usd_test_ns:" in n
        ]
        self.assertTrue(namespaced, f"no transform under the namespace in {new_nodes}")
        # Active namespace restored.
        self.assertEqual(
            cmds.namespaceInfo(currentNamespace=True, absoluteName=True), ":"
        )

    def test_import_reads_animation_by_default(self):
        # The translator's own default is readAnimData=0 -- every animated prim
        # imported static (measured on the pull bridge). The helper flips it on,
        # and an explicit options entry still wins.
        cube = cmds.polyCube(name="usd_anim_cube")[0]
        cmds.setKeyframe(cube, attribute="translateX", t=1, v=0)
        cmds.setKeyframe(cube, attribute="translateX", t=24, v=3)
        cmds.select(cube)
        path = os.path.join(self.tempdir, "anim.usda")
        cmds.mayaUSDExport(file=path, selection=True, frameRange=(1, 24))
        cmds.file(new=True, force=True)
        UsdUtils.import_scene(path)
        self.assertTrue(cmds.keyframe("usd_anim_cube", q=True, timeChange=True))
        cmds.file(new=True, force=True)
        UsdUtils.import_scene(path, read_animation=False)
        self.assertFalse(cmds.keyframe("usd_anim_cube", q=True, timeChange=True))

    def test_import_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            UsdUtils.import_scene(os.path.join(self.tempdir, "ghost.usd"))

    def test_interchange_import_options_serialize_to_the_templates_literal(self):
        """The bridge templates carry the string itself (they run without
        mayatk); both sides pin this one literal. Lists take the translator's
        bracket grammar, bools 0/1."""
        self.assertEqual(
            UsdUtils.options_string(UsdUtils.INTERCHANGE_IMPORT_OPTIONS),
            "readAnimData=1;remapUVSetsTo=[[st,map1]]",
        )
        self.assertEqual(
            UsdUtils.options_string({"a": False, "b": [1, "x"], "c": "s"}),
            "a=0;b=[1,x];c=s",
        )
        for table in (
            UsdUtils.INTERCHANGE_EXPORT_OPTIONS,
            UsdUtils._DEFAULT_EXPORT_OPTIONS,
        ):
            self.assertIs(table.get("preserveUVSetNames"), True)

    def test_primary_uv_set_travels_by_name_and_invisible_prims_land_hidden(self):
        """Production pull 2026-08-22: a Blender layer landed every mesh on UV set
        ``st`` (Blender names its render-active map so) and a Maya export had
        rewritten ``map1`` to ``st`` -- USD stores primvars alphabetically, so a
        second set sorts ahead and the primary is unknowable by position.
        Export keeps ``map1``; import remaps ``st`` to it and honors
        ``visibility = invisible`` (Blender's hidden objects) as Maya visibility."""
        from pxr import Sdf, Usd, UsdGeom

        cube = cmds.polyCube(name="uv_cube")[0]
        cmds.polyUVSet(cube, create=True, uvSet="lightmap")
        cmds.select(cube)
        out = os.path.join(self.tempdir, "uv_export.usda")
        UsdUtils.export(out, selection_only=True)
        stage = Usd.Stage.Open(out)
        mesh = next(p for p in stage.Traverse() if p.GetTypeName() == "Mesh")
        names = [
            v.GetPrimvarName()
            for v in UsdGeom.PrimvarsAPI(mesh).GetPrimvars()
            if v.GetTypeName().role == "TextureCoordinate"
        ]
        self.assertIn("map1", names, names)
        del stage

        src = os.path.join(self.tempdir, "blender_like.usda")
        stage = Usd.Stage.CreateNew(src)
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        quad = UsdGeom.Mesh.Define(stage, "/grp/quad")
        quad.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
        quad.CreateFaceVertexCountsAttr([4])
        quad.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
        for name in ("st", "lightmap"):
            pv = UsdGeom.PrimvarsAPI(quad.GetPrim()).CreatePrimvar(
                name, Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying
            )
            pv.Set([(0, 0), (1, 0), (1, 1), (0, 1)])
        UsdGeom.Imageable(quad.GetPrim()).CreateVisibilityAttr().Set(
            UsdGeom.Tokens.invisible
        )
        stage.GetRootLayer().Save()
        del stage

        cmds.file(new=True, force=True)
        UsdUtils.import_scene(src)
        shape = cmds.ls("quad", dag=True, type="mesh", long=True)[0]
        self.assertEqual(
            sorted(cmds.polyUVSet(shape, q=True, allUVSets=True)), ["lightmap", "map1"]
        )
        self.assertFalse(cmds.getAttr("quad.visibility"))

    def test_export_usdz_is_spec_valid_package(self):
        cube = self._cube("usd_z_cube")
        out = UsdUtils.export(os.path.join(self.tempdir, "pkg.usdz"), objects=[cube])
        self.assertTrue(out.endswith(".usdz"))
        self.assertEqual(ptk.UsdFile.sniff(out), "usdz")
        report = ptk.UsdzPackager.verify(out)
        self.assertTrue(report["valid"], report["issues"])
        self.assertIsNotNone(ptk.UsdFile.default_layer(out))
        # And it round-trips back through the importer.
        created = UsdUtils.import_scene(out, namespace="usdz_rt")
        self.assertTrue(cmds.ls(created, type="transform"))


class TestBridgeUsdFastPath(MayaTkTestCase):
    """import_blender_scene(.usd) must import natively — no headless Blender."""

    def setUp(self):
        super().setUp()
        UsdUtils.load_plugin()
        self.tempdir = tempfile.mkdtemp(prefix="usd_fastpath_")

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)
        super().tearDown()

    def test_usd_source_short_circuits_conversion(self):
        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        cube = cmds.polyCube(name="usd_bridge_cube")[0]
        out = UsdUtils.export(
            os.path.join(self.tempdir, "fastpath.usdc"), objects=[cube]
        )
        cmds.delete(cube)
        # A bogus blender_path proves the point: if the bridge tried to
        # convert, require_blender would fail — USD must never reach it.
        engine = BlenderSceneImport(
            blender_path="X:/definitely/not/blender.exe", log_level="WARNING"
        )
        imported = engine.import_scene(out)
        self.assertTrue(imported)
        self.assertTrue(cmds.ls(imported, type="transform"))

    def test_missing_usd_source_raises(self):
        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        engine = BlenderSceneImport(
            blender_path="X:/definitely/not/blender.exe", log_level="WARNING"
        )
        with self.assertRaises(FileNotFoundError):
            engine.import_scene(os.path.join(self.tempdir, "ghost.usdz"))

    def test_via_usd_selects_the_usd_template(self):
        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        engine = BlenderSceneImport(
            blender_path="X:/definitely/not/blender.exe", log_level="WARNING"
        )
        script = engine.render_script("C:/scenes/s.blend", "C:/tmp/out.usd", via="usd")
        self.assertIn("wm.usd_export", script)
        self.assertIn("C:/scenes/s.blend", script)
        self.assertIn("C:/tmp/out.usd", script)
        # The default is FBX: its instancing is format-native on both sides, so no
        # sidecar replay stands between a linked duplicate and a real Maya instance.
        # USD's equivalent is a recorded grouping replayed on import, and that replay
        # degrades SILENTLY into a flattened scene -- so USD is opt-in (via="usd").
        default_script = engine.render_script("C:/scenes/s.blend", "C:/tmp/out.fbx")
        self.assertIn("export_scene.fbx", default_script)
        self.assertNotIn("wm.usd_export", default_script)
        fbx_script = engine.render_script(
            "C:/scenes/s.blend", "C:/tmp/out.fbx", via="fbx"
        )
        self.assertIn("export_scene.fbx", fbx_script)
        with self.assertRaises(ValueError):
            engine.render_script("a.blend", "b", via="alembic")


class TestUsdRootRegistration(MayaTkTestCase):
    def test_symbol_resolves_from_package_root(self):
        import mayatk as mtk

        self.assertIs(mtk.UsdUtils, UsdUtils)


if __name__ == "__main__":
    unittest.main(verbosity=2)
