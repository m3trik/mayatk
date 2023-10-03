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

    @property
    def workspace_root(self):
        return pm.workspace(q=True, rd=True)

    @workspace_root.setter
    def workspace_root(self, value):
        pm.workspace(dir=value)

    def _get_workspace_files(self, search_root, filter_text=""):
        inc_files = ["*.ma", "*.mb"]
        workspace_files = ptk.get_dir_contents(
            search_root,
            returned_type="filepath",
            inc_files=inc_files,
            num_threads=4,
            recursive=True,
            group_by_type=True,
        ).get("filepath", [])
        if filter_text:
            workspace_files = ptk.filter_list(workspace_files, inc=[f"*{filter_text}*"])
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
        self.ui.list000.setSelectionMode(
            self.sb.QtWidgets.QAbstractItemView.MultiSelection
        )

        self.update_filtered_list()

    def set_custom_root(self):
        new_dir = self.ui.txt000.text()
        if os.path.isdir(new_dir):
            self.custom_root = new_dir
            self.update_filtered_list()

    def _populate_list(self, file_list):
        self.ui.list000.clear()
        self.ui.list000.addItems(file_list)

    def update_filtered_list(self):
        filter_text = self.ui.txt001.text().strip()
        search_root = getattr(self, "custom_root", self.workspace_root)
        file_list = [
            os.path.basename(fp)
            for fp in self._get_workspace_files(search_root, filter_text)
        ]
        self._populate_list(file_list)

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
