# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.uv_utils.texture_combine (TextureCombine -- in-place combine).

The plan and pack arithmetic is pinned in pythontk's ``test_uv_combine``; these
cover what the Maya adapter adds: gathering islands per material, the in-place
write-back (no duplicated meshes, no lasting UV set), the transfer into one
material, instances, the record, restore / re-combine / update, and the
lightmap consolidation.
"""

import os
import unittest

import numpy as np
import maya.cmds as cmds
import maya.api.OpenMaya as om
import pythontk as ptk
from PIL import Image

from base_test import MayaTkTestCase
from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords
from mayatk.uv_utils.texture_combine.texture_combine import (
    CombineInputs,
    TextureCombine,
)
from mayatk.uv_utils.texture_transfer import TextureTransfer


class TestTextureCombine(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self._artifacts = ptk.TempArtifacts("texture_combine_test", policy="scoped")
        self.addCleanup(self._artifacts.cleanup)
        self.tmp = self._artifacts.dir_path()
        self.out_dir = os.path.join(self.tmp, "out").replace("\\", "/")
        self.red = self._image("red.png", (255, 0, 0))
        self.blue = self._image("blue.png", (0, 0, 255))

    # ------------------------------------------------------------ helpers
    def _image(self, name, rgb, size=32):
        path = os.path.join(self.tmp, name).replace("\\", "/")
        Image.fromarray(np.full((size, size, 3), rgb, np.uint8)).save(path)
        return path

    def _plane(self, name, x=0.0, uv_scale=1.0):
        plane = cmds.polyPlane(name=name, sx=1, sy=1, w=1, h=1, ch=False)[0]
        cmds.move(x, 0, 0, plane)
        if uv_scale != 1.0:
            cmds.polyEditUV(
                f"{plane}.map[*]", scaleU=uv_scale, scaleV=uv_scale, pivotU=0, pivotV=0
            )
        return cmds.ls(plane, long=True)[0]

    def _lambert(self, name, texture):
        mat = cmds.shadingNode("lambert", asShader=True, name=name)
        sg = cmds.sets(
            name=f"{name}SG", renderable=True, noSurfaceShader=True, empty=True
        )
        cmds.connectAttr(f"{mat}.outColor", f"{sg}.surfaceShader")
        f = cmds.shadingNode("file", asTexture=True, name=f"{name}_file")
        cmds.setAttr(f"{f}.fileTextureName", texture, type="string")
        cmds.connectAttr(f"{f}.outColor", f"{mat}.color")
        return mat, sg

    def _scene(self):
        """Two materials; plane A2 is a stacked copy of A1 (same UVs)."""
        a1, a2 = self._plane("A1", 0), self._plane("A2", 2)
        b = self._plane("B", 4)
        _, sg_a = self._lambert("matA", self.red)
        _, sg_b = self._lambert("matB", self.blue)
        cmds.sets([a1, a2], e=True, forceElement=sg_a)
        cmds.sets(b, e=True, forceElement=sg_b)
        return a1, a2, b

    @staticmethod
    def _uvs(obj, uv_set="map1"):
        sel = om.MSelectionList()
        sel.add(obj)
        dag = sel.getDagPath(0)
        dag.extendToShape()
        us, vs = om.MFnMesh(dag).getUVs(uv_set)
        return np.column_stack([np.asarray(us), np.asarray(vs)])

    def _combine(self, objects, **kwargs):
        kwargs.setdefault("map_size", 64)
        kwargs.setdefault("padding", 2)
        kwargs.setdefault("supersample", 1)
        kwargs.setdefault("output_dir", self.out_dir)
        kwargs.setdefault("name", "kit")
        kwargs.setdefault("sizing", "density")
        return TextureCombine().combine(objects, **kwargs)

    @staticmethod
    def _sample(path, uv):
        img = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
        h, w = img.shape[:2]
        x = min(w - 1, int(uv[0] * w))
        y = min(h - 1, int((1.0 - uv[1]) * h))
        return img[y, x]

    # -------------------------------------------------------------- tests
    def test_combine_in_place_one_material_stacks_kept(self):
        a1, a2, b = self._scene()
        sets_before = cmds.polyUVSet(a1, query=True, allUVSets=True)
        result = self._combine([a1, a2, b])

        mats = {TextureTransfer.face_materials(o)[0][0] for o in (a1, a2, b)}
        self.assertEqual(len(mats), 1, mats)
        self.assertEqual(cmds.polyUVSet(a1, query=True, allUVSets=True), sets_before)
        self.assertEqual(len(cmds.ls(type="mesh")), 3)  # nothing duplicated
        ua1, ua2, ub = self._uvs(a1), self._uvs(a2), self._uvs(b)
        np.testing.assert_allclose(ua1, ua2, atol=1e-6)  # the stack stays stacked
        for u in (ua1, ub):
            self.assertTrue((u >= -1e-6).all() and (u <= 1 + 1e-6).all())
        apart = (ua1.max(0) <= ub.min(0) + 1e-6).any() or (
            ub.max(0) <= ua1.min(0) + 1e-6
        ).any()
        self.assertTrue(apart, "the two materials' islands overlap")
        color = result["maps"]["baseColor"]
        self.assertGreater(self._sample(color, ua1.mean(0))[0], 200)  # red
        self.assertGreater(self._sample(color, ub.mean(0))[2], 200)  # blue
        self.assertEqual(TextureCombine.names(), ["kit"])
        self.assertEqual(TextureCombine.owner([b]), "kit")

    def test_restore_is_exact(self):
        a1, a2, b = self._scene()
        before = {o: self._uvs(o).astype(np.float32) for o in (a1, a2, b)}
        self._combine([a1, a2, b])
        restored = TextureCombine().restore("kit")
        self.assertEqual(set(restored), {a1, a2, b})
        for o in (a1, a2, b):
            np.testing.assert_allclose(self._uvs(o), before[o], atol=1e-7)
        self.assertEqual(TextureTransfer.face_materials(a1)[0], ["matA"])
        self.assertEqual(TextureTransfer.face_materials(b)[0], ["matB"])
        self.assertEqual(TextureCombine.names(), [])

    def test_inputs_for_rebuilds_the_pre_combine_state(self):
        """What the panel re-plans an Update against: the ORIGINAL materials and
        UVs, read back from the record over the live topology."""
        a1, a2, b = self._scene()
        before = TextureCombine().gather([a1, a2, b])
        self._combine([a1, a2, b])
        after = TextureCombine().inputs_for("kit")
        self.assertEqual(set(after.sources), {"matA", "matB"})
        self.assertEqual(len(after.shells), len(before.shells))
        for uuid, read in before.shapes.items():
            np.testing.assert_allclose(
                after.shapes[uuid]["uvs"], read["uvs"].astype(np.float32), atol=1e-7
            )

    def test_recombine_reads_a_revised_source_into_the_same_layout(self):
        a1, a2, b = self._scene()
        result = self._combine([a1, a2, b])
        layout = self._uvs(a1)
        self.assertEqual(TextureCombine.stale("kit"), [])
        os.utime(self.red, None)
        self._image("red.png", (0, 255, 0))  # the source revised: now green
        self.assertIn(self.red, TextureCombine.stale("kit"))

        again = TextureCombine().recombine("kit")
        np.testing.assert_allclose(self._uvs(a1), layout, atol=1e-7)
        self.assertEqual(again["maps"]["baseColor"], result["maps"]["baseColor"])
        self.assertGreater(
            self._sample(again["maps"]["baseColor"], layout.mean(0))[1], 200
        )
        self.assertEqual(TextureCombine.stale("kit"), [])

    def test_material_row_scales_that_material(self):
        a1, _, b = self._scene()
        self._combine(
            [a1, b], rows=[{"kind": "material", "target": "matB", "scale": 0.5}]
        )
        span_a = np.ptp(self._uvs(a1), axis=0).max()
        span_b = np.ptp(self._uvs(b), axis=0).max()
        self.assertAlmostEqual(span_b / span_a, 0.5, delta=0.02)

    def test_proportional_keeps_a_materials_own_ratios(self):
        a1 = self._plane("A1", 0)
        a2 = self._plane("A2", 2, uv_scale=0.5)  # half the texels, same surface
        b = self._plane("B", 4)
        _, sg_a = self._lambert("matA", self.red)
        _, sg_b = self._lambert("matB", self.blue)
        cmds.sets([a1, a2], e=True, forceElement=sg_a)
        cmds.sets(b, e=True, forceElement=sg_b)
        self._combine([a1, a2, b], sizing="proportional")
        ratio = (
            np.ptp(self._uvs(a2), axis=0).max() / np.ptp(self._uvs(a1), axis=0).max()
        )
        self.assertAlmostEqual(ratio, 0.5, delta=0.02)

    def test_instances_all_receive_the_material(self):
        a1, _, b = self._scene()
        inst = cmds.instance(a1, name="A1_inst")[0]
        inst = cmds.ls(inst, long=True)[0]
        self._combine([a1, b])  # the instance is not picked
        mats = {TextureTransfer.face_materials(o)[0][0] for o in (a1, inst, b)}
        self.assertEqual(len(mats), 1)

    def test_name_and_membership_collisions_refuse(self):
        a1, a2, b = self._scene()
        self._combine([a1, b])
        with self.assertRaises(ValueError):
            self._combine([a2], name="kit")
        with self.assertRaises(ValueError):
            self._combine([a1, a2], name="other")

    def test_update_repacks_with_new_settings(self):
        a1, _, b = self._scene()
        self._combine([a1, b])
        TextureCombine().update(
            "kit", rows=[{"kind": "material", "target": "matB", "scale": 0.5}]
        )
        self.assertEqual(TextureCombine.names(), ["kit"])
        ratio = np.ptp(self._uvs(b), axis=0).max() / np.ptp(self._uvs(a1), axis=0).max()
        self.assertAlmostEqual(ratio, 0.5, delta=0.02)

    def test_one_undo_takes_an_update_back(self):
        """Restore + re-combine are ONE step: Ctrl+Z returns the combine as it
        was, not the restored originals."""
        a1, _, b = self._scene()
        self._combine([a1, b])
        combined = self._uvs(b)
        TextureCombine().update(
            "kit", rows=[{"kind": "material", "target": "matB", "scale": 0.5}]
        )
        cmds.undo()
        np.testing.assert_allclose(self._uvs(b), combined, atol=1e-6)
        self.assertEqual(TextureCombine.names(), ["kit"])

    def test_auto_spacing_is_recorded_as_auto(self):
        """Auto (None) is kept, so an update at another atlas size re-derives
        the gutter instead of keeping this atlas's."""
        a1, _, b = self._scene()
        self._combine([a1, b], padding=None)
        self.assertIsNone(TextureCombine.entry("kit")["settings"]["padding"])

    def _lightmap_sets(self, *objects):
        for o in objects:
            cmds.polyUVSet(o, copy=True, uvSet="map1", newUVSet="lightmap")
            cmds.polyUVSet(o, currentUVSet=True, uvSet="map1")

    def test_exclusive_lightmap_atlas_is_renamed_to_the_combine(self):
        a1, _, b = self._scene()
        self._lightmap_sets(a1, b)
        atlas = self._image("matA_Lightmap.png", (128, 128, 128), size=16)
        LightmapRecords.commit({a1: atlas, b: atlas}, written=False)
        result = self._combine([a1, b])
        self.assertEqual(result["lightmaps"]["how"], "renamed")
        info = LightmapRecords.lightmap_info(a1)
        self.assertEqual(info["map"], "kit_Lightmap.png")
        self.assertEqual(LightmapRecords.lightmap_info(b)["map"], "kit_Lightmap.png")
        # In the session until the scene is saved (LightmapRecords._stage).
        renamed = os.path.join(self.tmp, "kit_Lightmap.png")
        self.assertTrue(os.path.isfile(LightmapRecords._stage(renamed)))
        self.assertFalse(os.path.isfile(renamed), "nothing in the folder before the save")
        # Outside any project and never written by this scene: the re-bake
        # guard leaves the old file for whoever else may read it.
        self.assertTrue(os.path.isfile(atlas))

    def test_lightmap_shared_with_others_is_not_renamed(self):
        a1, a2, b = self._scene()
        self._lightmap_sets(a1, a2, b)
        atlas = self._image("room_Lightmap.png", (128, 128, 128), size=16)
        LightmapRecords.commit({a1: atlas, a2: atlas}, written=False)
        result = self._combine([a1, b])  # a2 keeps reading the atlas
        self.assertEqual(result["lightmaps"]["how"], "repacked")
        self.assertTrue(os.path.isfile(atlas))
        # a1 moved to the combine's own atlas; b had none, so it is listed.
        moved = LightmapRecords.lightmap_info(a1)
        self.assertTrue(moved["map"].startswith("kit_Lightmap"), moved["map"])
        self.assertTrue(moved.get("path") and os.path.isfile(moved["path"]))
        self.assertEqual(result["lightmaps"]["unbaked"], [b])
        self.assertEqual(LightmapRecords.lightmap_info(a2)["map"], "room_Lightmap.png")

    def test_renamed_atlas_steps_aside_from_a_name_another_object_reads(self):
        a1, a2, b = self._scene()
        self._lightmap_sets(a1, a2, b)
        own = self._image("matA_Lightmap.png", (128, 128, 128), size=16)
        held = self._image("kit_Lightmap.png", (64, 64, 64), size=16)
        LightmapRecords.commit({a1: own, b: own, a2: held}, written=False)
        result = self._combine([a1, b])  # a2 reads kit_Lightmap.png
        self.assertEqual(result["lightmaps"]["how"], "renamed")
        self.assertEqual(result["lightmaps"]["map"], "kit_Lightmap_1.png")
        self.assertEqual(LightmapRecords.lightmap_info(a1)["map"], "kit_Lightmap_1.png")
        self.assertEqual(LightmapRecords.lightmap_info(a2)["map"], "kit_Lightmap.png")

    def test_renamed_atlas_steps_aside_from_another_scenes_map_of_its_name(self):
        """Review 2026-10-24: a file another scene wrote under the combine's
        name -- read by nothing here, so no claim -- was taken over at the
        save (LightmapRecords.strangers)."""
        a1, _, b = self._scene()
        self._lightmap_sets(a1, b)
        own = self._image("matA_Lightmap.png", (128, 128, 128), size=16)
        self._image("kit_Lightmap.png", (64, 64, 64), size=16)  # theirs
        LightmapRecords.commit({a1: own, b: own}, written=False)
        result = self._combine([a1, b])
        self.assertEqual(result["lightmaps"]["how"], "renamed")
        self.assertEqual(result["lightmaps"]["map"], "kit_Lightmap_1.png")
        self.assertEqual(LightmapRecords.lightmap_info(a1)["map"], "kit_Lightmap_1.png")

    def test_lightmaps_gone_from_disk_leave_the_combine_standing(self):
        a1, _, b = self._scene()
        self._lightmap_sets(a1, b)
        one = self._image("one_Lightmap.png", (128, 128, 128), size=16)
        two = self._image("two_Lightmap.png", (64, 64, 64), size=16)
        LightmapRecords.commit({a1: one, b: two}, written=False)
        os.remove(one)
        os.remove(two)
        result = self._combine([a1, b])
        self.assertIsNone(result["lightmaps"]["how"])
        self.assertEqual(TextureCombine.names(), ["kit"])
        self.assertEqual(LightmapRecords.lightmap_info(a1)["map"], "one_Lightmap.png")

    def test_a_lightmap_uv_set_removed_since_the_bake_skips_its_cell(self):
        a1, _, b = self._scene()
        self._lightmap_sets(a1, b)
        one = self._image("one_Lightmap.png", (128, 128, 128), size=16)
        two = self._image("two_Lightmap.png", (64, 64, 64), size=16)
        LightmapRecords.commit({a1: one, b: two}, written=False)
        cmds.polyUVSet(b, delete=True, uvSet="lightmap")
        result = self._combine([a1, b])
        self.assertEqual(result["lightmaps"]["how"], "repacked")
        moved = LightmapRecords.lightmap_info(a1)["map"]
        self.assertTrue(moved.startswith("kit_Lightmap"), moved)
        self.assertEqual(LightmapRecords.lightmap_info(b)["map"], "two_Lightmap.png")


class TestPanelSelection(MayaTkTestCase):
    """What the shared Texture Combine panel (tentacle's
    ``slots/tools/texture_combine.py``, whose own tests are there) asks of the
    host beside the combine: the selection it sets and reads, and a row label."""

    @staticmethod
    def _plane(name, x=0.0):
        plane = cmds.polyPlane(name=name, sx=2, sy=1, w=1, h=1, ch=False)[0]
        cmds.move(x, 0, 0, plane)
        return cmds.ls(plane, long=True)[0]

    def test_select_takes_objects_and_faces_and_counts_what_exists(self):
        a, b = self._plane("selA"), self._plane("selB", 2)
        self.assertEqual(TextureCombine.select([a, "|goneForever"]), 1)
        self.assertEqual(cmds.ls(selection=True, long=True), [a])
        self.assertEqual(TextureCombine.select([f"{b}.f[1]"]), 1)
        self.assertEqual(cmds.ls(selection=True, long=True), [f"{b}.f[1]"])
        # Nothing that exists: the selection stays as it is.
        self.assertEqual(TextureCombine.select(["|goneForever"]), 0)
        self.assertEqual(cmds.ls(selection=True, long=True), [f"{b}.f[1]"])

    def test_selected_faces_reads_faces_or_converts_the_selection(self):
        a = self._plane("sfA")
        cmds.select(f"{a}.f[1]", replace=True)
        self.assertEqual(TextureCombine.selected_faces(), [f"{a}.f[1]"])
        cmds.select(f"{a}.map[0]", replace=True)
        self.assertTrue(TextureCombine.selected_faces()[0].startswith(f"{a}.f["))
        cmds.select(clear=True)
        self.assertEqual(TextureCombine.selected_faces(), [])

    def test_material_faces_names_a_materials_faces_on_each_mesh(self):
        a = self._plane("mfA")
        inputs = CombineInputs(transforms=[a])
        inputs.shapes["shape"] = {
            "materials": ["matX", "matY"],
            "face_material": np.array([0, 1, 1]),
            "transforms": [a],
        }
        self.assertEqual(TextureCombine.material_faces(inputs, "matY"), [f"{a}.f[1:2]"])
        self.assertEqual(TextureCombine.material_faces(inputs, "other"), [])

    def test_leaf_name_drops_the_path_and_the_component(self):
        self.assertEqual(TextureCombine.leaf_name("|grp|pCube1.f[0:3]"), "pCube1")
        self.assertEqual(TextureCombine.leaf_name("|grp|pCube1"), "pCube1")

    def test_default_name_falls_back_to_the_callers_word(self):
        engine = TextureCombine(log_level="WARNING")
        self.assertEqual(engine.default_name([]), "combined")
        self.assertEqual(engine.default_name([], fallback="transfer"), "transfer")
        self.assertEqual(engine.default_name(["|hero_GRP|hero_body_GEO"]), "hero_body")


class TestTransferSelectionSides(MayaTkTestCase):
    """``TextureTransfer.selection_sides``: Maya's selection ORDER read into the
    Transfer mode's source and targets (the panel's gates and report are
    tentacle's ``test_texture_combine_panel``)."""

    @staticmethod
    def _cube(name, x=0.0):
        cube = cmds.polyCube(name=name)[0]
        cmds.move(x, 0, 0, cube)
        return cmds.ls(cube, long=True)[0]

    def _joined(self):
        """srcA (cube), srcB (sphere) and their combine under ``out``."""
        a = cmds.polyCube(name="srcA")[0]
        b = cmds.polySphere(name="srcB", sx=6, sy=4)[0]
        cmds.move(3, 0, 0, b)
        c = cmds.polyUnite(cmds.duplicate([b, a]), ch=False, name="comb")[0]
        c = cmds.rename(cmds.parent(c, cmds.group(empty=True, name="out"))[0], a)
        return [cmds.ls(n, long=True)[0] for n in (f"|{a}", f"|{b}", c)]

    def test_first_selected_is_the_source_of_every_other(self):
        a, b, c = (self._cube(f"tsFirst{n}", i * 2) for i, n in enumerate("abc"))
        cmds.select([a, b, c], replace=True)
        self.assertEqual(TextureTransfer.selection_sides("first"), ("first", [a], [b, c]))

    def test_last_selected_is_the_target_of_every_other(self):
        a, b, c = self._joined()
        cmds.select([a, b, c], replace=True)
        self.assertEqual(TextureTransfer.selection_sides("last"), ("last", [a, b], [c]))

    def test_auto_finds_the_combined_target_wherever_it_was_picked(self):
        a, b, c = self._joined()
        cmds.select([c, a, b], replace=True)
        self.assertEqual(TextureTransfer.selection_sides(), ("last", [a, b], [c]))

    def test_auto_reads_one_mesh_as_its_own_uv_sets(self):
        a, _, _ = self._joined()
        cmds.select(a, replace=True)
        self.assertEqual(TextureTransfer.selection_sides(), ("uvset", None, [a]))

    def test_auto_falls_back_to_first_selected(self):
        a, b, _ = self._joined()
        cmds.select([b, a], replace=True)
        self.assertEqual(TextureTransfer.selection_sides(), ("first", [b], [a]))

    def test_auto_reads_stored_sources_that_match_the_selection(self):
        a, b, c = self._joined()
        cmds.select(c, replace=True)
        self.assertEqual(
            TextureTransfer.selection_sides("auto", stored=[a, b]),
            ("stored", [a, b], [c]),
        )

    def test_auto_ignores_a_stale_capture_and_a_deleted_one(self):
        a, _, _ = self._joined()
        torus = cmds.ls(cmds.polyTorus()[0], long=True)[0]
        cmds.select(a, replace=True)
        for stored in ([torus], ["|goneForever"]):
            self.assertEqual(
                TextureTransfer.selection_sides("auto", stored=stored),
                ("uvset", None, [a]),
            )

    def test_a_group_gives_its_meshes(self):
        src = cmds.group(empty=True, name="tsKit_ORIG")
        for n in ("pA", "pB"):
            cmds.parent(cmds.polyCube(name=n)[0], src)
        parts = cmds.listRelatives(src, children=True, fullPath=True)
        tgt = self._cube("tsKitTarget", 4)
        cmds.select([src, tgt], replace=True)
        mode, source, targets = TextureTransfer.selection_sides("first")
        self.assertEqual(sorted(source), sorted(parts))
        self.assertEqual(targets, [tgt])

    def test_capture_stores_long_names_and_live_skips_the_deleted(self):
        a, b = self._cube("tsCapA"), self._cube("tsCapB", 2)
        cmds.select([a, b], replace=True)
        names = TextureTransfer.capture_sources()
        self.assertEqual(names, [a, b])
        cmds.delete(b)
        self.assertEqual(TextureTransfer.live_sources(names), [a])

    def test_shader_types_and_terms(self):
        types = TextureTransfer.shader_types()
        self.assertTrue(types)
        self.assertTrue(all(isinstance(v, str) and v for v in types.values()))
        self.assertEqual(TextureTransfer.TERMS["uv"], "UV Set")
        self.assertEqual(TextureTransfer.TERMS["first"], "First Selected Mesh")


if __name__ == "__main__":
    unittest.main()
