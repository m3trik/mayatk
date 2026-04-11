# !/usr/bin/python
# coding=utf-8
import os
import re
import math
from typing import Optional, Dict, Any, List

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
from mayatk.env_utils.hierarchy_manager._hierarchy_sidecar import HierarchySidecar


class _TaskDataMixin:
    """ """

    @property
    def _has_keyframes(self) -> bool:
        """Check if the current objects have keyframes."""
        if hasattr(self, "_key_times"):
            return bool(self._key_times)
        return bool(self._get_all_keyframes())

    def _get_all_keyframes(self) -> List[float]:
        """Return a sorted list of all unique keyframe times for the specified objects.

        Delegates to ``AnimUtils.get_keyframe_times`` for the actual query and
        caches the result set in ``_key_times`` for downstream consumers.
        """
        if not self.objects:
            return []

        # Filter to objects that still exist (smart_bake may delete
        # constraints/expressions, removing nodes from the scene).
        existing = cmds.ls(self.objects, long=True) or []
        if not existing:
            return []

        times = AnimUtils.get_keyframe_times(existing)
        if times is None:
            return []

        self._key_times = set(times)
        return times

    def _get_all_materials(self) -> List[str]:
        """Return a list of all materials assigned to the specified objects.

        Results are cached per export run. The cache is invalidated when
        ``objects`` is reassigned via ``_initialize_objects``.
        """
        if not hasattr(self, "_cached_materials") or self._cached_materials is None:
            self._cached_materials = MatUtils.filter_materials_by_objects(
                self.objects, as_strings=True
            )
        return self._cached_materials


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
            elif not new_workspace:
                self.logger.warning(
                    "No workspace.mel found in scene path hierarchy "
                    f"\u2014 using current workspace: {original_workspace}"
                )
            else:
                self.logger.debug("Workspace already matches scene path.")

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
        # Pass silent=True and as_strings=True to avoid pm.displayInfo
        # and pm.PyNode overhead.  Do NOT disable undo here — the
        # perform_export undo chunk needs to capture these changes so
        # the scene can be restored after export.
        MatUtils.remap_texture_paths(materials, silent=True, as_strings=True)
        self.logger.debug("Path conversion completed.")

    def reassign_duplicate_materials(self):
        """Reassign duplicate materials in the scene."""
        self.logger.debug("Reassigning duplicate materials")
        materials = self._get_all_materials()
        MatUtils.reassign_duplicate_materials(materials, delete=True)
        # Invalidate the materials cache since duplicates were deleted
        self._cached_materials = None
        self.logger.debug("Reassignment completed.")

    def resolve_invalid_texture_paths(self):
        """Attempt to resolve missing texture paths using workspace and sourceimages lookup.

        Scoped to materials assigned to the export objects. Uses
        ``MatUtils.resolve_path`` which checks env-var expansion,
        workspace-relative resolution, sourceimages directory, and
        basename-in-sourceimages as fallbacks.
        """
        import os

        materials = self._get_all_materials()
        if not materials:
            self.logger.debug("No materials found. Skipping texture path resolution.")
            return

        # Filter out materials that may have been deleted by earlier tasks
        materials = [m for m in materials if cmds.objExists(m)]
        if not materials:
            self.logger.debug(
                "No valid materials remain. Skipping texture path resolution."
            )
            return

        # Traverse shading history to find file nodes for export materials
        history = cmds.listHistory(materials, pruneDagObjects=True) or []
        file_nodes = cmds.ls(history, type="file") or []
        file_nodes = list(set(file_nodes))  # deduplicate

        if not file_nodes:
            self.logger.debug("No file nodes found. Skipping texture path resolution.")
            return

        resolved_count = 0
        unresolved = []

        for node in file_nodes:
            if not cmds.attributeQuery("fileTextureName", node=node, exists=True):
                continue

            path = cmds.getAttr(f"{node}.fileTextureName")
            if not path:
                continue

            expanded = os.path.expandvars(path)
            # Handle UDIM patterns
            check_path = (
                expanded.replace("<UDIM>", "1001") if "<UDIM>" in expanded else expanded
            )
            if os.path.exists(check_path):
                continue  # Path is already valid

            resolved = MatUtils.resolve_path(path)
            if resolved:
                cmds.setAttr(f"{node}.fileTextureName", resolved, type="string")
                resolved_count += 1
                self.logger.info(f"Resolved texture: {node} -> {resolved}")
            else:
                unresolved.append(f"{node} -> {path}")

        if resolved_count:
            self.logger.info(f"Resolved {resolved_count} broken texture path(s).")
        if unresolved:
            self.logger.warning(
                f"{len(unresolved)} texture path(s) could not be resolved:"
            )
            for entry in unresolved:
                self.logger.warning(f"  {entry}")
        if not resolved_count and not unresolved:
            self.logger.debug("All texture paths are valid.")

    def smart_bake(self):
        """Pre-bake constrained and driven channels before export.

        Uses SmartBake to detect objects with constraints, driven keys,
        expressions, IK, motion paths, and blend shapes, then bakes only
        those specific channels onto an override animation layer.
        FBX export with FBXExportBakeComplexAnimation samples the final
        evaluated output, so the override layer produces correct results
        without deleting driver nodes.  After export, the layer is deleted
        to restore the original scene state non-destructively.
        """
        from mayatk.anim_utils.smart_bake import SmartBake

        self.logger.info("Analyzing scene for bake requirements...")
        baker = SmartBake(
            objects=self.objects,
            sample_by=1,
            preserve_outside_keys=True,
            optimize_keys=False,  # Handled by the separate optimize_keys task in _task_config()
            use_override_layer=True,  # Non-destructive: bake to override layer
            delete_inputs=False,  # Keep constraints — layer overrides them
        )

        analysis = baker.analyze()
        if not any(a.requires_bake for a in analysis.values()):
            self.logger.info(
                "No constrained/driven objects found. Skipping smart bake."
            )
            return

        # Log what will be baked
        bake_count = sum(1 for a in analysis.values() if a.requires_bake)
        self.logger.info(f"Found {bake_count} objects requiring bake.")

        result = baker.bake(analysis)

        # Store layer names and curves for cleanup after export
        if result.override_layer:
            self._bake_override_layer = result.override_layer
        # Build detailed log message
        log_parts = [
            f"Smart bake completed: {result.baked_count} objects baked",
            f"range {result.time_range[0]}-{result.time_range[1]}",
        ]
        if result.override_layer:
            log_parts.append(f"layer '{result.override_layer}'")
        if result.optimized:
            log_parts.append(f"{len(result.optimized)} objects optimized")

        self.logger.info(", ".join(log_parts) + ".")

        # Refresh self.objects (no deletions expected, but re-validate)
        self.objects = cmds.ls(self.objects, long=True) or []

        # Invalidate keyframe cache since we added new keys
        if hasattr(self, "_key_times"):
            delattr(self, "_key_times")

    def optimize_keys(self):
        """Optimize baked animation keys."""
        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping optimization.")
            return

        self.logger.info("Optimizing baked animation keys...")
        # Optimizes base-layer curves only.  Override-layer curves from
        # smart_bake are optimized internally by SmartBake.optimize_keys.
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

    def _obj_link(self, node: str, action: str = "reveal") -> str:
        """Return a clickable log link for a Maya scene node.

        Parameters:
            node:   Full or short DAG path (used as both label and param).
            action: ``"select"`` or ``"reveal"`` (default).
        """
        short = node.rsplit("|", 1)[-1]
        return self.logger.log_link(short, action, node=node)

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

        matches = {}
        for obj in self.objects:
            # Check if geometry (has shapes)
            # Use cmds for speed
            shapes = cmds.listRelatives(obj, shapes=True)
            if not shapes:
                continue

            name = obj.split("|")[-1]
            if self._LOD_SUFFIX_REGEX.search(name):
                matches.setdefault(name, obj)

        if matches:
            messages.append("Geometry with LOD suffix detected (informational):")
            for n in sorted(matches):
                link = self._obj_link(matches[n], "reveal")
                messages.append(f"  - {link}")

        return True, messages

    def ignore_groups(self, names: str) -> None:
        """Exclude top-level groups matching *names* (case-insensitive) and all
        their descendants from the export object list.

        Parameters:
            names: Comma-separated group names to exclude (e.g. ``"temp, proxy"``).
        """
        if not self.objects or not names:
            return

        # Parse comma-separated names, strip whitespace, lowercase for matching
        target_names = {n.strip().lower() for n in names.split(",") if n.strip()}
        if not target_names:
            return

        # Find top-level groups whose short name matches any target
        root_nodes = cmds.ls(self.objects, assemblies=True, long=True) or []
        matched_roots = [
            node for node in root_nodes if node.split("|")[-1].lower() in target_names
        ]

        if not matched_roots:
            self.logger.debug(f"No top-level groups matching {target_names} found.")
            return

        # Gather the matched roots and all their descendants
        exclude = set(matched_roots)
        for root in matched_roots:
            descendants = (
                cmds.listRelatives(root, allDescendents=True, fullPath=True) or []
            )
            exclude.update(descendants)

        original_count = len(self.objects)
        self.objects = [obj for obj in self.objects if obj not in exclude]
        removed = original_count - len(self.objects)

        for root in matched_roots:
            self.logger.info(f"Ignoring group: {root}")
        self.logger.info(
            f"Excluded {removed} object(s) under {len(matched_roots)} group(s) from export."
        )

    def check_root_default_transforms(self) -> tuple:
        """Check if all root group nodes have default transforms."""
        log_messages = []
        box_logged = False
        tolerance = 1e-5
        has_non_default_transforms = False

        # self.objects contains only geometry transforms (never assemblies),
        # so we walk up each object's DAG path to find the root ancestor.
        root_groups = set()
        for obj in self.objects:
            # Long path: "|root|child|...|geo" — the root is segment [1]
            parts = obj.split("|")
            if len(parts) > 2:
                root_long = "|" + parts[1]
                root_groups.add(root_long)

        root_nodes = cmds.ls(list(root_groups), long=True) or []

        for node in root_nodes:
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
                link = self._obj_link(node)
                log_messages.append(
                    f"Node: {link}, Translate: {translate}, Rotate: {rotate}, Scale: {scale}"
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
                link = self._obj_link(mat_name, "select")
                log_messages.append(f"Absolute path - {link} - {pth}")

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
                    link = self._obj_link(node, "select")
                    log_messages.append(f"Missing Texture: {link} -> {path}")
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
                    link = self._obj_link(node, "select")
                    log_messages.append(f"Missing Texture (Relative): {link} -> {path}")

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
                        link = self._obj_link(ref, "select")
                        log_messages.append(f"Missing Reference: {link} -> {path}")
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
                # Short names may be ambiguous; link uses the first full path
                full_path = seen.get(name, name)
                link = self._obj_link(full_path, "reveal")
                log_messages.append(f"Duplicate locator name: {link}")
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
                    dup_link = self._obj_link(str(duplicate), "select")
                    orig_link = self._obj_link(str(original), "select")
                    log_messages.append(f"Duplicate: {dup_link} -> {orig_link}")
            return False, log_messages  # Failed, log the duplicates

        return True, log_messages  # All checks passed, no duplicates found

    def check_referenced_objects(self) -> tuple:
        """Check if any referenced objects are present in the scene."""
        log_messages = []
        # Check all referenced objects in the scene, not just the selected objects
        referenced_objects = cmds.ls(references=True) or []

        if referenced_objects:
            for ref in referenced_objects:
                link = self._obj_link(ref, "select")
                log_messages.append(f"Referenced Object: {link}")
            return False, log_messages  # Failed, log the referenced objects

        return True, log_messages  # All checks passed, no referenced objects found

    def check_framerate(self, target_framerate: Optional[str]) -> tuple:
        """Check if the scene's current framerate matches the target framerate."""
        if not target_framerate or str(target_framerate).upper() == "OFF":
            return True, []

        if not self._has_keyframes:
            self.logger.debug("No keyframes found. Skipping framerate check.")
            return True, []

        current_time_unit = pm.currentUnit(query=True, time=True)
        if current_time_unit != target_framerate:
            return False, [
                f"Framerate mismatch: Current time unit is {current_time_unit}, expected {target_framerate}."
            ]

        return True, []

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
                link = self._obj_link(obj)
                log_messages.append(
                    f"Object: {link} - Below Floor: True (Y-min: {ymin:.3f})"
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
            messages = [
                f"Overlapping duplicate object: {self._obj_link(obj)}"
                for obj in duplicates
            ]
            return False, messages  # Failed, duplicates found
        return True, []  # Passed, no duplicates

    def check_hidden_geometry(self) -> tuple:
        """Check if any geometry objects are hidden."""
        hidden_objects = []
        # Define what we consider "geometry"
        geometry_types = {"mesh", "nurbsSurface", "subdiv"}

        for obj in self.objects:
            # Check if geometry (has shapes)
            shapes = cmds.listRelatives(obj, shapes=True, fullPath=True)
            if not shapes:
                continue

            # Check if any shape is actually geometry
            is_geometry = False
            for shape in shapes:
                if cmds.nodeType(shape) in geometry_types:
                    is_geometry = True
                    break

            if not is_geometry:
                continue

            # Check visibility
            if not cmds.getAttr(f"{obj}.visibility"):
                hidden_objects.append(obj)

        if hidden_objects:
            return False, [
                f"Hidden geometry detected: {self._obj_link(obj)}"
                for obj in hidden_objects
            ]
        return True, []

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
            obj_link = self._obj_link(obj)
            for curve, s, e in curve_data:
                if s > min_start:
                    untied_keyframes_found = True
                    log_messages.append(
                        f"Untied keyframes found on curve: {curve} on {obj_link} (Start {s} != {min_start})"
                    )
                if e < max_end:
                    untied_keyframes_found = True
                    log_messages.append(
                        f"Untied keyframes found on curve: {curve} on {obj_link} (End {e} != {max_end})"
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
                # offender format: "objName (frame N.NNN)" — link the object part
                name = offender.split(" (frame")[0]
                link = self._obj_link(name, "select")
                detail = offender[len(name) :]
                log_messages.append(f"  - {link}{detail}")
            return False, log_messages

        return True, log_messages

    # ------------------------------------------------------------------
    # Hierarchy diff check — delegates to HierarchySidecar
    # ------------------------------------------------------------------

    # Backward-compatible aliases so existing call-sites still work.
    _manifest_path_for = staticmethod(HierarchySidecar.manifest_path_for)
    _diff_report_path_for = staticmethod(HierarchySidecar.diff_report_path_for)
    _build_clean_path_set = staticmethod(HierarchySidecar.build_clean_path_set)
    _get_top_level = staticmethod(HierarchySidecar.get_top_level)
    rename_hierarchy_sidecar = HierarchySidecar.rename

    def _build_full_hierarchy_set(self) -> set:
        """Build a clean path set including all descendants of ``self.objects``."""
        return HierarchySidecar.build_full_path_set(self.objects)

    def write_hierarchy_manifest(self) -> None:
        """Write a sidecar JSON manifest of the exported hierarchy paths.

        Only writes when the manifest already exists (maintaining it for
        future checks) or the check was enabled in the current run.
        """
        export_path = getattr(self, "export_path", None)
        if not export_path or not self.objects:
            return

        manifest_path = HierarchySidecar.manifest_path_for(export_path)

        check_ran = getattr(self, "_hierarchy_check_ran", False)
        if not check_ran and not os.path.exists(manifest_path):
            return

        paths = HierarchySidecar.build_full_path_set(self.objects)
        if HierarchySidecar.write_manifest(export_path, paths) is None:
            self.logger.debug("Could not write hierarchy manifest")

    def check_hierarchy_vs_existing_fbx(self) -> tuple:
        """Check export objects against the hierarchy manifest of the previous export.

        Compares namespace-stripped DAG paths of the current export objects
        against the sidecar ``.hierarchy.json`` written during the last
        successful export to the same path.  Detects missing or extra nodes
        that would indicate accidental structural changes.
        """
        self._hierarchy_check_ran = True

        export_path = getattr(self, "export_path", None)
        if not export_path:
            return True, []

        manifest_path = HierarchySidecar.manifest_path_for(export_path)

        if not os.path.exists(manifest_path):
            if os.path.exists(export_path):
                return True, [
                    "No hierarchy manifest found for existing FBX. "
                    "A manifest will be created after this export."
                ]
            return True, []

        current_paths = HierarchySidecar.build_full_path_set(self.objects)

        match, missing, extra = HierarchySidecar.compare(
            export_path, current_paths
        )

        if match:
            HierarchySidecar.clean_stale_diff(export_path)
            return True, []

        messages = []

        # Detect reparenting patterns for a cleaner summary
        reparented = HierarchySidecar.detect_reparenting(missing, extra)

        diff_path = HierarchySidecar.write_diff_report(
            export_path, missing, extra, reparented=reparented
        )

        if reparented:
            for root, new_parent, count in reparented:
                messages.append(
                    f"Reparenting detected: '{root}' moved under '{new_parent}' "
                    f"({count} node(s) affected)"
                )
            # Report any remaining missing/extra not explained by reparenting
            explained_missing = set()
            explained_extra = set()
            for root, new_parent, _ in reparented:
                for p in missing:
                    if p.split("|")[0] == root:
                        explained_missing.add(p)
                        explained_extra.add(f"{new_parent}|{p}")
                explained_extra.add(new_parent)
            remaining_missing = [p for p in missing if p not in explained_missing]
            remaining_extra = [p for p in extra if p not in explained_extra]
        else:
            remaining_missing = missing
            remaining_extra = extra

        if remaining_missing:
            top_missing = HierarchySidecar.get_top_level(remaining_missing)
            messages.append(
                f"{len(remaining_missing)} node(s) in previous export but missing now "
                f"({len(top_missing)} top-level):"
            )
            for p in top_missing[:20]:
                messages.append(f"  − {p}")
            if len(top_missing) > 20:
                messages.append(f"  … and {len(top_missing) - 20} more")

        if remaining_extra:
            top_extra = HierarchySidecar.get_top_level(remaining_extra)
            messages.append(
                f"{len(remaining_extra)} new node(s) not in previous export "
                f"({len(top_extra)} top-level):"
            )
            for p in top_extra[:20]:
                messages.append(f"  + {p}")
            if len(top_extra) > 20:
                messages.append(f"  … and {len(top_extra) - 20} more")

        if diff_path:
            link = self.logger.log_link(
                "Open full diff report", "open", filepath=diff_path
            )
            messages.append(link)

        return False, messages


class TaskManager(TaskFactory, _TaskActionsMixin, _TaskChecksMixin):
    """Contains all task-related UI definitions for the Scene Exporter."""

    # Explicit execution order for export tasks.  Tasks not listed here
    # are appended at the end in alphabetical order.  This prevents the
    # alphabetical-sort default from running tasks in the wrong sequence
    # (e.g. set_bake_animation_range before smart_bake, or
    # delete_unused_materials before reassign_duplicate_materials).
    TASK_ORDER = [
        # Phase 1 — Environment setup
        "set_workspace",
        "set_linear_unit",
        # Phase 2 — Object filtering
        "ignore_groups",
        # Phase 3 — Material cleanup (reassign THEN resolve THEN convert)
        "reassign_duplicate_materials",
        "resolve_invalid_texture_paths",
        "convert_to_relative_paths",
        # Phase 4 — Animation (bake THEN optimize THEN snap/tie THEN set range)
        "smart_bake",
        "optimize_keys",
        "snap_keys_to_frame",
        "tie_all_keyframes",
        "set_bake_animation_range",
    ]

    _frame_rate_options: Dict[str, Any] = {
        f"Check Scene FPS: {k}": k if v is not None else None
        for k, v in ptk.insert_into_dict(
            ptk.VidUtils.FRAME_RATES, "OFF", None
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
        self._objects = None
        self._cached_materials = None

    @property
    def objects(self):
        return self._objects

    @objects.setter
    def objects(self, value):
        """Invalidate the materials cache whenever objects change."""
        self._objects = value
        self._cached_materials = None

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
            "resolve_invalid_texture_paths": {
                "widget_type": "QCheckBox",
                "setText": "Resolve Invalid Texture Paths",
                "setToolTip": "Attempt to resolve missing texture paths using workspace and sourceimages directory lookup.",
                "setChecked": True,
            },
            "sep_anim": {
                "widget_type": "Separator",
                "title": "Animation",
            },
            "smart_bake": {
                "widget_type": "QCheckBox",
                "setText": "Smart Bake",
                "setToolTip": "Intelligently bake constraints, driven keys, expressions, IK, motion paths, and blend shapes to keyframes.\nAuto-detects time range from drivers, deletes driver nodes after baking.",
                "setChecked": True,
            },
            "optimize_keys": {
                "widget_type": "QCheckBox",
                "setText": "Optimize Keys",
                "setToolTip": "Remove static curves and redundant flat keys from all exported objects.\nAlso controls key optimization inside Smart Bake.\nPreserves stepped tangent types.",
                "setChecked": True,
            },
            "tie_all_keyframes": {
                "widget_type": "QCheckBox",
                "setText": "Tie All Keyframes",
                "setToolTip": "Tie all keyframes on the specified objects.",
                "setChecked": True,
            },
            "snap_keys_to_frame": {
                "widget_type": "QCheckBox",
                "setText": "Snap Keys To Frame",
                "setToolTip": "Snap all keyframes to the nearest whole frame.",
                "setChecked": False,
            },
            "set_bake_animation_range": {
                "widget_type": "QCheckBox",
                "setText": "Auto Set Bake Animation Range",
                "setToolTip": "Set the animation export range to the first and last keyframes of the specified objects.\nThis will override the preset value, and is only applicable if baking is enabled.",
                "setChecked": True,
            },
            "sep_hierarchy": {
                "widget_type": "Separator",
                "title": "Hierarchy",
            },
            "ignore_groups": {
                "widget_type": "QLineEdit",
                "setPlaceholderText": "Group names to ignore (comma-separated)",
                "setToolTip": "Comma-separated names of top-level groups to exclude from export (case-insensitive).\nExample: temp, proxy\nLeave empty to skip.",
                "setText": "temp",
                "value_method": "text",
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
            "check_hierarchy_vs_existing_fbx": {
                "widget_type": "QCheckBox",
                "setText": "Check Hierarchy vs Existing FBX",
                "setToolTip": (
                    "Compare the current export hierarchy against the previous export.\n"
                    "Detects missing or extra nodes that may indicate accidental changes.\n"
                    "Uses a lightweight sidecar manifest — no FBX reimport required."
                ),
                "setChecked": False,
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
