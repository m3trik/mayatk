# coding=utf-8
"""Shot Manifest — parse structured CSVs and populate a ShotStore.

Reads a CSV with section/step structure, auto-detects object behaviors
from textual descriptions, and registers shots in a
:class:`~mayatk.anim_utils.shots._shots.ShotStore`.
"""
import csv
import logging
import re
from dataclasses import dataclass, field, fields
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
    behaviors: List[str] = field(default_factory=list)  # e.g. ["fade_in", "fade_out"]


@dataclass
class BuilderStep:
    """One step (= one future sequencer shot)."""

    step_id: str  # e.g. "A04"
    section: str  # e.g. "A"
    section_title: str  # e.g. "AILERON RIGGING"
    description: str  # merged step-contents text (used for behavior detection)
    objects: List[BuilderObject] = field(default_factory=list)
    audio: str = ""  # narration/voice-over text (display priority over description)

    @property
    def display_text(self) -> str:
        """Text shown in the tree Description column.

        Returns *audio* when available, otherwise *description*.
        """
        return self.audio if self.audio else self.description

    @classmethod
    def from_detection(
        cls,
        candidates: List[Dict],
    ) -> Tuple[List["BuilderStep"], Dict[str, Tuple[float, float]]]:
        """Convert detection candidates to BuilderSteps + pre-filled ranges.

        Parameters:
            candidates: List of dicts with keys: name, start, end, objects.

        Returns:
            ``(steps, ranges)`` — steps list and dict mapping
            ``step_id`` → ``(start, end)``.
        """
        steps: List["BuilderStep"] = []
        ranges: Dict[str, Tuple[float, float]] = {}
        for i, cand in enumerate(candidates):
            step_id = cand.get("name")
            start = cand.get("start")
            end = cand.get("end")
            if step_id is None or start is None or end is None:
                import logging

                logging.getLogger(__name__).warning(
                    "Skipping detection candidate %d: missing required "
                    "key(s) (name=%r, start=%r, end=%r)",
                    i,
                    step_id,
                    start,
                    end,
                )
                continue
            obj_names = cand.get("objects", [])
            objects = [BuilderObject(name=n) for n in obj_names]
            step = cls(
                step_id=step_id,
                section="",
                section_title="",
                description="",
                objects=objects,
            )
            steps.append(step)
            ranges[step_id] = (start, end)
        return steps, ranges


# ---------------------------------------------------------------------------
# Assessment data structures
# ---------------------------------------------------------------------------


@dataclass
class ObjectStatus:
    """Assessment result for one object within a step."""

    name: str
    exists: bool
    status: str  # "valid" | "missing_object" | "missing_behavior" | "user_animated"
    behaviors: List[str] = field(
        default_factory=list
    )  # expected behaviors (empty = user-animated)
    broken_behaviors: List[str] = field(
        default_factory=list
    )  # subset of *behaviors* that failed verification
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
    (re.compile(r"\bfades?\s+in\b", re.I), "fade_in"),
    (re.compile(r"\bfades?\s+out\b", re.I), "fade_out"),
]


def detect_behaviors(text: str) -> List[str]:
    """Return behavior names inferred from descriptive *text*.

    Each pattern is tested independently so text mentioning both
    "fades in" and "fades out" yields ``["fade_in", "fade_out"]``.
    """
    found = []
    for pattern, name in _BEHAVIOR_PATTERNS:
        if pattern.search(text):
            found.append(name)
    return found


# ---------------------------------------------------------------------------
# CSV column mapping
# ---------------------------------------------------------------------------


@dataclass
class ColumnMap:
    """Maps logical fields to CSV header names (case-insensitive).

    Each field is a tuple of acceptable header aliases. The parser reads
    the header row and resolves names to column indices automatically.

    Serialisable via :meth:`to_dict` / :meth:`from_dict` so instances
    can be stored as preset metadata.
    """

    step_id: Tuple[str, ...] = ("Step",)
    description: Tuple[str, ...] = ("Step Contents", "Contents")
    assets: Tuple[str, ...] = ("Asset Names", "Asset")
    audio: Tuple[str, ...] = ("Voice Support", "Voice")
    exclude_steps: Tuple[str, ...] = ("SETUP",)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-safe dict (tuples → lists)."""
        return {f.name: list(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ColumnMap":
        """Reconstruct from a dict produced by :meth:`to_dict`."""
        known = {f.name for f in fields(cls)}
        return cls(
            **{
                k: tuple(v) if isinstance(v, list) else v
                for k, v in data.items()
                if k in known
            }
        )


@dataclass
class _ResolvedColumns:
    """Integer column indices resolved from a header row."""

    step_id: int
    description: int
    assets: int
    audio: Optional[int] = None


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

    def _find_optional(aliases: Tuple[str, ...]) -> Optional[int]:
        for alias in aliases:
            try:
                return normalized.index(alias.lower())
            except ValueError:
                continue
        return None

    return _ResolvedColumns(
        step_id=_find(col_map.step_id, "step_id"),
        description=_find(col_map.description, "description"),
        assets=_find(col_map.assets, "assets"),
        audio=_find_optional(col_map.audio) if col_map.audio else None,
    )


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(r"^SECTION\s+([A-Z0-9]+)\s*:\s*(.*)", re.I)
_STEP_RE = re.compile(r"^([A-Z]\d+)\.\)")
_ALT_STEP_RE = re.compile(r"^([A-Z]{2,})$")  # non-numbered IDs: SETUP, INTRO …


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
    step_id_aliases = {a.lower() for a in col_map.step_id}

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
            if first.lower() in step_id_aliases:
                cols = _resolve_columns(row, col_map)
                continue

            # Skip data rows before we've seen a header
            if cols is None:
                continue

            # --- step row ---
            step_match = _STEP_RE.match(first) or _ALT_STEP_RE.match(first)
            description = (
                _strip_cell(row[cols.description])
                if len(row) > cols.description
                else ""
            )
            asset = _strip_cell(row[cols.assets]) if len(row) > cols.assets else ""
            audio = (
                _strip_cell(row[cols.audio])
                if cols.audio is not None and len(row) > cols.audio
                else ""
            )

            if step_match:
                step_id = step_match.group(1)
                if step_id in seen_ids:
                    log.warning("Duplicate step_id '%s' — skipping.", step_id)
                    current_step = None
                    continue
                seen_ids.add(step_id)
                step_behaviors = detect_behaviors(description)
                current_step = BuilderStep(
                    step_id=step_id,
                    section=current_section,
                    section_title=current_section_title,
                    description=description,
                    audio=audio,
                )
                steps.append(current_step)

                if asset and asset not in ("N/A",):
                    obj = BuilderObject(
                        name=asset,
                        behaviors=list(step_behaviors),
                    )
                    current_step.objects.append(obj)
                continue

            # --- continuation row (belongs to previous step) ---
            if current_step is not None:
                # Merge continuation description into the parent step
                if description:
                    current_step.description += " " + description

                if asset and asset not in ("N/A",):
                    # Own description overrides, otherwise inherit from parent step
                    row_behaviors = detect_behaviors(description) if description else []
                    behaviors = row_behaviors or detect_behaviors(
                        current_step.description
                    )
                    obj = BuilderObject(
                        name=asset,
                        behaviors=list(behaviors),
                    )
                    current_step.objects.append(obj)

    # Apply exclude list
    if col_map.exclude_steps:
        excluded = {s.upper() for s in col_map.exclude_steps}
        steps = [s for s in steps if s.step_id.upper() not in excluded]

    return steps


# ---------------------------------------------------------------------------
# Shot-region detection  (canonical implementation lives in _shots)
# ---------------------------------------------------------------------------

from mayatk.anim_utils.shots._shots import (  # noqa: E402
    detect_shot_regions,
    regions_from_selected_keys,
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
                {"name": o.name, "behavior": b}
                for o in step.objects
                for b in o.behaviors
            ],
        }

    # ---- sync (thin orchestrator) ----------------------------------------

    def sync(
        self,
        steps: List[BuilderStep],
        apply_behaviors: bool = True,
        ranges: Optional[Dict[str, Tuple[float, float]]] = None,
        remove_missing: bool = True,
        zero_duration_fallback: bool = False,
    ) -> Tuple[Dict[str, str], Dict[str, list], List[StepStatus]]:
        """Full build pipeline: update → apply behaviors → assess.

        Parameters:
            steps: Parsed steps to build.
            apply_behaviors: If True (default), detected behaviors are
                applied to Maya objects after updating the store.
            ranges: Optional mapping of ``step_id`` → ``(start, end)``
                frame ranges.  When provided, shots are placed at these
                positions instead of being sequentially appended.
            remove_missing: If True (default), shots in the store that
                are absent from *steps* are removed.  Set to False for
                scene-detection mode where existing shots should be
                preserved.
            zero_duration_fallback: If True, new shots without an
                explicit range are created with zero duration instead
                of using ``compute_duration``.  Used during incremental
                builds to avoid disrupting existing shot positions.

        Returns:
            ``(actions, behavior_result, assessment)`` tuple.
        """
        actions = self.update(
            steps,
            ranges=ranges,
            remove_missing=remove_missing,
            zero_duration_fallback=zero_duration_fallback,
        )

        behavior_result: Dict[str, list] = {"applied": [], "skipped": []}
        if apply_behaviors:
            from mayatk.anim_utils.shots.shot_manifest.behaviors import (
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
        remove_missing: bool = True,
        zero_duration_fallback: bool = False,
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
          store (unless *remove_missing* is False).

        Parameters:
            steps: Parsed steps from CSV.
            ranges: Optional mapping of ``step_id`` → ``(start, end)``
                frame ranges.  When provided, shots use these positions
                instead of sequential cursor placement.
            remove_missing: If True (default), shots in the store
                that are absent from *steps* are removed.  Set to
                False for scene-detection mode where existing shots
                should be preserved.
            zero_duration_fallback: If True, new shots without an
                explicit range are created with zero duration (start
                == end) instead of using ``compute_duration``.

        Returns:
            Dict mapping ``step_id`` → action taken
            (``"created"`` | ``"patched"`` | ``"skipped"``
            | ``"locked"`` | ``"removed"``).
        """
        from mayatk.anim_utils.shots.shot_manifest.behaviors import compute_duration

        sorted_shots = self.store.sorted_shots()
        built_map = {s.name: s for s in sorted_shots}
        csv_ids = {step.step_id for step in steps}
        actions: Dict[str, str] = {}

        # Remove shots no longer in steps (opt-out for detection mode)
        if remove_missing:
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
                elif zero_duration_fallback:
                    start = cursor
                    end = start
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
                    description=step.display_text,
                )
                for n in obj_names:
                    self.store.set_object_pinned(n)
                # Advance cursor past the new shot; for zero-duration
                # shots use the store gap to prevent stacking.
                if end == start:
                    cursor = end + (self.store.gap if self.store.gap > 0 else 1)
                else:
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
                csv_obj_map = {o.name: sorted(o.behaviors) for o in step.objects}
                csv_objs = set(csv_obj_map)

                # Compare against previous CSV objects (not all shot objects)
                old_csv_objs = set(
                    existing.metadata.get("csv_objects", existing.objects)
                )
                # Scene-discovered objects (in shot but never from CSV)
                scene_objs = set(existing.objects) - old_csv_objs

                # Build a map of old behaviors from metadata (list per object)
                old_behaviors: Dict[str, List[str]] = {}
                for entry in existing.metadata.get("behaviors", []):
                    old_behaviors.setdefault(entry["name"], []).append(
                        entry.get("behavior", "")
                    )
                for k in old_behaviors:
                    old_behaviors[k] = sorted(old_behaviors[k])

                new_objs = csv_objs - old_csv_objs
                removed_objs = old_csv_objs - csv_objs
                changed_beh = {
                    name
                    for name in csv_objs & old_csv_objs
                    if csv_obj_map.get(name, []) != old_behaviors.get(name, [])
                }

                # Update metadata and description from CSV
                existing.metadata = self._step_metadata(step)
                existing.description = step.display_text or ""

                if not new_objs and not removed_objs and not changed_beh:
                    if repositioned:
                        actions[step.step_id] = "patched"
                    else:
                        actions[step.step_id] = "skipped"
                    continue

                # Filter scene-discovered objects to those with actual
                # motion — drop flat-key objects from previous builds.
                if scene_objs:
                    scene_objs = set(
                        self._filter_to_animated(
                            sorted(scene_objs), existing.start, existing.end
                        )
                    )
                # Merge CSV objects with preserved scene-discovered objects
                from mayatk.anim_utils.shots._shots import _resolve_long_names

                merged = sorted(csv_objs | scene_objs)
                resolved = _resolve_long_names(merged)
                existing.objects = resolved if resolved else merged
                for n in csv_objs:
                    self.store.set_object_pinned(n)
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
                :func:`~mayatk.anim_utils.shots.shot_manifest.behaviors.verify_behavior`.
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
            from mayatk.anim_utils.shots.shot_manifest.behaviors import verify_behavior

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
                        behaviors=list(o.behaviors),
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
                broken = []
                if not exists:
                    status = "missing_object"
                elif built and obj.behaviors:
                    # Check each declared behavior individually
                    broken = [
                        b
                        for b in obj.behaviors
                        if not verify_fn(obj.name, b, shot.start, shot.end)
                    ]
                    status = "missing_behavior" if broken else "valid"
                elif built and not obj.behaviors:
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
                        behaviors=list(obj.behaviors),
                        broken_behaviors=broken,
                        key_range=key_range,
                    )
                )

            # Detect additional objects (in shot but not in CSV)
            additional = []
            if shot is not None:
                from mayatk.anim_utils.shots.shot_manifest._manifest_data import (
                    short_name as _short,
                )

                csv_short = {_short(o.name) for o in step.objects}
                stored_extra = [
                    n for n in shot.objects if _short(n) not in csv_short
                ]
                # Filter stored extras to only those with actual motion
                # (removes flat-key objects from previous builds).
                if stored_extra:
                    stored_extra = self._filter_to_animated(
                        stored_extra, shot.start, shot.end
                    )
                additional = stored_extra
                # Also discover scene objects with keys in this shot's
                # range that aren't tracked in the CSV or the store.
                known = csv_short | {_short(n) for n in shot.objects}
                scene_extra = self._discover_scene_objects(
                    shot.start, shot.end, known
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
        """Find transform nodes with non-flat standard-attribute animation in [start, end].

        Only objects with animation on standard transform/visibility
        attributes whose values actually change (variance > 1e-4) are
        returned.  Objects with flat keys or animated exclusively on
        custom attributes (e.g. ``audio_trigger``) are treated as
        boundary markers and excluded.

        The curve-to-transform mapping is built once per assess cycle and
        cached on ``self._animated_transforms`` to avoid redundant
        ``ls``/``listConnections`` calls when multiple steps are checked.
        """
        try:
            import maya.cmds as cmds
        except ImportError:
            return []

        from mayatk.anim_utils.shots._shots import _map_standard_curves_to_transforms

        # Build (and cache) the map: transform → [standard-attr curves]
        animated = getattr(self, "_animated_transforms", None)
        if animated is None:
            animated = _map_standard_curves_to_transforms()
            self._animated_transforms = animated

        found: list = []
        from mayatk.anim_utils.shots.shot_manifest._manifest_data import (
            short_name as _short,
        )

        for obj in sorted(animated):
            if _short(obj) in exclude_names:
                continue
            for crv in animated[obj]:
                vals = cmds.keyframe(crv, q=True, time=(start, end), valueChange=True)
                if vals and (max(vals) - min(vals)) > 1e-4:
                    found.append(obj)
                    break

        return found

    @staticmethod
    def _filter_to_animated(objects: List[str], start: float, end: float) -> List[str]:
        """Return only objects that have standard-attribute animation in [start, end].

        Objects animated exclusively on custom attributes (e.g.
        ``audio_trigger``) are treated as boundary markers and excluded.
        """
        if not objects:
            return []

        try:
            import maya.cmds as cmds
        except ImportError:
            return objects

        from mayatk.anim_utils.shots._shots import _map_standard_curves_to_transforms

        transform_curves = _map_standard_curves_to_transforms()
        result = []
        for obj in objects:
            crvs = transform_curves.get(obj)
            if not crvs:
                continue
            for crv in crvs:
                vals = cmds.keyframe(crv, q=True, time=(start, end), valueChange=True)
                if vals and (max(vals) - min(vals)) > 1e-4:
                    result.append(obj)
                    break
        return result

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
        from mayatk.anim_utils.shots.shot_manifest.behaviors import load_behavior

        latest = scene.start  # at minimum, content starts at scene start
        for obj, obj_st in zip(step.objects, obj_statuses):
            for beh in obj.behaviors:
                try:
                    tmpl = load_behavior(beh)
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
        st = store or ShotStore.active()
        builder = ShotManifest(st)
        return builder, steps
