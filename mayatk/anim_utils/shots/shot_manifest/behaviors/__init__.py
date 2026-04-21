# coding=utf-8
"""Behaviors — load and apply YAML keying recipes.

A behavior template defines attribute keyframe patterns (e.g. fade-in,
fade-out) anchored to a time range's start or end.  Shared across all
tools in the ``shots`` subpackage.
"""
import functools
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

try:
    import pymel.core as pm
except ImportError:
    pm = None  # type: ignore[assignment]

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

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


def list_behaviors(
    search_path: Optional[Path] = None, kind: Optional[str] = None
) -> List[str]:
    """Return stem names of all available behavior templates.

    Parameters:
        search_path: Directory to scan. Defaults to the built-in
            ``behaviors/`` directory.
        kind: When provided, only return behaviors whose ``kind`` list
            includes this value (e.g. ``"scene"`` or ``"audio"``).
            Templates without a ``kind`` key default to ``["scene"]``.
    """
    base = search_path or _BEHAVIORS_DIR
    if not base.is_dir():
        return []
    names = sorted(p.stem for p in base.glob("*.yaml"))
    if kind is None:
        return names
    result = []
    for name in names:
        try:
            tmpl = load_behavior(name, base)
        except FileNotFoundError:
            continue
        allowed = tmpl.get("kind", ["scene"])
        if kind in allowed:
            result.append(name)
    return result


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
            and optionally ``tangent`` and ``anchor``.
        start: First frame of the target range.
        end: Last frame of the target range.

    The ``anchor`` value may be:

    - ``"start"`` — place the block at the beginning of the range.
    - ``"end"`` — place the block at the end of the range.
    - A **float** between 0.0 and 1.0 — interpolate linearly between
      the start and end positions.  ``0.0`` is equivalent to
      ``"start"`` and ``1.0`` to ``"end"``.

    Returns:
        List of ``{"time": float, "value": float, "tangent": str}`` dicts.
    """
    anchor = block_def.get("anchor", "start")
    offset = block_def.get("offset", 0)
    dur = block_def.get("duration", 0)
    values = block_def.get("values", [])
    tangent = block_def.get("tangent", "linear")

    if isinstance(anchor, (int, float)):
        # Fractional anchor: 0.0 = start, 1.0 = end.
        base = start + anchor * (end - start - dur) + offset
    elif anchor == "end":
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
    source_path: str = "",
    anchor_override: Optional[str] = None,
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
        source_path: Audio file path, forwarded to
            :func:`apply_audio_clip` for ``audio_clip`` behaviors.
        anchor_override: When provided, overrides the anchor defined
            in the YAML template.  Accepts ``"start"``, ``"end"``, or
            a **float** between 0.0 and 1.0 (0.0 = start, 1.0 = end).
            Used by :func:`apply_to_shots` to place behaviors based on
            their position in the object's behavior list rather than
            relying on hardcoded template anchors.
    """
    if pm is None:
        raise RuntimeError("Maya (pymel) is required to apply behaviors")

    template = load_behavior(behavior_name, search_path)

    # Audio-clip behaviors delegate to the audio-specific helper.
    verify_mode = (template.get("verify") or {}).get("mode", "")
    if verify_mode == "audio_clip":
        apply_audio_clip(obj, start, end, source_path=source_path)
        return

    node = pm.PyNode(obj)
    has_opacity = node.hasAttr("opacity")

    # Auto-create opacity attribute when the template targets visibility.
    # This ensures the dual-keying path is always taken, producing both
    # opacity (smooth) and visibility (stepped) curves for FBX export.
    needs_opacity = not has_opacity and "visibility" in template.get("attributes", {})
    if needs_opacity:
        from mayatk.mat_utils.render_opacity.attribute_mode import OpacityAttributeMode

        OpacityAttributeMode.create([node])
        has_opacity = True

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

            # Anchor: use override if provided, else YAML, else
            # phase-based default for backward compatibility.
            if anchor_override is not None:
                block = dict(block, anchor=anchor_override)
            elif "anchor" not in block:
                block = dict(block, anchor="start" if phase == "in" else "end")

            keys = resolve_keys(block, start, end)
            for k in keys:
                tan = k["tangent"]
                # Maya's in-tangent doesn't accept "step" —
                # the equivalent is "stepnext".
                itt = "stepnext" if tan == "step" else tan
                # Use explicit plug path to target the transform only —
                # the kwarg form (attribute=) also hits the shape node.
                attr_plug = f"{node.longName()}.{target_attr}"
                pm.setKeyframe(
                    attr_plug,
                    time=k["time"],
                    value=k["value"],
                    inTangentType=itt,
                    outTangentType=tan,
                )
                # Mirror: set a matching visibility keyframe so FBX
                # export produces a real visibility animation curve.
                # Use explicit attr path to target the transform only —
                # the kwarg form also hits the shape node.
                if mirror_to_vis:
                    # Use longName to target only the transform —
                    # short names also match the shape node.
                    vis_plug = f"{node.longName()}.visibility"
                    t = k["time"]
                    pm.setKeyframe(
                        vis_plug,
                        time=t,
                        value=1.0 if k["value"] > 0 else 0.0,
                        inTangentType="stepnext",
                        outTangentType="step",
                    )
                    # Belt-and-suspenders: pm.setKeyframe may not
                    # honour stepnext on creation — force via keyTangent.
                    cmds.keyTangent(
                        vis_plug,
                        edit=True,
                        time=(t, t),
                        inTangentType="stepnext",
                        outTangentType="step",
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

    The verification strategy is controlled by the template's optional
    ``verify.mode`` key:

    ``"exact"`` (default)
        Every keyframe must exist at the exact time computed from the
        template offsets/durations.
    ``"values_in_range"``
        Every expected *value* must appear on at least one keyframe
        somewhere within the shot range.  Timing is ignored, so
        user-repositioned keys still pass.

    Parameters:
        obj: Maya node name.
        behavior_name: YAML template stem name (e.g. ``"fade_in"``).
        start: First frame of the scene range.
        end: Last frame of the scene range.
        search_path: Optional custom behaviors directory.
        keyframe_fn: Callable ``(obj, attribute, time) -> list``.
            Defaults to ``pm.keyframe(obj, q=True, at=attr, time=(t, t))``.
            Only used for ``exact`` mode.

    Returns:
        ``True`` if every expected keyframe is found.
    """
    template = load_behavior(behavior_name, search_path)
    verify_mode = (template.get("verify") or {}).get("mode", "exact")

    # Audio clip verification — track exists with start+stop keys.
    if verify_mode == "audio_clip":
        return _verify_audio_clip(obj, start, end)

    if keyframe_fn is None and verify_mode == "exact":
        if cmds is not None:
            keyframe_fn = lambda o, attr, t: cmds.keyframe(
                o, q=True, at=attr, time=(t, t)
            )
        elif pm is not None:
            keyframe_fn = lambda o, attr, t: pm.keyframe(
                o, q=True, at=attr, time=(t, t)
            )
        else:
            raise RuntimeError("Maya is required to verify behaviors")

    # Match the visibility → opacity redirect in apply_behavior so we
    # verify the attribute where keys were actually placed.
    if cmds is not None:
        _has_opacity = cmds.objExists(f"{obj}.opacity")
    elif pm is not None:
        _has_opacity = pm.objExists(f"{obj}.opacity")
    else:
        _has_opacity = False

    for attr_name, attr_def in template.get("attributes", {}).items():
        check_attr = attr_name
        if attr_name == "visibility" and _has_opacity:
            check_attr = "opacity"

        for phase in ("in", "out"):
            block = attr_def.get(phase)
            if not block:
                continue

            if verify_mode == "values_in_range":
                if not _verify_values_in_range(obj, check_attr, block, start, end):
                    return False
            else:
                if "anchor" not in block:
                    block = dict(block, anchor="start" if phase == "in" else "end")
                keys = resolve_keys(block, start, end)
                for k in keys:
                    result = keyframe_fn(obj, check_attr, k["time"])
                    if not result:
                        return False
    return True


def _verify_values_in_range(
    obj: str,
    attr: str,
    block: Dict,
    start: float,
    end: float,
) -> bool:
    """Check that every expected value exists on *attr* within the range.

    Uses a small epsilon (0.01) for floating-point comparison so that
    values like ``0.999999`` match an expected ``1.0``.
    """
    expected = block.get("values", [])
    if not expected:
        return True

    # Query all keyframe values on this attribute within the shot range.
    if cmds is not None:
        vals = cmds.keyframe(obj, q=True, at=attr, time=(start, end), valueChange=True)
    elif pm is not None:
        vals = pm.keyframe(obj, q=True, at=attr, time=(start, end), valueChange=True)
    else:
        return False

    if not vals:
        return False

    eps = 0.01
    for ev in expected:
        if not any(abs(v - ev) < eps for v in vals):
            return False
    return True


def _verify_audio_clip(obj: str, start: float, end: float) -> bool:
    """Check that a track exists with start (on) and stop (off) keys.

    Parameters:
        obj: Track identifier (canonical or raw — normalized internally).
        start: Expected start frame (value=1).
        end: Expected stop frame (value=0).

    Returns:
        ``True`` if the track exists with a start-key at *start* (value >= 1)
        and a stop-key (value == 0) anywhere in ``[start, end]``. The
        stop-key position is clip-length driven, not shot-end driven, so
        we don't pin it to *end* here.
    """
    from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils

    track_id = audio_utils.normalize_track_id(obj)
    if not audio_utils.has_track(track_id):
        return False
    keys = audio_utils.read_keys(track_id) or []
    has_start = any(abs(f - start) < 0.5 and int(round(v)) >= 1 for f, v in keys)
    has_stop = any(
        (start - 0.5) <= f <= (end + 0.5) and int(round(v)) == 0 for f, v in keys
    )
    return has_start and has_stop


def apply_audio_clip(
    obj: str,
    start: float,
    end: float,
    source_path: str = "",
) -> None:
    """Author start/stop keys for an audio track over *(start, end)*.

    Writes a stepped enum key pattern:

    - ``start`` → value=1  (audio on)
    - ``end``   → value=0  (audio off)

    The compositor materializes the DG audio node to play across this
    span.  Idempotent: rewrites the two boundary keys every call so the
    audio span always matches the current shot range exactly.

    Parameters:
        obj: Track identifier (canonical or raw — normalized internally).
        start: Shot start frame.
        end: Shot end frame. Upper bound for the off-key — the clip never
            plays past this, even if its source is longer.
        source_path: Path to the audio file (used when creating a new
            track).  Ignored when the track already exists.
    """
    from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils

    if end <= start:
        log.warning(
            "apply_audio_clip: non-positive range for '%s' (start=%s end=%s) "
            "\u2014 skipping.",
            obj,
            start,
            end,
        )
        return

    track_id = audio_utils.normalize_track_id(obj)
    with audio_utils.batch() as b:
        if not audio_utils.has_track(track_id):
            if not source_path:
                log.warning(
                    "Audio track '%s' not found and no source_path "
                    "\u2014 cannot create.",
                    obj,
                )
                return
            audio_utils.ensure_track_attr(track_id)
            audio_utils.set_path(track_id, source_path)
        else:
            # Drop stale keys (e.g. from the audio_clips UI or a prior
            # build with different shot boundaries) so the track is
            # idempotently rewritten to exactly (start, on) and (stop, off).
            audio_utils.clear_keys(track_id)

        # Place the off-key at the clip's natural end. The shot system
        # (apply_to_shots) auto-resizes the containing shot to fit the
        # written keys — grow-only. Do not clamp to shot.end here; that
        # would force the shot to dictate the clip, the opposite of the
        # design: keys drive shot size, not the other way around.
        probe_path = source_path or audio_utils.get_path(track_id) or ""
        clip_end: float = end
        if probe_path:
            try:
                fps = audio_utils.get_fps()
                dur_frames, _ = audio_utils.audio_duration_frames(probe_path, fps)
                if dur_frames > 0:
                    clip_end = start + float(dur_frames)
            except Exception as exc:
                log.debug("audio duration probe failed for '%s': %s", obj, exc)

        audio_utils.write_key(track_id, frame=start, value=1)
        audio_utils.write_key(track_id, frame=clip_end, value=0)
        b.mark_dirty([track_id])


# ---------------------------------------------------------------------------
# Duration computation
# ---------------------------------------------------------------------------


def compute_duration(
    behavior_entries: List[Dict[str, str]],
    fallback: float = 30,
    fps: Optional[float] = None,
) -> float:
    """Derive duration from the behavior templates referenced in *behavior_entries*.

    For each entry, the durations of all its behaviors are summed
    (since all get applied to the same object).  Audio templates whose
    ``duration`` field is the string ``"from_source"`` are resolved by
    probing the entry's ``source_path`` against the scene FPS — so an
    audio shot sized to the full clip length.  The result is the
    maximum across all entries.

    Parameters:
        behavior_entries: List of dicts with a ``"behavior"`` key, or
            ``BuilderObject``-like objects with a ``.behaviors`` list
            and optional ``.kind`` / ``.source_path`` attributes.
        fallback: Duration when no behavior-driven duration exists.
        fps: Scene frame-rate used to resolve ``from_source`` audio
            durations.  Queried from Maya when omitted.

    Returns:
        Duration in frames.
    """
    resolved_fps: Optional[float] = fps
    max_dur = 0.0
    has_any = False
    # Phase-layout tracking: when different objects carry start-anchored
    # ("in") and end-anchored ("out") behaviors, the shot must be long
    # enough for both phases laid out sequentially.
    global_max_in = 0.0
    global_max_out = 0.0

    # Hoisted so repeated probes don't re-import per entry.
    try:
        from mayatk.audio_utils._audio_utils import AudioUtils as _AU
    except Exception:
        _AU = None  # type: ignore[assignment]

    for entry in behavior_entries:
        # Support both dict format {"behavior": "name"} and
        # BuilderObject with .behaviors list
        if isinstance(entry, dict):
            behaviors = [entry.get("behavior", "")]
            source_path = entry.get("source_path", "") or ""
            entry_name = entry.get("name", "") or ""
            entry_kind = entry.get("kind", "") or ""
        else:
            behaviors = getattr(entry, "behaviors", [])
            source_path = getattr(entry, "source_path", "") or ""
            entry_name = getattr(entry, "name", "") or ""
            entry_kind = getattr(entry, "kind", "") or ""

        # Audio: the `audio_clips` tool populates tracks on data_internal
        # with paths independently of the manifest CSV, so a BuilderObject
        # with no source_path may still have a resolvable path via the
        # normalized track id. Fall back to that before giving up.
        if not source_path and entry_kind == "audio" and entry_name and _AU is not None:
            try:
                tid = _AU.normalize_track_id(entry_name)
                if _AU.has_track(tid):
                    source_path = _AU.get_path(tid) or ""
            except Exception as exc:
                log.debug("track-path fallback failed for '%s': %s", entry_name, exc)
        obj_total = 0.0
        obj_in = 0.0
        obj_out = 0.0
        for behavior in behaviors:
            if not behavior:
                continue
            try:
                tmpl = load_behavior(behavior)
            except FileNotFoundError:
                continue

            dur_field = tmpl.get("duration")
            if dur_field == "from_source":
                # An unresolvable source leaves has_any unchanged so the
                # caller falls back instead of collapsing the shot to 0.
                if not source_path:
                    continue
                if resolved_fps is None:
                    try:
                        resolved_fps = _AU.get_fps() if _AU else 24.0
                    except Exception:
                        resolved_fps = 24.0
                try:
                    if _AU is None:
                        raise RuntimeError("AudioUtils unavailable")
                    dur_frames, _ = _AU.audio_duration_frames(source_path, resolved_fps)
                except Exception as exc:
                    log.debug("from_source duration probe failed: %s", exc)
                    continue
                if dur_frames <= 0:
                    continue
                obj_total += float(dur_frames)
                has_any = True
                continue

            has_any = True
            for _attr_name, attr_def in tmpl.get("attributes", {}).items():
                for phase in ("in", "out"):
                    block = attr_def.get(phase)
                    if block:
                        d = block.get("duration", 0)
                        obj_total += d
                        if phase == "in":
                            obj_in += d
                        else:
                            obj_out += d
        if obj_total > max_dur:
            max_dur = obj_total
        global_max_in = max(global_max_in, obj_in)
        global_max_out = max(global_max_out, obj_out)
    if not has_any:
        return fallback
    # Ensure the duration accommodates both start-anchored and
    # end-anchored behaviors laid out without overlap.
    phase_total = global_max_in + global_max_out
    return max(max_dur, phase_total)


# ---------------------------------------------------------------------------
# Batch application
# ---------------------------------------------------------------------------


def apply_to_shots(
    shots: list,
    apply_fn,
    exists_fn=None,
    has_keys_fn=None,
    store=None,
) -> Dict[str, list]:
    """Apply declared behaviors from shot metadata to Maya objects.

    Reads ``metadata["behaviors"]`` from each shot and applies keyframe
    patterns via *apply_fn*.  Objects with existing keyframes in the
    shot range are skipped to avoid overwriting user animation.

    Audio-grow (expanding shot.end to fit audio clips and rippling
    downstream shots) is handled upstream by
    ``ShotManifest._compute_plan`` / ``_execute_plan``.  By the time
    this function runs, ``shot.start`` / ``shot.end`` are already at
    their final positions.

    Processing uses a **two-pass-per-shot** design:

    1. **Audio pass** — audio entries are applied first so their
       keyframes exist before non-audio anchors are computed.
    2. **Non-audio pass** — fade and other behavior entries are applied
       using the finalized ``shot.start`` / ``shot.end``.  Positional
       anchors are computed here.

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
    from mayatk.audio_utils._audio_utils import AudioUtils as _audio_utils

    def _is_audio(entry):
        return (entry.get("kind") == "audio") or bool(entry.get("source_path"))

    def _default_exists(obj_name, entry=None):
        if entry is not None and _is_audio(entry):
            try:
                if _audio_utils.has_track(_audio_utils.normalize_track_id(obj_name)):
                    return True
            except Exception:
                pass
            # New audio with a source_path counts as "buildable".
            if entry.get("source_path"):
                return True
        if pm is None:
            return False
        return pm.objExists(obj_name)

    if exists_fn is None:
        exists_fn = _default_exists

    def _default_has_keys(obj_name, start, end, entry=None):
        if entry is not None and _is_audio(entry):
            return _verify_audio_clip(obj_name, start, end)
        if pm is None:
            return False
        try:
            keys = pm.keyframe(obj_name, q=True, time=(start, end), tc=True)
            return bool(keys)
        except Exception:
            return False

    if has_keys_fn is None:
        has_keys_fn = _default_has_keys

    # Adapters so callers that pass their own fns (old 3-arg signature)
    # still work, while default fns may use the entry for audio dispatch.
    def _call_exists(obj_name, entry):
        try:
            return exists_fn(obj_name, entry)
        except TypeError:
            return exists_fn(obj_name)

    def _call_has_keys(obj_name, start, end, entry):
        try:
            return has_keys_fn(obj_name, start, end, entry)
        except TypeError:
            return has_keys_fn(obj_name, start, end)

    applied: list = []
    skipped: list = []
    for shot in shots:
        if shot.locked:
            continue
        if abs(shot.end - shot.start) < 1e-6:
            continue

        entries = shot.metadata.get("behaviors", [])

        # ------------------------------------------------------------------
        # Pass 1 — Audio entries: apply first so the shot range is
        # finalized before non-audio behaviors compute positional
        # anchors.  Audio clips may extend shot.end via grow-after-apply.
        # ------------------------------------------------------------------
        for entry in entries:
            obj_name = entry.get("name", "")
            behavior = entry.get("behavior", "")
            if not behavior or not obj_name:
                continue
            if not _is_audio(entry):
                continue
            if not _call_exists(obj_name, entry):
                continue
            if _call_has_keys(obj_name, shot.start, shot.end, entry):
                skipped.append(
                    {"object": obj_name, "behavior": behavior, "shot": shot.name}
                )
                continue

            source_path = entry.get("source_path", "") or ""
            try:
                apply_fn(
                    obj_name,
                    behavior,
                    shot.start,
                    shot.end,
                    source_path=source_path,
                    anchor_override=0.0,
                )
            except TypeError:
                apply_fn(obj_name, behavior, shot.start, shot.end)
            applied.append(
                {"object": obj_name, "behavior": behavior, "shot": shot.name}
            )

            # NOTE: Audio-grow (expanding shot.end + ripple) is handled
            # upstream by _compute_plan / _execute_plan.  shot.end is
            # already at the correct position when apply_to_shots runs.

        # ------------------------------------------------------------------
        # Pass 2 — Non-audio entries: shot.start / shot.end are now
        # finalized (audio grow is complete).  Compute positional
        # anchors and place behavior keyframes.
        # ------------------------------------------------------------------
        non_audio = [e for e in entries if not _is_audio(e)]
        obj_indices: Dict[str, int] = {}  # obj_name → count seen so far
        obj_counts: Dict[str, int] = {}  # obj_name → total behaviors
        for entry in non_audio:
            n = entry.get("name", "")
            if n:
                obj_counts[n] = obj_counts.get(n, 0) + 1

        for entry in non_audio:
            obj_name = entry.get("name", "")
            behavior = entry.get("behavior", "")
            if not behavior or not obj_name:
                continue
            if not _call_exists(obj_name, entry):
                continue
            if _call_has_keys(obj_name, shot.start, shot.end, entry):
                skipped.append(
                    {"object": obj_name, "behavior": behavior, "shot": shot.name}
                )
                continue

            # Positional anchor: distribute evenly across the shot.
            # List position always wins over the YAML template default.
            # 1 behavior  → 0.0                (start)
            # 2 behaviors → 0.0, 1.0           (start, end)
            # 3 behaviors → 0.0, 0.5, 1.0      (start, middle, end)
            # N behaviors → idx / max(total-1, 1)
            idx = obj_indices.get(obj_name, 0)
            obj_indices[obj_name] = idx + 1
            total = obj_counts.get(obj_name, 1)
            anchor = idx / max(total - 1, 1)

            source_path = entry.get("source_path", "") or ""
            try:
                apply_fn(
                    obj_name,
                    behavior,
                    shot.start,
                    shot.end,
                    source_path=source_path,
                    anchor_override=anchor,
                )
            except TypeError:
                apply_fn(obj_name, behavior, shot.start, shot.end)
            applied.append(
                {"object": obj_name, "behavior": behavior, "shot": shot.name}
            )

    return {"applied": applied, "skipped": skipped}
