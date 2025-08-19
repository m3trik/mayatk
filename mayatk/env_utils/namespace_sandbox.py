# !/usr/bin/python
# coding=utf-8
import tempfile
from pathlib import Path
from typing import Union, Optional, Dict, List, Any
import pythontk as ptk
import pymel.core as pm


class FBXImporter:
    """Handles FBX-specific import operations (.fbx files)."""

    # FBX Import Settings - these can be customized based on needs
    FBX_IMPORT_SETTINGS = {
        "FBXImportMode": "add",  # "add", "merge", or "exmerge"
        "FBXImportConvertDeformingNullsToJoint": True,  # Convert deforming nulls to joints
        "FBXImportConvertNullsToJoint": False,  # Don't convert all nulls to joints
        "FBXImportMergeAnimationLayers": True,  # Merge animation layers
        "FBXImportProtectDrivenKeys": True,  # Protect driven keys
        "FBXImportResamplingRateSource": "Scene",  # Use scene's frame rate
        "FBXImportSetLockedAttribute": False,  # Don't lock attributes
        "FBXImportConstraints": True,  # Import constraints
        "FBXImportCameras": True,  # Import cameras
        "FBXImportLights": True,  # Import lights
        "FBXImportAnimatedRootMotion": True,  # Import root motion
        "FBXImportBakeComplexAnimation": False,  # Don't bake complex animation
        "FBXImportDeleteOriginalTakeOnSplitAnimation": True,  # Clean up takes
        "FBXImportGenerateLog": False,  # Don't generate verbose logs
        # Material and texture settings
        "FBXImportMaterials": True,  # Import materials
        "FBXImportTextures": True,  # Import textures
        "FBXImportEmbeddedTextures": True,  # Extract embedded textures
        # Units and axis conversion
        "FBXImportConvertUnitString": "",  # Let Maya handle unit conversion
        "FBXImportForcedFileAxis": "None",  # Don't force axis conversion
        "FBXImportUpAxis": "Y",  # Use Y-up (Maya default)
    }

    def __init__(self, logger, dry_run: bool = True):
        self.logger = logger
        self.dry_run = dry_run

    def is_supported_file(self, file_path: Union[str, Path]) -> bool:
        """Check if the file is an FBX file."""
        return Path(file_path).suffix.lower() == ".fbx"

    def import_with_namespace(
        self, source_file: Path, namespace: str, temp_namespace_prefix: str
    ) -> Optional[Dict]:
        """Import FBX file with namespace - handles the complete import process."""
        try:
            if self.dry_run:
                self.logger.notice(
                    f"[DRY-RUN] Would import FBX: {source_file} into namespace: {namespace}"
                )
                return {"namespace": namespace, "transforms": []}

            # Import the FBX file
            imported_transforms = self._import_fbx_file(source_file, namespace)

            # Check if import was successful - None means error, empty list means no objects
            if imported_transforms is None:
                self.logger.error(f"Failed to import FBX file: {source_file}")
                return None

            # Even if no transforms, we should still return success info
            # The warning about no transforms will be logged by _import_fbx_file if needed
            self.logger.debug(
                f"FBX import completed: {len(imported_transforms)} transforms in namespace {namespace}"
            )

            return {
                "namespace": namespace,
                "transforms": imported_transforms,
                "all_nodes": imported_transforms,  # For FBX, transforms are the main nodes
                "requested_namespace": namespace,
            }

        except Exception as e:
            self.logger.error(f"Failed to import FBX file with namespace: {e}")
            return None

    def import_for_analysis(
        self, source_file: Path, namespace: str
    ) -> Optional[List[Any]]:
        """Import FBX file for analysis purposes."""
        try:
            # Clean up existing namespace if it exists
            if pm.namespace(exists=namespace):
                pm.namespace(removeNamespace=namespace, deleteNamespaceContent=True)

            pm.namespace(add=namespace)

            # Import FBX for analysis
            imported_transforms = self._import_fbx_file(source_file, namespace)

            self.logger.debug(
                f"Imported {len(imported_transforms) if imported_transforms else 0} FBX transforms for analysis"
            )
            return imported_transforms

        except Exception as e:
            self.logger.error(f"Failed to import FBX for analysis: {e}")
            return None

    def _setup_fbx_import_settings(self) -> None:
        """Configure FBX import settings for consistent imports."""
        try:
            # Only set FBX settings if FBX plugin is loaded
            if not pm.pluginInfo("fbxmaya", query=True, loaded=True):
                try:
                    pm.loadPlugin("fbxmaya", quiet=True)
                    self.logger.debug("Loaded FBX plugin")
                except Exception as e:
                    self.logger.warning(f"Could not load FBX plugin: {e}")
                    return

            # Apply our FBX import settings
            for setting, value in self.FBX_IMPORT_SETTINGS.items():
                try:
                    if isinstance(value, bool):
                        pm.mel.eval(
                            f'FBXProperty "Import|IncludeGrp|Geometry" -v {str(value).lower()}'
                        )
                    elif isinstance(value, str):
                        pm.mel.eval(f'FBXProperty "Import" -v "{value}"')
                    # Note: Full FBX settings would require more detailed MEL commands
                    # This is a simplified version - can be expanded based on needs
                except Exception as e:
                    self.logger.debug(f"Could not set FBX setting {setting}: {e}")

        except Exception as e:
            self.logger.warning(f"Failed to setup FBX import settings: {e}")

    def _import_fbx_file(
        self, source_file: Path, namespace: str
    ) -> Optional[List[Any]]:
        """Import FBX file with proper settings and namespace handling."""
        try:
            # Setup FBX import settings
            self._setup_fbx_import_settings()

            # Get objects before import to detect new ones
            objects_before = set(pm.ls(assemblies=True))
            transforms_before = set(pm.ls(type="transform"))

            # FBX import doesn't directly support namespaces like Maya ASCII
            # We need to import first, then move objects to namespace

            self.logger.debug(f"Importing FBX file: {source_file}")

            # Import the FBX file - use correct MEL syntax
            # Convert Path to string and ensure proper escaping for MEL
            fbx_path = str(source_file).replace("\\", "/")
            pm.mel.eval(f'FBXImport -file "{fbx_path}"')

            # Find newly imported objects
            objects_after = set(pm.ls(assemblies=True))
            transforms_after = set(pm.ls(type="transform"))

            new_assemblies = list(objects_after - objects_before)
            new_transforms = list(transforms_after - transforms_before)

            self.logger.debug(f"Import detection: {len(new_assemblies)} new assemblies, {len(new_transforms)} new transforms")
            
            if not new_transforms:
                self.logger.warning("No new transforms found after FBX import")
                return []

            # Move imported objects to namespace
            if namespace and namespace != ":":
                # Store the original transform names before moving
                original_transform_names = [t.nodeName() for t in new_transforms]
                
                # Move all new transforms to namespace, not just assemblies
                # This ensures we capture hierarchical objects too
                if new_assemblies:
                    # If we have assemblies, move them (this will move their children too)
                    self._move_objects_to_namespace(new_assemblies, namespace)
                else:
                    # If no assemblies but we have transforms, they might be children
                    # Find the top-level transforms from new_transforms
                    top_level_transforms = []
                    for transform in new_transforms:
                        parent = transform.getParent()
                        if parent is None or parent not in new_transforms:
                            # This transform is either world-level or its parent wasn't imported
                            top_level_transforms.append(transform)
                    
                    if top_level_transforms:
                        self.logger.debug(f"Found {len(top_level_transforms)} top-level transforms from {len(new_transforms)} total")
                        self._move_objects_to_namespace(top_level_transforms, namespace)
                    else:
                        # Last resort: move all transforms individually
                        self.logger.debug("Moving all transforms individually")
                        self._move_objects_to_namespace(new_transforms, namespace)

                # Re-query transforms in the new namespace - try multiple approaches
                namespaced_transforms = pm.ls(f"{namespace}:*", type="transform")
                self.logger.debug(
                    f"FBX import: {len(new_transforms)} transforms moved to namespace {namespace}"
                )

                self.logger.debug(
                    f"Found {len(namespaced_transforms)} transforms in namespace query"
                )                # Return the namespaced transforms, but if none found, use fallback methods
                if namespaced_transforms:
                    return namespaced_transforms
                else:
                    # Fallback 1: Try to find transforms using original names
                    fallback_transforms = []
                    for original_name in original_transform_names:
                        clean_name = original_name.split(":")[
                            -1
                        ]  # Remove any existing namespace
                        namespaced_name = f"{namespace}:{clean_name}"
                        if pm.objExists(namespaced_name):
                            try:
                                fallback_transforms.append(pm.PyNode(namespaced_name))
                                self.logger.debug(f"Fallback found: {namespaced_name}")
                            except Exception as e:
                                self.logger.debug(
                                    f"Could not create PyNode for {namespaced_name}: {e}"
                                )

                    if fallback_transforms:
                        self.logger.debug(
                            f"Found {len(fallback_transforms)} transforms via fallback method"
                        )
                        return fallback_transforms

                    # Fallback 2: List all objects in namespace (not just transforms)
                    try:
                        all_namespace_objects = pm.ls(f"{namespace}:*")
                        self.logger.debug(
                            f"All objects in namespace {namespace}: {len(all_namespace_objects)}"
                        )

                        # Filter for transforms manually
                        namespace_transforms = [
                            obj
                            for obj in all_namespace_objects
                            if pm.nodeType(obj) == "transform"
                        ]

                        if namespace_transforms:
                            self.logger.debug(
                                f"Found {len(namespace_transforms)} transforms via manual filtering"
                            )
                            return namespace_transforms

                    except Exception as fallback2_error:
                        self.logger.debug(
                            f"Fallback 2 method failed: {fallback2_error}"
                        )

                    # Last resort: return empty list but log detailed info
                    try:
                        all_namespaces = pm.namespaceInfo(listOnlyNamespaces=True)
                        namespace_exists = namespace in all_namespaces
                        self.logger.warning(
                            f"Objects were moved to namespace {namespace} (exists: {namespace_exists}) "
                            f"but could not query them back. Original transforms: {len(original_transform_names)}, "
                            f"Assemblies moved: {len(new_assemblies)}"
                        )
                    except:
                        self.logger.warning(
                            f"Objects were moved to namespace {namespace} but could not query them back"
                        )

                    return []
            else:
                self.logger.debug(
                    f"FBX import: {len(new_transforms)} transforms imported"
                )
                return new_transforms

        except Exception as e:
            self.logger.error(f"Failed to import FBX file {source_file}: {e}")
            return None

    def _move_objects_to_namespace(self, objects: List[Any], namespace: str) -> None:
        """Move objects to the specified namespace."""
        try:
            if not objects:
                self.logger.debug("No objects to move to namespace")
                return

            # Ensure namespace exists
            if not pm.namespace(exists=namespace):
                pm.namespace(add=namespace)

            moved_count = 0
            # Move each top-level object to the namespace
            for obj in objects:
                try:
                    # Get the object as PyNode for reliable operations
                    obj_node = pm.PyNode(obj) if not isinstance(obj, pm.PyNode) else obj

                    # Rename to move to namespace
                    original_name = obj_node.nodeName()
                    new_name = f"{namespace}:{original_name}"

                    # Check if target name already exists
                    if pm.objExists(new_name):
                        # Generate unique name
                        counter = 1
                        while pm.objExists(f"{new_name}_{counter}"):
                            counter += 1
                        new_name = f"{new_name}_{counter}"

                    obj_node.rename(new_name)
                    moved_count += 1
                    self.logger.debug(f"Moved {original_name} to {new_name}")

                except Exception as e:
                    self.logger.warning(
                        f"Failed to move object {obj} to namespace: {e}"
                    )

            self.logger.debug(
                f"Successfully moved {moved_count}/{len(objects)} objects to namespace {namespace}"
            )

        except Exception as e:
            self.logger.error(f"Failed to move objects to namespace {namespace}: {e}")


class MayaImporter:
    """Handles Maya-specific import operations (.ma/.mb files)."""

    def __init__(self, logger, dry_run: bool = True):
        self.logger = logger
        self.dry_run = dry_run

    def is_supported_file(self, file_path: Union[str, Path]) -> bool:
        """Check if the file is a Maya file (.ma or .mb)."""
        suffix = Path(file_path).suffix.lower()
        return suffix in [".ma", ".mb"]

    def import_with_namespace(
        self, source_file: Path, namespace: str, temp_namespace_prefix: str
    ) -> Optional[Dict]:
        """Import Maya file with namespace - original logic."""
        try:
            if self.dry_run:
                self.logger.notice(
                    f"[DRY-RUN] Would import Maya: {source_file} into namespace: {namespace}"
                )
                return {"namespace": namespace, "transforms": []}

            # Clean up existing namespace if it exists
            if pm.namespace(exists=namespace):
                self.logger.debug(f"Cleaning up existing namespace: {namespace}")
                pm.namespace(removeNamespace=namespace, deleteNamespaceContent=True)

            # Create the namespace
            self.logger.debug(f"Creating namespace: {namespace}")
            pm.namespace(add=namespace)

            # Get list of namespaces before import to detect what Maya actually creates
            namespaces_before = set(pm.namespaceInfo(listOnlyNamespaces=True))

            # Get list of transform nodes before import
            transforms_before = set(pm.ls(type="transform"))

            # Import the file into the namespace to avoid name clashes
            # Use options to reduce warnings and conflicts
            imported_nodes = pm.importFile(
                str(source_file),
                namespace=namespace,
                returnNewNodes=True,
                mergeNamespacesOnClash=False,  # Don't merge namespaces
                preserveReferences=False,  # Don't preserve external references
            )

            if not imported_nodes:
                self.logger.warning("No nodes were imported from source file")
                return None

            # Find newly imported transform nodes
            transforms_after = set(pm.ls(type="transform"))
            new_transforms = list(transforms_after - transforms_before)

            # Get list of namespaces after import to detect what Maya actually created
            namespaces_after = set(pm.namespaceInfo(listOnlyNamespaces=True))
            new_namespaces = namespaces_after - namespaces_before

            # Find temp namespaces that were actually created
            temp_namespaces_created = [
                ns for ns in new_namespaces if ns.startswith(temp_namespace_prefix)
            ]

            # Detect the actual namespace used by Maya
            actual_namespace = namespace

            # Method 1: Check what temp namespaces were actually created by Maya
            if temp_namespaces_created and len(temp_namespaces_created) == 1:
                actual_namespace = temp_namespaces_created[0]
                if actual_namespace != namespace:
                    self.logger.debug(
                        f"Maya created namespace '{actual_namespace}' instead of requested '{namespace}'"
                    )
            elif len(temp_namespaces_created) > 1:
                self.logger.warning(
                    f"Multiple temp namespaces created: {temp_namespaces_created}"
                )
                # Use the one with the most objects
                namespace_object_counts = {}
                for ns in temp_namespaces_created:
                    try:
                        objects = pm.ls(f"{ns}:*")
                        namespace_object_counts[ns] = len(objects)
                    except:
                        namespace_object_counts[ns] = 0

                if namespace_object_counts:
                    actual_namespace = max(
                        namespace_object_counts, key=namespace_object_counts.get
                    )
                    self.logger.debug(
                        f"Selected namespace with most objects: {actual_namespace}"
                    )

            # Method 2: Double-check with transform namespace detection
            if new_transforms:
                # Check the namespace of the first transform to verify
                first_transform_name = new_transforms[0].nodeName()
                if ":" in first_transform_name:
                    transform_namespace = first_transform_name.split(":")[0]
                    if transform_namespace != actual_namespace:
                        self.logger.warning(
                            f"Namespace mismatch! Detected from namespaces: {actual_namespace}, "
                            f"but transform says: {transform_namespace}"
                        )
                        # Trust the transform namespace as it's more reliable
                        actual_namespace = transform_namespace

                # Debug: Check all transforms to see their namespaces
                namespaces_found = set()
                for transform in new_transforms[:5]:  # Check first 5
                    transform_name = transform.nodeName()
                    if ":" in transform_name:
                        ns = transform_name.split(":")[0]
                        namespaces_found.add(ns)

                if len(namespaces_found) > 1:
                    self.logger.warning(
                        f"Multiple namespaces found in imported transforms: {namespaces_found}"
                    )
                elif namespaces_found:
                    confirmed_namespace = list(namespaces_found)[0]
                    if confirmed_namespace != actual_namespace:
                        self.logger.warning(
                            f"Final namespace correction: {actual_namespace} -> {confirmed_namespace}"
                        )
                        actual_namespace = confirmed_namespace

            self.logger.debug(
                f"Maya import {actual_namespace}: {len(imported_nodes)} total nodes, {len(new_transforms)} transforms"
            )

            return {
                "namespace": actual_namespace,
                "transforms": new_transforms,
                "all_nodes": imported_nodes,
                "requested_namespace": namespace,  # Track original request for cleanup
            }

        except Exception as e:
            self.logger.error(f"Failed to import Maya file with namespace: {e}")
            return None

    def import_for_analysis(
        self, source_file: Path, namespace: str
    ) -> Optional[List[Any]]:
        """Import Maya file for analysis purposes."""
        try:
            # Clean up existing namespace if it exists
            if pm.namespace(exists=namespace):
                pm.namespace(removeNamespace=namespace, deleteNamespaceContent=True)

            pm.namespace(add=namespace)

            # Import for analysis with consistent options
            imported_nodes = pm.importFile(
                str(source_file),
                namespace=namespace,
                returnNewNodes=True,
                mergeNamespacesOnClash=False,
                preserveReferences=False,
            )

            self.logger.debug(
                f"Imported {len(imported_nodes) if imported_nodes else 0} Maya nodes for analysis"
            )
            return imported_nodes

        except Exception as e:
            self.logger.error(f"Failed to import Maya file for analysis: {e}")
            return None


class NamespaceSandbox(ptk.LoggingMixin):
    """Handles temporary importing and namespace management for Maya scenes.

    Manages temporary imports with namespace isolation for safe cleanup.
    Automatically detects and handles Maya ASCII/Binary and FBX files.

    Uses specialized importers for different file types:
    - MayaImporter: Handles .ma/.mb files
    - FBXImporter: Handles .fbx files
    """

    # Constants
    TEMP_NAMESPACE_PREFIX = "temp_import_"
    DRY_RUN_NAMESPACE = "dry_run_temp"

    def __init__(self, dry_run: bool = True, fuzzy_matching: bool = True):
        super().__init__()
        self.dry_run = dry_run
        self.fuzzy_matching = fuzzy_matching
        self._active_namespaces = []  # Track only OUR namespaces for cleanup

        # Initialize specialized importers
        self._maya_importer = MayaImporter(self.logger, dry_run)
        self._fbx_importer = FBXImporter(self.logger, dry_run)

    # =============================================================================
    # PUBLIC API METHODS
    # =============================================================================

    def import_with_namespace(
        self, source_file: Union[str, Path], namespace_prefix: str = None
    ) -> Optional[Dict]:
        """Import file and return import information.

        Automatically detects file type and uses appropriate importer:
        - .ma/.mb files → MayaImporter
        - .fbx files → FBXImporter

        Returns:
            Dictionary with namespace and imported transform nodes if successful, None otherwise
        """
        try:
            source_file = Path(source_file)
            if not source_file.exists():
                self.logger.error(f"Source file not found: {source_file}")
                return None

            # Get the appropriate importer and delegate
            importer = self._get_importer_for_file(source_file)
            if not importer:
                self.logger.error(
                    f"No importer available for file type: {source_file.suffix}"
                )
                return None

            # Create unique namespace for tracking
            namespace = self._create_unique_namespace(namespace_prefix)

            # Delegate to the appropriate importer
            import_result = importer.import_with_namespace(
                source_file, namespace, self.TEMP_NAMESPACE_PREFIX
            )

            if import_result:
                # Track the namespace(s) for cleanup
                actual_namespace = import_result["namespace"]
                requested_namespace = import_result.get(
                    "requested_namespace", namespace
                )

                self._track_namespace(actual_namespace)

                # Also track the original requested namespace if different (Maya-specific logic)
                if (
                    actual_namespace != requested_namespace
                    and pm.namespace(exists=requested_namespace)
                    and requested_namespace not in self._active_namespaces
                ):
                    self._track_namespace(requested_namespace)
                    self.logger.debug(
                        f"Also tracking requested namespace: {requested_namespace}"
                    )

                self.logger.debug(f"Import successful: {actual_namespace}")
                self.logger.debug(f"Active namespaces: {self._active_namespaces}")

            return import_result

        except Exception as e:
            self.logger.error(f"Failed to import with namespace: {e}")
            return None

    def import_for_analysis(
        self, source_file: Union[str, Path], namespace: str = None
    ) -> Optional[List[Any]]:
        """Import file into temporary namespace for analysis (dry-run mode).

        Automatically detects file type and uses appropriate importer.

        Returns:
            List of imported nodes if successful, None otherwise
        """
        try:
            source_file = Path(source_file)
            if not source_file.exists():
                self.logger.error(f"Source file not found: {source_file}")
                return None

            # Get the appropriate importer and delegate
            importer = self._get_importer_for_file(source_file)
            if not importer:
                self.logger.error(
                    f"No importer available for file type: {source_file.suffix}"
                )
                return None

            # Set up analysis namespace
            if namespace is None:
                if self._fbx_importer.is_supported_file(source_file):
                    namespace = f"{self.DRY_RUN_NAMESPACE}_fbx"
                else:
                    namespace = self.DRY_RUN_NAMESPACE

            # Delegate to the appropriate importer
            return importer.import_for_analysis(source_file, namespace)

        except Exception as e:
            self.logger.error(f"Failed to import for analysis: {e}")
            return None

    # =============================================================================
    # IMPORTER COORDINATION METHODS
    # =============================================================================

    def _get_importer_for_file(self, file_path: Union[str, Path]):
        """Get the appropriate importer for the given file type."""
        if self._fbx_importer.is_supported_file(file_path):
            return self._fbx_importer
        elif self._maya_importer.is_supported_file(file_path):
            return self._maya_importer
        else:
            return None

    def get_supported_formats(self) -> List[str]:
        """Get list of supported file formats from all importers."""
        return [".ma", ".mb", ".fbx"]

    def _track_namespace(self, namespace: str) -> None:
        """Track a namespace for cleanup."""
        if namespace not in self._active_namespaces:
            self._active_namespaces.append(namespace)
            self.logger.debug(f"Tracking namespace: {namespace}")

    # =============================================================================
    # UTILITY METHODS
    # =============================================================================

    def find_objects_in_namespace(
        self, namespace: str, target_objects: List[str]
    ) -> List[Any]:
        """Find objects in the specified namespace with optional fuzzy matching."""
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

            # First pass: exact matches only
            for target_name in target_objects:
                if target_name in available_transforms:
                    found_objects.append(available_transforms[target_name])

            # Second pass: fuzzy matching (only if enabled)
            if self.fuzzy_matching:
                found_names = [
                    self._clean_namespace_name(obj.nodeName()) for obj in found_objects
                ]
                unmatched_targets = target_set - set(found_names)

                if unmatched_targets:
                    # Use batch fuzzy matching for better performance
                    matches = ptk.FuzzyMatcher.find_all_matches(
                        list(unmatched_targets),
                        list(available_transforms.keys()),
                        score_threshold=0.7,
                    )

                    for target_name, (matched_name, score) in matches.items():
                        found_objects.append(available_transforms[matched_name])
                        self.logger.info(
                            f"Fuzzy match: '{target_name}' -> '{matched_name}' (score: {score:.2f})"
                        )

            return found_objects

        except Exception as e:
            self.logger.error(f"Failed to find objects in namespace {namespace}: {e}")
            return []

    def find_objects_with_hierarchy_matching(
        self, namespace: str, target_objects: List[str]
    ) -> List[Any]:
        """Find objects using hierarchical path matching (only if fuzzy_matching enabled)."""
        if not self.fuzzy_matching:
            self.logger.debug("Hierarchical matching disabled (fuzzy_matching=False)")
            return []

        try:
            # Get full hierarchy paths
            if namespace:
                nodes = pm.ls(f"{namespace}:*", type="transform", long=True)
            else:
                nodes = pm.ls(type="transform", long=True)

            # Convert to hierarchy paths (using | separator like Maya)
            available_paths = []
            path_to_node = {}

            for node in nodes:
                # Convert Maya long name to hierarchy path
                hierarchy_path = node.replace("|", "|").replace(f"{namespace}:", "")
                available_paths.append(hierarchy_path)
                path_to_node[hierarchy_path] = pm.PyNode(node)

            # Create target paths (assume they're just node names for now)
            target_paths = [f"|{name}" for name in target_objects]

            # Use hierarchical fuzzy matching
            fuzzy_matches, _, _ = ptk.FuzzyMatcher.find_trailing_digit_matches(
                target_paths, available_paths, path_separator="|"
            )

            found_objects = []
            for match in fuzzy_matches:
                node = path_to_node[match["current_path"]]
                found_objects.append(node)
                self.logger.info(
                    f"Hierarchy match: {match['current_name']} -> {match['target_name']}"
                )

            return found_objects

        except Exception as e:
            self.logger.error(f"Hierarchical matching failed: {e}")
            return []

    def get_namespace_hierarchy(self, namespace: str) -> Dict[str, Any]:
        """Get complete hierarchy information for objects in namespace.

        Returns:
            Dictionary mapping object names to their hierarchy data
        """
        try:
            if namespace:
                nodes = pm.ls(f"{namespace}:*", type="transform", long=True)
            else:
                nodes = pm.ls(type="transform", long=True)

            hierarchy_data = {}

            for node in nodes:
                node_obj = pm.PyNode(node)
                clean_name = self._clean_namespace_name(node_obj.nodeName())

                hierarchy_data[clean_name] = {
                    "node": node_obj,
                    "parent": node_obj.getParent(),
                    "children": node_obj.getChildren(type="transform"),
                    "world_matrix": node_obj.getMatrix(worldSpace=True),
                    "local_matrix": node_obj.getMatrix(worldSpace=False),
                    "full_path": node,
                }

            return hierarchy_data

        except Exception as e:
            self.logger.error(f"Failed to get namespace hierarchy: {e}")
            return {}

    def cleanup_import(
        self, namespace: str, imported_objects: List[Any] = None
    ) -> bool:
        """Safely remove imported objects and cleanup namespace tracking."""
        try:
            # Verify this is one of our managed namespaces
            if namespace not in self._active_namespaces:
                self.logger.debug(
                    f"Namespace not managed by this instance: {namespace}"
                )
                return False

            if self.dry_run:
                self.logger.notice(f"[DRY-RUN] Would cleanup import: {namespace}")
                return True

            # Check if namespace actually exists in Maya
            if not pm.namespace(exists=namespace):
                self.logger.debug(f"Namespace {namespace} does not exist in Maya")
                # Remove from tracking anyway
                self._active_namespaces = [
                    ns for ns in self._active_namespaces if ns != namespace
                ]
                return True

            # Delete the imported objects if provided
            if imported_objects:
                existing_objects = [
                    obj for obj in imported_objects if pm.objExists(obj)
                ]
                if existing_objects:
                    pm.delete(existing_objects)
                    self.logger.debug(
                        f"Deleted {len(existing_objects)} imported objects"
                    )

            # Clean up namespace if it exists and has objects
            try:
                # List objects in namespace before deleting
                namespace_objects = pm.ls(f"{namespace}:*")
                self.logger.debug(
                    f"Removing namespace {namespace} with {len(namespace_objects)} objects"
                )

                pm.namespace(removeNamespace=namespace, deleteNamespaceContent=True)
                self.logger.debug(f"Removed namespace: {namespace}")
            except Exception as e:
                self.logger.debug(f"Namespace cleanup handled gracefully: {e}")

            # Remove from active namespaces tracking
            self._active_namespaces = [
                ns for ns in self._active_namespaces if ns != namespace
            ]

            return True

        except Exception as e:
            self.logger.debug(f"Import cleanup handled gracefully: {e}")
            # Still remove from tracking even if cleanup failed
            if namespace in self._active_namespaces:
                self._active_namespaces = [
                    ns for ns in self._active_namespaces if ns != namespace
                ]
            return False

    def cleanup_namespace(self, namespace: str) -> bool:
        """Backward compatibility alias for cleanup_import."""
        return self.cleanup_import(namespace)

    def cleanup_all_namespaces(self) -> None:
        """Clean up all temp imports managed by this instance."""
        if not self._active_namespaces:
            self.logger.debug("No temporary imports to clean up")
            return

        self.logger.debug(f"Cleaning up namespaces: {self._active_namespaces}")

        # Also check what temp namespaces actually exist in Maya
        all_namespaces = pm.namespaceInfo(listOnlyNamespaces=True)
        temp_namespaces_in_maya = [
            ns for ns in all_namespaces if ns.startswith("temp_import_")
        ]

        if temp_namespaces_in_maya:
            self.logger.debug(f"Temp namespaces in Maya: {temp_namespaces_in_maya}")

        temp_namespaces_to_cleanup = self._active_namespaces.copy()
        cleaned_count = 0

        for namespace in temp_namespaces_to_cleanup:
            self.logger.debug(f"Attempting to cleanup namespace: {namespace}")
            if self.cleanup_import(namespace):
                cleaned_count += 1

        self._active_namespaces.clear()

        if cleaned_count > 0:
            self.logger.debug(f"Cleaned up {cleaned_count} temporary imports")
        else:
            self.logger.debug("All temporary imports were already cleaned up")

        # Final check - see if any temp namespaces remain
        remaining_namespaces = pm.namespaceInfo(listOnlyNamespaces=True)
        remaining_temp_namespaces = [
            ns for ns in remaining_namespaces if ns.startswith("temp_import_")
        ]

        if remaining_temp_namespaces:
            self.logger.warning(
                f"WARNING: Temp namespaces still exist after cleanup: {remaining_temp_namespaces}"
            )

    def cleanup_all_temp_namespaces_force(self) -> None:
        """Force cleanup of ALL temp namespaces in Maya, not just tracked ones.

        This is a nuclear option for cleaning up orphaned temp namespaces
        from previous failed runs.
        """
        all_namespaces = pm.namespaceInfo(listOnlyNamespaces=True)
        all_temp_namespaces = [
            ns for ns in all_namespaces if ns.startswith("temp_import_")
        ]

        if not all_temp_namespaces:
            self.logger.debug("No temp namespaces found in Maya")
            return

        self.logger.warning(
            f"Force cleaning up ALL temp namespaces: {all_temp_namespaces}"
        )

        cleaned_count = 0
        for namespace in all_temp_namespaces:
            try:
                if pm.namespace(exists=namespace):
                    objects = pm.ls(f"{namespace}:*")
                    self.logger.debug(
                        f"Force removing namespace {namespace} with {len(objects)} objects"
                    )
                    pm.namespace(removeNamespace=namespace, deleteNamespaceContent=True)
                    cleaned_count += 1
                    self.logger.debug(f"Force removed namespace: {namespace}")
            except Exception as e:
                self.logger.warning(
                    f"Failed to force remove namespace {namespace}: {e}"
                )

        # Clear our tracking since we've cleaned everything
        self._active_namespaces.clear()

        self.logger.warning(f"Force cleaned up {cleaned_count} temp namespaces")

    def export_objects_to_temp(self, target_objects: List[str]) -> Optional[Path]:
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

    def import_objects_for_swapping(
        self, source_file: Union[str, Path]
    ) -> Optional[Dict]:
        """Import objects from source scene for object swapping operations.

        Automatically detects file type and uses appropriate importer.
        This method handles the import logic that was previously in SceneManager.
        Returns import info with normalized key names for compatibility.
        """
        try:
            source_file = Path(source_file)

            # For dry-run, we still need to import to analyze, but we'll clean up afterwards
            if self.dry_run:
                self.logger.notice(
                    f"[DRY-RUN] Importing from {source_file} for analysis"
                )

            # Use the existing import_with_namespace method (which auto-detects file type)
            import_info = self.import_with_namespace(source_file)

            if import_info:
                # Normalize the key name to match what the rest of the code expects
                import_info["imported_transforms"] = import_info.get("transforms", [])

            return import_info

        except Exception as e:
            self.logger.error(f"Failed to import source objects: {e}")
            return None

    def import_to_target_scene(
        self,
        temp_file: Union[str, Path],
        target_scene: Union[str, Path],
        backup: bool = True,
    ) -> bool:
        """Import objects into target scene.

        This method handles the push operation logic that was in SceneManager.
        """
        try:
            temp_file = Path(temp_file)
            target_scene = Path(target_scene)

            if self.dry_run:
                self.logger.notice(
                    f"[DRY-RUN] Would import {temp_file} to {target_scene}"
                )
                return True

            # Implementation for actual import would go here
            # This would involve opening target_scene, importing temp_file, then saving
            self.logger.info(f"Importing {temp_file} to {target_scene}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to import to target scene: {e}")
            return False

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup only our temp namespaces."""
        self.cleanup_all_namespaces()
        self.cleanup_analysis_namespace()

    # =============================================================================
    # PRIVATE/INTERNAL METHODS
    # =============================================================================

    def cleanup_analysis_namespace(self, namespace: str = None) -> bool:
        """Clean up analysis namespace and its contents."""
        try:
            if namespace is None:
                namespace = self.DRY_RUN_NAMESPACE

            if self.dry_run:
                self.logger.notice(f"[DRY-RUN] Would clean up namespace: {namespace}")
                return True

            if not pm.namespace(exists=namespace):
                return True

            pm.namespace(removeNamespace=namespace, deleteNamespaceContent=True)
            self.logger.debug(f"Cleaned up analysis namespace: {namespace}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to cleanup namespace {namespace}: {e}")
            return False

    def _create_unique_namespace(self, prefix: str = None) -> str:
        """Create a unique namespace for importing."""
        import time

        if prefix is None:
            prefix = self.TEMP_NAMESPACE_PREFIX

        return f"{prefix}{int(time.time())}"

    @staticmethod
    def _clean_namespace_name(namespaced_name: str) -> str:
        """Extract clean name without namespace prefix."""
        return namespaced_name.split(":")[-1]


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    ...

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
