import sys
import os

# Add paths
sys.path.append(r"o:\Cloud\Code\_scripts\pythontk")
sys.path.append(r"o:\Cloud\Code\_scripts\mayatk")

import maya.standalone

maya.standalone.initialize()

import pymel.core as pm
import numpy as np
from pythontk import MathUtils


def run():
    print("Creating cylinder...")
    c1 = pm.polyCylinder(r=1, h=2, sx=20, sy=1, sz=1, ax=[0, 1, 0])[0]

    # Get points
    pts1 = np.array([[p.x, p.y, p.z] for p in c1.getShape().getPoints(space="world")])

    print("Creating rotated cylinder...")
    c2 = pm.polyCylinder(r=1, h=2, sx=20, sy=1, sz=1, ax=[0, 1, 0])[0]
    # Rotate by 9 degrees around Y (symmetry axis)
    c2.setRotation([0, 9, 0])

    # Bake rotation (simulate combined mesh separation)
    # Actually, getPoints(space='world') gets the transformed points, effectively baked.
    pts2 = np.array([[p.x, p.y, p.z] for p in c2.getShape().getPoints(space="world")])

    print(f"Pts1 shape: {pts1.shape}")
    print(f"Pts2 shape: {pts2.shape}")

    print("Calculating PCA transform...")
    matrix = MathUtils.get_pca_transform(pts1, pts2, tolerance=0.001)

    if matrix:
        print("SUCCESS: Transform found!")
        print(matrix)

    # Debug eigenvalues
    c_a = np.mean(pts1, axis=0)
    p_a = pts1 - c_a
    cov_a = np.cov(p_a, rowvar=False)
    val_a, vec_a = np.linalg.eigh(cov_a)
    print(f"Eigenvalues A: {val_a}")

    c_b = np.mean(pts2, axis=0)
    p_b = pts2 - c_b
    cov_b = np.cov(p_b, rowvar=False)
    val_b, vec_b = np.linalg.eigh(cov_b)
    print(f"Eigenvalues B: {val_b}")

    if not matrix:
        print("FAILURE: No transform found.")

    # Clean up
    pm.delete(c1, c2)


if __name__ == "__main__":
    run()
