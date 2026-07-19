# !/usr/bin/python
# coding=utf-8
import math
import random
from collections import defaultdict
from contextlib import contextmanager
from typing import Union, List, Dict, Tuple, Optional

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except Exception as error:
    cmds = None
    om = None
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils, as_strings, short_name


def _split_component(comp: str) -> Tuple[str, Optional[str], Optional[int]]:
    """Split ``"obj.vtx[5]"`` into ``("obj", "vtx", 5)``.

    Returns ``(node, comp_type, index)``. Whichever parts are absent yield
    ``None``. Used everywhere we need to query a component string without
    relying on object wrappers.
    """
    s = str(comp)
    if "." not in s:
        return s, None, None
    node, rest = s.split(".", 1)
    if "[" in rest and rest.endswith("]"):
        ctype = rest.split("[", 1)[0]
        idx_str = rest[rest.index("[") + 1 : -1]
        try:
            return node, ctype, int(idx_str)
        except ValueError:
            return node, ctype, None
    return node, rest, None


def _connected_components(comp: str, to_kw: str) -> List[str]:
    """Wrapper around ``cmds.polyListComponentConversion`` + flatten."""
    res = cmds.polyListComponentConversion(comp, **{to_kw: True}) or []
    return cmds.ls(res, flatten=True) or []


def _components_by_node(
    components: List[str], comp_type: str
) -> Dict[str, List[Tuple[str, int]]]:
    """Group flattened component names by their owning node.

    Returns ``{node: [(name, index), ...], ...}``. Entries whose component
    type doesn't match ``comp_type``, or that don't carry a single index
    (i.e. unflattened ranges), are dropped — callers pre-flatten via
    ``cmds.ls(..., flatten=True)``.
    """
    grouped = defaultdict(list)
    for name in components:
        node, ctype, idx = _split_component(name)
        if ctype == comp_type and idx is not None:
            grouped[node].append((name, idx))
    return dict(grouped)


def _mesh_dag_path(node: str):
    """Return the mesh-shape ``MDagPath`` for a transform or shape name."""
    sel = om.MSelectionList()
    sel.add(str(node))
    dag = sel.getDagPath(0)
    if dag.apiType() == om.MFn.kTransform:
        dag.extendToShape()
    return dag


class GetComponentsMixin:
    """ """

    component_mapping = [  # abv, singular, plural, full, int, hex
        ("vtx", "vertex", "vertices", "Polygon Vertex", 31, 0x0001),
        ("e", "edge", "edges", "Polygon Edge", 32, 0x8000),
        ("f", "face", "faces", "Polygon Face", 34, 0x0008),
        ("uv", "texture", "texture coordinates", "Polygon UV", 35, 0x0010),
        ("cv", "control vertex", "control vertices", "Control Vertex", 28, None),
        ("vtxf", "vertexFace", "vertexFaces", "Polygon Vertex Face", 70, None),
        (None, "edit point", "edit points", "Edit Point", 30, None),
        (None, "handle", "handles", "Handle", 0, None),
        (None, "nurbs surface", "nurbs surfaces", "Nurbs Curves On Surface", 11, None),
        (None, "subd mesh point", "subd mesh points", "Subdivision Mesh Point", 36, None),
        (None, "subd mesh edge", "subd mesh edges", "Subdivision Mesh Edge", 37, None),
        (None, "subd mesh face", "subd mesh faces", "Subdivision Mesh Face", 38, None),
        (None, "curve parameter point", "curve parameter points", "Curve Parameter Point", 39, None),
        (None, "curve knot", "curve knots", "Curve Knot", 40, None),
        (None, "surface parameter point", "surface parameter points", "Surface Parameter Point", 41, None),
        (None, "surface knot", "surface knots", "Surface Knot", 42, None),
        (None, "surface range", "surface ranges", "Surface Range", 43, None),
        (None, "trim surface edge", "trim surface edges", "Trim Surface Edge", 44, None),
        (None, "surface isoparm", "surface isoparms", "Surface Isoparm", 45, None),
        (None, "lattice point", "lattice points", "Lattice Point", 46, None),
        (None, "particle", "particles", "Particle", 47, None),
        (None, "scale pivot", "scale pivots", "Scale Pivot", 49, None),
        (None, "rotate pivot", "rotate pivots", "Rotate Pivot", 50, None),
        (None, "select handle", "select handles", "Select Handle", 51, None),
        (None, "nurbs surface face", "nurbs surface faces", "NURBS Surface Face", 72, None),
        (None, "subd mesh UV", "subd mesh UVs", "Subdivision Mesh UV", 73, None),
    ]

    @classmethod
    def get_component_type(cls, component, returned_type="abv"):
        """Get the type of a given component."""
        for a, s, p, f, i, h in cls.component_mapping:
            try:
                if cmds.filterExpand(component, sm=i):
                    if returned_type == "abv":
                        return a
                    elif returned_type == "full":
                        return f
                    elif returned_type == "int":
                        return i
                    elif returned_type == "hex":
                        return h
                    elif returned_type == "plural":
                        return p
                    else:
                        return s
            except Exception as e:
                print(
                    f'File "{__file__}" in get_component_type\n# Error: Not a valid component. #\n{e}{"(empty string)" if component == "" else ""}',
                )
                break
        return None

    @classmethod
    def convert_alias(cls, component_type, returned_type="abv"):
        """Return an alternate component alias for the given alias."""
        rtypes = ("abv", "singular", "plural", "full", "int", "hex")

        # Several rows carry None in their abv/hex slots — without this guard
        # a None lookup would falsely match the first such row.
        if component_type is None:
            return None
        for t in cls.component_mapping:
            if component_type in t:
                index = rtypes.index(returned_type)
                return t[index]
        return None

    @classmethod
    def convert_component_type(
        cls, components, component_type, returned_type="str", flatten=False
    ):
        """Convert component(s) to its sub-components of the given type."""
        d = {
            "vtx": "toVertex",
            "e": "toEdge",
            "uv": "toUV",
            "f": "toFace",
            "vtxf": "toVertexFace",
        }
        typ = cls.convert_alias(component_type)

        if typ not in d:
            return components
        components = cmds.polyListComponentConversion(
            as_strings(components), **{d[typ.lower()]: True}
        )
        return CoreUtils.convert_array_type(
            components, returned_type=returned_type, flatten=flatten
        )

    @staticmethod
    def get_component_index(components):
        """Extract the numerical index or indices of a component or components from their descriptor strings."""
        try:
            flattened = cmds.ls(as_strings(components), flatten=True) or []
            result = []
            for c in flattened:
                _, _, idx = _split_component(c)
                if idx is None:
                    raise ValueError(f"Cannot extract index from {c!r}")
                result.append(idx)
            single = not isinstance(components, (list, tuple, set))
            return result[0] if single and len(result) == 1 else result
        except AttributeError:
            raise ValueError("Input must be a valid component type.")

    @classmethod
    def convert_int_to_component(
        cls, obj, integers, component_type, returned_type="str", flatten=False
    ):
        """Convert the given integers to components of the given object."""
        candidates = cmds.ls(as_strings(obj), objectsOnly=True) or []
        if not candidates:
            return []
        objName = str(candidates[0]).split("|")[-1].split(":")[-1]

        def n(c):
            return "{}:{}".format(c[0], c[-1]) if len(c) > 1 else str(c[0])

        if not flatten:
            result = [
                "{}.{}[{}]".format(objName, component_type, n(c))
                for c in ptk.split_list(integers, "range")
            ]
        else:
            result = ["{}.{}[{}]".format(objName, component_type, c) for c in integers]

        return CoreUtils.convert_array_type(
            result, returned_type=returned_type, flatten=flatten
        )

    @classmethod
    def filter_components(cls, components, inc=None, exc=None, flatten=False):
        """Filter the given components."""
        inc = inc if inc is not None else []
        exc = exc if exc is not None else []
        typ = cls.get_component_type(components)
        etyp = CoreUtils.get_array_type(components)
        etyp_inc = CoreUtils.get_array_type(inc)
        etyp_exc = CoreUtils.get_array_type(exc)

        if etyp_inc == "int" or etyp_exc == "int":
            try:
                obj = (cmds.ls(as_strings(components), objectsOnly=True) or [None])[0]
                if obj is None:
                    raise IndexError
            except IndexError as e:
                print(
                    f'File "{__file__}" in filter_components\n# Error: Operation requires at least one component. #\n{e}',
                )
                return []

        if etyp_inc == "int":
            inc = cls.convert_int_to_component(obj, inc, typ)
        inc = cmds.ls(as_strings(inc), flatten=True) or []

        if etyp_exc == "int":
            exc = cls.convert_int_to_component(obj, exc, typ)
        exc = cmds.ls(as_strings(exc), flatten=True) or []

        components = cmds.ls(as_strings(components), flatten=True) or []

        # Component strings contain '[' and ']' which fnmatch treats as
        # character classes — direct set membership avoids that pitfall.
        if inc or exc:
            inc_set = set(inc)
            exc_set = set(exc)
            filtered = [
                c
                for c in components
                if (not inc_set or c in inc_set) and c not in exc_set
            ]
        else:
            filtered = components

        result = CoreUtils.convert_array_type(
            filtered, returned_type=etyp, flatten=flatten
        )
        return result

    @classmethod
    def get_components(
        cls,
        objects,
        component_type,
        returned_type="str",
        inc=None,
        exc=None,
        randomize=0,
        flatten=False,
    ):
        """Get the components of the given type from the given object(s)."""
        components = cls.convert_component_type(objects, component_type)

        if inc or exc:
            components = cls.filter_components(components, inc=inc, exc=exc)

        if randomize:
            flat = cmds.ls(as_strings(components), flatten=True) or []
            count = max(1, int(len(flat) * float(randomize)))
            components = random.sample(flat, min(count, len(flat)))

        result = CoreUtils.convert_array_type(
            components, returned_type=returned_type, flatten=flatten
        )
        return result


class Components(GetComponentsMixin, ptk.HelpMixin):
    """ """

    # Tolerance (degrees) for classifying an edge's normal angle against a
    # threshold. World-space face normals carry sub-ulp float drift after any
    # transform+freeze, so a mesh's true right-angle edges measure 89.9999…°
    # rather than exactly 90°. Comparing with a strict boundary would then miss
    # them; nudging the boundary down by this epsilon absorbs the drift. 1e-3°
    # is ~45× the observed drift yet far below any artistically meaningful
    # angle, so it never merges genuinely distinct edges.
    _ANGLE_MATCH_EPS: float = 1e-3

    @staticmethod
    def map_components_to_objects(components_list):
        """Map a list of components to their respective objects.

        Returns:
            dict: ``{node_name: [component, ...], ...}`` keyed by the node
            name (leaf, namespace stripped) and component-strings as values.
        """
        result = defaultdict(list)
        for component in cmds.ls(as_strings(components_list), flatten=True) or []:
            node, _, _ = _split_component(component)
            if not node:
                continue
            key = node.split("|")[-1].split(":")[-1]
            result[key].append(component)
        return dict(result)

    @staticmethod
    def _group_by_shared_keys(items):
        """Union-find grouping: items sharing any key merge transitively.

        Parameters:
            items (list): ``(name, keys)`` pairs where ``keys`` is an iterable
                of hashable adjacency keys (e.g. vertex or edge ids).

        Returns:
            list: Sets of names, ordered by first appearance.
        """
        parent = {}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]  # path halving
                x = parent[x]
            return x

        key_owner = {}
        for name, keys in items:
            parent.setdefault(name, name)
            for key in keys:
                owner = key_owner.setdefault(key, name)
                if owner != name:
                    parent[find(owner)] = find(name)

        groups, order = {}, []
        for name, _ in items:
            root = find(name)
            if root not in groups:
                groups[root] = set()
                order.append(root)
            groups[root].add(name)
        return [groups[r] for r in order]

    @classmethod
    def get_contiguous_edges(cls, components):
        """Get a list containing sets of adjacent edges.

        Edges group transitively by shared vertex. Topology is read through
        one edge iterator per mesh rather than per-edge
        polyListComponentConversion round-trips, so large selections stay
        fast and the returned names keep cmds.ls' transform-prefixed form.
        """
        edges = (
            cmds.ls(
                cmds.polyListComponentConversion(as_strings(components), toEdge=True)
                or [],
                flatten=True,
            )
            or []
        )

        items = []
        for node, indexed in _components_by_node(edges, "e").items():
            edge_it = om.MItMeshEdge(_mesh_dag_path(node))
            for name, idx in indexed:
                edge_it.setIndex(idx)
                items.append(
                    (name, ((node, edge_it.vertexId(0)), (node, edge_it.vertexId(1))))
                )
        return cls._group_by_shared_keys(items)

    @classmethod
    def get_contiguous_islands(cls, faces):
        """Get a list containing sets of adjacent polygon faces grouped by islands.

        Faces group transitively by shared edge (corner-touching faces stay
        separate), matching Maya's shell semantics for a face subset.
        """
        faces = cmds.ls(as_strings(faces), flatten=True) or []

        items = []
        for node, indexed in _components_by_node(faces, "f").items():
            face_it = om.MItMeshPolygon(_mesh_dag_path(node))
            for name, idx in indexed:
                face_it.setIndex(idx)
                items.append((name, [(node, e) for e in face_it.getEdges()]))
        return cls._group_by_shared_keys(items)

    @staticmethod
    def get_islands(obj, returned_type="str", flatten=False):
        """Get the group of components in each separate island of a combined mesh."""
        candidates = cmds.ls(as_strings(obj)) or []
        if not candidates:
            raise ValueError(f"Object not found: {obj}")
        obj_name = candidates[0]

        if not cmds.objectType(obj_name, isAType="transform") or not cmds.listRelatives(
            obj_name, type="mesh", shapes=True, noIntermediate=True
        ):
            raise ValueError(f"Expected a single mesh transform, got {obj_name}")

        num_faces = cmds.polyEvaluate(obj_name, face=True)
        unprocessed = set(range(num_faces))

        while unprocessed:
            index = next(iter(unprocessed))
            faces = cmds.polySelect(obj_name, extendToShell=index, noSelection=True) or []

            if returned_type == "str":
                yield ["{}.f[{}]".format(obj_name, i) for i in faces]

            elif returned_type == "int":
                yield [i for i in faces]

            elif returned_type == "obj":
                yield [
                    (cmds.ls("{}.f[{}]".format(obj_name, i)) or [None])[0]
                    for i in faces
                ]

            unprocessed.difference_update(faces)

    @classmethod
    def get_border_components(
        cls,
        components,
        returned_type="str",
        component_border=False,
    ):
        """Get border components from given component(s) or a polygon object based on connectivity."""
        components = cmds.ls(as_strings(components), flatten=True) or []
        if not components:
            raise ValueError("No valid components given.")

        component_type = cls.get_component_type(components[0], "abv")
        if component_type is None:
            raise ValueError(f"Unrecognized component_type: {component_type}")

        border_components = []

        # Helpers (string-based) ----------------------------------------------
        def edge_face_count(edge):
            return len(_connected_components(edge, "toFace"))

        def vertex_connected_edges(vtx):
            return _connected_components(vtx, "toEdge")

        def vertex_connected_vertices(vtx):
            edges = _connected_components(vtx, "toEdge")
            verts = (
                cmds.ls(
                    cmds.polyListComponentConversion(
                        edges, fromEdge=True, toVertex=True
                    )
                    or [],
                    flatten=True,
                )
                or []
            )
            return [v for v in verts if v != vtx]

        def face_connected_edges(face):
            return _connected_components(face, "toEdge")

        # ---------------------------------------------------------------------

        if component_border:
            vertex_components = cls.convert_component_type(
                components, "vtx", "str", flatten=True
            ) or []
            vertex_components_set = set(vertex_components)

            def is_border_vertex(comp):
                connected_v = vertex_connected_vertices(comp)
                connected_e = vertex_connected_edges(comp)
                return any(cc not in vertex_components_set for cc in connected_v) or any(
                    edge_face_count(e) == 1 for e in connected_e
                )

            border_components = [
                comp for comp in vertex_components if is_border_vertex(comp)
            ]

            if component_type == "e":
                original_edges = cls.convert_component_type(
                    components, "e", "str", flatten=True
                ) or []
                # Count how many of the original edges each connected face has.
                face_edge_count = {}
                for edge in original_edges:
                    for face in _connected_components(edge, "toFace"):
                        face_edge_count[face] = face_edge_count.get(face, 0) + 1

                def is_border_edge(edge):
                    faces = _connected_components(edge, "toFace")
                    return (
                        any(face_edge_count.get(f, 0) == 1 for f in faces)
                        or len(faces) == 1
                    )

                border_components = [
                    edge for edge in original_edges if is_border_edge(edge)
                ]
            elif component_type == "f":
                border_faces = cls.convert_component_type(
                    border_components, "f", "str", flatten=True
                ) or []
                comp_set = set(components)
                border_components = [
                    comp for comp in border_faces if comp in comp_set
                ]
        else:
            if component_type == "e":
                border_components = [
                    edge for edge in components if edge_face_count(edge) == 1
                ]
            elif component_type == "vtx":
                border_components = [
                    vtx
                    for vtx in components
                    if any(
                        edge_face_count(e) == 1 for e in vertex_connected_edges(vtx)
                    )
                ]
            elif component_type == "f":
                border_faces = cls.convert_component_type(
                    components, "f", "str", flatten=True
                ) or []
                border_components = [
                    face
                    for face in border_faces
                    if any(edge_face_count(e) == 1 for e in face_connected_edges(face))
                ]
            else:
                raise ValueError(f"Unrecognized component_type: {component_type}")

        result = CoreUtils.convert_array_type(
            border_components, returned_type=returned_type
        )
        return result

    @staticmethod
    def get_furthest_vertices(vertices_a, vertices_b):
        """Determine the two furthest apart vertices, one from each of the two provided lists."""
        list_a = cmds.ls(as_strings(vertices_a), flatten=True) or []
        list_b = cmds.ls(as_strings(vertices_b), flatten=True) or []

        # Query each position once — not once per pair.
        positions_a = [
            (v, om.MVector(*cmds.pointPosition(v, world=True))) for v in list_a
        ]
        positions_b = [
            (v, om.MVector(*cmds.pointPosition(v, world=True))) for v in list_b
        ]

        max_dist = 0
        result_a = result_b = None
        for v1, v1_pos in positions_a:
            for v2, v2_pos in positions_b:
                dist = (v1_pos - v2_pos).length()
                if dist > max_dist:
                    max_dist = dist
                    result_a = v1
                    result_b = v2
        return (result_a, result_b)

    @classmethod
    def get_closest_verts(cls, a, b, tolerance=1000):
        """Find the two closest vertices between the two sets of vertices."""
        a = CoreUtils.convert_array_type(a, returned_type="str", flatten=True)
        b = CoreUtils.convert_array_type(b, returned_type="str", flatten=True)

        # Query each position once — not once per pair.
        b_positions = [(v2, cmds.pointPosition(v2, world=True)) for v2 in b]

        vert_pairs_and_distance = {}
        for v1 in a:
            v1_pos = cmds.pointPosition(v1, world=True)
            for v2, v2_pos in b_positions:
                distance = ptk.distance_between_points(v1_pos, v2_pos)
                if distance < tolerance:
                    vert_pairs_and_distance[(v1, v2)] = distance

        sorted_pairs = sorted(vert_pairs_and_distance.items(), key=lambda kv: kv[1])
        return [pair for pair, _ in sorted_pairs]

    @staticmethod
    @contextmanager
    def closest_point_probe(shape):
        """Temporary ``closestPointOnMesh`` node wired to *shape* for
        WORLD-space queries (``worldMatrix -> inputMatrix``), deleted on
        exit. Callers set ``.inPosition`` (world) and read ``.position`` /
        ``.normal`` / ``.closestVertexIndex``.
        """
        cpom = cmds.createNode("closestPointOnMesh")
        try:
            cmds.connectAttr(f"{shape}.outMesh", f"{cpom}.inMesh")
            cmds.connectAttr(f"{shape}.worldMatrix[0]", f"{cpom}.inputMatrix")
            yield cpom
        finally:
            cmds.delete(cpom)

    @classmethod
    @CoreUtils.undoable
    def get_closest_vertex(
        cls, vertices, obj, tolerance=0.0, freeze_transforms=False, returned_type="str"
    ):
        """Find the closest vertex of the given object for each vertex in the list of given vertices."""
        vertices = CoreUtils.convert_array_type(
            vertices, returned_type="str", flatten=True
        )

        obj = str(obj)
        if freeze_transforms:
            cmds.makeIdentity(obj, apply=True)

        # noIntermediate: with construction history / deformers the first
        # child shape can be the hidden "Orig" shape — sampling that would
        # return pre-deformation vertices.
        shapes = (
            cmds.listRelatives(obj, children=True, shapes=True, noIntermediate=True)
            or []
        )
        if not shapes:
            return {}
        target_shape = shapes[0]

        # World-space probe: without the worldMatrix hookup the node read
        # the world-space query positions in the target's LOCAL space and
        # returned the wrong vertex on any transformed target.
        with cls.closest_point_probe(target_shape) as cpm_node:
            closest_verts = {}
            for v1 in vertices:
                v1_pos = cmds.pointPosition(v1, world=True)
                cmds.setAttr(
                    f"{cpm_node}.inPosition",
                    v1_pos[0],
                    v1_pos[1],
                    v1_pos[2],
                    type="double3",
                )

                index = cmds.getAttr(f"{cpm_node}.closestVertexIndex")
                v2 = f"{target_shape}.vtx[{index}]"

                v2_pos = cmds.pointPosition(v2, world=True)
                distance = ptk.distance_between_points(v1_pos, v2_pos)

                if not tolerance or distance < tolerance:
                    closest_verts[v1] = CoreUtils.convert_array_type(
                        v2, returned_type=returned_type
                    )[0]

        return closest_verts

    @staticmethod
    def get_vertices_within_threshold(reference_vertices, max_distance):
        """Categorizes vertices of a mesh based on their distance from the first reference vertex."""
        reference_vertices = cmds.ls(as_strings(reference_vertices), flatten=True) or []
        if not reference_vertices:
            return ([], [])

        reference_point = om.MVector(*cmds.pointPosition(reference_vertices[0], world=True))
        node, _, _ = _split_component(reference_vertices[0])

        # Use the shape if the descriptor is on a transform
        shapes = cmds.listRelatives(node, shapes=True, noIntermediate=True) or []
        mesh = shapes[0] if shapes else node

        # One world-space query for every point instead of a pointPosition
        # command per vertex.
        points = om.MFnMesh(_mesh_dag_path(mesh)).getPoints(om.MSpace.kWorld)

        inside = []
        outside = []
        for i, point in enumerate(points):
            vtx = f"{mesh}.vtx[{i}]"
            distance = (om.MVector(point) - reference_point).length()
            if distance <= max_distance:
                inside.append(vtx)
            else:
                outside.append(vtx)

        return (inside, outside)

    @staticmethod
    def adjusted_distance_between_vertices(
        p1, p2, adjust: float = 0.0, as_percentage: bool = False
    ):
        """Calculate adjusted distance between two points/vertices."""
        # Resolve component descriptors to positions if needed
        if isinstance(p1, str) and "." in p1:
            p1 = cmds.pointPosition(p1, world=True)
        if isinstance(p2, str) and "." in p2:
            p2 = cmds.pointPosition(p2, world=True)

        dist = ptk.distance_between_points(p1, p2)

        if as_percentage:
            dist *= 1 + adjust / 100
        else:
            dist += adjust

        return dist

    @staticmethod
    @CoreUtils.undoable
    def bridge_connected_edges(edges) -> None:
        """Bridges two connected edges."""
        edges = cmds.ls(cmds.filterExpand(edges, sm=32) or [], flatten=True) or []
        if not edges or len(edges) < 2:
            raise ValueError(
                "Invalid input: At least two edges are required for bridging."
            )

        # Vertices of each edge
        verts_a = set(_connected_components(edges[0], "toVertex"))
        verts_b = set(_connected_components(edges[1], "toVertex"))

        common = list(verts_a & verts_b)
        if not common:
            raise ValueError(
                "Cannot bridge edges: The provided edges do not share a common vertex."
            )
        common_vertex = common[0]

        cmds.polyExtrudeEdge(edges[0], ltz=0.1, ls=(1, 1, 1))
        cmds.refresh()

        new_vertices = (
            cmds.ls(
                cmds.polyListComponentConversion(toVertex=True) or [], flatten=True
            )
            or []
        )
        new_vertex_set = set(new_vertices)

        # Find the new vertex connected to the common vertex
        connected_new_vertex = None
        for nv in new_vertices:
            connected_to_nv = set(
                cmds.ls(
                    cmds.polyListComponentConversion(
                        cmds.polyListComponentConversion(
                            nv, fromVertex=True, toEdge=True
                        )
                        or [],
                        fromEdge=True,
                        toVertex=True,
                    )
                    or [],
                    flatten=True,
                )
                or []
            )
            if common_vertex in connected_to_nv:
                connected_new_vertex = nv
                break

        if connected_new_vertex is None:
            return

        cmds.move(
            *cmds.pointPosition(common_vertex),
            connected_new_vertex,
            absolute=True,
        )
        cmds.polyMergeVertex([connected_new_vertex, common_vertex], d=0.0, am=True)

        remaining = list(new_vertex_set - {connected_new_vertex})
        if not remaining:
            return
        remaining_new_vertex = remaining[0]
        target_remaining = list(verts_b - {common_vertex})
        if not target_remaining:
            return
        target_vertex_edge2 = target_remaining[0]

        cmds.move(
            *cmds.pointPosition(target_vertex_edge2),
            remaining_new_vertex,
            absolute=True,
        )
        cmds.polyMergeVertex(
            [remaining_new_vertex, target_vertex_edge2], d=0.0, am=True
        )

        cmds.select(clear=True)

    @classmethod
    def get_edge_path(
        cls, components, path="edgeLoop", returned_type="str", flatten=False
    ):
        """Query the polySelect command for the components along different edge paths."""
        objs = cmds.ls(as_strings(components), objectsOnly=True) or []
        if not objs:
            return []
        obj = objs[0]

        cnums = cls.convert_component_type(
            components, "edge", returned_type="int", flatten=True
        )

        if len(cnums) < 2 and path in ("edgeRingPath", "edgeLoopPath"):
            print(
                f'File "{__file__}" in get_edge_path\n# Error: Operation requires at least two components. #\n Edges given: {cnums}',
            )
            return []

        if path == "edgeRing":
            edgesLong = cmds.polySelect(obj, q=True, edgeRing=cnums)

        elif path == "edgeRingPath":
            edgesLong = cmds.polySelect(
                obj, q=True, edgeRingPath=(cnums[0], cnums[1])
            )
            if not edgesLong:
                print(
                    f'File "{__file__}" in get_edge_path\n# Error: get_edge_path: Operation requires two edges that are on the same edge ring.\n\tEdges given: {cnums[0]}, {cnums[1]}',
                )
                return []

        elif path == "edgeLoopPath":
            edgesLong = cmds.polySelect(
                obj, q=True, edgeLoopPath=(cnums[0], cnums[1])
            )
            if not edgesLong:
                print(
                    f'File "{__file__}" in get_edge_path\n# Error: get_edge_path: Operation requires two edges that are on the same edge loop.\n\tEdges given: {cnums[0]}, {cnums[1]}',
                )
                return []
        else:  # EdgeLoop
            edgesLong = cmds.polySelect(obj, q=True, edgeLoop=cnums)

        objName = obj.split("|")[-1].split(":")[-1]
        result = ptk.remove_duplicates(
            ["{}.e[{}]".format(objName, e) for e in (edgesLong or [])]
        )
        return CoreUtils.convert_array_type(
            result, returned_type=returned_type, flatten=flatten
        )

    @classmethod
    def get_shortest_path(cls, components, flatten=False):
        """Calculate the shortest path between two specified edge or vertex components within the same 3D object."""
        components = cmds.ls(as_strings(components), flatten=True) or []
        if len(components) != 2:
            raise ValueError(f"Exactly two components are required. Got: {components}")
        a, b = components

        node_a, _, _ = _split_component(a)
        node_b, _, _ = _split_component(b)
        if node_a != node_b:
            raise ValueError("Components must belong to the same object")

        a_type = cls.get_component_type(a, returned_type="abv")
        b_type = cls.get_component_type(b, returned_type="abv")
        if a_type != b_type:
            raise ValueError("Both components must be of the same type")

        if a_type == "e":
            a_vertices = _connected_components(a, "toVertex")
            b_vertices = _connected_components(b, "toVertex")

            selected_a, selected_b = cls.get_furthest_vertices(a_vertices, b_vertices)
            a_index = cls.get_component_index(selected_a)
            b_index = cls.get_component_index(selected_b)

        elif a_type == "vtx":
            a_index = cls.get_component_index(a)
            b_index = cls.get_component_index(b)
        else:
            raise ValueError("Unsupported component type for path calculation")

        path_indices = cmds.polySelect(node_a, q=True, shortestEdgePath=[a_index, b_index]) or []

        if a_type == "e":
            objName = node_a.split("|")[-1].split(":")[-1]
            result = [a] + [f"{objName}.e[{idx}]" for idx in path_indices] + [b]
        elif a_type == "vtx":
            result = []
            objName = node_a.split("|")[-1].split(":")[-1]
            for idx in path_indices:
                edge = f"{objName}.e[{idx}]"
                vertices = cmds.polyListComponentConversion(
                    edge, fromEdge=True, toVertex=True
                ) or []
                result.extend(cmds.ls(vertices, flatten=True) or [])
        else:
            result = list(path_indices)

        return ptk.remove_duplicates(result)

    @staticmethod
    def get_normal(face):
        """Get the normal of a face in world space.

        Returns:
            om.MVector: The normal of the face in world space.
        """
        face = str(face)
        node, ctype, idx = _split_component(face)
        if ctype != "f" or idx is None:
            raise TypeError(f"Input must be a face component descriptor, got {face!r}.")

        sel_list = om.MSelectionList()
        sel_list.add(node)
        dag_path = sel_list.getDagPath(0)
        # If we got the transform, walk to the shape.
        if dag_path.apiType() == om.MFn.kTransform:
            dag_path.extendToShape()

        mesh_fn = om.MFnMesh(dag_path)
        normal = mesh_fn.getPolygonNormal(idx, om.MSpace.kWorld)
        return normal

    @staticmethod
    def get_normal_vector(x):
        """Get the normal vectors of the given polygon object(s) or its components.

        Returns:
            dict: ``{face_index: [x, y, z], ...}`` parsed from polyInfo lines
            of the form ``"FACE_NORMAL   12: 0.000000 1.000000 0.000000"``.
        """
        objs = cmds.ls(as_strings(x)) or []
        normals = cmds.polyInfo(objs, faceNormals=True) or []

        dct = {}
        for n in normals:
            parts = n.split()
            key = int(parts[1].rstrip(":"))
            dct[key] = [float(v) for v in parts[-3:]]

        return dct

    @classmethod
    def _edge_normal_angles(cls, edges: List[str]) -> Dict[str, float]:
        """Map each flattened edge name to the angle (degrees) between its two
        adjacent world-space face normals.

        Batched: one MFnMesh / MItMeshEdge per mesh with face normals cached,
        instead of two MSelectionList + MFnMesh constructions per edge — the
        difference between milliseconds and minutes on dense meshes. Border
        edges (a single adjacent face) map to 0.0. Non-edge entries are
        skipped and simply absent from the result.
        """
        result: Dict[str, float] = {}
        for node, indexed in _components_by_node(edges, "e").items():
            dag = _mesh_dag_path(node)
            mesh_fn = om.MFnMesh(dag)
            edge_it = om.MItMeshEdge(dag)
            face_normals: Dict[int, "om.MVector"] = {}
            for name, idx in indexed:
                edge_it.setIndex(idx)
                faces = edge_it.getConnectedFaces()
                if len(faces) != 2:
                    result[name] = 0.0
                    continue
                normals = []
                for f in faces:
                    normal = face_normals.get(f)
                    if normal is None:
                        normal = mesh_fn.getPolygonNormal(f, om.MSpace.kWorld)
                        face_normals[f] = normal
                    normals.append(normal)
                result[name] = math.degrees(normals[0].angle(normals[1]))
        return result

    @classmethod
    def get_normal_angle(cls, edges) -> Union[float, List[float]]:
        """Get the angle between the normals of the faces connected by one or more edges."""
        edges_list = cmds.ls(as_strings(edges), flatten=True) or []
        angles = cls._edge_normal_angles(edges_list)
        result = [angles[e] for e in edges_list if e in angles]
        return ptk.format_return(result, edges)

    @classmethod
    def get_edges_by_normal_angle(
        cls,
        objects,
        low_angle: float = 0,
        high_angle: float = 180,
        return_angles: bool = False,
    ):
        """Return edges whose adjacent face-normal angle falls within a range.

        The range is matched with a small tolerance (``_ANGLE_MATCH_EPS``) on
        both ends so an edge whose *true* angle sits exactly on a bound (e.g. a
        cube's 90° edges against a bound of 90) is reliably included despite the
        sub-ulp float drift world-space face normals carry after a
        transform+freeze — otherwise those edges would silently fall outside.
        """
        edges = cmds.ls(
            cls.convert_component_type(
                objects, "edge", returned_type="str", flatten=True
            )
            or [],
            flatten=True,
        ) or []

        low = low_angle - cls._ANGLE_MATCH_EPS
        high = high_angle + cls._ANGLE_MATCH_EPS

        edge_angles = cls._edge_normal_angles(edges)

        filtered_edges = []
        for edge in edges:
            # Absent from the batch result = not a parseable edge component
            # (e.g. a stale name). Skip rather than crashing the selection.
            angle = edge_angles.get(str(edge))
            if angle is None:
                continue
            if low <= angle <= high:
                filtered_edges.append(edge)

        if return_angles:
            return filtered_edges, edge_angles
        return filtered_edges

    @classmethod
    @CoreUtils.undoable
    def set_edge_hardness(
        cls,
        objects,
        angle_threshold: float,
        upper_hardness: float = None,
        lower_hardness: float = None,
        unlock_normals: bool = False,
    ) -> List[str]:
        """Set edge hardness based on normal angle thresholds.

        When ``unlock_normals`` is True, each affected mesh is reset to a
        clean shading baseline before re-applying hardness:

        1. ``polyNormalPerVertex -unFreezeNormal`` releases the lock on
           imported (FBX/Marmoset) vertex normals.
        2. ``polySetToFaceNormal`` resets per-vertex normals to face-aligned
           values so Maya treats them as recomputable; without this, Maya
           keeps the previously-baked values as "user-tweaked" and
           polySoftEdge only flips the edge flag without changing shading.
        3. ``polySoftEdge`` then drives the final shading.

        When ``unlock_normals`` is False the meshes are pre-flighted for
        locked vertex normals. Locked normals silently block ``polySoftEdge``
        from updating shading, so rather than no-op invisibly the operation is
        aborted before any edits: a console warning is emitted and the
        offending object paths are returned so the caller can surface a
        user-facing message.

        Returns:
            list: Unambiguous object paths skipped because their vertex
            normals are locked and ``unlock_normals`` was False. Empty when
            the operation ran (or had nothing to do).
        """
        if upper_hardness is None and lower_hardness is None:
            return []

        all_edges, edge_angles = cls.get_edges_by_normal_angle(
            objects, 0, 180, return_angles=True
        )

        object_to_edges = cls.map_components_to_objects(all_edges)

        # Resolve every unambiguous DAG path spanned by the selection up front.
        # A map_components_to_objects key is a leaf name that can collide across
        # the scene (two `pCube1`s under different parents merge into one key),
        # so derive the full path set from the path-qualified edge strings — a
        # single key can front more than one real object.
        object_to_paths = {}
        for obj, edges in object_to_edges.items():
            nodes = {e.split(".")[0] for e in edges} or {obj}
            object_to_paths[obj] = [
                path
                for node in nodes
                for path in (cmds.ls(node, long=True, objectsOnly=True) or [node])
            ]

        # Guard: locked vertex normals (FBX/Marmoset imports) silently block
        # polySoftEdge from updating shading. When the caller opts out of
        # unlocking, abort before any edits and report every offending object.
        if not unlock_normals:
            locked_objects = [
                path
                for paths in object_to_paths.values()
                for path in paths
                if any(
                    cmds.polyNormalPerVertex(f"{path}.vtx[*]", q=True, freezeNormal=True)
                    or []
                )
            ]
            if locked_objects:
                cmds.warning(
                    "set_edge_hardness: aborted — locked vertex normals on "
                    f"{', '.join(short_name(o) for o in locked_objects)}. "
                    "Enable 'Unlock Normals' to release them before applying "
                    "hardness."
                )
                return locked_objects

        saved_selection = cmds.ls(sl=True, long=True) or []

        # Split at a boundary nudged below the threshold so an edge whose *true*
        # angle equals it (e.g. a cube's 90° edges) still classifies as "upper".
        # See _ANGLE_MATCH_EPS for why the raw threshold is unreliable.
        boundary = angle_threshold - cls._ANGLE_MATCH_EPS

        for obj, edges in object_to_edges.items():
            if unlock_normals:
                for obj_path in object_to_paths[obj]:
                    cmds.polyNormalPerVertex(f"{obj_path}.vtx[*]", unFreezeNormal=True)
                    # polySetToFaceNormal acts on the active selection.
                    cmds.select(obj_path, replace=True)
                    cmds.polySetToFaceNormal()
            upper_edges = (
                [e for e in edges if edge_angles[str(e)] >= boundary]
                if upper_hardness is not None
                else []
            )
            lower_edges = (
                [e for e in edges if edge_angles[str(e)] < boundary]
                if lower_hardness is not None
                else []
            )

            if upper_edges:
                cmds.polySoftEdge(upper_edges, angle=upper_hardness, ch=True)
            if lower_edges:
                cmds.polySoftEdge(lower_edges, angle=lower_hardness, ch=True)

        # polySoftEdge / polySetToFaceNormal mutate the active selection.
        # Restore the caller's selection so downstream UI (HUD counts, etc.)
        # don't see thousands of accidentally-selected edges.
        if saved_selection:
            cmds.select(saved_selection, replace=True)
        else:
            cmds.select(clear=True)

        return []

    @classmethod
    def get_faces_with_similar_normals(
        cls,
        faces,
        transforms=None,
        similar_faces=None,
        range_x=0.1,
        range_y=0.1,
        range_z=0.1,
        returned_type="str",
    ):
        """Filter for faces with normals that fall within an X,Y,Z tolerance.

        Searches across every transform spanned by ``faces`` (or ``transforms``
        if provided), so multi-object selections are honored. Each matching
        face appears at most once in the returned list.
        """
        faces = cmds.ls(as_strings(faces), flatten=True) or []
        similar_faces = list(similar_faces) if similar_faces else []
        if not faces:
            return similar_faces

        if not transforms:
            transforms = list(
                dict.fromkeys(
                    t for face in faces for t in (cmds.ls(face, objectsOnly=True) or [])
                )
            )

        # Source normals — one batched polyInfo call regardless of source count.
        source_normals = list(cls.get_normal_vector(faces).values())
        if not source_normals:
            return similar_faces

        seen = set(similar_faces)
        for node in transforms:
            node_faces = (
                cls.get_components(
                    node, "faces", returned_type=returned_type, flatten=True
                )
                or []
            )
            if not node_faces:
                continue
            # One polyInfo per transform; keyed by face index.
            normals_by_idx = cls.get_normal_vector(node_faces)
            for f in node_faces:
                if f in seen:
                    continue
                try:
                    idx = int(str(f).rsplit("[", 1)[1].rstrip("]"))
                except (IndexError, ValueError):
                    continue
                n = normals_by_idx.get(idx)
                if n is None:
                    continue
                nX, nY, nZ = n
                for sX, sY, sZ in source_normals:
                    if (
                        abs(sX - nX) <= range_x
                        and abs(sY - nY) <= range_y
                        and abs(sZ - nZ) <= range_z
                    ):
                        similar_faces.append(f)
                        seen.add(f)
                        break

        return similar_faces

    @classmethod
    @CoreUtils.undoable
    def average_normals(cls, objects, by_uv_shell=False):
        """Average the normals of the given objects."""
        from mayatk.uv_utils._uv_utils import UvUtils

        components_dict = cls.map_components_to_objects(objects)

        for obj, components in components_dict.items():
            if by_uv_shell:
                uv_shell_sets = UvUtils.get_uv_shell_sets(components)
                for uv_set in uv_shell_sets:
                    cmds.polySoftEdge(uv_set, a=180)
            else:
                if components:
                    cmds.polySoftEdge(components, a=180)
                else:
                    cmds.polySoftEdge(obj, a=180)

    @staticmethod
    @CoreUtils.undoable
    def transfer_normals(objects, space: str = "world"):
        """Transfer vertex normals from source mesh to target meshes."""
        space_map = {"world": 0, "local": 1, "component": 4, "topology": 5}
        if space not in space_map:
            valid_spaces = ", ".join(space_map.keys())
            raise ValueError(f"space parameter must be one of: {valid_spaces}")

        objs: List[str] = []
        for obj in cmds.ls(as_strings(objects)) or []:
            ntype = cmds.objectType(obj)
            if ntype == "mesh":
                parents = cmds.listRelatives(obj, parent=True, fullPath=True) or []
                if parents:
                    objs.append(parents[0])
            elif ntype == "transform":
                shapes = cmds.listRelatives(obj, shapes=True, noIntermediate=True) or []
                if shapes and cmds.objectType(shapes[0]) == "mesh":
                    objs.append(obj)
        if len(objs) < 2:
            raise ValueError(
                "At least one source and one target mesh must be polygonal meshes."
            )

        source_mesh, *target_meshes = objs
        sample_space_value = space_map[space]

        source_vertices = cmds.polyEvaluate(source_mesh, vertex=True)
        for target_mesh in target_meshes:
            target_vertices = cmds.polyEvaluate(target_mesh, vertex=True)

            if source_vertices != target_vertices:
                raise ValueError(
                    "Source and target meshes do not have the same topology"
                )

            cmds.transferAttributes(
                source_mesh,
                target_mesh,
                transferNormals=1,
                sampleSpace=sample_space_value,
                searchMethod=3,
                colorBorders=1,
            )

            cmds.delete(target_mesh, constructionHistory=True)

    @classmethod
    def filter_components_by_connection_count(
        cls, components, num_of_connected=(0, 2), connected_type="", returned_type="str"
    ):
        """Get a list of components filtered by the number of their connected components."""
        if isinstance(num_of_connected, (tuple, list)) and len(num_of_connected) == 2:
            lowRange, highRange = num_of_connected
        elif isinstance(num_of_connected, int):
            lowRange = highRange = num_of_connected
        else:
            raise TypeError(
                f"num_of_connected expected an int or two int tuple, got {type(num_of_connected)}"
            )

        typ = cls.get_component_type(components)
        if connected_type:
            ctype = cls.convert_alias(connected_type)
        else:
            ctype = typ

        result = []
        for c in cmds.ls(as_strings(components), flatten=True) or []:
            attached = cls.convert_component_type(c, ctype, flatten=True) or []
            n = len(attached)
            if n >= lowRange and n <= highRange:
                result.append(c)

        return CoreUtils.convert_array_type(result, returned_type=returned_type)

    @classmethod
    def get_vertex_normal(cls, vertex, angle_weighted=False):
        """Return the normal at the given vertex."""
        candidates = cmds.ls(as_strings(vertex), objectsOnly=True) or []
        if not candidates:
            return None
        node = candidates[0]
        # Resolve transform → shape if needed
        if cmds.objectType(node) == "transform":
            shapes = cmds.listRelatives(node, shapes=True, noIntermediate=True) or []
            if shapes:
                node = shapes[0]

        sel_list = om.MSelectionList()
        sel_list.add(node)
        dag_path = sel_list.getDagPath(0)
        mesh_fn = om.MFnMesh(dag_path)

        vtxID = CoreUtils.convert_array_type(vertex, "int")[0]
        return mesh_fn.getVertexNormal(vtxID, angle_weighted, space=om.MSpace.kWorld)

    @staticmethod
    def get_vector_from_components(components):
        """Get a vector representing the averaged and normalized vertex-face normals."""
        vertices = cmds.polyListComponentConversion(components, toVertex=True) or []

        norm = cmds.polyNormalPerVertex(vertices, query=True, xyz=True) or []
        if not norm:
            return (0.0, 0.0, 0.0)
        normal_vector = (
            sum(norm[0::3]) / len(norm[0::3]),
            sum(norm[1::3]) / len(norm[1::3]),
            sum(norm[2::3]) / len(norm[2::3]),
        )
        return normal_vector

    @staticmethod
    def crease_edges(edges=None, amount=None, angle=None):
        """Adjust properties of the given edges with optional crease and edge softening/hardening."""
        if edges is None:
            if cmds.selectMode(q=True, object=True):
                selected_objects = cmds.ls(sl=True, o=True) or []
                edges = cmds.polyListComponentConversion(selected_objects, toEdge=True) or []
            else:
                edges = cmds.ls(sl=True, fl=True) or []

        edges = cmds.ls(edges, flatten=True) or []

        if not edges:
            return

        if amount is not None:
            cmds.polyCrease(edges, value=amount, vertexValue=amount)

        if angle is not None:
            cmds.polySoftEdge(edges, angle=angle)

    @staticmethod
    def get_creased_edges(edges):
        """Return any creased edges from a list of edges."""
        creased_edges = []
        for e in cmds.ls(as_strings(edges), flatten=True) or []:
            try:
                value = cmds.polyCrease(e, query=True, value=True)
                if value and value[0] > 0:
                    creased_edges.append(e)
            except Exception:
                continue
        return creased_edges

    @staticmethod
    def transfer_creased_edges(frm, to):
        """Transfer creased edges from the 'frm' object to the 'to' objects."""
        source = cmds.ls(as_strings(frm), objectsOnly=True) or []
        targets = cmds.ls(as_strings(to), objectsOnly=True) or []

        if not (source and targets):
            raise ValueError("Both source and target objects must exist.")

        src = source[0]
        try:
            crease_values = (
                cmds.polyCrease(f"{src}.e[*]", query=True, value=True) or []
            )
        except RuntimeError:
            crease_values = []

        for target in targets:
            for edge_id, crease_value in enumerate(crease_values):
                if crease_value > 0:
                    cmds.polyCrease(f"{target}.e[{edge_id}]", value=crease_value)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
