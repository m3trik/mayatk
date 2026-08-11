# !/usr/bin/python
# coding=utf-8
from __future__ import annotations

import contextlib
import math
from typing import List, Tuple, Dict, Set, Optional

try:
    import maya.cmds as cmds
    import maya.mel as mel
    from maya.api import OpenMaya as om  # For MPoint, MVector, etc.
except Exception as error:
    cmds = None
    mel = None
    om = None
    print(__file__, error)

import pythontk as ptk

# From this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.components import Components
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.node_utils.attributes._attributes import Attributes
from mayatk.xform_utils.matrices import Matrices


# ---------------------------------------------------------------------------
# Per-channel bake helpers used by store_transforms / restore_transforms.
#
# The freeze/unfreeze contract is *cumulative*: each freeze composes the
# current local TRS onto a per-channel bake history; each unfreeze pushes
# that bake history (composed with whatever the user did since) back into
# the local channels.  Tracking T/R/S separately keeps composition clean
# regardless of which channels the user freezes (you can freeze T, then R,
# and unfreeze them independently without rotation entangling the
# translation).
# ---------------------------------------------------------------------------

_IDENTITY_ROT_FLAT = [
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
]


class _XformUtilsInternal:
    """Internal helper methods for XformUtils.

    This class encapsulates implementation details that should not be part of
    the public API. XformUtils inherits from this class to access these helpers.
    """

    @staticmethod
    def _apply_freeze_deltas(obj, axes_to_freeze, normal=0):
        """Apply freeze transformations using Maya's native makeIdentity.

        Maya's makeIdentity automatically preserves world-space pivot positions
        by adjusting rotatePivotTranslate/scalePivotTranslate as needed.

        Parameters:
            obj: The transform node to freeze.
            axes_to_freeze (set): Set of axes to freeze (e.g., {'tx', 'ty', 'tz', 'rx', ...}).
            normal (int/bool): ``makeIdentity -normal`` — 0 leave normals
                alone, 1 freeze them, 2 freeze only when the transform
                mirrors.  Matters for negatively-scaled geometry, whose
                normals invert when the scale is baked out.

        Returns:
            bool: True if successful, False if skipped due to error.
        """
        freeze_t = not axes_to_freeze.isdisjoint({"tx", "ty", "tz"})
        freeze_r = not axes_to_freeze.isdisjoint({"rx", "ry", "rz"})
        freeze_s = not axes_to_freeze.isdisjoint({"sx", "sy", "sz"})
        normal = int(normal)

        # Note: We let RuntimeError bubble up so freeze_transforms can handle
        # connection/locking strategies.
        try:
            cmds.makeIdentity(
                obj,
                apply=True,
                t=freeze_t,
                r=freeze_r,
                s=freeze_s,
                pn=True,
                normal=normal,
            )
        except RuntimeError:
            cmds.makeIdentity(
                obj,
                apply=True,
                t=freeze_t,
                r=freeze_r,
                s=freeze_s,
                pn=False,
                normal=normal,
            )
        return True

    #: Channels a freeze rewrites — copied wholesale from the stand-in so the
    #: master ends up byte-identical to a real ``makeIdentity`` (pivots and
    #: rotateAxis included, not just TRS).
    _FREEZE_CHANNEL_ATTRS = (
        "translate",
        "rotate",
        "scale",
        "shear",
        "rotatePivot",
        "rotatePivotTranslate",
        "scalePivot",
        "scalePivotTranslate",
        "rotateAxis",
    )

    @staticmethod
    def _authoring_shapes(transform: str) -> List[str]:
        """Shapes under *transform* whose points a bake should be written to.

        Construction history (``polyCube1`` → ``inMesh``) is fine: point
        writes land on the shape's tweak and survive re-evaluation. A
        **deformer** is not — a shape downstream of a ``geometryFilter`` is
        evaluated output, so the deformer's orig (intermediate) shape is the
        one carrying the authored points and the visible shape is skipped
        to avoid baking the same delta twice.

        Empty shapes (no vertices) are skipped: there is nothing to
        transform and ``MFnMesh`` rejects them outright.
        """
        meshes = [
            s
            for s in cmds.listRelatives(transform, shapes=True, fullPath=True) or []
            if cmds.nodeType(s) == "mesh"
        ]

        def _has_vertices(shape):
            try:
                return bool(cmds.polyEvaluate(shape, vertex=True))
            except Exception:
                return False

        def _deformed(shape):
            return any(
                "geometryfilter" in NodeUtils.get_inherited_types(n)
                for n in cmds.listHistory(shape, pruneDagObjects=True) or []
            )

        visible = [s for s in meshes if not cmds.getAttr(f"{s}.intermediateObject")]
        # Only when a visible shape is deformer output does its orig shape
        # carry the authored points. Otherwise every intermediate present is
        # dead data (an orphaned history remnant — common in imported/scanned
        # assets) and baking it would be pointless work on a shape whose
        # member set may not even match the visible one.
        take_intermediates = any(_deformed(s) for s in visible)

        out = []
        for s in meshes:
            is_inter = bool(cmds.getAttr(f"{s}.intermediateObject"))
            if is_inter and not take_intermediates:
                continue
            if not is_inter and _deformed(s):
                continue  # evaluated output; its orig shape is baked instead
            if not _has_vertices(s):
                continue
            out.append(s)
        return out

    @classmethod
    def _instance_group_members(cls, transform: str):
        """``(members, shapes)`` for the instance group *transform* belongs
        to, resolved from the shapes a bake would actually write to.

        Single source of truth for "who is in this group": deriving it from
        :meth:`_authoring_shapes` rather than from every shared shape keeps
        the set that gets compensated identical to the set the caller
        considers handled — an orphaned intermediate can be shared with
        transforms that the visible shape is not, and treating those as
        group members silently drops them from the operation.

        Returns ``(None, None)`` when the shapes disagree about membership.
        """
        shapes = cls._authoring_shapes(transform)
        if not shapes:
            return None, None
        member_sets = {
            tuple(sorted(cmds.listRelatives(s, allParents=True, fullPath=True) or []))
            for s in shapes
        }
        if len(member_sets) != 1:
            return None, None
        return list(next(iter(member_sets))), shapes

    @staticmethod
    def _is_multi_path(transform: str) -> bool:
        """True when *transform* itself is instanced (several DAG paths)."""
        try:
            sel = om.MSelectionList()
            sel.add(transform)
            return sel.getDagPath(0).isInstanced()
        except Exception:
            return False

    @staticmethod
    def _transform_is_driven(
        transform: str, channels=("translate", "rotate", "scale", "shear")
    ) -> bool:
        """True when anything feeds *transform*'s *channels*.

        Compact yes/no twin of ``transform_diag._driving_connections``,
        which returns per-driver tags for its diagnosis dict; it can't be
        reused here because that module imports this one.

        *channels* defaults to TRS + shear (what a freeze has to bake).
        Narrow it to what a given caller actually writes — reporting a
        driven shear to a caller that only touches T/R/S is a false
        positive that costs a node its restore.
        """
        plugs = []
        for ch in channels:
            if cmds.attributeQuery(ch, node=transform, exists=True):
                plugs.append(f"{transform}.{ch}")
                plugs.extend(
                    f"{transform}.{c}"
                    for c in cmds.attributeQuery(ch, node=transform, listChildren=True)
                    or []
                )
        return bool(
            plugs and cmds.listConnections(plugs, source=True, destination=False)
        )

    @staticmethod
    def _set_matrix_plug(plug: str, mmatrix) -> None:
        """Write an ``om.MMatrix`` (or 16-flat iterable) to a matrix attribute plug."""
        if hasattr(mmatrix, "getElement"):
            flat = [mmatrix.getElement(r, c) for r in range(4) for c in range(4)]
        else:
            flat = list(mmatrix)
        cmds.setAttr(plug, *flat, type="matrix")

    @staticmethod
    def _mmatrix_to_flat(m) -> List[float]:
        if hasattr(m, "getElement"):
            return [m.getElement(r, c) for r in range(4) for c in range(4)]
        return list(m)

    @staticmethod
    def _partial_world_matrix(current, stored, channels):
        """Compose a world matrix by picking T/R/S components per *channels*.

        Components named in *channels* (a subset of ``{"translate", "rotate",
        "scale"}``) are sourced from *stored*; the rest come from *current*.
        Used by :func:`XformUtils.restore_transforms` for partial unfreeze.

        Decomposition is via ``MTransformationMatrix``; quaternions are used
        for rotation to avoid Euler-order ambiguity on round-trip.

        Shear is preserved from the *current* matrix in all cases.  Shear is
        not exposed as a freezable channel in the menu, and a fresh
        ``MTransformationMatrix`` defaults its shear to zero — without this
        explicit copy, ``cmds.xform(matrix=...)`` would silently zero
        ``obj.shear`` on any partial restore.
        """
        if om is None:
            return current
        current_tm = om.MTransformationMatrix(current)
        stored_tm = om.MTransformationMatrix(stored)
        target_tm = om.MTransformationMatrix()

        src_t = stored_tm if "translate" in channels else current_tm
        target_tm.setTranslation(src_t.translation(om.MSpace.kWorld), om.MSpace.kWorld)

        src_r = stored_tm if "rotate" in channels else current_tm
        target_tm.setRotation(src_r.rotation(asQuaternion=True))

        src_s = stored_tm if "scale" in channels else current_tm
        target_tm.setScale(src_s.scale(om.MSpace.kWorld), om.MSpace.kWorld)

        # Shear is not a freezable channel — preserve whatever the object
        # currently has (a fresh TM defaults shear to zero, which would
        # silently destroy user-set shear on partial restore).
        target_tm.setShear(current_tm.shear(om.MSpace.kWorld), om.MSpace.kWorld)

        return target_tm.asMatrix()

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

    @staticmethod
    def _read_bake_t(node, t_attr):
        """Read the stored translation bake as an ``MVector``; identity if missing/unset."""
        if not cmds.attributeQuery(t_attr, node=node, exists=True):
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
        if not cmds.attributeQuery(r_attr, node=node, exists=True):
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
        if not cmds.attributeQuery(s_attr, node=node, exists=True):
            return [1.0, 1.0, 1.0]
        raw = cmds.getAttr(f"{node}.{s_attr}")
        if raw and isinstance(raw[0], (list, tuple)):
            raw = raw[0]
        if raw is None or any(v is None for v in raw):
            return [1.0, 1.0, 1.0]
        return [raw[0], raw[1], raw[2]]

    @staticmethod
    def _write_bake_t(node, t_attr, t_vec):
        if not cmds.attributeQuery(t_attr, node=node, exists=True):
            cmds.addAttr(node, ln=t_attr, dt="double3", keyable=False)
        plug = f"{node}.{t_attr}"
        cmds.setAttr(plug, t_vec[0], t_vec[1], t_vec[2], type="double3")
        if cmds.getAttr(plug, keyable=True) or cmds.getAttr(plug, channelBox=True):
            cmds.setAttr(plug, keyable=False, channelBox=False)

    @staticmethod
    def _write_bake_r(node, r_attr, r_quat):
        if not cmds.attributeQuery(r_attr, node=node, exists=True):
            cmds.addAttr(node, ln=r_attr, at="matrix", keyable=False)
        plug = f"{node}.{r_attr}"
        flat = _XformUtilsInternal._mmatrix_to_flat(r_quat.asMatrix())
        cmds.setAttr(plug, *flat, type="matrix")
        if cmds.getAttr(plug, keyable=True) or cmds.getAttr(plug, channelBox=True):
            cmds.setAttr(plug, keyable=False, channelBox=False)

    @staticmethod
    def _write_bake_s(node, s_attr, s_vec):
        if not cmds.attributeQuery(s_attr, node=node, exists=True):
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
        attr = _XformUtilsInternal._opm_marker_name(prefix)
        if not cmds.attributeQuery(attr, node=node, exists=True):
            cmds.addAttr(node, ln=attr, at="bool", keyable=False)
        cmds.setAttr(f"{node}.{attr}", True)

    @staticmethod
    def _has_opm_bake(node, prefix="original"):
        attr = _XformUtilsInternal._opm_marker_name(prefix)
        return bool(
            cmds.attributeQuery(attr, node=node, exists=True)
            and cmds.getAttr(f"{node}.{attr}")
        )

    @classmethod
    def _accumulate_bake(cls, node, local, channels, accumulate=True, prefix="original"):
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
            old_t = cls._read_bake_t(node, t_attr) if accumulate else om.MVector(0, 0, 0)
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
        fn = _XformUtilsInternal._shape_fn(shape)
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
        cmds calls regardless of point count.
        """
        fn = _XformUtilsInternal._shape_fn(shape)
        if fn is None:
            return
        for i in range(len(points)):
            points[i] = points[i] * transform_matrix
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
            cmds.listRelatives(transform, shapes=True, noIntermediate=True, fullPath=True)
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
                new_local = (
                    w_cur * inv_new_anchor * current_worlds[anchor] * inv_parent
                )
                boundary_writes.append(
                    (xf, cls._mmatrix_to_flat(new_local))
                )
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
    def _transfer_pivot_channels(
        cls,
        source,
        targets,
        translate,
        rotate,
        scale,
        bake,
        world_space,
        mirror,
        mirror_index,
        mirror_matrix,
    ):
        """``transfer_pivot``'s per-target channel work, minus the bake/select
        bookkeeping — split out so the caller can scope the geometry-writing
        world-space rotate pass."""
        for target in targets:
            if translate:
                rp = cmds.xform(source, q=True, ws=world_space, rp=True)
                if mirror:
                    rp[mirror_index] = -rp[mirror_index]
                cmds.xform(target, ws=world_space, rp=rp)
                if scale:
                    sp = cmds.xform(source, q=True, ws=world_space, sp=True)
                    if mirror:
                        sp[mirror_index] = -sp[mirror_index]
                    cmds.xform(target, ws=world_space, sp=sp)
            elif scale:
                sp = cmds.xform(source, q=True, ws=world_space, sp=True)
                if mirror:
                    sp[mirror_index] = -sp[mirror_index]
                cmds.xform(target, ws=world_space, sp=sp)

            if rotate:
                if world_space:
                    children = (
                        cmds.listRelatives(
                            target, children=True, type="transform", fullPath=True
                        )
                        or []
                    )
                    if children:
                        # Re-read the paths cmds.parent hands back: the ones
                        # captured above named the children UNDER `target`, so
                        # they dangle the moment the children move to world and
                        # the restoring parent below silently no-ops on them.
                        children = cmds.ls(
                            cmds.parent(children, world=True) or [], long=True
                        )

                    shapes = (
                        cmds.listRelatives(
                            target, shapes=True, noIntermediate=True, fullPath=True
                        )
                        or []
                    )
                    shape_points = {}
                    for sh in shapes:
                        try:
                            stype = cmds.nodeType(sh)
                            if stype == "mesh":
                                num = cmds.polyEvaluate(sh, vertex=True) or 0
                                pts = []
                                for i in range(num):
                                    pts.append(
                                        cmds.pointPosition(f"{sh}.vtx[{i}]", world=True)
                                    )
                                shape_points[sh] = pts
                        except Exception:
                            pass

                    if mirror:
                        # Set the target's world orientation to the mirrored source pivot frame.
                        # Conjugating the rotation by the reflection (S * R * S) keeps a valid
                        # right-handed rotation while reflecting it across the axis-plane.
                        src_rot = om.MTransformationMatrix(
                            om.MMatrix(cmds.xform(source, q=True, ws=True, matrix=True))
                        ).asRotateMatrix()
                        mir_euler = om.MTransformationMatrix(
                            mirror_matrix * src_rot * mirror_matrix
                        ).rotation()
                        cmds.xform(
                            target,
                            ws=True,
                            ro=[
                                math.degrees(mir_euler.x),
                                math.degrees(mir_euler.y),
                                math.degrees(mir_euler.z),
                            ],
                        )
                    else:
                        try:
                            cmds.matchTransform(
                                target,
                                source,
                                rot=True,
                                pos=False,
                                piv=False,
                                scl=False,
                            )
                        except Exception as e:
                            cmds.warning(f"matchTransform failed in transfer_pivot: {e}")

                    if not bake:
                        m = om.MMatrix(cmds.xform(target, q=True, matrix=True, os=True))
                        m_inv = m.inverse()
                        tm = om.MTransformationMatrix(m_inv)
                        euler = tm.rotation()
                        euler_deg = [
                            math.degrees(euler.x),
                            math.degrees(euler.y),
                            math.degrees(euler.z),
                        ]

                        cmds.xform(target, ro=(0, 0, 0))
                        cmds.xform(target, ra=euler_deg)

                    if children:
                        try:
                            cmds.parent(children, target)
                        except Exception as e:
                            # Never silent: failing here leaves the children
                            # parked at world level, which is scene damage the
                            # user would otherwise only find later.
                            cmds.warning(
                                f"transfer_pivot: could not restore "
                                f"{len(children)} child(ren) under {target}: {e}"
                            )
                    for sh, pts in shape_points.items():
                        try:
                            for i, p in enumerate(pts):
                                cmds.xform(f"{sh}.vtx[{i}]", ws=True, t=p)
                        except Exception:
                            pass

                else:
                    source_ra = cmds.xform(source, q=True, ra=True)
                    if mirror:
                        # Mirror the pivot orientation (rotateAxis) across the axis-plane:
                        # the rotation about the mirror axis is preserved, the other two negate.
                        source_ra = [
                            source_ra[i] if i == mirror_index else -source_ra[i]
                            for i in range(3)
                        ]
                    cmds.xform(target, ra=source_ra)

    @staticmethod
    def _bake_pivot(objects, position=False, orientation=False):
        """``bake_pivot``'s body — a port of Maya's ``bakeCustomToolPivot.mel``.

        Split out so the public entry point can scope it; it assumes its
        inputs are already resolved transforms and does no instance guarding
        of its own.
        """
        ctx = cmds.currentCtx()
        pivotModeActive = 0
        customModeActive = 0
        if ctx in ("RotateSuperContext", "manipRotateContext"):
            customOri = cmds.manipRotateContext("Rotate", q=True, orientAxes=True)
            pivotModeActive = cmds.manipRotateContext(
                "Rotate", q=True, editPivotMode=True
            )
            customModeActive = cmds.manipRotateContext("Rotate", q=True, mode=True) == 3
        elif ctx in ("scaleSuperContext", "manipScaleContext"):
            customOri = cmds.manipScaleContext("Scale", q=True, orientAxes=True)
            pivotModeActive = cmds.manipScaleContext(
                "Scale", q=True, editPivotMode=True
            )
            customModeActive = cmds.manipScaleContext("Scale", q=True, mode=True) == 6
        else:
            customOri = cmds.manipMoveContext("Move", q=True, orientAxes=True)
            pivotModeActive = cmds.manipMoveContext("Move", q=True, editPivotMode=True)
            customModeActive = cmds.manipMoveContext("Move", q=True, mode=True) == 6

        if orientation and customModeActive:
            if not position:
                mel.eval(
                    'error (uiRes("m_bakeCustomToolPivot.kWrongAxisOriToolError"))'
                )
                return

            from math import degrees

            cX, cY, cZ = customOri = [
                degrees(customOri[0]),
                degrees(customOri[1]),
                degrees(customOri[2]),
            ]

            cmds.rotate(
                cX, cY, cZ, objects, a=True, pcp=True, pgp=True, ws=True, fo=True
            )

        if position:
            for obj in objects:
                m = cmds.xform(obj, q=True, m=True)
                p = cmds.xform(obj, q=True, os=True, sp=True)
                oldX, oldY, oldZ = [
                    (p[0] * m[0] + p[1] * m[4] + p[2] * m[8] + m[12]),
                    (p[0] * m[1] + p[1] * m[5] + p[2] * m[9] + m[13]),
                    (p[0] * m[2] + p[1] * m[6] + p[2] * m[10] + m[14]),
                ]

                cmds.xform(obj, zeroTransformPivots=True)

                newX, newY, newZ = cmds.getAttr(f"{obj}.translate")[0]
                cmds.move(
                    oldX - newX,
                    oldY - newY,
                    oldZ - newZ,
                    obj,
                    pcp=True,
                    pgp=True,
                    ls=True,
                    r=True,
                )

        if pivotModeActive:
            cmds.ctxEditMode()

        if orientation and customModeActive:
            if ctx in ("RotateSuperContext", "manipRotateContext"):
                cmds.manipPivot(rotateToolOri=0)
            elif ctx in ("scaleSuperContext", "manipScaleContext"):
                cmds.manipPivot(scaleToolOri=0)
            else:
                cmds.manipPivot(moveToolOri=0)
                if ctx not in ("moveSuperContext", "manipMoveContext"):
                    cmds.manipPivot(ro=True)

    @staticmethod
    def _resolve_transforms(objects) -> List[str]:
        """Resolve *objects* to their owning transform nodes (de-duped long paths).

        Components and shapes collapse to their parent transform; non-DAG nodes
        (materials, construction history, object sets) are dropped so ``xform``
        never sees a node it would reject with "No valid objects supplied". Unlike
        ``NodeUtils.get_transform_node`` this does NOT walk connections — a selected
        material won't drag in every mesh that uses it — which is the behaviour the
        pivot ops require.
        """
        objects = CoreUtils.as_strings(objects)
        if not objects:  # an empty list would turn the filtered ``ls`` scene-wide
            return []
        resolved = cmds.ls(objects, objectsOnly=True, long=True) or []
        transforms = cmds.ls(resolved, transforms=True, long=True) or []
        shapes = cmds.ls(resolved, shapes=True, long=True) or []
        # fullPath, not path: ``path=True`` yields the *shortest unique* name, so a
        # selection holding both an object and one of its components produced "|pc"
        # and "pc" — two entries the de-dupe can't merge, and two spellings callers
        # can't match against each other.
        transforms += (
            cmds.listRelatives(shapes, fullPath=True, parent=True, type="transform")
            or []
        )
        return list(dict.fromkeys(transforms))  # de-dupe, preserve order

    # Component selection masks that carry a real world position. One masked call
    # classifies a whole selection, versus walking ``Components.component_mapping``
    # a type at a time. Deliberately NOT the full mapping: the parametric types
    # (curve/surface parameter points, knots, ranges, trim edges, isoparms — 39-45)
    # report positions ``exactWorldBoundingBox`` can't measure, and a selected
    # rotate/scale pivot handle (49/50) is a manipulator, not geometry. Both would
    # otherwise be "centered on" as if they were. Passing this filter is necessary
    # but not sufficient — see ``_component_center``.
    _COMPONENT_MASKS = (
        28, 30, 31, 32, 34, 35, 36, 37, 38, 46, 47, 70, 72, 73,
    )  # fmt: skip

    @staticmethod
    def _component_center(components) -> Optional[List[float]]:
        """World-space center of *components*, or None if they have no measurable extent.

        ``exactWorldBoundingBox`` reports "nothing to measure" as an *inverted*
        sentinel box (min +1e20 / max -1e20) rather than raising, and averaging
        that yields exactly (0, 0, 0) — so an unguarded caller silently pivots on
        the world origin. NURBS surface faces hit this despite being a
        legitimately-masked component type, which is why the mask filter alone
        can't be trusted. ``XformUtils.get_bounding_box`` is not used here for the
        same reason: its "center" carries no such guard.
        """
        components = list(components)
        if not components:  # an empty list would make the query selection-wide
            return None
        bbox = cmds.exactWorldBoundingBox(components)
        if any(bbox[i] > bbox[i + 3] for i in range(3)):
            return None
        return [(bbox[i] + bbox[i + 3]) / 2 for i in range(3)]

    @classmethod
    def _group_components_by_transform(cls, objects) -> Dict[str, List[str]]:
        """Map each owning transform (long path) to the components of *objects* it owns.

        Components stay unexpanded (``pCube1.f[0:5]`` is not flattened into six
        names) — the callers only feed them to ``exactWorldBoundingBox``, which
        handles ranges, so a dense selection costs one entry rather than
        thousands. An empty dict means *objects* held no components at all,
        which is the signal to fall back to whole-object behaviour.
        """
        objects = CoreUtils.as_strings(objects)
        if not objects:  # an empty list would make the filtered call selection-wide
            return {}
        components = (
            cmds.filterExpand(objects, sm=cls._COMPONENT_MASKS, expand=False) or []
        )
        by_node: Dict[str, List[str]] = {}
        for comp in components:  # node names can't contain "."
            by_node.setdefault(comp.split(".", 1)[0], []).append(comp)

        grouped: Dict[str, List[str]] = {}
        for node, comps in by_node.items():  # resolve once per owner, not per component
            transform = (cls._resolve_transforms([node]) or [None])[0]
            if transform is None:
                continue
            grouped.setdefault(transform, []).extend(comps)
        return grouped


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
        index_to_axis = {0: "x", 1: "-x", 2: "y", 3: "-y", 4: "z", 5: "-z"}
        axis_to_index = {v: k for k, v in index_to_axis.items()}

        def get_inverted_axis(axis):
            return axis[1:] if axis.startswith("-") else "-" + axis

        orthogonal_axis_map = {
            "x": "y",
            "-x": "y",
            "y": "z",
            "-y": "z",
            "z": "x",
            "-z": "x",
        }

        if isinstance(value, int):
            if value not in index_to_axis:
                raise ValueError(f"Invalid axis value: {value!r}")
            axis = index_to_axis[value]
        elif isinstance(value, str):
            if value not in axis_to_index:
                raise ValueError(f"Invalid axis value: {value!r}")
            axis = value
        else:
            raise TypeError(
                "Input must be an integer or a string representing an axis."
            )

        if invert:
            axis = get_inverted_axis(axis)

        if ortho:
            axis = orthogonal_axis_map[axis]

        if to_integer:
            return axis_to_index[axis]
        return axis

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
        source = cmds.ls(CoreUtils.as_strings(source), flatten=True, long=True) or []
        target = cmds.ls(CoreUtils.as_strings(target), flatten=True, long=True) or []
        if not source or not target:
            return

        target_pos = cls._resolve_target_position(target, pivot)

        if group_move:
            group_center = cls.get_bounding_box(source, "center")
            translation_vector = [t - g for t, g in zip(target_pos, group_center)]

            for src in source:
                current_pos = cmds.xform(
                    src, query=True, translation=True, worldSpace=True
                )
                new_pos = [c + t for c, t in zip(current_pos, translation_vector)]
                cmds.xform(src, translation=new_pos, worldSpace=True)
        else:
            for src in source:
                cmds.xform(src, translation=target_pos, worldSpace=True)

    @classmethod
    def _resolve_target_position(cls, targets, pivot):
        """Resolve the world-space alignment point for `move_to`.

        Parameters:
            targets (list): Resolved (flattened), non-empty target node(s).
            pivot (str/list): A pivot option (see `get_pivot_options()`) or an explicit
                (x, y, z) world position.

        Returns:
            list: The [x, y, z] world-space position to align the source to.
        """
        # Explicit coordinate triple passes straight through.
        if isinstance(pivot, (tuple, list)) and len(pivot) == 3:
            return [float(p) for p in pivot]

        if pivot == "world":
            return [0.0, 0.0, 0.0]

        # Per-node pivots (manip/object/baked) don't aggregate across a set; resolve
        # them against the last target as the representative node.
        if pivot in ("manip", "object", "baked"):
            return list(cls.get_operation_axis_pos(targets[-1], pivot))

        # Bounding-box pivots collapse the full target set into one combined box,
        # preserving the legacy 'center' behavior for multi-object targets.
        bbox_pivots = {"center", "xmin", "xmax", "ymin", "ymax", "zmin", "zmax"}
        if pivot in bbox_pivots:
            if pivot == "center":
                return list(cls.get_bounding_box(targets, "center"))
            # One bbox eval for both the center and the requested extent.
            center, extent = cls.get_bounding_box(targets, f"center|{pivot}")
            center = list(center)
            center[{"x": 0, "y": 1, "z": 2}[pivot[0]]] = float(extent)
            return center

        cmds.warning(
            f"[move_to] Unknown pivot '{pivot}'; using target bounding box center."
        )
        return list(cls.get_bounding_box(targets, "center"))

    @classmethod
    @CoreUtils.undoable
    def drop_to_grid(
        cls, objects, align="Mid", origin=False, center_pivot=False, freeze_transforms=False
    ):
        """Align objects to Y origin on the grid using a helper plane.

        Parameters:
            objects (str/obj/list): The objects to translate.
            align (bool): Specify which point of the object's bounding box to align with the grid. (valid: 'Max','Mid'(default),'Min')
            origin (bool): Move to world grid's center.
            center_pivot (bool): Center the object's pivot.
            freeze_transforms (bool): Reset the selected transform and all of its children down to the shape level.
        """
        targets = cmds.ls(CoreUtils.as_strings(objects), transforms=True, long=True) or []
        for obj in targets:
            osPivot = cmds.xform(obj, q=True, rotatePivot=True, objectSpace=True)
            wsPivot = cmds.xform(obj, q=True, rotatePivot=True, worldSpace=True)

            cmds.xform(obj, centerPivots=True)
            plane = cmds.polyPlane(name="temp#")[0]

            if not origin:
                cmds.xform(
                    plane,
                    translation=(wsPivot[0], 0, wsPivot[2]),
                    absolute=True,
                    ws=True,
                )

            cmds.align(obj, plane, atl=True, x="Mid", y=align, z="Mid")
            cmds.delete(plane)

            if not center_pivot:
                cmds.xform(obj, rotatePivot=osPivot, objectSpace=True)

        if freeze_transforms and targets:
            # Through the engine, not raw makeIdentity: the drop is a real
            # user-facing bake, so it has to leave the history Un-Freeze reads
            # (store=True by default). One batched call after the loop — the
            # engine reports a per-call summary, and a drop of fifty objects
            # should not print fifty lines.
            cls.freeze_transforms(targets, force=True)

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
        to_scale = cmds.ls(CoreUtils.as_strings(a), flatten=True, long=True) or []

        bx, by, bz = cls.get_bounding_box(b, "size", world_space=True)

        result = []
        for obj in to_scale:
            ax, ay, az = cls.get_bounding_box(obj, "size", world_space=True)

            try:
                diffx, diffy, diffz = [bx / ax, by / ay, bz / az]
            except ZeroDivisionError:
                diffx, diffy, diffz = [1, 1, 1]

            scaleNew = [diffx, diffy, diffz]

            if average:
                scaleNew = [sum(scaleNew) / len(scaleNew)] * 3

            if scale:
                cmds.xform(obj, s=scaleNew, worldSpace=True, relative=True)

            [result.append(i) for i in scaleNew]

        return result

    @staticmethod
    @CoreUtils.selected
    @CoreUtils.undoable
    def scale_connected_edges(objects, scale_factor=1.1) -> None:
        """Scales each set of connected edges separately, either uniformly or non-uniformly.

        Parameters:
            objects (list): A list of selected edge components to be scaled.
            scale_factor (float, int, tuple, list): The factor by which to scale the edges.
        """
        if not objects:
            cmds.warning("No edges selected.")
            return

        connected_edges_sets = Components.get_contiguous_edges(objects)

        for edge_set in connected_edges_sets:
            vertices = cmds.polyListComponentConversion(
                edge_set, fromEdge=True, toVertex=True
            )
            vertices = cmds.ls(vertices, flatten=True) or []

            # Calculate the center point of the vertices
            positions = [cmds.pointPosition(v, world=True) for v in vertices]
            if not positions:
                continue
            center_point = om.MVector(
                sum(p[0] for p in positions) / len(positions),
                sum(p[1] for p in positions) / len(positions),
                sum(p[2] for p in positions) / len(positions),
            )

            if isinstance(scale_factor, (tuple, list)):
                scale_x, scale_y, scale_z = scale_factor
            else:
                scale_x = scale_y = scale_z = scale_factor

            for vertex, pos_arr in zip(vertices, positions):
                pos = om.MVector(*pos_arr)
                direction = pos - center_point
                new_pos = om.MVector(
                    center_point.x + direction.x * scale_x,
                    center_point.y + direction.y * scale_y,
                    center_point.z + direction.z * scale_z,
                )
                cmds.xform(vertex, ws=True, t=[new_pos.x, new_pos.y, new_pos.z])

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
            _XformUtilsInternal._accumulate_bake(
                obj,
                _XformUtilsInternal._decompose_local(obj),
                target_channels,
                accumulate=accumulate,
                prefix=prefix,
            )

    @classmethod
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
        master = (cmds.ls(master, long=True) or [master])[0]
        members, shapes = cls._instance_group_members(master)
        if members is None:
            if not quiet:
                cmds.warning(
                    f"freeze_instanced_group: '{master}' shares shapes with "
                    "differing member sets — skipped."
                )
            return False
        siblings = [m for m in members if m != master]

        # Checked across EVERY member, not just the siblings: a driven
        # sibling would have its compensation overwritten on the next
        # evaluation (displaced against baked geometry), and a driven master
        # rejects the frozen channels outright ("child attribute … is locked
        # or connected"). Which member the caller happened to pass must not
        # change the answer. A member on several DAG paths cannot carry a
        # per-path compensation at all.
        for m in members:
            if cls._transform_is_driven(m):
                if not quiet:
                    cmds.warning(
                        f"freeze_instanced_group: '{CoreUtils.short_name(m)}' has "
                        "driven transform channels — group skipped."
                    )
                return False
            if m != master and cls._is_multi_path(m):
                if not quiet:
                    cmds.warning(
                        f"freeze_instanced_group: '{CoreUtils.short_name(m)}' is "
                        "itself instanced (multiple DAG paths) — group skipped."
                    )
                return False

        for node in members:
            try:
                if cmds.referenceQuery(node, isNodeReferenced=True):
                    if not quiet:
                        cmds.warning(
                            f"freeze_instanced_group: '{node}' is referenced — skipped."
                        )
                    return False
            except RuntimeError:
                pass

        pre_local = om.MMatrix(cmds.xform(master, q=True, os=True, matrix=True))
        sib_local = {
            m: om.MMatrix(cmds.xform(m, q=True, os=True, matrix=True)) for m in siblings
        }
        sib_pivots = {
            m: (
                cmds.xform(m, q=True, ws=True, rotatePivot=True),
                cmds.xform(m, q=True, ws=True, scalePivot=True),
            )
            for m in siblings
        }

        standin = cmds.duplicate(master, parentOnly=True)[0]
        try:
            with Attributes.temporarily_unlock([standin]):
                cmds.makeIdentity(
                    standin,
                    apply=True,
                    t=translate,
                    r=rotate,
                    s=scale,
                    n=False,
                    pn=True,
                )
            post_local = om.MMatrix(cmds.xform(standin, q=True, os=True, matrix=True))
            B = pre_local * post_local.inverse()
            if B.isEquivalent(om.MMatrix(), 1e-12):
                return False

            # A mirroring bake (negative determinant) reverses the handedness
            # of the point set, so the existing face winding now faces inward
            # — the whole group renders inside-out.  ``makeIdentity`` fixes
            # this for itself on the normal path; baking the points by hand
            # here does not, so the winding has to be reversed to match.
            mirrored = B.det3x3() < 0

            for shape in shapes:
                sel = om.MSelectionList()
                sel.add(shape)
                fn = om.MFnMesh(sel.getDagPath(0))
                fn.setPoints(
                    om.MPointArray(
                        [p * B for p in fn.getPoints(om.MSpace.kObject)]
                    ),
                    om.MSpace.kObject,
                )
                if mirrored:
                    # Edit the SHARED shape once — every member sees it, which
                    # is exactly what the whole in-place design relies on.
                    cmds.polyNormal(
                        shape, normalMode=0, userNormalMode=0, constructionHistory=False
                    )

            with Attributes.temporarily_unlock([master]):
                for attr in cls._FREEZE_CHANNEL_ATTRS:
                    cmds.setAttr(
                        f"{master}.{attr}",
                        *cmds.getAttr(f"{standin}.{attr}")[0],
                        type="double3",
                    )
        finally:
            if cmds.objExists(standin):
                cmds.delete(standin)

        b_inv = B.inverse()
        for m in siblings:
            comp = b_inv * sib_local[m]
            with Attributes.temporarily_unlock([m]):
                cmds.xform(
                    m,
                    os=True,
                    matrix=[
                        comp.getElement(r, c) for r in range(4) for c in range(4)
                    ],
                )
                rp, sp = sib_pivots[m]
                cmds.xform(m, ws=True, rotatePivot=rp)
                cmds.xform(m, ws=True, scalePivot=sp)
        return True

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
        if center_pivot is True:
            center_pivot = 2
        elif center_pivot is False:
            center_pivot = 0

        axes_to_freeze = set()

        channel_map = {
            "translate": ["tx", "ty", "tz"],
            "t": ["tx", "ty", "tz"],
            "translateX": ["tx"],
            "tx": ["tx"],
            "translateY": ["ty"],
            "ty": ["ty"],
            "translateZ": ["tz"],
            "tz": ["tz"],
            "rotate": ["rx", "ry", "rz"],
            "r": ["rx", "ry", "rz"],
            "rotateX": ["rx"],
            "rx": ["rx"],
            "rotateY": ["ry"],
            "ry": ["ry"],
            "rotateZ": ["rz"],
            "rz": ["rz"],
            "scale": ["sx", "sy", "sz"],
            "s": ["sx", "sy", "sz"],
            "scaleX": ["sx"],
            "sx": ["sx"],
            "scaleY": ["sy"],
            "sy": ["sy"],
            "scaleZ": ["sz"],
            "sz": ["sz"],
        }

        if from_channel_box:
            selected_channels = set(Attributes.get_selected_channels() or [])
            for ch in selected_channels:
                if "." in ch:
                    ch = ch.split(".")[-1]
                if ch in channel_map:
                    axes_to_freeze.update(channel_map[ch])
        else:
            # Detect whether the caller specified any per-channel flag.
            channel_keys = {
                "translate",
                "t",
                "rotate",
                "r",
                "scale",
                "s",
                "translateX",
                "tx",
                "translateY",
                "ty",
                "translateZ",
                "tz",
                "rotateX",
                "rx",
                "rotateY",
                "ry",
                "rotateZ",
                "rz",
                "scaleX",
                "sx",
                "scaleY",
                "sy",
                "scaleZ",
                "sz",
            }
            # ``normal`` is deliberately NOT in this set. It is a makeIdentity
            # MODIFIER (freeze vertex normals), not a channel selector —
            # counting it here made ``freeze_transforms(obj, normal=True)``
            # suppress the freeze-everything default while contributing no
            # axes of its own, so the call silently froze nothing at all.
            any_channel_flag = any(k in kwargs for k in channel_keys)

            if not any_channel_flag:
                # No explicit channels → freeze all (matches Maya's default
                # ``makeIdentity -apply true`` behaviour).
                axes_to_freeze.update(
                    ["tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"]
                )
            else:
                if kwargs.get("translate") or kwargs.get("t"):
                    axes_to_freeze.update(["tx", "ty", "tz"])
                if kwargs.get("rotate") or kwargs.get("r"):
                    axes_to_freeze.update(["rx", "ry", "rz"])
                if kwargs.get("scale") or kwargs.get("s"):
                    axes_to_freeze.update(["sx", "sy", "sz"])
                # Per-axis flags (rare).
                for ch in (
                    "tx",
                    "ty",
                    "tz",
                    "rx",
                    "ry",
                    "rz",
                    "sx",
                    "sy",
                    "sz",
                ):
                    if kwargs.get(ch):
                        axes_to_freeze.add(ch)

        if not axes_to_freeze:
            return

        objects = (
            cmds.ls(CoreUtils.as_strings(objects), type="transform", long=True) or []
        )

        strategy = (connection_strategy or "preserve").lower()
        valid_strategies = {"preserve", "disconnect", "delete"}
        if strategy not in valid_strategies:
            raise ValueError(
                f"Invalid connection_strategy '{connection_strategy}'. "
                f"Valid options: {sorted(valid_strategies)}"
            )

        inst_strategy = (instance_strategy or "skip").lower()
        valid_inst_strategies = {"skip", "preserve", "uninstance"}
        if inst_strategy not in valid_inst_strategies:
            raise ValueError(
                f"Invalid instance_strategy '{instance_strategy}'. "
                f"Valid options: {sorted(valid_inst_strategies)}"
            )

        # ``makeIdentity -normal``: 0 leave alone, 1 freeze, 2 freeze only on
        # a mirroring transform. A modifier, not a channel — see the note on
        # ``channel_keys`` above.
        freeze_normals = int(kwargs.get("normal") or 0)

        freeze_channels: Set[str] = set()
        if not axes_to_freeze.isdisjoint({"tx", "ty", "tz"}):
            freeze_channels.add("translate")
        if not axes_to_freeze.isdisjoint({"rx", "ry", "rz"}):
            freeze_channels.add("rotate")
        if not axes_to_freeze.isdisjoint({"sx", "sy", "sz"}):
            freeze_channels.add("scale")

        # Snapshot the pre-freeze locals of every transform this call could
        # touch — the whole subtree, because ``makeIdentity`` on a group zeroes
        # every descendant's channels.  Read here, before the instance
        # strategies fork off; COMMITTED as bake history after the freeze, and
        # only for transforms that actually froze.  Stamping up front would
        # give every skipped object (instanced, connection-blocked) a bake
        # matching its untouched channels, so a later unfreeze would add a
        # transform that was never baked out and double it.
        pre_freeze: Dict[str, tuple] = {}
        if store:
            for obj in objects:
                for node in [obj] + (
                    cmds.listRelatives(obj, ad=True, type="transform", fullPath=True)
                    or []
                ):
                    if node not in pre_freeze:
                        pre_freeze[node] = cls._decompose_local(node)

        # Long paths of what actually froze. ``flattened`` freezes ran through
        # makeIdentity, which flattens the subtree, so their descendants are
        # stamped too; ``baked_in_place`` came from freeze_instanced_group,
        # which rewrites shape points and channels without touching the
        # subtree (and whose compensated siblings must NOT be stamped — their
        # channels were rewritten, not zeroed).
        flattened: List[str] = []
        baked_in_place: List[str] = []

        def _channels_zeroed(node) -> bool:
            """True when *node*'s freeze channels now sit at identity —
            proof ``makeIdentity`` actually flattened it.

            ``makeIdentity`` on a group is not all-or-nothing: a descendant
            whose shape is multiply-instanced is skipped with a *warning*
            (``Cannot freeze below transform X …``) while the rest of the
            subtree flattens (measured on a production module scene).
            Stamping such a skipped leaf would recreate exactly the stale
            bake this commit-after-freeze design exists to prevent.
            """
            if "translate" in freeze_channels and any(
                abs(v) > 1e-5 for v in cmds.getAttr(f"{node}.translate")[0]
            ):
                return False
            if "rotate" in freeze_channels and any(
                abs(v) > 1e-5 for v in cmds.getAttr(f"{node}.rotate")[0]
            ):
                return False
            if "scale" in freeze_channels and any(
                abs(v - 1.0) > 1e-5 for v in cmds.getAttr(f"{node}.scale")[0]
            ):
                return False
            return True

        def commit_bakes():
            if not store or not pre_freeze:
                return
            exact = set(flattened) | set(baked_in_place)
            flat_roots = set(flattened)
            for node, local in pre_freeze.items():
                # Ancestor lookup walks the path (O(depth)) rather than
                # testing every frozen root (O(roots)) — freeze_children over
                # a deep hierarchy puts every node in both collections.
                if node in exact:
                    cls._accumulate_bake(node, local, freeze_channels)
                elif (
                    cls._nearest_known_ancestor(node, flat_roots) is not None
                    and cmds.objExists(node)
                    and _channels_zeroed(node)
                ):
                    # Descendant of a flattened root: stamp only on PROOF the
                    # flatten reached it — see _channels_zeroed.
                    cls._accumulate_bake(node, local, freeze_channels)

        if inst_strategy != "skip" and objects:
            instanced = [o for o in objects if NodeUtils.get_instanced_shapes(o)]
            if instanced:
                if inst_strategy == "uninstance":
                    NodeUtils.uninstance(instanced, delete_history=delete_history)
                else:
                    # Preserve: bake the SHARED geometry in place and
                    # compensate the siblings — no fork, no DAG surgery, so
                    # per-instance shading survives (see
                    # freeze_instanced_group). One member per group does the
                    # work; the rest are dropped from this call's object list
                    # so they aren't frozen a second time.
                    handled: Set[str] = set()
                    frozen_groups = 0
                    for obj in instanced:
                        if obj in handled:
                            continue
                        # Claim exactly the members the bake compensates —
                        # NOT every transform sharing any shape. An orphaned
                        # intermediate can be shared more widely than the
                        # visible shape, and claiming those would drop them
                        # from this freeze without ever touching them.
                        members, _ = cls._instance_group_members(obj)
                        if cls.freeze_instanced_group(
                            obj,
                            translate=not axes_to_freeze.isdisjoint({"tx", "ty", "tz"}),
                            rotate=not axes_to_freeze.isdisjoint({"rx", "ry", "rz"}),
                            scale=not axes_to_freeze.isdisjoint({"sx", "sy", "sz"}),
                            quiet=False,
                        ):
                            # Claim the members only once the bake actually
                            # compensated them; a skipped group falls through
                            # to the normal loop, which reports it as an
                            # instanced skip instead of dropping it silently.
                            handled.update(members or [obj])
                            # Only the operated member ends at identity; the
                            # siblings absorbed a compensating matrix, so a
                            # bake of their pre-freeze local would be wrong.
                            baked_in_place.append(obj)
                            frozen_groups += 1
                    objects = [o for o in objects if o not in handled]
                    if frozen_groups:
                        print(
                            "XformUtils.freeze_transforms: "
                            f"{frozen_groups} instance group(s) frozen in place."
                        )
                    if not objects:
                        commit_bakes()
                        return

        if freeze_children:
            objects_set = set(objects)
            for obj in list(objects):
                descendants = (
                    cmds.listRelatives(obj, ad=True, type="transform", fullPath=True)
                    or []
                )
                for child in descendants:
                    if child not in objects_set:
                        objects.append(child)
                        objects_set.add(child)

        skipped_connections: List[Tuple[str, Dict[str, List[str]]]] = []
        instanced_skips: List[str] = []
        frozen_objects: List[str] = []

        def get_blockers(node: str) -> Dict[str, List[str]]:
            """Helper to find input connections on specified channels.

            Queries both the compound plug and its children — a compound
            ``listConnections`` does NOT see child-plug connections
            (``d.rotateZ -> c.rotateZ`` is invisible to a ``c.rotate`` query)
            and vice versa. Anim curves and constraints connect per-axis, so
            without the child plugs the disconnect/delete strategies found no
            blockers and silently skipped the node.

            Returns ``{dest_plug: [src_plug, ...]}``.
            """
            plugs = []
            for ch in freeze_channels:
                if cmds.attributeQuery(ch, node=node, exists=True):
                    plugs.append(f"{node}.{ch}")
                    children = (
                        cmds.attributeQuery(ch, node=node, listChildren=True)
                        or []
                    )
                    plugs.extend(f"{node}.{child}" for child in children)
            if not plugs:
                return {}

            # cmds.listConnections with connections=True returns a flat list:
            # [dest, src, dest, src, ...] when plugs=True.
            connections = (
                cmds.listConnections(
                    plugs,
                    source=True,
                    destination=False,
                    plugs=True,
                    connections=True,
                )
                or []
            )

            found_blockers: Dict[str, List[str]] = {}
            it = iter(connections)
            for dest, src in zip(it, it):
                found_blockers.setdefault(dest, []).append(src)
            return found_blockers

        for obj in objects:
            if not cmds.objExists(obj):
                continue

            if center_pivot == 2:
                cmds.xform(obj, centerPivots=True)
            elif center_pivot == 1:
                shapes = cmds.listRelatives(
                    obj, shapes=True, noIntermediate=True, type="mesh"
                )
                if shapes:
                    cmds.xform(obj, centerPivots=True)

            # Baking into a shared shape would rewrite every sibling instance's
            # geometry, so an instanced object is never frozen in place. Callers
            # that want it baked go through instance_strategy (or
            # NodeUtils.uninstance(freeze=True)), which forks first and then
            # calls back into here. Shared INTERMEDIATE shapes count — Maya's
            # makeIdentity refuses while any child shape is multiply-instanced,
            # so the test and the fork must span the same set.
            try:
                instanced = bool(NodeUtils.get_instanced_shapes(obj))
            except Exception:
                instanced = False

            if instanced:
                instanced_skips.append(CoreUtils.short_name(obj))
                continue

            nodes_to_unlock = []
            if force:
                nodes_to_unlock.append(obj)
                if unlock_children:
                    descendants = (
                        cmds.listRelatives(
                            obj, ad=True, type="transform", fullPath=True
                        )
                        or []
                    )
                    nodes_to_unlock.extend(descendants)

            with Attributes.temporarily_unlock(nodes_to_unlock):
                try:
                    if delete_history:
                        cmds.delete(obj, constructionHistory=True)

                    if cls._apply_freeze_deltas(obj, axes_to_freeze, normal=freeze_normals):
                        frozen_objects.append(CoreUtils.short_name(obj))
                        flattened.append(obj)

                except RuntimeError as exc:
                    msg = str(exc).lower()
                    if "incoming connection" in msg or "locked" in msg:
                        blockers = get_blockers(obj)

                        if not blockers and "locked" not in msg:
                            skipped_connections.append((CoreUtils.short_name(obj), {}))
                            cmds.warning(
                                f"XformUtils.freeze_transforms: Skipping '{obj}' due to connection error: {exc}"
                            )
                            continue

                        if strategy == "preserve":
                            skipped_connections.append(
                                (CoreUtils.short_name(obj), blockers)
                            )
                            continue

                        nodes_to_delete: Set[str] = set()
                        for plug, sources in blockers.items():
                            for src in sources:
                                try:
                                    cmds.disconnectAttr(src, plug)
                                except Exception as disconnect_exc:
                                    raise RuntimeError(
                                        f"Failed to disconnect {src} -> {plug}: {disconnect_exc}"
                                    ) from disconnect_exc

                                if strategy == "delete":
                                    src_node = src.split(".")[0]
                                    if not src_node or src_node == obj:
                                        continue
                                    try:
                                        if cmds.referenceQuery(
                                            src_node, isNodeReferenced=True
                                        ):
                                            continue
                                    except Exception:
                                        pass
                                    nodes_to_delete.add(src_node)

                        if nodes_to_delete:
                            cmds.delete(list(nodes_to_delete))

                        try:
                            if cls._apply_freeze_deltas(obj, axes_to_freeze, normal=freeze_normals):
                                frozen_objects.append(CoreUtils.short_name(obj))
                                flattened.append(obj)
                        except RuntimeError as retry_exc:
                            skipped_connections.append(
                                (CoreUtils.short_name(obj), blockers)
                            )
                            cmds.warning(
                                f"XformUtils.freeze_transforms: Skipping '{obj}' after clearing connections: {retry_exc}"
                            )

                    else:
                        raise

        commit_bakes()

        total_processed = (
            len(frozen_objects) + len(skipped_connections) + len(instanced_skips)
        )
        if total_processed:
            skipped_total = len(skipped_connections) + len(instanced_skips)
            print(
                "XformUtils.freeze_transforms: "
                f"{len(frozen_objects)} frozen, {skipped_total} skipped."
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
        transforms = (
            cmds.ls(CoreUtils.as_strings(objects), type="transform", flatten=True) or []
        )
        if not transforms:
            return

        identity_matrix = om.MMatrix()

        for obj in transforms:
            if not cmds.objExists(obj):
                continue

            if store:
                # An OPM bake and a geometry bake have different inverses, so
                # they must never share one history: composing them would send
                # the whole thing down the OPM path, putting the channels back
                # while the geometry stayed baked (the object doubles). A node
                # already carrying a non-OPM bake keeps it and this freeze goes
                # untracked — the honest option, since the alternative silently
                # corrupts a restore.
                if XformUtils.get_stored_transforms(
                    obj
                ) is not None and not _XformUtilsInternal._has_opm_bake(obj):
                    cmds.warning(
                        f"freeze_to_opm: '{CoreUtils.short_name(obj)}' already "
                        "carries a geometry bake — the OPM freeze is not being "
                        "recorded (the two have different inverses). Un-freeze "
                        "first if you want it tracked."
                    )
                else:
                    # Before any mutation: store_transforms reads the CURRENT local.
                    XformUtils.store_transforms(obj)
                    _XformUtilsInternal._mark_opm_bake(obj)

            with Attributes.temporarily_unlock([obj]):
                rotate_pivot_ws = cmds.xform(obj, q=True, ws=True, rp=True)
                scale_pivot_ws = cmds.xform(obj, q=True, ws=True, sp=True)

                rotate_pivot_translate = (
                    cmds.getAttr(f"{obj}.rotatePivotTranslate")[0]
                    if cmds.attributeQuery(
                        "rotatePivotTranslate", node=obj, exists=True
                    )
                    else None
                )
                scale_pivot_translate = (
                    cmds.getAttr(f"{obj}.scalePivotTranslate")[0]
                    if cmds.attributeQuery("scalePivotTranslate", node=obj, exists=True)
                    else None
                )

                original_local = om.MMatrix(
                    cmds.xform(obj, q=True, matrix=True, objectSpace=True)
                )

                temp = cmds.duplicate(obj, parentOnly=True)[0]
                try:
                    _XformUtilsInternal._set_matrix_plug(
                        f"{temp}.offsetParentMatrix", identity_matrix
                    )
                    cmds.setAttr(f"{temp}.translate", 0.0, 0.0, 0.0, type="double3")
                    cmds.setAttr(f"{temp}.rotate", 0.0, 0.0, 0.0, type="double3")
                    cmds.setAttr(f"{temp}.scale", 1.0, 1.0, 1.0, type="double3")
                    if cmds.attributeQuery("shear", node=temp, exists=True):
                        cmds.setAttr(f"{temp}.shear", 0.0, 0.0, 0.0, type="double3")

                    rest_matrix = om.MMatrix(
                        cmds.xform(temp, q=True, matrix=True, objectSpace=True)
                    )
                finally:
                    cmds.delete(temp)

                try:
                    compensation = rest_matrix.inverse()
                except RuntimeError:
                    cmds.warning(
                        f"XformUtils.freeze_to_opm: Skipping '{obj}' due to singular pivot matrix."
                    )
                    continue

                opm_matrix = compensation * original_local
                _XformUtilsInternal._set_matrix_plug(
                    f"{obj}.offsetParentMatrix", opm_matrix
                )

                cmds.setAttr(f"{obj}.translate", 0.0, 0.0, 0.0, type="double3")
                cmds.setAttr(f"{obj}.rotate", 0.0, 0.0, 0.0, type="double3")
                cmds.setAttr(f"{obj}.scale", 1.0, 1.0, 1.0, type="double3")
                if cmds.attributeQuery("shear", node=obj, exists=True):
                    cmds.setAttr(f"{obj}.shear", 0.0, 0.0, 0.0, type="double3")

                cmds.xform(obj, ws=True, rp=rotate_pivot_ws, preserve=True)
                cmds.xform(obj, ws=True, sp=scale_pivot_ws, preserve=True)

                if rotate_pivot_translate is not None:
                    cmds.setAttr(
                        f"{obj}.rotatePivotTranslate",
                        *rotate_pivot_translate,
                        type="double3",
                    )
                if scale_pivot_translate is not None:
                    cmds.setAttr(
                        f"{obj}.scalePivotTranslate",
                        *scale_pivot_translate,
                        type="double3",
                    )

                if reset_rotate_axis and cmds.attributeQuery(
                    "rotateAxis", node=obj, exists=True
                ):
                    cmds.setAttr(f"{obj}.rotateAxis", 0.0, 0.0, 0.0, type="double3")

                if reset_joint_orient and cmds.attributeQuery(
                    "jointOrient", node=obj, exists=True
                ):
                    cmds.setAttr(f"{obj}.jointOrient", 0.0, 0.0, 0.0, type="double3")

    @classmethod
    @CoreUtils.undoable
    def unfreeze_from_opm(cls, objects, prefix="original", delete_attrs=True) -> List[str]:
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
        restored: List[str] = []
        t_attr, r_attr, s_attr = _XformUtilsInternal._bake_attr_names(prefix)
        marker = _XformUtilsInternal._opm_marker_name(prefix)

        for obj in cmds.ls(CoreUtils.as_strings(objects), type="transform", long=True) or []:
            stored = cls.get_stored_transforms(obj, prefix=prefix)
            if stored is None:
                continue
            if cmds.referenceQuery(obj, isNodeReferenced=True):
                cmds.warning(f"unfreeze_from_opm: skipping referenced node {obj}.")
                continue

            with Attributes.temporarily_unlock([obj]):
                # The world matrix is identical either side of an OPM freeze,
                # so re-pinning the world pivots after the channels go back
                # recomputes exactly the local pivot values the freeze
                # displaced — the mirror of what freeze_to_opm did on the way
                # in. (_apply_clean_local is the wrong tool here: it ZEROES
                # the pivots, which is right after a geometry bake and wrong
                # after a freeze that deliberately preserved them.)
                rotate_pivot_ws = cmds.xform(obj, q=True, ws=True, rp=True)
                scale_pivot_ws = cmds.xform(obj, q=True, ws=True, sp=True)

                _XformUtilsInternal._set_matrix_plug(
                    f"{obj}.offsetParentMatrix", om.MMatrix()
                )

                t_vec = stored["translate"]
                s_vec = stored["scale"]
                cmds.setAttr(f"{obj}.translate", *t_vec, type="double3")
                cmds.setAttr(f"{obj}.scale", *s_vec, type="double3")
                rot_order = cmds.getAttr(f"{obj}.rotateOrder") or 0
                euler = stored["rotate"].asEulerRotation()
                euler.reorderIt(rot_order)
                cmds.setAttr(
                    f"{obj}.rotate",
                    math.degrees(euler.x),
                    math.degrees(euler.y),
                    math.degrees(euler.z),
                    type="double3",
                )

                cmds.xform(obj, ws=True, rp=rotate_pivot_ws, preserve=True)
                cmds.xform(obj, ws=True, sp=scale_pivot_ws, preserve=True)

            if delete_attrs:
                for attr in (t_attr, r_attr, s_attr, marker):
                    if cmds.attributeQuery(attr, node=obj, exists=True):
                        cmds.deleteAttr(f"{obj}.{attr}")
            restored.append(obj)
        return restored

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
        if om is None or cmds is None:
            return []

        nodes = (
            cmds.ls(CoreUtils.as_strings(objects), type="transform", long=True) or []
        )
        identity_matrix = om.MMatrix()
        modified_parents: List[str] = []
        root_set = set(nodes) if (traverse and preserve_root) else set()

        pairs: List[Tuple[str, str]] = []  # (parent, child)
        seen_children: Set[str] = set()

        for node in nodes:
            if not cmds.objExists(node):
                continue

            if traverse:
                locator_shapes = (
                    cmds.listRelatives(
                        node, allDescendents=True, type="locator", fullPath=True
                    )
                    or []
                )
                for shape in locator_shapes:
                    loc_xform_list = (
                        cmds.listRelatives(shape, parent=True, fullPath=True) or []
                    )
                    if not loc_xform_list:
                        continue
                    child = loc_xform_list[0]
                    if child in seen_children:
                        continue
                    parent_list = (
                        cmds.listRelatives(child, parent=True, fullPath=True) or []
                    )
                    if not parent_list:
                        continue
                    parent = parent_list[0]
                    if parent in root_set:
                        continue
                    pairs.append((parent, child))
                    seen_children.add(child)
            else:
                child = node
                if child in seen_children:
                    continue
                parent_list = (
                    cmds.listRelatives(child, parent=True, fullPath=True) or []
                )
                if not parent_list:
                    cmds.warning(
                        f"XformUtils.unfreeze_to_parent: '{CoreUtils.short_name(child)}' "
                        "has no parent. Skipping."
                    )
                    continue
                pairs.append((parent_list[0], child))
                seen_children.add(child)

        for parent, child in pairs:
            child_local = om.MMatrix(
                cmds.xform(child, q=True, matrix=True, objectSpace=True)
            )
            parent_local = om.MMatrix(
                cmds.xform(parent, q=True, matrix=True, objectSpace=True)
            )

            # Maya row-vector convention: descendant.world = ... * child_local *
            # parent_local * grandparent_world. Absorbing child_local into
            # parent_local gives parent_new = child_local * parent_local.
            parent_new = child_local * parent_local

            with Attributes.temporarily_unlock([parent, child]):
                XformUtils.set_object_matrix(parent, parent_new, world=False)
                XformUtils.set_object_matrix(child, identity_matrix, world=False)

            modified_parents.append(CoreUtils.short_name(parent))

        if modified_parents:
            print(
                "XformUtils.unfreeze_to_parent: "
                f"{len(modified_parents)} parent(s) updated."
            )

        return modified_parents

    @classmethod
    @CoreUtils.undoable
    def restore_transforms(
        cls, objects, prefix="original", delete_attrs=True, channels=None, traverse=False
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
        valid_channels = {"translate", "rotate", "scale"}
        if channels is None:
            target_channels = valid_channels
        else:
            target_channels = set(channels) & valid_channels
            if not target_channels:
                return []
        t_attr, r_attr, s_attr = _XformUtilsInternal._bake_attr_names(prefix)
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
        opm_targets = [
            t for t in targets if _XformUtilsInternal._has_opm_bake(t, prefix)
        ]
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
            if xf not in target_set and _XformUtilsInternal._owns_instanced_shape(xf):
                boundaries.add(xf)

        # Ancestor-lookup universe (restored nodes + boundaries), maintained
        # incrementally — a per-target union of the two key sets would be
        # quadratic over large restores.
        known: Set[str] = set(boundaries)

        def _drop(obj):
            """A target that won't be restored still needs boundary status if
            it owns an instanced shape — the pre-scan skipped all targets."""
            if _XformUtilsInternal._owns_instanced_shape(obj):
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
            if _XformUtilsInternal._transform_is_driven(
                obj, channels=("translate", "rotate", "scale")
            ):
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
            cur_t, cur_r, cur_s = _XformUtilsInternal._decompose_local(obj)

            # Compose stored bake history with the current local TRS per
            # channel.  Channels not in target_channels stay at current.
            if "translate" in target_channels and has_t:
                stored_t = _XformUtilsInternal._read_bake_t(obj, t_attr)
                target_t = stored_t + cur_t
            else:
                target_t = cur_t

            if "rotate" in target_channels and has_r:
                stored_r = _XformUtilsInternal._read_bake_r(obj, r_attr)
                target_r = stored_r * cur_r
            else:
                target_r = cur_r

            if "scale" in target_channels and has_s:
                stored_s = _XformUtilsInternal._read_bake_s(obj, s_attr)
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
            if not trivial and _XformUtilsInternal._owns_instanced_shape(obj):
                cmds.warning(
                    f"restore_transforms: '{obj}' owns an instanced (shared) shape — "
                    "restoring its channels would displace the other instances. "
                    "Skipped; bake history retained. (Uninstance first, or restore "
                    "the whole group's layout by other means.)"
                )
                boundaries.add(obj)
                known.add(obj)
                continue
            if not trivial and _XformUtilsInternal._is_multi_path(obj):
                cmds.warning(
                    f"restore_transforms: '{obj}' is instanced (several DAG paths) — "
                    "one set of channels cannot restore every path. Skipped."
                )
                _drop(obj)
                continue

            # The new clean local matrix is just T * R * S with zero
            # pivots and zero pivot translates — that's the state the
            # user expects after unfreeze.
            new_local = _XformUtilsInternal._compose_local(target_t, target_r, target_s)

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
            parent_world = om.MMatrix() if inv_local is None else inv_local * world_current
            ancestor = _XformUtilsInternal._nearest_known_ancestor(obj, known)
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
        point_writes, boundary_writes = _XformUtilsInternal._plan_restore_geometry(
            [p[0] for p in plans], current_worlds, final_worlds, boundaries
        )

        # Phase 2 — apply.  Compensation first, as one block: the writes are
        # absolute (object-space points / local matrices) against matrices
        # already resolved in phase 1, so they are order-independent, whereas
        # the channel loop below must stay strictly top-down for its
        # world-space pivot re-anchor (which reads the live parent chain).
        for shape, pts, inverse_new_world in point_writes:
            _XformUtilsInternal._set_shape_points_object(shape, pts, inverse_new_world)

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
            _XformUtilsInternal._apply_clean_local(obj, target_t, target_r, target_s)

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
        cleared: List[str] = []
        # The OPM marker is part of the same stamp — leaving it behind would
        # make a later restore route a node with no history down the OPM path.
        attr_names = _XformUtilsInternal._bake_attr_names(prefix) + (
            _XformUtilsInternal._opm_marker_name(prefix),
        )
        for obj in cmds.ls(CoreUtils.as_strings(objects), type="transform", long=True) or []:
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
        t_attr, r_attr, s_attr = _XformUtilsInternal._bake_attr_names(prefix)
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
                stored_s = _XformUtilsInternal._read_bake_s(obj, s_attr)
                degenerate = any(
                    (not math.isfinite(v)) or abs(v) < 1e-6 for v in stored_s
                )
            if not degenerate and cmds.attributeQuery(t_attr, node=obj, exists=True):
                stored_t = _XformUtilsInternal._read_bake_t(obj, t_attr)
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

    @staticmethod
    def has_stored_transforms(objects, prefix="original"):
        """Check if objects have any stored bake history.

        Returns:
            dict: Mapping of object short names to bool (True if any
            T/R/S bake attribute exists).
        """
        result = {}
        attr_names = _XformUtilsInternal._bake_attr_names(prefix)
        for obj in cmds.ls(CoreUtils.as_strings(objects), type="transform", long=True) or []:
            has_stored = any(
                cmds.attributeQuery(attr, node=obj, exists=True) for attr in attr_names
            )
            result[obj] = has_stored
        return result

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
        node = str(node)
        return (
            all(abs(v) < tolerance for v in cmds.getAttr(f"{node}.translate")[0])
            and all(abs(v) < tolerance for v in cmds.getAttr(f"{node}.rotate")[0])
            and all(abs(v - 1.0) < tolerance for v in cmds.getAttr(f"{node}.scale")[0])
        )

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
        resolved = (cmds.ls(str(node), type="transform", long=True) or [None])[0]
        if not resolved:
            return None

        t_attr, r_attr, s_attr = _XformUtilsInternal._bake_attr_names(prefix)
        if not any(
            cmds.attributeQuery(attr, node=resolved, exists=True)
            for attr in (t_attr, r_attr, s_attr)
        ):
            return None

        t_vec = _XformUtilsInternal._read_bake_t(resolved, t_attr)
        r_quat = _XformUtilsInternal._read_bake_r(resolved, r_attr)
        s_vec = _XformUtilsInternal._read_bake_s(resolved, s_attr)
        return {
            "translate": [t_vec.x, t_vec.y, t_vec.z],
            "rotate": r_quat,
            "scale": list(s_vec),
            "matrix": _XformUtilsInternal._compose_local(t_vec, r_quat, s_vec),
        }

    @classmethod
    @CoreUtils.undoable
    def reset_translation(cls, objects):
        """Reset the translation transformations on the given object(s)."""
        for obj in cmds.ls(CoreUtils.as_strings(objects), long=True) or []:
            pos = cmds.objectCenter(obj)
            cls.drop_to_grid(obj, origin=True, center_pivot=True)
            # Engine path (store=True): the translate bake is reversible.
            cls.freeze_transforms(obj, translate=True, force=True)
            cmds.xform(obj, translation=pos)

    @classmethod
    def set_translation_to_pivot(cls, node):
        """Set an object's translation value from its pivot location."""
        node = str(node)
        x, y, z = cmds.xform(node, query=True, worldSpace=True, rotatePivot=True)
        cmds.xform(node, relative=True, translation=[-x, -y, -z])
        cls.freeze_transforms(node, translate=True, force=True)
        cmds.xform(node, translation=[x, y, z])

    @staticmethod
    def get_manip_pivot_matrix(obj, **kwargs):
        """Return the object's transform matrix using xform, allowing kwargs override.

        Returns:
            om.MMatrix: The resulting transformation matrix.
        """
        matrix = cmds.xform(obj, q=True, matrix=True, **kwargs)
        return om.MMatrix(matrix)

    @staticmethod
    def set_manip_pivot_matrix(obj, matrix, **kwargs) -> None:
        """Apply a transformation matrix's position and orientation to the manip pivot."""
        if not hasattr(matrix, "getElement"):
            matrix = om.MMatrix(list(matrix))
        tm = om.MTransformationMatrix(matrix)
        pos_v = tm.translation(om.MSpace.kWorld)
        pos = (pos_v.x, pos_v.y, pos_v.z)
        euler = tm.rotation()
        rot = [math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z)]

        cmds.select(obj, replace=True)
        cmds.manipPivot(p=pos, o=rot, **kwargs)

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
        if objects is None:
            objects = cmds.ls(selection=True, type="transform") or []
        targets = (
            cmds.ls(CoreUtils.as_strings(objects), type="transform", long=True) or []
        )
        stamped = [
            t for t in targets if cls.get_stored_transforms(t, prefix=prefix) is not None
        ]
        if not stamped:
            cmds.warning(
                "restore_original_axes: no stored bake history on the given "
                "object(s) — nothing to restore the axes from."
            )
            return None

        node = stamped[-1]
        selection = cmds.ls(selection=True, long=True) or []
        try:
            cls.set_manip_pivot_matrix(node, cls.get_operation_axis_matrix(node, "original"))
        finally:
            # set_manip_pivot_matrix re-selects to address the manipulator —
            # put the caller's selection back exactly, empty included.
            if selection:
                cmds.select(selection, replace=True)
            else:
                cmds.select(clear=True)
        return node

    @classmethod
    def get_pivot_options(cls):
        """Returns a list of supported pivot options."""
        return [
            "object",
            "world",
            "center",
            "manip",
            "xmin",
            "xmax",
            "ymin",
            "ymax",
            "zmin",
            "zmax",
            "baked",
        ]

    _manip_cache = {}

    @staticmethod
    def _manip_cache_key(node):
        """Resolve a node to its long DAG path for stable manip-cache keying.

        Leaf names collide across objects; keying the cache by the long path
        prevents a cached pivot from leaking onto the wrong object.
        """
        resolved = cmds.ls(str(node), long=True)
        return resolved[0] if resolved else str(node)

    @classmethod
    def clear_manip_cache(cls):
        """Clears the cached manipulator pivot data."""
        cls._manip_cache.clear()

    @classmethod
    def snapshot_manip_pivot(cls, node):
        """Snapshot the current manipulator pivot state for the given node into the cache."""
        try:
            current_selection = cmds.ls(selection=True) or []
            if node not in current_selection:
                return

            manip_pivot_pos = cmds.manipPivot(q=True, p=True)[0]
            manip_pivot_rot = cmds.manipPivot(q=True, o=True)[0]

            if (
                isinstance(manip_pivot_rot, (list, tuple))
                and len(manip_pivot_rot) == 1
                and isinstance(manip_pivot_rot[0], (list, tuple))
            ):
                manip_pivot_rot = manip_pivot_rot[0]

            rp_pos = cmds.xform(node, q=True, ws=True, rp=True)

            def is_diff(v1, v2):
                if not v1 or not v2:
                    return False
                if isinstance(v1[0], (list, tuple)):
                    v1 = v1[0]
                return sum([abs(a - b) for a, b in zip(v1, v2)]) > 0.0001

            cache_key = cls._manip_cache_key(node)
            if is_diff(manip_pivot_pos, rp_pos):
                cls._manip_cache[cache_key] = (manip_pivot_rot, manip_pivot_pos)
            else:
                if cache_key in cls._manip_cache:
                    del cls._manip_cache[cache_key]

        except Exception:
            pass

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
        pos = cls.get_operation_axis_pos(node, pivot)
        mat_pos_list = [
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            pos[0],
            pos[1],
            pos[2],
            1.0,
        ]
        mat_pos = om.MMatrix(mat_pos_list)

        mat_rot = om.MMatrix.kIdentity

        if pivot in ("object", "original"):
            m_obj_arr = cmds.xform(node, query=True, worldSpace=True, matrix=True)
            m_obj = om.MMatrix(m_obj_arr)
            tm_obj = om.MTransformationMatrix(m_obj)
            mat_rot = tm_obj.rotation().asMatrix()

            if pivot == "original":
                stored = cls.get_stored_transforms(node)
                if stored is not None:
                    # Row-vector convention: world = local * parentWorld. After a
                    # freeze the local rotation is identity, so the live world
                    # rotation IS the parent's — pre-multiplying the stored local
                    # rotation rebuilds the authored world frame. If the node was
                    # rotated again since, the authored axes ride along with it,
                    # which is what an axis-based op wants.
                    mat_rot = stored["rotate"].asMatrix() * mat_rot

        elif pivot == "manip":
            current_selection = cmds.ls(selection=True) or []
            needs_selection_change = node not in current_selection

            if needs_selection_change:
                cmds.select(node, replace=True)
            try:
                manip_rot_queries = cmds.manipPivot(query=True, o=True)
                manip_rot_deg = manip_rot_queries[0]
                if (
                    isinstance(manip_rot_deg, (list, tuple))
                    and len(manip_rot_deg) == 1
                    and isinstance(manip_rot_deg[0], (list, tuple))
                ):
                    manip_rot_deg = manip_rot_deg[0]

                rp_pos = cmds.xform(node, q=True, ws=True, rp=True)
                manip_pos = cmds.manipPivot(q=True, p=True)[0]

                def is_diff(v1, v2):
                    return sum([abs(a - b) for a, b in zip(v1, v2)]) > 0.0001

                cache_key = cls._manip_cache_key(node)
                if is_diff(manip_pos, rp_pos):
                    cls._manip_cache[cache_key] = (manip_rot_deg, manip_pos)
                elif cache_key in cls._manip_cache:
                    cached_vals = cls._manip_cache[cache_key]
                    if cached_vals and len(cached_vals) == 2:
                        manip_rot_deg = cached_vals[0]

                euler = om.MEulerRotation(
                    math.radians(manip_rot_deg[0]),
                    math.radians(manip_rot_deg[1]),
                    math.radians(manip_rot_deg[2]),
                    om.MEulerRotation.kXYZ,
                )
                mat_rot = euler.asMatrix()
            except Exception:
                pass
            finally:
                if needs_selection_change and current_selection:
                    cmds.select(current_selection, replace=True)

        return mat_rot * mat_pos

    @classmethod
    def get_operation_axis_pos(cls, node, pivot, axis_index=None):
        """Determines the pivot position for mirroring/cutting along a specified axis or all axes."""
        node = str(node)
        if axis_index is None:
            return [
                cls.get_operation_axis_pos(node, pivot, 0),
                cls.get_operation_axis_pos(node, pivot, 1),
                cls.get_operation_axis_pos(node, pivot, 2),
            ]

        if isinstance(pivot, (tuple, list)) and len(pivot) == 3:
            return float(pivot[axis_index])

        if pivot == "manip":
            current_selection = cmds.ls(selection=True) or []
            needs_selection_change = node not in current_selection

            if needs_selection_change:
                cmds.select(node, replace=True)

            rp_pos = list(cmds.xform(node, q=True, ws=True, rp=True))
            manip_pivot_ws = list(rp_pos)
            try:
                manip_pivot_result = cmds.manipPivot(q=True, p=True)

                # Unwrap nested return shape: cmds.manipPivot may return either
                # [(x, y, z)] or [x, y, z] depending on context.
                queried_pos = None
                if manip_pivot_result:
                    head = manip_pivot_result[0]
                    if isinstance(head, (list, tuple)) and len(head) == 3:
                        queried_pos = list(head)
                    elif (
                        isinstance(manip_pivot_result, (list, tuple))
                        and len(manip_pivot_result) == 3
                    ):
                        queried_pos = list(manip_pivot_result)

                # cmds.manipPivot returns (0, 0, 0) when no Move/Rotate/Scale
                # context is active, regardless of what's selected. In that
                # case the manipulator hasn't been customized — fall back to
                # the object's rotate pivot, which is where Maya places the
                # gizmo by default when a transform tool is activated.
                is_default_origin = queried_pos is not None and all(
                    abs(v) < 1e-6 for v in queried_pos
                )

                if queried_pos is not None and not is_default_origin:
                    manip_pivot_ws = queried_pos
                elif (cache_key := cls._manip_cache_key(node)) in cls._manip_cache:
                    # Manip is at default state but we previously cached a
                    # custom position for this node — restore it.
                    _cached_rot, cached_pos = cls._manip_cache[cache_key]
                    if cached_pos is not None:
                        manip_pivot_ws = list(cached_pos)
                # else: manip_pivot_ws stays at rp_pos (the natural default).

            except Exception as e:
                print(
                    f"DEBUG: Exception in get_operation_axis_pos: {e}, Node: {node}, Pivot: {pivot}"
                )
                import traceback

                traceback.print_exc()
                manip_pivot_ws = list(rp_pos)

            finally:
                if needs_selection_change and current_selection:
                    cmds.select(current_selection, replace=True)

            return (
                float(manip_pivot_ws[axis_index])
                if axis_index is not None
                else manip_pivot_ws
            )

        # "original" shares the object pivot POSITION — a freeze moves the local
        # axes, not the world pivot. Only the orientation differs, and that is
        # resolved in get_operation_axis_matrix.
        if pivot in ("object", "original"):
            obj_pivot_ws = cmds.xform(node, q=True, ws=True, rp=True)
            return (
                float(obj_pivot_ws[axis_index])
                if axis_index is not None
                else obj_pivot_ws
            )

        if pivot == "baked":
            local_rp = cmds.xform(node, q=True, rp=True, os=True)
            world_matrix = XformUtils.get_object_matrix(node, world=True)
            world_rp = om.MPoint(local_rp[0], local_rp[1], local_rp[2]) * world_matrix
            return (
                float(world_rp[axis_index])
                if axis_index is not None
                else [world_rp[0], world_rp[1], world_rp[2]]
            )

        if pivot == "world":
            return 0.0 if axis_index is not None else [0.0, 0.0, 0.0]

        if pivot == "center":
            center = cls.get_bounding_box(node, "center")
            return float(center[axis_index]) if axis_index is not None else list(center)

        limit_pivots = {"xmin", "xmax", "ymin", "ymax", "zmin", "zmax"}
        if isinstance(pivot, str) and pivot in limit_pivots:
            center = cls.get_bounding_box(node, "center")
            limit_value = float(cls.get_bounding_box(node, pivot))
            axis_for_limit = {"x": 0, "y": 1, "z": 2}[pivot[0]]

            if axis_index is None:
                result = list(center)
                result[axis_for_limit] = limit_value
                return result
            return (
                limit_value
                if axis_index == axis_for_limit
                else float(center[axis_index])
            )

        cmds.warning(
            f"Invalid pivot type '{pivot}' for {node}. Defaulting to bounding box center."
        )
        fallback = cls.get_bounding_box(node, "center")
        return float(fallback[axis_index]) if axis_index is not None else list(fallback)

    @staticmethod
    @CoreUtils.undoable
    def align_pivot_to_selection(align_from=None, align_to=None, translate=True):
        """Align one object's pivot point to another using 3-point alignment."""
        if align_from is None:
            align_from = []
        if align_to is None:
            align_to = []
        align_from = CoreUtils.as_strings(align_from)
        align_to = CoreUtils.as_strings(align_to)
        pos = cmds.xform(align_to, q=True, translation=True, worldSpace=True)
        center_pos = [
            sum(pos[0::3]) / len(pos[0::3]),
            sum(pos[1::3]) / len(pos[1::3]),
            sum(pos[2::3]) / len(pos[2::3]),
        ]

        vertices = (
            cmds.ls(
                cmds.polyListComponentConversion(align_to, toVertex=True), flatten=True
            )
            or []
        )
        if len(vertices) < 3:
            return

        for obj in cmds.ls(CoreUtils.as_strings(align_from), flatten=True) or []:
            plane = cmds.polyPlane(
                name="_hptemp#",
                width=1,
                height=1,
                subdivisionsX=1,
                subdivisionsY=1,
                axis=[0, 1, 0],
                createUVs=2,
                constructionHistory=True,
            )[0]

            cmds.select(f"{plane}.vtx[0:2]", vertices[0:3])
            mel.eval("snap3PointsTo3Points(0)")

            cmds.xform(
                obj,
                rotation=cmds.xform(plane, q=True, rotation=True, worldSpace=True),
                worldSpace=True,
            )

            if translate:
                cmds.xform(obj, translation=center_pos, worldSpace=True)

            cmds.delete(plane)

    @staticmethod
    def reset_pivot_transforms(objects=None) -> None:
        """Reset Pivot Transforms for the specified objects or selected objects."""
        if objects is None:
            objs = cmds.ls(sl=True, type="transform", flatten=True) or []
        else:
            objs = (
                cmds.ls(CoreUtils.as_strings(objects), type="transform", flatten=True)
                or []
            )

        for obj in objs:
            cmds.xform(obj, centerPivots=True)
            # The legacy ``manipPivot(obj, rotatePivot=True, scalePivot=True)``
            # was a wrapper that re-aligned the manipulator pivot to the
            # object's rotate/scale pivots. ``cmds.manipPivot`` only takes
            # ``-p`` (position) / ``-o`` (orientation) — replicate by
            # querying and pushing.
            try:
                rp = cmds.xform(obj, q=True, ws=True, rp=True)
                cmds.manipPivot(p=rp, o=(0.0, 0.0, 0.0))
            except Exception:
                pass

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
        if objects is None:
            objects = cmds.ls(selection=True) or []

        grouped = _XformUtilsInternal._group_components_by_transform(objects)
        transforms = _XformUtilsInternal._resolve_transforms(objects)

        if not transforms:
            cmds.warning("No valid transform objects to align pivot.")
            return False if mode == "set" else None

        resolved: Dict[str, List[float]] = {}

        def pivot_for(xf):
            """A transform that contributed measurable components pivots on those; one
            selected whole (or whose components can't be measured) keeps its rotate pivot.

            Memoized so the mean below and the write further down can't disagree, and
            so neither re-queries what the other already resolved.
            """
            if xf not in resolved:
                center = (
                    _XformUtilsInternal._component_center(grouped[xf])
                    if xf in grouped
                    else None
                )
                resolved[xf] = center or cmds.xform(
                    xf, q=True, rotatePivot=True, worldSpace=True
                )
            return resolved[xf]

        all_components = [c for comps in grouped.values() for c in comps]
        # One shared manip position: the extent of every selected component (what the
        # manipulator itself straddles), falling back to the mean of the object pivots.
        shared_pivot_pos = (
            _XformUtilsInternal._component_center(all_components)
            if all_components
            else None
        )
        if shared_pivot_pos is None:
            positions = [pivot_for(xf) for xf in transforms]
            shared_pivot_pos = [sum(axis) / len(axis) for axis in zip(*positions)]

        if mode == "get":
            return {
                "position": shared_pivot_pos,
                "orientation": [0, 0, 0],
                "objects": [str(xf) for xf in transforms],
                "components": [str(c) for c in all_components],
            }

        if mode == "set":
            if pivot_type == "manip":
                cmds.manipPivot(p=shared_pivot_pos, o=(0, 0, 0))
                return True

            if pivot_type == "object":
                for xf in transforms:
                    cmds.xform(xf, worldSpace=True, pivots=pivot_for(xf), preserve=True)
                    cmds.xform(xf, preserve=True, rotateAxis=(0, 0, 0))
                # Re-align the manipulator onto the new pivot. See
                # ``reset_pivot_transforms`` for why this differs from the legacy overload.
                try:
                    cmds.manipPivot(p=shared_pivot_pos, o=(0.0, 0.0, 0.0))
                except Exception:
                    pass
                return True

            cmds.warning(f"Invalid pivot_type: {pivot_type}. Use 'manip' or 'object'.")
            return False

        cmds.warning(f"Invalid mode: {mode}. Use 'get' or 'set'.")
        return False

    @staticmethod
    @CoreUtils.undoable
    def bake_pivot(objects, position=False, orientation=False, preserve_instancing=True):
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
        bake: bool = False,
        world_space: bool = True,
        mirror: str = "",
        select_targets_after_transfer: bool = False,
        preserve_instancing: bool = True,
    ):
        """Transfer the pivot orientation from the first given object to the remaining given objects.

        Parameters:
            preserve_instancing (bool): Run the world-space ``rotate`` pass
                inside ``NodeUtils.preserve_instancing``.  That pass re-writes
                the target's vertex positions to pin its geometry while the
                transform re-orients — a shared datablock on an instanced
                target, which would swing every sibling instead.  The bake
                that follows is not covered here: ``freeze_transforms`` owns
                that decision through its own ``instance_strategy``.
            mirror (str): Optionally transfer a *mirror* of the source pivot instead of a direct
                copy. Accepts ``"x"``, ``"y"`` or ``"z"`` (case-insensitive) to reflect the
                transferred pivot across the axis-plane through the origin — the pivot position
                is reflected and its orientation is conjugated so the mirrored frame stays a
                valid right-handed rotation (useful when the target is a mirrored copy of the
                source). The reflection is taken in the operating space — world when
                ``world_space`` is True (the usual mirrored-copy case), otherwise the object's
                local space. Empty (the default) transfers the pivot unmirrored.
        """
        objects = cmds.ls(CoreUtils.as_strings(objects), type="transform", long=True) or []
        if not objects or len(objects) < 2:
            cmds.warning("At least two objects are required to transfer pivot.")
            return

        mirror = (mirror or "").lower()
        if mirror not in ("", "x", "y", "z"):
            cmds.warning(f"Invalid mirror axis '{mirror}'; expected 'x', 'y' or 'z'.")
            mirror = ""
        mirror_index = {"x": 0, "y": 1, "z": 2}.get(mirror)
        # Reflection matrix across the world plane perpendicular to the mirror axis (origin).
        mirror_matrix = None
        if mirror:
            _s = [-1.0 if i == mirror_index else 1.0 for i in range(3)]
            # fmt: off
            mirror_matrix = om.MMatrix([
                _s[0], 0.0,   0.0,   0.0,
                0.0,   _s[1], 0.0,   0.0,
                0.0,   0.0,   _s[2], 0.0,
                0.0,   0.0,   0.0,   1.0,
            ])
            # fmt: on

        source = objects[0]
        targets = objects[1:]

        with contextlib.ExitStack() as stack:
            # Only the world-space rotate pass touches geometry; anything else
            # is pure transform/pivot channel work and is instance-safe as-is.
            if preserve_instancing and rotate and world_space:
                stack.enter_context(NodeUtils.preserve_instancing(targets))
            cls._transfer_pivot_channels(
                source,
                targets,
                translate=translate,
                rotate=rotate,
                scale=scale,
                bake=bake,
                world_space=world_space,
                mirror=mirror,
                mirror_index=mirror_index,
                mirror_matrix=mirror_matrix,
            )

        if bake and targets:
            # Engine path (store=True) rather than raw makeIdentity: baking a
            # transferred pivot is a user-facing freeze and must stay
            # reversible. It also degrades gracefully when every channel flag
            # is False — makeIdentity errors on "nothing to do". One batched
            # call: the targets are independent, and the engine prints a
            # per-call summary.  Outside the instancing scope on purpose —
            # freeze_transforms guards instanced objects itself.
            cls.freeze_transforms(targets, t=translate, r=rotate, s=scale, force=True)

        if select_targets_after_transfer:
            cmds.select(targets, replace=True)

    @staticmethod
    @CoreUtils.undoable
    def aim_object_at_point(objects, target_pos, aim_vect=(1, 0, 0), up_vect=(0, 1, 0)):
        """Aim the given object(s) at the given world space position."""
        created_target = False
        if isinstance(target_pos, (tuple, list)):
            target = cmds.createNode("transform", name="target_helper")
            cmds.xform(target, translation=target_pos, absolute=True)
            created_target = True
        else:
            target = str(target_pos)

        constraints = []
        for obj in ptk.make_iterable(objects):
            obj = str(obj)
            const = cmds.aimConstraint(
                target, obj, aim=aim_vect, worldUpVector=up_vect, worldUpType="vector"
            )
            constraints.append(const)

        flat_constraints = []
        for c in constraints:
            if isinstance(c, list):
                flat_constraints.extend(c)
            else:
                flat_constraints.append(c)
        if flat_constraints:
            cmds.delete(flat_constraints)
        if created_target:
            cmds.delete(target)

    @staticmethod
    def orient_to_vector(
        transform,
        aim_vector=(1, 0, 0),
        up_vector=(0, 1, 0),
    ):
        """Orients a transform so its local +X aims along the given world-space vector."""
        transform = NodeUtils.get_transform_node(transform)
        if not transform:
            raise ValueError(f"// Error: Invalid transform node: {transform}")
        transform = str(transform)

        up_vector = om.MVector(up_vector[0], up_vector[1], up_vector[2])
        aim_vector = om.MVector(aim_vector[0], aim_vector[1], aim_vector[2])

        temp = cmds.spaceLocator()[0]
        target = cmds.spaceLocator()[0]

        pos_arr = cmds.xform(transform, q=True, ws=True, t=True)
        pos = om.MVector(pos_arr[0], pos_arr[1], pos_arr[2])
        cmds.xform(temp, ws=True, t=[pos.x, pos.y, pos.z])
        new_pos = pos + aim_vector
        cmds.xform(target, ws=True, t=[new_pos.x, new_pos.y, new_pos.z])

        cmds.delete(
            cmds.aimConstraint(
                target,
                temp,
                aimVector=(1, 0, 0),
                upVector=(up_vector.x, up_vector.y, up_vector.z),
                worldUpType="vector",
                worldUpVector=(up_vector.x, up_vector.y, up_vector.z),
                maintainOffset=False,
            )
        )

        rot = cmds.xform(temp, q=True, ws=True, ro=True)
        cmds.xform(transform, ws=True, ro=rot)
        cmds.delete([temp, target])

    @classmethod
    @CoreUtils.undoable
    def rotate_axis(cls, objects, target_pos):
        """Aim the given object at the given world space position. Rotations applied to
        rotated channel; geometry is transformed so it does not appear to move.
        """
        for obj in cmds.ls(CoreUtils.as_strings(objects), type="transform") or []:
            cls.aim_object_at_point(obj, target_pos)

            shapes = cmds.listRelatives(obj, shapes=True, noIntermediate=True) or []
            comp = None
            if shapes:
                stype = cmds.nodeType(shapes[0])
                if stype == "mesh":
                    comp = f"{obj}.vtx[*]"
                elif stype in ("nurbsCurve", "nurbsSurface"):
                    comp = f"{obj}.cv[*]"
                else:
                    comp = f"{obj}.cp[*]"
            else:
                comp = f"{obj}.cp[*]"

            wim = cmds.getAttr(f"{obj}.worldInverseMatrix[0]")
            cmds.xform(comp, matrix=wim)

            pos = cmds.xform(
                obj, q=True, translation=True, absolute=True, worldSpace=True
            )
            cmds.xform(comp, translation=pos, relative=True, worldSpace=True)

    @staticmethod
    def get_orientation(objects, returned_type="point"):
        """Get an objects orientation as a point or vector.

        Returns:
            (tuple)(list) If 'objects' given as a list, a list of tuples will be returned.
        """
        result = []
        for obj in cmds.ls(CoreUtils.as_strings(objects), objectsOnly=True) or []:
            world_matrix = cmds.xform(obj, q=True, matrix=True, worldSpace=True)
            rAxis = cmds.getAttr(f"{obj}.rotateAxis")[0]
            if any((rAxis[0], rAxis[1], rAxis[2])):
                print(
                    f"# Warning: {obj} has a modified .rotateAxis of {rAxis} which is included in the result. #"
                )

            if returned_type == "vector":
                ori = (
                    om.MVector(world_matrix[0], world_matrix[1], world_matrix[2]),
                    om.MVector(world_matrix[4], world_matrix[5], world_matrix[6]),
                    om.MVector(world_matrix[8], world_matrix[9], world_matrix[10]),
                )

            else:
                ori = (
                    world_matrix[0:3],
                    world_matrix[4:7],
                    world_matrix[8:11],
                )
            result.append(ori)

        return ptk.format_return(result, objects)

    @staticmethod
    def get_dist_between_two_objects(a, b):
        """Get the magnatude of a vector using the center points of two given objects.

        Returns:
            (float)
        """
        x1, y1, z1 = cmds.objectCenter(a)
        x2, y2, z2 = cmds.objectCenter(b)

        from math import sqrt

        return sqrt(pow((x1 - x2), 2) + pow((y1 - y2), 2) + pow((z1 - z2), 2))

    @staticmethod
    def get_center_point(objects):
        """Get the bounding box center point of any given object(s).

        Returns:
            (tuple) position as xyz float values.
        """
        objects = cmds.ls(CoreUtils.as_strings(objects), flatten=True) or []
        pos = [
            i
            for sublist in [
                cmds.xform(s, q=True, translation=True, worldSpace=True, absolute=True)
                for s in objects
            ]
            for i in sublist
        ]
        if not pos:
            return (0.0, 0.0, 0.0)
        center_pos = (
            sum(pos[0::3]) / len(pos[0::3]),
            sum(pos[1::3]) / len(pos[1::3]),
            sum(pos[2::3]) / len(pos[2::3]),
        )
        return center_pos

    @staticmethod
    def get_bounding_box(objects, value="", world_space=True, return_valid_keys=False):
        """Calculate and retrieve specific properties of the bounding box for the given object(s) or component(s)."""
        bbox_values = {
            "xmin": None,
            "xmax": None,
            "ymin": None,
            "ymax": None,
            "zmin": None,
            "zmax": None,
            "sizex": None,
            "sizey": None,
            "sizez": None,
            "size": None,
            "volume": None,
            "center": None,
            "centroid": None,
            "minsize": None,
            "maxsize": None,
        }

        if return_valid_keys:
            return list(bbox_values.keys())

        if not objects:
            raise ValueError("No objects provided for bounding box calculation.")

        objs = list(objects) if isinstance(objects, (list, tuple)) else [objects]
        objs = [str(o) for o in objs]
        bbox = (
            cmds.exactWorldBoundingBox(objs)
            if world_space
            else cmds.xform(objs, q=True, bb=True, ws=False)
        )

        xmin, ymin, zmin, xmax, ymax, zmax = bbox
        size = (xmax - xmin, ymax - ymin, zmax - zmin)
        center = ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)
        volume = size[0] * size[1] * size[2]

        bbox_values.update(
            {
                "xmin": xmin,
                "xmax": xmax,
                "ymin": ymin,
                "ymax": ymax,
                "zmin": zmin,
                "zmax": zmax,
                "sizex": size[0],
                "sizey": size[1],
                "sizez": size[2],
                "size": size,
                "volume": volume,
                "center": center,
                "centroid": center,
                "minsize": min(size),
                "maxsize": max(size),
            }
        )

        values = value.lower().split("|")
        try:
            return (
                tuple(bbox_values[val] for val in values)
                if len(values) > 1
                else bbox_values[values[0]]
            )
        except KeyError as e:
            raise ValueError(f"Invalid value for bounding box data requested: {e}")

    @classmethod
    def sort_by_bounding_box_value(
        cls, objects, value="volume", descending=True, also_return_value=False
    ):
        """Sort the given objects by their bounding box value."""
        valueAndObjs = []
        for obj in cmds.ls(CoreUtils.as_strings(objects), flatten=False) or []:
            v = cls.get_bounding_box(obj, value)
            valueAndObjs.append((v, obj))

        sorted_ = sorted(valueAndObjs, key=lambda x: x[0], reverse=descending)
        if also_return_value:
            return sorted_
        return [obj for v, obj in sorted_]

    @staticmethod
    @CoreUtils.undoable
    def align_using_three_points(vertices):
        """Move and align the object defined by the first 3 points to the last 3 points."""
        vertices = cmds.ls(CoreUtils.as_strings(vertices), flatten=True) or []
        if len(vertices) < 6:
            cmds.warning("align_using_three_points requires exactly 6 vertices.")
            return

        # Resolve the owning transform for the first 3 vertices.
        # ``cmds.ls(objectsOnly=True)`` on a vertex returns the *shape*, not
        # the transform. Walk up to the parent if needed.
        owners = cmds.ls(vertices[:3], objectsOnly=True) or []
        object_to_move = []
        for owner in owners:
            if cmds.objectType(owner, isAType="transform"):
                object_to_move.append(owner)
            else:
                parents = cmds.listRelatives(owner, parent=True, fullPath=True) or []
                if parents:
                    object_to_move.append(parents[0])
        if not object_to_move:
            cmds.warning("First 3 vertices must belong to a transform node.")
            return

        p0, p1, p2 = [
            om.MVector(*cmds.pointPosition(v, world=True)) for v in vertices[0:3]
        ]
        p3, p4, p5 = [
            om.MVector(*cmds.pointPosition(v, world=True)) for v in vertices[3:6]
        ]

        def _build_frame(a, b, c):
            x_axis = (b - a).normal()
            temp = (c - a).normal()
            z_axis = (x_axis ^ temp).normal()
            y_axis = (z_axis ^ x_axis).normal()
            return x_axis, y_axis, z_axis

        src_x, src_y, src_z = _build_frame(p0, p1, p2)
        tgt_x, tgt_y, tgt_z = _build_frame(p3, p4, p5)

        src_mat = om.MMatrix(
            [
                src_x.x,
                src_x.y,
                src_x.z,
                0,
                src_y.x,
                src_y.y,
                src_y.z,
                0,
                src_z.x,
                src_z.y,
                src_z.z,
                0,
                p0.x,
                p0.y,
                p0.z,
                1,
            ]
        )
        tgt_mat = om.MMatrix(
            [
                tgt_x.x,
                tgt_x.y,
                tgt_x.z,
                0,
                tgt_y.x,
                tgt_y.y,
                tgt_y.z,
                0,
                tgt_z.x,
                tgt_z.y,
                tgt_z.z,
                0,
                p3.x,
                p3.y,
                p3.z,
                1,
            ]
        )

        delta = src_mat.inverse() * tgt_mat

        current_mat = om.MMatrix(
            cmds.xform(object_to_move[0], q=True, matrix=True, worldSpace=True)
        )
        new_mat = current_mat * delta
        cmds.xform(
            object_to_move[0],
            matrix=_XformUtilsInternal._mmatrix_to_flat(new_mat),
            worldSpace=True,
        )

    @staticmethod
    def is_overlapping(a, b, tolerance=0.001):
        """Check if the vertices in a and b are overlapping within the given tolerance."""
        vert_setA = (
            cmds.ls(cmds.polyListComponentConversion(a, toVertex=True), flatten=True)
            or []
        )
        vert_setB = (
            cmds.ls(cmds.polyListComponentConversion(b, toVertex=True), flatten=True)
            or []
        )

        closestVerts = Components.get_closest_verts(
            vert_setA, vert_setB, tolerance=tolerance
        )

        return True if vert_setA and len(closestVerts) == len(vert_setA) else False

    @staticmethod
    def check_objects_against_plane(
        objects,
        plane_point,
        plane_normal,
        return_type: str = "bool",
    ):
        """General method to check if any object's geometry is below a defined plane."""
        plane_point = om.MPoint(*plane_point)
        plane_normal = om.MVector(*plane_normal).normalize()

        objects = CoreUtils.as_strings(objects)
        objects_below_threshold = []

        for obj in objects:
            obj = str(obj)
            try:
                if not cmds.objectType(obj, isAType="transform"):
                    print(f"Invalid object type: {obj}. Expected Transform node.")
                    continue
            except Exception:
                print(f"Invalid object: {obj}.")
                continue

            try:
                sel_list = om.MSelectionList()
                sel_list.add(obj)
                dag_path = sel_list.getDagPath(0)
            except Exception as e:
                print(f"Error getting dag path for {obj}: {e}")
                continue

            dag_path_shape = dag_path.extendToShape()
            if dag_path_shape.apiType() != om.MFn.kMesh:
                continue

            world_matrix = dag_path.inclusiveMatrix()

            mesh_fn = om.MFnMesh(dag_path_shape)
            points = mesh_fn.getPoints(om.MSpace.kObject)

            falling_vertices = []
            below = False

            for idx, point in enumerate(points):
                transformed_point = point * world_matrix
                distance = (transformed_point - plane_point) * plane_normal

                if distance < 0:
                    if return_type == "bool":
                        below = True
                        break
                    elif return_type == "mpoint":
                        falling_vertices.append(transformed_point)
                    elif return_type == "vector":
                        falling_vertices.append(
                            om.MVector(
                                transformed_point.x,
                                transformed_point.y,
                                transformed_point.z,
                            )
                        )
                    elif return_type == "vertex":
                        falling_vertices.append(f"{obj}.vtx[{idx}]")
                    else:
                        print(
                            f"Invalid return_type: {return_type}. Expected 'bool', 'mpoint', 'vector', or 'vertex'."
                        )
                        return []

            if falling_vertices and return_type != "bool":
                objects_below_threshold.append((obj, falling_vertices))

            if return_type == "bool":
                objects_below_threshold.append((obj, below))

        return objects_below_threshold

    @staticmethod
    def get_vertex_positions(objects, worldSpace=True):
        """Get all vertex positions for the given objects.

        Returns:
            (list) Nested lists if multiple objects given.
        """
        import maya.OpenMaya as om1

        space = om1.MSpace.kWorld if worldSpace else om1.MSpace.kObject

        result = []
        for mesh in CoreUtils.get_mfn_mesh(objects, api_version=1):
            points = om1.MPointArray()
            mesh.getPoints(points, space)

            result.append(
                [
                    (points[i][0], points[i][1], points[i][2])
                    for i in range(points.length())
                ]
            )
        return ptk.format_return(result, objects)

    @classmethod
    def get_matching_verts(cls, a, b, world_space=False):
        """Find any vertices which point locations match between two given mesh.

        Returns:
            (list) nested tuples with int values representing matching vertex pairs.
        """
        vert_pos_a, vert_pos_b = cls.get_vertex_positions([a, b], world_space)
        hash_a, hash_b = ptk.PointCloud.hash_points([vert_pos_a, vert_pos_b])

        matching = set(hash_a).intersection(hash_b)
        return [
            i
            for h in matching
            for i in zip(ptk.indices(hash_a, h), ptk.indices(hash_b, h))
        ]

    @classmethod
    def order_by_distance(cls, objects, reference_point=None, reverse=False):
        """Order the given objects by their distance from the given reference point.

        Returns:
            (list) ordered objects (as plain strings)
        """
        if reference_point is None:
            reference_point = [0, 0, 0]

        distance_object_pairs = []

        for obj in cmds.ls(CoreUtils.as_strings(objects), flatten=True, long=True) or []:
            bb_center = cls.get_bounding_box(obj, "center")
            distance = (
                (bb_center[0] - reference_point[0]) ** 2
                + (bb_center[1] - reference_point[1]) ** 2
                + (bb_center[2] - reference_point[2]) ** 2
            ) ** 0.5
            distance_object_pairs.append((distance, obj))

        distance_object_pairs.sort(key=lambda x: x[0], reverse=reverse)

        return [pair[1] for pair in distance_object_pairs]

    @staticmethod
    @CoreUtils.undoable
    def align_vertices(mode, average=False, edgeloop=False):
        """Align selected vertices along one or more axes."""
        selectTypeEdge = cmds.selectType(query=True, edge=True)

        if edgeloop:
            mel.eval("SelectEdgeLoopSp")

        mel.eval("PolySelectConvert 3")

        selection = cmds.ls(sl=True, flatten=True) or []

        if len(selection) < 2:
            if len(selection) == 0:
                return cmds.inViewMessage(
                    statusMessage="<hl>No vertices selected.</hl>",
                    pos="topCenter",
                    fade=True,
                )
            return cmds.inViewMessage(
                statusMessage="<hl>Selection must contain at least two vertices.</hl>",
                pos="topCenter",
                fade=True,
            )

        lastSelected = cmds.ls(tail=1, sl=True, flatten=True) or []
        align_to = cmds.xform(lastSelected, q=True, translation=True, worldSpace=True)
        alignX = align_to[0]
        alignY = align_to[1]
        alignZ = align_to[2]

        if average:
            xyz = cmds.xform(selection, q=True, translation=True, worldSpace=True)
            x = xyz[0::3]
            y = xyz[1::3]
            z = xyz[2::3]
            alignX = float(sum(x)) / (len(xyz) / 3)
            alignY = float(sum(y)) / (len(xyz) / 3)
            alignZ = float(sum(z)) / (len(xyz) / 3)

        for vertex in selection:
            vertexXYZ = cmds.xform(vertex, q=True, translation=True, worldSpace=True)
            vertX = vertexXYZ[0]
            vertY = vertexXYZ[1]
            vertZ = vertexXYZ[2]

            modes = {
                0: (vertX, alignY, alignZ),
                1: (alignX, vertY, alignZ),
                2: (alignX, alignY, vertZ),
                3: (alignX, vertY, vertZ),
                4: (vertX, alignY, vertZ),
                5: (vertX, vertY, alignZ),
                6: (alignX, alignY, alignZ),
            }

            cmds.xform(vertex, translation=modes[mode], worldSpace=True)

        if selectTypeEdge:
            cmds.selectType(edge=True)

    @staticmethod
    def get_translation(node, world: bool = False):
        """Translation as ``om.MVector``.

        ``world=False`` returns the object-space translation (the default for
        child translation); ``world=True`` returns world space.
        """
        flag = {"ws": True} if world else {"os": True}
        t = cmds.xform(str(node), q=True, t=True, **flag)
        return om.MVector(*t)

    @staticmethod
    def get_object_matrix(node, world: bool = False):
        """Local or world matrix as ``om.MMatrix``."""
        flag = {"ws": True} if world else {"os": True}
        flat = cmds.xform(str(node), q=True, m=True, **flag)
        return om.MMatrix(flat)

    @staticmethod
    def set_object_matrix(node, value, world: bool = False) -> None:
        """Apply *value* to *node*'s local or world transformation matrix.

        *value* may be an ``om.MMatrix`` (anything with ``getElement(r, c)``)
        or a 16-element iterable in row-major order.
        """
        if hasattr(value, "getElement"):
            flat = [value.getElement(r, c) for r in range(4) for c in range(4)]
        else:
            flat = list(value)
        if len(flat) != 16:
            raise ValueError(f"set_object_matrix expected 16 elements, got {len(flat)}")
        flag = {"worldSpace": True} if world else {"objectSpace": True}
        cmds.xform(str(node), matrix=flat, **flag)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
