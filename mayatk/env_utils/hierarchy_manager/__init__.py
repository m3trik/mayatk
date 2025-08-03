# !/usr/bin/python
# coding=utf-8

# Try to import the classes, but handle failures gracefully
try:
    from mayatk.env_utils.hierarchy_manager.manager import HierarchyManager

    HIERARCHY_MANAGER_AVAILABLE = True
except ImportError as e:
    HierarchyManager = None
    HIERARCHY_MANAGER_AVAILABLE = False

try:
    from mayatk.env_utils.hierarchy_manager.swapper import ObjectSwapper

    OBJECT_SWAPPER_AVAILABLE = True
except ImportError as e:
    ObjectSwapper = None
    OBJECT_SWAPPER_AVAILABLE = False

# --------------------------------------------------------------------------------------------

# Make sure these are available when the module is inspected
__all__ = [
    "HierarchyManager",
    "ObjectSwapper",
    "HIERARCHY_MANAGER_AVAILABLE",
    "OBJECT_SWAPPER_AVAILABLE",
]

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
# This module now uses a simplified structure:
# - manager.py: Unified hierarchy analysis and repair
# - swapper.py: Cross-scene object operations

# --------------------------------------------------------------------------------------------
