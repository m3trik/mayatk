import unittest
import os
import shutil
import struct
import tempfile
import warnings
from unittest import mock

# Try to initialize QApplication to avoid "Cannot create a QWidget without QApplication" error
# which might be triggered by mayatk imports via userSetup
try:
    from PySide2.QtWidgets import QApplication

    if not QApplication.instance():
        app = QApplication([])
except ImportError:
    try:
        from PySide6.QtWidgets import QApplication

        if not QApplication.instance():
            app = QApplication([])
    except ImportError:
        pass

import maya.cmds as cmds
import pythontk as ptk
from mayatk.core_utils.diagnostics.audit_records import AuditProfile, SceneInfoSection
from mayatk.core_utils.diagnostics.scene_audit import SceneAnalyzer
from mayatk.core_utils.diagnostics.scene_diag import SceneDiagnostics
from base_test import MayaTkTestCase


def _write_png_header(path, width, height):
    """A PNG signature + IHDR claiming *width* x *height*, and nothing else.

    All a header read needs -- and Maya cannot decode it, so dimensions that
    come back prove the audit read the header rather than loading the image.
    """
    with open(path, "wb") as fh:
        fh.write(
            b"\x89PNG\r\n\x1a\n"
            + struct.pack(">I", 13)
            + b"IHDR"
            + struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
            + b"\0\0\0\0"
        )
    return path.replace("\\", "/")


def _assign(objects, name, material_type="lambert"):
    """A new material + shading group, assigned headlessly (``hyperShade`` is GUI-only)."""
    mat = cmds.shadingNode(material_type, asShader=True, name=name)
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG")
    cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader", force=True)
    cmds.sets(objects, edit=True, forceElement=sg)
    return mat, sg


def _texture(mat, attr, path):
    """A file node reading *path*, wired into ``mat.attr``."""
    file_node = cmds.shadingNode("file", asTexture=True)
    cmds.setAttr(f"{file_node}.fileTextureName", path, type="string")
    cmds.connectAttr(f"{file_node}.outColor", f"{mat}.{attr}", force=True)
    return file_node


def _leaf(path):
    return path.rsplit("|", 1)[-1]


def _kinds(findings):
    return [f.kind for f in findings]


class _AuditCase(MayaTkTestCase):
    """Base with a per-class scratch dir for texture fixtures."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._store = ptk.TempArtifacts("mayatk_test_scene_audit", policy="scoped")
        cls._dir = cls._store.dir_path()

    @classmethod
    def tearDownClass(cls):
        cls._store.cleanup()
        super().tearDownClass()

    def audit(self, objects=None, **kwargs):
        analyzer = SceneAnalyzer()
        return analyzer.generate_report(analyzer.analyze(objects, **kwargs))


class TestSceneDiagnostics(_AuditCase):
    def test_clean_scene(self):
        """Test analysis on a clean scene (simple cube)."""
        cube = cmds.polyCube(name="CleanCube")[0]
        report = self.audit([cube])

        self.assertEqual(report.summary.total_meshes, 1)
        self.assertEqual(report.summary.total_tris, 12)
        self.assertEqual(len(report.offenders.by_score), 1)
        self.assertEqual(report.offenders.by_score[0].score, 0)  # Should be perfect
        self.assertEqual(report.fix_actions, [])

    def test_high_poly(self):
        """Test detection of high poly meshes."""
        # 100x100 sphere is approx 19800 tris (poles are tris)
        sphere = cmds.polySphere(
            name="DenseSphere", subdivisionsX=100, subdivisionsY=100
        )[0]

        # Use strict profile to ensure failure
        report = self.audit([sphere], profile=AuditProfile(max_tris=10000))
        rec = report.offenders.by_score[0]

        self.assertTrue(rec.mesh.tris >= 19000)
        self.assertTrue(rec.score > 0)
        self.assertIn("high_poly", _kinds(rec.findings))
        self.assertIn("decimate_scene", _kinds(report.fix_actions))

    def test_ngons(self):
        """Test detection of N-gons."""
        points = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0.5, 1.5, 0), (0, 1, 0)]
        plane = cmds.polyCreateFacet(p=points, name="NgonFace")[0]

        rec = self.audit([plane]).offenders.by_score[0]

        self.assertTrue(rec.mesh.ngons > 0)
        self.assertTrue(rec.score > 0)
        self.assertIn("N-gons", str(rec.score_breakdown))

    def test_multi_material(self):
        """Test detection of multiple material slots."""
        cube = cmds.polyCube()[0]
        _assign(cube, "Mat1")
        _assign(f"{cube}.f[0]", "Mat2")

        rec = self.audit([cube], profile=AuditProfile(max_slots=1)).offenders.by_score[
            0
        ]

        self.assertEqual(rec.material.slot_count, 2)
        self.assertEqual(rec.material.draw_calls, 2)
        self.assertTrue(rec.score > 0)
        self.assertIn("Draw Call Split", str(rec.score_breakdown))

    def test_global_texture_usage(self):
        """A texture is 'unique' to a mesh only when no other mesh -- in scope
        or not -- wears it."""
        tex = _write_png_header(
            os.path.join(self._dir, "shared_Base_Color.png"), 64, 64
        )
        cube1 = cmds.polyCube(name="Cube1")[0]
        cube2 = cmds.polyCube(name="Cube2")[0]
        mat, _ = _assign([cube1, cube2], "SharedTexMat")
        _texture(mat, "color", tex)

        report = self.audit([cube1, cube2])
        rec1 = next(r for r in report.assets if _leaf(r.transform) == "Cube1")
        self.assertEqual(rec1.material.unique_paths_local, 0)
        self.assertEqual(rec1.material.texture_count, 1)

        tex2 = _write_png_header(os.path.join(self._dir, "own_Base_Color.png"), 64, 64)
        mat2, _ = _assign(cube1, "OwnTexMat")
        _texture(mat2, "color", tex2)

        report = self.audit([cube1, cube2])
        rec1 = next(r for r in report.assets if _leaf(r.transform) == "Cube1")
        self.assertEqual(rec1.material.unique_paths_local, 1)

    def test_print_report(self):
        """print_report renders an empty and a populated report without raising."""
        analyzer = SceneAnalyzer()
        analyzer.print_report(analyzer.generate_report(analyzer.analyze([])))
        cube = cmds.polyCube(name="PrintCube")[0]
        analyzer.print_report(analyzer.generate_report(analyzer.analyze([cube])))


class TestAuditInstancing(_AuditCase):
    """Rendered totals count every instance; unique totals count each shape once.

    Regression: the Entire Scene audit was handed ``cmds.ls(type="mesh")``, which
    names an instanced shape ONCE, and the resolver keyed shapes by path -- so a
    production assembly of 1,505 mesh instances (92 instanced shapes) audited as
    486 meshes with "0 instanced shapes" and a quarter of its real triangles.
    """

    def _build(self):
        cube = cmds.polyCube(name="box")[0]
        cmds.instance(cube, name="box_i1")
        cmds.instance(cube, name="box_i2")
        ball = cmds.polySphere(name="ball", subdivisionsX=8, subdivisionsY=6)[0]
        grp = cmds.group(ball, name="grp")
        cmds.group(grp, name="P1")
        p2 = cmds.group(empty=True, name="P2")
        cmds.parent(grp, p2, addObject=True)  # group instancing: two paths
        return cmds.polyEvaluate(ball, triangle=True)

    def test_entire_scene_counts_every_instance(self):
        ball_tris = self._build()
        report = self.audit(scope="all")

        inst = report.summary.instance_stats
        self.assertEqual((inst.unique_meshes, inst.total_instances), (2, 5))
        self.assertEqual(inst.instanced_shapes, 2)
        self.assertEqual(report.summary.total_tris, 3 * 12 + 2 * ball_tris)
        self.assertEqual(report.summary.raw_total_tris, 12 + ball_tris)
        self.assertEqual(
            {_leaf(r.mesh.shape_name): r.instance_count for r in report.assets},
            {"boxShape": 3, "ballShape": 2},
        )

    def test_shape_list_input_counts_every_instance(self):
        """The one-name-per-shape list the Entire Scene scope used to pass."""
        self._build()
        report = self.audit(cmds.ls(type="mesh", long=True, noIntermediate=True))
        self.assertEqual(report.summary.instance_stats.total_instances, 5)

    def test_selection_counts_only_the_selected_instances(self):
        self._build()
        cmds.select("|box_i1")
        self.assertEqual(
            [(_leaf(r.mesh.shape_name), r.instance_count) for r in self.audit().assets],
            [("boxShape", 1)],
        )
        cmds.select(["|P1", "|P2"])
        self.assertEqual(
            [(_leaf(r.mesh.shape_name), r.instance_count) for r in self.audit().assets],
            [("ballShape", 2)],
        )

    def test_a_component_selection_counts_its_own_instance(self):
        """Faces picked on one instance resolve to that instance's path; they
        were mapped to every parent of the shared shape (x3 the triangles)."""
        self._build()
        cmds.select("|box_i1.f[0:2]")
        self.assertEqual(
            [(_leaf(r.mesh.shape_name), r.instance_count) for r in self.audit().assets],
            [("boxShape", 1)],
        )

    def test_a_named_shape_beside_a_component_counts_every_instance(self):
        """The shape named outright is every instance's; faces picked on one
        instance of that same shape do not narrow it to that one."""
        self._build()
        cmds.select(["|box|boxShape", "|box_i1.f[0]"])
        self.assertEqual(
            [(_leaf(r.mesh.shape_name), r.instance_count) for r in self.audit().assets],
            [("boxShape", 3)],
        )

    def test_slot_compliance_weighs_instances_on_both_sides(self):
        """Instance-weighted slots over an instance-weighted allowance: three
        one-slot instances against a four-slot budget are 25%, not 75%."""
        cube = cmds.polyCube(name="slotted")[0]
        cmds.instance(cube)
        cmds.instance(cube)
        report = self.audit(scope="all", profile=AuditProfile(max_slots=4))
        self.assertAlmostEqual(report.budget.compliance.slots_pct, 25.0)

    def test_redundant_slots_are_counted_per_instance(self):
        """Two engines of one material on one instance are one slot too many,
        whatever another instance wears (the union across instances hid it)."""
        cube = cmds.polyCube(name="redundant")[0]
        inst = cmds.instance(cube, name="redundant_i1")[0]
        m1, _ = _assign(f"{cube}.f[0]", "m1")
        again = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name="m1SG2"
        )
        cmds.connectAttr(f"{m1}.outColor", f"{again}.surfaceShader", force=True)
        cmds.sets(f"{cube}.f[1]", edit=True, forceElement=again)
        _assign(f"{cube}.f[2]", "m2")
        _assign(f"{cube}.f[3]", "m3")
        _assign(f"{cube}.f[4:5]", "m4")
        _assign(f"|{inst}", "m5")

        (rec,) = self.audit(scope="all", profile=AuditProfile(max_slots=2)).assets
        self.assertEqual(rec.material.slot_count, 5)
        self.assertIn("consolidate_slots", [a.kind for a in rec.fix_plan])

    def test_instances_are_shaded_per_instance(self):
        """Slots are the busiest instance's; draw calls sum every instance's."""
        cube = cmds.polyCube(name="box")[0]
        cmds.instance(cube, name="box_i1")
        cmds.instance(cube, name="box_i2")
        _assign(f"{cube}.f[0:2]", "matA")  # |box: matA + the default on the rest
        _assign("|box_i2", "matB")  # |box_i1 stays on the default
        # The scene default's shader (standardSurface1 on Maya 2025, not lambert1).
        default = cmds.listConnections("initialShadingGroup.surfaceShader")[0]

        (rec,) = self.audit(scope="all").assets
        self.assertEqual(rec.instance_count, 3)
        self.assertEqual(rec.material.slot_count, 2)
        self.assertEqual(rec.material.draw_calls, 4)
        self.assertEqual(set(rec.material.materials), {"matA", "matB", default})


class TestAuditUvSets(_AuditCase):
    def test_leftover_uv_snapshots_are_flagged_not_budgeted(self):
        """A ``_uv_snap_*`` backup ships as TEXCOORD_1 -- flagged on its own,
        never mistaken for a second texture channel."""
        from mayatk.uv_utils._uv_utils import UvUtils

        cube = cmds.polyCube(name="snap")[0]
        UvUtils.snapshot_uv_sets([cube])
        report = self.audit([cube])
        rec = report.assets[0]

        self.assertEqual(len(rec.mesh.uv_snapshot_sets), 1)
        self.assertIn("uv_snapshots", _kinds(rec.findings))
        self.assertNotIn("extra_uv_sets", _kinds(rec.findings))
        self.assertEqual(rec.delta.uvs_over, 0)
        self.assertEqual(report.fix_actions[0].kind, "uv_snapshots")
        self.assertEqual(report.summary.uv_snapshot_meshes, 1)

    def test_a_lightmap_uv_set_is_within_budget(self):
        cube = cmds.polyCube(name="lm")[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        cmds.polyUVSet(shape, create=True, uvSet="lightmap")

        rec = self.audit([cube]).assets[0]
        self.assertEqual(rec.mesh.lightmap_uv_set, "lightmap")
        self.assertNotIn("extra_uv_sets", _kinds(rec.findings))

        cmds.polyUVSet(shape, create=True, uvSet="detail")
        rec = self.audit([cube]).assets[0]
        self.assertIn("extra_uv_sets", _kinds(rec.findings))


class TestAuditBudget(_AuditCase):
    def test_adaptive_budget_uses_world_size_in_cm(self):
        """A prop modelled small and scaled up is budgeted at the size it
        renders, and the UI unit changes nothing (polyEvaluate's object-space,
        UI-unit box shrank every budget 100x in a meter scene)."""
        cube = cmds.polyCube(name="scaled", width=1, height=1, depth=1)[0]
        cmds.scale(100, 100, 100, cube)
        profile = AuditProfile(adaptive_tris=True)
        diag = 3**0.5 * 100
        expected = int(profile.max_tris * min(1.0, diag / profile.reference_diag))

        rec = SceneAnalyzer().analyze([cube], profile=profile)[0]
        self.assertAlmostEqual(rec.mesh.bounds_diag, diag, places=3)
        self.assertEqual(rec.target_tris, expected)

        prior = cmds.currentUnit(query=True, linear=True)
        cmds.currentUnit(linear="m")
        try:
            rec_m = SceneAnalyzer().analyze([cube], profile=profile)[0]
        finally:
            cmds.currentUnit(linear=prior)
        self.assertEqual(rec_m.target_tris, expected)


class TestAuditTextures(_AuditCase):
    def test_texture_size_comes_from_the_header(self):
        """Dimensions from the file header: reading the file node's outSize
        decodes the image (~0.25 s per 4K PNG -- 98% of a production audit)."""
        path = _write_png_header(
            os.path.join(self._dir, "hdr_Base_Color.png"), 4096, 2048
        )
        cube = cmds.polyCube()[0]
        mat, _ = _assign(cube, "hdrMat")
        _texture(mat, "color", path)

        (tex,) = self.audit([cube]).textures.heaviest
        self.assertEqual((tex.width, tex.height), (4096, 2048))
        self.assertEqual(tex.map_type, "Base_Color")
        # BC1 (0.5 B/px) with a full mip chain.
        self.assertAlmostEqual(tex.gpu_mb, 4096 * 2048 * 0.5 * 4 / 3 / 2**20, places=3)

    def test_non_surface_maps_are_listed_not_counted(self):
        """StingrayPBS wires Maya's IBL cube maps / BRDF LUT onto every material;
        they inflated sampler counts and were typed "Specular" by substring."""
        env = _write_png_header(os.path.join(self._dir, "specular_cube.dds"), 256, 256)
        color = _write_png_header(
            os.path.join(self._dir, "env_Base_Color.png"), 512, 512
        )
        cube = cmds.polyCube()[0]
        mat, _ = _assign(cube, "envMat")
        _texture(mat, "color", color)
        _texture(mat, "ambientColor", env)  # no map-type token, not a surface slot

        report = self.audit([cube])
        self.assertEqual(
            [os.path.basename(f.path) for f in report.textures.heaviest],
            ["env_Base_Color.png"],
        )
        self.assertEqual(
            [os.path.basename(f.path) for f in report.textures.other],
            ["specular_cube.dds"],
        )
        self.assertEqual(report.materials[0].textures, [color])

    def test_a_filename_without_a_token_is_typed_by_its_slot(self):
        path = _write_png_header(os.path.join(self._dir, "decal.png"), 128, 128)
        cube = cmds.polyCube()[0]
        mat, _ = _assign(cube, "slotMat")
        _texture(mat, "color", path)
        (tex,) = self.audit([cube]).textures.heaviest
        self.assertEqual(tex.map_type, "Base_Color")

    def test_texture_cost_is_the_materials_not_each_meshs(self):
        path = _write_png_header(
            os.path.join(self._dir, "big_Base_Color.png"), 4096, 4096
        )
        c1 = cmds.polyCube(name="c1")[0]
        c2 = cmds.polyCube(name="c2")[0]
        mat, _ = _assign([c1, c2], "bigMat")
        _texture(mat, "color", path)
        profile = AuditProfile(max_tex_res=2048)

        report = self.audit([c1, c2], profile=profile)
        (audit,) = report.materials
        self.assertIn("max_tex_dim", _kinds(audit.findings))
        self.assertEqual((audit.mesh_count, audit.instance_count), (2, 2))
        for rec in report.assets:  # judged once, not on every mesh wearing it
            self.assertNotIn("max_tex_dim", _kinds(rec.findings))
            self.assertNotIn("oversized_texture", _kinds(rec.findings))

        # Unique to one small mesh, a 4K set is more than its size can show.
        cmds.delete(c2)
        rec = self.audit([c1], profile=profile).assets[0]
        (finding,) = [f for f in rec.findings if f.kind == "oversized_texture"]
        self.assertEqual(finding.detail["suggested"], 512)

    def test_missing_texture_is_reported(self):
        missing = os.path.join(self._dir, "gone_Base_Color.png").replace("\\", "/")
        cube = cmds.polyCube()[0]
        mat, _ = _assign(cube, "goneMat")
        _texture(mat, "color", missing)

        report = self.audit([cube])
        self.assertEqual([m.path for m in report.pipeline.missing_project], [missing])
        self.assertEqual(report.fix_actions[0].kind, "relink_textures")
        self.assertIn("missing_textures", _kinds(report.materials[0].findings))

    def test_relink_counts_the_materials_of_project_files_only(self):
        """Maya's own missing presets are listed apart; their materials are not
        what a relink touches ("used by 3 materials" for one project file)."""
        preset = os.path.join(os.environ["MAYA_LOCATION"], "presets", "gone_cube.dds")
        cube = cmds.polyCube()[0]
        own, _ = _assign(f"{cube}.f[0]", "ownMat")
        _texture(own, "color", os.path.join(self._dir, "lost_Base_Color.png"))
        for i in (1, 2):
            mat, _ = _assign(f"{cube}.f[{i}]", f"presetMat{i}")
            _texture(mat, "color", preset.replace("\\", "/"))

        (relink,) = [
            a for a in self.audit([cube]).fix_actions if a.kind == "relink_textures"
        ]
        self.assertIn("used by 1 material.", relink.message)

    def test_offenders_alone_still_judges_oversized_textures(self):
        """The oversized-texture finding reads texture data: an offenders-only run
        skipped the file reads, dropped the finding and reordered the table."""
        path = _write_png_header(
            os.path.join(self._dir, "solo_Base_Color.png"), 4096, 4096
        )
        cube = cmds.polyCube(name="solo")[0]
        mat, _ = _assign(cube, "soloMat")
        _texture(mat, "color", path)
        profile = AuditProfile(max_tex_res=2048)
        (rec,) = self.audit([cube], sections=["offenders"], profile=profile).assets
        self.assertIn("oversized_texture", _kinds(rec.findings))

    def test_an_intermediate_shape_in_the_shading_group_is_not_a_second_mesh(self):
        """An ``Orig`` shape still in the engine (FBX imports leave it there) made
        a unique texture set read as shared, so its oversized check never ran."""
        path = _write_png_header(
            os.path.join(self._dir, "orig_Base_Color.png"), 4096, 4096
        )
        cube = cmds.polyCube(name="deformed")[0]
        mat, sg = _assign(cube, "origMat")
        _texture(mat, "color", path)
        cmds.lattice(cube)  # a deformer: the mesh gains an Orig intermediate
        orig = [
            s
            for s in cmds.listRelatives(cube, shapes=True, fullPath=True)
            if cmds.getAttr(f"{s}.intermediateObject")
        ]
        self.assertTrue(orig)
        # forceElement refuses an intermediate; a scene file's own connectAttr
        # (what an FBX import writes) does not.
        cmds.connectAttr(
            f"{orig[0]}.instObjGroups[0]",
            f"{sg}.dagSetMembers",
            nextAvailable=True,
            force=True,
        )
        self.assertIn(_leaf(orig[0]), cmds.sets(sg, q=True))

        (rec,) = self.audit([cube], profile=AuditProfile(max_tex_res=2048)).assets
        self.assertIn("oversized_texture", _kinds(rec.findings))

    def test_a_frame_sequence_costs_one_frame_a_udim_set_every_tile(self):
        """A tile set is loaded whole; a ``<f>`` sequence one frame at a time --
        240 frames were costed as 240 tiles."""
        for frame in (1, 2, 3):
            _write_png_header(
                os.path.join(self._dir, f"flip_Emissive.{frame:04d}.png"), 256, 256
            )
        for tile in (1001, 1002):
            _write_png_header(
                os.path.join(self._dir, f"rock_Base_Color.{tile}.png"), 256, 256
            )
        cube = cmds.polyCube()[0]
        mat, _ = _assign(cube, "tokenMat")
        folder = self._dir.replace("\\", "/")
        _texture(mat, "incandescence", f"{folder}/flip_Emissive.<f>.png")
        _texture(mat, "color", f"{folder}/rock_Base_Color.<UDIM>.png")

        tiles = {
            os.path.basename(t.path): t.tiles
            for t in self.audit([cube]).textures.heaviest
        }
        self.assertEqual(
            tiles, {"flip_Emissive.<f>.png": 1, "rock_Base_Color.<UDIM>.png": 2}
        )

    def test_a_tokenless_map_behind_utility_nodes_is_typed_by_its_slot(self):
        """Traced upstream from the slot (``MatUtils.get_texture_file_node``), not
        one hop down from the file: two utility nodes in between left it untyped."""
        path = _write_png_header(os.path.join(self._dir, "wood.png"), 128, 128)
        cube = cmds.polyCube()[0]
        mat, _ = _assign(cube, "hopMat")
        file_node = cmds.shadingNode("file", asTexture=True)
        cmds.setAttr(f"{file_node}.fileTextureName", path, type="string")
        first = cmds.shadingNode("gammaCorrect", asUtility=True)
        second = cmds.shadingNode("gammaCorrect", asUtility=True)
        cmds.connectAttr(f"{file_node}.outColor", f"{first}.value", force=True)
        cmds.connectAttr(f"{first}.outValue", f"{second}.value", force=True)
        cmds.connectAttr(f"{second}.outValue", f"{mat}.color", force=True)

        (tex,) = self.audit([cube]).textures.heaviest
        self.assertEqual(tex.map_type, "Base_Color")


class TestAuditMaterials(_AuditCase):
    def test_transparency_is_the_materials(self):
        cube = cmds.polyCube()[0]
        mat, _ = _assign(cube, "glassMat")
        cmds.setAttr(f"{mat}.transparency", 0.5, 0.5, 0.5, type="double3")

        report = self.audit([cube])
        self.assertEqual(report.materials[0].transparency, "blend")
        self.assertEqual(report.summary.blend_meshes, 1)
        self.assertTrue(report.assets[0].material.uses_transparency)
        self.assertNotIn("transparency", _kinds(report.assets[0].findings))

    def test_blend_materials_count_each_instance_once(self):
        """One instance wearing two blended materials is one instance, not two."""
        cube = cmds.polyCube(name="glassy")[0]
        for name, faces in (("glassA", "f[0:2]"), ("glassB", "f[3:5]")):
            mat, _ = _assign(f"{cube}.{faces}", name)
            cmds.setAttr(f"{mat}.transparency", 0.5, 0.5, 0.5, type="double3")

        (action,) = [
            a for a in self.audit([cube]).fix_actions if a.kind == "blend_materials"
        ]
        self.assertIn("2 alpha-blended materials on 1 instance:", action.message)

    def test_a_mesh_without_a_material_is_flagged(self):
        cube = cmds.polyCube(name="bare")[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        cmds.sets(shape, remove="initialShadingGroup")

        report = self.audit([cube])
        rec = report.assets[0]
        self.assertEqual(rec.material.materials, [])
        self.assertIn("unassigned", _kinds(rec.findings))
        self.assertEqual(len(report.pipeline.unassigned_meshes), 1)
        self.assertEqual(rec.material.draw_calls, 1)  # it still renders


class TestAuditReport(_AuditCase):
    def test_overview_census(self):
        cube = cmds.polyCube()[0]
        cmds.instance(cube)
        cmds.select(clear=True)
        cmds.joint()
        cmds.select(clear=True)

        overview = self.audit([cube]).overview
        self.assertEqual(overview.counts["mesh_shapes"], 1)
        self.assertEqual(overview.counts["mesh_instances"], 2)
        # The scene's own: the four startup cameras' transforms are not counted.
        self.assertEqual(overview.counts["transforms"], 2)
        self.assertEqual(overview.counts["joints"], 1)
        self.assertEqual(overview.linear_unit, cmds.currentUnit(q=True, linear=True))
        self.assertEqual(overview.scene_path, "")  # batch's phantom "untitled" dropped
        self.assertEqual(overview.unknown_nodes, 0)

    def test_unrequested_sections_skip_their_collection(self):
        cube = cmds.polyCube()[0]
        report = self.audit([cube], sections=["overview"])
        self.assertFalse(report.manifest.materials_collected)
        self.assertEqual(report.manifest.shading_engine_count, 0)
        # Not collected is not "unassigned" -- per mesh, in the pipeline or as a fix.
        self.assertNotIn("unassigned", _kinds(report.assets[0].findings))
        self.assertEqual(report.pipeline.unassigned_meshes, [])
        self.assertNotIn("unassigned_materials", _kinds(report.fix_actions))

    def test_sections_without_mesh_checks_skip_them(self):
        """Topology checks (polyInfo) run only for a section that shows them."""
        cube = cmds.polyCube()[0]
        with mock.patch.object(cmds, "polyInfo", wraps=cmds.polyInfo) as info:
            (rec,) = SceneAnalyzer().analyze([cube], sections=["pareto"])
        info.assert_not_called()
        self.assertEqual(rec.mesh.tris, 12)
        with mock.patch.object(cmds, "polyInfo", wraps=cmds.polyInfo) as info:
            SceneAnalyzer().analyze([cube], sections=["offenders"])
        info.assert_called()

    def test_a_scope_without_meshes_still_reports_scene_hazards(self):
        """Geometry parked in an unloaded reference audits no mesh at all -- the
        scene's own hazards still belong in Fix First and Pipeline."""
        cmds.createNode("unknown", name="mystery")
        loc = cmds.spaceLocator()[0]
        report = self.audit([loc])
        self.assertEqual(report.assets, [])
        self.assertIn("unknown_nodes", _kinds(report.fix_actions))
        text = SceneAnalyzer.format_audit_text(
            objects=[loc], sections=["fix_first", "pipeline"]
        )
        self.assertIn("unknown node", text["fix_first"])
        self.assertIn("unknown node", text["pipeline"])

    def test_multi_material_table_is_filtered_before_it_is_cut(self):
        """Twelve instanced single-slot meshes out-drawing the one two-slot mesh
        emptied the multi-material table: it was cut to twelve, then filtered."""
        for i in range(SceneAnalyzer.TABLE_ROWS):
            cube = cmds.polyCube(name=f"single{i}")[0]
            cmds.instance(cube)
            cmds.instance(cube)
        multi = cmds.polyCube(name="multi")[0]
        _assign(f"{multi}.f[0]", "mmA")

        pareto = SceneAnalyzer.format_audit_text(scope="all", sections=["pareto"])
        table = pareto["pareto"].split("Multi-material meshes by draw calls", 1)[1]
        self.assertIn("multi", table)

    def test_counts_read_as_english(self):
        cube = cmds.polyCube(name="glass")[0]
        mat, _ = _assign(cube, "glassMat")
        cmds.setAttr(f"{mat}.transparency", 0.5, 0.5, 0.5, type="double3")
        text = SceneAnalyzer.format_audit_text(objects=[cube])
        joined = "\n".join(text.values())
        self.assertIn("1 mesh alpha-blended", text["summary"])
        self.assertIn("the top mesh carries 100%", text["pareto"])
        self.assertIn("Every mesh has a material", text["pipeline"])
        self.assertNotIn("1 meshes", joined)

        rough = _write_png_header(
            os.path.join(self._dir, "big_Roughness.png"), 4096, 4096
        )
        _texture(mat, "ambientColor", rough)  # typed by its name
        report = self.audit([cube], profile=AuditProfile(max_texture_mb=1))
        (memory,) = [a for a in report.fix_actions if a.kind == "texture_memory"]
        self.assertIn("Halving the non-detail 4K map ", memory.message)

    def test_format_audit_html(self):
        path = _write_png_header(
            os.path.join(self._dir, "tiny_Base_Color.png"), 256, 256
        )
        cube = cmds.polyCube(name="linkCube")[0]
        mat, _ = _assign(cube, "tinyMat")
        _texture(mat, "color", path)

        html = SceneAnalyzer.format_audit_html(objects=[cube])
        self.assertEqual(list(html), ["_header"] + list(SceneInfoSection.ALL))
        joined = "".join(html.values())
        self.assertNotIn("\n", joined)  # the viewer turns free newlines into <br>
        self.assertIn("<table", joined)
        self.assertIn("action://select?node=%7ClinkCube", joined)
        # The <512 histogram bucket is text, not a tag the renderer swallows.
        self.assertIn("&lt;512 1", joined)

    def test_a_tile_set_links_a_file_that_opens(self):
        """A ``<UDIM>`` path names nothing on disk (a link to it raised the
        OS's "cannot find" dialog): the link opens the set's first real tile,
        and a set may start past 1001."""
        for tile in (1002, 1003):
            _write_png_header(
                os.path.join(self._dir, f"slab_Base_Color.{tile}.png"), 256, 256
            )
        cube = cmds.polyCube(name="slab")[0]
        mat, _ = _assign(cube, "slabMat")
        folder = self._dir.replace("\\", "/")
        _texture(mat, "color", f"{folder}/slab_Base_Color.<UDIM>.png")

        html = SceneAnalyzer.format_audit_html(objects=[cube], sections=["textures"])
        textures = html["textures"]
        self.assertIn("slab_Base_Color.1002.png", textures)
        self.assertNotIn("%3CUDIM%3E", textures)
        self.assertIn("slab_Base_Color.&lt;UDIM&gt;.png", textures)  # still the name

    def test_format_audit_text(self):
        cube = cmds.polyCube()[0]
        text = SceneAnalyzer.format_audit_text(
            objects=[cube], sections=["summary", "pipeline"]
        )
        self.assertEqual(list(text), ["_header", "summary", "pipeline"])
        self.assertIn("Executive Summary", text["summary"])
        self.assertNotIn("<", text["summary"])  # plain text, no markup

    def test_an_unloaded_reference_is_a_fix_first_entry(self):
        """Its meshes are in neither the audit nor an export -- said once, in
        Fix First, rather than as a quietly smaller scene."""
        cmds.polyCube(name="refBox")
        ref_path = os.path.join(self._dir, "unloaded_ref.ma").replace("\\", "/")
        cmds.file(rename=ref_path)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)
        ref_node = cmds.referenceQuery(
            cmds.file(ref_path, reference=True, namespace="gone"),
            referenceNode=True,
        )
        cmds.file(unloadReference=ref_node)
        cube = cmds.polyCube(name="here")[0]

        report = self.audit([cube])
        (action,) = [a for a in report.fix_actions if a.kind == "unloaded_references"]
        self.assertIn("1 unloaded reference", action.message)

    def test_retired_categories_key_resolves_to_materials(self):
        cube = cmds.polyCube()[0]
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            html = SceneAnalyzer.format_audit_html(
                objects=[cube], sections=["categories"]
            )
        self.assertEqual(list(html), ["_header", "materials"])
        self.assertTrue(any(issubclass(w.category, DeprecationWarning) for w in caught))


class TestSceneRepair(MayaTkTestCase):
    """SceneDiagnostics — repair helpers (clean-scene smoke)."""

    def test_fix_unknown_plugins_clean_scene_returns_empty(self):
        result = SceneDiagnostics.fix_unknown_plugins(dry_run=True, verbose=False)
        self.assertEqual(result, {"nodes": [], "plugins": []})

    def test_cleanup_scene_returns_summary(self):
        result = SceneDiagnostics.cleanup_scene(quiet=True)
        self.assertIn("unknown", result)
        self.assertEqual(result["xgen_removed"], 0)


class TestOcioProfileVersionGate(MayaTkTestCase):
    """fix_ocio must never adopt a config this Maya's OCIO runtime cannot load.

    Regression: Maya ships no ``PyOpenColorIO``, so config validation fell through
    to a text heuristic that accepts ANY well-formed config — including Blender
    5.1's ``ocio_profile_version: 2.5``, which Maya 2025 (OCIO 2.3) rejects at
    startup. A shared ``$OCIO`` between the two apps therefore survived the repair.
    """

    def setUp(self):
        super().setUp()
        self._prior_ocio = os.environ.get("OCIO")
        self._tmp = tempfile.mkdtemp(prefix="mtk_ocio_")

    def tearDown(self):
        if self._prior_ocio is None:
            os.environ.pop("OCIO", None)
        else:
            os.environ["OCIO"] = self._prior_ocio
        shutil.rmtree(self._tmp, ignore_errors=True)
        super().tearDown()

    def _write_config(self, version):
        path = os.path.join(self._tmp, f"config_{version.replace('.', '_')}.ocio")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"ocio_profile_version: {version}\nroles:\n  scene_linear: lin\n")
        return path

    def test_ocio_profile_version_parses_the_header(self):
        self.assertEqual(
            SceneDiagnostics._ocio_profile_version(self._write_config("2.5")), (2, 5)
        )
        self.assertEqual(
            SceneDiagnostics._ocio_profile_version(self._write_config("2")), (2, 0)
        )
        self.assertIsNone(SceneDiagnostics._ocio_profile_version(self._tmp))

    def test_ceiling_comes_from_mayas_shipped_ocio_library(self):
        ceiling = SceneDiagnostics._max_ocio_profile_version(
            os.environ.get("MAYA_LOCATION")
        )
        self.assertIsNotNone(ceiling, "MAYA_LOCATION/bin ships no OpenColorIO library")
        self.assertEqual(ceiling[0], 2)
        # An unknown ceiling must not gate anything (unknown != low).
        self.assertIsNone(SceneDiagnostics._max_ocio_profile_version(None))

    def test_fix_ocio_refuses_an_env_config_newer_than_the_runtime(self):
        ceiling = SceneDiagnostics._max_ocio_profile_version(
            os.environ.get("MAYA_LOCATION")
        )
        os.environ["OCIO"] = self._write_config(f"{ceiling[0]}.{ceiling[1] + 2}")
        result = SceneDiagnostics.fix_ocio(dry_run=True, verbose=False)
        self.assertNotEqual(result["new_config"], os.environ["OCIO"])
        self.assertTrue(
            any("newer than this Maya's OCIO runtime" in n for n in result["notes"]),
            result["notes"],
        )

    def test_fix_ocio_still_honors_an_env_config_the_runtime_can_load(self):
        ceiling = SceneDiagnostics._max_ocio_profile_version(
            os.environ.get("MAYA_LOCATION")
        )
        os.environ["OCIO"] = self._write_config(f"{ceiling[0]}.{ceiling[1]}")
        result = SceneDiagnostics.fix_ocio(dry_run=True, verbose=False)
        self.assertEqual(result["new_config"], os.environ["OCIO"])


if __name__ == "__main__":
    unittest.main()
