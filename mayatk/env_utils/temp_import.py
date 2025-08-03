# !/usr/bin/python
# coding=utf-8
import tempfile
from pathlib import Path
from typing import Union, Optional, Dict, List, Any
import pythontk as ptk
import pymel.core as pm


class TempImport(ptk.LoggingMixin):
    """Handles temporary importing and namespace management for Maya scenes.

    Manages temporary imports with namespace isolation for safe cleanup.
    """

    # Constants
    TEMP_NAMESPACE_PREFIX = "temp_import_"
    DRY_RUN_NAMESPACE = "dry_run_temp"

    def __init__(self, dry_run: bool = True, fuzzy_matching: bool = True):
        super().__init__()
        self.dry_run = dry_run
        self.fuzzy_matching = fuzzy_matching
        self._active_namespaces = []  # Track only OUR namespaces for cleanup

    # =============================================================================
    # PUBLIC API METHODS
    # =============================================================================

    def import_with_namespace(
        self, source_file: Union[str, Path], namespace_prefix: str = None
    ) -> Optional[Dict]:
        """Import file and return import information.

        Returns:
            Dictionary with namespace and imported transform nodes if successful, None otherwise
        """
        try:
            source_file = Path(source_file)
            if not source_file.exists():
                self.logger.error(f"Source file not found: {source_file}")
                return None

            # Create unique namespace for tracking
            namespace = self._create_unique_namespace(namespace_prefix)

            if self.dry_run:
                self.logger.notice(
                    f"[DRY-RUN] Would import: {source_file} into namespace: {namespace}"
                )
                return {"namespace": namespace, "transforms": []}

            # Clean up existing namespace if it exists
            if pm.namespace(exists=namespace):
                pm.namespace(removeNamespace=namespace, deleteNamespaceContent=True)

            # Create the namespace
            pm.namespace(add=namespace)

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

            self._active_namespaces.append(namespace)

            self.logger.debug(
                f"Import {namespace}: {len(imported_nodes)} total nodes, {len(new_transforms)} transforms"
            )

            return {
                "namespace": namespace,
                "transforms": new_transforms,
                "all_nodes": imported_nodes,
            }

        except Exception as e:
            self.logger.error(f"Failed to import with namespace: {e}")
            return None

    def import_for_analysis(
        self, source_file: Union[str, Path], namespace: str = None
    ) -> Optional[List[Any]]:
        """Import file into temporary namespace for analysis (dry-run mode).

        Returns:
            List of imported nodes if successful, None otherwise
        """
        try:
            source_file = Path(source_file)
            if not source_file.exists():
                self.logger.error(f"Source file not found: {source_file}")
                return None

            if namespace is None:
                namespace = self.DRY_RUN_NAMESPACE

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
                f"Imported {len(imported_nodes) if imported_nodes else 0} nodes for analysis"
            )
            return imported_nodes

        except Exception as e:
            self.logger.error(f"Failed to import for analysis: {e}")
            return None

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
            if pm.namespace(exists=namespace):
                try:
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

        temp_namespaces_to_cleanup = self._active_namespaces.copy()
        cleaned_count = 0

        for namespace in temp_namespaces_to_cleanup:
            if self.cleanup_import(namespace):
                cleaned_count += 1

        self._active_namespaces.clear()

        if cleaned_count > 0:
            self.logger.debug(f"Cleaned up {cleaned_count} temporary imports")
        else:
            self.logger.debug("All temporary imports were already cleaned up")

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
