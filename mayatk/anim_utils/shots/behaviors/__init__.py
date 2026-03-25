# coding=utf-8
"""Behaviors — load and apply YAML keying recipes.

A behavior template defines attribute keyframe patterns (e.g. fade-in,
fade-out) anchored to a time range's start or end.  Shared across all
tools in the ``shots`` subpackage.
"""
import functools
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

try:
    import pymel.core as pm
except ImportError:
    pm = None  # type: ignore[assignment]

_BEHAVIORS_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_behavior(name: str, search_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load a YAML behavior template by stem name.

    Results are cached per ``(name, search_path)`` pair so repeated
    lookups (e.g. many objects sharing the same behavior within one
    build) avoid redundant disk I/O and YAML parsing.

    Parameters:
        name: Template name without extension (e.g. ``"fade_in"``).
        search_path: Directory to search. Defaults to the built-in
            ``behaviors/`` directory next to this module.

    Returns:
        Parsed YAML dict.

    Raises:
        FileNotFoundError: If the template file does not exist.
    """
    return _load_behavior_cached(name, search_path or _BEHAVIORS_DIR)


@functools.lru_cache(maxsize=32)
def _load_behavior_cached(name: str, base: Path) -> Dict[str, Any]:
    """Internal cached loader — arguments must be hashable."""
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("PyYAML is required for behavior templates") from exc

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

    When the object has an ``opacity`` attribute (from :class:`RenderOpacity`),
    this function automatically handles dual-keying:

    - If the template targets ``visibility`` and the object has ``opacity``,
      the value is keyed on **both** ``opacity`` and ``visibility``.
    - If the template targets ``opacity`` directly, ``visibility`` is also
      mirrored automatically.

    This produces real animation curves on both channels so FBX export
    gives game engines a native ``visibility`` track without baking, while
    the ``opacity`` channel is available for engines that support it.

    Parameters:
        obj: Maya node name.
        behavior_name: YAML template stem name (e.g. ``"fade_in"``).
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
    has_opacity = node.hasAttr("opacity")

    for attr_name, attr_def in template.get("attributes", {}).items():
        if attrs and attr_name not in attrs:
            continue

        # Determine target attribute and whether to mirror to visibility.
        # When the template targets visibility and the object has opacity,
        # key opacity instead (smooth channel) and mirror to visibility
        # (so FBX contains a real visibility curve for game engines).
        target_attr = attr_name
        mirror_to_vis = False

        if attr_name == "visibility" and has_opacity:
            target_attr = "opacity"
            mirror_to_vis = True
        elif attr_name == "opacity" and has_opacity:
            mirror_to_vis = True

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
                    attribute=target_attr,
                    time=k["time"],
                    value=k["value"],
                    inTangentType=k["tangent"],
                    outTangentType=k["tangent"],
                )
                # Mirror: set a matching visibility keyframe so FBX
                # export produces a real visibility animation curve.
                if mirror_to_vis:
                    pm.setKeyframe(
                        node,
                        attribute="visibility",
                        time=k["time"],
                        value=k["value"],
                        inTangentType=k["tangent"],
                        outTangentType=k["tangent"],
                    )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_behavior(
    obj: str,
    behavior_name: str,
    start: float,
    end: float,
    search_path: Optional[Path] = None,
    keyframe_fn: Optional[Any] = None,
) -> bool:
    """Check whether expected behavior keyframes exist on an object.

    Parameters:
        obj: Maya node name.
        behavior_name: YAML template stem name (e.g. ``"fade_in"``).
        start: First frame of the scene range.
        end: Last frame of the scene range.
        search_path: Optional custom behaviors directory.
        keyframe_fn: Callable ``(obj, attribute, time) -> list``.
            Defaults to ``pm.keyframe(obj, q=True, at=attr, time=(t, t))``.

    Returns:
        ``True`` if every expected keyframe is found.
    """
    template = load_behavior(behavior_name, search_path)

    if keyframe_fn is None:
        try:
            import maya.cmds as _cmds
        except ImportError:
            if pm is None:
                raise RuntimeError("Maya is required to verify behaviors")
            keyframe_fn = lambda o, attr, t: pm.keyframe(
                o, q=True, at=attr, time=(t, t)
            )
        else:
            keyframe_fn = lambda o, attr, t: _cmds.keyframe(
                o, q=True, at=attr, time=(t, t)
            )

    for attr_name, attr_def in template.get("attributes", {}).items():
        for phase in ("in", "out"):
            block = attr_def.get(phase)
            if not block:
                continue
            if "anchor" not in block:
                block = dict(block, anchor="start" if phase == "in" else "end")
            keys = resolve_keys(block, start, end)
            for k in keys:
                result = keyframe_fn(obj, attr_name, k["time"])
                if not result:
                    return False
    return True


# ---------------------------------------------------------------------------
# Duration computation
# ---------------------------------------------------------------------------


def compute_duration(
    behavior_entries: List[Dict[str, str]],
    fallback: float = 30,
) -> float:
    """Derive duration from the behavior templates referenced in *behavior_entries*.

    For each entry, the durations of all its behaviors are summed
    (since all get applied to the same object).  The result is the
    maximum across all entries.

    Parameters:
        behavior_entries: List of dicts with a ``"behavior"``
            key, or ``BuilderObject``-like objects with a
            ``.behaviors`` list attribute.
        fallback: Duration when no behavior-driven duration exists.

    Returns:
        Duration in frames.
    """
    max_dur = 0.0
    has_any = False
    for entry in behavior_entries:
        # Support both dict format {"behavior": "name"} and
        # BuilderObject with .behaviors list
        if isinstance(entry, dict):
            behaviors = [entry.get("behavior", "")]
        else:
            behaviors = getattr(entry, "behaviors", [])
        obj_total = 0.0
        for behavior in behaviors:
            if not behavior:
                continue
            try:
                tmpl = load_behavior(behavior)
            except FileNotFoundError:
                continue
            has_any = True
            for _attr_name, attr_def in tmpl.get("attributes", {}).items():
                for phase in ("in", "out"):
                    block = attr_def.get(phase)
                    if block:
                        obj_total += block.get("duration", 0)
        if obj_total > max_dur:
            max_dur = obj_total
    if not has_any:
        return fallback
    return max_dur


# ---------------------------------------------------------------------------
# Batch application
# ---------------------------------------------------------------------------


def apply_to_shots(
    shots: list,
    apply_fn,
    exists_fn=None,
    has_keys_fn=None,
) -> Dict[str, list]:
    """Apply declared behaviors from shot metadata to Maya objects.

    Reads ``metadata["behaviors"]`` from each shot and applies keyframe
    patterns via *apply_fn*.  Objects with existing keyframes in the
    shot range are skipped to avoid overwriting user animation.

    Parameters:
        shots: :class:`ShotBlock` instances to process.
        apply_fn: Callable ``(obj, behavior, start, end)`` that applies
            a behavior template.
        exists_fn: Callable ``(name) -> bool`` that checks whether an
            object exists in the scene.  Defaults to
            ``pymel.core.objExists``.
        has_keys_fn: Callable ``(obj, start, end) -> bool``.  Defaults
            to checking keyframes in range via ``pm.keyframe``.

    Returns:
        Dict with ``"applied"`` and ``"skipped"`` lists of dicts
        containing ``object``, ``behavior``, and ``shot`` keys.
    """
    if exists_fn is None:
        if pm is None:
            raise RuntimeError("Maya is required to apply behaviors")
        exists_fn = pm.objExists

    if has_keys_fn is None:

        def has_keys_fn(obj_name, start, end):
            if pm is None:
                return False
            try:
                keys = pm.keyframe(obj_name, q=True, time=(start, end), tc=True)
                return bool(keys)
            except Exception:
                return False

    applied: list = []
    skipped: list = []
    for shot in shots:
        if shot.locked:
            continue
        for entry in shot.metadata.get("behaviors", []):
            obj_name = entry.get("name", "")
            behavior = entry.get("behavior", "")
            if not behavior or not obj_name:
                continue
            if not exists_fn(obj_name):
                continue
            if has_keys_fn(obj_name, shot.start, shot.end):
                skipped.append(
                    {"object": obj_name, "behavior": behavior, "shot": shot.name}
                )
                continue
            apply_fn(obj_name, behavior, shot.start, shot.end)
            applied.append(
                {"object": obj_name, "behavior": behavior, "shot": shot.name}
            )

    return {"applied": applied, "skipped": skipped}
