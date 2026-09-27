# !/usr/bin/python
# coding=utf-8
"""``AnimUtils`` -- the public index of mayatk's key and curve operations.

Every public method keeps its signature and docstring here and delegates its
body to the concept module that owns the job (the wildcard ``DEFAULT_INCLUDE``
entry registers the flat ``mtk.<method>`` names from this class body, so the
methods themselves never move):

- ``_key_query`` -- which curves drive a node, which keys exist and when.
- ``_timeline`` -- the playback clock: current frame, playback/scene range.
- ``_curve_optimize`` -- static/flat/simplify/extremes key reduction.
- ``_curve_snapshot`` -- stash animation curves and put them back exactly.
- ``_tangents`` -- tangent read/write, stepped keys, tangent-preserving keys.
- ``_key_timing`` -- retiming: move, space, align, snap, invert keys.
- ``_key_edit`` -- bake, set, insert, delete and select keys.
- ``_key_transfer`` -- copy/paste keys and object-to-object transfer.
- ``_tied_keys`` -- bookend keys (tie / untie / detect).
- ``_anim_layers`` -- animation layers and the transient preview layer.
"""

from typing import (
    List,
    Tuple,
    Dict,
    Iterable,
    Optional,
    Union,
    Any,
    Callable,
    Sequence,
)

import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils

# The concept bases of the helper class below: needed at class-definition
# time, so imported eagerly (they import nothing from this module).
from mayatk.anim_utils._key_query import _KeyQueryInternal
from mayatk.anim_utils._timeline import _TimelineInternal
from mayatk.anim_utils._curve_optimize import _CurveOptimizeInternal
from mayatk.anim_utils._curve_snapshot import _CurveSnapshotInternal
from mayatk.anim_utils._tangents import _TangentInternal
from mayatk.anim_utils._key_timing import _KeyTimingInternal
from mayatk.anim_utils._key_edit import _KeyEditInternal
from mayatk.anim_utils._key_transfer import _KeyTransferInternal
from mayatk.anim_utils._tied_keys import _TiedKeysInternal
from mayatk.anim_utils._anim_layers import _AnimLayerInternal

# A public module constant since tie_keyframes shipped; kept importable here.
from mayatk.anim_utils._tied_keys import TIED_KEYS_ATTR  # noqa: F401


STANDARD_TRANSFORM_ATTRS: frozenset = ptk.STANDARD_TRANSFORM_ATTRS
"""Per-axis transform + visibility attributes (pythontk's shots vocabulary).

Used across the shots system and SmartBake to distinguish genuine
scene-content animation from custom trigger/marker attributes.
"""


class _AnimUtilsInternal(
    _KeyQueryInternal,
    _TimelineInternal,
    _CurveOptimizeInternal,
    _CurveSnapshotInternal,
    _TangentInternal,
    _KeyTimingInternal,
    _KeyEditInternal,
    _KeyTransferInternal,
    _TiedKeysInternal,
    _AnimLayerInternal,
):
    """The helper base of :class:`AnimUtils`, composed from its concept modules.

    Each ``_<concept>.py`` sibling holds one job's private helpers and the
    bodies of its public methods (``AnimUtils.<name>`` keeps the signature and
    docstring and returns ``cls._<name>(...)``). Composing them here keeps every
    helper reachable as ``AnimUtils._<helper>`` / ``_AnimUtilsInternal._<helper>``,
    the spelling the key-timing siblings, the shot engine and the tests use.
    """


class AnimUtils(_AnimUtilsInternal, ptk.HelpMixin):
    """Animation utilities for Maya.

    For help on this class use: AnimUtils.help()

    BEST PRACTICES FOR GETTING ANIMATION CURVES:
    ============================================

    When working with animation curves, use these methods to ensure you capture ALL curve types
    (including visibility, custom attributes, etc.):

    1. For simple object-to-curves conversion:
       curves = AnimUtils.objects_to_curves(objects, recursive=False)

    2. For common patterns (scene curves, selected keys, object curves):
       curves = AnimUtils.get_anim_curves(objects=None, selected_keys_only=False, recursive=False)

    3. Both methods use cmds.listConnections(type="animCurve") which properly captures all curve types.

    AVOID querying keyframes at the object level for curve operations:
       - cmds.keyframe(obj, query=True, timeChange=True) # May miss some attributes

    PREFERRED approach - work with curves directly:
       - Get curves first using objects_to_curves() or get_anim_curves()
       - Then query/modify the curves: cmds.keyframe(curve, query=True, timeChange=True)
    """

    #: Optimization levels for :meth:`optimize_keys`, least to most aggressive.
    #: The single source of truth every consumer reads -- the Scene Exporter's
    #: Optimize Keys combo, SmartBake's pass-through, and any headless caller --
    #: so a level added here reaches all of them without a second edit.  Each
    #: value is literally the ``optimize_keys`` kwargs that level means: the
    #: level is sugar over the primitive, never a replacement for it, and a
    #: caller that wants a combination no level names still passes kwargs.
    OPTIMIZE_LEVELS: Dict[str, Dict[str, Any]] = {
        # Delete curves whose value never changes; leave every surviving curve's
        # keys alone.  The conservative rung: nothing that carries motion is
        # touched, so it is safe on hand-animated curves whose flat sections are
        # deliberate holds.
        "static": {"remove_static_curves": True, "remove_flat_keys": False},
        # ... plus the redundant middle keys of a flat run.  What every caller
        # got before levels existed (see DEFAULT_OPTIMIZE_LEVEL).
        "flat": {"remove_static_curves": True, "remove_flat_keys": True},
        # ... plus filterCurve(keyReducer) within value_tolerance.  Lossy by
        # construction: it removes keys whose absence changes the curve by less
        # than the tolerance, which is a judgement about the tolerance.
        "simplify": {
            "remove_static_curves": True,
            "remove_flat_keys": True,
            "simplify_keys": True,
        },
        # Reduce smooth curves to their extrema with tangents refit against the
        # samples (:meth:`reduce_to_extremes`, selected by the negative tolerance).
        # The answer for per-frame BAKED output, where the other rungs have
        # almost nothing to delete -- a bake has no redundant flat keys to find.
        "extremes": {
            "remove_static_curves": True,
            "remove_flat_keys": True,
            "value_tolerance": -1.0,
        },
    }

    #: The level a bare ``True`` resolves to -- what every caller got before
    #: levels existed, so a bool keeps behaving exactly as it did.
    DEFAULT_OPTIMIZE_LEVEL: str = "flat"

    #: Retired level names -> the canonical key, warning until they go.
    #: ``"unbake"`` (until 2026-09-02) read as reversing a bake -- which is
    #: ``SmartBake.restore`` -- when the level only thins a bake to its
    #: extremes; a saved template or preset may still say it.
    _resolve_retired_level = staticmethod(
        ptk.Deprecation.values(
            {"unbake": "extremes"},
            what="AnimUtils optimize level",
            remove_in="0.20.0",
            since="2026-09-23",
        )
    )

    @staticmethod
    def scene_animation_range() -> Tuple[float, float]:
        """The scene's AUTHORED animation range, as ``(start, end)``.

        ``animationStartTime``/``animationEndTime`` -- never
        ``minTime``/``maxTime``, which is the playback slider the artist
        happens to have scrubbed in.  A narrowed slider is not a statement
        about the deliverable, and every consumer here wants the authored
        extent: the FBX bake range, the exporter's "outermost statement of
        intent" fallback when a set-scoped key query comes back empty, and a
        USD export's sampling window.

        The read half of :meth:`fit_playback_range`, which writes the same pair.
        """
        return AnimUtils._scene_animation_range()

    @classmethod
    def normalize_optimize_level(cls, level):
        """The canonical :attr:`OPTIMIZE_LEVELS` key *level* names, or None for OFF.

        Split out of :meth:`resolve_optimize_level` so a caller that wants to
        REPORT the level (a log line, a summary) names the same thing the pass
        actually ran -- ``"  Extremes "`` resolves correctly but should not be
        echoed back with the caller's spacing and case.

        Parameters:
            level: A key of :attr:`OPTIMIZE_LEVELS`, or a bool -- ``True`` for
                :attr:`DEFAULT_OPTIMIZE_LEVEL`, anything falsy for OFF.

        Raises:
            ValueError: *level* is a non-empty string naming no known level.
        """
        return cls._normalize_optimize_level(level=level)

    @classmethod
    def resolve_optimize_level(
        cls, level: Union[bool, str, None]
    ) -> Optional[Dict[str, Any]]:
        """Resolve an optimization level into :meth:`optimize_keys` kwargs.

        The seam between a UI/config choice and the primitive, so no consumer
        hard-codes a level's kwargs:

            kwargs = AnimUtils.resolve_optimize_level(level)
            if kwargs:
                AnimUtils.optimize_keys(objects, **kwargs)

        Parameters:
            level: A key of :attr:`OPTIMIZE_LEVELS`, or a bool -- ``True`` for
                :attr:`DEFAULT_OPTIMIZE_LEVEL`, anything falsy for OFF.

        Returns:
            The kwargs for that level, or None when it is OFF (so the caller
            skips the pass rather than running it with everything disabled).

        Raises:
            ValueError: *level* is a string naming no known level.  Loud rather
                than silently falling back: an unknown level is a config error,
                and a quiet default would optimize the user's curves at a
                setting they did not choose.
        """
        return cls._resolve_optimize_level(level=level)

    @classmethod
    def bake(
        cls,
        objects: Union[str, List[str]],
        attributes: Optional[Union[str, List[str]]] = None,
        time_range: Optional[Tuple[float, float]] = None,
        sample_by: float = 1.0,
        preserve_outside_keys: bool = True,
        simulation: bool = False,
        destination_layer: Optional[str] = None,
        remove_baked_attr_from_layer: bool = False,
        bake_on_override_layer: bool = False,
        minimize_rotation: bool = True,
        sparse_anim_curve_bake: bool = False,
        disable_implicit_control: bool = True,
        control_points: bool = False,
        shape: bool = False,
        only_keyed: bool = False,
    ) -> List[str]:
        """Bake animation on specified objects and attributes with smart grouping.

        Handles filtering valid attributes per object and grouping them for
        efficient batch execution.

        Parameters:
            objects: Object(s) to bake.
            attributes: Attribute name(s) to bake. If None, bakes all keyable.
            time_range: (start, end) tuple. If None, Maya's bakeResults
                default range is used (the existing keyed range — NOT the
                playback range).
            sample_by: Step size for baking keys.
            preserve_outside_keys: Keep keys outside the bake range.
            simulation: Perform simulation bake.
            destination_layer: Target animation layer name.
            remove_baked_attr_from_layer: Remove attributes from source layer.
            bake_on_override_layer: Bake onto the override layer.
            minimize_rotation: Ensure rotation continuity (Euler filter).
            sparse_anim_curve_bake: Use sparse baking.
            disable_implicit_control: Disable implicit control during bake.
            control_points: Bake control points.
            shape: Bake shapes.
            only_keyed: Only bake attributes that already have animation curves.

        Returns:
            List of objects that were baked successfully.
        """
        return cls._bake(
            objects=objects,
            attributes=attributes,
            time_range=time_range,
            sample_by=sample_by,
            preserve_outside_keys=preserve_outside_keys,
            simulation=simulation,
            destination_layer=destination_layer,
            remove_baked_attr_from_layer=remove_baked_attr_from_layer,
            bake_on_override_layer=bake_on_override_layer,
            minimize_rotation=minimize_rotation,
            sparse_anim_curve_bake=sparse_anim_curve_bake,
            disable_implicit_control=disable_implicit_control,
            control_points=control_points,
            shape=shape,
            only_keyed=only_keyed,
        )

    @staticmethod
    @ptk.Deprecation.parameter(
        "as_strings", drop=True, remove_in="0.20.0", since="2026-09-23"
    )
    def objects_to_curves(
        objects: Union[str, List[str]],
        recursive: bool = False,
        as_strings: bool = False,
        through_blends: bool = True,
    ) -> List[str]:
        """Converts objects into a list of animation curves.
        Optionally recurses through the objects to find animation curves on children.
        Ensures no duplicates are returned.

        Parameters:
            objects: Single object name or list of names (keyed objects or curves).
            recursive: Whether to recursively search through children of objects for curves.
            as_strings: Deprecated (warns; removed in 0.20.0) and has no
                effect -- results are always name strings.
            through_blends: Also return the curves a layered, constrained or
                unit-converted channel hides behind an animBlendNode / pairBlend /
                unitConversion (the default). A direct connection query sees
                only the intermediary, so every range-scoped key tool built on
                it skipped a layered channel. ``False`` reads direct
                connections only: the shot sequencer's sites, whose
                ``Detection`` classifies layer curves on its own terms, and
                the Scene Exporter's key tasks, whose baked layer SmartBake
                optimizes itself.

        Returns:
            A list of unique animation curve names.
        """
        return AnimUtils._objects_to_curves(
            objects=objects,
            recursive=recursive,
            as_strings=as_strings,
            through_blends=through_blends,
        )

    @classmethod
    def get_anim_curves(
        cls,
        objects: Optional[List[str]] = None,
        selected_keys_only: bool = False,
        recursive: bool = False,
    ) -> List[str]:
        """Get animation curves from objects, selected keys, or all scene curves.

        This is a higher-level convenience method that handles common patterns for getting
        animation curves. It properly handles visibility and all other attribute types by
        working directly with animation curve nodes rather than querying at the object level.

        This method should be used when you need to:
        - Get all curves in a scene
        - Get curves from selected graph editor keys
        - Get curves from specific objects (with optional recursion)

        Parameters:
            objects: Objects to get curves from. If None, uses all scene curves or selected keys.
            selected_keys_only: If True, gets curves from selected keys in graph editor.
                               Only applies when objects is None.
            recursive: Whether to recursively search through children of objects for curves.

        Returns:
            A list of unique animation curves.

        Example:
            # Get all animation curves in the scene
            all_curves = AnimUtils.get_anim_curves()

            # Get curves from selected keys
            selected_curves = AnimUtils.get_anim_curves(selected_keys_only=True)

            # Get curves from specific objects
            curves = AnimUtils.get_anim_curves(objects=cmds.ls(selection=True))

            # Get curves from objects and their children
            curves = AnimUtils.get_anim_curves(objects=cmds.ls(selection=True), recursive=True)
        """
        return cls._get_anim_curves(
            objects=objects, selected_keys_only=selected_keys_only, recursive=recursive
        )

    @classmethod
    def snapshot_curves(
        cls, objects: Union[str, List[str]], recursive: bool = True
    ) -> Dict[str, Any]:
        """Stash every animation curve driving *objects*, so it can be put back.

        The counterpart of :meth:`restore_curves`, and the animation half of
        what the texture pass gets from staging copies: a caller may edit keys
        destructively -- optimize, snap, tie, bake -- and hand the scene back
        exactly as it was.

        The stash is a DUPLICATE of each curve node, which is why this is
        exact rather than approximately exact: key times, values, tangent
        types, weights, the curve's ``weightedTangents`` flag, its pre/post
        infinity and its node type all come along without being enumerated and
        re-applied one property at a time. The duplicate is disconnected
        (``inputConnections=False``), so it drives nothing while it waits.

        Nothing is locked or hidden: the stash nodes are ordinary DG nodes
        named ``<curve>__snapshot#`` and :meth:`restore_curves` deletes them.
        A caller that abandons a snapshot leaks those nodes, so pair the calls
        (the scene exporter stages the restore with
        ``stage_deferred_restore`` and therefore covers every exit path).

        Parameters:
            objects: Objects (or curves) whose animation should be captured.
            recursive: Include curves on the objects' descendants. Default
                True -- the opposite of :meth:`objects_to_curves`, because a
                caller asking to protect an object's animation means the
                animation that will actually be edited, and an export set
                names roots.

        Returns:
            An opaque snapshot dict for :meth:`restore_curves`. Empty
            ``records`` when nothing is animated, which restores as a no-op.
        """
        return cls._snapshot_curves(objects=objects, recursive=recursive)

    @classmethod
    def restore_curves(cls, snapshot: Optional[Dict[str, Any]]) -> int:
        """Put the animation captured by :meth:`snapshot_curves` back, exactly.

        A surviving ordinary curve has its stash swapped in
        (:meth:`_swap_in_stash`): wired to the same plugs under the same name
        and UUID, so to an animation layer, a pairBlend, a driven-key setup or
        another restore holding the curve nothing changed but its content.
        Every path hands back the UUID the snapshot RECORDED
        (:meth:`_take_back_uuid`): the node under the curve's name may be a
        rebuild carrying one of its own. A curve that cannot be swapped (referenced, locked, or carrying a
        connection the swap would drop) keeps its node and has its content
        replaced in place; a curve the caller deleted is rebuilt by
        reconnecting its stash where it sat.

        Always deletes the stash nodes, including on the paths where the
        restore itself fails, because a leaked stash is a curve-shaped node
        sitting in the artist's scene.

        Side effect worth knowing: the in-place path goes through
        ``copyKey``/``pasteKey``, which use Maya's single global key clipboard
        -- so whatever the user had copied there is replaced. That is the price
        of replacing a curve's content exactly rather than re-applying it key
        by key, and it is why this is an export-time operation rather than
        something to call from an interactive tool.

        Parameters:
            snapshot: The dict from :meth:`snapshot_curves`. ``None`` or an
                empty snapshot restores nothing and reports 0.

        Returns:
            The number of curves put back.
        """
        return cls._restore_curves(snapshot=snapshot)

    @classmethod
    @ptk.Deprecation.parameter(
        "as_strings", drop=True, remove_in="0.20.0", since="2026-09-23"
    )
    def get_static_curves(
        cls,
        objects: List[str],
        value_tolerance: float = 1e-5,
        recursive: bool = False,
        as_strings: bool = False,
    ) -> List[str]:
        """Detects static curves (curves with constant values) that are safe
        to delete.

        A static curve is one where all keyframe values are identical
        (within *value_tolerance*).  However, if the constant value
        differs from the driven attribute's default value, removing the
        curve would change the object's resting state (e.g. a
        constraint-baked constant position would revert to zero).  Such
        curves are **excluded** from the result.

        Parameters:
            objects: List of nodes (curves or objects).
            value_tolerance: The value tolerance to consider for static curves (difference between keyframe values).
            recursive: Whether to recursively search through children of objects for curves.
            as_strings: Deprecated (warns; removed in 0.20.0) and has no
                effect -- results are always name strings.

        Returns:
            A list of static curves that are safe to delete.
        """
        return cls._get_static_curves(
            objects=objects,
            value_tolerance=value_tolerance,
            recursive=recursive,
            as_strings=as_strings,
        )

    @classmethod
    @ptk.Deprecation.parameter(
        "as_strings", drop=True, remove_in="0.20.0", since="2026-09-23"
    )
    @CoreUtils.undoable
    def get_redundant_flat_keys(
        cls,
        objects: List[str],
        value_tolerance: float = 1e-5,
        remove: bool = False,
        recursive: bool = False,
        as_strings: bool = False,
        time_range: Optional[Tuple[float, float]] = None,
        selected_only: bool = False,
    ) -> List[Tuple[Any, List[float]]]:
        """Detects redundant flat keys in curves and optionally deletes them.

        A "flat segment" is a run of 3+ consecutive keys whose values all
        fall within ``value_tolerance`` of the first key in the run.  The
        interior keys are redundant because the boundary pair alone
        reproduces the constant value.

        Parameters:
            objects: List of nodes (curves or objects).
            value_tolerance: The value tolerance to consider for redundant flat keys.
            remove: If True, the redundant keys are deleted.
            recursive: Whether to recursively search through children of objects for curves.
            as_strings: Deprecated (warns; removed in 0.20.0) and has no
                effect -- curve names are always strings.
            time_range: ``(start, end)`` window a key must fall inside to be
                removable; None considers every interior key.
            selected_only: Only keys selected in the Graph Editor are removable.

        Returns:
            A list of ``(curve, [redundant_times])`` tuples.

        Either scope leaves the curve OUTSIDE it byte-identical, tangents
        included: the global auto-to-fixed freeze below is a whole-curve
        export concern and is skipped when a scope is given, so only the keys
        left facing a vanished run are re-typed.  This is what makes the pass
        safe to offer on a key SELECTION (the shot sequencer's Simplify) as
        well as on a scene.  A per-frame-dense curve (a ``sample_by=1`` bake)
        gets the same boundary-only freeze unscoped: every other survivor
        keeps neighbours one frame away, where no tangent algorithm can move
        a frame.

        Removal goes through ``MFnAnimCurve``, recorded on the undo queue by
        :class:`~mayatk.core_utils.undo_recorder.UndoRecorder`, so one undo
        reverts the call. It went through cmds while the queue recorded, at
        ~0.9 ms per ``cutKey`` range on a 1134-key layer curve -- a
        production-scale flat pass has ~21k runs (20 s) plus two boundary
        ``keyTangent`` edits per run (14 s) -- where the om2 form of the
        identical edit is under a second.
        """
        return cls._get_redundant_flat_keys(
            objects=objects,
            value_tolerance=value_tolerance,
            remove=remove,
            recursive=recursive,
            as_strings=as_strings,
            time_range=time_range,
            selected_only=selected_only,
        )

    @classmethod
    @ptk.Deprecation.parameter(
        "as_strings", drop=True, remove_in="0.20.0", since="2026-09-23"
    )
    @ptk.Deprecation.parameter(
        "time_tolerance", drop=True, remove_in="0.20.0", since="2026-09-23"
    )
    def simplify_curve(
        cls,
        objects: List[str],
        value_tolerance: float = 0.001,
        time_tolerance: float = 0.001,
        recursive: bool = False,
        as_strings: bool = False,
        time_range: Optional[Tuple[float, float]] = None,
        selected_only: bool = False,
    ) -> List[str]:
        """Simplify curves by removing keys that don't contribute to shape.

        Uses Maya's ``filterCurve`` with the ``keyReducer`` filter, which
        evaluates each key's contribution to the overall curve shape and
        removes keys whose absence would change the curve by less than
        *value_tolerance*.  This is far more effective than ``cmds.simplify``
        for post-bake cleanup because it handles smooth transitions (not
        just per-key value differences).

        Scope narrows two ways, and both keep the boundary keys: *time_range*
        reduces only inside the window, *selected_only* only the keys the
        Graph Editor has selected.  They compose with the curve list, which is
        the attribute-level scope -- so a caller holding a key selection (the
        shot sequencer's Simplify) reduces exactly what the user highlighted
        and nothing on the attributes beside it.

        Parameters:
            objects: List of nodes (curves or objects).
            value_tolerance: Maximum allowed value deviation when removing
                a key.  Maps to ``filterCurve -precision``.
            time_tolerance: Deprecated (warns; removed in 0.20.0) and has
                no effect -- ``filterCurve`` weighs values only.
            recursive: Whether to recursively search children for curves.
            as_strings: Deprecated (warns; removed in 0.20.0) and has no
                effect -- curve names are always strings.
            time_range: ``(start, end)`` to reduce within; None is the whole
                curve.  The two ends survive the pass.
            selected_only: Reduce only the currently selected keys
                (``filterCurve -selectedKeys``).  A curve with no selected
                key is left alone.

        Returns:
            A list of curves that were simplified.
        """
        return cls._simplify_curve(
            objects=objects,
            value_tolerance=value_tolerance,
            time_tolerance=time_tolerance,
            recursive=recursive,
            as_strings=as_strings,
            time_range=time_range,
            selected_only=selected_only,
        )

    @classmethod
    @CoreUtils.undoable
    def repair_corrupted_curves(
        cls,
        objects: Optional[Union[str, List[str]]] = None,
        recursive: bool = True,
        delete_corrupted: bool = False,
        fix_infinite: bool = True,
        fix_invalid_times: bool = True,
        time_range_threshold: float = 1e6,
        value_threshold: float = 1e6,
        quiet: bool = False,
    ) -> Dict[str, Any]:
        """Legacy wrapper maintained for backwards compatibility.

        The implementation now lives in :class:`AnimCurveDiagnostics`.
        """
        return cls._repair_corrupted_curves(
            objects=objects,
            recursive=recursive,
            delete_corrupted=delete_corrupted,
            fix_infinite=fix_infinite,
            fix_invalid_times=fix_invalid_times,
            time_range_threshold=time_range_threshold,
            value_threshold=value_threshold,
            quiet=quiet,
        )

    @classmethod
    @CoreUtils.undoable
    def reduce_to_extremes(
        cls,
        objects: Optional[Union[str, List[str]]] = None,
        value_tolerance: float = 0.001,
        recursive: bool = True,
        quiet: bool = False,
        stats: Optional[dict] = None,
        max_error: Optional[float] = None,
    ) -> List[str]:
        """Reduce baked curves to their shape-defining keys and refit the tangents.

        A per-frame bake thinned to its shape, not undone: the keys stay on
        the objects and only the tweens go (reversing a bake is
        ``SmartBake.restore``).  Each curve keeps its endpoints, peaks,
        valleys and hold boundaries, plus -- wherever the refit curve would
        miss a baked sample by more than *max_error* -- that sample
        (``ptk.MathUtils.reduce_samples``); the tweens are deleted and the
        survivors get ``fixed`` tangents fitted by least squares against the
        deleted samples, so the sparse curve traces the baked motion within
        the bound.  A hold stays exactly flat -- its facing tangents are
        ``flat`` and that key's tangents are broken; everywhere else the tangents
        are unified.  Curves are made non-weighted.  Curves carrying stepped
        tangents are left untouched (a step has no tween to refit) and are not
        returned.

        Driven (unitless-input) curves are reduced per driver unit.  Tangents
        are written through ``MFnAnimCurve`` (exact in UI units per frame for
        every curve type) and recorded with the rest of the edit, so one undo
        reverts the call; :meth:`optimize_keys` runs it for
        ``value_tolerance < 0``.

        Parameters:
            objects: Objects or curves to reduce; None means every keyed
                transform in the scene.
            value_tolerance: Consecutive samples closer than this are one flat
                step, and a segment within it of its start value is a hold.
            recursive: Whether to search through children of objects.
            quiet: If True, suppress output messages.
            stats: If provided, receives ``reduced`` (curve count),
                ``reduce_keys_removed`` and ``reduce_max_error`` (largest
                deviation of the refit curve from the baked samples, UI units).
            max_error: Largest allowed deviation of the refit curve from the
                bake, in the curve's UI units.  None (default) allows 1% of
                each curve's own amplitude; 0 keeps the extrema alone.
                Extrema alone hold a sine to ~2% but not a segment with an
                inflection between its extrema -- measured 6% of amplitude on
                plain constraint bakes under an animated parent, 293 of 1063
                curves over 5% -- which the refinement brings inside the bound
                for a few keys per curve.

        Returns:
            The curves that were reduced.
        """
        return cls._reduce_to_extremes(
            objects=objects,
            value_tolerance=value_tolerance,
            recursive=recursive,
            quiet=quiet,
            stats=stats,
            max_error=max_error,
        )

    @classmethod
    @ptk.Deprecation.parameter(
        "time_tolerance", drop=True, remove_in="0.20.0", since="2026-09-23"
    )
    @CoreUtils.undoable
    def optimize_keys(
        cls,
        objects: Union[str, List[str]],
        value_tolerance: float = 0.001,
        time_tolerance: float = 0.001,
        remove_flat_keys: bool = True,
        remove_static_curves: bool = True,
        simplify_keys: bool = False,
        recursive: bool = True,
        quiet: bool = False,
        stats: Optional[dict] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        through_blends: bool = True,
    ) -> List[str]:
        """Optimize animation keys for the given objects by removing static curves,
        redundant flat keys, and simplifying curves.

        A negative ``value_tolerance`` (``-1``) selects **extremes** mode: after
        the static-curve pass, every smooth curve is reduced to its endpoints,
        peaks, valleys and hold boundaries with tangents refit to the baked
        motion (:meth:`reduce_to_extremes`); stepped curves still get the flat-key
        pass.  ``simplify_keys`` is ignored in that mode and the static/flat
        tolerance falls back to the default.

        Parameters:
            objects (str, node, or list): The objects to optimize.
            value_tolerance (float): Tolerance for value comparison; negative
                selects extremes mode.
            time_tolerance (float): Deprecated (warns; removed in 0.20.0) and
                has no effect -- no pass compared times with it; its one
                reader was :meth:`simplify_curve`'s, which is ignored too.
            remove_flat_keys (bool): Whether to remove redundant flat keys.
            remove_static_curves (bool): Whether to remove static curves.
            simplify_keys (bool): Whether to simplify curves.
            recursive (bool): Whether to search through children of objects.
            quiet (bool): If True, suppress output messages.
            through_blends (bool): Also optimize the curves behind a layered or
                constrained channel's blend node (default). See
                :meth:`objects_to_curves`.
            stats (dict, optional): If provided, populated with
                ``keys_before``, ``keys_after``, ``curves_before``,
                ``curves_after``, ``static_deleted``, ``flat_removed``,
                ``simplified``, and ``auto_frozen`` counts (plus the
                :meth:`reduce_to_extremes` stats in extremes mode).

        Tangents: surviving keys of any curve that lost keys are frozen from
        ``auto`` to ``fixed`` (FBX reinterprets ``auto`` on sparse curves).  A
        per-frame-dense curve that lost no key keeps its tangent types unless
        ``simplify_keys`` is on, since at frame resolution the type cannot
        change the motion and freezing it is the bulk of a flat pass's cost.

        Returns:
            list: A list of modified curve names (strings).
        """
        return cls._optimize_keys(
            objects=objects,
            value_tolerance=value_tolerance,
            time_tolerance=time_tolerance,
            remove_flat_keys=remove_flat_keys,
            remove_static_curves=remove_static_curves,
            simplify_keys=simplify_keys,
            recursive=recursive,
            quiet=quiet,
            stats=stats,
            progress_callback=progress_callback,
            through_blends=through_blends,
        )

    @staticmethod
    def keyed_nodes(objects: Union[str, List[str]]) -> List[str]:
        """The subset of *objects* ``cmds.keyframe`` can find keys on.

        The cheap prefilter in front of a per-object key query, which pays
        the same per node whether or not it is animated. Names come back
        spelled and ordered as *objects* gave them, and an object that is
        itself an animation curve is kept: ``keyframe`` reads a curve
        directly.

        Parameters:
            objects: Object names (DAG paths or DG nodes); a single string is
                accepted.

        Returns:
            The animated subset of *objects*, in input order.
        """
        return AnimUtils._keyed_nodes(objects=objects)

    @staticmethod
    def has_keyframes(sources: Union[str, List[str]]) -> bool:
        """Whether *sources* (objects or curves) carry any key at all.

        Never the key times, nor a command that walks them: a wired curve
        answers from its own key count, and only a channel behind a layer
        blend asks ``keyframeCount`` -- one object at a time, so the first
        keyed one settles it. On a post-bake production subtree (2,000 dense
        curves, 2.18 M keys) listing the times cost 12 s and one batched
        ``keyframeCount`` 4.5 s, for the yes/no every "is there animation?"
        gate in the exporter asks (2026-09-13/14).
        """
        return AnimUtils._has_keyframes(sources=sources)

    @staticmethod
    def keyframe_range(
        sources: Union[str, List[str]],
    ) -> Optional[Tuple[float, float]]:
        """``(first, last)`` key time over *sources*, or ``None`` with no keys.

        What a bake range, a tie's bookends and the shear scan's frame grid
        need, read off the curves' own ends (:meth:`curve_key_spans`):
        ``keyframeCount`` + ``findKeyframe`` walk every key instead (12.4 s on
        a post-bake production subtree, 2026-09-14) and
        :meth:`get_keyframe_times` ``as_range=True`` lists them. A channel
        behind a layer blend reads the curve ``cmds.keyframe`` resolves for it
        -- the top layer's, never the base -- asked for by name.
        """
        return AnimUtils._keyframe_range(sources=sources)

    @staticmethod
    def curve_key_spans(
        curves: List[str],
        windows: Sequence[Tuple[Optional[float], Optional[float]]],
    ) -> List[Optional[Tuple[float, float]]]:
        """Per window, the first and last key time over *curves* inside it.

        Answered from each curve's own key index -- its ends directly, a bound
        that falls inside it by ``findClosest`` -- so no key in between is
        listed or walked. ``cmds.keyframe`` / ``findKeyframe`` walk every key
        their query covers: 3.7 s to seek both ends of a 2,000-curve override
        layer, 10 s a pass to list a production scene's keys for its take
        spans (2026-09-14); milliseconds here.

        Parameters:
            curves: Animation curve names. A missing one is skipped, and so is
                a curve keyed on a driver instead of time: a driven key's
                inputs are driver values, not frames.
            windows: ``(start, end)`` frame pairs in the current time unit,
                inclusive; ``None`` leaves that side unbounded.

        Returns:
            One entry per window: ``(first, last)``, or ``None`` when no key
            of any curve falls inside it.
        """
        return AnimUtils._curve_key_spans(curves=curves, windows=windows)

    @staticmethod
    def get_keyframe_times(
        sources: Union[str, List[str]],
        mode: str = "all",
        from_curves: Optional[bool] = None,
        as_range: bool = False,
        time_range: Optional[Tuple[float, float]] = None,
    ) -> Union[List[float], Tuple[float, float], None]:
        """Get keyframe times from objects or curves with flexible filtering options.

        This is a low-level utility for extracting keyframe time values. For getting
        animation curves themselves, use objects_to_curves() or get_anim_curves().

        Parameters:
            sources: Objects or animation curves to get keyframe times from.
            mode: How to select keyframes. Options:
                - "all": Get all keyframes (default)
                - "selected": Get only selected keyframes in graph editor
                - "selected_or_all": Try selected first, fallback to all if none selected
            from_curves: If True, treats sources as curves. If False, treats as objects.
                        If None (default), auto-detects based on node type.
            as_range: If True, returns (min_time, max_time) tuple. If False, returns sorted list.
            time_range: Optional (start, end) tuple to filter keyframes within a range.

        Returns:
            - List[float]: Sorted unique keyframe times (if as_range=False)
            - Tuple[float, float]: (start_time, end_time) range (if as_range=True)
            - None: If no keyframes found
        """
        return AnimUtils._get_keyframe_times(
            sources=sources,
            mode=mode,
            from_curves=from_curves,
            as_range=as_range,
            time_range=time_range,
        )

    @staticmethod
    def get_driver_animation_range(
        node: str,
        driver_type: str = "auto",
    ) -> List[float]:
        """Get keyframe times from a driver node's animation or its targets.

        Traces through different driver types (constraints, driven keys,
        expressions, IK, motion paths) to find the animation range of
        the ultimate source.

        Parameters:
            node: The driver node to query.
            driver_type: The type of driver. Options:
                - "auto": Auto-detect the node type
                - "constraint": Query constraint target animation
                - "driven_key": Query the driver of the SDK
                - "expression": Query expression input animation
                - "ik": Query IK handle/pole vector animation
                - "matrix": Walk an offsetParentMatrix network upstream
                  for animCurves (SmartBake matrix-drive analysis)
                - "motion_path": Query uValue animation
                - "inherited_visibility": Query an ancestor ``.visibility``
                  driver node (SmartBake's inherited-visibility analysis)
                - "inherited_visibility_plugs": Query an ancestor
                  ``<transform>.visibility`` PLUG (same analysis, plug form)

        Returns:
            List of keyframe times from the driver's animation.
            Empty list if no animation found.

        Example:
            >>> times = AnimUtils.get_driver_animation_range("pCube1_parentConstraint1")
            >>> print(min(times), max(times))  # 1.0 100.0
        """
        return AnimUtils._get_driver_animation_range(node=node, driver_type=driver_type)

    @staticmethod
    def get_tangent_info(attr_name: str, time: float) -> Dict[str, Any]:
        """Get tangent information (types, angles, and weights) for a given attribute at a specific time.

        Parameters:
            attr_name (str): The name of the attribute.
            time (float): The time at which to query the tangent information.

        Returns:
            Dict[str, Any]: A dictionary containing tangent information.
                Empty dict when no key exists at *time* (``set_tangent_info``
                treats an empty dict as a no-op).
        """
        return AnimUtils._get_tangent_info(attr_name=attr_name, time=time)

    @staticmethod
    def set_tangent_info(
        attr_name: str, time: float, tangent_info: Dict[str, Any]
    ) -> None:
        """Restore tangent information on a keyframe.

        Applies tangent types in a separate call after angles/weights so that
        type-specific tangents (e.g. stepped) are not overridden by the
        angle/weight values which would implicitly force the type to 'fixed'.

        Parameters:
            attr_name (str): The attribute name.
            time (float): The time of the keyframe.
            tangent_info (Dict[str, Any]): Tangent dict from ``get_tangent_info``.
        """
        return AnimUtils._set_tangent_info(
            attr_name=attr_name, time=time, tangent_info=tangent_info
        )

    @staticmethod
    @CoreUtils.undoable
    def step_keys(
        objects=None,
        keys=None,
        tangent: str = "out",
        resolution_order: Optional[Tuple[str, ...]] = None,
    ) -> dict:
        """Set stepped tangents on animation keys.

        Parameters:
            objects: Transforms / anim curves to operate on.  Falls back
                     to ``cmds.ls(selection=True)`` when *None*.
            keys: Which keys to step.  Accepted values:

                  - ``None`` (default) — step **all** keys on *objects*.
                  - ``"auto"`` — smart cascade via :meth:`_resolve_keys`:
                    uses selected keys if any (narrowed by Channel Box
                    highlights), falls back to Channel Box attributes,
                    then current-frame keys, then all keys.
                  - A ``float`` or ``int`` — treated as a time; only keys
                    at that frame are stepped.
                  - A ``list[str]`` — treated as animation-curve node
                    names (e.g. from ``cmds.keyframe(selected=True,
                    name=True)``); those curves are stepped directly
                    and *objects* is ignored.
                  - A ``dict[str, list[float] | None]`` — mapping of
                    curve names to specific times.  A *None* value means
                    step every key on that curve.
            tangent: Which tangent(s) to set stepped.
                  - ``"out"`` (default) — out-tangent ``step``.
                  - ``"in"`` — in-tangent ``stepnext``.
                  - ``"both"`` — both out ``step`` and in ``stepnext``.
            resolution_order: Strategies to try for ``"auto"`` mode.
                Default: ``("selected", "channel_box", "current_frame",
                "all")``.  See :meth:`_resolve_keys`.

        Returns:
            dict: ``{"curves": int, "objects": int}`` counts.
        """
        return AnimUtils._step_keys(
            objects=objects,
            keys=keys,
            tangent=tangent,
            resolution_order=resolution_order,
        )

    @staticmethod
    def set_current_frame(
        time: Optional[float] = None,
        update: bool = True,
        relative: bool = False,
        snap_mode: Optional[str] = None,
        invert_snap: bool = False,
    ) -> float:
        """Set the current frame on the timeslider with optional snapping.

        Parameters:
            time: The desired frame number or offset. If None, uses current time.
            update: If True (default), the scene evaluates at the new time;
                if False, only the time slider moves (the world is not updated).
            relative: If True, the frame will be moved relative to its current position.
            snap_mode: Snapping mode ('nearest', 'preferred', 'aggressive', etc.).
            invert_snap: If True, swaps directional snap modes ('floor' <-> 'ceil').
                Has no effect on other snap modes.

        Returns:
            float: The final time that was set.
        """
        return AnimUtils._set_current_frame(
            time=time,
            update=update,
            relative=relative,
            snap_mode=snap_mode,
            invert_snap=invert_snap,
        )

    @staticmethod
    @CoreUtils.undoable
    def move_keys_to_frame(
        objects=None,
        frame=None,
        time_range=None,
        selected_keys_only=False,
        retain_spacing=False,
        channel_box_attrs_only=False,
        align: str = "auto",
    ):
        """Move keyframes to the given frame with comprehensive control options.

        Parameters:
            objects (list, optional): Objects to move keys for. If None, uses selection.
            frame (int or float, optional): The frame to move keys to.
                                                   If None, uses the current time.
            time_range (tuple, optional): (start_frame, end_frame) to limit which keys to move.
                                         If None, moves all keys.
            selected_keys_only (bool): If True, only moves selected keys from the graph editor.
                                 If False, moves all keys in the specified time range.
            retain_spacing (bool): If True, maintains relative spacing between objects.
                                   If False, moves each object's first key to the target frame.
            channel_box_attrs_only (bool): If True, only affects attributes selected in the channel box.
                                    Works in combination with selected_keys_only.
            align (str): Which end of the key range lands on *frame*.
                ``"start"`` — the earliest key aligns to *frame*.
                ``"end"`` — the latest key aligns to *frame*.
                ``"auto"`` (default) — if the midpoint of the key range is
                before *frame*, behaves like ``"end"``; otherwise ``"start"``.
        Returns:
            bool: True if keys were moved successfully, False otherwise.
        """
        return AnimUtils._move_keys_to_frame(
            objects=objects,
            frame=frame,
            time_range=time_range,
            selected_keys_only=selected_keys_only,
            retain_spacing=retain_spacing,
            channel_box_attrs_only=channel_box_attrs_only,
            align=align,
        )

    @staticmethod
    @CoreUtils.undoable
    def set_keys_for_attributes(
        objects, target_times=None, refresh_channel_box=False, **kwargs
    ):
        """Sets keyframes for the specified attributes on given objects at given times.

        Automatically detects whether to apply the same values to all objects (shared mode)
        or different values per object (per-object mode) based on the data structure.

        Parameters:
            objects (list): The objects to set the keyframes on.
            target_times (int/list, optional): Frame(s) to set keys at. Default: current time.
            refresh_channel_box (bool, optional): Update channel box after setting keys. Default: False.
            **kwargs: Can be used in two modes:

                SHARED MODE - Same values to all objects:
                    Attribute names as keys with their values.

                PER-OBJECT MODE - Different values per object:
                    Pass the per-object dictionary unpacked. The function auto-detects this mode when
                    the first kwarg value is a dict containing attribute/value pairs.
                    Format when unpacking: {obj_name: {attr: value, ...}, ...}

        Example:
            # Shared mode - same values to all objects
            set_keys_for_attributes([obj1, obj2], translateX=5, translateY=10)
            set_keys_for_attributes(objects, target_times=[10, 15, 20], translateX=5)

            # Per-object mode - different values per object (auto-detected)
            data = {'pCube1': {'translateX': 5.0}, 'pCube2': {'translateX': 10.0}}
            set_keys_for_attributes([obj1, obj2], **data)

            # With times and refresh
            set_keys_for_attributes(objects, target_times=10, refresh_channel_box=True, translateX=5)
        """
        return AnimUtils._set_keys_for_attributes(
            objects=objects,
            target_times=target_times,
            refresh_channel_box=refresh_channel_box,
            **kwargs,
        )

    @staticmethod
    def filter_objects_with_keys(
        objects: Optional[Union[str, List[str]]] = None,
        keys: Optional[List[str]] = None,
    ) -> List[str]:
        """Filter the given objects for those with specific keys set. If no objects are given, use all scene objects. If no specific keys are given, check all keys.

        Parameters:
            objects: The objects (or their names) to filter. Can be a single object or a list of objects. If None, all scene objects are used.
            keys: Specific keys to check for. If none are provided, all keys are checked.

        Returns:
            List of transforms with the specified keys set.
        """
        return AnimUtils._filter_objects_with_keys(objects=objects, keys=keys)

    @staticmethod
    def scene_has_animation() -> bool:
        """True if the scene contains any time-based animation a playblast would capture.

        Looks for time-input animation curves (``animCurveTL/TA/TU/TT``) — keyed
        attributes driven by the timeline. This deliberately covers *all* keyed
        content (transforms, cameras, blendshape morphs, visibility, lights,
        materials, …), not just transforms, and excludes driven keys
        (``animCurveU*``), whose motion depends on a driver rather than time.

        Unlike :meth:`ShotStore.has_animation` (transform-scoped, for shot
        detection), this is the canonical "does anything move over time?" check —
        used to early-out of playblast/preview captures on a static scene.

        It is intentionally lightweight: it checks for the *existence* of time
        curves, not whether they carry meaningful (non-flat) motion. Returns
        ``False`` when Maya is unavailable.
        """
        return AnimUtils._scene_has_animation()

    @classmethod
    @CoreUtils.undoable
    def adjust_key_spacing(
        cls,
        objects: Optional[List[str]] = None,
        spacing: int = 1,
        time: Optional[int] = 0,
        relative: bool = True,
        preserve_keys: bool = False,
        selected_keys_only: bool = False,
        exact_gap: bool = False,
        prevent_collisions: bool = True,
    ):
        """Adjusts the spacing between keyframes for specified objects at a given time,
        with an option to preserve and adjust a keyframe at the specified time.

        Operates on animation curves directly, supporting both object-level and
        graph-editor-selection workflows.

        Parameters:
            objects (Optional[List[str]]): Objects to adjust keyframes for.
                If None, adjusts all scene objects (or selected keys when selected_keys_only is True).
                When selected_keys_only is True, objects is ignored and curves are
                determined entirely from the graph editor selection.
            spacing (int): Spacing to add or remove. Negative values remove spacing.
                When exact_gap is True, this is the desired gap size in frames
                (must be positive) and the actual shift is calculated so the first
                key after the start time lands exactly at (start + spacing).
            time (Optional[int]): Time at which to start adjusting spacing.
                                 If None, uses the current playhead time.
            relative (bool): If True, time is relative to the current frame.
            preserve_keys (bool): Preserves and adjusts a keyframe at the specified time
                if it exists. Only considers keys visible to the current query
                (i.e. when selected_keys_only is True, only selected keys are preserved).
            selected_keys_only (bool): If True, only affects selected keyframes in the graph editor.
                Overrides objects — the curve set comes from the graph editor selection.
            exact_gap (bool): If True, calculates the actual shift amount so that the
                first key after the start time is moved exactly to (start + spacing),
                clearing a precise range. Spacing must be positive in this mode.
            prevent_collisions (bool): If True (default), performs a dry-run
                collision check before moving any keys. If any destination time
                would land on an existing unmoved key, the entire operation is
                aborted and a warning is issued.
        """
        return cls._adjust_key_spacing(
            objects=objects,
            spacing=spacing,
            time=time,
            relative=relative,
            preserve_keys=preserve_keys,
            selected_keys_only=selected_keys_only,
            exact_gap=exact_gap,
            prevent_collisions=prevent_collisions,
        )

    @staticmethod
    @CoreUtils.undoable
    def add_intermediate_keys(
        objects: Union[str, List[str]],
        time_range: Optional[Union[int, Tuple[int, int]]] = None,
        percent: Optional[float] = None,
        include_flat: bool = False,
        ignore: Union[str, List[str], None] = None,
    ) -> None:
        """Keys selected or animated attributes on given object(s) within a time range.
        If attributes are selected in the channel box, only those will be keyed.
        If time_range is not specified, automatically detects the first and last keyframe per attribute.

        Parameters:
            objects (str/list): One or more objects to key.
            time_range (int, tuple, or None):
                - None: Auto-detects range from first to last keyframe per attribute
                - int: End frame (starts from first keyframe)
                - tuple (start, end): Explicit start and end frames
            percent (float): Optional percent (0-100) of frames to key, evenly distributed.
            include_flat (bool): If False, skips keys where value doesn't vary across time.
            ignore (str/list, optional): Attribute name(s) to ignore when adding keys.
                E.g., 'visibility' or ['visibility', 'translateX']. Curves connected to these
                attributes will not have intermediate keys added.
        """
        return AnimUtils._add_intermediate_keys(
            objects=objects,
            time_range=time_range,
            percent=percent,
            include_flat=include_flat,
            ignore=ignore,
        )

    @staticmethod
    @CoreUtils.undoable
    def remove_intermediate_keys(
        objects: Union[str, List[str]],
        time_range: Optional[Union[int, Tuple[int, int]]] = None,
        ignore: Union[str, List[str], None] = None,
        attributes: Union[str, List[str], None] = None,
    ) -> int:
        """Removes all intermediate keyframes, keeping only the first and last key on each attribute.
        If attributes are selected in the channel box, only those will be affected.
        Automatically detects the keyframe range for each attribute if time_range is not specified.

        Parameters:
            objects (str/list): One or more objects to remove intermediate keys from.
            time_range (int, tuple, or None):
                - None: Auto-detects range from first to last keyframe per attribute
                - int: End frame (starts from first keyframe)
                - tuple (start, end): Explicit start and end frames
            ignore (str/list, optional): Attribute name(s) to ignore when removing keys.
                E.g., 'visibility' or ['visibility', 'translateX']. Curves connected to these
                attributes will not have intermediate keys removed.
            attributes (str/list, optional): Attribute name(s) to limit the strip to,
                stated outright instead of read off the Channel Box. Takes precedence
                over the Channel Box highlight, so a caller that already knows which
                channels the user picked (the shot sequencer's key selection) does not
                depend on a UI highlight having landed.

        Returns:
            int: Number of keyframes removed.

        Example:
            # Remove all intermediate keys, keeping only first and last
            remove_intermediate_keys(cmds.ls(selection=True))

            # Remove intermediate keys for channel box selected attributes only
            remove_intermediate_keys([obj1, obj2])

            # Remove intermediate keys except for visibility
            remove_intermediate_keys(cmds.ls(selection=True), ignore='visibility')

            # Remove intermediate keys within specific range
            remove_intermediate_keys(cmds.ls(selection=True), time_range=(10, 50))
        """
        return AnimUtils._remove_intermediate_keys(
            objects=objects, time_range=time_range, ignore=ignore, attributes=attributes
        )

    @staticmethod
    @CoreUtils.undoable
    def invert_keys(
        objects=None,
        time=None,
        relative=True,
        delete_original=False,
        mode="horizontal",
        value_pivot=0.0,
    ):
        """Invert keyframes, preferring selected keys over all keys.

        When any keys are selected in the graph editor, only selected keys
        are inverted (a RuntimeError is raised if none of them belong to
        *objects*).  With no graph-editor selection, all keys on *objects*
        are inverted.

        When `time` is None (default) the keys are mirrored **in place**: the
        animation reverses within its own key range. That is a move, not a
        copy — `relative` and `delete_original` are ignored. When `time` is
        given, a reversed copy is placed at that time instead, and the source
        keys are kept unless `delete_original` is True.

        Tangents travel with the keys.  On a time flip the handles swap sides
        and a stepped hold is re-homed to the key that now precedes its
        segment, as its opposite (``step`` <-> ``stepnext``); types Maya
        recomputes itself (``auto``, ``linear``, ``flat``, ...) stay their own
        type rather than being frozen into ``fixed`` handles.

        Parameters:
            objects (str/list, optional): Objects whose keys to invert.
                Defaults to the current selection.
            time (int, optional): Start time for the reversed copy.
                If None, mirrors the keys in place (no copy is made).
            relative (bool): When True, time is treated as an offset from the last key.
                Ignored when time is None. Defaults to True.
            delete_original (bool): Delete the source keyframes after copying.
                Implied when time is None. Defaults to False.
            mode (str): Inversion mode. "horizontal" (time), "vertical" (value), or "both". Defaults to "horizontal".
            value_pivot (float): Pivot value for vertical inversion. Defaults to 0.0.
        """
        return AnimUtils._invert_keys(
            objects=objects,
            time=time,
            relative=relative,
            delete_original=delete_original,
            mode=mode,
            value_pivot=value_pivot,
        )

    @staticmethod
    @CoreUtils.undoable
    def align_selected_keyframes(
        objects: Optional[List[str]] = None,
        target_frame: Optional[float] = None,
        use_earliest: bool = True,
    ) -> bool:
        """Aligns the starting keyframes of selected keyframes in the graph editor across multiple objects.

        This method finds the earliest (or latest) selected keyframe across all objects and shifts
        each object's selected keyframes so they start at the same frame. Only processes selected
        keyframes from the graph editor.

        Parameters:
            objects (Optional[List[str]]): Objects to align. If None, uses current selection.
            target_frame (Optional[float]): Specific frame to align to. If None, aligns to the
                                           earliest (or latest, if use_earliest=False) selection
                                           START frame among the objects.
            use_earliest (bool): If True, aligns to the earliest per-object selection start.
                                If False, aligns to the latest per-object selection start.
                                Only used when target_frame is None. Default is True.

        Returns:
            bool: True if keyframes were successfully aligned, False otherwise.

        Example:
            # Align selected keyframes to their earliest frame
            align_selected_keyframes()

            # Align selected keyframes to frame 10
            align_selected_keyframes(target_frame=10)

            # Align selected keyframes to their latest frame
            align_selected_keyframes(use_earliest=False)
        """
        return AnimUtils._align_selected_keyframes(
            objects=objects, target_frame=target_frame, use_earliest=use_earliest
        )

    @staticmethod
    @CoreUtils.undoable
    def set_visibility_keys(
        objects: Optional[List[str]] = None,
        visible: bool = True,
        when: str = "start",
        offset: int = 0,
        group_overlapping: bool = False,
    ) -> int:
        """Sets visibility keyframes for objects with options for timing and grouping.

        This method creates visibility keyframes at specific points in the animation timeline,
        with support for grouping objects that have overlapping keyframe ranges.

        Parameters:
            objects (Optional[List[str]]): Objects to set visibility keys on.
                If None, uses current selection.
            visible (bool): Visibility state to set (True = visible, False = hidden). Default is True.
            when (str): When to set the visibility key. Options:
                - "start": At the start of each object's keyframe range
                - "end": At the end of each object's keyframe range
                - "both": At both start and end
                - "before_start": One frame before the start
                - "after_end": One frame after the end
                Default is "start".
            offset (int): Frame offset to apply to the keyframe timing. Positive values move
                keys later, negative values move keys earlier. Default is 0.
            group_overlapping (bool): If True, treats objects with overlapping keyframe ranges
                as a single group, setting visibility keys based on the group's combined range.
                Default is False.

        Returns:
            int: Number of visibility keyframes created.

        Example:
            # Hide objects at the start of their animation
            set_visibility_keys(visible=False, when="start")

            # Make objects visible at the end of their animation with 5 frame offset
            set_visibility_keys(visible=True, when="end", offset=5)

            # Set visibility for grouped overlapping animations
            set_visibility_keys(visible=True, when="both", group_overlapping=True)
        """
        return AnimUtils._set_visibility_keys(
            objects=objects,
            visible=visible,
            when=when,
            offset=offset,
            group_overlapping=group_overlapping,
        )

    @staticmethod
    @CoreUtils.undoable
    def snap_keys_to_frames(
        objects: Optional[List[str]] = None,
        method: str = "nearest",
        selected_only: bool = False,
        time_range: Optional[Tuple[float, float]] = None,
        include_driven: bool = False,
        through_blends: bool = True,
    ) -> int:
        """Snaps keyframes with decimal time values to whole frame numbers.

        This method rounds keyframe times to the nearest whole number, useful for cleaning up
        keyframes that have been scaled, retimed, or imported with fractional frame values.

        Parameters:
            objects (Optional[List[str]]): Objects to process keyframes for.
                If None, uses current selection.
            method (str): Rounding method to use. Options:
                - "nearest": Round to nearest whole number (default)
                - "floor": Always round down
                - "ceil": Always round up
                - "half_up": Round .5 and above up, below .5 down (standard rounding)
                - "preferred": Round to aesthetically pleasing numbers when very close (within ~1 frame).
                  Examples: 24→25, 19→20, 18→20, 99→100. Conservative approach.
                - "aggressive_preferred": Round to preferred numbers even when farther away.
                  Examples: 48.x→50, 73.x→75, 88.x→90, 23.x→25, 7.x→10. More aggressive rounding.
            selected_only (bool): If True, only snap selected keyframes. If False, snap all
                keyframes on the objects. Default is False.
            time_range (Optional[Tuple[float, float]]): (start_time, end_time) to limit which
                keyframes to snap. If None, processes all keyframes. Default is None.
            through_blends (bool): Also snap the keys behind a layered or
                constrained channel's blend node (default). See
                :meth:`objects_to_curves`.

        Returns:
            int: Number of keyframes that were snapped to whole frames.

        Example:
            # Snap all keyframes to nearest whole frame
            snap_keys_to_frames()

            # Snap only selected keyframes, always rounding down
            snap_keys_to_frames(method="floor", selected_only=True)

            # Snap keyframes in a specific time range
            snap_keys_to_frames(time_range=(10, 100))

            # Snap to preferred round numbers (conservative)
            snap_keys_to_frames(method="preferred")

            # Snap to preferred round numbers (aggressive)
            snap_keys_to_frames(method="aggressive_preferred")
        """
        return AnimUtils._snap_keys_to_frames(
            objects=objects,
            method=method,
            selected_only=selected_only,
            time_range=time_range,
            include_driven=include_driven,
            through_blends=through_blends,
        )

    @classmethod
    @CoreUtils.undoable
    def transfer_keyframes(
        cls,
        objects: List[str],
        relative: bool = False,
        transfer_tangents: bool = False,
        optimize: bool = False,
    ):
        """Transfer keyframes from the first selected object to the subsequent objects.

        If keyframes are selected in the graph editor, only those keyframes and their
        associated attributes will be transferred. Otherwise, all keyframes are transferred.

        Parameters:
            objects (List[str]): List of objects. The first object is the source, and the rest are targets.
            relative (bool): If True, apply keyframes relative to the current values of the target objects.
            transfer_tangents (bool): If True, transfer the tangent handles along with the keyframes.
            optimize (bool): If True, run optimize_keys on the source before transferring.
        """
        return cls._transfer_keyframes(
            objects=objects,
            relative=relative,
            transfer_tangents=transfer_tangents,
            optimize=optimize,
        )

    @staticmethod
    def parse_time_range(
        time: Union[None, int, str, Tuple, List],
    ) -> Union[Tuple[float, float], None, List]:
        """Parse time specification into a time range tuple for keyframe operations.

        This helper method handles various time specifications and converts them into
        time ranges suitable for Maya keyframe operations. Complex specifications
        (pipe-separated strings, 3+ element sequences) return a list — callers
        recurse over its elements themselves.

        Parameters:
            time (None, int, str, tuple, list): Time specification to parse.
                Accepts:
                - None or 'all': Returns None (entire timeline)
                - int: Returns (time, time) for specific frame
                - 'current': Returns (current_time, current_time)
                - 'before': Returns a range ending just before the current frame
                - 'after': Returns a range starting just after the current frame
                - tuple/list of 2 elements: Returns (start, end) range
                - tuple/list of 3+ elements: Returns list for recursive processing
                - Pipe-separated strings: Returns list for recursive processing

        Returns:
            Union[Tuple[float, float], None, List]:
                - None: Process entire timeline
                - Tuple[float, float]: (start_time, end_time) range
                - List: Multiple time values/ranges requiring recursive processing

        Example:
            # Single frame
            time_range = parse_time_range(10)  # Returns (10, 10)

            # Current frame
            time_range = parse_time_range('current')  # Returns (current_time, current_time)

            # Before current frame
            time_range = parse_time_range('before')  # Returns (-1000000, just before current)

            # Range
            time_range = parse_time_range((5, 15))  # Returns (5, 15)

            # Multiple frames (returns list for recursive processing)
            time_values = parse_time_range((1, 5, 10, 20))  # Returns [1, 5, 10, 20]

            # Pipe-separated (returns list for recursive processing)
            time_values = parse_time_range('before|current')  # Returns ['before', 'current']
        """
        return AnimUtils._parse_time_range(time=time)

    @staticmethod
    @CoreUtils.undoable
    def delete_keys(objects=None, *attributes, time=None, channel_box_only=False):
        """Deletes keyframes for specified attributes on given objects, optionally within a time range.

        This function can delete keyframes for all attributes or specified attributes, and within the entire timeline
        or a specified time range. Supports flexible time specification including single frames, ranges, and
        combinations using pipe separators or sequences.

        Parameters:
            objects (list): The list of objects from which to delete keyframes.
            *attributes (str): Variable length argument list of attribute names.
                            If empty, keyframes for all attributes will be deleted (unless channel_box_only=True).
                            Can accept a list by unpacking when calling the function using *
            time (None, int, str, tuple, list): Specifies the time range for keyframe deletion.
                    Accepts:
                    - None or 'all': Delete all keyframes (entire timeline)
                    - int: Delete keyframes at specific frame
                    - 'current': Delete keyframes at current frame
                    - 'before': Delete all keyframes before current frame (excluding current)
                    - 'after': Delete all keyframes after current frame (excluding current)
                    - Pipe-separated combinations: 'before|current', 'after|current', etc.
                    - tuple/list of 2 elements: (start, end) - Delete keyframes in range
                    - tuple/list of 3+ elements: (t1, t2, t3, ...) - Delete at each frame recursively
            channel_box_only (bool): If True, only deletes keys for attributes selected in the channel box.
                                    Ignores the *attributes parameter. Default is False.

        Notes:
            - Pipe-separated strings are processed recursively (e.g., 'before|current' deletes both ranges)
            - Tuples with more than 2 elements are processed as individual frames recursively
            - All string values are case-insensitive
            - When channel_box_only=True, no attributes are selected in channel box will result in no deletion

        Example Usage:
            delete_keys([obj1, obj2], 'translateX', 'translateY', time=10) # Delete keyframes at frame 10
            delete_keys([obj1, obj2], time='current') # Delete keyframes at current frame
            delete_keys([obj1, obj2], time='before') # Delete all keyframes before current (excluding current)
            delete_keys([obj1, obj2], time='after') # Delete all keyframes after current (excluding current)
            delete_keys([obj1, obj2], time='before|current') # Delete up to and including current
            delete_keys([obj1, obj2], time='after|current') # Delete from and after current
            delete_keys([obj1, obj2], time='before|current|after') # Delete all keyframes (equivalent to 'all')
            delete_keys([obj1, obj2], time=(5, 15)) # Delete all keyframes between frames 5 and 15
            delete_keys([obj1, obj2], time=(1, 5, 10, 20)) # Delete keyframes at frames 1, 5, 10, and 20
            delete_keys([obj1, obj2], 'rotateX', 'rotateY') # Delete all keyframes for specified attributes
            delete_keys([obj1, obj2], channel_box_only=True) # Delete only for channel box selected attributes
        """
        return AnimUtils._delete_keys(
            objects, *attributes, time=time, channel_box_only=channel_box_only
        )

    @staticmethod
    def select_keys(
        objects: Optional[List[str]] = None,
        *attributes: str,
        time: Union[None, int, str, Tuple, List] = None,
        channel_box_only: bool = False,
        add_to_selection: bool = False,
    ) -> int:
        """Selects keyframes for specified attributes on given objects, optionally within a time range.

        This function selects keyframes for all attributes or specified attributes, and within the entire timeline
        or a specified time range. Supports flexible time specification including single frames, ranges, and
        combinations using pipe separators or sequences.

        Parameters:
            objects (list, optional): The list of objects from which to select keyframes. If None, uses selection.
            *attributes (str): Variable length argument list of attribute names.
                            If empty, keyframes for all attributes will be selected (unless channel_box_only=True).
                            Can accept a list by unpacking when calling the function using *
            time (None, int, str, tuple, list): Specifies the time range for keyframe selection.
                    Accepts:
                    - None or 'all': Select all keyframes (entire timeline)
                    - int: Select keyframes at specific frame
                    - 'current': Select keyframes at current frame
                    - 'before': Select all keyframes before current frame (excluding current)
                    - 'after': Select all keyframes after current frame (excluding current)
                    - Pipe-separated combinations: 'before|current', 'after|current', etc.
                    - tuple/list of 2 elements: (start, end) - Select keyframes in range
                    - tuple/list of 3+ elements: (t1, t2, t3, ...) - Select at each frame recursively
            channel_box_only (bool): If True, only selects keys for attributes selected in the channel box.
                                    Ignores the *attributes parameter. Default is False.
            add_to_selection (bool): If True, adds to existing keyframe selection. If False, replaces selection.
                                    Default is False.

        Returns:
            int: Number of keyframes selected.

        Notes:
            - Pipe-separated strings are processed recursively (e.g., 'before|current' selects both ranges)
            - Tuples with more than 2 elements are processed as individual frames recursively
            - All string values are case-insensitive
            - When channel_box_only=True, no attributes selected in channel box will result in no selection

        Example Usage:
            select_keys([obj1, obj2], 'translateX', 'translateY', time=10) # Select keyframes at frame 10
            select_keys([obj1, obj2], time='current') # Select keyframes at current frame
            select_keys([obj1, obj2], time='before') # Select all keyframes before current (excluding current)
            select_keys([obj1, obj2], time='after') # Select all keyframes after current (excluding current)
            select_keys([obj1, obj2], time='before|current') # Select up to and including current
            select_keys([obj1, obj2], time='after|current') # Select from and after current
            select_keys([obj1, obj2], time='before|current|after') # Select all keyframes (equivalent to 'all')
            select_keys([obj1, obj2], time=(5, 15)) # Select all keyframes between frames 5 and 15
            select_keys([obj1, obj2], time=(1, 5, 10, 20)) # Select keyframes at frames 1, 5, 10, and 20
            select_keys([obj1, obj2], 'rotateX', 'rotateY') # Select all keyframes for specified attributes
            select_keys([obj1, obj2], channel_box_only=True) # Select only for channel box selected attributes
            select_keys([obj1, obj2], time='current', add_to_selection=True) # Add current frame keys to selection
        """
        return AnimUtils._select_keys(
            objects,
            *attributes,
            time=time,
            channel_box_only=channel_box_only,
            add_to_selection=add_to_selection,
        )

    @staticmethod
    def get_frame_ranges(
        objects: List[str],
        precision: Optional[int] = None,
        gap: Optional[int] = None,
    ) -> Dict[str, List[Tuple[int, int]]]:
        """Calculate frame ranges for a list of objects based on their keyframes.

        This method analyzes the keyframes of given objects and determines continuous
        frame ranges. It supports optional rounding of frame numbers to a specified precision
        and allows for specifying a gap threshold to split ranges.

        Parameters:
            objects (List[str]): List of object names to analyze.
            precision (Optional[int]): Precision for rounding frame numbers. If provided,
                                    frame numbers will be rounded to the nearest multiple
                                    of this value.
            gap (Optional[int]): Maximum allowed gap between consecutive keyframes in a
                                range. If the gap between two consecutive keyframes exceeds
                                this value, a new range will be started.
        Returns:
            Dict[str, List[Tuple[int, int]]]: Dictionary mapping object names to lists of
                                            frame ranges. Each frame range is represented
                                            as a tuple (start_frame, end_frame) — rounded
                                            ints when *precision* is given, otherwise raw
                                            (possibly fractional) keyframe times. If an
                                            object has no keyframes, the range will be
                                            [(None, None)].
        """
        return AnimUtils._get_frame_ranges(
            objects=objects, precision=precision, gap=gap
        )

    @staticmethod
    def get_tied_keyframes(
        objects: Optional[List[str]] = None,
        tolerance: float = 1e-5,
    ) -> Dict[str, Dict[str, List[float]]]:
        """Detects tied (bookend) keyframes for given objects.

        Curves tied by tie_keyframes carry an exact record of the inserted
        bookend times (see TIED_KEYS_ATTR); those are returned authoritatively.
        Curves without a record (tied before the metadata existed, or created
        by hand) fall back to a conservative heuristic: an end key is tied if
        it duplicates its neighbor's value AND is itself unshaped (flat or
        stepped tangents on both sides), on a curve with at least 3 keys.
        The tangent requirement protects genuine shaped keys (e.g. an authored
        overshoot returning to the same value); the 3-key minimum protects a
        deliberate 2-key hold from being flagged in its entirety.

        This is useful for:
        - Identifying keys added by tie_keyframes()
        - Filtering out bookend keys from operations
        - Validating animation data

        Parameters:
            objects (Optional[List[str]]): Objects to check for tied keyframes.
                If None, checks all keyed objects in the scene.
            tolerance (float): Tolerance for comparing keyframe values. Two values are
                considered the same if their difference is less than this value.
                Default is 1e-5.

        Returns:
            Dict[str, Dict[str, List[float]]]: Dictionary mapping objects to their
                tied keyframes. For each object, maps attribute names (curve names) to
                lists of tied keyframe times.

        Example:
            # Get all tied keyframes in the scene
            tied_keys = AnimUtils.get_tied_keyframes()
            # Returns: {obj1: {'pCube1_translateX': [1.0, 100.0]}, obj2: {...}}

            # Get tied keyframes for selected objects
            tied_keys = AnimUtils.get_tied_keyframes(cmds.ls(selection=True))

            # Check if a specific object has tied keyframes
            tied_keys = AnimUtils.get_tied_keyframes([my_obj])
            if my_obj in tied_keys:
                print(f"Object has tied keys: {tied_keys[my_obj]}")
        """
        return AnimUtils._get_tied_keyframes(objects=objects, tolerance=tolerance)

    @staticmethod
    @CoreUtils.undoable
    def insert_keys(
        objects: Union[str, List[str]],
        times: Iterable[float],
        tolerance: float = 1e-4,
        report: bool = False,
    ):
        """Insert keys at *times* WITHOUT changing what any curve evaluates to.

        The shape-preserving twin of :meth:`tie_keyframes`, and the difference
        is the whole point of having both. ``tie_keyframes`` gives its bookends
        FLAT tangents, which is what you want to HOLD an animation at the ends
        of a range -- and is a change to the curve everywhere near them
        (measured on a production assembly: tying at 12 shot boundaries moved
        ``USER_POS_LOC`` by up to 237 cm). ``insert_keys`` uses Maya's own
        ``setKeyframe -insert``, which computes the value and both tangents so
        the curve is bit-identical before and after; all it does is give the
        curve a key it can be CUT at.

        That is what makes a shot self-contained: with a key on each of its
        bounds, nothing outside the shot can change what plays inside it, so a
        move that repositions the shot cannot alter its content. Without it,
        moving a neighbour retimes the segment that spans the boundary -- and
        with auto tangents the change reaches back past the boundary into
        frames that never moved.

        Only times INSIDE a curve's own key range are inserted at, and that is
        a correctness rule rather than an optimisation. Outside its keys a
        curve HOLDS (constant extrapolation), so there is no shape there to
        preserve and a rigid move carries the hold with the key that produces
        it -- while asking Maya to insert there is not a shape-preserving
        insert at all: measured on Maya 2025, inserting at frame 7 on a
        ``visibility`` curve whose first key is at 8 dropped that key's STEP
        out-tangent, and the boolean it drove ramped instead of holding, so an
        object hidden until frame 23 reappeared at 16.

        Nor is a key inserted inside a HOLD -- a key segment that plays one
        value from end to end (the left key steps, or both keys carry the
        same value and the tangents facing the segment are flat).  The same
        rule as the range rule above, one level down: there is no shape in a
        hold to preserve, a rigid move of either key keeps it a hold, and a
        key planted on one is clutter that then has to be carried, reconciled
        and, for the user, explained.  Measured 2026-09-07 on the production
        assembly: 805 of the 824 samples the shot system had planted sat on
        flat plateaus, and the sequencer showed the objects carrying them as
        members of shots they never move in.

        Idempotent: a time a curve already has a key at is skipped, so
        re-running inserts nothing and cannot stack duplicates.

        Fully undoable, which is the other thing that separates it from
        :meth:`tie_keyframes`: this goes through ``cmds.setKeyframe`` and lands
        in Maya's undo queue, while the om2 ``addKey`` path records no
        ``MAnimCurveChange`` and leaves its bookends behind on an undo. A
        caller that inserts as the precondition for a larger edit (the shot
        respace does) therefore gets the whole thing back with one Ctrl+Z.

        Parameters:
            objects: Node(s) whose animated curves should be split.
            times: Frames to insert at.
            tolerance: How close an existing key has to be to count as
                already-there. Keys land on whole frames by default, so this
                only has to clear float noise from a previous move.
            report: Return ``[(curve, time), ...]`` for the keys inserted
                instead of a count, for a caller that has to be able to name
                them again later -- the shot system claims its own inserts so
                it can move or retire them when the bound they pin moves.

        Returns:
            The number of keys actually inserted, or the per-key list when
            *report* is set.
        """
        return AnimUtils._insert_keys(
            objects=objects, times=times, tolerance=tolerance, report=report
        )

    @staticmethod
    @CoreUtils.undoable
    def tie_keyframes(
        objects: List[str] = None,
        absolute: bool = False,
        padding: int = 0,
        custom_range: Optional[Tuple[float, float]] = None,
    ):
        """Ties the keyframes of all given objects (or all keyed objects in the scene if none are provided)
        by setting keyframes only on the attributes that already have keyframes,
        at the start and end of the specified animation range.

        Uses OpenMaya 2.0 (MFnAnimCurve) to freeze auto tangents on adjacent
        keys BEFORE inserting bookend keys, preventing Maya from recalculating
        them.  This eliminates the need for post-insertion tangent restoration
        and is O(curves) with only fast C++ calls per curve.

        Each curve records the inserted bookend times in a string attribute
        (TIED_KEYS_ATTR), so untie_keyframes can later remove exactly those
        keys — even bookends inside the keyed range or stacked by repeated
        tie passes.  untie_keyframes clears the record.

        Note:
            One undo reverts a tie -- the bookends, the tangents frozen for
            them and the bookend record: the OpenMaya edits are recorded on
            the undo queue (UndoRecorder).  untie_keyframes reverts one later.

        Parameters:
            objects (List[str], optional): List of transform node names to process.
                If None, all keyed objects in the scene will be used.
            absolute (bool, optional): If True, uses the absolute start and end keyframes
                across all objects as the range. If False, uses the scene's playback range. Default is False.
            padding (int, optional): Number of frames to extend the tie keyframes beyond the range.
                Positive values add padding (e.g., 5 = tie 5 frames before start and 5 frames after end).
                Negative values shrink the range inward. Default is 0.
            custom_range (Tuple[float, float], optional): Explicit (start, end) range to use.
                If provided, overrides absolute and scene range settings.

        Example:
            # Tie keyframes at the exact playback range (e.g., 10-100)
            tie_keyframes()  # Ties at 10 and 100

            # Add 5 frames of padding on both ends
            tie_keyframes(padding=5)  # Ties at 5 and 105 (if playback is 10-100)

            # Use with absolute=True to add padding around actual keyframes
            tie_keyframes(absolute=True, padding=10)  # Adds 10 frame hold before/after animation
        """
        return AnimUtils._tie_keyframes(
            objects=objects,
            absolute=absolute,
            padding=padding,
            custom_range=custom_range,
        )

    @staticmethod
    @CoreUtils.undoable
    def untie_keyframes(
        objects: List[str] = None,
    ) -> Dict[str, Dict[str, List[float]]]:
        """Removes bookend keyframes added by tie_keyframes, but preserves genuine animation keys.

        Curves tied by tie_keyframes carry an exact record of the inserted
        bookend times, so those keys are removed precisely — including
        bookends that landed inside the keyed range and bookends stacked by
        multiple tie passes.  Curves without a record fall back to the
        conservative heuristic in get_tied_keyframes (value-duplicate end key
        with flat/stepped tangents, on a curve with at least 3 keys), so
        genuine shaped keys and deliberate 2-key holds are never deleted.

        Parameters:
            objects (List[str], optional): List of transform node names to process.
                If None, all keyed objects in the scene will be used.

        Returns:
            Dict[str, Dict[str, List[float]]]: The removed keys, mapping each
                object to {curve_name: [removed_times]}.  Empty if nothing
                was removed.

        Example:
            # Remove bookend keys added by tie_keyframes
            untie_keyframes()

            # Remove bookend keys for specific objects
            untie_keyframes([obj1, obj2])
        """
        return AnimUtils._untie_keyframes(objects=objects)

    @staticmethod
    def create_animation_layer(
        name: str = "AnimLayer",
        override: bool = True,
        additive: bool = False,
        attributes: Optional[List[str]] = None,
        objects: Optional[List[str]] = None,
        weight: float = 1.0,
        mute: bool = False,
        solo: bool = False,
        lock: bool = False,
        preferred: bool = True,
        parent: Optional[str] = None,
        unique_name: bool = True,
        timestamp_suffix: bool = False,
        color: Optional[Tuple[float, float, float]] = None,
    ) -> str:
        """Create an animation layer with flexible configuration options.

        Creates a new animation layer and optionally adds attributes/objects to it.
        Handles unique naming, hierarchy, and layer properties.

        Parameters:
            name: Base name for the layer. Will be made unique if unique_name=True.
            override: If True, creates an override layer (replaces base animation).
                If False, creates an additive layer (adds to base animation).
            additive: Explicit additive mode. If True, sets override=False.
            attributes: List of attribute paths (e.g., ["pCube1.tx", "pCube1.ry"])
                to add to the layer. These attributes will be animatable on this layer.
                CAUTION: do NOT pre-register attributes you are about to
                ``bakeResults(destinationLayer=...)`` onto this same layer —
                the bake then writes a flat constant (the value live at
                registration time) at every sampled frame instead of the true
                curve. Hand bakeResults the empty layer and let it wire the
                attributes itself (see SmartBake._create_override_layer).
            objects: List of objects to add all keyable attributes from.
                Shorthand for adding all keyable attrs of each object.
            weight: Layer weight (0.0 to 1.0). Default is 1.0 (full influence).
            mute: If True, mute the layer (disable its effect).
            solo: If True, solo the layer (only this layer affects playback).
            lock: If True, lock the layer (prevent editing).
            preferred: If True, set as the preferred/selected layer for editing.
            parent: Name of parent layer. If None, uses the root (BaseAnimation).
            unique_name: If True, ensures layer name is unique by appending
                a counter if necessary (e.g., "MyLayer", "MyLayer_1", "MyLayer_2").
            timestamp_suffix: If True, appends timestamp to name for uniqueness
                (e.g., "MyLayer_20260203_143052"). Overrides unique_name counter.
            color: Optional RGB tuple (0-1 range) for layer display color in editor.

        Returns:
            The actual name of the created layer (may differ from input if
            unique_name=True and name collision occurred).

        Raises:
            RuntimeError: If layer creation fails.

        Example:
            >>> # Simple override layer
            >>> layer = AnimUtils.create_animation_layer("BakeLayer", override=True)

            >>> # Additive layer with specific attributes
            >>> layer = AnimUtils.create_animation_layer(
            ...     "Offset",
            ...     additive=True,
            ...     attributes=["pCube1.translateY", "pCube1.rotateZ"],
            ...     weight=0.5,
            ... )

            >>> # Layer for multiple objects
            >>> layer = AnimUtils.create_animation_layer(
            ...     "CharacterLayer",
            ...     objects=["joint1", "joint2", "joint3"],
            ...     timestamp_suffix=True,
            ... )

            >>> # Muted layer for comparison
            >>> layer = AnimUtils.create_animation_layer(
            ...     "Alternate", mute=True, preferred=False
            ... )
        """
        return AnimUtils._create_animation_layer(
            name=name,
            override=override,
            additive=additive,
            attributes=attributes,
            objects=objects,
            weight=weight,
            mute=mute,
            solo=solo,
            lock=lock,
            preferred=preferred,
            parent=parent,
            unique_name=unique_name,
            timestamp_suffix=timestamp_suffix,
            color=color,
        )

    @staticmethod
    def get_animation_layers(
        include_base: bool = False,
        muted_only: bool = False,
        active_only: bool = False,
    ) -> List[str]:
        """Get all animation layers in the scene.

        Parameters:
            include_base: If True, includes the BaseAnimation layer.
            muted_only: If True, returns only muted layers.
            active_only: If True, returns only non-muted layers.

        Returns:
            List of animation layer names.
        """
        return AnimUtils._get_animation_layers(
            include_base=include_base, muted_only=muted_only, active_only=active_only
        )

    @staticmethod
    def copy_keys(
        objects=None,
        mode: str = "auto",
        resolution_order: Optional[Tuple[str, ...]] = None,
        tangent_detail: bool = False,
    ) -> Dict[str, Dict[str, Any]]:
        """Copy attribute values from objects for later pasting as keys.

        Parameters:
            objects: Objects to copy from. Defaults to selection.
            mode: Copy mode — one of:
                - ``"auto"`` (default): Smart cascade via
                  :meth:`_resolve_keys` — uses selected keys if any
                  (narrowed to Channel Box highlights when they overlap,
                  otherwise all selected keys are used), falls back to
                  Channel Box values, then to all keyed attributes at
                  the current frame.
                - ``"current_frame"``: Copy all keyed attribute values at the
                  current time for each object.
                - ``"selected"``: Copy values of keys currently selected in the
                  Graph Editor.
                - ``"channel_box"``: Copy values of attributes highlighted in
                  the Channel Box.
            resolution_order: Strategies to try for ``"auto"`` mode.
                Default: ``("selected", "channel_box", "current_frame")``.
                See :meth:`_resolve_keys` for available strategies.
            tangent_detail: When True, multi-key data additionally
                captures tangent angles and weights per key, plus
                pre/post infinity types per curve.  This produces
                lossless snapshots suitable for undo/take management.
                Only affects ``"selected"`` mode.

        Returns:
            Nested dict ``{object_name: {attr: data, ...}, ...}``.

            For ``"current_frame"`` and ``"channel_box"`` modes, *data* is a
            single ``float``.

            For ``"selected"`` mode, *data* is a list of key dicts::

                [{"time": float, "value": float,
                  "inTangentType": str, "outTangentType": str}, ...]

            When *tangent_detail* is True each key dict also contains
            ``"inAngle"``, ``"outAngle"``, ``"inWeight"``,
            ``"outWeight"`` (floats), and the attribute entry gains
            ``"preInfinity"`` and ``"postInfinity"`` (strings).

            Empty dict when nothing could be copied.
        """
        return AnimUtils._copy_keys(
            objects=objects,
            mode=mode,
            resolution_order=resolution_order,
            tangent_detail=tangent_detail,
        )

    @staticmethod
    @CoreUtils.undoable
    def paste_keys(
        objects=None,
        copied_data: Optional[Dict[str, Dict[str, Any]]] = None,
        target_time=None,
        match_source: bool = True,
        refresh_channel_box: bool = True,
        **kwargs,
    ) -> int:
        """Paste previously copied attribute values as keyframes.

        Supports two data formats produced by :meth:`copy_keys`:

        * **Scalar** (``current_frame`` / ``channel_box``): a single float
          per attribute is keyed at *target_time*.
        * **Multi-key** (``selected``): a list of key dicts with time,
          value and tangent types.  Keys are offset so the earliest
          copied time aligns with *target_time* and tangent types are
          applied exactly as stored.

        Parameters:
            objects: Objects to paste onto. Defaults to selection.
            copied_data: Nested dict from :meth:`copy_keys`.
            target_time: Frame at which to paste.  Defaults to current time.
                For multi-key data the earliest copied key aligns here;
                later keys are offset accordingly.
            match_source: When True (default), each target object is
                matched to its corresponding source entry in *copied_data*
                by name.  When False, all attribute data from every source
                in *copied_data* is merged and applied to each target
                object — useful for pasting one object's animation onto
                a different object.
            refresh_channel_box: Update the Channel Box after keying.
            **kwargs: Extra flags forwarded to ``cmds.setKeyframe``
                (e.g. ``breakdown``, ``hierarchy``, ``shape``,
                ``controlPoints``, ``animLayer``).

        Returns:
            Number of objects that received keys.
        """
        return AnimUtils._paste_keys(
            objects=objects,
            copied_data=copied_data,
            target_time=target_time,
            match_source=match_source,
            refresh_channel_box=refresh_channel_box,
            **kwargs,
        )

    @staticmethod
    def delete_animation_layer(
        layer: str,
        merge_to_base: bool = False,
    ) -> bool:
        """Delete an animation layer.

        Parameters:
            layer: Name of the layer to delete.
            merge_to_base: If True, merges the layer's animation to the base
                layer before deleting. If False, animation is discarded.

        Returns:
            True if layer was deleted successfully, False otherwise.
        """
        return AnimUtils._delete_animation_layer(
            layer=layer, merge_to_base=merge_to_base
        )

    @staticmethod
    def fit_playback_range(
        objects=None,
        padding: float = 0,
    ) -> bool:
        """Set the playback range to encompass keyframes on all (or given) scene objects.

        Queries every keyed object in the scene (or a supplied list) and adjusts
        Maya's playback-range and animation-range to span from the earliest to
        the latest keyframe, optionally padded.

        Parameters:
            objects: Objects to consider. If None, every time-based animation
                curve in the scene is considered (including layered animation).
            padding: Extra frames to add before the first and after the last key.

        Returns:
            True if the range was updated, False if no keyframes were found.
        """
        return AnimUtils._fit_playback_range(objects=objects, padding=padding)

    # ---- key selection readers ---------------------------------------------

    @staticmethod
    def get_selected_key_times(
        curves: Optional[List[str]] = None,
    ) -> Dict[str, List[float]]:
        """Graph Editor key selection as ``{curve: [times]}``.

        Per curve, because a selection is per key: the user may have picked
        frames 10-30 on ``translateX`` and 15-40 on ``rotateY``.

        Parameters:
            curves: Restrict to these curve nodes (the scene-wide selection can
                include curves of unrelated objects).  ``None`` = every curve
                holding a selected key.

        Returns:
            Sorted, de-duplicated key times per curve; curves with no selected
            key are absent.
        """
        return AnimUtils._get_selected_key_times(curves=curves)

    @staticmethod
    def get_timeline_selection() -> Optional[Tuple[float, float]]:
        """The time slider's drag-selected range, or ``None`` when nothing is selected.

        Maya reports a one-frame "range" at the current time when there is no
        drag selection; that is treated as no selection.
        """
        return AnimUtils._get_timeline_selection()

    # ---- transient preview layer -------------------------------------------

    @staticmethod
    def create_preview_layer(
        sources: Dict[str, str],
        gate: Optional[Tuple[float, float]] = None,
        name: str = "previewLayer",
    ) -> str:
        """Play foreign curves on an object's plugs through a throwaway override layer.

        The plugs' own animation is untouched: an override layer at the top of
        the stack wins at weight 1 (no solo needed — soloing would also silence
        the user's own layers), and deleting the layer restores the direct
        curve→plug connections.  Used by the key stash to preview a stored
        clip without retrieving it; general enough to preview any curve set.

        Parameters:
            sources: ``{plug: anim_curve}`` — each curve's keys are pasted onto
                the layer's curve for that plug (the source is only read).
            gate: ``(start, end)``.  When given, the layer's weight is keyed to
                1 inside the range and 0 outside (stepped), so the base
                animation plays up to the range, the preview takes over, and the
                base resumes — the in-context view.  Without it the layer holds
                its end poses outside its keys (override extrapolation).
            name: Layer base name; made unique.

        Returns:
            The layer node name — hand it to :meth:`remove_preview_layer`.

        Raises:
            ValueError: When no source curve holds a key.
        """
        return AnimUtils._create_preview_layer(sources=sources, gate=gate, name=name)

    @staticmethod
    def remove_preview_layer(layer: Optional[str]) -> bool:
        """Delete a layer made by :meth:`create_preview_layer`; ``True`` if it existed.

        Deleting the layer removes its blend nodes and reconnects each plug's
        base curve directly (verified Maya 2025) — nothing is merged down.
        """
        return AnimUtils._remove_preview_layer(layer=layer)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
