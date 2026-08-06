"""Primitive creation utilities for Maya.

This module provides functionality for creating various primitive objects
with flexible parameter handling.
"""

import maya.cmds as cmds
import maya.mel as mel
import math
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

        selection = cmds.ls(selection=True) or []

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
            return cmds.polyCube(**defaults)

        def create_poly_sphere(**kw):
            defaults = {
                "axis": axis,
                "radius": 5,
                "subdivisionsX": 12,
                "subdivisionsY": 12,
            }
            defaults.update(kw)
            return cmds.polySphere(**defaults)

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
            return cmds.polyCylinder(**defaults)

        def create_poly_plane(**kw):
            defaults = {
                "axis": axis,
                "width": 5,
                "height": 5,
                "subdivisionsX": 1,
                "subdivisionsY": 1,
            }
            defaults.update(kw)
            return cmds.polyPlane(**defaults)

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
            return cmds.polyCone(**defaults)

        def create_poly_pyramid(**kw):
            defaults = {
                "axis": axis,
                "sideLength": 5,
                "numberOfSides": 5,
                "subdivisionsHeight": 1,
                "subdivisionsCaps": 1,
            }
            defaults.update(kw)
            return cmds.polyPyramid(**defaults)

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
            return cmds.polyTorus(**defaults)

        def create_poly_helix(**kw):
            defaults = {
                "axis": axis,
                "coils": 3,
                "height": 5,
                "width": 5,
                "radius": 5,
                "subdivisionsAxis": 8,
                "subdivisionsCoil": 50,
                "subdivisionsCaps": 0,
            }
            defaults.update(kw)
            return cmds.polyHelix(**defaults)

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
            return cmds.polyPipe(**defaults)

        def create_geosphere(**kw):
            defaults = {"axis": axis, "radius": 5, "sideLength": 5, "polyType": 0}
            defaults.update(kw)
            return cmds.polyPrimitive(**defaults)

        def create_platonic_solids(**kw):
            return mel.eval("performPolyPrimitive PlatonicSolid 0;")

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
            return cmds.nurbsCube(**defaults)

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
            return cmds.sphere(**defaults)

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
            return cmds.cylinder(**defaults)

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
            return cmds.cone(**defaults)

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
            return cmds.nurbsPlane(**defaults)

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
            return cmds.torus(**defaults)

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
            return cmds.circle(**defaults)

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
            return cmds.nurbsSquare(**defaults)

        def create_arnold_light(node_type, **kw):
            """Create an MtoA light and give its transform a meaningful name.

            Maya auto-names the transform after the node type only for its OWN
            light types; ``shadingNode`` on an ``ai*`` light yields ``transform1``,
            so a scene of Arnold lights ends up as transform1..N. Rename to the
            node type (Maya uniquifies) to match how ``areaLight1`` reads.

            The SHAPE is renamed too. Renaming only the transform leaves both it
            and its shape as ``aiAreaLight1``, so ``cmds.ls("aiAreaLight1")``
            returns TWO nodes and every short-name lookup on that light is
            ambiguous. Maya's own convention splits them (``areaLight1`` /
            ``areaLightShape1``), so mirror it: insert ``Shape`` before the
            transform's trailing digits, keeping the pair's numbering in step.

            ``mtoa`` is loaded on demand rather than at import: loading it boots
            the whole Arnold renderer and costs seconds, which must not be paid
            by merely importing this module. ``EnvUtils.load_plugin`` raises
            ValueError when the plugin is missing, which the callers' existing
            try/except surfaces as a message box instead of a silent no-op.
            """
            import re

            from mayatk.env_utils._env_utils import EnvUtils

            EnvUtils.load_plugin("mtoa")
            # shadingNode returns the TRANSFORM (``transform1``) and puts the
            # requested name — or the node type — on the SHAPE. That is
            # backwards from every other primitive here, and from what Maya's
            # own lights do, so both nodes are renamed below. Verified live
            # (Maya 2025 / mtoa 7.3): a caller-supplied name landed on the
            # shape while the transform stayed ``transform1``.
            node = cmds.shadingNode(node_type, asLight=True, **kw)
            base = kw.get("name") or kw.get("n") or f"{node_type}1"

            def _shape_name(transform_name: str) -> str:
                """Maya's convention: ``Shape`` before the trailing digits."""
                return re.sub(r"(\d*)$", r"Shape\1", transform_name, count=1)

            # Shape first: it currently holds the very name the transform wants,
            # so renaming the transform first would collide with its own child
            # and Maya's uniquifier would silently push it out of step (the bug
            # that left the SECOND light onward with a transform-looking shape
            # name like ``aiAreaLight2``).
            for shape in cmds.listRelatives(node, shapes=True, fullPath=True) or []:
                cmds.rename(shape, _shape_name(base))
            node = cmds.rename(node, base)

            # Re-derive from the FINAL transform name: Maya still uniquifies it
            # when the scene already holds that name, and the pair must stay in
            # step for every light, not just the first.
            target = _shape_name(node.rsplit("|", 1)[-1])
            for shape in cmds.listRelatives(node, shapes=True, fullPath=True) or []:
                if shape.rsplit("|", 1)[-1] != target:
                    cmds.rename(shape, target)
            return node

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
                "helix": create_poly_helix,
                "pipe": create_poly_pipe,
                "tube": create_poly_pipe,
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
                "ambient": lambda **kw: cmds.ambientLight(**kw),
                "directional": lambda **kw: cmds.directionalLight(**kw),
                "point": lambda **kw: cmds.pointLight(**kw),
                "spot": lambda **kw: cmds.spotLight(**kw),
                "area": lambda **kw: cmds.shadingNode("areaLight", asLight=True, **kw),
                "volume": lambda **kw: cmds.shadingNode(
                    "volumeLight", asLight=True, **kw
                ),
            },
            # Arnold (MtoA) lights, kept as their own base type rather than mixed
            # into "light": Maya's native lights and Arnold's are NOT
            # interchangeable at render time (a native areaLight translates to a
            # quad with normalize on, which is why one at default intensity is
            # effectively invisible in a cm-scale scene), so the create list must
            # not blur which renderer a light belongs to.
            # Only the five node types that are actual DAG lights are listed --
            # probed against MtoA 5.4.5; aiLightBlocker / aiLightDecay are light
            # FILTERS and aiImagerLightMixer is an imager.
            "arnold": {
                "area": lambda **kw: create_arnold_light("aiAreaLight", **kw),
                "skydome": lambda **kw: create_arnold_light("aiSkyDomeLight", **kw),
                "mesh": lambda **kw: create_arnold_light("aiMeshLight", **kw),
                "photometric": lambda **kw: create_arnold_light(
                    "aiPhotometricLight", **kw
                ),
                "portal": lambda **kw: create_arnold_light("aiLightPortal", **kw),
            },
        }

        # Create the primitive with remaining kwargs
        creation_func = primitives[baseType][subType]
        node = creation_func(**kwargs)
        if isinstance(node, str):  # light creators return a bare node-name string
            node = [node]

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
        axis="y",
        numPoints=12,
        radius=5,
        center=[0, 0, 0],
        mode=0,
        name="pCircle",
        history=False,
    ):
        """Create a circular polygon plane.

        Parameters:
            axis (str): 'x','y','z'
            numPoints(int): number of outer points
            radius=int
            center=[float3 list] - point location of circle center
            mode(int): 0 -no subdivisions, 1 -subdivide tris, 2 -subdivide quads
            history(bool): If True, creates a circle with construction history (Planar Trim).

        Returns:
            (list) [transform node, history node]
        """
        ax = (0, 1, 0)
        if axis == "x":
            ax = (1, 0, 0)
        elif axis == "z":
            ax = (0, 0, 1)

        if history:
            # Create Linear NURBS circle to drive the polygon
            # Degree 1 ensures straight edges between points (polygonal shape)
            curve_trans, curve_shape = cmds.circle(
                center=(0, 0, 0),
                normal=ax,
                radius=radius,
                sections=numPoints,
                degree=1,
                name=str(name) + "_crv",
            )

            # Create planar polygon surface from the curve
            # This creates a single-sided mesh with history
            mesh_nodes = cmds.planarSrf(
                curve_trans, polygon=1, name=name, tolerance=1
            )  # [transform, historyNode]
            mesh_trans = mesh_nodes[0]

            # Hide the construction curve
            cmds.hide(curve_trans)

            # Match intended position
            if center != [0, 0, 0]:
                cmds.move(mesh_trans, center)
                cmds.move(curve_trans, center)

            return mesh_nodes

        # Default Behavior (No History, Manual Vertex Calc)
        degree = 360 / float(numPoints)
        radian = math.radians(degree)

        vertexPoints = []
        for _ in range(numPoints):
            if axis == "x":  # x axis
                y = center[2] + (math.cos(radian) * radius)
                z = center[1] + (math.sin(radian) * radius)
                vertexPoints.append([0, y, z])
            elif axis == "y":  # y axis
                x = center[2] + (math.cos(radian) * radius)
                z = center[0] + (math.sin(radian) * radius)
                vertexPoints.append([x, 0, z])
            else:  # z axis
                x = center[0] + (math.cos(radian) * radius)
                y = center[1] + (math.sin(radian) * radius)
                vertexPoints.append([x, y, 0])

            radian = radian + math.radians(degree)

        node = cmds.ls(cmds.polyCreateFacet(point=vertexPoints, name=name))
        cmds.polyNormal(node, normalMode=4)  # 4=reverse and propagate

        if mode == 1:
            cmds.polySubdivideFacet(divisions=1, mode=1)
        elif mode == 2:
            cmds.polySubdivideFacet(divisions=1, mode=0)

        return node
