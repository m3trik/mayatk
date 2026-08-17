# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.display_utils module

Tests for DisplayUtils class functionality including:
- Visibility operations
- Template mode
- Isolation sets
- Visible geometry queries
"""
import types
import unittest
import maya.cmds as cmds
import mayatk as mtk

from base_test import MayaTkTestCase


class TestDisplayUtils(MayaTkTestCase):
    """Tests for DisplayUtils class."""

    def setUp(self):
        """Set up test scene."""
        super().setUp()
        self.cube = cmds.polyCube(name="test_display_cube")[0]
        self.sphere = cmds.polySphere(name="test_display_sphere")[0]

    def tearDown(self):
        """Clean up."""
        for obj in ["test_display_cube", "test_display_sphere"]:
            if cmds.objExists(obj):
                cmds.delete(obj)
        super().tearDown()

    def test_set_visibility_show(self):
        """Test making objects visible."""
        cmds.hide(self.cube)
        mtk.set_visibility(self.cube, visibility=True)
        self.assertTrue(cmds.getAttr(f"{self.cube}.visibility"))

    def test_set_visibility_hide(self):
        """Test hiding objects."""
        mtk.set_visibility(self.cube, visibility=False)
        self.assertFalse(cmds.getAttr(f"{self.cube}.visibility"))

    def test_is_templated(self):
        """Test checking if object is templated."""
        result = mtk.is_templated(self.cube)
        self.assertFalse(result)

        cmds.setAttr(f"{self.cube}.template", True)
        result = mtk.is_templated(self.cube)
        self.assertTrue(result)

    def test_get_visible_geometry(self):
        """Test getting visible geometry in scene."""
        result = mtk.get_visible_geometry()
        self.assertIsInstance(result, list)
        # get_visible_geometry returns full DAG paths (unambiguous under
        # duplicate leaf names); compare by leaf name.
        self.assertIn(self.cube, [r.split("|")[-1] for r in result])

    def test_get_visible_geometry_shapes_mode(self):
        """Regression: shapes=True compared nodeType (concrete: 'mesh') to
        the abstract type 'geometry', so it ALWAYS returned []."""
        result = mtk.get_visible_geometry(shapes=True)
        # Returns full DAG paths; compare by leaf name.
        leaves = [r.split("|")[-1] for r in result]
        self.assertIn("test_display_cubeShape", leaves)
        self.assertIn("test_display_sphereShape", leaves)
        # Intermediate (Orig) shapes must not appear.
        for s in result:
            self.assertFalse(
                cmds.getAttr(f"{s}.intermediateObject"),
                f"intermediate shape leaked into result: {s}",
            )


class TestSetSmoothPreview(MayaTkTestCase):
    """Smooth-preview attrs live on the mesh SHAPE.

    A transform accepts getAttr/setAttr on them (the plug resolves down) but
    reports ``attributeQuery(exists=True)`` as False — the trap that left both
    of tentacle's subdivision spinboxes silently inert.
    """

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="smooth_cube")[0]
        self.shape = cmds.listRelatives(self.cube, shapes=True, fullPath=True)[0]

    def test_attribute_query_is_false_on_the_transform(self):
        """Pins the platform behavior the guard bug relied on."""
        self.assertFalse(
            cmds.attributeQuery("smoothLevel", node=self.cube, exists=True)
        )
        self.assertTrue(
            cmds.attributeQuery("smoothLevel", node=self.shape, exists=True)
        )

    def test_sets_the_requested_attrs_only(self):
        before = cmds.getAttr(f"{self.shape}.smoothTessLevel")

        touched = mtk.DisplayUtils.set_smooth_preview(self.cube, display=2, level=3)

        self.assertEqual(touched, [self.shape])
        self.assertEqual(cmds.getAttr(f"{self.shape}.displaySmoothMesh"), 2)
        self.assertEqual(cmds.getAttr(f"{self.shape}.smoothLevel"), 3)
        # untouched: adaptive was not requested
        self.assertEqual(cmds.getAttr(f"{self.shape}.smoothTessLevel"), before)
        self.assertTrue(cmds.getAttr(f"{self.shape}.useGlobalSmoothDrawType"))

    def test_adaptive_level_enables_the_adaptive_draw_type(self):
        """``smoothTessLevel`` is inert on any other draw type, and a mesh
        follows the GLOBAL draw type until that flag is cleared."""
        mtk.DisplayUtils.set_smooth_preview(self.cube, adaptive_level=4)

        self.assertEqual(cmds.getAttr(f"{self.shape}.smoothTessLevel"), 4)
        self.assertEqual(
            cmds.getAttr(f"{self.shape}.smoothDrawType"),
            mtk.DisplayUtils.SMOOTH_DRAW_ADAPTIVE,
        )
        self.assertFalse(cmds.getAttr(f"{self.shape}.useGlobalSmoothDrawType"))

    def test_resolves_groups_and_skips_non_meshes(self):
        curve = cmds.curve(name="smooth_curve", degree=1, point=[(0, 0, 0), (1, 0, 0)])
        grp = cmds.group(self.cube, curve, name="smooth_grp")

        touched = mtk.DisplayUtils.set_smooth_preview(grp, level=2)

        self.assertEqual([t.split("|")[-1] for t in touched], ["smooth_cubeShape"])
        self.assertEqual(cmds.getAttr(f"{touched[0]}.smoothLevel"), 2)

    def test_a_locked_plug_does_not_abort_the_selection(self):
        """One referenced/locked mesh must not cost the rest of the selection
        its write — the same contract m_cycle_display_state holds."""
        other = cmds.polyCube(name="smooth_other")[0]
        other_shape = cmds.listRelatives(other, shapes=True, fullPath=True)[0]
        cmds.setAttr(f"{self.shape}.smoothLevel", lock=True)
        before = cmds.getAttr(f"{self.shape}.smoothLevel")

        mtk.DisplayUtils.set_smooth_preview([self.cube, other], display=2, level=4)

        self.assertEqual(cmds.getAttr(f"{self.shape}.smoothLevel"), before)
        self.assertEqual(cmds.getAttr(f"{other_shape}.smoothLevel"), 4)
        # the unlocked plugs on the locked mesh are still written
        self.assertEqual(cmds.getAttr(f"{self.shape}.displaySmoothMesh"), 2)

    def test_nothing_to_do_returns_empty(self):
        curve = cmds.curve(name="smooth_only_curve", degree=1, point=[(0, 0, 0), (1, 0, 0)])
        self.assertEqual(mtk.DisplayUtils.set_smooth_preview(curve, level=2), [])
        self.assertEqual(mtk.DisplayUtils.set_smooth_preview([], level=2), [])


class TestExplodedView(MayaTkTestCase):
    """Regression: ExplodedView must operate on cmds-style string node names.

    Bug fixed 2026-05-07: ``arrange_objects``, ``un_explode``, and
    ``un_explode_all`` called ``node.name()`` / ``obj.original_position`` /
    ``obj_attr.node()`` — PyMEL idioms — against plain strings.
    """

    def setUp(self):
        super().setUp()
        from mayatk.display_utils.exploded_view import ExplodedView

        self.ExplodedView = ExplodedView
        # Reset the class-level cache so tests don't leak state.
        ExplodedView.exploded_objects = {}

        self.parent = cmds.group(empty=True, name="ev_parent")
        self.cubes = []
        for i in range(3):
            c = cmds.polyCube(name=f"ev_cube_{i}")[0]
            cmds.parent(c, self.parent)
            cmds.xform(c, translation=[i * 2.0, 0, 0], worldSpace=True)
            self.cubes.append(c)

    def test_arrange_objects_accepts_string_nodes(self):
        """arrange_objects builds its cache key from string names without crashing."""
        ev = self.ExplodedView()
        # Children come back from get_unique_children as strings.
        children = [
            c for c in cmds.listRelatives(self.parent, children=True, fullPath=False) or []
        ]
        # Should not raise AttributeError on .name()
        ev.arrange_objects(children)

        # Cache key must be a tuple of node-name strings.
        keys = list(self.ExplodedView.exploded_objects.keys())
        self.assertTrue(keys, "arrange_objects should populate the cache")
        self.assertTrue(
            all(isinstance(k, str) for k in keys[0]),
            f"Cache key should hold strings, got {keys[0]!r}",
        )

    def test_explode_un_explode_round_trip(self):
        """explode followed by un_explode_all must restore world positions."""
        ev = self.ExplodedView(objects=[self.parent])
        original_positions = {
            c: cmds.xform(c, query=True, translation=True, worldSpace=True)
            for c in self.cubes
        }

        ev.explode()
        # Every cube should now carry the original_position attr.
        for c in self.cubes:
            self.assertTrue(
                cmds.attributeQuery("original_position", node=c, exists=True),
                f"explode() should set original_position on {c}",
            )

        ev.un_explode_all()

        for c in self.cubes:
            restored = cmds.xform(c, query=True, translation=True, worldSpace=True)
            for axis, expected in enumerate(original_positions[c]):
                self.assertAlmostEqual(
                    restored[axis],
                    expected,
                    places=3,
                    msg=f"{c} axis {axis}: restored {restored[axis]} != original {expected}",
                )
            self.assertFalse(
                cmds.attributeQuery("original_position", node=c, exists=True),
                f"un_explode_all should remove original_position from {c}",
            )

    def test_explode_actually_moves_objects(self):
        """explode() must change world positions, not just stamp attrs.

        Without this, an inverse round-trip can pass trivially (move nothing,
        restore nothing).
        """
        ev = self.ExplodedView(objects=[self.parent])
        before = {
            c: cmds.xform(c, query=True, translation=True, worldSpace=True)
            for c in self.cubes
        }

        ev.explode()

        moved = [
            c
            for c in self.cubes
            if cmds.xform(c, query=True, translation=True, worldSpace=True) != before[c]
        ]
        self.assertTrue(
            moved,
            "explode() should change world positions of at least one child",
        )

    def test_re_explode_uses_cached_positions(self):
        """A second explode of the same node-set replays cached positions.

        Locks in the documented cache-restore behavior in
        ``arrange_objects``: same nodes → same final pose, no re-simulation.
        """
        ev = self.ExplodedView(objects=[self.parent])
        ev.explode()
        first_positions = {
            c: cmds.xform(c, query=True, translation=True, worldSpace=True)
            for c in self.cubes
        }

        ev.un_explode_all()
        ev.explode()

        for c in self.cubes:
            replayed = cmds.xform(c, query=True, translation=True, worldSpace=True)
            for axis, expected in enumerate(first_positions[c]):
                self.assertAlmostEqual(
                    replayed[axis],
                    expected,
                    places=3,
                    msg=(
                        f"re-explode of {c} axis {axis} should hit cache "
                        f"({replayed[axis]} != {expected})"
                    ),
                )

    def test_un_explode_only_affects_targeted_hierarchy(self):
        """un_explode(objects=[group_a]) leaves group_b's exploded cubes alone."""
        parent_b = cmds.group(empty=True, name="ev_parent_b")
        cubes_b = []
        for i in range(3):
            c = cmds.polyCube(name=f"ev_cube_b_{i}")[0]
            cmds.parent(c, parent_b)
            cmds.xform(c, translation=[i * 2.0, 5.0, 0], worldSpace=True)
            cubes_b.append(c)

        ev = self.ExplodedView()
        ev.explode(objects=[self.parent, parent_b])

        ev.un_explode(objects=[self.parent])

        for c in self.cubes:
            self.assertFalse(
                cmds.attributeQuery("original_position", node=c, exists=True),
                f"{c} (group_a) should be un-exploded",
            )
        for c in cubes_b:
            self.assertTrue(
                cmds.attributeQuery("original_position", node=c, exists=True),
                f"{c} (group_b) should still be exploded",
            )

    def test_un_explode_skips_unexploded_objects(self):
        """un_explode filters to nodes carrying original_position only.

        Pre-explode one cube manually, then un_explode the parent: the bare
        cubes are ignored, the marked cube is restored to the stamped value.
        """
        from mayatk.node_utils.attributes._attributes import Attributes

        target_cube = self.cubes[0]
        original = cmds.xform(target_cube, query=True, translation=True, worldSpace=True)
        # Stamp original_position and push the cube somewhere obviously wrong.
        Attributes.set_attributes(target_cube, create=True, original_position=original)
        cmds.xform(target_cube, translation=[99.0, 99.0, 99.0], worldSpace=True)

        ev = self.ExplodedView()
        ev.un_explode(objects=[self.parent])

        restored = cmds.xform(target_cube, query=True, translation=True, worldSpace=True)
        for axis, expected in enumerate(original):
            self.assertAlmostEqual(
                restored[axis],
                expected,
                places=3,
                msg=f"marked cube axis {axis}: {restored[axis]} != {expected}",
            )
        # Untouched cubes should still lack the attr.
        for c in self.cubes[1:]:
            self.assertFalse(
                cmds.attributeQuery("original_position", node=c, exists=True),
                f"{c} should not have been touched by un_explode",
            )

    def test_toggle_explode_unexploded_to_exploded(self):
        """toggle on a fully-unexploded group runs explode()."""
        ev = self.ExplodedView(objects=[self.parent])
        ev.toggle_explode()

        for c in self.cubes:
            self.assertTrue(
                cmds.attributeQuery("original_position", node=c, exists=True),
                f"toggle should have exploded {c}",
            )

    def test_toggle_explode_exploded_to_unexploded(self):
        """toggle on a fully-exploded group runs un_explode()."""
        ev = self.ExplodedView(objects=[self.parent])
        ev.explode()
        self.assertTrue(
            all(
                cmds.attributeQuery("original_position", node=c, exists=True)
                for c in self.cubes
            ),
            "precondition: all cubes exploded",
        )

        ev.toggle_explode()

        for c in self.cubes:
            self.assertFalse(
                cmds.attributeQuery("original_position", node=c, exists=True),
                f"toggle should have un-exploded {c}",
            )

    def test_empty_selection_is_noop(self):
        """No selection and no objects arg → warns, returns without raising."""
        cmds.select(clear=True)
        ev = self.ExplodedView()
        # Should not raise; cmds.warning is non-fatal.
        ev.explode()
        ev.un_explode()
        # No cube should have been touched.
        for c in self.cubes:
            self.assertFalse(
                cmds.attributeQuery("original_position", node=c, exists=True),
                f"empty-selection explode should not have touched {c}",
            )

    def test_namespaced_nodes_round_trip(self):
        """Round-trip works on nodes inside a namespace."""
        cmds.namespace(addNamespace="ev_ns")
        ns_parent = cmds.group(empty=True, name="ev_ns:ns_parent")
        ns_cubes = []
        for i in range(3):
            c = cmds.polyCube(name=f"ev_ns:ns_cube_{i}")[0]
            cmds.parent(c, ns_parent)
            cmds.xform(c, translation=[i * 2.0, 0, 0], worldSpace=True)
            ns_cubes.append(c)

        original_positions = {
            c: cmds.xform(c, query=True, translation=True, worldSpace=True)
            for c in ns_cubes
        }

        ev = self.ExplodedView(objects=[ns_parent])
        ev.explode()
        ev.un_explode_all()

        for c in ns_cubes:
            restored = cmds.xform(c, query=True, translation=True, worldSpace=True)
            for axis, expected in enumerate(original_positions[c]):
                self.assertAlmostEqual(
                    restored[axis],
                    expected,
                    places=3,
                    msg=f"{c} axis {axis}: {restored[axis]} != {expected}",
                )


class TestColorIdSets(MayaTkTestCase):
    """Set Per Color — the ``ID_<HEX>`` objectSet grouping (blendertk's ID-collection twin).

    Membership discovery keys on the ``mtk_color_id`` stamp, never the ``ID_`` name, so a
    user's own set called ``ID_*`` is never adopted or deleted."""

    RED = (0.8, 0.1, 0.1)
    BLUE = (0.1, 0.2, 0.9)

    def setUp(self):
        super().setUp()
        from mayatk.display_utils.color_id import ColorId

        self.ColorId = ColorId
        self.a = cmds.polyCube(name="cid_a")[0]
        self.b = cmds.polyCube(name="cid_b")[0]

    def test_add_stamps_and_round_trips_the_exact_color(self):
        node = self.ColorId.add_to_color_set([self.a], self.RED)
        self.assertTrue(cmds.objExists(node))
        self.assertTrue(cmds.sets(self.a, isMember=node))
        got = self.ColorId.get_color_set_color(self.a)
        self.assertIsNotNone(got)
        self.assertLess(self.ColorId.get_color_difference(got, self.RED), 1e-6)
        self.assertIsNone(self.ColorId.get_color_set_color(self.b))

    def test_recolor_moves_between_sets_and_gcs_the_emptied_one(self):
        self.ColorId.add_to_color_set([self.a], self.RED)
        self.ColorId.add_to_color_set([self.a], self.BLUE)
        self.assertEqual(len(self.ColorId._stamped_sets()), 1)
        got = self.ColorId.get_color_set_color(self.a)
        self.assertLess(self.ColorId.get_color_difference(got, self.BLUE), 1e-6)

    def test_select_by_set_color(self):
        self.ColorId.add_to_color_set([self.a], self.RED)
        found = self.ColorId.get_objects_by_color(self.RED, check_set=True)
        self.assertEqual([n.split("|")[-1] for n in found], ["cid_a"])

    def test_apply_color_routes_set_per_color(self):
        self.ColorId.apply_color([self.b], self.RED, set_per_color=True)
        self.assertIsNotNone(self.ColorId.get_color_set_color(self.b))

    def test_user_named_id_set_is_never_adopted_or_deleted(self):
        user = cmds.sets(name="ID_CC1919", empty=True)
        cmds.sets(self.b, add=user)
        self.ColorId.add_to_color_set([self.a], self.RED)  # same color -> same ID_ name
        self.assertFalse(
            cmds.attributeQuery(self.ColorId._ID_SET_ATTR, node=user, exists=True)
        )
        self.ColorId.reset_colors([self.a, self.b])
        self.assertTrue(cmds.objExists(user))
        self.assertTrue(cmds.sets(self.b, isMember=user))

    def test_reset_clears_sets_without_touching_members(self):
        self.ColorId.add_to_color_set([self.a, self.b], self.RED)
        self.ColorId.reset_colors([self.a, self.b])
        self.assertEqual(self.ColorId._stamped_sets(), [])
        self.assertTrue(cmds.objExists(self.a) and cmds.objExists(self.b))
        self.assertIsNone(self.ColorId.get_color_set_color(self.a))

    def test_empty_batch_is_a_no_op(self):
        before = len(cmds.ls(sets=True) or [])
        self.assertIsNone(self.ColorId.add_to_color_set([], self.RED))
        self.assertEqual(len(cmds.ls(sets=True) or []), before)


class _StubCheckBox:
    """``isChecked``-only stand-in for a channel checkbox."""

    def __init__(self, state):
        self._state = bool(state)

    def isChecked(self):
        return self._state


class _StubSwitchboard:
    """Switchboard stand-in — the reset path reads only the Ctrl modifier + message_box."""

    CTRL = object()

    def __init__(self):
        self.messages = []
        self.modifier = None  # set to CTRL to exercise the reset-everything path
        self.app = types.SimpleNamespace(keyboardModifiers=lambda: self.modifier)
        self.QtCore = types.SimpleNamespace(
            Qt=types.SimpleNamespace(ControlModifier=self.CTRL)
        )

    def message_box(self, message, **kwargs):
        self.messages.append(message)


class TestColorIdSlotsChannelScope(MayaTkTestCase):
    """The channel checkboxes scope Reset, not just Set Color / Select By Color.

    Regression: ``b000`` called ``reset_colors(objects)`` with no flags, so all five
    ``reset_*`` defaults (True) fired — clearing a wireframe tint also reassigned lambert1,
    deleted the object's materials, its vertex-color sets and its ID set."""

    RED = (0.8, 0.1, 0.1)

    def setUp(self):
        super().setUp()
        from mayatk.display_utils.color_id import ColorId, ColorIdSlots

        self.ColorId = ColorId
        self.cube = cmds.polyCube(name="cid_scope")[0]
        self.shape = cmds.listRelatives(self.cube, shapes=True, fullPath=True)[0]
        # Slot instance without the Qt __init__: b000 touches only the checkboxes,
        # the Ctrl modifier and message_box, all stubbed above.
        self.slots = ColorIdSlots.__new__(ColorIdSlots)
        self.slots.sb = _StubSwitchboard()

    def _set_channels(
        self,
        wireframe=False,
        outliner=False,
        material=False,
        vertex=False,
        set_per_color=False,
    ):
        self.slots.ui = types.SimpleNamespace(
            chk012=_StubCheckBox(wireframe),
            chk013=_StubCheckBox(outliner),
            chk014=_StubCheckBox(material),
            chk015=_StubCheckBox(vertex),
            chk016=_StubCheckBox(set_per_color),
        )

    def _apply_every_channel(self):
        self.ColorId.apply_color(
            [self.cube],
            self.RED,
            apply_to_wireframe=True,
            apply_to_outliner=True,
            apply_to_material=True,
            apply_to_vertex=True,
            set_per_color=True,
        )
        cmds.select(self.cube)

    def test_reset_leaves_unchecked_channels_alone(self):
        self._apply_every_channel()
        self._set_channels(wireframe=True)  # only the wireframe channel is enabled
        self.slots.b000()

        self.assertFalse(cmds.getAttr(f"{self.cube}.overrideEnabled"))
        # material kept (a reset would have swapped in lambert1's grey)
        mat_color = self.ColorId.get_material_color(self.cube)
        self.assertIsNotNone(mat_color)
        self.assertLess(self.ColorId.get_color_difference(mat_color, self.RED), 0.05)
        self.assertTrue(cmds.getAttr(f"{self.cube}.useOutlinerColor"))
        self.assertTrue(cmds.polyColorSet(self.shape, query=True, allColorSets=True))
        self.assertIsNotNone(self.ColorId.get_color_set_color(self.cube))

    def test_reset_clears_every_enabled_channel(self):
        self._apply_every_channel()
        self._set_channels(
            wireframe=True,
            outliner=True,
            material=True,
            vertex=True,
            set_per_color=True,
        )
        self.slots.b000()

        self.assertFalse(cmds.getAttr(f"{self.cube}.overrideEnabled"))
        self.assertFalse(cmds.getAttr(f"{self.cube}.useOutlinerColor"))
        self.assertFalse(cmds.polyColorSet(self.shape, query=True, allColorSets=True))
        self.assertIsNone(self.ColorId.get_color_set_color(self.cube))
        mat_color = self.ColorId.get_material_color(self.cube)
        self.assertGreater(self.ColorId.get_color_difference(mat_color, self.RED), 0.05)

    def test_material_channel_is_readable_from_the_transform(self):
        """Shading engines connect to the SHAPE — a transform-only lookup found nothing, so
        the Material channel's Select By Color matched no object at all."""
        self.ColorId.apply_color([self.cube], self.RED, apply_to_material=True)
        mat_color = self.ColorId.get_material_color(self.cube)
        self.assertIsNotNone(mat_color)
        self.assertLess(self.ColorId.get_color_difference(mat_color, self.RED), 1e-3)
        found = self.ColorId.get_objects_by_color(self.RED, check_material_color=True)
        self.assertEqual([n.split("|")[-1] for n in found], ["cid_scope"])

    def test_reset_with_no_channels_enabled_is_a_no_op(self):
        self._apply_every_channel()
        self._set_channels()
        self.slots.b000()

        self.assertTrue(cmds.getAttr(f"{self.cube}.overrideEnabled"))
        self.assertTrue(self.slots.sb.messages)

    def test_select_by_color_with_no_channels_keeps_the_selection(self):
        """With nothing checked it fell through to a zero-check query, and answered the
        empty result by clearing the user's selection."""
        cmds.select(self.cube)
        self._set_channels()
        self.slots.b002()

        self.assertEqual(
            cmds.ls(selection=True, long=True), cmds.ls(self.cube, long=True)
        )
        self.assertTrue(self.slots.sb.messages)

    def test_ctrl_click_reset_reaches_the_transforms(self):
        """Ctrl+click sweeps ``cmds.ls(geometry=True)`` — shapes — but Set Color writes the
        outliner/wireframe channels on the transform, so it has to walk up to reach them."""
        self.ColorId.apply_color(
            [self.cube], self.RED, apply_to_wireframe=True, apply_to_outliner=True
        )
        cmds.select(clear=True)  # the Ctrl path must not depend on a selection
        self.slots.sb.modifier = self.slots.sb.CTRL
        self._set_channels(wireframe=True, outliner=True)
        self.slots.b000()

        self.assertFalse(cmds.getAttr(f"{self.cube}.overrideEnabled"))
        self.assertFalse(cmds.getAttr(f"{self.cube}.useOutlinerColor"))

    def test_reset_vertex_colors_accepts_shapes(self):
        """Ctrl+click reset passes ``cmds.ls(geometry=True)`` — i.e. shapes, not transforms."""
        self.ColorId.apply_color([self.cube], self.RED, apply_to_vertex=True)
        self.assertTrue(cmds.polyColorSet(self.shape, query=True, allColorSets=True))
        self.ColorId.reset_vertex_colors([self.shape])
        self.assertFalse(cmds.polyColorSet(self.shape, query=True, allColorSets=True))


class TestHiddenInOutliner(MayaTkTestCase):
    """DisplayUtils.set_hidden_in_outliner — the Outliner-row display flag."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="hio_cube")[0]
        self.shape = cmds.listRelatives(self.cube, shapes=True, fullPath=True)[0]

    def _flag(self, node):
        return bool(cmds.getAttr(f"{node}.hiddenInOutliner"))

    def test_hides_transform_and_shape(self):
        changed = mtk.DisplayUtils.set_hidden_in_outliner(self.cube)
        self.assertTrue(self._flag(self.cube))
        self.assertTrue(self._flag(self.shape))
        self.assertEqual(len(changed), 2)

    def test_shapes_opt_out(self):
        mtk.DisplayUtils.set_hidden_in_outliner(self.cube, shapes=False)
        self.assertTrue(self._flag(self.cube))
        self.assertFalse(self._flag(self.shape))

    def test_idempotent_reports_no_change(self):
        """A second call must not write (nothing changed → no panel redraw)."""
        mtk.DisplayUtils.set_hidden_in_outliner(self.cube)
        self.assertEqual(mtk.DisplayUtils.set_hidden_in_outliner(self.cube), [])

    def test_restores_row(self):
        mtk.DisplayUtils.set_hidden_in_outliner(self.cube)
        changed = mtk.DisplayUtils.set_hidden_in_outliner(self.cube, state=False)
        self.assertEqual(len(changed), 2)
        self.assertFalse(self._flag(self.cube))
        self.assertFalse(self._flag(self.shape))

    def test_component_input_resolves_to_node(self):
        mtk.DisplayUtils.set_hidden_in_outliner(f"{self.cube}.f[0]")
        self.assertTrue(self._flag(self.cube))

    def test_non_dag_node_skipped(self):
        """A DG node has no hiddenInOutliner — skip it, don't raise."""
        network = cmds.createNode("network", name="hio_network")
        self.assertEqual(mtk.DisplayUtils.set_hidden_in_outliner(network), [])

    def test_missing_node_skipped(self):
        self.assertEqual(
            mtk.DisplayUtils.set_hidden_in_outliner("hio_does_not_exist"), []
        )

    def test_duplicate_short_name_flags_every_match(self):
        """A short name matching two nodes must not silently no-op.

        ``getAttr`` on an ambiguous plug hands back a *list* of values (whose
        truthiness reads as "already hidden") and ``setAttr`` raises — so the
        names are resolved to full paths before any plug is touched.
        """
        for parent in ("hio_grp1", "hio_grp2"):
            cmds.group(empty=True, name=parent)
            cmds.group(empty=True, name="hio_dupe", parent=parent)
        paths = cmds.ls("hio_dupe", long=True)
        self.assertEqual(len(paths), 2, "test needs a genuinely ambiguous name")

        changed = mtk.DisplayUtils.set_hidden_in_outliner("hio_dupe")

        self.assertCountEqual(changed, paths)
        for path in paths:
            self.assertTrue(self._flag(path))

    def test_wildcard_input(self):
        """Names go through ``cmds.ls``, so wildcards work as in set_visibility."""
        cmds.group(empty=True, name="hio_wild_a")
        cmds.group(empty=True, name="hio_wild_b")
        changed = mtk.DisplayUtils.set_hidden_in_outliner("hio_wild_*")
        self.assertEqual(len(changed), 2)
        self.assertTrue(self._flag("hio_wild_a"))
        self.assertTrue(self._flag("hio_wild_b"))

    def test_locked_plug_not_reported_as_changed(self):
        """A locked plug is skipped, not force-written — so it must not be claimed."""
        cmds.setAttr(f"{self.cube}.hiddenInOutliner", lock=True)
        changed = mtk.DisplayUtils.set_hidden_in_outliner(self.cube)
        self.assertEqual(changed, [self.shape])
        self.assertFalse(self._flag(self.cube))

    def test_hidden_node_stays_listable_and_selectable(self):
        """The flag is display-only — ls/select (and therefore export sets) still see it."""
        mtk.DisplayUtils.set_hidden_in_outliner(self.cube)
        self.assertIn(self.cube, cmds.ls(self.cube))
        cmds.select(self.cube, replace=True)
        self.assertIn(self.cube, cmds.ls(selection=True))

    def test_flat_alias(self):
        """Exposed flat off the wildcard root, like the rest of display_utils."""
        mtk.set_hidden_in_outliner(self.cube)
        self.assertTrue(self._flag(self.cube))


if __name__ == "__main__":
    unittest.main(verbosity=2)
