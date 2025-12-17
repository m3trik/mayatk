# !/usr/bin/python
# coding=utf-8
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, ClassVar, Dict, Iterable, List, Optional, Tuple, Union

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)

import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.xform_utils._xform_utils import XformUtils


@dataclass(frozen=True)
class ControlNodes:
    control: "pm.nt.Transform"
    group: Optional["pm.nt.Transform"] = None


class _ControlsMeta(type):
    def __getattr__(cls, name: str):
        # Allow dynamic preset access: Controls.circle(...), Controls.arrow(...), etc.
        if not name or name.startswith("_"):
            raise AttributeError(name)

        if not getattr(cls, "_PRESETS", None):
            try:
                cls._register_builtin_presets()
            except Exception:
                pass

        preset = name.lower()
        if preset in cls._PRESETS:

            def _creator(**kwargs):
                return cls.create(preset, **kwargs)

            _creator.__name__ = name
            _creator.__qualname__ = f"{cls.__name__}.{name}"
            _creator.__doc__ = (
                f"Create a '{preset}' control preset.\n\n"
                f"This is generated dynamically via Controls.__getattr__.\n"
                f"See Controls.create() for common parameters and behavior."
            )
            return _creator

        raise AttributeError(name)


class Controls(ptk.HelpMixin, metaclass=_ControlsMeta):
    """Factory for creating NURBS animation controls.

    Goals:
        - Preset-driven creation ("circle", "square", etc.)
        - Parameter-driven behavior (size, axis, match, color, grouping)
        - Easy extension via `register_preset()`

    By default, this creates a control at the origin. If `match` is provided,
    it will be aligned in world-space.
    """

    _PRESETS: ClassVar[Dict[str, Callable[..., "pm.nt.Transform"]]] = {}

    @classmethod
    def _build_dashed_ring(
        cls,
        *,
        name: str,
        radius: float = 1.0,
        segments: int = 8,
        gap_ratio: float = 0.35,
        sections: int = 8,
    ) -> "pm.nt.Transform":
        """Create a dashed ring as a single transform (multiple arc segments merged)."""

        segs = max(3, int(segments))
        gap = max(0.0, min(float(gap_ratio), 0.9))
        arc_angle = (360.0 / float(segs)) * (1.0 - gap)

        # Create one arc segment in XZ plane and duplicate/rotate.
        # Maya circle command supports sweep via `sw` (sweep angle in degrees).
        base = pm.circle(
            name=name,
            ch=False,
            r=float(radius),
            s=int(sections),
            nr=(0, 1, 0),
            sw=float(arc_angle),
        )[0]

        arcs: List[pm.nt.Transform] = []
        step = 360.0 / float(segs)
        for i in range(1, segs):
            dup = pm.duplicate(base, rr=True)[0]
            pm.rotate(dup, (0.0, step * i, 0.0), r=True, os=True)
            cls._safe_freeze(dup)
            arcs.append(dup)

        cls._merge_curve_shapes(base, arcs, delete_sources=True)
        pm.rename(base, name)
        return base

    @staticmethod
    def _merge_curve_shapes(
        target_transform: "pm.nt.Transform",
        source_transforms: Iterable["pm.nt.Transform"],
        *,
        delete_sources: bool = True,
    ) -> None:
        """Merge NURBS curve shapes from sources into target as a single object."""

        for src in source_transforms:
            if not src or src == target_transform:
                continue
            shapes = pm.listRelatives(src, shapes=True, path=True) or []
            for s in shapes:
                try:
                    pm.parent(s, target_transform, r=True, s=True)
                except Exception:
                    pm.parent(s, target_transform, s=True)
            if delete_sources:
                try:
                    pm.delete(src)
                except Exception:
                    pass

    @staticmethod
    def _safe_freeze(node: "pm.nt.Transform") -> None:
        try:
            XformUtils.freeze_transforms(node, t=True, r=True, s=True)
        except Exception:
            try:
                pm.makeIdentity(node, apply=True, t=True, r=True, s=True, pn=True)
            except Exception:
                pass

    @classmethod
    def _create_text_curves(
        cls,
        *,
        text: str,
        name: str,
        size: float = 0.25,
        axis: str = "y",
        font: str = "Arial",
        offset: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        center: bool = True,
    ) -> "pm.nt.Transform":
        """Create a single transform containing curve-text shapes."""

        if text is None or str(text) == "":
            raise ValueError("Controls: text cannot be empty")

        grp = pm.textCurves(ch=False, f=font, t=str(text))
        grp = pm.ls(grp, type="transform", long=True) or []
        if not grp:
            raise RuntimeError("Controls: textCurves produced no transform")
        root = grp[0]

        root = pm.rename(root, name)

        # Normalize orientation, size, and placement
        rx, ry, rz = cls._axis_to_rotation(axis)
        if any(abs(v) > 1e-8 for v in (rx, ry, rz)):
            pm.rotate(root, (rx, ry, rz), r=True, os=True)

        if size and abs(float(size) - 1.0) > 1e-8:
            pm.scale(root, float(size), float(size), float(size), r=True)

        if center:
            try:
                # Center in local space by shifting so bbox center is at origin
                bb = pm.exactWorldBoundingBox(root)
                cx = (bb[0] + bb[3]) / 2.0
                cy = (bb[1] + bb[4]) / 2.0
                cz = (bb[2] + bb[5]) / 2.0
                pm.move(-cx, -cy, -cz, root, r=True, ws=True)
            except Exception:
                pass

        if offset and any(abs(v) > 1e-8 for v in offset):
            pm.move(offset[0], offset[1], offset[2], root, r=True, os=True)

        cls._safe_freeze(root)

        # Flatten: bring any nested curve shapes up to root so this behaves like a single object
        descendants = pm.listRelatives(root, ad=True, type="transform") or []
        for d in descendants:
            shapes = pm.listRelatives(d, shapes=True, path=True) or []
            for s in shapes:
                try:
                    pm.parent(s, root, r=True, s=True)
                except Exception:
                    pm.parent(s, root, s=True)
        for d in descendants:
            try:
                pm.delete(d)
            except Exception:
                pass

        return root

    @staticmethod
    def _axis_to_rotation(axis: str) -> Tuple[float, float, float]:
        axis_norm = (axis or "y").lower()
        if axis_norm in ("y", "+y"):
            return (0.0, 0.0, 0.0)
        if axis_norm in ("x", "+x"):
            return (0.0, 0.0, -90.0)
        if axis_norm in ("z", "+z"):
            return (90.0, 0.0, 0.0)
        if axis_norm in ("-x",):
            return (0.0, 0.0, 90.0)
        if axis_norm in ("-y",):
            return (180.0, 0.0, 0.0)
        if axis_norm in ("-z",):
            return (-90.0, 0.0, 0.0)
        raise ValueError(f"Invalid axis '{axis}'. Use x/y/z or signed variants.")

    @staticmethod
    def _apply_wire_color(
        node: "pm.nt.Transform", color: Union[int, Tuple[float, float, float]]
    ) -> None:
        if color is None:
            return

        try:
            if isinstance(color, int):
                if node.hasAttr("overrideEnabled"):
                    node.overrideEnabled.set(1)
                if node.hasAttr("overrideRGBColors"):
                    node.overrideRGBColors.set(0)
                if node.hasAttr("overrideColor"):
                    node.overrideColor.set(int(color))
                return

            if (
                isinstance(color, tuple)
                and len(color) == 3
                and all(isinstance(c, (int, float)) for c in color)
            ):
                from mayatk.display_utils.color_manager import ColorUtils

                ColorUtils.set_color_attribute(
                    node, color, attr_type="wireframe", force=True
                )
        except Exception as exc:
            try:
                pm.warning(f"Controls: color assignment failed on {node}: {exc}")
            except Exception:
                pass

    @staticmethod
    def _match_transform(node: "pm.nt.Transform", target: Any) -> None:
        target_xform = NodeUtils.get_transform_node(target)
        if not target_xform:
            return
        tgt = pm.ls(target_xform, flatten=True)[0]
        try:
            pm.delete(pm.parentConstraint(tgt, node, mo=False))
            pm.delete(pm.scaleConstraint(tgt, node, mo=False))
        except Exception:
            pm.delete(pm.parentConstraint(tgt, node, mo=False))

    @classmethod
    def register_preset(
        cls, name: str, builder: Callable[..., "pm.nt.Transform"]
    ) -> None:
        """Register a new control preset.

        The builder must return the control transform node.
        """
        if not name:
            raise ValueError("Preset name cannot be empty")
        cls._PRESETS[name.lower()] = builder

    @classmethod
    @CoreUtils.undoable
    def create(
        cls,
        preset: str = "circle",
        name: Optional[str] = None,
        *,
        size: float = 1.0,
        axis: str = "y",
        match: Any = None,
        parent: Optional["pm.nt.Transform"] = None,
        color: Union[int, Tuple[float, float, float], None] = None,
        offset_group: bool = True,
        group_suffix: str = "_GRP",
        ctrl_suffix: str = "_CTRL",
        freeze: bool = True,
        return_nodes: bool = False,
        **kwargs,
    ) -> Union["pm.nt.Transform", ControlNodes]:
        """Create a NURBS control.

        Parameters:
            preset: Preset name (e.g. "circle", "square", "box", "ball").
            name: Base name. If it doesn't end with `ctrl_suffix`, it will be appended.
            size: Uniform scale multiplier.
            axis: Primary control normal axis (x/y/z or signed variants). Ignored by some presets.
            match: Transform (or anything resolvable via NodeUtils.get_transform_node) to align to.
            parent: Optional parent for the resulting top node (group if created, else control).
            color: Either a Maya color index (int) or an RGB tuple (0-1).
            offset_group: If True, create an offset group above the control.
            group_suffix/ctrl_suffix: Naming suffixes.
            freeze: If True, freeze control transforms after creation/orientation/scaling.
            return_nodes: If True, return ControlNodes(control, group).
            **kwargs: Forwarded to the preset builder (preset-specific parameters).

        Returns:
            pm.nt.Transform (control) by default, or ControlNodes if return_nodes=True.
        """
        if not cls._PRESETS:
            cls._register_builtin_presets()

        preset_norm = (preset or "circle").lower()
        if preset_norm not in cls._PRESETS:
            raise ValueError(
                f"Unknown control preset '{preset}'. Available: {sorted(cls._PRESETS.keys())}"
            )

        base = name or preset_norm
        if ctrl_suffix and not base.endswith(ctrl_suffix):
            base = f"{base}{ctrl_suffix}"

        ctrl = cls._PRESETS[preset_norm](name=base, axis=axis, **kwargs)

        # Apply axis orientation (some presets already handle orientation internally)
        if preset_norm not in ("text",):
            rx, ry, rz = cls._axis_to_rotation(axis)
            if any(abs(v) > 1e-8 for v in (rx, ry, rz)):
                pm.rotate(ctrl, (rx, ry, rz), r=True, os=True)

        if size and abs(float(size) - 1.0) > 1e-8:
            pm.scale(ctrl, float(size), float(size), float(size), r=True)

        if freeze:
            cls._safe_freeze(ctrl)

        grp = None
        top = ctrl
        if offset_group:
            grp_name = f"{base}{group_suffix}" if group_suffix else f"{base}_GRP"
            grp = pm.group(em=True, n=grp_name)
            pm.parent(ctrl, grp)
            top = grp

        if match is not None:
            cls._match_transform(top, match)

        if parent is not None:
            try:
                top.setParent(parent)
            except Exception:
                pm.parent(top, parent)

        cls._apply_wire_color(ctrl, color)

        if return_nodes:
            return ControlNodes(control=ctrl, group=grp)
        return ctrl

    @classmethod
    @CoreUtils.undoable
    def combine(
        cls,
        controls: Iterable[Any],
        name: Optional[str] = None,
        *,
        parent: Optional["pm.nt.Transform"] = None,
        match: Any = None,
        color: Union[int, Tuple[float, float, float], None] = None,
        delete_sources: bool = True,
        ctrl_suffix: str = "_CTRL",
    ) -> "pm.nt.Transform":
        """Combine multiple control transforms into a single selectable transform.

        This merges all curve shapes under the provided transforms into one transform.
        Typical use: merge a control with a standalone text control.
        """

        resolved: List[pm.nt.Transform] = []
        for item in controls or []:
            if item is None:
                continue
            if isinstance(item, ControlNodes):
                node = item.control
            else:
                node = NodeUtils.get_transform_node(item) or item
            try:
                node = pm.ls(node, flatten=True)[0]
            except Exception:
                continue
            if node not in resolved:
                resolved.append(node)

        if not resolved:
            raise ValueError("Controls.combine: no valid controls provided")

        base = name or resolved[0].name()
        if ctrl_suffix and not base.endswith(ctrl_suffix):
            base = f"{base}{ctrl_suffix}"

        if len(resolved) == 1:
            combined = resolved[0]
            try:
                combined = pm.rename(combined, base)
            except Exception:
                pass
        else:
            combined = pm.group(em=True, n=base)

            # Put the combined transform at the first control's xform for nicer pivots.
            try:
                cls._match_transform(combined, resolved[0])
            except Exception:
                pass

            cls._merge_curve_shapes(combined, resolved, delete_sources=delete_sources)

        if match is not None:
            cls._match_transform(combined, match)

        if parent is not None:
            try:
                combined.setParent(parent)
            except Exception:
                pm.parent(combined, parent)

        cls._apply_wire_color(combined, color)
        return combined

    # ---------------------------------------------------------------------
    # Built-in presets
    # ---------------------------------------------------------------------

    @classmethod
    def _register_builtin_presets(cls) -> None:
        if cls._PRESETS:
            return

        cls.register_preset("circle", cls._build_circle)
        cls.register_preset("square", cls._build_square)
        cls.register_preset("diamond", cls._build_diamond)
        cls.register_preset("arrow", cls._build_arrow)
        cls.register_preset("two_way_arrow", cls._build_two_way_arrow)
        cls.register_preset("four_way_arrow", cls._build_four_way_arrow)

        # Icon-style presets
        cls.register_preset("chevron", cls._build_chevron)

        # Fancy animator-friendly defaults
        cls.register_preset("target", cls._build_target)
        cls.register_preset("secondary", cls._build_secondary)
        cls.register_preset("box", cls._build_box)
        cls.register_preset("ball", cls._build_ball)

        # Standalone text
        cls.register_preset("text", cls._build_text)

    @staticmethod
    def _build_circle(
        *, name: str, axis: str = "y", sections: int = 16, **_
    ) -> "pm.nt.Transform":
        ctrl = pm.circle(
            name=name,
            ch=False,
            r=1.0,
            s=int(sections),
            nr=(0, 1, 0),
        )[0]
        return ctrl

    @staticmethod
    def _build_square(*, name: str, axis: str = "y", **_) -> "pm.nt.Transform":
        points = [
            (-1.0, 0.0, -1.0),
            (-1.0, 0.0, 1.0),
            (1.0, 0.0, 1.0),
            (1.0, 0.0, -1.0),
            (-1.0, 0.0, -1.0),
        ]
        return pm.curve(name=name, p=points, d=1)

    @staticmethod
    def _build_diamond(*, name: str, axis: str = "y", **_) -> "pm.nt.Transform":
        points = [
            (0.0, 0.0, -1.25),
            (-1.25, 0.0, 0.0),
            (0.0, 0.0, 1.25),
            (1.25, 0.0, 0.0),
            (0.0, 0.0, -1.25),
        ]
        return pm.curve(name=name, p=points, d=1)

    @staticmethod
    def _build_arrow(*, name: str, axis: str = "y", **_) -> "pm.nt.Transform":
        points = [
            (-1.25, 0.0, -0.5),
            (0.0, 0.0, -0.5),
            (0.0, 0.0, -1.25),
            (1.5, 0.0, 0.0),
            (0.0, 0.0, 1.25),
            (0.0, 0.0, 0.5),
            (-1.25, 0.0, 0.5),
            (-1.25, 0.0, -0.5),
        ]
        return pm.curve(name=name, p=points, d=1)

    @staticmethod
    def _build_two_way_arrow(*, name: str, axis: str = "y", **_) -> "pm.nt.Transform":
        # Built in XZ plane, centered at origin.
        points = [
            (-1.6, 0.0, 0.0),
            (-1.1, 0.0, 0.6),
            (-1.1, 0.0, 0.25),
            (1.1, 0.0, 0.25),
            (1.1, 0.0, 0.6),
            (1.6, 0.0, 0.0),
            (1.1, 0.0, -0.6),
            (1.1, 0.0, -0.25),
            (-1.1, 0.0, -0.25),
            (-1.1, 0.0, -0.6),
            (-1.6, 0.0, 0.0),
        ]
        return pm.curve(name=name, p=points, d=1)

    @classmethod
    def _build_four_way_arrow(
        cls, *, name: str, axis: str = "y", **_
    ) -> "pm.nt.Transform":
        # Single clean outline (no overlapping inner lines).
        length = 1.6
        head_len = 0.5
        head_width = 0.6
        shaft_half = 0.25

        neck = float(length) - float(head_len)
        L = float(length)
        hw = float(head_width)
        c = float(shaft_half)

        # Points are in XZ plane (Y=0). This is a closed border around a plus-shaped
        # 4-way arrow with arrowheads at +Z, +X, -Z, -X.
        pts_xz = [
            (0.0, L),
            (hw, neck),
            (c, neck),
            (c, c),
            (neck, c),
            (neck, hw),
            (L, 0.0),
            (neck, -hw),
            (neck, -c),
            (c, -c),
            (c, -neck),
            (hw, -neck),
            (0.0, -L),
            (-hw, -neck),
            (-c, -neck),
            (-c, -c),
            (-neck, -c),
            (-neck, -hw),
            (-L, 0.0),
            (-neck, hw),
            (-neck, c),
            (-c, c),
            (-c, neck),
            (-hw, neck),
            (0.0, L),
        ]

        points = [(x, 0.0, z) for (x, z) in pts_xz]
        return pm.curve(name=name, p=points, d=1)

    @classmethod
    def _build_target(
        cls, *, name: str, axis: str = "y", sections: int = 24, **_
    ) -> "pm.nt.Transform":
        # Outer ring
        outer = pm.circle(name=name, ch=False, r=1.0, s=int(sections), nr=(0, 1, 0))[0]
        # Inner ring
        inner = pm.circle(
            name=f"{name}_inner", ch=False, r=0.65, s=int(sections), nr=(0, 1, 0)
        )[0]
        # Cardinal ticks
        tick_len = 0.25
        tick_out = 1.15
        ticks: List[pm.nt.Transform] = []
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            pts = [
                (dx * (tick_out - tick_len), 0.0, dz * (tick_out - tick_len)),
                (dx * tick_out, 0.0, dz * tick_out),
            ]
            ticks.append(pm.curve(name=f"{name}_tick", p=pts, d=1))

        cls._merge_curve_shapes(outer, [inner] + ticks, delete_sources=True)
        return outer

    @classmethod
    def _build_secondary(cls, *, name: str, axis: str = "y", **_) -> "pm.nt.Transform":
        # Dashed ring icon (secondary/alt). Very readable and clearly not the "main" solid ring.
        return cls._build_dashed_ring(
            name=name,
            radius=1.0,
            segments=8,
            gap_ratio=0.38,
            sections=6,
        )

    @staticmethod
    def _build_chevron(
        *,
        name: str,
        axis: str = "y",
        width: float = 1.25,
        height: float = 1.0,
        direction: str = "forward",
        **_,
    ) -> "pm.nt.Transform":
        """Single chevron icon.

        direction:
            - "forward" (default): points in +Z
            - "back": points in -Z
            - "up": points in +Y (after axis orientation)
            - "down": points in -Y
        """
        w = float(width) / 2.0
        h = float(height)

        pts = [(-w, 0.0, 0.0), (0.0, 0.0, h), (w, 0.0, 0.0)]
        crv = pm.curve(name=name, p=pts, d=1)

        d = (direction or "forward").lower()
        if d in ("back", "backward"):
            pm.rotate(crv, (0.0, 180.0, 0.0), r=True, os=True)
        elif d == "left":
            pm.rotate(crv, (0.0, -90.0, 0.0), r=True, os=True)
        elif d == "right":
            pm.rotate(crv, (0.0, 90.0, 0.0), r=True, os=True)
        elif d == "up":
            pm.rotate(crv, (-90.0, 0.0, 0.0), r=True, os=True)
        elif d == "down":
            pm.rotate(crv, (90.0, 0.0, 0.0), r=True, os=True)

        return crv

    @classmethod
    def _build_box(cls, *, name: str, axis: str = "y", **_) -> "pm.nt.Transform":
        # 3D box (cube wireframe) with beveled corners for a cleaner control.
        p = 1.0
        b = 0.25  # Bevel inset

        # 8 corners, each corner has 3 verts (one per adjacent edge)
        # Corner naming: TFL = Top-Front-Left, BBR = Bottom-Back-Right, etc.
        # Front = +Z, Back = -Z, Left = -X, Right = +X, Top = +Y, Bottom = -Y

        # Bottom face corners (Y = -p)
        BFL = [(-p + b, -p, p), (-p, -p, p - b), (-p, -p + b, p)]  # Bottom-Front-Left
        BFR = [(p, -p + b, p), (p, -p, p - b), (p - b, -p, p)]  # Bottom-Front-Right
        BBL = [(-p, -p + b, -p), (-p, -p, -p + b), (-p + b, -p, -p)]  # Bottom-Back-Left
        BBR = [(p - b, -p, -p), (p, -p, -p + b), (p, -p + b, -p)]  # Bottom-Back-Right

        # Top face corners (Y = +p)
        TFL = [(-p, p - b, p), (-p, p, p - b), (-p + b, p, p)]  # Top-Front-Left
        TFR = [(p - b, p, p), (p, p, p - b), (p, p - b, p)]  # Top-Front-Right
        TBL = [(-p + b, p, -p), (-p, p, -p + b), (-p, p - b, -p)]  # Top-Back-Left
        TBR = [(p, p - b, -p), (p, p, -p + b), (p - b, p, -p)]  # Top-Back-Right

        curves = []

        # Corner triangles (beveled corners)
        for pts in [BFL, BFR, BBL, BBR, TFL, TFR, TBL, TBR]:
            curves.append(pm.curve(p=pts + [pts[0]], d=1))

        # Edges connecting corners (12 edges of the cube)
        # Bottom face edges
        curves.append(pm.curve(p=[BFL[0], BFR[2]], d=1))  # Front bottom
        curves.append(pm.curve(p=[BBL[2], BBR[0]], d=1))  # Back bottom
        curves.append(pm.curve(p=[BFL[1], BBL[1]], d=1))  # Left bottom
        curves.append(pm.curve(p=[BFR[1], BBR[1]], d=1))  # Right bottom

        # Top face edges
        curves.append(pm.curve(p=[TFL[2], TFR[0]], d=1))  # Front top
        curves.append(pm.curve(p=[TBL[0], TBR[2]], d=1))  # Back top
        curves.append(pm.curve(p=[TFL[1], TBL[1]], d=1))  # Left top
        curves.append(pm.curve(p=[TFR[1], TBR[1]], d=1))  # Right top

        # Vertical edges
        curves.append(pm.curve(p=[BFL[2], TFL[0]], d=1))  # Front-Left
        curves.append(pm.curve(p=[BFR[0], TFR[2]], d=1))  # Front-Right
        curves.append(pm.curve(p=[BBL[0], TBL[2]], d=1))  # Back-Left
        curves.append(pm.curve(p=[BBR[2], TBR[0]], d=1))  # Back-Right

        base = pm.group(em=True, n=name)
        for c in curves:
            shapes = pm.listRelatives(c, s=True, path=True) or []
            for s in shapes:
                pm.parent(s, base, r=True, s=True)
            pm.delete(c)

        return base

    @classmethod
    def _build_ball(
        cls, *, name: str, axis: str = "y", sections: int = 20, **_
    ) -> "pm.nt.Transform":
        # Geodesic sphere (icosahedron wireframe) for a cleaner, more "designed" control.
        # Golden ratio for icosahedron vertex positions
        phi = (1.0 + 5.0**0.5) / 2.0
        r = 1.0  # Radius normalization factor
        scale = r / (1.0 + phi**2) ** 0.5

        # 12 vertices of an icosahedron (centered at origin, radius ~1)
        verts = [
            (0, 1, phi),
            (0, -1, phi),
            (0, 1, -phi),
            (0, -1, -phi),
            (1, phi, 0),
            (-1, phi, 0),
            (1, -phi, 0),
            (-1, -phi, 0),
            (phi, 0, 1),
            (-phi, 0, 1),
            (phi, 0, -1),
            (-phi, 0, -1),
        ]
        verts = [(x * scale, y * scale, z * scale) for (x, y, z) in verts]

        # 30 edges of an icosahedron
        edges = [
            (0, 1),
            (0, 4),
            (0, 5),
            (0, 8),
            (0, 9),
            (1, 6),
            (1, 7),
            (1, 8),
            (1, 9),
            (2, 3),
            (2, 4),
            (2, 5),
            (2, 10),
            (2, 11),
            (3, 6),
            (3, 7),
            (3, 10),
            (3, 11),
            (4, 5),
            (4, 8),
            (4, 10),
            (5, 9),
            (5, 11),
            (6, 7),
            (6, 8),
            (6, 10),
            (7, 9),
            (7, 11),
            (8, 10),
            (9, 11),
        ]

        curves = []
        for i, j in edges:
            curves.append(pm.curve(p=[verts[i], verts[j]], d=1))

        base = pm.group(em=True, n=name)
        for c in curves:
            shapes = pm.listRelatives(c, s=True, path=True) or []
            for s in shapes:
                pm.parent(s, base, r=True, s=True)
            pm.delete(c)

        return base

    @classmethod
    def _build_text(
        cls,
        *,
        name: str,
        axis: str = "y",
        text: Optional[str] = None,
        font: str = "Arial",
        offset: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        center: bool = True,
        **_,
    ) -> "pm.nt.Transform":
        return cls._create_text_curves(
            text=str(text or ""),
            name=name,
            axis=axis,
            size=1.0,
            font=font,
            offset=offset,
            center=center,
        )
