# !/usr/bin/python
# coding=utf-8
from pathlib import Path
from typing import Union, Optional, Dict, List, Any
import pymel.core as pm
import pythontk as ptk

# From this package
from mayatk.env_utils.namespace_sandbox import NamespaceSandbox
from mayatk.env_utils.hierarchy_manager.matching_engine import MatchingEngine


class ValidationManager(ptk.LoggingMixin):
    """Handles input validation and backup operations."""

    def __init__(self, dry_run: bool = True):
        super().__init__()
        self.dry_run = dry_run

    def validate_inputs(
        self, objects: List[str], scene_file: Path, operation: str
    ) -> bool:
        """Validate inputs for both push and pull operations."""
        if not scene_file.exists():
            self.logger.error(f"Scene file does not exist: {scene_file}")
            return False

        # Support only fbx, ma, and mb files
        if scene_file.suffix.lower() not in [".fbx", ".ma", ".mb"]:
            self.logger.error(
                f"Unsupported scene file format: {scene_file.suffix}. Supported formats: .fbx, .ma, .mb"
            )
            return False

        # For push operations, check if objects exist in current scene
        if operation == "Push":
            missing_objects = [obj for obj in objects if not pm.objExists(obj)]
            if missing_objects:
                self.logger.error(
                    f"Objects not found in current scene: {missing_objects}"
                )
                return False

        return True

    def create_backup(self, target_scene: Optional[Path] = None) -> None:
        """Create backup of current scene or specified scene."""
        try:
            if target_scene:
                backup_path = target_scene.with_suffix(".backup.ma")
                self.logger.info(f"Creating backup: {backup_path}")
                # Implementation for file backup
            else:
                current_file = pm.sceneName()
                if current_file:
                    backup_path = Path(current_file).with_suffix(".backup.ma")
                    pm.saveAs(str(backup_path))
                    self.logger.info(f"Created backup: {backup_path}")
        except Exception as e:
            self.logger.error(f"Failed to create backup: {e}")


class ObjectProcessor(ptk.LoggingMixin):
    """Handles object processing including renaming, hierarchy restoration, and cleanup."""

    def __init__(self, import_manager: NamespaceSandbox):
        super().__init__()
        self.import_manager = import_manager

    def _clean_namespace_name(self, namespaced_name: str) -> str:
        """Extract clean name without namespace prefix."""
        return namespaced_name.split(":")[-1]

    def process_found_objects_with_cleanup(
        self,
        found_objects: List,
        namespace: str,
        imported_objects: List = None,
        fuzzy_match_map: Dict = None,
    ) -> bool:
        """Process found objects and immediately clean up import to prevent issues."""
        self.logger.info(f"Found {len(found_objects)} matching objects:")
        for obj in found_objects:
            self.logger.info(f"  - {obj.nodeName()}")

        nodes_before = len(pm.ls())

        # Collect parent info for objects that will be replaced
        parent_info = self._collect_parent_info(found_objects, fuzzy_match_map or {})

        # Process all objects with materials
        processed_objects = self._process_all_objects(
            found_objects, parent_info, fuzzy_match_map or {}
        )

        # Smart cleanup that preserves hierarchy
        if imported_objects:
            self._cleanup_unused_objects(processed_objects, imported_objects)

        # Skip namespace cleanup to preserve object content
        self._handle_namespace_cleanup(namespace)

        # Verify cleanup and log results
        self._verify_import_results(processed_objects, nodes_before)

        return len(processed_objects) > 0

    def _collect_parent_info(
        self, found_objects: List, fuzzy_match_map: Dict
    ) -> Dict[str, Optional[Any]]:
        """Collect parent information for objects that will be replaced."""
        parent_info = {}
        for obj in found_objects:
            clean_name = self._clean_namespace_name(obj.nodeName())
            parent_info.update(
                self._handle_existing_object_replacement(obj, clean_name)
            )
        return parent_info

    def _handle_existing_object_replacement(
        self, obj: Any, clean_name: str
    ) -> Dict[str, Optional[Any]]:
        """Handle replacement of existing objects, returning parent info."""
        parent_info = {}

        # Check if this imported object is different from any existing object with the same name
        if pm.objExists(clean_name):
            existing_obj = pm.PyNode(clean_name)
            if existing_obj != obj:
                # Store parent info before deletion
                parent_info[clean_name] = (
                    existing_obj.getParent() if existing_obj.getParent() else None
                )
                self.logger.info(f"Will replace existing object: {clean_name}")
        else:
            parent_info[clean_name] = None

        return parent_info

    def _process_all_objects(
        self, found_objects: List, parent_info: Dict, fuzzy_match_map: Dict
    ) -> List:
        """Process imported objects - rename them and restore hierarchy if needed."""
        processed_objects = []

        for obj in found_objects:
            clean_name = self._clean_namespace_name(obj.nodeName())
            self._handle_object_rename_and_replacement(obj, clean_name, parent_info)
            processed_objects.append(obj)

        return processed_objects

    def _handle_object_rename_and_replacement(
        self, obj: Any, clean_name: str, parent_info: Dict
    ) -> None:
        """Handle object renaming and replacement logic."""
        # Check if there's an existing object with the same clean name that we need to replace
        existing_obj_to_replace = None
        if pm.objExists(clean_name):
            existing_obj = pm.PyNode(clean_name)
            if existing_obj != obj:
                existing_obj_to_replace = existing_obj

        # If we found an existing object to replace, delete it first
        if existing_obj_to_replace:
            self.logger.info(
                f"Deleting existing object to replace: {existing_obj_to_replace}"
            )
            pm.delete(existing_obj_to_replace)

        # Now ensure our imported object has the correct clean name
        if obj.nodeName() != clean_name:
            self._rename_imported_object(obj, clean_name, parent_info)
        else:
            self.logger.debug(f"Object already has correct name: {clean_name}")

    def _rename_imported_object(
        self, obj: Any, clean_name: str, parent_info: Dict
    ) -> None:
        """Rename imported object to clean name."""
        try:
            obj.rename(clean_name)
            self.logger.info(f"Renamed object: {obj.nodeName()} -> {clean_name}")
            self._restore_parent_hierarchy(obj, clean_name, parent_info)
        except Exception as rename_error:
            self.logger.error(
                f"Failed to rename {obj.nodeName()} to {clean_name}: {rename_error}"
            )

    def _restore_parent_hierarchy(
        self, obj: Any, clean_name: str, parent_info: Dict
    ) -> None:
        """Restore parent hierarchy if needed."""
        if clean_name in parent_info and parent_info[clean_name]:
            try:
                pm.parent(obj, parent_info[clean_name])
                self.logger.info(
                    f"Restored parent hierarchy: {clean_name} -> {parent_info[clean_name]}"
                )
            except Exception as parent_error:
                self.logger.warning(
                    f"Failed to restore parent for {clean_name}: {parent_error}"
                )

    def _cleanup_unused_objects(
        self, processed_objects: List, imported_objects: List
    ) -> None:
        """Clean up unused imported objects while preserving hierarchy."""
        # Get all objects that should be preserved (processed objects + their parents)
        objects_to_preserve = set()

        for obj in processed_objects:
            current = obj
            while current:
                objects_to_preserve.add(current)
                current = current.getParent()

        # Only delete imported objects that are NOT in the preserve list
        remaining_objects = []
        for obj in imported_objects:
            if obj not in objects_to_preserve:
                remaining_objects.append(obj)

        if remaining_objects:
            self.logger.info(
                f"Cleaning up {len(remaining_objects)} unused imported objects"
            )
            try:
                pm.delete(remaining_objects)
            except Exception as cleanup_error:
                self.logger.warning(f"Failed to clean up some objects: {cleanup_error}")

    def _handle_namespace_cleanup(self, namespace: str) -> None:
        """Handle namespace cleanup while preserving object content."""
        # Skip namespace cleanup to preserve object content
        self.logger.info(
            f"[CLEANUP] Skipping namespace cleanup for {namespace} to preserve object content"
        )

        # Just remove from tracking without deleting namespace content
        if (
            hasattr(self.import_manager, "_active_namespaces")
            and namespace in self.import_manager._active_namespaces
        ):
            self.import_manager._active_namespaces.remove(namespace)

    def _verify_import_results(self, imported_objects: List, nodes_before: int) -> None:
        """Verify and log import results."""
        nodes_after = len(pm.ls())
        nodes_added = nodes_after - nodes_before
        self.logger.debug(
            f"Node count: before={nodes_before}, after={nodes_after}, net added={nodes_added}"
        )

        if imported_objects:
            self.logger.success(
                f"Successfully processed {len(imported_objects)} objects"
            )
        else:
            self.logger.warning("No objects were processed")


class ObjectSwapper(ptk.LoggingMixin):
    """Handles pushing/pulling objects between Maya scenes without modifying source scene.

    Parameters:
        dry_run: If True, perform analysis without making changes
        fuzzy_matching: If True, allow fuzzy name matching when exact matches fail
    """

    def __init__(self, dry_run: bool = True, fuzzy_matching: bool = True):
        super().__init__()
        self.dry_run = dry_run
        self.fuzzy_matching = fuzzy_matching

        # Initialize components
        self.import_manager = NamespaceSandbox(
            dry_run=False
        )  # Always import for analysis
        self.matching_engine = MatchingEngine(self.import_manager, fuzzy_matching)
        self.validation_manager = ValidationManager(dry_run)
        self.object_processor = ObjectProcessor(self.import_manager)

    def _clean_namespace_name(self, namespaced_name: str) -> str:
        """Extract clean name without namespace prefix."""
        return namespaced_name.split(":")[-1]

    def pull_objects_from_scene(
        self,
        target_objects: Union[List[str], List[Any], str, Any],
        source_scene_file: Union[str, Path],
        backup: bool = True,
    ) -> bool:
        """Pull objects from source scene to current scene."""
        # Normalize inputs
        objects = self._normalize_object_list(target_objects)
        scene_file = Path(source_scene_file)

        # Validate inputs
        if not self.validation_manager.validate_inputs(objects, scene_file, "Pull"):
            return False

        self.logger.info(f"Pulling {len(objects)} objects from {scene_file}")

        return self._import_source_objects(scene_file, objects)

    def swap_objects(
        self,
        target_objects: List[str],
        source_file: Union[str, Path],
        backup: bool = True,
    ) -> bool:
        """Legacy method name for backward compatibility."""
        return self.pull_objects_from_scene(target_objects, source_file, backup)

    def _normalize_object_list(
        self, objects: Union[List[str], List[Any], str, Any]
    ) -> List[str]:
        """Normalize various object input formats to list of strings."""
        if isinstance(objects, str):
            return [objects]
        elif hasattr(objects, "__iter__"):
            return [
                str(obj.nodeName()) if hasattr(obj, "nodeName") else str(obj)
                for obj in objects
            ]
        else:
            return [
                (
                    str(objects.nodeName())
                    if hasattr(objects, "nodeName")
                    else str(objects)
                )
            ]

    def _import_source_objects(
        self, source_file: Path, target_objects: List[str]
    ) -> bool:
        """Import objects from source scene and process matches."""
        # Import source objects directly using NamespaceSandbox
        import_info = self.import_manager.import_objects_for_swapping(source_file)
        if not import_info:
            return False

        imported_transforms = import_info.get("imported_transforms", [])
        namespace = import_info.get("namespace", "")

        try:
            if self.dry_run:
                # For dry run, analyze matches then clean up
                found_objects, fuzzy_match_map = self.matching_engine.find_matches(
                    target_objects, imported_transforms, dry_run=True
                )
                self._log_dry_run_results(found_objects, fuzzy_match_map)

                # Clean up imported objects after analysis
                self._cleanup_dry_run_import(imported_transforms, namespace)

                return len(found_objects) > 0

            # Find matching objects
            found_objects, fuzzy_match_map = self.matching_engine.find_matches(
                target_objects, imported_transforms, dry_run=False
            )

            if not found_objects:
                self.logger.warning("No matching objects found")
                # Clean up imported objects even when no matches found
                self._cleanup_dry_run_import(imported_transforms, namespace)
                return False

            # Process found objects
            return self.object_processor.process_found_objects_with_cleanup(
                found_objects, namespace, imported_transforms, fuzzy_match_map
            )

        except Exception as e:
            self.logger.error(f"Error during import processing: {e}")
            # Clean up on error
            self._cleanup_on_error(imported_transforms, [])
            return False

    def _log_dry_run_results(self, found_objects: List, fuzzy_match_map: Dict) -> None:
        """Log the results of dry run analysis."""
        self.logger.info(f"[DRY-RUN] Would find {len(found_objects)} matching objects:")
        for obj in found_objects:
            obj_name = self._clean_namespace_name(obj.nodeName())
            if obj in fuzzy_match_map:
                target_name = fuzzy_match_map[obj]
                self.logger.info(f"  - {obj_name} (fuzzy match for '{target_name}')")
            else:
                self.logger.info(f"  - {obj_name} (exact match)")

    def _cleanup_dry_run_import(
        self, imported_transforms: List, namespace: str
    ) -> None:
        """Clean up imported objects after dry-run analysis."""
        try:
            if imported_transforms:
                self.logger.info(
                    f"Cleaning up {len(imported_transforms)} imported objects"
                )
                pm.delete(imported_transforms)

            # Clean up namespace if it exists and is empty
            if namespace and pm.namespace(exists=namespace):
                try:
                    remaining_objects = pm.ls(f"{namespace}:*")
                    if not remaining_objects:
                        pm.namespace(removeNamespace=namespace)
                        self.logger.info(f"Cleaned up empty namespace: {namespace}")
                except Exception as ns_error:
                    self.logger.debug(
                        f"Could not clean up namespace {namespace}: {ns_error}"
                    )

        except Exception as cleanup_error:
            self.logger.warning(f"Failed to clean up imported objects: {cleanup_error}")

    def _cleanup_on_error(self, imported_transforms: List, found_objects: List) -> None:
        """Clean up on error."""
        try:
            if imported_transforms:
                pm.delete(imported_transforms)
                self.logger.info("Cleaned up imported objects after error")
        except Exception as cleanup_error:
            self.logger.error(f"Failed to clean up after error: {cleanup_error}")


def pull_objects_from_scene(
    object_names: List[str],
    source_scene_file: Union[str, Path],
    backup: bool = True,
    **kwargs,
) -> bool:
    """Pull specific objects from source scene file."""
    swapper = ObjectSwapper(**kwargs)
    return swapper.pull_objects_from_scene(object_names, source_scene_file, backup)


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    # Example usage:
    # file = "C5_AFT_COMP_ASSEMBLY.fbx"
    file = "C5_AFT_COMP_ASSEMBLY_module.ma"
    # path = f"O:\\Dropbox (Moth+Flame)\\Moth+Flame Dropbox\\Ryan Simpson\\_tests\\hierarchy_test\\{file}"
    path = f"O:\\Dropbox (Moth+Flame)\\Moth+Flame Dropbox\\Moth+Flame Team Folder\\PRODUCTION\\AF\\C-5M\\PRODUCTION\\Maya\\Horizontal_Stab\\scenes\\modules\\C5_AFT_COMP_ASSEMBLY\\{file}"

    objs = pm.selected(type="transform")

    # Pull specific objects from another scene into current scene with comprehensive material handling
    pull_objects_from_scene(objs, path, fuzzy_matching=True, dry_run=1)


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
# This refactored module provides hierarchy-aware object swapping between Maya ASCII files
# with clear separation of concerns:
#
# - ValidationManager: Input validation and backup operations
# - NamespaceSandbox: Scene import/export operations and namespace management
# - MatchingEngine: Consolidated matching logic (no duplication)
# - ObjectProcessor: Object processing and cleanup
# - ObjectSwapper: High-level orchestration
#
# Key improvements:
# - Eliminated duplicate fuzzy matching code
# - Clear single responsibility for each class
# - Better testability through dependency injection
# - Consistent logging patterns
# - Reduced coupling between components
# - Removed unnecessary SceneManager wrapper around NamespaceSandbox
# --------------------------------------------------------------------------------------------
