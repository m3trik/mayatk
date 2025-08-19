# !/usr/bin/python
# coding=utf-8
from mayatk.env_utils.hierarchy_manager.slots import HierarchyManagerSlots
from mayatk.env_utils.hierarchy_manager.manager import HierarchyManager
from mayatk.env_utils.hierarchy_manager.swapper import ObjectSwapper


# --------------------------------------------------------------------------------------------

# Make sure these are available when the module is inspected
__all__ = ["HierarchyManager", "ObjectSwapper", "HierarchyManagerSlots"]

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
# This module now uses a simplified structure:
# - manager.py: Unified hierarchy analysis and repair
# - swapper.py: Cross-scene object operations

# --------------------------------------------------------------------------------------------
