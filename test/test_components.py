# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.core_utils.components module

Tests for Components class functionality including:
- Component index retrieval and type conversion
- Component filtering and queries
- Geometric operations (islands, contiguous edges)
- Distance calculations (closest/furthest vertices)
- Path finding (shortest path, edge loops/rings)
- Normal operations (angles, hardness, averaging)
- Topology modification (bridge)
"""
import math
import unittest
import mayatk as mtk
from mayatk.core_utils.components import Components

from base_test import MayaTkTestCase
import maya.cmds as cmds


class TestComponents(MayaTkTestCase):
    """Tests for Components class."""

    def setUp(self):
        """Set up test scene with geometry."""
        super().setUp()
        self.cube = cmds.polyCube(name="test_comp_cube")[0]
        self.sphere = cmds.polySphere(name="test_comp_sphere")[0]
        self.plane = cmds.polyPlane(name="test_comp_plane", sx=5, sy=5)[0]

    def tearDown(self):
        """Clean up."""
        for obj in [
            "test_comp_cube",
            "test_comp_sphere",
            "test_comp_plane",
            "test_comp_cube2",
        ]:
            if cmds.objExists(obj):
                cmds.delete(obj)
        super().tearDown()

    # -------------------------------------------------------------------------
    # Basic Component Operations
    # -------------------------------------------------------------------------

    def test_get_component_type(self):
        """Test getting component type."""
        comp = f"{self.cube}.vtx[0]"
        self.assertEqual(Components.get_component_type(comp, "abv"), "vtx")
        self.assertEqual(Components.get_component_type(comp, "full"), "Polygon Vertex")

        comp = f"{self.cube}.e[0]"
        self.assertEqual(Components.get_component_type(comp, "abv"), "e")

    def test_convert_alias(self):
        """Test converting component aliases."""
        self.assertEqual(Components.convert_alias("vertex", "abv"), "vtx")
        self.assertEqual(Components.convert_alias("e", "full"), "Polygon Edge")
        self.assertEqual(Components.convert_alias(31, "abv"), "vtx")

    def test_convert_component_type(self):
        """Test converting between component types."""
        # Vertex to Edge
        vtx = f"{self.cube}.vtx[0]"
        edges = Components.convert_component_type(vtx, "edge", flatten=True)
        self.assertTrue(len(edges) > 0)
        self.assertTrue("e[" in str(edges[0]))

    def test_get_component_index(self):
        """Test getting component indices."""
        # Single
        idx = Components.get_component_index(f"{self.cube}.vtx[5]")
        self.assertEqual(idx, 5)

        # List
        indices = Components.get_component_index(
            [f"{self.cube}.vtx[1]", f"{self.cube}.vtx[3]"]
        )
        self.assertEqual(sorted(indices), [1, 3])

    def test_convert_int_to_component(self):
        """Test converting integers to components."""
        comps = Components.convert_int_to_component(
            self.cube, [0, 1], "vtx", flatten=True
        )
        self.assertEqual(len(comps), 2)
        self.assertTrue("vtx[0]" in str(comps[0]) or "vtx[0]" in str(comps[1]))

    def test_filter_components(self):
        """Test filtering components."""
        all_vtx = f"{self.cube}.vtx[:]"
        inc = f"{self.cube}.vtx[0:1]"
        exc = f"{self.cube}.vtx[1]"

        # Include 0, 1. Exclude 1. Result should be 0.
        res = Components.filter_components(all_vtx, inc=inc, exc=exc, flatten=True)
        self.assertEqual(len(res), 1)
        self.assertTrue("vtx[0]" in str(res[0]))

    def test_get_components(self):
        """Test getting components from object."""
        vtxs = Components.get_components(self.cube, "vtx", flatten=True)
        self.assertEqual(len(vtxs), 8)  # Cube has 8 vertices

    def test_map_components_to_objects(self):
        """Test mapping components to objects."""
        comps = [f"{self.cube}.vtx[0]", f"{self.sphere}.vtx[0]"]
        mapping = Components.map_components_to_objects(comps)

        # Keys are likely shape names
        keys = list(mapping.keys())
        self.assertTrue(any("test_comp_cube" in k for k in keys))
        self.assertTrue(any("test_comp_sphere" in k for k in keys))

        # Check content
        cube_key = next(k for k in keys if "test_comp_cube" in k)
        self.assertEqual(len(mapping[cube_key]), 1)

    def test_map_components_to_objects_multiple_per_object(self):
        """Test mapping multiple components from same object."""
        comps = [f"{self.cube}.vtx[0]", f"{self.cube}.vtx[1]", f"{self.cube}.vtx[2]"]
        mapping = Components.map_components_to_objects(comps)

        # Should have one key (cube shape)
        self.assertEqual(len(mapping), 1)

        # That key should have 3 components
        cube_key = list(mapping.keys())[0]
        self.assertEqual(len(mapping[cube_key]), 3)

    def test_map_components_to_objects_empty(self):
        """Test mapping empty component list."""
        mapping = Components.map_components_to_objects([])
        self.assertEqual(mapping, {})

    def test_map_components_to_objects_collision_resolves_to_path(self):
        """Keys are collision-prone leaf names; values stay path-qualified.

        Two objects sharing a leaf name (under different parents) make the
        dict key (``pCube1``) ambiguous — passing it straight to a Maya cmd
        raises ``ValueError: More than one object matches name``. This was the
        tentacle normals.b000/b001 ``polyOptions(obj)`` crash. The values, by
        contrast, are path-qualified, so callers must resolve the unambiguous
        object path from them.
        """
        c1 = cmds.polyCube(name="pCube1")[0]
        cmds.group(c1, name="grp1")
        c2 = cmds.polyCube(name="pCube1")[0]
        cmds.group(c2, name="grp2")

        edges = cmds.ls("grp1|pCube1.e[0:3]", flatten=True)
        mapping = Components.map_components_to_objects(edges)

        # The bare leaf key is ambiguous and must not be fed to a cmd directly.
        self.assertIn("pCube1", mapping)
        with self.assertRaises(ValueError):
            cmds.polyOptions("pCube1", se=True)

        # Resolving from the (path-qualified) values yields a unique path that
        # Maya cmds accept — the fix the slots now rely on.
        for components in mapping.values():
            objects = cmds.ls(components, objectsOnly=True, long=True) or []
            self.assertEqual(len(objects), 1)
            self.assertTrue(objects[0].startswith("|grp1|pCube1"))
            cmds.polyOptions(objects, se=True)  # must not raise

    # -------------------------------------------------------------------------
    # Geometric Operations
    # -------------------------------------------------------------------------

    def test_get_contiguous_edges(self):
        """Test getting contiguous edges."""
        # Select two edges that share a vertex
        vtx = f"{self.plane}.vtx[0]"
        edges = cmds.polyListComponentConversion(vtx, toEdge=True)
        edges = cmds.ls(edges, flatten=True)

        groups = Components.get_contiguous_edges(edges)
        self.assertEqual(
            len(groups), 1
        )  # Should be one group since they share a vertex

    def test_get_contiguous_islands(self):
        """Test getting contiguous face islands."""
        # Select two disjoint faces
        f1 = f"{self.plane}.f[0]"
        f2 = f"{self.plane}.f[24]"  # Far corner

        islands = Components.get_contiguous_islands([f1, f2])
        self.assertEqual(len(islands), 2)

        # Select two adjacent faces
        f3 = f"{self.plane}.f[1]"
        islands = Components.get_contiguous_islands([f1, f3])
        self.assertEqual(len(islands), 1)

    def test_get_components_preserves_namespace(self):
        """Namespaced face strings must round-trip through get_components.

        Regression: shape-prefix substitution stripped the namespace, so
        ``ns1:cubeA.f[1]`` became ``cubeAShape.f[1]`` — silently empty, or
        worse, pointing at a same-name root-namespace object.
        """
        if not cmds.namespace(exists="ns_test"):
            cmds.namespace(add="ns_test")
        cmds.namespace(set="ns_test")
        ns_cube = cmds.polyCube(name="nscube", ch=False)[0]
        cmds.namespace(set=":")

        try:
            seed = f"{ns_cube}.f[1]"
            result = Components.get_components(
                [seed], component_type="faces", flatten=True
            )
            self.assertTrue(result, "namespaced face dropped by get_components")
            for f in result:
                self.assertIn("ns_test:", f, f"namespace stripped from {f!r}")
        finally:
            cmds.delete(ns_cube)
            if cmds.namespace(exists="ns_test"):
                cmds.namespace(removeNamespace="ns_test", mergeNamespaceWithRoot=True)

    def test_get_faces_with_similar_normals_multi_object(self):
        """Multi-object face input must consider faces on every transform.

        Regression: the helper used to capture only the first face's transform,
        so faces selected on a second object were silently ignored.
        """
        c2 = cmds.polyCube(name="test_comp_cube2", ch=False)[0]
        cmds.move(5, 0, 0, c2)

        seed_a = f"{self.cube}.f[1]"  # +Y face on cubeA
        seed_b = f"{c2}.f[1]"  # +Y face on cubeB
        seeds = cmds.ls([seed_a, seed_b], flatten=True)

        result = Components.get_faces_with_similar_normals(
            seeds, range_x=0.1, range_y=0.1, range_z=0.1
        )

        # Strip shape suffix so the assertion is form-agnostic.
        contributors = {
            f.split(".")[0].split("|")[-1].removesuffix("Shape") for f in result
        }
        self.assertIn("test_comp_cube", contributors)
        self.assertIn("test_comp_cube2", contributors)
        # Each cube has one +Y face, so exactly two matches expected.
        self.assertEqual(len(result), 2)

    def test_get_islands(self):
        """Test getting islands from object."""
        # Create combined object with 2 shells
        c2 = cmds.polyCube(name="test_comp_cube2")[0]
        cmds.move(5, 0, 0, c2)
        combined = cmds.polyUnite(self.cube, c2, ch=False)[0]

        islands = list(Components.get_islands(combined))
        self.assertEqual(len(islands), 2)

        cmds.delete(combined)

    def test_get_border_components(self):
        """Test getting border components."""
        # Plane has border edges
        borders = Components.get_border_components(
            f"{self.plane}.e[:]", returned_type="str"
        )
        self.assertTrue(len(borders) > 0)

        # Cube has no border edges (closed manifold)
        borders_cube = Components.get_border_components(
            f"{self.cube}.e[:]", returned_type="str"
        )
        self.assertEqual(len(borders_cube), 0)

    # -------------------------------------------------------------------------
    # Distance & Location
    # -------------------------------------------------------------------------

    def test_get_furthest_vertices(self):
        """Test getting furthest vertices."""
        # Cube corners are furthest
        v1, v2 = Components.get_furthest_vertices(
            f"{self.cube}.vtx[:]", f"{self.cube}.vtx[:]"
        )
        p1 = cmds.pointPosition(v1)
        p2 = cmds.pointPosition(v2)
        dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(p1, p2)))
        self.assertAlmostEqual(dist, 1.732, places=2)

    def test_get_closest_verts(self):
        """Test getting closest vertices between sets."""
        # Create another cube near the first
        c2 = cmds.polyCube()[0]
        cmds.move(2, 0, 0, c2)

        pairs = Components.get_closest_verts(f"{self.cube}.vtx[:]", f"{c2}.vtx[:]")
        self.assertTrue(len(pairs) > 0)

        v1, v2 = pairs[0]
        p1 = cmds.pointPosition(v1)
        p2 = cmds.pointPosition(v2)
        dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(p1, p2)))
        self.assertAlmostEqual(dist, 1.0, places=2)

        cmds.delete(c2)

    def test_get_closest_vertex(self):
        """Test getting closest vertex on object."""
        res = Components.get_closest_vertex(f"{self.sphere}.vtx[0]", self.cube)
        self.assertIsInstance(res, dict)
        self.assertEqual(len(res), 1)

    def test_get_vertices_within_threshold(self):
        """Test getting vertices within threshold."""
        inside, outside = Components.get_vertices_within_threshold(
            f"{self.plane}.vtx[0]", 0.5
        )
        self.assertTrue(len(inside) >= 1)
        self.assertTrue(len(outside) > 0)

    # -------------------------------------------------------------------------
    # Path Finding
    # -------------------------------------------------------------------------

    def test_get_shortest_path(self):
        """Test shortest path."""
        path = Components.get_shortest_path(
            [f"{self.plane}.vtx[0]", f"{self.plane}.vtx[35]"], flatten=True
        )
        self.assertTrue(len(path) > 2)

        # Check if start/end are in path (checking string representation for shape name)
        path_strs = [str(p) for p in path]
        self.assertTrue(
            any("test_comp_plane" in p and "vtx[0]" in p for p in path_strs)
        )

    def test_get_edge_path(self):
        """Test edge loops and rings."""
        edge = f"{self.sphere}.e[200]"  # Random edge

        loop = Components.get_edge_path(edge, "edgeLoop")
        self.assertTrue(len(loop) > 1)

        ring = Components.get_edge_path(edge, "edgeRing")
        self.assertTrue(len(ring) > 1)

    # -------------------------------------------------------------------------
    # Normals
    # -------------------------------------------------------------------------

    def test_get_normal(self):
        """Test getting face normal."""
        normal = Components.get_normal(f"{self.cube}.f[0]")
        self.assertEqual(len(normal), 3)

    def test_get_normal_angle(self):
        """Test getting normal angle."""
        angle = Components.get_normal_angle(f"{self.cube}.e[0]")
        # Returns float for single edge
        self.assertAlmostEqual(angle, 90.0, places=1)

    def test_get_edges_by_normal_angle(self):
        """Test filtering edges by normal angle."""
        hard_edges = Components.get_edges_by_normal_angle(self.cube, 80, 100)
        self.assertTrue(len(hard_edges) > 0)

        soft_edges = Components.get_edges_by_normal_angle(self.cube, 0, 10)
        self.assertEqual(len(soft_edges), 0)

    def test_get_edges_by_normal_angle_bound_matches_true_angle_after_freeze(self):
        """A range bound equal to a mesh's true edge angle must include those
        edges despite sub-ulp normal drift.

        Same defect family as the set_edge_hardness threshold bug: after a
        transform+freeze a cube's real 90° edges measure 89.9999…°, so a strict
        bound of 90 (as the Select-Edges-By-Angle / UV-cut tools pass) would
        silently drop them. The tolerance on both range ends keeps them in.
        """
        cmds.rotate(37, 12, 5, self.cube)
        cmds.makeIdentity(self.cube, apply=True, t=True, r=True, s=True)

        # Confirm the drift is present (some edges now measure just under 90).
        _, angles = Components.get_edges_by_normal_angle(
            self.cube, 0, 180, return_angles=True
        )
        self.assertTrue(any(a < 90.0 for a in angles.values()))

        # A bound of exactly 90 must still catch all 12 right-angle edges.
        hard_edges = Components.get_edges_by_normal_angle(self.cube, 90, 180)
        self.assertEqual(len(hard_edges), 12)

    def test_get_edges_by_normal_angle_return_angles(self):
        """Test return_angles parameter returns edges and angle dict."""
        result = Components.get_edges_by_normal_angle(
            self.cube, 80, 100, return_angles=True
        )

        # Should return a tuple
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)

        edges, angles = result
        # Edges should be a list
        self.assertIsInstance(edges, list)
        self.assertTrue(len(edges) > 0)

        # Angles should be a dict mapping edge names (strings) to floats
        self.assertIsInstance(angles, dict)
        for edge_name, angle in angles.items():
            self.assertIsInstance(edge_name, str)
            self.assertIsInstance(angle, float)
            # All edges in a cube have 90 degree angles
            self.assertAlmostEqual(angle, 90.0, places=1)

        # The filtered edges should have their names in the angles dict
        for edge in edges:
            self.assertIn(edge, angles)
            self.assertTrue(80 <= angles[edge] <= 100)

    def test_set_edge_hardness(self):
        """Test setting edge hardness based on angle threshold."""
        # Cube has all 90-degree edges, set threshold at 45 degrees
        # All edges should get upper_hardness since 90 > 45
        Components.set_edge_hardness(
            self.cube, 45, upper_hardness=0, lower_hardness=180
        )

        # Verify the cube still exists and is valid
        self.assertTrue(cmds.objExists(self.cube))

    def test_set_edge_hardness_threshold_matches_true_angle_after_freeze(self):
        """A threshold equal to a mesh's true edge angle must classify those
        edges as "upper".

        Bug: after a transform+freeze the world-space face normals drift by a
        few sub-ulp, so a cube's real 90° edges measure 89.9999…°. With a strict
        `>=` boundary a threshold of 90 missed them — the user had to dial in 89
        to restore the cube's default hard normals, which is unintuitive.
        Fixed: 2026-07-08 (boundary nudged down by Components._ANGLE_MATCH_EPS).
        """
        import maya.api.OpenMaya as om

        # Reproduce the drift: an ordinary rotate + freeze-transform.
        cmds.rotate(37, 12, 5, self.cube)
        cmds.makeIdentity(self.cube, apply=True, t=True, r=True, s=True)

        # Sanity: some edges now measure just under 90° (this is the drift that
        # defeated a strict comparison).
        _, edge_angles = Components.get_edges_by_normal_angle(
            self.cube, 0, 180, return_angles=True
        )
        self.assertTrue(
            any(a < 90.0 for a in edge_angles.values()),
            "fixture did not reproduce sub-90° normal drift",
        )

        # "Set normals by angle" intent: harden >= threshold, soften < threshold.
        # At threshold 90 every cube edge should harden and none should soften.
        Components.set_edge_hardness(
            self.cube, 90, upper_hardness=0, lower_hardness=180
        )

        sel = om.MSelectionList()
        sel.add(self.cube)
        dag = sel.getDagPath(0)
        edge_iter = om.MItMeshEdge(dag)
        soft = []
        while not edge_iter.isDone():
            if edge_iter.isSmooth:
                soft.append(edge_iter.index())
            edge_iter.next()
        self.assertEqual(
            soft,
            [],
            f"edges {soft} were softened at threshold 90 — a 90° edge must "
            f"classify as 'upper' (>= threshold), not 'lower'",
        )

    def test_set_edge_hardness_no_values(self):
        """Test set_edge_hardness early exit when no hardness values provided."""
        # Should not raise, just return early
        Components.set_edge_hardness(self.cube, 45)

    def test_set_edge_hardness_upper_only(self):
        """Test setting only upper hardness."""
        Components.set_edge_hardness(self.cube, 45, upper_hardness=0)
        self.assertTrue(cmds.objExists(self.cube))

    def test_set_edge_hardness_lower_only(self):
        """Test setting only lower hardness."""
        Components.set_edge_hardness(self.cube, 45, lower_hardness=180)
        self.assertTrue(cmds.objExists(self.cube))

    def test_set_edge_hardness_unlocks_normals_when_requested(self):
        """Locked vertex normals block polySoftEdge from updating shading.

        Repro: FIRE_ASSETS_module.ma — meshes round-tripped through Marmoset
        had 100% of vertex normals locked, so set_edge_hardness flipped the
        edge hard/soft flag but the visible smoothing did not change.
        Fix: opt-in `unlock_normals=True` unlocks before applying hardness.
        """
        # Lock all vertex normals to simulate an imported asset
        cmds.polyNormalPerVertex(self.cube + ".vtx[*]", freezeNormal=True)
        locked_before = cmds.polyNormalPerVertex(
            self.cube + ".vtx[*]", q=True, freezeNormal=True
        )
        self.assertTrue(all(locked_before), "setup: normals should be locked")

        Components.set_edge_hardness(
            self.cube, 45, upper_hardness=0, lower_hardness=180, unlock_normals=True
        )

        locked_after = cmds.polyNormalPerVertex(
            self.cube + ".vtx[*]", q=True, freezeNormal=True
        )
        self.assertFalse(
            any(locked_after),
            "unlock_normals=True should leave all vertex normals unlocked",
        )

    def test_set_edge_hardness_preserves_lock_when_not_requested(self):
        """Default behavior must leave existing locked normals locked."""
        cmds.polyNormalPerVertex(self.cube + ".vtx[*]", freezeNormal=True)

        Components.set_edge_hardness(
            self.cube, 45, upper_hardness=0, lower_hardness=180, unlock_normals=False
        )

        locked_after = cmds.polyNormalPerVertex(
            self.cube + ".vtx[*]", q=True, freezeNormal=True
        )
        self.assertTrue(
            all(locked_after),
            "unlock_normals=False must preserve the user's locked normals",
        )

    def test_set_edge_hardness_aborts_and_returns_locked_objects(self):
        """unlock_normals=False on a mesh with locked normals must abort and
        return the offending object(s) so the UI can warn the user — locked
        normals silently block polySoftEdge, so applying hardness would no-op.
        """
        cmds.polyNormalPerVertex(self.cube + ".vtx[*]", freezeNormal=True)

        result = Components.set_edge_hardness(
            self.cube, 45, upper_hardness=0, lower_hardness=180, unlock_normals=False
        )

        self.assertTrue(result, "locked normals must be reported, not silently skipped")
        self.assertTrue(
            any("test_comp_cube" in p for p in result),
            "the returned paths must identify the blocked object",
        )
        # Normals must remain locked — the operation was aborted before edits.
        self.assertTrue(
            all(
                cmds.polyNormalPerVertex(self.cube + ".vtx[*]", q=True, freezeNormal=True)
            ),
            "an aborted run must not touch the mesh",
        )

    def test_set_edge_hardness_detects_locked_across_name_collision(self):
        """Two objects sharing a leaf name merge into one
        map_components_to_objects key. The guard must check *every* object the
        key fronts — locking only the collided sibling must still be detected,
        not silently skipped because the first-resolved object was clean.
        """
        cmds.group(cmds.polyCube(name="dup_norm_cube")[0], name="grpA")
        cmds.group(cmds.polyCube(name="dup_norm_cube")[0], name="grpB")
        # Lock only the second cube's normals.
        cmds.polyNormalPerVertex("grpB|dup_norm_cube.vtx[*]", freezeNormal=True)

        result = Components.set_edge_hardness(
            ["grpA|dup_norm_cube", "grpB|dup_norm_cube"],
            45,
            upper_hardness=0,
            lower_hardness=180,
            unlock_normals=False,
        )
        self.assertTrue(
            any("grpB" in p for p in result),
            "the locked collided sibling must be reported, not silently skipped",
        )

    def test_set_edge_hardness_returns_empty_on_clean_mesh(self):
        """A mesh without locked normals proceeds and returns an empty list."""
        result = Components.set_edge_hardness(
            self.cube, 45, upper_hardness=0, lower_hardness=180, unlock_normals=False
        )
        self.assertEqual(result, [], "an unblocked run must report nothing skipped")

    def test_set_edge_hardness_unlock_requested_returns_empty(self):
        """unlock_normals=True bypasses the guard even when normals are locked."""
        cmds.polyNormalPerVertex(self.cube + ".vtx[*]", freezeNormal=True)

        result = Components.set_edge_hardness(
            self.cube, 45, upper_hardness=0, lower_hardness=180, unlock_normals=True
        )
        self.assertEqual(result, [], "unlocking opt-in must not report a block")

    def test_set_edge_hardness_no_values_returns_empty(self):
        """The no-hardness early exit returns an empty list (not None)."""
        self.assertEqual(Components.set_edge_hardness(self.cube, 45), [])

    def test_set_edge_hardness_restores_selection(self):
        """polySoftEdge selects the affected edges as a side-effect — the
        helper must restore the caller's selection so HUD/component-count
        consumers don't see thousands of accidentally-selected edges.
        """
        cmds.select(self.cube, replace=True)
        original = cmds.ls(sl=True, long=True)

        Components.set_edge_hardness(
            self.cube, 45, upper_hardness=0, lower_hardness=180, unlock_normals=True
        )

        restored = cmds.ls(sl=True, long=True) or []
        self.assertEqual(
            restored, original, "selection must be restored to the caller's state"
        )

    def test_set_edge_hardness_resets_locked_normal_values(self):
        """unlock_normals=True must also reset normal *values* — not just the
        lock flag — so polySoftEdge can drive visible shading.

        Repro: Marmoset-exported meshes have per-face-aligned normals BAKED
        and LOCKED. Unlocking alone keeps the baked values (Maya treats them
        as user-set), so polySoftEdge flips the soft flag but shading still
        looks set-to-face.
        """
        # Force per-face-aligned baked normals (simulates FBX-import state)
        cmds.polySetToFaceNormal(self.cube)
        cmds.polyNormalPerVertex(self.cube + ".vtx[*]", freezeNormal=True)

        Components.set_edge_hardness(
            self.cube, 45, upper_hardness=0, lower_hardness=180, unlock_normals=True
        )

        # All cube edges are 90° (>= threshold 45) → hardened → adjacent
        # verts SHOULD stay face-aligned, so this cube is the wrong fixture
        # for "smoothing happened." Instead, run a soften-everything pass:
        Components.set_edge_hardness(
            self.cube, 180, upper_hardness=180, lower_hardness=180, unlock_normals=True
        )

        # After full soften, the cube's 8 corners should each have a single
        # averaged normal (NOT face-aligned). Check vert 0:
        import maya.api.OpenMaya as om

        sel = om.MSelectionList()
        sel.add(self.cube)
        dag = sel.getDagPath(0)
        mesh = om.MFnMesh(dag)
        # Vertex 0 sits at a cube corner; with all edges soft, its normal
        # should be the average of three face normals — pointing roughly
        # toward (±1,±1,±1)/sqrt(3). Definitely not axis-aligned.
        face_normals = []
        for f in range(mesh.numPolygons):
            face_normals.append(mesh.getPolygonNormal(f, om.MSpace.kObject))
        vert_iter = om.MItMeshVertex(dag)
        while not vert_iter.isDone():
            if vert_iter.index() == 0:
                vn = vert_iter.getNormal(om.MSpace.kObject)
                axis_aligned = (
                    (abs(vn.x) > 0.99 and abs(vn.y) < 0.01 and abs(vn.z) < 0.01)
                    or (abs(vn.y) > 0.99 and abs(vn.x) < 0.01 and abs(vn.z) < 0.01)
                    or (abs(vn.z) > 0.99 and abs(vn.x) < 0.01 and abs(vn.y) < 0.01)
                )
                self.assertFalse(
                    axis_aligned,
                    f"vertex 0 normal {vn} is still axis-aligned — values "
                    f"were not recomputed despite unlock_normals=True",
                )
                break
            vert_iter.next()

    def test_average_normals(self):
        """Test averaging normals."""
        Components.average_normals(self.cube)
        pass

    def test_transfer_normals_basic(self):
        """Test transferring normals from source to target mesh.

        Bug: cmds.ls(objects, type='mesh') filtered out transform nodes,
        so the function always raised ValueError. Also, polySoftEdge at
        the end overwrote the transferred normals and the tentacle call
        site passed args incorrectly.
        Fixed: 2026-02-27
        """
        # Create two cubes at the same position (same topology)
        source = cmds.polyCube(name="src_cube")[0]
        target = cmds.polyCube(name="tgt_cube")[0]

        # Rotate source so its normals differ from target
        cmds.rotate(45, 0, 0, source)
        cmds.makeIdentity(source, apply=True, t=1, r=1, s=1, n=0)

        # Get target normals before transfer
        before = [
            cmds.polyNormalPerVertex(f"{target}.vtx[{i}]", q=True, xyz=True)
            for i in range(cmds.polyEvaluate(target, vertex=True))
        ]

        # Transfer normals using transform nodes (the typical user workflow)
        Components.transfer_normals([source, target])

        # Get target normals after transfer
        after = [
            cmds.polyNormalPerVertex(f"{target}.vtx[{i}]", q=True, xyz=True)
            for i in range(cmds.polyEvaluate(target, vertex=True))
        ]

        # Normals should have changed
        changed = any(
            any(abs(a - b) > 0.01 for a, b in zip(bv, av))
            for bv, av in zip(before, after)
        )
        self.assertTrue(changed, "Normals did not change after transfer")

    def test_transfer_normals_rejects_non_mesh(self):
        """Test that transfer_normals raises for non-mesh objects."""
        grp1 = cmds.group(empty=True, name="empty_grp1")
        grp2 = cmds.group(empty=True, name="empty_grp2")
        with self.assertRaises(ValueError):
            Components.transfer_normals([grp1, grp2])

    # -------------------------------------------------------------------------
    # Crease
    # -------------------------------------------------------------------------

    def test_transfer_creased_edges_copies_values(self):
        """Crease values on source edges replicate at matching edge ids on the target."""
        src = cmds.polyCube(name="crease_src")[0]
        tgt = cmds.polyCube(name="crease_tgt")[0]
        try:
            cmds.polyCrease([f"{src}.e[0]", f"{src}.e[3]"], value=5.0)
            cmds.polyCrease(f"{src}.e[7]", value=2.5)

            Components.transfer_creased_edges(src, tgt)

            tgt_values = cmds.polyCrease(f"{tgt}.e[*]", query=True, value=True)
            self.assertEqual(tgt_values[0], 5.0)
            self.assertEqual(tgt_values[3], 5.0)
            self.assertEqual(tgt_values[7], 2.5)
            # Untouched edges remain at the default (-1.0).
            self.assertEqual(tgt_values[1], -1.0)
        finally:
            for n in (src, tgt):
                if cmds.objExists(n):
                    cmds.delete(n)

    def test_transfer_creased_edges_handles_uncreased_source(self):
        """A source with no polyCrease history is a no-op, not an error."""
        src = cmds.polyCube(name="crease_clean_src")[0]
        tgt = cmds.polyCube(name="crease_clean_tgt")[0]
        try:
            Components.transfer_creased_edges(src, tgt)
        finally:
            for n in (src, tgt):
                if cmds.objExists(n):
                    cmds.delete(n)

    # -------------------------------------------------------------------------
    # Topology
    # -------------------------------------------------------------------------

    def test_bridge_connected_edges(self):
        """Test bridging connected edges."""
        try:
            Components.bridge_connected_edges(
                [f"{self.plane}.e[0]", f"{self.plane}.e[1]"]
            )
            self.assertTrue(cmds.polyEvaluate(self.plane, face=True) > 25)
        except Exception as e:
            print(f"Bridge failed: {e}")


class TestComponentsEdgeCases(MayaTkTestCase):
    """Edge case tests for Components class."""

    def test_get_components_empty_object(self):
        """Test getting components from object with no geometry."""
        empty_group = cmds.group(empty=True, name="test_empty_group")
        try:
            result = Components.get_components(empty_group, "vertex")
            self.assertTrue(not result)
        finally:
            if cmds.objExists("test_empty_group"):
                cmds.delete("test_empty_group")

    def test_filter_components_empty_list(self):
        """Test filtering with empty component list."""
        result = Components.filter_components([])
        self.assertEqual(result, [])

    def test_get_shortest_path_invalid(self):
        """Test shortest path with invalid inputs."""
        cube = cmds.polyCube()[0]
        with self.assertRaises(ValueError):
            Components.get_shortest_path([f"{cube}.vtx[0]"])  # Only 1
        cmds.delete(cube)


if __name__ == "__main__":
    unittest.main()
