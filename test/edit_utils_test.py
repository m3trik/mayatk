# !/usr/bin/python
# coding=utf-8
import unittest
import pymel.core as pm
import mayatk as mtk


class EditUtilsTest(unittest.TestCase):
    @classmethod
    def setUp(self):
        """Set up test scene once for all tests."""
        pm.mel.file(new=True, force=True)
        self.cube1 = pm.polyCube(
            width=5,
            height=5,
            depth=5,
            subdivisionsX=1,
            subdivisionsY=1,
            subdivisionsZ=1,
            name="cube1",
        )[0]
        self.cube2 = pm.polyCube(
            width=2,
            height=4,
            depth=8,
            subdivisionsX=3,
            subdivisionsY=3,
            subdivisionsZ=3,
            name="cube2",
        )[0]
        self.cyl = pm.polyCylinder(
            radius=5,
            height=10,
            subdivisionsX=12,
            subdivisionsY=1,
            subdivisionsZ=1,
            name="cyl",
        )[0]

    def test_rename(self):
        """Test renaming objects."""
        mtk.rename("cube1", "newName")
        self.assertTrue(pm.objExists("newName"))
        mtk.rename("newName", "cube1")
        self.assertTrue(pm.objExists("cube1"))

    def test_set_case(self):
        """Test setting the case of object names."""
        mtk.set_case("cube1", "upper")
        self.assertTrue(pm.objExists("CUBE1"))

    def test_append_location_based_suffix(self):
        """Test appending location-based suffixes to object names."""
        # Assuming the function name was incorrect and correcting it to match the function call.
        mtk.append_location_based_suffix(["cube1", "cube2"])
        self.assertTrue(pm.objExists("cube_0"))
        self.assertTrue(pm.objExists("cube_1"))

    def test_snap_closest_verts(self):
        """Test snapping closest vertices."""
        mtk.snap_closest_verts("cube1", "cube2")
        # Assuming the function modifies the geometry, so no specific return value to check.

    def test_merge_vertices(self):
        """Test merging vertices."""
        mtk.merge_vertices("cube1")
        # Assuming the function modifies the geometry, so no specific return value to check.

    def test_get_all_faces_on_axis(self):
        """Test getting all faces on a specified axis."""
        pm.setAttr("polyCube1.subdivisionsWidth", 2)
        # Test with cube moved from origin.
        pm.move(10, 10, 10, r=True, os=True, wd=True)

        result = mtk.get_all_faces_on_axis("cube1")
        self.assertEqual(
            [str(f) for f in pm.ls(result)],
            [
                "cube1Shape.f[1]",
                "cube1Shape.f[3]",
                "cube1Shape.f[5]",
                "cube1Shape.f[7]",
                "cube1Shape.f[8]",
            ],
        )

    def test_cut_along_axis(self):
        """Test cutting geometry along a specified axis."""

        # Ensure we start with the initial cube face count
        initial_face_count = pm.polyEvaluate(self.cube1, face=True)

        # Perform the cut operation along the 'x' axis without deletion
        mtk.cut_along_axis(self.cube1, axis="x", delete=False)

        # Verify that the cut operation increased the face count
        new_face_count = pm.polyEvaluate(self.cube1, face=True)
        self.assertTrue(
            new_face_count > initial_face_count, "Cut did not increase face count."
        )

        # Perform another cut operation along the 'x' axis with deletion
        mtk.cut_along_axis(self.cube1, axis="x", delete=True)

        # Verify that the cut operation has decreased the face count (due to deletion)
        final_face_count = pm.polyEvaluate(self.cube1, face=True)
        self.assertTrue(
            final_face_count < new_face_count,
            "Cut with deletion did not decrease face count.",
        )

    def test_delete_along_axis(self):
        """Test deleting geometry along a specified axis."""
        pm.setAttr("polyCube1.subdivisionsWidth", 2)

        mtk.delete_along_axis("cube1")
        # Assuming the function modifies the geometry, so no specific return value to check.

    def test_clean_geometry(self):
        """Test cleaning geometry."""
        mtk.clean_geometry("cyl")
        # Assuming the function modifies the geometry, so no specific return value to check.

    def test_get_overlapping_dup_objects(self):
        """Test getting overlapping duplicate objects."""
        result = mtk.get_overlapping_duplicates(["cyl", "cube1", "cube2"])
        self.assertEqual(result, set())

    def test_find_non_manifold_vertex(self):
        """Test finding non-manifold vertices."""
        result = mtk.find_non_manifold_vertex("cyl")
        self.assertEqual(result, set())

    def test_split_non_manifold_vertex(self):
        """Test splitting non-manifold vertices."""
        mtk.split_non_manifold_vertex("cyl")
        # Assuming the function modifies the geometry, so no specific return value to check.

    def test_get_ngons(self):
        """Test getting n-gons."""
        result = mtk.get_ngons("cyl")
        self.assertEqual(result, [])

    def test_get_overlapping_vertices(self):
        """Test getting overlapping vertices."""
        result = mtk.get_overlapping_vertices("cyl")
        self.assertEqual(result, [])

    def test_get_overlapping_faces(self):
        """Test getting overlapping faces."""
        result = mtk.get_overlapping_faces("cyl")
        self.assertEqual(result, [])
        result = mtk.get_overlapping_faces("cyl.f[:]")
        self.assertEqual(result, [])

    def test_get_similar_mesh(self):
        """Test finding similar mesh."""
        result = mtk.get_similar_mesh("cyl")
        self.assertEqual(result, [])

    def test_get_similar_topo(self):
        """Test finding objects with similar topology."""
        result = mtk.get_similar_topo("cyl")
        self.assertEqual(result, [])


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import importlib

    importlib.reload(mtk.edit_utils)
    mtk.clear_scroll_field_reporter()

    # Create a Test Suite
    suite = unittest.TestSuite()

    # Add the test case class to the suite
    suite.addTest(unittest.makeSuite(EditUtilsTest))

    # Run the suite
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
