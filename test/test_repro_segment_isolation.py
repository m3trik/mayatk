import unittest
import sys
import pymel.core as pm
from mayatk.anim_utils.segment_keys import SegmentKeys
from base_test import MayaTkTestCase

try:
    from PySide2 import QtWidgets

    if not QtWidgets.QApplication.instance():
        app = QtWidgets.QApplication(sys.argv)
except ImportError:
    pass


class TestSegmentIsolation(MayaTkTestCase):
    def test_segment_keyframe_isolation(self):
        """Verify that collected segments only contain their own keyframes."""
        cube = pm.polyCube(name="TestCube")[0]

        # Create two distinct segments: 0-10 and 20-30
        pm.setKeyframe(cube, t=0, v=0, at="tx")
        pm.setKeyframe(cube, t=10, v=10, at="tx")

        # Static gap 10-20 (flat)
        pm.setKeyframe(cube, t=20, v=10, at="tx")
        pm.setKeyframe(cube, t=30, v=20, at="tx")

        # Collect segments
        segments = SegmentKeys.collect_segments([cube], split_static=True)

        self.assertEqual(len(segments), 2, "Should find 2 segments")

        # Segment 1: 0-10
        seg1 = segments[0]
        self.assertEqual(seg1["start"], 0)
        self.assertEqual(seg1["end"], 10)
        # CRITICAL CHECK: Should only have keys 0, 10
        self.assertEqual(
            seg1["keyframes"],
            [0.0, 10.0],
            f"Segment 1 should only have keys [0, 10], got {seg1['keyframes']}",
        )

        # Segment 2: 20-30
        seg2 = segments[1]
        self.assertEqual(seg2["start"], 20)
        self.assertEqual(seg2["end"], 30)
        # CRITICAL CHECK: Should only have keys 20, 30
        self.assertEqual(
            seg2["keyframes"],
            [20.0, 30.0],
            f"Segment 2 should only have keys [20, 30], got {seg2['keyframes']}",
        )


if __name__ == "__main__":
    unittest.main()
