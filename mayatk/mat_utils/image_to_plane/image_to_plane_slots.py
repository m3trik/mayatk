# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Image to Plane UI.

Provides ``ImageToPlaneSlots`` — a standalone window for batch-creating
textured polygon planes from image files in Maya.
"""
import os

try:
    import maya.cmds as cmds
except ImportError:
    pass

import mayatk as mtk
from uitk.widgets.mixins.tooltip_mixin import fmt


class ImageToPlaneSlots:
    """Switchboard slots for the Image to Plane UI.

    Layout
    ------
    - **Header**: Title bar.
    - **Images**: Browse / file list / clear.
    - **Settings**: Material type, suffix, plane height.
    - **Create**: Main action button.
    - **Manage**: Remove selected planes.
    - **Footer**: Status messages.
    """

    IMAGE_FILTER = "Image Files (*.png *.jpg *.jpeg *.tga *.bmp *.tif *.tiff *.exr *.hdr);;All Files (*.*)"

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.image_to_plane

        # Wire plain QPushButton widgets
        self.ui.b000.clicked.connect(self._browse_images)
        self.ui.b001.clicked.connect(self._create_planes)
        self.ui.b002.clicked.connect(self._remove_selected)
        self.ui.b004.clicked.connect(self._clear_list)

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def header_init(self, widget):
        """Configure header menu."""
        widget.menu.add(
            "QCheckBox",
            setText="Group Result",
            setObjectName="chk_group_result",
            setChecked=False,
            setToolTip="Parent all created planes under a single group node.",
        )

        widget.menu.add("Separator", setTitle="About")
        widget.menu.add(
            "QPushButton",
            setText="Instructions",
            setObjectName="btn_instructions",
            setToolTip=fmt(
                title="Image to Plane",
                body="Creates textured polygon planes from images.",
                steps=[
                    "Press <b>Browse…</b> to select one or more image files.",
                    "Choose material type (Stingray PBS / Standard Surface).",
                    "Set the material suffix (default: _MAT). Use the option button to switch to prefix mode.",
                    "Set the plane height in scene units.",
                    "Press <b>Create Planes</b> to generate textured planes.",
                    "Use <b>Remove Selected</b> to delete planes and their materials.",
                ],
            ),
        )

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def txt_suffix_init(self, widget):
        """Add a prefix/suffix toggle to the affix field's option menu."""
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Use as Prefix",
            setObjectName="chk_use_as_prefix",
            setChecked=False,
            setToolTip=(
                "If checked, the value is prepended to the image name (prefix mode).\n"
                "Otherwise it is appended (suffix mode)."
            ),
        )
        widget.option_box.menu.chk_use_as_prefix.toggled.connect(
            lambda checked, w=widget: self._on_affix_mode_toggled(w, checked)
        )
        self._apply_affix_placeholder(widget, prefix_mode=False)

    def _on_affix_mode_toggled(self, widget, prefix_mode):
        text = widget.text()
        if prefix_mode and text == "_MAT":
            widget.setText("MAT_")
        elif not prefix_mode and text == "MAT_":
            widget.setText("_MAT")
        self._apply_affix_placeholder(widget, prefix_mode=prefix_mode)

    @staticmethod
    def _apply_affix_placeholder(widget, prefix_mode):
        if prefix_mode:
            widget.setPlaceholderText("Material Prefix")
            widget.setToolTip(
                'Prefix prepended to the image name for material naming.\n'
                'Example: image "brick" with prefix "MAT_" → material "MAT_brick".'
            )
        else:
            widget.setPlaceholderText("Material Suffix")
            widget.setToolTip(
                'Suffix appended to the image name for material naming.\n'
                'Example: image "brick" with suffix "_MAT" → material "brick_MAT".'
            )

    # ------------------------------------------------------------------
    # Images
    # ------------------------------------------------------------------

    def _browse_images(self):
        """Open a file dialog and populate the file list."""
        from qtpy.QtWidgets import QFileDialog

        paths, _ = QFileDialog.getOpenFileNames(
            self.ui,
            "Select Images",
            "",
            self.IMAGE_FILTER,
        )
        if not paths:
            return

        for path in paths:
            # Avoid duplicates
            existing = [
                self.ui.lst_files.item(i).text()
                for i in range(self.ui.lst_files.count())
            ]
            if path not in existing:
                self.ui.lst_files.addItem(path)

        self.ui.footer.setText(f"{self.ui.lst_files.count()} image(s) queued.")

    def _clear_list(self):
        """Clear the file list."""
        self.ui.lst_files.clear()
        self.ui.footer.setText("File list cleared.")

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    @mtk.CoreUtils.undoable
    def _create_planes(self):
        """Create textured planes from the queued images."""
        count = self.ui.lst_files.count()
        if count == 0:
            self.ui.footer.setText("No images queued. Use Browse to add files.")
            return

        image_paths = [self.ui.lst_files.item(i).text() for i in range(count)]

        mat_type = (
            "stingray" if self.ui.cmb_mat_type.currentIndex() == 0 else "standard"
        )
        affix_text = self.ui.txt_suffix.text()
        prefix_mode = self.ui.txt_suffix.option_box.menu.chk_use_as_prefix.isChecked()
        if prefix_mode:
            prefix = affix_text or "MAT_"
            suffix = ""
        else:
            prefix = ""
            suffix = affix_text or "_MAT"
        plane_height = self.ui.spn_height.value()

        group = self.ui.header.menu.chk_group_result.isChecked()

        try:
            results = mtk.ImageToPlane.create(
                image_paths,
                mat_type=mat_type,
                suffix=suffix,
                prefix=prefix,
                plane_height=plane_height,
                group=group,
            )
        except Exception as e:
            self.ui.footer.setText(f"Error: {e}")
            return

        names = list(results.keys())
        label = ", ".join(names[:5])
        if len(names) > 5:
            label += f" … (+{len(names) - 5} more)"

        self.ui.footer.setText(f"Created {len(results)} plane(s): {label}")

        # Select the group (if created) or the individual planes
        if group and "__group__" in results:
            cmds.select(results["__group__"], replace=True)
        else:
            cmds.select(list(results.values()), replace=True)

    # ------------------------------------------------------------------
    # Manage
    # ------------------------------------------------------------------

    @mtk.CoreUtils.undoable
    def _remove_selected(self):
        """Remove selected planes and their associated materials."""
        objects = cmds.ls(selection=True) or []
        if not objects:
            self.ui.footer.setText("Select planes to remove.")
            return

        try:
            removed = mtk.ImageToPlane.remove(objects)
        except Exception as e:
            self.ui.footer.setText(f"Error: {e}")
            return

        self.ui.footer.setText(f"Removed {removed} plane(s).")
