# !/usr/bin/python
# coding=utf-8
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Tuple
import pymel.core as pm
import pythontk as ptk
from pythontk.core_utils.hierarchy_utils import HierarchyMatching

# Third-party imports
from qtpy import QtCore, QtWidgets

# From mayatk package
from mayatk.env_utils.namespace_sandbox import NamespaceSandbox


class HierarchyMapBuilder:
    """Handles building hierarchy path maps for Maya transforms."""

    @staticmethod
    def build_path_map(
        root,
        exclude_namespace_prefixes: List[str] = None,
        strip_namespaces: bool = False,
    ) -> Dict[str, Any]:
        """Build a mapping of hierarchical paths to transform nodes.

        Args:
            root: "SCENE_WIDE_MODE" sentinel or a PyMEL transform root
            exclude_namespace_prefixes: namespace prefixes to exclude from traversal when scene-wide
            strip_namespaces: if True, remove namespace prefixes from each component stored
        """
        path_map: Dict[str, Any] = {}
        exclude_namespace_prefixes = exclude_namespace_prefixes or []

        def should_exclude(node) -> bool:
            try:
                nn = node.nodeName()
            except Exception:
                nn = str(node)

            # Exclude namespace prefixes
            for ns in exclude_namespace_prefixes:
                if nn.startswith(ns + ":"):
                    return True
            return False

        def traverse(node, path: str = ""):
            if should_exclude(node):
                return
            try:
                node_name = node.nodeName()
            except Exception:
                node_name = str(node)
            comp = NodeNameUtilities.format_component(node_name, strip_namespaces)
            current_path = f"{path}|{comp}" if path else comp
            path_map[current_path] = node
            for child in node.getChildren(type="transform"):
                traverse(child, current_path)

        if root == "SCENE_WIDE_MODE":
            for root_node in pm.ls(assemblies=True):
                if root_node.nodeType() == "transform":
                    traverse(root_node)
        else:
            traverse(root)
        return path_map

    @staticmethod
    def build_path_map_from_nodes(
        nodes: List[Any], strip_namespaces: bool = False
    ) -> Dict[str, Any]:
        """Build a path map starting from an arbitrary list of transform nodes.

        Root nodes are inferred as those whose parent is not in the provided node set.
        """
        path_map: Dict[str, Any] = {}
        node_set = set(nodes)

        def is_root(n) -> bool:
            try:
                p = n.getParent()
            except Exception:
                p = None
            return (p is None) or (p not in node_set)

        def traverse(node, path: str = ""):
            try:
                node_name = node.nodeName()
            except Exception:
                node_name = str(node)
            comp = NodeNameUtilities.format_component(node_name, strip_namespaces)
            current_path = f"{path}|{comp}" if path else comp
            path_map[current_path] = node
            for child in node.getChildren(type="transform"):
                if child in node_set:  # stay within imported cluster
                    traverse(child, current_path)

        for n in nodes:
            if is_root(n):
                traverse(n)
        return path_map


class TreePathMatcher(ptk.LoggingMixin):
    """Tree path matching functionality for UI tree widgets."""

    def build_tree_index(self, widget):
        """Build tree indices for fast item lookup."""
        items = list(self._iter_items(widget))

        # Build full path index using raw (namespace) names if available
        by_full = {}
        for item in items:
            raw_path = self._get_item_raw_path(item)
            if raw_path:
                existing = by_full.get(raw_path)
                if existing is None:
                    by_full[raw_path] = item
                else:  # preserve all duplicates (rare but possible)
                    if not isinstance(existing, list):
                        by_full[raw_path] = [existing]
                    by_full[raw_path].append(item)

        # Build cleaned path index with custom cleaning preserving hierarchy
        by_clean_full = {}
        for item in items:
            raw_path = self._get_item_raw_path(item)
            if raw_path:
                cleaned_path = NodeNameUtilities.clean_hierarchy_path(raw_path)
                existing = by_clean_full.get(cleaned_path)
                if existing is None:
                    by_clean_full[cleaned_path] = item
                else:  # accumulate duplicates under same cleaned representation
                    if not isinstance(existing, list):
                        by_clean_full[cleaned_path] = [existing]
                    by_clean_full[cleaned_path].append(item)

        # Build component index for last component matching
        by_last = {}
        for item in items:
            path = self._get_item_path(item)
            if path:
                last_component = HierarchyMatching._clean_namespace(path.split("|")[-1])
                by_last.setdefault(last_component, []).append(item)

        return by_full, by_clean_full, by_last

    def find_path_matches(
        self,
        target_path,
        by_full,
        by_clean_full,
        by_last,
        prefer_cleaned=False,
        strict: bool = False,
    ):
        """Find tree items matching a target path using multiple strategies."""
        cleaned_path = NodeNameUtilities.clean_hierarchy_path(target_path)
        last_clean = HierarchyMatching._clean_namespace(target_path.split("|")[-1])

        candidates = []
        strategy = "none"

        # Strategy order depends on prefer_cleaned flag
        if prefer_cleaned:
            # For reference trees that display cleaned names - use exact cleaned path match
            item = by_clean_full.get(cleaned_path)
            if item is not None:
                candidates = [item]
                strategy = "clean_full"

            if not candidates:
                item = by_full.get(target_path)
                if item is not None:
                    candidates = [item]
                    strategy = "full"
        else:
            # For current scene trees that may have exact paths
            item = by_full.get(target_path)
            if item is not None:
                candidates = [item]
                strategy = "full"

            if not candidates:
                item = by_clean_full.get(cleaned_path)
                if item is not None:
                    candidates = [item]
                    strategy = "clean_full"

        # Last component fallback - only if no exact matches found and not in strict mode
        if not strict and not candidates:
            candidates = by_last.get(last_clean, [])
            if candidates:
                strategy = "last"

        # Flatten candidates list
        if candidates:
            flat = []
            for entry in candidates:
                if isinstance(entry, (list, tuple, set)):
                    for sub in entry:
                        if sub is not None and sub not in flat:
                            flat.append(sub)
                else:
                    if entry is not None and entry not in flat:
                        flat.append(entry)
            candidates = flat

        return candidates, strategy

    def _iter_items(self, widget):
        """Iterate through all tree widget items recursively."""
        from pythontk.core_utils.hierarchy_utils import HierarchyIndexer

        stack = [widget.topLevelItem(i) for i in range(widget.topLevelItemCount())]
        while stack:
            n = stack.pop()
            if not n:
                continue
            yield n
            for ci in range(n.childCount() - 1, -1, -1):
                stack.append(n.child(ci))

    def _get_item_path(self, item) -> str:
        """Extract the full hierarchy path from a tree widget item."""
        from pythontk.core_utils.hierarchy_utils import HierarchyIndexer

        parts = []
        cur = item
        while cur:
            parts.insert(0, cur.text(0))
            cur = cur.parent()
        return HierarchyIndexer._join_hierarchy_path(parts)

    def _get_item_raw_path(self, item) -> str:
        """Extract the full hierarchy path using raw names (with namespaces) if stored."""
        from pythontk.core_utils.hierarchy_utils import HierarchyIndexer

        parts = []
        cur = item
        while cur:
            part = getattr(cur, "_raw_name", cur.text(0))
            parts.insert(0, part)
            cur = cur.parent()
        return HierarchyIndexer._join_hierarchy_path(parts)

    def log_matching_debug(self, path, candidates, strategy, prefix=""):
        """Log debug information about path matching."""
        self.logger.debug(
            f"{prefix} path '{path}' -> {len(candidates)} candidates via {strategy}"
        )

    def log_tree_index_debug(self, by_full, by_clean_full, by_last, tree_type):
        """Log debug information about tree indices."""
        self.logger.debug(
            f"{tree_type} tree index: {len(by_full)} full, {len(by_clean_full)} clean, {len(by_last)} last"
        )


class NodeNameUtilities:
    """Centralized utilities for consistent node name handling."""

    @staticmethod
    def get_clean_node_name(node) -> str:
        """Get a consistent clean node name for matching operations."""
        try:
            node_name = node.nodeName()
            if node_name:
                return node_name.split(":")[-1] if ":" in node_name else node_name
            full_path = node.fullPath()
            last_component = full_path.split("|")[-1] if "|" in full_path else str(node)
            return (
                last_component.split(":")[-1]
                if ":" in last_component
                else last_component
            )
        except Exception:
            return str(node).split(":")[-1] if ":" in str(node) else str(node)

    @staticmethod
    def get_clean_node_name_from_string(node_name: str) -> str:
        """Get a clean node name from a string path (removes namespace prefix)."""
        if not node_name:
            return ""

        # Handle hierarchical paths (with |)
        if "|" in node_name:
            last_component = node_name.split("|")[-1]
        else:
            last_component = node_name

        # Remove namespace prefix (after :)
        return (
            last_component.split(":")[-1] if ":" in last_component else last_component
        )

    @staticmethod
    def clean_hierarchy_path(path: str) -> str:
        """Clean namespace prefixes from hierarchical path components."""
        if "|" in path:
            parts = path.split("|")
            return "|".join(p.split(":")[-1] if ":" in p else p for p in parts)
        return path.split(":")[-1] if ":" in path else path

    @staticmethod
    def format_component(name: str, strip_namespaces: bool = False) -> str:
        """Format a single component name with optional namespace stripping."""
        if strip_namespaces and ":" in name:
            return name.split(":")[-1]
        return name


class MayaObjectMatcher(ptk.LoggingMixin):
    """Maya-specific object matching with fuzzy logic and container searches."""

    def __init__(self, import_manager, fuzzy_matching: bool = True):
        super().__init__()
        self.import_manager = import_manager
        self.fuzzy_matching = fuzzy_matching

    def find_matches(
        self,
        target_objects: List[str],
        imported_transforms: List,
        dry_run: bool = False,
    ) -> Tuple[List, Dict]:
        """Find matching objects using exact and fuzzy matching.

        Returns a tuple of (found_objects, fuzzy_match_map).
        """
        found_objects: List[Any] = []
        fuzzy_match_map: Dict[Any, str] = {}

        for target_name in target_objects:
            exact_matches = self._find_exact_matches(target_name, imported_transforms)
            if exact_matches:
                found_objects.extend(exact_matches)
                log_prefix = "[DRY-RUN] " if dry_run else ""
                self.logger.notice(f"{log_prefix}Exact match found: {target_name}")
                continue

            # Log debug info about why exact match failed
            self._log_debug_info(target_name, imported_transforms, dry_run)

            if self.fuzzy_matching:
                match_result = self._find_fuzzy_match(
                    target_name, imported_transforms, dry_run
                )
                if match_result:
                    matching_node, fuzzy_target_name = match_result
                    found_objects.append(matching_node)
                    fuzzy_match_map[matching_node] = fuzzy_target_name

        return found_objects, fuzzy_match_map

    def _find_exact_matches(self, target_name: str, imported_transforms: List) -> List:
        """Find exact name matches using consistent name extraction."""
        return [
            node
            for node in imported_transforms
            if NodeNameUtilities.get_clean_node_name(node) == target_name
        ]

    def _find_fuzzy_match(
        self, target_name: str, imported_transforms: List, dry_run: bool = False
    ) -> Optional[Tuple[Any, str]]:
        """Find fuzzy match for target object using consistent name extraction."""
        if pm.objExists(target_name):
            log_prefix = "[DRY-RUN] " if dry_run else ""
            self.logger.debug(
                f"{log_prefix}Target '{target_name}' exists in current scene - will attempt fuzzy match for replacement"
            )

        # Get clean names for fuzzy matching using consistent extraction
        import_names = [
            NodeNameUtilities.get_clean_node_name(node) for node in imported_transforms
        ]

        # Try fuzzy matching with standard threshold
        matches = ptk.FuzzyMatcher.find_all_matches(
            [target_name], import_names, score_threshold=0.7
        )

        log_prefix = "[DRY-RUN] " if dry_run else ""
        self.logger.debug(
            f"{log_prefix}Fuzzy matching for '{target_name}' with threshold 0.7: {len(matches)} matches found"
        )

        if matches:
            target, matched_name, score = matches[0]
            for node in imported_transforms:
                if NodeNameUtilities.get_clean_node_name(node) == matched_name:
                    self.logger.notice(
                        f"{log_prefix}Fuzzy match: '{target_name}' -> '{matched_name}' (score: {score:.2f})"
                    )
                    return node, target_name

        return None

    def _log_debug_info(
        self, target_name: str, imported_transforms: List, dry_run: bool = False
    ):
        """Log debug information for matching process."""
        log_prefix = "[DRY-RUN] " if dry_run else ""
        import_names = [
            NodeNameUtilities.get_clean_node_name(node) for node in imported_transforms
        ]
        self.logger.debug(
            f"{log_prefix}No exact match for '{target_name}' in imported objects: {import_names}"
        )


class NodeFilterUtilities:
    """Centralized filtering utilities for Maya nodes."""

    # Maya default cameras that should be excluded from analysis
    MAYA_DEFAULT_CAMERAS = {"persp", "top", "front", "side"}

    @staticmethod
    def is_default_maya_camera(path: str, node) -> bool:
        """Check if node represents a Maya default camera."""
        try:
            base_name = path.split("|")[-1].split(":")[-1]
            if base_name not in NodeFilterUtilities.MAYA_DEFAULT_CAMERAS:
                return False
            shapes = node.getShapes()
            for shape in shapes:
                try:
                    if pm.nodeType(shape) == "camera":
                        return True
                except Exception:
                    continue
            return False
        except Exception:
            return False

    @staticmethod
    def should_keep_node_by_type(
        node, node_types: List[str], exclude: bool = True
    ) -> bool:
        """Filter nodes by shape types."""
        try:
            shapes = node.getShapes()
            if not shapes:
                return True  # Keep transform-only nodes

            shape_types = [shape.nodeType() for shape in shapes]
            has_filtered_type = any(t in shape_types for t in node_types)
            return not has_filtered_type if exclude else has_filtered_type
        except:
            return True

    @staticmethod
    def filter_path_map_by_cameras(path_map: Dict[str, Any]) -> Dict[str, Any]:
        """Remove Maya default cameras from path map."""
        return {
            path: node
            for path, node in path_map.items()
            if not NodeFilterUtilities.is_default_maya_camera(path, node)
        }

    @staticmethod
    def filter_path_map_by_types(
        path_map: Dict[str, Any], node_types: List[str], exclude: bool = True
    ) -> Dict[str, Any]:
        """Filter path map by node types."""
        return {
            path: node
            for path, node in path_map.items()
            if NodeFilterUtilities.should_keep_node_by_type(node, node_types, exclude)
        }


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

        if not objects:
            self.logger.error(f"No objects specified for {operation} operation")
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


class HierarchyManager(ptk.LoggingMixin):
    """Core hierarchy analysis and repair manager."""

    def __init__(
        self,
        import_manager: Optional[NamespaceSandbox] = None,
        fuzzy_matching: bool = True,
        dry_run: bool = True,
    ):
        super().__init__()
        self.dry_run = dry_run
        self.fuzzy_matching = fuzzy_matching

        # Initialize managers
        self.import_manager = import_manager
        self.validation_manager = ValidationManager(dry_run)
        self.matcher = MayaObjectMatcher(import_manager, fuzzy_matching)

        # Initialize state
        self.current_scene_path_map = {}
        self.reference_scene_path_map = {}
        self.differences = {}
        self.missing_objects = []
        self.extra_objects = []

    def analyze_hierarchies(
        self,
        current_tree_root=None,
        reference_tree_root=None,
        reference_objects: List = None,
        filter_meshes: bool = True,
        filter_cameras: bool = False,
        filter_lights: bool = False,
        inc_names: Optional[List[str]] = None,
        exc_names: Optional[List[str]] = None,
        inc_types: Optional[List[str]] = None,
        exc_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Analyze differences between current and reference hierarchies."""
        try:
            ref_namespaces: List[str] = []
            if reference_objects:
                # Derive namespaces from imported reference objects
                ref_namespaces = sorted(
                    {
                        n.nodeName().split(":")[0]
                        for n in reference_objects
                        if ":" in n.nodeName()
                    }
                )
                if ref_namespaces:
                    self.logger.debug(
                        f"Reference namespaces detected: {', '.join(ref_namespaces)}"
                    )

            # Build current scene path map EXCLUDING reference namespaces (raw, keep namespaces)
            self.current_scene_path_map = HierarchyMapBuilder.build_path_map(
                current_tree_root or "SCENE_WIDE_MODE",
                exclude_namespace_prefixes=ref_namespaces,
                strip_namespaces=False,
            )

            current_sample = list(self.current_scene_path_map.keys())[:3]
            self.logger.info(
                f"Current scene path map (excluding ref namespaces): {len(self.current_scene_path_map)} paths (sample: {current_sample})"
            )

            # Build reference map strictly from imported reference objects (keeps namespaces)
            if reference_objects:
                self.reference_scene_path_map = (
                    HierarchyMapBuilder.build_path_map_from_nodes(
                        reference_objects, strip_namespaces=False
                    )
                )
            else:
                # Fallback to scene-wide if no reference provided
                self.reference_scene_path_map = HierarchyMapBuilder.build_path_map(
                    reference_tree_root or "SCENE_WIDE_MODE",
                    strip_namespaces=False,
                )

            reference_sample = list(self.reference_scene_path_map.keys())[:3]
            self.logger.info(
                f"Reference path map (raw): {len(self.reference_scene_path_map)} paths (sample: {reference_sample})"
            )

            # Filter out Maya default cameras from BOTH maps (current map is rebuilt scene-wide and may still contain them)
            try:
                self.current_scene_path_map = (
                    NodeFilterUtilities.filter_path_map_by_cameras(
                        self.current_scene_path_map
                    )
                )
                self.reference_scene_path_map = (
                    NodeFilterUtilities.filter_path_map_by_cameras(
                        self.reference_scene_path_map
                    )
                )
                self.logger.debug("Default camera filtering applied to both path maps")
            except Exception as cam_filt_err:
                self.logger.debug(f"Camera filtering skipped/failed: {cam_filt_err}")

            # Apply other filters (meshes, lights etc.)
            if filter_meshes:
                self.current_scene_path_map = (
                    NodeFilterUtilities.filter_path_map_by_types(
                        self.current_scene_path_map, ["mesh"], exclude=True
                    )
                )
                self.reference_scene_path_map = (
                    NodeFilterUtilities.filter_path_map_by_types(
                        self.reference_scene_path_map, ["mesh"], exclude=True
                    )
                )
            if filter_cameras:
                self.current_scene_path_map = (
                    NodeFilterUtilities.filter_path_map_by_types(
                        self.current_scene_path_map, ["camera"], exclude=True
                    )
                )
                self.reference_scene_path_map = (
                    NodeFilterUtilities.filter_path_map_by_types(
                        self.reference_scene_path_map, ["camera"], exclude=True
                    )
                )
            if filter_lights:
                self.current_scene_path_map = (
                    NodeFilterUtilities.filter_path_map_by_types(
                        self.current_scene_path_map, ["light"], exclude=True
                    )
                )
                self.reference_scene_path_map = (
                    NodeFilterUtilities.filter_path_map_by_types(
                        self.reference_scene_path_map, ["light"], exclude=True
                    )
                )

            # Prepare cleaned versions (strip namespaces per component) for comparison
            current_paths_raw = set(self.current_scene_path_map.keys())
            reference_paths_raw = set(self.reference_scene_path_map.keys())

            cleaned_current_paths = {
                NodeNameUtilities.clean_hierarchy_path(p) for p in current_paths_raw
            }
            cleaned_reference_paths = {
                NodeNameUtilities.clean_hierarchy_path(p) for p in reference_paths_raw
            }

            # Differences
            # Simple 1:1 cleaned path comparison (strict). Fuzzy handling occurs elsewhere.
            self.missing_objects = sorted(
                cleaned_reference_paths - cleaned_current_paths
            )
            self.extra_objects = sorted(cleaned_current_paths - cleaned_reference_paths)

            # Optional name pattern filtering (shell-style) using pythontk filter_list util
            if inc_names or exc_names:
                try:
                    patterns_inc = [p for p in (inc_names or []) if p]
                    patterns_exc = [p for p in (exc_names or []) if p]
                    if patterns_inc or patterns_exc:
                        before_missing = len(self.missing_objects)
                        before_extra = len(self.extra_objects)
                        self.missing_objects = ptk.filter_list(
                            self.missing_objects,
                            inc=patterns_inc or None,
                            exc=patterns_exc or None,
                        )
                        self.extra_objects = ptk.filter_list(
                            self.extra_objects,
                            inc=patterns_inc or None,
                            exc=patterns_exc or None,
                        )
                        self.logger.debug(
                            f"Applied name filters inc={patterns_inc} exc={patterns_exc} -> missing {before_missing}->{len(self.missing_objects)} extra {before_extra}->{len(self.extra_objects)}"
                        )
                except Exception as filt_err:
                    self.logger.warning(f"Name filtering failed: {filt_err}")

            # Optional type-based filtering (operates on diff lists)
            if inc_types or exc_types:
                try:
                    type_inc = [t for t in (inc_types or []) if t]
                    type_exc = [t for t in (exc_types or []) if t]
                    if type_inc or type_exc:
                        # Build cleaned-path -> shape type set maps for both sides
                        def build_type_map(path_map):
                            result = {}
                            for raw_path, node in path_map.items():
                                cleaned = NodeNameUtilities.clean_hierarchy_path(
                                    raw_path
                                )
                                try:
                                    shapes = node.getShapes()
                                    if shapes:
                                        stypes = sorted({s.nodeType() for s in shapes})
                                    else:
                                        stypes = ["transform"]
                                except Exception:
                                    stypes = ["unknown"]
                                if cleaned in result:
                                    # merge (handles namespace-collapsed duplicates)
                                    result[cleaned].update(stypes)
                                else:
                                    result[cleaned] = set(stypes)
                            return {k: sorted(v) for k, v in result.items()}

                        current_type_map = build_type_map(self.current_scene_path_map)
                        reference_type_map = build_type_map(
                            self.reference_scene_path_map
                        )

                        def map_missing(p):
                            return ",".join(reference_type_map.get(p, []))

                        def map_extra(p):
                            return ",".join(current_type_map.get(p, []))

                        before_missing_t = len(self.missing_objects)
                        before_extra_t = len(self.extra_objects)
                        self.missing_objects = ptk.filter_list(
                            self.missing_objects,
                            inc=type_inc or None,
                            exc=type_exc or None,
                            map_func=map_missing,
                            check_unmapped=True,
                        )
                        self.extra_objects = ptk.filter_list(
                            self.extra_objects,
                            inc=type_inc or None,
                            exc=type_exc or None,
                            map_func=map_extra,
                            check_unmapped=True,
                        )
                        self.logger.debug(
                            f"Applied type filters inc={type_inc} exc={type_exc} -> missing {before_missing_t}->{len(self.missing_objects)} extra {before_extra_t}->{len(self.extra_objects)}"
                        )
                except Exception as type_filt_err:
                    self.logger.warning(f"Type filtering failed: {type_filt_err}")

            self.logger.info(
                f"Path comparison (cleaned): {len(cleaned_current_paths)} current vs {len(cleaned_reference_paths)} reference"
            )
            self.logger.info(
                f"Differences -> missing: {len(self.missing_objects)}, extra: {len(self.extra_objects)}"
            )

            # Debug final differences
            self.logger.debug(
                f"[RESULT] Missing objects (sample): {self.missing_objects[:5]}"
            )
            self.logger.debug(
                f"[RESULT] Extra objects (sample): {self.extra_objects[:5]}"
            )

            # (No special-case diagnostics; uniform treatment of names.)

            # Build detailed differences
            self.differences = {
                "missing": self.missing_objects,
                "extra": self.extra_objects,
                "total_missing": len(self.missing_objects),
                "total_extra": len(self.extra_objects),
            }

            return self.differences

        except Exception as e:
            self.logger.error(f"Failed to analyze hierarchies: {e}")
            return {}


class TreeWidgetUtilities(ptk.LoggingMixin):
    """Centralized utilities for tree widget operations."""

    @staticmethod
    def get_selected_object_names(tree_widget) -> List[str]:
        """Extract object names from selected tree widget items."""
        selected_objects = []
        for item in TreeWidgetUtilities.get_selected_tree_items(tree_widget):
            object_name = TreeWidgetUtilities._extract_object_name_from_item(item)
            if object_name:
                selected_objects.append(object_name)
        return selected_objects

    @staticmethod
    def get_selected_tree_items(tree_widget):
        """Get all selected items from tree widget."""
        selected_items = []
        iterator = QtWidgets.QTreeWidgetItemIterator(tree_widget)
        while iterator.value():
            item = iterator.value()
            if item.isSelected():
                selected_items.append(item)
            iterator += 1
        return selected_items

    @staticmethod
    def _extract_object_name_from_item(item) -> str:
        """Extract Maya object name from tree widget item."""
        # Check for stored raw name first (with namespace)
        raw_name = getattr(item, "_raw_name", None)
        if raw_name:
            return raw_name

        # Build path from tree hierarchy
        parts = []
        current = item
        while current:
            parts.insert(0, current.text(0))
            current = current.parent()

        return "|".join(parts) if len(parts) > 1 else parts[0] if parts else ""

    @staticmethod
    def find_tree_item_by_name(tree_widget, object_name: str):
        """Find tree widget item by object name."""
        iterator = QtWidgets.QTreeWidgetItemIterator(tree_widget)
        while iterator.value():
            item = iterator.value()
            if TreeWidgetUtilities._extract_object_name_from_item(item) == object_name:
                return item
            iterator += 1
        return None

    @staticmethod
    def build_hierarchy_structure(objects: List) -> Tuple[Dict[str, Dict], List[str]]:
        """Build hierarchical structure from Maya transform objects.

        Returns:
            Tuple of (object_items_dict, root_objects_list) to match original API
        """
        object_items = {}  # obj_name -> dict with object info
        root_objects = []  # Objects with no parent

        for obj in objects:
            try:
                obj_name = obj.nodeName()
                obj_type = obj.type()
                parent = obj.getParent()

                # Store object info for later use
                object_items[obj_name] = {
                    "object": obj,
                    "type": obj_type,
                    "parent": parent.nodeName() if parent else None,
                    "item": None,  # Will be created in second pass
                }

                # Track root objects (no parent)
                if not parent:
                    root_objects.append(obj_name)

            except Exception:
                continue

        return object_items, root_objects


class MayaSelectionUtilities(ptk.LoggingMixin):
    """Utilities for Maya object selection operations."""

    @staticmethod
    def select_objects_in_maya(object_names: List[str]) -> int:
        """Select objects in Maya scene by name."""
        import pymel.core as pm

        if not object_names:
            return 0

        valid_objects = []
        for name in object_names:
            if pm.objExists(name):
                valid_objects.append(name)

        if valid_objects:
            pm.select(valid_objects, replace=True)
            return len(valid_objects)

        return 0


class ObjectSwapper(ptk.LoggingMixin):
    """Handles cross-scene object operations like push/pull."""

    def __init__(
        self,
        import_manager: Optional[NamespaceSandbox] = None,
        fuzzy_matching: bool = True,
        dry_run: bool = True,
        pull_mode: str = "Add to Scene",
        pull_children: bool = False,
    ):
        super().__init__()
        self.dry_run = dry_run
        self.fuzzy_matching = fuzzy_matching
        self.pull_mode = pull_mode
        self.pull_children = pull_children
        self.import_manager = import_manager or NamespaceSandbox(dry_run=dry_run)

        # Initialize managers
        self.validation_manager = ValidationManager(dry_run)
        self.matcher = MayaObjectMatcher(self.import_manager, fuzzy_matching)

    def push_objects_to_scene(
        self,
        target_objects: List[str],
        target_file: Union[str, Path],
        backup: bool = True,
    ) -> bool:
        """Push objects from current scene to target scene."""
        target_file = Path(target_file)

        if not self.validation_manager.validate_inputs(
            target_objects, target_file, "push"
        ):
            return False

        if backup:
            self.validation_manager.create_backup(target_file)

        return self.pull_objects_from_scene(target_objects, target_file, backup)

    def pull_objects_from_scene(
        self,
        target_objects: List[str],
        source_file: Union[str, Path],
        backup: bool = True,
    ) -> bool:
        """Pull objects from source scene into current scene."""
        source_file = Path(source_file)

        if not self.validation_manager.validate_inputs(
            target_objects, source_file, "pull"
        ):
            return False

        if backup:
            self.validation_manager.create_backup()

        try:
            # Import the source scene - use complete import for user pulls to ensure objects are available
            import_result = self.import_manager.import_with_namespace(
                str(source_file), force_complete_import=True
            )

            if not import_result or not import_result.get("transforms"):
                self.logger.error("No transforms imported from source scene")
                return False

            imported_transforms = import_result["transforms"]

            # Clean namespace from target object names to match against cleaned imported names
            cleaned_target_objects = [
                NodeNameUtilities.get_clean_node_name_from_string(obj)
                for obj in target_objects
            ]

            # Find matching objects
            found_objects, fuzzy_match_map = self.matcher.find_matches(
                cleaned_target_objects, imported_transforms, self.dry_run
            )

            if not found_objects:
                self.logger.warning("No matching objects found in source scene")
                return False

            # Process found objects
            if not self.dry_run:
                self._process_found_objects(found_objects, fuzzy_match_map)
            else:
                self.logger.info(
                    f"[DRY-RUN] Would process {len(found_objects)} objects"
                )

            return True

        except Exception as e:
            self.logger.error(f"Failed to pull objects: {e}")
            return False

    def _process_found_objects(self, found_objects: List, fuzzy_match_map: Dict):
        """Process and integrate found objects into current scene based on pull mode."""

        self.logger.info(
            f"[DEBUG] _process_found_objects called with pull_children={self.pull_children}"
        )
        self.logger.info(f"[DEBUG] Found {len(found_objects)} objects to process")

        # Log the names of found objects for debugging
        for i, obj in enumerate(found_objects[:5]):  # Show first 5
            try:
                obj_name = obj.nodeName() if hasattr(obj, "nodeName") else str(obj)
                self.logger.info(f"[DEBUG] Found object [{i}]: {obj_name}")
            except:
                self.logger.info(f"[DEBUG] Found object [{i}]: <name unavailable>")

        # When pull_children is enabled, filter to only root objects to avoid processing
        # hierarchies multiple times. Root objects will naturally include their children.
        if self.pull_children:
            self.logger.info(
                "[DEBUG] Pull children is ENABLED - filtering to root objects"
            )
            # Filter to root objects only (objects that are not children of other selected objects)
            root_objects = self._filter_to_root_objects(found_objects)
            self.logger.info(
                f"[DEBUG] Filtered {len(found_objects)} objects to {len(root_objects)} root objects for hierarchy pulling"
            )

            # Log the root objects for debugging
            for i, obj in enumerate(root_objects):
                try:
                    obj_name = obj.nodeName() if hasattr(obj, "nodeName") else str(obj)
                    self.logger.info(f"[DEBUG] Root object [{i}]: {obj_name}")
                except:
                    self.logger.info(f"[DEBUG] Root object [{i}]: <name unavailable>")

            objects_to_process = root_objects
        else:
            self.logger.info(
                "[DEBUG] Pull children is DISABLED - processing individual objects"
            )
            # Process individual objects without their children
            objects_to_process = found_objects

        self.logger.info(f"[DEBUG] Processing {len(objects_to_process)} objects")

        for i, obj in enumerate(objects_to_process):
            try:
                # Check if object still exists before processing
                if not obj.exists():
                    self.logger.warning(f"Object {obj} no longer exists, skipping")
                    continue

                clean_name = NodeNameUtilities.get_clean_node_name(obj)
                self.logger.info(
                    f"[DEBUG] Processing object [{i}]: {clean_name} (pull_mode={self.pull_mode})"
                )

                if self.pull_mode == "Merge Hierarchies":
                    # Merge Hierarchies: preserve parent hierarchy structure
                    if self.pull_children:
                        self.logger.info(
                            f"[DEBUG] Calling _process_with_hierarchy_and_children for {clean_name}"
                        )
                        self._process_with_hierarchy_and_children(obj, clean_name)
                    else:
                        self.logger.info(
                            f"[DEBUG] Calling _process_with_hierarchy for {clean_name}"
                        )
                        self._process_with_hierarchy(obj, clean_name)
                else:
                    # Add to Scene: add object to scene, maintaining hierarchy if pull_children=True
                    if self.pull_children:
                        self.logger.info(
                            f"[DEBUG] Calling _process_with_hierarchy_non_destructive_and_children for {clean_name}"
                        )
                        self._process_with_hierarchy_non_destructive_and_children(
                            obj, clean_name
                        )
                    else:
                        self.logger.info(
                            f"[DEBUG] Calling _process_as_root_object for {clean_name}"
                        )
                        self._process_as_root_object(obj, clean_name)

                self.logger.info(f"[DEBUG] Successfully processed object: {clean_name}")

            except Exception as e:
                self.logger.error(f"Failed to process object {obj}: {e}")
                import traceback

                self.logger.debug(f"Full traceback: {traceback.format_exc()}")

    def _filter_to_root_objects(self, objects: List) -> List:
        """Filter objects to only include roots (objects that are not children of other selected objects)."""
        root_objects = []
        object_paths = set()

        # Build set of all object paths for quick lookup
        for obj in objects:
            try:
                object_paths.add(obj.fullPath())
            except:
                continue

        # Check each object to see if it's a root (no parent in the selected set)
        for obj in objects:
            try:
                obj_path = obj.fullPath()
                is_root = True

                # Check if any parent of this object is also in the selected set
                current = obj.getParent()
                while current and is_root:
                    parent_path = current.fullPath()
                    if parent_path in object_paths:
                        is_root = (
                            False  # This object is a child of another selected object
                        )
                        break
                    current = current.getParent()

                if is_root:
                    root_objects.append(obj)

            except Exception as e:
                self.logger.debug(f"Error checking if {obj} is root object: {e}")
                # If we can't determine, include it as a root to be safe
                root_objects.append(obj)

        return root_objects

    def _expand_objects_with_children(self, objects: List) -> List:
        """Expand object list to include all children when pull_children is enabled."""
        expanded_objects = []
        processed_objects = set()

        for obj in objects:
            self._collect_object_and_children(obj, expanded_objects, processed_objects)

        return expanded_objects

    def _collect_object_and_children(self, obj, result_list: List, processed_set: set):
        """Recursively collect an object and all its transform children."""
        try:
            # Check if object still exists before processing
            if not obj.exists():
                self.logger.debug(f"Object {obj} no longer exists, skipping")
                return

            obj_name = obj.fullPath()
            if obj_name in processed_set:
                return

            processed_set.add(obj_name)
            result_list.append(obj)

            # Get all transform children
            children = obj.getChildren(type="transform")
            for child in children:
                self._collect_object_and_children(child, result_list, processed_set)

        except Exception as e:
            self.logger.debug(f"Error collecting children for {obj}: {e}")

    def _process_with_hierarchy_and_children(self, obj, clean_name: str):
        """Process object and all its children preserving hierarchy for Merge mode."""
        try:
            # Get the complete hierarchy for this root object
            all_objects = []
            self._collect_object_and_children(obj, all_objects, set())

            self.logger.info(
                f"Processing hierarchy root '{clean_name}' with {len(all_objects)} total objects for Merge mode"
            )

            # For Merge Hierarchies mode with pull_children:
            # IMPORTANT: Don't delete existing objects from previous pull operations
            # Instead, create a unique name for the new object if there's a conflict
            if pm.objExists(clean_name):
                existing_obj = pm.PyNode(clean_name)
                # Check if the existing object is at world level (from "Add to Scene")
                parent = existing_obj.getParent()
                if parent is None:  # Object is at world level
                    self.logger.info(
                        f"Existing object '{clean_name}' from previous pull found at world level - preserving it"
                    )
                    # Create a unique name for the new merged hierarchy object
                    counter = 1
                    unique_name = f"{clean_name}_{counter}"
                    while pm.objExists(unique_name):
                        counter += 1
                        unique_name = f"{clean_name}_{counter}"
                    self.logger.info(
                        f"Using unique name '{unique_name}' for merged hierarchy"
                    )
                    clean_name = unique_name
                else:
                    # Object is part of another hierarchy, safe to replace
                    self.logger.info(f"Replacing existing hierarchy root: {clean_name}")
                    pm.delete(existing_obj)

            # Process the root object to establish hierarchy
            self._process_with_hierarchy_merge_root_only(obj, clean_name)

            self.logger.debug(f"Successfully merged hierarchy root: {obj.nodeName()}")

        except Exception as e:
            self.logger.warning(f"Failed to process hierarchy for {clean_name}: {e}")
            # Fallback to processing just the root object
            self._process_with_hierarchy(obj, clean_name)

    def _process_with_hierarchy_merge_root_only(self, obj, clean_name: str):
        """Process root object preserving hierarchy for Merge mode without aggressive deletion."""
        try:
            self.logger.info(
                f"[DEBUG] _process_with_hierarchy_merge_root_only called for {clean_name}"
            )

            # Get the full path of the object in the imported scene
            original_path = obj.fullPath()
            path_components = original_path.split("|")

            # Remove namespace from each component
            clean_path_components = []
            for component in path_components:
                if component:  # Skip empty components
                    clean_component = NodeNameUtilities.get_clean_node_name_from_string(
                        component
                    )
                    clean_path_components.append(clean_component)

            # Build the hierarchy in current scene (similar to non-destructive but for root only)
            current_parent = None
            for i, component_name in enumerate(
                clean_path_components[:-1]
            ):  # Exclude the object itself
                if not pm.objExists(component_name):
                    # Create parent transform if it doesn't exist
                    parent_obj = pm.createNode("transform", name=component_name)
                    if current_parent:
                        pm.parent(parent_obj, current_parent)
                    current_parent = parent_obj
                else:
                    current_parent = pm.PyNode(component_name)

            # Now parent the object to its proper parent (or root if no parents)
            if current_parent:
                pm.parent(obj, current_parent)
            else:
                pm.parent(obj, world=True)

            # CRITICAL FIX: Remove namespace from the entire hierarchy
            # When we pull a hierarchy, we need to remove the temp namespace from ALL objects
            if ":" in obj.nodeName():
                self.logger.info(
                    f"[DEBUG] Removing namespace from entire hierarchy under {obj.nodeName()}"
                )
                # For Merge Hierarchies mode: Use manual conflict resolution with _1, _2 suffixes
                self._remove_namespace_from_hierarchy(obj, allow_maya_auto_rename=False)
                # Also remove namespace from materials and shading engines
                self._remove_namespace_from_materials(obj, allow_maya_auto_rename=False)

            # Rename root if needed (after namespace removal)
            current_name = obj.nodeName()
            if current_name != clean_name:
                self.logger.info(f"[DEBUG] Renaming {current_name} to {clean_name}")
                obj.rename(clean_name)

        except Exception as e:
            self.logger.warning(
                f"Failed to preserve hierarchy for {clean_name}, falling back to root: {e}"
            )
            import traceback

            self.logger.debug(f"Full traceback: {traceback.format_exc()}")
            self._process_as_root_object(obj, clean_name)

    def _remove_namespace_from_hierarchy(self, root_obj, allow_maya_auto_rename=False):
        """Remove namespace from an entire hierarchy of objects.

        Args:
            root_obj: The root object of the hierarchy
            allow_maya_auto_rename: If True, let Maya handle naming conflicts automatically.
                                   If False, use manual _1, _2, etc. suffixes.
        """
        try:
            # Get all descendants of this object
            all_objects = []
            self._collect_object_and_children(root_obj, all_objects, set())

            self.logger.info(
                f"[DEBUG] Removing namespace from {len(all_objects)} objects in hierarchy (maya_auto_rename={allow_maya_auto_rename})"
            )

            # Process from bottom up (children first) to avoid parenting issues
            all_objects.reverse()

            for obj in all_objects:
                try:
                    current_name = obj.nodeName()
                    if ":" in current_name:
                        # Remove namespace prefix
                        clean_name = current_name.split(":")[-1]

                        if allow_maya_auto_rename:
                            # For "Add to Scene" mode: Let Maya handle naming automatically
                            # Maya will automatically add suffixes like INTERACTIVE1, INTERACTIVE2, etc.
                            try:
                                obj.rename(clean_name)
                                final_name = obj.nodeName()
                                if final_name != clean_name:
                                    self.logger.debug(
                                        f"[DEBUG] Maya auto-renamed {current_name} to {final_name}"
                                    )
                                else:
                                    self.logger.debug(
                                        f"[DEBUG] Renamed {current_name} to {clean_name}"
                                    )
                            except Exception as maya_rename_error:
                                self.logger.debug(
                                    f"Maya auto-rename failed for {current_name}: {maya_rename_error}"
                                )
                        else:
                            # For "Merge Hierarchies" mode: Use manual conflict resolution
                            # Only rename if the clean name doesn't already exist
                            if (
                                not pm.objExists(clean_name)
                                or pm.PyNode(clean_name) == obj
                            ):
                                obj.rename(clean_name)
                                self.logger.debug(
                                    f"[DEBUG] Renamed {current_name} to {clean_name}"
                                )
                            else:
                                # Find a unique name with _1, _2, etc.
                                counter = 1
                                unique_name = f"{clean_name}_{counter}"
                                while pm.objExists(unique_name):
                                    counter += 1
                                    unique_name = f"{clean_name}_{counter}"
                                obj.rename(unique_name)
                                self.logger.debug(
                                    f"[DEBUG] Renamed {current_name} to {unique_name} (conflict resolved)"
                                )
                except Exception as rename_error:
                    self.logger.debug(f"Could not rename {obj}: {rename_error}")

        except Exception as e:
            self.logger.warning(f"Failed to remove namespace from hierarchy: {e}")

    def _remove_namespace_from_materials(self, root_obj, allow_maya_auto_rename=False):
        """Remove namespace from materials and shading engines connected to a hierarchy.

        Args:
            root_obj: The root object of the hierarchy
            allow_maya_auto_rename: If True, let Maya handle naming conflicts automatically.
        """
        try:
            # Get all descendants of this object
            all_objects = []
            self._collect_object_and_children(root_obj, all_objects, set())

            # Collect all materials and shading engines from this hierarchy
            materials_to_process = set()
            shading_engines_to_process = set()

            for obj in all_objects:
                try:
                    # Get all shapes under this object
                    shapes = obj.getShapes(allDescendents=True)

                    for shape in shapes:
                        try:
                            # Get shading engines connected to this shape
                            shading_groups = shape.outputs(type="shadingEngine")
                            for sg in shading_groups:
                                sg_name = sg.nodeName()
                                if ":" in sg_name and sg_name not in [
                                    "initialShadingGroup",
                                    "initialParticleSE",
                                ]:
                                    shading_engines_to_process.add(sg)

                                    # Get materials connected to this shading engine
                                    materials = []
                                    if sg.surfaceShader.inputs():
                                        materials.extend(sg.surfaceShader.inputs())
                                    if sg.displacementShader.inputs():
                                        materials.extend(sg.displacementShader.inputs())
                                    if sg.volumeShader.inputs():
                                        materials.extend(sg.volumeShader.inputs())

                                    for mat in materials:
                                        if mat and hasattr(mat, "nodeType"):
                                            mat_name = mat.nodeName()
                                            if ":" in mat_name and mat_name not in [
                                                "lambert1",
                                                "particleCloud1",
                                                "shaderGlow1",
                                            ]:
                                                materials_to_process.add(mat)

                                                # Also get textures and utility nodes
                                                try:
                                                    connected_nodes = mat.inputs()
                                                    for node in connected_nodes:
                                                        if node and hasattr(
                                                            node, "nodeType"
                                                        ):
                                                            node_name = node.nodeName()
                                                            if ":" in node_name:
                                                                materials_to_process.add(
                                                                    node
                                                                )
                                                except Exception:
                                                    pass

                        except Exception as e:
                            self.logger.debug(
                                f"Could not process materials for shape {shape}: {e}"
                            )

                except Exception as e:
                    self.logger.debug(
                        f"Could not process object {obj} for materials: {e}"
                    )

            self.logger.debug(
                f"Found {len(materials_to_process)} materials and {len(shading_engines_to_process)} shading engines with namespaces"
            )

            # Process materials first
            for material in materials_to_process:
                try:
                    current_name = material.nodeName()
                    if ":" in current_name:
                        clean_name = current_name.split(":")[-1]

                        if allow_maya_auto_rename:
                            try:
                                material.rename(clean_name)
                                final_name = material.nodeName()
                                self.logger.debug(
                                    f"[DEBUG] Renamed material {current_name} to {final_name}"
                                )
                            except Exception as e:
                                self.logger.debug(
                                    f"Could not rename material {current_name}: {e}"
                                )
                        else:
                            # Manual conflict resolution for materials
                            if not pm.objExists(clean_name):
                                material.rename(clean_name)
                                self.logger.debug(
                                    f"[DEBUG] Renamed material {current_name} to {clean_name}"
                                )
                            else:
                                counter = 1
                                unique_name = f"{clean_name}_{counter}"
                                while pm.objExists(unique_name):
                                    counter += 1
                                    unique_name = f"{clean_name}_{counter}"
                                material.rename(unique_name)
                                self.logger.debug(
                                    f"[DEBUG] Renamed material {current_name} to {unique_name}"
                                )

                except Exception as e:
                    self.logger.debug(f"Could not rename material {material}: {e}")

            # Process shading engines
            for sg in shading_engines_to_process:
                try:
                    current_name = sg.nodeName()
                    if ":" in current_name:
                        clean_name = current_name.split(":")[-1]

                        if allow_maya_auto_rename:
                            try:
                                sg.rename(clean_name)
                                final_name = sg.nodeName()
                                self.logger.debug(
                                    f"[DEBUG] Renamed shading engine {current_name} to {final_name}"
                                )
                            except Exception as e:
                                self.logger.debug(
                                    f"Could not rename shading engine {current_name}: {e}"
                                )
                        else:
                            # Manual conflict resolution for shading engines
                            if not pm.objExists(clean_name):
                                sg.rename(clean_name)
                                self.logger.debug(
                                    f"[DEBUG] Renamed shading engine {current_name} to {clean_name}"
                                )
                            else:
                                counter = 1
                                unique_name = f"{clean_name}_{counter}"
                                while pm.objExists(unique_name):
                                    counter += 1
                                    unique_name = f"{clean_name}_{counter}"
                                sg.rename(unique_name)
                                self.logger.debug(
                                    f"[DEBUG] Renamed shading engine {current_name} to {unique_name}"
                                )

                except Exception as e:
                    self.logger.debug(f"Could not rename shading engine {sg}: {e}")

        except Exception as e:
            self.logger.warning(f"Failed to remove namespace from materials: {e}")

    def _process_with_hierarchy_non_destructive_and_children(
        self, obj, clean_name: str
    ):
        """Process object and all its children preserving hierarchy for Add to Scene mode."""
        try:
            self.logger.info(
                f"[DEBUG] _process_with_hierarchy_non_destructive_and_children called for {clean_name}"
            )

            # Get the complete hierarchy for this root object
            all_objects = []
            self._collect_object_and_children(obj, all_objects, set())

            self.logger.info(
                f"[DEBUG] Processing hierarchy root '{clean_name}' with {len(all_objects)} total objects for Add to Scene mode"
            )

            # Log a few child names for debugging
            for i, child_obj in enumerate(all_objects[:5]):  # Show first 5
                try:
                    child_name = (
                        child_obj.nodeName()
                        if hasattr(child_obj, "nodeName")
                        else str(child_obj)
                    )
                    self.logger.info(f"[DEBUG] Child object [{i}]: {child_name}")
                except:
                    self.logger.info(f"[DEBUG] Child object [{i}]: <name unavailable>")

            # For Add to Scene mode, just parent the root object to world
            # and let Maya handle any naming conflicts automatically
            # This preserves the entire hierarchy intact

            # Simply parent the root object to world - Maya will handle naming automatically
            pm.parent(obj, world=True)
            self.logger.info(f"[DEBUG] Parented {clean_name} to world successfully")

            # CRITICAL FIX: Remove namespace from the entire hierarchy
            # When we pull a hierarchy, we need to remove the temp namespace from ALL objects
            if ":" in obj.nodeName():
                self.logger.info(
                    f"[DEBUG] Removing namespace from entire hierarchy under {obj.nodeName()}"
                )
                # For Add to Scene mode: Let Maya handle naming conflicts automatically
                self._remove_namespace_from_hierarchy(obj, allow_maya_auto_rename=True)
                # Also remove namespace from materials and shading engines
                self._remove_namespace_from_materials(obj, allow_maya_auto_rename=True)

            self.logger.debug(f"Added hierarchy root to scene: {obj.nodeName()}")

            # Log the actual hierarchy that was added
            final_objects = []
            self._collect_object_and_children(obj, final_objects, set())
            self.logger.info(
                f"[DEBUG] Successfully added hierarchy with {len(final_objects)} objects"
            )

        except Exception as e:
            self.logger.warning(f"Failed to process hierarchy for {clean_name}: {e}")
            import traceback

            self.logger.debug(f"Full traceback: {traceback.format_exc()}")
            # Fallback to non-destructive single object processing
            self._process_with_hierarchy_non_destructive(obj, clean_name)

    def _process_with_hierarchy(self, obj, clean_name: str):
        """Process object preserving its parent hierarchy."""
        try:
            # Check if object already exists in current scene and handle replacement
            if pm.objExists(clean_name):
                existing_obj = pm.PyNode(clean_name)
                self.logger.info(f"Replacing existing object: {clean_name}")
                # If pull_children is enabled, also delete all children to avoid orphans
                if self.pull_children:
                    children = existing_obj.getChildren(
                        allDescendents=True, type="transform"
                    )
                    if children:
                        self.logger.debug(
                            f"Deleting {len(children)} children of {clean_name}"
                        )
                        pm.delete(children)
                pm.delete(existing_obj)

            # Get the full path of the object in the imported scene
            original_path = obj.fullPath()
            path_components = original_path.split("|")

            # Remove namespace from each component
            clean_path_components = []
            for component in path_components:
                if component:  # Skip empty components
                    clean_component = NodeNameUtilities.get_clean_node_name_from_string(
                        component
                    )
                    clean_path_components.append(clean_component)

            # Build the hierarchy in current scene
            current_parent = None
            for i, component_name in enumerate(
                clean_path_components[:-1]
            ):  # Exclude the object itself
                if not pm.objExists(component_name):
                    # Create parent transform if it doesn't exist
                    parent_obj = pm.createNode("transform", name=component_name)
                    if current_parent:
                        pm.parent(parent_obj, current_parent)
                    current_parent = parent_obj
                else:
                    current_parent = pm.PyNode(component_name)

            # Now parent the object to its proper parent (or root if no parents)
            if current_parent:
                pm.parent(obj, current_parent)
            else:
                pm.parent(obj, world=True)

            # Rename if needed
            if obj.nodeName() != clean_name:
                obj.rename(clean_name)

        except Exception as e:
            self.logger.warning(
                f"Failed to preserve hierarchy for {clean_name}, falling back to root: {e}"
            )
            self._process_as_root_object(obj, clean_name)

    def _process_with_hierarchy_non_destructive(self, obj, clean_name: str):
        """Process object preserving hierarchy but without replacing existing objects."""
        try:
            # If object exists, create a unique name instead of replacing
            final_name = clean_name
            if pm.objExists(clean_name):
                counter = 1
                while pm.objExists(f"{clean_name}_{counter}"):
                    counter += 1
                final_name = f"{clean_name}_{counter}"
                self.logger.info(
                    f"Object {clean_name} already exists, using name: {final_name}"
                )

            # Get the full path of the object in the imported scene
            original_path = obj.fullPath()
            path_components = original_path.split("|")

            # Remove namespace from each component and create unique names if needed
            clean_path_components = []
            for component in path_components:
                if component:  # Skip empty components
                    clean_component = NodeNameUtilities.get_clean_node_name_from_string(
                        component
                    )
                    clean_path_components.append(clean_component)

            # For non-destructive mode, we need to handle name conflicts for the entire hierarchy
            # Start building from the top of the hierarchy
            current_parent = None

            # Build hierarchy with unique names for any conflicts
            for i, component_name in enumerate(
                clean_path_components[:-1]
            ):  # Exclude the object itself
                # Check if this component needs a unique name
                unique_component_name = component_name
                if pm.objExists(component_name):
                    # Find a unique name for this parent
                    counter = 1
                    while pm.objExists(f"{component_name}_{counter}"):
                        counter += 1
                    unique_component_name = f"{component_name}_{counter}"
                    self.logger.debug(
                        f"Parent {component_name} exists, using: {unique_component_name}"
                    )

                if not pm.objExists(unique_component_name):
                    # Create parent transform with unique name
                    parent_obj = pm.createNode("transform", name=unique_component_name)
                    if current_parent:
                        pm.parent(parent_obj, current_parent)
                    current_parent = parent_obj
                else:
                    current_parent = pm.PyNode(unique_component_name)

            # Now parent the object to its proper parent (or root if no parents)
            if current_parent:
                pm.parent(obj, current_parent)
            else:
                pm.parent(obj, world=True)

            # Rename to final unique name
            if obj.nodeName() != final_name:
                obj.rename(final_name)

        except Exception as e:
            self.logger.warning(
                f"Failed to preserve hierarchy for {clean_name}, falling back to root: {e}"
            )
            self._process_as_root_object(obj, clean_name)

    def _process_as_root_object(self, obj, clean_name: str):
        """Process object by adding it to scene root (original behavior)."""
        # In "Add to Scene" mode, we should NOT replace existing objects
        # Instead, create additional objects (potentially with different names if conflicts exist)
        final_name = clean_name

        # If an object with this name already exists, create a unique name
        if pm.objExists(clean_name):
            counter = 1
            while pm.objExists(f"{clean_name}_{counter}"):
                counter += 1
            final_name = f"{clean_name}_{counter}"
            self.logger.info(
                f"Object {clean_name} already exists, using name: {final_name}"
            )

        # Remove namespace and parent to scene root
        pm.parent(obj, world=True)

        # Rename to final name (could be original or unique variant)
        if obj.nodeName() != final_name:
            obj.rename(final_name)


# Export the main classes
__all__ = [
    "HierarchyManager",
    "ObjectSwapper",
    "MayaObjectMatcher",
    "TreePathMatcher",
    "NodeNameUtilities",
    "NodeFilterUtilities",
    "HierarchyMapBuilder",
    "ValidationManager",
    "TreeWidgetUtilities",
    "MayaSelectionUtilities",
]
# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    ...

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
