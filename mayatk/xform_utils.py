# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk import core_utils, component_utils


class XformUtils:
    """ """

    @staticmethod
    def move_to(source, target, center=True):
        """Move an object(s) to the given target.

        Parameters:
            source (str/obj/list): The objects to move.
            target (str/obj): The object to move to.
            center (bool): Move to target pivot pos, or the bounding box center of the target.
        """
        if center:  # temporarily move the targets pivot to it's bounding box center.
            orig_target_piv = pm.xform(
                target, q=1, worldSpace=1, rp=1
            )  # get target pivot position.
            pm.xform(target, centerPivots=1)  # center target pivot.
            target_pos = pm.xform(
                target, q=1, worldSpace=1, rp=1
            )  # get the pivot position at center of object.
            pm.xform(
                target, worldSpace=1, rp=orig_target_piv
            )  # return the target pivot to it's orig position.
        else:
            target_pos = pm.xform(
                target, q=1, worldSpace=1, rp=1
            )  # get the pivot position.

        pm.xform(source, translation=target_pos, worldSpace=1, relative=1)

    @staticmethod
    @core_utils.CoreUtils.undo
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

        Example:
            drop_to_grid(obj, align='Min') #set the object onto the grid.
        """
        # pm.undoInfo(openChunk=1)
        for obj in pm.ls(objects, transforms=1):
            osPivot = pm.xform(
                obj, q=True, rotatePivot=1, objectSpace=1
            )  # save the object space obj pivot.
            wsPivot = pm.xform(
                obj, q=True, rotatePivot=1, worldSpace=1
            )  # save the world space obj pivot.

            pm.xform(obj, centerPivots=1)  # center pivot
            plane = pm.polyPlane(name="temp#")

            if not origin:
                pm.xform(
                    plane, translation=(wsPivot[0], 0, wsPivot[2]), absolute=1, ws=1
                )  # move the object to the pivot location

            pm.align(obj, plane, atl=1, x="Mid", y=align, z="Mid")
            pm.delete(plane)

            if not center_pivot:
                pm.xform(
                    obj, rotatePivot=osPivot, objectSpace=1
                )  # return pivot to orig position.

            if freeze_transforms:
                pm.makeIdentity(obj, apply=True)
        # pm.undoInfo (closeChunk=1)

    @classmethod
    @core_utils.CoreUtils.undo
    def reset_translation(cls, objects):
        """Reset the translation transformations on the given object(s).

        Parameters:
            objects (str/obj/list): The object(s) to reset the translation values for.
        """
        # pm.undoInfo(openChunk=1)
        for obj in pm.ls(objects):
            pos = pm.objectCenter(obj)  # get the object's current position.
            cls.drop_to_grid(
                obj, origin=1, center_pivot=1
            )  # move to origin and center pivot.
            pm.makeIdentity(obj, apply=1, t=1, r=0, s=0, n=0, pn=1)  # bake transforms
            pm.xform(
                obj, translation=pos
            )  # move the object back to it's original position.
        # pm.undoInfo(closeChunk=1)

    @staticmethod
    def set_translation_to_pivot(node):
        """Set an object’s translation value from its pivot location.

        Parameters:
                node (str/obj/list): An object, or it's name.
        """
        x, y, z = pm.xform(node, query=True, worldSpace=True, rotatePivot=True)
        pm.xform(node, relative=True, translation=[-x, -y, -z])
        pm.makeIdentity(node, apply=True, translate=True)
        pm.xform(node, translation=[x, y, z])

    @staticmethod
    @core_utils.CoreUtils.undo
    def align_pivot_to_selection(align_from=[], align_to=[], translate=True):
        """Align one objects pivot point to another using 3 point align.

        Parameters:
            align_from (list): At minimum; 1 object, 1 Face, 2 Edges, or 3 Vertices.
            align_to (list): The object to align with.
            translate (bool): Move the object with it's pivot.
        """
        # pm.undoInfo(openChunk=1)
        pos = pm.xform(align_to, q=1, translation=True, worldSpace=True)
        center_pos = [  # Get center by averaging of all x,y,z points.
            sum(pos[0::3]) / len(pos[0::3]),
            sum(pos[1::3]) / len(pos[1::3]),
            sum(pos[2::3]) / len(pos[2::3]),
        ]

        vertices = pm.ls(
            pm.polyListComponentConversion(align_to, toVertex=True), flatten=True
        )
        if len(vertices) < 3:
            return

        for obj in pm.ls(align_from, flatten=1):
            plane = pm.polyPlane(
                name="_hptemp#",
                width=1,
                height=1,
                subdivisionsX=1,
                subdivisionsY=1,
                axis=[0, 1, 0],
                createUVs=2,
                constructionHistory=True,
            )[
                0
            ]  # Create and align helper plane.

            pm.select("%s.vtx[0:2]" % plane, vertices[0:3])
            pm.mel.snap3PointsTo3Points(0)

            pm.xform(
                obj,
                rotation=pm.xform(plane, q=True, rotation=True, worldSpace=True),
                worldSpace=True,
            )

            if translate:
                pm.xform(obj, translation=center_pos, worldSpace=True)

            pm.delete(plane)
        # pm.undoInfo(closeChunk=1)

    @staticmethod
    @core_utils.CoreUtils.undo
    def aim_object_at_point(objects, target_pos, aim_vect=(1, 0, 0), up_vect=(0, 1, 0)):
        """Aim the given object(s) at the given world space position.

        Parameters:
            objects (str/obj/list): Transform node(s) of the objects to orient.
            target_pos (obj)(tuple): A point as xyz, or one or more transform nodes at which to aim the other given 'objects'.
            aim_vect (tuple): The vector in local coordinates that points at the target.
            up_vect (tuple): The vector in local coordinates that aligns with the world up vector.

        Example:
            aim_object_at_point(['cube1', 'cube2'], (0, 15, 15))
        """
        if isinstance(target_pos, (tuple, set, list)):
            target = pm.createNode("transform", name="target_helper")
            pm.xform(target, translation=target_pos, absolute=True)
        else:
            target = target_pos  # Assume it's an existing object's name

        for obj in ptk.make_iterable(objects):
            const = pm.aimConstraint(
                target, obj, aim=aim_vect, worldUpVector=up_vect, worldUpType="vector"
            )

        pm.delete(const, target)

    @classmethod
    @core_utils.CoreUtils.undo
    def rotate_axis(cls, objects, target_pos):
        """Aim the given object at the given world space position.
        All rotations in rotated channel, geometry is transformed so
        it does not appear to move during this transformation

        Parameters:
            objects (str/obj/list): Transform node(s) of the objects to orient.
            target_pos (obj)(tuple): A point as xyz, or one or more transform nodes at which to aim the other given 'objects'.
        """
        for obj in pm.ls(objects, type="transform"):
            cls.aim_object_at_point(obj, target_pos)

            try:
                c = obj.verts
            except TypeError:
                c = obj.cp

            wim = pm.getAttr(obj.worldInverseMatrix)
            pm.xform(c, matrix=wim)

            pos = pm.xform(
                obj, q=True, translation=True, absolute=True, worldSpace=True
            )
            pm.xform(c, translation=pos, relative=True, worldSpace=True)

    @staticmethod
    def get_orientation(objects, returned_type="point"):
        """Get an objects orientation as a point or vector.

        Parameters:
            objects (str/obj/list): The object(s) to get the orientation of.
            returned_type (str): The desired returned value type. (valid: 'point'(default), 'vector')

        Returns:
            (tuple)(list) If 'objects' given as a list, a list of tuples will be returned.
        """
        result = []
        for obj in pm.ls(objects, objectsOnly=True):
            world_matrix = pm.xform(obj, q=True, matrix=True, worldSpace=True)
            rAxis = pm.getAttr(obj.rotateAxis)
            if any((rAxis[0], rAxis[1], rAxis[2])):
                print(
                    f"# Warning: {obj} has a modified .rotateAxis of {rAxis} which is included in the result. #"
                )

            if returned_type == "vector":
                from maya.api.OpenMaya import MVector

                ori = (
                    MVector(world_matrix[0:3]),
                    MVector(world_matrix[4:7]),
                    MVector(world_matrix[8:11]),
                )

            else:
                ori = (world_matrix[0:3], world_matrix[4:7], world_matrix[8:11])
            result.append(ori)

        return ptk.format_return(result, objects)

    @staticmethod
    def get_dist_between_two_objects(a, b):
        """Get the magnatude of a vector using the center points of two given objects.

        Parameters:
            a (obj)(str): Object, object name, or point (x,y,z).
            b (obj)(str): Object, object name, or point (x,y,z).

        Returns:
            (float)
        """
        x1, y1, z1 = pm.objectCenter(a)
        x2, y2, z2 = pm.objectCenter(b)

        from math import sqrt

        distance = sqrt(pow((x1 - x2), 2) + pow((y1 - y2), 2) + pow((z1 - z2), 2))

        return distance

    @staticmethod
    def get_center_point(objects):
        """Get the bounding box center point of any given object(s).

        Parameters:
            objects (str)(obj(list): The objects or components to get the center of.

        Returns:
            (tuple) position as xyz float values.
        """
        objects = pm.ls(objects, flatten=True)
        pos = [
            i
            for sublist in [
                pm.xform(s, q=1, translation=1, worldSpace=1, absolute=1)
                for s in objects
            ]
            for i in sublist
        ]
        center_pos = (  # Get center by averaging of all x,y,z points.
            sum(pos[0::3]) / len(pos[0::3]),
            sum(pos[1::3]) / len(pos[1::3]),
            sum(pos[2::3]) / len(pos[2::3]),
        )
        return center_pos

    @classmethod
    def get_bounding_box(cls, objects, value=""):
        """Get information of the given object(s) combined bounding box.

        Parameters:
            objects (str/obj/list): The object(s) or component(s) to query.
                    Multiple objects will be treated as a combined bounding box.
            value (str): The type of value to return. Multiple types can be given
                    separated by '|'. The order given determines the return order.
                    valid (case insensitive): 'xmin', 'xmax', 'ymin', 'ymax', 'zmin', 'zmax', 'size',
                    'x' or 'sizex', 'y' or 'sizey', 'z' or 'sizez', 'volume', 'center' or 'centroid'
        Returns:
            (float)(tuple) Dependant on args.

        Example:
            get_bounding_box(sel, 'center|volume') #returns: [[171.9106216430664, 93.622802734375, -1308.4896240234375], 743.2855185396038]
            get_bounding_box(sel, 'sizeY') #returns: 144.71902465820312
        """
        if "|" in value:  # use recursion to construct the list using each value.
            return tuple(cls.get_bounding_box(objects, i) for i in value.split("|"))

        v = value.lower()
        xmin, ymin, zmin, xmax, ymax, zmax = pm.exactWorldBoundingBox(objects)
        if v == "xmin":
            return xmin
        elif v == "xmax":
            return xmax
        elif v == "ymin":
            return ymin
        elif v == "ymax":
            return ymax
        elif v == "zmin":
            return zmin
        elif v == "zmax":
            return zmax
        elif v == "size":
            return (xmax - xmin, ymax - ymin, zmax - zmin)
        elif v == "sizex" or v == "x":
            return xmax - xmin
        elif v == "sizey" or v == "y":
            return ymax - ymin
        elif v == "sizez" or v == "z":
            return zmax - zmin
        elif v == "minsize":
            return min(xmax - xmin, ymax - ymin, zmax - zmin)
        elif v == "maxsize":
            return max(xmax - xmin, ymax - ymin, zmax - zmin)
        elif v == "volume":
            return (xmax - xmin) * (ymax - ymin) * (zmax - zmin)
        elif v == "center" or v == "centroid":
            return ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0, (zmin + zmax) / 2.0)
        else:
            (xmin, ymin, zmin, xmax, ymax, zmax)

    @classmethod
    def sort_by_bounding_box_value(
        cls, objects, value="volume", descending=True, also_return_value=False
    ):
        """Sort the given objects by their bounding box value.

        Parameters:
            objects (str/obj/list): The objects or components to sort.
            value (str): See 'getBoundingBoxInfo' 'value' parameter.
                            ex. 'xmin', 'xmax', 'sizex', 'volume', 'center' etc.
            descending (bool): Sort the list from the largest value down.
            also_return_value (bool): Instead of just the object; return a
                            list of two element tuples as [(<value>, <obj>)].
        Returns:
            (list)
        """
        valueAndObjs = []
        for obj in pm.ls(objects, flatten=False):
            v = cls.get_bounding_box(obj, value)
            valueAndObjs.append((v, obj))

        sorted_ = sorted(valueAndObjs, key=lambda x: int(x[0]), reverse=descending)
        if also_return_value:
            return sorted_
        return [obj for v, obj in sorted_]

    @staticmethod
    def match_scale(a, b, scale=True, average=False):
        """Scale each of the given objects to the combined bounding box of a second set of objects.

        Parameters:
            a (str/obj/list): The object(s) to scale.
            b (str/obj/list): The object(s) to get a bounding box size from.
            scale (bool): Scale the objects. Else, just return the scale value.
            average (bool): Average the result across all axes.

        Returns:
            (list) scale values as [x,y,z,x,y,z...]
        """
        to = pm.ls(a, flatten=True)
        frm = pm.ls(b, flatten=True)

        xmin, ymin, zmin, xmax, ymax, zmax = pm.exactWorldBoundingBox(frm)
        ax, ay, az = [xmax - xmin, ymax - ymin, zmax - zmin]

        result = []
        for obj in to:
            xmin, ymin, zmin, xmax, ymax, zmax = pm.exactWorldBoundingBox(obj)
            bx, by, bz = [xmax - xmin, ymax - ymin, zmax - zmin]

            oldx, oldy, oldz = pm.xform(obj, q=1, s=1, r=1)

            try:
                diffx, diffy, diffz = [ax / bx, ay / by, az / bz]
            except ZeroDivisionError:
                diffx, diffy, diffz = [1, 1, 1]

            bScaleNew = [oldx * diffx, oldy * diffy, oldz * diffz]

            if average:
                bScaleNew = [sum(bScaleNew) / len(bScaleNew) for _ in range(3)]

            if scale:
                pm.xform(obj, scale=bScaleNew)

            [result.append(i) for i in bScaleNew]

        return result

    @staticmethod
    def align_using_three_points(vertices):
        """Move and align the object defined by the first 3 points to the last 3 points.

        Parameters:
            vertices (list): The first 3 points must be on the same object (i.e. it is the
                    object to be transformed). The second set of points define
                    the position and plane to transform to.
        """
        from maya import OpenMaya

        vertices = pm.ls(vertices, flatten=True)
        objectToMove = pm.ls(vertices[:3], objectsOnly=True)

        p0, p1, p2 = [
            OpenMaya.MPoint(*pm.pointPosition(v, w=True)) for v in vertices[0:3]
        ]
        p3, p4, p5 = [
            OpenMaya.MPoint(*pm.pointPosition(v, w=True)) for v in vertices[3:6]
        ]

        # Translate
        pm.move(*(p3 - p0), objectToMove, r=True, ws=True)

        # First rotation
        axis1 = (p1 - p0).normal()
        axis2 = (p4 - p3).normal()
        # cross_product = axis1 ^ axis2
        angle = axis1.angle(axis2)
        rotation = OpenMaya.MEulerRotation(0, 0, angle).asVector()
        pm.rotate(*rotation, objectToMove, p=p3, r=True, os=True)

        # Second rotation
        axis3 = (p2 - p0).normal()
        axis4 = (p5 - p3).normal()
        # cross_product = axis3 ^ axis4
        angle = axis3.angle(axis4)
        rotation = OpenMaya.MEulerRotation(0, 0, angle).asVector()
        pm.rotate(*rotation, objectToMove, p=p4, r=True, os=True)

    @staticmethod
    def is_overlapping(a, b, tolerance=0.001):
        """Check if the vertices in a and b are overlapping within the given tolerance.

        Parameters:
            a (str/obj): The first object to check. Object can be a component.
            b (str/obj): The second object to check. Object can be a component.
            tolerance (float) = The maximum search distance before a vertex is considered not overlapping.

        Returns:
            (bool)
        """
        vert_setA = pm.ls(pm.polyListComponentConversion(a, toVertex=1), flatten=1)
        vert_setB = pm.ls(pm.polyListComponentConversion(b, toVertex=1), flatten=1)

        closestVerts = component_utils.ComponentUtils.get_closest_verts(
            vert_setA, vert_setB, tolerance=tolerance
        )

        return True if vert_setA and len(closestVerts) == len(vert_setA) else False

    @staticmethod
    def get_vertex_positions(objects, worldSpace=True):
        """Get all vertex positions for the given objects.

        Parameters:
            objects (str/obj/list): The polygon object(s).
            worldSpace (bool): Sample in world or object space.

        Returns:
            (list) Nested lists if multiple objects given.
        """
        import maya.OpenMaya as om

        space = om.MSpace.kWorld if worldSpace else om.MSpace.kObject

        result = []
        for mesh in core_utils.CoreUtils.mfn_mesh_generator(objects):
            points = om.MPointArray()
            mesh.getPoints(points, space)

            result.append(
                [
                    (points[i][0], points[i][1], points[i][2])
                    for i in range(points.length())
                ]
            )
        return ptk.format_return(result, objects)

    @staticmethod
    def hash_points(points, precision=4):
        """Hash the given list of point values.

        Parameters:
            points (list): A list of point values as tuples.
            precision (int): determines the number of decimal places that are retained
                    in the fixed-point representation. For example, with a value of 4, the
                    fixed-point representation would retain 4 decimal place.
        Returns:
            (list) list(s) of hashed tuples.
        """
        nested = ptk.nested_depth(points) > 1
        sets = points if nested else [points]

        def clamp(p):
            return int(p * 10**precision)

        result = []
        for pset in sets:
            result.append([hash(tuple(map(clamp, i))) for i in pset])
        return ptk.format_return(result, nested)

    @classmethod
    def get_matching_verts(cls, a, b, world_space=False):
        """Find any vertices which point locations match between two given mesh.

        Parameters:
            a (str/obj/list): The first polygon object.
            a (str/obj/list): A second polygon object.
            world_space (bool): Sample in world or object space.

        Returns:
            (list) nested tuples with int values representing matching vertex pairs.
        """
        vert_pos_a, vert_pos_b = cls.get_vertex_positions([a, b], world_space)
        hash_a, hash_b = cls.hash_points([vert_pos_a, vert_pos_b])

        matching = set(hash_a).intersection(hash_b)
        return [
            i
            for h in matching
            for i in zip(ptk.indices(hash_a, h), ptk.indices(hash_b, h))
        ]

    @staticmethod
    def order_by_distance(objects, point=[0, 0, 0], reverse=False):
        """Order the given objects by their distance from the given point.

        Parameters:
            objects (str)(int/list): The object(s) to order.
            point (list): A three value float list x, y, z.
            reverse (bool): Reverse the naming order. (Farthest object first)

        Returns:
            (list) ordered objects
        """
        distance = {}
        for obj in pm.ls(objects, flatten=1):
            xmin, ymin, zmin, xmax, ymax, zmax = pm.xform(obj, q=1, boundingBox=1)
            bb_pos = ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)
            bb_dist = ptk.get_distance(point, bb_pos)

            distance[bb_dist] = obj

        result = [distance[i] for i in sorted(distance)]
        return list(reversed(result)) if reverse else result

    @staticmethod
    @core_utils.CoreUtils.undo
    def align_vertices(mode, average=False, edgeloop=False):
        """Align vertices.

        Parameters:
            mode (int): possible values are align: 0-YZ, 1-XZ, 2-XY, 3-X, 4-Y, 5-Z, 6-XYZ
            average (bool): align to average of all selected vertices. else, align to last selected
            edgeloop (bool): align vertices in edgeloop from a selected edge

        Example:
            align_vertices(mode=3, average=True, edgeloop=True)
        """
        # pm.undoInfo (openChunk=True)
        selectTypeEdge = pm.selectType(query=True, edge=True)

        if edgeloop:
            pm.mel.SelectEdgeLoopSp()  # select edgeloop

        pm.mel.PolySelectConvert(3)  # convert to vertices

        selection = pm.ls(sl=True, flatten=1)
        lastSelected = pm.ls(tail=1, sl=True, flatten=1)
        align_to = pm.xform(lastSelected, q=True, translation=1, worldSpace=1)
        alignX = align_to[0]
        alignY = align_to[1]
        alignZ = align_to[2]

        if average:
            xyz = pm.xform(selection, q=True, translation=1, worldSpace=1)
            x = xyz[0::3]
            y = xyz[1::3]
            z = xyz[2::3]
            alignX = float(sum(x)) / (len(xyz) / 3)
            alignY = float(sum(y)) / (len(xyz) / 3)
            alignZ = float(sum(z)) / (len(xyz) / 3)

        if len(selection) < 2:
            if len(selection) == 0:
                core_utils.CoreUtils.viewport_message("No vertices selected")
            core_utils.CoreUtils.viewport_message(
                "Selection must contain at least two vertices"
            )

        for vertex in selection:
            vertexXYZ = pm.xform(vertex, q=True, translation=1, worldSpace=1)
            vertX = vertexXYZ[0]
            vertY = vertexXYZ[1]
            vertZ = vertexXYZ[2]

            modes = {
                0: (vertX, alignY, alignZ),  # align YZ
                1: (alignX, vertY, alignZ),  # align XZ
                2: (alignX, alignY, vertZ),  # align XY
                3: (alignX, vertY, vertZ),
                4: (vertX, alignY, vertZ),
                5: (vertX, vertY, alignZ),
                6: (alignX, alignY, alignZ),  # align XYZ
            }

            pm.xform(vertex, translation=modes[mode], worldSpace=1)

        if selectTypeEdge:
            pm.selectType(edge=True)
        # pm.undoInfo (closeChunk=True)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
