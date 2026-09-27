# !/usr/bin/python
# coding=utf-8
"""Measuring and placing objects behind :class:`mayatk.XformUtils`.

Bounding boxes, centers, distances and orientation; moving, dropping, scaling
and aiming objects; three-point and vertex alignment; overlap and plane checks;
and the local/world matrix accessors. Reached through
:class:`mayatk.XformUtils`; nothing here is called directly.
"""

from __future__ import annotations


try:
    import maya.cmds as cmds
    import maya.mel as mel
    from maya.api import OpenMaya as om
except Exception:
    cmds = mel = om = None

import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.components import Components
from mayatk.node_utils._node_utils import NodeUtils


class _PlacementInternal:
    """Private helpers and ``XformUtils`` method bodies; see the module docstring."""

    @staticmethod
    def _convert_axis(value, invert, ortho, to_integer):
        """Body of :meth:`XformUtils.convert_axis`."""
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
    def _move_to(cls, source, target, pivot, group_move):
        """Body of :meth:`XformUtils.move_to`."""
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
    def _drop_to_grid(cls, objects, align, origin, center_pivot, freeze_transforms):
        """Body of :meth:`XformUtils.drop_to_grid`."""
        targets = (
            cmds.ls(CoreUtils.as_strings(objects), transforms=True, long=True) or []
        )
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
    def _match_scale(cls, a, b, scale, average):
        """Body of :meth:`XformUtils.match_scale`."""
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
    def _scale_connected_edges(objects, scale_factor):
        """Body of :meth:`XformUtils.scale_connected_edges`."""
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

    @classmethod
    def _reset_translation(cls, objects):
        """Body of :meth:`XformUtils.reset_translation`."""
        for obj in cmds.ls(CoreUtils.as_strings(objects), long=True) or []:
            pos = cmds.objectCenter(obj)
            cls.drop_to_grid(obj, origin=True, center_pivot=True)
            # Engine path (store=True): the translate bake is reversible.
            cls.freeze_transforms(obj, translate=True, force=True)
            cmds.xform(obj, translation=pos)

    @classmethod
    def _set_translation_to_pivot(cls, node):
        """Body of :meth:`XformUtils.set_translation_to_pivot`."""
        node = str(node)
        x, y, z = cmds.xform(node, query=True, worldSpace=True, rotatePivot=True)
        cmds.xform(node, relative=True, translation=[-x, -y, -z])
        cls.freeze_transforms(node, translate=True, force=True)
        cmds.xform(node, translation=[x, y, z])

    @staticmethod
    def _aim_object_at_point(objects, target_pos, aim_vect, up_vect):
        """Body of :meth:`XformUtils.aim_object_at_point`."""
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
    def _orient_to_vector(transform, aim_vector, up_vector):
        """Body of :meth:`XformUtils.orient_to_vector`."""
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
    def _rotate_axis(cls, objects, target_pos):
        """Body of :meth:`XformUtils.rotate_axis`."""
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
    def _get_orientation(objects, returned_type):
        """Body of :meth:`XformUtils.get_orientation`."""
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
    def _get_dist_between_two_objects(a, b):
        """Body of :meth:`XformUtils.get_dist_between_two_objects`."""
        x1, y1, z1 = cmds.objectCenter(a)
        x2, y2, z2 = cmds.objectCenter(b)

        from math import sqrt

        return sqrt(pow((x1 - x2), 2) + pow((y1 - y2), 2) + pow((z1 - z2), 2))

    @staticmethod
    def _get_center_point(objects):
        """Body of :meth:`XformUtils.get_center_point`."""
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
    def _get_bounding_box(objects, value, world_space, return_valid_keys):
        """Body of :meth:`XformUtils.get_bounding_box`."""
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
        if world_space:
            bbox = cmds.exactWorldBoundingBox(objs)
        elif len(objs) > 1:
            # Object space is a single node's OWN frame; several nodes have no
            # shared one, and the old query silently answered with a combined
            # WORLD box -- exactly the wrong-and-quiet failure this call had.
            raise ValueError(
                "world_space=False takes ONE object (its own frame); got "
                f"{len(objs)}. Query them individually, or ask in world space."
            )
        else:
            # Delegated so the two public bounding-box entry points cannot
            # disagree about what object space means; see CoreUtils for why it
            # is constructed from shape attributes rather than queried.
            box = CoreUtils.get_bounding_box(objs[0], world=False)
            bbox = [*box.min, *box.max]

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

        if not value:
            # An empty default that always raised was a trap: "the bounding
            # box" is the obvious meaning, so return the six corners.
            return (xmin, ymin, zmin, xmax, ymax, zmax)

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
    def _sort_by_bounding_box_value(cls, objects, value, descending, also_return_value):
        """Body of :meth:`XformUtils.sort_by_bounding_box_value`."""
        valueAndObjs = []
        for obj in cmds.ls(CoreUtils.as_strings(objects), flatten=False) or []:
            v = cls.get_bounding_box(obj, value)
            valueAndObjs.append((v, obj))

        sorted_ = sorted(valueAndObjs, key=lambda x: x[0], reverse=descending)
        if also_return_value:
            return sorted_
        return [obj for v, obj in sorted_]

    @classmethod
    def _align_using_three_points(cls, vertices):
        """Body of :meth:`XformUtils.align_using_three_points`."""
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
            matrix=cls._mmatrix_to_flat(new_mat),
            worldSpace=True,
        )

    @staticmethod
    def _is_overlapping(a, b, tolerance):
        """Body of :meth:`XformUtils.is_overlapping`."""
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
    def _check_objects_against_plane(objects, plane_point, plane_normal, return_type):
        """Body of :meth:`XformUtils.check_objects_against_plane`."""
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
    def _get_vertex_positions(objects, worldSpace):
        """Body of :meth:`XformUtils.get_vertex_positions`."""
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
    def _get_matching_verts(cls, a, b, world_space):
        """Body of :meth:`XformUtils.get_matching_verts`."""
        vert_pos_a, vert_pos_b = cls.get_vertex_positions([a, b], world_space)
        hash_a, hash_b = ptk.PointCloud.hash_points([vert_pos_a, vert_pos_b])

        matching = set(hash_a).intersection(hash_b)
        return [
            i
            for h in matching
            for i in zip(ptk.indices(hash_a, h), ptk.indices(hash_b, h))
        ]

    @classmethod
    def _order_by_distance(cls, objects, reference_point, reverse):
        """Body of :meth:`XformUtils.order_by_distance`."""
        if reference_point is None:
            reference_point = [0, 0, 0]

        distance_object_pairs = []

        for obj in (
            cmds.ls(CoreUtils.as_strings(objects), flatten=True, long=True) or []
        ):
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
    def _align_vertices(mode, average, edgeloop):
        """Body of :meth:`XformUtils.align_vertices`."""
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
    def _get_translation(node, world):
        """Body of :meth:`XformUtils.get_translation`."""
        flag = {"ws": True} if world else {"os": True}
        t = cmds.xform(str(node), q=True, t=True, **flag)
        return om.MVector(*t)

    @staticmethod
    def _get_object_matrix(node, world):
        """Body of :meth:`XformUtils.get_object_matrix`."""
        flag = {"ws": True} if world else {"os": True}
        flat = cmds.xform(str(node), q=True, m=True, **flag)
        return om.MMatrix(flat)

    @staticmethod
    def _set_object_matrix(node, value, world):
        """Body of :meth:`XformUtils.set_object_matrix`."""
        if hasattr(value, "getElement"):
            flat = [value.getElement(r, c) for r in range(4) for c in range(4)]
        else:
            flat = list(value)
        if len(flat) != 16:
            raise ValueError(f"set_object_matrix expected 16 elements, got {len(flat)}")
        flag = {"worldSpace": True} if world else {"objectSpace": True}
        cmds.xform(str(node), matrix=flat, **flag)
