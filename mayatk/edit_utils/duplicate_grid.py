# !/usr/bin/python
# coding=utf-8
import pymel.core as pm
from typing import List, Tuple, Union, Optional
import pythontk as ptk

from mayatk.display_utils._display_utils import DisplayUtils
from mayatk.core_utils.preview import Preview


class DuplicateGrid(ptk.LoggingMixin):
    @classmethod
    def duplicate_grid(
        cls,
        objects: List[pm.PyNode],
        dimensions: Tuple[int, int, int],
        spacing: float = 0,
        instance: bool = True,
        group: bool = True,
    ) -> Union[pm.PyNode, List[pm.PyNode]]:
        """Duplicate objects in a grid pattern.

        Parameters:
            objects (List[pm.PyNode]): List of objects to duplicate.
            dimensions (Tuple[int, int, int]): Number of copies in x, y, z.
            spacing (float): Extra spacing between copies (added to bounding box).
            instance (bool): Whether to instance the duplicates.
            group (bool): Whether to group the result.

        Returns:
            Union[pm.PyNode, List[pm.PyNode]]: The container group or list of duplicates.
        """
        duplicates = []
        x_count, y_count, z_count = dimensions
        cls.logger.info(f"Duplicating grid: {dimensions}, spacing: {spacing}")

        if not objects:
            return []

        # Create a temporary group for the originals to calculate bounding box and duplicate easily
        original_group = pm.group(em=True, name="temp_original_group")
        for obj in objects:
            pm.parent(obj, original_group)

        # Calculate offsets based on original position and bounding box
        original_pos = pm.xform(original_group, query=True, translation=True)
        orig_x, orig_y, orig_z = original_pos

        bbox = pm.exactWorldBoundingBox(original_group)
        base_spacing_x = bbox[3] - bbox[0]
        base_spacing_y = bbox[4] - bbox[1]
        base_spacing_z = bbox[5] - bbox[2]

        spacing_x = base_spacing_x + (spacing if spacing else 0)
        spacing_y = base_spacing_y + (spacing if spacing else 0)
        spacing_z = base_spacing_z + (spacing if spacing else 0)

        final_group = (
            pm.group(em=True, name="final_duplicated_group") if group else None
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
            # Create a container for the row
            row_group = pm.group(em=True, name="temp_row_group")
            # We duplicate the original (single cell) abs(x) times
            for i in range(abs(x_count)):
                # Duplicate the source cell (original_group)
                cell_dup = pm.duplicate(
                    original_group,
                    instanceLeaf=instance,
                )[0]
                # Move relative to the row's origin.
                # Note: original_group is at 0,0,0 inside the temp group,
                # so duplications are at 0,0,0. We just translate by step * i.
                pm.xform(cell_dup, t=(orig_x + (step_x * i), orig_y, orig_z), ws=True)
                pm.parent(cell_dup, row_group)

            # 2. Build Y-Planes
            # Create a container for the plane
            plane_group = pm.group(em=True, name="temp_plane_group")
            # Duplicate the Row abs(y) times
            for j in range(abs(y_count)):
                row_dup = pm.duplicate(
                    row_group,
                    instanceLeaf=instance,
                )[0]
                # Shift the entire row in Y
                # row_group itself was at 0,0,0 world (default create).
                # But it contains absolute positioned children.
                # Moving row_dup shifts its children.
                pm.xform(row_dup, t=(0, step_y * j, 0), r=True)
                pm.parent(row_dup, plane_group)

            # Cleanup row template
            pm.delete(row_group)

            # 3. Build Z-Volume
            # Create a container for the volume
            volume_group = pm.group(em=True, name="temp_volume_group")
            for k in range(abs(z_count)):
                plane_dup = pm.duplicate(
                    plane_group,
                    instanceLeaf=instance,
                )[0]
                # Shift plane in Z
                pm.xform(plane_dup, t=(0, 0, step_z * k), r=True)
                pm.parent(plane_dup, volume_group)

            # Cleanup plane template
            pm.delete(plane_group)

            # 4. Flatten / Ungroup
            # current hierarchy: volume_group -> [planes] -> [rows] -> [cells] -> [objects]
            # We want: [objects]
            # or if group=True: final_group -> [objects]

            # Move all high-level items (planes) out of volume group
            # Actually, simplest is to ungroup sequentially.

            # Ungroup Volume -> List of Planes
            planes = pm.ungroup(volume_group)
            if not isinstance(planes, list):
                planes = [planes]

            # -----------------------------------------------------------
            # FIX: Ungrouping multiple groups at once works, but if 'ungroup'
            # is called with a list, PyMEL passes them as arguments.
            # If the list is empty, it fails.
            # Also, 'pm.ungroup' might not iterate efficiently over the list
            # if passed as `*planes`.
            # Let's ensure we are passing valid objects.
            # -----------------------------------------------------------

            # Ungroup Planes -> List of Rows
            if planes:
                rows_raw = []
                for p in planes:
                    # Check if group has children before ungrouping to avoid error
                    if pm.listRelatives(p, children=True):
                        res = pm.ungroup(p)
                        if isinstance(res, list):
                            rows_raw.extend(res)
                        else:
                            rows_raw.append(res)
                    else:
                        # If empty group, just delete it? Or keep?
                        # It shouldn't be empty if logic is correct.
                        pm.delete(p)
                rows = rows_raw
            else:
                rows = []

            # Ungroup Rows -> List of Cells (copies of original_group)
            if rows:
                cells_raw = []
                for r in rows:
                    if pm.listRelatives(r, children=True):
                        res = pm.ungroup(r)
                        if isinstance(res, list):
                            cells_raw.extend(res)
                        else:
                            cells_raw.append(res)
                    else:
                        pm.delete(r)
                cells = cells_raw
            else:
                cells = []

            # Ungroup Cells -> List of Objects
            if cells:
                final_objects_raw = []
                for c in cells:
                    if pm.listRelatives(c, children=True):
                        res = pm.ungroup(c)
                        if isinstance(res, list):
                            final_objects_raw.extend(res)
                        else:
                            final_objects_raw.append(res)
                    else:
                        pm.delete(c)
                final_objects = final_objects_raw
            else:
                final_objects = []

            # 5. Finalize
            if group:
                if final_group is None:  # Should be created earlier if group=True
                    final_group = pm.group(em=True, name="final_duplicated_group")
                pm.parent(final_objects, final_group)

            duplicates.extend(final_objects)
            for d in final_objects:
                DisplayUtils.add_to_isolation_set(d)

        finally:
            # Always restore originals
            if pm.objExists(original_group):
                pm.ungroup(original_group)

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

    def b001(self):
        """Reset to Defaults: Resets all UI widgets to their default values."""
        self.ui.state.reset_all()

    def perform_operation(self, objects):
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
    from mayatk.ui_utils.mayatk_ui_manager import UiManager

    ui = UiManager.instance().get("duplicate_grid", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
