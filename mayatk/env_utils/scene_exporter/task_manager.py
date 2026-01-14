# !/usr/bin/python
# coding=utf-8
from typing import Optional, Dict, Any, List
import re
import math

try:
    import pymel.core as pm
    from maya import cmds
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# From this package:
from mayatk.edit_utils._edit_utils import EditUtils
from mayatk.anim_utils._anim_utils import AnimUtils
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.xform_utils._xform_utils import XformUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.env_utils.scene_exporter.task_factory import TaskFactory


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

        import time

        t0 = time.time()

        # Optimization: Iterate curves instead of objects
        # cmds.keyframe(objects) is slow (5s for 200 objects).
        # Iterating curves is fast (0.02s for 200 curves).

        all_curves = (
            cmds.listConnections(
                self.objects, type="animCurve", source=True, destination=False
            )
            or []
        )
        # Ensure uniqueness
        all_curves = list(set(all_curves))

        times = []
        for curve in all_curves:
            t = cmds.keyframe(curve, query=True, timeChange=True)
            if t:
                times.extend(t)

        t1 = time.time()
        self.logger.info(
            f"_get_all_keyframes query took: {t1-t0:.4f}s. Found {len(times)} keys."
        )

        key_times = set(times)

        if key_times:
            self._key_times = key_times
        return sorted(key_times)

    def _get_all_materials(self) -> List[str]:
        """Return a list of all materials assigned to the specified objects."""
        mats = MatUtils.filter_materials_by_objects(self.objects, as_strings=True)
        return mats


class _TaskActionsMixin(_TaskDataMixin):
    """ """

    def set_workspace(self, enable=True):
        """Manage temporary workspace change."""
        original_workspace = pm.workspace(query=True, rootDirectory=True)

        if enable:
            new_workspace = EnvUtils.find_workspace_using_path()
            if new_workspace and new_workspace != original_workspace:
                pm.workspace(new_workspace, openWorkspace=True)
                self.logger.debug(
                    f"Changed workspace from {original_workspace} to {new_workspace}"
                )
            else:
                self.logger.debug("Workspace change not needed or workspace not found")

        return original_workspace

    def revert_workspace(self, original_workspace):
        """Revert to the original workspace."""
        pm.workspace(original_workspace, openWorkspace=True)
        self.logger.debug(f"Reverted workspace to: {original_workspace}")

    def set_linear_unit(self, linear_unit):
        """Manage temporary linear unit change."""
        original_linear_unit = pm.currentUnit(query=True, linear=True)

        if linear_unit and linear_unit != "OFF":
            pm.currentUnit(linear=linear_unit)
            self.logger.debug(
                f"Changed linear unit from {original_linear_unit} to {linear_unit}"
            )
        else:
            self.logger.debug(f"Linear unit change skipped (value: {linear_unit})")

        return original_linear_unit

    def revert_linear_unit(self, original_linear_unit):
        """Revert to the original linear unit."""
        pm.currentUnit(linear=original_linear_unit)
        self.logger.debug(f"Reverted linear unit to: {original_linear_unit}")

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

        # Use cmds for performance to avoid creating PyNodes for all file nodes
        file_nodes = cmds.ls(type="file") or []
        to_delete = []

        for node in file_nodes:
            if cmds.attributeQuery("fileTextureName", node=node, exists=True):
                texture_path = cmds.getAttr(f"{node}.fileTextureName")
                if texture_path and any(
                    keyword in texture_path.lower() for keyword in env_keywords
                ):
                    to_delete.append(node)

        if to_delete:
            cmds.delete(to_delete)
            self.logger.info(f"Deleted {len(to_delete)} environment file nodes.")
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

        # Optimization: Pass cached keyframe range to avoid re-querying
        custom_range = None
        if hasattr(self, "_key_times") and self._key_times:
            # _key_times is a set, need to sort it to get min/max
            sorted_times = sorted(self._key_times)
            custom_range = (sorted_times[0], sorted_times[-1])

        AnimUtils.tie_keyframes(self.objects, absolute=True, custom_range=custom_range)
        self.logger.info("Keyframes have been tied.")

    def snap_keys_to_frame(self):
        """Snap all keyframes to the nearest whole frame."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping snap operation.")
            return

        self.logger.info("Snapping keyframes to nearest whole frame.")
        AnimUtils.snap_keys_to_frames(self.objects)
        self.logger.info("Keyframes have been snapped.")


class _TaskChecksMixin(_TaskDataMixin):
    """ """

    _LOD_SUFFIX_REGEX = re.compile(r"_lod\d*$", re.IGNORECASE)

    def check_geometry_lod_suffix(self) -> tuple:
        """Check for geometry whose names end with '_LOD' or '_LOD' followed by digits.

        Returns:
            tuple: (status: bool, messages: list)

        Notes:
            - This check is informational. It returns True regardless, and lists any matches.
            - Suffix examples matched: '_LOD', '_LOD0', '_LOD1', '_LOD02', etc. (case-insensitive)
        """
        messages: List[str] = []

        if not self.objects:
            return True, messages

        matches = []
        for obj in self.objects:
            # Check if geometry (has shapes)
            # Use cmds for speed
            shapes = cmds.listRelatives(obj, shapes=True)
            if not shapes:
                continue

            name = obj.split("|")[-1]
            if self._LOD_SUFFIX_REGEX.search(name):
                matches.append(name)

        if matches:
            messages.append("Geometry with LOD suffix detected (informational):")
            for n in sorted(set(matches)):
                messages.append(f"  - {n}")

        return True, messages

    def check_top_level_group_temp(self) -> tuple:
        """Fail if any top-level group (assembly) is named 'temp' (case-insensitive).

        Returns:
            tuple: (status: bool, messages: list)
        """
        log_messages: List[str] = []
        offenders: List[str] = []

        # Consider only assemblies (top-level DAG nodes)
        # Use cmds for speed
        root_nodes = (
            cmds.ls(self.objects, assemblies=True, long=True) if self.objects else []
        )
        for node in root_nodes:
            short_name = node.split("|")[-1]
            if short_name.lower() == "temp":
                offenders.append(node)

        if offenders:
            log_messages.append("Top-level group(s) named 'temp' found:")
            for n in sorted(set(offenders)):
                log_messages.append(f"  - {n}")
            # Treat as a failure so it blocks export until renamed
            return False, log_messages

        return True, log_messages

    def check_root_default_transforms(self) -> tuple:
        """Check if all root group nodes have default transforms."""
        log_messages = []
        box_logged = False
        root_nodes = cmds.ls(self.objects, assemblies=True, long=True)
        tolerance = 1e-5
        has_non_default_transforms = False

        for node in root_nodes:
            # Check if it's a group (has children but no shape children usually, or just check transforms)
            if not NodeUtils.is_group(node):
                continue

            translate = cmds.getAttr(f"{node}.translate")[0]
            rotate = cmds.getAttr(f"{node}.rotate")[0]
            scale = cmds.getAttr(f"{node}.scale")[0]

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
                mat_name = mat.name() if hasattr(mat, "name") else str(mat)
                log_messages.append(f"Absolute path - {mat_name} - {pth}")

        return all_relative, log_messages

    def check_valid_paths(self) -> tuple:
        """Check if all file paths (textures, references, etc.) exist on disk."""
        import os

        # We can accept relative paths if they resolve relative to project
        log_messages = []
        all_valid = True

        # 1. Texture Paths
        # Use cmds to avoid PyNodes
        file_nodes = cmds.ls(type="file") or []
        for node in file_nodes:
            if not cmds.attributeQuery("fileTextureName", node=node, exists=True):
                continue

            path = cmds.getAttr(f"{node}.fileTextureName")
            if not path:
                # Some empty file nodes might exist?
                continue

            expanded_path = os.path.expandvars(path)

            # If absolute check directly
            if os.path.isabs(expanded_path):
                if not os.path.exists(expanded_path):
                    all_valid = False
                    log_messages.append(f"Missing Texture: {node} -> {path}")
            else:
                # If relative, try to resolve
                workspace_root = cmds.workspace(query=True, rootDirectory=True)

                # Check common relative locations
                possible_paths = [
                    os.path.join(workspace_root, expanded_path),
                    os.path.join(workspace_root, "sourceimages", expanded_path),
                    os.path.abspath(expanded_path),  # Relative to current working dir
                ]

                found = False
                for p in possible_paths:
                    if os.path.exists(p):
                        found = True
                        break

                if not found:
                    all_valid = False
                    log_messages.append(f"Missing Texture (Relative): {node} -> {path}")

        # 2. Reference Paths
        references = cmds.ls(references=True) or []
        for ref in references:
            try:
                # withoutCopyNumber=True gets actual file path
                path = cmds.referenceQuery(ref, filename=True, withoutCopyNumber=True)
                if path:
                    expanded_path = os.path.expandvars(path)
                    if not os.path.exists(expanded_path):
                        all_valid = False
                        log_messages.append(f"Missing Reference: {ref} -> {path}")
            except Exception:
                continue

        if all_valid:
            log_messages.append("All checked paths exist on disk.")

        return all_valid, log_messages

    def check_duplicate_locator_names(self) -> tuple:
        """Check for duplicate locator short names among the specified objects.

        Returns:
            tuple: (status: bool, messages: list)
        """
        log_messages = []
        # Use cmds for speed
        # Get all shapes of type locator from self.objects (which are transforms)
        locator_shapes = (
            cmds.listRelatives(self.objects, shapes=True, type="locator", fullPath=True)
            or []
        )
        if not locator_shapes:
            return True, log_messages

        locator_transforms = (
            cmds.listRelatives(locator_shapes, parent=True, fullPath=True) or []
        )

        seen = {}
        duplicates = set()
        for loc in locator_transforms:
            name = loc.split("|")[-1]
            if name in seen:
                duplicates.add(name)
            else:
                seen[name] = loc

        if duplicates:
            for name in sorted(duplicates):
                log_messages.append(f"Duplicate locator name: {name}")
            return False, log_messages
        return True, log_messages

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
        # Check all referenced objects in the scene, not just the selected objects
        referenced_objects = cmds.ls(references=True) or []

        if referenced_objects:
            for ref in referenced_objects:
                log_messages.append(f"Referenced Object: {ref}")
            return False, log_messages  # Failed, log the referenced objects

        return True, log_messages  # All checks passed, no referenced objects found

    def check_framerate(self, target_framerate: Optional[str]) -> tuple:
        """Check if the scene's current framerate matches the target framerate."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping framerate check.")
            return True, []

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

    def check_objects_below_floor(self, tolerance: float = 0.5) -> tuple:
        """Check if any object's geometry is below the floor plane (Y=0).

        Args:
            tolerance: Allowable distance (in scene units) beneath the plane before failing.
        """
        log_messages = []
        has_objects_below = False

        tolerance = 0.0 if tolerance is None else max(0.0, float(tolerance))
        limit = -tolerance

        for obj in self.objects:
            # Check if geometry (has shapes)
            shapes = cmds.listRelatives(obj, shapes=True)
            if not shapes:
                continue

            bbox = cmds.xform(obj, query=True, ws=True, bb=True)
            if not bbox:
                continue

            ymin = bbox[1]
            if ymin < limit:
                has_objects_below = True
                log_messages.append(
                    f"Object: {obj} - Below Floor: True (Y-min: {ymin:.3f})"
                )

        if has_objects_below:
            log_messages.insert(
                0,
                f"Tolerance used: {tolerance:.3f} unit{'s' if tolerance != 1 else ''}",
            )
            return False, log_messages  # Failed, log objects below the floor threshold

        return True, log_messages  # All checks passed, no objects below the floor

    def check_overlapping_duplicate_mesh(self) -> tuple:
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
        hidden_objects = []
        for obj in self.objects:
            # Check if geometry (has shapes)
            shapes = cmds.listRelatives(obj, shapes=True)
            if not shapes:
                continue

            # Check visibility
            if not cmds.getAttr(f"{obj}.visibility"):
                hidden_objects.append(obj)

        if hidden_objects:
            return False, [f"Hidden geometry detected: {obj}" for obj in hidden_objects]
        return True, []

    def check_and_delete_visibility_keys(self) -> tuple:
        """Check if there are any visibility keys on the specified objects and delete them."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping visibility key check.")
            return True, []

        log_messages = []
        visibility_keys_found = False

        # Find objects with visibility keys using cmds
        objs_with_vis_keys = (
            cmds.keyframe(self.objects, attribute="visibility", query=True, name=True)
            or []
        )
        # Remove duplicates
        objs_with_vis_keys = list(set(objs_with_vis_keys))

        for obj in objs_with_vis_keys:
            visibility_keys_found = True

            # Set visibility to true before deleting keys
            cmds.setAttr(f"{obj}.visibility", True)

            # Delete visibility keys
            cmds.cutKey(obj, attribute="visibility")
            log_messages.append(
                f"Visibility set to true and keys deleted for object: {obj}"
            )

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
            return True, []

        log_messages = []
        untied_keyframes_found = False

        # Optimization: Get all connections at once to avoid N calls to listConnections
        # connections=True returns [source, dest, source, dest...]
        # plugs=True returns [obj.plug, curve.output, ...]
        connections = (
            cmds.listConnections(
                self.objects,
                type="animCurve",
                source=True,
                destination=False,
                connections=True,
                plugs=True,
            )
            or []
        )

        # Parse into a dict: obj_name -> set(curves)
        obj_curves = {}
        for i in range(0, len(connections), 2):
            obj_plug = connections[i]  # e.g. "pCube1.translateX"
            curve_plug = connections[i + 1]  # e.g. "animCurveTL1.output"

            obj_name = obj_plug.split(".")[0]
            curve_name = curve_plug.split(".")[0]

            if obj_name not in obj_curves:
                obj_curves[obj_name] = set()
            obj_curves[obj_name].add(curve_name)

        for obj, curves in obj_curves.items():
            if not curves:
                continue

            # Get start/end for each curve
            curve_data = []
            min_start = float("inf")
            max_end = float("-inf")

            for curve in curves:
                # findKeyframe on a curve is fast
                s = cmds.findKeyframe(curve, which="first")
                e = cmds.findKeyframe(curve, which="last")
                curve_data.append((curve, s, e))

                if s < min_start:
                    min_start = s
                if e > max_end:
                    max_end = e

            # Check for mismatches
            for curve, s, e in curve_data:
                if s > min_start:
                    untied_keyframes_found = True
                    log_messages.append(
                        f"Untied keyframes found on curve: {curve} on {obj} (Start {s} != {min_start})"
                    )
                if e < max_end:
                    untied_keyframes_found = True
                    log_messages.append(
                        f"Untied keyframes found on curve: {curve} on {obj} (End {e} != {max_end})"
                    )

        if untied_keyframes_found:
            return False, log_messages  # Failed, log untied keyframes

        return True, log_messages  # All checks passed, no untied keyframes

    def check_floating_point_keys(self) -> tuple:
        """Check if there are any floating point keyframes on the specified objects."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping floating point key check.")
            return True, []

        log_messages = []
        offenders = []

        # Optimization: Iterate curves instead of objects
        # This is much faster than querying keyframes per object
        all_curves = (
            cmds.listConnections(
                self.objects, type="animCurve", source=True, destination=False
            )
            or []
        )
        all_curves = list(set(all_curves))

        for curve in all_curves:
            times = cmds.keyframe(curve, query=True, timeChange=True)
            if not times:
                continue

            for t in times:
                if not math.isclose(t, round(t), abs_tol=1e-4):
                    # Find object name
                    conn = cmds.listConnections(
                        curve, plugs=True, destination=True, source=False
                    )
                    obj_name = conn[0].split(".")[0] if conn else curve
                    offenders.append(f"{obj_name} (frame {t:.3f})")
                    break

        # Remove duplicates
        offenders = sorted(list(set(offenders)))

        if offenders:
            log_messages.append("Floating point keys found on:")
            for offender in offenders:
                log_messages.append(f"  - {offender}")
            return False, log_messages

        return True, log_messages


class TaskManager(TaskFactory, _TaskActionsMixin, _TaskChecksMixin):
    """Contains all task-related UI definitions for the Scene Exporter."""

    _frame_rate_options: Dict[str, Any] = {
        f"Check Scene FPS: {v}": k
        for k, v in ptk.insert_into_dict(ptk.VidUtils.FRAME_RATES, "OFF", None).items()
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

    _export_mode_options: Dict[str, Any] = {
        "Export: All Scene Objects": "all",
        "Export: All Visible Objects": "visible",
        "Export: Selected Objects Only": "selected",
    }

    @property
    def task_definitions(self) -> Dict[str, Dict[str, Any]]:
        """Return the task definitions for the UI."""
        return {
            "sep_general": {
                "widget_type": "Separator",
                "title": "General",
            },
            "export_visible_objects": {
                "widget_type": "ComboBox",
                "setToolTip": "Choose what objects to export:\n- All Visible Objects: Export all visible geometry in the scene\n- Selected Objects Only: Export only currently selected objects\n- All Scene Objects: Export all objects regardless of visibility or selection",
                "add": self._export_mode_options,
                "value_method": "currentData",
            },
            "set_linear_unit": {
                "widget_type": "ComboBox",
                "setToolTip": "Linear unit to be used during export.",
                "add": self._scene_unit_options,
            },
            "set_workspace": {
                "widget_type": "QCheckBox",
                "setText": "Auto Set Workspace",
                "setToolTip": "Determine the workspace directory from the scene path.",
                "setChecked": True,
            },
            "sep_materials": {
                "widget_type": "Separator",
                "title": "Materials",
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
            "convert_to_relative_paths": {
                "widget_type": "QCheckBox",
                "setText": "Convert To Relative Paths",
                "setToolTip": "Convert absolute paths to relative paths.",
                "setChecked": True,
            },
            "sep_env": {
                "widget_type": "Separator",
                "title": "Environment",
            },
            "delete_env_nodes": {
                "widget_type": "QCheckBox",
                "setText": "Delete Environment Nodes",
                "setToolTip": "Delete environment file nodes.\nEnvironment nodes are defined as: 'diffuse_cube', 'specular_cube', 'ibl_brdf_lut'",
                "setChecked": False,
            },
            "sep_anim": {
                "widget_type": "Separator",
                "title": "Animation",
            },
            "tie_all_keyframes": {
                "widget_type": "QCheckBox",
                "setText": "Tie All Keyframes",
                "setToolTip": "Tie all keyframes on the specified objects.",
                "setChecked": False,
            },
            "snap_keys_to_frame": {
                "widget_type": "QCheckBox",
                "setText": "Snap Keys To Frame",
                "setToolTip": "Snap all keyframes to the nearest whole frame.",
                "setChecked": False,
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
            "set_bake_animation_range": {
                "widget_type": "QCheckBox",
                "setText": "Auto Set Bake Animation Range",
                "setToolTip": "Set the animation export range to the first and last keyframes of the specified objects.\nThis will override the preset value, and is only applicable if baking is enabled.",
                "setChecked": True,
            },
        }

    @property
    def check_definitions(self) -> Dict[str, Dict[str, Any]]:
        """Return the check definitions for the UI."""
        return {
            "sep_general": {
                "widget_type": "Separator",
                "title": "General",
            },
            "check_framerate": {
                "widget_type": "ComboBox",
                "setToolTip": "Check the scene framerate against the target framerate.",
                "add": self._frame_rate_options,
            },
            "check_referenced_objects": {
                "widget_type": "QCheckBox",
                "setText": "Check For Referenced Objects.",
                "setToolTip": "Check for referenced objects.",
                "setChecked": True,
            },
            "sep_hierarchy": {
                "widget_type": "Separator",
                "title": "Hierarchy & Naming",
            },
            "check_top_level_group_temp": {
                "widget_type": "QCheckBox",
                "setText": "Check Top-level Group Named 'temp'",
                "setToolTip": "Fail if any top-level group (assembly) is named 'temp' (case-insensitive).",
                "setChecked": True,
            },
            "check_geometry_lod_suffix": {
                "widget_type": "QCheckBox",
                "setText": "Check Geometry LOD Suffix (_LODx)",
                "setToolTip": "Detect geometry named with LOD suffixes ending in '_LOD' or '_LOD' followed by digits (e.g., _LOD, _LOD1, _LOD02). This is informational.",
                "setChecked": True,
            },
            "check_duplicate_locator_names": {
                "widget_type": "QCheckBox",
                "setText": "Check For Duplicate Locator Names",
                "setToolTip": "Check for duplicate locator names.",
                "setChecked": True,
            },
            "check_root_default_transforms": {
                "widget_type": "QCheckBox",
                "setText": "Check Root Default Transforms",
                "setToolTip": "Check for default transforms on root group nodes.\nTranslate, rotate, and scale should be (0, 0, 0) and (1, 1, 1) respectively.",
                "setChecked": True,
            },
            "sep_geometry": {
                "widget_type": "Separator",
                "title": "Geometry",
            },
            "check_hidden_geometry": {
                "widget_type": "QCheckBox",
                "setText": "Check For Hidden Geometry.",
                "setToolTip": "Check for hidden geometry that will be exported.",
                "setChecked": True,
            },
            "check_overlapping_duplicate_mesh": {
                "widget_type": "QCheckBox",
                "setText": "Check For Overlapping Duplicates",
                "setToolTip": "Check for overlapping duplicate geometry.",
                "setChecked": True,
            },
            "check_objects_below_floor": {
                "widget_type": "QCheckBox",
                "setText": "Check For Objects Below Floor.",
                "setToolTip": (
                    "Check for geometry dipping below Y=0. A default 0.5 unit "
                    "tolerance is applied so shallow penetrations (e.g. tires) "
                    "do not immediately fail. Override by calling the check with a "
                    "'tolerance' keyword argument."
                ),
                "setChecked": True,
            },
            "sep_materials": {
                "widget_type": "Separator",
                "title": "Materials",
            },
            "check_duplicate_materials": {
                "widget_type": "QCheckBox",
                "setText": "Check For Duplicate Materials.",
                "setToolTip": "Check for duplicate materials.",
                "setChecked": True,
            },
            "check_absolute_paths": {
                "widget_type": "QCheckBox",
                "setText": "Check For Absolute Paths.",
                "setToolTip": "Check for absolute paths.",
                "setChecked": True,
            },
            "check_valid_paths": {
                "widget_type": "QCheckBox",
                "setText": "Check For Valid Paths.",
                "setToolTip": "Check if all file paths (textures, references) exist on disk.",
                "setChecked": True,
            },
            "sep_anim": {
                "widget_type": "Separator",
                "title": "Animation",
            },
            "check_untied_keyframes": {
                "widget_type": "QCheckBox",
                "setText": "Check For Untied Keyframes",
                "setToolTip": "Check for untied keyframes on the specified objects.",
                "setChecked": True,
            },
            "check_floating_point_keys": {
                "widget_type": "QCheckBox",
                "setText": "Check For Floating Point Keys",
                "setToolTip": "Check for keyframes that are not on whole frames.",
                "setChecked": True,
            },
        }

    @property
    def definitions(self) -> Dict[str, Dict[str, Any]]:
        """Return all definitions combined for backward compatibility."""
        return {**self.task_definitions, **self.check_definitions}


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
