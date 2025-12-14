import pymel.core as pm
import unittest
from mayatk.anim_utils.stagger_keys import StaggerKeys
from mayatk.anim_utils.segment_keys import SegmentKeys
from mayatk.anim_utils._anim_utils import AnimUtils

class TestSharedStaticCurve(unittest.TestCase):
    def setUp(self):
        pm.newFile(force=True)
        
        # Create two objects
        self.obj1 = pm.spaceLocator(name="Obj1")
        self.obj2 = pm.spaceLocator(name="Obj2")
        
        # Create distinct animation curves for translation
        # Obj1: 0-10
        pm.setKeyframe(self.obj1, t=0, v=0, at="tx")
        pm.setKeyframe(self.obj1, t=10, v=10, at="tx")
        
        # Obj2: 5-15 (overlaps Obj1)
        pm.setKeyframe(self.obj2, t=5, v=0, at="tx")
        pm.setKeyframe(self.obj2, t=15, v=10, at="tx")
        
        # Create a SHARED ACTIVE curve for visibility
        # Connect to both
        # We create a curve manually or copy it
        pm.setKeyframe(self.obj1, t=0, v=0, at="visibility")
        pm.setKeyframe(self.obj1, t=10, v=1, at="visibility") # Ramp makes it active
        vis_curve = pm.listConnections(self.obj1.visibility, s=True)[0]
        pm.connectAttr(vis_curve.output, self.obj2.visibility, force=True)
        
        # Verify sharing
        c1 = pm.listConnections(self.obj1.visibility, s=True)[0]
        c2 = pm.listConnections(self.obj2.visibility, s=True)[0]
        self.assertEqual(c1, c2, "Objects should share visibility curve")
        
        # Verify curve is NOT static
        is_static = AnimUtils.get_static_curves([c1])
        self.assertFalse(is_static, "Visibility curve should be active")
        
        # Verify AnimUtils finds the shared curve
        curves1 = AnimUtils.objects_to_curves([self.obj1])
        curves2 = AnimUtils.objects_to_curves([self.obj2])
        
        print(f"Obj1 Curves: {curves1}")
        print(f"Obj2 Curves: {curves2}")
        
        self.assertIn(c1, curves1, "Obj1 should have visibility curve")
        self.assertIn(c1, curves2, "Obj2 should have visibility curve")

    def test_stagger_shared_static(self):
        # Try to stagger them
        # Obj1 (0-10). Obj2 (5-15).
        # Should stagger Obj2 to start at 10 (or 10+spacing).
        
        StaggerKeys.stagger_keys([self.obj1, self.obj2], spacing=0)
        
        # Check results
        t1 = pm.keyframe(self.obj1, q=True, tc=True, at="tx")
        t2 = pm.keyframe(self.obj2, q=True, tc=True, at="tx")
        
        print(f"Obj1 TX: {t1}")
        print(f"Obj2 TX: {t2}")
        
        # Obj1 should stay at 0-10
        self.assertEqual(t1[0], 0.0)
        
        # Obj2 should move to 10-20
        # If merged, it stays at 5-15
        if t2[0] == 5.0:
            print("SUCCESS: Objects were merged due to shared curve.")
            # This is the expected behavior for shared curves to prevent double-transform
        else:
            print("FAIL: Objects were NOT merged.")
            self.fail("Objects sharing active curves should be merged.")
            
        self.assertEqual(t2[0], 5.0)

if __name__ == "__main__":
    unittest.main()
