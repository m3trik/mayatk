# !/usr/bin/python
# coding=utf-8
"""
Maya hierarchy manager - streamlined to 3 core modules.

This package provides comprehensive hierarchy analysis and cross-scene object
operations for Maya, leveraging generic pythontk.core_utils.hierarchy_utils
for consistent path manipulation and matching strategies.
"""

# Import from consolidated modules
from mayatk.env_utils.hierarchy_manager._hierarchy_manager import (
    HierarchyManager,
    ObjectSwapper,
)
from mayatk.env_utils.hierarchy_manager.hierarchy_manager_slots import (
    HierarchyManagerSlots,
    HierarchyManagerController,
    TreePathMatcher,
)

# --------------------------------------------------------------------------------------------

# Clean public API - only 3 main classes
__all__ = [
    "HierarchyManager",  # Core Maya scene operations
    "ObjectSwapper",  # Cross-scene object operations
    "HierarchyManagerSlots",  # UI handling and Qt integration
    "HierarchyManagerController",  # UI controller logic
    "TreePathMatcher",  # Tree path matching utilities
]

# --------------------------------------------------------------------------------------------
# Streamlined Architecture (3 modules total)
# --------------------------------------------------------------------------------------------
#
# _hierarchy_manager.py:
#   - HierarchyManager: Scene analysis and hierarchy comparison
#   - ObjectSwapper: Cross-scene push/pull operations
#   - MayaObjectMatcher: Maya-specific object matching (internal)
#   - ValidationManager: Input validation and backup (internal)
#   - HierarchyMapBuilder: Path mapping utilities (internal)
#
# hierarchy_manager_slots.py:
#   - HierarchyManagerSlots: Main UI slots and event handling
#   - TreeWidgetMatcher: Qt tree widget operations (internal)
#   - HierarchyManagerController: UI controller logic (internal)
#
# __init__.py:
#   - Clean exports with no implementation overlap
#   - All modules use pythontk.core_utils.hierarchy_utils for consistency
#
# --------------------------------------------------------------------------------------------
