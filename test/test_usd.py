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


class TestDualQuaternionImportGuard(MayaTkTestCase):
    """mayaUsd 0.30 SEGFAULTS importing a UsdSkel skin whose ``skinningMethod``
    is ``dualQuaternion`` -- on stages mayaUsd itself wrote, since its exporter
    authors exactly that value for a DQ skinCluster.

    Maya dies; nothing raises, so nothing can catch it and no test can assert on
    the crash without taking the process down. What IS assertable is the guard:
    the source handed to the importer must never carry the value, the caller's
    own file must never be touched, and the real method must be restored after.
    """

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="mtk_usd_dq_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def _stage(self, method):
        from pxr import Usd, UsdGeom, UsdSkel

        path = os.path.join(self.tmp, f"skin_{method}.usda")
        stage = Usd.Stage.CreateNew(path)
        prim = UsdGeom.Mesh.Define(stage, "/grp/skin").GetPrim()
        UsdSkel.BindingAPI.Apply(prim)
        UsdSkel.BindingAPI(prim).CreateSkinningMethodAttr().Set(method)
        stage.GetRootLayer().Save()
        return path

    def test_a_dual_quaternion_stage_is_never_handed_to_maya_as_is(self):
        try:
            from pxr import Usd, UsdSkel
        except ImportError:
            self.skipTest("pxr not bundled with this Maya")
        src = self._stage("dualQuaternion")
        before = open(src, encoding="utf-8").read()
        safe, methods = UsdUtils.dq_safe_source(src)

        self.assertNotEqual(safe, src, "the crashing stage was passed through")
        self.assertEqual(
            methods, {"/grp/skin": "dualQuaternion"}, "the real method is lost"
        )
        composed = Usd.Stage.Open(safe)
        attr = UsdSkel.BindingAPI(
            composed.GetPrimAtPath("/grp/skin")
        ).GetSkinningMethodAttr()
        self.assertEqual(str(attr.Get()), "classicLinear")
        self.assertEqual(
            open(src, encoding="utf-8").read(),
            before,
            "the caller's own file was modified",
        )

    def test_a_stage_with_nothing_dangerous_is_passed_straight_through(self):
        try:
            import pxr  # noqa: F401
        except ImportError:
            self.skipTest("pxr not bundled with this Maya")
        src = self._stage("classicLinear")
        safe, methods = UsdUtils.dq_safe_source(src)
        self.assertEqual(safe, src, "an overlay was allocated for nothing")
        self.assertEqual(methods, {"/grp/skin": "classicLinear"})

    def test_the_real_method_is_restored_onto_the_skin_cluster(self):
        # The point of neutralizing: the SKIN must not silently become linear.
        cmds.file(new=True, force=True)
        joint = cmds.joint(position=(0, 0, 0))
        mesh = cmds.polyCube()[0]
        cluster = cmds.skinCluster(joint, mesh)[0]
        cmds.setAttr(f"{cluster}.skinningMethod", 0)
        shape = cmds.listRelatives(mesh, shapes=True, fullPath=True)[0]
        prim = "/" + mesh

        self.assertEqual(
            UsdUtils.apply_skinning_methods([shape], {prim: "dualQuaternion"}), 1
        )
        self.assertEqual(cmds.getAttr(f"{cluster}.skinningMethod"), 1)
        # Idempotent: nothing left to change on a second pass.
        self.assertEqual(
            UsdUtils.apply_skinning_methods([shape], {prim: "dualQuaternion"}), 0
        )

    def test_restoring_dual_quaternion_also_enables_non_rigid_support(self):
        # Dual quaternions are RIGID transforms. On a SCALED chain, the method
        # without this flag does not degrade -- it destroys: measured 1264.97 mm
        # on a production module against 4.08 mm for plain linear, and 0.06 mm
        # once the flag is on.
        cmds.file(new=True, force=True)
        joint = cmds.joint(position=(0, 0, 0))
        mesh = cmds.polyCube()[0]
        cluster = cmds.skinCluster(joint, mesh)[0]
        shape = cmds.listRelatives(mesh, shapes=True, fullPath=True)[0]
        self.assertEqual(cmds.getAttr(f"{cluster}.dqsSupportNonRigid"), 0, "premise")

        UsdUtils.apply_skinning_methods([shape], {"/" + mesh: "dualQuaternion"})
        self.assertEqual(cmds.getAttr(f"{cluster}.skinningMethod"), 1)
        self.assertEqual(cmds.getAttr(f"{cluster}.dqsSupportNonRigid"), 1)

    def test_non_rigid_support_is_inert_on_a_rigid_skin(self):
        # Enabling it unconditionally is only safe if it changes NOTHING when the
        # joints do not scale -- otherwise restoring the method would alter a rig
        # that never needed the flag. Measured rather than assumed.
        cmds.file(new=True, force=True)
        root = cmds.joint(position=(0, 0, 0))
        cmds.joint(position=(0, 2, 0))
        mesh = cmds.polyCylinder(height=4, subdivisionsY=6)[0]
        cluster = cmds.skinCluster(root, mesh)[0]
        shape = cmds.listRelatives(mesh, shapes=True, fullPath=True)[0]
        cmds.setAttr(f"{root}.rotateZ", 35)  # rotation only: rigid

        import maya.api.OpenMaya as om2

        def points():
            sel = om2.MSelectionList()
            sel.add(shape)
            return [
                (p.x, p.y, p.z)
                for p in om2.MFnMesh(sel.getDagPath(0)).getPoints(om2.MSpace.kWorld)
            ]

        cmds.setAttr(f"{cluster}.skinningMethod", 1)
        cmds.setAttr(f"{cluster}.dqsSupportNonRigid", 0)
        without = points()
        cmds.setAttr(f"{cluster}.dqsSupportNonRigid", 1)
        worst = max(
            sum((a - b) ** 2 for a, b in zip(p, q)) ** 0.5
            for p, q in zip(without, points())
        )
        self.assertLess(worst, 1e-6, f"the flag moved a rigid skin by {worst}")

    def test_a_prim_that_matches_no_imported_mesh_changes_nothing(self):
        cmds.file(new=True, force=True)
        joint = cmds.joint(position=(0, 0, 0))
        mesh = cmds.polyCube()[0]
        cluster = cmds.skinCluster(joint, mesh)[0]
        shape = cmds.listRelatives(mesh, shapes=True, fullPath=True)[0]
        self.assertEqual(
            UsdUtils.apply_skinning_methods(
                [shape], {"/somewhere/else": "dualQuaternion"}
            ),
            0,
        )
        self.assertEqual(cmds.getAttr(f"{cluster}.skinningMethod"), 0)

    def test_the_overlay_resolves_a_source_whose_path_has_spaces(self):
        """The overlay writes its sublayer path by hand, so awkward paths matter.

        Real scenes sit under sync-folder directories carrying spaces, parentheses
        and punctuation, while every other test here builds in a space-free temp
        dir -- so the resolver was never exercised on one. This fails SILENTLY if
        it ever breaks: an unresolvable sublayer composes to an empty stage rather
        than raising, and the import then brings in nothing at all.
        """
        try:
            from pxr import Usd, UsdGeom, UsdSkel
        except ImportError:
            self.skipTest("pxr not bundled with this Maya")
        for sub in ("plain", "synced (paren) dir", "odd #1, dir"):
            with self.subTest(directory=sub):
                folder = os.path.join(self.tmp, sub)
                os.makedirs(folder, exist_ok=True)
                src = os.path.join(folder, "skin.usda")
                stage = Usd.Stage.CreateNew(src)
                mesh = UsdGeom.Mesh.Define(stage, "/grp/skin")
                mesh.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (0, 1, 0)])
                UsdSkel.BindingAPI.Apply(mesh.GetPrim())
                UsdSkel.BindingAPI(mesh.GetPrim()).CreateSkinningMethodAttr().Set(
                    "dualQuaternion"
                )
                stage.GetRootLayer().Save()
                del stage

                safe, methods = UsdUtils.dq_safe_source(src)
                self.assertNotEqual(safe, src, "the crashing stage was passed through")
                # Held in a name: a temporary stage is collected and its prims go
                # invalid ("Accessed schema on invalid prim").
                composed = Usd.Stage.Open(safe)
                prim = composed.GetPrimAtPath("/grp/skin")
                self.assertTrue(prim.IsValid(), "the overlay lost the source")
                self.assertEqual(
                    len(UsdGeom.Mesh(prim).GetPointsAttr().Get() or []),
                    3,
                    "the source geometry did not compose through the overlay",
                )
                self.assertEqual(
                    UsdSkel.BindingAPI(prim).GetSkinningMethodAttr().Get(),
                    "classicLinear",
                )


class TestUsdLiveRead(MayaTkTestCase):
    """A reference or an open reads the layer LIVE -- from its path, on every load.

    Two consequences, both measured on Maya 2025 / mayaUsd 0.30. The translator must
    be NAMED: left to pick one by extension, Maya reads with ``readAnimData`` at its
    OFF default, and a keyed stage referenced static. And a skin the reader crashes on
    cannot be neutralized the way :meth:`UsdUtils.import_scene` does it -- that
    overlay is a session temp file, which a saved scene would go on pointing at -- so
    a live read of one is refused (a raw reference took mayapy down with an access
    violation, exactly like the import).
    """

    def setUp(self):
        super().setUp()
        UsdUtils.load_plugin()
        self.tempdir = tempfile.mkdtemp(prefix="usd_live_")

    def tearDown(self):
        cmds.file(new=True, force=True)
        shutil.rmtree(self.tempdir, ignore_errors=True)
        super().tearDown()

    def _skinned_stage(self, name, **methods):
        """``<name>.usda`` holding one mesh prim ``/grp/<prim>`` per ``prim=method``."""
        from pxr import Usd, UsdGeom, UsdSkel

        path = os.path.join(self.tempdir, f"{name}.usda")
        stage = Usd.Stage.CreateNew(path)
        for prim_name, method in methods.items():
            prim = UsdGeom.Mesh.Define(stage, f"/grp/{prim_name}").GetPrim()
            UsdSkel.BindingAPI.Apply(prim)
            UsdSkel.BindingAPI(prim).CreateSkinningMethodAttr().Set(method)
        stage.GetRootLayer().Save()
        return path

    def test_file_options_name_a_registered_reader(self):
        self.assertEqual(
            UsdUtils.file_options(),
            {
                "type": UsdUtils.IMPORT_TRANSLATOR,
                "options": UsdUtils.options_string(UsdUtils.INTERCHANGE_IMPORT_OPTIONS),
            },
        )
        self.assertTrue(
            cmds.translator(UsdUtils.IMPORT_TRANSLATOR, q=True, readSupport=True)
        )

    def test_file_options_honor_the_animation_flag_and_explicit_entries(self):
        self.assertIn(
            "readAnimData=0", UsdUtils.file_options(read_animation=False)["options"]
        )
        # An explicit entry wins over the flag -- import_scene's contract.
        self.assertIn(
            "readAnimData=0",
            UsdUtils.file_options({"readAnimData": False}, read_animation=True)[
                "options"
            ],
        )
        self.assertIn(
            "primPath=/grp", UsdUtils.file_options({"primPath": "/grp"})["options"]
        )

    def test_crashing_skins_names_only_the_dual_quaternion_prims(self):
        mixed = self._skinned_stage(
            "mixed", b_dq="dualQuaternion", a_lin="classicLinear", c_dq="dualQuaternion"
        )
        self.assertEqual(UsdUtils.crashing_skins(mixed), ["/grp/b_dq", "/grp/c_dq"])
        safe = self._skinned_stage("safe", limb="classicLinear")
        self.assertEqual(UsdUtils.crashing_skins(safe), [])

    def test_live_read_options_refuse_a_stage_the_reader_crashes_on(self):
        from mayatk.env_utils.usd import UsdReadRefused

        path = self._skinned_stage("dq", limb="dualQuaternion")
        with self.assertRaises(UsdReadRefused) as ctx:
            UsdUtils.live_read_options(path)
        self.assertEqual(ctx.exception.prims, ["/grp/limb"])
        self.assertIn("/grp/limb", str(ctx.exception))
        self.assertIn("import", str(ctx.exception).lower(), "no way forward named")

    def test_live_read_options_refuse_a_layer_pxr_cannot_read(self):
        """Measured: handed to the translator, a damaged or empty layer does not fail
        -- it leaves an EMPTY reference node behind. Refused instead, as a plain
        failure (not UsdReadRefused: the import cannot read it either), with pxr's
        C++ source location stripped from the reason."""
        from mayatk.env_utils.usd import UsdReadRefused

        for name, data in (
            ("garbage.usda", b"#usda 1.0\n(\n this is not usd {{{\n"),
            ("empty.usd", b""),
        ):
            with self.subTest(layer=name):
                path = os.path.join(self.tempdir, name)
                with open(path, "wb") as fh:
                    fh.write(data)
                with self.assertRaises(RuntimeError) as ctx:
                    UsdUtils.live_read_options(path)
                self.assertNotIsInstance(ctx.exception, UsdReadRefused)
                message = str(ctx.exception)
                self.assertIn(f"{name} is not a readable USD layer", message)
                self.assertNotIn("Error in '", message)

    def test_live_read_options_pass_a_safe_stage_through(self):
        path = self._skinned_stage("linear", limb="classicLinear")
        self.assertEqual(UsdUtils.live_read_options(path), UsdUtils.file_options())

    def test_live_read_options_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            UsdUtils.live_read_options(os.path.join(self.tempdir, "ghost.usda"))

    def test_a_reference_read_through_them_keeps_its_animation(self):
        cube = cmds.polyCube(name="usd_live_cube")[0]
        cmds.setKeyframe(cube, attribute="translateX", t=1, v=0)
        cmds.setKeyframe(cube, attribute="translateX", t=24, v=3)
        cmds.select(cube)
        path = os.path.join(self.tempdir, "live.usda")
        cmds.mayaUSDExport(file=path, selection=True, frameRange=(1, 24))
        cmds.file(new=True, force=True)
        cmds.file(
            path, reference=True, namespace="live", **UsdUtils.live_read_options(path)
        )
        self.assertTrue(
            cmds.keyframe("live:usd_live_cube", q=True, timeChange=True),
            "the referenced stage arrived static",
        )


class TestSampledExport(MayaTkTestCase):
    """An animated export writes what ONE ``mayaUSDExport`` pass writes -- pinned
    against mayaUsd itself, prim for prim and sample for sample.

    mayaUsd 0.30 runs every prim writer at every sampled frame, and two interchange
    flags make that real work on prims that never move (``_maya_usd_export`` says
    which), so a sampled export is split: a one-frame pass with every flag, then
    the sampled pass without visibility (and, with ``prune_static``, without the
    static subtrees), merged back with visibility resampled -- mayaUsd's visibility
    rule restated. Production module, 2026-09-27: the export went 944 s -> 638 s
    with the layer unchanged. The scene here holds every visibility shape that pass
    treats differently, each one probed against its own single pass.
    """

    FRAMES = (1.0, 30.0)

    #: The mayaUsd ``UsdUtils._animated_visibility`` restates, and the only one the
    #: split export was proven equal to one pass on (these tests, and a production
    #: pull diffed sample for sample).
    PROVEN_MAYAUSD = "0.30.0"

    def test_the_visibility_rule_was_proven_on_the_loaded_mayausd(self):
        """A tripwire, not a feature test: the restated visibility writer can only
        be checked at the first frame, and a prim it misses whose first value
        equals the fallback would slip through. So a different mayaUsd fails HERE
        rather than exporting a quietly different layer."""
        loaded = cmds.pluginInfo("mayaUsdPlugin", query=True, version=True)
        self.assertEqual(
            loaded,
            self.PROVEN_MAYAUSD,
            f"mayaUsd {loaded} is loaded, but UsdUtils._animated_visibility restates "
            f"mayaUsd {self.PROVEN_MAYAUSD}'s UsdMayaPrimWriter::Write. Re-read its "
            "visibility code, re-run the split-export equivalence proof "
            "(TestSampledExport, and a production pull diffed against ONE "
            "mayaUSDExport pass: 0 differences), then bump PROVEN_MAYAUSD.",
        )

    def setUp(self):
        super().setUp()
        UsdUtils.load_plugin()
        self.out = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "temp_tests",
            "usd_sampled_" + self._testMethodName,
        )
        shutil.rmtree(self.out, ignore_errors=True)
        os.makedirs(self.out)
        self.addCleanup(shutil.rmtree, self.out, True)

    # ---- the scene ----------------------------------------------------------
    @staticmethod
    def _keys(node, attr, pairs, step=True):
        for time, value in pairs:
            cmds.setKeyframe(node, attribute=attr, time=time, value=value)
        if step:
            cmds.keyTangent(node, attribute=attr, outTangentType="step")

    @staticmethod
    def _shape(node):
        return cmds.listRelatives(node, shapes=True, fullPath=True)[0]

    @staticmethod
    def _cube(name, parent):
        node = cmds.polyCube(name=name, constructionHistory=False)[0]
        return cmds.parent(node, parent)[0]

    def _visibility_cases(self):
        """One of every visibility shape mayaUsd 0.30 writes differently, a mover
        with static children, a skinned mesh beside a static prop, a static block."""
        keys, shape, cube = self._keys, self._shape, self._cube
        cmds.playbackOptions(
            animationStartTime=1, animationEndTime=30, minTime=1, maxTime=30
        )
        root = cmds.group(empty=True, name="root")
        # a group: samples only, the fallback-equal default never authored
        blink = cmds.group(empty=True, name="blink_grp", parent=root)
        keys(blink, "visibility", [(1, 1), (10, 0), (20, 1)])
        cube("blink_child", blink)
        # a merged mesh keyed on its TRANSFORM, then on its SHAPE: shape AND parent
        keys(cube("xvis_mesh", root), "visibility", [(1, 0), (8, 1)])
        keys(shape(cube("svis_mesh", root)), "visibility", [(1, 1), (15, 0)])
        hidden = cube("hidden_shape_mesh", root)
        cmds.setAttr(shape(hidden) + ".visibility", 0)
        keys(hidden, "visibility", [(1, 1), (25, 0)])
        # driven through a set-driven key (a node between curve and plug)
        driver = cmds.group(empty=True, name="driver", parent=root)
        keys(driver, "translateX", [(1, 0), (30, 10)], step=False)
        sdk = cmds.group(empty=True, name="sdk_grp", parent=root)
        for value, visible in ((0, 1), (5, 0)):
            cmds.setDrivenKeyframe(
                sdk + ".visibility",
                currentDriver=driver + ".translateX",
                driverValue=value,
                value=visible,
            )
        # an expression: NOT animated to mayaUsd; a flat curve: animated to it
        expr = cmds.group(empty=True, name="expr_grp", parent=root)
        cmds.expression(string="%s.visibility = (frame %% 10) < 5;" % expr)
        keys(
            cmds.group(empty=True, name="flat_hidden", parent=root),
            "visibility",
            [(1, 0), (30, 0)],
        )
        # an unmerged transform (a child beside its shape): a prim per shape
        unmerged = cube("unmerged_mesh", root)
        cmds.group(empty=True, name="um_child", parent=unmerged)
        keys(unmerged, "visibility", [(1, 1), (5, 0), (9, 1)])
        # an instanced shape: both instances
        source = cube("inst_src", root)
        cmds.instance(source, name="inst_dup")
        keys(shape(source), "visibility", [(1, 1), (22, 0)])
        # a curve control, a camera, a locator -- merged shapes of other writers
        for node, pairs in (
            (cmds.circle(name="ctl_crv")[0], [(1, 0), (6, 1)]),
            (cmds.camera(name="cam_vis")[0], [(1, 1), (8, 0)]),
            (cmds.spaceLocator(name="loc_vis")[0], [(1, 1), (12, 0)]),
        ):
            keys(cmds.parent(node, root)[0], "visibility", pairs)
        cmds.setAttr(
            cmds.group(empty=True, name="static_hidden", parent=root) + ".visibility", 0
        )
        mover = cmds.group(empty=True, name="mover", parent=root)
        keys(mover, "translateY", [(1, 0), (30, 5)], step=False)
        for i in range(6):
            cube("static%d" % i, mover if i % 2 else root)
        # a keyed skeleton skinning a cylinder, a static prop in the same SkelRoot
        # (a SkelRoot's extent is written by whichever mesh last changed), and a
        # keyed joint's visibility (the skeleton writer writes none)
        rig = cmds.group(empty=True, name="rig_grp", parent=root)
        cmds.select(clear=True)
        joints = [
            cmds.joint(name="rig_jnt%d" % i, position=(0, i * 2, 0)) for i in range(3)
        ]
        cylinder = cmds.polyCylinder(
            name="rig_skin", height=4, subdivisionsY=4, constructionHistory=False
        )[0]
        cmds.move(0, 2, 0, cylinder)
        cmds.skinCluster(joints, cylinder, toSelectedBones=True)
        cmds.parent(joints[0], rig)
        cmds.parent(cylinder, rig)
        cube("rig_prop", rig)
        self._keys(joints[1], "rotateZ", [(1, 0), (30, 45)], step=False)
        keys(joints[0], "visibility", [(1, 1), (17, 0)])
        block = cmds.group(empty=True, name="static_block", parent=root)
        for i in range(4):
            cmds.move(i, 0, 0, cube("block%d" % i, block))
        cmds.currentTime(12)  # defaults are read HERE, not at frame one

    # ---- the export and its reference ---------------------------------------
    def _export(self, selection_only=False, prune_static=False, **flags):
        """``(path, calls)``: the split export, every ``mayaUSDExport`` recorded as
        ``(flags, {static node: intermediateObject at the call})``."""
        from unittest import mock

        real = cmds.mayaUSDExport
        calls = []
        watched = [n for n in ("static_block", "rig_prop") if cmds.objExists(n)]

        def record(**kwargs):
            calls.append(
                (
                    {k: v for k, v in kwargs.items() if k != "file"},
                    {n: cmds.getAttr(n + ".intermediateObject") for n in watched},
                )
            )
            return real(**kwargs)

        path = os.path.join(self.out, "sampled.usd")
        options = dict(UsdUtils.INTERCHANGE_EXPORT_OPTIONS, frameRange=self.FRAMES)
        options.update(flags)
        with mock.patch("maya.cmds.mayaUSDExport", side_effect=record):
            UsdUtils.export(
                path,
                options=options,
                selection_only=selection_only,
                material_names="shading_group",
                prune_static=prune_static,
            )
        return path, calls

    def _one_pass(self, selection_only=False, **flags):
        path = os.path.join(self.out, "one_pass.usd")
        options = dict(UsdUtils.INTERCHANGE_EXPORT_OPTIONS, **flags)
        cmds.mayaUSDExport(
            file=path, selection=selection_only, frameRange=self.FRAMES, **options
        )
        return path

    @staticmethod
    def _usd_state(path):
        """Every prim's type and child order, every attribute's authored default,
        time samples and connections, every relationship's targets, and the layer's
        time range, keyed by path. Property ORDER is not compared: Usd never
        exposes it (a prim lists its properties by name), and a spec grafted in
        lands last."""
        from pxr import Sdf

        def plain(value):
            if isinstance(value, str) or not hasattr(value, "__len__"):
                return value
            return tuple(plain(v) for v in value)

        layer = Sdf.Layer.FindOrOpen(str(path))
        layer.Reload()
        found = []
        layer.Traverse(layer.pseudoRoot.path, found.append)
        state = {"<range>": (layer.startTimeCode, layer.endTimeCode)}
        for p in found:
            spec = layer.GetObjectAtPath(p)
            if p.IsPrimPath():
                state[str(p)] = (spec.typeName, tuple(spec.nameChildren.keys()))
            elif isinstance(spec, Sdf.AttributeSpec):
                state[str(p)] = (
                    plain(spec.default) if spec.HasDefaultValue() else None,
                    tuple(
                        (t, plain(layer.QueryTimeSample(p, t)))
                        for t in layer.ListTimeSamplesForPath(p)
                    ),
                    tuple(spec.connectionPathList.GetAddedOrExplicitItems()),
                )
            elif isinstance(spec, Sdf.RelationshipSpec):
                state[str(p)] = tuple(spec.targetPathList.GetAddedOrExplicitItems())
        return state

    def _assert_one_pass(self, mine, theirs):
        """*mine* is *theirs*; returns ``(sampled attributes, samples)``."""
        a, b = self._usd_state(mine), self._usd_state(theirs)
        self.assertEqual(sorted(set(a) ^ set(b)), [], "prims/properties differ")
        self.assertEqual(
            {k: (a[k], b[k]) for k in b if a[k] != b[k]}, {}, "values differ"
        )
        tracks = [v for v in b.values() if isinstance(v, tuple) and len(v) == 3]
        tracks = [v for v in tracks if v[1]]
        self.assertGreater(len(tracks), 10, "the scene must sample for real")
        return len(tracks), sum(len(v[1]) for v in tracks)

    def _sampled_visibility(self, path):
        state = self._usd_state(path)
        return sorted(k for k, v in state.items() if k.endswith(".visibility") and v[1])

    # ---- the tests ----------------------------------------------------------
    def test_a_sampled_export_writes_what_one_pass_writes(self):
        self._visibility_cases()
        path, calls = self._export()
        self.assertEqual(cmds.currentTime(query=True), 12)
        shared = dict(UsdUtils.INTERCHANGE_EXPORT_OPTIONS, exportBlendShapes=False)
        self.assertEqual(
            [flags for flags, _ in calls],
            [
                dict(shared, selection=False, frameRange=(1.0, 1.0)),
                dict(
                    shared,
                    selection=False,
                    frameRange=self.FRAMES,
                    exportVisibility=False,
                ),
            ],
            "a one-frame pass with every flag, then the sampled pass without "
            "visibility (and without blendshapes: the scene has none)",
        )
        self.assertEqual(sorted(os.listdir(self.out)), ["sampled.usd"])
        attrs, samples = self._assert_one_pass(path, self._one_pass())
        # 12 sampled visibility tracks: blink, xvis, svis, hidden-shape, sdk, the
        # unmerged transform AND its shape's own prim, both instances, curve,
        # camera, locator (flat_hidden: a default only; the expression and the
        # joint: nothing).
        self.assertEqual(len(self._sampled_visibility(path)), 12)
        print(f"  split == one pass: {attrs} sampled attributes, {samples} samples")

    def test_a_selection_export_samples_its_own_scope(self):
        """Visibility keyed OUTSIDE the selection must not stop the split; inside
        it, the selected node, its descendants and its ancestors all count."""
        self._visibility_cases()
        cmds.select(["|root|blink_grp", "|root|unmerged_mesh", "|root|mover"])
        path, calls = self._export(selection_only=True)
        self.assertEqual(len(calls), 2, [flags for flags, _ in calls])
        cmds.select(["|root|blink_grp", "|root|unmerged_mesh", "|root|mover"])
        self._assert_one_pass(path, self._one_pass(selection_only=True))
        self.assertEqual(
            self._sampled_visibility(path),
            [
                "/root/blink_grp.visibility",
                "/root/unmerged_mesh.visibility",
                "/root/unmerged_mesh/unmerged_meshShape.visibility",
            ],
        )

    def test_prune_static_leaves_static_subtrees_out_and_writes_one_pass(self):
        """The sampled pass runs without the subtrees that never move -- here a
        static block and a static prop inside the SkelRoot, whose extent follows
        the last mesh written -- and every flag is back afterwards."""
        self._visibility_cases()
        path, calls = self._export(prune_static=True)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], {"static_block": False, "rig_prop": False})
        self.assertEqual(
            calls[1][1],
            {"static_block": True, "rig_prop": True},
            "the sampled pass must run with the static subtrees left out",
        )
        self.assertFalse(cmds.getAttr("static_block.intermediateObject"))
        self.assertFalse(cmds.getAttr("rig_prop.intermediateObject"))
        self._assert_one_pass(path, self._one_pass())

    def test_blendshapes_stay_where_the_scene_has_one(self):
        """The blendshape flag is dropped only when NO mesh could export one."""
        cmds.playbackOptions(
            animationStartTime=1, animationEndTime=30, minTime=1, maxTime=30
        )
        group = cmds.group(empty=True, name="bs_grp")
        base = cmds.polyPlane(name="bs_base", constructionHistory=False)[0]
        target = cmds.polyPlane(name="bs_target", constructionHistory=False)[0]
        cmds.move(0, 1, 0, target + ".vtx[0]", relative=True)
        blend = cmds.blendShape(target, base, name="bs")[0]
        cmds.delete(target)
        cmds.parent(base, group)
        cmds.setKeyframe(blend, attribute="w[0]", time=1, value=0.0)
        cmds.setKeyframe(blend, attribute="w[0]", time=30, value=1.0)
        self._keys(group, "visibility", [(1, 1), (10, 0)])
        cmds.currentTime(1)
        path, calls = self._export()
        self.assertTrue(all(flags["exportBlendShapes"] for flags, _ in calls), calls)
        state = self._usd_state(path)
        weights = [k for k in state if k.endswith(".blendShapeWeights")]
        self.assertTrue(weights and all(len(state[k][1]) > 1 for k in weights))
        a, b = self._usd_state(path), self._usd_state(self._one_pass())
        self.assertEqual(
            {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)},
            {},
        )

    def test_a_verdict_that_misses_falls_back_to_one_pass(self):
        """The restated visibility rule is checked against mayaUsd's own on the
        first frame. Should it miss a prim (a newer mayaUsd, a node kind never
        probed), the export runs as ONE pass -- slower, never different."""
        from unittest import mock

        self._visibility_cases()
        with mock.patch.object(UsdUtils, "_animated_visibility", return_value={}):
            path, calls = self._export(prune_static=True)
        self.assertEqual(len(calls), 3, [flags for flags, _ in calls])
        self.assertEqual(calls[-1][0]["frameRange"], self.FRAMES)
        self.assertTrue(calls[-1][0]["exportVisibility"])
        self.assertFalse(any(calls[-1][1].values()), "a fallback prunes nothing")
        self._assert_one_pass(path, self._one_pass())

    def test_a_flag_that_moves_prims_exports_in_one_pass(self):
        """The split finds prims by DAG path: a flag that puts them elsewhere
        (here namespaces stripped) sends the export through one plain pass."""
        self._visibility_cases()
        _, calls = self._export(stripNamespaces=True)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][0]["exportVisibility"])

    def test_a_flag_that_blinds_the_one_frame_pass_exports_in_one_pass(self):
        """``staticSingleSample`` writes the one-frame pass's lone samples as
        defaults, so that pass no longer shows what moves: an expression-driven
        transform (not animated to ``MAnimUtil``) read as static, sat out the
        sampled pass under ``prune_static``, and shipped frozen (measured: its
        translate a default where one pass samples 30 frames). It takes one pass."""
        cmds.playbackOptions(
            animationStartTime=1, animationEndTime=30, minTime=1, maxTime=30
        )
        group = cmds.group(empty=True, name="quiet_grp")
        cube = cmds.parent(cmds.polyCube(name="expr_cube", ch=False)[0], group)[0]
        cmds.expression(string="%s.translateY = frame * 0.5;" % cube)
        mover = cmds.group(empty=True, name="mover")
        self._keys(mover, "translateX", [(1, 0), (30, 5)], step=False)
        path, calls = self._export(prune_static=True, staticSingleSample=True)
        a = self._usd_state(path)
        b = self._usd_state(self._one_pass(staticSingleSample=True))
        self.assertEqual(
            {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)},
            {},
        )
        self.assertEqual(len(calls), 1, [flags for flags, _ in calls])


if __name__ == "__main__":
    unittest.main(verbosity=2)
