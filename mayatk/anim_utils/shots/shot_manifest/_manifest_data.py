# !/usr/bin/python
# coding=utf-8
"""Constants, column layout, and pure helper functions for the Shot Manifest UI."""
from typing import Dict, List, Optional, Tuple, Union

from mayatk.anim_utils.shots._shots import SHOT_PALETTE

# QSettings namespace
SETTINGS_NS = "ShotManifest"
_SETTINGS_NS_COLORS = "ShotManifest/colors"

# Column headers for the manifest tree widget
HEADERS = ["Step", "Section", "Description", "Behaviors", "Start", "End"]

# Fixed column indices for the unified 6-column layout
COL_STEP = 0
COL_SECTION = 1
COL_DESC = 2  # parent: description, child: object name
COL_BEHAVIORS = 3
COL_START = 4
COL_END = 5

# Foreground colors for behavior names on child rows (pastel)
BEHAVIOR_COLORS = {
    "fade_in": ("#8ECFBF", None),  # soft teal
    "fade_out": ("#E0B880", None),  # soft amber
}

STEP_ICON_COLOR = "#8E8E8E"  # neutral dark grey for parent step rows

# Assessment status colours — shared palette from _shots (single source of truth).
PASTEL_STATUS = SHOT_PALETTE

# Derived from the palette — used for footer error labels
ERROR_COLOR = PASTEL_STATUS["error"][0]

# Assessment status vocabulary — shared by assess(), _reconstruct_assessment(),
# and _table_presenter so that status strings are defined once.
STATUS_VALID = "valid"
STATUS_MISSING_OBJECT = "missing_object"
STATUS_MISSING_BEHAVIOR = "missing_behavior"
STATUS_USER_ANIMATED = "user_animated"
STATUS_ADDITIONAL = "additional"
STATUS_MISSING_SHOT = "missing_shot"
STATUS_LOCKED = "locked"

# ---------------------------------------------------------------------------
# Customisable color mapping (user-overridable via ColorMappingEditor)
# ---------------------------------------------------------------------------

ColorValue = Union[str, Tuple[str, str]]

_DEFAULT_MANIFEST_COLORS: Dict[str, ColorValue] = {
    # Status indicators — (fg, bg) pairs
    "info": ("#88B8D0", "#28323D"),
    "warn": ("#D4B878", "#737350"),
    "error": ("#D4908F", "#735050"),
    "locked": "#888888",
    # Behavior labels — fg only
    "fade_in": "#8ECFBF",
    "fade_out": "#E0B880",
    # Icons
    "step_icon": "#8E8E8E",
}

_MANIFEST_COLOR_SECTIONS = [
    ("Status Indicators", ["info", "warn", "error", "locked"]),
    ("Behaviors", ["fade_in", "fade_out"]),
    ("Display", ["step_icon"]),
]

# Status aliases — domain names that map to base status colors
_STATUS_ALIASES = {
    "csv_object": "valid",
    "scene_discovered": "info",
    "missing_object": "error",
    "missing_behavior": "warn",
    "user_animated": "info",
    "additional": "warn",
    "collision": "error",
    "missing_shot": "info",
}


def load_manifest_colors() -> Dict[str, ColorValue]:
    """Return the persisted manifest color map without opening a dialog.

    Reads from QSettings (``ShotManifest/colors`` namespace) and falls
    back to :data:`_DEFAULT_MANIFEST_COLORS` for missing keys.
    """
    from uitk.widgets.mixins.settings_manager import SettingsManager

    sm = SettingsManager(namespace=_SETTINGS_NS_COLORS)
    result: Dict[str, ColorValue] = {}
    for key, default in _DEFAULT_MANIFEST_COLORS.items():
        if isinstance(default, tuple):
            fg = sm.value(f"{key}/fg") or default[0]
            bg = sm.value(f"{key}/bg") or default[1]
            result[key] = (fg, bg)
        else:
            val = sm.value(key)
            result[key] = val if val else default
    return result


def build_status_palette(cmap: Dict[str, ColorValue]) -> dict:
    """Derive a status-lookup dict from a color map.

    Returns a dict compatible with ``PASTEL_STATUS`` usage::

        fg, bg = palette["collision"]  # resolves via alias → "error"
    """
    base = {
        "valid": (None, None),
        "locked": (cmap.get("locked", "#888888"), None),
        "info": cmap.get("info", ("#88B8D0", "#28323D")),
        "warn": cmap.get("warn", ("#D4B878", "#3D3528")),
        "error": cmap.get("error", ("#D4908F", "#3D2828")),
    }
    palette = dict(base)
    for alias, target in _STATUS_ALIASES.items():
        palette[alias] = base[target]
    return palette


def fmt_behavior(name: str) -> str:
    """``'fade_in'`` → ``'Fade In'``."""
    return name.replace("_", " ").title() if name else ""


def unfmt_behavior(display: str) -> str:
    """``'Fade In'`` → ``'fade_in'``."""
    return display.strip().lower().replace(" ", "_") if display else ""


def format_behavior_html(behaviors, behavior_colors=None) -> str:
    """Return rich-text HTML for a list of behavior names."""
    if not behaviors:
        return ""
    colors = behavior_colors if behavior_colors is not None else BEHAVIOR_COLORS
    spans = []
    for b in behaviors:
        display = fmt_behavior(b)
        color = colors.get(b, (None, None))[0]
        if color:
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
