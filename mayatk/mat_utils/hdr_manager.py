# !/usr/bin/python
# coding=utf-8
import os

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils import CoreUtils
from mayatk.node_utils import NodeUtils
from mayatk.env_utils import EnvUtils


class HdrManager:
    hdr_env_name = "aiSkyDomeLight_"

    @property
    def hdr_env(self) -> object:
        """ """
        node = pm.ls(self.hdr_env_name, exactType="aiSkyDomeLight")
        try:
            return node[0]
        except IndexError:
            return None

    @hdr_env.setter
    def hdr_env(self, tex) -> None:
        """ """
        node = self.hdr_env
        if not node:
            node = NodeUtils.create_render_node(
                "aiSkyDomeLight",
                "asLight",
                name=self.hdr_env_name,
                camera=0,
                skyRadius=0,
            )  # turn off skydome and viewport visibility.
            self.hdr_env_transform.hiddenInOutliner.set(1)
            pm.outlinerEditor("outlinerPanel1", edit=True, refresh=True)

        file_node = NodeUtils.get_connected_nodes(
            node, node_type="file", direction="incoming", first_match=True
        )
        if not file_node:
            file_node = NodeUtils.create_render_node(
                "file", "as2DTexture", texture_node=True
            )
            pm.connectAttr(file_node.outColor, node.color, force=True)

        file_node.fileTextureName.set(str(tex))

    @property
    def hdr_env_transform(self) -> object:
        """ """
        node = NodeUtils.get_transform_node(self.hdr_env)
        if not node:
            return None
        return node

    def set_hdr_map_visibility(self, state):
        """ """
        node = self.hdr_env
        if node:
            node.camera.set(state)

    @CoreUtils.undo
    def create_network(
        self,
        hdrMap="",
        hdrMapVisibility=False,
    ):
        """ """
        self.hdr_env = hdrMap
        self.set_hdr_map_visibility(hdrMapVisibility)


class HdrManagerSlots(HdrManager):
    def __init__(self, **kwargs):
        super().__init__()

        self.sb = self.switchboard()
        self.ui = self.sb.hdr_manager
        self.workspace_dir = EnvUtils.get_maya_info("workspace_dir")
        self.source_images_dir = os.path.join(self.workspace_dir, "sourceimages")

        hdr_info = ptk.get_dir_contents(
            self.source_images_dir,
            ["filename", "filepath"],
            inc_files=["*.exr", "*.hdr"],
            group_by_type=True,
        )
        self.ui.cmb000.add(
            zip(hdr_info["filename"], hdr_info["filepath"]), ascending=False
        )

        node = self.hdr_env_transform
        if node:
            rotation = node.rotateY.get()
            self.ui.slider000.setSliderPosition(rotation)

    @property
    def hdr_map(self) -> str:
        """Get the hdr map filepath from the comboBoxes current text.

        Returns:
            (str) data as string.
        """
        data = self.ui.cmb000.currentData()
        return data

    @property
    def hdr_map_visibility(self) -> bool:
        """Get the hdr map visibility state from the checkBoxes current state.

        Returns:
            (bool)
        """
        state = self.ui.chk000.isChecked()
        return state

    def cmb000(self, index, widget):
        """HDR map selection."""
        data = widget.currentData()

        self.hdr_env = data  # set the HDR map.

    def chk000(self, state, widget):
        """ """
        self.set_hdr_map_visibility(state)  # set the HDR map visibility.

    def slider000(self, value, widget):
        """Rotate the HDR map."""
        if self.hdr_env:
            transform = NodeUtils.get_transform_node(self.hdr_env)
            pm.rotate(
                transform,
                value,
                rotateY=True,
                forceOrderXYZ=True,
                objectSpace=True,
                absolute=True,
            )

    def b000(self):
        """Create network."""
        self.create_network(
            hdrMap=self.hdr_map,
            hdrMapVisibility=self.hdr_map_visibility,
        )

    # def b001(self):
    #     """Get texture maps."""
    #     image_files = self.sb.file_dialog(
    #         file_types=["*.png", "*.jpg", "*.bmp", "*.tga", "*.tiff", "*.gif"],
    #         title="Select one or more image files to open.",
    #         directory=self.source_images_dir,
    #     )

    #     if image_files:
    #         self.image_files = image_files
    #         self.ui.txt001.clear()

    #         msg_mat_selection = self.image_files
    #         for (
    #             i
    #         ) in msg_mat_selection:  # format msg_intro using the map_types in imtools.
    #             self.callback(ptk.truncate(i, 60))

    #         self.ui.b000.setDisabled(False)
    #     elif not self.image_files:
    #         self.ui.b000.setDisabled(True)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from uitk import Switchboard

    parent = CoreUtils.get_main_window()
    ui_file = os.path.join(os.path.dirname(__file__), "hdr_manager.ui")
    sb = Switchboard(parent, ui_location=ui_file, slot_location=HdrManagerSlots)

    sb.current_ui.set_attributes(WA_TranslucentBackground=True)
    sb.current_ui.set_flags(FramelessWindowHint=True, WindowStaysOnTopHint=True)
    sb.current_ui.set_style(theme="dark", style_class="translucentBgWithBorder")
    sb.current_ui.header.configureButtons(minimize_button=True, hide_button=True)
    sb.current_ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
