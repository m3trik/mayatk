import sys

try:
    from PySide2 import QtWidgets

    if not QtWidgets.QApplication.instance():
        app = QtWidgets.QApplication(sys.argv)
except ImportError:
    pass

import pymel.core as pm
import mayatk.anim_utils.scale_keys as sk
import unittest


class TestSegmentOverlap(unittest.TestCase):
    def setUp(self):
        pm.newFile(force=True)
        self.cube = pm.polyCube(name="testCube")[0]

        # Create two segments with a small gap
        # Segment 1: 1-10 (Duration 9)
        pm.setKeyframe(self.cube, time=1, attribute="translateX", value=0)
        pm.setKeyframe(self.cube, time=10, attribute="translateX", value=10)

        # Gap: 10-15 (5 frames)

        # Segment 2: 15-25 (Duration 10)
        pm.setKeyframe(self.cube, time=15, attribute="translateX", value=0)
        pm.setKeyframe(self.cube, time=25, attribute="translateX", value=10)

        # Ensure flat tangents for clean segments
        pm.keyTangent(
            self.cube, edit=True, inTangentType="linear", outTangentType="linear"
        )

    def test_overlap_creation(self):
        # Scale by 2.0 with split_static=True
        # Seg 1 (1-10) -> (1-19)
        # Seg 2 (15-25) -> (15-35)
        # Expected Overlap: 15 < 19

        print("\nRunning scale_keys with split_static=True, factor=2.0")
        sk.ScaleKeys.scale_keys(
            objects=[self.cube],
            factor=2.0,
            split_static=True,
            group_mode="per_object",  # This maps to per_segment
            prevent_overlap=False,  # Default is False, user implies this should happen automatically or be the intended behavior
        )

        times = pm.keyframe(self.cube, query=True, timeChange=True)
        print(f"Key times after scale: {times}")

        # Check for overlap
        # We expect 4 keys.
        # If overlap occurred, the keys might be out of order or interleaved if we just query sorted
        # But physically on the curve, if we scaled in place, we have keys at 1, 19, 15, 35.
        # Maya sorts keys by time automatically.
        # So we'd see 1, 15, 19, 35.
        # Value at 15 (start of seg 2) should be 0.
        # Value at 19 (end of seg 1) should be 10.

        # If they overlapped, the curve is now messy.
        # The user wants to PREVENT this.

        # Let's check the gap.
        # Seg 1 End should be < Seg 2 Start.

        # We can identify segments again?
        # Or just check specific key indices if we know them.
        # Keys were at indices 0, 1, 2, 3.

        t0 = pm.keyframe(self.cube, index=0, query=True, timeChange=True)[0]
        t1 = pm.keyframe(self.cube, index=1, query=True, timeChange=True)[0]
        t2 = pm.keyframe(self.cube, index=2, query=True, timeChange=True)[0]
        t3 = pm.keyframe(self.cube, index=3, query=True, timeChange=True)[0]

        print(f"Keys: {t0}, {t1}, {t2}, {t3}")

        # Original Seg 1 End was index 1. Original Seg 2 Start was index 2.
        # If t1 > t2, we have overlap (or rather, they crossed).

        # Wait, Maya sorts keys. So index 1 is the second key in time.
        # If Seg 2 Start (15) is before Seg 1 End (19), then index 1 is Seg 2 Start (15) and index 2 is Seg 1 End (19).
        # We can check values to identify which key is which.
        # Seg 1 End value = 10. Seg 2 Start value = 0.

        v1 = pm.keyframe(self.cube, index=1, query=True, valueChange=True)[0]
        v2 = pm.keyframe(self.cube, index=2, query=True, valueChange=True)[0]

        print(f"Value at index 1: {v1}")
        print(f"Value at index 2: {v2}")

        # If overlap happened (15 < 19):
        # Time 15 comes first. Value 0.
        # Time 19 comes second. Value 10.
        # So index 1 is 15 (0), index 2 is 19 (10).

        # If NO overlap happened (shifted):
        # Seg 1 End (19) comes first. Value 10.
        # Seg 2 Start (shifted > 19) comes second. Value 0.

        # So if v1 == 0 and v2 == 10, we have overlap/crossing.
        # If v1 == 10 and v2 == 0, we preserved order.

        if v1 == 0 and v2 == 10:
            self.fail(
                "Segments overlapped/crossed! Seg 2 Start (0.0) is before Seg 1 End (10.0)"
            )
        else:
            print("PASS: Segments maintained order.")


if __name__ == "__main__":
    unittest.main()
