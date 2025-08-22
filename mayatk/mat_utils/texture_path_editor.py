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
    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.texture_path_editor
        self._scene_change_job_id = None  # Store the scriptJob ID for cleanup

    def header_init(self, widget):
        """Initialize the header for the texture path editor."""
        # Add a button to open the hypershade editor.
        widget.menu.setTitle("Global Tasks:")
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

        # Refresh the table widget to show updated paths
        self.ui.tbl000.init_slot()

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

        # Refresh the table widget to show updated paths
        self.ui.tbl000.init_slot()

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

        # Refresh the table widget to show updated paths
        self.ui.tbl000.init_slot()

    def lbl013(self):
        """Convert to Relative Paths"""
        MatUtils.remap_texture_paths()

        # Refresh the table widget to show updated paths
        self.ui.tbl000.init_slot()

    def tbl000_init(self, widget):
        if not widget.is_initialized:
            widget.refresh_on_show = True
            widget.cellChanged.connect(self.handle_cell_edit)

            # Add context menu items using the existing menu system
            widget.menu.add(
                "QPushButton",
                setText="Remap to Relative Path",
                setObjectName="remap_to_relative",
                setToolTip="Convert the selected file node's texture path to a relative path",
            )
            widget.menu.add(
                "QPushButton",
                setText="Select File Node",
                setObjectName="select_file_node",
                setToolTip="Select the file node in Maya",
            )
            widget.menu.add(
                "QPushButton",
                setText="Delete File Node",
                setObjectName="delete_file_node",
                setToolTip="Delete the selected file node from Maya",
            )

            # Connect menu interactions to methods
            widget.menu.on_item_interacted.connect(self._handle_menu_action)

            # Set up Maya scriptJob to refresh on scene changes
            self._setup_scene_change_callback(widget)

        self._refresh_table_content(widget)

    def _setup_scene_change_callback(self, widget):
        """Set up Maya scriptJob to refresh the table when scene changes."""
        try:
            # Clean up any existing scriptJob first
            if self._scene_change_job_id is not None:
                pm.scriptJob(kill=self._scene_change_job_id, force=True)
                self._scene_change_job_id = None

            # Create new scriptJob for multiple scene change events
            # We'll use a list to handle multiple events with the same callback
            events_to_watch = [
                "SceneOpened",  # When a scene is opened
                "NewSceneOpened",  # When a new scene is created
                "SceneImported",  # When a scene is imported (though this might not always trigger)
            ]

            # Create scriptJob with multiple events (Maya will create separate jobs for each)
            job_ids = []
            for event in events_to_watch:
                try:
                    job_id = pm.scriptJob(
                        event=[event, lambda: self._on_scene_change(widget)],
                        protected=False,
                    )
                    job_ids.append(job_id)
                except Exception as e:
                    print(
                        f"TexturePathEditor: Failed to create scriptJob for event '{event}': {e}"
                    )

            # Store the job IDs (we'll store them as a list)
            self._scene_change_job_id = job_ids
            print(
                f"TexturePathEditor: Created scene change scriptJobs (IDs: {job_ids})"
            )

        except Exception as e:
            print(f"TexturePathEditor: Failed to create scene change scriptJob: {e}")

    def _on_scene_change(self, widget):
        """Callback function that gets called when the scene changes."""
        try:
            print("TexturePathEditor: Scene changed, refreshing texture path table...")
            self._refresh_table_content(widget)
        except Exception as e:
            print(f"TexturePathEditor: Error refreshing table on scene change: {e}")

    def _refresh_table_content(self, widget):
        """Refresh the table content with current scene data."""
        widget.clear()
        rows = MatUtils.get_file_nodes(
            return_type="shaderName|path|fileNodeName|fileNode", raw=True
        )
        if not rows:
            rows = [("", "", "No file nodes found", None)]

        formatted = [
            [shader_name, path, (file_node_name, file_node)]
            for shader_name, path, file_node_name, file_node in rows
        ]

        widget.add(formatted, headers=["Shader", "Texture Path", "File Node"])

        # Configure column resizing behavior
        header = widget.horizontalHeader()
        header.setSectionsMovable(False)  # Prevent column reordering
        header.setSectionResizeMode(
            0, self.sb.QtWidgets.QHeaderView.Interactive
        )  # Shader column - manually resizable
        header.setSectionResizeMode(
            1, self.sb.QtWidgets.QHeaderView.Stretch
        )  # Texture Path column - stretches to fill
        header.setSectionResizeMode(
            2, self.sb.QtWidgets.QHeaderView.Interactive
        )  # File Node column - manually resizable

        # Set minimum column widths for usability
        widget.setColumnWidth(0, 200)  # Shader column minimum width
        widget.setColumnWidth(2, 200)  # File Node column minimum width

        self.setup_formatting(widget)
        widget.apply_formatting()

    def cleanup_scene_callbacks(self):
        """Clean up any active Maya scriptJobs when the widget is destroyed."""
        try:
            if self._scene_change_job_id is not None:
                # Handle both single job ID (int) and multiple job IDs (list)
                job_ids = (
                    self._scene_change_job_id
                    if isinstance(self._scene_change_job_id, list)
                    else [self._scene_change_job_id]
                )

                for job_id in job_ids:
                    try:
                        pm.scriptJob(kill=job_id, force=True)
                        print(
                            f"TexturePathEditor: Cleaned up scene change scriptJob (ID: {job_id})"
                        )
                    except Exception as e:
                        print(
                            f"TexturePathEditor: Error cleaning up scriptJob {job_id}: {e}"
                        )

                self._scene_change_job_id = None
        except Exception as e:
            print(f"TexturePathEditor: Error cleaning up scene change scriptJobs: {e}")

    def __del__(self):
        """Ensure cleanup when the object is destroyed."""
        self.cleanup_scene_callbacks()

    def setup_formatting(self, widget):
        source_root = EnvUtils.get_env_info("workspace")

        def format_if_invalid(item, value, row, col, *_):
            path = str(value).strip()
            abs_path = (
                os.path.normpath(os.path.join(source_root, path))
                if not os.path.isabs(path)
                else os.path.normpath(path)
            )
            exists = os.path.exists(abs_path)
            widget.set_action_color(item, "reset" if exists else "invalid", row, col)
            item.setToolTip("" if exists else f"Missing file:\n{abs_path}")

        widget.set_column_formatter(1, format_if_invalid)

    def _handle_menu_action(self, widget):
        """Handle menu item interactions by calling the corresponding method."""
        object_name = widget.objectName()
        if hasattr(self, object_name):
            method = getattr(self, object_name)
            method()

    def delete_file_node(self):
        """Delete the selected file node."""
        widget = self.ui.tbl000
        current_row = widget.currentRow()
        if current_row < 0:
            pm.displayWarning("No row selected.")
            return

        # Get the file node data from the third column
        file_node_data = widget.item_data(current_row, 2)
        if not file_node_data or not hasattr(file_node_data, "name"):
            pm.displayWarning("No valid file node found in selected row.")
            return

        try:
            node_name = file_node_data.name()

            # Confirm deletion
            reply = self.sb.message_box(
                f"Are you sure you want to delete the file node '{node_name}'?",
                "Yes",
                "No",
            )

            if reply == "Yes":
                # Delete the node
                pm.delete(file_node_data)
                pm.displayInfo(f"Deleted file node: {node_name}")

                # Refresh the table to show updated data
                widget.init_slot()

        except Exception as e:
            pm.displayError(f"Failed to delete file node: {str(e)}")

    def select_file_node(self):
        """Select the file node from the current row."""
        widget = self.ui.tbl000
        current_row = widget.currentRow()
        if current_row < 0:
            pm.displayWarning("No row selected.")
            return

        # Get the file node data from the third column
        file_node_data = widget.item_data(current_row, 2)
        if not file_node_data or not hasattr(file_node_data, "name"):
            pm.displayWarning("No valid file node found in selected row.")
            return

        try:
            pm.select(file_node_data, r=True)
            pm.displayInfo(f"Selected file node: {file_node_data.name()}")
        except Exception as e:
            pm.displayError(f"Failed to select file node: {str(e)}")

    def remap_to_relative(self):
        """Remap the selected file node's texture path to a relative path."""
        widget = self.ui.tbl000
        current_row = widget.currentRow()
        if current_row < 0:
            pm.displayWarning("No row selected.")
            return

        # Get the file node data from the third column
        file_node_data = widget.item_data(current_row, 2)
        if not file_node_data or not hasattr(file_node_data, "name"):
            pm.displayWarning("No valid file node found in selected row.")
            return

        try:
            node_name = file_node_data.name()

            # Get the current texture path
            if hasattr(file_node_data, "fileTextureName"):
                current_path = file_node_data.fileTextureName.get()

                # Use MatUtils to remap just this file node to relative path
                # Pass a list containing just this file node
                MatUtils.remap_texture_paths([file_node_data])

                # Get the new path after remapping
                new_path = file_node_data.fileTextureName.get()

                pm.displayInfo(
                    f"Remapped '{node_name}' texture path:\nFrom: {current_path}\nTo: {new_path}"
                )

                # Refresh the table to show updated path
                widget.init_slot()

            else:
                pm.displayWarning(
                    f"File node '{node_name}' does not have a fileTextureName attribute."
                )

        except Exception as e:
            pm.displayError(f"Failed to remap file node to relative path: {str(e)}")

    def handle_cell_edit(self, row: int, col: int):
        tbl = self.ui.tbl000
        value = tbl.item(row, col).text()

        if col == 1:  # File path update
            file_node = tbl.item_data(row, 2)
            if file_node and hasattr(file_node, "fileTextureName"):
                file_node.fileTextureName.set(value)
                tbl.apply_formatting()  # Recheck path formatting after update

        if col in (0, 2):
            node_name = tbl.item(row, col).text()
            if pm.objExists(node_name):
                pm.select(node_name, r=True)
                pm.mel.eval("NodeEditorWindow;")
                pm.mel.eval("nodeEditor -e -f true nodeEditor1;")


# --------------------------------------------------------------------------------------------

# module name
# print(__name__)
# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
