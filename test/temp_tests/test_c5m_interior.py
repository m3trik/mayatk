import unittest
import os
import sys
import pymel.core as pm
import mayatk as mtk

# Add the folder containing test_001.py to sys.path so we can import it
TEST_001_PATH = r"o:\Cloud\Code\MEL\_editor"
if TEST_001_PATH not in sys.path:
    sys.path.append(TEST_001_PATH)

import test_001

# Import base test from the correct location
# Since we are running this file directly, we need to make sure mayatk.test is importable
# or just import base_test directly if it's in the python path
try:
    from mayatk.test.base_test import MayaTkTestCase
except ImportError:
    # Fallback: try to find it relative to this file
    current_dir = os.path.dirname(os.path.abspath(__file__))
    test_dir = os.path.dirname(current_dir)
    if test_dir not in sys.path:
        sys.path.append(test_dir)
    from base_test import MayaTkTestCase


class TestC5MInterior(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        self.scene_path = r"O:\Dropbox (Moth+Flame)\Moth+Flame Dropbox\Ryan Simpson\_tests\optimize_scene_textures\scenes\modules\C5M_ALPHA_INTERIOR\C5M_ALPHA_INTERIOR_module.ma"

        # Check if scene exists
        if not os.path.exists(self.scene_path):
            # Try .mb if .ma doesn't exist
            self.scene_path = self.scene_path.replace(".ma", ".mb")

        if not os.path.exists(self.scene_path):
            self.skipTest(f"Scene file not found: {self.scene_path}")

        # Open the scene
        print(f"Opening scene: {self.scene_path}")
        pm.openFile(self.scene_path, force=True)

    def test_stingray_optimization_and_connection(self):
        """
        Rigorously test the Stingray material update pipeline on the C5M Interior scene.
        """
        # 1. Run the update process using the logic from test_001.py
        # We use the static method from the Test class in test_001.py
        print("Running update_stingray_materials_with_optimized_textures...")
        results = test_001.Test.update_stingray_materials_with_optimized_textures(
            materials=None,  # Process all
            max_size=2048,  # Use smaller size for speed in test
            create_msao=True,
            convert_specgloss=True,
            output_extension="png",
            verbose=True,
        )

        self.assertTrue(results, "No materials were processed.")

        # 2. Verify connections for each processed material
        for mat_name, data in results.items():
            print(f"Verifying material: {mat_name}")
            mat_node = pm.PyNode(mat_name)

            # Check if MSAO was created and connected if expected
            if data.get("msao"):
                self._verify_msao_connection(mat_node)

            # Check other connections based on what was reported as connected
            for map_type, path in data.get("connected", []):
                if map_type == "MSAO":
                    continue  # Already checked

                self._verify_standard_connection(mat_node, map_type)

    def _verify_msao_connection(self, mat_node):
        """Verify that MSAO map is correctly connected to Metallic, AO, and Roughness."""
        # 1. Metallic (Red Channel)
        # Should be connected to TEX_metallic_map (or children)
        metallic_input = self._get_input_connection(mat_node, "TEX_metallic_map")
        self.assertIsNotNone(
            metallic_input, f"{mat_node}: TEX_metallic_map should have input"
        )
        # Check if it comes from a file node's outColorR
        attr_name = metallic_input.attrName(longName=True)
        self.assertTrue(
            attr_name.endswith("outColorR") or attr_name.endswith("outputR"),
            f"{mat_node}: Metallic should be connected to Red channel. Got {attr_name}",
        )

        # 2. Ambient Occlusion (Green Channel)
        ao_input = self._get_input_connection(mat_node, "TEX_ao_map")
        self.assertIsNotNone(ao_input, f"{mat_node}: TEX_ao_map should have input")
        attr_name = ao_input.attrName(longName=True)
        self.assertTrue(
            attr_name.endswith("outColorG") or attr_name.endswith("outputG"),
            f"{mat_node}: AO should be connected to Green channel. Got {attr_name}",
        )

        # 3. Roughness (Alpha Channel -> Reverse -> Roughness)
        roughness_input = self._get_input_connection(mat_node, "TEX_roughness_map")
        self.assertIsNotNone(
            roughness_input, f"{mat_node}: TEX_roughness_map should have input"
        )

        # Should come from a reverse node
        source_node = roughness_input.node()
        self.assertEqual(
            source_node.type(),
            "reverse",
            f"{mat_node}: Roughness should come from a reverse node",
        )

        # Reverse node input should come from Alpha channel of the file
        reverse_input = self._get_input_connection(source_node, "inputX")
        self.assertIsNotNone(
            reverse_input, f"{mat_node}: Reverse node inputX should be connected"
        )
        attr_name = reverse_input.attrName(longName=True)
        self.assertTrue(
            attr_name.endswith("outAlpha") or attr_name.endswith("outputA"),
            f"{mat_node}: Reverse input should come from Alpha channel. Got {attr_name}",
        )

    def _verify_standard_connection(self, mat_node, map_type):
        """Verify standard connections like Diffuse, Normal, Emissive."""
        attr_map = {
            "Base_Color": "TEX_color_map",
            "Diffuse": "TEX_color_map",
            "Normal": "TEX_normal_map",
            "Emissive": "TEX_emissive_map",
            "Metallic": "TEX_metallic_map",
            "Roughness": "TEX_roughness_map",
            "Ambient_Occlusion": "TEX_ao_map",
        }

        target_attr = attr_map.get(map_type)
        if not target_attr:
            return

        conn = self._get_input_connection(mat_node, target_attr)
        self.assertIsNotNone(
            conn, f"{mat_node}: {target_attr} should be connected for {map_type}"
        )

        # For scalar maps (Metallic, Roughness, AO) NOT in MSAO mode,
        # we expect outColorR connection now (based on recent fix)
        if map_type in ["Metallic", "Roughness", "Ambient_Occlusion"]:
            attr_name = conn.attrName(longName=True)
            self.assertTrue(
                attr_name.endswith("outColorR") or attr_name.endswith("outputR"),
                f"{mat_node}: {map_type} should be connected to Red channel (scalar). Got {attr_name}",
            )

    def _get_input_connection(self, node, attr_name):
        """Helper to get the source plug connected to an attribute (handling compound/children)."""
        # Try parent attribute first
        if node.hasAttr(attr_name):
            inputs = node.attr(attr_name).inputs(plugs=True)
            if inputs:
                return inputs[0]

        # Try children (X/Y/Z or R/G/B)
        # If parent has no direct connection, check if children are connected.
        # We assume if one child is connected, the map is "connected".
        # For verification, we usually check the first child (X or R).
        for suffix in ["X", "R"]:
            child_name = attr_name + suffix
            if node.hasAttr(child_name):
                inputs = node.attr(child_name).inputs(plugs=True)
                if inputs:
                    return inputs[0]

        return None


if __name__ == "__main__":
    unittest.main()
