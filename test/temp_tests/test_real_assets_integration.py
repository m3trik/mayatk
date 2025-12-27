import sys
import os
import shutil
import unittest

# Add script paths
sys.path.append(r"o:\Cloud\Code\_scripts\mayatk")
sys.path.append(r"o:\Cloud\Code\_scripts\pythontk")

from pythontk.img_utils.texture_map_factory import TextureMapFactory


class TestRealAssetsIntegration(unittest.TestCase):
    def setUp(self):
        self.assets_dir = r"o:\Cloud\Code\_scripts\mayatk\test\test_assets"
        self.output_dir = os.path.join(self.assets_dir, "output")
        if os.path.exists(self.output_dir):
            shutil.rmtree(self.output_dir)
        os.makedirs(self.output_dir)

    def tearDown(self):
        # Keep output for inspection
        pass

    def test_process_model_assets(self):
        """Test processing the 'model_*' assets."""
        # Filter for model_* files
        files = [
            os.path.join(self.assets_dir, f)
            for f in os.listdir(self.assets_dir)
            if f.startswith("model_") and f.endswith(".png")
        ]

        print(f"Found {len(files)} model assets.")

        # Config: Unity HDRP (creates MaskMap, AlbedoTransparency)
        config = {
            "mask_map": True,
            "albedo_transparency": True,
            "orm_map": True,
            "rename": True,  # Enable renaming to verify canonical names
        }

        results = TextureMapFactory.prepare_maps(
            files, config, output_dir=self.output_dir, callback=print
        )

        result_names = [os.path.basename(p) for p in results]
        print("\nGenerated files:")
        for n in result_names:
            print(f" - {n}")

        # Assertions
        self.assertTrue(
            any("MaskMap" in name or "MSAO" in name for name in result_names),
            "MaskMap/MSAO not created",
        )
        self.assertTrue(any("ORM" in n for n in result_names), "ORM map not created")
        self.assertTrue(
            any("Albedo_Transparency" in n for n in result_names),
            "Albedo_Transparency not created",
        )


if __name__ == "__main__":
    unittest.main()
