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
        """Rebuild workspace file cache as {workspace_path: [maya files]}."""
        self._workspace_files = {}

        workspaces = EnvUtils.find_workspaces(
            self.current_working_dir,
            return_type="dirname|dir",
            ignore_empty=True,
        )

        for _, ws_path in workspaces:
            if os.path.isdir(ws_path):
                self._workspace_files[ws_path] = EnvUtils.get_workspace_scenes(
                    root_dir=ws_path,
                    full_path=True,
                    recursive=self.recursive_search,
                    omit_autosave=True,
                )

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


class ReferenceManagerController(ReferenceManager):
    def __init__(self, slot):
        super().__init__()
        self.slot = slot
        self.sb = slot.sb
        self.ui = slot.ui

        self._last_dir_valid = None

    @property
    def current_working_dir(self):
        if not hasattr(self, "_current_working_dir"):
            self._current_working_dir = self.current_workspace
        return self._current_working_dir

    @current_working_dir.setter
    def current_working_dir(self, value):
        if os.path.isdir(value):
            self._current_working_dir = value
            self.invalidate_workspace_files()
            self.refresh_file_list()

    def block_table_selection_method(method):
        @wraps(method)
        def wrapper(self, *args, **kwargs):
            t = self.ui.tbl000
            t.blockSignals(True)
            try:
                return method(self, *args, **kwargs)
            finally:
                t.blockSignals(False)

        return wrapper

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

        for namespace in namespaces_to_remove:
            self.remove_references(namespace)

        for namespace in namespaces_to_add:
            file_path = next(fp for ns, fp in selected_data if ns == namespace)
            success = self.add_reference(namespace, file_path)
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

        self.ui.txt000.setToolTip(new_dir if is_valid else "Invalid directory")
        self.ui.txt000.set_action_color("reset" if is_valid else "invalid")

        revalidate = is_valid and (changed or self._last_dir_valid is False)
        self._last_dir_valid = is_valid

        if revalidate:
            self.current_working_dir = new_dir
            self.ui.cmb000.init_slot()
            self.refresh_file_list(invalidate=True)
        elif not is_valid:
            self.ui.cmb000.clear()
            self.current_working_dir = new_dir

    @block_table_selection_method
    def refresh_file_list(self, invalidate=False):
        """Refresh the file list for the table widget."""
        if invalidate:
            self.invalidate_workspace_files()

        index = self.ui.cmb000.currentIndex()
        workspace_path = self.ui.cmb000.itemData(index)

        if not workspace_path or not os.path.isdir(workspace_path):
            self.slot.logger.warning(
                f"[refresh_file_list] Invalid workspace: {workspace_path}"
            )
            return

        file_list = self.workspace_files.get(workspace_path, [])

        filter_text = self.ui.txt001.text().strip()
        if filter_text:
            file_list = ptk.filter_list(file_list, inc=filter_text, basename_only=True)

        file_names = [os.path.basename(f) for f in file_list]
        self.update_table(file_names, file_list)

    @block_table_selection_method
    def update_table(self, file_names, file_list):
        t = self.ui.tbl000
        existing = {
            t.item(row, 0).text(): row for row in range(t.rowCount()) if t.item(row, 0)
        }

        to_remove = [row for name, row in existing.items() if name not in file_names]
        for row in reversed(sorted(to_remove)):
            if t.cellWidget(row, 1):
                t.removeCellWidget(row, 1)
            t.removeRow(row)

        for idx, (scene_name, file_path) in enumerate(zip(file_names, file_list)):
            row = existing.get(scene_name)
            if row is None:
                row = t.rowCount()
                t.insertRow(row)

            item = t.item(row, 0)
            if not item:
                item = self.sb.QtWidgets.QTableWidgetItem(scene_name)
                item.setFlags(item.flags() | self.sb.QtCore.Qt.ItemIsEditable)
                item.setData(self.sb.QtCore.Qt.UserRole, file_path)
                t.setItem(row, 0, item)
            else:
                if item.text() != scene_name:
                    item.setText(scene_name)
                item.setData(self.sb.QtCore.Qt.UserRole, file_path)

            if not t.cellWidget(row, 1):
                btn_open = self.sb.QtWidgets.QPushButton("Open")
                btn_open.clicked.connect(partial(self.open_scene, file_path))
                t.setCellWidget(row, 1, btn_open)

        self.sync_selection_to_references()
        t.apply_formatting()
        t.stretch_column_to_fill(0)

    def open_scene(self, file_path: str):
        if os.path.exists(file_path):
            pm.openFile(file_path, force=True)
        else:
            self.slot.logger.error(f"Scene file not found: {file_path}")
            self.sb.message_box(f"Scene file not found:<br>{file_path}")

    @block_table_selection_method
    def unreference_all(self):
        self.remove_references()
        self.refresh_file_list()

    @block_table_selection_method
    def unlink_all(self):
        if (
            self.sb.message_box(
                "<b>Warning:</b> The unlink operation is not undoable.<br>Do you want to proceed?",
                "Yes",
                "No",
            )
            != "Yes"
        ):
            self.sb.message_box("<b>Unlink operation cancelled.</b>")
            return

        self.import_references(remove_namespace=True)
        self.refresh_file_list()

    def convert_to_assembly(self):
        user_choice = self.sb.message_box(
            "<b>Warning:</b> The convert to assembly operation is not undoable.<br>Do you want to proceed?",
            "Yes",
            "No",
        )
        if user_choice == "Yes":
            AssemblyManager.convert_references_to_assemblies()
        else:
            self.sb.message_box("<b>Convert to assembly operation cancelled.</b>")


class ReferenceManagerSlots(ptk.HelpMixin, ptk.LoggingMixin):
    def __init__(self, **kwargs):
        super().__init__()
        self.sb = kwargs.get("switchboard")
        self.ui = self.sb.loaded_ui.reference_manager

        self.controller = ReferenceManagerController(self)
        self.ui.txt000.setText(self.controller.current_working_dir)

        self.ui.b002.clicked.connect(self.controller.unreference_all)
        print("Unreference button connected.", self)
        self.ui.b003.clicked.connect(self.controller.unlink_all)
        self.ui.b005.clicked.connect(self.controller.convert_to_assembly)
        self.ui.b004.clicked.connect(
            lambda: self.controller.refresh_file_list(invalidate=True)
        )

        self.script_job = pm.scriptJob(
            event=["SceneOpened", self.controller.refresh_file_list]
        )

    def __del__(self):
        if hasattr(self, "script_job") and pm.scriptJob(exists=self.script_job):
            pm.scriptJob(kill=self.script_job, force=True)

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
            widget.textChanged.connect(
                lambda text: self.sb.defer_with_timer(
                    lambda: self.controller.update_current_dir(text), ms=500
                )
            )

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

    def cmb000(self, index, widget):
        """Handle workspace selection changes."""
        path = widget.itemData(index)
        if path and os.path.isdir(path):
            self.controller.current_working_dir = path

    def chk000(self, checked):
        """Handle the recursive search toggle."""
        self.controller.recursive_search = checked

    def txt001(self, text):
        """Handle the filter text input."""
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
        if selected_directory:
            self.ui.txt000.setText(selected_directory)

    def b001(self):
        """Set dir to current workspace."""
        self.ui.txt000.setText(self.controller.current_workspace)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("reference_manager", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
