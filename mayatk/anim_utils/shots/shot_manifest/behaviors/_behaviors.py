# coding=utf-8
"""Behaviors — Maya appliers over the engine's pure keying-recipe core.

Template discovery/loading (``Behaviors.load_behavior`` /
``list_behaviors`` / ``templates``), the schema, and the anchor/offset/duration
→ absolute-keyframe math (``Behaviors.resolve_keys``) live once, DCC-agnostic,
in ``pythontk.core_utils.engines.shots.manifest.behaviors`` (JSON templates,
shared with blendertk); :class:`Behaviors` extends that engine class.  This
module supplies the **scene-touching** half: applying keys via ``cmds``
(:meth:`Behaviors.apply_behavior`; :meth:`Behaviors.apply_to_shots` binds the
engine's build loop to Maya's checks), verifying them
(:meth:`Behaviors.verify_behavior`), the audio-clip track writer
(:meth:`Behaviors.apply_audio_clip`), and :meth:`Behaviors.compute_duration`
bound to Maya's audio measurement.

A template naming an ``effect`` (the built-in fades and highlight) is keyed by
the render-effect writer from the scene's effect recipe
(``RenderEffects.apply_effect``) -- the same keys the Render Effects panel
writes by hand; the clip goes through ``AudioUtils.key_clip``, the Audio Clips
panel's writer.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pythontk.core_utils.engines.shots.manifest.behaviors import (  # noqa: F401
    Behaviors as _PyBehaviors,
    BehaviorSpec,  # published by this package's __init__
)

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None  # type: ignore[assignment]

# Log under the package name, not this private impl module, so the logger name
# stays stable across the __init__ -> _behaviors split (user log filters and
# tests key on ``...shot_manifest.behaviors``).
log = logging.getLogger(__name__.rpartition(".")[0])


class _BehaviorsInternal(object):
    """Internal helpers for Behaviors."""

    @staticmethod
    def _track_source_path(name: str) -> str:
        """Resolve an audio entry's source path from the ``audio_clips`` track
        registry (tracks carry paths independently of the manifest CSV).

        The single lookup shared by :func:`compute_duration`'s source fallback and
        the manifest adapter's ``_measure_audio`` hook.
        """
        if not name:
            return ""
        try:
            from mayatk.audio_utils._audio_utils import AudioUtils as _AU

            tid = _AU.normalize_track_id(name)
            if _AU.has_track(tid):
                return _AU.get_path(tid) or ""
        except Exception as exc:
            log.debug("track-path fallback failed for '%s': %s", name, exc)
        return ""

    @staticmethod
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
            vals = cmds.keyframe(
                obj, q=True, at=attr, time=(start, end), valueChange=True
            )
        else:
            return False

        if not vals:
            return False

        eps = 0.01
        for ev in expected:
            if not any(abs(v - ev) < eps for v in vals):
                return False
        return True

    @staticmethod
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


class Behaviors(_PyBehaviors, _BehaviorsInternal):
    """Behaviors — module namespace.

    Extends the pure engine class (so ``Behaviors.load_behavior`` /
    ``list_behaviors`` / ``resolve_keys`` / ``templates`` resolve through this
    one name) with the Maya appliers; :meth:`compute_duration` overrides the
    pure version with the Maya-bound binding. Mirror of blendertk's.
    """

    @staticmethod
    def apply_behavior(
        obj: str,
        behavior_name: str,
        start: float,
        end: float,
        attrs: Optional[List[str]] = None,
        search_path: Optional[Path] = None,
        source_path: str = "",
        anchor_override: Optional[str] = None,
        recipe: Optional[Any] = None,
        fps: Optional[float] = None,
    ) -> List[Tuple[str, float]]:
        """Apply a named behavior template to an object over a time range.

        Returns ``(anim_curve, time)`` for every key it set -- what the
        manifest records as its own (``ShotEditLedger``), so a re-apply can
        replace exactly those; an audio clip's are its track's keys.

        A template naming an ``effect`` is keyed by ``RenderEffects.apply_effect``
        from *recipe* (the scene's effect recipe when omitted) -- the keys the
        Render Effects panel writes -- placed by the template's ``place`` and
        *anchor_override*.  A template keying its own ``attributes`` is keyed
        as below.

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
            behavior_name: Template stem name (e.g. ``"fade_in"``).
            start: First frame of the range.
            end: Last frame of the range.
            attrs: If given, only key these attributes. Otherwise key all
                attributes defined in the template.
            search_path: Optional custom behaviors directory.
            source_path: Audio file path, forwarded to
                :func:`apply_audio_clip` for ``audio_clip`` behaviors.
            anchor_override: When provided, overrides the anchor defined
                in the template.  Accepts ``"start"``, ``"end"``, or
                a **float** between 0.0 and 1.0 (0.0 = start, 1.0 = end).
                Used by :func:`apply_to_shots` to place behaviors based on
                their position in the object's behavior list rather than
                relying on hardcoded template anchors.
            recipe: The scene's ``ptk.EffectRecipe`` (an ``effect``
                template's source); queried when omitted.
            fps: The scene's rate; queried when omitted.
        """
        if cmds is None:
            raise RuntimeError("Maya (cmds) is required to apply behaviors")

        template = Behaviors.load_behavior(behavior_name, search_path)

        # Audio-clip behaviors delegate to the audio-specific helper.
        verify_mode = (template.get("verify") or {}).get("mode", "")
        if verify_mode == "audio_clip":
            return Behaviors.apply_audio_clip(obj, start, end, source_path=source_path)

        effect = Behaviors.effect_of(template)
        if effect is not None:
            from mayatk.mat_utils.render_opacity.render_effects import RenderEffects

            return RenderEffects.apply_effect(
                obj,
                effect,
                start,
                end,
                recipe=recipe,
                fps=fps,
                place=Behaviors.place_of(template),
                anchor=anchor_override,
            )

        node = str(obj)
        written: List[Tuple[str, float]] = []
        template_attrs = template.get("attributes", {})

        # Render-effect channels are created from their presets on demand, so a
        # template keying "opacity" or "highlight" does not error on an object
        # that lacks the attribute. The presence channel (``opacity``) is also
        # created when the template targets "visibility", so the dual-keying
        # path below is always taken: a smooth ramp plus a stepped visibility
        # mirror for FBX export.
        from mayatk.mat_utils.render_opacity.attribute_mode import (
            OpacityAttributeMode,
        )
        from mayatk.mat_utils.render_opacity.channels import CHANNELS, PRESENCE

        wanted = [
            spec
            for name, spec in CHANNELS.items()
            if name in template_attrs
            or (spec.drives_presence and "visibility" in template_attrs)
        ]
        for spec in wanted:
            if not OpacityAttributeMode.has_channel(node, spec):
                OpacityAttributeMode.create([node], spec)
        has_opacity = OpacityAttributeMode.has_channel(node, PRESENCE)

        # Resolve the transform's long name once — plugs built per key below.
        long_node = (cmds.ls(node, long=True) or [node])[0]

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
                target_attr = PRESENCE.name
                mirror_to_vis = True
            elif attr_name == PRESENCE.name and has_opacity:
                mirror_to_vis = True

            for phase in ("in", "out"):
                block = attr_def.get(phase)
                if not block:
                    continue

                # Anchor: use override if provided, else the template's, else
                # phase-based default for backward compatibility.
                if anchor_override is not None:
                    block = dict(block, anchor=anchor_override)
                elif "anchor" not in block:
                    block = dict(block, anchor="start" if phase == "in" else "end")

                keys = Behaviors.resolve_keys(block, start, end)
                for k in keys:
                    tan = k["tangent"]
                    # Maya's in-tangent doesn't accept "step" —
                    # the equivalent is "stepnext".
                    itt = "stepnext" if tan == "step" else tan
                    # Use explicit plug path to target the transform only —
                    # the kwarg form (attribute=) also hits the shape node.
                    attr_plug = f"{long_node}.{target_attr}"
                    cmds.setKeyframe(
                        attr_plug,
                        time=k["time"],
                        value=k["value"],
                        inTangentType=itt,
                        outTangentType=tan,
                    )
                    written.append((Behaviors.curve_of(attr_plug), k["time"]))
                    # Mirror: set a matching visibility keyframe so FBX
                    # export produces a real visibility animation curve.
                    # Use explicit attr path to target the transform only —
                    # the kwarg form also hits the shape node.
                    if mirror_to_vis:
                        # Use longName to target only the transform —
                        # short names also match the shape node.
                        vis_plug = f"{long_node}.visibility"
                        t = k["time"]
                        cmds.setKeyframe(
                            vis_plug,
                            time=t,
                            value=1.0 if k["value"] > 0 else 0.0,
                            inTangentType="stepnext",
                            outTangentType="step",
                        )
                        # Belt-and-suspenders: cmds.setKeyframe may not
                        # honour stepnext on creation — force via keyTangent.
                        cmds.keyTangent(
                            vis_plug,
                            edit=True,
                            time=(t, t),
                            inTangentType="stepnext",
                            outTangentType="step",
                        )
                        written.append((Behaviors.curve_of(vis_plug), t))
        return written

    @staticmethod
    def curve_of(plug: str) -> str:
        """The anim curve keying *plug* (the plug itself when none yet)."""
        return (cmds.keyframe(plug, q=True, name=True) or [plug])[0]

    @staticmethod
    def behavior_plugs(obj: str, behavior_name: str) -> List[str]:
        """The plugs *behavior_name* keys on *obj*: its template's channels,
        with ``visibility`` and ``opacity`` each bringing the other (the
        dual-keyed presence pair :meth:`apply_behavior` writes).  Only plugs
        that exist; ``[]`` when the template or object does not."""
        if cmds is None:
            return []
        try:
            attrs = set(Behaviors.keyed(behavior_name).get("attributes") or {})
        except (FileNotFoundError, ValueError):
            return []
        if attrs & {"visibility", "opacity"}:
            attrs |= {"visibility", "opacity"}
        long_node = (cmds.ls(str(obj), long=True) or [None])[0]
        if long_node is None:
            return []
        return [
            f"{long_node}.{a}"
            for a in sorted(attrs)
            if cmds.attributeQuery(a, node=long_node, exists=True)
        ]

    @staticmethod
    def verify_behavior(
        obj: str,
        behavior_name: str,
        start: float,
        end: float,
        search_path: Optional[Path] = None,
        keyframe_fn: Optional[Any] = None,
        anchor_override: Optional[Any] = None,
        recipe: Optional[Any] = None,
        fps: Optional[float] = None,
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
            behavior_name: Template stem name (e.g. ``"fade_in"``).
            start: First frame of the scene range.
            end: Last frame of the scene range.
            search_path: Optional custom behaviors directory.
            keyframe_fn: Callable ``(obj, attribute, time) -> list``.
                Defaults to ``cmds.keyframe(obj, q=True, at=attr, time=(t, t))``.
                Only used for ``exact`` mode.
            anchor_override: Same semantics as :func:`apply_behavior` —
                when the keys were placed with a distributed anchor
                (multi-behavior objects), ``exact`` verification must model
                the same anchor or it checks the template's default
                positions and permanently flags the object as broken.
            recipe: The scene's effect recipe -- an ``effect`` template is
                checked as :meth:`Behaviors.keyed` states it under *recipe*.
            fps: The scene's rate, for an effect's lengths.

        Returns:
            ``True`` if every expected keyframe is found.
        """
        template = Behaviors.keyed(
            Behaviors.load_behavior(behavior_name, search_path), recipe, fps
        )
        verify_mode = (template.get("verify") or {}).get("mode", "exact")

        # Audio clip verification — track exists with start+stop keys.
        if verify_mode == "audio_clip":
            return _BehaviorsInternal._verify_audio_clip(obj, start, end)

        # No object in the scene → no keys → cannot verify.  Bail out so
        # cmds.keyframe doesn't raise on a non-existent name.
        if cmds is not None and not cmds.objExists(obj):
            return False

        if keyframe_fn is None and verify_mode == "exact":
            if cmds is not None:

                def keyframe_fn(o, attr, t):
                    return cmds.keyframe(o, q=True, at=attr, time=(t, t))

            else:
                raise RuntimeError("Maya is required to verify behaviors")

        # Match the visibility → presence-channel redirect in apply_behavior so
        # we verify the attribute where keys were actually placed.
        from mayatk.mat_utils.render_opacity.channels import PRESENCE

        if cmds is not None:
            _has_opacity = cmds.objExists(f"{obj}.{PRESENCE.name}")
        else:
            _has_opacity = False

        for attr_name, attr_def in template.get("attributes", {}).items():
            check_attr = attr_name
            if attr_name == "visibility" and _has_opacity:
                check_attr = PRESENCE.name

            for phase in ("in", "out"):
                block = attr_def.get(phase)
                if not block:
                    continue

                if verify_mode == "values_in_range":
                    if not _BehaviorsInternal._verify_values_in_range(
                        obj, check_attr, block, start, end
                    ):
                        return False
                else:
                    # Mirror apply_behavior's anchor precedence exactly:
                    # override > template > phase-based default.
                    if anchor_override is not None:
                        block = dict(block, anchor=anchor_override)
                    elif "anchor" not in block:
                        block = dict(block, anchor="start" if phase == "in" else "end")
                    keys = Behaviors.resolve_keys(block, start, end)
                    for k in keys:
                        result = keyframe_fn(obj, check_attr, k["time"])
                        if not result:
                            return False
        return True

    @staticmethod
    def apply_audio_clip(
        obj: str,
        start: float,
        end: float,
        source_path: str = "",
    ) -> List[Tuple[str, float]]:
        """Place an audio track's clip at *start* -- the Audio Clips panel's
        writer (``AudioUtils.key_clip``): ON at *start*, OFF at its end.

        The compositor materializes the DG audio node to play across this
        span.  Nothing else on the track is touched -- it used to be cleared
        first, taking the panel's keys and another shot's use of the track with
        it; the build now takes out only the keys it claimed last time.

        Parameters:
            obj: Track identifier (canonical or raw — normalized internally).
            start: Shot start frame.
            end: Shot end frame — the off-key fallback when the source's
                duration can't be probed.  When it can, the off-key lands at
                the clip's natural end (``start + duration``), which may
                exceed *end*: keys drive shot size (grow-only, via
                ``apply_to_shots``' upstream plan), not the other way around.
            source_path: Path to the audio file (used when creating a new
                track).  Ignored when the track already exists.

        Returns:
            ``(anim curve, time)`` of the keys written -- what the build
            claims as its own.
        """
        from mayatk.audio_utils._audio_utils import AudioUtils as audio_utils

        if end <= start:
            log.warning(
                "apply_audio_clip: non-positive range for '%s' (start=%s end=%s) "
                "— skipping.",
                obj,
                start,
                end,
            )
            return []

        track_id = audio_utils.normalize_track_id(obj)
        with audio_utils.batch() as b:
            if not audio_utils.has_track(track_id):
                if not source_path:
                    log.warning(
                        "Audio track '%s' not found and no source_path "
                        "— cannot create.",
                        obj,
                    )
                    return []
                audio_utils.ensure_track_attr(track_id)
                audio_utils.set_path(track_id, source_path)
            written = audio_utils.key_clip(track_id, start, end=end)
            b.mark_dirty([track_id])
        return written

    @staticmethod
    def compute_duration(
        behavior_entries: List[Dict[str, str]],
        fallback: float = 30,
        fps: Optional[float] = None,
    ) -> float:
        """Derive duration from the behavior templates in *behavior_entries*.

        Maya-bound facade over the engine's pure
        :func:`~pythontk.core_utils.engines.shots.manifest.behaviors.compute_duration`:
        injects Maya's audio measurement (``from_source`` templates probe the
        entry's ``source_path`` against the scene FPS) and the ``audio_clips``
        track-registry fallback (an audio entry with no ``source_path`` may still
        resolve a path via its normalized track id).

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
        try:
            from mayatk.audio_utils._audio_utils import AudioUtils as _AU
        except Exception:
            _AU = None  # type: ignore[assignment]

        # Resolved lazily on the first from_source probe so a template-only
        # manifest never queries cmds.currentUnit.
        state = {"fps": fps}

        def _audio_duration_fn(source_path: str) -> Optional[float]:
            if _AU is None:
                return None
            if state["fps"] is None:
                from mayatk.anim_utils.shots.shot_manifest._shot_manifest import (
                    ShotManifest,
                )

                state["fps"] = ShotManifest._scene_fps()
            dur_frames, _ = _AU.audio_duration_frames(source_path, state["fps"])
            return dur_frames

        def _resolve_source_fn(name: str, kind: str) -> Optional[str]:
            # Audio-kind entries only: a manifest entry with no source_path may
            # still resolve a path via its registered track (see _track_source_path).
            if kind != "audio":
                return None
            return _BehaviorsInternal._track_source_path(name) or None

        return _PyBehaviors.compute_duration(
            behavior_entries,
            fallback=fallback,
            fps=fps,
            audio_duration_fn=_audio_duration_fn,
            resolve_source_fn=_resolve_source_fn,
        )

    @staticmethod
    def apply_to_shots(
        shots: list,
        apply_fn,
        exists_fn=None,
        has_keys_fn=None,
        store=None,
        resolve_fn=None,
        conflict_fn=None,
        release_fn=None,
    ) -> Dict[str, list]:
        """Apply declared behaviors from shot metadata to Maya objects.

        The engine's build loop (``ptk`` ``Behaviors.apply_to_shots`` -- two
        passes per shot, guards settled before anything is keyed, every
        previous key released first) bound to Maya's checks: *exists_fn*
        defaults to ``cmds.objExists`` (an audio entry: a registered track, or a
        ``source_path`` to make one) and *has_keys_fn* to keys in the range
        (an audio entry: its clip already placed).  The other parameters and
        the result are the engine's.
        """
        from mayatk.audio_utils._audio_utils import AudioUtils as _audio_utils

        def _is_audio(entry):
            return (entry.get("kind") == "audio") or bool(entry.get("source_path"))

        def _default_exists(obj_name, entry=None):
            if entry is not None and _is_audio(entry):
                try:
                    if _audio_utils.is_registered(obj_name):
                        return True
                except Exception:
                    pass
                # New audio with a source_path counts as "buildable".
                if entry.get("source_path"):
                    return True
            if cmds is None:
                return False
            return cmds.objExists(obj_name)

        def _default_has_keys(obj_name, start, end, entry=None):
            if entry is not None and _is_audio(entry):
                return _BehaviorsInternal._verify_audio_clip(obj_name, start, end)
            if cmds is None:
                return False
            try:
                keys = cmds.keyframe(obj_name, q=True, time=(start, end), tc=True)
                return bool(keys)
            except Exception:
                return False

        return _PyBehaviors.apply_to_shots(
            shots,
            apply_fn,
            exists_fn=exists_fn if exists_fn is not None else _default_exists,
            has_keys_fn=has_keys_fn if has_keys_fn is not None else _default_has_keys,
            store=store,
            resolve_fn=resolve_fn,
            conflict_fn=conflict_fn,
            release_fn=release_fn,
        )


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Duration computation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Batch application
# ---------------------------------------------------------------------------
