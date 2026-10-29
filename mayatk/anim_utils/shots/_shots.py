# coding=utf-8
"""Maya shots host — the DCC layer under ``pythontk``'s one shot store.

All of the shot *model* (:class:`ShotBlock`, :class:`ShotStore` CRUD/observer/
serialisation, typed store events, clip-spec resolution, the hand-off transfer)
lives once in ``pythontk.core_utils.engines.shots`` (the DCC-agnostic engine
shared with blendertk); ``mtk.ShotStore`` IS ``ptk.ShotStore``.  This module is
the thin Maya **acquisition + persistence** layer it is injected with:

- :class:`MayaScenePersistence` stores the serialized store on the shared
  ``data_internal`` carrier node (undo-safe writes, legacy-node migration,
  scene-lifecycle subscriptions via :class:`ScriptJobManager`).
- :class:`MayaShotsHost` is the store's :class:`pythontk.ShotsHost`: the
  scene reads, the named undo chunks and their pairing with Maya's queue, the
  deferred flush, the export carrier and the transfer's curve naming.  It is
  registered as this module is imported, so ``mtk.ShotStore.active()`` binds
  Maya with no entry point having run.

Cross-scene detection prefs live in the engine's JSON store
(``user_config_root()/shots/prefs.json`` — shared with Blender); legacy
QSettings values are migrated on first access (see
:meth:`MayaShotsHost.legacy_prefs`).
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

# The one store, re-exported: ``mtk.ShotStore`` (and this module's
# ``ShotStore``, which ``DataNodes.OWNERS`` and ``FbxUtils.PRODUCERS`` name).
from pythontk import ShotStore
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
    ShotsEdited,
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
    "ShotsEdited",
    "StoreInvalidated",
    "ScenePersistence",
    "MayaScenePersistence",
    "MayaShotsHost",
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
                save and rescaled on a frame-rate change.  Defaults to
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
        """The store class this backend serves (``ShotStore`` unless the key
        stash passed its own)."""
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
        from mayatk.core_utils.script_job_manager import ScriptJobManager

        # During a file read Maya can fire timeUnitChanged before the
        # SceneOpened invalidation — rescaling the OLD scene's still-active
        # store here would mark it dirty and flush its data onto the NEW
        # scene's carrier node.
        if ScriptJobManager.is_reading_file():
            return
        store = self.store_cls._active
        if store is None or store.is_empty():
            return
        new_fps = _MayaShotsHostInternal._get_scene_fps()
        old_fps = store.scene_fps
        if old_fps and abs(new_fps - old_fps) > 0.01:
            store.rescale_to_fps(new_fps)

    def _on_before_save(self, *args) -> None:
        """Flush dirty store data to the scene node before save."""
        store = self.store_cls._active
        if store is not None and store._dirty:
            store.save()


# ---------------------------------------------------------------------------
# Maya shots host
# ---------------------------------------------------------------------------


class _MayaShotsHostInternal(object):
    """Internal helpers for MayaShotsHost."""

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
    def _member_nodes(name) -> List[str]:
        """Long names of every transform answering to a doc object name --
        see :meth:`MayaShotsHost.member_nodes`."""
        try:
            import maya.cmds as cmds
        except ImportError:
            return [str(name)]
        name = str(name)
        hits = cmds.ls(name, long=True, type="transform") or []
        if not hits and ":" not in name.split("|")[-1]:
            # A referenced asset lives in a namespace the doc does not write.
            hits = cmds.ls(name, long=True, type="transform", recursive=True) or []
        return hits

    @staticmethod
    def _record_undo_step() -> None:
        """Put one no-op step in the open chunk, so Maya keeps the chunk.

        An edit that changed only shot RECORDS (a new shot, a delete that
        keeps the keys, a bound moved over empty frames) changes nothing Maya
        records, and Maya discards an empty chunk: the edit had no step of its
        own, so Maya's Ctrl+Z went to the operation before it and the shot
        stayed (2026-10-07: "new shot creation is not undoable" -- the first
        shot of a scene, every time).  With a step on the queue, Maya's own
        undo and redo reach the edit wherever the focus is, and the store's
        Undo/Redo listener applies its restore point
        (:meth:`MayaShotsHost.step_crossed`).

        The step re-sets the lock of the private data node's ``caching`` plug
        to what it already is: no value changes, nothing re-evaluates, and the
        record is a few bytes where re-writing the shot record would put a
        copy of it in every step.  The node is created outside the undo queue
        (:meth:`DataNodes.ensure_internal`), so undoing the step never takes
        it away.  blendertk's twin stamps an edit serial inside its step.
        """
        from mayatk.node_utils.data_nodes import DataNodes

        plug = f"{DataNodes.ensure_internal()}.caching"
        cmds.setAttr(plug, lock=cmds.getAttr(plug, lock=True))

    @staticmethod
    def _read_legacy_prefs() -> Optional[Dict[str, Any]]:
        """The pre-engine QSettings detection prefs under the prefs file's keys
        (``ShotStore._apply_prefs`` takes them as it takes the file's), or
        ``None`` when they cannot be read."""
        prefix = MayaShotsHost._QSETTINGS_PREFIX
        truthy = (True, "true", 1, "1")
        prefs: Dict[str, Any] = {}
        try:
            s = QSettings("uitk", "shots")
            dm = s.value(f"{prefix}/detection_mode")
            if dm is not None and str(dm) in ptk.ShotStore.DETECTION_MODES:
                prefs["detection_mode"] = str(dm)
            else:
                val = s.value(f"{prefix}/use_selected_keys")
                if val is not None and val in truthy:
                    kf = s.value(f"{prefix}/key_filter_mode")
                    prefs["detection_mode"] = (
                        str(kf) if kf in ("all", "skip_zero", "zero_as_end") else "all"
                    )
                # else leave at default "auto"
            sol = s.value(f"{prefix}/select_on_load")
            if sol is not None and sol in truthy:
                prefs["select_on_load"] = True
            dt = s.value(f"{prefix}/detection_threshold")
            if dt is not None:
                prefs["detection_threshold"] = dt
            fm = s.value(f"{prefix}/fit_mode")
            if fm is not None:
                prefs["fit_mode"] = str(fm)
            isl = s.value(f"{prefix}/initial_shot_length")
            if isl is not None:
                prefs["initial_shot_length"] = isl
            snap = s.value(f"{prefix}/snap_whole_frames")
            if snap is not None:
                prefs["snap_whole_frames"] = snap in truthy
        except Exception:
            return None
        return prefs


class MayaShotsHost(_MayaShotsHostInternal):
    """Maya's half of the shot store: the :class:`pythontk.ShotsHost` the one
    :class:`pythontk.ShotStore` asks about the scene.

    Registered as this module is imported (:meth:`resolve`), so
    ``mtk.ShotStore.active()`` binds Maya's scene, persistence, undo queue and
    export hooks with no entry point having run.  Each member answers the pure
    default outside Maya (``maya.cmds`` absent).  blendertk's
    ``BlenderShotsHost`` is its twin.
    """

    #: Maya keys keep real time when the scene's rate changes, so the shots
    #: rescale to follow them (``ShotStore.rescale_to_fps``).
    keys_keep_time = True

    #: The legacy QSettings namespace (pre-JSON prefs): a user's saved
    #: settings, read once by :meth:`legacy_prefs`.
    _QSETTINGS_PREFIX = "ShotStore"

    #: Monotonic source for unique chunk names — the marker that lets
    #: :meth:`undo_step` recognise its own entry on Maya's queue.
    _chunk_seq = itertools.count(1)

    #: ``{on_step: (ScriptJobManager, tokens)}``: the Undo / Redo
    #: subscriptions :meth:`install_undo_sync` made, one per store class.
    _native_undo_subs: Dict[Any, tuple] = {}

    #: The one adapter :meth:`resolve` hands out.
    _instance: Optional["MayaShotsHost"] = None

    @classmethod
    def resolve(cls) -> Optional["MayaShotsHost"]:
        """The adapter when this process runs Maya (``maya.cmds`` imports),
        else ``None`` -- the resolver :meth:`ShotStore.register_host` holds."""
        if cmds is None:
            return None
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ---- the scene and the store's lifecycle -----------------------------

    @staticmethod
    def available() -> bool:
        """Whether the scene can be reached (``maya.cmds`` imports)."""
        return cmds is not None

    @staticmethod
    def persistence() -> Optional[MayaScenePersistence]:
        """A new :class:`MayaScenePersistence` (``ShotStore.active`` installs
        it once), or ``None`` outside Maya."""
        return MayaScenePersistence() if cmds is not None else None

    @staticmethod
    def store_state() -> None:
        """Nothing kept per store: Maya's lookups ask the scene each time."""
        return None

    @staticmethod
    def scene_fps() -> float:
        """Current Maya scene framerate (24.0 outside Maya)."""
        return _MayaShotsHostInternal._get_scene_fps()

    @staticmethod
    def schedule_flush(store) -> None:
        """Coalesce rapid mutations into a single deferred write."""
        try:
            import maya.cmds as cmds

            cmds.evalDeferred(store._flush_dirty, lowestPriority=True)
        except ImportError:
            # Outside Maya (tests, standalone) — flush immediately.
            store._flush_dirty()

    @classmethod
    def legacy_prefs(cls) -> Optional[Dict[str, Any]]:
        """The detection prefs ``QSettings("uitk", "shots")`` held before the
        engine's cross-DCC JSON store (``user_config_root()/shots/prefs.json``,
        shared with Blender), including the even-older ``use_selected_keys`` +
        ``key_filter_mode`` pair, under the file's keys; ``None`` without Qt.
        The store asks once, while the file is absent, and writes them through
        (``ShotStore._restore_user_prefs``)."""
        if QSettings is None:
            return None
        return cls._read_legacy_prefs()

    @staticmethod
    def migrate_ledger(store) -> None:
        """Nothing to migrate: Maya claims by anim-curve node in every format."""
        return None

    @staticmethod
    def follow_renames(store) -> bool:
        """Nothing to follow: a Maya member is re-resolved where it is read
        (the sequencer's stale-path reconcile, the saved node UUIDs)."""
        return False

    # ---- scene reads -----------------------------------------------------

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

    @staticmethod
    def detection():
        """Maya's region detection (:class:`Detection`)."""
        return Detection

    @staticmethod
    def assess(store) -> Dict[int, str]:
        """Lightweight assessment: check if shot objects exist in the scene.

        Returns:
            Dict mapping ``shot_id`` → ``"valid"`` or
            ``"missing_object"``.
        """
        try:
            import maya.cmds as cmds
        except ImportError:
            return {s.shot_id: "valid" for s in store.shots}
        # Resolve the union of all shot objects in one ls call instead
        # of one per shot.  Objects are stored as long names (the
        # _resolve_long_names SSoT), so exact membership is the
        # contract — no second-guessing via objExists.
        all_objs = {obj for shot in store.shots for obj in shot.objects}
        existing = set(cmds.ls(list(all_objs), long=True) or []) if all_objs else set()
        return {
            shot.shot_id: (
                "valid"
                if all(obj in existing for obj in shot.objects)
                else "missing_object"
            )
            for shot in store.shots
        }

    @staticmethod
    def member_nodes(name: str) -> List[str]:
        """Long names of every transform answering to a doc object *name*.

        Exact first; a name the scene holds only inside a namespace (a
        referenced asset, ``AC:door_geo`` for ``door_geo``) is looked up
        next.  :meth:`~pythontk.ShotStore.resolve_member` reads one hit as
        found, several as ambiguous.
        """
        return _MayaShotsHostInternal._member_nodes(name)

    @staticmethod
    def existing_objects(store, names) -> set:
        """Which of *names* the scene still holds (``ptk.ShotStore.stale_shots``).

        A member is stored as its long DAG path, and one whose parent was
        renamed or reparented away is still the object the export view names
        by its leaf -- so a path that no longer resolves is looked up by its
        leaf, among transforms (a member is one; a material of that name is
        not it), before it counts as gone.  Two batched ``ls`` calls, whatever
        the member count, and a lookup per member neither finds.  A member
        whose leaf was renamed too is found by its saved node UUID
        (:meth:`_current_names`): a HOLD shot keys nothing in its frames, so
        without it the rename made the shot stale and its take left the
        export.
        """
        names = [str(n) for n in names]
        if cmds is None or not names:
            return set(names)
        found = set(cmds.ls(names, long=True) or [])
        present = {n for n in names if n in found}
        rest = [n for n in names if n not in present]
        if rest:
            leaves = sorted({store.leaf_name(n) for n in rest})
            held = {
                store.leaf_name(h) for h in cmds.ls(leaves, type="transform") or []
            }
            present.update(n for n in rest if store.leaf_name(n) in held)
            present.update(
                MayaShotsHost._current_names(
                    store, (n for n in rest if n not in present)
                )
            )
        return present

    @staticmethod
    def member_ids(names) -> Dict[str, str]:
        """``{name: node UUID}`` for each of *names* that answers to ONE node
        (``ptk.ShotStore._capture_member_ids``): saved with the store, so a
        member renamed behind its back is found again (:meth:`_current_names`)."""
        if cmds is None:
            return {}
        out = {}
        for name in names:
            try:
                found = cmds.ls(str(name), uuid=True) or []
            except Exception:  # an unparseable stored name: no id, no raise
                continue
            if len(found) == 1:
                out[str(name)] = found[0]
        return out

    @staticmethod
    def _current_names(store, names) -> Dict[str, str]:
        """``{name: long name now}`` for each of *names* no node answers to --
        by its path or by its leaf -- whose saved UUID (``member_ids``) names
        exactly one node: a member renamed with the store not told (a rename
        in the Outliner, panel closed).  Read-only.  A UUID that names no
        node -- deleted, or another scene's -- leaves the name as it was."""
        ids = store.member_ids
        if cmds is None or not ids:
            return {}
        out = {}
        for name in names:
            name = str(name)
            uid = ids.get(name)
            if not uid:
                continue
            try:
                if cmds.ls(name) or cmds.ls(store.leaf_name(name), type="transform"):
                    continue  # it answers by name: nothing to follow
                found = cmds.ls(uid, long=True) or []
            except Exception:  # an unparseable name or id: today's behaviour
                continue
            if len(found) == 1 and found[0] != name:
                out[name] = found[0]
        return out

    @staticmethod
    def keyed_windows(store, windows) -> List[bool]:
        """Per ``(start, end)`` window, whether any animation curve in the
        scene keys a frame inside it -- whatever the curve drives, layers
        included (``ptk.ShotStore.stale_shots``).  A curve that drives nothing
        animates nothing and is skipped: a stash an interrupted export leaked
        (``<curve>__snapshot``), a deleted rig's leftovers -- blendertk skips a
        userless action alike.  So is one whose only outputs are ``message``
        links: Key Stash clips and SmartBake's parked originals are duplicates
        kept alive by ``message -> <registry>.<multi>``, which drives nothing
        (SmartBake's own restore skips those links for the same reason) --
        blendertk skips an action only its fake user holds.  Read from each
        curve's own key index (``AnimUtils.curve_key_spans``): no key is listed
        or walked, but on the few curves the ledger samples -- a key the shot
        system holds as a bound sample (``edit_ledger``) is its bookkeeping,
        not content, as :meth:`members_keyed_windows` reads it.
        """
        windows = list(windows)
        if cmds is None:
            return [True] * len(windows)  # the pure default: never stale on a guess
        from mayatk.anim_utils._anim_utils import AnimUtils

        curves = cmds.ls(type="animCurve") or []
        pairs = (
            cmds.listConnections(
                curves, source=False, destination=True, connections=True
            )
            if curves
            else None
        ) or []
        wired = sorted(
            {
                plug.split(".")[0]
                for plug in pairs[::2]
                if plug.rpartition(".")[2] != "message"
            }
        )
        if not wired:
            return [False] * len(windows)
        ledger = store.edit_ledger
        sampled = set(ledger.keyed_curves())
        plain = [c for c in wired if c not in sampled]
        claimed = [c for c in wired if c in sampled]
        keyed = (
            [span is not None for span in AnimUtils.curve_key_spans(plain, windows)]
            if plain
            else [False] * len(windows)
        )
        # A gap hold pins every curve passing a new shot's start: that sample
        # alone must not fill a shot that names nothing (Delete Empty).
        for i, (start, end) in enumerate(windows):
            if not keyed[i] and claimed:
                keyed[i] = any(
                    not ledger.owns_key(crv, t)
                    for crv in claimed
                    for t in cmds.keyframe(crv, q=True, time=(start, end), tc=True)
                    or []
                )
        return keyed

    @staticmethod
    def members_keyed_windows(store, entries) -> List[Optional[bool]]:
        """Per ``(names, (start, end))``, whether a curve of the named objects
        keys a frame inside the window -- ``None`` when none of the names
        resolves (``ptk.ShotStore.empty_shots``).  Every curve driving a member,
        through layers; a key the shot system holds as a bound sample
        (``edit_ledger``) is its bookkeeping, not the shot's content.
        """
        if cmds is None:
            return [True] * len(entries)  # the pure default: never empty on a guess
        from mayatk.anim_utils._anim_utils import AnimUtils

        ledger = store.edit_ledger
        sampled = set(ledger.keyed_curves())
        out: List[Optional[bool]] = []
        for names, (start, end) in entries:
            nodes = _MayaShotsHostInternal._resolve_long_names(names)
            if not nodes:
                out.append(None)
                continue
            curves = AnimUtils.objects_to_curves(nodes)
            # The curves holding no sample answer from their key index at once
            # (the shot list's menu asks every scope each time it opens); only
            # the few the ledger samples are read key by key.
            plain = [c for c in curves if c not in sampled]
            if plain and AnimUtils.curve_key_spans(plain, [(start, end)])[0]:
                out.append(True)
                continue
            out.append(
                any(
                    not ledger.owns_key(crv, t)
                    for crv in curves
                    if crv in sampled
                    for t in cmds.keyframe(crv, q=True, time=(start, end), tc=True)
                    or []
                )
            )
        return out

    @staticmethod
    def audio_spans() -> List[tuple]:
        """Every audio track's on/off spans (``ptk.ShotStore.empty_shots``)."""
        if cmds is None:
            return []
        from mayatk.audio_utils._audio_utils import AudioUtils

        return [s for events in AudioUtils.read_all_events().values() for s in events]

    @staticmethod
    def curve_ledger_keys(store, objects=None):
        """``keys_of(curve)``: a Maya curve is claimed under its own anim-curve
        node name."""
        return lambda curve: (str(curve),)

    # ---- Maya's undo -----------------------------------------------------

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

    @classmethod
    @contextlib.contextmanager
    def undo_step(cls, store, label: str = "edit", snapshot: bool = True):
        """Run a shot edit as ONE uniquely NAMED undo chunk
        (``ptk.ShotStore.scene_edit``, which keeps the restore point's books).

        Tags the handle with ``(paired, marker)`` describing what Maya
        recorded:

        * ``paired`` — whether the chunk actually landed on the queue.  An edit
          that changed the shot records (the handle's ``changed``) puts a step
          there even when it touched no scene data (:meth:`_record_undo_step`),
          so it is ``False`` only for an edit that changed nothing, one that
          raised before changing anything, or one made with undo off; a
          consumer that fires ``cmds.undo()`` for an unpaired point would pop
          the user's PREVIOUS, unrelated operation.  (Verified: an empty chunk
          leaves ``undoName`` reporting the entry before it.)
        * ``marker`` — the queue's top right after the edit.  If it has
          changed by the time undo runs, something else happened since, so
          the native undo belongs to THAT and the restore point stays put.

        ``cancel()`` needs nothing here: Maya drops an empty chunk itself.  The
        chunk is named after *label* without its spaces
        (``shotSeq_MoveShot_12``).
        """
        edit = store.EditHandle()
        if cmds is None:
            yield edit
            return
        name = f"shotSeq_{''.join(label.split())}_{next(cls._chunk_seq)}"
        cmds.undoInfo(openChunk=True, chunkName=name)
        try:
            yield edit
            # Only for an edit that changed what the store alone holds: one
            # that changed nothing must not cost the user a dead Ctrl+Z.
            if edit.changed:
                cls._record_undo_step()
        finally:
            cmds.undoInfo(closeChunk=True)
            top = cls.undo_queue_top()
            edit.tag = (top == name, top)

    @classmethod
    def step_crossed(cls, marker, redo: bool = False) -> bool:
        """Whether the undo (or redo) Maya JUST performed is the chunk named
        *marker* (``ptk.ShotStore._native_step_crossed``).

        Maya fires its Undo/Redo events for every undo in the session, so the
        event alone says nothing about whose edit it was.  The entry Maya just
        moved is named by the OPPOSITE queue (verified: after ``cmds.undo()``,
        ``undoInfo -q -redoName`` returns the chunk that was undone), and the
        restore point records the marker its edit landed under
        (:meth:`undo_step`) -- so the two match only when the step is that
        edit.  Without the test, undoing an unrelated scene edit consumed a
        restore point and reverted shot bounds whose keys Maya had left
        exactly where they were.  An untagged point (*marker* ``None``, a
        legacy push) reads as crossed.
        """
        if marker is None:
            return True
        return marker == cls.undo_queue_top(redo=not redo)

    @classmethod
    def install_undo_sync(cls, on_step) -> None:
        """Subscribe *on_step* (the store's ``_on_native_step``) to Maya's Undo /
        Redo events (``MEventMessage``, through :class:`ScriptJobManager`, which
        tears them down with its own reset).  Once per store class and
        manager -- a manager reset re-arms it."""
        if cmds is None:
            return
        try:
            import maya.api.OpenMaya as om
            from mayatk.core_utils.script_job_manager import ScriptJobManager
        except Exception:
            return
        mgr = ScriptJobManager.instance()
        held = cls._native_undo_subs.get(on_step)
        if held is not None and held[0] is mgr:
            return
        owner = getattr(on_step, "__self__", cls)  # the store class, as before
        tokens = []
        for event, redo in (("Undo", False), ("Redo", True)):
            token = mgr.add_om_callback(
                om.MEventMessage.addEventCallback,
                event,
                cls._step_handler(on_step, redo),
                owner=owner,
            )
            if token is not None:
                tokens.append(token)
        cls._native_undo_subs[on_step] = (mgr, tokens)

    @staticmethod
    def _step_handler(on_step, redo: bool):
        """Maya's Undo (or Redo) event handler: the active store follows it."""

        def handler(*_args) -> None:
            on_step(redo=redo)

        return handler

    @classmethod
    def remove_undo_sync(cls, on_step) -> None:
        """Drop the Undo / Redo subscriptions :meth:`install_undo_sync` made
        for *on_step*."""
        held = cls._native_undo_subs.pop(on_step, None)
        if held is None:
            return
        mgr, tokens = held
        for token in tokens:
            mgr.unsubscribe(token)

    # ---- export ----------------------------------------------------------

    @staticmethod
    def publish_records(records) -> Optional[str]:
        """Commit the shot records onto the shared ``data_export`` node
        (``FbxUtils.publish_authored``); returns the export node name, or
        ``None`` when a clear had nothing to do."""
        from mayatk.env_utils.fbx_utils import FbxUtils
        from mayatk.node_utils.data_nodes import DataNodes

        FbxUtils.publish_authored(records)
        node = DataNodes.get_export_node(create=False)
        if node is None:
            return None
        # A clear on a carrier that never held the record had nothing to do.
        key = ptk.SceneRecords.SHOTS.key
        return node if cmds.attributeQuery(key, node=node, exists=True) else None

    @staticmethod
    def export_producer(enabled: bool) -> None:
        """Opt the shot record into (or out of) the any-export session hook
        (``FbxUtils.enable_export_producer``)."""
        from mayatk.env_utils.fbx_utils import FbxUtils

        if enabled:
            FbxUtils.enable_export_producer(ptk.SceneRecords.SHOTS)
        else:
            FbxUtils.disable_export_producer(ptk.SceneRecords.SHOTS)

    # ---- hand-off transfer (the manifest's ``shots`` section) ---------------

    @staticmethod
    def transfer() -> "_MayaShotTransfer":
        """How a Maya scene meets the hand-off codec (:class:`_MayaShotTransfer`)."""
        return _MayaShotTransfer()


class _MayaShotTransfer(object):
    """Maya's :class:`pythontk.ShotTransferHost`: how the hand-off codec
    (``pythontk.ShotTransfer``) names a Maya curve and finds a key.

    Neither FBX nor USD carries a shot, so the Blender bridge's
    ``.manifest.json`` sidecar does (``ptk.ShotStore.export_transfer`` /
    ``apply_transfer``); these only say how Maya spells it.
    """

    #: Hops followed from an animCurve toward the plug it drives (a
    #: unitConversion, an anim-layer blend node) before giving up.
    _TRANSFER_CURVE_HOPS = 3

    @staticmethod
    def spell(name: str) -> str:
        """FBX's spelling of a Maya name: the leaf, its namespace kept."""
        return ptk.ShotStore.leaf_name(name)

    @classmethod
    def curve_refs(cls):
        """``curve_ref(curve) -> (driven node, attribute) | None``."""
        return cls._curve_ref

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
    def curve_key(node: str, label: str) -> Optional[str]:
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
    def key_exists(curve: str, time: float) -> bool:
        """Whether *curve* holds a key at *time* (the ledger's own tolerance)."""
        if cmds is None or not cmds.objExists(curve):
            return False
        eps = ptk.ShotEditLedger().eps
        return bool(cmds.keyframe(curve, q=True, time=(time - eps, time + eps)))

    @staticmethod
    def resolve(leaf: str) -> Optional[str]:
        """The one scene node spelled *leaf*, long-named; ``None`` when absent or
        ambiguous (a consumer scoped to an import passes its own resolver)."""
        if cmds is None:
            return None
        hits = cmds.ls(leaf, long=True) or []
        return hits[0] if len(hits) == 1 else None

    @staticmethod
    def channels(names):
        """The render-effect channels *names* carry
        (``RenderEffects.channel_records``)."""
        from mayatk.mat_utils.render_opacity.render_effects import RenderEffects

        return RenderEffects.channel_records(names)

    @staticmethod
    def write_channels(node: str, records: Dict[str, Any]) -> int:
        """Land received render-effect *records* on *node*
        (``RenderEffects.apply_channel_records``)."""
        from mayatk.mat_utils.render_opacity.render_effects import RenderEffects

        return RenderEffects.apply_channel_records(node, records)

    @staticmethod
    def audio() -> List[Dict[str, Any]]:
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

    @staticmethod
    def write_audio(clips: List[Dict[str, Any]]) -> int:
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


# A registration that must run at import: ``ptk.ShotStore`` resolves the running
# host on first ask, so a script reaching ``mtk.ShotStore`` binds Maya's half.
ShotStore.register_host("maya", MayaShotsHost.resolve)
