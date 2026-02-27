# !/usr/bin/python
# coding=utf-8
import os
from pathlib import Path
from typing import Optional, Dict, List, Any

# Third-party imports
from qtpy import QtCore, QtWidgets, QtGui
import pythontk as ptk
import pymel.core as pm

# From this package
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.env_utils.namespace_sandbox import NamespaceSandbox
from mayatk.env_utils.hierarchy_manager._hierarchy_manager import (
    HierarchyManager,
    ObjectSwapper,
    get_clean_node_name_from_string,
    is_default_maya_camera,
    select_objects_in_maya,
)
import mayatk.env_utils.hierarchy_manager._tree_utils as tree_utils


class HierarchyManagerController(ptk.LoggingMixin):
    """Controller for hierarchy management operations."""

    def __init__(self, slots_instance):
        super().__init__()
        self.sb = slots_instance.sb
        self.ui = slots_instance.ui

        # Setup logging
        self._redirect_logger(self.logger)

        # Initialize state
        self.hierarchy_manager = None
        self.object_swapper = None
        self._current_diff_result = None
        self._importing_reference = (
            False  # Flag to prevent current scene refresh during reference import
        )
        self._reference_namespaces = (
            []
        )  # Track current reference namespaces for filtering

        self.logger.debug("HierarchyManagerController initialized.")

    def _redirect_logger(self, logger) -> None:
        """Configure a logger to redirect output to the UI text widget."""
        logger.hide_logger_name(True)
        logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        logger.setup_logging_redirect(self.ui.txt003)

    @property
    def workspace(self) -> Optional[str]:
        """Get the current workspace directory."""
        workspace_path = EnvUtils.get_env_info("workspace")
        if not workspace_path:
            self.logger.error("Workspace directory not found.")
        return workspace_path

    def analyze_hierarchies(
        self, reference_path: str, fuzzy_matching: bool = True, dry_run: bool = True
    ) -> bool:
        """Analyze hierarchies and perform comparison.

        This method creates a hierarchy comparison between the current scene and reference file.
        It's separate from just displaying the hierarchies - this is for actual analysis.

        Args:
            reference_path: Path to the reference scene file
            fuzzy_matching: Enable fuzzy name matching
            dry_run: Perform analysis without making changes

        Returns:
            bool: True if analysis was successful
        """
        if not reference_path:
            self.logger.error("Please specify a reference scene path.")
            return False

        if not os.path.exists(reference_path):
            self.logger.error(f"Reference scene does not exist: {reference_path}")
            return False

        # Former cache reuse logic relied on hierarchy_manager.reference_file which no longer exists.
        # Removed to avoid silently skipping fresh analyses.

        try:
            self.logger.progress(
                f"Analyzing hierarchy differences with: {os.path.basename(reference_path)}"
            )

            # Ensure we have a valid comparison setup
            selected = pm.selected(type="transform")
            if not selected:
                # No selection - this means we want scene-wide comparison
                # Don't select anything - let the analyzer handle scene-wide mode
                self.logger.notice(
                    "No objects selected — performing full scene hierarchy comparison"
                )
            else:
                self.logger.debug(
                    f"Using {len(selected)} pre-selected objects for hierarchy comparison"
                )

            # Import the reference scene temporarily for analysis
            temp_import = NamespaceSandbox(dry_run=False)
            self._redirect_logger(temp_import.logger)

            import_info = temp_import.import_with_namespace(
                reference_path, force_complete_import=True
            )
            if not import_info or not import_info.get("transforms"):
                self.logger.error(
                    "Failed to import reference file or no transforms found"
                )
                return False

            # Create hierarchy manager for comparison analysis
            self.hierarchy_manager = HierarchyManager(
                import_manager=temp_import,
                fuzzy_matching=fuzzy_matching,
                dry_run=dry_run,
            )

            self._redirect_logger(self.hierarchy_manager.logger)

            # Perform analysis - pass the imported reference objects
            reference_transforms = import_info.get("transforms", [])

            # Note: Camera cleanup is now handled automatically by NamespaceSandbox camera tracking
            # We still filter out Maya default cameras from analysis to avoid namespace issues,
            # but the cleanup is handled by the enhanced NamespaceSandbox.cleanup_all_namespaces()

            # Filter out Maya default cameras from reference transforms
            non_default_camera_reference_transforms = [
                t for t in reference_transforms if not self._is_default_maya_camera(t)
            ]

            self.logger.debug(
                f"Filtered reference objects: {len(reference_transforms)} total, "
                f"{len(non_default_camera_reference_transforms)} for analysis"
            )

            # Extract reference namespaces for UI filtering (from all transforms, not just non-cameras)
            if reference_transforms:
                self._reference_namespaces = sorted(
                    {
                        t.nodeName().split(":")[0]
                        for t in reference_transforms
                        if ":" in t.nodeName()
                    }
                )
                if self._reference_namespaces:
                    self.logger.debug(
                        f"Tracking reference namespaces for UI filtering: {', '.join(self._reference_namespaces)}"
                    )
            else:
                self._reference_namespaces = []

            self._current_diff_result = self.hierarchy_manager.analyze_hierarchies(
                current_tree_root="SCENE_WIDE_MODE",
                reference_objects=non_default_camera_reference_transforms,  # Pass filtered transforms
                filter_meshes=True,
                filter_cameras=False,  # No longer needed since we pre-filtered default cameras
                filter_lights=False,
            )

            # Clean up the imported reference data
            temp_import.cleanup_all_namespaces()

            if not self._current_diff_result:
                self.logger.warning("Analysis returned no results")
                return False

            return True

        except Exception as e:
            self.logger.error(f"Error during hierarchy analysis: {e}")

            # Provide more specific error information
            if "Failed to resolve source or target roots" in str(e):
                self.logger.error(
                    "Analysis failed because no valid objects could be found for comparison."
                )
                self.logger.info(
                    "Try selecting an object in your current scene before running analysis."
                )

            self._current_diff_result = None
            return False

    def _clear_analysis_cache(self):
        """Clear the analysis cache to force re-analysis on next diff operation."""
        self.hierarchy_manager = None
        self._current_diff_result = None
        self._reference_namespaces = []  # Clear reference namespace tracking
        self.logger.debug("Analysis cache cleared")

    def _cleanup_temp_namespaces(self):
        """Clean up any remaining temporary namespaces."""
        try:
            # Get all current namespaces
            all_namespaces = pm.namespaceInfo(listOnlyNamespaces=True, recurse=True)

            # Find temp import namespaces
            temp_namespaces = [ns for ns in all_namespaces if "temp_import_" in ns]

            if temp_namespaces:
                self.logger.debug(
                    f"Cleaning up {len(temp_namespaces)} remaining temp namespaces"
                )

                for namespace in temp_namespaces:
                    try:
                        # Delete objects in namespace first
                        namespace_objects = pm.ls(f"{namespace}:*")
                        if namespace_objects:
                            pm.delete(namespace_objects)

                        # Remove namespace
                        if pm.namespace(exists=namespace):
                            pm.namespace(
                                removeNamespace=namespace, mergeNamespaceWithRoot=True
                            )
                            self.logger.debug(f"Removed temp namespace: {namespace}")
                    except Exception as ns_error:
                        self.logger.debug(
                            f"Could not remove namespace {namespace}: {ns_error}"
                        )
            else:
                self.logger.debug("No temp namespaces found to clean up")

            # Clear reference namespace tracking after cleanup
            # This ensures old namespace tracking doesn't affect future operations
            self._reference_namespaces = []

        except Exception as cleanup_error:
            self.logger.debug(f"Temp namespace cleanup failed: {cleanup_error}")

    def pull_objects(
        self,
        object_names: List[str],
        reference_path: str,
        fuzzy_matching: bool = True,
        dry_run: bool = True,
        pull_children: bool = False,
        pull_mode: str = "Add to Scene",
    ) -> bool:
        """Pull objects from reference scene to current scene.

        Args:
            object_names: List of object names to pull
            reference_path: Path to the reference scene
            fuzzy_matching: Enable fuzzy matching
            dry_run: Perform dry run
            pull_children: Include children in the pull operation
            pull_mode: How to handle hierarchy ("Add to Scene" or "Merge Hierarchies")

        Returns:
            bool: True if operation was successful
        """
        if not object_names:
            self.logger.error("No objects specified for pulling.")
            return False

        try:
            # Always create a fresh object swapper with current settings
            # to ensure dry_run setting is up to date
            self.object_swapper = ObjectSwapper(
                dry_run=dry_run,
                fuzzy_matching=fuzzy_matching,
                pull_mode=pull_mode,
                pull_children=pull_children,
            )

            # Setup logging for object swapper and its sub-components
            for sub_logger in (
                self.object_swapper.logger,
                self.object_swapper.matcher.logger,
                self.object_swapper.import_manager.logger,
            ):
                self._redirect_logger(sub_logger)

            success = self.object_swapper.pull_objects_from_scene(
                target_objects=object_names,
                source_file=reference_path,
                backup=True,
            )

            # Display formatted pull operation results using box formatting
            self._display_pull_operation_results(
                object_names,
                reference_path,
                success,
                pull_mode,
                fuzzy_matching,
                dry_run,
            )

            if success:
                return True
            else:
                self.logger.error("Failed to pull objects from reference scene.")
                return False

        except Exception as e:
            self.logger.error(f"Error pulling objects: {e}")
            return False

    def _is_default_maya_camera(self, transform):
        """Check if a transform is a Maya default camera that should be excluded."""
        try:
            node_name = transform.nodeName()
            return is_default_maya_camera(node_name, transform)
        except Exception:
            return False

    def _display_pull_operation_results(
        self,
        object_names: List[str],
        reference_path: str,
        success: bool,
        pull_mode: str,
        fuzzy_matching: bool,
        dry_run: bool,
    ):
        """Display formatted pull operation results using log_table + log_box."""
        self.logger.log_divider()

        # Summary table
        self.log_table(
            data=[
                ["Source", Path(reference_path).name],
                ["Requested", f"{len(object_names)} objects"],
                ["Pull Mode", pull_mode],
                ["Fuzzy Matching", "Enabled" if fuzzy_matching else "Disabled"],
                ["Dry Run", "Yes" if dry_run else "No"],
                ["Status", "SUCCESS" if success else "FAILED"],
            ],
            headers=["Setting", "Value"],
            title="PULL OPERATION",
        )

        # Display requested objects
        display_objects = object_names[:10]
        if len(object_names) > 10:
            display_objects.append(f"... and {len(object_names) - 10} more")
        self.logger.log_box("REQUESTED OBJECTS", display_objects)

        # Display operation results
        if success:
            if not dry_run:
                self.logger.result(f"Objects integrated using '{pull_mode}' mode")
            else:
                self.logger.notice("Dry run - no actual changes made")
        else:
            self.logger.error("Pull operation failed — check logs for details")

        self.logger.log_divider()

    def _display_hierarchy_analysis_results(
        self, reference_path: str, diff_result: dict
    ):
        """Display formatted hierarchy analysis results using log_table + log_box."""
        missing = diff_result.get("missing", [])
        extra = diff_result.get("extra", [])
        reparented = diff_result.get("reparented", [])
        fuzzy_matches = diff_result.get("fuzzy_matches", [])

        self.logger.log_divider()

        # Summary table
        total_diffs = len(missing) + len(extra) + len(reparented)
        status = "PERFECT MATCH" if total_diffs == 0 else f"{total_diffs} DIFFERENCES"

        self.log_table(
            data=[
                ["Reference", Path(reference_path).name],
                ["Missing", str(len(missing))],
                ["Extra", str(len(extra))],
                ["Reparented", str(len(reparented))],
                ["Fuzzy Matches", str(len(fuzzy_matches))],
                ["Status", status],
            ],
            headers=["Metric", "Value"],
            title="HIERARCHY ANALYSIS",
        )

        # Display missing objects if any
        if missing:
            display_missing = [f"  - {m}" for m in missing[:10]]
            if len(missing) > 10:
                display_missing.append(f"  ... and {len(missing) - 10} more")
            self.logger.log_box("MISSING OBJECTS", display_missing, level="WARNING")

        # Display extra objects if any
        if extra:
            display_extra = [f"  + {e}" for e in extra[:10]]
            if len(extra) > 10:
                display_extra.append(f"  ... and {len(extra) - 10} more")
            self.logger.log_box("EXTRA OBJECTS", display_extra, level="INFO")

        # Display reparented objects if any
        if reparented:
            display_reparented = [f"  ~ {r}" for r in reparented[:10]]
            if len(reparented) > 10:
                display_reparented.append(f"  ... and {len(reparented) - 10} more")
            self.logger.log_box(
                "REPARENTED OBJECTS", display_reparented, level="WARNING"
            )

        # Display fuzzy matches if any
        if fuzzy_matches:
            fuzzy_items = []
            for match in fuzzy_matches[:10]:
                if (
                    isinstance(match, dict)
                    and "original" in match
                    and "matched" in match
                ):
                    fuzzy_items.append(
                        f"  '{match['original']}' -> '{match['matched']}'"
                    )
                else:
                    fuzzy_items.append(f"  {match}")
            if len(fuzzy_matches) > 10:
                fuzzy_items.append(f"  ... and {len(fuzzy_matches) - 10} more")
            self.logger.log_box("FUZZY MATCHES", fuzzy_items, level="NOTICE")

        self.logger.log_divider()

    def populate_current_scene_tree(self, tree_widget):
        """Populate the current scene hierarchy tree."""
        # Skip refresh if we're importing reference data
        if getattr(self, "_importing_reference", False):
            self.logger.debug(
                "Skipping current scene tree refresh during reference import"
            )
            return

        try:
            # Get current scene name
            current_scene = pm.sceneName()
            scene_name = "Current Scene"
            if current_scene:
                scene_name = Path(current_scene).stem or "Untitled Scene"

            # Always clear tree before repopulating
            tree_widget.clear()

            # Get all transform nodes in the current scene
            all_transforms = pm.ls(type="transform")
            self.logger.debug(
                f"Current scene has {len(all_transforms)} total transforms"
            )

            # Filter out temporary import objects and reference objects
            filtered_transforms = []
            temp_objects = []
            reference_objects = []
            default_cameras = []
            for transform in all_transforms:
                node_name = transform.nodeName()

                # Skip Maya default cameras (they cause namespace issues and aren't useful for comparison)
                if self._is_default_maya_camera(transform):
                    default_cameras.append(node_name)
                    continue

                # Skip objects that belong to temporary namespaces but allow objects that were just moved
                # Only filter out objects that are still actively in temp_import namespaces
                if "temp_import_" in node_name and ":" in node_name:
                    # This is still in a temporary namespace, skip it
                    temp_objects.append(node_name)
                    continue

                # Skip objects that belong to reference namespaces (imported reference objects)
                if self._reference_namespaces and ":" in node_name:
                    namespace = node_name.split(":")[0]
                    if namespace in self._reference_namespaces:
                        reference_objects.append(node_name)
                        continue

                # This is a current scene object
                filtered_transforms.append(transform)

            self.logger.debug(
                f"After filtering: {len(filtered_transforms)} current scene transforms, "
                f"{len(temp_objects)} temp objects, {len(reference_objects)} reference objects, "
                f"{len(default_cameras)} default cameras excluded"
            )
            if temp_objects:
                self.logger.debug(
                    f"Temp objects: {temp_objects[:5]}{'...' if len(temp_objects) > 5 else ''}"
                )
            if reference_objects:
                self.logger.debug(
                    f"Reference objects: {reference_objects[:5]}{'...' if len(reference_objects) > 5 else ''}"
                )
            if default_cameras:
                self.logger.debug(f"Default cameras excluded: {default_cameras}")

            # Log some of the filtered transforms to see what we have
            if filtered_transforms:
                transform_names = [t.nodeName() for t in filtered_transforms[:10]]
                self.logger.debug(
                    f"Current scene objects: {transform_names}{'...' if len(filtered_transforms) > 10 else ''}"
                )

            if not filtered_transforms:
                tree_widget.setHeaderLabels([scene_name])
                open_item = tree_widget.create_item(["Open Scene"])
                font = open_item.font(0)
                font.setUnderline(True)
                open_item.setFont(0, font)
                open_item.setForeground(0, QtGui.QBrush(QtGui.QColor("#6699CC")))
                open_item.setData(0, QtCore.Qt.UserRole, "open_scene_placeholder")
                self.logger.debug(
                    "No transform objects found in current scene (excluding temp imports)."
                )
                return

            tree_widget.setHeaderLabels([scene_name])
            self.populate_tree_with_hierarchy(
                tree_widget, filtered_transforms, "current"
            )

        except Exception as e:
            self.logger.error(f"Error populating current scene tree: {e}")
            tree_widget.clear()
            tree_widget.setHeaderLabels(["Current Scene"])
            error_item = tree_widget.create_item([f"Error: {str(e)}"])

    def populate_reference_tree(
        self,
        tree_widget,
        reference_path: str = None,
        fuzzy_matching: bool = True,
        dry_run: bool = True,
    ):
        """Populate the reference hierarchy tree."""
        # Clear analysis cache when loading a new reference
        self._clear_analysis_cache()

        # Get reference scene name for header
        reference_name = "Reference Scene"
        if reference_path:
            reference_name = Path(reference_path).stem or "Reference Scene"

        tree_widget.setHeaderLabels([reference_name])

        if not reference_path:
            tree_widget.clear()
            info_item = tree_widget.create_item(["Browse for Reference Scene"])
            font = info_item.font(0)
            font.setUnderline(True)
            info_item.setFont(0, font)
            info_item.setForeground(0, QtGui.QBrush(QtGui.QColor("#6699CC")))
            info_item.setData(0, QtCore.Qt.UserRole, "browse_placeholder")
            return

        if not os.path.exists(reference_path):
            tree_widget.clear()
            info_item = tree_widget.create_item(["File Not Found"])
            return

        try:
            # Set flag to prevent current scene tree refresh during import
            self._importing_reference = True

            # Clear reference namespace tracking before loading new reference
            # This prevents old namespace tracking from affecting current scene object detection
            self._reference_namespaces = []

            # Import the reference scene just for hierarchy display
            self.logger.progress(
                f"Loading reference hierarchy from: {os.path.basename(reference_path)}"
            )

            temp_import = NamespaceSandbox(dry_run=False)

            self._redirect_logger(temp_import.logger)

            import_info = temp_import.import_with_namespace(
                reference_path, force_complete_import=True
            )

            if not import_info or not import_info.get("transforms"):
                self.logger.error(
                    "Failed to import reference file or no transforms found"
                )
                tree_widget.clear()
                error_item = tree_widget.create_item(["Failed to load reference"])
                return

            transforms = import_info.get("transforms", [])

            # Filter out default Maya cameras from tree display
            initial_count = len(transforms)
            filtered_transforms = [
                transform
                for transform in transforms
                if not self._is_default_maya_camera(transform)
            ]
            excluded_count = initial_count - len(filtered_transforms)

            self.logger.result(
                f"Imported {initial_count} transforms from reference "
                f"({excluded_count} default cameras excluded)"
            )

            # Debug: Log details about the imported transforms
            if filtered_transforms:
                # Show hierarchy structure details
                root_count = 0
                child_count = 0
                max_depth = 0

                for transform in filtered_transforms:
                    parent = transform.getParent()
                    if parent is None:
                        root_count += 1
                    else:
                        child_count += 1

                    # Calculate depth
                    depth = 0
                    current = transform
                    while current.getParent():
                        depth += 1
                        current = current.getParent()
                        if depth > 10:  # Safety break
                            break
                    max_depth = max(max_depth, depth)

                self.logger.debug(
                    f"Reference hierarchy structure: {root_count} roots, {child_count} children, max depth {max_depth}"
                )

                # Show some example transform names
                example_names = [t.nodeName() for t in filtered_transforms[:10]]
                self.logger.debug(
                    f"Example transforms: {example_names}{'...' if len(filtered_transforms) > 10 else ''}"
                )
            else:
                self.logger.warning("No transforms remaining after filtering")

            # Clear and populate the tree
            tree_widget.clear()
            self.populate_tree_with_hierarchy(
                tree_widget, filtered_transforms, "reference"
            )

            # Clean up the imported data since this is just for display
            temp_import.cleanup_all_namespaces()

            # Additional cleanup to ensure no temp namespaces are left behind
            self._cleanup_temp_namespaces()

            self.logger.debug("Reference tree populated successfully")

        except Exception as e:
            self.logger.error(f"Error loading reference hierarchy: {e}")
            tree_widget.clear()
            error_item = tree_widget.create_item([f"Error: {str(e)}"])
        finally:
            # Always clear the import flag
            self._importing_reference = False

    def populate_tree_with_hierarchy(self, tree_widget, objects, tree_type="current"):
        """Populate tree widget with proper Maya-style hierarchy."""
        try:
            # Don't clear the tree or set headers here - that's done in the calling method

            if not objects:
                # Show empty message
                empty_item = tree_widget.create_item([f"No {tree_type} objects"])
                return

            # Build hierarchy structure
            object_items, root_objects = tree_utils.build_hierarchy_structure(objects)

            if not object_items:
                empty_item = tree_widget.create_item(["No Objects"])
                return

            self.logger.debug(
                f"Tree building for {tree_type}: {len(object_items)} object items, {len(root_objects)} roots"
            )
            self.logger.debug(f"Root objects: {root_objects}")

            # Create tree items in proper hierarchy order
            created_items = {}

            def create_item_recursive(obj_key, parent_widget_item=None):
                """Recursively create tree items maintaining hierarchy."""
                if obj_key in created_items:
                    return created_items[obj_key]

                obj_info = object_items.get(obj_key)
                if not obj_info:
                    return None

                # Display short name; for reference trees strip namespace prefix
                display_name = obj_info["short_name"]
                if tree_type == "reference" and ":" in display_name:
                    display_name = display_name.split(":")[-1]

                # Create the tree widget item with clean display name
                item_data = [display_name, obj_info["type"]]

                tree_item = tree_widget.create_item(
                    item_data, obj_info["object"], parent_widget_item
                )

                # Store raw/original key (full DAG path) for later matching
                try:
                    tree_item._raw_name = obj_info[
                        "short_name"
                    ]  # original short name, may contain namespace
                except Exception:
                    pass

                # Set tooltip with full information
                if tree_type == "reference":
                    tree_item.setToolTip(
                        0, f"Full Name: {obj_key}\nType: {obj_info['type']}"
                    )

                created_items[obj_key] = tree_item

                # Find and create children (keyed by full path now)
                children = [
                    key
                    for key, info in object_items.items()
                    if info["parent"] == obj_key
                ]

                for child_key in sorted(children):  # Sort for consistent order
                    create_item_recursive(child_key, tree_item)

                return tree_item

            # Create root level items first
            for root_name in sorted(root_objects):
                create_item_recursive(root_name)

            # Expand first level by default
            tree_widget.expandToDepth(0)

            self.logger.debug(
                f"Populated {tree_type} tree with {len(objects)} objects in hierarchy."
            )

        except Exception as e:
            self.logger.error(f"Error populating {tree_type} tree with hierarchy: {e}")
            # Show error in tree
            error_item = tree_widget.create_item([f"Error: {str(e)}"])

    def apply_difference_formatting(self, tree001, tree000):
        """Apply color formatting to tree widgets based on hierarchy differences."""
        if not self._current_diff_result:
            return

        try:
            # Reset all item colors before applying new formatting
            for tree_widget in (tree001, tree000):
                self._clear_tree_colors(tree_widget)

            # Build tree indices once for proper path-based matching
            tree_matcher = tree_utils.TreePathMatcher()
            self._redirect_logger(tree_matcher.logger)

            cur_by_full, cur_by_clean, cur_by_last = tree_matcher.build_tree_index(
                tree001
            )
            ref_by_full, ref_by_clean, ref_by_last = tree_matcher.build_tree_index(
                tree000
            )

            # Apply formatting to current scene tree
            self.format_tree_differences(
                tree001, "current", tree_matcher, cur_by_full, cur_by_clean, cur_by_last
            )

            # Apply formatting to reference tree
            self.format_tree_differences(
                tree000,
                "reference",
                tree_matcher,
                ref_by_full,
                ref_by_clean,
                ref_by_last,
            )

        except Exception as e:
            self.logger.error(f"Error applying difference formatting: {e}")

    def _clear_tree_colors(self, tree_widget):
        """Remove foreground/background colors from every item in a tree widget."""
        try:
            default_brush = QtGui.QBrush()
            iterator = QtWidgets.QTreeWidgetItemIterator(tree_widget)
            while iterator.value():
                item = iterator.value()
                for col in range(tree_widget.columnCount()):
                    item.setForeground(col, default_brush)
                    item.setBackground(col, default_brush)
                iterator += 1
        except Exception as e:
            self.logger.debug(f"Error clearing tree colors: {e}")

    # Desaturated diff colors — (foreground, background)
    DIFF_COLORS = {
        "missing": ("#9E6B6B", "#3A2828"),  # muted red
        "extra": ("#8E8555", "#33301E"),  # muted gold
        "fuzzy": ("#6B8C9E", "#1E2D33"),  # muted teal
        "reparented": ("#A87EC8", "#2D1E3A"),  # muted purple
    }

    def format_tree_differences(
        self, tree_widget, tree_type, tree_matcher, by_full, by_clean, by_last
    ):
        """Format a specific tree widget based on differences.

        Uses TreePathMatcher indices for accurate path-based item lookup
        instead of naive leaf-name matching.
        """
        if not self._current_diff_result:
            return

        def _find_item(path):
            """Locate the best matching tree item for a diff path."""
            candidates, _ = tree_matcher.find_path_matches(
                path,
                by_full,
                by_clean,
                by_last,
                prefer_cleaned=True,
                strict=False,
            )
            return candidates[0] if candidates else None

        try:
            # Apply formatters based on difference type
            if tree_type == "current":
                # Current scene tree — extra items exist here but not in reference
                for extra_path in self._current_diff_result.get("extra", []):
                    item = _find_item(extra_path)
                    if item:
                        self._apply_diff_color(item, "extra")

                # Reparented items shown in current tree too
                for reparented in self._current_diff_result.get("reparented", []):
                    current_path = reparented.get("current_path", "")
                    if current_path:
                        item = _find_item(current_path)
                        if item:
                            self._apply_diff_color(item, "reparented")

            elif tree_type == "reference":
                # Reference tree — missing items exist here but not in current
                for missing_path in self._current_diff_result.get("missing", []):
                    item = _find_item(missing_path)
                    if item:
                        self._apply_diff_color(item, "missing")

                # Reparented items shown in reference tree too
                for reparented in self._current_diff_result.get("reparented", []):
                    ref_path = reparented.get("reference_path", "")
                    if ref_path:
                        item = _find_item(ref_path)
                        if item:
                            self._apply_diff_color(item, "reparented")

            # Apply fuzzy match formatting to both trees
            for fuzzy_match in self._current_diff_result.get("fuzzy_matches", []):
                current_name = fuzzy_match.get("current_name", "")
                target_name = fuzzy_match.get("target_name", "")

                if tree_type == "current" and current_name:
                    item = _find_item(current_name)
                    if item:
                        self._apply_diff_color(item, "fuzzy")

                elif tree_type == "reference" and target_name:
                    item = _find_item(target_name)
                    if item:
                        self._apply_diff_color(item, "fuzzy")

        except Exception as e:
            self.logger.error(f"Error formatting tree differences: {e}")

    def _apply_diff_color(self, item, diff_type: str):
        """Apply desaturated foreground/background to a tree item."""
        fg_hex, bg_hex = self.DIFF_COLORS.get(diff_type, (None, None))
        if not fg_hex:
            return
        for col in range(item.treeWidget().columnCount()):
            item.setForeground(col, QtGui.QBrush(QtGui.QColor(fg_hex)))
            item.setBackground(col, QtGui.QBrush(QtGui.QColor(bg_hex)))

    def find_tree_item_by_name(self, tree_widget, object_name):
        """Find a tree item by object name (first column).

        Handles pipe-separated hierarchy paths (e.g. ``GRP|CHILD|LEAF``)
        by extracting the leaf name for the search, since tree items only
        display the short node name.
        """
        try:
            # Diff results use pipe-separated full paths; tree items show leaf names
            leaf_name = (
                object_name.rsplit("|", 1)[-1] if "|" in object_name else object_name
            )

            items = tree_widget.findItems(
                leaf_name, QtCore.Qt.MatchExactly | QtCore.Qt.MatchRecursive, 0
            )
            return items[0] if items else None
        except Exception as e:
            self.logger.debug(f"Error finding tree item '{object_name}': {e}")
            return None

    def get_selected_tree_items(self, tree_widget):
        """Get selected items from a tree widget."""
        try:
            selected_items = tree_widget.selectedItems()
            return selected_items
        except Exception as e:
            self.logger.debug(f"Error getting selected tree items: {e}")
            return []

    def get_selected_object_names(self, tree_widget):
        """Extract object names from selected tree widget items."""
        return tree_utils.get_selected_object_names(tree_widget)

    def select_objects_in_maya(self, object_names: List[str]) -> int:
        """Select objects in Maya scene by name."""
        return select_objects_in_maya(object_names)

    def _store_tree_selection(self, tree_widget):
        """Store the current selection state of a tree widget."""
        try:
            selected_paths = []
            iterator = QtWidgets.QTreeWidgetItemIterator(tree_widget)
            while iterator.value():
                item = iterator.value()
                if item.isSelected():
                    # Build path to this item
                    path_parts = []
                    current = item
                    while current:
                        path_parts.insert(0, current.text(0))
                        current = current.parent()
                    selected_paths.append("|".join(path_parts))
                iterator += 1
            return selected_paths
        except Exception as e:
            self.logger.debug(f"Error storing tree selection: {e}")
            return []

    def _restore_tree_selection(self, tree_widget, selected_paths):
        """Restore selection state to a tree widget."""
        try:
            restored_count = 0
            tree_widget.clearSelection()

            for path in selected_paths:
                # Find item by path
                item = self._find_item_by_path(tree_widget, path)
                if item:
                    item.setSelected(True)
                    restored_count += 1

            return restored_count
        except Exception as e:
            self.logger.debug(f"Error restoring tree selection: {e}")
            return 0

    def _find_item_by_path(self, tree_widget, path):
        """Find a tree item by its hierarchical path."""
        try:
            path_parts = path.split("|")
            iterator = QtWidgets.QTreeWidgetItemIterator(tree_widget)

            while iterator.value():
                item = iterator.value()
                # Build path for this item
                item_path_parts = []
                current = item
                while current:
                    item_path_parts.insert(0, current.text(0))
                    current = current.parent()

                if item_path_parts == path_parts:
                    return item
                iterator += 1
            return None
        except Exception as e:
            self.logger.debug(f"Error finding item by path '{path}': {e}")
            return None

    def _get_tree_structure(self, tree_widget):
        """Get a simplified structure representation of the tree for comparison."""
        try:
            structure = []
            iterator = QtWidgets.QTreeWidgetItemIterator(tree_widget)

            while iterator.value():
                item = iterator.value()
                # Build path for this item
                path_parts = []
                current = item
                while current:
                    path_parts.insert(0, current.text(0))
                    current = current.parent()
                structure.append("|".join(path_parts))
                iterator += 1

            return sorted(structure)  # Sort for consistent comparison
        except Exception as e:
            self.logger.debug(f"Error getting tree structure: {e}")
            return []

    def refresh_trees(self):
        """Refresh both tree widgets with current hierarchy data. Store and restore selection if hierarchy unchanged."""
        # Store current selection before refresh
        current_scene_selection = self._store_tree_selection(self.ui.tree001)
        reference_selection = self._store_tree_selection(self.ui.tree000)

        # Store current hierarchy structure for comparison
        old_current_structure = self._get_tree_structure(self.ui.tree001)
        old_reference_structure = self._get_tree_structure(self.ui.tree000)

        # Populate current scene tree
        self.populate_current_scene_tree(self.ui.tree001)

        # For reference tree, only populate if we have a reference path
        reference_path = self.ui.txt001.text().strip()
        reference_populated = False

        if reference_path:
            fuzzy_matching = (
                self.ui.tb001.option_box.menu.chk_fuzzy_matching.isChecked()
                if hasattr(self.ui.tb001.menu, "chk_fuzzy_matching")
                else True
            )
            dry_run = (
                self.ui.chk002.isChecked() if getattr(self.ui, "chk002", None) else True
            )

            self.populate_reference_tree(
                self.ui.tree000, reference_path, fuzzy_matching, dry_run
            )
            reference_populated = True

        # Get new hierarchy structure after refresh
        new_current_structure = self._get_tree_structure(self.ui.tree001)
        new_reference_structure = self._get_tree_structure(self.ui.tree000)

        # Restore selection if hierarchy hasn't changed
        restored_count = 0

        if old_current_structure == new_current_structure and current_scene_selection:
            restored_count += self._restore_tree_selection(
                self.ui.tree001, current_scene_selection
            )

        if (
            reference_populated
            and old_reference_structure == new_reference_structure
            and reference_selection
        ):
            restored_count += self._restore_tree_selection(
                self.ui.tree000, reference_selection
            )

        if restored_count > 0:
            self.logger.success(
                f"Refreshed trees and restored {restored_count} selections (hierarchy unchanged)."
            )
        else:
            self.logger.result(
                "Refreshed trees (hierarchy may have changed — selection cleared)."
            )

    def log_diff_results(self):
        """Log detailed hierarchy difference analysis results using rich formatting."""
        if not self._current_diff_result:
            self.logger.error(
                "No diff results available. Please analyze hierarchies first."
            )
            return

        # NOTE: 'missing' = present in REFERENCE, absent in CURRENT
        #       'extra'   = present in CURRENT, absent in REFERENCE
        missing = self._current_diff_result.get("missing", [])  # reference-only
        extra = self._current_diff_result.get("extra", [])  # current-only
        reparented = self._current_diff_result.get("reparented", [])
        fuzzy_matches = self._current_diff_result.get("fuzzy_matches", [])

        self.logger.log_divider()

        if missing:
            items = [f"  - {m}" for m in missing[:10]]
            if len(missing) > 10:
                items.append(f"  ... and {len(missing) - 10} more")
            self.logger.log_box(
                f"MISSING IN CURRENT SCENE ({len(missing)})", items, level="WARNING"
            )

        if extra:
            items = [f"  + {e}" for e in extra[:10]]
            if len(extra) > 10:
                items.append(f"  ... and {len(extra) - 10} more")
            self.logger.log_box(
                f"EXTRA IN CURRENT SCENE ({len(extra)})", items, level="INFO"
            )

        if reparented:
            items = [f"  ~ {r}" for r in reparented[:10]]
            if len(reparented) > 10:
                items.append(f"  ... and {len(reparented) - 10} more")
            self.logger.log_box(
                f"REPARENTED OBJECTS ({len(reparented)})", items, level="WARNING"
            )

        if fuzzy_matches:
            fuzzy_rows = []
            for match in fuzzy_matches[:10]:
                current_name = match.get("current_name", "")
                target_name = match.get("target_name", "")
                fuzzy_rows.append([current_name, target_name])
            self.log_table(
                data=fuzzy_rows,
                headers=["Current", "Reference"],
                title=f"FUZZY MATCHES ({len(fuzzy_matches)})",
            )
            if len(fuzzy_matches) > 10:
                self.logger.notice(
                    f"  ... and {len(fuzzy_matches) - 10} more fuzzy matches"
                )

        if not missing and not extra and not reparented:
            self.logger.success("Hierarchies match perfectly!")
        else:
            total_diffs = len(missing) + len(extra) + len(reparented)
            self.logger.warning(f"Found {total_diffs} hierarchy differences")

        self.logger.log_divider()

    def get_recent_reference_scenes(self) -> List[str]:
        """Get recent reference scenes from settings."""
        recent_scenes = self.ui.settings.value("recent_reference_scenes", [])
        # Filter out non-existent files and return last 10
        return [scene for scene in recent_scenes if os.path.exists(scene)][-10:]

    def save_recent_reference_scene(self, scene_path: str):
        """Save reference scene to recent list."""
        if not scene_path:
            return

        scene_path = str(Path(scene_path).resolve())
        recent_scenes = self.ui.settings.value("recent_reference_scenes", [])

        # Remove if already exists to avoid duplicates
        if scene_path in recent_scenes:
            recent_scenes.remove(scene_path)

        # Add to end and keep only last 10
        recent_scenes.append(scene_path)
        recent_scenes = recent_scenes[-10:]

        # Save to settings
        self.ui.settings.setValue("recent_reference_scenes", recent_scenes)

        # Update combo box
        self.ui.txt001.option_box.menu.cmb002.clear()
        self.ui.txt001.option_box.menu.cmb002.add(
            recent_scenes, header="Recent Scenes:"
        )


class HierarchyManagerSlots(ptk.LoggingMixin):
    """Slots class for hierarchy management UI operations.

    This class provides the interface between the UI and the HierarchyManagerController.
    It manages UI event handling, widget initialization, and routes user interactions
    to the appropriate controller methods.

    Widget Responsibilities:
    - tree001: Current scene hierarchy tree widget
    - tree000: Reference/imported hierarchy tree widget
    - txt001: Reference scene path input
    - txt003: Log output display
    - Various buttons for operations and settings

    The slots class maintains no business logic - it purely routes UI events
    to the appropriate controller methods.
    """

    _log_level_options: Dict[str, Any] = {
        "Log Level: DEBUG": 10,
        "Log Level: INFO": 20,
        "Log Level: WARNING": 30,
        "Log Level: ERROR": 40,
    }

    def __init__(self, switchboard):
        super().__init__()

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.hierarchy_manager

        # Initialize UI components
        self.ui.txt001.setText("")  # Reference Scene Path
        self.ui.txt003.setText("")  # Log Output

        # Create controller
        self.controller = HierarchyManagerController(self)

        # Setup logging
        self.controller._redirect_logger(self.logger)

        # Footer default status
        if hasattr(self.ui, "footer") and self.ui.footer:
            self.ui.footer.setDefaultStatusText(
                "Set a reference scene path and click Diff or Pull to begin."
            )

        # Startup welcome text
        self._show_startup_text()

        # Re-show startup text when a new scene is opened or created
        self._scene_script_jobs = []
        for event in ("SceneOpened", "NewSceneOpened"):
            job_id = pm.scriptJob(event=[event, self._on_scene_changed])
            self._scene_script_jobs.append(job_id)

        # Kill scriptJobs when the UI is destroyed
        self.ui.destroyed.connect(self._cleanup_script_jobs)

        # Auto-refresh current scene tree on initialization
        self.controller.populate_current_scene_tree(self.ui.tree001)

    def _cleanup_script_jobs(self):
        """Kill registered Maya scriptJobs to prevent stale callbacks."""
        for job_id in self._scene_script_jobs:
            try:
                pm.scriptJob(kill=job_id, force=True)
            except Exception:
                pass
        self._scene_script_jobs.clear()

    def _on_scene_changed(self):
        """Reset UI state when a new scene is loaded."""
        self.controller._clear_analysis_cache()
        self.controller.populate_current_scene_tree(self.ui.tree001)
        self.ui.tree000.clear()
        self._show_startup_text()

    def _show_startup_text(self):
        """Display startup instructions in the log output widget."""
        scene = pm.sceneName()
        scene_name = Path(scene).name if scene else "Untitled"
        workspace = self.controller.workspace or "(not set)"

        lines = [
            '<span style="color:#aaa; font-size:11px;">'
            "<b>Hierarchy Manager</b><br>"
            f"Scene: {scene_name}<br>"
            f"Workspace: {workspace}<br><br>"
            "<b>Workflow:</b><br>"
            "&nbsp;&nbsp;1. Browse or enter a reference scene path<br>"
            "&nbsp;&nbsp;2. <b>Diff</b> &mdash; compare current scene against reference<br>"
            "&nbsp;&nbsp;3. Select objects in the reference tree<br>"
            "&nbsp;&nbsp;4. <b>Pull</b> &mdash; import selected objects into current scene<br><br>"
            "Right-click trees for more options. "
            "Enable <i>Dry Run</i> in the header menu to preview without changes."
            "</span>",
        ]
        self.ui.txt003.setHtml("\n".join(lines))

    def header_init(self, widget):
        """Initialize the header widget."""
        widget.menu.setTitle("Hierarchy Settings:")
        widget.menu.add(
            "QCheckBox",
            setText="Dry Run Mode",
            setObjectName="chk002",
            setChecked=True,
            setToolTip="Perform analysis without making actual changes.",
        )
        widget.menu.add(
            self.sb.registered_widgets.ComboBox,
            setObjectName="cmb001",
            add=self._log_level_options,
            setCurrentIndex=1,  # Default to INFO
            setToolTip="Set the log level.",
        )

        widget.menu.add("Separator", setTitle="About")
        widget.menu.add(
            "QPushButton",
            setText="Instructions",
            setObjectName="btn_instructions",
            setToolTip=(
                "Hierarchy Manager — Compare, diff, and synchronise scene\n"
                "hierarchies against a reference file.\n\n"
                "Workflow:\n"
                "  1. Enter or browse to a reference scene (.ma / .mb).\n"
                "  2. Press 'Diff' to compare the current scene against\n"
                "     the reference. Differences are highlighted in the\n"
                "     tree views and logged below.\n"
                "  3. Select objects in the reference tree that you want\n"
                "     to bring into the current scene.\n"
                "  4. Press 'Pull' to import the selected objects.\n\n"
                "Options:\n"
                "  • Enable Dry Run in this menu to preview changes\n"
                "    without modifying the scene.\n"
                "  • Right-click either tree for additional actions\n"
                "    (refresh, show differences, select in Maya).\n"
                "  • Use the log-level combo to control output verbosity."
            ),
        )

    def tree000_init(self, widget):
        """Initialize the reference/imported hierarchy tree widget."""
        if not hasattr(widget, "is_initialized") or not widget.is_initialized:
            # Enable multi-selection for auto-select functionality
            widget.setSelectionMode(
                self.sb.QtWidgets.QAbstractItemView.ExtendedSelection
            )

            widget.menu.setTitle("Reference Hierarchy:")
            widget.menu.add(
                "QPushButton",
                setText="Refresh Reference",
                setObjectName="b009",
                setToolTip="Refresh the reference hierarchy display.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Analyze Hierarchies",
                setObjectName="b012",
                setToolTip="Analyze and compare current scene with reference hierarchy.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Show Differences",
                setObjectName="b011",
                setToolTip="Highlight differences between hierarchies.",
            )

            # Open file browser when the placeholder item is clicked
            widget.itemClicked.connect(self._on_reference_tree_item_clicked)

            # Mark as initialized to prevent re-adding menu items
            widget.is_initialized = True

        # Don't auto-populate here - let explicit calls handle tree population
        # This prevents multiple imports during UI initialization/refresh
        if not widget.topLevelItemCount():
            # Only show placeholder if tree is empty
            widget.clear()
            widget.setHeaderLabels(["Reference Scene"])
            info_item = widget.create_item(["Browse for Reference Scene"])
            font = info_item.font(0)
            font.setUnderline(True)
            info_item.setFont(0, font)
            info_item.setForeground(
                0, self.sb.QtGui.QBrush(self.sb.QtGui.QColor("#6699CC"))
            )
            info_item.setData(0, self.sb.QtCore.Qt.UserRole, "browse_placeholder")

    def _on_reference_tree_item_clicked(self, item, column):
        """Handle clicks on the reference tree — opens file browser for the placeholder."""
        if item.data(0, self.sb.QtCore.Qt.UserRole) == "browse_placeholder":
            self.b003()

    def _on_current_tree_item_clicked(self, item, column):
        """Handle clicks on the current scene tree — opens a scene file for the placeholder."""
        if item.data(0, self.sb.QtCore.Qt.UserRole) == "open_scene_placeholder":
            scene_files = self.sb.file_dialog(
                file_types="Maya Files (*.ma *.mb);;FBX Files (*.fbx);;All Files (*.*)",
                title="Open Scene:",
                start_dir=self.controller.workspace,
            )
            if scene_files and len(scene_files) > 0:
                import maya.cmds as cmds

                cmds.file(scene_files[0], open=True, force=True)
                self.controller.refresh_trees()

    def tree001_init(self, widget):
        """Initialize the current scene hierarchy tree widget."""
        if not hasattr(widget, "is_initialized") or not widget.is_initialized:
            # Enable multi-selection for auto-select functionality
            widget.setSelectionMode(
                self.sb.QtWidgets.QAbstractItemView.ExtendedSelection
            )

            widget.menu.setTitle("Current Scene Hierarchy:")
            widget.menu.add(
                "QPushButton",
                setText="Refresh Current Scene",
                setObjectName="b005",
                setToolTip="Refresh the current scene hierarchy display.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Select Objects",
                setObjectName="b006",
                setToolTip="Select the checked objects in the Maya scene.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Expand All",
                setObjectName="b007",
                setToolTip="Expand all hierarchy items.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Collapse All",
                setObjectName="b008",
                setToolTip="Collapse all hierarchy items.",
            )

            # Open file dialog when the placeholder item is clicked
            widget.itemClicked.connect(self._on_current_tree_item_clicked)

            # Mark as initialized to prevent re-adding menu items
            widget.is_initialized = True

        # Always populate the current scene tree when initialized
        self.controller.populate_current_scene_tree(widget)

    def txt001_init(self, widget):
        """Initialize the reference scene path input."""
        widget.option_box.menu.add(
            "QPushButton",
            setText="Browse Reference Scene",
            setObjectName="b003",
            setToolTip="Browse for a reference scene file.",
        )

        # Recent reference scenes — option box button with history popup
        from uitk.widgets.optionBox.options.recent_values import RecentValuesOption

        self._recent_refs_option = RecentValuesOption(
            wrapped_widget=widget,
            settings_key="hierarchy_manager_recent_scenes",
            max_recent=10,
        )
        widget.option_box.add_option(self._recent_refs_option)

        # Seed from legacy QSettings if the plugin's store is empty
        if not self._recent_refs_option.recent_values:
            for scene in self.controller.get_recent_reference_scenes():
                self._recent_refs_option.add_recent_value(scene)

        # Connect text change signal for auto-refresh
        widget.textChanged.connect(self.txt001_textChanged)

    def tb001_init(self, widget):
        """Initialize the diff analysis toggle button with options menu."""
        widget.option_box.menu.setTitle("Diff Options:")

        # Add diff mode options
        widget.option_box.menu.add(
            self.sb.registered_widgets.ComboBox,
            setObjectName="cmb_diff_mode",
            setToolTip="Select diff analysis mode.",
        )
        diff_mode_options = {
            "Mode: Full Hierarchy Compare": "Full Hierarchy Compare",
            "Mode: Selected Objects Only": "Selected Objects Only",
            "Mode: Missing Objects Only": "Missing Objects Only",
            "Mode: Extra Objects Only": "Extra Objects Only",
        }
        widget.option_box.menu.cmb_diff_mode.add(diff_mode_options)

        # Add selection mode combobox (replaces individual checkboxes)
        widget.option_box.menu.add(
            self.sb.registered_widgets.ComboBox,
            setObjectName="cmb_selection_mode",
            setToolTip="Select how differences should be selected in trees.",
        )
        selection_mode_options = {
            "Select: All Differences": "all",
            "Select: Root Only": "root_only",
            "Select: Leaves Only": "leaves_only",
            "Select: No Auto-Selection": "none",
        }
        widget.option_box.menu.cmb_selection_mode.add(selection_mode_options)

        # Add remaining diff display options
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Expand Difference Nodes",
            setObjectName="chk_expand_diff",
            setChecked=True,
            setToolTip="Automatically expand nodes with differences.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Force Re-analysis",
            setObjectName="chk_force_reanalysis",
            setChecked=False,
            setToolTip="Force re-import and re-analysis even if reference was already analyzed.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Enable Fuzzy Matching",
            setObjectName="chk_fuzzy_matching",
            setChecked=True,
            setToolTip="Enable fuzzy name matching for improved object identification.",
        )

    def tb002_init(self, widget):
        """Initialize the pull objects toggle button with options menu."""
        widget.option_box.menu.setTitle("Pull Options:")

        # Add pull mode options
        widget.option_box.menu.add(
            self.sb.registered_widgets.ComboBox,
            setObjectName="cmb_pull_mode",
            setToolTip="Select how objects should be pulled.",
        )
        pull_mode_options = {
            "Mode: Add to Scene": "Add to Scene",
            "Mode: Merge Hierarchies": "Merge Hierarchies",
        }
        widget.option_box.menu.cmb_pull_mode.add(pull_mode_options)

        # Add pull children option
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Pull Children",
            setObjectName="chk_pull_children",
            setChecked=True,
            setToolTip="Include all children when pulling objects. When enabled, complete hierarchies are pulled; when disabled, only the selected objects are pulled.",
        )

    def b000(self):
        """Refresh tree widgets with current hierarchy data."""
        self.controller.refresh_trees()

    def tb001(self, state=None):
        """Toggle button for diff check with options option_box.menu."""
        self.ui.txt003.clear()

        reference_path = self.ui.txt001.text().strip()
        if not reference_path:
            self.logger.error("Please specify a reference scene path.")
            return

        # Verify we can access the reference file
        try:
            if not os.path.exists(reference_path):
                self.logger.error(f"Reference scene does not exist: {reference_path}")
                return
        except Exception as e:
            self.logger.error(f"Cannot access reference file: {e}")
            return

        # Get settings from UI
        fuzzy_matching = True  # Default value
        dry_run = self.ui.chk002.isChecked()

        # Get diff options from toggle button menu
        diff_mode = "Full Hierarchy Compare"  # Default
        selection_mode = "all"  # Default: select all differences
        expand_diff = True
        force_reanalysis = False

        if hasattr(self.ui, "tb001") and hasattr(self.ui.tb001, "menu"):
            if hasattr(self.ui.tb001.menu, "cmb_diff_mode"):
                diff_mode = (
                    self.ui.tb001.option_box.menu.cmb_diff_mode.currentData()
                    or diff_mode
                )
            if hasattr(self.ui.tb001.menu, "cmb_selection_mode"):
                selection_mode = (
                    self.ui.tb001.option_box.menu.cmb_selection_mode.currentData()
                    or selection_mode
                )
            if hasattr(self.ui.tb001.menu, "chk_expand_diff"):
                expand_diff = self.ui.tb001.option_box.menu.chk_expand_diff.isChecked()
            if hasattr(self.ui.tb001.menu, "chk_force_reanalysis"):
                force_reanalysis = (
                    self.ui.tb001.option_box.menu.chk_force_reanalysis.isChecked()
                )
            if hasattr(self.ui.tb001.menu, "chk_fuzzy_matching"):
                fuzzy_matching = (
                    self.ui.tb001.option_box.menu.chk_fuzzy_matching.isChecked()
                )

        # Parse selection mode
        auto_select = selection_mode != "none"
        select_root_only = selection_mode == "root_only"
        select_leaves_only = selection_mode == "leaves_only"

        # Debug: Log the selection options being used
        self.logger.debug(
            f"Selection mode: {selection_mode} (auto_select={auto_select}, root_only={select_root_only}, leaves_only={select_leaves_only})"
        )

        # Clear cache if force re-analysis is enabled
        if force_reanalysis:
            self.controller._clear_analysis_cache()
            self.logger.notice("Force re-analysis: cache cleared")

        self.logger.log_divider()
        self.logger.progress(f"Running diff analysis in '{diff_mode}' mode")

        # For full hierarchy compare, clear selection to trigger scene-wide comparison
        if diff_mode == "Full Hierarchy Compare":
            # Clear selection to force scene-wide analysis
            pm.select(clear=True)
            self.logger.debug(
                "Cleared selection for full scene hierarchy comparison (scene-wide mode)"
            )

        # Perform hierarchy analysis
        success = self.controller.analyze_hierarchies(
            reference_path, fuzzy_matching, dry_run
        )
        if not success:
            return

        # Log diff results
        self.controller.log_diff_results()

        # Apply analysis mode specific behavior
        if diff_mode == "Missing Objects Only":
            self.logger.debug("Focusing on missing objects only")
        elif diff_mode == "Extra Objects Only":
            self.logger.debug("Focusing on extra objects only")
        elif diff_mode == "Selected Objects Only":
            self.logger.debug("Analyzing selected objects only")

        # Ensure trees are populated for diff visualization
        # Only populate if not already populated or if structure has changed
        self._ensure_trees_populated_for_diff(reference_path, fuzzy_matching, dry_run)

        # Apply diff formatting to trees
        if self.controller._current_diff_result:
            self.controller.apply_difference_formatting(
                self.ui.tree001, self.ui.tree000
            )

        # Apply auto-select and expand options if trees are ready
        if auto_select or expand_diff:
            # Verify trees are populated before attempting auto-selection
            ref_item_count = self.count_tree_items(self.ui.tree000)
            cur_item_count = self.count_tree_items(self.ui.tree001)

            if ref_item_count > 0:
                self.logger.debug(
                    f"Trees populated: ref={ref_item_count}, cur={cur_item_count} - proceeding with auto-selection"
                )
                try:
                    self._apply_diff_options(
                        auto_select, expand_diff, select_root_only, select_leaves_only
                    )
                except Exception as e:
                    self.logger.error(f"Auto-selection failed: {e}")
            else:
                self.logger.warning(
                    f"Reference tree is empty ({ref_item_count} items) - skipping auto-selection."
                )
                self.logger.info(
                    "Trees may need to be refreshed manually. Try the Refresh button."
                )

        # Clean up any remaining temp namespaces after diff analysis
        self.controller._cleanup_temp_namespaces()

        # Update footer with diff summary
        if hasattr(self.ui, "footer") and self.ui.footer:
            diff = self.controller._current_diff_result
            if diff:
                n_miss = len(diff.get("missing", []))
                n_extra = len(diff.get("extra", []))
                n_repar = len(diff.get("reparented", []))
                total = n_miss + n_extra + n_repar
                if total == 0:
                    self.ui.footer.setText("Diff: hierarchies match.")
                else:
                    parts = []
                    if n_miss:
                        parts.append(f"{n_miss} missing")
                    if n_extra:
                        parts.append(f"{n_extra} extra")
                    if n_repar:
                        parts.append(f"{n_repar} reparented")
                    self.ui.footer.setText(f"Diff: {', '.join(parts)}.")

    def tb002(self, state=None):
        """Toggle button for pull objects with options menu."""
        self.ui.txt003.clear()

        # Validate that we have objects selected and a reference path
        object_names = self.controller.get_selected_object_names(self.ui.tree000)
        if not object_names:
            self.logger.error("Please select objects in the reference hierarchy tree.")
            return

        reference_path = self.ui.txt001.text().strip()
        if not reference_path:
            self.logger.error("Please specify a reference scene path.")
            return

        reference_path = self.ui.txt001.text().strip()
        fuzzy_matching = True  # Default value
        dry_run = self.ui.chk002.isChecked()

        # Get fuzzy matching setting from diff options menu
        if hasattr(self.ui, "tb001") and hasattr(self.ui.tb001, "menu"):
            if hasattr(self.ui.tb001.menu, "chk_fuzzy_matching"):
                fuzzy_matching = (
                    self.ui.tb001.option_box.menu.chk_fuzzy_matching.isChecked()
                )

        # Get pull options from toggle button menu
        pull_mode = "Add to Scene"  # Default
        pull_children = True  # Default

        if hasattr(self.ui, "tb002") and hasattr(self.ui.tb002, "menu"):
            if hasattr(self.ui.tb002.menu, "cmb_pull_mode"):
                pull_mode = (
                    self.ui.tb002.option_box.menu.cmb_pull_mode.currentData()
                    or pull_mode
                )
            if hasattr(self.ui.tb002.menu, "chk_pull_children"):
                pull_children = (
                    self.ui.tb002.option_box.menu.chk_pull_children.isChecked()
                )

        children_msg = "with children" if pull_children else "individual only"
        self.logger.notice(f"Pull: '{pull_mode}' mode, {children_msg}")

        success = self.controller.pull_objects(
            object_names,
            reference_path,
            fuzzy_matching,
            dry_run,
            pull_children=pull_children,
            pull_mode=pull_mode,
        )
        if success:
            # Force Maya to refresh its internal state
            from qtpy.QtWidgets import QApplication

            QApplication.processEvents()
            pm.refresh()

            # Verify objects exist in Maya after pull
            self.logger.progress("Verifying pulled objects exist in Maya...")

            # When pull_children is enabled, we only need to verify that the root hierarchies exist
            # The ObjectSwapper filters to root objects, so we should check what was actually processed
            if pull_children:
                # For pull_children mode, let's verify by checking the actual result
                # We'll count how many of the originally selected objects now exist in Maya
                successfully_pulled = 0
                root_objects_found = set()

                for obj_name in object_names:
                    clean_name = get_clean_node_name_from_string(obj_name)

                    if pm.objExists(clean_name):
                        successfully_pulled += 1
                        # Track which root objects we found
                        root_name = (
                            clean_name.split("|")[0]
                            if "|" in clean_name
                            else clean_name
                        )
                        root_objects_found.add(root_name)

                self.logger.debug(
                    f"Pull verification: {successfully_pulled}/{len(object_names)} requested objects now exist in Maya"
                )
                self.logger.debug(
                    f"Root hierarchies successfully imported: {sorted(root_objects_found)}"
                )
            else:
                # When pull_children is disabled, verify all selected objects individually
                verify_rows = []
                for obj_name in object_names:
                    clean_name = get_clean_node_name_from_string(obj_name)

                    if pm.objExists(clean_name):
                        obj = pm.PyNode(clean_name)
                        children = obj.getChildren(type="transform")
                        verify_rows.append([clean_name, "OK", str(len(children))])
                        if children:
                            child_names = [c.nodeName() for c in children[:3]]
                            self.logger.debug(
                                f"   Children of {clean_name}: {child_names}{'...' if len(children) > 3 else ''}"
                            )
                    elif pm.objExists(obj_name):
                        obj = pm.PyNode(obj_name)
                        children = obj.getChildren(type="transform")
                        verify_rows.append([obj_name, "OK", str(len(children))])
                        if children:
                            child_names = [c.nodeName() for c in children[:3]]
                            self.logger.debug(
                                f"   Children of {obj_name}: {child_names}{'...' if len(children) > 3 else ''}"
                            )
                    else:
                        verify_rows.append([obj_name, "MISSING", "-"])
                        self.logger.error(
                            f"{obj_name} (or {clean_name}) does not exist after pull!"
                        )

                if verify_rows:
                    self.log_table(
                        data=verify_rows,
                        headers=["Object", "Status", "Children"],
                        title="PULL VERIFICATION",
                    )

            self.logger.progress(
                "Refreshing current scene tree to show pulled objects..."
            )

            # Force refresh current scene tree to show pulled objects
            self.controller.populate_current_scene_tree(self.ui.tree001)

            # Also refresh the entire UI to ensure consistency
            self.b000()

            # Clean up any remaining temp namespaces after pull operation
            self.controller._cleanup_temp_namespaces()

            # For FBX files, repopulate the reference tree since temp namespaces were cleaned up
            reference_path = self.ui.txt001.text().strip()
            if reference_path and reference_path.lower().endswith(".fbx"):
                self.logger.debug(
                    "Repopulating reference tree after FBX pull operation..."
                )

                # Clear any stale reference namespace tracking before repopulating FBX tree
                # This prevents objects with same root names from being filtered out incorrectly
                self.controller._reference_namespaces = []
                self.logger.debug(
                    "Cleared reference namespace tracking before FBX tree repopulation"
                )

                fuzzy_matching = (
                    self.ui.tb001.option_box.menu.chk_fuzzy_matching.isChecked()
                    if hasattr(self.ui.tb001.menu, "chk_fuzzy_matching")
                    else True
                )
                dry_run = (
                    self.ui.chk002.isChecked()
                    if getattr(self.ui, "chk002", None)
                    else True
                )

                # Repopulate the reference tree for FBX files
                self.controller.populate_reference_tree(
                    self.ui.tree000, reference_path, fuzzy_matching, dry_run
                )

            if pull_children:
                self.logger.success(
                    f"Successfully pulled {len(root_objects_found)} root hierarchies (from {len(object_names)} selected objects) using '{pull_mode}' mode"
                )
                pull_summary = (
                    f"Pulled {len(root_objects_found)} hierarchies ({pull_mode})"
                )
            else:
                self.logger.success(
                    f"Successfully pulled {len(object_names)} objects using '{pull_mode}' mode"
                )
                pull_summary = f"Pulled {len(object_names)} objects ({pull_mode})"

            # Update footer
            if hasattr(self.ui, "footer") and self.ui.footer:
                if dry_run:
                    self.ui.footer.setText(f"Dry run: {pull_summary}")
                else:
                    self.ui.footer.setText(pull_summary)

    def b003(self):
        """Browse for reference scene file."""
        reference_file = self.sb.file_dialog(
            file_types="Maya Files (*.ma *.mb);;FBX Files (*.fbx);;All Files (*.*)",
            title="Select Reference Scene:",
            start_dir=self.controller.workspace,
        )

        if reference_file and len(reference_file) > 0:
            self.ui.txt001.setText(reference_file[0])
            # Record to recent scenes
            if hasattr(self, "_recent_refs_option"):
                self._recent_refs_option.record(reference_file[0])

    def b005(self):
        """Refresh current scene hierarchy tree."""
        self.controller.populate_current_scene_tree(self.ui.tree001)

    def b006(self):
        """Select checked objects in Maya scene."""
        object_names = self.controller.get_selected_object_names(self.ui.tree001)
        if not object_names:
            self.logger.warning("No objects selected in hierarchy tree.")
            return

        self.controller.select_objects_in_maya(object_names)

    def b007(self):
        """Expand all items in current scene tree."""
        self.ui.tree001.expandAll()

    def b008(self):
        """Collapse all items in current scene tree."""
        self.ui.tree001.collapseAll()

    def b009(self):
        """Refresh reference hierarchy tree."""
        reference_path = self.ui.txt001.text().strip()
        fuzzy_matching = True  # Default value
        if hasattr(self.ui, "tb001") and hasattr(self.ui.tb001, "menu"):
            if hasattr(self.ui.tb001.menu, "chk_fuzzy_matching"):
                fuzzy_matching = (
                    self.ui.tb001.option_box.menu.chk_fuzzy_matching.isChecked()
                )

        dry_run = getattr(self.ui, "chk002", None)
        dry_run = dry_run.isChecked() if dry_run else True

        self.controller.populate_reference_tree(
            self.ui.tree000, reference_path, fuzzy_matching, dry_run
        )

    def b011(self):
        """Show differences between hierarchies."""
        if not self.controller._current_diff_result:
            self.logger.error("Please analyze hierarchies first.")
            return

        self.controller.apply_difference_formatting(self.ui.tree001, self.ui.tree000)
        self.logger.debug("Applied difference highlighting to tree widgets.")

    def b012(self):
        """Analyze hierarchies and perform comparison."""
        self.ui.txt003.clear()

        reference_path = self.ui.txt001.text().strip()
        fuzzy_matching = True  # Default value
        if hasattr(self.ui, "tb001") and hasattr(self.ui.tb001, "menu"):
            if hasattr(self.ui.tb001.menu, "chk_fuzzy_matching"):
                fuzzy_matching = (
                    self.ui.tb001.option_box.menu.chk_fuzzy_matching.isChecked()
                )

        dry_run = self.ui.chk002.isChecked()
        log_level = self.ui.cmb001.currentData()

        # Set log level
        if log_level:
            self.logger.setLevel(log_level)

        success = self.controller.analyze_hierarchies(
            reference_path, fuzzy_matching, dry_run
        )
        if success:
            # Refresh tree widgets with new analysis
            self.b000()

            # Record reference path to recent list
            if hasattr(self, "_recent_refs_option"):
                self._recent_refs_option.record(reference_path)

    def txt001_textChanged(self, text):
        """Handle reference path text changes for auto-refresh."""
        text = text.strip()
        self.logger.debug(f"Reference path changed: {text}")

        if not text:
            # Clear reference tree when path is empty
            self.ui.tree000.clear()
            self.ui.tree000.setHeaderLabels(["Reference Scene"])
            info_item = self.ui.tree000.create_item(["Browse for Reference Scene"])
            font = info_item.font(0)
            font.setUnderline(True)
            info_item.setFont(0, font)
            info_item.setForeground(
                0, self.sb.QtGui.QBrush(self.sb.QtGui.QColor("#6699CC"))
            )
            info_item.setData(0, self.sb.QtCore.Qt.UserRole, "browse_placeholder")
            # Clear analysis cache
            self.controller._clear_analysis_cache()
            return

        if not os.path.exists(text):
            # Show file not found when path is invalid
            self.ui.tree000.clear()
            self.ui.tree000.setHeaderLabels(["Reference Scene"])
            error_item = self.ui.tree000.create_item(["File Not Found"])
            # Clear analysis cache since reference is invalid
            self.controller._clear_analysis_cache()
            return

        # Valid file path - auto-refresh the reference tree
        self.logger.debug(
            f"Auto-refreshing reference tree for: {os.path.basename(text)}"
        )

        # Get settings for refresh
        fuzzy_matching = True  # Default value
        if hasattr(self.ui, "tb001") and hasattr(self.ui.tb001, "menu"):
            if hasattr(self.ui.tb001.menu, "chk_fuzzy_matching"):
                fuzzy_matching = (
                    self.ui.tb001.option_box.menu.chk_fuzzy_matching.isChecked()
                )

        dry_run = getattr(self.ui, "chk002", None)
        dry_run = dry_run.isChecked() if dry_run else True

        # Clear analysis cache when reference changes
        self.controller._clear_analysis_cache()

        # Refresh the reference tree with new path
        self.controller.populate_reference_tree(
            self.ui.tree000, text, fuzzy_matching, dry_run
        )

    def _apply_diff_options(
        self,
        auto_select: bool,
        expand_diff: bool,
        select_root_only: bool = False,
        select_leaves_only: bool = False,
    ):
        """Apply auto-select and expand diff options to tree widgets.

        Args:
            auto_select: Whether to automatically select differences
            expand_diff: Whether to expand nodes with differences
            select_root_only: If True, only select root differences (condensed view).
                             If False, select all differences.
            select_leaves_only: If True, only select leaf differences (deepest level objects).
                               If False, select according to root_only setting.
        """
        if not self.controller._current_diff_result:
            return

        # Get tree widgets
        tree001 = self.ui.tree001  # Current scene tree
        tree000 = self.ui.tree000  # Reference tree

        # Auto-select differences in both trees
        if auto_select:
            tree001.clearSelection()
            tree000.clearSelection()
            selected_count = 0

            # Initialize tree path matcher
            tree_matcher = tree_utils.TreePathMatcher()
            self.controller._redirect_logger(tree_matcher.logger)

            # Build indices for both trees
            ref_by_full, ref_by_clean_full, ref_by_last = tree_matcher.build_tree_index(
                tree000
            )
            cur_by_full, cur_by_clean_full, cur_by_last = tree_matcher.build_tree_index(
                tree001
            )

            # Debug logging for tree indices
            tree_matcher.log_tree_index_debug(
                cur_by_full, cur_by_clean_full, cur_by_last, "current"
            )

            unresolved_extra = []  # Extra objects exist ONLY in current scene
            unresolved_missing = []  # Missing objects exist ONLY in reference

            # NOTE: Previous implementation inverted the tree search targets.
            # Correct logic:
            #   missing -> select in REFERENCE tree (tree000)
            #   extra   -> select in CURRENT tree (tree001)

            # -------------------- Select MISSING (reference tree) ---------------------
            missing_list = self.controller._current_diff_result.get("missing", [])

            # DEBUG: Show which tree each type of diff will be processed in
            extra_list_preview = self.controller._current_diff_result.get("extra", [])
            self.logger.debug(
                f"[TREE-DEBUG] Missing paths will be selected in REFERENCE TREE (tree000): {len(missing_list)} paths"
            )
            self.logger.debug(
                f"[TREE-DEBUG] Extra paths will be selected in CURRENT TREE (tree001): {len(extra_list_preview)} paths"
            )
            if missing_list:
                self.logger.debug(
                    f"[TREE-DEBUG] Missing path examples (reference tree): {missing_list[:3]}"
                )
            if extra_list_preview:
                self.logger.debug(
                    f"[TREE-DEBUG] Extra path examples (current tree): {extra_list_preview[:3]}"
                )

            # Check tree structure for debugging
            try:
                ref_item_count = self.count_tree_items(tree000)
                cur_item_count = self.count_tree_items(tree001)
                self.logger.debug(
                    f"[TREE-DEBUG] Reference tree (tree000) total items: {ref_item_count}"
                )
                self.logger.debug(
                    f"[TREE-DEBUG] Current tree (tree001) total items: {cur_item_count}"
                )
            except Exception as e:
                self.logger.debug(f"[TREE-DEBUG] Could not count tree items: {e}")

            # Optionally condense to root-only missing paths (avoid selecting every descendant when a parent is already missing)
            if select_root_only and missing_list:
                self.logger.debug(
                    f"[ROOT-ONLY] Applying root-only filter to {len(missing_list)} missing paths"
                )
                original_count = len(missing_list)
                # Sort by depth (shallow first) so parents appear before children
                missing_list_sorted = sorted(missing_list, key=lambda p: p.count("|"))
                condensed_missing = []
                seen_roots = set()
                for path in missing_list_sorted:
                    # If any existing condensed path is a strict prefix (parent) of this path, skip it
                    skip = False
                    for root in condensed_missing:
                        if path.startswith(root + "|"):
                            skip = True
                            break
                    if not skip:
                        condensed_missing.append(path)
                        seen_roots.add(path)
                missing_list = condensed_missing
                self.logger.debug(
                    f"[ROOT-ONLY] Condensed missing paths from {original_count} to {len(missing_list)} root paths"
                )
                if len(missing_list) < original_count:
                    self.logger.debug(
                        f"Root-only filtering reduced selection from {original_count} to {len(missing_list)} objects"
                    )
            elif select_root_only:
                self.logger.debug(
                    "[ROOT-ONLY] Root-only filter requested but no missing objects to filter"
                )

            # Optionally filter to leaf-only missing paths (deepest level objects)
            if select_leaves_only and not select_root_only and missing_list:
                original_count = len(missing_list)
                # Sort by depth (deepest first) so leaves appear before parents
                missing_list_sorted = sorted(
                    missing_list, key=lambda p: p.count("|"), reverse=True
                )
                leaf_missing = []
                for path in missing_list_sorted:
                    # If this path is a parent of any other path in the list, skip it
                    is_parent = False
                    for other_path in missing_list_sorted:
                        if other_path != path and other_path.startswith(path + "|"):
                            is_parent = True
                            break
                    if not is_parent:
                        leaf_missing.append(path)
                missing_list = leaf_missing
                if len(missing_list) != original_count:
                    self.logger.debug(
                        f"Filtered to leaf-only missing paths from {original_count} to {len(missing_list)} leaf paths"
                    )

            mode_desc = (
                "(root-only)"
                if select_root_only
                else "(leaves-only)" if select_leaves_only else "(all differences)"
            )
            self.logger.debug(
                f"[AUTO-SELECT] Missing paths (reference lookup): {len(missing_list)} {mode_desc}"
            )
            for missing_path in missing_list:
                self.logger.debug(
                    f"Processing missing path in REFERENCE TREE (tree000): '{missing_path}'"
                )

                # Check for leaf object patterns (debug only)
                if any(leaf in missing_path for leaf in ["_FRES_LOC", "_LOC", "_GEO"]):
                    self.logger.debug(f"Processing leaf object: {missing_path}")

                # Find matches in reference tree (prefer cleaned paths since it displays cleaned names)
                candidates, strategy = tree_matcher.find_path_matches(
                    missing_path,
                    ref_by_full,
                    ref_by_clean_full,
                    ref_by_last,
                    prefer_cleaned=True,
                    strict=not self.controller.hierarchy_manager.fuzzy_matching,
                )

                if not candidates:
                    self.logger.debug(
                        f"Missing path unresolved in reference tree: '{missing_path}'"
                    )
                    # Debug: Let's see what tree items we actually have for the last component
                    last_component = missing_path.split("|")[-1]
                    all_items_with_name = tree000.findItems(
                        last_component, QtCore.Qt.MatchRecursive
                    )
                    if all_items_with_name:
                        self.logger.debug(
                            f"  Found {len(all_items_with_name)} items with last component '{last_component}':"
                        )
                        for idx, item in enumerate(
                            all_items_with_name[:3]
                        ):  # Show first 3
                            # Build actual tree path for this item
                            path_parts = []
                            cur = item
                            while cur:
                                path_parts.insert(0, cur.text(0))
                                cur = cur.parent()
                            actual_path = "|".join(path_parts)
                            self.logger.debug(
                                f"    [{idx}] Tree item path: '{actual_path}'"
                            )
                            # Also check if it has raw name stored
                            raw_name = getattr(item, "_raw_name", "N/A")
                            self.logger.debug(f"        Raw name: '{raw_name}'")
                    else:
                        self.logger.debug(
                            f"  No tree items found with last component '{last_component}'"
                        )
                        # Show what last components we do have available
                        sample_items = []
                        it_sample = QtWidgets.QTreeWidgetItemIterator(tree000)
                        while it_sample.value() and len(sample_items) < 10:
                            item_sample = it_sample.value()
                            if not item_sample.parent():  # Only top-level for brevity
                                sample_items.append(item_sample.text(0))
                            it_sample += 1
                        self.logger.debug(
                            f"  Available top-level items: {sample_items}"
                        )

                    unresolved_missing.append(missing_path)
                    continue

                # Log matching results (simplified)
                self.logger.debug(
                    f"Missing path '{missing_path}' -> {len(candidates)} candidates via {strategy}"
                )

                for c in candidates:
                    if not c.isSelected():
                        c.setSelected(True)
                        selected_count += 1
                        self.logger.debug(f"Selected: '{c.text(0)}'")

                        # Ensure parent items are expanded so selection is visible
                        parent = c.parent()
                        while parent:
                            if not parent.isExpanded():
                                parent.setExpanded(True)
                            parent = parent.parent()
                    else:
                        self.logger.debug(
                            f"  ⚠ Already selected: '{missing_path}'"
                        )  # -------------------- Fuzzy child fallback for unresolved missing ---------------------
            if unresolved_missing and self.controller.hierarchy_manager.fuzzy_matching:
                self.logger.debug(
                    f"Attempting fuzzy child resolution for {len(unresolved_missing)} unresolved missing paths"
                )
                resolved_via_fuzzy = 0
                for missing_path in list(unresolved_missing):  # copy for safe removal
                    if "|" not in missing_path:
                        continue  # no parent to search under
                    parent_path = missing_path.rsplit("|", 1)[0]
                    last_component = missing_path.split("|")[-1]
                    parent_candidates, _ = tree_matcher.find_path_matches(
                        parent_path,
                        ref_by_full,
                        ref_by_clean_full,
                        ref_by_last,
                        prefer_cleaned=True,
                        strict=not self.controller.hierarchy_manager.fuzzy_matching,
                    )
                    if len(parent_candidates) != 1:
                        continue
                    parent_item = parent_candidates[0]
                    # Gather child display names (reference tree shows cleaned names)
                    child_names = [
                        parent_item.child(i).text(0)
                        for i in range(parent_item.childCount())
                    ]
                    if not child_names:
                        continue
                    # Use fuzzy match to try to map requested last component to an existing child
                    try:
                        matches = ptk.FuzzyMatcher.find_all_matches(
                            [last_component], child_names, score_threshold=0.6
                        )
                    except Exception as fm_err:
                        self.logger.debug(
                            f"Fuzzy match error for '{missing_path}': {fm_err}"
                        )
                        continue
                    if not matches:
                        continue
                    # find_all_matches returns Dict[str, Tuple[str, float]]
                    first_key = next(iter(matches))
                    matched_child, score = matches[first_key]
                    # Select the matched child
                    for i in range(parent_item.childCount()):
                        child_item = parent_item.child(i)
                        if child_item.text(0) == matched_child:
                            if not child_item.isSelected():
                                child_item.setSelected(True)
                                selected_count += 1
                                # Ensure parent items are expanded so selection is visible
                                parent = child_item.parent()
                                while parent:
                                    if not parent.isExpanded():
                                        parent.setExpanded(True)
                                    parent = parent.parent()
                            unresolved_missing.remove(missing_path)
                            resolved_via_fuzzy += 1
                            self.logger.debug(
                                f"Fuzzy child match resolved missing path '{missing_path}' -> '{matched_child}' (score {score:.2f})"
                            )
                            break
                if resolved_via_fuzzy:
                    self.logger.debug(
                        f"Resolved {resolved_via_fuzzy} missing paths via fuzzy child matching"
                    )

            # -------------------- Select EXTRA (current tree) ---------------------
            extra_list = self.controller._current_diff_result.get("extra", [])

            # Optionally condense extra paths similarly (though usually these are shallow already)
            if select_root_only and extra_list:
                self.logger.debug(
                    f"[ROOT-ONLY] Applying root-only filter to {len(extra_list)} extra paths"
                )
                original_extra = len(extra_list)
                extra_sorted = sorted(extra_list, key=lambda p: p.count("|"))
                condensed_extra = []
                for path in extra_sorted:
                    if any(path.startswith(root + "|") for root in condensed_extra):
                        continue
                    condensed_extra.append(path)
                extra_list = condensed_extra
                self.logger.debug(
                    f"[ROOT-ONLY] Condensed extra paths from {original_extra} to {len(extra_list)} root paths"
                )
                if len(extra_list) < original_extra:
                    self.logger.debug(
                        f"Root-only filtering reduced extra selection from {original_extra} to {len(extra_list)} objects"
                    )
            elif select_root_only:
                self.logger.debug(
                    "[ROOT-ONLY] Root-only filter requested but no extra objects to filter"
                )

            # Optionally filter to leaf-only extra paths (deepest level objects)
            if select_leaves_only and not select_root_only and extra_list:
                original_count = len(extra_list)
                # Sort by depth (deepest first) so leaves appear before parents
                extra_list_sorted = sorted(
                    extra_list, key=lambda p: p.count("|"), reverse=True
                )
                leaf_extra = []
                for path in extra_list_sorted:
                    # If this path is a parent of any other path in the list, skip it
                    is_parent = False
                    for other_path in extra_list_sorted:
                        if other_path != path and other_path.startswith(path + "|"):
                            is_parent = True
                            break
                    if not is_parent:
                        leaf_extra.append(path)
                extra_list = leaf_extra
                if len(extra_list) != original_count:
                    self.logger.debug(
                        f"Filtered to leaf-only extra paths from {original_count} to {len(extra_list)} leaf paths"
                    )

            mode_desc = (
                "(root-only)"
                if select_root_only
                else "(leaves-only)" if select_leaves_only else "(all differences)"
            )
            self.logger.debug(
                f"[AUTO-SELECT] Extra paths (current lookup): {len(extra_list)} {mode_desc}"
            )
            for extra_path in extra_list:
                self.logger.debug(
                    f"Processing extra path in CURRENT TREE (tree001): '{extra_path}'"
                )

                # Find matches in current tree (exact preferred; names are already clean)
                candidates, strategy = tree_matcher.find_path_matches(
                    extra_path,
                    cur_by_full,
                    cur_by_clean_full,
                    cur_by_last,
                    prefer_cleaned=False,
                    strict=not self.controller.hierarchy_manager.fuzzy_matching,
                )

                if not candidates:
                    self.logger.debug(
                        f"Extra path unresolved in current tree: '{extra_path}'"
                    )
                    unresolved_extra.append(extra_path)
                    continue

                tree_matcher.log_matching_debug(
                    extra_path, candidates, strategy, "Extra"
                )

                for c in candidates:
                    if not c.isSelected():
                        c.setSelected(True)
                        selected_count += 1
                        # Debug: Log which specific item was selected
                        item_path_parts = []
                        cur_item = c
                        while cur_item:
                            item_path_parts.insert(0, cur_item.text(0))
                            cur_item = cur_item.parent()
                        actual_item_path = "|".join(item_path_parts)
                        self.logger.debug(
                            f"  ✓ SELECTED: '{actual_item_path}' (strategy: {strategy})"
                        )

                        # Ensure parent items are expanded so selection is visible
                        parent = c.parent()
                        while parent:
                            if not parent.isExpanded():
                                parent.setExpanded(True)
                            parent = parent.parent()

            # -------------------- Select ALL CHILDREN of selected nodes ---------------------
            # After selecting the diff paths, also select all their children to ensure
            # the deepest visible nodes (like _GEO objects) are also selected
            children_selected = 0

            # Function to recursively select all children of a tree item
            def select_all_children(tree_item):
                count = 0
                for i in range(tree_item.childCount()):
                    child = tree_item.child(i)
                    if not child.isSelected():
                        child.setSelected(True)
                        count += 1
                    # Recursively select grandchildren
                    count += select_all_children(child)
                return count

            # Select children in reference tree
            it_ref = QtWidgets.QTreeWidgetItemIterator(tree000)
            while it_ref.value():
                item = it_ref.value()
                if item.isSelected() and item.childCount() > 0:
                    children_selected += select_all_children(item)
                it_ref += 1

            # Select children in current tree
            it_cur = QtWidgets.QTreeWidgetItemIterator(tree001)
            while it_cur.value():
                item = it_cur.value()
                if item.isSelected() and item.childCount() > 0:
                    children_selected += select_all_children(item)
                it_cur += 1

            if children_selected > 0:
                selected_count += children_selected
                self.logger.debug(
                    f"Child selection: Added {children_selected} child nodes"
                )

            # Update expected total to reflect condensed selection intention
            total_expected = len(extra_list) + len(missing_list)
            if unresolved_extra or unresolved_missing:
                self.logger.warning(
                    f"Unresolved diff paths (extra={len(unresolved_extra)}, missing={len(unresolved_missing)}) sample extra={unresolved_extra[:5]} missing={unresolved_missing[:5]}"
                )
            self.logger.debug(
                f"Auto-selected {selected_count} diff items (expected {total_expected}) unresolved={len(unresolved_extra)+len(unresolved_missing)}"
            )

            # Immediate selection verification before any further processing
            try:
                # Count actually selected items in reference tree immediately
                ref_selected_count = 0
                it_verify = QtWidgets.QTreeWidgetItemIterator(tree000)
                while it_verify.value():
                    if it_verify.value().isSelected():
                        ref_selected_count += 1
                    it_verify += 1

                # Count actually selected items in current tree
                cur_selected_count = 0
                it_cur_verify = QtWidgets.QTreeWidgetItemIterator(tree001)
                while it_cur_verify.value():
                    if it_cur_verify.value().isSelected():
                        cur_selected_count += 1
                    it_cur_verify += 1

                self.logger.debug(
                    f"[VERIFY] Actual UI selection state: ref_tree={ref_selected_count} cur_tree={cur_selected_count} total={ref_selected_count + cur_selected_count}"
                )

                # Note: selected_count now includes children, so actual count may be higher
                if ref_selected_count + cur_selected_count != selected_count:
                    self.logger.debug(
                        f"[VERIFY] Selection count difference (includes children): Reported={selected_count} Actual UI={ref_selected_count + cur_selected_count}"
                    )

                # Ensure proper tree focus and visibility
                if ref_selected_count > 0:
                    tree000.setFocus()
                    tree000.viewport().update()
                elif cur_selected_count > 0:
                    tree001.setFocus()
                    tree001.viewport().update()

                # Force a repaint to ensure selection is visually updated
                tree000.repaint()
                tree001.repaint()

                # Process any pending events before visual updates
                try:
                    self.sb.QtWidgets.QApplication.processEvents()
                except:
                    pass

                # ENHANCED FIX: Always ensure both trees get proper focus and updates
                # This fixes the issue where leaf objects aren't visually selected
                self.logger.debug(
                    f"[FOCUS] Ensuring both trees are properly updated..."
                )

                # Update both trees regardless of selection counts
                tree000.setFocus()
                tree000.viewport().update()
                tree000.repaint()
                self.sb.QtWidgets.QApplication.processEvents()

                tree001.setFocus()
                tree001.viewport().update()
                tree001.repaint()
                self.sb.QtWidgets.QApplication.processEvents()

                # Final focus on the tree with more selections, or current tree as default
                if ref_selected_count >= cur_selected_count:
                    tree000.setFocus()
                    self.logger.debug(
                        f"[FOCUS] Final focus set to reference tree (ref:{ref_selected_count} >= cur:{cur_selected_count})"
                    )
                else:
                    tree001.setFocus()
                    self.logger.debug(
                        f"[FOCUS] Final focus set to current tree (cur:{cur_selected_count} > ref:{ref_selected_count})"
                    )

                # Ensure trees are visible and focused
                if ref_selected_count > 0:
                    tree000.setFocus()
                    tree000.activateWindow()
                    tree000.raise_()

                # Multiple update methods to ensure visual refresh
                for tree in [tree000, tree001]:
                    tree.repaint()
                    tree.updateGeometry()
                    if hasattr(tree, "viewport") and tree.viewport():
                        tree.viewport().update()
                        tree.viewport().repaint()

            except Exception as verify_err:
                self.logger.error(
                    f"[VERIFY] Selection verification failed: {verify_err}"
                )

            # Post-selection debug: enumerate actually selected items in reference tree
            try:
                ref_selected = []
                it = QtWidgets.QTreeWidgetItemIterator(tree000)
                while it.value():
                    item = it.value()
                    if item.isSelected():
                        # Build path for clarity
                        path_parts = []
                        cur = item
                        while cur:
                            path_parts.insert(0, cur.text(0))
                            cur = cur.parent()
                        ref_selected.append("|".join(path_parts))
                    it += 1
                # Secondary fuzzy variant pass for unresolved missing paths (strip trailing digits on _GRP/_LOC segments)
                if (
                    unresolved_missing
                    and self.controller.hierarchy_manager.fuzzy_matching
                ):
                    import re as _re

                    fuzzy_variant_selected = 0
                    still_unresolved = []
                    pattern = _re.compile(r"(.+_(?:GRP|LOC))\d+$")
                    for miss_path in list(unresolved_missing):
                        components = miss_path.split("|")
                        variant_components = []
                        changed = False
                        for comp in components:
                            m = pattern.match(comp)
                            if m:
                                variant_components.append(m.group(1))
                                changed = True
                            else:
                                variant_components.append(comp)
                        if not changed:
                            still_unresolved.append(miss_path)
                            continue
                        variant_path = "|".join(variant_components)
                        # Try to find matches for the variant path
                        candidates, strategy = tree_matcher.find_path_matches(
                            variant_path,
                            ref_by_full,
                            ref_by_clean_full,
                            ref_by_last,
                            prefer_cleaned=True,
                            strict=False,
                        )
                        if candidates:
                            tree_matcher.log_matching_debug(
                                variant_path,
                                candidates,
                                strategy,
                                "Missing-FuzzyVariant",
                            )
                            for c in candidates:
                                if not c.isSelected():
                                    c.setSelected(True)
                                    selected_count += 1
                                    fuzzy_variant_selected += 1
                                    # Ensure parent items are expanded so selection is visible
                                    parent = c.parent()
                                    while parent:
                                        if not parent.isExpanded():
                                            parent.setExpanded(True)
                                        parent = parent.parent()
                        else:
                            still_unresolved.append(miss_path)
                    if fuzzy_variant_selected:
                        self.logger.debug(
                            f"Fuzzy variant pass selected {fuzzy_variant_selected} additional missing items"
                        )
                        unresolved_missing = still_unresolved
                        # Rebuild enumeration after fuzzy variant pass
                        ref_selected = []
                        it_rv = QtWidgets.QTreeWidgetItemIterator(tree000)
                        while it_rv.value():
                            item_rv = it_rv.value()
                            if item_rv.isSelected():
                                path_parts_rv = []
                                cur_rv = item_rv
                                while cur_rv:
                                    path_parts_rv.insert(0, cur_rv.text(0))
                                    cur_rv = cur_rv.parent()
                                ref_selected.append("|".join(path_parts_rv))
                            it_rv += 1
                elif unresolved_missing:
                    self.logger.debug(
                        f"[AUTO-SELECT] Skipping fuzzy variant pass (fuzzy matching disabled) unresolved_missing={len(unresolved_missing)}"
                    )
                self.logger.debug(
                    f"[AUTO-SELECT] Reference tree selected items ({len(ref_selected)} of {total_expected} expected leaf diffs): {ref_selected[:30]}{'...' if len(ref_selected) > 30 else ''}"
                )
                if unresolved_missing or unresolved_extra:
                    self.logger.debug(
                        f"[AUTO-SELECT] Remaining unresolved paths -> missing: {unresolved_missing[:10]}{'...' if len(unresolved_missing) > 10 else ''} extra: {unresolved_extra[:10]}{'...' if len(unresolved_extra) > 10 else ''}"
                    )
            except Exception as sel_dbg_err:
                self.logger.debug(f"Selection debug enumeration failed: {sel_dbg_err}")

        # Expand nodes with differences
        if expand_diff:
            expanded_count = 0

            # Correct expansion logic:
            #   missing (reference-only) -> expand in REFERENCE tree (tree000)
            #   extra   (current-only)   -> expand in CURRENT tree (tree001)

            # Expand reference-only (missing) paths in reference tree
            for missing_path in self.controller._current_diff_result.get("missing", []):
                node_name = missing_path.split("|")[-1]
                items = tree000.findItems(node_name, self.sb.QtCore.Qt.MatchRecursive)
                for item in items:
                    if self._matches_hierarchy_path(item, missing_path):
                        parent = item.parent()
                        while parent:
                            if not parent.isExpanded():
                                parent.setExpanded(True)
                                expanded_count += 1
                            parent = parent.parent()

            # Expand current-only (extra) paths in current tree
            for extra_path in self.controller._current_diff_result.get("extra", []):
                node_name = extra_path.split("|")[-1]
                items = tree001.findItems(node_name, self.sb.QtCore.Qt.MatchRecursive)
                for item in items:
                    if self._matches_hierarchy_path(item, extra_path):
                        parent = item.parent()
                        while parent:
                            if not parent.isExpanded():
                                parent.setExpanded(True)
                                expanded_count += 1
                            parent = parent.parent()

            if expanded_count > 0:
                self.logger.debug(
                    f"Expanded {expanded_count} nodes showing differences"
                )

        # Final selection summary for user clarity
        if auto_select:
            try:
                final_ref_count = 0
                it_final = QtWidgets.QTreeWidgetItemIterator(tree000)
                while it_final.value():
                    if it_final.value().isSelected():
                        final_ref_count += 1
                    it_final += 1

                final_cur_count = 0
                it_final_cur = QtWidgets.QTreeWidgetItemIterator(tree001)
                while it_final_cur.value():
                    if it_final_cur.value().isSelected():
                        final_cur_count += 1
                    it_final_cur += 1

                if final_ref_count > 0:
                    self.logger.success(
                        f"✓ AUTO-SELECT COMPLETE: {final_ref_count} items selected in REFERENCE tree (left panel). "
                        f"Use 'Pull' button to import selected objects to current scene."
                    )
                elif final_cur_count > 0:
                    self.logger.success(
                        f"✓ AUTO-SELECT COMPLETE: {final_cur_count} items selected in CURRENT tree (right panel)."
                    )
                else:
                    self.logger.warning(
                        "No items remain selected after auto-select process."
                    )

            except Exception as final_err:
                self.logger.debug(f"Final selection summary failed: {final_err}")

    def _matches_hierarchy_path(self, tree_item, full_path: str) -> bool:
        """Check if a tree item matches the full hierarchy path."""
        try:
            # Build path from tree item up to root
            item_path_parts = []
            current = tree_item
            while current:
                item_path_parts.insert(0, current.text(0))
                current = current.parent()

            item_path = "|".join(item_path_parts)

            # Handle namespace prefixes in the full_path
            # Convert "temp_import_123:OBJECT|temp_import_123:CHILD" to "OBJECT|CHILD"
            clean_full_path = full_path
            if ":" in full_path:
                path_parts = full_path.split("|")
                clean_parts = []
                for part in path_parts:
                    clean_part = part.split(":")[-1] if ":" in part else part
                    clean_parts.append(clean_part)
                clean_full_path = "|".join(clean_parts)

            # Try multiple matching strategies
            return (
                item_path == clean_full_path  # Exact match
                or item_path == full_path  # Direct match with namespace
                or item_path.endswith(clean_full_path)  # Suffix match
                or clean_full_path.endswith(item_path)  # Item is part of path
                or item_path.split("|")[-1]
                == clean_full_path.split("|")[-1]  # Last node matches
            )
        except:
            return False

    def _matches_node_name(self, tree_item, target_name: str) -> bool:
        """Simple node name matching for comprehensive selection."""
        try:
            item_name = tree_item.text(0)
            # Remove namespace prefix if present
            clean_item_name = (
                item_name.split(":")[-1] if ":" in item_name else item_name
            )
            return clean_item_name == target_name
        except:
            return False

    def _ensure_trees_populated_for_diff(self, reference_path, fuzzy_matching, dry_run):
        """Ensure both trees are populated for diff visualization.

        Always repopulates both trees so that diff formatting and expansion
        reflect the latest scene state and reference file.

        Saves and restores the analysis cache because populate_reference_tree
        calls _clear_analysis_cache() internally.
        """
        try:
            # Save analysis results — populate_reference_tree clears the cache
            saved_diff = self.controller._current_diff_result
            saved_hm = self.controller.hierarchy_manager
            saved_ns = getattr(self.controller, "_reference_namespaces", [])

            # Always repopulate current scene tree
            self.controller.populate_current_scene_tree(self.ui.tree001)
            self.logger.debug("Populated current scene tree for diff visualization")

            # Always repopulate reference tree
            if reference_path:
                self.controller.populate_reference_tree(
                    self.ui.tree000, reference_path, fuzzy_matching, dry_run
                )
                self.logger.debug("Populated reference tree for diff visualization")

            # Restore analysis results so apply_difference_formatting can use them
            self.controller._current_diff_result = saved_diff
            self.controller.hierarchy_manager = saved_hm
            self.controller._reference_namespaces = saved_ns

        except Exception as e:
            self.logger.debug(f"Error ensuring trees populated for diff: {e}")

    def count_tree_items(self, tree_widget):
        """Count total items in a tree widget for debugging"""
        try:
            count = 0
            iterator = QtWidgets.QTreeWidgetItemIterator(tree_widget)
            while iterator.value():
                count += 1
                iterator += 1
            return count
        except Exception as e:
            return f"Error: {e}"


# Export the main classes
__all__ = ["HierarchyManagerSlots", "HierarchyManagerController"]

# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    ...

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
