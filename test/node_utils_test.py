# !/usr/bin/python
# coding=utf-8
import os
import unittest
import inspect
import pymel.core as pm
from mayatk import NodeUtils


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


class NodeUtils_test(Main, NodeUtils):
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

    def test_getType(self):
        """ """
        self.assertEqual(self.get_type("cyl"), "transform")
        self.assertEqual(self.get_type("cylShape"), "mesh")
        self.assertEqual(self.get_type("cylShape.vtx[0]"), "vtx")
        self.assertEqual(self.get_type("cylShape.e[0]"), "e")
        self.assertEqual(self.get_type("cylShape.f[0]"), "f")

    def test_getTransformNode(self):
        """ """
        self.assertEqual(self.get_transform_node("cyl"), "cyl")
        self.assertEqual(self.get_transform_node("cylShape"), "cyl")

    def test_getShapeNode(self):
        """ """
        self.assertEqual(self.get_shape_node("cyl"), "cylShape")
        self.assertEqual(self.get_shape_node("cylShape"), "cylShape")

    def test_getHistoryNode(self):
        """ """
        self.assertEqual(self.get_history_node("cyl"), "polyCylinder1")
        self.assertEqual(self.get_history_node("cylShape"), "polyCylinder1")

    def test_isLocator(self):
        """ """
        if not pm.objExists("loc"):
            loc = pm.spaceLocator(name="loc")

        self.assertEqual(self.is_locator("cyl"), False)
        self.assertEqual(self.is_locator(loc), True)

    def test_isGroup(self):
        # Single node tests
        pm.polyCube(name="cube")
        pm.polySphere(name="sphere")
        self.assertEqual(self.is_group("cube"), False)  # Single cube
        self.assertEqual(self.is_group("cubeShape"), False)  # Shape node
        self.assertEqual(self.is_group("sphereShape.vtx[0]"), False)  # Single vertex

        # Group tests
        pm.group("cube", "sphere", name="group1")
        self.assertEqual(self.is_group("group1"), True)  # Group with multiple children

        # Nested group tests
        pm.polyCone(name="cone")
        pm.group("group1", "cone", name="group2")
        self.assertEqual(
            self.is_group("group2"), True
        )  # Group containing another group

        # Empty group tests
        pm.group(name="emptyGroup")
        self.assertEqual(self.is_group("emptyGroup"), True)  # Empty group

        # Cleanup
        pm.delete("group2", "emptyGroup")

    def test_getGroups(self):
        """ """
        self.assertEqual(self.get_groups(), [])

    def test_getParent(self):
        """ """
        self.assertEqual(self.get_parent("cyl"), None)

    def test_getChildren(self):
        """ """
        self.assertEqual(self.get_children("cyl"), [])

    def test_getNodeAttributes(self):
        """ """
        # self.assertEqual(self.get_node_attributes(), None)

    def test_setNodeAttributes(self):
        """ """
        # self.assertEqual(self.set_node_attributes(), None)

    def test_connectAttributes(self):
        """ """
        # self.assertEqual(self.connect_attributes(), None)

    def test_createRenderNode(self):
        """ """
        # self.assertEqual(self.create_render_node(), None)

    def test_get_connected_nodes(self):
        """ """
        # self.get_incoming_node_by_type(), None)

    def test_connectMultiAttr(self):
        """ """
        # self.assertEqual(self.connect_multi_attr(), None)

    def test_nodeExists(self):
        """ """
        # self.assertEqual(self.node_exists(), None)

    def test_createAssembly(self):
        """ """
        # self.assertEqual(self.create_assembly(), None)


# -----------------------------------------------------------------------------


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
#       "self.()": None,
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
