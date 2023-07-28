# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk import core_utils, node_utils, component_utils, xform_utils


class EditUtils:
    """ """

    @staticmethod
    @core_utils.CoreUtils.undo
    def rename(objects, to, fltr="", regex=False, ignore_case=False):
        """Rename scene objects.

        Parameters:
            objects (str/obj/list): The object(s to rename. If nothing is given, all scene objects will be renamed.
            to (str): Desired name: An optional asterisk modifier can be used for formatting
                    chars - replace all.
                    *chars* - replace only.
                    *chars - replace suffix.
                    **chars - append suffix.
                    chars* - replace prefix.
                    chars** - append prefix.
            fltr (str): Optionally, filter which the given objects to rename using the following:
                    An asterisk denotes startswith*, *endswith, *contains*, and multiple search strings can be separated by pipe ('|') chars.
                    chars - Search exact.
                    *chars* - Search contains chars.
                    *chars - Search endswith chars.
                    chars* - Search startswith chars.
                    chars|chars - Search any of.  can be used in conjuction with other modifiers.
            regex (bool): If True, regular expression syntax is used instead of the default '*' and '|' modifiers.
            ignore_case (bool): Ignore case when searching. Applies only to the 'fltr' parameter's search.

        Example:
            rename(r'Cube', '*001', regex=True) #replace chars after 'fltr' on any object with a name that contains 'Cube'. ie. 'polyCube001' from 'polyCube'
            rename(r'Cube', '**001', regex=True) #append chars on any object with a name that contains 'Cube'. ie. 'polyCube1001' from 'polyCube1'
        """
        # pm.undoInfo (openChunk=1)
        objects = pm.ls(objectsOnly=1) if not objects else pm.ls(objects)

        # get the short names from the long in order to correctly format. ex. 'NUT_' from: 'CENTER_HINGE_FEMALE_GRP|NUT_'
        long_names = [obj.name() for obj in objects]
        short_names = [
            ii if ii else i for i, ii in ptk.split_at_chars(long_names)
        ]  # split the long names at the last '|' to get the short name.

        names = ptk.find_str_and_format(
            short_names,
            to,
            fltr,
            regex=regex,
            ignore_case=ignore_case,
            return_orig_strings=True,
        )
        print("# Rename: Found {} matches. #".format(len(names)))

        for i, (oldName, newName) in enumerate(names):
            oldName = long_names[
                i
            ]  # use the long name to reference the object instead.
            try:
                if pm.objExists(oldName):
                    n = pm.rename(
                        oldName, newName
                    )  # Rename the object with the new name
                    if not n == newName:
                        print(
                            '# Warning: Attempt to rename "{}" to "{}" failed. Renamed instead to "{}". #'.format(
                                oldName, newName, n
                            )
                        )
                    else:
                        print(
                            '# Result: Successfully renamed "{}" to "{}". #'.format(
                                oldName, newName
                            )
                        )

            except Exception as e:
                if not pm.ls(oldName, readOnly=True) == []:  # ignore read-only errors.
                    print(
                        '# Error: Attempt to rename "{}" to "{}" failed. {} #'.format(
                            oldName, newName, str(e).rstrip()
                        )
                    )
        # pm.undoInfo (closeChunk=1)

    @staticmethod
    @core_utils.CoreUtils.undo
    def set_case(objects=[], case="caplitalize"):
        """Rename objects following the given case.

        Parameters:
            objects (str/list): The objects to rename. default:all scene objects
            case (str): Desired case using python case operators.
                    valid: 'upper', 'lower', 'caplitalize', 'swapcase' 'title'. default:'caplitalize'
        Example:
            set_case(pm.ls(sl=1), 'upper')
        """
        # pm.undoInfo(openChunk=1)
        for obj in pm.ls(objects):
            name = obj.name()

            newName = getattr(name, case)()
            try:
                pm.rename(name, newName)
            except Exception as error:
                if not pm.ls(obj, readOnly=True) == []:  # Ignore read-only errors.
                    print(name + ": ", error)
        # pm.undoInfo(closeChunk=1)

    @staticmethod
    @core_utils.CoreUtils.undo
    def append_location_based_suffix(
        objects,
        alphanumeric=False,
        strip_trailing_ints=True,
        strip_trailing_alpha=True,
        reverse=False,
    ):
        """Rename objects with a suffix defined by its location from origin.

        Parameters:
            objects (str)(int/list): The object(s) to rename.
            alphanumeric (str): When True use an alphanumeric character as a suffix when there is less than 26 objects else use integers.
            strip_trailing_ints (bool): Strip any trailing integers. ie. 'cube123'
            strip_trailing_alpha (bool): Strip any trailing uppercase alphanumeric chars that are prefixed with an underscore.  ie. 'cube_A'
            reverse (bool): Reverse the naming order. (Farthest object first)
        """
        import string
        import re

        length = len(objects)
        if alphanumeric:
            if length <= 26:
                suffix = string.ascii_lowercase.upper()
        else:
            suffix = [str(n).zfill(len(str(length))) for n in range(length)]

        ordered_objs = xform_utils.XformUtils.order_by_distance(
            objects, reverse=reverse
        )

        newNames = {}  # the object with the new name set as a key.
        for n, obj in enumerate(ordered_objs):
            current_name = obj.name()

            while (
                (current_name[-1] == "_" or current_name[-1].isdigit())
                and strip_trailing_ints
            ) or (
                (
                    len(current_name) > 1
                    and current_name[-2] == "_"
                    and current_name[-1].isupper()
                )
                and strip_trailing_alpha
            ):
                if (
                    current_name[-2] == "_" and current_name[-1].isupper()
                ) and strip_trailing_alpha:  # trailing underscore and uppercase alphanumeric char.
                    current_name = re.sub(
                        re.escape(current_name[-2:]) + "$", "", current_name
                    )

                if (
                    current_name[-1] == "_" or current_name[-1].isdigit()
                ) and strip_trailing_ints:  # trailing underscore and integers.
                    current_name = re.sub(
                        re.escape(current_name[-1:]) + "$", "", current_name
                    )

            newNames[obj] = current_name + "_" + suffix[n]

        for (
            obj
        ) in (
            ordered_objs
        ):  # rename all with a placeholder first so that there are no conflicts.
            pm.rename(obj, "p0000000000")
        for obj in ordered_objs:  # rename all with the new names.
            pm.rename(obj, newNames[obj])

    @staticmethod
    def snap_closest_verts(obj1, obj2, tolerance=10.0, freeze_transforms=False):
        """Snap the vertices from object one to the closest verts on object two.

        Parameters:
            obj1 (obj): The object in which the vertices are moved from.
            obj2 (obj): The object in which the vertices are moved to.
            tolerance (float) = Maximum search distance.
            freeze_transforms (bool): Reset the selected transform and all of its children down to the shape level.
        """
        vertices = component_utils.ComponentUtils.get_components(obj1, "vertices")
        closestVerts = component_utils.ComponentUtils.get_closest_vertex(
            vertices, obj2, tolerance=tolerance, freeze_transforms=freeze_transforms
        )

        progressBar = "mainProgressBar"
        pm.progressBar(
            progressBar,
            edit=True,
            beginProgress=True,
            isInterruptable=True,
            status="Snapping Vertices ...",
            maxValue=len(closestVerts),
        )

        pm.undoInfo(openChunk=True)
        for v1, v2 in closestVerts.items():
            if pm.progressBar(progressBar, query=True, isCancelled=True):
                break

            v2Pos = pm.pointPosition(v2, world=True)
            pm.xform(v1, translation=v2Pos, worldSpace=True)

            pm.progressBar(progressBar, edit=True, step=1)
        pm.undoInfo(closeChunk=True)

        pm.progressBar(progressBar, edit=True, endProgress=True)

    @staticmethod
    def merge_vertices(objects, selected=False, tolerance=0.001):
        """Merge Vertices on the given objects.

        Parameters:
            objects (str/obj/list): The object(s) to merge vertices on.
            selected (bool): Merge only the currently selected components.
            tolerance (float) = The maximum merge distance.
        """
        for obj in pm.ls(objects):
            if selected:  # merge selected components.
                if pm.filterExpand(selectionMask=31):  # selectionMask=vertices
                    sel = pm.ls(obj, sl=1)
                    pm.polyMergeVertex(
                        sel,
                        distance=tolerance,
                        alwaysMergeTwoVertices=True,
                        constructionHistory=True,
                    )
                else:  # if selection type =edges or facets:
                    pm.mel.MergeToCenter()

            else:  # merge all vertices on the object.
                vertices = obj.vtx[:]  # mel expression: select -r geometry.vtx[0:1135];
                pm.polyMergeVertex(
                    vertices,
                    distance=tolerance,
                    alwaysMergeTwoVertices=False,
                    constructionHistory=False,
                )
                # return to original state
                pm.select(clear=1)
                pm.select(objects)

    @staticmethod
    def get_all_faces_on_axis(obj, axis="-x", localspace=False):
        """Get all faces on a specified axis.

        Parameters:
            obj (str/obj): The name of the geometry.
            axis (str): The representing axis. case insensitive. (valid: 'x', '-x', 'y', '-y', 'z', '-z')
            localspace (bool): Specify world or local space.

        Example:
            get_all_faces_on_axis('polyObject', 'y')
        """
        obj = pm.ls(obj)[0] if pm.ls(obj) else None

        if not obj:
            raise ValueError(f"No such object exists: {obj}")

        # Get shape nodes
        shapes = obj.getShapes()
        if not shapes:
            raise ValueError("The object has no shape nodes.")

        # Validate axis
        valid_axes = {"x": 0, "-x": 0, "y": 1, "-y": 1, "z": 2, "-z": 2}
        axis = axis.lower()
        if axis not in valid_axes:
            raise ValueError(
                "Invalid axis. Valid options are 'x', '-x', 'y', '-y', 'z', '-z'"
            )

        i = valid_axes[axis]
        # Collect faces from all geometry type shapes
        faces = []
        for shape in shapes:
            if pm.nodeType(shape) not in ["mesh", "nurbsSurface", "subdiv"]:
                continue
            faces.extend(pm.filterExpand(f"{shape.name()}.f[*]", sm=34))

        if not faces:
            raise ValueError("The shape nodes have no faces.")

        # Get faces on the specified axis
        if axis.startswith("-"):
            return [
                face for face in faces if pm.exactWorldBoundingBox(face)[i] < -0.00001
            ]
        else:
            return [
                face for face in faces if pm.exactWorldBoundingBox(face)[i] > -0.00001
            ]

    @classmethod
    def delete_along_axis(cls, objects, axis="-x"):
        """Delete components of the given mesh object along the specified axis.

        Parameters:
            obj (obj): Mesh object.
            axis (str): Axis to delete on. ie. '-x' Components belonging to the mesh object given in the 'obj' arg, that fall on this axis, will be deleted.
        """
        # Get any mesh type child nodes of obj.
        for node in (o for o in pm.ls(objects) if not node_utils.NodeUtils.is_group(o)):
            faces = cls.get_all_faces_on_axis(node, axis)
            # If all faces fall on the specified axis.
            if len(faces) == pm.polyEvaluate(node, face=1):
                pm.delete(node)  # Delete entire node
            else:
                pm.delete(faces)  # Else, delete any individual faces.

    @classmethod
    def clean_geometry(
        cls,
        objects,
        allMeshes=False,
        repair=False,
        quads=False,
        nsided=False,
        concave=False,
        holed=False,
        nonplanar=False,
        zeroGeom=False,
        zeroGeomTol=0.000010,
        zeroEdge=False,
        zeroEdgeTol=0.000010,
        zeroMap=False,
        zeroMapTol=0.000010,
        sharedUVs=False,
        nonmanifold=False,
        lamina=False,
        invalidComponents=False,
        split_non_manifold_vertex=False,
        historyOn=True,
    ):
        """Select or remove unwanted geometry from a polygon mesh.

        Parameters:
            objects (str/obj/list): The polygon objects to clean.
            allMeshes (bool): Clean all geomtry in the scene instead of only the current selection.
            repair (bool): Attempt to repair instead of just selecting geometry.
        """
        arg_list = '"{0}","{1}","{2}","{3}","{4}","{5}","{6}","{7}","{8}","{9}","{10}","{11}","{12}","{13}","{14}","{15}","{16}","{17}"'.format(
            allMeshes,
            1 if repair else 2,
            historyOn,
            quads,
            nsided,
            concave,
            holed,
            nonplanar,
            zeroGeom,
            zeroGeomTol,
            zeroEdge,
            zeroEdgeTol,
            zeroMap,
            zeroMapTol,
            sharedUVs,
            nonmanifold,
            lamina,
            invalidComponents,
        )
        command = (
            "polyCleanupArgList 4 {" + arg_list + "}"
        )  # command = 'polyCleanup '+arg_list #(not used because of arg count error, also the quotes in the arg list would need to be removed).

        if split_non_manifold_vertex:  # Split Non-Manifold Vertex
            nonManifoldVerts = cls.find_non_manifold_vertex(
                objects, select=2
            )  # Select: 0=off, 1=on, 2=on while keeping any existing vertex selections. (default: 1)
            if repair:
                for vertex in nonManifoldVerts:
                    cls.split_non_manifold_vertex(
                        vertex, select=True
                    )  # select(bool): Select the vertex after the operation. (default: True)

        pm.select(objects)
        pm.mel.eval(command)
        # print (command)

    @staticmethod
    def get_overlapping_duplicates(
        objects=[], retain_given_objects=False, select=False, verbose=False
    ):
        """Find any duplicate overlapping geometry at the object level.

        Parameters:
            objects (list): A list of objects to find duplicate overlapping geometry for. Default is selected objects, or all if nothing is selected.
            retain_given_objects (bool): Search only for duplicates of the given objects (or any selected objects if None given), and omit them from the return results.
            select (bool): Select any found duplicate objects.
            verbose (bool): Print each found object to console.

        Returns:
            (set)

        Example:
            duplicates = get_overlapping_duplicates(retain_given_objects=True, select=True, verbose=True)
        """
        scene_objs = pm.ls(transforms=1, geometry=1)  # get all scene geometry

        # attach a unique identifier consisting each objects polyEvaluate attributes, and it's bounding box center point in world space.
        scene_objs = {
            i: str(pm.objectCenter(i)) + str(pm.polyEvaluate(i))
            for i in scene_objs
            if not node_utils.NodeUtils.is_group(i)
        }
        selected_objs = pm.ls(scene_objs.keys(), sl=1) if not objects else objects

        objs_inverted = {}  # invert the dict, combining objects with like identifiers.
        for k, v in scene_objs.items():
            objs_inverted[v] = objs_inverted.get(v, []) + [k]

        duplicates = set()
        for k, v in objs_inverted.items():
            if len(v) > 1:
                if selected_objs:  # limit scope to only selected objects.
                    if set(selected_objs) & set(
                        v
                    ):  # if any selected objects in found duplicates:
                        if retain_given_objects:
                            [
                                duplicates.add(i) for i in v if i not in selected_objs
                            ]  # add any duplicated of that object, omitting the selected object.
                        else:
                            [
                                duplicates.add(i) for i in v[1:]
                            ]  # add all but the first object to the set of duplicates.
                else:
                    [
                        duplicates.add(i) for i in v[1:]
                    ]  # add all but the first object to the set of duplicates.

        if verbose:
            for i in duplicates:
                print("# Found: overlapping duplicate object: {} #".format(i))
        print("# {} overlapping duplicate objects found. #".format(len(duplicates)))

        if select:
            pm.select(duplicates)

        return duplicates

    @staticmethod
    def find_non_manifold_vertex(objects, select=1):
        """Locate a connected vertex of non-manifold geometry where the faces share a single vertex.

        Parameters:
            objects (str/obj/list): A polygon mesh, or a list of meshes.
            select (int): Select any found non-manifold vertices. 0=off, 1=on, 2=on while keeping any existing vertex selections. (default: 1)

        Returns:
            (set) any found non-manifold verts.
        """
        pm.undoInfo(openChunk=True)
        nonManifoldVerts = set()

        vertices = component_utils.ComponentUtils.get_components(objects, "vertices")
        for vertex in vertices:
            connected_faces = pm.polyListComponentConversion(
                vertex, fromVertex=1, toFace=1
            )  # pm.mel.PolySelectConvert(1) #convert to faces
            connected_faces_flat = pm.ls(
                connected_faces, flatten=1
            )  # selectedFaces = pm.ls(sl=1, flatten=1)

            # get a list of the edges of each face that is connected to the original vertex.
            edges_sorted_by_face = []
            for face in connected_faces_flat:
                connected_edges = pm.polyListComponentConversion(
                    face, fromFace=1, toEdge=1
                )  # pm.mel.PolySelectConvert(1) #convert to faces
                connected_edges_flat = [
                    str(i) for i in pm.ls(connected_edges, flatten=1)
                ]  # selectedFaces = pm.ls(sl=1, flatten=1)
                edges_sorted_by_face.append(connected_edges_flat)

            out = (
                []
            )  # 1) take first set A from list. 2) for each other set B in the list do if B has common element(s) with A join B into A; remove B from list. 3) repeat 2. until no more overlap with A. 4) put A into outpup. 5) repeat 1. with rest of list.
            while len(edges_sorted_by_face) > 0:
                first, rest = (
                    edges_sorted_by_face[0],
                    edges_sorted_by_face[1:],
                )  # first list, all other lists, of the list of lists.
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

                out.append(first)
                edges_sorted_by_face = rest

            if len(out) > 1:
                nonManifoldVerts.add(vertex)
        pm.undoInfo(closeChunk=True)

        if select == 2:
            pm.select(nonManifoldVerts, add=1)
        elif select == 1:
            pm.select(nonManifoldVerts)

        return nonManifoldVerts

    @staticmethod
    def split_non_manifold_vertex(vertex, select=True):
        """Separate a connected vertex of non-manifold geometry where the faces share a single vertex.

        Parameters:
            vertex (str/obj): A single polygon vertex.
            select (bool): Select the vertex after the operation. (default is True)
        """
        pm.undoInfo(openChunk=True)
        connected_faces = pm.polyListComponentConversion(
            vertex, fromVertex=1, toFace=1
        )  # pm.mel.PolySelectConvert(1) #convert to faces
        connected_faces_flat = pm.ls(
            connected_faces, flatten=1
        )  # selectedFaces = pm.ls(sl=1, flatten=1)

        pm.polySplitVertex(vertex)

        # get a list for the vertices of each face that is connected to the original vertex.
        verts_sorted_by_face = []
        for face in connected_faces_flat:
            connected_verts = pm.polyListComponentConversion(
                face, fromFace=1, toVertex=1
            )  # pm.mel.PolySelectConvert(1) #convert to faces
            connected_verts_flat = [
                str(i) for i in pm.ls(connected_verts, flatten=1)
            ]  # selectedFaces = pm.ls(sl=1, flatten=1)
            verts_sorted_by_face.append(connected_verts_flat)

        # 1) take first set A from list. 2) for each other set B in the list do if B has common element(s) with A join B into A; remove B from list. 3) repeat 2. until no more overlap with A. 4) put A into outpup. 5) repeat 1. with rest of list.
        out = []
        while len(verts_sorted_by_face) > 0:
            # first, *rest = verts_sorted_by_face
            first, rest = (
                verts_sorted_by_face[0],
                verts_sorted_by_face[1:],
            )
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

            out.append(first)
            verts_sorted_by_face = rest

        for vertex_set in out:
            pm.polyMergeVertex(vertex_set, distance=0.001)

        # deselect the vertices that were selected during the polyMergeVertex operation.
        pm.select(vertex_set, deselect=1)
        if select:
            pm.select(vertex, add=1)
        pm.undoInfo(closeChunk=True)

    @staticmethod
    def get_ngons(objects, repair=False):
        """Get any N-Gons from the given object using selection contraints.

        Parameters:
            objects (str/obj/list): The objects to query.
            repair (bool): Repair any found N-gons.

        Returns:
            (list)
        """
        pm.select(objects)
        # Change to Component mode to retain object highlighting
        pm.mel.changeSelectMode(1)
        # Change to Face Component Mode
        pm.selectType(smp=0, sme=1, smf=0, smu=0, pv=0, pe=1, pf=0, puv=0)
        # Select Object/s and Run Script to highlight N-Gons
        pm.polySelectConstraint(mode=3, type=0x0008, size=3)
        nGons = pm.ls(sl=1)
        pm.polySelectConstraint(disable=1)

        if repair:  # convert N-Sided Faces To Quads
            pm.polyQuad(nGons, angle=30, kgb=1, ktb=1, khe=1, ws=1)

        return nGons

    @staticmethod
    def get_overlapping_vertices(objects, threshold=0.0003):
        """Query the given objects for overlapping vertices.

        Parameters:
            objects (str/obj/list): The objects to query.
            threshold (float) = The maximum allowed distance.

        Returns:
            (list)
        """
        import maya.OpenMaya as om

        result = []
        for mfnMesh in core_utils.CoreUtils.mfn_mesh_generator(objects):
            points = om.MPointArray()
            mfnMesh.getPoints(points, om.MSpace.kWorld)

            for i in range(points.length()):
                for ii in range(points.length()):
                    if i == ii:
                        continue

                    dist = points[i].distanceTo(points[ii])
                    if dist < threshold:
                        if i not in result:
                            result.append(i)

                        if ii not in result:
                            result.append(ii)
        return result

    @classmethod
    def get_overlapping_faces(cls, objects):
        """Get any duplicate overlapping faces of the given objects.

        :Parameters:
            objects (str/obj/list): Faces or polygon objects.

        Returns:
            (list) duplicate overlapping faces.

        Example: pm.select(get_overlapping_faces(selection))
        """
        if not objects:
            return []

        elif not pm.nodeType(objects) == "mesh":  # if the objects are not faces.
            duplicates = ptk.flatten(
                [
                    cls.get_overlapping_faces(obj.faces)
                    for obj in pm.ls(objects, objectsOnly=1)
                ]
            )
            return list(duplicates)

        face, *otherFaces = pm.ls(objects)
        face_vtx_positions = [
            v.getPosition()
            for v in pm.ls(pm.polyListComponentConversion(face, toVertex=1), flatten=1)
        ]

        duplicates = []
        for otherFace in otherFaces:
            otherFace_vtx_positions = [
                v.getPosition()
                for v in pm.ls(
                    pm.polyListComponentConversion(otherFace, toVertex=1), flatten=1
                )
            ]

            if face_vtx_positions == otherFace_vtx_positions:  # duplicate found.
                duplicates.append(otherFace)
                otherFaces.remove(otherFace)

        if otherFaces:
            # after adding any found duplicates, call again with any remaining faces.
            duplicates += cls.get_overlapping_faces(otherFaces)

        return duplicates

    @staticmethod
    def get_similar_mesh(obj, tolerance=0.0, inc_orig=False, **kwargs):
        """Find similar geometry objects using the polyEvaluate command.
        Default behaviour is to compare all flags.

        Parameters:
            obj (str/obj/list): The object to find similar for.
            tolerance (float) = The allowed difference in any of the given polyEvalute flag results (that return an int, float (or list of the int or float) value(s)).
            inc_orig (bool): Include the original given obj with the return results.
            kwargs (bool): Any keyword argument 'polyEvaluate' takes. Used to filter the results.
                    ex: vertex, edge, face, uvcoord, triangle, shell, boundingBox, boundingBox2d,
                    vertexComponent, boundingBoxComponent, boundingBoxComponent2d, area, worldArea
        Returns:
            (list) Similar objects.

        Example:
            get_similar_mesh(selection, vertex=1, area=1)
        """
        lst = (
            lambda x: list(x)
            if isinstance(x, (list, tuple, set))
            else list(x.values())
            if isinstance(x, dict)
            else [x]
        )  # assure the returned result from polyEvaluate is a list of values.

        obj, *other = pm.ls(obj, long=True, transforms=True)
        objProps = lst(pm.polyEvaluate(obj, **kwargs))

        otherSceneMeshes = set(
            pm.filterExpand(pm.ls(long=True, typ="transform"), selectionMask=12)
        )  # polygon selection mask.
        similar = pm.ls(
            [
                m
                for m in otherSceneMeshes
                if ptk.are_similar(
                    objProps, lst(pm.polyEvaluate(m, **kwargs)), tolerance=tolerance
                )
                and m != obj
            ]
        )
        return similar + [obj] if inc_orig else similar

    @staticmethod
    def get_similar_topo(obj, inc_orig=False, **kwargs):
        """Find similar geometry objects using the polyCompare command.
        Default behaviour is to compare all flags.

        Parameters:
            obj (str/obj/list): The object to find similar for.
            inc_orig (bool): Include the original given obj with the return results.
            kwargs (bool): Any keyword argument 'polyCompare' takes. Used to filter the results.
                    ex: vertices, edges, faceDesc, uvSets, uvSetIndices, colorSets, colorSetIndices, userNormals
        Returns:
            (list) Similar objects.
        """
        obj, *other = pm.filterExpand(
            pm.ls(obj, long=True, tr=True), selectionMask=12
        )  # polygon selection mask.

        otherSceneMeshes = set(
            pm.filterExpand(pm.ls(long=True, typ="transform"), sm=12)
        )
        similar = pm.ls(
            [
                m
                for m in otherSceneMeshes
                if pm.polyCompare(obj, m, **kwargs) == 0 and m != obj
            ]
        )  # 0:equal,Verts:1,Edges:2,Faces:4,UVSets:8,UVIndices:16,ColorSets:32,ColorIndices:64,UserNormals=128. So a return value of 3 indicates both vertices and edges are different.
        return similar + [obj] if inc_orig else similar


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------

# deprecated ---------------------
