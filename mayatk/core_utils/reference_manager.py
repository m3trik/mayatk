# !/usr/bin/python
# coding=utf-8
import os
import re
import glob

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils import CoreUtils


class ReferenceManager(ptk.HelpMixin):
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
    def workspace_files(self):
        """Get workspace files, utilizing a cache to improve performance."""
        if not hasattr(self, "_workspace_files") or self._workspace_files is None:
            self._workspace_files = self.get_workspace_files(self.current_working_dir)
        return self._workspace_files

    @property
    def recursive_search(self):
        if not hasattr(self, "_recursive_search"):
            self._recursive_search = True  # Default value
        return self._recursive_search

    @recursive_search.setter
    def recursive_search(self, value):
        self._recursive_search = value
        self.invalidate_workspace_files()  # Invalidate cache when recursive_search changes

    def invalidate_workspace_files(self):
        """Invalidate the workspace files cache."""
        self._workspace_files = None

    def resolve_file_path(self, selected_file):
        """Resolve file path without requiring search_root as argument."""
        return next((fp for fp in self.workspace_files if selected_file in fp), None)

    def _matches_prefilter_regex(self, filename):
        """Check if a file is an auto-save file based on its name."""
        return bool(self.prefilter_regex.match(filename))

    def get_workspace_files(self, omit_autosave=True):
        # Use self.recursive_search to determine whether to search recursively
        workspace_files = glob.glob(
            os.path.join(self.current_working_dir, "**", "*.ma"),
            recursive=self.recursive_search,
        ) + glob.glob(
            os.path.join(self.current_working_dir, "**", "*.mb"),
            recursive=self.recursive_search,
        )

        # Filter out auto-save files if omit_autosave is True
        if omit_autosave:
            workspace_files = [
                fp
                for fp in workspace_files
                if not self._matches_prefilter_regex(os.path.basename(fp))
            ]

        return workspace_files

    def add_reference(self, namespace, file_path):
        if not os.path.exists(file_path):
            pm.displayError(f"Could not open file: {file_path}")
            return

        # Remove the file extension from the namespace
        namespace, _ = os.path.splitext(namespace)

        try:
            pm.createReference(file_path, namespace=namespace)
        except RuntimeError as e:
            if "Could not open file" in str(e):
                pm.displayError(f"Could not open file: {file_path}")
            else:
                raise

    def import_references(self, namespaces=None):
        """Imports the referenced objects into the scene.

        Parameters:
            namespaces (str, list of str, or None): A list of namespaces to import. If not provided, all references will be imported.
        """
        all_references = self.current_references

        # If namespaces are provided, filter the references
        if namespaces is not None:
            all_references = [
                ref
                for ref in all_references
                if ref.namespace in ptk.make_iterable(namespaces)
            ]

        for ref in all_references:
            ref.importContents()

    def remove_references(self, namespaces=None):
        """Remove references based on their namespaces.

        If no namespace is provided, all references will be removed.

        Parameters:
            namespaces (str, list of str, or None): The namespace(s) of the reference(s) to be removed.
                If None, all references will be removed. Default is None.
        """
        all_references = self.current_references

        if namespaces is None:
            # Unreference all
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


class ReferenceManagerSlots(ReferenceManager):
    def __init__(self):
        super().__init__()
        self.sb = self.switchboard()
        self.ui = self.sb.reference_manager

        # Initialize and connect UI components
        self.ui.txt000.setText(self.current_working_dir)
        self.ui.txt000.textChanged.connect(self.update_current_dir)
        self.ui.txt001.textEdited.connect(self.refresh_file_list)
        self.ui.list000.itemSelectionChanged.connect(self.handle_item_selection)
        self.ui.list000.setSelectionMode(self.sb.QAbstractItemView.MultiSelection)
        self.ui.b002.clicked.connect(self.unreference_all)
        self.ui.b003.clicked.connect(self.unlink_all)

    def txt000_init(self, widget):
        """ """
        widget.menu.mode = "context"
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

        # Connect buttons and checkbox to their respective methods
        widget.menu.b000.clicked.connect(self.browse_for_root_directory)
        widget.menu.b001.clicked.connect(self.set_dir_to_workspace)
        widget.menu.chk000.toggled.connect(self.handle_recursive_search_toggle)

        # Initialize list
        self.update_current_dir_tooltip()
        self.refresh_file_list()

    def handle_recursive_search_toggle(self, checked):
        self.recursive_search = checked
        self.refresh_file_list(invalidate=True)  # Invalidate and refresh the file list

    def update_current_dir(self, text=None, invalidate_and_refresh=False):
        """Update the current working directory.

        Parameters:
            text (str, optional): The directory path. If None, the text from ui.txt000 is used.
            invalidate_and_refresh (bool, optional): Whether to invalidate and refresh the file list.
        """
        new_dir = text or self.ui.txt000.text()
        if os.path.isdir(new_dir):
            self.current_working_dir = new_dir
            self.update_current_dir_tooltip()
            if invalidate_and_refresh:
                self.refresh_file_list(invalidate=True)

    def update_current_dir_tooltip(self):
        """Update the tooltip of txt000 based on its current text."""
        new_dir = self.ui.txt000.text()
        self.ui.txt000.setToolTip(new_dir)

    def browse_for_root_directory(self):
        selected_directory = self.sb.dir_dialog("Select a root directory")
        if selected_directory:
            self.ui.txt000.setText(selected_directory)
            self.update_current_dir(invalidate_and_refresh=True)

    def set_dir_to_workspace(self):
        self.ui.txt000.setText(self.current_workspace)
        self.update_current_dir(invalidate_and_refresh=True)

    def refresh_file_list(self, invalidate=False):
        """Refresh the file list based on the current filter text and workspace root."""
        if invalidate:
            self.invalidate_workspace_files()

        # Filter workspace files
        filter_text = self.ui.txt001.text().strip()

        file_list = self.workspace_files
        if filter_text:
            file_list = ptk.filter_list(file_list, inc=filter_text, basename_only=True)
        file_list = [os.path.basename(fp) for fp in file_list]

        # Block signals to prevent unintended UI updates
        self.ui.list000.blockSignals(True)
        self.ui.list000.clear()

        current_references = self.current_references

        # Populate list and set selection states
        items_to_select = []
        for file in file_list:
            item = self.sb.QListWidgetItem(file)
            full_path = self.resolve_file_path(file)
            item.setData(self.sb.Qt.UserRole, full_path)

            if any(
                os.path.normpath(ref.path) == os.path.normpath(full_path)
                for ref in current_references
            ):
                items_to_select.append(item)

            self.ui.list000.addItem(item)

        for item in items_to_select:
            item.setSelected(True)

        # Unblock signals after updating the list
        self.ui.list000.blockSignals(False)

    def handle_item_selection(self):
        """Handle the logic for item selection changes."""

        # Fetch selected items and associated data
        selected_items = self.ui.list000.selectedItems()
        selected_data = {
            (item.text(), item.data(self.sb.Qt.UserRole)) for item in selected_items
        }

        current_references = self.current_references
        current_namespaces = {ref.namespace for ref in current_references}

        namespaces_to_add = {ns for ns, _ in selected_data} - current_namespaces
        namespaces_to_remove = current_namespaces - {ns for ns, _ in selected_data}

        for namespace in namespaces_to_remove:
            self.remove_references(namespace)

        for namespace in namespaces_to_add:
            file_path = next(fp for ns, fp in selected_data if ns == namespace)
            self.add_reference(namespace, file_path)

    def unreference_all(self):
        """Slot to handle the unreference all button click."""
        self.remove_references()
        # Update the filtered list to reflect the changes
        self.refresh_file_list()

    def unlink_all(self):
        """Slot to handle the unreference all button click."""
        self.import_references()
        # Update the filtered list to reflect the changes
        self.refresh_file_list()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from uitk import Switchboard

    parent = CoreUtils.get_main_window()
    ui_file = os.path.join(os.path.dirname(__file__), "reference_manager.ui")
    sb = Switchboard(parent, ui_location=ui_file, slot_location=ReferenceManagerSlots)

    sb.current_ui.set_attributes(WA_TranslucentBackground=True)
    sb.current_ui.set_flags(FramelessWindowHint=True, WindowStaysOnTopHint=True)
    sb.current_ui.set_style(theme="dark", style_class="translucentBgWithBorder")
    sb.current_ui.header.configureButtons(minimize_button=True, hide_button=True)
    sb.current_ui.show(pos="screen", app_exec=True)
# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
