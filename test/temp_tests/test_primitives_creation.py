import unittest
import pymel.core as pm
import mayatk as mtk
from mayatk.edit_utils.primitives import Primitives

class TestPrimitivesCreation(unittest.TestCase):

    def setUp(self):
        pm.newFile(force=True)

    def test_create_circle_default(self):
        """Test default circle creation (no history)."""
        node = Primitives.create_circle(name="test_circle_default", history=False)
        
        # Check node exists
        self.assertTrue(pm.objExists("test_circle_default"))
        
        # Check node type
        shapes = pm.listRelatives("test_circle_default", shapes=True)
        self.assertTrue(shapes)
        self.assertEqual(pm.nodeType(shapes[0]), "mesh")
        
        # Check for history - should be None or minimal (polyCreateFacet)
        # The new implementation uses polyCreateFacet, which creates a polyCreateFace node.
        # But it should NOT have the circle history params like radius, sections driven by a makeCircle node.
        history_nodes = pm.listHistory("test_circle_default")
        # Ensure no planarTrimSurface or makeNurbCircle nodes
        planar_trims = [n for n in history_nodes if pm.nodeType(n) == "planarTrimSurface"]
        nurb_circles = [n for n in history_nodes if pm.nodeType(n) == "makeNurbCircle"]
        
        self.assertEqual(len(planar_trims), 0, "Default circle should not have PlanarTrim history")
        self.assertEqual(len(nurb_circles), 0, "Default circle should not have NurbCircle history")

    def test_create_circle_history(self):
        """Test circle creation with history."""
        node_list = Primitives.create_circle(name="test_circle_history", history=True, radius=6, numPoints=8)
        
        transform_node = node_list[0]
        
        # Check node exists
        self.assertTrue(pm.objExists("test_circle_history"))
        
        # Check history chain
        history_nodes = pm.listHistory(transform_node)
        planar_trims = [n for n in history_nodes if pm.nodeType(n) == "planarTrimSurface"]
        nurb_circles = [n for n in history_nodes if pm.nodeType(n) == "makeNurbCircle"]
        
        self.assertTrue(len(planar_trims) > 0, "History circle should have PlanarTrim history")
        self.assertTrue(len(nurb_circles) > 0, "History circle should have NurbCircle history")
        
        # Verify attributes on the makesNurbCircle node
        circle_node = nurb_circles[0]
        self.assertAlmostEqual(circle_node.radius.get(), 6.0)
        self.assertEqual(circle_node.sections.get(), 8)
        
        # Verify hidden curve exists
        # The implementation names it name + "_crv"
        crv_name = "test_circle_history_crv"
        self.assertTrue(pm.objExists(crv_name))
        self.assertFalse(pm.getAttr(crv_name + ".visibility"), "Source curve should be hidden")

    def test_create_helix(self):
        """Test helix creation."""
        # Using create_default_primitive which likely calls the dictionary lookup
        node = Primitives.create_default_primitive("polygon", "helix", name="test_helix", coils=4)
        
        self.assertTrue(pm.objExists("test_helix"))
        
        # Check history
        history_nodes = pm.listHistory("test_helix")
        helix_nodes = [n for n in history_nodes if pm.nodeType(n) == "polyHelix"]
        
        self.assertTrue(len(helix_nodes) > 0, "Helix should have polyHelix history")
        self.assertAlmostEqual(helix_nodes[0].coils.get(), 4.0)

if __name__ == "__main__":
    unittest.main()
