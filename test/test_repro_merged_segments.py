import maya.standalone

maya.standalone.initialize(name="python")

import sys

try:
    from PySide2 import QtWidgets

    if not QtWidgets.QApplication.instance():
        app = QtWidgets.QApplication(sys.argv)
except Exception as e:
    print(f"Failed to init QApplication: {e}")

import pymel.core as pm
import unittest
from mayatk.anim_utils.scale_keys import ScaleKeys


class TestMergedSegments(unittest.TestCase):
    def setUp(self):
        pm.newFile(force=True)
        self.cube1 = pm.polyCube(n="Cube1")[0]
        self.cube2 = pm.polyCube(n="Cube2")[0]

        # Group 1: 0-10
        pm.setKeyframe(self.cube1, t=0, v=0, at="tx")
        pm.setKeyframe(self.cube1, t=10, v=10, at="tx")

        # Group 2: 20-30
        pm.setKeyframe(self.cube2, t=20, v=0, at="tx")
        pm.setKeyframe(self.cube2, t=30, v=10, at="tx")

    def test_merged_segments_stagger(self):
        # Scale 400% with prevent_overlap=True
        # This should cause temporary overlap (20-40) which merges segments if re-collected
        # But our new logic should handle it.

        scale_keys = ScaleKeys(
            objects=[self.cube1, self.cube2],
            factor=4.0,
            prevent_overlap=True,
            split_static=True,
            group_mode="overlap_groups",  # Treat each cube as a group (since they don't overlap initially)
            verbose=True,
        )
        scale_keys.execute()

        # Check Cube 1
        # Should be 0-40
        t1 = pm.keyframe(self.cube1, q=True, tc=True)
        print(f"Cube1 keys: {t1}")
        self.assertAlmostEqual(t1[0], 0.0)
        self.assertAlmostEqual(t1[-1], 40.0)

        # Check Cube 2
        # Original Gap 10. Scaled Gap 40.
        # Target Start = 40 + 40 = 80.
        # Target End = 80 + 40 = 120.
        t2 = pm.keyframe(self.cube2, q=True, tc=True)
        print(f"Cube2 keys: {t2}")
        self.assertAlmostEqual(t2[0], 80.0)
        self.assertAlmostEqual(t2[-1], 120.0)


if __name__ == "__main__":
    unittest.main()
