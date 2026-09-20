# !/usr/bin/python
# coding=utf-8
try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import math
from typing import Dict, List, Optional, Tuple
import pythontk as ptk


# From this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.env_utils.fbx_utils import FbxUtils
from mayatk.node_utils.data_nodes import DataNodes

# Import delegate classes
from mayatk.mat_utils.render_opacity.attribute_mode import OpacityAttributeMode
from mayatk.mat_utils.render_opacity.material_mode import OpacityMaterialMode
from mayatk.mat_utils.render_opacity.channels import (
    CHANNELS,
    ChannelSpec,
    spec_for,
)


class RenderEffects(ptk.LoggingMixin):
    """
    Per-object render-effect channels for engine-ready control.

    Adds a keyable float per effect (``opacity``, ``highlight``, ...) to
    object transforms -- one channel table
    (:mod:`~mayatk.mat_utils.render_opacity.channels`), one transport -- and
    optionally binds each object's material for live viewport feedback.
    ``opacity`` is the first channel and the one that drives *presence*
    (mirrors to ``visibility`` and gates the GLB); ``highlight`` is an
    additive emissive intensity with a per-object colour.

    .. note:: Use :meth:`key_fade` to animate an opacity fade with
              automatic visibility mirroring and :meth:`key_pulse` for a
              repeating highlight.  :meth:`create` sets up the mechanism
              (Attribute or Material binding) without keying.
              :meth:`prepare_for_export` runs before every FBX export
              (the ``FbxUtils.STAGERS`` bracket) and :meth:`finish_export`
              after it; call them yourself only around a raw ``cmds.file``.
              :meth:`export_record` is the ``visibility_tracks`` producer
              (``FbxUtils.PRODUCERS``).

    Two modes of operation:

    **mode="attribute"** (Recommended):
        Adds the channel's custom float (0-1) to object transforms.
        Use for per-object control in Game Engines.

    **mode="material"**:
        Also binds each object's material to the channel for viewport
        lookdev (the transparent StingrayPBS graph for opacity; the native
        emissive weight for a highlight). Bindings are suspended for the
        duration of an export so the deliverable carries the authored
        material, and re-bound after.

    ``RenderOpacity`` is this class under its previous name (one release).
    """

    ATTR_NAME = OpacityAttributeMode.ATTR_NAME
    CHANNELS = CHANNELS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Hand-off transfer: the channels as records the far side rebuilds
    # ------------------------------------------------------------------
    # Neither FBX nor USD animates a custom attribute, so a fade or a pulse
    # crossed the Blender bridge as nothing. The shot store's transfer carries
    # these records in its ``channels`` payload (``ptk.ShotTransfer``); a new
    # effect is a new row in ``CHANNELS`` and travels with no transport change,
    # and so does any other keyed user attribute.

    #: Attribute types a record carries: numeric scalars (a colour travels as
    #: its three leaves, which are these).
    _RECORD_TYPES = frozenset(
        {
            "double",
            "float",
            "long",
            "short",
            "byte",
            "bool",
            "doubleLinear",
            "doubleAngle",
        }
    )
    #: Out-tangent -> the transfer's interpolation (``ShotTransfer.KEY_INTERPOLATIONS``);
    #: anything else is ``"smooth"``.
    _INTERP_FROM_TANGENT = {"step": "step", "stepnext": "step", "linear": "linear"}
    #: The transfer's interpolation -> ``(inTangentType, outTangentType)``.
    _TANGENTS_FROM_INTERP = {
        "step": ("linear", "step"),
        "linear": ("linear", "linear"),
        "smooth": ("auto", "auto"),
    }

    @classmethod
    def channel_records(cls, objects=None) -> Dict[str, Dict[str, Dict]]:
        """``{long transform: {attribute: {"value", "keys"}}}`` -- every
        render-effect channel *objects* carry (keyed or not: a colour stop is
        a value) and every other keyed numeric user attribute, a key being
        ``[time, value, interpolation]``.

        Parameters:
            objects: The transforms to read (``None`` = every transform).
        """
        if cmds is None:
            return {}
        declared = {attr for spec in CHANNELS.values() for attr in spec.attrs}
        nodes = (
            cmds.ls(objects, long=True, type="transform")
            if objects is not None
            else cmds.ls(type="transform", long=True)
        )
        out: Dict[str, Dict[str, Dict]] = {}
        for node in nodes or []:
            records: Dict[str, Dict] = {}
            for attr in cmds.listAttr(node, userDefined=True) or []:
                plug = f"{node}.{attr}"
                try:
                    # A compound (the colour) is listed beside its leaves; the
                    # leaves are the records.
                    if cmds.attributeQuery(attr, node=node, numberOfChildren=True):
                        continue
                    if cmds.getAttr(plug, type=True) not in cls._RECORD_TYPES:
                        continue
                    value = float(cmds.getAttr(plug))
                except (RuntimeError, TypeError, ValueError):
                    continue
                keyed = cmds.listConnections(plug, type="animCurve", s=True, d=False)
                if not keyed and attr not in declared:
                    continue
                keys: List[List] = []
                if keyed:
                    times = cmds.keyframe(plug, query=True, timeChange=True) or []
                    values = cmds.keyframe(plug, query=True, valueChange=True) or []
                    outs = cmds.keyTangent(plug, query=True, outTangentType=True) or []
                    keys = [
                        [float(t), float(v), cls._INTERP_FROM_TANGENT.get(o, "smooth")]
                        for t, v, o in zip(times, values, outs)
                    ]
                records[attr] = {"value": value, "keys": keys}
            if records:
                out[node] = records
        return out

    @classmethod
    def apply_channel_records(cls, node: str, records: Dict[str, Dict]) -> int:
        """Land :meth:`channel_records` records on *node*; returns the attributes written.

        A declared channel is created through its own factory (the preset, so
        limits and keyability are the channel's); any other attribute lands as
        a keyable double. A locked or driven value is skipped, its keys still
        land. Times must already be on the scene's clock.
        """
        if cmds is None or not cmds.objExists(node):
            return 0
        declared = {attr: spec for spec in CHANNELS.values() for attr in spec.attrs}
        written = 0
        for attr, rec in (records or {}).items():
            plug = f"{node}.{attr}"
            spec = declared.get(attr)
            if spec is not None:
                # The preset adds what is missing and keeps what is there: an
                # FBX import may already have made a bare ``highlight`` from the
                # sender's custom property, with no colour compound beside it.
                if not cmds.objExists(plug):
                    # A compound the importer shaped its own way blocks the
                    # preset's: Maya's FBX importer spells a Blender vector
                    # property ``highlightColor0/1/2`` (measured), and
                    # ``ensure_attribute`` cannot add a leaf to it. The record
                    # carries every leaf's value, so nothing is lost by
                    # replacing it.
                    stems = spec.color_stops.keys if spec.color_stops else ()
                    stem = attr[:-1] if attr[-1:] in "RGB" else ""
                    if stem in stems and cmds.attributeQuery(
                        stem, node=node, exists=True
                    ):
                        cmds.deleteAttr(node, attribute=stem)
                    with CoreUtils.preserved_selection():
                        OpacityAttributeMode.create([node], spec)
                try:
                    cmds.setAttr(plug, keyable=True)
                except RuntimeError:
                    pass
            elif not cmds.attributeQuery(attr, node=node, exists=True):
                cmds.addAttr(node, longName=attr, attributeType="double", keyable=True)
            value = rec.get("value")
            if value is not None:
                try:
                    cmds.setAttr(plug, float(value))
                except (RuntimeError, TypeError, ValueError):
                    pass
            for key in rec.get("keys") or []:
                try:
                    time, val = float(key[0]), float(key[1])
                except (TypeError, ValueError, IndexError):
                    continue
                interp = key[2] if len(key) > 2 else "smooth"
                itt, ott = cls._TANGENTS_FROM_INTERP.get(
                    interp, cls._TANGENTS_FROM_INTERP["smooth"]
                )
                cmds.setKeyframe(
                    node,
                    attribute=attr,
                    time=time,
                    value=val,
                    inTangentType=itt,
                    outTangentType=ott,
                )
            written += 1
        return written

    @classmethod
    def objects_with_visibility_keys(cls, objects) -> List:
        """Return the subset of *objects* that have keyframes on visibility."""
        result = []
        for obj in objects:
            try:
                vis_plug = f"{(cmds.ls(obj, long=True) or [obj])[0]}.visibility"
                keys = cmds.keyframe(vis_plug, query=True, timeChange=True)
                if keys:
                    result.append(obj)
            except Exception:
                pass
        return result

    @classmethod
    @CoreUtils.undoable
    def create(
        cls,
        objects=None,
        mode: str = "attribute",
        delete_visibility_keys: bool = False,
        channel="opacity",
    ) -> Dict[str, Dict]:
        """Create a channel's attribute on *objects* (or remove it).

        Running this on objects that carry the retired material-mode preview
        heals them first (``OpacityMaterialMode.remove``).

        Parameters:
            objects: Objects to process. If None, uses selection.
            mode: ``"attribute"`` — Adds the channel attribute (Game Engine friendly).
                  ``"remove"``   — Removes the channel's artifacts from the objects.
                  ``"material"`` — DEPRECATED (2026-09-05, one release): the viewport
                  binding replaced the authored material; it is now the attribute
                  mode with a warning. Lookdev is the WebXR push.
            delete_visibility_keys: Presence channel only. If ``True``, existing
                visibility keyframes are deleted before creating the opacity
                setup.  If ``False`` (default), objects that have visibility
                keys are skipped and a warning is logged.
            channel: The channel name or :class:`ChannelSpec`; ``"opacity"``.

        Returns:
            dict: Results of the operation per object.

        Raises:
            RuntimeError: When *delete_visibility_keys* is ``False`` and one
                or more objects have visibility keyframes.
        """
        spec = spec_for(channel)
        if objects is None:
            objects = cmds.ls(selection=True) or []
        if not objects:
            cls.logger.warning("No objects selected.")
            return {}

        # --- Handle existing visibility keys (presence channel only) ---
        vis_keyed = (
            cls.objects_with_visibility_keys(objects) if spec.drives_presence else []
        )
        if vis_keyed:
            names = [o.split("|")[-1].split(":")[-1] for o in vis_keyed]
            if delete_visibility_keys:
                for obj in vis_keyed:
                    vis_plug = f"{(cmds.ls(obj, long=True) or [obj])[0]}.visibility"
                    cmds.cutKey(vis_plug, clear=True)
                    # Reset visibility to on after removing keys
                    cmds.setAttr(vis_plug, True)
                cls.logger.info("Deleted visibility keys on: %s", ", ".join(names))
            else:
                msg = (
                    f"Visibility keys found on: {', '.join(names)}. "
                    "Enable 'Delete Visibility Keys' or remove them "
                    "manually before applying opacity."
                )
                raise RuntimeError(msg)

        if mode == "material":
            cls._warn_preview_retired()
            mode = "attribute"
        # Always clean existing state first (legacy material-mode artifacts
        # before the attribute: the disconnect needs the attribute to exist).
        with CoreUtils.preserved_selection():
            cls.remove(objects, channel=spec)
            if mode == "remove":
                return {}
            elif mode == "attribute":
                return OpacityAttributeMode.create(objects, spec)
        cls.logger.error(f"Unknown mode: {mode}")
        return {}

    @classmethod
    def _warn_preview_retired(cls) -> None:
        """One line, once per session: the in-scene preview is gone, and why."""
        if getattr(cls, "_preview_warned", False):
            return
        cls._preview_warned = True
        cls.logger.warning(
            "The viewport material preview was retired (2026-09-05): it replaced "
            "the authored material and cost every export a restore step. Keys are "
            "written as before; preview the deliverable with the WebXR push."
        )

    @classmethod
    def preview(cls, objects=None, channel="highlight", enabled: bool = True) -> Dict:
        """DEPRECATED (one release). ``enabled=False`` heals a scene saved with the
        old preview on (``OpacityMaterialMode.remove``); ``True`` warns and does
        nothing -- the attribute and its keys are the whole authoring now."""
        spec = spec_for(channel)
        if objects is None:
            objects = cmds.ls(selection=True) or []
        if not objects:
            return {}
        if enabled:
            cls._warn_preview_retired()
            return {}
        with CoreUtils.preserved_selection():
            OpacityMaterialMode.remove(objects, spec)
        return {}

    # Legacy alias support
    setup = create

    @classmethod
    def ensure_connections(cls, objects=None) -> None:
        """Re-establish opacity driver connections on objects that already
        have the ``opacity`` attribute but lost their wiring — typically
        after a **Duplicate** operation in Maya.

        Lightweight and non-destructive; safe to call before every keyframe
        operation and from a ``SelectionChanged`` subscriber.  It mirrors
        opacity onto ``visibility`` only where visibility is unkeyed — an
        existing visibility curve is never rewritten (use
        :meth:`sync_visibility_from_opacity` for that).

        Parameters:
            objects: Objects to check. If *None*, uses the current selection.
        """
        if objects is None:
            objects = cmds.ls(selection=True) or []
        if not objects:
            return
        OpacityAttributeMode.ensure_connections(objects)

    @classmethod
    def sync_visibility_from_opacity(cls, objects=None) -> None:
        """Create visibility keyframes mirroring opacity animation curves.

        Delegates to :meth:`OpacityAttributeMode.sync_visibility_from_opacity`.
        Call after manually keying ``opacity`` to ensure the ``visibility``
        channel has matching keyframes for FBX export.

        Parameters:
            objects: Objects to sync. If *None*, uses the current selection.
        """
        if objects is None:
            objects = cmds.ls(selection=True) or []
        if not objects:
            return
        OpacityAttributeMode.sync_visibility_from_opacity(objects)

    @classmethod
    def key_fade(
        cls,
        objects=None,
        start: float = 0,
        end: float = 15,
        direction: str = "in",
        auto_create: bool = True,
        tangent: str = "linear",
        preview: Optional[bool] = None,
        delete_visibility_keys: bool = False,
        channel="opacity",
        whole_frames: bool = True,
    ) -> List[Tuple[str, str]]:
        """Key a two-key ramp on a channel; the presence channel mirrors to visibility.

        Convenience wrapper around :meth:`OpacityAttributeMode.key_fade` that
        also owns channel creation (see :meth:`_ensure_channel`), so a caller
        keys in one step -- the panel has no separate Create action.

        Parameters:
            objects: Maya nodes. If *None*, uses the current selection.
            start: First frame of the fade.
            end: Last frame of the fade.
            direction: ``"in"`` (0→1), ``"out"`` (1→0), or ``"auto"``.
            auto_create: Create the channel on objects that lack it.
            tangent: Tangent type for the channel's keys (default ``"linear"``).
            preview: DEPRECATED, ignored (one release) -- the viewport binding
                was retired 2026-09-05; ``True`` logs why, once.
            delete_visibility_keys: Presence channel, objects being created
                only -- clear their existing visibility keys first. Otherwise
                the mirror is written over them.
            channel: The channel name or :class:`ChannelSpec`; ``"opacity"``.
            whole_frames: Snap the keys to whole frames (the default); see
                :meth:`OpacityAttributeMode.key_fade`.

        Returns:
            List of ``(object_name, "in"|"out")`` per keyed object.
        """
        spec = spec_for(channel)
        objects = cls._selection_or(objects)
        if not objects:
            return []
        cls._ensure_channel(objects, spec, auto_create, preview, delete_visibility_keys)
        return OpacityAttributeMode.key_fade(
            objects,
            start=start,
            end=end,
            direction=direction,
            auto_create=False,
            tangent=tangent,
            spec=spec,
            whole_frames=whole_frames,
        )

    @classmethod
    def _selection_or(cls, objects) -> List[str]:
        """*objects*, or the selection when *None*; warns when that is empty."""
        if objects is None:
            objects = cmds.ls(selection=True) or []
        if not objects:
            cls.logger.warning("No objects selected.")
        return list(objects)

    @classmethod
    def _ensure_channel(
        cls,
        objects,
        spec: ChannelSpec,
        auto_create: bool,
        preview: Optional[bool],
        delete_visibility_keys: bool,
    ) -> None:
        """Give *objects* the channel before keying.

        Objects lacking the attribute get it; with *delete_visibility_keys* the
        presence channel's create path clears their visibility keys first
        (:meth:`create`'s guard), otherwise the unguarded attribute-mode create
        runs and the keying mirror writes over whatever is there. *preview* is
        the retired viewport binding's kwarg: honoured as a warning, nothing more.
        """
        if preview:
            cls._warn_preview_retired()
        # The attribute-mode create touches ``data_internal``; the selection
        # must not end up on it -- the next tool would act on that node.
        with CoreUtils.preserved_selection():
            if auto_create:
                missing = [
                    o for o in objects if not OpacityAttributeMode.has_channel(o, spec)
                ]
                if missing and delete_visibility_keys and spec.drives_presence:
                    cls.create(
                        missing,
                        mode="attribute",
                        delete_visibility_keys=True,
                        channel=spec,
                    )
                elif missing:
                    OpacityAttributeMode.create(missing, spec)

    @classmethod
    def prepare_for_export(cls, objects=None) -> List[str]:
        """Stage the curve-proxy transport for an FBX write; write nothing else.

        One transient child per keyed channel carries the per-object curve to
        engines that flatten custom-property animation
        (:meth:`stage_export_proxies`); :meth:`finish_export` removes them after
        the write. The scene's animation is not touched: the GLB derives
        presence from the authored channels itself
        (``ptk.MeshConvert.apply_glb_visibility``), so an opacity keyed by hand
        gets no visibility mirror here. The missing-mirror repair this method
        used to run made what the track walk published depend on which producer
        ran first, and keyed the artist's scene during an export.

        Parameters:
            objects: Ignored, kept for API compatibility for one release -- the
                staging covers every keyed channel in the scene.

        Returns:
            An empty list, kept for API compatibility for one release (it named
            the objects whose visibility was re-synced).
        """
        # Nor the materials: keying never binds them (the viewport preview that
        # did was retired 2026-09-05), so the FBX and the sidecar read the scene
        # as authored with no restore step.
        cls.stage_export_proxies()
        return []

    @classmethod
    def finish_export(cls) -> None:
        """Undo :meth:`prepare_for_export`'s staging (the ``finish`` half of the
        ``"render_effects"`` row in ``FbxUtils.STAGERS``).

        Idempotent: with nothing staged it changes nothing.
        """
        cls.remove_export_proxies()

    # ------------------------------------------------------------------
    # Curve-proxy transport (FBX -> Unity)
    # ------------------------------------------------------------------

    #: FBX user property stamped on every proxy; the GLB conversion strips
    #: nodes carrying it (``MeshConvert.strip_glb_curve_proxies``) and the
    #: Unity importer rebinds and deletes them.
    PROXY_MARKER = ptk.MeshConvert.CURVE_PROXY_MARKER
    #: ``<node leaf>__<channel>``: the Unity importer parses the parent path
    #: and the channel name off the leaf.
    PROXY_SEPARATOR = "__"

    @classmethod
    def stage_export_proxies(cls) -> List[str]:
        """Stage one transient child transform per keyed channel, for the FBX write.

        Unity imports animated custom properties flattened onto the root
        Animator with EMPTY paths, so several objects' ``opacity`` (or
        ``highlight``) curves collapse into indistinguishable bindings; the
        historical workaround reconstructs a fade from the stepped visibility
        mirror, which is lossy and has no analogue for a pulse. Object
        transform animation, by contrast, is a channel every FBX consumer
        bakes natively with its hierarchy path intact. So each keyed channel
        gets a child transform named ``<leaf>__<channel>`` whose ``scale.x``
        carries the curve -- scale because it is unitless (translation picks
        up unit conversion, rotation its degree handling; measured: the
        proxy's 0->1 arrived in the FBX exactly). Blender's emissive-groups
        transport is the same idiom.

        Strictly export-transient: stamped with :attr:`PROXY_MARKER` and
        removed by :meth:`remove_export_proxies`. Stale proxies from an
        interrupted export are pre-cleaned here.

        Returns:
            The created proxy transforms' long names.
        """
        cls.remove_export_proxies()  # stale pre-clean (idempotent)
        proxies: List[str] = []
        for node, plug, spec in cls._channel_curves():
            leaf = node.split("|")[-1].split(":")[-1]
            name = f"{leaf}{cls.PROXY_SEPARATOR}{spec.name}"
            if cmds.objExists(name):
                cls.logger.warning(
                    "Curve-proxy name %r is taken by an existing object -- %s's "
                    "%s animation will not ship this export.",
                    name,
                    leaf,
                    spec.name,
                )
                continue
            proxy = cmds.createNode(
                "transform", name=name, parent=node, skipSelect=True
            )
            proxy = (cmds.ls(proxy, long=True) or [proxy])[0]
            cmds.addAttr(proxy, longName=cls.PROXY_MARKER, attributeType="bool")
            cmds.setAttr(f"{proxy}.{cls.PROXY_MARKER}", True)
            # Copy the curve, tangents included, onto scaleX. copyKey/pasteKey
            # keeps the authored shape; a baked curve would be per-frame.
            cmds.copyKey(plug)
            cmds.pasteKey(f"{proxy}.scaleX", option="replaceCompletely")
            proxies.append(proxy)
        if proxies:
            cls.logger.info(
                "Staged %d curve prox%s: %s",
                len(proxies),
                "y" if len(proxies) == 1 else "ies",
                ", ".join(p.split("|")[-1] for p in proxies),
            )
        return proxies

    @classmethod
    def remove_export_proxies(cls) -> List[str]:
        """Delete every staged curve proxy (marker-matched). Idempotent."""
        removed: List[str] = []
        # recursive: ``*`` must match into namespaces (a referenced module's proxies).
        for node in (
            cmds.ls(
                f"*.{cls.PROXY_MARKER}", objectsOnly=True, long=True, recursive=True
            )
            or []
        ):
            if cmds.objExists(node):
                removed.append(node.split("|")[-1])
                cmds.delete(node)
        if removed:
            cls.logger.debug("Removed curve prox(ies): %s", removed)
        return removed

    # ------------------------------------------------------------------
    # Keying
    # ------------------------------------------------------------------

    @classmethod
    def key_pulse(
        cls,
        objects=None,
        start: float = 0,
        end: float = 100,
        period: float = 86,
        bright_fraction: float = 0.59,
        ramp_fraction: float = 0.25,
        lead_in: Optional[float] = None,
        lead_out: Optional[float] = None,
        color=None,
        dim_color=None,
        auto_create: bool = True,
        channel="highlight",
        preview: Optional[bool] = None,
        delete_visibility_keys: bool = False,
        whole_frames: bool = True,
    ) -> List[str]:
        """Key a repeating bright/dim pulse on a channel over ``start..end``.

        Convenience wrapper around :meth:`OpacityAttributeMode.key_pulse` that
        also owns channel creation (see :meth:`_ensure_channel`). The defaults
        are the cadence measured on the WebXR reference at 30 fps: a 2.86 s
        period (86 frames), bright for 59% of it. The pulse is bracketed by dim
        keys at both ends -- see the writer for why that is load-bearing rather
        than cosmetic.

        Parameters:
            objects: Maya nodes. If *None*, uses the current selection.
            start: First frame of the pulse; the channel is dim here.
            end: Last frame of the pulse; the channel is dim here too.
            period: One cycle, in frames.
            bright_fraction: Share of the cycle spent bright.
            ramp_fraction: Share of the cycle spent in each transition.
            lead_in: Frames the pulse takes to come up from dim at *start*.
                None takes the cycle's own ramp; 0 cuts as hard as the
                floor allows (one frame under *whole_frames*).
            lead_out: The same at *end*, going back down to dim.
            color: Optional ``(r, g, b)`` for the BRIGHT end of the channel's
                colour ramp -- what the object reads at intensity 1.
            dim_color: Optional ``(r, g, b)`` for the other end, read at
                intensity 0. Leaving it ``None`` keeps whatever the object
                carries, black on a freshly created channel, so the pulse
                fades to unlit exactly as it did before this end existed.
            auto_create: Create the channel on objects that lack it.
            channel: The channel name or spec; ``"highlight"``.
            preview: DEPRECATED, ignored (one release) -- see :meth:`key_fade`.
            delete_visibility_keys: See :meth:`key_fade`; no effect unless the
                channel drives presence.
            whole_frames: Snap every key to a whole frame (the default); see
                :meth:`OpacityAttributeMode.key_pulse`.

        Returns:
            The keyed objects' short names.
        """
        spec = spec_for(channel)
        objects = cls._selection_or(objects)
        if not objects:
            return []
        cls._ensure_channel(objects, spec, auto_create, preview, delete_visibility_keys)
        return OpacityAttributeMode.key_pulse(
            objects,
            start=start,
            end=end,
            period=period,
            bright_fraction=bright_fraction,
            ramp_fraction=ramp_fraction,
            lead_in=lead_in,
            lead_out=lead_out,
            color=color,
            dim_color=dim_color,
            auto_create=False,
            spec=spec,
            whole_frames=whole_frames,
        )

    @classmethod
    def preview_channels(
        cls,
        objects,
        channel="highlight",
        keys=(),
        colors=None,
        fps: Optional[float] = None,
    ) -> Dict:
        """The WebXR-push overlay that previews one effect on *objects* at *keys*.

        Nothing is created, keyed or coloured -- the preview never touches the
        scene. The objects' names go into
        ``ptk.MeshConvert.effect_preview_channels``, and the result goes to
        ``WebXrPreview.push(data_export=...)``, which builds the GLB exactly as
        if these keys had been authored. Whatever the objects already carry is
        left out of that build, so the page shows this effect alone.

        Parameters:
            objects: Maya nodes; shapes resolve to their transforms.
            channel: The channel name or spec; ``"highlight"``.
            keys: ``[(frame, value), ...]`` -- a ``ptk.RampKeys`` plan.
            colors: ``(bright, dim)`` for a coloured channel; ``None`` in either
                place leaves that end to the reader's default.
            fps: The rate *keys* are quoted in; the scene's when ``None``.

        Returns:
            ``{data_export channel: value}``.

        Raises:
            ValueError: No object resolved, or fewer than two keys.
        """
        from mayatk.node_utils._node_utils import NodeUtils

        spec = spec_for(channel)
        transforms = NodeUtils.get_transform_node(list(objects or [])) or []
        # The leaf the producer publishes (``visibility_tracks``) and the GLB
        # node is named: no DAG path, no namespace.
        nodes = [str(t).split("|")[-1].split(":")[-1] for t in transforms]
        return ptk.MeshConvert.effect_preview_channels(
            nodes,
            spec.name,
            keys,
            colors=colors,
            fps=fps or cls._scene_fps() or 30.0,
        )

    # ------------------------------------------------------------------
    # Colour revision
    # ------------------------------------------------------------------

    @classmethod
    def objects_with_channel(cls, channel="highlight") -> List[str]:
        """Every transform in the scene carrying the channel's attribute.

        Parameters:
            channel: The channel name or spec; ``"highlight"``.

        Returns:
            Long names, in scene order.
        """
        spec = spec_for(channel)
        return [
            obj
            for obj in (cmds.ls(type="transform", long=True) or [])
            if OpacityAttributeMode.has_channel(obj, spec)
        ]

    @classmethod
    def channel_colors(
        cls, objects=None, channel="highlight", stop: str = "hi"
    ) -> Dict[str, Tuple]:
        """What each object's channel colour is authored as right now.

        The read half of :meth:`set_channel_color` -- what a revision starts
        from, and what proves one landed.

        Parameters:
            objects: Nodes to read. ``None`` reads every object in the scene
                that carries the channel.
            channel: The channel name or spec; ``"highlight"``.
            stop: Which end of the ramp to read -- ``"hi"`` (the default) or
                ``"lo"``.

        Returns:
            ``{long name: (r, g, b)}``, skipping objects without the channel.
        """
        spec = spec_for(channel)
        if objects is None:
            objects = cls.objects_with_channel(spec)
        colors: Dict[str, Tuple] = {}
        for obj in cmds.ls(objects, long=True) or []:
            color = OpacityAttributeMode.get_color(obj, spec, stop)
            if color is not None:
                colors[obj] = color
        return colors

    @classmethod
    def channel_color_stops(cls, objects=None, channel="highlight") -> Dict[str, Tuple]:
        """Both ends of each object's colour ramp, high first.

        What an editor showing the two ends side by side seeds from: reading
        the pair in ONE pass is what lets it tell a mixed selection from an
        agreeing one without walking the scene twice. An end the object does
        not carry reads ``None``.

        Returns:
            ``{long name: ((r, g, b) | None, ...)}``, skipping objects without
            the channel.
        """
        spec = spec_for(channel)
        if objects is None:
            objects = cls.objects_with_channel(spec)
        out: Dict[str, Tuple] = {}
        for obj in cmds.ls(objects, long=True) or []:
            stops = OpacityAttributeMode.get_color_stops(obj, spec)
            if any(c is not None for c in stops):
                out[obj] = stops
        return out

    @classmethod
    def set_channel_color(
        cls, objects=None, color=None, channel="highlight", stop: str = "hi"
    ) -> List[str]:
        """Restate an already-authored channel colour, leaving its keys alone.

        The revision path for a look signed off after the pulses were keyed.
        The colour lives on its own attribute rather than in the curve, so it
        can be rewritten at any time and the animation is untouched -- which is
        what makes a scene-wide recolour a one-liner instead of a re-key.

        Parameters:
            objects: Nodes to write. ``None`` takes the selection, and falls
                back to every object in the scene carrying the channel when
                nothing is selected -- the scene-wide revision this exists for.
            color: ``(r, g, b)``, linear 0-1. Required.
            channel: The channel name or spec; ``"highlight"``.
            stop: Which end of the ramp to write -- ``"hi"`` (the default) or
                ``"lo"``.

        Returns:
            The short names of the objects written.

        Raises:
            ValueError: When *color* is missing, or the channel has no colour.
        """
        spec = spec_for(channel)
        if color is None:
            raise ValueError("A colour is required.")
        if objects is None:
            objects = cmds.ls(selection=True) or cls.objects_with_channel(spec)
            if not objects:
                cls.logger.warning(f"No objects carry the {spec.name} channel.")
                return []
        with CoreUtils.preserved_selection():
            written = OpacityAttributeMode.set_color(objects, color, spec, stop)
        cls.logger.info(
            "Set %s colour to (%s) on %d object(s).",
            spec.name,
            ", ".join(f"{c:.3f}" for c in tuple(color)[:3]),
            len(written),
        )
        return written

    # ------------------------------------------------------------------
    # In-band export metadata — the glTF route
    # ------------------------------------------------------------------

    #: ``data_export`` channel read by ``ptk.MeshConvert.apply_glb_visibility``
    #: -- the key of the ``ptk.SceneRecords.VISIBILITY`` record.
    DATA_CHANNEL = ptk.SceneRecords.VISIBILITY.key
    #: Schema this producer writes (stamped by the declaration); the reader
    #: refuses anything newer.
    SCHEMA_VERSION = ptk.SceneRecords.VISIBILITY.version

    @classmethod
    def visibility_tracks(cls) -> List[Dict]:
        """Every keyed-visibility transform in the scene, as stepped on/off tracks.

        Emits what a DCC-agnostic consumer can use without knowing Maya: the
        boolean timeline, already evaluated under Maya's own rules, plus the
        authored ``opacity`` ramp when there is one.  Doing the evaluation here
        is the point of the split — visibility is a *boolean* attribute driven
        by a float curve, so what a non-step curve means is a Maya question,
        and the answer must not be re-guessed downstream.
        """
        tracks: List[Dict] = []
        visibility = cls._visibility_curves()
        channels: Dict[str, Dict[str, ChannelSpec]] = {}
        for node, _plug, spec in cls._channel_curves():
            channels.setdefault(node, {})[spec.name] = spec
        for node in sorted(set(visibility) | set(channels)):
            track: Dict = {"node": node.split("|")[-1].split(":")[-1]}
            if node in visibility:
                keys = cls._stepped_track(visibility[node])
                if keys:
                    track["visibility"] = keys
            for name, spec in sorted(channels.get(node, {}).items()):
                # Linearized, not raw: the ramp's consumers interpolate it
                # linearly and Maya's own curve often does not (see
                # :meth:`_linear_ramp`).
                ramp = cls._linear_ramp(f"{node}.{name}")
                if not ramp:
                    continue
                track[name] = ramp
                track_keys = spec.track_color_stops
                if track_keys is None:
                    continue
                # One published key per stop the node actually carries. A node
                # missing a stop states nothing for it, so the reader falls
                # back to THAT stop's default rather than to the other end.
                for attr, key in zip(spec.color_stops.keys, track_keys.keys):
                    plug = f"{node}.{attr}"
                    if not cmds.objExists(plug):
                        continue
                    try:
                        rgb = cmds.getAttr(plug)[0]
                        track[key] = [float(c) for c in rgb[:3]]
                    except (RuntimeError, TypeError, IndexError):
                        pass
            if len(track) > 1:
                tracks.append(track)
        return tracks

    @classmethod
    def export_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]:
        """The ``visibility_tracks`` record for this scene, or ``None`` when it
        has no keyed visibility -- the ``ptk.SceneRecords.VISIBILITY`` producer
        (``FbxUtils.PRODUCERS``).  Pure: it reads the curves and the shot
        record and never writes.

        Exists because keyed visibility is the one animated channel that does
        NOT survive to glTF: the format animates translation, rotation, scale
        and morph weights, and nothing else, so an FBX's ``Visibility`` curves
        are dropped in the conversion without a word.
        ``MeshConvert.apply_glb_visibility`` rebuilds them from this channel
        as stepped scale, which every viewer plays.

        Also publishes ``clip_span`` — per take, the first and last authored
        frame inside its window.  That is the take's own zero: the converter
        rebases a clip onto its first authored key rather than onto the take's
        declared start, and it counts the visibility keys when deciding which
        key is first even though it emits no channel for them.  Only this side
        can see the curves, so only this side can say.  The whole-timeline
        entry is the exporter's ``ctx.clip_span`` when the pipeline measured
        one (the first and last frame the stack CARRIES), else the FBX bake
        range as a seed.

        Parameters:
            ctx: The export's decisions (``clip_span``) and the records
                produced before this one -- the shot record's ``fps`` and the
                takes its clips declare (``ptk.SceneRecords.declared_takes``);
                the stored records are read when this assembly did not produce
                them (an authoring-time republish).

        Returns:
            The record, or ``None`` when there is no keyed visibility (the
            publisher then clears the channel).
        """
        # Bail BEFORE the span walk: that reads every anim curve in the scene,
        # and a scene with no keyed visibility has nothing to spend it on.
        tracks = cls.visibility_tracks()
        if not tracks:
            return None

        # The shot record the shots producer has just built (it runs first:
        # the record declares ``after=("shot_metadata",)``), else the stored
        # one -- it keeps the frame rate defined in ONE place for the export.
        metadata = ctx.record(ptk.SceneRecords.SHOTS, DataNodes)
        if not isinstance(metadata, dict):
            metadata = {}
        # The scene's own rate when the shots producer published none (a
        # shot-less scene CLEARS shot_metadata): without a rate the GLB
        # appliers cannot place the frames in time and drop every track and
        # ramp -- measured 2026-09-02, "carry no frame rate ... not applied".
        fps = metadata.get("fps") or cls._scene_fps()
        takes = ptk.SceneRecords.declared_takes(lambda key: ctx.record(key, DataNodes))
        # The stack's origin.  Measured by the pipeline (``ctx.clip_span``)
        # once it has seen the final curves; until then the bake range is a
        # SEED, not the answer, and a producer cannot do better: it has no
        # export set and no view of the final curves, so it reads whatever
        # the FBX preset happens to hold. The bake range bounds only what the
        # plugin RE-BAKES, while an authored curve is written whole (a curve
        # keyed 0-100 exports as 0-100 under a 20-80 range), so as a
        # description of the stack it is simply wrong. The seed survives only
        # where no export pipeline runs (a hand-driven FBX write), or on a
        # scene with no exported keys, where there is no stack to misplace.
        stack_range = ctx.clip_span or FbxUtils.bake_range()
        payload = ptk.MeshConvert.build_visibility_tracks(
            tracks,
            fps=fps,
            clip_spans=ptk.MeshConvert.clip_spans(
                (),
                takes,
                key_spans=cls._scene_key_spans,
                stack_range=stack_range,
            ),
        )
        if payload is None:
            return None
        return ptk.SceneRecords.VISIBILITY.make(payload)

    @classmethod
    def refresh_export_metadata(cls) -> Optional[str]:
        """Republish the ``visibility_tracks`` channel on the ``data_export`` carrier.

        The authoring-time publish of :meth:`export_record`, committed through
        ``FbxUtils.publish_authored`` (an export pipeline runs the producer
        itself: ``FbxUtils.PRODUCERS``).  Clears the channel when the scene
        has no keyed visibility, leaving no empty carrier behind.

        Returns:
            The published JSON string, or ``None`` when cleared.
        """
        record = cls._publish_record(
            ptk.ExportContext(mode=ptk.ExportContext.AUTHORING)
        )
        if record is None:
            return None
        cls.logger.info(
            "Visibility: published %d keyed-visibility track(s) for the GLB "
            "route (glTF drops the FBX's own visibility curves).",
            len(record.payload.get("tracks") or []),
        )
        return record.text

    @classmethod
    def _publish_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]:
        """:meth:`export_record` under *ctx*, committed at authoring time
        (``FbxUtils.publish_authored``) -- the channel CLEARED when the scene
        has no keyed visibility, so a stale one never outlives its curves.
        The one publish path of this record outside an export pipeline."""
        record = cls.export_record(ctx)
        FbxUtils.publish_authored({ptk.SceneRecords.VISIBILITY: record})
        return record


    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _scene_fps():
        """The scene frame rate through the shared reader, or None."""
        from mayatk.audio_utils._audio_utils import AudioUtils

        try:
            return float(AudioUtils.get_fps()) or None
        except Exception:
            return None

    @staticmethod
    def _visibility_curves() -> Dict[str, str]:
        """``long node name -> visibility plug`` for every keyed transform.

        Walked from the anim curves rather than from the transforms: a scene
        has thousands of the latter and a handful of the former, and a query
        per transform is what makes a pre-export hook felt.

        The cost of that direction is its one blind spot: a curve reaching
        ``visibility`` THROUGH an animation layer or a ``pairBlend`` lands on
        the blend node, not on the transform, so such a node publishes no track
        and ships visible.  Accepted rather than paid for -- every writer in
        this pipeline (:meth:`key_fade`, the shot behaviors,
        :meth:`sync_visibility_from_opacity`) keys the plug directly, and the
        alternative is a per-transform query on every export.
        """
        found: Dict[str, str] = {}
        for _curve, plug in RenderEffects._curve_outputs():
            node, _, attr = plug.partition(".")
            if attr != "visibility" or not cmds.objExists(node):
                continue
            long_name = (cmds.ls(node, long=True) or [node])[0]
            found[long_name] = f"{long_name}.visibility"
        return found

    @staticmethod
    def _curve_outputs() -> List[Tuple[str, str]]:
        """``(curve, driven plug)`` for every scene animation curve's
        ``output`` connection -- ONE call.

        The curve walks (visibility, channels, the take spans) asked
        ``listConnections`` once per curve; a scene mid-export carries
        thousands (the bake's layer curves and the flatten's fitted ones on
        top of the authored set), and each call is a full command round-trip.
        One ``connections=True`` query over the whole list returns the pairs.
        """
        curves = cmds.ls(type="animCurve", long=True) or []
        if not curves:
            return []
        pairs = (
            cmds.listConnections(
                curves, plugs=True, connections=True, source=False, destination=True
            )
            or []
        )
        return [
            (src.rsplit(".", 1)[0], dst)
            for src, dst in zip(pairs[::2], pairs[1::2])
            if src.endswith(".output")
        ]

    @classmethod
    def _channel_curves(cls) -> List[Tuple[str, str, ChannelSpec]]:
        """``(long node, plug, spec)`` for every keyed render-effect channel.

        Walked from the anim curves like :meth:`_visibility_curves`, and with
        the same blind spot (a curve reaching the plug through a layer or a
        ``pairBlend`` is not seen), for the same reason.
        """
        found: List[Tuple[str, str, ChannelSpec]] = []
        seen = set()
        for _curve, plug in cls._curve_outputs():
            node, _, attr = plug.partition(".")
            spec = CHANNELS.get(attr)
            if spec is None or not cmds.objExists(node):
                continue
            # isAType: a joint or any transform subtype carries the attr too.
            if not cmds.objectType(node, isAType="transform"):
                continue
            long_name = (cmds.ls(node, long=True) or [node])[0]
            if (long_name, attr) in seen:
                continue
            seen.add((long_name, attr))
            found.append((long_name, f"{long_name}.{attr}", spec))
        return found

    @staticmethod
    def _curve_keys(plug: str) -> List[List[float]]:
        """``[[frame, value], ...]`` for *plug*, or ``[]``."""
        try:
            times = cmds.keyframe(plug, query=True, timeChange=True) or []
            values = cmds.keyframe(plug, query=True, valueChange=True) or []
        except Exception:
            return []
        return [[float(t), float(v)] for t, v in zip(times, values)]

    #: How much of a frame a STEP's jump is given when it is linearized. Small
    #: enough to be invisible at any playback rate, large enough to survive the
    #: float32 sampler buffers the ramp ends up in.
    _STEP_JUMP = 0.01

    @classmethod
    def _linear_ramp(cls, plug: str) -> List[List[float]]:
        """*plug*'s curve as keys a LINEAR consumer reproduces exactly.

        The ramp is published as ``[frame, alpha]`` pairs and every consumer
        interpolates them linearly -- which is only faithful while the Maya
        curve does too. Mixed tangents are the norm rather than the exception:
        measured on a production assembly, ``REPAIRED_CMPT_LOC.opacity`` reads
        ``linear, step, step, linear``, so Maya HOLDS it at 1.0 from frame 23
        to 1983 and cuts, while a linear reading of the same four keys invents
        a fifteen-frame fade-out that the scene does not have.

        So a stepped segment is made explicit: the hold is stated as its own
        key and the jump is given :attr:`_STEP_JUMP` of a frame. ``stepnext``
        is the mirror image -- it takes the FOLLOWING key's value immediately,
        so the jump goes at the front instead.

        Only tangents matter here, not tangent WEIGHTS: a weighted linear
        segment is still a straight line between its keys, and the curved
        tangent types (auto/spline/clamped) are not something this pipeline's
        writers produce on an alpha ramp.
        """
        keys = cls._curve_keys(plug)
        if len(keys) < 2:
            return keys
        try:
            tangents = cmds.keyTangent(plug, query=True, outTangentType=True) or []
        except Exception:
            return keys
        out: List[List[float]] = []
        for index, (time, value) in enumerate(keys):
            out.append([time, value])
            if index + 1 >= len(keys) or index >= len(tangents):
                continue
            next_time, next_value = keys[index + 1]
            if next_time - time <= cls._STEP_JUMP:
                continue  # no room to state a hold in
            if tangents[index] == "step":
                out.append([next_time - cls._STEP_JUMP, value])
            elif tangents[index] == "stepnext":
                out.append([time + cls._STEP_JUMP, next_value])
        return out

    @classmethod
    def _stepped_track(cls, plug: str) -> List[List[float]]:
        """*plug*'s boolean timeline as ``[[frame, 0|1], ...]``.

        ``visibility`` is a BOOLEAN fed by a float curve, and what a given
        curve means is a Maya question rather than a general one — which is
        the whole reason this evaluation happens here and not downstream.

        A stepped curve — what :meth:`key_fade` and the shot behaviors write —
        switches exactly at its keys, so its keys ARE the timeline.  Anything
        else is EVALUATED rather than guessed: measured on Maya 2025, a linear
        curve on this plug still steps (the tangents are accepted and ignored),
        which is not what either plausible reading of "linear" predicts.  The
        sampling is bounded by the curve's own key span, and the common path
        never reaches it.
        """
        keys = cls._curve_keys(plug)
        if not keys:
            return []
        try:
            tangents = cmds.keyTangent(plug, query=True, outTangentType=True) or []
        except Exception:
            tangents = []
        if tangents and all(t in ("step", "stepnext") for t in tangents):
            return [[frame, 1.0 if value >= 0.5 else 0.0] for frame, value in keys]

        first, last = int(math.floor(keys[0][0])), int(math.ceil(keys[-1][0]))
        frames = range(first, last + 1)
        # The curve evaluated at every frame, not the plug: a ``getAttr`` at a
        # time is a DG evaluation per frame (11 tracks over a 4,700-frame
        # production timeline: most of the data-node task's 19 s), while the
        # curve answers from its own keys. Maya's bool reads a float plug as
        # ``value >= 0.5`` -- measured on 2025: 0.4999 is off, 0.5 is on, a
        # spline's negative overshoot is off -- so the threshold is applied
        # here, on the same numbers the plug would see.
        values = cls._evaluate_curve(plug, frames)
        if values is None:  # no curve node reachable: the plug is the truth
            values = []
            for frame in frames:
                try:
                    values.append(1.0 if cmds.getAttr(plug, time=frame) else 0.0)
                except Exception:
                    values.append(None)
        sampled: List[List[float]] = []
        for frame, value in zip(frames, values):
            if value is None:
                continue
            on = 1.0 if value >= 0.5 else 0.0
            if not sampled or sampled[-1][1] != on:
                sampled.append([float(frame), on])
        return sampled

    @staticmethod
    def _evaluate_curve(plug: str, frames) -> Optional[List[float]]:
        """The animation curve on *plug* evaluated at *frames*, or ``None``
        when no curve drives it directly (through the API: no DG pass, no
        time change)."""
        curves = cmds.keyframe(plug, query=True, name=True) or []
        if len(curves) != 1:
            return None
        try:
            import maya.api.OpenMaya as om2
            import maya.api.OpenMayaAnim as oma2

            selection = om2.MSelectionList()
            selection.add(curves[0])
            fn = oma2.MFnAnimCurve(selection.getDependNode(0))
            unit = om2.MTime.uiUnit()
            return [float(fn.evaluate(om2.MTime(float(f), unit))) for f in frames]
        except Exception:  # an unexpected curve type: the command path below
            pass
        try:
            return [
                float(cmds.keyframe(curves[0], query=True, eval=True, time=(f, f))[0])
                for f in frames
            ]
        except Exception:
            return None

    @classmethod
    def _scene_key_spans(
        cls, windows: List[Tuple[Optional[float], Optional[float]]]
    ) -> List[Optional[Tuple[float, float]]]:
        """Per window, the scene's first and last authored key inside it.

        The scene-reaching half of ``ptk.MeshConvert.clip_spans`` (its
        ``key_spans``), which owns the rest.  EVERY animated channel counts —
        transforms, visibility and the custom ``opacity`` alike — because the
        converter sizes a take from all of them while emitting a channel for
        only some; a curve that drives nothing (an export snapshot's stash, a
        leftover) is no channel and does not.  Read off the curves' own key
        indices (``AnimUtils.curve_key_spans``): listing every key time cost
        10 s a pass on a post-bake production assembly, and the export bracket
        and ``export_data_node`` each run one (2026-09-14).
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        curves = [curve for curve, _plug in cls._curve_outputs()]
        return AnimUtils.curve_key_spans(curves, windows)

    @classmethod
    def remove(
        cls,
        objects=None,
        mode: Optional[str] = None,
        channel=None,
    ) -> None:
        """Remove the channel attributes, and heal the retired preview's leftovers.

        Parameters:
            objects: Objects to clean. If None, uses selection.
            mode: ``"attribute"``; ``"material"`` = only the legacy material-mode
                cleanup (``OpacityMaterialMode.remove``); ``None`` does both.
            channel: One channel (name or spec), or ``None`` for every channel.
        """
        if objects is None:
            objects = cmds.ls(selection=True) or []
        if not objects:
            # cls.logger.warning("No objects selected.")
            return

        spec = spec_for(channel) if channel is not None else None
        modes = [mode] if mode else ["material", "attribute"]

        # Legacy material-mode leftovers BEFORE the attribute — the
        # disconnect needs the channel attribute to still exist.
        with CoreUtils.preserved_selection():
            if "material" in modes:
                OpacityMaterialMode.remove(objects, spec)
            if "attribute" in modes:
                OpacityAttributeMode.remove(objects, spec)
