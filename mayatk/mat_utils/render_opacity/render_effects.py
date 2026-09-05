# !/usr/bin/python
# coding=utf-8
try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

import json
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
              (``FbxUtils._KNOWN_PRODUCERS``) and :meth:`finish_export`
              after it; call them yourself only around a raw ``cmds.file``.

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
        """Create a channel's mechanism (Attribute, Material binding, or Remove).

        Running this on objects that already have a different mode applied
        will automatically clean up the previous mode first.

        Parameters:
            objects: Objects to process. If None, uses selection.
            mode: ``"attribute"`` — Adds the channel attribute (Game Engine friendly).
                  ``"material"`` — Also binds each object's material for viewport feedback.
                  ``"remove"``   — Removes the channel's artifacts from the objects.
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

        # Always clean existing state first.  Material mode must be
        # removed before attribute mode because the proxy disconnect
        # needs the channel attribute to still exist on the transform.
        cls.remove(objects, channel=spec)

        if mode == "remove":
            return {}
        elif mode == "attribute":
            return OpacityAttributeMode.create(objects, spec)
        elif mode == "material":
            return OpacityMaterialMode.create(objects, spec)
        else:
            cls.logger.error(f"Unknown mode: {mode}")
            return {}

    @classmethod
    def preview(cls, objects=None, channel="highlight", enabled: bool = True) -> Dict:
        """Bind (or unbind) a channel to the selection's materials for lookdev.

        ``enabled`` is :meth:`create` in ``"material"`` mode -- the attribute
        plus the viewport binding; ``False`` unbinds the material and keeps the
        attribute and its keys. The binding is suspended for the duration of
        every export and re-bound after, so it never reaches a deliverable.
        """
        spec = spec_for(channel)
        if objects is None:
            objects = cmds.ls(selection=True) or []
        if not objects:
            return {}
        if enabled:
            return OpacityMaterialMode.create(objects, spec)
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
        OpacityMaterialMode.ensure_connections(objects)

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
    ) -> List[Tuple[str, str]]:
        """Key an opacity fade and mirror to visibility.

        Convenience wrapper around
        :meth:`OpacityAttributeMode.key_fade`.  Creates ``opacity``
        keyframes (smooth linear channel) and matching ``visibility``
        keyframes (stepped binary) so FBX export produces native tracks
        for both channels.

        Parameters:
            objects: Maya nodes. If *None*, uses the current selection.
            start: First frame of the fade.
            end: Last frame of the fade.
            direction: ``"in"`` (0→1), ``"out"`` (1→0), or ``"auto"``.
            auto_create: Create the ``opacity`` attribute on objects
                that lack it before keying.
            tangent: Tangent type for opacity keys (default ``"linear"``).

        Returns:
            List of ``(object_name, "in"|"out")`` per keyed object.
        """
        if objects is None:
            objects = cmds.ls(selection=True) or []
        if not objects:
            cls.logger.warning("No objects selected.")
            return []
        return OpacityAttributeMode.key_fade(
            objects,
            start=start,
            end=end,
            direction=direction,
            auto_create=auto_create,
            tangent=tangent,
        )

    @classmethod
    def prepare_for_export(cls, objects=None) -> List[str]:
        """Sync visibility keyframes for every opacity object before FBX export.

        Walks *objects* (or the entire scene when ``None``), and for each
        transform that has the ``opacity`` attribute with animation but no
        matching ``visibility`` keys, mirrors the opacity curve onto
        ``visibility`` via :meth:`sync_visibility_from_opacity`.

        Why this exists: the Unity importer reconstructs per-object opacity
        fades from the ``visibility`` (m_Enabled) curves, because Unity
        binds animated custom properties to the root Animator with empty
        paths — the opacity custom-property curves cannot be mapped back
        to individual objects.  An object with only ``opacity`` keys and
        no ``visibility`` keys silently animates nothing in Unity.  The
        :meth:`key_fade` helper and shot behaviors already dual-key both
        channels; this method is a safety net for hand-authored opacity
        animation.

        Idempotent — objects already in sync are skipped.  Safe to call
        from a scene-exporter pre-export hook.

        Parameters:
            objects: Objects to scan. If *None*, scans every transform in
                the scene that has the ``opacity`` attribute.

        Returns:
            List of object names that were re-synced.
        """
        if objects is None:
            # Scan only transforms — some shape and material nodes carry a
            # native ``opacity`` attribute that is unrelated to RenderOpacity.
            objects = [
                t
                for t in cmds.ls(type="transform")
                if cmds.attributeQuery(cls.ATTR_NAME, node=t, exists=True)
            ]
        else:
            objects = cmds.ls(objects)

        synced: List[str] = []
        needs_sync = []
        for obj in objects:
            if not cmds.attributeQuery(cls.ATTR_NAME, node=obj, exists=True):
                continue
            # Use long-name plug paths so the query targets ONLY the
            # transform — passing attribute="visibility" by kwarg also
            # hits the shape node and would double-count keys.
            opa_plug = f"{(cmds.ls(obj, long=True) or [obj])[0]}.{cls.ATTR_NAME}"
            vis_plug = f"{(cmds.ls(obj, long=True) or [obj])[0]}.visibility"
            opa_keys = cmds.keyframe(opa_plug, q=True, keyframeCount=True)
            if not opa_keys:
                continue
            vis_keys = cmds.keyframe(vis_plug, q=True, keyframeCount=True)
            # Resync if visibility has no keys at all, or fewer keys than
            # opacity (a partial sync from a stale state).
            if not vis_keys or vis_keys < opa_keys:
                needs_sync.append(obj)
                synced.append(obj.split("|")[-1].split(":")[-1])

        if needs_sync:
            OpacityAttributeMode.sync_visibility_from_opacity(needs_sync)
            cls.logger.info(
                "prepare_for_export: synced visibility on %d object(s): %s",
                len(synced),
                ", ".join(synced),
            )

        # The export-time staging, undone by :meth:`finish_export`: viewport
        # bindings are suspended so the FBX material and the SceneState
        # sidecar read the AUTHORED values (measured 2026-09-04: a driven
        # emissive reached the GLB's emissiveFactor through both), and one
        # curve-proxy child per keyed channel carries the per-object curve to
        # engines that flatten custom-property animation.
        OpacityMaterialMode.suspend_for_export()
        cls.stage_export_proxies()
        return synced

    @classmethod
    def finish_export(cls) -> None:
        """Undo :meth:`prepare_for_export`'s staging (``FbxUtils._KNOWN_FINALIZERS``).

        Idempotent: with nothing staged it changes nothing.
        """
        cls.remove_export_proxies()
        OpacityMaterialMode.resume_after_export()

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
        color=None,
        auto_create: bool = True,
        channel="highlight",
    ) -> List[str]:
        """Key a repeating bright/dim pulse on a channel over ``start..end``.

        Convenience wrapper around :meth:`OpacityAttributeMode.key_pulse`.
        The defaults are the cadence measured on the WebXR reference at
        30 fps: a 2.86 s period (86 frames), bright for 59% of it.

        Parameters:
            objects: Maya nodes. If *None*, uses the current selection.
            start: First frame of the pulse.
            end: Last frame of the pulse.
            period: One cycle, in frames.
            bright_fraction: Share of the cycle spent bright.
            ramp_fraction: Share of the cycle spent in each transition.
            color: Optional ``(r, g, b)`` for the channel's colour attribute.
            auto_create: Create the channel on objects that lack it.
            channel: The channel name or spec; ``"highlight"``.

        Returns:
            The keyed objects' short names.
        """
        if objects is None:
            objects = cmds.ls(selection=True) or []
        if not objects:
            cls.logger.warning("No objects selected.")
            return []
        return OpacityAttributeMode.key_pulse(
            objects,
            start=start,
            end=end,
            period=period,
            bright_fraction=bright_fraction,
            ramp_fraction=ramp_fraction,
            color=color,
            auto_create=auto_create,
            spec=spec_for(channel),
        )

    # ------------------------------------------------------------------
    # In-band export metadata — the glTF route
    # ------------------------------------------------------------------

    #: ``data_export`` channel read by ``ptk.MeshConvert.apply_glb_visibility``.
    DATA_CHANNEL = ptk.MeshConvert.VISIBILITY_TRACKS_KEY
    #: Schema this producer writes; the reader refuses anything newer.
    SCHEMA_VERSION = ptk.MeshConvert.VISIBILITY_TRACKS_VERSION

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
                if spec.color_attr and cmds.objExists(f"{node}.{spec.color_attr}"):
                    try:
                        rgb = cmds.getAttr(f"{node}.{spec.color_attr}")[0]
                        track[spec.track_color_key] = [float(c) for c in rgb[:3]]
                    except (RuntimeError, TypeError, IndexError):
                        pass
            if len(track) > 1:
                tracks.append(track)
        return tracks

    @classmethod
    def refresh_export_metadata(cls) -> Optional[str]:
        """Republish the ``visibility_tracks`` channel on the ``data_export`` carrier.

        The canonical no-arg pre-export refresh, wired into
        ``FbxUtils._KNOWN_PRODUCERS``.  Exists because keyed visibility is the
        one animated channel that does NOT survive to glTF: the format animates
        translation, rotation, scale and morph weights, and nothing else, so an
        FBX's ``Visibility`` curves are dropped in the conversion without a
        word.  ``MeshConvert.apply_glb_visibility`` rebuilds them from this
        channel as stepped scale, which every viewer plays.

        Also publishes ``clip_span`` — per take, the first and last authored
        frame inside its window.  That is the take's own zero: the converter
        rebases a clip onto its first authored key rather than onto the take's
        declared start, and it counts the visibility keys when deciding which
        key is first even though it emits no channel for them.  Only this side
        can see the curves, so only this side can say.

        Clears the channel when the scene has no keyed visibility, leaving no
        empty carrier behind.

        Returns:
            The published JSON string, or ``None`` when cleared.
        """
        # Bail BEFORE the span walk: that reads every anim curve in the scene,
        # and a scene with no keyed visibility has nothing to spend it on.
        tracks = cls.visibility_tracks()
        if not tracks:
            DataNodes.set_export_string(cls.DATA_CHANNEL, "")
            return None

        # Both read off the carrier the shots producer has just refreshed --
        # _KNOWN_PRODUCERS runs "shots" first for exactly this reason, and it
        # keeps the frame rate defined in ONE place for the whole export.
        metadata = cls._carrier_json("shot_metadata")
        # The scene's own rate when the shots producer published none (a
        # shot-less scene CLEARS shot_metadata): without a rate the GLB
        # appliers cannot place the frames in time and drop every track and
        # ramp -- measured 2026-09-02, "carry no frame rate ... not applied".
        fps = (metadata or {}).get("fps") or cls._scene_fps()
        text = json.dumps(
            ptk.MeshConvert.build_visibility_tracks(
                tracks,
                fps=fps,
                clip_spans=ptk.MeshConvert.clip_spans(
                    cls._scene_key_frames(),
                    cls._carrier_json("fbx_takes") or [],
                    stack_range=FbxUtils.bake_range(),
                    # The stack ships only the range the export bakes, and
                    # the converter rebases it onto its first key.
                    # set_bake_animation_range has already narrowed this to
                    # the takes' union, so it is the truth about what the
                    # FBX will carry -- the scene's own first key is not.
                ),
            )
        )
        DataNodes.set_export_string(cls.DATA_CHANNEL, text)
        cls.logger.info(
            "Visibility: published %d keyed-visibility track(s) for the GLB "
            "route (glTF drops the FBX's own visibility curves).",
            len(tracks),
        )
        return text

    @classmethod
    def restamp_stack_span(cls, start: float, end: float) -> bool:
        """Rewrite the published ``clip_span`` whole-timeline entry to *(start, end)*.

        The stack's zero is the first frame the FBX will CARRY, and only the
        task that SETS the bake range knows that number.
        ``set_bake_animation_range`` runs LAST by design -- it owns the range --
        which is two tasks after :meth:`refresh` publishes this channel, so the
        producer can only read whatever the preset happens to hold at publish
        time. On a production assembly that was the plugin's untouched default,
        ``[0, 10000]``; it reached the GLB as ``source_zero = 0`` and slid every
        clip cut from the stack by the bake start -- the same 33-frame slide
        ``FbxUtils.bake_range`` was introduced to close, reopened by the
        reordering that landed with it.

        Inverting the dependency is what makes it stay closed: the task that
        WRITES the range publishes the value derived from it, so no future
        reordering can separate the two again. Only the ``*`` entry moves --
        each take's own span is measured from its keys and is already correct.

        Parameters:
            start (float): First frame the bake will write.
            end (float): Last frame the bake will write.

        Returns:
            bool: True when the carrier now names that span. False when there
            is nothing to restamp -- a scene with no keyed visibility publishes
            no channel at all, and an older payload may carry no ``clip_span``.
        """
        payload = cls._carrier_json(cls.DATA_CHANNEL)
        if not isinstance(payload, dict):
            return False
        spans = payload.get("clip_span")
        if not isinstance(spans, dict):
            return False
        key = ptk.MeshConvert.DEFAULT_CLIP_SPAN
        wanted = [float(start), float(end)]
        previous = spans.get(key)
        if previous != wanted:
            spans[key] = wanted
            DataNodes.set_export_string(cls.DATA_CHANNEL, json.dumps(payload))
            cls.logger.debug(
                "Clip origin restamped to %g-%g (was %s).",
                wanted[0],
                wanted[1],
                previous,
            )
        return True

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
    def _carrier_json(attr: str) -> Optional[object]:
        """One ``data_export`` channel, decoded, or ``None``."""
        try:
            raw = DataNodes.get_export_string(attr)
            return json.loads(raw) if raw else None
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
        for curve in cmds.ls(type="animCurve", long=True) or []:
            for plug in (
                cmds.listConnections(
                    f"{curve}.output", plugs=True, source=False, destination=True
                )
                or []
            ):
                node, _, attr = plug.partition(".")
                if attr != "visibility" or not cmds.objExists(node):
                    continue
                long_name = (cmds.ls(node, long=True) or [node])[0]
                found[long_name] = f"{long_name}.visibility"
        return found

    @classmethod
    def _channel_curves(cls) -> List[Tuple[str, str, ChannelSpec]]:
        """``(long node, plug, spec)`` for every keyed render-effect channel.

        Walked from the anim curves like :meth:`_visibility_curves`, and with
        the same blind spot (a curve reaching the plug through a layer or a
        ``pairBlend`` is not seen), for the same reason.
        """
        found: List[Tuple[str, str, ChannelSpec]] = []
        seen = set()
        for curve in cmds.ls(type="animCurve", long=True) or []:
            for plug in (
                cmds.listConnections(
                    f"{curve}.output", plugs=True, source=False, destination=True
                )
                or []
            ):
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
        sampled: List[List[float]] = []
        for frame in range(first, last + 1):
            try:
                on = 1.0 if cmds.getAttr(plug, time=frame) else 0.0
            except Exception:
                continue
            if not sampled or sampled[-1][1] != on:
                sampled.append([float(frame), on])
        return sampled

    @staticmethod
    def _scene_key_frames() -> List[float]:
        """Every authored key time in the scene, in frames.

        The scene-reaching half of ``ptk.MeshConvert.clip_spans``, which owns
        the rest.  EVERY animated channel counts — transforms, visibility and
        the custom ``opacity`` alike — because the converter sizes a take from
        all of them while emitting a channel for only some.
        """
        every: List[float] = []
        for curve in cmds.ls(type="animCurve", long=True) or []:
            every.extend(cmds.keyframe(curve, query=True, timeChange=True) or [])
        return every

    @classmethod
    def remove(
        cls,
        objects=None,
        mode: Optional[str] = None,
        channel=None,
    ) -> None:
        """Remove attributes or reset material settings.

        Parameters:
            objects: Objects to clean. If None, uses selection.
            mode: ``"attribute"``, ``"material"``, or ``None`` (cleans both).
            channel: One channel (name or spec), or ``None`` for every channel.
        """
        if objects is None:
            objects = cmds.ls(selection=True) or []
        if not objects:
            # cls.logger.warning("No objects selected.")
            return

        spec = spec_for(channel) if channel is not None else None
        modes = [mode] if mode else ["material", "attribute"]

        # Material mode must be cleaned BEFORE attribute mode — the
        # proxy disconnect needs the channel attribute to still exist.
        if "material" in modes:
            OpacityMaterialMode.remove(objects, spec)
        if "attribute" in modes:
            OpacityAttributeMode.remove(objects, spec)
