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

    def find_available_workspaces(self, root_dir: str = None) -> list:
        """Find all available workspaces under the given root directory.

        Args:
            root_dir: Directory to search in. If None, uses current_working_dir.

        Returns:
            List of (dirname, path) tuples for found workspaces.
        """
        if root_dir is None:
            root_dir = self.current_working_dir

        if not root_dir or not os.path.isdir(root_dir):
            return []

        return EnvUtils.find_workspaces(
            root_dir,
            return_type="dirname|dir",
            ignore_empty=True,
            recursive=self.recursive_search,
        )

    def invalidate_workspace_files(self):
        self.logger.debug(f"Scanning for workspaces under: {self.current_working_dir}")
        self._workspace_files = {}

        workspaces = self.find_available_workspaces()

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

    def set_reference_mode(self, reference=None, reference_only: bool = True) -> bool:
        """Set reference(s) to be reference-only (non-selectable) or selectable.

        Parameters:
            reference: The reference object (FileReference or reference node name), or None to affect all references
            reference_only (bool): If True, makes reference(s) non-selectable. If False, makes them selectable.

        Returns:
            bool: True if successful, False otherwise.
        """
        try:
            # If no specific reference is given, apply to all references
            if reference is None:
                for ref in self.current_references:
                    self.set_reference_mode(ref, reference_only=reference_only)

                mode_str = "reference-only" if reference_only else "selectable"
                self.logger.info(f"Set all references to {mode_str} mode")
                return True

            # Get the reference node if we have a FileReference object
            if hasattr(reference, "_refNode"):
                ref_node = reference._refNode
            elif isinstance(reference, str):
                # Assume it's a reference node name
                ref_node = pm.PyNode(reference)
            else:
                ref_node = reference

            # Set the reference display mode by controlling selectability of referenced objects
            referenced_nodes = pm.referenceQuery(ref_node, nodes=True, dagPath=True)
            if referenced_nodes:
                for node_name in referenced_nodes:
                    try:
                        node = pm.PyNode(node_name)
                        if hasattr(node, "overrideEnabled") and hasattr(
                            node, "overrideDisplayType"
                        ):
                            # Check if the attribute is locked or connected before trying to modify
                            if (
                                node.overrideEnabled.isLocked()
                                or node.overrideEnabled.isConnected()
                            ):
                                self.logger.debug(
                                    f"Skipping {node_name}: overrideEnabled is locked or connected"
                                )
                                continue

                            if reference_only:
                                # Make reference non-selectable
                                node.overrideEnabled.set(True)
                                # Only set display type if it's not locked
                                if (
                                    not node.overrideDisplayType.isLocked()
                                    and not node.overrideDisplayType.isConnected()
                                ):
                                    node.overrideDisplayType.set(
                                        2
                                    )  # Reference display type
                            else:
                                # Make reference selectable (reset overrides)
                                node.overrideEnabled.set(False)
                    except (pm.MayaNodeError, AttributeError, RuntimeError) as e:
                        # Skip nodes that don't support override attributes or have other issues
                        self.logger.debug(f"Skipping {node_name}: {str(e)}")
                        continue

            # Store the reference mode state in a custom attribute for tracking
            try:
                if not ref_node.hasAttr("referenceOnlyMode"):
                    ref_node.addAttr(
                        "referenceOnlyMode", attributeType="bool", defaultValue=False
                    )
                ref_node.referenceOnlyMode.set(reference_only)
            except:
                # If we can't add the attribute, just continue
                pass

            mode_str = "reference-only" if reference_only else "selectable"
            self.logger.info(f"Set reference {ref_node} to {mode_str} mode")
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
                # Check current state using our custom attribute or default to False
                try:
                    if ref._refNode.hasAttr("referenceOnlyMode"):
                        current_reference_only = ref._refNode.referenceOnlyMode.get()
                    else:
                        current_reference_only = False
                except:
                    current_reference_only = False

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
                # Check if reference has our custom reference-only mode attribute
                if ref._refNode.hasAttr("referenceOnlyMode"):
                    is_reference_only = ref._refNode.referenceOnlyMode.get()
                else:
                    is_reference_only = False

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
        self._updating_directory = False  # Flag to prevent cascading UI events
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
            old_value = getattr(self, "_current_working_dir", None)
            self._current_working_dir = value

            # Only invalidate and refresh if the directory actually changed
            if old_value != value:
                self.logger.debug(
                    f"Directory changed from {old_value} to {value}, invalidating workspace files"
                )
                self.invalidate_workspace_files()
                # Don't call refresh_file_list here to avoid circular calls
                # Let the calling code handle the refresh timing
            else:
                self.logger.debug("Directory unchanged, no invalidation needed")
        else:
            self.logger.warning(
                f"Invalid directory set as current_working_dir: {value}"
            )
            # Still set it even if invalid to maintain consistency
            self._current_working_dir = value

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
        """Apply enable/disable state based on whether the file is the current scene."""
        norm_fp = os.path.normpath(file_path)
        current_scene = os.path.normpath(pm.sceneName()) if pm.sceneName() else ""
        is_current_scene = norm_fp == current_scene

        if is_current_scene:
            # Disable the item completely to prevent selection and referencing
            item.setFlags(
                item.flags()
                & ~(
                    self.sb.QtCore.Qt.ItemIsSelectable | self.sb.QtCore.Qt.ItemIsEnabled
                )
            )
            item.setToolTip(f"Current scene file - cannot be referenced\n{file_path}")
            # Apply grayed out style
            font = item.font()
            font.setItalic(True)
            item.setFont(font)
        else:
            # Re-enable the item if it was previously disabled
            item.setFlags(
                item.flags()
                | (self.sb.QtCore.Qt.ItemIsSelectable | self.sb.QtCore.Qt.ItemIsEnabled)
            )
            item.setToolTip(f"Available for referencing\n{file_path}")
            # Reset styling
            font = item.font()
            font.setItalic(False)
            item.setFont(font)

    def handle_item_selection(self):
        t = self.ui.tbl000
        selected_items = [
            t.item(idx.row(), 0)
            for idx in t.selectedIndexes()
            if idx.column() == 0 and t.item(idx.row(), 0)
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
        """Sync the table selection to match current scene references."""
        t = self.ui.tbl000
        t.blockSignals(True)
        try:
            t.clearSelection()
            current_references = self.current_references
            current_scene = os.path.normpath(pm.sceneName()) if pm.sceneName() else ""

            # Create a mapping from file paths to namespaces for current references
            ref_path_to_namespace = {
                os.path.normpath(ref.path): ref.namespace for ref in current_references
            }

            self.logger.debug(
                f"Syncing selection to current references: {[ref.namespace for ref in current_references]}"
            )
            self.logger.debug(
                f"Reference path to namespace mapping: {ref_path_to_namespace}"
            )

            for row in range(t.rowCount()):
                item = t.item(row, 0)
                if item:
                    file_path = item.data(self.sb.QtCore.Qt.UserRole)
                    norm_fp = os.path.normpath(file_path) if file_path else ""

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

        # Apply table formatting
        t.apply_formatting()
        t.stretch_column_to_fill(0)

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


class ReferenceManagerSlots(ptk.HelpMixin, ptk.LoggingMixin):
    def __init__(self, switchboard, log_level="DEBUG"):
        super().__init__()
        self.logger.setLevel(log_level)

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.reference_manager

        # Flag to prevent checkbox events during initialization
        self._initializing = True

        self.controller = ReferenceManagerController(self)
        self.ui.txt000.setText(self.controller.current_working_dir)

        self.ui.b002.clicked.connect(self.controller.unreference_all)
        self.ui.b003.clicked.connect(self.controller.unlink_all)
        self.ui.b005.clicked.connect(self.controller.convert_to_assembly)
        self.ui.b004.clicked.connect(
            lambda: self.controller.refresh_file_list(invalidate=True)
        )

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
            "QCheckBox",
            setText="Reference Only",
            setObjectName="chk001",
            setToolTip="Set all references to reference-only, meaning they cannot be selected or modified.",
        )

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

            # Add context menu for reference mode operations
            widget.menu.setTitle("Reference Options:")
            widget.menu.add(
                "QPushButton",
                setText="Toggle Reference Mode",
                setObjectName="btn_toggle_mode",
                setToolTip="Toggle between selectable and reference-only mode for selected references (Hotkey: T)",
            )
            widget.menu.add(
                "QPushButton",
                setText="Set to Reference-Only",
                setObjectName="btn_set_ref_only",
                setToolTip="Set selected references to reference-only (non-selectable) mode",
            )
            widget.menu.add(
                "QPushButton",
                setText="Set to Selectable",
                setObjectName="btn_set_selectable",
                setToolTip="Set selected references to selectable mode",
            )

            # Add keyboard shortcut for toggling reference mode
            shortcut = self.sb.QtWidgets.QShortcut(
                self.sb.QtGui.QKeySequence("T"), widget
            )
            shortcut.activated.connect(self.tbl000_toggle_reference_mode)

            self.logger.debug(
                "tbl000 table widget initialized with context menu and shortcuts."
            )

    def tbl000_toggle_reference_mode(self):
        """Toggle reference mode for selected references."""
        selected_namespaces = self._get_selected_reference_namespaces()
        if not selected_namespaces:
            self.sb.message_box("No references selected.")
            return

        for namespace in selected_namespaces:
            self.controller.toggle_reference_selectability(namespace)

        # Refresh to update visual styling
        self.controller.refresh_file_list()

    def _get_selected_reference_namespaces(self):
        """Get namespaces of selected items that are current references."""
        t = self.ui.tbl000
        selected_items = [
            t.item(idx.row(), 0)
            for idx in t.selectedIndexes()
            if idx.column() == 0 and t.item(idx.row(), 0)
        ]

        current_namespaces = {
            ref.namespace for ref in self.controller.current_references
        }
        selected_namespaces = []

        for item in selected_items:
            if item.text() in current_namespaces:
                selected_namespaces.append(item.text())

        return selected_namespaces

    def btn_toggle_mode(self):
        """Handle toggle mode button click."""
        self.tbl000_toggle_reference_mode()

    def btn_set_ref_only(self):
        """Set selected references to reference-only mode."""
        selected_namespaces = self._get_selected_reference_namespaces()
        if not selected_namespaces:
            self.sb.message_box("No references selected.")
            return

        for namespace in selected_namespaces:
            # Find the reference by namespace
            for ref in self.controller.current_references:
                if ref.namespace == namespace:
                    self.controller.set_reference_mode(ref, reference_only=True)
                    break

        # Refresh to update visual styling
        self.controller.refresh_file_list()

    def btn_set_selectable(self):
        """Set selected references to selectable mode."""
        selected_namespaces = self._get_selected_reference_namespaces()
        if not selected_namespaces:
            self.sb.message_box("No references selected.")
            return

        for namespace in selected_namespaces:
            # Find the reference by namespace
            for ref in self.controller.current_references:
                if ref.namespace == namespace:
                    self.controller.set_reference_mode(ref, reference_only=False)
                    break

        # Refresh to update visual styling
        self.controller.refresh_file_list()

    def txt000_init(self, widget):
        """Initialize the text input for the current working directory."""
        self.logger.debug(
            f"txt000_init called, is_initialized: {getattr(widget, 'is_initialized', False)}"
        )

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
                "• Current scene file (green) is disabled to prevent self-referencing\n"
                "• Hold Ctrl while selecting files to add as reference-only (non-selectable)\n"
                "• Referenced files appear selected and are colored gold/brown\n"
                "• Reference-only items appear in italic with darker brown color\n"
                "• Right-click table for reference mode options\n"
                "• Press 'T' to toggle selectability of selected references"
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

    def chk001(self, checked):
        """Set all references to reference-only mode."""
        self.controller.set_reference_mode(reference_only=checked)

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


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("reference_manager", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
