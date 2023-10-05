# !/usr/bin/python
# coding=utf-8
import os

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils import CoreUtils


class ReferenceManager:
    def __init__(self):
        self.references = {}
        self._recursive_search = True
        self._filter_text = ""

    @property
    def workspace_root(self):
        return pm.workspace(q=True, rd=True)

    @workspace_root.setter
    def workspace_root(self, value):
        pm.workspace(dir=value)

    @property
    def recursive_search(self):
        return self._recursive_search

    @recursive_search.setter
    def recursive_search(self, value):
        self._recursive_search = value

    @property
    def filter_text(self):
        return self._filter_text

    @filter_text.setter
    def filter_text(self, value):
        self._filter_text = value

    def _get_workspace_files(self, search_root, extensions=["*.ma", "*.mb"]):
        workspace_files = ptk.get_dir_contents(
            search_root,
            returned_type="filepath",
            inc_files=extensions,
            num_threads=4,
            recursive=self._recursive_search,
        )
        if self._filter_text:
            workspace_files = ptk.filter_list(
                workspace_files, inc=[f"*{self._filter_text}*"]
            )

        return workspace_files

    def add_reference(self, namespace, file_path):
        # Validate and create reference
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File {file_path} does not exist.")
        ref_node = pm.createReference(file_path, namespace=namespace)
        self.references[
            namespace
        ] = ref_node  # Store the reference node, not just the path

    def remove_reference(self, namespace):
        if namespace in self.references:
            self.references[namespace].remove()  # Remove the reference in Maya
            del self.references[namespace]  # Remove from internal dictionary

    def resolve_file_path(self, selected_file, search_root):
        workspace_files = self._get_workspace_files(search_root)
        return next((fp for fp in workspace_files if selected_file in fp), None)


class ReferenceManagerSlots(ReferenceManager):
    def __init__(self):
        super().__init__()
        self.sb = self.switchboard()
        self.ui = self.sb.reference_manager

        self.ui.txt000.setText(self.workspace_root)
        self.ui.txt000.textChanged.connect(self.set_custom_root)
        self.ui.txt001.textChanged.connect(self.update_filtered_list)

        self.ui.list000.doubleClicked.connect(self.add_reference_from_list)
        self.ui.list000.itemClicked.connect(self.handle_item_click)
        self.ui.list000.setSelectionMode(self.sb.QAbstractItemView.MultiSelection)

        self.update_filtered_list()

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
        self.ui.b000.clicked.connect(self.browse_for_root_directory)
        self.ui.b001.clicked.connect(self.set_root_to_workspace)
        self.ui.chk000.toggled.connect(self.toggle_recursive_search)

    def toggle_recursive_search(self, checked):
        self.recursive_search = checked
        self.update_filtered_list()

    def set_custom_root(self):
        new_dir = self.ui.txt000.text()
        if os.path.isdir(new_dir):
            self.custom_root = new_dir
            self.ui.txt000.setToolTip(new_dir)
            self.update_filtered_list()

    def update_filtered_list(self):
        self.filter_text = self.ui.txt001.text().strip()
        search_root = getattr(self, "custom_root", self.workspace_root)
        file_list = [
            os.path.basename(fp) for fp in self._get_workspace_files(search_root)
        ]
        self._populate_list(file_list)

    def browse_for_root_directory(self):
        selected_directory = self.sb.dir_dialog("Select a root directory")
        if selected_directory:
            self.ui.txt000.setText(selected_directory)

    def set_root_to_workspace(self):
        self.ui.txt000.setText(self.workspace_root)

    def _populate_list(self, file_list):
        self.ui.list000.clear()
        self.ui.list000.addItems(file_list)

    def handle_item_click(self, item):
        selected_file = item.text()
        search_root = getattr(self, "custom_root", self.workspace_root)
        file_path = self.resolve_file_path(selected_file, search_root)
        if file_path:
            namespace = os.path.splitext(selected_file)[0]
            if item.isSelected():
                try:
                    self.add_reference(namespace, file_path)
                except ValueError:
                    print(f"Namespace {namespace} already in use.")
            else:
                try:
                    self.remove_reference(
                        namespace
                    )  # This now calls the updated remove_reference
                except KeyError:
                    print(f"No reference found for namespace {namespace}.")

    def add_reference_from_list(self, index):
        selected_file = self.ui.list000.itemFromIndex(index).text()
        search_root = getattr(self, "custom_root", self.workspace_root)
        file_path = self.resolve_file_path(selected_file, search_root)
        if file_path:
            namespace = os.path.splitext(selected_file)[0]
            self.add_reference(namespace, file_path)


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
