# !/usr/bin/python
# coding=utf-8
import tempfile
from pathlib import Path
from typing import Union, Optional, Dict, List, Any
import pythontk as ptk
import maya.cmds as cmds

from mayatk.env_utils.fbx_utils import FbxUtils


def _node_name(node) -> str:
    """Leaf name (with namespace prefix preserved) for node or string."""
    if node is None:
        return ""
    if hasattr(node, "nodeName"):
        try:
            return node.nodeName()
        except Exception:
            pass
    return str(node).split("|")[-1]


def _get_parent(node):
    """Single immediate parent — works for node or string."""
    if hasattr(node, "getParent"):
        try:
            return node.getParent()
        except Exception:
            pass
    parents = cmds.listRelatives(str(node), parent=True, fullPath=True) or []
    return parents[0] if parents else None


def _get_children(node, **kwargs):
    """Children — works for node or string."""
    if hasattr(node, "getChildren"):
        try:
            return node.getChildren(**kwargs) or []
        except Exception:
            pass
    cmds_kwargs = {"children": True, "fullPath": True}
    if "type" in kwargs:
        cmds_kwargs["type"] = kwargs["type"]
    if kwargs.get("allDescendents"):
        cmds_kwargs.pop("children", None)
        cmds_kwargs["allDescendents"] = True
    return cmds.listRelatives(str(node), **cmds_kwargs) or []


class FBXImporter:
    """Handles FBX-specific import operations (.fbx files).

    The actual import is delegated to :meth:`FbxUtils.import_scene`, which
    isolates the whole import under the target namespace via Maya's
    *active-namespace* mechanism (the ``file(namespace=...)`` flag is ignored
    by the FBX translator, but setting the active namespace works). This gives
    the same clean namespace isolation the :class:`MayaImporter` gets natively
    — no manual per-node namespace moves, and no live-scene object shelving to
    dodge name clashes (isolation makes clashes impossible).
    """

    def __init__(self, logger, dry_run: bool = True):
        self.logger = logger
        self.dry_run = dry_run

    def is_supported_file(self, file_path: Union[str, Path]) -> bool:
        """Check if the file is an FBX file."""
        return Path(file_path).suffix.lower() == ".fbx"

    def import_with_namespace(
        self,
        source_file: Path,
        namespace: str,
        temp_namespace_prefix: str,
        force_complete_import: bool = False,
    ) -> Optional[Dict]:
        """Import an FBX file isolated into *namespace*.

        ``force_complete_import`` is accepted for interface parity with
        :class:`MayaImporter` but is a no-op here: native namespace isolation
        prevents the root-name clashes it used to work around.
        """
        try:
            if self.dry_run:
                self.logger.notice(
                    f"[DRY-RUN] Would import FBX: {source_file} into namespace: {namespace}"
                )
                return {"namespace": namespace, "transforms": []}

            new_transforms, new_nodes = self._import_into_clean_namespace(
                source_file, namespace
            )
            if not new_transforms:
                self.logger.warning(
                    f"No transforms found after FBX import into namespace {namespace}"
                )

            self.logger.debug(
                f"FBX import completed: {len(new_transforms)} transforms in namespace {namespace}"
            )
            return {
                "namespace": namespace,
                "transforms": new_transforms,
                "all_nodes": new_nodes or new_transforms,
                "requested_namespace": namespace,
            }

        except Exception as e:
            self.logger.error(f"Failed to import FBX file with namespace: {e}")
            return None

    def import_for_analysis(
        self, source_file: Path, namespace: str
    ) -> Optional[List[Any]]:
        """Import FBX file into a fresh namespace for analysis."""
        try:
            transforms, _ = self._import_into_clean_namespace(source_file, namespace)
            self.logger.debug(f"Imported {len(transforms)} FBX transforms for analysis")
            return transforms

        except Exception as e:
            self.logger.error(f"Failed to import FBX for analysis: {e}")
            return None

    @staticmethod
    def _import_into_clean_namespace(source_file, namespace):
        """Import *source_file* into a freshly-cleared *namespace*.

        Clears any pre-existing namespace first (as MayaImporter does) so a
        reused name — unique-namespace timestamps collide within a second —
        can't let a stale import inflate the re-queried transform list. Native
        active-namespace isolation puts every imported node under *namespace*,
        so the transform list is a direct namespace re-query.

        Returns ``(transforms, all_new_nodes)``.
        """
        if cmds.namespace(exists=namespace):
            cmds.namespace(removeNamespace=namespace, deleteNamespaceContent=True)
        new_nodes = FbxUtils.import_scene(source_file, namespace=namespace)
        return cmds.ls(f"{namespace}:*", type="transform") or [], new_nodes


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
        self,
        source_file: Path,
        namespace: str,
        temp_namespace_prefix: str,
        force_complete_import: bool = False,
    ) -> Optional[Dict]:
        """Import Maya file with namespace - original logic."""
        try:
            if self.dry_run:
                self.logger.notice(
                    f"[DRY-RUN] Would import Maya: {source_file} into namespace: {namespace}"
                )
                return {"namespace": namespace, "transforms": []}

            # Clean up existing namespace if it exists
            if cmds.namespace(exists=namespace):
                self.logger.debug(f"Cleaning up existing namespace: {namespace}")
                cmds.namespace(removeNamespace=namespace, deleteNamespaceContent=True)

            # Create the namespace
            self.logger.debug(f"Creating namespace: {namespace}")
            cmds.namespace(add=namespace)

            # Get list of namespaces before import to detect what Maya actually creates
            namespaces_before = set(cmds.namespaceInfo(listOnlyNamespaces=True))

            # Get list of transform nodes before import
            transforms_before = set(cmds.ls(type="transform"))

            # Import the file into the namespace to avoid name clashes
            # Use options to reduce warnings and conflicts
            imported_nodes = cmds.file(
                str(source_file),
                i=True,  # import — pm.importFile implied this; cmds.file needs it
                namespace=namespace,
                returnNewNodes=True,
                mergeNamespacesOnClash=False,  # Don't merge namespaces
                preserveReferences=False,  # Don't preserve external references
            )

            if not imported_nodes:
                self.logger.warning("No nodes were imported from source file")
                return None

            # Find newly imported transform nodes
            transforms_after = set(cmds.ls(type="transform"))
            new_transforms = list(transforms_after - transforms_before)

            # Get list of namespaces after import to detect what Maya actually created
            namespaces_after = set(cmds.namespaceInfo(listOnlyNamespaces=True))
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
                        objects = cmds.ls(f"{ns}:*")
                        namespace_object_counts[ns] = len(objects)
                    except Exception:
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
                first_transform_name = _node_name(new_transforms[0])
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
                    transform_name = _node_name(transform)
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
            if cmds.namespace(exists=namespace):
                cmds.namespace(removeNamespace=namespace, deleteNamespaceContent=True)

            cmds.namespace(add=namespace)

            # Import for analysis with consistent options
            imported_nodes = cmds.file(
                str(source_file),
                i=True,  # import — pm.importFile implied this; cmds.file needs it
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


class CameraTracker(ptk.LoggingMixin):
    """Tracks cameras before and after import operations for proper cleanup."""

    def __init__(self, logger=None, log_level="WARNING"):
        if logger:
            self.logger = logger
        else:
            super().__init__()
        self.set_log_level(log_level)
        self.pre_import_cameras = set()
        self.post_import_cameras = set()
        self.new_cameras = set()

    def capture_pre_import_state(self):
        """Capture camera state before import."""
        self.logger.debug("Capturing pre-import camera state...")
        self.pre_import_cameras = self._get_scene_cameras()
        self.logger.debug(f"Pre-import cameras: {sorted(self.pre_import_cameras)}")
        return self.pre_import_cameras

    def capture_post_import_state(self):
        """Capture camera state after import."""
        self.logger.debug("Capturing post-import camera state...")
        self.post_import_cameras = self._get_scene_cameras()
        self.new_cameras = self.post_import_cameras - self.pre_import_cameras
        self.logger.debug(f"Post-import cameras: {sorted(self.post_import_cameras)}")
        self.logger.debug(f"New cameras detected: {sorted(self.new_cameras)}")
        return self.new_cameras

    def get_imported_cameras(self, namespace_filter=None):
        """Get cameras that were imported (optionally filtered by namespace)."""
        imported = self.new_cameras
        if namespace_filter:
            imported = {cam for cam in imported if namespace_filter in cam}
        self.logger.debug(
            f"Imported cameras (filtered={namespace_filter}): {sorted(imported)}"
        )
        return imported

    def cleanup_imported_cameras(
        self, namespace_filter=None, preserve_user_cameras=True
    ):
        """Clean up imported cameras with optional preservation of user cameras."""
        self.logger.debug("Cleaning up imported cameras...")

        cameras_to_delete = self.get_imported_cameras(namespace_filter)

        if preserve_user_cameras:
            # Define Maya default cameras that should be deleted (exact matches only)
            MAYA_DEFAULT_CAMERAS = {"persp", "top", "front", "side"}

            # Filter to only delete exact default cameras
            filtered_cameras = set()
            for camera in cameras_to_delete:
                base_name = camera.split(":")[-1] if ":" in camera else camera
                if base_name in MAYA_DEFAULT_CAMERAS:  # Exact match only
                    filtered_cameras.add(camera)

            cameras_to_delete = filtered_cameras

        if cameras_to_delete:
            self.logger.info(
                f"Deleting {len(cameras_to_delete)} imported cameras: {sorted(cameras_to_delete)}"
            )
            try:
                # Get camera transforms and delete them
                camera_transforms = []
                for camera_name in cameras_to_delete:
                    try:
                        # camera_name is already the transform name
                        camera_transform = camera_name
                        camera_transforms.append(camera_transform)
                    except Exception as e:
                        self.logger.warning(
                            f"Could not access camera transform {camera_name}: {e}"
                        )

                if camera_transforms:
                    cmds.delete(camera_transforms)
                    self.logger.debug(
                        f"Successfully deleted {len(camera_transforms)} camera transforms"
                    )

                return list(cameras_to_delete)
            except Exception as e:
                self.logger.error(f"Failed to delete imported cameras: {e}")
                return []
        else:
            self.logger.debug("No imported cameras to clean up")
            return []

    def _get_scene_cameras(self):
        """Get all camera transform names in the scene."""
        try:
            cameras = cmds.ls(type="camera")
            # Get the transform nodes (parents) of the camera shapes
            camera_transforms = []
            for cam in cameras:
                try:
                    transform = _get_parent(cam)
                    if transform:
                        camera_transforms.append(_node_name(transform))
                except Exception:
                    # If we can't get the parent, use the camera name directly
                    camera_transforms.append(_node_name(cam))
            return set(camera_transforms)
        except Exception as e:
            self.logger.error(f"Failed to get scene cameras: {e}")
            return set()

    def reset(self):
        """Reset tracking state."""
        self.pre_import_cameras = set()
        self.post_import_cameras = set()
        self.new_cameras = set()


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

    def __init__(
        self, dry_run: bool = True, fuzzy_matching: bool = True, log_level="WARNING"
    ):
        super().__init__()
        self.set_log_level(log_level)
        self.dry_run = dry_run
        self.fuzzy_matching = fuzzy_matching
        self._active_namespaces = []  # Track only OUR namespaces for cleanup

        # Initialize camera tracking
        self.camera_tracker = CameraTracker(self.logger)

        # Initialize specialized importers
        self._maya_importer = MayaImporter(self.logger, dry_run)
        self._fbx_importer = FBXImporter(self.logger, dry_run)

    # =============================================================================
    # PUBLIC API METHODS
    # =============================================================================

    def import_with_namespace(
        self,
        source_file: Union[str, Path],
        namespace_prefix: str = None,
        force_complete_import: bool = False,
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

            # Track cameras before import
            self.camera_tracker.capture_pre_import_state()

            # Delegate to the appropriate importer
            import_result = importer.import_with_namespace(
                source_file,
                namespace,
                self.TEMP_NAMESPACE_PREFIX,
                force_complete_import=force_complete_import,
            )

            if import_result:
                # Track cameras after import
                self.camera_tracker.capture_post_import_state()

                # Track the namespace(s) for cleanup
                actual_namespace = import_result["namespace"]
                requested_namespace = import_result.get(
                    "requested_namespace", namespace
                )

                self._track_namespace(actual_namespace)

                # Also track the original requested namespace if different (Maya-specific logic)
                if (
                    actual_namespace != requested_namespace
                    and cmds.namespace(exists=requested_namespace)
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
                nodes = cmds.ls(f"{namespace}:*", type="transform")
            else:
                nodes = cmds.ls(type="transform")

            available_transforms = {}
            for node in nodes:
                base_name = self._clean_namespace_name(_node_name(node))
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
                    self._clean_namespace_name(_node_name(obj)) for obj in found_objects
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
                nodes = cmds.ls(f"{namespace}:*", type="transform", long=True)
            else:
                nodes = cmds.ls(type="transform", long=True)

            # Convert to hierarchy paths (using | separator like Maya)
            available_paths = []
            path_to_node = {}

            for node in nodes:
                # Convert Maya long name to hierarchy path
                hierarchy_path = node.replace("|", "|").replace(f"{namespace}:", "")
                available_paths.append(hierarchy_path)
                path_to_node[hierarchy_path] = node

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
                nodes = cmds.ls(f"{namespace}:*", type="transform", long=True)
            else:
                nodes = cmds.ls(type="transform", long=True)

            hierarchy_data = {}

            for node in nodes:
                node_obj = node
                clean_name = self._clean_namespace_name(_node_name(node_obj))

                hierarchy_data[clean_name] = {
                    "node": node_obj,
                    "parent": _get_parent(node_obj),
                    "children": _get_children(node_obj, type="transform"),
                    "world_matrix": cmds.xform(
                        node_obj, query=True, matrix=True, worldSpace=True
                    ),
                    "local_matrix": cmds.xform(
                        node_obj, query=True, matrix=True, worldSpace=False
                    ),
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
            if not cmds.namespace(exists=namespace):
                self.logger.debug(f"Namespace {namespace} does not exist in Maya")
                # Remove from tracking anyway
                self._active_namespaces = [
                    ns for ns in self._active_namespaces if ns != namespace
                ]
                return True

            # Delete the imported objects if provided
            if imported_objects:
                existing_objects = [
                    obj for obj in imported_objects if cmds.objExists(obj)
                ]
                if existing_objects:
                    cmds.delete(existing_objects)
                    self.logger.debug(
                        f"Deleted {len(existing_objects)} imported objects"
                    )

            # Clean up namespace if it exists and has objects
            try:
                # List objects in namespace before deleting
                namespace_objects = cmds.ls(f"{namespace}:*")
                self.logger.debug(
                    f"Removing namespace {namespace} with {len(namespace_objects)} objects"
                )

                cmds.namespace(removeNamespace=namespace, deleteNamespaceContent=True)
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
            # Still clean up any imported cameras that might remain (including root namespace)
            self.camera_tracker.cleanup_imported_cameras(
                preserve_user_cameras=False
            )  # Clean up everything
            # Also clean up any cameras imported to root namespace
            self._cleanup_root_namespace_cameras()
            self.camera_tracker.reset()
            return

        self.logger.debug(f"Cleaning up namespaces: {self._active_namespaces}")

        # Clean up imported cameras for each namespace
        total_deleted_cameras = []
        for namespace in self._active_namespaces:
            # For temporary imports, clean up ALL imported cameras (not just defaults)
            deleted = self.camera_tracker.cleanup_imported_cameras(
                namespace_filter=namespace,
                preserve_user_cameras=False,  # Clean up everything
            )
            total_deleted_cameras.extend(deleted)

        # Also clean up any cameras that were imported to root namespace
        root_deleted = self._cleanup_root_namespace_cameras()
        total_deleted_cameras.extend(root_deleted)

        if total_deleted_cameras:
            self.logger.info(
                f"Cleaned up {len(total_deleted_cameras)} imported cameras"
            )

        # Also check what temp namespaces actually exist in Maya
        all_namespaces = cmds.namespaceInfo(listOnlyNamespaces=True)
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

        # Reset camera tracking after cleanup
        self.camera_tracker.reset()

        if cleaned_count > 0:
            self.logger.debug(f"Cleaned up {cleaned_count} temporary imports")
        else:
            self.logger.debug("All temporary imports were already cleaned up")

    def _cleanup_root_namespace_cameras(self):
        """Clean up cameras that were imported to root namespace."""
        # Get cameras that were imported but don't have namespace prefixes
        root_imported_cameras = {
            cam
            for cam in self.camera_tracker.new_cameras
            if ":" not in cam  # No namespace prefix
        }

        if root_imported_cameras:
            self.logger.warning(
                f"Found cameras imported to root namespace: {sorted(root_imported_cameras)}"
            )

            # For temporary imports, clean up ALL imported cameras (aggressive cleanup)
            cameras_to_delete = list(root_imported_cameras)

            if cameras_to_delete:
                self.logger.info(
                    f"Cleaning up ALL root namespace imported cameras: {cameras_to_delete}"
                )
                try:
                    camera_transforms = []
                    for camera_name in cameras_to_delete:
                        try:
                            camera_transform = camera_name
                            camera_transforms.append(camera_transform)
                        except Exception as e:
                            self.logger.warning(
                                f"Could not access camera transform {camera_name}: {e}"
                            )

                    if camera_transforms:
                        cmds.delete(camera_transforms)
                        self.logger.debug(
                            f"Successfully deleted {len(camera_transforms)} root namespace cameras"
                        )

                    return cameras_to_delete
                except Exception as e:
                    self.logger.error(f"Failed to delete root namespace cameras: {e}")
                    return []

        return []

    def get_imported_cameras(self, namespace_filter=None):
        """Get cameras that were imported during the last import operation.

        Args:
            namespace_filter: Optional namespace to filter cameras by

        Returns:
            Set of imported camera names
        """
        return self.camera_tracker.get_imported_cameras(namespace_filter)

    def cleanup_imported_cameras(
        self, namespace_filter=None, preserve_user_cameras=True
    ):
        """Clean up imported cameras for a specific namespace.

        Args:
            namespace_filter: Optional namespace to filter cameras by
            preserve_user_cameras: If True, only delete Maya default cameras

        Returns:
            List of deleted camera names
        """
        return self.camera_tracker.cleanup_imported_cameras(
            namespace_filter, preserve_user_cameras
        )

    def cleanup_all_temp_namespaces_force(self) -> None:
        """Force cleanup of ALL temp namespaces in Maya, not just tracked ones.

        This is a nuclear option for cleaning up orphaned temp namespaces
        from previous failed runs.
        """
        all_namespaces = cmds.namespaceInfo(listOnlyNamespaces=True)
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
                if cmds.namespace(exists=namespace):
                    objects = cmds.ls(f"{namespace}:*")
                    self.logger.debug(
                        f"Force removing namespace {namespace} with {len(objects)} objects"
                    )
                    cmds.namespace(removeNamespace=namespace, deleteNamespaceContent=True)
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
        """Export objects to temporary file using cmds.ls() for robust object handling."""
        try:
            # cmds.ls() handles the input normalization automatically
            objects_to_export = cmds.ls(target_objects, type="transform")

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

            cmds.select(objects_to_export)
            cmds.file(
                str(temp_file),
                exportSelected=True,
                type="mayaAscii",
                force=True,
            )

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
            import_info = self.import_with_namespace(
                source_file, force_complete_import=True
            )

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

            # Not implemented: this would require opening target_scene,
            # importing temp_file, saving, and restoring the current scene.
            # Returning True here previously reported success without doing
            # anything — fail honestly instead.
            self.logger.error(
                f"import_to_target_scene is not implemented — nothing was "
                f"imported into {target_scene}"
            )
            return False

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

            if not cmds.namespace(exists=namespace):
                return True

            cmds.namespace(removeNamespace=namespace, deleteNamespaceContent=True)
            self.logger.debug(f"Cleaned up analysis namespace: {namespace}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to cleanup namespace {namespace}: {e}")
            return False

    def _create_unique_namespace(self, prefix: str = None) -> str:
        """Return a namespace name not currently present in the scene.

        ``int(time.time())`` is only 1-second resolution, so two imports in the
        same second would otherwise collide (and the second would wipe the
        first's temp namespace) — disambiguate against the live scene.
        """
        import time

        if prefix is None:
            prefix = self.TEMP_NAMESPACE_PREFIX

        base = f"{prefix}{int(time.time())}"
        ns, n = base, 1
        while cmds.namespace(exists=ns):
            ns = f"{base}_{n}"
            n += 1
        return ns

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
