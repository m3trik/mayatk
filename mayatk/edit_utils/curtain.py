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
- :class:`CurtainSlots` — *UI wiring*: drives the engine through the hermetic
  :class:`~mayatk.core_utils.preview.Preview` and a built-in preset combo.
"""
from __future__ import annotations

import bisect
import math
import random
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

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
from mayatk.core_utils._core_utils import BoundingBox
from mayatk.core_utils.preview import Preview
from mayatk.edit_utils._edit_utils import EditUtils
from mayatk.edit_utils.naming._naming import Naming

# Shipped, read-only presets (loaded via PresetManager's built-in tier).
_PRESETS_DIR = Path(__file__).resolve().parent / "presets" / "curtain"


# ----------------------------------------------------------------------------
# Math helpers. The reusable primitives — vector lerp, zero-guarded normalize,
# the clamped smoothstep ease, the Ricker fold wavelet, and the catenary sag
# profiles — now live in ``ptk.MathUtils`` (the ecosystem SSoT). These are thin
# aliases so the drape code below reads unchanged; ``catenary_shape`` /
# ``sag_profile`` stay importable for back-compat. ``_v_arms`` (the two-armed
# fold V) is a curtain-specific composition and stays local.
# ----------------------------------------------------------------------------

Vec = Tuple[float, float, float]

_lerp = ptk.MathUtils.lerp                # point/vector lerp
_unit = ptk.MathUtils.safe_normalize      # normalize with a degenerate fallback
_smoothstep = ptk.MathUtils.smoothstep    # clamped Hermite ease
catenary_shape = ptk.MathUtils.catenary   # back-compat re-export
sag_profile = ptk.MathUtils.catenary_sag  # back-compat re-export


def _v_arms(u: float, u0: float, spread: float, depth: float, half_width: float) -> float:
    """Sum of the two **mean-preserving** arms of a downward **V** apexed at ``u0``.

    Each arm is a Ricker wavelet (:meth:`ptk.MathUtils.ricker` — a ridge with
    flanking troughs), so a fold reads as in/out undulation rather than a
    one-sided bulge. The arms coincide at the apex (``depth == 0``) and fan
    symmetrically to ``u0 ± spread·depth`` as the V runs down (``depth`` =
    distance below the apex); ``half_width`` sets each arm's width. Shared by the
    surface creases and the mid-fold forks.
    """
    ricker = ptk.MathUtils.ricker
    return ricker((u - (u0 - spread * depth)) / half_width) + ricker(
        (u - (u0 + spread * depth)) / half_width
    )


# ----------------------------------------------------------------------------
# Rail geometry (generate / resolve / sample / measure / resample)
# ----------------------------------------------------------------------------


class Rail:
    """Pure rail-polyline geometry — the line a curtain hangs from.

    Everything here is a stateless ``staticmethod`` returning plain values
    (lists of points, floats), so it composes freely and is unit-testable
    without building a curtain. The cloth engine (:class:`CurtainMesh`) and the
    rig (:class:`CurtainRig`) both consume its output but neither lives here.
    """

    @staticmethod
    def make(
        width: float = 6.0,
        curvature: float = 0.0,
        segments: int = 24,
        closed: bool = False,
        center: Vec = (0.0, 0.0, 0.0),
    ) -> Tuple[List[Vec], bool]:
        """Build a default rail: a straight line of ``width`` (``curvature == 0``).

        ``curvature`` (-1..1) bows the rail by a parabola — positive forward in
        +Z, negative back in -Z — so the default is flat and bowing is opt-in.
        ``closed`` makes a ring instead. ``center`` (x, y, z) is where the rail
        is centered — the straight line's midpoint / the ring's center (default
        origin).
        """
        segments = max(2, int(segments))
        cx, cy, cz = (float(c) for c in center)
        pts: List[Vec] = []
        if closed:
            r = width / 2.0
            for i in range(segments):
                a = (i / segments) * 2.0 * math.pi
                pts.append((cx + r * math.sin(a), cy, cz + r * math.cos(a)))
        else:
            bow = curvature * width * 0.5
            for i in range(segments + 1):
                f = i / segments
                pts.append(
                    (
                        cx + (f - 0.5) * width,
                        cy,
                        cz + bow * (1.0 - (2.0 * f - 1.0) ** 2),
                    )
                )
        return pts, closed

    @staticmethod
    def from_selection(objects) -> Optional[Tuple[List[Vec], bool]]:
        """Resolve a rail polyline from a Maya selection.

        Accepts (in priority order) polygon edges, a NURBS curve, or two-plus
        transforms (locators/joints). Returns ``(points, closed)`` or ``None``
        when nothing usable is selected.
        """
        flat = cmds.ls(objects, flatten=True) or []
        if not flat:
            return None

        edges = [o for o in flat if ".e[" in str(o)]
        if edges:
            verts = cmds.ls(
                cmds.polyListComponentConversion(edges, fromEdge=True, toVertex=True),
                flatten=True,
            ) or []
            pts = [tuple(cmds.pointPosition(v, world=True)) for v in verts]
            if len(pts) < 2:
                return None
            ordered = ptk.arrange_points_as_path(pts)
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

    @staticmethod
    def length(points: Sequence[Vec], closed: bool) -> float:
        """Total arc length of the polyline (wrapping last->first if closed)."""
        dist = ptk.MathUtils.distance_between_points
        total = sum(dist(points[i - 1], points[i]) for i in range(1, len(points)))
        if closed:
            total += dist(points[-1], points[0])
        return total

    @staticmethod
    def resample(points: Sequence[Vec], count: int) -> List[Vec]:
        """Resample to *count* evenly-spaced points (ecosystem SSoT helper)."""
        pts = ptk.dist_points_along_centerline(
            [list(p) for p in points], max(2, int(count))
        )
        return [(float(p[0]), float(p[1]), float(p[2])) for p in pts]

    @staticmethod
    def frames(
        points: Sequence[Vec], u_segs: int, closed: bool
    ) -> List[Tuple[Vec, Vec, Vec]]:
        """Resample the rail to ``u_segs + 1`` even points with local frames.

        Each frame is ``(position, tangent, horizontal_normal)``; the normal is
        the in-plane perpendicular the folds bow along.
        """
        dist = ptk.MathUtils.distance_between_points
        pts = list(points)
        if closed and dist(pts[-1], pts[0]) > 1e-6:
            pts = pts + [pts[0]]

        cum = [0.0]
        for i in range(1, len(pts)):
            cum.append(cum[-1] + dist(pts[i - 1], pts[i]))
        total = cum[-1]
        up = (0.0, 1.0, 0.0)

        def sample(s: float) -> Vec:
            s = max(0.0, min(total, s))
            for i in range(1, len(cum)):
                if s <= cum[i] or i == len(cum) - 1:
                    span = cum[i] - cum[i - 1]
                    t = (s - cum[i - 1]) / span if span > 1e-9 else 0.0
                    return _lerp(pts[i - 1], pts[i], t)
            return pts[-1]

        frames: List[Tuple[Vec, Vec, Vec]] = []
        eps = max(total * 1e-3, 1e-5)
        for c in range(u_segs + 1):
            s = (c / u_segs) * total if total > 0 else 0.0
            pos = sample(s)
            # get_vector_from_two_points(a, b) -> b - a, so this is the forward
            # tangent; _unit guards the degenerate (vertical / coincident) case.
            tan = _unit(
                ptk.MathUtils.get_vector_from_two_points(
                    sample(s - eps), sample(s + eps)
                ),
                (0.0, 0.0, 1.0),
            )
            normal = _unit(ptk.MathUtils.cross_product(up, tan), (1.0, 0.0, 0.0))
            frames.append((pos, tan, normal))
        return frames


# ----------------------------------------------------------------------------
# Deformation engine (the procedural cloth drape)
# ----------------------------------------------------------------------------


class CurtainMesh(ptk.LoggingMixin):
    """Generate a pleated, gravity-draped curtain mesh from a rail polyline.

    Pure *deformation*: it consumes plain rail points (see :class:`Rail`) and
    emits a mesh. It does not resolve the rail from a selection, nor rig the
    result — those are :class:`Rail` and :class:`CurtainRig`.

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
        rail = [tuple(float(c) for c in p) for p in rail]
        if len(rail) < 2:
            raise ValueError("rail must contain at least two points.")

        self.rail = rail
        self.height = float(height)
        self.hanging_points = max(2, int(hanging_points))
        self.hang_jitter = max(0.0, min(1.0, float(hang_jitter)))
        self.hang_seed = int(hang_seed)
        self.gravity = float(gravity)
        self.tension = float(tension)
        self.round_points = max(0.0, min(1.0, float(round_points)))
        self.round_gather = max(0.0, float(round_gather))
        self.fullness = max(1.0, float(fullness))
        self.taper = float(taper)
        self.mid_folds = max(0.0, float(mid_folds))
        self.mid_fold_seed = int(mid_fold_seed)
        self.creases = max(0.0, float(creases))
        self.crease_seed = int(crease_seed)
        self.sway = max(0.0, float(sway))
        self.sway_seed = int(sway_seed)
        self.end_bend_left = float(end_bend_left)
        self.end_bend_right = float(end_bend_right)
        self.end_bend_falloff = max(1e-4, min(1.0, float(end_bend_falloff)))
        self.irregularity = float(irregularity)
        self.density = max(0.1, float(density))
        self.reduce = float(reduce)
        self.thickness = float(thickness)
        self.invert = bool(invert)
        self.soften = bool(soften)
        self.closed = bool(closed)
        self.name = name

        # Spans between hanging points (a closed loop wraps, so one more span).
        self.spans = (
            self.hanging_points if self.closed else max(self.hanging_points - 1, 1)
        )
        # u-positions (0..1) of the hanging points along the rail — evenly spaced
        # unless hang_jitter randomizes the interior spacing. Param-derived, so
        # computed here (always available to the offset helpers, even standalone).
        self._hang_points = self._make_hang_points()

    # Alias so callers can `CurtainMesh.create(rail, **opts)` in one line.
    @classmethod
    def create(cls, rail: Sequence[Vec], **opts) -> str:
        return cls(rail, **opts).build()

    def build(self) -> str:
        """Create the curtain mesh and return its transform name."""
        self._total_length = Rail.length(self.rail, self.closed)
        u_segs, v_segs = self._resolve_resolution()
        frames = Rail.frames(self.rail, u_segs, self.closed)

        # Fixed-seed base RNG for the always-on subtle variation (the band-
        # limited grain and per-span depth jitter): deliberately subtle, so a
        # user-facing seed for it never earned its keep — the seeds that matter
        # are the per-feature ones (mid_fold_seed / crease_seed).
        rng = random.Random(self._BASE_SEED)
        # Per-span depth variation so the folds aren't mechanically identical.
        self._span_jitter = [rng.uniform(0.8, 1.2) for _ in range(self.spans)]
        self._creases = self._make_creases()
        self._midfolds = self._make_midfolds()
        self._sway = self._make_sway()
        self._billow = self._make_billow()

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
            pts[i] = om.MPoint(*self._drape(u, v, pos, tan, normal))

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

    # ------------------------------------------------------------- internals

    def _make_hang_points(self) -> List[float]:
        """u-positions (0..1) of the hanging points, ``spans + 1`` of them.

        Evenly spaced unless ``hang_jitter`` perturbs the interior spacing. The
        ends (``0`` and ``1`` — the outer pins, or a closed rail's seam) stay
        pinned so the cloth still spans the full rail. Each interior point shifts
        by at most ``0.4`` of a span, so the points stay strictly ordered (no
        crossed/zero-width spans) at any jitter.
        """
        n = self.spans
        pts = [i / n for i in range(n + 1)]
        if self.hang_jitter > 0.0:
            rng = random.Random(self.hang_seed)
            span_u = 1.0 / n
            for i in range(1, n):  # interior points only
                pts[i] += rng.uniform(-1.0, 1.0) * self.hang_jitter * 0.4 * span_u
        return pts

    def _span_at(self, u: float) -> Tuple[int, float]:
        """Map a rail position ``u`` (0..1) to its ``(span index, local t)``.

        ``t`` is 0..1 within the (possibly uneven) span; ``span index + t`` is
        the old uniform ``u * spans`` phase, so the belly/sway half-sines stay
        zero at every (now uneven) hang point.
        """
        hp = self._hang_points
        k = max(0, min(bisect.bisect_right(hp, u) - 1, self.spans - 1))
        w = hp[k + 1] - hp[k]
        return k, (u - hp[k]) / w if w > 0.0 else 0.0

    def _drape(self, u, v, pos, tan, normal) -> Vec:
        """Place one cloth vertex.

        ``u`` runs along the rail (0..1), ``v`` runs vertically (0 = hem,
        1 = rail). The fabric is pinned at each hanging point (``belly`` and
        ``sag`` both vanish there) and, between points, bellies into an
        alternating fold (``normal`` direction) while its whole strip sags down
        a catenary under gravity. ``sway`` additionally leans a fold sideways
        along ``tan`` (the in-plane rail tangent).
        """
        k, t = self._span_at(u)             # span index + local 0..1
        phase = k + t                       # integers = hang points

        # Fold belly: ``_BELLY_HUMPS_PER_SPAN`` half-sine humps per pleat-span (2
        # = one out-bulge + one in-recess = a full fold), zero at every hang
        # point *and* at the in/out crossover inside the span — so one dialed
        # hang point reads as one pleat with a full out-and-in fold between it
        # and the next (instead of a single half-hump per span). taper deepens it
        # toward the hem and gathers (shallows) it at the top.
        depth = (
            0.15
            * (self.fullness - 1.0)
            * (1.0 + self.taper * (1.0 - 2.0 * v))
            * self._span_jitter[k]
        )
        belly = depth * math.sin(math.pi * self._BELLY_HUMPS_PER_SPAN * phase)

        # Kept subtle (the deliberate folds come from fullness / mid_folds):
        # coherent, band-limited surface grain built from zero-mean waves, so it
        # ripples the cloth in and out rather than puffing it one way.
        irr = self.irregularity * 0.2 * self._billow_offset(u, v)
        # All sideways shaping rides the in-plane normal: belly fold, billow,
        # the mid-fold forks, the V-creases, and the per-end bend.
        offset = (
            belly
            + irr
            + self._midfold_offset(u, v)
            + self._crease_offset(u, v)
            + self._end_bend_offset(u)
        )

        # Lateral lean rides the in-plane tangent (along the rail) — the random
        # left/right drift of a fold, distinct from the in/out `offset`.
        lateral = self._sway_offset(u, v)
        x = pos[0] + normal[0] * offset + tan[0] * lateral
        z = pos[2] + normal[2] * offset + tan[2] * lateral

        # Gravity: the span between two hang points sags along a catenary
        # (optionally rounded and/or push-pull gathered at the pins); the whole
        # vertical strip drops with its (sagged) top edge. The span's *own* width
        # scales the sag, so a wider (jittered) gap falls further. Dividing by
        # ``_BELLY_HUMPS_PER_SPAN`` normalizes that to the per-hump width: the
        # catenary and its push-pull gather fire once per pleat (one clean cusp
        # at the rail — a smoother header) at the same depth as before, so
        # halving the dialed hang points (each span now that much wider)
        # reproduces the old sag rather than ballooning it. The 0.5 calibrates
        # the gather dial so a full slider lifts the pin ~half a sag-unit
        # (matches the other dials).
        sag_width = (
            (self._hang_points[k + 1] - self._hang_points[k])
            * self._total_length
            / self._BELLY_HUMPS_PER_SPAN
        )
        sag = self.gravity * sag_width * sag_profile(
            2.0 * t - 1.0, self.tension, self.round_points, self.round_gather * 0.5
        )
        y = pos[1] - sag - (1.0 - v) * self.height
        return (x, y, z)

    # Belly half-sine humps per pleat-span (the run between two consecutive hang
    # points): ``2`` = one out-bulge + one in-recess = a single full fold per
    # span. Decouples the *body* fold density from the *top* gather frequency —
    # the catenary sag + push-pull gather fire once per hang point (one clean
    # pleat at the rail), while the belly runs at this many humps, so the dialed
    # hang-point count maps ~1:1 to the visible folds. ``2`` gives the previous
    # look at half the dialed points (and half the top cusps). The same factor
    # rescales the sag (per-hump width) and the mesh resolution (cols per hump).
    _BELLY_HUMPS_PER_SPAN = 2

    # Fixed seed for the always-on subtle grain + per-span depth jitter (the
    # tunable seeds are the per-feature mid_fold_seed / crease_seed).
    _BASE_SEED = 0
    # Crease half-width in u (narrow enough to read as a fold, wide enough that
    # a normal-density mesh resolves it without aliasing).
    _CREASE_WIDTH = 0.05
    # Peak of the spindle profile x^0.6·(1-x)^1.3 (at x = 0.6/1.9); the profile
    # is divided by this so its crest is 1, keeping the creases slider's
    # strength independent of the exact profile exponents.
    _CREASE_PEAK = 0.3058

    def _make_creases(self):
        """Seeded set of V-creases: ``(u0, length, amp, spread)`` per crease.

        ``u0`` is the apex (top) position, ``length`` how far down it runs,
        ``amp`` its *signed* depth — so creases push out **and** in, reading as
        a mix of ridges and valleys rather than bumps all on one side —, and
        ``spread`` how far the two arms diverge (each crease draws a downward
        **V** of a different length). Count scales with the number of spans;
        empty when ``creases`` is off.
        """
        if self.creases <= 0.0:
            return []
        rng = random.Random(self.crease_seed)
        n = max(3, round(self.spans * 2.5))
        return [
            (
                rng.uniform(0.04, 0.96),                          # u0 — apex position
                rng.uniform(0.3, 1.0),                            # length — fraction of the drop
                rng.choice((-1.0, 1.0)) * rng.uniform(0.4, 1.0),  # amp — signed ridge/valley
                rng.uniform(0.02, 0.10),                          # spread — V arm divergence
            )
            for _ in range(n)
        ]

    def _crease_offset(self, u: float, v: float) -> float:
        """Summed V-crease displacement at ``(u, v)`` (0 when creases off)."""
        if not self._creases:
            return 0.0
        depth_from_top = 1.0 - v  # 0 at the rail, 1 at the hem
        # A crease's gaussians vanish (``~e⁻²⁵``) past this much |u - u0|; skip
        # those so a dense live re-drape isn't O(verts × creases) ``exp`` calls.
        band_tail = 5.0 * self._CREASE_WIDTH
        total = 0.0
        for u0, length, amp, spread in self._creases:
            if depth_from_top > length:
                continue  # this crease has already petered out above the vertex
            if abs(u - u0) > spread + band_tail:
                continue  # the vertex is outside this crease's u-band
            # Spindle profile along the crease: fades in just below the rail
            # (so the pinned top edge stays put), crests in the upper third,
            # and tapers to nothing at the tip. ``> length`` guard above keeps
            # ``1 - x`` non-negative for the fractional power.
            x = depth_from_top / length
            fall = (x ** 0.6) * ((1.0 - x) ** 1.3) / self._CREASE_PEAK
            total += amp * fall * _v_arms(
                u, u0, spread, depth_from_top, self._CREASE_WIDTH
            )
        return self.creases * 0.15 * total

    # Mid-fold vertical fades. The fork ramps in over a *small* top fade so it
    # reads nearly to the rail (only the pinned rail row v=1 itself stays put)
    # and out over a wider tip fade at its lower end, holding full strength
    # between — so a long fork reads from just under the hooks all the way down.
    _MIDFOLD_TOP_FADE = 0.04
    _MIDFOLD_FADE = 0.12  # tip fade-out width

    def _make_midfolds(self):
        """Seeded mid-folds: downward **V** forks anchored at the hang points.

        Returns ``(u0, length, amp, spread, half_width)`` per fork. Unlike the
        belly (one in/out bulge per span) these apex *on* a hang point (``u0``)
        and fan **out and down** into the neighbouring spans, interrupting that
        plain in/out cycle the way a heavy gathered drape forks below each hook.
        Only a random ~1/4–1/2 of the (interior) hang points are chosen; lengths
        vary (some stop high, some run nearly to the hem) as do width/depth
        (narrow-deep vs wide-shallow). Empty when ``mid_folds`` is off.
        """
        if self.mid_folds <= 0.0:
            return []
        # A fork at the very end would be half-clipped, so anchor only on
        # interior hang points; a closed loop wraps, so every point is interior.
        points = (
            list(range(self.spans)) if self.closed else list(range(1, self.spans))
        )
        if not points:
            return []
        # Arm spread and fold width are stored in global u but authored as
        # fractions of a *span* (1/spans wide), so the fork keeps the same shape
        # relative to the gap whatever the hang-point count — amp stays absolute
        # (a fold's depth shouldn't change just because there are more pleats).
        span_u = 1.0 / self.spans
        rng = random.Random(self.mid_fold_seed)
        frac = rng.uniform(0.25, 0.5)
        chosen = rng.sample(points, max(1, round(len(points) * frac)))
        folds = []
        for i in chosen:
            if rng.random() < 0.5:                  # long, narrow-ish, deep
                length = rng.uniform(0.7, 1.0)
                width_frac = rng.uniform(0.18, 0.42)
                amp = rng.uniform(0.7, 1.1)
            else:                                   # short, wide, shallow
                length = rng.uniform(0.3, 0.6)
                width_frac = rng.uniform(0.45, 0.85)
                amp = rng.uniform(0.4, 0.7)
            spread_frac = rng.uniform(0.25, 0.6)    # arms fan this much of a span
            # Anchor on the hang point's *actual* (possibly jittered) u-position;
            # arm spread / width stay relative to the average span so the fork
            # shape is invariant to hang-point count.
            folds.append(
                (
                    self._hang_points[i],
                    length,
                    amp,
                    spread_frac * span_u,
                    width_frac * span_u,
                )
            )
        return folds

    def _midfold_offset(self, u: float, v: float) -> float:
        """Summed mid-fold V displacement at ``(u, v)`` (0 when off)."""
        if not self._midfolds:
            return 0.0
        depth_from_top = 1.0 - v  # 0 at the rail, 1 at the hem
        total = 0.0
        for u0, length, amp, spread, half_width in self._midfolds:
            if depth_from_top > length:
                continue  # this fork has petered out above the vertex
            if abs(u - u0) > spread * length + 4.5 * half_width:
                continue  # past the fork's u-band (incl. the ricker troughs)
            # Ramp in quickly below the rail (small top fade -> runs to the top)
            # and fade out at the tip; hold full strength between.
            v_prof = _smoothstep(depth_from_top / self._MIDFOLD_TOP_FADE) * _smoothstep(
                (length - depth_from_top) / self._MIDFOLD_FADE
            )
            total += amp * v_prof * _v_arms(u, u0, spread, depth_from_top, half_width)
        return self.mid_folds * 0.15 * total

    def _make_sway(self):
        """Seeded per-span lateral lean (one signed factor per span).

        Only a random ~half of the spans lean (the rest sit at ``0``, so the
        effect reads as *certain areas* drifting sideways, not a uniform shear);
        the chosen ones get a random sign (left/right) and magnitude. Empty when
        ``sway`` is off.
        """
        if self.sway <= 0.0:
            return []
        rng = random.Random(self.sway_seed)
        return [
            rng.choice((-1.0, 1.0)) * rng.uniform(0.4, 1.0)
            if rng.random() < 0.5
            else 0.0
            for _ in range(self.spans)
        ]

    def _sway_offset(self, u: float, v: float) -> float:
        """Lateral (along-rail) lean at ``(u, v)`` (0 when off).

        Rides the belly envelope — ``|sin(pi * _BELLY_HUMPS_PER_SPAN * phase)|``
        is zero at the pinned hang points and peaks where the fabric bellies most
        — so a fold drifts sideways most where it bulges most, and grows toward
        the free hem (calm at the gathered top).
        """
        if not self._sway:
            return 0.0
        k, t = self._span_at(u)
        lean = self._sway[k]
        if lean == 0.0:
            return 0.0
        phase = k + t
        # Track the belly: zero at the pinned hang points (and the in/out
        # crossover), peak where the fabric bellies most.
        env = abs(math.sin(math.pi * self._BELLY_HUMPS_PER_SPAN * phase))
        hem = 0.3 + 0.7 * (1.0 - v)           # more sway toward the free hem
        return self.sway * 0.2 * lean * env * hem

    def _end_bend_offset(self, u: float) -> float:
        """Signed bend ramped in from each end over ``end_bend_falloff``."""
        if self.end_bend_left == 0.0 and self.end_bend_right == 0.0:
            return 0.0
        fo = self.end_bend_falloff
        wl = _smoothstep((fo - u) / fo)            # 1 at u=0 -> 0 at u=fo
        wr = _smoothstep((u - (1.0 - fo)) / fo)    # 0 -> 1 at u=1
        return self.end_bend_left * wl + self.end_bend_right * wr

    # Band-limited surface grain: a handful of smooth wave octaves read as soft
    # fabric relief, where per-vertex white noise (the prior approach) read as
    # static. Amplitude rolls off ~1/f per octave.
    _BILLOW_OCTAVES = 4
    _BILLOW_FALLOFF = 0.55

    def _make_billow(self):
        """Build the coherent band-limited surface-grain field (``None`` when off).

        Delegates to :class:`ptk.BandLimitedNoise` (the reusable primitive); a
        ``closed`` rail makes the field wrap across the u-seam. The drape-
        specific weighting (stronger toward the free hem) lives in
        :meth:`_billow_offset`, not baked into the noise.
        """
        if self.irregularity <= 0.0:
            return None
        return ptk.BandLimitedNoise(
            seed=self._BASE_SEED,
            octaves=self._BILLOW_OCTAVES,
            falloff=self._BILLOW_FALLOFF,
            u_periodic=self.closed,
        )

    def _billow_offset(self, u: float, v: float) -> float:
        """Coherent surface grain at ``(u, v)`` (0 when off), weighted to the hem."""
        if self._billow is None:
            return 0.0
        # More relief toward the free hem; the gathered top stays calmer.
        return self._billow.at(u, v) * (0.4 + 0.6 * (1.0 - v))

    def _resolve_resolution(self) -> Tuple[int, int]:
        # Relies on self._total_length (set first in build()).
        # Resolve at least ~8 segments per belly hump: the belly runs
        # _BELLY_HUMPS_PER_SPAN humps per span, so guarantee that many more
        # columns.
        u_segs = max(
            int(math.ceil(self._total_length * self.density)),
            self.spans * 8 * self._BELLY_HUMPS_PER_SPAN,
            12,
        )
        v_segs = max(int(math.ceil(self.height * self.density)), 8)
        # Cap so an extreme density slider can't lock up the session.
        return min(u_segs, 4000), min(v_segs, 1000)


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


# ----------------------------------------------------------------------------
# UI slots
# ----------------------------------------------------------------------------


class CurtainSlots(ptk.LoggingMixin):
    """Switchboard slot wiring for the curtain UI (hermetic preview + presets)."""

    def __init__(self, switchboard, log_level="WARNING"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.curtain
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[curtain] ")
        self.last_curtain: Optional[str] = None
        self.presets = None
        # Auto-rail state: when nothing usable is selected we own a generated
        # driver curve (``_driver``) built from the Width/Curvature/Hanging-
        # Points/Closed fields. ``_generated`` flags that mode; ``_driver_sig``
        # is the field tuple the current driver was built from, so it's only
        # rebuilt when one of those fields actually changes.
        self._driver: Optional[str] = None
        self._driver_sig: Optional[tuple] = None
        self._generated: bool = False

        # Ensure a rail exists the moment Preview is toggled on (and clean the
        # auto-rail when it's toggled off). Connected BEFORE Preview so it runs
        # first and Preview.enable() finds a selection — you never have to
        # select an unrelated object.
        self.ui.chk000.toggled.connect(self._ensure_rail)

        # Per-parameter reset button (uitk option-box plugin): a small icon
        # button beside each field that resets it to its default on click, or
        # bypasses it to default (greyed, restorable) on Alt/Ctrl+click. The
        # X/Y/Z Position triplet is skipped — it already shares a tight row with
        # the Get button.
        # Must precede connect_multi/Preview — wrapping reparents the widgets and
        # invalidates any already-deferred wrapper (see add_reset_buttons docstring).
        self.sb.add_reset_buttons(self.ui, skip=("s025", "s026", "s027"))

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            finalize_func=self._finalize,
            message_func=self.sb.message_box,
            # Select Result is first-class in Preview: it (de)selects the
            # curtain on every preview build and on commit (after _finalize
            # discards the auto-rail), and wires chk005 live.
            select_result_checkbox=self.ui.chk005,
            result_provider=lambda: self.last_curtain,
        )
        # Re-drape live as any numeric field changes; rail-shaping fields also
        # resync the generated driver. Closed reshapes the rail; Invert is a
        # pure re-drape.
        self.sb.connect_multi(self.ui, "s000-27", "valueChanged", self._on_param_changed)
        self.ui.chk001.toggled.connect(self._on_param_changed)
        self.ui.chk004.toggled.connect(self.preview.refresh)

        # The Position fields dropped their "X "/"Y "/"Z " prefixes; color-code
        # the values red/green/blue instead (axis convention) so the row stays
        # compact while still reading per-axis at a glance.
        self._color_code_position_fields()

        # Footer doubles as a stats readout (the result's tri count) once a
        # curtain is built; show a hint until then.
        try:
            self.ui.footer.setDefaultStatusText("Toggle Preview to drape a curtain.")
        except Exception:
            pass

    # --------------------------------------------------------------- header

    def header_init(self, widget):
        """Configure header help text (the preset combo lives in the panel)."""
        widget.set_help_text(
            fmt(
                title="Curtain",
                body="Drape a pleated cloth curtain from a <b>rail</b> — a "
                "selected NURBS curve, polygon edge loop, or chain of locators, "
                "or a generated straight rail when nothing usable is selected.",
                steps=[
                    "Toggle <b>Preview</b> (a rail is auto-created from "
                    "Width/Curvature if you haven't selected your own).",
                    "Set <b>Hanging Points</b> (the pleats/pins) and "
                    "<b>Fullness</b>.",
                    "Dial <b>Gravity</b> — how far the fabric falls between "
                    "hanging points.",
                    "Press <b>Create</b> to commit.",
                ],
                sections=[
                    ("Model", [
                        "Each <b>Hanging Point</b> is a pleat where the fabric "
                        "pins to the rail — one clean gather at the rail — and "
                        "bellies into a full fold between consecutive points, so "
                        "the count maps roughly 1:1 to the folds you see. The "
                        "spans sag down a real <b>catenary</b> (cosh).",
                        "<b>Gravity</b> sets the sag depth (wider gaps fall "
                        "further); <b>Catenary Tension</b> shapes that curve.",
                        "<b>Taper</b> gathers the pleats at the top and flares "
                        "them toward the hem.",
                        "<b>Mid Folds</b> fork V-folds down from some hang "
                        "points (seed varies which), breaking the plain in/out "
                        "belly; <b>Creases</b> add diagonal V break-lines; "
                        "<b>Sway</b> randomly leans a subset of the folds left "
                        "or right along the rail (not just in/out); the "
                        "<b>Ends</b> group bends each end; <b>Round</b> softens "
                        "the hooks.",
                    ]),
                ],
                notes=[
                    "The <b>preset</b> combo loads built-in looks "
                    "(Stage Swag, Shower Curtain) and saves your own.",
                    "<b>Select Result</b> selects the finished curtain on "
                    "<b>Create</b> so you can see the result.",
                ],
            )
        )
        # Align every spinbox's value column once the panel's fonts/styles are
        # settled (deferred a tick so QFontMetrics sees the themed font).
        try:
            from qtpy import QtCore

            QtCore.QTimer.singleShot(0, self._align_spinbox_prefixes)
        except Exception as e:
            self.logger.debug(f"Prefix alignment deferral failed: {e}")

    def cmb000_init(self, widget):
        """Wire the in-panel preset selector (built-in + user tiers)."""
        try:
            from uitk.widgets.mixins.preset_manager import PresetManager

            self.presets = PresetManager(
                parent=self.ui,
                state=self.ui.state,
                preset_dir="mayatk/curtain",
                builtin_dir=str(_PRESETS_DIR),
            )
            # on_loaded resyncs the generated driver to the loaded fields, then
            # refreshes the preview in one shot.
            self.presets.wire_combo(widget, on_loaded=self._on_param_changed)
        except Exception as e:
            self.logger.warning(f"Preset combo unavailable: {e}")

    # ------------------------------------------------- spinbox value alignment

    def _color_code_position_fields(self) -> None:
        """Tint the rail Position values red/green/blue for X/Y/Z.

        The fields dropped their "X "/"Y "/"Z " prefixes (see curtain.ui); the
        axis-coded value text now carries that meaning at a glance, with the
        tooltips naming the axis as a textual fallback. Colors come from the
        shared ``pythontk.Palette.axes()`` (Maya/3D RGB convention), applied via
        the uitk ``SpinBox``/``DoubleSpinBox`` ``set_text_color`` helper.
        """
        try:
            axes = ptk.Palette.axes()
        except Exception as e:
            self.logger.debug(f"Position color-coding unavailable: {e}")
            return
        for name, key in (("s025", "x"), ("s026", "y"), ("s027", "z")):
            setter = getattr(getattr(self.ui, name, None), "set_text_color", None)
            if callable(setter):
                setter(axes[key].hex)

    def _align_spinbox_prefixes(self) -> None:
        """Pad each spinbox prefix so the values line up within each group.

        The custom spin widgets add a single ``\\t`` after the prefix, which
        only lands on one tab stop — long prefixes ("Catenary Tension:") then
        overflow past short ones ("Seed:"), so the value columns don't align.
        Here we measure the widest prefix per section (with the widget's own
        font metrics) and right-pad the rest with spaces to match (font-correct
        to within a space width), bypassing the ``\\t``. Aligning per group —
        keyed on each spinbox's container — keeps short-labelled sections tight
        instead of indenting them to clear a long label elsewhere.
        """
        try:
            from qtpy import QtWidgets, QtGui
        except Exception:
            return

        # Bucket the spinboxes by their titled group. Walk up to the nearest
        # CollapsableGroup rather than using the immediate parent, since the
        # option-box "disable" wrapping reparents each spinbox into its own
        # container — grouping on that would defeat the per-section alignment.
        try:
            from uitk.widgets.collapsableGroup import CollapsableGroup
        except Exception:
            CollapsableGroup = ()

        def _group_of(w):
            p = w.parentWidget()
            while p is not None:
                if CollapsableGroup and isinstance(p, CollapsableGroup):
                    return p
                p = p.parentWidget()
            return w.parentWidget()

        groups = {}
        for sb in self.ui.findChildren(QtWidgets.QAbstractSpinBox):
            base = sb.prefix().rstrip()  # drop the trailing tab/space
            if not base:
                continue
            groups.setdefault(_group_of(sb), []).append(
                (sb, base, QtGui.QFontMetrics(sb.font()))
            )

        for entries in groups.values():
            max_w = max(fm.horizontalAdvance(base) for _, base, fm in entries)
            for sb, base, fm in entries:
                space_w = fm.horizontalAdvance(" ") or 1
                gap = max_w + 2 * space_w - fm.horizontalAdvance(base)
                text = base + " " * max(1, round(gap / space_w))
                # Bypass the custom setPrefix (which would re-append a tab).
                if isinstance(sb, QtWidgets.QDoubleSpinBox):
                    QtWidgets.QDoubleSpinBox.setPrefix(sb, text)
                else:
                    QtWidgets.QSpinBox.setPrefix(sb, text)

    # ----------------------------------------------------------- rail / driver

    def _on_param_changed(self, *_):
        """A field changed: resync the generated driver (if any) and re-drape.

        The driver only resyncs while a preview is live — otherwise a slider
        nudge after committing (preview off) would spawn a stray rail.
        """
        if self.preview.is_enabled:
            self._sync_driver()
        self.preview.refresh()

    def _field_rail(self) -> Tuple[List[Vec], bool]:
        """The generated rail from the Width / Curvature / Position / Closed fields."""
        return Rail.make(
            width=self.ui.s001.value(),
            curvature=self.ui.s002.value(),
            closed=self.ui.chk001.isChecked(),
            center=(self.ui.s025.value(), self.ui.s026.value(), self.ui.s027.value()),
        )

    def _build_driver(self, points: Sequence[Vec], closed: bool) -> str:
        """Build a low-CV rail curve whose CVs sit at the hanging points.

        Used as the preview's visible rail — resampled to ``hanging_points``
        control points so it reads as the line of pins the cloth gathers on.
        """
        n = max(2, int(self.ui.s003.value()))
        ctrl = Rail.resample(points, n)
        crv = cmds.curve(point=ctrl, degree=min(3, max(1, len(ctrl) - 1)))
        if closed:
            cmds.closeCurve(crv, ch=False, replaceOriginal=True)
        return cmds.rename(crv, "curtain_rail")

    def _sync_driver(self, force: bool = False) -> None:
        """Rebuild the owned driver curve when a rail-shaping field changed.

        No-op unless we're in generated mode. The signature
        (width/curvature/position/closed/hanging-points) gates the rebuild so
        dragging a drape-only field (gravity, taper, hang spacing…) doesn't churn
        the curve.
        """
        if not self._generated:
            return
        sig = (
            self.ui.s001.value(),
            self.ui.s002.value(),
            self.ui.chk001.isChecked(),
            int(self.ui.s003.value()),
            self.ui.s025.value(),
            self.ui.s026.value(),
            self.ui.s027.value(),
        )
        have = bool(self._driver and cmds.objExists(self._driver))
        if have and not force and sig == self._driver_sig:
            return
        if have:
            cmds.delete(self._driver)
        points, closed = self._field_rail()
        self._driver = self._build_driver(points, closed)
        self._driver_sig = sig
        cmds.select(self._driver)

    def _discard_driver(self) -> None:
        """Delete the generated driver curve we own (orphan-rail cleanup)."""
        if self._driver and cmds.objExists(self._driver):
            try:
                cmds.delete(self._driver)
            except Exception:
                pass
        self._driver = None
        self._driver_sig = None

    def _user_selection(self):
        """Current selection minus our own driver curve."""
        return [s for s in (cmds.ls(selection=True) or []) if s != self._driver]

    def _ensure_rail(self, state: bool) -> None:
        """On preview-enable, guarantee a usable rail; on disable, clean ours.

        If the user has their own rail selected we hang on that (Width/Curvature
        are ignored). Otherwise we enter *generated* mode and build/select a
        driver curve — both to satisfy Preview's selection gate and to show the
        rail the cloth hangs on (dropped on commit).
        """
        if not state:
            self._discard_driver()
            return
        if Rail.from_selection(self._user_selection()) is not None:
            # Hang on the user's own rail; drop any auto-rail we still own.
            self._discard_driver()
            self._generated = False
            return
        self._generated = True
        self._sync_driver(force=True)

    def _resolve_rail(self, objects) -> Tuple[List[Vec], bool]:
        """Rail points for the current drape.

        Generated mode reads the Width/Curvature/Closed fields live (so they
        take effect on every refresh); selected mode resolves the user's rail.
        """
        if not self._generated:
            rail = Rail.from_selection([o for o in objects if o != self._driver])
            if rail is not None:
                points, closed = rail
                return points, closed or self.ui.chk001.isChecked()
        return self._field_rail()

    # --------------------------------------------------------------- buttons

    def b001(self):
        """Reset to Defaults."""
        self.ui.state.reset_all()

    def b002(self):
        """Set Position to the bounding-box center of the selected object(s).

        Centers the generated rail on whatever is selected (its combined world
        bounding box). Ignores the panel's own auto-rail driver and the curtain
        it's building, so Get centers on the *external* target. The three
        Position fields are set in one shot (signals blocked) and a single
        re-drape is fired, so the curtain re-centers immediately.
        """
        ours = {self._driver, self.last_curtain}
        sel = [s for s in (cmds.ls(selection=True, flatten=True) or []) if s not in ours]
        if not sel:
            self.sb.message_box("Select object(s) to center the rail on.")
            return
        bb = cmds.exactWorldBoundingBox(sel)  # combined; accepts one or many
        center = BoundingBox(bb[:3], bb[3:]).center
        for widget, value in (
            (self.ui.s025, center.x),
            (self.ui.s026, center.y),
            (self.ui.s027, center.z),
        ):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        self._on_param_changed()

    # ------------------------------------------------------------- operation

    def perform_operation(self, objects, contract):
        """Build the curtain from the resolved rail (Preview entry point)."""
        points, closed = self._resolve_rail(objects)

        self.last_curtain = CurtainMesh(
            points,
            height=self.ui.s000.value(),
            hanging_points=self.ui.s003.value(),
            hang_jitter=self.ui.s023.value(),
            hang_seed=self.ui.s024.value(),
            gravity=self.ui.s004.value(),
            tension=self.ui.s005.value(),
            round_points=self.ui.s013.value(),
            round_gather=self.ui.s022.value(),
            fullness=self.ui.s006.value(),
            taper=self.ui.s007.value(),
            mid_folds=self.ui.s019.value(),
            mid_fold_seed=self.ui.s010.value(),
            creases=self.ui.s014.value(),
            crease_seed=self.ui.s015.value(),
            sway=self.ui.s020.value(),
            sway_seed=self.ui.s021.value(),
            end_bend_left=self.ui.s016.value(),
            end_bend_right=self.ui.s017.value(),
            end_bend_falloff=self.ui.s018.value(),
            irregularity=self.ui.s008.value(),
            density=self.ui.s009.value(),
            reduce=self.ui.s012.value(),
            thickness=self.ui.s011.value(),
            invert=self.ui.chk004.isChecked(),
            closed=closed,
        ).build()
        self._update_footer()
        # Select Result is applied by Preview itself (it owns the checkbox +
        # result_provider) after this build and on commit -- see __init__.

    def _update_footer(self):
        """Show the result's triangle count in the footer; clears to the default
        hint when there is no result. Updates live as the preview re-drapes."""
        try:
            footer = self.ui.footer
        except Exception:
            return
        curtain = self.last_curtain
        if not curtain or not cmds.objExists(curtain):
            footer.setStatusText("")  # falls back to the default hint
            return
        tris = cmds.polyEvaluate(curtain, triangle=True) or 0
        footer.setStatusText(f"{tris:,} tris")

    def _finalize(self):
        """On commit, drop the preview's auto-rail.

        The auto-rail is only a preview aid (it shows where the cloth hangs and
        satisfies Preview's selection gate); it isn't wanted in the committed
        scene. Wrapped in its own undo chunk (finalize_func runs outside
        Preview's commit chunk). The next preview recomputes the rail mode from
        the live selection, so the mode flag is cleared here. Preview applies
        the Select Result toggle *after* this runs (the discard can change the
        active selection), so the result wins.
        """
        self._generated = False
        cmds.undoInfo(openChunk=True)
        try:
            self._discard_driver()
        finally:
            cmds.undoInfo(closeChunk=True)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("curtain", reload=True)
    ui.show(pos="screen", app_exec=True)
