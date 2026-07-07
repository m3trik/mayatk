#!/usr/bin/env python
# coding=utf-8
"""AutoInstancer acceptance against the user's ground-truth scenes.

Two hand-authored reference scenes define the contract:

* ``example_of_a_split_assembly.ma`` — ``expected_sorting`` (the part→
  assembly partition) and ``final_expected_result`` (the finished scene:
  5 canisters sharing ONE shape, 3 uniformly-scaled cases sharing ONE
  shape, and every leftover merged into a single ``other`` mesh).
* ``example_of_a_split_assembly_alt.ma`` — ``sorted_mesh_expected_assemblies``
  (102 shells hand-sorted into assembly types A–H + leftovers).

Sorting quality is measured MECHANICALLY as pair precision/recall over the
part partition (never by eye). Both scenes' residual recall misses are
data-honest (documented in the assertions).
"""
import os
import sys
import unittest
from collections import defaultdict

scripts_dir = r"O:\Cloud\Code\_scripts"
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)
test_dir = os.path.dirname(os.path.abspath(__file__))
parent_test_dir = os.path.dirname(test_dir)
if parent_test_dir not in sys.path:
    sys.path.insert(0, parent_test_dir)

import numpy as np  # noqa: E402
import maya.cmds as cmds  # noqa: E402
import maya.api.OpenMaya as om  # noqa: E402

from base_test import MayaTkTestCase  # noqa: E402

_SCENE_DIR = (
    r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Ryan Simpson"
    r"\_tests\instance_separator"
)
SCENE_MAIN = os.path.join(_SCENE_DIR, "example_of_a_split_assembly.ma")
SCENE_ALT = os.path.join(_SCENE_DIR, "example_of_a_split_assembly_alt.ma")


def _mesh_transforms_under(grp):
    kids = (
        cmds.listRelatives(grp, children=True, type="transform", fullPath=True) or []
    )
    return [
        k
        for k in kids
        if (cmds.listRelatives(k, shapes=True, noIntermediate=True) or [])
    ]


def _part_key(node):
    nv = cmds.polyEvaluate(node, vertex=True)
    nf = cmds.polyEvaluate(node, face=True)
    bb = cmds.exactWorldBoundingBox(node)
    c = np.array([(bb[0] + bb[3]) / 2, (bb[1] + bb[4]) / 2, (bb[2] + bb[5]) / 2])
    return (nv, nf), c


def _world_points(transform):
    shapes = cmds.listRelatives(transform, shapes=True, ni=True, fullPath=True) or []
    sel = om.MSelectionList()
    sel.add(shapes[0])
    fn = om.MFnMesh(sel.getDagPath(0))
    return np.array([[p.x, p.y, p.z] for p in fn.getPoints(om.MSpace.kWorld)])


def _partition_scores(expected_parts, produced_groups):
    """Pair precision/recall of the produced partition vs the expected one.

    ``expected_parts``: list of (topo, centroid, name, group_label).
    ``produced_groups``: list of (group_name, [mesh transforms]).
    Parts map to expected entries by topology + nearest centroid (the
    reference copies sit at identical world coordinates).
    """

    def find_expected(topo, c):
        best, best_d = None, None
        for i, (etopo, ec, _en, _eg) in enumerate(expected_parts):
            if etopo != topo:
                continue
            d = float(np.linalg.norm(ec - c))
            if best_d is None or d < best_d:
                best, best_d = i, d
        return best, best_d

    assigned = {}
    for gname, parts in produced_groups:
        for p in parts:
            topo, c = _part_key(p)
            i, d = find_expected(topo, c)
            if i is not None and d <= 0.5:
                assigned[p] = (gname, expected_parts[i][3], expected_parts[i][2])

    def pairs(partition):
        s = set()
        for members in partition.values():
            ms = sorted(members)
            for i in range(len(ms)):
                for j in range(i + 1, len(ms)):
                    s.add((ms[i], ms[j]))
        return s

    exp_p, prod_p = defaultdict(list), defaultdict(list)
    for p, (gname, eg, en) in assigned.items():
        exp_p[eg].append(en)
        prod_p[gname].append(en)
    P, E = pairs(prod_p), pairs(exp_p)
    tp = len(P & E)
    precision = tp / len(P) if P else 1.0
    recall = tp / len(E) if E else 1.0
    return precision, recall


def _run_sorting(input_node):
    """Separate + reassemble only; returns [(group_name, [members])]."""
    from mayatk.core_utils.auto_instancer.geometry_matcher import GeometryMatcher
    from mayatk.core_utils.auto_instancer.assembly_reconstructor import (
        AssemblyReconstructor,
        ASSEMBLY_TAG_ATTR,
    )

    matcher = GeometryMatcher(
        tolerance=0.001, require_same_material=True, verbose=False
    )
    recon = AssemblyReconstructor(matcher=matcher, verbose=False)
    shells = recon.separate_combined_meshes([input_node])
    result = recon.reassemble_assemblies(shells)

    groups = []
    for node in result:
        node = str(node)
        if not cmds.objExists(node):
            continue
        try:
            tagged = cmds.attributeQuery(ASSEMBLY_TAG_ATTR, node=node, exists=True)
        except Exception:
            tagged = False
        if tagged:
            groups.append((node, _mesh_transforms_under(node)))
        elif cmds.listRelatives(node, shapes=True, noIntermediate=True) or []:
            groups.append((f"single:{node}", [node]))
    return groups


@unittest.skipUnless(os.path.exists(SCENE_MAIN), "ground-truth scene unavailable")
class TestScene1GroundTruth(MayaTkTestCase):
    """example_of_a_split_assembly.ma — sorting AND the finished result."""

    def test_sorting_matches_expected_sorting_exactly(self):
        cmds.file(SCENE_MAIN, open=True, force=True)
        expected = []
        for grp in cmds.listRelatives(
            "expected_sorting", children=True, type="transform", fullPath=True
        ):
            gname = grp.split("|")[-1]
            for part in _mesh_transforms_under(grp):
                topo, c = _part_key(part)
                label = (
                    f"OTHER/{part.split('|')[-1]}" if gname == "other" else gname
                )
                expected.append((topo, c, part.split("|")[-1], label))

        produced = _run_sorting("|original_combined_mesh")
        precision, recall = _partition_scores(expected, produced)
        self.assertEqual(precision, 1.0, "no part may land in a wrong assembly")
        self.assertEqual(recall, 1.0, "every expected assembly must reconstruct")

    def test_full_default_run_matches_final_expected_result(self):
        cmds.file(SCENE_MAIN, open=True, force=True)

        before = _world_points("|original_combined_mesh")
        tree = None
        try:
            from scipy.spatial import KDTree

            tree = KDTree(before)
        except ImportError:
            pass

        import mayatk as mtk

        mtk.auto_instance(["|original_combined_mesh"], separate_combined=True)

        produced = []
        for t in cmds.ls(type="transform", long=True):
            if t.startswith("|final_expected_result") or t.startswith(
                "|expected_sorting"
            ):
                continue
            shapes = cmds.listRelatives(t, shapes=True, ni=True, fullPath=True) or []
            if shapes and cmds.objectType(shapes[0]) == "mesh":
                produced.append((t, shapes[0]))

        # Shape sharing mirrors the reference: ONE canister shape x5 paths,
        # ONE case shape x3 (scaled instances), `other` standalone.
        # Key by the shape NODE (short name) — instances share the node but
        # every DAG path to it is distinct.
        by_shape = defaultdict(list)
        for t, s in produced:
            by_shape[s.split("|")[-1]].append(t)
        path_counts = sorted(len(v) for v in by_shape.values())
        self.assertEqual(
            path_counts, [1, 3, 5], f"shape sharing wrong: {dict(by_shape)}"
        )

        # Every expected final mesh exists at its position with its topology.
        for c in (
            cmds.listRelatives(
                "final_expected_result", children=True, fullPath=True
            )
            or []
        ):
            topo, center = _part_key(c)
            hit = False
            for t, _s in produced:
                ptopo, pcenter = _part_key(t)
                if ptopo == topo and float(np.linalg.norm(pcenter - center)) <= 0.5:
                    hit = True
                    break
            self.assertTrue(hit, f"no produced mesh matches {c}")

        # World geometry preserved: every produced point exists in the input.
        if tree is not None:
            worst = 0.0
            for t, _s in produced:
                d, _ = tree.query(_world_points(t), k=1)
                worst = max(worst, float(d.max()))
            self.assertLess(worst, 0.01, "output must preserve input geometry")


@unittest.skipUnless(os.path.exists(SCENE_ALT), "ground-truth scene unavailable")
class TestAltSceneGroundTruth(MayaTkTestCase):
    """example_of_a_split_assembly_alt.ma — sorting partition quality."""

    def test_sorting_precision_and_recall(self):
        cmds.file(SCENE_ALT, open=True, force=True)
        expected = []
        for tnode in (
            cmds.listRelatives(
                "sorted_mesh_expected_assemblies",
                children=True,
                type="transform",
                fullPath=True,
            )
            or []
        ):
            tname = tnode.split("|")[-1]
            if tname == "leftovers":
                for part in _mesh_transforms_under(tnode):
                    topo, c = _part_key(part)
                    expected.append(
                        (topo, c, part.split("|")[-1], f"leftover/{part.split('|')[-1]}")
                    )
                continue
            for grp in (
                cmds.listRelatives(
                    tnode, children=True, type="transform", fullPath=True
                )
                or []
            ):
                glabel = f"{tname}/{grp.split('|')[-1]}"
                for part in _mesh_transforms_under(grp):
                    topo, c = _part_key(part)
                    expected.append((topo, c, part.split("|")[-1], glabel))

        produced = _run_sorting("|original_combined_mesh")
        precision, recall = _partition_scores(expected, produced)
        self.assertEqual(precision, 1.0, "no part may land in a wrong assembly")
        # Data-honest residuals: the H-type copies carry different SG
        # assignments in the source, so pairing them is correctly refused.
        self.assertGreaterEqual(recall, 0.97, "expected assemblies missing")


if __name__ == "__main__":
    unittest.main()
