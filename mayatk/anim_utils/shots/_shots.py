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
    _sanitize_clip_name,
    resolve_clip_specs,
)

from mayatk.anim_utils.shots._detection import (  # noqa: F401 — re-exports
    STANDARD_TRANSFORM_ATTRS,
    _map_standard_curves_to_transforms,
    detect_shot_regions,
    _filter_flat_objects,
    regions_from_selected_keys,
    resolve_to_transform,
)

_log = logging.getLogger(__name__)

ATTR_NAME = "shot_store"  # string channel on the shared ``data_internal`` node
# Pre-consolidation carrier: a dedicated network node. Folded into
# ``data_internal`` on first load (see MayaScenePersistence._migrate_legacy).
LEGACY_NODE_NAME = "shotStore"
LEGACY_ATTR_NAME = "shotData"
_DEFAULT_FPS = 24.0


def _get_scene_fps() -> float:
    """Return the current Maya scene framerate, or *_DEFAULT_FPS* outside Maya."""
    if cmds is None:
        return _DEFAULT_FPS
    try:
        return float(mel.eval("float $fps = `currentTimeUnitToFPS`"))
    except Exception:
        return _DEFAULT_FPS


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
    "detect_shot_regions",
    "regions_from_selected_keys",
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

    def __init__(self, attr_name: str = ATTR_NAME):
        self._attr_name = attr_name
        self._before_save_cb_id = None  # OpenMaya callback id
        self._scene_subs_installed = False
        self._install_scene_jobs()

    def save(self, data: Dict[str, Any]) -> None:
        if cmds is None:
            return
        import json
        from mayatk.node_utils.data_nodes import DataNodes

        # Persistence writes must not pollute the undo queue.  They
        # fire via evalDeferred AFTER an UndoChunk closes and would
        # otherwise become the top undo entry, preventing the real
        # operation (e.g. keyframe move) from being undone.
        prev_undo_state = cmds.undoInfo(q=True, state=True)
        cmds.undoInfo(stateWithoutFlush=False)
        try:
            DataNodes.set_internal_string(self._attr_name, json.dumps(data))
        finally:
            cmds.undoInfo(stateWithoutFlush=prev_undo_state)

    def load(self) -> Optional[Dict[str, Any]]:
        if cmds is None:
            return None
        import json
        from mayatk.node_utils.data_nodes import DataNodes

        raw = DataNodes.get_internal_string(self._attr_name)
        if raw is None:
            raw = self._migrate_legacy()
        if not raw:
            return None
        return json.loads(raw)

    def _migrate_legacy(self) -> Optional[str]:
        """Fold the pre-consolidation ``shotStore`` node into ``data_internal``.

        Reads the old dedicated carrier once, rewrites its payload onto the
        shared channel, and deletes the old node.  Undo-safe and effectively
        idempotent — the legacy node is gone after the first call.
        """
        if not cmds.objExists(LEGACY_NODE_NAME):
            return None
        # The attr is the carrier's signature — a node that merely shares the
        # name (a user transform called "shotStore") must be left untouched.
        if not cmds.attributeQuery(LEGACY_ATTR_NAME, node=LEGACY_NODE_NAME, exists=True):
            return None
        raw = cmds.getAttr(f"{LEGACY_NODE_NAME}.{LEGACY_ATTR_NAME}") or None

        from mayatk.node_utils.data_nodes import DataNodes

        prev_undo_state = cmds.undoInfo(q=True, state=True)
        cmds.undoInfo(stateWithoutFlush=False)
        try:
            if raw:
                DataNodes.set_internal_string(self._attr_name, raw)
            # The legacy carrier had its name locked — unlock before delete.
            cmds.lockNode(LEGACY_NODE_NAME, lock=False, lockName=False)
            cmds.delete(LEGACY_NODE_NAME)
        finally:
            cmds.undoInfo(stateWithoutFlush=prev_undo_state)
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
        ShotStore._active = None
        ShotStore._notify_invalidated()

    def _on_time_unit_changed(self) -> None:
        """Rescale shot timings when the scene framerate changes."""
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
        store = ShotStore._active
        if store is None or not store.shots:
            return
        new_fps = _get_scene_fps()
        old_fps = store.scene_fps
        if old_fps and abs(new_fps - old_fps) > 0.01:
            store.rescale_to_fps(new_fps)

    def _on_before_save(self, *args) -> None:
        """Flush dirty store data to the scene node before save."""
        store = ShotStore._active
        if store is not None and store._dirty:
            store.save()


# ---------------------------------------------------------------------------
# Maya shot store
# ---------------------------------------------------------------------------


class ShotStore(ptk.ShotStore):
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

    # ---- scene hooks -------------------------------------------------------

    def _scene_fps(self) -> float:
        """Current Maya scene framerate (24.0 outside Maya)."""
        return _get_scene_fps()

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
        return any(resolve_to_transform(n, cache=node_cache) for n in conns)

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
            return regions_from_selected_keys(
                gap_threshold=self.detection_threshold,
                key_filter=self.detection_mode,
            )
        return detect_shot_regions(gap_threshold=self.detection_threshold)

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
        existing = (
            set(cmds.ls(list(all_objs), long=True) or []) if all_objs else set()
        )
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
        return _resolve_long_names(names)

    # ---- export-view projection (Maya carriers) ----------------------------

    def publish_export_view(self, strategy: Optional[str] = None) -> Optional[str]:
        """Project the export view onto the shared ``data_export`` node.

        Writes the ``fbx_takes`` and ``shot_metadata`` channels as plain string
        attrs (JSON).  Idempotent; regenerated from the live store so it can't go
        stale.  Returns the export node name, or ``None`` outside Maya / on error.
        """
        try:
            import json
            from mayatk.node_utils.data_nodes import DataNodes
        except ImportError:
            return None

        view = self.to_export_view(strategy=strategy or self.clip_name_strategy)
        DataNodes.set_export_string(
            DataNodes.FBX_TAKES, json.dumps(view["fbx_takes"], ensure_ascii=True)
        )
        return DataNodes.set_export_string(
            DataNodes.SHOT_METADATA,
            json.dumps(view["shot_metadata"], ensure_ascii=True),
        )

    @classmethod
    def _register_export_preparer(cls) -> None:
        """Install the session preparer unless the user explicitly opted out."""
        if cls._auto_export_disabled:
            return
        try:
            from mayatk.env_utils.fbx_utils import FbxUtils

            FbxUtils.register_export_preparer("shots", cls.refresh_export_view)
        except Exception:  # outside Maya / hooks unavailable — never block a save
            pass

    @classmethod
    def _unregister_export_preparer(cls) -> None:
        """Remove the before-export preparer from the FBX exporter."""
        from mayatk.env_utils.fbx_utils import FbxUtils

        FbxUtils.unregister_export_preparer("shots")

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
