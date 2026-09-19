# coding=utf-8
"""Maya shot-store adapter — the DCC layer over ``pythontk``'s shots engine.

All of the shot *model* (:class:`ShotBlock`, :class:`ShotStore` CRUD/observer/
serialisation, typed store events, clip-spec resolution) lives once in
``pythontk.core_utils.engines.shots`` (the DCC-agnostic engine shared with
blendertk); this module is the thin Maya **acquisition + persistence** layer:

- :class:`MayaScenePersistence` stores the serialized store on the shared
  ``data_internal`` carrier node (undo-safe writes, legacy-node migration,
  scene-lifecycle subscriptions via :class:`ScriptJobManager`).
- :class:`ShotStore` subclasses :class:`pythontk.ShotStore` and overrides the
  scene-reaching hooks (:meth:`_scene_fps`, :meth:`has_animation`,
  :meth:`detect_regions`, :meth:`assess`, :meth:`publish_export_view`,
  :meth:`_schedule_flush`, the export-preparer registration) with their
  original Maya implementations.

Cross-scene detection prefs live in the engine's JSON store
(``user_config_root()/shots/prefs.json`` — shared with Blender); legacy
QSettings values are migrated on first access (see
:meth:`ShotStore._restore_user_prefs`).
"""

import contextlib
import itertools
import logging

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:
    # Maya-soft: the planner chain (shot_plan → _shots) and headless
    # tests import this module without Maya; every cmds use below is
    # guarded by ``if cmds is None`` checks.
    cmds = None  # type: ignore[assignment]
    mel = None  # type: ignore[assignment]

from typing import Any, Dict, List, Optional

try:
    from qtpy.QtCore import QSettings
except ImportError:
    QSettings = None  # type: ignore[misc,assignment]

import pythontk as ptk
from pythontk.core_utils.engines.shots.shot_model import (  # noqa: F401 — re-exports
    SHOT_PALETTE,
    ShotBlock,
    StoreEvent,
    ShotDefined,
    ShotUpdated,
    ShotRemoved,
    ActiveShotChanged,
    SettingsChanged,
    BatchComplete,
    StoreInvalidated,
    ScenePersistence,
    CLIP_NAME_STRATEGIES,
)

# ``resolve_clip_specs`` moved onto :class:`pythontk.ShotStore`; re-export it under
# the historical flat name for mayatk-internal callers (e.g. the export view).
resolve_clip_specs = ptk.ShotStore.resolve_clip_specs

from mayatk.anim_utils.shots._detection import (
    CONTENT_ATTRS,
    Detection,
    STANDARD_TRANSFORM_ATTRS,
)

_log = logging.getLogger(__name__)

_DEFAULT_FPS = 24.0


__all__ = [
    "SHOT_PALETTE",
    "ShotBlock",
    "ShotStore",
    "StoreEvent",
    "ShotDefined",
    "ShotUpdated",
    "ShotRemoved",
    "ActiveShotChanged",
    "SettingsChanged",
    "BatchComplete",
    "StoreInvalidated",
    "ScenePersistence",
    "MayaScenePersistence",
    "STANDARD_TRANSFORM_ATTRS",
    "CONTENT_ATTRS",
    "Detection",
]


# ---------------------------------------------------------------------------
# Persistence backend
# ---------------------------------------------------------------------------


class MayaScenePersistence:
    """Persist ShotStore data to a string channel on ``data_internal``.

    The store rides the shared :class:`DataNodes` internal carrier (the same
    node SmartBake uses for its session manifests) so it persists with the
    scene but never exports — ``data_internal`` is a ``network`` node and
    can't serialise into an FBX.  Scenes written before the consolidation
    used a dedicated ``shotStore`` network node; that carrier is folded into
    ``data_internal`` transparently on first load.

    Registers ``SceneOpened`` / ``NewSceneOpened`` subscriptions via
    :class:`ScriptJobManager` so that :attr:`ShotStore._active` is
    automatically invalidated when the user opens or creates a scene.
    The subscriptions are *persistent* (not ephemeral) so they survive
    across scene switches.
    """

    ATTR_NAME = "shot_store"  # string channel on the shared ``data_internal`` node
    # Pre-consolidation carrier: a dedicated network node. Folded into
    # ``data_internal`` on first load (see :meth:`_migrate_legacy`).
    LEGACY_NODE_NAME = "shotStore"
    LEGACY_ATTR_NAME = "shotData"

    def __init__(self, attr_name: Optional[str] = None, store_cls=None):
        """
        Parameters:
            attr_name: String channel on ``data_internal`` (default ``shot_store``).
            store_cls: The active-store class this backend serves — the one
                whose ``_active`` is invalidated on scene change, flushed before
                save and rescaled on a frame-rate change.  Defaults to the Maya
                :class:`ShotStore`; the key stash passes its own class so the two
                stores ride the same carrier node on separate channels without
                either reacting to the other's scene events.
        """
        self._attr_name = attr_name or self.ATTR_NAME
        self._store_cls = store_cls
        self._before_save_cb_id = None  # OpenMaya callback id
        self._scene_subs_installed = False
        #: The record as this backend last wrote or read it
        #: (:meth:`record_changed`).
        self._last_raw: Optional[str] = None
        self._install_scene_jobs()

    @property
    def store_cls(self):
        """The store class this backend serves (resolved lazily: ``ShotStore``
        is defined below this class in the module)."""
        return self._store_cls if self._store_cls is not None else ShotStore

    def save(self, data: Dict[str, Any], undoable: bool = False) -> None:
        """Write *data* to the channel.

        Parameters:
            data: The store's ``to_dict()``.
            undoable: Record the write in the OPEN undo chunk, so an undo
                or redo of that operation moves the record with the scene
                edit it describes (the key stash's operations). Off by
                default: in interactive Maya deferred flushes fire AFTER a
                chunk closes, and a recorded one would become the top undo
                entry, keeping the real operation (e.g. a keyframe move)
                from being undone. mayapy runs them at once, inside the
                chunk, which is why the key stash batches its mutations.
        """
        if cmds is None:
            return
        from mayatk.node_utils.data_nodes import DataNodes
        from mayatk.core_utils._core_utils import CoreUtils

        raw = self._spec.encode(data)
        if undoable:
            DataNodes.write(ptk.Scope.PRIVATE, self._attr_name, raw)
        else:
            with CoreUtils.undo_disabled():
                DataNodes.write(ptk.Scope.PRIVATE, self._attr_name, raw)
        self._last_raw = raw

    @property
    def _spec(self) -> ptk.RecordSpec:
        """The record this backend serves (``shot_store`` / ``key_stash``),
        which owns the encoding; an unregistered channel name gets a shapeless
        private declaration so the backend still works for it."""
        return ptk.SceneRecords.by_key(self._attr_name, ptk.Scope.PRIVATE) or (
            ptk.RecordSpec(
                self._attr_name, ptk.Scope.PRIVATE, 1, "store", "", envelope=False
            )
        )

    def load(self) -> Optional[Dict[str, Any]]:
        """The stored record, or ``None`` when the scene holds none.

        Raises:
            ValueError: The channel holds text that is not the record (a
                truncated write).  Never read as "no record": the store would
                open EMPTY and its next save overwrite what is there.  The
                channel is left untouched.
        """
        if cmds is None:
            return None
        from mayatk.node_utils.data_nodes import DataNodes

        raw = DataNodes.read(ptk.Scope.PRIVATE, self._attr_name)
        if raw is None:
            raw = self._migrate_legacy()
        self._last_raw = raw
        if not raw:
            return None
        unreadable = object()
        data = self._spec.decode(raw, default=unreadable)
        if data is unreadable:
            raise ValueError(
                f"{DataNodes.INTERNAL}.{self._attr_name} holds {len(raw)} "
                "characters that are not a readable record; left untouched so "
                "nothing is saved over it. Repair or clear the attribute."
            )
        return data

    def record_changed(self) -> bool:
        """Whether the channel differs from what this backend last wrote or read.

        An undo or redo of a write made INSIDE an operation's chunk moves the
        record under a store that is already loaded; the store asks this to
        know it has to read the record again.
        """
        if cmds is None:
            return False
        from mayatk.node_utils.data_nodes import DataNodes

        return DataNodes.read(ptk.Scope.PRIVATE, self._attr_name) != self._last_raw

    def _migrate_legacy(self) -> Optional[str]:
        """Fold the pre-consolidation ``shotStore`` node into ``data_internal``.

        Reads the old dedicated carrier once, rewrites its payload onto the
        shared channel, and deletes the old node.  Undo-safe and effectively
        idempotent — the legacy node is gone after the first call.
        """
        # The legacy carrier held SHOT data. A backend serving another channel
        # (the key stash) must not fold it onto its own channel — and delete
        # the node from under the shot store that would have migrated it.
        if self._attr_name != self.ATTR_NAME:
            return None
        if not cmds.objExists(self.LEGACY_NODE_NAME):
            return None
        # The attr is the carrier's signature — a node that merely shares the
        # name (a user transform called "shotStore") must be left untouched.
        if not cmds.attributeQuery(
            self.LEGACY_ATTR_NAME, node=self.LEGACY_NODE_NAME, exists=True
        ):
            return None
        raw = cmds.getAttr(f"{self.LEGACY_NODE_NAME}.{self.LEGACY_ATTR_NAME}") or None

        from mayatk.node_utils.data_nodes import DataNodes
        from mayatk.core_utils._core_utils import CoreUtils

        with CoreUtils.undo_disabled():
            if raw:
                DataNodes.write(ptk.Scope.PRIVATE, self._attr_name, raw)
            # The legacy carrier had its name locked — unlock before delete.
            cmds.lockNode(self.LEGACY_NODE_NAME, lock=False, lockName=False)
            cmds.delete(self.LEGACY_NODE_NAME)
        return raw

    # ---- scene lifecycle subscriptions ------------------------------------

    def _install_scene_jobs(self) -> None:
        """Register persistent subscriptions via ScriptJobManager."""
        try:
            from mayatk.core_utils.script_job_manager import ScriptJobManager
        except Exception:
            return

        mgr = ScriptJobManager.instance()

        if not self._scene_subs_installed:
            mgr.subscribe("SceneOpened", self._on_scene_changed, owner=self)
            mgr.subscribe("NewSceneOpened", self._on_scene_changed, owner=self)
            mgr.subscribe("timeUnitChanged", self._on_time_unit_changed, owner=self)
            self._scene_subs_installed = True

        try:
            import maya.api.OpenMaya as om

            if self._before_save_cb_id is None:
                self._before_save_cb_id = mgr.add_om_callback(
                    om.MSceneMessage.addCallback,
                    om.MSceneMessage.kBeforeSave,
                    self._on_before_save,
                    owner=self,
                )
        except Exception:
            pass

    def remove_callbacks(self) -> None:
        """Tear down every SJM subscription owned by this store."""
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        ScriptJobManager.instance().unsubscribe_all(self)
        self._scene_subs_installed = False
        self._before_save_cb_id = None

    def _on_scene_changed(self) -> None:
        """Invalidate the cached store when a different scene is loaded."""
        self.store_cls.invalidate()

    def _on_time_unit_changed(self) -> None:
        """Rescale the store's timings when the scene framerate changes."""
        try:
            import maya.api.OpenMaya as om

            # During a file read Maya can fire timeUnitChanged before
            # the SceneOpened invalidation — rescaling the OLD scene's
            # still-active store here would mark it dirty and flush its
            # data onto the NEW scene's carrier node.
            if om.MFileIO.isReadingFile():
                return
        except Exception:
            pass
        store = self.store_cls._active
        if store is None or store.is_empty():
            return
        new_fps = _ShotStoreInternal._get_scene_fps()
        old_fps = store.scene_fps
        if old_fps and abs(new_fps - old_fps) > 0.01:
            store.rescale_to_fps(new_fps)

    def _on_before_save(self, *args) -> None:
        """Flush dirty store data to the scene node before save."""
        store = self.store_cls._active
        if store is not None and store._dirty:
            store.save()


# ---------------------------------------------------------------------------
# Maya shot store
# ---------------------------------------------------------------------------


class _ShotStoreInternal(object):
    """Internal helpers for ShotStore."""

    @staticmethod
    def _get_scene_fps() -> float:
        """Return the current Maya scene framerate, or *_DEFAULT_FPS* outside Maya."""
        if cmds is None:
            return _DEFAULT_FPS
        try:
            return float(mel.eval("float $fps = `currentTimeUnitToFPS`"))
        except Exception:
            return _DEFAULT_FPS

    @staticmethod
    def _resolve_long_names(names):
        """Resolve object names to long DAG paths.

        Returns only names that exist in the scene.  This is the single
        source of truth for disambiguation — all code paths that store or
        query Maya objects should go through this helper.
        """
        try:
            import maya.cmds as cmds
        except ImportError:
            return list(names) if names else []
        if not names:
            return []
        return cmds.ls(names, long=True) or []

    @staticmethod
    def _resolve_long_names_keep_missing(names):
        """Long-name-resolve *names*, keeping the caller's form for entries
        that don't (yet) exist in the scene.

        Unlike :func:`_resolve_long_names`, nothing is dropped: missing
        objects stay tracked under their original name so the pinned-object
        system can surface them as "missing" instead of silently losing
        them.  Ambiguous short names (multiple scene matches) also keep the
        caller's form.
        """
        try:
            import maya.cmds as cmds
        except ImportError:
            return list(names) if names else []
        resolved = []
        for n in names:
            hits = cmds.ls(n, long=True) or []
            resolved.append(hits[0] if len(hits) == 1 else n)
        return resolved


class ShotStore(ptk.ShotStore, _ShotStoreInternal):
    """:class:`pythontk.ShotStore` with the scene hooks bound to Maya.

    Only the DCC-reaching hooks are overridden; every CRUD / observer /
    serialisation behaviour is inherited unchanged from the pure engine.
    :meth:`active` auto-installs :class:`MayaScenePersistence` when Maya is
    available, so ``ShotStore.active()`` transparently loads any store saved
    in the current scene.
    """

    _QSETTINGS_PREFIX = "ShotStore"  # legacy QSettings namespace (pre-JSON prefs)

    # ---- singleton / persistence -----------------------------------------

    @classmethod
    def active(cls) -> "ShotStore":
        """Return the active store, auto-installing the Maya backend once."""
        if cls._active is None and cls._persistence is None and cmds is not None:
            cls.set_persistence(MayaScenePersistence())
        return super().active()  # type: ignore[return-value]

    # ---- undo pairing ------------------------------------------------------

    #: Monotonic source for unique chunk names — the marker that lets
    #: :meth:`scene_edit` recognise its own entry on Maya's queue.
    _chunk_seq = itertools.count(1)

    @staticmethod
    def undo_queue_top(redo: bool = False) -> str:
        """Name of the entry Maya's undo (or redo) would consume next.

        ``""`` when that queue is empty, or when Maya is unavailable.
        """
        if cmds is None:
            return ""
        try:
            if redo:
                return cmds.undoInfo(q=True, redoName=True) or ""
            return cmds.undoInfo(q=True, undoName=True) or ""
        except Exception:
            return ""

    @contextlib.contextmanager
    def scene_edit(self, label: str = "edit", snapshot: bool = True):
        """Run a boundary-mutating edit as ONE named, undo-paired step.

        Pushes a boundary restore point, runs the body inside a uniquely
        NAMED undo chunk, and tags the restore point with ``(paired,
        marker)`` describing what Maya recorded:

        * ``paired`` — whether the chunk actually landed on the queue.  An
          edit that moved only shot BOUNDS touches no scene data, and Maya
          discards an empty chunk, so nothing is recorded; a consumer that
          fires ``cmds.undo()`` anyway pops the user's PREVIOUS, unrelated
          operation.  (Verified: an empty chunk leaves ``undoName``
          reporting the entry before it.)
        * ``marker`` — the queue's top right after the edit.  If it has
          changed by the time undo runs, something else happened since, so
          the native undo belongs to THAT and this restore point stays put.

        Pass ``snapshot=False`` for an edit that must not record a restore
        point (it then only supplies the named chunk).
        """
        if cmds is None:
            yield
            return
        if snapshot:
            self.push_boundary_snapshot()
        name = f"shotSeq_{label}_{next(ShotStore._chunk_seq)}"
        cmds.undoInfo(openChunk=True, chunkName=name)
        try:
            yield
        finally:
            cmds.undoInfo(closeChunk=True)
            if snapshot:
                top = self.undo_queue_top()
                self.tag_boundary_snapshot((top == name, top))

    # ---- scene hooks -------------------------------------------------------

    def _scene_fps(self) -> float:
        """Current Maya scene framerate (24.0 outside Maya)."""
        return _ShotStoreInternal._get_scene_fps()

    def _schedule_flush(self) -> None:
        """Coalesce rapid mutations into a single deferred write."""
        try:
            import maya.cmds as cmds

            cmds.evalDeferred(self._flush_dirty, lowestPriority=True)
        except ImportError:
            # Outside Maya (tests, standalone) — flush immediately.
            self._flush_dirty()

    @staticmethod
    def has_animation() -> bool:
        """True if the scene contains animCurves driving transforms.

        This is a lightweight check — it only looks for the existence
        of animCurve nodes connected to transforms, not whether they
        contain meaningful motion.  Returns ``False`` outside Maya.
        """
        try:
            import maya.cmds as cmds
        except ImportError:
            return False
        curves = cmds.ls(type="animCurve") or []
        if not curves:
            return False
        # One batched connection query over every curve — sampling a
        # subset here previously false-negatived scenes whose first
        # curves drove non-transform nodes (blendshapes, materials).
        conns = set(cmds.listConnections(curves, d=True, s=False) or [])
        if not conns:
            return False
        if cmds.ls(list(conns), type="transform"):
            return True
        node_cache: dict = {}
        return any(Detection.resolve_to_transform(n, cache=node_cache) for n in conns)

    def detect_regions(self) -> List[Dict[str, Any]]:
        """Detect shot candidates using the store's detection settings.

        Dispatches to :func:`detect_shot_regions` (auto mode) or
        :func:`regions_from_selected_keys` (selected-keys modes)
        based on :attr:`detection_mode` and :attr:`detection_threshold`.

        Returns:
            List of candidate dicts with ``"name"``, ``"start"``,
            ``"end"``, and ``"objects"`` keys.
        """
        if self.detection_mode != "auto":
            return Detection.regions_from_selected_keys(
                gap_threshold=self.detection_threshold,
                key_filter=self.detection_mode,
            )
        return Detection.detect_shot_regions(gap_threshold=self.detection_threshold)

    def assess(self) -> Dict[int, str]:
        """Lightweight assessment: check if shot objects exist in the scene.

        Returns:
            Dict mapping ``shot_id`` → ``"valid"`` or
            ``"missing_object"``.
        """
        try:
            import maya.cmds as cmds
        except ImportError:
            return {s.shot_id: "valid" for s in self.shots}
        # Resolve the union of all shot objects in one ls call instead
        # of one per shot.  Objects are stored as long names (the
        # _resolve_long_names SSoT), so exact membership is the
        # contract — no second-guessing via objExists.
        all_objs = {obj for shot in self.shots for obj in shot.objects}
        existing = set(cmds.ls(list(all_objs), long=True) or []) if all_objs else set()
        return {
            shot.shot_id: (
                "valid"
                if all(obj in existing for obj in shot.objects)
                else "missing_object"
            )
            for shot in self.shots
        }

    def _resolve_long_names(self, names):
        """Resolve object names to long DAG paths (drops missing objects)."""
        return _ShotStoreInternal._resolve_long_names(names)

    # ---- export-view projection (Maya carriers) ----------------------------

    def publish_export_view(self, strategy: Optional[str] = None) -> Optional[str]:
        """Publish this store's shot records onto the shared ``data_export`` node.

        The authoring-time publish: the shot record :meth:`export_records`
        builds (each clip with its range) committed through
        ``FbxUtils.publish_authored``, handoff block included.  Idempotent;
        regenerated from the live store so it can't go stale.  An empty store
        **clears** the record (never creating the carrier just to hold it), and
        either way the commit clears a legacy ``fbx_takes`` the scene still
        holds — deleting the last shot must not leave the previous takes
        riding into the next export.  Returns the export node
        name, or ``None`` outside Maya / when a clear had nothing to do.

        An export pipeline does not call this: ``FbxUtils.PRODUCERS`` names
        :meth:`produce_export_records`, and the pipeline's own context (the
        Animation Clips mode) reaches the record there.
        """
        if cmds is None:
            return None
        from mayatk.env_utils.fbx_utils import FbxUtils
        from mayatk.node_utils.data_nodes import DataNodes

        records = {r.spec: r for r in self.export_records(strategy=strategy) or []}
        records.setdefault(ptk.SceneRecords.SHOTS, None)  # none = clear
        FbxUtils.publish_authored(records)
        node = DataNodes.get_export_node(create=False)
        if node is None:
            return None
        # A clear on a carrier that never held the record had nothing to do.
        key = ptk.SceneRecords.SHOTS.key
        return node if cmds.attributeQuery(key, node=node, exists=True) else None

    # ---- hand-off transfer (the manifest's ``shots`` section) ---------------
    #
    # Neither FBX nor USD carries a shot, so the Blender bridge's ``.manifest.json``
    # sidecar does: ``export_transfer`` writes the section every producer ships
    # (the in-process send and the pull conversion's mayapy) and ``apply_transfer``
    # rebuilds the store from it on every door in (the pull, the receiving
    # templates, the reference bake). The codec is ``pythontk.ShotTransfer``; this
    # class only says how Maya names a curve and finds a key.

    #: Hops followed from an animCurve toward the plug it drives (a
    #: unitConversion, an anim-layer blend node) before giving up.
    _TRANSFER_CURVE_HOPS = 3

    @classmethod
    def _curve_ref(cls, curve: str) -> Optional[tuple]:
        """``(driven node's long name, attribute)`` for a ledger curve, or ``None``.

        The ledger keys a claim by animCurve node; the far side has no such
        node, so the claim travels as the object + channel the curve drives.
        DG intermediaries (``unitConversion``, an animation layer's blend node)
        are stepped through, bounded, toward the DAG node -- the same hop
        :meth:`Detection.resolve_to_transform` takes.
        """
        if cmds is None or not cmds.objExists(curve):
            return None
        plugs = cmds.listConnections(curve, plugs=True, d=True, s=False) or []
        for _ in range(cls._TRANSFER_CURVE_HOPS):
            if not plugs:
                return None
            plug = plugs[0]
            node, _, attr = plug.partition(".")
            if cmds.ls(node, dag=True):
                long_names = cmds.ls(node, long=True) or []
                return (long_names[0] if long_names else node, attr) if attr else None
            plugs = cmds.listConnections(node, plugs=True, d=True, s=False) or []
        return None

    @staticmethod
    def _curve_key(node: str, label: str) -> Optional[str]:
        """The animCurve driving ``node.label``, through a unitConversion; else ``None``."""
        if cmds is None:
            return None
        plug = f"{node}.{label}"
        if not cmds.objExists(plug):
            return None
        curves = cmds.listConnections(plug, type="animCurve", s=True, d=False) or []
        if curves:
            return curves[0]
        upstream = cmds.listConnections(plug, s=True, d=False) or []
        if upstream and cmds.nodeType(upstream[0]) == "unitConversion":
            curves = (
                cmds.listConnections(
                    f"{upstream[0]}.input", type="animCurve", s=True, d=False
                )
                or []
            )
            if curves:
                return curves[0]
        return None

    @staticmethod
    def _key_exists(curve: str, time: float) -> bool:
        """Whether *curve* holds a key at *time* (the ledger's own tolerance)."""
        if cmds is None or not cmds.objExists(curve):
            return False
        eps = ptk.ShotEditLedger().eps
        return bool(cmds.keyframe(curve, q=True, time=(time - eps, time + eps)))

    @staticmethod
    def _resolve_transfer_name(leaf: str) -> Optional[str]:
        """The one scene node spelled *leaf*, long-named; ``None`` when absent or
        ambiguous (a consumer scoped to an import passes its own resolver)."""
        if cmds is None:
            return None
        hits = cmds.ls(leaf, long=True) or []
        return hits[0] if len(hits) == 1 else None

    @classmethod
    def export_transfer(
        cls, spell=None, objects: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """The active store as a hand-off ``shots`` section (``None`` when empty).

        Parameters:
            spell: How the carrier spells a Maya name -- the short name with its
                namespace for FBX (the default), the sanitized prim for USD
                (``BlenderBridge._manifest_spelling``); it must be the spelling
                the manifest's other sections use so one resolver serves all.
            objects: The exported transforms; scopes memberships and ledger
                claims to what actually ships (``None`` = the whole scene).
        """
        if cmds is None:
            return None
        from mayatk.mat_utils.render_opacity.render_effects import RenderEffects

        return ptk.ShotTransfer.encode(
            cls.active().to_dict(),
            spell=spell or ptk.ShotStore.leaf_name,
            curve_ref=cls._curve_ref,
            objects=objects,
            channels=RenderEffects.channel_records(objects),
            audio=cls._audio_records(),
        )

    @staticmethod
    def _audio_records() -> List[Dict[str, Any]]:
        """The scene's audio clips as the transfer's ``audio`` payload (the
        sequencer's own segments: track, file, placed span)."""
        from mayatk.audio_utils.segments import AudioSegment

        return [
            {
                "name": seg.track_id,
                "file": seg.file_path,
                "start": seg.start,
                "end": seg.end,
                "offset": 0.0,
            }
            for seg in AudioSegment.collect_all_segments(include_waveform=False)
        ]

    @classmethod
    def _write_audio(cls, clips: List[Dict[str, Any]]) -> int:
        """Land transfer ``audio`` clips as tracks (``AudioUtils.add_clip``),
        composited once at the end; a track already here is left alone, so a
        re-apply and the scene's own clips are never doubled."""
        from mayatk.audio_utils._audio_utils import AudioUtils

        added = 0
        for clip in clips or []:
            raw = str(clip.get("name") or "")
            path = str(clip.get("file") or "")
            if not path:
                continue
            try:
                track_id = AudioUtils.normalize_track_id(raw or path)
                if AudioUtils.has_track(track_id):
                    continue
                AudioUtils.add_clip(
                    path,
                    float(clip.get("start") or 0.0),
                    name=track_id,
                    frame_end=clip.get("end"),
                )
            except (ValueError, RuntimeError, OSError) as e:
                _log.warning("audio: clip %r not added (%s)", raw, e)
                continue
            added += 1
        if added:
            AudioUtils.sync()
        return added

    @classmethod
    def apply_transfer(
        cls,
        section: Dict[str, Any],
        *,
        resolve=None,
        frame_offset: float = 0.0,
        replace: bool = False,
        converted=None,
    ) -> Optional["ShotStore"]:
        """Rebuild the scene's shots from a hand-off ``shots`` section.

        Decodes against this scene (names through *resolve*, claims onto the
        animCurves now driving the imported nodes, times onto the scene's
        clock), folds the result into the scene's own store
        (:meth:`pythontk.ShotTransfer.merge`: a shot-less scene adopts it whole,
        one with shots gains the incoming shots after its own), persists the
        record and reloads the active store from it -- the path a scene open
        takes, so every panel rebinds as it does then.

        Parameters:
            section: The manifest's ``shots`` section.
            resolve: Carrier spelling -> imported node; default: the one scene
                node of that name.
            frame_offset: The importer's frame shift (none for Maya's importers).
            replace: Discard the scene's own shots instead of merging.
            converted: ``converted(node) -> bool``: the importer put *node*
                through the Y-up / Z-up crossing, so its claims' Y and Z
                channels are exchanged (``ptk.ShotTransfer.swap_up_axis``);
                the consumers pass "is a root". Default: none was.

        Returns:
            The active store after the apply, or ``None`` outside Maya.
        """
        if cmds is None or not section:
            return None
        store = cls.active()
        from mayatk.mat_utils.render_opacity.render_effects import RenderEffects

        decoded = ptk.ShotTransfer.decode(
            section,
            resolve=resolve or cls._resolve_transfer_name,
            curve_key=cls._curve_key,
            key_exists=cls._key_exists,
            scene_fps=store._scene_fps(),
            frame_offset=frame_offset,
            converted=converted,
            write_channels=RenderEffects.apply_channel_records,
            write_audio=cls._write_audio,
        )
        merged = (
            decoded if replace else ptk.ShotTransfer.merge(store.to_dict(), decoded)
        )
        if cls._persistence is None:
            cls.set_active(cls.from_dict(merged))
        else:
            cls._persistence.save(merged)
            cls.invalidate()
        return cls.active()

    @classmethod
    def _register_export_preparer(cls) -> None:
        """Opt the shot record into the any-export session hook unless the
        user explicitly opted out: ``FbxUtils.PRODUCERS`` already names
        :meth:`produce_export_records`, so enabling the record is all a File >
        Export needs to carry fresh shots."""
        if cls._auto_export_disabled:
            return
        try:
            from mayatk.env_utils.fbx_utils import FbxUtils

            FbxUtils.enable_export_producer(ptk.SceneRecords.SHOTS)
        except Exception:  # outside Maya / hooks unavailable — never block a save
            pass

    @classmethod
    def _unregister_export_preparer(cls) -> None:
        """Opt the shot record out of the session hook."""
        from mayatk.env_utils.fbx_utils import FbxUtils

        FbxUtils.disable_export_producer(ptk.SceneRecords.SHOTS)

    # ---- cross-scene user preferences ------------------------------------

    def _restore_user_prefs(self) -> None:
        """Apply detection prefs, migrating legacy QSettings on first run.

        Prefs moved from ``QSettings("uitk", "shots")`` to the engine's
        cross-DCC JSON store (``user_config_root()/shots/prefs.json``) so Maya
        and Blender share one detection-prefs source.  When the JSON file
        already exists the engine restore runs as-is; otherwise any legacy
        QSettings values (including the even-older ``use_selected_keys`` +
        ``key_filter_mode`` pair) are read once, applied, and written through
        to the JSON store so the migration never re-runs.
        """
        try:
            prefs_exist = self._prefs_path().exists()
        except Exception:
            prefs_exist = True  # can't probe — fall through to the engine
        if prefs_exist or QSettings is None:
            super()._restore_user_prefs()
            return
        self._migrate_legacy_qsettings_prefs()

    def _migrate_legacy_qsettings_prefs(self) -> None:
        """One-time QSettings → JSON prefs migration (pre-engine stores)."""
        try:
            s = QSettings("uitk", "shots")
            dm = s.value(f"{self._QSETTINGS_PREFIX}/detection_mode")
            if dm is not None and str(dm) in self.DETECTION_MODES:
                self.detection_mode = str(dm)
            else:
                val = s.value(f"{self._QSETTINGS_PREFIX}/use_selected_keys")
                if val is not None and val in (True, "true", 1, "1"):
                    kf = s.value(f"{self._QSETTINGS_PREFIX}/key_filter_mode")
                    self.detection_mode = (
                        str(kf) if kf in ("all", "skip_zero", "zero_as_end") else "all"
                    )
                # else leave at default "auto"
            sol = s.value(f"{self._QSETTINGS_PREFIX}/select_on_load")
            if sol is not None and sol in (True, "true", 1, "1"):
                self.select_on_load = True
            dt = s.value(f"{self._QSETTINGS_PREFIX}/detection_threshold")
            if dt is not None:
                try:
                    self.detection_threshold = float(dt)
                except (TypeError, ValueError):
                    pass
            fm = s.value(f"{self._QSETTINGS_PREFIX}/fit_mode")
            if fm is not None and str(fm) in self.FIT_MODES:
                self.fit_mode = str(fm)
            isl = s.value(f"{self._QSETTINGS_PREFIX}/initial_shot_length")
            if isl is not None:
                try:
                    self.initial_shot_length = float(isl)
                except (TypeError, ValueError):
                    pass
            snap = s.value(f"{self._QSETTINGS_PREFIX}/snap_whole_frames")
            if snap is not None:
                self.snap_whole_frames = snap in (True, "true", 1, "1")
        except Exception:
            return
        # Persist immediately so the JSON store exists and the legacy
        # read never runs again.
        self._save_user_prefs()
