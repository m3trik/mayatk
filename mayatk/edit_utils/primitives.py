"""Primitive creation utilities for Maya.

This module provides functionality for creating various primitive objects
with flexible parameter handling.
"""

import math
import pymel.core as pm
from typing import Optional, List

# Import required utilities
try:
    from mayatk.core_utils._core_utils import CoreUtils
    from mayatk.display_utils._display_utils import DisplayUtils
    from mayatk.node_utils._node_utils import NodeUtils
    from mayatk.xform_utils._xform_utils import XformUtils
except ImportError:
    # Fallback for development/testing
    pass


class Primitives:
    """Utilities for creating primitive objects in Maya."""

    @classmethod
    @CoreUtils.undoable
    @DisplayUtils.add_to_isolation
    def create_default_primitive(cls, baseType, subType, **kwargs):
        """Create a primitive object with flexible parameters.

        Parameters:
            baseType (str): The base type ('polygon', 'nurbs', 'light').
            subType (str): The sub type ('cube', 'sphere', 'cylinder', etc.).
            **kwargs: Flexible parameters including:
                scale (bool): Whether to match scale to selected objects.
                translate (bool): Whether to move to selected objects' center.
                axis (list): Axis orientation [x, y, z] (default: [0, 90, 0]).
                Any other parameters specific to the primitive creation command.

        Returns:
            The created primitive's history node.
        """
        baseType = baseType.lower()
        subType = subType.lower()

        # Extract post-creation options
        scale = kwargs.pop("scale", False)
        translate = kwargs.pop("translate", False)
        axis = kwargs.pop("axis", [0, 90, 0])

        selection = pm.selected()

        # Define primitive creation functions with default parameters
        def create_poly_cube(**kw):
            defaults = {
                "axis": axis,
                "width": 5,
                "height": 5,
                "depth": 5,
                "subdivisionsX": 1,
                "subdivisionsY": 1,
                "subdivisionsZ": 1,
            }
            defaults.update(kw)
            return pm.polyCube(**defaults)

        def create_poly_sphere(**kw):
            defaults = {
                "axis": axis,
                "radius": 5,
                "subdivisionsX": 12,
                "subdivisionsY": 12,
            }
            defaults.update(kw)
            return pm.polySphere(**defaults)

        def create_poly_cylinder(**kw):
            defaults = {
                "axis": axis,
                "radius": 5,
                "height": 10,
                "subdivisionsX": 12,
                "subdivisionsY": 1,
                "subdivisionsZ": 1,
            }
            defaults.update(kw)
            return pm.polyCylinder(**defaults)

        def create_poly_plane(**kw):
            defaults = {
                "axis": axis,
                "width": 5,
                "height": 5,
                "subdivisionsX": 1,
                "subdivisionsY": 1,
            }
            defaults.update(kw)
            return pm.polyPlane(**defaults)

        def create_circle(**kw):
            defaults = {"axis": "y", "numPoints": 12, "radius": 5, "mode": 0}
            defaults.update(kw)
            return cls.create_circle(**defaults)

        def create_poly_cone(**kw):
            defaults = {
                "axis": axis,
                "radius": 5,
                "height": 5,
                "subdivisionsX": 1,
                "subdivisionsY": 1,
                "subdivisionsZ": 1,
            }
            defaults.update(kw)
            return pm.polyCone(**defaults)

        def create_poly_pyramid(**kw):
            defaults = {
                "axis": axis,
                "sideLength": 5,
                "numberOfSides": 5,
                "subdivisionsHeight": 1,
                "subdivisionsCaps": 1,
            }
            defaults.update(kw)
            return pm.polyPyramid(**defaults)

        def create_poly_torus(**kw):
            defaults = {
                "axis": axis,
                "radius": 10,
                "sectionRadius": 5,
                "twist": 0,
                "subdivisionsX": 5,
                "subdivisionsY": 5,
            }
            defaults.update(kw)
            return pm.polyTorus(**defaults)

        def create_poly_pipe(**kw):
            defaults = {
                "axis": axis,
                "radius": 5,
                "height": 5,
                "thickness": 2,
                "subdivisionsHeight": 1,
                "subdivisionsCaps": 1,
            }
            defaults.update(kw)
            return pm.polyPipe(**defaults)

        def create_geosphere(**kw):
            defaults = {"axis": axis, "radius": 5, "sideLength": 5, "polyType": 0}
            defaults.update(kw)
            return pm.polyPrimitive(**defaults)

        def create_platonic_solids(**kw):
            return pm.mel.eval("performPolyPrimitive PlatonicSolid 0;")

        def create_nurbs_cube(**kw):
            defaults = {
                "ch": 1,
                "d": 3,
                "hr": 1,
                "p": (0, 0, 0),
                "lr": 1,
                "w": 1,
                "v": 1,
                "ax": (0, 1, 0),
                "u": 1,
            }
            defaults.update(kw)
            return pm.nurbsCube(**defaults)

        def create_nurbs_sphere(**kw):
            defaults = {
                "esw": 360,
                "ch": 1,
                "d": 3,
                "ut": 0,
                "ssw": 0,
                "p": (0, 0, 0),
                "s": 8,
                "r": 1,
                "tolerance": 0.01,
                "nsp": 4,
                "ax": (0, 1, 0),
            }
            defaults.update(kw)
            return pm.sphere(**defaults)

        def create_nurbs_cylinder(**kw):
            defaults = {
                "esw": 360,
                "ch": 1,
                "d": 3,
                "hr": 2,
                "ut": 0,
                "ssw": 0,
                "p": (0, 0, 0),
                "s": 8,
                "r": 1,
                "tolerance": 0.01,
                "nsp": 1,
                "ax": (0, 1, 0),
            }
            defaults.update(kw)
            return pm.cylinder(**defaults)

        def create_nurbs_cone(**kw):
            defaults = {
                "esw": 360,
                "ch": 1,
                "d": 3,
                "hr": 2,
                "ut": 0,
                "ssw": 0,
                "p": (0, 0, 0),
                "s": 8,
                "r": 1,
                "tolerance": 0.01,
                "nsp": 1,
                "ax": (0, 1, 0),
            }
            defaults.update(kw)
            return pm.cone(**defaults)

        def create_nurbs_plane(**kw):
            defaults = {
                "ch": 1,
                "d": 3,
                "v": 1,
                "p": (0, 0, 0),
                "u": 1,
                "w": 1,
                "ax": (0, 1, 0),
                "lr": 1,
            }
            defaults.update(kw)
            return pm.nurbsPlane(**defaults)

        def create_nurbs_torus(**kw):
            defaults = {
                "esw": 360,
                "ch": 1,
                "d": 3,
                "msw": 360,
                "ut": 0,
                "ssw": 0,
                "hr": 0.5,
                "p": (0, 0, 0),
                "s": 8,
                "r": 1,
                "tolerance": 0.01,
                "nsp": 4,
                "ax": (0, 1, 0),
            }
            defaults.update(kw)
            return pm.torus(**defaults)

        def create_nurbs_circle(**kw):
            defaults = {
                "c": (0, 0, 0),
                "ch": 1,
                "d": 3,
                "ut": 0,
                "sw": 360,
                "s": 8,
                "r": 1,
                "tolerance": 0.01,
                "nr": (0, 1, 0),
            }
            defaults.update(kw)
            return pm.circle(**defaults)

        def create_nurbs_square(**kw):
            defaults = {
                "c": (0, 0, 0),
                "ch": 1,
                "d": 3,
                "sps": 1,
                "sl1": 1,
                "sl2": 1,
                "nr": (0, 1, 0),
            }
            defaults.update(kw)
            return pm.nurbsSquare(**defaults)

        primitives = {
            "polygon": {
                "cube": create_poly_cube,
                "sphere": create_poly_sphere,
                "cylinder": create_poly_cylinder,
                "plane": create_poly_plane,
                "circle": create_circle,
                "cone": create_poly_cone,
                "pyramid": create_poly_pyramid,
                "torus": create_poly_torus,
                "pipe": create_poly_pipe,
                "geosphere": create_geosphere,
                "platonic solids": create_platonic_solids,
            },
            "nurbs": {
                "cube": create_nurbs_cube,
                "sphere": create_nurbs_sphere,
                "cylinder": create_nurbs_cylinder,
                "cone": create_nurbs_cone,
                "plane": create_nurbs_plane,
                "torus": create_nurbs_torus,
                "circle": create_nurbs_circle,
                "square": create_nurbs_square,
            },
            "light": {
                "ambient": lambda **kw: pm.ambientLight(**kw),
                "directional": lambda **kw: pm.directionalLight(**kw),
                "point": lambda **kw: pm.pointLight(**kw),
                "spot": lambda **kw: pm.spotLight(**kw),
                "area": lambda **kw: pm.shadingNode("areaLight", asLight=True, **kw),
                "volume": lambda **kw: pm.shadingNode(
                    "volumeLight", asLight=True, **kw
                ),
            },
        }

        # Create the primitive with remaining kwargs
        creation_func = primitives[baseType][subType]
        node = creation_func(**kwargs)

        # Post-creation operations
        if selection:
            if translate:
                XformUtils.move_to(node, selection)
            if scale:
                XformUtils.match_scale(node[0], selection, average=True)

        return NodeUtils.get_history_node(node[0])

    @staticmethod
    @CoreUtils.undoable
    def create_circle(
        axis="y", numPoints=5, radius=5, center=[0, 0, 0], mode=0, name="pCircle"
    ):
        """Create a circular polygon plane.

        Parameters:
            axis (str): 'x','y','z'
            numPoints(int): number of outer points
            radius=int
            center=[float3 list] - point location of circle center
            mode(int): 0 -no subdivisions, 1 -subdivide tris, 2 -subdivide quads

        Returns:
            (list) [transform node, history node] ex. [nt.Transform('polySurface1'), nt.PolyCreateFace('polyCreateFace1')]

        Example: create_circle(axis='x', numPoints=20, radius=8, mode='tri')
        """
        degree = 360 / float(numPoints)
        radian = math.radians(degree)  # or math.pi*degree/180 (pi * degrees / 180)

        vertexPoints = []
        for _ in range(numPoints):
            # print("deg:", degree,"\n", "cos:",math.cos(radian),"\n", "sin:",math.sin(radian),"\n", "rad:",radian)
            if axis == "x":  # x axis
                y = center[2] + (math.cos(radian) * radius)
                z = center[1] + (math.sin(radian) * radius)
                vertexPoints.append([0, y, z])
            if axis == "y":  # y axis
                x = center[2] + (math.cos(radian) * radius)
                z = center[0] + (math.sin(radian) * radius)
                vertexPoints.append([x, 0, z])
            else:  # z axis
                x = center[0] + (math.cos(radian) * radius)
                y = center[1] + (math.sin(radian) * radius)
                vertexPoints.append([x, y, 0])  # not working.

            # increment by original radian value that was converted from degrees
            radian = radian + math.radians(degree)
            # print(x,y,"\n")

        node = pm.ls(pm.polyCreateFacet(point=vertexPoints, name=name))
        # returns: ['Object name', 'node name']. pymel 'ls' converts those to objects.
        pm.polyNormal(node, normalMode=4)  # 4=reverse and propagate
        if mode == 1:
            pm.polySubdivideFacet(divisions=1, mode=1)
        if mode == 2:
            pm.polySubdivideFacet(divisions=1, mode=0)

        return node
