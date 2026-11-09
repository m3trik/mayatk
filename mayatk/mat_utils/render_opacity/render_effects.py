# !/usr/bin/python
# coding=utf-8
try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import math
from typing import Any, Dict, List, Optional, Tuple
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
    HIGHLIGHT,
    OPACITY,
    PRESENCE,
    ChannelSpec,
)


class RenderEffects(ptk.LoggingMixin):
    """
    Per-object render-effect channels for engine-ready control.

    Adds a keyable float per effect (``opacity``, ``highlight``, ...) to
    object transforms -- one channel table
    (:mod:`~mayatk.mat_utils.render_opacity.channels`), one transport. The
    authored material is never bound or duplicated; lookdev is the WebXR push.
    ``opacity`` is the first channel and the one that drives *presence*
    (mirrors to ``visibility``; at 0 the object is hidden); ``highlight`` is an
    additive emissive intensity with a per-object colour.

    .. note:: Use :meth:`key_fade` to animate an opacity fade with
              automatic visibility mirroring and :meth:`key_pulse` for a
              repeating highlight; both key from the scene's effect recipe
              (:meth:`scene_recipe`) unless told otherwise, as the Shot
              Manifest's build does through :meth:`apply_effect`.
              :meth:`create` adds (or removes) the channel without keying.
              :meth:`prepare_for_export` runs before every FBX export
              (the ``FbxUtils.STAGERS`` bracket) and :meth:`finish_export`
              after it; call them yourself only around a raw ``cmds.file``.
              :meth:`export_record` is the ``render_effects`` producer
              (``FbxUtils.PRODUCERS``).

    :meth:`create`'s ``mode="attribute"`` adds the channel's custom float
    (0-1) to object transforms, for per-object control in game engines;
    ``mode="remove"`` takes it off. The viewport material binding
    (``mode="material"``) was retired 2026-09-05 and removed 2026-09-21.

    ``RenderOpacity`` is this class's previous name; its import alias was
    removed in mayatk 0.28.0.
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
                  Anything else is refused (logged, ``{}`` returned) BEFORE any
                  object is touched -- including the retired ``"material"``.
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
        spec = ChannelSpec.resolve(channel)
        if mode not in ("attribute", "remove"):
            # Refused up front: past this point the objects' existing channel
            # (and, with delete_visibility_keys, their visibility keys) is
            # cleared, so a typo'd -- or retired -- mode must never get here.
            cls.logger.error(f"Unknown mode: {mode}")
            return {}
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
                    OpacityAttributeMode._clear_visibility_keys(obj)
                cls.logger.info("Deleted visibility keys on: %s", ", ".join(names))
            else:
                msg = (
                    f"Visibility keys found on: {', '.join(names)}. "
                    "Enable 'Delete Visibility Keys' or remove them "
                    "manually before applying opacity."
                )
                raise RuntimeError(msg)

        # Always clean existing state first (legacy material-mode artifacts
        # before the attribute: the disconnect needs the attribute to exist).
        with CoreUtils.preserved_selection():
            cls.remove(objects, channel=spec)
            if mode == "remove":
                return {}
            return OpacityAttributeMode.create(objects, spec)

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
        end: Optional[float] = None,
        direction: str = "in",
        auto_create: bool = True,
        tangent: str = "linear",
        delete_visibility_keys: bool = False,
        channel="opacity",
        whole_frames: bool = True,
        recipe: Optional[ptk.EffectRecipe] = None,
    ) -> List[Tuple[str, str]]:
        """Key a two-key ramp on a channel; the presence channel mirrors to visibility.

        Convenience wrapper around :meth:`OpacityAttributeMode.key_fade` that
        also owns channel creation (see :meth:`_ensure_channel`), so a caller
        keys in one step -- the panel has no separate Create action.

        Parameters:
            objects: Maya nodes. If *None*, uses the current selection.
            start: First frame of the fade.
            end: Last frame of the fade; *start* plus the recipe's
                ``fade_frames`` when omitted.
            direction: ``"in"`` (0→1), ``"out"`` (1→0), or ``"auto"``.
            auto_create: Create the channel on objects that lack it.
            tangent: Tangent type for the channel's keys (default ``"linear"``).
            delete_visibility_keys: Presence channel, objects being created
                only -- clear their existing visibility keys first. Otherwise
                the mirror is written over them.
            channel: The channel name or :class:`ChannelSpec`; ``"opacity"``.
            whole_frames: Snap the keys to whole frames (the default); see
                :meth:`OpacityAttributeMode.key_fade`.
            recipe: The effect recipe the length comes from; the scene's
                (:meth:`scene_recipe`) when omitted.

        Returns:
            List of ``(object_name, "in"|"out")`` per keyed object.
        """
        spec = ChannelSpec.resolve(channel)
        objects = cls._selection_or(objects)
        if not objects:
            return []
        if end is None:
            end = float(start) + (recipe or cls.scene_recipe()).fade_frames
        cls._ensure_channel(objects, spec, auto_create, delete_visibility_keys)
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
        delete_visibility_keys: bool,
    ) -> None:
        """Give *objects* the channel before keying.

        Objects lacking the attribute get it; with *delete_visibility_keys* the
        presence channel's create path clears their visibility keys first
        (:meth:`create`'s guard), otherwise the unguarded attribute-mode create
        runs and the keying mirror writes over whatever is there.
        """
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
    def prepare_for_export(cls) -> List[str]:
        """Stage the curve-proxy transport for an FBX write; write nothing else.

        One transient child per keyed channel carries the per-object curve --
        the animated-value transport every reader rebinds
        (:meth:`stage_export_proxies`); :meth:`finish_export` removes them after
        the write. The scene's animation is not touched: an opacity at 0 is
        presence by the record's own contract (``render_effects`` v2), so an
        opacity keyed by hand gets no visibility mirror here. The missing-mirror
        repair this method used to run made what the track walk published
        depend on which producer ran first, and keyed the artist's scene during
        an export. The staging covers every keyed channel in the scene.

        Returns:
            An empty list (it named the objects whose visibility was
            re-synced).
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
        from mayatk.mat_utils.emissive_groups import EmissiveGroups

        EmissiveGroups.remove_export_proxies()
        cls.remove_export_proxies()

    @classmethod
    def scope_export(cls, ctx) -> List[str]:
        """Narrow this record's staging to the write's scope once a publish
        knows it (``FbxUtils.HOOKS["staging_scope"]``): an emissive-group
        copy's proxy rides the carrier, which every write ships, so a writer
        that staged before its scoped publish drops the proxies of copies it
        does not ship. A node's proxy needs nothing: it rides its node.

        Returns:
            The proxies removed.
        """
        from mayatk.mat_utils.emissive_groups import EmissiveGroups

        return EmissiveGroups.scope_export_proxies(ctx)

    # ------------------------------------------------------------------
    # Curve-proxy transport (FBX -> Unity)
    # ------------------------------------------------------------------

    #: FBX user property stamped on every proxy; the GLB conversion strips
    #: nodes carrying it (``MeshConvert.strip_glb_curve_proxies``) and the
    #: Unity importer rebinds and deletes them.
    PROXY_MARKER = ptk.MeshConvert.CURVE_PROXY_MARKER
    #: ``<node leaf>__<channel>``: the Unity importer parses the parent path
    #: and the channel name off the leaf (``ptk.SceneRecords.TRANSPORT``).
    PROXY_SEPARATOR = ptk.SceneRecords.TRANSPORT.separator

    @classmethod
    def stage_export_proxies(cls) -> List[str]:
        """Stage one transient child transform per keyed channel, for the FBX write
        -- the channels :meth:`visibility_tracks` publishes, each on its proxy
        (``ptk.SceneRecords.TRANSPORT``), so the record and the file's curves
        name the same set (the ``fbx_render_effects`` gate holds them equal).

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

        ``visibility`` -- a node's keyed visibility, where no opacity channel
        mirrors onto it -- rides the transport as its fellow channels do, a
        stepped proxy (measured 2026-11-05: FBX2glTF carries no native
        ``Visibility`` in any form, and Maya's split takes drop a held value a
        take does not key), its keys the boolean timeline Maya draws
        (:meth:`_stepped_track`).

        Strictly export-transient: stamped with :attr:`PROXY_MARKER` and
        removed by :meth:`remove_export_proxies`. Stale proxies from an
        interrupted export are pre-cleaned here.

        Returns:
            The created proxy transforms' long names.
        """
        cls.remove_export_proxies()  # stale pre-clean (idempotent)
        proxies: List[str] = []
        for node, plug, channel in cls._staged_channels():
            leaf = node.split("|")[-1].split(":")[-1]
            name = ptk.SceneRecords.TRANSPORT.proxy_name(leaf, channel)
            if cmds.objExists(name):
                cls.logger.warning(
                    "Curve-proxy name %r is taken by an existing object -- %s's "
                    "%s animation will not ship this export.",
                    name,
                    leaf,
                    channel,
                )
                continue
            proxy = cmds.createNode(
                "transform", name=name, parent=node, skipSelect=True
            )
            proxy = (cmds.ls(proxy, long=True) or [proxy])[0]
            cmds.addAttr(proxy, longName=cls.PROXY_MARKER, attributeType="bool")
            cmds.setAttr(f"{proxy}.{cls.PROXY_MARKER}", True)
            if channel == "visibility":
                # The boolean Maya draws, stated as steps: a curve on a bool
                # is evaluated, never read off its tangents.
                for frame, value in cls._stepped_track(plug):
                    cmds.setKeyframe(
                        f"{proxy}.scaleX",
                        time=frame,
                        value=value,
                        inTangentType="stepnext",
                        outTangentType="step",
                    )
            else:
                # Copy the curve, tangents included, onto scaleX. copyKey/
                # pasteKey keeps the authored shape; a baked one is per-frame.
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
        # The emissive-group copies' weights ride the same transport (material
        # targets under the carrier): this record's stager stages all of it.
        from mayatk.mat_utils.emissive_groups import EmissiveGroups

        return proxies + EmissiveGroups.stage_export_proxies(FbxUtils.write_context())

    @classmethod
    def _staged_channels(cls) -> List[Tuple[str, str, str]]:
        """``(long node, plug, channel)`` for every channel a curve proxy
        carries (:meth:`stage_export_proxies`, :meth:`effect_curves`): each
        keyed render-effect channel, and each keyed ``visibility`` no
        presence channel mirrors onto."""
        staged = [(node, plug, spec.name) for node, plug, spec in cls._channel_curves()]
        staged += [
            (node, plug, "visibility")
            for node, plug in cls._visibility_curves().items()
            if not cls._mirrored(node)
        ]
        return staged

    @classmethod
    def effect_curves(cls, ctx: Optional[ptk.ExportContext] = None) -> Dict[str, Any]:
        """The scene's render-effect curves as a write's curve proxies carry
        them, in the scene's frames -- for a write that carries no take to
        hold them: the WebXR push with animation off states them for its build
        (``ptk.MeshConvert.effect_preview_curves``). Mirror of blendertk's.

        Each curve is one :meth:`stage_export_proxies` would stage, each key
        ``[frame, value, interpolation]`` -- its segment's: ``"step"`` (a
        ``step`` or ``stepnext`` out-tangent, the latter's jump stated at the
        key) or ``"linear"``, and a curved segment as the curve's own values
        at each frame inside it, linear between. ``visibility`` is the boolean
        Maya draws (:meth:`_stepped_track`), stepped. Each animated
        emissive-group copy's weight rides as its material's ``emission``
        (``EmissiveGroups.weight_curves``).

        ``start`` is the frame an animated write of the same nodes puts at its
        clip's t=0: the first key of every curve it ships (the converter opens
        a take on its first key over all its curves), so a push places an
        effect where it would with animation on.

        Parameters:
            ctx: The write's decisions (``nodes``): only the curves of the
                nodes it ships, and their keys for the origin.

        Returns:
            ``{"curves": [{"target", "channel", "keys"}], "start": frame,
            "fps": rate}``, or ``{}`` when no effect is keyed.
        """
        curves = []
        for node, plug, channel in cls._staged_channels():
            leaf = node.split("|")[-1].split(":")[-1]
            if ctx is not None and not ctx.ships(leaf):
                continue
            if channel == "visibility":
                keys = [[f, v, "step"] for f, v in cls._stepped_track(plug)]
            else:
                keys = cls._segment_keys(plug)
            if keys:
                curves.append({"target": leaf, "channel": channel, "keys": keys})
        # Each animated emissive-group copy's weight, as its
        # ``<material>__emission`` proxy carries it (that tool's stager).
        from mayatk.mat_utils.emissive_groups import EmissiveGroups

        emission_channel = EmissiveGroups.HOST.emission_channel
        curves += EmissiveGroups.weight_curves(
            lambda copy: cls._segment_keys(emission_channel(copy)), ctx
        )
        if not curves:
            return {}
        span = cls._first_key(ctx)
        start = span if span is not None else min(c["keys"][0][0] for c in curves)
        return {"curves": curves, "start": float(start), "fps": cls._scene_fps() or 24.0}

    @classmethod
    def _segment_keys(cls, plug: str) -> List[List[Any]]:
        """*plug*'s keys as ``[frame, value, interpolation]``, each segment's
        interpolation stated (:meth:`effect_curves`)."""
        keys = cls._curve_keys(plug)
        try:
            outs = cmds.keyTangent(plug, query=True, outTangentType=True) or []
            ins = cmds.keyTangent(plug, query=True, inTangentType=True) or []
        except Exception:
            outs, ins = [], []
        out: List[List[Any]] = []
        for index, (time, value) in enumerate(keys):
            if index + 1 >= len(keys):
                out.append([time, value, "linear"])
                break
            following, next_value = keys[index + 1]
            kind = outs[index] if index < len(outs) else "linear"
            if kind in ("step", "stepnext"):
                out.append([time, value, "step"])
                if kind == "stepnext":
                    out.append([time, next_value, "step"])
                continue
            out.append([time, value, "linear"])
            if kind == "linear" and (index + 1 >= len(ins) or ins[index + 1] == "linear"):
                continue
            frames = [
                float(f)
                for f in range(int(math.floor(time)) + 1, int(math.ceil(following)))
                if time < f < following
            ]
            values = cls._evaluate_curve(plug, frames) if frames else []
            out.extend([f, float(v), "linear"] for f, v in zip(frames, values or ()))
        return out

    @classmethod
    def _first_key(cls, ctx: Optional[ptk.ExportContext] = None) -> Optional[float]:
        """The first key of every scene curve a write under *ctx* ships, or
        ``None``. A curve driving a DAG node the write leaves out is no
        channel of it; one driving a node outside the DAG (a blend shape's
        weight, a shader's input) still counts -- what it animates may ride a
        shipped mesh. Each curve's first key is read by its index, so no key
        is walked; a curve keyed on a driver (its inputs are driver values,
        not frames) is no time."""
        pairs = cls._curve_outputs()
        if ctx is not None and ctx.nodes is not None:
            driven = {plug.partition(".")[0] for _curve, plug in pairs}
            dag = set(cmds.ls(list(driven), type="dagNode") or []) if driven else set()
            pairs = [
                (curve, plug)
                for curve, plug in pairs
                if plug.partition(".")[0] not in dag or ctx.ships(plug.partition(".")[0])
            ]
        timed = cmds.ls(
            list({curve for curve, _plug in pairs}),
            type=("animCurveTL", "animCurveTA", "animCurveTT", "animCurveTU"),
        ) if pairs else []
        if not timed:
            return None
        firsts = cmds.keyframe(timed, query=True, index=(0, 0), timeChange=True) or []
        return float(min(firsts)) if firsts else None

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
        period: Optional[float] = None,
        bright_fraction: Optional[float] = None,
        ramp_fraction: Optional[float] = None,
        lead_in: Optional[float] = None,
        lead_out: Optional[float] = None,
        color=None,
        dim_color=None,
        auto_create: bool = True,
        channel="highlight",
        delete_visibility_keys: bool = False,
        whole_frames: bool = True,
        recipe: Optional[ptk.EffectRecipe] = None,
    ) -> List[str]:
        """Key a repeating bright/dim pulse on a channel over ``start..end``.

        Convenience wrapper around :meth:`OpacityAttributeMode.key_pulse` that
        also owns channel creation (see :meth:`_ensure_channel`). The cadence
        is the scene's effect recipe -- the one the Render Effects panel edits
        and the Shot Manifest's build keys with (2.86 s, bright 59% of it, by
        default) -- converted at the scene's rate, so a pulse keyed at 24 fps
        beats as one keyed at 30. The pulse is bracketed by dim keys at both
        ends -- see the writer for why that is load-bearing rather than
        cosmetic. The keys already in the window are replaced.

        Parameters:
            objects: Maya nodes. If *None*, uses the current selection.
            start: First frame of the pulse; the channel is dim here.
            end: Last frame of the pulse; the channel is dim here too.
            period: One cycle, in FRAMES; the recipe's when omitted.
            bright_fraction: Share of the cycle spent bright; the recipe's
                duty when omitted.
            ramp_fraction: Share of the cycle spent in each transition.
            lead_in: Frames the pulse takes to come up from dim at *start*;
                omitted, the recipe's lead-in -- or, when *period* or
                *ramp_fraction* is given, that cycle's own ramp. 0 cuts as hard
                as the floor allows (one frame under *whole_frames*).
            lead_out: The same at *end*, going back down to dim.
            color: Optional ``(r, g, b)`` for the BRIGHT end of the channel's
                colour ramp -- what the object reads at intensity 1.
            dim_color: Optional ``(r, g, b)`` for the other end, read at
                intensity 0. Leaving either ``None`` keeps what the object
                carries; a channel created here takes the recipe's colours.
            auto_create: Create the channel on objects that lack it.
            channel: The channel name or spec; ``"highlight"``.
            delete_visibility_keys: See :meth:`key_fade`; no effect unless the
                channel drives presence.
            whole_frames: Snap every key to a whole frame (the default); see
                :meth:`OpacityAttributeMode.key_pulse`.
            recipe: The effect recipe; the scene's (:meth:`scene_recipe`) when
                omitted.

        Returns:
            The keyed objects' short names.
        """
        spec = ChannelSpec.resolve(channel)
        objects = cls._selection_or(objects)
        if not objects:
            return []
        recipe = recipe or cls.scene_recipe()
        cadence = recipe.pulse_cadence(cls._scene_fps() or recipe.REFERENCE_FPS)
        given = {
            "period": period,
            "bright_fraction": bright_fraction,
            "ramp_fraction": ramp_fraction,
            "lead_in": lead_in,
            "lead_out": lead_out,
        }
        if period is not None or ramp_fraction is not None:
            # A caller's own cycle: a lead it does not state is that cycle's
            # own ramp (the writer's rule), not the recipe's seconds -- which
            # fit only the recipe's cycle.
            cadence["lead_in"] = cadence["lead_out"] = None
        cadence.update({k: v for k, v in given.items() if v is not None})
        created = (
            [o for o in objects if not OpacityAttributeMode.has_channel(o, spec)]
            if auto_create
            else []
        )
        cls._ensure_channel(objects, spec, auto_create, delete_visibility_keys)
        cls._seed_colors(created, spec, recipe)
        return OpacityAttributeMode.key_pulse(
            objects,
            start=start,
            end=end,
            color=color,
            dim_color=dim_color,
            auto_create=False,
            spec=spec,
            whole_frames=whole_frames,
            **cadence,
        )

    @staticmethod
    def scene_store():
        """The class holding the scene's effect recipe -- the shot store. It
        sits above this layer, so it is reached through the record owners
        (``DataNodes.owner``), never imported; ``None`` when unavailable."""
        return DataNodes.owner(ptk.SceneRecords.SHOT_STORE.key)

    @classmethod
    def scene_recipe(cls) -> ptk.EffectRecipe:
        """The scene's effect recipe -- the shot store's, which the Render
        Effects panel edits and the Shot Manifest's build keys with; the
        defaults when no store can be had."""
        try:
            return cls.scene_store().active().effect_recipe
        except Exception:
            return ptk.EffectRecipe()

    #: The channel each recipe effect keys.
    EFFECT_CHANNELS = {"fade_in": OPACITY, "fade_out": OPACITY, "pulse": HIGHLIGHT}

    @classmethod
    def apply_effect(
        cls,
        obj,
        effect: str,
        start: float,
        end: float,
        recipe: Optional[ptk.EffectRecipe] = None,
        fps: Optional[float] = None,
        place=None,
        anchor: Optional[float] = None,
    ) -> List[Tuple[str, float]]:
        """Key one recipe effect on *obj*, placed in the range ``start..end``.

        The Shot Manifest's writer: the same plan :meth:`key_fade` /
        :meth:`key_pulse` key by hand (``ptk.EffectRecipe.plan``), placed where
        a behavior template puts it (``EffectRecipe.window``) and written by the
        one writer (:meth:`OpacityAttributeMode.write_keys`). It deletes
        nothing -- the build takes out its own previous keys first -- and
        writes the recipe's colours only on a channel it creates, so a revised
        object keeps its colours through a rebuild.

        Parameters:
            obj: The node to key.
            effect: ``"fade_in"``, ``"fade_out"`` or ``"pulse"``.
            start: The range's first frame (a shot's start).
            end: Its last frame.
            recipe: The effect recipe; the scene's when omitted.
            fps: The scene's rate; queried when omitted.
            place: ``"start"`` / ``"end"`` / ``"span"`` / a fraction; where
                the effect naturally goes when omitted.
            anchor: A fraction overriding *place* (the build's spread).

        Returns:
            ``(anim curve, time)`` for every key written.

        Raises:
            ValueError: An effect with no channel (``"clip"`` keys audio).
        """
        spec = cls.EFFECT_CHANNELS.get(effect)
        if spec is None:
            raise ValueError(f"The {effect!r} effect keys no render channel.")
        recipe = recipe or cls.scene_recipe()
        fps = float(fps or cls._scene_fps() or recipe.REFERENCE_FPS)
        if place is None:
            place = "end" if effect == "fade_out" else "start"
        plan = recipe.plan(
            effect, *recipe.window(effect, start, end, place, anchor), fps
        )
        node = (cmds.ls(str(obj), long=True) or [str(obj)])[0]
        if not OpacityAttributeMode.has_channel(node, spec):
            # The attribute-mode create touches ``data_internal``; the
            # selection must not end up on it.
            with CoreUtils.preserved_selection():
                OpacityAttributeMode.create([node], spec)
            cls._seed_colors([node], spec, recipe)
        return OpacityAttributeMode.write_keys(node, plan, spec)

    @classmethod
    def _seed_colors(cls, objects, spec: ChannelSpec, recipe) -> None:
        """Give channels just created the recipe's colours (no-op for a
        channel without a colour ramp)."""
        if not objects or spec.color_stops is None:
            return
        with CoreUtils.preserved_selection():
            for stop, color in zip(("hi", "lo"), recipe.colors):
                if spec.stop_attr(stop):
                    OpacityAttributeMode.set_color(objects, color, spec, stop)

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

        spec = ChannelSpec.resolve(channel)
        transforms = NodeUtils.get_transform_node(list(objects or [])) or []
        # The leaf the producer publishes (``render_effects``) and the GLB
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
    # The selection the Render Effects panel acts on
    # ------------------------------------------------------------------

    @staticmethod
    def selected_objects(last_only: bool = False) -> List[str]:
        """The objects the Render Effects actions act on: the selection, in
        the order it was made -- or, with *last_only*, its lead alone (the
        last selected; blendertk's twin reads the active object, Blender
        keeping no selection order).

        Parameters:
            last_only: Only the selection's lead.

        Returns:
            Node names as ``cmds.ls(selection=True)`` gives them; ``[]`` when
            nothing is selected, or outside Maya.
        """
        if cmds is None:
            return []
        objects = cmds.ls(selection=True) or []
        return objects[-1:] if last_only else objects

    @classmethod
    def has_channel(cls, obj, channel="opacity") -> bool:
        """Whether *obj* carries *channel*'s attribute (twin of blendertk's).

        Parameters:
            obj: A node.
            channel: The channel name or spec; ``"opacity"``.

        Raises:
            KeyError: An unknown channel.
        """
        return OpacityAttributeMode.has_channel(obj, ChannelSpec.resolve(channel))

    @staticmethod
    def select_objects(objects) -> List[str]:
        """Make *objects* the selection without recording an undo step -- a
        view of them, not an edit (the Shot Manifest focusing the Render
        Effects panel on one object) -- and return them resolved. Twin of
        blendertk's.

        Parameters:
            objects: Nodes or names; the missing ones are dropped.

        Returns:
            Long names of the objects selected. With none resolved the
            selection is left as it was (an empty ``cmds.ls`` would have
            listed every node).
        """
        names = [str(o) for o in objects or ()]
        resolved = (cmds.ls(names, long=True) or []) if names and cmds else []
        if resolved:
            with CoreUtils.undo_disabled():
                cmds.select(resolved, replace=True)
        return resolved

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
        spec = ChannelSpec.resolve(channel)
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
        spec = ChannelSpec.resolve(channel)
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
        spec = ChannelSpec.resolve(channel)
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
        spec = ChannelSpec.resolve(channel)
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

    #: ``data_export`` channel of the ``ptk.SceneRecords.VISIBILITY`` record
    #: (``render_effects``), which every reader of the file reads.
    DATA_CHANNEL = ptk.SceneRecords.VISIBILITY.key
    #: Schema this producer writes (stamped by the declaration); the reader
    #: refuses anything newer.
    SCHEMA_VERSION = ptk.SceneRecords.VISIBILITY.version

    @classmethod
    def visibility_tracks(cls) -> List[Dict]:
        """Every node carrying a keyed render effect, as ``render_effects`` v2
        tracks: ``{"node", "channels", "highlight_color"?, "highlight_color_dim"?}``.

        A track names the channels its node carries -- ``visibility``,
        ``opacity``, ``highlight`` -- and the curves ride the transport
        (:meth:`stage_export_proxies`), never the record. A node with an
        ``opacity`` channel publishes no ``visibility``: its visibility keys are
        that channel's mirror (``key_fade`` writes it, and ``create`` refuses a
        node whose visibility is already keyed), and opacity at 0 is presence
        by the record's contract -- a mirror beside it would hide the node for
        the fade-in it should be fading over. The highlight's colour stops are
        the node's own, read once: a node missing a stop states nothing for it,
        so a reader falls back to THAT stop's default.
        """
        tracks: List[Dict] = []
        visibility = cls._visibility_curves()
        channels: Dict[str, Dict[str, ChannelSpec]] = {}
        for node, _plug, spec in cls._channel_curves():
            channels.setdefault(node, {})[spec.name] = spec
        for node in sorted(set(visibility) | set(channels)):
            named = channels.get(node, {})
            track: Dict = {
                "node": node.split("|")[-1].split(":")[-1],
                "channels": [],
            }
            if node in visibility and not cls._mirrored(node):
                track["channels"].append("visibility")
            for name, spec in sorted(named.items()):
                track["channels"].append(name)
                track_keys = spec.track_color_stops
                if track_keys is None:
                    continue
                for attr, key in zip(spec.color_stops.keys, track_keys.keys):
                    plug = f"{node}.{attr}"
                    if not cmds.objExists(plug):
                        continue
                    try:
                        rgb = cmds.getAttr(plug)[0]
                        track[key] = [float(c) for c in rgb[:3]]
                    except (RuntimeError, TypeError, IndexError):
                        pass
            if track["channels"]:
                tracks.append(track)
        return tracks

    @staticmethod
    def _mirrored(node: str) -> bool:
        """Whether *node*'s visibility is its presence channel's mirror: that
        channel (``opacity``) is keyed, and its 0 is presence itself. A static
        opacity mirrors nothing, so a visibility keyed beside it is its own."""
        plug = f"{node}.{PRESENCE.name}"
        return bool(
            cmds.objExists(plug) and cmds.keyframe(plug, query=True, keyframeCount=True)
        )

    @classmethod
    def export_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]:
        """The ``render_effects`` record for this scene, or ``None`` when it
        keys no effect -- the ``ptk.SceneRecords.VISIBILITY`` producer
        (``FbxUtils.PRODUCERS``).  Pure: it reads the curves and never writes.

        Version 2: which nodes carry which channels (:meth:`visibility_tracks`)
        and their colours. The curves are the file's own, each on its proxy in
        each take the write makes (:meth:`stage_export_proxies`), so no frame
        and no rate is published: a curve scopes to its clip by construction.

        Parameters:
            ctx: The export's decisions (``nodes``). A scoped write keeps the
                tracks of the nodes it ships (the record is
                ``export_scoped``): another node's keys are no channel of this
                file, and a track for it warned "not in this GLB".

        Returns:
            The record, or ``None`` when no effect is keyed (the publisher then
            clears the channel).
        """
        tracks = [track for track in cls.visibility_tracks() if ctx.ships(track["node"])]
        # Each animated emissive-group copy is a material entry: its weight
        # rides its ``<material>__emission`` proxy (staged with this record's).
        from mayatk.mat_utils.emissive_groups import EmissiveGroups

        materials = EmissiveGroups.render_effect_materials(ctx)
        payload = ptk.MeshConvert.build_visibility_tracks(tracks, materials)
        return ptk.SceneRecords.VISIBILITY.make(payload) if payload is not None else None

    @classmethod
    def refresh_export_metadata(cls) -> Optional[str]:
        """Republish the ``render_effects`` channel on the ``data_export`` carrier.

        The authoring-time publish of :meth:`export_record`, committed through
        ``FbxUtils.publish_authored`` (an export pipeline runs the producer
        itself: ``FbxUtils.PRODUCERS``).  Clears the channel when the scene
        keys no effect, leaving no empty carrier behind.

        Returns:
            The published JSON string, or ``None`` when cleared.
        """
        record = cls._publish_record(
            ptk.ExportContext(mode=ptk.ExportContext.AUTHORING)
        )
        if record is None:
            return None
        cls.logger.info(
            "Render effects: published %d track(s); their curves ride each "
            "export's curve proxies.",
            len(record.payload.get("tracks") or []),
        )
        return record.text

    @classmethod
    def _publish_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]:
        """:meth:`export_record` under *ctx*, committed at authoring time
        (``FbxUtils.publish_authored``) -- the channel CLEARED when the scene
        keys no effect, so a stale one never outlives its curves.
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

        A SHAPE's keyed visibility belongs to its transform -- ``setKeyframe``
        on a transform keys its shape's visibility too (its ``shape`` flag is
        on by default), and a track names a transform, whose curve proxy hangs
        under it -- so a shape's curve stands for its transform only where the
        transform keys none of its own (measured 2026-11-05: a ``blinkerShape``
        track beside ``blinker``'s, its proxy unparentable under a shape).
        """
        found: Dict[str, str] = {}
        shapes: Dict[str, str] = {}
        for _curve, plug in RenderEffects._curve_outputs():
            node, _, attr = plug.partition(".")
            if attr != "visibility" or not cmds.objExists(node):
                continue
            long_name = (cmds.ls(node, long=True) or [node])[0]
            if cmds.objectType(long_name, isAType="transform"):
                found[long_name] = f"{long_name}.visibility"
                continue
            parent = (cmds.listRelatives(long_name, parent=True, fullPath=True) or [None])[0]
            if parent:
                shapes.setdefault(parent, f"{long_name}.visibility")
        for parent, plug in shapes.items():
            found.setdefault(parent, plug)
        return found

    @staticmethod
    def _curve_outputs() -> List[Tuple[str, str]]:
        """``(curve, driven plug)`` for every scene animation curve's
        ``output`` connection -- ONE call.

        The curve walks (visibility, channels) asked
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

        spec = ChannelSpec.resolve(channel) if channel is not None else None
        modes = [mode] if mode else ["material", "attribute"]

        # Legacy material-mode leftovers BEFORE the attribute — the
        # disconnect needs the channel attribute to still exist.
        with CoreUtils.preserved_selection():
            if "material" in modes:
                OpacityMaterialMode.remove(objects, spec)
            if "attribute" in modes:
                OpacityAttributeMode.remove(objects, spec)
