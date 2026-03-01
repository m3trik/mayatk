# !/usr/bin/python
# coding=utf-8
"""Switchboard slots for the Image to Plane UI.

Provides ``ImageToPlaneSlots`` — a standalone window for batch-creating
textured polygon planes from image files in Maya.
"""
import os

try:
    import pymel.core as pm
except ImportError:
    pass

import mayatk as mtk


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
        widget.config_buttons("menu", "pin")
        widget.menu.setTitle("Image to Plane:")

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
            setToolTip=(
                "Image to Plane — Creates textured polygon planes from images.\n\n"
                "Workflow:\n"
                "  1. Press 'Browse…' to select one or more image files.\n"
                "  2. Choose material type (Stingray PBS / Standard Surface).\n"
                "  3. Set the material suffix (default: _MAT).\n"
                "  4. Set the plane height in scene units.\n"
                "  5. Press 'Create Planes' to generate textured planes.\n"
                "  6. Use 'Remove Selected' to delete planes and their materials."
            ),
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
        suffix = self.ui.txt_suffix.text() or "_MAT"
        plane_height = self.ui.spn_height.value()

        group = self.ui.header.menu.chk_group_result.isChecked()

        try:
            results = mtk.ImageToPlane.create(
                image_paths,
                mat_type=mat_type,
                suffix=suffix,
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
            pm.select(results["__group__"], replace=True)
        else:
            pm.select(list(results.values()), replace=True)

    # ------------------------------------------------------------------
    # Manage
    # ------------------------------------------------------------------

    @mtk.CoreUtils.undoable
    def _remove_selected(self):
        """Remove selected planes and their associated materials."""
        objects = pm.selected()
        if not objects:
            self.ui.footer.setText("Select planes to remove.")
            return

        try:
            removed = mtk.ImageToPlane.remove(objects)
        except Exception as e:
            self.ui.footer.setText(f"Error: {e}")
            return

        self.ui.footer.setText(f"Removed {removed} plane(s).")
