# !/usr/bin/python
# coding=utf-8
from __future__ import annotations

import contextlib
import math
from typing import List, Tuple, Dict, Union, Optional, Set, Any

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
from mayatk.core_utils._core_utils import CoreUtils, as_strings, short_name
from mayatk.core_utils.components import Components
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.node_utils.attributes._attributes import Attributes


def get_translation(node, world: bool = False):
    """Translation as ``om.MVector``.

    ``world=False`` returns the object-space translation (the default for
    child translation); ``world=True`` returns world space.
    """
    flag = {"ws": True} if world else {"os": True}
    t = cmds.xform(str(node), q=True, t=True, **flag)
    return om.MVector(*t)


def get_object_matrix(node, world: bool = False):
    """Local or world matrix as ``om.MMatrix``."""
    flag = {"ws": True} if world else {"os": True}
    flat = cmds.xform(str(node), q=True, m=True, **flag)
    return om.MMatrix(flat)


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


def _set_matrix_plug(plug: str, mmatrix) -> None:
    """Write an ``om.MMatrix`` (or 16-flat iterable) to a matrix attribute plug."""
    if hasattr(mmatrix, "getElement"):
        flat = [mmatrix.getElement(r, c) for r in range(4) for c in range(4)]
    else:
        flat = list(mmatrix)
    cmds.setAttr(plug, *flat, type="matrix")


def _mmatrix_to_flat(m) -> List[float]:
    if hasattr(m, "getElement"):
        return [m.getElement(r, c) for r in range(4) for c in range(4)]
    return list(m)


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
    target_tm.setTranslation(
        src_t.translation(om.MSpace.kWorld), om.MSpace.kWorld
    )

    src_r = stored_tm if "rotate" in channels else current_tm
    target_tm.setRotation(src_r.rotation(asQuaternion=True))

    src_s = stored_tm if "scale" in channels else current_tm
    target_tm.setScale(src_s.scale(om.MSpace.kWorld), om.MSpace.kWorld)

    # Shear is not a freezable channel — preserve whatever the object
    # currently has (a fresh TM defaults shear to zero, which would
    # silently destroy user-set shear on partial restore).
    target_tm.setShear(current_tm.shear(om.MSpace.kWorld), om.MSpace.kWorld)

    return target_tm.asMatrix()


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
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
]


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


def _compose_local(t_vec, r_quat, s_vec):
    """Build an ``MMatrix`` from a translation vector, rotation quaternion, and scale vector."""
    tm = om.MTransformationMatrix()
    tm.setTranslation(t_vec, om.MSpace.kTransform)
    tm.setRotation(r_quat)
    tm.setScale(s_vec, om.MSpace.kTransform)
    return tm.asMatrix()


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


def _write_bake_t(node, t_attr, t_vec):
    if not cmds.attributeQuery(t_attr, node=node, exists=True):
        cmds.addAttr(node, ln=t_attr, dt="double3", keyable=False)
    plug = f"{node}.{t_attr}"
    cmds.setAttr(plug, t_vec[0], t_vec[1], t_vec[2], type="double3")
    if cmds.getAttr(plug, keyable=True) or cmds.getAttr(plug, channelBox=True):
        cmds.setAttr(plug, keyable=False, channelBox=False)


def _write_bake_r(node, r_attr, r_quat):
    if not cmds.attributeQuery(r_attr, node=node, exists=True):
        cmds.addAttr(node, ln=r_attr, at="matrix", keyable=False)
    plug = f"{node}.{r_attr}"
    flat = _mmatrix_to_flat(r_quat.asMatrix())
    cmds.setAttr(plug, *flat, type="matrix")
    if cmds.getAttr(plug, keyable=True) or cmds.getAttr(plug, channelBox=True):
        cmds.setAttr(plug, keyable=False, channelBox=False)


def _write_bake_s(node, s_attr, s_vec):
    if not cmds.attributeQuery(s_attr, node=node, exists=True):
        cmds.addAttr(node, ln=s_attr, dt="double3", keyable=False)
    plug = f"{node}.{s_attr}"
    cmds.setAttr(plug, s_vec[0], s_vec[1], s_vec[2], type="double3")
    if cmds.getAttr(plug, keyable=True) or cmds.getAttr(plug, channelBox=True):
        cmds.setAttr(plug, keyable=False, channelBox=False)


def _bake_attr_names(prefix):
    """``(t_attr, r_attr, s_attr)`` triple used by store/restore/clear/has."""
    return f"{prefix}_T_bake", f"{prefix}_R_bake", f"{prefix}_S_bake"


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

        cmds.setAttr(
            f"{node}.translate", t_vec.x, t_vec.y, t_vec.z, type="double3"
        )
        cmds.setAttr(
            f"{node}.scale", s_vec[0], s_vec[1], s_vec[2], type="double3"
        )

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


def _shift_shape_points(shape: str, transform_matrix) -> None:
    """Bulk-transform a shape's points by *transform_matrix* (world-space).

    Reads each point in world space, multiplies by *transform_matrix*, writes
    back in object space. Used by ``restore_transforms`` to compensate vertex
    positions before the transform's world matrix is reset. Vectorized via
    the OpenMaya 2.0 API — O(1) cmds calls regardless of point count.

    Supports mesh (``MFnMesh``), nurbsCurve (``MFnNurbsCurve``), and
    nurbsSurface (``MFnNurbsSurface``). Other shape types are skipped.
    """
    if om is None or cmds is None:
        return
    node_type = cmds.nodeType(shape)
    if node_type not in ("mesh", "nurbsCurve", "nurbsSurface"):
        return
    sel = om.MSelectionList()
    sel.add(shape)
    dag = sel.getDagPath(0)
    if node_type == "mesh":
        fn = om.MFnMesh(dag)
        pts = fn.getPoints(om.MSpace.kWorld)
        for i in range(len(pts)):
            pts[i] = pts[i] * transform_matrix
        fn.setPoints(pts, om.MSpace.kObject)
    elif node_type == "nurbsCurve":
        fn = om.MFnNurbsCurve(dag)
        pts = fn.cvPositions(om.MSpace.kWorld)
        for i in range(len(pts)):
            pts[i] = pts[i] * transform_matrix
        fn.setCVPositions(pts, om.MSpace.kObject)
        fn.updateCurve()
    else:  # nurbsSurface
        fn = om.MFnNurbsSurface(dag)
        pts = fn.cvPositions(om.MSpace.kWorld)
        for i in range(len(pts)):
            pts[i] = pts[i] * transform_matrix
        fn.setCVPositions(pts, om.MSpace.kObject)
        fn.updateSurface()


class XformUtilsInternals:
    """Internal helper methods for XformUtils.

    This class encapsulates implementation details that should not be part of
    the public API. XformUtils inherits from this class to access these helpers.
    """

    @staticmethod
    def _apply_freeze_deltas(obj, axes_to_freeze):
        """Apply freeze transformations using Maya's native makeIdentity.

        Maya's makeIdentity automatically preserves world-space pivot positions
        by adjusting rotatePivotTranslate/scalePivotTranslate as needed.

        Parameters:
            obj: The transform node to freeze.
            axes_to_freeze (set): Set of axes to freeze (e.g., {'tx', 'ty', 'tz', 'rx', ...}).

        Returns:
            bool: True if successful, False if skipped due to error.
        """
        freeze_t = not axes_to_freeze.isdisjoint({"tx", "ty", "tz"})
        freeze_r = not axes_to_freeze.isdisjoint({"rx", "ry", "rz"})
        freeze_s = not axes_to_freeze.isdisjoint({"sx", "sy", "sz"})

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
                normal=False,
            )
        except RuntimeError:
            cmds.makeIdentity(
                obj,
                apply=True,
                t=freeze_t,
                r=freeze_r,
                s=freeze_s,
                pn=False,
                normal=False,
            )
        return True


class XformUtils(XformUtilsInternals, ptk.HelpMixin):
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
            axis = index_to_axis[value]
        elif isinstance(value, str):
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
        source = cmds.ls(as_strings(source), flatten=True) or []
        target = cmds.ls(as_strings(target), flatten=True) or []
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

    @staticmethod
    @CoreUtils.undoable
    def drop_to_grid(
        objects, align="Mid", origin=False, center_pivot=False, freeze_transforms=False
    ):
        """Align objects to Y origin on the grid using a helper plane.

        Parameters:
            objects (str/obj/list): The objects to translate.
            align (bool): Specify which point of the object's bounding box to align with the grid. (valid: 'Max','Mid'(default),'Min')
            origin (bool): Move to world grid's center.
            center_pivot (bool): Center the object's pivot.
            freeze_transforms (bool): Reset the selected transform and all of its children down to the shape level.
        """
        for obj in cmds.ls(as_strings(objects), transforms=True) or []:
            osPivot = cmds.xform(obj, q=True, rotatePivot=True, objectSpace=True)
            wsPivot = cmds.xform(obj, q=True, rotatePivot=True, worldSpace=True)

            cmds.xform(obj, centerPivots=True)
            plane = cmds.polyPlane(name="temp#")[0]

            if not origin:
                cmds.xform(
                    plane, translation=(wsPivot[0], 0, wsPivot[2]), absolute=True, ws=True
                )

            cmds.align(obj, plane, atl=True, x="Mid", y=align, z="Mid")
            cmds.delete(plane)

            if not center_pivot:
                cmds.xform(obj, rotatePivot=osPivot, objectSpace=True)

            if freeze_transforms:
                cmds.makeIdentity(obj, apply=True)

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
        to_scale = cmds.ls(as_strings(a), flatten=True) or []

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

        connected_edges_sets = Components.get_contigious_edges(objects)

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

        targets = cmds.ls(as_strings(objects), type="transform", long=True) or []
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

        t_attr, r_attr, s_attr = _bake_attr_names(prefix)

        for obj in targets:
            cur_t, cur_r, cur_s = _decompose_local(obj)

            if "translate" in target_channels:
                old_t = _read_bake_t(obj, t_attr) if accumulate else om.MVector(0, 0, 0)
                new_t = old_t + cur_t
                _write_bake_t(obj, t_attr, [new_t.x, new_t.y, new_t.z])

            if "rotate" in target_channels:
                old_r = _read_bake_r(obj, r_attr) if accumulate else om.MQuaternion()
                new_r = old_r * cur_r
                _write_bake_r(obj, r_attr, new_r)

            if "scale" in target_channels:
                old_s = _read_bake_s(obj, s_attr) if accumulate else [1.0, 1.0, 1.0]
                new_s = [old_s[i] * cur_s[i] for i in range(3)]
                _write_bake_s(obj, s_attr, new_s)

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
        from_channel_box=False,
        **kwargs,
    ):
        """Freezes transformations on the given objects."""
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
                "translate", "t", "rotate", "r", "scale", "s",
                "translateX", "tx", "translateY", "ty", "translateZ", "tz",
                "rotateX", "rx", "rotateY", "ry", "rotateZ", "rz",
                "scaleX", "sx", "scaleY", "sy", "scaleZ", "sz",
                "normal",
            }
            any_channel_flag = any(k in kwargs for k in channel_keys)

            if not any_channel_flag:
                # No explicit channels → freeze all (matches Maya's default
                # ``makeIdentity -apply true`` behaviour).
                axes_to_freeze.update(["tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"])
            else:
                if kwargs.get("translate") or kwargs.get("t"):
                    axes_to_freeze.update(["tx", "ty", "tz"])
                if kwargs.get("rotate") or kwargs.get("r"):
                    axes_to_freeze.update(["rx", "ry", "rz"])
                if kwargs.get("scale") or kwargs.get("s"):
                    axes_to_freeze.update(["sx", "sy", "sz"])
                # Per-axis flags (rare).
                for ch in (
                    "tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz",
                ):
                    if kwargs.get(ch):
                        axes_to_freeze.add(ch)

        if not axes_to_freeze:
            return

        objects = cmds.ls(as_strings(objects), type="transform", long=True) or []

        strategy = (connection_strategy or "preserve").lower()
        valid_strategies = {"preserve", "disconnect", "delete"}
        if strategy not in valid_strategies:
            raise ValueError(
                f"Invalid connection_strategy '{connection_strategy}'. "
                f"Valid options: {sorted(valid_strategies)}"
            )

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

        freeze_channels: Set[str] = set()
        if not axes_to_freeze.isdisjoint({"tx", "ty", "tz"}):
            freeze_channels.add("translate")
        if not axes_to_freeze.isdisjoint({"rx", "ry", "rz"}):
            freeze_channels.add("rotate")
        if not axes_to_freeze.isdisjoint({"sx", "sy", "sz"}):
            freeze_channels.add("scale")

        skipped_connections: List[Tuple[str, Dict[str, List[str]]]] = []
        instanced_skips: List[str] = []
        frozen_objects: List[str] = []

        def get_blockers(node: str) -> Dict[str, List[str]]:
            """Helper to find input connections on specified channels.

            Returns ``{dest_plug: [src_plug, ...]}``.
            """
            plugs = []
            for ch in freeze_channels:
                if cmds.attributeQuery(ch, node=node, exists=True):
                    plugs.append(f"{node}.{ch}")
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

            shapes = (
                cmds.listRelatives(
                    obj, shapes=True, noIntermediate=False, fullPath=True
                )
                or []
            )
            if shapes:
                try:
                    if NodeUtils.get_instances(obj):
                        instanced_skips.append(short_name(obj))
                        continue
                except Exception:
                    pass

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

                    if cls._apply_freeze_deltas(obj, axes_to_freeze):
                        frozen_objects.append(short_name(obj))

                except RuntimeError as exc:
                    msg = str(exc).lower()
                    if "incoming connection" in msg or "locked" in msg:
                        blockers = get_blockers(obj)

                        if not blockers and "locked" not in msg:
                            skipped_connections.append((short_name(obj), {}))
                            cmds.warning(
                                f"XformUtils.freeze_transforms: Skipping '{obj}' due to connection error: {exc}"
                            )
                            continue

                        if strategy == "preserve":
                            skipped_connections.append((short_name(obj), blockers))
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
                            if cls._apply_freeze_deltas(obj, axes_to_freeze):
                                frozen_objects.append(short_name(obj))
                        except RuntimeError as retry_exc:
                            skipped_connections.append((short_name(obj), blockers))
                            cmds.warning(
                                f"XformUtils.freeze_transforms: Skipping '{obj}' after clearing connections: {retry_exc}"
                            )

                    else:
                        raise

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
    ) -> None:
        """Freeze transforms into offsetParentMatrix while preserving pivot placement."""
        transforms = cmds.ls(as_strings(objects), type="transform", flatten=True) or []
        if not transforms:
            return

        identity_matrix = om.MMatrix()

        for obj in transforms:
            if not cmds.objExists(obj):
                continue

            with Attributes.temporarily_unlock([obj]):
                rotate_pivot_ws = cmds.xform(obj, q=True, ws=True, rp=True)
                scale_pivot_ws = cmds.xform(obj, q=True, ws=True, sp=True)

                rotate_pivot_translate = (
                    cmds.getAttr(f"{obj}.rotatePivotTranslate")[0]
                    if cmds.attributeQuery("rotatePivotTranslate", node=obj, exists=True)
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
                    _set_matrix_plug(f"{temp}.offsetParentMatrix", identity_matrix)
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
                _set_matrix_plug(f"{obj}.offsetParentMatrix", opm_matrix)

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

        nodes = cmds.ls(as_strings(objects), type="transform", long=True) or []
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
                    loc_xform_list = cmds.listRelatives(
                        shape, parent=True, fullPath=True
                    ) or []
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
                        f"XformUtils.unfreeze_to_parent: '{short_name(child)}' "
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
                set_object_matrix(parent, parent_new, world=False)
                set_object_matrix(child, identity_matrix, world=False)

            modified_parents.append(short_name(parent))

        if modified_parents:
            print(
                "XformUtils.unfreeze_to_parent: "
                f"{len(modified_parents)} parent(s) updated."
            )

        return modified_parents

    @staticmethod
    @CoreUtils.undoable
    def restore_transforms(
        objects, prefix="original", delete_attrs=True, channels=None
    ):
        """Compose stored bake history with current local TRS, per channel.

        For each channel C in *channels*:

            new local C = stored bake C  *  current local C

        (vector addition for T, quaternion composition for R, component-
        wise multiplication for S).  Channels not in *channels* keep their
        current local value.  Geometry is shifted so visual world position
        is preserved across the operation.

        Counterpart of ``store_transforms`` under the cumulative
        freeze/unfreeze contract — repeated freeze + transform + unfreeze
        cycles compose, never snap back.

        Robustness:
            * Temporarily unlocks T/R/S channels before writing.
            * Skips referenced nodes with a warning.
            * Skips nodes with no stored bake attributes with a warning.
            * Vectorizes per-vertex updates via the OpenMaya 2.0 API.

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
        full_restore = target_channels == valid_channels

        t_attr, r_attr, s_attr = _bake_attr_names(prefix)
        restored = []

        for obj in cmds.ls(as_strings(objects), type="transform") or []:
            has_t = cmds.attributeQuery(t_attr, node=obj, exists=True)
            has_r = cmds.attributeQuery(r_attr, node=obj, exists=True)
            has_s = cmds.attributeQuery(s_attr, node=obj, exists=True)
            if not (has_t or has_r or has_s):
                cmds.warning(
                    f"restore_transforms: '{obj}' has no stored bake history. Skipping."
                )
                continue

            try:
                if cmds.referenceQuery(obj, isNodeReferenced=True):
                    cmds.warning(
                        f"restore_transforms: '{obj}' is a referenced node "
                        "(can't modify). Skipping."
                    )
                    continue
            except Exception:
                pass

            local_current = om.MMatrix(
                cmds.xform(obj, q=True, matrix=True, objectSpace=True)
            )
            world_current = om.MMatrix(
                cmds.xform(obj, q=True, matrix=True, worldSpace=True)
            )
            cur_t, cur_r, cur_s = _decompose_local(obj)

            # Compose stored bake history with the current local TRS per
            # channel.  Channels not in target_channels stay at current.
            if "translate" in target_channels and has_t:
                stored_t = _read_bake_t(obj, t_attr)
                target_t = stored_t + cur_t
            else:
                target_t = cur_t

            if "rotate" in target_channels and has_r:
                stored_r = _read_bake_r(obj, r_attr)
                target_r = stored_r * cur_r
            else:
                target_r = cur_r

            if "scale" in target_channels and has_s:
                stored_s = _read_bake_s(obj, s_attr)
                target_s = [stored_s[i] * cur_s[i] for i in range(3)]
            else:
                target_s = cur_s

            # The new clean local matrix is just T * R * S with zero
            # pivots and zero pivot translates — that's the state the
            # user expects after unfreeze.
            new_local = _compose_local(target_t, target_r, target_s)

            # In Maya's row-vector convention: world = local * parent.
            # Recover parent_world from the current pair so the new world
            # matrix can be derived for the geometry shift.
            try:
                parent_world = local_current.inverse() * world_current
            except Exception:
                parent_world = om.MMatrix()
            new_world = new_local * parent_world

            try:
                inverse_new_world = new_world.inverse()
            except Exception:
                cmds.warning(
                    f"restore_transforms: '{obj}' has singular target matrix. Skipping."
                )
                continue

            shapes = (
                cmds.listRelatives(
                    obj, shapes=True, noIntermediate=True, fullPath=True
                )
                or []
            )

            # Shape shift preserves visual world position once we write
            # the new world matrix (built from clean channel values, no
            # pivot offsets).
            for shape in shapes:
                _shift_shape_points(shape, inverse_new_world)

            # Set channels directly so Maya doesn't fold lingering
            # ``rotatePivotTranslate`` / ``scalePivotTranslate`` (left by
            # ``makeIdentity``) into the new translate values.
            _apply_clean_local(obj, target_t, target_r, target_s)

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
        attr_names = _bake_attr_names(prefix)
        for obj in cmds.ls(as_strings(objects), type="transform") or []:
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

    @staticmethod
    def has_stored_transforms(objects, prefix="original"):
        """Check if objects have any stored bake history.

        Returns:
            dict: Mapping of object short names to bool (True if any
            T/R/S bake attribute exists).
        """
        result = {}
        attr_names = _bake_attr_names(prefix)
        for obj in cmds.ls(as_strings(objects), type="transform") or []:
            has_stored = any(
                cmds.attributeQuery(attr, node=obj, exists=True)
                for attr in attr_names
            )
            short = obj.split("|")[-1].split(":")[-1]
            result[short] = has_stored
        return result

    @classmethod
    @CoreUtils.undoable
    def reset_translation(cls, objects):
        """Reset the translation transformations on the given object(s)."""
        for obj in cmds.ls(as_strings(objects)) or []:
            pos = cmds.objectCenter(obj)
            cls.drop_to_grid(obj, origin=True, center_pivot=True)
            cmds.makeIdentity(obj, apply=True, t=True, r=False, s=False, n=False, pn=True)
            cmds.xform(obj, translation=pos)

    @staticmethod
    def set_translation_to_pivot(node):
        """Set an object's translation value from its pivot location."""
        node = str(node)
        x, y, z = cmds.xform(node, query=True, worldSpace=True, rotatePivot=True)
        cmds.xform(node, relative=True, translation=[-x, -y, -z])
        cmds.makeIdentity(node, apply=True, translate=True)
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

            if is_diff(manip_pivot_pos, rp_pos):
                cls._manip_cache[node] = (manip_pivot_rot, manip_pivot_pos)
            else:
                if node in cls._manip_cache:
                    del cls._manip_cache[node]

        except Exception:
            pass

    @classmethod
    def get_operation_axis_matrix(cls, node, pivot: str):
        """Determines the pivot matrix (orientation + position) for transformations.

        Returns:
            om.MMatrix: The 4x4 transfomation matrix.
        """
        pos = cls.get_operation_axis_pos(node, pivot)
        mat_pos_list = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            pos[0], pos[1], pos[2], 1.0,
        ]
        mat_pos = om.MMatrix(mat_pos_list)

        mat_rot = om.MMatrix.kIdentity

        if pivot == "object":
            m_obj_arr = cmds.xform(node, query=True, worldSpace=True, matrix=True)
            m_obj = om.MMatrix(m_obj_arr)
            tm_obj = om.MTransformationMatrix(m_obj)
            mat_rot = tm_obj.rotation().asMatrix()

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

                if is_diff(manip_pos, rp_pos):
                    cls._manip_cache[node] = (manip_rot_deg, manip_pos)
                elif node in cls._manip_cache:
                    cached_vals = cls._manip_cache[node]
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
                is_default_origin = (
                    queried_pos is not None
                    and all(abs(v) < 1e-6 for v in queried_pos)
                )

                if queried_pos is not None and not is_default_origin:
                    manip_pivot_ws = queried_pos
                elif node in cls._manip_cache:
                    # Manip is at default state but we previously cached a
                    # custom position for this node — restore it.
                    _cached_rot, cached_pos = cls._manip_cache[node]
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

            return float(manip_pivot_ws[axis_index]) if axis_index is not None else manip_pivot_ws

        if pivot == "object":
            obj_pivot_ws = cmds.xform(node, q=True, ws=True, rp=True)
            return (
                float(obj_pivot_ws[axis_index])
                if axis_index is not None
                else obj_pivot_ws
            )

        if pivot == "baked":
            local_rp = cmds.xform(node, q=True, rp=True, os=True)
            world_matrix = get_object_matrix(node, world=True)
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
        align_from = as_strings(align_from)
        align_to = as_strings(align_to)
        pos = cmds.xform(align_to, q=True, translation=True, worldSpace=True)
        center_pos = [
            sum(pos[0::3]) / len(pos[0::3]),
            sum(pos[1::3]) / len(pos[1::3]),
            sum(pos[2::3]) / len(pos[2::3]),
        ]

        vertices = cmds.ls(
            cmds.polyListComponentConversion(align_to, toVertex=True), flatten=True
        ) or []
        if len(vertices) < 3:
            return

        for obj in cmds.ls(as_strings(align_from), flatten=True) or []:
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
            objs = cmds.ls(as_strings(objects), type="transform", flatten=True) or []

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
    def world_align_pivot(
        objects=None,
        pivot_type: str = "object",
        mode: str = "set",
    ):
        """Get or set a world-aligned pivot for the specified objects."""
        if objects is None:
            selected = cmds.ls(selection=True) or []
            if not selected:
                cmds.warning("No objects specified and nothing is selected.")
                return False if mode == "set" else None
            objects = selected
        else:
            objects = cmds.ls(as_strings(objects), flatten=True) or []

        if not objects:
            cmds.warning("No valid objects found.")
            return False if mode == "set" else None

        original_selection = cmds.ls(selection=True) or []
        cmds.select(objects, replace=True)

        pivot_positions = [
            cmds.xform(obj, q=True, rotatePivot=True, worldSpace=True) for obj in objects
        ]
        avg_pivot_pos = [sum(coords) / len(coords) for coords in zip(*pivot_positions)]

        if mode == "get":
            result = {
                "position": avg_pivot_pos,
                "orientation": [0, 0, 0],
                "objects": [str(obj) for obj in objects],
            }
            if original_selection:
                cmds.select(original_selection, replace=True)
            return result

        if mode == "set":
            if pivot_type == "manip":
                cmds.manipPivot(p=avg_pivot_pos, o=(0, 0, 0))
                return True

            if pivot_type == "object":
                for obj in objects:
                    pivot_pos = cmds.xform(
                        obj, q=True, rotatePivot=True, worldSpace=True
                    )
                    cmds.xform(obj, worldSpace=True, pivots=pivot_pos, preserve=True)
                    # See ``reset_pivot_transforms`` for why this differs
                    # from the legacy overload.
                    try:
                        cmds.manipPivot(p=pivot_pos, o=(0.0, 0.0, 0.0))
                    except Exception:
                        pass
                    cmds.xform(obj, preserve=True, rotateAxis=(0, 0, 0))

                if original_selection:
                    cmds.select(original_selection, replace=True)
                return True

            cmds.warning(f"Invalid pivot_type: {pivot_type}. Use 'manip' or 'object'.")
            if original_selection:
                cmds.select(original_selection, replace=True)
            return False

        cmds.warning(f"Invalid mode: {mode}. Use 'get' or 'set'.")
        if original_selection:
            cmds.select(original_selection, replace=True)
        return False

    @staticmethod
    @CoreUtils.undoable
    def bake_pivot(objects, position=False, orientation=False):
        """Bake the pivot orientation and position of the given object(s)."""
        objects = as_strings(objects)
        transforms = cmds.ls(objects, transforms=True) or []
        shapes = cmds.ls(objects, shapes=True) or []
        objects = transforms + (
            cmds.listRelatives(shapes, path=True, parent=True, type="transform") or []
        )

        ctx = cmds.currentCtx()
        pivotModeActive = 0
        customModeActive = 0
        if ctx in ("RotateSuperContext", "manipRotateContext"):
            customOri = cmds.manipRotateContext("Rotate", q=True, orientAxes=True)
            pivotModeActive = cmds.manipRotateContext("Rotate", q=True, editPivotMode=True)
            customModeActive = cmds.manipRotateContext("Rotate", q=True, mode=True) == 3
        elif ctx in ("scaleSuperContext", "manipScaleContext"):
            customOri = cmds.manipScaleContext("Scale", q=True, orientAxes=True)
            pivotModeActive = cmds.manipScaleContext("Scale", q=True, editPivotMode=True)
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

            cmds.rotate(cX, cY, cZ, objects, a=True, pcp=True, pgp=True, ws=True, fo=True)

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
    @CoreUtils.undoable
    def transfer_pivot(
        objects,
        translate: bool = False,
        rotate: bool = False,
        scale: bool = False,
        bake: bool = False,
        world_space: bool = True,
        select_targets_after_transfer: bool = False,
    ):
        """Transfer the pivot orientation from the first given object to the remaining given objects."""
        objects = cmds.ls(as_strings(objects), type="transform") or []
        if not objects or len(objects) < 2:
            cmds.warning("At least two objects are required to transfer pivot.")
            return

        source = objects[0]
        targets = objects[1:]

        for target in targets:
            if translate:
                rp = cmds.xform(source, q=True, ws=world_space, rp=True)
                cmds.xform(target, ws=world_space, rp=rp)
                if scale:
                    sp = cmds.xform(source, q=True, ws=world_space, sp=True)
                    cmds.xform(target, ws=world_space, sp=sp)
            elif scale:
                sp = cmds.xform(source, q=True, ws=world_space, sp=True)
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
                        cmds.parent(children, world=True)

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
                                        cmds.pointPosition(
                                            f"{sh}.vtx[{i}]", world=True
                                        )
                                    )
                                shape_points[sh] = pts
                        except Exception:
                            pass

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
                        m = om.MMatrix(
                            cmds.xform(target, q=True, matrix=True, os=True)
                        )
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
                        except Exception:
                            pass
                    for sh, pts in shape_points.items():
                        try:
                            for i, p in enumerate(pts):
                                cmds.xform(f"{sh}.vtx[{i}]", ws=True, t=p)
                        except Exception:
                            pass

                else:
                    source_ra = cmds.xform(source, q=True, ra=True)
                    cmds.xform(target, ra=source_ra)

            if bake:
                cmds.makeIdentity(
                    target, apply=True, t=translate, r=rotate, s=scale, n=False, pn=True
                )

        if select_targets_after_transfer:
            cmds.select(targets, replace=True)

    @staticmethod
    @CoreUtils.undoable
    def aim_object_at_point(objects, target_pos, aim_vect=(1, 0, 0), up_vect=(0, 1, 0)):
        """Aim the given object(s) at the given world space position."""
        created_target = False
        if isinstance(target_pos, (tuple, set, list)):
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
        for obj in cmds.ls(as_strings(objects), type="transform") or []:
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
        for obj in cmds.ls(as_strings(objects), objectsOnly=True) or []:
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
        objects = cmds.ls(as_strings(objects), flatten=True) or []
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

        objs = (
            list(objects)
            if isinstance(objects, (list, tuple))
            else [objects]
        )
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
        for obj in cmds.ls(as_strings(objects), flatten=False) or []:
            v = cls.get_bounding_box(obj, value)
            valueAndObjs.append((v, obj))

        sorted_ = sorted(valueAndObjs, key=lambda x: int(x[0]), reverse=descending)
        if also_return_value:
            return sorted_
        return [obj for v, obj in sorted_]

    @staticmethod
    @CoreUtils.undoable
    def align_using_three_points(vertices):
        """Move and align the object defined by the first 3 points to the last 3 points."""
        vertices = cmds.ls(as_strings(vertices), flatten=True) or []
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

        p0, p1, p2 = [om.MVector(*cmds.pointPosition(v, world=True)) for v in vertices[0:3]]
        p3, p4, p5 = [om.MVector(*cmds.pointPosition(v, world=True)) for v in vertices[3:6]]

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
                src_x.x, src_x.y, src_x.z, 0,
                src_y.x, src_y.y, src_y.z, 0,
                src_z.x, src_z.y, src_z.z, 0,
                p0.x, p0.y, p0.z, 1,
            ]
        )
        tgt_mat = om.MMatrix(
            [
                tgt_x.x, tgt_x.y, tgt_x.z, 0,
                tgt_y.x, tgt_y.y, tgt_y.z, 0,
                tgt_z.x, tgt_z.y, tgt_z.z, 0,
                p3.x, p3.y, p3.z, 1,
            ]
        )

        delta = src_mat.inverse() * tgt_mat

        current_mat = om.MMatrix(
            cmds.xform(object_to_move[0], q=True, matrix=True, worldSpace=True)
        )
        new_mat = current_mat * delta
        cmds.xform(
            object_to_move[0],
            matrix=_mmatrix_to_flat(new_mat),
            worldSpace=True,
        )

    @staticmethod
    def is_overlapping(a, b, tolerance=0.001):
        """Check if the vertices in a and b are overlapping within the given tolerance."""
        vert_setA = cmds.ls(
            cmds.polyListComponentConversion(a, toVertex=True), flatten=True
        ) or []
        vert_setB = cmds.ls(
            cmds.polyListComponentConversion(b, toVertex=True), flatten=True
        ) or []

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

            for idx, point in enumerate(points):
                transformed_point = point * world_matrix
                distance = (transformed_point - plane_point) * plane_normal

                if distance < 0:
                    if return_type == "bool":
                        objects_below_threshold.append((obj, True))
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

            if return_type == "bool" and not objects_below_threshold:
                objects_below_threshold.append((obj, False))

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
        hash_a, hash_b = ptk.hash_points([vert_pos_a, vert_pos_b])

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

        for obj in cmds.ls(as_strings(objects), flatten=True) or []:
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


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
