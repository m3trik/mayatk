# !/usr/bin/python
# coding=utf-8
import os
import re
from functools import partial, wraps
from typing import Optional

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# From this package:
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.env_utils.workspace_manager import WorkspaceManager


class AssemblyManager:
    @classmethod
    def current_references(cls):
        """Get the current scene references.

        Returns:
            list: A list of FileReference objects representing the current scene references.
        """
        return pm.system.listReferences()

    @classmethod
    def create_assembly_definition(cls, namespace: str, file_path: str) -> str:
        """Create an assembly definition for the given file path.

        Parameters:
            namespace (str): The namespace to be used for the assembly.
            file_path (str): The file path of the scene to create the assembly from.

        Returns:
            str: The name of the created representation, or None if the creation failed.
        """
        try:
            # Validate file path
            if not pm.util.path(file_path).exists():
                pm.displayError(f"File does not exist: {file_path}")
                return None

            # Create assembly definition
            assembly_name = f"{namespace}_assembly"
            assembly_node = pm.assembly(name=assembly_name, type="assemblyDefinition")

            # Create representation
            rep_name = pm.assembly(
                assembly_node, edit=True, createRepresentation="Scene", input=file_path
            )
            representations = pm.assembly(
                assembly_node, query=True, listRepresentations=True
            )
            return representations[0] if representations else None
        except Exception as e:
            pm.displayError(f"Failed to create assembly definition for {file_path}")
            return None

    @classmethod
    def set_active_representation(
        cls, assembly_node: str, representation_name: str
    ) -> bool:
        """Set the active representation for an assembly.

        Parameters:
            assembly_node (str): The name of the assembly node.
            representation_name (str): The name of the representation to set as active.

        Returns:
            bool: True if the representation was successfully set as active, False otherwise.
        """
        try:
            pm.assembly(assembly_node, edit=True, active=representation_name)
            return True
        except Exception as e:
            pm.displayError(f"Failed to set active representation for {assembly_node}")
            return False

    @classmethod
    def convert_references_to_assemblies(cls):
        """Convert all current references to assembly definitions and references.

        Iterates through all current references, creates an assembly definition for each,
        sets the active representation, and optionally removes the original reference after conversion.
        """
        for ref in cls.current_references():
            namespace = ref.namespace
            file_path = ref.path

            rep_name = cls.create_assembly_definition(namespace, file_path)
            if rep_name:
                assembly_name = f"{namespace}_assembly"
                if cls.set_active_representation(assembly_name, rep_name):
                    # Optionally remove the original reference after conversion
                    ref.remove()
                else:
                    cls.logger.error(
                        f"Failed to set active representation for {assembly_name}"
                    )
            else:
                cls.logger.error(
                    f"Failed to create assembly definition for {file_path}"
                )


class ReferenceManager(WorkspaceManager, ptk.HelpMixin, ptk.LoggingMixin):
    """Core Maya scene reference management functionality.

    Features:
    - Add/remove references with namespace management
    - Import references into the scene
    - Update references from source files
    - Convert references to assemblies

    This class provides the core Maya reference functionality without any UI dependencies.
    For UI integration, use ReferenceManagerController and ReferenceManagerSlots.
    """

    def __init__(self):
        super().__init__()
        self._filter_text = ""
        self.prefilter_regex = re.compile(r".+\.\d{4}\.(ma|mb)$")

    @property
    def current_references(self):
        """Get the current scene references.
        Returns a list of FileReference objects.
        """
        return pm.system.listReferences()

    def _matches_prefilter_regex(self, filename):
        """Check if a file is an auto-save file based on its name."""
        return bool(self.prefilter_regex.match(filename))

    def _extract_strip_patterns(self, filter_text: str, delimiter=(",", ";")) -> list:
        """Extract the core patterns to strip from wildcard filter text.

        For example:
        - '*_v001*' -> ['_v001']
        - 'character_*' -> ['character_']
        - '*' -> []
        - '*_module.ma;C130*' -> ['_module.ma', 'C130']
        - 'test_*_rig' -> ['test_'] (takes the longest contiguous part)

        Parameters:
            filter_text (str): The filter text possibly containing multiple patterns.
            delimiter (str or tuple): Delimiter(s) used to split patterns.

        Returns:
            list: List of core patterns to strip from filenames.
        """
        import re

        if not filter_text:
            return []

        # Split by delimiters first
        if isinstance(delimiter, tuple):
            pattern = "|".join(re.escape(d) for d in delimiter)
            patterns = [p.strip() for p in re.split(pattern, filter_text) if p.strip()]
        elif delimiter in filter_text:
            patterns = [p.strip() for p in filter_text.split(delimiter) if p.strip()]
        else:
            patterns = [filter_text]

        strip_patterns = []
        for pattern in patterns:
            # If pattern is just wildcards, skip
            if pattern.replace("*", "").replace("?", "") == "":
                continue

            # Remove leading wildcards
            while pattern.startswith("*") or pattern.startswith("?"):
                pattern = pattern[1:]

            # Remove trailing wildcards
            while pattern.endswith("*") or pattern.endswith("?"):
                pattern = pattern[:-1]

            # If there are still wildcards in the middle, take the longest contiguous part
            if "*" in pattern or "?" in pattern:
                parts = [part for part in pattern.replace("?", "*").split("*") if part]
                if parts:
                    pattern = max(parts, key=len)
                else:
                    pattern = ""

            if pattern:
                strip_patterns.append(pattern)

        return strip_patterns

    def _extract_strip_pattern(self, filter_text: str) -> str:
        """Extract the core pattern to strip from wildcard filter text.

        DEPRECATED: Use _extract_strip_patterns() for multi-pattern support.

        For example:
        - '*_v001*' -> '_v001'
        - 'character_*' -> 'character_'
        - '*' -> '' (empty string)
        - 'literal_text' -> 'literal_text'
        - 'test_*_rig' -> 'test_' and '_rig' (but we'll take the longest contiguous part)
        """
        patterns = self._extract_strip_patterns(filter_text, delimiter=(",", ";"))
        return patterns[0] if patterns else ""

    @staticmethod
    def sanitize_namespace(namespace: str) -> str:
        """Sanitize the namespace by replacing or removing illegal characters."""
        return EnvUtils.sanitize_namespace(namespace)

    def add_reference(self, namespace: str, file_path: str) -> bool:
        # Ensure the file exists before proceeding
        if not os.path.exists(file_path):
            file_not_found_error_msg = f"File not found: {file_path}"
            self.logger.error(file_not_found_error_msg)
            pm.displayError(file_not_found_error_msg)
            return False

        # Check if the file is fully accessible (not virtual)
        try:
            with open(file_path, "rb") as f:
                f.read(1)  # Try to read a byte to ensure the file is accessible
        except (OSError, IOError) as e:
            error_msg = (
                f"Could not open file: {file_path}\n"
                f"Possible reasons include:\n"
                f"- The file is virtual or not fully downloaded\n"
                f"- There is an issue accessing the file (ex. permissions)\n"
                f"Error details: {str(e)}"
            )
            pm.displayError(error_msg)
            return False

        # Normalize the file path to ensure consistent comparison
        normalized_file_path = os.path.normcase(os.path.normpath(file_path))

        # Check if the file is already referenced
        for ref in self.current_references:
            if os.path.normcase(os.path.normpath(ref.path)) == normalized_file_path:
                return True  # Exit the method if the file is already referenced

        # Sanitize the namespace to ensure it contains only valid characters
        sanitized_namespace = self.sanitize_namespace(namespace)

        try:
            # Proceed with adding the reference since it's not already referenced
            ref = pm.createReference(file_path, namespace=sanitized_namespace)
            if ref is None or not hasattr(ref, "_refNode") or ref._refNode is None:
                raise RuntimeError(
                    f"Failed to create reference for {file_path}. Reference object or its _refNode attribute is None."
                )
            assert ref._refNode.type() == "reference"

            return True
        except AssertionError:
            pm.displayError(
                f"Reference created for {file_path} did not result in a valid reference node."
            )
            return False
        except RuntimeError as e:
            if "Could not open file" in str(e):
                pm.displayError(
                    f"Could not open file: {file_path} (Maya RuntimeError: {str(e)})"
                )
            else:
                raise
            return False

    def import_references(self, namespaces=None, remove_namespace=False):
        """Import referenced objects into the scene."""
        all_references = self.current_references

        if namespaces is not None:
            all_references = [
                ref
                for ref in all_references
                if ref.namespace in ptk.make_iterable(namespaces)
            ]

        with pm.UndoChunk():
            for ref in all_references:
                try:
                    ref.importContents(removeNamespace=remove_namespace)
                except RuntimeError as e:
                    self.logger.warning(
                        f"Failed to import reference '{ref.namespace}': {e}"
                    )

    def update_references(self):
        """Update all references to reflect the latest changes from the original files."""
        for ref in self.current_references:
            ref.load()

    def remove_references(self, namespaces=None):
        """Remove references based on their namespaces.

        If no namespace is provided, all references will be removed.

        Parameters:
            namespaces (str, list of str, or None): The namespace(s) of the reference(s) to be removed.
                If None, all references will be removed. Default is None.
        """
        all_references = self.current_references

        if namespaces is None:  # Unreference all
            for ref in all_references:
                ref.remove()
        else:
            namespaces = ptk.make_iterable(namespaces)
            for namespace in namespaces:
                matching_refs = [
                    ref for ref in all_references if ref.namespace == namespace
                ]
                for ref in matching_refs:
                    ref.remove()


class ReferenceManagerController(ReferenceManager, ptk.LoggingMixin):
    """Controller that bridges Maya reference functionality with UI interactions.

    This class extends ReferenceManager with UI-specific logic including:
    - Table widget management and item formatting
    - File selection and reference synchronization
    - Directory and workspace management
    - UI state management and signal blocking
    - Item editing and rename functionality

    UI Integration:
    - Manages table selection sync with Maya references
    - Handles file filtering and display name stripping
    - Controls workspace combo box updates
    - Manages current scene file highlighting and disabling

    Usage:
    - Select files in the table to add them as references
    - Double-click file names to rename display text
    """

    def __init__(self, slot, log_level="WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)

        self.slot = slot
        self.sb = slot.sb
        self.ui = slot.ui

        self._last_dir_valid = None
        self._updating_directory = False  # Flag to prevent cascading UI events
        self._editing_item = None  # Track which item is being edited
        self.logger.debug("ReferenceManagerController initialized.")

    @property
    def current_working_dir(self):
        # Use the parent class implementation but add logging
        working_dir = super().current_working_dir
        self.logger.debug(f"Getting current_working_dir: {working_dir}")
        return working_dir

    @current_working_dir.setter
    def current_working_dir(self, value):
        self.logger.debug(f"Setting current_working_dir to: {value}")

        # Validate directory first
        if not os.path.isdir(value):
            self.logger.warning(
                f"Invalid directory set as current_working_dir: {value}"
            )
            # Still set it for consistency, but it will be corrected by the parent property getter
            self._current_working_dir = value
            return

        old_value = getattr(self, "_current_working_dir", None)

        # Use parent class setter logic
        if os.path.isdir(value):
            self._current_working_dir = value
            # Only invalidate if the directory actually changed
            if old_value != value:
                self.logger.debug(
                    f"Directory changed from {old_value} to {value}, invalidating workspace files"
                )
                self.invalidate_workspace_files()
                # Don't call refresh_file_list here to avoid circular calls
                # Let the calling code handle the refresh timing
            else:
                self.logger.debug("Directory unchanged, no invalidation needed")

    def block_table_selection_method(method):
        @wraps(method)
        def wrapper(self, *args, **kwargs):
            t = self.ui.tbl000
            t.blockSignals(True)
            self.logger.debug(f"Blocking signals for method: {method.__name__}")
            try:
                return method(self, *args, **kwargs)
            finally:
                t.blockSignals(False)
                self.logger.debug(f"Unblocking signals for method: {method.__name__}")

        return wrapper

    def prepare_item_for_edit(self, item):
        """Prepare an item for editing by showing the full filename."""
        if item.column() != 0:  # Files column is at index 0
            return

        # Store the current editing item
        self._editing_item = item

        # Get the full filename for editing
        full_filename = item.data(self.sb.QtCore.Qt.UserRole + 1)
        if full_filename:
            item.setText(full_filename)
            self.logger.debug(
                f"Prepared item for edit with full filename: {full_filename}"
            )

    def restore_item_display(self, item):
        """Restore the item to its display name after editing."""
        if item.column() != 0:  # Files column is at index 0
            return

        # Clear the editing item tracker
        if self._editing_item == item:
            self._editing_item = None

        # Restore the display name
        display_name = item.data(self.sb.QtCore.Qt.UserRole + 2)
        if display_name:
            item.setText(display_name)
            self.logger.debug(f"Restored item display name: {display_name}")

    def is_item_being_edited(self, item):
        """Check if an item is currently being edited."""
        return self._editing_item == item

    def _format_table_item(self, item, file_path: str) -> None:
        """Apply enable/disable state based on whether the file is the current scene."""
        norm_fp = os.path.normcase(os.path.normpath(file_path))
        current_scene = (
            os.path.normcase(os.path.normpath(pm.sceneName())) if pm.sceneName() else ""
        )
        is_current_scene = norm_fp == current_scene

        if is_current_scene:
            # Disable the item and set a tooltip
            item.setFlags(
                item.flags()
                & ~(
                    self.sb.QtCore.Qt.ItemIsSelectable | self.sb.QtCore.Qt.ItemIsEnabled
                )
            )
            item.setToolTip(f"Current scene file - cannot be referenced\n{file_path}")
            # Apply current style (italic + orange) and mark as styled
            self.ui.tbl000.format_item(item, key="current", italic=True)
            item.setData(
                self.sb.QtCore.Qt.UserRole + 10, True
            )  # Mark as current-styled
        else:
            # Re-enable the item if it was previously disabled
            item.setFlags(
                item.flags()
                | (self.sb.QtCore.Qt.ItemIsSelectable | self.sb.QtCore.Qt.ItemIsEnabled)
            )
            # Only reset color if this item was previously styled as current scene
            was_current = item.data(self.sb.QtCore.Qt.UserRole + 10)
            if was_current:
                self.ui.tbl000.format_item(item, key="reset", italic=False)
                item.setData(self.sb.QtCore.Qt.UserRole + 10, False)  # Clear the marker

    def handle_item_selection(self):
        t = self.ui.tbl000
        selected_items = [
            t.item(idx.row(), 0)  # Files column is at index 0
            for idx in t.selectedIndexes()
            if idx.column() == 0 and t.item(idx.row(), 0)  # Files column is at index 0
        ]

        # Filter out disabled items (current scene) from selection data
        selected_data = set()
        current_scene = os.path.normpath(pm.sceneName()) if pm.sceneName() else ""

        # Clear selection of any disabled items (current scene) immediately
        items_to_deselect = []

        for item in selected_items:
            file_path = item.data(self.sb.QtCore.Qt.UserRole)
            norm_fp = os.path.normpath(file_path) if file_path else ""

            # Skip if this is the current scene file (disabled item)
            if norm_fp == current_scene:
                self.logger.debug(
                    f"Skipping current scene file in selection: {file_path}"
                )
                # Mark item for deselection
                items_to_deselect.append(item)
                continue

            # Skip if item is disabled (shouldn't be selectable)
            if not (item.flags() & self.sb.QtCore.Qt.ItemIsSelectable):
                self.logger.debug(f"Skipping disabled item in selection: {file_path}")
                items_to_deselect.append(item)
                continue

            selected_data.add((item.text(), file_path))

        # Deselect disabled items immediately to provide visual feedback
        for item in items_to_deselect:
            item.setSelected(False)

        current_references = self.current_references
        current_namespaces = {ref.namespace for ref in current_references}

        namespaces_to_add = {ns for ns, _ in selected_data} - current_namespaces
        namespaces_to_remove = current_namespaces - {ns for ns, _ in selected_data}

        self.logger.debug(
            f"Selected namespaces to add: {namespaces_to_add}, to remove: {namespaces_to_remove}"
        )

        for namespace in namespaces_to_remove:
            self.logger.debug(f"Removing reference for namespace: {namespace}")
            self.remove_references(namespace)

        for namespace in namespaces_to_add:
            file_path = next(fp for ns, fp in selected_data if ns == namespace)
            self.logger.debug(
                f"Adding reference for namespace: {namespace}, file_path: {file_path}"
            )
            success = self.add_reference(namespace, file_path)
            if not success:
                for item in selected_items:
                    if item.text() == namespace:
                        item.setSelected(False)
                        break

    @block_table_selection_method
    def sync_selection_to_references(self):
        """Sync the table selection to match current scene references."""
        t = self.ui.tbl000
        t.blockSignals(True)
        try:
            t.clearSelection()
            current_references = self.current_references
            current_scene = (
                os.path.normcase(os.path.normpath(pm.sceneName()))
                if pm.sceneName()
                else ""
            )

            # Create a mapping from file paths to namespaces for current references
            ref_path_to_namespace = {
                os.path.normcase(os.path.normpath(ref.path)): ref.namespace
                for ref in current_references
            }

            self.logger.debug(
                f"Syncing selection to current references: {[ref.namespace for ref in current_references]}"
            )
            self.logger.debug(
                f"Reference path to namespace mapping: {ref_path_to_namespace}"
            )

            for row in range(t.rowCount()):
                item = t.item(row, 0)  # Files column is at index 0
                if item:
                    file_path = item.data(self.sb.QtCore.Qt.UserRole)
                    norm_fp = (
                        os.path.normcase(os.path.normpath(file_path))
                        if file_path
                        else ""
                    )

                    # Check if this file path corresponds to a current reference
                    if norm_fp in ref_path_to_namespace:
                        # Don't select the current scene file even if it's somehow referenced
                        if norm_fp != current_scene and (
                            item.flags() & self.sb.QtCore.Qt.ItemIsSelectable
                        ):
                            item.setSelected(True)
                            namespace = ref_path_to_namespace[norm_fp]
                            self.logger.debug(
                                f"Selected item for reference: {item.text()} (namespace: {namespace})"
                            )
                        else:
                            self.logger.debug(
                                f"Skipped selecting disabled/current scene item: {item.text()}"
                            )
        finally:
            t.blockSignals(False)

    def update_current_dir(self, text: Optional[str] = None):
        # Prevent cascading updates during directory changes
        if self._updating_directory:
            self.logger.debug(
                "update_current_dir: Already updating directory, skipping"
            )
            return

        self._updating_directory = True
        try:
            text = text or self.ui.txt000.text()
            new_dir = os.path.normpath(text.strip())

            is_valid = os.path.isdir(new_dir)
            changed = new_dir != self.current_working_dir

            self.logger.debug(
                f"update_current_dir: new_dir='{new_dir}', current='{self.current_working_dir}', is_valid={is_valid}, changed={changed}, recursive={self.recursive_search}"
            )

            self.ui.txt000.setToolTip(new_dir if is_valid else "Invalid directory")
            self.ui.txt000.set_action_color("reset" if is_valid else "invalid")

            revalidate = is_valid and (changed or self._last_dir_valid is False)
            self._last_dir_valid = is_valid

            if revalidate:
                self.logger.debug(
                    "update_current_dir: Revalidating and updating current working dir."
                )
                # Update the current working directory first
                self.current_working_dir = new_dir
                # Update the workspace combo box with the new directory
                self._update_workspace_combo()
            elif not is_valid:
                self.logger.debug(
                    "update_current_dir: Directory is not valid, clearing workspace combo box."
                )
                self.ui.cmb000.clear()
                # Clear the file list as well since directory is invalid
                self.ui.tbl000.setRowCount(0)
                # Still update the working dir even if invalid for consistency
                self.current_working_dir = new_dir
            else:
                self.logger.debug(
                    "update_current_dir: No revalidation needed (directory unchanged and was already valid)"
                )
        finally:
            self._updating_directory = False

    def _update_workspace_combo(self):
        """Update the workspace combo box and refresh the file list."""
        self.logger.debug("_update_workspace_combo: Updating workspace combo box")

        # Find workspaces in the current directory
        workspaces = self.find_available_workspaces()

        # Block signals to prevent cascading events
        self.ui.cmb000.blockSignals(True)
        try:
            # Store current selection if any
            current_index = self.ui.cmb000.currentIndex()
            current_path = (
                self.ui.cmb000.itemData(current_index) if current_index >= 0 else None
            )

            # Clear and repopulate
            self.ui.cmb000.clear()
            self.ui.cmb000.add(workspaces)

            if workspaces:
                # Try to restore previous selection if it's still valid
                restored = False
                if current_path:
                    for i in range(self.ui.cmb000.count()):
                        if self.ui.cmb000.itemData(i) == current_path:
                            self.ui.cmb000.setCurrentIndex(i)
                            self.logger.debug(
                                f"_update_workspace_combo: Restored selection to index {i}"
                            )
                            restored = True
                            break

                # If we couldn't restore or there was no previous selection, select first
                if not restored:
                    self.ui.cmb000.setCurrentIndex(0)
                    self.logger.debug(
                        "_update_workspace_combo: Set selection to first workspace"
                    )

                self.logger.debug(
                    f"_update_workspace_combo: Found {len(workspaces)} workspaces"
                )
            else:
                self.logger.warning(
                    f"_update_workspace_combo: No workspaces found in {self.current_working_dir}"
                )

        finally:
            self.ui.cmb000.blockSignals(False)

        # Always refresh the file list for the selected workspace after updating combo box
        # Since signals were blocked, the normal cmb000 slot won't have been triggered
        if self.ui.cmb000.count() > 0 and self.ui.cmb000.currentIndex() >= 0:
            selected_workspace_path = self.ui.cmb000.itemData(
                self.ui.cmb000.currentIndex()
            )
            self.logger.debug(
                f"_update_workspace_combo: Refreshing file list for workspace: {selected_workspace_path}"
            )
            # Also update the current working dir to match the selected workspace
            if selected_workspace_path and os.path.isdir(selected_workspace_path):
                self.current_working_dir = selected_workspace_path

            # Invalidate cache to ensure we pick up any changes in workspace files
            # This is important when switching directories or when workspace contents might have changed
            self.refresh_file_list(invalidate=True)
        else:
            # Clear the table if no workspaces
            self.logger.debug(
                "_update_workspace_combo: No workspaces available, clearing table"
            )
            self.ui.tbl000.setRowCount(0)

    def refresh_file_list(self, invalidate=False):
        """Refresh the file list for the table widget."""
        # Use internal method for the table operations that need signal blocking
        self._refresh_file_list_internal(invalidate)

        # Ensure references are properly selected after table update (outside signal blocking)
        self.sync_selection_to_references()

    @block_table_selection_method
    def _refresh_file_list_internal(self, invalidate=False):
        """Internal method that refreshes the file list with signals blocked."""
        if invalidate:
            self.logger.debug("Invalidating workspace files cache.")
            self.invalidate_workspace_files()

        index = self.ui.cmb000.currentIndex()
        workspace_path = self.ui.cmb000.itemData(index)

        # If no workspace is selected, try to use current_working_dir as fallback
        if workspace_path is None:
            if index == -1 and self.ui.cmb000.count() > 0:
                # Combo box was just repopulated but currentIndex is still -1
                # This can happen during initialization, so just return without warning
                self.logger.debug(
                    "No workspace selected yet (combobox initializing) - skipping refresh"
                )
                return
            else:
                self.logger.warning("No workspace selected in combo box.")
                return

        self.logger.debug(f"Refreshing file list for workspace: {workspace_path}")

        if not workspace_path or not os.path.isdir(workspace_path):
            self.slot.logger.warning(
                f"[refresh_file_list] Invalid workspace: {workspace_path}"
            )
            return

        file_list = self.workspace_files.get(workspace_path, [])

        # Check for hide binary setting
        header_menu = self.slot.ui.header.menu
        hide_binary = getattr(header_menu, "chk_hide_binary", None)
        if hide_binary and hide_binary.isChecked():
            file_list = [f for f in file_list if not f.lower().endswith(".mb")]

        # Check for filter by suffix setting
        filter_suffix = getattr(header_menu, "chk_filter_suffix", None)
        suffix = getattr(header_menu, "txt_suffix", None)
        suffix_text = suffix.text() if suffix else ""

        if filter_suffix and filter_suffix.isChecked() and suffix_text:
            filtered_list = []
            for f in file_list:
                name_without_ext = os.path.splitext(os.path.basename(f))[0]
                if name_without_ext.endswith(suffix_text):
                    filtered_list.append(f)
            file_list = filtered_list

        # Check for filter by folder structure setting
        filter_structure = getattr(header_menu, "chk_filter_folder_structure", None)
        structure_text = ""
        txt_structure = getattr(header_menu, "txt_subfolder_structure", None)
        if txt_structure:
            structure_text = txt_structure.text().strip()

        chk_enable_folder = getattr(header_menu, "chk_enable_folder", None)
        use_folder = chk_enable_folder.isChecked() if chk_enable_folder else False

        if filter_structure and filter_structure.isChecked():
            # Determine the pattern to use
            pattern = structure_text
            if not pattern and use_folder:
                pattern = "{name}"

            if pattern:
                filtered_list = []
                # Create a copy of file_list to iterate over
                for f in list(file_list):
                    try:
                        # Get relative path of the file's directory
                        rel_dir = os.path.relpath(os.path.dirname(f), workspace_path)
                    except ValueError:
                        continue

                    base_name = os.path.splitext(os.path.basename(f))[0]

                    # Strip suffix if present and defined
                    if suffix_text and base_name.endswith(suffix_text):
                        name_for_path = base_name[: -len(suffix_text)]
                    else:
                        name_for_path = base_name

                    workspace_name = os.path.basename(workspace_path)
                    expected_rel_dir = ptk.StrUtils.replace_placeholders(
                        pattern,
                        name=name_for_path,
                        workspace=workspace_name,
                        suffix=suffix_text,
                    )

                    # Normalize paths for comparison (handle case sensitivity on Windows)
                    rel_dir_norm = os.path.normcase(os.path.normpath(rel_dir))
                    expected_rel_dir_norm = os.path.normcase(
                        os.path.normpath(expected_rel_dir)
                    )

                    # Check if rel_dir ends with expected_rel_dir (handling path separators)
                    # We split by separator to ensure we match full directory names
                    # This allows matching even if the file is deeper in the structure (e.g. inside 'scenes')
                    rel_parts = rel_dir_norm.split(os.sep)
                    exp_parts = expected_rel_dir_norm.split(os.sep)

                    if (
                        len(rel_parts) >= len(exp_parts)
                        and rel_parts[-len(exp_parts) :] == exp_parts
                    ):
                        filtered_list.append(f)
                file_list = filtered_list

        filter_text = self.ui.txt001.text().strip()

        # Check if filtering is enabled via checkbox
        filter_enabled = getattr(self.ui, "chk004", None)
        filter_enabled = (
            filter_enabled.isChecked() if filter_enabled else True
        )  # Default to True if checkbox doesn't exist

        # Check if ignore case is enabled via checkbox
        ignore_case = getattr(self.ui, "chk_ignore_case", None)
        ignore_case = (
            ignore_case.isChecked() if ignore_case else True
        )  # Default to True if checkbox doesn't exist

        if filter_text and filter_enabled:
            self.logger.debug(f"Filtering file list with filter: {filter_text}")
            file_list = ptk.filter_list(
                file_list,
                inc=filter_text,
                basename_only=True,
                delimiter=(",", ";"),
                match_all=True,
                ignore_case=ignore_case,
            )

        # Identify and include external references
        current_refs = self.current_references
        external_refs_paths = []

        # Get all files in current workspace to check against (unfiltered)
        full_workspace_files = self.workspace_files.get(workspace_path, [])
        full_workspace_files_set = set(
            os.path.normcase(os.path.normpath(f)) for f in full_workspace_files
        )

        for ref in current_refs:
            try:
                path = os.path.normcase(os.path.normpath(ref.path))
                # If path is not in the current workspace, it's external
                if path not in full_workspace_files_set:
                    # Avoid duplicates in external list
                    if path not in [
                        os.path.normcase(os.path.normpath(p))
                        for p in external_refs_paths
                    ]:
                        external_refs_paths.append(ref.path)
            except Exception:
                continue

        # Prepend external references to the file list
        if external_refs_paths:
            self.logger.debug(
                f"Adding {len(external_refs_paths)} external references to table."
            )
            file_list = external_refs_paths + file_list

        if not file_list:
            self.logger.warning(f"No scene files found in workspace: {workspace_path}")
        else:
            self.logger.debug(f"Found {len(file_list)} scenes to populate in table.")

        # Check settings
        header_menu = self.slot.ui.header.menu

        hide_suffix = getattr(header_menu, "chk_hide_suffix", None)
        hide_suffix_enabled = hide_suffix.isChecked() if hide_suffix else False
        suffix = getattr(header_menu, "txt_suffix", None)
        suffix_text = suffix.text() if suffix else ""

        hide_extension = getattr(header_menu, "chk_hide_extension", None)
        hide_extension_enabled = hide_extension.isChecked() if hide_extension else False

        # Generate file names, marking external references
        file_names = []
        for f in file_list:
            name = os.path.basename(f)

            # Apply hide extension
            if hide_extension_enabled:
                name = os.path.splitext(name)[0]

            # Apply hide suffix
            if hide_suffix_enabled and suffix_text:
                name = name.replace(suffix_text, "")

            if f in external_refs_paths:
                # Try to find the workspace name for the external reference
                try:
                    workspace_path = EnvUtils.find_workspace_using_path(f)
                    if workspace_path:
                        workspace_name = os.path.basename(workspace_path)
                        name = f"{name} ({workspace_name})"
                    else:
                        name = f"{name} (External)"
                except Exception:
                    name = f"{name} (External)"
            file_names.append(name)

        self.logger.debug(f"Updating table with {len(file_names)} files.")
        self.update_table(file_names, file_list)

    @block_table_selection_method
    def update_table(self, file_names, file_list):
        t = self.ui.tbl000
        sorting_enabled = t.isSortingEnabled()
        t.setSortingEnabled(False)
        try:
            existing = {
                t.item(row, 0).text(): row
                for row in range(t.rowCount())
                if t.item(row, 0)  # Files column is at index 0
            }

            to_remove = [
                row for name, row in existing.items() if name not in file_names
            ]
            self.logger.debug(f"Rows to remove: {to_remove}")
            for row in reversed(sorted(to_remove)):
                t.removeRow(row)

            for idx, (scene_name, file_path) in enumerate(zip(file_names, file_list)):
                self.logger.debug(f"Inserting row for: {scene_name} ({file_path})")
                row = existing.get(scene_name)
                if row is None:
                    row = t.rowCount()
                    t.insertRow(row)

                item = t.item(row, 0)  # Files column is at index 0
                if not item:
                    # Get the full filename without stripping for rename functionality
                    full_filename = os.path.basename(file_path)
                    item = self.sb.QtWidgets.QTableWidgetItem(scene_name)
                    item.setFlags(item.flags() | self.sb.QtCore.Qt.ItemIsEditable)
                    t.setItem(row, 0, item)  # Files column is at index 0

                    # Store both the full file path and the full filename for rename functionality
                    item.setData(
                        self.sb.QtCore.Qt.UserRole, file_path
                    )  # Full file path
                    item.setData(
                        self.sb.QtCore.Qt.UserRole + 1, full_filename
                    )  # Full filename for rename
                    item.setData(
                        self.sb.QtCore.Qt.UserRole + 2, scene_name
                    )  # Display name

                item.setText(scene_name)
                # Update data attributes
                item.setData(self.sb.QtCore.Qt.UserRole, file_path)
                item.setData(
                    self.sb.QtCore.Qt.UserRole + 1, os.path.basename(file_path)
                )
                item.setData(self.sb.QtCore.Qt.UserRole + 2, scene_name)

                self._format_table_item(item, file_path)

                # Column 1: Notes (Metadata)
                item_notes = t.item(row, 1)
                if not item_notes:
                    item_notes = self.sb.QtWidgets.QTableWidgetItem()
                    item_notes.setFlags(
                        item_notes.flags() | self.sb.QtCore.Qt.ItemIsEditable
                    )
                    t.setItem(row, 1, item_notes)

                # Store file path in notes item too for easy access during edit
                item_notes.setData(self.sb.QtCore.Qt.UserRole, file_path)

                try:  # Fetch metadata (Comments)
                    ptk.Metadata.enable_sidecar = True
                    metadata = ptk.Metadata.get(file_path, "Comments")
                    comments = metadata.get("Comments") or ""
                    if item_notes.text() != comments:
                        item_notes.setText(comments)
                except Exception:
                    pass

            # Apply table formatting
            t.apply_formatting()
        finally:
            t.setSortingEnabled(sorting_enabled)

    def open_scene(self, file_path: str, set_workspace: bool = True):
        """Open a scene file, optionally setting the workspace to match the file.

        Parameters:
            file_path (str): Path to the scene file to open
            set_workspace (bool): If True, sets the Maya workspace to the workspace
                                containing the opened file. Default is True.
        """
        self.logger.debug(f"Opening scene: {file_path}")

        if not os.path.exists(file_path):
            self.slot.logger.error(f"Scene file not found: {file_path}")
            self.sb.message_box(f"Scene file not found:<br>{file_path}")
            return False

        try:
            pm.openFile(file_path, force=True)
            self.logger.info(f"Opened scene: {file_path}")
        except Exception as e:
            self.logger.error(f"Failed to open scene: {e}")
            self.sb.message_box(
                f"Failed to open scene:<br>{file_path}<br><br>Error:<br>{e}"
            )
            return False

        # Set workspace based on the opened file's location
        if set_workspace:
            try:
                new_workspace = EnvUtils.find_workspace_using_path(file_path)
                if new_workspace:
                    current_workspace = pm.workspace(q=True, rd=True)
                    if os.path.normcase(
                        os.path.normpath(current_workspace)
                    ) != os.path.normcase(os.path.normpath(new_workspace)):
                        pm.workspace(new_workspace, openWorkspace=True)
                        self.logger.info(f"Set workspace to: {new_workspace}")
                    else:
                        self.logger.debug("Workspace already correct")
                else:
                    self.logger.warning(f"No workspace found for: {file_path}")
            except Exception as e:
                self.logger.error(f"Failed to set workspace: {e}")

        return True

    @block_table_selection_method
    def unreference_all(self):
        self.logger.debug("Unreferencing all references.")
        self.remove_references()
        self.refresh_file_list()
        # refresh_file_list now properly syncs selection after signals are unblocked

    @block_table_selection_method
    def unlink_all(self):
        self.logger.debug("Unlink all operation triggered.")
        if (
            self.sb.message_box(
                "<b>Warning:</b> The unlink operation is not undoable.<br>Do you want to proceed?",
                "Yes",
                "No",
            )
            != "Yes"
        ):
            self.sb.message_box("<b>Unlink operation cancelled.</b>")
            self.logger.debug("Unlink operation cancelled by user.")
            return

        self.import_references(remove_namespace=True)
        self.refresh_file_list()
        self.logger.info("Unlinked all references and refreshed file list.")
        # refresh_file_list now properly syncs selection after signals are unblocked

    @block_table_selection_method
    def unlink_references(self, namespaces):
        """Unlink specific references."""
        if not namespaces:
            return

        count = len(namespaces)
        msg = f"Unlink {count} reference(s)?"
        if self.sb.message_box(msg, "Yes", "No") != "Yes":
            return

        self.import_references(namespaces=namespaces, remove_namespace=True)
        self.refresh_file_list()
        self.logger.info(f"Unlinked {count} references.")

    @block_table_selection_method
    def convert_to_assembly(self):
        self.logger.debug("Convert to assembly operation triggered.")
        user_choice = self.sb.message_box(
            "<b>Warning:</b> The convert to assembly operation is not undoable.<br>Do you want to proceed?",
            "Yes",
            "No",
        )
        if user_choice == "Yes":
            self.logger.info("Converting references to assemblies.")
            AssemblyManager.convert_references_to_assemblies()
        else:
            self.sb.message_box("<b>Convert to assembly operation cancelled.</b>")
            self.logger.debug("Convert to assembly operation cancelled by user.")

    def _format_name(self, name, case_style="None", suffix=""):
        """Format a filename with case style and suffix."""
        # Strip 'Case: ' prefix if present
        if case_style and case_style.startswith("Case: "):
            case_style = case_style[6:]  # Remove 'Case: ' prefix

        if case_style and case_style != "None":
            try:
                name = ptk.StrUtils.set_case(name, case_style)
            except Exception as e:
                self.logger.warning(f"Failed to set case style {case_style}: {e}")

        if suffix:
            name += suffix

        return name

    def save_scene(self):
        """Save the current scene to the workspace, prompting for a name."""
        # Get settings from UI via slot
        header_menu = self.slot.ui.header.menu
        use_folder = header_menu.chk_enable_folder.isChecked()
        case_style = header_menu.cmb_case_style.currentText()
        suffix = header_menu.txt_suffix.text()

        # Pre-populate with current scene name if available, with case formatting applied
        default_name = ""
        current_scene = pm.sceneName()
        if current_scene:
            base_name = os.path.basename(current_scene).split(".")[0]
            # Apply case formatting to the default name (without suffix)
            default_name = self._format_name(base_name, case_style, suffix="")

        name = self.sb.input_dialog("Save Scene", "Enter name for scene:", default_name)
        if not name:
            return

        formatted_name = self._format_name(name, case_style, suffix)

        workspace = self.current_working_dir
        if not workspace or not os.path.isdir(workspace):
            self.sb.message_box("Current workspace directory is invalid.")
            return

        target_dir = workspace

        # Check for custom subfolder structure
        subfolder_structure = getattr(header_menu, "txt_subfolder_structure", None)
        subfolder_structure_text = (
            subfolder_structure.text().strip() if subfolder_structure else ""
        )

        if subfolder_structure_text:
            # Use custom structure
            base_name_formatted = self._format_name(name, case_style, suffix="")
            workspace_name = os.path.basename(workspace)
            resolved_path = ptk.StrUtils.replace_placeholders(
                subfolder_structure_text,
                name=base_name_formatted,
                workspace=workspace_name,
                suffix=suffix,
            )
            target_dir = os.path.join(workspace, resolved_path)

            if not os.path.exists(target_dir):
                try:
                    os.makedirs(target_dir)
                except OSError as e:
                    self.sb.message_box(f"Failed to create directory: {e}")
                    return

        elif use_folder:
            # Folder name matches the base name (without suffix)
            folder_name = self._format_name(name, case_style, suffix="")
            target_dir = os.path.join(workspace, folder_name)
            if not os.path.exists(target_dir):
                try:
                    os.makedirs(target_dir)
                except OSError as e:
                    self.sb.message_box(f"Failed to create directory: {e}")
                    return

        new_path = os.path.join(target_dir, formatted_name + ".ma")

        if os.path.exists(new_path):
            if (
                self.sb.message_box(
                    f"File exists:<br>{new_path}<br>Overwrite?", "Yes", "No"
                )
                != "Yes"
            ):
                return

        try:
            pm.saveAs(new_path, type="mayaAscii")
            self.logger.info(f"Saved scene to: {new_path}")
            self.refresh_file_list()
        except Exception as e:
            self.sb.message_box(f"Failed to save scene: {e}")

    def rename_scene(self):
        """Rename the selected scene file."""
        t = self.ui.tbl000
        selected_items = t.selectedItems()

        # Fallback to current item if nothing is selected (context menu case)
        if not selected_items:
            current_item = t.currentItem()
            if current_item:
                selected_items = [current_item]
            else:
                self.sb.message_box("No scene selected.")
                return

        # Assuming single selection for rename
        item = selected_items[0]
        if item.column() != 0:
            # Find the item in column 0 for this row
            item = t.item(item.row(), 0)

        old_path = item.data(self.sb.QtCore.Qt.UserRole)
        if not old_path or not os.path.exists(old_path):
            self.sb.message_box("File not found.")
            return

        old_name = os.path.basename(old_path)
        old_base, ext = os.path.splitext(old_name)

        new_base = self.sb.input_dialog("Rename Scene", "Enter new name:", old_base)
        if not new_base or new_base == old_base:
            return

        # Get settings
        header_menu = self.slot.ui.header.menu
        use_folder = header_menu.chk_enable_folder.isChecked()
        case_style = header_menu.cmb_case_style.currentText()
        suffix = header_menu.txt_suffix.text()

        formatted_name = self._format_name(new_base, case_style, suffix)
        new_filename = formatted_name + ext

        old_dir = os.path.dirname(old_path)
        new_path = os.path.join(old_dir, new_filename)

        if os.path.exists(new_path):
            self.sb.message_box(f"Target file exists: {new_path}")
            return

        try:
            os.rename(old_path, new_path)
            self.logger.info(f"Renamed {old_path} to {new_path}")

            # Handle folder rename
            if use_folder:
                parent_dir_name = os.path.basename(old_dir)
                # Check if parent folder name is contained in the old file base name
                # This handles cases where file has a suffix (e.g., folder "MyScene", file "MyScene_v01")
                if old_base.startswith(parent_dir_name):
                    # Rename folder to new base (without suffix)
                    new_folder_name = self._format_name(new_base, case_style, suffix="")
                    new_folder_path = os.path.join(
                        os.path.dirname(old_dir), new_folder_name
                    )

                    if not os.path.exists(new_folder_path):
                        os.rename(old_dir, new_folder_path)
                        self.logger.info(
                            f"Renamed folder {old_dir} to {new_folder_path}"
                        )
                    else:
                        self.logger.warning(
                            f"Cannot rename folder, target exists: {new_folder_path}"
                        )
                else:
                    self.logger.debug(
                        f"Folder '{parent_dir_name}' doesn't match file base '{old_base}', skipping folder rename"
                    )

            self.refresh_file_list()

        except Exception as e:
            self.sb.message_box(f"Rename failed: {e}")

    def delete_scene(self):
        """Delete the selected scene file."""
        t = self.ui.tbl000
        selected_items = t.selectedItems()

        # Fallback to current item if nothing is selected (context menu case)
        if not selected_items:
            current_item = t.currentItem()
            if current_item:
                selected_items = [current_item]
            else:
                self.sb.message_box("No scene selected.")
                return

        # Get all selected files
        rows = set(item.row() for item in selected_items)
        files_to_delete = []
        for row in rows:
            item = t.item(row, 0)
            path = item.data(self.sb.QtCore.Qt.UserRole)
            if path and os.path.exists(path):
                files_to_delete.append(path)

        if not files_to_delete:
            return

        if (
            self.sb.message_box(f"Delete {len(files_to_delete)} file(s)?", "Yes", "No")
            != "Yes"
        ):
            return

        header_menu = self.slot.ui.header.menu
        use_folder = header_menu.chk_enable_folder.isChecked()

        import shutil

        for path in files_to_delete:
            try:
                os.remove(path)
                self.logger.info(f"Deleted file: {path}")

                if use_folder:
                    parent_dir = os.path.dirname(path)
                    parent_name = os.path.basename(parent_dir)
                    file_base = os.path.splitext(os.path.basename(path))[0]

                    # Check if parent folder name is contained in the file base name
                    # This handles cases where file has a suffix (e.g., folder "MyScene", file "MyScene_v01")
                    if file_base.startswith(parent_name):
                        shutil.rmtree(parent_dir)
                        self.logger.info(f"Deleted folder: {parent_dir}")
                    else:
                        self.logger.debug(
                            f"Folder '{parent_name}' doesn't match file base '{file_base}', skipping folder delete"
                        )

            except Exception as e:
                self.logger.error(f"Delete failed for {path}: {e}")

        self.refresh_file_list()


class ReferenceManagerSlots(ptk.HelpMixin, ptk.LoggingMixin):
    """UI event handlers and widget initialization for the Reference Manager interface.

    This class handles pure UI interactions including:
    - Widget initialization and setup (tables, buttons, checkboxes)
    - Event slot connections and signal handling
    - User input processing (text changes, button clicks, selections)
    - Menu and context menu setup
    - UI state synchronization during initialization

    Widget Responsibilities:
    - txt000: Root directory input with browse, workspace options, and pin values for directory history
    - txt001: File filter input with enable/strip options
    - cmb000: Workspace selection dropdown
    - tbl000: File table with reference selection and context menu
    - Various buttons and checkboxes for reference operations

    New Features:
    - Pin Values: txt000 now supports pinning frequently used directories for quick access
      Users can pin current directory and select from previously pinned directories
      Directories are persisted using the key "reference_manager_directories"

    The slots class maintains no business logic - it purely routes UI events
    to the appropriate controller methods.
    """

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.reference_manager

        # Flag to prevent checkbox events during initialization
        self._initializing = True

        self.controller = ReferenceManagerController(self)
        self.ui.txt000.setText(self.controller.current_working_dir)

        self.script_job = pm.scriptJob(
            event=["SceneOpened", self.controller.refresh_file_list]
        )

        # Initialization complete
        self._initializing = False

        # Initial sync of selection to existing references
        # Use a timer to ensure UI is fully initialized first
        self.sb.defer_with_timer(
            lambda: self.controller.sync_selection_to_references(), ms=100
        )

        self.logger.debug("ReferenceManagerSlots initialized and scriptJob created.")

    def __del__(self):
        if hasattr(self, "script_job") and pm.scriptJob(exists=self.script_job):
            pm.scriptJob(kill=self.script_job, force=True)
            self.logger.debug("ScriptJob killed in __del__.")

    def header_init(self, widget):
        """Initialize the header for the reference manager."""
        widget.menu.setTitle("Global Settings:")
        widget.menu.add(
            "QPushButton",
            setText="Refresh",
            setObjectName="btn_refresh",
            setToolTip="Refresh the file list.",
        )
        widget.menu.add("Separator", setTitle="Naming:")
        widget.menu.add(
            "QPushButton",
            setText="Save Current Scene",
            setObjectName="btn_save_scene",
            setToolTip="Save the current scene to the workspace.",
        )
        widget.menu.add(
            "QComboBox",
            setObjectName="cmb_case_style",
            setToolTip="Enforce a specific case style for new filenames.",
            addItems=[
                "Case: None",
                "Case: lower",
                "Case: upper",
                "Case: title",
                "Case: camel",
                "Case: pascal",
            ],
        )
        widget.menu.add(
            "QLineEdit",
            setObjectName="txt_suffix",
            setPlaceholderText="Suffix",
            setToolTip="Optional suffix to append to filenames (excluded from case formatting).",
        )
        widget.menu.add(
            "QCheckBox",
            setText="Hide Suffix",
            setObjectName="chk_hide_suffix",
            setChecked=False,
            setToolTip="Hide the suffix from the file list display.",
        )
        widget.menu.add(
            "QCheckBox",
            setText="Hide Extension",
            setObjectName="chk_hide_extension",
            setChecked=False,
            setToolTip="Hide the file extension from the file list display.",
        )
        widget.menu.add(
            "QCheckBox",
            setText="Enable Folder Structure",
            setObjectName="chk_enable_folder",
            setChecked=False,
            setToolTip="If checked, new files will be managed within a folder of the same name.",
        )
        widget.menu.add(
            "QLineEdit",
            setObjectName="txt_subfolder_structure",
            setPlaceholderText="Subfolder Structure (e.g. {name}/versions)",
            setToolTip="Optional nested folder structure relative to workspace.\nSupports placeholders: {name}, {workspace}, {suffix}\nNote: {name} excludes the suffix if one is defined, otherwise the full name minus extension is used.",
        )
        widget.menu.add(
            "QCheckBox",
            setText="Filter by Folder Structure",
            setObjectName="chk_filter_folder_structure",
            setChecked=False,
            setToolTip="If checked, only show files that match the folder structure pattern.",
        )
        widget.menu.add(
            "QCheckBox",
            setText="Hide Binary Files (.mb)",
            setObjectName="chk_hide_binary",
            setChecked=False,
            setToolTip="If checked, hide Maya Binary (.mb) files.",
        )
        widget.menu.add(
            "QCheckBox",
            setText="Filter by Suffix",
            setObjectName="chk_filter_suffix",
            setChecked=False,
            setToolTip="If checked, only show files that end with the specified suffix.",
        )
        widget.menu.add("Separator", setTitle="Operations:")
        widget.menu.add(
            "QPushButton",
            setText="Convert to Assembly",
            setObjectName="btn_convert_assembly",
            setToolTip="Convert all references to assemblies.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Unlink and Import All",
            setObjectName="btn_unlink_import_all",
            setToolTip="Unlink and import all references.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Un-Reference All",
            setObjectName="btn_unreference_all",
            setToolTip="Remove all references from the scene.",
        )

    def tbl000_init(self, widget):
        if not widget.is_initialized:
            widget.setColumnCount(2)
            widget.setHorizontalHeaderLabels(["FILES:", "NOTES:"])
            # Use NoEditTriggers and handle editing manually to prevent conflicts with double-click
            widget.setEditTriggers(self.sb.QtWidgets.QAbstractItemView.NoEditTriggers)
            widget.setSelectionBehavior(self.sb.QtWidgets.QAbstractItemView.SelectRows)
            widget.setSelectionMode(self.sb.QtWidgets.QAbstractItemView.MultiSelection)
            widget.setSortingEnabled(True)
            widget.verticalHeader().setVisible(False)

            # Make the Notes column (index 1) non-selecting so clicking it doesn't trigger reference logic
            widget.set_column_selectable(1, False)
            widget.setAlternatingRowColors(False)
            widget.setWordWrap(False)
            widget.set_stretch_column(0)

            # Connect double-click FIRST to ensure it gets priority
            widget.itemDoubleClicked.connect(self.tbl000_item_double_clicked)

            # Then connect other signals
            widget.itemSelectionChanged.connect(self.controller.handle_item_selection)

            # Add context menu
            widget.menu.add(
                "QPushButton",
                setText="Open",
                setObjectName="btn_open_scene",
                setToolTip="Open the selected scene file",
            )

            widget.menu.add(
                "QPushButton",
                setText="Rename",
                setObjectName="btn_rename_scene",
                setToolTip="Rename the selected scene file.",
            )

            widget.menu.add(
                "QPushButton",
                setText="Delete",
                setObjectName="btn_delete_scene",
                setToolTip="Delete the selected scene file.",
            )

            widget.menu.add(
                "QPushButton",
                setText="Unlink and Import",
                setObjectName="btn_unlink_import",
                setToolTip="Unlink and import the selected reference(s).",
            )

            # Connect context menu actions
            widget.register_menu_action("btn_open_scene", self.btn_open_scene)
            widget.register_menu_action(
                "btn_rename_scene", self.controller.rename_scene
            )
            widget.register_menu_action(
                "btn_delete_scene", self.controller.delete_scene
            )
            widget.register_menu_action("btn_unlink_import", self.btn_unlink_import)

            # Connect item delegate signals for rename functionality
            widget.itemChanged.connect(self.tbl000_item_changed)
            widget.itemDelegate().closeEditor.connect(self.tbl000_editor_closed)

            self.logger.debug(
                "tbl000 table widget initialized with context menu and rename functionality."
            )

    def tbl000_item_double_clicked(self, item):
        """Handle double-click to prepare item for editing."""
        self.logger.debug(
            f"Double-click detected on item: {item.text() if item else 'None'}"
        )

        if item and item.column() == 0:  # Only handle the filename column (at index 0)
            self.logger.debug(f"Starting edit for item: {item.text()}")

            # Prepare the item for editing (show full filename)
            self.controller.prepare_item_for_edit(item)

            # Manually start editing since we disabled automatic edit triggers
            table = self.ui.tbl000
            table.editItem(item)

        elif item and item.column() == 1:  # Notes column
            self.logger.debug(f"Starting edit for notes: {item.text()}")
            table = self.ui.tbl000
            table.editItem(item)

    def tbl000_item_changed(self, item):
        """Handle item changes when user renames a file."""
        if item.column() == 0:  # Only handle the filename column (at index 0)
            # Only process if this item is being edited
            if not self.controller.is_item_being_edited(item):
                return

            new_name = item.text().strip()
            if not new_name:
                # If empty, restore the original display name
                self.controller.restore_item_display(item)
                return

            # For now, just update the display name
            # In a real implementation, you might want to rename the actual file
            self.logger.info(f"File renamed to: {new_name}")

            # Update the stored display name
            item.setData(self.sb.QtCore.Qt.UserRole + 2, new_name)

        elif item.column() == 1:  # Notes column
            file_path = item.data(self.sb.QtCore.Qt.UserRole)
            if not file_path:
                return

            new_comments = item.text()
            try:
                ptk.Metadata.enable_sidecar = True
                ptk.Metadata.set(file_path, Comments=new_comments)
                self.logger.info(f"Updated comments for {file_path}")
            except Exception as e:
                self.logger.error(f"Failed to set metadata for {file_path}: {e}")

    def tbl000_editor_closed(self, editor, hint):
        """Handle when the rename editor is closed."""
        # Get the item that was being edited
        current_item = self.ui.tbl000.currentItem()
        if current_item and current_item.column() == 0:  # Files column is at index 0
            # Restore the display name (either original or newly edited)
            self.controller.restore_item_display(current_item)

    def _get_selected_reference_namespaces(self):
        """Get namespaces of selected items that are current references."""
        t = self.ui.tbl000
        selected_items = [
            t.item(idx.row(), 0)
            for idx in t.selectedIndexes()
            if idx.column() == 0 and t.item(idx.row(), 0)
        ]

        # Map paths to namespaces for current references
        path_to_namespaces = {}
        for ref in self.controller.current_references:
            try:
                path = os.path.normpath(ref.path)
                if path not in path_to_namespaces:
                    path_to_namespaces[path] = []
                path_to_namespaces[path].append(ref.namespace)
            except Exception:
                continue

        selected_namespaces = []
        for item in selected_items:
            file_path = item.data(self.sb.QtCore.Qt.UserRole)
            if file_path:
                norm_path = os.path.normpath(file_path)
                if norm_path in path_to_namespaces:
                    selected_namespaces.extend(path_to_namespaces[norm_path])

        return selected_namespaces

    def btn_unlink_import(self):
        """Unlink and import the selected references."""
        namespaces = self._get_selected_reference_namespaces()
        if not namespaces:
            self.sb.message_box("No active references selected.")
            return
        self.controller.unlink_references(namespaces)

    def btn_open_scene(self):
        """Open the selected scene file."""
        t = self.ui.tbl000

        # Get currently selected rows
        selected_rows = set(idx.row() for idx in t.selectedIndexes())

        # If no rows are selected, try to get the current item (right-click context)
        if not selected_rows:
            current_item = t.currentItem()
            if current_item:
                selected_rows = {current_item.row()}
                self.logger.debug(
                    f"b008: Using current item at row {current_item.row()}"
                )
            else:
                self.logger.debug("b008: No rows selected and no current item")
                self.sb.message_box("No scene file selected.")
                return

        if len(selected_rows) > 1:
            self.logger.debug(f"b008: Multiple rows selected ({len(selected_rows)})")
            self.sb.message_box("Please select only one scene file to open.")
            return

        # Get the item from the selected row
        row = list(selected_rows)[0]
        item = t.item(row, 0)  # Get item from Files column

        if not item:
            self.logger.warning(f"b008: No item found at row {row}")
            self.sb.message_box("Could not retrieve scene file information.")
            return

        # Get the file path from the item
        file_path = item.data(self.sb.QtCore.Qt.UserRole)

        if not file_path:
            self.logger.warning(f"b008: No file path data for item {item.text()}")
            self.sb.message_box("Scene file path not found.")
            return

        self.logger.debug(f"b008: Opening scene file: {file_path}")
        self.controller.open_scene(file_path)

    def txt000_init(self, widget):
        """Initialize the text input for the current working directory with pin values."""
        self.logger.debug(
            f"txt000_init called, is_initialized: {getattr(widget, 'is_initialized', False)}"
        )
        if not widget.is_initialized:
            widget.option_box.pin(
                settings_key="reference_manager_directories",
                single_click_restore=True,
            )
            widget.option_box.menu.add(
                "QPushButton",
                setText="Browse",
                setObjectName="b000",
                setToolTip="Open a file browser to select a root directory.",
            )
            widget.option_box.menu.add(
                "QPushButton",
                setText="Open Directory",
                setObjectName="b006",
                setToolTip="Open the current directory in the file explorer.",
            )
            widget.option_box.menu.add(
                "QPushButton",
                setText="Set To Current Workspace",
                setObjectName="b001",
                setToolTip="Set the root folder to that of the current workspace.",
            )
            widget.option_box.menu.add(
                "QCheckBox",
                setText="Recursive Search",
                setObjectName="chk000",
                setChecked=True,
                setToolTip="Also search sub-folders.",
            )
            widget.option_box.menu.add(
                "QCheckBox",
                setText="Ignore Empty Workspaces",
                setObjectName="chk003",
                setChecked=True,
                setToolTip="Skip workspaces that contain no scene files.",
            )
            widget.textChanged.connect(
                lambda text: self.sb.defer_with_timer(
                    lambda: self.controller.update_current_dir(text), ms=500
                )
            )
            self.logger.debug(
                "txt000 text input initialized with pin values for directory history."
            )

        self.controller.update_current_dir()

    def txt001_init(self, widget):
        """Initialize the filter text input with filtering options."""
        if not widget.is_initialized:
            widget.option_box.menu.add(
                "QCheckBox",
                setText="Enable Filter",
                setObjectName="chk004",
                setChecked=True,
                setToolTip="Filter the file list by the text entered above.",
            )
            widget.option_box.menu.add(
                "QCheckBox",
                setText="Ignore Case",
                setObjectName="chk_ignore_case",
                setChecked=True,
                setToolTip="Ignore case when filtering.",
            )

            self.logger.debug(
                "txt001 filter text input initialized with filter options."
            )

    def txt001(self, text):
        """Handle the filter text input."""
        self.logger.debug(f"txt001 filter text changed: {text}")
        self.controller._filter_text = text.strip()
        self.controller.refresh_file_list(invalidate=True)

    def cmb000_init(self, widget):
        # Use the controller's current_working_dir for consistency
        root_dir = self.controller.current_working_dir

        self.logger.debug(f"cmb000_init called for root_dir: {root_dir}")

        if not root_dir or not os.path.isdir(root_dir):
            self.logger.debug(f"Invalid root directory for cmb000_init: {root_dir}")
            widget.clear()
            return

        self.logger.debug(
            f"cmb000_init searching workspaces in: {root_dir}, recursive: {self.controller.recursive_search}"
        )

        # Use the centralized workspace finding method
        workspaces = self.controller.find_available_workspaces(root_dir)

        # Block signals while we update the combobox to prevent unwanted events
        widget.blockSignals(True)
        try:
            widget.clear()
            widget.add(workspaces)

            if workspaces:
                # Set the current index to 0 and ensure it's properly selected
                widget.setCurrentIndex(0)
                self.logger.debug(
                    f"cmb000_init: Set current index to 0, count={widget.count()}"
                )
            else:
                self.logger.warning(
                    f"No workspaces found in {root_dir} (recursive: {self.controller.recursive_search})"
                )
        finally:
            widget.blockSignals(False)

        self.logger.debug(
            f"cmb000 combo box initialized with {len(workspaces)} workspaces, current index: {widget.currentIndex()}"
        )

    def cmb000(self, index, widget):
        """Handle workspace selection changes."""
        # Handle the case where index is -1 (no selection) which can happen during clearing/repopulating
        if index == -1:
            self.logger.debug(
                f"cmb000 changed to index {index} (no selection) - ignoring"
            )
            return

        # Skip processing during directory updates to prevent cascading triggers
        if getattr(self.controller, "_updating_directory", False):
            self.logger.debug("cmb000 called during directory update - ignoring")
            return

        path = widget.itemData(index)
        self.logger.debug(f"cmb000 changed to index {index}, path: {path}")

        # Add debugging to track what happens next
        current_index_before = widget.currentIndex()
        self.logger.debug(
            f"cmb000: Current index before operations: {current_index_before}"
        )

        if path and os.path.isdir(path):
            # Update the current working dir to the selected workspace
            old_working_dir = self.controller.current_working_dir
            self.logger.debug(
                f"cmb000: Changing current_working_dir from {old_working_dir} to {path}"
            )
            self.controller.current_working_dir = path

            current_index_after_set = widget.currentIndex()
            self.logger.debug(
                f"cmb000: Current index after setting working dir: {current_index_after_set}"
            )

            # Refresh the file list for this workspace
            self.logger.debug(
                f"cmb000: About to refresh file list for directory: {path}"
            )

            # Check if workspace files cache has this directory
            workspace_files = self.controller.workspace_files.get(path, [])
            self.logger.debug(
                f"cmb000: Found {len(workspace_files)} cached files for workspace"
            )

            self.controller.refresh_file_list(invalidate=False)

            current_index_after_refresh = widget.currentIndex()
            self.logger.debug(
                f"cmb000: Current index after refresh: {current_index_after_refresh}"
            )

            # Verify table was updated
            table_row_count = self.controller.ui.tbl000.rowCount()
            self.logger.debug(f"cmb000: Table now has {table_row_count} rows")
        else:
            self.logger.warning(f"Invalid workspace path selected: {path}")

    def chk000(self, checked):
        """Handle the recursive search toggle."""
        # Skip processing during initialization or directory updates to prevent unwanted triggers
        if getattr(self, "_initializing", False):
            self.logger.debug("chk000 called during initialization - ignoring")
            return

        if getattr(self.controller, "_updating_directory", False):
            self.logger.debug("chk000 called during directory update - ignoring")
            return

        self.logger.debug(
            f"chk000 recursive search toggled: {checked} (type: {type(checked)})"
        )

        # Convert Qt checkbox state to boolean
        # Qt.Unchecked = 0, Qt.PartiallyChecked = 1, Qt.Checked = 2
        if isinstance(checked, int):
            checked_bool = checked == 2  # Qt.Checked
        else:
            checked_bool = bool(checked)

        old_recursive = self.controller.recursive_search

        self.logger.debug(
            f"chk000 old_recursive: {old_recursive}, new_recursive: {checked_bool}"
        )

        # Don't process if the value hasn't actually changed (avoid UI triggering loops)
        if old_recursive == checked_bool:
            self.logger.debug("chk000 recursive search unchanged, no refresh needed")
            return

        self.controller.recursive_search = checked_bool

        self.logger.debug("chk000 recursive search changed, updating workspace combo")
        # Use the centralized workspace combo update method
        self.controller._update_workspace_combo()

    def chk003(self, checked):
        """Handle the ignore empty workspaces toggle."""
        # Skip processing during initialization or directory updates to prevent unwanted triggers
        if getattr(self, "_initializing", False):
            self.logger.debug("chk003 called during initialization - ignoring")
            return

        if getattr(self.controller, "_updating_directory", False):
            self.logger.debug("chk003 called during directory update - ignoring")
            return

        self.logger.debug(
            f"chk003 ignore empty workspaces toggled: {checked} (type: {type(checked)})"
        )

        # Convert Qt checkbox state to boolean
        # Qt.Unchecked = 0, Qt.PartiallyChecked = 1, Qt.Checked = 2
        if isinstance(checked, int):
            checked_bool = checked == 2  # Qt.Checked
        else:
            checked_bool = bool(checked)

        old_ignore_empty = self.controller.ignore_empty_workspaces

        self.logger.debug(
            f"chk003 old_ignore_empty: {old_ignore_empty}, new_ignore_empty: {checked_bool}"
        )

        # Don't process if the value hasn't actually changed (avoid UI triggering loops)
        if old_ignore_empty == checked_bool:
            self.logger.debug(
                "chk003 ignore empty workspaces unchanged, no refresh needed"
            )
            return

        self.controller.ignore_empty_workspaces = checked_bool

        self.logger.debug(
            "chk003 ignore empty workspaces changed, updating workspace combo"
        )
        # Use the centralized workspace combo update method
        self.controller._update_workspace_combo()

    def chk004(self, checked):
        """Handle the filter enable checkbox."""
        self.logger.debug(f"chk004 filter enable changed: {checked}")
        # Refresh the file list when filter enable state changes
        self.controller.refresh_file_list(invalidate=False)

    def chk_ignore_case(self, checked):
        """Handle the ignore case checkbox."""
        self.logger.debug(f"chk_ignore_case changed: {checked}")
        self.controller.refresh_file_list(invalidate=False)

    def chk_hide_binary(self, checked):
        """Handle the hide binary checkbox."""
        self.logger.debug(f"chk_hide_binary changed: {checked}")
        self.controller.refresh_file_list(invalidate=False)

    def chk_filter_suffix(self, checked):
        """Handle the filter by suffix checkbox."""
        self.logger.debug(f"chk_filter_suffix changed: {checked}")
        self.controller.refresh_file_list(invalidate=False)

    def chk_hide_suffix(self, checked):
        """Handle the hide suffix checkbox."""
        self.logger.debug(f"chk_hide_suffix changed: {checked}")
        self.controller.refresh_file_list(invalidate=False)

    def chk_hide_extension(self, checked):
        """Handle the hide extension checkbox."""
        self.logger.debug(f"chk_hide_extension changed: {checked}")
        self.controller.refresh_file_list(invalidate=False)

    def txt_suffix(self, text):
        """Handle suffix text changes."""
        # Refresh if hide suffix or filter by suffix is enabled
        header_menu = self.ui.header.menu
        hide_suffix = getattr(header_menu, "chk_hide_suffix", None)
        filter_suffix = getattr(header_menu, "chk_filter_suffix", None)

        should_refresh = False
        if hide_suffix and hide_suffix.isChecked():
            should_refresh = True
        if filter_suffix and filter_suffix.isChecked():
            should_refresh = True

        if should_refresh:
            self.controller.refresh_file_list(invalidate=False)

    def chk_filter_folder_structure(self, checked):
        """Handle the filter by folder structure checkbox."""
        self.logger.debug(f"chk_filter_folder_structure changed: {checked}")
        self.controller.refresh_file_list(invalidate=False)

    def txt_subfolder_structure(self, text):
        """Handle subfolder structure text changes."""
        # Refresh if filter by folder structure is enabled
        header_menu = self.ui.header.menu
        filter_structure = getattr(header_menu, "chk_filter_folder_structure", None)

        if filter_structure and filter_structure.isChecked():
            self.controller.refresh_file_list(invalidate=False)

    def b000(self):
        """Browse for a root directory."""
        start_dir = self.ui.txt000.text()
        if not os.path.isdir(start_dir):
            start_dir = self.controller.current_workspace

        selected_directory = self.sb.dir_dialog(
            "Select a root directory", start_dir=start_dir
        )
        self.logger.debug(f"b000 browse selected directory: {selected_directory}")
        if selected_directory:
            self.ui.txt000.setText(selected_directory)

    def b006(self):
        """Open the current directory in the file explorer."""
        current_dir = self.ui.txt000.text()
        ptk.FileUtils.open_explorer(current_dir, logger=self.logger)

    def b001(self):
        """Set dir to current workspace."""
        self.logger.debug("b001 set to current workspace clicked.")
        self.ui.txt000.setText(self.controller.current_workspace)

    def btn_open_scene(self):
        """Open the selected scene file."""
        t = self.ui.tbl000

        # Get currently selected rows
        selected_rows = set(idx.row() for idx in t.selectedIndexes())

        # If no rows are selected, try to get the current item (right-click context)
        if not selected_rows:
            current_item = t.currentItem()
            if current_item:
                selected_rows.add(current_item.row())

        if not selected_rows:
            self.sb.message_box("No scene selected.")
            return

        # Open each selected file
        for row in selected_rows:
            item = t.item(row, 0)
            file_path = item.data(self.sb.QtCore.Qt.UserRole)
            if file_path:
                self.controller.open_scene(file_path)

    def btn_unlink_import(self):
        """Unlink and import the selected reference(s)."""
        namespaces = self._get_selected_reference_namespaces()
        if not namespaces:
            self.sb.message_box("No active references selected.")
            return
        self.controller.unlink_references(namespaces)

    def btn_save_scene(self):
        """Save the current scene to the workspace."""
        self.controller.save_scene()

    def btn_refresh(self):
        """Refresh the file list."""
        self.controller.refresh_file_list(invalidate=True)

    def btn_convert_assembly(self):
        """Convert all references to assemblies."""
        self.controller.convert_to_assembly()

    def btn_unlink_import_all(self):
        """Unlink and import all references."""
        self.controller.unlink_all()

    def btn_unreference_all(self):
        """Remove all references from the scene."""
        self.controller.unreference_all()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("reference_manager", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
