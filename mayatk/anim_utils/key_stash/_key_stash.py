# !/usr/bin/python
# coding=utf-8
"""Key Stash — park keyframes outside the working animation, retrieve later (Maya).

**Storage.** A stashed curve is a DUPLICATE of the live animCurve trimmed to
the stashed keys, left UNCONNECTED, locked against *Optimize Scene Size*, and
kept alive through a message-multi registry on ``data_internal`` (the carrier
SmartBake parks its curves on).  An unconnected animCurve evaluates nothing
and belongs to no export set, so a clip that is never retrieved has zero
effect on the scene or on an FBX/GLB.  Verified live (Maya 2025): evaluation
identical to a plain delete, absent from the FBX with bake-complex on and off,
survives Optimize Scene Size and save/reopen.

Why not a muted animation layer: layer membership permanently inserts
animBlendNodes on every member attribute, one layer per clip litters the Layer
Editor, and there is no Blender twin.  The layer IS the right tool for the
transient preview (:meth:`AnimUtils.create_preview_layer`), where its
lifetime is the preview's.

**Record.** The clip manifest (:class:`pythontk.KeyStash`) rides
``data_internal.key_stash`` as JSON through :class:`MayaScenePersistence`,
beside the shot store's channel.  Copy-before-cut: the manifest and the stash
nodes exist before a single live key is removed.

**Retrieve.** Curve-first: keys paste back onto the very curve node they left
(a layered curve that still exists needs no special casing); if that curve is
gone (Maya deletes an animCurve that loses its last key) they paste into the
blend-node socket it left when the channel is layered -- so they land on THEIR
layer, not whichever is active -- else onto the recorded plug, which recreates
it.  Objects are tracked by UUID + name, so a
rename in between is harmless; a deleted object keeps the record so the clip
can later be retrieved onto another target.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import maya.cmds as cmds
    import maya.mel as mel
except Exception:
    cmds = mel = None

from pythontk.core_utils.engines.key_stash.key_stash_model import (
    KeyStash as _KeyStashCore,
    StashChanged,
    StashedClip,
)

from mayatk.anim_utils._anim_utils import AnimUtils
from mayatk.anim_utils.shots._detection import Detection
from mayatk.anim_utils.shots._shots import MayaScenePersistence
from mayatk.anim_utils.smart_bake.bake_session import (
    BakeSessionStore,
    _BakeSessionStoreInternal,
)
from mayatk.core_utils._core_utils import CoreUtils


class _KeyStashInternal(object):
    """Scene-side helpers for :class:`KeyStash`."""

    #: message-multi attr on ``data_internal`` keeping the parked curves alive.
    REGISTRY_ATTR = "key_stash_curves"
    STASH_SUFFIX = "__keyStash"
    PREVIEW_LAYER = "keyStashPreview"
    #: ``pasteKey -option`` values a retrieve accepts.
    PASTE_OPTIONS = ("merge", "replace", "insert")

    @staticmethod
    def _plug_of(curve: str) -> Optional[str]:
        """The scene plug *curve* ultimately drives (through conversion / blend nodes)."""
        for attr, node in Detection.terminal_destinations(curve):
            if attr and attr != "message":
                return f"{node}.{attr}"
        return None

    @staticmethod
    def _blend_socket(curve: str) -> Optional[str]:
        """The animBlendNode input *curve* feeds, or ``None`` for a plain curve.

        A layered channel's curves plug into an animBlendNode (``inputA`` for
        the base animation, ``inputB`` for the layer).  Maya deletes a curve
        that loses its last key, and a paste onto the channel afterwards
        lands on whichever layer is "best" at the time -- a paste to the base
        by ``animLayer`` silently does nothing -- so the record keeps the
        socket and retrieve pastes there, recreating the curve where it was.
        """
        for dst in (
            cmds.listConnections(f"{curve}.output", d=True, s=False, plugs=True) or []
        ):
            if cmds.nodeType(dst.split(".")[0]).startswith("animBlendNode"):
                return dst
        return None

    @staticmethod
    def _long_name(node: str) -> str:
        found = cmds.ls(node, long=True) or []
        return found[0] if found else str(node)

    @staticmethod
    def _range_key_times(
        curves: Sequence[str], start: float, end: float
    ) -> Dict[str, List[float]]:
        """``{curve: [times]}`` for keys inside ``[start, end]`` (inclusive)."""
        out: Dict[str, List[float]] = {}
        for crv in curves:
            times = cmds.keyframe(crv, query=True, time=(start, end), timeChange=True)
            if times:
                out[crv] = sorted(set(times))
        return out

    @staticmethod
    def _filter_by_attributes(
        curves: Sequence[str], attributes: Sequence[str]
    ) -> List[str]:
        """Keep the curves whose driven plug is one of *attributes* (any spelling)."""
        wanted = set(attributes)
        kept = []
        for crv in curves:
            plug = _KeyStashInternal._plug_of(crv)
            if plug and not AnimUtils._plug_attr_names(plug).isdisjoint(wanted):
                kept.append(crv)
        return kept

    @classmethod
    def _stash_curve(
        cls, curve: str, times: Sequence[float], plug: str
    ) -> Dict[str, Any]:
        """Duplicate *curve*, keep only *times*, register + lock the copy.

        Returns the clip's curve record (``times`` for the engine; ``curve`` /
        ``plug`` / ``stash`` rename-safe refs for this adapter).  *plug* is the
        channel the curve drives (the caller has already resolved it).
        """
        dup = cmds.duplicate(curve, name=f"{curve}{cls.STASH_SUFFIX}")[0]
        keep = {round(float(t), 6) for t in times}
        for t in cmds.keyframe(dup, query=True, timeChange=True) or []:
            if round(float(t), 6) not in keep:
                cmds.cutKey(dup, time=(t, t), clear=True)
        internal = _BakeSessionStoreInternal._ensure_stash_registry(cls.REGISTRY_ATTR)
        cmds.connectAttr(
            f"{dup}.message", f"{internal}.{cls.REGISTRY_ATTR}", nextAvailable=True
        )
        cmds.lockNode(dup, lock=True)
        rec: Dict[str, Any] = {
            "times": [float(t) for t in times],
            "curve": BakeSessionStore.node_ref(curve),
            "plug": BakeSessionStore.plug_ref(plug),
            "stash": BakeSessionStore.node_ref(dup),
        }
        socket = cls._blend_socket(curve)
        if socket:
            rec["socket"] = BakeSessionStore.plug_ref(socket)
        return rec

    @classmethod
    def _release_stash(cls, rec: Dict[str, Any]) -> Optional[str]:
        """Unlock + deregister a record's stash node; return it (``None`` if gone)."""
        node = BakeSessionStore.resolve_ref(rec.get("stash"))
        if not node:
            return None
        cmds.lockNode(node, lock=False)
        for dst in (
            cmds.listConnections(
                f"{node}.message", source=False, destination=True, plugs=True
            )
            or []
        ):
            if cls.REGISTRY_ATTR in dst:
                cmds.disconnectAttr(f"{node}.message", dst)
        return node

    @classmethod
    def _delete_stash(cls, rec: Dict[str, Any]) -> bool:
        node = cls._release_stash(rec)
        if node:
            cmds.delete(node)
        return bool(node)

    @staticmethod
    def _cut_times(curve: str, times: Sequence[float]) -> None:
        for t in times:
            cmds.cutKey(curve, time=(t, t), clear=True)

    @staticmethod
    def _retrieve_target(rec: Dict[str, Any], target: Optional[str]) -> Optional[str]:
        """Where a record's keys paste back: explicit *target* plug, else the
        original curve node, else its blend-node socket, else the original
        plug.  ``None`` = nowhere."""
        plug_ref = rec.get("plug") or {}
        attr = plug_ref.get("attr")
        if target:
            if not attr:
                return None
            cand = f"{target}.{attr}"
            return cand if cmds.objExists(cand) else None
        curve = BakeSessionStore.resolve_ref(rec.get("curve"))
        if curve and cmds.nodeType(curve).startswith("animCurve"):
            return curve
        # The curve is gone: a layered channel's keys go back into the
        # blend-node socket they left, so they land on THEIR layer.
        socket = BakeSessionStore.resolve_plug(rec.get("socket"))
        if socket and cmds.objExists(socket):
            return socket
        plug = BakeSessionStore.resolve_plug(plug_ref)
        if plug and cmds.objExists(plug):
            return plug
        return None

    @staticmethod
    def _restore_curve_name(plug: str, rec: Dict[str, Any]) -> None:
        """Give the curve a paste just recreated on *plug* its recorded name.

        Maya names it after the plug -- right for ``cube.translateX``, wrong
        for a blend-node socket (``cube_translateX_lyr_inputA``).  A round
        trip should leave the scene's curve names as they were.
        """
        wanted = (rec.get("curve") or {}).get("name")
        made = cmds.listConnections(plug, s=True, d=False, type="animCurve") or []
        if wanted and made and made[0] != wanted and not cmds.objExists(wanted):
            cmds.rename(made[0], wanted)

    @staticmethod
    def _capture_playback() -> List[float]:
        return [
            cmds.playbackOptions(query=True, minTime=True),
            cmds.playbackOptions(query=True, maxTime=True),
        ]

    @staticmethod
    def _restore_playback(payload: Dict[str, Any]) -> None:
        rng = payload.get("playback")
        if rng and len(rng) == 2:
            cmds.playbackOptions(minTime=rng[0], maxTime=rng[1])


class KeyStash(_KeyStashCore, _KeyStashInternal):
    """:class:`pythontk.KeyStash` with the scene side bound to Maya.

    ``KeyStash.active()`` auto-installs :class:`MayaScenePersistence` on the
    ``key_stash`` channel, loads any clips saved in the scene, prunes records
    whose stash nodes are gone and tears down a preview left over from a save
    made mid-preview.

    The scene operations — :meth:`stash`, :meth:`retrieve`, :meth:`drop`,
    :meth:`preview`, :meth:`end_preview` — are each ONE undo chunk; manifest
    writes ride outside the undo queue (as the shot store's do), so an undone
    stash leaves a record whose node is gone until :meth:`reconcile` runs.
    """

    ATTR_NAME = "key_stash"

    # ---- singleton / hooks ---------------------------------------------

    @classmethod
    def active(cls) -> "KeyStash":
        """The active store, auto-installing the Maya backend once."""
        if cls._active is None and cls._persistence is None and cmds is not None:
            cls.set_persistence(
                MayaScenePersistence(attr_name=cls.ATTR_NAME, store_cls=cls)
            )
        return super().active()  # type: ignore[return-value]

    def _scene_fps(self) -> float:
        if cmds is None:
            return 24.0
        try:
            return float(mel.eval("currentTimeUnitToFPS"))
        except Exception:
            return 24.0

    def _schedule_flush(self) -> None:
        """Coalesce rapid mutations into one deferred write (mirror of ShotStore)."""
        if cmds is None:
            self._flush_dirty()
            return
        cmds.evalDeferred(self._flush_dirty, lowestPriority=True)

    def _on_activated(self) -> None:
        self.reconcile()

    def reconcile(self) -> List[int]:
        """Bring the record in line with the scene.

        Drops every clip none of whose stash nodes still exist (an undone
        stash, a manual delete) and ends a preview the record says is active
        (a scene saved mid-preview must not reopen silently overridden).

        Returns:
            The ids of the pruned clips.
        """
        if cmds is None:
            return []
        gone: List[int] = []
        for clip in list(self.clips):
            if not any(
                BakeSessionStore.resolve_ref(rec.get("stash")) for rec in clip.curves
            ):
                self.clips.remove(clip)
                gone.append(clip.clip_id)
        changed = bool(gone)
        if self.active_preview:
            AnimUtils.remove_preview_layer(
                BakeSessionStore.resolve_ref(self.active_preview.get("layer"))
            )
            self._restore_playback(self.active_preview)
            self.active_preview = None
            changed = True
        if changed:
            self.mark_dirty()
            self._notify(StashChanged("reloaded"))
        return gone

    # ---- operations ----------------------------------------------------

    def stash(
        self,
        objects: Optional[Sequence[str]] = None,
        time_range: Optional[Tuple[float, float]] = None,
        selected_keys: bool = False,
        attributes: Optional[Sequence[str]] = None,
        label: Optional[str] = None,
        source_shot_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[StashedClip]:
        """Move keys off the working animation into a stored clip.

        Two selection sources:

        * ``selected_keys=True`` — the Graph Editor key selection, per curve
          (optionally narrowed to *objects*' curves).
        * otherwise *objects* (default: the scene selection) and *time_range*
          — every key inside the inclusive range, optionally only on
          *attributes*.

        The gap the keys leave is left as-is (neighbours interpolate across
        it, exactly as after Delete Keys); the shots system owns gap holds.

        Parameters:
            objects: Source objects (any name form).
            time_range: ``(start, end)`` frames, inclusive.
            selected_keys: Use the Graph Editor selection instead of a range.
            attributes: Channel names to restrict a range stash to.
            label: Clip name; defaults to ``"<object> <start>-<end>"``.
            source_shot_id: Shot the keys belong to, when driven from the shots
                system.
            metadata: Free-form extras stored on the clip.

        Returns:
            The new clip, or ``None`` when the source held no keys.

        Raises:
            ValueError: Range mode without a range, or nothing to stash from.
        """
        if selected_keys:
            curves = (
                AnimUtils.objects_to_curves(list(objects), through_blends=True)
                if objects
                else None
            )
            mapping = AnimUtils.get_selected_key_times(curves)
        else:
            objects = list(objects) if objects else cmds.ls(selection=True, long=True)
            if not objects:
                raise ValueError("stash: no objects given or selected")
            if time_range is None:
                raise ValueError("stash: time_range is required unless selected_keys")
            curves = AnimUtils.objects_to_curves(objects, through_blends=True)
            if attributes:
                curves = self._filter_by_attributes(curves, attributes)
            mapping = self._range_key_times(
                curves, float(time_range[0]), float(time_range[1])
            )
        if not mapping:
            return None

        with CoreUtils.undo_chunk("Store Keys"):
            records = []
            owners: List[str] = []
            for curve, times in mapping.items():
                plug = self._plug_of(curve)
                if plug is None:
                    cmds.warning(f"KeyStash.stash: {curve} drives nothing — skipped")
                    continue
                owner = self._long_name(plug.partition(".")[0])
                if owner not in owners:
                    owners.append(owner)
                records.append(self._stash_curve(curve, times, plug))
            if not records:
                return None
            clip = self.add_clip(
                owners,
                records,
                label=label,
                source_shot_id=source_shot_id,
                metadata=metadata,
            )
            # Copy-before-cut: the manifest and the stash nodes are on disk-bound
            # state before a single live key goes.
            self.save()
            for rec in records:
                curve = BakeSessionStore.resolve_ref(rec["curve"])
                if curve:
                    self._cut_times(curve, rec["times"])
        return clip

    def retrieve(
        self,
        clip_id: int,
        at: Optional[float] = None,
        mode: str = "merge",
        target: Optional[str] = None,
    ) -> int:
        """Put a stored clip's keys back and forget the clip.

        Parameters:
            clip_id: The clip.
            at: Frame the clip's first key lands on; ``None`` = original frames.
            mode: ``"merge"`` (keys at the same time are replaced), ``"replace"``
                (existing keys inside the pasted range go), ``"insert"`` (later
                keys shift right) — ``pasteKey -option``.
            target: Paste onto this object's matching channels instead of the
                original (the original may be gone or renamed beyond recovery).

        Returns:
            Number of keys restored.  Records whose destination no longer
            exists, or refuses the paste, stay in the clip so it can be
            retrieved again or onto a *target*.

        Raises:
            KeyError: Unknown *clip_id*.  ValueError: unknown *mode*.
        """
        clip = self.get_clip(clip_id)
        if clip is None:
            raise KeyError(f"no stashed clip {clip_id}")
        if mode not in self.PASTE_OPTIONS:
            raise ValueError(f"mode must be one of {self.PASTE_OPTIONS}, got {mode!r}")
        if self.is_previewing(clip_id):
            self.end_preview()
        offset = self.offset_for(clip, at)
        restored = 0
        remaining: List[Dict[str, Any]] = []
        problems: List[str] = []
        with CoreUtils.undo_chunk("Retrieve Stored Keys"):
            for rec in clip.curves:
                stash = BakeSessionStore.resolve_ref(rec.get("stash"))
                if stash is None:
                    problems.append(
                        f"stash node for {(rec.get('plug') or {}).get('name')} is gone"
                    )
                    continue
                dst = self._retrieve_target(rec, target)
                if dst is None:
                    remaining.append(rec)
                    problems.append(
                        f"{(rec.get('plug') or {}).get('name')}."
                        f"{(rec.get('plug') or {}).get('attr')} no longer exists — "
                        "record kept; retrieve onto a target"
                    )
                    continue
                times = [float(t) for t in rec.get("times", ())]
                if times:
                    cmds.copyKey(stash, time=(min(times), max(times)))
                    paste: Dict[str, Any] = {"option": mode}
                    if offset:
                        paste["timeOffset"] = offset
                    try:
                        cmds.pasteKey(dst, **paste)
                    except RuntimeError as exc:
                        # A refused paste (locked or referenced target)
                        # keeps its record and stash node; the rest of
                        # the clip still lands and the clip stays
                        # retrievable, instead of one bad channel
                        # aborting the chunk half-applied.
                        remaining.append(rec)
                        problems.append(f"{dst}: {str(exc).strip()} -- record kept")
                        continue
                    restored += len(times)
                    if target is None and "." in dst:
                        self._restore_curve_name(dst, rec)
                self._delete_stash(rec)
            if remaining:
                clip.curves = remaining
                self.mark_dirty()
            else:
                self.remove_clip(clip_id, kind="retrieved")
            self.save()
        for msg in problems:
            cmds.warning(f"KeyStash.retrieve: {msg}")
        return restored

    def drop(self, clip_id: int) -> None:
        """Discard a stored clip and delete its stash nodes.

        Raises:
            KeyError: Unknown *clip_id*.
        """
        clip = self.get_clip(clip_id)
        if clip is None:
            raise KeyError(f"no stashed clip {clip_id}")
        if self.is_previewing(clip_id):
            self.end_preview()
        with CoreUtils.undo_chunk("Drop Stored Keys"):
            for rec in clip.curves:
                self._delete_stash(rec)
            self.remove_clip(clip_id, kind="dropped")
            self.save()

    # ---- preview -------------------------------------------------------

    def is_previewing(self, clip_id: Optional[int] = None) -> bool:
        """Whether a preview is active (for *clip_id*, when given)."""
        if not self.active_preview:
            return False
        return clip_id is None or self.active_preview.get("clip_id") == clip_id

    def preview(
        self,
        clip_id: int,
        in_context: bool = True,
        set_playback_range: bool = True,
    ) -> str:
        """Play a stored clip on its objects without retrieving it.

        A transient override layer at the top of the stack carries the stash
        curves; the base animation is untouched and comes back when the
        preview ends.  Only one preview is active at a time.

        Parameters:
            clip_id: The clip.
            in_context: Gate the layer's weight to the clip's range so the base
                animation plays outside it.  ``False`` holds the clip's end
                poses outside its keys instead (isolated view).
            set_playback_range: Clamp the time slider to the clip's range
                (restored by :meth:`end_preview`).

        Returns:
            The preview layer node.

        Raises:
            KeyError: Unknown *clip_id*.  RuntimeError: nothing of the clip is
            still in the scene to preview on.
        """
        clip = self.get_clip(clip_id)
        if clip is None:
            raise KeyError(f"no stashed clip {clip_id}")
        if self.active_preview:
            self.end_preview()
        sources: Dict[str, str] = {}
        for rec in clip.curves:
            stash = BakeSessionStore.resolve_ref(rec.get("stash"))
            plug = BakeSessionStore.resolve_plug(rec.get("plug"))
            if stash and plug and cmds.objExists(plug):
                sources[plug] = stash
        if not sources:
            raise RuntimeError("preview: none of the clip's objects are in the scene")
        payload: Dict[str, Any] = {"in_context": bool(in_context)}
        with CoreUtils.undo_chunk("Preview Stored Keys"):
            layer = AnimUtils.create_preview_layer(
                sources,
                gate=self.gate_range(clip) if in_context else None,
                name=self.PREVIEW_LAYER,
            )
            payload["layer"] = BakeSessionStore.node_ref(layer)
            if set_playback_range and clip.start is not None:
                payload["playback"] = self._capture_playback()
                cmds.playbackOptions(minTime=clip.start, maxTime=clip.end)
        self.set_preview(clip_id, payload)
        self.save()
        return layer

    def end_preview(self) -> bool:
        """Tear the preview layer down and restore the time slider.

        Returns:
            ``True`` if a preview was active.
        """
        payload = self.clear_preview()
        if payload is None:
            return False
        with CoreUtils.undo_chunk("End Stored Keys Preview"):
            AnimUtils.remove_preview_layer(
                BakeSessionStore.resolve_ref(payload.get("layer"))
            )
            self._restore_playback(payload)
        self.save()
        return True
