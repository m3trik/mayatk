# !/usr/bin/python
# coding=utf-8
import maya.cmds as cmds
import os

import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.env_utils._env_utils import EnvUtils


class HdrManager:
    hdr_env_name = "aiSkyDomeLight_"

    @staticmethod
    def ensure_plugin_loaded() -> bool:
        """Ensure the Arnold (mtoa) plugin is loaded.

        Returns True on success, False if loading failed (e.g. plugin
        not installed). Callers that need Arnold should bail when this
        returns False.
        """
        try:
            if not cmds.pluginInfo("mtoa", query=True, loaded=True):
                cmds.loadPlugin("mtoa")
            return True
        except Exception:
            return False

    @property
    def hdr_env(self) -> object:
        """ """
        node = cmds.ls(self.hdr_env_name, exactType="aiSkyDomeLight")
        try:
            return node[0]
        except IndexError:
            return None

    @hdr_env.setter
    def hdr_env(self, tex) -> None:
        """ """
        if not self.ensure_plugin_loaded():
            cmds.warning("Arnold (mtoa) plugin not available — cannot set HDR env.")
            return
        node = self.hdr_env
        if not node:
            node = NodeUtils.create_render_node(
                "aiSkyDomeLight",
                "asLight",
                name=self.hdr_env_name,
                camera=0,
                skyRadius=0,
            )  # turn off skydome and viewport visibility.
            transform = self.hdr_env_transform
            if transform:
                cmds.setAttr(f"{transform}.hiddenInOutliner", 1)
            cmds.outlinerEditor("outlinerPanel1", edit=True, refresh=True)

        file_node = NodeUtils.get_connected_nodes(
            node, node_type="file", direction="incoming", first_match=True
        )
        if not file_node:
            file_node = NodeUtils.create_render_node(
                "file", "as2DTexture", texture_node=True
            )
            cmds.connectAttr(f"{file_node}.outColor", f"{node}.color", force=True)

        cmds.setAttr(f"{file_node}.fileTextureName", str(tex), type="string")

    @property
    def hdr_env_transform(self) -> object:
        """ """
        node = NodeUtils.get_transform_node(self.hdr_env)
        if not node:
            return None
        return node

    def set_hdr_map_visibility(self, state):
        """ """
        if not self.ensure_plugin_loaded():
            return
        node = self.hdr_env
        if node:
            cmds.setAttr(f"{node}.camera", state)

    @CoreUtils.undoable
    def create_network(
        self,
        hdrMap="",
        hdrMapVisibility=False,
    ):
        """ """
        self.hdr_env = hdrMap
        self.set_hdr_map_visibility(hdrMapVisibility)


class HdrManagerSlots(HdrManager):
    def __init__(self, switchboard):
        super().__init__()

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.hdr_manager

        self.workspace_dir = EnvUtils.get_env_info("workspace_dir")
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
            rotation = cmds.getAttr(f"{node}.rotateY")
            self.ui.slider000.setSliderPosition(rotation)

    def header_init(self, widget):
        """Configure header menu with tool instructions."""
        widget.menu.add("Separator", setTitle="About")
        widget.menu.add(
            "QPushButton",
            setText="Instructions",
            setObjectName="btn_instructions",
            setToolTip=(
                "HDR Manager — Manage Arnold HDR environment lighting.\n\n"
                "• Lists .exr and .hdr files from sourceimages.\n"
                "• Select an HDR map to apply as a skydome light.\n"
                "• Rotate the environment with the slider."
            ),
        )

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
            cmds.rotate(
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


# ----------------------------------------------------------------------------


if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("hdr_manager", reload=True)
    ui.show(pos="screen", app_exec=True)


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
