import pymel.core as pm
from mayatk.anim_utils.segment_keys import SegmentKeys
import unittest


class TestBetterSegmentation(unittest.TestCase):
    def setUp(self):
        pm.newFile(force=True)
        self.cube = pm.polyCube(name="TestCube")[0]

        # Create a "Sequence" of animation
        # Motion 1: 10-20
        pm.setKeyframe(self.cube.tx, t=10, v=0)
        pm.setKeyframe(self.cube.tx, t=20, v=10)

        # Motion 2: 40-50
        pm.setKeyframe(self.cube.tx, t=40, v=10)
        pm.setKeyframe(self.cube.tx, t=50, v=20)

        # Visibility Hold: 1-60 (Spans everything)
        pm.setKeyframe(self.cube.visibility, t=1, v=1)
        pm.setKeyframe(self.cube.visibility, t=60, v=1)

    def test_scaling_behavior(self):
        """Verify default behavior preserves the sequence (1 segment)."""
        segments = SegmentKeys.collect_segments([self.cube], split_static=True)
        print(f"\nScaling Mode (Default): Found {len(segments)} segments")
        for s in segments:
            print(f"  {s['start']} - {s['end']}")

        # Should be 1 segment (1-60) because visibility bridges the gap
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["start"], 1.0)
        self.assertEqual(segments[0]["end"], 60.0)

    def test_reporting_behavior(self):
        """Verify reporting behavior shows details (2 segments)."""
        segments = SegmentKeys.collect_segments(
            [self.cube],
            split_static=True,
            ignore_visibility_holds=True,  # <--- The "Better Way" flag
        )
        print(f"\nReporting Mode: Found {len(segments)} segments")
        for s in segments:
            print(f"  {s['start']} - {s['end']}")

        # Should be 2 segments (10-20, 40-50)
        # Note: The visibility keys at 1 and 60 are ignored because they don't change value
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["start"], 10.0)
        self.assertEqual(segments[0]["end"], 20.0)
        self.assertEqual(segments[1]["start"], 40.0)
        self.assertEqual(segments[1]["end"], 50.0)


if __name__ == "__main__":
    unittest.main()
