# coding=utf-8
"""Shot Manifest — parse structured CSVs and populate a ShotStore.

Reads a CSV with section/step structure, auto-detects object behaviors
from textual descriptions, and registers shots in a
:class:`~mayatk.anim_utils.shots._shots.ShotStore`.
"""
import csv
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from mayatk.anim_utils.shots._shots import ShotStore

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class BuilderObject:
    """One asset within a step."""

    name: str
    behavior: str = ""  # e.g. "fade_in_out", "fade_in", "fade_out"


@dataclass
class BuilderStep:
    """One step (= one future sequencer shot)."""

    step_id: str  # e.g. "A04"
    section: str  # e.g. "A"
    section_title: str  # e.g. "AILERON RIGGING"
    content: str  # merged step-contents text
    objects: List[BuilderObject] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Assessment data structures
# ---------------------------------------------------------------------------


@dataclass
class ObjectStatus:
    """Assessment result for one object within a step."""

    name: str
    exists: bool
    status: str  # "valid" | "missing_object" | "missing_behavior" | "user_animated"
    behavior: str = ""  # expected behavior name (empty = user-animated)
    key_range: Optional[Tuple[float, float]] = None  # actual keyframe extent


@dataclass
class StepStatus:
    """Assessment result for one step."""

    step_id: str
    built: bool  # shot exists in sequencer
    objects: List[ObjectStatus] = field(default_factory=list)
    additional_objects: List[str] = field(default_factory=list)  # in shot but not CSV
    shrinkable_frames: float = 0.0  # frames of unused range at step tail
    locked: bool = False  # shot is user-finalized; skip automated flags

    @property
    def status(self) -> str:
        """Worst-of-children rollup.

        Priority: ``"locked"`` (user-finalized) > ``"missing_shot"``
        > ``"missing_object"`` > ``"missing_behavior"`` > ``"valid"``.
        """
        if self.locked:
            return "locked"
        if not self.built:
            return "missing_shot"
        if any(o.status == "missing_object" for o in self.objects):
            return "missing_object"
        if any(o.status == "missing_behavior" for o in self.objects):
            return "missing_behavior"
        return "valid"

    @property
    def missing_count(self) -> int:
        return sum(1 for o in self.objects if o.status == "missing_object")

    @property
    def total_count(self) -> int:
        return len(self.objects)


# ---------------------------------------------------------------------------
# Behavior detection
# ---------------------------------------------------------------------------

_BEHAVIOR_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\bfades?\s+in\b.*\bfades?\s+out\b", re.I), "fade_in_out"),
    (re.compile(r"\bfades?\s+out\b.*\bfades?\s+in\b", re.I), "fade_in_out"),
    (re.compile(r"\bfades?\s+in\b", re.I), "fade_in"),
    (re.compile(r"\bfades?\s+out\b", re.I), "fade_out"),
]


def detect_behavior(text: str) -> str:
    """Return behavior name inferred from descriptive *text*, or ``""``."""
    for pattern, name in _BEHAVIOR_PATTERNS:
        if pattern.search(text):
            return name
    return ""


# ---------------------------------------------------------------------------
# CSV column mapping
# ---------------------------------------------------------------------------


@dataclass
class ColumnMap:
    """Maps logical fields to CSV header names (case-insensitive).

    Each field is a tuple of acceptable header aliases. The parser reads
    the header row and resolves names to column indices automatically.
    """

    step_id: Tuple[str, ...] = ("Step",)
    content: Tuple[str, ...] = ("Step Contents", "Contents")
    asset_name: Tuple[str, ...] = ("Asset Names", "Asset")


@dataclass
class _ResolvedColumns:
    """Integer column indices resolved from a header row."""

    step_id: int
    content: int
    asset_name: int


def _resolve_columns(header: List[str], col_map: ColumnMap) -> _ResolvedColumns:
    """Match header cell text to column indices via *col_map* aliases.

    Raises:
        ValueError: If a required column cannot be found.
    """
    normalized = [c.strip().lower() for c in header]

    def _find(aliases: Tuple[str, ...], field_name: str) -> int:
        for alias in aliases:
            try:
                return normalized.index(alias.lower())
            except ValueError:
                continue
        raise ValueError(
            f"Column '{field_name}' not found in header row. "
            f"Expected one of {aliases!r}, got {header}"
        )

    return _ResolvedColumns(
        step_id=_find(col_map.step_id, "step_id"),
        content=_find(col_map.content, "content"),
        asset_name=_find(col_map.asset_name, "asset_name"),
    )


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(r"^SECTION\s+([A-Z0-9]+)\s*:\s*(.*)", re.I)
_STEP_RE = re.compile(r"^([A-Z]\d+)\.\)")


def _strip_cell(cell: str) -> str:
    """Strip whitespace from a CSV cell."""
    return (cell or "").strip()


def parse_csv(
    filepath: str,
    columns: Optional[ColumnMap] = None,
) -> List[BuilderStep]:
    """Parse a structured CSV into a list of :class:`BuilderStep`.

    Parameters:
        filepath: Path to the CSV file.
        columns: Optional header-name mapping.  Defaults cover
            common layouts (C-5M, C-130H, C-17A).

    Returns:
        Ordered list of steps, each carrying its objects and detected
        behaviors.
    """
    col_map = columns or ColumnMap()
    cols: Optional[_ResolvedColumns] = None
    steps: List[BuilderStep] = []
    seen_ids: set = set()
    current_section = ""
    current_section_title = ""
    current_step: Optional[BuilderStep] = None

    with open(filepath, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        for row in reader:
            if not row:
                continue

            first = _strip_cell(row[0])

            # --- section header ---
            sec_match = _SECTION_RE.match(first)
            if sec_match:
                current_section = sec_match.group(1)
                current_section_title = sec_match.group(2).strip()
                current_step = None
                continue

            # --- column header row (resolve indices) ---
            if first.lower() == "step":
                cols = _resolve_columns(row, col_map)
                continue

            # Skip data rows before we've seen a header
            if cols is None:
                continue

            # --- step row ---
            step_match = _STEP_RE.match(first)
            content = _strip_cell(row[cols.content]) if len(row) > cols.content else ""
            asset = (
                _strip_cell(row[cols.asset_name]) if len(row) > cols.asset_name else ""
            )

            if step_match:
                step_id = step_match.group(1)
                if step_id in seen_ids:
                    log.warning("Duplicate step_id '%s' — skipping.", step_id)
                    current_step = None
                    continue
                seen_ids.add(step_id)
                step_behavior = detect_behavior(content)
                current_step = BuilderStep(
                    step_id=step_id,
                    section=current_section,
                    section_title=current_section_title,
                    content=content,
                )
                steps.append(current_step)

                if asset and asset not in ("N/A",):
                    obj = BuilderObject(name=asset, behavior=step_behavior)
                    current_step.objects.append(obj)
                continue

            # --- continuation row (belongs to previous step) ---
            if current_step is not None:
                # Merge continuation content into the parent step
                if content:
                    current_step.content += " " + content

                if asset and asset not in ("N/A",):
                    # Own content overrides, otherwise inherit from parent step
                    row_behavior = detect_behavior(content) if content else ""
                    behavior = row_behavior or detect_behavior(current_step.content)
                    obj = BuilderObject(name=asset, behavior=behavior)
                    current_step.objects.append(obj)

    return steps


# ---------------------------------------------------------------------------
# Gap detection  (canonical implementation lives in _shots)
# ---------------------------------------------------------------------------

from mayatk.anim_utils.shots._shots import (  # noqa: E402
    _motion_frames_for_curve,
    detect_animation_gaps,
)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class ShotManifest:
    """Creates shot store entries from parsed steps and applies behaviors.

    Duration for each step is derived entirely from behavior templates.
    Layout is computed from the current store state (new shots append
    after the last existing shot; frame 1 when empty).

    Parameters:
        store: Target ``ShotStore`` instance to populate.
    """

    def __init__(self, store: ShotStore):
        self.store = store

    @staticmethod
    def _step_metadata(step: BuilderStep) -> Dict[str, Any]:
        """Build a metadata dict from a parsed step."""
        return {
            "section": step.section,
            "section_title": step.section_title,
            "csv_objects": [o.name for o in step.objects],
            "behaviors": [
                {"name": o.name, "behavior": o.behavior}
                for o in step.objects
                if o.behavior
            ],
        }

    # ---- sync (thin orchestrator) ----------------------------------------

    def sync(
        self,
        steps: List[BuilderStep],
        apply_behaviors: bool = True,
        ranges: Optional[Dict[str, Tuple[float, float]]] = None,
    ) -> Tuple[Dict[str, str], Dict[str, list], List[StepStatus]]:
        """Full build pipeline: update → apply behaviors → assess.

        Parameters:
            steps: Parsed steps to build.
            apply_behaviors: If True (default), detected behaviors are
                applied to Maya objects after updating the store.
            ranges: Optional mapping of ``step_id`` → ``(start, end)``
                frame ranges.  When provided, shots are placed at these
                positions instead of being sequentially appended.

        Returns:
            ``(actions, behavior_result, assessment)`` tuple.
        """
        actions = self.update(steps, ranges=ranges)

        behavior_result: Dict[str, list] = {"applied": [], "skipped": []}
        if apply_behaviors:
            from mayatk.anim_utils.shots.behaviors import (
                apply_behavior,
                apply_to_shots,
            )

            actionable = [
                s for s in self.store.sorted_shots() if actions.get(s.name) == "created"
            ]
            behavior_result = apply_to_shots(actionable, apply_fn=apply_behavior)

        assessment = self.assess(steps)
        return actions, behavior_result, assessment

    # ---- update (data-only sync) ----------------------------------------

    def update(
        self,
        steps: List[BuilderStep],
        ranges: Optional[Dict[str, Tuple[float, float]]] = None,
    ) -> Dict[str, str]:
        """Sync parsed steps to the ShotStore (data only, no behaviors).

        Compares each step against the existing store state:

        - **New shot**: created at the end of the timeline (after the
          last existing shot).
        - **Changed shot** (CSV added/removed objects or changed
          behaviors in metadata): the shot's object list and metadata
          are synced to the CSV.  Non-CSV objects (scene-discovered) are
          preserved.
        - **Unchanged shot**: skipped entirely.
        - **Locked shot**: skipped (metadata, objects, and description
          are protected).
        - **Removed shot** (in store but not in CSV): deleted from the
          store.

        Parameters:
            steps: Parsed steps from CSV.
            ranges: Optional mapping of ``step_id`` → ``(start, end)``
                frame ranges.  When provided, shots use these positions
                instead of sequential cursor placement.

        Returns:
            Dict mapping ``step_id`` → action taken
            (``"created"`` | ``"patched"`` | ``"skipped"``
            | ``"locked"`` | ``"removed"``).
        """
        from mayatk.anim_utils.shots.behaviors import compute_duration

        sorted_shots = self.store.sorted_shots()
        built_map = {s.name: s for s in sorted_shots}
        csv_ids = {step.step_id for step in steps}
        actions: Dict[str, str] = {}

        # Remove shots no longer in CSV
        for name, shot in list(built_map.items()):
            if name not in csv_ids:
                self.store.remove_shot(shot.shot_id)
                actions[name] = "removed"

        # Determine cursor for new shots (after all existing shots)
        cursor = sorted_shots[-1].end if sorted_shots else 1

        for step in steps:
            existing = built_map.get(step.step_id)

            if existing is None:
                # New shot — create it with metadata
                rng = ranges.get(step.step_id) if ranges else None
                if rng is not None:
                    start, end = rng
                else:
                    dur = compute_duration(step.objects)
                    start = cursor
                    end = start + dur
                obj_names = [o.name for o in step.objects]
                meta = self._step_metadata(step)
                self.store.define_shot(
                    name=step.step_id,
                    start=start,
                    end=end,
                    objects=obj_names,
                    metadata=meta,
                    description=step.content,
                )
                cursor = end
                actions[step.step_id] = "created"
            else:
                # Locked shots are protected — skip all content changes
                if existing.locked:
                    actions[step.step_id] = "locked"
                    continue

                # Reposition shot if user-provided range differs
                repositioned = False
                rng = ranges.get(step.step_id) if ranges else None
                if rng is not None:
                    new_start, new_end = rng
                    if existing.start != new_start or existing.end != new_end:
                        self.store.update_shot(
                            existing.shot_id,
                            start=new_start,
                            end=new_end,
                        )
                        repositioned = True

                # Shot exists — detect CSV-side add/remove/change
                csv_obj_map = {o.name: o.behavior for o in step.objects}
                csv_objs = set(csv_obj_map)

                # Compare against previous CSV objects (not all shot objects)
                old_csv_objs = set(
                    existing.metadata.get("csv_objects", existing.objects)
                )
                # Scene-discovered objects (in shot but never from CSV)
                scene_objs = set(existing.objects) - old_csv_objs

                # Build a map of old behaviors from metadata
                old_behaviors: Dict[str, str] = {}
                for entry in existing.metadata.get("behaviors", []):
                    old_behaviors[entry["name"]] = entry.get("behavior", "")

                new_objs = csv_objs - old_csv_objs
                removed_objs = old_csv_objs - csv_objs
                changed_beh = {
                    name
                    for name in csv_objs & old_csv_objs
                    if csv_obj_map.get(name, "") != old_behaviors.get(name, "")
                }

                # Update metadata and description from CSV
                existing.metadata = self._step_metadata(step)
                existing.description = step.content or ""

                if not new_objs and not removed_objs and not changed_beh:
                    if repositioned:
                        actions[step.step_id] = "patched"
                    else:
                        actions[step.step_id] = "skipped"
                    continue

                # Merge CSV objects with preserved scene-discovered objects
                existing.objects = sorted(csv_objs | scene_objs)
                actions[step.step_id] = "patched"

        return actions

    # ---- assess ----------------------------------------------------------

    def assess(
        self,
        steps: List[BuilderStep],
        exists_fn: Optional[Callable[[str], bool]] = None,
        verify_fn: Optional[Callable] = None,
        keyframe_range_fn: Optional[
            Callable[[str], Optional[Tuple[float, float]]]
        ] = None,
    ) -> List[StepStatus]:
        """Compare parsed steps against the current store state.

        For each step, checks whether a matching shot has been built in
        :attr:`store`, whether every referenced object exists in the
        host application, and whether expected behavior keyframes are
        present.

        User-animated objects (no detected behavior) are checked for
        keyframe extent within the step range.  If their keys exceed the
        step boundaries, the step is flagged for expansion.

        Parameters:
            steps: Parsed steps from the CSV.
            exists_fn: Callable that returns ``True`` when an object name
                exists in the scene.  Defaults to ``pymel.core.objExists``.
            verify_fn: Callable ``(obj, behavior, start, end) -> bool``
                that returns ``True`` when the expected behaviour keys
                exist.  Defaults to
                :func:`~mayatk.anim_utils.shots.behaviors.verify_behavior`.
            keyframe_range_fn: Callable ``(obj) -> (min_time, max_time)``
                returning the full keyframe extent for a user-animated
                object, or ``None`` if no keys exist.

        Returns:
            One :class:`StepStatus` per step with per-object results.
        """
        if exists_fn is None:
            import maya.cmds as _cmds

            exists_fn = _cmds.objExists

        if verify_fn is None:
            from mayatk.anim_utils.shots.behaviors import verify_behavior

            verify_fn = lambda obj, beh, s, e: verify_behavior(obj, beh, s, e)

        # Invalidate per-assess caches
        self._animated_transforms = None

        if keyframe_range_fn is None:
            keyframe_range_fn = self._default_keyframe_range

        built_map = {s.name: s for s in self.store.sorted_shots()}

        results: List[StepStatus] = []
        for step in steps:
            shot = built_map.get(step.step_id)
            built = shot is not None
            is_locked = built and shot.locked

            # Locked shots are user-finalized — skip detailed checking
            if is_locked:
                obj_statuses = [
                    ObjectStatus(
                        name=o.name,
                        exists=True,
                        status="valid",
                        behavior=o.behavior,
                    )
                    for o in step.objects
                ]
                results.append(
                    StepStatus(
                        step_id=step.step_id,
                        built=True,
                        objects=obj_statuses,
                        locked=True,
                    )
                )
                continue

            obj_statuses = []
            max_key_end = shot.end if shot else 0.0
            for obj in step.objects:
                exists = exists_fn(obj.name)
                key_range = None
                if not exists:
                    status = "missing_object"
                elif built and obj.behavior:
                    has_keys = verify_fn(obj.name, obj.behavior, shot.start, shot.end)
                    status = "valid" if has_keys else "missing_behavior"
                elif built and not obj.behavior:
                    # User-animated: query actual keyframe extent
                    key_range = keyframe_range_fn(obj.name)
                    status = "user_animated" if key_range else "valid"
                    if key_range and key_range[1] > max_key_end:
                        max_key_end = key_range[1]
                else:
                    status = "valid"
                obj_statuses.append(
                    ObjectStatus(
                        name=obj.name,
                        exists=exists,
                        status=status,
                        behavior=obj.behavior,
                        key_range=key_range,
                    )
                )

            # Detect additional objects (in shot but not in CSV)
            additional = []
            if shot is not None:
                csv_names = {o.name for o in step.objects}
                additional = [n for n in shot.objects if n not in csv_names]
                # Also discover scene objects with keys in this shot's
                # range that aren't tracked in the CSV or the store.
                scene_extra = self._discover_scene_objects(
                    shot.start, shot.end, csv_names | set(shot.objects)
                )
                additional.extend(scene_extra)
                # Merge discovered objects into the shot so the sequencer
                # can display them (it reads shot.objects).
                if scene_extra:
                    shot.objects = sorted(set(shot.objects) | set(scene_extra))

            # Compute shrinkable frames (unused tail)
            shrinkable = 0.0
            if built and shot is not None:
                content_end = self._compute_content_end(step, shot, obj_statuses)
                if content_end < shot.end:
                    shrinkable = shot.end - content_end

            results.append(
                StepStatus(
                    step_id=step.step_id,
                    built=built,
                    objects=obj_statuses,
                    additional_objects=additional,
                    shrinkable_frames=shrinkable,
                )
            )
        return results

    def _discover_scene_objects(
        self,
        start: float,
        end: float,
        exclude_names: set,
    ) -> List[str]:
        """Find transform nodes with keyframes in [start, end] not in *exclude_names*.

        The curve-to-transform mapping is built once per assess cycle and
        cached on ``self._animated_transforms`` to avoid redundant
        ``ls``/``listConnections`` calls when multiple steps are checked.
        """
        try:
            import maya.cmds as cmds
        except ImportError:
            return []

        # Build (and cache) the set of animated transforms once
        animated = getattr(self, "_animated_transforms", None)
        if animated is None:
            curves = cmds.ls(type="animCurve") or []
            animated = set()
            for crv in curves:
                conns = (
                    cmds.listConnections(crv, d=True, s=False, type="transform") or []
                )
                animated.update(conns)
            self._animated_transforms = animated

        found: list = []
        for obj in sorted(animated):
            if obj in exclude_names:
                continue
            keys = cmds.keyframe(obj, q=True, time=(start, end))
            if keys:
                found.append(obj)
        return found

    @staticmethod
    def _default_keyframe_range(obj_name: str) -> Optional[Tuple[float, float]]:
        """Query the full keyframe time range for an object in Maya."""
        try:
            import maya.cmds as cmds

            times = cmds.keyframe(obj_name, q=True, tc=True)
            if times:
                return (min(times), max(times))
        except Exception:
            pass
        return None

    @staticmethod
    def _compute_content_end(
        step: BuilderStep,
        scene,
        obj_statuses: List[ObjectStatus],
    ) -> float:
        """Return the latest frame used by content in this step."""
        from mayatk.anim_utils.shots.behaviors import load_behavior

        latest = scene.start  # at minimum, content starts at scene start
        for obj, obj_st in zip(step.objects, obj_statuses):
            if obj.behavior:
                try:
                    tmpl = load_behavior(obj.behavior)
                except FileNotFoundError:
                    continue
                for _attr, attr_def in tmpl.get("attributes", {}).items():
                    for phase in ("in", "out"):
                        block = attr_def.get(phase)
                        if not block:
                            continue
                        anchor = block.get(
                            "anchor", "start" if phase == "in" else "end"
                        )
                        offset = block.get("offset", 0)
                        dur = block.get("duration", 0)
                        if anchor == "end":
                            end_t = scene.end - offset
                        else:
                            end_t = scene.start + offset + dur
                        if end_t > latest:
                            latest = end_t
            if obj_st.key_range:
                if obj_st.key_range[1] > latest:
                    latest = obj_st.key_range[1]
        return latest

    # ---- from_csv --------------------------------------------------------

    @staticmethod
    def from_csv(
        filepath: str,
        store: Optional[ShotStore] = None,
        columns: Optional[ColumnMap] = None,
    ) -> Tuple["ShotManifest", List[BuilderStep]]:
        """Convenience: parse a CSV and return a ready-to-build engine.

        Parameters:
            filepath: Path to the CSV file.
            store: Optional existing ``ShotStore`` to populate.
                If ``None``, a fresh instance is created.
            columns: Column index mapping.

        Returns:
            ``(builder, steps)`` tuple. Call ``builder.sync(steps)`` to
            execute.
        """
        steps = parse_csv(filepath, columns)
        st = store or ShotStore()
        builder = ShotManifest(st)
        return builder, steps
