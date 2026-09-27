# !/usr/bin/python
# coding=utf-8
"""Pivots behind :class:`mayatk.XformUtils`.

The manipulator pivot (read / write / snapshot cache), the operation-axis
matrix and position the mirror and cut tools build on, and object-pivot edits:
align, reset, world-align, bake and transfer. Reached through
:class:`mayatk.XformUtils`; nothing here is called directly.
"""

from __future__ import annotations

import contextlib
import math
from typing import List, Dict, Optional

try:
    import maya.cmds as cmds
    import maya.mel as mel
    from maya.api import OpenMaya as om
except Exception:
    cmds = mel = om = None


from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.xform_utils.matrices import Matrices


class _PivotInternal:
    """Private helpers and ``XformUtils`` method bodies; see the module docstring."""

    @staticmethod
    def _pure_world_rotation(obj: str):
        """*obj*'s world orientation as a PURE rotation matrix.

        Decomposed through ``MTransformationMatrix`` rather than sliced off
        the world matrix: a scaled object's upper 3x3 carries its scale, and
        that scale survives the mirror conjugation below as a reflection the
        frame was never meant to have.
        """
        m = om.MMatrix(cmds.xform(obj, q=True, ws=True, matrix=True))
        return om.MTransformationMatrix(m).rotation().asMatrix()

    @staticmethod
    def _set_world_rotation(obj: str, rotation) -> None:
        """Drive *obj*'s world orientation to the *rotation* matrix.

        ``xform -ws -ro`` interprets the euler it is handed in the object's
        OWN rotate order, so it is decomposed in that order — feeding raw XYZ
        to a ``yzx`` object lands it on a different orientation entirely.
        """
        _, euler_deg, _ = Matrices.decompose(
            rotation, rotate_order=cmds.xform(obj, q=True, roo=True)
        )
        cmds.xform(obj, ws=True, ro=list(euler_deg))

    @classmethod
    def _transfer_pivot_channels(
        cls,
        source,
        targets,
        translate,
        rotate,
        scale,
        world_space,
        mirror,
        mirror_index,
        mirror_matrix,
    ):
        """``transfer_pivot``'s PERMANENT per-target channel work, minus the
        select bookkeeping — split out so the caller can scope the
        geometry-writing world-space rotate pass."""
        # Snapshot the source ONCE, mirrored, before anything is written.
        # Re-reading it per target is not just redundant queries: if a target
        # is an ancestor of the source, re-orienting it MOVES the source, and
        # the next target would receive that moved frame instead of the one
        # the caller asked to transfer.
        rp = sp = src_rot = source_ra = None
        if translate:
            rp = cmds.xform(source, q=True, ws=world_space, rp=True)
            if mirror:
                rp[mirror_index] = -rp[mirror_index]
        if scale:
            sp = cmds.xform(source, q=True, ws=world_space, sp=True)
            if mirror:
                sp[mirror_index] = -sp[mirror_index]
        if rotate:
            if world_space:
                src_rot = cls._pure_world_rotation(source)
                if mirror:
                    # Conjugating the rotation by the reflection (S * R * S)
                    # keeps a valid right-handed rotation while reflecting it
                    # across the axis-plane.
                    src_rot = mirror_matrix * src_rot * mirror_matrix
            else:
                source_ra = cmds.xform(source, q=True, ra=True)
                if mirror:
                    # Mirror the pivot orientation (rotateAxis) across the
                    # axis-plane: the rotation about the mirror axis is
                    # preserved, the other two negate.
                    source_ra = [
                        source_ra[i] if i == mirror_index else -source_ra[i]
                        for i in range(3)
                    ]

        for target in targets:
            if translate:
                cmds.xform(target, ws=world_space, rp=rp)
            if scale:
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
                    # Snapshot the geometry in world space so the re-orientation
                    # below can be lifted back out of it.  Vectorized through the
                    # shared primitives: the per-vertex `pointPosition` loop this
                    # replaces cost two cmds calls per point and silently skipped
                    # every non-mesh shape, leaving NURBS targets swinging.
                    shape_points = {}
                    for sh in shapes:
                        pts = cls._get_shape_points_world(sh)
                        if pts is not None:
                            shape_points[sh] = pts

                    # Any pivot orientation the target ALREADY carries has to go
                    # first.  The write below drives the rotate channel, and the
                    # net local rotation is rotateAxis * rotate — so a leftover
                    # rotateAxis composes on top and the transfer lands on
                    # `ra * source` instead of on `source`.  `xform -ra`
                    # compensates rotate to hold the world orientation, so
                    # clearing it here moves nothing.
                    # Cleared inside the try below: a locked or connected
                    # rotateAxis raises here, and the children are already
                    # parked at world level by this point.

                    # `src_rot` is the source's FULL world frame, snapshotted
                    # above.  `matchTransform -rot` (the previous path) matches
                    # the rotate CHANNEL and ignores rotateAxis on both ends, so
                    # a source carrying a custom pivot orientation — the case
                    # this tool exists for — transferred as an unrotated frame.
                    try:
                        cmds.xform(target, ra=(0, 0, 0))
                        cls._set_world_rotation(target, src_rot)

                        # Park the transferred frame on the PIVOT and leave the
                        # rotate channel clean — a pivot transfer should not
                        # show up as a rotation the user never applied.
                        # `xform -ra` compensates rotate to hold the world
                        # orientation, so handing it the net local rotation
                        # zeroes rotate exactly.  (Writing the INVERSE here,
                        # after zeroing rotate, is what cancelled the whole
                        # transfer out to identity — the operation did nothing.)
                        # rotateAxis is always XYZ, whatever the rotate order.
                        _, euler_deg, _ = Matrices.decompose(
                            om.MMatrix(cmds.xform(target, q=True, matrix=True, os=True))
                        )
                        cmds.xform(target, ra=list(euler_deg))
                    except Exception as e:
                        # Locked/driven rotate channels: never abort the batch
                        # mid-target — the children below are parked at world
                        # level and still have to be put back.
                        cmds.warning(
                            f"transfer_pivot: could not orient '{target}': {e}"
                        )

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
                    # Pin the geometry: re-framing a pivot must not move the
                    # object, and the write above swung it with the transform.
                    if shape_points:
                        inverse_new_world = Matrices.safe_inverse(
                            om.MMatrix(cmds.xform(target, q=True, ws=True, matrix=True))
                        )
                        if inverse_new_world is None:
                            cmds.warning(
                                f"transfer_pivot: '{target}' has a singular world "
                                "matrix — its geometry was left uncompensated."
                            )
                        else:
                            for sh, pts in shape_points.items():
                                cls._set_shape_points_object(sh, pts, inverse_new_world)

                else:
                    # Object space is a verbatim channel copy, matching how the
                    # translate pass copies the local rotate pivot.  Restore the
                    # rotate channel afterwards: `xform -ra` silently compensates
                    # it to hold the world orientation, which both mangles a
                    # channel the user never touched and cancels the copy out to
                    # no visible change.
                    target_ro = cmds.xform(target, q=True, ro=True)
                    cmds.xform(target, ra=source_ra)
                    cmds.xform(target, ro=target_ro)

    @classmethod
    def _transfer_manip_pivot(
        cls,
        source,
        targets,
        translate,
        rotate,
        mirror,
        mirror_index,
        mirror_matrix,
    ):
        """``transfer_pivot``'s TEMPORARY path: aim Maya's manipulator at the
        source's pivot without touching a single target attribute.

        ``manipPivot`` is one global manipulator override, not a per-object
        channel, so the targets are selected together and share the pivot —
        the same convention ``restore_original_axes`` follows.  Maya's
        ``manipPivotReset`` (the panel's Reset Pivot) clears it, and nothing
        here is saved with the scene.
        """
        if not (translate or rotate):
            return

        kwargs = {}
        if translate:
            pos = cmds.xform(source, q=True, ws=True, rp=True)
            if mirror:
                pos[mirror_index] = -pos[mirror_index]
            kwargs["p"] = pos
        if rotate:
            rotation = cls._pure_world_rotation(source)
            if mirror:
                rotation = mirror_matrix * rotation * mirror_matrix
            # manipPivot's orientation is a world-space XYZ euler.
            _, euler_deg, _ = Matrices.decompose(rotation)
            kwargs["o"] = list(euler_deg)

        # The manipulator addresses whatever is selected, so the targets have
        # to be the selection before the override lands.
        cmds.select(targets, replace=True)
        try:
            cmds.manipPivot(**kwargs)
        except Exception as e:
            # manipPivot is GUI-only — it has no manipulator to override in a
            # batch session.  Say so rather than failing silently: the caller
            # asked for a pivot and did not get one.
            cmds.warning(
                f"transfer_pivot: could not set the manipulator pivot ({e}). "
                "A temporary pivot needs an interactive Maya session — pass "
                "bake=True for a permanent one."
            )

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

    @staticmethod
    def _get_manip_pivot_matrix(obj, **kwargs):
        """Body of :meth:`XformUtils.get_manip_pivot_matrix`."""
        matrix = cmds.xform(obj, q=True, matrix=True, **kwargs)
        return om.MMatrix(matrix)

    @staticmethod
    def _set_manip_pivot_matrix(obj, matrix, **kwargs):
        """Body of :meth:`XformUtils.set_manip_pivot_matrix`."""
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
    def _restore_original_axes(cls, objects, prefix):
        """Body of :meth:`XformUtils.restore_original_axes`."""
        if objects is None:
            objects = cmds.ls(selection=True, type="transform") or []
        targets = (
            cmds.ls(CoreUtils.as_strings(objects), type="transform", long=True) or []
        )
        stamped = [
            t
            for t in targets
            if cls.get_stored_transforms(t, prefix=prefix) is not None
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
            cls.set_manip_pivot_matrix(
                node, cls.get_operation_axis_matrix(node, "original")
            )
        finally:
            # set_manip_pivot_matrix re-selects to address the manipulator —
            # put the caller's selection back exactly, empty included.
            if selection:
                cmds.select(selection, replace=True)
            else:
                cmds.select(clear=True)
        return node

    @classmethod
    def _get_pivot_options(cls):
        """Body of :meth:`XformUtils.get_pivot_options`."""
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
    def _clear_manip_cache(cls):
        """Body of :meth:`XformUtils.clear_manip_cache`."""
        cls._manip_cache.clear()

    @classmethod
    def _snapshot_manip_pivot(cls, node):
        """Body of :meth:`XformUtils.snapshot_manip_pivot`."""
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
    def _get_operation_axis_matrix(cls, node, pivot):
        """Body of :meth:`XformUtils.get_operation_axis_matrix`."""
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
    def _get_operation_axis_pos(cls, node, pivot, axis_index):
        """Body of :meth:`XformUtils.get_operation_axis_pos`."""
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
            world_matrix = cls.get_object_matrix(node, world=True)
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
    def _align_pivot_to_selection(align_from, align_to, translate):
        """Body of :meth:`XformUtils.align_pivot_to_selection`."""
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
    def _reset_pivot_transforms(objects):
        """Body of :meth:`XformUtils.reset_pivot_transforms`."""
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

    @classmethod
    def _world_align_pivot(cls, objects, pivot_type, mode):
        """Body of :meth:`XformUtils.world_align_pivot`."""
        if objects is None:
            objects = cmds.ls(selection=True) or []

        grouped = cls._group_components_by_transform(objects)
        transforms = cls._resolve_transforms(objects)

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
                center = cls._component_center(grouped[xf]) if xf in grouped else None
                resolved[xf] = center or cmds.xform(
                    xf, q=True, rotatePivot=True, worldSpace=True
                )
            return resolved[xf]

        all_components = [c for comps in grouped.values() for c in comps]
        # One shared manip position: the extent of every selected component (what the
        # manipulator itself straddles), falling back to the mean of the object pivots.
        shared_pivot_pos = (
            cls._component_center(all_components) if all_components else None
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

    @classmethod
    def _transfer_pivot(
        cls,
        objects,
        translate,
        rotate,
        scale,
        bake,
        world_space,
        mirror,
        select_targets_after_transfer,
        preserve_instancing,
    ):
        """Body of :meth:`XformUtils.transfer_pivot`."""
        objects = (
            cmds.ls(CoreUtils.as_strings(objects), type="transform", long=True) or []
        )
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

        if not bake:
            # Temporary: aim the MANIPULATOR at the source's pivot and leave
            # every target attribute alone.  Nothing here is permanent, so the
            # geometry-pinning and instancing machinery is not involved.
            cls._transfer_manip_pivot(
                source,
                targets,
                translate=translate,
                rotate=rotate,
                mirror=mirror,
                mirror_index=mirror_index,
                mirror_matrix=mirror_matrix,
            )
        else:
            with contextlib.ExitStack() as stack:
                # Only the world-space rotate pass touches geometry; anything
                # else is pure transform/pivot channel work and is
                # instance-safe as-is.
                if preserve_instancing and rotate and world_space:
                    stack.enter_context(NodeUtils.preserve_instancing(targets))
                cls._transfer_pivot_channels(
                    source,
                    targets,
                    translate=translate,
                    rotate=rotate,
                    scale=scale,
                    world_space=world_space,
                    mirror=mirror,
                    mirror_index=mirror_index,
                    mirror_matrix=mirror_matrix,
                )

        if select_targets_after_transfer:
            cmds.select(targets, replace=True)
