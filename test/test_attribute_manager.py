# !/usr/bin/python
# coding=utf-8
"""
Test Suite for AttributeManagerController

Tests for the stateless controller backing the Attribute Manager UI.
Covers filtering, formatting, parsing, mutation helpers, and node
traversal methods that operate via ``maya.cmds``.
"""
import unittest
import maya.cmds as cmds

from base_test import MayaTkTestCase, skipUnlessExtended
from mayatk.node_utils.attributes.attribute_manager import AttributeManagerController


class TestFormatValue(MayaTkTestCase):
    """Tests for AttributeManagerController.format_value."""

    def test_none_returns_empty(self):
        self.assertEqual(AttributeManagerController.format_value(None), "")

    def test_star_passthrough(self):
        self.assertEqual(AttributeManagerController.format_value("*"), "*")

    def test_float_precision(self):
        self.assertEqual(AttributeManagerController.format_value(1.23456789), "1.2346")

    def test_int_passthrough(self):
        self.assertEqual(AttributeManagerController.format_value(42), "42")

    def test_tuple_formatted(self):
        result = AttributeManagerController.format_value((1.0, 2.0, 3.0))
        self.assertEqual(result, "(1.0000, 2.0000, 3.0000)")

    def test_string_passthrough(self):
        self.assertEqual(AttributeManagerController.format_value("hello"), "hello")

    def test_bool_passthrough(self):
        self.assertEqual(AttributeManagerController.format_value(True), "True")


class TestParseValue(MayaTkTestCase):
    """Tests for AttributeManagerController.parse_value."""

    def test_double(self):
        self.assertAlmostEqual(
            AttributeManagerController.parse_value("3.14", "double"), 3.14
        )

    def test_float_type(self):
        self.assertAlmostEqual(
            AttributeManagerController.parse_value("2.5", "float"), 2.5
        )

    def test_long_truncates(self):
        self.assertEqual(AttributeManagerController.parse_value("7.9", "long"), 7)

    def test_bool_true_variants(self):
        for text in ("1", "true", "True", "yes", "on"):
            self.assertTrue(
                AttributeManagerController.parse_value(text, "bool"),
                f"Expected True for '{text}'",
            )

    def test_bool_false(self):
        self.assertFalse(AttributeManagerController.parse_value("0", "bool"))

    def test_string_passthrough(self):
        self.assertEqual(AttributeManagerController.parse_value("abc", "string"), "abc")

    def test_enum_int(self):
        self.assertEqual(AttributeManagerController.parse_value("2", "enum"), 2)

    def test_enum_label_fallback(self):
        self.assertEqual(AttributeManagerController.parse_value("Red", "enum"), "Red")

    def test_unsupported_returns_none(self):
        self.assertIsNone(AttributeManagerController.parse_value("x", "compound"))


class TestSortChannelBox(MayaTkTestCase):
    """Tests for AttributeManagerController._sort_channel_box."""

    def test_canonical_order(self):
        attrs = ["scaleX", "translateX", "rotateX", "visibility"]
        result = AttributeManagerController._sort_channel_box(attrs)
        self.assertEqual(result, ["translateX", "rotateX", "scaleX", "visibility"])

    def test_custom_attrs_appended_sorted(self):
        attrs = ["zzz_custom", "aaa_custom", "translateY"]
        result = AttributeManagerController._sort_channel_box(attrs)
        self.assertEqual(result, ["translateY", "aaa_custom", "zzz_custom"])

    def test_empty_list(self):
        self.assertEqual(AttributeManagerController._sort_channel_box([]), [])


class TestGetFilterKwargs(MayaTkTestCase):
    """Tests for AttributeManagerController.get_filter_kwargs."""

    def test_custom_filter(self):
        kw = AttributeManagerController.get_filter_kwargs("Custom")
        self.assertIn("userDefined", kw)

    def test_keyable_filter(self):
        kw = AttributeManagerController.get_filter_kwargs("Keyable")
        self.assertIn("keyable", kw)

    def test_all_filter(self):
        kw = AttributeManagerController.get_filter_kwargs("All")
        self.assertEqual(kw, {})

    def test_unknown_filter_defaults_custom(self):
        kw = AttributeManagerController.get_filter_kwargs("NonExistent")
        self.assertIn("userDefined", kw)


class TestToggleLock(MayaTkTestCase):
    """Tests for toggle_lock / set_lock."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="lock_cube")[0]

    def test_toggle_lock_on_and_off(self):
        """Toggle lock should flip the lock state."""
        self.assertFalse(cmds.getAttr(f"{self.cube}.tx", lock=True))
        AttributeManagerController.toggle_lock([self.cube], "translateX")
        self.assertTrue(cmds.getAttr(f"{self.cube}.tx", lock=True))
        AttributeManagerController.toggle_lock([self.cube], "translateX")
        self.assertFalse(cmds.getAttr(f"{self.cube}.tx", lock=True))

    def test_set_lock_explicit(self):
        AttributeManagerController.set_lock([self.cube], ["translateX"], True)
        self.assertTrue(cmds.getAttr(f"{self.cube}.tx", lock=True))
        AttributeManagerController.set_lock([self.cube], ["translateX"], False)
        self.assertFalse(cmds.getAttr(f"{self.cube}.tx", lock=True))


class TestBreakConnections(MayaTkTestCase):
    """Tests for break_connections."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="brk_cube")[0]
        self.sphere = cmds.polySphere(name="brk_sphere")[0]
        cmds.connectAttr(f"{self.sphere}.tx", f"{self.cube}.tx", force=True)

    def test_break_connected(self):
        self.assertTrue(
            cmds.listConnections(f"{self.cube}.tx", source=True, destination=False)
        )
        result = AttributeManagerController.break_connections([self.cube], "translateX")
        self.assertTrue(result)
        self.assertFalse(
            cmds.listConnections(f"{self.cube}.tx", source=True, destination=False)
            or []
        )

    def test_break_unconnected_returns_false(self):
        result = AttributeManagerController.break_connections([self.cube], "translateY")
        self.assertFalse(result)


class TestClassifyConnection(MayaTkTestCase):
    """Tests for classify_connection."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="cls_cube")[0]

    def test_none_connection(self):
        result = AttributeManagerController.classify_connection(self.cube, "translateX")
        self.assertEqual(result, "none")

    def test_keyframe_connection(self):
        cmds.setKeyframe(self.cube, attribute="translateX", time=1, value=0)
        cmds.setKeyframe(self.cube, attribute="translateX", time=10, value=5)
        result = AttributeManagerController.classify_connection(self.cube, "translateX")
        self.assertEqual(result, "keyframe")

    def test_expression_connection(self):
        cmds.expression(string=f"{self.cube}.translateX = frame;", object=self.cube)
        result = AttributeManagerController.classify_connection(self.cube, "translateX")
        self.assertEqual(result, "expression")


class TestBuildTableData(MayaTkTestCase):
    """Tests for build_table_data."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="tbl_cube")[0]

    def test_custom_filter_returns_empty_for_cube(self):
        """A fresh cube has no user-defined attrs."""
        rows, states = AttributeManagerController.build_table_data(
            [self.cube], {"userDefined": True}
        )
        # Either empty or the placeholder row
        if rows and rows[0][0]:
            self.fail("Fresh cube should have no custom attrs")

    def test_keyable_filter_returns_standard_attrs(self):
        rows, states = AttributeManagerController.build_table_data(
            [self.cube], {"keyable": True}
        )
        names = [r[0] for r in rows]
        self.assertIn("translateX", names)
        self.assertIn("visibility", names)
        self.assertEqual(len(rows), len(states))

    def test_multi_node_mixed_value(self):
        """Multiple nodes with different values show '*'."""
        cube2 = cmds.polyCube(name="tbl_cube2")[0]
        cmds.setAttr(f"{cube2}.translateX", 99)
        rows, _ = AttributeManagerController.build_table_data(
            [self.cube, cube2], {"keyable": True}
        )
        tx_row = [r for r in rows if r[0] == "translateX"][0]
        self.assertEqual(tx_row[3], "*")


class TestResetToDefault(MayaTkTestCase):
    """Tests for reset_to_default."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="rst_cube")[0]

    def test_reset_translation(self):
        cmds.setAttr(f"{self.cube}.translateX", 42)
        AttributeManagerController.reset_to_default([self.cube], ["translateX"])
        self.assertAlmostEqual(cmds.getAttr(f"{self.cube}.translateX"), 0.0, places=4)


class TestCreateAttribute(MayaTkTestCase):
    """Tests for create_attribute."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="crt_cube")[0]

    def test_create_float_attr(self):
        AttributeManagerController.create_attribute(
            [self.cube], "myFloat", "float", default_val=1.5
        )
        self.assertTrue(cmds.attributeQuery("myFloat", node=self.cube, exists=True))
        self.assertAlmostEqual(cmds.getAttr(f"{self.cube}.myFloat"), 1.5, places=4)

    def test_create_enum_attr(self):
        AttributeManagerController.create_attribute(
            [self.cube], "myEnum", "enum", enum_names="Red:Green:Blue"
        )
        self.assertTrue(cmds.attributeQuery("myEnum", node=self.cube, exists=True))

    def test_create_bool_attr(self):
        AttributeManagerController.create_attribute([self.cube], "myBool", "bool")
        self.assertTrue(cmds.attributeQuery("myBool", node=self.cube, exists=True))

    def test_create_double3_attr(self):
        AttributeManagerController.create_attribute([self.cube], "myVec", "double3")
        self.assertTrue(cmds.attributeQuery("myVec", node=self.cube, exists=True))
        self.assertTrue(cmds.attributeQuery("myVecX", node=self.cube, exists=True))

    def test_duplicate_attr_warns_not_errors(self):
        """Creating an attr that already exists should warn, not raise."""
        AttributeManagerController.create_attribute([self.cube], "myFloat", "float")
        # Second call should not raise
        AttributeManagerController.create_attribute([self.cube], "myFloat", "float")


class TestRenameAttribute(MayaTkTestCase):
    """Tests for rename_attribute."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="ren_cube")[0]
        cmds.addAttr(self.cube, longName="oldName", attributeType="float", keyable=True)

    def test_rename_success(self):
        result = AttributeManagerController.rename_attribute(
            [self.cube], "oldName", "newName"
        )
        self.assertTrue(result)
        self.assertTrue(cmds.attributeQuery("newName", node=self.cube, exists=True))
        self.assertFalse(cmds.attributeQuery("oldName", node=self.cube, exists=True))

    def test_rename_same_returns_false(self):
        result = AttributeManagerController.rename_attribute(
            [self.cube], "oldName", "oldName"
        )
        self.assertFalse(result)

    def test_rename_empty_returns_false(self):
        result = AttributeManagerController.rename_attribute([self.cube], "oldName", "")
        self.assertFalse(result)


class TestDeleteAttributes(MayaTkTestCase):
    """Tests for delete_attributes."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="del_cube")[0]
        cmds.addAttr(
            self.cube, longName="toDelete", attributeType="float", keyable=True
        )

    def test_delete_custom_attr(self):
        self.assertTrue(cmds.attributeQuery("toDelete", node=self.cube, exists=True))
        AttributeManagerController.delete_attributes([self.cube], ["toDelete"])
        self.assertFalse(cmds.attributeQuery("toDelete", node=self.cube, exists=True))


class TestGetShapeNodes(MayaTkTestCase):
    """Tests for get_shape_nodes."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="shp_cube")[0]

    def test_returns_shape(self):
        shapes = AttributeManagerController.get_shape_nodes([self.cube])
        self.assertTrue(len(shapes) >= 1)
        self.assertTrue(cmds.nodeType(shapes[0]) == "mesh")

    def test_empty_for_group(self):
        grp = cmds.group(empty=True, name="empty_grp")
        shapes = AttributeManagerController.get_shape_nodes([grp])
        self.assertEqual(shapes, [])


class TestGetHistoryNodes(MayaTkTestCase):
    """Tests for get_history_nodes."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="hist_cube")[0]

    def test_returns_polycube_node(self):
        history = AttributeManagerController.get_history_nodes([self.cube])
        self.assertTrue(len(history) >= 1)
        types = [cmds.nodeType(h) for h in history]
        self.assertIn("polyCube", types)


class TestSetBreakdownKey(MayaTkTestCase):
    """Tests for set_breakdown_key."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="bkd_cube")[0]

    def test_creates_breakdown_key(self):
        """A breakdown key is a keyframe with breakdown=True."""
        AttributeManagerController.set_breakdown_key([self.cube], ["translateX"])
        keys = cmds.keyframe(f"{self.cube}.translateX", query=True, timeChange=True)
        self.assertTrue(keys, "Expected at least one keyframe")
        bd = cmds.keyframe(f"{self.cube}.translateX", query=True, breakdown=True)
        self.assertTrue(bd, "Expected breakdown flag on the key")


class TestMuteUnmute(MayaTkTestCase):
    """Tests for mute_attrs / unmute_attrs."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="mut_cube")[0]
        # Mute requires an anim curve
        cmds.setKeyframe(self.cube, attribute="translateX", time=1, value=0)

    def test_mute_and_unmute(self):
        AttributeManagerController.mute_attrs([self.cube], ["translateX"])
        self.assertTrue(cmds.mute(f"{self.cube}.translateX", q=True))
        AttributeManagerController.unmute_attrs([self.cube], ["translateX"])
        self.assertFalse(cmds.mute(f"{self.cube}.translateX", q=True))


class TestToggleKeyable(MayaTkTestCase):
    """Tests for toggle_keyable."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="key_cube")[0]

    def test_toggle_off_and_on(self):
        self.assertTrue(cmds.getAttr(f"{self.cube}.translateX", keyable=True))
        AttributeManagerController.toggle_keyable([self.cube], ["translateX"])
        self.assertFalse(cmds.getAttr(f"{self.cube}.translateX", keyable=True))
        AttributeManagerController.toggle_keyable([self.cube], ["translateX"])
        self.assertTrue(cmds.getAttr(f"{self.cube}.translateX", keyable=True))


class TestSelectConnections(MayaTkTestCase):
    """Tests for select_connections."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="sel_cube")[0]
        self.sphere = cmds.polySphere(name="sel_sphere")[0]
        cmds.connectAttr(f"{self.sphere}.tx", f"{self.cube}.tx", force=True)

    def test_selects_upstream(self):
        result = AttributeManagerController.select_connections(
            [self.cube], "translateX"
        )
        self.assertTrue(result)
        sel = cmds.ls(sl=True)
        self.assertIn(self.sphere, sel)

    def test_no_connection_returns_false(self):
        result = AttributeManagerController.select_connections(
            [self.cube], "translateY"
        )
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
