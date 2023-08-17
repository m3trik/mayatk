# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk import core_utils, node_utils


class GetComponentsMixin:
    """ """

    componentTypes = [  # abv, singular, plural, full, int, hex
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
        for a, s, p, f, i, h in cls.componentTypes:
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

        for t in cls.componentTypes:
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
        typ = cls.convert_alias(
            component_type
        )  # get the correct component_type variable from possible args.

        if typ not in d:
            return components
        components = pm.polyListComponentConversion(
            components, **{d[typ.lower()]: True}
        )
        return core_utils.CoreUtils.convert_array_type(
            components, returned_type=returned_type, flatten=flatten
        )

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

        return core_utils.CoreUtils.convert_array_type(
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
        etyp = core_utils.CoreUtils.get_array_type(components)
        etyp_inc = core_utils.CoreUtils.get_array_type(inc)
        etyp_exc = core_utils.CoreUtils.get_array_type(exc)

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
        result = core_utils.CoreUtils.convert_array_type(
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
        If no objects are given the current selection will be used.

        Parameters:
            objects (str/obj/list): The object(s) to get the components of. (Polygon, Polygon components)(default: current selection)
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
            get_components(obj, 'vertex', 'str', '', 'obj.vtx[2:23]') #returns: ['objShape.vtx[0]', 'objShape.vtx[1]', 'objShape.vtx[24]', 'objShape.vtx[25]']
            get_components(obj, 'vertex', 'obj', '', 'obj.vtx[:23]') #returns: [MeshVertex('objShape.vtx[24]'), MeshVertex('objShape.vtx[25]')]
            get_components(obj, 'f', 'int') #returns: {nt.Mesh('objShape'): [(0, 35)]}
            get_components(obj, 'edges') #returns: ['objShape.e[0:59]']
            get_components(obj, 'edges', 'str', 'obj.e[:2]') #returns: ['objShape.e[0]', 'objShape.e[1]', 'objShape.e[2]']
        """
        components = cls.convert_component_type(objects, component_type)

        if inc or exc:
            components = cls.filter_components(components, inc=inc, exc=exc)

        if randomize:
            components = randomize(pm.ls(components, flatten=1), randomize)

        result = core_utils.CoreUtils.convert_array_type(
            components, returned_type=returned_type, flatten=flatten
        )
        return result


class ComponentUtils(GetComponentsMixin):
    """ """

    @staticmethod
    def map_components_to_objects(components_list):
        """Maps a list of components to their respective objects.

        This function takes in a list of PyMel component objects and returns a
        dictionary where the keys are the names of the parent objects of the
        components, and the values are lists of components belonging to each object.

        Parameters:
            components_list (list): A list of PyMel component objects.

        Returns:
            dict: A dictionary mapping object names to lists of components.
                  The components are represented as PyMel objects.
        """
        objects_components_dict = {}

        for component in components_list:
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
        x,
        component_type="",
        returned_type="str",
        component_border=False,
        flatten=False,
    ):
        """Get any object border components from given component(s) or a polygon object.
        A border is defined as a hole or detached edge.

        Parameters:
            x (str/obj/list): Component(s) (or a polygon object) to find any border components for.
            component_type (str): The desired returned component type. (valid: 'vertex','edge','face', '',
                    An empty string returns the same type as the first given component, or edges if an object is given)
            returned_type (str): The desired returned object type.
                    (valid: 'str'(default), 'obj'(shape object), 'transform'(as string), 'int'(valid only at sub-object level).
            component_border (bool): Get the components that border given components instead of the mesh border.
                    (valid: 'component', 'object'(default))
            flatten (bool): Flattens the returned list of objects so that each component is it's own element.

        Returns:
            (list) components that border an open edge.

        Example:
        get_border_components('pln', 'vtx') #returns: ['plnShape.vtx[0:4]', 'plnShape.vtx[7:8]', 'plnShape.vtx[11:15]'],
            get_border_components('pln') #returns: ['plnShape.e[0:2]', 'plnShape.e[4]', 'plnShape.e[6]', 'plnShape.e[8]', 'plnShape.e[13]', 'plnShape.e[15]', 'plnShape.e[20:23]'],
            get_border_components('pln.e[:]') #returns: ['plnShape.e[0:2]', 'plnShape.e[4]', 'plnShape.e[6]', 'plnShape.e[8]', 'plnShape.e[13]', 'plnShape.e[15]', 'plnShape.e[20:23]'],
            get_border_components(['pln.e[9]','pln.e[10]', 'pln.e[12]', 'pln.e[16]'], 'f', component_border=True) #returns: ['plnShape.f[1]', 'plnShape.f[3:5]', 'plnShape.f[7]'],
            get_border_components('pln.f[3:4]', 'vtx', component_border=True) #returns: ['plnShape.vtx[4:6]', 'plnShape.vtx[8:10]'],
        """
        if not x:
            print(
                f'File "{__file__}" in get_border_components\n# Error: Operation requires a given object(s) or component(s). #',
            )
            return []

        origType = cls.get_component_type(x, "abv")
        if not origType:
            origType, x = "mesh", cls.get_components(x, "edges")
        origVerts = cls.convert_component_type(x, "vtx", flatten=True)
        origEdges = cls.convert_component_type(x, "edge", flatten=True)
        origFaces = cls.convert_component_type(x, "face", flatten=True)

        if (
            not component_type
        ):  # if no component type is specified, return the same type of component as given. in the case of mesh object, edges will be returned.
            component_type = origType if not origType == "mesh" else "e"
        else:
            component_type = cls.convert_alias(
                component_type
            )  # get the correct component_type variable from possible args.

        result = []
        if component_border:  # get edges Qthat form the border of the given components.
            for edge in origEdges:
                attachedFaces = cls.convert_component_type(edge, "face", flatten=1)
                if component_type == "f":
                    for f in attachedFaces:
                        if origType == "f" and f in origFaces:
                            continue
                        result.append(f)
                    continue
                attachedEdges = cls.convert_component_type(
                    attachedFaces, "edge", flatten=1
                )
                for e in attachedEdges:
                    if origType == "e" and e in origEdges:
                        continue
                    attachedVerts = cls.convert_component_type(e, "vtx", flatten=1)
                    for v in attachedVerts:
                        if v in origVerts:
                            result.append(v)

        else:  # get edges that form the border of the object.
            for edge in origEdges:
                attachedFaces = cls.convert_component_type(edge, "face", flatten=1)
                if len(attachedFaces) == 1:
                    result.append(edge)

        result = cls.convert_component_type(
            result, component_type
        )  # convert back to the original component type and flatten /un-flatten list.
        result = core_utils.CoreUtils.convert_array_type(
            result, returned_type=returned_type, flatten=flatten
        )
        return result

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

        a = core_utils.CoreUtils.convert_array_type(
            a, returned_type="str", flatten=True
        )
        b = core_utils.CoreUtils.convert_array_type(
            b, returned_type="str", flatten=True
        )
        vertPairsAndDistance = {}
        for v1 in a:
            v1Pos = pm.pointPosition(v1, world=1)
            for v2 in b:
                v2Pos = pm.pointPosition(v2, world=1)
                distance = ptk.get_distance(v1Pos, v2Pos)
                if distance < tolerance:
                    vertPairsAndDistance[(v1, v2)] = distance

        sorted_ = sorted(vertPairsAndDistance.items(), key=itemgetter(1))
        vertPairs = [i[0] for i in sorted_]

        return vertPairs

    @classmethod
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
        vertices = core_utils.CoreUtils.convert_array_type(
            vertices, returned_type="str", flatten=True
        )
        pm.undoInfo(openChunk=True)

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
            distance = ptk.get_distance(v1Pos, v2Pos)

            v2_convertedType = core_utils.CoreUtils.convert_array_type(
                v2, returned_type=returned_type
            )[0]
            if not tolerance:
                closestVerts[v1] = v2_convertedType
            elif distance < tolerance:
                closestVerts[v1] = v2_convertedType

        pm.delete(cpmNode)
        pm.undoInfo(closeChunk=True)

        return closestVerts

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
        return core_utils.CoreUtils.convert_array_type(
            result, returned_type=returned_type, flatten=flatten
        )

    @classmethod
    def get_shortest_path(cls, components, returned_type="str", flatten=False):
        """Get the shortest path between two components.

        Parameters:
            components (obj): A Pair of vertices or edges.
            returned_type (str): The desired returned object type.
                    valid: 'str'(default), 'obj', 'int'(valid only at sub-object level)
            flatten (bool): Flattens the returned list of objects so that each component is it's own element.

        Returns:
            (list) the components that comprise the path as strings.

        Example:
            get_edge_path('sph.e[12]', 'edgeLoop') #returns: ['sphShape.e[12]', 'sphShape.e[17]', 'sphShape.e[16]', 'sphShape.e[15]', 'sphShape.e[14]', 'sphShape.e[13]']
            get_edge_path('sph.e[12]', 'edgeLoop', 'int') #returns: [12, 17, 16, 15, 14, 13]
            get_edge_path('sph.e[12]', 'edgeRing') #returns: ['sphShape.e[0]', 'sphShape.e[6]', 'sphShape.e[12]', 'sphShape.e[18]', 'sphShape.e[24]']
            get_edge_path(['sph.e[43]', 'sph.e[46]'], 'edgeRingPath') #returns: ['sphShape.e[43]', 'sphShape.e[42]', 'sphShape.e[47]', 'sphShape.e[46]']
            get_edge_path(['sph.e[54]', 'sph.e[60]'], 'edgeLoopPath') #returns: ['sphShape.e[60]', 'sphShape.e[48]', 'sphShape.e[42]', 'sphShape.e[36]', 'sphShape.e[30]', 'sphShape.e[54]']
        """
        obj = pm.ls(components, objectsOnly=1)[0]
        ctype = cls.get_component_type(components)
        try:
            A, B = components = cls.convert_component_type(components, ctype)[:2]
        except ValueError as e:
            print(
                f'File "{__file__}" in get_shortest_path\n# Error: Operation requires exactly two components.\n\t{e}',
            )
            return []

        returnAsVerts = False
        if ctype == "vtx":
            edgesA = cls.convert_component_type(A, "e", flatten=1)
            vertsA = cls.convert_component_type(edgesA, "vtx", flatten=1)
            closestA = cls.get_closest_verts(B, [i for i in vertsA if not i == A])[0]
            edgeA = [
                e
                for e in edgesA
                if closestA[1] in cls.convert_component_type(e, "vtx", flatten=1)
            ]

            edgeB = cls.convert_component_type(B, "e", flatten=1)
            vertsB = cls.convert_component_type(edgeB, "vtx", flatten=1)
            closestB = cls.get_closest_verts(A, [i for i in vertsB if not i == B])[0]
            edgeB = [
                e
                for e in edgeB
                if closestB[1] in cls.convert_component_type(e, "vtx", flatten=1)
            ]

            components = (edgeA, edgeB)
            ctype = "e"
            returnAsVerts = True

        compNums = cls.convert_component_type(
            components, ctype, returned_type="int", flatten=True
        )

        kwargs = {
            "shortestFacePath"
            if ctype == "f"
            else "shortestEdgePathUV"
            if ctype == "uv"
            else "shortestEdgePath": compNums
        }
        compLong = set(pm.polySelect(obj, q=True, **kwargs) + compNums)

        result = cls.convert_int_to_component(
            obj, compLong, ctype, returned_type=returned_type, flatten=flatten
        )

        if returnAsVerts:
            result = cls.convert_component_type(result, "vtx", flatten=flatten)

        return result

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
    def get_normal_angle(cls, edge):
        """Get the angle between the normals of the two faces connected by an edge.

        Parameters:
            edge (MeshEdge): The edge to get the normal angle of.

        Returns:
            float: The angle between the normals of the two faces connected by the edge, in degrees.

        Raises:
            TypeError: If the input edge is not a MeshEdge.
        """
        import math

        if not isinstance(edge, pm.general.MeshEdge):
            raise TypeError(f"Input must be a MeshEdge, got {type(edge)}.")

        # Get the faces connected by the edge
        connected_faces = list(edge.connectedFaces())
        if len(connected_faces) != 2:
            return 0

        # Get the normals of the faces
        normal1 = cls.get_normal(connected_faces[0])
        normal2 = cls.get_normal(connected_faces[1])
        # Calculate the angle between the normals
        angle = normal1.angle(normal2)
        # Convert the angle from radians to degrees
        angle = math.degrees(angle)

        return angle

    @classmethod
    def average_normals(cls, objects, by_uv_shell=False):
        """Average the normals of the given objects.

        Parameters:
            objects (str/obj/list): The objects to operate on.
            by_uv_shell (bool): Average each UV shell individually.
        """
        pm.undoInfo(openChunk=True)  # Open undo chunk to make operation reversible

        if by_uv_shell:
            uv_shell_sets = cls.get_uv_shell_sets(objects)
            # Iteratively operate on each uv_shell_set
            for uv_set in uv_shell_sets:
                # Convert faces to vertices
                vertices = pm.polyListComponentConversion(
                    uv_set, fromFace=True, toVertexFace=True
                )
                # Unfreeze normals
                pm.polyNormalPerVertex(vertices, unFreezeNormal=True)
                # Soften edges to smooth normals across the entire UV shell
                pm.polySoftEdge(vertices, a=180)
        else:
            # If not by_uv_shell, directly average normals of the object or components
            vertices = pm.polyListComponentConversion(
                objects, fromFace=True, toVertexFace=True
            )
            pm.polyNormalPerVertex(vertices, unFreezeNormal=True)  # Unfreeze normals
            pm.polySoftEdge(vertices, a=180)  # Soften edges to smooth normals

        pm.undoInfo(closeChunk=True)  # Close undo chunk after operations are done

    @classmethod
    def get_edges_by_normal_angle(cls, objects, low_angle=0, high_angle=180):
        """Get a list of edges having normals between the given high and low angles.

        Parameters:
            objects (str/list)(obj): The object(s) to get edges of.
            low_angle (int): Normal angle low range.
            high_angle (int): Normal angle high range.

        Returns:
            list: Polygon edges that have normals within the specified angle range.

        Raises:
            TypeError: If the input objects are not Mesh or MeshEdge.
        """
        # Assure objects is a list
        objects = pm.ls(objects)

        edges = []
        # Iterate over the objects
        for obj in objects:
            # If the object is a transform, get its shape
            if isinstance(obj, pm.nt.Transform):
                obj = obj.getShape()

            # If the object is a mesh, get all its edges
            if isinstance(obj, pm.nt.Mesh):
                edges.extend(obj.edges)

            # If the object is an edge, add it to the list
            elif isinstance(obj, pm.general.MeshEdge):
                edges.append(obj)

            else:
                raise TypeError(f"Input must be a Mesh or MeshEdge, got {type(obj)}.")

        # Filter the edges based on their normal angle
        edges = [
            edge
            for edge in edges
            if low_angle <= cls.get_normal_angle(edge) <= high_angle
        ]

        return edges

    @classmethod
    def set_edge_hardness(
        cls, x, angle_threshold, upper_hardness=None, lower_hardness=None
    ):
        """Sets the hardness (softness) of edges in the provided objects based on their normal angles.
        The function recursively processes lists, tuples, and sets of objects, applying the hardness settings
        to each item individually. It also supports PyMel Transform, Mesh, and MeshEdge objects.
        If an edge's normal angle is greater than or equal to the given threshold, the edge is considered
        for upper hardness application. If an edge's normal angle is less than the threshold, the edge is
        considered for lower hardness application.

        Parameters:
            cls (class): The class that the method is part of.
            x (str, pm.nt.Transform, pm.nt.Mesh, pm.general.MeshEdge, list/tuple/set of these types):
                The objects whose edge hardness is to be set. For string input, it should be in the format 'object.e[start:end]'.
            angle_threshold (float): The threshold of the normal angle in degrees to determine hardness.
            upper_hardness (float, optional): The hardness to apply to edges with a normal angle greater
                than or equal to the threshold. If None, these edges are not processed. Value should be between 0 and 180.
            lower_hardness (float, optional): The hardness to apply to edges with a normal angle less
                than the threshold. If None, these edges are not processed. Value should be between 0 and 180.

        Returns:
            None: This function doesn't return anything; it modifies the provided objects in-place.

        Raises:
            TypeError: If the 'x' argument is not of the correct type.
        """
        # If x is a list, handle each item recursively
        if isinstance(x, (list, tuple, set)):
            for item in x:
                cls.set_edge_hardness(
                    item, angle_threshold, upper_hardness, lower_hardness
                )
            return

        # If x is a PyMel object, handle it accordingly
        if isinstance(x, pm.nt.Transform):
            is_group = node_utils.NodeUtils.is_group(x)
            if is_group:
                grp_children = node_utils.NodeUtils.get_unique_children(x)
                cls.set_edge_hardness(
                    grp_children, angle_threshold, upper_hardness, lower_hardness
                )
                return
            shape = x.getShape()
            x = f"{shape.name()}.e[0:{len(shape.edges)-1}]"
        elif isinstance(x, pm.nt.Mesh):
            # If it's a mesh, operate on all edges
            x = f"{x.name()}.e[0:{len(x.edges())-1}]"
        elif isinstance(x, pm.general.MeshEdge):
            x = x.name()

        # Ensure x is a list of strings
        if isinstance(x, str):
            x = [x]

        upper_hardness_edges = []
        lower_hardness_edges = []

        for obj_name in x:
            # Create a PyNode of the mesh object
            mesh_obj = pm.PyNode(obj_name.split(".")[0])
            # Extract edge indices from obj_name using regex
            edge_indices = obj_name.split("[")[-1].split("]")[0].split(":")
            # Generate all indices between start and end
            edge_start = int(edge_indices[0])
            edge_end = int(edge_indices[1]) if len(edge_indices) > 1 else edge_start
            edge_indices = list(range(edge_start, edge_end + 1))
            # Iterate over edges
            for edge_index in edge_indices:
                edge = mesh_obj.edges[edge_index]
                edge_angle = cls.get_normal_angle(edge)
                # Check edge angle and add to respective list
                if upper_hardness is not None and edge_angle >= angle_threshold:
                    upper_hardness_edges.append(edge)
                elif lower_hardness is not None and edge_angle < angle_threshold:
                    lower_hardness_edges.append(edge)

        # Apply softness to edges in the lists
        if upper_hardness_edges:
            pm.polySoftEdge(upper_hardness_edges, a=upper_hardness, ch=True)

        if lower_hardness_edges:
            pm.polySoftEdge(lower_hardness_edges, a=lower_hardness, ch=True)

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

    @staticmethod
    def transfer_normals(source, target):
        """Transfer normal information from one object to another.

        Parameters:
            source (str/obj/list): The transform node to copy normals from.
            target (str/obj/list): The transform node(s) to copy normals to.
        """
        pm.undoInfo(openChunk=1)
        s, *other = pm.ls(source)
        # store source transforms
        sourcePos = pm.xform(s, q=1, t=1, ws=1)
        sourceRot = pm.xform(s, q=1, ro=1, ws=1)
        sourceScale = pm.xform(s, q=1, s=1, ws=1)

        for t in pm.ls(target):
            # store target transforms
            targetPos = pm.xform(t, q=1, t=1, ws=1)
            targetRot = pm.xform(t, q=1, ro=1, ws=1)
            targetScale = pm.xform(t, q=1, s=1, ws=1)

            # move target to source position
            pm.xform(t, t=sourcePos, ws=1)
            pm.xform(t, ro=sourceRot, ws=1)
            pm.xform(t, s=sourceScale, ws=1)

            # copy normals
            pm.polyNormalPerVertex(t, ufn=0)
            pm.transferAttributes(s, t, pos=0, nml=1, uvs=0, col=0, spa=0, sm=3, clb=1)
            pm.delete(t, ch=1)

            # restore t position
            pm.xform(t, t=targetPos, ws=1)
            pm.xform(t, ro=targetRot, ws=1)
            pm.xform(t, s=targetScale, ws=1)
        pm.undoInfo(closeChunk=1)

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
        try:
            lowRange, highRange = num_of_connected
        except TypeError:
            lowRange = highRange = num_of_connected

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

        result = core_utils.CoreUtils.convert_array_type(
            result, returned_type=returned_type
        )
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

        vtxID = core_utils.CoreUtils.convert_array_type(vertex, "int")[0]
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
    def orient_shells(objects):
        """Rotate UV shells to run parallel with the most adjacent U or V axis of their bounding box.

        Parameters:
            objects (str/obj/list): Polygon mesh objects and/or components.
        """
        for obj in pm.ls(objects, objectsOnly=1):
            # filter components for only this object.
            obj_compts = [i for i in objects if obj in pm.ls(i, objectsOnly=1)]
            pm.polyLayoutUV(
                obj_compts,
                flipReversed=0,
                layout=0,
                layoutMethod=1,
                percentageSpace=0.2,
                rotateForBestFit=3,
                scale=0,
                separate=0,
            )

    @classmethod
    def move_to_uv_space(cls, objects, u, v, relative=True):
        """Move objects to the given u and v coordinates.

        Parameters:
            objects (str/obj/list): The object(s) to move.
            u (int): u coordinate.
            v (int): v coordinate.
            relative (bool): Move relative or absolute.
        """
        # Convert the objects to UVs
        uvs = pm.polyListComponentConversion(objects, fromFace=True, toUV=True)
        uvs = pm.ls(uvs, flatten=True)

        # Move the UVs to the given u and v coordinates
        pm.polyEditUV(uvs, u=u, v=v, relative=relative)

    @classmethod
    def get_uv_shell_sets(cls, objects=None, returned_type="shells"):
        """Get UV shells and their corresponding sets of faces.

        Parameters:
            objects (obj/list): Polygon object(s) or Polygon face(s).
            returned_type (str): The desired returned type. valid values are: 'shells', 'IDs'. If None is given, the full dict will be returned.

        Returns:
            (list)(dict) dependant on the given returned_type arg. ex. {0L:[[MeshFace(u'pShape.f[0]'), MeshFace(u'pShape.f[1]')], 1L:[[MeshFace(u'pShape.f[2]'), MeshFace(u'pShape.f[3]')]}
        """
        faces = cls.get_components(objects, "faces", flatten=1)

        shells = {}
        for face in faces:
            shell_Id = pm.polyEvaluate(face, uvShellIds=True)

            try:
                shells[shell_Id[0]].append(face)
            except KeyError:
                try:
                    shells[shell_Id[0]] = [face]
                except IndexError:
                    pass

        if returned_type == "shells":
            shells = list(shells.values())
        elif returned_type == "IDs":
            shells = shells.keys()

        return shells

    @staticmethod
    def get_uv_shell_border_edges(objects):
        """Get the edges that make up any UV islands of the given objects.

        Parameters:
            objects (str/obj/list): Polygon objects, mesh UVs, or Edges.

        Returns:
            (list): UV border edges.
        """
        uv_border_edges = []
        for obj in pm.ls(objects):
            # If the obj is a mesh object, get its shape
            if isinstance(obj, pm.nt.Transform):
                obj = obj.getShape()

            # If the obj is a mesh shape, get its UV borders
            if isinstance(obj, pm.nt.Mesh):
                # Get the connected edges to the selected UVs
                connected_edges = pm.polyListComponentConversion(
                    obj, fromUV=True, toEdge=True
                )
                connected_edges = pm.ls(connected_edges, flatten=True)
            elif isinstance(obj, pm.general.MeshEdge):
                # If the object is already an edge, no conversion is necessary
                connected_edges = pm.ls(obj, flatten=True)
            elif isinstance(obj, pm.general.MeshUV):
                # If the object is a UV, convert it to its connected edges
                connected_edges = pm.polyListComponentConversion(
                    obj, fromUV=True, toEdge=True
                )
                connected_edges = pm.ls(connected_edges, flatten=True)
            else:
                raise ValueError(f"Unsupported object type: {type(obj)}")

            for edge in connected_edges:
                edge_uvs = pm.ls(
                    pm.polyListComponentConversion(edge, tuv=True), fl=True
                )
                edge_faces = pm.ls(
                    pm.polyListComponentConversion(edge, tf=True), fl=True
                )
                if (
                    len(edge_uvs) > 2 or len(edge_faces) < 2
                ):  # If an edge has more than two uvs or less than 2 faces, it's a uv border edge.
                    uv_border_edges.append(edge)

        return uv_border_edges

    @staticmethod
    def transfer_uvs(source, target):
        """ """
        import maya.api.OpenMaya as om2

        # Create a MSelectionList
        sList = om2.MSelectionList()

        # Add objects to the list (accepts both PyNodes and strings)
        if isinstance(source, str):
            sList.add(source)
        else:
            sList.add(source.name())
        if isinstance(target, str):
            sList.add(target)
        else:
            sList.add(target.name())

        # Get MObjects of source and target
        source_obj = sList.getDagPath(0).extendToShape()  # extend to shape node
        target_obj = sList.getDagPath(1).extendToShape()  # extend to shape node

        # Get the function set for source and target
        sFnMesh = om2.MFnMesh(source_obj)
        tFnMesh = om2.MFnMesh(target_obj)

        # Get uv count for source
        uArray, vArray = sFnMesh.getUVs()

        # Clear target UVs and set them with source UVs
        tFnMesh.clearUVs()
        tFnMesh.setUVs(uArray, vArray)

        # Get the number of polygons in source mesh
        poly_count = sFnMesh.numPolygons

        # Assign UVs to faces
        for i in range(poly_count):
            count = sFnMesh.polygonVertexCount(i)
            for j in range(count):
                uv_id = sFnMesh.getPolygonUVid(i, j)
                tFnMesh.assignUV(i, j, uv_id)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
