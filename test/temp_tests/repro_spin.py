import pymel.core as pm
import pythontk as ptk
import numpy as np
import random
import unittest
import logging

logging.basicConfig(level=logging.DEBUG)


class TestSpin(unittest.TestCase):
    def setUp(self):
        pm.newFile(force=True)

    def test_pca_spin(self):
        # Create two identical cylinders
        c1 = pm.polyCylinder(r=1, h=2, sx=20, sy=1, sz=1, ax=(0, 1, 0))[0]
        c2 = pm.polyCylinder(r=1, h=2, sx=20, sy=1, sz=1, ax=(0, 1, 0))[0]

        # Rotate c2 arbitrarily
        # A rotation around Y (symmetry axis) should be handled by PCA if robust
        # But PCA axes are arbitrary in the XZ plane.
        # So we rotate c2 around Y by 45 degrees.
        c2.setRotation([0, 45, 0])

        # Also rotate both in world space to make it harder
        c1.setRotation([10, 20, 30])
        c2.setRotation([10, 65, 30])  # 20 + 45 = 65 around Y (roughly)

        # Bake transforms (simulate separate shells)
        pm.makeIdentity(c1, apply=True, t=1, r=1, s=1, n=0, pn=1)
        pm.makeIdentity(c2, apply=True, t=1, r=1, s=1, n=0, pn=1)

        pts1 = np.array(c1.getShape().getPoints(space="world"))
        pts2 = np.array(c2.getShape().getPoints(space="world"))

        # Try GeometryMatcher
        from mayatk.core_utils.instancing.geometry_matcher import GeometryMatcher

        matcher = GeometryMatcher(tolerance=0.01, verbose=True)

        # Use internal method to test logic directly
        # pts1 and pts2 are numpy arrays
        m_combined = matcher._get_pca_transform_robust(pts1, pts2)

        if m_combined:
            print("Match Found!")
            # Verify
            m = m_combined
            # Transform pts1
            pts1_h = np.hstack([pts1, np.ones((len(pts1), 1))])
            pts1_t = np.dot(pts1_h, np.array(m))[:, :3]

            # Check distance
            from scipy.spatial import KDTree

            tree = KDTree(pts2)
            dists, _ = tree.query(pts1_t, k=1)
            max_dist = np.max(dists)
            print(f"Max Dist: {max_dist}")
            self.assertLess(max_dist, 0.01)
        else:
            print("No Match Found")
            self.fail("PCA failed to match rotated cylinders")


if __name__ == "__main__":
    unittest.main()
