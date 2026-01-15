import pymel.core as pm
import mayatk as mtk
from mayatk.core_utils.instancing.auto_instancer import AutoInstancer
import unittest


class TestScaleInstancing(unittest.TestCase):
    def setUp(self):
        pm.newFile(force=True)

    def test_baked_scale_instancing(self):
        # 1. Create Prototype
        proto = pm.polyCube(name="Prototype", w=1, h=1, d=1)[0]

        # 2. Create Baked Scale Candidate (2x size)
        baked = pm.polyCube(name="BakedScale", w=2, h=2, d=2)[0]
        # Ensure it's baked (polyCube creates it with inputs, but points are 2x)
        # Let's delete history to be sure
        pm.delete(proto, ch=True)
        pm.delete(baked, ch=True)

        # 3. Create Transform Scale Candidate (2x size)
        xform = pm.polyCube(name="XformScale", w=1, h=1, d=1)[0]
        xform.setScale([2, 2, 2])
        pm.delete(xform, ch=True)

        # 4. Run AutoInstancer with scale tolerance
        # We need a tolerance that allows 2x scale difference.
        # scale_diff = abs(2 - 1) / 2 = 0.5. So tolerance 0.6 should work.
        instancer = AutoInstancer(
            scale_tolerance=0.6, verbose=True, check_hierarchy=False
        )
        groups = instancer.run([proto, baked, xform])
        print(f"DEBUG: Test received groups of length {len(groups)}")

        # 5. Verify Results
        # run() returns a list of all instances (including prototypes)
        # We expect 3 objects (Prototype + 2 instances)
        self.assertEqual(len(groups), 3, "Should return 3 instances")

        # Verify instances were created
        # We expect Prototype, BakedScale, XformScale to be instances of Prototype shape

        # Re-fetch objects by name (since originals might be deleted)
        proto_new = pm.PyNode("Prototype")
        baked_new = pm.PyNode("BakedScale")
        xform_new = pm.PyNode("XformScale")

        proto_shape = proto_new.getShape()
        baked_shape = baked_new.getShape()
        xform_shape = xform_new.getShape()

        # BakedScale is the prototype (based on log), so others should share ITS shape
        # Check if they share the same underlying shape node (instances)
        paths = pm.ls(baked_shape, ap=True)
        self.assertIn(
            proto_shape, paths, "Prototype shape should be instance of BakedScale shape"
        )
        self.assertIn(
            xform_shape, paths, "Xform shape should be instance of BakedScale shape"
        )

        # Verify Visual Size

        # BakedScale (Prototype): Size 2 (Baked)
        bb_baked = baked_new.getBoundingBox(space="world")
        size_baked = [bb_baked.width(), bb_baked.height(), bb_baked.depth()]
        print(f"Baked Instance Size: {size_baked}")
        self.assertAlmostEqual(size_baked[0], 2.0, delta=0.01)

        # Prototype: Size 1 (Original)
        # It is now an instance of BakedScale (Size 2).
        # So it should have scale 0.5.
        proto_scale = proto_new.getScale()
        print(f"Prototype Instance Scale: {proto_scale}")
        self.assertAlmostEqual(proto_scale[0], 0.5, delta=0.01)

        bb_proto = proto_new.getBoundingBox(space="world")
        size_proto = [bb_proto.width(), bb_proto.height(), bb_proto.depth()]
        print(f"Prototype Instance Size: {size_proto}")
        self.assertAlmostEqual(size_proto[0], 1.0, delta=0.01)

        # XformScale: Size 2 (Original)
        # It is now an instance of BakedScale (Size 2).
        # Original transform was Scale 2.
        # rel_mtx (Baked->XformShape) is Scale 0.5.
        # Final Scale = 2 * 0.5 = 1.0.
        xform_scale = xform_new.getScale()
        print(f"Xform Instance Scale: {xform_scale}")
        self.assertAlmostEqual(xform_scale[0], 1.0, delta=0.01)

        bb_xform = xform_new.getBoundingBox(space="world")
        size_xform = [bb_xform.width(), bb_xform.height(), bb_xform.depth()]
        print(f"Xform Instance Size: {size_xform}")
        self.assertAlmostEqual(size_xform[0], 2.0, delta=0.01)


if __name__ == "__main__":
    unittest.main()
