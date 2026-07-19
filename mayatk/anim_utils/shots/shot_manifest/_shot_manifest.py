# !/usr/bin/python
# coding=utf-8
"""Maya Shot Manifest adapter — the DCC layer over pythontk's manifest engine.

The CSV parsing, column mapping, build planning (compute-then-commit), and
assessment orchestration all live once, pure, in
``pythontk.core_utils.engines.shots.manifest``; this module subclasses that
engine's :class:`~pythontk.ShotManifest` and overrides only its scene hooks:

- ``_resolve_fps`` → ``cmds.currentUnit`` (via :class:`AudioUtils`);
- ``_measure_audio`` → source-path / registered-track probe against scene FPS;
- ``_audio_grow_duration`` → the Maya-bound ``behaviors.compute_duration``;
- ``_resolve_names_keep_missing`` → long-DAG-name resolution;
- ``_discover_scene_objects`` / ``_filter_to_animated`` → animCurve walks;
- assess seams (``_object_exists`` / ``_verify_behavior`` / ``_keyframe_range``
  / ``_audio_exists``) → ``cmds`` / audio-track queries;
- ``apply_behaviors`` → :func:`behaviors.apply_to_shots` keying fades and
  audio onto each shot's objects;
- ``rewire_audio`` → the audio compositor sync.

The pure model names (:class:`BuilderStep`, :class:`ColumnMap`,
:func:`parse_csv`, …) are re-exported so existing
``mayatk.anim_utils.shots.shot_manifest._shot_manifest`` imports keep working.
"""
import logging
from typing import Callable, Dict, List, Optional, Tuple

from pythontk.core_utils.engines.shots.manifest.manifest_model import (  # noqa: F401 — re-exports
    Action,
    AUDIO_PLACEHOLDER_DURATION,
    BuilderObject,
    BuilderStep,
    ColumnMap,
    DEFAULT_FIT_MODE,
    DEFAULT_INITIAL_SHOT_LENGTH,
    FitMode,
    ObjectStatus,
    PlannedShot,
    StepStatus,
    _ALT_STEP_RE,
    _BEHAVIOR_PATTERNS,
    _ResolvedColumns,
    _SECTION_RE,
    _STEP_RE,
    _read_csv_rows,
    _resolve_columns,
    _strip_cell,
    detect_behaviors,
    parse_csv,
)
from pythontk.core_utils.engines.shots.manifest.manifest_engine import (
    ShotManifest as _EngineShotManifest,
)

from mayatk.anim_utils.shots._shots import (
    ShotStore,
    _resolve_long_names_keep_missing,
)

# Imported at module scope (not deferred like the other AudioUtils uses) so tests
# can patch ``_shot_manifest.AudioUtils`` as the seam for ``_default_audio_exists``.
from mayatk.audio_utils._audio_utils import AudioUtils

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Maya audio measurement (shared by the class hook and the module facades)
# ---------------------------------------------------------------------------


def _measure_audio_obj(obj: BuilderObject, fps: float) -> Optional[float]:
    """Length in frames of *obj*'s audio source (path or registered track)."""
    from mayatk.anim_utils.shots.shot_manifest.behaviors._behaviors import (
        _track_source_path,
    )

    src = getattr(obj, "source_path", "") or ""
    if not src:
        src = _track_source_path(getattr(obj, "name", "") or "")
    if not src:
        return None
    try:
        frames, _ = AudioUtils.audio_duration_frames(src, fps)
    except Exception as exc:
        log.debug("audio duration probe failed for %r: %s", obj.name, exc)
        return None
    return float(frames) if frames > 0 else None


def _scene_fps() -> float:
    """Scene FPS, or 24 when Maya is unavailable."""
    try:
        return float(AudioUtils.get_fps())
    except Exception:
        return 24.0


# ---------------------------------------------------------------------------
# Shot-region detection  (canonical implementation lives in _shots)
# ---------------------------------------------------------------------------

from mayatk.anim_utils.shots._shots import (  # noqa: E402,F401
    detect_shot_regions,
    regions_from_selected_keys,
)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class ShotManifest(_EngineShotManifest):
    """:class:`pythontk.ShotManifest` with the scene hooks bound to Maya.

    Only the DCC-reaching hooks are overridden; the planner
    (``update`` / ``_compute_plan`` / ``_execute_plan``), the ``sync``
    orchestrator, and ``assess`` are inherited unchanged from the pure engine.
    """

    # ---- scene hooks -------------------------------------------------------

    def _resolve_fps(self) -> float:
        """Return scene FPS, or 24 when Maya is unavailable.

        Cached per instance; cleared at the top of ``update`` so a
        single build call queries ``cmds.currentUnit`` once instead of
        twice per shot.
        """
        if self._fps_cache is not None:
            return self._fps_cache
        self._fps_cache = _scene_fps()
        return self._fps_cache

    def _measure_audio(self, obj: BuilderObject) -> Optional[float]:
        """Audio-clip length in frames via source path or registered track."""
        return _measure_audio_obj(obj, self._resolve_fps())

    def _audio_grow_duration(self, audio_objs: List[BuilderObject]) -> float:
        """Content-driven duration for an existing audio step.

        Routes through the Maya-bound ``behaviors.compute_duration`` (which
        resolves registered track paths and probes files itself) — imported
        lazily from the package, the established mock seam.
        """
        from mayatk.anim_utils.shots.shot_manifest.behaviors import compute_duration

        return compute_duration(audio_objs, fallback=0.0)

    def _resolve_names_keep_missing(self, names: List[str]) -> List[str]:
        """Long-name-resolve *names*, keeping the CSV form for missing objects
        so the pinned-object system can surface them."""
        return _resolve_long_names_keep_missing(names)

    # ---- behavior application / audio rewire ------------------------------

    def apply_behaviors(self) -> Dict[str, list]:
        """Apply detected behaviors to Maya objects (fades, audio clips).

        Lazy package imports preserve the ``...behaviors.apply_behavior`` /
        ``...behaviors.apply_to_shots`` mock seams.
        """
        from mayatk.anim_utils.shots.shot_manifest.behaviors import (
            apply_behavior,
            apply_to_shots,
        )

        return apply_to_shots(
            self.store.sorted_shots(),
            apply_fn=apply_behavior,
            store=self.store,
        )

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

    # ---- assess seams ------------------------------------------------------

    def _object_exists(self, name: str) -> bool:
        import maya.cmds as _cmds

        return _cmds.objExists(name)

    def _verify_behavior(
        self,
        obj: str,
        behavior: str,
        start: float,
        end: float,
        anchor_override: Optional[float] = None,
    ) -> bool:
        # Lazy package import — the established ``...behaviors.verify_behavior``
        # mock seam.
        from mayatk.anim_utils.shots.shot_manifest.behaviors import verify_behavior

        return verify_behavior(
            obj, behavior, start, end, anchor_override=anchor_override
        )

    def _keyframe_range(self, obj_name: str) -> Optional[Tuple[float, float]]:
        return self._default_keyframe_range(obj_name)

    def _audio_exists(self, name: str) -> bool:
        return self._default_audio_exists(name)

    # ---- scene walks (animCurve acquisition) -------------------------------

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
            import maya.cmds  # noqa: F401 — availability probe
        except ImportError:
            return []

        animated = self._transform_curve_map()

        found: list = []
        from mayatk.core_utils._core_utils import leaf_name as _short

        for obj in sorted(animated):
            if _short(obj) in exclude_names:
                continue
            if any(
                self._curve_varies_in_range(crv, start, end) for crv in animated[obj]
            ):
                found.append(obj)

        return found

    def _transform_curve_map(self) -> Dict[str, List[str]]:
        """Transform → standard-attr anim-curves map, cached per cycle.

        Building this map walks every animCurve in the scene, so it is
        computed at most once per assess/update cycle (both entry points
        clear ``self._animated_transforms``) and shared by every
        per-step animation check.
        """
        if self._animated_transforms is None:
            from mayatk.anim_utils.shots._shots import (
                _map_standard_curves_to_transforms,
            )

            self._animated_transforms = _map_standard_curves_to_transforms()
        return self._animated_transforms

    def _curve_varies_in_range(self, crv: str, start: float, end: float) -> bool:
        """True if *crv*'s value varies by >1e-4 within ``[start, end]``.

        Each curve's full (times, values) arrays are queried from Maya
        once per assess/update cycle and range checks are evaluated in
        Python — S steps × C curves used to mean S×C ranged
        ``cmds.keyframe`` calls; now it's C.
        """
        if self._curve_data is None:
            self._curve_data = {}
        data = self._curve_data.get(crv)
        if data is None:
            import maya.cmds as cmds

            times = cmds.keyframe(crv, q=True, timeChange=True) or []
            values = cmds.keyframe(crv, q=True, valueChange=True) or []
            data = self._curve_data[crv] = (times, values)
        times, values = data
        window = [v for t, v in zip(times, values) if start <= t <= end]
        return bool(window) and (max(window) - min(window)) > 1e-4

    def _filter_to_animated(
        self, objects: List[str], start: float, end: float
    ) -> List[str]:
        """Return only objects that have standard-attribute animation in [start, end].

        Objects animated exclusively on custom attributes (e.g.
        ``audio_trigger``) are treated as boundary markers and excluded.
        """
        if not objects:
            return []

        try:
            import maya.cmds  # noqa: F401 — availability probe
        except ImportError:
            return objects

        transform_curves = self._transform_curve_map()
        result = []
        for obj in objects:
            crvs = transform_curves.get(obj)
            if crvs and any(
                self._curve_varies_in_range(crv, start, end) for crv in crvs
            ):
                result.append(obj)
        return result

    # ---- default seam implementations (kept as named statics for tests) ----

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
                if AudioUtils.is_registered(name):
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

    # ---- from_csv ----------------------------------------------------------

    @classmethod
    def from_csv(
        cls,
        filepath: str,
        store: Optional[ShotStore] = None,
        columns: Optional[ColumnMap] = None,
        post_process: Optional[Callable[[BuilderStep], None]] = None,
    ) -> Tuple["ShotManifest", List[BuilderStep]]:
        """Convenience: parse a CSV and return a ready-to-build engine.

        Overrides the engine version so the default store is the **Maya**
        :meth:`ShotStore.active` (auto-installing scene persistence), not the
        pure engine base's.

        Parameters:
            filepath: Path to the CSV file.
            store: Optional existing ``ShotStore`` to populate.
                If ``None``, the active Maya store is used.
            columns: Column index mapping.
            post_process: Optional callable invoked on each step after
                assembly.

        Returns:
            ``(builder, steps)`` tuple. Call ``builder.sync(steps)`` to
            execute.
        """
        steps = parse_csv(filepath, columns, post_process=post_process)
        st = store or ShotStore.active()
        return cls(st), steps
