# !/usr/bin/python
# coding=utf-8
import traceback
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Tuple

import maya.cmds as cmds
import pymel.core as pm
import pythontk as ptk

# From mayatk package
from mayatk.env_utils.namespace_sandbox import NamespaceSandbox
from mayatk.cam_utils._cam_utils import CamUtils


# ---------------------------------------------------------------------------
# Node name utilities (module-level functions, no class wrapper needed)
# ---------------------------------------------------------------------------

# Centralised in CamUtils — keep a module alias for backward compatibility.
MAYA_DEFAULT_CAMERAS = CamUtils.DEFAULT_CAMERAS


def get_clean_node_name(node) -> str:
    """Get a consistent clean node name for matching (strips namespace)."""
    try:
        node_name = node.nodeName()
        if node_name:
            return node_name.split(":")[-1] if ":" in node_name else node_name
        full_path = node.fullPath()
        last_component = full_path.split("|")[-1] if "|" in full_path else str(node)
        return (
            last_component.split(":")[-1] if ":" in last_component else last_component
        )
    except Exception:
        s = str(node)
        return s.split(":")[-1] if ":" in s else s


def get_clean_node_name_from_string(node_name: str) -> str:
    """Get a clean node name from a string path (removes namespace prefix)."""
    if not node_name:
        return ""
    last_component = node_name.split("|")[-1] if "|" in node_name else node_name
    return last_component.split(":")[-1] if ":" in last_component else last_component


def clean_hierarchy_path(path: str) -> str:
    """Clean namespace prefixes from all components of a hierarchical path."""
    if "|" in path:
        parts = path.split("|")
        return "|".join(p.split(":")[-1] if ":" in p else p for p in parts)
    return path.split(":")[-1] if ":" in path else path


def format_component(name: str, strip_namespaces: bool = False) -> str:
    """Format a single component name with optional namespace stripping."""
    if strip_namespaces and ":" in name:
        return name.split(":")[-1]
    return name


# ---------------------------------------------------------------------------
# Node filtering utilities (module-level functions)
# ---------------------------------------------------------------------------


def is_default_maya_camera(path: str, node) -> bool:
    """Check if *node* represents a Maya default camera."""
    try:
        base_name = path.split("|")[-1].split(":")[-1]
        if base_name not in MAYA_DEFAULT_CAMERAS:
            return False
        long_path = node.fullPath() if hasattr(node, "fullPath") else str(node)
        shapes = cmds.listRelatives(long_path, shapes=True, fullPath=True) or []
        for shape in shapes:
            if cmds.nodeType(shape) == "camera":
                return True
        return False
    except (RuntimeError, AttributeError):
        return False


def should_keep_node_by_type(node, node_types: List[str], exclude: bool = True) -> bool:
    """Filter nodes by shape types."""
    try:
        long_path = node.fullPath() if hasattr(node, "fullPath") else str(node)
        shapes = cmds.listRelatives(long_path, shapes=True, fullPath=True) or []
        if not shapes:
            return True  # Keep transform-only nodes
        shape_types = [cmds.nodeType(s) for s in shapes]
        has_filtered_type = any(t in shape_types for t in node_types)
        return not has_filtered_type if exclude else has_filtered_type
    except (RuntimeError, AttributeError):
        return True


def filter_path_map_by_cameras(path_map: Dict[str, Any]) -> Dict[str, Any]:
    """Remove Maya default cameras from *path_map*."""
    return {
        path: node
        for path, node in path_map.items()
        if not is_default_maya_camera(path, node)
    }


def filter_path_map_by_types(
    path_map: Dict[str, Any], node_types: List[str], exclude: bool = True
) -> Dict[str, Any]:
    """Filter *path_map* by shape node types."""
    return {
        path: node
        for path, node in path_map.items()
        if should_keep_node_by_type(node, node_types, exclude)
    }


def select_objects_in_maya(object_names: List[str]) -> int:
    """Select objects in Maya scene by name. Returns count of selected."""
    if not object_names:
        return 0
    valid = [n for n in object_names if cmds.objExists(n)]
    if valid:
        cmds.select(valid, replace=True)
    return len(valid)


# ---------------------------------------------------------------------------
# Shared rename helper (used by ObjectSwapper methods)
# ---------------------------------------------------------------------------


def _rename_node_removing_namespace(
    node, allow_maya_auto_rename: bool = False, logger=None
):
    """Rename a single PyMEL *node* by stripping its namespace prefix.

    If *allow_maya_auto_rename* is True Maya will resolve conflicts automatically.
    Otherwise a ``_1``, ``_2`` … suffix is appended manually.
    """
    try:
        current_name = node.nodeName()
        if ":" not in current_name:
            return  # nothing to strip
        clean_name = current_name.split(":")[-1]

        if allow_maya_auto_rename:
            try:
                node.rename(clean_name)
                final_name = node.nodeName()
                if logger and final_name != clean_name:
                    logger.debug(f"Maya auto-renamed {current_name} -> {final_name}")
            except RuntimeError as e:
                if logger:
                    logger.debug(f"Maya auto-rename failed for {current_name}: {e}")
        else:
            if not pm.objExists(clean_name) or pm.PyNode(clean_name) == node:
                node.rename(clean_name)
            else:
                counter = 1
                unique_name = f"{clean_name}_{counter}"
                while pm.objExists(unique_name):
                    counter += 1
                    unique_name = f"{clean_name}_{counter}"
                node.rename(unique_name)
                if logger:
                    logger.debug(f"Renamed {current_name} -> {unique_name} (conflict)")
    except RuntimeError as e:
        if logger:
            logger.debug(f"Could not rename {node}: {e}")


# ---------------------------------------------------------------------------
# HierarchyMapBuilder — uses maya.cmds for fast traversal
# ---------------------------------------------------------------------------


class HierarchyMapBuilder:
    """Builds hierarchy path maps for Maya transforms.

    Uses ``maya.cmds`` internally for significantly faster scene traversal
    compared to PyMEL, while still returning PyMEL nodes in the resulting map
    so that downstream consumers keep working unchanged.
    """

    @staticmethod
    def build_path_map(
        root,
        exclude_namespace_prefixes: List[str] = None,
        strip_namespaces: bool = False,
    ) -> Dict[str, Any]:
        """Build a mapping of hierarchical paths to transform nodes.

        Args:
            root: ``"SCENE_WIDE_MODE"`` sentinel or a PyMEL transform root.
            exclude_namespace_prefixes: namespace prefixes to skip.
            strip_namespaces: if True, strip namespace prefixes from stored
                component names.
        """
        # First pass: traverse with cmds (strings only — no PyMEL overhead).
        key_to_long: Dict[str, str] = {}
        exclude_ns = exclude_namespace_prefixes or []

        def _should_exclude(short_name: str) -> bool:
            for ns in exclude_ns:
                if short_name.startswith(ns + ":"):
                    return True
            return False

        def _traverse(long_path: str, parent_key: str = ""):
            short_name = long_path.rsplit("|", 1)[-1]
            if _should_exclude(short_name):
                return
            comp = format_component(short_name, strip_namespaces)
            current_key = f"{parent_key}|{comp}" if parent_key else comp
            key_to_long[current_key] = long_path
            children = cmds.listRelatives(
                long_path, children=True, fullPath=True, type="transform"
            )
            if children:
                for child_path in children:
                    _traverse(child_path, current_key)

        if root == "SCENE_WIDE_MODE":
            assemblies = cmds.ls(assemblies=True, long=True) or []
            for asm in assemblies:
                if cmds.nodeType(asm) == "transform":
                    _traverse(asm)
        else:
            _traverse(root.fullPath())

        # Second pass: batch-convert long paths to PyMEL nodes in one call.
        if not key_to_long:
            return {}
        long_paths = list(key_to_long.values())
        pymel_nodes = pm.ls(long_paths)
        long_to_pynode = {n.longName(): n for n in pymel_nodes}

        path_map: Dict[str, Any] = {}
        for key, long_path in key_to_long.items():
            node = long_to_pynode.get(long_path)
            if node is not None:
                path_map[key] = node

        return path_map

    @staticmethod
    def build_path_map_from_nodes(
        nodes: List[Any], strip_namespaces: bool = False
    ) -> Dict[str, Any]:
        """Build a path map from an arbitrary list of PyMEL transform nodes.

        Root nodes are inferred as those whose parent is not in the set.
        Uses cmds for traversal; values remain PyMEL nodes.
        """
        path_map: Dict[str, Any] = {}
        # Map long paths → PyMEL nodes for fast lookup
        node_paths = {n.fullPath(): n for n in nodes}
        long_path_set = set(node_paths)

        def _is_root(long_path: str) -> bool:
            parent = cmds.listRelatives(long_path, parent=True, fullPath=True)
            return (not parent) or (parent[0] not in long_path_set)

        def _traverse(long_path: str, path: str = ""):
            short_name = long_path.rsplit("|", 1)[-1]
            comp = format_component(short_name, strip_namespaces)
            current_path = f"{path}|{comp}" if path else comp
            path_map[current_path] = node_paths[long_path]
            children = cmds.listRelatives(
                long_path, children=True, fullPath=True, type="transform"
            )
            if children:
                for child_path in children:
                    if child_path in long_path_set:
                        _traverse(child_path, current_path)

        for lp in long_path_set:
            if _is_root(lp):
                _traverse(lp)
        return path_map


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

        # Pre-build name → node index for O(1) exact lookups.
        name_to_nodes: Dict[str, List[Any]] = {}
        for node in imported_transforms:
            clean = get_clean_node_name(node)
            name_to_nodes.setdefault(clean, []).append(node)

        for target_name in target_objects:
            exact_matches = name_to_nodes.get(target_name, [])
            if exact_matches:
                found_objects.extend(exact_matches)
                log_prefix = "[DRY-RUN] " if dry_run else ""
                self.logger.notice(f"{log_prefix}Exact match found: {target_name}")
                continue

            # Log debug info about why exact match failed
            self._log_debug_info(target_name, name_to_nodes, dry_run)

            if self.fuzzy_matching:
                match_result = self._find_fuzzy_match(
                    target_name, name_to_nodes, dry_run
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
            if get_clean_node_name(node) == target_name
        ]

    def _find_fuzzy_match(
        self,
        target_name: str,
        name_to_nodes: Dict[str, List[Any]],
        dry_run: bool = False,
    ) -> Optional[Tuple[Any, str]]:
        """Find fuzzy match for target object using pre-built name index."""
        if pm.objExists(target_name):
            log_prefix = "[DRY-RUN] " if dry_run else ""
            self.logger.debug(
                f"{log_prefix}Target '{target_name}' exists in current scene - will attempt fuzzy match for replacement"
            )

        import_names = list(name_to_nodes.keys())

        # Try fuzzy matching with standard threshold
        matches = ptk.FuzzyMatcher.find_all_matches(
            [target_name], import_names, score_threshold=0.7
        )

        log_prefix = "[DRY-RUN] " if dry_run else ""
        self.logger.debug(
            f"{log_prefix}Fuzzy matching for '{target_name}' with threshold 0.7: {len(matches)} matches found"
        )

        if matches and target_name in matches:
            matched_name, score = matches[target_name]
            nodes = name_to_nodes.get(matched_name)
            if nodes:
                self.logger.notice(
                    f"{log_prefix}Fuzzy match: '{target_name}' -> '{matched_name}' (score: {score:.2f})"
                )
                return nodes[0], target_name

        return None

    def _log_debug_info(
        self,
        target_name: str,
        name_to_nodes: Dict[str, List[Any]],
        dry_run: bool = False,
    ):
        """Log debug information for matching process."""
        log_prefix = "[DRY-RUN] " if dry_run else ""
        self.logger.debug(
            f"{log_prefix}No exact match for '{target_name}' in imported objects: {list(name_to_nodes.keys())}"
        )


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
        self.import_manager = import_manager

        # Initialize state
        self.current_scene_path_map: Dict[str, Any] = {}
        self.reference_scene_path_map: Dict[str, Any] = {}
        self.differences: Dict[str, Any] = {}
        self.missing_objects: List[str] = []
        self.extra_objects: List[str] = []

        # Reverse mappings: cleaned_path → raw_path (populated by analyze_hierarchies)
        self.clean_to_raw_current: Dict[str, str] = {}
        self.clean_to_raw_reference: Dict[str, str] = {}

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

            self.logger.progress(
                f"Built current scene path map: {len(self.current_scene_path_map)} paths"
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

            self.logger.progress(
                f"Built reference path map: {len(self.reference_scene_path_map)} paths"
            )

            # Single-pass filtering: cameras + type filters in one iteration per map.
            exclude_types: List[str] = []
            if filter_meshes:
                exclude_types.append("mesh")
            if filter_cameras:
                exclude_types.append("camera")
            if filter_lights:
                exclude_types.append("light")

            for attr in ("current_scene_path_map", "reference_scene_path_map"):
                pmap = getattr(self, attr)
                filtered: Dict[str, Any] = {}
                for path, node in pmap.items():
                    if is_default_maya_camera(path, node):
                        continue
                    if exclude_types:
                        if not should_keep_node_by_type(
                            node, exclude_types, exclude=True
                        ):
                            continue
                    filtered[path] = node
                setattr(self, attr, filtered)

            self.logger.debug(
                f"Filtering done — current: {len(self.current_scene_path_map)}, "
                f"reference: {len(self.reference_scene_path_map)}"
            )

            # Prepare cleaned versions (strip namespaces per component) for comparison
            current_paths_raw = set(self.current_scene_path_map.keys())
            reference_paths_raw = set(self.reference_scene_path_map.keys())

            cleaned_current_paths = {clean_hierarchy_path(p) for p in current_paths_raw}
            cleaned_reference_paths = {
                clean_hierarchy_path(p) for p in reference_paths_raw
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
                                cleaned = clean_hierarchy_path(raw_path)
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

            self.log_table(
                data=[
                    ["Current paths", str(len(cleaned_current_paths))],
                    ["Reference paths", str(len(cleaned_reference_paths))],
                    ["Missing", str(len(self.missing_objects))],
                    ["Extra", str(len(self.extra_objects))],
                ],
                headers=["Metric", "Count"],
                title="PATH COMPARISON",
            )

            # Debug final differences
            self.logger.debug(
                f"[RESULT] Missing objects (sample): {self.missing_objects[:5]}"
            )
            self.logger.debug(
                f"[RESULT] Extra objects (sample): {self.extra_objects[:5]}"
            )

            # ── Build reverse mapping: cleaned_path → raw_path ──
            self.clean_to_raw_current = {}
            for raw_path in current_paths_raw:
                self.clean_to_raw_current[clean_hierarchy_path(raw_path)] = raw_path

            self.clean_to_raw_reference = {}
            for raw_path in reference_paths_raw:
                self.clean_to_raw_reference[clean_hierarchy_path(raw_path)] = raw_path

            # ── Detect reparented items ──
            # Same leaf name appears in both missing and extra but under different parents.
            reparented: list = []
            remaining_missing = list(self.missing_objects)
            remaining_extra = list(self.extra_objects)

            try:
                # Build leaf-name → [cleaned_path] indexes for both pools
                missing_by_leaf: Dict[str, List[str]] = {}
                for p in remaining_missing:
                    leaf = p.rsplit("|", 1)[-1]
                    missing_by_leaf.setdefault(leaf, []).append(p)

                extra_by_leaf: Dict[str, List[str]] = {}
                for p in remaining_extra:
                    leaf = p.rsplit("|", 1)[-1]
                    extra_by_leaf.setdefault(leaf, []).append(p)

                # Match strictly: only when there's exactly 1 candidate on each side
                matched_missing = set()
                matched_extra = set()
                for leaf, missing_paths in missing_by_leaf.items():
                    extra_paths = extra_by_leaf.get(leaf, [])
                    if len(missing_paths) == 1 and len(extra_paths) == 1:
                        ref_path = missing_paths[0]
                        cur_path = extra_paths[0]
                        reparented.append(
                            {
                                "leaf": leaf,
                                "reference_path": ref_path,
                                "current_path": cur_path,
                            }
                        )
                        matched_missing.add(ref_path)
                        matched_extra.add(cur_path)

                # Remove reparented items from the missing/extra pools
                remaining_missing = [
                    p for p in remaining_missing if p not in matched_missing
                ]
                remaining_extra = [p for p in remaining_extra if p not in matched_extra]

                if reparented:
                    self.logger.debug(
                        f"Detected {len(reparented)} reparented items "
                        f"(e.g. {reparented[0]['leaf']}: "
                        f"{reparented[0]['reference_path']} → {reparented[0]['current_path']})"
                    )
            except Exception as rp_err:
                self.logger.debug(f"Reparented detection failed: {rp_err}")

            # ── Detect renamed (fuzzy) items ──
            fuzzy_matches: list = []
            try:
                if remaining_missing and remaining_extra and self.fuzzy_matching:
                    missing_leaves = [p.rsplit("|", 1)[-1] for p in remaining_missing]
                    extra_leaves = [p.rsplit("|", 1)[-1] for p in remaining_extra]

                    raw_matches = ptk.FuzzyMatcher.find_all_matches(
                        missing_leaves,
                        extra_leaves,
                        score_threshold=0.7,
                    )
                    # raw_matches: Dict[str, Tuple[str, float]]
                    matched_fm_missing = set()
                    matched_fm_extra = set()
                    for query_leaf, (best_leaf, score) in raw_matches.items():
                        # Map leaves back to full cleaned paths
                        ref_path = next(
                            (
                                p
                                for p in remaining_missing
                                if p.rsplit("|", 1)[-1] == query_leaf
                            ),
                            None,
                        )
                        cur_path = next(
                            (
                                p
                                for p in remaining_extra
                                if p.rsplit("|", 1)[-1] == best_leaf
                            ),
                            None,
                        )
                        if (
                            ref_path
                            and cur_path
                            and ref_path not in matched_fm_missing
                            and cur_path not in matched_fm_extra
                        ):
                            fuzzy_matches.append(
                                {
                                    "target_name": ref_path,
                                    "current_name": cur_path,
                                    "score": score,
                                }
                            )
                            matched_fm_missing.add(ref_path)
                            matched_fm_extra.add(cur_path)

                    # Remove fuzzy-matched from the remaining pools
                    remaining_missing = [
                        p for p in remaining_missing if p not in matched_fm_missing
                    ]
                    remaining_extra = [
                        p for p in remaining_extra if p not in matched_fm_extra
                    ]

                    if fuzzy_matches:
                        self.logger.debug(
                            f"Detected {len(fuzzy_matches)} fuzzy renamed matches "
                            f"(e.g. {fuzzy_matches[0]['target_name']} ↔ {fuzzy_matches[0]['current_name']} "
                            f"score={fuzzy_matches[0]['score']:.2f})"
                        )
            except Exception as fz_err:
                self.logger.debug(f"Fuzzy renamed detection failed: {fz_err}")

            # ── Detect FBX name-flattening (suffix matching) ──
            # FBX export can prepend parent group names to child node names,
            # e.g. "BOOSTER_OFF_6_SWITCH" → "OVERHEAD_CONSOLE_BOOSTERS_BOOSTER_OFF_6_SWITCH".
            # Detect these by checking if an extra leaf is a suffix of a missing leaf
            # (or vice versa) at the same parent path.
            try:
                if remaining_missing and remaining_extra:
                    # Group by parent path for efficient matching
                    missing_by_parent: Dict[str, List[tuple]] = {}
                    for p in remaining_missing:
                        if "|" in p:
                            parent, leaf = p.rsplit("|", 1)
                        else:
                            parent, leaf = "", p
                        missing_by_parent.setdefault(parent, []).append((leaf, p))

                    extra_by_parent: Dict[str, List[tuple]] = {}
                    for p in remaining_extra:
                        if "|" in p:
                            parent, leaf = p.rsplit("|", 1)
                        else:
                            parent, leaf = "", p
                        extra_by_parent.setdefault(parent, []).append((leaf, p))

                    matched_sfx_missing: set = set()
                    matched_sfx_extra: set = set()

                    for parent in missing_by_parent:
                        if parent not in extra_by_parent:
                            continue
                        m_items = missing_by_parent[parent]
                        e_items = extra_by_parent[parent]
                        # For each missing leaf, look for an extra leaf that is
                        # a suffix (the shorter name is contained at the end of
                        # the longer name preceded by _ or matching exactly).
                        for m_leaf, m_path in m_items:
                            if m_path in matched_sfx_missing:
                                continue
                            for e_leaf, e_path in e_items:
                                if e_path in matched_sfx_extra:
                                    continue
                                if m_leaf == e_leaf:
                                    continue
                                # Determine which is the longer (flattened) name
                                if len(m_leaf) > len(e_leaf):
                                    longer, shorter = m_leaf, e_leaf
                                else:
                                    longer, shorter = e_leaf, m_leaf
                                # The shorter name must appear at the end of the
                                # longer name, preceded by '_'
                                if (
                                    longer.endswith(shorter)
                                    and longer[len(longer) - len(shorter) - 1] == "_"
                                ):
                                    fuzzy_matches.append(
                                        {
                                            "target_name": m_path,
                                            "current_name": e_path,
                                            "score": 1.0,
                                        }
                                    )
                                    matched_sfx_missing.add(m_path)
                                    matched_sfx_extra.add(e_path)
                                    break  # move to next missing item

                    if matched_sfx_missing:
                        remaining_missing = [
                            p for p in remaining_missing if p not in matched_sfx_missing
                        ]
                        remaining_extra = [
                            p for p in remaining_extra if p not in matched_sfx_extra
                        ]
                        self.logger.debug(
                            f"Detected {len(matched_sfx_missing)} FBX name-flattening "
                            f"matches (suffix matching)"
                        )
            except Exception as sfx_err:
                self.logger.debug(f"Suffix matching failed: {sfx_err}")

            # Update missing/extra to only contain truly-missing/truly-extra items
            self.missing_objects = remaining_missing
            self.extra_objects = remaining_extra

            # Build detailed differences
            self.differences = {
                "missing": self.missing_objects,
                "extra": self.extra_objects,
                "reparented": reparented,
                "fuzzy_matches": fuzzy_matches,
                "total_missing": len(self.missing_objects),
                "total_extra": len(self.extra_objects),
                "total_reparented": len(reparented),
                "total_fuzzy": len(fuzzy_matches),
            }

            self.log_table(
                data=[
                    ["Truly missing", str(len(self.missing_objects))],
                    ["Truly extra", str(len(self.extra_objects))],
                    ["Reparented", str(len(reparented))],
                    ["Fuzzy renamed", str(len(fuzzy_matches))],
                ],
                headers=["Category", "Count"],
                title="DIFF CATEGORIES",
            )

            return self.differences

        except Exception as e:
            self.logger.error(f"Failed to analyze hierarchies: {e}")
            return {}

    # ------------------------------------------------------------------ #
    # Hierarchy repair methods (operate on results from analyze_hierarchies)
    # ------------------------------------------------------------------ #

    def _resolve_node(self, cleaned_path: str, source: str = "current"):
        """Resolve a cleaned diff path to a live PyMEL node.

        Args:
            cleaned_path: Namespace-stripped hierarchy path from the diff.
            source: ``"current"`` or ``"reference"`` — which path map to look up.

        Returns:
            PyMEL transform node, or *None* if not found.
        """
        if source == "current":
            raw = self.clean_to_raw_current.get(cleaned_path)
            path_map = self.current_scene_path_map
        else:
            raw = self.clean_to_raw_reference.get(cleaned_path)
            path_map = self.reference_scene_path_map

        if raw and raw in path_map:
            node = path_map[raw]
            try:
                if node.exists():
                    return node
            except Exception:
                pass
        return None

    @staticmethod
    def _ensure_parent_chain(path: str):
        """Create any missing intermediate transforms for *path* and return the
        immediate parent node (or *None* for root-level items).

        *path* is a pipe-separated cleaned hierarchy path, e.g.
        ``GRP_A|GRP_B|LEAF``.  For this example the method ensures ``GRP_A``
        and ``GRP_B`` exist and returns the PyMEL node for ``GRP_B``.

        Uses parent-relative child lookups to correctly handle duplicate
        names at different hierarchy levels (e.g. ``A|A|A``).
        """
        parts = path.split("|")
        if len(parts) <= 1:
            return None  # root-level, no parent needed

        current_parent = None
        for component in parts[:-1]:  # everything except the leaf
            if current_parent is not None:
                # Look for component as a direct child of current_parent
                parent_long = current_parent.fullPath()
                children = (
                    cmds.listRelatives(
                        parent_long,
                        children=True,
                        fullPath=True,
                        type="transform",
                    )
                    or []
                )
                match = None
                for c in children:
                    if c.rsplit("|", 1)[-1] == component:
                        match = c
                        break
                if match:
                    current_parent = pm.PyNode(match)
                else:
                    new_grp = pm.createNode("transform", name=component)
                    pm.parent(new_grp, current_parent)
                    current_parent = new_grp
            else:
                # Root level — use leading pipe for unambiguous lookup
                root_path = f"|{component}"
                if cmds.objExists(root_path):
                    current_parent = pm.PyNode(root_path)
                else:
                    current_parent = pm.createNode("transform", name=component)
        return current_parent

    def create_stubs(self, paths: Optional[List[str]] = None) -> List[str]:
        """Create empty transform stubs for missing hierarchy paths.

        This makes the current scene's skeleton match the reference without
        importing actual geometry.  Each stub is an empty transform node
        parented at the correct position in the hierarchy.

        Args:
            paths: Cleaned hierarchy paths to stub.  Defaults to
                ``self.differences["missing"]``.

        Returns:
            List of created node names.
        """
        targets = paths if paths is not None else self.differences.get("missing", [])
        if not targets:
            self.logger.notice("No missing items to stub.")
            return []

        created: List[str] = []
        for cleaned_path in targets:
            leaf = cleaned_path.rsplit("|", 1)[-1]

            if self.dry_run:
                self.logger.info(f"[DRY-RUN] Would create stub: {cleaned_path}")
                created.append(leaf)
                continue

            try:
                parent = self._ensure_parent_chain(cleaned_path)
                # Check if leaf already exists under this specific parent
                if parent is not None:
                    parent_long = parent.fullPath()
                    children = (
                        cmds.listRelatives(
                            parent_long,
                            children=True,
                            fullPath=True,
                            type="transform",
                        )
                        or []
                    )
                    if any(c.rsplit("|", 1)[-1] == leaf for c in children):
                        self.logger.debug(
                            f"Stub skipped (already exists): {cleaned_path}"
                        )
                        continue
                else:
                    if cmds.objExists(f"|{leaf}"):
                        self.logger.debug(
                            f"Stub skipped (already exists): {cleaned_path}"
                        )
                        continue

                stub = pm.createNode("transform", name=leaf)
                if parent:
                    pm.parent(stub, parent)
                created.append(stub.nodeName())
                self.logger.debug(f"Created stub: {stub.fullPath()}")
            except Exception as e:
                self.logger.warning(f"Failed to create stub for {cleaned_path}: {e}")

        self.logger.result(f"Created {len(created)} stub transform(s).")
        return created

    @staticmethod
    def _has_animated_ancestor(node) -> bool:
        """Return True if *node* or any of its ancestors has animation curves."""
        current = node
        while current is not None:
            if pm.keyframe(current, query=True, keyframeCount=True):
                return True
            current = current.getParent()
        return False

    def quarantine_extras(
        self,
        group: str = "_QUARANTINE",
        paths: Optional[List[str]] = None,
        skip_animated: bool = False,
    ) -> List[str]:
        """Move extra (scene-only) items to a root-level quarantine group.

        Items that exist in the current scene but not in the reference are
        reparented under *group* so they no longer pollute the matched
        hierarchy.  A game engine will see them as new top-level content
        rather than orphans breaking the expected structure.

        Ancestor deduplication is applied: if ``GRP`` and ``GRP|CHILD`` are
        both extra, only ``GRP`` is moved (``CHILD`` comes along for free).

        Root-level extras (no ``|`` in path) are already isolated from the
        reference hierarchy and are left in place unless they need to be
        gathered under a specific group.

        Auto-detection: when ``group`` is the default ``"_QUARANTINE"`` and
        all extras share a single root-level ancestor that is itself extra,
        that existing group is reused instead of creating a new one.

        Args:
            group: Name of the root-level quarantine group.
            paths: Cleaned hierarchy paths to quarantine.  Defaults to
                ``self.differences["extra"]``.
            skip_animated: When True, extras parented under an animated
                object are left in place (they may be intentionally
                constrained/attached).

        Returns:
            List of node names that were moved.
        """
        targets = paths if paths is not None else self.differences.get("extra", [])
        if not targets:
            self.logger.notice("No extra items to quarantine.")
            return []

        # ── Ancestor deduplication ──
        targets_set = set(targets)
        roots_only: List[str] = []
        for p in sorted(targets, key=lambda x: x.count("|")):
            # Keep only if no ancestor is also in the target set
            parts = p.split("|")
            if not any(
                "|".join(parts[: i + 1]) in targets_set for i in range(len(parts) - 1)
            ):
                roots_only.append(p)

        # ── Auto-detect existing container ──
        # If the user hasn't set a custom name (still default) and all
        # extras share a single root-level ancestor that is itself extra
        # AND that root has multiple direct extra children, reuse it as
        # the quarantine container instead of creating _QUARANTINE.
        if group == "_QUARANTINE" and roots_only:
            root_names = {p.split("|")[0] for p in roots_only}
            if len(root_names) == 1:
                natural_root = next(iter(root_names))
                # Count direct children of this root that are also extra
                direct_extra_children = sum(
                    1
                    for p in targets_set
                    if p.startswith(natural_root + "|")
                    and "|" not in p[len(natural_root) + 1 :]
                )
                # Only adopt if the root is extra AND has multiple extra
                # children — meaning it's a real container, not a lone orphan.
                if natural_root in targets_set and direct_extra_children >= 2:
                    group = natural_root
                    self.logger.info(
                        f"Using existing root group '{group}' as quarantine "
                        f"(all extras are already under it)."
                    )

        # ── Separate already-root items from nested ones ──
        already_root: List[str] = []
        needs_move: List[str] = []
        for p in roots_only:
            top = p.split("|")[0]
            if "|" not in p:
                # This IS a root-level item
                if p == group:
                    # The quarantine group itself — nothing to do
                    already_root.append(p)
                else:
                    needs_move.append(p)
            elif top == group:
                # Already under the quarantine group
                already_root.append(p)
            else:
                needs_move.append(p)

        if already_root:
            self.logger.info(
                f"{len(already_root)} extra(s) already under '{group}' — skipped."
            )

        moved: List[str] = []

        if not needs_move:
            self.logger.notice(
                f"All {len(already_root)} extra(s) are already contained "
                f"under '{group}'. Nothing to move."
            )
            return [p.rsplit("|", 1)[-1] for p in already_root]

        if self.dry_run:
            for p in needs_move:
                node = self._resolve_node(p, source="current")
                if skip_animated and node and self._has_animated_ancestor(node):
                    self.logger.info(f"[DRY-RUN] Would skip (animated ancestor): {p}")
                    continue
                self.logger.info(f"[DRY-RUN] Would quarantine: {p}")
                moved.append(p.rsplit("|", 1)[-1])
            self.logger.result(f"[DRY-RUN] Would quarantine {len(moved)} item(s).")
            return moved

        # Ensure quarantine group exists
        if pm.objExists(group):
            quarantine_grp = pm.PyNode(group)
        else:
            quarantine_grp = pm.createNode("transform", name=group)

        skipped_animated: List[str] = []
        for cleaned_path in needs_move:
            node = self._resolve_node(cleaned_path, source="current")
            if not node:
                self.logger.debug(
                    f"Quarantine skipped (node not found): {cleaned_path}"
                )
                continue
            if skip_animated and self._has_animated_ancestor(node):
                skipped_animated.append(cleaned_path)
                continue
            try:
                pm.parent(node, quarantine_grp)
                moved.append(node.nodeName())
                self.logger.debug(f"Quarantined: {node.fullPath()}")
            except Exception as e:
                self.logger.warning(f"Failed to quarantine {cleaned_path}: {e}")

        if skipped_animated:
            self.logger.info(
                f"{len(skipped_animated)} extra(s) skipped (under animated ancestor)."
            )

        self.logger.result(f"Quarantined {len(moved)} item(s) under '{group}'.")
        return moved

    def fix_reparented(self, items: Optional[List[Dict[str, str]]] = None) -> List[str]:
        """Move reparented nodes to match their reference hierarchy position.

        Each item is a dict with ``current_path`` and ``reference_path``
        keys (as produced by ``analyze_hierarchies``).

        Args:
            items: List of reparented-item dicts.  Defaults to
                ``self.differences["reparented"]``.

        Returns:
            List of node names that were reparented.
        """
        targets = items if items is not None else self.differences.get("reparented", [])
        if not targets:
            self.logger.notice("No reparented items to fix.")
            return []

        fixed: List[str] = []
        for entry in targets:
            current_path = entry.get("current_path", "")
            reference_path = entry.get("reference_path", "")
            if not current_path or not reference_path:
                continue

            if self.dry_run:
                self.logger.info(
                    f"[DRY-RUN] Would reparent: {current_path} -> {reference_path}"
                )
                fixed.append(current_path.rsplit("|", 1)[-1])
                continue

            node = self._resolve_node(current_path, source="current")
            if not node:
                self.logger.debug(f"Reparent skipped (node not found): {current_path}")
                continue

            try:
                old_parent = node.getParent()
                target_parent = self._ensure_parent_chain(reference_path)
                if target_parent:
                    pm.parent(node, target_parent)
                else:
                    pm.parent(node, world=True)
                fixed.append(node.nodeName())
                self.logger.debug(f"Reparented: {node.nodeName()} -> {node.fullPath()}")

                # Clean up now-empty source parent (avoids leftover shells)
                if old_parent and old_parent.exists():
                    children = old_parent.getChildren(type="transform")
                    shapes = old_parent.getShapes()
                    if not children and not shapes:
                        old_name = old_parent.nodeName()
                        pm.delete(old_parent)
                        self.logger.debug(f"Deleted empty source parent: {old_name}")
            except Exception as e:
                self.logger.warning(f"Failed to reparent {current_path}: {e}")

        self.logger.result(f"Fixed {len(fixed)} reparented item(s).")
        return fixed


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

        self.matcher = MayaObjectMatcher(self.import_manager, fuzzy_matching)

    def push_objects_to_scene(
        self,
        target_objects: List[str],
        target_file: Union[str, Path],
        backup: bool = True,
    ) -> bool:
        """Push objects from current scene to target scene."""
        target_file = Path(target_file)

        if not target_objects:
            self.logger.error("No target objects specified for push")
            return False
        if not target_file.exists():
            self.logger.error(f"Target file not found: {target_file}")
            return False

        return self.pull_objects_from_scene(target_objects, target_file, backup)

    def pull_objects_from_scene(
        self,
        target_objects: List[str],
        source_file: Union[str, Path],
        backup: bool = True,
    ) -> bool:
        """Pull objects from source scene into current scene."""
        source_file = Path(source_file)

        if not target_objects:
            self.logger.error("No target objects specified for pull")
            return False
        if not source_file.exists():
            self.logger.error(f"Source file not found: {source_file}")
            return False

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
                get_clean_node_name_from_string(obj) for obj in target_objects
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

        self.logger.debug(
            f" _process_found_objects called with pull_children={self.pull_children}"
        )
        self.logger.debug(f" Found {len(found_objects)} objects to process")

        # Log the names of found objects for debugging
        for i, obj in enumerate(found_objects[:5]):  # Show first 5
            try:
                obj_name = obj.nodeName() if hasattr(obj, "nodeName") else str(obj)
                self.logger.debug(f" Found object [{i}]: {obj_name}")
            except Exception:
                self.logger.debug(f" Found object [{i}]: <name unavailable>")

        # When pull_children is enabled, filter to only root objects to avoid processing
        # hierarchies multiple times. Root objects will naturally include their children.
        if self.pull_children:
            self.logger.debug(" Pull children is ENABLED - filtering to root objects")
            # Filter to root objects only (objects that are not children of other selected objects)
            root_objects = self._filter_to_root_objects(found_objects)
            self.logger.debug(
                f" Filtered {len(found_objects)} objects to {len(root_objects)} root objects for hierarchy pulling"
            )

            # Log the root objects for debugging
            for i, obj in enumerate(root_objects):
                try:
                    obj_name = obj.nodeName() if hasattr(obj, "nodeName") else str(obj)
                    self.logger.debug(f" Root object [{i}]: {obj_name}")
                except Exception:
                    self.logger.debug(f" Root object [{i}]: <name unavailable>")

            objects_to_process = root_objects
        else:
            self.logger.debug(
                " Pull children is DISABLED - processing individual objects"
            )
            # Process individual objects without their children
            objects_to_process = found_objects

        self.logger.debug(f" Processing {len(objects_to_process)} objects")

        for i, obj in enumerate(objects_to_process):
            try:
                # Check if object still exists before processing
                if not obj.exists():
                    self.logger.warning(f"Object {obj} no longer exists, skipping")
                    continue

                clean_name = get_clean_node_name(obj)
                self.logger.debug(
                    f"Processing object [{i}]: {clean_name} (pull_mode={self.pull_mode})"
                )

                if self.pull_mode == "Merge Hierarchies":
                    # Merge Hierarchies: preserve parent hierarchy structure
                    if self.pull_children:
                        self.logger.debug(
                            f" Calling _process_with_hierarchy_and_children for {clean_name}"
                        )
                        self._process_with_hierarchy_and_children(obj, clean_name)
                    else:
                        self.logger.debug(
                            f" Calling _process_with_hierarchy for {clean_name}"
                        )
                        self._process_with_hierarchy(obj, clean_name)
                else:
                    # Add to Scene: add object to scene, maintaining hierarchy if pull_children=True
                    if self.pull_children:
                        self.logger.debug(
                            f" Calling _process_with_hierarchy_non_destructive_and_children for {clean_name}"
                        )
                        self._process_with_hierarchy_non_destructive_and_children(
                            obj, clean_name
                        )
                    else:
                        self.logger.debug(
                            f" Calling _process_as_root_object for {clean_name}"
                        )
                        self._process_as_root_object(obj, clean_name)

                self.logger.debug(f" Successfully processed object: {clean_name}")

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
            except Exception:
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

            self.logger.debug(
                f"Processing hierarchy root '{clean_name}' with {len(all_objects)} total objects for Merge mode"
            )

            # For Merge Hierarchies mode with pull_children:
            # IMPORTANT: Don't delete existing objects from previous pull operations
            # Let Maya handle naming conflicts naturally instead of manual renaming
            if pm.objExists(clean_name):
                existing_obj = pm.PyNode(clean_name)
                # Check if the existing object is at world level (from "Add to Scene")
                parent = existing_obj.getParent()
                if parent is None:  # Object is at world level
                    self.logger.debug(
                        f"Existing object '{clean_name}' from previous pull found at world level - preserving it"
                    )
                    self.logger.debug(
                        f"Maya will automatically rename new object to avoid conflict"
                    )
                    # Let Maya handle the naming - don't pre-rename
                else:
                    # Object is part of another hierarchy, safe to replace
                    self.logger.debug(
                        f"Replacing existing hierarchy root: {clean_name}"
                    )
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
            self.logger.debug(
                f" _process_with_hierarchy_merge_root_only called for {clean_name}"
            )

            # Get the full path of the object in the imported scene
            original_path = obj.fullPath()
            path_components = original_path.split("|")

            # Remove namespace from each component
            clean_path_components = []
            for component in path_components:
                if component:  # Skip empty components
                    clean_component = get_clean_node_name_from_string(component)
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
                self.logger.debug(
                    f" Removing namespace from entire hierarchy under {obj.nodeName()}"
                )
                # Let Maya handle naming conflicts naturally
                self._remove_namespace_from_hierarchy(obj, allow_maya_auto_rename=True)
                # Also remove namespace from materials and shading engines
                self._remove_namespace_from_materials(obj, allow_maya_auto_rename=True)

            # Rename root if needed (after namespace removal)
            current_name = obj.nodeName()
            if current_name != clean_name:
                self.logger.debug(f" Renaming {current_name} to {clean_name}")
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

            self.logger.debug(
                f" Removing namespace from {len(all_objects)} objects in hierarchy (maya_auto_rename={allow_maya_auto_rename})"
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
                                        f"Maya auto-renamed {current_name} to {final_name}"
                                    )
                                else:
                                    self.logger.debug(
                                        f"Renamed {current_name} to {clean_name}"
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
                                    f"Renamed {current_name} to {clean_name}"
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
                                    f"Renamed {current_name} to {unique_name} (conflict resolved)"
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
                                    f"Renamed material {current_name} to {final_name}"
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
                                    f"Renamed material {current_name} to {clean_name}"
                                )
                            else:
                                counter = 1
                                unique_name = f"{clean_name}_{counter}"
                                while pm.objExists(unique_name):
                                    counter += 1
                                    unique_name = f"{clean_name}_{counter}"
                                material.rename(unique_name)
                                self.logger.debug(
                                    f"Renamed material {current_name} to {unique_name}"
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
                                    f"Renamed shading engine {current_name} to {final_name}"
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
                                    f"Renamed shading engine {current_name} to {clean_name}"
                                )
                            else:
                                counter = 1
                                unique_name = f"{clean_name}_{counter}"
                                while pm.objExists(unique_name):
                                    counter += 1
                                    unique_name = f"{clean_name}_{counter}"
                                sg.rename(unique_name)
                                self.logger.debug(
                                    f"Renamed shading engine {current_name} to {unique_name}"
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
            self.logger.debug(
                f" _process_with_hierarchy_non_destructive_and_children called for {clean_name}"
            )

            # Get the complete hierarchy for this root object
            all_objects = []
            self._collect_object_and_children(obj, all_objects, set())

            self.logger.debug(
                f" Processing hierarchy root '{clean_name}' with {len(all_objects)} total objects for Add to Scene mode"
            )

            # Log a few child names for debugging
            for i, child_obj in enumerate(all_objects[:5]):  # Show first 5
                try:
                    child_name = (
                        child_obj.nodeName()
                        if hasattr(child_obj, "nodeName")
                        else str(child_obj)
                    )
                    self.logger.debug(f" Child object [{i}]: {child_name}")
                except Exception:
                    self.logger.debug(f" Child object [{i}]: <name unavailable>")

            # For Add to Scene mode, just parent the root object to world
            # and let Maya handle any naming conflicts automatically
            # This preserves the entire hierarchy intact

            # Simply parent the root object to world - Maya will handle naming automatically
            pm.parent(obj, world=True)
            self.logger.debug(f" Parented {clean_name} to world successfully")

            # CRITICAL FIX: Remove namespace from the entire hierarchy
            # When we pull a hierarchy, we need to remove the temp namespace from ALL objects
            if ":" in obj.nodeName():
                self.logger.debug(
                    f" Removing namespace from entire hierarchy under {obj.nodeName()}"
                )
                # For Add to Scene mode: Let Maya handle naming conflicts automatically
                self._remove_namespace_from_hierarchy(obj, allow_maya_auto_rename=True)
                # Also remove namespace from materials and shading engines
                self._remove_namespace_from_materials(obj, allow_maya_auto_rename=True)

            self.logger.debug(f"Added hierarchy root to scene: {obj.nodeName()}")

            # Log the actual hierarchy that was added
            final_objects = []
            self._collect_object_and_children(obj, final_objects, set())
            self.logger.debug(
                f" Successfully added hierarchy with {len(final_objects)} objects"
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
                self.logger.debug(f"Replacing existing object: {clean_name}")
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
                    clean_component = get_clean_node_name_from_string(component)
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
            # Let Maya handle naming conflicts automatically instead of pre-renaming
            final_name = clean_name
            if pm.objExists(clean_name):
                self.logger.debug(
                    f"Object {clean_name} already exists, Maya will automatically rename"
                )

            # Get the full path of the object in the imported scene
            original_path = obj.fullPath()
            path_components = original_path.split("|")

            # Remove namespace from each component and create unique names if needed
            clean_path_components = []
            for component in path_components:
                if component:  # Skip empty components
                    clean_component = get_clean_node_name_from_string(component)
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

            # Rename to clean name - Maya will handle conflicts automatically
            if obj.nodeName() != final_name:
                obj.rename(final_name)
                # Log the actual final name after Maya's automatic handling
                actual_final_name = obj.nodeName()
                if actual_final_name != final_name:
                    self.logger.debug(f"Maya auto-renamed to: {actual_final_name}")

        except Exception as e:
            self.logger.warning(
                f"Failed to preserve hierarchy for {clean_name}, falling back to root: {e}"
            )
            self._process_as_root_object(obj, clean_name)

    def _process_as_root_object(self, obj, clean_name: str):
        """Process object by adding it to scene root (original behavior)."""
        # In "Add to Scene" mode, we should NOT replace existing objects
        # Instead, create additional objects (potentially with different names if conflicts exist)
        # Let Maya handle naming conflicts automatically
        final_name = clean_name
        if pm.objExists(clean_name):
            self.logger.debug(
                f"Object {clean_name} already exists, Maya will automatically rename"
            )

        # Remove namespace and parent to scene root
        pm.parent(obj, world=True)

        # Rename to final name - Maya will handle conflicts by adding suffixes automatically
        if obj.nodeName() != final_name:
            obj.rename(final_name)
            # Log the actual final name after Maya's automatic handling
            actual_final_name = obj.nodeName()
            if actual_final_name != final_name:
                self.logger.debug(f"Maya auto-renamed to: {actual_final_name}")


# Export the main classes and key functions
__all__ = [
    "HierarchyManager",
    "ObjectSwapper",
    "MayaObjectMatcher",
    "HierarchyMapBuilder",
    "MAYA_DEFAULT_CAMERAS",
    "get_clean_node_name",
    "get_clean_node_name_from_string",
    "clean_hierarchy_path",
    "format_component",
    "is_default_maya_camera",
    "should_keep_node_by_type",
    "filter_path_map_by_cameras",
    "filter_path_map_by_types",
    "select_objects_in_maya",
]
# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    ...

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
