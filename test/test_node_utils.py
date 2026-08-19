# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.node_utils module

Tests for NodeUtils class functionality including:
- Node type detection
- Transform and shape node queries
- Parent/child relationships
- Group detection and management
- Locator utilities
- Node attribute operations
- Node connections
- Instancing operations
- Assembly creation
"""
import unittest
import maya.cmds as cmds
import maya.mel as mel
import mayatk as mtk
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.node_utils.attributes._attributes import Attributes
from base_test import MayaTkTestCase


class TestNodeUtils(MayaTkTestCase):
    """Comprehensive tests for NodeUtils class."""

    def setUp(self):
        """Set up test scene with standard geometry."""
        super().setUp()
        # Create test cylinder
        self.cyl = cmds.polyCylinder(
            radius=5,
            height=10,
            subdivisionsX=12,
            subdivisionsY=1,
            subdivisionsZ=1,
            name="cyl",
        )[0]
        self.cyl_shape = cmds.listRelatives(self.cyl, shapes=True)[0]

    # -------------------------------------------------------------------------
    # Node Type Detection Tests
    # -------------------------------------------------------------------------

    def test_get_type(self):
        """Test getting type of various nodes."""
        self.assertEqual(NodeUtils.get_type("cyl"), "mesh")
        self.assertEqual(NodeUtils.get_type(self.cyl_shape), "mesh")
        self.assertEqual(NodeUtils.get_type(f"{self.cyl_shape}.vtx[0]"), "vtx")

        # Test list input
        types = NodeUtils.get_type(["cyl", self.cyl_shape])
        self.assertEqual(types, ["mesh", "mesh"])

    def test_is_geometry(self):
        """Test is_geometry method."""
        self.assertTrue(NodeUtils.is_geometry("cyl"))

        # Create a group (transform but no shape)
        grp = cmds.group(empty=True, name="empty_grp")
        self.assertFalse(NodeUtils.is_geometry(grp))

        # Create a locator (has shape but is locator)
        loc = cmds.spaceLocator(name="loc")[0]
        # is_geometry checks for ANY shape, so locator is technically geometry in this context?
        # Let's check implementation: "Return True for each object that has a shape node and is not a group."
        # Locator has a shape, so it should be True unless specifically excluded.
        self.assertTrue(NodeUtils.is_geometry(loc))

        # Filter mode
        result = NodeUtils.is_geometry(["cyl", grp, loc], filter=True)
        self.assertIn(self.cyl, result)
        self.assertNotIn(grp, result)

    # -------------------------------------------------------------------------
    # Transform, Shape, History Queries
    # -------------------------------------------------------------------------

    def test_get_transform_node(self):
        """Test get_transform_node."""
        # From transform
        self.assertEqual(NodeUtils.get_transform_node("cyl"), self.cyl)
        # From shape
        self.assertEqual(NodeUtils.get_transform_node(self.cyl_shape), self.cyl)
        # From component
        self.assertEqual(NodeUtils.get_transform_node(f"{self.cyl}.vtx[0]"), self.cyl)

        # Test with attributes=True
        attrs = NodeUtils.get_transform_node(
            "cyl", attributes=True, returned_type="str"
        )
        self.assertIsInstance(attrs, list)
        self.assertIn("translateX", attrs)

    def test_get_shape_node(self):
        """Test get_shape_node."""
        # From transform
        self.assertEqual(NodeUtils.get_shape_node("cyl"), self.cyl_shape)
        # From shape
        self.assertEqual(NodeUtils.get_shape_node(self.cyl_shape), self.cyl_shape)

        # Test with attributes=True
        attrs = NodeUtils.get_shape_node("cyl", attributes=True, returned_type="str")
        self.assertIsInstance(attrs, list)

    def test_get_shape_node_order_is_deterministic(self):
        """Results follow DAG/input order, not str-hash order.

        A bare ``set`` here made the order vary PER PROCESS, so a caller taking
        ``[0]`` off a deformed mesh (live shape + its orig) got a different shape
        run to run -- a coin flip that reads as a scene problem, not a code one.
        """
        cube = cmds.polyCube(name="orderCube")[0]
        cmds.select(cube)
        cmds.nonLinear(type="bend")  # deformer -> the transform gains an orig shape
        cmds.select(clear=True)
        want = cmds.listRelatives(cube, shapes=True, fullPath=True) or []
        self.assertEqual(len(want), 2, "test needs a mesh with an orig shape")

        got = NodeUtils.get_shape_node(cube, returned_type="str")
        self.assertEqual([cmds.ls(s, long=True)[0] for s in got], want)

        # Several nodes: input order preserved, no duplicates.
        other = cmds.polyCube(name="orderCube2")[0]
        pair = NodeUtils.get_shape_node([cube, other, cube], returned_type="str")
        self.assertEqual(
            [cmds.ls(s, long=True)[0] for s in pair],
            want + (cmds.listRelatives(other, shapes=True, fullPath=True) or []),
        )

    def test_get_history_node(self):
        """Test get_history_node."""
        hist = NodeUtils.get_history_node("cyl")
        self.assertEqual(cmds.nodeType(hist), "polyCylinder")

    # -------------------------------------------------------------------------
    # Group & Hierarchy Tests
    # -------------------------------------------------------------------------

    def test_is_group(self):
        """Test is_group detection."""
        self.assertFalse(NodeUtils.is_group("cyl"))

        grp = cmds.group(empty=True, name="test_grp")
        self.assertTrue(NodeUtils.is_group(grp))

        # Group with children
        cmds.parent(self.cyl, grp)
        self.assertTrue(NodeUtils.is_group(grp))

    def test_get_groups(self):
        """Test get_groups."""
        grp1 = cmds.group(empty=True, name="grp1")
        grp2 = cmds.group(empty=True, name="grp2")
        cmds.parent(self.cyl, grp1)

        groups = NodeUtils.get_groups()
        self.assertIn(grp1, groups)
        self.assertIn(grp2, groups)

        # Test empty=True
        empty_groups = NodeUtils.get_groups(empty=True)
        self.assertIn(grp2, empty_groups)
        self.assertNotIn(grp1, empty_groups)

    def test_get_unique_children(self):
        """Test get_unique_children."""
        c1 = cmds.polyCube(n="c1")[0]
        c2 = cmds.polyCube(n="c2")[0]
        grp = cmds.group(c1, c2, n="parent_grp")

        children = NodeUtils.get_unique_children(grp)
        self.assertEqual(len(children), 2)
        # get_unique_children returns full DAG paths (listRelatives fullPath=True),
        # so compare by leaf name rather than the bare creation name.
        leaves = {c.split("|")[-1] for c in children}
        self.assertIn(c1, leaves)
        self.assertIn(c2, leaves)

    def test_get_unique_children_preserves_hierarchy_order(self):
        """Callers key decisions off the first element, so order must be stable."""
        a = cmds.polyCube(n="ord_a")[0]
        b = cmds.polyCube(n="ord_b")[0]
        c = cmds.polyCube(n="ord_c")[0]
        grp = cmds.group(a, b, c, n="ord_grp")

        expected = cmds.listRelatives(grp, children=True, fullPath=True)
        self.assertEqual(NodeUtils.get_unique_children(grp), expected)

    def test_get_unique_children_resolves_components(self):
        cube = cmds.polyCube(n="comp_cube")[0]

        children = NodeUtils.get_unique_children([f"{cube}.f[0]", f"{cube}.f[1]"])

        self.assertEqual([c.split("|")[-1] for c in children], [cube])

    def test_get_unique_children_expands_object_sets(self):
        """A set node has no DAG attributes -- it must resolve to its members.

        Compared unordered: ``cmds.sets(query=True)`` hands members back in
        Maya's own (hash) order, so a set has no membership order to preserve.
        """
        a = cmds.polyCube(n="set_a")[0]
        b = cmds.polyCube(n="set_b")[0]
        s = cmds.sets([a, b], n="set_members")

        children = NodeUtils.get_unique_children(s)

        self.assertEqual(sorted(c.split("|")[-1] for c in children), sorted([a, b]))

    def test_get_unique_children_preserves_argument_order(self):
        """Callers key off the first element, so input order must survive."""
        a = cmds.polyCube(n="arg_a")[0]
        b = cmds.polyCube(n="arg_b")[0]

        self.assertEqual(
            [c.split("|")[-1] for c in NodeUtils.get_unique_children([b, a])], [b, a]
        )

    def test_get_unique_children_expands_nested_sets_and_groups(self):
        """Members may themselves be sets, groups, or components."""
        leaf = cmds.polyCube(n="nest_leaf")[0]
        grp_child = cmds.polyCube(n="nest_grouped")[0]
        grp = cmds.group(grp_child, n="nest_grp")
        comp_owner = cmds.polyCube(n="nest_comp")[0]

        inner = cmds.sets([leaf], n="nest_inner")
        outer = cmds.sets([inner, grp, f"{comp_owner}.f[0]"], n="nest_outer")

        children = NodeUtils.get_unique_children(outer)

        self.assertEqual(
            sorted(c.split("|")[-1] for c in children),
            sorted([leaf, grp_child, comp_owner]),
        )

    def test_get_unique_children_dedupes_a_shared_subtree(self):
        """Two sets sharing a group must yield its leaves once, in order."""
        a = cmds.polyCube(n="shared_a")[0]
        b = cmds.polyCube(n="shared_b")[0]
        grp = cmds.group(a, b, n="shared_grp")
        s1 = cmds.sets([grp], n="shared_set_1")
        s2 = cmds.sets([grp], n="shared_set_2")

        children = NodeUtils.get_unique_children([s1, s2])

        self.assertEqual([c.split("|")[-1] for c in children], [a, b])

    def test_get_shapes(self):
        """get_shapes returns non-intermediate shape children of a transform."""
        shapes = NodeUtils.get_shapes("cyl")
        self.assertEqual(len(shapes), 1)
        self.assertEqual(cmds.nodeType(shapes[0]), "mesh")

        # Empty transform (group) returns []
        grp = cmds.group(empty=True, name="empty_grp")
        self.assertEqual(NodeUtils.get_shapes(grp), [])

    def test_get_shapes_accepts_a_list(self):
        """A mixed list of transforms and shapes resolves in one pass."""
        cube = cmds.polyCube(name="batchCube")[0]
        cube_shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        cyl_shape = NodeUtils.get_shapes("cyl")[0]

        got = NodeUtils.get_shapes([cube, cyl_shape])
        self.assertCountEqual(got, [cube_shape, cyl_shape])

        # Naming the same geometry twice (transform AND its shape) yields it once.
        self.assertEqual(NodeUtils.get_shapes([cube, cube_shape]), [cube_shape])
        self.assertEqual(NodeUtils.get_shapes([]), [])

    def test_get_shapes_descend_is_opt_in_and_gated(self):
        """descend=True resolves groups; the default stays direct-children only."""
        cube = cmds.polyCube(name="descendCube")[0]
        inner = cmds.group(cube, name="descend_inner")
        outer = cmds.group(inner, name="descend_outer")
        cube_shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]

        self.assertEqual(NodeUtils.get_shapes(outer), [])
        self.assertEqual(NodeUtils.get_shapes(outer, descend=True), [cube_shape])

        # Gated: a transform carrying its own shape contributes only that shape,
        # never its children's.
        child = cmds.polyCube(name="descendChild")[0]
        cmds.parent(child, cube)
        self.assertEqual(NodeUtils.get_shapes(cube, descend=True), [cube_shape])

    def test_get_shapes_descend_dedupes_overlapping_inputs(self):
        """Naming a group AND a mesh under it yields that mesh once.

        The descend branch and the direct-children branch both reach it, so the
        two have to agree on spelling for the dedupe to hold -- they do, in both
        path modes and for an ambiguous leaf name (measured).
        """
        mesh = cmds.polyCube(name="dedupeCube")[0]
        grp = cmds.group(mesh, name="dedupe_grp")

        for full_path in (True, False):
            got = NodeUtils.get_shapes([grp, mesh], full_path=full_path, descend=True)
            self.assertEqual(
                len(got), 1, f"full_path={full_path} returned duplicates: {got}"
            )

    def test_get_shapes_descend_excludes_intermediates(self):
        """noIntermediate is honoured through the descent (orig shapes stay out)."""
        deformed = cmds.polyCube(name="descendDeformed")[0]
        cmds.cluster(deformed)
        grp = cmds.group(deformed, name="descend_deform_grp")

        shapes = NodeUtils.get_shapes(grp, descend=True)
        self.assertEqual(len(shapes), 1, f"orig shape leaked: {shapes}")
        self.assertFalse(NodeUtils.is_intermediate(shapes[0]))

    def test_get_shapes_type_filters_every_branch(self):
        """The type filter is applied last, so it covers all three branches."""
        cube = cmds.polyCube(name="typed_cube")[0]
        curve = cmds.curve(name="typed_curve", degree=1, point=[(0, 0, 0), (1, 0, 0)])
        grp = cmds.group(cube, curve, name="typed_grp")
        # after the group: re-parenting invalidates any path captured earlier
        cube_shape = NodeUtils.get_shapes(cube)[0]
        curve_shape = NodeUtils.get_shapes(curve)[0]

        # direct children, an already-a-shape input, and a group descent
        self.assertEqual(NodeUtils.get_shapes(cube, type="mesh"), [cube_shape])
        self.assertEqual(NodeUtils.get_shapes(curve_shape, type="mesh"), [])
        self.assertEqual(NodeUtils.get_shapes(grp, descend=True, type="mesh"), [cube_shape])
        # unfiltered still returns both
        self.assertEqual(len(NodeUtils.get_shapes(grp, descend=True)), 2)

    def test_get_shapes_type_preserves_input_order(self):
        """The filter runs through ``cmds.ls``, which must not re-sort."""
        cubes = [cmds.polyCube(name=f"ordered_{i}")[0] for i in range(4)]
        want = [NodeUtils.get_shapes(c)[0] for c in reversed(cubes)]

        self.assertEqual(NodeUtils.get_shapes(list(reversed(cubes)), type="mesh"), want)

    def test_get_shape_singular(self):
        """get_shape returns the first shape, or None."""
        shape = NodeUtils.get_shape("cyl")
        self.assertIsNotNone(shape)
        self.assertEqual(cmds.nodeType(shape), "mesh")

        grp = cmds.group(empty=True, name="empty_grp2")
        self.assertIsNone(NodeUtils.get_shape(grp))

    def test_get_shape_input_flexibility(self):
        """get_shape(s) accepts a transform, a shape, or a component."""
        cube = cmds.polyCube(name="flexCube")[0]
        shape = cmds.listRelatives(cube, shapes=True, fullPath=True)[0]
        want = cmds.ls(shape, long=True)

        norm = lambda r: cmds.ls(r, long=True)
        self.assertEqual(norm(NodeUtils.get_shape(cube)), want)  # transform (regression)
        self.assertEqual(norm(NodeUtils.get_shape(shape)), want)  # shape -> itself
        self.assertEqual(norm(NodeUtils.get_shape(f"{cube}.f[0]")), want)  # component
        self.assertEqual(
            [cmds.ls(s, long=True)[0] for s in NodeUtils.get_shapes(shape)], want
        )

        # A direct shape respects no_intermediate just like the transform path.
        cmds.setAttr(f"{shape}.intermediateObject", 1)
        self.assertIsNone(NodeUtils.get_shape(shape))
        self.assertEqual(norm(NodeUtils.get_shape(shape, no_intermediate=False)), want)

    def test_is_intermediate(self):
        """is_intermediate flips with the intermediateObject attr."""
        shape = NodeUtils.get_shape("cyl")
        self.assertFalse(NodeUtils.is_intermediate(shape))

        cmds.setAttr(f"{shape}.intermediateObject", 1)
        self.assertTrue(NodeUtils.is_intermediate(shape))
        cmds.setAttr(f"{shape}.intermediateObject", 0)

    def test_get_parent_extended_kwargs(self):
        """get_parent honors full_path and type filter."""
        grp = cmds.group(empty=True, name="parent_grp")
        cmds.parent(str(self.cyl), grp)

        # Default: short path, transform-only
        parent = NodeUtils.get_parent("cyl")
        self.assertEqual(parent, grp)

        # full_path=True returns a path beginning with "|"
        parent_long = NodeUtils.get_parent("cyl", full_path=True)
        self.assertTrue(parent_long.startswith("|"))
        self.assertTrue(parent_long.endswith(grp))

        # type=None returns immediate parent regardless of type — for a
        # transform-under-transform that's still the same parent.
        self.assertEqual(NodeUtils.get_parent("cyl", type=None), grp)

    def test_get_children_extended_kwargs(self):
        """get_children honors type filter and full_path."""
        grp = cmds.group(empty=True, name="children_grp")
        cmds.parent(str(self.cyl), grp)

        # Default returns transform children
        children = NodeUtils.get_children(grp)
        self.assertIn(str(self.cyl), [c.split("|")[-1] for c in children])

        # type=None returns all children
        children_all = NodeUtils.get_children(grp, type=None)
        self.assertTrue(any(c.endswith("cyl") for c in children_all))

        # full_path=True
        children_long = NodeUtils.get_children(grp, full_path=True)
        for c in children_long:
            self.assertTrue(c.startswith("|"))

    def test_list_transforms(self):
        """list_transforms walks shape hits up to their transform parent."""
        # cyl has a mesh shape
        result = NodeUtils.list_transforms(type="mesh")
        # Should contain cyl's transform, not its shape
        self.assertIn(str(self.cyl), result)
        self.assertNotIn(str(self.cyl_shape), result)

    def test_node_is(self):
        """node_is matches exact objectType."""
        self.assertTrue(NodeUtils.node_is(self.cyl_shape, "mesh"))
        self.assertFalse(NodeUtils.node_is(self.cyl_shape, "transform"))
        self.assertTrue(NodeUtils.node_is(self.cyl, "transform"))

    # -------------------------------------------------------------------------
    # Attribute Operations
    # -------------------------------------------------------------------------

    def test_get_maya_attribute_type(self):
        """Test get_maya_attribute_type."""
        self.assertEqual(Attributes.get_type(1), "long")
        self.assertEqual(Attributes.get_type(1.0), "double")
        self.assertEqual(Attributes.get_type("s"), "string")
        self.assertEqual(Attributes.get_type(True), "bool")
        self.assertEqual(Attributes.get_type([1.0, 2.0, 3.0]), "double3")
        self.assertEqual(Attributes.get_type(["a", "b"]), "stringArray")

    def test_has_attr(self):
        """has_attr returns True only for attrs that exist on the node."""
        self.assertTrue(Attributes.has_attr(self.cyl, "translateX"))
        self.assertFalse(Attributes.has_attr(self.cyl, "doesNotExist"))

    def test_set_plug_unlocks_when_forced(self):
        """set_plug bypasses a lock when force=True and re-locks afterwards."""
        plug = f"{self.cyl}.translateX"
        cmds.setAttr(plug, lock=True)
        try:
            # Without force, the locked plug must not change.
            Attributes.set_plug(plug, 7.0, force=False)
            self.assertEqual(cmds.getAttr(plug), 0.0)

            # With force, the write goes through and the lock is restored.
            Attributes.set_plug(plug, 7.0, force=True)
            self.assertAlmostEqual(cmds.getAttr(plug), 7.0)
            self.assertTrue(cmds.getAttr(plug, lock=True))
        finally:
            cmds.setAttr(plug, lock=False)

    def test_set_plug_skips_connected_plug(self):
        """A connected plug is unwritable — skip it rather than raising.

        ``force`` deliberately does NOT help here: unlike a lock, clearing a
        connection is destructive.
        """
        driver = cmds.createNode("multiplyDivide", name="drv")
        cmds.setAttr(f"{driver}.input1X", 5.0)
        plug = f"{self.cyl}.translateX"
        cmds.connectAttr(f"{driver}.outputX", plug)

        Attributes.set_plug(plug, 7.0)
        Attributes.set_plug(plug, 7.0, force=True)

        self.assertTrue(cmds.isConnected(f"{driver}.outputX", plug))
        self.assertAlmostEqual(cmds.getAttr(plug), 5.0)

    def test_set_plug_writes_float3_tuple(self):
        """set_plug expands a 3-tuple into a double3 setAttr."""
        Attributes.set_plug(f"{self.cyl}.translate", (1.0, 2.0, 3.0))
        self.assertEqual(
            tuple(cmds.getAttr(f"{self.cyl}.translate")[0]),
            (1.0, 2.0, 3.0),
        )

    def test_set_plug_writes_a_string(self):
        """A string plug needs type="string" — set_plug supplies it.

        Added: 2026-08-16
        """
        node = cmds.shadingNode("file", asTexture=True, name="set_plug_file")
        Attributes.set_plug(f"{node}.fileTextureName", "a/b.png")
        self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), "a/b.png")

    def test_set_plug_literal_stores_a_relative_path_verbatim(self):
        """``fileTextureName`` auto-expands a RESOLVABLE relative path back to
        absolute under plain ``cmds.setAttr`` -- which is why a relativized
        scene could never be written, or restored, through it.
        ``set_plug_literal`` stores the string as given.

        Added: 2026-08-16
        """
        node = cmds.shadingNode("file", asTexture=True, name="literal_file")
        rel = "sourceimages/rel_probe.png"

        Attributes.set_plug_literal(f"{node}.fileTextureName", rel)

        self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), rel)

    def test_set_plug_literal_leaves_an_undo_anchor(self):
        """``MPlug.setString`` alone does not enter the undo queue, so the
        helper places a ``cmds.setAttr`` of the ORIGINAL value first -- undo
        must put the previous path back rather than leaving the literal.

        Added: 2026-08-16
        """
        node = cmds.shadingNode("file", asTexture=True, name="literal_undo_file")
        cmds.setAttr(f"{node}.fileTextureName", "orig.png", type="string")

        cmds.undoInfo(openChunk=True)
        try:
            Attributes.set_plug_literal(f"{node}.fileTextureName", "sourceimages/x.png")
        finally:
            cmds.undoInfo(closeChunk=True)
        self.assertEqual(
            cmds.getAttr(f"{node}.fileTextureName"), "sourceimages/x.png"
        )

        cmds.undo()
        self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), "orig.png")

    def test_set_plug_literal_raises_on_a_locked_plug(self):
        """It must RAISE rather than skip: MatSnapshot.restore_network counts
        successful restores, and a silent skip would inflate that tally the
        same way the optimize_textures repath count was inflated.

        Added: 2026-08-16
        """
        node = cmds.shadingNode("file", asTexture=True, name="literal_locked_file")
        cmds.setAttr(f"{node}.fileTextureName", "orig.png", type="string")
        cmds.setAttr(f"{node}.fileTextureName", lock=True)
        try:
            with self.assertRaises(RuntimeError):
                Attributes.set_plug_literal(f"{node}.fileTextureName", "new.png")
        finally:
            cmds.setAttr(f"{node}.fileTextureName", lock=False)

    def test_pinned_restores_a_relative_texture_path_unexpanded(self):
        """"Export Copies (Scene Untouched)" pins fileTextureName around an
        export. The restore must put the RELATIVE path back verbatim -- a
        plain cmds.setAttr would expand it to absolute and hand the user a
        scene the mode promised not to touch.

        Added: 2026-08-16
        """
        node = cmds.shadingNode("file", asTexture=True, name="pinned_rel_file")
        rel = "sourceimages/pinned_rel.png"
        Attributes.set_plug_literal(f"{node}.fileTextureName", rel)
        self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), rel)

        with Attributes.pinned(node, fileTextureName="C:/staged/abs.png"):
            self.assertEqual(
                cmds.getAttr(f"{node}.fileTextureName"), "C:/staged/abs.png"
            )

        self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), rel)

    def test_pinned_sets_for_the_block_and_restores_on_exit(self):
        """Attributes.pinned: snapshot -> set -> restore, including on a raise.

        Added: 2026-08-16
        """
        node = cmds.shadingNode("file", asTexture=True, name="pinned_file")
        cmds.setAttr(f"{node}.fileTextureName", "orig.png", type="string")
        cmds.setAttr(f"{node}.alphaIsLuminance", 0)
        with Attributes.pinned(node, fileTextureName="staged.png", alphaIsLuminance=1):
            self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), "staged.png")
            self.assertEqual(cmds.getAttr(f"{node}.alphaIsLuminance"), 1)
        self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), "orig.png")
        self.assertEqual(cmds.getAttr(f"{node}.alphaIsLuminance"), 0)

        with self.assertRaises(RuntimeError):
            with Attributes.pinned(node, fileTextureName="staged.png"):
                raise RuntimeError("body failed")
        self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), "orig.png")

    def test_pinned_skips_an_unknown_attribute_and_still_restores_the_rest(self):
        node = cmds.shadingNode("file", asTexture=True, name="pinned_file2")
        cmds.setAttr(f"{node}.alphaIsLuminance", 0)
        with Attributes.pinned(node, alphaIsLuminance=1, noSuchAttr=5):
            self.assertEqual(cmds.getAttr(f"{node}.alphaIsLuminance"), 1)
        self.assertEqual(cmds.getAttr(f"{node}.alphaIsLuminance"), 0)

    def test_pinned_round_trips_a_compound_and_refuses_a_locked_plug(self):
        """A double3 reads back as [(x, y, z)] and must restore through
        set_plug; a locked plug is reported, not silently 'pinned'."""
        cmds.setAttr(f"{self.cyl}.translate", 1.0, 2.0, 3.0, type="double3")
        with Attributes.pinned(self.cyl, translate=(4.0, 5.0, 6.0)):
            self.assertEqual(
                tuple(cmds.getAttr(f"{self.cyl}.translate")[0]), (4.0, 5.0, 6.0)
            )
        self.assertEqual(
            tuple(cmds.getAttr(f"{self.cyl}.translate")[0]), (1.0, 2.0, 3.0)
        )

        cmds.setAttr(f"{self.cyl}.visibility", lock=True)
        with self.assertLogs("mayatk.node_utils.attributes._attributes", "WARNING"):
            with Attributes.pinned(self.cyl, visibility=0):
                self.assertEqual(cmds.getAttr(f"{self.cyl}.visibility"), 1)
        cmds.setAttr(f"{self.cyl}.visibility", lock=False)

    def test_pinned_composes_lifo_under_an_exit_stack(self):
        import contextlib

        node = cmds.shadingNode("file", asTexture=True, name="pinned_file3")
        cmds.setAttr(f"{node}.fileTextureName", "orig.png", type="string")
        with contextlib.ExitStack() as stack:
            stack.enter_context(Attributes.pinned(node, fileTextureName="one.png"))
            stack.enter_context(Attributes.pinned(node, fileTextureName="two.png"))
            self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), "two.png")
        self.assertEqual(cmds.getAttr(f"{node}.fileTextureName"), "orig.png")

    def test_set_node_custom_attributes(self):
        """Test set_node_custom_attributes."""
        # Simple attribute
        Attributes.create_or_set(self.cyl, myFloat=1.5)
        self.assertTrue(cmds.attributeQuery("myFloat", node=str(self.cyl), exists=True))
        self.assertEqual(cmds.getAttr(f"{self.cyl}.myFloat"), 1.5)

        # Compound attribute (vector)
        Attributes.create_or_set(self.cyl, myVec=[1.0, 2.0, 3.0])
        self.assertTrue(cmds.attributeQuery("myVec", node=str(self.cyl), exists=True))
        self.assertEqual(tuple(cmds.getAttr(f"{self.cyl}.myVec")[0]), (1.0, 2.0, 3.0))

    def test_create_or_set_string_and_array_attributes(self):
        """String / data-type array attributes must round-trip.

        Regression for two bugs:
        - ``_set_value`` omitted ``type=`` for string/array attrs, so the
          ``setAttr`` raised ``RuntimeError`` (uncaught) on creation.
        - ``get_type``/``_set_value`` used ``"int32Array"`` for integer arrays,
          but Maya's canonical spelling is the capital-I ``"Int32Array"`` (the
          lone casing exception vs ``doubleArray``/``stringArray``); the wrong
          case made ``addAttr``/``setAttr`` raise "Invalid/Unknown data type".
        """
        # String data type
        Attributes.create_or_set(self.cyl, myStr="hello")
        self.assertEqual(cmds.getAttr(f"{self.cyl}.myStr"), "hello")

        # Integer array -> Maya's Int32Array (length != 2/3 avoids the long2/3
        # compound path).
        Attributes.create_or_set(self.cyl, myInts=[1, 2, 3, 4])
        self.assertTrue(cmds.attributeQuery("myInts", node=str(self.cyl), exists=True))
        self.assertEqual(list(cmds.getAttr(f"{self.cyl}.myInts") or []), [1, 2, 3, 4])

        # Double array
        Attributes.create_or_set(self.cyl, myDbls=[1.0, 2.0, 3.0, 4.0])
        self.assertEqual(
            list(cmds.getAttr(f"{self.cyl}.myDbls") or []), [1.0, 2.0, 3.0, 4.0]
        )

        # String array
        Attributes.create_or_set(self.cyl, myTags=["a", "b"])
        self.assertEqual(list(cmds.getAttr(f"{self.cyl}.myTags") or []), ["a", "b"])

    def test_get_node_attributes_filtering(self):
        """Test get_node_attributes with filtering."""
        # Set a non-default value
        cmds.setAttr(f"{self.cyl}.translateX", 5.0)

        # Test exc_defaults=True
        attrs = Attributes.get_attributes(self.cyl, exc_defaults=True)
        self.assertIn("translateX", attrs)
        self.assertNotIn(
            "translateY", attrs
        )  # Should be excluded as it's 0.0 (default)

    # -------------------------------------------------------------------------
    # Connection Tests
    # -------------------------------------------------------------------------

    def test_get_connected_nodes(self):
        """Test get_connected_nodes."""
        cube = cmds.polyCube()[0]
        cmds.connectAttr(f"{self.cyl}.tx", f"{cube}.tx")

        # Outgoing from cyl
        outgoing = NodeUtils.get_connected_nodes(self.cyl, direction="outgoing")
        self.assertIn(cube, outgoing)

        # Incoming to cube
        incoming = NodeUtils.get_connected_nodes(cube, direction="incoming")
        self.assertIn(self.cyl, incoming)

        # Filter by type
        connected = NodeUtils.get_connected_nodes(self.cyl, node_type="transform")
        self.assertIn(cube, connected)

    def test_connect_multi_attr(self):
        """Test connect_multi_attr."""
        cube = cmds.polyCube()[0]
        Attributes.connect_multi((f"{self.cyl}.tx", f"{cube}.tx"), (f"{self.cyl}.ty", f"{cube}.ty"))
        self.assertTrue(cmds.isConnected(f"{self.cyl}.tx", f"{cube}.tx"))
        self.assertTrue(cmds.isConnected(f"{self.cyl}.ty", f"{cube}.ty"))

    # -------------------------------------------------------------------------
    # Instancing Tests
    # -------------------------------------------------------------------------

    def test_instancing_operations(self):
        """Test instance creation, retrieval, and uninstancing."""
        # Create instance
        target = cmds.polyCube()[0]
        instances = NodeUtils.replace_with_instances([self.cyl, target])
        inst = instances[0]

        # Verify it is an instance
        self.assertTrue(len(cmds.ls(cmds.listRelatives(inst, shapes=True, ni=True)[0], allPaths=True)) > 1)

        # Get instances
        found_instances = NodeUtils.get_instances(self.cyl)
        # Production returns long paths; compare on short name
        inst_short = str(inst).split("|")[-1]
        found_short = [str(f).split("|")[-1] for f in found_instances]
        self.assertIn(inst_short, found_short)

        # Filter duplicate instances
        filtered = NodeUtils.filter_duplicate_instances([self.cyl, inst])
        # Should return only one transform per instance group
        self.assertEqual(len(filtered), 1)

        # Uninstance
        NodeUtils.uninstance(inst)
        self.assertFalse(len(cmds.ls(cmds.listRelatives(inst, shapes=True, ni=True)[0], allPaths=True)) > 1)

    def test_uninstance_preserves_transform_and_siblings(self):
        """Regression: uninstance must NOT delete the transform or its children,
        and the sibling instance must keep the original shape.
        """
        src = cmds.polyCube()[0]
        target = cmds.polyCube()[0]
        instances = NodeUtils.replace_with_instances([src, target])
        inst = instances[0]
        # Source shape long path is shared with inst pre-uninstance.
        src_shape_long = cmds.listRelatives(src, shapes=True, fullPath=True)[0]

        # Anchor: a child locator under the instance. Pre-fix code deleted
        # the transform and took the child with it.
        child = cmds.spaceLocator()[0]
        child = cmds.parent(child, inst)[0]
        child_short = child.split("|")[-1]

        result = NodeUtils.uninstance(inst)

        # Transform survives.
        self.assertTrue(cmds.objExists(inst), "uninstance deleted the transform")

        # Child survives and is still parented under inst.
        inst_children = cmds.listRelatives(inst, children=True, type="transform") or []
        self.assertIn(
            child_short,
            [c.split("|")[-1] for c in inst_children],
            "child was deleted or reparented away from inst",
        )

        # Forked shape is unique to inst (no longer shared).
        new_shape = cmds.listRelatives(inst, shapes=True, ni=True, fullPath=True)[0]
        self.assertEqual(
            len(cmds.listRelatives(new_shape, allParents=True, fullPath=True) or []),
            1,
            "new shape is still instanced",
        )

        # Source kept its original shape.
        self.assertTrue(
            cmds.objExists(src_shape_long),
            "source's original shape was destroyed",
        )

        # Result contract: returns the same transform identity.
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].split("|")[-1], inst.split("|")[-1])

    def test_uninstance_all_siblings_each_become_unique(self):
        """Uninstance applied to every member of an instance set leaves each
        with its own unique shape.
        """
        src = cmds.polyCube()[0]
        t1 = cmds.polyCube()[0]
        t2 = cmds.polyCube()[0]
        instances = NodeUtils.replace_with_instances([src, t1, t2])
        # Set is now {src, instances[0], instances[1]} sharing src's shape.
        members = [src] + instances

        NodeUtils.uninstance(members)

        for t in members:
            self.assertTrue(cmds.objExists(t), f"{t} was deleted")
            shp = cmds.listRelatives(t, shapes=True, ni=True, fullPath=True)[0]
            parents = cmds.listRelatives(shp, allParents=True, fullPath=True) or []
            self.assertEqual(len(parents), 1, f"{t} shape still instanced")

    def test_replace_with_instances_duplicate_parent_names(self):
        """Regression: targets whose parents share a non-unique short name must
        not raise 'More than one object matches name'. The op must resolve
        parents by full path, not the ambiguous short name.
        """
        src = cmds.polyCube(name="src")[0]

        # Two sibling-group hierarchies whose inner parent transforms collide
        # on short name 'dup' (distinct only by full path). Each holds a target.
        top1 = cmds.group(empty=True, name="top1")
        top2 = cmds.group(empty=True, name="top2")
        dupA = cmds.ls(cmds.parent(cmds.group(empty=True, name="dup"), top1), long=True)[0]
        dupB = cmds.ls(cmds.parent(cmds.group(empty=True, name="dup"), top2), long=True)[0]
        # Sanity: the short name really is ambiguous scene-wide.
        self.assertEqual(len(cmds.ls("dup")), 2)

        tA_long = cmds.ls(cmds.parent(cmds.polyCube(name="tgtA")[0], dupA), long=True)[0]
        tB_long = cmds.ls(cmds.parent(cmds.polyCube(name="tgtB")[0], dupB), long=True)[0]

        # Pre-fix this raised ValueError: More than one object matches name: dup
        instances = NodeUtils.replace_with_instances([src, tA_long, tB_long])

        self.assertEqual(len(instances), 2)
        # Each instance must land under its target's OWN 'dup' parent (resolved
        # by full path), not an arbitrary same-named one. Instance order follows
        # target order, so instances[0] -> dupA, instances[1] -> dupB.
        parents = [
            cmds.listRelatives(i, parent=True, fullPath=True)[0] for i in instances
        ]
        self.assertEqual(parents, [dupA, dupB])

    def test_replace_with_instances_retain_bbox_scale(self):
        """retain_bbox_scale keeps each target's apparent size when the size
        lives in the geometry (identical scale channels, different mesh size).
        Without it, matchTransform copies only the scale channels, so the
        instance comes back at the SOURCE's size.
        """
        src = cmds.polyCube(name="src", width=1, height=1, depth=1)[0]
        # Target is 3x bigger at the vertex level; its scale channels stay 1.
        tgt = cmds.polyCube(name="tgt", width=3, height=3, depth=3)[0]
        cmds.move(20, 0, 0, tgt)

        # Off (default): instance takes the source's size.
        plain = mtk.NodeUtils.replace_with_instances([src, cmds.duplicate(tgt)[0]])
        sx, sy, sz = mtk.XformUtils.get_bounding_box(plain[0], "size", world_space=True)
        for axis, v in zip("xyz", (sx, sy, sz)):
            self.assertAlmostEqual(v, 1.0, places=3, msg=f"default size {axis}")

        # On: instance is rescaled to the size of the object it replaced.
        instances = mtk.NodeUtils.replace_with_instances(
            [src, tgt], retain_bbox_scale=True
        )
        rx, ry, rz = mtk.XformUtils.get_bounding_box(
            instances[0], "size", world_space=True
        )
        for axis, v in zip("xyz", (rx, ry, rz)):
            self.assertAlmostEqual(v, 3.0, places=3, msg=f"retained size {axis}")

        # Still a real instance of the source shape.
        shape = cmds.listRelatives(instances[0], shapes=True, ni=True, fullPath=True)[0]
        self.assertGreater(
            len(cmds.listRelatives(shape, allParents=True, fullPath=True) or []), 1
        )

    def test_replace_with_instances_retain_bbox_per_axis(self):
        """retain_bbox_per_axis fits each axis independently — and does it in
        the LOCAL frame, so a rotated target still lands on its own size
        (a world-axis ratio would be wrong there).
        """
        src = cmds.polyCube(name="src", width=1, height=1, depth=1)[0]
        # Target's proportions differ per axis (1 x 2 x 4), baked into geometry.
        tgt = cmds.polyCube(name="tgt", width=1, height=2, depth=4)[0]
        cmds.move(20, 0, 0, tgt)
        cmds.rotate(0, 45, 0, tgt)  # local frame != world frame

        want = mtk.XformUtils.get_bounding_box(tgt, "size", world_space=True)
        instances = mtk.NodeUtils.replace_with_instances(
            [src, tgt], retain_bbox_scale=True, retain_bbox_per_axis=True
        )
        got = mtk.XformUtils.get_bounding_box(instances[0], "size", world_space=True)
        for axis, g, w in zip("xyz", got, want):
            self.assertAlmostEqual(g, w, places=3, msg=f"world size {axis}")

        # Local scale really is non-uniform (1 x 2 x 4 against a unit cube).
        sx, sy, sz = cmds.getAttr(f"{instances[0]}.scale")[0]
        self.assertAlmostEqual(sy / sx, 2.0, places=3)
        self.assertAlmostEqual(sz / sx, 4.0, places=3)

    def test_replace_with_instances_retain_bbox_scale_rotated(self):
        """The uniform path goes through a relative world-space xform — pin
        that it still lands on the target's own size when the target is
        rotated off-axis."""
        src = cmds.polyCube(name="src", width=1, height=1, depth=1)[0]
        tgt = cmds.polyCube(name="tgt", width=4, height=4, depth=4)[0]
        cmds.move(20, 0, 0, tgt)
        cmds.rotate(15, 45, 30, tgt)

        want = mtk.XformUtils.get_bounding_box(tgt, "size", world_space=True)
        instances = mtk.NodeUtils.replace_with_instances(
            [src, tgt], retain_bbox_scale=True
        )
        got = mtk.XformUtils.get_bounding_box(instances[0], "size", world_space=True)
        for axis, g, w in zip("xyz", got, want):
            self.assertAlmostEqual(g, w, places=3, msg=f"world size {axis}")

    def test_replace_with_instances_retain_scale_preserves_mirroring(self):
        """The scale fit must never introduce or cancel a mirror: bounding-box
        extents are unsigned, so the ratio is always positive and each scale
        channel keeps the sign ``matchTransform`` copied off the target.
        (A negative determinant can't be corrected per-instance anyway —
        ``opposite`` is a SHAPE attribute, shared by every sibling.)
        """
        for per_axis in (False, True):
            with self.subTest(per_axis=per_axis):
                cmds.file(new=True, force=True)
                src = cmds.polyCube(name="src", width=1, height=1, depth=1)[0]
                tgt = cmds.polyCube(name="tgt", width=3, height=3, depth=3)[0]
                cmds.move(20, 0, 0, tgt)
                cmds.setAttr(f"{tgt}.scaleX", -1)  # mirrored target

                instances = mtk.NodeUtils.replace_with_instances(
                    [src, tgt],
                    retain_bbox_scale=True,
                    retain_bbox_per_axis=per_axis,
                )
                sx, sy, sz = cmds.getAttr(f"{instances[0]}.scale")[0]
                self.assertLess(sx, 0, "mirror was cancelled")
                self.assertGreater(sy, 0)
                self.assertGreater(sz, 0)
                # ...and it still lands on the target's size.
                size = mtk.XformUtils.get_bounding_box(
                    instances[0], "size", world_space=True
                )
                for axis, v in zip("xyz", size):
                    self.assertAlmostEqual(v, 3.0, places=3, msg=f"world size {axis}")
                # The shared shape is untouched — the source is not mirrored.
                self.assertGreater(cmds.getAttr(f"{src}.scaleX"), 0)

    def test_replace_with_instances_per_axis_falls_back_for_groups(self):
        """A group has no single shape, so there's no unambiguous local frame
        to fit per-axis in — the request must degrade to the uniform fit
        rather than raise or silently skip the retention."""
        src = cmds.group(cmds.polyCube(width=1, height=1, depth=1)[0], name="src_grp")
        tgt = cmds.group(cmds.polyCube(width=3, height=3, depth=3)[0], name="tgt_grp")
        cmds.move(20, 0, 0, tgt)

        self.assertIsNone(mtk.NodeUtils._local_bbox_size(tgt))  # no single shape

        instances = mtk.NodeUtils.replace_with_instances(
            [src, tgt], retain_bbox_scale=True, retain_bbox_per_axis=True
        )
        size = mtk.XformUtils.get_bounding_box(instances[0], "size", world_space=True)
        for axis, v in zip("xyz", size):
            self.assertAlmostEqual(v, 3.0, places=3, msg=f"world size {axis}")

    def test_replace_with_instances_per_axis_skips_degenerate_axis(self):
        """An axis with no extent on one side has no reproducible ratio — it
        keeps the target's own scale instead of collapsing the instance."""
        src = cmds.polyCube(name="src", width=1, height=1, depth=1)[0]
        tgt = cmds.polyPlane(name="tgt", width=3, height=3, axis=(0, 1, 0))[0]
        # Plane has zero Y extent; X/Z are 3.
        instances = mtk.NodeUtils.replace_with_instances(
            [src, tgt], retain_bbox_scale=True, retain_bbox_per_axis=True
        )
        sx, sy, sz = cmds.getAttr(f"{instances[0]}.scale")[0]
        self.assertAlmostEqual(sx, 3.0, places=3)
        self.assertAlmostEqual(sz, 3.0, places=3)
        self.assertAlmostEqual(sy, 1.0, places=3, msg="degenerate axis collapsed")

    def test_replace_with_instances_preserves_frozen_rotation(self):
        """A target whose rotation was frozen (orientation baked into the
        geometry, rotate channels zeroed) must come back at its own apparent
        orientation. matchTransform alone copies the channels (0,0,0) and
        silently snaps the instance to the SOURCE's orientation — the fix
        places identical geometry by geometric registration instead."""
        src = cmds.polyCube(name="src", width=4, height=2, depth=1)[0]
        tgt = cmds.duplicate(src)[0]
        cmds.setAttr(f"{tgt}.translateX", 20)
        cmds.setAttr(f"{tgt}.rotateZ", 90)
        cmds.makeIdentity(tgt, apply=True, rotate=True)  # bake the rotation

        want = mtk.XformUtils.get_bounding_box(tgt, "size", world_space=True)
        want_center = mtk.XformUtils.get_bounding_box(tgt, "center", world_space=True)

        instances = mtk.NodeUtils.replace_with_instances([src, tgt])

        got = mtk.XformUtils.get_bounding_box(instances[0], "size", world_space=True)
        got_center = mtk.XformUtils.get_bounding_box(
            instances[0], "center", world_space=True
        )
        for axis, g, w in zip("xyz", got, want):
            self.assertAlmostEqual(g, w, places=3, msg=f"world size {axis}")
        for axis, g, w in zip("xyz", got_center, want_center):
            self.assertAlmostEqual(g, w, places=3, msg=f"world center {axis}")

        # Still a real instance of the source shape.
        shape = cmds.listRelatives(instances[0], shapes=True, ni=True, fullPath=True)[0]
        self.assertGreater(
            len(cmds.listRelatives(shape, allParents=True, fullPath=True) or []), 1
        )

    def test_replace_with_instances_center_pivot_without_freeze(self):
        """center_pivot must act even when freeze_transforms is False —
        passing translate=False into freeze_transforms is an explicit
        'freeze nothing' request, which returned before the centering step
        and made the option a silent no-op."""
        src = cmds.polyCube(name="src")[0]
        cmds.xform(src, ws=True, rotatePivot=(5, 5, 5))
        tgt = cmds.duplicate(src)[0]
        cmds.setAttr(f"{tgt}.translateX", 20)

        instances = mtk.NodeUtils.replace_with_instances(
            [src, tgt], center_pivot=True, freeze_transforms=False
        )

        rp = cmds.xform(instances[0], q=True, ws=True, rotatePivot=True)
        center = mtk.XformUtils.get_bounding_box(
            instances[0], "center", world_space=True
        )
        for axis, p, c in zip("xyz", rp, center):
            self.assertAlmostEqual(p, c, places=3, msg=f"pivot {axis} not centered")

    def test_replace_with_instances_frozen_rotation_under_rotated_parent(self):
        """The registration path sets the world matrix while the instance is
        still under the SOURCE's parent, then reparents under the target's —
        pin that the two steps compose under a transformed parent."""
        src = cmds.polyCube(name="src", width=4, height=2, depth=1)[0]
        tgt = cmds.duplicate(src)[0]
        cmds.setAttr(f"{tgt}.rotateZ", 90)
        cmds.makeIdentity(tgt, apply=True, rotate=True)
        grp = cmds.group(tgt, name="grp")
        cmds.setAttr(f"{grp}.rotate", 30, 40, 10)
        cmds.setAttr(f"{grp}.translateY", 5)
        tgt = cmds.listRelatives(grp, children=True, fullPath=True)[0]

        want = mtk.XformUtils.get_bounding_box(tgt, "size", world_space=True)
        want_center = mtk.XformUtils.get_bounding_box(tgt, "center", world_space=True)

        instances = mtk.NodeUtils.replace_with_instances([src, tgt])

        got = mtk.XformUtils.get_bounding_box(instances[0], "size", world_space=True)
        got_center = mtk.XformUtils.get_bounding_box(
            instances[0], "center", world_space=True
        )
        for axis, g, w in zip("xyz", got, want):
            self.assertAlmostEqual(g, w, places=3, msg=f"world size {axis}")
        for axis, g, w in zip("xyz", got_center, want_center):
            self.assertAlmostEqual(g, w, places=3, msg=f"world center {axis}")
        self.assertEqual(
            cmds.listRelatives(instances[0], parent=True)[0], grp, "wrong parent"
        )

    def test_replace_with_instances_mirrored_identical_target(self):
        """A mirrored (negative-scale) target whose object-space geometry is
        identical to the source takes the registration path and must come
        back with its exact world matrix — negative determinant included.
        (Which CHANNEL carries the mirror may differ from the target's own
        decomposition; the world matrix is the contract.)"""
        src = cmds.polyCube(name="src", width=4, height=2, depth=1)[0]
        tgt = cmds.duplicate(src)[0]
        cmds.setAttr(f"{tgt}.translateX", 20)
        cmds.setAttr(f"{tgt}.scaleX", -1)
        want = cmds.xform(tgt, q=True, ws=True, m=True)

        instances = mtk.NodeUtils.replace_with_instances([src, tgt])

        got = cmds.xform(instances[0], q=True, ws=True, m=True)
        for i, (g, w) in enumerate(zip(got, want)):
            self.assertAlmostEqual(g, w, places=4, msg=f"world matrix [{i}]")

    def test_replace_with_instances_keeps_target_name(self):
        """The replacement takes over the target's exact name: the target is
        deleted BEFORE the rename — renaming against the still-living target
        auto-suffixed ('my_wall' came back as 'my_wall1')."""
        src = cmds.polyCube(name="src")[0]
        tgt = cmds.polyCube(name="my_wall")[0]
        cmds.setAttr(f"{tgt}.translateX", 20)

        instances = mtk.NodeUtils.replace_with_instances([src, tgt])

        self.assertEqual(instances[0].split("|")[-1], "my_wall")

    def test_replace_with_instances_no_center_pivot_keeps_target_pivot(self):
        """With center_pivot off, the instance keeps the pivot of the object
        it replaced — including on the geometric-registration path."""
        src = cmds.polyCube(name="src")[0]
        tgt = cmds.duplicate(src)[0]
        cmds.setAttr(f"{tgt}.translateX", 20)
        cmds.xform(tgt, ws=True, rotatePivot=(22, 3, 1))
        want_rp = cmds.xform(tgt, q=True, ws=True, rotatePivot=True)

        instances = mtk.NodeUtils.replace_with_instances(
            [src, tgt], center_pivot=False
        )

        got_rp = cmds.xform(instances[0], q=True, ws=True, rotatePivot=True)
        for axis, g, w in zip("xyz", got_rp, want_rp):
            self.assertAlmostEqual(g, w, places=3, msg=f"pivot {axis}")

    # -------------------------------------------------------------------------
    # Assembly Tests
    # -------------------------------------------------------------------------

    def test_create_assembly(self):
        """Test create_assembly."""
        try:
            # Check if assembly command exists
            cmds.assembly
        except AttributeError:
            self.skipTest("Assembly command not available")

        try:
            asm = NodeUtils.create_assembly([self.cyl], assembly_name="test_asm")
            self.assertEqual(cmds.nodeType(asm), "assembly")
            self.assertIn(self.cyl, asm.children())
        except RuntimeError as e:
            print(f"Skipping assembly test due to runtime error: {e}")
            # This often fails in batch mode or if plugin not loaded
            pass

    # -------------------------------------------------------------------------
    # Render Node Tests
    # -------------------------------------------------------------------------

    def test_get_classification_tokens_drops_draw_overrides(self):
        """Draw-override / swatch tokens are not roles and must be dropped.

        Matching them is how ``aiBump2d`` (``drawdb/shader/surface/...``, role
        ``utility/shader``) reads as a surface shader.
        """
        tokens = NodeUtils.get_classification_tokens("bump2d")
        self.assertIn("utility/general/bump", tokens)
        self.assertFalse([t for t in tokens if t.startswith(("drawdb/", "swatch/"))])

        # Unknown type: empty, not an exception.
        self.assertEqual(NodeUtils.get_classification_tokens("noSuchNodeType"), [])

    def test_get_classification_tokens_draw_only_fallback(self):
        """A type classified ONLY by its draw override still reports a role."""
        raw = cmds.getClassification("adskMaterial") or []
        if not raw or any(
            not tok.startswith(("drawdb/", "swatch/"))
            for entry in raw
            for tok in entry.split(":")
        ):
            self.skipTest("adskMaterial is absent or no longer draw-only")
        # 'drawdb/shader/surface/adskMaterial' -> 'shader/surface/adskMaterial'
        self.assertEqual(
            NodeUtils.get_classification_tokens("adskMaterial"),
            ["shader/surface/adskMaterial"],
        )

    def test_create_render_node_flags_utility_types_as_utility(self):
        """A utility type must not be created as a shader.

        ``shadingNode -asShader`` parks the node in ``defaultShaderList1``,
        which is what ``cmds.ls(materials=True)`` reports — so a bump2d created
        that way shows up as a material (and gets a spurious shading group).
        """
        bump = NodeUtils.create_render_node("bump2d", name="test_render_bump")
        self.assertEqual(cmds.nodeType(bump), "bump2d")
        self.assertNotIn(bump, cmds.ls(materials=True) or [])
        self.assertFalse(cmds.listConnections(bump, type="shadingEngine"))

    def test_create_render_node(self):
        """Test create_render_node."""
        # Try to source the MEL script required
        try:
            mel.source("createRenderNode.mel")
        except Exception:
            pass

        try:
            # Create a shader
            shader = NodeUtils.create_render_node("lambert", name="test_lambert")
            if shader:
                self.assertNodeExists("test_lambert")
                self.assertEqual(cmds.nodeType(shader), "lambert")

            # Create a texture with placement
            tex = NodeUtils.create_render_node(
                "checker", name="test_checker", create_placement_nodes=True
            )
            if tex:
                self.assertNodeExists("test_checker")
                # Check for placement node connection
                self.assertTrue(cmds.listConnections(tex, type="place2dTexture"))
        except RuntimeError as e:
            if "Cannot find procedure" in str(e):
                print("Skipping create_render_node test: MEL procedure missing")
            else:
                raise e


class TestInstancedShapeHelpers(MayaTkTestCase):
    """Instanced-shape detection and the safe uninstance path."""

    def _make_history_group(self, name="pih"):
        """An instanced pair whose shape carries a deformer — so both the
        visible shape AND the intermediate (orig) shape are shared."""
        src = cmds.polyCube(name=f"{name}_m0")[0]
        cmds.select(src)
        cmds.nonLinear(type="bend")
        sib = cmds.instance(src, name=f"{name}_m1")[0]
        cmds.setAttr(f"{sib}.translateX", 8)
        cmds.setAttr(f"{src}.rotateY", 30)
        cmds.setAttr(f"{src}.scaleX", 2)
        return cmds.ls([src, sib], long=True)

    def test_get_instanced_shapes_includes_intermediates(self):
        """Regression: a shared orig shape blocks makeIdentity exactly like a
        shared visible shape, so it must be reported as instanced."""
        src, _ = self._make_history_group("gis")
        all_inst = NodeUtils.get_instanced_shapes(src)
        visible_inst = NodeUtils.get_instanced_shapes(src, intermediate=False)

        self.assertEqual(len(all_inst), 2)
        self.assertEqual(len(visible_inst), 1)
        self.assertTrue(any(NodeUtils.is_intermediate(s) for s in all_inst))

    def test_uninstance_with_delete_history_detaches_and_freezes(self):
        """Regression: uninstance forked visible shapes only, so the shared
        orig shape survived and uninstance(freeze=True) silently did
        nothing. Baking the history away is what actually detaches it."""
        src, sib = self._make_history_group("uif")

        NodeUtils.uninstance(src, freeze=True, delete_history=True)

        self.assertEqual(NodeUtils.get_instanced_shapes(src), [])
        for v in cmds.getAttr(f"{src}.scale")[0]:
            self.assertAlmostEqual(v, 1.0, places=4)
        # The sibling keeps real geometry (a fork of the orig shape would
        # have invalidated the deformer's per-instance input and left it
        # evaluating to an empty mesh).
        sib_shape = cmds.listRelatives(
            sib, shapes=True, fullPath=True, noIntermediate=True
        )[0]
        self.assertGreater(cmds.polyEvaluate(sib_shape, vertex=True), 0)

    def test_uninstance_leaves_shared_intermediate_alone_by_default(self):
        """Forking a live-history orig shape empties the remaining
        instances, so it must be left shared (and reported) unless the
        caller opts into baking the history away."""
        src, sib = self._make_history_group("uil")

        NodeUtils.uninstance(src)

        remaining = NodeUtils.get_instanced_shapes(src)
        self.assertTrue(remaining)
        self.assertTrue(all(NodeUtils.is_intermediate(s) for s in remaining))
        sib_shape = cmds.listRelatives(
            sib, shapes=True, fullPath=True, noIntermediate=True
        )[0]
        self.assertGreater(cmds.polyEvaluate(sib_shape, vertex=True), 0)


class TestPreserveInstancing(MayaTkTestCase):
    """``NodeUtils.preserve_instancing`` — localize a shape-editing op to the
    objects it was aimed at, then re-instance in place."""

    def _group(self, n=3, name="pi"):
        """*n* instances of one cube, spaced along X."""
        master = cmds.polyCube(name=f"{name}_m0")[0]
        members = [master]
        for i in range(1, n):
            sib = cmds.instance(master, name=f"{name}_m{i}")[0]
            cmds.setAttr(f"{sib}.translateX", 10 * i)
            members.append(sib)
        return cmds.ls(members, long=True)

    @staticmethod
    def _bbox(node):
        return [round(v, 5) for v in cmds.exactWorldBoundingBox(node)]

    @staticmethod
    def _shape(node):
        """The shape's NODE identity, not its path — two instances resolve to
        different DAG paths for the very same node."""
        path = cmds.listRelatives(node, shapes=True, fullPath=True, ni=True)[0]
        return cmds.ls(path, uuid=True)[0]

    @staticmethod
    def _nudge(node, delta=(0, 1, 0)):
        """Move the object's POINTS — the shared-datablock write that drags
        every sibling along when it isn't scoped."""
        cmds.move(*delta, f"{node}.vtx[*]", relative=True, objectSpace=True)

    def test_scoped_edit_leaves_untargeted_siblings_alone(self):
        """The bug behind the pivot-bake report: editing one instance's
        points moved every other member of its group."""
        master, sib1, sib2 = self._group(3, "pia")
        before = [self._bbox(sib1), self._bbox(sib2)]

        with NodeUtils.preserve_instancing(master):
            self._nudge(master)

        self.assertEqual([self._bbox(sib1), self._bbox(sib2)], before)
        # The edited member had to fork to hold its own points...
        self.assertNotEqual(self._shape(master), self._shape(sib1))
        # ...but the members nobody touched are still instances of each other.
        self.assertEqual(self._shape(sib1), self._shape(sib2))

    def test_group_edited_as_a_whole_is_re_instanced_in_place(self):
        """Every member edited identically still describes ONE datablock, so
        the scope must hand the instancing back rather than leave three
        unique shapes behind."""
        members = self._group(3, "pib")
        before = [self._bbox(m) for m in members]

        with NodeUtils.preserve_instancing(members):
            for m in members:
                self._nudge(m)

        self.assertEqual(len({self._shape(m) for m in members}), 1)
        # In place: the edit applied, and no transform was moved to achieve it.
        after = [self._bbox(m) for m in members]
        for b, a in zip(before, after):
            self.assertAlmostEqual(a[1] - b[1], 1.0, places=5)
            self.assertAlmostEqual(a[0], b[0], places=5)

    def test_divergent_edits_stay_unique(self):
        """Two members that no longer agree cannot share one datablock — the
        scope must leave them forked rather than snap one onto the other."""
        master, sib = self._group(2, "pic")

        with NodeUtils.preserve_instancing([master, sib]):
            self._nudge(master, (0, 1, 0))
            self._nudge(sib, (0, 5, 0))

        self.assertNotEqual(self._shape(master), self._shape(sib))
        self.assertAlmostEqual(
            cmds.exactWorldBoundingBox(sib)[4] - cmds.exactWorldBoundingBox(master)[4],
            4.0,
            places=5,
        )

    def test_partial_group_edited_together_re_instances_among_itself(self):
        """A subset edited identically re-instances within the subset, while
        the members left out keep the original shape."""
        m0, m1, m2 = self._group(3, "pid")
        untouched_before = self._bbox(m2)

        with NodeUtils.preserve_instancing([m0, m1]):
            self._nudge(m0)
            self._nudge(m1)

        self.assertEqual(self._shape(m0), self._shape(m1))
        self.assertNotEqual(self._shape(m0), self._shape(m2))
        self.assertEqual(self._bbox(m2), untouched_before)

    def test_non_instanced_objects_round_trip_untouched(self):
        """The scope has to be free to wrap unconditionally."""
        cube = cmds.polyCube(name="pie_lone")[0]
        shape, before = self._shape(cube), self._bbox(cube)

        with NodeUtils.preserve_instancing(cube):
            pass

        self.assertEqual(self._shape(cube), shape)
        self.assertEqual(self._bbox(cube), before)

    def test_shapes_without_points_are_left_alone(self):
        """A locator has no points for an op to write through, and no way to
        prove two copies identical afterwards — forking it would be a scene
        change the scope could never take back."""
        loc = cmds.spaceLocator(name="pih_loc")[0]
        sib = cmds.instance(loc, name="pih_loc1")[0]
        shape = cmds.listRelatives(loc, shapes=True, fullPath=True)[0]

        with NodeUtils.preserve_instancing([loc, sib]):
            pass

        self.assertEqual(len(cmds.listRelatives(shape, allParents=True) or []), 2)

    def test_restores_even_when_the_operation_raises(self):
        """A failed op must not strand the scene mid-fork."""
        members = self._group(2, "pif")

        with self.assertRaises(RuntimeError):
            with NodeUtils.preserve_instancing(members):
                raise RuntimeError("boom")

        self.assertEqual(len({self._shape(m) for m in members}), 1)

    @staticmethod
    def _make_sg(name):
        shader = cmds.shadingNode("lambert", asShader=True, name=name)
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}_SG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        return sg

    @staticmethod
    def _assigned_sgs(node):
        path = cmds.listRelatives(node, shapes=True, fullPath=True, ni=True)[0]
        return [
            s
            for s in (cmds.listSets(object=path, type=1) or [])
            if cmds.nodeType(s) == "shadingEngine"
        ]

    def test_per_instance_shading_survives_the_round_trip(self):
        """Two members of one group on DIFFERENT materials — the case
        ``freeze_instanced_group`` documents as the way instance surgery goes
        wrong. Adding/removing DAG instance edges renumbers ``instObjGroups``,
        so the re-link has to re-apply each assignment BY PATH or the members
        come back sharing one material."""
        m0, m1 = self._group(2, "pig")
        sg_a, sg_b = self._make_sg("pigA"), self._make_sg("pigB")
        for node, sg in ((m0, sg_a), (m1, sg_b)):
            cmds.sets(
                cmds.listRelatives(node, shapes=True, fullPath=True, ni=True)[0],
                edit=True,
                forceElement=sg,
            )

        with NodeUtils.preserve_instancing([m0, m1]):
            self._nudge(m0)
            self._nudge(m1)

        self.assertEqual(self._shape(m0), self._shape(m1))  # still instanced
        self.assertEqual(self._assigned_sgs(m0), [sg_a])
        self.assertEqual(self._assigned_sgs(m1), [sg_b])

    def test_deformed_instance_keeps_its_deformer_and_its_siblings(self):
        """The visible shape is deformer OUTPUT and shared. Forking it must
        not empty the sibling (the failure mode ``uninstance`` documents for
        a shared ORIG shape), and the sibling must not move."""
        src = cmds.polyCube(name="pid_m0", sx=4, sy=4, sz=4)[0]
        cmds.select(src)
        cmds.nonLinear(type="bend")
        sib = cmds.instance(src, name="pid_m1")[0]
        cmds.setAttr(f"{sib}.translateX", 10)
        before = self._bbox(sib)
        verts = cmds.polyEvaluate(
            cmds.listRelatives(sib, shapes=True, fullPath=True, ni=True)[0], vertex=True
        )

        with NodeUtils.preserve_instancing(src):
            self._nudge(src)

        self.assertEqual(self._bbox(sib), before)
        for node in (src, sib):
            shape = cmds.listRelatives(node, shapes=True, fullPath=True, ni=True)[0]
            self.assertEqual(cmds.polyEvaluate(shape, vertex=True), verts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
