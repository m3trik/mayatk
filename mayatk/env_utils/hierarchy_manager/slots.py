# !/usr/bin/python
# coding=utf-8
import os
import shutil
from pathlib import Path
from typing import Optional, Dict, List, Any

# Third-party imports
import pymel.core as pm
from qtpy import QtCore

# From this package
from mayatk.env_utils.hierarchy_manager.manager import HierarchyManager
from mayatk.env_utils.hierarchy_manager.swapper import ObjectSwapper
from mayatk.env_utils import EnvUtils
import pythontk as ptk


class HierarchyManagerBase(ptk.LoggingMixin):
    """Base class for hierarchy management operations.

    Contains core functionality for hierarchy analysis, tree population,
    and object swapping operations.
    """

    def __init__(self):
        """Initialize the base hierarchy manager."""
        super().__init__()

        # Core components
        self.hierarchy_manager = None
        self.object_swapper = None
        self._current_diff_result = None

    @property
    def workspace(self) -> Optional[str]:
        """Get the current workspace directory."""
        workspace_path = EnvUtils.get_env_info("workspace")
        if not workspace_path:
            self.logger.error("Workspace directory not found.")
        return workspace_path

    def analyze_hierarchies(
        self, reference_path: str, fuzzy_matching: bool = True, dry_run: bool = True
    ) -> bool:
        """Analyze hierarchies and perform comparison.

        Args:
            reference_path: Path to the reference scene file
            fuzzy_matching: Enable fuzzy name matching
            dry_run: Perform analysis without making changes

        Returns:
            bool: True if analysis was successful
        """
        if not reference_path:
            self.logger.error("Please specify a reference scene path.")
            return False

        if not os.path.exists(reference_path):
            self.logger.error(f"Reference scene does not exist: {reference_path}")
            return False

        try:
            # Create hierarchy manager
            self.hierarchy_manager = HierarchyManager(
                reference_file=reference_path,
                fuzzy_matching=fuzzy_matching,
                dry_run=dry_run,
            )

            if not self.hierarchy_manager.is_valid():
                self.logger.error("Failed to initialize hierarchy manager.")
                return False

            # Perform analysis
            self.logger.info("Analyzing hierarchies...")
            self._current_diff_result = self.hierarchy_manager.analyze()

            self.logger.info("Hierarchy analysis completed.")
            return True

        except Exception as e:
            self.logger.error(f"Error during hierarchy analysis: {e}")
            return False

    def pull_objects(
        self,
        object_names: List[str],
        reference_path: str,
        fuzzy_matching: bool = True,
        dry_run: bool = True,
    ) -> bool:
        """Pull objects from reference scene to current scene.

        Args:
            object_names: List of object names to pull
            reference_path: Path to the reference scene
            fuzzy_matching: Enable fuzzy matching
            dry_run: Perform dry run

        Returns:
            bool: True if operation was successful
        """
        if not object_names:
            self.logger.error("No objects specified for pulling.")
            return False

        try:
            # Initialize object swapper if needed
            if not self.object_swapper:
                self.object_swapper = ObjectSwapper(
                    dry_run=dry_run,
                    fuzzy_matching=fuzzy_matching,
                )

            success = self.object_swapper.pull_objects_from_scene(
                target_objects=object_names,
                source_scene_file=reference_path,
                backup=True,
            )

            if success:
                self.logger.success(f"Successfully pulled {len(object_names)} objects.")
                return True
            else:
                self.logger.error("Failed to pull objects from reference scene.")
                return False

        except Exception as e:
            self.logger.error(f"Error pulling objects: {e}")
            return False

    def populate_current_scene_tree(self, tree_widget):
        """Populate the current scene hierarchy tree."""
        try:
            # Get current scene name
            current_scene = pm.sceneName()
            scene_name = "Current Scene"
            if current_scene:
                scene_name = Path(current_scene).stem or "Untitled Scene"

            # Always clear tree before repopulating
            tree_widget.clear()

            # Get all transform nodes in the current scene
            all_transforms = pm.ls(type="transform")

            if not all_transforms:
                tree_widget.setHeaderLabels([scene_name])
                empty_item = tree_widget.create_item(["Empty Scene"])
                self.logger.debug("No transform objects found in current scene.")
                return

            tree_widget.setHeaderLabels([scene_name])
            self.populate_tree_with_hierarchy(tree_widget, all_transforms, "current")

        except Exception as e:
            self.logger.error(f"Error populating current scene tree: {e}")
            tree_widget.clear()
            tree_widget.setHeaderLabels(["Current Scene"])
            error_item = tree_widget.create_item([f"Error: {str(e)}"])

    def populate_reference_tree(
        self,
        tree_widget,
        reference_path: str = None,
        fuzzy_matching: bool = True,
        dry_run: bool = True,
    ):
        """Populate the reference hierarchy tree."""
        # Get reference scene name for header
        reference_name = "Reference Scene"
        if reference_path:
            reference_name = Path(reference_path).stem or "Reference Scene"

        if not self.hierarchy_manager and reference_path:
            # Try to create hierarchy manager if we have a reference path
            if os.path.exists(reference_path):
                try:
                    self.hierarchy_manager = HierarchyManager(
                        reference_file=reference_path,
                        fuzzy_matching=fuzzy_matching,
                        dry_run=dry_run,
                    )

                    if self.hierarchy_manager.is_valid():
                        self._current_diff_result = self.hierarchy_manager.analyze()
                except Exception as e:
                    self.logger.error(f"Failed to create hierarchy manager: {e}")
                    tree_widget.clear()
                    tree_widget.setHeaderLabels([reference_name])
                    error_item = tree_widget.create_item([f"Error: {str(e)}"])
                    return
            else:
                tree_widget.clear()
                tree_widget.setHeaderLabels([reference_name])
                info_item = tree_widget.create_item(["File Not Found"])
                return

        if not self.hierarchy_manager:
            tree_widget.clear()
            tree_widget.setHeaderLabels([reference_name])
            info_item = tree_widget.create_item(["No Reference"])
            return

        try:
            # Get reference hierarchy data from the analyzer
            analyzer = self.hierarchy_manager.analyzer
            if not analyzer or not analyzer.target_map:
                tree_widget.clear()
                tree_widget.setHeaderLabels([reference_name])
                warning_item = tree_widget.create_item(["No Data"])
                return

            # Build hierarchy from the target map
            reference_objects = list(analyzer.target_map.values())

            # Set header with reference scene name
            tree_widget.setHeaderLabels([reference_name])

            # Populate with proper hierarchy
            self.populate_tree_with_hierarchy(
                tree_widget, reference_objects, "reference"
            )

        except Exception as e:
            self.logger.error(f"Error populating reference tree: {e}")
            tree_widget.clear()
            tree_widget.setHeaderLabels([reference_name])
            error_item = tree_widget.create_item([f"Error: {str(e)}"])

    def build_hierarchy_structure(self, objects):
        """Build proper hierarchical tree structure for Maya objects."""
        # First pass: create all items and map them
        object_items = {}  # obj_name -> QTreeWidgetItem
        root_objects = []  # Objects with no parent

        for obj in objects:
            try:
                obj_name = obj.nodeName() if hasattr(obj, "nodeName") else str(obj)
                obj_type = obj.type() if hasattr(obj, "type") else "Unknown"
                parent = obj.getParent() if hasattr(obj, "getParent") else None

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

            except Exception as e:
                self.logger.debug(f"Error processing object {obj}: {e}")
                continue

        return object_items, root_objects

    def populate_tree_with_hierarchy(self, tree_widget, objects, tree_type="current"):
        """Populate tree widget with proper Maya-style hierarchy."""
        try:
            # Don't clear the tree or set headers here - that's done in the calling method

            if not objects:
                # Show empty message
                empty_item = tree_widget.create_item([f"No {tree_type} objects"])
                return

            # Build hierarchy structure
            object_items, root_objects = self.build_hierarchy_structure(objects)

            if not object_items:
                empty_item = tree_widget.create_item(["No Objects"])
                return

            # Create tree items in proper hierarchy order
            created_items = {}

            def create_item_recursive(obj_name, parent_widget_item=None):
                """Recursively create tree items maintaining hierarchy."""
                if obj_name in created_items:
                    return created_items[obj_name]

                obj_info = object_items.get(obj_name)
                if not obj_info:
                    return None

                # Create the tree widget item
                item_data = [obj_name]  # Object name only

                tree_item = tree_widget.create_item(
                    item_data, obj_info["object"], parent_widget_item
                )
                created_items[obj_name] = tree_item

                # Find and create children
                children = [
                    name
                    for name, info in object_items.items()
                    if info["parent"] == obj_name
                ]

                for child_name in sorted(children):  # Sort for consistent order
                    create_item_recursive(child_name, tree_item)

                return tree_item

            # Create root level items first
            for root_name in sorted(root_objects):
                create_item_recursive(root_name)

            # Expand first level by default
            tree_widget.expandToDepth(0)

            self.logger.debug(
                f"Populated {tree_type} tree with {len(objects)} objects in hierarchy."
            )

        except Exception as e:
            self.logger.error(f"Error populating {tree_type} tree with hierarchy: {e}")
            # Show error in tree
            error_item = tree_widget.create_item([f"Error: {str(e)}"])

    def apply_difference_formatting(self, tree001, tree000):
        """Apply color formatting to tree widgets based on hierarchy differences."""
        if not self._current_diff_result:
            return

        try:
            # Apply formatting to current scene tree
            self.format_tree_differences(tree001, "current")

            # Apply formatting to reference tree
            self.format_tree_differences(tree000, "reference")

        except Exception as e:
            self.logger.error(f"Error applying difference formatting: {e}")

    def format_tree_differences(self, tree_widget, tree_type):
        """Format a specific tree widget based on differences."""
        if not self._current_diff_result:
            return

        try:
            # Define formatters for different types of differences
            missing_formatter = tree_widget.make_color_map_formatter(
                {"missing": ("#B97A7A", "#FBEAEA")}  # Red for missing
            )

            extra_formatter = tree_widget.make_color_map_formatter(
                {"extra": ("#B49B5C", "#FFF6DC")}  # Yellow for extra
            )

            fuzzy_formatter = tree_widget.make_color_map_formatter(
                {"fuzzy": ("#6D9BAA", "#E2F3F9")}  # Blue for fuzzy matches
            )

            # Apply formatters based on difference type
            if tree_type == "current":
                # Current scene tree - highlight extra items
                for extra_path in self._current_diff_result.extra:
                    item = self.find_tree_item_by_name(tree_widget, extra_path)
                    if item:
                        tree_widget.set_item_formatter(id(item), extra_formatter)

            elif tree_type == "reference":
                # Reference tree - highlight missing items
                for missing_path in self._current_diff_result.missing:
                    item = self.find_tree_item_by_name(tree_widget, missing_path)
                    if item:
                        tree_widget.set_item_formatter(id(item), missing_formatter)

            # Apply fuzzy match formatting to both trees
            for fuzzy_match in self._current_diff_result.fuzzy_matches:
                current_name = fuzzy_match.get("current_name", "")
                target_name = fuzzy_match.get("target_name", "")

                if tree_type == "current" and current_name:
                    item = self.find_tree_item_by_name(tree_widget, current_name)
                    if item:
                        tree_widget.set_item_formatter(id(item), fuzzy_formatter)

                elif tree_type == "reference" and target_name:
                    item = self.find_tree_item_by_name(tree_widget, target_name)
                    if item:
                        tree_widget.set_item_formatter(id(item), fuzzy_formatter)

            # Apply formatting
            tree_widget.apply_formatting()

        except Exception as e:
            self.logger.error(f"Error formatting tree differences: {e}")

    def find_tree_item_by_name(self, tree_widget, object_name):
        """Find a tree item by object name (first column)."""
        try:
            items = tree_widget.findItems(
                object_name, QtCore.Qt.MatchExactly | QtCore.Qt.MatchRecursive, 0
            )
            return items[0] if items else None
        except Exception as e:
            self.logger.debug(f"Error finding tree item '{object_name}': {e}")
            return None

    def get_selected_tree_items(self, tree_widget):
        """Get selected items from a tree widget."""
        try:
            selected_items = tree_widget.selectedItems()
            return selected_items
        except Exception as e:
            self.logger.debug(f"Error getting selected tree items: {e}")
            return []

    def get_selected_object_names(self, tree_widget):
        """Get object names from selected tree items."""
        try:
            selected_items = tree_widget.selectedItems()
            object_names = []

            for item in selected_items:
                # Get object name from first column
                obj_name = item.text(0)
                if obj_name and obj_name not in [
                    "Empty Scene",
                    "No Reference",
                    "No Data",
                    "Error",
                    "No Objects",
                ]:
                    object_names.append(obj_name)

            return object_names
        except Exception as e:
            self.logger.debug(f"Error getting selected object names: {e}")
            return []

    def select_objects_in_maya(self, object_names: List[str]) -> int:
        """Select objects in Maya scene.

        Args:
            object_names: List of object names to select

        Returns:
            int: Number of objects successfully selected
        """
        valid_objects = []
        for object_name in object_names:
            if pm.objExists(object_name):
                valid_objects.append(object_name)

        if valid_objects:
            pm.select(valid_objects, replace=True)
            self.logger.info(f"Selected {len(valid_objects)} objects in Maya scene.")
            return len(valid_objects)
        else:
            self.logger.warning("No valid objects found to select.")
            return 0


class HierarchyManagerSlots(HierarchyManagerBase):
    """Slots class for hierarchy management UI operations.

    This class provides the interface between the UI and the HierarchyManager/ObjectSwapper
    backend classes. It manages two tree widgets showing current scene and imported hierarchies.
    """

    _log_level_options: Dict[str, Any] = {
        "Log Level: DEBUG": 10,
        "Log Level: INFO": 20,
        "Log Level: WARNING": 30,
        "Log Level: ERROR": 40,
    }

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.hierarchy_manager

        # Initialize UI components
        self.ui.txt001.setText("")  # Reference Scene Path
        self.ui.txt003.setText("")  # Log Output

        # Setup logging
        self.logger.hide_logger_name(False)
        self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        self.logger.setup_logging_redirect(self.ui.txt003)

    def header_init(self, widget):
        """Initialize the header widget."""
        widget.menu.setTitle("Hierarchy Settings:")
        widget.menu.add(
            "QCheckBox",
            setText="Fuzzy Matching",
            setObjectName="chk001",
            setChecked=True,
            setToolTip="Enable fuzzy name matching for hierarchy comparison.",
        )
        widget.menu.add(
            "QCheckBox",
            setText="Dry Run Mode",
            setObjectName="chk002",
            setChecked=True,
            setToolTip="Perform analysis without making actual changes.",
        )
        widget.menu.add(
            self.sb.registered_widgets.ComboBox,
            setObjectName="cmb001",
            add=self._log_level_options,
            setCurrentIndex=1,  # Default to INFO
            setToolTip="Set the log level.",
        )

    def tree000_init(self, widget):
        """Initialize the reference/imported hierarchy tree widget."""
        if not widget.is_initialized:
            widget.menu.setTitle("Reference Hierarchy:")
            widget.menu.mode = "context"
            widget.menu.add(
                "QPushButton",
                setText="Refresh Reference",
                setObjectName="b009",
                setToolTip="Refresh the reference hierarchy display.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Analyze Hierarchies",
                setObjectName="b012",
                setToolTip="Analyze and compare current scene with reference hierarchy.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Show Differences",
                setObjectName="b011",
                setToolTip="Highlight differences between hierarchies.",
            )

            # Mark as initialized to prevent re-adding menu items
            widget.is_initialized = True

        # Populate the reference tree if we have a reference path
        reference_path = self.ui.txt001.text().strip()
        if reference_path and os.path.exists(reference_path):
            # Get settings from UI or use defaults
            fuzzy_matching = getattr(self.ui, "chk001", None)
            fuzzy_matching = fuzzy_matching.isChecked() if fuzzy_matching else True

            dry_run = getattr(self.ui, "chk002", None)
            dry_run = dry_run.isChecked() if dry_run else True

            self.populate_reference_tree(
                widget, reference_path, fuzzy_matching, dry_run
            )
        else:
            # Show empty tree with instruction
            widget.clear()
            widget.setHeaderLabels(["Reference Scene"])
            info_item = widget.create_item(["No Reference Scene"])

    def tree001_init(self, widget):
        """Initialize the current scene hierarchy tree widget."""
        if not widget.is_initialized:
            widget.menu.setTitle("Current Scene Hierarchy:")
            widget.menu.mode = "context"
            widget.menu.add(
                "QPushButton",
                setText="Refresh Current Scene",
                setObjectName="b005",
                setToolTip="Refresh the current scene hierarchy display.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Select Objects",
                setObjectName="b006",
                setToolTip="Select the checked objects in the Maya scene.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Expand All",
                setObjectName="b007",
                setToolTip="Expand all hierarchy items.",
            )
            widget.menu.add(
                "QPushButton",
                setText="Collapse All",
                setObjectName="b008",
                setToolTip="Collapse all hierarchy items.",
            )

            # Mark as initialized to prevent re-adding menu items
            widget.is_initialized = True

        # Always populate the current scene tree when initialized
        self.populate_current_scene_tree(widget)

    def txt001_init(self, widget):
        """Initialize the reference scene path input."""
        widget.menu.add(
            "QPushButton",
            setText="Browse Reference Scene",
            setObjectName="b003",
            setToolTip="Browse for a reference scene file.",
        )
        widget.menu.add(
            "QPushButton",
            setText="Load Recent",
            setObjectName="b004",
            setToolTip="Load from recently used reference scenes.",
        )
        widget.menu.add(
            self.sb.registered_widgets.ComboBox,
            setObjectName="cmb002",
            setToolTip="Select from recent reference scenes.",
        )

        # Load recent reference scenes
        recent_scenes = self._get_recent_reference_scenes()
        self.ui.txt001.menu.cmb002.add(recent_scenes, header="Recent Scenes:")

    def b000(self):
        """Refresh tree widgets with current hierarchy data and differences."""
        # Populate tree widgets
        self.populate_current_scene_tree(self.ui.tree001)

        # Get reference path and settings for reference tree
        reference_path = self.ui.txt001.text().strip()
        fuzzy_matching = getattr(self.ui, "chk001", None)
        fuzzy_matching = fuzzy_matching.isChecked() if fuzzy_matching else True
        dry_run = getattr(self.ui, "chk002", None)
        dry_run = dry_run.isChecked() if dry_run else True

        self.populate_reference_tree(
            self.ui.tree000, reference_path, fuzzy_matching, dry_run
        )

        # Apply formatting based on differences if we have diff results
        if self._current_diff_result:
            self.apply_difference_formatting(self.ui.tree001, self.ui.tree000)
            self.logger.info("Refreshed tree widgets with hierarchy differences.")
        else:
            self.logger.info("Refreshed tree widgets (no comparison data available).")

    def b001(self):
        """Run diff check between current scene and reference hierarchies."""
        reference_path = self.ui.txt001.text().strip()
        if not reference_path:
            self.logger.error("Please specify a reference scene path.")
            return

        # Get settings from UI
        fuzzy_matching = self.ui.chk001.isChecked()
        dry_run = self.ui.chk002.isChecked()

        # Perform hierarchy analysis
        success = self.analyze_hierarchies(reference_path, fuzzy_matching, dry_run)
        if not success:
            return

        # Log diff results
        if self._current_diff_result:
            self.logger.info("Hierarchy Difference Analysis:")

            if self._current_diff_result.missing:
                self.logger.warning(
                    f"Missing in reference ({len(self._current_diff_result.missing)} items):"
                )
                for item in self._current_diff_result.missing[:10]:  # Show first 10
                    self.logger.warning(f"  - {item}")
                if len(self._current_diff_result.missing) > 10:
                    self.logger.warning(
                        f"  ... and {len(self._current_diff_result.missing) - 10} more"
                    )

            if self._current_diff_result.extra:
                self.logger.info(
                    f"Extra in reference ({len(self._current_diff_result.extra)} items):"
                )
                for item in self._current_diff_result.extra[:10]:  # Show first 10
                    self.logger.info(f"  + {item}")
                if len(self._current_diff_result.extra) > 10:
                    self.logger.info(
                        f"  ... and {len(self._current_diff_result.extra) - 10} more"
                    )

            if self._current_diff_result.reparented:
                self.logger.warning(
                    f"Reparented objects ({len(self._current_diff_result.reparented)} items):"
                )
                for item in self._current_diff_result.reparented[:10]:  # Show first 10
                    self.logger.warning(f"  ~ {item}")
                if len(self._current_diff_result.reparented) > 10:
                    self.logger.warning(
                        f"  ... and {len(self._current_diff_result.reparented) - 10} more"
                    )

            if self._current_diff_result.fuzzy_matches:
                self.logger.info(
                    f"Fuzzy matches found ({len(self._current_diff_result.fuzzy_matches)} items):"
                )
                for match in self._current_diff_result.fuzzy_matches[
                    :10
                ]:  # Show first 10
                    current_name = match.get("current_name", "")
                    target_name = match.get("target_name", "")
                    self.logger.info(f"  ≈ {current_name} ↔ {target_name}")
                if len(self._current_diff_result.fuzzy_matches) > 10:
                    self.logger.info(
                        f"  ... and {len(self._current_diff_result.fuzzy_matches) - 10} more"
                    )

            if self._current_diff_result.is_valid():
                self.logger.success("Hierarchies match perfectly!")
            else:
                total_diffs = (
                    len(self._current_diff_result.missing)
                    + len(self._current_diff_result.extra)
                    + len(self._current_diff_result.reparented)
                )
                self.logger.warning(f"Found {total_diffs} hierarchy differences")

        # Refresh tree widgets to show comparison results
        self.b000()

    def b002(self):
        """Pull selected objects from reference scene."""
        if not self.hierarchy_manager:
            self.logger.error("Please analyze hierarchies first.")
            return

        object_names = self.get_selected_object_names(self.ui.tree000)
        if not object_names:
            self.logger.error("Please select objects in the reference hierarchy tree.")
            return

        reference_path = self.ui.txt001.text().strip()
        fuzzy_matching = self.ui.chk001.isChecked()
        dry_run = self.ui.chk002.isChecked()

        success = self.pull_objects(
            object_names, reference_path, fuzzy_matching, dry_run
        )
        if success:
            # Refresh current scene tree
            self.populate_current_scene_tree(self.ui.tree001)

    def b003(self):
        """Browse for reference scene file."""
        reference_file = self.sb.file_dialog(
            file_types="Maya Files (*.ma *.mb);;FBX Files (*.fbx);;All Files (*.*)",
            title="Select Reference Scene:",
            start_dir=self.workspace,
        )

        if reference_file and len(reference_file) > 0:
            self.ui.txt001.setText(reference_file[0])

    def b004(self):
        """Load from recent reference scenes."""
        recent_scenes = self._get_recent_reference_scenes()
        if recent_scenes:
            self.ui.txt001.menu.cmb002.clear()
            self.ui.txt001.menu.cmb002.add(recent_scenes, header="Recent Scenes:")

    def b005(self):
        """Refresh current scene hierarchy tree."""
        self.populate_current_scene_tree(self.ui.tree001)

    def b006(self):
        """Select checked objects in Maya scene."""
        object_names = self.get_selected_object_names(self.ui.tree001)
        if not object_names:
            self.logger.warning("No objects selected in hierarchy tree.")
            return

        self.select_objects_in_maya(object_names)

    def b007(self):
        """Expand all items in current scene tree."""
        self.ui.tree001.expandAll()

    def b008(self):
        """Collapse all items in current scene tree."""
        self.ui.tree001.collapseAll()

    def b009(self):
        """Refresh reference hierarchy tree."""
        reference_path = self.ui.txt001.text().strip()
        fuzzy_matching = getattr(self.ui, "chk001", None)
        fuzzy_matching = fuzzy_matching.isChecked() if fuzzy_matching else True
        dry_run = getattr(self.ui, "chk002", None)
        dry_run = dry_run.isChecked() if dry_run else True

        self.populate_reference_tree(
            self.ui.tree000, reference_path, fuzzy_matching, dry_run
        )

    def b011(self):
        """Show differences between hierarchies."""
        if not self._current_diff_result:
            self.logger.error("Please analyze hierarchies first.")
            return

        self.apply_difference_formatting(self.ui.tree001, self.ui.tree000)
        self.logger.info("Applied difference highlighting to tree widgets.")

    def b012(self):
        """Analyze hierarchies and perform comparison."""
        self.ui.txt003.clear()

        reference_path = self.ui.txt001.text().strip()
        fuzzy_matching = self.ui.chk001.isChecked()
        dry_run = self.ui.chk002.isChecked()
        log_level = self.ui.cmb001.currentData()

        # Set log level
        if log_level:
            self.logger.setLevel(log_level)

        success = self.analyze_hierarchies(reference_path, fuzzy_matching, dry_run)
        if success:
            # Refresh tree widgets with new analysis
            self.b000()

            # Save reference path to recent list
            self._save_recent_reference_scene(reference_path)

    def cmb002(self, index, widget):
        """Handle recent reference scene selection."""
        if index >= 0:
            selected_scene = widget.items[index]
            if selected_scene and os.path.exists(selected_scene):
                self.ui.txt001.setText(selected_scene)
            else:
                self.logger.error(f"Selected scene does not exist: {selected_scene}")

    def _get_recent_reference_scenes(self) -> List[str]:
        """Get recent reference scenes from settings."""
        recent_scenes = self.ui.settings.value("recent_reference_scenes", [])
        # Filter out non-existent files and return last 10
        return [scene for scene in recent_scenes if os.path.exists(scene)][-10:]

    def _save_recent_reference_scene(self, scene_path: str):
        """Save reference scene to recent list."""
        if not scene_path:
            return

        scene_path = str(Path(scene_path).resolve())
        recent_scenes = self.ui.settings.value("recent_reference_scenes", [])

        # Remove if already exists to avoid duplicates
        if scene_path in recent_scenes:
            recent_scenes.remove(scene_path)

        # Add to end and keep only last 10
        recent_scenes.append(scene_path)
        recent_scenes = recent_scenes[-10:]

        # Save to settings
        self.ui.settings.setValue("recent_reference_scenes", recent_scenes)

        # Update combo box
        self.ui.txt001.menu.cmb002.clear()
        self.ui.txt001.menu.cmb002.add(recent_scenes, header="Recent Scenes:")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    # This would typically be instantiated by the UI framework
    print("HierarchyManagerSlots - Use within Maya UI framework")


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------

"""
This slots class provides the interface between the hierarchy management UI and the 
backend HierarchyManager/ObjectSwapper classes.

Key Features:
- Two tree widgets for current scene and reference hierarchy display
- Hierarchy comparison and difference visualization
- Object pulling/pushing between scenes
- Recent reference scene management
- Configurable fuzzy matching and dry-run modes
- Color-coded difference highlighting

UI Components:
- tree001: Current scene hierarchy tree
- tree000: Reference/imported hierarchy tree  
- txt001: Reference scene path input
- txt003: Log output display
- Various buttons for operations and settings

The class follows the same patterns as SceneExporterSlots for consistency
with the existing codebase.
"""
