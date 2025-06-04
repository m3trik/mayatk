# !/usr/bin/python
# coding=utf-8
from typing import Optional, Dict, Any, List

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# From this package:
from mayatk.edit_utils import EditUtils
from mayatk.anim_utils import AnimUtils
from mayatk.env_utils import EnvUtils
from mayatk.mat_utils import MatUtils
from mayatk.xform_utils import XformUtils
from mayatk.node_utils import NodeUtils
from mayatk.env_utils.scene_exporter import task_factory


class _TaskDataMixin:
    """ """

    @property
    def _has_keyframes(self) -> bool:
        """Check if the current objects have keyframes."""
        if hasattr(self, "_key_times"):
            return bool(self._key_times)
        return bool(self._get_all_keyframes())

    def _get_all_keyframes(self) -> List[float]:
        """Return a sorted list of all unique keyframe times for the specified objects."""
        if not self.objects:
            return []
        key_times = set()
        for obj in pm.ls(self.objects):
            times = pm.keyframe(obj, query=True, timeChange=True)
            if times:
                key_times.update(times)

        if key_times:
            self._key_times = key_times
        return sorted(key_times)

    def _get_all_materials(self) -> List["pm.nt.ShadingNode"]:
        """Return a list of all materials assigned to the specified objects."""
        mats = MatUtils.filter_materials_by_objects(self.objects)
        return mats


class _TaskActionsMixin(_TaskDataMixin):
    """ """

    def set_workspace(self):
        """Manage temporary workspace change."""
        original_workspace = pm.workspace(query=True, rootDirectory=True)
        new_workspace = EnvUtils.find_workspace_using_path()
        if new_workspace and new_workspace != original_workspace:
            pm.workspace(new_workspace, openWorkspace=True)
        return original_workspace

    def revert_workspace(self, original_workspace):
        """Revert to the original workspace."""
        pm.workspace(original_workspace, openWorkspace=True)

    def set_linear_unit(self, linear_unit):
        """Manage temporary linear unit change."""
        original_linear_unit = pm.currentUnit(query=True, linear=True)
        if linear_unit:
            pm.currentUnit(linear=linear_unit)
        # Ensure that the original value is returned
        self.logger.debug(f"Original linear unit: {original_linear_unit}")
        return original_linear_unit

    def revert_linear_unit(self, original_linear_unit):
        """Revert to the original linear unit."""
        pm.currentUnit(linear=original_linear_unit)

    def convert_to_relative_paths(self):
        """Convert absolute material paths to relative paths."""
        self.logger.debug("Converting absolute paths to relative")
        materials = self._get_all_materials()
        MatUtils.remap_texture_paths(materials)
        self.logger.debug("Path conversion completed.")

    def reassign_duplicate_materials(self):
        """Reassign duplicate materials in the scene."""
        self.logger.debug("Reassigning duplicate materials")
        materials = self._get_all_materials()
        MatUtils.reassign_duplicate_materials(materials)
        self.logger.debug("Reassignment completed.")

    def delete_unused_materials(self):
        """Delete unused materials from the scene."""
        self.logger.debug("Deleting unused materials")
        pm.mel.hyperShadePanelMenuCommand("hyperShadePanel1", "deleteUnusedNodes")
        self.logger.debug("Unused materials deleted.")

    def delete_env_nodes(self) -> None:
        """Delete environment file nodes based on filtered texture path patterns."""
        env_keywords = ["diffuse_cube", "specular_cube", "ibl_brdf_lut"]
        file_nodes = pm.ls(type="file")

        file_nodes = [
            node
            for node in file_nodes
            if node.hasAttr("fileTextureName")
            and any(
                keyword in node.fileTextureName.get().lower()
                for keyword in env_keywords
            )
        ]
        if file_nodes:
            pm.delete(file_nodes)
            self.logger.info(f"Deleted {len(file_nodes)} environment file nodes.")
        else:
            self.logger.info("No environment file nodes found.")

    def optimize_keys(self):
        """Optimize baked animation keys."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping optimization.")
            return

        self.logger.info("Optimizing baked animation keys...")
        AnimUtils.optimize_keys(self.objects, recursive=True, quiet=True)
        self.logger.info("Optimization completed.")

    def set_bake_animation_range(self):
        """Set the animation export range to the first and last keyframes of the specified objects if baking is enabled."""
        all_keyframes = self._get_all_keyframes()
        if not all_keyframes:
            self.logger.debug("No keyframes found. Skipping frame range setting.")
            return

        if not pm.mel.eval("FBXExportBakeComplexAnimation -q"):
            self.logger.info(
                "Baking complex animation is disabled. Skipping frame range setting."
            )
            return

        first_key, last_key = all_keyframes[0], all_keyframes[-1]
        pm.mel.eval(f"FBXExportBakeComplexStart -v {int(first_key)}")
        pm.mel.eval(f"FBXExportBakeComplexEnd -v {int(last_key)}")

        self.logger.info(
            f"Set animation range to start: {int(first_key)}, end: {int(last_key)}"
        )

    def tie_all_keyframes(self):
        """Use AnimUtils to tie all keyframes for the specified objects."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping tie operation.")
            return

        self.logger.info("Tying keyframes for all objects.")
        AnimUtils.tie_keyframes(self.objects, absolute=True)
        self.logger.info("Keyframes have been tied.")


class _TaskChecksMixin(_TaskDataMixin):
    """ """

    def check_root_default_transforms(self) -> tuple:
        """Check if all root group nodes have default transforms."""
        log_messages = []
        box_logged = False
        root_nodes = pm.ls(self.objects, assemblies=True)
        tolerance = 1e-5
        has_non_default_transforms = False

        for node in root_nodes:
            if not NodeUtils.is_group(node):
                continue

            translate = node.translate.get()
            rotate = node.rotate.get()
            scale = node.scale.get()

            if (
                not all(abs(val) < tolerance for val in translate)
                or not all(abs(val) < tolerance for val in rotate)
                or not all(abs(val - 1) < tolerance for val in scale)
            ):
                if not box_logged:
                    log_messages.append(
                        f"Root level group nodes found with non-default transforms:"
                    )
                    box_logged = True

                has_non_default_transforms = True
                log_messages.append(
                    f"Node: {node}, Translate: {translate}, Rotate: {rotate}, Scale: {scale}"
                )

        if has_non_default_transforms:
            return (
                False,
                log_messages,
            )  # Failed, log the nodes with non-default transforms

        return True, log_messages  # All checks passed, no non-default transforms

    def check_absolute_paths(self) -> tuple:
        """Check if any absolute material paths are present in the scene."""
        all_relative = True
        log_messages = []

        materials = self._get_all_materials()
        material_paths = MatUtils.collect_material_paths(
            materials,
            inc_mat_name=True,
            inc_path_type=True,
            nested_as_unit=True,
        )

        for mat, typ, pth in material_paths:
            if typ == "Absolute":
                all_relative = False
                log_messages.append(f"Absolute path - {mat.name()} - {pth}")

        return all_relative, log_messages

    def check_duplicate_materials(self) -> tuple:
        """Check if any duplicate materials are present in the scene."""
        log_messages = []

        materials = self._get_all_materials()
        duplicate_mapping = MatUtils.find_materials_with_duplicate_textures(materials)

        if duplicate_mapping:
            for original, duplicates in duplicate_mapping.items():
                for duplicate in duplicates:
                    log_messages.append(f"Duplicate: {duplicate} -> {original}")
            return False, log_messages  # Failed, log the duplicates

        return True, log_messages  # All checks passed, no duplicates found

    def check_referenced_objects(self) -> tuple:
        """Check if any referenced objects are present in the scene."""
        log_messages = []
        referenced_objects = pm.ls(self.objects, references=True)

        if referenced_objects:
            for ref in referenced_objects:
                log_messages.append(f"Referenced Object: {ref}")
            return False, log_messages  # Failed, log the referenced objects

        return True, log_messages  # All checks passed, no referenced objects found

    def check_framerate(self, target_framerate: Optional[str]) -> tuple:
        """Check if the scene's current framerate matches the target framerate."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping framerate check.")
            return True

        log_messages = []

        if target_framerate:
            current_time_unit = pm.currentUnit(query=True, time=True)
            if current_time_unit != target_framerate:
                log_messages.append(
                    f"Framerate mismatch: Current time unit is {current_time_unit}, expected {target_framerate}."
                )
                return False, log_messages  # Failed, log the mismatch

            log_messages.append(
                f"Framerate check passed: Scene framerate matches the target framerate ({target_framerate})."
            )

        return True, log_messages  # All checks passed

    def check_objects_below_floor(self) -> tuple:
        """Check if any object's geometry is below the floor plane (Y=0)."""
        log_messages = []
        has_objects_below = False

        objects_below_threshold = XformUtils.check_objects_against_plane(
            self.objects,
            plane_point=(0, 0, 0),
            plane_normal=(0, 1, 0),
            return_type="bool",
        )

        for obj, is_below in objects_below_threshold:
            if is_below:
                has_objects_below = True
                log_messages.append(f"Object: {obj} - Below Floor: {is_below}")

        if has_objects_below:
            return False, log_messages  # Failed, log objects below the floor threshold

        return True, log_messages  # All checks passed, no objects below the floor

    def check_overlapping_duplicates(self) -> tuple:
        """Check if there are any duplicate overlapping geometry objects in the current selection.

        Parameters:
            select (bool): Select any found duplicate objects.
            verbose (bool): Print found duplicates to the console.

        Returns:
            tuple: (status: bool, messages: list)
        """
        duplicates = EditUtils.get_overlapping_duplicates(objects=self.objects)
        if duplicates:
            messages = [f"Overlapping duplicate object: {obj}" for obj in duplicates]
            return False, messages  # Failed, duplicates found
        return True, []  # Passed, no duplicates

    def check_hidden_geometry(self) -> tuple:
        """Check if any geometry objects are hidden."""
        hidden_objects = [
            obj
            for obj in self.objects
            if NodeUtils.is_geometry(obj) and not obj.isVisible()
        ]
        if hidden_objects:
            return False, [f"Hidden geometry detected: {obj}" for obj in hidden_objects]
        return True, []

    def check_and_delete_visibility_keys(self) -> tuple:
        """Check if there are any visibility keys on the specified objects and delete them."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping visibility key check.")
            return True

        log_messages = []
        visibility_keys_found = False

        for obj in AnimUtils.filter_objects_with_keys(keys="visibility"):
            visibility_keys_found = True

            # Delete visibility keys
            pm.cutKey(obj, attribute="visibility")
            log_messages.append(f"Visibility keys deleted for object: {obj}")

        if visibility_keys_found:
            log_messages.append(
                "check_and_delete_visibility_keys passed - visibility keys deleted."
            )
            return True, log_messages  # All checks passed, visibility keys deleted

        log_messages.append(
            "check_and_delete_visibility_keys passed - no visibility keys found."
        )
        return True, log_messages  # No visibility keys found, but passed

    def check_untied_keyframes(self) -> tuple:
        """Check if there are any untied keyframes on the specified objects."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping untied keyframe check.")
            return True

        log_messages = []
        untied_keyframes_found = False

        for obj in self.objects:
            keyed_attrs = pm.keyframe(obj, query=True, name=True)
            if keyed_attrs:
                all_keyframes = pm.keyframe(obj, query=True, timeChange=True)
                if not all_keyframes:
                    continue

                start_frame, end_frame = min(all_keyframes), max(all_keyframes)

                for attr in keyed_attrs:
                    start_key = pm.keyframe(
                        attr, time=(start_frame,), query=True, timeChange=True
                    )
                    end_key = pm.keyframe(
                        attr, time=(end_frame,), query=True, timeChange=True
                    )

                    if not start_key or not end_key:
                        untied_keyframes_found = True
                        log_messages.append(
                            f"Untied keyframes found on attribute: {attr} on {obj}"
                        )

        if untied_keyframes_found:
            return False, log_messages  # Failed, log untied keyframes

        return True, log_messages  # All checks passed, no untied keyframes


class TaskManager(task_factory.TaskFactory, _TaskActionsMixin, _TaskChecksMixin):
    """Contains all task-related UI definitions for the Scene Exporter."""

    _log_level_options: Dict[str, Any] = {
        "Log Level: DEBUG": 10,
        "Log Level: INFO": 20,
        "Log Level: WARNING": 30,
        "Log Level: ERROR": 40,
    }

    _frame_rate_options: Dict[str, Any] = {
        f"Check Scene FPS: {v}": k
        for k, v in ptk.insert_into_dict(
            AnimUtils.FRAME_RATE_VALUES, "OFF", None
        ).items()
    }

    _scene_unit_options: Dict[str, Any] = {
        f"Set Linear Unit: {k}": v
        for k, v in ptk.insert_into_dict(
            EnvUtils.SCENE_UNIT_VALUES, "OFF", None
        ).items()
    }

    def __init__(self, logger):
        super().__init__(logger)

        self.logger = logger
        self.objects = None

    @property
    def definitions(self) -> Dict[str, Dict[str, Any]]:
        """Return the task definitions for the UI."""
        return {
            "export_visible_objects": {
                "widget_type": "QCheckBox",
                "setText": "Export All Visible Objects",
                "setToolTip": "Export all visible objects regardless of the current selection.\nIf unchecked, only the selected objects will be exported.",
                "setChecked": True,
            },
            "set_workspace": {
                "widget_type": "QCheckBox",
                "setText": "Auto Set Workspace",
                "setToolTip": "Determine the workspace directory from the scene path.",
                "setChecked": True,
            },
            "set_linear_unit": {
                "widget_type": "ComboBox",
                "setToolTip": "Linear unit to be used during export.",
                "add": self._scene_unit_options,
            },
            "check_framerate": {
                "widget_type": "ComboBox",
                "setToolTip": "Check the scene framerate against the target framerate.",
                "add": self._frame_rate_options,
            },
            "delete_env_nodes": {
                "widget_type": "QCheckBox",
                "setText": "Delete Environment Nodes",
                "setToolTip": "Delete environment file nodes.\nEnvironment nodes are defined as: 'diffuse_cube', 'specular_cube', 'ibl_brdf_lut'",
                "setChecked": False,
            },
            "delete_unused_materials": {
                "widget_type": "QCheckBox",
                "setText": "Delete Unused Materials",
                "setToolTip": "Delete unassigned material nodes.",
                "setChecked": True,
            },
            "reassign_duplicate_materials": {
                "widget_type": "QCheckBox",
                "setText": "Reassign Duplicate Materials",
                "setToolTip": "Reassign any duplicate materials to a single material.",
                "setChecked": True,
            },
            "check_duplicate_materials": {
                "widget_type": "QCheckBox",
                "setText": "Check For Duplicate Materials.",
                "setToolTip": "Check for duplicate materials.",
                "setChecked": True,
            },
            "check_hidden_geometry": {
                "widget_type": "QCheckBox",
                "setText": "Check For Hidden Geometry.",
                "setToolTip": "Check for hidden geometry that will be exported.",
                "setChecked": True,
            },
            "check_and_delete_visibility_keys": {
                "widget_type": "QCheckBox",
                "setText": "Delete Visibility Keys",
                "setToolTip": "Delete visibility keys from the exported objects.",
                "setChecked": True,
            },
            "optimize_keys": {
                "widget_type": "QCheckBox",
                "setText": "Optimize Keys",
                "setToolTip": "Optimize animation keys by removing redundant keys.",
                "setChecked": True,
            },
            "check_root_default_transforms": {
                "widget_type": "QCheckBox",
                "setText": "Check Root Default Transforms",
                "setToolTip": "Check for default transforms on root group nodes.\nTranslate, rotate, and scale should be (0, 0, 0) and (1, 1, 1) respectively.",
                "setChecked": True,
            },
            "check_referenced_objects": {
                "widget_type": "QCheckBox",
                "setText": "Check For Referenced Objects.",
                "setToolTip": "Check for referenced objects.",
                "setChecked": True,
            },
            "check_overlapping_duplicates": {
                "widget_type": "QCheckBox",
                "setText": "Check For Overlapping Duplicates",
                "setToolTip": "Check for overlapping duplicate geometry.",
                "setChecked": True,
            },
            "check_objects_below_floor": {
                "widget_type": "QCheckBox",
                "setText": "Check For Objects Below Floor.",
                "setToolTip": "Check for objects with a bounding box having a negative Y value.",
                "setChecked": True,
            },
            "convert_to_relative_paths": {
                "widget_type": "QCheckBox",
                "setText": "Convert To Relative Paths",
                "setToolTip": "Convert absolute paths to relative paths.",
                "setChecked": True,
            },
            "check_absolute_paths": {
                "widget_type": "QCheckBox",
                "setText": "Check For Absolute Paths.",
                "setToolTip": "Check for absolute paths.",
                "setChecked": True,
            },
            "tie_all_keyframes": {
                "widget_type": "QCheckBox",
                "setText": "Tie All Keyframes",
                "setToolTip": "Tie all keyframes on the specified objects.",
                "setChecked": False,
            },
            "check_untied_keyframes": {
                "widget_type": "QCheckBox",
                "setText": "Check For Untied Keyframes",
                "setToolTip": "Check for untied keyframes on the specified objects.",
                "setChecked": True,
            },
            "set_bake_animation_range": {
                "widget_type": "QCheckBox",
                "setText": "Auto Set Bake Animation Range",
                "setToolTip": "Set the animation export range to the first and last keyframes of the specified objects.\nThis will override the preset value, and is only applicable if baking is enabled.",
                "setChecked": True,
            },
            "create_log_file": {
                "widget_type": "QCheckBox",
                "setText": "Create Log File",
                "setToolTip": "Export a log file along with the fbx.",
                "setChecked": False,
            },
            "set_log_level": {
                "widget_type": "ComboBox",
                "setToolTip": "Set the log level.",
                "add": self._log_level_options,
                "setCurrentText": "Log Level: INFO",
            },
        }


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
