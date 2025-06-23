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

    def header_init(self, widget):
        """ """
        # Add a button to open the hypershade editor.
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Set Texture Directory",
            setToolTip="Set the texture file paths for selected objects.\nThe path will be relative if it is within the project's source images directory.",
            setObjectName="lbl010",
        )
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Find and Move Textures",
            setToolTip="Find texture files for selected objects by searching recursively from the given source directory.\nAny textures found will be moved to the destination directory.\n\nNote: This will not work with Arnold texture nodes.",
            setObjectName="lbl011",
        )
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Migrate Textures",
            setToolTip="Migrate file textures for selected objects to a new directory.\nFirst, select the objects with the textures you want to migrate and the directory to migrate from.\nThen, select the directory you want to migrate the textures to.",
            setObjectName="lbl012",
        )
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Convert to Relative Paths",
            setToolTip="Convert all texture paths to relative paths based on the project's source images directory.",
            setObjectName="lbl013",
        )

    def lbl010(self):
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

    def lbl011(self):
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

    def lbl012(self):
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

    def lbl013(self):
        """Convert to Relative Paths"""
        MatUtils.remap_texture_paths()

    def tbl000_init(self, widget):
        print("Initializing Texture Path Editor Table", widget.is_initialized)
        if not widget.is_initialized:
            widget.refresh_on_show = True
            widget.cellChanged.connect(self.handle_cell_edit)

        widget.clear()
        rows = MatUtils.get_file_nodes(
            return_type="shaderName|path|fileNodeName|fileNode", raw=True
        )
        if not rows:
            rows = [("No file nodes found", "", "", None)]

        formatted = [
            [shader_name, path, (file_node_name, file_node)]
            for shader_name, path, file_node_name, file_node in rows
        ]

        widget.add(formatted, headers=["Shader", "Texture Path", "File Node"])
        widget.fit_to_path_column(1)
        self.setup_formatting(widget)
        widget.apply_formatting()

    def setup_formatting(self, widget):
        """Attach formatters for the texture path table."""
        import os

        def format_if_invalid(item, value, *_):
            exists = bool(value) and os.path.exists(str(value))
            fg_color = widget.to_qobject(
                widget.ACTION_COLOR_MAP["valid_fg" if exists else "invalid_fg"],
                "QColor",
            )
            item.setForeground(fg_color)

        widget.set_column_formatter(1, format_if_invalid)

    def handle_cell_edit(self, row: int, col: int):

        tbl = self.ui.tbl000
        value = tbl.item(row, col).text()

        if col == 0:  # Shader rename
            shader_name = tbl.item(row, 0).text()
            if shader_name and pm.objExists(shader_name):
                pm.rename(shader_name, value)

        elif col == 1:  # File path update
            file_node = tbl.item_data(row, 2)
            if file_node and hasattr(file_node, "fileTextureName"):
                file_node.fileTextureName.set(value)

        elif col == 2:  # File node rename
            file_node = tbl.item_data(row, 2)
            if file_node and pm.objExists(file_node):
                pm.rename(file_node, value)


# --------------------------------------------------------------------------------------------

# module name
# print(__name__)
# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
