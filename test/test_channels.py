# !/usr/bin/python
# coding=utf-8
"""
Test Suite for Channels

Tests for the stateless controller backing the Channels UI.
Covers filtering, formatting, parsing, mutation helpers, and node
traversal methods that operate via ``maya.cmds``.
"""
import unittest
import maya.cmds as cmds

from base_test import MayaTkTestCase, skipUnlessExtended
from mayatk.node_utils.attributes.channels import Channels


class TestFormatValue(MayaTkTestCase):
    """Tests for Channels.format_value."""

    def test_none_returns_empty(self):
        self.assertEqual(Channels.format_value(None), "")

    def test_star_passthrough(self):
        self.assertEqual(Channels.format_value("*"), "*")

    def test_float_precision(self):
        self.assertEqual(Channels.format_value(1.23456789), "1.2346")

    def test_int_passthrough(self):
        self.assertEqual(Channels.format_value(42), "42")

    def test_tuple_formatted(self):
        # Trailing zeros and trailing decimal points are stripped.
        result = Channels.format_value((1.0, 2.0, 3.0))
        self.assertEqual(result, "(1, 2, 3)")

    def test_float_strips_trailing_zeros(self):
        self.assertEqual(Channels.format_value(1.0), "1")
        self.assertEqual(Channels.format_value(1.5), "1.5")
        self.assertEqual(Channels.format_value(0.0), "0")
        self.assertEqual(Channels.format_value(0.0001), "0.0001")

    def test_string_passthrough(self):
        self.assertEqual(Channels.format_value("hello"), "hello")

    def test_bool_passthrough(self):
        self.assertEqual(Channels.format_value(True), "True")


class TestParseValue(MayaTkTestCase):
    """Tests for Channels.parse_value."""

    def test_double(self):
        self.assertAlmostEqual(
            Channels.parse_value("3.14", "double"), 3.14
        )

    def test_float_type(self):
        self.assertAlmostEqual(
            Channels.parse_value("2.5", "float"), 2.5
        )

    def test_long_truncates(self):
        self.assertEqual(Channels.parse_value("7.9", "long"), 7)

    def test_bool_true_variants(self):
        for text in ("1", "true", "True", "yes", "on"):
            self.assertTrue(
                Channels.parse_value(text, "bool"),
                f"Expected True for '{text}'",
            )

    def test_bool_false(self):
        self.assertFalse(Channels.parse_value("0", "bool"))

    def test_string_passthrough(self):
        self.assertEqual(Channels.parse_value("abc", "string"), "abc")

    def test_enum_int(self):
        self.assertEqual(Channels.parse_value("2", "enum"), 2)

    def test_enum_label_fallback(self):
        self.assertEqual(Channels.parse_value("Red", "enum"), "Red")

    def test_unsupported_returns_none(self):
        self.assertIsNone(Channels.parse_value("x", "compound"))


class TestSortChannelBox(MayaTkTestCase):
    """Tests for Channels._sort_channel_box."""

    def test_canonical_order(self):
        attrs = ["scaleX", "translateX", "rotateX", "visibility"]
        result = Channels._sort_channel_box(attrs)
        self.assertEqual(result, ["translateX", "rotateX", "scaleX", "visibility"])

    def test_custom_attrs_appended_sorted(self):
        attrs = ["zzz_custom", "aaa_custom", "translateY"]
        result = Channels._sort_channel_box(attrs)
        self.assertEqual(result, ["translateY", "aaa_custom", "zzz_custom"])

    def test_empty_list(self):
        self.assertEqual(Channels._sort_channel_box([]), [])


class TestGetFilterKwargs(MayaTkTestCase):
    """Tests for Channels.get_filter_kwargs."""

    def test_custom_filter(self):
        kw = Channels.get_filter_kwargs("Custom")
        self.assertIn("userDefined", kw)

    def test_keyable_filter(self):
        kw = Channels.get_filter_kwargs("Keyable")
        self.assertIn("keyable", kw)

    def test_all_filter(self):
        kw = Channels.get_filter_kwargs("All")
        self.assertEqual(kw, {})

    def test_unknown_filter_defaults_custom(self):
        kw = Channels.get_filter_kwargs("NonExistent")
        self.assertIn("userDefined", kw)


class TestToggleLock(MayaTkTestCase):
    """Tests for toggle_lock / set_lock."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="lock_cube")[0]

    def test_toggle_lock_on_and_off(self):
        """Toggle lock should flip the lock state."""
        self.assertFalse(cmds.getAttr(f"{self.cube}.tx", lock=True))
        Channels.toggle_lock([self.cube], "translateX")
        self.assertTrue(cmds.getAttr(f"{self.cube}.tx", lock=True))
        Channels.toggle_lock([self.cube], "translateX")
        self.assertFalse(cmds.getAttr(f"{self.cube}.tx", lock=True))

    def test_set_lock_explicit(self):
        Channels.set_lock([self.cube], ["translateX"], True)
        self.assertTrue(cmds.getAttr(f"{self.cube}.tx", lock=True))
        Channels.set_lock([self.cube], ["translateX"], False)
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
        result = Channels.break_connections([self.cube], "translateX")
        self.assertTrue(result)
        self.assertFalse(
            cmds.listConnections(f"{self.cube}.tx", source=True, destination=False)
            or []
        )

    def test_break_unconnected_returns_false(self):
        result = Channels.break_connections([self.cube], "translateY")
        self.assertFalse(result)


class TestClassifyConnection(MayaTkTestCase):
    """Tests for classify_connection."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="cls_cube")[0]

    def test_none_connection(self):
        result = Channels.classify_connection(self.cube, "translateX")
        self.assertEqual(result, "none")

    def test_keyframe_connection(self):
        cmds.setKeyframe(str(self.cube), attribute="translateX", time=1, value=0)
        cmds.setKeyframe(str(self.cube), attribute="translateX", time=10, value=5)
        # Pin current time BETWEEN the keys — classify_connection promotes to
        # "keyframe_active" when a key sits on the current frame, and a fresh
        # scene's current time is environment-dependent (batch lands on 1).
        cmds.currentTime(5)
        result = Channels.classify_connection(self.cube, "translateX")
        self.assertEqual(result, "keyframe")

    def test_keyframe_active_connection(self):
        cmds.setKeyframe(str(self.cube), attribute="translateX", time=1, value=0)
        cmds.setKeyframe(str(self.cube), attribute="translateX", time=10, value=5)
        cmds.currentTime(10)
        result = Channels.classify_connection(self.cube, "translateX")
        self.assertEqual(result, "keyframe_active")

    def test_expression_connection(self):
        cmds.expression(string=f"{self.cube}.translateX = frame;", object=self.cube)
        result = Channels.classify_connection(self.cube, "translateX")
        self.assertEqual(result, "expression")


class TestBuildTableData(MayaTkTestCase):
    """Tests for build_table_data."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="tbl_cube")[0]

    def test_custom_filter_returns_empty_for_cube(self):
        """A fresh cube has no user-defined attrs."""
        rows, states = Channels.build_table_data(
            [self.cube], {"userDefined": True}
        )
        # Either empty or the placeholder row
        if rows and rows[0][0]:
            self.fail("Fresh cube should have no custom attrs")

    def test_keyable_filter_returns_standard_attrs(self):
        rows, states = Channels.build_table_data(
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
        rows, _ = Channels.build_table_data(
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
        Channels.reset_to_default([self.cube], ["translateX"])
        self.assertAlmostEqual(cmds.getAttr(f"{self.cube}.translateX"), 0.0, places=4)


class TestCreateAttribute(MayaTkTestCase):
    """Tests for create_attribute."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="crt_cube")[0]

    def test_create_float_attr(self):
        Channels.create_attribute(
            [self.cube], "myFloat", "float", default_val=1.5
        )
        self.assertTrue(cmds.attributeQuery("myFloat", node=self.cube, exists=True))
        self.assertAlmostEqual(cmds.getAttr(f"{self.cube}.myFloat"), 1.5, places=4)

    def test_create_enum_attr(self):
        Channels.create_attribute(
            [self.cube], "myEnum", "enum", enum_names="Red:Green:Blue"
        )
        self.assertTrue(cmds.attributeQuery("myEnum", node=self.cube, exists=True))

    def test_create_bool_attr(self):
        Channels.create_attribute([self.cube], "myBool", "bool")
        self.assertTrue(cmds.attributeQuery("myBool", node=self.cube, exists=True))

    def test_create_double3_attr(self):
        Channels.create_attribute([self.cube], "myVec", "double3")
        self.assertTrue(cmds.attributeQuery("myVec", node=self.cube, exists=True))
        self.assertTrue(cmds.attributeQuery("myVecX", node=self.cube, exists=True))

    def test_duplicate_attr_warns_not_errors(self):
        """Creating an attr that already exists should warn, not raise."""
        Channels.create_attribute([self.cube], "myFloat", "float")
        # Second call should not raise
        Channels.create_attribute([self.cube], "myFloat", "float")


class TestRenameAttribute(MayaTkTestCase):
    """Tests for rename_attribute."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="ren_cube")[0]
        cmds.addAttr(self.cube, longName="oldName", attributeType="float", keyable=True)

    def test_rename_success(self):
        result = Channels.rename_attribute(
            [self.cube], "oldName", "newName"
        )
        self.assertTrue(result)
        self.assertTrue(cmds.attributeQuery("newName", node=self.cube, exists=True))
        self.assertFalse(cmds.attributeQuery("oldName", node=self.cube, exists=True))

    def test_rename_same_returns_false(self):
        result = Channels.rename_attribute(
            [self.cube], "oldName", "oldName"
        )
        self.assertFalse(result)

    def test_rename_empty_returns_false(self):
        result = Channels.rename_attribute([self.cube], "oldName", "")
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
        Channels.delete_attributes([self.cube], ["toDelete"])
        self.assertFalse(cmds.attributeQuery("toDelete", node=self.cube, exists=True))


class TestGetShapeNodes(MayaTkTestCase):
    """Tests for get_shape_nodes."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="shp_cube")[0]

    def test_returns_shape(self):
        shapes = Channels.get_shape_nodes([self.cube])
        self.assertTrue(len(shapes) >= 1)
        self.assertTrue(cmds.nodeType(shapes[0]) == "mesh")

    def test_empty_for_group(self):
        grp = cmds.group(empty=True, name="empty_grp")
        shapes = Channels.get_shape_nodes([grp])
        self.assertEqual(shapes, [])

    def test_resolves_shape_from_shape_node(self):
        """Passing a shape node returns that same shape (idempotent).

        Required for the footer Shape button to be re-pressable after it
        has already swapped the selection to the shape node.
        """
        shape = cmds.listRelatives(self.cube, shapes=True, fullPath=True)[0]
        shapes = Channels.get_shape_nodes([shape])
        self.assertTrue(shapes)
        self.assertEqual(cmds.nodeType(shapes[0]), "mesh")

    def test_resolves_shape_from_history_node(self):
        """Regression: the Shape button after the History button.

        Once History has swapped the selection to a construction-history
        DG node, pressing Shape must still resolve the mesh — a plain
        ``listRelatives(shapes=True)`` returns nothing for a DG node and
        used to warn "No shape nodes found".
        """
        history = Channels.get_history_nodes([self.cube])
        self.assertTrue(history, "expected the cube to have construction history")
        shapes = Channels.get_shape_nodes(history)
        self.assertTrue(shapes, "Shape must resolve from a history node")
        self.assertEqual(cmds.nodeType(shapes[0]), "mesh")

    def test_shape_history_toggle_round_trip(self):
        """Toggling Shape<->History never loses the target mesh.

        transform -> history -> shape -> history -> shape must keep landing
        on the same shape / history nodes without re-selecting the cube.
        """
        expected_shape = cmds.listRelatives(self.cube, shapes=True, fullPath=True)[0]

        history = Channels.get_history_nodes([self.cube])
        self.assertTrue(history)

        shapes = Channels.get_shape_nodes(history)
        self.assertEqual(cmds.ls(shapes[0], long=True)[0], expected_shape)

        history2 = Channels.get_history_nodes(shapes)
        self.assertEqual(set(cmds.ls(history2)), set(cmds.ls(history)))

        shapes2 = Channels.get_shape_nodes(history2)
        self.assertEqual(cmds.ls(shapes2[0], long=True)[0], expected_shape)


class TestGetHistoryNodes(MayaTkTestCase):
    """Tests for get_history_nodes."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="hist_cube")[0]

    def test_returns_polycube_node(self):
        history = Channels.get_history_nodes([self.cube])
        self.assertTrue(len(history) >= 1)
        types = [cmds.nodeType(h) for h in history]
        self.assertIn("polyCube", types)

    def test_history_consistent_from_transform_and_shape(self):
        """Shape and transform inputs resolve to the *same* history node.

        Toggle-consistency guard: on a mesh with a construction stack,
        pressing History from the transform and from its shape must land
        on the same (top-of-stack) node so the footer Shape<->History
        toggle never jumps between history nodes. Delegating this to
        ``NodeUtils.get_history_node`` would break it — that helper returns
        the *bottom* of the stack from a shape input.
        """
        cmds.select(self.cube)
        cmds.polyExtrudeFacet(f"{self.cube}.f[0]", localTranslateZ=1.0)
        shape = cmds.listRelatives(self.cube, shapes=True, fullPath=True)[0]
        from_xform = Channels.get_history_nodes([self.cube])
        from_shape = Channels.get_history_nodes([shape])
        self.assertTrue(from_xform)
        self.assertEqual(set(cmds.ls(from_xform)), set(cmds.ls(from_shape)))


class TestSetBreakdownKey(MayaTkTestCase):
    """Tests for set_breakdown_key."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="bkd_cube")[0]

    def test_creates_breakdown_key(self):
        """A breakdown key is a keyframe with breakdown=True."""
        Channels.set_breakdown_key([self.cube], ["translateX"])
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
        cmds.setKeyframe(str(self.cube), attribute="translateX", time=1, value=0)

    def test_mute_and_unmute(self):
        Channels.mute_attrs([self.cube], ["translateX"])
        self.assertTrue(cmds.mute(f"{self.cube}.translateX", q=True))
        Channels.unmute_attrs([self.cube], ["translateX"])
        self.assertFalse(cmds.mute(f"{self.cube}.translateX", q=True))


class TestToggleKeyable(MayaTkTestCase):
    """Tests for toggle_keyable."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="key_cube")[0]

    def test_toggle_off_and_on(self):
        self.assertTrue(cmds.getAttr(f"{self.cube}.translateX", keyable=True))
        Channels.toggle_keyable([self.cube], ["translateX"])
        self.assertFalse(cmds.getAttr(f"{self.cube}.translateX", keyable=True))
        Channels.toggle_keyable([self.cube], ["translateX"])
        self.assertTrue(cmds.getAttr(f"{self.cube}.translateX", keyable=True))


class TestSelectConnections(MayaTkTestCase):
    """Tests for select_connections."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="sel_cube")[0]
        self.sphere = cmds.polySphere(name="sel_sphere")[0]
        cmds.connectAttr(f"{self.sphere}.tx", f"{self.cube}.tx", force=True)

    def test_selects_upstream(self):
        result = Channels.select_connections(
            [self.cube], "translateX"
        )
        self.assertTrue(result)
        sel = cmds.ls(sl=True)
        self.assertIn(self.sphere, sel)

    def test_no_connection_returns_false(self):
        result = Channels.select_connections(
            [self.cube], "translateY"
        )
        self.assertFalse(result)


class TestFreezeUnfreezeTransforms(MayaTkTestCase):
    """Tests for freeze_transforms / unfreeze_transforms / has_unfreeze_info."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="frz_cube")[0]
        cmds.xform(self.cube, translation=(1.0, 2.0, 3.0), rotation=(10.0, 20.0, 30.0))

    def test_has_unfreeze_info_false_initially(self):
        self.assertFalse(Channels.has_unfreeze_info([self.cube]))

    def test_has_unfreeze_info_empty_nodes(self):
        self.assertFalse(Channels.has_unfreeze_info([]))
        self.assertFalse(Channels.has_unfreeze_info(None))

    def test_freeze_zeros_transforms_and_stores(self):
        Channels.freeze_transforms([self.cube])

        # Translation and rotation should be zeroed.
        self.assertAlmostEqual(cmds.getAttr(f"{self.cube}.translateX"), 0.0)
        self.assertAlmostEqual(cmds.getAttr(f"{self.cube}.rotateX"), 0.0)
        # And stored info now exists for unfreeze.
        self.assertTrue(Channels.has_unfreeze_info([self.cube]))

    def test_unfreeze_restores_world_position(self):
        ws_before = cmds.xform(self.cube, q=True, ws=True, t=True)
        Channels.freeze_transforms([self.cube])
        restored = Channels.unfreeze_transforms([self.cube])

        self.assertTrue(restored, "Expected at least one node to be restored")
        ws_after = cmds.xform(self.cube, q=True, ws=True, t=True)
        for a, b in zip(ws_before, ws_after):
            self.assertAlmostEqual(a, b, places=4)

    def test_unfreeze_without_stored_info_returns_empty(self):
        restored = Channels.unfreeze_transforms([self.cube])
        self.assertFalse(restored)

    def test_freeze_without_store_leaves_no_unfreeze_info(self):
        Channels.freeze_transforms([self.cube], store=False)
        self.assertFalse(Channels.has_unfreeze_info([self.cube]))

    # -- can_freeze_selection ---------------------------------------------

    def test_can_freeze_empty_selection(self):
        self.assertTrue(Channels.can_freeze_selection([]))
        self.assertTrue(Channels.can_freeze_selection(None))

    def test_can_freeze_full_translate_group_via_children(self):
        self.assertTrue(
            Channels.can_freeze_selection(
                ["translateX", "translateY", "translateZ"]
            )
        )

    def test_can_freeze_full_translate_group_via_parent(self):
        self.assertTrue(Channels.can_freeze_selection(["translate"]))

    def test_can_freeze_short_names(self):
        self.assertTrue(Channels.can_freeze_selection(["tx", "ty", "tz"]))

    def test_can_freeze_strips_plug_prefix(self):
        self.assertTrue(
            Channels.can_freeze_selection(
                [
                    f"{self.cube}.translateX",
                    f"{self.cube}.translateY",
                    f"{self.cube}.translateZ",
                ]
            )
        )

    def test_can_freeze_multiple_groups(self):
        self.assertTrue(
            Channels.can_freeze_selection(
                [
                    "translateX", "translateY", "translateZ",
                    "rotateX", "rotateY", "rotateZ",
                ]
            )
        )

    def test_cannot_freeze_partial_group(self):
        self.assertFalse(
            Channels.can_freeze_selection(["translateX", "translateY"])
        )

    def test_cannot_freeze_partial_group_single_axis(self):
        self.assertFalse(Channels.can_freeze_selection(["rotateX"]))

    def test_cannot_freeze_with_non_transform_attr(self):
        self.assertFalse(
            Channels.can_freeze_selection(
                ["translateX", "translateY", "translateZ", "visibility"]
            )
        )

    def test_cannot_freeze_only_non_transform(self):
        self.assertFalse(Channels.can_freeze_selection(["visibility"]))

    # -- freeze_transforms with attrs -------------------------------------

    def test_freeze_complete_translate_group_only(self):
        """Selecting all 3 translate axes freezes translate; rotate stays."""
        Channels.freeze_transforms(
            [self.cube], attrs=["translateX", "translateY", "translateZ"]
        )
        self.assertAlmostEqual(cmds.getAttr(f"{self.cube}.translateX"), 0.0)
        self.assertAlmostEqual(cmds.getAttr(f"{self.cube}.translateY"), 0.0)
        self.assertAlmostEqual(cmds.getAttr(f"{self.cube}.translateZ"), 0.0)
        # Rotate group untouched.
        self.assertAlmostEqual(cmds.getAttr(f"{self.cube}.rotateX"), 10.0)
        self.assertAlmostEqual(cmds.getAttr(f"{self.cube}.rotateY"), 20.0)
        self.assertAlmostEqual(cmds.getAttr(f"{self.cube}.rotateZ"), 30.0)

    def test_freeze_translate_via_parent_attr(self):
        """Selecting just the 'translate' parent is equivalent to all 3 axes."""
        Channels.freeze_transforms([self.cube], attrs=["translate"])
        self.assertAlmostEqual(cmds.getAttr(f"{self.cube}.translateX"), 0.0)
        self.assertAlmostEqual(cmds.getAttr(f"{self.cube}.translateY"), 0.0)
        self.assertAlmostEqual(cmds.getAttr(f"{self.cube}.translateZ"), 0.0)
        # Rotate group untouched.
        self.assertAlmostEqual(cmds.getAttr(f"{self.cube}.rotateX"), 10.0)

    def test_freeze_partial_group_returns_false(self):
        """A partial-group selection is rejected; no freeze occurs."""
        result = Channels.freeze_transforms(
            [self.cube], attrs=["translateX", "translateY"]
        )
        self.assertFalse(result)
        # Original values intact — including the unselected translateZ.
        self.assertAlmostEqual(cmds.getAttr(f"{self.cube}.translateX"), 1.0)
        self.assertAlmostEqual(cmds.getAttr(f"{self.cube}.translateY"), 2.0)

    def test_freeze_non_transform_attrs_returns_false(self):
        result = Channels.freeze_transforms(
            [self.cube], attrs=["visibility"]
        )
        self.assertFalse(result)
        self.assertAlmostEqual(cmds.getAttr(f"{self.cube}.translateX"), 1.0)

    # -- partial unfreeze -------------------------------------------------

    def test_unfreeze_translate_only_restores_translate_attribute(self):
        """Unfreezing only translate brings translate back to its pre-freeze
        value while leaving the (post-freeze) rotate value alone."""
        Channels.freeze_transforms([self.cube])  # freezes T+R+S, stores M
        # Add a rotation change after the freeze.
        cmds.xform(self.cube, rotation=(45.0, 0.0, 0.0), relative=False)

        restored = Channels.unfreeze_transforms(
            [self.cube], attrs=["translate"]
        )
        self.assertTrue(restored)

        # Translate restored to the pre-freeze value.
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.translateX"), 1.0, places=4
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.translateY"), 2.0, places=4
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.translateZ"), 3.0, places=4
        )
        # The post-freeze rotation change is preserved (not overwritten by
        # the stored pre-freeze rotation).
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.rotateX"), 45.0, places=4
        )

    def test_unfreeze_rotate_only_restores_rotation(self):
        """Restoring only rotate brings R back without touching T."""
        Channels.freeze_transforms([self.cube])
        # Move post-freeze; rotate should not pick up this move.
        cmds.xform(self.cube, translation=(99.0, 0.0, 0.0), relative=False)

        restored = Channels.unfreeze_transforms(
            [self.cube], attrs=["rotateX", "rotateY", "rotateZ"]
        )
        self.assertTrue(restored)

        # Rotation restored to pre-freeze values.
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.rotateX"), 10.0, places=4
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.rotateY"), 20.0, places=4
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.rotateZ"), 30.0, places=4
        )
        # Post-freeze translate change is preserved.
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.translateX"), 99.0, places=4
        )

    def test_unfreeze_scale_only_restores_scale(self):
        """Cumulative-S unfreeze composes the stored scale onto current.

        Scale composes multiplicatively: stored S=(2,3,4) baked at freeze
        time, current post-freeze S=(5,5,5) → unfrozen S=(10,15,20).
        Other channels are untouched (post-freeze rotate stays).
        """
        cmds.xform(self.cube, scale=(2.0, 3.0, 4.0))
        Channels.freeze_transforms([self.cube])  # stored.S_bake = (2,3,4)
        # Modify scale and rotate after the freeze.
        cmds.xform(self.cube, scale=(5.0, 5.0, 5.0))
        cmds.xform(self.cube, rotation=(45.0, 0.0, 0.0), relative=False)

        restored = Channels.unfreeze_transforms(
            [self.cube], attrs=["scale"]
        )
        self.assertTrue(restored)

        # Scale = stored * current (component-wise).
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.scaleX"), 10.0, places=4
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.scaleY"), 15.0, places=4
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.scaleZ"), 20.0, places=4
        )
        # Post-freeze rotation change preserved.
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.rotateX"), 45.0, places=4
        )

    # -- safety / regression tests ---------------------------------------

    def test_partial_unfreeze_preserves_shear(self):
        """Partial restore must not zero ``obj.shear`` — shear isn't a
        freezable channel, so ``cmds.xform(matrix=...)`` would silently
        wipe it without explicit preservation in the target matrix."""
        cmds.setAttr(f"{self.cube}.shearXY", 0.5)
        Channels.freeze_transforms([self.cube])
        # Modify shear after freeze; partial restore of rotate should
        # leave THIS value alone (not snap to the stored 0.5 nor to 0).
        cmds.setAttr(f"{self.cube}.shearXY", 0.8)

        Channels.unfreeze_transforms([self.cube], attrs=["rotate"])
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.shearXY"), 0.8, places=4
        )

    def test_sequential_partial_freezes_preserve_channel_originals(self):
        """Sequential partial freezes (T then R) must keep each channel's
        pre-freeze value so a later partial unfreeze restores correctly.

        Without merge-on-store, the second freeze would overwrite the
        stored matrix with the post-first-freeze state — destroying T's
        pre-freeze value of 1 before unfreeze ever runs.
        """
        # Setup gives us T=(1,2,3), R=(10,20,30).
        # Freeze translate first.
        Channels.freeze_transforms([self.cube], attrs=["translate"])
        # Modify R, then freeze rotate.
        cmds.xform(self.cube, rotation=(99.0, 0.0, 0.0), relative=False)
        Channels.freeze_transforms([self.cube], attrs=["rotate"])

        # Now unfreeze translate — should restore to the original
        # pre-translate-freeze value (1), not to 0 which is what the
        # local translate became after the first freeze.
        Channels.unfreeze_transforms([self.cube], attrs=["translate"])
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.translateX"), 1.0, places=4
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.translateY"), 2.0, places=4
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.translateZ"), 3.0, places=4
        )

        # And unfreeze rotate should restore to the value captured at
        # rotate-freeze time (99), not the original 10.
        Channels.unfreeze_transforms([self.cube], attrs=["rotate"])
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.rotateX"), 99.0, places=4
        )

    def test_refreezing_same_channel_accumulates_stored_value(self):
        """Re-freezing the SAME channel accumulates onto the bake history.

        Freeze T at 1 → bake history = T(1,2,3).  Move to 50, freeze T
        again → bake history accumulates to T(51,2,3).  Move to 99,99,99,
        unfreeze T → local T = stored * current = (51+99, 2+99, 3+99) =
        (150, 101, 102).  Each freeze adds to the bake history, no
        information is discarded.
        """
        Channels.freeze_transforms([self.cube], attrs=["translate"])
        # Move post-freeze and re-freeze the SAME channel.
        cmds.xform(self.cube, translation=(50.0, 0.0, 0.0), relative=False)
        Channels.freeze_transforms([self.cube], attrs=["translate"])

        cmds.xform(self.cube, translation=(99.0, 99.0, 99.0))
        Channels.unfreeze_transforms([self.cube], attrs=["translate"])

        # Bake history: (1,2,3) + (50,0,0) = (51,2,3); plus current
        # (99,99,99) → (150, 101, 102).
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.translateX"), 150.0, places=3
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.translateY"), 101.0, places=3
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.translateZ"), 102.0, places=3
        )

    def test_full_freeze_after_partial_overwrites_baseline(self):
        """A full freeze (no attrs) re-baselines everything, overwriting
        any merged partial-freeze state.

        The distinguishing assertion is the rotation: if the full freeze
        correctly overwrote, Unfreeze restores R to 45 (the value at
        time of the most recent freeze); if it incorrectly preserved
        the prior partial-freeze data, it would restore to the original
        10 from setUp.
        """
        Channels.freeze_transforms([self.cube], attrs=["translate"])
        cmds.xform(self.cube, rotation=(45.0, 0.0, 0.0), relative=False)
        # Full freeze should overwrite, not merge.
        Channels.freeze_transforms([self.cube])

        # Mess with the state, then unfreeze.
        cmds.xform(self.cube, translation=(7.0, 7.0, 7.0))
        cmds.xform(self.cube, rotation=(0.0, 0.0, 0.0), relative=False)
        Channels.unfreeze_transforms([self.cube])

        # R restored to the re-baselined 45 (not the original 10).
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.rotateX"), 45.0, places=4
        )

    def test_groups_from_attrs_helper(self):
        """``_groups_from_attrs`` exposes the group set used by both freeze
        and unfreeze paths."""
        self.assertEqual(Channels._groups_from_attrs([]), set())
        self.assertEqual(Channels._groups_from_attrs(None), set())
        self.assertEqual(
            Channels._groups_from_attrs(["translate"]), {"translate"}
        )
        self.assertEqual(
            Channels._groups_from_attrs(["translateX", "rotateY"]),
            {"translate", "rotate"},
        )
        # Plug prefix stripped.
        self.assertEqual(
            Channels._groups_from_attrs(["pCube1.scaleZ"]), {"scale"}
        )
        # Non-transform attrs are dropped (completeness check happens
        # separately in ``can_freeze_selection``).
        self.assertEqual(
            Channels._groups_from_attrs(["visibility"]), set()
        )

    def test_partial_unfreeze_keeps_stored_attrs_for_future_calls(self):
        """A partial unfreeze must not delete the stored data — the user
        may still want to unfreeze the remaining groups later."""
        Channels.freeze_transforms([self.cube])
        Channels.unfreeze_transforms([self.cube], attrs=["translate"])
        # Stored attrs survive a partial restore.
        self.assertTrue(Channels.has_unfreeze_info([self.cube]))

        # And a subsequent unfreeze of the remaining groups still works.
        restored = Channels.unfreeze_transforms(
            [self.cube], attrs=["rotate"]
        )
        self.assertTrue(restored)

    def test_full_unfreeze_deletes_stored_attrs(self):
        """A full restore (default) cleans the stored attrs up."""
        Channels.freeze_transforms([self.cube])
        Channels.unfreeze_transforms([self.cube])  # no attrs → full restore
        self.assertFalse(Channels.has_unfreeze_info([self.cube]))

    def test_unfreeze_rejects_partial_group(self):
        Channels.freeze_transforms([self.cube])
        restored = Channels.unfreeze_transforms(
            [self.cube], attrs=["translateX", "translateY"]
        )
        self.assertEqual(restored, [])
        # Stored data still intact.
        self.assertTrue(Channels.has_unfreeze_info([self.cube]))

    def test_unfreeze_rejects_non_transform_attr(self):
        Channels.freeze_transforms([self.cube])
        restored = Channels.unfreeze_transforms(
            [self.cube], attrs=["visibility"]
        )
        self.assertEqual(restored, [])

    def test_refreeze_after_move_accumulates_in_bake_history(self):
        """Re-freezing after a move accumulates onto the bake history.

        Freeze1 stores T(1,2,3).  Move +5 in X then freeze2 stores the
        full bake history T(6,2,3).  Move to (99,99,99), unfreeze →
        local T = (6,2,3) + (99,99,99) = (105, 101, 102).  Cumulative
        — the bake history of every previous freeze is preserved.
        """
        # First freeze at (1, 2, 3).
        Channels.freeze_transforms([self.cube])

        # Move local translate to (5, 0, 0).
        cmds.xform(self.cube, translation=(5.0, 0.0, 0.0), relative=False)

        # Second freeze accumulates: bake history becomes (1+5, 2, 3).
        Channels.freeze_transforms([self.cube])

        # Move again — unfreeze should compose this onto bake history.
        cmds.xform(self.cube, translation=(99.0, 99.0, 99.0), relative=False)

        Channels.unfreeze_transforms([self.cube])

        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.translateX"), 105.0, places=4
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.translateY"), 101.0, places=4
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.translateZ"), 102.0, places=4
        )

    def test_freeze_move_unfreeze_keeps_post_freeze_move_in_local(self):
        """Regression: freeze, move, unfreeze must NOT snap back to pre-freeze pose.

        User-reported behaviour:  freezing + moving + unfreezing used to
        discard the post-freeze move and snap the object back to its
        pre-freeze world position.  Under the cumulative contract the
        post-freeze move composes onto the bake history, so the visible
        geometry stays where the user put it and the local channels hold
        the cumulative T = (pre-freeze + post-freeze).
        """
        # setUp gave us T=(1,2,3), R=(10,20,30).
        Channels.freeze_transforms([self.cube])
        cmds.xform(self.cube, translation=(5.0, 0.0, 0.0), relative=False)

        bbox = cmds.exactWorldBoundingBox(self.cube)
        center_before = (
            (bbox[0] + bbox[3]) / 2,
            (bbox[1] + bbox[4]) / 2,
            (bbox[2] + bbox[5]) / 2,
        )

        Channels.unfreeze_transforms([self.cube])

        # Local T = stored.T + current.T = (1,2,3) + (5,0,0).
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.translateX"), 6.0, places=4
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.translateY"), 2.0, places=4
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.translateZ"), 3.0, places=4
        )

        # Visual world bbox center is preserved across the unfreeze.
        bbox = cmds.exactWorldBoundingBox(self.cube)
        center_after = (
            (bbox[0] + bbox[3]) / 2,
            (bbox[1] + bbox[4]) / 2,
            (bbox[2] + bbox[5]) / 2,
        )
        for a, b in zip(center_before, center_after):
            self.assertAlmostEqual(a, b, delta=1e-3)

    def test_freeze_move_freeze_move_unfreeze_accumulates(self):
        """Regression: two freezes interspersed with moves still recover the full history.

        Pre: T=(1,2,3).  freeze → move (5,0,0) → freeze → move (7,8,9) →
        unfreeze.  Expected local T = ((1+5) + 7, (2+0) + 8, (3+0) + 9) =
        (13, 10, 12).  Each freeze adds to bake history; nothing is
        discarded.
        """
        # Start with a clean cube (override the rotation setUp does so the
        # math is a pure translation chain).
        cmds.file(new=True, force=True)
        self.cube = cmds.polyCube(name="frz_cube")[0]
        cmds.xform(self.cube, translation=(1.0, 2.0, 3.0))

        Channels.freeze_transforms([self.cube])           # bake = T(1,2,3)
        cmds.xform(self.cube, translation=(5.0, 0.0, 0.0), relative=False)
        Channels.freeze_transforms([self.cube])           # bake = T(6,2,3)
        cmds.xform(self.cube, translation=(7.0, 8.0, 9.0), relative=False)

        Channels.unfreeze_transforms([self.cube])

        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.translateX"), 13.0, places=4
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.translateY"), 10.0, places=4
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.cube}.translateZ"), 12.0, places=4
        )


if __name__ == "__main__":
    unittest.main()
