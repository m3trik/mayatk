# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
from mayatk.display_utils._display_utils import DisplayUtils
from mayatk.core_utils.preview import Preview


class DuplicateGrid:
    @staticmethod
    def duplicate_grid(objects, dimensions, spacing=0, instance=True, group=True):
        duplicates = []
        x_count, y_count, z_count = dimensions

        original_group = pm.group(em=True, name="temp_original_group")
        for obj in objects:
            pm.parent(obj, original_group)

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

        for z in range(abs(z_count)):
            for y in range(abs(y_count)):
                for x in range(abs(x_count)):
                    duplicate_group = pm.duplicate(
                        original_group,
                        instanceLeaf=instance,
                        name=f"{original_group}_{x}_{y}_{z}",
                    )[0]

                    pm.setAttr(
                        duplicate_group.translateX,
                        x * spacing_x * (1 if x_count >= 0 else -1) + orig_x,
                    )
                    pm.setAttr(
                        duplicate_group.translateY,
                        y * spacing_y * (1 if y_count >= 0 else -1) + orig_y,
                    )
                    pm.setAttr(
                        duplicate_group.translateZ,
                        z * spacing_z * (1 if z_count >= 0 else -1) + orig_z,
                    )

                    if group:
                        pm.parent(duplicate_group, final_group)

                    ungrouped_dups = pm.ungroup(duplicate_group)
                    if ungrouped_dups:
                        duplicates.extend(ungrouped_dups)
                        DisplayUtils.add_to_isolation_set(ungrouped_dups)
                    else:
                        duplicates.append(duplicate_group)
                        # Add the group itself if there are no ungrouped duplicates
                        DisplayUtils.add_to_isolation_set(duplicate_group)

        pm.ungroup(original_group)

        return final_group if group else duplicates


class DuplicateGridSlots:
    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.duplicate_grid

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
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("duplicate_grid", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
