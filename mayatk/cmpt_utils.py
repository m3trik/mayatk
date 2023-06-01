# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)

from pythontk import Iter, Math

# from this package:
from mayatk import misc_utils


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
            except Exception as error:
                print(
                    'File "{}" in get_component_type\n# Error: Not a valid component. #\n {}{}'.format(
                        __file__, error, "(empty string)" if component == "" else ""
                    )
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

        if not typ in d:
            return components
        components = pm.polyListComponentConversion(
            components, **{d[typ.lower()]: True}
        )
        return misc_utils.Misc.convert_array_type(
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

        Example: convert_int_to_component('cyl', range(4), 'f') #returns: ['cylShape.f[0:3]']
        """
        obj = pm.ls(obj, objectsOnly=True)[0]
        objName = obj.name()

        if not flatten:
            n = lambda c: "{}:{}".format(c[0], c[-1]) if len(c) > 1 else str(c[0])
            result = [
                "{}.{}[{}]".format(objName, component_type, n(c))
                for c in Iter.split_list(integers, "range")
            ]
        else:
            result = ["{}.{}[{}]".format(objName, component_type, c) for c in integers]

        return misc_utils.Misc.convert_array_type(
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
        etyp = misc_utils.Misc.get_array_type(components)
        etyp_inc = misc_utils.Misc.get_array_type(inc)
        etyp_exc = misc_utils.Misc.get_array_type(exc)

        if etyp_inc == "int" or etyp_exc == "int":
            try:
                obj = pm.ls(components, objectsOnly=True)[0]
            except IndexError as error:
                print(
                    'File "{}" in filter_components\n# Error: Operation requires at least one component. #\n {}'.format(
                        __file__, error
                    )
                )
                return []

        if etyp_inc == "int":
            inc = cls.convert_int_to_component(obj, inc, typ)
        inc = pm.ls(inc, flatten=True)

        if etyp_exc == "int":
            exc = cls.convert_int_to_component(obj, exc, typ)
        exc = pm.ls(exc, flatten=True)

        components = pm.ls(components, flatten=True)

        filtered = Iter.filter_list(components, inc=inc, exc=exc)
        result = misc_utils.Misc.convert_array_type(
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
        get_components('obj', 'vertex', 'str', '', 'obj.vtx[2:23]') #returns: ['objShape.vtx[0]', 'objShape.vtx[1]', 'objShape.vtx[24]', 'objShape.vtx[25]']
        get_components('obj', 'vertex', 'obj', '', 'obj.vtx[:23]') #returns: [MeshVertex('objShape.vtx[24]'), MeshVertex('objShape.vtx[25]')]
        get_components('obj', 'f', 'int') #returns: {nt.Mesh('objShape'): [(0, 35)]}
        get_components('obj', 'edges') #returns: ['objShape.e[0:59]']
        get_components('obj', 'edges', 'str', 'obj.e[:2]') #returns: ['objShape.e[0]', 'objShape.e[1]', 'objShape.e[2]']
        """

        components = cls.convert_component_type(objects, component_type)

        if inc or exc:
            components = cls.filter_components(components, inc=inc, exc=exc)

        if randomize:
            components = randomize(pm.ls(components, flatten=1), randomize)

        result = misc_utils.Misc.convert_array_type(
            components, returned_type=returned_type, flatten=flatten
        )
        return result


class Cmpt(GetComponentsMixin):
    """ """

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
    def get_contigious_islands(cls, faces, face_islands=[]):
        """Get a list containing sets of adjacent polygon faces grouped by islands.

        Parameters:
                faces (str/obj/list): The polygon faces to be filtered for adjacent.
                face_islands (list/optional): list of sets. ability to add faces from previous calls to the return value.

        Returns:
                (list): of sets of adjacent faces.

        Example: get_contigious_islands('obj.f[21:26]') #returns: [{'objShape.f[22]', 'objShape.f[21]', 'objShape.f[23]'}, {'objShape.f[26]', 'objShape.f[24]', 'objShape.f[25]'}]
        """
        sets = []
        faces = pm.ls(faces, flatten=1)
        for face in faces:
            edges = pm.polyListComponentConversion(face, fromFace=1, toEdge=1)
            borderFaces = cls.convert_component_type(edges, "face", "obj", flatten=1)
            set_ = set([str(f) for f in borderFaces if f in faces])
            if set_:
                sets.append(set_)

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

        Example: get_islands('combined_obj') #returns: [['combined_obj.f[0]', 'combined_obj.f[5]', ..etc, ['combined_obj.f[15]', ..etc]]
        """
        num_shells = pm.polyEvaluate(obj, shell=True)
        num_faces = pm.polyEvaluate(obj, face=True)

        unprocessed = set(range(num_faces))

        shells = []
        while unprocessed:
            index = next(iter(unprocessed))  # face_index
            faces = pm.polySelect(
                obj, extendToShell=index, noSelection=True
            )  # shell faces

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
                'File "{}" in get_border_components\n# Error: Operation requires a given object(s) or component(s). #'.format(
                    __file__
                )
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
        result = misc_utils.Misc.convert_array_type(
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

        a = misc_utils.Misc.convert_array_type(a, returned_type="str", flatten=True)
        b = misc_utils.Misc.convert_array_type(b, returned_type="str", flatten=True)
        vertPairsAndDistance = {}
        for v1 in a:
            v1Pos = pm.pointPosition(v1, world=1)
            for v2 in b:
                v2Pos = pm.pointPosition(v2, world=1)
                distance = Math.get_distance(v1Pos, v2Pos)
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
        vertices = misc_utils.Misc.convert_array_type(
            vertices, returned_type="str", flatten=True
        )
        pm.undoInfo(openChunk=True)

        if freeze_transforms:
            pm.makeIdentity(obj, apply=True)

        obj2Shape = pm.listRelatives(obj, children=1, shapes=1)[
            0
        ]  # pm.listRelatives(obj, fullPath=False, shapes=True, noIntermediate=True)

        cpmNode = pm.ls(pm.createNode("closestPointOnMesh"))[
            0
        ]  # create a closestPointOnMesh node.
        pm.connectAttr(
            obj2Shape.outMesh, cpmNode.inMesh, force=1
        )  # object's shape mesh output to the cpm node.

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
            distance = Math.get_distance(v1Pos, v2Pos)

            v2_convertedType = misc_utils.Misc.convert_array_type(
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
                'File "{}" in get_edge_path\n# Error: Operation requires at least two components. #\n Edges given: {}'.format(
                    __file__, cnums
                )
            )
            return []

        if path == "edgeRing":
            edgesLong = pm.polySelect(obj, query=1, edgeRing=cnums)  # (e..)

        elif path == "edgeRingPath":
            edgesLong = pm.polySelect(
                obj, query=1, edgeRingPath=(cnums[0], cnums[1])
            )  # (e, e)
            if not edgesLong:
                print(
                    'File "{}" in get_edge_path\n# Error: get_edge_path: Operation requires two edges that are on the same edge ring. #\n   Edges given: {}, {}'.format(
                        __file__, cnums[0], cnums[1]
                    )
                )
                return []

        elif path == "edgeLoopPath":
            edgesLong = pm.polySelect(
                obj, query=1, edgeLoopPath=(cnums[0], cnums[1])
            )  # (e, e)
            if not edgesLong:
                print(
                    'File "{}" in get_edge_path\n# Error: get_edge_path: Operation requires two edges that are on the same edge loop. #\n   Edges given: {}, {}'.format(
                        __file__, cnums[0], cnums[1]
                    )
                )
                return []
        else:  #'edgeLoop'
            edgesLong = pm.polySelect(obj, query=1, edgeLoop=cnums)  # (e..)

        objName = obj.name()
        result = Iter.remove_duplicates(
            ["{}.e[{}]".format(objName, e) for e in edgesLong]
        )
        return misc_utils.Misc.convert_array_type(
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
        except ValueError as error:
            print(
                'File "{}" in get_shortest_path\n# Error: Operation requires exactly two components. #\n  {}'.format(
                    __file__, error
                )
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
        compLong = set(pm.polySelect(obj, query=1, **kwargs) + compNums)

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

        # Get the normals of the faces
        normal1 = cls.get_normal(connected_faces[0])
        normal2 = cls.get_normal(connected_faces[1])

        # Calculate the angle between the normals
        angle = normal1.angle(normal2)

        # Convert the angle from radians to degrees
        angle = math.degrees(angle)

        return angle

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

        ex. faces = filter_components_by_connection_count('sph.f[:]', 4, 'e') #returns faces with four connected edges (four sided faces).
        ex. verts = filter_components_by_connection_count('pln.vtx[:]', (0,2), 'e') #returns vertices with up to two connected edges.
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

        result = misc_utils.Misc.convert_array_type(result, returned_type=returned_type)
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

        vtxID = misc_utils.Misc.convert_array_type(vertex, "int")[0]
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


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# deprecated:
# --------------------------------------------------------------------------------------------

# def filter_components(cls, frm, inc=[], exc=[]):
#       '''Filter the given 'frm' list for the items in 'exc'.

#       Parameters:
#           frm (str/obj/list): The components(s) to filter.
#           inc (str/obj/list): The component(s) to include.
#           exc (str/obj/list): The component(s) to exclude.
#                               (exlude take precidence over include)
#       Returns:
#           (list)

#       Example: filter_components('obj.vtx[:]', 'obj.vtx[1:23]') #returns: [MeshVertex('objShape.vtx[0]'), MeshVertex('objShape.vtx[24]'), MeshVertex('objShape.vtx[25]')]
#       '''
#       exc = pm.ls(exc, flatten=True)
#       if not exc:
#           return frm

#       c, *other = components = pm.ls(frm, flatten=True)
#       #determine the type of items in 'exc' by sampling the first element.
#       if isinstance(c, str):
#           if 'Shape' in c:
#               rtn = 'transform'
#           else:
#               rtn = 'str'
#       elif isinstance(c, int):
#           rtn = 'int'
#       else:
#           rtn = 'obj'

#       if exc and isinstance(exc[0], int): #attempt to create a component list from the given integers. warning: this will only exclude from a single object.
#           obj = pm.ls(frm, objectsOnly=1)
#           if len(obj)>1:
#               return frm
#           component_type = cls.get_component_type(frm[0])
#           typ = cls.convert_alias(component_type) #get the correct component_type variable from possible args.
#           exc = ["{}.{}[{}]".format(obj[0], typ, n) for n in exc]

#       if inc and isinstance(inc[0], int): #attempt to create a component list from the given integers. warning: this will only exclude from a single object.
#           obj = pm.ls(frm, objectsOnly=1)
#           if len(obj)>1:
#               return frm
#           component_type = cls.get_component_type(frm[0])
#           typ = cls.convert_alias(component_type) #get the correct component_type variable from possible args.
#           inc = ["{}.{}[{}]".format(obj[0], typ, n) for n in inc]

#       inc = misc_utils.Misc.convert_array_type(inc, returned_type=rtn, flatten=True) #assure both lists are of the same type for comparison.
#       exc = misc_utils.Misc.convert_array_type(exc, returned_type=rtn, flatten=True)
#       return [i for i in components if i not in exc and (inc and i in inc)]
