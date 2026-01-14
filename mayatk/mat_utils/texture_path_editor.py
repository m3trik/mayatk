# !/usr/bin/python
# coding=utf-8
import os

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
from uitk.widgets.footer import FooterStatusController

# From this package:
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.mat_utils._mat_utils import MatUtils


class TexturePathEditorSlots:
    _ROW_SELECTION_COLUMNS = {
        "shader": 0,
        "path": 1,
        "file_node": 2,
    }

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.texture_path_editor
        self._scene_change_job_id = None  # Store the scriptJob ID for cleanup
        self._refresh_pending = False  # Flag to debounce refresh calls
        self._footer_controller = self._create_footer_controller()

    def header_init(self, widget):
        """Initialize the header for the texture path editor."""
        # Add a button to open the hypershade editor.
        widget.menu.setTitle("Global Tasks:")

        widget.menu.add("Separator", setTitle="General")
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Refresh Texture List",
            setToolTip="Rescan the current scene and update the texture table.",
            setObjectName="refresh_texture_table",
        )
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Open Source Images",
            setToolTip="Open the project's sourceimages directory in the file explorer.",
            setObjectName="open_source_images",
        )
        widget.menu.add("Separator", setTitle="Path Management")
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Set Texture Directory",
            setToolTip="Set the texture file paths for all file nodes in the scene.\nPaths will be relative if they reside within the project's sourceimages directory.",
            setObjectName="lbl010",
        )
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Find and Copy Textures",
            setToolTip="Search recursively from a source directory for textures used by all file nodes in the scene, then copy them into a destination directory.\n\nNote: Arnold texture nodes are not supported.",
            setObjectName="lbl_find_copy",
        )
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Convert to Relative Paths",
            setToolTip="Convert all texture paths in the scene to relative paths based on the project's sourceimages directory.",
            setObjectName="lbl013",
        )
        widget.menu.add("Separator", setTitle="Selection")
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Select Textures for Selected Objects",
            setToolTip="Select the texture path cells in the table associated with the currently selected objects in the scene.",
            setObjectName="lbl014",
        )
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setText="Select Broken Paths",
            setToolTip="Select all rows in the table where the texture file is missing.",
            setObjectName="lbl015",
        )

    def open_source_images(self):
        """Open the project's sourceimages directory."""
        path = EnvUtils.get_env_info("sourceimages")
        if path and os.path.exists(path):
            os.startfile(path)
        else:
            pm.warning(f"Source images directory not found: {path}")

    def lbl010(self):
        """Set Texture Paths for All File Nodes."""
        texture_dir = self.sb.dir_dialog(
            title="Set Texture Paths for All File Nodes",
            start_dir=EnvUtils.get_env_info("sourceimages"),
        )
        if not texture_dir:
            return

        all_file_nodes = pm.ls(type="file")
        if not all_file_nodes:
            pm.warning("No file nodes in the scene.")
            return

        pm.displayInfo(f"Setting texture paths to: {texture_dir}")
        MatUtils.remap_texture_paths(file_nodes=all_file_nodes, new_dir=texture_dir)

        # Refresh the table widget to show updated paths
        self.ui.tbl000.init_slot()

    def lbl_find_copy(self):
        """Find and Copy Textures (Global)"""
        all_file_nodes = pm.ls(type="file")
        if not all_file_nodes:
            pm.warning("No file nodes in the scene.")
            return

        self._find_and_copy_workflow(all_file_nodes)

    def _find_and_copy_workflow(self, file_nodes):
        """Shared workflow for finding and copying textures."""
        start_dir = EnvUtils.get_env_info("sourceimages")
        source_dir = self.sb.dir_dialog(
            title="Select a root directory to recursively search for textures:",
            start_dir=start_dir,
        )
        if not source_dir:
            return

        found_textures = MatUtils.find_texture_files(
            file_nodes=file_nodes, source_dir=source_dir, recursive=True
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

        # Filter file nodes to only remap ones that were successfully found and copied
        found_basenames = {os.path.basename(f).lower() for f in found_textures}
        nodes_to_remap = []
        for node in file_nodes:
            # Check if node's texture name matches one of the found ones
            try:
                path = node.fileTextureName.get()
                if path and os.path.basename(path).lower() in found_basenames:
                    nodes_to_remap.append(node)
            except Exception:
                continue

        if nodes_to_remap:
            # We manualy remap here to ensure paths are flattened (matching the copy operation)
            # using MatUtils.remap_texture_paths would attempt to preserve relative directory structure
            # which breaks if the file was moved from a subdirectory to the root of dest_dir.
            project_sourceimages = EnvUtils.get_env_info("sourceimages")
            sourceimages_name = (
                os.path.basename(project_sourceimages) if project_sourceimages else ""
            )
            if project_sourceimages:
                project_sourceimages = os.path.abspath(project_sourceimages).replace(
                    "\\", "/"
                )

            pm.undoInfo(openChunk=True, chunkName="Remap Found Textures")
            try:
                count = 0
                for node in nodes_to_remap:
                    path = node.fileTextureName.get()
                    if not path:
                        continue

                    # Create new path based on destination directory + original filename (flattened)
                    filename = os.path.basename(path)
                    new_abs_path = os.path.normpath(
                        os.path.join(dest_dir, filename)
                    ).replace("\\", "/")

                    final_path = new_abs_path

                    # Convert to relative path if inside sourceimages
                    if project_sourceimages and new_abs_path.lower().startswith(
                        project_sourceimages.lower()
                    ):
                        rel = os.path.relpath(
                            new_abs_path, project_sourceimages
                        ).replace("\\", "/")
                        if sourceimages_name and not rel.startswith(
                            sourceimages_name + "/"
                        ):
                            final_path = f"{sourceimages_name}/{rel}"
                        else:
                            final_path = rel

                    node.fileTextureName.set(final_path)
                    count += 1
                pm.displayInfo(f"Remapped {count} file nodes.")
            finally:
                pm.undoInfo(closeChunk=True)
        else:
            pm.warning("No file nodes matched the copied textures.")

        # Refresh the table widget to show updated paths
        self.ui.tbl000.init_slot()

    def lbl013(self):
        """Convert to Relative Paths (Global)"""
        all_file_nodes = pm.ls(type="file")
        if not all_file_nodes:
            pm.warning("No file nodes in the scene.")
            return
        MatUtils.remap_texture_paths(file_nodes=all_file_nodes)

        # Refresh the table widget to show updated paths
        self.ui.tbl000.init_slot()

    def refresh_texture_table(self):
        """Manual refresh trigger from the header menu."""
        table = getattr(self.ui, "tbl000", None)
        if not table:
            return
        table.init_slot()

    def tbl000_init(self, widget):
        if not widget.is_initialized:
            widget.refresh_on_show = True
            widget.cellChanged.connect(self.handle_cell_edit)
            if self._footer_controller:
                widget.itemSelectionChanged.connect(self._footer_controller.update)

            # Add context menu items using the existing menu system
            widget.menu.setTitle("Context Tasks:")

            widget.menu.add("Separator", setTitle="Path Management")
            widget.menu.add(
                "QPushButton",
                setText="Set Directory",
                setObjectName="row_set_texture_directory",
                setToolTip="Move or relink the texture for this row to a new directory.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Find and Copy Texture",
                setObjectName="row_find_and_copy_texture",
                setToolTip="Search for this texture in a folder and copy any matches to a destination.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Convert to Relative Path",
                setObjectName="remap_to_relative",
                setToolTip="Convert the selected file node's texture path to a relative path",
            )

            widget.menu.add("Separator", setTitle="Selection")
            widget.menu.add(
                "QPushButton",
                setText="Select In Scene",
                setObjectName="select_material",
                setToolTip="Select all scene objects currently assigned to this material",
            )
            widget.menu.add(
                "QPushButton",
                setText="Select File Node",
                setObjectName="select_file_node",
                setToolTip="Select the file node in Maya",
            )
            widget.menu.add(
                "QPushButton",
                setText="Show in Hypershade",
                setObjectName="row_show_in_hypershade",
                setToolTip="Graph the selected file node in the Hypershade editor.",
            )

            widget.menu.add("Separator", setTitle="Edit")
            widget.menu.add(
                "QPushButton",
                setText="Delete File Node",
                setObjectName="delete_file_node",
                setToolTip="Delete the selected file node from Maya",
            )

            def _bind_menu_action(action_name, method, columns=None):
                widget.register_menu_action(
                    action_name,
                    lambda selection, fn=method: fn(selection),
                    columns=columns or self._ROW_SELECTION_COLUMNS,
                )

            _bind_menu_action(
                "row_set_texture_directory", self.row_set_texture_directory
            )
            _bind_menu_action(
                "row_find_and_copy_texture", self.row_find_and_copy_texture
            )

            _bind_menu_action("remap_to_relative", self.remap_to_relative)
            _bind_menu_action("select_material", self.select_material)
            _bind_menu_action("select_file_node", self.select_file_node)
            _bind_menu_action("row_show_in_hypershade", self.row_show_in_hypershade)
            _bind_menu_action("delete_file_node", self.delete_file_node)

            # Set up Maya scriptJob to refresh on scene changes
            self._setup_scene_change_callback(widget)

            # Ensure cleanup when widget is destroyed
            try:
                widget.destroyed.connect(self.cleanup_scene_callbacks)
            except Exception:
                pass

        self._refresh_table_content(widget)

    def _setup_scene_change_callback(self, widget):
        """Set up Maya scriptJob to refresh the table when scene changes."""
        try:
            # Clean up any existing scriptJob first
            self.cleanup_scene_callbacks()

            # Create new scriptJob for multiple scene change events
            # We'll use a list to handle multiple events with the same callback
            events_to_watch = [
                "SceneOpened",  # When a scene is opened
                "NewSceneOpened",  # When a new scene is created
                "SceneImported",  # When a scene is imported (though this might not always trigger)
                "workspaceChanged",  # When the workspace is changed
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
        # Debounce: if a refresh is already pending, skip this call
        if self._refresh_pending:
            return

        self._refresh_pending = True

        def do_refresh():
            self._refresh_pending = False
            try:
                # Check if widget is still valid
                try:
                    if not widget.isVisible():
                        # If widget is hidden or destroyed, we might want to cleanup
                        pass
                except RuntimeError:
                    # Widget has been deleted (C++ object deleted)
                    self.cleanup_scene_callbacks()
                    return

                print(
                    "TexturePathEditor: Scene changed, refreshing texture path table..."
                )
                self._refresh_table_content(widget)
            except Exception as e:
                print(f"TexturePathEditor: Error refreshing table on scene change: {e}")

        # Use evalDeferred to coalesce multiple events and ensure scene is ready
        pm.evalDeferred(do_refresh)

    def _refresh_table_content(self, widget):
        """Refresh the table content with current scene data."""
        # Show wait cursor during refresh for large scenes
        pm.waitCursor(state=True)
        try:
            widget.setUpdatesEnabled(False)
            widget.blockSignals(
                True
            )  # Prevent cellChanged from firing during population
            widget.clear()
            # Optimization: Request strings only to avoid PyNode creation overhead
            rows = MatUtils.get_file_nodes(
                return_type="shaderName|path|fileNodeName", raw=True
            )
            if not rows:
                rows = [("", "", "No file nodes found")]

            formatted = []
            for shader_name, path, file_node_name in rows:
                # Pass strings for display; PyNodes will be resolved on-demand by context menu actions
                formatted.append([shader_name, path, file_node_name])

            # Populate table (triggers cellChanged if signals not blocked)
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

            # Setup and apply formatting in one pass (avoid double formatting)
            self.setup_formatting(widget)
            # Note: widget.add() already calls apply_formatting(), but we need our
            # custom formatter applied, so we call it once more after setup
            widget.apply_formatting()
        finally:
            widget.blockSignals(False)
            widget.setUpdatesEnabled(True)
            pm.waitCursor(state=False)

        if self._footer_controller:
            self._footer_controller.update()

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

                def kill_jobs(ids):
                    for job_id in ids:
                        try:
                            if pm.scriptJob(exists=job_id):
                                pm.scriptJob(kill=job_id, force=True)
                                print(
                                    f"TexturePathEditor: Cleaned up scene change scriptJob (ID: {job_id})"
                                )
                        except Exception as e:
                            print(
                                f"TexturePathEditor: Error cleaning up scriptJob {job_id}: {e}"
                            )

                # Use evalDeferred to avoid "cannot kill running scriptJob" error
                # We pass the list of IDs to the deferred function
                pm.evalDeferred(lambda: kill_jobs(job_ids))

                self._scene_change_job_id = None
        except Exception as e:
            print(f"TexturePathEditor: Error cleaning up scene change scriptJobs: {e}")

    def __del__(self):
        """Ensure cleanup when the object is destroyed."""
        self.cleanup_scene_callbacks()

    def setup_formatting(self, widget):
        source_root = EnvUtils.get_env_info("workspace")

        # Pre-compute all unique paths and check existence in batch
        # This avoids repeated os.path.exists() calls during formatting
        path_cache = {}

        # Collect all unique paths first
        unique_paths = set()
        for row in range(widget.rowCount()):
            item = widget.item(row, 1)
            if item:
                path = str(item.text()).strip()
                if path:
                    unique_paths.add(path)

        # Batch check file existence (potentially parallelized for network paths)
        def resolve_and_check(path):
            abs_path = (
                os.path.normpath(os.path.join(source_root, path))
                if not os.path.isabs(path)
                else os.path.normpath(path)
            )
            return path, os.path.exists(abs_path), abs_path

        # For small datasets, check inline; for large datasets, use threading
        if len(unique_paths) > 50:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {
                    executor.submit(resolve_and_check, p): p for p in unique_paths
                }
                for future in as_completed(futures):
                    try:
                        path, exists, abs_path = future.result(timeout=5)
                        path_cache[path] = (exists, abs_path)
                    except Exception:
                        path = futures[future]
                        abs_path = (
                            os.path.normpath(os.path.join(source_root, path))
                            if not os.path.isabs(path)
                            else os.path.normpath(path)
                        )
                        path_cache[path] = (False, abs_path)
        else:
            for path in unique_paths:
                _, exists, abs_path = resolve_and_check(path)
                path_cache[path] = (exists, abs_path)

        def format_if_invalid(item, value, row, col, *_):
            path = str(value).strip()

            if path in path_cache:
                exists, abs_path = path_cache[path]
            else:
                # Fallback for any paths not in cache
                abs_path = (
                    os.path.normpath(os.path.join(source_root, path))
                    if not os.path.isabs(path)
                    else os.path.normpath(path)
                )
                exists = os.path.exists(abs_path)
                path_cache[path] = (exists, abs_path)

            widget.format_item(item, key="reset" if exists else "invalid")
            item.setToolTip("" if exists else f"Missing file:\n{abs_path}")

        widget.set_column_formatter(1, format_if_invalid)

    def _resolve_context(self, shader_name, file_node_data):
        shader_name = str(shader_name).strip() if shader_name else ""
        shader_node = None
        if shader_name:
            try:
                shader_node = pm.PyNode(shader_name)
            except (pm.MayaNodeError, TypeError):
                shader_node = None

        # Resolve actual PyNode for the file node column payload
        if isinstance(file_node_data, (list, tuple)):
            file_node_data = next(
                (value for value in file_node_data if hasattr(value, "name")), None
            )
        elif file_node_data and not hasattr(file_node_data, "name"):
            try:
                file_node_data = pm.PyNode(file_node_data)
            except Exception:
                file_node_data = None

        # OPTIMIZATION: Use file_node_data directly if available instead of
        # calling get_file_nodes which is expensive
        material_file_nodes = []
        if file_node_data:
            material_file_nodes = [file_node_data]
        elif shader_node:
            # Use listHistory directly on the shader - much faster than get_file_nodes
            try:
                history = pm.listHistory(shader_node, type="file") or []
                material_file_nodes = list(dict.fromkeys(history))  # unique, ordered
            except Exception:
                material_file_nodes = []

        return {
            "shader_name": shader_name,
            "shader_node": shader_node,
            "file_node": file_node_data,
            "file_nodes": material_file_nodes,
        }

    def _get_selected_contexts(
        self,
        selection=None,
        require_file_nodes: bool = True,
        warn_on_empty: bool = False,
    ):
        table = getattr(self.ui, "tbl000", None)
        if table is None:
            return []

        if selection is None:
            selection = table.get_selection(
                columns=self._ROW_SELECTION_COLUMNS,
                include_current=True,
            )

        if not selection:
            if warn_on_empty:
                pm.displayWarning("No row selected.")
            return []

        contexts = []
        for entry in selection:
            shader_value = self._selection_value(entry, "shader")
            file_node_value = self._selection_value(entry, "file_node")
            context = self._resolve_context(shader_value, file_node_value)
            if require_file_nodes and not context["file_nodes"]:
                continue
            contexts.append(context)

        if require_file_nodes and not contexts:
            pm.displayWarning("No valid file nodes found in the selected row(s).")
            return []

        return contexts

    def _selection_value(self, entry, key: str):
        if hasattr(entry, "values"):
            return entry.values.get(key)
        if hasattr(entry, "get"):
            try:
                value = entry.get(key)
                if value is not None:
                    return value
            except TypeError:
                pass
        column = self._ROW_SELECTION_COLUMNS.get(key)
        if column is not None and isinstance(entry, dict):
            return entry.get(column)
        return None

    def _resolve_absolute_texture_path(self, file_node):
        try:
            path_value = file_node.fileTextureName.get()
        except Exception:
            return ""
        if not path_value:
            return ""
        project_sourceimages = EnvUtils.get_env_info("sourceimages") or ""
        if os.path.isabs(path_value):
            return os.path.abspath(path_value)
        return os.path.abspath(os.path.join(project_sourceimages, path_value))

    def _remap_context_textures(self, contexts, **kwargs):
        contexts = contexts or []
        file_nodes = []
        shader_names = []
        for context in contexts:
            if not context:
                continue
            nodes = context.get("file_nodes") or []
            if nodes:
                file_nodes.extend(nodes)
            else:
                shader_name = context.get("shader_name")
                if shader_name:
                    shader_names.append(shader_name)

        if file_nodes:
            MatUtils.remap_texture_paths(
                file_nodes=MatUtils._unique_ordered(file_nodes), **kwargs
            )
        elif shader_names:
            MatUtils.remap_texture_paths(materials=shader_names, **kwargs)

    def delete_file_node(self, selection=None):
        """Delete the selected file nodes."""
        contexts = self._get_selected_contexts(selection)
        if not contexts:
            return

        nodes_to_delete = []
        for context in contexts:
            file_node = context.get("file_node") or (
                context["file_nodes"][0] if context["file_nodes"] else None
            )
            if file_node:
                nodes_to_delete.append(file_node)

        if not nodes_to_delete:
            return

        # Deduplicate
        nodes_to_delete = list(set(nodes_to_delete))
        node_names = [n.name() for n in nodes_to_delete]

        count = len(nodes_to_delete)
        msg = f"Are you sure you want to delete {count} file node(s)?"
        if count == 1:
            msg = f"Are you sure you want to delete the file node '{node_names[0]}'?"

        reply = self.sb.message_box(msg, "Yes", "No")

        if reply == "Yes":
            try:
                pm.delete(nodes_to_delete)
                pm.displayInfo(f"Deleted {count} file node(s).")
                self.ui.tbl000.init_slot()
            except Exception as e:
                pm.displayError(f"Failed to delete file nodes: {str(e)}")

    def select_file_node(self, selection=None):
        """Select the file nodes from the selected rows."""
        contexts = self._get_selected_contexts(selection)
        if not contexts:
            return

        nodes_to_select = []
        for context in contexts:
            file_node = context.get("file_node") or (
                context["file_nodes"][0] if context["file_nodes"] else None
            )
            if file_node:
                nodes_to_select.append(file_node)

        if not nodes_to_select:
            return

        try:
            pm.select(nodes_to_select, r=True)
            pm.displayInfo(f"Selected {len(nodes_to_select)} file node(s).")
        except Exception as e:
            pm.displayError(f"Failed to select file nodes: {str(e)}")

    def row_show_in_hypershade(self, selection=None):
        """Graph the selected file node in the Hypershade."""
        contexts = self._get_selected_contexts(selection)
        if not contexts:
            return

        nodes_to_graph = []
        for context in contexts:
            file_node = context.get("file_node") or (
                context["file_nodes"][0] if context["file_nodes"] else None
            )
            if file_node:
                nodes_to_graph.append(file_node)

        if not nodes_to_graph:
            return

        MatUtils.graph_materials(nodes_to_graph)

    def select_material(self, selection=None):
        """Select the materials associated with the selected rows."""
        contexts = self._get_selected_contexts(
            selection,
            require_file_nodes=False,
        )
        if not contexts:
            return

        all_assigned_objects = []
        for context in contexts:
            shader_name = context.get("shader_name")
            if not shader_name:
                continue
            try:
                assigned = MatUtils.find_by_mat_id(shader_name, shell=True)
                if assigned:
                    all_assigned_objects.extend(assigned)
            except Exception as e:
                print(f"Failed to query objects for '{shader_name}': {e}")

        if not all_assigned_objects:
            pm.displayWarning("No scene objects found for the selected materials.")
            return

        try:
            pm.select(all_assigned_objects, r=True)
            pm.displayInfo(f"Selected objects for {len(contexts)} material(s).")
        except Exception as e:
            pm.displayError(f"Failed to select objects: {str(e)}")

    def remap_to_relative(self, selection=None):
        """Remap the selected file nodes' texture paths to relative paths."""
        contexts = self._get_selected_contexts(selection)
        if not contexts:
            return

        try:
            self._remap_context_textures(contexts)

            pm.displayInfo(
                f"Remapped textures for {len(contexts)} item(s) to relative paths."
            )
            self.ui.tbl000.init_slot()
        except Exception as e:
            pm.displayError(f"Failed to remap file nodes to relative path: {str(e)}")

    def row_set_texture_directory(self, selection=None):
        contexts = self._get_selected_contexts(selection)
        if not contexts:
            return
        target_dir = self.sb.dir_dialog(
            title="Select a directory for these textures:",
            start_dir=EnvUtils.get_env_info("sourceimages"),
        )
        if not target_dir:
            return

        self._remap_context_textures(contexts, new_dir=target_dir)

        self.ui.tbl000.init_slot()

    def row_find_and_copy_texture(self, selection=None):
        contexts = self._get_selected_contexts(selection)
        if not contexts:
            return

        all_file_nodes = []
        for context in contexts:
            all_file_nodes.extend(context["file_nodes"])

        if not all_file_nodes:
            pm.warning("No file nodes found in selection.")
            return

        self._find_and_copy_workflow(all_file_nodes)

    def handle_cell_edit(self, row: int, col: int):
        tbl = self.ui.tbl000
        value = tbl.item(row, col).text()

        if col == 0:  # Shader rename
            shader_node = tbl.item_data(row, 0)
            if shader_node and hasattr(shader_node, "rename"):
                try:
                    shader_node.rename(value)
                    pm.displayInfo(f"Renamed shader to: {value}")
                except Exception as e:
                    pm.warning(f"Failed to rename shader: {e}")

        elif col == 1:  # File path update
            file_node = tbl.item_data(row, 2)
            if file_node and hasattr(file_node, "fileTextureName"):
                file_node.fileTextureName.set(value)
                pm.displayInfo(f"Updated texture path to: {value}")
                tbl.apply_formatting()  # Recheck path formatting after update
                if self._footer_controller:
                    self._footer_controller.update()

        elif col == 2:  # File node rename
            file_node = tbl.item_data(row, 2)
            if file_node and hasattr(file_node, "rename"):
                try:
                    file_node.rename(value)
                    pm.displayInfo(f"Renamed file node to: {value}")
                except Exception as e:
                    pm.warning(f"Failed to rename file node: {e}")

        if col in (0, 2):
            node_name = value
            if pm.objExists(node_name):
                pm.select(node_name, r=True)
                try:
                    pm.mel.eval("NodeEditorWindow;")
                    # Attempt to find the Node Editor panel
                    editors = pm.getPanel(scriptType="nodeEditorPanel")
                    if editors:
                        # Use the first found editor
                        editor_name = editors[0] + "NodeEditorEd"
                        pm.mel.eval(f"nodeEditor -e -f true {editor_name};")
                    else:
                        # Fallback to default name if query fails
                        pm.mel.eval("nodeEditor -e -f true nodeEditor1;")
                except Exception:
                    # Fail silently if Node Editor interaction doesn't work (non-critical)
                    pass

    def _create_footer_controller(self):
        footer = getattr(self.ui, "footer", None)
        if not footer:
            return None
        return FooterStatusController(
            footer=footer,
            resolver=self._resolve_source_images_path,
            default_text="",
            truncate_kwargs={"length": 96, "mode": "middle"},
        )

    def _resolve_source_images_path(self) -> str:
        return EnvUtils.get_env_info("sourceimages") or ""

    def lbl014(self):
        """Select table rows associated with selected objects."""
        selection = pm.ls(sl=True, flatten=True)
        if not selection:
            self.sb.message_box("Select object(s) first.")
            return

        # Get materials from selection
        mats = MatUtils.get_mats(selection)
        if not mats:
            pm.warning("No materials found on selected objects.")
            return

        # OPTIMIZATION: Get file nodes directly via listHistory on materials
        # instead of calling get_file_nodes which rebuilds the entire mapping
        target_node_names = set()
        for mat in mats:
            try:
                file_nodes = pm.listHistory(mat, type="file") or []
                for fn in file_nodes:
                    target_node_names.add(fn.name())
            except Exception:
                pass

        if not target_node_names:
            pm.warning("No file nodes found for selected objects.")
            return

        table = self.ui.tbl000
        table.clearSelection()

        selected_count = 0

        for row in range(table.rowCount()):
            # Column 2 contains the file node data
            node_data = table.item_data(row, 2)
            if not node_data:
                continue

            node_name = (
                node_data.name() if hasattr(node_data, "name") else str(node_data)
            )

            if node_name in target_node_names:
                # Select the Texture Path cell (Column 1)
                path_item = table.item(row, 1)
                if path_item:
                    path_item.setSelected(True)
                    selected_count += 1

                    # Scroll to the first selected item
                    if selected_count == 1:
                        table.scrollToItem(path_item)

        if selected_count > 0:
            pm.displayInfo(f"Selected {selected_count} rows in the table.")

    def lbl015(self):
        """Select Broken Paths"""
        widget = self.ui.tbl000
        source_root = EnvUtils.get_env_info("workspace")

        # Clear current selection
        widget.clearSelection()

        # Select rows with broken paths
        selection_mode = widget.selectionMode()
        widget.setSelectionMode(self.sb.QtWidgets.QAbstractItemView.MultiSelection)

        rows_to_select = []
        try:
            for row in range(widget.rowCount()):
                item = widget.item(row, 1)
                if not item:
                    continue

                path = str(item.text()).strip()
                if not path:
                    continue

                abs_path = (
                    os.path.normpath(os.path.join(source_root, path))
                    if not os.path.isabs(path)
                    else os.path.normpath(path)
                )

                if not os.path.exists(abs_path):
                    rows_to_select.append(row)

            if rows_to_select:
                for row in rows_to_select:
                    # Select the Texture Path cell (Column 1)
                    path_item = widget.item(row, 1)
                    if path_item:
                        path_item.setSelected(True)

                # Scroll to first selected
                widget.scrollToItem(widget.item(rows_to_select[0], 1))

                pm.displayInfo(f"Selected {len(rows_to_select)} broken paths.")
            else:
                pm.displayInfo("No broken paths found.")

        except Exception as e:
            pm.warning(f"Error selecting broken paths: {e}")
        finally:
            widget.setSelectionMode(selection_mode)


# --------------------------------------------------------------------------------------------

# module name
# print(__name__)
# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
