# !/usr/bin/python
# coding=utf-8
"""Quantitative deformation metrics for tube rigs.

Rig quality is otherwise judged by eye, which is how a 1.5-tube-radius
axis drift and a hinge-crease at every driver handoff both survived a green
test suite. Every metric here is scale-free — distances are reported in TUBE
RADII and angles in degrees — so one tolerance holds across assets of any
size, and each answers a question an artist would actually ask:

- :meth:`TubeRigMetrics.conformance`   "does the mesh still sit on its rig?"
- :meth:`TubeRigMetrics.ring_integrity` "do the cross-sections keep their shape?"
- :meth:`TubeRigMetrics.smoothness`     "does it bend, or does it crease?"
- :meth:`TubeRigMetrics.end_alignment`  "does the end face stay square to its control?"
- :meth:`TubeRigMetrics.control_usability` "can the controls actually be grabbed?"
- :meth:`TubeRigMetrics.max_displacement` "did binding (or a pose) move the mesh at all?"
- :meth:`TubeRigMetrics.rigidity_drift` "does this zone move as one rigid piece?"
- :meth:`TubeRigMetrics.spacing_uniformity` "does stretch distribute evenly, or bunch?"
- :meth:`TubeRigMetrics.bone_lengths`  "did posing stretch bones rotation never should?"

Test-side only (``mayatk/test/``): these read a built rig and return numbers,
so they belong with the assertions rather than in the shipped package.
"""
import math
from typing import Dict, List, Optional, Sequence

import maya.cmds as cmds
import maya.api.OpenMaya as om

from mayatk.rig_utils.tube_path import TubePath


class _TubeRigMetricsInternal(object):
    """Geometry helpers shared by the metrics."""

    @staticmethod
    def _mesh_points(shape) -> "om.MPointArray":
        sel = om.MSelectionList()
        sel.add(str(shape))
        dag = sel.getDagPath(0)
        if not dag.hasFn(om.MFn.kMesh):
            dag.extendToShape()
        return om.MFnMesh(dag).getPoints(om.MSpace.kWorld)

    @staticmethod
    def _curve_fn(curve) -> "om.MFnNurbsCurve":
        """A function set read FRESH from the shape.

        Never cache one across a pose change: a stale handle silently keeps
        answering with the REST curve, which reads as a perfect result.
        """
        sel = om.MSelectionList()
        sel.add(cmds.listRelatives(str(curve), s=True, f=True)[0])
        return om.MFnNurbsCurve(sel.getDagPath(0))

    @staticmethod
    def _point_to_polyline(p: "om.MVector", poly: Sequence["om.MVector"]) -> float:
        """Distance from *p* to the piecewise-linear path through *poly*."""
        best = float("inf")
        for a, b in zip(poly, poly[1:]):
            ab = b - a
            denom = ab * ab
            t = 0.0 if denom < 1e-12 else max(0.0, min(1.0, ((p - a) * ab) / denom))
            best = min(best, (p - (a + ab * t)).length())
        return best

    @classmethod
    def _ring_frames(cls, shape, rings):
        """(centroid, mean_radius, radii, plane_normal) per ring, world space."""
        pts = cls._mesh_points(shape)
        out = []
        for ring in rings:
            acc = om.MVector()
            for v in ring:
                acc += om.MVector(pts[v])
            centre = acc / len(ring)
            radii = [(om.MVector(pts[v]) - centre).length() for v in ring]
            normal = om.MVector()
            for i, v in enumerate(ring):
                a = om.MVector(pts[v]) - centre
                b = om.MVector(pts[ring[(i + 1) % len(ring)]]) - centre
                normal += a ^ b
            normal = normal.normal() if normal.length() > 1e-9 else om.MVector(1, 0, 0)
            out.append((centre, sum(radii) / len(radii), radii, normal))
        return out


class TubeRigMetrics(_TubeRigMetricsInternal):
    """Deformation quality of a built tube rig, in scale-free units."""

    @staticmethod
    def rings(mesh) -> List[List[int]]:
        """Cross-section vertex groups — the unit every metric is keyed on."""
        return TubePath.get_vertex_rings(mesh)

    @classmethod
    def tube_radius(cls, mesh, rings=None) -> float:
        """Mean cross-section radius: the scale every distance is divided by."""
        shape = TubePath._resolve_mesh_shape(mesh)
        frames = cls._ring_frames(shape, rings or cls.rings(mesh))
        return sum(f[1] for f in frames) / len(frames)

    @classmethod
    def conformance(cls, mesh, joints, rings=None, curve=None, radius=None) -> Dict:
        """How far the mesh has left its own rig, in tube radii.

        Each cross-section's centre is measured against the rig's axis — the
        IK *curve* when the rig has one, else the joint chain. A rig that is
        working keeps this near zero at ANY pose: the mesh is rigidly bound
        to the skeleton that defines that axis, so drift means the skinning
        and the skeleton disagree (the signature of DQS meeting joint scale).

        Returns ``None`` when the rig has no axis to measure against — an
        anchor rig is two end joints, whose "axis" is the straight chord, and
        a curved hose is legitimately far from its own chord. Reporting that
        as non-conformance would be measuring the metric's assumption, not
        the rig; judge those rigs by ring integrity, smoothness and end
        alignment instead.
        """
        shape = TubePath._resolve_mesh_shape(mesh)
        rings = rings or cls.rings(mesh)
        r = radius or cls.tube_radius(mesh, rings)
        if not curve and len(list(joints)) < 3:
            return None
        centres = [f[0] for f in cls._ring_frames(shape, rings)]

        if curve:
            fn = cls._curve_fn(curve)
            devs = [
                (
                    om.MVector(fn.closestPoint(om.MPoint(c), space=om.MSpace.kWorld)[0])
                    - c
                ).length()
                / r
                for c in centres
            ]
        else:
            poly = [
                om.MVector(*cmds.xform(str(j), q=True, ws=True, t=True)) for j in joints
            ]
            devs = [cls._point_to_polyline(c, poly) / r for c in centres]
        return {"max": max(devs), "mean": sum(devs) / len(devs), "per_ring": devs}

    @classmethod
    def ring_integrity(cls, mesh, rest_frames, rings=None, radius=None) -> Dict:
        """Cross-section shape preservation vs the rest pose.

        ``radius_ratio`` near 1 means no collapse (the candy-wrapper signal);
        ``roundness`` is ``(max - min) / mean`` radius within a ring, so 0 is
        a perfect circle and larger values mean pinch or shear — but ONLY on
        tubes whose rest sections are circles. A hex fitting reads ~0.15 at
        rest, so never gate roundness on a non-circular tube; gate
        ``shape_error_max`` instead: the per-vertex radius-ratio spread
        within a ring vs its own rest, which is 0 for ANY rest section (hex,
        rib, ellipse) under any rigid motion or uniform scale, and grows
        only on genuine pinch or shear.
        """
        shape = TubePath._resolve_mesh_shape(mesh)
        now = cls._ring_frames(shape, rings or cls.rings(mesh))
        ratios = [n[1] / max(rest[1], 1e-9) for n, rest in zip(now, rest_frames)]
        roundness = [
            (max(n[2]) - min(n[2])) / n[1] if n[1] > 1e-9 else 0.0 for n in now
        ]
        shape_err = 0.0
        for n, rest in zip(now, rest_frames):
            vr = [a / max(b, 1e-9) for a, b in zip(n[2], rest[2])]
            shape_err = max(shape_err, max(vr) - min(vr))
        return {
            "radius_ratio_min": min(ratios),
            "radius_ratio_max": max(ratios),
            "roundness_max": max(roundness),
            "shape_error_max": shape_err,
        }

    @classmethod
    def smoothness(cls, mesh, rings=None, radius=None) -> Dict:
        """Peak curvature of the MESH's own centerline, in 1/tube-radius.

        This is the crease metric: a kink is a local curvature spike, so
        comparing the posed peak against the rest peak separates "the hose
        bent" from "the hose creased" without any reference to the rig.
        """
        shape = TubePath._resolve_mesh_shape(mesh)
        rings = rings or cls.rings(mesh)
        r = radius or cls.tube_radius(mesh, rings)
        centres = [f[0] for f in cls._ring_frames(shape, rings)]
        peak = 0.0
        for a, b, c in zip(centres, centres[1:], centres[2:]):
            v1, v2 = b - a, c - b
            seg = (v1.length() + v2.length()) / 2
            if seg > 1e-9 and v1.length() > 1e-9 and v2.length() > 1e-9:
                peak = max(peak, v1.angle(v2) / seg)
        return {"peak_curvature": peak * r}

    @classmethod
    def end_alignment(cls, mesh, control, at_start: bool = False, rings=None) -> float:
        """Angle (deg) between the end cap's plane and its control's axis.

        The end of a hose should stay square to whatever it is plugged into,
        so this measures the end ring's best-fit plane normal against the
        control's local X (the axis the rig builds down the tube). 0 means
        the cap face is perpendicular to the control; the value is folded
        into [0, 90] because the normal's sign is arbitrary.
        """
        shape = TubePath._resolve_mesh_shape(mesh)
        frames = cls._ring_frames(shape, rings or cls.rings(mesh))
        normal = frames[0][3] if at_start else frames[-1][3]
        m = cmds.xform(str(control), q=True, ws=True, matrix=True)
        axis = om.MVector(m[0], m[1], m[2])
        if axis.length() < 1e-9:
            return float("nan")
        angle = math.degrees(normal.angle(axis.normal()))
        return min(angle, 180.0 - angle)

    @staticmethod
    def control_usability(controls, tube_radius: float) -> Dict:
        """Can these controls actually be picked apart in a viewport?

        A control wider than the gap to its neighbour swallows it, and a
        chain of them reads as one blob with nothing individually selectable
        — the failure behind "this rig has no controls". ``size_vs_gap``
        below 1 means each control fits its own slot.
        """
        controls = [str(c) for c in controls]
        result = {
            "count": len(controls),
            "all_visible": all(
                cmds.getAttr(f"{c}.visibility") for c in controls if cmds.objExists(c)
            ),
            "all_have_shapes": all(
                bool(cmds.listRelatives(c, s=True)) for c in controls if cmds.objExists(c)
            ),
        }
        pos = [
            om.MVector(*cmds.xform(c, q=True, ws=True, t=True))
            for c in controls
            if cmds.objExists(c)
        ]
        gaps = [(b - a).length() for a, b in zip(pos, pos[1:])]
        result["min_gap"] = min(gaps) if gaps else float("inf")
        # Measure each control's OWN shapes. FK controls nest, so asking for
        # a control transform's bounding box returns the whole remaining
        # chain's extent and every control looks enormous.
        widths = []
        for c in controls:
            if not cmds.objExists(c):
                continue
            for shp in cmds.listRelatives(c, s=True, f=True) or []:
                bbox = cmds.exactWorldBoundingBox(shp)
                widths.append(
                    max(bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2])
                )
        result["max_width"] = max(widths) if widths else 0.0
        result["size_vs_gap"] = (
            result["max_width"] / result["min_gap"] if gaps else 0.0
        )
        return result

    @classmethod
    def rest_frames(cls, mesh, rings=None):
        """Snapshot to compare a posed state against (feeds ring_integrity)."""
        return cls._ring_frames(
            TubePath._resolve_mesh_shape(mesh), rings or cls.rings(mesh)
        )

    @classmethod
    def snapshot_points(cls, mesh) -> "om.MPointArray":
        """World-space vertex snapshot (feeds max_displacement / rigidity_drift)."""
        return cls._mesh_points(TubePath._resolve_mesh_shape(mesh))

    @classmethod
    def max_displacement(cls, mesh, rest_points, radius: float) -> float:
        """Largest vertex move since *rest_points*, in tube radii.

        Snapshot before binding, measure after: a correct bind is a no-op on
        the rest pose (weight rows sum to 1), so anything above numeric noise
        means the bind itself popped the mesh.
        """
        now = cls.snapshot_points(mesh)
        return (
            max(
                (om.MVector(a) - om.MVector(b)).length()
                for a, b in zip(now, rest_points)
            )
            / radius
        )

    @classmethod
    def rigidity_drift(
        cls, mesh, rings, ring_indices: Sequence[int], rest_points, radius: float
    ) -> float:
        """How far a zone is from moving as ONE rigid piece, in tube radii.

        Max change of any intra-zone vertex-pair distance vs rest. A rigid
        body preserves every pairwise distance, so this is exact, needs no
        best-fit solve, and is immune to vertex ordering. Distinguishes "the
        fitting rode along" from "the fitting bent/stretched" — note a
        uniform scale (the stretch system) DOES register, deliberately: a
        steel fitting must not stretch with the rubber.
        """
        now = cls.snapshot_points(mesh)
        verts = [v for i in ring_indices for v in rings[i]]
        worst = 0.0
        for i in range(len(verts)):
            a = verts[i]
            for j in range(i + 1, len(verts)):
                b = verts[j]
                d0 = (om.MVector(rest_points[a]) - om.MVector(rest_points[b])).length()
                d1 = (om.MVector(now[a]) - om.MVector(now[b])).length()
                worst = max(worst, abs(d1 - d0))
        return worst / radius

    @classmethod
    def spacing_uniformity(cls, mesh, rest_frames, rings=None) -> float:
        """Ring-spacing ratio spread posed/rest: ``(max - min) / mean``.

        Under a clean global stretch every inter-ring gap scales by the same
        factor, so this stays near 0; bunching (weights that pile stretch
        into one span) shows up directly.
        """
        frames = cls._ring_frames(
            TubePath._resolve_mesh_shape(mesh), rings or cls.rings(mesh)
        )
        ratios = []
        for (a1, b1), (a0, b0) in zip(
            zip(frames, frames[1:]), zip(rest_frames, rest_frames[1:])
        ):
            d0 = (b0[0] - a0[0]).length()
            if d0 > 1e-6:
                ratios.append((b1[0] - a1[0]).length() / d0)
        if not ratios:
            return 0.0
        mean = sum(ratios) / len(ratios)
        return (max(ratios) - min(ratios)) / mean if mean > 1e-9 else 0.0

    @staticmethod
    def bone_lengths(joints) -> List[float]:
        """World-space bone lengths down a chain. Rotation-only posing must
        preserve these; drift means something is dragging joints off the
        chain (the FK span-boundary failure reads 0 in curvature and ~100%
        here)."""
        ps = [
            om.MVector(*cmds.xform(str(j), q=True, ws=True, t=True)) for j in joints
        ]
        return [(b - a).length() for a, b in zip(ps, ps[1:])]
