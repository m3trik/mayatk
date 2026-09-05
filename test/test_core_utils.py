# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.core_utils module

Tests for CoreUtils class functionality including:
- Array type detection and conversion
- Decorators (selected, undoable, reparent)
- Attribute handling (unlock, filter)
- Mesh operations (similarity, MFnMesh)
- Parameter mapping
"""

import unittest
import maya.cmds as cmds
import mayatk as mtk
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils.attributes._attributes import Attributes

from base_test import MayaTkTestCase


class TestCoreUtils(MayaTkTestCase):
    """Comprehensive tests for CoreUtils class."""

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
        # Get history node for parameter tests
        self.cyl_hist = cmds.ls(cmds.listHistory(self.cyl), type="polyCylinder")[0]

    def tearDown(self):
        """Clean up test geometry."""
        if cmds.objExists("cyl"):
            cmds.delete("cyl")
        super().tearDown()

    # -------------------------------------------------------------------------
    # Array Type Detection and Conversion Tests
    # -------------------------------------------------------------------------

    def test_get_array_type_with_int(self):
        """Test array type detection for integer values."""
        result = CoreUtils.get_array_type(100)
        self.assertEqual(result, "int")

    def test_get_array_type_with_string(self):
        """Test array type detection for string values."""
        result = CoreUtils.get_array_type("cylShape.vtx[:]")
        self.assertEqual(result, "str")

    def test_get_array_type_with_pymel_vertex_list(self):
        """Test array type detection for PyMEL vertex components."""
        vertices = cmds.ls("cylShape.vtx[:]")
        result = CoreUtils.get_array_type(vertices)
        self.assertEqual(result, "vtx")

    def test_get_array_type_with_edge(self):
        """Test array type detection for edge components."""
        edges = cmds.ls("cylShape.e[:]")
        result = CoreUtils.get_array_type(edges)
        self.assertEqual(result, "e")

    def test_get_array_type_with_face(self):
        """Test array type detection for face components."""
        faces = cmds.ls("cylShape.f[:]")
        result = CoreUtils.get_array_type(faces)
        self.assertEqual(result, "f")

    def test_convert_array_type_string_to_str_list(self):
        """Test converting component string to string list.

        The helper returns shape-prefixed names using fullPath=True
        (intentional — short names would collide when multiple shapes
        share a leaf name across DAG branches). Accept both short and
        full forms; the contract is "ends with the expected shape+comp".
        """
        result = CoreUtils.convert_array_type("cyl.vtx[:2]", "str")
        self.assertEqual(len(result), 1)
        self.assertTrue(
            result[0].endswith("cylShape.vtx[0:2]"),
            f"Expected component on cylShape, got {result[0]}",
        )

    def test_convert_array_type_string_to_str_list_flattened(self):
        """Test converting component string to flattened string list."""
        result = CoreUtils.convert_array_type("cyl.vtx[:2]", "str", flatten=True)
        self.assertEqual(len(result), 3)
        for i, r in enumerate(result):
            self.assertTrue(
                r.endswith(f"cylShape.vtx[{i}]"),
                f"Index {i}: expected suffix cylShape.vtx[{i}], got {r}",
            )

    def test_convert_array_type_string_to_pymel_objects(self):
        """Test converting component string to PyMEL objects."""
        result = CoreUtils.convert_array_type("cyl.vtx[:2]", "obj")
        self.assertEqual(len(result), 1)
        self.assertTrue(
            str(result[0]).endswith("cylShape.vtx[0:2]"),
            f"Expected component on cylShape, got {result[0]}",
        )

    def test_convert_array_type_string_to_pymel_objects_flattened(self):
        """Test converting component string to flattened PyMEL objects."""
        result = CoreUtils.convert_array_type("cyl.vtx[:2]", "obj", flatten=True)
        self.assertEqual(len(result), 3)
        for i, r in enumerate(result):
            self.assertTrue(
                str(r).endswith(f"cylShape.vtx[{i}]"),
                f"Index {i}: expected suffix cylShape.vtx[{i}], got {r}",
            )

    def test_convert_array_type_string_to_int_indices(self):
        """Test converting component string to integer index range."""
        result = CoreUtils.convert_array_type("cyl.vtx[:2]", "int")
        # For single object, it returns a flattened list.
        # Based on current implementation, it returns [0, 2] for range 0:2
        self.assertIsInstance(result, list)
        self.assertEqual(result, [0, 2])

    def test_convert_array_type_string_to_int_indices_flattened(self):
        """Test converting component string to flattened integer indices."""
        result = CoreUtils.convert_array_type("cyl.vtx[:2]", "int", flatten=True)
        # For single object, it returns a list of indices
        self.assertIsInstance(result, list)
        self.assertEqual(result, [0, 1, 2])

    # -------------------------------------------------------------------------
    # Decorator Tests
    # -------------------------------------------------------------------------

    def test_undoable_decorator(self):
        """Test undoable decorator wraps operations in undo chunk."""

        @CoreUtils.undoable
        def create_and_move_cube():
            cube = cmds.polyCube(name="test_undo_cube")[0]
            cmds.move(5, 0, 0, cube)
            return cube

        # Execute the decorated function
        create_and_move_cube()
        self.assertTrue(cmds.objExists("test_undo_cube"))

        # Undo should remove both the move and creation
        cmds.undo()
        self.assertFalse(cmds.objExists("test_undo_cube"))

    def test_undoable_decorator_with_exception(self):
        """Test undoable decorator handles exceptions properly."""

        @CoreUtils.undoable
        def create_and_fail():
            cmds.polyCube(name="test_exception_cube")
            raise ValueError("Intentional test error")

        # Should raise the exception but still close undo chunk
        with self.assertRaises(ValueError):
            create_and_fail()

        # Clean up if cube was created
        if cmds.objExists("test_exception_cube"):
            cmds.delete("test_exception_cube")

    def test_undoable_accepts_a_chunk_name(self):
        """A named chunk is what Maya's Edit menu reads back as "Undo <name>".

        The default stays unnamed so existing call sites keep their historic
        menu label.
        """

        @CoreUtils.undoable(name="Probe Named Chunk")
        def make():
            cmds.polyCube(name="test_named_chunk_cube")

        make()
        self.assertEqual(cmds.undoInfo(q=True, undoName=True), "Probe Named Chunk")
        cmds.undo()
        self.assertFalse(cmds.objExists("test_named_chunk_cube"))

    def test_undoable_forwards_receiver_and_arguments(self):
        """The decorator must work on instance methods, args and kwargs intact."""

        class _Rig:
            @CoreUtils.undoable(suspend_refresh=True)
            def make(self, name, suffix="_x"):
                return cmds.polyCube(name=f"{name}{suffix}")[0]

        made = _Rig().make("test_recv_cube", suffix="_ok")
        self.assertTrue(made.startswith("test_recv_cube_ok"))
        cmds.undo()

    def test_suspended_refresh_is_reentrant(self):
        """Only the outermost block may resume the viewport.

        Regression: ``cmds.refresh -suspend`` is a flag with no query form, so
        a nested block's exit resumed the viewport while the outer one was
        still running -- which is exactly what a suspended operation calling
        another one does (``TubeRig.build`` -> ``teardown``).
        """
        calls = []
        real_refresh = cmds.refresh

        def spy(*args, **kwargs):
            if "suspend" in kwargs or "su" in kwargs:
                calls.append(kwargs.get("suspend", kwargs.get("su")))
            return real_refresh(*args, **kwargs)

        cmds.refresh = spy
        try:
            with CoreUtils.suspended_refresh():
                with CoreUtils.suspended_refresh():
                    pass
                self.assertEqual(calls, [True], "inner block resumed the viewport")
        finally:
            cmds.refresh = real_refresh
        self.assertEqual(calls, [True, False])
        self.assertEqual(CoreUtils._refresh_suspend_depth, 0)

    def test_suspended_refresh_restores_depth_on_exception(self):
        """A raising body must not strand the viewport suspended."""
        with self.assertRaises(ValueError):
            with CoreUtils.suspended_refresh():
                raise ValueError("Intentional test error")
        self.assertEqual(CoreUtils._refresh_suspend_depth, 0)

    def test_undo_disabled_keeps_work_off_the_queue(self):
        """Work inside the block must be invisible to undo."""
        recorded = cmds.polyCube(name="test_recorded_cube")[0]
        with CoreUtils.undo_disabled():
            cmds.polyCube(name="test_unrecorded_cube")

        cmds.undo()

        self.assertFalse(
            cmds.objExists(recorded), "The recorded cube should have been undone."
        )
        self.assertTrue(
            cmds.objExists("test_unrecorded_cube"),
            "Undo reached work done with recording off.",
        )
        cmds.delete("test_unrecorded_cube")

    def test_undo_disabled_restores_state_and_preserves_the_queue(self):
        """An exception must not leave recording off, nor flush prior history.

        The flush half is the reason this uses ``stateWithoutFlush``: plain
        ``state=False`` would throw away the undo history the user built
        before the block ever ran.
        """
        cube = cmds.polyCube(name="test_queue_survivor")[0]

        with self.assertRaises(ValueError):
            with CoreUtils.undo_disabled():
                raise ValueError("Intentional test error")

        self.assertTrue(
            cmds.undoInfo(query=True, state=True),
            "Undo recording was left disabled after the block raised.",
        )
        cmds.undo()
        self.assertFalse(
            cmds.objExists(cube),
            "The pre-block undo queue was flushed instead of preserved.",
        )

    def test_selected_decorator(self):
        """Test selected decorator passes selection to function."""

        class TestClass:
            @CoreUtils.selected
            def get_selection_names(self, selection=None):
                return [x for x in selection] if selection else []

        tester = TestClass()
        cmds.select(self.cyl)
        result = tester.get_selection_names()
        self.assertEqual(result, ["cyl"])

        # Test passing explicit argument overrides selection
        result_explicit = tester.get_selection_names([self.cyl])
        self.assertEqual(result_explicit, ["cyl"])

    def test_reparent_decorator(self):
        """Test reparent decorator maintains hierarchy."""

        # Create a hierarchy
        parent = cmds.group(em=True, name="parent_grp")
        child = cmds.polyCube(name="child_cube")[0]
        cmds.parent(child, parent)

        @CoreUtils.reparent
        def operate_on_child(nodes):
            # Operation that might unparent or modify hierarchy
            # For test, we'll just return the node
            return nodes[0]

        # Pass both child and parent (or just child if logic allows, but error said 2 nodes required)
        # The decorator expects args[0] to be a list of nodes?
        # Let's check implementation:
        # instance, node_args = ptk.parse_method_args(args)
        # if not args or not args[0] or len(args[0]) < 2: raise ValueError
        # It seems it expects the first argument to be a list of at least 2 nodes?
        # Or maybe it expects (node1, node2, ...)?
        # "At least two Maya nodes are required."
        # This suggests it's designed for operations like boolean or combine where multiple nodes are involved.

        operate_on_child([child, parent])

        # Verify child is still under parent
        self.assertEqual(
            (cmds.listRelatives(str(child), parent=True) or [None])[0], parent
        )

        cmds.delete(parent)

    # -------------------------------------------------------------------------
    # Attribute Tests
    # -------------------------------------------------------------------------

    def test_temporarily_unlock_attributes(self):
        """Test temporarily unlocking attributes via Attributes.temporarily_unlock."""
        # Lock an attribute
        cmds.setAttr(f"{self.cyl}.translateX", lock=True)
        self.assertTrue(cmds.getAttr(f"{self.cyl}.translateX", lock=True))

        with Attributes.temporarily_unlock(self.cyl, ["translateX"]):
            self.assertFalse(cmds.getAttr(f"{self.cyl}.translateX", lock=True))

        # Should be locked again
        self.assertTrue(cmds.getAttr(f"{self.cyl}.translateX", lock=True))

    def test_temporarily_unlock_attributes_scoped(self):
        """Passing explicit attributes must scope the unlock to only those.

        Bug: the ``attributes`` parameter was accepted but never used —
        ``temporarily_unlock`` always unlocked the full standard TRS set
        regardless of what was passed in, silently unlocking sibling
        attributes the caller never asked to touch.
        Fixed: 2026-07-01
        """
        cmds.setAttr(f"{self.cyl}.translateX", lock=True)
        cmds.setAttr(f"{self.cyl}.translateY", lock=True)

        with Attributes.temporarily_unlock(self.cyl, ["translateX"]):
            self.assertFalse(cmds.getAttr(f"{self.cyl}.translateX", lock=True))
            self.assertTrue(
                cmds.getAttr(f"{self.cyl}.translateY", lock=True),
                "translateY should remain locked — it was not in the scoped list",
            )

        self.assertTrue(cmds.getAttr(f"{self.cyl}.translateX", lock=True))
        self.assertTrue(cmds.getAttr(f"{self.cyl}.translateY", lock=True))

    def test_filter_attributes(self):
        """Test filtering attributes via Attributes."""
        attrs = ["translateX", "translateY", "translateZ", "rotateX", "visibility"]

        # Exclude specific
        filtered = Attributes.filter(attrs, exclude="visibility")
        self.assertEqual(
            filtered, ["translateX", "translateY", "translateZ", "rotateX"]
        )

        # Include pattern
        filtered = Attributes.filter(attrs, include="translate*")
        self.assertEqual(filtered, ["translateX", "translateY", "translateZ"])

        # Exclude pattern
        filtered = Attributes.filter(attrs, exclude="*Z")
        self.assertEqual(
            filtered, ["translateX", "translateY", "rotateX", "visibility"]
        )

    # -------------------------------------------------------------------------
    # Parameter Mapping Tests
    # -------------------------------------------------------------------------

    def test_get_parameter_mapping(self):
        """Test getting parameter values from MEL command."""
        # polyCube parameters
        result = CoreUtils.get_parameter_mapping(
            node=self.cyl_hist, cmd="polyCylinder", parameters=["radius", "height"]
        )

        self.assertIsInstance(result, dict)
        self.assertIn("radius", result)
        self.assertAlmostEqual(result["radius"], 5.0)

    def test_set_parameter_mapping(self):
        """Test setting parameter values via MEL command."""
        # Use transformLimits as it works well with direct command calls
        CoreUtils.set_parameter_mapping(
            node=self.cyl,
            cmd="transformLimits",
            parameters={"enableTranslationX": (True, True), "translationX": (-5, 5)},
        )

        # Verify change
        limits = cmds.transformLimits(self.cyl, q=True, translationX=True)
        self.assertEqual(list(limits), [-5.0, 5.0])
        enabled = cmds.transformLimits(self.cyl, q=True, enableTranslationX=True)
        self.assertEqual(list(enabled), [True, True])

    # -------------------------------------------------------------------------
    # Mesh Operations
    # -------------------------------------------------------------------------

    def test_get_mfn_mesh(self):
        """Test getting MFnMesh."""
        # API 2.0
        mfn = CoreUtils.get_mfn_mesh(self.cyl, api_version=2)
        # Should be MFnMesh
        self.assertTrue(hasattr(mfn, "numVertices"))

        # API 1.0
        mfn_gen = CoreUtils.get_mfn_mesh(self.cyl, api_version=1)
        mfn_list = list(mfn_gen)
        self.assertTrue(len(mfn_list) > 0)
        self.assertTrue(hasattr(mfn_list[0], "numVertices"))

    def test_build_mesh_similarity_mapping(self):
        """Test mesh similarity mapping."""
        # Duplicate cylinder
        cyl2 = cmds.duplicate(self.cyl)[0]
        cmds.move(10, 0, 0, cyl2)

        mapping = CoreUtils.build_mesh_similarity_mapping(source=self.cyl, target=cyl2)

        # Mapping is keyed by leaf name (namespace preserved, DAG path
        # stripped); production returns string values. No namespace here,
        # so leaf name and short name coincide -- see
        # test_build_mesh_similarity_mapping_preserves_namespace for the
        # namespace-preserving case this fixture can't exercise.
        cyl_key = str(self.cyl).split("|")[-1]
        self.assertIn(cyl_key, mapping)
        self.assertEqual(str(mapping[cyl_key]), str(cyl2).split("|")[-1])

    def test_build_mesh_similarity_mapping_preserves_namespace(self):
        """Regression: mapping values must stay resolvable via cmds -- a
        namespaced target (e.g. RizomUV's re-imported ``ns:mesh``) used to
        come back with its namespace stripped (short_name), so consumers
        like UvUtils.transfer_uvs fed cmds.transferAttributes an
        unresolvable name and raised 'No object matches name'."""
        cmds.namespace(add="ns")
        cyl2 = cmds.duplicate(self.cyl, name="ns:cyl")[0]
        cmds.move(10, 0, 0, cyl2)

        mapping = CoreUtils.build_mesh_similarity_mapping(source=self.cyl, target=cyl2)

        cyl_key = str(self.cyl).split("|")[-1]
        self.assertIn(cyl_key, mapping)
        target_name = mapping[cyl_key]
        self.assertTrue(
            target_name.startswith("ns:"),
            f"namespace stripped from mapped value: {target_name!r}",
        )
        self.assertTrue(
            cmds.objExists(target_name),
            f"mapped value not resolvable by cmds: {target_name!r}",
        )

    def test_confirm_existence(self):
        """Test confirming object existence."""
        existing, non_existing = CoreUtils.confirm_existence(
            [self.cyl, "non_existent_obj"]
        )

        self.assertIn(self.cyl, existing)
        self.assertIn("non_existent_obj", non_existing)


class TestCoreUtilsEdgeCases(MayaTkTestCase):
    """Edge case tests for CoreUtils."""

    def test_get_array_type_with_empty_list(self):
        """Test array type detection with empty list."""
        result = CoreUtils.get_array_type([])
        self.assertIn(result, ["list", None, ""])

    def test_get_array_type_with_none(self):
        """Test array type detection with None."""
        result = CoreUtils.get_array_type(None)
        self.assertIn(result, ["none", "NoneType", None, ""])

    def test_convert_array_type_with_invalid_target_type(self):
        """Test converting to invalid target type returns lst unchanged."""
        cyl = cmds.polyCylinder()[0]
        result = CoreUtils.convert_array_type(f"{cyl}.vtx[0]", "invalid_type")
        # Should return PyMEL objects (the 'lst' parameter unchanged)
        self.assertTrue(len(result) > 0)
        cmds.delete(cyl)

    def test_convert_array_type_with_nonexistent_component(self):
        """Test converting nonexistent component."""
        try:
            result = CoreUtils.convert_array_type("nonexistent.vtx[0]", "str")
            if result is not None:
                self.assertIsInstance(result, list)
        except (RuntimeError, RuntimeError):
            pass  # Expected behavior


class TestBoundingBox(MayaTkTestCase):
    """The plain-data BoundingBox (backs clip fitting / frustum work)."""

    def test_corners_are_every_min_max_combination(self):
        from mayatk.core_utils._core_utils import BoundingBox

        box = BoundingBox((-1, -2, -3), (1, 2, 3))
        corners = box.corners
        self.assertEqual(len(corners), 8)
        self.assertEqual(
            {(c.x, c.y, c.z) for c in corners},
            {(x, y, z) for x in (-1.0, 1.0) for y in (-2.0, 2.0) for z in (-3.0, 3.0)},
        )

    def test_corners_of_a_degenerate_box_are_all_the_same_point(self):
        from mayatk.core_utils._core_utils import BoundingBox

        corners = BoundingBox((5, 5, 5), (5, 5, 5)).corners
        self.assertEqual(len(corners), 8)
        self.assertEqual({(c.x, c.y, c.z) for c in corners}, {(5.0, 5.0, 5.0)})

    def test_bounding_box_of_a_cube_matches_its_corners(self):
        cube = cmds.polyCube(w=2, h=2, d=2, n="bbox_cube")[0]
        box = CoreUtils.get_bounding_box(cube)
        self.assertAlmostEqual(box.diagonal, (2**2 * 3) ** 0.5, places=5)
        self.assertTrue(all(abs(abs(c.x) - 1) < 1e-5 for c in box.corners))


class TestNodeHandles(MayaTkTestCase):
    """``node_handles`` / ``resolve_handles`` — rename-proof node references.

    The point is not that they resolve, it is that they resolve CORRECTLY across an
    operation that renames: a captured name string does not merely go stale, it can
    resolve to whatever a clash-uniquifier moved into that name.
    """

    def test_handles_follow_a_rename(self):
        cube = cmds.polyCube(name="before")[0]
        handles = CoreUtils.node_handles(cube)
        cmds.rename(cube, "after")
        self.assertEqual(
            [p.split("|")[-1] for p in CoreUtils.resolve_handles(handles)], ["after"]
        )

    def test_handles_survive_a_clashing_namespace_merge(self):
        """The exact scenario the reference-manager unlink rests on: a merge that
        renames the moved node because its name is already taken at the root."""
        cmds.polyCube(name="asset")  # squats the root-namespace name
        cmds.namespace(add=":NS")
        cmds.namespace(set=":NS")
        moved = cmds.polyCube(name="asset")[0]
        cmds.namespace(set=":")

        handles = CoreUtils.node_handles(moved)
        cmds.namespace(removeNamespace=":NS", mergeNamespaceWithRoot=True)

        (resolved,) = CoreUtils.resolve_handles(handles)
        leaf = resolved.split("|")[-1]
        # Maya uniquified it, and the handle reports the NEW name -- not "asset",
        # which is now a different node entirely.
        self.assertNotEqual(leaf, "asset")
        self.assertTrue(leaf.startswith("asset"))

    def test_dead_nodes_are_dropped_or_held_as_none(self):
        cube = cmds.polyCube(name="doomed")[0]
        handles = CoreUtils.node_handles(cube)
        cmds.delete(cube)
        self.assertEqual(CoreUtils.resolve_handles(handles), [])
        self.assertEqual(CoreUtils.resolve_handles(handles, drop_dead=False), [None])

    def test_unresolvable_names_are_skipped_not_raised(self):
        self.assertEqual(CoreUtils.node_handles("no_such_node_here"), [])
        self.assertEqual(CoreUtils.node_handles(None), [])

    def test_accepts_a_bare_node_or_a_list(self):
        a = cmds.polyCube(name="handle_a")[0]
        b = cmds.polyCube(name="handle_b")[0]
        self.assertEqual(len(CoreUtils.node_handles(a)), 1)
        self.assertEqual(len(CoreUtils.node_handles([a, b])), 2)

    def test_dg_nodes_resolve_by_name(self):
        """Not every node is a DAG node — a shader has a name, not a path."""
        shader = cmds.shadingNode("lambert", asShader=True, name="handle_shader")
        handles = CoreUtils.node_handles(shader)
        self.assertEqual(CoreUtils.resolve_handles(handles), [shader])


class TestObjectSpaceBoundingBox(MayaTkTestCase):
    """``get_bounding_box(world=False)`` must be transform-INVARIANT.

    Every Maya bounding-box query answers in world axis-aligned terms --
    ``polyEvaluate -boundingBox`` and ``xform -q -bb`` alike, with or without
    ``-ws``, on the shape as much as the transform -- so both public entry
    points were returning the WORLD box for an "object space" request: a moved
    copy read a different box and a rotated one read a larger box, silently.
    """

    def _cube(self, name):
        return cmds.polyCube(name=name, width=2, height=4, depth=6)[0]

    def test_object_space_box_ignores_move_rotate_and_scale(self):
        """The whole contract: the node's own transform must not show up."""
        base = self._cube("osBase")
        moved = cmds.duplicate(base, name="osMoved")[0]
        cmds.xform(moved, translation=(10, 5, -3), worldSpace=True)
        cmds.xform(moved, rotation=(0, 45, 0), worldSpace=True)
        cmds.xform(moved, scale=(3, 1, 2))

        a = CoreUtils.get_bounding_box(base, world=False)
        b = CoreUtils.get_bounding_box(moved, world=False)
        for axis in range(3):
            self.assertAlmostEqual(a.min[axis], b.min[axis], places=5)
            self.assertAlmostEqual(a.max[axis], b.max[axis], places=5)
        # ...and the world box of the moved copy genuinely IS different, so
        # this is not just "both queries return the same wrong thing".
        w = CoreUtils.get_bounding_box(moved, world=True)
        self.assertGreater(abs(w.center.x - a.center.x), 1.0)

    def test_world_space_is_unchanged(self):
        cube = self._cube("osWorld")
        cmds.xform(cube, translation=(7, 0, 0), worldSpace=True)
        box = CoreUtils.get_bounding_box(cube, world=True)
        expected = cmds.exactWorldBoundingBox(cube)
        for axis in range(3):
            self.assertAlmostEqual(box.min[axis], expected[axis], places=5)

    def test_component_keeps_the_plain_query(self):
        """A face's ``xform -bb -ws 0`` IS object space and must not change.

        ``edit_utils`` relies on this for its object-space face filtering;
        there is no per-component ``boundingBoxMin`` to build from.
        """
        base = self._cube("osComp")
        moved = cmds.duplicate(base, name="osCompMoved")[0]
        cmds.xform(moved, translation=(10, 5, -3), rotation=(0, 45, 0), worldSpace=True)
        a = CoreUtils.get_bounding_box(f"{base}.f[0]", world=False)
        b = CoreUtils.get_bounding_box(f"{moved}.f[0]", world=False)
        for axis in range(3):
            self.assertAlmostEqual(a.min[axis], b.min[axis], places=5)

    def test_group_composes_descendants_into_its_own_frame(self):
        """A group has no shape of its own; its children fold into its frame."""
        a = self._cube("osGrpA")
        b = self._cube("osGrpB")
        cmds.xform(b, translation=(10, 0, 0), worldSpace=True)
        grp = cmds.group(a, b, name="osGrp")
        moved = cmds.duplicate(grp, name="osGrpMoved")[0]
        cmds.xform(
            moved, translation=(50, 20, -7), rotation=(0, 33, 0), worldSpace=True
        )

        g1 = CoreUtils.get_bounding_box(grp, world=False)
        g2 = CoreUtils.get_bounding_box(moved, world=False)
        for axis in range(3):
            self.assertAlmostEqual(g1.min[axis], g2.min[axis], places=4)
            self.assertAlmostEqual(g1.max[axis], g2.max[axis], places=4)
        # The group spans both cubes, so it is wider than either alone.
        self.assertGreater(g1.size.x, 10.0)

    def test_non_mesh_shapes_are_supported(self):
        """polyEvaluate fails outright on these, so they used to fall through
        to the world box -- the very thing object space must not be."""
        for maker, name in (
            (lambda n: cmds.nurbsPlane(name=n)[0], "osNurbs"),
            (lambda n: cmds.spaceLocator(name=n)[0], "osLoc"),
        ):
            node = maker(name)
            moved = cmds.duplicate(node, name=name + "Moved")[0]
            cmds.xform(moved, translation=(9, 9, 9), worldSpace=True)
            a = CoreUtils.get_bounding_box(node, world=False)
            b = CoreUtils.get_bounding_box(moved, world=False)
            for axis in range(3):
                self.assertAlmostEqual(a.min[axis], b.min[axis], places=5, msg=name)

    def test_empty_group_falls_back_to_the_world_box(self):
        """No shapes means no object-space extent to report."""
        grp = cmds.group(empty=True, name="osEmpty")
        box = CoreUtils.get_bounding_box(grp, world=False)
        self.assertIsNotNone(box)


class TestXformBoundingBoxDelegation(MayaTkTestCase):
    """``XformUtils.get_bounding_box`` shares one object-space definition."""

    def test_object_space_matches_core_utils(self):
        cube = cmds.polyCube(name="xfBase", width=2, height=4, depth=6)[0]
        cmds.xform(cube, translation=(4, 4, 4), rotation=(0, 30, 0), worldSpace=True)
        corners = "xmin|ymin|zmin|xmax|ymax|zmax"
        got = mtk.XformUtils.get_bounding_box(cube, corners, world_space=False)
        want = CoreUtils.get_bounding_box(cube, world=False)
        for axis in range(3):
            self.assertAlmostEqual(got[axis], want.min[axis], places=5)
            self.assertAlmostEqual(got[axis + 3], want.max[axis], places=5)

    def test_multiple_objects_in_object_space_is_refused(self):
        """Several nodes share no frame; the old query answered with a
        combined WORLD box, which is wrong AND silent."""
        a = cmds.polyCube(name="xfA")[0]
        b = cmds.polyCube(name="xfB")[0]
        with self.assertRaises(ValueError):
            mtk.XformUtils.get_bounding_box([a, b], "center", world_space=False)
        # World space over several objects stays legal.
        self.assertIsNotNone(
            mtk.XformUtils.get_bounding_box([a, b], "center", world_space=True)
        )

    def test_empty_value_returns_the_whole_box(self):
        """The default used to raise; "the bounding box" is the obvious
        meaning of asking for no particular key."""
        cube = cmds.polyCube(name="xfDefault", width=2, height=4, depth=6)[0]
        got = mtk.XformUtils.get_bounding_box(cube)
        self.assertEqual(len(got), 6)
        expected = cmds.exactWorldBoundingBox(cube)
        for axis in range(6):
            self.assertAlmostEqual(got[axis], expected[axis], places=5)


if __name__ == "__main__":
    unittest.main()
