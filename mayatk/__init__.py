# !/usr/bin/python
# coding=utf-8
from pythontk.core_utils.module_resolver import bootstrap_package


__package__ = "mayatk"
__version__ = "0.9.89"

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
    # Animation utilities
    "anim_utils.scale_keys": "*",
    "anim_utils.stagger_keys": "*",
    "anim_utils.segment_keys": "SegmentKeys",
    "anim_utils.smart_bake": ["SmartBake", "smart_bake"],
    # Attribute utils
    "node_utils.attributes._attributes": ["Attributes"],
    # Environment utils
    "env_utils.devtools": "*",
    "env_utils.channel_box": "ChannelBox",
    # Core utils - specific classes
    "core_utils.components": "Components",
    "core_utils.instancing.auto_instancer": "AutoInstancer",
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
    "edit_utils.naming._naming": "Naming",
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
    "env_utils.maya_connection": "MayaConnection",
    "env_utils.workspace_manager": "WorkspaceManager",
    "env_utils.workspace_map": "WorkspaceMap",
    "env_utils.namespace_sandbox": "NamespaceSandbox",
    "env_utils.reference_manager": "ReferenceManager",
    "env_utils.script_output": "ScriptOutput",
    "env_utils.hierarchy_manager._hierarchy_manager": [
        "HierarchyManager",
        "ObjectSwapper",
    ],
    "env_utils.fbx_utils": "FbxUtils",
    # Material utils
    "mat_utils.game_shader": "GameShader",
    "mat_utils.render_opacity._render_opacity": "RenderOpacity",
    "mat_utils.mat_updater": "MatUpdater",
    "mat_utils.texture_path_editor": "TexturePathEditor",
    "mat_utils.shader_templates": "ShaderTemplates",
    "mat_utils.mat_manifest": "MatManifest",
    "mat_utils.mat_snapshot": "MatSnapshot",
    # Marmoset Bridge
    "mat_utils.marmoset.bridge": "MarmosetBridge",
    # UI utils
    "ui_utils.maya_native_menus": "MayaNativeMenus",
    "ui_utils.maya_ui_handler": "MayaUiHandler",
    # Transform utils
    "xform_utils.matrices": "Matrices",
    # NURBS utils
    "nurbs_utils.image_tracer": "ImageTracer",
    # Rig utils
    "rig_utils.controls": "Controls",
    "rig_utils.shadow_rig": "ShadowRig",
    # UV utils
    "uv_utils.rizom_bridge._rizom_bridge": "RizomUVBridge",
    # Scene exporter
    "env_utils.scene_exporter._scene_exporter": "SceneExporter",
    "env_utils.scene_exporter.task_manager": "TaskManager",
    "env_utils.scene_exporter.task_factory": "TaskFactory",
}

bootstrap_package(
    globals(),
    include=DEFAULT_INCLUDE,
)


# Configure pythontk ExecutionMonitor to use mayapy
try:
    import sys
    import os
    from pythontk.core_utils.execution_monitor._execution_monitor import (
        ExecutionMonitor,
    )

    # Only configure if running in Maya GUI
    executable = sys.executable
    if os.path.basename(executable).lower() in ["maya.exe", "maya"]:
        # Try using MAYA_LOCATION first as it's most reliable
        maya_location = os.environ.get("MAYA_LOCATION")
        mayapy = None

        if maya_location:
            if sys.platform == "win32":
                mayapy = os.path.join(maya_location, "bin", "mayapy.exe")
            else:
                mayapy = os.path.join(maya_location, "bin", "mayapy")

        # Fallback to looking relative to executable
        if not mayapy or not os.path.exists(mayapy):
            exec_dir = os.path.dirname(executable)
            if sys.platform == "win32":
                mayapy = os.path.join(exec_dir, "mayapy.exe")
            else:
                mayapy = os.path.join(exec_dir, "mayapy")

        if mayapy and os.path.exists(mayapy):
            ExecutionMonitor.set_interpreter(mayapy)
except ImportError:
    pass


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
# Test: 222117--------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
# Test: 222117

print('DEBUG: Loading LOCAL mayatk package')
