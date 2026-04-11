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
from mayatk.display_utils.color_manager import ColorUtils


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
            remaining_missing = list(self.missing_objects)
            remaining_extra = list(self.extra_objects)

            reparented, remaining_missing, remaining_extra = self._detect_reparented(
                remaining_missing, remaining_extra
            )

            # ── Detect renamed (fuzzy) items ──
            fuzzy_matches, remaining_missing, remaining_extra = (
                self._detect_fuzzy_renames(remaining_missing, remaining_extra)
            )

            # ── Detect FBX name-flattening (suffix matching) ──
            suffix_matches, remaining_missing, remaining_extra = (
                self._detect_suffix_flattening(remaining_missing, remaining_extra)
            )
            fuzzy_matches.extend(suffix_matches)

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
    # Detection passes (called by analyze_hierarchies)
    # ------------------------------------------------------------------ #

    def _detect_reparented(
        self,
        remaining_missing: List[str],
        remaining_extra: List[str],
    ) -> Tuple[List[Dict], List[str], List[str]]:
        """Detect items that exist in both pools under different parents.

        Returns ``(reparented, remaining_missing, remaining_extra)`` with
        matched items removed from the remaining pools.
        """
        reparented: List[Dict] = []
        try:
            missing_by_leaf: Dict[str, List[str]] = {}
            for p in remaining_missing:
                missing_by_leaf.setdefault(p.rsplit("|", 1)[-1], []).append(p)

            extra_by_leaf: Dict[str, List[str]] = {}
            for p in remaining_extra:
                extra_by_leaf.setdefault(p.rsplit("|", 1)[-1], []).append(p)

            matched_missing: set = set()
            matched_extra: set = set()
            for leaf, m_paths in missing_by_leaf.items():
                e_paths = extra_by_leaf.get(leaf, [])
                if len(m_paths) == 1 and len(e_paths) == 1:
                    reparented.append(
                        {
                            "leaf": leaf,
                            "reference_path": m_paths[0],
                            "current_path": e_paths[0],
                        }
                    )
                    matched_missing.add(m_paths[0])
                    matched_extra.add(e_paths[0])

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
        except Exception as e:
            self.logger.debug(f"Reparented detection failed: {e}")

        return reparented, remaining_missing, remaining_extra

    def _detect_fuzzy_renames(
        self,
        remaining_missing: List[str],
        remaining_extra: List[str],
    ) -> Tuple[List[Dict], List[str], List[str]]:
        """Detect items that were renamed (fuzzy leaf-name matching).

        Returns ``(fuzzy_matches, remaining_missing, remaining_extra)``.
        """
        fuzzy_matches: List[Dict] = []
        try:
            if not (remaining_missing and remaining_extra and self.fuzzy_matching):
                return fuzzy_matches, remaining_missing, remaining_extra

            missing_leaves = [p.rsplit("|", 1)[-1] for p in remaining_missing]
            extra_leaves = [p.rsplit("|", 1)[-1] for p in remaining_extra]

            raw_matches = ptk.FuzzyMatcher.find_all_matches(
                missing_leaves,
                extra_leaves,
                score_threshold=0.7,
            )

            matched_fm_missing: set = set()
            matched_fm_extra: set = set()
            for query_leaf, (best_leaf, score) in raw_matches.items():
                if query_leaf == best_leaf:
                    continue
                ref_path = next(
                    (
                        p
                        for p in remaining_missing
                        if p.rsplit("|", 1)[-1] == query_leaf
                    ),
                    None,
                )
                cur_path = next(
                    (p for p in remaining_extra if p.rsplit("|", 1)[-1] == best_leaf),
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

            remaining_missing = [
                p for p in remaining_missing if p not in matched_fm_missing
            ]
            remaining_extra = [p for p in remaining_extra if p not in matched_fm_extra]

            if fuzzy_matches:
                self.logger.debug(
                    f"Detected {len(fuzzy_matches)} fuzzy renamed matches "
                    f"(e.g. {fuzzy_matches[0]['target_name']} ↔ "
                    f"{fuzzy_matches[0]['current_name']} "
                    f"score={fuzzy_matches[0]['score']:.2f})"
                )
        except Exception as e:
            self.logger.debug(f"Fuzzy renamed detection failed: {e}")

        return fuzzy_matches, remaining_missing, remaining_extra

    def _detect_suffix_flattening(
        self,
        remaining_missing: List[str],
        remaining_extra: List[str],
    ) -> Tuple[List[Dict], List[str], List[str]]:
        """Detect FBX name-flattening where parent names are prepended to children.

        e.g. ``BOOSTER_OFF_6_SWITCH`` → ``OVERHEAD_CONSOLE_BOOSTERS_BOOSTER_OFF_6_SWITCH``.
        Matched pairs share the same parent path, and the shorter name is a
        ``_``-delimited suffix of the longer name.

        Returns ``(suffix_matches, remaining_missing, remaining_extra)``.
        """
        suffix_matches: List[Dict] = []
        try:
            if not (remaining_missing and remaining_extra):
                return suffix_matches, remaining_missing, remaining_extra

            def _group_by_parent(paths):
                result: Dict[str, List[Tuple[str, str]]] = {}
                for p in paths:
                    if "|" in p:
                        parent, leaf = p.rsplit("|", 1)
                    else:
                        parent, leaf = "", p
                    result.setdefault(parent, []).append((leaf, p))
                return result

            missing_by_parent = _group_by_parent(remaining_missing)
            extra_by_parent = _group_by_parent(remaining_extra)

            matched_missing: set = set()
            matched_extra: set = set()

            for parent, m_items in missing_by_parent.items():
                e_items = extra_by_parent.get(parent)
                if not e_items:
                    continue
                for m_leaf, m_path in m_items:
                    if m_path in matched_missing:
                        continue
                    for e_leaf, e_path in e_items:
                        if e_path in matched_extra or m_leaf == e_leaf:
                            continue
                        longer, shorter = (
                            (m_leaf, e_leaf)
                            if len(m_leaf) > len(e_leaf)
                            else (e_leaf, m_leaf)
                        )
                        if (
                            longer.endswith(shorter)
                            and longer[len(longer) - len(shorter) - 1] == "_"
                        ):
                            suffix_matches.append(
                                {
                                    "target_name": m_path,
                                    "current_name": e_path,
                                    "score": 1.0,
                                }
                            )
                            matched_missing.add(m_path)
                            matched_extra.add(e_path)
                            break

            if matched_missing:
                remaining_missing = [
                    p for p in remaining_missing if p not in matched_missing
                ]
                remaining_extra = [p for p in remaining_extra if p not in matched_extra]
                self.logger.debug(
                    f"Detected {len(matched_missing)} FBX name-flattening matches (suffix matching)"
                )
        except Exception as e:
            self.logger.debug(f"Suffix matching failed: {e}")

        return suffix_matches, remaining_missing, remaining_extra

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
                    HierarchyManager._unlock_if_stub(current_parent)
                    pm.parent(new_grp, current_parent)
                    HierarchyManager._relock_if_stub(current_parent)
                    HierarchyManager._finalize_stub_node(new_grp)
                    current_parent = new_grp
            else:
                # Root level — use leading pipe for unambiguous lookup
                root_path = f"|{component}"
                if cmds.objExists(root_path):
                    current_parent = pm.PyNode(root_path)
                else:
                    current_parent = pm.createNode("transform", name=component)
                    HierarchyManager._finalize_stub_node(current_parent)
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
                    self._unlock_if_stub(parent)
                    pm.parent(stub, parent)
                    self._relock_if_stub(parent)
                self._finalize_stub_node(stub)
                created.append(stub.nodeName())
                self.logger.debug(f"Created stub: {stub.fullPath()}")
            except Exception as e:
                self.logger.warning(f"Failed to create stub for {cleaned_path}: {e}")

        self.logger.result(f"Created {len(created)} stub transform(s).")
        return created

    # -- stub protection -----------------------------------------------

    #: Outliner colour for stub transforms (muted teal, consistent
    #: with the "informational" palette used in diff formatting).
    STUB_OUTLINER_COLOR = (0.42, 0.55, 0.62)

    #: Custom attribute name used to identify stub transforms.
    STUB_ATTR = "hierarchyManagerStub"

    #: Human-readable note stored on every stub node.
    STUB_NOTE = (
        "Placeholder created by Hierarchy Manager. "
        "This empty transform preserves the reference hierarchy structure. "
        "Locked to prevent accidental deletion by scene-cleanup operations."
    )

    @staticmethod
    def _finalize_stub_node(node) -> None:
        """Tag, colour, and lock a newly created stub transform.

        Applies:
        * A boolean ``hierarchyManagerStub`` attribute (for programmatic
          discovery via ``*.hierarchyManagerStub``).
        * A ``notes`` attribute with a human-readable explanation.
        * A teal outliner colour so stubs are visually distinct.
        * A node lock so Maya's *Optimize Scene Size* cannot delete them.
        """
        name = str(node)
        try:
            # Tag ----------------------------------------------------------
            if not cmds.attributeQuery(
                HierarchyManager.STUB_ATTR, node=name, exists=True
            ):
                cmds.addAttr(name, ln=HierarchyManager.STUB_ATTR, at="bool", dv=True)
                cmds.setAttr(f"{name}.{HierarchyManager.STUB_ATTR}", True)

            # Note ---------------------------------------------------------
            if not cmds.attributeQuery("notes", node=name, exists=True):
                cmds.addAttr(name, ln="notes", dt="string")
            cmds.setAttr(f"{name}.notes", HierarchyManager.STUB_NOTE, type="string")

            # Colour -------------------------------------------------------
            ColorUtils.set_color_attribute(
                node,
                HierarchyManager.STUB_OUTLINER_COLOR,
                attr_type="outliner",
                force=True,
            )

            # Lock ---------------------------------------------------------
            cmds.lockNode(name, lock=True)
        except Exception:
            pass  # best-effort; don't block stub creation

    @staticmethod
    def _unlock_if_stub(node) -> None:
        """Unlock *node* if it was locked by :meth:`_finalize_stub_node`.

        Call before ``pm.delete`` or ``pm.parent`` on a node that may be a
        locked stub.  Safe to call on any node (non-stubs are ignored).
        """
        name = str(node)
        try:
            if cmds.attributeQuery(HierarchyManager.STUB_ATTR, node=name, exists=True):
                cmds.lockNode(name, lock=False)
        except Exception:
            pass

    @staticmethod
    def _relock_if_stub(node) -> None:
        """Re-lock *node* if it carries the stub attribute.

        Call after ``pm.parent`` to restore the protection that was
        temporarily lifted by :meth:`_unlock_if_stub`.
        """
        name = str(node)
        try:
            if cmds.attributeQuery(HierarchyManager.STUB_ATTR, node=name, exists=True):
                cmds.lockNode(name, lock=True)
        except Exception:
            pass

    @staticmethod
    def _has_animation_data(node, check_descendants=False) -> bool:
        """Return True if *node* has animation curves, constraints, or expressions.

        Unlike the former ``_has_animated_ancestor`` (which walked *up* the
        hierarchy checking only keyframes), this checks the node itself for
        a broader set of animation connections:

        * Time-based and set-driven key curves (``animCurve``).
        * Constraints (``parentConstraint``, ``aimConstraint``, …).
        * Expressions.
        * Anim-layer blend nodes (``animBlendNodeBase`` subtypes).

        When *check_descendants* is True the check also recurses into all
        descendants — useful before deleting a parent (the subtree would be
        destroyed too).
        """
        try:
            name = str(node)
            if not cmds.objExists(name):
                return False

            # Direct anim curves (time-based + driven keys)
            if cmds.listConnections(name, type="animCurve", s=True, d=False):
                return True
            # Constraints (inherit from transform, but type filter works)
            if cmds.listRelatives(name, type="constraint"):
                return True
            # Expressions
            if cmds.listConnections(name, type="expression"):
                return True
            # Anim-layer blend nodes
            if cmds.listConnections(name, type="animBlendNodeBase"):
                return True

            if check_descendants:
                descendants = (
                    cmds.listRelatives(
                        name, allDescendents=True, type="transform", fullPath=True
                    )
                    or []
                )
                for desc in descendants:
                    if cmds.listConnections(desc, type="animCurve", s=True, d=False):
                        return True
                    if cmds.listRelatives(desc, type="constraint"):
                        return True
                    if cmds.listConnections(desc, type="expression"):
                        return True
                    if cmds.listConnections(desc, type="animBlendNodeBase"):
                        return True
        except Exception:
            pass
        return False

    @staticmethod
    def _is_locator_transform(node) -> bool:
        """Return True if *node* is a transform with a locator shape child."""
        try:
            # Use fullPath when available to avoid ambiguity with
            # duplicate short names at different hierarchy levels.
            name = node.fullPath() if hasattr(node, "fullPath") else str(node)
            if not cmds.objExists(name):
                return False
            shapes = cmds.listRelatives(name, shapes=True, fullPath=True) or []
            return any(cmds.nodeType(s) == "locator" for s in shapes)
        except Exception:
            return False

    @staticmethod
    def _find_locator_group_root(node):
        """Walk up the hierarchy to find the root of a locator-group chain.

        A locator-group chain is: ``GRP → LOC → children``.  If an object
        sits under a locator (transform with a ``locatorShape``), the
        locator and its parent group form an atomic unit that must be moved
        together.  If the GRP is itself under another locator the walk
        continues upward.

        Returns the root transform of the chain (always the GRP *above*
        the highest locator), or *None* if *node* is not inside a
        locator-group chain.
        """
        try:
            current = node
            root = None

            # If the node itself is a locator, start from its parent GRP
            if HierarchyManager._is_locator_transform(current):
                parent = current.getParent()
                if parent is not None:
                    root = parent
                    current = parent
                else:
                    return current  # locator at world root

            while True:
                parent = current.getParent()
                if parent is None:
                    break
                if HierarchyManager._is_locator_transform(parent):
                    # Parent is a locator — the GRP above it is the root
                    grandparent = parent.getParent()
                    if grandparent is not None:
                        root = grandparent
                        current = grandparent
                        continue  # keep walking up
                    else:
                        # Locator is at world root — locator itself is root
                        root = parent
                        break
                elif root is not None:
                    # Parent is not a locator and we already found a root
                    break
                else:
                    current = parent
                    continue
            return root
        except Exception:
            return None

    def _promote_to_locator_groups(
        self,
        paths: List[str],
        extras_set: Optional[set] = None,
    ) -> List[str]:
        """Promote paths to their locator-group roots if applicable.

        For each cleaned path, resolve it to a live node and check if it
        sits inside a locator-group chain.  If so, replace the path with
        the root of that chain (the GRP above the locator).  Deduplicates
        the result.

        Args:
            paths: Cleaned hierarchy paths (e.g. from ``differences["extra"]``).
            extras_set: Full set of extra paths.  When provided, promotion
                is only applied if the locator-group root is itself extra.
                This prevents quarantining a matched root just because one
                of its locator-group children is extra.

        Returns:
            Deduplicated list of paths with locator-group promotion applied.
        """
        promoted: Dict[str, str] = {}  # original_path -> promoted_path
        for p in paths:
            node = self._resolve_node(p, source="current")
            if not node:
                promoted[p] = p
                continue

            root = self._find_locator_group_root(node)
            if root is not None:
                root_full = root.fullPath().lstrip("|")
                # Find the cleaned path for this root
                root_cleaned = None
                for clean, raw in self.clean_to_raw_current.items():
                    if raw == root_full or raw == root.fullPath():
                        root_cleaned = clean
                        break
                if root_cleaned is None:
                    # Build cleaned path from full path components
                    root_cleaned = clean_hierarchy_path(root_full)

                # Only promote when the root is itself extra.  If the root
                # is a matched/expected node we must not quarantine it.
                if extras_set is not None and root_cleaned not in extras_set:
                    promoted[p] = p
                else:
                    promoted[p] = root_cleaned
            else:
                promoted[p] = p

        # Deduplicate while preserving order
        seen = set()
        result = []
        for orig in paths:
            target = promoted.get(orig, orig)
            if target not in seen:
                seen.add(target)
                result.append(target)
        return result

    @staticmethod
    def _classify_animation(node) -> dict:
        """Return a structured breakdown of animation on *node*.

        Separates time-based anim curves (safe to transfer via
        disconnect/reconnect) from non-transferable connections
        (constraints, driven keys, expressions, anim layers).

        Returns a dict with keys:
            ``curves``        – list of ``(src_plug, dest_plug)`` for time-based curves
            ``driven_keys``   – list of ``(src_plug, dest_plug)`` for set-driven keys
            ``constraints``   – list of constraint node names
            ``expressions``   – list of expression node names
            ``is_referenced`` – True if the node comes from a Maya file reference
            ``has_anim_layers`` – True if anim-layer blend nodes are connected
        """
        name = str(node)
        result = {
            "curves": [],
            "driven_keys": [],
            "constraints": [],
            "expressions": [],
            "is_referenced": False,
            "has_anim_layers": False,
        }
        try:
            pairs = (
                cmds.listConnections(
                    name,
                    type="animCurve",
                    s=True,
                    d=False,
                    connections=True,
                    plugs=True,
                )
                or []
            )
            for i in range(0, len(pairs), 2):
                dest_plug = pairs[i]
                src_plug = pairs[i + 1]
                curve = src_plug.split(".")[0]
                input_conns = (
                    cmds.listConnections(curve + ".input", s=True, d=False) or []
                )
                if input_conns and input_conns[0] != "time1":
                    result["driven_keys"].append((src_plug, dest_plug))
                else:
                    result["curves"].append((src_plug, dest_plug))

            result["constraints"] = cmds.listRelatives(name, type="constraint") or []
            result["expressions"] = list(
                set(cmds.listConnections(name, type="expression") or [])
            )
            try:
                result["is_referenced"] = cmds.referenceQuery(
                    name, isNodeReferenced=True
                )
            except Exception:
                result["is_referenced"] = False
            result["has_anim_layers"] = bool(
                cmds.listConnections(name, type="animBlendNodeBase")
            )
        except Exception:
            pass
        return result

    @staticmethod
    def _transfer_anim_curves(old_node, new_node, logger=None) -> dict:
        """Transfer time-based anim curves from *old_node* to *new_node*.

        Uses a lossless disconnect/reconnect approach for normal nodes,
        falling back to ``copyKey``/``pasteKey`` for referenced nodes
        whose connections cannot be disconnected.

        Returns a dict::

            {"transferred": int,
             "skipped": [{"attr": str, "reason": str}, ...],
             "method": "rewire" | "copyKey"}
        """
        classification = HierarchyManager._classify_animation(old_node)
        old_name, new_name = str(old_node), str(new_node)
        result = {"transferred": 0, "skipped": [], "method": "rewire"}

        # Anim layers — too complex for automatic transfer
        if classification["has_anim_layers"]:
            for src_plug, dest_plug in classification["curves"]:
                attr = dest_plug.split(".")[-1]
                result["skipped"].append(
                    {"attr": attr, "reason": "animation on anim layers"}
                )
            return result

        use_copy = classification["is_referenced"]
        if use_copy:
            result["method"] = "copyKey"

        # Transfer time-based curves
        for src_plug, dest_plug in classification["curves"]:
            attr_name = dest_plug.split(".")[-1]
            new_dest = f"{new_name}.{attr_name}"
            try:
                if not cmds.attributeQuery(attr_name, node=new_name, exists=True):
                    result["skipped"].append(
                        {
                            "attr": attr_name,
                            "reason": "attribute not found on replacement",
                        }
                    )
                    continue

                # Unlock destination if needed
                if cmds.getAttr(new_dest, lock=True):
                    cmds.setAttr(new_dest, lock=False)

                # Remove any existing curves on the replacement attr
                existing_curves = (
                    cmds.listConnections(new_dest, type="animCurve", s=True, d=False)
                    or []
                )
                if existing_curves:
                    cmds.delete(existing_curves)

                if use_copy:
                    cmds.copyKey(old_name, attribute=attr_name)
                    cmds.pasteKey(
                        new_name, attribute=attr_name, option="replaceCompletely"
                    )
                else:
                    cmds.disconnectAttr(src_plug, dest_plug)
                    cmds.connectAttr(src_plug, new_dest, force=True)

                result["transferred"] += 1
            except Exception as exc:
                result["skipped"].append({"attr": attr_name, "reason": str(exc)})

        # Report non-transferable connections
        for src_plug, dest_plug in classification["driven_keys"]:
            attr = dest_plug.split(".")[-1]
            result["skipped"].append({"attr": attr, "reason": "driven key"})
        for c in classification["constraints"]:
            result["skipped"].append({"attr": c, "reason": "constraint"})
        for e in classification["expressions"]:
            result["skipped"].append({"attr": e, "reason": "expression"})

        return result

    def quarantine_extras(
        self,
        group: str = "_QUARANTINE",
        paths: Optional[List[str]] = None,
        skip_animated: bool = True,
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

        # ── Locator-group promotion ──
        # If an extra item sits under a locator (GRP → LOC → children),
        # promote the move target to the GRP so the entire atomic unit
        # stays together.  Re-run ancestor dedup after promotion.
        # Only promote when the root GRP is itself extra — otherwise
        # we would quarantine matched/expected content.
        promoted = self._promote_to_locator_groups(roots_only, extras_set=targets_set)
        if promoted != roots_only:
            promoted_set = set(promoted)
            roots_only = []
            for p in sorted(promoted, key=lambda x: x.count("|")):
                parts = p.split("|")
                if not any(
                    "|".join(parts[: i + 1]) in promoted_set
                    for i in range(len(parts) - 1)
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
                if skip_animated and node and self._has_animation_data(node):
                    self.logger.info(f"[DRY-RUN] Would skip (animated): {p}")
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
            if skip_animated and self._has_animation_data(node):
                skipped_animated.append(cleaned_path)
                continue
            try:
                pm.parent(node, quarantine_grp)
                moved.append(node.nodeName())
                self.logger.debug(f"Quarantined: {node.fullPath()}")
            except Exception as e:
                self.logger.warning(f"Failed to quarantine {cleaned_path}: {e}")

        if skipped_animated:
            for path in skipped_animated:
                self.logger.debug(f"Skipped (animated): {path}")
            self.logger.info(
                f"{len(skipped_animated)} extra(s) skipped (has animation data)."
            )

        self.logger.result(f"Quarantined {len(moved)} item(s) under '{group}'.")
        return moved

    def fix_fuzzy_renames(
        self,
        items: Optional[List[Dict[str, str]]] = None,
        skip_animated: bool = True,
    ) -> List[str]:
        """Rename nodes identified as fuzzy matches to their reference names.

        Each item is a dict with ``current_name`` (cleaned current path) and
        ``target_name`` (cleaned reference path) as produced by
        ``analyze_hierarchies``.

        Returns:
            List of node names that were renamed.
        """
        targets = (
            items if items is not None else self.differences.get("fuzzy_matches", [])
        )
        if not targets:
            self.logger.notice("No fuzzy renames to fix.")
            return []

        renamed: List[str] = []
        for entry in targets:
            current_path = entry.get("current_name", "")
            reference_path = entry.get("target_name", "")
            if not current_path or not reference_path:
                continue

            cur_leaf = current_path.rsplit("|", 1)[-1]
            ref_leaf = reference_path.rsplit("|", 1)[-1]
            if cur_leaf == ref_leaf:
                continue

            if self.dry_run:
                self.logger.info(
                    f"[DRY-RUN] Would rename: '{cur_leaf}' \u2192 '{ref_leaf}'"
                )
                renamed.append(cur_leaf)
                continue

            node = self._resolve_node(current_path, source="current")
            if not node:
                self.logger.debug(f"Rename skipped (node not found): {current_path}")
                continue

            if skip_animated:
                exprs = cmds.listConnections(str(node), type="expression") or []
                if exprs:
                    self.logger.debug(
                        f"Rename skipped (expression-connected): {current_path}"
                    )
                    continue

            try:
                old_name = node.nodeName()
                node.rename(ref_leaf)
                actual_name = node.nodeName()
                renamed.append(actual_name)
                self.logger.debug(f"Renamed: '{old_name}' \u2192 '{actual_name}'")
            except Exception as e:
                self.logger.warning(f"Failed to rename {cur_leaf}: {e}")

        self.logger.result(f"Renamed {len(renamed)} fuzzy-matched item(s).")
        return renamed

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
        skipped_locator: List[str] = []
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

            # Nodes inside a locator-group chain (GRP > LOC > children)
            # must not be reparented individually — that would break
            # the atomic unit.  Skip and log instead.
            if self._find_locator_group_root(node) is not None:
                skipped_locator.append(current_path)
                continue

            try:
                old_parent = node.getParent()
                target_parent = self._ensure_parent_chain(reference_path)
                if target_parent:
                    self._unlock_if_stub(target_parent)
                    pm.parent(node, target_parent)
                    self._relock_if_stub(target_parent)
                else:
                    pm.parent(node, world=True)
                fixed.append(node.nodeName())
                self.logger.debug(f"Reparented: {node.nodeName()} -> {node.fullPath()}")

                # Clean up now-empty source parent (avoids leftover shells)
                if old_parent and old_parent.exists():
                    children = old_parent.getChildren(type="transform")
                    shapes = old_parent.getShapes()
                    if not children and not shapes:
                        if HierarchyManager._has_animation_data(old_parent):
                            self.logger.debug(
                                f"Preserved empty parent "
                                f"'{old_parent.nodeName()}' (has animation data)"
                            )
                        else:
                            old_name = old_parent.nodeName()
                            HierarchyManager._unlock_if_stub(old_parent)
                            pm.delete(old_parent)
                            self.logger.debug(
                                f"Deleted empty source parent: {old_name}"
                            )
            except Exception as e:
                self.logger.warning(f"Failed to reparent {current_path}: {e}")

        if skipped_locator:
            for path in skipped_locator:
                self.logger.debug(f"Reparent skipped (inside locator group): {path}")
            self.logger.info(
                f"{len(skipped_locator)} reparent(s) skipped "
                f"(inside locator-group chains)."
            )

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
        merge = self.pull_mode == "Merge Hierarchies"

        self.logger.debug(
            f"_process_found_objects: pull_children={self.pull_children}, merge={merge}"
        )

        # When pull_children is enabled, filter to only root objects to avoid
        # processing hierarchies multiple times.
        if self.pull_children:
            objects_to_process = self._filter_to_root_objects(found_objects)
            self.logger.debug(
                f"Filtered {len(found_objects)} objects to {len(objects_to_process)} roots"
            )
        else:
            objects_to_process = found_objects

        for i, obj in enumerate(objects_to_process):
            try:
                if not obj.exists():
                    self.logger.warning(f"Object {obj} no longer exists, skipping")
                    continue

                clean_name = get_clean_node_name(obj)
                self.logger.debug(
                    f"Processing [{i}]: {clean_name} (merge={merge}, children={self.pull_children})"
                )
                self._integrate_object(obj, clean_name, merge=merge)
                self.logger.debug(f"Successfully processed: {clean_name}")

            except Exception as e:
                self.logger.error(f"Failed to process object {obj}: {e}")
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

    def _integrate_object(self, obj, clean_name: str, *, merge: bool):
        """Integrate a single imported object into the current scene.

        This is the unified entry point that replaces the former matrix of
        ``_process_with_hierarchy*`` / ``_process_as_root_object`` methods.

        Args:
            obj: PyMEL transform node from the imported namespace.
            clean_name: Namespace-stripped leaf name for the object.
            merge: When True (Merge Hierarchies mode), existing objects with
                the same name are replaced.  When False (Add to Scene mode),
                Maya auto-renames to avoid conflicts.
        """
        allow_auto_rename = not merge

        if self.pull_children:
            # Whole-hierarchy pull: parent root to world, keeping children intact.
            self._integrate_hierarchy(
                obj, clean_name, merge=merge, allow_auto_rename=allow_auto_rename
            )
        else:
            # Single-object pull: rebuild parent chain, then place the object.
            self._integrate_single(
                obj, clean_name, merge=merge, allow_auto_rename=allow_auto_rename
            )

    # -- animation-safe merge helpers ------------------------------------

    def _safe_merge_delete(self, existing, replacement) -> bool:
        """Attempt to safely delete *existing*, transferring animation first.

        Returns True if *existing* was deleted (caller proceeds normally).
        Returns False if *existing* was preserved because it has
        non-transferable animation — the caller should clean up *replacement*.
        """
        name = str(existing)

        if not HierarchyManager._has_animation_data(existing, check_descendants=True):
            # No animation at all — safe to delete unconditionally
            HierarchyManager._unlock_if_stub(existing)
            pm.delete(existing)
            return True

        classification = HierarchyManager._classify_animation(existing)
        has_non_transferable = (
            classification["constraints"]
            or classification["expressions"]
            or classification["driven_keys"]
            or classification["has_anim_layers"]
        )

        # Check descendants for any animation
        descendants = (
            cmds.listRelatives(
                name, allDescendents=True, type="transform", fullPath=True
            )
            or []
        )
        descendant_animated = any(
            HierarchyManager._has_animation_data(d) for d in descendants
        )

        if has_non_transferable or descendant_animated:
            reasons = []
            if classification["constraints"]:
                reasons.append(f"{len(classification['constraints'])} constraint(s)")
            if classification["expressions"]:
                reasons.append(f"{len(classification['expressions'])} expression(s)")
            if classification["driven_keys"]:
                reasons.append(f"{len(classification['driven_keys'])} driven key(s)")
            if classification["has_anim_layers"]:
                reasons.append("anim layers")
            if descendant_animated:
                reasons.append("animated descendants")
            self.logger.info(f"Preserved '{name}' (has {', '.join(reasons)})")
            return False

        # Only transferable (time-based) curves on the root node
        transfer_result = HierarchyManager._transfer_anim_curves(
            existing, replacement, logger=self.logger
        )
        if transfer_result["skipped"]:
            skipped_reasons = [s["reason"] for s in transfer_result["skipped"]]
            self.logger.info(
                f"Preserved '{name}' (could not transfer: "
                f"{', '.join(skipped_reasons)})"
            )
            return False

        self.logger.debug(
            f"Transferred {transfer_result['transferred']} anim curve(s) "
            f"from '{name}' to replacement ({transfer_result['method']})"
        )
        HierarchyManager._unlock_if_stub(existing)
        pm.delete(existing)
        return True

    # -- hierarchy (pull_children=True) --------------------------------

    def _integrate_hierarchy(
        self, obj, clean_name: str, *, merge: bool, allow_auto_rename: bool
    ):
        """Pull an entire hierarchy rooted at *obj* into the scene."""
        try:
            # In merge mode, delete any pre-existing object with the same
            # name so the pulled version can take its place.
            if merge and pm.objExists(clean_name):
                existing = pm.PyNode(clean_name)
                self.logger.debug(f"Replacing existing object: {clean_name}")
                if not self._safe_merge_delete(existing, obj):
                    try:
                        pm.delete(obj)
                    except Exception:
                        pass
                    return

            # Build the parent chain above the root, then parent the object.
            self._build_parent_chain_and_reparent(
                obj, allow_auto_rename=allow_auto_rename
            )

            # Strip temp namespace from the whole subtree + connected materials.
            self._cleanup_namespaces(obj, allow_auto_rename=allow_auto_rename)

            # Final rename of the root if needed (after namespace removal).
            if obj.nodeName() != clean_name:
                obj.rename(clean_name)

        except Exception as e:
            self.logger.warning(f"Hierarchy integration failed for {clean_name}: {e}")
            self.logger.debug(f"Full traceback: {traceback.format_exc()}")
            # Fallback: treat as single-object at scene root.
            self._place_at_root(obj, clean_name)

    # -- single object (pull_children=False) ---------------------------

    def _integrate_single(
        self, obj, clean_name: str, *, merge: bool, allow_auto_rename: bool
    ):
        """Pull a single object (no children) into the scene."""
        try:
            if merge and pm.objExists(clean_name):
                existing = pm.PyNode(clean_name)
                self.logger.debug(f"Replacing existing object: {clean_name}")
                if not self._safe_merge_delete(existing, obj):
                    try:
                        pm.delete(obj)
                    except Exception:
                        pass
                    return

            self._build_parent_chain_and_reparent(
                obj, allow_auto_rename=allow_auto_rename
            )

            # Single objects don't carry a subtree, but may still have a
            # namespace prefix that needs stripping.
            if ":" in obj.nodeName():
                _rename_node_removing_namespace(
                    obj, allow_maya_auto_rename=allow_auto_rename, logger=self.logger
                )

            if obj.nodeName() != clean_name:
                obj.rename(clean_name)
                actual = obj.nodeName()
                if actual != clean_name:
                    self.logger.debug(f"Maya auto-renamed to: {actual}")

        except Exception as e:
            self.logger.warning(
                f"Single-object integration failed for {clean_name}: {e}"
            )
            self._place_at_root(obj, clean_name)

    # -- shared helpers ------------------------------------------------

    def _build_parent_chain_and_reparent(self, obj, *, allow_auto_rename: bool):
        """Build the parent hierarchy for *obj* in the current scene, then reparent it.

        Reads the object's full DAG path, strips namespaces from each
        component, and ensures each ancestor exists.  Finally parents *obj*
        under the deepest ancestor (or at world if there are none).
        """
        original_path = obj.fullPath()
        path_components = [
            get_clean_node_name_from_string(c) for c in original_path.split("|") if c
        ]

        if len(path_components) <= 1:
            # Root-level object — just parent to world.
            pm.parent(obj, world=True)
            return

        # Ensure each ancestor exists (everything except the leaf).
        current_parent = None
        for component_name in path_components[:-1]:
            if pm.objExists(component_name):
                current_parent = pm.PyNode(component_name)
            else:
                parent_obj = pm.createNode("transform", name=component_name)
                if current_parent:
                    pm.parent(parent_obj, current_parent)
                current_parent = parent_obj

        if current_parent:
            pm.parent(obj, current_parent)
        else:
            pm.parent(obj, world=True)

    def _cleanup_namespaces(self, obj, *, allow_auto_rename: bool):
        """Strip temp-import namespaces from *obj*'s hierarchy and materials."""
        if ":" not in obj.nodeName():
            return
        self.logger.debug(f"Removing namespace from hierarchy under {obj.nodeName()}")
        self._remove_namespace_from_hierarchy(
            obj, allow_maya_auto_rename=allow_auto_rename
        )
        self._remove_namespace_from_materials(
            obj, allow_maya_auto_rename=allow_auto_rename
        )

    @staticmethod
    def _place_at_root(obj, clean_name: str):
        """Last-resort fallback: parent *obj* to world and rename."""
        pm.parent(obj, world=True)
        if obj.nodeName() != clean_name:
            obj.rename(clean_name)

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
