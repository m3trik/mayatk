# !/usr/bin/python
# coding=utf-8
"""Sweep a circular profile along NURBS curve(s) to build a tube.

The work splits into two reusable halves (SRP):

- :class:`CurveToTube` — *pure geometry*. Given curve(s) and a handful of
  plain parameters it sweeps a profile along each path and returns the created
  transform(s). The output is either a **NURBS** surface or a **polygon**
  mesh; it has no Qt dependency (it raises the preview's :class:`OperationError`
  for clean error popups, as :mod:`~mayatk.edit_utils.bridge` does).
- :class:`CurveToTubeSlots` — *UI wiring*. Drives the engine through the
  hermetic :class:`~mayatk.core_utils.preview.Preview`, exposing an
  output-type combo (NURBS / Polygon) and the options that go with each.

Both output types share one node chain, so toggling *Keep History* never
changes the topology — it only decides whether the history is kept (the curve
keeps driving the tube) or collapsed (baked). NURBS extrudes a circle along the
curve. Polygon places rings by a **Ramer-Douglas-Peucker** simplification of the
curve (:func:`pythontk.Polyline.simplify`): the path is taken *through* the
kept points and the circle is extruded along it, then ``nurbsToPoly`` converts
the surface (exactly ``sections`` sides around, one ring per span). Because the
rings land only at the kept points, they concentrate on the tight bends and thin
out on straight runs, decoupled from the source curve's CV spacing. A *live*
polygon **resamples the source curve in place** to those points, so the curve
stays the editable driver (the hermetic Preview restores it on rollback).

The polygon mesh is UV-seamed for a clean unwrap (one lengthwise cut plus a ring
per cap) and shaded smooth with hard cap rims; both reuse
:class:`~mayatk.uv_utils._uv_utils.UvUtils` cylinder-seam detection, which also
backs the standalone *Unwrap Cylinder* UV tool.
"""
from __future__ import annotations

from typing import List, Optional

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError as error:
    cmds = None
    om = None
    print(__file__, error)

import pythontk as ptk
from uitk.widgets.mixins.tooltip_mixin import fmt

# from this package:
from mayatk.core_utils.preview import Preview, OperationError
from mayatk.uv_utils._uv_utils import UvUtils


class CurveToTube(ptk.LoggingMixin):
    """Extrude a circular profile along NURBS curve(s) to build a tube.

    Stateless: every entry point is a ``classmethod`` returning plain Maya
    node names, so it composes freely and is callable straight from a script
    (``mtk.CurveToTube.create(curves, ...)``) as well as from the preview UI.
    """

    # (label, value) pairs for the UI combo and for input validation.
    OUTPUT_TYPES = (("NURBS Tube", "nurbs"), ("Polygon Tube", "polygon"))

    # Polygon ring placement (RDP): how many points to sample the curve into
    # before simplifying, and the deviation tolerance as a fraction of arc
    # length at path_divisions=1 (rings land at the kept points — dense on
    # bends, sparse on straight runs). Path Res tightens it (divides the tol).
    _DENSE_SAMPLES = 200
    _RDP_TOLERANCE = 0.01
    # Fraction of the dense sampling in from each open end to anchor a ring, so
    # the end tangent (and thus the end ring's orientation) is fixed regardless
    # of Path Res. Small enough to track the true end direction, large enough to
    # avoid a degenerate first/last span.
    _END_ANCHOR_FRAC = 0.03

    @classmethod
    def create(
        cls,
        curves,
        output_type: str = "nurbs",
        radius: float = 1.0,
        sections: int = 8,
        path_divisions: int = 1,
        degree: int = 3,
        caps: bool = True,
        quads: bool = True,
        live: bool = False,
        cleanup: bool = True,
        name: str = "tube",
    ) -> List[str]:
        """Build a tube along each selected curve.

        Parameters:
            curves (str/obj/list): NURBS curve transform(s) or shape(s) to
                sweep along. Non-curve selections are ignored.
            output_type (str): ``"nurbs"`` for a NURBS surface, ``"polygon"``
                for a tessellated polygon mesh.
            radius (float): Radius of the swept profile (1:1 with world units).
            sections (int): Spans around the profile — the surface's smoothness
                for NURBS, the exact number of sides for polygon.
            path_divisions (int): Polygon only — ring resolution along the path.
                Rings are placed by an RDP simplification of the curve (dense
                where it bends, sparse on straight runs); this scales that
                density (1 = coarsest, higher = finer).
            degree (int): Degree of the profile circle / NURBS surface (1 for
                a faceted profile, 3 for smooth). NURBS only.
            caps (bool): Polygon only — fill the open ends of an open tube.
            quads (bool): Polygon only — quads (True) vs triangles (False).
            live (bool): Keep the construction history so the curve keeps
                driving the tube (editing the curve updates it). A NURBS tube
                extrudes from the source curve directly. A polygon tube
                **resamples the source curve in place** to the RDP points, so it
                stays the editable driver (hidden inputs — profile circle, NURBS
                surface — are parented under the tube). Either way the topology
                matches the baked result — *live* only toggles whether history is
                retained.
            cleanup (bool): Baked only — delete the construction inputs so only
                the finished tube is left.
            name (str): Base name for the created transform.

        Returns:
            (list) The created tube transform(s).
        """
        if cmds is None:
            raise RuntimeError("CurveToTube requires maya.cmds.")
        if output_type not in ("nurbs", "polygon"):
            raise ValueError(f"Unknown output_type: {output_type!r}")

        shapes = cls._curve_shapes(curves)
        if not shapes:
            raise OperationError(
                "No NURBS curve selected.",
                causes=["Select one or more NURBS curves to sweep along."],
                title="Curve to Tube",
            )

        results: List[str] = []
        for shape in shapes:
            results.append(
                cls._build_one(
                    shape,
                    output_type=output_type,
                    radius=radius,
                    sections=sections,
                    path_divisions=path_divisions,
                    degree=degree,
                    caps=caps,
                    quads=quads,
                    live=live,
                    cleanup=cleanup,
                    name=name,
                )
            )
        # Leave a clean object-level selection of the result. Polygon builds run
        # polyMapCut / polySoftEdge on the seam edges, which leaves those edges
        # selected in component mode — a confusing leftover for any caller (and
        # it hides a later object selection in the viewport). Reset to object
        # mode and select the finished tube(s), matching how Maya's own creation
        # commands behave.
        cls._reset_object_selection(results)
        return results

    @staticmethod
    def _reset_object_selection(nodes: List[str]) -> None:
        """Force object selection mode and select *nodes* (clears any component
        selection left by the build). Best-effort: no-op outside an interactive
        Maya / on failure."""
        try:
            cmds.selectMode(object=True)
        except Exception:
            pass
        alive = [n for n in nodes if n and cmds.objExists(n)]
        try:
            if alive:
                cmds.select(alive, replace=True)
            else:
                cmds.select(clear=True)
        except Exception:
            pass

    # ----------------------------------------------------------- internals
    @classmethod
    def _curve_shapes(cls, curves) -> List[str]:
        """Resolve *curves* to a de-duplicated list of ``nurbsCurve`` shapes."""
        flat = cmds.ls(curves, long=True, flatten=True) or []
        shapes: List[str] = []
        for node in flat:
            if not cmds.objExists(node):
                continue
            if cmds.objectType(node) == "nurbsCurve":
                found = [node]
            else:
                found = (
                    cmds.listRelatives(
                        node, shapes=True, fullPath=True, type="nurbsCurve",
                        noIntermediate=True,
                    )
                    or []
                )
            for s in found:
                if s not in shapes:
                    shapes.append(s)
        return shapes

    @classmethod
    def _build_one(
        cls,
        curve_shape: str,
        output_type: str,
        radius: float,
        sections: int,
        path_divisions: int,
        degree: int,
        caps: bool,
        quads: bool,
        live: bool,
        cleanup: bool,
        name: str,
    ) -> str:
        """Sweep one profile along one curve; return the finished transform."""
        sections = max(3, int(sections))
        path_divisions = max(1, int(path_divisions))
        if output_type == "nurbs":
            return cls._nurbs_tube(
                curve_shape, radius, sections, degree, live, cleanup, name
            )
        return cls._polygon_tube(
            curve_shape, radius, sections, path_divisions, caps, quads, live, name
        )

    # --------------------------------------------------------------- NURBS
    @staticmethod
    def _extrude_surface(curve_shape, radius, sections, degree, name):
        """Extrude a circle profile along the path; return (surface, profile).

        Both stay live (``constructionHistory=True``): the curve drives the
        surface and the circle drives the cross-section. Baked callers delete
        them; live callers keep them.
        """
        degree = 1 if int(degree) <= 1 else 3
        profile = cmds.circle(
            radius=float(radius),
            sections=sections,
            degree=degree,
            normal=(0, 1, 0),
            constructionHistory=True,
            name=f"{name}_profile#",
        )[0]
        # Tube extrude: sweep the profile along the path, reorienting it to the
        # path tangent (useProfileNormal) with the profile pivot riding the
        # path (useComponentPivot + fixedPath).
        surface = cmds.extrude(
            profile,
            curve_shape,
            extrudeType=2,  # 0 distance, 1 flat, 2 tube (along path)
            useComponentPivot=1,
            fixedPath=True,
            useProfileNormal=True,
            reverseSurfaceIfPathReversed=True,
            polygon=0,
            constructionHistory=True,
        )[0]
        return surface, profile

    @classmethod
    def _nurbs_tube(cls, curve_shape, radius, sections, degree, live, cleanup, name):
        """Extrude a NURBS circle along the path -> NURBS surface tube.

        Baked (``not live``): drop the extrude history and the profile circle.
        Live: keep the history and hide the profile circle as the live
        cross-section input. Either way conform the normals outward and set a
        fine display smoothness so the surface renders smooth, not faceted.
        """
        surface, profile = cls._extrude_surface(
            curve_shape, radius, sections, degree, name
        )
        if not live and cleanup:
            cmds.delete(surface, constructionHistory=True)
            cls._safe_delete(profile)
        # Extrude emits either orientation depending on how the curve was drawn
        # (reverseSurfaceIfPathReversed), so conform the normals outward to
        # match the polygon result instead of trusting a fixed direction.
        cls._conform_nurbs_outward(surface)
        tube = cmds.rename(surface, f"{name}#")
        if live:
            # A live extrude needs the profile circle as its cross-section
            # input; bundle it (hidden) under the tube so it isn't a stray node.
            cls._bundle_under(tube, profile)
        cls._smooth_nurbs_display(tube)
        return tube

    @staticmethod
    def _smooth_nurbs_display(surf):
        """Set fine display smoothness (the viewport ``3`` preset).

        A freshly extruded NURBS surface displays coarsely (faceted); this is
        the equivalent of pressing ``3`` so the tube renders smooth.
        """
        try:
            cmds.displaySmoothness(
                surf,
                divisionsU=3,
                divisionsV=3,
                pointsWire=16,
                pointsShaded=4,
                polygonObject=3,
            )
        except Exception:
            pass

    # ------------------------------------------------------------- polygon
    @classmethod
    def _polygon_tube(
        cls, curve_shape, radius, sections, path_divisions, caps, quads, live, name
    ):
        """Polygon tube via an ``extrude -> nurbsToPoly`` chain whose rings are
        placed by an **RDP simplification** of the curve.

        The curve is densely sampled and run through
        :func:`pythontk.Polyline.simplify`, which keeps points where the
        curve bends and drops near-collinear runs. The path is then defined
        *through* those points (so its spans — and the tube's rings — land only
        at the curvature-selected positions, dense on tight bends, sparse on
        straight runs, **decoupled from the source curve's CV spacing**). The
        circle is extruded along it and ``nurbsToPoly(uType=2, vType=1)``
        converts the surface to exactly ``sections`` sides around with one ring
        per span.

        ``live`` **resamples the source curve in place** to the kept points, so
        the curve itself stays the (editable) live driver — the hermetic Preview
        restores it on rollback (``PRESERVE_GEOMETRY``). Baked builds a throwaway
        path through the points and leaves the source curve untouched. Either way
        the topology is identical.

        Open tubes are then driven by a ``curveWarp`` **deformer** on a straight,
        already-conformed base mesh (:meth:`_polygon_tube_warped`) rather than a
        live ``extrude -> nurbsToPoly`` graph, so a live curve edit only
        repositions vertices and the normals can't flip (``cmds.extrude``'s
        swept-surface orientation is not edit-invariant). Closed (torus) paths
        and the no-plugin fallback keep the curved ``extrude -> nurbsToPoly``
        build; the finishing steps are shared via :meth:`_finish_poly_tube`.
        """
        closed = cmds.getAttr(f"{curve_shape}.form") in (1, 2)  # 1 closed, 2 periodic
        dense = cls._sample_centerline(curve_shape, cls._DENSE_SAMPLES)
        length = cmds.arclen(curve_shape) or 1.0
        tol = length * cls._RDP_TOLERANCE / path_divisions
        keep = set(ptk.Polyline.simplify(dense, tol))
        # Pin the end tangents so the end rings don't tilt/rotate as Path Res
        # changes: RDP keeps a closer first-interior point at finer tolerances,
        # which swings the endpoint tangent (the end ring is perpendicular to
        # it). Anchoring a fixed interior sample near each end fixes that tangent
        # — the open ends stay put while only the interior re-tessellates.
        if not closed:
            anchor = max(1, round(cls._DENSE_SAMPLES * cls._END_ANCHOR_FRAC))
            keep |= {anchor, len(dense) - 1 - anchor}
        keep = sorted(keep)
        rdp_pts = [dense[i] for i in keep]
        degree = max(1, min(3, len(rdp_pts) - 1))

        if live:
            # Resample the source curve in place -> it stays the editable driver
            # (identity preserved); the Preview restores it on rollback.
            path = (
                cmds.listRelatives(curve_shape, parent=True, fullPath=True)
                or [curve_shape]
            )[0]
            # rdp_pts are WORLD-space samples, but curve(replace=True, point=...)
            # writes CVs in the transform's OBJECT space. On a curve whose
            # transform carries any TRS (the common case — curves are rarely
            # frozen) feeding world points as object CVs offsets the whole curve
            # by its own transform. Map them back through the world-inverse first
            # so the resampled curve lands exactly where it was.
            obj_pts = cls._to_object_space(path, rdp_pts)
            cmds.curve(path, replace=True, degree=degree, point=obj_pts)
            throwaway = []
        else:
            # Baked: a throwaway path through the points; source curve untouched.
            path = cmds.rename(cmds.curve(degree=degree, point=rdp_pts), f"{name}_path#")
            throwaway = [path]
        if closed:
            cmds.closeCurve(
                path, constructionHistory=False, replaceOriginal=True, preserveShape=0
            )

        # Open tubes drive the mesh with a curveWarp DEFORMER on a straight base:
        # a deformer only repositions vertices, so a live curve edit can never
        # flip the winding/normals. (cmds.extrude's swept-surface orientation is
        # NOT edit-invariant — it globally flips when the path tangent swings
        # into the profile axis — and nurbsToPoly + the baked conform faithfully
        # propagate that, inverting a live tube; see _conform_poly_outward.)
        # Closed (torus) paths and the no-plugin fallback keep the original
        # curved extrude -> nurbsToPoly build.
        if not closed and cls._load_curvewarp():
            return cls._polygon_tube_warped(
                path, rdp_pts, radius, sections, caps, quads, live, throwaway, name
            )

        mesh, surface, profile = cls._extrude_poly_mesh(path, radius, sections, name)
        cls._finish_poly_tube(mesh, sections, caps, quads, dense, closed)

        if live:
            # Bundle the hidden construction inputs under the tube; the resampled
            # source curve stays separate as the visible, editable driver.
            cls._bundle_under(mesh, surface, profile)
        else:
            cmds.delete(mesh, constructionHistory=True)  # bake to a plain mesh
            for node in [surface, profile] + throwaway:
                cls._safe_delete(node)
        return mesh

    @classmethod
    def _extrude_poly_mesh(cls, path_shape, radius, sections, name):
        """Extrude the profile along ``path_shape`` and tessellate to a polygon
        mesh: exactly ``sections`` sides around (``uType=2``), one ring per span
        (``vType=1``), ``format=3`` for a clean result. Returns
        ``(mesh, surface, profile)`` with history live (callers bake/bundle).
        """
        surface, profile = cls._extrude_surface(path_shape, radius, sections, 3, name)
        mesh = cmds.nurbsToPoly(
            surface,
            constructionHistory=True,
            format=3,
            polygonType=1,  # quads
            uType=2,
            uNumber=int(sections),
            vType=1,
            vNumber=1,
        )[0]
        return cmds.rename(mesh, f"{name}#"), surface, profile

    @staticmethod
    def _load_curvewarp() -> bool:
        """Load the ``curveWarp`` plugin (ships with Maya). True if usable.

        The live open-polygon tube drives a baked mesh with a ``curveWarp``
        deformer; if the plugin can't load we fall back to the curved
        ``extrude -> nurbsToPoly`` build (which works but can flip normals on a
        live curve edit — see ``_conform_poly_outward``).
        """
        try:
            if not cmds.pluginInfo("curveWarp", query=True, loaded=True):
                cmds.loadPlugin("curveWarp", quiet=True)
            return bool(cmds.pluginInfo("curveWarp", query=True, loaded=True))
        except Exception:
            return False

    @classmethod
    def _finish_poly_tube(cls, mesh, sections, caps, quads, centerline, closed):
        """Cap the open ends, UV-seam (one lengthwise cut + a ring per cap),
        conform the normals outward, soften the body with hard cap rims, and
        triangulate when ``quads`` is off. Shared by the curved-extrude build
        and the straight-base (curveWarp) build — ``centerline`` is whichever
        path the mesh was swept along (the outward check is relative to it).
        """
        # Cap the open ends first (capturing the new cap faces so the seams are
        # exact for any section count), then let UvUtils place the UV seams:
        # one lengthwise cut plus each cap's ring, so the body unwraps to a strip
        # and the caps peel into their own UV shells.
        cap_faces = None
        if caps and not closed:
            n_before = cmds.polyEvaluate(mesh, face=True)
            cmds.polyCloseBorder(mesh, constructionHistory=True)
            cap_faces = list(range(n_before, cmds.polyEvaluate(mesh, face=True)))
        length_loop, cap_rings = UvUtils.get_cylinder_seam_edges(
            mesh, sections=sections, cap_faces=cap_faces
        )
        for seam in (length_loop, cap_rings):
            if seam:
                cmds.polyMapCut(seam, constructionHistory=True)
        cls._conform_poly_outward(mesh, centerline)
        # Smooth the body but keep the caps crisp: soften every edge, then
        # re-harden the cap rings so the tube shades smooth while the cap edges
        # read as hard.
        cmds.polySoftEdge(mesh, angle=180, constructionHistory=True)
        if cap_rings:
            cmds.polySoftEdge(cap_rings, angle=0, constructionHistory=True)
        if not quads:
            cmds.polyTriangulate(mesh, constructionHistory=True)
        return mesh

    @classmethod
    def _polygon_tube_warped(
        cls, driver, rdp_pts, radius, sections, caps, quads, live, throwaway, name
    ):
        """Open polygon tube built on a STRAIGHT base + a ``curveWarp`` deformer.

        The straight base is the same ``extrude -> nurbsToPoly`` mesh (so the
        topology — exact sides, RDP rings, caps, UV seams — is identical to the
        curved build), laid out along +X with its rings at the RDP points'
        cumulative-chord positions scaled to the driver's arc length. It is baked
        (history deleted) and then driven by a ``curveWarp`` whose input is the
        driver curve, so editing the curve only *repositions* vertices — the face
        winding (hence the normals) is fixed and can't flip. Baked builds
        additionally collapse the deformer into the mesh.
        """
        driver_shape = (
            cmds.listRelatives(driver, shapes=True, fullPath=True) or [driver]
        )[0]
        arclen = cmds.arclen(driver_shape) or 1.0
        # Rings at the RDP points' cumulative chord length, scaled to the driver
        # arc length so curveWarp maps each ring back to its curvature spot.
        cum = [0.0]
        for k in range(1, len(rdp_pts)):
            a, b = rdp_pts[k - 1], rdp_pts[k]
            cum.append(
                cum[-1]
                + ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5
            )
        scale = arclen / cum[-1] if cum[-1] else 1.0
        degree = max(1, min(3, len(rdp_pts) - 1))
        base_path = cmds.rename(
            cmds.curve(degree=degree, point=[(c * scale, 0, 0) for c in cum]),
            f"{name}_base#",
        )
        base_shape = cmds.listRelatives(base_path, shapes=True)[0]

        mesh, surface, profile = cls._extrude_poly_mesh(base_shape, radius, sections, name)
        cls._finish_poly_tube(
            mesh, sections, caps, quads,
            cls._sample_centerline(base_shape, cls._DENSE_SAMPLES), closed=False,
        )

        # Bake the straight base, drop the build inputs, then let ONLY the
        # deformer reposition the (fixed-winding) mesh.
        cmds.delete(mesh, constructionHistory=True)
        for node in (surface, profile, base_path):
            cls._safe_delete(node)
        deformer = cmds.deformer(mesh, type="curveWarp")[0]
        cmds.connectAttr(
            f"{driver_shape}.worldSpace[0]", f"{deformer}.inputCurve", force=True
        )

        if not live:
            cmds.delete(mesh, constructionHistory=True)  # collapse the warp
            for node in throwaway:
                cls._safe_delete(node)
        return mesh

    @staticmethod
    def _to_object_space(transform, world_pts):
        """Map ``world_pts`` into ``transform``'s object space.

        ``pointOnCurve`` returns world coordinates, but ``cmds.curve`` writes
        CVs in the transform's local space; converting through the world-inverse
        keeps an in-place resample from offsetting a transformed curve.
        """
        inv = om.MMatrix(
            cmds.xform(transform, query=True, matrix=True, worldSpace=True)
        ).inverse()
        return [tuple(om.MPoint(*p) * inv)[:3] for p in world_pts]

    @staticmethod
    def _sample_centerline(curve_shape, n=24):
        """Sample ``n`` points along the curve (for the outward-normal check)."""
        mn = cmds.getAttr(f"{curve_shape}.minValue")
        mx = cmds.getAttr(f"{curve_shape}.maxValue")
        return [
            cmds.pointOnCurve(curve_shape, pr=mn + (mx - mn) * i / (n - 1), position=True)
            for i in range(n)
        ]

    @classmethod
    def _conform_poly_outward(cls, mesh, centerline):
        """Flip the mesh normals if a sample of faces points inward.

        Each sampled face normal is compared to the radial direction from the
        nearest centerline point; an inward majority is reversed with
        ``polyNormal``. This reverse-or-not is a one-time decision frozen at
        build time, which is fine because every caller conforms a mesh whose
        orientation is then stable: the open-tube path conforms the **straight**
        base (a straight extrude can't flip) before the ``curveWarp`` deformer
        takes over — and a deformer only repositions vertices, so the winding
        never changes on a live edit. Only the curved-extrude fallback (closed
        torus / no curveWarp plugin) keeps a live tube whose swept-surface
        normal could still flip on a large edit (``cmds.extrude`` orientation is
        not edit-invariant); there, bake or re-run Conform Normals.
        """
        sel = om.MSelectionList()
        sel.add(mesh)
        dag = sel.getDagPath(0)
        dag.extendToShape()
        fn = om.MFnMesh(dag)
        pts = fn.getPoints(om.MSpace.kWorld)
        cl = [om.MVector(*p) for p in centerline]
        outward = inward = 0
        for i in range(min(fn.numPolygons, 24)):
            verts = fn.getPolygonVertices(i)
            fc = om.MVector(0, 0, 0)
            for vi in verts:
                fc += om.MVector(pts[vi])
            fc /= len(verts)
            radial = fc - min(cl, key=lambda c: (c - fc).length())
            if radial.length() < 1e-9:
                continue
            if fn.getPolygonNormal(i, om.MSpace.kWorld) * radial > 0:
                outward += 1
            else:
                inward += 1
        if inward > outward:
            cmds.polyNormal(
                mesh, normalMode=0, userNormalMode=0, constructionHistory=True
            )

    @staticmethod
    def _hide(node):
        """Hide a kept-but-internal history input."""
        try:
            cmds.setAttr(f"{node}.visibility", 0)
        except Exception:
            pass

    @classmethod
    def _bundle_under(cls, parent, *nodes):
        """Hide each (truthy) node and parent it under ``parent``.

        Live construction inputs (profile circle, knot-enriched path copy,
        NURBS surface) are kept but tucked under the result so they travel with
        / delete with it rather than cluttering the scene.
        """
        for node in nodes:
            if not node:
                continue
            cls._hide(node)
            try:
                cmds.parent(node, parent, relative=False)
            except Exception:
                pass

    @staticmethod
    def _conform_nurbs_outward(surf: str) -> None:
        """Reverse the NURBS surface if its normals face inward.

        Extrude emits either orientation depending on the curve's direction, so
        sample a mid-path ring's normal against the radial direction (from the
        ring centroid) and reverse the U direction — which flips the normal —
        when it points inward. (Reversing one direction rather than swapping
        U<->V keeps the parameterization conventional, U around / V along.)
        """
        try:
            n_samp = 12
            center = om.MVector()
            for k in range(n_samp):
                center += om.MVector(
                    *cmds.pointOnSurface(
                        surf, u=k / n_samp, v=0.5, turnOnPercentage=True, position=True
                    )
                )
            center /= n_samp
            p = om.MVector(
                *cmds.pointOnSurface(
                    surf, u=0.0, v=0.5, turnOnPercentage=True, position=True
                )
            )
            n = om.MVector(
                *cmds.pointOnSurface(
                    surf, u=0.0, v=0.5, turnOnPercentage=True, normal=True
                )
            )
            if n * (p - center) < 0:
                cmds.reverseSurface(
                    surf, direction=0, constructionHistory=False, replaceOriginal=True
                )
        except Exception:
            pass

    @staticmethod
    def _safe_delete(node: Optional[str]) -> None:
        if node and cmds.objExists(node):
            try:
                cmds.delete(node)
            except Exception:
                pass


class CurveToTubeSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the Curve to Tube UI (hermetic preview)."""

    # A live polygon tube resamples its source curve in place (RDP) -- tell the
    # Preview to snapshot the selected curve(s) so rollback restores them.
    PRESERVE_GEOMETRY = True

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.curve_to_tube
        self.last_tubes: List[str] = []

        # Output-type combo (NURBS / Polygon) drives which options apply.
        self.ui.cmb000.add(list(CurveToTube.OUTPUT_TYPES))
        # Polygon topology: quads vs triangles (was the "Quads" checkbox). Quads is
        # the default, matching the prior checked state.
        self.ui.cmb_topology.add([("Quads", "quads"), ("Triangles", "triangles")])
        self.ui.cmb_topology.setAsCurrent("quads")

        # Per-field reset buttons (uitk option-box): click resets a field to its
        # default; Alt/Ctrl+click bypasses it to default (greyed, restorable).
        # Must precede connect_multi/Preview — wrapping reparents the widgets and
        # invalidates any already-deferred wrapper (see add_reset_buttons docstring).
        self.sb.add_reset_buttons(self.ui)

        # Select Result is first-class in Preview: it (de)selects the tube(s)
        # on every preview build and on commit, and wires chk004 live.
        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            message_func=self.sb.message_box,
            validation_func=self._validate,
            select_result_checkbox=self.ui.chk004,
            result_provider=lambda: self.last_tubes,
        )
        # Re-sweep live as any numeric field changes.
        self.sb.connect_multi(self.ui, "s000-3", "valueChanged", self.preview.refresh)
        # Output type and the poly-only toggles also re-sweep; the combo also
        # enables/disables the options that don't apply to the current type.
        self.ui.cmb000.currentIndexChanged.connect(self.preview.refresh)
        self.ui.cmb000.currentIndexChanged.connect(self._toggle_output_options)
        self.ui.chk001.toggled.connect(self.preview.refresh)
        self.ui.cmb_topology.currentIndexChanged.connect(self.preview.refresh)
        self.ui.chk003.toggled.connect(self.preview.refresh)  # Live (keep history)

        self._toggle_output_options()

        # Footer doubles as a stats readout (triangle count, spans) once a
        # tube is built; show a hint until then.
        try:
            self.ui.footer.setDefaultStatusText("Select NURBS curve(s), then Preview.")
        except Exception:
            pass

    def header_init(self, widget):
        """Configure header help text."""
        widget.set_help_text(
            fmt(
                title="Curve to Tube",
                body="Sweep a circular profile along selected NURBS curves to "
                "build a tube, output as a NURBS surface or a polygon mesh.",
                steps=[
                    "Select one or more NURBS curves.",
                    "Pick an <b>Output</b> type (NURBS or Polygon).",
                    "Set <b>Radius</b> and <b>Sections</b> (sides around).",
                    "For polygon, set <b>Path Res</b> (ring density along the "
                    "curve) and toggle <b>Caps</b> / <b>Quads</b>.",
                    "Toggle <b>Preview</b> to iterate, then <b>Create</b> to "
                    "commit.",
                ],
                notes=[
                    "<b>Sections</b> sets the profile smoothness for NURBS and "
                    "the literal number of sides for polygon.",
                    "<b>Path Res</b> sets ring density along the path. Rings are "
                    "placed by an <i>RDP</i> simplification of the curve — dense "
                    "where it bends, sparse on straight runs; higher = finer. "
                    "<b>Path Res</b>, <b>Caps</b>, and <b>Quads</b> apply to "
                    "polygon output only.",
                    "<b>Keep History</b> retains the construction history so the "
                    "curve keeps driving the tube (edit the curve and it "
                    "updates). The topology is identical either way — it only "
                    "decides whether the history is kept.",
                    "<b>Select Result</b> selects the finished tube(s) on "
                    "<b>Create</b> so you can see the resulting tessellation.",
                ],
            )
        )

    def _toggle_output_options(self, *_):
        """Enable only the options that apply to the current output type."""
        is_poly = self.ui.cmb000.currentData() == "polygon"
        self.ui.s002.setEnabled(is_poly)  # Path divisions
        self.ui.chk001.setEnabled(is_poly)  # Caps
        self.ui.cmb_topology.setEnabled(is_poly)  # Quads/Triangles
        self.ui.s003.setEnabled(not is_poly)  # Degree (NURBS profile/surface)

    def _validate(self, objects) -> bool:
        """Preview gate: require at least one NURBS curve in the selection."""
        if not CurveToTube._curve_shapes(objects):
            self.sb.message_box("Select one or more NURBS curves.")
            return False
        return True

    def b001(self):
        """Reset to Defaults."""
        self.ui.state.reset_all()

    def perform_operation(self, objects, contract):
        """Build the tube(s) from the selected curves (Preview entry point).

        ``contract`` is the hermetic ``CleanupContract`` during the live preview
        and ``None`` on the commit replay. Select Result is applied by Preview
        itself (it owns the checkbox + ``result_provider``) after this build and
        on commit, so the toggle governs the live preview selection too.
        """
        self.last_tubes = CurveToTube.create(
            objects,
            output_type=self.ui.cmb000.currentData(),
            radius=self.ui.s000.value(),
            sections=self.ui.s001.value(),
            path_divisions=self.ui.s002.value(),
            degree=self.ui.s003.value(),
            caps=self.ui.chk001.isChecked(),
            quads=self.ui.cmb_topology.currentData() == "quads",
            live=self.ui.chk003.isChecked(),
        )
        self._update_footer()

    def _update_footer(self):
        """Show stats for the last build in the footer (triangle count for a
        polygon tube, spans for a NURBS one). Updates live as the preview
        re-sweeps; clears to the default hint when there is no result."""
        try:
            footer = self.ui.footer
        except Exception:
            return
        tubes = [t for t in self.last_tubes if t and cmds.objExists(t)]
        if not tubes:
            footer.setStatusText("")  # falls back to the default hint
            return
        prefix = f"{len(tubes)} tubes — " if len(tubes) > 1 else ""
        if self.ui.cmb000.currentData() == "polygon":
            tris = sum(cmds.polyEvaluate(t, triangle=True) or 0 for t in tubes)
            footer.setStatusText(f"{prefix}{tris:,} tris")
        else:
            spans = []
            for t in tubes:
                shp = (cmds.listRelatives(t, shapes=True, type="nurbsSurface") or [None])[0]
                if shp:
                    spans.append(
                        f"{cmds.getAttr(shp + '.spansU')}×{cmds.getAttr(shp + '.spansV')}"
                    )
            footer.setStatusText(f"{prefix}NURBS surface · {', '.join(spans)} spans")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("curve_to_tube", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
