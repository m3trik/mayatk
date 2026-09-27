# !/usr/bin/python
# coding=utf-8
"""Procedural draped-cloth (curtain) generator for Maya.

A curtain hangs from a *rail* — any polyline in world space, sampled from a
NURBS curve, a polygon edge path, a chain of locators, or a generated straight
(optionally bowed) rail. The cloth is pinned to the rail at evenly-spaced
**hanging points**; each hanging point is a pleat (the fabric gathers there),
and between consecutive points the fabric bellies into a fold and its top edge
sags under gravity along a real **catenary** (``y = a·cosh(x/a)`` — the curve a
cloth/cable assumes under its own weight). So the pleats define the hang points,
and the gaps between them fall with the gravity setting.

Responsibilities are deliberately split so each stays reusable on its own
(SRP):

- :class:`Rail` — *rail geometry*: generate, resolve-from-selection, sample,
  measure, and resample the polyline the cloth hangs from. No cloth, no rig.
- :class:`CurtainMesh` — *deformation*: drape a grid into the pleated, gravity-
  sagged cloth (the catenary math). Consumes plain rail points; knows nothing
  about how the rail was found or how it's later rigged.
- :class:`CurtainRig` — *rig*: make a curve drive a finished curtain via a wire
  **deformer** plus per-CV **cluster** controls. The deformer and the controls
  are separate steps (:meth:`CurtainRig._add_wire` /
  :meth:`CurtainRig._add_clusters`).

The panel, :class:`CurtainSlots` (``curtain_slots.py``), drives this engine
through the hermetic :class:`~mayatk.core_utils.preview.Preview` and a
built-in preset combo (``presets/``).
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError as error:
    cmds = None
    om = None
    print(__file__, error)

import pythontk as ptk

# from this package:
from mayatk.edit_utils._edit_utils import EditUtils
from mayatk.edit_utils.curtain._curtain_drape import CurtainDrape
from mayatk.edit_utils.naming._naming import Naming


# ----------------------------------------------------------------------------
# Math + drape engine. The reusable primitives live in ``ptk.MathUtils``, the
# generic polyline geometry in ``ptk.Polyline`` (``ptk.geo_utils.polyline``), and
# the rail→grid machinery in ``ptk.RailSurface`` (``ptk.geo_utils.rail_surface``).
# The curtain-specific drape is the vendored ``_curtain_drape.CurtainDrape``
# (code-identical with blendertk's copy — drift fails extapps'
# test_vendor_sync.py). This module keeps only the Maya halves: resolving a
# rail from a selection, building the mesh, and the wire rig.
# ----------------------------------------------------------------------------

Vec = Tuple[float, float, float]


# ----------------------------------------------------------------------------
# Rail geometry — ptk.Polyline + the Maya selection readers
# ----------------------------------------------------------------------------


class Rail(ptk.Polyline):
    """Rail-polyline geometry — the line a curtain hangs from.

    The pure parts (``make`` / ``length`` / ``resample`` / ``frames``) come
    from :class:`ptk.Polyline`; this subclass adds the Maya-only resolvers
    (selection / NURBS-curve sampling). The cloth engine (:class:`CurtainMesh`)
    and the rig (:class:`CurtainRig`) both consume its output but neither
    lives here.
    """

    @staticmethod
    def from_selection(objects) -> Optional[Tuple[List[Vec], bool]]:
        """Resolve a rail polyline from a Maya selection.

        Accepts (in priority order) polygon edges, a NURBS curve, or two-plus
        transforms (locators/joints). Returns ``(points, closed)`` or ``None``
        when nothing usable is selected.
        """
        flat = cmds.ls(objects, flatten=True, long=True) or []
        if not flat:
            return None

        edges = [o for o in flat if ".e[" in str(o)]
        if edges:
            verts = (
                cmds.ls(
                    cmds.polyListComponentConversion(
                        edges, fromEdge=True, toVertex=True
                    ),
                    flatten=True,
                )
                or []
            )
            pts = [tuple(cmds.pointPosition(v, world=True)) for v in verts]
            if len(pts) < 2:
                return None
            ordered = ptk.Polyline.order_points(pts)
            return ([tuple(float(c) for c in p) for p in ordered], False)

        for o in flat:
            shape = Rail._curve_shape(o)
            if shape:
                return Rail.sample_curve(shape)

        transforms = [
            o
            for o in flat
            if cmds.objExists(o) and cmds.objectType(o, isAType="transform")
        ]
        if len(transforms) >= 2:
            pts = [tuple(cmds.xform(t, q=True, ws=True, t=True)) for t in transforms]
            return ([tuple(float(c) for c in p) for p in pts], False)

        return None

    @staticmethod
    def _curve_shape(node) -> Optional[str]:
        """Return a ``nurbsCurve`` shape under (or equal to) *node*, else None."""
        if not cmds.objExists(node):
            return None
        if cmds.objectType(node) == "nurbsCurve":
            return node
        shapes = (
            cmds.listRelatives(node, shapes=True, fullPath=True, type="nurbsCurve")
            or []
        )
        return shapes[0] if shapes else None

    @staticmethod
    def sample_curve(shape: str, count: int = 200) -> Tuple[List[Vec], bool]:
        """Sample a NURBS curve into a dense polyline (resampled later by length)."""
        count = max(2, int(count))
        form = cmds.getAttr(f"{shape}.form")  # 0 open, 1 closed, 2 periodic
        closed = form in (1, 2)
        pts = [
            tuple(
                float(c)
                for c in cmds.pointOnCurve(shape, pr=i / (count - 1), top=True, p=True)
            )
            for i in range(count)
        ]
        return pts, closed


# ----------------------------------------------------------------------------
# Curtain generator — the vendored CurtainDrape engine (_curtain_drape) + the Maya mesh build
# ----------------------------------------------------------------------------


class CurtainMesh(CurtainDrape):
    """Generate a pleated, gravity-draped curtain mesh from a rail polyline.

    The drape math lives in :class:`CurtainDrape` (the vendored
    ``_curtain_drape`` twin — code-identical with blendertk's copy);
    this subclass adds the Maya *mesh build* (``polyPlane`` + ``MFnMesh`` +
    the shell/decimate/normal post-ops). It consumes plain rail points (see
    :class:`Rail`) and emits a mesh — it does not resolve the rail from a
    selection, nor rig the result; those are :class:`Rail` and
    :class:`CurtainRig`.

    Parameters:
        rail: Ordered world-space points the cloth hangs from (the rail).
        height: Drop of the curtain below the rail.
        hanging_points: Number of evenly-spaced pins (pleats) along the rail.
            Each is a pleat where the fabric gathers/attaches; the catenary sag
            and push-pull gather fire once per point — one clean pleat at the
            rail. Between consecutive points the fabric bellies into one full
            fold (``_BELLY_HUMPS_PER_SPAN`` half-sine humps) and sags, so the
            dialed count maps ~1:1 to the visible folds (you set roughly the fold
            count you want). ``2`` = a single span.
        hang_jitter: ``0``–``1`` — randomize the *spacing* of the hanging points
            along the rail (``0`` = evenly spaced). The outer ends (and a closed
            seam) stay pinned; only the interior points shift, so spans become
            uneven — wider gaps belly and sag further. ``hang_seed`` picks the
            pattern.
        hang_seed: RNG seed for the random hang-point spacing.
        gravity: How far the fabric falls between hanging points (the catenary
            sag depth, scaled by the span width — wider gaps fall further).
        tension: Catenary shape parameter for that sag (see
            :func:`catenary_shape`).
        round_points: ``0``–``1`` — round off the sharp cusp at each hanging
            point into a smooth dome (see :func:`sag_profile`).
        round_gather: ``≥0`` — *push-pull* gather at each hanging point: the
            fabric puckers **up** above the rail right at the point and **dips**
            just inside as the slack falls off (a gathered/pleated header),
            easing out by mid-span. Independent of ``round_points`` (``0`` =
            off).
        fullness: Drapery fullness ratio (≥1); drives fold/belly depth.
        taper: ``-1``–``1`` vertical bias of the fold depth — positive gathers
            the pleats at the top and flares them toward the free hem.
        mid_folds: Intensity of **V-folds** that fork down from the hang points
            (``0`` = off). Each apex sits on a seeded ~1/4–1/2 subset of the
            (interior) hang points and its two arms fan out and down into the
            neighbouring spans — some short, some running nearly to the hem —
            interrupting their plain in/out belly the way a heavy gathered drape
            forks below each hook. Each arm creases the cloth **out** at its line
            and **in** to either side (material-conserving), so the fold reads
            without ballooning the surface outward.
        mid_fold_seed: RNG seed for which hang points fork and each V's length /
            width / depth; the variation per seed is large, so it does most of
            the look's work.
        creases: Intensity of extra **V-shaped creases** that radiate down from
            random points near the top and run various lengths (``0`` = off).
            Evokes the diagonal break-lines of gathered fabric.
        crease_seed: RNG seed for the crease placement / length / depth.
        sway: **Lateral** fold lean — randomly leans a subset of the folds left
            or right *along the rail* (not just in/out), so pushed-in and -out
            areas drift sideways. Direction and amount per fold are random;
            pinned at the hang points and strongest toward the hem (``0`` = off).
        sway_seed: RNG seed for which folds sway and how far / which way.
        end_bend_left: Signed sideways bend applied to the left end of the
            curtain (e.g. a panel curling toward the camera); ``0`` = none.
        end_bend_right: Signed sideways bend applied to the right end.
        end_bend_falloff: ``0``–``1`` — fraction of the width over which each
            end bend ramps in from the edge.
        irregularity: Coherent, band-limited surface grain — a few smooth,
            zero-mean wave octaves (kept subtle; the deliberate folds come from
            fullness / mid_folds).
        density: Mesh resolution in segments per world unit.
        reduce: Percent (0–100) to decimate the result via ``polyReduce``
            (``0`` = none).
        thickness: Optional shell thickness (``0`` = single-sided cloth).
        invert: Reverse face normals (flip which side the cloth faces).
        soften: Soften mesh normals on build.
        closed: Treat the rail as a closed loop.
        name: Base name for the created transform.
    """

    def __init__(
        self,
        rail: Sequence[Vec],
        height: float = 3.0,
        hanging_points: int = 8,
        hang_jitter: float = 0.0,
        hang_seed: int = 0,
        gravity: float = 0.3,
        tension: float = 1.5,
        round_points: float = 0.0,
        round_gather: float = 0.0,
        fullness: float = 2.5,
        taper: float = 0.5,
        mid_folds: float = 0.0,
        mid_fold_seed: int = 0,
        creases: float = 0.0,
        crease_seed: int = 0,
        sway: float = 0.0,
        sway_seed: int = 0,
        end_bend_left: float = 0.0,
        end_bend_right: float = 0.0,
        end_bend_falloff: float = 0.25,
        irregularity: float = 0.15,
        density: float = 8.0,
        reduce: float = 0.0,
        thickness: float = 0.0,
        invert: bool = False,
        soften: bool = True,
        closed: bool = False,
        name: str = "curtain",
    ):
        if cmds is None:
            raise RuntimeError("CurtainMesh requires maya.cmds.")
        super().__init__(
            rail,
            height=height,
            hanging_points=hanging_points,
            hang_jitter=hang_jitter,
            hang_seed=hang_seed,
            gravity=gravity,
            tension=tension,
            round_points=round_points,
            round_gather=round_gather,
            fullness=fullness,
            taper=taper,
            mid_folds=mid_folds,
            mid_fold_seed=mid_fold_seed,
            creases=creases,
            crease_seed=crease_seed,
            sway=sway,
            sway_seed=sway_seed,
            end_bend_left=end_bend_left,
            end_bend_right=end_bend_right,
            end_bend_falloff=end_bend_falloff,
            irregularity=irregularity,
            density=density,
            reduce=reduce,
            thickness=thickness,
            invert=invert,
            soften=soften,
            closed=closed,
            name=name,
        )

    # Alias so callers can `CurtainMesh.create(rail, **opts)` in one line.
    @classmethod
    def create(cls, rail: Sequence[Vec], **opts) -> str:
        return cls(rail, **opts).build()

    def build(self) -> str:
        """Create the curtain mesh and return its transform name."""
        # Total length / resolution / rail frames / seeded feature sets — the
        # whole pure precompute lives in CurtainDrape.prepare().
        u_segs, v_segs, frames = self.prepare()

        plane = cmds.polyPlane(
            name=Naming.generate_unique_name(self.name),
            width=1.0,
            height=1.0,
            subdivisionsWidth=u_segs,
            subdivisionsHeight=v_segs,
            createUVs=2,
            constructionHistory=False,
        )[0]

        sel = om.MSelectionList()
        sel.add(plane)
        dag = sel.getDagPath(0)
        dag.extendToShape()
        mesh = om.MFnMesh(dag)
        pts = mesh.getPoints(om.MSpace.kObject)

        # Default polyPlane lies in XZ (width->X, length->Z); read (u, v) from
        # that regular grid and re-emit each vertex draped on the rail.
        xs = [p.x for p in pts]
        zs = [p.z for p in pts]
        xmin, xmax = min(xs), max(xs)
        zmin, zmax = min(zs), max(zs)
        w = (xmax - xmin) or 1.0
        h = (zmax - zmin) or 1.0

        for i in range(len(pts)):
            p = pts[i]
            u = (p.x - xmin) / w
            v = (p.z - zmin) / h
            col = max(0, min(u_segs, int(round(u * u_segs))))
            pos, tan, normal = frames[col]
            pts[i] = om.MPoint(*self.drape(u, v, pos, tan, normal))

        # No undo recording needed: the plane comes from a recorded polyPlane,
        # so an undo takes it away and a redo puts the same node back, drape
        # and all (measured with the undo recorder off).
        mesh.setPoints(pts, om.MSpace.kObject)
        mesh.updateSurface()

        # Shell, decimate, flip, then soften last so normals cover the result.
        if self.thickness > 0:
            cmds.polyExtrudeFacet(
                f"{plane}.f[*]", localTranslateZ=self.thickness, keepFacesTogether=True
            )
            cmds.delete(plane, constructionHistory=True)
        if self.reduce > 0:
            EditUtils.decimate([plane], percentage=self.reduce)
        if self.invert:
            cmds.polyNormal(
                plane, normalMode=0, userNormalMode=0, constructionHistory=False
            )
        if self.soften:
            cmds.polySoftEdge(plane, angle=180, constructionHistory=False)
        return plane


# ----------------------------------------------------------------------------
# Rig (wire deformer + cluster controls)
# ----------------------------------------------------------------------------


class CurtainRig:
    """Make a curve drive a finished curtain.

    Kept apart from the cloth *deformation* (:class:`CurtainMesh`) so the rig
    can be applied to any mesh and any driver curve. The **deformer** (a wire)
    and the **controls** (a cluster per CV) are separate steps; :meth:`attach`
    only orchestrates them and groups the result.
    """

    @staticmethod
    def attach(curtain: str, curve: str, dropoff: float, cluster: bool = True) -> str:
        """Wire-deform *curtain* with *curve* and add per-CV cluster controls.

        ``dropoff`` is how far the curve's pull reaches into the drop. Returns
        the rig group (curtain + driver + hidden base wire + any clusters).
        """
        _wire_node, base = CurtainRig._add_wire(curtain, curve, dropoff)
        members = [curtain, curve]
        if base:
            members.append(base)
        if cluster:
            members.extend(CurtainRig._add_clusters(curve))
        return cmds.group(members, name=f"{curtain}_rig")

    @staticmethod
    def _add_wire(
        curtain: str, curve: str, dropoff: float
    ) -> Tuple[str, Optional[str]]:
        """Deformer step: bind *curve* to *curtain* as a wire; hide the base wire.

        Returns ``(wire_node, base_transform_or_None)``. ``listConnections``
        returns the base wire's *transform* (not its shape) by default, which
        is what we hide and group.
        """
        wire_node = cmds.wire(
            curtain,
            wire=curve,
            groupWithBase=False,
            dropoffDistance=[(0, float(dropoff))],
        )[0]
        base = (cmds.listConnections(f"{wire_node}.baseWire[0]") or [None])[0]
        if base:
            try:
                cmds.setAttr(f"{base}.visibility", 0)
            except Exception:
                pass
        return wire_node, base

    @staticmethod
    def _add_clusters(curve: str) -> List[str]:
        """Rig step: a draggable cluster handle per curve CV. Returns the handles."""
        handles: List[str] = []
        for i, cv in enumerate(cmds.ls(f"{curve}.cv[*]", flatten=True) or []):
            handles.append(cmds.cluster(cv, name=f"{curve}_ctrl_{i}_cluster")[1])
        return handles
