import unittest
import os
import shutil
import tempfile
from PIL import Image
from pythontk.img_utils.texture_map_factory import TextureMapFactory


class TestMetallicSmoothnessPacking(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="texture_factory_test_ms_")
        self.textures_dir = os.path.join(self.test_dir, "textures")
        os.makedirs(self.textures_dir)

        # Create dummy textures
        self.size = (64, 64)

        # Metallic (Black)
        self.metallic_path = os.path.join(self.textures_dir, "test_mat_Metallic.png")
        Image.new("L", self.size, 0).save(self.metallic_path)

        # Roughness (White) -> Smoothness should be Black
        self.roughness_path = os.path.join(self.textures_dir, "test_mat_Roughness.png")
        Image.new("L", self.size, 255).save(self.roughness_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_pack_metallic_smoothness(self):
        # Configure for Metallic_Smoothness
        config = {"metallic_smoothness": True}

        # Run factory
        # prepare_maps returns a list of paths if only one asset is found
        result = TextureMapFactory.prepare_maps(
            source=self.textures_dir, workflow_config=config
        )

        # Check if Metallic_Smoothness map was created
        ms_map = None
        # Result is a list of paths for the single asset
        for path in result:
            if "MetallicSmoothness" in path:
                ms_map = path
                break

        self.assertIsNotNone(ms_map, "Metallic_Smoothness map was not created")

        # Verify content
        # Metallic (R,G,B) should be from Metallic map (0)
        # Smoothness (A) should be inverted Roughness (255 -> 0)
        with Image.open(ms_map) as img:
            self.assertEqual(img.mode, "RGBA")
            r, g, b, a = img.split()

            # Check Metallic (R channel usually, but Metallic map is grayscale so RGB are same)
            self.assertEqual(r.getpixel((0, 0)), 0)

            # Check Smoothness (Alpha)
            # Roughness was 255 (White), so Smoothness should be 0 (Black)
            self.assertEqual(a.getpixel((0, 0)), 0)


if __name__ == "__main__":
    unittest.main()
