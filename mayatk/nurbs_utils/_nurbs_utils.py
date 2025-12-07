# !/usr/bin/python
# coding=utf-8
from typing import List, Tuple, Union, Optional

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.components import Components as components
from mayatk.core_utils.mash import MashToolkit
from mayatk.xform_utils._xform_utils import XformUtils


class NurbsUtils(ptk.HelpMixin):
    """ """

    @classmethod
    @CoreUtils.undoable
    def loft(
        cls,
        uniform=True,
        close=False,
        degree=3,
        autoReverse=False,
        sectionSpans=1,
        range_=False,
        polygon=True,
        reverseSurfaceNormals=True,
        angle_loft_between_two_curves=False,
        angleLoftSpans=6,
    ):
        """Create a loft between two selections.

        Parameters:
                uniform (bool): The resulting surface will have uniform parameterization in the loft direction. If set to false, the parameterization will be chord length.
                close (bool): The resulting surface will be closed (periodic) with the start (end) at the first curve. If set to false, the surface will remain open.
                degree (int): The degree of the resulting surface.
                autoReverse (bool): The direction of the curves for the loft is computed automatically. If set to false, the values of the multi-use reverse flag are used instead.
                sectionSpans (int): The number of surface spans between consecutive curves in the loft.
                range_ (bool): Force a curve range on complete input curve.
                polygon (bool): The object created by this operation.
                reverseSurfaceNormals (bool): The surface normals on the output NURBS surface will be reversed. This is accomplished by swapping the U and V parametric directions.
                angle_loft_between_two_curves (bool): Perform a loft at an angle between two selected curves or polygon edges (that will be extracted as curves).
                angleLoftSpans (int): Angle loft: Number of duplicated points (spans).

        Returns:
                (obj) nurbsToPoly history node.
        """
        # pm.undoInfo(openChunk=1)
        sel = pm.ls(sl=True)

        if len(sel) > 1:
            if angle_loft_between_two_curves:
                start, end = sel[:2]  # get the first two selected edge loops or curves.
                result = cls.angle_loft_between_two_curves(
                    start,
                    end,
                    count=angleLoftSpans,
                    cleanup=True,
                    uniform=uniform,
                    close=close,
                    autoReverse=autoReverse,
                    degree=degree,
                    sectionSpans=sectionSpans,
                    range=range_,
                    polygon=0,
                    reverseSurfaceNormals=reverseSurfaceNormals,
                )
            else:
                result = pm.loft(
                    sel,
                    u=uniform,
                    c=close,
                    ar=autoReverse,
                    d=degree,
                    ss=sectionSpans,
                    rn=range_,
                    po=0,
                    rsn=reverseSurfaceNormals,
                )
        else:
            return "# Error: Operation requires the selection of two curves or polygon edge sets. #"

        if polygon:  # convert nurb surface to polygon.
            converted = pm.nurbsToPoly(
                result[0],
                mnd=1,
                f=3,
                pt=1,
                pc=200,
                chr=0.1,
                ft=0.01,
                mel=0.001,
                d=0.1,
                ut=1,
                un=3,
                vt=1,
                vn=3,
                uch=0,
                ucr=0,
                cht=0.2,
                es=0,
                ntr=0,
                mrt=0,
                uss=1,
            )
            for obj in result:
                try:
                    pm.delete(obj)
                except Exception:
                    pass
            result = converted

        # pm.undoInfo(closeChunk=1)
        return result

    @classmethod
    @CoreUtils.undoable
    def create_curve_between_two_objs(cls, start, end):
        """Create a bezier curve between starting and end object(s).

        Parameters:
                start () = Starting object(s).
                end () = Ending object(s).

        Returns:
                (obj) Bezier curve.
        """
        # pm.undoInfo(openChunk=1)
        p1 = pm.objectCenter(start)
        p2 = pm.objectCenter(end)
        hypotenuse = ptk.distance_between_points(p1, p2)

        v1, v2 = cls.getCrossProductOfCurves([start, end], normalize=1, values=1)
        v3a = ptk.get_vector_from_two_points(p1, p2)
        v3b = ptk.get_vector_from_two_points(p2, p1)

        a1 = ptk.get_angle_from_two_vectors(
            v1, v3a, degree=1
        )  # SlotsMaya.get_angle_from_three_points(v1, p1, p2, degree=1)
        a2 = ptk.get_angle_from_two_vectors(
            v2, v3b, degree=1
        )  # SlotsMaya.get_angle_from_three_points(v2, p1, p2, degree=1)
        a3 = ptk.get_angle_from_two_vectors(v1, v2, degree=1)

        d1, d2 = ptk.get_two_sides_of_asa_triangle(
            a2, a1, hypotenuse
        )  # get length of sides 1 and 2.

        p_from_v1 = ptk.move_point_relative_along_vector(p1, p2, v1, d1)
        p_from_v2 = ptk.move_point_relative_along_vector(p2, p1, v2, d2)
        p3 = ptk.get_center_of_two_points(p_from_v1, p_from_v2)

        if d1 < d2:
            min_dist = d1
            max_vect = ptk.get_vector_from_two_points(p2, p3)
        else:
            min_dist = d2
            max_vect = ptk.get_vector_from_two_points(p1, p3)
            p1, p2 = p2, p1

        # pm.spaceLocator(position=p1); pm.spaceLocator(position=p2); pm.spaceLocator(position=p3)

        p4 = ptk.move_point_relative(p3, min_dist, max_vect)
        # pm.spaceLocator(position=p4)
        p5 = ptk.get_center_of_two_points(p4, p1)
        # pm.spaceLocator(position=p5)
        p6 = ptk.get_center_of_two_points(p3, p5)
        # pm.spaceLocator(position=p6)

        # add weighting to the curve points.
        p1w, p3w, p4w, p2w = [
            (p1[0], p1[1], p1[2], 1),
            (p3[0], p3[1], p3[2], 4),
            (p4[0], p4[1], p4[2], 10),
            (p2[0], p2[1], p2[2], 1),
        ]

        result = pm.curve(pw=[p1w, p3w, p4w, p2w], k=[0, 0, 0, 1, 1, 1], bezier=1)
        # pm.undoInfo(closeChunk=1)

        return result

    @CoreUtils.undoable
    @staticmethod
    def duplicate_along_curve(path, start, count=6, geometry="Instancer"):
        """Duplicate objects along a given curve using MASH.

        Parameters:
                path (obj): The curve to use as a path.
                start () = Starting object.
                count (int): The number of duplicated objects. (point count on the MASH network)
                geometry (str): Particle instancer or mesh instancer (Repro node). (valid: 'Mesh' (default), 'Instancer')

        Returns:
                (list) The duplicated objects in order of start to end.
        """
        # pm.undoInfo(openChunk=1)
        # create a MASH network
        mashNW, _waiter, instNode, distNode = MashToolkit.create_network(
            objects=start,
            geometry=geometry,
            hideOnCreate=False,
        )

        curveNode = pm.ls(mashNW.addNode("MASH_Curve").name)[0]
        pm.connectAttr(path.worldSpace[0], curveNode.inCurves[0], force=1)

        pm.setAttr(curveNode.stopAtEnd, 1)  # 0=off, 1=on
        pm.setAttr(curveNode.clipStart, 0)
        pm.setAttr(curveNode.clipEnd, 1)
        pm.setAttr(curveNode.equalSpacing, 1)
        pm.setAttr(curveNode.timeStep, 1)
        pm.setAttr(curveNode.curveLengthAffectsSpeed, 1)

        pm.setAttr(distNode.pointCount, count)
        pm.setAttr(distNode.amplitudeX, 0)

        baked_curves = MashToolkit.bake_instancer(mashNW, instNode)

        result = [start]
        for curve in reversed(baked_curves):
            result.append(curve)

        pm.delete(mashNW.waiter.name())  # delete the MASH network.
        # pm.undoInfo(closeChunk=1)

        return result

    @CoreUtils.undoable
    @classmethod
    def angle_loft_between_two_curves(
        cls,
        start,
        end,
        count=6,
        cleanup=False,
        uniform=1,
        close=0,
        autoReverse=0,
        degree=3,
        sectionSpans=1,
        range=0,
        polygon=1,
        reverseSurfaceNormals=0,
    ):
        """Perform a loft between two nurbs curves or polygon sets of edges (that will be extracted as curves).

        Parameters:
                start (list): Starting edges.
                end (list): Ending edges.
                count (int): Section count.
                cleanup (bool): Delete the start, end, and any additional construction curves upon completion.

        Returns:
                (list) Loft object name and node name.
        """
        if pm.objectType(start) == "mesh":  # vs. 'nurbsCurve'
            start, startNode = pm.polyToCurve(
                start, form=2, degree=3, conformToSmoothMeshPreview=True
            )  # extract curve from mesh
        XformUtils.reset_translation(start)  # reset the transforms to world origin.

        if pm.objectType(end) == "mesh":  # vs. 'nurbsCurve'
            end, endNode = pm.polyToCurve(
                end, form=2, degree=3, conformToSmoothMeshPreview=True
            )  # extract curve from mesh
        XformUtils.reset_translation(end)  # reset the transforms to world origin.

        path = cls.create_curve_between_two_objs(start, end)
        curves = cls.duplicate_along_curve(path, start, count=count)

        result = pm.loft(
            curves,
            u=uniform,
            c=close,
            ar=autoReverse,
            d=degree,
            ss=sectionSpans,
            rn=range,
            po=polygon,
            rsn=reverseSurfaceNormals,
        )

        if cleanup:  # perform cleanup by deleting construction curves.
            try:
                curves_parent = pm.listRelatives(curves[1], parent=1)
                pm.delete(curves_parent)
                pm.delete(end)
                pm.delete(path)
                pm.delete(start)
            except Exception as e:
                print(e)

        return result

    @CoreUtils.undoable
    @staticmethod
    def get_closest_cv(x, curves, tolerance=0.0):
        """Find the closest control vertex between the given vertices, CVs, or objects and each of the given curves.

        Parameters:
                x (str/obj/list): Polygon vertices, control vertices, objects, or points given as (x,y,z) tuples.
                curves (str/obj/list): The reference object in which to find the closest CV for each vertex in the list of given vertices.
                tolerance (int)(float) = Maximum search distance. Default is 0.0, which turns off the tolerance flag.

        Returns:
                (dict) closest vertex/cv pairs (one pair for each given curve) ex. {<vertex from set1>:<vertex from set2>}.
        """
        x = pm.ls(x, flatten=True)

        npcNode = pm.ls(pm.createNode("nearestPointOnCurve"))[0]

        result = {}
        for curve in pm.ls(curves):
            pm.connectAttr(curve.worldSpace, npcNode.inputCurve, force=1)

            for i in x:
                if not isinstance(i, (tuple, list, set)):
                    pos = pm.pointPosition(i)
                else:
                    pos = i
                pm.setAttr(npcNode.inPosition, pos)

                distance = ptk.distance_between_points(
                    pos, pm.getAttr(npcNode.position)
                )
                p = pm.getAttr(npcNode.parameter)
                if not tolerance:
                    result[str(i)] = p
                elif distance < tolerance:
                    result[str(i)] = p

        pm.delete(npcNode)

        return result

    @classmethod
    def get_cv_info(cls, c, returned_type="cv", filter_=[]):
        """Get a dict containing CV's of the given curve(s) and their corresponding point positions (based on Maya's pointOnCurve command).

        Parameters:
                - c (str/obj/list): Curves or CVs to get CV info from.
                - returned_type (str): The desired returned values. Default is 'cv'.
                        valid values are:
                                'cv' = Return a list of all CV's for the given curves.
                                'count' = Return an integer representing the total number of cvs for each of the curves given.
                                'parameter', 'position', 'index', 'localPosition', 'tangent', 'normalizedTangent', 'normal', 'normalizedNormal', 'curvatureRadius', 'curvatureCenter'
                                = Return a dict with CV's as keys and the returned_type as their corresponding values.
                        ex. {NurbsCurveCV(u'polyToCurveShape7.cv[5]'): [-12.186520865542082, 15.260936896515751, -369.6159740743584]}
                - filter_ (str/obj/list): Value(s) to filter for in the returned results.

        Returns:
                (dict)(list)(int) dependant on returned_type.

        ex. cv_tan = get_cv_info(curve.cv[0:2],'tangent') #get CV tangents for cvs 0-2.
        ex. cvParam = get_cv_info(curve, 'parameters') #get the curves CVs and their corresponding U parameter values.
        ex. filtered = get_cv_info(<curve>, 'normal', <normal>) #filter results for those that match the given value.
        """
        result = {}
        for curve in pm.ls(c):
            if ".cv" in str(curve):  # if CV given.
                cvs = curve
                curve = pm.listRelatives(cvs, parent=1)
            else:  # if curve(s) given
                cvs = curve.cv

            parameters = cls.get_closest_cv(
                cvs, curve
            )  # use get_closest_cv to get the parameter location for each of the curves CVs.
            for cv, p in parameters.items():
                if returned_type == "position":  # Get cv position
                    v = pm.pointOnCurve(curve, parameter=p, position=True)
                elif returned_type == "localPosition":
                    v = pm.getAttr(cv)  # local cv position
                elif returned_type == "tangent":  # Get cv tangent
                    v = pm.pointOnCurve(curve, parameter=p, tangent=True)
                elif returned_type == "normalizedTangent":
                    v = pm.pointOnCurve(curve, parameter=p, normalizedTangent=True)
                elif returned_type == "normal":  # Get cv normal
                    v = pm.pointOnCurve(curve, parameter=p, normal=True)
                elif returned_type == "normalizedNormal":
                    v = pm.pointOnCurve(
                        curve, parameter=p, normalizedNormal=True
                    )  # Returns the (x,y,z) normalized normal of curve1 at parameter 0.5.
                elif returned_type == "curvatureRadius":  # Get cv curvature
                    v = pm.pointOnCurve(
                        curve, parameter=p, curvatureRadius=True
                    )  # Returns the curvature radius of curve1 at parameter 0.5.
                elif returned_type == "curvatureCenter":
                    v = pm.pointOnCurve(curve, parameter=p, curvatureCenter=True)
                elif returned_type == "parameter":  # Return the CVs parameter.
                    v = p
                elif returned_type == "count":  # total number of cv's for the curve.
                    result[curve] = len(cls.get_cv_info(curve))
                    break
                elif returned_type == "index":  # index of the cv
                    s = str(cv)
                    v = int(s[s.index("[") + 1 : s.index("]")])
                else:
                    v = None

                result[cv] = v

        if returned_type == "cv":
            result = result.keys()

        if filter_:
            if not isinstance(filter_, (tuple, set, list)):
                filter_ = list(filter_)
            try:
                result = {
                    k: v for k, v in result.items() if any((v in filter_, v == filter_))
                }
            except AttributeError:
                result = [i for i in result if any((i in filter_, i == filter_))]

        if len(result) == 1:
            try:
                result = list(result.values())[0]
            except (AttributeError, TypeError):
                result = result[0]

        return result

    @classmethod
    def getCrossProductOfCurves(cls, curves, normalize=1, values=False):
        """Get the cross product of two vectors using points derived from the given curves.

        Parameters:
                curves (str/obj/list): Nurbs curve(s).
                normalize (float) = (0) Do not normalize. (1) Normalize standard. (value other than 0 or 1) Normalize using the given float value as desired length.
                values (bool): Return only a list of the cross product vector values [(<Vx>, <Vy>, <Vz>)] instead of the full dict {<curve1>:(<Vx>, <Vy>, <Vz>)}.

        Returns:
                (dict)(list)
        """
        result = {}
        for curve in pm.ls(curves):
            p0 = pm.objectCenter(curve)

            cvPos = cls.get_cv_info(curve, "position")
            cvs = list(cvPos.keys())
            p1 = cvPos[cvs[0]]
            p2 = cvPos[cvs[int(len(cvs) / 2)]]

            n1 = ptk.cross_product(p0, p1, p2, normalize=normalize)

            result[curve] = n1

        if values:
            result = list(result.values())
        return result


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
