import unittest
import pymel.core as pm
from mayatk.anim_utils.scale_keys import ScaleKeys
from mayatk.anim_utils._anim_utils import AnimUtils


class TestReproScaleTangents(unittest.TestCase):
    def test_repro_debug_lengths(self):
        pm.newFile(force=True)
        cube = pm.polyCube(name="test_cube")[0]

        # Create stepped animation
        times = [1, 10, 20]
        values = [0, 10, 20]

        for t, v in zip(times, values):
            pm.setKeyframe(cube.tx, time=t, value=v)
            pm.keyTangent(
                cube.tx, time=(t,), outTangentType="step", inTangentType="clamped"
            )

        # Check lengths before scaling
        times_q = pm.keyframe(cube.tx, query=True, tc=True)
        types_q = pm.keyTangent(cube.tx, query=True, outTangentType=True)
        print(f"Before: Times len={len(times_q)}, Types len={len(types_q)}")
        print(f"Types: {types_q}")

        # Scale keys
        ScaleKeys.scale_keys(objects=[cube], factor=2.0, flatten_tangents=True)

        # Check lengths after scaling (but before flatten, effectively)
        # We can't check "during" easily, but we can check if the result is stepped.

        final_tangents = pm.keyTangent(cube.tx, query=True, outTangentType=True)
        print(f"Final tangents: {final_tangents}")

        self.assertIn("step", final_tangents, "Stepped tangents were lost!")


if __name__ == "__main__":
    unittest.main()
