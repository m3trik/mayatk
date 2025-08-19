# !/usr/bin/python
# coding=utf-8
import os
import re
import glob
from functools import partial, wraps
from typing import Optional

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# From this package:
from mayatk.env_utils import EnvUtils


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
                print(f"File does not exist: {file_path}")
                pm.displayError(f"File does not exist: {file_path}")
                return None

            # Create assembly definition
            assembly_name = f"{namespace}_assembly"
            assembly_node = pm.assembly(name=assembly_name, type="assemblyDefinition")
            print(f"Created assembly definition: {assembly_node}")

            # Create representation
            rep_name = pm.assembly(
                assembly_node, edit=True, createRepresentation="Scene", input=file_path
            )
            representations = pm.assembly(
                assembly_node, query=True, listRepresentations=True
            )
            print(
                f"Created representation for assembly: {assembly_node} from file: {file_path}"
            )
            print(f"Available representations for {assembly_node}: {representations}")
            return representations[0] if representations else None
        except Exception as e:
            print(f"Failed to create assembly definition for {file_path}: {str(e)}")
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
            print(
                f"Set active representation {representation_name} for {assembly_node}"
            )
            return True
        except Exception as e:
            print(f"Failed to set active representation for {assembly_node}: {str(e)}")
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
                    print(
                        f"Successfully created and set active representation for {assembly_name}"
                    )
                    # Optionally remove the original reference after conversion
                    ref.remove()
                else:
                    print(f"Failed to set active representation for {assembly_name}")
            else:
                print(f"Failed to create assembly definition for {file_path}")


class ReferenceManager(ptk.HelpMixin, ptk.LoggingMixin):
    """Manages Maya scene references with support for selectable and reference-only modes.

    Features:
    - Add/remove references with namespace management
    - Import references into the scene
    - Update references from source files
    - Convert references to assemblies
    - Control reference selectability (selectable vs reference-only)

    Reference Modes:
    - Selectable: References can be selected and modified in the viewport
    - Reference-Only: References are visible but cannot be selected (display-only)

    Usage:
    - Hold Ctrl while selecting files to add them as reference-only
    - Use context menu or keyboard shortcuts to toggle reference modes
    - Press 'T' to toggle selectability of selected references
    """

    def __init__(self):
        self._filter_text = ""
        self.prefilter_regex = re.compile(r".+\.\d{4}\.(ma|mb)$")

    @property
    def current_workspace(self):
        return pm.workspace(q=True, rd=True)

    @property
    def current_working_dir(self):
        if not hasattr(self, "_current_working_dir"):
            self._current_working_dir = self.current_workspace
        return self._current_working_dir

    @current_working_dir.setter
    def current_working_dir(self, value):  # Corrected setter name here
        if os.path.isdir(value):
            self._current_working_dir = value
            self.invalidate_workspace_files()

    @property
    def current_references(self):
        """Get the current scene references.
        Returns a list of FileReference objects.
        """
        return pm.system.listReferences()

    @property
    def recursive_search(self):
        if not hasattr(self, "_recursive_search"):
            self._recursive_search = True  # Default value
        return self._recursive_search

    @recursive_search.setter
    def recursive_search(self, value):
        self._recursive_search = value
        self.invalidate_workspace_files()  # Invalidate cache when recursive_search changes

    @property
    def workspace_files(self) -> dict[str, list[str]]:
        """Return the cached workspace file dictionary, rebuilding if needed."""
        if not hasattr(self, "_workspace_files") or self._workspace_files is None:
            self.invalidate_workspace_files()
        return self._workspace_files

    def invalidate_workspace_files(self):
        self.logger.debug(f"Scanning for workspaces under: {self.current_working_dir}")
        self._workspace_files = {}

        workspaces = EnvUtils.find_workspaces(
            self.current_working_dir,
            return_type="dirname|dir",
            ignore_empty=True,
        )

        if not workspaces:
            self.logger.warning("No valid workspaces found.")

        for _, ws_path in workspaces:
            if os.path.isdir(ws_path):
                scenes = EnvUtils.get_workspace_scenes(
                    root_dir=ws_path,
                    full_path=True,
                    recursive=self.recursive_search,
                    omit_autosave=True,
                )
                self.logger.debug(f"Workspace '{ws_path}' has {len(scenes)} scene(s).")
                self._workspace_files[ws_path] = scenes

    def resolve_file_path(self, selected_file: str) -> Optional[str]:
        return next(
            (
                fp
                for files in self.workspace_files.values()
                for fp in files
                if os.path.basename(fp) == selected_file
            ),
            None,
        )

    def _matches_prefilter_regex(self, filename):
        """Check if a file is an auto-save file based on its name."""
        return bool(self.prefilter_regex.match(filename))

    @staticmethod
    def sanitize_namespace(namespace: str) -> str:
        """Sanitize the namespace by replacing or removing illegal characters."""
        return re.sub(r"[^a-zA-Z0-9_]", "_", namespace)

    def set_reference_mode(self, reference, reference_only: bool = True) -> bool:
        """Set a reference to be reference-only (non-selectable) or selectable.

        Parameters:
            reference: The reference object (FileReference or reference node name)
            reference_only (bool): If True, makes reference non-selectable. If False, makes it selectable.

        Returns:
            bool: True if successful, False otherwise.
        """
        try:
            # Get the reference node if we have a FileReference object
            if hasattr(reference, "_refNode"):
                ref_node = reference._refNode
            elif isinstance(reference, str):
                # Assume it's a reference node name
                ref_node = pm.PyNode(reference)
            else:
                ref_node = reference

            # Set the reference display mode
            # In Maya, setting the reference node's "displayLayerMode" affects selectability
            if reference_only:
                # Make reference non-selectable by setting display layer to reference mode
                ref_node.referenceFlag.set(True)
                # Also disable selection for all referenced objects
                referenced_nodes = pm.referenceQuery(ref_node, nodes=True, dagPath=True)
                if referenced_nodes:
                    for node_name in referenced_nodes:
                        try:
                            node = pm.PyNode(node_name)
                            if hasattr(node, "overrideEnabled") and hasattr(
                                node, "overrideDisplayType"
                            ):
                                node.overrideEnabled.set(True)
                                node.overrideDisplayType.set(
                                    2
                                )  # Reference display type
                        except (pm.MayaNodeError, AttributeError):
                            # Skip nodes that don't support override attributes
                            continue
                self.logger.info(f"Set reference {ref_node} to reference-only mode")
            else:
                # Make reference selectable
                ref_node.referenceFlag.set(False)
                # Enable selection for all referenced objects
                referenced_nodes = pm.referenceQuery(ref_node, nodes=True, dagPath=True)
                if referenced_nodes:
                    for node_name in referenced_nodes:
                        try:
                            node = pm.PyNode(node_name)
                            if hasattr(node, "overrideEnabled"):
                                node.overrideEnabled.set(False)
                        except (pm.MayaNodeError, AttributeError):
                            # Skip nodes that don't support override attributes
                            continue
                self.logger.info(f"Set reference {ref_node} to selectable mode")

            return True

        except Exception as e:
            self.logger.error(f"Failed to set reference mode: {str(e)}")
            pm.displayError(f"Failed to set reference mode: {str(e)}")
            return False

    def toggle_reference_selectability(self, namespace: str = None) -> bool:
        """Toggle the selectability of a reference by namespace.

        Parameters:
            namespace (str): The namespace of the reference to toggle. If None, toggles all references.

        Returns:
            bool: True if successful, False otherwise.
        """
        try:
            references_to_toggle = []

            if namespace is None:
                # Toggle all references
                references_to_toggle = self.current_references
            else:
                # Find reference by namespace
                for ref in self.current_references:
                    if ref.namespace == namespace:
                        references_to_toggle.append(ref)
                        break

            if not references_to_toggle:
                self.logger.warning(f"No references found for namespace: {namespace}")
                return False

            for ref in references_to_toggle:
                # Check current state - if referenceFlag is True, it's currently reference-only
                current_reference_only = ref._refNode.referenceFlag.get()
                # Toggle the state
                self.set_reference_mode(ref, reference_only=not current_reference_only)

            return True

        except Exception as e:
            self.logger.error(f"Failed to toggle reference selectability: {str(e)}")
            pm.displayError(f"Failed to toggle reference selectability: {str(e)}")
            return False

    def get_reference_info(self, namespace: str = None) -> dict:
        """Get detailed information about references and their selectability status.

        Parameters:
            namespace (str, optional): Specific namespace to query. If None, returns info for all references.

        Returns:
            dict: Reference information including selectability status
        """
        reference_info = {}

        references = self.current_references
        if namespace:
            references = [ref for ref in references if ref.namespace == namespace]

        for ref in references:
            try:
                is_reference_only = ref._refNode.referenceFlag.get()
                reference_info[ref.namespace] = {
                    "path": ref.path,
                    "namespace": ref.namespace,
                    "reference_only": is_reference_only,
                    "status": "Reference-Only" if is_reference_only else "Selectable",
                }
            except (AttributeError, pm.MayaNodeError) as e:
                reference_info[ref.namespace] = {
                    "path": ref.path,
                    "namespace": ref.namespace,
                    "reference_only": False,
                    "status": "Unknown",
                    "error": str(e),
                }

        return reference_info

    def print_reference_status(self):
        """Print the current status of all references to the console."""
        ref_info = self.get_reference_info()

        if not ref_info:
            print("No references found in the current scene.")
            return

        print("\n=== Reference Status ===")
        for namespace, info in ref_info.items():
            status = info.get("status", "Unknown")
            path = info.get("path", "Unknown")
            print(f"Namespace: {namespace}")
            print(f"  Status: {status}")
            print(f"  Path: {path}")
            if "error" in info:
                print(f"  Error: {info['error']}")
            print()

    def set_all_references_mode(self, reference_only: bool = True) -> bool:
        """Set all references to reference-only or selectable mode.

        Parameters:
            reference_only (bool): If True, makes all references non-selectable. If False, makes them selectable.

        Returns:
            bool: True if successful, False otherwise.
        """
        try:
            for ref in self.current_references:
                self.set_reference_mode(ref, reference_only=reference_only)

            mode_str = "reference-only" if reference_only else "selectable"
            self.logger.info(f"Set all references to {mode_str} mode")
            return True

        except Exception as e:
            self.logger.error(f"Failed to set all references mode: {str(e)}")
            pm.displayError(f"Failed to set all references mode: {str(e)}")
            return False

    def re_reference_as_mode(self, namespace: str, reference_only: bool = True) -> bool:
        """Re-reference an existing reference with a specific selectability mode.

        This method removes and re-adds a reference to change its selectability mode,
        useful when you want to change an existing selectable reference to reference-only or vice versa.

        Parameters:
            namespace (str): The namespace of the reference to re-reference
            reference_only (bool): If True, re-reference as reference-only. If False, as selectable.

        Returns:
            bool: True if successful, False otherwise.
        """
        try:
            # Find the reference by namespace
            target_ref = None
            for ref in self.current_references:
                if ref.namespace == namespace:
                    target_ref = ref
                    break

            if not target_ref:
                self.logger.warning(f"No reference found with namespace: {namespace}")
                return False

            # Store the file path
            file_path = target_ref.path

            # Remove the existing reference
            target_ref.remove()
            self.logger.info(f"Removed existing reference: {namespace}")

            # Re-add the reference with the new mode
            success = self.add_reference(
                namespace, file_path, reference_only=reference_only
            )

            if success:
                mode_str = "reference-only" if reference_only else "selectable"
                self.logger.info(f"Re-referenced {namespace} as {mode_str}")
            else:
                self.logger.error(f"Failed to re-reference {namespace}")

            return success

        except Exception as e:
            self.logger.error(f"Failed to re-reference {namespace}: {str(e)}")
            pm.displayError(f"Failed to re-reference {namespace}: {str(e)}")
            return False

    def add_reference(
        self, namespace: str, file_path: str, reference_only: bool = False
    ) -> bool:
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
        normalized_file_path = os.path.normpath(file_path)

        # Check if the file is already referenced
        for ref in self.current_references:
            if os.path.normpath(ref.path) == normalized_file_path:
                print(f"File already referenced: {file_path}")
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

            # Set reference to reference-only mode if requested
            if reference_only:
                self.set_reference_mode(ref, reference_only=True)

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
    def __init__(self, slot, log_level="WARNING"):
        super().__init__()
        self.logger.setLevel(log_level)

        self.slot = slot
        self.sb = slot.sb
        self.ui = slot.ui

        self._last_dir_valid = None
        self.logger.debug("ReferenceManagerController initialized.")

    @property
    def current_working_dir(self):
        if not hasattr(self, "_current_working_dir"):
            self._current_working_dir = self.current_workspace
        self.logger.debug(f"Getting current_working_dir: {self._current_working_dir}")
        return self._current_working_dir

    @current_working_dir.setter
    def current_working_dir(self, value):
        self.logger.debug(f"Setting current_working_dir to: {value}")
        if os.path.isdir(value):
            self._current_working_dir = value
            self.invalidate_workspace_files()
            self.refresh_file_list()

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

    def format_table_item(self, item, file_path: str) -> None:
        """Apply coloring based on whether the file is the current scene or a reference."""
        norm_fp = os.path.normpath(file_path)
        current_scene = os.path.normpath(pm.sceneName()) if pm.sceneName() else ""
        referenced_paths = getattr(self, "_referenced_paths", None)

        if referenced_paths is None:
            referenced_paths = {
                os.path.normpath(ref.path) for ref in self.current_references
            }
            self._referenced_paths = referenced_paths  # cache for this update

        color = "#FFFFFF"  # default
        style = ""

        if norm_fp == current_scene:
            color = "#3C8D3C"  # green
        elif norm_fp in referenced_paths:
            # Check if this reference is in reference-only mode
            ref_is_reference_only = False
            for ref in self.current_references:
                if os.path.normpath(ref.path) == norm_fp:
                    try:
                        ref_is_reference_only = ref._refNode.referenceFlag.get()
                    except (AttributeError, pm.MayaNodeError):
                        ref_is_reference_only = False
                    break

            if ref_is_reference_only:
                color = "#8A6914"  # darker brown for reference-only
                style = "font-style: italic;"  # italic text for reference-only
            else:
                color = "#B49B5C"  # gold/brown for normal references

        item.setForeground(self.sb.QtGui.QBrush(self.sb.QtGui.QColor(color)))

        # Apply styling if needed
        if style:
            font = item.font()
            font.setItalic(True)
            item.setFont(font)
        else:
            font = item.font()
            font.setItalic(False)
            item.setFont(font)

        # Update tooltip to show reference status
        if norm_fp in referenced_paths:
            for ref in self.current_references:
                if os.path.normpath(ref.path) == norm_fp:
                    try:
                        ref_is_reference_only = ref._refNode.referenceFlag.get()
                        status = (
                            "Reference-Only (Non-selectable)"
                            if ref_is_reference_only
                            else "Reference (Selectable)"
                        )
                        item.setToolTip(f"{file_path}\nStatus: {status}")
                    except (AttributeError, pm.MayaNodeError):
                        item.setToolTip(f"{file_path}\nStatus: Reference")
                    break
        else:
            item.setToolTip(file_path)

        self.logger.debug(f"Formatted table item for {file_path} with color {color}")

    def handle_item_selection(self):
        t = self.ui.tbl000
        selected_items = [
            t.item(idx.row(), 0)
            for idx in t.selectedIndexes()
            if idx.column() == 0 and t.item(idx.row(), 0)
        ]
        selected_data = {
            (item.text(), item.data(self.sb.QtCore.Qt.UserRole))
            for item in selected_items
        }

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
            # Check if Ctrl key is held for reference-only mode
            modifiers = self.sb.QtWidgets.QApplication.keyboardModifiers()
            reference_only = modifiers == self.sb.QtCore.Qt.ControlModifier

            success = self.add_reference(
                namespace, file_path, reference_only=reference_only
            )
            if not success:
                for item in selected_items:
                    if item.text() == namespace:
                        item.setSelected(False)
                        break

    @block_table_selection_method
    def sync_selection_to_references(self):
        t = self.ui.tbl000
        t.blockSignals(True)
        try:
            t.clearSelection()
            current_namespaces = {ref.namespace for ref in self.current_references}
            self.logger.debug(
                f"Syncing selection to current references: {current_namespaces}"
            )
            for row in range(t.rowCount()):
                item = t.item(row, 0)
                if item and item.text() in current_namespaces:
                    item.setSelected(True)
        finally:
            t.blockSignals(False)

    def update_current_dir(self, text: Optional[str] = None):
        text = text or self.ui.txt000.text()
        new_dir = os.path.normpath(text.strip())

        is_valid = os.path.isdir(new_dir)
        changed = new_dir != self.current_working_dir

        self.logger.debug(
            f"Updating current dir to: {new_dir}, is_valid: {is_valid}, changed: {changed}"
        )

        self.ui.txt000.setToolTip(new_dir if is_valid else "Invalid directory")
        self.ui.txt000.set_action_color("reset" if is_valid else "invalid")

        revalidate = is_valid and (changed or self._last_dir_valid is False)
        self._last_dir_valid = is_valid

        if revalidate:
            self.logger.debug("Revalidating and updating current working dir.")
            self.current_working_dir = new_dir
            self.ui.cmb000.init_slot()
            self.refresh_file_list(invalidate=True)
        elif not is_valid:
            self.logger.debug("Directory is not valid, clearing workspace combo box.")
            self.ui.cmb000.clear()
            self.current_working_dir = new_dir

    @block_table_selection_method
    def refresh_file_list(self, invalidate=False):
        """Refresh the file list for the table widget."""
        if invalidate:
            self.logger.debug("Invalidating workspace files cache.")
            self.invalidate_workspace_files()

        index = self.ui.cmb000.currentIndex()
        workspace_path = self.ui.cmb000.itemData(index)
        if workspace_path is None:
            self.logger.warning("No workspace selected in combo box.")
            return

        self.logger.debug(f"Refreshing file list for workspace: {workspace_path}")

        if not workspace_path or not os.path.isdir(workspace_path):
            self.slot.logger.warning(
                f"[refresh_file_list] Invalid workspace: {workspace_path}"
            )
            return

        file_list = self.workspace_files.get(workspace_path, [])

        filter_text = self.ui.txt001.text().strip()
        if filter_text:
            self.logger.debug(f"Filtering file list with filter: {filter_text}")
            file_list = ptk.filter_list(file_list, inc=filter_text, basename_only=True)

        if not file_list:
            self.logger.warning(f"No scene files found in workspace: {workspace_path}")
        else:
            self.logger.debug(f"Found {len(file_list)} scenes to populate in table.")

        file_names = [os.path.basename(f) for f in file_list]
        self.logger.debug(f"Updating table with {len(file_names)} files.")
        self.update_table(file_names, file_list)

    @block_table_selection_method
    def update_table(self, file_names, file_list):
        t = self.ui.tbl000
        existing = {
            t.item(row, 0).text(): row for row in range(t.rowCount()) if t.item(row, 0)
        }

        # Cache referenced paths for use in format_table_item
        self._referenced_paths = {
            os.path.normpath(ref.path) for ref in self.current_references
        }

        to_remove = [row for name, row in existing.items() if name not in file_names]
        self.logger.debug(f"Rows to remove: {to_remove}")
        for row in reversed(sorted(to_remove)):
            if t.cellWidget(row, 1):
                t.removeCellWidget(row, 1)
            t.removeRow(row)

        for idx, (scene_name, file_path) in enumerate(zip(file_names, file_list)):
            self.logger.debug(f"Inserting row for: {scene_name} ({file_path})")
            row = existing.get(scene_name)
            if row is None:
                row = t.rowCount()
                t.insertRow(row)

            item = t.item(row, 0)
            if not item:
                item = self.sb.QtWidgets.QTableWidgetItem(scene_name)
                item.setFlags(item.flags() | self.sb.QtCore.Qt.ItemIsEditable)
                t.setItem(row, 0, item)

            item.setText(scene_name)
            item.setData(self.sb.QtCore.Qt.UserRole, file_path)

            self.format_table_item(item, file_path)

            if not t.cellWidget(row, 1):
                btn_open = self.sb.QtWidgets.QPushButton("Open")
                btn_open.clicked.connect(partial(self.open_scene, file_path))
                t.setCellWidget(row, 1, btn_open)

        self.logger.debug("Syncing selection to references after table update.")
        self.sync_selection_to_references()
        t.apply_formatting()
        t.stretch_column_to_fill(0)

        self._referenced_paths = None  # Clear cache after update

    def open_scene(self, file_path: str):
        self.logger.debug(f"Attempting to open scene: {file_path}")
        if os.path.exists(file_path):
            pm.openFile(file_path, force=True)
            self.logger.info(f"Opened scene: {file_path}")
        else:
            self.slot.logger.error(f"Scene file not found: {file_path}")
            self.sb.message_box(f"Scene file not found:<br>{file_path}")

    @block_table_selection_method
    def unreference_all(self):
        self.logger.debug("Unreferencing all references.")
        self.remove_references()
        self.refresh_file_list()

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

    @block_table_selection_method
    def set_references_selectable(self):
        """Set all current references to selectable mode."""
        self.logger.debug("Setting all references to selectable mode.")
        success = self.set_all_references_mode(reference_only=False)
        if success:
            self.sb.message_box("<b>All references set to selectable mode.</b>")
            self.refresh_file_list()
        else:
            self.sb.message_box("<b>Failed to set references to selectable mode.</b>")

    @block_table_selection_method
    def set_references_reference_only(self):
        """Set all current references to reference-only mode."""
        self.logger.debug("Setting all references to reference-only mode.")
        success = self.set_all_references_mode(reference_only=True)
        if success:
            self.sb.message_box("<b>All references set to reference-only mode.</b>")
            self.refresh_file_list()
        else:
            self.sb.message_box(
                "<b>Failed to set references to reference-only mode.</b>"
            )

    @block_table_selection_method
    def toggle_selected_reference_selectability(self):
        """Toggle selectability of currently selected references in the table."""
        t = self.ui.tbl000
        selected_items = [
            t.item(idx.row(), 0)
            for idx in t.selectedIndexes()
            if idx.column() == 0 and t.item(idx.row(), 0)
        ]

        if not selected_items:
            self.sb.message_box("<b>No references selected to toggle.</b>")
            return

        selected_namespaces = {item.text() for item in selected_items}
        current_namespaces = {ref.namespace for ref in self.current_references}

        # Only toggle references that are actually loaded
        namespaces_to_toggle = selected_namespaces & current_namespaces

        if not namespaces_to_toggle:
            self.sb.message_box("<b>Selected items are not currently referenced.</b>")
            return

        self.logger.debug(
            f"Toggling selectability for namespaces: {namespaces_to_toggle}"
        )

        success_count = 0
        for namespace in namespaces_to_toggle:
            if self.toggle_reference_selectability(namespace):
                success_count += 1

        if success_count > 0:
            self.sb.message_box(
                f"<b>Toggled selectability for {success_count} reference(s).</b>"
            )
            self.refresh_file_list()
        else:
            self.sb.message_box("<b>Failed to toggle reference selectability.</b>")


class ReferenceManagerSlots(ptk.HelpMixin, ptk.LoggingMixin):
    def __init__(self, switchboard, log_level="DEBUG"):
        super().__init__()
        self.logger.setLevel(log_level)

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.reference_manager

        self.controller = ReferenceManagerController(self)
        self.ui.txt000.setText(self.controller.current_working_dir)

        self.ui.b002.clicked.connect(self.controller.unreference_all)
        self.ui.b003.clicked.connect(self.controller.unlink_all)
        self.ui.b005.clicked.connect(self.controller.convert_to_assembly)
        self.ui.b004.clicked.connect(
            lambda: self.controller.refresh_file_list(invalidate=True)
        )

        # Add new buttons for reference selectability control
        # These would need to be added to the UI file, but we'll assume they exist
        # b006: Set all references to selectable
        # b007: Set all references to reference-only
        # b008: Toggle selected reference selectability
        if hasattr(self.ui, "b006"):
            self.ui.b006.clicked.connect(self.controller.set_references_selectable)
        if hasattr(self.ui, "b007"):
            self.ui.b007.clicked.connect(self.controller.set_references_reference_only)
        if hasattr(self.ui, "b008"):
            self.ui.b008.clicked.connect(
                self.controller.toggle_selected_reference_selectability
            )

        self.script_job = pm.scriptJob(
            event=["SceneOpened", self.controller.refresh_file_list]
        )
        self.logger.debug("ReferenceManagerSlots initialized and scriptJob created.")

    def __del__(self):
        if hasattr(self, "script_job") and pm.scriptJob(exists=self.script_job):
            pm.scriptJob(kill=self.script_job, force=True)
            self.logger.debug("ScriptJob killed in __del__.")

    def tbl000_init(self, widget):
        if not widget.is_initialized:
            widget.setColumnCount(2)
            widget.setHorizontalHeaderLabels(["Files", "Open"])
            widget.setEditTriggers(self.sb.QtWidgets.QAbstractItemView.DoubleClicked)
            widget.setSelectionBehavior(self.sb.QtWidgets.QAbstractItemView.SelectRows)
            widget.setSelectionMode(self.sb.QtWidgets.QAbstractItemView.MultiSelection)
            widget.verticalHeader().setVisible(False)
            widget.setAlternatingRowColors(True)
            widget.setWordWrap(False)
            widget.itemSelectionChanged.connect(self.controller.handle_item_selection)

            # Add context menu for reference mode control
            widget.setContextMenuPolicy(self.sb.QtCore.Qt.CustomContextMenu)
            widget.customContextMenuRequested.connect(self.show_table_context_menu)

            # Add keyboard shortcuts
            toggle_shortcut = self.sb.QtWidgets.QShortcut(
                self.sb.QtGui.QKeySequence("T"), widget
            )
            toggle_shortcut.activated.connect(
                self.controller.toggle_selected_reference_selectability
            )

            self.logger.debug(
                "tbl000 table widget initialized with context menu and shortcuts."
            )

    def show_table_context_menu(self, position):
        """Show context menu for table operations."""
        t = self.ui.tbl000
        if t.itemAt(position) is None:
            return

        menu = self.sb.QtWidgets.QMenu()

        # Add reference mode actions
        selectable_action = menu.addAction("Set All References Selectable")
        selectable_action.triggered.connect(self.controller.set_references_selectable)

        reference_only_action = menu.addAction("Set All References Reference-Only")
        reference_only_action.triggered.connect(
            self.controller.set_references_reference_only
        )

        menu.addSeparator()

        toggle_action = menu.addAction("Toggle Selected Reference Selectability")
        toggle_action.triggered.connect(
            self.controller.toggle_selected_reference_selectability
        )

        # Show the menu
        global_pos = t.mapToGlobal(position)
        menu.exec_(global_pos)

    def txt000_init(self, widget):
        """Initialize the text input for the current working directory."""
        if not widget.is_initialized:
            widget.menu.add(
                "QPushButton",
                setText="Browse",
                setObjectName="b000",
                setToolTip="Open a file browser to select a root directory.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Set To Workspace",
                setObjectName="b001",
                setToolTip="Set the root folder to that of the current workspace.",
            )
            widget.menu.add(
                "QCheckBox",
                setText="Recursive Search",
                setObjectName="chk000",
                setChecked=True,
                setToolTip="Also search sub-folders.",
            )

            # Add help text for new functionality
            help_text = (
                "Reference Manager Controls:\n"
                "• Hold Ctrl while selecting files to add as reference-only (non-selectable)\n"
                "• Right-click table for reference mode options\n"
                "• Press 'T' to toggle selectability of selected references\n"
                "• Reference-only items appear in italic with darker brown color"
            )

            widget.menu.add(
                "QLabel",
                setText="Help",
                setObjectName="lbl_help",
                setToolTip=help_text,
                setWordWrap=True,
            )
            widget.textChanged.connect(
                lambda text: self.sb.defer_with_timer(
                    lambda: self.controller.update_current_dir(text), ms=500
                )
            )
            self.logger.debug("txt000 text input initialized.")

        self.controller.update_current_dir()

    def cmb000_init(self, widget):
        root_dir = self.ui.txt000.text()
        workspaces = EnvUtils.find_workspaces(
            root_dir, return_type="dirname|dir", ignore_empty=True
        )

        widget.clear()
        widget.add(workspaces)

        if workspaces:
            widget.setCurrentIndex(0)
            first_path = widget.itemData(0)
            if first_path and os.path.isdir(first_path):
                self.controller.current_working_dir = first_path
                self.controller.refresh_file_list(invalidate=True)

        self.logger.debug(f"cmb000 combo box initialized with workspaces: {workspaces}")

    def cmb000(self, index, widget):
        """Handle workspace selection changes."""
        path = widget.itemData(index)
        self.logger.debug(f"cmb000 changed to index {index}, path: {path}")
        if path and os.path.isdir(path):
            self.controller.current_working_dir = path

    def chk000(self, checked):
        """Handle the recursive search toggle."""
        self.logger.debug(f"chk000 recursive search toggled: {checked}")
        self.controller.recursive_search = checked

    def txt001(self, text):
        """Handle the filter text input."""
        self.logger.debug(f"txt001 filter text changed: {text}")
        self.controller._filter_text = text.strip()
        self.controller.refresh_file_list(invalidate=True)

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

    def b001(self):
        """Set dir to current workspace."""
        self.logger.debug("b001 set to current workspace clicked.")
        self.ui.txt000.setText(self.controller.current_workspace)

    def b006(self):
        """Set all references to selectable mode."""
        self.logger.debug("b006 set all references selectable clicked.")
        self.controller.set_references_selectable()

    def b007(self):
        """Set all references to reference-only mode."""
        self.logger.debug("b007 set all references reference-only clicked.")
        self.controller.set_references_reference_only()

    def b008(self):
        """Toggle selectability of selected references."""
        self.logger.debug("b008 toggle selected reference selectability clicked.")
        self.controller.toggle_selected_reference_selectability()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("reference_manager", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
