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
# Math + drape engine. The reusable primitives live in ``ptk.MathUtils``, the
# generic polyline geometry in ``ptk.Polyline`` (``ptk.geo_utils.polyline``),
# and the pure drape deformation in ``ptk.CurtainDrape`` (``ptk.geo_utils.drape``
# — the ecosystem SSoT; blendertk consumes the same engine). This module keeps
# only the Maya halves: resolving a rail from a selection, building the mesh, and
# the wire rig. ``catenary_shape`` / ``sag_profile`` stay importable for
# back-compat.
# ----------------------------------------------------------------------------

Vec = Tuple[float, float, float]

catenary_shape = ptk.MathUtils.catenary   # back-compat re-export
sag_profile = ptk.MathUtils.catenary_sag  # back-compat re-export


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
# Deformation engine — ptk.CurtainDrape (ptk.geo_utils.drape) + the Maya mesh build
# ----------------------------------------------------------------------------


class CurtainMesh(ptk.CurtainDrape):
    """Generate a pleated, gravity-draped curtain mesh from a rail polyline.

    The drape math lives in :class:`ptk.CurtainDrape` (the ecosystem SSoT);
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
        # whole pure precompute lives in ptk.CurtainDrape.prepare().
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
