# !/usr/bin/python
# coding=utf-8
"""Test suite for EditUtils.separate_objects.

Covers the two failure modes that motivated the rewrite:

1. A connected mesh with multiple materials must split per-material when
   ``by_material=True`` (the legacy implementation short-circuited on the
   first ``polySeparate`` call, which only splits disjoint shells).

2. A mesh with disjoint shells where each shell carries multiple materials
   must split into one transform *per material per shell*, not per shell.

Also covers ``group_by_material=True`` regrouping (the mirror of
``combine_objects(group_by_material=True)``) and a combine→separate
round-trip.
"""
import re
import unittest
from typing import List

import mayatk as mtk
from mayatk.edit_utils._edit_utils import EditUtils
from mayatk.mat_utils._mat_utils import MatUtils

from base_test import MayaTkTestCase
import maya.cmds as cmds


def _short(node: str) -> str:
    return str(node).split("|")[-1]


def _mats_on(node: str) -> List[str]:
    return MatUtils.get_mats(node, as_strings=True) or []


class TestSeparateObjects(MayaTkTestCase):
    """Exercises EditUtils.separate_objects across the supported modes."""

    def setUp(self):
        super().setUp()
        self.mat1 = mtk.MatUtils.create_mat("lambert", name="mat_red")
        self.mat2 = mtk.MatUtils.create_mat("lambert", name="mat_green")
        self.mat3 = mtk.MatUtils.create_mat("lambert", name="mat_blue")

    # ---- Standard (disjoint shells) -----------------------------------------

    def test_separate_disjoint_shells(self):
        """Default mode splits a polyUnite of N cubes into N transforms."""
        c1 = cmds.polyCube()[0]
        c2 = cmds.polyCube()[0]
        cmds.move(5, 0, 0, c2)
        combined = cmds.polyUnite(c1, c2, ch=False)[0]

        res = EditUtils.separate_objects([combined], by_material=False)
        self.assertEqual(len(res), 2)
        for r in res:
            self.assertTrue(cmds.objExists(r))

    def test_separate_single_shell_is_noop(self):
        """A single-shell mesh comes back unchanged when by_material is False."""
        c = cmds.polyCube(n="solo")[0]
        res = EditUtils.separate_objects([c], by_material=False)
        self.assertEqual(len(res), 1)
        self.assertTrue(cmds.objExists(res[0]))

    # ---- by_material on connected meshes ------------------------------------

    def test_separate_by_material_splits_connected_mesh(self):
        """Connected mesh with 2 materials → 2 transforms, one material each."""
        c = cmds.polyCube(sx=2, n="biMat")[0]
        mtk.MatUtils.assign_mat(c, self.mat1)
        cmds.select(f"{c}.f[0:3]")
        mtk.MatUtils.assign_mat(cmds.ls(selection=True), self.mat2)

        res = EditUtils.separate_objects([c], by_material=True)
        self.assertEqual(len(res), 2, "Expected one transform per material")

        per_mat_counts = {self.mat1: 0, self.mat2: 0}
        for r in res:
            mats = _mats_on(r)
            self.assertEqual(
                len(mats), 1,
                f"Result {r} should have exactly one material, got {mats}",
            )
            per_mat_counts[mats[0]] += 1
        self.assertEqual(per_mat_counts[self.mat1], 1)
        self.assertEqual(per_mat_counts[self.mat2], 1)

    def test_separate_by_material_disjoint_shells_with_multiple_mats(self):
        """Bug case: disjoint shells each with 2 mats → 4 single-material results.

        The legacy implementation hit ``polySeparate`` first and returned 2
        transforms (one per shell), each still carrying both materials.
        """
        # Build two disjoint cubes, each face-painted with two materials.
        c1 = cmds.polyCube(sx=2, n="left")[0]
        mtk.MatUtils.assign_mat(c1, self.mat1)
        cmds.select(f"{c1}.f[0:3]")
        mtk.MatUtils.assign_mat(cmds.ls(selection=True), self.mat2)

        c2 = cmds.polyCube(sx=2, n="right")[0]
        cmds.move(10, 0, 0, c2)
        mtk.MatUtils.assign_mat(c2, self.mat1)
        cmds.select(f"{c2}.f[0:3]")
        mtk.MatUtils.assign_mat(cmds.ls(selection=True), self.mat3)

        combined = cmds.polyUnite(c1, c2, n="dual", ch=False)[0]

        res = EditUtils.separate_objects([combined], by_material=True)

        self.assertEqual(
            len(res), 4,
            f"Expected 4 single-material transforms, got {len(res)}: {res}",
        )
        for r in res:
            mats = _mats_on(r)
            self.assertEqual(
                len(mats), 1,
                f"Result {r} should have exactly one material, got {mats}",
            )

    # ---- group_by_material (mirror of combine) ------------------------------

    def test_separate_group_by_material_creates_groups(self):
        """``group_by_material=True`` parents results under per-material groups
        named after the source object."""
        c = cmds.polyCube(sx=2, n="triMat")[0]
        mtk.MatUtils.assign_mat(c, self.mat1)
        cmds.select(f"{c}.f[0:1]")
        mtk.MatUtils.assign_mat(cmds.ls(selection=True), self.mat2)
        cmds.select(f"{c}.f[2:3]")
        mtk.MatUtils.assign_mat(cmds.ls(selection=True), self.mat3)

        groups = EditUtils.separate_objects(
            [c], by_material=True, group_by_material=True
        )

        self.assertEqual(len(groups), 3)
        for grp in groups:
            self.assertTrue(cmds.objExists(grp))
            self.assertEqual(cmds.nodeType(grp), "transform")
            children = cmds.listRelatives(grp, children=True) or []
            self.assertGreaterEqual(len(children), 1)
            for child in children:
                mats = _mats_on(child)
                self.assertEqual(len(mats), 1)

        # Group names must be derived from the source object name, not the
        # material name, with a single-letter disambiguator + _grp suffix.
        group_names = sorted(_short(g) for g in groups)
        self.assertEqual(
            group_names,
            ["triMat_A_grp", "triMat_B_grp", "triMat_C_grp"],
        )

        # Single-child groups should rename their child to ``source_<suffix>``.
        for grp in groups:
            children = cmds.listRelatives(grp, children=True) or []
            if len(children) == 1:
                short = _short(children[0])
                self.assertTrue(
                    short.startswith("triMat_") and not short.endswith("_grp"),
                    f"Expected child named after source object, got {short!r}",
                )

    def test_group_by_material_alone_groups_existing_objects(self):
        """``group_by_material=True`` without ``by_material`` still groups by mat."""
        a = cmds.polyCube(n="a")[0]
        b = cmds.polyCube(n="b")[0]
        cmds.move(5, 0, 0, b)
        d = cmds.polyCube(n="d")[0]
        cmds.move(10, 0, 0, d)

        mtk.MatUtils.assign_mat(a, self.mat1)
        mtk.MatUtils.assign_mat(b, self.mat1)
        mtk.MatUtils.assign_mat(d, self.mat2)

        groups = EditUtils.separate_objects(
            [a, b, d], by_material=False, group_by_material=True
        )

        # Three sources, but ``a`` and ``b`` share a material with their own
        # source so each source produces only one group.
        self.assertEqual(len(groups), 3)
        for grp in groups:
            children = cmds.listRelatives(grp, children=True) or []
            self.assertEqual(len(children), 1)
            mats = _mats_on(children[0])
            self.assertEqual(len(mats), 1)

        # Groups should derive their name from each source.
        group_names = sorted(_short(g) for g in groups)
        self.assertEqual(group_names, ["a_A_grp", "b_A_grp", "d_A_grp"])

    def test_group_by_material_multi_member_uses_inner_lowercase_suffix(self):
        """A bucket with multiple members names children ``source_<X>_<a/b>``."""
        # Build 4 disjoint cubes so the combined source has 4 shells:
        # three painted with mat1 and one with mat2.
        cubes = []
        for i in range(4):
            c = cmds.polyCube(n=f"piece{i}")[0]
            cmds.move(i * 5, 0, 0, c)
            cubes.append(c)
        combined = cmds.polyUnite(*cubes, n="src", ch=False)[0]
        # Rename in case Maya disambiguated "src" → "src1".
        combined = cmds.rename(combined, "src")
        # 6 faces per cube, 4 cubes → faces 0-17 are mat1, 18-23 are mat2.
        cmds.select(f"{combined}.f[0:17]")
        mtk.MatUtils.assign_mat(cmds.ls(selection=True), self.mat1)
        cmds.select(f"{combined}.f[18:23]")
        mtk.MatUtils.assign_mat(cmds.ls(selection=True), self.mat2)

        groups = EditUtils.separate_objects(
            [combined], by_material=True, group_by_material=True
        )
        self.assertEqual(len(groups), 2)

        children_per_group = {
            _short(g): sorted(
                _short(c) for c in (cmds.listRelatives(g, children=True) or [])
            )
            for g in groups
        }
        # Big bucket (mat1, 3 members) uses lowercase inner suffix; small
        # bucket (mat2, 1 member) collapses to ``source_<X>`` with no inner.
        big = next(c for c in children_per_group.values() if len(c) == 3)
        small = next(c for c in children_per_group.values() if len(c) == 1)
        self.assertTrue(
            all(re.fullmatch(r"src_[AB]_[abc]", n) for n in big),
            f"Expected source_<X>_<a-c> names, got {big}",
        )
        self.assertRegex(small[0], r"^src_[AB]$")

    def test_group_by_material_inner_suffix_falls_back_to_numeric(self):
        """A single bucket with >26 members switches inner suffix to numerics."""
        # 28 disjoint cubes, all sharing the same material → one bucket of 28.
        cubes = []
        for i in range(28):
            c = cmds.polyCube(n=f"slab{i}")[0]
            cmds.move(i * 3, 0, 0, c)
            cubes.append(c)
        combined = cmds.polyUnite(*cubes, n="slab", ch=False)[0]
        combined = cmds.rename(combined, "slab")
        mtk.MatUtils.assign_mat(combined, self.mat1)

        groups = EditUtils.separate_objects(
            [combined], by_material=True, group_by_material=True
        )
        self.assertEqual(len(groups), 1)
        children = sorted(
            _short(c) for c in (cmds.listRelatives(groups[0], children=True) or [])
        )
        self.assertEqual(len(children), 28)
        # Group still gets a single 'A' letter (only one bucket); inner suffix
        # is numeric because the bucket has >26 members.
        for name in children:
            self.assertRegex(name, r"^slab_A_\d{2}$")

    def test_group_by_material_falls_back_to_numeric_above_26(self):
        """When the bucket count exceeds 26, the suffix scheme switches to
        zero-padded numerics."""
        # Build 27 cubes, each with its own unique material.
        cubes = []
        mats = []
        for i in range(27):
            c = cmds.polyCube(n=f"piece{i}")[0]
            cmds.move(i * 3, 0, 0, c)
            m = mtk.MatUtils.create_mat("lambert", name=f"matX{i}")
            mtk.MatUtils.assign_mat(c, m)
            cubes.append(c)
            mats.append(m)
        combined = cmds.polyUnite(*cubes, n="big", ch=False)[0]

        groups = EditUtils.separate_objects(
            [combined], by_material=True, group_by_material=True
        )
        self.assertEqual(len(groups), 27)
        names = sorted(_short(g) for g in groups)
        # All names should match the numeric pattern when count > 26.
        for n in names:
            self.assertRegex(n, r"^big_\d{2}_grp$")

    # ---- Round-trip with combine_objects ------------------------------------

    def test_combine_then_separate_round_trip(self):
        """combine(group_by_material) → separate(by_material, group_by_material).

        Material assignments and topology counts should round-trip.
        """
        cubes = []
        mats = [self.mat1, self.mat2, self.mat3]
        for i, mat in enumerate(mats):
            for j in range(2):  # two cubes per material
                cube = cmds.polyCube(n=f"src_{i}_{j}")[0]
                cmds.move(i * 5, 0, j * 5, cube)
                mtk.MatUtils.assign_mat(cube, mat)
                cubes.append(cube)

        before_mats = sorted({m for c in cubes for m in _mats_on(c)})
        before_face_total = sum(
            cmds.polyEvaluate(c, face=True) or 0 for c in cubes
        )

        combined = EditUtils.combine_objects(cubes, group_by_material=True)
        self.assertEqual(len(combined), 3)

        groups = EditUtils.separate_objects(
            combined, by_material=True, group_by_material=True
        )
        self.assertEqual(len(groups), 3)

        leaves: List[str] = []
        for grp in groups:
            leaves.extend(cmds.listRelatives(grp, allDescendents=True, type="transform") or [])

        after_mats = sorted({m for leaf in leaves for m in _mats_on(leaf)})
        after_face_total = sum(
            cmds.polyEvaluate(leaf, face=True) or 0 for leaf in leaves
        )

        self.assertEqual(before_mats, after_mats)
        self.assertEqual(before_face_total, after_face_total)

    # ---- Pivot / rename behavior --------------------------------------------

    def test_separate_centers_pivots_by_default(self):
        """Each result's pivot should sit at its bounding-box center."""
        c1 = cmds.polyCube()[0]
        c2 = cmds.polyCube()[0]
        cmds.move(7, 0, 0, c2)
        combined = cmds.polyUnite(c1, c2, ch=False)[0]

        res = EditUtils.separate_objects([combined])
        for r in res:
            bb = cmds.exactWorldBoundingBox(r)
            center = ((bb[0] + bb[3]) / 2.0, (bb[1] + bb[4]) / 2.0, (bb[2] + bb[5]) / 2.0)
            pivot = cmds.xform(r, q=True, ws=True, rp=True)
            for c, p in zip(center, pivot):
                self.assertAlmostEqual(c, p, places=4)

    def test_separate_with_rename_uses_original_name(self):
        """``rename=True`` produces names derived from the original transform."""
        c1 = cmds.polyCube()[0]
        c2 = cmds.polyCube()[0]
        cmds.move(8, 0, 0, c2)
        combined = cmds.polyUnite(c1, c2, n="MyComp", ch=False)[0]

        res = EditUtils.separate_objects([combined], rename=True)
        self.assertEqual(len(res), 2)
        for r in res:
            self.assertTrue(_short(r).startswith("MyComp"))


if __name__ == "__main__":
    unittest.main()
