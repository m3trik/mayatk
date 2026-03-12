# coding=utf-8
"""Sequencer — manages per-scene animation with ripple editing.

Scenes are contiguous keyframe ranges ("blocks") along the timeline.
Changing one scene's duration or position ripples downstream scenes.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
import json

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)

from mayatk.core_utils._core_utils import CoreUtils


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------


@dataclass
class SceneBlock:
    """Represents a single scene (contiguous animation range).

    Attributes:
        scene_id: Unique identifier for the scene.
        name: Human-readable label (e.g. "Intro", "Scene_1").
        start: First frame of the scene.
        end: Last frame of the scene.
        objects: Transform node names that belong to this scene.
    """

    scene_id: int
    name: str
    start: float
    end: float
    objects: List[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


# ---------------------------------------------------------------------------
# Sequencer
# ---------------------------------------------------------------------------


class Sequencer:
    """Manages a linear sequence of :class:`SceneBlock` objects and provides
    operations for ripple editing, scene detection, and template application.
    """

    def __init__(self, scenes: Optional[List[SceneBlock]] = None):
        self.scenes: List[SceneBlock] = list(scenes) if scenes else []
        self.hidden_objects: set = set()
        self.markers: List[Dict[str, Any]] = []

    def is_object_hidden(self, obj_name: str) -> bool:
        """Return True if *obj_name* is hidden in the sequencer UI."""
        return obj_name in self.hidden_objects

    def set_object_hidden(self, obj_name: str, hidden: bool = True) -> None:
        """Show or hide *obj_name* in the sequencer UI."""
        if hidden:
            self.hidden_objects.add(obj_name)
        else:
            self.hidden_objects.discard(obj_name)

    # ---- query -----------------------------------------------------------

    def sorted_scenes(self) -> List[SceneBlock]:
        """Return scenes ordered by start time."""
        return sorted(self.scenes, key=lambda s: s.start)

    def scene_by_id(self, scene_id: int) -> Optional[SceneBlock]:
        for s in self.scenes:
            if s.scene_id == scene_id:
                return s
        return None

    def scene_by_name(self, name: str) -> Optional[SceneBlock]:
        """Return the first scene whose name matches *name*, or ``None``."""
        for s in self.scenes:
            if s.name == name:
                return s
        return None

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _find_keyed_transforms(
        start: float, end: float, value_tolerance: float = 1e-4
    ) -> List[str]:
        """Return names of all transforms with non-flat animation in [start, end].

        Objects whose curves are entirely constant (all values within
        *value_tolerance*) across the range are excluded.
        """
        curves = pm.ls(type="animCurve")
        if not curves:
            return []

        # Map each transform to its curves that have keys in this range
        from collections import defaultdict

        transform_curves: dict = defaultdict(list)
        for crv in curves:
            keys = pm.keyframe(crv, q=True, time=(start, end))
            if not keys:
                continue
            conns = pm.listConnections(crv, d=True, s=False) or []
            for node in conns:
                if node.type() == "transform":
                    transform_curves[str(node)].append(crv)
                else:
                    parents = pm.listRelatives(node, parent=True, type="transform")
                    if parents:
                        transform_curves[str(parents[0])].append(crv)

        # Keep only transforms where at least one curve changes value
        result = []
        for xform, crvs in sorted(transform_curves.items()):
            for crv in crvs:
                vals = pm.keyframe(crv, q=True, time=(start, end), valueChange=True)
                if vals and (max(vals) - min(vals)) > value_tolerance:
                    result.append(xform)
                    break
        return result

    # ---- manual definition -----------------------------------------------

    def define_scene(
        self,
        name: str,
        start: float,
        end: float,
        objects: Optional[List[str]] = None,
    ) -> SceneBlock:
        """Define a scene manually from a name and range.

        Parameters:
            name: Human-readable label.
            start: First frame.
            end: Last frame.
            objects: Transform node names.  If ``None``, automatically
                discovers all transforms with keyframes in [start, end].

        Returns:
            The newly created :class:`SceneBlock`.
        """
        if objects is None:
            objects = self._find_keyed_transforms(start, end)
        new_id = max((s.scene_id for s in self.scenes), default=-1) + 1
        block = SceneBlock(
            scene_id=new_id,
            name=name,
            start=float(start),
            end=float(end),
            objects=sorted(set(objects)),
        )
        self.scenes.append(block)
        return block

    @classmethod
    def from_current_range(
        cls,
        name: str = "Scene",
        objects: Optional[List[str]] = None,
    ) -> "Sequencer":
        """Create a Sequencer with one scene spanning Maya's current
        playback range.

        Parameters:
            name: Label for the scene.
            objects: Transform node names.  If ``None``, automatically
                discovers all transforms with keyframes in the range.

        Returns:
            A new :class:`Sequencer` with a single scene.
        """
        start = pm.playbackOptions(q=True, min=True)
        end = pm.playbackOptions(q=True, max=True)
        if objects is None:
            objects = cls._find_keyed_transforms(start, end)
        block = SceneBlock(
            scene_id=0,
            name=name,
            start=float(start),
            end=float(end),
            objects=sorted(set(objects)),
        )
        return cls([block])

    @staticmethod
    def _scene_nodes(scene: SceneBlock) -> list:
        """Return live PyNode references for a scene's objects."""
        return [pm.PyNode(o) for o in scene.objects if pm.objExists(o)]

    def collect_object_segments(
        self,
        scene_id: int,
        ignore: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Collect per-object animation segments within a scene's range.

        Each returned dict has ``"obj"`` (str), ``"start"``, ``"end"``,
        and ``"duration"`` keys — suitable for populating per-object
        tracks in the sequencer widget.

        Parameters:
            scene_id: The scene whose objects and range to query.
            ignore: Attribute pattern(s) to exclude.

        Returns:
            A list of segment dicts grouped by object.
        """
        scene = self.scene_by_id(scene_id)
        if scene is None:
            return []

        nodes = self._scene_nodes(scene)
        if not nodes:
            return []

        from mayatk.anim_utils.segment_keys import SegmentKeys

        segments = SegmentKeys.collect_segments(
            nodes,
            split_static=True,
            ignore=ignore,
            time_range=(scene.start, scene.end),
            ignore_holds=True,
            ignore_visibility_holds=True,
        )
        # Normalise obj to str
        for seg in segments:
            seg["obj"] = str(seg["obj"])
        return segments

    # ---- detection -------------------------------------------------------

    @classmethod
    def detect_scenes(
        cls,
        objects: List["pm.PyNode"],
        gap_threshold: float = 10.0,
        ignore: Optional[str] = None,
    ) -> "Sequencer":
        """Build a Sequencer by detecting animation segments.

        Uses :class:`~mayatk.anim_utils.segment_keys.SegmentKeys` to
        collect per-object segments, then merges overlapping ranges into
        scene blocks.

        Parameters:
            objects: Transform nodes to analyse.
            gap_threshold: Frames of silence between segments before a new
                scene is created.
            ignore: Attribute name(s) to exclude (forwarded to
                ``collect_segments``).

        Returns:
            A populated :class:`Sequencer`.
        """
        from mayatk.anim_utils.segment_keys import SegmentKeys

        segments = SegmentKeys.collect_segments(
            objects, split_static=True, ignore=ignore
        )
        if not segments:
            return cls()

        # Collect all (start, end) ranges
        ranges = [(seg["start"], seg["end"]) for seg in segments]
        ranges.sort()

        # Merge overlapping / close ranges into scene blocks
        merged: List[List[float]] = [list(ranges[0])]
        for start, end in ranges[1:]:
            if start <= merged[-1][1] + gap_threshold:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])

        # Build SceneBlocks and assign objects
        scenes: List[SceneBlock] = []
        for idx, (m_start, m_end) in enumerate(merged):
            # Gather objects whose segments fall within this merged range
            objs = set()
            for seg in segments:
                if seg["start"] >= m_start and seg["end"] <= m_end:
                    objs.add(str(seg["obj"]))
            scenes.append(
                SceneBlock(
                    scene_id=idx,
                    name=f"Scene_{idx}",
                    start=m_start,
                    end=m_end,
                    objects=sorted(objs),
                )
            )

        return cls(scenes)

    # ---- per-object keyframe editing -------------------------------------

    def move_object_keys(
        self,
        obj: str,
        old_start: float,
        old_end: float,
        new_start: float,
    ) -> None:
        """Offset all keyframes of *obj* that fall within [old_start, old_end]
        so the segment begins at *new_start*.

        Parameters:
            obj: Transform node name.
            old_start: Original first frame of the segment.
            old_end: Original last frame of the segment.
            new_start: Desired first frame after the move.
        """
        if not pm.objExists(obj):
            return
        delta = new_start - old_start
        if abs(delta) < 1e-6:
            return

        # Operate on individual anim curves so we only affect keys in the
        # requested time range without colliding with keys elsewhere.
        node = pm.PyNode(obj)
        curves = pm.listConnections(node, type="animCurve", s=True, d=False) or []
        if not curves:
            return

        _TEMP_OFFSET = 100000.0
        eps = 1e-3
        tr = (old_start - eps, old_end + eps)
        temp_tr = (old_start + _TEMP_OFFSET - eps, old_end + _TEMP_OFFSET + eps)

        for crv in curves:
            # Skip curves that have no keys in this range
            if not pm.keyframe(crv, q=True, time=tr):
                continue
            try:
                pm.keyframe(
                    crv,
                    edit=True,
                    relative=True,
                    timeChange=_TEMP_OFFSET,
                    time=tr,
                )
            except RuntimeError:
                continue
            try:
                pm.keyframe(
                    crv,
                    edit=True,
                    relative=True,
                    timeChange=delta - _TEMP_OFFSET,
                    time=temp_tr,
                )
            except RuntimeError:
                try:
                    pm.keyframe(
                        crv,
                        edit=True,
                        relative=True,
                        timeChange=-_TEMP_OFFSET,
                        time=temp_tr,
                    )
                except RuntimeError:
                    pass

    @CoreUtils.undoable
    def move_object_in_scene(
        self,
        scene_id: int,
        obj: str,
        old_start: float,
        old_end: float,
        new_start: float,
        prevent_overlap: bool = False,
    ) -> None:
        """Move one object's keys within a scene, expanding the scene and
        rippling downstream scenes when the clip exceeds scene boundaries.

        Parameters:
            scene_id: Scene the object belongs to.
            obj: Transform node name to move.
            old_start: Original first frame of the object segment.
            old_end: Original last frame of the object segment.
            new_start: Desired first frame after the move.
            prevent_overlap: If True, push other objects in the same scene
                that would overlap with the moved object's new range.
        """
        scene = self.scene_by_id(scene_id)
        if scene is None:
            raise ValueError(f"No scene with id {scene_id}")

        dur = old_end - old_start
        new_end = new_start + dur

        # Move the object's keys
        self.move_object_keys(obj, old_start, old_end, new_start)

        # Optionally push overlapping objects within the same scene
        if prevent_overlap:
            self._push_overlapping_objects(scene, obj, new_start, new_end)

        # Check if the clip now exceeds the scene boundaries
        prior_end = scene.end
        expanded = False

        if new_start < scene.start:
            scene.start = new_start
            expanded = True

        if new_end > scene.end:
            scene.end = new_end
            expanded = True

        # Ripple downstream scenes by however much the scene tail grew
        if expanded:
            delta = scene.end - prior_end
            if abs(delta) > 1e-6:
                self._ripple_downstream(scene_id, prior_end, delta)

    def _push_overlapping_objects(
        self,
        scene: SceneBlock,
        moved_obj: str,
        moved_start: float,
        moved_end: float,
    ) -> None:
        """Push other objects in *scene* to resolve overlaps with the moved object.

        Objects whose animation range overlaps with [moved_start, moved_end]
        are shifted forward so they start at moved_end.  This cascades: if
        pushing one object causes a new overlap with the next, that object
        is pushed too.
        """
        segments = self.collect_object_segments(scene.scene_id)
        # Build per-object ranges (excluding the moved object)
        obj_ranges = {}
        for seg in segments:
            name = seg["obj"]
            if name == moved_obj:
                continue
            if name in obj_ranges:
                obj_ranges[name] = (
                    min(obj_ranges[name][0], seg["start"]),
                    max(obj_ranges[name][1], seg["end"]),
                )
            else:
                obj_ranges[name] = (seg["start"], seg["end"])

        # Sort by start time and cascade pushes
        sorted_objs = sorted(obj_ranges.items(), key=lambda x: x[1][0])
        push_end = moved_end
        for name, (s, e) in sorted_objs:
            if s < push_end and e > moved_start:
                delta = push_end - s
                self.move_object_keys(name, s, e, s + delta)
                push_end = e + delta
            else:
                push_end = max(push_end, e)

    def scale_object_keys(
        self,
        obj: str,
        old_start: float,
        old_end: float,
        new_start: float,
        new_end: float,
    ) -> None:
        """Scale (and optionally shift) keyframes of *obj* from
        [old_start, old_end] into [new_start, new_end].

        Parameters:
            obj: Transform node name.
            old_start: Original first frame.
            old_end: Original last frame.
            new_start: Desired first frame.
            new_end: Desired last frame.
        """
        if not pm.objExists(obj):
            return
        if abs(old_end - old_start) < 1e-6:
            return
        pm.scaleKey(
            obj,
            time=(old_start, old_end),
            newStartTime=new_start,
            newEndTime=new_end,
        )

    # ---- ripple editing --------------------------------------------------

    def _ripple_downstream(self, scene_id: int, after_frame: float, delta: float):
        """Shift all scenes starting at or after *after_frame* by *delta*."""
        for s in self.sorted_scenes():
            if s.scene_id == scene_id:
                continue
            if s.start >= after_frame:
                for obj in s.objects:
                    self.move_object_keys(obj, s.start, s.end, s.start + delta)
                s.start += delta
                s.end += delta

    @CoreUtils.undoable
    def resize_object(
        self,
        scene_id: int,
        obj: str,
        old_start: float,
        old_end: float,
        new_start: float,
        new_end: float,
    ) -> None:
        """Scale one object's keys and ripple-shift all downstream scenes.

        Only the named *obj* is scaled.  Other objects in the same scene
        are untouched.  Downstream scenes are shifted by the end-frame
        delta so the gap is preserved.

        Parameters:
            scene_id: Scene the object belongs to.
            obj: Transform node name to resize.
            old_start: Original first frame of the object segment.
            old_end: Original last frame of the object segment.
            new_start: Desired first frame after the resize.
            new_end: Desired last frame after the resize.
        """
        scene = self.scene_by_id(scene_id)
        if scene is None:
            raise ValueError(f"No scene with id {scene_id}")

        # Scale only this object's keys
        self.scale_object_keys(obj, old_start, old_end, new_start, new_end)

        # The scene envelope may need updating
        prior_end = scene.end
        scene.start = min(scene.start, new_start)
        scene.end = max(scene.end, new_end)

        # Ripple downstream scenes by the change at the tail
        delta = scene.end - prior_end
        if abs(delta) > 1e-6:
            self._ripple_downstream(scene_id, prior_end, delta)

    @CoreUtils.undoable
    def set_scene_duration(self, scene_id: int, new_duration: float) -> None:
        """Change a scene's duration and ripple-shift all downstream scenes.

        The scene's *start* stays fixed; its *end* moves, and every
        downstream scene shifts by the same delta.

        Parameters:
            scene_id: ID of the scene to resize.
            new_duration: Desired duration in frames.
        """
        scene = self.scene_by_id(scene_id)
        if scene is None:
            raise ValueError(f"No scene with id {scene_id}")

        delta = new_duration - scene.duration
        if abs(delta) < 1e-6:
            return

        old_end = scene.end
        new_end = scene.start + new_duration

        # Scale keyframes within this scene
        for obj in scene.objects:
            self.scale_object_keys(obj, scene.start, old_end, scene.start, new_end)
        scene.end = new_end

        # Shift downstream scenes
        self._ripple_downstream(scene_id, old_end, delta)

    @CoreUtils.undoable
    def set_scene_start(
        self, scene_id: int, new_start: float, ripple: bool = True
    ) -> None:
        """Move a scene to a new start time.

        Parameters:
            scene_id: ID of the scene to move.
            new_start: New start frame.
            ripple: If True, downstream scenes shift by the same delta.
        """
        scene = self.scene_by_id(scene_id)
        if scene is None:
            raise ValueError(f"No scene with id {scene_id}")

        delta = new_start - scene.start
        if abs(delta) < 1e-6:
            return

        old_end = scene.end

        # Move this scene's keys
        for obj in scene.objects:
            self.move_object_keys(obj, scene.start, scene.end, new_start)
        scene.start += delta
        scene.end += delta

        if ripple:
            self._ripple_downstream(scene_id, old_end, delta)

    # ---- serialisation ---------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialise scenes and settings to a plain dict."""
        return {
            "scenes": [
                {
                    "scene_id": s.scene_id,
                    "name": s.name,
                    "start": s.start,
                    "end": s.end,
                    "objects": list(s.objects),
                }
                for s in self.sorted_scenes()
            ],
            "hidden_objects": sorted(self.hidden_objects),
            "markers": list(self.markers),
        }

    @classmethod
    def from_dict(cls, data) -> "Sequencer":
        """Restore from serialised data.

        Accepts the current dict format ``{"scenes": [...], ...}`` or
        the legacy list-of-dicts format for backwards compatibility.
        """
        if isinstance(data, list):
            scene_list = data
            hidden = []
        else:
            scene_list = data.get("scenes", [])
            hidden = data.get("hidden_objects", [])

        scenes = [
            SceneBlock(
                scene_id=d["scene_id"],
                name=d["name"],
                start=d["start"],
                end=d["end"],
                objects=d.get("objects", []),
            )
            for d in scene_list
        ]
        seq = cls(scenes)
        seq.hidden_objects = set(hidden)
        seq.markers = data.get("markers", []) if isinstance(data, dict) else []
        return seq

    # ---- Maya scene persistence (network node) ---------------------------

    STORAGE_NODE = "SCENE_SEQ_DATA"
    _DATA_ATTR = "scene_seq_json"
    _MSG_PREFIX = "scene_obj_"

    def save(self) -> str:
        """Persist the current scene list to a Maya network node.

        Creates (or reuses) a locked ``network`` node named
        :pyattr:`STORAGE_NODE` with:
        - A ``string`` attribute holding the JSON-serialised scene list.
        - ``message`` attributes connected to each scene object for
          rename/namespace-safe references.

        Returns:
            The name of the storage node.
        """
        node_name = self.STORAGE_NODE

        # Find or create the storage node
        if pm.objExists(node_name):
            node = pm.PyNode(node_name)
            pm.lockNode(node, lock=False)
        else:
            node = pm.createNode("network", name=node_name)

        # Ensure the JSON data attr exists
        if not node.hasAttr(self._DATA_ATTR):
            node.addAttr(self._DATA_ATTR, dt="string")

        # Write JSON
        node.attr(self._DATA_ATTR).set(json.dumps(self.to_dict()))

        # --- message connections ---
        # Remove stale message attrs
        for attr in node.listAttr(ud=True):
            if attr.attrName().startswith(self._MSG_PREFIX):
                pm.deleteAttr(attr)

        # Create fresh message attrs for every object in every scene
        _seen = set()
        for scene in self.scenes:
            for obj_name in scene.objects:
                if obj_name in _seen:
                    continue
                _seen.add(obj_name)
                if not pm.objExists(obj_name):
                    continue
                safe_name = obj_name.replace("|", "_").replace(":", "_")
                attr_name = f"{self._MSG_PREFIX}{safe_name}"
                if not node.hasAttr(attr_name):
                    node.addAttr(attr_name, at="message")
                pm.connectAttr(
                    pm.PyNode(obj_name).message, node.attr(attr_name), force=True
                )

        pm.lockNode(node, lock=True)
        return str(node)

    @classmethod
    def load(cls) -> Optional["Sequencer"]:
        """Load a Sequencer from the Maya scene's storage node.

        Reads the JSON data from :pyattr:`STORAGE_NODE` and refreshes
        object names from live ``message`` connections so that renames
        and namespace changes are automatically resolved.

        Returns:
            A populated :class:`Sequencer`, or ``None`` if no
            storage node exists.
        """
        if not pm.objExists(cls.STORAGE_NODE):
            return None

        node = pm.PyNode(cls.STORAGE_NODE)
        if not node.hasAttr(cls._DATA_ATTR):
            return None

        raw = node.attr(cls._DATA_ATTR).get()
        if not raw:
            return None

        data = json.loads(raw)
        seq = cls.from_dict(data)

        # Refresh object names from message connections
        live_names: Dict[str, str] = {}
        for attr in node.listAttr(ud=True):
            if not attr.attrName().startswith(cls._MSG_PREFIX):
                continue
            conns = attr.connections()
            if conns:
                stored_suffix = attr.attrName()[len(cls._MSG_PREFIX) :]
                live_names[stored_suffix] = str(conns[0])

        for scene in seq.scenes:
            updated = []
            for obj_name in scene.objects:
                safe = obj_name.replace("|", "_").replace(":", "_")
                updated.append(live_names.get(safe, obj_name))
            scene.objects = updated

        return seq

    @classmethod
    def delete_storage_node(cls) -> bool:
        """Remove the storage node from the Maya scene.

        Returns:
            True if a node was deleted, False if none existed.
        """
        if not pm.objExists(cls.STORAGE_NODE):
            return False
        node = pm.PyNode(cls.STORAGE_NODE)
        pm.lockNode(node, lock=False)
        pm.delete(node)
        return True
