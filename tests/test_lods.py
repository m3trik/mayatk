# !/usr/bin/python
# coding=utf-8
"""Tests for ``mtk.LodUtils``: LOD groups in Maya, to the one convention.

A LOD group is a ``lodGroup`` ``<base>`` whose children ``<base>_LOD0..n`` are
its levels, LOD0 the most detailed (``ptk.LodChain``).
"""

import unittest

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)


import mayatk as mtk
from base_test import MayaTkTestCase


def _tris(node):
    return cmds.polyEvaluate(node, triangle=True)


class TestGenerate(MayaTkTestCase):
    def test_a_mesh_becomes_a_lod_group_of_falling_levels(self):
        sphere = cmds.polySphere(name="ball", sx=40, sy=40)[0]
        cmds.move(1, 2, 3, sphere)
        (group,) = mtk.LodUtils.generate([sphere])
        self.assertEqual(cmds.nodeType(group), "lodGroup")
        self.assertEqual(group.rsplit("|", 1)[-1], "ball")
        levels = cmds.listRelatives(group, children=True, fullPath=True)
        self.assertEqual([p.rsplit("|", 1)[-1] for p in levels], ["ball_LOD0", "ball_LOD1", "ball_LOD2"])
        counts = [_tris(p) for p in levels]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertLess(counts[1], counts[0] * 0.6)
        self.assertAlmostEqual(cmds.xform(levels[0], q=True, ws=True, t=True)[1], 2.0, places=4)
        self.assertEqual(cmds.getAttr(f"{group}.percentageThreshold")[0][:2], (50.0, 25.0))
        # What the exporter reads: one valid group.
        (read,) = mtk.LodUtils.groups([group])
        self.assertEqual(read["issues"], [])

    def test_a_non_manifold_mesh_is_cleaned_on_its_copies_and_the_selection_kept(self):
        """polyReduce refuses non-manifold geometry, so each level's copy goes
        through Maya's Mesh > Cleanup first; the source mesh and the user's
        selection stay as they were."""
        a = cmds.polySphere(name="a", sx=40, sy=40)[0]
        b = cmds.polySphere(name="b", sx=40, sy=40)[0]
        cmds.move(0, 2, 0, b)  # the two poles meet at one point
        pair = cmds.polyUnite(a, b, name="pair", constructionHistory=False)[0]
        cmds.polyMergeVertex(pair, distance=1e-4, constructionHistory=False)
        self.assertTrue(cmds.polyInfo(pair, nonManifoldVertices=True))
        before = _tris(pair)
        keep = cmds.polyCube(name="keep")[0]
        cmds.select(keep, replace=True)
        (group,) = mtk.LodUtils.generate([pair])
        levels = cmds.listRelatives(group, children=True, fullPath=True)
        self.assertEqual(_tris(levels[0]), before)  # LOD0 is the source, untouched
        self.assertLess(_tris(levels[1]), before * 0.6)
        self.assertEqual(cmds.ls(selection=True), ["keep"])

    def test_generation_is_one_undo_step(self):
        sphere = cmds.polySphere(name="ball", sx=40, sy=40)[0]
        cmds.undoInfo(state=True)
        cmds.flushUndo()
        mtk.LodUtils.generate([sphere])
        cmds.undo()
        self.assertTrue(cmds.objExists("|ball"))
        self.assertEqual(cmds.nodeType("|ball"), "transform")
        self.assertFalse(cmds.ls(type="lodGroup"))

    def test_a_light_mesh_and_a_level_are_skipped(self):
        cube = cmds.polyCube(name="box")[0]  # 12 triangles: below the floor
        level = cmds.polySphere(name="old_LOD0", sx=40, sy=40)[0]
        self.assertEqual(mtk.LodUtils.generate([cube, level]), [])

    def test_nothing_chosen_generates_nothing(self):
        """``cmds.ls`` of an empty list lists the whole scene: an empty
        selection, or ``objects=[]``, must group nothing (2026-11-10 review)."""
        cmds.polySphere(name="ball", sx=40, sy=40)
        cmds.select(clear=True)
        self.assertEqual(mtk.LodUtils.generate(), [])
        self.assertEqual(mtk.LodUtils.generate([]), [])
        self.assertIsNone(mtk.LodUtils.create_group([]))
        self.assertFalse(cmds.ls(type="lodGroup"))

    def test_a_parented_mesh_keeps_its_world_placement_and_its_own_transform(self):
        holder = cmds.createNode("transform", name="holder")
        cmds.xform(holder, translation=(5, 0, 0), rotation=(0, 45, 0), scale=(2, 2, 2))
        sphere = cmds.polySphere(name="ball", sx=40, sy=40)[0]
        sphere = cmds.parent(sphere, holder)[0]
        cmds.xform(sphere, translation=(1, 2, 3))
        world = cmds.xform(sphere, q=True, ws=True, matrix=True)
        local = cmds.xform(sphere, q=True, os=True, translation=True)
        (group,) = mtk.LodUtils.generate([sphere])
        self.assertEqual(cmds.listRelatives(group, parent=True, fullPath=True), ["|holder"])
        lod0 = f"{group}|ball_LOD0"
        for got, want in zip(cmds.xform(lod0, q=True, ws=True, matrix=True), world):
            self.assertAlmostEqual(got, want, places=4)
        self.assertEqual([round(v, 6) for v in cmds.xform(lod0, q=True, os=True, translation=True)], [round(v, 6) for v in local])

    def test_a_blend_shaped_mesh_is_skipped_and_said(self):
        """A level is a static copy: a deforming mesh needs authored LODs, in
        Create LODs as in the exporter (2026-11-10 review)."""
        base = cmds.polySphere(name="face", sx=40, sy=40)[0]
        target = cmds.polySphere(name="smile", sx=40, sy=40)[0]
        cmds.blendShape(target, base)
        self.assertEqual(mtk.LodUtils.ineligible(base), "skinned or with blend shapes")
        self.assertEqual(mtk.LodUtils.generate([base]), [])

    def test_namesakes_under_two_parents_each_get_their_group(self):
        """Addressed by uuid, not short name (2026-11-10 review), and each
        group's levels numbered from its own LOD0: ``duplicate -name`` makes a
        name unique scene-wide, so the second mesh's copies were numbered on
        past the first's (``knob_LOD3``, ``knob_LOD4``), a gapped chain every
        reader refuses (production scene, 2026-11-11)."""
        for holder in ("left", "right"):
            cmds.createNode("transform", name=holder)
            cmds.parent(cmds.polySphere(name="knob", sx=40, sy=40)[0], holder)
        made = mtk.LodUtils.generate(["|left|knob", "|right|knob"])
        self.assertEqual(sorted(made), ["|left|knob", "|right|knob"])
        for group in made:
            levels = [p.rsplit("|", 1)[-1] for p in cmds.listRelatives(group, children=True)]
            self.assertEqual(levels, ["knob_LOD0", "knob_LOD1", "knob_LOD2"], group)
        self.assertEqual([g["issues"] for g in mtk.LodUtils.groups()], [[], []])

    def test_a_level_name_a_non_dag_node_holds_is_refused_not_renumbered(self):
        """Non-DAG names are global: a shader named ``knob_LOD1`` made Maya
        rename the copy ``knob_LOD3``, a gapped chain that read as made
        (2026-11-11 review)."""
        cmds.shadingNode("lambert", asShader=True, name="knob_LOD1")
        knob = cmds.polySphere(name="knob", sx=40, sy=40)[0]
        with self.assertRaisesRegex(ValueError, "knob_LOD1 names another node"):
            mtk.LodUtils.generate([knob])
        self.assertFalse(cmds.ls("knob_LOD3"))

    def test_a_mesh_under_a_moving_parent_is_generated_and_its_levels_follow(self):
        """A level sits under the mesh's own parent, so it inherits whatever
        moves above; only the mesh's own drivers stay with LOD0 alone.
        Production rigs key the ``_LOC`` above each mesh: refusing an animated
        ancestor left 330 of an assembly's 331 meshes ungenerated (2026-11-11)."""
        rig = cmds.createNode("transform", name="arm_LOC")
        cmds.setKeyframe(rig, attribute="rotateY", time=1, value=0)
        cmds.setKeyframe(rig, attribute="rotateY", time=10, value=90)
        ball = cmds.parent(cmds.polySphere(name="ball", sx=40, sy=40)[0], rig)[0]
        cmds.xform(ball, translation=(2, 0, 0))
        self.assertIsNone(mtk.LodUtils.ineligible(ball))
        (group,) = mtk.LodUtils.generate([ball])
        cmds.currentTime(10)
        lod0, lod1 = cmds.listRelatives(group, children=True, fullPath=True)[:2]
        for got, want in zip(cmds.xform(lod1, q=True, ws=True, matrix=True), cmds.xform(lod0, q=True, ws=True, matrix=True)):
            self.assertAlmostEqual(got, want, places=4)
        spinner = cmds.polySphere(name="spinner", sx=40, sy=40)[0]
        cmds.setKeyframe(spinner, attribute="rotateY", time=1, value=0)
        self.assertEqual(mtk.LodUtils.ineligible(spinner), "animated")

    def test_an_empty_list_reads_no_group(self):
        """``cmds.ls([])`` lists the whole scene: nothing chosen reads nothing."""
        mtk.LodUtils.generate([cmds.polySphere(name="ball", sx=40, sy=40)[0]])
        self.assertEqual(mtk.LodUtils.groups([]), [])
        self.assertEqual(len(mtk.LodUtils.groups()), 1)

    def test_a_mesh_holding_other_transforms_is_skipped(self):
        """Its children would belong to LOD0 alone, and each copy carry them."""
        plate = cmds.polySphere(name="plate", sx=40, sy=40)[0]
        bolt = cmds.polySphere(name="bolt", sx=40, sy=40)[0]
        cmds.parent(bolt, plate)
        self.assertEqual(mtk.LodUtils.generate([plate]), [])
        (group,) = mtk.LodUtils.generate(["|plate|bolt"])
        self.assertEqual(group, "|plate|bolt")


class TestCreateAndConform(MayaTkTestCase):
    def test_meshes_in_order_become_the_levels_of_a_group(self):
        hi = cmds.polySphere(name="seat", sx=30, sy=30)[0]
        lo = cmds.polySphere(name="seatLow", sx=8, sy=8)[0]
        group = mtk.LodUtils.create_group([hi, lo], name="seat")
        self.assertEqual(
            [p.rsplit("|", 1)[-1] for p in cmds.listRelatives(group, children=True)],
            ["seat_LOD0", "seat_LOD1"],
        )
        self.assertEqual(cmds.getAttr(f"{group}.percentageThreshold")[0][0], 50.0)

    def test_the_group_takes_the_base_its_first_level_held(self):
        """``seat`` becomes ``seat_LOD0`` first, so the group is ``seat``, not
        ``seat1`` (2026-11-10 review)."""
        hi = cmds.polySphere(name="seat", sx=30, sy=30)[0]
        lo = cmds.polySphere(name="seatLow", sx=8, sy=8)[0]
        group = mtk.LodUtils.create_group([hi, lo])
        self.assertEqual(group, "|seat")

    def test_a_level_away_from_lod0_is_noted_not_blocked(self):
        """Each level's world box is read: one that moved pops at its switch."""
        hi = cmds.polySphere(name="seat", sx=30, sy=30)[0]
        lo = cmds.polySphere(name="seatLow", sx=8, sy=8)[0]
        group = mtk.LodUtils.create_group([hi, lo])
        (read,) = mtk.LodUtils.groups([group])
        self.assertEqual((read["issues"], read["notes"]), ([], []))
        cmds.move(5, 0, 0, f"{group}|seat_LOD1", relative=True)
        (read,) = mtk.LodUtils.groups([group])
        self.assertEqual(read["issues"], [])
        self.assertIn("LOD1", " ".join(read["notes"]))

    def test_maya_generate_lod_meshes_containers_conform_to_the_convention(self):
        """Maya's own generator: ``LOD_Group_1|LOD_0|wheel``, ``LOD_1|wheel``."""
        group = cmds.createNode("lodGroup", name="LOD_Group_1")
        for index, segments in enumerate((24, 8)):
            container = cmds.createNode("transform", name=f"LOD_{index}", parent=group)
            wheel = cmds.polyTorus(name="wheel", sx=segments, sy=segments)[0]
            cmds.parent(wheel, container)
        (before,) = mtk.LodUtils.groups([group])
        self.assertEqual(before["form"], "containers")
        mtk.LodUtils.conform([group])
        self.assertEqual(
            sorted(c.rsplit("|", 1)[-1] for c in cmds.listRelatives(group, children=True)),
            ["wheel_LOD0", "wheel_LOD1"],
        )
        (after,) = mtk.LodUtils.groups([group])
        self.assertEqual((after["form"], after["issues"], after["notes"]), ("names", [], []))


if __name__ == "__main__":
    unittest.main(exit=False)
