# !/usr/bin/python
# coding=utf-8
import tempfile
from pathlib import Path
from typing import Union, Optional, Dict, List
import pymel.core as pm
import pythontk as ptk

from pythontk.str_utils import FuzzyMatcher
from mayatk.mat_utils.material_preserver import MaterialPreserver


class ObjectSwapper(ptk.LoggingMixin):
    """Handles pushing/pulling objects between Maya scenes without modifying source scene."""

    # Constants
    TEMP_NAMESPACE_PREFIX = "temp_ref_"
    DRY_RUN_NAMESPACE = "dry_run_temp"

    def __init__(
        self,
        dry_run: bool = True,
        fuzzy_matching: bool = True,
    ):
        super().__init__()
        self.dry_run = dry_run
        self.fuzzy_matching = fuzzy_matching
        self.backup_dir = None
        self.source_objects: Dict[str, pm.nt.Transform] = {}
        self.temp_namespace = "cross_scene_temp"

        # Initialize material preserver
        self.material_preserver = MaterialPreserver()

    def push_objects_to_scene(
        self,
        target_objects: Union[List[str], List[pm.PyNode], str, pm.PyNode],
        target_scene_file: Union[str, Path],
        backup: bool = True,
    ) -> bool:
        """Push objects from current scene to target scene.

        Parameters:
            target_objects: Object names, PyNodes, or mixed list to push to target scene
            target_scene_file: Path to target Maya ASCII file
            backup: Whether to create backup of target scene
        """
        # pm.ls() handles all input types automatically
        target_pynodes = pm.ls(target_objects, type="transform")

        if not target_pynodes:
            self.logger.error("No valid transform objects found to push")
            return False

        object_names = [obj.nodeName() for obj in target_pynodes]
        target_scene_file = Path(target_scene_file)

        if not self._validate_inputs(object_names, target_scene_file, "Push"):
            return False

        try:
            temp_export_file = self._export_objects_to_temp(object_names)
            if not temp_export_file:
                return False

            current_scene = pm.sceneName()
            success = self._import_to_target_scene(
                temp_export_file, target_scene_file, backup
            )

            if current_scene:
                pm.openFile(current_scene, force=True)
                self.logger.info(f"Returned to original scene: {current_scene}")

            if temp_export_file and temp_export_file.exists():
                temp_export_file.unlink()

            return success

        except Exception as e:
            self.logger.error(f"Push operation failed: {e}")
            return False

    def pull_objects_from_scene(
        self,
        target_objects: Union[List[str], List[pm.PyNode], str, pm.PyNode],
        source_scene_file: Union[str, Path],
        backup: bool = True,
    ) -> bool:
        """Pull objects from source scene into current scene.

        Parameters:
            target_objects: Object names, PyNodes, or mixed list to pull from source scene
            source_scene_file: Path to source Maya ASCII file
            backup: Whether to create backup of current scene
        """
        # pm.ls() handles single objects, lists, and mixed types automatically
        target_pynodes = pm.ls(target_objects, type="transform")

        if not target_pynodes:
            self.logger.error("No valid transform objects found to pull")
            return False

        # Extract names for validation and logging
        object_names = [obj.nodeName() for obj in target_pynodes]
        source_scene_file = Path(source_scene_file)

        if not self._validate_inputs(object_names, source_scene_file, "Pull"):
            return False

        try:
            if backup and not self.dry_run:
                self._create_backup()

            return self._import_source_objects(source_scene_file, object_names)

        except Exception as e:
            self.logger.error(f"Pull operation failed: {e}")
            return False

    # Legacy method name for backward compatibility
    def swap_objects(
        self,
        target_objects: List[str],
        source_file: Union[str, Path],
        backup: bool = True,
    ) -> bool:
        """Legacy method - now redirects to pull_objects_from_scene for backward compatibility."""
        self.logger.info("Note: swap_objects is now pull_objects_from_scene")
        return self.pull_objects_from_scene(target_objects, source_file, backup)

    def _validate_inputs(
        self, objects: List[str], scene_file: Path, operation: str
    ) -> bool:
        """Validate inputs for both push and pull operations."""
        if not scene_file.exists():
            self.logger.error(f"{operation} scene file not found: {scene_file}")
            return False

        if scene_file.suffix.lower() != ".ma":
            self.logger.error(
                f"Only Maya ASCII (.ma) files supported: {scene_file.suffix}"
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

    @staticmethod
    def _clean_namespace_name(namespaced_name: str) -> str:
        """Extract clean name without namespace prefix."""
        return namespaced_name.split(":")[-1]

    def _create_unique_reference_namespace(self) -> str:
        """Create a unique namespace for referencing."""
        import time

        return f"{self.TEMP_NAMESPACE_PREFIX}{int(time.time())}"

    def _safely_remove_reference(self, reference_node) -> None:
        """Safely remove a Maya reference."""
        try:
            ref_filename = pm.referenceQuery(reference_node, filename=True)
            import maya.mel as mel

            mel.eval(f'file -removeReference "{ref_filename}";')
            self.logger.debug("Removed temporary reference")
        except Exception as ref_error:
            self.logger.warning(f"Could not remove reference: {ref_error}")

    def _handle_existing_object_replacement(
        self, clean_name: str
    ) -> Optional[pm.PyNode]:
        """Handle replacement of existing objects, returning parent info."""
        if pm.objExists(clean_name):
            self.logger.info(f"Object '{clean_name}' already exists - will replace it")
            existing_obj = pm.PyNode(clean_name)
            parent = existing_obj.getParent()
            pm.delete(existing_obj)
            self.logger.debug(f"Deleted existing object: {clean_name}")
            return parent
        return None

    def _find_objects_in_namespace(
        self, namespace: str, target_objects: List[str]
    ) -> List:
        """Find objects in the specified namespace (works for both reference and import)."""
        try:
            # Get all transform nodes in the namespace
            if namespace:
                nodes = pm.ls(f"{namespace}:*", type="transform")
            else:
                nodes = pm.ls(type="transform")

            available_transforms = {}
            for node in nodes:
                base_name = self._clean_namespace_name(node.nodeName())
                available_transforms[base_name] = node

            found_objects = []
            target_set = set(target_objects)

            # First pass: exact matches
            for target_name in target_objects:
                if target_name in available_transforms:
                    found_objects.append(available_transforms[target_name])

            # Second pass: fuzzy matching for unmatched targets
            if self.fuzzy_matching:
                found_names = [
                    self._clean_namespace_name(obj.nodeName()) for obj in found_objects
                ]
                unmatched_targets = target_set - set(found_names)
                available_names = list(available_transforms.keys())

                for target_name in unmatched_targets:
                    match_result = FuzzyMatcher.find_best_match(
                        target_name, available_names
                    )
                    if match_result:
                        best_match, score = match_result
                        found_objects.append(available_transforms[best_match])
                        self.logger.info(
                            f"Fuzzy match: '{target_name}' -> '{best_match}' (score: {score:.2f})"
                        )

            return found_objects

        except Exception as e:
            self.logger.error(f"Failed to find objects in namespace {namespace}: {e}")
            return []

    def _duplicate_object_with_materials(
        self, obj, clean_name: str
    ) -> Optional[pm.PyNode]:
        """Duplicate a referenced object while preserving materials."""
        # Get material assignments before duplication using MaterialPreserver
        original_material_assignments = (
            self.material_preserver.collect_material_assignments(obj)
        )

        # Duplicate the referenced object
        duplicated_nodes = pm.duplicate(
            obj,
            name=clean_name,
            parentOnly=False,
            inputConnections=True,
            returnRootsOnly=True,
        )

        if not duplicated_nodes:
            self.logger.warning(f"Failed to duplicate object: {obj.nodeName()}")
            return None

        duplicated_obj = duplicated_nodes[0]

        # Ensure clean name
        if duplicated_obj.nodeName() != clean_name:
            duplicated_obj.rename(clean_name)

        # Handle materials using MaterialPreserver
        self.material_preserver.apply_materials_to_object(
            duplicated_obj, original_material_assignments
        )

        return duplicated_obj

    def _export_objects_to_temp(self, target_objects: List[str]) -> Optional[Path]:
        """Export objects to temporary file using pm.ls() for robust object handling."""
        try:
            # pm.ls() handles the input normalization automatically
            objects_to_export = pm.ls(target_objects, type="transform")

            if not objects_to_export:
                self.logger.error("No valid transform objects to export")
                return None

            temp_dir = Path(tempfile.mkdtemp(prefix="maya_cross_scene_"))
            temp_file = temp_dir / "exported_objects.ma"

            if self.dry_run:
                self.logger.notice(
                    f"[DRY-RUN] Would export {len(objects_to_export)} objects to {temp_file}"
                )
                return temp_file

            pm.select(objects_to_export)
            pm.exportSelected(str(temp_file), type="mayaAscii", force=True)

            self.logger.info(
                f"Exported {len(objects_to_export)} objects to temporary file"
            )
            return temp_file

        except Exception as e:
            self.logger.error(f"Failed to export objects to temp file: {e}")
            return None

    def _import_to_target_scene(
        self, temp_file: Path, target_scene: Path, backup: bool
    ) -> bool:
        """Import objects into target scene."""
        try:
            if self.dry_run:
                self.logger.notice(
                    f"[DRY-RUN] Would open {target_scene} and import objects"
                )
                return True

            # Create backup of target scene if requested
            if backup:
                backup_path = target_scene.with_suffix(f".backup{target_scene.suffix}")
                backup_path.write_bytes(target_scene.read_bytes())
                self.logger.info(f"Created backup: {backup_path}")

            # Open target scene
            pm.openFile(str(target_scene), force=True)
            self.logger.info(f"Opened target scene: {target_scene}")

            # Import the objects
            imported_nodes = pm.importFile(str(temp_file), returnNewNodes=True)

            if imported_nodes:
                self.logger.success(
                    f"Imported {len(imported_nodes)} nodes into target scene"
                )

                # Save the target scene
                pm.saveFile(force=True)
                self.logger.info("Saved target scene with new objects")

                return True
            else:
                self.logger.error("No nodes were imported")
                return False

        except Exception as e:
            self.logger.error(f"Failed to import to target scene: {e}")
            return False

    def _import_source_objects(
        self, source_file: Path, target_objects: List[str]
    ) -> bool:
        """Import objects from source scene using reference and duplicate method."""
        try:
            self.logger.info(f"Importing from: {source_file}")

            if self.dry_run:
                return self._dry_run_analysis(source_file, target_objects)

            # Create reference and find objects
            reference_namespace = self._create_unique_reference_namespace()
            reference_node = pm.createReference(
                str(source_file), namespace=reference_namespace
            )
            self.logger.debug(
                f"Created reference with namespace: {reference_namespace}"
            )

            found_objects = self._find_objects_in_reference(
                reference_namespace, target_objects
            )

            if not found_objects:
                self.logger.warning("No matching objects found in source scene")
                self._safely_remove_reference(reference_node)
                return False

            # Process the found objects
            success = self._process_found_objects(found_objects, reference_node)

            # Cleanup reference
            self._safely_remove_reference(reference_node)

            return success

        except Exception as e:
            self.logger.error(f"Failed to import source objects: {e}")
            # Try to clean up reference if it exists
            if "reference_node" in locals():
                self._safely_remove_reference(reference_node)
            return False

    def _process_found_objects(self, found_objects: List, reference_node) -> bool:
        """Process found objects by duplicating them with materials and hierarchy."""
        self.logger.info(f"Found {len(found_objects)} matching objects:")
        for obj in found_objects:
            self.logger.info(f"  - {obj.nodeName()}")

        nodes_before = len(pm.ls())

        # Collect parent info for objects that will be replaced
        parent_info = self._collect_parent_info(found_objects)

        # Duplicate all objects with materials
        imported_objects = self._duplicate_all_objects(found_objects, parent_info)

        # Verify cleanup and log results
        self._verify_import_results(imported_objects, nodes_before)

        return len(imported_objects) > 0

    def _collect_parent_info(
        self, found_objects: List
    ) -> Dict[str, Optional[pm.PyNode]]:
        """Collect parent information for objects that will be replaced."""
        parent_info = {}
        for obj in found_objects:
            clean_name = self._clean_namespace_name(obj.nodeName())
            parent = self._handle_existing_object_replacement(clean_name)
            if parent:
                parent_info[clean_name] = parent
        return parent_info

    def _duplicate_all_objects(self, found_objects: List, parent_info: Dict) -> List:
        """Duplicate all found objects with materials and hierarchy restoration."""
        imported_objects = []

        for obj in found_objects:
            clean_name = self._clean_namespace_name(obj.nodeName())
            duplicated_obj = self._duplicate_object_with_materials(obj, clean_name)

            if duplicated_obj:
                # Restore parent hierarchy
                if clean_name in parent_info and parent_info[clean_name]:
                    pm.parent(duplicated_obj, parent_info[clean_name])
                    self.logger.debug(
                        f"Parented {clean_name} to {parent_info[clean_name].nodeName()}"
                    )

                imported_objects.append(duplicated_obj)
                self.logger.debug(
                    f"Duplicated with materials: {obj.nodeName()} -> {clean_name}"
                )
            else:
                self.logger.warning(f"Failed to duplicate object: {obj.nodeName()}")

        return imported_objects

    def _verify_import_results(self, imported_objects: List, nodes_before: int) -> None:
        """Verify and log import results."""
        nodes_after = len(pm.ls())
        nodes_added = nodes_after - nodes_before
        self.logger.debug(
            f"Node count: before={nodes_before}, after={nodes_after}, net added={nodes_added}"
        )

        if imported_objects:
            self.logger.success(
                f"Successfully pulled {len(imported_objects)} objects with materials"
            )
        else:
            self.logger.error("No objects were successfully imported")

    def _dry_run_analysis(self, source_file: Path, target_objects: List[str]) -> bool:
        """Analyze what would be imported in dry-run mode."""
        try:
            # Clean up any existing temp namespace
            if pm.namespace(exists=self.DRY_RUN_NAMESPACE):
                pm.namespace(
                    removeNamespace=self.DRY_RUN_NAMESPACE, deleteNamespaceContent=True
                )

            pm.namespace(add=self.DRY_RUN_NAMESPACE)

            # Import for analysis
            imported_nodes = pm.importFile(
                str(source_file),
                namespace=self.DRY_RUN_NAMESPACE,
                returnNewNodes=True,
            )

            if imported_nodes:
                # Update namespace name if Maya changed it
                first_node_name = imported_nodes[0].nodeName()
                if ":" in first_node_name:
                    actual_namespace = first_node_name.split(":")[0]
                else:
                    actual_namespace = self.DRY_RUN_NAMESPACE

                # Find matching objects
                found_objects = self._find_objects_in_namespace("", target_objects)

                for obj in found_objects:
                    clean_name = self._clean_namespace_name(obj.nodeName())
                    self.logger.notice(f"[DRY-RUN] Would pull: {clean_name}")

                # Clean up analysis import
                pm.namespace(
                    removeNamespace=actual_namespace, deleteNamespaceContent=True
                )
                return len(found_objects) > 0

            return False

        except Exception as e:
            self.logger.error(f"Dry-run analysis failed: {e}")
            return False

    def _find_objects_in_reference(
        self, reference_namespace: str, target_objects: List[str]
    ) -> List:
        """Find objects in the referenced namespace."""
        return self._find_objects_in_namespace(reference_namespace, target_objects)

    def _create_backup(self):
        """Create backup of current scene."""
        try:
            current_scene = pm.sceneName()
            if not current_scene:
                self.logger.warning("No scene file to backup")
                return

            # Save current scene first to ensure we have latest changes
            pm.saveFile(force=True)

            # Create backup with timestamp suffix
            current_path = Path(current_scene)
            backup_path = current_path.with_suffix(f".backup{current_path.suffix}")

            # Copy the file
            import shutil

            shutil.copy2(current_path, backup_path)

            self.logger.info(f"Created backup: {backup_path}")

        except Exception as e:
            self.logger.error(f"Failed to create backup: {e}")

    def push_selected_objects(
        self, target_scene_file: Union[str, Path], backup: bool = True
    ) -> bool:
        """Convenience method to push currently selected objects."""
        selected = pm.selected(type="transform")
        if not selected:
            self.logger.warning("No transform nodes selected")
            return False

        target_objects = [obj.nodeName() for obj in selected]
        self.logger.info(f"Pushing {len(target_objects)} selected objects")

        return self.push_objects_to_scene(target_objects, target_scene_file, backup)


def push_selected_objects(
    target_scene_file: Union[str, Path], backup: bool = True, **kwargs
) -> bool:
    """Push selected objects to target scene file."""
    swapper = ObjectSwapper(**kwargs)
    return swapper.push_selected_objects(target_scene_file, backup)


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
    file = "C5_AFT_COMP_ASSEMBLY_current"
    path = f"O:\\Dropbox (Moth+Flame)\\Moth+Flame Dropbox\\Ryan Simpson\\_tests\\hierarchy_test\\{file}.ma"

    objs = pm.selected(type="transform")

    # Push selected objects to another scene (no modification of current scene)
    # push_selected_objects(path, dry_run=True)

    # Pull specific objects from another scene into current scene with comprehensive material handling
    pull_objects_from_scene(objs, path, dry_run=False)


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
# This module provides hierarchy-aware object swapping between Maya ASCII files.
# Key features:
# - Preserves parent-child relationships
# - Maintains world transforms
# - Supports dry-run mode for testing
# - Creates automatic backups
# - Handles namespace conflicts
# - Provides detailed logging
# - Comprehensive material preservation using MaterialPreserver class
# - Shader utilities integration with ShaderAttributeMap and MatUtils
# - Supports all shader types and intelligent material network preservation
# --------------------------------------------------------------------------------------------
