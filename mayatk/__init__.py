# !/usr/bin/python
# coding=utf-8
from pythontk.core_utils.module_resolver import bootstrap_package


__package__ = "mayatk"
__version__ = "0.9.60"

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
    # Env utils
    # Animation utilities
    "anim_utils.scale_keys": "*",
    "anim_utils.stagger_keys": "*",
    "anim_utils.segment_keys": "SegmentKeys",
    # Core utils - specific classes
    "core_utils.components": "Components",
    "core_utils.auto_instancer": "AutoInstancer",
    "core_utils.mash->Mash": "*",
    "core_utils.preview": "Preview",
    "core_utils.diagnostics->Diagnostics": "*",
    "core_utils.diagnostics.scene_diag": [
        "SceneAnalyzer",
        "AuditProfile",
        "SceneDiagnostics",
    ],
    # Edit utils - specific classes
    "edit_utils.selection": "Selection",
    "edit_utils.naming": "Naming",
    "edit_utils.primitives": "Primitives",
    "edit_utils.snap": "Snap",
    "edit_utils.macros": "Macros",
    "edit_utils.bevel": "Bevel",
    "edit_utils.bridge": "Bridge",
    "edit_utils.cut_on_axis": "CutOnAxis",
    "edit_utils.duplicate_grid": "DuplicateGrid",
    "edit_utils.duplicate_linear": "DuplicateLinear",
    "edit_utils.duplicate_radial": "DuplicateRadial",
    "edit_utils.dynamic_pipe": "DynamicPipe",
    "edit_utils.mirror": "Mirror",
    "edit_utils.mesh_graph": "MeshGraph",
    # Environment utilities
    "env_utils.command_port": "CommandPort",
    "env_utils.workspace_manager": "WorkspaceManager",
    "env_utils.workspace_map": "WorkspaceMap",
    "env_utils.namespace_sandbox": "NamespaceSandbox",
    "env_utils.reference_manager": "ReferenceManager",
    "env_utils.script_output": "ScriptOutput",
    "env_utils.hierarchy_manager": "HierarchyManager",
    # Material utils
    "mat_utils.stingray_arnold_shader": "*",
    "mat_utils.texture_path_editor": "TexturePathEditor",
    "mat_utils.shader_templates": "ShaderTemplates",
    # UI utils
    "ui_utils.maya_menu_handler": "MayaMenuHandler",
    "ui_utils.ui_manager": "UiManager",
    # Transform utils
    "xform_utils.matrices": "Matrices",
    # NURBS utils
    "nurbs_utils.image_tracer": "ImageTracer",
    # Rig utils
    "rig_utils.controls": "Controls",
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
