import pymel.core as pm
from mayatk.anim_utils.segment_keys import SegmentKeys
import unittest


class TestSegmentation(unittest.TestCase):
    def setUp(self):
        pm.newFile(force=True)
        self.cube = pm.polyCube(name="TestCube")[0]

    def test_flat_gap(self):
        """Test that a flat gap (same value) splits segments."""
        # Segment 1: 1-10, Value 0
        pm.setKeyframe(self.cube.tx, t=1, v=0)
        pm.setKeyframe(self.cube.tx, t=10, v=0)

        # Gap: 10-20 (Flat, v=0)

        # Segment 2: 20-30, Value 0->5
        pm.setKeyframe(self.cube.tx, t=20, v=0)
        pm.setKeyframe(self.cube.tx, t=30, v=5)

        segments = SegmentKeys.collect_segments([self.cube], split_static=True)
        print(f"\nFlat Gap Segments: {len(segments)}")
        for s in segments:
            print(f"  {s['start']} - {s['end']}")

        self.assertEqual(len(segments), 2)

    def test_value_change_gap(self):
        """Test that a value change across a gap bridges segments."""
        # Segment 1: 1-10, Value 0
        pm.setKeyframe(self.cube.tx, t=1, v=0)
        pm.setKeyframe(self.cube.tx, t=10, v=0)

        # Gap: 10-20 (Value 0->5)

        # Segment 2: 20-30, Value 5
        pm.setKeyframe(self.cube.tx, t=20, v=5)
        pm.setKeyframe(self.cube.tx, t=30, v=5)

        segments = SegmentKeys.collect_segments([self.cube], split_static=True)
        print(f"\nValue Change Gap Segments: {len(segments)}")
        for s in segments:
            print(f"  {s['start']} - {s['end']}")

        # This is expected to be 1 segment because of interpolation
        self.assertEqual(len(segments), 1)

    def test_visibility_gap(self):
        """Test visibility curve behavior."""
        # Segment 1: 1-10
        pm.setKeyframe(self.cube.tx, t=1, v=0)
        pm.setKeyframe(self.cube.tx, t=10, v=1)

        # Visibility keys spanning the gap
        pm.setKeyframe(self.cube.visibility, t=1, v=1)
        pm.setKeyframe(self.cube.visibility, t=30, v=1)

        # Segment 2: 20-30
        pm.setKeyframe(self.cube.tx, t=20, v=0)
        pm.setKeyframe(self.cube.tx, t=30, v=1)

        segments = SegmentKeys.collect_segments([self.cube], split_static=True)
        print(f"\nVisibility Gap Segments: {len(segments)}")
        for s in segments:
            print(f"  {s['start']} - {s['end']}")

        # Visibility is treated as always active, so this might bridge it
        self.assertEqual(len(segments), 1)


if __name__ == "__main__":
    unittest.main()
