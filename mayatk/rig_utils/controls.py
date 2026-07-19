# !/usr/bin/python
# coding=utf-8
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, ClassVar, Dict, Iterable, List, Optional, Tuple, Union

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)

import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils, leaf_name
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.node_utils.attributes._attributes import Attributes
from mayatk.xform_utils._xform_utils import XformUtils


@dataclass(frozen=True)
class ControlNodes:
    control: str
    group: Optional[str] = None


class _ControlsMeta(type):
    def __getattr__(cls, name: str):
        # Allow dynamic preset access: Controls.diamond(...), Controls.arrow(...), etc.
        if not name or name.startswith("_"):
            raise AttributeError(name)

        cls._register_builtin_presets()

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

    def __dir__(cls):
        # Expose dynamic presets for tab-completion / discoverability.
        cls._register_builtin_presets()
        return sorted(set(super().__dir__()) | set(cls._PRESETS))


class Controls(ptk.HelpMixin, metaclass=_ControlsMeta):
    """Factory for creating NURBS animation controls.

    Goals:
        - Preset-driven creation ("diamond", "box", "beveled_cube", etc.)
        - Parameter-driven behavior (size, axis, match, color, grouping)
        - Easy extension via `register_preset()`

    By default, this creates a control at the origin. If `match` is provided,
    it will be aligned in world-space.
    """

    _PRESETS: ClassVar[Dict[str, Callable[..., str]]] = {}
    _builtins_registered: ClassVar[bool] = False

    @staticmethod
    def _addressable(node) -> str:
        """Shortest unambiguous name for *node*: the bare leaf when it
        resolves to exactly one node, else the full DAG path.

        A control's short name goes ambiguous the instant a same-named node
        exists elsewhere (a second rig, a re-run) — returning the bare leaf
        then would raise 'More than one object matches name' on every later
        use. Keeps the leaf in the common (unique) case so callers and tests
        that address controls by short name are unaffected.
        """
        s = str(node)
        leaf = s.split("|")[-1]
        if len(cmds.ls(leaf) or []) == 1:
            return leaf
        matches = cmds.ls(s, long=True) or []
        return matches[0] if matches else s

    @staticmethod
    def _merge_curve_shapes(
        target_transform: str,
        source_transforms: Iterable[str],
    ) -> None:
        """Merge NURBS curve shapes from sources into target as a single object.

        Sources are consumed (deleted) — callers that need to keep an original
        must pass a duplicate.
        """
        for src in source_transforms:
            if not src or src == target_transform:
                continue
            shapes = cmds.listRelatives(src, shapes=True, path=True) or []
            for s in shapes:
                try:
                    cmds.parent(s, target_transform, r=True, s=True)
                except Exception:
                    cmds.parent(s, target_transform, s=True)
            try:
                cmds.delete(src)
            except Exception:
                pass

    @classmethod
    def _curves_from_poly(
        cls,
        poly_transform: str,
        name: str,
    ) -> str:
        """Extract wireframe curves from a polygon mesh, delete it,
        and return a single transform containing all edge curves.
        """
        mesh_shapes = cmds.listRelatives(poly_transform, shapes=True, type="mesh") or []
        if not mesh_shapes:
            raise RuntimeError(
                f"Controls._curves_from_poly: no mesh under {poly_transform}"
            )
        mesh = mesh_shapes[0]
        num_edges = cmds.polyEvaluate(mesh, edge=True)

        curves: List[str] = []
        for edge_idx in range(num_edges):
            verts = cmds.polyListComponentConversion(
                f"{mesh}.e[{edge_idx}]", fromEdge=True, toVertex=True
            )
            verts = cmds.ls(verts, flatten=True) or []
            positions = [cmds.pointPosition(v, world=True) for v in verts]
            if len(positions) >= 2:
                curves.append(cmds.curve(p=positions, d=1))

        base = cmds.group(em=True, n=name)
        cls._merge_curve_shapes(base, curves)
        cmds.delete(poly_transform)
        return base

    @staticmethod
    def _safe_freeze(node: str) -> None:
        try:
            XformUtils.freeze_transforms(node, t=True, r=True, s=True)
        except Exception:
            try:
                cmds.makeIdentity(node, apply=True, t=True, r=True, s=True, pn=True)
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
    ) -> str:
        """Create a single transform containing curve-text shapes."""

        if text is None or str(text) == "":
            raise ValueError("Controls: text cannot be empty")

        cmds.textCurves(ch=False, f=font, t=str(text))
        # Maya may have selected the text root after textCurves — normalise via ls.
        grp = cmds.ls(selection=True, type="transform", long=True) or []
        if not grp:
            raise RuntimeError("Controls: textCurves produced no transform")
        root = grp[0]

        root = cmds.rename(root, name)

        # Normalize orientation, size, and placement
        rx, ry, rz = cls._axis_to_rotation(axis)
        if any(abs(v) > 1e-8 for v in (rx, ry, rz)):
            cmds.rotate(rx, ry, rz, root, r=True, os=True)

        if size and abs(float(size) - 1.0) > 1e-8:
            cmds.scale(float(size), float(size), float(size), root, r=True)

        if center:
            try:
                # Center in local space by shifting so bbox center is at origin
                bb = cmds.exactWorldBoundingBox(root)
                cx = (bb[0] + bb[3]) / 2.0
                cy = (bb[1] + bb[4]) / 2.0
                cz = (bb[2] + bb[5]) / 2.0
                cmds.move(-cx, -cy, -cz, root, r=True, ws=True)
            except Exception:
                pass

        if offset and any(abs(v) > 1e-8 for v in offset):
            cmds.move(offset[0], offset[1], offset[2], root, r=True, os=True)

        cls._safe_freeze(root)

        # Flatten: bring any nested curve shapes up to root so this behaves like a single object
        descendants = cmds.listRelatives(root, ad=True, type="transform") or []
        for d in descendants:
            shapes = cmds.listRelatives(d, shapes=True, path=True) or []
            for s in shapes:
                try:
                    cmds.parent(s, root, r=True, s=True)
                except Exception:
                    cmds.parent(s, root, s=True)
        for d in descendants:
            try:
                cmds.delete(d)
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
        node: str, color: Union[int, Tuple[float, float, float]]
    ) -> None:
        if color is None:
            return

        try:
            if isinstance(color, int):
                if Attributes.has_attr(node, "overrideEnabled"):
                    cmds.setAttr(f"{node}.overrideEnabled", 1)
                if Attributes.has_attr(node, "overrideRGBColors"):
                    cmds.setAttr(f"{node}.overrideRGBColors", 0)
                if Attributes.has_attr(node, "overrideColor"):
                    cmds.setAttr(f"{node}.overrideColor", int(color))
                return

            if (
                isinstance(color, tuple)
                and len(color) == 3
                and all(isinstance(c, (int, float)) for c in color)
            ):
                from mayatk.display_utils.color_id import ColorUtils

                ColorUtils.set_color_attribute(
                    node, color, attr_type="wireframe", force=True
                )
        except Exception as exc:
            try:
                cmds.warning(f"Controls: color assignment failed on {node}: {exc}")
            except Exception:
                pass

    @staticmethod
    def _match_transform(node: str, target: Any) -> None:
        target_xform = NodeUtils.get_transform_node(target)
        if not target_xform:
            return
        tgt = (cmds.ls(target_xform, flatten=True) or [None])[0]
        if not tgt:
            return
        try:
            cmds.matchTransform(
                str(node), str(tgt), position=True, rotation=True, scale=True
            )
        except Exception:
            # Locked scale channels etc. — match what we can.
            cmds.matchTransform(str(node), str(tgt), position=True, rotation=True)

    @classmethod
    def register_preset(cls, name: str, builder: Callable[..., str]) -> None:
        """Register a new control preset.

        The builder must return the control transform node.
        """
        if not name:
            raise ValueError("Preset name cannot be empty")
        cls._PRESETS[name.lower()] = builder

    @classmethod
    def shapes(cls) -> List[str]:
        """Sorted names of the registered presets (for a UI combo / validation)."""
        cls._register_builtin_presets()
        return sorted(cls._PRESETS)

    @classmethod
    @CoreUtils.undoable
    def create(
        cls,
        preset: str = "diamond",
        name: Optional[str] = None,
        *,
        size: float = 1.0,
        axis: str = "y",
        match: Any = None,
        parent: Optional[str] = None,
        color: Union[int, Tuple[float, float, float], None] = None,
        offset_group: bool = True,
        group_suffix: str = "_GRP",
        ctrl_suffix: str = "_CTRL",
        freeze: bool = True,
        tag_as_controller: bool = True,
        return_nodes: bool = False,
        **kwargs,
    ) -> Union[str, ControlNodes]:
        """Create a NURBS control.

        Parameters:
            preset: Preset name (e.g. "diamond", "box", "ball", "beveled_cube").
            name: Base name. If it doesn't end with `ctrl_suffix`, it will be appended.
            size: Uniform scale multiplier.
            axis: Primary control normal axis (x/y/z or signed variants). Ignored by some presets.
            match: Transform (or anything resolvable via NodeUtils.get_transform_node) to align to.
            parent: Optional parent for the resulting top node (group if created, else control).
            color: Either a Maya color index (int) or an RGB tuple (0-1).
            offset_group: If True, create an offset group above the control.
            group_suffix/ctrl_suffix: Naming suffixes.
            freeze: If True, freeze control transforms after creation/orientation/scaling.
            tag_as_controller: If True, tag the control as a Maya animation controller
                (enables pick-walking, controller filter in outliner, etc.).
            return_nodes: If True, return ControlNodes(control, group).
            **kwargs: Forwarded to the preset builder (preset-specific parameters).

        Returns:
            str (control transform name) by default, or ControlNodes if return_nodes=True.
        """
        cls._register_builtin_presets()

        preset_norm = (preset or "diamond").lower()
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
                cmds.rotate(rx, ry, rz, ctrl, r=True, os=True)

        if size and abs(float(size) - 1.0) > 1e-8:
            cmds.scale(float(size), float(size), float(size), ctrl, r=True)

        if freeze:
            cls._safe_freeze(ctrl)

        grp = None
        top = ctrl
        if offset_group:
            grp_name = f"{base}{group_suffix}" if group_suffix else f"{base}_GRP"
            grp = cmds.group(em=True, n=grp_name)
            # Recapture the control after the move — parenting can rename it
            # (a same-named control elsewhere makes the short name ambiguous,
            # so Maya returns a path-qualified name). Dropping this return
            # leaves ``ctrl`` pointing at the pre-parent path, which no longer
            # resolves under a collision.
            ctrl = cmds.parent(ctrl, grp)[0]
            top = grp

        if match is not None:
            cls._match_transform(top, match)

        if parent is not None:
            try:
                top = cmds.parent(top, str(parent))[0]
                if grp is not None:
                    grp = top  # the group moved with the top node
            except Exception as exc:
                cmds.warning(f"Controls: could not parent {top} under {parent}: {exc}")

        # The names above go stale/ambiguous after the moves; resolve each to
        # its shortest UNAMBIGUOUS form (bare leaf when unique, full DAG path
        # only when a same-named node forces it). Deriving the control from
        # its group guarantees the right node under a collision.
        if grp is not None:
            grp = cls._addressable(grp)
            ctrl = cls._addressable(f"{grp}|{ctrl.split('|')[-1]}")
        else:
            ctrl = top = cls._addressable(top)

        cls._apply_wire_color(ctrl, color)

        if tag_as_controller:
            try:
                cmds.controller(ctrl)
            except Exception:
                pass

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
        parent: Optional[str] = None,
        match: Any = None,
        color: Union[int, Tuple[float, float, float], None] = None,
        delete_sources: bool = True,
        ctrl_suffix: str = "_CTRL",
    ) -> str:
        """Combine multiple control transforms into a single selectable transform.

        This merges all curve shapes under the provided transforms into one
        transform. Every source keeps its world placement (offsets are baked
        into the CVs); the combined pivot sits at the first control's xform.
        With ``delete_sources=False`` the originals are left untouched and
        duplicates are merged instead.
        Typical use: merge a control with a standalone text control.
        """

        resolved: List[str] = []
        for item in controls or []:
            if item is None:
                continue
            if isinstance(item, ControlNodes):
                node = item.control
            else:
                node = NodeUtils.get_transform_node(item) or item
            try:
                node = (cmds.ls(node, flatten=True) or [None])[0]
            except Exception:
                continue
            if node and node not in resolved:
                resolved.append(node)

        if not resolved:
            raise ValueError("Controls.combine: no valid controls provided")

        base = name or leaf_name(resolved[0]).split(":")[-1]
        if ctrl_suffix and not base.endswith(ctrl_suffix):
            base = f"{base}{ctrl_suffix}"

        # When keeping sources, operate on duplicates — the shape merge consumes
        # its inputs, so working on the originals would leave gutted husks.
        if not delete_sources:
            resolved = [cmds.duplicate(src, renameChildren=True)[0] for src in resolved]

        if len(resolved) == 1:
            combined = resolved[0]
            try:
                combined = cmds.rename(combined, base)
            except Exception:
                pass
        else:
            combined = cmds.group(em=True, n=base)

            # Put the combined transform at the first control's xform for nicer pivots.
            try:
                cls._match_transform(combined, resolved[0])
            except Exception:
                pass

            # Bake each source's world placement before the merge: parent it under
            # the combined transform (world preserved), then freeze so the offset
            # lands in the CVs. Shape-parenting alone (-r -s) keeps local CVs, which
            # would snap every source except the matched one to the combined pivot.
            baked: List[str] = []
            for src in resolved:
                try:
                    src = cmds.parent(src, combined)[0]
                    cls._safe_freeze(src)
                except Exception as exc:
                    cmds.warning(
                        f"Controls.combine: could not bake {src}'s placement: {exc}"
                    )
                baked.append(src)

            cls._merge_curve_shapes(combined, baked)

        if match is not None:
            cls._match_transform(combined, match)

        if parent is not None:
            try:
                combined = cmds.parent(combined, str(parent))[0]
            except Exception as exc:
                cmds.warning(
                    f"Controls: could not parent {combined} under {parent}: {exc}"
                )

        cls._apply_wire_color(combined, color)
        return combined

    # ---------------------------------------------------------------------
    # Built-in presets
    # ---------------------------------------------------------------------

    @classmethod
    def _register_builtin_presets(cls) -> None:
        # Guarded by a flag, not `if cls._PRESETS` — a custom preset registered
        # before the first create()/shapes() call must not block the builtins.
        if cls._builtins_registered:
            return
        cls._builtins_registered = True

        # Flat curves
        cls.register_preset("circle", cls._build_circle)
        cls.register_preset("square", cls._build_square)
        cls.register_preset("diamond", cls._build_diamond)
        cls.register_preset("arrow", cls._build_arrow)
        cls.register_preset("two_way_arrow", cls._build_two_way_arrow)
        cls.register_preset("four_way_arrow", cls._build_four_way_arrow)
        cls.register_preset("chevron", cls._build_chevron)

        # 3D primitives
        cls.register_preset("target", cls._build_target)
        cls.register_preset("box", cls._build_box)
        cls.register_preset("cube", cls._build_box)
        cls.register_preset("beveled_cube", cls._build_beveled_cube)
        cls.register_preset("ball", cls._build_ball)
        cls.register_preset("sphere", cls._build_ball)
        cls.register_preset("torus", cls._build_torus)
        cls.register_preset("helix", cls._build_helix)
        cls.register_preset("geosphere", cls._build_geosphere)

        # 3D solids
        cls.register_preset("pyramid", cls._build_pyramid)
        cls.register_preset("star", cls._build_star)

        # Standalone text
        cls.register_preset("text", cls._build_text)

    @classmethod
    def _build_circle(
        cls, *, name: str, axis: str = "y", sections: int = 16, **_
    ) -> str:
        """Flat circle — the standard rig control."""
        return cmds.circle(name=name, ch=False, r=1.0, s=int(sections), nr=(0, 1, 0))[0]

    @classmethod
    def _build_square(cls, *, name: str, axis: str = "y", **_) -> str:
        """Flat square outline."""
        p = 1.0
        pts = [
            (-p, 0.0, -p),
            (p, 0.0, -p),
            (p, 0.0, p),
            (-p, 0.0, p),
            (-p, 0.0, -p),
        ]
        return cmds.curve(name=name, p=pts, d=1)

    @classmethod
    def _build_diamond(cls, *, name: str, axis: str = "y", **_) -> str:
        """3D octahedron — two four-sided pyramids joined at the equator."""
        r = 1.0  # equatorial radius
        h = 1.25  # half-height (top/bottom apex)

        top = (0.0, h, 0.0)
        bot = (0.0, -h, 0.0)
        eq = [
            (r, 0.0, 0.0),
            (0.0, 0.0, r),
            (-r, 0.0, 0.0),
            (0.0, 0.0, -r),
        ]

        curves: List[str] = []
        # Equatorial ring
        curves.append(cmds.curve(p=eq + [eq[0]], d=1))
        # Ribs from top to equator and bottom to equator
        for pt in eq:
            curves.append(cmds.curve(p=[top, pt, bot], d=1))

        base = cmds.group(em=True, n=name)
        cls._merge_curve_shapes(base, curves)
        return base

    @classmethod
    def _build_arrow(cls, *, name: str, axis: str = "y", **_) -> str:
        """3D arrow with depth — reads well from any camera angle."""
        # Flat arrow outline
        y = 0.0
        outline = [
            (-1.25, y, -0.4),
            (0.15, y, -0.4),
            (0.15, y, -1.0),
            (1.5, y, 0.0),
            (0.15, y, 1.0),
            (0.15, y, 0.4),
            (-1.25, y, 0.4),
            (-1.25, y, -0.4),
        ]
        # Build top and bottom planes offset slight in Y for 3D depth
        d = 0.15  # depth half-height
        top_pts = [(x, d, z) for (x, _, z) in outline]
        bot_pts = [(x, -d, z) for (x, _, z) in outline]

        curves: List[str] = []
        curves.append(cmds.curve(p=top_pts, d=1))
        curves.append(cmds.curve(p=bot_pts, d=1))
        # Vertical struts at key vertices (skip redundant ones for cleanliness)
        for i in (0, 2, 3, 4, 6):
            curves.append(cmds.curve(p=[top_pts[i], bot_pts[i]], d=1))

        base = cmds.group(em=True, n=name)
        cls._merge_curve_shapes(base, curves)
        return base

    @classmethod
    def _build_two_way_arrow(
        cls, *, name: str, axis: str = "y", **_
    ) -> str:
        """3D two-way arrow with depth."""
        outline = [
            (-1.6, 0.0, 0.0),
            (-1.1, 0.0, 0.55),
            (-1.1, 0.0, 0.22),
            (1.1, 0.0, 0.22),
            (1.1, 0.0, 0.55),
            (1.6, 0.0, 0.0),
            (1.1, 0.0, -0.55),
            (1.1, 0.0, -0.22),
            (-1.1, 0.0, -0.22),
            (-1.1, 0.0, -0.55),
            (-1.6, 0.0, 0.0),
        ]
        d = 0.12
        top_pts = [(x, d, z) for (x, _, z) in outline]
        bot_pts = [(x, -d, z) for (x, _, z) in outline]

        curves: List[str] = []
        curves.append(cmds.curve(p=top_pts, d=1))
        curves.append(cmds.curve(p=bot_pts, d=1))
        # Vertical struts at arrow-tip and shaft corners
        for i in (0, 1, 4, 5, 6, 9):
            curves.append(cmds.curve(p=[top_pts[i], bot_pts[i]], d=1))

        base = cmds.group(em=True, n=name)
        cls._merge_curve_shapes(base, curves)
        return base

    @classmethod
    def _build_four_way_arrow(
        cls, *, name: str, axis: str = "y", **_
    ) -> str:
        """3D four-way arrow with depth."""
        L = 1.6
        head_len = 0.5
        hw = 0.55
        c = 0.22
        neck = L - head_len

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

        d = 0.12
        top_pts = [(x, d, z) for (x, z) in pts_xz]
        bot_pts = [(x, -d, z) for (x, z) in pts_xz]

        curves: List[str] = []
        curves.append(cmds.curve(p=top_pts, d=1))
        curves.append(cmds.curve(p=bot_pts, d=1))
        # Struts at all four arrow tips and the inner elbow corners
        for i in (0, 1, 5, 6, 7, 11, 12, 13, 17, 18, 19, 23):
            curves.append(cmds.curve(p=[top_pts[i], bot_pts[i]], d=1))

        base = cmds.group(em=True, n=name)
        cls._merge_curve_shapes(base, curves)
        return base

    @classmethod
    def _build_target(
        cls, *, name: str, axis: str = "y", sections: int = 24, **_
    ) -> str:
        """3D target — three orthogonal circles (gimbal/gyroscope)."""
        xy = cmds.circle(name=name, ch=False, r=1.0, s=int(sections), nr=(0, 0, 1))[0]
        xz = cmds.circle(
            name=f"{name}_xz", ch=False, r=1.0, s=int(sections), nr=(0, 1, 0)
        )[0]
        yz = cmds.circle(
            name=f"{name}_yz", ch=False, r=1.0, s=int(sections), nr=(1, 0, 0)
        )[0]

        cls._merge_curve_shapes(xy, [xz, yz])
        return xy

    @classmethod
    def _build_chevron(
        cls,
        *,
        name: str,
        axis: str = "y",
        **_,
    ) -> str:
        """3D chevron — triangular prism pointing in +Z."""
        prism = cmds.polyPrism(l=0.35, w=1.6, ns=3, sh=1, sc=0, ax=(0, 1, 0), ch=False)[0]
        # Flatten Y and stretch Z for a chevron silhouette
        cmds.scale(1.0, 0.5, 1.2, prism, r=True)
        cmds.makeIdentity(prism, apply=True, t=True, r=True, s=True, pn=True)
        return cls._curves_from_poly(prism, name)

    @classmethod
    def _build_box(cls, *, name: str, axis: str = "y", **_) -> str:
        """Simple cube wireframe — 12 edges, no bevels."""
        p = 1.0
        verts = [
            (-p, -p, -p),
            (-p, -p, p),
            (p, -p, p),
            (p, -p, -p),
            (-p, p, -p),
            (-p, p, p),
            (p, p, p),
            (p, p, -p),
        ]
        edges = [
            (0, 1),
            (1, 2),
            (2, 3),
            (3, 0),  # bottom
            (4, 5),
            (5, 6),
            (6, 7),
            (7, 4),  # top
            (0, 4),
            (1, 5),
            (2, 6),
            (3, 7),  # verticals
        ]

        curves: List[str] = []
        for i, j in edges:
            curves.append(cmds.curve(p=[verts[i], verts[j]], d=1))

        base = cmds.group(em=True, n=name)
        cls._merge_curve_shapes(base, curves)
        return base

    @classmethod
    def _build_beveled_cube(
        cls, *, name: str, axis: str = "y", **_
    ) -> str:
        """Beveled cube — polyCube with all edges beveled at 50%,
        converted to NURBS curves via edge extraction.
        """
        cube = cmds.polyCube(w=2, h=2, d=2, sx=1, sy=1, sz=1, ch=True)[0]
        cmds.polyBevel3(
            f"{cube}.e[*]",
            offset=0.5,
            offsetAsFraction=True,
            segments=1,
            depth=1,
            chamfer=True,
            ch=True,
        )
        cmds.delete(cube, ch=True)
        return cls._curves_from_poly(cube, name)

    @classmethod
    def _build_ball(
        cls, *, name: str, axis: str = "y", sections: int = 20, **_
    ) -> str:
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

        curves: List[str] = []
        for i, j in edges:
            curves.append(cmds.curve(p=[verts[i], verts[j]], d=1))

        base = cmds.group(em=True, n=name)
        cls._merge_curve_shapes(base, curves)
        return base

    @classmethod
    def _build_torus(cls, *, name: str, axis: str = "y", **_) -> str:
        """Torus — polyTorus converted to NURBS curves."""
        torus = cmds.polyTorus(
            r=0.8, sr=0.25, tw=0, sx=12, sy=4, ax=(0, 1, 0), ch=False
        )[0]
        return cls._curves_from_poly(torus, name)

    @classmethod
    def _build_helix(cls, *, name: str, axis: str = "y", **_) -> str:
        """Helix — polyHelix converted to NURBS curves."""
        helix = cmds.polyHelix(
            c=2,
            h=6,
            w=5,
            r=0.35,
            sa=5,
            sco=15,
            sc=0,
            ax=(0, 1, 0),
            ch=False,
        )[0]
        return cls._curves_from_poly(helix, name)

    @classmethod
    def _build_geosphere(cls, *, name: str, axis: str = "y", **_) -> str:
        """Geosphere — icosahedron subdivided once to form a geodesic sphere."""
        ico = cmds.polyPlatonicSolid(solidType=1, r=1.0, ch=True)[0]
        cmds.polySmooth(ico, divisions=1, ch=True)
        cmds.delete(ico, ch=True)
        return cls._curves_from_poly(ico, name)

    @classmethod
    def _build_pyramid(cls, *, name: str, axis: str = "y", **_) -> str:
        """3D four-sided pyramid — apex above a square base."""
        apex = (0.0, 1.2, 0.0)
        base_pts = [
            (-0.8, 0.0, -0.8),
            (0.8, 0.0, -0.8),
            (0.8, 0.0, 0.8),
            (-0.8, 0.0, 0.8),
        ]

        curves: List[str] = []
        # Base square
        curves.append(cmds.curve(p=base_pts + [base_pts[0]], d=1))
        # Ribs from apex to each base corner
        for pt in base_pts:
            curves.append(cmds.curve(p=[apex, pt], d=1))

        base = cmds.group(em=True, n=name)
        cls._merge_curve_shapes(base, curves)
        return base

    @classmethod
    def _build_star(
        cls, *, name: str, axis: str = "y", points: int = 6, **_
    ) -> str:
        """3D star burst — outer and inner rings connected by radial spokes,
        with a subtle Y-depth for viewport readability."""
        import math

        n = max(3, int(points))
        outer_r = 1.0
        inner_r = 0.5
        d = 0.15  # depth half-height

        star_pts: List[Tuple[float, float, float]] = []
        for i in range(n):
            angle_out = math.radians(i * 360.0 / n - 90.0)
            angle_in = math.radians((i + 0.5) * 360.0 / n - 90.0)
            star_pts.append(
                (math.cos(angle_out) * outer_r, 0.0, math.sin(angle_out) * outer_r)
            )
            star_pts.append(
                (math.cos(angle_in) * inner_r, 0.0, math.sin(angle_in) * inner_r)
            )
        star_pts.append(star_pts[0])  # close

        top_pts = [(x, d, z) for (x, _, z) in star_pts]
        bot_pts = [(x, -d, z) for (x, _, z) in star_pts]

        curves: List[str] = []
        curves.append(cmds.curve(p=top_pts, d=1))
        curves.append(cmds.curve(p=bot_pts, d=1))
        # Vertical struts at outer points only (every other vertex)
        for i in range(0, len(star_pts) - 1, 2):
            curves.append(cmds.curve(p=[top_pts[i], bot_pts[i]], d=1))

        base = cmds.group(em=True, n=name)
        cls._merge_curve_shapes(base, curves)
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
    ) -> str:
        return cls._create_text_curves(
            text=str(text or ""),
            name=name,
            axis=axis,
            size=1.0,
            font=font,
            offset=offset,
            center=center,
        )
