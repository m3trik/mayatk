import unittest
import pymel.core as pm
from mayatk.core_utils.instancing.auto_instancer import AutoInstancer


class TestScaleMerge(unittest.TestCase):
    def setUp(self):
        pm.newFile(force=True)

    def test_scale_merge(self):
        # Create two spheres with different scales
        s1 = pm.polySphere(r=1)[0]
        s2 = pm.polySphere(r=2)[0]

        pm.select([s1, s2])

        print(f"Meshes: {pm.ls(type='mesh')}")
        print(f"Transforms: {pm.ls(type='transform')}")

        # AutoInstancer with scale tolerance
        instancer = AutoInstancer(scale_tolerance=1.0, verbose=True, is_static=False)
        instances = instancer.run()

        print(f"Instances: {instances}")
        s2_exists = pm.objExists(s2)
        print(f"S2 exists: {s2_exists}")

        if instances:
            self.assertEqual(len(instances), 2)
        else:
            self.fail("No instances created")


if __name__ == "__main__":
    unittest.main()
