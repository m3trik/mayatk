# !/usr/bin/python
# coding=utf-8
# from typing import List, Union, Tuple, Dict, Any

try:
    import pymel.core as pm
except ModuleNotFoundError as error:
    print(__file__, error)

import pythontk as ptk

# from this package:
from mayatk.env_utils import EnvUtils


class MapConverterSlots(ptk.ImgUtils):
    texture_file_types = [
        "*.png",
        "*.jpg",
        "*.bmp",
        "*.tga",
        "*.tiff",
        "*.gif",
        "*.exr",
    ]

    def __init__(self, **kwargs):
        super().__init__()
        self.sb = kwargs.get("switchboard")
        self.ui = self.sb.loaded_ui.map_converter

    @property
    def sourceimages(self):
        source_images_path = EnvUtils.get_maya_info("sourceimages")
        if not source_images_path:
            print("Source images directory not found.")
        return source_images_path

    def b000(self):
        """Convert DirectX to OpenGL"""
        dx_map_path = self.sb.file_dialog(
            file_types=self.texture_file_types,
            title="Select a DirectX normal map to convert:",
            start_dir=self.sourceimages,
            allow_multiple=False,
        )
        if dx_map_path:
            print(f"Converting: {dx_map_path} ..")
            gl_map_path = self.create_gl_from_dx(dx_map_path)
            print(f"// Result: {gl_map_path}")

    def b001(self):
        """Convert OpenGL to DirectX"""
        gl_map_path = self.sb.file_dialog(
            file_types=self.texture_file_types,
            title="Select an OpenGL normal map to convert:",
            start_dir=self.sourceimages,
            allow_multiple=False,
        )
        if gl_map_path:
            print(f"Converting: {gl_map_path} ..")
            dx_map_path = self.create_dx_from_gl(gl_map_path)
            print(f"// Result: {dx_map_path}")

    def b002(self):
        """Pack Transparency into Albedo"""
        paths = self.sb.file_dialog(
            file_types=self.texture_file_types,
            title="Select an albedo map and transparency map:",
            start_dir=self.sourceimages,
            allow_multiple=True,
        )
        if paths:
            if len(paths) < 2:
                raise ValueError(
                    "Please select both an albedo map and a transparency map."
                )

            albedo_map_path = None
            alpha_map_path = None

            for path in paths:
                map_type = self.get_map_type_from_filename(path)
                if map_type == "Base_Color":
                    albedo_map_path = path
                elif map_type == "Albedo_Transparency":
                    alpha_map_path = path

            if not albedo_map_path:
                raise FileNotFoundError("Albedo map not found in the selected files.")
            if not alpha_map_path:
                raise FileNotFoundError(
                    "Transparency map not found in the selected files."
                )

            print(
                f"Packing transparency from {alpha_map_path} into {albedo_map_path} .."
            )
            albedo_transparency_map_path = self.pack_transparency_into_albedo(
                albedo_map_path, alpha_map_path
            )
            print(f"// Result: {albedo_transparency_map_path}")

    def b003(self):
        """Pack Smoothness or Roughness into Metallic"""
        paths = self.sb.file_dialog(
            file_types=self.texture_file_types,
            title="Select a metallic map and a smoothness or roughness map:",
            start_dir=self.sourceimages,
            allow_multiple=True,
        )
        if paths:
            if len(paths) < 2:
                raise ValueError(
                    "Please select both a metallic map and a smoothness or roughness map."
                )

            metallic_map_path = None
            alpha_map_path = None
            invert_alpha = False

            for path in paths:
                map_type = self.get_map_type_from_filename(path)
                if map_type == "Metallic":
                    metallic_map_path = path
                elif map_type == "Smoothness":
                    alpha_map_path = path
                    invert_alpha = False
                elif map_type == "Roughness" and alpha_map_path is None:
                    alpha_map_path = path
                    invert_alpha = True

            if not metallic_map_path:
                raise FileNotFoundError("Metallic map not found in the selected files.")
            if not alpha_map_path:
                raise FileNotFoundError(
                    "Smoothness or Roughness map not found in the selected files."
                )

            print(
                f"Packing {'smoothness' if not invert_alpha else 'roughness'} from {alpha_map_path} into {metallic_map_path} .."
            )
            metallic_smoothness_map_path = self.pack_smoothness_into_metallic(
                metallic_map_path, alpha_map_path, invert_alpha=invert_alpha
            )
            print(f"// Result: {metallic_smoothness_map_path}")

    def b004(self):
        """Convert Specular map(s) to Metallic"""
        spec_map_paths = self.sb.file_dialog(
            file_types=self.texture_file_types,
            title="Select a specular map to convert:",
            start_dir=self.sourceimages,
            allow_multiple=True,
        )
        for spec_map_path in spec_map_paths:
            print(f"Converting: {spec_map_path} ..")
            metallic_map_path = self.create_metallic_from_spec(spec_map_path)
            print(f"// Result: {metallic_map_path}")

    def b005(self):
        """Convert Specular map(s) to Roughness"""
        spec_map_paths = self.sb.file_dialog(
            file_types=self.texture_file_types,
            title="Select a specular map to convert:",
            start_dir=self.sourceimages,
            allow_multiple=True,
        )
        for spec_map_path in spec_map_paths:
            print(f"Converting: {spec_map_path} ..")
            roughness_map_path = self.create_roughness_from_spec(spec_map_path)
            print(f"// Result: {roughness_map_path}")

    def b006(self):
        """Optimize a texture map(s)"""
        texture_paths = self.sb.file_dialog(
            file_types=self.texture_file_types,
            title="Select texture map(s) to optimize:",
            start_dir=self.sourceimages,
            allow_multiple=True,
        )
        for texture_path in texture_paths:
            print(f"Optimizing: {texture_path} ..")
            optimized_map_path = self.optimize_texture(
                texture_path, max_size=8192, suffix="_opt"
            )
            print(f"// Result: {optimized_map_path}")


class MapConverterUi:
    def __new__(self):
        """Get the Map Converter UI."""
        import os
        from mayatk.ui_utils.ui_manager import UiManager

        ui_file = os.path.join(os.path.dirname(__file__), "map_converter.ui")
        ui = UiManager.get_ui(ui_source=ui_file, slot_source=MapConverterSlots)
        return ui


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    MapConverterUi().show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
