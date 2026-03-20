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
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable
from contextlib import contextmanager

try:
    import pymel.core as pm
except ImportError:
    pm = None

NODE_NAME = "shotStore"
ATTR_NAME = "shotData"


# ---------------------------------------------------------------------------
# Persistence protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ScenePersistence(Protocol):
    """Interface for saving / loading ShotStore data."""

    def save(self, data: Dict[str, Any]) -> None: ...

    def load(self) -> Optional[Dict[str, Any]]: ...


class MayaScenePersistence:
    """Persist ShotStore data to a Maya network-node attribute."""

    def __init__(
        self,
        node_name: str = NODE_NAME,
        attr_name: str = ATTR_NAME,
    ):
        self._node_name = node_name
        self._attr_name = attr_name

    def save(self, data: Dict[str, Any]) -> None:
        if pm is None:
            return
        import json

        node = None
        if pm.objExists(self._node_name):
            node = pm.PyNode(self._node_name)
        else:
            node = pm.createNode("network", name=self._node_name)
            pm.addAttr(node, longName=self._attr_name, dataType="string")

        node.attr(self._attr_name).set(json.dumps(data))

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

    def __init__(
        self,
        shots: Optional[List[ShotBlock]] = None,
        anim_layer: Optional[str] = None,
    ):
        self.shots: List[ShotBlock] = list(shots) if shots else []
        self.hidden_objects: set = set()
        self.markers: List[Dict[str, Any]] = []
        self.gap: float = 0.0
        self.anim_layer: Optional[str] = anim_layer
        self._listeners: List[Callable[[str, Optional[Any]], None]] = []
        self._batch_depth: int = 0
        self._batch_events: List[tuple] = []

    # ---- observer --------------------------------------------------------

    def add_listener(self, callback: Callable[[str, Optional[Any]], None]) -> None:
        """Register a listener called on store mutations.

        The callback receives ``(event_name, payload)`` where
        *event_name* is one of ``"shot_defined"``, ``"shot_removed"``,
        ``"shot_updated"``.
        """
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[str, Optional[Any]], None]) -> None:
        """Remove a previously registered listener."""
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def _notify(self, event: str, payload: Optional[Any] = None) -> None:
        """Fire all registered listeners (deferred during :meth:`batch_update`)."""
        if self._batch_depth > 0:
            self._batch_events.append((event, payload))
            return
        for cb in self._listeners:
            try:
                cb(event, payload)
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
                for cb in self._listeners:
                    try:
                        cb("batch_complete", None)
                    except Exception:
                        pass

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
                cls._active = cls.from_dict(data) if data else cls()
            else:
                cls._active = cls()
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
        self._notify("shot_defined", block)
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
            shot.objects = sorted(set(objects))
        if description is not None:
            shot.description = description
        if locked is not None:
            shot.locked = locked
        if metadata is not None:
            shot.metadata = dict(metadata)
        self._notify("shot_updated", shot)
        return shot

    def remove_shot(self, shot_id: int) -> bool:
        """Remove a shot by ID.  Returns ``True`` if found."""
        for i, s in enumerate(self.shots):
            if s.shot_id == shot_id:
                self.shots.pop(i)
                self._notify("shot_removed", shot_id)
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
            "markers": list(self.markers),
            "gap": self.gap,
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
        store.markers = data.get("markers", [])
        store.gap = float(data.get("gap", 0.0))
        return store

    # ---- persistence convenience -----------------------------------------

    def save(self) -> None:
        """Persist via the configured backend (no-op if none set)."""
        if self._persistence is not None:
            self._persistence.save(self.to_dict())


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------


def _is_motion_interval(
    v1: float,
    v2: float,
    t1: float,
    t2: float,
    rate_threshold: float = 1e-3,
) -> bool:
    """Decide whether a key-pair represents actual motion.

    Uses a rate-based check: ``abs(v2 - v1) / max(t2 - t1, 1) > rate_threshold``.
    Normalising by interval duration prevents sparse/baked keys that drift
    slowly from being classified as motion.

    Parameters:
        v1, v2: Value at start and end of the interval.
        t1, t2: Time at start and end of the interval.
        rate_threshold: Minimum value-change per frame to qualify as motion.

    Returns:
        ``True`` if the interval contains meaningful motion.
    """
    dt = max(t2 - t1, 1.0)
    return abs(v2 - v1) / dt > rate_threshold


def _motion_frames_for_curve(
    times: list,
    values: list,
    value_tolerance: float = 1e-5,
) -> list:
    """Return frames where a curve's value actually changes.

    For each consecutive key pair where the rate of change exceeds
    *value_tolerance* (normalised per frame via :func:`_is_motion_interval`),
    both the source and destination frame are included.  This identifies
    where real motion occurs, ignoring the flat/baked regions entirely.

    Parameters:
        times: Sorted key times.
        values: Corresponding key values.
        value_tolerance: Per-frame rate threshold to consider values changing.

    Returns:
        List of frames where the curve has actual motion.
    """
    import numpy as np

    if len(times) < 2 or len(values) != len(times):
        return list(times)

    t_arr = np.array(times)
    v_arr = np.array(values)
    dt_arr = np.maximum(np.diff(t_arr), 1.0)
    rates = np.abs(np.diff(v_arr)) / dt_arr
    motion_idx = np.where(rates > value_tolerance)[0]

    if len(motion_idx) == 0:
        return []

    # Both "from" and "to" keys of each transition are motion frames
    result = set()
    for idx in motion_idx:
        result.add(float(t_arr[idx]))
        result.add(float(t_arr[idx + 1]))
    return sorted(result)


def _collect_all_motion_frames(value_tolerance: float = 1e-5) -> List[float]:
    """Return sorted frames where any animation curve has actual motion.

    Iterates every ``animCurve`` node via ``maya.cmds`` and delegates to
    :func:`_motion_frames_for_curve` for each.  Frames where the value
    difference between consecutive keys is within *value_tolerance* are
    excluded, revealing motion hidden by baked/constant-value keys.

    This is the shared primitive used by both :func:`detect_animation_gaps`
    and :meth:`ShotSequencer.detect_shots` when flat-key filtering is
    enabled.

    Parameters:
        value_tolerance: Max difference to consider values equal.

    Returns:
        Sorted list of frames with actual value change, or ``[]``.
    """
    try:
        import maya.cmds as cmds
    except ImportError:
        return []

    curves = cmds.ls(type="animCurve")
    if not curves:
        return []

    motion: set = set()
    for crv in curves:
        times = cmds.keyframe(crv, q=True, tc=True) or []
        values = cmds.keyframe(crv, q=True, vc=True) or []
        motion.update(_motion_frames_for_curve(times, values, value_tolerance))
    return sorted(motion)


def detect_animation_gaps(
    min_gap: float = 2.0,
    ignore_flat_keys: bool = False,
    value_tolerance: float = 1e-5,
) -> List[float]:
    """Scan all animation curves and return animation-region start frames.

    An animation region is a contiguous block of keyed (or motion-bearing)
    frames.  Regions are separated by gaps of at least *min_gap* frames.

    The first entry is always the earliest key / motion frame; each
    subsequent entry is the first frame where animation resumes after a
    qualifying gap.  Returns an empty list when no qualifying gaps exist
    or no animation is present.

    Uses ``maya.cmds`` for performance (avoids PyMEL node wrapping).

    Parameters:
        min_gap: Minimum span of empty frames to qualify as a gap.
        ignore_flat_keys: When ``True``, only frames where at least one
            curve has an actual value change are considered.  This
            reveals gaps hidden by baked/constant-value animation.
        value_tolerance: Value tolerance for motion detection
            (only used when *ignore_flat_keys* is ``True``).

    Returns:
        Sorted list of animation-region start frames, or ``[]`` when
        no qualifying gaps are found.
    """
    if ignore_flat_keys:
        sorted_keys = _collect_all_motion_frames(value_tolerance)
    else:
        try:
            import maya.cmds as cmds
        except ImportError:
            return []

        curves = cmds.ls(type="animCurve")
        if not curves:
            return []

        all_keys: set = set()
        for crv in curves:
            keys = cmds.keyframe(crv, q=True)
            if keys:
                all_keys.update(keys)
        sorted_keys = sorted(all_keys)

    if len(sorted_keys) < 2:
        return []

    region_starts: List[float] = [sorted_keys[0]]
    for i in range(len(sorted_keys) - 1):
        span = sorted_keys[i + 1] - sorted_keys[i]
        if span >= min_gap:
            region_starts.append(sorted_keys[i + 1])

    # Only one entry means no qualifying gaps were found
    if len(region_starts) <= 1:
        return []
    return region_starts
