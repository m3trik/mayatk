# !/usr/bin/python
# coding=utf-8
from mayatk.env_utils.hierarchy_manager.manager import HierarchyManager
from mayatk.env_utils.hierarchy_manager.swapper import ObjectSwapper
from mayatk.mat_utils.material_preserver import MaterialPreserver

# Import from pythontk for convenience
from pythontk.str_utils import FuzzyMatcher
from pythontk.core_utils import HierarchyDiffResult

# --------------------------------------------------------------------------------------------

# Make sure these are available when the module is inspected
__all__ = [
    "HierarchyManager",
    "HierarchyDiffResult",
    "ObjectSwapper",
    "FuzzyMatcher",
    "MaterialPreserver",
]

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
# This module now uses a simplified structure:
# - manager.py: Unified hierarchy analysis and repair
# - swapper.py: Cross-scene object operations
# - material_preserver.py: Material handling utilities
#
# General-purpose utilities like FuzzyMatcher and HierarchyDiffResult
# have been moved to pythontk for reuse across projects.
# --------------------------------------------------------------------------------------------
