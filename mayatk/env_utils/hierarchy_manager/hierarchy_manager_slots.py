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
from mayatk.ui_utils.node_icons import NodeIcons


class HierarchyManagerController(ptk.LoggingMixin):
    """Controller for hierarchy management operations."""

    def __init__(self, slots_instance, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)
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

        # Per-tree ignored path sets
        self._ignored_ref_paths = set()  # Ignored paths in reference tree (tree000)
        self._ignored_cur_paths = set()  # Ignored paths in current tree (tree001)

        # Cached reference import — avoids re-importing the same file for
        # tree display + diff analysis within a single session.
        # Structure: {"path": str, "sandbox": NamespaceSandbox, "transforms": list} or None
        self._cached_reference_import = None

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
        self,
        reference_path: str,
        fuzzy_matching: bool = True,
        dry_run: bool = True,
        filter_meshes: bool = False,
    ) -> bool:
        """Analyze hierarchies and perform comparison.

        Uses the cached reference import from ``_import_reference_cached()``
        to avoid re-importing the file when the reference tree was already
        populated with the same path.

        Args:
            reference_path: Path to the reference scene file
            fuzzy_matching: Enable fuzzy name matching
            dry_run: Perform analysis without making changes
            filter_meshes: Exclude mesh-bearing transforms from comparison

        Returns:
            bool: True if analysis was successful
        """
        if not reference_path:
            self.logger.error("Please specify a reference scene path.")
            return False

        if not os.path.exists(reference_path):
            self.logger.error(f"Reference scene does not exist: {reference_path}")
            return False

        try:
            self.logger.progress(
                f"Analyzing hierarchy differences with: {os.path.basename(reference_path)}"
            )

            # Ensure we have a valid comparison setup
            selected = pm.selected(type="transform")
            if not selected:
                self.logger.notice(
                    "No objects selected — performing full scene hierarchy comparison"
                )
            else:
                self.logger.debug(
                    f"Using {len(selected)} pre-selected objects for hierarchy comparison"
                )

            # Reuse cached import or perform a fresh one.
            non_default_camera_reference_transforms = self._import_reference_cached(
                reference_path
            )
            if not non_default_camera_reference_transforms:
                self.logger.error(
                    "Failed to import reference file or no transforms found"
                )
                return False

            # Obtain the sandbox from the cache for the HierarchyManager.
            cached_sandbox = (
                self._cached_reference_import.get("sandbox")
                if self._cached_reference_import
                else None
            )

            # Create hierarchy manager for comparison analysis
            self.hierarchy_manager = HierarchyManager(
                import_manager=cached_sandbox,
                fuzzy_matching=fuzzy_matching,
                dry_run=dry_run,
            )

            self._redirect_logger(self.hierarchy_manager.logger)

            self._current_diff_result = self.hierarchy_manager.analyze_hierarchies(
                current_tree_root="SCENE_WIDE_MODE",
                reference_objects=non_default_camera_reference_transforms,
                filter_meshes=filter_meshes,
                filter_cameras=False,
                filter_lights=False,
            )

            # Do NOT clean up the cached import here — it may still be
            # needed for tree display or subsequent operations.

            if not self._current_diff_result:
                self.logger.warning("Analysis returned no results")
                return False

            return True

        except Exception as e:
            self.logger.error(f"Error during hierarchy analysis: {e}")

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
        self.clear_ignored_paths()
        self._cleanup_cached_reference_import()
        self.logger.debug("Analysis cache cleared (ignore paths also reset)")

    def _on_window_hidden(self):
        """Unload reference scene data when the hierarchy manager is hidden."""
        self._cleanup_cached_reference_import()
        # Clear the reference tree
        if hasattr(self, "ui") and hasattr(self.ui, "tree000"):
            self.ui.tree000.clear()
        self.logger.debug("Reference scene unloaded on window hide.")

    def _cleanup_cached_reference_import(self):
        """Clean up the cached reference import (namespace + transforms)."""
        if self._cached_reference_import is not None:
            sandbox = self._cached_reference_import.get("sandbox")
            if sandbox is not None:
                try:
                    sandbox.cleanup_all_namespaces()
                except Exception as e:
                    self.logger.debug(f"Cached reference cleanup failed: {e}")
            self._cached_reference_import = None
            self._cleanup_temp_namespaces()

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

    def repair_hierarchies(
        self,
        create_stubs: bool = True,
        quarantine_extras: bool = True,
        quarantine_group: str = "_QUARANTINE",
        skip_animated: bool = False,
        fix_reparented: bool = True,
        fix_fuzzy_renames: bool = True,
        dry_run: bool = True,
    ) -> bool:
        """Run repair operations on the current scene to match reference hierarchy.

        Requires a prior successful diff analysis.  Ignored paths are
        automatically excluded.

        Args:
            create_stubs: Create empty transforms for missing items.
            quarantine_extras: Move extra items to a _QUARANTINE group.
            quarantine_group: Name of the quarantine group.
            skip_animated: Skip quarantining extras under animated ancestors.
            fix_reparented: Move reparented nodes to reference position.
            fix_fuzzy_renames: Rename fuzzy-matched nodes to reference names.
            dry_run: Preview without changes.

        Returns:
            True if any repairs were applied (or would be in dry-run).
        """
        if not self.hierarchy_manager or not self._current_diff_result:
            self.logger.error("Please run a diff analysis first.")
            return False

        effective = self._filter_ignored_from_diff()

        # Temporarily set dry_run without leaking state
        prev_dry_run = self.hierarchy_manager.dry_run
        self.hierarchy_manager.dry_run = dry_run

        results = {}
        try:
            # Fuzzy renames FIRST — renaming a parent (e.g. GRP → GRP1) makes
            # its children resolvable, preventing stub creation from claiming
            # the target name and causing Maya auto-suffix collisions (GRP2).
            if fix_fuzzy_renames and effective.get("fuzzy_matches"):
                self.logger.progress(
                    f"Renaming {len(effective['fuzzy_matches'])} fuzzy-matched items..."
                )
                results["renamed"] = self.hierarchy_manager.fix_fuzzy_renames(
                    effective["fuzzy_matches"]
                )

                # After renaming a parent (e.g. GRP → GRP1), children that
                # were "extra" under the old name are now correctly parented
                # under the new name.  Remove them from the extras list so
                # quarantine doesn't move them away.
                if results["renamed"]:
                    fuzzy_cur_prefixes = [
                        f["current_name"]
                        for f in effective["fuzzy_matches"]
                        if f.get("current_name")
                    ]
                    effective["extra"] = [
                        p
                        for p in effective["extra"]
                        if not any(
                            p.startswith(prefix + "|") for prefix in fuzzy_cur_prefixes
                        )
                    ]

                    # Also remove children from the missing list — the fuzzy
                    # rename resolved the parent so children exist now.
                    fuzzy_ref_prefixes = [
                        f["target_name"]
                        for f in effective["fuzzy_matches"]
                        if f.get("target_name")
                    ]
                    effective["missing"] = [
                        p
                        for p in effective["missing"]
                        if not any(
                            p.startswith(prefix + "|") for prefix in fuzzy_ref_prefixes
                        )
                    ]

            if create_stubs and effective["missing"]:
                self.logger.progress(
                    f"Creating stubs for {len(effective['missing'])} missing items..."
                )
                results["stubs"] = self.hierarchy_manager.create_stubs(
                    effective["missing"]
                )

            if quarantine_extras and effective["extra"]:
                self.logger.progress(
                    f"Quarantining {len(effective['extra'])} extra items..."
                )
                results["quarantined"] = self.hierarchy_manager.quarantine_extras(
                    group=quarantine_group,
                    paths=effective["extra"],
                    skip_animated=skip_animated,
                )

            if fix_reparented and effective["reparented"]:
                self.logger.progress(
                    f"Fixing {len(effective['reparented'])} reparented items..."
                )
                results["reparented"] = self.hierarchy_manager.fix_reparented(
                    effective["reparented"]
                )
        finally:
            self.hierarchy_manager.dry_run = prev_dry_run

        total = sum(len(v) for v in results.values())
        if total == 0:
            self.logger.notice("No repairs needed or no applicable items found.")
            return False

        mode = "DRY-RUN" if dry_run else "APPLIED"
        parts = []
        for key, items in results.items():
            if items:
                parts.append(f"{key}: {len(items)}")
        self.logger.result(f"[{mode}] Repairs — {', '.join(parts)}")

        # Invalidate stale cache after live changes
        if not dry_run:
            self._clear_analysis_cache()

        return True

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
            tree_widget.blockSignals(True)
            try:
                self.populate_tree_with_hierarchy(
                    tree_widget, filtered_transforms, "current"
                )
            finally:
                tree_widget.blockSignals(False)

        except Exception as e:
            self.logger.error(f"Error populating current scene tree: {e}")
            tree_widget.clear()
            tree_widget.setHeaderLabels(["Current Scene"])
            error_item = tree_widget.create_item([f"Error: {str(e)}"])

    def _import_reference_cached(self, reference_path: str):
        """Import a reference file, reusing a cached import when the path matches.

        Returns the list of (non-default-camera) transforms, or ``None`` on
        failure.  The import is kept alive in ``_cached_reference_import`` so
        that subsequent operations (tree display, diff analysis) can reuse
        the same transforms without re-importing the file.
        """
        resolved = str(Path(reference_path).resolve())

        # Reuse cached import if the path hasn't changed and transforms are still valid.
        if self._cached_reference_import is not None:
            cached_path = self._cached_reference_import.get("path")
            cached_transforms = self._cached_reference_import.get("transforms", [])
            if cached_path == resolved and cached_transforms:
                # Quick validity check — first node must still exist.
                try:
                    if cached_transforms[0].exists():
                        self.logger.debug(
                            f"Reusing cached reference import ({len(cached_transforms)} transforms)"
                        )
                        return cached_transforms
                except Exception:
                    pass
                # Stale cache — fall through to fresh import.
                self.logger.debug("Cached reference import is stale, re-importing")

        # Clean up any previous cached import before creating a new one.
        self._cleanup_cached_reference_import()

        self.logger.progress(f"Importing reference: {os.path.basename(reference_path)}")

        sandbox = NamespaceSandbox(dry_run=False)
        self._redirect_logger(sandbox.logger)

        import_info = sandbox.import_with_namespace(
            reference_path, force_complete_import=True
        )
        if not import_info or not import_info.get("transforms"):
            self.logger.error("Failed to import reference file or no transforms found")
            return None

        all_transforms = import_info.get("transforms", [])

        # Filter out default Maya cameras.
        filtered = [t for t in all_transforms if not self._is_default_maya_camera(t)]
        excluded_count = len(all_transforms) - len(filtered)

        self.logger.result(
            f"Imported {len(all_transforms)} transforms from reference "
            f"({excluded_count} default cameras excluded)"
        )

        # Cache for reuse.
        self._cached_reference_import = {
            "path": resolved,
            "sandbox": sandbox,
            "transforms": filtered,
        }

        # Extract reference namespaces for UI filtering.
        self._reference_namespaces = sorted(
            {t.nodeName().split(":")[0] for t in all_transforms if ":" in t.nodeName()}
        )
        if self._reference_namespaces:
            self.logger.debug(
                f"Tracking reference namespaces for UI filtering: "
                f"{', '.join(self._reference_namespaces)}"
            )

        return filtered

    def populate_reference_tree(
        self,
        tree_widget,
        reference_path: str = None,
        fuzzy_matching: bool = True,
        dry_run: bool = True,
    ):
        """Populate the reference hierarchy tree."""
        # Only invalidate the cache when the reference path actually changes.
        if reference_path:
            resolved = str(Path(reference_path).resolve())
            cached_path = (
                self._cached_reference_import.get("path")
                if self._cached_reference_import
                else None
            )
            if cached_path != resolved:
                self._clear_analysis_cache()
        else:
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

            filtered_transforms = self._import_reference_cached(reference_path)
            if not filtered_transforms:
                tree_widget.clear()
                error_item = tree_widget.create_item(["Failed to load reference"])
                return

            # Debug: Log details about the imported transforms
            if self.logger.isEnabledFor(10):  # DEBUG level
                root_count = sum(
                    1 for t in filtered_transforms if t.getParent() is None
                )
                child_count = len(filtered_transforms) - root_count
                self.logger.debug(
                    f"Reference hierarchy structure: {root_count} roots, "
                    f"{child_count} children"
                )
                example_names = [t.nodeName() for t in filtered_transforms[:10]]
                self.logger.debug(
                    f"Example transforms: {example_names}"
                    f"{'...' if len(filtered_transforms) > 10 else ''}"
                )

            # Clear and populate the tree
            tree_widget.clear()
            self.populate_tree_with_hierarchy(
                tree_widget, filtered_transforms, "reference"
            )

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

                # Set node-type icon from Maya's resource system
                icon = NodeIcons.get_icon(obj_key)
                if icon:
                    tree_item.setIcon(0, icon)

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
            candidates, strategy = tree_matcher.find_path_matches(
                path,
                by_full,
                by_clean,
                by_last,
                prefer_cleaned=True,
                strict=False,
            )
            if candidates:
                self.logger.debug(
                    f"[DIFF-FMT] {tree_type}: '{path}' -> found via {strategy}"
                )
            else:
                self.logger.debug(f"[DIFF-FMT] {tree_type}: '{path}' -> NOT FOUND")
            return candidates[0] if candidates else None

        def _expand_parents(item):
            """Expand all ancestor items so the colored item is visible."""
            parent = item.parent()
            while parent:
                if not parent.isExpanded():
                    parent.setExpanded(True)
                parent = parent.parent()

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
                            _expand_parents(item)

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
                            _expand_parents(item)

            # Apply fuzzy match formatting to both trees
            for fuzzy_match in self._current_diff_result.get("fuzzy_matches", []):
                current_name = fuzzy_match.get("current_name", "")
                target_name = fuzzy_match.get("target_name", "")

                if tree_type == "current" and current_name:
                    item = _find_item(current_name)
                    if item:
                        self._apply_diff_color(item, "fuzzy")
                        _expand_parents(item)

                elif tree_type == "reference" and target_name:
                    item = _find_item(target_name)
                    if item:
                        self._apply_diff_color(item, "fuzzy")
                        _expand_parents(item)

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

    # ----------------------------- Ignore support ----------------------------- #

    def _get_ignored_set(self, tree_widget):
        """Return the ignored-path set associated with *tree_widget*."""
        if tree_widget is self.ui.tree000:
            return self._ignored_ref_paths
        if tree_widget is self.ui.tree001:
            return self._ignored_cur_paths
        return set()

    @staticmethod
    def _build_item_path(item):
        """Build a pipe-separated hierarchy path from a QTreeWidgetItem."""
        parts = []
        current = item
        while current:
            parts.insert(0, current.text(0))
            current = current.parent()
        return "|".join(parts)

    def is_path_ignored(self, tree_widget, path):
        """Check whether *path* (or any ancestor) is in the ignored set."""
        ignored = self._get_ignored_set(tree_widget)
        if path in ignored:
            return True
        return any(path.startswith(ip + "|") for ip in ignored)

    def _apply_ignore_styling(self, tree_widget):
        """Apply or remove strikethrough + dim styling for ignored items.

        Directly-ignored items get strikethrough + dim ``#666666``.
        Inherited-ignored items (ancestor is ignored) get italic + ``#888888``.
        """
        ignored = self._get_ignored_set(tree_widget)
        if not ignored:
            # Fast-path: nothing ignored — strip any leftover styling.
            iterator = QtWidgets.QTreeWidgetItemIterator(tree_widget)
            while iterator.value():
                item = iterator.value()
                font = item.font(0)
                if font.strikeOut() or font.italic():
                    font.setStrikeOut(False)
                    font.setItalic(False)
                    for col in range(tree_widget.columnCount()):
                        item.setFont(col, font)
                iterator += 1
            return

        direct_fg = QtGui.QColor("#666666")
        inherited_fg = QtGui.QColor("#888888")
        iterator = QtWidgets.QTreeWidgetItemIterator(tree_widget)
        while iterator.value():
            item = iterator.value()
            path = self._build_item_path(item)
            font = item.font(0)
            if path in ignored:
                # Directly ignored: strikethrough + dim
                if not font.strikeOut():
                    font.setStrikeOut(True)
                font.setItalic(False)
                for col in range(tree_widget.columnCount()):
                    item.setFont(col, font)
                    item.setForeground(col, QtGui.QBrush(direct_fg))
                    item.setBackground(col, QtGui.QBrush())
            elif self.is_path_ignored(tree_widget, path):
                # Inherited from an ignored ancestor: italic + lighter dim
                font.setStrikeOut(False)
                font.setItalic(True)
                for col in range(tree_widget.columnCount()):
                    item.setFont(col, font)
                    item.setForeground(col, QtGui.QBrush(inherited_fg))
                    item.setBackground(col, QtGui.QBrush())
            else:
                if font.strikeOut() or font.italic():
                    font.setStrikeOut(False)
                    font.setItalic(False)
                    for col in range(tree_widget.columnCount()):
                        item.setFont(col, font)
            iterator += 1

    def clear_ignored_paths(self):
        """Clear all ignored paths for both trees."""
        self._ignored_ref_paths.clear()
        self._ignored_cur_paths.clear()

    def _filter_ignored_from_diff(self):
        """Return diff result with ignored items excluded."""
        if not self._current_diff_result:
            return {"missing": [], "extra": [], "reparented": [], "fuzzy_matches": []}

        missing = [
            p
            for p in self._current_diff_result.get("missing", [])
            if not self.is_path_ignored(self.ui.tree000, p)
        ]
        extra = [
            p
            for p in self._current_diff_result.get("extra", [])
            if not self.is_path_ignored(self.ui.tree001, p)
        ]
        reparented = [
            r
            for r in self._current_diff_result.get("reparented", [])
            if not self.is_path_ignored(self.ui.tree001, r.get("current_path", ""))
            and not self.is_path_ignored(self.ui.tree000, r.get("reference_path", ""))
        ]
        fuzzy = [
            f
            for f in self._current_diff_result.get("fuzzy_matches", [])
            if not self.is_path_ignored(self.ui.tree001, f.get("current_name", ""))
            and not self.is_path_ignored(self.ui.tree000, f.get("target_name", ""))
        ]

        return {
            "missing": missing,
            "extra": extra,
            "reparented": reparented,
            "fuzzy_matches": fuzzy,
        }

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

        # Re-apply ignore styling to newly-rebuilt tree items
        self._apply_ignore_styling(self.ui.tree000)
        self._apply_ignore_styling(self.ui.tree001)

    def log_diff_results(self):
        """Log detailed hierarchy difference analysis results using rich formatting."""
        if not self._current_diff_result:
            self.logger.error(
                "No diff results available. Please analyze hierarchies first."
            )
            return

        # NOTE: 'missing' = present in REFERENCE, absent in CURRENT
        #       'extra'   = present in CURRENT, absent in REFERENCE
        # Use filtered results that exclude ignored paths
        effective = self._filter_ignored_from_diff()
        missing = effective["missing"]
        extra = effective["extra"]
        reparented = effective["reparented"]
        fuzzy_matches = effective["fuzzy_matches"]

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


class _MiddleButtonDragFilter(QtCore.QObject):
    """Event filter that enables middle-mouse drag-to-reparent on a QTreeWidget.

    Installed on ``tree001``'s **viewport** to intercept middle-button presses
    and synthesise left-button events so that Qt's built-in ``InternalMove``
    drag-drop machinery handles the visual move.

    Also installed on the **tree widget** itself to intercept ``Drop`` events.
    After Qt completes the internal move, the filter calls back into the slots
    layer to mirror the reparent operation inside Maya.
    """

    def __init__(self, parent=None, *, reparent_callback=None):
        super().__init__(parent)
        self._mid_dragging = False
        self._reparent_callback = reparent_callback
        self._dragged_items = []

    # ---- helpers -----------------------------------------------------------

    @staticmethod
    def _synth_mouse(etype, event, button=QtCore.Qt.LeftButton):
        return QtGui.QMouseEvent(
            etype, event.localPos(), button, button, event.modifiers()
        )

    # ---- eventFilter -------------------------------------------------------

    def eventFilter(self, obj, event):  # noqa: N802
        etype = event.type()

        # --- viewport events (middle → left translation) ---
        is_viewport = not obj.inherits("QTreeWidget")

        if is_viewport:
            if (
                etype == QtCore.QEvent.MouseButtonPress
                and event.button() == QtCore.Qt.MiddleButton
            ):
                tree = obj.parent()
                self._dragged_items = list(tree.selectedItems())
                self._mid_dragging = True
                QtCore.QCoreApplication.sendEvent(
                    obj, self._synth_mouse(QtCore.QEvent.MouseButtonPress, event)
                )
                return True

            if self._mid_dragging and etype == QtCore.QEvent.MouseMove:
                QtCore.QCoreApplication.sendEvent(
                    obj, self._synth_mouse(QtCore.QEvent.MouseMove, event)
                )
                return True

            if (
                etype == QtCore.QEvent.MouseButtonRelease
                and event.button() == QtCore.Qt.MiddleButton
            ):
                was_dragging = self._mid_dragging
                self._mid_dragging = False
                if was_dragging:
                    QtCore.QCoreApplication.sendEvent(
                        obj,
                        self._synth_mouse(QtCore.QEvent.MouseButtonRelease, event),
                    )
                    return True

            return super().eventFilter(obj, event)

        # --- tree-widget-level: intercept Drop to reparent in Maya ----------
        if etype == QtCore.QEvent.Drop and self._reparent_callback:
            # Let Qt handle the tree-item move first
            result = super().eventFilter(obj, event)
            # Now mirror reparent operations in Maya
            for item in self._dragged_items:
                new_parent = item.parent()
                self._reparent_callback(item, new_parent)
            self._dragged_items.clear()
            return result

        return super().eventFilter(obj, event)


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

    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.hierarchy_manager

        # Initialize UI components
        self.ui.txt001.setText("")  # Reference Scene Path
        self.ui.txt003.setText("")  # Log Output

        # Create controller
        self.controller = HierarchyManagerController(self)

        # Middle-mouse drag filter for current scene tree reparenting
        self._tree001_drag_filter = _MiddleButtonDragFilter(
            self.ui, reparent_callback=self._on_tree001_drop_reparent
        )

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

        # Unload reference scene when the manager window is hidden
        if hasattr(self.ui, "on_hide"):
            self.ui.on_hide.connect(self.controller._on_window_hidden)

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
        self.controller.hierarchy_manager = None
        self.controller._current_diff_result = None
        self.controller._reference_namespaces = []
        self.controller.clear_ignored_paths()
        self.controller._cleanup_cached_reference_import()
        self.controller.populate_current_scene_tree(self.ui.tree001)

        # Clear the reference tree — the cached import was cleaned up above
        # so there is nothing to display until the user triggers a fresh
        # import.  Re-importing automatically would pollute a new scene.
        ref_path = self.ui.txt001.text().strip() if hasattr(self.ui, "txt001") else ""
        if ref_path and os.path.exists(ref_path):
            self.ui.tree000.clear()
            self.ui.tree000.setHeaderLabels([Path(ref_path).stem or "Reference Scene"])
            info_item = self.ui.tree000.create_item(
                ["Click Diff or Pull to reload reference"]
            )
        else:
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
            "&nbsp;&nbsp;3. <b>Pull</b> &mdash; select objects in the reference tree and import<br>"
            "&nbsp;&nbsp;4. <b>Fix</b> &mdash; auto-repair stubs, quarantine extras, fix reparented<br><br>"
            "Right-click trees for more options. "
            "Enable <i>Dry Run</i> in the header menu to preview without changes."
            "</span>",
        ]
        self.ui.txt003.setHtml("\n".join(lines))

    def header_init(self, widget):
        """Initialize the header widget."""
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
            # Reference tree is read-only — editing names here has no meaning
            widget.setEditTriggers(self.sb.QtWidgets.QAbstractItemView.NoEditTriggers)

            # Enable multi-selection for auto-select functionality
            widget.setSelectionMode(
                self.sb.QtWidgets.QAbstractItemView.ExtendedSelection
            )

            widget.configure_menu(hide_on_leave=True)
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
            widget.menu.add("Separator")
            widget.menu.add(
                "QPushButton",
                setText="Ignore Selected",
                setObjectName="b013",
                setToolTip="Mark selected items as ignored (skipped during auto-selection and dimmed).",
            )
            widget.menu.add(
                "QPushButton",
                setText="Unignore Selected",
                setObjectName="b014",
                setToolTip="Remove ignore mark from selected items.",
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

    def _on_current_tree_item_renamed(self, item, column):
        """Rename the Maya scene object when the user edits a tree item's name."""
        if column != 0:
            return

        if getattr(self, "_renaming_in_progress", False):
            return
        self._renaming_in_progress = True
        try:
            node = item.data(0, self.sb.QtCore.Qt.UserRole)
            if node is None or isinstance(node, str):
                return

            new_name = item.text(0).strip()
            if not new_name:
                return

            try:
                old_name = node.nodeName()
                if new_name == old_name:
                    return

                node.rename(new_name)
                actual_name = node.nodeName()
                item._raw_name = actual_name

                # Maya may have appended a number to avoid clashes
                if actual_name != new_name:
                    item.setText(0, actual_name)

                self.controller.logger.info(
                    f"Renamed '{old_name}' \u2192 '{actual_name}'"
                )
            except Exception as e:
                # Revert the tree item text on failure
                try:
                    item.setText(0, node.nodeName())
                except Exception:
                    pass
                self.controller.logger.error(f"Rename failed: {e}")
        finally:
            self._renaming_in_progress = False

    def _on_tree001_drop_reparent(self, item, new_parent_item):
        """Mirror a tree-widget drag-drop reparent in the Maya scene.

        Called by ``_MiddleButtonDragFilter`` after Qt finishes moving the
        tree item.

        Parameters:
            item: The ``QTreeWidgetItem`` that was moved.
            new_parent_item: Its new parent item (``None`` if dropped at root).
        """
        node = item.data(0, self.sb.QtCore.Qt.UserRole)
        if node is None or isinstance(node, str):
            return

        try:
            if new_parent_item is not None:
                parent_node = new_parent_item.data(0, self.sb.QtCore.Qt.UserRole)
                if parent_node is None or isinstance(parent_node, str):
                    self.controller.logger.warning(
                        "Drop target has no Maya node — reparent skipped."
                    )
                    return
                pm.parent(node, parent_node)
                self.controller.logger.info(
                    f"Reparented '{node}' under '{parent_node}'"
                )
            else:
                # Dropped at root level → world-parent
                pm.parent(node, world=True)
                self.controller.logger.info(f"Reparented '{node}' to world")
        except Exception as e:
            self.controller.logger.error(f"Maya reparent failed for '{node}': {e}")
            # Refresh tree to revert the visual move
            self.controller.populate_current_scene_tree(self.ui.tree001)

    def tree001_init(self, widget):
        """Initialize the current scene hierarchy tree widget."""
        if not hasattr(widget, "is_initialized") or not widget.is_initialized:
            # Enable multi-selection for auto-select functionality
            widget.setSelectionMode(
                self.sb.QtWidgets.QAbstractItemView.ExtendedSelection
            )

            widget.configure_menu(hide_on_leave=True)
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
            widget.menu.add("Separator")
            widget.menu.add(
                "QPushButton",
                setText="Ignore Selected",
                setObjectName="b015",
                setToolTip="Mark selected items as ignored (skipped during auto-selection and dimmed).",
            )
            widget.menu.add(
                "QPushButton",
                setText="Unignore Selected",
                setObjectName="b016",
                setToolTip="Remove ignore mark from selected items.",
            )
            widget.menu.add("Separator")
            widget.menu.add(
                "QPushButton",
                setText="Rename from Reference",
                setObjectName="b017",
                setToolTip="Rename selected current-scene items using the names of selected reference-tree items (matched by order).",
            )

            # Open file dialog when the placeholder item is clicked
            widget.itemClicked.connect(self._on_current_tree_item_clicked)

            # Rename the Maya scene object when user edits a tree item
            widget.itemChanged.connect(self._on_current_tree_item_renamed)

            # Enable internal drag-and-drop for reparenting via middle mouse button
            widget.setDragDropMode(self.sb.QtWidgets.QAbstractItemView.InternalMove)
            widget.setDefaultDropAction(self.sb.QtCore.Qt.MoveAction)
            widget.viewport().installEventFilter(self._tree001_drag_filter)
            widget.installEventFilter(self._tree001_drag_filter)

            # Mark as initialized to prevent re-adding menu items
            widget.is_initialized = True

        # Always populate the current scene tree when initialized
        self.controller.populate_current_scene_tree(widget)

    def txt001_init(self, widget):
        """Initialize the reference scene path input."""
        from uitk.widgets.optionBox.options.browse import BrowseOption

        self._browse_option = BrowseOption(
            wrapped_widget=widget,
            file_types="Maya Files (*.ma *.mb);;FBX Files (*.fbx);;All Files (*.*)",
            title="Select Reference Scene:",
            start_dir=lambda: self.controller.workspace,
        )
        widget.option_box.add_option(self._browse_option)

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
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Filter Mesh Objects",
            setObjectName="chk_filter_meshes",
            setChecked=False,
            setToolTip="Exclude mesh-bearing transforms from the comparison. When unchecked, all transforms (including geometry) are compared.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Ignore Quarantine Group",
            setObjectName="chk_ignore_quarantine",
            setChecked=True,
            setToolTip="Automatically ignore the quarantine group (e.g. _QUARANTINE) in the current scene tree during diff analysis.",
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

    def tb003_init(self, widget):
        """Initialize the fix/repair toggle button with options menu."""
        widget.option_box.menu.setTitle("Repair Options:")

        widget.option_box.menu.add(
            "QCheckBox",
            setText="Create Stubs (Missing)",
            setObjectName="chk_fix_stubs",
            setChecked=True,
            setToolTip="Create empty transform placeholders for items missing from the current scene.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Quarantine Extras",
            setObjectName="chk_fix_quarantine",
            setChecked=True,
            setToolTip="Move extra items (not in reference) to a quarantine group.",
        )
        widget.option_box.menu.add(
            "QLineEdit",
            setObjectName="txt_quarantine_name",
            setPlaceholderText="_QUARANTINE",
            setToolTip="Custom name for the quarantine group (leave blank for default).",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Skip Animated Parents",
            setObjectName="chk_skip_animated",
            setChecked=False,
            setToolTip="Skip quarantining extras that are parented under an animated object (they may be intentionally attached).",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Fix Reparented",
            setObjectName="chk_fix_reparented",
            setChecked=True,
            setToolTip="Move reparented nodes to match their reference hierarchy position.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Fix Fuzzy Renames",
            setObjectName="chk_fix_fuzzy_renames",
            setChecked=True,
            setToolTip="Rename nodes identified as fuzzy matches to their reference names.",
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

        # Get filter_meshes option
        filter_meshes = False  # Default: compare all transforms
        if hasattr(self.ui, "tb001") and hasattr(self.ui.tb001, "menu"):
            if hasattr(self.ui.tb001.menu, "chk_filter_meshes"):
                filter_meshes = (
                    self.ui.tb001.option_box.menu.chk_filter_meshes.isChecked()
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
            reference_path, fuzzy_matching, dry_run, filter_meshes=filter_meshes
        )
        if not success:
            return

        # Ensure trees are populated for diff visualization
        # Only populate if not already populated or if structure has changed
        self._ensure_trees_populated_for_diff(reference_path, fuzzy_matching, dry_run)

        # Auto-ignore the quarantine group BEFORE logging/formatting so that
        # counts and styling reflect the exclusion.
        ignore_quarantine = True
        if hasattr(self.ui, "tb001") and hasattr(self.ui.tb001, "menu"):
            if hasattr(self.ui.tb001.menu, "chk_ignore_quarantine"):
                ignore_quarantine = (
                    self.ui.tb001.option_box.menu.chk_ignore_quarantine.isChecked()
                )
        if ignore_quarantine:
            self._auto_ignore_quarantine_group()

        # Log diff results (uses _filter_ignored_from_diff, so quarantine is excluded)
        self.controller.log_diff_results()

        # Apply analysis mode specific behavior
        if diff_mode == "Missing Objects Only":
            self.logger.debug("Focusing on missing objects only")
        elif diff_mode == "Extra Objects Only":
            self.logger.debug("Focusing on extra objects only")
        elif diff_mode == "Selected Objects Only":
            self.logger.debug("Analyzing selected objects only")

        # Apply diff formatting to trees
        if self.controller._current_diff_result:
            self.controller.apply_difference_formatting(
                self.ui.tree001, self.ui.tree000
            )
            # Re-apply ignore styling on top of diff colors
            self.controller._apply_ignore_styling(self.ui.tree000)
            self.controller._apply_ignore_styling(self.ui.tree001)

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

        # Update footer with diff summary (uses effective counts excluding ignored)
        if hasattr(self.ui, "footer") and self.ui.footer:
            diff = self.controller._current_diff_result
            if diff:
                effective = self.controller._filter_ignored_from_diff()
                n_miss = len(effective["missing"])
                n_extra = len(effective["extra"])
                n_repar = len(effective["reparented"])
                n_fuzzy = len(effective.get("fuzzy_matches", []))
                total = n_miss + n_extra + n_repar + n_fuzzy
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
                    if n_fuzzy:
                        parts.append(f"{n_fuzzy} renamed")
                    self.ui.footer.setText(f"Diff: {', '.join(parts)}.")

    def tb002(self, state=None):
        """Toggle button for pull objects with options menu."""
        self.ui.txt003.clear()

        # Validate that we have objects selected and a reference path
        object_names = self.controller.get_selected_object_names(self.ui.tree000)
        # Filter out ignored items from selection
        object_names = [
            n
            for n in object_names
            if not self.controller.is_path_ignored(self.ui.tree000, n)
        ]
        if not object_names:
            self.logger.error("Please select objects in the reference hierarchy tree.")
            return

        reference_path = self.ui.txt001.text().strip()
        if not reference_path:
            self.logger.error("Please specify a reference scene path.")
            return

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

    def tb003(self, state=None):
        """Toggle button for fix/repair operations."""
        self.ui.txt003.clear()

        if not self.controller._current_diff_result:
            self.logger.error("Please run a diff analysis first (Diff button).")
            return

        dry_run = self.ui.chk002.isChecked()

        # Get repair options from toggle button menu
        do_stubs = True
        do_quarantine = True
        do_reparent = True
        skip_animated = False
        quarantine_name = "_QUARANTINE"
        do_fuzzy_renames = True

        if hasattr(self.ui, "tb003") and hasattr(self.ui.tb003, "menu"):
            if hasattr(self.ui.tb003.menu, "chk_fix_stubs"):
                do_stubs = self.ui.tb003.option_box.menu.chk_fix_stubs.isChecked()
            if hasattr(self.ui.tb003.menu, "chk_fix_quarantine"):
                do_quarantine = (
                    self.ui.tb003.option_box.menu.chk_fix_quarantine.isChecked()
                )
            if hasattr(self.ui.tb003.menu, "chk_skip_animated"):
                skip_animated = (
                    self.ui.tb003.option_box.menu.chk_skip_animated.isChecked()
                )
            if hasattr(self.ui.tb003.menu, "chk_fix_reparented"):
                do_reparent = (
                    self.ui.tb003.option_box.menu.chk_fix_reparented.isChecked()
                )
            if hasattr(self.ui.tb003.menu, "chk_fix_fuzzy_renames"):
                do_fuzzy_renames = (
                    self.ui.tb003.option_box.menu.chk_fix_fuzzy_renames.isChecked()
                )
            if hasattr(self.ui.tb003.menu, "txt_quarantine_name"):
                custom_name = (
                    self.ui.tb003.option_box.menu.txt_quarantine_name.text().strip()
                )
                if custom_name:
                    quarantine_name = custom_name

        mode = "DRY-RUN" if dry_run else "LIVE"
        self.logger.log_divider()
        self.logger.progress(f"Running hierarchy repair ({mode})...")

        success = self.controller.repair_hierarchies(
            create_stubs=do_stubs,
            quarantine_extras=do_quarantine,
            quarantine_group=quarantine_name,
            skip_animated=skip_animated,
            fix_reparented=do_reparent,
            fix_fuzzy_renames=do_fuzzy_renames,
            dry_run=dry_run,
        )

        if success and not dry_run:
            # Refresh trees after live repairs
            self.b000()
            self.logger.info("Scene modified — re-run Diff to see updated differences.")

        # Update footer
        if hasattr(self.ui, "footer") and self.ui.footer:
            if success:
                self.ui.footer.setText(
                    f"Fix: {mode} complete" if dry_run else "Fix: repairs applied"
                )
            else:
                self.ui.footer.setText("Fix: nothing to repair")

    def b003(self):
        """Browse for reference scene file."""
        if hasattr(self, "_browse_option"):
            self._browse_option.browse()
            return

        reference_file = self.sb.file_dialog(
            file_types="Maya Files (*.ma *.mb);;FBX Files (*.fbx);;All Files (*.*)",
            title="Select Reference Scene:",
            start_dir=self.controller.workspace,
        )

        if reference_file and len(reference_file) > 0:
            self.ui.txt001.setText(reference_file[0])
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
        self.controller._apply_ignore_styling(self.ui.tree000)
        self.controller._apply_ignore_styling(self.ui.tree001)
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

        # Get filter_meshes option
        filter_meshes = False
        if hasattr(self.ui, "tb001") and hasattr(self.ui.tb001, "menu"):
            if hasattr(self.ui.tb001.menu, "chk_filter_meshes"):
                filter_meshes = (
                    self.ui.tb001.option_box.menu.chk_filter_meshes.isChecked()
                )

        # Set log level
        if log_level:
            self.logger.setLevel(log_level)

        success = self.controller.analyze_hierarchies(
            reference_path, fuzzy_matching, dry_run, filter_meshes=filter_meshes
        )
        if success:
            # Refresh tree widgets with new analysis
            self.b000()

            # Record reference path to recent list
            if hasattr(self, "_recent_refs_option"):
                self._recent_refs_option.record(reference_path)

    def b013(self):
        """Ignore selected items in the reference tree."""
        self._ignore_selected(self.ui.tree000)

    def b014(self):
        """Unignore selected items in the reference tree."""
        self._unignore_selected(self.ui.tree000)

    def b015(self):
        """Ignore selected items in the current scene tree."""
        self._ignore_selected(self.ui.tree001)

    def b016(self):
        """Unignore selected items in the current scene tree."""
        self._unignore_selected(self.ui.tree001)

    def b017(self):
        """Rename current-scene items to match reference names.

        Works in two modes:
        1. **Manual** — when items are selected in both trees, pairs them
           by selection order (first-to-first, second-to-second, etc.).
        2. **Auto (fuzzy)** — when nothing is selected in the reference tree,
           uses the fuzzy-match results from the last diff to auto-pair items.
           Only fuzzy-matched items that are currently selected in the current
           tree are renamed, or ALL fuzzy matches if nothing is selected.
        """
        cur_items = self.ui.tree001.selectedItems()
        ref_items = self.ui.tree000.selectedItems()

        # ── Build rename pairs ──
        rename_pairs = []  # list of (cur_tree_item, new_name_str)

        if ref_items:
            # Manual mode: pair by selection order
            if not cur_items:
                self.logger.warning(
                    "Select at least one item in the current scene tree."
                )
                return
            pairs = min(len(cur_items), len(ref_items))
            if len(cur_items) != len(ref_items):
                self.logger.notice(
                    f"Selection counts differ (current={len(cur_items)}, "
                    f"reference={len(ref_items)}). Renaming first {pairs} pairs."
                )
            for ci, ri in zip(cur_items[:pairs], ref_items[:pairs]):
                rename_pairs.append((ci, ri.text(0).strip()))
        else:
            # Auto mode: use fuzzy matches from last diff
            diff = self.controller._current_diff_result
            if not diff or not diff.get("fuzzy_matches"):
                self.logger.warning(
                    "No reference selection and no fuzzy matches available.\n"
                    "Select items in both trees, or run Diff first."
                )
                return

            fuzzy_list = diff["fuzzy_matches"]
            self.logger.info(
                f"Auto-rename: {len(fuzzy_list)} fuzzy match(es) from last diff."
            )

            # Build a lookup: cleaned_path → tree item for current tree
            cur_item_map = {}
            it = self.sb.QtWidgets.QTreeWidgetItemIterator(self.ui.tree001)
            while it.value():
                item = it.value()
                path = self.controller._build_item_path(item)
                cur_item_map[path] = item
                it += 1

            # If user selected specific items, restrict to those
            selected_paths = set()
            if cur_items:
                selected_paths = {
                    self.controller._build_item_path(i) for i in cur_items
                }

            for fz in fuzzy_list:
                cur_path = fz.get("current_name", "")
                ref_path = fz.get("target_name", "")
                if not cur_path or not ref_path:
                    continue
                # Extract the leaf name from the reference path
                ref_leaf = ref_path.rsplit("|", 1)[-1]
                item = cur_item_map.get(cur_path)
                if not item:
                    continue
                if selected_paths and cur_path not in selected_paths:
                    continue
                rename_pairs.append((item, ref_leaf))

            if not rename_pairs:
                self.logger.notice("No matching fuzzy items found in the current tree.")
                return

        # ── Execute renames ──
        renamed = 0
        for cur_item, new_name in rename_pairs:
            node = cur_item.data(0, self.sb.QtCore.Qt.UserRole)
            if node is None or isinstance(node, str):
                continue
            if not new_name:
                continue

            try:
                old_name = node.nodeName()
                if new_name == old_name:
                    continue

                node.rename(new_name)
                actual_name = node.nodeName()
                cur_item._raw_name = actual_name

                # Block signals to prevent itemChanged from interfering
                self.ui.tree001.blockSignals(True)
                try:
                    cur_item.setText(0, actual_name)
                finally:
                    self.ui.tree001.blockSignals(False)

                renamed += 1
                self.controller.logger.info(
                    f"Renamed '{old_name}' \u2192 '{actual_name}'"
                )
            except Exception as e:
                self.controller.logger.error(f"Failed to rename '{node}': {e}")

        if renamed:
            self.logger.success(f"Renamed {renamed} object(s) from reference names.")
            # Re-run diff if one was active
            if self.controller._current_diff_result:
                self.controller.apply_difference_formatting(
                    self.ui.tree001, self.ui.tree000
                )

    def _auto_ignore_quarantine_group(self):
        """Add the quarantine group path to the current-scene ignored set.

        Reads the quarantine group name from the repair options (tb003) or
        falls back to ``_QUARANTINE``.  If a matching root-level item exists
        in tree001, its path is added to ``_ignored_cur_paths`` so the
        existing ignore styling (strikethrough + dim) is applied automatically.
        """
        quarantine_name = "_QUARANTINE"
        if hasattr(self.ui, "tb003") and hasattr(self.ui.tb003, "menu"):
            if hasattr(self.ui.tb003.menu, "txt_quarantine_name"):
                custom = (
                    self.ui.tb003.option_box.menu.txt_quarantine_name.text().strip()
                )
                if custom:
                    quarantine_name = custom

        tree = self.ui.tree001
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            if item.text(0) == quarantine_name:
                path = self.controller._build_item_path(item)
                self.controller._ignored_cur_paths.add(path)
                return

    def _ignore_selected(self, tree_widget):
        """Mark selected tree items (and their descendants) as ignored."""
        items = tree_widget.selectedItems()
        if not items:
            self.logger.warning("No items selected to ignore.")
            return

        ignored_set = self.controller._get_ignored_set(tree_widget)
        added = 0
        for item in items:
            path = self.controller._build_item_path(item)
            if path not in ignored_set:
                ignored_set.add(path)
                added += 1

        self._refresh_tree_styling()
        tree_widget.clearSelection()
        self.logger.info(f"Ignored {added} items (descendants also ignored).")

    def _unignore_selected(self, tree_widget):
        """Remove ignore mark from selected tree items."""
        items = tree_widget.selectedItems()
        if not items:
            self.logger.warning("No items selected to unignore.")
            return

        ignored_set = self.controller._get_ignored_set(tree_widget)
        removed = 0
        inherited_count = 0
        for item in items:
            path = self.controller._build_item_path(item)
            if path in ignored_set:
                ignored_set.discard(path)
                # Also discard any explicitly-ignored descendants
                to_remove = {p for p in ignored_set if p.startswith(path + "|")}
                ignored_set -= to_remove
                removed += 1 + len(to_remove)
            elif self.controller.is_path_ignored(tree_widget, path):
                inherited_count += 1

        self._refresh_tree_styling()
        if removed:
            self.logger.info(f"Unignored {removed} items.")
        if inherited_count:
            self.logger.warning(
                f"{inherited_count} item(s) ignored via a parent — unignore the parent to remove."
            )

    def _refresh_tree_styling(self):
        """Re-apply diff colors then ignore styling to both trees."""
        if self.controller._current_diff_result:
            self.controller.apply_difference_formatting(
                self.ui.tree001, self.ui.tree000
            )
        else:
            self.controller._clear_tree_colors(self.ui.tree001)
            self.controller._clear_tree_colors(self.ui.tree000)
        self.controller._apply_ignore_styling(self.ui.tree000)
        self.controller._apply_ignore_styling(self.ui.tree001)

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

            # Filter out ignored paths (reference tree)
            if missing_list:
                pre_ignore = len(missing_list)
                missing_list = [
                    p
                    for p in missing_list
                    if not self.controller.is_path_ignored(tree000, p)
                ]
                ignored_count = pre_ignore - len(missing_list)
                if ignored_count:
                    self.logger.debug(
                        f"Filtered {ignored_count} ignored paths from missing list"
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

            # Filter out ignored paths (current tree)
            if extra_list:
                pre_ignore = len(extra_list)
                extra_list = [
                    p
                    for p in extra_list
                    if not self.controller.is_path_ignored(tree001, p)
                ]
                ignored_count = pre_ignore - len(extra_list)
                if ignored_count:
                    self.logger.debug(
                        f"Filtered {ignored_count} ignored paths from extra list"
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

            # -------------------- Select REPARENTED (both trees) ---------------------
            reparented_list = self.controller._current_diff_result.get("reparented", [])
            if reparented_list:
                self.logger.debug(
                    f"[AUTO-SELECT] Selecting {len(reparented_list)} reparented items"
                )
            for rp in reparented_list:
                for key, tree, by_f, by_c, by_l in (
                    (
                        "reference_path",
                        tree000,
                        ref_by_full,
                        ref_by_clean_full,
                        ref_by_last,
                    ),
                    (
                        "current_path",
                        tree001,
                        cur_by_full,
                        cur_by_clean_full,
                        cur_by_last,
                    ),
                ):
                    rp_path = rp.get(key, "")
                    if not rp_path:
                        continue
                    candidates, strategy = tree_matcher.find_path_matches(
                        rp_path,
                        by_f,
                        by_c,
                        by_l,
                        prefer_cleaned=True,
                        strict=False,
                    )
                    for c in candidates:
                        if not c.isSelected():
                            c.setSelected(True)
                            selected_count += 1
                            parent = c.parent()
                            while parent:
                                if not parent.isExpanded():
                                    parent.setExpanded(True)
                                parent = parent.parent()
                            self.logger.debug(
                                f"  ✓ SELECTED reparented '{rp['leaf']}' in "
                                f"{'ref' if key == 'reference_path' else 'cur'} tree"
                            )

            # -------------------- Select FUZZY (both trees) ---------------------
            fuzzy_list = self.controller._current_diff_result.get("fuzzy_matches", [])
            if fuzzy_list:
                self.logger.debug(
                    f"[AUTO-SELECT] Selecting {len(fuzzy_list)} fuzzy-matched items"
                )
            for fz in fuzzy_list:
                for key, tree, by_f, by_c, by_l in (
                    (
                        "target_name",
                        tree000,
                        ref_by_full,
                        ref_by_clean_full,
                        ref_by_last,
                    ),
                    (
                        "current_name",
                        tree001,
                        cur_by_full,
                        cur_by_clean_full,
                        cur_by_last,
                    ),
                ):
                    fz_path = fz.get(key, "")
                    if not fz_path:
                        continue
                    candidates, strategy = tree_matcher.find_path_matches(
                        fz_path,
                        by_f,
                        by_c,
                        by_l,
                        prefer_cleaned=True,
                        strict=False,
                    )
                    for c in candidates:
                        if not c.isSelected():
                            c.setSelected(True)
                            selected_count += 1
                            parent = c.parent()
                            while parent:
                                if not parent.isExpanded():
                                    parent.setExpanded(True)
                                parent = parent.parent()
                            self.logger.debug(
                                f"  ✓ SELECTED fuzzy '{fz_path.split('|')[-1]}' in "
                                f"{'ref' if key == 'target_name' else 'cur'} tree"
                            )

            # -------------------- Select ALL CHILDREN of selected nodes ---------------------
            # After selecting the diff paths, also select all their children to ensure
            # the deepest visible nodes (like _GEO objects) are also selected
            children_selected = 0

            # Function to recursively select all children of a tree item
            def select_all_children(tree_item):
                count = 0
                tree_widget = tree_item.treeWidget()
                for i in range(tree_item.childCount()):
                    child = tree_item.child(i)
                    # Skip ignored children
                    child_path = self.controller._build_item_path(child)
                    if self.controller.is_path_ignored(tree_widget, child_path):
                        continue
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
            total_expected = (
                len(extra_list)
                + len(missing_list)
                + len(reparented_list) * 2  # both trees
                + len(fuzzy_list) * 2  # both trees
            )
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

                # Flush pending events and repaint both trees
                try:
                    self.sb.QtWidgets.QApplication.processEvents()
                except Exception:
                    pass

                for tree in (tree000, tree001):
                    tree.viewport().update()
                    tree.repaint()

                # Set focus on the tree with more selections
                if ref_selected_count >= cur_selected_count:
                    tree000.setFocus()
                else:
                    tree001.setFocus()

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
                if self.controller.is_path_ignored(tree000, missing_path):
                    continue
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
                if self.controller.is_path_ignored(tree001, extra_path):
                    continue
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

            # Expand reparented items in both trees
            for reparented in self.controller._current_diff_result.get(
                "reparented", []
            ):
                for key, tree in (
                    ("reference_path", tree000),
                    ("current_path", tree001),
                ):
                    rp_path = reparented.get(key, "")
                    if not rp_path:
                        continue
                    node_name = rp_path.split("|")[-1]
                    items = tree.findItems(node_name, self.sb.QtCore.Qt.MatchRecursive)
                    for item in items:
                        if self._matches_hierarchy_path(item, rp_path):
                            parent = item.parent()
                            while parent:
                                if not parent.isExpanded():
                                    parent.setExpanded(True)
                                    expanded_count += 1
                                parent = parent.parent()

            # Expand fuzzy-matched items in both trees
            for fuzzy in self.controller._current_diff_result.get("fuzzy_matches", []):
                for key, tree in (
                    ("target_name", tree000),
                    ("current_name", tree001),
                ):
                    fz_path = fuzzy.get(key, "")
                    if not fz_path:
                        continue
                    node_name = fz_path.split("|")[-1]
                    items = tree.findItems(node_name, self.sb.QtCore.Qt.MatchRecursive)
                    for item in items:
                        if self._matches_hierarchy_path(item, fz_path):
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

                if final_ref_count > 0 and final_cur_count > 0:
                    self.logger.success(
                        f"✓ AUTO-SELECT COMPLETE: {final_ref_count} items in REFERENCE tree, "
                        f"{final_cur_count} items in CURRENT tree. "
                        f"Use 'Pull' to import missing, or 'Fix' to repair reparented/renamed."
                    )
                elif final_ref_count > 0:
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
            )
        except Exception:
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
        except Exception:
            return False

    def _ensure_trees_populated_for_diff(self, reference_path, fuzzy_matching, dry_run):
        """Ensure both trees are populated for diff visualization.

        Always repopulates both trees so that diff formatting and expansion
        reflect the latest scene state and reference file.

        Because ``populate_reference_tree`` now reuses the cached import
        (instead of re-importing and clearing the analysis cache), the
        previous save/restore workaround is no longer necessary.
        """
        try:
            # Repopulate current scene tree
            self.controller.populate_current_scene_tree(self.ui.tree001)
            self.logger.debug("Populated current scene tree for diff visualization")

            # Repopulate reference tree (reuses cached import — no re-import)
            if reference_path:
                self.controller.populate_reference_tree(
                    self.ui.tree000, reference_path, fuzzy_matching, dry_run
                )
                self.logger.debug("Populated reference tree for diff visualization")

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
