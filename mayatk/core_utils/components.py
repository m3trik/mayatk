# !/usr/bin/python
# coding=utf-8
from typing import Union, List

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils


class GetComponentsMixin:
    """ """

    component_mapping = [  # abv, singular, plural, full, int, hex
        (
            "vtx",
            "vertex",
            "vertices",
            "Polygon Vertex",
            31,
            0x0001,
        ),
        (
            "e",
            "edge",
            "edges",
            "Polygon Edge",
            32,
            0x8000,
        ),
        (
            "f",
            "face",
            "faces",
            "Polygon Face",
            34,
            0x0008,
        ),
        (
            "uv",
            "texture",
            "texture coordinates",
            "Polygon UV",
            35,
            0x0010,
        ),
        (
            "cv",
            "control vertex",
            "control vertices",
            "Control Vertex",
            28,
            None,
        ),
        (
            "vtxf",
            "vertexFace",
            "vertexFaces",
            "Polygon Vertex Face",
            70,
            None,
        ),
        (
            None,
            "edit point",
            "edit points",
            "Edit Point",
            30,
            None,
        ),
        (
            None,
            "handle",
            "handles",
            "Handle",
            0,
            None,
        ),
        (
            None,
            "nurbs surface",
            "nurbs surfaces",
            "Nurbs Curves On Surface",
            11,
            None,
        ),
        (
            None,
            "subd mesh point",
            "subd mesh points",
            "Subdivision Mesh Point",
            36,
            None,
        ),
        (
            None,
            "subd mesh edge",
            "subd mesh edges",
            "Subdivision Mesh Edge",
            37,
            None,
        ),
        (
            None,
            "subd mesh face",
            "subd mesh faces",
            "Subdivision Mesh Face",
            38,
            None,
        ),
        (
            None,
            "curve parameter point",
            "curve parameter points",
            "Curve Parameter Point",
            39,
            None,
        ),
        (
            None,
            "curve knot",
            "curve knots",
            "Curve Knot",
            40,
            None,
        ),
        (
            None,
            "surface parameter point",
            "surface parameter points",
            "Surface Parameter Point",
            41,
            None,
        ),
        (
            None,
            "surface knot",
            "surface knots",
            "Surface Knot",
            42,
            None,
        ),
        (
            None,
            "surface range",
            "surface ranges",
            "Surface Range",
            43,
            None,
        ),
        (
            None,
            "trim surface edge",
            "trim surface edges",
            "Trim Surface Edge",
            44,
            None,
        ),
        (
            None,
            "surface isoparm",
            "surface isoparms",
            "Surface Isoparm",
            45,
            None,
        ),
        (
            None,
            "lattice point",
            "lattice points",
            "Lattice Point",
            46,
            None,
        ),
        (
            None,
            "particle",
            "particles",
            "Particle",
            47,
            None,
        ),
        (
            None,
            "scale pivot",
            "scale pivots",
            "Scale Pivot",
            49,
            None,
        ),
        (
            None,
            "rotate pivot",
            "rotate pivots",
            "Rotate Pivot",
            50,
            None,
        ),
        (
            None,
            "select handle",
            "select handles",
            "Select Handle",
            51,
            None,
        ),
        (
            None,
            "nurbs surface face",
            "nurbs surface faces",
            "NURBS Surface Face",
            72,
            None,
        ),
        (
            None,
            "subd mesh UV",
            "subd mesh UVs",
            "Subdivision Mesh UV",
            73,
            None,
        ),
    ]

    @classmethod
    def get_component_type(cls, component, returned_type="abv"):
        """Get the type of a given component.

        Parameters:
            obj (str/obj/list): A single maya component.
                    If multiple components are given, only the first will be sampled.
            returned_type (str): Specify the desired return value type. (default: 'str')
                    (valid: 'full' - object type as a string.
                                    'int' - maya mask value as an integer.
                                    'hex' - hex value. ie. 0x0001
                                    'abv' - abreviated object type as a string. ie. 'vtx'
        Returns:
            (str)(int) dependant on 'returned_type' arg.

        Example:
            get_component_type('cyl.e[:]') #returns: 'e'
            get_component_type('cyl.vtx[:]', 'abv') #returns: 'vtx'
            get_component_type('cyl.e[:]', 'int') #returns: 32
        """
        for a, s, p, f, i, h in cls.component_mapping:
            try:
                if pm.filterExpand(component, sm=i):
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
        """Return an alternate component alias for the given alias.
        ie. a hex value of 0x0001 for 'vertex'
        If nothing is found, a value of 'None' will be returned.

        Parameters:
            component_type () = A component type. ex. 'vertex', 'vtx', 31, or 0x0001
            returned_type (str): The desired returned alias.  (default: 'abv')
                    (valid: 'abv', 'singular', 'plural', 'str', 'int', 'hex')

        Returns:
            (str)(int)(hex)(None) dependant on 'returned_type' argument.

        Example:
            convert_alias('vertex', 'hex') #returns: 0x0001
            convert_alias(0x0001, 'str') #returns: 'Polygon Vertex'
        """
        rtypes = ("abv", "singular", "plural", "full", "int", "hex")

        for t in cls.component_mapping:
            if component_type in t:
                index = rtypes.index(returned_type)
                return t[index]
        return None

    @classmethod
    def convert_component_type(
        cls, components, component_type, returned_type="str", flatten=False
    ):
        """Convert component(s) to it's sub-components of the given type.

        Parameters:
            components (str/obj/list): The components(s) to convert.
            component_type (str): The desired returned component type.
                    valid: 'vtx' (or 'vertex', 'vertices', 'Polygon Vertex', 31, 0x0001),
                            and the same for each: 'edge', 'uv', 'face'.
            returned_type (str): The desired returned object type.
                    (valid: 'str'(default), 'obj'(shape object), 'transform'(as string), 'int'(valid only at sub-object level).
            flatten (bool): Flattens the returned list of objects so that each component is it's own element.

        Returns:
            (list)(dict)

        Example:
            convert_component_type('obj.vtx[:2]', 'vertex') #returns: ['obj.vtx[0:2]']
            convert_component_type('obj.vtx[:2]', 'face') #returns: ['obj.f[0:2]', 'obj.f[11:14]', 'obj.f[23]']
            convert_component_type('obj.vtx[:2]', 'edge') #returns: ['obj.e[0:2]', 'obj.e[11]', 'obj.e[24:26]', 'obj.e[36:38]']
            convert_component_type('obj.vtx[:2]', 'uv') #returns: ['obj.map[0:2]', 'obj.map[12:14]', 'obj.map[24]']
        """
        d = {
            "vtx": "toVertex",
            "e": "toEdge",
            "uv": "toUV",
            "f": "toFace",
            "uv": "toUV",
            "shell": "toShell",
            "vertexFace": "toVertexFace",
        }
        typ = cls.convert_alias(component_type)

        if typ not in d:
            return components
        components = pm.polyListComponentConversion(
            components, **{d[typ.lower()]: True}
        )
        return CoreUtils.convert_array_type(
            components, returned_type=returned_type, flatten=flatten
        )

    @staticmethod
    def get_component_index(components):
        """Extracts the numerical index or indices of a component or components from their descriptor strings.

        Parameters:
            components (str/obj/list): A single component descriptor or an iterable of descriptors,
                                      typically in the format 'nodeName.componentType[index]'.
        Returns:
            int or list: The numerical index of the component if a single string is provided, or a list of indices
                         if an iterable of strings is provided.
        Raises:
            ValueError: If any component descriptor does not contain a valid index or if the input is not as expected.

        Examples:
            >>> ComponentsMixin.get_component_index('pCube1.vtx[32]')
            32
            >>> ComponentsMixin.get_component_index(['pCube1.vtx[32]', 'pCube2.vtx[45]'])
            [32, 45]
        """
        try:
            flattened = pm.ls(components, flatten=True)
            result = [c.index() for c in flattened]
            return result[0] if isinstance(components, (str, pm.PyNode)) else result
        except AttributeError:
            raise ValueError("Input must be a valid component type.")

    @classmethod
    def convert_int_to_component(
        cls, obj, integers, component_type, returned_type="str", flatten=False
    ):
        """Convert the given integers to components of the given object.

        Parameters:
            obj (str/obj/list): The object to convert to vertices of.
            integers (list): The integer(s) to convert.
            component_type (str): The desired returned component type.
                    valid: 'vtx' (or 'vertex', 'vertices', 'Polygon Vertex', 31, 0x0001),
                            and the same for each: 'edge', 'uv', 'face'.
            returned_type (str): The desired returned object type.
                    (valid: 'str'(default), 'obj'(shape object), 'transform'(as string), 'int'(valid only at sub-object level).
            flatten (bool): Flattens the returned list of objects so that each component is it's own element.

        Returns:
            (list)

        Example:
            convert_int_to_component('cyl', range(4), 'f') #returns: ['cylShape.f[0:3]']
        """
        obj = pm.ls(obj, objectsOnly=True)[0]
        objName = obj.name()

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
    def filter_components(cls, components, inc=[], exc=[], flatten=False):
        """Filter the given components.

        Parameters:
            components (str/obj/list): The components(s) to filter.
            inc (str)(int)(obj/list): The component(s) to include.
            exc (str)(int)(obj/list): The component(s) to exclude.
                                    (exlude take precidence over include)
            flatten (bool): Flattens the returned list of objects so that each component is it's own element.

        Returns:
            (list)

        Example:
            filter_components('cyl.vtx[:]', 'cyl.vtx[:2]', 'cyl.vtx[1:23]') #returns: ['cyl.vtx[0]']
            filter_components('cyl.f[:]', range(2), range(1, 23)) #returns: ['cyl.f[0]']
        """
        typ = cls.get_component_type(components)
        etyp = CoreUtils.get_array_type(components)
        etyp_inc = CoreUtils.get_array_type(inc)
        etyp_exc = CoreUtils.get_array_type(exc)

        if etyp_inc == "int" or etyp_exc == "int":
            try:
                obj = pm.ls(components, objectsOnly=True)[0]
            except IndexError as e:
                print(
                    f'File "{__file__}" in filter_components\n# Error: Operation requires at least one component. #\n{e}',
                )
                return []

        if etyp_inc == "int":
            inc = cls.convert_int_to_component(obj, inc, typ)
        inc = pm.ls(inc, flatten=True)

        if etyp_exc == "int":
            exc = cls.convert_int_to_component(obj, exc, typ)
        exc = pm.ls(exc, flatten=True)

        components = pm.ls(components, flatten=True)

        filtered = ptk.filter_list(components, inc=inc, exc=exc)
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
        inc=[],
        exc=[],
        randomize=0,
        flatten=False,
    ):
        """Get the components of the given type from the given object(s).

        Parameters:
            objects (str/obj/list): The object(s) to get the components of.
            component_type (str)(int): The component type to return. (valid: any type allowed in the 'convert_alias' method)
            returned_type (str): The desired returned object type.
                    (valid: 'str'(default), 'obj'(shape object), 'transform'(as string), 'int'(valid only at sub-object level).
            inc (str/obj/list): The component(s) to include.
            exc (str/obj/list): The component(s) to exclude. (exlude take precidence over include)
            randomize (float) = If a 0.1-1 value is given, random components will be returned with a quantity determined by the given ratio.
                                                    A value of 0.5 will return a 50% of the components of an object in random order.
            flatten (bool): Flattens the returned list of objects so that each component is it's own element.

        Returns:
            (list)(dict) Dependant on flags.

        Example:
            get_components(obj, 'vertex', 'str', '', 'obj.vtx[2:23]') # Returns: ['objShape.vtx[0]', 'objShape.vtx[1]', 'objShape.vtx[24]', 'objShape.vtx[25]']
            get_components(obj, 'vertex', 'obj', '', 'obj.vtx[:23]') # Returns: [MeshVertex('objShape.vtx[24]'), MeshVertex('objShape.vtx[25]')]
            get_components(obj, 'f', 'int') # Returns: {nt.Mesh('objShape'): [(0, 35)]}
            get_components(obj, 'edges') # Returns: ['objShape.e[0:59]']
            get_components(obj, 'edges', 'str', 'obj.e[:2]') # Returns: ['objShape.e[0]', 'objShape.e[1]', 'objShape.e[2]']
        """
        components = cls.convert_component_type(objects, component_type)

        if inc or exc:
            components = cls.filter_components(components, inc=inc, exc=exc)

        if randomize:
            components = randomize(pm.ls(components, flatten=1), randomize)

        result = CoreUtils.convert_array_type(
            components, returned_type=returned_type, flatten=flatten
        )
        return result


class Components(GetComponentsMixin, ptk.HelpMixin):
    """ """

    @staticmethod
    def map_components_to_objects(components_list):
        """Maps a list of components to their respective objects.

        This function takes in a list of PyMel component objects and returns a
        dictionary where the keys are the names of the parent objects of the
        components, and the values are lists of components belonging to each object.

        Parameters:
            components_list (str, obj, list): A list of component objects.

        Returns:
            dict: A dictionary mapping object names to lists of components.
                    The components are represented as PyMel objects.
        Example:
            components_dict = cls.map_components_to_objects(objects)
            for obj, components in components_dict.items():
                ...
        """
        objects_components_dict = {}

        for component in pm.ls(components_list, flatten=True):
            try:
                obj_name = component.node().name()
            except AttributeError:
                continue
            try:
                objects_components_dict[obj_name].append(component)
            except KeyError:
                objects_components_dict[obj_name] = [component]

        return objects_components_dict

    @classmethod
    def get_contigious_edges(cls, components):
        """Get a list containing sets of adjacent edges.

        Parameters:
            components (list): Polygon components to be filtered for adjacent edges.

        Returns:
            (list) adjacent edge sets.

        Example:
            get_contigious_edges(['obj.e[:2]']) #returns: [{'objShape.e[1]', 'objShape.e[0]', 'objShape.e[2]'}]
            get_contigious_edges(['obj.f[0]']) #returns: [{'objShape.e[24]', 'objShape.e[0]', 'objShape.e[25]', 'objShape.e[12]'}]
        """
        edges = cls.convert_component_type(components, "edge", flatten=1)

        sets = []
        for edge in edges:
            vertices = pm.polyListComponentConversion(edge, fromEdge=1, toVertex=1)
            connEdges = cls.convert_component_type(vertices, "edge", flatten=1)
            edge_set = set(
                [e for e in connEdges if e in edges]
            )  # restrict the connected edges to the original edge pool.
            sets.append(edge_set)

        result = []
        while len(sets) > 0:  # combine sets in 'sets' that share common elements.
            first, rest = sets[0], sets[1:]  # python 3: first, *rest = sets
            first = set(first)

            lf = -1
            while len(first) > lf:
                lf = len(first)

                rest2 = []
                for r in rest:
                    if len(first.intersection(set(r))) > 0:
                        first |= set(r)
                    else:
                        rest2.append(r)
                rest = rest2

            result.append(first)
            sets = rest

        return result

    @classmethod
    def get_contigious_islands(cls, faces):
        """Get a list containing sets of adjacent polygon faces grouped by islands.

        Parameters:
            faces (str/obj/list): The polygon faces to be filtered for adjacent.

        Returns:
            (list): of sets of adjacent faces.

        Example:
            get_contigious_islands('obj.f[21:26]') #returns: [{'objShape.f[22]', 'objShape.f[21]', 'objShape.f[23]'}, {'objShape.f[26]', 'objShape.f[24]', 'objShape.f[25]'}]
        """
        face_islands = []
        sets = []
        faces = pm.ls(faces, flatten=1)
        for face in faces:
            edges = pm.polyListComponentConversion(face, fromFace=1, toEdge=1)
            borderFaces = cls.convert_component_type(edges, "face", "obj", flatten=1)
            set_ = set([str(f) for f in borderFaces if f in faces])
            if set_:
                sets.append(set_)

        while len(sets) > 0:
            first, rest = sets[0], sets[1:]
            first = set(first)

            lf = -1
            while len(first) > lf:
                lf = len(first)
                rest2 = []
                for r in rest:
                    if len(first.intersection(set(r))) > 0:
                        first |= set(r)
                    else:
                        rest2.append(r)
                rest = rest2

            face_islands.append(first)
            sets = rest

        return face_islands

    @staticmethod
    def get_islands(obj, returned_type="str", flatten=False):
        """Get the group of components in each separate island of a combined mesh.

        Parameters:
            obj (str/obj/list): The object to get shells from.
            returned_type (bool): Return the shell faces as a list of type: 'str' (default), 'int', or 'obj'.
            flatten (bool): Flattens the returned list of objects so that each component is it's own element.

        Returns:
            (generator)

        Example:
            get_islands('combined_obj') #returns: [['combined_obj.f[0]', 'combined_obj.f[5]', ..etc, ['combined_obj.f[15]', ..etc]]
        """
        if isinstance(obj, str):
            obj = pm.ls(obj)[0]  # Convert input to a single PyMel object
        elif not isinstance(obj, pm.nt.Transform):
            raise ValueError(f"Expected a single mesh object, got {type(obj)}")

        # num_shells = pm.polyEvaluate(obj, shell=True)
        num_faces = pm.polyEvaluate(obj, face=True)
        unprocessed = set(range(num_faces))

        while unprocessed:
            # face_index
            index = next(iter(unprocessed))
            # shell faces
            faces = pm.polySelect(obj, extendToShell=index, noSelection=True)

            if returned_type == "str":
                yield ["{}.f[{}]".format(obj, index) for index in faces]

            elif returned_type == "int":
                yield [index for index in faces]

            elif returned_type == "obj":
                yield [pm.ls("{}.f[{}]".format(obj, index))[0] for index in faces]

            unprocessed.difference_update(faces)

    @classmethod
    def get_border_components(
        cls,
        components,
        returned_type="str",
        component_border=False,
    ):
        """Get border components from given component(s) or a polygon object based on connectivity.

        Parameters:
            x (str/obj/list): The component(s) or mesh object to find any border components for.
            returned_type (str): The desired returned object type (valid: 'str'(default), 'obj'(shape object), 'transform'(as string), 'int'(valid only at sub-object level).
            component_border (bool): Get the components that border the given components instead of the mesh border.

        Returns:
            (list) components that border an open edge or the given components.

        """
        components = pm.ls(components, flatten=True)

        # Early exit if no valid components given
        if not components:
            raise ValueError("No valid components given.")

        # Get component type early to handle error if None
        component_type = cls.get_component_type(components[0], "abv")
        if component_type is None:
            raise ValueError(f"Unrecognized component_type: {component_type}")

        border_components = []

        def is_border_vertex(comp):
            connected_vertices = comp.connectedVertices()
            connected_edges = comp.connectedEdges()
            return any(cc not in vertex_components for cc in connected_vertices) or any(
                len(edge.connectedFaces()) == 1 for edge in connected_edges
            )

        def is_border_edge(edge):
            return (
                any(
                    face_edge_count[(type(face), face.name())] == 1
                    for face in edge.connectedFaces()
                )
                or len(edge.connectedFaces()) == 1
            )

        def is_external_edge(edge):
            return len(cls.convert_component_type(edge, "face", "obj", flatten=1)) == 1

        def is_external_vertex(vtx):
            return any(is_external_edge(edge) for edge in vtx.connectedEdges())

        def is_external_face(face):
            return any(
                len(edge.connectedFaces()) == 1 for edge in face.connectedEdges()
            )

        if component_border:
            vertex_components = cls.convert_component_type(
                components, "vtx", "obj", flatten=True
            )
            border_components = [
                comp for comp in vertex_components if is_border_vertex(comp)
            ]

            if component_type == "e":
                original_edges = cls.convert_component_type(
                    components, "e", "obj", flatten=True
                )
                face_edge_count = {
                    (type(face), face.name()): 0
                    for edge in original_edges
                    for face in edge.connectedFaces()
                }
                for edge in original_edges:
                    for face in edge.connectedFaces():
                        face_key = (type(face), face.name())
                        face_edge_count[face_key] += 1

                border_components = [
                    edge for edge in original_edges if is_border_edge(edge)
                ]
            elif component_type == "f":
                border_faces = cls.convert_component_type(
                    border_components, "f", "obj", flatten=True
                )
                border_components = [
                    comp for comp in border_faces if comp in components
                ]
        else:
            if component_type == "e":
                border_components = [
                    edge for edge in components if is_external_edge(edge)
                ]
            elif component_type == "vtx":
                border_components = [
                    vtx for vtx in components if is_external_vertex(vtx)
                ]
            elif component_type == "f":
                border_faces = cls.convert_component_type(
                    components, "f", "obj", flatten=True
                )
                border_components = [
                    face for face in border_faces if is_external_face(face)
                ]
            else:
                raise ValueError(f"Unrecognized component_type: {component_type}")

        result = CoreUtils.convert_array_type(
            border_components, returned_type=returned_type
        )
        return result

    @staticmethod
    def get_furthest_vertices(vertices_a, vertices_b):
        """Determines the two furthest apart vertices, one from each of the two provided lists of vertices.

        Parameters:
            vertices_a (str/obj/list): A list of vertices (pm.MeshVertex or string descriptors).
            vertices_b (str/obj/list): Another list of vertices (pm.MeshVertex or string descriptors).

        Returns:
            tuple: The pair of vertices (from a_vertices and b_vertices respectively) that are furthest apart.
        """
        list_a = pm.ls(vertices_a, flatten=True)
        list_b = pm.ls(vertices_b, flatten=True)

        max_dist = 0
        result_a = result_b = None
        for v1 in list_a:
            v1_pos = pm.pointPosition(v1, world=True)
            for v2 in list_b:
                v2_pos = pm.pointPosition(v2, world=True)
                dist = (v1_pos - v2_pos).length()
                if dist > max_dist:
                    max_dist = dist
                    result_a = v1
                    result_b = v2
        return (result_a, result_b)

    @classmethod
    def get_closest_verts(cls, a, b, tolerance=1000):
        """Find the two closest vertices between the two sets of vertices.

        Parameters:
            a (str/list): The first set of vertices.
            b (str/list): The second set of vertices.
            tolerance (float) = Maximum search distance.

        Returns:
            (list): closest vertex pairs by order of distance (excluding those not meeting the tolerance). (<vertex from a>, <vertex from b>).

        Example:
            get_closest_verts('pln.vtx[:10]', 'pln.vtx[11:]', 6.667) #returns: [('plnShape.vtx[7]', 'plnShape.vtx[11]'), ('plnShape.vtx[8]', 'plnShape.vtx[12]'), ('plnShape.vtx[9]', 'plnShape.vtx[13]'), ('plnShape.vtx[10]', 'plnShape.vtx[11]'), ('plnShape.vtx[10]', 'plnShape.vtx[14]')]
        """
        from operator import itemgetter

        a = CoreUtils.convert_array_type(a, returned_type="str", flatten=True)
        b = CoreUtils.convert_array_type(b, returned_type="str", flatten=True)
        vertPairsAndDistance = {}
        for v1 in a:
            v1Pos = pm.pointPosition(v1, world=1)
            for v2 in b:
                v2Pos = pm.pointPosition(v2, world=1)
                distance = ptk.distance_between_points(v1Pos, v2Pos)
                if distance < tolerance:
                    vertPairsAndDistance[(v1, v2)] = distance

        sorted_ = sorted(vertPairsAndDistance.items(), key=itemgetter(1))
        vertPairs = [i[0] for i in sorted_]

        return vertPairs

    @classmethod
    @CoreUtils.undoable
    def get_closest_vertex(
        cls, vertices, obj, tolerance=0.0, freeze_transforms=False, returned_type="str"
    ):
        """Find the closest vertex of the given object for each vertex in the list of given vertices.

        Parameters:
            vertices (list): A set of vertices.
            obj (str/obj/list): The reference object in which to find the closest vertex for each vertex in the list of given vertices.
            tolerance (float) = Maximum search distance. Default is 0.0, which turns off the tolerance flag.
            freeze_transforms (bool): Reset the selected transform and all of its children down to the shape level.
            returned_type (str): The desired returned object type. This only affects the dict value (found vertex),
                            the key (orig vertex) is always a string. ex. {'planeShape.vtx[0]': 'objShape.vtx[3]'} vs. {'planeShape.vtx[0]': MeshVertex('objShape.vtx[3]')}
                            (valid: 'str'(default), 'obj'(shape object), 'transform'(as string), 'int'(valid only at sub-object level).
        Returns:
            (dict) closest vertex pairs {<vertex from a>:<vertex from b>}.

        Example:
            get_closest_vertex('plnShape.vtx[0]', 'cyl', returned_type='int') #returns: {'plnShape.vtx[0]': 3},
            get_closest_vertex('plnShape.vtx[0]', 'cyl') #returns: {'plnShape.vtx[0]': 'cylShape.vtx[3]'},
            get_closest_vertex('plnShape.vtx[2:3]', 'cyl') #returns: {'plnShape.vtx[2]': 'cylShape.vtx[2]', 'plnShape.vtx[3]': 'cylShape.vtx[1]'}
        """
        vertices = CoreUtils.convert_array_type(
            vertices, returned_type="str", flatten=True
        )

        if freeze_transforms:
            pm.makeIdentity(obj, apply=True)

        # pm.listRelatives(obj, fullPath=False, shapes=True, noIntermediate=True)
        obj2Shape = pm.listRelatives(obj, children=1, shapes=1)[0]
        # create a closestPointOnMesh node.
        cpmNode = pm.ls(pm.createNode("closestPointOnMesh"))[0]
        # object's shape mesh output to the cpm node.
        pm.connectAttr(obj2Shape.outMesh, cpmNode.inMesh, force=1)

        closestVerts = {}
        for (
            v1
        ) in (
            vertices
        ):  # assure the list of vertices is a flattened list of stings. prevent unhashable type error when closestVerts[v1] = v2.  may not be needed with python versions 3.8+
            v1Pos = pm.pointPosition(v1, world=True)
            pm.setAttr(
                cpmNode.inPosition, v1Pos[0], v1Pos[1], v1Pos[2], type="double3"
            )  # set a compound attribute

            index = pm.getAttr(
                cpmNode.closestVertexIndex
            )  # vertex Index. | ie. result: [34]
            v2 = obj2Shape.vtx[index]

            v2Pos = pm.pointPosition(v2, world=True)
            distance = ptk.distance_between_points(v1Pos, v2Pos)

            v2_convertedType = CoreUtils.convert_array_type(
                v2, returned_type=returned_type
            )[0]
            if not tolerance:
                closestVerts[v1] = v2_convertedType
            elif distance < tolerance:
                closestVerts[v1] = v2_convertedType

        pm.delete(cpmNode)

        return closestVerts

    @staticmethod
    def get_vertices_within_threshold(reference_vertices, max_distance):
        """Categorizes vertices of a mesh based on their distance from the first given reference vertex or vertices.

        This function checks if vertices are within a specified maximum distance from the first vertex in the given list
        or from a single vertex. It returns two lists: one containing vertices within the maximum distance and another
        containing vertices outside of this distance.

        Parameters:
            reference_vertices (list or pm.MeshVertex): A single vertex or a list of vertices to use as reference.
            max_distance (float): The maximum distance to use for categorization.

        Returns:
            tuple of lists: (inside, outside)
                            - inside: A list of vertices within the maximum distance.
                            - outside: A list of vertices outside the maximum distance.
        Example:
            >>> inside, outside = get_vertices_within_distance('pCube1.vtx[2:6]', 5.0)
        """
        reference_vertices = pm.ls(reference_vertices, flatten=True)

        reference_point = reference_vertices[0].getPosition(space="world")
        mesh = reference_vertices[0].node()

        inside = []
        outside = []

        all_vertices = mesh.vtx
        for vtx in all_vertices:
            vtx_position = vtx.getPosition(space="world")
            distance = (vtx_position - reference_point).length()
            if distance <= max_distance:
                inside.append(vtx)
            else:
                outside.append(vtx)

        return (inside, outside)

    @staticmethod
    def adjusted_distance_between_vertices(
        p1, p2, adjust: float = 0.0, as_percentage: bool = False
    ):
        """Calculate Adjusted Distance Between Vertices

        This function calculates the distance between two points or vertices, adjusts it by a
        specified absolute value or percentage, and optionally selects the parent object.
        The method supports inputs in the form of MeshVertex objects, Points, or strings
        referencing vertices.

        Parameters:
            p1 (str/pm.MeshVertex/pm.datatypes.Point): The first vertex or point.
                Can be a MeshVertex, Point, or a string that references a vertex.
            p2 (str/pm.MeshVertex/pm.datatypes.Point): The second vertex or point.
                Can be a MeshVertex, Point, or a string that references a vertex.
            adjust (float): The absolute or percentage value to adjust the calculated distance.
                Default is 0.0, meaning no adjustment. Positive values increase the distance,
                and negative values decrease it. If as_percentage is True, then this value is
                interpreted as a percentage.
            as_percentage (bool): If True, the adjust value is treated as a percentage of the
                original distance. If False, the adjust value is treated as an absolute value
                to add or subtract from the distance. Default is False.

        Returns:
            float: The adjusted distance between the two points or vertices.
        """
        # Convert MeshVertex to points if necessary
        if isinstance(p1, pm.MeshVertex):
            p1 = pm.pointPosition(p1, world=True)
        if isinstance(p2, pm.MeshVertex):
            p2 = pm.pointPosition(p2, world=True)

        # Calculate the distance between the two vertices or points
        dist = ptk.distance_between_points(p1, p2)

        if as_percentage:
            # Adjust by percentage
            dist *= 1 + adjust / 100
        else:
            # Adjust by absolute value
            dist += adjust

        return dist

    @staticmethod
    @CoreUtils.undoable
    def bridge_connected_edges(edges: Union[str, object, list]) -> None:
        """Bridges two connected edges by extruding one edge, then moving and merging
        the new vertices with the corresponding vertices of the second edge.

        Parameters:
            edges (str, object, list): Two connected MeshEdge objects their identifiers.
        """
        # Validate input edges
        edges = pm.ls(pm.filterExpand(edges, sm=32))
        if not edges or len(edges) < 2:
            raise ValueError(
                "Invalid input: At least two edges are required for bridging."
            )

        # Extract vertex names from edges
        vertices_edge1_names = {v.name() for v in edges[0].connectedVertices()}
        vertices_edge2_names = {v.name() for v in edges[1].connectedVertices()}
        try:
            common_vertex_name = list(vertices_edge1_names & vertices_edge2_names)[0]
        except IndexError:
            raise ValueError(
                "Cannot bridge edges: The provided edges do not share a common vertex."
            )
        common_vertex = pm.PyNode(common_vertex_name)

        # Perform extrusion to create new vertices
        pm.polyExtrudeEdge(edges[0], ltz=0.1, ls=(1, 1, 1))
        pm.refresh()

        # Identify new vertices created by the extrusion
        new_vertices = pm.ls(
            pm.polyListComponentConversion(toVertex=True), flatten=True
        )
        new_vertex_names = {v.name() for v in new_vertices}

        # Determine which new vertex is connected to the common vertex
        for new_vertex_name in new_vertex_names:
            new_vertex = pm.PyNode(new_vertex_name)
            if common_vertex in new_vertex.connectedVertices():
                connected_new_vertex = new_vertex
                break

        # Move and merge the connected new vertex with the common vertex
        pm.move(connected_new_vertex, pm.pointPosition(common_vertex), absolute=True)
        pm.polyMergeVertex([connected_new_vertex, common_vertex], d=0.0, am=True)

        # Identify the remaining new vertex and the target vertex on edge 2
        remaining_new_vertex_name = list(
            new_vertex_names - {connected_new_vertex.name()}
        )[0]
        remaining_new_vertex = pm.PyNode(remaining_new_vertex_name)
        target_vertex_edge2_name = list(vertices_edge2_names - {common_vertex_name})[0]
        target_vertex_edge2 = pm.PyNode(target_vertex_edge2_name)

        # Move the remaining new vertex to the target vertex and merge
        pm.move(
            remaining_new_vertex, pm.pointPosition(target_vertex_edge2), absolute=True
        )
        pm.polyMergeVertex([remaining_new_vertex, target_vertex_edge2], d=0.0, am=True)

        pm.select(clear=True)

    @classmethod
    def get_edge_path(
        cls, components, path="edgeLoop", returned_type="str", flatten=False
    ):
        """Query the polySelect command for the components along different edge paths.
        Supports components from a single object.

        Parameters:
            components (str/obj/list): The components used for the query (dependant on the operation type).
            path (str): The desired return type. valid: 'edgeLoop': Select an edge loop starting at the given edge.
                    'edgeRing': Select an edge ring starting at the given edge.
                    'edgeRingPath', Given two edges that are on the same edge ring, this will select the shortest path between them on the ring.
                    'edgeLoopPath': Given two edges that are on the same edge loop, this will select the shortest path between them on the loop.
            returned_type (str): The desired returned object type.
                    (valid: 'str'(default), 'obj'(shape object), 'transform'(as string), 'int'(valid only at sub-object level).
            flatten (bool): Flattens the returned list of objects so that each component is it's own element.

        Returns:
            (list) The components comprising the path.

        Example:
            get_edge_path('sph.e[12]', 'edgeLoop') #returns: ['sphShape.e[12]', 'sphShape.e[17]', 'sphShape.e[16]', 'sphShape.e[15]', 'sphShape.e[14]', 'sphShape.e[13]']
            get_edge_path('sph.e[12]', 'edgeLoop', 'int') #returns: [12, 17, 16, 15, 14, 13]
            get_edge_path('sph.e[12]', 'edgeRing') #returns: ['sphShape.e[0]', 'sphShape.e[6]', 'sphShape.e[12]', 'sphShape.e[18]', 'sphShape.e[24]']
            get_edge_path(['sph.e[43]', 'sph.e[46]'], 'edgeRingPath') #returns: ['sphShape.e[43]', 'sphShape.e[42]', 'sphShape.e[47]', 'sphShape.e[46]']
            get_edge_path(['sph.e[54]', 'sph.e[60]'], 'edgeLoopPath') #returns: ['sphShape.e[60]', 'sphShape.e[48]', 'sphShape.e[42]', 'sphShape.e[36]', 'sphShape.e[30]', 'sphShape.e[54]']
        """
        obj, *other = pm.ls(components, objectsOnly=1)
        cnums = cls.convert_component_type(
            components, "edge", returned_type="int", flatten=True
        )

        if len(cnums) < 2 and path in ("edgeRingPath", "edgeLoopPath"):
            print(
                f'File "{__file__}" in get_edge_path\n# Error: Operation requires at least two components. #\n Edges given: {cnums}',
            )
            return []

        if path == "edgeRing":
            edgesLong = pm.polySelect(obj, q=True, edgeRing=cnums)  # (e..)

        elif path == "edgeRingPath":
            edgesLong = pm.polySelect(
                obj, q=True, edgeRingPath=(cnums[0], cnums[1])
            )  # (e, e)
            if not edgesLong:
                print(
                    f'File "{__file__}" in get_edge_path\n# Error: get_edge_path: Operation requires two edges that are on the same edge ring.\n\tEdges given: {cnums[0]}, {cnums[1]}',
                )
                return []

        elif path == "edgeLoopPath":
            edgesLong = pm.polySelect(
                obj, q=True, edgeLoopPath=(cnums[0], cnums[1])
            )  # (e, e)
            if not edgesLong:
                print(
                    f'File "{__file__}" in get_edge_path\n# Error: get_edge_path: Operation requires two edges that are on the same edge loop.\n\tEdges given: {cnums[0]}, {cnums[1]}',
                )
                return []
        else:  # EdgeLoop
            edgesLong = pm.polySelect(obj, q=True, edgeLoop=cnums)  # (e..)

        objName = obj.name()
        result = ptk.remove_duplicates(
            ["{}.e[{}]".format(objName, e) for e in edgesLong]
        )
        return CoreUtils.convert_array_type(
            result, returned_type=returned_type, flatten=flatten
        )

    @classmethod
    def get_shortest_path(cls, components, flatten=False):
        """Calculate the shortest path between two specified edge or vertex components within the same 3D object.
        This method supports only edges and vertices. The path includes the initial and final components.

        Parameters:
            components (list): A list containing exactly two edge or vertex components from which to find the shortest path.
            flatten (bool): If set to True, the result will be a flat list of all vertices in the path if the components are vertices.
                            If the components are edges, it will be a list of edges. Defaults to False, which returns components in their hierarchical structures.
        Returns:
            list: A list containing the shortest path between the two components, including the start and end components.
                  If edges are used, the result includes the original edges. If vertices are used, the result includes all intermediate vertices.
        Raises:
            ValueError: If the input does not contain exactly two components, if the components do not belong to the same object,
                        if the components are not of the same type, or if an unsupported component type is provided.
        Example:
            >>> cls.get_shortest_path(['pCube1.e[1]', 'pCube1.e[3]'])
            ['pCube1.e[1]', 'pCube1.e[2]', 'pCube1.e[3]']

            >>> cls.get_shortest_path(['pCube1.vtx[1]', 'pCube1.vtx[3]'], flatten=True)
            ['pCube1.vtx[1]', 'pCube1.vtx[2]', 'pCube1.vtx[3]']
        """
        try:
            a, b = components = pm.ls(components, flatten=True)
        except ValueError:
            raise ValueError(f"Exactly two components are required. Got: {components}")

        obj_a = pm.ls(components[0], objectsOnly=True)[0]
        obj_b = pm.ls(components[1], objectsOnly=True)[0]
        if obj_a != obj_b:
            raise ValueError("Components must belong to the same object")

        a_type = cls.get_component_type(a, returned_type="abv")
        b_type = cls.get_component_type(b, returned_type="abv")
        if a_type != b_type:
            raise ValueError("Both components must be of the same type")

        if a_type == "e":
            a_vertices = pm.ls(
                pm.polyListComponentConversion(a, fromEdge=True, toVertex=True),
                flatten=True,
            )
            b_vertices = pm.ls(
                pm.polyListComponentConversion(b, fromEdge=True, toVertex=True),
                flatten=True,
            )

            selected_a, selected_b = cls.get_furthest_vertices(a_vertices, b_vertices)
            a_index = cls.get_component_index(selected_a)
            b_index = cls.get_component_index(selected_b)

        elif a_type == "vtx":
            a_index = cls.get_component_index(a)
            b_index = cls.get_component_index(b)
        else:
            raise ValueError("Unsupported component type for path calculation")

        path_indices = pm.polySelect(obj_a, q=True, shortestEdgePath=[a_index, b_index])

        # Include the starting and ending edges in the results if type is edge
        if a_type == "e":
            result = [a] + [f"{obj_a.name()}.e[{idx}]" for idx in path_indices] + [b]
        elif a_type == "vtx":
            result = []
            for idx in path_indices:
                edge = f"{obj_a.name()}.e[{idx}]"
                vertices = pm.polyListComponentConversion(
                    edge, fromEdge=True, toVertex=True
                )
                result.extend(pm.ls(vertices, flatten=True))
        else:
            result = path_indices

        return ptk.remove_duplicates(result)

    @staticmethod
    def get_normal(face):
        """Get the normal of a face in world space.

        Parameters:
            face (pymel.core.nodetypes.MeshFace): The face to get the normal of.

        Returns:
            om.MVector: The normal of the face in world space.

        Raises:
            TypeError: If the input is not a MeshFace.
        """
        import maya.OpenMaya as om

        if not isinstance(face, pm.general.MeshFace):
            raise TypeError(f"Input must be a MeshFace, got {type(face)}.")

        # Create an MSelectionList and add the face to it
        sel_list = om.MSelectionList()
        sel_list.add(face.name())

        # Create an MDagPath and an MObject for the face
        dag_path = om.MDagPath()
        component = om.MObject()
        sel_list.getDagPath(0, dag_path, component)

        # Create an MFnMesh for the mesh the face belongs to
        mesh_fn = om.MFnMesh(dag_path)

        # Get the index of the face
        face_index = int(face.name().split("[")[-1].split("]")[0])

        # Get the normal of the face
        normal = om.MVector()
        mesh_fn.getPolygonNormal(face_index, normal, om.MSpace.kWorld)

        return normal

    @staticmethod
    def get_normal_vector(x):
        """Get the normal vectors of the given polygon object(s) or its components.

        Parameters:
            x (str/obj/list): A polygon mesh or its components. Accepts a string representation, a PyNode object,
                    or a list of either, representing one or more polygon meshes or their faces.
        Returns:
            dict: A dictionary where each key-value pair corresponds to a face of the polygon object(s).
                  The key is the face's ID, and the value is a list representing the face's normal vector in the format [x, y, z].

        This function extracts the normal vectors for each face of the provided polygon object(s).
        If components (faces) are provided directly, the function will only calculate and return the normals for those faces.
        """
        obj = pm.ls(x)
        normals = pm.polyInfo(obj, faceNormals=1)

        regex = "[A-Z]*_[A-Z]* *[0-9]*: "

        dct = {}
        for n in normals:
            lst = list(
                s.replace(regex, "") for s in n.split() if s
            )  # ['FACE_NORMAL', '150:', '0.935741', '0.110496', '0.334931\n']

            key = int(lst[1].strip(":"))  # int face number as key ie. 150
            value = list(
                float(i) for i in lst[-3:]
            )  # vector list as value. ie. [[0.935741, 0.110496, 0.334931]]
            dct[key] = value

        return dct

    @classmethod
    def get_normal_angle(
        cls, edges: Union[object, List[object]]
    ) -> Union[float, List[float]]:
        """Get the angle between the normals of the faces connected by one or more edges.

        Parameters:
            edges (str/obj/list): The edge or edges to get the normal angles of.

        Returns:
            float or List[float]: The angle(s) between the normals of the faces connected by the edge(s), in degrees.
            Returns a list when a list is given.
        """
        import math

        def calculate_angle(edge: pm.general.MeshEdge) -> float:
            connected_faces = list(edge.connectedFaces())
            if len(connected_faces) != 2:
                return 0

            normal1 = cls.get_normal(connected_faces[0])
            normal2 = cls.get_normal(connected_faces[1])
            angle = normal1.angle(normal2)
            return math.degrees(angle)

        result = [
            calculate_angle(e)
            for e in pm.ls(edges)
            if isinstance(e, pm.general.MeshEdge)
        ]
        return ptk.format_return(result, edges)

    @classmethod
    def get_edges_by_normal_angle(
        cls,
        objects: Union[str, object, List],
        low_angle: float = 0,
        high_angle: float = 180,
    ) -> List[object]:
        """Return edges whose adjacent face-normal angle falls within a range.

        Parameters:
            objects: Any of Transform, Mesh, MeshEdge, MeshFace, MeshVertex, or their strings.
            low_angle: The lower bound of the normal angle range.
            high_angle: The upper bound of the normal angle range.

        Returns:
            A list of polygon edges that have normals within the specified angle range.
        """
        # Normalize any supported input to MeshEdge objects using the shared converter
        edges: List[pm.general.MeshEdge] = pm.ls(
            cls.convert_component_type(
                objects, "edge", returned_type="obj", flatten=True
            ),
            flatten=True,
        )

        # Filter edges by normal angle
        filtered_edges = [
            edge
            for edge in edges
            if low_angle <= cls.get_normal_angle(edge) <= high_angle
        ]

        return filtered_edges

    @classmethod
    @CoreUtils.undoable
    def set_edge_hardness(
        cls,
        objects: Union[str, object, List],
        angle_threshold: float,
        upper_hardness: float = None,
        lower_hardness: float = None,
    ) -> None:
        """Set edge hardness based on normal angle thresholds using the enhanced get_edges_by_normal_angle.

        Parameters:
            cls: The class the method belongs to.
            objects: Objects or collections of objects to process.
            angle_threshold: Angle in degrees to classify edges.
            upper_hardness: Hardness to apply to edges above the angle threshold.
            lower_hardness: Hardness to apply to edges below the angle threshold.
        """
        # Retrieve all edges within the specified angle range
        all_edges = cls.get_edges_by_normal_angle(objects, 0, 180)

        # Map components to their respective objects to ensure single object operation
        object_to_edges = cls.map_components_to_objects(all_edges)

        # Iterate over each object and apply edge hardness settings
        for obj, edges in object_to_edges.items():
            # Filter edges for upper and lower hardness
            upper_edges = [
                edge
                for edge in edges
                if cls.get_normal_angle(edge) >= angle_threshold
                and upper_hardness is not None
            ]
            lower_edges = [
                edge
                for edge in edges
                if cls.get_normal_angle(edge) < angle_threshold
                and lower_hardness is not None
            ]

            # Apply hardness settings to the filtered edges
            if upper_edges:
                pm.polySoftEdge(upper_edges, angle=upper_hardness, ch=True)
            if lower_edges:
                pm.polySoftEdge(lower_edges, angle=lower_hardness, ch=True)

    @classmethod
    def get_faces_with_similar_normals(
        cls,
        faces,
        transforms=[],
        similar_faces=[],
        range_x=0.1,
        range_y=0.1,
        range_z=0.1,
        returned_type="str",
    ):
        """Filter for faces with normals that fall within an X,Y,Z tolerance.

        Parameters:
            faces (list): ['polygon faces'] - faces to find similar normals for.
            similar_faces (list): optional ability to add faces from previous calls to the return value.
            transforms (list): [<shape nodes>] - objects to check faces on. If none are given the objects containing the given faces will be used.
            range_x = float - x axis tolerance
            range_y = float - y axis tolerance
            range_z = float - z axis tolerance
            returned_type (str): The desired returned object type.
                        valid: 'str'(default), 'obj'(shape object), 'transform'(as string), 'int'(valid only at sub-object level).
        Returns:
            (list) faces that fall within the given normal range.

        Example:
            get_faces_with_similar_normals(selectedFaces, range_x=0.5, range_y=0.5, range_z=0.5)
        """
        # Work on a copy of the argument so that removal of elements doesn't effect the passed in list.
        faces = pm.ls(faces, flatten=1)
        for face in faces:
            normals = cls.get_normal_vector(face)

            for k, v in normals.items():
                sX, sY, sZ = v

                if not transforms:
                    transforms = pm.ls(face, objectsOnly=True)

                for node in transforms:
                    for f in cls.get_components(
                        node, "faces", returned_type=returned_type, flatten=1
                    ):
                        n = cls.get_normal_vector(f)
                        for k, v in n.items():
                            nX, nY, nZ = v

                            if (
                                sX <= nX + range_x
                                and sX >= nX - range_x
                                and sY <= nY + range_y
                                and sY >= nY - range_y
                                and sZ <= nZ + range_z
                                and sZ >= nZ - range_z
                            ):
                                similar_faces.append(f)
                                if (
                                    f in faces
                                ):  # If the face is in the loop que, remove it, as has already been evaluated.
                                    faces.remove(f)

        return similar_faces

    @classmethod
    @CoreUtils.undoable
    def average_normals(cls, objects, by_uv_shell=False):
        """Average the normals of the given objects.

        Parameters:
            objects (str/obj/list): The mesh or mesh faces to averge.
            by_uv_shell (bool): Average each UV shell individually.
        """
        # Map components to their respective objects
        components_dict = cls.map_components_to_objects(objects)

        # Loop through each object and its corresponding components
        for obj, components in components_dict.items():
            if by_uv_shell:
                uv_shell_sets = cls.get_uv_shell_sets(components)
                for uv_set in uv_shell_sets:
                    pm.polySoftEdge(uv_set, a=180)
            else:
                if components:  # if faces/components are selected
                    pm.polySoftEdge(components, a=180)
                else:  # if objects are selected
                    pm.polySoftEdge(obj, a=180)

    @staticmethod
    @CoreUtils.undoable
    def transfer_normals(objects: list[str], space: str = "world"):
        """Transfer vertex normals from source mesh to target meshes.

        Parameters:
            objects (list): List of mesh names, with the first being the source and the rest being the targets.
            space (str): The space in which to transfer the normals ('world' or 'local').
        """
        space_map = {"world": 0, "local": 1, "component": 4, "topology": 5}
        if space not in space_map:
            valid_spaces = ", ".join(space_map.keys())
            raise ValueError(f"space parameter must be one of: {valid_spaces}")

        # Filter objects to ensure only polygonal meshes are included
        objs = pm.ls(objects, type="mesh")
        if len(objs) < 2:
            raise ValueError(
                "At least one source and one target mesh must be polygonal meshes."
            )

        source_mesh, *target_meshes = objs
        sample_space_value = space_map[space]

        source_vertices = source_mesh.numVertices()
        for target_mesh in target_meshes:
            target_vertices = target_mesh.numVertices()

            if source_vertices != target_vertices:
                raise ValueError(
                    "Source and target meshes do not have the same topology"
                )

            # Transfer vertex normals
            pm.transferAttributes(
                source_mesh,
                target_mesh,
                transferNormals=1,
                sampleSpace=sample_space_value,
                searchMethod=3,  # Closest to point
                colorBorders=1,
            )

            # Ensure normals are unfrozen and correct
            pm.polyNormalPerVertex(target_mesh, unFreezeNormal=True)

            # Soften edges to ensure a smooth appearance
            pm.polySoftEdge(target_mesh, angle=180)

    @classmethod
    def filter_components_by_connection_count(
        cls, components, num_of_connected=(0, 2), connected_type="", returned_type="str"
    ):
        """Get a list of components filtered by the number of their connected components.

        Parameters:
            components (str/list)(obj): The components to filter.
            num_of_connected (int)(tuple): The number of connected components. Can be given as a range. (Default: (0,2))
            connected_type (str)(int): The desired component mask. (valid: 'vtx','vertex','vertices','Polygon Vertex',31,0x0001(vertices), 'e','edge','edges','Polygon Edge',32,0x8000(edges), 'f','face','faces','Polygon Face',34,0x0008(faces), 'uv','texture','texture coordinates','Polygon UV',35,0x0010(texture coordiantes).
            returned_type (str): The desired returned object type.
                        valid: 'str'(default), 'obj', 'int'(valid only at sub-object level)
        Returns:
            (list) flattened list.

        Example:
            faces = filter_components_by_connection_count('sph.f[:]', 4, 'e') #returns faces with four connected edges (four sided faces).
            verts = filter_components_by_connection_count('pln.vtx[:]', (0,2), 'e') #returns vertices with up to two connected edges.
        """
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
        for c in pm.ls(components, flatten=True):
            attached = cls.convert_component_type(c, ctype, flatten=True)
            n = len(attached)
            if n >= lowRange and n <= highRange:
                result.append(c)

        result = CoreUtils.convert_array_type(result, returned_type=returned_type)
        return result

    @classmethod
    def get_vertex_normal(cls, vertex, angle_weighted=False):
        """Return the normal at the given vertex. The returned normal is a single
        per-vertex normal, so unshared normals at a vertex will be averaged.

        Parameters:
            vertex (str/obj/list): A polygon vertex.
            angle_weighted (bool): Weight by the angle subtended by the face at the vertex.
                    If angle_weighted is set to false, a simple average of surround face normals is returned.
                    The simple average evaluation is significantly faster than the angle-weighted average.
        Returns:
            (MVector)
        """
        import maya.api.OpenMaya as om

        mesh = pm.ls(vertex, objectsOnly=True)[0].name()
        selectionList = om.MSelectionList()  # empty selection list.
        selectionList.add(mesh)

        dagPath = selectionList.getDagPath(0)  # create empty dag path object.
        mesh = om.MFnMesh(dagPath)  # get mesh.

        vtxID = CoreUtils.convert_array_type(vertex, "int")[0]
        # get vertex normal and use om.MSpace.kObject for object space.
        return mesh.getVertexNormal(vtxID, angle_weighted, space=om.MSpace.kWorld)

    @staticmethod
    def get_vector_from_components(components):
        """Get a vector representing the averaged and normalized vertex-face normals.

        Parameters:
            components (list): A list of component to get normals of.

        Returns:
            (tuple) vector ie. (-4.5296159711938344e-08, 1.0, 1.6846732009412335e-08)
        """
        vertices = pm.polyListComponentConversion(components, toVertex=1)

        norm = pm.polyNormalPerVertex(
            vertices, query=True, xyz=True
        )  # return all of the normals associated with the vert.
        normal_vector = (
            sum(norm[0::3]) / len(norm[0::3]),
            sum(norm[1::3]) / len(norm[1::3]),
            sum(norm[2::3]) / len(norm[2::3]),
        )  # averaging of all x,y,z points.

        return normal_vector

    @staticmethod
    def crease_edges(edges=None, amount=None, angle=None):
        """Adjust properties of the given edges with optional crease and edge softening/hardening.

        Parameters:
            edges (str/obj/list/None): List of edges or None. If None, uses current selection.
            amount (float/None): Value of the crease to apply, or None to skip.
            angle (int/None): Angle for softening (180) or hardening (0) the edges, or None to skip.
        """
        # If edges are not provided, determine the selection context
        if edges is None:
            if pm.selectMode(q=True, object=True):
                # Object mode: Get all edges of the selected objects
                selected_objects = pm.ls(sl=True, o=True)
                edges = pm.polyListComponentConversion(selected_objects, toEdge=True)
            else:
                # Edge selection mode: Use the current edge selection
                edges = pm.ls(sl=True, fl=True)

        # Ensure edges are flattened for polyCrease and polySoftEdge
        edges = pm.ls(edges, flatten=True)

        if not edges:
            return

        # Apply crease if specified
        if amount is not None:
            pm.polyCrease(edges, value=amount, vertexValue=amount)

        # Soften/harden edges if edge_angle is specified
        if angle is not None:
            pm.polySoftEdge(edges, angle=angle)

    @staticmethod
    def get_creased_edges(edges):
        """Return any creased edges from a list of edges.

        Parameters:
            edges (str/obj/list): The edges to query.

        Returns:
            list: edges.
        """
        creased_edges = [
            e
            for e in pm.ls(edges, flatten=1)
            if pm.polyCrease(e, query=True, value=True)[0] > 0
        ]
        return creased_edges

    @staticmethod
    def transfer_creased_edges(frm, to):
        """Transfers creased edges from the 'frm' object to the 'to' objects.

        Parameters:
            frm (str/obj/list): The name(s) of the source object(s).
            to (str/obj/list): The name(s) of the target object(s).
        """
        # Convert frm and to into lists of PyNode objects
        source = pm.ls(frm, objectsOnly=True)
        targets = pm.ls(to, objectsOnly=True)

        # Ensure there is at least one source and one target object
        if not all(source, targets):
            raise ValueError("Both source and target objects must exist.")

        # Retrieve creased edges from the source
        creased_edges = pm.polyCrease(source[0], query=True, value=True)

        # Loop through each target object
        for target in targets:
            # Apply crease values to corresponding edges in the target object
            for edge_id, crease_value in enumerate(creased_edges):
                if crease_value > 0:  # Apply only to creased edges
                    pm.polyCrease(f"{target}.e[{edge_id}]", value=crease_value)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
