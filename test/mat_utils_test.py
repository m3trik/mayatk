# !/usr/bin/python
# coding=utf-8
import unittest
import importlib
import pymel.core as pm
from mayatk.mat_utils import mat_utils

importlib.reload(mat_utils)


class MatTest(unittest.TestCase, mat_utils.Mat):
    def test_get_mats(self):
        """Test the get_mats method."""
        # Create a sphere and assign a lambert material to it
        sphere = pm.polySphere()[0]
        lambert = pm.shadingNode("lambert", asShader=True)
        pm.select(sphere)
        pm.hyperShade(assign=lambert)

        # Get the materials of the sphere
        mats = self.get_mats(sphere)

        # Print materials for debug
        print(f"Materials of the sphere: {mats}")

        # Check if the lambert material is in the materials of the sphere
        self.assertIn(lambert, mats)

        # Get the materials of a face of the sphere
        face = sphere.f[0]
        face_mats = self.get_mats(face)

        # Print materials for debug
        print(f"Materials of the face: {face_mats}")

        # Check if the lambert material is in the materials of the face
        self.assertIn(lambert, face_mats)

    def test_get_scene_mats(self):
        """Test the get_scene_mats method."""
        # Create a lambert material
        lambert = pm.shadingNode("lambert", asShader=True)

        # Get all materials in the scene
        scene_mats = self.get_scene_mats()

        # Check if the lambert material is in the scene materials
        self.assertIn(lambert, scene_mats)

    def test_get_fav_mats(self):
        """Test the get_fav_mats method."""
        # Get the favorite materials
        fav_mats = self.get_fav_mats()

        # Check if the favorite materials are not None
        self.assertIsNotNone(fav_mats)

    def test_create_random_mat(self):
        """Test the create_random_mat method."""
        # Create a random material
        random_mat = self.create_random_mat()

        # Check if the random material is not None
        self.assertIsNotNone(random_mat)

    def test_assign_mat(self):
        """Test the assign_mat method."""
        # Create a sphere
        sphere = pm.polySphere()[0]

        # Create a lambert material
        lambert = pm.shadingNode("lambert", asShader=True)

        # Assign the lambert material to the sphere
        mat_utils.Mat.assign_mat([sphere], lambert)

        # Print the materials of the sphere after assignment
        print(
            "Materials after assignment:",
            pm.ls(pm.listConnections(sphere), materials=True),
        )

        # Get the materials of the sphere
        mats = list(mat_utils.Mat.get_mats(sphere))

        # Print the materials retrieved by get_mats
        print("Materials from get_mats:", mats)

        # Check that the lambert material is in the materials of the sphere
        self.assertIn(lambert, mats)

    def test_find_by_mat_id(self):
        cube = pm.polyCube()[0]
        lambert = pm.shadingNode("lambert", asShader=True)

        # # Test that find_by_mat_id raises error with multi-material
        # multi_mat = pm.createNode("VRayMultiSubTex")
        # with self.assertRaises(TypeError):
        #     self.find_by_mat_id(multi_mat, objects=[cube])

        # Assign lambert material to face 0 of the cube
        self.assign_mat([f"{cube.getShape().name()}.f[0]"], lambert)

        # Test that find_by_mat_id finds the face when no objects are specified
        objs = self.find_by_mat_id(lambert)
        objs_as_strings = [obj.name() for obj in objs]
        self.assertIn(f"{cube.getShape().name()}.f[0]", objs_as_strings)

        # Test that find_by_mat_id finds the face from the given objects
        objs = self.find_by_mat_id(lambert, objects=[cube])
        objs_as_strings = [obj.name() for obj in objs]
        self.assertIn(f"{cube.getShape().name()}.f[0]", objs_as_strings)

        # Test that find_by_mat_id returns complete objects when shell is True
        objs = self.find_by_mat_id(lambert, shell=True)
        self.assertIn(cube.getShape(), objs)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(exit=False)


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------


# Deprecated ---------------------
