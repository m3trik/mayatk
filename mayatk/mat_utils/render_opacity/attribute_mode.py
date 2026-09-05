# !/usr/bin/python
# coding=utf-8
import re
from typing import Dict, List, Optional, Sequence, Tuple
import pythontk as ptk

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None  # type: ignore[assignment]

from mayatk.node_utils.attributes._attributes import Attributes
from mayatk.mat_utils.render_opacity.channels import (
    CHANNELS,
    OPACITY,
    HIGHLIGHT,
    ChannelSpec,
    spec_for,
)


class OpacityAttributeMode(ptk.LoggingMixin):
    """
    Implements the 'attribute' mode for the render-effect channels.

    This mode adds a custom keyable float per channel (``opacity``,
    ``highlight``, ...) to each object's transform, from the channel's
    ``Attributes`` YAML preset. Recommended for per-object control in Game
    Engines. Every method takes the channel as a :class:`ChannelSpec` or its
    name and defaults to ``opacity``, which is what the class was written for.

    Visibility is managed through **direct keyframe mirroring** rather than
    a condition-node driver, and only for the channel that *drives presence*
    (``opacity``).  When it is keyed (either manually or via the behavior
    system), a matching visibility keyframe is set at the same time.  This
    produces real animation curves on both channels that FBX can export and
    game engines can read natively.

    The behavior system in ``shots.behaviors`` performs this mirroring
    automatically: when a template targets ``visibility`` and the object
    has an ``opacity`` attribute, the behavior keys ``opacity`` and mirrors
    the value to ``visibility`` at the same time.
    """

    ATTR_NAME = OPACITY.name
    """The presence channel's attribute name (kept for its callers)."""

    _VIS_DRIVER_RE = re.compile(r"_VisDriver\d*$")
    """Matches legacy condition node names including Maya auto-incremented variants."""

    # ------------------------------------------------------------------
    # Create / remove
    # ------------------------------------------------------------------

    @classmethod
    def create(cls, objects, spec: ChannelSpec = OPACITY) -> Dict[str, Dict]:
        """Add the channel's attribute(s) on each transform (no keyframes).

        No condition-node driver is created.  Visibility mirroring is
        handled by the behavior system or by calling
        :meth:`sync_visibility_from_opacity` explicitly after keying.
        """
        spec = spec_for(spec)
        results = {}

        Attributes.apply_preset(spec.preset, objects)

        for obj in cmds.ls(objects):
            if spec.drives_presence:
                # Remove any legacy condition-node driver left from older versions
                cls._remove_legacy_vis_driver(obj)

            short = obj.split("|")[-1].split(":")[-1]
            attrs = [f"{short}.{spec.name}"]
            if spec.color_attr:
                attrs.append(f"{short}.{spec.color_attr}")
            results[short] = {"attrs_created": attrs}
            cls.logger.info(f"Verified {spec.name} on {obj}")

        return results

    @classmethod
    def has_channel(cls, obj, spec: ChannelSpec = OPACITY) -> bool:
        """Whether *obj* carries the channel's attribute."""
        return bool(
            cmds.attributeQuery(spec_for(spec).name, node=str(obj), exists=True)
        )

    @classmethod
    def channels_on(cls, obj) -> List[ChannelSpec]:
        """Every channel *obj* carries."""
        return [spec for spec in CHANNELS.values() if cls.has_channel(obj, spec)]

    @staticmethod
    def _long_plug(obj, attr: str) -> str:
        """``<long name>.<attr>`` -- the plug form that targets the TRANSFORM only.

        The kwarg form (``attribute=``) also hits the shape node.
        """
        return f"{(cmds.ls(obj, long=True) or [obj])[0]}.{attr}"

    # ------------------------------------------------------------------
    # Keying
    # ------------------------------------------------------------------

    @classmethod
    def key_fade(
        cls,
        objects,
        start: float,
        end: float,
        direction: str = "in",
        auto_create: bool = True,
        tangent: str = "linear",
        spec: ChannelSpec = OPACITY,
    ) -> List[Tuple[str, str]]:
        """Key a two-key ramp on the channel; mirror to visibility if it gates presence.

        Creates two keyframes on the channel (smooth) and, for the presence
        channel, two matching keyframes on ``visibility`` (stepped binary) so
        that FBX export produces native tracks for both channels.

        Parameters:
            objects: Maya nodes to key.
            start: First frame of the fade.
            end: Last frame of the fade.
            direction: ``"in"`` (0→1), ``"out"`` (1→0), or ``"auto"``
                (detect from the last keyed value).
            auto_create: When ``True``, create the attribute on objects that
                lack it before keying.
            tangent: Tangent type for the keys (default ``"linear"``).
            spec: The channel (name or :class:`ChannelSpec`); ``opacity``.

        Returns:
            List of ``(object_name, "in"|"out")`` for each keyed object.
        """
        spec = spec_for(spec)
        objects = cmds.ls(objects)
        if not objects:
            return []

        if auto_create:
            missing = [o for o in objects if not cls.has_channel(o, spec)]
            if missing:
                cls.create(missing, spec)

        keyed: List[Tuple[str, str]] = []
        for obj in objects:
            if not cls.has_channel(obj, spec):
                continue

            # Resolve direction
            if direction == "auto":
                fade_in = cls._resolve_auto_fade(obj, start, spec)
            else:
                fade_in = direction == "in"

            start_val, end_val = (0.0, 1.0) if fade_in else (1.0, 0.0)

            # Maya's inTangentType doesn't accept "step"; use "stepnext".
            itt = "stepnext" if tangent == "step" else tangent

            plug = cls._long_plug(obj, spec.name)
            for t, v in ((start, start_val), (end, end_val)):
                cmds.setKeyframe(
                    plug, time=t, value=v, inTangentType=itt, outTangentType=tangent
                )

            if spec.drives_presence:
                cls._mirror_visibility(obj, ((start, start_val), (end, end_val)))

            keyed.append(
                (obj.split("|")[-1].split(":")[-1], "in" if fade_in else "out")
            )

        return keyed

    @classmethod
    def key_pulse(
        cls,
        objects,
        start: float,
        end: float,
        period: float,
        bright_fraction: float = 0.59,
        ramp_fraction: float = 0.25,
        color: Optional[Sequence[float]] = None,
        auto_create: bool = True,
        spec: ChannelSpec = HIGHLIGHT,
    ) -> List[str]:
        """Key a repeating bright/dim pulse on the channel over ``start..end``.

        The shape is the one measured on the WebXR reference: a period of
        2.86 s with the object bright for 59% of it, eased at both ends. Keys
        are written with LINEAR tangents at each transition rather than as a
        spline through the extrema, because the published ramp is read
        linearly (see ``RenderEffects._linear_ramp``) -- a spline would ship
        as a triangle wave and lose the dwell. Four keys per cycle: bright hold
        start, dim ramp end, dim hold end, bright ramp end.

        Parameters:
            objects: Maya nodes to key.
            start: First frame of the pulse.
            end: Last frame; the pulse is cut here, holding its last value.
            period: One cycle, in FRAMES.
            bright_fraction: Share of the cycle spent bright (0-1).
            ramp_fraction: Share of the cycle spent in EACH transition (0-0.5);
                the holds take what remains.
            color: Optional ``(r, g, b)`` written to the channel's colour attr.
            auto_create: Create the channel on objects that lack it.
            spec: The channel; ``highlight``.

        Returns:
            The keyed objects' short names.
        """
        spec = spec_for(spec)
        objects = cmds.ls(objects)
        if not objects or period <= 0 or end <= start:
            return []
        if auto_create:
            missing = [o for o in objects if not cls.has_channel(o, spec)]
            if missing:
                cls.create(missing, spec)

        ramp = max(0.0, min(0.5, ramp_fraction)) * period
        bright = max(0.0, min(1.0, bright_fraction)) * period
        # Each hold gives up one ramp; the ramps then sit between the holds.
        bright_hold = max(0.0, bright - ramp)
        dim_hold = max(0.0, (period - bright) - ramp)
        cycle = [
            (0.0, 1.0),
            (bright_hold, 1.0),
            (bright_hold + ramp, 0.0),
            (bright_hold + ramp + dim_hold, 0.0),
        ]

        keyed: List[str] = []
        for obj in objects:
            if not cls.has_channel(obj, spec):
                continue
            plug = cls._long_plug(obj, spec.name)
            cmds.cutKey(plug, time=(start, end), clear=True)
            t0 = float(start)
            while t0 < end:
                for offset, value in cycle:
                    t = t0 + offset
                    if t > end:
                        break
                    cmds.setKeyframe(
                        plug,
                        time=t,
                        value=value,
                        inTangentType="linear",
                        outTangentType="linear",
                    )
                t0 += period
            # The cut point holds its last value explicitly, so a clip window
            # ending here reads a finished pulse rather than a ramp it invents.
            last = cmds.keyframe(plug, query=True, time=(start, end), valueChange=True)
            if last:
                cmds.setKeyframe(
                    plug,
                    time=end,
                    value=last[-1],
                    inTangentType="linear",
                    outTangentType="linear",
                )
            if color is not None and spec.color_attr:
                Attributes.set_plug(
                    cls._long_plug(obj, spec.color_attr),
                    tuple(float(c) for c in color[:3]),
                )
            keyed.append(obj.split("|")[-1].split(":")[-1])
        return keyed

    @classmethod
    def _mirror_visibility(cls, obj, keys) -> None:
        """Set stepped ``visibility`` keys mirroring ``keys`` (``(time, value)``)."""
        vis_plug = cls._long_plug(obj, "visibility")
        for t, v in keys:
            cmds.setKeyframe(
                vis_plug,
                time=t,
                value=1.0 if v > 0 else 0.0,
                inTangentType="stepnext",
                outTangentType="step",
            )
            cmds.keyTangent(
                vis_plug,
                edit=True,
                time=(t, t),
                inTangentType="stepnext",
                outTangentType="step",
            )

    @classmethod
    def _resolve_auto_fade(
        cls, obj, reference_time: float, spec: ChannelSpec = OPACITY
    ) -> bool:
        """Return ``True`` for fade-in, ``False`` for fade-out.

        Inspects the most recent key at or before *reference_time*.
        If its value is >= 0.5 (opaque), the object needs a fade-out.
        Defaults to fade-in when no previous key exists.
        """
        plug = cls._long_plug(obj, spec_for(spec).name)
        key_times = cmds.keyframe(plug, query=True, timeChange=True) or []
        prev_time = None
        for t in sorted(key_times):
            if t <= reference_time:
                prev_time = t
            else:
                break
        if prev_time is None:
            return True
        vals = cmds.keyframe(
            plug, query=True, time=(prev_time, prev_time), valueChange=True
        )
        if vals:
            return vals[0] < 0.5
        return True

    @classmethod
    def _remove_legacy_vis_driver(cls, obj):
        """Disconnect and delete any condition-node visibility driver.

        Handles condition nodes created by older versions of this module
        so that visibility is free for direct keyframing.
        """
        vis_plug = f"{obj}.visibility"
        if cmds.getAttr(vis_plug, lock=True):
            return
        inputs = cmds.listConnections(vis_plug, source=True, destination=False) or []
        if (
            inputs
            and cmds.objectType(inputs[0]) == "condition"
            and cls._VIS_DRIVER_RE.search(inputs[0].split("|")[-1].split(":")[-1])
        ):
            cmds.delete(inputs[0])
            try:
                cmds.setAttr(vis_plug, True)
            except Exception:
                pass

        # Also clean orphaned VisDrivers still connected via opacity
        if cmds.attributeQuery(cls.ATTR_NAME, node=obj, exists=True):
            conds = (
                cmds.listConnections(f"{obj}.{cls.ATTR_NAME}", type="condition") or []
            )
            for c in conds:
                if cls._VIS_DRIVER_RE.search(c.split("|")[-1].split(":")[-1]):
                    cmds.delete(c)

    # ------------------------------------------------------------------
    # Visibility mirror (the presence channel only)
    # ------------------------------------------------------------------

    @staticmethod
    def fade_windows(keys, eps: float = 1e-3) -> List[Tuple[float, float]]:
        """Reduce a DENSE opacity curve to the sparse visibility keys that
        bound each fade: ``[(time, 1.0|0.0), ...]``.

        A per-frame ramp (a baked expression, say) mirrored key-for-key gives
        the engines nothing to work with: Unity's opacity importer rebuilds a
        fade from consecutive OPPOSITE-value visibility keys — the gap is the
        fade window — so per-frame keys read as one-frame cuts. This keeps
        only the boundaries: for every run of zero opacity, the last
        fully-opaque key before it (fade-out start), the run's first and last
        keys, and the first fully-opaque key after it (fade-in end). The
        first key is always emitted so a ramp that never reaches zero still
        leaves the node with keyed visibility — the GLB route publishes a
        node's ``opacity`` ramp only beside a visibility track.

        Partial fades (a ramp that never hits zero) are therefore visible in
        the GLB, whose ramp is exact, and lost in Unity, whose fades are
        boolean-derived — the documented approximation.

        Parameters:
            keys: ``(time, value)`` pairs, any order.
            eps: Values at or below this are "zero"; at or above ``1 - eps``
                are "full".

        Returns:
            Sorted ``(time, 1.0 | 0.0)`` pairs (empty for no keys).
        """
        keys = sorted((float(t), float(v)) for t, v in keys)
        if not keys:
            return []
        n = len(keys)

        def visible(v):
            return v > eps

        def full(v):
            return v >= 1.0 - eps

        out = [(keys[0][0], 1.0 if visible(keys[0][1]) else 0.0)]
        i = 0
        while i < n:
            if visible(keys[i][1]):
                i += 1
                continue
            i0 = i
            while i + 1 < n and not visible(keys[i + 1][1]):
                i += 1
            i1 = i
            if i0 > 0:
                j = i0 - 1
                while j > 0 and not full(keys[j][1]):
                    j -= 1
                out.append((keys[j][0], 1.0))
                out.append((keys[i0][0], 0.0))
            if i1 < n - 1:
                k = i1 + 1
                while k < n - 1 and not full(keys[k][1]):
                    k += 1
                out.append((keys[i1][0], 0.0))
                out.append((keys[k][0], 1.0))
            elif i0 > 0 or i1 > i0:
                out.append((keys[i1][0], 0.0))
            i += 1

        merged: Dict[float, float] = {}
        for t, v in out:
            merged.setdefault(t, v)
        return sorted(merged.items())

    @classmethod
    def _visibility_matches(cls, vis_attr, desired, eps: float = 1e-4) -> bool:
        """Whether *vis_attr*'s curve already IS the stepped mirror *desired*.

        Compares key times, values and out-tangents — enough to tell a
        rebuild from a no-op.  In-tangents are not compared: they carry no
        meaning on a stepped boolean channel, and a mismatch there is not
        worth destroying the curve node over.

        Parameters:
            vis_attr: Full ``<longName>.visibility`` plug path.
            desired: ``[(time, 0.0|1.0), ...]`` the mirror would write.
            eps: Float tolerance for time and value comparison.
        """
        times = cmds.keyframe(vis_attr, q=True, tc=True) or []
        if len(times) != len(desired):
            return False
        values = cmds.keyframe(vis_attr, q=True, vc=True) or []
        if len(values) != len(desired):
            return False
        for (want_t, want_v), got_t, got_v in zip(desired, times, values):
            if abs(want_t - got_t) > eps or abs(want_v - got_v) > eps:
                return False
        out_tangents = cmds.keyTangent(vis_attr, q=True, outTangentType=True) or []
        return all(t == "step" for t in out_tangents)

    @classmethod
    def sync_visibility_from_opacity(cls, objects, windows: bool = False) -> None:
        """Create visibility keyframes that mirror the opacity animation curve.

        For each keyframe on ``opacity``, a matching keyframe is set on
        ``visibility`` with the same value and tangent type.  This produces
        real animation curves that FBX can export and game engines can read
        natively — unlike the deprecated condition-node approach which was
        Maya-only.

        Safe to call repeatedly: an object whose visibility curve already IS
        this mirror is skipped outright, so a no-op call writes nothing to the
        DG and the animCurve node survives (a rebuild deletes it, taking any
        Graph Editor key selection with it).  Otherwise the existing keys are
        cleared and rebuilt from the current opacity curve.

        Parameters:
            objects: Transforms carrying the ``opacity`` attribute.
            windows: Mirror only the fade BOUNDARIES (:meth:`fade_windows`)
                instead of every key — for a dense, baked ramp, whose
                per-frame mirror the engines would read as one-frame cuts.

        .. warning:: This replaces **all** visibility keyframes with those
           derived from opacity.  Any hand-keyed visibility animation that
           does not correspond to an opacity key will be lost.
        """
        for obj in cmds.ls(objects):
            if not cmds.attributeQuery(cls.ATTR_NAME, node=obj, exists=True):
                continue
            if cmds.getAttr(f"{obj}.visibility", lock=True):
                continue

            # Remove any legacy condition-node driver first
            cls._remove_legacy_vis_driver(obj)

            times = cmds.keyframe(obj, attribute=cls.ATTR_NAME, q=True, tc=True)
            if not times:
                continue
            values = cmds.keyframe(obj, attribute=cls.ATTR_NAME, q=True, vc=True)
            pairs = (
                cls.fade_windows(zip(times, values))
                if windows
                else list(zip(times, values))
            )

            # Visibility is a binary stepped channel — it mirrors the opacity
            # key TIMES but always uses stepped tangents (not the opacity
            # curve's tangents).
            vis_attr = cls._long_plug(obj, "visibility")
            desired = [(t, 1.0 if v > 0 else 0.0) for t, v in pairs]
            # Nothing to write when the curve already says exactly this.  The
            # rebuild below DELETES the animCurve node (``cutKey -clear``) and
            # creates a new one, so anything holding a reference to the old
            # node loses it — including a Graph Editor key selection.
            if cls._visibility_matches(vis_attr, desired):
                continue

            # Clear existing visibility keys so repeated calls don't
            # accumulate duplicates.  Use full DAG path to target the
            # transform only — short names also match the shape.
            cmds.cutKey(vis_attr, clear=True)

            # Maya rejects "step" for IN-tangents (wants "stepnext");
            # matches key_fade.
            for t, v in desired:
                cmds.setKeyframe(
                    vis_attr,
                    time=t,
                    value=v,
                    inTangentType="stepnext",
                    outTangentType="step",
                )

    @classmethod
    def ensure_connections(cls, objects) -> None:
        """Repair opacity → visibility mirroring on objects that already have
        the ``opacity`` attribute (e.g. after a duplicate operation).

        Removes any legacy condition-node driver, then mirrors the opacity
        curve onto ``visibility`` **only for objects whose visibility is
        unkeyed** — the case an object can actually be broken in.

        .. note:: This is a repair, not a re-sync: an object that already has
           a visibility curve is left alone.  Callers reach this from a
           ``SelectionChanged`` subscriber (``RenderOpacitySlots``), where
           rewriting the curve would silently discard hand-authored
           visibility animation and the sparse ``windows=True`` encoding
           :class:`~mayatk.rig_utils.shadow_rig.ShadowRig` writes — and would
           drop the user's Graph Editor key selection with it.  Call
           :meth:`sync_visibility_from_opacity` explicitly to re-mirror.
        """
        needs_mirror = []
        for obj in cmds.ls(objects):
            if not cmds.attributeQuery(cls.ATTR_NAME, node=obj, exists=True):
                continue
            if cmds.getAttr(f"{obj}.visibility", lock=True):
                continue
            cls._remove_legacy_vis_driver(obj)
            vis_attr = cls._long_plug(obj, "visibility")
            if not cmds.keyframe(vis_attr, q=True, keyframeCount=True):
                needs_mirror.append(obj)
        if needs_mirror:
            cls.sync_visibility_from_opacity(needs_mirror)

    @classmethod
    def remove(cls, objects, spec: Optional[ChannelSpec] = None):
        """Delete the channel's attribute(s) and their curves from *objects*.

        *spec* ``None`` removes every channel the object carries.
        """
        specs = [spec_for(spec)] if spec is not None else list(CHANNELS.values())
        for obj in objects:
            obj = str(obj)
            for one in specs:
                if not cls.has_channel(obj, one):
                    continue
                if one.drives_presence:
                    # Clean up any legacy condition-node visibility driver
                    cls._remove_legacy_vis_driver(obj)
                for attr in (one.name, one.color_attr):
                    if not attr or not cmds.attributeQuery(attr, node=obj, exists=True):
                        continue
                    # Delete anim curves first (deleteAttr errors on connected attrs)
                    curves = cmds.listConnections(f"{obj}.{attr}", type="animCurve")
                    if curves:
                        cmds.delete(curves)
                    cmds.deleteAttr(f"{obj}.{attr}")
                cls.logger.info(f"Removed {one.name} from {obj}")
