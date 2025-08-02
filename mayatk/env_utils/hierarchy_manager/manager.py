# !/usr/bin/python
# coding=utf-8
import json
import re
from pathlib import Path
import pythontk as ptk
import pymel.core as pm

# Import from pythontk for general utilities
from pythontk.str_utils import FuzzyMatcher
from pythontk.core_utils import HierarchyDiffResult


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


class HierarchyManager(ptk.LoggingMixin):
    """Complete hierarchy management with analysis and repair capabilities."""

    def __init__(
        self,
        source_root,
        target_root,
        dry_run=True,
        fuzzy_matching=False,
    ):
        super(HierarchyManager, self).__init__()
        self.source_root = source_root
        self.target_root = target_root
        self.dry_run = dry_run
        self.fuzzy_matching = fuzzy_matching

        # Analysis state
        self._diff_result = None
        self._source_map = {}
        self._target_map = {}
        self._analyzed = False

    @property
    def diff_result(self):
        """Get diff result, analyzing if needed."""
        if not self._analyzed:
            self._analyze()
        return self._diff_result

    @property
    def source_map(self):
        """Get source hierarchy map."""
        if not self._analyzed:
            self._analyze()
        return self._source_map

    @property
    def target_map(self):
        """Get target hierarchy map."""
        if not self._analyzed:
            self._analyze()
        return self._target_map

    def is_valid(self):
        """Check if hierarchy is valid."""
        return self.diff_result.is_valid()

    def analyze(self):
        """Force analysis and return results."""
        self._analyze()
        return self._diff_result

    def print_report(self):
        """Print hierarchy report."""
        if self.diff_result.is_valid():
            self.logger.success("Hierarchy is Unity-safe.")
            return

        self.logger.warning("Hierarchy mismatch detected:")
        self._print_section("Missing in target:", self.diff_result.missing)
        self._print_section("Extra in target:", self.diff_result.extra)
        self._print_section("Renamed nodes:", self.diff_result.renamed)
        self._print_section("Reparented nodes:", self.diff_result.reparented)

    def repair_all(self):
        """Repair all detected issues."""
        # Ensure analysis is done
        diff = self.diff_result

        self.repair_fuzzy_matches(diff)
        self.repair_reparented(diff)
        self.repair_missing(diff)

        # Mark as needing re-analysis after repairs
        self._analyzed = False

    def repair_fuzzy_matches(self, diff_result):
        """Repair fuzzy matched nodes by renaming them."""
        if not diff_result.fuzzy_matches:
            self.logger.success("No fuzzy matches to repair.")
            return

        self.logger.info(
            "Repairing {} fuzzy matches...".format(len(diff_result.fuzzy_matches))
        )

        for match in diff_result.fuzzy_matches:
            self._rename_and_reparent(match)

    def repair_reparented(self, diff_result):
        """Repair reparented nodes."""
        if not diff_result.reparented:
            self.logger.success("No reparented nodes to fix.")
            return

        self.logger.info(
            "Repairing {} reparented nodes...".format(len(diff_result.reparented))
        )

        for name in diff_result.reparented:
            self._reparent_node(name)

    def repair_missing(self, diff_result):
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

    def re_analyze(self):
        """Force re-analysis (useful after repairs)."""
        self._analyzed = False
        return self.diff_result

    def save_json_report(self, path, indent=2):
        """Save hierarchy diff to JSON file."""
        self.diff_result.save_to_file(path, indent)
        self.logger.success("Saved hierarchy diff to: {}".format(path))

    # ===== PRIVATE METHODS =====

    def _analyze(self):
        """Perform hierarchy analysis."""
        if self._analyzed:
            return

        # Build path maps
        self._source_map = HierarchyMapBuilder.build_path_map(self.source_root)
        self._target_map = HierarchyMapBuilder.build_path_map(self.target_root)

        # Analyze differences
        result = HierarchyDiffResult()
        self._find_missing_and_extra(result)
        self._find_reparented(result)
        if self.fuzzy_matching:
            self._find_fuzzy_matches(result)

        # Add metadata about the analysis
        result.add_metadata("source_root", self.source_root.nodeName())
        result.add_metadata("target_root", self.target_root.nodeName())
        result.add_metadata("fuzzy_matching", self.fuzzy_matching)

        self._diff_result = result
        self._analyzed = True

    def _find_missing_and_extra(self, result):
        """Find missing and extra nodes."""
        source_paths = set(self._source_map.keys())
        target_paths = set(self._target_map.keys())

        result.missing = sorted(source_paths - target_paths)
        result.extra = sorted(target_paths - source_paths)

    def _find_reparented(self, result):
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

    def _find_fuzzy_matches(self, result):
        """Find fuzzy matches for trailing digits using pythontk FuzzyMatcher."""
        fuzzy_matches, paths_to_remove_from_missing, paths_to_remove_from_extra = (
            FuzzyMatcher.find_trailing_digit_matches(result.missing, result.extra)
        )

        # Remove matched items from missing/extra lists
        for path in paths_to_remove_from_missing:
            result.missing.remove(path)
        for path in paths_to_remove_from_extra:
            result.extra.remove(path)

        result.fuzzy_matches = fuzzy_matches

        # Log matches found
        for match in fuzzy_matches:
            self.logger.info(
                "Fuzzy match found: {} -> {}".format(
                    match["current_name"], match["target_name"]
                )
            )

    def _rename_and_reparent(self, match):
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
            if parent_path and parent_path in self._target_map:
                correct_parent = self._target_map[parent_path]
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

    def _reparent_node(self, name):
        """Reparent a single node to its correct parent."""
        if self.dry_run:
            self.logger.notice("[dry-run] Would reparent {}".format(name))
            return

        try:
            # Find paths for the node
            src_path = self._find_path_for_name(self._source_map, name)
            tgt_path = self._find_path_for_name(self._target_map, name)

            if not src_path or not tgt_path:
                self.logger.warning("Cannot find paths for {}".format(name))
                return

            source_node = self._source_map[src_path]
            target_node = self._target_map[tgt_path]

            # Get correct parent from source
            source_parent = source_node.getParent()
            if not source_parent:
                self.logger.warning("Source node {} has no parent".format(name))
                return

            # Find corresponding parent in target
            source_parent_path = self._get_node_path(source_parent, self.source_root)
            target_parent = self._target_map.get(source_parent_path)

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

    def _create_missing_node(self, name, path):
        """Create a missing node."""
        if self.dry_run:
            self.logger.notice("[dry-run] Would create {} at {}".format(name, path))
            return

        try:
            parent_path = "|".join(path.split("|")[:-1])

            if not parent_path:
                parent = self.target_root
            else:
                parent = self._target_map.get(parent_path)
                if not parent:
                    self._refresh_target_map()
                    parent = self._target_map.get(parent_path)

            if not parent:
                self.logger.warning(
                    "Missing parent for {} (parent_path: {})".format(path, parent_path)
                )
                return

            node = pm.createNode("transform", name=name, parent=parent)
            self.logger.success("Created transform: {}".format(node))

        except Exception as e:
            self.logger.error("Failed to create {}: {}".format(name, e))

    def _refresh_target_map(self):
        """Refresh the target map to include newly created nodes."""
        if not self.dry_run:
            self._target_map = HierarchyMapBuilder.build_path_map(self.target_root)

    def _get_node_path(self, node, root):
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

    def _find_path_for_name(self, mapping, name):
        """Find the path for a given node name."""
        for path, node in mapping.items():
            if node.nodeName() == name:
                return path
        return None

    def _print_section(self, title, items):
        """Print a section of the report."""
        if items:
            self.logger.notice(title)
            for item in items:
                self.logger.warning("  -> {}".format(item))


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    source_root, target_root = pm.selected(flatten=True)
    manager = HierarchyManager(
        source_root,
        target_root,
        dry_run=False,
        enable_fuzzy_matching=True,
    )

    if not manager.is_valid():
        manager.repair_all()

        # Check results after repair
        manager.print_report()
    else:
        print("Hierarchy is already valid!")
# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
# This unified module now contains all hierarchy analysis and repair functionality.
# It uses general-purpose utilities from pythontk for string matching and diff results,
# while keeping Maya-specific operations (transform node manipulation) in this module.
# --------------------------------------------------------------------------------------------
