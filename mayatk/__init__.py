# !/usr/bin/python
# coding=utf-8
from pythontk.core_utils.module_resolver import bootstrap_package


__package__ = "mayatk"
__version__ = "0.29.2"

"""Dynamic Attribute Resolver for Module-based Packages

``bootstrap_package`` wires a :class:`ModuleAttributeResolver` into this package so classes,
methods, and helper APIs (``configure_resolver``, ``build_dictionaries``, ``export_all``,
``import_module``) remain available while keeping this module lean.
"""

# Unified include dictionary supporting both simple modules and nested module paths
DEFAULT_INCLUDE = {
    # Namespace main modules (full dotted path, wildcard-scanned). The "*" is
    # deliberate, not legacy laxness: the resolver's wildcard AST scan also
    # publishes each class's public methods as package attributes (e.g.
    # ``mtk.get_parent`` -> ``NodeUtils.get_parent``) — a surface an explicit
    # name list cannot express (explicit entries resolve module-level names
    # only). Prefer explicit full-path + name-list entries for NEW modules.
    "anim_utils._anim_utils": "*",
    "cam_utils._cam_utils": "*",
    "cam_utils.camera_visibility": ["CameraVisibility"],
    "core_utils._core_utils": "*",
    "display_utils._display_utils": "*",
    "edit_utils._edit_utils": "*",
    "edit_utils.target_weld": "TargetWeld",
    "env_utils._env_utils": "*",
    "mat_utils._mat_utils": "*",
    "node_utils._node_utils": "*",
    "node_utils.data_nodes": "*",
    "rig_utils._rig_utils": "*",
    "ui_utils._ui_utils": "*",
    "uv_utils._uv_utils": "*",
    "uv_utils.texture_transfer": "TextureTransfer",
    # Texture Combine engine (its panel is tentacle's shared Texture Combine tool).
    "uv_utils.texture_combine.texture_combine": ["TextureCombine", "CombineInputs"],
    "xform_utils._xform_utils": "*",
    "nurbs_utils._nurbs_utils": "*",
    "light_utils._light_utils": "*",
    # Animation utilities
    "anim_utils.scale_keys": "*",
    "anim_utils.stagger_keys": "*",
    "anim_utils.segment_keys": "SegmentKeys",
    "anim_utils.smart_bake._smart_bake": "SmartBake",
    "anim_utils.world_fit_bake": "WorldFitBake",
    "anim_utils.smart_bake.bake_session": "RestoreResult",
    "anim_utils.shots.shot_sequencer._shot_sequencer": ["ShotSequencer", "ShotBlock"],
    "anim_utils.shots.shot_sequencer.sequencer_host": "SequencerHost",
    # The one store (``ptk.ShotStore``, re-exported) and the Maya half it is
    # injected with.
    "anim_utils.shots._shots": ["ShotStore", "MayaShotsHost"],
    "anim_utils.shots._detection": "Detection",
    "anim_utils.shots.shot_manifest.manifest_host": "ManifestHost",
    # Key stash — key clips parked out of the working animation (Maya adapter
    # over ``ptk.KeyStash``); its panel is tentacle's shared Key Stash tool.
    "anim_utils.key_stash._key_stash": "KeyStash",
    "anim_utils.blendshape_animator._blendshape_animator": "BlendshapeAnimator",
    # Playblast exporter engine + its result records (its panel is tentacle's).
    "anim_utils.playblast_exporter": [
        "PlayblastExporter",
        "ExportTarget",
        "CaptureResult",
        "ExportResult",
    ],
    # Attribute utils
    "node_utils.attributes._attributes": ["Attributes"],
    # The Channels table's API (its panel is tentacle's shared Channels tool).
    "node_utils.attributes.channels._channels": "Channels",
    "audio_utils.audio_clips._audio_clips": "AudioClips",
    # Audio utils
    "audio_utils._audio_utils": "AudioUtils",
    "audio_utils.compositor": "Compositor",
    "audio_utils.segments": "AudioSegment",
    # Core utils - specific classes
    "core_utils.components": "Components",
    "core_utils.auto_instancer._auto_instancer": "AutoInstancer",
    "core_utils.mash->Mash": "*",
    # Every plug-in load goes through one door, by name.
    "core_utils.plugins._plugins": "Plugins",
    "core_utils.preview": "Preview",
    "core_utils.script_job_manager": "ScriptJobManager",
    # OpenMaya edits as one ordinary undo step (Maya's own ufeCmd carries them).
    "core_utils.undo_recorder": "UndoRecorder",
    # Lengths and angles by the kind of query that read them (internal vs working).
    "core_utils.units": "Units",
    "core_utils.diagnostics->Diagnostics": "*",
    "core_utils.diagnostics.scene_diag": "SceneDiagnostics",
    "core_utils.diagnostics.mesh_diag": "MeshDiagnostics",
    "core_utils.diagnostics.transform_diag": "TransformDiagnostics",
    "core_utils.diagnostics.scene_audit": "SceneAnalyzer",
    "core_utils.diagnostics.audit_records": [
        "SceneInfoSection",
        "SceneReport",
        "AnalysisManifest",
        "SummaryStats",
        "BudgetStats",
        "TextureStats",
        "PipelineStats",
        "OffenderLists",
        "AssetRecord",
        "MeshRecord",
        "MaterialRecord",
        "Finding",
        "FixAction",
        "BudgetDelta",
        "ParetoEntry",
        "TextureFile",
        "MissingTexture",
        "SharedTexture",
        "MaterialSplit",
        "MaterialAudit",
        "SceneOverview",
        "SlotStats",
        "InstanceStats",
        "AuditProfile",
    ],
    # Edit utils - specific classes
    "edit_utils.selection": "Selection",
    "edit_utils.naming._naming": "Naming",
    "edit_utils.primitives": "Primitives",
    "edit_utils.snap": "Snap",
    "edit_utils.macros._macros": "Macros",
    "edit_utils.bevel": "Bevel",
    "edit_utils.bridge": "Bridge",
    "edit_utils.cut_on_axis": "CutOnAxis",
    "edit_utils.duplicate_grid": "DuplicateGrid",
    "edit_utils.duplicate_linear": "DuplicateLinear",
    "edit_utils.duplicate_radial": "DuplicateRadial",
    "edit_utils.dynamic_pipe": "DynamicPipe",
    # Curtain: the tool's API (``Curtain``, what the shared panel calls) and its
    # wire rig (``btk.Curtain`` / ``btk.CurtainRig`` twins). The drape engine,
    # ``Rail`` + ``CurtainMesh`` (same names in blendertk), stays unregistered.
    "edit_utils.curtain._curtain": ["Curtain", "CurtainRig"],
    "edit_utils.rack_builder": [
        "RackBuilder",
        "RackSpec",
        "BaySpec",
        "OccupantSpec",
        "EIA310",
    ],
    # Display utils
    "display_utils.color_id": "ColorId",
    "display_utils.exploded_view": "ExplodedView",
    # Environment utilities
    "env_utils.devtools": "*",
    "env_utils.maya_connection": "MayaConnection",
    "env_utils.workspace_manager": "WorkspaceManager",
    "env_utils.workspace_map": "WorkspaceMap",
    "env_utils.namespace_sandbox": "NamespaceSandbox",
    "env_utils.reference_manager._reference_manager": ["ReferenceManager", "AssemblyManager"],
    # ``ScriptConsole`` is the class this module DEFINES; the previous
    # "ScriptOutput" entry named uitk's widget, which the module merely imports
    # — so ``mtk.ScriptOutput`` resolved to a uitk class leaking through this
    # namespace while ``mtk.ScriptConsole`` (what the editors slot calls) did
    # not resolve at all. Mirrors blendertk's ``env_utils.script_output`` entry.
    "env_utils.script_output": "ScriptConsole",
    "env_utils.hierarchy_sync._hierarchy_sync": [
        "HierarchySync",
        "ObjectSwapper",
    ],
    # What the shared Hierarchy Sync panel asks of the scene (its panel is tentacle's).
    "env_utils.hierarchy_sync.hierarchy_sync_session": "HierarchySyncSession",
    "env_utils.hierarchy_sync.scene_data_sidecar": "SceneDataSidecar",
    "env_utils.fbx_utils": "FbxUtils",
    # USD import/export over the native mayaUsd runtime — the USD sibling of
    # FbxUtils (mirrored by blendertk's ``env_utils.usd``: mtk.UsdUtils ↔
    # btk.UsdUtils). Zero-dep USDZ packaging composes pythontk's UsdzPackager.
    "env_utils.usd": "UsdUtils",
    # Blender bridge engine — one-way send of the selection to a fresh Blender (its panel
    # is tentacle's DCC Bridge, the Blender Bridge in Maya).
    # Counterpart of blendertk's ``MayaBridge``.
    "env_utils.blender_bridge._blender_bridge": "BlenderBridge",
    # Pull direction — import a .blend into the CURRENT Maya scene via a headless
    # Blender FBX round-trip. Counterpart of blendertk's ``MayaSceneImport`` /
    # ``btk.import_maya_scene``.
    "env_utils.blender_bridge._scene_import": "BlenderSceneImport",
    # Live browser / WebXR preview — a hand-off bridge whose target is a loopback
    # PreviewServer rather than an app. Counterpart of blendertk's ``WebXrPreview``.
    "env_utils.webxr_preview": "WebXrPreview",
    # Retired 2026-10-20 (removed in 0.29.0): the sections of live-scene state
    # a GLB took beside its FBX -- the materials record states every material.
    "env_utils.scene_state": "SceneState",
    # Unity bridge engine (``btk.UnityBridge`` twin); its panel is tentacle's shared tool.
    "env_utils.unity_bridge._unity_bridge": "UnityBridge",
    # What every bridge panel asks of the host: scope, closure, package install.
    "env_utils.handoff_export": "BridgeHost",
    # Material utils
    "mat_utils.game_shader": "GameShader",
    "light_utils.hdr_manager": "HdrManager",
    "mat_utils.arnold_bridge": "ArnoldBridge",
    "mat_utils.render_opacity.render_effects": "RenderEffects",
    "mat_utils.render_opacity.attribute_mode": "OpacityAttributeMode",
    "mat_utils.image_to_plane._image_to_plane": "ImageToPlane",
    "mat_utils.mat_updater": "MatUpdater",
    # Texture Path Editor's engine (its panel is tentacle's shared tool).
    "mat_utils.texture_path_editor": "TexturePathEditor",
    # ``shader_templates`` is a PACKAGE whose ``__init__`` is docstring-only, so the
    # class has to be named through its inner module, exactly as its
    # ``render_opacity`` / ``image_to_plane`` siblings above do.
    "mat_utils.shader_templates._shader_templates": "ShaderTemplates",
    "mat_utils.mat_manifest": "MatManifest",
    # The ``materials`` record's producer (``FbxUtils.PRODUCERS``): every
    # exported material stated as glTF states it. Mirror of blendertk's.
    "env_utils.material_records": "MaterialRecords",
    "mat_utils.mat_snapshot": "MatSnapshot",
    "mat_utils.shader_converter": "ShaderConverter",
    # Marmoset Bridge
    "mat_utils.marmoset_bridge._marmoset_bridge": "MarmosetBridge",
    # Substance Bridge
    "mat_utils.substance_bridge._substance_bridge": "SubstanceBridge",
    # Texture baking (bake an object's shaded surface to a texture)
    "mat_utils.texture_baker": "TextureBaker",
    # Emissive groups: the API, pythontk's model over the Maya half (its panel is
    # tentacle's).
    "mat_utils.emissive_groups": "EmissiveGroups",
    # Render utils
    "render_utils._render_utils": "RenderUtils",
    # Light utils. Lightmap Baker's and Light Rigs' panels are tentacle's tools
    # (Light Rigs is Maya-only: tentacle's slots/tools HOSTS).
    "light_utils.lightmap_baker.lightmap_baker": "LightmapBaker",
    # The bake's Exclude / Isolate sets (the shared panel's rows read them).
    "mat_utils.bake_sets": ["BakeSourceSet", "LightmapExcludeSet", "LightmapIsolateSet"],
    "light_utils.lightmap_baker.lightmap_records": "LightmapRecords",
    "light_utils.light_rig": "LightRig",
    "light_utils.light_layer_baker": "LightLayerBaker",
    # UI utils
    "ui_utils.calculator": "CalculatorController",
    "ui_utils.cancel_provider": "MayaCancelProvider",
    "ui_utils.channel_box": "ChannelBox",
    "ui_utils.hotkey_collisions": "HotkeyCollisions",
    "ui_utils.maya_native_menus": "MayaNativeMenus",
    "ui_utils.maya_ui_handler": "MayaUiHandler",
    "ui_utils.node_icons": "NodeIcons",
    "ui_utils.style_setter._style_setter": "StyleSetter",
    # Transform utils
    "xform_utils.matrices": "Matrices",
    "xform_utils.pivot_watcher": "PivotWatcher",
    # NURBS utils
    "nurbs_utils.image_tracer": "ImageTracer",
    "nurbs_utils.curve_to_tube": "CurveToTube",
    # Rig utils
    "rig_utils.controls": ["Controls", "ControlNodes"],
    "rig_utils.shadow_rig._shadow_rig": "ShadowRig",
    "rig_utils.shadow_rig.shadow_preview": "ShadowPreview",
    "rig_utils.telescope_rig": "TelescopeRig",
    "rig_utils.wheel_rig": "WheelRig",
    "rig_utils.tube_rig._tube_rig": "TubeRig",
    "rig_utils.articulated_rig._articulated_rig": "ArticulatedRig",
    "rig_utils.articulated_rig.grab_tool": "ArticulatedRigGrab",
    "rig_utils.tube_rig.strategies": ["TubeStrategy", "TubeRigBundle"],
    "rig_utils.tube_rig.tube_path": "TubePath",
    "rig_utils.skinning": ["SkinUtils", "CurveWeights"],
    "rig_utils.rig_graph_extract": "RigGraphExtractor",
    "rig_utils.rig_graph_build": "RigGraphBuilder",
    # UV utils
    "uv_utils.rizom_bridge._rizom_bridge": "RizomUVBridge",
    # Scene exporter
    "env_utils.scene_exporter._scene_exporter": "SceneExporter",
    "env_utils.scene_exporter.task_manager": "TaskManager",
}

#: Public names that left this package (CODE_STANDARD section 5): no alias, so a
#: read raises MovedNameError naming the new home. ``{name: (new home, version)}``;
#: a move adds its names here in the same change set.
MOVED_NAMES = {
    # The curtain drape, written once for both hosts (CurtainMesh subclasses it).
    "CurtainDrape": ("pythontk.CurtainDrape", "0.30.0"),
    "DuplicateRadialSlots": (
        "tentacle.slots.tools.duplicate_radial.DuplicateRadialSlots",
        "0.30.0",
    ),
    "EmissiveGroupsSlots": (
        "tentacle.slots.tools.emissive_groups.EmissiveGroupsSlots",
        "0.30.0",
    ),
    # The Shot Manifest panel: tentacle's slots/tools/shot_manifest.py and its
    # private _shot_manifest/ package (the .ui in ui/tools/).
    "ShotManifestSlots": (
        "tentacle.slots.tools.shot_manifest.ShotManifestSlots",
        "0.30.0",
    ),
    "ShotManifestController": (
        "tentacle.slots.tools._shot_manifest.controller.ShotManifestController",
        "0.30.0",
    ),
    "ExportPreviewMixin": (
        "tentacle.slots.tools._shot_manifest.export_preview.ExportPreviewMixin",
        "0.30.0",
    ),
    "HeaderMenuMixin": (
        "tentacle.slots.tools._shot_manifest.header_menu.HeaderMenuMixin",
        "0.30.0",
    ),
    "ManifestEditor": (
        "tentacle.slots.tools._shot_manifest.manifest_editor.ManifestEditor",
        "0.30.0",
    ),
    "ManifestSourceMixin": (
        "tentacle.slots.tools._shot_manifest.manifest_source.ManifestSourceMixin",
        "0.30.0",
    ),
    "MappingPickerMixin": (
        "tentacle.slots.tools._shot_manifest.mapping_picker.MappingPickerMixin",
        "0.30.0",
    ),
    "RangeColumnMixin": (
        "tentacle.slots.tools._shot_manifest.range_column.RangeColumnMixin",
        "0.30.0",
    ),
    "RowMenusMixin": (
        "tentacle.slots.tools._shot_manifest.row_menus.RowMenusMixin",
        "0.30.0",
    ),
    "StoreFollowMixin": (
        "tentacle.slots.tools._shot_manifest.store_follow.StoreFollowMixin",
        "0.30.0",
    ),
    "ManifestTableMixin": (
        "tentacle.slots.tools._shot_manifest.table_presenter.ManifestTableMixin",
        "0.30.0",
    ),
    "ViewCombosMixin": (
        "tentacle.slots.tools._shot_manifest.view_combos.ViewCombosMixin",
        "0.30.0",
    ),
    "ManifestData": (
        "tentacle.slots.tools._shot_manifest.manifest_data.ManifestData",
        "0.30.0",
    ),
    "SETTINGS_NS": (
        "tentacle.slots.tools._shot_manifest.manifest_data.SETTINGS_NS",
        "0.30.0",
    ),
    "HEADERS": (
        "tentacle.slots.tools._shot_manifest.manifest_data.HEADERS",
        "0.30.0",
    ),
    "COL_STEP": (
        "tentacle.slots.tools._shot_manifest.manifest_data.COL_STEP",
        "0.30.0",
    ),
    "COL_SECTION": (
        "tentacle.slots.tools._shot_manifest.manifest_data.COL_SECTION",
        "0.30.0",
    ),
    "COL_DESC": (
        "tentacle.slots.tools._shot_manifest.manifest_data.COL_DESC",
        "0.30.0",
    ),
    "COL_BEHAVIORS": (
        "tentacle.slots.tools._shot_manifest.manifest_data.COL_BEHAVIORS",
        "0.30.0",
    ),
    "COL_START": (
        "tentacle.slots.tools._shot_manifest.manifest_data.COL_START",
        "0.30.0",
    ),
    "COL_END": (
        "tentacle.slots.tools._shot_manifest.manifest_data.COL_END",
        "0.30.0",
    ),
    "STEP_ICON_COLOR": (
        "tentacle.slots.tools._shot_manifest.manifest_data.STEP_ICON_COLOR",
        "0.30.0",
    ),
    "PASTEL_STATUS": (
        "tentacle.slots.tools._shot_manifest.manifest_data.PASTEL_STATUS",
        "0.30.0",
    ),
    "ERROR_COLOR": (
        "tentacle.slots.tools._shot_manifest.manifest_data.ERROR_COLOR",
        "0.30.0",
    ),
    # Small shared tools (tentacle's slots/tools/<tool>.py, the .ui in ui/tools/).
    "BevelSlots": (
        "tentacle.slots.tools.bevel.BevelSlots",
        "0.30.0",
    ),
    "DuplicateLinearSlots": (
        "tentacle.slots.tools.duplicate_linear.DuplicateLinearSlots",
        "0.30.0",
    ),
    "DuplicateGridSlots": (
        "tentacle.slots.tools.duplicate_grid.DuplicateGridSlots",
        "0.30.0",
    ),
    "ExplodedViewSlots": (
        "tentacle.slots.tools.exploded_view.ExplodedViewSlots",
        "0.30.0",
    ),
    "DynamicPipeSlots": (
        "tentacle.slots.tools.dynamic_pipe.DynamicPipeSlots",
        "0.30.0",
    ),
    "CalculatorSlots": (
        "tentacle.slots.tools.calculator.CalculatorSlots",
        "0.30.0",
    ),
    "SnapSlots": (
        "tentacle.slots.tools.snap.SnapSlots",
        "0.30.0",
    ),
    "CutOnAxisSlots": (
        "tentacle.slots.tools.cut_on_axis.CutOnAxisSlots",
        "0.30.0",
    ),
    "MirrorSlots": (
        "tentacle.slots.tools.mirror.MirrorSlots",
        "0.30.0",
    ),
    "ImageTracerSlots": (
        "tentacle.slots.tools.image_tracer.ImageTracerSlots",
        "0.30.0",
    ),
    "CurveToTubeSlots": (
        "tentacle.slots.tools.curve_to_tube.CurveToTubeSlots",
        "0.30.0",
    ),
    "TelescopeRigSlots": (
        "tentacle.slots.tools.telescope_rig.TelescopeRigSlots",
        "0.30.0",
    ),
    "WheelRigSlots": (
        "tentacle.slots.tools.wheel_rig.WheelRigSlots",
        "0.30.0",
    ),
    "BridgeSlots": (
        "tentacle.slots.tools.bridge.BridgeSlots",
        "0.30.0",
    ),
    "ShellXformSlots": (
        "tentacle.slots.tools.shell_xform.ShellXformSlots",
        "0.30.0",
    ),
    "ColorIdSlots": (
        "tentacle.slots.tools.color_id.ColorIdSlots",
        "0.30.0",
    ),
    "ImageToPlaneSlots": (
        "tentacle.slots.tools.image_to_plane.ImageToPlaneSlots",
        "0.30.0",
    ),
    "KeyStashSlots": (
        "tentacle.slots.tools.key_stash.KeyStashSlots",
        "0.30.0",
    ),
    "SmartBakeSlots": (
        "tentacle.slots.tools.smart_bake.SmartBakeSlots",
        "0.30.0",
    ),
    "GameShaderSlots": (
        "tentacle.slots.tools.game_shader.GameShaderSlots",
        "0.30.0",
    ),
    "MatUpdaterSlots": (
        "tentacle.slots.tools.mat_updater.MatUpdaterSlots",
        "0.30.0",
    ),
    "HdrManagerSlots": (
        "tentacle.slots.tools.hdr_manager.HdrManagerSlots",
        "0.30.0",
    ),
    "ShaderTemplatesSlots": (
        "tentacle.slots.tools.shader_templates.ShaderTemplatesSlots",
        "0.30.0",
    ),
    "ArticulatedRigSlots": (
        "tentacle.slots.tools.articulated_rig.ArticulatedRigSlots",
        "0.30.0",
    ),
    "ShadowRigSlots": (
        "tentacle.slots.tools.shadow_rig.ShadowRigSlots",
        "0.30.0",
    ),
    "CurtainSlots": (
        "tentacle.slots.tools.curtain.CurtainSlots",
        "0.30.0",
    ),
    "NamingSlots": (
        "tentacle.slots.tools.naming.NamingSlots",
        "0.30.0",
    ),
    "TubeRigSlots": (
        "tentacle.slots.tools.tube_rig.TubeRigSlots",
        "0.30.0",
    ),
    # The panel's mode table stayed in the library, beside the strategies it names.
    "RIG_MODES": ("mayatk.rig_utils.tube_rig.strategies.RIG_MODES", "0.30.0"),
    "BlendshapeAnimatorSlots": (
        "tentacle.slots.tools.blendshape_animator.BlendshapeAnimatorSlots",
        "0.30.0",
    ),
    "COL_FRAME": (
        "tentacle.slots.tools.blendshape_animator.COL_FRAME",
        "0.30.0",
    ),
    "COL_NAME": (
        "tentacle.slots.tools.blendshape_animator.COL_NAME",
        "0.30.0",
    ),
    "COL_STATUS": (
        "tentacle.slots.tools.blendshape_animator.COL_STATUS",
        "0.30.0",
    ),
    "COL_TOPOLOGY": (
        "tentacle.slots.tools.blendshape_animator.COL_TOPOLOGY",
        "0.30.0",
    ),
    "COL_WEIGHT": (
        "tentacle.slots.tools.blendshape_animator.COL_WEIGHT",
        "0.30.0",
    ),
    "MODE_FRAME": (
        "tentacle.slots.tools.blendshape_animator.MODE_FRAME",
        "0.30.0",
    ),
    "MODE_WEIGHT": (
        "tentacle.slots.tools.blendshape_animator.MODE_WEIGHT",
        "0.30.0",
    ),
    "LightmapBakerSlots": (
        "tentacle.slots.tools.lightmap_baker.LightmapBakerSlots",
        "0.30.0",
    ),
    "RenderEffectsSlots": (
        "tentacle.slots.tools.render_effects.RenderEffectsSlots",
        "0.30.0",
    ),
    "AudioClipsSlots": (
        "tentacle.slots.tools.audio_clips.AudioClipsSlots",
        "0.30.0",
    ),
    # The Audio Clips panel's two mixins: their scene work is AudioClips' API now
    # (watch, key_all, export_tracks, ...).
    "CallbacksMixin": ("mayatk.AudioClips", "0.30.0"),
    "ExportMixin": ("mayatk.AudioClips", "0.30.0"),
    "SceneExporterSlots": (
        "tentacle.slots.tools.scene_exporter.SceneExporterSlots",
        "0.30.0",
    ),
    "TextureCombineSlots": (
        "tentacle.slots.tools.texture_combine.TextureCombineSlots",
        "0.30.0",
    ),
    "ChannelsSlots": (
        "tentacle.slots.tools.channels.ChannelsSlots",
        "0.30.0",
    ),
    "HierarchySyncSlots": (
        "tentacle.slots.tools.hierarchy_sync.HierarchySyncSlots",
        "0.30.0",
    ),
    # Hierarchy Sync's panel helpers: tentacle's private _hierarchy_sync package.
    "HierarchySyncController": (
        "tentacle.slots.tools._hierarchy_sync.controller.HierarchySyncController",
        "0.30.0",
    ),
    "HierarchyTreeRenderer": (
        "tentacle.slots.tools._hierarchy_sync.tree_renderer.HierarchyTreeRenderer",
        "0.30.0",
    ),
    "TreePathMatcher": (
        "tentacle.slots.tools._hierarchy_sync.tree_matcher.TreePathMatcher",
        "0.30.0",
    ),
    "UnityBridgeSlots": (
        "tentacle.slots.tools.unity_bridge.UnityBridgeSlots",
        "0.30.0",
    ),
    "UnityPanelMixin": (
        "tentacle.slots.tools._unity_bridge._unity_panel.UnityPanelMixin",
        "0.30.0",
    ),
    "ShotsSlots": (
        "tentacle.slots.tools.shots.ShotsSlots",
        "0.30.0",
    ),
    "ShotsController": (
        "tentacle.slots.tools._shots.controller.ShotsController",
        "0.30.0",
    ),
    "ReferenceManagerSlots": (
        "tentacle.slots.tools.reference_manager.ReferenceManagerSlots",
        "0.30.0",
    ),
    "ReferenceManagerController": (
        "tentacle.slots.tools._reference_manager.controller.ReferenceManagerController",
        "0.30.0",
    ),
    "TexturePathEditorSlots": (
        "tentacle.slots.tools.texture_path_editor.TexturePathEditorSlots",
        "0.30.0",
    ),
    "ArnoldBridgeSlots": (
        "tentacle.slots.tools.arnold_bridge.ArnoldBridgeSlots",
        "0.30.0",
    ),
    "LightRigSlots": (
        "tentacle.slots.tools.light_rig.LightRigSlots",
        "0.30.0",
    ),
    "WorkspaceMapSlots": (
        "tentacle.slots.tools.workspaces.WorkspacesSlots",
        "0.30.0",
    ),
    # The Workspace Map's tree controller folded into the shared panel.
    "WorkspaceMapController": (
        "tentacle.slots.tools.workspaces.WorkspacesSlots",
        "0.30.0",
    ),
    # WorkspaceMap became pythontk's WorkspaceMapBase bound to Maya: the
    # Reference Manager's file cache it inherited stays WorkspaceManager's.
    "WorkspaceMap.workspace_files": ("mayatk.WorkspaceManager.workspace_files", "0.30.0"),
    "WorkspaceMap.invalidate_workspace_files": (
        "mayatk.WorkspaceManager.invalidate_workspace_files",
        "0.30.0",
    ),
    "WorkspaceMap.ignore_empty_workspaces": (
        "mayatk.WorkspaceManager.ignore_empty_workspaces",
        "0.30.0",
    ),
    "WorkspaceMap.resolve_file_path": (
        "mayatk.WorkspaceManager.resolve_file_path",
        "0.30.0",
    ),
    "WorkspaceMap.SCENE_FILE_TYPES": ("mayatk.WorkspaceManager.SCENE_FILE_TYPES", "0.30.0"),
    "MarmosetBridgeSlots": (
        "tentacle.slots.tools.marmoset_bridge.MarmosetBridgeSlots",
        "0.30.0",
    ),
    "SubstanceBridgeSlots": (
        "tentacle.slots.tools.substance_bridge.SubstanceBridgeSlots",
        "0.30.0",
    ),
    "RizomBridgeSlots": (
        "tentacle.slots.tools.rizom_bridge.RizomBridgeSlots",
        "0.30.0",
    ),
    "BlenderBridgeSlots": (
        "tentacle.slots.tools.dcc_bridge.DccBridgeSlots",
        "0.30.0",
    ),
    "curves_for_attr": ("mayatk.SequencerHost.curves_for_attr", "0.30.0"),
    "scale_attribute_keys": ("mayatk.SequencerHost.scale_attribute_keys", "0.30.0"),
    "ShotSequencerSlots": (
        "tentacle.slots.tools.shot_sequencer.ShotSequencerSlots",
        "0.30.0",
    ),
    "ShotEditDialog": (
        "tentacle.slots.tools._shot_sequencer.shot_edit_dialog.ShotEditDialog",
        "0.30.0",
    ),
    "ShotSequencerController": (
        "tentacle.slots.tools._shot_sequencer.controller.ShotSequencerController",
        "0.30.0",
    ),
    "ClipMenuMixin": (
        "tentacle.slots.tools._shot_sequencer.clip_menu.ClipMenuMixin",
        "0.30.0",
    ),
    "ClipMotionMixin": (
        "tentacle.slots.tools._shot_sequencer.clip_motion.ClipMotionMixin",
        "0.30.0",
    ),
    "GapManagerMixin": (
        "tentacle.slots.tools._shot_sequencer.gap_manager.GapManagerMixin",
        "0.30.0",
    ),
    "KeyMenuMixin": (
        "tentacle.slots.tools._shot_sequencer.key_menu.KeyMenuMixin",
        "0.30.0",
    ),
    "MarkerManagerMixin": (
        "tentacle.slots.tools._shot_sequencer.marker_manager.MarkerManagerMixin",
        "0.30.0",
    ),
    "SceneCallbacksMixin": (
        "tentacle.slots.tools._shot_sequencer.scene_callbacks.SceneCallbacksMixin",
        "0.30.0",
    ),
    "SceneSelectionMixin": (
        "tentacle.slots.tools._shot_sequencer.scene_selection.SceneSelectionMixin",
        "0.30.0",
    ),
    "ShotLaneMixin": (
        "tentacle.slots.tools._shot_sequencer.shot_lane.ShotLaneMixin",
        "0.30.0",
    ),
    "ShotNavMixin": (
        "tentacle.slots.tools._shot_sequencer.shot_nav.ShotNavMixin",
        "0.30.0",
    ),
    "TransportMixin": (
        "tentacle.slots.tools._shot_sequencer.transport.TransportMixin",
        "0.30.0",
    ),
    "UndoLedgerMixin": (
        "tentacle.slots.tools._shot_sequencer.undo_ledger.UndoLedgerMixin",
        "0.30.0",
    ),
    "WidgetSyncMixin": (
        "tentacle.slots.tools._shot_sequencer.widget_sync.WidgetSyncMixin",
        "0.30.0",
    ),
    "FLOAT_ZERO_EPS": (
        "tentacle.slots.tools._shot_sequencer.clip_motion.FLOAT_ZERO_EPS",
        "0.30.0",
    ),
    "TIME_SNAP_EPS": (
        "tentacle.slots.tools._shot_sequencer.gap_manager.TIME_SNAP_EPS",
        "0.30.0",
    ),
    "MayaBridgeSlotsBase": (
        "tentacle.slots.tools._app_bridge.host_bridge.HostBridge",
        "0.30.0",
    ),
    # The library answers the colour; the Materials panel paints the icon.
    "get_mat_swatch_icon": ("mayatk.MatUtils.get_mat_swatch_color", "0.30.0"),
    # The render-effect channel resolver became its class's own classmethod.
    "spec_for": (
        "mayatk.mat_utils.render_opacity.channels.ChannelSpec.resolve",
        "0.30.0",
    ),
    # ``ShotStore`` is pythontk's one store; its Maya-only member went to the
    # host adapter the store is injected with.
    "ShotStore.undo_queue_top": ("mayatk.MayaShotsHost.undo_queue_top", "0.30.0"),
}

bootstrap_package(
    globals(),
    include=DEFAULT_INCLUDE,
    moved=MOVED_NAMES,
)

