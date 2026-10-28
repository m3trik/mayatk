# !/usr/bin/python
# coding=utf-8
try:
    import maya.cmds as cmds
    import maya.mel as mel
    import maya.api.OpenMaya as om
except ImportError:
    cmds = mel = om = None

from typing import List, Tuple, Union, Optional

import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.components import Components as components
from mayatk.core_utils.mash import MashToolkit
from mayatk.display_utils._display_utils import DisplayUtils
from mayatk.xform_utils._xform_utils import XformUtils


class NurbsUtils(ptk.HelpMixin):
    """ """

    #: The curve actions tentacle's Nurbs list (and its Extrude button) offers ->
    #: the Maya runtime command each runs: Maya's own menu item, with the
    #: settings its option box last applied (an ``...Options`` command opens that
    #: option box). The keys are the vocabulary :meth:`curve_action` shares with
    #: blendertk's ``NurbsUtils.CURVE_ACTIONS``, which carries the ones Blender
    #: can serve.
    CURVE_ACTIONS = {
        "project": "ProjectCurveOnMesh",
        "extract": "CreateCurveFromPoly",
        "duplicate": "DuplicateCurve",
        "lock_length": "LockCurveLength",
        "unlock_length": "UnlockCurveLength",
        "bend": "BendCurves",
        "curl": "CurlCurves",
        "scale_curvature": "ScaleCurvature",
        "smooth": "SmoothHairCurves",
        "straighten": "StraightenCurves",
        "planar": "Planar",
        "insert_isoparm": "InsertIsoparms",
        "attach": "AttachCurveOptions",
        "detach": "DetachCurve",
        "cut": "CutCurve",
        "open_close": "OpenCloseCurve",
        "insert_knot": "InsertKnot",
        "rebuild": "RebuildCurveOptions",
        "reverse": "ReverseCurve",
        "extend_options": "ExtendCurveOptions",
        "extend": "ExtendCurve",
        "extend_on_surface": "ExtendCurveOnSurface",
        "extrude": "Extrude",
    }

    #: Revolve axes by name (``cmds.revolve -axis``).
    REVOLVE_AXES = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}

    @classmethod
    @CoreUtils.undoable
    def curve_action(cls, action: str, curves=None) -> List[str]:
        """Run the curve action *action* (a :attr:`CURVE_ACTIONS` key) on *curves*.

        Twin of blendertk's ``NurbsUtils.curve_action``: one call per action for
        a panel shared by both hosts. Maya's runtime command acts on the
        selection, so *curves* other than None are selected first; Maya's own
        warnings (a wrong selection) stay Maya's. One undo step.

        Parameters:
            action (str): A :attr:`CURVE_ACTIONS` key (``"reverse"``, ``"planar"``).
            curves (str/list/None): What to act on; None keeps the selection.

        Returns:
            (list): The selection the command left -- its result, for an action
                that creates (Extract, Duplicate, Planar); ``[]`` when none.

        Raises:
            ValueError: *action* is not a :attr:`CURVE_ACTIONS` key.
            RuntimeError: Maya refused it (its message, after the command's name).
        """
        command = cls.CURVE_ACTIONS.get(action)
        if command is None:
            raise ValueError(
                f"Unknown curve action {action!r}: expected one of "
                f"{', '.join(sorted(cls.CURVE_ACTIONS))}."
            )
        if curves is not None:
            curves = CoreUtils.as_strings(curves)
            if not curves:
                return []
            cmds.select(curves, replace=True)
        try:
            mel.eval(command)
        except RuntimeError as error:
            raise RuntimeError(f"{command}: {error}") from error
        return cmds.ls(selection=True) or []

    @classmethod
    @CoreUtils.selected
    @CoreUtils.undoable
    @DisplayUtils.add_to_isolation
    def revolve(
        cls,
        curves=None,
        axis: str = "y",
        start_sweep: float = 0.0,
        end_sweep: float = 360.0,
        sections: int = 8,
        degree: int = 3,
        range_: bool = False,
        polygon: bool = True,
        use_tolerance: bool = False,
        tolerance: float = 0.001,
        auto_correct_normal: bool = False,
    ) -> List[str]:
        """Revolve each profile curve about *axis* through its own pivot into a
        surface (``cmds.revolve``), one surface per curve as Maya's Revolve does.

        Twin of blendertk's ``NurbsUtils.revolve`` (a Screw modifier there, which
        has no degree, range, polygon toggle or tolerance). One undo step; the
        surfaces join an isolated viewport and end selected.

        Parameters:
            curves (str/list/None): NURBS curves, curves on surface, isoparms or
                polygon edges; None takes the selection.
            axis (str): ``"x"``, ``"y"`` or ``"z"``.
            start_sweep (float): The start angle of the sweep (degrees).
            end_sweep (float): The end angle of the sweep (degrees).
            sections (int): The number of sections around the revolution.
            degree (int): The surface degree.
            range_ (bool): Force a curve range on the complete input curve.
            polygon (bool): Output a polygon mesh rather than a NURBS surface.
            use_tolerance (bool): Build to *tolerance* rather than *sections*.
            tolerance (float): The tolerance to build to.
            auto_correct_normal (bool): Reverse the axis where needed for the
                normals to point outward.

        Returns:
            (list): The surfaces (or meshes) made; ``[]`` when *curves* holds no
                curve to revolve.

        Raises:
            ValueError: An unknown *axis*.
        """
        vector = cls.REVOLVE_AXES.get(str(axis).lower())
        if vector is None:
            raise ValueError(f"axis must be 'x', 'y' or 'z', not {axis!r}.")
        nodes = CoreUtils.as_strings(curves)
        if not nodes:
            return []
        # Maya's own Revolve takes the same set (revolvePreset.mel): NURBS curves,
        # curves on surface, isoparms and mesh edges, one revolve each.
        profiles = (
            cmds.filterExpand(nodes, expand=True, selectionMask=(9, 11, 32, 45)) or []
        )
        surfaces = []
        for profile in profiles:
            result = cmds.revolve(
                profile,
                polygon=int(bool(polygon)),
                range=bool(range_),
                startSweep=start_sweep,
                endSweep=end_sweep,
                useTolerance=bool(use_tolerance),
                tolerance=tolerance,
                degree=degree,
                sections=sections,
                useLocalPivot=True,
                axis=vector,
                autoCorrectNormal=bool(auto_correct_normal),
            )
            surfaces.append(result[0])
        if surfaces:
            cmds.select(surfaces, replace=True)
        return surfaces

    @classmethod
    @CoreUtils.undoable
    @DisplayUtils.add_to_isolation
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
                (list) The loft's nodes (its nurbsToPoly nodes when ``polygon``);
                ``[]`` with fewer than two profiles selected. Twin of blendertk's
                ``NurbsUtils.loft``.
        """
        # cmds.undoInfo(openChunk=1)
        sel = cmds.ls(sl=True, long=True)

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
                result = cmds.loft(
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
            return []  # fewer than two profiles: nothing to loft across

        if polygon:  # convert nurb surface to polygon.
            converted = cmds.nurbsToPoly(
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
                    cmds.delete(obj)
                except Exception:
                    pass
            result = converted

        # cmds.undoInfo(closeChunk=1)
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
        start = str(start)
        end = str(end)
        p1 = cmds.objectCenter(start)
        p2 = cmds.objectCenter(end)
        hypotenuse = ptk.distance_between_points(p1, p2)

        v1, v2 = cls.getCrossProductOfCurves([start, end], normalize=1, values=1)
        v3a = ptk.get_vector_from_two_points(p1, p2)
        v3b = ptk.get_vector_from_two_points(p2, p1)

        a1 = ptk.get_angle_from_two_vectors(
            v1, v3a, degree=1
        )
        a2 = ptk.get_angle_from_two_vectors(
            v2, v3b, degree=1
        )

        try:
            d1, d2 = ptk.get_two_sides_of_asa_triangle(
                a2, a1, hypotenuse
            )  # get length of sides 1 and 2.
        except ValueError:
            # Parallel construction rays (a1 + a2 == 180°, e.g. two curves
            # with identical normals): no triangle apex exists. Place the
            # apex construction points at half the span instead, which
            # degrades the result to a gentle symmetric arc.
            d1 = d2 = hypotenuse / 2.0

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

        # cmds.spaceLocator(position=p1); cmds.spaceLocator(position=p2); cmds.spaceLocator(position=p3)

        p4 = ptk.move_point_relative(p3, min_dist, max_vect)
        # cmds.spaceLocator(position=p4)
        p5 = ptk.get_center_of_two_points(p4, p1)
        # cmds.spaceLocator(position=p5)
        p6 = ptk.get_center_of_two_points(p3, p5)
        # cmds.spaceLocator(position=p6)

        # add weighting to the curve points.
        p1w, p3w, p4w, p2w = [
            (p1[0], p1[1], p1[2], 1),
            (p3[0], p3[1], p3[2], 4),
            (p4[0], p4[1], p4[2], 10),
            (p2[0], p2[1], p2[2], 1),
        ]

        result = cmds.curve(pw=[p1w, p3w, p4w, p2w], k=[0, 0, 0, 1, 1, 1], bezier=1)
        # cmds.undoInfo(closeChunk=1)

        return result

    @staticmethod
    @CoreUtils.undoable
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
        # cmds.undoInfo(openChunk=1)
        # create a MASH network
        mashNW, _waiter, instNode, distNode = MashToolkit.create_network(
            objects=start,
            geometry=geometry,
            hideOnCreate=False,
        )

        curveNode = cmds.ls(mashNW.addNode("MASH_Curve").name)[0]
        cmds.connectAttr(f"{path}.worldSpace[0]", f"{curveNode}.inCurves[0]", force=1)

        cmds.setAttr(f"{curveNode}.stopAtEnd", 1)  # 0=off, 1=on
        cmds.setAttr(f"{curveNode}.clipStart", 0)
        cmds.setAttr(f"{curveNode}.clipEnd", 1)
        cmds.setAttr(f"{curveNode}.equalSpacing", 1)
        cmds.setAttr(f"{curveNode}.timeStep", 1)
        cmds.setAttr(f"{curveNode}.curveLengthAffectsSpeed", 1)

        cmds.setAttr(f"{distNode}.pointCount", count)
        cmds.setAttr(f"{distNode}.amplitudeX", 0)

        baked_curves = MashToolkit.bake_instancer(mashNW, instNode)

        result = [start]
        for curve in reversed(baked_curves):
            result.append(curve)

        cmds.delete(mashNW.waiter)  # delete the MASH network.
        # cmds.undoInfo(closeChunk=1)

        return result

    @classmethod
    @CoreUtils.undoable
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
        if cmds.objectType(start) == "mesh":  # vs. 'nurbsCurve'
            start, startNode = cmds.polyToCurve(
                start, form=2, degree=3, conformToSmoothMeshPreview=True
            )  # extract curve from mesh
        XformUtils.reset_translation(start)  # reset the transforms to world origin.

        if cmds.objectType(end) == "mesh":  # vs. 'nurbsCurve'
            end, endNode = cmds.polyToCurve(
                end, form=2, degree=3, conformToSmoothMeshPreview=True
            )  # extract curve from mesh
        XformUtils.reset_translation(end)  # reset the transforms to world origin.

        path = cls.create_curve_between_two_objs(start, end)
        curves = cls.duplicate_along_curve(path, start, count=count)

        result = cmds.loft(
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
                curves_parent = cmds.listRelatives(curves[1], parent=1)
                cmds.delete(curves_parent)
                cmds.delete(end)
                cmds.delete(path)
                cmds.delete(start)
            except Exception as e:
                print(e)

        return result

    @staticmethod
    def _curve_fn(curve) -> "om.MFnNurbsCurve":
        """Resolve a curve transform/shape name to a dagPath-bound MFnNurbsCurve."""
        sel = om.MSelectionList()
        sel.add(str(curve))
        dag = sel.getDagPath(0)
        if not dag.hasFn(om.MFn.kNurbsCurve):
            dag.extendToShape()
        return om.MFnNurbsCurve(dag)

    @classmethod
    def get_curve_length(cls, curve) -> float:
        """World-space arc length of the given curve (transform or shape)."""
        return cls._curve_fn(curve).length()

    @classmethod
    def get_arc_lengths(cls, curve, points) -> List[float]:
        """Arc length along *curve* of the closest curve point to each given point.

        Batch primitive for parametric weighting / motion-path math: opens the
        curve function set once, then per point runs closestPoint -> param ->
        findLengthFromParam. Params are clamped into the knot domain to guard
        against end-seam float noise.

        Parameters:
            curve (str/obj): NURBS curve transform or shape.
            points (list): World-space points (MPoint, MVector, or xyz sequences).

        Returns:
            (List[float]) Arc length for each input point, in input order.
        """
        fn = cls._curve_fn(curve)
        domain_min, domain_max = fn.knotDomain
        lengths = []
        for p in points:
            point = om.MPoint(p[0], p[1], p[2])
            _, param = fn.closestPoint(point, space=om.MSpace.kWorld)
            param = min(max(param, domain_min), domain_max)
            lengths.append(fn.findLengthFromParam(param))
        return lengths

    @classmethod
    def get_greville_arc_lengths(cls, curve) -> List[float]:
        """Arc length of each CV's Greville abscissa, in CV index order.

        A CV's Greville abscissa is the parameter where its basis function
        peaks — the CV's own station along the curve. This is the exact way
        to place a curve's CVs on its own arc: a CV of a degree > 1 curve
        sits OFF the curve (on the control polygon), so projecting it back
        with ``closestPoint`` is both approximate and ambiguous wherever the
        curve doubles back near itself. Pure knot arithmetic — no search.

        Note:
            ``MFnNurbsCurve.knots()`` omits the first and last of the
            textbook ``numCVs + degree + 1`` knot vector, so Maya's
            ``knots[i]`` is the literature's ``u[i + 1]`` and CV *i*'s
            abscissa averages the slice ``knots[i : i + degree]`` — NOT the
            ``[i + 1 : i + degree + 1]`` the textbook formula reads as. On a
            clamped curve this puts the first and last CVs exactly at the
            ends (arc length 0 and the full length).

        Note:
            Intended for OPEN (clamped) curves. A periodic curve's leading
            abscissae fall before the knot domain and clamp onto its start,
            so the first ``degree`` CVs share a station rather than
            increasing — fine for a lookup, not usable as skin-weight
            stations.

        Returns:
            (List[float]) Arc length per CV, in CV index order.
        """
        fn = cls._curve_fn(curve)
        degree = fn.degree
        knots = list(fn.knots())
        domain_min, domain_max = fn.knotDomain
        lengths = []
        for i in range(fn.numCVs):
            window = knots[i : i + degree]
            param = sum(window) / len(window) if window else domain_min
            param = min(max(param, domain_min), domain_max)
            lengths.append(fn.findLengthFromParam(param))
        return lengths

    @staticmethod
    @CoreUtils.undoable
    def get_closest_cv(x, curves, tolerance=0.0):
        """Find the closest control vertex between the given vertices, CVs, or objects and each of the given curves.

        Parameters:
                x (str/obj/list): Polygon vertices, control vertices, objects, or points given as (x,y,z) tuples.
                curves (str/obj/list): The reference object in which to find the closest CV for each vertex in the list of given vertices.
                tolerance (int)(float) = Maximum search distance. Default is 0.0, which turns off the tolerance flag.

        Returns:
                (dict) closest vertex/cv pairs (one pair for each given curve) ex. {<vertex from set1>:<vertex from set2>}.
        """
        x = cmds.ls(x, flatten=True)

        npcNode = cmds.ls(cmds.createNode("nearestPointOnCurve"))[0]

        result = {}
        for curve in cmds.ls(curves):
            cmds.connectAttr(f"{curve}.worldSpace", f"{npcNode}.inputCurve", force=1)

            for i in x:
                if not isinstance(i, (tuple, list, set)):
                    pos = cmds.pointPosition(i)
                else:
                    pos = i
                cmds.setAttr(f"{npcNode}.inPosition", *pos)

                distance = ptk.distance_between_points(
                    pos, cmds.getAttr(f"{npcNode}.position")[0]
                )
                p = cmds.getAttr(f"{npcNode}.parameter")
                if not tolerance:
                    result[str(i)] = p
                elif distance < tolerance:
                    result[str(i)] = p

        cmds.delete(npcNode)

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
        for curve in cmds.ls(c, long=True):
            curve = str(curve)
            if ".cv" in curve:  # if CV given.
                cvs = curve
                curve = cmds.listRelatives(cvs, parent=1, fullPath=True)
            else:  # if curve(s) given
                cvs = f"{curve}.cv[*]"

            parameters = cls.get_closest_cv(
                cvs, curve
            )  # use get_closest_cv to get the parameter location for each of the curves CVs.
            for cv, p in parameters.items():
                if returned_type == "position":  # Get cv position
                    v = cmds.pointOnCurve(curve, parameter=p, position=True)
                elif returned_type == "localPosition":
                    v = cmds.getAttr(cv)  # local cv position
                elif returned_type == "tangent":  # Get cv tangent
                    v = cmds.pointOnCurve(curve, parameter=p, tangent=True)
                elif returned_type == "normalizedTangent":
                    v = cmds.pointOnCurve(curve, parameter=p, normalizedTangent=True)
                elif returned_type == "normal":  # Get cv normal
                    v = cmds.pointOnCurve(curve, parameter=p, normal=True)
                elif returned_type == "normalizedNormal":
                    v = cmds.pointOnCurve(
                        curve, parameter=p, normalizedNormal=True
                    )  # Returns the (x,y,z) normalized normal of curve1 at parameter 0.5.
                elif returned_type == "curvatureRadius":  # Get cv curvature
                    v = cmds.pointOnCurve(
                        curve, parameter=p, curvatureRadius=True
                    )  # Returns the curvature radius of curve1 at parameter 0.5.
                elif returned_type == "curvatureCenter":
                    v = cmds.pointOnCurve(curve, parameter=p, curvatureCenter=True)
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
        for curve in cmds.ls(curves, long=True):
            p0 = cmds.objectCenter(curve)

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
