# !/usr/bin/python
# coding=utf-8
"""Transform diagnostics and repair helpers.

Non-orthogonal (sheared) axes are what the FBX plug-in reports as
*"Non-orthogonal matrix support"* — "one or more objects in the scene have
local axes that are not perpendicular to each other ... will not correctly
import or export any transformations that involve non-perpendicular local
axes". FBX evaluates the **composite (world) matrix**, so an object trips the
warning in two distinct ways:

* **Own shear** — the transform carries a non-zero ``shear`` attribute
  (:meth:`TransformDiagnostics.get_sheared`).
* **Inherited shear** — the transform's own matrix is clean, but it sits under
  an ancestor that is *non-uniformly scaled* **and** rotated relative to it, so
  the evaluated world axes come out non-perpendicular. ``xform -q -shear``
  reads ``[0, 0, 0]`` here, which is why local-only detection misses it; this
  is the far more common source of the warning in production scenes.

:meth:`TransformDiagnostics.get_non_orthogonal` measures the world matrix and
therefore catches both.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Union

try:
    import maya.cmds as cmds
except ImportError as error:  # pragma: no cover - Maya runtime specific
    print(__file__, error)

import pythontk as ptk

from mayatk.xform_utils._xform_utils import XformUtils
from mayatk.xform_utils.matrices import Matrices
from mayatk.node_utils._node_utils import NodeUtils

# Type aliases keep Maya stubs optional during static analysis
NodeLike = Union[str, object]
NodeSeq = Union[NodeLike, Sequence[NodeLike]]


class _TransformDiagnosticsInternal:
    """Internal helpers for :class:`TransformDiagnostics`."""

    @staticmethod
    def _matrix_skew(matrix: Sequence[float]) -> float:
        """Max abs cosine between the axis rows of a flat 16-float matrix.

        0.0 means perfectly perpendicular axes; anything above the tolerance is
        shear. Measurement shared with blendertk via
        :meth:`ptk.MathUtils.max_axis_skew`.
        """
        return ptk.MathUtils.max_axis_skew(
            (matrix[0:3], matrix[4:7], matrix[8:11])
        )

    @staticmethod
    def _local_shear(node: str) -> List[float]:
        """Return ``[shearXY, shearXZ, shearYZ]`` for *node*.

        Read off the attribute rather than ``xform -q -shear``, which emits a
        "Cannot query absolute shear. Defaulting to relative." warning for every
        node queried — unusable noise on a scene-wide scan.
        """
        return list(cmds.getAttr(f"{node}.shear")[0])

    @staticmethod
    def _depth(node: str) -> int:
        """Hierarchy depth of *node*, for sorting a fix pass top-down."""
        long_name = (cmds.ls(node, long=True) or [node])[0]
        return long_name.count("|")

    # The channels the orthogonalization freeze writes. Translate is NOT here:
    # translation never contributes to axis skew, so it is never frozen and
    # its connections (constraints, anim curves — the most common kind) are
    # never at risk.
    _FREEZE_CHANNELS = ("rotate", "scale", "shear")

    @classmethod
    def _driving_connections(cls, node: str) -> List[str]:
        """Source plugs driving the channels the fix must freeze (r/s/shear).

        Queries both the compound plug and its children — a compound query
        does NOT see child-plug connections (``d.rotateZ -> c.rotateZ`` is
        invisible to ``listConnections(c.rotate)``) and vice versa; anim
        curves and constraints connect per-axis, so both levels matter.
        """
        plugs = []
        for channel in cls._FREEZE_CHANNELS:
            plugs.append(f"{node}.{channel}")
            children = (
                cmds.attributeQuery(channel, node=node, listChildren=True) or []
            )
            plugs.extend(f"{node}.{child}" for child in children)
        return (
            cmds.listConnections(
                plugs, source=True, destination=False, plugs=True
            )
            or []
        )

    @staticmethod
    def _is_referenced(node: str) -> bool:
        """True if *node* comes from a reference (freezing it would fail)."""
        try:
            return bool(cmds.referenceQuery(node, isNodeReferenced=True))
        except RuntimeError:
            return False


class TransformDiagnostics(_TransformDiagnosticsInternal):
    """Operations for inspecting and fixing common transform issues."""

    # Shear components / axis-pair cosines at or below this magnitude are
    # treated as orthogonal.
    SHEAR_TOLERANCE = 1e-6

    @classmethod
    def get_sheared(
        cls, objects: Optional[NodeSeq] = None, tolerance: Optional[float] = None
    ) -> List[str]:
        """Return the transforms carrying their own shear.

        Local-only: a transform under a non-uniformly scaled, rotated ancestor
        has no shear of its own yet still exports non-orthogonally — use
        :meth:`get_non_orthogonal` for the export-facing check.

        Parameters:
            objects: Transforms (or nodes resolvable to transforms) to check.
                None uses the current selection.
            tolerance: Max abs shear component treated as zero. Defaults to
                :attr:`SHEAR_TOLERANCE`.

        Returns:
            list[str]: Transforms with shear above the tolerance.
        """
        if objects is None:
            objects = cmds.ls(selection=True) or []
        tolerance = cls.SHEAR_TOLERANCE if tolerance is None else tolerance

        sheared: List[str] = []
        for obj in cmds.ls(objects, transforms=True) or []:
            if any(abs(s) > tolerance for s in cls._local_shear(obj)):
                sheared.append(obj)
        return sheared

    @classmethod
    def get_non_orthogonal(
        cls,
        objects: Optional[NodeSeq] = None,
        tolerance: Optional[float] = None,
        detailed: bool = False,
    ) -> Union[List[str], Dict[str, dict]]:
        """Return the transforms whose evaluated (world) axes are not perpendicular.

        This is the condition the FBX plug-in warns about. It covers both shear
        carried by the transform itself and shear inherited from a
        non-uniformly scaled, rotated ancestor (see the module docstring).

        Parameters:
            objects: Transforms (or nodes resolvable to transforms) to check.
                None uses the current selection. Pass
                ``cmds.ls(transforms=True)`` for a whole-scene scan.
            tolerance: Max axis-pair cosine treated as perpendicular. Defaults
                to :attr:`SHEAR_TOLERANCE`.
            detailed: Return a per-node diagnosis dict instead of a flat list.

        Returns:
            list[str]: The offending transforms (default), or with
            ``detailed=True`` a ``{node: {"skew": float, "shear": [xy, xz, yz],
            "cause": "shear" | "inherited", "driven": [src_plug, ...]}}``
            mapping. ``cause`` is ``shear`` when the node carries shear itself,
            ``inherited`` when the skew comes from its ancestors. ``driven``
            lists the source plugs connected into the node's rotate/scale/shear
            channels — the channels the fix must freeze — so a non-empty list
            means :meth:`fix_non_orthogonal_axes` will skip the node unless
            ``break_connections`` is set.
        """
        if objects is None:
            objects = cmds.ls(selection=True) or []
        tolerance = cls.SHEAR_TOLERANCE if tolerance is None else tolerance

        found: Dict[str, dict] = {}
        for obj in cmds.ls(objects, transforms=True) or []:
            skew = cls._matrix_skew(Matrices.get_matrix(obj, "worldMatrix"))
            shear = cls._local_shear(obj)
            own_shear = any(abs(s) > tolerance for s in shear)
            if skew > tolerance or own_shear:
                found[obj] = {
                    "skew": skew,
                    "shear": shear,
                    "cause": "shear" if own_shear else "inherited",
                    "driven": cls._driving_connections(obj),
                }
        return found if detailed else list(found)

    @classmethod
    def fix_non_orthogonal_axes(
        cls,
        objects: Optional[NodeSeq] = None,
        dry_run: bool = False,
        tolerance: Optional[float] = None,
        quiet: bool = False,
        break_connections: bool = False,
        instance_strategy: str = "preserve",
        delete_history: bool = False,
    ) -> List[str]:
        """Fix non-orthogonal axes by freezing the offending transforms.

        Detection is world-space (:meth:`get_non_orthogonal`), so objects that
        inherit shear from a non-uniformly scaled ancestor are fixed too, not
        just objects carrying their own shear.

        Only **rotate, scale and shear** are frozen — translation never
        contributes to axis skew, so translate channels (and their
        connections: point constraints, position anim curves, the most common
        kind) are left completely untouched. Freezing bakes those channels
        into the shape's points, so the composite result is unchanged — the
        object looks identical afterwards, but its evaluated axes come out
        perpendicular. Objects are processed **top-down** (shallowest first)
        and re-checked before each freeze: freezing an ancestor commonly
        clears its descendants, and those are then skipped rather than frozen
        twice.

        **Driven rotate/scale channels** (constraints, anim curves,
        expressions) are skipped by default with a warning naming the driver:
        there is no accurate way to both fix and keep the driver. Reconnecting
        after the freeze re-drives the channel the freeze just zeroed — the
        object double-transforms AND the skew returns (measured: 0.79 units
        off with the skew back, on a driven-rotation repro) — and with a
        varying driver the skew is time-varying, so no static bake can fix it.
        Bake or remove the driver (or fix the ancestor's non-uniform scale)
        and re-run; or pass ``break_connections=True`` to permanently
        disconnect the drivers and freeze anyway.

        If the object carries stored bake history
        (``XformUtils.store_transforms``), the freeze is composed into it
        first, so a later ``restore_transforms`` still returns the expected
        values — the freeze/unfreeze contract survives the fix.

        **Instanced objects** are handled per ``instance_strategy``:

        - ``"preserve"`` (default): when *every* member of the group is itself
          flagged (the duplicated-instance case), the member is fixed through
          ``XformUtils.freeze_instanced_group``, which bakes the shared points
          in place — one freeze clears the whole group, and its instancing,
          per-instance shading and world geometry all survive. Siblings are
          reported fixed only once they verify orthogonal: one bake applies one
          correction, so a sibling skewed *differently* is left flagged and
          picked up on its own iteration (as a divergent group, below).
          A group with any *unflagged* member is not baked shared: shared
          points cannot satisfy differing corrections, so pushing this
          member's correction onto the others would skew them instead
          (measured — fixing 1 of 4 broke the other 3).  The flagged member
          is forked off via ``NodeUtils.uninstance`` and baked alone; the
          rest stay instanced and untouched.  Groups
          ``freeze_instanced_group`` refuses (referenced, transform-level
          instancing, driven members) fall back to the same uninstance path.
        - ``"uninstance"``: legacy behavior — every flagged member is
          permanently uninstanced before freezing.

        Referenced nodes cannot be frozen and are reported rather than
        attempted.

        Parameters:
            objects: Transforms to process. None uses the current selection.
            dry_run: Report (and return) what would be fixed without making changes.
            tolerance: Max axis-pair cosine treated as perpendicular. Defaults
                to :attr:`SHEAR_TOLERANCE`.
            quiet: Suppress console output.
            break_connections: Permanently disconnect drivers on rotate/scale/
                shear channels instead of skipping the objects they drive.
            instance_strategy: ``"preserve"`` (default) or ``"uninstance"``
                — see above.
            delete_history: Allow the *uninstance* fallback to bake away
                construction history first when the member shares an
                intermediate (orig) shape.  Such a shape cannot be forked —
                the deformer reads it per instance, so dropping one edge
                empties the remaining instances — and while it stays shared
                the fork leaves the member un-detached.  Unused by the
                shared-bake path, which never forks and so is unaffected by
                a shared intermediate.

        Returns:
            list[str]: The transforms fixed (or, on ``dry_run``, the transforms
            that would be fixed — driven objects included only when
            ``break_connections`` is set).
        """
        if instance_strategy not in ("preserve", "uninstance"):
            raise ValueError(
                f"Invalid instance_strategy '{instance_strategy}'. "
                "Valid options: ['preserve', 'uninstance']"
            )
        tolerance = cls.SHEAR_TOLERANCE if tolerance is None else tolerance
        diagnosis = cls.get_non_orthogonal(objects, tolerance, detailed=True)
        # Top-down: an ancestor freeze can clear its descendants, so handling
        # the shallowest first keeps the pass to one freeze per offender.
        flagged = sorted(diagnosis, key=cls._depth)

        if dry_run:
            fixable = []
            for obj in flagged:
                info = diagnosis[obj]
                skipped = info["driven"] and not break_connections
                if not skipped:
                    fixable.append(obj)
                if not quiet:
                    note = (
                        f" — SKIPPED, driven by {', '.join(info['driven'])}"
                        if skipped
                        else ""
                    )
                    print(
                        f"Dry run: would fix {obj} (skew: {info['skew']:.6f}, "
                        f"cause: {info['cause']}){note}"
                    )
            if not quiet:
                print("Dry run complete.")
            return fixable

        fixed: List[str] = []
        # get_non_orthogonal reports shortest-unique names; the preserve
        # machinery reports long paths — one lookup bridges the two.
        flagged_long = {(cmds.ls(k, long=True) or [k])[0]: k for k in flagged}

        def _bake(node: str) -> bool:
            """Store bake history, freeze rotate+scale (shear bakes with
            scale), fall back to direct makeIdentity; True when the node
            verifies orthogonal afterwards.

            A node still sharing geometry goes through
            ``freeze_instanced_group``, which bakes the shared points in
            place via a stand-in instead of ``makeIdentity`` — so a shared
            (often orphaned) intermediate shape, which Maya refuses to
            freeze past, stops mattering.
            """
            # Keep the freeze/unfreeze contract intact: if the node has
            # stored bake history, compose the about-to-be-frozen rotate/
            # scale into it so a later restore returns the full values.
            if XformUtils.has_stored_transforms(node):
                XformUtils.store_transforms(
                    node, accumulate=True, channels=("rotate", "scale")
                )
            if NodeUtils.get_instanced_shapes(node):
                XformUtils.freeze_instanced_group(
                    node, translate=False, quiet=quiet
                )
                return not cls.get_non_orthogonal([node], tolerance)
            # The driven pre-check above means 'disconnect' only ever fires
            # when the caller explicitly opted in via break_connections.
            XformUtils.freeze_transforms(
                node,
                r=1,
                s=1,
                connection_strategy=(
                    "disconnect" if break_connections else "preserve"
                ),
                force=True,
            )
            if cls.get_non_orthogonal([node], tolerance):
                # freeze_transforms left residual skew; bake it directly.
                if not quiet:
                    print(
                        f"Warning: freeze_transforms failed to fix {node}. "
                        "Attempting direct makeIdentity..."
                    )
                cmds.makeIdentity(node, apply=True, t=0, r=1, s=1, n=0, pn=1)
            return not cls.get_non_orthogonal([node], tolerance)

        for obj in flagged:
            if not cls.get_non_orthogonal([obj], tolerance):
                # An ancestor's freeze already cleared this one.
                continue
            if cls._is_referenced(obj):
                if not quiet:
                    cmds.warning(
                        f"Skipping referenced node {obj} — freeze it in the source file."
                    )
                continue
            driven = diagnosis[obj]["driven"]
            if driven and not break_connections:
                if not quiet:
                    cmds.warning(
                        f"Skipping {obj} — rotate/scale driven by "
                        f"{', '.join(driven)}. Bake or remove the driver, or "
                        "run with break_connections=True."
                    )
                continue
            if not quiet:
                info = diagnosis[obj]
                print(
                    f"Fixing non-orthogonal axes on {obj} "
                    f"(skew: {info['skew']:.6f}, cause: {info['cause']})"
                )
            try:
                obj_ok = None
                tried_uninstance = False
                obj_long = (cmds.ls(obj, long=True) or [obj])[0]
                inst_shapes = NodeUtils.get_instanced_shapes(obj_long)
                if inst_shapes:
                    members = sorted(
                        {
                            p
                            for s in inst_shapes
                            for p in cmds.listRelatives(
                                s, allParents=True, fullPath=True
                            )
                            or []
                        }
                    )
                    unflagged = [
                        m
                        for m in members
                        if m != obj_long and not cls.get_non_orthogonal([m], tolerance)
                    ]
                    if instance_strategy == "preserve" and not unflagged:
                        # Every member needs the same correction, so one bake of
                        # the shared points fixes the whole group in place.
                        #
                        # Refused and baked-but-still-skewed must NOT collapse
                        # into one falsy result: a False return comes only from
                        # the up-front triage, every branch of which precedes any
                        # mutation, so retrying is legal. A True return means the
                        # shared points and sibling matrices were rewritten, and
                        # baking a second correction onto that would compound it
                        # — residual skew there is a failure to report.
                        if XformUtils.freeze_instanced_group(
                            obj_long, translate=False, quiet=quiet
                        ):
                            obj_ok = not cls.get_non_orthogonal(
                                [obj_long], tolerance
                            )
                            if obj_ok:
                                # Claim a sibling only once it VERIFIES clean.
                                # "All members flagged" does not mean all
                                # members are skewed the SAME way, and one bake
                                # of shared points applies one correction — a
                                # divergently skewed sibling comes out still
                                # flagged, and reporting it fixed would hide
                                # that. Unclaimed members simply get their own
                                # iteration, where they now read as a divergent
                                # group and take the uninstance path.
                                for m in members:
                                    orig = flagged_long.get(m)
                                    if (
                                        orig is not None
                                        and orig not in fixed
                                        and not cls.get_non_orthogonal(
                                            [m], tolerance
                                        )
                                    ):
                                        fixed.append(orig)
                        # else: refused, scene untouched — obj_ok stays None so
                        # the uninstance path below retries. Detaching this
                        # member alone still fixes it and leaves the rest of the
                        # group instanced.
                    if obj_ok is None:
                        # Divergent group (or the uninstance strategy): baking
                        # the shared points would push THIS member's shear onto
                        # the unflagged ones — measured on a production group,
                        # fixing 1 of 4 broke the other 3. Detach just this
                        # member instead; the rest stay instanced, untouched.
                        tried_uninstance = True
                        if not quiet and unflagged:
                            print(
                                f"Object {obj} shares its shape with "
                                f"{len(unflagged)} object(s) that are NOT skewed — "
                                "uninstancing this one so the others are left alone."
                            )
                        obj = (
                            NodeUtils.uninstance(obj, delete_history=delete_history)
                            or [obj]
                        )[0]
                        obj_ok = _bake(obj)
                else:
                    obj_ok = _bake(obj)

                if not obj_ok:
                    if not quiet:
                        # The delete_history advice is only actionable when a
                        # fork was actually attempted and blocked: the
                        # shared-bake path never forks, and re-suggesting a flag
                        # the caller already passed names the wrong cause.
                        blocked = NodeUtils.get_instanced_shapes(obj)
                        if (
                            tried_uninstance
                            and not delete_history
                            and any(NodeUtils.is_intermediate(s) for s in blocked)
                        ):
                            cmds.warning(
                                f"Unable to fix {obj}: it still shares an "
                                "intermediate (construction history) shape, which "
                                "cannot be forked without emptying the other "
                                "instances — so it could not be detached. Re-run "
                                "with delete_history=True to bake the history away "
                                "first (this member is then fully detached from "
                                "the group)."
                            )
                        else:
                            cmds.warning(
                                f"Unable to remove non-orthogonal axes on {obj}."
                            )
                elif obj not in fixed:
                    fixed.append(obj)
            except Exception as e:
                if not quiet:
                    cmds.warning(f"Failed to fix {obj}: {e}")

        if not quiet:
            if fixed:
                print(f"Fixed non-orthogonal axes on {len(fixed)} objects.")
            elif not flagged:
                print("No objects with non-orthogonal axes found.")
        return fixed
