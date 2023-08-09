# !/usr/bin/python
# coding=utf-8
import os
import unittest
import inspect
import maya.OpenMaya as om
import pymel.core as pm
from mayatk import ComponentUtils


# sfr = pm.melGlobals['cmdScrollFieldReporter']
# pm.cmdScrollFieldReporter(sfr, edit=1, clear=1)


class Main(unittest.TestCase):
    """Main test class."""

    def perform_test(self, cases):
        """Execute the test cases."""
        for case in cases:
            if isinstance(case, str):
                expression = case
                expected_result = cases[case]
                method_name = str(expression).split("(")[0]
            else:
                result, expected_result = case
                method_name = result.__class__.__name__
                expression = None

            try:
                path = os.path.abspath(inspect.getfile(eval(method_name)))
            except (TypeError, IOError):
                path = ""

            if expression:
                result = eval(expression)

            self.assertEqual(
                result,
                expected_result,
                f"\n\n# Error: {path}\n#\tCall: {method_name}({', '.join(map(str, function_args)) if 'function_args' in locals() else ''})\n#\tExpected {type(expected_result)}: {expected_result}\n#\tReturned {type(result)}: {result}",
            )

    @staticmethod
    def replace_mem_address(obj):
        """Replace memory addresses in a string representation of an object with a fixed format of '0x00000000000'.

        Parameters:
                obj (object): The input object. The function first converts this object to a string using the `str` function.

        Returns:
                (str) The string representation of the object with all memory addresses replaced.

        Example:
                >>> replace_mem_address("<class 'str'> <PySide2.QtWidgets.QWidget(0x1ebe2677e80, name='MayaWindow') at 0x000001EBE6D48500>")
                "<class 'str'> <PySide2.QtWidgets.QWidget(0x00000000000, name='MayaWindow') at 0x00000000000>"
        """
        import re

        return re.sub(r"0x[a-fA-F\d]+", "0x00000000000", str(obj))


class CmptUtils_test(Main, ComponentUtils):
    """
    set object mode:
            pm.selectMode(object=1)

    set component mode:
            pm.selectMode(component=1)

    set component mode type:
            pm.selectType(allObjects=1)
            pm.selectType(mc=1)
            pm.selectType(vertex=1)
            pm.selectType(edge=1)
            pm.selectType(facet=1)
            pm.selectType(polymeshUV=1)
            pm.selectType(meshUVShell=1)
    """

    # Tear down the any previous test by creating a new scene:
    pm.mel.file(new=True, force=True)

    # assemble the test scene:
    if not pm.objExists("cyl"):
        cyl = pm.polyCylinder(
            radius=5,
            height=10,
            subdivisionsX=12,
            subdivisionsY=1,
            subdivisionsZ=1,
            name="cyl",
        )

    if not pm.objExists("pln"):
        pln = pm.polyPlane(
            width=20, height=20, subdivisionsX=3, subdivisionsY=3, name="pln"
        )

    if not pm.objExists("sph"):
        sph = pm.polySphere(radius=8, subdivisionsX=6, subdivisionsY=6, name="sph")

    def test_getComponentType(self):
        """ """
        self.perform_test(
            {
                "self.get_component_type('cyl.e[:]')": "e",
                "self.get_component_type('cyl.vtx[:]', 'abv')": "vtx",
                "self.get_component_type('cyl.e[:]', 'int')": 32,
                "self.get_component_type('cyl.e[:]', 'hex')": 0x8000,
            }
        )

    def test_convertAlias(self):
        """ """
        self.perform_test(
            {
                "self.convert_alias('vertex', 'hex')": 0x0001,
                "self.convert_alias(0x0001, 'full')": "Polygon Vertex",
            }
        )

    def test_convertComponentType(self):
        """ """
        self.perform_test(
            {
                "self.convert_component_type('cylShape.vtx[:2]', 'vertex')": [
                    "cylShape.vtx[0:2]"
                ],
                "self.convert_component_type('cylShape.vtx[:2]', 'face')": [
                    "cylShape.f[0:2]",
                    "cylShape.f[11:14]",
                    "cylShape.f[23]",
                ],
                "self.convert_component_type('cylShape.vtx[:2]', 'edge')": [
                    "cylShape.e[0:2]",
                    "cylShape.e[11]",
                    "cylShape.e[24:26]",
                    "cylShape.e[36:38]",
                ],
                "self.convert_component_type('cylShape.vtx[:2]', 'uv')": [
                    "cylShape.map[0:2]",
                    "cylShape.map[12:14]",
                    "cylShape.map[24]",
                ],
            }
        )

    def test_convertIntToComponent(self):
        """ """
        self.perform_test(
            {
                "self.convert_int_to_component('cyl', range(4), 'f')": [
                    "cylShape.f[0:3]"
                ],
                "self.convert_int_to_component('cyl', range(4), 'f', 'int', flatten=True)": [
                    0,
                    1,
                    2,
                    3,
                ],
            }
        )

    def test_filterComponents(self):
        """ """
        self.perform_test(
            {
                "self.filter_components('cyl.vtx[:]', 'cyl.vtx[:2]', 'cyl.vtx[1:23]')": [
                    "cylShape.vtx[0]"
                ],
                "self.filter_components('cyl.f[:]', range(2), range(1, 23))": [
                    "cylShape.f[0]"
                ],
            }
        )

    def test_getComponents(self):
        """ """
        self.assertEqual(
            self.get_components("cyl", "vertex", "str", "", "cyl.vtx[2:23]"),
            [
                "cylShape.vtx[0]",
                "cylShape.vtx[1]",
                "cylShape.vtx[24]",
                "cylShape.vtx[25]",
            ],
        )
        self.assertEqual(
            str(self.get_components("cyl", "vertex", "cyl", "", "cyl.vtx[:23]")),
            "[MeshVertex('cylShape.vtx[24]'), MeshVertex('cylShape.vtx[25]')]",
        )
        self.assertEqual(self.get_components("cyl", "f", "int"), [0, 35])
        self.assertEqual(self.get_components("cyl", "edges"), ["cylShape.e[0:59]"])
        self.assertEqual(
            self.get_components("cyl", "edges", "str", "cyl.e[:2]"),
            [
                "cylShape.e[0]",
                "cylShape.e[1]",
                "cylShape.e[2]",
            ],
        )

    def test_map_components_to_objects(self):
        """Test mapping of components to their respective objects."""
        # Create a sphere
        pm.polySphere(name="mySphere")[0]  # This is the transform node

        actual = self.map_components_to_objects(
            [pm.MeshFace(f"mySphereShape.f[{i}]") for i in range(5)]
        )
        expected = {
            "mySphereShape": [
                pm.MeshFace("mySphereShape.f[0]"),
                pm.MeshFace("mySphereShape.f[1]"),
                pm.MeshFace("mySphereShape.f[2]"),
                pm.MeshFace("mySphereShape.f[3]"),
                pm.MeshFace("mySphereShape.f[4]"),
            ]
        }
        self.assertEqual(actual, expected)

    def test_getContigiousEdges(self):
        """ """
        self.assertEqual(
            self.get_contigious_edges(["cyl.e[:2]"]),
            [{"cylShape.e[1]", "cylShape.e[0]", "cylShape.e[2]"}],
        )
        self.assertEqual(
            self.get_contigious_edges(["cyl.f[0]"]),
            [
                {
                    "cylShape.e[24]",
                    "cylShape.e[0]",
                    "cylShape.e[25]",
                    "cylShape.e[12]",
                }
            ],
        )

    def test_getContigiousIslands(self):
        actual = self.get_contigious_islands("cyl.f[21:26]")
        expected = [
            {"cylShape.f[22]", "cylShape.f[21]", "cylShape.f[23]"},
            {"cylShape.f[26]", "cylShape.f[24]", "cylShape.f[25]"},
        ]

        # Convert each set to a sorted tuple, and sort the lists
        actual_sorted = sorted(tuple(sorted(s)) for s in actual)
        expected_sorted = sorted(tuple(sorted(s)) for s in expected)

        self.assertEqual(actual_sorted, expected_sorted)

    def test_getIslands(self):
        """ """
        if not pm.objExists("cmb"):  # create two objects and combine them.
            pm.polyCylinder(
                radius=5,
                height=10,
                subdivisionsX=5,
                subdivisionsY=1,
                subdivisionsZ=1,
                name="cmbA",
            )
            pm.polyCylinder(
                radius=5,
                height=6,
                subdivisionsX=5,
                subdivisionsY=1,
                subdivisionsZ=1,
                name="cmbB",
            )
            # polyUnite returns a tuple of the resulting object and the node that performed the operation.
            cmb, _ = pm.polyUnite(
                "cmbA", "cmbB", ch=1, mergeUVSets=1, centerPivot=1, name="cmb"
            )

        # get_islands returns a generator so convert to list.
        self.assertEqual(
            list(self.get_islands(cmb)),
            [
                [
                    "cmb.f[0]",
                    "cmb.f[5]",
                    "cmb.f[4]",
                    "cmb.f[9]",
                    "cmb.f[6]",
                    "cmb.f[1]",
                    "cmb.f[10]",
                    "cmb.f[11]",
                    "cmb.f[14]",
                    "cmb.f[8]",
                    "cmb.f[7]",
                    "cmb.f[3]",
                    "cmb.f[13]",
                    "cmb.f[2]",
                    "cmb.f[12]",
                ],
                [
                    "cmb.f[15]",
                    "cmb.f[20]",
                    "cmb.f[19]",
                    "cmb.f[24]",
                    "cmb.f[21]",
                    "cmb.f[16]",
                    "cmb.f[25]",
                    "cmb.f[26]",
                    "cmb.f[29]",
                    "cmb.f[23]",
                    "cmb.f[22]",
                    "cmb.f[18]",
                    "cmb.f[28]",
                    "cmb.f[17]",
                    "cmb.f[27]",
                ],
            ],
        )

    def test_getBorderComponents(self):
        """ """
        self.perform_test(
            {
                "self.get_border_components('pln', 'vtx')": [
                    "plnShape.vtx[0:4]",
                    "plnShape.vtx[7:8]",
                    "plnShape.vtx[11:15]",
                ],
                "self.get_border_components('pln', 'face')": [
                    "plnShape.f[0:3]",
                    "plnShape.f[5:8]",
                ],
                "self.get_border_components('pln')": [
                    "plnShape.e[0:2]",
                    "plnShape.e[4]",
                    "plnShape.e[6]",
                    "plnShape.e[8]",
                    "plnShape.e[13]",
                    "plnShape.e[15]",
                    "plnShape.e[20:23]",
                ],
                "self.get_border_components('pln.e[:]')": [
                    "plnShape.e[0:2]",
                    "plnShape.e[4]",
                    "plnShape.e[6]",
                    "plnShape.e[8]",
                    "plnShape.e[13]",
                    "plnShape.e[15]",
                    "plnShape.e[20:23]",
                ],
                "self.get_border_components(['pln.e[9]','pln.e[10]', 'pln.e[12]', 'pln.e[16]'], 'f', component_border=True)": [
                    "plnShape.f[1]",
                    "plnShape.f[3:5]",
                    "plnShape.f[7]",
                ],
                "self.get_border_components('pln.f[3:4]', 'f', component_border=True)": [
                    "plnShape.f[0:1]",
                    "plnShape.f[5:7]",
                ],
                "self.get_border_components('pln.f[3:4]', 'vtx', component_border=True)": [
                    "plnShape.vtx[4:6]",
                    "plnShape.vtx[8:10]",
                ],
                "self.get_border_components('pln.vtx[6]', 'e', component_border=True)": [
                    "plnShape.e[5]",
                    "plnShape.e[9]",
                    "plnShape.e[11:12]",
                ],
            }
        )

    def test_getClosestVerts(self):
        """ """
        self.perform_test(
            {
                "self.get_closest_verts('pln.vtx[:10]', 'pln.vtx[11:]', 6.667)": [
                    ("plnShape.vtx[7]", "plnShape.vtx[11]"),
                    ("plnShape.vtx[8]", "plnShape.vtx[12]"),
                    ("plnShape.vtx[9]", "plnShape.vtx[13]"),
                    ("plnShape.vtx[10]", "plnShape.vtx[11]"),
                    ("plnShape.vtx[10]", "plnShape.vtx[14]"),
                ],
            }
        )

    def test_getClosestVertex(self):
        """ """
        self.perform_test(
            {
                "self.get_closest_vertex('plnShape.vtx[0]', 'cyl', returned_type='int')": {
                    "plnShape.vtx[0]": 6
                },
                "self.get_closest_vertex('plnShape.vtx[0]', 'cyl')": {
                    "plnShape.vtx[0]": "cylShape.vtx[6]"
                },
                "self.get_closest_vertex('plnShape.vtx[2:3]', 'cyl')": {
                    "plnShape.vtx[2]": "cylShape.vtx[9]",
                    "plnShape.vtx[3]": "cylShape.vtx[9]",
                },
            }
        )

    def test_getEdgePath(self):
        """ """
        self.perform_test(
            {
                "self.get_edge_path('sph.e[12]', 'edgeLoop')": [
                    "sphShape.e[12]",
                    "sphShape.e[17]",
                    "sphShape.e[16]",
                    "sphShape.e[15]",
                    "sphShape.e[14]",
                    "sphShape.e[13]",
                ],
                "self.get_edge_path('sph.e[12]', 'edgeLoop', 'int')": [
                    12,
                    17,
                    16,
                    15,
                    14,
                    13,
                ],
                "self.get_edge_path('sph.e[12]', 'edgeRing')": [
                    "sphShape.e[0]",
                    "sphShape.e[6]",
                    "sphShape.e[12]",
                    "sphShape.e[18]",
                    "sphShape.e[24]",
                ],
                "self.get_edge_path(['sph.e[43]', 'sph.e[46]'], 'edgeRingPath')": [
                    "sphShape.e[43]",
                    "sphShape.e[42]",
                    "sphShape.e[47]",
                    "sphShape.e[46]",
                ],
                "self.get_edge_path(['sph.e[54]', 'sph.e[60]'], 'edgeLoopPath')": [
                    "sphShape.e[60]",
                    "sphShape.e[48]",
                    "sphShape.e[42]",
                    "sphShape.e[36]",
                    "sphShape.e[30]",
                    "sphShape.e[54]",
                ],
            }
        )

    def test_get_normal(self):
        """ """
        face = pm.PyNode("cylShape.f[0]")
        expected_normal = om.MVector(0.7071068109888711, 0.0, -0.7071067513842227)
        result_normal = self.get_normal(face)
        self.assertAlmostEqual(result_normal.x, expected_normal.x)
        self.assertAlmostEqual(result_normal.y, expected_normal.y)
        self.assertAlmostEqual(result_normal.z, expected_normal.z)

    def test_get_normal_vector(self):
        """ """
        # Create a cube and get its first face
        cube = pm.polyCube()[0]
        face = cube.f[0]

        # Retrieve the normal directly from Maya for the first face of the cube
        expected_normal = pm.polyInfo(face, fn=True)[0].split()[2:5]
        expected_normal = [float(i) for i in expected_normal]

        # Get the normal using the get_normal_vector function
        result_normal = self.get_normal_vector(face)

        # Since result_normal is a dictionary, we retrieve the value using the face's ID as key
        self.assertAlmostEqual(result_normal[0][0], expected_normal[0])
        self.assertAlmostEqual(result_normal[0][1], expected_normal[1])
        self.assertAlmostEqual(result_normal[0][2], expected_normal[2])

    def test_get_normal_angle(self):
        """ """
        # Create a cube and get its first two edges
        cube = pm.polyCube()[0]
        edge1 = cube.e[0]
        edge2 = cube.e[1]

        # The angle between the normals of the faces connected by the first edge is 90 degrees
        # The angle between the normals of the faces connected by the second edge is also 90 degrees
        self.assertEqual(self.get_normal_angle(edge1), 90.0)
        self.assertEqual(self.get_normal_angle(edge2), 90.0)

    def test_average_normals(self):
        """ """
        # Create a cube
        cube = pm.polyCube()[0]

        # Average the normals of the cube
        self.average_normals([cube])

        # The average normal for a cube should be equal for each face
        normals = self.get_normal_vector(cube.f)

        # Expected normals for a cube
        expected_normals = {
            0: [0.0, 0.0, 1.0],
            1: [0.0, 1.0, 0.0],
            2: [0.0, 0.0, -1.0],
            3: [0.0, -1.0, 0.0],
            4: [1.0, 0.0, 0.0],
            5: [-1.0, 0.0, 0.0],
        }

        # Verify that the normals are approximately equal to the expected normals
        for key, normal in normals.items():
            self.assertAlmostEqual(normal[0], expected_normals[key][0])
            self.assertAlmostEqual(normal[1], expected_normals[key][1])
            self.assertAlmostEqual(normal[2], expected_normals[key][2])

    def test_get_edges_by_normal_angle(self):
        """ """
        self.assertEqual(
            self.get_edges_by_normal_angle("cyl", 50, 130),
            [pm.PyNode("cylShape.e[{}]".format(i)) for i in range(24)],
        )

    def test_set_edge_hardness(self):
        """Test the set_edge_hardness method"""
        # Create a cube
        cube = pm.polyCube()[0]

        # Apply different hardness to different edges of the cube
        self.set_edge_hardness([cube], 45, upper_hardness=60, lower_hardness=20)

        # ** temp disabled: polySoftEdge is throwing an error on edge angle query. **
        # The first edge of the cube should have a hardness of 60 (upper hardness)
        # self.assertEqual(pm.polySoftEdge(cube.e[0], q=True, angle=True), 60.0)

        # If we now set the threshold to a higher value and apply again
        self.set_edge_hardness([cube], 135, upper_hardness=80, lower_hardness=30)

        # The first edge of the cube should now have a hardness of 30 (lower hardness)
        # self.assertEqual(pm.polySoftEdge(cube.e[0], q=True, angle=True), 30.0)

    def test_get_faces_with_similar_normals(self):
        """ """
        # Create a cube and sphere
        cube = pm.polyCube()[0]
        sphere = pm.polySphere()[0]

        # Get faces of the cube and sphere
        cube_faces = cube.f[:]
        # sphere_faces = sphere.f[:]

        # For a cube and a sphere, there should be faces with similar normals.
        # Since we do not know which faces will have similar normals,
        # we cannot predict the exact number of similar faces.
        similar_faces = self.get_faces_with_similar_normals(
            cube_faces, transforms=[sphere], range_x=0.1, range_y=0.1, range_z=0.1
        )

        # So we just assert that there are some similar faces found
        self.assertTrue(len(similar_faces) > 0)

    def test_transfer_normals(self):
        """ """
        # Create two cubes
        source = pm.polyCube()[0]
        target = pm.polyCube()[0]

        # Get the vertices of the first face of the source cube
        vertices = pm.polyListComponentConversion(
            source.f[0], fromFace=True, toVertex=True
        )
        vertices = pm.ls(vertices, flatten=True)

        # Change the normal of the vertices
        for vertex in vertices:
            pm.polyNormalPerVertex(vertex, normalXYZ=[1.0, 0.0, 0.0])

        # Transfer normals from source to target
        self.transfer_normals(source, target)

        # The normal for the first face of the target should now match the source
        source_normal = self.get_normal_vector(source.f[0])[0]
        target_normal = self.get_normal_vector(target.f[0])[0]

        self.assertAlmostEqual(source_normal[0], target_normal[0])
        self.assertAlmostEqual(source_normal[1], target_normal[1])
        self.assertAlmostEqual(source_normal[2], target_normal[2])

    def test_getComponentsByNumberOfConnected(self):
        """ """
        self.perform_test(
            {
                "self.filter_components_by_connection_count(['sph.f[18:23]', 'sph.f[30:35]'], 3, 'e')": [
                    "sphShape.f[30]",
                    "sphShape.f[31]",
                    "sphShape.f[32]",
                    "sphShape.f[33]",
                    "sphShape.f[34]",
                    "sphShape.f[35]",
                ],
                "self.filter_components_by_connection_count('pln.vtx[:]', (0,2), 'e')": [
                    "plnShape.vtx[0]",
                    "plnShape.vtx[3]",
                    "plnShape.vtx[12]",
                    "plnShape.vtx[15]",
                ],
            }
        )

    def test_getVertexNormal(self):
        """ """
        import maya.api.OpenMaya as om

        self.perform_test(
            {
                "self.get_vertex_normal('pln.vtx[2]', angle_weighted=False)": om.MVector(
                    0, 1, 0
                ),
            }
        )

    def test_getVectorFromComponents(self):
        """ """
        self.perform_test(
            {
                "self.get_vector_from_components('pln.f[:]')": (0.0, 1.0, 0.0),
                "self.get_vector_from_components(['cyl.f[7]', 'cyl.f[8]'])": (
                    0.0,
                    0.0,
                    0.43982641180356435,
                ),
            }
        )

    def test_orient_shells(self):
        """ """
        # Create a plane with a UV map
        plane = pm.polyPlane()[0]
        pm.polyUVSet(plane, create=True, uvSet="map1")

        # Orient the UV shells of the plane
        self.orient_shells([plane])

        # Check if UV shells are oriented as expected
        uv_shells = pm.polyUVSet(plane, query=True, allUVSets=True)
        self.assertTrue(uv_shells)

    def test_move_to_uv_space(self):
        """Test the move_to_uv_space method."""
        # Create a plane
        plane = pm.polyPlane()[0]

        # Move the UVs of the plane
        self.move_to_uv_space([plane], 1, 1, relative=False)

        # Get the UVs of the plane
        uvs = pm.polyListComponentConversion(plane.f, fromFace=True, toUV=True)
        uvs = pm.ls(uvs, flatten=True)

        for uv in uvs:
            # Query the UV coordinate
            u, v = pm.polyEditUV(uv, query=True)

            # Check that the UV coordinate is approximately equal to [1, 1]
            self.assertAlmostEqual(u, 1, places=5)
            self.assertAlmostEqual(v, 1, places=5)

    def test_get_uv_shell_sets(self):
        """ """
        # Create a plane with a UV map
        plane = pm.polyPlane()[0]
        pm.polyUVSet(plane, create=True, uvSet="map1")

        # Get the UV shell sets of the plane
        uv_shell_sets = self.get_uv_shell_sets([plane])

        # Check if the UV shell sets are as expected
        self.assertTrue(uv_shell_sets)  # Should not be empty

    def test_get_uv_shell_border_edges(self):
        """ """
        # Create a plane with a UV map
        plane = pm.polyPlane()[0]
        pm.polyUVSet(plane, create=True, uvSet="map1")

        # Get the UV shell border edges of the plane
        uv_shell_border_edges = self.get_uv_shell_border_edges([plane])

        # Check if the UV shell border edges are as expected
        self.assertTrue(uv_shell_border_edges)  # Should not be empty

    def test_transfer_uvs(self):
        """ """
        # Create two planes
        source = pm.polyPlane()[0]
        target = pm.polyPlane()[0]

        # Set a UV for the first vertex of the first face of the source plane
        source_uv_set = "map1"
        target_uv_set = "map2"
        pm.polyUVSet(source, create=True, uvSet=source_uv_set)
        pm.polyUVSet(target, create=True, uvSet=target_uv_set)

        # Get the UVs of the first face of the source plane
        source_uvs = pm.polyListComponentConversion(
            source.f[0], fromFace=True, toUV=True
        )
        source_uvs = pm.ls(source_uvs, flatten=True)

        # Edit the first UV of the source plane
        pm.polyEditUV(source_uvs[0], u=0.5, v=0.5)

        # Transfer UVs from source to target
        self.transfer_uvs(source, target)

        # Get the UVs of the first face of the target plane
        target_uvs = pm.polyListComponentConversion(
            target.f[0], fromFace=True, toUV=True
        )
        target_uvs = pm.ls(target_uvs, flatten=True)

        # The UV for the first vertex of the first face of the target should now match the source
        source_u, source_v = pm.polyEditUV(source_uvs[0], query=True)
        target_u, target_v = pm.polyEditUV(target_uvs[0], query=True)

        self.assertAlmostEqual(source_u, target_u)
        self.assertAlmostEqual(source_v, target_v)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(exit=False)


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------

# """
# def test_(self):
#   '''
#   '''
#   self.perform_test({
#       # "self.": '',
#   })


# def test_(self):
#   '''
#   '''
#   self.perform_test({
#       # "self.": '',
#   })


# def test_(self):
#   '''
#   '''
#   self.perform_test({
#       # "self.": '',
#   })


# def test_(self):
#   '''
#   '''
#   self.perform_test({
#       # "self.": '',
#   })
# """

# Deprecated ---------------------
