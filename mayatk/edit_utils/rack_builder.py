# !/usr/bin/python
# coding=utf-8
"""Parametric EIA-310 (19-inch) equipment-rack generator.

A rack cabinet is a rectilinear object whose geometry is *fully specified* by a
published standard (EIA-310-D / IEC 60297) — panel width, rack-unit pitch, and
mounting-hole pattern are constants, not things to guess. That makes it the
worst possible subject for an image-to-mesh model (marching cubes rounds every
edge and fills every bay) and a trivial subject for a generator driven by the
standard. This module is the generator.

The shape is described declaratively by :class:`RackSpec` (a
:class:`pythontk.SchemaSpec`), so a cabinet is *data* — JSON a user can author,
validate, and document — not code. :class:`RackBuilder` reads a spec and emits
Maya geometry, composing existing ecosystem primitives
(:class:`~mayatk.edit_utils.primitives.Primitives`,
:class:`~mayatk.edit_utils.duplicate_grid.DuplicateGrid`) rather than
re-implementing them.

Units
    Every dimension in a spec is real-world **millimetres**. The builder emits
    at ``1 Maya unit = scene_unit_mm`` (default ``10.0`` → centimetres, Maya's
    default working unit), so the result is metrically correct and rescales
    cleanly for a project that works in metres or inches.

Example
    >>> from mayatk.edit_utils.rack_builder import RackBuilder, RackSpec
    >>> spec = RackSpec.from_dict(RackSpec.skeleton())   # a valid 2-bay default
    >>> root = RackBuilder(spec).build()                 # -> group transform
"""

from __future__ import annotations

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)

from dataclasses import dataclass
from typing import List, Tuple

import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.edit_utils.duplicate_grid import DuplicateGrid


# -----------------------------------------------------------------------------
# EIA-310 / IEC 60297 constants (millimetres). Single source of truth — every
# derived measurement in this module references these, never a magic literal.
# -----------------------------------------------------------------------------
class EIA310:
    """EIA-310-D / IEC 60297-3-100 rack dimensions, in millimetres."""

    U_MM: float = 44.45  # one rack unit (1.75 in)
    PANEL_WIDTH_MM: float = 482.6  # 19 in panel face
    HOLE_SPACING_MM: float = 465.1  # between L/R mounting-flange hole centres (18.312 in)
    # Vertical hole pattern within one U, bottom-up (0.5 / 0.625 / 0.625 in),
    # arranged so a U boundary bisects the 0.5 in gap.
    HOLE_PITCH_MM: Tuple[float, float, float] = (12.7, 15.875, 15.875)
    HOLE_SIZE_MM: float = 9.5  # square cage-nut aperture
    RAIL_WIDTH_MM: float = 15.875  # visible mounting-flange width
    PANEL_THICKNESS_MM: float = 2.0  # sheet-metal faceplate thickness

    @classmethod
    def u_to_mm(cls, u: float) -> float:
        """Rack units → millimetres."""
        return u * cls.U_MM


# -----------------------------------------------------------------------------
# Declarative schema — a cabinet is data (validated, self-documenting JSON).
# -----------------------------------------------------------------------------
@dataclass
class OccupantSpec(ptk.SchemaSpec):
    """One piece of installed rack gear, described by its front panel."""

    label: str = ptk.SchemaSpec.spec_field(
        help="Front-panel label / identifier.", required=True, example="Oscilloscope"
    )
    height_u: int = ptk.SchemaSpec.spec_field(
        help="Panel height in rack units (U).", default=1, example=4
    )
    depth_mm: float = ptk.SchemaSpec.spec_field(
        help="Chassis depth behind the panel (mm).", default=300.0
    )
    style: str = ptk.SchemaSpec.spec_field(
        help="Front-face treatment.",
        choices=["blank", "instrument", "vented", "handles"],
        default="instrument",
    )
    gap_u: float = ptk.SchemaSpec.spec_field(
        help="Empty U left below this occupant (spacing).", default=0.0
    )


@dataclass
class BaySpec(ptk.SchemaSpec):
    """A single rack bay: a mounting frame holding an ordered occupant stack."""

    ru: int = ptk.SchemaSpec.spec_field(
        help="Usable frame height in rack units (U).", default=24, example=24
    )
    punch_holes: bool = ptk.SchemaSpec.spec_field(
        help="Model the mounting-flange hole pattern (hero geometry; slower).",
        default=False,
    )
    occupants: list = ptk.SchemaSpec.spec_field(
        help="Installed gear, listed bottom-up.",
        nested=[OccupantSpec],
        default_factory=list,
    )


@dataclass
class RackSpec(ptk.SchemaSpec):
    """A complete equipment cabinet — one or more bays inside an enclosure."""

    name: str = ptk.SchemaSpec.spec_field(
        help="Base name for the generated group.", default="rack", example="DA_2"
    )
    bays: list = ptk.SchemaSpec.spec_field(
        help="Bays, left-to-right.",
        nested=[BaySpec],
        default_factory=lambda: [BaySpec(), BaySpec()],
    )
    depth_mm: float = ptk.SchemaSpec.spec_field(
        help="Enclosure outer depth (mm).", default=700.0
    )
    plinth_mm: float = ptk.SchemaSpec.spec_field(
        help="Base plinth / caster height below the lowest U (mm).", default=90.0
    )
    top_mm: float = ptk.SchemaSpec.spec_field(
        help="Enclosure header height above the highest U (mm).", default=90.0
    )
    side_panels: bool = ptk.SchemaSpec.spec_field(
        help="Enclose the outer sides with solid panels.", default=True
    )
    scene_unit_mm: float = ptk.SchemaSpec.spec_field(
        help="Millimetres per Maya unit (10.0 = cm, Maya default).", default=10.0
    )


# -----------------------------------------------------------------------------
# Builder
# -----------------------------------------------------------------------------
class RackBuilder(ptk.LoggingMixin):
    """Emit Maya geometry for a :class:`RackSpec`.

    Collaborators are injected (spec in; primitive factories are the ecosystem
    classes) so the builder stays a pure translation from data to geometry with
    no hidden scene state. Each ``_build_*`` method returns the node names it
    created; :meth:`build` groups them under a single transform named after the
    spec and never mutates anything it did not create.
    """

    def __init__(self, spec: RackSpec) -> None:
        self.spec = spec
        self._s = spec.scene_unit_mm  # mm per Maya unit (divisor)

    # -- unit helper ------------------------------------------------------
    def _u(self, mm: float) -> float:
        """Millimetres → Maya units at the spec's scene scale."""
        return mm / self._s

    # -- geometry primitives ---------------------------------------------
    def _box(
        self,
        name: str,
        size_mm: Tuple[float, float, float],
        center_mm: Tuple[float, float, float],
    ) -> str:
        """A named poly cube of *size* (mm) centred at *center* (mm)."""
        w, h, d = (self._u(v) for v in size_mm)
        # constructionHistory=False returns only [transform] (no polyCube node).
        transform = cmds.polyCube(
            width=w, height=h, depth=d, name=name, constructionHistory=False
        )[0]
        cx, cy, cz = (self._u(v) for v in center_mm)
        cmds.move(cx, cy, cz, transform, absolute=True)
        return transform

    # -- components -------------------------------------------------------
    def _bay_width_mm(self) -> float:
        """Outer width one bay occupies (panel face + flanges)."""
        return EIA310.PANEL_WIDTH_MM

    def _build_rails(self, bay: BaySpec, tag: str, x0_mm: float) -> List[str]:
        """The two front mounting flanges (left/right) for one bay frame.

        *tag* is the bay's child-naming prefix; *x0_mm* is its left edge. Holes
        are punched only when the bay opts in (``punch_holes``), via a
        duplicated-grid boolean — hero-only because hundreds of boolean
        apertures are expensive.
        """
        frame_h_mm = EIA310.u_to_mm(bay.ru)
        cy_mm = frame_h_mm / 2.0
        half_span = EIA310.HOLE_SPACING_MM / 2.0
        panel_cx = x0_mm + self._bay_width_mm() / 2.0

        rails: List[str] = []
        for side, sign in (("L", -1.0), ("R", 1.0)):
            rail = self._box(
                f"{self.spec.name}_{tag}_rail{side}",
                (EIA310.RAIL_WIDTH_MM, frame_h_mm, EIA310.PANEL_THICKNESS_MM * 2.0),
                (panel_cx + sign * half_span, cy_mm, 0.0),
            )
            if bay.punch_holes:
                # polyCBoolOp(ch=False) consumes `rail` and returns a NEW mesh
                # ({rail}_punched); rebind so the punched result is what gets
                # grouped, not the now-deleted input node.
                rail = self._punch_rail_holes(rail, bay)
            rails.append(rail)
        return rails

    def _punch_rail_holes(self, rail: str, bay: BaySpec) -> str:
        """Subtract the EIA-310 hole column from *rail*; return the cut result.

        Builds one aperture at the rail's base, arrays it UP the rail on the
        standard (averaged) pitch via :class:`DuplicateGrid`, then a single
        boolean cut. The rail spans ``[0, frame_h]`` in Y, so the column is
        anchored to that base — not the rail centre.
        """
        aperture = self._box(
            f"{rail}_holeSeed",
            (EIA310.HOLE_SIZE_MM, EIA310.HOLE_SIZE_MM, EIA310.PANEL_THICKNESS_MM * 6.0),
            (0.0, 0.0, 0.0),
        )
        # Seed = the first (bottom) hole, half the standard 0.5-in gap above the
        # base U line; DuplicateGrid leaves the seed in place and offsets the
        # copies in +Y, so the column stacks UP the rail from here.
        rail_x = cmds.getAttr(f"{rail}.translateX")
        cmds.move(rail_x, self._u(EIA310.HOLE_PITCH_MM[0] / 2.0), 0.0, aperture, absolute=True)

        holes_per_u = len(EIA310.HOLE_PITCH_MM)
        count = bay.ru * holes_per_u
        # Uniform average pitch keeps the array node-count low; the visual read
        # of the triplet pattern is preserved by the per-U grouping. "combine"
        # merges the column into ONE mesh so the boolean is a single clean cut
        # (a "copy"/"instance" group is not a valid boolean operand).
        avg_pitch = EIA310.U_MM / holes_per_u
        combined = DuplicateGrid.duplicate_grid(
            [aperture],
            dimensions=(1, count, 1),
            spacing=self._u(avg_pitch) - self._u(EIA310.HOLE_SIZE_MM),
            mode="combine",
        )
        column = combined[0] if isinstance(combined, list) else combined
        # operation=2 → difference (rail minus the hole column).
        # constructionHistory=False returns only [transform] (no boolOp node).
        cut = cmds.polyCBoolOp(
            rail,
            column,
            operation=2,
            name=f"{rail}_punched",
            constructionHistory=False,
        )[0]
        return cut

    def _build_occupants(self, bay: BaySpec, tag: str, x0_mm: float) -> List[str]:
        """Front-panel geometry for each occupant, stacked bottom-up by U."""
        nodes: List[str] = []
        cursor_u = 0.0
        panel_cx = x0_mm + self._bay_width_mm() / 2.0
        for i, occ in enumerate(bay.occupants):
            cursor_u += occ.gap_u
            h_mm = EIA310.u_to_mm(occ.height_u)
            cy_mm = EIA310.u_to_mm(cursor_u) + h_mm / 2.0
            # Faceplate.
            face = self._box(
                f"{self.spec.name}_{tag}_{i:02d}_{occ.label.replace(' ', '_')}",
                (EIA310.PANEL_WIDTH_MM, h_mm, EIA310.PANEL_THICKNESS_MM),
                (panel_cx, cy_mm, EIA310.PANEL_THICKNESS_MM),
            )
            nodes.append(face)
            # Chassis body behind the faceplate (skipped for pure blanks).
            if occ.style != "blank":
                body = self._box(
                    f"{face}_body",
                    (EIA310.PANEL_WIDTH_MM * 0.94, h_mm * 0.92, occ.depth_mm),
                    (panel_cx, cy_mm, -occ.depth_mm / 2.0),
                )
                nodes.append(body)
            cursor_u += occ.height_u
        return nodes

    def _build_enclosure(self, total_width_mm: float) -> List[str]:
        """Base plinth, top header, and optional side panels around the bays."""
        s = self.spec
        max_ru = max((b.ru for b in s.bays), default=24)
        frame_h_mm = EIA310.u_to_mm(max_ru)
        d = s.depth_mm
        nodes: List[str] = []

        # Base plinth (sits below U0).
        nodes.append(
            self._box(
                f"{s.name}_plinth",
                (total_width_mm, s.plinth_mm, d),
                (total_width_mm / 2.0, -s.plinth_mm / 2.0, -d / 2.0 + EIA310.PANEL_WIDTH_MM / 4.0),
            )
        )
        # Top header (above the tallest bay).
        nodes.append(
            self._box(
                f"{s.name}_header",
                (total_width_mm, s.top_mm, d),
                (total_width_mm / 2.0, frame_h_mm + s.top_mm / 2.0, -d / 2.0 + EIA310.PANEL_WIDTH_MM / 4.0),
            )
        )
        if s.side_panels:
            for side, cx in (("L", 0.0), ("R", total_width_mm)):
                nodes.append(
                    self._box(
                        f"{s.name}_side{side}",
                        (EIA310.PANEL_THICKNESS_MM * 2.0, frame_h_mm, d),
                        (cx, frame_h_mm / 2.0, -d / 2.0 + EIA310.PANEL_WIDTH_MM / 4.0),
                    )
                )
        return nodes

    # -- orchestration ----------------------------------------------------
    def build(self, group: bool = True) -> str:
        """Build the whole rack; return the group transform (or a flat list).

        Wrapped in a single undo chunk so the entire construction collapses to
        one undo; nothing pre-existing is touched.
        """
        with CoreUtils.undo_chunk(f"build_rack_{self.spec.name}"):
            created: List[str] = []
            x_mm = 0.0
            for b_i, bay in enumerate(self.spec.bays):
                tag = f"bay{b_i:02d}"
                created += self._build_rails(bay, tag, x_mm)
                created += self._build_occupants(bay, tag, x_mm)
                x_mm += self._bay_width_mm()
            created += self._build_enclosure(x_mm)

            if not group:
                return created
            grp = cmds.group(created, name=f"{self.spec.name}_grp")
            self.logger.info(
                f"Built rack {self.spec.name!r}: {len(self.spec.bays)} bay(s), "
                f"{len(created)} parts."
            )
            return grp

    # -- convenience ------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict) -> "RackBuilder":
        """Build directly from a raw spec ``dict`` (validated by RackSpec)."""
        RackSpec.validate(data).raise_if_errors(prefix="RackSpec: ")
        return cls(RackSpec.from_dict(data))
