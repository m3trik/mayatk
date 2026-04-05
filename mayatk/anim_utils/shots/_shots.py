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
    "ScenePersistence",
    "MayaScenePersistence",
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

    Registers ``SceneOpened`` / ``NewSceneOpened`` scriptJobs so that
    :attr:`ShotStore._active` is automatically invalidated when the
    user opens or creates a scene.  The jobs are *persistent* (not
    ``killWithScene``) so they survive across scene switches.
    """

    def __init__(
        self,
        node_name: str = NODE_NAME,
        attr_name: str = ATTR_NAME,
    ):
        self._node_name = node_name
        self._attr_name = attr_name
        self._scene_opened_job: Optional[int] = None
        self._new_scene_job: Optional[int] = None
        self._time_unit_job: Optional[int] = None
        self._before_save_cb_id = None  # OpenMaya callback id
        self._install_scene_jobs()

    def save(self, data: Dict[str, Any]) -> None:
        if pm is None:
            return
        import json
        import maya.cmds as cmds

        # Persistence writes must not pollute the undo queue.  They
        # fire via evalDeferred AFTER an UndoChunk closes and would
        # otherwise become the top undo entry, preventing the real
        # operation (e.g. keyframe move) from being undone.
        cmds.undoInfo(stateWithoutFlush=False)
        try:
            node = None
            if pm.objExists(self._node_name):
                node = pm.PyNode(self._node_name)
            else:
                node = pm.createNode("network", name=self._node_name)
                pm.addAttr(node, longName=self._attr_name, dataType="string")

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

    # ---- scene lifecycle scriptJobs --------------------------------------

    def _install_scene_jobs(self) -> None:
        """Register persistent scriptJobs for scene lifecycle events."""
        try:
            import maya.cmds as cmds
        except ImportError:
            return

        try:
            if self._scene_opened_job is None or not cmds.scriptJob(
                exists=self._scene_opened_job
            ):
                self._scene_opened_job = cmds.scriptJob(
                    event=["SceneOpened", self._on_scene_changed],
                )
        except Exception:
            pass

        try:
            if self._new_scene_job is None or not cmds.scriptJob(
                exists=self._new_scene_job
            ):
                self._new_scene_job = cmds.scriptJob(
                    event=["NewSceneOpened", self._on_scene_changed],
                )
        except Exception:
            pass

        try:
            if self._time_unit_job is None or not cmds.scriptJob(
                exists=self._time_unit_job
            ):
                self._time_unit_job = cmds.scriptJob(
                    event=["timeUnitChanged", self._on_time_unit_changed],
                )
        except Exception:
            pass

        try:
            import maya.api.OpenMaya as om

            if self._before_save_cb_id is None:
                self._before_save_cb_id = om.MSceneMessage.addCallback(
                    om.MSceneMessage.kBeforeSave, self._on_before_save
                )
        except Exception:
            pass

    def _on_scene_changed(self) -> None:
        """Invalidate the cached store when a different scene is loaded."""
        ShotStore._active = None

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
        csv_objs = set(self.metadata.get("csv_objects", []))
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


# ---------------------------------------------------------------------------
# ShotStore
# ---------------------------------------------------------------------------


class ShotStore:
    """Central store for shot data with pluggable persistence.

    Parameters:
        shots: Initial shot list.  Copied on construction.
        anim_layer: Optional animation layer name for future layer support.
    """

    _active: Optional["ShotStore"] = None
    _persistence: Optional[ScenePersistence] = None
    _QSETTINGS_PREFIX = "ShotStore"
    DETECTION_MODES = ("auto", "all", "skip_zero", "zero_as_end")

    def __init__(
        self,
        shots: Optional[List[ShotBlock]] = None,
        anim_layer: Optional[str] = None,
    ):
        self.shots: List[ShotBlock] = list(shots) if shots else []
        self.hidden_objects: set = set()
        self.pinned_objects: set = set()
        self.markers: List[Dict[str, Any]] = []
        self.gap: float = 0.0
        self.detection_threshold: float = 5.0
        self.detection_mode: str = "auto"  # "auto", "all", "skip_zero", "zero_as_end"
        self.select_on_load: bool = False
        self.locked_gaps: set = set()  # {(left_shot_id, right_shot_id), ...}
        self.anim_layer: Optional[str] = anim_layer
        self.scene_fps: float = _get_scene_fps()
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
        except Exception:
            pass

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
            start=float(start),
            end=float(end),
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
            shot.start = float(start)
        if end is not None:
            shot.end = float(end)
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
        for s in self.sorted_shots():
            if s.shot_id == exclude_id:
                continue
            if s.start >= after_frame - 1e-6:
                s.start += delta
                s.end += delta
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
            "select_on_load": self.select_on_load,
            "locked_gaps": [list(pair) for pair in sorted(self.locked_gaps)],
            "scene_fps": self.scene_fps,
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
        store.locked_gaps = {tuple(pair) for pair in data.get("locked_gaps", [])}
        stored_fps = data.get("scene_fps")
        if stored_fps is not None:
            store.scene_fps = float(stored_fps)
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


# ---------------------------------------------------------------------------
# Standard attribute filtering  (shared by sequencer + manifest)
# ---------------------------------------------------------------------------

STANDARD_TRANSFORM_ATTRS = frozenset(
    {
        "translateX",
        "translateY",
        "translateZ",
        "rotateX",
        "rotateY",
        "rotateZ",
        "scaleX",
        "scaleY",
        "scaleZ",
        "visibility",
    }
)
"""Attributes considered genuine scene content.

Objects animated only on attributes *outside* this set (e.g.
``audio_trigger``) are treated as boundary markers and excluded
from shot object lists.
"""


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


def _map_standard_curves_to_transforms(curves=None):
    """Map each transform to anim curves driving standard attrs.

    Returns ``dict[str, list[str]]`` — *transform_name* → [*curve_names*].
    Curves that only drive custom/user-defined attributes are skipped.
    Intermediate nodes (e.g. ``unitConversion``, ``pairBlend``) are
    resolved to their parent transform.
    """
    import maya.cmds as cmds
    from collections import defaultdict

    if curves is None:
        curves = cmds.ls(type="animCurve") or []

    result = defaultdict(list)
    for crv in curves:
        plugs = cmds.listConnections(crv, d=True, s=False, plugs=True) or []
        for plug_str in plugs:
            attr = plug_str.rsplit(".", 1)[-1] if "." in plug_str else ""
            if attr not in STANDARD_TRANSFORM_ATTRS:
                continue
            node = plug_str.split(".")[0]
            if cmds.nodeType(node) == "transform":
                long = cmds.ls(node, long=True)
                result[long[0] if long else node].append(crv)
            else:
                parents = (
                    cmds.listRelatives(
                        node, parent=True, type="transform", fullPath=True
                    )
                    or []
                )
                if parents:
                    result[parents[0]].append(crv)
            break  # one standard destination per curve is sufficient
    return dict(result)


# ---------------------------------------------------------------------------
# Shot-region detection  (shared by sequencer + manifest)
# ---------------------------------------------------------------------------


def detect_shot_regions(
    objects: Optional[List[str]] = None,
    gap_threshold: float = 5.0,
    ignore: Optional[str] = None,
    motion_rate: float = 1e-3,
    min_duration: float = 2.0,
) -> List[Dict[str, Any]]:
    """Detect animation regions by clustering per-object segments.

    Scans the full timeline using ``SegmentKeys`` and groups contiguous
    segments into regions separated by gaps of at least *gap_threshold*
    frames.  This is the single source of truth for shot-boundary
    detection — used by both the shot sequencer and the shot manifest.

    Flat/constant-value intervals are always excluded so that
    boundaries hidden by baked animation are correctly detected.

    Parameters:
        objects: Transform names to scan.  ``None`` discovers all
            transforms driven by animation curves.
        gap_threshold: Minimum gap (frames) between clusters.
        ignore: Attribute pattern(s) to exclude from segment collection.
        motion_rate: Per-frame rate-of-change threshold.  Intervals
            whose per-frame rate falls below this are treated as static.
        min_duration: Minimum shot duration in frames.  Clusters
            shorter than this are discarded.  Default ``2.0``.

    Returns:
        List of dicts with ``"name"``, ``"start"``, ``"end"``, and
        ``"objects"`` keys, sorted by start time.
    """
    try:
        import maya.cmds as cmds
    except ImportError:
        return []

    from mayatk.anim_utils.segment_keys import SegmentKeys

    # Discover objects if not provided
    if objects is None:
        curves = cmds.ls(type="animCurve") or []
        found: set = set()
        for crv in curves:
            conns = cmds.listConnections(crv, d=True, s=False) or []
            for node in conns:
                node_type = cmds.nodeType(node)
                if node_type == "transform":
                    long = cmds.ls(node, long=True)
                    found.add(long[0] if long else node)
                else:
                    parents = (
                        cmds.listRelatives(
                            node, parent=True, type="transform", fullPath=True
                        )
                        or []
                    )
                    if parents:
                        found.add(parents[0])
        objects = sorted(found)

    if not objects:
        return []

    # Validate existence — use long names to avoid ambiguity
    valid = cmds.ls(objects, long=True) or []
    if not valid:
        return []

    segments = SegmentKeys.collect_segments(
        valid,
        split_static=True,
        ignore=ignore,
        ignore_holds=True,
        ignore_visibility_holds=True,
        motion_only=True,
        motion_rate=motion_rate,
    )
    if not segments:
        return []

    segments.sort(key=lambda s: s["start"])

    # Cluster segments by gap_threshold
    clusters: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = [segments[0]]
    current_end = segments[0]["end"]

    for seg in segments[1:]:
        if seg["start"] - current_end > gap_threshold:
            clusters.append(current)
            current = [seg]
            current_end = seg["end"]
        else:
            current.append(seg)
            current_end = max(current_end, seg["end"])
    clusters.append(current)

    candidates: List[Dict[str, Any]] = []
    for cluster in clusters:
        start = min(s["start"] for s in cluster)
        end = max(s["end"] for s in cluster)
        if (end - start) < min_duration:
            continue
        objs = sorted({str(s["obj"]) for s in cluster})
        candidates.append(
            {
                "name": f"Shot {len(candidates) + 1}",
                "start": start,
                "end": end,
                "objects": objs,
            }
        )
    return candidates


def _filter_flat_objects(
    candidates: List[Dict[str, Any]], value_tolerance: float = 1e-4
) -> List[Dict[str, Any]]:
    """Remove objects whose animation is flat or only on custom trigger attributes.

    An object is considered genuine animated content if it has at least
    one animation curve that drives a standard transform or visibility
    attribute **and** that curve has changing values within the shot's
    range.  Objects animated only on custom attributes (e.g.
    ``audio_trigger``) are treated as boundary markers and excluded.

    Candidates with no remaining objects are kept (the shot boundary
    is still valid); only the ``"objects"`` list is pruned.
    """
    try:
        import maya.cmds as cmds
    except ImportError:
        return candidates

    if not candidates:
        return candidates

    try:
        transform_curves = _map_standard_curves_to_transforms()
    except (AttributeError, RuntimeError):
        return candidates
    if not transform_curves:
        return candidates

    for cand in candidates:
        start, end = cand["start"], cand["end"]
        filtered = []
        for obj in cand["objects"]:
            crvs = transform_curves.get(obj)
            if not crvs:
                continue
            for crv in crvs:
                vals = cmds.keyframe(crv, q=True, time=(start, end), valueChange=True)
                if vals and (max(vals) - min(vals)) > value_tolerance:
                    filtered.append(obj)
                    break
        cand["objects"] = filtered
    return candidates


def regions_from_selected_keys(
    gap_threshold: float = 5.0,
    key_filter: str = "all",
) -> List[Dict[str, Any]]:
    """Build shot regions from currently selected keyframes.

    Each unique selected key time is treated as an explicit shot
    boundary.  Keys closer than *gap_threshold* are merged into a
    single boundary.  This is designed for stepped / marker keys
    (e.g. audio triggers) where each key marks the start of a shot
    rather than representing continuous animation.

    Objects with flat/constant animation within a shot's range are
    automatically excluded from that shot's ``"objects"`` list.

    Parameters:
        gap_threshold: Keys within this many frames are merged
            into one boundary.
        key_filter: How to interpret key values:

            ``"all"``
                Every key is a boundary (contiguous shots).
            ``"skip_zero"``
                Keys with value 0 are ignored; only non-zero keys
                become boundaries.
            ``"zero_as_end"``
                Non-zero keys start shots; zero-value keys end the
                preceding shot (allows gaps between shots).

    Returns:
        List of dicts with ``"name"``, ``"start"``, ``"end"``, and
        ``"objects"`` keys, sorted by start time.
    """
    try:
        import maya.cmds as cmds
    except ImportError:
        return []

    sel_curves = cmds.keyframe(query=True, selected=True, name=True) or []
    if not sel_curves:
        return []

    # Collect (time, value, object) triples from selected keys
    entries: List[Tuple[float, float, str]] = []
    for crv in set(sel_curves):
        times = cmds.keyframe(crv, query=True, selected=True, timeChange=True) or []
        values = cmds.keyframe(crv, query=True, selected=True, valueChange=True) or []
        conns = cmds.listConnections(crv, d=True, s=False) or []
        obj_name = crv  # fallback
        for node in conns:
            node_type = cmds.nodeType(node)
            if node_type == "transform":
                long = cmds.ls(node, long=True)
                obj_name = long[0] if long else node
                break
            parents = (
                cmds.listRelatives(node, parent=True, type="transform", fullPath=True)
                or []
            )
            if parents:
                obj_name = parents[0]
                break
        for t, v in zip(times, values):
            if v is None:
                continue
            entries.append((t, v, obj_name))

    if not entries:
        return []

    def _is_zero(v) -> bool:
        """Treat None and near-zero floats as 'zero'."""
        return v is None or abs(v) < 1e-9

    # Stable sort: same-time entries have zeros first so that in
    # ``zero_as_end`` mode a closing zero is processed before the
    # opening non-zero trigger at the same frame.
    entries.sort(key=lambda e: (e[0], 0 if _is_zero(e[1]) else 1))

    # ---- "zero_as_end" mode: pair non-zero starts with zero ends ---------
    if key_filter == "zero_as_end":
        candidates: List[Dict[str, Any]] = []
        current_start: Optional[float] = None
        current_objs: set = set()
        for t, v, obj in entries:
            if not _is_zero(v):
                if current_start is None:
                    current_start = t
                    current_objs = {obj}
                else:
                    current_objs.add(obj)
            else:
                # Zero-value key ends the current shot
                if current_start is not None:
                    candidates.append(
                        {
                            "name": f"Shot {len(candidates) + 1}",
                            "start": current_start,
                            "end": t,
                            "objects": sorted(str(o) for o in current_objs),
                        }
                    )
                    current_start = None
                    current_objs = set()
        # Trailing shot with no closing zero key
        if current_start is not None:
            candidates.append(
                {
                    "name": f"Shot {len(candidates) + 1}",
                    "start": current_start,
                    "end": current_start + 1.0,
                    "objects": sorted(str(o) for o in current_objs),
                }
            )
        return _filter_flat_objects(candidates)

    # ---- "skip_zero" mode: filter zeros, then use boundary logic below -----
    if key_filter == "skip_zero":
        entries = [(t, v, obj) for t, v, obj in entries if not _is_zero(v)]
        if not entries:
            return []
        # Fall through to "all" mode boundary logic.

    # ---- "all" mode: merge keys within gap_threshold into boundary points
    boundaries: List[Tuple[float, set]] = []  # (time, {objects})
    first_time = entries[0][0]
    cur_time = entries[0][0]
    cur_objs: set = {entries[0][2]}

    for t, _v, obj in entries[1:]:
        if t - cur_time <= gap_threshold:
            cur_objs.add(obj)
            cur_time = t
        else:
            boundaries.append((first_time, cur_objs))
            first_time = t
            cur_time = t
            cur_objs = {obj}
    boundaries.append((first_time, cur_objs))

    if not boundaries:
        return []

    # Build contiguous regions: each boundary starts a shot that ends
    # at the next boundary.  The last shot gets a nominal 1-frame end
    # (the manifest's range resolver will compute the real end).
    candidates = []
    for i, (start, objs) in enumerate(boundaries):
        if i + 1 < len(boundaries):
            end = boundaries[i + 1][0]
        else:
            end = start + 1.0
        candidates.append(
            {
                "name": f"Shot {len(candidates) + 1}",
                "start": start,
                "end": end,
                "objects": sorted(str(o) for o in objs),
            }
        )
    return _filter_flat_objects(candidates)
