# !/usr/bin/python
# coding=utf-8
import unittest
import pymel.core as pm
import mayatk as mtk


class NodeUtilsTest(unittest.TestCase):
    def setUp(self):
        """Set up test scene for each test."""
        pm.mel.file(new=True, force=True)
        self.cyl = (
            pm.polyCylinder(
                radius=5,
                height=10,
                subdivisionsX=12,
                subdivisionsY=1,
                subdivisionsZ=1,
                name="cyl",
            )[0]
            if not pm.objExists("cyl")
            else pm.PyNode("cyl")
        )

    def test_get_type(self):
        tests = {
            "cyl": "transform",
            "cylShape": "mesh",
            "cylShape.vtx[0]": "vtx",
            "cylShape.e[0]": "e",
            "cylShape.f[0]": "f",
        }
        for input, expected in tests.items():
            with self.subTest(input=input):
                self.assertEqual(mtk.get_type(input), expected)

    def test_get_transform_node(self):
        tests = {
            "cyl": "cyl",
            "cylShape": "cyl",
        }
        for input, expected in tests.items():
            with self.subTest(input=input):
                self.assertEqual(mtk.get_transform_node(input), expected)

    def test_get_shape_node(self):
        tests = {
            "cyl": "cylShape",
            "cylShape": "cylShape",
        }
        for input, expected in tests.items():
            with self.subTest(input=input):
                self.assertEqual(mtk.get_shape_node(input), expected)

    def test_get_history_node(self):
        tests = {
            "cyl": "polyCylinder1",
            "cylShape": "polyCylinder1",
        }
        for input, expected in tests.items():
            with self.subTest(input=input):
                self.assertEqual(mtk.get_history_node(input), expected)

    def test_is_locator(self):
        loc = (
            pm.spaceLocator(name="loc") if not pm.objExists("loc") else pm.PyNode("loc")
        )
        tests = {
            "cyl": False,
            loc: True,
        }
        for input, expected in tests.items():
            with self.subTest(input=input):
                self.assertEqual(mtk.is_locator(input), expected)

    def test_is_group(self):
        pm.polyCube(name="cube")
        pm.polySphere(name="sphere")
        pm.group("cube", "sphere", name="group1")
        pm.polyCone(name="cone")
        pm.group("group1", "cone", name="group2")
        pm.group(name="emptyGroup")

        tests = {
            "cube": False,
            "cubeShape": False,
            "sphereShape.vtx[0]": False,
            "group1": True,
            "group2": True,
            "emptyGroup": True,
        }
        for input, expected in tests.items():
            with self.subTest(input=input):
                self.assertEqual(mtk.is_group(input), expected)

        # Cleanup
        pm.delete("group2", "emptyGroup")

    def test_get_unique_children(self):
        pm.polyCube(name="cube")
        pm.polySphere(name="sphere")
        pm.group("cube", "sphere", name="group1")
        pm.polyCone(name="cone")
        pm.group("group1", "cone", name="group2")
        pm.group(name="emptyGroup")

        expected_children = ["cube", "sphere", "cone"]
        result = mtk.get_unique_children("group2")

        assert sorted([str(child) for child in result]) == sorted(
            expected_children
        ), "Test failed: Incorrect children list"

        # Cleanup
        pm.delete("group2", "emptyGroup")

    def test_get_groups(self):
        self.assertEqual(mtk.get_groups(), [])

    def test_get_parent(self):
        self.assertEqual(mtk.get_parent("cyl"), None)

    def test_get_children(self):
        self.assertEqual(mtk.get_children("cyl"), [])

    def test_get_node_attributes(self):
        pass  # self.assertEqual(mtk.get_node_attributes(), None)

    def test_set_node_attributes(self):
        pass  # self.assertEqual(mtk.set_node_attributes(), None)

    def test_connect_attributes(self):
        pass  # self.assertEqual(mtk.connect_attributes(), None)

    def test_create_render_node(self):
        pass  # self.assertEqual(mtk.create_render_node(), None)

    def test_get_connected_nodes(self):
        pass  # self.get_incoming_node_by_type(), None)

    def test_connect_multi_attr(self):
        pass  # self.assertEqual(mtk.connect_multi_attr(), None)

    def test_create_assembly(self):
        pass  # self.assertEqual(mtk.create_assembly(), None)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import importlib

    importlib.reload(mtk.node_utils)
    mtk.clear_scroll_field_reporters()

    # Create a Test Suite
    suite = unittest.TestSuite()

    # Add the test case class to the suite
    suite.addTest(unittest.makeSuite(NodeUtilsTest))

    # Run the suite
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
