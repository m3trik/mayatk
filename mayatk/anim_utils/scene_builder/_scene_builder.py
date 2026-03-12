# coding=utf-8
"""Scene Builder — parse structured CSVs and build sequencer scenes.

Reads a CSV with section/step structure, auto-detects object behaviors
from textual descriptions, and builds scenes in a
:class:`~mayatk.anim_utils.sequencer._sequencer.Sequencer`.
"""
import csv
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from mayatk.anim_utils.sequencer._sequencer import Sequencer

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
    """One step (= one future sequencer scene)."""

    step_id: str  # e.g. "A04"
    section: str  # e.g. "A"
    section_title: str  # e.g. "AILERON RIGGING"
    content: str  # merged step-contents text
    objects: List[BuilderObject] = field(default_factory=list)


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
    """Maps logical fields to CSV column indices.

    Adjust these to match different CSV layouts.
    """

    step_id: int = 0
    content: int = 4
    asset_name: int = 5


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
        columns: Optional column index mapping.  Defaults to the standard
            C-5M layout.

    Returns:
        Ordered list of steps, each carrying its objects and detected
        behaviors.
    """
    cols = columns or ColumnMap()
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

            # --- column header row (skip) ---
            if first.lower() == "step":
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
# Engine
# ---------------------------------------------------------------------------


class SceneBuilder:
    """Creates sequencer scenes from parsed steps and applies behaviors.

    Parameters:
        sequencer: Target ``Sequencer`` instance to populate.
        step_duration: Default number of frames per step.
        gap: Frames of gap between consecutive scenes.
        start_frame: Timeline frame at which the first scene begins.
    """

    def __init__(
        self,
        sequencer: Sequencer,
        step_duration: float = 30,
        gap: float = 0,
        start_frame: float = 1,
    ):
        self.sequencer = sequencer
        self.step_duration = step_duration
        self.gap = gap
        self.start_frame = start_frame

    def preview(self, steps: List[BuilderStep]) -> List[dict]:
        """Return planned scene layout without mutating anything.

        Each dict has ``step_id``, ``start``, ``end``, and ``objects``
        (list of ``{"name": str, "behavior": str}`` dicts).
        """
        result = []
        cursor = self.start_frame
        for step in steps:
            start = cursor
            end = start + self.step_duration
            result.append(
                {
                    "step_id": step.step_id,
                    "section": step.section,
                    "start": start,
                    "end": end,
                    "objects": [
                        {"name": o.name, "behavior": o.behavior} for o in step.objects
                    ],
                }
            )
            cursor = end + self.gap
        return result

    def build(self, steps: List[BuilderStep], apply_behaviors: bool = True) -> None:
        """Populate the sequencer with one scene per step.

        Scenes are laid out sequentially with uniform duration.

        Parameters:
            steps: Parsed steps to build.
            apply_behaviors: If True (default), detected behaviors are
                applied to Maya objects via
                :func:`~mayatk.anim_utils.behavior_keys.apply_behavior`.
                Set to False for data-only population (no Maya required).
        """
        if apply_behaviors:
            from mayatk.anim_utils.behavior_keys import apply_behavior

        cursor = self.start_frame

        for step in steps:
            start = cursor
            end = start + self.step_duration
            obj_names = [o.name for o in step.objects]

            self.sequencer.define_scene(
                name=step.step_id,
                start=start,
                end=end,
                objects=obj_names,
            )

            if apply_behaviors:
                for obj in step.objects:
                    if obj.behavior:
                        apply_behavior(obj.name, obj.behavior, start, end)

            cursor = end + self.gap

    @staticmethod
    def from_csv(
        filepath: str,
        sequencer: Optional[Sequencer] = None,
        step_duration: float = 30,
        gap: float = 0,
        start_frame: float = 1,
        columns: Optional[ColumnMap] = None,
    ) -> Tuple["SceneBuilder", List[BuilderStep]]:
        """Convenience: parse a CSV and return a ready-to-build engine.

        Parameters:
            filepath: Path to the CSV file.
            sequencer: Optional existing ``Sequencer`` to populate.
                If ``None``, a fresh instance is created.
            step_duration: Default frames per step.
            gap: Frames between consecutive scenes.
            start_frame: Timeline frame for the first scene.
            columns: Column index mapping.

        Returns:
            ``(builder, steps)`` tuple. Call ``builder.build(steps)`` to
            execute.
        """
        steps = parse_csv(filepath, columns)
        seq = sequencer or Sequencer()
        builder = SceneBuilder(
            seq,
            step_duration=step_duration,
            gap=gap,
            start_frame=start_frame,
        )
        return builder, steps
