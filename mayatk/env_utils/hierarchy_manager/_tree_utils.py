# !/usr/bin/python
# coding=utf-8
"""Tree widget utilities for hierarchy manager UI operations.

Separated from _hierarchy_manager.py to keep Qt imports out of the core module.
"""
from typing import Dict, List, Tuple, Any

from qtpy import QtCore, QtWidgets
import pythontk as ptk
from pythontk.core_utils.hierarchy_utils.hierarchy_matching import HierarchyMatching
from pythontk.core_utils.hierarchy_utils.hierarchy_indexer import HierarchyIndexer

from mayatk.env_utils.hierarchy_manager._hierarchy_manager import (
    clean_hierarchy_path,
    get_clean_node_name_from_string,
)


class TreePathMatcher(ptk.LoggingMixin):
    """Tree path matching functionality for UI tree widgets."""

    def build_tree_index(self, widget):
        """Build tree indices for fast item lookup."""
        items = list(self._iter_items(widget))

        # Build full path index using raw (namespace) names if available
        by_full: Dict[str, Any] = {}
        for item in items:
            raw_path = self._get_item_raw_path(item)
            if raw_path:
                existing = by_full.get(raw_path)
                if existing is None:
                    by_full[raw_path] = item
                else:
                    if not isinstance(existing, list):
                        by_full[raw_path] = [existing]
                    by_full[raw_path].append(item)

        # Build cleaned path index with custom cleaning preserving hierarchy
        by_clean_full: Dict[str, Any] = {}
        for item in items:
            raw_path = self._get_item_raw_path(item)
            if raw_path:
                cleaned_path = clean_hierarchy_path(raw_path)
                existing = by_clean_full.get(cleaned_path)
                if existing is None:
                    by_clean_full[cleaned_path] = item
                else:
                    if not isinstance(existing, list):
                        by_clean_full[cleaned_path] = [existing]
                    by_clean_full[cleaned_path].append(item)

        # Build component index for last component matching
        by_last: Dict[str, list] = {}
        for item in items:
            path = self._get_item_path(item)
            if path:
                last_component = HierarchyMatching._clean_namespace(path.split("|")[-1])
                by_last.setdefault(last_component, []).append(item)

        return by_full, by_clean_full, by_last

    def find_path_matches(
        self,
        target_path: str,
        by_full: dict,
        by_clean_full: dict,
        by_last: dict,
        prefer_cleaned: bool = False,
        strict: bool = False,
    ):
        """Find tree items matching a target path using multiple strategies."""
        cleaned_path = clean_hierarchy_path(target_path)
        last_clean = HierarchyMatching._clean_namespace(target_path.split("|")[-1])

        candidates = []
        strategy = "none"

        if prefer_cleaned:
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
            item = by_full.get(target_path)
            if item is not None:
                candidates = [item]
                strategy = "full"
            if not candidates:
                item = by_clean_full.get(cleaned_path)
                if item is not None:
                    candidates = [item]
                    strategy = "clean_full"

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
        parts = []
        cur = item
        while cur:
            parts.insert(0, cur.text(0))
            cur = cur.parent()
        return HierarchyIndexer._join_hierarchy_path(parts)

    def _get_item_raw_path(self, item) -> str:
        """Extract the full hierarchy path using raw names (with namespaces) if stored."""
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
            f"{tree_type} tree index: {len(by_full)} full, "
            f"{len(by_clean_full)} clean, {len(by_last)} last"
        )


def get_selected_object_names(tree_widget) -> List[str]:
    """Extract object names from selected tree widget items."""
    selected_objects = []
    for item in get_selected_tree_items(tree_widget):
        object_name = _extract_object_name_from_item(item)
        if object_name:
            selected_objects.append(object_name)
    return selected_objects


def get_selected_tree_items(tree_widget) -> list:
    """Get all selected items from tree widget."""
    selected_items = []
    iterator = QtWidgets.QTreeWidgetItemIterator(tree_widget)
    while iterator.value():
        item = iterator.value()
        if item.isSelected():
            selected_items.append(item)
        iterator += 1
    return selected_items


def _extract_object_name_from_item(item) -> str:
    """Extract Maya object name from tree widget item."""
    raw_name = getattr(item, "_raw_name", None)
    if raw_name:
        return raw_name

    parts = []
    current = item
    while current:
        parts.insert(0, current.text(0))
        current = current.parent()

    return "|".join(parts) if len(parts) > 1 else parts[0] if parts else ""


def find_tree_item_by_name(tree_widget, object_name: str):
    """Find tree widget item by object name."""
    iterator = QtWidgets.QTreeWidgetItemIterator(tree_widget)
    while iterator.value():
        item = iterator.value()
        if _extract_object_name_from_item(item) == object_name:
            return item
        iterator += 1
    return None


def build_hierarchy_structure(objects: list) -> Tuple[Dict[str, Dict], List[str]]:
    """Build hierarchical structure from Maya transform objects.

    Keys are the full DAG pipe-path (``|GRP|child``) so duplicate short
    names under different parents are preserved.

    Returns:
        Tuple of (object_items_dict, root_objects_list)
    """
    object_items: Dict[str, dict] = {}
    root_objects: List[str] = []

    for obj in objects:
        try:
            obj_key = obj.fullPath()  # unique DAG path
            obj_name = obj.nodeName()  # short display name
            obj_type = obj.type()
            parent = obj.getParent()

            object_items[obj_key] = {
                "object": obj,
                "short_name": obj_name,
                "type": obj_type,
                "parent": parent.fullPath() if parent else None,
                "item": None,
            }

            if not parent:
                root_objects.append(obj_key)
        except Exception:
            continue

    return object_items, root_objects
