# !/usr/bin/python
# coding=utf-8
import os

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
from mayatk.env_utils import EnvUtils
from mayatk.mat_utils import MatUtils


class TexturePathEditorSlots:
    def __init__(self, *args, **kwargs):

        self.sb = kwargs.get("switchboard")
        self.ui = self.sb.loaded_ui.texture_path_editor

    def b000(self):
        """Convert to Relative Paths"""
        MatUtils.remap_texture_paths()

    def b001(self):
        """Set Texture Paths for Selected Objects."""
        texture_dir = self.sb.dir_dialog(
            title="Set Texture Paths for Selected Objects",
            start_dir=EnvUtils.get_env_info("sourceimages"),
        )
        if not texture_dir:
            return
        pm.displayInfo(f"Setting texture paths to: {texture_dir}")
        materials = MatUtils.get_mats()
        MatUtils.remap_texture_paths(materials, new_dir=texture_dir)

    def b002(self):
        """Find and Move Textures"""
        start_dir = EnvUtils.get_env_info("sourceimages")
        source_dir = self.sb.dir_dialog(
            title="Select a root directory to recursively search for textures:",
            start_dir=start_dir,
        )
        if not source_dir:
            return

        selection = pm.ls(sl=True, flatten=True)
        found_textures = MatUtils.find_texture_files(
            objects=selection, source_dir=source_dir, recursive=True
        )
        if not found_textures:
            pm.warning("No textures found.")
            return

        dest_dir = self.sb.dir_dialog(
            title="Select destination directory for textures:",
            start_dir=start_dir,
        )
        if not dest_dir:
            return

        MatUtils.move_texture_files(
            found_files=found_textures, new_dir=dest_dir, delete_old=False
        )

    def b003(self):
        """Migrate Textures"""
        old_dir = self.sb.dir_dialog(
            title="Select a directory to migrate textures from:",
            start_dir=EnvUtils.get_env_info("sourceimages"),
        )
        if not old_dir:
            return
        new_dir = self.sb.dir_dialog(
            title="Select a directory to migrate textures to:",
            start_dir=EnvUtils.get_env_info("sourceimages"),
        )
        if not new_dir:
            return

        selection = pm.ls(sl=True, flatten=True)
        materials = MatUtils.get_mats(selection)
        if not materials:
            self.sb.message_box("No materials found.\nSelect object(s) with materials.")
            return

        MatUtils.migrate_textures(materials=materials, old_dir=old_dir, new_dir=new_dir)

    def cmb000_init(self, widget):
        """Initialize the combo box with file nodes and their texture paths."""
        widget.refresh = True
        if not widget.is_initialized:
            widget.editable = True
            widget.menu.mode = "context"
            widget.menu.setTitle("Texture Path Editor: Options")
            widget.menu.add(
                self.sb.registered_widgets.Label,
                setText="Edit in Place",
                setObjectName="lbl000",
                setToolTip="Set a new file path for the selected texture node.",
            )
            widget.menu.add(
                self.sb.registered_widgets.Label,
                setText="Open in Editor",
                setObjectName="lbl001",
                setToolTip="Open the material in the hypershade editor.",
            )

            def set_file_path(text: str) -> None:
                file_node = widget.currentData()
                if not file_node or not hasattr(file_node, "fileTextureName"):
                    pm.warning("No valid file node selected.")
                    return
                file_node.fileTextureName.set(text)

            widget.on_editing_finished.connect(set_file_path)
            widget.before_popup_shown.connect(widget.init_slot)

        widget.clear()
        items = MatUtils.get_file_nodes(return_type="path|node", raw=True)
        if not items:
            items = [("No file nodes found", None)]
        widget.add(items)

    def lbl000(self, widget):
        """Edit in Place"""
        self.ui.cmb000.setEditable(True)
        self.ui.cmb000.menu.hide()

    def lbl001(self, widget):
        """Open in Editor (Hypershade)"""
        node = self.ui.cmb000.currentData()
        if not node or not pm.objExists(node):
            pm.warning("No node selected.")
            return

        # Select the node (file or material)
        pm.select(node, r=True)
        pm.mel.HypershadeWindow()

        def graph_selected():
            pm.mel.hyperShadePanelGraphCommand("hyperShadePanel1", "graphMaterials")
            pm.mel.hyperShadePanelGraphCommand("hyperShadePanel1", "addSelected")

        pm.evalDeferred(graph_selected)


# --------------------------------------------------------------------------------------------

# module name
# print(__name__)
# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
