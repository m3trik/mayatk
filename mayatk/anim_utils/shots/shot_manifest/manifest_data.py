# !/usr/bin/python
# coding=utf-8
"""Constants, column layout, and pure helper functions for the Shot Manifest UI."""
from typing import List, Optional, Tuple

from mayatk.anim_utils.shots._shots import SHOT_PALETTE

# QSettings namespace
SETTINGS_NS = "ShotManifest"

# Column headers for the manifest tree widget
HEADERS = ["Step", "Section", "Description", "Behaviors", "Start", "End"]

# Fixed column indices for the unified 6-column layout
COL_STEP = 0
COL_SECTION = 1
COL_DESC = 2  # parent: description, child: object name
COL_BEHAVIORS = 3
COL_START = 4
COL_END = 5

STEP_ICON_COLOR = "#8E8E8E"  # neutral dark grey for parent step rows

# Assessment status colours — shared palette from _shots (single source of truth).
PASTEL_STATUS = SHOT_PALETTE

# Foreground colors for behavior issue states on child rows.
# Valid behaviors are rendered without color.
BEHAVIOR_STATUS_COLORS = {
    "missing": PASTEL_STATUS["missing_behavior"][0],  # warn gold
    "error": PASTEL_STATUS["missing_object"][0],  # error red
}

# Derived from the palette — used for footer error labels
ERROR_COLOR = PASTEL_STATUS["error"][0]


def fmt_behavior(name: str) -> str:
    """``'fade_in'`` → ``'Fade In'``."""
    return name.replace("_", " ").title() if name else ""


def unfmt_behavior(display: str) -> str:
    """``'Fade In'`` → ``'fade_in'``."""
    return display.strip().lower().replace(" ", "_") if display else ""


def short_name(dag_path: str) -> str:
    """Return the leaf node name from a Maya DAG path.

    ``"|group1|subgrp|mesh"`` → ``"mesh"``.
    Already-short names pass through unchanged.
    """
    return dag_path.rsplit("|", 1)[-1] if dag_path else ""


def format_behavior_html(behaviors, broken=(), status_color=None) -> str:
    """Return rich-text HTML for a list of behavior names.

    Parameters:
        behaviors: Sequence of raw behavior names to display.
        broken: Subset of *behaviors* that failed verification.
            These are rendered with the ``missing_behavior`` palette
            colour; the rest are left uncoloured.
        status_color: Optional override colour applied to *all* behaviours.
            When set, *broken* is ignored and every behaviour is rendered
            in this colour (e.g. the error colour for missing objects).
    """
    if not behaviors:
        return ""
    spans = []
    if status_color:
        for b in behaviors:
            display = fmt_behavior(b)
            spans.append(f'<span style="color:{status_color}">{display}</span>')
    else:
        broken_set = set(broken)
        for b in behaviors:
            display = fmt_behavior(b)
            if b in broken_set:
                color = BEHAVIOR_STATUS_COLORS.get("missing")
                spans.append(f'<span style="color:{color}">{display}</span>')
            else:
                spans.append(display)
    return "  ".join(spans)


def parse_range(raw: str) -> Optional[Tuple[float, Optional[float]]]:
    """Parse a range string without storing it.

    Accepts ``"120"`` (start only) or ``"120-250"`` / ``"120\u2013250"``.
    Returns ``(start, end_or_None)`` on success, ``None`` on parse failure.
    """
    raw = raw.replace("\u2013", "-")  # en-dash to hyphen
    parts = [p.strip() for p in raw.split("-", 1)]
    try:
        start = float(parts[0])
    except (ValueError, IndexError):
        return None
    end: Optional[float] = None
    if len(parts) == 2 and parts[1]:
        try:
            end = float(parts[1])
        except ValueError:
            pass
    return (start, end)


def try_load_maya_icons():
    """Return the :class:`NodeIcons` class if Maya is available, else ``None``."""
    try:
        from mayatk.ui_utils.node_icons import NodeIcons
        import maya.cmds as cmds  # noqa: F401 — availability check
    except ImportError:
        return None
    return NodeIcons


def prune_to_top_boundaries(region_starts: List[float], n_steps: int) -> List[float]:
    """Keep only *n_steps* region starts by selecting the largest gaps.

    Picks the *n_steps - 1* largest consecutive differences in
    *region_starts* as the primary shot boundaries, then returns
    the first region plus the region after each selected boundary.
    """
    if len(region_starts) <= n_steps:
        return region_starts
    diffs = [
        (region_starts[i + 1] - region_starts[i], i)
        for i in range(len(region_starts) - 1)
    ]
    diffs.sort(key=lambda x: -x[0])
    top_indices = sorted(d[1] for d in diffs[: n_steps - 1])
    selected = [region_starts[0]]
    for idx in top_indices:
        selected.append(region_starts[idx + 1])
    return selected
