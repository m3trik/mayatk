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
curve (:func:`pythontk.MathUtils.simplify_rdp`): the path is taken *through* the
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
        :func:`pythontk.MathUtils.simplify_rdp`, which keeps points where the
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
        """
        closed = cmds.getAttr(f"{curve_shape}.form") in (1, 2)  # 1 closed, 2 periodic
        dense = cls._sample_centerline(curve_shape, cls._DENSE_SAMPLES)
        length = cmds.arclen(curve_shape) or 1.0
        tol = length * cls._RDP_TOLERANCE / path_divisions
        keep = set(ptk.MathUtils.simplify_rdp(dense, tol))
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
            cmds.curve(path, replace=True, degree=degree, point=rdp_pts)
            throwaway = []
        else:
            # Baked: a throwaway path through the points; source curve untouched.
            path = cmds.rename(cmds.curve(degree=degree, point=rdp_pts), f"{name}_path#")
            throwaway = [path]
        if closed:
            cmds.closeCurve(
                path, constructionHistory=False, replaceOriginal=True, preserveShape=0
            )

        surface, profile = cls._extrude_surface(path, radius, sections, 3, name)
        # uType=2 -> exactly `sections` segments around; vType=1 -> one ring per
        # surface V-span (i.e. at each kept point). format=3 keeps it clean.
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
        mesh = cmds.rename(mesh, f"{name}#")

        # Cap the open ends first (capturing the new cap faces so the seams are
        # exact for any section count), then let UvUtils place the UV seams:
        # one lengthwise cut plus each cap's ring, so the body unwraps to a
        # strip and the caps peel into their own UV shells.
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
        cls._conform_poly_outward(mesh, dense)
        # Smooth the body but keep the caps crisp: soften every edge, then
        # re-harden the cap rings so the tube shades smooth while the cap edges
        # read as hard. Kept in history so a live tube stays correct on edits.
        cmds.polySoftEdge(mesh, angle=180, constructionHistory=True)
        if cap_rings:
            cmds.polySoftEdge(cap_rings, angle=0, constructionHistory=True)
        if not quads:
            cmds.polyTriangulate(mesh, constructionHistory=True)

        if live:
            # Bundle the hidden construction inputs under the tube; the resampled
            # source curve stays separate as the visible, editable driver.
            cls._bundle_under(mesh, surface, profile)
        else:
            cmds.delete(mesh, constructionHistory=True)  # bake to a plain mesh
            for node in [surface, profile] + throwaway:
                cls._safe_delete(node)
        return mesh

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
        ``polyNormal``. NOTE: this reverse-or-not is a one-time decision frozen
        at build time. For a LIVE tube it is correct at build, but a large
        control-curve edit can globally flip ``cmds.extrude``'s swept-surface
        normal (an intrinsic extrude instability — no extrude option or baked
        ``polyNormal`` mode prevents it), which ``nurbsToPoly`` and this node
        then faithfully propagate, inverting the mesh. See the characterization
        test ``test_live_polygon_normals_invert_on_curve_edit_is_extrude_limitation``.
        Mitigation: bake (Keep History off) for the final mesh, or re-run
        Conform Normals after editing.
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

        # No finalize_func: the result selection is applied inside
        # perform_operation on the commit replay (contract is None) so it is the
        # operation's final action and can't be lost to a separate callback.
        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            message_func=self.sb.message_box,
            validation_func=self._validate,
        )
        # Re-sweep live as any numeric field changes.
        self.sb.connect_multi(self.ui, "s000-3", "valueChanged", self.preview.refresh)
        # Output type and the poly-only toggles also re-sweep; the combo also
        # enables/disables the options that don't apply to the current type.
        self.ui.cmb000.currentIndexChanged.connect(self.preview.refresh)
        self.ui.cmb000.currentIndexChanged.connect(self._toggle_output_options)
        self.ui.chk001.toggled.connect(self.preview.refresh)
        self.ui.chk002.toggled.connect(self.preview.refresh)
        self.ui.chk003.toggled.connect(self.preview.refresh)  # Live (keep history)
        # Select Result is applied at commit AND live — toggling it immediately
        # (de)selects the current result. It must NOT trigger a re-sweep, so it
        # is wired to the selection apply, not to refresh.
        self.ui.chk004.toggled.connect(self._apply_result_selection)

        self._toggle_output_options()

        # Footer doubles as a stats readout (tri / vert counts, spans) once a
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
        self.ui.chk002.setEnabled(is_poly)  # Quads
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
        and ``None`` on the commit replay. The Select Result choice is applied
        as the final action of **every** build — preview AND commit — so the
        toggle governs the live preview selection too, not only the committed
        result (``create`` always leaves its result selected to clear the seam
        components, which would otherwise ignore the toggle during preview). On
        commit it is also the last thing in the committed chunk, immune to the
        operation's own leftover selection (NURBS extrude leaves the surface
        selected; a baked polygon leaves nothing). The idle re-assert is needed
        only on commit (see ``_apply_result_selection``); Curve to Tube installs
        no selection scriptJob, so a preview-time (de)select can't trigger a
        rebuild.
        """
        self.last_tubes = CurveToTube.create(
            objects,
            output_type=self.ui.cmb000.currentData(),
            radius=self.ui.s000.value(),
            sections=self.ui.s001.value(),
            path_divisions=self.ui.s002.value(),
            degree=self.ui.s003.value(),
            caps=self.ui.chk001.isChecked(),
            quads=self.ui.chk002.isChecked(),
            live=self.ui.chk003.isChecked(),
        )
        self._update_footer()
        self._apply_result_selection(defer=contract is None)

    def _update_footer(self):
        """Show stats for the last build in the footer (tri / vert counts for a
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
            faces = sum(cmds.polyEvaluate(t, face=True) or 0 for t in tubes)
            verts = sum(cmds.polyEvaluate(t, vertex=True) or 0 for t in tubes)
            footer.setStatusText(
                f"{prefix}{tris:,} tris · {faces:,} faces · {verts:,} verts"
            )
        else:
            spans = []
            for t in tubes:
                shp = (cmds.listRelatives(t, shapes=True, type="nurbsSurface") or [None])[0]
                if shp:
                    spans.append(
                        f"{cmds.getAttr(shp + '.spansU')}×{cmds.getAttr(shp + '.spansV')}"
                    )
            footer.setStatusText(f"{prefix}NURBS surface · {', '.join(spans)} spans")

    def _apply_result_selection(self, *args, defer=False):
        """Select the last-created tube(s) — or explicitly deselect them.

        With *Select Result* (``chk004``) on, the tube(s) are selected so the
        user can see the resulting tessellation; with it off, they are
        explicitly **deselected** (the build can leave the new mesh selected, so
        "off" must actively clear it). The ``chk004`` read is defensive so a UI
        without the widget falls back to selecting (the prior behavior).

        Called three ways: on every preview build and on commit (both from
        ``perform_operation`` — ``defer=True`` only on commit) and live from
        ``chk004.toggled`` so toggling the option immediately (de)selects the
        current result. ``*args`` absorbs the signal's ``bool``.

        The ``chk004`` value is re-read inside ``_apply`` on every invocation
        (not snapshotted once up front). On the commit path the immediate apply
        runs synchronously inside the undo chunk, but in interactive Maya the
        marking-menu panel's state restore can leave ``chk004`` still reporting
        its ``.ui`` default at that instant and only settle to the saved value
        slightly later — so the FIRST commit from a saved "off" wrongly
        selected. Re-reading on the deferred idle re-assert lets the settled
        value govern the final selection (the deferred read wins).

        Parameters:
            defer (bool): Re-assert the choice on idle (``evalDeferred``). Only
                the commit path needs it — committing can leave Maya restoring
                the pre-op selection or switching to a component mode *after*
                this returns, and (per above) the toggle's logical value may not
                have settled yet. A live toggle is a direct user action with
                nothing to clobber it, and deferring there would let an idle
                ``SelectionChanged -> refresh`` rebuild undo the toggle.
        """
        tubes = list(self.last_tubes)

        def _apply():
            alive = [t for t in tubes if t and cmds.objExists(t)]
            if not alive:
                return
            # Read the toggle at apply time so the deferred idle re-assert
            # reflects the settled UI value, not a possibly-stale commit-time
            # snapshot. Defensive so a UI without the widget falls back to
            # selecting (the prior behavior).
            try:
                select = self.ui.chk004.isChecked()
            except Exception:
                select = True
            # The build leaves the result selected in object mode (create's
            # _reset_object_selection); force object mode defensively so the
            # deselect operates on the object, not lingering seam components.
            try:
                cmds.selectMode(object=True)
            except Exception:
                pass
            if select:
                cmds.select(alive, replace=True)
            else:
                cmds.select(alive, deselect=True)

        _apply()  # immediate
        if defer:
            try:
                cmds.evalDeferred(_apply, lowestPriority=True)
            except Exception:
                pass


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("curve_to_tube", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
