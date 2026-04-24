# coding=utf-8
"""Shared shot data model and persistent store.

Provides :class:`ShotBlock` (the fundamental shot data structure) and
:class:`ShotStore` (CRUD + observer).  Both :mod:`shot_sequencer` and
:mod:`shot_manifest` import from here so they share the same data types
and — via :meth:`ShotStore.active` — the same live instance.

Persistence is pluggable: call :meth:`ShotStore.set_persistence` with a
backend that implements ``save(data)`` / ``load() -> dict | None``.
:class:`MayaScenePersistence` is the default backend when PyMEL is
available.
"""
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    List,
    Optional,
    Protocol,
    Tuple,
    runtime_checkable,
)
from contextlib import contextmanager

try:
    import pymel.core as pm
except ImportError:
    pm = None

try:
    from qtpy.QtCore import QSettings
except ImportError:
    QSettings = None  # type: ignore[misc,assignment]

from mayatk.anim_utils.shots._detection import (
    STANDARD_TRANSFORM_ATTRS,
    _map_standard_curves_to_transforms,
    detect_shot_regions,
    _filter_flat_objects,
    regions_from_selected_keys,
)

NODE_NAME = "shotStore"
ATTR_NAME = "shotData"
_DEFAULT_FPS = 24.0


def _get_scene_fps() -> float:
    """Return the current Maya scene framerate, or *_DEFAULT_FPS* outside Maya."""
    if pm is None:
        return _DEFAULT_FPS
    try:
        return float(pm.mel.eval("float $fps = `currentTimeUnitToFPS`"))
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
# Persistence protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ScenePersistence(Protocol):
    """Interface for saving / loading ShotStore data."""

    def save(self, data: Dict[str, Any]) -> None: ...

    def load(self) -> Optional[Dict[str, Any]]: ...


class MayaScenePersistence:
    """Persist ShotStore data to a Maya network-node attribute.

    Registers ``SceneOpened`` / ``NewSceneOpened`` subscriptions via
    :class:`ScriptJobManager` so that :attr:`ShotStore._active` is
    automatically invalidated when the user opens or creates a scene.
    The subscriptions are *persistent* (not ephemeral) so they survive
    across scene switches.
    """

    def __init__(
        self,
        node_name: str = NODE_NAME,
        attr_name: str = ATTR_NAME,
    ):
        self._node_name = node_name
        self._attr_name = attr_name
        self._before_save_cb_id = None  # OpenMaya callback id
        self._scene_subs_installed = False
        self._install_scene_jobs()

    def save(self, data: Dict[str, Any]) -> None:
        if pm is None:
            return
        import json
        import maya.cmds as cmds
        from mayatk.node_utils._node_utils import NodeUtils

        # Persistence writes must not pollute the undo queue.  They
        # fire via evalDeferred AFTER an UndoChunk closes and would
        # otherwise become the top undo entry, preventing the real
        # operation (e.g. keyframe move) from being undone.
        cmds.undoInfo(stateWithoutFlush=False)
        try:
            node = NodeUtils.ensure_data_node(self._node_name, self._attr_name)
            node.attr(self._attr_name).set(json.dumps(data))
        finally:
            cmds.undoInfo(stateWithoutFlush=True)

    def load(self) -> Optional[Dict[str, Any]]:
        if pm is None:
            return None
        import json

        if not pm.objExists(self._node_name):
            return None
        node = pm.PyNode(self._node_name)
        if not node.hasAttr(self._attr_name):
            return None
        raw = node.attr(self._attr_name).get()
        if not raw:
            return None
        return json.loads(raw)

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
# Shared shot palette (single source of truth for both UIs)
# ---------------------------------------------------------------------------

try:
    from pythontk import Palette

    SHOT_PALETTE = Palette.status().alias(
        {
            "csv_object": "valid",  # expected — no color
            "scene_discovered": "info",  # found in scene, not in CSV
            "missing_object": "error",  # referenced but missing
            "missing_behavior": "warn",  # expected behaviour keys absent
            "user_animated": "info",  # custom user animation detected
            "additional": "warn",  # unexpected scene objects
            "collision": "error",  # timing overlap
            "missing_shot": "info",  # shot not yet built
        }
    )
except ImportError:
    SHOT_PALETTE = {}  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Data Structure
# ---------------------------------------------------------------------------


@dataclass
class ShotBlock:
    """Represents a single shot (contiguous animation range).

    Attributes:
        shot_id: Unique identifier for the shot.
        name: Human-readable label (e.g. "Intro", "Shot_1").
        start: First frame of the shot.
        end: Last frame of the shot.
        objects: Transform node names that belong to this shot.
        metadata: Arbitrary key/value pairs (section, behaviors, …).
        locked: If True, the shot has been finalized by the user and
            should not be flagged/modified by automated assessment.
        description: Free-text description of the shot content.
    """

    shot_id: int
    name: str
    start: float
    end: float
    objects: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    locked: bool = False
    description: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start

    def classify_objects(self) -> Dict[str, str]:
        """Return ``{obj_name: status_key}`` using stored metadata.

        Resolution order per object:

        1. ``metadata["object_status"][obj]`` — written by manifest
           assessment (richest: missing_object / missing_behavior /
           user_animated / valid).
        2. ``metadata["csv_objects"]`` membership — if present and the
           object is *not* listed → ``"scene_discovered"``.
        3. Fallback → ``"valid"``.

        Both the manifest and sequencer use this method so that
        classification logic lives in one place.
        """
        statuses = self.metadata.get("object_status", {})
        raw_csv = self.metadata.get("csv_objects", [])
        csv_objs = set((e["name"] if isinstance(e, dict) else e) for e in raw_csv)
        result: Dict[str, str] = {}
        for obj in self.objects:
            if obj in statuses:
                result[obj] = statuses[obj]
            elif csv_objs and obj not in csv_objs:
                result[obj] = "scene_discovered"
            else:
                result[obj] = "valid"
        return result


# ---------------------------------------------------------------------------
# Store events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoreEvent:
    """Base class for typed :class:`ShotStore` events.

    Each subclass carries event-specific payload as typed fields.
    The ``name`` class variable matches the legacy string event name
    for backward compatibility with the Qt ``app_event`` signal.
    """

    name: ClassVar[str] = ""


@dataclass(frozen=True)
class ShotDefined(StoreEvent):
    """A new shot was created and added to the store."""

    name: ClassVar[str] = "shot_defined"
    shot: ShotBlock


@dataclass(frozen=True)
class ShotUpdated(StoreEvent):
    """An existing shot's fields were modified."""

    name: ClassVar[str] = "shot_updated"
    shot: ShotBlock


@dataclass(frozen=True)
class ShotRemoved(StoreEvent):
    """A shot was removed from the store."""

    name: ClassVar[str] = "shot_removed"
    shot_id: int = 0


@dataclass(frozen=True)
class ActiveShotChanged(StoreEvent):
    """The active (selected) shot changed."""

    name: ClassVar[str] = "active_shot_changed"
    shot_id: Optional[int] = None


@dataclass(frozen=True)
class SettingsChanged(StoreEvent):
    """Detection-relevant settings were modified."""

    name: ClassVar[str] = "settings_changed"


@dataclass(frozen=True)
class BatchComplete(StoreEvent):
    """A :meth:`ShotStore.batch_update` context has exited."""

    name: ClassVar[str] = "batch_complete"


@dataclass(frozen=True)
class StoreInvalidated(StoreEvent):
    """The active store was discarded (scene change / new scene).

    Listeners should rebind to the new :meth:`ShotStore.active` instance.
    Fired on class-level invalidation listeners registered via
    :meth:`ShotStore.add_invalidation_listener`.
    """

    name: ClassVar[str] = "store_invalidated"


# ---------------------------------------------------------------------------
# ShotStore
# ---------------------------------------------------------------------------


class ShotStore:
    """Central store for shot data with pluggable persistence.

    Parameters:
        shots: Initial shot list.  Copied on construction.
    """

    _active: Optional["ShotStore"] = None
    _persistence: Optional[ScenePersistence] = None
    _invalidation_listeners: ClassVar[List[Callable[["StoreInvalidated"], None]]] = []
    _QSETTINGS_PREFIX = "ShotStore"
    DETECTION_MODES = ("auto", "all", "skip_zero", "zero_as_end")
    FIT_MODES = ("extend_only", "fit_contents")
    DEFAULT_INITIAL_SHOT_LENGTH: float = 200.0
    DEFAULT_FIT_MODE: str = "extend_only"
    DEFAULT_SNAP_WHOLE_FRAMES: bool = True

    def __init__(
        self,
        shots: Optional[List[ShotBlock]] = None,
    ):
        self.shots: List[ShotBlock] = list(shots) if shots else []
        self.hidden_objects: set = set()
        self.pinned_objects: set = set()
        self.markers: List[Dict[str, Any]] = []
        self.gap: float = 0.0
        self.detection_threshold: float = 5.0
        self.detection_mode: str = "auto"  # "auto", "all", "skip_zero", "zero_as_end"
        # Shot construction policy (applies to any caller that builds shots —
        # manifest, sequencer, future tools).  ``fit_mode`` governs whether a
        # shot may shrink below ``initial_shot_length`` to fit its contents.
        self.initial_shot_length: float = self.DEFAULT_INITIAL_SHOT_LENGTH
        self.fit_mode: str = self.DEFAULT_FIT_MODE
        # When enabled, every frame value written through ``snap()`` is
        # rounded to the nearest integer.  Applied at mutation sites so the
        # in-memory model is always valid (see ``ShotStore.snap``).
        self.snap_whole_frames: bool = self.DEFAULT_SNAP_WHOLE_FRAMES
        self.select_on_load: bool = False
        self.frame_on_shot_change: bool = True
        self.locked_gaps: set = set()  # {(left_shot_id, right_shot_id), ...}
        self.locked_objects: set = set()  # object names locked in the sequencer
        self.scene_fps: float = _get_scene_fps()
        # Source CSV path (when the store was populated from a manifest CSV).
        # Purely informational — lets the user retrace provenance on reopen.
        self.source_csv: str = ""
        self._active_shot_id: Optional[int] = None  # session-only, not persisted
        self._listeners: List[Callable[[StoreEvent], None]] = []
        self._batch_depth: int = 0
        self._batch_events: List[tuple] = []
        self._dirty: bool = False

    # ---- active shot (session state, not persisted) ----------------------

    @property
    def active_shot_id(self) -> Optional[int]:
        """The currently selected shot, or ``None``."""
        return self._active_shot_id

    def set_active_shot(self, shot_id: Optional[int]) -> None:
        """Set the active shot and notify listeners."""
        if shot_id == self._active_shot_id:
            return
        self._active_shot_id = shot_id
        self._notify(ActiveShotChanged(shot_id=shot_id))

    # ---- observer --------------------------------------------------------

    def notify_settings_changed(self) -> None:
        """Fire a ``"settings_changed"`` event.

        Call after modifying detection-relevant settings such as
        ``detection_mode``, ``detection_threshold``, or ``gap`` so
        downstream consumers (e.g. the Shot Manifest) can invalidate
        cached results and re-detect.
        """
        self._notify(SettingsChanged())

    def add_listener(self, callback: Callable[[StoreEvent], None]) -> None:
        """Register a listener called on store mutations.

        The callback receives a single :class:`StoreEvent` instance.
        Use ``isinstance()`` to dispatch on event type::

            def on_event(event: StoreEvent) -> None:
                if isinstance(event, ShotDefined):
                    print(event.shot)

        Event types: :class:`ShotDefined`, :class:`ShotUpdated`,
        :class:`ShotRemoved`, :class:`ActiveShotChanged`,
        :class:`SettingsChanged`, :class:`BatchComplete`.
        """
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[StoreEvent], None]) -> None:
        """Remove a previously registered listener."""
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def _notify(self, event: StoreEvent) -> None:
        """Fire all registered listeners (deferred during :meth:`batch_update`)."""
        if self._batch_depth > 0:
            self._batch_events.append(event)
            return
        for cb in self._listeners:
            try:
                cb(event)
            except Exception:
                pass

    @contextmanager
    def batch_update(self):
        """Defer listener notifications until the block exits.

        On exit a single ``"batch_complete"`` event is fired instead of
        the individual events that were accumulated.
        """
        self._batch_depth += 1
        try:
            yield
        finally:
            self._batch_depth -= 1
            if self._batch_depth == 0 and self._batch_events:
                self._batch_events.clear()
                _evt = BatchComplete()
                for cb in self._listeners:
                    try:
                        cb(_evt)
                    except Exception:
                        pass
                # Synchronous flush — batch = single atomic write.
                self._flush_dirty()

    # ---- gap locking -----------------------------------------------------

    def is_gap_locked(self, left_id: str, right_id: str) -> bool:
        """Return whether the gap between two adjacent shots is locked."""
        return (left_id, right_id) in self.locked_gaps

    def lock_gap(self, left_id: str, right_id: str) -> None:
        """Lock a gap so its width is preserved during global respace."""
        self.locked_gaps.add((left_id, right_id))

    def unlock_gap(self, left_id: str, right_id: str) -> None:
        """Unlock a gap so it follows the global gap value."""
        self.locked_gaps.discard((left_id, right_id))

    def lock_all_gaps(self) -> None:
        """Lock every adjacent gap."""
        sorted_shots = self.sorted_shots()
        for i in range(len(sorted_shots) - 1):
            self.locked_gaps.add((sorted_shots[i].shot_id, sorted_shots[i + 1].shot_id))

    def unlock_all_gaps(self) -> None:
        """Unlock every gap."""
        self.locked_gaps.clear()

    # ---- singleton / persistence -----------------------------------------

    @classmethod
    def set_persistence(cls, backend: Optional[ScenePersistence]) -> None:
        """Set the persistence backend used by :meth:`active` and :meth:`save`.

        Pass ``None`` to disable persistence (pure in-memory mode).
        Call *before* :meth:`active` to ensure load picks up the backend.
        """
        cls._persistence = backend

    @classmethod
    def active(cls) -> "ShotStore":
        """Return the current active store, creating one if needed.

        If a persistence backend is configured (or PyMEL is available),
        saved data is loaded automatically on first access.
        """
        if cls._active is None:
            persistence = cls._persistence
            if persistence is None and pm is not None:
                persistence = MayaScenePersistence()
                cls._persistence = persistence
            if persistence is not None:
                data = persistence.load()
                if data:
                    cls._active = cls.from_dict(data)
                    # Reconcile FPS: if the scene was saved at a different
                    # framerate, rescale shot timings to match the current one.
                    current_fps = _get_scene_fps()
                    if (
                        cls._active.shots
                        and abs(cls._active.scene_fps - current_fps) > 0.01
                    ):
                        cls._active.rescale_to_fps(current_fps)
                else:
                    cls._active = cls()
                    cls._active._restore_user_prefs()
            else:
                cls._active = cls()
                cls._active._restore_user_prefs()
        return cls._active

    @classmethod
    def set_active(cls, store: "ShotStore") -> None:
        """Replace the active store instance."""
        cls._active = store

    @classmethod
    def clear_active(cls) -> None:
        """Reset the active store and persistence backend."""
        cls._active = None
        cls._persistence = None

    # ---- invalidation listeners (class-level) ----------------------------

    @classmethod
    def add_invalidation_listener(
        cls, callback: Callable[["StoreInvalidated"], None]
    ) -> None:
        """Register a callback fired when the active store is discarded.

        Unlike instance-level :meth:`add_listener`, these survive across
        store instances — useful for UI controllers that need to rebind
        after a scene change.
        """
        if callback not in cls._invalidation_listeners:
            cls._invalidation_listeners.append(callback)

    @classmethod
    def remove_invalidation_listener(
        cls, callback: Callable[["StoreInvalidated"], None]
    ) -> None:
        """Remove a previously registered invalidation listener."""
        try:
            cls._invalidation_listeners.remove(callback)
        except ValueError:
            pass

    @classmethod
    def _notify_invalidated(cls) -> None:
        """Fire all invalidation listeners."""
        event = StoreInvalidated()
        for cb in cls._invalidation_listeners:
            try:
                cb(event)
            except Exception:
                pass

    # ---- cross-scene user preferences ------------------------------------

    def _restore_user_prefs(self) -> None:
        """Apply detection preferences from QSettings (cross-scene).

        Called when a fresh store is created (no per-scene persistence)
        so that ``detection_mode`` survives scene changes without
        requiring the shots settings panel to be opened.

        Handles migration from the legacy ``use_selected_keys`` +
        ``key_filter_mode`` pair.
        """
        if QSettings is None:
            return
        try:
            s = QSettings()
            # New key first
            dm = s.value(f"{self._QSETTINGS_PREFIX}/detection_mode")
            if dm is not None and str(dm) in self.DETECTION_MODES:
                self.detection_mode = str(dm)
            else:
                # Migrate legacy keys
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
            pass

    def _save_user_prefs(self) -> None:
        """Persist detection preferences to QSettings (cross-scene)."""
        if QSettings is None:
            return
        try:
            s = QSettings()
            s.setValue(
                f"{self._QSETTINGS_PREFIX}/detection_mode",
                self.detection_mode,
            )
            s.setValue(
                f"{self._QSETTINGS_PREFIX}/select_on_load",
                self.select_on_load,
            )
            s.setValue(
                f"{self._QSETTINGS_PREFIX}/detection_threshold",
                self.detection_threshold,
            )
            s.setValue(f"{self._QSETTINGS_PREFIX}/fit_mode", self.fit_mode)
            s.setValue(
                f"{self._QSETTINGS_PREFIX}/initial_shot_length",
                self.initial_shot_length,
            )
            s.setValue(
                f"{self._QSETTINGS_PREFIX}/snap_whole_frames",
                self.snap_whole_frames,
            )
        except Exception:
            pass

    # ---- frame snapping --------------------------------------------------

    def snap(self, frame: float) -> float:
        """Return *frame* rounded to the nearest integer when snapping is on.

        Single chokepoint for the ``snap_whole_frames`` policy.  Call at
        any site that writes a frame value to a shot, keyframe, or
        timeline range to guarantee the in-memory model stays valid.
        """
        if self.snap_whole_frames:
            return float(round(frame))
        return float(frame)

    # ---- derived queries --------------------------------------------------

    def compute_gap(self) -> float:
        """Derive the predominant inter-shot gap from current shot positions.

        Returns the median gap between consecutive shots (rounded to the
        nearest integer).  When fewer than two shots exist the current
        ``self.gap`` value is returned unchanged.
        """
        shots = self.sorted_shots()
        if len(shots) < 2:
            return self.gap
        gaps = [
            max(0, round(shots[i + 1].start - shots[i].end))
            for i in range(len(shots) - 1)
        ]
        gaps.sort()
        mid = len(gaps) // 2
        median = gaps[mid] if len(gaps) % 2 else round((gaps[mid - 1] + gaps[mid]) / 2)
        return float(median)

    # ---- CRUD ------------------------------------------------------------

    def sorted_shots(self) -> List[ShotBlock]:
        """Return shots ordered by start time."""
        return sorted(self.shots, key=lambda s: s.start)

    def shot_by_id(self, shot_id: int) -> Optional[ShotBlock]:
        for s in self.shots:
            if s.shot_id == shot_id:
                return s
        return None

    def shot_by_name(self, name: str) -> Optional[ShotBlock]:
        """Return the first shot whose name matches *name*, or ``None``."""
        for s in self.shots:
            if s.name == name:
                return s
        return None

    def define_shot(
        self,
        name: str,
        start: float,
        end: float,
        objects: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        locked: bool = False,
        description: str = "",
    ) -> ShotBlock:
        """Create a new shot and add it to the store.

        Parameters:
            name: Human-readable label.
            start: First frame.
            end: Last frame.
            objects: Transform node names.  ``None`` → empty list.
            metadata: Arbitrary key/value pairs.
            locked: Mark this shot as user-finalized.

        Returns:
            The newly created :class:`ShotBlock`.
        """
        if objects is None:
            objects = []
        else:
            objects = _resolve_long_names(objects) or list(objects)
        new_id = max((s.shot_id for s in self.shots), default=-1) + 1
        block = ShotBlock(
            shot_id=new_id,
            name=name,
            start=self.snap(start),
            end=self.snap(end),
            objects=sorted(set(objects)),
            metadata=dict(metadata) if metadata else {},
            locked=locked,
            description=description,
        )
        self.shots.append(block)
        self._notify(ShotDefined(shot=block))
        self.mark_dirty()
        return block

    def update_shot(
        self,
        shot_id: int,
        *,
        start: Optional[float] = None,
        end: Optional[float] = None,
        name: Optional[str] = None,
        objects: Optional[List[str]] = None,
        description: Optional[str] = None,
        locked: Optional[bool] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[ShotBlock]:
        """Update fields on an existing shot.  Returns the shot, or ``None``."""
        shot = self.shot_by_id(shot_id)
        if shot is None:
            return None
        if start is not None:
            shot.start = self.snap(start)
        if end is not None:
            shot.end = self.snap(end)
        if name is not None:
            shot.name = name
        if objects is not None:
            resolved = _resolve_long_names(objects)
            shot.objects = sorted(set(resolved or objects))
        if description is not None:
            shot.description = description
        if locked is not None:
            shot.locked = locked
        if metadata is not None:
            shot.metadata = dict(metadata)
        self._notify(ShotUpdated(shot=shot))
        self.mark_dirty()
        return shot

    def ripple_shift(
        self,
        after_frame: float,
        delta: float,
        exclude_id: Optional[int] = None,
    ) -> None:
        """Shift all shots starting at or after *after_frame* by *delta*.

        Parameters:
            after_frame: Only shots whose start is >= this value are moved.
            delta: Frames to add (positive) or subtract (negative).
            exclude_id: Optional shot id to skip (the shot being resized).
        """
        if abs(delta) < 1e-6:
            return
        delta = self.snap(delta)
        for s in self.sorted_shots():
            if s.shot_id == exclude_id:
                continue
            if s.start >= after_frame - 1e-6:
                s.start = self.snap(s.start + delta)
                s.end = self.snap(s.end + delta)
        self.mark_dirty()

    def ripple_shift_upstream(
        self,
        before_frame: float,
        delta: float,
        exclude_id: Optional[int] = None,
    ) -> None:
        """Shift all shots ending at or before *before_frame* by *delta*.

        Upstream counterpart of :meth:`ripple_shift`.

        Parameters:
            before_frame: Only shots whose end is <= this value are moved.
            delta: Frames to add (positive) or subtract (negative).
            exclude_id: Optional shot id to skip (the shot being resized).
        """
        if abs(delta) < 1e-6:
            return
        delta = self.snap(delta)
        for s in self.sorted_shots():
            if s.shot_id == exclude_id:
                continue
            if s.end <= before_frame + 1e-6:
                s.start = self.snap(s.start + delta)
                s.end = self.snap(s.end + delta)
        self.mark_dirty()

    def remove_shot(self, shot_id: int) -> bool:
        """Remove a shot by ID.  Returns ``True`` if found."""
        for i, s in enumerate(self.shots):
            if s.shot_id == shot_id:
                self.shots.pop(i)
                self._notify(ShotRemoved(shot_id=shot_id))
                self.mark_dirty()
                return True
        return False

    def append_shot(
        self,
        name: str,
        duration: float,
        gap: float = 0,
        start_frame: Optional[float] = None,
        objects: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        locked: bool = False,
        description: str = "",
    ) -> ShotBlock:
        """Append a shot after the last existing shot, with gap-aware placement.

        Parameters:
            name: Human-readable label.
            duration: Shot duration in frames.
            gap: Gap frames after the previous shot.
            start_frame: Explicit start frame.  If ``None``, computed
                from the last shot's end + *gap*.
            objects: Transform node names.
            metadata: Arbitrary key/value pairs.
            locked: Mark this shot as user-finalized.

        Returns:
            The newly created :class:`ShotBlock`.
        """
        if start_frame is None:
            sorted_s = self.sorted_shots()
            start_frame = (sorted_s[-1].end + gap) if sorted_s else 0.0
        return self.define_shot(
            name=name,
            start=start_frame,
            end=start_frame + duration,
            objects=objects,
            metadata=metadata,
            locked=locked,
            description=description,
        )

    # ---- visibility ------------------------------------------------------

    def is_object_hidden(self, obj_name: str) -> bool:
        """Return True if *obj_name* is hidden in the sequencer UI."""
        return obj_name in self.hidden_objects

    def set_object_hidden(self, obj_name: str, hidden: bool = True) -> None:
        """Show or hide *obj_name* in the sequencer UI."""
        if hidden:
            self.hidden_objects.add(obj_name)
        else:
            self.hidden_objects.discard(obj_name)

    # ---- pinning ---------------------------------------------------------

    def is_object_pinned(self, obj_name: str) -> bool:
        """Return True if *obj_name* is pinned (kept even when missing)."""
        return obj_name in self.pinned_objects

    def set_object_pinned(self, obj_name: str, pinned: bool = True) -> None:
        """Pin or unpin *obj_name*.

        Pinned objects remain visible in the sequencer with a
        'missing' indicator when they no longer exist in the scene.
        Non-pinned objects are silently removed from tracks.
        """
        if pinned:
            self.pinned_objects.add(obj_name)
        else:
            self.pinned_objects.discard(obj_name)

    # ---- object removal --------------------------------------------------

    def remove_object_from_shots(self, obj_name: str) -> None:
        """Remove *obj_name* from every shot's object list."""
        for shot in self.shots:
            if obj_name in shot.objects:
                shot.objects.remove(obj_name)
        self.pinned_objects.discard(obj_name)
        self.hidden_objects.discard(obj_name)

    # ---- serialisation ---------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialise shots and settings to a plain dict."""
        return {
            "shots": [
                {
                    "shot_id": s.shot_id,
                    "name": s.name,
                    "start": s.start,
                    "end": s.end,
                    "objects": list(s.objects),
                    "metadata": dict(s.metadata) if s.metadata else {},
                    "locked": s.locked,
                    "description": s.description,
                }
                for s in self.sorted_shots()
            ],
            "hidden_objects": sorted(self.hidden_objects),
            "pinned_objects": sorted(self.pinned_objects),
            "markers": list(self.markers),
            "gap": self.gap,
            "detection_threshold": self.detection_threshold,
            "detection_mode": self.detection_mode,
            "initial_shot_length": self.initial_shot_length,
            "fit_mode": self.fit_mode,
            "snap_whole_frames": self.snap_whole_frames,
            "select_on_load": self.select_on_load,
            "frame_on_shot_change": self.frame_on_shot_change,
            "locked_gaps": [list(pair) for pair in sorted(self.locked_gaps)],
            "scene_fps": self.scene_fps,
            "source_csv": self.source_csv,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ShotStore":
        """Restore from serialised data.

        Parameters:
            data: Dict with ``"shots"`` list and optional
                ``"hidden_objects"`` / ``"markers"`` keys.
        """
        shot_list = data.get("shots", [])
        hidden = data.get("hidden_objects", [])
        pinned = data.get("pinned_objects", [])
        shots = [
            ShotBlock(
                shot_id=d["shot_id"],
                name=d["name"],
                start=d["start"],
                end=d["end"],
                objects=d.get("objects", []),
                metadata=d.get("metadata", {}),
                locked=d.get("locked", False),
                description=d.get("description", ""),
            )
            for d in shot_list
        ]
        store = cls(shots)
        store.hidden_objects = set(hidden)
        store.pinned_objects = set(pinned)
        store.markers = data.get("markers", [])
        store.gap = float(data.get("gap", 0.0))
        store.detection_threshold = float(data.get("detection_threshold", 5.0))
        # Migrate legacy use_selected_keys + key_filter_mode if present
        dm = data.get("detection_mode")
        if dm is not None:
            store.detection_mode = str(dm)
        elif data.get("use_selected_keys"):
            kf = data.get("key_filter_mode", "all")
            store.detection_mode = (
                str(kf) if kf in ("all", "skip_zero", "zero_as_end") else "all"
            )
        store.select_on_load = bool(data.get("select_on_load", False))
        try:
            store.initial_shot_length = float(
                data.get("initial_shot_length", cls.DEFAULT_INITIAL_SHOT_LENGTH)
            )
        except (TypeError, ValueError):
            store.initial_shot_length = cls.DEFAULT_INITIAL_SHOT_LENGTH
        fm = data.get("fit_mode")
        store.fit_mode = str(fm) if fm in cls.FIT_MODES else cls.DEFAULT_FIT_MODE
        snap = data.get("snap_whole_frames")
        store.snap_whole_frames = (
            bool(snap) if snap is not None else cls.DEFAULT_SNAP_WHOLE_FRAMES
        )
        store.frame_on_shot_change = bool(data.get("frame_on_shot_change", True))
        store.locked_gaps = {tuple(pair) for pair in data.get("locked_gaps", [])}
        stored_fps = data.get("scene_fps")
        if stored_fps is not None:
            store.scene_fps = float(stored_fps)
        store.source_csv = str(data.get("source_csv", "") or "")
        return store

    # ---- persistence convenience -----------------------------------------

    def rescale_to_fps(self, new_fps: float) -> None:
        """Scale all shot timings from the current ``scene_fps`` to *new_fps*.

        Called automatically when Maya's time-unit changes.  Updates
        ``scene_fps``, rescales shot boundaries, gap, and markers,
        then fires a :class:`BatchComplete` so the UI repaints.
        """
        old_fps = self.scene_fps
        if not old_fps or abs(new_fps - old_fps) < 0.01:
            return
        ratio = new_fps / old_fps
        for shot in self.shots:
            shot.start = round(shot.start * ratio)
            shot.end = round(shot.end * ratio)
        self.gap = round(self.gap * ratio, 2)
        for marker in self.markers:
            if "time" in marker:
                marker["time"] = round(marker["time"] * ratio)
        self.scene_fps = new_fps
        self.mark_dirty()
        self._notify(BatchComplete())

    def mark_dirty(self) -> None:
        """Flag the store as needing a save.

        Inside a :meth:`batch_update` block the flush is deferred to
        the block exit.  Otherwise an ``evalDeferred`` callback
        coalesces rapid mutations into a single write.
        """
        self._dirty = True
        if self._batch_depth > 0:
            return
        try:
            import maya.cmds as cmds

            cmds.evalDeferred(self._flush_dirty, lowestPriority=True)
        except ImportError:
            # Outside Maya (tests, standalone) — flush immediately.
            self._flush_dirty()

    def _flush_dirty(self) -> None:
        """Write to the persistence backend if the dirty flag is set."""
        if not self._dirty:
            return
        self.save()

    def save(self) -> None:
        """Persist via the configured backend (no-op if none set).

        Also writes detection preferences to QSettings so they survive
        across scenes even when the shots settings panel is not opened.
        """
        self._dirty = False
        if self._persistence is not None:
            self._persistence.save(self.to_dict())
        self._save_user_prefs()

    # ---- detection convenience -------------------------------------------

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
        # Check a sample — if any curve drives a transform, we have animation
        for crv in curves[:50]:
            conns = cmds.listConnections(crv, d=True, s=False) or []
            for node in conns:
                if cmds.nodeType(node) == "transform":
                    return True
                parents = (
                    cmds.listRelatives(
                        node, parent=True, type="transform", fullPath=True
                    )
                    or []
                )
                if parents:
                    return True
        return False

    @property
    def is_detection_relevant(self) -> bool:
        """True when detection settings are actionable.

        Returns False when shots already exist in the store (detection
        settings would have no effect — shots are already defined).
        """
        return not bool(self.shots)

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

    def _overlaps_existing(self, candidate: Dict[str, Any]) -> bool:
        """True if *candidate* overlaps any existing shot's range."""
        c_start = candidate["start"]
        c_end = candidate["end"]
        for shot in self.shots:
            if c_start < shot.end and c_end > shot.start:
                return True
        return False

    def detect_and_define(self, overwrite: bool = False) -> List[ShotBlock]:
        """Detect shot regions and define them in the store.

        Convenience method that calls :meth:`detect_regions` and
        :meth:`define_shot` for each candidate.  Wraps all mutations
        in :meth:`batch_update` for a single ``BatchComplete`` event.

        Parameters:
            overwrite: If False (default), candidates that overlap
                existing shots are skipped.

        Returns:
            List of newly created :class:`ShotBlock` instances.
        """
        candidates = self.detect_regions()
        created: List[ShotBlock] = []
        with self.batch_update():
            for cand in candidates:
                if not overwrite and self._overlaps_existing(cand):
                    continue
                shot = self.define_shot(
                    name=cand["name"],
                    start=cand["start"],
                    end=cand["end"],
                    objects=cand.get("objects", []),
                )
                created.append(shot)
        return created

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
        result: Dict[int, str] = {}
        for shot in self.shots:
            if not shot.objects:
                result[shot.shot_id] = "valid"
                continue
            existing = cmds.ls(shot.objects, long=True) or []
            result[shot.shot_id] = (
                "valid" if len(existing) == len(shot.objects) else "missing_object"
            )
        return result


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
