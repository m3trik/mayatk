# !/usr/bin/python
# coding=utf-8
"""Constants, column layout, and pure helper functions for the Shot Manifest UI.

Shared text: blendertk carries this module identical
(``m3trik/scripts/check_dcc_twins.py``); mayatk's is the one edited, then
copied over.  Only ``manifest_host.py`` differs between the two.
"""

from pythontk.core_utils.engines.shots.manifest.range_resolver import (  # noqa: F401
    RangeResolver as _PyRangeResolver,
)

# canonical home is the engine class; re-exported under the historical name for callers
prune_to_top_boundaries = _PyRangeResolver.prune_to_top_boundaries  # noqa: F401

from pythontk import SHOT_PALETTE  # noqa: E402

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

# Assessment status colours — the pythontk engine's palette (single source of truth).
PASTEL_STATUS = SHOT_PALETTE

# Foreground colors for behavior issue states on child rows.
# Valid behaviors are rendered without color.
BEHAVIOR_STATUS_COLORS = {
    "missing": PASTEL_STATUS["missing_behavior"][0],  # warn gold
    "error": PASTEL_STATUS["missing_object"][0],  # error red
    "stale": PASTEL_STATUS["stale_behavior"][0],  # keyed under an older recipe
}

# Derived from the palette — used for footer error labels
ERROR_COLOR = PASTEL_STATUS["error"][0]


class ManifestData:
    """ManifestData — module namespace."""

    @staticmethod
    def fmt_behavior(name: str) -> str:
        """``'fade_in'`` → ``'Fade In'``."""
        return name.replace("_", " ").title() if name else ""

    @staticmethod
    def format_behavior_html(
        behaviors, broken=(), status_color=None, stale=(), disabled=()
    ) -> str:
        """Return rich-text HTML for a list of behavior names.

        Parameters:
            behaviors: Sequence of raw behavior names to display.
            broken: Subset of *behaviors* that failed verification.
                These are rendered with the ``missing_behavior`` palette
                colour; the rest are left uncoloured.
            status_color: Optional override colour applied to *all* behaviours.
                When set, *broken* is ignored and every behaviour is rendered
                in this colour (e.g. the error colour for missing objects).
            stale: Subset of *behaviors* keyed under an older effect recipe
                (Build re-keys them), rendered in the ``stale`` colour.
            disabled: Behavior types turned off (header menu): struck through
                in the ``locked`` grey, whatever else applies.
        """
        if not behaviors:
            return ""
        spans = []
        off = set(disabled)
        if off:
            grey = PASTEL_STATUS["locked"][0]
            on = [b for b in behaviors if b not in off]
            html = ManifestData.format_behavior_html(on, broken, status_color, stale)
            struck = [
                f'<span style="color:{grey};text-decoration:line-through">'
                f"{ManifestData.fmt_behavior(b)}</span>"
                for b in behaviors
                if b in off
            ]
            return "  ".join(([html] if html else []) + struck)
        if status_color:
            for b in behaviors:
                display = ManifestData.fmt_behavior(b)
                spans.append(f'<span style="color:{status_color}">{display}</span>')
        else:
            broken_set, stale_set = set(broken), set(stale)
            for b in behaviors:
                display = ManifestData.fmt_behavior(b)
                if b in broken_set:
                    color = BEHAVIOR_STATUS_COLORS.get("missing")
                    spans.append(f'<span style="color:{color}">{display}</span>')
                elif b in stale_set:
                    color = BEHAVIOR_STATUS_COLORS.get("stale")
                    spans.append(f'<span style="color:{color}">{display}</span>')
                else:
                    spans.append(display)
        return "  ".join(spans)
