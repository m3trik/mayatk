# coding=utf-8
"""Behavior Keys — load and apply YAML keying recipes.

A behavior template defines attribute keyframe patterns (e.g. fade-in,
fade-out) anchored to a time range's start or end.  This module is a
standalone utility in ``anim_utils``, usable by any consumer — the scene
builder, scripting, or the sequencer itself.
"""
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

try:
    import pymel.core as pm
except ImportError:
    pm = None  # type: ignore[assignment]

_BEHAVIORS_DIR = Path(__file__).parent / "behaviors"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_behavior(name: str, search_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load a YAML behavior template by stem name.

    Parameters:
        name: Template name without extension (e.g. ``"fade_in_out"``).
        search_path: Directory to search. Defaults to the built-in
            ``behaviors/`` directory next to this module.

    Returns:
        Parsed YAML dict.

    Raises:
        FileNotFoundError: If the template file does not exist.
    """
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML is required for behavior templates") from exc

    base = search_path or _BEHAVIORS_DIR
    path = base / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Behavior template not found: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def list_behaviors(search_path: Optional[Path] = None) -> List[str]:
    """Return stem names of all available behavior templates.

    Parameters:
        search_path: Directory to scan. Defaults to the built-in
            ``behaviors/`` directory.
    """
    base = search_path or _BEHAVIORS_DIR
    if not base.is_dir():
        return []
    return sorted(p.stem for p in base.glob("*.yaml"))


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------


def resolve_keys(
    block_def: Dict,
    start: float,
    end: float,
) -> List[Dict[str, Any]]:
    """Resolve an ``in`` or ``out`` block to absolute keyframe dicts.

    Parameters:
        block_def: Dict with ``offset``, ``duration``, ``values``,
            and optionally ``tangent`` and ``anchor`` (``"start"`` or
            ``"end"``).
        start: First frame of the target range.
        end: Last frame of the target range.

    Returns:
        List of ``{"time": float, "value": float, "tangent": str}`` dicts.
    """
    anchor = block_def.get("anchor", "start")
    offset = block_def.get("offset", 0)
    dur = block_def.get("duration", 0)
    values = block_def.get("values", [])
    tangent = block_def.get("tangent", "linear")

    if anchor == "end":
        base = end - dur - offset
    else:
        base = start + offset

    n = len(values)
    keys = []
    for i, v in enumerate(values):
        t = base + (dur * i / max(n - 1, 1))
        keys.append({"time": t, "value": v, "tangent": tangent})
    return keys


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


def apply_behavior(
    obj: str,
    behavior_name: str,
    start: float,
    end: float,
    attrs: Optional[List[str]] = None,
    search_path: Optional[Path] = None,
) -> None:
    """Apply a named behavior template to an object over a time range.

    Parameters:
        obj: Maya node name.
        behavior_name: YAML template stem name (e.g. ``"fade_in_out"``).
        start: First frame of the range.
        end: Last frame of the range.
        attrs: If given, only key these attributes. Otherwise key all
            attributes defined in the template.
        search_path: Optional custom behaviors directory.
    """
    if pm is None:
        raise RuntimeError("Maya (pymel) is required to apply behaviors")

    template = load_behavior(behavior_name, search_path)
    node = pm.PyNode(obj)

    for attr_name, attr_def in template.get("attributes", {}).items():
        if attrs and attr_name not in attrs:
            continue

        for phase in ("in", "out"):
            block = attr_def.get(phase)
            if not block:
                continue

            # Use anchor from YAML; fall back to phase-based default
            # for backward compatibility with templates that omit it.
            if "anchor" not in block:
                block = dict(block, anchor="start" if phase == "in" else "end")

            keys = resolve_keys(block, start, end)
            for k in keys:
                pm.setKeyframe(
                    node,
                    attribute=attr_name,
                    time=k["time"],
                    value=k["value"],
                    inTangentType=k["tangent"],
                    outTangentType=k["tangent"],
                )
