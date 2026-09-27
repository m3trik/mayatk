# !/usr/bin/python
# coding=utf-8
"""The stored-transform (bake history) record behind :class:`mayatk.XformUtils`.

Freezing leaves each node a cumulative per-channel record of what it baked
away; this module writes, reads, restores, repairs and clears that record --
including putting back geometry the freeze moved. Reached through
:class:`mayatk.XformUtils`; nothing here is called directly.

The freeze/unfreeze contract is *cumulative*: each freeze composes the current
local TRS onto a per-channel bake history; each unfreeze pushes that bake
history (composed with whatever the user did since) back into the local
channels. Tracking T/R/S separately keeps composition clean regardless of which
channels the user freezes (you can freeze T, then R, and unfreeze them
independently without rotation entangling the translation).
"""

from __future__ import annotations

import math
from typing import List, Set, Optional

try:
    import maya.cmds as cmds
    from maya.api import OpenMaya as om
except Exception:
    cmds = om = None


from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.undo_recorder import UndoRecorder
from mayatk.node_utils.attributes._attributes import Attributes
from mayatk.xform_utils.matrices import Matrices


class _StoredTransformsInternal:
    """Private helpers and ``XformUtils`` method bodies; see the module docstring."""

    @staticmethod
    def _mmatrix_to_flat(m) -> List[float]:
        if hasattr(m, "getElement"):
            return [m.getElement(r, c) for r in range(4) for c in range(4)]
        return list(m)

    @staticmethod
    def _decompose_local(node):
        """Read ``node``'s T/R/S CHANNEL values as ``(t_vec, r_quat, s_vec)``.

        Reads the translate/rotate/scale channel attributes directly rather
        than decomposing the local matrix.  Maya's local matrix folds in
        ``rotatePivotTranslate`` / ``scalePivotTranslate`` (left non-zero by
        ``makeIdentity``), so the matrix translation row may not match the
        channel value.  For freeze/unfreeze accumulation we want the channel
        values — what the user sees and edits.
        """
        t_raw = cmds.getAttr(f"{node}.translate")[0]
        r_raw = cmds.getAttr(f"{node}.rotate")[0]
        s_raw = cmds.getAttr(f"{node}.scale")[0]
        rot_order = cmds.getAttr(f"{node}.rotateOrder") or 0
        euler = om.MEulerRotation(
            math.radians(r_raw[0]),
            math.radians(r_raw[1]),
            math.radians(r_raw[2]),
            rot_order,
        )
        return (
            om.MVector(t_raw[0], t_raw[1], t_raw[2]),
            euler.asQuaternion(),
            [s_raw[0], s_raw[1], s_raw[2]],
        )

    @staticmethod
    def _compose_local(t_vec, r_quat, s_vec):
        """Build an ``MMatrix`` from a translation vector, rotation quaternion, and scale vector."""
        tm = om.MTransformationMatrix()
        tm.setTranslation(t_vec, om.MSpace.kTransform)
        tm.setRotation(r_quat)
        tm.setScale(s_vec, om.MSpace.kTransform)
        return tm.asMatrix()

    #: The bake helpers below probe "does this attribute exist" with
    #: ``cmds.objExists("<node>.<attr>")`` rather than
    #: ``cmds.attributeQuery(attr, node=..., exists=True)``. They agree on every
    #: shape these callers pass (long DAG paths, namespaces, compound children,
    #: multis, shapes — measured), but objExists is ~20x cheaper (0.017 ms vs
    #: 0.335 ms per two probes) and this runs FOUR times per node across the
    #: whole subtree: it was 41% of a 2000-node freeze. The one behavioural
    #: difference is a NONEXISTENT node, where attributeQuery raises and
    #: objExists returns False — the softer answer, and these callers only ever
    #: run on nodes they have just enumerated.

    @staticmethod
    def _read_bake_t(node, t_attr):
        """Read the stored translation bake as an ``MVector``; identity if missing/unset."""
        if not cmds.objExists(f"{node}.{t_attr}"):
            return om.MVector(0.0, 0.0, 0.0)
        raw = cmds.getAttr(f"{node}.{t_attr}")
        if raw and isinstance(raw[0], (list, tuple)):
            raw = raw[0]
        if raw is None or any(v is None for v in raw):
            return om.MVector(0.0, 0.0, 0.0)
        return om.MVector(raw[0], raw[1], raw[2])

    @staticmethod
    def _read_bake_r(node, r_attr):
        """Read the stored rotation bake as an ``MQuaternion``; identity if missing/unset."""
        if not cmds.objExists(f"{node}.{r_attr}"):
            return om.MQuaternion()
        raw = cmds.getAttr(f"{node}.{r_attr}")
        if raw and isinstance(raw[0], (list, tuple)):
            raw = [v for row in raw for v in row]
        if raw is None or any(v is None for v in raw):
            return om.MQuaternion()
        mat = om.MMatrix(list(raw))
        return om.MTransformationMatrix(mat).rotation(asQuaternion=True)

    @staticmethod
    def _read_bake_s(node, s_attr):
        """Read the stored scale bake as a 3-element list; identity (1,1,1) if missing/unset."""
        if not cmds.objExists(f"{node}.{s_attr}"):
            return [1.0, 1.0, 1.0]
        raw = cmds.getAttr(f"{node}.{s_attr}")
        if raw and isinstance(raw[0], (list, tuple)):
            raw = raw[0]
        if raw is None or any(v is None for v in raw):
            return [1.0, 1.0, 1.0]
        return [raw[0], raw[1], raw[2]]

    @staticmethod
    def _write_bake_t(node, t_attr, t_vec):
        if not cmds.objExists(f"{node}.{t_attr}"):
            cmds.addAttr(node, ln=t_attr, dt="double3", keyable=False)
        plug = f"{node}.{t_attr}"
        cmds.setAttr(plug, t_vec[0], t_vec[1], t_vec[2], type="double3")
        if cmds.getAttr(plug, keyable=True) or cmds.getAttr(plug, channelBox=True):
            cmds.setAttr(plug, keyable=False, channelBox=False)

    @staticmethod
    def _write_bake_r(node, r_attr, r_quat):
        if not cmds.objExists(f"{node}.{r_attr}"):
            cmds.addAttr(node, ln=r_attr, at="matrix", keyable=False)
        plug = f"{node}.{r_attr}"
        flat = _StoredTransformsInternal._mmatrix_to_flat(r_quat.asMatrix())
        cmds.setAttr(plug, *flat, type="matrix")
        if cmds.getAttr(plug, keyable=True) or cmds.getAttr(plug, channelBox=True):
            cmds.setAttr(plug, keyable=False, channelBox=False)

    @staticmethod
    def _write_bake_s(node, s_attr, s_vec):
        if not cmds.objExists(f"{node}.{s_attr}"):
            cmds.addAttr(node, ln=s_attr, dt="double3", keyable=False)
        plug = f"{node}.{s_attr}"
        cmds.setAttr(plug, s_vec[0], s_vec[1], s_vec[2], type="double3")
        if cmds.getAttr(plug, keyable=True) or cmds.getAttr(plug, channelBox=True):
            cmds.setAttr(plug, keyable=False, channelBox=False)

    @staticmethod
    def _bake_attr_names(prefix):
        """``(t_attr, r_attr, s_attr)`` triple used by store/restore/clear/has."""
        return f"{prefix}_T_bake", f"{prefix}_R_bake", f"{prefix}_S_bake"

    @staticmethod
    def _opm_marker_name(prefix):
        """Attr flagging a bake made by ``freeze_to_opm`` rather than a real
        geometry bake — the two have DIFFERENT inverses."""
        return f"{prefix}_opm_bake"

    @staticmethod
    def _mark_opm_bake(node, prefix="original"):
        attr = _StoredTransformsInternal._opm_marker_name(prefix)
        if not cmds.attributeQuery(attr, node=node, exists=True):
            cmds.addAttr(node, ln=attr, at="bool", keyable=False)
        cmds.setAttr(f"{node}.{attr}", True)

    @staticmethod
    def _has_opm_bake(node, prefix="original"):
        attr = _StoredTransformsInternal._opm_marker_name(prefix)
        return bool(
            cmds.attributeQuery(attr, node=node, exists=True)
            and cmds.getAttr(f"{node}.{attr}")
        )

    @classmethod
    def _accumulate_bake(
        cls, node, local, channels, accumulate=True, prefix="original"
    ):
        """Compose *local* ``(t_vec, r_quat, s_vec)`` onto ``node``'s bake
        history, for each channel named in *channels*.

        Single source of truth for the cumulative contract, shared by
        :meth:`XformUtils.store_transforms` — which passes the node's CURRENT
        local — and :meth:`XformUtils.freeze_transforms`, which passes a
        PRE-freeze snapshot committed only for the transforms that actually
        froze.
        """
        t_attr, r_attr, s_attr = cls._bake_attr_names(prefix)
        cur_t, cur_r, cur_s = local

        if "translate" in channels:
            old_t = (
                cls._read_bake_t(node, t_attr) if accumulate else om.MVector(0, 0, 0)
            )
            new_t = old_t + cur_t
            cls._write_bake_t(node, t_attr, [new_t.x, new_t.y, new_t.z])

        if "rotate" in channels:
            old_r = cls._read_bake_r(node, r_attr) if accumulate else om.MQuaternion()
            cls._write_bake_r(node, r_attr, old_r * cur_r)

        if "scale" in channels:
            old_s = cls._read_bake_s(node, s_attr) if accumulate else [1.0, 1.0, 1.0]
            cls._write_bake_s(node, s_attr, [old_s[i] * cur_s[i] for i in range(3)])

    @staticmethod
    def _apply_clean_local(node, t_vec, r_quat, s_vec):
        """Write target T/R/S to ``node`` and zero any pivot offsets.

        ``makeIdentity`` leaves non-zero ``rotatePivotTranslate`` /
        ``scalePivotTranslate`` behind so the world pivot stays put across the
        freeze.  Those offsets would otherwise fold into the channel values
        when we restore via ``cmds.xform(matrix=...)`` — translate ends up
        shifted by the pivot delta.  Writing channels directly with the
        pivots cleared sidesteps the decomposition entirely.
        """
        with Attributes.temporarily_unlock([node]):
            for attr in (
                "rotatePivot",
                "scalePivot",
                "rotatePivotTranslate",
                "scalePivotTranslate",
            ):
                if cmds.attributeQuery(attr, node=node, exists=True):
                    cmds.setAttr(f"{node}.{attr}", 0.0, 0.0, 0.0, type="double3")

            if cmds.attributeQuery("rotateAxis", node=node, exists=True):
                cmds.setAttr(f"{node}.rotateAxis", 0.0, 0.0, 0.0, type="double3")

            cmds.setAttr(f"{node}.translate", t_vec.x, t_vec.y, t_vec.z, type="double3")
            cmds.setAttr(f"{node}.scale", s_vec[0], s_vec[1], s_vec[2], type="double3")

            rot_order = cmds.getAttr(f"{node}.rotateOrder") or 0
            euler = r_quat.asEulerRotation()
            euler.reorderIt(rot_order)
            cmds.setAttr(
                f"{node}.rotate",
                math.degrees(euler.x),
                math.degrees(euler.y),
                math.degrees(euler.z),
                type="double3",
            )

    @staticmethod
    def _shape_fn(shape: str):
        """Function set for a supported shape, or None.

        Supports mesh (``MFnMesh``), nurbsCurve (``MFnNurbsCurve``), and
        nurbsSurface (``MFnNurbsSurface``). Other shape types return None.
        """
        if om is None or cmds is None:
            return None
        node_type = cmds.nodeType(shape)
        fn_class = {
            "mesh": om.MFnMesh,
            "nurbsCurve": om.MFnNurbsCurve,
            "nurbsSurface": om.MFnNurbsSurface,
        }.get(node_type)
        if fn_class is None:
            return None
        sel = om.MSelectionList()
        sel.add(shape)
        return fn_class(sel.getDagPath(0))

    @staticmethod
    def _get_shape_points_world(shape: str):
        """Snapshot a shape's points in world space, or None if unsupported.

        Used by ``restore_transforms`` phase 1: the snapshot must be taken
        before ANY transform in the batch is written, or a descendant's read
        would include its ancestors' already-restored transforms.
        """
        fn = _StoredTransformsInternal._shape_fn(shape)
        if fn is None:
            return None
        if isinstance(fn, om.MFnMesh):
            return fn.getPoints(om.MSpace.kWorld)
        return fn.cvPositions(om.MSpace.kWorld)

    @staticmethod
    def _set_shape_points_object(shape: str, points, transform_matrix) -> None:
        """Write snapshotted world-space *points* transformed by
        *transform_matrix* (the inverse of the shape's final world matrix)
        back in object space. Vectorized via the OpenMaya 2.0 API — O(1)
        cmds calls regardless of point count — and recorded on the undo queue
        (``UndoRecorder``), so an undo puts the points back with the transform.
        """
        fn = _StoredTransformsInternal._shape_fn(shape)
        if fn is None:
            return
        for i in range(len(points)):
            points[i] = points[i] * transform_matrix
        with UndoRecorder.record() as recorder, recorder.points(fn):
            if isinstance(fn, om.MFnMesh):
                fn.setPoints(points, om.MSpace.kObject)
            elif isinstance(fn, om.MFnNurbsCurve):
                fn.setCVPositions(points, om.MSpace.kObject)
                fn.updateCurve()
            else:  # MFnNurbsSurface
                fn.setCVPositions(points, om.MSpace.kObject)
                fn.updateSurface()

    @staticmethod
    def _nearest_known_ancestor(path: str, known) -> Optional[str]:
        """Nearest STRICT ancestor of *path* present in *known*, or None.

        Loops on ``"|"`` rather than on truthiness: a name with no separator
        (a short name reaching here by mistake) re-``rsplit``\\s to itself
        forever, hanging Maya.
        """
        parent = path.rsplit("|", 1)[0]
        while "|" in parent:
            if parent in known:
                return parent
            parent = parent.rsplit("|", 1)[0]
        return None

    @staticmethod
    def _owns_instanced_shape(transform: str) -> bool:
        """True when any non-intermediate shape under *transform* has several
        DAG parents (is shared with other transforms)."""
        for shape in (
            cmds.listRelatives(
                transform, shapes=True, noIntermediate=True, fullPath=True
            )
            or []
        ):
            if len(cmds.listRelatives(shape, allParents=True, fullPath=True) or []) > 1:
                return True
        return False

    @classmethod
    def _plan_restore_geometry(cls, restored, current_worlds, final_worlds, boundaries):
        """Plan the compensation that keeps every world position fixed across
        a restore: point writes for ordinary shapes, LOCAL-MATRIX writes for
        instanced-shape owners.

        Not merely each restored node's OWN shapes.  ``makeIdentity`` on a
        group flattens the WHOLE subtree — every descendant transform's
        channels are zeroed and the composed matrix is baked into the leaf
        shape points.  Restoring the group's channels without
        counter-shifting those leaves therefore applies the restored matrix
        a *second* time: the mesh visibly jumps and rescales.  A group has no
        shapes of its own, so the own-shapes-only sweep compensated nothing
        at all.

        Each shape is carried by its nearest restored ancestor-or-self, whose
        world delta is ``A_cur⁻¹ · A_new``: an unrestored descendant keeps its
        local chain ``L``, so in Maya's row-vector convention
        ``W_new = L · A_new = (W_cur · A_cur⁻¹) · A_new``.

        A shape on several DAG paths cannot be point-baked — writing shared
        points would drag every other instance along.  Its owning transform
        becomes a *boundary* instead: the ancestor delta is absorbed into the
        boundary's local matrix (``L' = W_cur · A_new⁻¹ · A_cur · W_P_cur⁻¹``,
        preserving its world exactly), and its whole subtree is pruned from
        the sweep — nothing below a world-preserved transform moves.  Measured
        on a production module scene, the previous warn-but-move behaviour
        displaced 314 instanced meshes by the restored group's full delta.

        Every read here is world-space and must happen in phase 1, before
        any transform is written — a mid-restore read would fold an
        already-restored ancestor back into the descendant's snapshot.

        Parameters:
            restored: Long paths whose channels phase 2 will rewrite.
            current_worlds / final_worlds: Their world matrices, now / target.
            boundaries: Instanced-shape owners (targets demoted in the main
                loop + non-target owners found by this walk's caller).

        Returns:
            tuple: ``(point_writes, boundary_writes)`` —
            ``[(shape, world_points, inverse_new_world), ...]`` and
            ``[(transform, local_matrix_flat), ...]``.
        """
        if not restored:
            return [], []

        # One batched descendant query for the whole set rather than one per
        # restored node — a traverse restore of a deep rig would otherwise
        # re-walk the same subtree once per node.
        subtree = list(restored) + (
            cmds.listRelatives(restored, ad=True, type="transform", fullPath=True) or []
        )

        claim = set(final_worlds) | boundaries
        deltas = {}

        def claim_delta(anchor):
            """``A_cur⁻¹ · A_new`` for a restored anchor (cached), else None."""
            if anchor not in deltas:
                inv_cur = Matrices.safe_inverse(current_worlds[anchor])
                if inv_cur is None:
                    cmds.warning(
                        f"restore_transforms: '{anchor}' has a singular world matrix — "
                        "its subtree geometry was left uncompensated."
                    )
                deltas[anchor] = (
                    None if inv_cur is None else inv_cur * final_worlds[anchor]
                )
            return deltas[anchor]

        point_writes = []
        boundary_writes = []
        seen: Set[str] = set()
        for xf in subtree:
            if xf in seen:
                continue
            seen.add(xf)

            if xf in boundaries:
                # Absorb the nearest restored ancestor's delta into this
                # transform's local matrix so its world (and its entire
                # subtree, skipped via the claim search) stays put.  If the
                # nearest claim above is itself a boundary, that one already
                # preserves everything below it — including this transform.
                anchor = cls._nearest_known_ancestor(xf, claim)
                if anchor is None or anchor in boundaries:
                    continue  # nothing above it moves — nothing to absorb
                delta = claim_delta(anchor)
                if delta is None or delta.isEquivalent(om.MMatrix(), 1e-9):
                    continue
                if cls._transform_is_driven(xf):
                    cmds.warning(
                        f"restore_transforms: '{xf}' owns an instanced shape and "
                        "has driven transform channels — its subtree was left "
                        "uncompensated."
                    )
                    continue
                w_cur = om.MMatrix(cmds.xform(xf, q=True, matrix=True, worldSpace=True))
                parent_path = xf.rsplit("|", 1)[0]
                w_parent = om.MMatrix(
                    cmds.xform(parent_path, q=True, matrix=True, worldSpace=True)
                )
                inv_parent = Matrices.safe_inverse(w_parent)
                inv_new_anchor = Matrices.safe_inverse(final_worlds[anchor])
                if inv_parent is None or inv_new_anchor is None:
                    cmds.warning(
                        f"restore_transforms: '{xf}' — singular matrix in its "
                        "chain; subtree left uncompensated."
                    )
                    continue
                new_local = w_cur * inv_new_anchor * current_worlds[anchor] * inv_parent
                boundary_writes.append((xf, cls._mmatrix_to_flat(new_local)))
                continue

            shapes = (
                cmds.listRelatives(xf, shapes=True, noIntermediate=True, fullPath=True)
                or []
            )
            if not shapes:
                continue

            # The nearest restored ancestor-or-self carries this shape; its
            # delta already accounts for any restored node above it, because
            # phase 1 resolved final worlds top-down.  A boundary in between
            # means this subtree's world is preserved — nothing to bake.
            if xf in final_worlds:
                anchor = xf
            else:
                anchor = cls._nearest_known_ancestor(xf, claim)
                if anchor is None or anchor in boundaries:
                    continue
            delta = claim_delta(anchor)
            if delta is None or delta.isEquivalent(om.MMatrix(), 1e-9):
                # Identity delta (a trivial restore): nothing moves, so
                # rewriting every point would be wasted work — and on a
                # shared shape it would trip the instanced safety net below
                # for an operation that needs no compensation at all.
                continue

            w_cur = om.MMatrix(cmds.xform(xf, q=True, matrix=True, worldSpace=True))
            inverse_new_world = Matrices.safe_inverse(w_cur * delta)
            if inverse_new_world is None:
                cmds.warning(
                    f"restore_transforms: '{xf}' has a singular target matrix — "
                    "its geometry was left uncompensated."
                )
                continue

            for shape in shapes:
                # Safety net — instanced-shape owners are demoted to
                # boundaries before this walk, so this should never fire.
                if (
                    len(cmds.listRelatives(shape, allParents=True, fullPath=True) or [])
                    > 1
                ):
                    cmds.warning(
                        f"restore_transforms: '{shape}' is instanced — geometry "
                        "left uncompensated (it would move every instance)."
                    )
                    continue
                pts = cls._get_shape_points_world(shape)
                if pts is not None:
                    point_writes.append((shape, pts, inverse_new_world))
        return point_writes, boundary_writes

    @classmethod
    def _store_transforms(cls, objects, prefix, accumulate, traverse, channels):
        """Body of :meth:`XformUtils.store_transforms`."""
        valid_channels = {"translate", "rotate", "scale"}
        if channels is None:
            target_channels = valid_channels
        else:
            target_channels = set(channels) & valid_channels
            if not target_channels:
                return

        targets = (
            cmds.ls(CoreUtils.as_strings(objects), type="transform", long=True) or []
        )
        if traverse:
            seen = set(targets)
            for obj in list(targets):
                for child in (
                    cmds.listRelatives(obj, ad=True, type="transform", fullPath=True)
                    or []
                ):
                    if child not in seen:
                        targets.append(child)
                        seen.add(child)

        for obj in targets:
            cls._accumulate_bake(
                obj,
                cls._decompose_local(obj),
                target_channels,
                accumulate=accumulate,
                prefix=prefix,
            )

    @classmethod
    def _restore_transforms(cls, objects, prefix, delete_attrs, channels, traverse):
        """Body of :meth:`XformUtils.restore_transforms`."""
        valid_channels = {"translate", "rotate", "scale"}
        if channels is None:
            target_channels = valid_channels
        else:
            target_channels = set(channels) & valid_channels
            if not target_channels:
                return []
        t_attr, r_attr, s_attr = cls._bake_attr_names(prefix)
        restored = []

        targets = (
            cmds.ls(CoreUtils.as_strings(objects), type="transform", long=True) or []
        )
        if traverse:
            seen = set(targets)
            for obj in list(targets):
                for child in (
                    cmds.listRelatives(obj, ad=True, type="transform", fullPath=True)
                    or []
                ):
                    if child not in seen:
                        targets.append(child)
                        seen.add(child)
        # A bake made by freeze_to_opm has a DIFFERENT inverse: no geometry was
        # ever baked, so the whole counter-bake pipeline below would displace
        # it. Split those off and hand them to their own inverse. Doing it here
        # — before any planning or world snapshot — keeps the two paths from
        # interacting at all.
        opm_targets = [t for t in targets if cls._has_opm_bake(t, prefix)]
        if opm_targets:
            if target_channels == valid_channels:
                restored.extend(
                    cls.unfreeze_from_opm(
                        opm_targets, prefix=prefix, delete_attrs=delete_attrs
                    )
                )
            else:
                # An OPM freeze moves the whole local matrix into one plug, so
                # there is no per-channel take-back. Skipping is the only safe
                # answer — letting these through would counter-bake geometry
                # that was never baked.
                cmds.warning(
                    f"restore_transforms: {len(opm_targets)} node(s) carry an "
                    "offsetParentMatrix bake, which cannot be restored one "
                    "channel at a time — skipped. Restore them with all three "
                    "channels enabled, or call unfreeze_from_opm directly."
                )
            opm_set = set(opm_targets)
            targets = [t for t in targets if t not in opm_set]

        # Process top-down so a child's final world matrix can be derived
        # from its parent's already-computed final world matrix.
        targets.sort(key=lambda p: p.count("|"))

        # Phase 1 — plan and snapshot, no scene writes.  Everything world-
        # space (shape points, pivots, current matrices) must be captured
        # BEFORE any transform is written: restoring an ancestor moves its
        # descendants, so a mid-restore world read would fold the ancestor's
        # restored transform back into the descendant's geometry.
        #
        # ``boundaries`` — transforms owning an INSTANCED shape.  Shared
        # points cannot be counter-baked (every other instance would move),
        # so these absorb a restored ancestor's delta into their local matrix
        # instead, and their subtrees need no compensation at all.  Seeded
        # here with non-target owners inside the restore's reach; targets that
        # turn out to own instanced shapes are demoted into it below.
        plans = []
        final_worlds = {}
        current_worlds = {}
        boundaries: Set[str] = set()
        target_set = set(targets)
        for xf in targets + (
            cmds.listRelatives(targets, ad=True, type="transform", fullPath=True) or []
        ):
            if xf not in target_set and cls._owns_instanced_shape(xf):
                boundaries.add(xf)

        # Ancestor-lookup universe (restored nodes + boundaries), maintained
        # incrementally — a per-target union of the two key sets would be
        # quadratic over large restores.
        known: Set[str] = set(boundaries)

        def _drop(obj):
            """A target that won't be restored still needs boundary status if
            it owns an instanced shape — the pre-scan skipped all targets."""
            if cls._owns_instanced_shape(obj):
                boundaries.add(obj)
                known.add(obj)

        for obj in targets:
            has_t = cmds.attributeQuery(t_attr, node=obj, exists=True)
            has_r = cmds.attributeQuery(r_attr, node=obj, exists=True)
            has_s = cmds.attributeQuery(s_attr, node=obj, exists=True)
            if not (has_t or has_r or has_s):
                cmds.warning(
                    f"restore_transforms: '{obj}' has no stored bake history. Skipping."
                )
                _drop(obj)
                continue

            try:
                if cmds.referenceQuery(obj, isNodeReferenced=True):
                    cmds.warning(
                        f"restore_transforms: '{obj}' is a referenced node "
                        "(can't modify). Skipping."
                    )
                    _drop(obj)
                    continue
            except Exception:
                pass

            # ``_apply_clean_local`` rewrites translate/rotate/scale wholesale
            # (unrestored channels are written back at their current value),
            # so ANY driven TRS channel makes the write raise "a child
            # attribute … is locked or connected" — unlocking can't help, the
            # plug is connected. Skip coherently instead of aborting the batch
            # partway through, which would leave earlier objects restored and
            # their geometry already shifted.
            if cls._transform_is_driven(obj, channels=("translate", "rotate", "scale")):
                cmds.warning(
                    f"restore_transforms: '{obj}' has driven transform channels "
                    "(the restore would be overwritten on the next evaluation). "
                    "Skipping."
                )
                _drop(obj)
                continue

            local_current = om.MMatrix(
                cmds.xform(obj, q=True, matrix=True, objectSpace=True)
            )
            world_current = om.MMatrix(
                cmds.xform(obj, q=True, matrix=True, worldSpace=True)
            )
            # World pivot positions, re-anchored in phase 2 after the clean
            # channel write zeroes the pivot attrs.
            world_rp = cmds.xform(obj, q=True, rotatePivot=True, worldSpace=True)
            world_sp = cmds.xform(obj, q=True, scalePivot=True, worldSpace=True)
            cur_t, cur_r, cur_s = cls._decompose_local(obj)

            # Compose stored bake history with the current local TRS per
            # channel.  Channels not in target_channels stay at current.
            if "translate" in target_channels and has_t:
                stored_t = cls._read_bake_t(obj, t_attr)
                target_t = stored_t + cur_t
            else:
                target_t = cur_t

            if "rotate" in target_channels and has_r:
                stored_r = cls._read_bake_r(obj, r_attr)
                target_r = stored_r * cur_r
            else:
                target_r = cur_r

            if "scale" in target_channels and has_s:
                stored_s = cls._read_bake_s(obj, s_attr)
                target_s = [stored_s[i] * cur_s[i] for i in range(3)]
            else:
                target_s = cur_s

            # A restore that would actually change the channels cannot run on
            # a transform whose own shape is shared or whose transform sits on
            # several DAG paths — writing the channels would displace every
            # other instance (their compensation cannot be per-path).  A
            # TRIVIAL restore (identity bake) is fine and still consumes the
            # attrs.  Demoted instanced-shape owners become boundaries so the
            # ancestors' deltas still can't move them.
            rot_diff = target_r * cur_r.conjugate()  # identity ⇔ |w| ≈ 1
            trivial = (
                (target_t - cur_t).length() < 1e-6
                and abs(rot_diff.w) > 1.0 - 1e-9
                and max(abs(target_s[i] - cur_s[i]) for i in range(3)) < 1e-6
            )
            if not trivial and cls._owns_instanced_shape(obj):
                cmds.warning(
                    f"restore_transforms: '{obj}' owns an instanced (shared) shape — "
                    "restoring its channels would displace the other instances. "
                    "Skipped; bake history retained. (Uninstance first, or restore "
                    "the whole group's layout by other means.)"
                )
                boundaries.add(obj)
                known.add(obj)
                continue
            if not trivial and cls._is_multi_path(obj):
                cmds.warning(
                    f"restore_transforms: '{obj}' is instanced (several DAG paths) — "
                    "one set of channels cannot restore every path. Skipped."
                )
                _drop(obj)
                continue

            # The new clean local matrix is just T * R * S with zero
            # pivots and zero pivot translates — that's the state the
            # user expects after unfreeze.
            new_local = cls._compose_local(target_t, target_r, target_s)

            # In Maya's row-vector convention: world = local * parent.  The
            # parent's CURRENT world is recovered from this node's matrix
            # pair; it then has to absorb the restore of the nearest
            # ancestor that is part of this call (top-down order guarantees
            # that ancestor is already resolved).  Looking only at the
            # DIRECT parent is not enough — restoring a grandparent while
            # skipping the transform in between would leave this node
            # planned against a stale parent world.  A BOUNDARY in between
            # absorbs the ancestor's delta into its own local, so everything
            # below it — including this node's parent chain — keeps its
            # current world: no composition.
            inv_local = Matrices.safe_inverse(local_current)
            parent_world = (
                om.MMatrix() if inv_local is None else inv_local * world_current
            )
            ancestor = cls._nearest_known_ancestor(obj, known)
            if ancestor is not None and ancestor not in boundaries:
                inv_anc = Matrices.safe_inverse(current_worlds[ancestor])
                if inv_anc is not None:
                    parent_world = parent_world * (inv_anc * final_worlds[ancestor])
            new_world = new_local * parent_world

            if Matrices.safe_inverse(new_world) is None:
                cmds.warning(
                    f"restore_transforms: '{obj}' has singular target matrix. Skipping."
                )
                _drop(obj)
                continue
            final_worlds[obj] = new_world
            current_worlds[obj] = world_current
            known.add(obj)

            plans.append(
                (
                    obj,
                    (has_t, has_r, has_s),
                    (target_t, target_r, target_s),
                    (world_rp, world_sp),
                )
            )

        # Still phase 1 (reads only): every shape the restore will displace,
        # across the whole subtree — not just each node's own shapes — plus
        # the local-matrix compensation for instanced-shape boundaries.  Must
        # run after every final world is known and before any write.
        point_writes, boundary_writes = cls._plan_restore_geometry(
            [p[0] for p in plans], current_worlds, final_worlds, boundaries
        )

        # Phase 2 — apply.  Compensation first, as one block: the writes are
        # absolute (object-space points / local matrices) against matrices
        # already resolved in phase 1, so they are order-independent, whereas
        # the channel loop below must stay strictly top-down for its
        # world-space pivot re-anchor (which reads the live parent chain).
        for shape, pts, inverse_new_world in point_writes:
            cls._set_shape_points_object(shape, pts, inverse_new_world)

        for xf, local_flat in boundary_writes:
            try:
                with Attributes.temporarily_unlock([xf]):
                    cmds.xform(xf, objectSpace=True, matrix=local_flat)
            except Exception as exc:
                cmds.warning(
                    f"restore_transforms: could not compensate instanced-shape "
                    f"owner '{xf}' ({exc}) — its subtree will move with the "
                    "restored ancestor."
                )

        for (
            obj,
            (has_t, has_r, has_s),
            (target_t, target_r, target_s),
            (world_rp, world_sp),
        ) in plans:
            # Set channels directly so Maya doesn't fold lingering
            # ``rotatePivotTranslate`` / ``scalePivotTranslate`` (left by
            # ``makeIdentity``) into the new translate values.
            cls._apply_clean_local(obj, target_t, target_r, target_s)

            # Re-anchor the pivots at their pre-restore world position —
            # ``_apply_clean_local`` zeroed them to keep the channel write
            # clean.  xform's default -preserve rebuilds the pivot-translate
            # compensation so the object itself doesn't move.
            with Attributes.temporarily_unlock([obj]):
                cmds.xform(obj, rotatePivot=world_rp, worldSpace=True)
                cmds.xform(obj, scalePivot=world_sp, worldSpace=True)

            # Channels we just consumed are reset to identity bake so a
            # later freeze doesn't double-apply them.  Channels not yet
            # restored keep their bake history for future calls.
            if delete_attrs:
                if "translate" in target_channels and has_t:
                    if cmds.getAttr(f"{obj}.{t_attr}", lock=True):
                        cmds.setAttr(f"{obj}.{t_attr}", lock=False)
                    cmds.deleteAttr(f"{obj}.{t_attr}")
                if "rotate" in target_channels and has_r:
                    if cmds.getAttr(f"{obj}.{r_attr}", lock=True):
                        cmds.setAttr(f"{obj}.{r_attr}", lock=False)
                    cmds.deleteAttr(f"{obj}.{r_attr}")
                if "scale" in target_channels and has_s:
                    if cmds.getAttr(f"{obj}.{s_attr}", lock=True):
                        cmds.setAttr(f"{obj}.{s_attr}", lock=False)
                    cmds.deleteAttr(f"{obj}.{s_attr}")

            restored.append(obj)

        if restored:
            print(f"restore_transforms: Restored {len(restored)} object(s).")

        return restored

    @classmethod
    def _clear_stored_transforms(cls, objects, prefix):
        """Body of :meth:`XformUtils.clear_stored_transforms`."""
        cleared: List[str] = []
        # The OPM marker is part of the same stamp — leaving it behind would
        # make a later restore route a node with no history down the OPM path.
        attr_names = cls._bake_attr_names(prefix) + (cls._opm_marker_name(prefix),)
        for obj in (
            cmds.ls(CoreUtils.as_strings(objects), type="transform", long=True) or []
        ):
            removed_any = False
            for attr in attr_names:
                if cmds.attributeQuery(attr, node=obj, exists=True):
                    plug = f"{obj}.{attr}"
                    if cmds.getAttr(plug, lock=True):
                        cmds.setAttr(plug, lock=False)
                    cmds.deleteAttr(plug)
                    removed_any = True
            if removed_any:
                cleared.append(obj)
        if cleared:
            print(
                f"clear_stored_transforms: Cleared stored attrs on "
                f"{len(cleared)} object(s)."
            )
        return cleared

    @classmethod
    def _repair_stored_transforms(
        cls, objects, prefix, dry_run, clear_stale, tolerance
    ):
        """Body of :meth:`XformUtils.repair_stored_transforms`."""
        t_attr, r_attr, s_attr = cls._bake_attr_names(prefix)
        if objects is None:
            pool = cmds.ls(type="transform", long=True) or []
        else:
            pool = (
                cmds.ls(CoreUtils.as_strings(objects), type="transform", long=True)
                or []
            )

        result = {
            "frozen": [],
            "stale": [],
            "degenerate": [],
            "restored": [],
            "cleared": [],
        }
        for obj in pool:
            if not any(
                cmds.attributeQuery(a, node=obj, exists=True)
                for a in (t_attr, r_attr, s_attr)
            ):
                continue

            degenerate = False
            if cmds.attributeQuery(s_attr, node=obj, exists=True):
                stored_s = cls._read_bake_s(obj, s_attr)
                degenerate = any(
                    (not math.isfinite(v)) or abs(v) < 1e-6 for v in stored_s
                )
            if not degenerate and cmds.attributeQuery(t_attr, node=obj, exists=True):
                stored_t = cls._read_bake_t(obj, t_attr)
                degenerate = any(
                    not math.isfinite(v) for v in (stored_t.x, stored_t.y, stored_t.z)
                )
            if degenerate:
                result["degenerate"].append(obj)
                continue

            identity = cls.channels_at_identity(obj, tolerance)
            result["frozen" if identity else "stale"].append(obj)

        if not dry_run:
            if result["frozen"]:
                result["restored"] = cls.restore_transforms(
                    result["frozen"], prefix=prefix
                )
            if clear_stale and (result["stale"] or result["degenerate"]):
                result["cleared"] = cls.clear_stored_transforms(
                    result["stale"] + result["degenerate"], prefix=prefix
                )

        print(
            "repair_stored_transforms: "
            f"{len(result['frozen'])} frozen (trustworthy), "
            f"{len(result['stale'])} stale, "
            f"{len(result['degenerate'])} degenerate — "
            f"{len(result['restored'])} restored, {len(result['cleared'])} cleared"
            f"{' [dry run]' if dry_run else ''}."
        )
        return result

    @classmethod
    def _has_stored_transforms(cls, objects, prefix):
        """Body of :meth:`XformUtils.has_stored_transforms`."""
        result = {}
        attr_names = cls._bake_attr_names(prefix)
        for obj in (
            cmds.ls(CoreUtils.as_strings(objects), type="transform", long=True) or []
        ):
            has_stored = any(
                cmds.attributeQuery(attr, node=obj, exists=True) for attr in attr_names
            )
            result[obj] = has_stored
        return result

    @staticmethod
    def _channels_at_identity(node, tolerance):
        """Body of :meth:`XformUtils.channels_at_identity`."""
        node = str(node)
        return (
            all(abs(v) < tolerance for v in cmds.getAttr(f"{node}.translate")[0])
            and all(abs(v) < tolerance for v in cmds.getAttr(f"{node}.rotate")[0])
            and all(abs(v - 1.0) < tolerance for v in cmds.getAttr(f"{node}.scale")[0])
        )

    @classmethod
    def _get_stored_transforms(cls, node, prefix):
        """Body of :meth:`XformUtils.get_stored_transforms`."""
        resolved = (cmds.ls(str(node), type="transform", long=True) or [None])[0]
        if not resolved:
            return None

        t_attr, r_attr, s_attr = cls._bake_attr_names(prefix)
        if not any(
            cmds.attributeQuery(attr, node=resolved, exists=True)
            for attr in (t_attr, r_attr, s_attr)
        ):
            return None

        t_vec = cls._read_bake_t(resolved, t_attr)
        r_quat = cls._read_bake_r(resolved, r_attr)
        s_vec = cls._read_bake_s(resolved, s_attr)
        return {
            "translate": [t_vec.x, t_vec.y, t_vec.z],
            "rotate": r_quat,
            "scale": list(s_vec),
            "matrix": cls._compose_local(t_vec, r_quat, s_vec),
        }
