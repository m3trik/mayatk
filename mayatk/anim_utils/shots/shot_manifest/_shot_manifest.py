# !/usr/bin/python
# coding=utf-8
"""Shot Manifest â€” parse structured CSVs and populate a ShotStore.

Reads a CSV with section/step structure, auto-detects object behaviors
from textual descriptions, and registers shots in a
:class:`~mayatk.anim_utils.shots._shots.ShotStore`.
"""
import csv
import logging
import re
from dataclasses import dataclass, field, fields
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from mayatk.anim_utils.shots._shots import ShotStore
from mayatk.audio_utils._audio_utils import AudioUtils

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class BuilderObject:
    """One asset within a step."""

    name: str
    behaviors: List[str] = field(default_factory=list)  # e.g. ["fade_in", "fade_out"]
    kind: str = "scene"  # "scene" | "audio"
    source_path: str = ""  # file path for audio creation (transient)


@dataclass
class BuilderStep:
    """One step (= one future sequencer shot)."""

    step_id: str  # e.g. "A04"
    section: str  # e.g. "A"
    section_title: str  # e.g. "AILERON RIGGING"
    description: str  # merged step-contents text (used for behavior detection)
    objects: List[BuilderObject] = field(default_factory=list)
    audio: str = ""  # narration/voice-over text from CSV

    @property
    def display_text(self) -> str:
        """Text shown in the tree Description column."""
        return self.description

    @classmethod
    def from_detection(
        cls,
        candidates: List[Dict],
    ) -> Tuple[List["BuilderStep"], Dict[str, Tuple[float, float]]]:
        """Convert detection candidates to BuilderSteps + pre-filled ranges.

        Parameters:
            candidates: List of dicts with keys: name, start, end, objects.

        Returns:
            ``(steps, ranges)`` â€” steps list and dict mapping
            ``step_id`` â†’ ``(start, end)``.
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
# Build plan (compute-then-commit)
# ---------------------------------------------------------------------------

Action = Literal["created", "patched", "skipped", "locked", "removed"]


@dataclass
class PlannedShot:
    """Immutable build instruction computed before any store mutation.

    Produced by :meth:`ShotManifest._compute_plan` and consumed by
    :meth:`ShotManifest._execute_plan`.  Fields capture the *final*
    position each shot will occupy, so downstream consumers (behavior
    keying, ripple bookkeeping) never read stale ranges.
    """

    step: BuilderStep
    action: Action
    start: float = 0.0
    end: float = 0.0
    objects: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    description: str = ""
    existing_shot_id: Optional[int] = None
    ripple_delta: float = 0.0  # shift applied to later shots
    planned_objects: List["PlannedObject"] = field(default_factory=list)


FitMode = Literal["extend_only", "fit_contents"]

# Defaults live on ShotStore (single source of truth for shot-construction
# policy).  Re-exported here so the pure-python API retains keyword defaults.
DEFAULT_INITIAL_SHOT_LENGTH: float = ShotStore.DEFAULT_INITIAL_SHOT_LENGTH
DEFAULT_FIT_MODE: FitMode = ShotStore.DEFAULT_FIT_MODE  # type: ignore[assignment]


@dataclass
class PlannedKey:
    """One keyframe to be written during commit.

    All frame values are *absolute* (already offset by shot.start).
    """

    frame: float
    attr: str
    value: float


@dataclass
class PlannedObject:
    """Per-object build instruction with fully-resolved keyframes.

    Lives inside a :class:`PlannedShot`.  No Maya calls required to
    produce or consume this structure; commit just replays the keys.
    """

    name: str
    kind: str = "scene"  # "scene" | "audio"
    source_path: str = ""
    behaviors: List[str] = field(default_factory=list)
    keys: List[PlannedKey] = field(default_factory=list)
    # For audio: the resolved clip length in frames at scene FPS.
    audio_span: float = 0.0


@dataclass
class BuildPlan:
    """Complete plan for a build pass. Pure data, no Maya references."""

    shots: List[PlannedShot] = field(default_factory=list)
    fit_mode: FitMode = DEFAULT_FIT_MODE
    initial_shot_length: float = DEFAULT_INITIAL_SHOT_LENGTH
    fps: float = 24.0


# ---------------------------------------------------------------------------
# Duration resolution  (pure — no Maya, only file probes)
# ---------------------------------------------------------------------------


def resolve_duration(
    step: BuilderStep,
    initial_shot_length: float,
    fit_mode: FitMode,
    fps: float,
) -> Tuple[float, float, float]:
    """Compute final shot duration for *step* under the given fit policy.

    Probes behavior templates and audio file lengths to determine the
    minimum content-driven length, then applies *fit_mode* against the
    user-specified *initial_shot_length* (default 200f).

    Returns:
        ``(duration, behavior_span, audio_span)`` — the resolved shot
        length plus the individual content measurements that drove it.
    """
    from mayatk.anim_utils.shots.shot_manifest.behaviors import load_behavior

    try:
        from mayatk.audio_utils._audio_utils import AudioUtils as _AU
    except Exception:
        _AU = None  # type: ignore[assignment]

    audio_span = 0.0
    max_obj_total = 0.0
    global_max_in = 0.0
    global_max_out = 0.0

    for obj in step.objects:
        # ---- behavior template durations (phase-aware) ----
        obj_in = 0.0
        obj_out = 0.0
        for b in obj.behaviors:
            if not b:
                continue
            try:
                tmpl = load_behavior(b)
            except FileNotFoundError:
                continue
            dur_field = tmpl.get("duration")
            if dur_field == "from_source":
                continue  # handled via audio_span below
            for _attr_name, attr_def in tmpl.get("attributes", {}).items():
                for phase in ("in", "out"):
                    block = attr_def.get(phase)
                    if not block:
                        continue
                    d = float(block.get("duration", 0) or 0)
                    if phase == "in":
                        obj_in += d
                    else:
                        obj_out += d
        max_obj_total = max(max_obj_total, obj_in + obj_out)
        global_max_in = max(global_max_in, obj_in)
        global_max_out = max(global_max_out, obj_out)

        # ---- audio clip length ----
        if obj.kind == "audio":
            src = obj.source_path
            if not src and _AU is not None:
                try:
                    tid = _AU.normalize_track_id(obj.name)
                    if _AU.has_track(tid):
                        src = _AU.get_path(tid) or ""
                except Exception as exc:
                    log.debug(
                        "audio track-path lookup failed for %r: %s", obj.name, exc
                    )
            if src and _AU is not None:
                try:
                    frames, _ = _AU.audio_duration_frames(src, fps)
                    if frames > 0:
                        audio_span = max(audio_span, float(frames))
                except Exception as exc:
                    log.debug("audio duration probe failed for %r: %s", obj.name, exc)

    behavior_span = max(max_obj_total, global_max_in + global_max_out)
    content_min = max(behavior_span, audio_span)

    if fit_mode == "extend_only":
        duration = max(initial_shot_length, content_min)
    elif fit_mode == "fit_contents":
        duration = content_min if content_min > 0 else initial_shot_length
    else:
        raise ValueError(f"Unknown fit_mode: {fit_mode!r}")

    return duration, behavior_span, audio_span


def plan_object_keys(
    obj: BuilderObject,
    shot_start: float,
    shot_end: float,
    fps: float,
) -> "PlannedObject":
    """Materialise a :class:`PlannedObject` with absolute keyframes.

    Non-audio behaviors are distributed positionally across the shot
    span by their index in ``obj.behaviors``:
    ``anchor = idx / max(total - 1, 1)`` (0.0=start, 0.5=middle,
    1.0=end).  Each behavior's block is slid linearly via
    ``base = shot_start + anchor * (span - duration) + offset`` — the
    same formula used at commit time by ``apply_behavior`` /
    :func:`resolve_keys`.  The YAML template's ``anchor`` field is a
    default used only by direct callers; it is overridden here.

    Audio objects with ``duration: from_source`` emit a two-key range
    at ``shot_start`` → ``shot_start + audio_span`` (value 1 / value 0)
    and do not participate in positional distribution — they key the
    track's carrier attr, not scene attributes.

    Pure function — no Maya calls, only behavior template file reads
    and optional audio file stat via :class:`AudioUtils`.
    """
    from mayatk.anim_utils.shots.shot_manifest.behaviors import load_behavior

    try:
        from mayatk.audio_utils._audio_utils import AudioUtils as _AU
    except Exception:
        _AU = None  # type: ignore[assignment]

    po = PlannedObject(
        name=obj.name,
        kind=obj.kind,
        source_path=obj.source_path,
        behaviors=list(obj.behaviors),
    )

    # Precompute non-audio behavior count so positional anchors can be
    # assigned by list position (matches apply_to_shots).  Audio
    # ``from_source`` behaviors are excluded — they are time-driven by
    # the clip length, not positionally distributed.
    non_audio_behaviors: List[str] = []
    for bname in obj.behaviors:
        if not bname:
            continue
        try:
            tmpl = load_behavior(bname)
        except FileNotFoundError:
            continue
        if tmpl.get("duration") == "from_source":
            continue
        non_audio_behaviors.append(bname)
    non_audio_total = len(non_audio_behaviors)
    non_audio_idx = 0

    for bname in obj.behaviors:
        if not bname:
            continue
        try:
            tmpl = load_behavior(bname)
        except FileNotFoundError:
            continue

        dur_field = tmpl.get("duration")
        if dur_field == "from_source":
            # Audio clip — two boundary keys on the track's enum attr.
            src = obj.source_path
            track_attr = ""
            if _AU is not None:
                try:
                    tid = _AU.normalize_track_id(obj.name)
                    track_attr = _AU.attr_for(tid)
                    if not src and _AU.has_track(tid):
                        src = _AU.get_path(tid) or ""
                except Exception as exc:
                    log.debug("audio track resolve failed for %r: %s", obj.name, exc)
            span = 0.0
            if src and _AU is not None:
                try:
                    frames, _ = _AU.audio_duration_frames(src, fps)
                    span = float(frames) if frames and frames > 0 else 0.0
                except Exception as exc:
                    log.debug("audio duration probe failed for %r: %s", obj.name, exc)
            if span <= 0 or not track_attr:
                # Without a real source or track attr we can't author
                # authoritative markers.  Skip — assess will flag it.
                continue
            po.audio_span = span
            po.source_path = po.source_path or src
            po.keys.append(PlannedKey(frame=shot_start, attr=track_attr, value=1))
            po.keys.append(
                PlannedKey(frame=shot_start + span, attr=track_attr, value=0)
            )
            continue

        # Scene behavior: iterate attributes and resolve in/out phases.
        # Positional anchor: distribute behaviors evenly across the shot
        # based on their order in obj.behaviors.  1 behavior → 0.0,
        # 2 → 0.0/1.0, 3 → 0.0/0.5/1.0, N → idx / max(total-1, 1).
        anchor = non_audio_idx / max(non_audio_total - 1, 1)
        non_audio_idx += 1
        span = shot_end - shot_start
        for attr_name, attr_def in tmpl.get("attributes", {}).items():
            for phase in ("in", "out"):
                block = attr_def.get(phase)
                if not block:
                    continue
                offset = float(block.get("offset", 0) or 0)
                duration = float(block.get("duration", 0) or 0)
                values = block.get("values", [0.0, 1.0])
                if len(values) < 2:
                    continue
                # Slide the behavior's block linearly between shot
                # start and end (end-aligned when anchor == 1.0).
                base = shot_start + anchor * (span - duration) + offset
                k0_frame = base
                k1_frame = base + duration
                po.keys.append(
                    PlannedKey(frame=k0_frame, attr=attr_name, value=float(values[0]))
                )
                po.keys.append(
                    PlannedKey(frame=k1_frame, attr=attr_name, value=float(values[-1]))
                )

    return po


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
    exclude_values: Dict[str, Tuple[str, ...]] = field(
        default_factory=lambda: {"assets": ("N/A",)}
    )
    metadata_pass: Dict[str, Tuple[str, ...]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-safe dict (tuples â†’ lists)."""
        result: Dict[str, Any] = {}
        for f in fields(self):
            val = getattr(self, f.name)
            if isinstance(val, dict):
                result[f.name] = {
                    k: list(v) if isinstance(v, tuple) else v for k, v in val.items()
                }
            else:
                result[f.name] = list(val)
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ColumnMap":
        """Reconstruct from a dict produced by :meth:`to_dict`."""
        known = {f.name for f in fields(cls)}
        kwargs: Dict[str, Any] = {}
        for k, v in data.items():
            if k not in known:
                continue
            if isinstance(v, dict):
                kwargs[k] = {
                    dk: tuple(dv) if isinstance(dv, list) else dv
                    for dk, dv in v.items()
                }
            elif isinstance(v, list):
                kwargs[k] = tuple(v)
            else:
                kwargs[k] = v
        return cls(**kwargs)


@dataclass
class _ResolvedColumns:
    """Integer column indices resolved from a header row."""

    step_id: int
    description: int
    assets: int
    audio: Optional[int] = None
    metadata_pass: Dict[str, int] = field(default_factory=dict)


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

    resolved = _ResolvedColumns(
        step_id=_find(col_map.step_id, "step_id"),
        description=_find(col_map.description, "description"),
        assets=_find(col_map.assets, "assets"),
        audio=_find_optional(col_map.audio) if col_map.audio else None,
    )
    for key, aliases in col_map.metadata_pass.items():
        idx = _find_optional(aliases)
        if idx is not None:
            resolved.metadata_pass[key] = idx
    return resolved


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(r"^SECTION\s+([A-Z0-9]+)\s*:\s*(.*)", re.I)
_STEP_RE = re.compile(r"^([A-Z]\d+)\.\)")
_ALT_STEP_RE = re.compile(r"^([A-Z]{2,})$")  # non-numbered IDs: SETUP, INTRO â€¦


def _strip_cell(cell: str) -> str:
    """Strip whitespace from a CSV cell."""
    return (cell or "").strip()


def parse_csv(
    filepath: str,
    columns: Optional[ColumnMap] = None,
    post_process: Optional[Callable[[BuilderStep], None]] = None,
) -> List[BuilderStep]:
    """Parse a structured CSV into a list of :class:`BuilderStep`.

    Parameters:
        filepath: Path to the CSV file.
        columns: Optional header-name mapping.  Defaults cover
            common layouts (C-5M, C-130H, C-17A).
        post_process: Optional callable invoked on each step after
            assembly.  Use to compute derived fields (e.g.
            audio objects) from the parsed data.

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
    asset_excludes = {v.upper() for v in col_map.exclude_values.get("assets", ())}
    # Per-step accumulator for metadata_pass values (first-row-wins)
    step_pass: Dict[str, str] = {}

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
                    log.warning("Duplicate step_id '%s' â€” skipping.", step_id)
                    current_step = None
                    continue
                seen_ids.add(step_id)
                step_behaviors = detect_behaviors(description)
                # Collect metadata_pass values for this step row
                step_pass = {}
                for key, idx in cols.metadata_pass.items():
                    val = _strip_cell(row[idx]) if len(row) > idx else ""
                    if val:
                        step_pass[key] = val
                current_step = BuilderStep(
                    step_id=step_id,
                    section=current_section,
                    section_title=current_section_title,
                    description=description,
                    audio=audio,
                )
                current_step._pass_through = dict(step_pass)
                steps.append(current_step)

                if asset and asset.upper() not in asset_excludes:
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

                if asset and asset.upper() not in asset_excludes:
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

    # Apply post-processing hook (e.g. derive audio objects from step fields)
    if post_process:
        for step in steps:
            post_process(step)

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
        self._fps_cache: Optional[float] = None

    @staticmethod
    def _step_metadata(
        step: BuilderStep,
        pass_through: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Build a metadata dict from a parsed step."""
        meta: Dict[str, Any] = {
            "section": step.section,
            "section_title": step.section_title,
            "csv_objects": [{"name": o.name, "kind": o.kind} for o in step.objects],
            "behaviors": [
                {
                    "name": o.name,
                    "behavior": b,
                    "kind": o.kind,
                    "source_path": o.source_path,
                }
                for o in step.objects
                for b in o.behaviors
            ],
        }
        if step.audio and step.audio.upper() != "N/A":
            meta["voice_text"] = step.audio
        if pass_through:
            meta.update(pass_through)
        return meta

    # ---- sync (thin orchestrator) ----------------------------------------

    def sync(
        self,
        steps: List[BuilderStep],
        apply_behaviors: bool = True,
        ranges: Optional[Dict[str, Tuple[float, float]]] = None,
        remove_missing: bool = True,
        zero_duration_fallback: bool = False,
        fit_mode: FitMode = DEFAULT_FIT_MODE,
        initial_shot_length: float = DEFAULT_INITIAL_SHOT_LENGTH,
    ) -> Tuple[Dict[str, str], Dict[str, list], List[StepStatus]]:
        """Full build pipeline: plan -> commit -> apply behaviors -> assess.

        Parameters:
            steps: Parsed steps to build.
            apply_behaviors: If True (default), detected behaviors are
                applied to Maya objects after updating the store.
            ranges: Optional mapping of ``step_id`` â†’ ``(start, end)``
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
            fit_mode=fit_mode,
            initial_shot_length=initial_shot_length,
        )

        behavior_result: Dict[str, list] = {"applied": [], "skipped": []}
        if apply_behaviors:
            from mayatk.anim_utils.shots.shot_manifest.behaviors import (
                apply_behavior,
                apply_to_shots,
            )

            behavior_result = apply_to_shots(
                self.store.sorted_shots(),
                apply_fn=apply_behavior,
                store=self.store,
            )

        # Rewire managed DG audio nodes so the sequencer/timeline
        # reflects any key changes authored above.  Idempotent.
        self.rewire_audio()

        assessment = self.assess(steps)
        return actions, behavior_result, assessment

    # ---- rewire (markers → DG audio nodes) -----------------------------

    @staticmethod
    def rewire_audio(tracks: Optional[List[str]] = None) -> Dict[str, List[str]]:
        """Reconcile managed DG audio nodes with keyed track state.

        Delegates to :func:`mayatk.audio_utils.compositor.sync`.  Safe
        to call any time — after a build, after Graph Editor marker
        edits, or standalone from the UI.

        Parameters:
            tracks: When provided, limit reconciliation to these
                ``track_id`` values.  Default: full scan.

        Returns:
            ``{"created": [...], "updated": [...], "deleted": [...]}``
            of DG audio node names, or empty lists if Maya is
            unavailable.
        """
        try:
            from mayatk.audio_utils.compositor import sync as _sync

            return _sync(tracks=tracks)
        except Exception as exc:
            log.debug("rewire_audio failed: %s", exc)
            return {"created": [], "updated": [], "deleted": []}

    # ---- update (data-only sync) ----------------------------------------

    def update(
        self,
        steps: List[BuilderStep],
        ranges: Optional[Dict[str, Tuple[float, float]]] = None,
        remove_missing: bool = True,
        zero_duration_fallback: bool = False,
        fit_mode: FitMode = DEFAULT_FIT_MODE,
        initial_shot_length: float = DEFAULT_INITIAL_SHOT_LENGTH,
    ) -> Dict[str, str]:
        """Sync parsed steps to the ShotStore (data only, no behaviors).

        Computes a full build plan, then commits it to the store in a
        single pass.  All position arithmetic (cursor placement,
        audio-grow, ripple deltas) happens on :class:`PlannedShot`
        objects before any store mutation occurs.

        Returns:
            Dict mapping ``step_id`` -> action taken
            (``"created"`` | ``"patched"`` | ``"skipped"``
            | ``"locked"`` | ``"removed"``).
        """
        self._fps_cache = None
        plan = self._compute_plan(
            steps,
            ranges=ranges,
            remove_missing=remove_missing,
            zero_duration_fallback=zero_duration_fallback,
            fit_mode=fit_mode,
            initial_shot_length=initial_shot_length,
        )
        return self._execute_plan(plan, remove_missing=remove_missing)

    # ---- compute-then-commit internals -----------------------------------

    def _compute_plan(
        self,
        steps: List[BuilderStep],
        ranges: Optional[Dict[str, Tuple[float, float]]] = None,
        remove_missing: bool = True,
        zero_duration_fallback: bool = False,
        fit_mode: FitMode = DEFAULT_FIT_MODE,
        initial_shot_length: float = DEFAULT_INITIAL_SHOT_LENGTH,
    ) -> List[PlannedShot]:
        """Pure planning pass: compute final positions without touching the store.

        Reads the current store state once, then builds a list of
        :class:`PlannedShot` objects that describe every mutation
        (create, patch, skip, lock, remove).  All cursor advancement,
        audio-grow, and ripple arithmetic happens here on plan data.

        Returns:
            Ordered list of :class:`PlannedShot` instructions.
        """
        from mayatk.anim_utils.shots.shot_manifest.behaviors import compute_duration

        sorted_shots = self.store.sorted_shots()
        built_map = {s.name: s for s in sorted_shots}
        csv_ids = {step.step_id for step in steps}
        plan: List[PlannedShot] = []

        # Track removals
        if remove_missing:
            for name, shot in list(built_map.items()):
                if name not in csv_ids:
                    dummy_step = BuilderStep(
                        step_id=name,
                        section="",
                        section_title="",
                        description="",
                    )
                    plan.append(
                        PlannedShot(
                            step=dummy_step,
                            action="removed",
                            existing_shot_id=shot.shot_id,
                        )
                    )

        # Cursor for new shots (after all existing shots).
        # We maintain a virtual cursor that advances as we plan
        # new shots, independent of the store.
        cursor = sorted_shots[-1].end if sorted_shots else 1.0

        # Accumulate ripple deltas from audio-grow so downstream
        # planned positions account for earlier expansions.
        cumulative_ripple = 0.0

        for step in steps:
            existing = built_map.get(step.step_id)
            meta = self._step_metadata(
                step, pass_through=getattr(step, "_pass_through", None)
            )

            if existing is None:
                # ---- NEW SHOT ----
                # Fit policy is authoritative for duration.  When a
                # range is provided, ``rng[0]`` supplies placement but
                # ``resolve_duration`` drives the end — so ``fit_mode``
                # and ``initial_shot_length`` always govern new-shot
                # length, even when the slots layer has pre-computed a
                # gap-based range_map.  ``zero_duration_fallback`` is
                # the one opt-out (incremental/selected-keys flows).
                rng = ranges.get(step.step_id) if ranges else None
                if zero_duration_fallback and rng is not None:
                    start = rng[0] + cumulative_ripple
                    end = rng[1] + cumulative_ripple
                elif zero_duration_fallback:
                    start = cursor + cumulative_ripple
                    end = start
                else:
                    adjusted_cursor = cursor + cumulative_ripple
                    if rng is not None:
                        # ``rng[0]`` is the preferred placement, but if
                        # the fit-driven duration would overlap the
                        # previous shot, ripple forward to the cursor.
                        start = max(rng[0] + cumulative_ripple, adjusted_cursor)
                    else:
                        start = adjusted_cursor
                    fps = self._resolve_fps()
                    dur, _beh, _aud = resolve_duration(
                        step,
                        initial_shot_length,
                        fit_mode,
                        fps,
                    )
                    end = start + dur

                scene_objs = [o for o in step.objects if o.kind != "audio"]
                obj_names = [o.name for o in scene_objs]

                fps_for_keys = self._resolve_fps()
                planned_objs = [
                    plan_object_keys(o, start, end, fps_for_keys) for o in step.objects
                ]

                plan.append(
                    PlannedShot(
                        step=step,
                        action="created",
                        start=start,
                        end=end,
                        objects=obj_names,
                        metadata=meta,
                        description=step.display_text,
                        planned_objects=planned_objs,
                    )
                )
                # Advance virtual cursor
                if end == start:
                    cursor = (end - cumulative_ripple) + (
                        self.store.gap if self.store.gap > 0 else 1
                    )
                else:
                    cursor = end - cumulative_ripple
                continue

            # ---- EXISTING SHOT ----
            if existing.locked:
                plan.append(
                    PlannedShot(
                        step=step,
                        action="locked",
                        start=existing.start + cumulative_ripple,
                        end=existing.end + cumulative_ripple,
                        existing_shot_id=existing.shot_id,
                    )
                )
                continue

            # Apply cumulative ripple to existing position
            ex_start = existing.start + cumulative_ripple
            ex_end = existing.end + cumulative_ripple

            # Reposition from user-provided range
            repositioned = False
            rng = ranges.get(step.step_id) if ranges else None
            if rng is not None:
                new_start = rng[0] + cumulative_ripple
                new_end = rng[1] + cumulative_ripple
                if abs(ex_start - new_start) > 1e-6 or abs(ex_end - new_end) > 1e-6:
                    ex_start, ex_end = new_start, new_end
                    repositioned = True

            # Audio-grow: compute whether audio extends the shot
            range_is_noop = rng is None or (
                abs(rng[0] - existing.start) < 1e-6
                and abs(rng[1] - existing.end) < 1e-6
            )
            ripple_delta = 0.0
            new_audio = {o.name for o in step.objects if o.kind == "audio"}
            if range_is_noop and new_audio and not existing.locked:
                audio_objs = [o for o in step.objects if o.kind == "audio"]
                new_dur = compute_duration(audio_objs, fallback=0.0)
                current_dur = ex_end - ex_start
                if new_dur > current_dur + 1e-6:
                    ripple_delta = (ex_start + new_dur) - ex_end
                    ex_end = ex_start + new_dur
                    repositioned = True
                    cumulative_ripple += ripple_delta

            # Diff CSV objects vs previous
            csv_obj_map = {
                o.name: sorted(o.behaviors) for o in step.objects if o.kind != "audio"
            }
            csv_objs = set(csv_obj_map)
            raw_csv = existing.metadata.get("csv_objects", existing.objects)
            old_csv_objs = set(
                (e["name"] if isinstance(e, dict) else e)
                for e in raw_csv
                if not (isinstance(e, dict) and e.get("kind") == "audio")
            )
            scene_discovered = set(existing.objects) - old_csv_objs

            old_behaviors: Dict[str, List[str]] = {}
            for entry in existing.metadata.get("behaviors", []):
                old_behaviors.setdefault(entry["name"], []).append(
                    entry.get("behavior", "")
                )
            for k in old_behaviors:
                old_behaviors[k] = sorted(old_behaviors[k])

            new_objs = csv_objs - old_csv_objs
            changed_beh = {
                name
                for name in csv_objs & old_csv_objs
                if csv_obj_map.get(name, []) != old_behaviors.get(name, [])
            }

            old_audio = {
                e["name"]
                for e in raw_csv
                if isinstance(e, dict) and e.get("kind") == "audio"
            }
            audio_changed = new_audio != old_audio

            has_content_change = bool(
                new_objs or (old_csv_objs - csv_objs) or changed_beh
            )

            if has_content_change or repositioned or audio_changed:
                action: Action = "patched"
            else:
                action = "skipped"

            # Compute merged objects for patched shots
            merged_objects = sorted(csv_objs | scene_discovered)

            fps_for_keys = self._resolve_fps()
            planned_objs = [
                plan_object_keys(o, ex_start, ex_end, fps_for_keys)
                for o in step.objects
            ]

            plan.append(
                PlannedShot(
                    step=step,
                    action=action,
                    start=ex_start,
                    end=ex_end,
                    objects=merged_objects,
                    metadata=meta,
                    description=step.display_text or "",
                    existing_shot_id=existing.shot_id,
                    ripple_delta=ripple_delta,
                    planned_objects=planned_objs,
                )
            )

        return plan

    def _execute_plan(
        self,
        plan: List[PlannedShot],
        remove_missing: bool = True,
    ) -> Dict[str, str]:
        """Commit a build plan to the store in a single pass.

        Applies removals first, then creates/patches in plan order.
        All positional data comes from the plan -- no re-reading of
        the store is needed.

        Returns:
            Dict mapping ``step_id`` -> action string.
        """
        actions: Dict[str, str] = {}

        # Coalesce per-shot mutations into a single flush/save and a
        # single BatchComplete event for UI listeners.
        with self.store.batch_update():
            # Phase 1: removals
            for ps in plan:
                if ps.action == "removed" and ps.existing_shot_id is not None:
                    self.store.remove_shot(ps.existing_shot_id)
                    actions[ps.step.step_id] = "removed"

            # Phase 2: creates / patches / skips / locks (order matters)
            for ps in plan:
                if ps.action == "removed":
                    continue

                if ps.action == "created":
                    self.store.define_shot(
                        name=ps.step.step_id,
                        start=ps.start,
                        end=ps.end,
                        objects=ps.objects,
                        metadata=ps.metadata,
                        description=ps.description,
                    )
                    for n in ps.objects:
                        self.store.set_object_pinned(n)
                    actions[ps.step.step_id] = "created"

                elif ps.action == "locked":
                    # Locked shots are content-protected, but still need
                    # repositioning if an upstream ripple displaced them.
                    if ps.existing_shot_id is not None:
                        existing = self._find_shot(ps.existing_shot_id)
                        if existing and (
                            abs(existing.start - ps.start) > 1e-6
                            or abs(existing.end - ps.end) > 1e-6
                        ):
                            self.store.update_shot(
                                existing.shot_id,
                                start=ps.start,
                                end=ps.end,
                            )
                    actions[ps.step.step_id] = "locked"

                elif ps.action == "skipped":
                    # Still update metadata/description from CSV, and
                    # reposition if an upstream ripple displaced this shot.
                    if ps.existing_shot_id is not None:
                        existing = self._find_shot(ps.existing_shot_id)
                        if existing:
                            if (
                                abs(existing.start - ps.start) > 1e-6
                                or abs(existing.end - ps.end) > 1e-6
                            ):
                                self.store.update_shot(
                                    existing.shot_id,
                                    start=ps.start,
                                    end=ps.end,
                                )
                            existing.metadata = ps.metadata
                            existing.description = ps.description
                    actions[ps.step.step_id] = "skipped"

                elif ps.action == "patched":
                    if ps.existing_shot_id is not None:
                        existing = self._find_shot(ps.existing_shot_id)
                        if existing:
                            # Apply absolute position from plan — no
                            # ripple_shift needed because the plan already
                            # computed final positions for every shot.
                            if (
                                abs(existing.start - ps.start) > 1e-6
                                or abs(existing.end - ps.end) > 1e-6
                            ):
                                self.store.update_shot(
                                    existing.shot_id,
                                    start=ps.start,
                                    end=ps.end,
                                )
                            # Update metadata, description, objects
                            existing.metadata = ps.metadata
                            existing.description = ps.description

                            # Resolve long names and filter scene-discovered
                            from mayatk.anim_utils.shots._shots import (
                                _resolve_long_names,
                            )

                            csv_objs = {
                                o.name for o in ps.step.objects if o.kind != "audio"
                            }
                            scene_objs = set(ps.objects) - csv_objs
                            if scene_objs:
                                scene_objs = set(
                                    self._filter_to_animated(
                                        sorted(scene_objs), ps.start, ps.end
                                    )
                                )
                            merged = sorted(csv_objs | scene_objs)
                            resolved = _resolve_long_names(merged)
                            existing.objects = resolved if resolved else merged

                            for n in csv_objs:
                                self.store.set_object_pinned(n)

                    actions[ps.step.step_id] = "patched"

        return actions

    def _find_shot(self, shot_id: int):
        """Return the ShotBlock with *shot_id*, or None."""
        for s in self.store.shots:
            if s.shot_id == shot_id:
                return s
        return None

    def _resolve_fps(self) -> float:
        """Return scene FPS, or 24 when Maya is unavailable.

        Cached per instance; cleared at the top of ``update`` so a
        single build call queries ``cmds.currentUnit`` once instead of
        twice per shot.
        """
        if self._fps_cache is not None:
            return self._fps_cache
        try:
            from mayatk.audio_utils._audio_utils import AudioUtils as _AU

            self._fps_cache = float(_AU.get_fps())
        except Exception:
            self._fps_cache = 24.0
        return self._fps_cache

    def build_plan(
        self,
        steps: List[BuilderStep],
        ranges: Optional[Dict[str, Tuple[float, float]]] = None,
        remove_missing: bool = True,
        zero_duration_fallback: bool = False,
        fit_mode: FitMode = DEFAULT_FIT_MODE,
        initial_shot_length: float = DEFAULT_INITIAL_SHOT_LENGTH,
    ) -> BuildPlan:
        """Return a :class:`BuildPlan` without committing anything.

        Public plan-inspection API — useful for previews, diffing, and
        the test harness.  Calling :meth:`update` or :meth:`sync`
        afterwards recomputes an equivalent plan internally.
        """
        self._fps_cache = None
        shots = self._compute_plan(
            steps,
            ranges=ranges,
            remove_missing=remove_missing,
            zero_duration_fallback=zero_duration_fallback,
            fit_mode=fit_mode,
            initial_shot_length=initial_shot_length,
        )
        return BuildPlan(
            shots=shots,
            fit_mode=fit_mode,
            initial_shot_length=initial_shot_length,
            fps=self._resolve_fps(),
        )

    # ---- assess ----------------------------------------------------------

    def assess(
        self,
        steps: List[BuilderStep],
        exists_fn: Optional[Callable[[str], bool]] = None,
        verify_fn: Optional[Callable] = None,
        keyframe_range_fn: Optional[
            Callable[[str], Optional[Tuple[float, float]]]
        ] = None,
        audio_exists_fn: Optional[Callable[[str], bool]] = None,
        skip_scene_discovery: bool = False,
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
            audio_exists_fn: Callable that returns ``True`` when an
                audio DG node with the given name exists.  Defaults to
                ``bool(cmds.ls(name, type='audio'))``.

        Returns:
            One :class:`StepStatus` per step with per-object results.
        """
        if exists_fn is None:
            import maya.cmds as _cmds

            exists_fn = _cmds.objExists

        if verify_fn is None:
            from mayatk.anim_utils.shots.shot_manifest.behaviors import verify_behavior

            verify_fn = lambda obj, beh, s, e: verify_behavior(obj, beh, s, e)

        if audio_exists_fn is None:
            audio_exists_fn = self._default_audio_exists

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

            # Locked shots are user-finalized â€” skip detailed checking
            if is_locked:
                obj_statuses = [
                    ObjectStatus(
                        name=o.name,
                        exists=True,
                        status="valid",
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
                if obj.kind == "audio":
                    exists = audio_exists_fn(obj.name)
                    broken = []
                    if not exists:
                        status = "missing_object"
                    elif built and obj.behaviors:
                        broken = [
                            b
                            for b in obj.behaviors
                            if not verify_fn(obj.name, b, shot.start, shot.end)
                        ]
                        status = "missing_behavior" if broken else "valid"
                    else:
                        status = "valid" if exists else "missing_object"
                    obj_statuses.append(
                        ObjectStatus(
                            name=obj.name,
                            exists=exists,
                            status=status,
                            behaviors=list(obj.behaviors),
                            broken_behaviors=broken,
                        )
                    )
                    continue
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
                from mayatk.anim_utils.shots.shot_manifest.manifest_data import (
                    short_name as _short,
                )

                csv_short = {_short(o.name) for o in step.objects}
                stored_extra = [n for n in shot.objects if _short(n) not in csv_short]
                # Filter stored extras to only those with actual motion
                # (removes flat-key objects from previous builds).
                if stored_extra:
                    stored_extra = self._filter_to_animated(
                        stored_extra, shot.start, shot.end
                    )
                additional = stored_extra
                # Also discover scene objects with keys in this shot's
                # range that aren't tracked in the CSV or the store.
                # Skip in selected-keys mode: only the explicitly
                # selected keys' objects are relevant.
                if not skip_scene_discovery:
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

        # Build (and cache) the map: transform â†’ [standard-attr curves]
        animated = getattr(self, "_animated_transforms", None)
        if animated is None:
            animated = _map_standard_curves_to_transforms()
            self._animated_transforms = animated

        found: list = []
        from mayatk.anim_utils.shots.shot_manifest.manifest_data import (
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
    def _default_audio_exists(name: str) -> bool:
        """Return True if *name* is either a registered audio_clips track
        on the canonical carrier, or an audio DG node in the scene.

        The audio_clips workflow registers tracks (attr + file_map) before
        any DG node exists — DG nodes are produced lazily by the compositor
        from keyed start frames.  Checking only for DG nodes would flag
        loaded-but-unkeyed tracks as missing and block the manifest build
        that is supposed to key them.
        """
        try:
            import maya.cmds as cmds

            try:
                track_id = AudioUtils.normalize_track_id(name)
                if AudioUtils.has_track(track_id):
                    return True
            except Exception:
                pass

            matches = cmds.ls(name, type="audio") or []
            if len(matches) > 1:
                log.warning(
                    "Multiple audio nodes match '%s': %s — using first.",
                    name,
                    matches,
                )
            return bool(matches)
        except Exception:
            return False

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
        post_process: Optional[Callable[[BuilderStep], None]] = None,
    ) -> Tuple["ShotManifest", List[BuilderStep]]:
        """Convenience: parse a CSV and return a ready-to-build engine.

        Parameters:
            filepath: Path to the CSV file.
            store: Optional existing ``ShotStore`` to populate.
                If ``None``, a fresh instance is created.
            columns: Column index mapping.
            post_process: Optional callable invoked on each step after
                assembly.

        Returns:
            ``(builder, steps)`` tuple. Call ``builder.sync(steps)`` to
            execute.
        """
        steps = parse_csv(filepath, columns, post_process=post_process)
        st = store or ShotStore.active()
        builder = ShotManifest(st)
        return builder, steps
