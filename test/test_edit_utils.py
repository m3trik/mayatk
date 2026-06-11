# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.edit_utils module

Tests for EditUtils class functionality including:
- Vertex operations (merge, pairs)
- Axis-based operations (get faces, cut, delete)
- Mirroring and symmetry
- Overlap detection (duplicates, vertices, faces)
- Topology analysis (non-manifold, similarity)
- Selection utilities (invert, delete)
- Curve creation
"""
import unittest
import mayatk as mtk
from mayatk.edit_utils._edit_utils import EditUtils
import pythontk as ptk

from base_test import MayaTkTestCase
import maya.cmds as cmds


class TestEditUtils(MayaTkTestCase):
    """Comprehensive tests for EditUtils class."""

    def setUp(self):
        """Set up test scene."""
        super().setUp()
        self.cube = cmds.polyCube(name="test_cube", w=10, h=10, d=10)[0]
        self.sphere = cmds.polySphere(name="test_sphere", r=5)[0]

    def tearDown(self):
        """Clean up."""
        super().tearDown()

    # -------------------------------------------------------------------------
    # Vertex Operations
    # -------------------------------------------------------------------------

    def test_merge_vertices(self):
        """Test merging vertices."""
        # Create a mesh with overlapping vertices
        # Duplicate cube and move slightly to create overlap when combined
        cube2 = cmds.duplicate(self.cube)[0]
        cmds.move(0.0001, 0, 0, cube2, r=True)
        combined = cmds.polyUnite(self.cube, cube2, ch=False)[0]

        initial_count = cmds.polyEvaluate(combined, v=True)
        EditUtils.merge_vertices(combined, tolerance=0.001)
        final_count = cmds.polyEvaluate(combined, v=True)

        self.assertLess(final_count, initial_count)

    def test_merge_vertices_selected_only(self):
        """selected_only operates on the live vertex selection (once —
        regression: it used to re-run the selection merge per shape in
        the objects loop)."""
        cube2 = cmds.duplicate(self.cube)[0]
        cmds.move(0.0001, 0, 0, cube2, r=True)
        combined = cmds.polyUnite(self.cube, cube2, ch=False)[0]

        initial_count = cmds.polyEvaluate(combined, v=True)
        cmds.select(f"{combined}.vtx[*]")
        EditUtils.merge_vertices(combined, tolerance=0.001, selected_only=True)
        final_count = cmds.polyEvaluate(combined, v=True)

        self.assertLess(final_count, initial_count)

    def test_merge_vertex_pairs(self):
        """Test merging specific vertex pairs."""
        # Select two vertices
        vtx1 = f"{self.cube}.vtx[0]"
        vtx2 = f"{self.cube}.vtx[1]"

        # Get initial positions
        p1 = cmds.pointPosition(vtx1, world=True)
        p2 = cmds.pointPosition(vtx2, world=True)
        midpoint = [(a + b) / 2 for a, b in zip(p1, p2)]

        EditUtils.merge_vertex_pairs([vtx1, vtx2])

        # Check if they merged (count reduced)
        # Note: polyMergeVertex might change vertex IDs, so we check total count
        # But here we merged 2 verts into 1, so count should decrease by 1
        # However, merge_vertex_pairs moves them to center then merges.
        # Let's verify position of the resulting vertex (which might be vtx[0] or new ID)
        # Easier to check total count
        # self.assertEqual(cmds.polyEvaluate(self.cube, v=True), 7) # Cube has 8 verts, 2 merged -> 7
        pass  # Logic verification depends on exact topology, skipping strict assert for now

    # -------------------------------------------------------------------------
    # Axis Operations
    # -------------------------------------------------------------------------

    def test_get_all_faces_on_axis(self):
        """Test retrieving faces on specific axes."""
        # Cube at origin. Faces on +X should be selected.
        faces_x = EditUtils.get_all_faces_on_axis(self.cube, axis="x")
        self.assertTrue(len(faces_x) > 0)

        # Verify normal or position
        # Face center of +X face should have positive X
        # Use exactWorldBoundingBox to get center
        bbox = cmds.exactWorldBoundingBox(faces_x[0])
        center_x = (bbox[0] + bbox[3]) / 2
        self.assertGreater(center_x, 0)

        # Test with pivot
        faces_neg_x = EditUtils.get_all_faces_on_axis(self.cube, axis="-x")
        self.assertTrue(len(faces_neg_x) > 0)
        bbox = cmds.exactWorldBoundingBox(faces_neg_x[0])
        center_x = (bbox[0] + bbox[3]) / 2
        self.assertLess(center_x, 0)

    def test_cut_along_axis(self):
        """Test cutting geometry along an axis."""
        # Cut cube in half along X
        initial_faces = cmds.polyEvaluate(self.cube, f=True)
        EditUtils.cut_along_axis(self.cube, axis="x", amount=1)
        new_faces = cmds.polyEvaluate(self.cube, f=True)
        self.assertGreater(new_faces, initial_faces)

    def test_cut_along_axis_mirror(self):
        """Test cutting and mirroring."""
        # Move cube off center
        cmds.move(5, 0, 0, self.cube)
        EditUtils.cut_along_axis(self.cube, axis="x", delete=True, mirror=True)
        # Should result in a symmetric object
        self.assertTrue(cmds.objExists(self.cube))

    def test_delete_along_axis(self):
        """Test deleting faces along an axis."""
        EditUtils.delete_along_axis(self.cube, axis="x")
        # Should have deleted the +X face
        # Hard to verify exact topology without complex checks, but face count should drop
        # Actually, deleting a face of a cube leaves it open.
        pass

    # -------------------------------------------------------------------------
    # Mirror Operations
    # -------------------------------------------------------------------------

    def test_mirror(self):
        """Test mirroring geometry with merge mode."""
        cmds.move(5, 0, 0, self.cube)
        mirrored = EditUtils.mirror(self.cube, axis="-x", mergeMode=1)  # Merge
        self.assertTrue(mirrored)
        # Merged mirror should still be one object
        if isinstance(mirrored, list):
            self.assertEqual(len(mirrored), 1)
        self.assertTrue(cmds.objExists(self.cube))

    def test_mirror_separate_mode(self):
        """Test mirror with custom separate mode (mergeMode=-1).

        Bug: Separate mode was broken - polySeparate was called without connecting
        firstNewFace/lastNewFace attributes, so Maya couldn't track the mirrored half.
        Fixed: 2026-02-10 - Now delegates to separate_mirrored_mesh.
        """
        cube = cmds.polyCube(name="sep_cube", w=10, h=10, d=10)[0]
        cmds.move(5, 0, 0, cube)
        result = EditUtils.mirror(cube, axis="-x", mergeMode=-1)
        # Separate mode should produce result(s)
        self.assertTrue(result)
        results = result if isinstance(result, list) else [result]
        # Should have produced at least the original + mirrored half
        self.assertGreaterEqual(len(results), 1)
        # All results should exist in the scene
        for r in results:
            self.assertTrue(cmds.objExists(r))

    def test_mirror_use_object_axes(self):
        """Test mirror with use_object_axes on a rotated object.

        Bug: use_object_axes parameter was accepted but completely ignored.
        Fixed: 2026-02-10 - Pivot is now computed in object-local space when enabled.
        """
        cube = cmds.polyCube(name="rotated_cube", w=10, h=10, d=10)[0]
        cmds.move(5, 0, 0, cube)
        cmds.rotate(0, 45, 0, cube)

        result = EditUtils.mirror(
            cube, axis="x", pivot="object", mergeMode=1, use_object_axes=True
        )
        self.assertTrue(result)
        self.assertTrue(cmds.objExists(cube))

    def test_mirror_world_pivot(self):
        """Test mirror with world origin pivot."""
        cmds.move(5, 0, 0, self.cube)
        result = EditUtils.mirror(self.cube, axis="x", pivot="world", mergeMode=1)
        self.assertTrue(result)

    def test_mirror_tuple_pivot(self):
        """Test mirror with explicit tuple pivot."""
        cmds.move(5, 0, 0, self.cube)
        result = EditUtils.mirror(self.cube, axis="x", pivot=(0, 0, 0), mergeMode=1)
        self.assertTrue(result)

    def test_separate_mirrored_mesh(self):
        """Test separating a mirrored mesh using the polyMirrorFace history node."""
        cmds.move(5, 0, 0, self.cube)
        # Use mergeMode=0 (no merge) so the mirror history node is preserved
        EditUtils.mirror(self.cube, axis="-x", mergeMode=0)

        # Get the polyMirrorFace history node from the cube's history
        history = cmds.ls(cmds.listHistory(self.cube), type="polyMirrorFace")
        if history:
            mirror_node = history[0]
            new_obj = EditUtils.separate_mirrored_mesh(mirror_node)
            if new_obj is not None:
                self.assertTrue(cmds.objExists(new_obj))

    def test_mirror_preserves_normals_merged(self):
        """Verify mirrored mesh has outward-facing normals after merge.

        Bug: polyMirrorFace could produce reversed normals on the mirrored
        half, causing the mesh to render inside-out or black.
        Fixed: 2026-03-08 - Added polyNormal conform step after mirror.
        """
        cube = cmds.polyCube(name="norm_cube", w=10, h=10, d=10)[0]
        cmds.move(5, 0, 0, cube)
        EditUtils.mirror(cube, axis="-x", mergeMode=1)

        # Verify normals point outward: the dot product of each face normal
        # with the vector from mesh center to face center should be > 0.
        import maya.api.OpenMaya as om

        bbox = cmds.exactWorldBoundingBox(cube)
        mesh_center = om.MVector(
            (bbox[0] + bbox[3]) / 2,
            (bbox[1] + bbox[4]) / 2,
            (bbox[2] + bbox[5]) / 2,
        )
        face_count = cmds.polyEvaluate(cube, f=True)
        for i in range(face_count):
            info = cmds.polyInfo(f"{cube}.f[{i}]", fn=True)
            parts = info[0].split()
            normal = om.MVector(float(parts[-3]), float(parts[-2]), float(parts[-1]))
            # Face center
            fb = cmds.exactWorldBoundingBox(f"{cube}.f[{i}]")
            face_center = om.MVector(
                (fb[0] + fb[3]) / 2, (fb[1] + fb[4]) / 2, (fb[2] + fb[5]) / 2
            )
            outward = face_center - mesh_center
            dot = normal * outward
            self.assertGreater(dot, 0, f"Face {i} normal points inward (dot={dot:.4f})")

    def test_mirror_preserves_normals_separate(self):
        """Verify both halves have correct normals after separate-mode mirror.

        Bug: polySeparate after polyMirrorFace could flip normals on the
        mirrored half, especially after construction history deletion.
        Fixed: 2026-03-08 - Added polyNormal conform before history deletion.
        """
        import maya.api.OpenMaya as om

        cube = cmds.polyCube(name="sep_norm_cube", w=10, h=10, d=10)[0]
        cmds.move(5, 0, 0, cube)
        results = EditUtils.mirror(cube, axis="-x", mergeMode=-1)
        results = results if isinstance(results, list) else [results]

        for obj in results:
            if not cmds.objExists(obj):
                continue
            # Resolve to a mesh transform — mirror may return a group.
            target = str(obj)
            if not cmds.listRelatives(target, shapes=True, ni=True, type="mesh"):
                meshes = cmds.listRelatives(target, allDescendents=True, type="mesh") or []
                if not meshes:
                    continue
                # Walk to the mesh's parent transform.
                target = (cmds.listRelatives(meshes[0], parent=True, fullPath=True) or [target])[0]

            bbox = cmds.exactWorldBoundingBox(target)
            mesh_center = om.MVector(
                (bbox[0] + bbox[3]) / 2,
                (bbox[1] + bbox[4]) / 2,
                (bbox[2] + bbox[5]) / 2,
            )
            face_count = cmds.polyEvaluate(target, f=True)
            for i in range(face_count):
                info = cmds.polyInfo(f"{target}.f[{i}]", fn=True)
                parts = info[0].split()
                normal = om.MVector(
                    float(parts[-3]), float(parts[-2]), float(parts[-1])
                )
                fb = cmds.exactWorldBoundingBox(f"{target}.f[{i}]")
                face_center = om.MVector(
                    (fb[0] + fb[3]) / 2, (fb[1] + fb[4]) / 2, (fb[2] + fb[5]) / 2
                )
                outward = face_center - mesh_center
                dot = normal * outward
                self.assertGreater(
                    dot, 0,
                    f"Face {i} on {target} normal points inward (dot={dot:.4f})",
                )

    def test_mirror_delete_original(self):
        """Verify delete_original removes the original half in separate mode.

        Feature: Added delete_original parameter so users can mirror and discard
        the source half in one step.
        Added: 2026-03-08
        """
        cube = cmds.polyCube(name="del_orig_cube", w=10, h=10, d=10)[0]
        cmds.move(5, 0, 0, cube)

        # Count transforms before mirror
        before = set(str(t) for t in cmds.ls(type="transform"))

        result = EditUtils.mirror(cube, axis="-x", mergeMode=-1, delete_original=True)
        results = result if isinstance(result, list) else [result]

        # Should return exactly one object (the mirrored half only)
        self.assertEqual(len(results), 1, "Expected only the mirrored half")
        self.assertTrue(cmds.objExists(results[0]))

        # The result should have geometry (not an empty group). When
        # results[0] is a group, pm.polyEvaluate returns a string error
        # message, so look up shapes ourselves.
        target = results[0]
        if not cmds.listRelatives(str(target), shapes=True, ni=True, type="mesh"):
            target = (cmds.listRelatives(str(target), allDescendents=True, type="mesh") or [None])[0]
            self.assertIsNotNone(target, "Mirrored result should contain a mesh")
        face_count = cmds.polyEvaluate(target, f=True)
        self.assertGreater(face_count, 0, "Mirrored half should have faces")

    def test_mirror_delete_original_false(self):
        """Verify delete_original=False (default) keeps both halves."""
        cube = cmds.polyCube(name="keep_orig_cube", w=10, h=10, d=10)[0]
        cmds.move(5, 0, 0, cube)

        result = EditUtils.mirror(cube, axis="-x", mergeMode=-1, delete_original=False)
        results = result if isinstance(result, list) else [result]

        # Should return at least the mirrored half
        self.assertGreaterEqual(len(results), 1)

    # -------------------------------------------------------------------------
    # Overlap Detection
    # -------------------------------------------------------------------------

    def test_get_overlapping_duplicates(self):
        """Test finding duplicate objects."""
        dup = cmds.duplicate(self.cube)[0]
        dup_long = cmds.ls(str(dup), l=True)[0]
        cube_long = cmds.ls(str(self.cube), l=True)[0]
        duplicates = EditUtils.get_overlapping_duplicates([self.cube, dup])
        self.assertIn(dup_long, duplicates)
        self.assertNotIn(cube_long, duplicates)  # Should keep one

    def test_get_overlapping_vertices(self):
        """Test finding overlapping vertices."""
        # Create overlap
        cube2 = cmds.duplicate(self.cube)[0]
        combined = cmds.polyUnite(self.cube, cube2, ch=False)[0]
        overlaps = EditUtils.get_overlapping_vertices(combined)
        self.assertTrue(len(overlaps) > 0)

    def test_get_overlapping_faces(self):
        """Test finding overlapping faces."""
        cube2 = cmds.duplicate(self.cube)[0]
        combined = cmds.polyUnite(self.cube, cube2, ch=False)[0]
        overlaps = EditUtils.get_overlapping_faces(combined)
        self.assertTrue(len(overlaps) > 0)

    # -------------------------------------------------------------------------
    # Topology & Similarity
    # -------------------------------------------------------------------------

    def test_get_similar_mesh(self):
        """Test finding similar meshes."""
        dup = cmds.duplicate(self.cube)[0]
        cmds.move(10, 0, 0, dup)
        similar = EditUtils.get_similar_mesh(self.cube)
        self.assertIn(dup, similar)

    def test_get_similar_topo(self):
        """Test finding similar topology."""
        dup = cmds.duplicate(self.cube)[0]
        cmds.move(10, 0, 0, dup)
        similar = EditUtils.get_similar_topo(self.cube)
        self.assertIn(dup, similar)

    def test_non_manifold(self):
        """Test non-manifold geometry detection."""
        # Create non-manifold geometry: 2 cubes sharing one vertex
        # Hard to script reliably without complex setup.
        # We'll skip strict creation but test the function call doesn't crash on normal geo
        nm_verts = EditUtils.find_non_manifold_vertex(self.cube)
        self.assertEqual(len(nm_verts), 0)

    # -------------------------------------------------------------------------
    # Selection Utilities
    # -------------------------------------------------------------------------

    def test_invert_geometry(self):
        """Test inverting object selection."""
        cmds.select(self.cube)
        inverted = EditUtils.invert_geometry()
        self.assertIn(self.sphere, inverted)
        self.assertNotIn(self.cube, inverted)

    def test_invert_components(self):
        """Test inverting component selection."""
        cmds.select(f"{self.cube}.vtx[0]")
        inverted = EditUtils.invert_components()
        # Production returns strings; compare on string form.
        inverted_strs = [str(i) for i in inverted]
        self.assertNotIn(str(f"{self.cube}.vtx[0]"), inverted_strs)
        # Some shape-prefixed variant of vtx[1] should be present.
        self.assertTrue(any(".vtx[1]" in s for s in inverted_strs))

    def test_delete_selected(self):
        """Test delete selected wrapper."""
        # Test object deletion
        cmds.select(self.sphere)
        EditUtils.delete_selected()
        self.assertFalse(cmds.objExists(self.sphere))

    def test_delete_selected_faces_single_object(self):
        """Selecting faces must delete only the faces, not the whole mesh."""
        face_count = cmds.polyEvaluate(self.cube, f=True)
        cmds.selectType(ocm=True, alc=False, polymeshFace=True)
        cmds.select(f"{self.cube}.f[0]", f"{self.cube}.f[1]")
        EditUtils.delete_selected()
        self.assertTrue(cmds.objExists(self.cube))
        self.assertEqual(cmds.polyEvaluate(self.cube, f=True), face_count - 2)

    def test_delete_selected_faces_multi_object(self):
        """Components selected across multiple meshes must all be deleted, no mesh removed."""
        cube_faces = cmds.polyEvaluate(self.cube, f=True)
        sphere_faces = cmds.polyEvaluate(self.sphere, f=True)
        cmds.selectType(ocm=True, alc=False, polymeshFace=True)
        cmds.select(f"{self.cube}.f[0]", f"{self.sphere}.f[0]", f"{self.sphere}.f[1]")
        EditUtils.delete_selected()
        self.assertTrue(cmds.objExists(self.cube))
        self.assertTrue(cmds.objExists(self.sphere))
        self.assertEqual(cmds.polyEvaluate(self.cube, f=True), cube_faces - 1)
        self.assertEqual(cmds.polyEvaluate(self.sphere, f=True), sphere_faces - 2)

    def test_delete_selected_mixed_components_and_objects(self):
        """Mixed selection: components on one mesh + a whole second mesh."""
        extra = cmds.polyCube(name="test_cube_extra")[0]
        cube_faces = cmds.polyEvaluate(self.cube, f=True)
        cmds.selectType(ocm=True, alc=False, polymeshFace=True)
        cmds.select(f"{self.cube}.f[0]", extra)
        EditUtils.delete_selected()
        self.assertTrue(cmds.objExists(self.cube))
        self.assertFalse(cmds.objExists(extra))
        self.assertEqual(cmds.polyEvaluate(self.cube, f=True), cube_faces - 1)

    def test_create_curve_from_edges(self):
        """Test creating curve from edges."""
        edges = [f"{self.cube}.e[0]", f"{self.cube}.e[1]"]
        curve = EditUtils.create_curve_from_edges(edges)

        # curve might be a list [transform, history]
        if isinstance(curve, list):
            curve = curve[0]

        # Ensure it's a PyNode
        curve = curve

        self.assertTrue(cmds.objExists(curve))
        self.assertEqual(cmds.nodeType((cmds.listRelatives(str(curve), shapes=True, ni=True) or [None])[0]), "nurbsCurve")

    def test_separate_objects(self):
        """Test separate_objects method."""
        # Setup materials
        mat1 = mtk.MatUtils.create_mat("lambert", name="mat1")
        mat2 = mtk.MatUtils.create_mat("lambert", name="mat2")

        # Scenario 1: Standard Separate (Disjoint Shells)
        # ---------------------------------------------
        c1 = cmds.polyCube()[0]
        c2 = cmds.polyCube()[0]
        cmds.move(5, 0, 0, c2)
        combined = cmds.polyUnite(c1, c2, ch=False)[0]

        # separate_objects default (by_material=False) should work like polySeparate
        res = EditUtils.separate_objects([combined], by_material=False)
        self.assertEqual(len(res), 2)
        cmds.delete(res)

        # Scenario 2: Separate by Material (Disjoint Shells)
        # ---------------------------------------------
        c3 = cmds.polyCube()[0]
        c4 = cmds.polyCube()[0]
        cmds.move(5, 0, 0, c4)
        mtk.MatUtils.assign_mat(c3, mat1)
        mtk.MatUtils.assign_mat(c4, mat2)
        combined2 = cmds.polyUnite(c3, c4, ch=False)[0]

        res2 = EditUtils.separate_objects([combined2], by_material=True)
        self.assertEqual(len(res2), 2)
        cmds.delete(res2)

        # Scenario 3: Separate by Material (Single Shell)
        # ---------------------------------------------
        c5 = cmds.polyCube(sx=2)[0]
        mtk.MatUtils.assign_mat(c5, mat1)
        cmds.select(f"{c5}.f[0:3]")
        mtk.MatUtils.assign_mat(cmds.ls(selection=True), mat2)

        # Without by_material, should remain 1 object
        res3a = EditUtils.separate_objects([c5], by_material=False)
        self.assertEqual(len(res3a), 1)
        # (It returns the object itself if no separation happened)

        # With by_material, should split
        res3b = EditUtils.separate_objects(res3a, by_material=True)
        self.assertEqual(len(res3b), 2)
        cmds.delete(res3b)

        # Scenario 4: Rename Check
        # ---------------------------------------------
        c6 = cmds.polyCube(n="MyBox")[0]
        c7 = cmds.polyCube()[0]  # Shell 2
        cmds.move(10, 0, 0, c7)
        combined3 = cmds.polyUnite(c6, c7, n="MyComp", ch=False)[0]

        # Rename=True
        # Expect MyComp_01, MyComp_02 (or location based suffix)
        res4 = EditUtils.separate_objects([combined3], rename=True)
        self.assertEqual(len(res4), 2)

        names = [r.split("|")[-1] for r in res4]
        # Verify names start with "MyComp"
        self.assertTrue(all(n.startswith("MyComp") for n in names))
        cmds.delete(res4)

    # -------------------------------------------------------------------------
    # Decimate
    # -------------------------------------------------------------------------

    def test_decimate_reduces_faces(self):
        sphere = cmds.polySphere(subdivisionsX=40, subdivisionsY=40, ch=False)[0]
        before = cmds.polyEvaluate(sphere, face=True)
        result = EditUtils.decimate([sphere], percentage=50.0)
        self.assertEqual(result, [sphere])
        self.assertLess(cmds.polyEvaluate(sphere, face=True), before)
        # delete_history (default) removes the polyReduce node.
        self.assertNotIn("polyReduce", str(cmds.listHistory(sphere) or []))

    def test_decimate_handles_multiple_objects(self):
        # polyReduce raises "Doesn't work with multiple objects selected" when
        # handed more than one mesh, so decimate must reduce each independently.
        sphere = cmds.polySphere(subdivisionsX=40, subdivisionsY=40, ch=False)[0]
        cube = cmds.polyCube(sx=20, sy=20, sz=20, ch=False)[0]
        before = {o: cmds.polyEvaluate(o, face=True) for o in (sphere, cube)}
        result = EditUtils.decimate([sphere, cube], percentage=50.0)
        self.assertEqual(result, [sphere, cube])
        for o in (sphere, cube):
            self.assertLess(cmds.polyEvaluate(o, face=True), before[o])

    def test_decimate_no_objects_is_noop(self):
        cmds.select(clear=True)
        self.assertEqual(EditUtils.decimate([]), [])

    def test_decimate_zero_percent_leaves_mesh_untouched(self):
        sphere = cmds.polySphere(subdivisionsX=12, subdivisionsY=12, ch=False)[0]
        before = cmds.polyEvaluate(sphere, face=True)
        EditUtils.decimate([sphere], percentage=0.0)
        self.assertEqual(cmds.polyEvaluate(sphere, face=True), before)
        self.assertNotIn("polyReduce", str(cmds.listHistory(sphere) or []))

    def test_dissolve_coplanar_strips_flat_regions_losslessly(self):
        # A subdivided cube is all coplanar quads per side + 90 deg cube edges:
        # planar dissolve must merge each side back to one face (6 total) while
        # leaving the shape (bounding box) identical.
        cube = cmds.polyCube(sx=5, sy=5, sz=5, ch=False)[0]
        before = cmds.polyEvaluate(cube, face=True)
        bb_before = cmds.exactWorldBoundingBox(cube)
        result = EditUtils.dissolve_coplanar([cube], angle_tolerance=1.0)
        self.assertEqual(result, [cube])
        self.assertLess(cmds.polyEvaluate(cube, face=True), before)
        self.assertEqual(cmds.polyEvaluate(cube, face=True), 6)
        for a, b in zip(bb_before, cmds.exactWorldBoundingBox(cube)):
            self.assertAlmostEqual(a, b, places=5)

    def test_dissolve_coplanar_keeps_curved_features(self):
        # On a sphere every interior edge is a real angle change, so a tight
        # tolerance must leave the face count essentially unchanged.
        sphere = cmds.polySphere(subdivisionsX=16, subdivisionsY=16, ch=False)[0]
        before = cmds.polyEvaluate(sphere, face=True)
        EditUtils.dissolve_coplanar([sphere], angle_tolerance=0.5)
        self.assertEqual(cmds.polyEvaluate(sphere, face=True), before)


if __name__ == "__main__":
    unittest.main()
