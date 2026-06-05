# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.nurbs_utils.curve_to_tube (CurveToTube engine).

Covers both output types and the invariants the preview relies on:
- exact topology (sides around, path divisions along),
- outward normals and watertight caps / closed loops,
- clean single-undo of a committed tube (the API-orphan regression),
- input validation.
"""
import types
import unittest
import importlib
import math

import maya.cmds as cmds
import maya.api.OpenMaya as om

try:
    from qtpy import QtWidgets
except ImportError:
    QtWidgets = None

from base_test import MayaTkTestCase
import mayatk.nurbs_utils.curve_to_tube as ctt_mod

importlib.reload(ctt_mod)
from mayatk.nurbs_utils.curve_to_tube import CurveToTube, CurveToTubeSlots
from mayatk.core_utils.preview import OperationError


def _shape_type(transform):
    shapes = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
    return cmds.objectType(shapes[0]) if shapes else None


def _signed_volume(mesh):
    """Enclosed volume of a closed mesh (divergence theorem); >0 iff the face
    normals point outward. Axis-independent, so it verifies the torus too."""
    sel = om.MSelectionList()
    sel.add(mesh)
    dag = sel.getDagPath(0)
    dag.extendToShape()
    fn = om.MFnMesh(dag)
    pts = fn.getPoints(om.MSpace.kWorld)
    vol = 0.0
    for i in range(fn.numPolygons):
        vids = fn.getPolygonVertices(i)
        v0 = om.MVector(pts[vids[0]])
        for k in range(1, len(vids) - 1):  # fan-triangulate
            a = om.MVector(pts[vids[k]])
            b = om.MVector(pts[vids[k + 1]])
            vol += v0 * (a ^ b)  # scalar triple v0 . (a x b)
    return vol / 6.0


def _nurbs_outward(surf):
    """A mid-path ring's surface normal vs the radial direction (outward?)."""
    center = om.MVector()
    n = 12
    for k in range(n):
        center += om.MVector(*cmds.pointOnSurface(surf, u=k / n, v=0.5, turnOnPercentage=True, position=True))
    center /= n
    p = om.MVector(*cmds.pointOnSurface(surf, u=0.0, v=0.5, turnOnPercentage=True, position=True))
    nrm = om.MVector(*cmds.pointOnSurface(surf, u=0.0, v=0.5, turnOnPercentage=True, normal=True))
    return (nrm * (p - center)) > 0


def _tube_outward_fraction(mesh, curve):
    """Fraction of `mesh` face normals pointing away from `curve`'s centerline.

    General for a bent tube (no fixed axis): each face center is compared to the
    nearest of a set of sampled curve points. 1.0 == fully outward, ~0 == fully
    inverted. Mirrors `CurveToTube._conform_poly_outward`'s own check.
    """
    shp = cmds.listRelatives(curve, shapes=True)[0]
    mn, mx = cmds.getAttr(f"{shp}.minValue"), cmds.getAttr(f"{shp}.maxValue")
    cl = [
        om.MVector(*cmds.pointOnCurve(shp, pr=mn + (mx - mn) * i / 23.0, position=True))
        for i in range(24)
    ]
    sel = om.MSelectionList()
    sel.add(mesh)
    dag = sel.getDagPath(0)
    dag.extendToShape()
    fn = om.MFnMesh(dag)
    pts = fn.getPoints(om.MSpace.kWorld)
    out = total = 0
    for i in range(fn.numPolygons):
        verts = fn.getPolygonVertices(i)
        fc = om.MVector(0, 0, 0)
        for vi in verts:
            fc += om.MVector(pts[vi])
        fc /= len(verts)
        radial = fc - min(cl, key=lambda c: (c - fc).length())
        if radial.length() < 1e-9:
            continue
        total += 1
        if fn.getPolygonNormal(i, om.MSpace.kWorld) * radial > 0:
            out += 1
    return out / max(total, 1)


def _side_faces_outward(mesh, axis=om.MVector(1, 0, 0)):
    """For a tube whose axis passes through the origin along `axis`, count
    side faces whose normal points inward (should be zero)."""
    sel = om.MSelectionList()
    sel.add(mesh)
    dag = sel.getDagPath(0)
    dag.extendToShape()
    fn = om.MFnMesh(dag)
    pts = fn.getPoints(om.MSpace.kWorld)
    inward = 0
    for i in range(fn.numPolygons):
        n = fn.getPolygonNormal(i, om.MSpace.kWorld)
        if abs(n * axis) > 0.7:  # cap face -> skip
            continue
        verts = fn.getPolygonVertices(i)
        fc = om.MVector(0, 0, 0)
        for vi in verts:
            fc += om.MVector(pts[vi])
        fc /= len(verts)
        radial = fc - axis * (fc * axis)  # strip the axial component
        if n * radial <= 0:
            inward += 1
    return inward


class TestCurveToTube(MayaTkTestCase):
    """Tests for the CurveToTube engine."""

    def setUp(self):
        super().setUp()
        # Degree-1 path with two spans.
        self.path = cmds.curve(
            d=1, p=[(0, 0, 0), (5, 2, 0), (10, 0, 0)], name="ctt_path"
        )

    # --------------------------------------------------------------- NURBS
    def test_nurbs_tube_is_nurbs_surface(self):
        tube = CurveToTube.create(self.path, output_type="nurbs", sections=8)[0]
        self.assertNodeExists(tube)
        self.assertEqual(_shape_type(tube), "nurbsSurface")

    def test_nurbs_tube_cleans_profile_circle(self):
        CurveToTube.create(self.path, output_type="nurbs", sections=8)
        self.assertEqual(cmds.ls(type="makeNurbCircle") or [], [])

    def test_nurbs_normals_outward_either_curve_direction(self):
        # Extrude orientation depends on how the curve was drawn; the tube must
        # face outward whichever way the path runs (regression: it was inverted).
        fwd = [(0, 0, 0), (3, 3, 0), (6, -3, 0), (9, 3, 0), (12, 0, 0)]
        for direction in (fwd, list(reversed(fwd))):
            cmds.file(new=True, force=True)
            crv = cmds.curve(d=3, p=direction)
            tube = CurveToTube.create(crv, output_type="nurbs", sections=8)[0]
            self.assertTrue(_nurbs_outward(tube), f"inward for {direction[0]}..")

    # ------------------------------------------------------------- polygon
    def test_polygon_sides_exact_and_straight_is_minimal(self):
        # `Sections` is the exact face count around; a straight curve gets only
        # a minimal handful of rings along the path (optimize thins straight
        # runs), not the dense uniform spread the old approach produced.
        straight = cmds.curve(d=1, p=[(0, 0, 0), (5, 0, 0), (10, 0, 0)], name="ctt_x")
        tube = CurveToTube.create(
            straight, output_type="polygon", sections=10, path_divisions=1, caps=False
        )[0]
        # Open uncapped tube: verts - faces == sides (exact around count).
        around = cmds.polyEvaluate(tube, vertex=True) - cmds.polyEvaluate(tube, face=True)
        self.assertEqual(around, 10)
        rings = cmds.polyEvaluate(tube, vertex=True) // 10
        # Minimal: 2 endpoint rings + a fixed end-tangent anchor ring per end.
        self.assertLessEqual(rings, 4)

    def test_polygon_rings_focus_on_bends(self):
        # RDP spends rings where the curve bends: a bendy path gets many more
        # rings than a straight one between the same endpoints.
        def rings_for_pts(pts, d, pd=2):
            cmds.file(new=True, force=True)
            c = cmds.curve(d=d, p=pts)
            t = CurveToTube.create(
                c, output_type="polygon", sections=8, path_divisions=pd, caps=False
            )[0]
            return cmds.polyEvaluate(t, vertex=True) // 8

        straight = rings_for_pts([(0, 0, 0), (6, 0, 0), (12, 0, 0)], 1)
        bendy = rings_for_pts([(0, 0, 0), (3, 4, 0), (6, -4, 0), (9, 4, 0), (12, 0, 0)], 3)
        self.assertLessEqual(straight, 4)  # straight run stays minimal (+ end anchors)
        self.assertGreater(bendy, straight * 2)  # bends draw the rings

        # Higher path_divisions -> finer (monotonic) along a curved path.
        curved = [(0, 0, 0), (3, 4, 0), (6, -4, 0), (9, 0, 0)]
        self.assertGreater(rings_for_pts(curved, 3, pd=4), rings_for_pts(curved, 3, pd=1))

    def test_polygon_rings_concentrate_at_tight_bends(self):
        """Known-region check: ring density must be higher on a TIGHT
        (high-curvature) bend than a GENTLE (low-curvature) one. The curve is a
        narrow Gaussian bump (x~6-10) and a wide one (x~22-34), both smooth and
        monotonic in x so vertices bin cleanly by x."""
        xs = [i * 0.5 for i in range(81)]  # x 0..40

        def y(x):  # narrow bump (high curvature) + wide bump (low curvature)
            return 2.5 * math.exp(-((x - 8) / 0.8) ** 2) + 2.5 * math.exp(
                -((x - 28) / 4.0) ** 2
            )

        crv = cmds.curve(d=3, p=[(x, y(x), 0) for x in xs])
        tube = CurveToTube.create(
            crv, output_type="polygon", sections=8, radius=0.15, path_divisions=1, caps=False
        )[0]
        shp = cmds.listRelatives(crv, s=True)[0]
        mn, mx = cmds.getAttr(shp + ".minValue"), cmds.getAttr(shp + ".maxValue")
        samp = [
            cmds.pointOnCurve(crv, pr=mn + (mx - mn) * i / 400, position=True)
            for i in range(401)
        ]

        def arclen_in(lo, hi):
            return sum(
                sum((b[k] - a[k]) ** 2 for k in range(3)) ** 0.5
                for a, b in zip(samp, samp[1:])
                if lo <= (a[0] + b[0]) / 2 <= hi
            )

        sel = om.MSelectionList()
        sel.add(tube)
        dag = sel.getDagPath(0)
        dag.extendToShape()
        verts = om.MFnMesh(dag).getPoints(om.MSpace.kWorld)

        def density(lo, hi):  # rings per unit arc length in the x-band
            rings = sum(1 for v in verts if lo <= v.x <= hi) / 8.0
            return rings / max(arclen_in(lo, hi), 1e-6)

        tight, gentle = density(6, 10), density(22, 34)
        self.assertGreater(tight, gentle * 1.8)  # rings pack onto the tight bend
        # coarse total (+ a fixed end-anchor ring per open end, outside both bands)
        self.assertLessEqual(cmds.polyEvaluate(tube, vertex=True) // 8, 16)

    def test_polygon_uv_seams(self):
        """The polygon tube is UV-seamed for a clean unwrap: one cut along the
        length and one per cap -> a capped tube is 3 UV shells (body + 2 caps),
        and the seams survive triangulation; an open tube's body unwraps to a
        single strip with a lengthwise seam (UVs duplicated along the cut)."""
        pts = [(0, 0, 0), (3, 3, 0), (6, -3, 0), (9, 3, 0), (12, 0, 0)]

        def tube(**kw):
            cmds.file(new=True, force=True)
            c = cmds.curve(d=3, p=pts)
            return CurveToTube.create(
                c, output_type="polygon", sections=8, radius=0.5, **kw
            )[0]

        capped = tube(caps=True)
        self.assertEqual(cmds.polyEvaluate(capped, uvShell=True), 3)  # body + 2 caps
        v = cmds.polyEvaluate(capped, vertex=True)
        e = cmds.polyEvaluate(capped, edge=True)
        f = cmds.polyEvaluate(capped, face=True)
        self.assertEqual(v - e + f, 2)  # cuts don't change topology

        tri = tube(caps=True, quads=False)
        self.assertEqual(cmds.polyEvaluate(tri, uvShell=True), 3)  # survive triangulation

        open_t = tube(caps=False)
        self.assertEqual(cmds.polyEvaluate(open_t, uvShell=True), 1)  # body strip
        # the lengthwise cut duplicates the UVs along the seam.
        self.assertGreater(
            cmds.polyEvaluate(open_t, uvcoord=True), cmds.polyEvaluate(open_t, vertex=True)
        )

    def test_polygon_ends_fixed_across_path_res(self):
        """The open ends must stay put as Path Res changes — only the interior
        re-tessellates. End ring centroids are fixed (endpoints are always kept)
        and the end rings must not tilt/rotate (a fixed end-tangent anchor)."""
        pts = [(0, 0, 0), (3, 4, 0), (6, -4, 0), (9, 4, 0), (12, 0, 0)]
        sections, radius = 8, 0.6

        def end_ring(mesh, endpoint):
            sel = om.MSelectionList()
            sel.add(mesh)
            dag = sel.getDagPath(0)
            dag.extendToShape()
            verts = om.MFnMesh(dag).getPoints(om.MSpace.kWorld)
            ep = om.MVector(*endpoint)
            near = sorted(range(len(verts)), key=lambda i: (om.MVector(verts[i]) - ep).length())
            return [tuple(round(c, 4) for c in verts[i])[:3] for i in near[:sections]]

        base = None
        for pd in (1, 2, 4):
            cmds.file(new=True, force=True)
            crv = cmds.curve(d=3, p=pts)
            tube = CurveToTube.create(
                crv, output_type="polygon", sections=sections, radius=radius,
                path_divisions=pd, caps=False,
            )[0]
            rings = {"s": set(end_ring(tube, pts[0])), "e": set(end_ring(tube, pts[-1]))}
            if base is None:
                base = rings
                continue
            # Each end ring vertex must match a base vertex within a tight band
            # (well under the ~0.07 ≈ 11%-of-radius drift the unpinned tangent
            # produced before the end-anchor fix).
            for side in ("s", "e"):
                for p in rings[side]:
                    drift = min(
                        sum((p[k] - q[k]) ** 2 for k in range(3)) ** 0.5 for q in base[side]
                    )
                    self.assertLess(drift, 0.02, f"end {side} drifted {drift} at pd={pd}")

    def test_polygon_caps_have_hard_edges(self):
        """Smoothing is by angle: the body shades smooth (soft edges) while the
        cap rims stay hard, so the caps read as crisp ends."""
        import mayatk as mtk

        pts = [(0, 0, 0), (3, 3, 0), (6, -3, 0), (9, 3, 0), (12, 0, 0)]
        c = cmds.curve(d=3, p=pts)
        tube = CurveToTube.create(
            c, output_type="polygon", sections=8, radius=0.5, caps=True
        )[0]

        # The cap rings (detected the way the engine seams them) must be hard.
        _, cap_rings = mtk.UvUtils.get_cylinder_seam_edges(tube)
        cap_ids = {int(e.split("[")[1].rstrip("]")) for e in cmds.ls(cap_rings, flatten=True)}
        self.assertEqual(len(cap_ids), 16)  # two 8-edge cap rings

        sel = om.MSelectionList()
        sel.add(tube)
        dag = sel.getDagPath(0)
        dag.extendToShape()
        it = om.MItMeshEdge(dag)
        hard = set()
        while not it.isDone():
            if not it.isSmooth:
                hard.add(it.index())
            it.next()
        # Every cap-ring edge is hard; no body edge is.
        self.assertTrue(cap_ids.issubset(hard))
        self.assertEqual(hard - cap_ids, set())

    def test_polygon_triangulate_option(self):
        tube = CurveToTube.create(
            self.path, output_type="polygon", sections=6, quads=False
        )[0]
        self.assertEqual(
            cmds.polyEvaluate(tube, face=True), cmds.polyEvaluate(tube, triangle=True)
        )

    def test_polygon_keeps_source_curve(self):
        CurveToTube.create(self.path, output_type="polygon", sections=8)
        self.assertTrue(cmds.objExists(self.path))
        self.assertEqual(len(cmds.ls(type="nurbsSurface") or []), 0)

    def test_capped_tube_is_closed_shell(self):
        tube = CurveToTube.create(
            self.path, output_type="polygon", sections=10, caps=True
        )[0]
        v = cmds.polyEvaluate(tube, vertex=True)
        e = cmds.polyEvaluate(tube, edge=True)
        f = cmds.polyEvaluate(tube, face=True)
        self.assertEqual(v - e + f, 2)  # sphere topology (watertight)
        self.assertEqual(cmds.polyEvaluate(tube, shell=True), 1)
        self.assertGreater(_signed_volume(tube), 0)  # normals point outward

    def test_side_normals_point_outward(self):
        straight = cmds.curve(d=1, p=[(0, 0, 0), (4, 0, 0), (8, 0, 0)], name="ctt_x")
        tube = CurveToTube.create(
            straight, output_type="polygon", sections=10, path_divisions=2, caps=True
        )[0]
        self.assertEqual(_side_faces_outward(tube), 0)

    # ----------------------------------------------------------- closed loop
    def test_closed_path_makes_watertight_torus(self):
        loop = cmds.circle(name="ctt_loop", normal=(0, 1, 0), radius=5)[0]  # periodic
        tube = CurveToTube.create(loop, output_type="polygon", sections=6)[0]
        v = cmds.polyEvaluate(tube, vertex=True)
        e = cmds.polyEvaluate(tube, edge=True)
        f = cmds.polyEvaluate(tube, face=True)
        self.assertEqual(v - e + f, 0)  # torus topology
        self.assertEqual(cmds.polyEvaluate(tube, shell=True), 1)
        self.assertIsNone(cmds.polyInfo(tube, nonManifoldEdges=True))
        self.assertGreater(_signed_volume(tube), 0)  # normals point outward

    # ------------------------------------------------------------------ live
    def test_live_nurbs_curve_drives_tube(self):
        crv = cmds.curve(d=3, p=[(0, 0, 0), (3, 3, 0), (6, -3, 0), (9, 3, 0), (12, 0, 0)], name="ctt_live")
        tube = CurveToTube.create(crv, output_type="nurbs", sections=8, live=True)[0]
        self.assertEqual(_shape_type(tube), "nurbsSurface")
        self.assertTrue(_nurbs_outward(tube))
        before = cmds.exactWorldBoundingBox(tube)
        cmds.move(0, 8, 0, f"{crv}.cv[2]", relative=True)  # editing the curve...
        self.assertNotEqual(cmds.exactWorldBoundingBox(tube), before)  # ...drives the tube
        # The profile circle is kept (hidden) as the live cross-section input,
        # parented under the tube so it isn't a stray scene object.
        profiles = cmds.ls("*_profile*", type="transform") or []
        self.assertTrue(profiles)
        self.assertFalse(cmds.getAttr(f"{profiles[0]}.visibility"))
        parent = (cmds.listRelatives(profiles[0], parent=True) or [""])[0]
        self.assertEqual(parent, tube)

    def test_live_polygon_source_curve_resampled_and_drives(self):
        crv = cmds.curve(
            d=3, p=[(i, math.sin(i * 0.5) * 2, 0) for i in range(24)], name="ctt_livep"
        )
        shp = cmds.listRelatives(crv, s=True)[0]
        cvs_before = cmds.getAttr(shp + ".spans") + cmds.getAttr(shp + ".degree")
        tube = CurveToTube.create(crv, output_type="polygon", sections=10, live=True, caps=True)[0]
        self.assertEqual(_shape_type(tube), "mesh")
        v = cmds.polyEvaluate(tube, vertex=True)
        e = cmds.polyEvaluate(tube, edge=True)
        f = cmds.polyEvaluate(tube, face=True)
        self.assertEqual(v - e + f, 2)  # capped watertight shell
        self.assertGreater(_signed_volume(tube), 0)  # outward
        self.assertIn("nurbsTessellate", [cmds.nodeType(h) for h in cmds.listHistory(tube)])
        # The source curve was RESAMPLED in place (fewer CVs) and still drives
        # the tube live -- editing it updates the mesh.
        cvs_after = cmds.getAttr(shp + ".spans") + cmds.getAttr(shp + ".degree")
        self.assertLess(cvs_after, cvs_before)
        before = cmds.exactWorldBoundingBox(tube)
        cmds.move(0, 8, 0, f"{crv}.cv[1]", relative=True)  # editing the SOURCE curve...
        self.assertNotEqual(cmds.exactWorldBoundingBox(tube), before)  # ...drives the tube

    def test_live_polygon_normals_invert_on_curve_edit_is_extrude_limitation(self):
        """REPLICATES the reported quirk: a LIVE polygon tube's normals can
        invert when the control curve is edited.

        Root cause (localized in test/temp_tests): `cmds.extrude`'s swept-surface
        normal orientation is NOT invariant under path edits — a large enough CV
        move makes the whole NURBS surface normal flip — and `nurbsToPoly` plus
        the baked `polyNormal` conform faithfully propagate that flip into the
        live mesh. The conform's reverse-or-not is a one-time decision frozen at
        build time, so it cannot track the flip. No `cmds.extrude` option keeps
        the surface stable and no baked `polyNormal` mode keeps the mesh outward
        across edits (verified by sweep). A baked tube (Keep History off) is
        immune because it has no upstream curve driving it.

        This is a characterization test: it pins the limitation (and will fail —
        as a reminder to update it — if the construction is reworked to be
        orientation-stable). Mitigation today: bake for the final mesh, or re-run
        Conform Normals after editing the curve."""
        crv = cmds.curve(
            d=3, p=[(0, 0, 0), (3, 4, 0), (6, -2, 0), (9, 3, 0), (12, 0, 0)],
            name="ctt_flip",
        )
        tube = CurveToTube.create(crv, output_type="polygon", sections=8, live=True)[0]
        self.assertGreater(_tube_outward_fraction(tube, crv), 0.9, "build is outward")

        # A large end-CV move flips the swept surface; the live mesh follows.
        cmds.move(0, 5, 0, f"{crv}.cv[0]", relative=True)
        cmds.dgdirty(tube)
        cmds.polyEvaluate(tube, vertex=True)  # force history re-evaluation
        self.assertLess(
            _tube_outward_fraction(tube, crv), 0.1, "normals inverted after edit"
        )

        # Baked control: no live driver, so editing the curve can't change it at
        # all — the tube is immune. Measure outward against the (matching) build
        # curve BEFORE moving, then prove immunity by vertex invariance.
        cmds.file(new=True, force=True)
        crv2 = cmds.curve(
            d=3, p=[(0, 0, 0), (3, 4, 0), (6, -2, 0), (9, 3, 0), (12, 0, 0)]
        )
        baked = CurveToTube.create(crv2, output_type="polygon", sections=8, live=False)[0]
        self.assertGreater(_tube_outward_fraction(baked, crv2), 0.9, "baked is outward")
        before = cmds.xform(f"{baked}.vtx[*]", q=True, ws=True, t=True)
        cmds.move(0, 5, 0, f"{crv2}.cv[0]", relative=True)
        after = cmds.xform(f"{baked}.vtx[*]", q=True, ws=True, t=True)
        self.assertEqual(before, after, "baked tube is unaffected by curve edits")

    def test_live_polygon_exact_sides_on_curved_path(self):
        # `sections` is exact around even on a bend (nurbsToPoly uType=2).
        for sec in (6, 12):
            cmds.file(new=True, force=True)
            c = cmds.curve(d=3, p=[(0, 0, 0), (3, 3, 0), (6, -3, 0), (9, 3, 0), (12, 0, 0)])
            tube = CurveToTube.create(c, output_type="polygon", sections=sec, live=True, caps=False)[0]
            around = cmds.polyEvaluate(tube, vertex=True) - cmds.polyEvaluate(tube, face=True)
            self.assertEqual(around, sec)

    def test_live_commit_single_undo(self):
        """A committed live tube (+ its hidden inputs) must undo in one step."""
        for out in ("nurbs", "polygon"):
            with self.subTest(output_type=out):
                cmds.file(new=True, force=True)
                src = cmds.curve(d=3, p=[(0, 0, 0), (3, 2, 0), (6, -2, 0), (9, 0, 0)], name="u_src")
                before = set(cmds.ls(assemblies=True))
                cmds.undoInfo(openChunk=True, chunkName="CurveToTube")
                CurveToTube.create(src, output_type=out, sections=8, live=True)
                cmds.undoInfo(closeChunk=True)
                cmds.undo()
                self.assertEqual(set(cmds.ls(assemblies=True)), before)
                # No leftover geometry or construction inputs after a single undo.
                self.assertEqual(
                    (cmds.ls(type="mesh") or [])
                    + (cmds.ls(type="nurbsSurface") or [])
                    + [c for c in (cmds.ls(type="nurbsCurve") or []) if "_path" in c],
                    [],
                )

    # --------------------------------------------------------------- general
    def test_multiple_curves(self):
        c2 = cmds.curve(d=1, p=[(0, 5, 0), (5, 5, 0)], name="ctt_path2")
        result = CurveToTube.create([self.path, c2], output_type="nurbs")
        self.assertEqual(len(result), 2)

    def test_create_leaves_clean_object_selection(self):
        """create() must leave only the result transform(s) selected — not the
        seam edges the polygon build's polyMapCut/polySoftEdge leave behind
        (which stuck Maya in component mode and broke the result selection)."""
        for output_type in ("nurbs", "polygon"):
            with self.subTest(output_type=output_type):
                cmds.file(new=True, force=True)
                c = cmds.curve(d=3, p=[(0, 0, 0), (3, 3, 0), (6, -3, 0), (9, 3, 0), (12, 0, 0)])
                tubes = CurveToTube.create(
                    c, output_type=output_type, sections=8, caps=True
                )
                self.assertEqual(
                    set(cmds.ls(selection=True, long=True) or []),
                    set(cmds.ls(tubes, long=True)),
                )
                # No lingering component (vertex/edge/face) selection.
                self.assertFalse(
                    cmds.filterExpand(cmds.ls(selection=True), sm=(31, 32, 34)) or []
                )

    @unittest.skipIf(QtWidgets is None, "Qt not available")
    def test_selection_respects_toggle_preview_and_commit(self):
        """Through the *real* Preview (enable -> Create), Select Result must
        control the selection on BOTH the live preview and the commit, for BOTH
        outputs: on -> the tube is selected, off -> it is not. Regressions:
        (1) the operation's own leftover selection (NURBS extrude leaves the
        surface selected; baked polygon leaves nothing) won at commit; (2) the
        preview ignored the toggle entirely — `create` always selects its result
        (to clear the seam components) and the toggle was only applied on the
        commit replay, so previewing with Select Result off still selected."""
        from mayatk.core_utils.preview import Preview

        for output_type in ("nurbs", "polygon"):
            for want in (True, False):
                with self.subTest(output_type=output_type, select=want):
                    cmds.file(new=True, force=True)
                    crv = cmds.curve(
                        d=3, p=[(0, 0, 0), (3, 3, 0), (6, -3, 0), (9, 3, 0), (12, 0, 0)],
                        name="cmt_crv",
                    )
                    chk004 = QtWidgets.QCheckBox()
                    chk004.setChecked(want)
                    op = types.SimpleNamespace(
                        PRESERVE_GEOMETRY=True,
                        ui=types.SimpleNamespace(chk004=chk004),
                        last_tubes=[],
                        operated_objects=set(),
                    )

                    def _perform(objects, contract, _op=op, _ot=output_type):
                        _op.last_tubes = CurveToTube.create(
                            objects, output_type=_ot, sections=8
                        )
                        # Mirror production: apply on every build, defer on commit.
                        CurveToTubeSlots._apply_result_selection(
                            _op, defer=contract is None
                        )

                    op.perform_operation = _perform
                    preview = Preview(
                        op,
                        QtWidgets.QCheckBox(),
                        QtWidgets.QPushButton(),
                        message_func=lambda m: None,
                    )
                    cmds.select(crv)

                    # Live preview: the toggle governs the preview selection.
                    preview.enable()
                    prev_tube = cmds.ls(op.last_tubes[0], long=True)[0]
                    sel = set(cmds.ls(selection=True, long=True) or [])
                    self.assertEqual(prev_tube in sel, want, "preview")

                    # Commit: same for the final result.
                    preview.finalize_changes()
                    tube_long = cmds.ls(op.last_tubes[0], long=True)[0]
                    sel = set(cmds.ls(selection=True, long=True) or [])
                    self.assertEqual(tube_long in sel, want, "commit immediate")

                    # Run the deferred re-assert (real Maya fires it on idle).
                    try:
                        from maya.utils import processIdleEvents

                        processIdleEvents()
                    except Exception:
                        pass
                    sel = set(cmds.ls(selection=True, long=True) or [])
                    self.assertEqual(tube_long in sel, want, "commit after idle")

    def test_update_footer_reports_stats(self):
        """_update_footer reports tri/vert counts for a polygon result, span
        counts for a NURBS one, and clears when there is no result."""

        class _Footer:
            def __init__(self):
                self._t = ""

            def setStatusText(self, t):
                self._t = t

        def fake_for(output_type, tubes):
            return types.SimpleNamespace(
                ui=types.SimpleNamespace(
                    footer=_Footer(),
                    cmb000=types.SimpleNamespace(currentData=lambda o=output_type: o),
                ),
                last_tubes=tubes,
            )

        poly = CurveToTube.create(self.path, output_type="polygon", sections=8, caps=True)
        f = fake_for("polygon", poly)
        CurveToTubeSlots._update_footer(f)
        self.assertIn("tris", f.ui.footer._t)
        self.assertIn("verts", f.ui.footer._t)

        cmds.file(new=True, force=True)
        crv = cmds.curve(d=3, p=[(0, 0, 0), (3, 3, 0), (6, 0, 0)])
        nurbs = CurveToTube.create(crv, output_type="nurbs", sections=8)
        f = fake_for("nurbs", nurbs)
        CurveToTubeSlots._update_footer(f)
        self.assertIn("spans", f.ui.footer._t)

        # No result -> cleared.
        f = fake_for("polygon", [])
        CurveToTubeSlots._update_footer(f)
        self.assertEqual(f.ui.footer._t, "")

    @unittest.skipIf(QtWidgets is None, "Qt not available")
    def test_chk004_toggle_applies_immediately(self):
        """Toggling Select Result (`chk004`) immediately (de)selects the current
        result — the wiring the panel installs, so the option is responsive and
        not only consulted at commit."""
        tube = cmds.polyCylinder(name="tog_tube")[0]
        chk = QtWidgets.QCheckBox()
        fake = types.SimpleNamespace(
            ui=types.SimpleNamespace(chk004=chk), last_tubes=[tube]
        )
        # Mirror CurveToTubeSlots.__init__'s wiring.
        chk.toggled.connect(lambda *_: CurveToTubeSlots._apply_result_selection(fake))

        cmds.select(clear=True)
        chk.setChecked(True)  # toggled -> select
        self.assertIn(tube, cmds.ls(selection=True) or [])
        chk.setChecked(False)  # toggled -> deselect
        self.assertNotIn(tube, cmds.ls(selection=True) or [])

    @unittest.skipIf(QtWidgets is None, "Qt not available")
    def test_apply_result_selection(self):
        """_apply_result_selection selects the tube when *Select Result* is on,
        explicitly **deselects** it when off (so 'off' clears a build-selected
        mesh), and falls back to selecting when the widget is absent."""
        tube = cmds.polyCylinder(name="fin_tube")[0]
        fake = types.SimpleNamespace(
            ui=types.SimpleNamespace(chk004=QtWidgets.QCheckBox()),
            last_tubes=[tube],
        )

        # On -> selects the tube.
        fake.ui.chk004.setChecked(True)
        cmds.select(clear=True)
        CurveToTubeSlots._apply_result_selection(fake)
        self.assertIn(tube, cmds.ls(selection=True) or [])

        # Off -> explicitly deselects it even when currently selected.
        fake.ui.chk004.setChecked(False)
        cmds.select(tube)
        CurveToTubeSlots._apply_result_selection(fake)
        self.assertNotIn(tube, cmds.ls(selection=True) or [])

        # Missing widget -> defensive fallback selects (prior behavior).
        bare = types.SimpleNamespace(ui=types.SimpleNamespace(), last_tubes=[tube])
        cmds.select(clear=True)
        CurveToTubeSlots._apply_result_selection(bare)
        self.assertIn(tube, cmds.ls(selection=True) or [])

    def test_commit_reread_settles_after_restore(self):
        """Regression: in interactive Maya the marking-menu panel's state
        restore can leave `chk004` reporting its `.ui` default (checked) at the
        synchronous commit, settling to the saved "off" only slightly later — so
        the FIRST commit from a saved "off" wrongly *selected* the tube. The
        deferred idle re-assert must RE-READ the toggle (not reuse a commit-time
        snapshot) so the settled value governs the final selection."""
        tube = cmds.polyCylinder(name="settle_tube")[0]
        tube_long = cmds.ls(tube, long=True)[0]

        class _SettlingCheck:
            """isChecked() reads True (stale .ui default) on the immediate
            apply, then False (settled saved value) on the deferred re-assert."""

            def __init__(self):
                self._reads = 0

            def isChecked(self):
                self._reads += 1
                return self._reads == 1

        fake = types.SimpleNamespace(
            ui=types.SimpleNamespace(chk004=_SettlingCheck()),
            last_tubes=[tube],
        )

        # Capture the deferred re-assert instead of relying on the idle loop —
        # processIdleEvents does not reliably pump a lowestPriority evalDeferred.
        captured = []
        orig_defer = cmds.evalDeferred
        cmds.evalDeferred = lambda fn, **kw: captured.append(fn)
        try:
            cmds.select(clear=True)
            CurveToTubeSlots._apply_result_selection(fake, defer=True)
        finally:
            cmds.evalDeferred = orig_defer

        # Immediate read = stale True -> selected.
        self.assertIn(tube_long, cmds.ls(selection=True, long=True) or [])
        # Run the deferred re-assert: it re-reads the now-settled False -> the
        # tube is deselected (the settled value wins over the stale snapshot).
        self.assertTrue(captured, "commit must schedule a deferred re-assert")
        for fn in captured:
            fn()
        self.assertNotIn(tube_long, cmds.ls(selection=True, long=True) or [])

    def test_non_curve_selection_raises(self):
        loc = cmds.spaceLocator()[0]
        with self.assertRaises(OperationError):
            CurveToTube.create(loc, output_type="nurbs")

    # ------------------------------------------------------------------ undo
    def test_committed_tube_is_single_undo(self):
        """A commit (open/close chunk) must leave nothing behind on undo.

        The baked polygon collapses Sweep Mesh history and the NURBS path
        deletes the extrude history; both are cmds-based, so a single Ctrl+Z
        after commit must remove the result completely (no orphaned nodes).
        """
        for out in ("polygon", "nurbs"):
            with self.subTest(output_type=out):
                cmds.file(new=True, force=True)
                path = cmds.curve(d=1, p=[(0, 0, 0), (4, 0, 0), (8, 0, 0)], name="u_path")
                before = set(cmds.ls(assemblies=True))
                cmds.undoInfo(openChunk=True, chunkName="CurveToTube")
                CurveToTube.create(path, output_type=out, sections=8)
                cmds.undoInfo(closeChunk=True)
                cmds.undo()
                self.assertEqual(set(cmds.ls(assemblies=True)), before)
                self.assertEqual(cmds.ls(type="mesh") or [], [])
                self.assertEqual(cmds.ls(type="nurbsSurface") or [], [])


if __name__ == "__main__":
    unittest.main()
