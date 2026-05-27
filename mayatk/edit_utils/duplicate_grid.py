# !/usr/bin/python
# coding=utf-8
from __future__ import annotations

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)
from typing import List, Tuple, Union, Optional
import pythontk as ptk
from uitk.widgets.mixins.tooltip_mixin import fmt

from mayatk.display_utils._display_utils import DisplayUtils
from mayatk.core_utils.preview import Preview


class DuplicateGrid(ptk.LoggingMixin):
    @classmethod
    def duplicate_grid(
        cls,
        objects: List[str],
        dimensions: Tuple[int, int, int],
        spacing: float = 0,
        instance: bool = True,
        group: bool = True,
    ) -> Union[str, List[str]]:
        """Duplicate objects in a grid pattern.

        Parameters:
            objects (List[str]): List of objects to duplicate.
            dimensions (Tuple[int, int, int]): Number of copies in x, y, z.
            spacing (float): Extra spacing between copies (added to bounding box).
            instance (bool): Whether to instance the duplicates.
            group (bool): Whether to group the result.

        Returns:
            Union[str, List[str]]: The container group or list of duplicates.
        """
        duplicates = []
        x_count, y_count, z_count = dimensions
        cls.logger.info(f"Duplicating grid: {dimensions}, spacing: {spacing}")

        if not objects:
            return []

        # A zero dimension produces an empty volume_group, and cmds.ungroup
        # rejects leaf-level transforms — bail before any scene mutation.
        if not (x_count and y_count and z_count):
            return []

        # Create a temporary group for the originals to calculate bounding box and duplicate easily
        original_group = cmds.group(em=True, name="temp_original_group")
        for obj in objects:
            cmds.parent(obj, original_group)

        # Calculate offsets based on original position and bounding box
        original_pos = cmds.xform(original_group, query=True, translation=True)
        orig_x, orig_y, orig_z = original_pos

        bbox = cmds.exactWorldBoundingBox(original_group)
        base_spacing_x = bbox[3] - bbox[0]
        base_spacing_y = bbox[4] - bbox[1]
        base_spacing_z = bbox[5] - bbox[2]

        spacing_x = base_spacing_x + (spacing if spacing else 0)
        spacing_y = base_spacing_y + (spacing if spacing else 0)
        spacing_z = base_spacing_z + (spacing if spacing else 0)

        final_group = (
            cmds.group(em=True, name="final_duplicated_group") if group else None
        )

        try:
            # --- Optimized Hierarchical Duplication ---
            # Instead of iterating O(X*Y*Z) times, we iterate O(X+Y+Z) times.
            # We build a Row (X), then duplicate the Row to build a Plane (Y),
            # then duplicate the Plane to build a Volume (Z).

            dir_x = 1 if x_count >= 0 else -1
            dir_y = 1 if y_count >= 0 else -1
            dir_z = 1 if z_count >= 0 else -1

            step_x = spacing_x * dir_x
            step_y = spacing_y * dir_y
            step_z = spacing_z * dir_z

            # 1. Build X-Row
            row_group = cmds.group(em=True, name="temp_row_group")
            for i in range(abs(x_count)):
                cell_dup = cmds.duplicate(
                    original_group,
                    instanceLeaf=instance,
                )[0]
                cmds.xform(
                    cell_dup, t=(orig_x + (step_x * i), orig_y, orig_z), ws=True
                )
                cell_dup = cmds.parent(cell_dup, row_group)[0]

            # 2. Build Y-Planes
            plane_group = cmds.group(em=True, name="temp_plane_group")
            for j in range(abs(y_count)):
                row_dup = cmds.duplicate(
                    row_group,
                    instanceLeaf=instance,
                )[0]
                cmds.xform(row_dup, t=(0, step_y * j, 0), r=True)
                row_dup = cmds.parent(row_dup, plane_group)[0]

            cmds.delete(row_group)

            # 3. Build Z-Volume
            volume_group = cmds.group(em=True, name="temp_volume_group")
            for k in range(abs(z_count)):
                plane_dup = cmds.duplicate(
                    plane_group,
                    instanceLeaf=instance,
                )[0]
                cmds.xform(plane_dup, t=(0, 0, step_z * k), r=True)
                plane_dup = cmds.parent(plane_dup, volume_group)[0]

            cmds.delete(plane_group)

            # 4. Flatten / Ungroup
            # current hierarchy: volume_group -> [planes] -> [rows] -> [cells] -> [objects]
            # cmds.ungroup returns None, so we capture children first and
            # reparent them to world — cmds.parent returns the new full paths.

            def _flatten(grp):
                """Move grp's children to world and delete grp. Return new paths."""
                children = cmds.listRelatives(grp, children=True, fullPath=True) or []
                if not children:
                    if cmds.objExists(grp):
                        cmds.delete(grp)
                    return []
                new_paths = cmds.parent(children, world=True) or []
                if not isinstance(new_paths, list):
                    new_paths = [new_paths]
                # cmds.parent returns short names — re-resolve to full paths.
                new_paths = [cmds.ls(n, long=True)[0] for n in new_paths if cmds.objExists(n)]
                if cmds.objExists(grp):
                    cmds.delete(grp)
                return new_paths

            planes = _flatten(volume_group)

            rows = []
            for p in planes:
                rows.extend(_flatten(p))

            cells = []
            for r in rows:
                cells.extend(_flatten(r))

            final_objects = []
            for c in cells:
                final_objects.extend(_flatten(c))

            # 5. Finalize
            if group:
                if final_group is None:
                    final_group = cmds.group(em=True, name="final_duplicated_group")
                if final_objects:
                    final_objects = cmds.parent(final_objects, final_group) or []

            duplicates.extend(final_objects)
            for d in final_objects:
                DisplayUtils.add_to_isolation_set(d)

        finally:
            # Always restore originals
            if cmds.objExists(original_group):
                cmds.ungroup(original_group)

        return final_group if group else duplicates


class DuplicateGridSlots(ptk.LoggingMixin):
    def __init__(self, switchboard, log_level="INFO"):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.duplicate_grid

        # Initialize Logger
        self.logger.setLevel(log_level)
        self.logger.set_log_prefix("[Duplicate Grid] ")

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            message_func=self.sb.message_box,
        )

        self.sb.connect_multi(
            self.ui,
            "s000-3",
            "valueChanged",
            self.preview.refresh,
        )

        self.sb.connect_multi(
            self.ui,
            "chk001-2",
            "stateChanged",
            self.preview.refresh,
        )

    def header_init(self, widget):
        """Configure header help text."""
        widget.set_help_text(
            fmt(
                title="Duplicate Grid",
                body="Duplicate selected objects into a 3D grid layout.",
                steps=[
                    "Select one or more transforms.",
                    "Set per-axis counts <b>X</b> / <b>Y</b> / <b>Z</b> and a "
                    "uniform <b>Spacing</b>.",
                    "Toggle <b>Preview</b> to iterate, or press <b>Duplicate</b> "
                    "to commit.",
                ],
                sections=[
                    ("Options", [
                        "<b>Instance</b> — link copies to the source (cheaper, "
                        "edits propagate).",
                        "<b>Group</b> — parent all copies under a new group node.",
                    ]),
                ],
            )
        )

    def b001(self):
        """Reset to Defaults: Resets all UI widgets to their default values."""
        self.ui.state.reset_all()

    def perform_operation(self, objects, contract):
        dimensions = (
            self.ui.s000.value(),
            self.ui.s001.value(),
            self.ui.s002.value(),
        )
        spacing = self.ui.s003.value()
        instance = self.ui.chk001.isChecked()
        group = self.ui.chk002.isChecked()

        self.copies = DuplicateGrid.duplicate_grid(
            objects,
            dimensions,
            spacing,
            instance,
            group,
        )


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("duplicate_grid", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
