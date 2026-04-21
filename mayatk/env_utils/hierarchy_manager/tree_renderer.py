# !/usr/bin/python
# coding=utf-8
"""Tree rendering, formatting, and selection management for the hierarchy manager UI.

Extracted from HierarchyManagerController to isolate presentation logic
from orchestration and state management.  All Controller state is accessed
via ``self._ctrl``.
"""
import re
from pathlib import Path
from typing import Dict, List, Any

from qtpy import QtCore, QtWidgets, QtGui
import pythontk as ptk
import pymel.core as pm

import mayatk.env_utils.hierarchy_manager.tree_utils as tree_utils
from mayatk.ui_utils.node_icons import NodeIcons

# Custom data role for storing the original (pre-diff) tooltip so that
# clear_tree_colors can restore it without accumulating stale diff text.
_ORIG_TOOLTIP_ROLE = QtCore.Qt.UserRole + 500


class HierarchyTreeRenderer(ptk.LoggingMixin):
    """Owns all QTreeWidget population, diff-colour formatting, ignore
    styling, selection persistence, and refresh orchestration.

    Constructed by the Controller and given a back-reference so it can
    read (but not own) state such as ``_current_diff_result``,
    ``_importing_reference``, and the ignored-path sets.
    """

    def __init__(self, controller):
        super().__init__()
        self._ctrl = controller
        # Share the controller's log level and redirect target.
        self.set_log_level(controller.logger.level)
        self._ctrl._redirect_logger(self.logger)

    # ------------------------------------------------------------------ #
    # Tree population
    # ------------------------------------------------------------------ #

    def populate_current_scene_tree(self, tree_widget):
        """Populate the current scene hierarchy tree."""
        if getattr(self._ctrl, "_importing_reference", False):
            self.logger.debug(
                "Skipping current scene tree refresh during reference import"
            )
            return

        try:
            current_scene = pm.sceneName()
            scene_name = "Current Scene"
            if current_scene:
                scene_name = Path(current_scene).stem or "Untitled Scene"

            tree_widget.clear()

            all_transforms = pm.ls(type="transform")
            self.logger.debug(
                f"Current scene has {len(all_transforms)} total transforms"
            )

            filtered_transforms = []
            temp_objects = []
            reference_objects = []
            default_cameras = []
            for transform in all_transforms:
                node_name = transform.nodeName()

                if self._ctrl._is_default_maya_camera(transform):
                    default_cameras.append(node_name)
                    continue

                if "temp_import_" in node_name and ":" in node_name:
                    temp_objects.append(node_name)
                    continue

                if self._ctrl._reference_namespaces and ":" in node_name:
                    namespace = node_name.split(":")[0]
                    if namespace in self._ctrl._reference_namespaces:
                        reference_objects.append(node_name)
                        continue

                filtered_transforms.append(transform)

            self.logger.debug(
                f"After filtering: {len(filtered_transforms)} current scene transforms, "
                f"{len(temp_objects)} temp objects, {len(reference_objects)} reference objects, "
                f"{len(default_cameras)} default cameras excluded"
            )
            if temp_objects:
                self.logger.debug(
                    f"Temp objects: {temp_objects[:5]}{'...' if len(temp_objects) > 5 else ''}"
                )
            if reference_objects:
                self.logger.debug(
                    f"Reference objects: {reference_objects[:5]}{'...' if len(reference_objects) > 5 else ''}"
                )
            if default_cameras:
                self.logger.debug(f"Default cameras excluded: {default_cameras}")

            if filtered_transforms:
                transform_names = [t.nodeName() for t in filtered_transforms[:10]]
                self.logger.debug(
                    f"Current scene objects: {transform_names}{'...' if len(filtered_transforms) > 10 else ''}"
                )

            if not filtered_transforms:
                tree_widget.setHeaderLabels([scene_name])
                open_item = tree_widget.create_item(["Open Scene"])
                font = open_item.font(0)
                font.setUnderline(True)
                open_item.setFont(0, font)
                open_item.setForeground(0, QtGui.QBrush(QtGui.QColor("#6699CC")))
                open_item.setData(0, QtCore.Qt.UserRole, "open_scene_placeholder")
                self.logger.debug(
                    "No transform objects found in current scene (excluding temp imports)."
                )
                return

            tree_widget.setHeaderLabels([scene_name])
            tree_widget.blockSignals(True)
            try:
                self.populate_tree_with_hierarchy(
                    tree_widget, filtered_transforms, "current"
                )
            finally:
                tree_widget.blockSignals(False)

        except Exception as e:
            self.logger.error(f"Error populating current scene tree: {e}")
            tree_widget.clear()
            tree_widget.setHeaderLabels(["Current Scene"])
            tree_widget.create_item([f"Error: {str(e)}"])

    def populate_reference_tree(
        self, tree_widget, transforms, reference_name="Reference Scene"
    ):
        """Populate the reference hierarchy tree with pre-fetched transforms.

        Business logic (cache invalidation, data import) is handled by
        ``Controller.populate_reference_tree`` before this method is called.
        """
        tree_widget.clear()
        tree_widget.setHeaderLabels([reference_name])

        if not transforms:
            tree_widget.create_item(["No objects in reference"])
            return

        if self.logger.isEnabledFor(10):  # DEBUG level
            root_count = sum(1 for t in transforms if t.getParent() is None)
            child_count = len(transforms) - root_count
            self.logger.debug(
                f"Reference hierarchy structure: {root_count} roots, "
                f"{child_count} children"
            )
            example_names = [t.nodeName() for t in transforms[:10]]
            self.logger.debug(
                f"Example transforms: {example_names}"
                f"{'...' if len(transforms) > 10 else ''}"
            )

        self.populate_tree_with_hierarchy(tree_widget, transforms, "reference")
        self.logger.debug("Reference tree populated successfully")

    def show_reference_placeholder(self, tree_widget, reference_name="Reference Scene"):
        """Show a 'Browse for Reference Scene' placeholder in an empty tree."""
        tree_widget.clear()
        tree_widget.setHeaderLabels([reference_name])
        info_item = tree_widget.create_item(["Browse for Reference Scene"])
        font = info_item.font(0)
        font.setUnderline(True)
        info_item.setFont(0, font)
        info_item.setForeground(0, QtGui.QBrush(QtGui.QColor("#6699CC")))
        info_item.setData(0, QtCore.Qt.UserRole, "browse_placeholder")

    def show_reference_error(
        self, tree_widget, reference_name="Reference Scene", message="File Not Found"
    ):
        """Show an error or status message in the reference tree."""
        tree_widget.clear()
        tree_widget.setHeaderLabels([reference_name])
        tree_widget.create_item([message])

    def populate_tree_with_hierarchy(self, tree_widget, objects, tree_type="current"):
        """Populate tree widget with proper Maya-style hierarchy."""
        try:
            if not objects:
                tree_widget.create_item([f"No {tree_type} objects"])
                return

            object_items, root_objects = tree_utils.build_hierarchy_structure(objects)

            if not object_items:
                tree_widget.create_item(["No Objects"])
                return

            self.logger.debug(
                f"Tree building for {tree_type}: {len(object_items)} object items, {len(root_objects)} roots"
            )
            self.logger.debug(f"Root objects: {root_objects}")

            created_items: Dict[str, Any] = {}

            def create_item_recursive(obj_key, parent_widget_item=None):
                if obj_key in created_items:
                    return created_items[obj_key]

                obj_info = object_items.get(obj_key)
                if not obj_info:
                    return None

                display_name = obj_info["short_name"]
                if tree_type == "reference" and ":" in display_name:
                    display_name = display_name.split(":")[-1]

                item_data = [display_name, obj_info["type"]]

                tree_item = tree_widget.create_item(
                    item_data, obj_info["object"], parent_widget_item
                )

                icon = NodeIcons.get_icon(obj_key)
                if icon:
                    tree_item.setIcon(0, icon)

                try:
                    tree_item._raw_name = obj_info["short_name"]
                except Exception:
                    pass

                if tree_type == "reference":
                    tree_item.setToolTip(
                        0, f"Full Name: {obj_key}\nType: {obj_info['type']}"
                    )

                created_items[obj_key] = tree_item

                children = [
                    key
                    for key, info in object_items.items()
                    if info["parent"] == obj_key
                ]

                for child_key in sorted(children):
                    create_item_recursive(child_key, tree_item)

                return tree_item

            for root_name in sorted(root_objects):
                create_item_recursive(root_name)

            tree_widget.expandToDepth(0)

            self.logger.debug(
                f"Populated {tree_type} tree with {len(objects)} objects in hierarchy."
            )

        except Exception as e:
            self.logger.error(f"Error populating {tree_type} tree with hierarchy: {e}")
            tree_widget.create_item([f"Error: {str(e)}"])

    # ------------------------------------------------------------------ #
    # Diff formatting
    # ------------------------------------------------------------------ #

    # Desaturated diff colors — derived via Palette.diff()
    DIFF_COLORS = ptk.Palette.diff().alias(
        {
            "missing": "removed",
            "extra": "added",
            "fuzzy": "changed",
            "reparented": "moved",
        }
    )

    def apply_difference_formatting(self, tree001, tree000):
        """Apply color formatting to tree widgets based on hierarchy differences."""
        if not self._ctrl._current_diff_result:
            return

        try:
            for tree_widget in (tree001, tree000):
                self.clear_tree_colors(tree_widget)
                tree_widget.selection_style = "tint"

            tree_matcher = tree_utils.TreePathMatcher()
            self._ctrl._redirect_logger(tree_matcher.logger)

            cur_by_full, cur_by_clean, cur_by_last = tree_matcher.build_tree_index(
                tree001
            )
            ref_by_full, ref_by_clean, ref_by_last = tree_matcher.build_tree_index(
                tree000
            )

            self.format_tree_differences(
                tree001, "current", tree_matcher, cur_by_full, cur_by_clean, cur_by_last
            )

            self.format_tree_differences(
                tree000,
                "reference",
                tree_matcher,
                ref_by_full,
                ref_by_clean,
                ref_by_last,
            )

        except Exception as e:
            self.logger.error(f"Error applying difference formatting: {e}")

    def clear_tree_colors(self, tree_widget):
        """Remove foreground/background colors from every item in a tree widget."""
        try:
            default_brush = QtGui.QBrush()
            iterator = QtWidgets.QTreeWidgetItemIterator(tree_widget)
            while iterator.value():
                item = iterator.value()
                for col in range(tree_widget.columnCount()):
                    item.setForeground(col, default_brush)
                    item.setBackground(col, default_brush)
                # Restore original tooltip (strip diff annotation).
                orig_tip = item.data(0, _ORIG_TOOLTIP_ROLE)
                if orig_tip is not None:
                    item.setToolTip(0, orig_tip)
                    item.setData(0, _ORIG_TOOLTIP_ROLE, None)
                iterator += 1

            # Restore border-based selection style.
            tree_widget.selection_style = "border"

            # Remove leftover transparent-selection stylesheet from earlier runs.
            ss = tree_widget.styleSheet()
            if "selection-background-color: transparent" in ss:
                # Strip the exact old diff-selection block that was appended.
                cleaned = re.sub(
                    r"\s*QTreeWidget\s*\{\s*selection-background-color:\s*transparent;\s*\}"
                    r"\s*QTreeWidget::item:selected\s*\{[^}]*\}"
                    r"\s*QTreeWidget::item:hover:!selected\s*\{[^}]*\}",
                    "",
                    ss,
                )
                tree_widget.setStyleSheet(cleaned.strip())

        except Exception as e:
            self.logger.debug(f"Error clearing tree colors: {e}")

    def format_tree_differences(
        self, tree_widget, tree_type, tree_matcher, by_full, by_clean, by_last
    ):
        """Format a specific tree widget based on differences.

        Uses TreePathMatcher indices for accurate path-based item lookup
        instead of naive leaf-name matching.
        """
        if not self._ctrl._current_diff_result:
            return

        def _find_item(path):
            candidates, strategy = tree_matcher.find_path_matches(
                path,
                by_full,
                by_clean,
                by_last,
                prefer_cleaned=True,
                strict=False,
            )
            if candidates:
                self.logger.debug(
                    f"[DIFF-FMT] {tree_type}: '{path}' -> found via {strategy}"
                )
            else:
                self.logger.debug(f"[DIFF-FMT] {tree_type}: '{path}' -> NOT FOUND")
            return candidates[0] if candidates else None

        def _expand_parents(item):
            parent = item.parent()
            while parent:
                if not parent.isExpanded():
                    parent.setExpanded(True)
                parent = parent.parent()

        try:
            if tree_type == "current":
                for extra_path in self._ctrl._current_diff_result.get("extra", []):
                    item = _find_item(extra_path)
                    if item:
                        self._apply_diff_color(
                            item,
                            "extra",
                            f"Extra — not in reference\n{extra_path}",
                        )
                        _expand_parents(item)

                for reparented in self._ctrl._current_diff_result.get("reparented", []):
                    current_path = reparented.get("current_path", "")
                    ref_path = reparented.get("reference_path", "")
                    if current_path:
                        item = _find_item(current_path)
                        if item:
                            self._apply_diff_color(
                                item,
                                "reparented",
                                f"Reparented — was at:\n{ref_path}",
                            )
                            _expand_parents(item)

            elif tree_type == "reference":
                for missing_path in self._ctrl._current_diff_result.get("missing", []):
                    item = _find_item(missing_path)
                    if item:
                        self._apply_diff_color(
                            item,
                            "missing",
                            f"Missing — not in current scene\n{missing_path}",
                        )
                        _expand_parents(item)

                for reparented in self._ctrl._current_diff_result.get("reparented", []):
                    ref_path = reparented.get("reference_path", "")
                    current_path = reparented.get("current_path", "")
                    if ref_path:
                        item = _find_item(ref_path)
                        if item:
                            self._apply_diff_color(
                                item,
                                "reparented",
                                f"Reparented — now at:\n{current_path}",
                            )
                            _expand_parents(item)

            for fuzzy_match in self._ctrl._current_diff_result.get("fuzzy_matches", []):
                current_name = fuzzy_match.get("current_name", "")
                target_name = fuzzy_match.get("target_name", "")

                if tree_type == "current" and current_name:
                    item = _find_item(current_name)
                    if item:
                        self._apply_diff_color(
                            item,
                            "fuzzy",
                            f"Fuzzy match — reference name:\n{target_name}",
                        )
                        _expand_parents(item)

                elif tree_type == "reference" and target_name:
                    item = _find_item(target_name)
                    if item:
                        self._apply_diff_color(
                            item,
                            "fuzzy",
                            f"Fuzzy match — current name:\n{current_name}",
                        )
                        _expand_parents(item)

        except Exception as e:
            self.logger.error(f"Error formatting tree differences: {e}")

    def _apply_diff_color(self, item, diff_type: str, tooltip: str = ""):
        """Apply desaturated foreground/background to a tree item."""
        pair = self.DIFF_COLORS.get(diff_type)
        if pair is None:
            return
        fg_hex, bg_hex = pair
        if not fg_hex:
            return
        for col in range(item.treeWidget().columnCount()):
            item.setForeground(col, QtGui.QBrush(QtGui.QColor(fg_hex)))
            if bg_hex:
                item.setBackground(col, QtGui.QBrush(QtGui.QColor(bg_hex)))
        if tooltip:
            # Snapshot the original tooltip before the first diff annotation
            # so clear_tree_colors can restore it on re-apply cycles.
            if item.data(0, _ORIG_TOOLTIP_ROLE) is None:
                item.setData(0, _ORIG_TOOLTIP_ROLE, item.toolTip(0) or "")
            original = item.data(0, _ORIG_TOOLTIP_ROLE) or ""
            if original:
                item.setToolTip(0, f"{original}\n\n{tooltip}")
            else:
                item.setToolTip(0, tooltip)

    # ------------------------------------------------------------------ #
    # Ignore styling (visual only — state lives on Controller)
    # ------------------------------------------------------------------ #

    def apply_ignore_styling(self, tree_widget):
        """Apply or remove strikethrough + dim styling for ignored items.

        Directly-ignored items get strikethrough + dim ``#666666``.
        Inherited-ignored items (ancestor is ignored) get italic + ``#888888``.
        """
        ignored = self._ctrl._get_ignored_set(tree_widget)
        if not ignored:
            iterator = QtWidgets.QTreeWidgetItemIterator(tree_widget)
            while iterator.value():
                item = iterator.value()
                font = item.font(0)
                if font.strikeOut() or font.italic():
                    font.setStrikeOut(False)
                    font.setItalic(False)
                    for col in range(tree_widget.columnCount()):
                        item.setFont(col, font)
                iterator += 1
            return

        direct_fg = QtGui.QColor("#666666")
        inherited_fg = QtGui.QColor("#888888")
        iterator = QtWidgets.QTreeWidgetItemIterator(tree_widget)
        while iterator.value():
            item = iterator.value()
            path = self.build_item_path(item)
            font = item.font(0)
            if path in ignored:
                if not font.strikeOut():
                    font.setStrikeOut(True)
                font.setItalic(False)
                for col in range(tree_widget.columnCount()):
                    item.setFont(col, font)
                    item.setForeground(col, QtGui.QBrush(direct_fg))
                    item.setBackground(col, QtGui.QBrush())
            elif self._ctrl.is_path_ignored(tree_widget, path):
                font.setStrikeOut(False)
                font.setItalic(True)
                for col in range(tree_widget.columnCount()):
                    item.setFont(col, font)
                    item.setForeground(col, QtGui.QBrush(inherited_fg))
                    item.setBackground(col, QtGui.QBrush())
            else:
                if font.strikeOut() or font.italic():
                    font.setStrikeOut(False)
                    font.setItalic(False)
                    for col in range(tree_widget.columnCount()):
                        item.setFont(col, font)
            iterator += 1

    # ------------------------------------------------------------------ #
    # Tree item queries
    # ------------------------------------------------------------------ #

    @staticmethod
    def build_item_path(item):
        """Build a pipe-separated hierarchy path from a QTreeWidgetItem."""
        parts = []
        current = item
        while current:
            parts.insert(0, current.text(0))
            current = current.parent()
        return "|".join(parts)

    def find_tree_item_by_name(self, tree_widget, object_name):
        """Find a tree item by object name (first column).

        Handles pipe-separated hierarchy paths by extracting the leaf name.
        """
        try:
            leaf_name = (
                object_name.rsplit("|", 1)[-1] if "|" in object_name else object_name
            )
            items = tree_widget.findItems(
                leaf_name, QtCore.Qt.MatchExactly | QtCore.Qt.MatchRecursive, 0
            )
            return items[0] if items else None
        except Exception as e:
            self.logger.debug(f"Error finding tree item '{object_name}': {e}")
            return None

    def get_selected_tree_items(self, tree_widget):
        """Get selected items from a tree widget."""
        try:
            return tree_widget.selectedItems()
        except Exception as e:
            self.logger.debug(f"Error getting selected tree items: {e}")
            return []

    def get_selected_object_names(self, tree_widget):
        """Extract object names from selected tree widget items."""
        return tree_utils.get_selected_object_names(tree_widget)

    # ------------------------------------------------------------------ #
    # Selection persistence
    # ------------------------------------------------------------------ #

    def _store_tree_selection(self, tree_widget):
        """Store the current selection state of a tree widget."""
        try:
            selected_paths = []
            iterator = QtWidgets.QTreeWidgetItemIterator(tree_widget)
            while iterator.value():
                item = iterator.value()
                if item.isSelected():
                    selected_paths.append(self.build_item_path(item))
                iterator += 1
            return selected_paths
        except Exception as e:
            self.logger.debug(f"Error storing tree selection: {e}")
            return []

    def _restore_tree_selection(self, tree_widget, selected_paths):
        """Restore selection state to a tree widget."""
        try:
            restored_count = 0
            tree_widget.clearSelection()

            for path in selected_paths:
                item = self._find_item_by_path(tree_widget, path)
                if item:
                    item.setSelected(True)
                    restored_count += 1

            return restored_count
        except Exception as e:
            self.logger.debug(f"Error restoring tree selection: {e}")
            return 0

    def _find_item_by_path(self, tree_widget, path):
        """Find a tree item by its hierarchical path."""
        try:
            iterator = QtWidgets.QTreeWidgetItemIterator(tree_widget)
            while iterator.value():
                item = iterator.value()
                if self.build_item_path(item) == path:
                    return item
                iterator += 1
            return None
        except Exception as e:
            self.logger.debug(f"Error finding item by path '{path}': {e}")
            return None

    def _get_tree_structure(self, tree_widget):
        """Get a simplified structure representation of the tree for comparison."""
        try:
            structure = []
            iterator = QtWidgets.QTreeWidgetItemIterator(tree_widget)
            while iterator.value():
                structure.append(self.build_item_path(iterator.value()))
                iterator += 1
            return sorted(structure)
        except Exception as e:
            self.logger.debug(f"Error getting tree structure: {e}")
            return []
