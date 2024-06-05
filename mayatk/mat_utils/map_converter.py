# !/usr/bin/python
# coding=utf-8
import os

# from typing import List, Union, Tuple, Dict, Any

try:
    import pymel.core as pm
except ModuleNotFoundError as error:
    print(__file__, error)

import pythontk as ptk
from uitk import Switchboard

# from this package:
from mayatk import core_utils

# from mayatk import xform_utils
# from mayatk import node_utils


class MapConverter:
    @staticmethod
    def pack_transparency_into_albedo(
        albedo_map_path: str,
        alpha_map_path: str,
        output_dir: str = None,
        suffix: str = "_AlbedoTransparency",
        invert_alpha: bool = False,
    ) -> str:
        """Packs the transparency channel into the albedo map.

        Parameters:
            albedo_map_path (str): File path of the albedo texture.
            alpha_map_path (str): File path of the transparency texture to be packed into the alpha channel.
            output_dir (str, optional): Directory path for the output. If None, the output directory will be the same as the albedo map path.
            invert_alpha (bool): If True, inverts the alpha channel before packing.

        Returns:
            str: File path of the resulting AlbedoTransparency map.
        """
        base_name = ptk.get_base_texture_name(albedo_map_path)
        if output_dir is None:
            output_dir = os.path.dirname(albedo_map_path)
        elif not os.path.isdir(output_dir):
            raise ValueError(
                f"The specified output directory '{output_dir}' is not valid."
            )

        output_path = os.path.join(output_dir, f"{base_name}{suffix}.png")

        # Pack the transparency channel into the albedo map
        success = ptk.pack_channel_into_alpha(
            albedo_map_path, alpha_map_path, output_path, invert_alpha=invert_alpha
        )

        if success:
            return output_path
        else:
            raise Exception("Failed to pack transparency into albedo map.")

    @staticmethod
    def pack_smoothness_into_metallic(
        metallic_map_path: str,
        alpha_map_path: str,
        output_dir: str = None,
        suffix: str = "_MetallicSmoothness",
        invert_alpha: bool = False,
    ) -> str:
        """Packs the alpha channel (smoothness or inverted roughness) into the metallic map.

        Parameters:
            metallic_map_path (str): File path of the metallic texture.
            alpha_map_path (str): File path of the smoothness or roughness texture to be packed into the alpha channel.
            output_dir (str, optional): Directory path for the output. If None, the output directory will be the same as the metallic map path.
            invert_alpha (bool): If True, inverts the alpha channel. Useful for converting roughness to smoothness.

        Returns:
            str: File path of the resulting metallic smoothness map.
        """
        base_name = ptk.get_base_texture_name(metallic_map_path)
        if output_dir is None:
            output_dir = os.path.dirname(metallic_map_path)
        elif not os.path.isdir(output_dir):
            raise ValueError(
                f"The specified output directory '{output_dir}' is not valid."
            )

        output_path = os.path.join(output_dir, f"{base_name}{suffix}.png")

        # Pack the alpha channel into the metallic map
        success = ptk.pack_channel_into_alpha(
            metallic_map_path, alpha_map_path, output_path, invert_alpha=invert_alpha
        )

        if success:
            return output_path
        else:
            raise Exception("Failed to pack smoothness into metallic map.")


class MapConverterSlots(MapConverter):
    def __init__(self):
        super().__init__()
        self.sb = self.switchboard()
        self.ui = self.sb.map_converter

        # Initialize and connect UI components

    def browse_for_input_directory(self):
        selected_directory = self.sb.dir_dialog("Select input directory")
        if selected_directory:
            self.ui.txt_input_dir.setText(selected_directory)
            self.update_current_dir(invalidate_and_refresh=True)

    def browse_for_output_directory(self):
        selected_directory = self.sb.dir_dialog("Select output directory")
        if selected_directory:
            self.ui.txt_output_dir.setText(selected_directory)
            self.update_output_dir()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    parent = core_utils.CoreUtils.get_main_window()
    ui_file = os.path.join(os.path.dirname(__file__), "texture_map_converter.ui")
    sb = Switchboard(parent, ui_location=ui_file, slot_location=MapConverterSlots)

    sb.current_ui.set_attributes(WA_TranslucentBackground=True)
    sb.current_ui.set_flags(FramelessWindowHint=True, WindowStaysOnTopHint=True)
    sb.current_ui.set_style(theme="dark", style_class="translucentBgWithBorder")
    sb.current_ui.header.configureButtons(minimize_button=True, hide_button=True)
    sb.current_ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
