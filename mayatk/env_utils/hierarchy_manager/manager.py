# !/usr/bin/python
# coding=utf-8
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
import pymel.core as pm
import pythontk as ptk

# From this package
from mayatk.env_utils.namespace_sandbox import NamespaceSandbox
from mayatk.env_utils.hierarchy_manager.matching_engine import MatchingEngine


class HierarchyMapBuilder:
    """Handles building hierarchy path maps for Maya transforms."""

    @staticmethod
    def build_path_map(root):
        """Build a mapping of full paths to transform nodes."""
        path_map = {}

        def traverse(node, path=""):
            current_path = (
                "{}|{}".format(path, node.nodeName()) if path else node.nodeName()
            )
            path_map[current_path] = node
            for child in node.getChildren(type="transform"):
                traverse(child, current_path)

        traverse(root)
        return path_map


class HierarchyAnalyzer(ptk.LoggingMixin):
    """Handles hierarchy analysis and difference detection.

    Can analyze hierarchies from:
    1. Two Maya scene nodes (original behavior)
    2. Two Maya scene files using NamespaceSandbox
    3. One file and one scene node
    """

    def __init__(
        self,
        source: Union[Any, str, Path],
        target: Union[Any, str, Path],
        fuzzy_matching: bool = False,
        dry_run: bool = True,
    ):
        super().__init__()
        self.source = source
        self.target = target
        self.fuzzy_matching = fuzzy_matching
        self.dry_run = dry_run

        # NamespaceSandbox instance for file operations (now supports Maya + FBX automatically)
        self.temp_import = NamespaceSandbox(dry_run=False)  # Always import for analysis

        # MatchingEngine for advanced fuzzy matching
        self.matching_engine = MatchingEngine(
            import_manager=self.temp_import, fuzzy_matching=fuzzy_matching
        )

        # These will be set during analysis
        self.source_root = None
        self.target_root = None
        self._source_map = {}
        self._target_map = {}

        # Track imported namespaces for cleanup
        self._source_namespace = None
        self._target_namespace = None

    def analyze(self) -> ptk.HierarchyDiff:
        """Perform hierarchy analysis and return diff results."""
        try:
            # Resolve source and target to Maya nodes
            self.source_root = self._resolve_source_root()
            self.target_root = self._resolve_target_root()

            if not self.source_root or not self.target_root:
                raise ValueError("Failed to resolve source or target roots")

            # Build path maps
            self._source_map = HierarchyMapBuilder.build_path_map(self.source_root)
            self._target_map = HierarchyMapBuilder.build_path_map(self.target_root)

            # Analyze differences
            result = ptk.HierarchyDiff()
            self._find_missing_and_extra(result)
            self._find_reparented(result)

            if self.fuzzy_matching:
                self._find_fuzzy_matches(result)

            # Add metadata about the analysis
            result.add_metadata("source_root", self._get_source_description())
            result.add_metadata("target_root", self._get_target_description())
            result.add_metadata("fuzzy_matching", self.fuzzy_matching)

            return result

        finally:
            # Always cleanup imported files if in dry-run mode
            if self.dry_run:
                self._cleanup_temp_imports()

    def _resolve_source_root(self) -> Optional[Any]:
        """Resolve source to a Maya node, importing from file if necessary."""
        if self.source == "current_scene":
            # Use current scene - need to determine appropriate root
            # For now, use selected objects or scene root
            selected = pm.selected(type="transform")
            if selected:
                # Use first selected transform as root
                root = selected[0]
                self.logger.info(
                    f"Using selected object as source root: {root.nodeName()}"
                )
                return root
            else:
                # No selection - could use scene root or ask user to select
                self.logger.warning("No objects selected for current scene comparison")
                return None

        elif isinstance(self.source, (str, Path)):
            # Source is a file path - import it
            source_file = Path(self.source)
            if not source_file.exists():
                self.logger.error(f"Source file not found: {source_file}")
                return None

            self.logger.info(f"Importing source hierarchy from: {source_file.name}")
            import_info = self.temp_import.import_with_namespace(source_file)

            if not import_info or not import_info.get("transforms"):
                self.logger.error("Failed to import source file or no transforms found")
                return None

            self._source_namespace = import_info.get("namespace")
            transforms = import_info.get("transforms", [])

            # Find root transform (one with no parent or parent outside our namespace)
            for transform in transforms:
                parent = transform.getParent()
                if not parent or not parent.nodeName().startswith(
                    f"{self._source_namespace}:"
                ):
                    self.logger.info(f"Using source root: {transform.nodeName()}")
                    return transform

            # Fallback to first transform if no clear root found
            if transforms:
                self.logger.warning(
                    f"No clear root found, using first transform: {transforms[0].nodeName()}"
                )
                return transforms[0]

            return None
        else:
            # Source is already a Maya node
            return self.source

    def _resolve_target_root(self) -> Optional[Any]:
        """Resolve target to a Maya node, importing from file if necessary."""
        if self.target == "current_scene":
            # Use current scene - need to determine appropriate root
            selected = pm.selected(type="transform")
            if selected:
                # Use first selected transform as root
                root = selected[0]
                self.logger.info(
                    f"Using selected object as target root: {root.nodeName()}"
                )
                return root
            else:
                # No selection - could use scene root or ask user to select
                self.logger.warning("No objects selected for current scene comparison")
                return None

        elif isinstance(self.target, (str, Path)):
            # Target is a file path - import it
            target_file = Path(self.target)
            if not target_file.exists():
                self.logger.error(f"Target file not found: {target_file}")
                return None

            self.logger.info(f"Importing target hierarchy from: {target_file.name}")
            import_info = self.temp_import.import_with_namespace(target_file)

            if not import_info or not import_info.get("transforms"):
                self.logger.error("Failed to import target file or no transforms found")
                return None

            self._target_namespace = import_info.get("namespace")
            transforms = import_info.get("transforms", [])

            # Find root transform (one with no parent or parent outside our namespace)
            for transform in transforms:
                parent = transform.getParent()
                if not parent or not parent.nodeName().startswith(
                    f"{self._target_namespace}:"
                ):
                    self.logger.info(f"Using target root: {transform.nodeName()}")
                    return transform

            # Fallback to first transform if no clear root found
            if transforms:
                self.logger.warning(
                    f"No clear root found, using first transform: {transforms[0].nodeName()}"
                )
                return transforms[0]

            return None
        else:
            # Target is already a Maya node
            return self.target

    def _get_source_description(self) -> str:
        """Get description of source for metadata."""
        if self.source == "current_scene":
            return "Current Scene"
        elif isinstance(self.source, (str, Path)):
            return f"File: {Path(self.source).name}"
        else:
            return f"Node: {self.source.nodeName()}"

    def _get_target_description(self) -> str:
        """Get description of target for metadata."""
        if self.target == "current_scene":
            return "Current Scene"
        elif isinstance(self.target, (str, Path)):
            return f"File: {Path(self.target).name}"
        else:
            return f"Node: {self.target.nodeName()}"

    def _cleanup_temp_imports(self) -> None:
        """Clean up any temporary imports created during analysis."""
        # Use the NamespaceSandbox's cleanup mechanism instead of manual cleanup
        self.temp_import.cleanup_all_namespaces()

        # Reset our namespace tracking
        self._source_namespace = None
        self._target_namespace = None

    @property
    def source_map(self) -> Dict:
        """Get source hierarchy map."""
        return self._source_map

    @property
    def target_map(self) -> Dict:
        """Get target hierarchy map."""
        return self._target_map

    def _find_missing_and_extra(self, result: ptk.HierarchyDiff) -> None:
        """Find missing and extra nodes."""
        source_paths = set(self._source_map.keys())
        target_paths = set(self._target_map.keys())

        result.missing = sorted(source_paths - target_paths)
        result.extra = sorted(target_paths - source_paths)

    def _find_reparented(self, result: ptk.HierarchyDiff) -> None:
        """Find reparented nodes."""
        source_names = {n.nodeName(): p for p, n in self._source_map.items()}
        target_names = {n.nodeName(): p for p, n in self._target_map.items()}
        shared_names = set(source_names) & set(target_names)

        reparented = []
        for name in shared_names:
            src_path = source_names[name]
            tgt_path = target_names[name]
            if src_path != tgt_path:
                reparented.append(name)
                expected_path = src_path
                if expected_path in result.missing:
                    result.missing.remove(expected_path)

        result.reparented = reparented

    def _find_fuzzy_matches(self, result: ptk.HierarchyDiff) -> None:
        """Find fuzzy matches using the comprehensive MatchingEngine."""
        # Get all target transforms for matching
        target_transforms = list(self._target_map.values())

        # Use MatchingEngine to find matches for missing items
        found_objects, fuzzy_match_map = self.matching_engine.find_matches(
            target_objects=result.missing,
            imported_transforms=target_transforms,
            dry_run=True,  # Always dry-run for analysis
        )

        # Convert the matching engine results to the expected format
        fuzzy_matches = []
        paths_to_remove_from_missing = []
        paths_to_remove_from_extra = []

        for matched_node, target_name in fuzzy_match_map.items():
            # Find the path of the matched node in our target map
            matched_path = None
            for path, node in self._target_map.items():
                if node == matched_node:
                    matched_path = path
                    break

            if matched_path:
                # Create fuzzy match entry
                fuzzy_match = {
                    "current_name": matched_path,  # Path in imported file
                    "target_name": target_name,  # What it should be named
                }
                fuzzy_matches.append(fuzzy_match)

                # Remove from missing/extra lists
                if target_name in result.missing:
                    paths_to_remove_from_missing.append(target_name)
                if matched_path in result.extra:
                    paths_to_remove_from_extra.append(matched_path)

        # Remove matched items from missing/extra lists
        for path in paths_to_remove_from_missing:
            if path in result.missing:
                result.missing.remove(path)
        for path in paths_to_remove_from_extra:
            if path in result.extra:
                result.extra.remove(path)

        result.fuzzy_matches = fuzzy_matches

        # Log matches found
        for match in fuzzy_matches:
            self.logger.info(
                "Fuzzy match found: {} -> {}".format(
                    match["current_name"], match["target_name"]
                )
            )


class HierarchyReporter(ptk.LoggingMixin):
    """Handles hierarchy reporting and output formatting."""

    def __init__(self):
        super().__init__()

    def print_report(self, diff_result: ptk.HierarchyDiff) -> None:
        """Print hierarchy report."""
        if diff_result.is_valid():
            self.logger.success("Hierarchy is Unity-safe.")
            return

        self.logger.warning("Hierarchy mismatch detected:")
        self._print_section("Missing in target:", diff_result.missing)
        self._print_section("Extra in target:", diff_result.extra)
        self._print_section("Renamed nodes:", diff_result.renamed)
        self._print_section("Reparented nodes:", diff_result.reparented)

        if diff_result.fuzzy_matches:
            self.logger.notice("Fuzzy matches found:")
            for match in diff_result.fuzzy_matches:
                self.logger.info(f"  {match['current_name']} -> {match['target_name']}")

    def save_json_report(
        self, diff_result: ptk.HierarchyDiff, path: Path, indent: int = 2
    ) -> None:
        """Save hierarchy diff to JSON file."""
        diff_result.save_to_file(path, indent)
        self.logger.success("Saved hierarchy diff to: {}".format(path))

    def _print_section(self, title: str, items: List[str]) -> None:
        """Print a section of the report."""
        if items:
            self.logger.notice(title)
            for item in items:
                self.logger.warning("  -> {}".format(item))


class HierarchyRepairer(ptk.LoggingMixin):
    """Handles hierarchy repair operations."""

    def __init__(self, analyzer: HierarchyAnalyzer, dry_run: bool = True):
        super().__init__()
        self.analyzer = analyzer
        self.dry_run = dry_run

    def repair_all(self, diff_result: ptk.HierarchyDiff) -> None:
        """Repair all detected issues."""
        self.repair_fuzzy_matches(diff_result)
        self.repair_reparented(diff_result)
        self.repair_missing(diff_result)

    def repair_fuzzy_matches(self, diff_result: ptk.HierarchyDiff) -> None:
        """Repair fuzzy matched nodes by renaming them."""
        if not diff_result.fuzzy_matches:
            self.logger.success("No fuzzy matches to repair.")
            return

        self.logger.info(
            "Repairing {} fuzzy matches...".format(len(diff_result.fuzzy_matches))
        )

        for match in diff_result.fuzzy_matches:
            self._rename_and_reparent(match)

    def repair_reparented(self, diff_result: ptk.HierarchyDiff) -> None:
        """Repair reparented nodes."""
        if not diff_result.reparented:
            self.logger.success("No reparented nodes to fix.")
            return

        self.logger.info(
            "Repairing {} reparented nodes...".format(len(diff_result.reparented))
        )

        for name in diff_result.reparented:
            self._reparent_node(name)

    def repair_missing(self, diff_result: ptk.HierarchyDiff) -> None:
        """Repair missing nodes in hierarchical order."""
        if not diff_result.missing:
            self.logger.success("No missing nodes to repair.")
            return

        # Sort missing paths by depth to create parents before children
        missing_sorted = sorted(diff_result.missing, key=lambda x: x.count("|"))
        self.logger.info("Creating {} missing nodes...".format(len(missing_sorted)))

        for path in missing_sorted:
            name = path.split("|")[-1]
            if name not in diff_result.reparented:
                self._create_missing_node(name, path)
            else:
                self.logger.info(
                    "Skipping {} - already handled as reparented".format(name)
                )

    def _rename_and_reparent(self, match: Dict[str, str]) -> None:
        """Rename and reparent a fuzzy matched node."""
        current_name = match["current_name"]
        target_name = match["target_name"]

        if self.dry_run:
            self.logger.notice(
                "[dry-run] Would rename {} to {}".format(current_name, target_name)
            )
            return

        try:
            if not pm.objExists(current_name):
                self.logger.error("Node {} no longer exists".format(current_name))
                return

            node = pm.PyNode(current_name)
            node.rename(target_name)
            self.logger.success("Renamed {} to {}".format(current_name, target_name))

            # Handle reparenting if needed
            parent_path = match.get("parent_path")
            if parent_path and parent_path in self.analyzer.target_map:
                correct_parent = self.analyzer.target_map[parent_path]
                current_parent = node.getParent()
                if current_parent != correct_parent:
                    pm.parent(node, correct_parent)
                    self.logger.success(
                        "Reparented {} to {}".format(target_name, correct_parent)
                    )

        except Exception as e:
            self.logger.error(
                "Failed to rename/reparent {}: {}".format(current_name, e)
            )

    def _reparent_node(self, name: str) -> None:
        """Reparent a single node to its correct parent."""
        if self.dry_run:
            self.logger.notice("[dry-run] Would reparent {}".format(name))
            return

        try:
            # Find paths for the node
            src_path = self._find_path_for_name(self.analyzer.source_map, name)
            tgt_path = self._find_path_for_name(self.analyzer.target_map, name)

            if not src_path or not tgt_path:
                self.logger.warning("Cannot find paths for {}".format(name))
                return

            source_node = self.analyzer.source_map[src_path]
            target_node = self.analyzer.target_map[tgt_path]

            # Get correct parent from source
            source_parent = source_node.getParent()
            if not source_parent:
                self.logger.warning("Source node {} has no parent".format(name))
                return

            # Find corresponding parent in target
            source_parent_path = self._get_node_path(
                source_parent, self.analyzer.source_root
            )
            target_parent = self.analyzer.target_map.get(source_parent_path)

            if not target_parent:
                self.logger.warning("Cannot find target parent for {}".format(name))
                return

            # Perform reparenting
            current_parent = target_node.getParent()
            if current_parent == target_parent:
                self.logger.info("{} is already correctly parented".format(name))
                return

            pm.parent(target_node, target_parent)
            self.logger.success(
                "Reparented {} from {} to {}".format(
                    name, current_parent, target_parent
                )
            )

        except Exception as e:
            self.logger.error("Failed to reparent {}: {}".format(name, e))

    def _create_missing_node(self, name: str, path: str) -> None:
        """Create a missing node."""
        if self.dry_run:
            self.logger.notice("[dry-run] Would create {} at {}".format(name, path))
            return

        try:
            parent_path = "|".join(path.split("|")[:-1])

            if not parent_path:
                parent = self.analyzer.target_root
            else:
                parent = self.analyzer.target_map.get(parent_path)
                if not parent:
                    self._refresh_target_map()
                    parent = self.analyzer.target_map.get(parent_path)

            if not parent:
                self.logger.warning(
                    "Missing parent for {} (parent_path: {})".format(path, parent_path)
                )
                return

            node = pm.createNode("transform", name=name, parent=parent)
            self.logger.success("Created transform: {}".format(node))

        except Exception as e:
            self.logger.error("Failed to create {}: {}".format(name, e))

    def _refresh_target_map(self) -> None:
        """Refresh the target map to include newly created nodes."""
        if not self.dry_run:
            self.analyzer._target_map = HierarchyMapBuilder.build_path_map(
                self.analyzer.target_root
            )

    def _get_node_path(self, node: Any, root: Any) -> str:
        """Get the path of a node relative to root."""
        path_parts = []
        current = node

        while current and current != root:
            path_parts.append(current.nodeName())
            current = current.getParent()

        if current != root:
            return ""

        path_parts.append(root.nodeName())
        return "|".join(reversed(path_parts))

    def _find_path_for_name(self, mapping: Dict, name: str) -> Optional[str]:
        """Find the path for a given node name."""
        for path, node in mapping.items():
            if node.nodeName() == name:
                return path
        return None


class HierarchyManager(ptk.LoggingMixin):
    """Complete hierarchy management with analysis and repair capabilities.

    This is the main facade that orchestrates the different components.

    Workflow-focused design:
    - Compare current scene to a reference file (most common)
    - Compare a source file to current scene
    - Compare two specific nodes in current scene (legacy)

    Examples:
        # Compare current scene to reference file (most common workflow)
        manager = HierarchyManager("reference.ma")
        manager = HierarchyManager(target="reference.ma")

        # Compare source file to current scene
        manager = HierarchyManager(source="source.ma")

        # Compare two specific nodes (legacy/advanced usage)
        manager = HierarchyManager(source=source_node, target=target_node)
    """

    def __init__(
        self,
        reference_file: Union[str, Path] = None,
        *,
        source: Union[Any, str, Path] = None,
        target: Union[Any, str, Path] = None,
        dry_run: bool = True,
        fuzzy_matching: bool = False,
    ):
        super().__init__()
        self.dry_run = dry_run
        self.fuzzy_matching = fuzzy_matching

        # Resolve the comparison setup
        resolved_source, resolved_target = self._resolve_comparison_setup(
            reference_file, source, target
        )

        self.source = resolved_source
        self.target = resolved_target

        # Initialize components
        self.analyzer = HierarchyAnalyzer(
            resolved_source, resolved_target, fuzzy_matching, dry_run
        )
        self.reporter = HierarchyReporter()
        self.repairer = HierarchyRepairer(self.analyzer, dry_run)

        # Analysis state
        self._diff_result = None
        self._analyzed = False

    def _resolve_comparison_setup(self, reference_file, source, target):
        """Resolve the comparison setup based on provided arguments."""

        # Count provided arguments
        provided_args = sum(x is not None for x in [reference_file, source, target])

        if provided_args == 0:
            raise ValueError("Must provide either reference_file, source, or target")

        if provided_args > 1 and reference_file is not None:
            raise ValueError("Cannot use reference_file with explicit source/target")

        if provided_args == 2 and (source is None or target is None):
            raise ValueError(
                "When providing two arguments, use source= and target= explicitly"
            )

        # Case 1: Reference file provided (most common)
        if reference_file is not None:
            self.logger.info(
                f"Comparing current scene to reference: {Path(reference_file).name}"
            )
            return "current_scene", reference_file

        # Case 2: Only source provided - compare source file to current scene
        if source is not None and target is None:
            if isinstance(source, (str, Path)):
                self.logger.info(
                    f"Comparing source file to current scene: {Path(source).name}"
                )
                return source, "current_scene"
            else:
                raise ValueError("When providing only source, it must be a file path")

        # Case 3: Only target provided - compare current scene to target file
        if target is not None and source is None:
            if isinstance(target, (str, Path)):
                self.logger.info(
                    f"Comparing current scene to target file: {Path(target).name}"
                )
                return "current_scene", target
            else:
                raise ValueError("When providing only target, it must be a file path")

        # Case 4: Both source and target provided (legacy/advanced)
        if source is not None and target is not None:
            self.logger.info("Comparing explicit source and target")
            return source, target

        raise ValueError("Invalid combination of arguments")

    @property
    def diff_result(self) -> ptk.HierarchyDiff:
        """Get diff result, analyzing if needed."""
        if not self._analyzed:
            self._analyze()
        return self._diff_result

    @property
    def source_map(self) -> Dict:
        """Get source hierarchy map."""
        if not self._analyzed:
            self._analyze()
        return self.analyzer.source_map

    @property
    def target_map(self) -> Dict:
        """Get target hierarchy map."""
        if not self._analyzed:
            self._analyze()
        return self.analyzer.target_map

    def is_valid(self) -> bool:
        """Check if hierarchy is valid."""
        return self.diff_result.is_valid()

    def analyze(self) -> ptk.HierarchyDiff:
        """Force analysis and return results."""
        self._analyze()
        return self._diff_result

    def print_report(self) -> None:
        """Print hierarchy report."""
        self.reporter.print_report(self.diff_result)

    def repair_all(self) -> None:
        """Repair all detected issues."""
        diff = self.diff_result
        self.repairer.repair_all(diff)
        # Mark as needing re-analysis after repairs
        self._analyzed = False

    def repair_fuzzy_matches(self) -> None:
        """Repair fuzzy matched nodes by renaming them."""
        self.repairer.repair_fuzzy_matches(self.diff_result)

    def repair_reparented(self) -> None:
        """Repair reparented nodes."""
        self.repairer.repair_reparented(self.diff_result)

    def repair_missing(self) -> None:
        """Repair missing nodes in hierarchical order."""
        self.repairer.repair_missing(self.diff_result)

    def re_analyze(self) -> ptk.HierarchyDiff:
        """Force re-analysis (useful after repairs)."""
        self._analyzed = False
        return self.diff_result

    def save_json_report(self, path: Path, indent: int = 2) -> None:
        """Save hierarchy diff to JSON file."""
        self.reporter.save_json_report(self.diff_result, path, indent)

    def cleanup(self) -> None:
        """Manually clean up any temporary imports."""
        self.analyzer._cleanup_temp_imports()

    def force_cleanup_all_temp_namespaces(self) -> None:
        """Force cleanup of ALL temp namespaces in Maya (nuclear option)."""
        self.analyzer.temp_import.cleanup_all_temp_namespaces_force()

    def get_active_namespaces(self) -> List[str]:
        """Get list of active temporary namespaces for debugging."""
        return self.analyzer.temp_import._active_namespaces.copy()

    def _analyze(self) -> None:
        """Perform hierarchy analysis."""
        if self._analyzed:
            return

        self._diff_result = self.analyzer.analyze()
        self._analyzed = True


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    # Example 1: Compare current scene to reference file (most common workflow)
    reference_file = "C5_AFT_COMP_ASSEMBLY_module.ma"
    reference_path = f"O:\\Dropbox (Moth+Flame)\\Moth+Flame Dropbox\\Ryan Simpson\\_tests\\hierarchy_test\\{reference_file}"

    # Select an object in your scene first, then run:
    manager = HierarchyManager(reference_path, fuzzy_matching=True, dry_run=True)

    # Alternative syntax:
    # manager = HierarchyManager(target=reference_path, fuzzy_matching=True)

    # Example 2: Compare source file to current scene (less common)
    # source_file = "C5_AFT_COMP_ASSEMBLY_current.ma"
    # source_path = f"O:\\Dropbox (Moth+Flame)\\Moth+Flame Dropbox\\Ryan Simpson\\_tests\\hierarchy_test\\{source_file}"
    # manager = HierarchyManager(source=source_path, fuzzy_matching=True)

    # Example 3: Compare two specific nodes (legacy/advanced usage)
    # source_root, target_root = pm.selected(flatten=True)
    # manager = HierarchyManager(source=source_root, target=target_root, fuzzy_matching=True)

    if not manager.is_valid():
        if not manager.dry_run:
            manager.repair_all()

        # Check results after repair
        manager.print_report()
    else:
        print("Hierarchy is already valid!")

    # Clean up any temporary imports
    manager.cleanup()


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
# This refactored module provides clear separation of concerns:
#
# - HierarchyMapBuilder: Pure utility for building path maps
# - HierarchyAnalyzer: Analysis logic with NamespaceSandbox support for file-based comparison
# - HierarchyReporter: All reporting and output formatting
# - HierarchyRepairer: All repair operations
# - HierarchyManager: High-level facade that orchestrates components
#
# Key improvements:
# - Single responsibility principle applied to each class
# - Better testability (each component can be tested in isolation)
# - Reduced coupling between analysis, repair, and reporting
# - Cleaner dependency injection pattern
# - Easier to extend (e.g., add new repair strategies)
# - File-based hierarchy comparison using NamespaceSandbox
# - Support for mixed file/node comparisons
# - Automatic cleanup of temporary imports
# --------------------------------------------------------------------------------------------
