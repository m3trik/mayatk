# !/usr/bin/python
# coding=utf-8
from pythontk.core_utils.module_resolver import bootstrap_package


__package__ = "mayatk"
__version__ = "0.9.49"

"""Dynamic Attribute Resolver for Module-based Packages

``bootstrap_package`` wires a :class:`ModuleAttributeResolver` into this package so classes,
methods, and helper APIs (``configure_resolver``, ``build_dictionaries``, ``export_all``,
``import_module``) remain available while keeping this module lean.
"""

# Unified include dictionary supporting both simple modules and nested module paths
DEFAULT_INCLUDE = {
    # Legacy modules - expose all classes using wildcard
    "_anim_utils": "*",
    "_cam_utils": "*",
    "_core_utils": "*",
    "_display_utils": "*",
    "_edit_utils": "*",
    "_env_utils": "*",
    "_mat_utils": "*",
    "_node_utils": "*",
    "_rig_utils": "*",
    "_ui_utils": "*",
    "_uv_utils": "*",
    "_xform_utils": "*",
    # Specific classes from modules
    "components": "Components",
    "matrices": "Matrices",
    "macros": "Macros",
    # Nested ui_utils mappings (explicit for clarity and robustness)
    "ui_utils.maya_menu_handler": "MayaMenuHandler",
    "naming": "Naming",
    "ui_utils.ui_manager": "UiManager",
    # Selection utilities
    "edit_utils.selection": "Selection",
    "edit_utils.primitives": "Primitives",
    # Add hierarchy manager support (these will now work!):
    "env_utils.hierarchy_manager.manager": "HierarchyManager",
    "env_utils.hierarchy_manager.core": ["DiffResult", "RepairAction", "FileFormat"],
    "env_utils.hierarchy_manager.swapper": "ObjectSwapper",
    # Diagnostics utilities
    "core_utils.diagnostic.mesh": "MeshDiagnostics",
    "core_utils.diagnostic.animation": "AnimCurveDiagnostics",
    # Examples of wildcard usage:
    # "some_module": ["*"],  # Expose all classes from some_module
}

DEFAULT_FALLBACKS = {
    "UiManager": "mayatk.ui_utils.ui_manager",
    "MayaMenuHandler": "mayatk.ui_utils.maya_menu_handler",
    "clean_geometry": "mayatk.core_utils.diagnostic.mesh",
    "get_ngons": "mayatk.core_utils.diagnostic.mesh",
    "repair_corrupted_curves": "mayatk.core_utils.diagnostic.animation",
}


bootstrap_package(
    globals(),
    include=DEFAULT_INCLUDE,
    fallbacks=DEFAULT_FALLBACKS,
)

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
# Test: 222117
