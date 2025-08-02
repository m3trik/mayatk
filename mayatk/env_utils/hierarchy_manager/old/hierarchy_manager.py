# !/usr/bin/python
# coding=utf-8
import json
from enum import Enum
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Protocol
import pythontk as ptk
import pymel.core as pm


class RepairAction(Enum):
    """Enumeration of available repair actions."""

    REPARENT = "reparent"
    CREATE_MISSING = "create_missing"
    REMOVE_EXTRA = "remove_extra"
    RENAME_AND_REPARENT = "rename_and_reparent"  # New action


class DiffResult:
    """Data class to hold hierarchy difference results."""

    def __init__(self):
        self.missing: List[str] = []
        self.extra: List[str] = []
        self.renamed: List[str] = []
        self.reparented: List[str] = []
        self.fuzzy_matches: List[Dict[str, str]] = []  # New field for fuzzy matches

    def is_valid(self) -> bool:
        """Check if hierarchy is valid (no differences)."""
        return not (self.missing or self.renamed or self.reparented)

    def as_dict(self) -> Dict[str, List[str]]:
        """Convert to dictionary representation."""
        return {
            "missing": self.missing,
            "extra": self.extra,
            "renamed": self.renamed,
            "reparented": self.reparented,
        }


class RepairStrategy(ABC):
    """Abstract base class for repair strategies."""

    @abstractmethod
    def execute(self, context: "RepairContext") -> bool:
        """Execute the repair strategy."""
        pass

    @abstractmethod
    def get_description(self, context: "RepairContext") -> str:
        """Get a description of what this strategy would do."""
        pass


class ReparentStrategy(RepairStrategy):
    """Strategy for reparenting nodes."""

    def execute(self, context: "RepairContext") -> bool:
        try:
            # Verify nodes exist before attempting to parent
            if not pm.objExists(context.target_node.name()):
                context.logger.error(
                    f"Target node {context.target_node.name()} no longer exists"
                )
                return False

            if not pm.objExists(context.correct_parent.name()):
                context.logger.error(
                    f"Target parent {context.correct_parent.name()} no longer exists"
                )
                return False

            # Check if already correctly parented
            current_parent = context.target_node.getParent()
            if current_parent == context.correct_parent:
                context.logger.info(f"{context.name} is already correctly parented")
                return True

            # Perform the reparenting
            pm.parent(context.target_node, context.correct_parent)
            context.logger.success(
                f"Reparented {context.name} from {current_parent} to {context.correct_parent}"
            )
            return True

        except Exception as e:
            context.logger.error(f"Failed to reparent {context.name}: {e}")
            return False

    def get_description(self, context: "RepairContext") -> str:
        current_parent = (
            context.target_node.getParent()
            if hasattr(context, "target_node")
            else "unknown"
        )
        return (
            f"Reparent {context.name} from {current_parent} to {context.correct_parent}"
        )


class CreateMissingStrategy(RepairStrategy):
    """Strategy for creating missing nodes."""

    def execute(self, context: "RepairContext") -> bool:
        try:
            node = pm.createNode("transform", name=context.name, parent=context.parent)
            context.logger.success(f"Created transform: {node}")
            return True
        except Exception as e:
            context.logger.error(f"Failed to create {context.name}: {e}")
            return False

    def get_description(self, context: "RepairContext") -> str:
        return f"Create {context.name} under {context.parent}"


class RenameAndReparentStrategy(RepairStrategy):
    """Strategy for renaming and reparenting fuzzy matched nodes."""

    def execute(self, context: "RepairContext") -> bool:
        try:
            # First rename the node
            old_name = context.current_name
            new_name = context.target_name

            if not pm.objExists(old_name):
                context.logger.error(f"Node {old_name} no longer exists")
                return False

            node = pm.PyNode(old_name)
            node.rename(new_name)
            context.logger.success(f"Renamed {old_name} to {new_name}")

            # Then reparent if needed
            if hasattr(context, "correct_parent") and context.correct_parent:
                current_parent = node.getParent()
                if current_parent != context.correct_parent:
                    pm.parent(node, context.correct_parent)
                    context.logger.success(
                        f"Reparented {new_name} to {context.correct_parent}"
                    )

            return True

        except Exception as e:
            context.logger.error(
                f"Failed to rename/reparent {context.current_name}: {e}"
            )
            return False

    def get_description(self, context: "RepairContext") -> str:
        desc = f"Rename {context.current_name} to {context.target_name}"
        if hasattr(context, "correct_parent") and context.correct_parent:
            desc += f" and reparent to {context.correct_parent}"
        return desc


class RepairContext:
    """Context object to pass data between repair strategies."""

    def __init__(self, name: str, logger, **kwargs):
        self.name = name
        self.logger = logger
        for key, value in kwargs.items():
            setattr(self, key, value)


class HierarchyBase:
    """Base class for hierarchy components."""

    def __init__(self, manager: "HierarchyManager"):
        self.manager = manager
        self.logger = manager.logger

    @property
    def diff_result(self) -> DiffResult:
        """Get the diff result from manager."""
        return self.manager.diff_result

    @property
    def source_map(self) -> Dict[str, pm.nt.Transform]:
        """Get source map from manager."""
        return self.manager.source_map

    @property
    def target_map(self) -> Dict[str, pm.nt.Transform]:
        """Get target map from manager."""
        return self.manager.target_map


class HierarchyDiff(HierarchyBase):
    """Handles hierarchy difference analysis."""

    def __init__(
        self, manager: "HierarchyManager", enable_fuzzy_matching: bool = False
    ):
        super().__init__(manager)
        self.enable_fuzzy_matching = enable_fuzzy_matching

    def _build_path_map(self, root: pm.nt.Transform) -> Dict[str, pm.nt.Transform]:
        """Build a mapping of full paths to transform nodes."""
        path_map = {}

        def traverse(node, path=""):
            current_path = f"{path}|{node.nodeName()}" if path else node.nodeName()
            path_map[current_path] = node

            for child in node.getChildren(type="transform"):
                traverse(child, current_path)

        traverse(root)
        return path_map

    def _find_reparented(self, source_map: Dict, target_map: Dict, result: DiffResult):
        """Find reparented nodes."""
        # Get nodes that exist in both hierarchies but at different paths
        source_names = {n.nodeName(): p for p, n in source_map.items()}
        target_names = {n.nodeName(): p for p, n in target_map.items()}
        shared_names = set(source_names) & set(target_names)

        reparented = []
        for name in shared_names:
            src_path = source_names[name]
            tgt_path = target_names[name]
            if src_path != tgt_path:
                reparented.append(name)
                # IMPORTANT: Remove from missing list since we found it (just in wrong place)
                expected_path = src_path
                if expected_path in result.missing:
                    result.missing.remove(expected_path)

        result.reparented = reparented

    def _find_missing_and_extra(
        self, source_map: Dict, target_map: Dict, result: DiffResult
    ):
        """Find missing and extra nodes."""
        source_paths = set(source_map.keys())
        target_paths = set(target_map.keys())

        result.missing = sorted(source_paths - target_paths)
        result.extra = sorted(target_paths - source_paths)

    def _find_fuzzy_matches(
        self, source_map: Dict, target_map: Dict, result: DiffResult
    ):
        """Find fuzzy matches for trailing digits and store them for repair."""
        if not self.enable_fuzzy_matching:
            return

        import re

        def get_base_name(name):
            return re.sub(r"\d+$", "", name)

        missing_to_check = result.missing.copy()
        extra_to_check = result.extra.copy()

        fuzzy_matches = []
        paths_to_remove_from_missing = []
        paths_to_remove_from_extra = []

        for missing_path in missing_to_check:
            missing_node_name = missing_path.split("|")[-1]
            missing_base_name = get_base_name(missing_node_name)

            # Only try fuzzy matching if base name is different (has trailing digits)
            if missing_base_name == missing_node_name:
                continue

            # Look for extra nodes with same base name
            for extra_path in extra_to_check:
                extra_node_name = extra_path.split("|")[-1]
                extra_base_name = get_base_name(extra_node_name)

                # Found a potential match
                if (
                    missing_base_name == extra_base_name
                    and missing_node_name != extra_node_name
                ):
                    # Verify they're in similar locations (same parent structure)
                    missing_parent = "|".join(missing_path.split("|")[:-1])
                    extra_parent = "|".join(extra_path.split("|")[:-1])

                    if missing_parent == extra_parent:
                        fuzzy_match = {
                            "current_name": extra_node_name,
                            "current_path": extra_path,
                            "target_name": missing_node_name,
                            "target_path": missing_path,
                            "parent_path": missing_parent,
                        }
                        fuzzy_matches.append(fuzzy_match)
                        paths_to_remove_from_missing.append(missing_path)
                        paths_to_remove_from_extra.append(extra_path)
                        self.logger.info(
                            f"Fuzzy match found: {extra_node_name} -> {missing_node_name}"
                        )
                        break

        # Remove matched items from missing/extra lists
        for path in paths_to_remove_from_missing:
            result.missing.remove(path)
        for path in paths_to_remove_from_extra:
            result.extra.remove(path)

        result.fuzzy_matches = fuzzy_matches

    def analyze(self) -> DiffResult:
        """Analyze differences between source and target hierarchies."""
        result = DiffResult()

        # Build path maps
        source_map = self._build_path_map(self.manager.source_root)
        target_map = self._build_path_map(self.manager.target_root)

        # Store maps in manager for other components
        self.manager.source_map = source_map
        self.manager.target_map = target_map

        # Analyze differences - ORDER MATTERS!
        self._find_missing_and_extra(source_map, target_map, result)
        self._find_reparented(source_map, target_map, result)
        self._find_fuzzy_matches(source_map, target_map, result)  # New step

        return result


class HierarchyReport(HierarchyBase):
    """Handles hierarchy reporting."""

    def print_summary(self) -> None:
        """Print a summary of hierarchy differences."""
        if self.diff_result.is_valid():
            self.logger.success("Hierarchy is Unity-safe.")
            return

        self.logger.warning("Hierarchy mismatch detected:")
        self._print_section("Missing in target:", self.diff_result.missing)
        self._print_section("Extra in target:", self.diff_result.extra)
        self._print_section("Renamed nodes:", self.diff_result.renamed)
        self._print_section("Reparented nodes:", self.diff_result.reparented)

    def _print_section(self, title: str, items: List[str]):
        """Print a section of the report."""
        if items:
            self.logger.notice(title)
            for item in items:
                self.logger.warning(f"  -> {item}")

    def save_json(self, path: str, indent: int = 2) -> None:
        """Save hierarchy diff to JSON file."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.diff_result.as_dict(), f, indent=indent)
        self.logger.success(f"Saved hierarchy diff to: {path}")


class HierarchyRepair(HierarchyBase):
    """Handles hierarchy repairs with strategy pattern."""

    def __init__(self, manager: "HierarchyManager", dry_run: bool = True):
        super().__init__(manager)
        self.dry_run = dry_run
        self.strategies = {
            RepairAction.REPARENT: ReparentStrategy(),
            RepairAction.CREATE_MISSING: CreateMissingStrategy(),
            RepairAction.RENAME_AND_REPARENT: RenameAndReparentStrategy(),  # New strategy
        }

    def repair_all(self) -> None:
        """Repair all detected issues."""
        # Order matters: fuzzy matches first, then reparented, then missing
        self.repair_fuzzy_matches()
        self.repair_reparented()
        self.repair_missing()

    def repair_fuzzy_matches(self) -> None:
        """Repair fuzzy matched nodes by renaming them."""
        if not self.diff_result.fuzzy_matches:
            self.logger.success("No fuzzy matches to repair.")
            return

        self.logger.info(
            f"Repairing {len(self.diff_result.fuzzy_matches)} fuzzy matches..."
        )
        for match in self.diff_result.fuzzy_matches:
            self._repair_fuzzy_match(match)

    def repair_reparented(self) -> None:
        """Repair reparented nodes."""
        if not self.diff_result.reparented:
            self.logger.success("No reparented nodes to fix.")
            return

        self.logger.info(
            f"Repairing {len(self.diff_result.reparented)} reparented nodes..."
        )
        for name in self.diff_result.reparented:
            self._repair_node(name, RepairAction.REPARENT)

    def repair_missing(self) -> None:
        """Repair missing nodes in hierarchical order."""
        if not self.diff_result.missing:
            self.logger.success("No missing nodes to repair.")
            return

        # Sort missing paths by depth to create parents before children
        missing_sorted = sorted(self.diff_result.missing, key=lambda x: x.count("|"))

        self.logger.info(f"Creating {len(missing_sorted)} missing nodes...")
        for path in missing_sorted:
            name = path.split("|")[-1]
            # Double-check this isn't a reparented node
            if name not in self.diff_result.reparented:
                self._repair_node(name, RepairAction.CREATE_MISSING, path=path)
            else:
                self.logger.info(f"Skipping {name} - already handled as reparented")

    def _repair_fuzzy_match(self, match: Dict[str, str]):
        """Repair a single fuzzy match."""
        current_name = match["current_name"]
        self.logger.info(f"Attempting to repair fuzzy match: {current_name}")

        context = RepairContext(current_name, self.logger)
        context.current_name = current_name
        context.target_name = match["target_name"]
        context.current_path = match["current_path"]
        context.target_path = match["target_path"]

        # Add parent info if needed for reparenting
        parent_path = match["parent_path"]
        if parent_path and parent_path in self.target_map:
            context.correct_parent = self.target_map[parent_path]

        strategy = self.strategies[RepairAction.RENAME_AND_REPARENT]

        if not self.dry_run:
            success = strategy.execute(context)
            if not success:
                self.logger.error(f"Fuzzy match repair failed for {current_name}")
        else:
            description = strategy.get_description(context)
            self.logger.notice(f"[dry-run] Would {description.lower()}")

    def _repair_node(self, name: str, action: RepairAction, **kwargs):
        """Repair a single node using the specified strategy."""
        self.logger.info(f"Attempting to repair {name} with action {action}")

        context = self._build_repair_context(name, action, **kwargs)
        if not context:
            self.logger.warning(f"Could not build repair context for {name}")
            return

        strategy = self.strategies[action]

        if not self.dry_run:
            self.logger.info(f"Executing repair for {name}")
            success = strategy.execute(context)
            if not success:
                self.logger.error(f"Repair failed for {name}")
        else:
            description = strategy.get_description(context)
            self.logger.notice(f"[dry-run] Would {description.lower()}")

    def _build_repair_context(
        self, name: str, action: RepairAction, **kwargs
    ) -> Optional[RepairContext]:
        """Build repair context for the given action."""
        context = RepairContext(name, self.logger)

        if action == RepairAction.REPARENT:
            return self._build_reparent_context(context, name)
        elif action == RepairAction.CREATE_MISSING:
            return self._build_create_context(context, kwargs.get("path", ""))

        return None

    def _build_reparent_context(
        self, context: RepairContext, name: str
    ) -> Optional[RepairContext]:
        """Build context for reparenting."""
        src_path = self._find_path_for_name(self.source_map, name)
        tgt_path = self._find_path_for_name(self.target_map, name)

        if not src_path or not tgt_path:
            self.logger.error(
                f"Cannot find paths for {name}: src={src_path}, tgt={tgt_path}"
            )
            return None

        source_node = self.source_map[src_path]
        target_node = self.target_map[tgt_path]

        # Get the correct parent from the source hierarchy
        source_parent = source_node.getParent()
        if not source_parent:
            self.logger.warning(f"Source node {name} has no parent")
            return None

        # Find corresponding parent in target hierarchy
        source_parent_path = self._get_node_path(
            source_parent, self.manager.source_root
        )
        target_parent = self.target_map.get(source_parent_path)

        if not target_parent:
            self.logger.warning(f"Cannot find target parent for {name}")
            return None

        context.source_node = source_node
        context.target_node = target_node
        context.correct_parent = target_parent
        context.source_parent = source_parent
        return context

    def _build_create_context(
        self, context: RepairContext, path: str
    ) -> Optional[RepairContext]:
        """Build context for creating missing nodes."""
        parent_path = "|".join(path.split("|")[:-1])

        if not parent_path:
            parent = self.manager.target_root
        else:
            parent = self.target_map.get(parent_path)

            # If parent doesn't exist in target_map, update the map after creating nodes
            if not parent:
                # Try to refresh the target_map to pick up newly created nodes
                self._refresh_target_map()
                parent = self.target_map.get(parent_path)

        if not parent:
            self.logger.warning(
                f"  ! Missing parent for {path} (parent_path: {parent_path})"
            )
            return None

        context.parent = parent
        return context

    def _refresh_target_map(self):
        """Refresh the target map to include newly created nodes."""
        if not self.dry_run:
            # Only refresh if we're actually creating nodes
            self.manager.target_map = self.manager.diff._build_path_map(
                self.manager.target_root
            )

    def _get_node_path(self, node: pm.nt.Transform, root: pm.nt.Transform) -> str:
        """Get the path of a node relative to root."""
        path_parts = []
        current = node

        while current and current != root:
            path_parts.append(current.nodeName())
            current = current.getParent()

        if current != root:
            return ""  # Node is not under root

        path_parts.append(root.nodeName())
        return "|".join(reversed(path_parts))

    def _get_correct_parent_node(self, full_path: str) -> Optional[pm.nt.Transform]:
        """Get the correct parent node from a full path."""
        parts = full_path.split("|")
        if len(parts) <= 1:
            return None
        parent_path = "|".join(parts[:-1])
        try:
            return pm.PyNode(parent_path)
        except pm.MayaNodeError:
            return None

    def _find_path_for_name(
        self, mapping: Dict[str, pm.nt.Transform], name: str
    ) -> Optional[str]:
        """Find the path for a given node name."""
        for path, node in mapping.items():
            if node.nodeName() == name:
                return path
        return None


class HierarchyManager(ptk.LoggingMixin):
    """Main manager class for hierarchy operations."""

    def __init__(
        self,
        source_root,
        target_root,
        dry_run: bool = True,
        enable_fuzzy_matching: bool = False,
    ):
        super().__init__()
        self.source_root = source_root
        self.target_root = target_root
        self.dry_run = dry_run
        self.enable_fuzzy_matching = enable_fuzzy_matching

        # Data storage
        self.diff_result: Optional[DiffResult] = None
        self.source_map: Optional[Dict[str, pm.nt.Transform]] = None
        self.target_map: Optional[Dict[str, pm.nt.Transform]] = None

        # Components
        self.diff = HierarchyDiff(self, enable_fuzzy_matching=enable_fuzzy_matching)
        self.report = HierarchyReport(self)
        self.repair = HierarchyRepair(self, dry_run=dry_run)

    def analyze(self) -> DiffResult:
        """Analyze hierarchy differences."""
        self.diff_result = self.diff.analyze()
        return self.diff_result

    def is_valid(self) -> bool:
        """Check if hierarchy is valid."""
        if not self.diff_result:
            self.analyze()
        return self.diff_result.is_valid()

    def print_report(self) -> None:
        """Print hierarchy report."""
        if not self.diff_result:
            self.analyze()
        self.report.print_summary()

    def repair_all(self) -> None:
        """Repair all detected issues."""
        if not self.diff_result:
            self.analyze()
        self.repair.repair_all()


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.core_utils import CoreUtils

    CoreUtils.clear_scrollfield_reporters()

    source_root, target_root = pm.selected(flatten=True)
    manager = HierarchyManager(
        source_root,
        target_root,
        dry_run=0,
        enable_fuzzy_matching=0,  # Enable fuzzy matching
    )
    manager.analyze()

    manager.print_report()
    manager.repair_all()  # Will rename S00C37_BREAKER_BAR_GEO to S00C37_BREAKER_BAR_GEO1


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
