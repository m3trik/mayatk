# !/usr/bin/python
# coding=utf-8
from pathlib import Path
from typing import Union, Optional, Dict, List, Any
import pythontk as ptk
import pymel.core as pm

# From this package
from mayatk.env_utils.temp_import import TempImport


class ObjectSwapper(ptk.LoggingMixin):
    """Handles pushing/pulling objects between Maya scenes without modifying source scene.

    Parameters:
        dry_run: If True, perform analysis without making changes
        fuzzy_matching: If True, allow fuzzy name matching when exact matches fail
    """

    def __init__(
        self,
        dry_run: bool = True,
        fuzzy_matching: bool = True,
    ):
        super().__init__()
        self.dry_run = dry_run
        self.fuzzy_matching = fuzzy_matching
        self.backup_dir = None
        self.source_objects: Dict[str, Any] = {}

        # Initialize import manager
        self.import_manager = TempImport(dry_run=dry_run, fuzzy_matching=fuzzy_matching)

    def push_objects_to_scene(
        self,
        target_objects: Union[List[str], List[Any], str, Any],
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
            temp_export_file = self.import_manager.export_objects_to_temp(object_names)
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
        target_objects: Union[List[str], List[Any], str, Any],
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

    def _handle_existing_object_replacement(self, clean_name: str) -> Optional[Any]:
        """Handle replacement of existing objects, returning parent info."""
        if pm.objExists(clean_name):
            self.logger.info(f"Object '{clean_name}' already exists - will replace it")
            existing_obj = pm.PyNode(clean_name)
            parent = existing_obj.getParent()
            pm.delete(existing_obj)
            self.logger.debug(f"Deleted existing object: {clean_name}")
            return parent
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

            # Import the objects with options to reduce warnings
            imported_nodes = pm.importFile(
                str(temp_file),
                returnNewNodes=True,
                preserveReferences=False,  # Don't preserve external references
            )

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
        """Import objects from source scene using import method to avoid reference warnings."""
        try:
            self.logger.info(f"Importing from: {source_file}")

            if self.dry_run:
                # Use enhanced analysis for dry run that matches the real fuzzy matching logic
                return self._dry_run_analysis(source_file, target_objects)

            # Import objects and get import information
            import_info = self.import_manager.import_with_namespace(
                source_file, "import_"
            )

            if not import_info:
                self.logger.error("Failed to import source file")
                return False

            temp_namespace = import_info["namespace"]
            imported_transforms = import_info["transforms"]

            try:
                self.logger.debug(
                    f"Imported {len(imported_transforms)} transform nodes total"
                )

                if not imported_transforms:
                    self.logger.warning("No transform nodes found in imported file")
                    return False

                # Show sample of what was imported for debugging
                sample_names = [node.nodeName() for node in imported_transforms[:5]]
                sample_clean_names = [
                    self.import_manager._clean_namespace_name(node.nodeName())
                    for node in imported_transforms[:5]
                ]
                self.logger.debug(f"Sample imported transform names: {sample_names}")
                self.logger.debug(f"Sample clean names: {sample_clean_names}")

                # Find matching objects among imported transforms
                found_objects = []
                # Track fuzzy matches for proper renaming
                fuzzy_match_map = {}  # Maps imported_node -> target_name

                for target_name in target_objects:
                    # First try exact match - compare clean names (without namespace)
                    matching_nodes = [
                        node
                        for node in imported_transforms
                        if self.import_manager._clean_namespace_name(node.nodeName())
                        == target_name
                    ]
                    if matching_nodes:
                        found_objects.extend(matching_nodes)
                        continue

                    # Debug: Show available objects that contain the target name
                    available_containing_target = [
                        self.import_manager._clean_namespace_name(node.nodeName())
                        for node in imported_transforms
                        if target_name
                        in self.import_manager._clean_namespace_name(node.nodeName())
                    ]
                    if available_containing_target:
                        self.logger.debug(
                            f"Objects containing '{target_name}': {available_containing_target[:10]}"
                        )
                    else:
                        self.logger.debug(
                            f"No objects found containing '{target_name}'"
                        )

                    # Show a sample of all available objects for context
                    sample_objects = [
                        self.import_manager._clean_namespace_name(node.nodeName())
                        for node in imported_transforms[:20]
                    ]
                    self.logger.debug(f"Sample available objects: {sample_objects}")

                    # Try fuzzy matching if enabled
                    if self.import_manager.fuzzy_matching:
                        # Standard behavior: Skip fuzzy matching when target already exists in current scene
                        # This prevents pulling the wrong object when user specifies an exact name
                        if pm.objExists(target_name):
                            self.logger.warning(
                                f"Skipping fuzzy match for '{target_name}' - object already exists in current scene. "
                                f"Use exact name matching only to avoid pulling wrong object."
                            )
                            continue

                        # Get clean names for fuzzy matching
                        import_names = [
                            self.import_manager._clean_namespace_name(node.nodeName())
                            for node in imported_transforms
                        ]
                        matches = ptk.FuzzyMatcher.find_all_matches(
                            [target_name], import_names, score_threshold=0.7
                        )

                        # Try each match in order of score (best first)
                        matched_successfully = False
                        if target_name in matches:
                            # If there are multiple matches, try them in order
                            # Create a list of potential matches - start with the best one
                            potential_matches = [
                                (matches[target_name][0], matches[target_name][1])
                            ]

                            # Try to get additional matches if supported
                            try:
                                all_matches = ptk.FuzzyMatcher.find_all_matches(
                                    [target_name],
                                    import_names,
                                    score_threshold=0.6,
                                    return_all=True,
                                )
                                if target_name in all_matches and isinstance(
                                    all_matches[target_name], list
                                ):
                                    potential_matches = all_matches[target_name]
                            except (TypeError, AttributeError):
                                # Fallback if return_all parameter not supported
                                self.logger.debug(
                                    "Using single best match (return_all not supported)"
                                )

                            self.logger.debug(
                                f"Trying {len(potential_matches)} potential matches for {target_name}"
                            )

                            for matched_name, score in potential_matches:
                                self.logger.debug(
                                    f"  Evaluating match: '{target_name}' -> '{matched_name}' (score: {score:.2f})"
                                )

                                # Find the actual node that corresponds to the matched clean name
                                try:
                                    matching_node = next(
                                        node
                                        for node in imported_transforms
                                        if self.import_manager._clean_namespace_name(
                                            node.nodeName()
                                        )
                                        == matched_name
                                    )
                                except StopIteration:
                                    self.logger.debug(
                                        f"  No node found for match '{matched_name}', trying next..."
                                    )
                                    continue  # Try next match

                                # ENHANCED FUZZY MATCHING: Generic approach
                                # If fuzzy match found a container, search inside for better matches
                                geometry_found = False
                                better_match_found = False

                                # Check if the matched object has children that might be better matches
                                if hasattr(matching_node, "getChildren"):
                                    try:
                                        # Function to recursively search for better matches inside containers
                                        def find_better_match_recursive(
                                            node, target_name, depth=0, max_depth=3
                                        ):
                                            if depth > max_depth:
                                                return None, False

                                            children = node.getChildren()
                                            for child in children:
                                                child_clean_name = self.import_manager._clean_namespace_name(
                                                    child.nodeName()
                                                )

                                                # Check if this child is a better match than the parent
                                                child_has_shapes = (
                                                    bool(child.getShapes())
                                                    if hasattr(child, "getShapes")
                                                    else False
                                                )

                                                # Calculate match quality - exact name match gets highest priority
                                                is_exact_match = (
                                                    child_clean_name == target_name
                                                )
                                                is_better_match = False

                                                if is_exact_match:
                                                    # Exact name match - this is definitely better
                                                    is_better_match = True
                                                    self.logger.debug(
                                                        f"  Found exact name match: {child_clean_name}"
                                                    )
                                                elif (
                                                    child_has_shapes
                                                    and target_name in child_clean_name
                                                ):
                                                    # Child has geometry and similar name - likely better than empty container
                                                    is_better_match = True
                                                    self.logger.debug(
                                                        f"  Found geometry match: {child_clean_name} (has shapes)"
                                                    )
                                                elif (
                                                    len(child_clean_name)
                                                    > len(matched_name)
                                                    and target_name in child_clean_name
                                                ):
                                                    # Child has more specific/longer name that contains target - might be better
                                                    is_better_match = True
                                                    self.logger.debug(
                                                        f"  Found more specific match: {child_clean_name}"
                                                    )

                                                self.logger.debug(
                                                    f"  Checking child: {child_clean_name}, has_shapes: {child_has_shapes}, is_better: {is_better_match}"
                                                )

                                                if is_better_match:
                                                    return child, child_has_shapes

                                                # If this child doesn't match but has children, search recursively
                                                if child.getChildren():
                                                    (
                                                        recursive_result,
                                                        recursive_has_shapes,
                                                    ) = find_better_match_recursive(
                                                        child,
                                                        target_name,
                                                        depth + 1,
                                                        max_depth,
                                                    )
                                                    if recursive_result:
                                                        return (
                                                            recursive_result,
                                                            recursive_has_shapes,
                                                        )

                                            return None, False

                                        # Search for better matches inside the container
                                        self.logger.debug(
                                            f"Searching inside container '{matched_name}' for better match for '{target_name}'"
                                        )

                                        better_child, child_has_shapes = (
                                            find_better_match_recursive(
                                                matching_node, target_name
                                            )
                                        )

                                        if better_child:
                                            # Found a better match inside the container
                                            original_matched_name = matched_name
                                            matching_node = better_child
                                            matched_name = self.import_manager._clean_namespace_name(
                                                better_child.nodeName()
                                            )
                                            score = 0.95  # High score for exact match found inside container
                                            better_match_found = True
                                            geometry_found = child_has_shapes

                                            self.logger.info(
                                                f"Found better match inside container: '{original_matched_name}' -> '{matched_name}' (has_shapes: {child_has_shapes})"
                                            )
                                        else:
                                            self.logger.debug(
                                                f"No better match found inside container '{matched_name}'"
                                            )

                                    except Exception as search_error:
                                        self.logger.debug(
                                            f"Could not search inside container: {search_error}"
                                        )

                                # Decide whether to use this match
                                should_use_match = True

                                # If we searched inside a container but found no better match,
                                # we might want to be more cautious about using the container itself
                                container_has_shapes = (
                                    bool(matching_node.getShapes())
                                    if hasattr(matching_node, "getShapes")
                                    else False
                                )

                                # Only skip the match if:
                                # 1. We searched inside a container (has children)
                                # 2. Found no better match inside
                                # 3. The container itself has no geometry/content
                                # 4. The match quality isn't very high
                                if (
                                    hasattr(matching_node, "getChildren")
                                    and matching_node.getChildren()
                                    and not better_match_found
                                    and not container_has_shapes
                                    and score < 0.9
                                ):
                                    should_use_match = False
                                    self.logger.warning(
                                        f"Skipping low-quality container match: '{target_name}' -> '{matched_name}' (score: {score:.2f}, empty container)"
                                    )

                                if should_use_match:
                                    found_objects.append(matching_node)
                                    # Store the fuzzy match mapping
                                    fuzzy_match_map[matching_node] = target_name
                                    self.logger.info(
                                        f"Fuzzy match: '{target_name}' -> '{matched_name}' (score: {score:.2f})"
                                    )
                                    matched_successfully = True
                                    break  # Found a good match, stop trying alternatives
                                else:
                                    # Continue to try next potential match
                                    continue

                            if not matched_successfully:
                                self.logger.warning(
                                    f"No suitable fuzzy match found for {target_name} (tried {len(potential_matches)} options)"
                                )

                if not found_objects:
                    self.logger.warning("No matching objects found in imported scene")
                    # Additional debug info
                    self.logger.debug(f"Looking for: {target_objects}")
                    available_clean_names = [
                        self.import_manager._clean_namespace_name(node.nodeName())
                        for node in imported_transforms[:10]
                    ]
                    self.logger.debug(f"Available clean names: {available_clean_names}")

                    # Clean up the entire import since we're not using any objects
                    self.import_manager.cleanup_import(
                        temp_namespace, imported_transforms
                    )

                    return False

                # Process the found objects and clean up import
                success = self._process_found_objects_with_cleanup(
                    found_objects, temp_namespace, imported_transforms, fuzzy_match_map
                )

                return success

            except Exception as e:
                # Only clean up unused imported objects on error - but be very careful
                if "imported_transforms" in locals() and "found_objects" in locals():
                    try:
                        # Only delete objects that are definitely not being used
                        remaining_objects = []
                        for obj in imported_transforms:
                            # Skip if it's one of our found objects
                            if obj in found_objects:
                                continue
                            # Skip if object no longer exists (might have been processed)
                            if not pm.objExists(obj):
                                continue
                            # Only add to cleanup list if it's really unused
                            remaining_objects.append(obj)

                        if remaining_objects:
                            pm.delete(remaining_objects)
                            self.logger.debug(
                                f"Cleaned up {len(remaining_objects)} unused objects after error"
                            )
                    except Exception as cleanup_error:
                        self.logger.debug(f"Error during cleanup: {cleanup_error}")

                # Clean up namespace properly
                self.import_manager.cleanup_import(temp_namespace)

                raise e

        except Exception as e:
            self.logger.error(f"Failed to import source objects: {e}")
            return False

    def _process_found_objects(
        self, found_objects: List, fuzzy_match_map: Dict = None
    ) -> bool:
        """Process found objects by duplicating them with materials and hierarchy."""
        self.logger.info(f"Found {len(found_objects)} matching objects:")
        for obj in found_objects:
            self.logger.info(f"  - {obj.nodeName()}")

        nodes_before = len(pm.ls())

        # Collect parent info for objects that will be replaced
        parent_info = self._collect_parent_info(found_objects, fuzzy_match_map or {})

        # Duplicate all objects with materials
        imported_objects = self._process_all_objects(
            found_objects, parent_info, fuzzy_match_map or {}
        )

        # Verify cleanup and log results
        self._verify_import_results(imported_objects, nodes_before)

        return len(imported_objects) > 0

    def _process_found_objects_with_cleanup(
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

        # FIXED: Smart cleanup that preserves hierarchy
        if imported_objects:
            # Get all objects that should be preserved (processed objects + their parents)
            objects_to_preserve = set()

            for obj in processed_objects:
                if pm.objExists(obj):
                    objects_to_preserve.add(obj)
                    # Add all parents up the hierarchy
                    current = obj
                    while True:
                        try:
                            parent = current.getParent()
                            if parent and parent not in objects_to_preserve:
                                objects_to_preserve.add(parent)
                                current = parent
                            else:
                                break
                        except:
                            break

            # Only delete imported objects that are NOT in the preserve list
            remaining_objects = []
            for obj in imported_objects:
                if (
                    obj not in found_objects
                    and obj not in objects_to_preserve
                    and pm.objExists(obj)
                ):
                    remaining_objects.append(obj)

            if remaining_objects:
                pm.delete(remaining_objects)
                self.logger.debug(
                    f"Cleaned up {len(remaining_objects)} unused imported objects (preserved {len(objects_to_preserve)} hierarchy objects)"
                )

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
            self.logger.info(
                f"[CLEANUP] Removed {namespace} from tracking without cleanup"
            )

        # Verify cleanup and log results
        self._verify_import_results(processed_objects, nodes_before)

        return len(processed_objects) > 0

    def _collect_parent_info(
        self, found_objects: List, fuzzy_match_map: Dict
    ) -> Dict[str, Optional[Any]]:
        """Collect parent information for objects that will be replaced."""
        parent_info = {}
        for obj in found_objects:
            try:
                # Verify the found object still exists before any operations
                if not pm.objExists(obj):
                    self.logger.warning(
                        f"Found object {obj} no longer exists, skipping"
                    )
                    continue

                # Get the clean name - use fuzzy match target if available
                if obj in fuzzy_match_map:
                    clean_name = fuzzy_match_map[obj]
                    self.logger.debug(
                        f"Processing fuzzy match: imported='{obj.nodeName()}' -> target_name='{clean_name}'"
                    )
                else:
                    clean_name = self.import_manager._clean_namespace_name(
                        obj.nodeName()
                    )
                    self.logger.debug(
                        f"Processing exact match: imported='{obj.nodeName()}' -> target_name='{clean_name}'"
                    )

                # Check if this imported object is different from any existing object with the same name
                if pm.objExists(clean_name):
                    # Try to create PyNode safely - handle non-unique names
                    try:
                        existing_obj = pm.PyNode(clean_name)

                        # Use a more reliable comparison - check if they are actually different objects
                        try:
                            # Double-check both objects still exist before comparison
                            if not pm.objExists(obj) or not pm.objExists(existing_obj):
                                self.logger.debug(
                                    f"One of the objects no longer exists during comparison"
                                )
                                continue

                            # Get full DAG paths for comparison - use more defensive approach
                            obj_path = None
                            existing_path = None

                            try:
                                obj_path = (
                                    obj.fullPath()
                                    if hasattr(obj, "fullPath")
                                    else str(obj)
                                )
                            except:
                                obj_path = str(obj)

                            try:
                                existing_path = (
                                    existing_obj.fullPath()
                                    if hasattr(existing_obj, "fullPath")
                                    else str(existing_obj)
                                )
                            except:
                                existing_path = str(existing_obj)

                            # Check if they are actually different objects by comparing node names and full paths
                            # For fuzzy matching, we should always replace since the names are different
                            objects_are_different = False

                            # If the imported object has a different name than the target clean name,
                            # it's definitely a different object (fuzzy match case)
                            if obj.nodeName() != clean_name:
                                objects_are_different = True
                                self.logger.debug(
                                    f"Objects are different: imported='{obj.nodeName()}' vs existing='{clean_name}'"
                                )
                            # Otherwise check paths
                            elif (
                                obj_path != existing_path and obj_path and existing_path
                            ):
                                objects_are_different = True
                                self.logger.debug(
                                    f"Objects have different paths: imported='{obj_path}' vs existing='{existing_path}'"
                                )

                            if objects_are_different:
                                self.logger.info(
                                    f"Object '{clean_name}' already exists - will replace it"
                                )

                                # Get parent before deletion
                                parent = None
                                try:
                                    parent = existing_obj.getParent()
                                except:
                                    pass

                                # Delete the existing object safely
                                try:
                                    pm.delete(existing_obj)
                                    self.logger.debug(
                                        f"Deleted existing object: {clean_name}"
                                    )
                                    if parent:
                                        parent_info[clean_name] = parent
                                except Exception as del_error:
                                    self.logger.debug(
                                        f"Could not delete existing object: {del_error}"
                                    )
                            else:
                                self.logger.debug(
                                    f"Found object {clean_name} appears to be the same as existing - no replacement needed"
                                )
                        except Exception as comp_error:
                            # If comparison fails, be conservative and don't delete anything
                            self.logger.debug(
                                f"Could not compare objects safely: {comp_error}"
                            )

                    except Exception as pynode_error:
                        self.logger.debug(
                            f"Could not create PyNode for '{clean_name}': {pynode_error}"
                        )
                        # If we can't safely reference the existing object, we'll proceed with caution
                        # The rename operation in _process_all_objects will handle any conflicts
                        self.logger.debug(
                            f"Will proceed with processing {clean_name} - rename operation will handle conflicts"
                        )

                else:
                    self.logger.debug(
                        f"No existing object named {clean_name} to replace"
                    )

            except Exception as e:
                self.logger.warning(f"Error collecting parent info for {obj}: {e}")
                continue

        return parent_info

    def _process_all_objects(
        self, found_objects: List, parent_info: Dict, fuzzy_match_map: Dict
    ) -> List:
        """Process imported objects - rename them and restore hierarchy if needed."""
        processed_objects = []

        for obj in found_objects:
            try:
                # Verify object still exists before processing (more robust check)
                try:
                    if not pm.objExists(obj):
                        self.logger.warning(f"Object {obj} no longer exists, skipping")
                        continue
                    # Additional check - try to access the object's node name
                    obj.nodeName()
                except Exception as exist_check:
                    self.logger.warning(
                        f"Object {obj} is not accessible, skipping: {exist_check}"
                    )
                    continue

                # Get the target name - use fuzzy match target if available
                if obj in fuzzy_match_map:
                    clean_name = fuzzy_match_map[obj]
                    self.logger.debug(
                        f"Processing fuzzy match for rename: '{obj.nodeName()}' -> '{clean_name}'"
                    )
                else:
                    clean_name = self.import_manager._clean_namespace_name(
                        obj.nodeName()
                    )
                    self.logger.debug(
                        f"Processing exact match for rename: '{obj.nodeName()}' -> '{clean_name}'"
                    )

                # Check if there's an existing object with the same clean name that we need to replace
                existing_obj_to_replace = None
                if pm.objExists(clean_name):
                    try:
                        # Try to get the existing object
                        potential_existing = pm.PyNode(clean_name)
                        # Check if it's different from our imported object
                        if potential_existing != obj:
                            existing_obj_to_replace = potential_existing
                            self.logger.info(
                                f"Found existing object '{clean_name}' that will be replaced"
                            )
                    except Exception as check_error:
                        # If we can't check reliably, list all objects with this name
                        all_matching = pm.ls(clean_name)
                        if len(all_matching) > 1:
                            # Find the one that's not our imported object
                            for potential in all_matching:
                                if potential != obj:
                                    existing_obj_to_replace = potential
                                    self.logger.info(
                                        f"Found existing object '{clean_name}' to replace (resolved from multiple matches)"
                                    )
                                    break

                # If we found an existing object to replace, delete it first
                if existing_obj_to_replace:
                    try:
                        # Get parent info before deletion
                        parent = None
                        try:
                            parent = existing_obj_to_replace.getParent()
                        except:
                            pass

                        # Delete the existing object
                        pm.delete(existing_obj_to_replace)
                        self.logger.info(f"Deleted existing object: {clean_name}")

                        # Store parent info for later use
                        if parent and clean_name not in parent_info:
                            parent_info[clean_name] = parent

                    except Exception as delete_error:
                        self.logger.warning(
                            f"Could not delete existing object {clean_name}: {delete_error}"
                        )

                # Now ensure our imported object has the correct clean name
                if obj.nodeName() != clean_name:
                    try:
                        # The imported object needs to be renamed to the target name
                        original_name = obj.nodeName()

                        # CRITICAL FIX: Use a more reliable method to move objects out of namespace
                        # Instead of relying on rename, use parent to root and then rename
                        if ":" in original_name:
                            # Object is in a namespace - use parent to move to root namespace first
                            try:
                                # Get current parent (if any) before moving to root
                                current_parent = obj.getParent()

                                # Move to world (root namespace) to ensure it's out of import namespace
                                pm.parent(obj, world=True)
                                self.logger.debug(
                                    f"Moved object to root namespace: '{original_name}'"
                                )

                                # Store the original parent for later restoration if needed
                                if current_parent and clean_name not in parent_info:
                                    parent_info[clean_name] = current_parent

                            except Exception as parent_error:
                                self.logger.debug(
                                    f"Could not parent to world: {parent_error}"
                                )

                        # Now rename the object (it should be safely in root namespace)
                        obj.rename(clean_name)
                        final_name = obj.nodeName()

                        if final_name != clean_name:
                            self.logger.info(
                                f"Renamed imported object '{original_name}' to: {final_name} (Maya resolved naming conflict)"
                            )
                        else:
                            self.logger.info(
                                f"Renamed imported object '{original_name}' to: {clean_name}"
                            )
                    except Exception as rename_error:
                        self.logger.warning(
                            f"Could not rename object {obj} from '{obj.nodeName()}' to '{clean_name}': {rename_error}"
                        )
                        # Continue processing even if rename fails
                else:
                    self.logger.debug(f"Object already has correct name: {clean_name}")

                # Materials are automatically preserved during import - no additional handling needed
                self.logger.debug(
                    f"Materials automatically preserved for {obj.nodeName()}"
                )

                # Restore parent hierarchy if needed
                if clean_name in parent_info and parent_info[clean_name]:
                    try:
                        pm.parent(obj, parent_info[clean_name])
                        self.logger.debug(
                            f"Parented {clean_name} to {parent_info[clean_name].nodeName()}"
                        )
                    except Exception as parent_error:
                        self.logger.debug(
                            f"Could not restore parent for {clean_name}: {parent_error}"
                        )

                processed_objects.append(obj)
                self.logger.debug(f"Processed imported object: {clean_name}")

            except Exception as e:
                self.logger.warning(f"Failed to process object {obj}: {e}")

        return processed_objects

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
        """Analyze what would be imported in dry-run mode using enhanced fuzzy matching."""
        imported_transforms = None
        try:
            imported_transforms = self.import_manager.import_for_analysis(source_file)

            if not imported_transforms:
                self.logger.warning("[DRY-RUN] No transforms found in source file")
                return False

            self.logger.debug(
                f"[DRY-RUN] Imported {len(imported_transforms)} transform nodes for analysis"
            )

            # Apply the same enhanced fuzzy matching logic as the real import
            found_objects = []
            fuzzy_match_map = {}

            for target_name in target_objects:
                # First try exact match
                matching_nodes = [
                    node
                    for node in imported_transforms
                    if self.import_manager._clean_namespace_name(node.nodeName())
                    == target_name
                ]
                if matching_nodes:
                    found_objects.extend(matching_nodes)
                    self.logger.notice(f"[DRY-RUN] Exact match found: {target_name}")
                    continue

                # Debug: Show available objects that contain the target name
                available_containing_target = [
                    self.import_manager._clean_namespace_name(node.nodeName())
                    for node in imported_transforms
                    if target_name
                    in self.import_manager._clean_namespace_name(node.nodeName())
                ]
                if available_containing_target:
                    self.logger.debug(
                        f"[DRY-RUN] Objects containing '{target_name}': {available_containing_target[:10]}"
                    )
                else:
                    self.logger.debug(
                        f"[DRY-RUN] No objects found containing '{target_name}'"
                    )

                # Show a sample of all available objects for context
                sample_objects = [
                    self.import_manager._clean_namespace_name(node.nodeName())
                    for node in imported_transforms[:20]
                ]
                self.logger.debug(
                    f"[DRY-RUN] Sample available objects: {sample_objects}"
                )

                # Try fuzzy matching if enabled
                if self.import_manager.fuzzy_matching:
                    # Standard behavior: Skip fuzzy matching when target already exists in current scene
                    # This prevents pulling the wrong object when user specifies an exact name
                    if pm.objExists(target_name):
                        self.logger.warning(
                            f"[DRY-RUN] Skipping fuzzy match for '{target_name}' - object already exists in current scene. "
                            f"Use exact name matching only to avoid pulling wrong object."
                        )
                        continue

                    import_names = [
                        self.import_manager._clean_namespace_name(node.nodeName())
                        for node in imported_transforms
                    ]
                    matches = ptk.FuzzyMatcher.find_all_matches(
                        [target_name], import_names, score_threshold=0.7
                    )

                    matched_successfully = False
                    if target_name in matches:
                        # Get potential matches
                        potential_matches = [
                            (matches[target_name][0], matches[target_name][1])
                        ]

                        try:
                            all_matches = ptk.FuzzyMatcher.find_all_matches(
                                [target_name],
                                import_names,
                                score_threshold=0.6,
                                return_all=True,
                            )
                            if target_name in all_matches and isinstance(
                                all_matches[target_name], list
                            ):
                                potential_matches = all_matches[target_name]
                        except (TypeError, AttributeError):
                            pass

                        self.logger.debug(
                            f"[DRY-RUN] Trying {len(potential_matches)} potential matches for {target_name}"
                        )

                        for matched_name, score in potential_matches:
                            self.logger.debug(
                                f"[DRY-RUN] Evaluating match: '{target_name}' -> '{matched_name}' (score: {score:.2f})"
                            )

                            # Find the actual node
                            try:
                                matching_node = next(
                                    node
                                    for node in imported_transforms
                                    if self.import_manager._clean_namespace_name(
                                        node.nodeName()
                                    )
                                    == matched_name
                                )
                            except StopIteration:
                                continue

                            # Apply the same container search logic for dry run
                            better_match_found = False
                            if hasattr(matching_node, "getChildren"):
                                try:

                                    def find_better_match_recursive(
                                        node, target_name, depth=0, max_depth=3
                                    ):
                                        if depth > max_depth:
                                            return None, False

                                        children = node.getChildren()
                                        for child in children:
                                            child_clean_name = self.import_manager._clean_namespace_name(
                                                child.nodeName()
                                            )
                                            child_has_shapes = (
                                                bool(child.getShapes())
                                                if hasattr(child, "getShapes")
                                                else False
                                            )

                                            # Calculate match quality - exact name match gets highest priority
                                            is_exact_match = (
                                                child_clean_name == target_name
                                            )
                                            is_better_match = False

                                            if is_exact_match:
                                                # Exact name match - this is definitely better
                                                is_better_match = True
                                                self.logger.debug(
                                                    f"[DRY-RUN] Found exact name match: {child_clean_name}"
                                                )
                                            elif (
                                                child_has_shapes
                                                and target_name in child_clean_name
                                            ):
                                                # Child has geometry and similar name - likely better than empty container
                                                is_better_match = True
                                                self.logger.debug(
                                                    f"[DRY-RUN] Found geometry match: {child_clean_name} (has shapes)"
                                                )
                                            elif (
                                                len(child_clean_name)
                                                > len(matched_name)
                                                and target_name in child_clean_name
                                            ):
                                                # Child has more specific/longer name that contains target - might be better
                                                is_better_match = True
                                                self.logger.debug(
                                                    f"[DRY-RUN] Found more specific match: {child_clean_name}"
                                                )

                                            self.logger.debug(
                                                f"[DRY-RUN] Checking child: {child_clean_name}, has_shapes: {child_has_shapes}, is_better: {is_better_match}"
                                            )

                                            if is_better_match:
                                                return child, child_has_shapes

                                            # If this child doesn't match but has children, search recursively
                                            if child.getChildren():
                                                (
                                                    recursive_result,
                                                    recursive_has_shapes,
                                                ) = find_better_match_recursive(
                                                    child,
                                                    target_name,
                                                    depth + 1,
                                                    max_depth,
                                                )
                                                if recursive_result:
                                                    return (
                                                        recursive_result,
                                                        recursive_has_shapes,
                                                    )

                                        return None, False

                                    better_child, child_has_shapes = (
                                        find_better_match_recursive(
                                            matching_node, target_name
                                        )
                                    )

                                    if better_child:
                                        original_matched_name = matched_name
                                        matching_node = better_child
                                        matched_name = (
                                            self.import_manager._clean_namespace_name(
                                                better_child.nodeName()
                                            )
                                        )
                                        score = 0.95
                                        better_match_found = True
                                        self.logger.notice(
                                            f"[DRY-RUN] Found better match inside container: '{original_matched_name}' -> '{matched_name}'"
                                        )

                                except Exception as search_error:
                                    self.logger.debug(
                                        f"[DRY-RUN] Could not search inside container: {search_error}"
                                    )

                            # Apply the same quality assessment
                            should_use_match = True
                            container_has_shapes = (
                                bool(matching_node.getShapes())
                                if hasattr(matching_node, "getShapes")
                                else False
                            )

                            if (
                                hasattr(matching_node, "getChildren")
                                and matching_node.getChildren()
                                and not better_match_found
                                and not container_has_shapes
                                and score < 0.9
                            ):
                                should_use_match = False
                                self.logger.debug(
                                    f"[DRY-RUN] Skipping low-quality container match: '{target_name}' -> '{matched_name}' (score: {score:.2f})"
                                )

                            if should_use_match:
                                found_objects.append(matching_node)
                                fuzzy_match_map[matching_node] = target_name
                                self.logger.notice(
                                    f"[DRY-RUN] Fuzzy match: '{target_name}' -> '{matched_name}' (score: {score:.2f})"
                                )
                                matched_successfully = True
                                break

                        if not matched_successfully:
                            self.logger.warning(
                                f"[DRY-RUN] No suitable fuzzy match found for {target_name}"
                            )

            # Report results
            if found_objects:
                self.logger.info(
                    f"[DRY-RUN] Would find {len(found_objects)} matching objects:"
                )
                for obj in found_objects:
                    # Show the actual object name that would be pulled, not the target name
                    actual_name = self.import_manager._clean_namespace_name(
                        obj.nodeName()
                    )
                    target_name = fuzzy_match_map.get(obj, actual_name)
                    self.logger.notice(
                        f"[DRY-RUN] Would pull: {actual_name} (for target: {target_name})"
                    )
            else:
                self.logger.warning("[DRY-RUN] No matching objects found")

            return len(found_objects) > 0

        except Exception as e:
            self.logger.error(f"Dry-run analysis failed: {e}")
            return False
        finally:
            # CRITICAL: Always cleanup analysis import regardless of dry_run flag
            # The analysis namespace contains real imported objects that must be removed
            if imported_transforms:
                try:
                    self.logger.debug("[DRY-RUN] Forcing cleanup of analysis namespace")

                    # Step 1: Delete all imported objects explicitly first
                    objects_to_delete = []
                    for obj in imported_transforms:
                        if pm.objExists(obj):
                            objects_to_delete.append(obj)

                    if objects_to_delete:
                        try:
                            pm.delete(objects_to_delete)
                            self.logger.debug(
                                f"[DRY-RUN] Deleted {len(objects_to_delete)} imported objects"
                            )
                        except Exception as delete_error:
                            self.logger.debug(
                                f"[DRY-RUN] Error deleting objects: {delete_error}"
                            )

                    # Step 2: Remove the namespace completely
                    namespace = self.import_manager.DRY_RUN_NAMESPACE
                    if pm.namespace(exists=namespace):
                        try:
                            # Force remove namespace with all content
                            pm.namespace(
                                removeNamespace=namespace, deleteNamespaceContent=True
                            )
                            self.logger.debug(
                                f"[DRY-RUN] Removed namespace: {namespace}"
                            )
                        except Exception as ns_error:
                            self.logger.debug(
                                f"[DRY-RUN] Error removing namespace: {ns_error}"
                            )
                            # Try alternative cleanup approach
                            try:
                                # Get all objects in the namespace and delete them manually
                                all_ns_objects = pm.ls(f"{namespace}:*")
                                if all_ns_objects:
                                    pm.delete(all_ns_objects)
                                    self.logger.debug(
                                        f"[DRY-RUN] Manually deleted {len(all_ns_objects)} namespace objects"
                                    )

                                # Now try to remove the empty namespace
                                if pm.namespace(exists=namespace):
                                    pm.namespace(removeNamespace=namespace)
                                    self.logger.debug(
                                        f"[DRY-RUN] Removed empty namespace: {namespace}"
                                    )
                            except Exception as manual_error:
                                self.logger.warning(
                                    f"[DRY-RUN] Manual cleanup failed: {manual_error}"
                                )

                    # Step 3: Verify cleanup was successful
                    remaining_objects = (
                        pm.ls(f"{namespace}:*")
                        if pm.namespace(exists=namespace)
                        else []
                    )
                    if remaining_objects:
                        self.logger.warning(
                            f"[DRY-RUN] Warning: {len(remaining_objects)} objects still remain in {namespace}"
                        )
                        # Try one more aggressive cleanup
                        try:
                            pm.delete(remaining_objects)
                            if pm.namespace(exists=namespace):
                                pm.namespace(removeNamespace=namespace)
                            self.logger.debug(
                                "[DRY-RUN] Final aggressive cleanup completed"
                            )
                        except:
                            self.logger.error(
                                f"[DRY-RUN] FAILED TO CLEAN UP: {len(remaining_objects)} objects remain in {namespace}"
                            )
                    else:
                        self.logger.debug(
                            f"[DRY-RUN] Cleanup successful - no objects remain"
                        )

                except Exception as cleanup_error:
                    self.logger.warning(f"[DRY-RUN] Cleanup failed: {cleanup_error}")
                    # As a last resort, try to list what's still there
                    try:
                        namespace = self.import_manager.DRY_RUN_NAMESPACE
                        remaining = (
                            pm.ls(f"{namespace}:*")
                            if pm.namespace(exists=namespace)
                            else []
                        )
                        if remaining:
                            self.logger.error(
                                f"[DRY-RUN] OBJECTS STILL REMAIN: {len(remaining)} objects in {namespace}"
                            )
                    except:
                        pass

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

    # Pull specific objects from another scene into current scene with comprehensive material handling
    pull_objects_from_scene(objs, path, fuzzy_matching=True, dry_run=0)


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
# - Automatic material preservation through Maya ASCII import
# - Supports all shader types and material networks
# - Intelligent object replacement with hierarchy preservation
# --------------------------------------------------------------------------------------------
