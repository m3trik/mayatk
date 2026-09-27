# !/usr/bin/python
# coding=utf-8
"""``XformUtils`` -- the public index of mayatk's transform operations.

Every public method keeps its signature and docstring here and delegates its
body to the concept module that owns the job (the wildcard ``DEFAULT_INCLUDE``
entry registers the flat ``mtk.<method>`` names from this class body, so the
methods themselves never move):

- ``_freeze`` -- freezing transforms (instanced groups, offsetParentMatrix,
  push-to-parent) and undoing it.
- ``_stored_transforms`` -- the per-channel bake history freezing records:
  store / restore / repair / clear / read it.
- ``_pivots`` -- manip and object pivots: the operation-axis matrix and
  position, pivot alignment, baking and transfer.
- ``_placement`` -- measuring and placing objects: bounding boxes, distances,
  orientation, aiming, snapping and aligning objects and vertices.
"""

from __future__ import annotations

import contextlib
from typing import List

import pythontk as ptk

# From this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils

# The concept bases of the helper class below: needed at class-definition
# time, so imported eagerly (they import nothing from this module).
from mayatk.xform_utils._freeze import _FreezeInternal
from mayatk.xform_utils._stored_transforms import _StoredTransformsInternal
from mayatk.xform_utils._pivots import _PivotInternal
from mayatk.xform_utils._placement import _PlacementInternal


class _XformUtilsInternal(
    _FreezeInternal,
    _StoredTransformsInternal,
    _PivotInternal,
    _PlacementInternal,
):
    """The helper base of :class:`XformUtils`, composed from its concept modules.

    Each ``_<concept>.py`` sibling holds one job's private helpers and the
    bodies of its public methods (``XformUtils.<name>`` keeps the signature and
    docstring and returns ``cls._<name>(...)``). Composing them here keeps every
    helper reachable as ``XformUtils._<helper>``, the spelling the tests use.
    """


class XformUtils(_XformUtilsInternal, ptk.HelpMixin):
    """Transform utilities for Maya objects."""

    @staticmethod
    def convert_axis(value, invert=False, ortho=False, to_integer=False):
        """Converts between axis representations and optionally inverts the axis or returns an orthogonal axis.

        Parameters:
            value (int/str): The axis value to convert, either an integer index or a string representation.
                        Valid values are: 0 or "x", 1 or "-x", 2 or "y", 3 or "-y", 4 or "z", 5 or "-z".
            invert (bool): When True, inverts the axis direction.
            ortho (bool): When True, returns the axis that is orthogonal to the given axis.
            to_integer (bool): If True, returns the converted axis value as an integer index.

        Returns:
            str/int: The converted axis value as a string unless to_integer is True.

        Raises:
            TypeError: If `value` is not an int or str.
            ValueError: If `value` is invalid.

        Example:
            convert_axis(0)  # Returns "x"
            convert_axis("y")  # Returns "y"
            convert_axis("x", invert=True)  # Returns "-x"
            convert_axis(2, ortho=True)  # Returns "z"
            convert_axis("z", to_integer=True) # Returns 4
        """
        return XformUtils._convert_axis(
            value=value, invert=invert, ortho=ortho, to_integer=to_integer
        )

    @classmethod
    @CoreUtils.undoable
    def move_to(cls, source, target, pivot="center", group_move=False):
        """Move source object(s) to align with the target object(s).

        Parameters:
            source (str/obj/list): The Maya object(s) to move.
            target (str/obj/list): The Maya object(s) to move to.
            pivot (str/list): Which point of the target to align to. Accepts any value
                from `get_pivot_options()` — 'manip', 'object', 'world', 'center',
                'baked', or a bounding-box extent ('xmin'/'xmax'/'ymin'/'ymax'/
                'zmin'/'zmax') — or an explicit (x, y, z) world position. Per-node
                pivots (manip/object/baked) resolve against the last target; bounding-box
                pivots aggregate across the full target set. Defaults to 'center'.
            group_move (bool): If True, move the source objects as a single group centered around their common bounding box.
        """
        return cls._move_to(
            source=source, target=target, pivot=pivot, group_move=group_move
        )

    @classmethod
    @CoreUtils.undoable
    def drop_to_grid(
        cls,
        objects,
        align="Mid",
        origin=False,
        center_pivot=False,
        freeze_transforms=False,
    ):
        """Align objects to Y origin on the grid using a helper plane.

        Parameters:
            objects (str/obj/list): The objects to translate.
            align (bool): Specify which point of the object's bounding box to align with the grid. (valid: 'Max','Mid'(default),'Min')
            origin (bool): Move to world grid's center.
            center_pivot (bool): Center the object's pivot.
            freeze_transforms (bool): Reset the selected transform and all of its children down to the shape level.
        """
        return cls._drop_to_grid(
            objects=objects,
            align=align,
            origin=origin,
            center_pivot=center_pivot,
            freeze_transforms=freeze_transforms,
        )

    @classmethod
    def match_scale(cls, a, b, scale=True, average=False):
        """Scale each of the given objects in 'a' to the combined bounding box of the objects in 'b'.

        Parameters:
            a (str/obj/list): The object(s) to scale.
            b (str/obj/list): The object(s) to get a bounding box size from.
            scale (bool): Scale the objects. Else, just return the scale value.
            average (bool): Average the result across all axes.

        Returns:
            (list) scale values as [x,y,z,x,y,z...]
        """
        return cls._match_scale(a=a, b=b, scale=scale, average=average)

    @staticmethod
    @CoreUtils.selected
    @CoreUtils.undoable
    def scale_connected_edges(objects, scale_factor=1.1) -> None:
        """Scales each set of connected edges separately, either uniformly or non-uniformly.

        Parameters:
            objects (list): A list of selected edge components to be scaled.
            scale_factor (float, int, tuple, list): The factor by which to scale the edges.
        """
        return XformUtils._scale_connected_edges(
            objects=objects, scale_factor=scale_factor
        )

    @staticmethod
    @CoreUtils.undoable
    def store_transforms(
        objects,
        prefix="original",
        accumulate=True,
        traverse=False,
        channels=None,
    ):
        """Capture the current local TRS as a cumulative per-channel bake history.

        Stored as three custom attributes per node:

            ``{prefix}_T_bake`` (double3) — cumulative translation
            ``{prefix}_R_bake`` (matrix)  — cumulative rotation
            ``{prefix}_S_bake`` (double3) — cumulative scale

        The freeze/unfreeze contract is cumulative: each call composes the
        current local TRS onto whatever was previously stored for each
        channel listed in *channels*.

        Parameters:
            objects (str/obj/list): Transform nodes to store transforms for.
            prefix (str): Attribute name prefix (default: "original").
            accumulate (bool): When True (default) and a bake already exists
                for a channel, compose the current local value onto it; when
                False, overwrite that channel with the current local value.
            traverse (bool): If True, also store transforms on every descendant
                transform of the given objects.  Mirrors ``freeze_transforms
                (freeze_children=True)`` so that a later ``restore_transforms``
                on any node in the chain finds its bake history.
            channels (iterable): Subset of ``{"translate", "rotate", "scale"}``
                restricting which channel(s) to update.  ``None`` (default)
                updates all three.
        """
        return XformUtils._store_transforms(
            objects=objects,
            prefix=prefix,
            accumulate=accumulate,
            traverse=traverse,
            channels=channels,
        )

    @classmethod
    @CoreUtils.undoable
    def freeze_instanced_group(
        cls,
        master: str,
        translate: bool = True,
        rotate: bool = True,
        scale: bool = True,
        quiet: bool = True,
    ) -> bool:
        """Freeze *master* while keeping its instance group intact.

        Maya refuses ``makeIdentity`` on a transform sharing a shape, and
        forking the shape to get around it is not viable: the fork has to be
        re-linked onto every sibling afterwards, and adding/removing DAG
        instance edges renumbers ``instObjGroups``, breaking per-instance
        shading assignments (measured on a production scene:
        ``Connection not made … SG.dagSetMembers[n]``, leaving siblings on
        baked geometry with un-compensated matrices — geometry visibly out
        of position).

        So nothing here touches the DAG. The shared shape is edited in
        place, which every member sees at once:

        1. Duplicate *master* ``parentOnly`` (a shapeless stand-in that Maya
           *will* freeze) and ``makeIdentity`` it — this yields the exact
           baked delta ``B = pre_local · post_local⁻¹`` including pivots,
           without touching real geometry.
        2. Bake the shared authoring shapes' points by ``B``.
        3. Copy the stand-in's frozen channels onto *master*.
        4. Compensate every sibling, ``L → B⁻¹·L``, re-pinning world pivots.

        World geometry is preserved for the whole group (measured 1.4e-7 on
        a 4-member production group), instancing and per-instance shading
        are untouched, and a shared intermediate shape is irrelevant — it is
        baked alongside rather than forked.

        Note the siblings absorb ``B⁻¹`` into their channels: if *master*
        carried shear the others did not, they come out sheared. That is
        unavoidable — one shared point set cannot satisfy two different
        corrections — and it is why the non-orthogonal fix uninstances a
        lone skewed member instead of calling this.

        Returns:
            True when the group was frozen; False when it was left alone
            (reason warned unless *quiet*).
        """
        return cls._freeze_instanced_group(
            master=master, translate=translate, rotate=rotate, scale=scale, quiet=quiet
        )

    @classmethod
    @CoreUtils.undoable
    def freeze_transforms(
        cls,
        objects,
        center_pivot=0,
        force=True,
        delete_history=False,
        freeze_children=False,
        unlock_children=True,
        connection_strategy="preserve",
        instance_strategy="skip",
        from_channel_box=False,
        store=True,
        **kwargs,
    ):
        """Freezes transformations on the given objects.

        ``store`` (default True) records the pre-freeze local TRS as bake
        history, so the freeze is always reversible via
        ``XformUtils.restore_transforms``.  It is three attributes per
        transform and is what makes this safe to call from any tool — pass
        ``store=False`` only for construction-time freezes whose pre-freeze
        state is meaningless (building a rig control, uninstancing).

        The snapshot always spans the subtree, independent of
        ``freeze_children``: ``makeIdentity`` on a group zeroes EVERY
        descendant transform's channels, so recording only the roots would
        lose the descendants' values irrecoverably.  It is *committed* after
        the freeze and only for the transforms that actually froze — an
        object skipped as instanced or connection-blocked keeps its channels,
        so stamping it would make a later unfreeze add a transform that was
        never baked out.

        Per-channel kwargs (``translate``/``t``, ``rotate``/``r``,
        ``scale``/``s``, or per-axis ``tx``…``sz``) restrict the freeze; with
        none of them the whole transform is frozen, matching Maya's
        ``makeIdentity -apply true``.  ``normal`` is a **modifier**, not a
        channel: it maps to ``makeIdentity -normal`` (freeze vertex normals,
        which matters for negatively-scaled geometry) and does not narrow the
        channel set.

        ``instance_strategy`` decides what happens to instanced objects:

        - ``"skip"`` (default): skipped in place — baking into a shared
          shape would rewrite every sibling instance's geometry.
        - ``"preserve"``: each group's first targeted member is frozen via
          ``XformUtils.freeze_instanced_group``, which bakes the *shared* shape in
          place rather than forking it — instancing, per-instance shading and
          every member's world geometry survive; sibling channels are
          rewritten with the compensating matrix (so only the operated
          member ends identity).
        - ``"uninstance"``: break the instance links first
          (``NodeUtils.uninstance``), then freeze every object normally.
        """
        return cls._freeze_transforms(
            objects=objects,
            center_pivot=center_pivot,
            force=force,
            delete_history=delete_history,
            freeze_children=freeze_children,
            unlock_children=unlock_children,
            connection_strategy=connection_strategy,
            instance_strategy=instance_strategy,
            from_channel_box=from_channel_box,
            store=store,
            **kwargs,
        )

    @staticmethod
    @CoreUtils.undoable
    def freeze_to_opm(
        objects,
        reset_rotate_axis: bool = False,
        reset_joint_orient: bool = False,
        store: bool = True,
    ) -> None:
        """Freeze transforms into offsetParentMatrix while preserving pivot placement.

        Non-destructive: the geometry is never touched — the local transform
        simply moves into ``offsetParentMatrix``. ``store`` (default True)
        still records bake history, because the freeze/unfreeze CONTRACT is
        what the UI reads: without a stamp ``has_stored_transforms`` reports
        False and the Channels panel greys out Un-Freeze on a node that is in
        fact perfectly reversible. The stamp carries a ``{prefix}_opm_bake``
        marker so ``restore_transforms`` reverses it by clearing the OPM
        (:meth:`unfreeze_from_opm`) instead of counter-baking geometry that was
        never baked.
        """
        return XformUtils._freeze_to_opm(
            objects=objects,
            reset_rotate_axis=reset_rotate_axis,
            reset_joint_orient=reset_joint_orient,
            store=store,
        )

    @classmethod
    @CoreUtils.undoable
    def unfreeze_from_opm(
        cls, objects, prefix="original", delete_attrs=True
    ) -> List[str]:
        """Inverse of :meth:`freeze_to_opm`: clear ``offsetParentMatrix`` and put
        the stored channels back.

        An OPM freeze never touched the geometry, so its inverse must not
        counter-bake any — it just moves the transform back out of the OPM.
        ``restore_transforms`` routes marked nodes here automatically; call it
        directly only when you want the OPM path specifically.

        There is no per-channel variant: an OPM freeze moves the whole local
        matrix into one plug, so the three channels cannot be taken back
        independently.

        Parameters:
            objects (str/obj/list): Nodes to restore.
            prefix (str): Bake-attr prefix used by ``store_transforms``.
            delete_attrs (bool): Delete the consumed bake attrs and the OPM
                marker. Default True; False leaves the history in place.

        Returns:
            list: Long names of the nodes restored.
        """
        return cls._unfreeze_from_opm(
            objects=objects, prefix=prefix, delete_attrs=delete_attrs
        )

    @staticmethod
    @CoreUtils.undoable
    def unfreeze_to_parent(
        objects,
        traverse: bool = False,
        preserve_root: bool = True,
    ) -> List[str]:
        """Push a child transform's local matrix up into its parent and zero the child.

        Inverse of ``freeze_transforms`` for the common rig pattern where the
        parent is at identity and a locator child holds the world-space matrix
        the parent "should" have. After the operation the parent absorbs the
        child's local matrix and the child is reset to identity. Descendants
        of the child stay in place visually; **siblings of the child shift**
        because the parent's local matrix changes — only use where the parent
        has a single meaningful child (e.g. restoring a GRP > LOC > GEO
        locator rig after a recursive freeze).

        Parameters:
            objects (str/obj/list): Nodes to operate on. With ``traverse=False``
                (default) each input is the *child* whose local matrix is
                lifted into its parent. With ``traverse=True`` each input is a
                container — the subtree is scanned for locators, and each
                locator's local matrix is lifted into its immediate parent.
            traverse (bool): When True, walk each input's subtree and lift
                every locator descendant into its parent. Default False.
            preserve_root (bool): When ``traverse=True``, never lift into one
                of the input root nodes themselves — keeps the top-level
                containers zero'd out. Default True. Ignored when
                ``traverse=False`` (the input is the child, not the parent).

        Returns:
            List of parent node short names whose local matrix was modified.
        """
        return XformUtils._unfreeze_to_parent(
            objects=objects, traverse=traverse, preserve_root=preserve_root
        )

    @classmethod
    @CoreUtils.undoable
    def restore_transforms(
        cls,
        objects,
        prefix="original",
        delete_attrs=True,
        channels=None,
        traverse=False,
    ):
        """Compose stored bake history with current local TRS, per channel.

        For each channel C in *channels*:

            new local C = stored bake C  *  current local C

        (vector addition for T, quaternion composition for R, component-
        wise multiplication for S).  Channels not in *channels* keep their
        current local value.  Geometry is shifted so visual world position
        is preserved across the operation, and each object's rotate/scale
        pivot is re-anchored at its pre-restore world position.

        The geometry shift spans the whole SUBTREE of each restored node,
        not just its own shapes: ``makeIdentity`` on a group flattens every
        descendant into the leaf shape points, and a group has no shapes of
        its own — compensating only direct shapes would move the meshes.

        Counterpart of ``store_transforms`` under the cumulative
        freeze/unfreeze contract — repeated freeze + transform + unfreeze
        cycles compose, never snap back.

        Robustness:
            * Temporarily unlocks T/R/S channels before writing.
            * Skips referenced nodes with a warning.
            * Skips nodes with no stored bake attributes with a warning.
            * Vectorizes per-vertex updates via the OpenMaya 2.0 API.
            * Instancing-safe: a transform owning a SHARED shape is never
              restored non-trivially (its channels would displace every other
              instance) and never dragged along by a restored ancestor — the
              ancestor's delta is absorbed into its local matrix instead, so
              its world position (and its whole subtree's) is preserved.

        Parameters:
            objects (str/obj/list): Transforms to restore.
            prefix (str): Bake-attr prefix used by ``store_transforms``.
                Default ``"original"``.
            delete_attrs (bool): Delete each ``{prefix}_{T,R,S}_bake`` attr
                after consuming it.  Default True; channels NOT in
                *channels* are never consumed so their bake history
                remains available for future restore calls.
            channels (iterable): Optional subset of ``{"translate",
                "rotate", "scale"}`` restricting which channels to
                restore.  ``None`` (default) restores all three.
            traverse (bool): If True, also restore every descendant
                transform of the given objects, top-down.  Mirrors
                ``store_transforms(traverse=True)`` / ``freeze_transforms
                (freeze_children=True)`` so a whole hierarchy unfreezes
                from one root call.  Restore a hierarchy in ONE call
                (a list, or this flag) — world-space geometry snapshots
                are per call, so splitting a hierarchy across calls lets
                an earlier call's restored ancestors displace a later
                call's geometry reads.

        Returns:
            list: Object names successfully restored.
        """
        return cls._restore_transforms(
            objects=objects,
            prefix=prefix,
            delete_attrs=delete_attrs,
            channels=channels,
            traverse=traverse,
        )

    @staticmethod
    @CoreUtils.undoable
    def clear_stored_transforms(objects, prefix="original") -> List[str]:
        """Delete the per-channel bake attrs without restoring.

        Use when you committed to the frozen state and just want to remove
        the ``{prefix}_T_bake`` / ``{prefix}_R_bake`` / ``{prefix}_S_bake``
        attributes that ``store_transforms`` left behind. Safe to call on
        objects that don't have stored attributes (silently skipped).

        Parameters:
            objects (str/obj/list): Transforms to clean up.
            prefix (str): Custom-attr prefix used by ``store_transforms``.

        Returns:
            list: Object names from which stored attrs were deleted.
        """
        return XformUtils._clear_stored_transforms(objects=objects, prefix=prefix)

    @classmethod
    @CoreUtils.undoable
    def repair_stored_transforms(
        cls,
        objects=None,
        prefix="original",
        dry_run=False,
        clear_stale=False,
        tolerance=1e-4,
    ):
        """Triage bake history left by earlier tool versions, restore only
        what is provably clean, and (optionally) clear the residue.

        Earlier versions of the freeze tooling stamped the bake history
        BEFORE the freeze ran, so every object the freeze then skipped —
        instanced, connection-blocked — kept its live channels *and* gained a
        bake claiming those same values.  Un-freezing such a scene composes
        that bake on top of channels that were never zeroed: objects fly.
        (Measured on a production module scene: 481 baked transforms, 305 of
        them never actually frozen, drifts up to ~18,000 units.)  Freezing
        again doesn't help — the new stamp composes onto the stale history.

        Classification per baked transform:
            * ``frozen`` — channels at identity (within *tolerance*): the
              freeze demonstrably ran, the bake is trustworthy.  Restored.
            * ``stale`` — live (non-identity) channels: either a stamp whose
              freeze was skipped (residue), or a legitimate freeze the user
              moved afterwards.  The two are indistinguishable from scene
              state, so these are NEVER restored here; they are cleared only
              with ``clear_stale=True``.  (For the frozen-then-moved case,
              call :meth:`restore_transforms` directly — it composes.)
            * ``degenerate`` — a bake no restore could apply (zero or
              non-finite scale component): cleared with ``clear_stale=True``.

        Parameters:
            objects (str/obj/list): Transforms to triage.  ``None`` (default)
                sweeps every transform in the scene.
            prefix (str): Bake-attr prefix used by ``store_transforms``.
            dry_run (bool): Classify and report only — no scene writes.
            clear_stale (bool): Also delete the bake attrs of ``stale`` and
                ``degenerate`` nodes (their channels are left untouched —
                a skipped freeze never zeroed them, so they are already
                correct).  Explicit opt-in because it discards history.
            tolerance (float): Channel-identity tolerance for ``frozen``.

        Returns:
            dict: ``{"frozen": [...], "stale": [...], "degenerate": [...],
            "restored": [...], "cleared": [...]}`` (long names).
        """
        return cls._repair_stored_transforms(
            objects=objects,
            prefix=prefix,
            dry_run=dry_run,
            clear_stale=clear_stale,
            tolerance=tolerance,
        )

    @staticmethod
    def has_stored_transforms(objects, prefix="original"):
        """Check if objects have any stored bake history.

        Returns:
            dict: Mapping of object short names to bool (True if any
            T/R/S bake attribute exists).
        """
        return XformUtils._has_stored_transforms(objects=objects, prefix=prefix)

    @staticmethod
    def channels_at_identity(node, tolerance=1e-4):
        """True when *node*'s T/R/S channels sit at identity.

        The proof that a stamped freeze actually RAN — and therefore that its
        bake history can be trusted. A bake on a node whose channels are still
        live is stale: the freeze was skipped (instanced, connection-blocked)
        and the stamp claims values that were never baked out. Every consumer
        of bake history needs this test before acting on one, so it is public
        rather than inline in ``repair_stored_transforms``.
        """
        return XformUtils._channels_at_identity(node=node, tolerance=tolerance)

    @staticmethod
    def get_stored_transforms(node, prefix="original"):
        """Read one node's stored pre-freeze channels back as plain values.

        The read side of the freeze/unfreeze contract, and the primitive every
        consumer of that history shares — a frozen transform reports identity
        channels, so anything that needs the object's *authored* frame (pivot
        orientation, mirror/cut axes, instance matching, export checks) has to
        come through here rather than reading the live matrix.

        Unlike :meth:`has_stored_transforms` (a name→bool map keyed by long
        path) this takes a single node and resolves the name itself, so a short
        name works.

        Parameters:
            node (str/obj): The transform to read.
            prefix (str): Attribute name prefix (default: ``"original"``).

        Returns:
            (dict/None): ``{"translate": [x, y, z], "rotate": om.MQuaternion,
            "scale": [x, y, z], "matrix": om.MMatrix}`` — the pre-freeze local
            transform — or ``None`` when the node carries no bake history.
            Absent channels read as identity, so the dict is always complete.
        """
        return XformUtils._get_stored_transforms(node=node, prefix=prefix)

    @classmethod
    @CoreUtils.undoable
    def reset_translation(cls, objects):
        """Reset the translation transformations on the given object(s)."""
        return cls._reset_translation(objects=objects)

    @classmethod
    def set_translation_to_pivot(cls, node):
        """Set an object's translation value from its pivot location."""
        return cls._set_translation_to_pivot(node=node)

    @staticmethod
    def get_manip_pivot_matrix(obj, **kwargs):
        """Return the object's transform matrix using xform, allowing kwargs override.

        Returns:
            om.MMatrix: The resulting transformation matrix.
        """
        return XformUtils._get_manip_pivot_matrix(obj=obj, **kwargs)

    @staticmethod
    def set_manip_pivot_matrix(obj, matrix, **kwargs) -> None:
        """Apply a transformation matrix's position and orientation to the manip pivot."""
        return XformUtils._set_manip_pivot_matrix(obj=obj, matrix=matrix, **kwargs)

    @classmethod
    @CoreUtils.undoable
    def restore_original_axes(cls, objects=None, prefix="original"):
        """Aim the manipulator at an object's PRE-FREEZE axes, without un-freezing it.

        The companion to Un-Freeze for the common case where the freeze is
        wanted but the authored frame is still needed to work in: a frozen
        object's local axes are the world's, so the gizmo can no longer show
        the frame the asset was modelled in. This reads it back out of the
        stored bake history and points the manipulator there — non-destructive,
        nothing about the object changes.

        ``manipPivot`` is a single global manipulator, so with several objects
        selected the LAST one wins (Maya's own convention for the manipulator).

        Parameters:
            objects (str/obj/list/None): Transforms; None uses the selection.
            prefix (str): Bake-attr prefix used by ``store_transforms``.

        Returns:
            (str/None): The node the manipulator was aimed at, or None when
            nothing in the selection carries bake history.
        """
        return cls._restore_original_axes(objects=objects, prefix=prefix)

    @classmethod
    def get_pivot_options(cls):
        """Returns a list of supported pivot options."""
        return cls._get_pivot_options()

    @classmethod
    def clear_manip_cache(cls):
        """Clears the cached manipulator pivot data."""
        return cls._clear_manip_cache()

    @classmethod
    def snapshot_manip_pivot(cls, node):
        """Snapshot the current manipulator pivot state for the given node into the cache."""
        return cls._snapshot_manip_pivot(node=node)

    @classmethod
    def get_operation_axis_matrix(cls, node, pivot: str):
        """Determines the pivot matrix (orientation + position) for transformations.

        Pivot modes: ``"object"`` (the node's live local axes), ``"original"``
        (its **pre-freeze** local axes, read from the stored bake history),
        ``"manip"``, ``"baked"``, ``"world"``, a bounding-box key, or an
        explicit point.

        ``"original"`` exists because a freeze zeroes the rotate channel: a
        frozen object's local axes ARE the world axes, so ``"object"`` silently
        degrades into ``"world"`` and every axis-based op (mirror, cut-on-axis,
        radial/linear duplicate, face-on-axis selection) loses the frame the
        asset was authored in. Composing the stored rotate bake back on
        recovers it. Nodes with no bake history fall back to ``"object"``, so
        the mode is always safe to pass.

        Returns:
            om.MMatrix: The 4x4 transfomation matrix.
        """
        return cls._get_operation_axis_matrix(node=node, pivot=pivot)

    @classmethod
    def get_operation_axis_pos(cls, node, pivot, axis_index=None):
        """Determines the pivot position for mirroring/cutting along a specified axis or all axes."""
        return cls._get_operation_axis_pos(
            node=node, pivot=pivot, axis_index=axis_index
        )

    @staticmethod
    @CoreUtils.undoable
    def align_pivot_to_selection(align_from=None, align_to=None, translate=True):
        """Align one object's pivot point to another using 3-point alignment."""
        return XformUtils._align_pivot_to_selection(
            align_from=align_from, align_to=align_to, translate=translate
        )

    @staticmethod
    def reset_pivot_transforms(objects=None) -> None:
        """Reset Pivot Transforms for the specified objects or selected objects."""
        return XformUtils._reset_pivot_transforms(objects=objects)

    @staticmethod
    @CoreUtils.undoable
    def world_align_pivot(
        objects=None,
        pivot_type: str = "object",
        mode: str = "set",
    ):
        """Get or set a world-aligned pivot for the specified objects or components.

        Parameters:
            objects (str/list/None): Objects *or* components. None (default) operates on
                the active selection.
            pivot_type (str): 'manip' sets a temporary manipulator pivot; 'object' sets
                the permanent object pivot.
            mode (str): 'set' applies the pivot, 'get' returns it without changing the scene.

        Component selections are honoured rather than collapsed to their object: the pivot
        lands on the selected components' bounding-box center — per owning transform for
        'object', the combined center for 'manip' — instead of on the object's existing
        rotate pivot. The selection is never touched (the whole op addresses nodes by name),
        so a component selection survives and Maya stays in component mode. Components with
        no measurable extent fall back to their object's rotate pivot rather than collapsing
        the pivot onto the world origin.

        Returns:
            (bool)(dict)(None): 'set' → success; 'get' → the pivot dict, or None if there
            was nothing to align.
        """
        return XformUtils._world_align_pivot(
            objects=objects, pivot_type=pivot_type, mode=mode
        )

    @staticmethod
    @CoreUtils.undoable
    def bake_pivot(
        objects, position=False, orientation=False, preserve_instancing=True
    ):
        """Bake the pivot orientation and position of the given object(s).

        ``preserve_instancing`` (default True): run the bake inside
        ``NodeUtils.preserve_instancing``.  Baking a pivot position is
        implemented — here and in Maya's own ``bakeCustomToolPivot`` — as
        ``move -preserveGeometryPosition``, i.e. the transform moves onto the
        pivot and the SHAPE'S POINTS are offset back so nothing appears to
        move.  On an instanced object those points are shared, so every
        sibling instance jumps by the pivot delta while the baked object
        stays put.  The scope forks the shared shapes for the duration and
        re-instances them in place afterwards; pass False for the raw
        (sibling-moving) behavior.
        """
        objects = _XformUtilsInternal._resolve_transforms(objects)

        with contextlib.ExitStack() as stack:
            if preserve_instancing:
                stack.enter_context(NodeUtils.preserve_instancing(objects))
            _XformUtilsInternal._bake_pivot(objects, position, orientation)

    @classmethod
    @CoreUtils.undoable
    def transfer_pivot(
        cls,
        objects,
        translate: bool = False,
        rotate: bool = False,
        scale: bool = False,
        bake: bool = True,
        world_space: bool = True,
        mirror: str = "",
        select_targets_after_transfer: bool = False,
        preserve_instancing: bool = True,
    ):
        """Transfer the pivot from the first given object to the remaining given objects.

        Parameters:
            bake (bool): Whether the transferred pivot is PERMANENT or a
                temporary manipulator pivot.

                - ``True`` (default) — write it into each target's own pivot
                  attributes (``rotatePivot``/``scalePivot``, and
                  ``rotateAxis`` for the orientation).  Survives selection
                  changes and is saved with the scene.  Geometry is pinned,
                  so re-framing a pivot never moves the object.
                - ``False`` — leave every target attribute untouched and aim
                  Maya's manipulator at the source's pivot instead.  Transient:
                  ``manipPivot`` is a single global manipulator, so the targets
                  share one pivot and Maya's ``manipPivotReset`` clears it.
                  ``scale`` and ``world_space`` do not apply — the manipulator
                  has no separate scale pivot, and its override is always
                  world-space.

            preserve_instancing (bool): Run the permanent world-space ``rotate``
                pass inside ``NodeUtils.preserve_instancing``.  That pass
                re-writes the target's vertex positions to pin its geometry
                while the transform re-orients — a shared datablock on an
                instanced target would swing every sibling instead.  Moot when
                ``bake`` is False: a manipulator pivot writes no geometry.
            mirror (str): Optionally transfer a *mirror* of the source pivot instead of a direct
                copy. Accepts ``"x"``, ``"y"`` or ``"z"`` (case-insensitive) to reflect the
                transferred pivot across the axis-plane through the origin — the pivot position
                is reflected and its orientation is conjugated so the mirrored frame stays a
                valid right-handed rotation (useful when the target is a mirrored copy of the
                source). The reflection is taken in the operating space — world when
                ``world_space`` is True (the usual mirrored-copy case), otherwise the object's
                local space. Empty (the default) transfers the pivot unmirrored.
        """
        return cls._transfer_pivot(
            objects=objects,
            translate=translate,
            rotate=rotate,
            scale=scale,
            bake=bake,
            world_space=world_space,
            mirror=mirror,
            select_targets_after_transfer=select_targets_after_transfer,
            preserve_instancing=preserve_instancing,
        )

    @staticmethod
    @CoreUtils.undoable
    def aim_object_at_point(objects, target_pos, aim_vect=(1, 0, 0), up_vect=(0, 1, 0)):
        """Aim the given object(s) at the given world space position."""
        return XformUtils._aim_object_at_point(
            objects=objects, target_pos=target_pos, aim_vect=aim_vect, up_vect=up_vect
        )

    @staticmethod
    def orient_to_vector(
        transform,
        aim_vector=(1, 0, 0),
        up_vector=(0, 1, 0),
    ):
        """Orients a transform so its local +X aims along the given world-space vector."""
        return XformUtils._orient_to_vector(
            transform=transform, aim_vector=aim_vector, up_vector=up_vector
        )

    @classmethod
    @CoreUtils.undoable
    def rotate_axis(cls, objects, target_pos):
        """Aim the given object at the given world space position. Rotations applied to
        rotated channel; geometry is transformed so it does not appear to move.
        """
        return cls._rotate_axis(objects=objects, target_pos=target_pos)

    @staticmethod
    def get_orientation(objects, returned_type="point"):
        """Get an objects orientation as a point or vector.

        Returns:
            (tuple)(list) If 'objects' given as a list, a list of tuples will be returned.
        """
        return XformUtils._get_orientation(objects=objects, returned_type=returned_type)

    @staticmethod
    def get_dist_between_two_objects(a, b):
        """Get the magnatude of a vector using the center points of two given objects.

        Returns:
            (float)
        """
        return XformUtils._get_dist_between_two_objects(a=a, b=b)

    @staticmethod
    def get_center_point(objects):
        """Get the bounding box center point of any given object(s).

        Returns:
            (tuple) position as xyz float values.
        """
        return XformUtils._get_center_point(objects=objects)

    @staticmethod
    def get_bounding_box(objects, value="", world_space=True, return_valid_keys=False):
        """Calculate and retrieve specific properties of the bounding box for the given object(s) or component(s)."""
        return XformUtils._get_bounding_box(
            objects=objects,
            value=value,
            world_space=world_space,
            return_valid_keys=return_valid_keys,
        )

    @classmethod
    def sort_by_bounding_box_value(
        cls, objects, value="volume", descending=True, also_return_value=False
    ):
        """Sort the given objects by their bounding box value."""
        return cls._sort_by_bounding_box_value(
            objects=objects,
            value=value,
            descending=descending,
            also_return_value=also_return_value,
        )

    @staticmethod
    @CoreUtils.undoable
    def align_using_three_points(vertices):
        """Move and align the object defined by the first 3 points to the last 3 points."""
        return XformUtils._align_using_three_points(vertices=vertices)

    @staticmethod
    def is_overlapping(a, b, tolerance=0.001):
        """Check if the vertices in a and b are overlapping within the given tolerance."""
        return XformUtils._is_overlapping(a=a, b=b, tolerance=tolerance)

    @staticmethod
    def check_objects_against_plane(
        objects,
        plane_point,
        plane_normal,
        return_type: str = "bool",
    ):
        """General method to check if any object's geometry is below a defined plane."""
        return XformUtils._check_objects_against_plane(
            objects=objects,
            plane_point=plane_point,
            plane_normal=plane_normal,
            return_type=return_type,
        )

    @staticmethod
    def get_vertex_positions(objects, worldSpace=True):
        """Get all vertex positions for the given objects.

        Returns:
            (list) Nested lists if multiple objects given.
        """
        return XformUtils._get_vertex_positions(objects=objects, worldSpace=worldSpace)

    @classmethod
    def get_matching_verts(cls, a, b, world_space=False):
        """Find any vertices which point locations match between two given mesh.

        Returns:
            (list) nested tuples with int values representing matching vertex pairs.
        """
        return cls._get_matching_verts(a=a, b=b, world_space=world_space)

    @classmethod
    def order_by_distance(cls, objects, reference_point=None, reverse=False):
        """Order the given objects by their distance from the given reference point.

        Returns:
            (list) ordered objects (as plain strings)
        """
        return cls._order_by_distance(
            objects=objects, reference_point=reference_point, reverse=reverse
        )

    @staticmethod
    @CoreUtils.undoable
    def align_vertices(mode, average=False, edgeloop=False):
        """Align selected vertices along one or more axes."""
        return XformUtils._align_vertices(mode=mode, average=average, edgeloop=edgeloop)

    @staticmethod
    def get_translation(node, world: bool = False):
        """Translation as ``om.MVector``.

        ``world=False`` returns the object-space translation (the default for
        child translation); ``world=True`` returns world space.
        """
        return XformUtils._get_translation(node=node, world=world)

    @staticmethod
    def get_object_matrix(node, world: bool = False):
        """Local or world matrix as ``om.MMatrix``."""
        return XformUtils._get_object_matrix(node=node, world=world)

    @staticmethod
    def set_object_matrix(node, value, world: bool = False) -> None:
        """Apply *value* to *node*'s local or world transformation matrix.

        *value* may be an ``om.MMatrix`` (anything with ``getElement(r, c)``)
        or a 16-element iterable in row-major order.
        """
        return XformUtils._set_object_matrix(node=node, value=value, world=world)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
