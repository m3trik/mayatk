# !/usr/bin/python
# coding=utf-8
from pythontk.core_utils.module_resolver import bootstrap_package


__package__ = "mayatk"
__version__ = "0.9.51"

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
    "_nurbs_utils": "*",
    "_light_utils": "*",
    # Core utils - specific classes
    "core_utils.components": "Components",
    "core_utils.auto_instancer": "AutoInstancer",
    "core_utils.mash->Mash": ["MashToolkit", "MashNetworkNodes"],
    "core_utils.preview->Preview": "*",
    "core_utils.diagnostics->Diagnostics": "*",
    # Edit utils - specific classes
    "edit_utils.selection": "*",
    "edit_utils.naming": "*",
    "edit_utils.primitives": "*",
    "edit_utils.snap": "*",
    "edit_utils.macros": "Macros",
    "edit_utils.bevel": "*",
    "edit_utils.bridge": "*",
    "edit_utils.cut_on_axis": "*",
    "edit_utils.duplicate_grid": "*",
    "edit_utils.duplicate_linear": "*",
    "edit_utils.duplicate_radial": "*",
    "edit_utils.dynamic_pipe": "*",
    "edit_utils.mirror": "*",
    "edit_utils.mesh_graph": "*",
    # Environment utilities
    "env_utils.command_port": "*",
    "env_utils.workspace_manager": "WorkspaceManager",
    "env_utils.workspace_map": "WorkspaceMap",
    "env_utils.namespace_sandbox": "*",
    "env_utils.reference_manager": "*",
    "env_utils.script_output": "*",
    "env_utils.hierarchy_manager": "*",
    # UI utils
    "ui_utils.maya_menu_handler": "MayaMenuHandler",
    "ui_utils.ui_manager": "UiManager",
    # Transform utils
    "xform_utils.matrices": "Matrices",
    # NURBS utils
    "nurbs_utils.image_tracer": "ImageTracer",
}

bootstrap_package(
    globals(),
    include=DEFAULT_INCLUDE,
)


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
# Test: 222117--------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
# Test: 222117
