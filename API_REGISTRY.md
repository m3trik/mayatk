# mayatk — API Registry

_Auto-generated. Do not edit by hand. Refresh via `m3trik/scripts/generate_api_registry.py`._

_Generated: 2026-08-10_

## Index

- [`anim_utils/_anim_utils.py`](#anim_utils--_anim_utils)
- [`anim_utils/blendshape_animator/_blendshape_animator.py`](#anim_utils--blendshape_animator--_blendshape_animator) — Main workflow facade for blendShape morph-animation creation, editing, and export.
- [`anim_utils/blendshape_animator/applicator.py`](#anim_utils--blendshape_animator--applicator) — Applies tween mesh edits back to blendShape in-between targets.
- [`anim_utils/blendshape_animator/blendshape_animator_slots.py`](#anim_utils--blendshape_animator--blendshape_animator_slots) — Switchboard slots controller for blendshape_animator.ui.
- [`anim_utils/blendshape_animator/creator.py`](#anim_utils--blendshape_animator--creator) — Creates in-between target meshes for custom blendShape animation curves.
- [`anim_utils/blendshape_animator/helpers.py`](#anim_utils--blendshape_animator--helpers) — Shared helpers internal to the blendshape_animator subpackage.
- [`anim_utils/blendshape_animator/keyframes.py`](#anim_utils--blendshape_animator--keyframes) — Core blendShape keyframe animation operations.
- [`anim_utils/blendshape_animator/recovery.py`](#anim_utils--blendshape_animator--recovery) — Recovery utilities for corrupted blendShape setups.
- [`anim_utils/blendshape_animator/target.py`](#anim_utils--blendshape_animator--target) — Tween mesh wrappers and registry for blendShape in-between targets.
- [`anim_utils/blendshape_animator/validator.py`](#anim_utils--blendshape_animator--validator) — Mesh and blendShape validation for blendShape animation setup.
- [`anim_utils/playblast_exporter.py`](#anim_utils--playblast_exporter) — Playblast capture, encoding, and preview-render exports for Maya.
- [`anim_utils/scale_keys.py`](#anim_utils--scale_keys) — Dedicated scale-keys module to keep AnimUtils lean and testable.
- [`anim_utils/segment_keys.py`](#anim_utils--segment_keys)
- [`anim_utils/shots/_detection.py`](#anim_utils--shots--_detection) — Shot-region detection — Maya scene acquisition over the pure engine math.
- [`anim_utils/shots/_shot_apply.py`](#anim_utils--shots--_shot_apply) — Commit resolved :class:`MovePlan`\ s to the Maya scene.
- [`anim_utils/shots/_shots.py`](#anim_utils--shots--_shots) — Maya shot-store adapter — the DCC layer over ``pythontk``'s shots engine.
- [`anim_utils/shots/shot_manifest/_shot_manifest.py`](#anim_utils--shots--shot_manifest--_shot_manifest) — Maya Shot Manifest adapter — the DCC layer over pythontk's manifest engine.
- [`anim_utils/shots/shot_manifest/behaviors/_behaviors.py`](#anim_utils--shots--shot_manifest--behaviors--_behaviors) — Behaviors — Maya appliers over the engine's pure keying-recipe core.
- [`anim_utils/shots/shot_manifest/manifest_data.py`](#anim_utils--shots--shot_manifest--manifest_data) — Constants, column layout, and pure helper functions for the Shot Manifest UI.
- [`anim_utils/shots/shot_manifest/range_resolver.py`](#anim_utils--shots--shot_manifest--range_resolver) — Range resolution for the Shot Manifest build pipeline (Maya-bound facade).
- [`anim_utils/shots/shot_manifest/shot_manifest_slots.py`](#anim_utils--shots--shot_manifest--shot_manifest_slots) — Switchboard slots for the Shot Manifest UI.
- [`anim_utils/shots/shot_manifest/table_presenter.py`](#anim_utils--shots--shot_manifest--table_presenter) — Tree-widget presentation mixin for the Shot Manifest controller.
- [`anim_utils/shots/shot_sequencer/_shot_sequencer.py`](#anim_utils--shots--shot_sequencer--_shot_sequencer) — Shot Sequencer — manages per-shot animation with ripple editing.
- [`anim_utils/shots/shot_sequencer/clip_motion.py`](#anim_utils--shots--shot_sequencer--clip_motion) — Clip motion, resize, and key-scaling logic for the shot sequencer.
- [`anim_utils/shots/shot_sequencer/gap_manager.py`](#anim_utils--shots--shot_sequencer--gap_manager) — Gap and range-highlight handlers for the shot sequencer controller.
- [`anim_utils/shots/shot_sequencer/marker_manager.py`](#anim_utils--shots--shot_sequencer--marker_manager) — Marker persistence for the shot sequencer controller.
- [`anim_utils/shots/shot_sequencer/segment_collector.py`](#anim_utils--shots--shot_sequencer--segment_collector) — Segment collection and attribute extraction for the shot sequencer.
- [`anim_utils/shots/shot_sequencer/shot_nav.py`](#anim_utils--shots--shot_sequencer--shot_nav) — Shot navigation and combobox synchronization.
- [`anim_utils/shots/shot_sequencer/shot_sequencer_slots.py`](#anim_utils--shots--shot_sequencer--shot_sequencer_slots) — Switchboard slots for the Shot Sequencer UI.
- [`anim_utils/shots/shots_slots.py`](#anim_utils--shots--shots_slots) — Switchboard slots for the Shots settings UI.
- [`anim_utils/smart_bake/_smart_bake.py`](#anim_utils--smart_bake--_smart_bake) — Smart bake module for intelligent pre-bake animation processing.
- [`anim_utils/smart_bake/bake_session.py`](#anim_utils--smart_bake--bake_session) — Persistence and restore engine for SmartBake's nondestructive manifest.
- [`anim_utils/smart_bake/smart_bake_slots.py`](#anim_utils--smart_bake--smart_bake_slots) — Slots for the Smart Bake tool panel (smart_bake.ui).
- [`anim_utils/stagger_keys.py`](#anim_utils--stagger_keys) — Dedicated stagger-keys module to keep AnimUtils lean and testable.
- [`audio_utils/_audio_utils.py`](#audio_utils--_audio_utils) — Unified audio system for Maya scenes.
- [`audio_utils/audio_clips/_audio_clips.py`](#audio_utils--audio_clips--_audio_clips) — Scene-wide audio event manager — thin facade over ``audio_utils``.
- [`audio_utils/audio_clips/audio_clips_slots.py`](#audio_utils--audio_clips--audio_clips_slots) — Switchboard slots for the Audio Clips UI.
- [`audio_utils/audio_clips/callbacks.py`](#audio_utils--audio_clips--callbacks) — Maya event lifecycle and hydration for Audio Clips.
- [`audio_utils/audio_clips/export_ops.py`](#audio_utils--audio_clips--export_ops) — Export operations for Audio Clips.
- [`audio_utils/batch.py`](#audio_utils--batch) — Batch orchestration — undo chunk + dirty-track buffering.
- [`audio_utils/compositor.py`](#audio_utils--compositor) — Compositor — derives DG audio nodes from keyed track events.
- [`audio_utils/migrate.py`](#audio_utils--migrate) — One-shot migration from legacy single-enum carriers to per-track schema.
- [`audio_utils/nodes.py`](#audio_utils--nodes) — Low-level DG audio node primitives.
- [`audio_utils/segments.py`](#audio_utils--segments) — Consumer-facing segment discovery for sequencer + manifest.
- [`cam_utils/_cam_utils.py`](#cam_utils--_cam_utils)
- [`core_utils/_core_utils.py`](#core_utils--_core_utils)
- [`core_utils/auto_instancer/_auto_instancer.py`](#core_utils--auto_instancer--_auto_instancer) — Scene auto-instancer: convert geometrically identical meshes to instances.
- [`core_utils/auto_instancer/assembly_reconstructor.py`](#core_utils--auto_instancer--assembly_reconstructor) — Logic for separating and reassembling mesh assemblies.
- [`core_utils/auto_instancer/geometry_matcher.py`](#core_utils--auto_instancer--geometry_matcher) — Geometry analysis and matching logic for AutoInstancer.
- [`core_utils/auto_instancer/instancing_strategy.py`](#core_utils--auto_instancer--instancing_strategy) — Instancing strategy logic for AutoInstancer.
- [`core_utils/components.py`](#core_utils--components)
- [`core_utils/diagnostics/animation_diag.py`](#core_utils--diagnostics--animation_diag) — Animation-curve diagnostics and optional repair helpers.
- [`core_utils/diagnostics/audit_records.py`](#core_utils--diagnostics--audit_records) — Scene-audit data contract: profiles, per-asset records, and the SceneReport tree.
- [`core_utils/diagnostics/mesh_diag.py`](#core_utils--diagnostics--mesh_diag) — Mesh diagnostics and repair helpers.
- [`core_utils/diagnostics/scene_audit.py`](#core_utils--diagnostics--scene_audit) — Scene audit engine — game-readiness analysis over meshes, materials, and textures.
- [`core_utils/diagnostics/scene_diag.py`](#core_utils--diagnostics--scene_diag) — Scene repair helpers: OCIO / color management, unknown nodes and plugins,
- [`core_utils/diagnostics/transform_diag.py`](#core_utils--diagnostics--transform_diag) — Transform diagnostics and repair helpers.
- [`core_utils/diagnostics/uv_diag.py`](#core_utils--diagnostics--uv_diag) — UV diagnostics and repair helpers.
- [`core_utils/mash.py`](#core_utils--mash)
- [`core_utils/preview.py`](#core_utils--preview) — Hermetic preview with replay-on-commit (H1 design).
- [`core_utils/script_job_manager.py`](#core_utils--script_job_manager) — Centralized Maya event subscription manager.
- [`display_utils/_display_utils.py`](#display_utils--_display_utils)
- [`display_utils/color_id.py`](#display_utils--color_id)
- [`display_utils/exploded_view.py`](#display_utils--exploded_view)
- [`edit_utils/_curtain_drape.py`](#edit_utils--_curtain_drape) — Procedural draped-cloth (curtain) drape engine — pure geometry, no DCC.
- [`edit_utils/_edit_utils.py`](#edit_utils--_edit_utils)
- [`edit_utils/bevel.py`](#edit_utils--bevel)
- [`edit_utils/bridge.py`](#edit_utils--bridge)
- [`edit_utils/curtain.py`](#edit_utils--curtain) — Procedural draped-cloth (curtain) generator for Maya.
- [`edit_utils/cut_on_axis.py`](#edit_utils--cut_on_axis)
- [`edit_utils/duplicate_grid.py`](#edit_utils--duplicate_grid)
- [`edit_utils/duplicate_linear.py`](#edit_utils--duplicate_linear)
- [`edit_utils/duplicate_radial.py`](#edit_utils--duplicate_radial)
- [`edit_utils/dynamic_pipe.py`](#edit_utils--dynamic_pipe)
- [`edit_utils/macros.py`](#edit_utils--macros)
- [`edit_utils/mesh_graph.py`](#edit_utils--mesh_graph)
- [`edit_utils/mirror.py`](#edit_utils--mirror)
- [`edit_utils/naming/_naming.py`](#edit_utils--naming--_naming)
- [`edit_utils/naming/naming_slots.py`](#edit_utils--naming--naming_slots)
- [`edit_utils/primitives.py`](#edit_utils--primitives) — Primitive creation utilities for Maya.
- [`edit_utils/rack_builder.py`](#edit_utils--rack_builder) — Parametric EIA-310 (19-inch) equipment-rack generator.
- [`edit_utils/selection.py`](#edit_utils--selection)
- [`edit_utils/snap.py`](#edit_utils--snap)
- [`env_utils/_env_utils.py`](#env_utils--_env_utils)
- [`env_utils/blender_bridge/_blender_bridge.py`](#env_utils--blender_bridge--_blender_bridge) — Blender bridge engine -- export the Maya selection and run a chosen import template in Blender.
- [`env_utils/blender_bridge/_scene_import.py`](#env_utils--blender_bridge--_scene_import) — Import a Blender scene (.blend) into Maya via a headless-Blender round-trip
- [`env_utils/blender_bridge/blender_bridge_slots.py`](#env_utils--blender_bridge--blender_bridge_slots) — Slots for the Blender bridge panel.
- [`env_utils/blender_bridge/parameters.py`](#env_utils--blender_bridge--parameters) — Registry of user-tunable Blender-bridge parameters exposed to the panel.
- [`env_utils/blender_bridge/templates/_bake_scene.py`](#env_utils--blender_bridge--templates--_bake_scene) — Import a converted intermediate (USD or FBX) headlessly (mayapy) and save it as a ``.ma``
- [`env_utils/blender_bridge/templates/_import_scene.py`](#env_utils--blender_bridge--templates--_import_scene) — Open a .blend headlessly (blender --background) and export it as FBX for a Maya import.
- [`env_utils/blender_bridge/templates/_import_scene_usd.py`](#env_utils--blender_bridge--templates--_import_scene_usd) — Open a .blend headlessly (blender --background) and export it as USD for a Maya import.
- [`env_utils/blender_bridge/templates/_save_scene.py`](#env_utils--blender_bridge--templates--_save_scene) — Import the bridged FBX into a headless Blender and save it as a ``.blend``.
- [`env_utils/blender_bridge/templates/bake_lightmaps.py`](#env_utils--blender_bridge--templates--bake_lightmaps) — Bake the bridged Maya selection's lightmaps in a headless Blender and write a WebXR GLB.
- [`env_utils/blender_bridge/templates/import.py`](#env_utils--blender_bridge--templates--import) — Import the bridged FBX into Blender, with optional clean-slate and frame-on-import behaviors.
- [`env_utils/devtools.py`](#env_utils--devtools)
- [`env_utils/fbx_utils.py`](#env_utils--fbx_utils)
- [`env_utils/handoff_export.py`](#env_utils--handoff_export) — Maya-side selection + FBX-export hooks shared by the hand-off bridge engines.
- [`env_utils/hierarchy_sync/_hierarchy_sync.py`](#env_utils--hierarchy_sync--_hierarchy_sync)
- [`env_utils/hierarchy_sync/hierarchy_sync_slots.py`](#env_utils--hierarchy_sync--hierarchy_sync_slots)
- [`env_utils/hierarchy_sync/scene_data_sidecar.py`](#env_utils--hierarchy_sync--scene_data_sidecar) — Scene-data sidecar manifest management.
- [`env_utils/hierarchy_sync/tree_renderer.py`](#env_utils--hierarchy_sync--tree_renderer) — Tree rendering, formatting, and selection management for the hierarchy sync UI.
- [`env_utils/hierarchy_sync/tree_utils.py`](#env_utils--hierarchy_sync--tree_utils) — Tree widget utilities for hierarchy sync UI operations.
- [`env_utils/maya_connection.py`](#env_utils--maya_connection) — Maya Connection Module
- [`env_utils/namespace_sandbox.py`](#env_utils--namespace_sandbox)
- [`env_utils/reference_manager.py`](#env_utils--reference_manager)
- [`env_utils/scene_exporter/_scene_exporter.py`](#env_utils--scene_exporter--_scene_exporter)
- [`env_utils/scene_exporter/task_manager.py`](#env_utils--scene_exporter--task_manager)
- [`env_utils/scene_state.py`](#env_utils--scene_state) — Read named sections of live-scene state for transport.
- [`env_utils/script_output.py`](#env_utils--script_output)
- [`env_utils/unity_bridge/_unity_bridge.py`](#env_utils--unity_bridge--_unity_bridge) — Unity bridge engine -- export the Maya selection into a Unity project's Assets/.
- [`env_utils/unity_bridge/parameters.py`](#env_utils--unity_bridge--parameters) — User-tunable parameters for the Maya->Unity bridge panel.
- [`env_utils/unity_bridge/unity_bridge_slots.py`](#env_utils--unity_bridge--unity_bridge_slots) — Slots for the Unity bridge panel.
- [`env_utils/usd.py`](#env_utils--usd) — USD import / export over Maya's native ``mayaUsd`` runtime.
- [`env_utils/webxr_preview.py`](#env_utils--webxr_preview) — Push the Maya selection to a live browser / WebXR preview.
- [`env_utils/workspace_manager.py`](#env_utils--workspace_manager)
- [`env_utils/workspace_map.py`](#env_utils--workspace_map)
- [`light_utils/_light_utils.py`](#light_utils--_light_utils)
- [`light_utils/hdr_manager.py`](#light_utils--hdr_manager) — Arnold HDR environment manager.
- [`light_utils/lightmap_baker/lightmap_baker.py`](#light_utils--lightmap_baker--lightmap_baker) — High-level lightmap baking workflow for Maya -> game engines (Unity-first).
- [`mat_utils/_mat_utils.py`](#mat_utils--_mat_utils)
- [`mat_utils/arnold_bridge.py`](#mat_utils--arnold_bridge) — Arnold render-bridge management.
- [`mat_utils/bake_sets.py`](#mat_utils--bake_sets) — Scene-stored bake-source set shared by the hand-off bridges.
- [`mat_utils/emissive_groups.py`](#mat_utils--emissive_groups) — Emissive groups — named face sets that gate emissive regions at runtime.
- [`mat_utils/game_shader.py`](#mat_utils--game_shader)
- [`mat_utils/image_to_plane/_image_to_plane.py`](#mat_utils--image_to_plane--_image_to_plane) — Map image files to textured polygon planes in Maya.
- [`mat_utils/image_to_plane/image_to_plane_slots.py`](#mat_utils--image_to_plane--image_to_plane_slots) — Switchboard slots for the Image to Plane UI.
- [`mat_utils/marmoset_bridge/_marmoset_bridge.py`](#mat_utils--marmoset_bridge--_marmoset_bridge) — Maya-side glue for the Marmoset Toolbag engine.
- [`mat_utils/marmoset_bridge/_marmoset_engine.py`](#mat_utils--marmoset_bridge--_marmoset_engine) — Drive Marmoset Toolbag from the outside -- launch + templated automation.
- [`mat_utils/marmoset_bridge/_toolbag_helpers.py`](#mat_utils--marmoset_bridge--_toolbag_helpers) — Shared helpers for Marmoset Toolbag template scripts.
- [`mat_utils/marmoset_bridge/marmoset_bridge_slots.py`](#mat_utils--marmoset_bridge--marmoset_bridge_slots) — Slots for the Marmoset Toolbag bridge panel.
- [`mat_utils/marmoset_bridge/marmoset_rpc/connection.py`](#mat_utils--marmoset_bridge--marmoset_rpc--connection) — JSON-RPC client bound to the marmoset_rpc Toolbag plugin.
- [`mat_utils/marmoset_bridge/marmoset_rpc/installer.py`](#mat_utils--marmoset_bridge--marmoset_rpc--installer) — Install the marmoset_rpc plugin into Toolbag's user plugin folder.
- [`mat_utils/marmoset_bridge/marmoset_rpc/job.py`](#mat_utils--marmoset_bridge--marmoset_rpc--job) — One-shot batch pipeline for the marmoset_rpc bridge.
- [`mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/__init__.py`](#mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--__init__) — Marmoset Toolbag RPC plugin -- entry point.
- [`mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/_rpc_core.py`](#mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--_rpc_core) — The in-application half of the RPC pair: registry + marshaller + server.
- [`mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/scene_ops.py`](#mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--ops--scene_ops) — Scene-inspection ops.
- [`mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py`](#mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--ops--system_ops) — Toolbag-specific system ops.
- [`mat_utils/marmoset_bridge/parameters.py`](#mat_utils--marmoset_bridge--parameters) — Registry of user-tunable Marmoset Toolbag parameters exposed to the bridge UI.
- [`mat_utils/marmoset_bridge/template_params.py`](#mat_utils--marmoset_bridge--template_params) — Plain default values + literal formatting for Marmoset template tokens.
- [`mat_utils/marmoset_bridge/templates/bake.py`](#mat_utils--marmoset_bridge--templates--bake) — Bake source detail + surface maps onto the target meshes.
- [`mat_utils/marmoset_bridge/templates/import.py`](#mat_utils--marmoset_bridge--templates--import) — Open the model in Toolbag and wire materials from the manifest.
- [`mat_utils/marmoset_bridge/templates/lookdev.py`](#mat_utils--marmoset_bridge--templates--lookdev) — Open the model in Toolbag, apply a Sky preset, and frame the model.
- [`mat_utils/marmoset_bridge/toolbag_log.py`](#mat_utils--marmoset_bridge--toolbag_log) — Marmoset Toolbag log-file resolution, classification, and live tailing.
- [`mat_utils/mat_manifest.py`](#mat_utils--mat_manifest)
- [`mat_utils/mat_snapshot.py`](#mat_utils--mat_snapshot) — Lightweight material state snapshot and restore.
- [`mat_utils/mat_updater.py`](#mat_utils--mat_updater)
- [`mat_utils/render_opacity/_render_opacity.py`](#mat_utils--render_opacity--_render_opacity)
- [`mat_utils/render_opacity/attribute_mode.py`](#mat_utils--render_opacity--attribute_mode)
- [`mat_utils/render_opacity/material_mode.py`](#mat_utils--render_opacity--material_mode)
- [`mat_utils/render_opacity/render_opacity_slots.py`](#mat_utils--render_opacity--render_opacity_slots) — Switchboard slots for the Render Opacity UI.
- [`mat_utils/shader_attribute_map.py`](#mat_utils--shader_attribute_map) — Logical texture channel -> per-shader (attribute, output plug), and the one
- [`mat_utils/shader_converter.py`](#mat_utils--shader_converter) — Retype a material in place — legacy Maya shaders to an exportable PBR one.
- [`mat_utils/shader_templates/_shader_templates.py`](#mat_utils--shader_templates--_shader_templates)
- [`mat_utils/substance_bridge/_substance_bridge.py`](#mat_utils--substance_bridge--_substance_bridge) — Substance 3D Painter bridge -- export Maya selection and hand off to Painter.
- [`mat_utils/substance_bridge/connection.py`](#mat_utils--substance_bridge--connection) — Substance 3D Painter connection module.
- [`mat_utils/substance_bridge/parameters.py`](#mat_utils--substance_bridge--parameters) — Registry of user-tunable Substance Painter parameters exposed to the bridge UI.
- [`mat_utils/substance_bridge/substance_bridge_slots.py`](#mat_utils--substance_bridge--substance_bridge_slots) — Slots for the Substance Painter bridge panel.
- [`mat_utils/substance_bridge/substance_rpc/client.py`](#mat_utils--substance_bridge--substance_rpc--client) — HTTP RPC client for the Painter-side ``substance_rpc`` plugin.
- [`mat_utils/substance_bridge/substance_rpc/installer.py`](#mat_utils--substance_bridge--substance_rpc--installer) — Install the substance_rpc plugin into Painter's user plugin folder.
- [`mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/__init__.py`](#mat_utils--substance_bridge--substance_rpc--plugin_src--substance_rpc--__init__) — Substance 3D Painter RPC plugin -- entry point.
- [`mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/_rpc_core.py`](#mat_utils--substance_bridge--substance_rpc--plugin_src--substance_rpc--_rpc_core) — The in-application half of the RPC pair: registry + marshaller + server.
- [`mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/project_ops.py`](#mat_utils--substance_bridge--substance_rpc--plugin_src--substance_rpc--ops--project_ops) — Project-level ops: inspect the open project and reload its mesh.
- [`mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/setup_ops.py`](#mat_utils--substance_bridge--substance_rpc--plugin_src--substance_rpc--ops--setup_ops) — Project-setup ops: resolution, the baking high poly, and mesh maps.
- [`mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/system_ops.py`](#mat_utils--substance_bridge--substance_rpc--plugin_src--substance_rpc--ops--system_ops) — Painter-specific system ops: version reporting and script evaluation.
- [`mat_utils/texture_baker.py`](#mat_utils--texture_baker) — Bake an object's shaded surface (material under scene lighting) to a texture.
- [`mat_utils/texture_path_editor.py`](#mat_utils--texture_path_editor)
- [`node_utils/_node_utils.py`](#node_utils--_node_utils)
- [`node_utils/attributes/_attributes.py`](#node_utils--attributes--_attributes) — Consolidated attribute utilities for Maya.
- [`node_utils/attributes/channels/__init__.py`](#node_utils--attributes--channels--__init__) — Channels — Switchboard UI for inspecting and editing Maya attributes.
- [`node_utils/attributes/channels/_channels.py`](#node_utils--attributes--channels--_channels) — Channels — Maya attribute query / mutation logic.
- [`node_utils/attributes/channels/channels_slots.py`](#node_utils--attributes--channels--channels_slots) — UI slots for the Channels UI.
- [`node_utils/data_nodes.py`](#node_utils--data_nodes)
- [`nurbs_utils/_nurbs_utils.py`](#nurbs_utils--_nurbs_utils)
- [`nurbs_utils/curve_to_tube.py`](#nurbs_utils--curve_to_tube) — Sweep a circular profile along NURBS curve(s) to build a tube.
- [`nurbs_utils/image_tracer.py`](#nurbs_utils--image_tracer)
- [`render_utils/_render_utils.py`](#render_utils--_render_utils) — Render-control helpers.
- [`rig_utils/_rig_utils.py`](#rig_utils--_rig_utils)
- [`rig_utils/controls.py`](#rig_utils--controls)
- [`rig_utils/shadow_rig.py`](#rig_utils--shadow_rig)
- [`rig_utils/skinning.py`](#rig_utils--skinning) — Skinning utilities: binding, batch weight I/O, transfer, procedural weights.
- [`rig_utils/telescope_rig.py`](#rig_utils--telescope_rig)
- [`rig_utils/tube_rig.py`](#rig_utils--tube_rig)
- [`rig_utils/wheel_rig.py`](#rig_utils--wheel_rig)
- [`ui_utils/_ui_utils.py`](#ui_utils--_ui_utils)
- [`ui_utils/calculator.py`](#ui_utils--calculator)
- [`ui_utils/channel_box.py`](#ui_utils--channel_box) — Programmatic access to Maya's Channel Box.
- [`ui_utils/hotkey_collisions.py`](#ui_utils--hotkey_collisions) — Maya hotkey collision checker for the uitk ShortcutEditor.
- [`ui_utils/maya_bridge_slots_base.py`](#ui_utils--maya_bridge_slots_base) — Maya-flavored :class:`BridgeSlotsBase` -- adds Maya-side defaults.
- [`ui_utils/maya_native_menus.py`](#ui_utils--maya_native_menus)
- [`ui_utils/maya_ui_handler.py`](#ui_utils--maya_ui_handler)
- [`ui_utils/node_icons.py`](#ui_utils--node_icons) — Reusable helper for resolving Maya node icons at runtime.
- [`ui_utils/style_setter/_style_setter.py`](#ui_utils--style_setter--_style_setter) — Match Maya's scriptable viewport colors to another DCC's look.
- [`uv_utils/_auto_unwrap.py`](#uv_utils--_auto_unwrap) — External auto-unwrap round-trip: OBJ out, engine, OBJ back, UVs transferred.
- [`uv_utils/_uv_pack.py`](#uv_utils--_uv_pack) — xatlas pack round-trip: UV arrays out, :class:`pythontk.UvPack`, per-shell
- [`uv_utils/_uv_utils.py`](#uv_utils--_uv_utils)
- [`uv_utils/rizom_bridge/_rizom_bridge.py`](#uv_utils--rizom_bridge--_rizom_bridge)
- [`uv_utils/rizom_bridge/parameters.py`](#uv_utils--rizom_bridge--parameters) — Registry of user-tunable RizomUV parameters exposed to the bridge UI.
- [`uv_utils/rizom_bridge/rizom_bridge_slots.py`](#uv_utils--rizom_bridge--rizom_bridge_slots) — Slots for the RizomUV bridge panel.
- [`uv_utils/shell_xform.py`](#uv_utils--shell_xform) — Dedicated UV shell-transform panel.
- [`xform_utils/_xform_utils.py`](#xform_utils--_xform_utils)
- [`xform_utils/matrices.py`](#xform_utils--matrices) — Matrix utilities for Maya rigging and animation.
- [`xform_utils/pivot_watcher.py`](#xform_utils--pivot_watcher) — Real-time pivot-change notifier built on :class:`ScriptJobManager`.

---

<a id="anim_utils--_anim_utils"></a>
### `anim_utils/_anim_utils.py`

- **[`class AnimUtils(_AnimUtilsInternal, ptk.HelpMixin)`](mayatk/mayatk/anim_utils/_anim_utils.py#L687)** — Animation utilities for Maya.
  - `AnimUtils.bake(cls, objects: Union[str, List[str]], attributes: Optional[Union[str, List[str]]] = None, time_range: Optional[Tuple[float, float]] = None, sample_by: float = 1.0, preserve_outside_keys: bool = True, simulation: bool = False, destination_layer: Optional[str] = None, remove_baked_attr_from_layer: bool = False, bake_on_override_layer: bool = False, minimize_rotation: bool = True, sparse_anim_curve_bake: bool = False, disable_implicit_control: bool = True, control_points: bool = False, shape: bool = False, only_keyed: bool = False) -> List[str]` *(class)* — Bake animation on specified objects and attributes with smart grouping.
  - `AnimUtils.objects_to_curves(objects: Union[str, List[str]], recursive: bool = False, as_strings: bool = False) -> List[str]` *(static)* — Converts objects into a list of animation curves.
  - `AnimUtils.get_anim_curves(cls, objects: Optional[List[str]] = None, selected_keys_only: bool = False, recursive: bool = False) -> List[str]` *(class)* — Get animation curves from objects, selected keys, or all scene curves.
  - `AnimUtils.get_static_curves(cls, objects: List[str], value_tolerance: float = 1e-05, recursive: bool = False, as_strings: bool = False) -> List[str]` *(class)* — Detects static curves (curves with constant values) that are safe
  - `AnimUtils.get_redundant_flat_keys(cls, objects: List[str], value_tolerance: float = 1e-05, remove: bool = False, recursive: bool = False, as_strings: bool = False) -> List[Tuple[Any, List[float]]]` *(class)* — Detects redundant flat keys in curves and optionally deletes them.
  - `AnimUtils.simplify_curve(cls, objects: List[str], value_tolerance: float = 0.001, time_tolerance: float = 0.001, recursive: bool = False, as_strings: bool = False) -> List[str]` *(class)* — Simplify curves by removing keys that don't contribute to shape.
  - `AnimUtils.repair_corrupted_curves(cls, objects: Optional[Union[str, List[str]]] = None, recursive: bool = True, delete_corrupted: bool = False, fix_infinite: bool = True, fix_invalid_times: bool = True, time_range_threshold: float = 1000000.0, value_threshold: float = 1000000.0, quiet: bool = False) -> Dict[str, Any]` *(class)* — Legacy wrapper maintained for backwards compatibility.
  - `AnimUtils.optimize_keys(cls, objects: Union[str, List[str]], value_tolerance: float = 0.001, time_tolerance: float = 0.001, remove_flat_keys: bool = True, remove_static_curves: bool = True, simplify_keys: bool = False, recursive: bool = True, quiet: bool = False, stats: Optional[dict] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> List[str]` *(class)* — Optimize animation keys for the given objects by removing static curves,
  - `AnimUtils.get_keyframe_times(sources: Union[str, List[str]], mode: str = 'all', from_curves: Optional[bool] = None, as_range: bool = False, time_range: Optional[Tuple[float, float]] = None) -> Union[List[float], Tuple[float, float], None]` *(static)* — Get keyframe times from objects or curves with flexible filtering options.
  - `AnimUtils.get_driver_animation_range(node: str, driver_type: str = 'auto') -> List[float]` *(static)* — Get keyframe times from a driver node's animation or its targets.
  - `AnimUtils.get_tangent_info(attr_name: str, time: float) -> Dict[str, Any]` *(static)* — Get tangent information (types, angles, and weights) for a given attribute at a specific time.
  - `AnimUtils.set_tangent_info(attr_name: str, time: float, tangent_info: Dict[str, Any]) -> None` *(static)* — Restore tangent information on a keyframe.
  - `AnimUtils.step_keys(objects=None, keys=None, tangent: str = 'out', resolution_order: Optional[Tuple[str, ...]] = None) -> dict` *(static)* — Set stepped tangents on animation keys.
  - `AnimUtils.set_current_frame(time: Optional[float] = None, update: bool = True, relative: bool = False, snap_mode: Optional[str] = None, invert_snap: bool = False) -> float` *(static)* — Set the current frame on the timeslider with optional snapping.
  - `AnimUtils.move_keys_to_frame(objects=None, frame=None, time_range=None, selected_keys_only=False, retain_spacing=False, channel_box_attrs_only=False, align: str = 'auto')` *(static)* — Move keyframes to the given frame with comprehensive control options.
  - `AnimUtils.set_keys_for_attributes(objects, target_times=None, refresh_channel_box=False, **kwargs)` *(static)* — Sets keyframes for the specified attributes on given objects at given times.
  - `AnimUtils.filter_objects_with_keys(objects: Optional[Union[str, List[str]]] = None, keys: Optional[List[str]] = None) -> List[str]` *(static)* — Filter the given objects for those with specific keys set.
  - `AnimUtils.scene_has_animation() -> bool` *(static)* — True if the scene contains any time-based animation a playblast would capture.
  - `AnimUtils.adjust_key_spacing(cls, objects: Optional[List[str]] = None, spacing: int = 1, time: Optional[int] = 0, relative: bool = True, preserve_keys: bool = False, selected_keys_only: bool = False, exact_gap: bool = False, prevent_collisions: bool = True)` *(class)* — Adjusts the spacing between keyframes for specified objects at a given time,
  - `AnimUtils.add_intermediate_keys(objects: Union[str, List[str]], time_range: Optional[Union[int, Tuple[int, int]]] = None, percent: Optional[float] = None, include_flat: bool = False, ignore: Union[str, List[str], None] = None) -> None` *(static)* — Keys selected or animated attributes on given object(s) within a time range.
  - `AnimUtils.remove_intermediate_keys(objects: Union[str, List[str]], time_range: Optional[Union[int, Tuple[int, int]]] = None, ignore: Union[str, List[str], None] = None) -> int` *(static)* — Removes all intermediate keyframes, keeping only the first and last key on each attribute.
  - `AnimUtils.invert_keys(objects=None, time=None, relative=True, delete_original=False, mode='horizontal', value_pivot=0.0)` *(static)* — Invert keyframes, preferring selected keys over all keys.
  - `AnimUtils.align_selected_keyframes(objects: Optional[List[str]] = None, target_frame: Optional[float] = None, use_earliest: bool = True) -> bool` *(static)* — Aligns the starting keyframes of selected keyframes in the graph editor across multiple objects.
  - `AnimUtils.set_visibility_keys(objects: Optional[List[str]] = None, visible: bool = True, when: str = 'start', offset: int = 0, group_overlapping: bool = False) -> int` *(static)* — Sets visibility keyframes for objects with options for timing and grouping.
  - `AnimUtils.snap_keys_to_frames(objects: Optional[List[str]] = None, method: str = 'nearest', selected_only: bool = False, time_range: Optional[Tuple[float, float]] = None, include_driven: bool = False) -> int` *(static)* — Snaps keyframes with decimal time values to whole frame numbers.
  - `AnimUtils.transfer_keyframes(cls, objects: List[str], relative: bool = False, transfer_tangents: bool = False, optimize: bool = False)` *(class)* — Transfer keyframes from the first selected object to the subsequent objects.
  - `AnimUtils.parse_time_range(time: Union[None, int, str, Tuple, List]) -> Union[Tuple[float, float], None, List]` *(static)* — Parse time specification into a time range tuple for keyframe operations.
  - `AnimUtils.delete_keys(objects=None, *attributes, time=None, channel_box_only=False)` *(static)* — Deletes keyframes for specified attributes on given objects, optionally within a time range.
  - `AnimUtils.select_keys(objects: Optional[List[str]] = None, *attributes: str, time: Union[None, int, str, Tuple, List] = None, channel_box_only: bool = False, add_to_selection: bool = False) -> int` *(static)* — Selects keyframes for specified attributes on given objects, optionally within a time range.
  - `AnimUtils.get_frame_ranges(objects: List[str], precision: Optional[int] = None, gap: Optional[int] = None) -> Dict[str, List[Tuple[int, int]]]` *(static)* — Calculate frame ranges for a list of objects based on their keyframes.
  - `AnimUtils.get_tied_keyframes(objects: Optional[List[str]] = None, tolerance: float = 1e-05) -> Dict[str, Dict[str, List[float]]]` *(static)* — Detects tied (bookend) keyframes for given objects.
  - `AnimUtils.tie_keyframes(objects: List[str] = None, absolute: bool = False, padding: int = 0, custom_range: Optional[Tuple[float, float]] = None)` *(static)* — Ties the keyframes of all given objects (or all keyed objects in the scene if none are provided)
  - `AnimUtils.untie_keyframes(objects: List[str] = None) -> Dict[str, Dict[str, List[float]]]` *(static)* — Removes bookend keyframes added by tie_keyframes, but preserves genuine animation keys.
  - `AnimUtils.create_animation_layer(name: str = 'AnimLayer', override: bool = True, additive: bool = False, attributes: Optional[List[str]] = None, objects: Optional[List[str]] = None, weight: float = 1.0, mute: bool = False, solo: bool = False, lock: bool = False, preferred: bool = True, parent: Optional[str] = None, unique_name: bool = True, timestamp_suffix: bool = False, color: Optional[Tuple[float, float, float]] = None) -> str` *(static)* — Create an animation layer with flexible configuration options.
  - `AnimUtils.get_animation_layers(include_base: bool = False, muted_only: bool = False, active_only: bool = False) -> List[str]` *(static)* — Get all animation layers in the scene.
  - `AnimUtils.copy_keys(objects=None, mode: str = 'auto', resolution_order: Optional[Tuple[str, ...]] = None, tangent_detail: bool = False) -> Dict[str, Dict[str, Any]]` *(static)* — Copy attribute values from objects for later pasting as keys.
  - `AnimUtils.paste_keys(objects=None, copied_data: Optional[Dict[str, Dict[str, Any]]] = None, target_time=None, match_source: bool = True, refresh_channel_box: bool = True, **kwargs) -> int` *(static)* — Paste previously copied attribute values as keyframes.
  - `AnimUtils.delete_animation_layer(layer: str, merge_to_base: bool = False) -> bool` *(static)* — Delete an animation layer.
  - `AnimUtils.fit_playback_range(objects=None, padding: float = 0) -> bool` *(static)* — Set the playback range to encompass keyframes on all (or given) scene objects.

<a id="anim_utils--blendshape_animator--_blendshape_animator"></a>
### `anim_utils/blendshape_animator/_blendshape_animator.py`

Main workflow facade for blendShape morph-animation creation, editing, and export.

- **[`class BlendshapeAnimator(ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/blendshape_animator/_blendshape_animator.py#L26)** — Main workflow facade for blendShape animations.
  - `BlendshapeAnimator.create(self, base_mesh: Optional[str] = None, target_mesh: Optional[str] = None, start_frame: Optional[int] = None, end_frame: Optional[int] = None, name: str = 'morph', test_setup: bool = True) -> bool` — Set up basic morph animation between two meshes.
  - `BlendshapeAnimator.edit_weight_based(self, weights: Optional[List[float]] = None, count: int = 3, weight_range: Tuple[float, float] = (0.0, 1.0), group_name: Optional[str] = None, name_prefix: Optional[str] = None) -> List[Target]` — Create tweens at specific weights or evenly spaced.
  - `BlendshapeAnimator.edit_frame_based(self, frames: Optional[List[int]] = None, target_frame: Optional[int] = None, group_name: Optional[str] = None, name_prefix: Optional[str] = None) -> List[Target]` — Create tweens at specific animation frames.
  - `BlendshapeAnimator.edit_apply_tweens(self, tweens: Optional[List[Target]] = None) -> List[Target]` — Apply tween mesh edits back to blendShape.
  - `BlendshapeAnimator.basic_workflow(cls, base_mesh: Optional[str] = None, target_mesh: Optional[str] = None, inbetween_meshes: Optional[List[str]] = None, start_frame: Optional[int] = None, end_frame: Optional[int] = None, frame_range: Optional[Union[Tuple[int, int], List[int]]] = None, name: str = 'morph') -> Optional['BlendshapeAnimator']` *(class)* — Complete basic workflow: create setup with targets ready for editing.
  - `BlendshapeAnimator.apply_all_edits(self) -> bool` — Apply all target edits to the current setup.
  - `BlendshapeAnimator.finalize_for_export(self, cleanup_scene: bool = True, delete_construction_history: bool = True, hide_target_mesh: bool = True, delete_inbetween_meshes: bool = True) -> bool` — Finalize the morph animation and clean up the scene for baking/export.
  - `BlendshapeAnimator.from_existing(cls, base_mesh: Optional[str] = None) -> Optional['BlendshapeAnimator']` *(class)* — Create animator from existing blendShape setup on ``base_mesh``.
  - `BlendshapeAnimator.recover_animation(self) -> bool` — Recover lost animation keyframes and validate setup.
  - `BlendshapeAnimator.diagnose_topology_issues(self) -> bool` — Diagnose topology mismatches between base mesh and in-between meshes.
  - `BlendshapeAnimator.cleanup_topology_mismatches(self, delete_mismatched: bool = True, apply_valid_only: bool = True) -> bool` — Clean up topology mismatches by deleting bad meshes and applying good ones.
  - `BlendshapeAnimator.remove_target_for_export(self) -> bool` — Remove target mesh for clean export.
  - `BlendshapeAnimator.recover_setup(cls, base_mesh: Optional[str] = None, target_mesh: Optional[str] = None) -> Optional['BlendshapeAnimator']` *(class)* — Recover corrupted blendShape setup.

<a id="anim_utils--blendshape_animator--applicator"></a>
### `anim_utils/blendshape_animator/applicator.py`

Applies tween mesh edits back to blendShape in-between targets.

- **[`class ApplyStatus(Enum)`](mayatk/mayatk/anim_utils/blendshape_animator/applicator.py#L18)**
- **[`class Applicator(ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/blendshape_animator/applicator.py#L24)** — Applies tween mesh edits back to blendShape in-between targets.
  - `Applicator.validate_topology(self, tweens: List[Target]) -> List[Target]` — Filter ``tweens`` to those matching base mesh vertex count.
  - `Applicator.apply_tweens(self, tweens: Optional[List[Target]] = None, skip_duplicates: bool = True, validate_topology: bool = False) -> List[Tuple[Target, ApplyStatus]]` — Apply tween mesh edits to blendShape in-between targets.

<a id="anim_utils--blendshape_animator--blendshape_animator_slots"></a>
### `anim_utils/blendshape_animator/blendshape_animator_slots.py`

Switchboard slots controller for blendshape_animator.ui.

- **[`class BlendshapeAnimatorSlots(BlendshapeAnimator)`](mayatk/mayatk/anim_utils/blendshape_animator/blendshape_animator_slots.py#L56)** — Controller wiring blendshape_animator.ui to the BlendshapeAnimator domain class.
  - `BlendshapeAnimatorSlots.header_init(self, widget) -> None` — Configure header buttons + about menu.
  - `BlendshapeAnimatorSlots.b000_init(self, widget) -> None` — Create Setup button — option_box exposes alternative entrypoints.
  - `BlendshapeAnimatorSlots.b000(self, widget) -> None` — Create Setup.
  - `BlendshapeAnimatorSlots.cmb000_init(self, widget) -> None` — Populate the edit-mode combo.
  - `BlendshapeAnimatorSlots.le000_init(self, widget) -> None` — Name-prefix field — optional, so an empty prefix is a real choice.
  - `BlendshapeAnimatorSlots.le001_init(self, widget) -> None` — CSV weights field — option_box menu offers preset lists.
  - `BlendshapeAnimatorSlots.b001_init(self, widget) -> None` — Add Tweens — option_box exposes count + group / prefix overrides.
  - `BlendshapeAnimatorSlots.b001(self, widget) -> None` — Add Tweens — dispatches by mode through the domain facade
  - `BlendshapeAnimatorSlots.b003(self, widget) -> None` — Diagnose Topology.
  - `BlendshapeAnimatorSlots.b004_init(self, widget) -> None` — Cleanup Topology Mismatches — option_box for the two flags.
  - `BlendshapeAnimatorSlots.b004(self, widget) -> None` — Clean up blendshape targets whose topology doesn't match the base mesh.
  - `BlendshapeAnimatorSlots.b005(self, widget) -> None` — Recover Animation.
  - `BlendshapeAnimatorSlots.b006_init(self, widget) -> None` — Apply All Edits — option_box for skip_duplicates, validate_topology.
  - `BlendshapeAnimatorSlots.b006(self, widget) -> None` — Apply All Edits — bulk apply with optional flags from the option_box.
  - `BlendshapeAnimatorSlots.b007(self, widget) -> None` — Remove Target Mesh.
  - `BlendshapeAnimatorSlots.b008_init(self, widget) -> None` — Finalize for Export — option_box for the four boolean flags.
  - `BlendshapeAnimatorSlots.b008(self, widget) -> None` — Finalize the blendshape setup for export (scene cleanup, bake history, hide source).

<a id="anim_utils--blendshape_animator--creator"></a>
### `anim_utils/blendshape_animator/creator.py`

Creates in-between target meshes for custom blendShape animation curves.

- **[`class Creator(ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/blendshape_animator/creator.py#L20)** — Creates in-between target meshes for custom animation curves.
  - `Creator.create_weight_based_tweens(self, weights: List[float], group_name: str = Targets.DEFAULT_GROUPS[0], name_prefix: str = 'morph_ib') -> List[Target]` — Create tween meshes at specific weight values.
  - `Creator.create_frame_based_tween(self, target_frame: int, group_name: str = Targets.DEFAULT_GROUPS[0], name_prefix: str = 'tween') -> Optional[Target]` — Create a tween mesh at a specific animation frame.
  - `Creator.tag_tween_mesh(self, mesh: str, weight: float, target_frame: Optional[int] = None) -> None` — Add metadata attributes to ``mesh``.
  - `Creator.get_existing_weights(self) -> Set[float]` — Return the in-between weights already taken by THIS setup.
  - `Creator.find_nearby_weight(self, target_weight: float, existing_weights: Set[float], tolerance: float = 0.01) -> Optional[float]` — Find a free weight within ``tolerance`` of ``target_weight``.

<a id="anim_utils--blendshape_animator--helpers"></a>
### `anim_utils/blendshape_animator/helpers.py`

Shared helpers internal to the blendshape_animator subpackage.

- **[`class BlendshapeHelpers`](mayatk/mayatk/anim_utils/blendshape_animator/helpers.py#L13)** — BlendshapeHelpers — module namespace.
  - `BlendshapeHelpers.list_history(node: str, type_filter: Optional[str] = None) -> List[str]` *(static)* — List the construction history of a node, optionally filtered by node type.

<a id="anim_utils--blendshape_animator--keyframes"></a>
### `anim_utils/blendshape_animator/keyframes.py`

Core blendShape keyframe animation operations.

- **[`class Keyframes(ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/blendshape_animator/keyframes.py#L16)** — Core blendShape animation functionality.
  - `Keyframes.create_keyframes(self, start_frame: int, end_frame: int) -> bool` — Create linear keyframe animation from weight 0.0 -> 1.0.
  - `Keyframes.test_morph(self) -> bool` — Test the blendShape by temporarily setting weight to 0.5.
  - `Keyframes.get_frame_range(self) -> Tuple[int, int]` — Return (start, end) frame range from keyframes on weight[0].

<a id="anim_utils--blendshape_animator--recovery"></a>
### `anim_utils/blendshape_animator/recovery.py`

Recovery utilities for corrupted blendShape setups.

- **[`class Recovery(ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/blendshape_animator/recovery.py#L18)** — Utilities for recovering from corrupted blendShape setups.
  - `Recovery.fix_corrupted_animation(cls, base_mesh: str, target_mesh: str) -> bool` *(class)* — Rebuild corrupted blendShape animation.
  - `Recovery.recover_with_targets(cls, base_mesh: str, target_mesh: str) -> bool` *(class)* — Complete recovery: fix animation AND restore tween customizations.

<a id="anim_utils--blendshape_animator--target"></a>
### `anim_utils/blendshape_animator/target.py`

Tween mesh wrappers and registry for blendShape in-between targets.

- **[`class Target`](mayatk/mayatk/anim_utils/blendshape_animator/target.py#L20)** — Represents a single target/in-between target mesh.
  - `Target.weight(self) -> float` *(property)* — Get the weight value for this tween.
  - `Target.blendshape_name(self) -> str` *(property)* — Get the blendShape node name this tween targets.
  - `Target.base_mesh_name(self) -> str` *(property)* — Get the base mesh name this tween applies to.
  - `Target.target_frame(self) -> Optional[int]` *(property)* — Get target frame if this tween was created from a specific frame.
  - `Target.update_references(self, new_blendshape: str, new_base_mesh: str) -> None` — Update this tween's references to new blendShape/base mesh.
- **[`class Targets(ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/blendshape_animator/target.py#L68)** — Manages collections of tween meshes.
  - `Targets.find_all_targets(cls, blendshape: Optional[str] = None, base_mesh: Optional[str] = None) -> List[Target]` *(class)* — Find all tween meshes in the scene (deduplicated).
  - `Targets.group_by_weight(cls, tweens: List[Target]) -> Dict[float, List[Target]]` *(class)* — Group tweens by weight value, handling duplicates.
  - `Targets.update_all_references(cls, new_blendshape: str, new_base_mesh: str) -> int` *(class)* — Rebind tween references after a blendShape rebuild.

<a id="anim_utils--blendshape_animator--validator"></a>
### `anim_utils/blendshape_animator/validator.py`

Mesh and blendShape validation for blendShape animation setup.

- **[`class Validator(ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/blendshape_animator/validator.py#L14)** — Handles validation of meshes and blendShape setups.
  - `Validator.validate_meshes(cls, mesh1: str, mesh2: str) -> bool` *(class)* — Validate that both objects are compatible meshes.
  - `Validator.validate_blendshape(cls, blendshape: str) -> bool` *(class)* — Validate blendShape node configuration.

<a id="anim_utils--playblast_exporter"></a>
### `anim_utils/playblast_exporter.py`

Playblast capture, encoding, and preview-render exports for Maya.

- **[`class ExportTarget`](mayatk/mayatk/anim_utils/playblast_exporter.py#L43)** — One entry in the playblast target registry.
- **[`class CaptureResult`](mayatk/mayatk/anim_utils/playblast_exporter.py#L70)** — A captured image sequence on disk.
  - `CaptureResult.pattern(self) -> str` *(property)* — printf-style pattern for the sequence (ffmpeg input).
- **[`class ExportResult`](mayatk/mayatk/anim_utils/playblast_exporter.py#L94)** — Outcome of one export target.
  - `ExportResult.ok(self) -> bool` *(property)*
- **[`class PlayblastExporter(ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/playblast_exporter.py#L107)** — Viewport capture and preview-render exports.
  - `PlayblastExporter.available_targets(cls) -> List[Tuple[str, str]]` *(class)* — (name, label) pairs in registry order — for building UI pickers.
  - `PlayblastExporter.scene_name() -> str` *(static)* — Basename of the current scene without extension;
  - `PlayblastExporter.scene_fps() -> float` *(static)* — The scene frame rate as a float.
  - `PlayblastExporter.resolve_frame_range(cls, mode: str = 'playback', start: Optional[int] = None, end: Optional[int] = None) -> Tuple[int, int]` *(class)* — Resolve a frame range from a mode, with explicit overrides.
  - `PlayblastExporter.resolve_sound_node() -> Optional[str]` *(static)* — The timeline's active audio node, or the scene's sole audio node.
  - `PlayblastExporter.capture_sequence(self, directory: str, prefix: Optional[str] = None, start: Optional[int] = None, end: Optional[int] = None, camera: Optional[str] = None, image_format: str = 'png', **overrides: Any) -> CaptureResult` — Capture the frame range as a numbered image sequence.
  - `PlayblastExporter.capture_still(self, filepath: str, frame: Optional[int] = None, camera: Optional[str] = None, image_format: str = 'png', **overrides: Any) -> str` — Capture a single frame to an exact filepath (default: current frame).
  - `PlayblastExporter.capture_movie(self, filepath: str, fmt: str = 'avi', compression: str = 'none', start: Optional[int] = None, end: Optional[int] = None, camera: Optional[str] = None, sound: Optional[str] = None, **overrides: Any) -> str` — Capture with Maya's native movie playblast (``avi``/``movie``).
  - `PlayblastExporter.encode_sequence(self, capture: Union[CaptureResult, str], output_filepath: str, fps: Optional[float] = None, audio: Optional[Union[bool, str]] = None, quality: Optional[int] = None, **ffmpeg_options: Any) -> str` — Encode a captured image sequence to a movie via ffmpeg.
  - `PlayblastExporter.export(self, output_dir: str, name: Optional[str] = None, targets: Union[str, Sequence[str]] = ('mp4',), range_mode: str = 'playback', start: Optional[int] = None, end: Optional[int] = None, camera: Optional[str] = None, keep_frames: bool = False, progress_callback: Optional[Callable[[int, int, str], None]] = None, **overrides: Any) -> List[ExportResult]` — Produce one or more registered targets from a single plan.
  - `PlayblastExporter.render_with_arnold(self, output_dir: str, start: Optional[int] = None, end: Optional[int] = None, camera: Optional[str] = None, prefix: Optional[str] = None, frame_padding: Optional[int] = None, render_layer: Optional[str] = None, **kwargs: Any) -> List[str]` — Render a frame range with Arnold;

<a id="anim_utils--scale_keys"></a>
### `anim_utils/scale_keys.py`

Dedicated scale-keys module to keep AnimUtils lean and testable.

- **[`class ScaleKeys`](mayatk/mayatk/anim_utils/scale_keys.py#L18)** — Encapsulates scale_keys logic for clarity and focused testing.
  - `ScaleKeys.execute(self) -> int`
  - `ScaleKeys.scale_keys(cls, **kwargs) -> int` *(class)* — Scale keyframes uniformly or via motion-aware retiming.

<a id="anim_utils--segment_keys"></a>
### `anim_utils/segment_keys.py`

- **[`class SegmentKeysInfo`](mayatk/mayatk/anim_utils/segment_keys.py#L28)** — Mixin for reporting animation segment information.
  - `SegmentKeysInfo.get_time_ranges(segments: List[Dict[str, Any]]) -> List[Tuple[str, float, float]]` *(static)* — Extract time ranges from segment data.
  - `SegmentKeysInfo.print_time_ranges(cls, source: Union[List[Dict[str, Any]], List[Tuple[str, float, float]]], header: Optional[str] = None, per_segment: bool = False, object_fmt: Optional[str] = None, segment_fmt: Optional[str] = None, by_time: bool = False, csv_output: bool = False)` *(class)* — Print formatted time ranges to stdout.
  - `SegmentKeysInfo.format_time_ranges_text(cls, source: Union[List[Dict[str, Any]], List[Tuple[str, float, float]]], **kwargs) -> str` *(class)* — Return the same output as :meth:`print_time_ranges` as a
  - `SegmentKeysInfo.format_time_ranges_html(cls, source: Union[List[Dict[str, Any]], List[Tuple[str, float, float]]], title: Optional[str] = None, **kwargs) -> str` *(class)* — Wrap :meth:`format_time_ranges_text` in styled HTML suitable
- **[`class SegmentKeys(SegmentKeysInfo)`](mayatk/mayatk/anim_utils/segment_keys.py#L280)** — Shared helper for collecting and grouping animation segments.
  - `SegmentKeys.collect_segments(cls, objects: List[Any], ignore: Optional[Union[str, List[str]]] = None, split_static: bool = False, selected_keys_only: bool = False, channel_box_attrs: Optional[List[str]] = None, static_tolerance: float = 0.0001, time_range: Optional[Tuple[Optional[float], Optional[float]]] = None, ignore_visibility_holds: bool = False, ignore_holds: bool = False, exclude_next_start: bool = True, motion_only: bool = False, motion_rate: float = 0.001, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> List[Dict[str, Any]]` *(class)* — Collect animation segments from objects.
  - `SegmentKeys.get_scene_info(cls, objects: Optional[List[str]] = None, detailed: bool = True, ignore_holds: bool = True, traversal: Optional[str] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> List[Dict[str, Any]]` *(class)* — Collect animation segments for the scene info report.
  - `SegmentKeys.format_scene_info_text(cls, objects: Optional[List[str]] = None, detailed: bool = True, csv_output: bool = False, by_time: bool = False, ignore_holds: bool = True, traversal: Optional[str] = None) -> str` *(class)* — Plain-text scene-info report.
  - `SegmentKeys.format_scene_info_html(cls, objects: Optional[List[str]] = None, detailed: bool = True, csv_output: bool = False, by_time: bool = False, ignore_holds: bool = True, traversal: Optional[str] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> str` *(class)* — HTML scene-info report for ``sb.text_view_dialog``.
  - `SegmentKeys.print_scene_info(cls, objects: Optional[List[str]] = None, detailed: bool = True, csv_output: bool = False, by_time: bool = False, ignore_holds: bool = True)` *(class)* — Print animation info to stdout.
  - `SegmentKeys.group_segments(cls, segments: List[Dict[str, Any]], mode: str = 'per_segment', **kwargs) -> List[Dict[str, Any]]` *(class)* — Group segments based on the specified mode.
  - `SegmentKeys.merge_groups_sharing_curves(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]` *(static)* — Merge groups that share any animation curves.
  - `SegmentKeys.shift_curves(curves: List[Any], offset: float, time_range: Optional[Tuple[float, float]] = None, remove_flat_at_dest: bool = False)` *(static)* — Shift keys on curves by offset in a single relative move,
  - `SegmentKeys.execute_stagger(cls, groups_data: List[dict], start_frame: float, spacing: Union[int, float] = 0, use_intervals: bool = False, avoid_overlap: bool = False, preserve_gaps: bool = False)` *(class)* — Calculate and execute staggering on groups of segments.

<a id="anim_utils--shots--_detection"></a>
### `anim_utils/shots/_detection.py`

Shot-region detection — Maya scene acquisition over the pure engine math.

- **[`class Detection(_DetectionInternal)`](mayatk/mayatk/anim_utils/shots/_detection.py#L115)** — Detection — module namespace.
  - `Detection.resolve_to_transform(node, cache=None, _depth=0)` *(static)* — Resolve a curve-destination node to its owning transform.
  - `Detection.detect_shot_regions(objects: Optional[List[str]] = None, gap_threshold: float = 5.0, ignore: Optional[str] = None, motion_rate: float = 0.001, min_duration: float = 2.0) -> List[Dict[str, Any]]` *(static)* — Detect animation regions by clustering per-object segments.
  - `Detection.regions_from_selected_keys(gap_threshold: float = 5.0, key_filter: str = 'all') -> List[Dict[str, Any]]` *(static)* — Build shot regions from currently selected keyframes.

<a id="anim_utils--shots--_shot_apply"></a>
### `anim_utils/shots/_shot_apply.py`

Commit resolved :class:`MovePlan`\ s to the Maya scene.

- **[`class ShotApply(_ShotApplyInternal)`](mayatk/mayatk/anim_utils/shots/_shot_apply.py#L148)** — ShotApply — module namespace.
  - `ShotApply.apply(store: ShotStore, plan: MovePlan, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> None` *(static)* — Execute ``plan`` against the scene and ``store``.

<a id="anim_utils--shots--_shots"></a>
### `anim_utils/shots/_shots.py`

Maya shot-store adapter — the DCC layer over ``pythontk``'s shots engine.

- **[`class MayaScenePersistence`](mayatk/mayatk/anim_utils/shots/_shots.py#L99)** — Persist ShotStore data to a string channel on ``data_internal``.
  - `MayaScenePersistence.save(self, data: Dict[str, Any]) -> None`
  - `MayaScenePersistence.load(self) -> Optional[Dict[str, Any]]`
  - `MayaScenePersistence.remove_callbacks(self) -> None` — Tear down every SJM subscription owned by this store.
- **[`class ShotStore(ptk.ShotStore, _ShotStoreInternal)`](mayatk/mayatk/anim_utils/shots/_shots.py#L310)** — :class:`pythontk.ShotStore` with the scene hooks bound to Maya.
  - `ShotStore.active(cls) -> 'ShotStore'` *(class)* — Return the active store, auto-installing the Maya backend once.
  - `ShotStore.has_animation() -> bool` *(static)* — True if the scene contains animCurves driving transforms.
  - `ShotStore.detect_regions(self) -> List[Dict[str, Any]]` — Detect shot candidates using the store's detection settings.
  - `ShotStore.assess(self) -> Dict[int, str]` — Lightweight assessment: check if shot objects exist in the scene.
  - `ShotStore.publish_export_view(self, strategy: Optional[str] = None) -> Optional[str]` — Project the export view onto the shared ``data_export`` node.

<a id="anim_utils--shots--shot_manifest--_shot_manifest"></a>
### `anim_utils/shots/shot_manifest/_shot_manifest.py`

Maya Shot Manifest adapter — the DCC layer over pythontk's manifest engine.

- **[`class ShotManifest(_EngineShotManifest, _ShotManifestInternal)`](mayatk/mayatk/anim_utils/shots/shot_manifest/_shot_manifest.py#L111)** — :class:`pythontk.ShotManifest` with the scene hooks bound to Maya.
  - `ShotManifest.apply_behaviors(self) -> Dict[str, list]` — Apply detected behaviors to Maya objects (fades, audio clips).
  - `ShotManifest.rewire_audio(tracks: Optional[List[str]] = None) -> Dict[str, List[str]]` *(static)* — Reconcile managed DG audio nodes with keyed track state.
  - `ShotManifest.from_csv(cls, filepath: str, store: Optional[ShotStore] = None, columns: Optional[ColumnMap] = None, post_process: Optional[Callable[[BuilderStep], None]] = None) -> Tuple['ShotManifest', List[BuilderStep]]` *(class)* — Convenience: parse a CSV and return a ready-to-build engine.

<a id="anim_utils--shots--shot_manifest--behaviors--_behaviors"></a>
### `anim_utils/shots/shot_manifest/behaviors/_behaviors.py`

Behaviors — Maya appliers over the engine's pure keying-recipe core.

- **[`class Behaviors(_BehaviorsInternal)`](mayatk/mayatk/anim_utils/shots/shot_manifest/behaviors/_behaviors.py#L144)** — Behaviors — module namespace.
  - `Behaviors.apply_behavior(obj: str, behavior_name: str, start: float, end: float, attrs: Optional[List[str]] = None, search_path: Optional[Path] = None, source_path: str = '', anchor_override: Optional[str] = None) -> None` *(static)* — Apply a named behavior template to an object over a time range.
  - `Behaviors.verify_behavior(obj: str, behavior_name: str, start: float, end: float, search_path: Optional[Path] = None, keyframe_fn: Optional[Any] = None, anchor_override: Optional[Any] = None) -> bool` *(static)* — Check whether expected behavior keyframes exist on an object.
  - `Behaviors.apply_audio_clip(obj: str, start: float, end: float, source_path: str = '') -> None` *(static)* — Author start/stop keys for an audio track over *(start, end)*.
  - `Behaviors.compute_duration(behavior_entries: List[Dict[str, str]], fallback: float = 30, fps: Optional[float] = None) -> float` *(static)* — Derive duration from the behavior templates in *behavior_entries*.
  - `Behaviors.apply_to_shots(shots: list, apply_fn, exists_fn=None, has_keys_fn=None, store=None) -> Dict[str, list]` *(static)* — Apply declared behaviors from shot metadata to Maya objects.

<a id="anim_utils--shots--shot_manifest--manifest_data"></a>
### `anim_utils/shots/shot_manifest/manifest_data.py`

Constants, column layout, and pure helper functions for the Shot Manifest UI.

- **[`class ManifestData`](mayatk/mayatk/anim_utils/shots/shot_manifest/manifest_data.py#L44)** — ManifestData — module namespace.
  - `ManifestData.fmt_behavior(name: str) -> str` *(static)* — ``'fade_in'`` → ``'Fade In'``.
  - `ManifestData.format_behavior_html(behaviors, broken=(), status_color=None) -> str` *(static)* — Return rich-text HTML for a list of behavior names.
  - `ManifestData.try_load_maya_icons()` *(static)* — Return the :class:`NodeIcons` class if Maya is available, else ``None``.

<a id="anim_utils--shots--shot_manifest--range_resolver"></a>
### `anim_utils/shots/shot_manifest/range_resolver.py`

Range resolution for the Shot Manifest build pipeline (Maya-bound facade).

- **[`class RangeResolver`](mayatk/mayatk/anim_utils/shots/shot_manifest/range_resolver.py#L28)** — RangeResolver — module namespace.
  - `RangeResolver.resolve_ranges(steps: List[BuilderStep], user_ranges: Dict[str, Tuple[Optional[float], Optional[float]]], gap_starts: List[float], gap_end_map: Dict[float, float], gap: float, use_selected_keys: bool, last_resolved: List[Tuple[str, float, Optional[float], bool]], from_step_idx: int = 0, default_duration: float = 0, duration_fn: Optional[Callable[..., float]] = None) -> List[Tuple[str, float, Optional[float], bool]]` *(static)* — Compute a resolved ``(start, end)`` for every step.

<a id="anim_utils--shots--shot_manifest--shot_manifest_slots"></a>
### `anim_utils/shots/shot_manifest/shot_manifest_slots.py`

Switchboard slots for the Shot Manifest UI.

- **[`class ShotManifestController(ManifestTableMixin, ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/shots/shot_manifest/shot_manifest_slots.py#L52)** — Business logic for the Shot Manifest UI.
  - `ShotManifestController.detect(self, gap: Optional[float] = None) -> None` — Detect animation regions in the scene and populate the table.
  - `ShotManifestController.remove_callbacks(self) -> None` — Remove ShotStore listener and ScriptJobManager subscriptions.
  - `ShotManifestController.build(self) -> None` — Build or update shots in the store from loaded steps.
  - `ShotManifestController.assess(self, skip_key_check: bool = False) -> None` — Compare CSV steps against the live Maya shots and color the tree.
- **[`class ShotManifestSlots(ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/shots/shot_manifest/shot_manifest_slots.py#L1980)** — Switchboard slot class — routes UI events to the controller.
  - `ShotManifestSlots.header_init(self, widget)` — Header menu is configured once in controller.__init__.
  - `ShotManifestSlots.btn_expand_missing(self)` — Expand all step rows that have missing objects or behaviors.
  - `ShotManifestSlots.btn_expand_extra(self)` — Expand all step rows that have scene-discovered extra objects.
  - `ShotManifestSlots.btn_settings(self)` — Open the shared shots settings panel.
  - `ShotManifestSlots.b002(self)` — Assess shots against live Maya scene.
  - `ShotManifestSlots.b003(self)` — Build shots from loaded steps (or auto-detect from scene).

<a id="anim_utils--shots--shot_manifest--table_presenter"></a>
### `anim_utils/shots/shot_manifest/table_presenter.py`

Tree-widget presentation mixin for the Shot Manifest controller.

- **[`class ManifestTableMixin`](mayatk/mayatk/anim_utils/shots/shot_manifest/table_presenter.py#L33)** — Presentation methods for the manifest tree widget.
  - `ManifestTableMixin.expand_missing(self) -> None` — Expand all step rows that have missing objects, behaviors, or additional objects.
  - `ManifestTableMixin.expand_extra(self) -> None` — Expand all step rows that have scene-discovered extra objects.

<a id="anim_utils--shots--shot_sequencer--_shot_sequencer"></a>
### `anim_utils/shots/shot_sequencer/_shot_sequencer.py`

Shot Sequencer — manages per-shot animation with ripple editing.

- **[`class ShotSequencer`](mayatk/mayatk/anim_utils/shots/shot_sequencer/_shot_sequencer.py#L28)** — Manages a :class:`ShotStore` and provides ripple editing and
  - `ShotSequencer.shots(self) -> List[ShotBlock]` *(property)*
  - `ShotSequencer.hidden_objects(self) -> set` *(property)*
  - `ShotSequencer.markers(self) -> List[Dict[str, Any]]` *(property)*
  - `ShotSequencer.is_object_hidden(self, obj_name: str) -> bool`
  - `ShotSequencer.set_object_hidden(self, obj_name: str, hidden: bool = True) -> None`
  - `ShotSequencer.sorted_shots(self) -> List[ShotBlock]`
  - `ShotSequencer.shot_by_id(self, shot_id: int) -> Optional[ShotBlock]`
  - `ShotSequencer.shot_by_name(self, name: str) -> Optional[ShotBlock]`
  - `ShotSequencer.define_shot(self, name: str, start: float, end: float, objects: Optional[List[str]] = None, metadata: Optional[Dict[str, Any]] = None, locked: bool = False, description: str = '') -> ShotBlock` — Define a shot manually from a name and range.
  - `ShotSequencer.reconcile_all_shots(self) -> bool` — Re-resolve stale DAG paths across every shot and persist changes.
  - `ShotSequencer.collect_object_segments(self, shot_id: int, ignore: Optional[str] = None, motion_rate: float = 0.001, ignore_holds: bool = True) -> List[Dict[str, Any]]` — Collect per-object animation segments within a shot's range.
  - `ShotSequencer.collect_shot_sequences(self, shot_id: int, include_audio: bool = True) -> List[Dict[str, Any]]` — Return all sequences (anim + audio) inside a shot's range.
  - `ShotSequencer.move_sequences_to_shot(self, sequences: List[Dict[str, Any]], dest_shot_id: int) -> None` — Move *sequences* (anim and/or audio) into *dest_shot_id*.
  - `ShotSequencer.fit_shot_to_content(self, shot_id: int, mode: str = 'fit') -> tuple[float, float]` — Resize a shot's boundaries to its sequence content, rippling neighbors.
  - `ShotSequencer.trim_shot_to_content(self, shot_id: int) -> tuple[float, float]` — Shrink shot boundaries inward so they exactly enclose content.
  - `ShotSequencer.extend_shot_to_fit(self, shot_id: int) -> tuple[float, float]` — Expand shot boundaries outward to enclose all of its sequences.
  - `ShotSequencer.detect_shots(self, objects: Optional[List[str]] = None, gap_threshold: float = 5.0, ignore: Optional[str] = None, motion_rate: float = 0.001, min_duration: float = 2.0) -> List[Dict[str, Any]]` — Detect shot boundaries from existing animation on *objects*.
  - `ShotSequencer.detect_next_shot(self, gap_threshold: float = 5.0, ignore: Optional[str] = None, motion_rate: float = 0.001) -> Optional[Dict[str, Any]]` — Detect the first animation cluster after all existing shots.
  - `ShotSequencer.move_object_keys(self, obj: str, old_start: float, old_end: float, new_start: float) -> None` — Offset all keyframes of *obj* that fall within [old_start, old_end]
  - `ShotSequencer.move_stepped_keys(self, obj: str, old_time: float, new_time: float, attr_name: str | None = None, eps: float = 0.001) -> None` — Move stepped keys at *old_time* to *new_time* via delete-and-recreate.
  - `ShotSequencer.move_object_in_shot(self, shot_id: int, obj: str, old_start: float, old_end: float, new_start: float) -> None` — Move one object's keys within a shot, expanding the shot and
  - `ShotSequencer.scale_object_keys(self, obj: str, old_start: float, old_end: float, new_start: float, new_end: float) -> None` — Scale (and optionally shift) keyframes of *obj* from
  - `ShotSequencer.move_shot(self, shot_id: int, new_start: float) -> None` — Move an entire shot (all object keys) to *new_start*, rippling downstream.
  - `ShotSequencer.slide_shot(self, shot_id: int, new_start: float, direction: str = 'downstream', _enforce: bool = True) -> None` — Slide a shot intact to *new_start*, rippling only in *direction*.
  - `ShotSequencer.ripple_downstream(self, shot_id: int, after_frame: float, delta: float)` — Shift all shots starting at or after *after_frame* by *delta*.
  - `ShotSequencer.ripple_upstream(self, shot_id: int, before_frame: float, delta: float)` — Shift all shots ending at or before *before_frame* by *delta*.
  - `ShotSequencer.expand_shot(self, shot_id: int, new_end: float) -> float` — Expand a shot's end frame and ripple downstream shots.
  - `ShotSequencer.resize_object(self, shot_id: int, obj: str, old_start: float, old_end: float, new_start: float, new_end: float) -> None` — Scale one object's keys and ripple-shift all downstream shots.
  - `ShotSequencer.set_shot_duration(self, shot_id: int, new_duration: float) -> None` — Change a shot's duration and ripple-shift all downstream shots.
  - `ShotSequencer.resize_shot(self, shot_id: int, new_start: float, new_end: float, _enforce: bool = True) -> None` — Resize a shot to [new_start, new_end], scaling all keys and rippling.
  - `ShotSequencer.set_shot_start(self, shot_id: int, new_start: float, ripple: bool = True) -> None` — Move a shot to a new start time.
  - `ShotSequencer.reorder_shots(self, shot_id_a: int, shot_id_b: int) -> None` — Swap two shots' timeline positions non-destructively.
  - `ShotSequencer.move_shot_to_position(self, shot_id: int, target_pos: int) -> None` — Move a shot to a new 1-based position in the timeline order.
  - `ShotSequencer.respace(self, gap: float = 0, start_frame: float = 1) -> None` — Redistribute all shots sequentially with uniform gaps.
  - `ShotSequencer.apply_gap(self, gap: float, scope: str = 'all', shot_id: Optional[int] = None) -> bool` — Re-space shots so the given *gap* separates them, per *scope*.
  - `ShotSequencer.to_dict(self) -> Dict[str, Any]` — Serialise shots and settings to a plain dict.
  - `ShotSequencer.from_dict(cls, data: Dict[str, Any]) -> 'ShotSequencer'` *(class)* — Restore from serialised data.

<a id="anim_utils--shots--shot_sequencer--clip_motion"></a>
### `anim_utils/shots/shot_sequencer/clip_motion.py`

Clip motion, resize, and key-scaling logic for the shot sequencer.

- [`curves_for_attr(obj_name: str, attr_name: str) -> list`](mayatk/mayatk/anim_utils/shots/shot_sequencer/clip_motion.py#L41) — Return anim curves connected to a specific attribute on an object.
- [`scale_attribute_keys(obj_name: str, attr_name: str, old_start: float, old_end: float, new_start: float, new_end: float) -> None`](mayatk/mayatk/anim_utils/shots/shot_sequencer/clip_motion.py#L52) — Scale only the curves driving *attr_name* on *obj_name*.
- **[`class ClipMotionMixin`](mayatk/mayatk/anim_utils/shots/shot_sequencer/clip_motion.py#L85)** — Mixin supplying clip move, resize, and batch-move handlers.
  - `ClipMotionMixin.on_clip_resized(self, clip_id: int, new_start: float, new_duration: float) -> None` — Handle clip resize — routes to attribute, shot-boundary, or per-object logic.
  - `ClipMotionMixin.on_clip_moved(self, clip_id: int, new_start: float) -> None` — Handle clip move — routes to audio or shot-level logic.
  - `ClipMotionMixin.on_clips_batch_moved(self, moves) -> None` — Handle a batch of clip moves (group drag), syncing once at the end.
  - `ClipMotionMixin.on_keys_moved(self, clip_id: int, changes: list) -> None` — Move individual keyframes on the Maya curves, then refresh.
  - `ClipMotionMixin.on_keys_deleted(self, clip_id: int, times: list) -> None` — Delete individual keyframes from the Maya curves, then refresh.

<a id="anim_utils--shots--shot_sequencer--gap_manager"></a>
### `anim_utils/shots/shot_sequencer/gap_manager.py`

Gap and range-highlight handlers for the shot sequencer controller.

- **[`class GapManagerMixin`](mayatk/mayatk/anim_utils/shots/shot_sequencer/gap_manager.py#L19)** — Mixin supplying gap-overlay and range-highlight handlers.
  - `GapManagerMixin.on_range_highlight_changed(self, start: float, end: float) -> None` — Update the active shot boundaries when the range highlight is dragged.
  - `GapManagerMixin.on_gap_resized(self, original_next_start: float, new_next_start: float) -> None` — Handle right-edge gap drag.
  - `GapManagerMixin.on_gap_left_resized(self, original_prev_end: float, new_prev_end: float) -> None` — Handle left-edge gap drag.
  - `GapManagerMixin.on_gap_moved(self, old_start: float, old_end: float, new_start: float, new_end: float) -> None` — Handle body gap drag — slide the gap while preserving its width.
  - `GapManagerMixin.on_gap_lock_changed(self, gap_start: float, gap_end: float, locked: bool) -> None` — Handle a single gap's lock state being toggled via context menu.
  - `GapManagerMixin.on_gap_lock_all(self) -> None` — Lock all gaps so they are preserved during respace.
  - `GapManagerMixin.on_gap_unlock_all(self) -> None` — Unlock all gaps so they follow the global gap value.

<a id="anim_utils--shots--shot_sequencer--marker_manager"></a>
### `anim_utils/shots/shot_sequencer/marker_manager.py`

Marker persistence for the shot sequencer controller.

- **[`class MarkerManagerMixin(_MarkerManagerMixinInternal)`](mayatk/mayatk/anim_utils/shots/shot_sequencer/marker_manager.py#L33)** — Mixin supplying marker CRUD persistence.
  - `MarkerManagerMixin.on_marker_added(self, marker_id: int, time: float) -> None` — Persist a newly added marker.
  - `MarkerManagerMixin.on_marker_moved(self, marker_id: int, new_time: float) -> None` — Update persisted marker time.
  - `MarkerManagerMixin.on_marker_changed(self, marker_id: int) -> None` — Update persisted marker note/color.
  - `MarkerManagerMixin.on_marker_removed(self, marker_id: int) -> None` — Remove marker from persistent store.

<a id="anim_utils--shots--shot_sequencer--segment_collector"></a>
### `anim_utils/shots/shot_sequencer/segment_collector.py`

Segment collection and attribute extraction for the shot sequencer.

- **[`class SegmentCollector`](mayatk/mayatk/anim_utils/shots/shot_sequencer/segment_collector.py#L27)** — SegmentCollector — module namespace.
  - `SegmentCollector.collect_segments(sequencer, shot, visible_shots, segment_cache, shifted_out_keys, logger)` *(static)* — Collect animation segments for visible shots.
  - `SegmentCollector.active_object_set(shot, segments_by_shot) -> set` *(static)* — Return the set of objects that belong to the active shot.
  - `SegmentCollector.extract_attributes(segments) -> list` *(static)* — Extract attribute names from animation curves in the given segments.
  - `SegmentCollector.build_curve_preview(crv, t_start, t_end)` *(static)* — Extract Bézier curve shape data for a single anim curve.

<a id="anim_utils--shots--shot_sequencer--shot_nav"></a>
### `anim_utils/shots/shot_sequencer/shot_nav.py`

Shot navigation and combobox synchronization.

- **[`class ShotNavMixin`](mayatk/mayatk/anim_utils/shots/shot_sequencer/shot_nav.py#L19)** — Mixin supplying shot selection and navigation.
  - `ShotNavMixin.select_shot(self, shot_id: int) -> None` — Set Maya's playback range to the shot and select its objects.
  - `ShotNavMixin.on_shot_block_clicked(self, shot_name: str) -> None` — Select a shot by name when its block is clicked in the shot lane.

<a id="anim_utils--shots--shot_sequencer--shot_sequencer_slots"></a>
### `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py`

Switchboard slots for the Shot Sequencer UI.

- **[`class ShotSequencerController(GapManagerMixin, ClipMotionMixin, ShotNavMixin, MarkerManagerMixin, ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/shots/shot_sequencer/shot_sequencer_slots.py#L55)** — Business logic controller bridging SequencerWidget ↔ ShotSequencer.
  - `ShotSequencerController.sequencer(self) -> Optional[ShotSequencer]` *(property)* — Return the ShotSequencer, lazily creating one from the active store.
  - `ShotSequencerController.remove_callbacks(self) -> None` — Remove Maya event callbacks and ShotStore listener (call on teardown).
  - `ShotSequencerController.on_zone_context_menu(self, zone: str, time: float, global_pos) -> None` — Build a context menu specific to the clicked zone.
  - `ShotSequencerController.active_shot_id(self) -> Optional[int]` *(property)* — Return the shot_id currently selected, or the first shot's id.
  - `ShotSequencerController.on_undo(self) -> None` — Handle undo_requested from the widget — delegate to Maya undo.
  - `ShotSequencerController.on_redo(self) -> None` — Handle redo_requested from the widget — delegate to Maya redo.
  - `ShotSequencerController.on_clip_menu(self, menu, clip_id: int) -> None` — Add domain-specific actions to a clip's context menu.
  - `ShotSequencerController.on_gap_menu(self, menu, gap_start: float, gap_end: float) -> None` — Add domain-specific actions to a gap overlay's context menu.
  - `ShotSequencerController.refresh(self) -> None` — Clear cached segments and rebuild the sequencer widget.
  - `ShotSequencerController.hide_track(self, track_names) -> None` — Hide one or more tracks by name, persist, and rebuild the widget.
  - `ShotSequencerController.show_track(self, track_name: str) -> None` — Un-hide a track by object name, persist, and rebuild the widget.
  - `ShotSequencerController.delete_track(self, track_names) -> None` — Permanently remove objects from all shots and rebuild the widget.
  - `ShotSequencerController.on_selection_changed(self, clip_ids: list) -> None` — Select the corresponding Maya objects when clips are clicked.
  - `ShotSequencerController.on_track_selected(self, track_names: list) -> None` — Select Maya objects when track labels are clicked in the header.
  - `ShotSequencerController.on_clip_locked(self, clip_id: int, locked: bool) -> None` — Persist per-object clip lock and propagate to sibling clips.
  - `ShotSequencerController.on_track_menu(self, menu, track_names) -> None` — Add Maya-specific actions to the track header context menu.
  - `ShotSequencerController.on_header_menu(self, menu) -> None` — Add settings actions to the header background context menu.
  - `ShotSequencerController.on_key_selection_changed(self, key_groups: list) -> None` — Sync the Maya Graph Editor selection to match the sequencer.
  - `ShotSequencerController.on_clip_renamed(self, clip_id: int, new_label: str) -> None` — Handle inline rename — currently a no-op (shot clips removed).
  - `ShotSequencerController.on_playhead_moved(self, frame: float) -> None` — Sync the Maya playhead to the widget playhead.
- **[`class ShotEditDialog`](mayatk/mayatk/anim_utils/shots/shot_sequencer/shot_sequencer_slots.py#L2353)** — Lightweight dialog for creating or editing a shot.
  - `ShotEditDialog.show(parent=None, name: str = '', start: float = 1.0, end: float = 100.0, description: str = '', title: str = 'Shot')` *(static)* — Show a modal dialog and return the result tuple or ``None``.
- **[`class ShotSequencerSlots(ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/shots/shot_sequencer/shot_sequencer_slots.py#L2415)** — Switchboard slot class — routes UI events to the controller.
  - `ShotSequencerSlots.header_init(self, widget)` — Configure header menu.
  - `ShotSequencerSlots.btn_colors(self)` — Open the attribute color configuration dialog.
  - `ShotSequencerSlots.cmb_shot(self, index)` — Handle direct combobox selection of a shot or marker.
  - `ShotSequencerSlots.spn_snap(self, value)` — Set the snap interval on the sequencer widget.
  - `ShotSequencerSlots.btn_shortcuts(self)` — Open the sequencer shortcut editor.
  - `ShotSequencerSlots.btn_shot_settings(self)` — Open the shared shots settings panel.

<a id="anim_utils--shots--shots_slots"></a>
### `anim_utils/shots/shots_slots.py`

Switchboard slots for the Shots settings UI.

- **[`class ShotsController(ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/shots/shots_slots.py#L25)** — Business logic for the Shots settings panel.
  - `ShotsController.remove_callbacks(self) -> None` — Remove store listeners and invalidation subscription (call on teardown).
  - `ShotsController.refresh_state(self) -> None` — Central enable/disable refresh for all Shots UI widgets.
  - `ShotsController.on_detection_changed(self, value: float) -> None`
  - `ShotsController.on_detection_mode_changed(self, index: int) -> None`
  - `ShotsController.on_initial_length_changed(self, value: float) -> None`
  - `ShotsController.on_snap_whole_frames_changed(self, checked: bool) -> None`
  - `ShotsController.on_fit_mode_changed(self, index: int) -> None`
  - `ShotsController.on_gap_changed(self, value, scope: str = 'all') -> None`
  - `ShotsController.on_shot_selected(self, index: int) -> None` — User picked a different shot from the combobox.
  - `ShotsController.on_shot_name_changed(self, text: str) -> None`
  - `ShotsController.on_shot_start_changed(self, value: float) -> None`
  - `ShotsController.on_shot_end_changed(self, value: float) -> None`
  - `ShotsController.on_shot_desc_changed(self, text: str) -> None`
  - `ShotsController.on_delete_shot(self) -> None` — Delete the active shot after confirmation.
  - `ShotsController.on_delete_all_shots(self) -> None` — Delete every shot after confirmation.
  - `ShotsController.on_move_shot(self) -> None` — Move the active shot to the position specified by spn_move_to.
  - `ShotsController.on_trim_empty(self) -> None` — Trim empty space from the active shot's start and end.
  - `ShotsController.on_trim_all_shots(self) -> None` — Trim empty space from every shot.
- **[`class ShotsSlots(ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/shots/shots_slots.py#L818)** — Switchboard slot class — routes UI events to the controller.
  - `ShotsSlots.header_init(self, widget)` — Configure header help text.
  - `ShotsSlots.spn_detection(self, value)` — Detection threshold changed.
  - `ShotsSlots.cmb_detection_mode(self, index)` — Detection mode combobox changed.
  - `ShotsSlots.spn_initial_length(self, value)` — Initial shot length changed.
  - `ShotsSlots.cmb_fit_mode(self, index)` — Fit mode combobox changed.
  - `ShotsSlots.chk_snap_whole_frames(self, checked)` — Snap-to-whole-frames checkbox toggled.
  - `ShotsSlots.cmb_shot_select(self, index)` — Shot selector combobox changed.
  - `ShotsSlots.txt_shot_name(self, text=None)` — Shot name edited.
  - `ShotsSlots.spn_shot_start(self, value)` — Shot start frame changed.
  - `ShotsSlots.spn_shot_end(self, value)` — Shot end frame changed.
  - `ShotsSlots.txt_shot_desc(self, text=None)` — Shot description edited.
  - `ShotsSlots.b000(self)` — Delete the selected shot.
  - `ShotsSlots.btn_delete_all_shots(self)` — Delete all shots.
  - `ShotsSlots.btn_move_shot(self)` — Move shot to the position in spn_move_to.
  - `ShotsSlots.btn_apply_gap(self)` — Apply gap value with the scope selected in the option box.
  - `ShotsSlots.btn_trim_empty(self)` — Trim empty space from the selected shot.
  - `ShotsSlots.btn_trim_all_shots(self)` — Trim empty space from every shot.

<a id="anim_utils--smart_bake--_smart_bake"></a>
### `anim_utils/smart_bake/_smart_bake.py`

Smart bake module for intelligent pre-bake animation processing.

- **[`class BakeAnalysis`](mayatk/mayatk/anim_utils/smart_bake/_smart_bake.py#L41)** — Analysis result for a single object's bake requirements.
  - `BakeAnalysis.requires_bake(self) -> bool` *(property)* — Return True if this object has any driven channels needing bake.
  - `BakeAnalysis.all_driven_channels(self) -> List[str]` *(property)* — Return flat list of all channels that need baking.
- **[`class BakeResult`](mayatk/mayatk/anim_utils/smart_bake/_smart_bake.py#L71)** — Result container for SmartBake.bake() operation.
  - `BakeResult.baked_count(self) -> int` *(property)* — Number of objects successfully baked.
  - `BakeResult.success(self) -> bool` *(property)* — Return True if any objects were baked.
- **[`class SmartBake`](mayatk/mayatk/anim_utils/smart_bake/_smart_bake.py#L124)** — Intelligent baking with automatic detection of what needs to be baked.
  - `SmartBake.analyze(self) -> Dict[str, BakeAnalysis]` — Analyze objects to determine what needs baking.
  - `SmartBake.get_time_range(self, analysis: Optional[Dict[str, BakeAnalysis]] = None) -> Tuple[int, int]` — Determine optimal bake time range from driver animation.
  - `SmartBake.bake(self, analysis: Optional[Dict[str, BakeAnalysis]] = None, time_range: Optional[Tuple[int, int]] = None) -> BakeResult` — Execute baking on analyzed objects.
  - `SmartBake.execute(self) -> BakeResult` — High-level entry point: analyze and bake in one call.
  - `SmartBake.list_sessions(cls) -> List[str]` *(class)* — Return ids of restorable bake sessions recorded in this scene,
  - `SmartBake.restore(cls, session_id: Optional[str] = None) -> 'RestoreResult'` *(class)* — Reverse a bake session recorded by ``bake(restorable=True)``.
  - `SmartBake.session(cls, **kwargs)` *(class)* — Context manager: bake on enter, restore on exit.
  - `SmartBake.run(cls, **kwargs) -> BakeResult` *(class)* — Class method for quick smart baking without explicit instantiation.

<a id="anim_utils--smart_bake--bake_session"></a>
### `anim_utils/smart_bake/bake_session.py`

Persistence and restore engine for SmartBake's nondestructive manifest.

- **[`class BakeSessionStore(_BakeSessionStoreInternal)`](mayatk/mayatk/anim_utils/smart_bake/bake_session.py#L139)** — LIFO stack of bake-session manifests on the ``data_internal`` node.
  - `BakeSessionStore.load(cls) -> List[dict]` *(class)* — Return all persisted sessions (oldest first).
  - `BakeSessionStore.save(cls, sessions: List[dict]) -> None` *(class)*
  - `BakeSessionStore.push(cls, session: dict) -> None` *(class)*
  - `BakeSessionStore.peek(cls, session_id: Optional[str] = None) -> Optional[dict]` *(class)* — Return the latest session (or the one matching *session_id*).
  - `BakeSessionStore.pop(cls, session_id: Optional[str] = None) -> Optional[dict]` *(class)* — Remove and return the latest session (or the matching one).
  - `BakeSessionStore.list_ids(cls) -> List[str]` *(class)*
  - `BakeSessionStore.new_session_id(cls) -> str` *(class)*
  - `BakeSessionStore.node_ref(node: str) -> Dict[str, Optional[str]]` *(static)* — Return a rename-safe reference ``{"name", "uuid"}`` for *node*.
  - `BakeSessionStore.resolve_ref(ref: Optional[Dict[str, Optional[str]]]) -> Optional[str]` *(static)* — Resolve a :func:`node_ref` back to a live node name, or ``None``.
  - `BakeSessionStore.plug_ref(plug: str) -> Dict[str, Optional[str]]` *(static)* — Return a rename-safe reference ``{"name", "uuid", "attr"}`` for *plug*.
  - `BakeSessionStore.resolve_plug(ref: Optional[Dict[str, Optional[str]]]) -> Optional[str]` *(static)* — Resolve a :func:`plug_ref` back to ``"node.attr"``, or ``None``.
  - `BakeSessionStore.stash_curve(curve: str) -> dict` *(static)* — Duplicate *curve* into a locked, registered stash node.
  - `BakeSessionStore.unstash_curve(record: dict, warnings: Optional[List[str]] = None, fallback_dst: Optional[str] = None) -> Optional[str]` *(static)* — Reconnect a stashed curve into its recorded network.
  - `BakeSessionStore.discard_stash(record: dict) -> None` *(static)* — Delete a stash node that is no longer needed (bake was a no-op).
  - `BakeSessionStore.collect_upstream_curves(plug: str, passthrough_types: Set[str]) -> List[str]` *(static)* — Return all animCurves feeding *plug*, traced through passthrough nodes.
  - `BakeSessionStore.snapshot_connections(plug: str) -> List[List[dict]]` *(static)* — Record incoming connection pairs for *plug* (and its parent compound).
  - `BakeSessionStore.restore_session(session: dict) -> 'RestoreResult'` *(static)* — Reverse everything recorded in *session*.
- **[`class RestoreResult`](mayatk/mayatk/anim_utils/smart_bake/bake_session.py#L620)** — Result container for ``SmartBake.restore()``.

<a id="anim_utils--smart_bake--smart_bake_slots"></a>
### `anim_utils/smart_bake/smart_bake_slots.py`

Slots for the Smart Bake tool panel (smart_bake.ui).

- **[`class SmartBakeSlots(ptk.LoggingMixin, ptk.HelpMixin)`](mayatk/mayatk/anim_utils/smart_bake/smart_bake_slots.py#L18)** — Controller wiring smart_bake.ui to the SmartBake engine.
  - `SmartBakeSlots.cmb_scope_init(self, widget) -> None`
  - `SmartBakeSlots.cmb_backup_init(self, widget) -> None`
  - `SmartBakeSlots.header_init(self, widget) -> None` — Configure header menu, refresh button, and help text.
  - `SmartBakeSlots.reset_defaults(self) -> None` — Header menu: reset every field in this panel to its registry default.
  - `SmartBakeSlots.b000(self, widget) -> None` — Bake.
  - `SmartBakeSlots.b001(self, widget) -> None` — Unbake.

<a id="anim_utils--stagger_keys"></a>
### `anim_utils/stagger_keys.py`

Dedicated stagger-keys module to keep AnimUtils lean and testable.

- **[`class StaggerKeys`](mayatk/mayatk/anim_utils/stagger_keys.py#L16)** — Class containing keyframe staggering operations.
  - `StaggerKeys.stagger_keys(objects: list, start_frame: int = None, spacing: Union[int, float] = 0, use_intervals: bool = False, avoid_overlap: bool = False, smooth_tangents: bool = False, invert: bool = False, group_overlapping: bool = False, ignore: Union[str, List[str]] = None, channel_box_attrs_only: bool = False, split_static: bool = True, merge_touching: bool = False, ignore_visibility_holds: bool = True, verbose: bool = False, verbose_header: str = None)` *(static)* — Stagger the keyframes of selected objects with various positioning controls.

<a id="audio_utils--_audio_utils"></a>
### `audio_utils/_audio_utils.py`

Unified audio system for Maya scenes.

- **[`class TrackEvent`](mayatk/mayatk/audio_utils/_audio_utils.py#L34)** — One keyed play-event on a track.
- **[`class AudioUtils(ptk.HelpMixin)`](mayatk/mayatk/audio_utils/_audio_utils.py#L85)** — Unified audio system API for Maya scenes.
  - `AudioUtils.get_snap_frames() -> bool` *(static)* — Return the global whole-frame snap default for key writes.
  - `AudioUtils.set_snap_frames(value: bool) -> None` *(static)* — Set the global whole-frame snap default for key writes.
  - `AudioUtils.validate_track_id(track_id: str) -> None` *(static)* — Raise ``ValueError`` if *track_id* violates schema rules.
  - `AudioUtils.normalize_track_id(cls, raw: str) -> str` *(class)* — Derive a canonical ``track_id`` from arbitrary text.
  - `AudioUtils.attr_for(cls, track_id: str) -> str` *(class)* — Return the attr name for *track_id* (e.g.
  - `AudioUtils.track_id_from_attr(cls, attr_name: str) -> str` *(class)* — Inverse of :meth:`attr_for`.
  - `AudioUtils.find_carriers() -> List[str]` *(static)* — Return carriers holding audio data (``[CARRIER_NODE]`` or ``[]``).
  - `AudioUtils.list_track_attrs(carrier: str) -> List[str]` *(static)* — List all per-track audio attrs on *carrier* (sorted).
  - `AudioUtils.load_file_map(carrier: Optional[str] = None) -> Dict[str, str]` *(static)* — Return the ``{track_id: path}`` dict from the carrier's JSON attr.
  - `AudioUtils.set_path(cls, track_id: str, path: str, carrier: Optional[str] = None) -> None` *(class)* — Store *path* for *track_id* in the file map (creates attr if needed).
  - `AudioUtils.get_path(cls, track_id: str, carrier: Optional[str] = None) -> Optional[str]` *(class)* — Return the stored path for *track_id*, or ``None``.
  - `AudioUtils.remove_path(cls, track_id: str, carrier: Optional[str] = None) -> bool` *(class)* — Remove *track_id* from the file map.
  - `AudioUtils.get_fps() -> float` *(static)* — Return the current Maya scene framerate (or 24.0 outside Maya).
  - `AudioUtils.cached_waveform(wav_path: str) -> List[Tuple[float, float]]` *(static)* — Return the waveform envelope for *wav_path*, computing once per path.
  - `AudioUtils.clear_waveform_cache() -> None` *(static)* — Drop all cached waveform envelopes.
  - `AudioUtils.audio_duration_frames(file_path: str, fps: float) -> Tuple[float, str]` *(static)* — Return ``(duration_in_frames, resolved_wav_path)`` for *file_path*.
  - `AudioUtils.ensure_track_attr(cls, track_id: str, carrier: Optional[str] = None) -> str` *(class)* — Create the per-track enum attr if missing.
  - `AudioUtils.has_track(cls, track_id: str, carrier: Optional[str] = None) -> bool` *(class)* — Return True if *track_id* has a per-track attr on the carrier.
  - `AudioUtils.is_registered(cls, raw: str, carrier: Optional[str] = None) -> bool` *(class)* — True when *raw* (canonical or raw name) resolves to a registered track.
  - `AudioUtils.list_tracks(cls, carrier: Optional[str] = None) -> List[str]` *(class)* — Return all track_ids with attrs on *carrier* (sorted).
  - `AudioUtils.read_keys(cls, track_id: str, carrier: Optional[str] = None) -> List[tuple]` *(class)* — Return ``[(frame, value), ...]`` for *track_id* (time-ordered).
  - `AudioUtils.pair_on_off_events(pairs) -> List[Tuple[float, Optional[float]]]` *(static)* — Pair time-ordered ``(frame, value)`` keys into on/off spans.
  - `AudioUtils.read_events(cls, track_id: str, carrier: Optional[str] = None) -> List[TrackEvent]` *(class)* — Return :class:`TrackEvent` list for *track_id*.
  - `AudioUtils.write_key(cls, track_id: str, frame: float, value: int = 1, carrier: Optional[str] = None, snap: Optional[bool] = None) -> None` *(class)* — Set a key at *frame* with *value* (0=off, 1=on) on the track attr.
  - `AudioUtils.remove_key(cls, track_id: str, frame: float, carrier: Optional[str] = None) -> bool` *(class)* — Remove the key at *frame* on the track attr.
  - `AudioUtils.clear_keys(cls, track_id: str, carrier: Optional[str] = None) -> bool` *(class)* — Remove every key on *track_id*'s attr.
  - `AudioUtils.shift_keys_in_range(cls, old_start: float, old_end: float, delta: float, track_ids: Optional[List[str]] = None, carrier: Optional[str] = None) -> List[str]` *(class)* — Shift audio keys in ``[old_start, old_end]`` by *delta*.
  - `AudioUtils.tracks_on_at_frame(cls, frame: float, carrier: Optional[str] = None, track_ids: Optional[List[str]] = None) -> List[str]` *(class)* — Return track_ids currently "on" (value=1) at *frame*.
  - `AudioUtils.bake_events(cls, carrier: Optional[str] = None, display_map: Optional[dict] = None) -> List[tuple]` *(class)* — Return the keyed "on" events as ``[(frame, label), ...]``.
  - `AudioUtils.delete_track(cls, track_id: str, carrier: Optional[str] = None) -> bool` *(class)* — Remove the per-track attr and its keys.
  - `AudioUtils.rename_track(cls, old_id: str, new_id: str, carrier: Optional[str] = None) -> bool` *(class)* — Rename a track's attr + enum labels + file_map key.
  - `AudioUtils.show_track_attrs(cls, track_id: Optional[str] = None, carrier: Optional[str] = None) -> List[str]` *(class)* — Un-hide track attrs in the Channel Box.
  - `AudioUtils.hide_track_attrs(cls, track_id: Optional[str] = None, carrier: Optional[str] = None) -> List[str]` *(class)* — Hide track attrs from the Channel Box.
  - `AudioUtils.sync(tracks=None, carrier=None)` *(static)* — Reconcile managed DG audio nodes with keyed track state.
  - `AudioUtils.find_dg_node_for_track(track_id)` *(static)* — Return the managed DG audio node for *track_id*, or ``None``.
  - `AudioUtils.is_managed_dg(node)` *(static)* — True if *node* has the ``audio_node_source`` marker attr.
  - `AudioUtils.batch(auto_sync=True, undo=True)` *(static)* — Context manager grouping audio edits into one undo + one sync.
  - `AudioUtils.detect_legacy(obj=CARRIER_NODE, category='audio')` *(static)* — Return True if *obj* has legacy ``<category>_trigger`` attr.
  - `AudioUtils.migrate_legacy_triggers(obj, category='audio', keep_old_attrs=False)` *(static)* — Migrate legacy trigger keys to per-track attrs.

<a id="audio_utils--audio_clips--_audio_clips"></a>
### `audio_utils/audio_clips/_audio_clips.py`

Scene-wide audio event manager — thin facade over ``audio_utils``.

- **[`class AudioClips(ptk.LoggingMixin)`](mayatk/mayatk/audio_utils/audio_clips/_audio_clips.py#L48)** — Scene-wide audio event facade.
  - `AudioClips.sync(cls, track_ids: Optional[List[str]] = None, composite: bool = True, activate: bool = True) -> Dict[str, list]` *(class)* — Reconcile DG nodes and rebuild the composite WAV.
  - `AudioClips.rebuild_composite(cls) -> Optional[str]` *(class)* — Rebuild the scene-wide composite WAV from keyed start events.
  - `AudioClips.remove(cls) -> int` *(class)* — Delete every managed DG node, the composite, and all tracks.
  - `AudioClips.load_tracks(cls, audio_files: List[str]) -> List[str]` *(class)* — Register audio files as tracks (no keys authored).
  - `AudioClips.prepare_for_export(cls) -> str` *(class)* — Bake the scene-wide audio manifest for FBX export.
  - `AudioClips.enable_auto_export(cls) -> None` *(class)* — Bake the audio manifest onto ``data_export`` before **every** FBX export.
  - `AudioClips.disable_auto_export(cls) -> None` *(class)* — Remove the before-export preparer for the rest of the session.
  - `AudioClips.list_nodes(cls) -> List[str]` *(class)* — Return names of every managed DG audio node plus the composite.
  - `AudioClips.set_active(cls, node_name: str, time_slider: bool = True) -> None` *(class)* — Set an audio node as the active Time Slider sound.

<a id="audio_utils--audio_clips--audio_clips_slots"></a>
### `audio_utils/audio_clips/audio_clips_slots.py`

Switchboard slots for the Audio Clips UI.

- **[`class AudioClipsSlots(ExportMixin, CallbacksMixin)`](mayatk/mayatk/audio_utils/audio_clips/audio_clips_slots.py#L38)** — Switchboard slots for the Audio Clips UI.
  - `AudioClipsSlots.header_init(self, widget)` — Configure header menu with tool description and workflow instructions.
  - `AudioClipsSlots.cmb000_init(self, widget)` — Init track combo with browse option_box and management menu.
  - `AudioClipsSlots.cmb000(self, index, widget)` — Track selection — activate the track's DG node on the Time Slider.
  - `AudioClipsSlots.tb000(self, widget=None)` — Sync Audio to Timeline — reconcile DG nodes and rebuild composite.
  - `AudioClipsSlots.tb001_init(self, widget)` — Init Key Audio Event option-box menu.
  - `AudioClipsSlots.tb001(self, widget=None)` — Key Audio Event — write ON (1) at current frame, optionally OFF at end.
  - `AudioClipsSlots.b002(self)` — Remove Audio — nuke every track, DG node, and the composite.
  - `AudioClipsSlots.b004(self)` — Cleanup Unused — delete unkeyed tracks and their DG nodes.
  - `AudioClipsSlots.b005(self)` — Replace Selected Track — swap the selected track's audio file.
  - `AudioClipsSlots.b006(self)` — Rename Track — rename the currently selected track's id.

<a id="audio_utils--audio_clips--callbacks"></a>
### `audio_utils/audio_clips/callbacks.py`

Maya event lifecycle and hydration for Audio Clips.

- **[`class CallbacksMixin`](mayatk/mayatk/audio_utils/audio_clips/callbacks.py#L39)** — Maya event lifecycle and hydration for single-scope audio.
  - `CallbacksMixin.remove_callbacks(self)` — Tear down every SJM subscription owned by this instance.

<a id="audio_utils--audio_clips--export_ops"></a>
### `audio_utils/audio_clips/export_ops.py`

Export operations for Audio Clips.

- **[`class ExportMixin`](mayatk/mayatk/audio_utils/audio_clips/export_ops.py#L31)** — Composite and per-clip WAV export.

<a id="audio_utils--batch"></a>
### `audio_utils/batch.py`

Batch orchestration — undo chunk + dirty-track buffering.

- **[`class Batch(_BatchInternal)`](mayatk/mayatk/audio_utils/batch.py#L44)** — Batch — module namespace.
  - `Batch.batch(auto_sync: bool = True, undo: bool = True) -> '_BatchContext'` *(static)* — Context manager grouping audio edits into one undo + one sync.

<a id="audio_utils--compositor"></a>
### `audio_utils/compositor.py`

Compositor — derives DG audio nodes from keyed track events.

- **[`class Compositor(_CompositorInternal)`](mayatk/mayatk/audio_utils/compositor.py#L61)** — Compositor — module namespace.
  - `Compositor.is_managed_dg(node: str) -> bool` *(static)* — True if *node* has the ``audio_node_source`` marker attr.
  - `Compositor.find_dg_node_for_track(track_id: str) -> Optional[str]` *(static)* — Return the managed DG audio node for *track_id*, or ``None``.
  - `Compositor.sync(tracks: Optional[List[str]] = None, carrier: Optional[str] = None) -> dict` *(static)* — Reconcile managed DG audio nodes with keyed track state.

<a id="audio_utils--migrate"></a>
### `audio_utils/migrate.py`

One-shot migration from legacy single-enum carriers to per-track schema.

- **[`class Migrate(_MigrateInternal)`](mayatk/mayatk/audio_utils/migrate.py#L98)** — Migrate — module namespace.
  - `Migrate.detect_legacy(obj: str, category: str = 'audio') -> bool` *(static)* — Return True if *obj* has the legacy ``<category>_trigger`` attr.
  - `Migrate.migrate_legacy_triggers(obj: str, category: str = 'audio', keep_old_attrs: bool = False) -> List[str]` *(static)* — Migrate legacy ``<category>_trigger`` keys to per-track attrs.

<a id="audio_utils--nodes"></a>
### `audio_utils/nodes.py`

Low-level DG audio node primitives.

- **[`class Nodes(_NodesInternal)`](mayatk/mayatk/audio_utils/nodes.py#L47)** — Nodes — module namespace.
  - `Nodes.resolve_playable_path(audio_path: str, cache_dir: Optional[str] = None) -> Optional[str]` *(static)* — Return a Maya-playable path, converting to WAV via ``ptk.AudioUtils``.
  - `Nodes.workspace_sound_dir() -> Optional[str]` *(static)* — Return the Maya workspace ``sound/`` directory, or ``None``.
  - `Nodes.create_dg(file_path: str, name: Optional[str] = None, offset: float = 0, track_id: Optional[str] = None) -> Optional[str]` *(static)* — Create a new audio DG node configured for playback.
  - `Nodes.configure_dg(node_name: str, file_path: str, offset: float) -> None` *(static)* — Configure an existing DG audio node for reliable playback.
  - `Nodes.query_duration(node_name: str) -> float` *(static)* — Return the duration of an audio DG node in frames (0 on failure).

<a id="audio_utils--segments"></a>
### `audio_utils/segments.py`

Consumer-facing segment discovery for sequencer + manifest.

- **[`class AudioSegment(_AudioSegmentInternal)`](mayatk/mayatk/audio_utils/segments.py#L94)** — A resolved audio segment for sequencer/manifest consumption.
  - `AudioSegment.is_audio(self) -> bool` *(property)*
  - `AudioSegment.collect_all_segments(scene_start: Optional[float] = None, scene_end: Optional[float] = None, include_waveform: bool = True, carrier: Optional[str] = None) -> List[AudioSegment]` *(static)* — Return every :class:`AudioSegment` visible on the canonical carrier.
  - `AudioSegment.collect_segments_for_track(track_id: str, include_waveform: bool = True, carrier: Optional[str] = None) -> List[AudioSegment]` *(static)* — Return segments for a single *track_id*.

<a id="cam_utils--_cam_utils"></a>
### `cam_utils/_cam_utils.py`

- **[`class CamUtils(ptk.HelpMixin)`](mayatk/mayatk/cam_utils/_cam_utils.py#L19)**
  - `CamUtils.group_cameras(name='cameras', non_default=True, root_only=False, hide_group=False)` *(static)* — Group cameras in the scene based on the provided parameters.
  - `CamUtils.toggle_safe_frames(cls)` *(class)* — Toggle display of the film gate for the current camera.
  - `CamUtils.get_current_cam()` *(static)* — Get the currently active camera.
  - `CamUtils.create_camera_from_view(name='camera#')` *(static)* — Create a new camera based on the current view.
  - `CamUtils.get_view_state(cls, camera=None)` *(class)* — Snapshot a camera's placement and lens clipping, for a later restore.
  - `CamUtils.set_view_state(cls, state)` *(class)* — Restore a snapshot taken by :meth:`get_view_state`.
  - `CamUtils.fit_camera_clipping(cls, objects=None, camera=None, buffer=0.25)` *(class)* — Widen a camera's clip planes until `objects` can't be clipped by them.
  - `CamUtils.adjust_camera_clipping(cls, camera=None, near_clip=None, far_clip=None)` *(class)* — Adjusts the near and far clipping planes of one or multiple cameras.
  - `CamUtils.switch_viewport_camera(cls, camera_name)` *(class)* — Unified method to switch to a camera, creating custom ones if needed.

<a id="core_utils--_core_utils"></a>
### `core_utils/_core_utils.py`

- **[`class BoundingBox`](mayatk/mayatk/core_utils/_core_utils.py#L20)** — Plain-data bounding box with ``MVector`` extents.
  - `BoundingBox.corners(self)` *(property)* — The box's 8 corner ``MVector``s (every min/max combination per axis).
- **[`class CoreUtils(ptk.CoreUtils, _CoreUtilsInternal)`](mayatk/mayatk/core_utils/_core_utils.py#L187)**
  - `CoreUtils.undo_chunk(name: str = '')` *(static)* — Group operations into a single Maya undo chunk.
  - `CoreUtils.undo_disabled()` *(static)* — Run a block without recording anything into the undo queue.
  - `CoreUtils.suspended_refresh()` *(static)* — Suspend viewport refresh for the duration of a bulk operation.
  - `CoreUtils.selected(func: Callable) -> Callable` *(static)* — A decorator to pass the current selection to the target parameter if None is given.
  - `CoreUtils.undoable(fn)` *(static)* — A decorator to place a function into Maya's undo chunk.
  - `CoreUtils.reparent(func: Callable) -> Callable` *(static)* — A decorator to manage reparenting of Maya nodes before and after an operation.
  - `CoreUtils.wrap_control(control_name, container)` *(static)* — Embed a Maya Native UI Object.
  - `CoreUtils.confirm_existence(objects: List[str]) -> Tuple[List[str], List[str]]` *(static)* — Confirms the existence of each object in the provided list in Maya.
  - `CoreUtils.get_mfn_mesh(objects, api_version: int = 2)` *(static)* — Get MFnMesh function set(s) from transform or shape node(s).
  - `CoreUtils.get_array_type(array)` *(static)* — Determine the given element(s) type.
  - `CoreUtils.convert_array_type(lst, returned_type='str', flatten=False)` *(static)* — Convert the given element(s) to <obj>, 'str', or int values.
  - `CoreUtils.get_parameter_mapping(node, cmd, parameters)` *(static)* — Query a specified Maya command and return a dict mapping parameters to their values.
  - `CoreUtils.set_parameter_mapping(node, cmd, parameters)` *(static)* — Apply a set of parameter values to a specified Maya node using a given Maya command.
  - `CoreUtils.build_mesh_similarity_mapping(cls, source, target, tolerance: float = 0.1) -> dict` *(class)* — Build a mapping of source meshes to target meshes based on geometric similarity.
  - `CoreUtils.get_mel_globals(keyword=None, ignore_case=True)` *(static)* — Get global MEL variables.
  - `CoreUtils.reorder_objects(objects=None, method='name', reverse=False)` *(static)* — Reorder a given set of objects using various sorting methods.
  - `CoreUtils.as_strings(nodes) -> List[str]` *(static)* — Coerce a node-or-iterable-of-nodes to a list of plain DAG-path strings.
  - `CoreUtils.short_name(node) -> str` *(static)* — Leaf name with namespace stripped: ``"|grp|ns:obj"`` -> ``"obj"``.
  - `CoreUtils.leaf_name(node) -> str` *(static)* — Leaf name with namespace preserved: ``"|grp|ns:obj"`` -> ``"ns:obj"``.
  - `CoreUtils.get_bounding_box(node, world: bool = True) -> BoundingBox` *(static)* — Return a :class:`BoundingBox` for *node*.

<a id="core_utils--auto_instancer--_auto_instancer"></a>
### `core_utils/auto_instancer/_auto_instancer.py`

Scene auto-instancer: convert geometrically identical meshes to instances.

- **[`class InstanceCandidate`](mayatk/mayatk/core_utils/auto_instancer/_auto_instancer.py#L48)** — Holds information about a transform candidate for instancing.
  - `InstanceCandidate.transform(self) -> str` *(property)*
  - `InstanceCandidate.exists(self) -> bool`
- **[`class InstanceGroup`](mayatk/mayatk/core_utils/auto_instancer/_auto_instancer.py#L81)** — A group of objects that are geometrically identical.
- **[`class AutoInstancer(ptk.LoggingMixin, _AutoInstancerInternal)`](mayatk/mayatk/core_utils/auto_instancer/_auto_instancer.py#L141)** — Convert matching meshes into instances.
  - `AutoInstancer.default_summary() -> Dict[str, object]` *(static)* — A zeroed run-summary — the shape of :attr:`last_run_summary`.
  - `AutoInstancer.format_summary(summary: Dict[str, object], output_count: int) -> str` *(static)* — Human-readable, DCC-agnostic description of a run *summary*.
  - `AutoInstancer.tolerance(self)` *(property)*
  - `AutoInstancer.scale_tolerance(self)` *(property)*
  - `AutoInstancer.require_same_material(self)` *(property)*
  - `AutoInstancer.check_uvs(self)` *(property)*
  - `AutoInstancer.combine_assemblies(self)` *(property)*
  - `AutoInstancer.search_radius_mult(self)` *(property)*
  - `AutoInstancer.verbose(self)` *(property)*
  - `AutoInstancer.run(self, nodes: Optional[Sequence[object]] = None) -> List[str]` — Discover and instance matching meshes.
  - `AutoInstancer.find_instance_groups(self, nodes: Optional[Sequence[object]] = None, check_hierarchy: Optional[bool] = None) -> List[InstanceGroup]` — Find groups of identical objects.
  - `AutoInstancer.run_once(cls, nodes: Optional[Sequence[object]] = None, *, return_summary: bool = False, **config) -> Union[List[str], Tuple[List[str], Dict[str, object]]]` *(class)* — One-shot: build an ``AutoInstancer`` from ``config`` and run it.

<a id="core_utils--auto_instancer--assembly_reconstructor"></a>
### `core_utils/auto_instancer/assembly_reconstructor.py`

Logic for separating and reassembling mesh assemblies.

- **[`class AssemblyReconstructor`](mayatk/mayatk/core_utils/auto_instancer/assembly_reconstructor.py#L37)** — Handles the separation and intelligent reassembly of combined meshes.
  - `AssemblyReconstructor.separate_combined_meshes(self, nodes: List[object]) -> List[object]` — Separate any combined meshes in the list into their shells.
  - `AssemblyReconstructor.cleanup_empty_sources(self) -> None` — Delete leftover source transforms whose shells were all moved out.
  - `AssemblyReconstructor.cleanup_empty_assembly_groups(self) -> None` — Delete assembly groups this run created that have since emptied.
  - `AssemblyReconstructor.center_transform_on_geometry(self, node) -> None` — Moves the transform to the center of its geometry without moving the geometry.
  - `AssemblyReconstructor.canonicalize_transform(self, node) -> None` — Aligns the transform's rotation to the geometry's PCA axes.
  - `AssemblyReconstructor.canonicalize_leaf_meshes(self, nodes: List[object]) -> List[object]` — Canonicalize all leaf mesh transforms for instancing.
  - `AssemblyReconstructor.reassemble_assemblies(self, nodes: List[object]) -> List[object]` — Reassemble separated shells into logical assemblies.
  - `AssemblyReconstructor.combine_reassembled_assemblies(self, nodes: List[object]) -> List[object]` — Combine each copy of a repeated assembly type into a single mesh.

<a id="core_utils--auto_instancer--geometry_matcher"></a>
### `core_utils/auto_instancer/geometry_matcher.py`

Geometry analysis and matching logic for AutoInstancer.

- **[`class ShellInfo`](mayatk/mayatk/core_utils/auto_instancer/geometry_matcher.py#L26)** — Stores cached analysis data for a single shell.
- **[`class GeometryMatcher(_GeometryMatcherInternal)`](mayatk/mayatk/core_utils/auto_instancer/geometry_matcher.py#L130)** — Handles geometric analysis and comparison.
  - `GeometryMatcher.clear_cache(self) -> None` — Drop cached point arrays and pair results (call after scene edits).
  - `GeometryMatcher.quantize(self, value: float, precision: int = 4) -> float` — Round a value to a specific precision to ignore float noise.
  - `GeometryMatcher.get_pca_basis(self, node: str) -> Optional['om.MMatrix']` — Returns the PCA basis matrix (rotation only) for the node's mesh.
  - `GeometryMatcher.get_mesh_signature(self, transform: str) -> Optional[Tuple]` — Lightweight signature for quick rejection.
  - `GeometryMatcher.are_meshes_identical(self, t1: str, t2: str) -> Tuple[bool, Optional['om.MMatrix']]` — Detailed geometric comparison using robust PCA alignment.
  - `GeometryMatcher.get_hierarchy_signature(self, node: str) -> Tuple` — Recursive signature generation for hierarchy comparison.
  - `GeometryMatcher.are_meshes_identical_with_transform(self, t1: str, t2: str, matrix) -> bool` — Check if t1 transformed by matrix matches t2.
  - `GeometryMatcher.are_hierarchies_identical(self, t1: str, t2: str, expected_transform: Optional['om.MMatrix'] = None, is_root: bool = False) -> Tuple[bool, Optional['om.MMatrix']]` — Detailed hierarchy comparison.
  - `GeometryMatcher.mesh_points(shape, world: bool = False)` *(static)* — ``MPointArray`` for *shape*.
  - `GeometryMatcher.mesh_triangles(shape)` *(static)* — ``(counts, indices)`` from ``MFnMesh.getTriangles``, as plain lists.
  - `GeometryMatcher.mesh_uv_set_names(shape)` *(static)*
  - `GeometryMatcher.mesh_get_uvs(shape, uv_set=None)` *(static)*
  - `GeometryMatcher.mesh_num_uvs(shape, uv_set=None)` *(static)*
  - `GeometryMatcher.calculate_mesh_volume(node: str) -> float` *(static)* — Calculate mesh volume using the divergence theorem (numpy).

<a id="core_utils--auto_instancer--instancing_strategy"></a>
### `core_utils/auto_instancer/instancing_strategy.py`

Instancing strategy logic for AutoInstancer.

- **[`class StrategyType(Enum)`](mayatk/mayatk/core_utils/auto_instancer/instancing_strategy.py#L16)**
- **[`class StrategyConfig`](mayatk/mayatk/core_utils/auto_instancer/instancing_strategy.py#L24)**
- **[`class InstancingStrategy`](mayatk/mayatk/core_utils/auto_instancer/instancing_strategy.py#L32)** — Determines the best instancing strategy for a group of objects.
  - `InstancingStrategy.evaluate(self, group_size: int, mesh_node: Optional[object] = None, triangle_count: Optional[int] = None) -> StrategyType` — Evaluate the strategy for a given group.

<a id="core_utils--components"></a>
### `core_utils/components.py`

- **[`class GetComponentsMixin`](mayatk/mayatk/core_utils/components.py#L22)**
  - `GetComponentsMixin.get_component_type(cls, component, returned_type='abv')` *(class)* — Get the type of a given component.
  - `GetComponentsMixin.convert_alias(cls, component_type, returned_type='abv')` *(class)* — Return an alternate component alias for the given alias.
  - `GetComponentsMixin.convert_component_type(cls, components, component_type, returned_type='str', flatten=False)` *(class)* — Convert component(s) to its sub-components of the given type.
  - `GetComponentsMixin.get_component_index(components)` *(static)* — Extract the numerical index or indices of a component or components from their descriptor strings.
  - `GetComponentsMixin.convert_int_to_component(cls, obj, integers, component_type, returned_type='str', flatten=False)` *(class)* — Convert the given integers to components of the given object.
  - `GetComponentsMixin.filter_components(cls, components, inc=None, exc=None, flatten=False)` *(class)* — Filter the given components.
  - `GetComponentsMixin.get_components(cls, objects, component_type, returned_type='str', inc=None, exc=None, randomize=0, flatten=False)` *(class)* — Get the components of the given type from the given object(s).
- **[`class Components(GetComponentsMixin, ptk.HelpMixin, _ComponentsInternal)`](mayatk/mayatk/core_utils/components.py#L391)**
  - `Components.get_mesh_transforms(objects) -> List[str]` *(static)* — Full paths of every mesh TRANSFORM in *objects*, descendants included.
  - `Components.get_standoff_distances(cls, objects, target, sample_limit: Optional[int] = None) -> Dict[str, float]` *(class)* — Measure how far each mesh in *objects* stands off *target*'s surface.
  - `Components.map_components_to_objects(components_list)` *(static)* — Map a list of components to their respective objects.
  - `Components.get_contiguous_edges(cls, components)` *(class)* — Get a list containing sets of adjacent edges.
  - `Components.get_contiguous_islands(cls, faces)` *(class)* — Get a list containing sets of adjacent polygon faces grouped by islands.
  - `Components.get_islands(obj, returned_type='str', flatten=False)` *(static)* — Get the group of components in each separate island of a combined mesh.
  - `Components.get_border_components(cls, components, returned_type='str', component_border=False)` *(class)* — Get border components from given component(s) or a polygon object based on connectivity.
  - `Components.get_furthest_vertices(vertices_a, vertices_b)` *(static)* — Determine the two furthest apart vertices, one from each of the two provided lists.
  - `Components.get_closest_verts(cls, a, b, tolerance=1000)` *(class)* — Find the two closest vertices between the two sets of vertices.
  - `Components.closest_point_probe(shape)` *(static)* — Temporary ``closestPointOnMesh`` node wired to *shape* for
  - `Components.get_closest_vertex(cls, vertices, obj, tolerance=0.0, freeze_transforms=False, returned_type='str')` *(class)* — Find the closest vertex of the given object for each vertex in the list of given vertices.
  - `Components.get_vertices_within_threshold(reference_vertices, max_distance)` *(static)* — Categorizes vertices of a mesh based on their distance from the first reference vertex.
  - `Components.adjusted_distance_between_vertices(p1, p2, adjust: float = 0.0, as_percentage: bool = False)` *(static)* — Calculate adjusted distance between two points/vertices.
  - `Components.bridge_connected_edges(edges) -> None` *(static)* — Bridges two connected edges.
  - `Components.get_edge_path(cls, components, path='edgeLoop', returned_type='str', flatten=False)` *(class)* — Query the polySelect command for the components along different edge paths.
  - `Components.get_shortest_path(cls, components, flatten=False)` *(class)* — Calculate the shortest path between two specified edge or vertex components within the same 3D obje…
  - `Components.get_normal(face)` *(static)* — Get the normal of a face in world space.
  - `Components.get_normal_vector(x)` *(static)* — Get the normal vectors of the given polygon object(s) or its components.
  - `Components.get_normal_angle(cls, edges) -> Union[float, List[float]]` *(class)* — Get the angle between the normals of the faces connected by one or more edges.
  - `Components.get_edges_by_normal_angle(cls, objects, low_angle: float = 0, high_angle: float = 180, return_angles: bool = False)` *(class)* — Return edges whose adjacent face-normal angle falls within a range.
  - `Components.set_edge_hardness(cls, objects, angle_threshold: float, upper_hardness: float = None, lower_hardness: float = None, unlock_normals: bool = False) -> List[str]` *(class)* — Set edge hardness based on normal angle thresholds.
  - `Components.get_faces_with_similar_normals(cls, faces, transforms=None, similar_faces=None, range_x=0.1, range_y=0.1, range_z=0.1, returned_type='str')` *(class)* — Filter for faces with normals that fall within an X,Y,Z tolerance.
  - `Components.average_normals(cls, objects, by_uv_shell=False)` *(class)* — Average the normals of the given objects.
  - `Components.transfer_normals(objects, space: str = 'world')` *(static)* — Transfer vertex normals from source mesh to target meshes.
  - `Components.filter_components_by_connection_count(cls, components, num_of_connected=(0, 2), connected_type='', returned_type='str')` *(class)* — Get a list of components filtered by the number of their connected components.
  - `Components.get_vertex_normal(cls, vertex, angle_weighted=False)` *(class)* — Return the normal at the given vertex.
  - `Components.get_vector_from_components(components)` *(static)* — Get a vector representing the averaged and normalized vertex-face normals.
  - `Components.crease_edges(edges=None, amount=None, angle=None)` *(static)* — Adjust properties of the given edges with optional crease and edge softening/hardening.
  - `Components.get_creased_edges(edges)` *(static)* — Return any creased edges from a list of edges.
  - `Components.transfer_creased_edges(frm, to)` *(static)* — Transfer creased edges from the 'frm' object to the 'to' objects.

<a id="core_utils--diagnostics--animation_diag"></a>
### `core_utils/diagnostics/animation_diag.py`

Animation-curve diagnostics and optional repair helpers.

- **[`class AnimCurveDiagnostics`](mayatk/mayatk/core_utils/diagnostics/animation_diag.py#L19)** — Utilities for detecting and resolving common animation-curve issues.
  - `AnimCurveDiagnostics.repair_visibility_tangents(cls, objects: Optional[Union[NodeLike, Sequence[NodeLike]]] = None, recursive: bool = True, quiet: bool = False) -> int` *(class)* — Repair visibility animation curves by forcing 'step' tangents.
  - `AnimCurveDiagnostics.repair_corrupted_curves(cls, objects: Optional[Union[NodeLike, Sequence[NodeLike]]] = None, recursive: bool = True, delete_corrupted: bool = False, fix_infinite: bool = True, fix_invalid_times: bool = True, time_range_threshold: float = 1000000.0, value_threshold: float = 1000000.0, quiet: bool = False) -> Dict[str, Any]` *(class)* — Detect and (optionally) repair corrupted animation curves.

<a id="core_utils--diagnostics--audit_records"></a>
### `core_utils/diagnostics/audit_records.py`

Scene-audit data contract: profiles, per-asset records, and the SceneReport tree.

- **[`class AuditProfile`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L18)** — Thresholds for scene analysis.
- **[`class MeshRecord`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L45)** — Per-mesh statistics for a single shape node.
- **[`class MaterialRecord`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L63)** — Per-shape material usage summary (aggregated across slots).
- **[`class Finding`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L81)** — An observation about an asset (negative or risk-flagged).
- **[`class FixAction`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L91)** — A recommended remediation step.
- **[`class BudgetDelta`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L102)** — How far an asset exceeds the profile budget along each axis.
  - `BudgetDelta.is_over_budget(self) -> bool`
  - `BudgetDelta.summary(self) -> str` — Pre-rendered ``"tris +N | slots +M | …"`` string used by the
- **[`class AssetRecord`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L137)** — Combined per-asset record produced by analyze().
- **[`class ParetoEntry`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L156)** — One row of a Pareto ranking (top contributor + cumulative %).
- **[`class TextureFile`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L165)** — A texture file referenced by the scene, with usage stats.
- **[`class MissingTexture`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L179)** — A texture referenced by a material but not present on disk.
- **[`class SharedTexture`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L188)** — A texture used by more than one mesh.
- **[`class MaterialSplit`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L196)** — A material correlated with high-slot meshes (draw-call splits).
- **[`class SlotStats`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L206)** — Distribution stats for material slots-per-mesh.
- **[`class InstanceStats`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L217)** — Mesh / instance counts.
- **[`class BudgetBuckets`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L226)** — Histogram of overage severity per dimension.
- **[`class ComplianceStats`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L238)** — Percentage of scene over budget per dimension.
- **[`class MissingTextureImpact`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L246)** — Downstream effect of missing textures on the asset list.
  - `MissingTextureImpact.is_empty(self) -> bool`
- **[`class SummaryStats`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L258)** — High-level scene counters surfaced by the Executive Summary.
- **[`class BudgetStats`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L281)** — Budget / compliance / savings figures.
- **[`class TextureStats`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L299)** — Texture-side aggregates.
- **[`class PipelineStats`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L318)** — Pipeline integrity findings (missing textures + impact).
- **[`class OffenderLists`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L328)** — Top-N rankings across various dimensions.
- **[`class AnalysisManifest`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L353)** — What was analyzed, how, and how long it took.
- **[`class SceneReport`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L375)** — Top-level result of ``SceneAnalyzer.generate_report``.
  - `SceneReport.to_dict(self) -> Dict[str, Any]` — Serialize the report to a nested plain-dict tree.
- **[`class SceneInfoSection`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L407)** — Report-section identifiers used to gate analyze() work and report output.
  - `SceneInfoSection.normalize(cls, sections: Optional[List[str]]) -> List[str]` *(class)* — Coerce a caller-supplied sections argument to a stable,

<a id="core_utils--diagnostics--mesh_diag"></a>
### `core_utils/diagnostics/mesh_diag.py`

Mesh diagnostics and repair helpers.

- **[`class MeshDiagnostics`](mayatk/mayatk/core_utils/diagnostics/mesh_diag.py#L18)** — Operations for inspecting and fixing common mesh issues.
  - `MeshDiagnostics.clean_geometry(objects: NodeSeq, allMeshes: bool = False, repair: bool = False, quads: bool = False, nsided: bool = False, concave: bool = False, holed: bool = False, nonplanar: bool = False, zeroGeom: bool = False, zeroGeomTol: float = 1e-05, zeroEdge: bool = False, zeroEdgeTol: float = 1e-05, zeroMap: bool = False, zeroMapTol: float = 1e-05, sharedUVs: bool = False, nonmanifold: bool = False, lamina: bool = False, invalidComponents: bool = False, historyOn: bool = True, bakePartialHistory: bool = False) -> list` *(static)* — Select or remove unwanted geometry from a mesh via ``polyCleanupArgList``.
  - `MeshDiagnostics.get_ngons(objects: Optional[NodeSeq] = None, repair: bool = False) -> list` *(static)* — Find N-gons and optionally convert them to quads.

<a id="core_utils--diagnostics--scene_audit"></a>
### `core_utils/diagnostics/scene_audit.py`

Scene audit engine — game-readiness analysis over meshes, materials, and textures.

- **[`class SceneAnalyzer(ptk.LoggingMixin)`](mayatk/mayatk/core_utils/diagnostics/scene_audit.py#L64)** — Analyzes scene objects for performance expectations in game engines.
  - `SceneAnalyzer.run_audit(cls, adaptive: bool = False, verbose: bool = True) -> None` *(class)* — Run a full scene audit and print the report.
  - `SceneAnalyzer.format_audit_text(cls, adaptive: bool = False, objects: Optional[List[Any]] = None, sections: Optional[List[str]] = None) -> Dict[str, str]` *(class)* — Run the audit and return the formatted report as a
  - `SceneAnalyzer.format_audit_html(cls, adaptive: bool = False, objects: Optional[List[Any]] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None, sections: Optional[List[str]] = None) -> Dict[str, str]` *(class)* — Run the audit and return a section-keyed dict of HTML
  - `SceneAnalyzer.analyze(self, objects: List[Any] = None, fast_mode: bool = True, progress_callback: Optional[Callable[[int, int, str], None]] = None, profile: AuditProfile = None, sections: Optional[List[str]] = None) -> List[AssetRecord]` — Main entry point for analysis.
  - `SceneAnalyzer.generate_report(self, records: List[AssetRecord]) -> SceneReport` — Build a :class:`SceneReport` from per-asset records.
  - `SceneAnalyzer.print_report(self, report: SceneReport, sections: Optional[List[str]] = None)` — Print the formatted scene-audit report to the logger.

<a id="core_utils--diagnostics--scene_diag"></a>
### `core_utils/diagnostics/scene_diag.py`

Scene repair helpers: OCIO / color management, unknown nodes and plugins,

- **[`class SceneDiagnostics(_SceneDiagnosticsInternal)`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L64)** — Operations for inspecting and fixing common scene issues.
  - `SceneDiagnostics.fix_ocio(cls, dry_run: bool = False, verbose: bool = True, prefer_env_ocio: bool = True, prefer_aces: bool = True, fix_color_spaces: bool = True) -> dict` *(class)* — Repair Maya OCIO/Color Management preferences.
  - `SceneDiagnostics.fix_missing_color_spaces(cls, fallback_color_space: Optional[str] = None, fallback_raw_space: Optional[str] = None, auto_detect: bool = True, dry_run: bool = False, verbose: bool = True, scan_all: bool = True, force_update: bool = False) -> Dict[str, Any]` *(class)* — Fix missing color space errors on file texture nodes.
  - `SceneDiagnostics.fix_unknown_plugins(dry_run: bool = False, verbose: bool = True) -> Dict[str, List[str]]` *(static)* — Fixes the 'Unable to Save Scene' issue by removing unknown nodes and plugins.
  - `SceneDiagnostics.remove_xgen_expressions(quiet: bool = False) -> int` *(static)* — Remove legacy XGen expressions that cause 'Cannot find procedure xgmPreview' errors.
  - `SceneDiagnostics.cleanup_scene(cls, quiet: bool = False) -> Dict[str, Any]` *(class)* — Run all scene cleanup operations:
  - `SceneDiagnostics.repair_mangled_names(cls, objects: Optional[List[str]] = None, dry_run: bool = False) -> Dict[str, Any]` *(class)* — Repair scratch/mangled node names, then conform shape names.

<a id="core_utils--diagnostics--transform_diag"></a>
### `core_utils/diagnostics/transform_diag.py`

Transform diagnostics and repair helpers.

- **[`class TransformDiagnostics(_TransformDiagnosticsInternal)`](mayatk/mayatk/core_utils/diagnostics/transform_diag.py#L112)** — Operations for inspecting and fixing common transform issues.
  - `TransformDiagnostics.get_sheared(cls, objects: Optional[NodeSeq] = None, tolerance: Optional[float] = None) -> List[str]` *(class)* — Return the transforms carrying their own shear.
  - `TransformDiagnostics.get_non_orthogonal(cls, objects: Optional[NodeSeq] = None, tolerance: Optional[float] = None, detailed: bool = False) -> Union[List[str], Dict[str, dict]]` *(class)* — Return the transforms whose evaluated (world) axes are not perpendicular.
  - `TransformDiagnostics.fix_non_orthogonal_axes(cls, objects: Optional[NodeSeq] = None, dry_run: bool = False, tolerance: Optional[float] = None, quiet: bool = False, break_connections: bool = False, instance_strategy: str = 'preserve', delete_history: bool = False) -> List[str]` *(class)* — Fix non-orthogonal axes by freezing the offending transforms.

<a id="core_utils--diagnostics--uv_diag"></a>
### `core_utils/diagnostics/uv_diag.py`

UV diagnostics and repair helpers.

- **[`class UvSetCleanupResult`](mayatk/mayatk/core_utils/diagnostics/uv_diag.py#L20)** — Result of a UV set cleanup operation for a single mesh.
- **[`class UvDiagnostics`](mayatk/mayatk/core_utils/diagnostics/uv_diag.py#L42)** — Operations for inspecting and fixing common UV issues.
  - `UvDiagnostics.find_non_manifold_uvs(objects: NodeSeq) -> dict` *(static)* — Map each mesh in *objects* to its non-manifold UVs, via ``polyInfo``.
  - `UvDiagnostics.repair_non_manifold_uvs(cls, objects: NodeSeq) -> dict` *(class)* — Repair non-manifold UVs on *objects* by re-mapping the affected faces.
  - `UvDiagnostics.find_lightmap_uv_set(cls, shape, all_sets=None, names=None)` *(class)* — Detect a lightmap UV set on *shape*, or ``None``.
  - `UvDiagnostics.is_bakeable_lightmap(cls, shape, uv_set) -> bool` *(class)* — True if *uv_set* is usable as a lightmap: has UVs, non-overlapping,
  - `UvDiagnostics.cleanup_uv_sets(cls, objects: NodeSeq, remove_empty: bool = True, keep_only_primary: bool = True, rename_to_map1: bool = True, force_rename: bool = False, prefer_largest_area: bool = False, protect: Sequence[str] = (), protect_lightmaps: bool = True, dry_run: bool = False, quiet: bool = False) -> list[UvSetCleanupResult]` *(class)* — Cleanup UV sets by removing empty/secondary sets and renaming the primary to 'map1'.

<a id="core_utils--mash"></a>
### `core_utils/mash.py`

- **[`class MashNetworkNodes(object)`](mayatk/mayatk/core_utils/mash.py#L40)** — Lightweight container for the core nodes created per network.
  - `MashNetworkNodes.as_tuple(self)`
- **[`class MashToolkit(object)`](mayatk/mayatk/core_utils/mash.py#L54)** — Thin wrapper around MASH API for building and baking networks.
  - `MashToolkit.ensure_plugin_loaded()` *(static)*
  - `MashToolkit.create_network(cls, network=None, objects=None, networkName='MASH#', geometry='Mesh', distType='linearNetwork', hideOnCreate=True)` *(class)* — Create (or populate) a MASH network and return it with its core nodes.
  - `MashToolkit.bake_instancer(cls, network, instancer, bakeTranslate=True, bakeRotation=True, bakeScale=True, bakeAnimation=False, bakeVisibility=True, bakeToInstances=False, upstreamNodes=False, _getMObjectFromName=None)` *(class)* — Convert an instancer's points to real geometry.

<a id="core_utils--preview"></a>
### `core_utils/preview.py`

Hermetic preview with replay-on-commit (H1 design).

- **[`class OperationError(Exception)`](mayatk/mayatk/core_utils/preview.py#L70)** — User-facing operation failure for the Preview message box.
- **[`class CleanupContract`](mayatk/mayatk/core_utils/preview.py#L87)** — Captures and reverses side effects of a previewed operation.
  - `CleanupContract.add_file(self, path) -> None`
  - `CleanupContract.record_modification(self, node: str, attr: str) -> None`
  - `CleanupContract.rollback(self) -> None`
- **[`class Preview(_PreviewInternal)`](mayatk/mayatk/core_utils/preview.py#L505)** — Hermetic preview orchestrator (H1).
  - `Preview.cleanup_all_instances(cls) -> None` *(class)*
  - `Preview.init_show_hide_behavior(self, enable_on_show: bool, disable_on_hide: bool) -> None`
  - `Preview.conditionally_enable(self) -> None`
  - `Preview.conditionally_disable(self) -> None`
  - `Preview.toggle(self, state: bool) -> None`
  - `Preview.validate_operation(self, objects: List[Any]) -> bool`
  - `Preview.enable(self) -> None`
  - `Preview.refresh(self, *args) -> None` — Roll back the previous preview and re-run perform_operation.
  - `Preview.disable(self) -> None` — Roll back the preview without committing.
  - `Preview.finalize_changes(self) -> None` — Commit -- with a live preview, or straight from the selection.
  - `Preview.cleanup(self) -> None`
  - `Preview.enabled(self) -> bool` *(property)*
  - `Preview.operated_object_count(self) -> int` *(property)*
  - `Preview.get_operated_objects(self) -> List[str]`
  - `Preview.cleanup_all_previews() -> None` *(static)*
  - `Preview.apply_result_selection(widget, results, *, object_mode: bool = False, defer: bool = False) -> None` *(static)* — Select the operation's result(s) — or explicitly deselect them — per a

<a id="core_utils--script_job_manager"></a>
### `core_utils/script_job_manager.py`

Centralized Maya event subscription manager.

- **[`class ScriptJobManager`](mayatk/mayatk/core_utils/script_job_manager.py#L75)** — Centralized Maya scriptJob event dispatcher.
  - `ScriptJobManager.instance(cls) -> 'ScriptJobManager'` *(class)* — Return the module-wide singleton, creating it on first access.
  - `ScriptJobManager.reset(cls) -> None` *(class)* — Tear down the singleton and allow a fresh one to be created.
  - `ScriptJobManager.subscribe(self, event: str, callback: Callable, *, owner: Any = None, ephemeral: bool = False) -> int` — Register *callback* for a Maya scriptJob *event*.
  - `ScriptJobManager.add_om_callback(self, register_fn: Callable, *register_args: Any, owner: Any = None) -> Optional[int]` — Register an OpenMaya ``MMessage`` callback under SJM management.
  - `ScriptJobManager.unsubscribe(self, token: int) -> None` — Remove a single subscription by *token* (script job or OM).
  - `ScriptJobManager.unsubscribe_all(self, owner: Any) -> None` — Remove every subscription registered under *owner* (both kinds).
  - `ScriptJobManager.connect_cleanup(self, widget, owner: Any) -> None` — Connect *widget*.destroyed → :meth:`unsubscribe_all` for *owner*.
  - `ScriptJobManager.suppress(self, token: int) -> None` — Temporarily silence a subscription without removing it.
  - `ScriptJobManager.resume(self, token: int) -> None` — Undo one :meth:`suppress`;
  - `ScriptJobManager.suppressed(self, *tokens: Optional[int]) -> Iterator[None]` — Silence *tokens* for the duration of a ``with`` block.
  - `ScriptJobManager.status(self) -> Dict[str, Any]` — Return a snapshot of managed and unmanaged Maya event listeners.
  - `ScriptJobManager.print_status(self) -> None` — Pretty-print :meth:`status` for interactive debugging in Maya.
  - `ScriptJobManager.teardown(self) -> None` — Kill every managed scriptJob, OM callback, and subscription.

<a id="display_utils--_display_utils"></a>
### `display_utils/_display_utils.py`

- **[`class DisplayUtils(ptk.HelpMixin)`](mayatk/mayatk/display_utils/_display_utils.py#L17)**
  - `DisplayUtils.add_to_isolation(func: Callable) -> Callable` *(static)* — A decorator to add the result to the current isolation set.
  - `DisplayUtils.is_templated(obj: Union[str, object]) -> bool` *(static)* — Check if a given object is templated.
  - `DisplayUtils.set_visibility(cls, elements: Union[str, object, List], visibility: bool = True, include_ancestors: bool = True, affect_layers: bool = True) -> None` *(class)* — Sets the visibility of specified elements in the Maya scene.
  - `DisplayUtils.get_visible_geometry(cls, shapes: bool = False, consider_templated_visible: bool = False, inherit_parent_visibility: bool = False, consider_animated_visible: bool = False) -> List[str]` *(class)* — Get a list of visible geometry.
  - `DisplayUtils.add_to_isolation_set(objects: Union[str, object, List[Union[str, object]]])` *(static)* — Adds the specified transform objects to the current isolation set if isolation mode is active in th…
  - `DisplayUtils.reset_viewport(max_res=4096)` *(static)* — Resets Viewport 2.0 to fix graphical glitches (e.g.

<a id="display_utils--color_id"></a>
### `display_utils/color_id.py`

- **[`class ColorUtils`](mayatk/mayatk/display_utils/color_id.py#L17)**
  - `ColorUtils.assign_material(obj: str, color: Tuple[float, float, float]) -> str` *(static)* — Assigns a material to an object based on the RGB value.
  - `ColorUtils.set_color_attribute(cls, obj: str, color: Tuple[float, float, float], attr_type: str, force: bool = False) -> None` *(class)* — Applies color based on the attribute type specified, optionally overriding attribute locks.
  - `ColorUtils.get_material_color(cls, obj: str) -> Optional[Tuple[float, float, float]]` *(class)* — Gets the color of the object's material (the first, on a multi-shader mesh).
  - `ColorUtils.get_wireframe_color(obj: str, normalize: bool = False) -> Optional[Tuple[float, float, float]]` *(static)* — Gets the wireframe color of the given object.
  - `ColorUtils.get_vertex_color(obj: str, vertex_id: int) -> Optional[Tuple[float, float, float]]` *(static)* — Gets the color of a specific vertex on the object.
  - `ColorUtils.set_vertex_color(objects: List[str], color: Tuple[float, float, float]) -> None` *(static)* — Applies the specified color to the object's vertices.
  - `ColorUtils.get_color_difference(color1: Tuple[float, float, float], color2: Tuple[float, float, float]) -> float` *(static)* — Calculate the average difference between two RGB colors.
  - `ColorUtils.add_to_color_set(cls, objects: List[str], color: Tuple[float, float, float]) -> Optional[str]` *(class)* — Group ``objects`` into a stamped ``ID_<HEX>`` objectSet;
  - `ColorUtils.get_color_set_color(cls, obj: str) -> Optional[Tuple[float, float, float]]` *(class)* — The exact color stamped on the object's ID set, or None.
  - `ColorUtils.remove_from_color_sets(cls, objects: List[str]) -> None` *(class)* — Drop ``objects`` from every stamped ID set;
- **[`class ColorId(ColorUtils)`](mayatk/mayatk/display_utils/color_id.py#L270)**
  - `ColorId.apply_color(cls, objects: List[str], color: Optional[Tuple[float, float, float]] = None, apply_to_material: bool = False, apply_to_vertex: bool = False, apply_to_wireframe: bool = False, apply_to_outliner: bool = False, set_per_color: bool = False) -> None` *(class)* — Applies color based on given criteria to objects.
  - `ColorId.get_objects_by_color(cls, target_color: Tuple[float, float, float], threshold: float = 0.1, check_material_color: bool = False, check_vertex_color: bool = False, check_wireframe_color: bool = False, check_outliner_color: bool = False, check_set: bool = False) -> List[str]` *(class)* — Select objects by color, with optional checks for material, vertex, wireframe, and outliner colors.
  - `ColorId.reset_colors(cls, objects: List[str], reset_outliner: bool = True, reset_wireframe: bool = True, reset_vertex: bool = True, reset_material: bool = True, reset_sets: bool = True) -> None` *(class)* — Resets colors to default for given objects, with options to specify which color types to reset.
  - `ColorId.reset_vertex_colors(objects: List[str]) -> None` *(static)* — Resets vertex colors for the given object(s), handling potential errors gracefully.
- **[`class ColorIdSlots(ColorId)`](mayatk/mayatk/display_utils/color_id.py#L457)**
  - `ColorIdSlots.header_init(self, widget)` — Configure header help text and preset combobox.
  - `ColorIdSlots.selected_objects(self) -> List[str]` *(property)* — Return the currently selected objects, or an empty list if no objects are selected.
  - `ColorIdSlots.selected_button(self) -> Optional[object]` *(property)* — Return the currently selected button in the button group.
  - `ColorIdSlots.target_color(self) -> Optional[Tuple[float, float, float]]` *(property)* — Return the color of the selected button, or None if no button is selected.
  - `ColorIdSlots.b000(self) -> None` — Reset Colors — clear the ENABLED channels (Ctrl+click resets all geometry).
  - `ColorIdSlots.b001(self) -> None` — Apply selected color to selected objects (on the enabled channels).
  - `ColorIdSlots.b002(self) -> None` — Select objects by the currently selected color (across the enabled channels).
  - `ColorIdSlots.b003(self) -> None` — Pick up the selected object's wireframe color into the active color button (eyedropper).

<a id="display_utils--exploded_view"></a>
### `display_utils/exploded_view.py`

- **[`class ExplodedView`](mayatk/mayatk/display_utils/exploded_view.py#L19)**
  - `ExplodedView.objects(self) -> list` *(property)* — Return assigned objects or fallback to current selection.
  - `ExplodedView.calculate_repulsive_force_vectorized(cls, positions, sizes, scale=0.05)` *(class)* — Vectorized calculation of repulsive forces between objects.
  - `ExplodedView.arrange_objects(self, nodes: list, convergence_threshold: float = 0.0001, max_iterations: int = 1000, max_movement: float = 1.0) -> int` — Arranges a list of objects in 3D space to avoid overlap.
  - `ExplodedView.explode(self)` — Explode the objects.
  - `ExplodedView.un_explode(self)` — Un-explode the objects.
  - `ExplodedView.toggle_explode(self)` — Toggle explode state of the objects.
  - `ExplodedView.un_explode_all(self)` — Un-explode all
- **[`class ExplodedViewSlots(ExplodedView)`](mayatk/mayatk/display_utils/exploded_view.py#L238)** — Exploded View Slots
  - `ExplodedViewSlots.header_init(self, widget)` — Configure header help text.
  - `ExplodedViewSlots.b000(self)` — Explode button
  - `ExplodedViewSlots.b001(self)` — Un-explode selected button
  - `ExplodedViewSlots.b002(self)` — Un-explode all button
  - `ExplodedViewSlots.b003(self)` — Toggle Exlode

<a id="edit_utils--_curtain_drape"></a>
### `edit_utils/_curtain_drape.py`

Procedural draped-cloth (curtain) drape engine — pure geometry, no DCC.

- **[`class CurtainDrape(_CurtainDrapeInternal)`](mayatk/mayatk/edit_utils/_curtain_drape.py#L69)** — Drape a grid into a pleated, gravity-sagged curtain — pure math.
  - `CurtainDrape.prepare(self) -> Tuple[int, int, List[Tuple[Vec, Vec, Vec]]]` — Precompute the per-build state and return ``(u_segs, v_segs, frames)``.
  - `CurtainDrape.grid_points(self) -> Tuple[int, int, List[Vec]]` — The full draped grid: ``(u_segs, v_segs, points)``.
  - `CurtainDrape.drape(self, u, v, pos, tan, normal) -> Vec` — Place one cloth vertex.

<a id="edit_utils--_edit_utils"></a>
### `edit_utils/_edit_utils.py`

- **[`class EditUtils(ptk.HelpMixin, _EditUtilsInternal)`](mayatk/mayatk/edit_utils/_edit_utils.py#L333)**
  - `EditUtils.combine_objects(objects=None, group_by_material=False, cluster_by_distance=False, threshold=10000.0, uninstance=False, **kwargs)` *(static)* — Combine multiple meshes.
  - `EditUtils.group_objects(objects=None)` *(static)* — Group the given objects (or selection), center the pivot, and rename the group.
  - `EditUtils.ungroup_objects(objects=None) -> List[str]` *(static)* — Inverse of `group_objects` — dissolve the given group(s) (or selection).
  - `EditUtils.separate_objects(objects=None, by_material: bool = False, group_by_material: bool = False, center_pivots: bool = True, rename: bool = False, uninstance: bool = False) -> List` *(static)* — Separate meshes into individual objects.
  - `EditUtils.merge_vertices(objects, tolerance=0.001, selected_only=False)` *(static)* — Merge Vertices on the given objects.
  - `EditUtils.merge_vertex_pairs(vertices)` *(static)* — Merge vertices in pairs by moving them to their center and merging.
  - `EditUtils.detach_components(components=None, duplicate: bool = True, separate: bool = True, offset: bool = False, keep_faces_together: bool = True, center_pivot: bool = True) -> Optional[List]` *(static)* — Detach mesh components (vertices or faces) from their parent mesh.
  - `EditUtils.decimate(objects=None, percentage: float = 50.0, preserve_borders: bool = True, preserve_hard_edges: bool = True, preserve_uv_borders: bool = True, preserve_quads: bool = True, symmetry: bool = False, symmetry_tolerance: float = 0.01, delete_history: bool = True) -> List[str]` *(static)* — Decimate (``polyReduce``) meshes toward a target reduction percentage.
  - `EditUtils.dissolve_coplanar(objects=None, angle_tolerance: float = 1.0, delete_history: bool = True) -> List[str]` *(static)* — Planar decimation (limited dissolve) — merge faces across near-coplanar edges.
  - `EditUtils.get_all_faces_on_axis(obj, axis='x', pivot='center', use_object_axes=True)` *(static)* — Get all faces on the specified axis of an object.
  - `EditUtils.cut_along_axis(cls, objects, axis='x', pivot='center', amount=1, offset=0, spacing=0.0, distribution='linear', weight_bias=0.5, weight_curve=2.0, invert=False, ortho=False, delete=False, mirror=False, use_object_axes=True)` *(class)* — Cut objects along the specified axis.
  - `EditUtils.delete_along_axis(cls, objects, axis='-x', pivot='center', delete_history=True, mirror=False, use_object_axes=True)` *(class)* — Delete faces along the specified axis and optionally mirror the result.
  - `EditUtils.mirror(cls, objects, axis: str = 'x', pivot: Union[str, tuple] = 'object', mergeMode: int = -1, use_object_axes: bool = True, delete_original: bool = False, center_pivot: bool = True, **kwargs)` *(class)* — Mirror geometry across a given axis.
  - `EditUtils.mirror_instance(cls, objects=None, axis: str = 'x', pivot: Union[str, tuple] = 'object', use_object_axes: bool = True) -> list` *(class)* — Mirror as **instances**: each object gets a linked copy reflected
  - `EditUtils.separate_mirrored_mesh(mirror_node: str, center_pivot: bool = True, delete_original: bool = False) -> Optional[str]` *(static)* — Separate mirrored geometry and clean up hierarchy, history, and parenting.
  - `EditUtils.get_overlapping_duplicates(objects: Optional[List] = None, retain_given_objects: bool = False, select: bool = False, verbose: bool = False) -> set` *(static)* — Find duplicate, overlapping geometry at the object (transform) level.
  - `EditUtils.find_non_manifold_vertex(objects, select=1)` *(static)* — Locate a connected vertex of non-manifold geometry where the faces share a single vertex.
  - `EditUtils.split_non_manifold_vertex(vertex, select=True)` *(static)* — Separate a connected vertex of non-manifold geometry where the faces share a single vertex.
  - `EditUtils.get_overlapping_vertices(objects, threshold=0.0003)` *(static)* — Query the given objects for overlapping vertices.
  - `EditUtils.get_overlapping_faces(cls, objects, delete_history=False)` *(class)* — Get any duplicate overlapping faces of the given objects.
  - `EditUtils.get_similar_mesh(objects, tolerance=0.0, inc_orig=False, select=False, **kwargs)` *(static)* — Find similar geometry objects using the polyEvaluate command.
  - `EditUtils.get_similar_topo(obj, inc_orig=False, **kwargs)` *(static)* — Find similar geometry objects using the polyCompare command.
  - `EditUtils.invert_geometry(objects: Optional[List] = None, select: bool = False) -> List[str]` *(static)* — Invert selection to unselected mesh transforms.
  - `EditUtils.invert_components(objects: Optional[List] = None, select: bool = False) -> List[str]` *(static)* — Invert selection of mesh components (verts, edges, or faces).
  - `EditUtils.delete_selected()` *(static)* — Delete selected components and/or objects in Autodesk Maya.
  - `EditUtils.create_curve_from_edges(edges: Optional[List[str]] = None, **kwargs)` *(static)* — Create a curve from selected polygon edges or a provided list of edges.

<a id="edit_utils--bevel"></a>
### `edit_utils/bevel.py`

- **[`class Bevel`](mayatk/mayatk/edit_utils/bevel.py#L9)**
  - `Bevel.bevel(edges, width=0.5, segments=1, autoFit=True, depth=1, mitering=0, miterAlong=0, chamfer=True, worldSpace=True, smoothingAngle=30, fillNgons=True, mergeVertices=True, mergeVertexTolerance=0.0001, miteringAngle=180, angleTolerance=180)` *(static)* — Bevels the given edges with highly customizable options for topology,
- **[`class BevelSlots`](mayatk/mayatk/edit_utils/bevel.py#L110)**
  - `BevelSlots.header_init(self, widget)` — Configure header help text.
  - `BevelSlots.perform_operation(self, objects, contract)`

<a id="edit_utils--bridge"></a>
### `edit_utils/bridge.py`

- **[`class Bridge`](mayatk/mayatk/edit_utils/bridge.py#L13)**
  - `Bridge.bridge(edges, **kwargs)` *(static)* — Bridge open edge loops, grouped per owning mesh.
  - `Bridge.get_child_curves_from_bridge(mesh_nodes)` *(static)* — Find child curves created by polyBridgeEdge operations on mesh nodes.
  - `Bridge.cleanup_bridge_curves_and_history(mesh_nodes)` *(static)* — Clean up child curves and deformer history from mesh nodes.
- **[`class BridgeSlots`](mayatk/mayatk/edit_utils/bridge.py#L167)**
  - `BridgeSlots.header_init(self, widget)` — Configure header help text.
  - `BridgeSlots.perform_operation(self, objects, contract)`

<a id="edit_utils--curtain"></a>
### `edit_utils/curtain.py`

Procedural draped-cloth (curtain) generator for Maya.

- **[`class Rail(ptk.Polyline)`](mayatk/mayatk/edit_utils/curtain.py#L77)** — Rail-polyline geometry — the line a curtain hangs from.
  - `Rail.from_selection(objects) -> Optional[Tuple[List[Vec], bool]]` *(static)* — Resolve a rail polyline from a Maya selection.
  - `Rail.sample_curve(shape: str, count: int = 200) -> Tuple[List[Vec], bool]` *(static)* — Sample a NURBS curve into a dense polyline (resampled later by length).
- **[`class CurtainMesh(CurtainDrape)`](mayatk/mayatk/edit_utils/curtain.py#L166)** — Generate a pleated, gravity-draped curtain mesh from a rail polyline.
  - `CurtainMesh.create(cls, rail: Sequence[Vec], **opts) -> str` *(class)*
  - `CurtainMesh.build(self) -> str` — Create the curtain mesh and return its transform name.
- **[`class CurtainRig`](mayatk/mayatk/edit_utils/curtain.py#L379)** — Make a curve drive a finished curtain.
  - `CurtainRig.attach(curtain: str, curve: str, dropoff: float, cluster: bool = True) -> str` *(static)* — Wire-deform *curtain* with *curve* and add per-CV cluster controls.
- **[`class CurtainSlots(ptk.LoggingMixin)`](mayatk/mayatk/edit_utils/curtain.py#L441)** — Switchboard slot wiring for the curtain UI (hermetic preview + presets).
  - `CurtainSlots.header_init(self, widget)` — Configure header help text (the preset combo lives in the panel).
  - `CurtainSlots.cmb000_init(self, widget)` — Wire the in-panel preset selector (built-in + user tiers).
  - `CurtainSlots.b001(self)` — Reset to Defaults.
  - `CurtainSlots.b002(self)` — Set Position to the bounding-box center of the selected object(s).
  - `CurtainSlots.perform_operation(self, objects, contract)` — Build the curtain from the resolved rail (Preview entry point).

<a id="edit_utils--cut_on_axis"></a>
### `edit_utils/cut_on_axis.py`

- **[`class CutOnAxis`](mayatk/mayatk/edit_utils/cut_on_axis.py#L11)**
  - `CutOnAxis.perform_cut_on_axis(objects, axis='-x', cuts=0, cut_offset=0, cut_spacing=0.0, distribution='linear', weight_bias=0.5, weight_curve=2.0, delete=False, mirror=False, pivot='manip', use_object_axes=True)` *(static)* — Iterates over provided objects and performs cut or delete operations based on the axis specified.
- **[`class CutOnAxisSlots`](mayatk/mayatk/edit_utils/cut_on_axis.py#L67)**
  - `CutOnAxisSlots.header_init(self, widget)` — Configure header help text.
  - `CutOnAxisSlots.toggle_weight_ui(self)` — Enable the weight fields only for the modes that consume them.
  - `CutOnAxisSlots.perform_operation(self, objects, contract)`

<a id="edit_utils--duplicate_grid"></a>
### `edit_utils/duplicate_grid.py`

- **[`class DuplicateGrid(ptk.LoggingMixin)`](mayatk/mayatk/edit_utils/duplicate_grid.py#L17)**
  - `DuplicateGrid.duplicate_grid(cls, objects: List[str], dimensions: Tuple[int, int, int], spacing: Union[float, Tuple[float, float, float]] = 0, mode: str = 'instance') -> Union[str, List[str]]` *(class)* — Duplicate objects in a grid pattern.
- **[`class DuplicateGridSlots(ptk.LoggingMixin)`](mayatk/mayatk/edit_utils/duplicate_grid.py#L259)**
  - `DuplicateGridSlots.header_init(self, widget)` — Configure header help text.
  - `DuplicateGridSlots.b001(self)` — Reset to Defaults: Resets all UI widgets to their default values.
  - `DuplicateGridSlots.perform_operation(self, objects, contract)`

<a id="edit_utils--duplicate_linear"></a>
### `edit_utils/duplicate_linear.py`

- **[`class DuplicateLinear`](mayatk/mayatk/edit_utils/duplicate_linear.py#L21)**
  - `DuplicateLinear.duplicate_linear(objects, num_copies, translate=(0, 0, 0), rotate=(0, 0, 0), scale=(1, 1, 1), weight_bias=0.5, weight_curve=4, pivot='object', calculation_mode='weighted', instance=True)` *(static)*
- **[`class DuplicateLinearSlots`](mayatk/mayatk/edit_utils/duplicate_linear.py#L133)**
  - `DuplicateLinearSlots.header_init(self, widget)` — Configure header help text.
  - `DuplicateLinearSlots.toggle_weight_ui(self)` — Disable weight UI components if the current calculation mode doesn't use them.
  - `DuplicateLinearSlots.b001(self)` — Reset to Defaults: Resets all UI widgets to their default values.
  - `DuplicateLinearSlots.perform_operation(self, objects, contract)` — Perform the linear duplication operation.

<a id="edit_utils--duplicate_radial"></a>
### `edit_utils/duplicate_radial.py`

- **[`class DuplicateRadial(ptk.LoggingMixin)`](mayatk/mayatk/edit_utils/duplicate_radial.py#L21)**
  - `DuplicateRadial.duplicate_radial(objects: List[str], num_copies: int, start_angle: float = 0, end_angle: float = 360, weight_bias: float = 0.5, weight_curve: float = 0.5, rotate_axis: str = 'y', offset: Tuple[float, float, float] = (0, 0, 0), translate: Tuple[float, float, float] = (0, 0, 0), rotate: Tuple[float, float, float] = (0, 0, 0), scale: Tuple[float, float, float] = (1, 1, 1), pivot: Union[str, Tuple[float, float, float]] = 'object', keep_original: bool = False, instance: bool = False, combine: bool = False, suffix: bool = True) -> Dict[str, List[str]]` *(static)* — Duplicate objects in a radial pattern.
- **[`class DuplicateRadialSlots(ptk.LoggingMixin)`](mayatk/mayatk/edit_utils/duplicate_radial.py#L302)**
  - `DuplicateRadialSlots.header_init(self, widget)` — Configure header help text.
  - `DuplicateRadialSlots.s015_init(self, widget)` — Initialize Weight Bias.
  - `DuplicateRadialSlots.s016_init(self, widget)` — Initialize Weight Curve.
  - `DuplicateRadialSlots.b001(self)` — Reset to Defaults: Resets all UI widgets to their default values.
  - `DuplicateRadialSlots.perform_operation(self, objects, contract)` — Perform the radial duplication operation.
  - `DuplicateRadialSlots.regroup_copies(self)` — Regroup the committed copies under a fresh ``*_array`` group.

<a id="edit_utils--dynamic_pipe"></a>
### `edit_utils/dynamic_pipe.py`

- **[`class DynamicPipe`](mayatk/mayatk/edit_utils/dynamic_pipe.py#L9)** — Build a pipe-style mesh by lofting NURBS circles parented to a chain of locators.
  - `DynamicPipe.create_pipe_geometry(self, segments_to_loft: Optional[Sequence[int]] = None) -> List[str]` — Loft consecutive circle pairs to produce pipe segments.
- **[`class DynamicPipeSlots`](mayatk/mayatk/edit_utils/dynamic_pipe.py#L139)** — Switchboard slot wiring for the dynamic_pipe UI.
  - `DynamicPipeSlots.header_init(self, widget)` — Configure header help text.
  - `DynamicPipeSlots.b000(self)` — Initialize Pipe — build pipe from the current ordered selection.

<a id="edit_utils--macros"></a>
### `edit_utils/macros.py`

- **[`class MacroManager(ptk.HelpMixin)`](mayatk/mayatk/edit_utils/macros.py#L26)** — Assign macro functions to hotkeys.
  - `MacroManager.set_macros(cls, *args)` *(class)* — Extends `set_macro` to accept a list of strings representing positional and keyword arguments.
  - `MacroManager.call_with_input(func, input_string)` *(static)* — Parses an input string into positional and keyword arguments, and
  - `MacroManager.set_macro(cls, name, key=None, cat=None, ann=None, default=False, delete_existing=True)` *(class)* — Sets a default runtime command with a keyboard shortcut.
  - `MacroManager.list_available_macros(cls) -> Dict[str, str]` *(class)* — Discover every ``m_*`` macro callable, mapped to its annotation.
  - `MacroManager.macro_label(cls, name: str) -> str` *(class)* — Humanize a macro name for display, e.g.
  - `MacroManager.macro_category(cls, name: str) -> str` *(class)* — Default category for a macro, derived from the ``*Macros`` mixin that
  - `MacroManager.list_categories(cls) -> List[str]` *(class)* — Sorted distinct default categories across all discoverable macros.
  - `MacroManager.macro_help(cls, name: str) -> str` *(class)* — Return a macro's full (dedented) docstring — the single source of
  - `MacroManager.get_current_bindings(cls) -> Dict[str, dict]` *(class)* — Return the *live* key + category for every available macro.
  - `MacroManager.apply_bindings(cls, bindings: Dict[str, dict]) -> None` *(class)* — Apply a binding set ``{name: {"key", "cat"}}``.
  - `MacroManager.clear_hotkey(cls, name: str, key: Optional[str] = None) -> None` *(class)* — Unbind ``name``'s hotkey (the runtime command itself is kept).
  - `MacroManager.unset_macro(cls, name: str, key: Optional[str] = None) -> None` *(class)* — Clear ``name``'s hotkey and delete its (non-default) runtime command.
  - `MacroManager.find_conflicts(cls, bindings: Dict[str, dict]) -> Dict[str, List[str]]` *(class)* — Return ``{normalized_key: [macro, ...]}`` for keys bound more than once.
  - `MacroManager.qt_sequence_to_maya_key(cls, sequence: str) -> str` *(class)* — Convert a Qt key-sequence string (``"Ctrl+Shift+I"``) to a Maya token.
  - `MacroManager.maya_key_to_qt_sequence(cls, key: str) -> str` *(class)* — Convert a Maya key token (``"ctl+sht+i"``) to a Qt key-sequence string.
  - `MacroManager.list_presets(cls) -> List[str]` *(class)* — Return all preset names (built-in + user, user shadows built-in).
  - `MacroManager.load_preset(cls, name: str) -> Dict[str, dict]` *(class)* — Return the binding set stored under *name* (``_meta`` stripped).
  - `MacroManager.save_preset(cls, name: str, bindings: Optional[Dict[str, dict]] = None) -> str` *(class)* — Save *bindings* (default: the current bindings) as user preset *name*.
  - `MacroManager.delete_preset(cls, name: str) -> bool` *(class)* — Delete a *user* preset (built-ins are read-only).
  - `MacroManager.get_active_preset(cls) -> Optional[str]` *(class)* — The last-selected/applied preset name, or ``None``.
  - `MacroManager.set_active_preset(cls, name: Optional[str]) -> None` *(class)* — Set (or clear, with ``None``) the active-preset pointer.
  - `MacroManager.apply_saved_macros(cls, name: Optional[str] = None) -> None` *(class)* — Apply a saved preset/template's bindings to Maya on demand.
  - `MacroManager.editor_categories(cls) -> List[str]` *(class)* — Mixin-derived categories plus any custom category carried by the
  - `MacroManager.get_editor_registry(cls, category: str) -> List[dict]` *(class)* — Editor-shaped entries for every macro in *category*.
  - `MacroManager.apply_editor_binding(cls, name: str, sequence: str) -> None` *(class)* — Apply a Qt key sequence captured in the editor (``""`` clears).
  - `MacroManager.export_bindings(cls) -> Dict[str, dict]` *(class)* — The persist-worthy subset of the live bindings — every macro with a
  - `MacroManager.import_bindings(cls, data: Optional[Dict[str, dict]]) -> int` *(class)* — Apply a loaded binding set (the preset ``value_applier``): release
  - `MacroManager.show_editor(cls, parent=None)` *(class)* — Open the Macro Manager — the unified uitk ``ShortcutEditor`` over
- **[`class DisplayMacros`](mayatk/mayatk/edit_utils/macros.py#L875)**
  - `DisplayMacros.m_component_id_display()` *(static)* — Toggle Component Id Display through vertices, edges, faces, UVs, and off.
  - `DisplayMacros.m_normals_display()` *(static)* — Toggle face normals, vertex normals, tangents, and off.
  - `DisplayMacros.m_soft_edge_display()` *(static)* — Toggle Soft Edge Display.
  - `DisplayMacros.m_toggle_visibility()` *(static)* — Toggle visibility of the selected objects, keeping them selected.
  - `DisplayMacros.m_toggle_uv_border_edges(objects)` *(static)* — Toggle the display of UV border edges for the given objects.
  - `DisplayMacros.m_back_face_culling(objects) -> None` *(static)* — Toggle Back-Face Culling on selected objects, or on all objects if none are selected.
  - `DisplayMacros.m_isolate_selected() -> None` *(static)* — Isolate the current selection in the active 3D viewport.
  - `DisplayMacros.m_cycle_display_state(objects) -> None` *(static)* — Cycle the display state of the selection: Visible -> XRay -> Templated -> Hidden.
  - `DisplayMacros.m_wireframe_toggle(objects) -> None` *(static)* — Toggle Wireframe Display on selected objects, or on all objects if none are selected.
  - `DisplayMacros.m_grid() -> bool` *(static)* — Toggle the grid.
  - `DisplayMacros.m_grid_and_image_planes() -> None` *(static)* — Toggle grid and image plane visibility together.
  - `DisplayMacros.m_frame(cls, steps: int = 2, adjust_clipping: bool = True) -> None` *(class)* — Frame the selection at the ideal working distance;
  - `DisplayMacros.m_smooth_preview(cls, objects) -> None` *(class)* — Toggle smooth mesh preview.
  - `DisplayMacros.m_wireframe() -> None` *(static)* — Toggles the wireframe display state.
  - `DisplayMacros.m_material_override()` *(static)* — Toggle the viewport's default-material override.
  - `DisplayMacros.m_shading(cls) -> None` *(class)* — Toggles viewport display mode between wireframe, smooth shaded with textures off,
  - `DisplayMacros.m_lighting(cls) -> None` *(class)* — Toggles viewport lighting between different states: default, all lights, active lights,
- **[`class EditMacros`](mayatk/mayatk/edit_utils/macros.py#L1495)**
  - `EditMacros.m_group(objects=None)` *(static)* — Group the given objects (or selection), center the pivot, and rename the group.
  - `EditMacros.m_ungroup(objects=None)` *(static)* — Ungroup the selected group(s) — children keep their world transforms.
  - `EditMacros.m_combine(objects=None, group_by_material=False, cluster_by_distance=False, threshold=10000.0, **kwargs)` *(static)* — Combine multiple meshes.
  - `EditMacros.m_boolean(objects, repair_mesh=True, keep_boolean=True, **kwargs)` *(static)* — Perform a boolean operation on two meshes using cmds, managing shorthand and full parameter names d…
  - `EditMacros.m_lock_vertex_normals(objects)` *(static)* — Toggle lock/unlock vertex normals.
  - `EditMacros.m_paste_and_rename() -> None` *(static)* — Paste and rename by removing 'pasted__' prefix and reference file names,
  - `EditMacros.m_multi_component() -> None` *(static)* — Enable the multi-component selection mask.
  - `EditMacros.m_merge_vertices(objects, tolerance=0.001) -> None` *(static)* — Merge vertices within a small distance tolerance.
- **[`class SelectionMacros`](mayatk/mayatk/edit_utils/macros.py#L1761)**
  - `SelectionMacros.m_object_selection() -> None` *(static)* — Set object selection mask.
  - `SelectionMacros.m_vertex_selection() -> None` *(static)* — Set vertex selection mask.
  - `SelectionMacros.m_edge_selection() -> None` *(static)* — Set edge selection mask.
  - `SelectionMacros.m_face_selection() -> None` *(static)* — Set face selection mask.
  - `SelectionMacros.m_invert_selection() -> None` *(static)* — Invert the current selection of geometry or components.
  - `SelectionMacros.m_toggle_selectability(objects)` *(static)* — Toggle selectability of the given objects.
  - `SelectionMacros.m_toggle_UV_select_type() -> None` *(static)* — Toggles between UV shell and UV component selection.
  - `SelectionMacros.m_invert_component_selection() -> None` *(static)* — Invert the component selection on the currently selected objects.
- **[`class UiMacros`](mayatk/mayatk/edit_utils/macros.py#L1924)**
  - `UiMacros.m_toggle_panels(toggle_menu: bool = True, toggle_panels: bool = True) -> None` *(static)* — Toggle UI toolbars and menu bar in sync.
- **[`class AnimationMacros`](mayatk/mayatk/edit_utils/macros.py#L1960)**
  - `AnimationMacros.m_set_selected_keys(objects) -> None` *(static)* — Set keys for any attributes (channels) that are selected in the channel box.
  - `AnimationMacros.m_unset_selected_keys(objects) -> None` *(static)* — Un-set keys for any attributes (channels) that are selected in the channel box.
- **[`class Macros(MacroManager, DisplayMacros, EditMacros, SelectionMacros, AnimationMacros, UiMacros)`](mayatk/mayatk/edit_utils/macros.py#L1987)**

<a id="edit_utils--mesh_graph"></a>
### `edit_utils/mesh_graph.py`

- **[`class Graph`](mayatk/mayatk/edit_utils/mesh_graph.py#L11)**
  - `Graph.add_node(self, node, data=None)` — Adds a node to the graph along with its associated data.
  - `Graph.add_edge(self, node1, node2, weight=1)` — Adds an edge between two specified nodes with an optional weight.
  - `Graph.heuristic(self, node1, node2)` — Calculates the default heuristic between two nodes.
  - `Graph.find_path(self, start, goal, algorithm='a_star')` — Finds a path from start node to goal node using the specified algorithm.
  - `Graph.a_star(self, start, goal)` — Implements the A* algorithm to find the shortest path from start to goal node.
  - `Graph.dijkstra(self, start, goal)` — Implements Dijkstra's algorithm to find the shortest path from start to goal node.
- **[`class MeshGraph(Graph)`](mayatk/mayatk/edit_utils/mesh_graph.py#L153)**
  - `MeshGraph.build_graph(self)` — Efficiently builds graph based on the mesh's topology.
  - `MeshGraph.heuristic(self, node1, node2)`

<a id="edit_utils--mirror"></a>
### `edit_utils/mirror.py`

- **[`class MirrorSlots(ptk.LoggingMixin)`](mayatk/mayatk/edit_utils/mirror.py#L11)**
  - `MirrorSlots.header_init(self, widget)` — Configure header help text.
  - `MirrorSlots.prepare_operation(self, objects)` — Break instance links once, before the preview contract exists.
  - `MirrorSlots.perform_operation(self, objects, contract)`

<a id="edit_utils--naming--_naming"></a>
### `edit_utils/naming/_naming.py`

- **[`class Naming(ptk.HelpMixin)`](mayatk/mayatk/edit_utils/naming/_naming.py#L20)**
  - `Naming.rename(cls, objects: Union[str, 'object', List[Union[str, 'object']]], to: str, fltr: str = '', regex: bool = False, ignore_case: bool = False, retain_suffix: bool = False, valid_suffixes: Optional[List[str]] = None, collapse_padding: bool = True) -> List[str]` *(class)* — Rename scene objects based on specified patterns and filters, ensuring compliance with Maya's namin…
  - `Naming.generate_unique_name(cls, base_name, suffix='_', padding=3)` *(class)* — Generate a unique name based on the base_name.
  - `Naming.conform_shape_names(cls, objects: Union[str, 'object', List[Union[str, 'object']], None] = None, force: bool = False) -> List[Tuple[str, str]]` *(class)* — Rename shape nodes to Maya's conventional ``<transform>Shape`` form.
  - `Naming.strip_illegal_chars(input_data, replace_with='_')` *(static)* — Strips illegal characters from a string or a list of strings, replacing them with a specified chara…
  - `Naming.strip_chars(objects: Union[str, object, List[Union[str, object]]], num_chars: int = 1, trailing: bool = False) -> List[str]` *(static)* — Deletes leading or trailing characters from the names of the provided objects,
  - `Naming.set_case(objects=None, case='capitalize')` *(static)* — Rename objects following the given case.
  - `Naming.suffix_by_type(objects: Union[str, object, List[Union[str, object]]], group_suffix: str = '_GRP', locator_suffix: str = '_LOC', joint_suffix: str = '_JNT', mesh_suffix: str = '_GEO', nurbs_curve_suffix: str = '_CRV', camera_suffix: str = '_CAM', light_suffix: str = '_LGT', display_layer_suffix: str = '_LYR', custom_suffixes: Optional[Dict[str, str]] = None, strip: Union[str, List[str]] = None, strip_trailing_ints: bool = False, strip_trailing_underscores: bool = False, strip_trailing_padding: bool = True) -> List[str]` *(static)* — Appends a conventional suffix based on Maya object type, stripping any existing known suffix.
  - `Naming.append_location_based_suffix(objects, first_obj_as_ref=False, alphabetical=False, strip_trailing_ints=True, strip_defined_suffixes=True, valid_suffixes=None, reverse=False, independent_groups=False)` *(static)* — Rename objects with a suffix defined by its location from origin.

<a id="edit_utils--naming--naming_slots"></a>
### `edit_utils/naming/naming_slots.py`

- **[`class NamingSlots(Naming, ptk.LoggingMixin)`](mayatk/mayatk/edit_utils/naming/naming_slots.py#L15)**
  - `NamingSlots.header_init(self, widget)` — Configure header menu with tool description and workflow instructions.
  - `NamingSlots.valid_suffixes(self)` *(property)* — Get current valid suffixes from tb003 widget fields.
  - `NamingSlots.txt000_init(self, widget)` — Initialize Find
  - `NamingSlots.txt000(self, widget)` — Find: filter/select scene objects whose name matches the search pattern.
  - `NamingSlots.txt001_init(self, widget)` — Initialize Rename
  - `NamingSlots.txt001(self, widget)` — Rename: rename matched objects (find → replace, with regex / suffix options).
  - `NamingSlots.tb000_init(self, widget)` — Initialize Convert Case
  - `NamingSlots.tb000(self, widget)` — Convert Case
  - `NamingSlots.tb001_init(self, widget)` — Initialize Suffix By Location
  - `NamingSlots.tb001(self, widget)` — Suffix By Location
  - `NamingSlots.tb002_init(self, widget)` — Initialize Strip Chars
  - `NamingSlots.tb002(self, widget)` — Strip Chars: remove a number of leading/trailing characters from the selected names.
  - `NamingSlots.tb003_init(self, widget)` — Initialize Suffix By Type
  - `NamingSlots.tb003(self, widget)` — Suffix By Type

<a id="edit_utils--primitives"></a>
### `edit_utils/primitives.py`

Primitive creation utilities for Maya.

- **[`class Primitives`](mayatk/mayatk/edit_utils/primitives.py#L23)** — Utilities for creating primitive objects in Maya.
  - `Primitives.create_default_primitive(cls, baseType, subType, **kwargs)` *(class)* — Create a primitive object with flexible parameters.
  - `Primitives.create_circle(axis='y', numPoints=12, radius=5, center=[0, 0, 0], mode=0, name='pCircle', history=False)` *(static)* — Create a circular polygon plane.

<a id="edit_utils--rack_builder"></a>
### `edit_utils/rack_builder.py`

Parametric EIA-310 (19-inch) equipment-rack generator.

- **[`class EIA310`](mayatk/mayatk/edit_utils/rack_builder.py#L52)** — EIA-310-D / IEC 60297-3-100 rack dimensions, in millimetres.
  - `EIA310.u_to_mm(cls, u: float) -> float` *(class)* — Rack units → millimetres.
- **[`class OccupantSpec(ptk.SchemaSpec)`](mayatk/mayatk/edit_utils/rack_builder.py#L75)** — One piece of installed rack gear, described by its front panel.
- **[`class BaySpec(ptk.SchemaSpec)`](mayatk/mayatk/edit_utils/rack_builder.py#L98)** — A single rack bay: a mounting frame holding an ordered occupant stack.
- **[`class RackSpec(ptk.SchemaSpec)`](mayatk/mayatk/edit_utils/rack_builder.py#L116)** — A complete equipment cabinet — one or more bays inside an enclosure.
- **[`class RackBuilder(ptk.LoggingMixin)`](mayatk/mayatk/edit_utils/rack_builder.py#L147)** — Emit Maya geometry for a :class:`RackSpec`.
  - `RackBuilder.build(self, group: bool = True) -> str` — Build the whole rack;
  - `RackBuilder.from_dict(cls, data: dict) -> 'RackBuilder'` *(class)* — Build directly from a raw spec ``dict`` (validated by RackSpec).

<a id="edit_utils--selection"></a>
### `edit_utils/selection.py`

- **[`class Selection(ptk.LoggingMixin, ptk.HelpMixin)`](mayatk/mayatk/edit_utils/selection.py#L19)** — Utilities for advanced Maya selection operations.
  - `Selection.select_by_type(selection_type: str, objects: List[Union[str, object]] = None, mode: str = 'replace') -> List[object]` *(static)* — Select objects by type with comprehensive type support.
  - `Selection.select_children(objects: List[Union[str, object]]) -> Set[object]` *(static)* — Select the immediate children of the given objects.
  - `Selection.select_hierarchy_above(objects: List[Union[str, object]]) -> Set[object]` *(static)* — Select all parent objects in the hierarchy above the given objects.
  - `Selection.select_hierarchy_below(objects: List[Union[str, object]]) -> Set[object]` *(static)* — Select all child objects in the hierarchy below the given objects.
  - `Selection.get_available_selection_types() -> List[str]` *(static)* — Get a list of all available selection types.
  - `Selection.get_selection_categories() -> dict` *(static)* — Get a dictionary of selection types organized by category.

<a id="edit_utils--snap"></a>
### `edit_utils/snap.py`

- **[`class Snap(ptk.HelpMixin)`](mayatk/mayatk/edit_utils/snap.py#L15)** — Vertex and mesh snapping utilities.
  - `Snap.snap_to_closest_vertex(obj1, obj2, tolerance=10.0, freeze_transforms=False)` *(static)* — Snap the vertices from object one to the closest verts on object two.
  - `Snap.snap_to_surface(source_meshes, target_mesh, offset: float = None, threshold: float = None, invert: bool = False) -> int` *(static)* — Snap source mesh vertices to the closest point on a target surface.
  - `Snap.snap_to_grid(objects=None, grid_size: float = 1.0, axes: str = 'xyz') -> int` *(static)* — Snap object pivots or vertices to the nearest grid point.
- **[`class SnapSlots`](mayatk/mayatk/edit_utils/snap.py#L270)** — UI slots for the Snap tool.
  - `SnapSlots.header_init(self, widget)` — Configure header help text.
  - `SnapSlots.b000_init(self, widget)` — Initialize Snap to Surface button option box.
  - `SnapSlots.b000(self)` — Snap to Surface button.
  - `SnapSlots.b001_init(self, widget)` — Initialize Snap to Closest Vertex button option box.
  - `SnapSlots.b001(self)` — Snap to Closest Vertex button.
  - `SnapSlots.b002_init(self, widget)` — Initialize Snap to Grid button option box.
  - `SnapSlots.b002(self)` — Snap to Grid button.

<a id="env_utils--_env_utils"></a>
### `env_utils/_env_utils.py`

- **[`class EnvUtils(ptk.HelpMixin)`](mayatk/mayatk/env_utils/_env_utils.py#L16)**
  - `EnvUtils.get_env_info(key)` *(static)* — Fetch specific information about the current Maya environment based on the provided key.
  - `EnvUtils.saved_scene_path() -> str` *(static)* — The open scene's path, or ``""`` when it has never been saved.
  - `EnvUtils.default_artifact_dir(cls) -> str` *(class)* — Return a sensible default directory for exported/baked artifacts.
  - `EnvUtils.append_maya_paths(maya_version=None)` *(static)* — Appends various Maya-related paths to the system's Python environment and sys.path.
  - `EnvUtils.load_plugin(plugin_name)` *(static)* — Loads a specified plugin.
  - `EnvUtils.vray_plugin(load=False, unload=False, query=False)` *(static)* — Load/Unload/Query the Maya Vray Plugin.
  - `EnvUtils.get_recent_files(index=None)` *(static)* — Get a list of recent files sorted by modification time.
  - `EnvUtils.get_recent_projects(index=None, format='standard')` *(static)* — Get a list of recently set projects.
  - `EnvUtils.find_autosave_directories()` *(static)* — Search for and compile a list of existing autosave directories based on
  - `EnvUtils.get_recent_autosave(cls, filter_time=None, timestamp_format='%Y-%m-%d %H:%M:%S')` *(class)* — Retrieves a list of recent autosave files from Maya autosave directories, optionally filtered by ag…
  - `EnvUtils.find_workspaces(root_dir: str, return_type: str = 'dir', ignore_empty: bool = True, recursive: bool = True, file_types: Optional[tuple] = None) -> list` *(static)* — Find Maya workspaces under a root directory.
  - `EnvUtils.get_workspace_scenes(root_dir: Optional[str] = None, full_path: bool = True, recursive: bool = False, omit_autosave: bool = True, file_types=None) -> list[str]` *(static)* — Return a list of Maya scene files (.ma/.mb) from the given or current workspace directory.
  - `EnvUtils.find_workspace_using_path(cls, scene_path: Optional[str] = None) -> Optional[str]` *(class)* — Determine the workspace directory for a given scene by moving up directory levels until a workspace…
  - `EnvUtils.current_workspace(path: Optional[str] = None) -> Optional[ptk.Workspace]` *(static)* — The active project as a ``pythontk.Workspace`` (root + parsed file rules), or None.
  - `EnvUtils.set_current_workspace(root: str) -> str` *(static)* — Make *root* Maya's active project (``workspace -openWorkspace``).
  - `EnvUtils.workspace_root(path: Optional[str] = None) -> str` *(static)* — Absolute root of the current workspace, or ''.
  - `EnvUtils.scenes_dir(path: Optional[str] = None) -> str` *(static)* — The workspace's scene folder — its ``scene`` rule → an existing ``scenes/`` →
  - `EnvUtils.source_images_dir(path: Optional[str] = None) -> str` *(static)* — The workspace's texture folder — its ``sourceImages`` rule → an existing
  - `EnvUtils.list_workspace_templates() -> list` *(static)* — Saved workspace-template names.
  - `EnvUtils.workspace_template_rules(name: Optional[str] = None) -> dict` *(static)* — File rules for building a NEW workspace: the *name*d (default: active /
  - `EnvUtils.save_workspace_template(name: str, rules: Optional[dict] = None) -> str` *(static)* — Save *rules* as workspace template *name* and make it the active default for new
  - `EnvUtils.delete_workspace_template(name: str) -> bool` *(static)* — Delete the user template *name*.
  - `EnvUtils.create_workspace(root: str, rules: Optional[dict] = None, create_dirs: bool = True) -> Optional[ptk.Workspace]` *(static)* — Create a marked workspace at *root* — File ▸ Project Window ▸ New, scripted.
  - `EnvUtils.promote_workspace(root: Optional[str] = None) -> Optional[ptk.Workspace]` *(static)* — Mark *root* (default: the active project's folder) as a shared Maya/Blender
  - `EnvUtils.reference_scene(file_path)` *(static)* — Reference a Maya scene.
  - `EnvUtils.remove_reference(file_path)` *(static)* — Remove a reference to a Maya scene.
  - `EnvUtils.is_referenced(file_path)` *(static)* — Check if a Maya scene is referenced.
  - `EnvUtils.get_reference_nodes(file_path)` *(static)* — Get the nodes from a referenced Maya scene.
  - `EnvUtils.list_references()` *(static)* — List all references in the current Maya scene.
  - `EnvUtils.export_scene_as_fbx(file_path: str = None, *, selection_only: bool = False, **fbx_options: Any) -> None` *(static)* — Export the Maya scene as an FBX file with flexible MEL command options.
  - `EnvUtils.export_scene_as_obj(file_path: str = None, *, selection_only: bool = False, materials: bool = True, smoothing: bool = True, normals: bool = True, groups: bool = True) -> str` *(static)* — Export the Maya scene as a Wavefront OBJ.
  - `EnvUtils.sanitize_namespace(namespace: str) -> str` *(static)* — Sanitize the namespace by replacing or removing illegal characters.
  - `EnvUtils.resolve_file_path_in_workspaces(selected_file: str, workspace_files: dict) -> Optional[str]` *(static)* — Resolve a file name to its full path by searching in workspace files.
  - `EnvUtils.get_workspace_file_cache(cls, workspaces: list, recursive: bool = True) -> dict` *(class)* — Build a cache of workspace files for multiple workspaces.
  - `EnvUtils.matches_autosave_pattern(filename: str) -> bool` *(static)* — Check if a file matches the Maya autosave pattern.
  - `EnvUtils.save_scene_backup(backup_path: Optional[Union[str, bool]] = True, suffix: str = '_backup', file_type: str = 'mayaAscii', force: bool = True, preserve_scene_name: bool = True) -> Optional[str]` *(static)* — Save a backup copy of the current scene.
  - `EnvUtils.find_original_for_autosave(cls, autosave_path: Optional[str] = None) -> Optional[str]` *(class)* — Resolve the original scene file an autosave was generated from.
  - `EnvUtils.save_autosave_to_original(cls, original_path: Optional[str] = None, backup_existing: bool = True) -> Optional[str]` *(class)* — Save the currently open autosave scene back to its original path.

<a id="env_utils--blender_bridge--_blender_bridge"></a>
### `env_utils/blender_bridge/_blender_bridge.py`

Blender bridge engine -- export the Maya selection and run a chosen import template in Blender.

- **[`class BlenderBridge(MayaExportMixin, ptk.ScriptLaunchBridge)`](mayatk/mayatk/env_utils/blender_bridge/_blender_bridge.py#L130)** — Export the Maya selection and run a chosen Blender import template.
  - `BlenderBridge.blender_path(self) -> Optional[str]` *(property)*
  - `BlenderBridge.params_defaults(self) -> Dict[str, Any]`
  - `BlenderBridge.render_context(self, params: Dict[str, Any]) -> Dict[str, str]`
  - `BlenderBridge.bake_lightmaps(self, out_glb: str, objects: Optional[List[Any]] = None, *, environment_hdr: Optional[str] = None, resolution: Optional[int] = None, samples: Optional[int] = None, mode: Optional[str] = None, fixture_lights: Optional[bool] = None, fixture_watts: Optional[float] = None, timeout: Optional[float] = None, reassemble: bool = True, **params: Any) -> Optional[Dict[str, Any]]` — Bake the selection's lightmaps in a headless Blender and wire them back in.
  - `BlenderBridge.reassemble_lightmaps(self, out_glb: str, objects: Optional[List[Any]] = None) -> Dict[str, str]` — Wire a finished Blender bake back into this Maya scene.
  - `BlenderBridge.list_templates() -> List[Path]` *(static)* — User-visible templates in ``templates/`` (skips underscore-prefixed).
  - `BlenderBridge.template_modes(cls, template_path: Path) -> Tuple[str, ...]` *(class)* — Modes a template declares via ``BRIDGE_MODES``;
  - `BlenderBridge.list_template_modes(cls) -> List[Tuple[str, str]]` *(class)* — ``[(stem, mode), ...]`` for every (template, mode) pairing.
  - `BlenderBridge.template_path(stem: str) -> Path` *(static)* — The template file for a combo entry's *stem*.
  - `BlenderBridge.template_output_ext(cls, template_path) -> str` *(class)* — Artifact extension a template declares via ``BRIDGE_OUTPUT_EXT``.
  - `BlenderBridge.template_timeout(cls, template_path) -> Optional[float]` *(class)* — Seconds a template declares via ``BRIDGE_TIMEOUT``, else ``None`` (spec default).

<a id="env_utils--blender_bridge--_scene_import"></a>
### `env_utils/blender_bridge/_scene_import.py`

Import a Blender scene (.blend) into Maya via a headless-Blender round-trip

- **[`class BlenderSceneImport(ptk.LoggingMixin, _BlenderSceneImportInternal)`](mayatk/mayatk/env_utils/blender_bridge/_scene_import.py#L159)** — Engine: convert a .blend to FBX via headless Blender, then import it.
  - `BlenderSceneImport.blender_path(self) -> Optional[str]` *(property)* — The Blender executable (explicit, or discovered via the bridge's AppSpec).
  - `BlenderSceneImport.require_blender(self) -> str` — Return :attr:`blender_path` or raise the spec's not-found error.
  - `BlenderSceneImport.find_scenes(root_dir: str, recursive: bool = False, extensions: Optional[Sequence[str]] = None) -> List[str]` *(static)* — Every importable Blender scene (``.blend``) under *root_dir* — sorted abs paths.
  - `BlenderSceneImport.render_script(self, src_path: str, out_path: str, *, via: str = 'fbx', embed_textures: bool = False, include_animation: bool = True) -> str` — Render the Blender-side conversion script (exposed for tests/preview).
  - `BlenderSceneImport.convert(self, src_path: str, out_path: str, *, via: str = 'fbx', timeout: float = 600, **script_opts: Any) -> 'ptk.ScriptRunResult'` — Convert *src_path* to *out_path* in a fresh headless Blender (blocking).
  - `BlenderSceneImport.import_scene(self, src_path: str, *, via: str = 'fbx', cleanup: bool = True, use_cache: bool = True, timeout: float = 600, fbx_options: Optional[Dict[str, Any]] = None, shader_type: str = 'stingray', **script_opts: Any) -> List[str]` — Import the Blender scene at *src_path*;
  - `BlenderSceneImport.mayapy_path(self) -> Optional[str]` *(property)* — The headless ``mayapy`` used for the bake — this host's own interpreter.
  - `BlenderSceneImport.require_mayapy(self) -> str` — Return :attr:`mayapy_path` or raise an error naming what's missing.
  - `BlenderSceneImport.render_bake_script(self, src_path: str, out_path: str) -> str` — Render the Maya-side intermediate->.ma bake script (exposed for
  - `BlenderSceneImport.bake(self, src_path: str, out_path: str, *, timeout: float = 600) -> Any` — Bake the USD/FBX intermediate *src_path* into the .ma at *out_path* in a
  - `BlenderSceneImport.bake_scene(self, src_path: str, *, via: str = 'fbx', use_cache: bool = True, timeout: float = 600, **script_opts: Any) -> str` — Bake *src_path* to a cached ``.ma`` and return its path — the reference path.
  - `BlenderSceneImport.bake_source(baked_path: str) -> Optional[str]` *(static)* — The foreign scene *baked_path* was baked from, or None if it is not a bake.

<a id="env_utils--blender_bridge--blender_bridge_slots"></a>
### `env_utils/blender_bridge/blender_bridge_slots.py`

Slots for the Blender bridge panel.

- **[`class BlenderBridgeSlots(MayaBridgeSlotsBase)`](mayatk/mayatk/env_utils/blender_bridge/blender_bridge_slots.py#L35)** — Slots wired to ``blender_bridge.ui`` via :class:`MayaBridgeSlotsBase`.
  - `BlenderBridgeSlots.params_module(self)` *(property)*
  - `BlenderBridgeSlots.template_dir(self) -> Path` *(property)*
  - `BlenderBridgeSlots.make_bridge(self) -> BlenderBridge`
  - `BlenderBridgeSlots.list_template_modes(self)`
  - `BlenderBridgeSlots.b000(self)` — Send the selected objects to Blender with the chosen template.

<a id="env_utils--blender_bridge--parameters"></a>
### `env_utils/blender_bridge/parameters.py`

Registry of user-tunable Blender-bridge parameters exposed to the panel.

- **[`class Parameters`](mayatk/mayatk/env_utils/blender_bridge/parameters.py#L268)** — Parameters — module namespace.
  - `Parameters.referenced_keys(script_text: str) -> 'set[str]'` *(static)* — Registered keys present in *script_text* (delegates to uitk.bridge).
  - `Parameters.defaults() -> 'dict[str, Any]'` *(static)* — Return ``{key: default}`` for every registered parameter.
  - `Parameters.render_context(values: 'dict[str, Any]') -> 'dict[str, str]'` *(static)* — Format *values* for ``StrUtils.replace_delimited`` using Python literals.

<a id="env_utils--blender_bridge--templates--_bake_scene"></a>
### `env_utils/blender_bridge/templates/_bake_scene.py`

Import a converted intermediate (USD or FBX) headlessly (mayapy) and save it as a ``.ma``

- [`import_source(cmds, engine)`](mayatk/mayatk/env_utils/blender_bridge/templates/_bake_scene.py#L74) — Import *SRC_FILE* into the empty standalone scene;
- [`apply_manifest(engine, new_nodes)`](mayatk/mayatk/env_utils/blender_bridge/templates/_bake_scene.py#L106) — Replay the conversion's texture sidecar through the shared rebuild engine.
- [`restore_empty_groups(engine, new_nodes)`](mayatk/mayatk/env_utils/blender_bridge/templates/_bake_scene.py#L123) — Empties -> correct Maya node types (FBX branch;
- [`apply_instances(engine, new_nodes)`](mayatk/mayatk/env_utils/blender_bridge/templates/_bake_scene.py#L141) — Rebuild real Maya instances from Blender's linked-duplicate groups.
- [`main()`](mayatk/mayatk/env_utils/blender_bridge/templates/_bake_scene.py#L169)

<a id="env_utils--blender_bridge--templates--_import_scene"></a>
### `env_utils/blender_bridge/templates/_import_scene.py`

Open a .blend headlessly (blender --background) and export it as FBX for a Maya import.

- [`collect_texture_manifest(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L104) — Manifest entries for every textured material on an exportable object,
- [`collect_empties(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L159) — ``[{name, display_type}, ...]`` for the scene's Empties (node-type sidecar).
- [`write_texture_manifest(entries, scene_materials, empties, path)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L182) — Sidecar for what FBX cannot carry, consumed by BlenderSceneImport.
- [`export_fbx(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L205) — Full-fidelity FBX export with per-flag tolerance across Blender versions.
- [`main()`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L239)

<a id="env_utils--blender_bridge--templates--_import_scene_usd"></a>
### `env_utils/blender_bridge/templates/_import_scene_usd.py`

Open a .blend headlessly (blender --background) and export it as USD for a Maya import.

- [`export_usd(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L85) — Whole-scene USD export with per-kwarg tolerance across Blender versions.
- [`collect_instance_groups(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L154) — Blender linked duplicates -> ``[[sanitized prim names sharing one mesh], ...]``.
- [`write_manifest(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L206) — Sidecar beside the USD carrying what the flat export cannot: instance groups.
- [`main()`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L227)

<a id="env_utils--blender_bridge--templates--_save_scene"></a>
### `env_utils/blender_bridge/templates/_save_scene.py`

Import the bridged FBX into a headless Blender and save it as a ``.blend``.

- [`apply_texture_manifest(new_objects)`](mayatk/mayatk/env_utils/blender_bridge/templates/_save_scene.py#L67) — Replay the sidecar manifest through blendertk's applier (see module docstring).
- [`main()`](mayatk/mayatk/env_utils/blender_bridge/templates/_save_scene.py#L99)

<a id="env_utils--blender_bridge--templates--bake_lightmaps"></a>
### `env_utils/blender_bridge/templates/bake_lightmaps.py`

Bake the bridged Maya selection's lightmaps in a headless Blender and write a WebXR GLB.

- [`apply_texture_manifest(new_objects)`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L126) — Replay the sidecar through blendertk's applier.
- [`light_scene(web, meshes)`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L154) — Apply the world and the fixture lights;
- [`write_return_manifest(result)`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L208) — Write the sidecar Maya reads to reassemble this bake into its own scene.
- [`main()`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L254)

<a id="env_utils--blender_bridge--templates--import"></a>
### `env_utils/blender_bridge/templates/import.py`

Import the bridged FBX into Blender, with optional clean-slate and frame-on-import behaviors.

- [`apply_texture_manifest(new_objects)`](mayatk/mayatk/env_utils/blender_bridge/templates/import.py#L57) — Replay the sidecar manifest through blendertk's applier (see module docstring).
- [`tag_node_types(new_objects)`](mayatk/mayatk/env_utils/blender_bridge/templates/import.py#L81) — Stamp ``maya_node_type`` custom props from the manifest's ``transforms``.
- [`main()`](mayatk/mayatk/env_utils/blender_bridge/templates/import.py#L113)

<a id="env_utils--devtools"></a>
### `env_utils/devtools.py`

- **[`class DevTools(CoreUtils)`](mayatk/mayatk/env_utils/devtools.py#L22)** — Tools for inspecting Maya's environment and debugging.
  - `DevTools.echo_all(state=True)` *(static)* — Toggle the 'Echo All Commands' state in the Script Editor.
  - `DevTools.find_mel(name)` *(static)* — Find the file path of a MEL procedure or script.
  - `DevTools.find_python(name)` *(static)* — Find the file path of a Python module or object.
  - `DevTools.find(cls, name)` *(class)* — Find the file path of a MEL or Python object.
  - `DevTools.grep_maya_dir(query, root_paths=None, ext='.mel', recursive=True, regex=False, context=0, max_results=500)` *(static)* — Search for a string or regex in files within Maya's script paths.
  - `DevTools.grep_mel_procs(pattern='', root_paths=None, recursive=True, include_args=True)` *(static)* — Scan MEL files for ``proc`` declarations matching a pattern.
  - `DevTools.read_mel_proc(proc_name)` *(static)* — Extract the full source text of a named MEL procedure.
  - `DevTools.find_all(cls, name)` *(class)* — Return *all* locations where *name* is defined (MEL + Python).
  - `DevTools.list_mel_globals(pattern='')` *(static)* — List global MEL variables whose names match a pattern.
  - `DevTools.get_mel_global(var_name, type_hint='string')` *(static)* — Get the value of a global MEL variable.
  - `DevTools.source_mel(path)` *(static)* — Source a MEL script.
- **[`class WidgetInspector(CoreUtils)`](mayatk/mayatk/env_utils/devtools.py#L467)** — Deep PyQt/PySide inspection tools for reverse-engineering Maya widgets.
  - `WidgetInspector.from_maya_control(cls, control_name)` *(class)* — Resolve a Maya control name to a QWidget.
  - `WidgetInspector.from_mel_global(cls, var_name)` *(class)* — Resolve a MEL global variable that holds a control name to a QWidget.
  - `WidgetInspector.main_window()` *(static)* — Return Maya's main window as a QWidget.
  - `WidgetInspector.walk(cls, widget, depth=0, max_depth=-1)` *(class)* — Recursively yield ``(depth, widget)`` for all descendants.
  - `WidgetInspector.find_children_by_type(cls, widget, type_name)` *(class)* — Find all descendants matching a Qt class name string.
  - `WidgetInspector.find_child_by_name(cls, widget, object_name)` *(class)* — Find first descendant whose ``objectName`` matches.
  - `WidgetInspector.dump_tree(widget, max_depth=3)` *(static)* — Print an indented widget tree for debugging.
  - `WidgetInspector.dump_properties(widget)` *(static)* — Print all Qt dynamic properties on a widget.
  - `WidgetInspector.list_signals(widget)` *(static)* — List all signals defined on a widget's class.
  - `WidgetInspector.list_slots(widget)` *(static)* — List all slots defined on a widget's class.
  - `WidgetInspector.find_by_property(cls, widget, prop_name, value=None, max_depth=-1)` *(class)* — Find descendants that have a Qt property matching criteria.
  - `WidgetInspector.snapshot(cls, widget, max_depth=4)` *(class)* — Capture the full state of a widget subtree as a serializable dict.
  - `WidgetInspector.diff_snapshots(before, after, path='')` *(static)* — Compare two snapshots and return a list of differences.
  - `WidgetInspector.connect_signal_logger(cls, widget, signal_name=None, callback=None)` *(class)* — Connect a logger to signals on *widget* so you can trace when they fire.
  - `WidgetInspector.dump_actions(cls, widget)` *(class)* — List all QAction items attached to a widget (menus, context menus).
  - `WidgetInspector.find_item_views(cls, widget)` *(class)* — Find all QAbstractItemView descendants (QTreeView, QListView, etc.).
  - `WidgetInspector.dump_model(view, max_rows=50)` *(static)* — Print the contents of the model attached to a QAbstractItemView.
  - `WidgetInspector.get_selection_model(view)` *(static)* — Return the QItemSelectionModel for a view.

<a id="env_utils--fbx_utils"></a>
### `env_utils/fbx_utils.py`

- **[`class FbxUtils(ptk.HelpMixin)`](mayatk/mayatk/env_utils/fbx_utils.py#L19)** — Low-level utilities for FBX import/export operations in Maya.
  - `FbxUtils.load_plugin()` *(static)* — Ensure the fbxmaya plugin is loaded.
  - `FbxUtils.embed_media_write_cwd()` *(static)* — Yield with the process CWD at the workspace root when the live FBX
  - `FbxUtils.reset_import()` *(static)* — Reset the FBX plugin's global IMPORT options to factory defaults.
  - `FbxUtils.set_fbx_options(options: Dict[str, Any])` *(static)* — Apply FBX export options via MEL commands.
  - `FbxUtils.load_preset(preset_path: str)` *(static)* — Load an FBX export preset file.
  - `FbxUtils.export(cls, file_path: str, objects: Optional[List] = None, preset_file: Optional[str] = None, options: Optional[Dict[str, Any]] = None, selection_only: bool = True) -> str` *(class)* — Export geometry to an FBX file.
  - `FbxUtils.import_scene(cls, file_path: str, namespace: Optional[str] = None, options: Optional[Dict[str, Any]] = None, return_new_nodes: bool = True) -> List[str]` *(class)* — Import an FBX file, optionally isolated into a namespace.
  - `FbxUtils.reset_takes() -> None` *(static)* — Clear FBX take definitions and restore pre-takes bake-complex state.
  - `FbxUtils.apply_takes(takes: Iterable[Any]) -> int` *(static)* — Configure FBX export to emit one AnimStack (Unity clip) per take.
  - `FbxUtils.apply_takes_from_node(node: Optional[str] = None, attr: Optional[str] = None) -> int` *(static)* — Read take defs from a JSON string channel on *node* and apply them.
  - `FbxUtils.run_export_preparers(include_known: bool = True) -> None` *(static)* — Refresh every producer's ``data_export`` channel once, right now.
  - `FbxUtils.register_export_preparer(name: str, prepare: Callable[[], Any]) -> None` *(static)* — Run *prepare* before every FBX export this session (installs the hook).
  - `FbxUtils.unregister_export_preparer(name: str) -> None` *(static)* — Remove a preparer;
  - `FbxUtils.enable_auto_takes() -> None` *(static)* — Realize declared takes on **every** FBX export — shot-agnostic, no preparer.
  - `FbxUtils.disable_auto_takes() -> None` *(static)* — Clear the explicit enable;
  - `FbxUtils.is_auto_takes_enabled() -> bool` *(static)* — Return whether the auto-takes export hook is currently registered.

<a id="env_utils--handoff_export"></a>
### `env_utils/handoff_export.py`

Maya-side selection + FBX-export hooks shared by the hand-off bridge engines.

- **[`class MayaExportMixin`](mayatk/mayatk/env_utils/handoff_export.py#L32)** — The Maya producer hooks for hand-off bridges (``_resolve_objects`` + ``_produce``).

<a id="env_utils--hierarchy_sync--_hierarchy_sync"></a>
### `env_utils/hierarchy_sync/_hierarchy_sync.py`

- **[`class HierarchyMapBuilder`](mayatk/mayatk/env_utils/hierarchy_sync/_hierarchy_sync.py#L48)** — Builds hierarchy path maps for Maya transforms.
  - `HierarchyMapBuilder.build_path_map(root, exclude_namespace_prefixes: List[str] = None, strip_namespaces: bool = False) -> Dict[str, Any]` *(static)* — Build a mapping of hierarchical paths to transform nodes.
  - `HierarchyMapBuilder.build_path_map_from_nodes(nodes: List[Any], strip_namespaces: bool = False) -> Dict[str, Any]` *(static)* — Build a path map from an arbitrary list of transform node names.
- **[`class MayaObjectMatcher(ptk.LoggingMixin)`](mayatk/mayatk/env_utils/hierarchy_sync/_hierarchy_sync.py#L163)** — Maya-specific object matching with fuzzy logic and container searches.
  - `MayaObjectMatcher.find_matches(self, target_objects: List[str], imported_transforms: List, dry_run: bool = False) -> Tuple[List, Dict]` — Find matching objects using exact and fuzzy matching.
- **[`class HierarchySync(ptk.LoggingMixin, _HierarchySyncInternal)`](mayatk/mayatk/env_utils/hierarchy_sync/_hierarchy_sync.py#L345)** — Core hierarchy analysis and repair manager.
  - `HierarchySync.analyze_hierarchies(self, current_tree_root=None, reference_tree_root=None, reference_objects: List = None, filter_meshes: bool = True, filter_cameras: bool = False, filter_lights: bool = False, inc_names: Optional[List[str]] = None, exc_names: Optional[List[str]] = None, inc_types: Optional[List[str]] = None, exc_types: Optional[List[str]] = None) -> Dict[str, Any]` — Analyze differences between current and reference hierarchies.
  - `HierarchySync.create_stubs(self, paths: Optional[List[str]] = None) -> List[str]` — Create empty transform stubs for missing hierarchy paths.
  - `HierarchySync.quarantine_extras(self, group: str = '_QUARANTINE', paths: Optional[List[str]] = None, skip_animated: bool = True) -> List[str]` — Move extra (scene-only) items to a root-level quarantine group.
  - `HierarchySync.fix_fuzzy_renames(self, items: Optional[List[Dict[str, str]]] = None, skip_animated: bool = True) -> List[str]` — Rename nodes identified as fuzzy matches to their reference names.
  - `HierarchySync.fix_reparented(self, items: Optional[List[Dict[str, str]]] = None, skip_animated: bool = True) -> List[str]` — Move reparented nodes to match their reference hierarchy position.
  - `HierarchySync.get_clean_node_name(node) -> str` *(static)* — Get a consistent clean node name for matching (strips namespace).
  - `HierarchySync.get_clean_node_name_from_string(node_name: str) -> str` *(static)* — Get a clean node name from a string path (removes namespace prefix).
  - `HierarchySync.clean_hierarchy_path(path: str) -> str` *(static)* — Clean namespace prefixes from all components of a hierarchical path.
  - `HierarchySync.format_component(name: str, strip_namespaces: bool = False) -> str` *(static)* — Format a single component name with optional namespace stripping.
  - `HierarchySync.is_default_maya_camera(path: str, node) -> bool` *(static)* — Check if *node* represents a Maya default camera.
  - `HierarchySync.should_keep_node_by_type(node, node_types: List[str], exclude: bool = True) -> bool` *(static)* — Filter nodes by shape types.
  - `HierarchySync.filter_path_map_by_cameras(path_map: Dict[str, Any]) -> Dict[str, Any]` *(static)* — Remove Maya default cameras from *path_map*.
  - `HierarchySync.filter_path_map_by_types(path_map: Dict[str, Any], node_types: List[str], exclude: bool = True) -> Dict[str, Any]` *(static)* — Filter *path_map* by shape node types.
  - `HierarchySync.select_objects_in_maya(object_names: List[str]) -> int` *(static)* — Select objects in Maya scene by name.
- **[`class ObjectSwapper(ptk.LoggingMixin)`](mayatk/mayatk/env_utils/hierarchy_sync/_hierarchy_sync.py#L2259)** — Handles cross-scene object operations like push/pull.
  - `ObjectSwapper.pull_objects_from_scene(self, target_objects: List[str], source_file: Union[str, Path], backup: bool = True) -> bool` — Pull objects from source scene into current scene.

<a id="env_utils--hierarchy_sync--hierarchy_sync_slots"></a>
### `env_utils/hierarchy_sync/hierarchy_sync_slots.py`

- **[`class HierarchySyncController(ptk.LoggingMixin)`](mayatk/mayatk/env_utils/hierarchy_sync/hierarchy_sync_slots.py#L20)** — Controller for hierarchy management operations.
  - `HierarchySyncController.workspace(self) -> Optional[str]` *(property)* — Get the current workspace directory.
  - `HierarchySyncController.reference_path(self) -> str` *(property)* — The current reference scene path.
  - `HierarchySyncController.analyze_hierarchies(self, reference_path: str, fuzzy_matching: bool = True, dry_run: bool = True, filter_meshes: bool = False, filter_cameras: bool = False, filter_lights: bool = False) -> bool` — Analyze hierarchies and perform comparison.
  - `HierarchySyncController.pull_objects(self, object_names: List[str], reference_path: str, fuzzy_matching: bool = True, dry_run: bool = True, pull_children: bool = False, pull_mode: str = 'Add to Scene') -> bool` — Pull objects from reference scene to current scene.
  - `HierarchySyncController.repair_hierarchies(self, create_stubs: bool = True, quarantine_extras: bool = True, quarantine_group: str = '_QUARANTINE', skip_animated: bool = True, fix_reparented: bool = True, fix_fuzzy_renames: bool = True, dry_run: bool = True) -> bool` — Run repair operations on the current scene to match reference hierarchy.
  - `HierarchySyncController.select_objects_in_maya(self, object_names: List[str]) -> int` — Select objects in Maya scene by name.
  - `HierarchySyncController.populate_reference_tree(self, tree_widget, reference_path: str = None)` — Populate the reference tree — handles cache, import, and rendering.
  - `HierarchySyncController.refresh_trees(self, restore_selection: bool = True)` — Refresh both tree widgets with current hierarchy data.
  - `HierarchySyncController.is_path_ignored(self, tree_widget, path)` — Check whether *path* (or any ancestor) is in the ignored set.
  - `HierarchySyncController.clear_ignored_paths(self)` — Clear all ignored paths for both trees.
  - `HierarchySyncController.log_diff_results(self)` — Log detailed hierarchy difference analysis results using rich formatting.
  - `HierarchySyncController.get_recent_reference_scenes(self) -> List[str]` — Get recent reference scenes from settings.
  - `HierarchySyncController.save_recent_reference_scene(self, scene_path: str)` — Save reference scene to recent list.
- **[`class HierarchySyncSlots(ptk.LoggingMixin)`](mayatk/mayatk/env_utils/hierarchy_sync/hierarchy_sync_slots.py#L1158)** — Slots class for hierarchy management UI operations.
  - `HierarchySyncSlots.header_init(self, widget)` — Initialize the header widget.
  - `HierarchySyncSlots.tree000_init(self, widget)` — Initialize the reference/imported hierarchy tree widget.
  - `HierarchySyncSlots.tree001_init(self, widget)` — Initialize the current scene hierarchy tree widget.
  - `HierarchySyncSlots.cmb_diff_options_init(self, widget)` — Populate the diff-options WidgetComboBox below the Diff button.
  - `HierarchySyncSlots.cmb_pull_options_init(self, widget)` — Populate the pull-options WidgetComboBox below the Pull button.
  - `HierarchySyncSlots.tb003_init(self, widget)` — Initialize the fix/repair toggle button with options menu.
  - `HierarchySyncSlots.tb001(self, state=None)` — Run the diff analysis using settings from cmb_diff_options.
  - `HierarchySyncSlots.tb002(self, state=None)` — Toggle button for pull objects with options menu.
  - `HierarchySyncSlots.tb003(self, state=None)` — Toggle button for fix/repair operations.
  - `HierarchySyncSlots.b003(self)` — Browse for reference scene file.
  - `HierarchySyncSlots.b005(self)` — Refresh current scene hierarchy tree.
  - `HierarchySyncSlots.b006(self)` — Select checked objects in Maya scene.
  - `HierarchySyncSlots.b007(self)` — Expand all items in current scene tree.
  - `HierarchySyncSlots.b008(self)` — Collapse all items in current scene tree.
  - `HierarchySyncSlots.b009(self)` — Refresh reference hierarchy tree.
  - `HierarchySyncSlots.b011(self)` — Show differences between hierarchies.
  - `HierarchySyncSlots.b012(self)` — Analyze hierarchies and perform comparison.
  - `HierarchySyncSlots.b013(self)` — Ignore selected items in the reference tree.
  - `HierarchySyncSlots.b014(self)` — Unignore selected items in the reference tree.
  - `HierarchySyncSlots.b015(self)` — Ignore selected items in the current scene tree.
  - `HierarchySyncSlots.b016(self)` — Unignore selected items in the current scene tree.
  - `HierarchySyncSlots.b018(self)` — Delete selected objects from the Maya scene and refresh the tree.
  - `HierarchySyncSlots.b017(self)` — Rename current-scene items to match reference names.
  - `HierarchySyncSlots.count_tree_items(self, tree_widget)` — Count total items in a tree widget.

<a id="env_utils--hierarchy_sync--scene_data_sidecar"></a>
### `env_utils/hierarchy_sync/scene_data_sidecar.py`

Scene-data sidecar manifest management.

- **[`class SceneDataSidecar`](mayatk/mayatk/env_utils/hierarchy_sync/scene_data_sidecar.py#L46)** — Manages scene-data sidecar files stored alongside export files.
  - `SceneDataSidecar.base_stem(cls, export_path: str) -> str` *(class)* — Return the export stem with any trailing ``_vNN`` suffix stripped.
  - `SceneDataSidecar.manifest_path_for(cls, export_path: str, *, base_stem: bool = False) -> str` *(class)* — Return the sidecar manifest path for an export file.
  - `SceneDataSidecar.diff_report_path_for(cls, export_path: str, *, base_stem: bool = False) -> str` *(class)* — Return the sidecar diff report path for an export file.
  - `SceneDataSidecar.find_legacy_manifest(cls, export_path: str) -> Optional[str]` *(class)* — Return the path of a legacy per-version sidecar to migrate from.
  - `SceneDataSidecar.ensure_base_name(cls, export_path: str) -> Optional[str]` *(class)* — Migrate a legacy per-version manifest to the base-stem name.
  - `SceneDataSidecar.migrate_legacy(cls, export_path: str, *, base_stem: bool = False) -> Optional[str]` *(class)* — Idempotently bring on-disk sidecars up to the current naming.
  - `SceneDataSidecar.rename(cls, old_export_path: str, new_export_path: str) -> list` *(class)* — Rename sidecar files to match a renamed export file.
  - `SceneDataSidecar.build_clean_path_set(objects) -> set` *(static)* — Build a set of namespace-stripped hierarchy paths from DAG long paths.
  - `SceneDataSidecar.expand_to_descendants(objects) -> list` *(static)* — Return *objects* plus all their DAG descendants (full paths).
  - `SceneDataSidecar.get_top_level(paths) -> list` *(static)* — Return only paths whose ancestor is *not* also in the set.
  - `SceneDataSidecar.detect_reparenting(missing: list, extra: list) -> list` *(static)* — Detect nodes that were reparented rather than added/removed.
  - `SceneDataSidecar.write_manifest(cls, export_path: str, paths, *, data: Optional[dict] = None, base_stem: bool = False) -> Optional[str]` *(class)* — Write the sidecar manifest for *export_path*.
  - `SceneDataSidecar.read_manifest(cls, export_path: str, *, base_stem: bool = False) -> Optional[Set[str]]` *(class)* — Read the hierarchy paths from the manifest for *export_path*.
  - `SceneDataSidecar.read_data(cls, export_path: str, *, base_stem: bool = False) -> Optional[dict]` *(class)* — Read the ``data_export`` snapshot from the manifest for *export_path*.
  - `SceneDataSidecar.count_descendants(top_path: str, all_paths) -> int` *(static)* — Count *top_path* plus its descendants in *all_paths*.
  - `SceneDataSidecar.write_diff_report(cls, export_path: str, missing: list, extra: list, reparented: list = None, *, base_stem: bool = False) -> Optional[str]` *(class)* — Write a human-readable diff report to the sidecar text file.
  - `SceneDataSidecar.clean_stale_diff(cls, export_path: str, *, base_stem: bool = False) -> None` *(class)* — Remove a stale diff report left over from a previous failure.
  - `SceneDataSidecar.build_full_path_set(cls, objects) -> set` *(class)* — Expand *objects* to descendants, then clean and deduplicate.
  - `SceneDataSidecar.compare(cls, export_path: str, current_paths: set, *, base_stem: bool = False) -> Tuple[bool, list, list]` *(class)* — Compare *current_paths* against the stored hierarchy baseline.

<a id="env_utils--hierarchy_sync--tree_renderer"></a>
### `env_utils/hierarchy_sync/tree_renderer.py`

Tree rendering, formatting, and selection management for the hierarchy sync UI.

- **[`class HierarchyTreeRenderer(ptk.LoggingMixin)`](mayatk/mayatk/env_utils/hierarchy_sync/tree_renderer.py#L26)** — Owns all QTreeWidget population, diff-colour formatting, ignore
  - `HierarchyTreeRenderer.populate_current_scene_tree(self, tree_widget)` — Populate the current scene hierarchy tree.
  - `HierarchyTreeRenderer.populate_reference_tree(self, tree_widget, transforms, reference_name='Reference Scene')` — Populate the reference hierarchy tree with pre-fetched transforms.
  - `HierarchyTreeRenderer.show_reference_placeholder(self, tree_widget, reference_name='Reference Scene')` — Show a 'Browse for Reference Scene' placeholder in an empty tree.
  - `HierarchyTreeRenderer.show_reference_error(self, tree_widget, reference_name='Reference Scene', message='File Not Found')` — Show an error or status message in the reference tree.
  - `HierarchyTreeRenderer.populate_tree_with_hierarchy(self, tree_widget, objects, tree_type='current')` — Populate tree widget with proper Maya-style hierarchy.
  - `HierarchyTreeRenderer.apply_difference_formatting(self, tree001, tree000)` — Apply color formatting to tree widgets based on hierarchy differences.
  - `HierarchyTreeRenderer.clear_tree_colors(self, tree_widget)` — Remove foreground/background colors from every item in a tree widget.
  - `HierarchyTreeRenderer.format_tree_differences(self, tree_widget, tree_type, tree_matcher, by_full, by_clean, by_last)` — Format a specific tree widget based on differences.
  - `HierarchyTreeRenderer.apply_ignore_styling(self, tree_widget)` — Apply strikethrough/dim styling for ignored items — and, when the 'Hide Ignored'
  - `HierarchyTreeRenderer.build_item_path(item)` *(static)* — Build a pipe-separated hierarchy path from a QTreeWidgetItem.
  - `HierarchyTreeRenderer.find_tree_item_by_name(self, tree_widget, object_name)` — Find a tree item by object name (first column).
  - `HierarchyTreeRenderer.get_selected_tree_items(self, tree_widget)` — Get selected items from a tree widget.
  - `HierarchyTreeRenderer.get_selected_object_names(self, tree_widget)` — Extract object names from selected tree widget items.

<a id="env_utils--hierarchy_sync--tree_utils"></a>
### `env_utils/hierarchy_sync/tree_utils.py`

Tree widget utilities for hierarchy sync UI operations.

- **[`class TreePathMatcher(ptk.LoggingMixin, _TreePathMatcherInternal)`](mayatk/mayatk/env_utils/hierarchy_sync/tree_utils.py#L47)** — Tree path matching functionality for UI tree widgets.
  - `TreePathMatcher.build_tree_index(self, widget)` — Build tree indices for fast item lookup.
  - `TreePathMatcher.find_path_matches(self, target_path: str, by_full: dict, by_clean_full: dict, by_last: dict, prefer_cleaned: bool = False, strict: bool = False)` — Find tree items matching a target path using multiple strategies.
  - `TreePathMatcher.log_matching_debug(self, path, candidates, strategy, prefix='')` — Log debug information about path matching.
  - `TreePathMatcher.log_tree_index_debug(self, by_full, by_clean_full, by_last, tree_type)` — Log debug information about tree indices.
  - `TreePathMatcher.get_selected_object_names(tree_widget) -> List[str]` *(static)* — Extract object names from selected tree widget items.
  - `TreePathMatcher.get_selected_tree_items(tree_widget) -> list` *(static)* — Get all selected items from tree widget.
  - `TreePathMatcher.find_tree_item_by_name(tree_widget, object_name: str)` *(static)* — Find tree widget item by object name.
  - `TreePathMatcher.build_hierarchy_structure(objects: list) -> Tuple[Dict[str, Dict], List[str]]` *(static)* — Build hierarchical structure from Maya transform objects.

<a id="env_utils--maya_connection"></a>
### `env_utils/maya_connection.py`

Maya Connection Module

- **[`class MayaConnection`](mayatk/mayatk/env_utils/maya_connection.py#L28)** — Manages connection to Maya for testing purposes.
  - `MayaConnection.get_instance() -> 'MayaConnection'` *(static)* — Get the global Maya connection instance.
  - `MayaConnection.open_command_ports(**kwargs)` *(static)* — Open command ports for external script editor.
  - `MayaConnection.close_command_ports(ports=None)` *(static)* — Close the specified Maya command ports.
  - `MayaConnection.open_available_command_ports(mel_start: int = 7001, python_start: int = 7002, max_offset: int = 50, tag_window: bool = True) -> dict` *(static)* — Open command ports auto-negotiating around port collisions.
  - `MayaConnection.toggle_command_ports(mel_port: int = 7001, python_port: int = 7002) -> tuple` *(static)* — Toggle Maya command ports on or off.
  - `MayaConnection.reload_modules(modules: Union[str, List[str]], include_submodules: bool = True, verbose: bool = True) -> List[str]` *(static)* — Reload specified modules and their submodules using pythontk.ModuleReloader.
  - `MayaConnection.connect(self, mode: ConnectionMode = 'auto', port: int = 7002, host: str = 'localhost', launch: bool = True, app_path: Optional[str] = None, force_new_instance: bool = True, launch_args: Optional[List[str]] = None, confirm_existing: bool = True, auto_cleanup: bool = False) -> bool` — Connect to Maya using the specified mode.
  - `MayaConnection.get_pid_from_port(cls, port: int) -> Optional[int]` *(class)* — Find the process ID (PID) listening on the given TCP port.
  - `MayaConnection.get_port_from_pid(cls, pid: int, start_port: Optional[int] = None, span: Optional[int] = None) -> Optional[int]` *(class)* — Find a TCP port the given PID is LISTENING on (inverse of
  - `MayaConnection.close_instance(port: Optional[int] = None, pid: Optional[int] = None, force: bool = False) -> bool` *(static)* — Close a Maya instance identified by Port or PID.
  - `MayaConnection.get_available_port(cls, start_port: int = 7002, max_check: int = 100) -> int` *(class)* — Find an available port starting from start_port.
  - `MayaConnection.ensure_connection(self, launch: bool = True, app_path: Optional[str] = None, launch_args: Optional[List[str]] = None) -> bool` — Verify the port is reachable;
  - `MayaConnection.execute(self, code: str, timeout: int = 30, capture_output: bool = False, wait_for_response: bool = False, output_callback: Optional[Callable[[str], None]] = None) -> Optional[str]` — Execute Python code in Maya.
  - `MayaConnection.get_script_editor_output(self, last_n_chars: Optional[int] = None) -> Optional[str]` — Get the full content of the Maya Script Editor history.
  - `MayaConnection.execute_and_capture_editor_output(self, code: str, timeout: int = 30, mirror_to_script_output: bool = False) -> tuple[Optional[str], Optional[str]]` — Execute code and capture the Script Editor output generated by the execution.
  - `MayaConnection.clear_script_editor(self) -> bool` — Clear the Maya Script Editor history.
  - `MayaConnection.shutdown(self, force: bool = False) -> None` — Shut down the connected Maya session and reset state.
  - `MayaConnection.disconnect(self)` — Disconnect from Maya.

<a id="env_utils--namespace_sandbox"></a>
### `env_utils/namespace_sandbox.py`

- **[`class FBXImporter`](mayatk/mayatk/env_utils/namespace_sandbox.py#L11)** — Handles FBX-specific import operations (.fbx files).
  - `FBXImporter.is_supported_file(self, file_path: Union[str, Path]) -> bool` — Check if the file is an FBX file.
  - `FBXImporter.import_with_namespace(self, source_file: Path, namespace: str, temp_namespace_prefix: str, force_complete_import: bool = False) -> Optional[Dict]` — Import an FBX file isolated into *namespace*.
  - `FBXImporter.import_for_analysis(self, source_file: Path, namespace: str) -> Optional[List[Any]]` — Import FBX file into a fresh namespace for analysis.
- **[`class MayaImporter`](mayatk/mayatk/env_utils/namespace_sandbox.py#L104)** — Handles Maya-specific import operations (.ma/.mb files).
  - `MayaImporter.is_supported_file(self, file_path: Union[str, Path]) -> bool` — Check if the file is a Maya file (.ma or .mb).
  - `MayaImporter.import_with_namespace(self, source_file: Path, namespace: str, temp_namespace_prefix: str, force_complete_import: bool = False) -> Optional[Dict]` — Import Maya file with namespace - original logic.
  - `MayaImporter.import_for_analysis(self, source_file: Path, namespace: str) -> Optional[List[Any]]` — Import Maya file for analysis purposes.
- **[`class CameraTracker(ptk.LoggingMixin)`](mayatk/mayatk/env_utils/namespace_sandbox.py#L287)** — Tracks cameras before and after import operations for proper cleanup.
  - `CameraTracker.capture_pre_import_state(self)` — Capture camera state before import.
  - `CameraTracker.capture_post_import_state(self)` — Capture camera state after import.
  - `CameraTracker.get_imported_cameras(self, namespace_filter=None)` — Get cameras that were imported (optionally filtered by namespace).
  - `CameraTracker.cleanup_imported_cameras(self, namespace_filter=None, preserve_user_cameras=True)` — Clean up imported cameras with optional preservation of user cameras.
  - `CameraTracker.reset(self)` — Reset tracking state.
- **[`class NamespaceSandbox(ptk.LoggingMixin, _NamespaceSandboxInternal)`](mayatk/mayatk/env_utils/namespace_sandbox.py#L449)** — Handles temporary importing and namespace management for Maya scenes.
  - `NamespaceSandbox.import_with_namespace(self, source_file: Union[str, Path], namespace_prefix: str = None, force_complete_import: bool = False) -> Optional[Dict]` — Import file and return import information.
  - `NamespaceSandbox.import_for_analysis(self, source_file: Union[str, Path], namespace: str = None) -> Optional[List[Any]]` — Import file into temporary namespace for analysis (dry-run mode).
  - `NamespaceSandbox.get_supported_formats(self) -> List[str]` — Get list of supported file formats from all importers.
  - `NamespaceSandbox.find_objects_in_namespace(self, namespace: str, target_objects: List[str]) -> List[Any]` — Find objects in the specified namespace with optional fuzzy matching.
  - `NamespaceSandbox.find_objects_with_hierarchy_matching(self, namespace: str, target_objects: List[str]) -> List[Any]` — Find objects using hierarchical path matching (only if fuzzy_matching enabled).
  - `NamespaceSandbox.get_namespace_hierarchy(self, namespace: str) -> Dict[str, Any]` — Get complete hierarchy information for objects in namespace.
  - `NamespaceSandbox.cleanup_import(self, namespace: str, imported_objects: List[Any] = None) -> bool` — Safely remove imported objects and cleanup namespace tracking.
  - `NamespaceSandbox.cleanup_namespace(self, namespace: str) -> bool` — Backward compatibility alias for cleanup_import.
  - `NamespaceSandbox.cleanup_all_namespaces(self) -> None` — Clean up all temp imports managed by this instance.
  - `NamespaceSandbox.get_imported_cameras(self, namespace_filter=None)` — Get cameras that were imported during the last import operation.
  - `NamespaceSandbox.cleanup_imported_cameras(self, namespace_filter=None, preserve_user_cameras=True)` — Clean up imported cameras for a specific namespace.
  - `NamespaceSandbox.cleanup_all_temp_namespaces_force(self) -> None` — Force cleanup of ALL temp namespaces in Maya, not just tracked ones.
  - `NamespaceSandbox.export_objects_to_temp(self, target_objects: List[str]) -> Optional[Path]` — Export objects to temporary file using cmds.ls() for robust object handling.
  - `NamespaceSandbox.import_objects_for_swapping(self, source_file: Union[str, Path]) -> Optional[Dict]` — Import objects from source scene for object swapping operations.
  - `NamespaceSandbox.import_to_target_scene(self, temp_file: Union[str, Path], target_scene: Union[str, Path], backup: bool = True) -> bool` — Import objects into target scene.
  - `NamespaceSandbox.cleanup_analysis_namespace(self, namespace: str = None) -> bool` — Clean up analysis namespace and its contents.

<a id="env_utils--reference_manager"></a>
### `env_utils/reference_manager.py`

- **[`class AssemblyManager`](mayatk/mayatk/env_utils/reference_manager.py#L56)**
  - `AssemblyManager.current_references(cls)` *(class)* — Get the current scene references.
  - `AssemblyManager.create_assembly_definition(cls, namespace: str, file_path: str) -> str` *(class)* — Create an assembly definition for the given file path.
  - `AssemblyManager.set_active_representation(cls, assembly_node: str, representation_name: str) -> bool` *(class)* — Set the active representation for an assembly.
  - `AssemblyManager.convert_references_to_assemblies(cls)` *(class)* — Convert all current references to assembly definitions and references.
- **[`class ReferenceManager(WorkspaceManager, ptk.HelpMixin, ptk.LoggingMixin, _ReferenceManagerInternal)`](mayatk/mayatk/env_utils/reference_manager.py#L168)** — Core Maya scene reference management functionality.
  - `ReferenceManager.current_references(self)` *(property)* — Get the current scene references.
  - `ReferenceManager.sanitize_namespace(namespace: str) -> str` *(static)* — Sanitize the namespace by replacing or removing illegal characters.
  - `ReferenceManager.add_reference(self, namespace: str, file_path: str) -> bool`
  - `ReferenceManager.import_references(self, namespaces=None, remove_namespace=True)` — Import referenced objects into the scene.
  - `ReferenceManager.update_references(self)` — Update all references to reflect the latest changes from the original files.
  - `ReferenceManager.get_reference_top_transforms(self, ref)` — Return top-level (parent-less) transforms belonging to the given reference.
  - `ReferenceManager.get_reference_display_mode(self, ref) -> str` — Return the active display mode for the reference's top-level transforms.
  - `ReferenceManager.set_reference_display_mode(self, ref, mode: str) -> bool` — Set the display override mode on the reference's top-level transforms.
  - `ReferenceManager.remove_references(self, namespaces=None)` — Remove references based on their namespaces.
- **[`class ReferenceManagerController(ReferenceManager, ptk.LoggingMixin)`](mayatk/mayatk/env_utils/reference_manager.py#L560)** — Controller that bridges Maya reference functionality with UI interactions.
  - `ReferenceManagerController.current_working_dir(self)` *(property)*
  - `ReferenceManagerController.block_table_selection_method(method)`
  - `ReferenceManagerController.prepare_item_for_edit(self, item)` — Prepare an item for editing by showing the full filename.
  - `ReferenceManagerController.restore_item_display(self, item)` — Restore the item to its display name after editing.
  - `ReferenceManagerController.is_item_being_edited(self, item)` — Check if an item is currently being edited.
  - `ReferenceManagerController.handle_item_selection(self)`
  - `ReferenceManagerController.sync_selection_to_references(self)` — Sync the table selection to match current scene references.
  - `ReferenceManagerController.update_current_dir(self, text: Optional[str] = None)`
  - `ReferenceManagerController.set_workspace(self, workspace_path: str, invalidate: bool = True) -> bool` — Set the current workspace for browsing and refresh the file list.
  - `ReferenceManagerController.refresh_file_list(self, invalidate=False)` — Refresh the file list for the table widget.
  - `ReferenceManagerController.update_table(self, file_names, file_list)`
  - `ReferenceManagerController.open_scene(self, file_path: str, set_workspace: bool = True)` — Open a scene file, optionally setting the workspace to match the file.
  - `ReferenceManagerController.new_scene(self)` — Discard the current file and start an empty scene (Maya's ``file -new``).
  - `ReferenceManagerController.unreference_all(self)`
  - `ReferenceManagerController.unlink_all(self)`
  - `ReferenceManagerController.unlink_references(self, namespaces)` — Unlink specific references.
  - `ReferenceManagerController.convert_to_assembly(self)`
  - `ReferenceManagerController.save_scene(self)` — Save the current scene to the workspace, prompting for a name.
  - `ReferenceManagerController.rename_scene(self)` — Rename the scene file at the right-clicked row.
  - `ReferenceManagerController.delete_scene(self)` — Delete the scene file at the right-clicked row.
- **[`class ReferenceManagerSlots(ptk.HelpMixin, ptk.LoggingMixin)`](mayatk/mayatk/env_utils/reference_manager.py#L2264)** — UI event handlers and widget initialization for the Reference Manager interface.
  - `ReferenceManagerSlots.header_init(self, widget)` — Initialize the header for the reference manager.
  - `ReferenceManagerSlots.tbl000_init(self, widget)` — Table setup: (re)wire signals every show, one-time context-menu build, then populate.
  - `ReferenceManagerSlots.tbl000_item_double_clicked(self, item)` — Handle double-click to prepare item for editing.
  - `ReferenceManagerSlots.tbl000_item_changed(self, item)` — Handle item changes when user renames a file via inline edit.
  - `ReferenceManagerSlots.tbl000_editor_closed(self, editor, hint)` — Handle when the rename editor is closed.
  - `ReferenceManagerSlots.btn_open_file_location(self)` — Open the containing folder of the right-clicked scene file in the file explorer.
  - `ReferenceManagerSlots.txt000_init(self, widget)` — Initialize the text input for the current working directory with pin values.
  - `ReferenceManagerSlots.txt001_init(self, widget)` — Initialize the filter text input with filtering options.
  - `ReferenceManagerSlots.txt001(self, text)` — Handle the filter text input.
  - `ReferenceManagerSlots.cmb000_init(self, widget)`
  - `ReferenceManagerSlots.cmb000(self, index, widget)` — Handle workspace selection changes.
  - `ReferenceManagerSlots.chk000(self, checked)` — Handle the recursive search toggle.
  - `ReferenceManagerSlots.chk003(self, checked)` — Handle the ignore empty workspaces toggle.
  - `ReferenceManagerSlots.chk_ignore_case(self, checked)` — Handle the ignore case checkbox.
  - `ReferenceManagerSlots.chk_filter_suffix(self, checked)` — Handle the filter by suffix checkbox.
  - `ReferenceManagerSlots.chk_hide_suffix(self, checked)` — Handle the hide suffix checkbox.
  - `ReferenceManagerSlots.chk_hide_extension(self, checked)` — Handle the hide extension checkbox.
  - `ReferenceManagerSlots.chk_show_notes_column(self, checked)` — Toggle visibility of the Notes (metadata) column.
  - `ReferenceManagerSlots.txt_suffix(self, text)` — Handle suffix text changes.
  - `ReferenceManagerSlots.chk_filter_folder_structure(self, checked)` — Handle the filter by folder structure checkbox.
  - `ReferenceManagerSlots.b000(self)` — Browse for a root directory.
  - `ReferenceManagerSlots.b006(self)` — Open the current directory in the file explorer.
  - `ReferenceManagerSlots.b001(self)` — Set dir to current workspace.
  - `ReferenceManagerSlots.btn_open_scene(self)` — Open the scene file at the right-clicked row.
  - `ReferenceManagerSlots.btn_toggle_reference(self)` — Toggle reference state for the right-clicked row.
  - `ReferenceManagerSlots.btn_unlink_import(self)` — Unlink and import at the right-clicked row — covers both cases (mirror of Blender).
  - `ReferenceManagerSlots.btn_save_scene(self)` — Save the current scene to the workspace.
  - `ReferenceManagerSlots.btn_refresh(self)` — Refresh the file list.
  - `ReferenceManagerSlots.btn_convert_assembly(self)` — Convert all references to assemblies.
  - `ReferenceManagerSlots.btn_unlink_import_all(self)` — Unlink and import all references.
  - `ReferenceManagerSlots.btn_unreference_all(self)` — Remove all references from the scene.

<a id="env_utils--scene_exporter--_scene_exporter"></a>
### `env_utils/scene_exporter/_scene_exporter.py`

- **[`class SceneExporter(ptk.LoggingMixin)`](mayatk/mayatk/env_utils/scene_exporter/_scene_exporter.py#L27)**
  - `SceneExporter.perform_export(self, export_dir: str, objects: Optional[Union[List[str], Callable]] = None, preset_file: Optional[str] = None, output_name: Optional[str] = None, export_visible: bool = True, file_format: Optional[str] = 'FBX export', create_log_file: bool = False, timestamp: bool = False, name_regex: Optional[str] = None, log_level: str = 'WARNING', hide_log_file: Optional[bool] = None, log_handler: Optional[object] = None, tasks: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, bool]]` — Perform the export operation, including initialization and task management.
  - `SceneExporter.generate_export_path(self, version_format: str = '') -> str` — Generate the full export file path.
  - `SceneExporter.format_export_name(self, name: str) -> str` — Format the export name using a regex pattern and replacement (e.g.
  - `SceneExporter.generate_log_file_path(self, export_path: str) -> str` — Generate the log file path based on the export path.
  - `SceneExporter.setup_file_logging(self, log_file_path: str)` — Setup file logging to log actions during export.
  - `SceneExporter.close_file_handlers(self)` — Close and remove file handlers after logging is complete.
  - `SceneExporter.load_fbx_export_preset(self, preset_file: str = None, verify: bool = False) -> Optional[dict]` — Load an FBX export preset and optionally verify it.
  - `SceneExporter.verify_fbx_preset(self) -> dict` — Verify a set of predefined FBX export settings and log their values.
- **[`class SceneExporterSlots(SceneExporter)`](mayatk/mayatk/env_utils/scene_exporter/_scene_exporter.py#L690)**
  - `SceneExporterSlots.workspace(self) -> Optional[str]` *(property)*
  - `SceneExporterSlots.presets(self) -> Dict[str, Optional[str]]` *(property)* — Return available presets ({name: filepath}, plus a leading "None" entry).
  - `SceneExporterSlots.header_init(self, widget)` — Initialize the header widget.
  - `SceneExporterSlots.cmb000_init(self, widget) -> None` — Init Preset
  - `SceneExporterSlots.txt000_init(self, widget) -> None` — Init Output Directory
  - `SceneExporterSlots.txt001_init(self, widget) -> None` — Init Output Name
  - `SceneExporterSlots.cmb001_init(self, widget) -> None` — Auto-generate Export Settings UI from task definitions using WidgetComboBox.
  - `SceneExporterSlots.cmb002_init(self, widget) -> None` — Auto-generate Check Settings UI from check definitions using WidgetComboBox.
  - `SceneExporterSlots.cmb004_init(self, widget) -> None` — Init Output Format — FBX (default), GLB, or FBX + GLB.
  - `SceneExporterSlots.b000(self) -> None` — Export: run the scene export with the configured tasks and settings.
  - `SceneExporterSlots.b010(self) -> None` — Set Output Directory
  - `SceneExporterSlots.b005(self) -> None` — Set Preset Directory.
  - `SceneExporterSlots.b006(self) -> None` — Open Output Directory
  - `SceneExporterSlots.b007(self) -> None` — Open Preset Directory.
  - `SceneExporterSlots.b008(self) -> None` — Edit Preset
  - `SceneExporterSlots.save_output_dir(self, output_dir: str) -> None` — Record the output directory into the recent values plugin.
  - `SceneExporterSlots.save_output_name(self, output_name: str) -> None` — Record the output filename into the recent values plugin.

<a id="env_utils--scene_exporter--task_manager"></a>
### `env_utils/scene_exporter/task_manager.py`

- **[`class TaskManager(TaskFactory, _TaskActionsMixin, _TaskChecksMixin)`](mayatk/mayatk/env_utils/scene_exporter/task_manager.py#L1875)** — Contains all task-related UI definitions for the Scene Exporter.
  - `TaskManager.objects(self)` *(property)*
  - `TaskManager.task_definitions(self) -> Dict[str, Dict[str, Any]]` *(property)* — Return the task definitions for the UI.
  - `TaskManager.check_definitions(self) -> Dict[str, Dict[str, Any]]` *(property)* — Return the check definitions for the UI.
  - `TaskManager.definitions(self) -> Dict[str, Dict[str, Any]]` *(property)* — Return all definitions combined for backward compatibility.
  - `TaskManager.set_workspace(self, enable=True)` — Switch to the workspace matching the scene path, and align the
  - `TaskManager.set_linear_unit(self, linear_unit)` — Set Maya's working linear unit for the export.
  - `TaskManager.conform_shape_names(self)` — Repair scratch/mangled names in the export set, then conform shapes.
  - `TaskManager.convert_to_relative_paths(self)` — Copy external textures into sourceimages, then convert paths to relative.
  - `TaskManager.reassign_duplicate_materials(self)` — Reassign duplicate materials in the scene.
  - `TaskManager.resolve_invalid_texture_paths(self)` — Attempt to resolve missing texture paths via a gated sourceimages hunt.
  - `TaskManager.smart_bake(self)` — Pre-bake constrained and driven channels before export.
  - `TaskManager.optimize_keys(self)` — Optimize baked animation keys.
  - `TaskManager.set_bake_animation_range(self)` — Set the animation export range to the first and last keyframes of the specified objects if baking i…
  - `TaskManager.tie_all_keyframes(self)` — Use AnimUtils to tie all keyframes for the specified objects.
  - `TaskManager.snap_keys_to_frame(self)` — Snap all keyframes to the nearest whole frame.
  - `TaskManager.create_glb(self, fbx_path: Optional[str] = None, announce: bool = True)` — Convert an exported FBX to a GLB via pythontk's MeshConvert.
  - `TaskManager.export_data_node(self)` — Include the shared ``data_export`` carrier in the export (default on).
  - `TaskManager.apply_declared_takes(self)` — Export each declared take as a named Unity clip.
  - `TaskManager.check_geometry_lod_suffix(self) -> tuple` — Check for geometry whose names end with '_LOD' or '_LOD' followed by digits.
  - `TaskManager.ignore_groups(self, names: str) -> None` — Exclude top-level groups matching *names* (case-insensitive) and all
  - `TaskManager.exclude_hdr(self) -> None` — Remove Arnold HDR environment lights (``aiSkyDomeLight``) from the export set.
  - `TaskManager.check_root_default_transforms(self) -> tuple` — Check if all root group nodes have default transforms.
  - `TaskManager.check_absolute_paths(self) -> tuple` — Check for stored-absolute (or project-escaping) texture paths.
  - `TaskManager.check_valid_paths(self) -> tuple` — Check that every export texture and scene reference resolves on disk
  - `TaskManager.check_texture_file_size(self, max_size_mb: Optional[float] = 16.0) -> tuple` — Check that no export texture exceeds a maximum on-disk file size.
  - `TaskManager.check_mangled_names(self) -> tuple` — Check the export set (including shapes) for scratch/mangled names.
  - `TaskManager.check_duplicate_locator_names(self) -> tuple` — Check for duplicate locator short names among the specified objects.
  - `TaskManager.check_duplicate_materials(self) -> tuple` — Check if any duplicate materials are present in the scene.
  - `TaskManager.check_referenced_objects(self) -> tuple` — Check if any referenced objects are present in the scene.
  - `TaskManager.check_framerate(self, target_framerate: Optional[str]) -> tuple` — Check if the scene's current framerate matches the target framerate.
  - `TaskManager.check_objects_below_floor(self, tolerance: float = _DEFAULT_FLOOR_TOLERANCE) -> tuple` — Check if any object's geometry is below the floor plane (Y=0).
  - `TaskManager.check_overlapping_duplicate_mesh(self) -> tuple` — Check for duplicate overlapping geometry among the export objects.
  - `TaskManager.check_hidden_geometry(self) -> tuple` — Check for geometry that will ship in the FBX while hidden.
  - `TaskManager.check_untied_keyframes(self) -> tuple` — Check if there are any untied keyframes on the specified objects.
  - `TaskManager.check_floating_point_keys(self) -> tuple` — Check if there are any floating point keyframes on the specified objects.
  - `TaskManager.write_scene_data_sidecar(self) -> None` — Write the sidecar JSON recording what shipped in the export.
  - `TaskManager.check_hierarchy_vs_existing_fbx(self) -> tuple` — Check export objects against the hierarchy manifest of the previous export.

<a id="env_utils--scene_state"></a>
### `env_utils/scene_state.py`

Read named sections of live-scene state for transport.

- **[`class SceneState`](mayatk/mayatk/env_utils/scene_state.py#L35)** — Section-registry reader of scene state the FBX cannot express.
  - `SceneState.source() -> Dict[str, str]` *(static)* — This host's identity for the envelope's ``source`` key.
  - `SceneState.read(cls, objects: List[str], include_textures: bool = True, sections: Optional[List[str]] = None) -> Dict[str, Any]` *(class)* — Scene state the FBX cannot express, one key per requested section.

<a id="env_utils--script_output"></a>
### `env_utils/script_output.py`

- **[`class ScriptConsole(MayaQWidgetDockableMixin, QtWidgets.QDialog)`](mayatk/mayatk/env_utils/script_output.py#L14)** — Dockable window that live-mirrors Maya's Script Editor output,
  - `ScriptConsole.toggle(cls, *args, **kwargs)` *(class)* — Toggle the Script Output panel.
  - `ScriptConsole.show_console(cls, dock=None, width: int = None, height: int = None, tab_position: str = None, restore: bool = False)` *(class)* — Show the Script Output console.

<a id="env_utils--unity_bridge--_unity_bridge"></a>
### `env_utils/unity_bridge/_unity_bridge.py`

Unity bridge engine -- export the Maya selection into a Unity project's Assets/.

- **[`class UnityBridge(MayaExportMixin, ptk.HandoffBridge)`](mayatk/mayatk/env_utils/unity_bridge/_unity_bridge.py#L37)** — Export the Maya selection and copy it into a Unity project's ``Assets/``.
  - `UnityBridge.list_template_modes(self)`
  - `UnityBridge.params_defaults(self)`
  - `UnityBridge.list_delivery_modes(cls) -> List[Tuple[str, str]]` *(class)* — ``[(mode_stem, ""), ...]`` for the panel's delivery combo.

<a id="env_utils--unity_bridge--parameters"></a>
### `env_utils/unity_bridge/parameters.py`

User-tunable parameters for the Maya->Unity bridge panel.

- **[`class Parameters`](mayatk/mayatk/env_utils/unity_bridge/parameters.py#L167)** — Parameters — module namespace.
  - `Parameters.referenced_keys(script_text: str) -> 'set[str]'` *(static)* — Registered keys present in *script_text* (delegates to uitk.bridge).
  - `Parameters.defaults() -> 'dict[str, Any]'` *(static)* — Return ``{key: default}`` for every registered parameter.
  - `Parameters.render_context(values: 'dict[str, Any]') -> 'dict[str, str]'` *(static)* — Format *values* for substitution (kept for API parity;

<a id="env_utils--unity_bridge--unity_bridge_slots"></a>
### `env_utils/unity_bridge/unity_bridge_slots.py`

Slots for the Unity bridge panel.

- **[`class UnityBridgeSlots(MayaBridgeSlotsBase)`](mayatk/mayatk/env_utils/unity_bridge/unity_bridge_slots.py#L46)** — Slots wired to ``unity_bridge.ui`` via :class:`MayaBridgeSlotsBase`.
  - `UnityBridgeSlots.params_module(self)` *(property)*
  - `UnityBridgeSlots.template_dir(self) -> Path` *(property)*
  - `UnityBridgeSlots.make_bridge(self)` — Build the engine, or ``None`` when the optional unitytk is absent.
  - `UnityBridgeSlots.list_template_modes(self)`
  - `UnityBridgeSlots.default_output_dir(self) -> str`
  - `UnityBridgeSlots.b000(self)` — Run the selected template: export-and-copy, or script management.

<a id="env_utils--usd"></a>
### `env_utils/usd.py`

USD import / export over Maya's native ``mayaUsd`` runtime.

- **[`class UsdUtils(ptk.HelpMixin)`](mayatk/mayatk/env_utils/usd.py#L36)** — Low-level USD import/export utilities over the ``mayaUsd`` plugin.
  - `UsdUtils.load_plugin()` *(static)* — Ensure the ``mayaUsdPlugin`` plugin is loaded.
  - `UsdUtils.is_usd_file(file_path: str) -> bool` *(static)* — True when *file_path* is a USD layer/package (delegates to pythontk).
  - `UsdUtils.export(cls, file_path: str, objects: Optional[List] = None, options: Optional[Dict[str, Any]] = None, selection_only: bool = True) -> str` *(class)* — Export to a USD file (``.usd``/``.usda``/``.usdc``/``.usdz``).
  - `UsdUtils.import_scene(cls, file_path: str, namespace: Optional[str] = None, options: Optional[Dict[str, Any]] = None, return_new_nodes: bool = True) -> List[str]` *(class)* — Import a USD file, optionally isolated into a namespace.

<a id="env_utils--webxr_preview"></a>
### `env_utils/webxr_preview.py`

Push the Maya selection to a live browser / WebXR preview.

- **[`class WebXrPreview(MayaExportMixin, ptk.PreviewBridge)`](mayatk/mayatk/env_utils/webxr_preview.py#L36)** — Live browser / WebXR preview of the Maya selection.

<a id="env_utils--workspace_manager"></a>
### `env_utils/workspace_manager.py`

- **[`class WorkspaceManager(ptk.HelpMixin, ptk.LoggingMixin)`](mayatk/mayatk/env_utils/workspace_manager.py#L13)** — Shared workspace management utilities for UI components.
  - `WorkspaceManager.current_workspace(self)` *(property)* — Get the current Maya workspace with fallback handling.
  - `WorkspaceManager.current_working_dir(self)` *(property)* — Get the current working directory.
  - `WorkspaceManager.recursive_search(self)` *(property)* — Whether to search recursively for files.
  - `WorkspaceManager.ignore_empty_workspaces(self)` *(property)* — Whether to ignore empty workspaces when searching.
  - `WorkspaceManager.workspace_files(self) -> dict[str, list[str]]` *(property)* — Get cached workspace file dictionary, rebuilding if needed.
  - `WorkspaceManager.find_available_workspaces(self, root_dir: str = None) -> list` — Find all available workspaces under the given root directory.
  - `WorkspaceManager.invalidate_workspace_files(self)` — Scan for workspaces and rebuild the file cache.
  - `WorkspaceManager.resolve_file_path(self, selected_file: str) -> Optional[str]` — Resolve a file name to its full path by searching in workspace files.

<a id="env_utils--workspace_map"></a>
### `env_utils/workspace_map.py`

- **[`class WorkspaceMap(WorkspaceManager, ptk.HelpMixin, ptk.LoggingMixin)`](mayatk/mayatk/env_utils/workspace_map.py#L12)** — Maps and displays Maya workspaces in a tree structure.
  - `WorkspaceMap.current_working_dir(self)` *(property)* — Get the current working directory for workspace discovery.
  - `WorkspaceMap.recursive_search(self)` *(property)* — Whether to search recursively for workspaces.
  - `WorkspaceMap.workspace_data(self) -> Dict[str, Dict]` *(property)* — Get cached workspace data, rebuilding if needed.
  - `WorkspaceMap.invalidate_workspace_data(self)` — Scan for workspaces and build data cache.
  - `WorkspaceMap.get_workspace_tree_data(self, filter_text: str = None) -> Dict` — Get workspace data organized for tree display.
  - `WorkspaceMap.get_filtered_workspaces(self, filter_text: str = None) -> List[Dict]` — Get a filtered list of workspaces.
  - `WorkspaceMap.create_project(self, name: str) -> Optional[ptk.Workspace]` — Create a project named *name* under the current root, built from the
  - `WorkspaceMap.mark_root_as_project(self) -> Optional[ptk.Workspace]` — Mark the current ROOT directory as a shared Maya/Blender project —
- **[`class WorkspaceMapController(WorkspaceMap, ptk.LoggingMixin)`](mayatk/mayatk/env_utils/workspace_map.py#L241)** — Controller for the WorkspaceMap UI components.
  - `WorkspaceMapController.update_current_dir(self, text: Optional[str] = None)` — Update the current working directory from UI input.
  - `WorkspaceMapController.refresh_tree(self, invalidate: bool = False)` — Refresh the workspace tree.
  - `WorkspaceMapController.selected_workspace(self) -> Optional[Dict]` — The workspace record under the tree cursor, or None (a directory
  - `WorkspaceMapController.open_selected_workspace(self) -> Optional[str]` — Set Maya's project to the selected workspace.
- **[`class WorkspaceMapSlots(ptk.HelpMixin, ptk.LoggingMixin)`](mayatk/mayatk/env_utils/workspace_map.py#L369)** — UI slots for the WorkspaceMap interface.
  - `WorkspaceMapSlots.header_init(self, widget)` — Project creation actions + header help text.
  - `WorkspaceMapSlots.txt000_init(self, widget)` — Initialize the directory input widget.
  - `WorkspaceMapSlots.txt001_init(self, widget)` — Initialize the filter input widget.
  - `WorkspaceMapSlots.tree000_init(self, widget)` — Initialize the workspace tree widget.
  - `WorkspaceMapSlots.filter_workspaces(self, text)` — Handle filter text changes.
  - `WorkspaceMapSlots.chk000(self, checked)` — Handle recursive search toggle.
  - `WorkspaceMapSlots.browse_directory(self)` — Browse for a root directory.
  - `WorkspaceMapSlots.set_to_workspace(self)` — Set directory to current Maya workspace.
  - `WorkspaceMapSlots.btn_open_workspace(self)` — Open selected workspace in Maya.
  - `WorkspaceMapSlots.btn_explore_folder(self)` — Open selected workspace folder in file explorer.
  - `WorkspaceMapSlots.new_project(self)` — Create a project under the root directory from the ACTIVE template.
  - `WorkspaceMapSlots.mark_root(self)` — Promote the ROOT directory to a shared Maya/Blender project.
  - `WorkspaceMapSlots.save_template(self)` — Publish the ACTIVE project's file rules as a named workspace template.

<a id="light_utils--_light_utils"></a>
### `light_utils/_light_utils.py`

- **[`class LightUtils(ptk.HelpMixin)`](mayatk/mayatk/light_utils/_light_utils.py#L12)**

<a id="light_utils--hdr_manager"></a>
### `light_utils/hdr_manager.py`

Arnold HDR environment manager.

- **[`class HdrManager(ptk.LoggingMixin, ptk.HelpMixin)`](mayatk/mayatk/light_utils/hdr_manager.py#L57)** — Manage a single ``aiSkyDomeLight`` + connected ``file`` texture.
  - `HdrManager.arnold_loaded() -> bool` *(static)* — True if ``mtoa`` is *already* loaded — cheap, side-effect-free query.
  - `HdrManager.arnold_available() -> bool` *(static)* — True if the ``mtoa`` plugin can be loaded right now.
  - `HdrManager.ensure_plugin_loaded(cls) -> bool` *(class)* — Backward-compat alias for :meth:`arnold_available`.
  - `HdrManager.hdr_env(self) -> Optional[str]` *(property)* — The active skydome shape node, or ``None`` if none exists.
  - `HdrManager.hdr_env_transform(self) -> Optional[str]` *(property)* — Transform parent of the skydome shape, or ``None``.
  - `HdrManager.hdr_file_node(self) -> Optional[str]` *(property)* — The ``file`` node currently driving ``color`` on the skydome.
  - `HdrManager.hdr_file_path(self) -> Optional[str]` *(property)* — Current HDR file path on disk, or ``None``.
  - `HdrManager.visibility(self) -> bool` *(property)* — Primary-ray visibility of the HDR (skydome as backdrop).
  - `HdrManager.set_hdr_map_visibility(self, state: bool) -> None` — Backward-compat shim for :attr:`visibility`.
  - `HdrManager.sky_radius(self) -> float` *(property)* — Viewport-preview dome radius (``skyRadius``);
  - `HdrManager.preview(self) -> bool` *(property)* — Viewport-preview visibility — True when the dome shows in the viewport.
  - `HdrManager.rotation(self) -> float` *(property)* — Y rotation (degrees, azimuth) of the skydome transform;
  - `HdrManager.intensity(self) -> float` *(property)* — Linear light-output multiplier on the skydome;
  - `HdrManager.exposure(self) -> float` *(property)* — Photographic stops (log2) on the skydome's ``aiExposure``.
  - `HdrManager.resolution(self) -> int` *(property)* — Importance-sampling resolution of the HDR (``resolution``);
  - `HdrManager.samples(self) -> int` *(property)* — Light samples (``aiSamples``) — soft-IBL noise control;
  - `HdrManager.diffuse(self) -> float` *(property)* — Diffuse contribution scale (``aiDiffuse``);
  - `HdrManager.specular(self) -> float` *(property)* — Specular contribution scale (``aiSpecular``);
  - `HdrManager.create_network(self, hdrMap: str = '', hdrMapVisibility: bool = False, intensity: Optional[float] = None, exposure: Optional[float] = None, rotation: Optional[float] = None, resolution: Optional[int] = None, samples: Optional[int] = None, diffuse: Optional[float] = None, specular: Optional[float] = None, preview: Optional[bool] = None) -> Optional[str]` — Apply settings to the (lazily-created) skydome network.
  - `HdrManager.clear(self) -> None` — Remove the skydome and its connected file/place2d nodes.
- **[`class HdrManagerSlots(ptk.LoggingMixin, ptk.HelpMixin)`](mayatk/mayatk/light_utils/hdr_manager.py#L532)** — Switchboard slots for the HDR Manager UI.
  - `HdrManagerSlots.header_init(self, widget) -> None` — Configure header menu and refresh button.
  - `HdrManagerSlots.cmb000_init(self, widget) -> None` — Wire the HDR dropdown: option-box plugins, context menu, auto-refresh.
  - `HdrManagerSlots.hdr_map(self) -> Optional[str]` *(property)* — Selected HDR file path from the combobox.
  - `HdrManagerSlots.hdr_map_visibility(self) -> bool` *(property)* — Render 'Visible' flag — read from the rotation slider's render toggle.
  - `HdrManagerSlots.hdr_map_preview(self) -> bool` *(property)* — Viewport-preview flag — read from the rotation slider's viewport toggle.
  - `HdrManagerSlots.cmb000(self, index, widget) -> None` — HDR map selection — the panel's sole apply action (always deferred).
  - `HdrManagerSlots.slider000(self, value, widget) -> None` — Rotate the HDR around Y.
  - `HdrManagerSlots.spn_intensity(self, value) -> None` — Set the skydome's HDR intensity (brightness multiplier).
  - `HdrManagerSlots.spn_exposure(self, value) -> None` — Set the skydome's exposure (in stops).
  - `HdrManagerSlots.spn_resolution(self, value) -> None` — Set the baked skydome resolution.
  - `HdrManagerSlots.spn_samples(self, value) -> None` — Set the skydome's render sample count.
  - `HdrManagerSlots.spn_diffuse(self, value) -> None` — Set the skydome's diffuse contribution.
  - `HdrManagerSlots.spn_specular(self, value) -> None` — Set the skydome's specular contribution.
  - `HdrManagerSlots.add_hdr(self) -> None` — Add HDR(s) from one dialog — pick loose files and/or a whole folder.
  - `HdrManagerSlots.open_sourceimages(self) -> None` — Open the workspace's sourceimages folder in Explorer.
  - `HdrManagerSlots.clear_network(self) -> None` — Delete the skydome network and reset the UI to defaults.
  - `HdrManagerSlots.ctx_select_skydome(self) -> None` — Select the skydome (HDR environment) node in the scene.
  - `HdrManagerSlots.ctx_select_transform(self) -> None` — Select the skydome's transform node.
  - `HdrManagerSlots.ctx_select_file_node(self) -> None` — Select the file node feeding the skydome's HDR texture.
  - `HdrManagerSlots.ctx_reveal_in_explorer(self) -> None` — Reveal the skydome's HDR texture file in the system file explorer.

<a id="light_utils--lightmap_baker--lightmap_baker"></a>
### `light_utils/lightmap_baker/lightmap_baker.py`

High-level lightmap baking workflow for Maya -> game engines (Unity-first).

- **[`class LightmapBaker(ptk.LoggingMixin)`](mayatk/mayatk/light_utils/lightmap_baker/lightmap_baker.py#L61)** — Orchestrate the lightmap workflow: bake -> dilate -> engine export prep.
  - `LightmapBaker.preset_store() -> 'ptk.PresetStore'` *(static)* — Shared store of lightmap quality presets (built-in + user tiers).
  - `LightmapBaker.from_preset(cls, name: str, **overrides) -> 'LightmapBaker'` *(class)* — Construct a baker from a named quality preset.
  - `LightmapBaker.bake_fused(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, uv_set: Optional[str] = None, map_size: Optional[int] = None, create_uvs: bool = True, dilate: bool = True, dilate_iterations: Optional[int] = None, alpha_threshold: float = 0.001, prefix: str = 'lightmap_', suffix: str = '', backend: str = 'arnold', on_progress: Optional[Callable[[int, int, str], bool]] = None, stem: Optional[Any] = None, shader: Optional[str] = None, batch: bool = False) -> Dict[str, str]` — Bake a fused HDR lightmap per object into the UV2 channel.
  - `LightmapBaker.bake_separated(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, prefix: str = 'lightmap_irr_', batch: bool = True, **kwargs) -> Dict[str, str]` — Bake a **lighting-only** (white-card) irradiance lightmap per object.
  - `LightmapBaker.commit_unlit(self, mapping: Dict[str, str]) -> Dict[str, str]` — Make the fused bake each object's live appearance (non-destructive).
  - `LightmapBaker.revert_unlit(self, objects: Optional[List[str]] = None) -> List[str]` — Undo :meth:`commit_unlit` -- restore the source material + UV order.
  - `LightmapBaker.pack_atlas(self, mapping: Dict[str, str], output_dir: Optional[str] = None, prefix: str = '', suffix: str = '_Lightmap') -> Dict[str, Tuple[str, List[float]]]` — Consolidate per-object lightmaps into one atlas EXR per primary material.
  - `LightmapBaker.commit_lightmap(self, mapping: Dict[str, str], intensity: float = 1.0, scale_offsets: Optional[Dict[str, List[float]]] = None, uv_rects: Optional[Dict[str, List[float]]] = None) -> Dict[str, str]` — Record a lighting-only bake for the engine (fully non-destructive).
  - `LightmapBaker.refresh_export_metadata(cls) -> Optional[str]` *(class)* — Rebuild the ``lightmap_metadata`` export channel from the scene's markers.
  - `LightmapBaker.revert_lightmap(self, objects: Optional[List[str]] = None) -> List[str]` — Undo :meth:`commit_lightmap` -- drop the markers + republish.
  - `LightmapBaker.revert(self, objects: Optional[List[str]] = None) -> List[str]` — Undo any lightmap wiring -- fused commit and/or lighting-only marker.
- **[`class LightmapBakerSlots(ptk.LoggingMixin, ptk.HelpMixin)`](mayatk/mayatk/light_utils/lightmap_baker/lightmap_baker.py#L1362)** — Switchboard slots for the ``lightmap_baker.ui`` panel.
  - `LightmapBakerSlots.header_init(self, widget) -> None` — Configure the header menu and help text.
  - `LightmapBakerSlots.cmb000_init(self, widget) -> None` — Populate the Quality combobox from the shared preset store.
  - `LightmapBakerSlots.cmb000(self, index, widget) -> None` — Apply the selected preset's dials to the Resolution / Samples fields.
  - `LightmapBakerSlots.cmb001_init(self, widget) -> None` — Populate the bake-level (Mode) combobox;
  - `LightmapBakerSlots.cmb002_init(self, widget) -> None` — Populate the Packing combobox;
  - `LightmapBakerSlots.cmb_scope_init(self, widget) -> None` — Populate the Scope combobox;
  - `LightmapBakerSlots.cmb_resolution_init(self, widget) -> None` — Populate the Resolution combobox (value carried as item data);
  - `LightmapBakerSlots.txt000_init(self, widget) -> None` — Add the Prefix / Suffix / Auto picker to the name-affix field.
  - `LightmapBakerSlots.b000(self) -> None` — Bake lightmaps for the selection in the chosen Mode (revert → bake → commit).
  - `LightmapBakerSlots.revert_to_source(self) -> None` — Undo the bake wiring on the selected objects (or all baked ones).
  - `LightmapBakerSlots.open_sourceimages(self) -> None` — Open the project's sourceimages folder (where bakes go) in Explorer.

<a id="mat_utils--_mat_utils"></a>
### `mat_utils/_mat_utils.py`

- **[`class MatUtils(_MatUtilsInternal)`](mayatk/mayatk/mat_utils/_mat_utils.py#L685)**
  - `MatUtils.resolve_path(path: str, search: bool = True) -> Union[str, None]` *(static)* — Resolve a texture path, expanding env vars and ``<UDIM>`` tokens.
  - `MatUtils.get_mats(objs=None, as_strings=True, mat_type=None, include_displacement=False) -> List[str]` *(static)* — Returns the set of materials assigned to a given list of objects or components.
  - `MatUtils.group_objects_by_material(objects, cluster_by_distance=False, threshold=10000.0)` *(static)* — Groups objects based on their assigned material(s).
  - `MatUtils.is_bundled_texture(path: str) -> bool` *(static)* — Does *path* live inside Maya's own installation?
  - `MatUtils.get_texture_paths(cls, objects: Optional[List[Any]] = None, materials: Optional[List[Any]] = None, file_nodes: Optional[List[Any]] = None, texture_names: Optional[List[str]] = None, absolute: bool = True, exclude_bundled: bool = False) -> List[str]` *(class)* — Resolve unique texture file paths for the given scope.
  - `MatUtils.get_texture_info(cls, objects=None, materials=None, file_nodes=None, texture_names=None)` *(class)* — Get image metadata (size, mode, format) for texture files in scope.
  - `MatUtils.get_mat_info(cls, materials: Optional[List[Any]] = None, objects: Optional[List[Any]] = None, optimize_check: bool = False, progress_callback: Optional[Callable[[int, int, str], None]] = None, exclude_defaults: bool = False, exclude_unassigned: bool = False, include_textures: bool = True, include_image_metadata: bool = True, **optimize_kwargs) -> List[Dict[str, Any]]` *(class)* — Aggregate per-material info: name, type, textures + image metadata.
  - `MatUtils.format_texture_info_text(cls, info_list: List[Dict[str, Any]]) -> str` *(class)* — Render :meth:`get_texture_info` output as a plain-text report (``pythontk.MatReport``).
  - `MatUtils.format_texture_info_html(cls, info_list: List[Dict[str, Any]]) -> str` *(class)* — Render :meth:`get_texture_info` output as styled HTML (``pythontk.MatReport``).
  - `MatUtils.format_mat_info_text(cls, records: List[Dict[str, Any]]) -> str` *(class)* — Render :meth:`get_mat_info` output as a plain-text report (``pythontk.MatReport``).
  - `MatUtils.format_mat_info_html(cls, records: List[Dict[str, Any]]) -> str` *(class)* — Render :meth:`get_mat_info` output as styled HTML (``pythontk.MatReport``).
  - `MatUtils.get_scene_mats(inc=None, exc=None, node_type=None, sort: bool = False, as_dict: bool = False, exclude_defaults: bool = True, exclude_utility_nodes: bool = True, exc_classification=None, **filter_kwargs)` *(static)* — Retrieves all materials from the current scene, with flexible name/type filtering.
  - `MatUtils.get_connected_shaders(cls, file_nodes) -> List[str]` *(class)* — Return surface shaders connected to one or more file nodes, ignoring intermediates.
  - `MatUtils.connect_to_channels(source_plug: str, node: str, attr: str) -> bool` *(static)* — Connect a single-channel `source_plug` into a (possibly compound) slot.
  - `MatUtils.get_mats_by_scope(cls, scope: str = 'selected', mat_type: Optional[str] = None) -> List[str]` *(class)* — Materials in the given scope.
  - `MatUtils.find_opacity_source(cls, mat: str) -> Optional[str]` *(class)* — The file node in `mat`'s network that carries its opacity.
  - `MatUtils.enable_viewport_opacity(cls, materials=None, transparency_algorithm: Optional[str] = None, search_disk: bool = True) -> Dict[str, str]` *(class)* — Wire every opacity map in `materials` so it shows in the viewport.
  - `MatUtils.set_transparency_algorithm(cls, algorithm: str) -> bool` *(class)* — Set the Viewport 2.0 transparency mode.
  - `MatUtils.ensure_transparent_graph(cls, mat: str) -> bool` *(class)* — Load ``Standard_Transparent.sfx`` onto a StingrayPBS node if needed.
  - `MatUtils.get_file_nodes(cls, materials: Optional[List[str]] = None, raw: bool = False, return_type: str = 'fileNode', exc_classification=None) -> list` *(class)* — Returns file node info in any column order based on return_type.
  - `MatUtils.get_fav_mats()` *(static)* — Retrieves the list of favorite materials in Maya.
  - `MatUtils.is_mat_assigned(mat: object) -> bool` *(static)* — True iff *mat*'s shading engines contain at least one DAG member.
  - `MatUtils.is_connected(mat: object, delete: bool = False) -> bool` *(static)* — Checks if a given material is assigned and optionally deletes it.
  - `MatUtils.create_mat(mat_type, prefix='', name='')` *(static)* — Creates a material based on the provided type or a random material if 'mat_type' is 'random'.
  - `MatUtils.assign_mat(objects, mat_name)` *(static)* — Assigns a material to a list of objects or components.
  - `MatUtils.claim_material_name(shading_group: str, desired: str) -> str` *(static)* — Rename a rebuilt network to *desired* once that name is free.
  - `MatUtils.get_shading_assignments(obj) -> Dict[str, Optional[List[int]]]` *(static)* — Snapshot a mesh's shading-group membership as plain data.
  - `MatUtils.apply_shading_assignments(obj, assignments: Dict[str, Optional[List[int]]])` *(static)* — Apply a :meth:`get_shading_assignments` snapshot onto *obj*.
  - `MatUtils.create_file_node(image_path, name=None, color_space=None)` *(static)* — Create a ``file`` texture node with a wired ``place2dTexture``.
  - `MatUtils.create_shading_group(shader, name=None, assign_to=None)` *(static)* — Create a shading group for *shader* and optionally assign objects.
  - `MatUtils.resolve_opacity_mode(cls, opacity_mode=None, opacity: bool = False) -> str` *(class)* — Normalize an opacity-mode argument to a :attr:`STINGRAY_GRAPHS` key.
  - `MatUtils.resolve_stingray_graph(cls, opacity_mode=None, opacity: bool = False)` *(class)* — Absolute path to the ShaderFX preset for *opacity_mode*.
  - `MatUtils.load_stingray_graph(cls, mat, opacity_mode=None, opacity: bool = False) -> bool` *(class)* — Load the ShaderFX preset for *opacity_mode* onto a StingrayPBS node.
  - `MatUtils.create_stingray_shader(cls, name, opacity=False, opacity_mode=None)` *(class)* — Create a StingrayPBS shader by loading a ShaderFX preset graph.
  - `MatUtils.find_by_mat_id(cls, material: str, objects: Optional[List[str]] = None, shell: bool = False) -> List[str]` *(class)* — Find objects or faces by the material ID.
  - `MatUtils.find_unassigned(cls, objects: Optional[List[str]] = None, include_default: bool = True) -> List[str]` *(class)* — Objects carrying no material — the complement of :meth:`find_by_mat_id`.
  - `MatUtils.collect_material_paths(materials: Optional[List[str]] = None, attributes: Optional[List[str]] = None, inc_mat_name: bool = False, inc_path_type: bool = False, resolve_full_path: bool = False) -> Union[List[str], List[Tuple[str, ...]]]` *(static)* — Collects specified attributes file paths for given materials.
  - `MatUtils.remap_file_nodes(file_paths: List[str], target_dir: str, silent: bool = False, limit_to_nodes: Optional[List[str]] = None, as_strings: bool = True) -> List[str]` *(static)* — Internal helper to remap file nodes to target_dir, preserving relative subfolders inside sourceimag…
  - `MatUtils.remap_texture_paths(cls, materials: Optional[List[str]] = None, new_dir: Optional[str] = None, silent: bool = False, file_nodes: Optional[List[str]] = None, objects: Optional[List[str]] = None, as_strings: bool = True) -> None` *(class)* — Remaps file texture paths for materials to new_dir.
  - `MatUtils.stage_textures_relative(cls, file_nodes: List[str], sourceimages: Optional[str] = None) -> Dict[str, str]` *(class)* — Stage textures under sourceimages and store project-relative paths.
  - `MatUtils.is_duplicate_material(material1: str, material2: str) -> bool` *(static)* — Check if two materials are duplicates based on their textures.
  - `MatUtils.find_materials_with_duplicate_textures(cls, materials: Optional[List[str]] = None, strict: bool = False, verify: bool = True) -> Dict[str, List[str]]` *(class)* — Find duplicate materials based on their texture file names or full paths.
  - `MatUtils.reassign_duplicate_materials(cls, materials: Optional[List[str]] = None, delete: bool = False, strict: bool = False, verify: bool = True) -> None` *(class)* — Find duplicate materials, remove duplicates, and reassign them to the original material.
  - `MatUtils.filter_materials_by_objects(objects: List[str], as_strings: bool = True, include_displacement: bool = False) -> List[str]` *(static)* — Filter materials assigned to the given objects.
  - `MatUtils.reload_textures(materials=None, inc=None, exc=None, log=False, refresh_viewport=False, refresh_hypershade=False, texture_types: Optional[List[str]] = None)` *(static)* — Reloads textures connected to specified materials with inclusion/exclusion filters.
  - `MatUtils.move_texture_files(cls, found_files: List[Union[str, Tuple[str, str]]], new_dir: str, delete_old: bool = False, create_dir: bool = True, per_file_timeout: float = 120.0, max_workers: int = 8, progress_callback: Optional[Callable[[int, int, str], bool]] = None) -> List[Tuple[str, str]]` *(class)* — Move or copy found texture files to a new directory.
  - `MatUtils.copy_textures_to_sourceimages(cls, objects: Optional[List[str]] = None, materials: Optional[List[str]] = None, file_nodes: Optional[List[str]] = None, sourceimages_dir: Optional[str] = None, delete_old: bool = False) -> List[Tuple[str, str]]` *(class)* — Copy referenced textures that live outside ``sourceimages`` into it.
  - `MatUtils.find_texture_files(cls, objects: Optional[List[str]] = None, source_dir: str = '', recursive: bool = True, return_dir: bool = False, quiet: bool = False, file_nodes: Optional[List[str]] = None, materials: Optional[List[str]] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> List[Union[str, Tuple[str, str]]]` *(class)* — Find texture files for given objects' materials inside source_dir.
  - `MatUtils.migrate_textures(cls, materials: Optional[List[str]] = None, old_dir: Optional[str] = None, new_dir: Optional[str] = None, silent: bool = False, delete_old: bool = False, objects: Optional[List[str]] = None, file_nodes: Optional[List[str]] = None, progress_callback: Optional[Callable[[int, int, str], bool]] = None) -> None` *(class)* — Copies texture files from an old directory to a new one.
  - `MatUtils.move_unused_textures(source_dir: str = None, output_dir: str = None) -> None` *(static)* — Move unused textures to a specified directory.
  - `MatUtils.get_mat_swatch_icon(mat: Union[str, object], size: List[int] = [20, 20], fallback_to_blank: bool = True) -> object` *(static)* — Get an icon with a color fill matching the given material's RGB value.
  - `MatUtils.convert_bump_to_normal(bump_file_node, output_path: Optional[str] = None, intensity: float = 1.0, format_type: str = 'opengl', create_file_node: bool = True, node_name: Optional[str] = None) -> Optional[str]` *(static)* — Convert a bump/height file node's texture to a normal map on disk.
  - `MatUtils.validate_normal_map_setup(normal_file_node, material=None) -> Dict[str, Any]` *(static)* — Validate normal map file node setup and provide recommendations.
  - `MatUtils.graph_materials(materials: Union[str, List[str], object], mode: str = 'showUpAndDownstream') -> None` *(static)* — Open the Hypershade and graph the specified materials.
  - `MatUtils.get_texture_file_node(material, attr_name, _depth=0)` *(static)* — Locate the file texture node feeding a material attribute.

<a id="mat_utils--arnold_bridge"></a>
### `mat_utils/arnold_bridge.py`

Arnold render-bridge management.

- **[`class ArnoldBridge(ptk.LoggingMixin, _ArnoldBridgeInternal)`](mayatk/mayatk/mat_utils/arnold_bridge.py#L60)** — Add, remove, query, and rebuild Arnold ``aiStandardSurface`` bridges.
  - `ArnoldBridge.add(self, materials: Optional[Union[str, List[str]]] = None, objects: Optional[Union[str, List[str]]] = None, force: bool = False) -> List[str]` — Attach an Arnold bridge to every base material in scope.
  - `ArnoldBridge.remove(self, materials: Optional[Union[str, List[str]]] = None, objects: Optional[Union[str, List[str]]] = None) -> List[str]` — Delete the Arnold bridge from every base material in scope.
  - `ArnoldBridge.rebuild(self, materials: Optional[Union[str, List[str]]] = None, objects: Optional[Union[str, List[str]]] = None) -> List[str]` — Remove and re-add the bridge — resyncs it to the base material's
  - `ArnoldBridge.get_bridge(self, material: str) -> Optional[str]` — Return the ``aiStandardSurface`` bridging *material*, or None.
  - `ArnoldBridge.has_bridge(self, material: str) -> bool` — True if *material*'s shading engine already has an Arnold bridge.
- **[`class ArnoldBridgeSlots(ptk.LoggingMixin, ptk.HelpMixin)`](mayatk/mayatk/mat_utils/arnold_bridge.py#L586)** — Switchboard slots for the ``arnold_bridge.ui`` panel.
  - `ArnoldBridgeSlots.header_init(self, widget) -> None` — Configure the header menu and help text.
  - `ArnoldBridgeSlots.cmb000_init(self, widget) -> None` — Populate the Scope combobox (Selected Objects is the default).
  - `ArnoldBridgeSlots.b000(self) -> None` — Add Network.
  - `ArnoldBridgeSlots.b001(self) -> None` — Remove Network.
  - `ArnoldBridgeSlots.select_bridged(self) -> None` — Header action: select every base material that has a bridge.

<a id="mat_utils--bake_sets"></a>
### `mat_utils/bake_sets.py`

Scene-stored bake-source set shared by the hand-off bridges.

- **[`class BakeSourceSet`](mayatk/mayatk/mat_utils/bake_sets.py#L36)** — The scene's bake source, stored as a plain ``objectSet``.
  - `BakeSourceSet.companion_path(cls, export_path: str) -> str` *(class)* — ``.../asset.fbx`` -> ``.../asset_source.fbx``.
  - `BakeSourceSet.exists(cls) -> bool` *(class)* — Whether a bake-source set node (canonical or legacy) is present.
  - `BakeSourceSet.members(cls) -> List[str]` *(class)* — Long names of the set's surviving members (deleted nodes drop out).
  - `BakeSourceSet.define(cls, objects: Optional[List[str]] = None) -> List[str]` *(class)* — Replace the set's contents with *objects* (default: the selection).
  - `BakeSourceSet.clear(cls) -> None` *(class)* — Delete the set node(s) (members themselves are untouched).

<a id="mat_utils--emissive_groups"></a>
### `mat_utils/emissive_groups.py`

Emissive groups — named face sets that gate emissive regions at runtime.

- **[`class EmissiveGroups(_EmissiveGroupsInternal, ptk.LoggingMixin, ptk.HelpMixin)`](mayatk/mayatk/mat_utils/emissive_groups.py#L206)** — Author, bake, and export named emissive face-groups (see module doc).
  - `EmissiveGroups.add_group(cls, name: str, faces=None, default: float = 1.0) -> str` *(class)* — Create a group from faces (or the selection), or extend an existing one.
  - `EmissiveGroups.remove_group(cls, name: str) -> None` *(class)* — Delete a group's set, registry entry, and any keyable weight attr;
  - `EmissiveGroups.list_groups(cls) -> Dict[str, dict]` *(class)* — ``{name: {"slot", "default", "faces"(count), "missing"(set gone),
  - `EmissiveGroups.select_group(cls, name: str) -> None` *(class)*
  - `EmissiveGroups.set_default(cls, name: str, default: float) -> None` *(class)* — Set the group's default gate weight (0-1; clamped).
  - `EmissiveGroups.make_weights_keyable(cls, names=None) -> Dict[str, str]` *(class)* — Add a keyable 0-1 float per group on the ``data_export`` carrier.
  - `EmissiveGroups.remove_keyable_weights(cls, names=None) -> List[str]` *(class)* — Delete the keyable weight attrs — including their animation — and
  - `EmissiveGroups.key_weight(cls, name: str, value: Optional[float] = None, frame: Optional[float] = None, auto_keyable: bool = True) -> str` *(class)* — Key one group's weight on its carrier attr.
  - `EmissiveGroups.compact_slots(cls) -> List[int]` *(class)* — Reclaim retired slots.
  - `EmissiveGroups.validate(cls) -> List[str]` *(class)* — Non-fatal authoring warnings (empty list = clean).
  - `EmissiveGroups.bake_vertex_colors(cls, force: bool = False) -> dict` *(class)* — Bake membership into the ``emissiveGroups`` RGBA color set.
  - `EmissiveGroups.bake_mask(cls, output_path: Optional[str] = None, resolution: int = 512, padding_px: int = 4, uv_set: Optional[str] = None) -> dict` *(class)* — Rasterize membership into an ``_EMask`` RGBA texture (channels encoding).
  - `EmissiveGroups.refresh_export_metadata(cls) -> Optional[str]` *(class)* — Republish the ``emissive_groups`` channel on the ``data_export``
- **[`class EmissiveGroupsSlots(ptk.LoggingMixin, ptk.HelpMixin)`](mayatk/mayatk/mat_utils/emissive_groups.py#L670)** — Switchboard slots for the ``emissive_groups.ui`` panel.
  - `EmissiveGroupsSlots.header_init(self, widget) -> None`
  - `EmissiveGroupsSlots.txt000_init(self, widget) -> None` — Group-name field — clearable back to the auto-derived name.
  - `EmissiveGroupsSlots.tbl000_init(self, widget) -> None` — Table setup: one-time construction, then (re)wire signals and populate.
  - `EmissiveGroupsSlots.b000(self) -> None` — Add (or extend) a group from the selection.
  - `EmissiveGroupsSlots.b001(self) -> None` — Remove the selected group (retires its slot).
  - `EmissiveGroupsSlots.b002(self) -> None` — Select the group's member faces.
  - `EmissiveGroupsSlots.b003(self) -> None` — Validate authoring state.
  - `EmissiveGroupsSlots.tb000_init(self, widget) -> None` — Initialize Bake.
  - `EmissiveGroupsSlots.tb000(self, widget) -> None` — Bake membership and publish the export manifest.
  - `EmissiveGroupsSlots.select_members(self) -> None`
  - `EmissiveGroupsSlots.remove_group(self) -> None`
  - `EmissiveGroupsSlots.weights_all_on(self) -> None`
  - `EmissiveGroupsSlots.weights_all_off(self) -> None`
  - `EmissiveGroupsSlots.make_weights_keyable(self) -> None`
  - `EmissiveGroupsSlots.key_weights(self) -> None`
  - `EmissiveGroupsSlots.remove_keyable_weights(self) -> None`
  - `EmissiveGroupsSlots.compact_slots(self) -> None`
  - `EmissiveGroupsSlots.republish_export(self) -> None`

<a id="mat_utils--game_shader"></a>
### `mat_utils/game_shader.py`

- **[`class GameShader(ptk.LoggingMixin, _GameShaderInternal)`](mayatk/mayatk/mat_utils/game_shader.py#L149)** — A class to manage the creation of a shader network using StingrayPBS or Standard Surface shaders.
  - `GameShader.create_network(self, textures: List[str], name: str = '', prefix: str = '', suffix: str = '', config: Union[str, Dict[str, Any]] = None, progress_callback: Callable = None, **kwargs) -> Union[Optional[object], List[Optional[object]]]` — Create a PBR shader network with textures.
  - `GameShader.setup_stringray_node(self, name: str, opacity: bool, opacity_mode: str = None) -> object` — Create a StingrayPBS shader node with the right ShaderFX graph loaded.
  - `GameShader.setup_standard_surface_node(self, name: str, opacity: bool) -> object` — Creates and sets up a Maya Standard Surface shader node.
  - `GameShader.setup_open_pbr_node(self, name: str, opacity: bool) -> object` — Creates and sets up a Maya OpenPBR Surface shader node.
  - `GameShader.connect_stingray_nodes(self, texture: str, texture_type: str, sr_node: object) -> bool` — Connects texture files to the corresponding slots in the StingrayPBS shader node
  - `GameShader.connect_standard_surface_nodes(self, texture: str, texture_type: str, std_node: object) -> bool` — Connects texture files to Maya Standard Surface shader slots.
  - `GameShader.connect_open_pbr_nodes(self, texture: str, texture_type: str, op_node: object) -> bool` — Connects texture files to Maya OpenPBR Surface shader slots.
  - `GameShader.filter_for_correct_metallic_map(self, textures: List[str], use_metallic_smoothness: bool, output_extension: str = 'png') -> List[str]` — Filters textures to ensure the correct handling of metallic maps based on the use_metallic_smoothne…
  - `GameShader.filter_for_mask_map(self, textures: List[str], output_extension: str = 'png') -> List[str]` — Creates Unity HDRP Mask Map (MSAO) by packing Metallic, AO, Detail, and Smoothness.
  - `GameShader.filter_for_correct_base_color_map(self, textures: List[str], use_albedo_transparency: bool) -> List[str]` — Filters textures to ensure the correct handling of albedo maps based on the use_albedo_transparency…
- **[`class GameShaderSlots(GameShader)`](mayatk/mayatk/mat_utils/game_shader.py#L1774)**
  - `GameShaderSlots.header_init(self, widget)` — Initialize the header widget.
  - `GameShaderSlots.lbl_graph_material(self)` — Graph the material in the Hypershade.
  - `GameShaderSlots.mat_name(self) -> str` *(property)* — Get the mat name from the user input text field.
  - `GameShaderSlots.mat_prefix(self) -> str` *(property)* — Return the affix text when it resolves as a prefix, else empty string.
  - `GameShaderSlots.mat_suffix(self) -> str` *(property)* — Return the affix text when it resolves as a suffix, else empty string.
  - `GameShaderSlots.normal_map_type(self) -> str` *(property)* — Get the normal map type from the comboBoxes current text.
  - `GameShaderSlots.output_extension(self) -> str` *(property)* — Selected output extension, or '' when 'Profile default' is chosen.
  - `GameShaderSlots.shader_type(self) -> str` *(property)* — Get the shader type selection.
  - `GameShaderSlots.cmb002_init(self, widget)` — Initialize Presets
  - `GameShaderSlots.cmb003_init(self, widget)` — Initialize Output Format.
  - `GameShaderSlots.txt002_init(self, widget)` — Add a prefix/suffix/auto-mode picker to the affix field.
  - `GameShaderSlots.b000(self)` — Create network.

<a id="mat_utils--image_to_plane--_image_to_plane"></a>
### `mat_utils/image_to_plane/_image_to_plane.py`

Map image files to textured polygon planes in Maya.

- **[`class ImageToPlane(ptk.LoggingMixin)`](mayatk/mayatk/mat_utils/image_to_plane/_image_to_plane.py#L23)** — Create textured polygon planes from image files.
  - `ImageToPlane.create(cls, image_paths: List[str], mat_type: str = 'stingray', suffix: str = '_MAT', prefix: str = '', plane_height: float = 10.0, axis: Optional[List[float]] = None, group: bool = False, group_name: str = 'imagePlanes_GRP', stingray_opacity_mode: str = 'transparent', mask_threshold: float = 0.5, roughness: float = 0.0) -> Dict[str, object]` *(class)* — Create textured planes for one or more images.
  - `ImageToPlane.remove(cls, objects=None) -> int` *(class)* — Remove planes and their materials created by this tool.

<a id="mat_utils--image_to_plane--image_to_plane_slots"></a>
### `mat_utils/image_to_plane/image_to_plane_slots.py`

Switchboard slots for the Image to Plane UI.

- **[`class ImageToPlaneSlots`](mayatk/mayatk/mat_utils/image_to_plane/image_to_plane_slots.py#L16)** — Switchboard slots for the Image to Plane UI.
  - `ImageToPlaneSlots.header_init(self, widget)` — Configure header menu.
  - `ImageToPlaneSlots.txt_suffix_init(self, widget)` — Add a prefix/suffix/auto-mode picker to the affix field.

<a id="mat_utils--marmoset_bridge--_marmoset_bridge"></a>
### `mat_utils/marmoset_bridge/_marmoset_bridge.py`

Maya-side glue for the Marmoset Toolbag engine.

- **[`class MarmosetBridge(ptk.HandoffBridge, _MarmosetBridgeInternal)`](mayatk/mayatk/mat_utils/marmoset_bridge/_marmoset_bridge.py#L226)** — Export the Maya selection to Marmoset Toolbag with templated automation.
  - `MarmosetBridge.toolbag_path(self) -> Optional[str]` *(property)*
  - `MarmosetBridge.params_defaults(self) -> Dict[str, Any]`
  - `MarmosetBridge.render_template(self, *args, **kwargs) -> Optional[str]` — Render a Toolbag script body (delegates to the engine deliverer).
  - `MarmosetBridge.source_model_path_for(cls, fbx_path: str) -> str` *(class)* — ``.../asset.fbx`` -> ``.../asset_source.fbx`` (shared convention).
  - `MarmosetBridge.baked_material_name(cls, mat_name: str) -> str` *(class)* — ``<source material>_BAKED``, idempotent across re-bakes.
  - `MarmosetBridge.build_bake_pairs_manifest(objects: Sequence[str], high_suffix: str, low_suffix: str, include_children: bool = True) -> Dict[str, str]` *(static)* — Build the ``{mesh_short_name: 'source'|'target'}`` sidecar for the bake.

<a id="mat_utils--marmoset_bridge--_marmoset_engine"></a>
### `mat_utils/marmoset_bridge/_marmoset_engine.py`

Drive Marmoset Toolbag from the outside -- launch + templated automation.

- **[`class MarmosetEngine(ptk.Deliverer, ptk.LoggingMixin)`](mayatk/mayatk/mat_utils/marmoset_bridge/_marmoset_engine.py#L58)** — Export-agnostic Marmoset Toolbag automation -- a hand-off :class:`pythontk.Deliverer`.
  - `MarmosetEngine.toolbag_path(self) -> Optional[str]` *(property)* — Resolve the Toolbag executable path.
  - `MarmosetEngine.toolbag_log_path(self) -> Optional[str]` *(property)* — Resolve Toolbag's application log file (script prints + tracebacks).
  - `MarmosetEngine.preflight(self, bridge, request) -> bool` — Validate the (template, mode) before the bridge produces its payload.
  - `MarmosetEngine.deliver(self, bridge, payload, request) -> Optional[Dict[str, Any]]` — Hand the produced model + manifests to Toolbag via :meth:`send`.
  - `MarmosetEngine.send(self, model_path: str, manifest_path: Optional[str] = None, pairs_path: Optional[str] = None, source_model_path: Optional[str] = None, output_dir: Optional[str] = None, output_name: Optional[str] = None, toolbag_exe: Optional[str] = None, template: str = 'import', mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]` — Render *template* in *mode* against *model_path* and hand off to Toolbag.
  - `MarmosetEngine.render_template(self, template: str, model_path: str, manifest_path: str, output_dir: str, mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None, headless: Optional[bool] = None, pairs_path: Optional[str] = None, source_model_path: Optional[str] = None) -> Optional[str]` — Return the rendered Toolbag Python script body, or *None* on miss.
  - `MarmosetEngine.list_templates() -> List[Path]` *(static)* — Return user-visible templates in ``templates/`` (skips underscore-prefixed).
  - `MarmosetEngine.template_modes(template_path: Path) -> Tuple[str, ...]` *(static)* — Return the modes declared by *template_path*'s ``BRIDGE_MODES`` constant.
  - `MarmosetEngine.list_template_modes() -> List[Tuple[str, str]]` *(static)* — Return ``[(stem, mode), ...]`` for every (template, mode) pairing.

<a id="mat_utils--marmoset_bridge--_toolbag_helpers"></a>
### `mat_utils/marmoset_bridge/_toolbag_helpers.py`

Shared helpers for Marmoset Toolbag template scripts.

- **[`class ToolbagHelpers(_ToolbagHelpersInternal)`](mayatk/mayatk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L200)** — ToolbagHelpers — module namespace.
  - `ToolbagHelpers.derive_per_run_log_path(manifest_path)` *(static)* — Return the ``<base>.toolbag.log`` path next to *manifest_path*.
  - `ToolbagHelpers.begin_log(reference_path)` *(static)* — Start a fresh log file alongside *reference_path*.
  - `ToolbagHelpers.log(msg)` *(static)* — Print *msg* and (best-effort) append it to the active log file.
  - `ToolbagHelpers.find_material(name, scene_mats)` *(static)* — Return the Toolbag material whose name matches *name*.
  - `ToolbagHelpers.load_manifest(manifest_path)` *(static)* — Return the ``materials`` dict from a MatManifest JSON sidecar.
  - `ToolbagHelpers.wire_materials_from_manifest(manifest_path, verbose=True, srgb_colors=True)` *(static)* — Wire every texture slot in *manifest_path* onto matching Toolbag mats.
  - `ToolbagHelpers.split_source_target(objects, high_suffix, low_suffix, pre_classified=None, include_children=True)` *(static)* — Group *objects* into ``(sources, targets, others)`` by name suffix.
  - `ToolbagHelpers.collect_mesh_objects(root)` *(static)* — Recursively gather ``mset.MeshObject`` descendants of *root*.
  - `ToolbagHelpers.apply_sky_preset(preset_path)` *(static)* — Load a ``.tbsky`` preset onto the scene's existing SkyObject.
  - `ToolbagHelpers.frame_in_viewport()` *(static)* — Frame the imported scene in the viewport (best-effort).

<a id="mat_utils--marmoset_bridge--marmoset_bridge_slots"></a>
### `mat_utils/marmoset_bridge/marmoset_bridge_slots.py`

Slots for the Marmoset Toolbag bridge panel.

- **[`class MarmosetBridgeSlots(MayaBridgeSlotsBase)`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_bridge_slots.py#L41)** — Slots wired to ``marmoset_bridge.ui`` via :class:`MayaBridgeSlotsBase`.
  - `MarmosetBridgeSlots.set_bake_source_from_selection(self) -> None` — Store the current selection as the scene's bake source.
  - `MarmosetBridgeSlots.select_bake_source(self) -> None` — Select the bake-source set's members (hidden ones included).
  - `MarmosetBridgeSlots.clear_bake_source(self) -> None` — Delete the bake-source set node;
  - `MarmosetBridgeSlots.params_module(self)` *(property)*
  - `MarmosetBridgeSlots.template_dir(self) -> Path` *(property)*
  - `MarmosetBridgeSlots.make_bridge(self) -> MarmosetBridge`
  - `MarmosetBridgeSlots.list_template_modes(self)`
  - `MarmosetBridgeSlots.select_initial_template_index(self, pairs)` — Prefer 'bake (roundtrip)' then 'bake (send_to)', else first entry.
  - `MarmosetBridgeSlots.b000(self)` — Process selected transforms with the chosen template + mode.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--connection"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/connection.py`

JSON-RPC client bound to the marmoset_rpc Toolbag plugin.

- **[`class MarmosetConnection(RpcClient, _MarmosetConnectionInternal)`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/connection.py#L52)** — JSON-RPC client bound to Toolbag's default port + finder.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--installer"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/installer.py`

Install the marmoset_rpc plugin into Toolbag's user plugin folder.

- **[`class Installer(_InstallerInternal)`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/installer.py#L39)** — Installer — module namespace.
  - `Installer.user_plugin_dir(toolbag_exe: Optional[str] = None) -> Optional[Path]` *(static)* — Resolve ``%LOCALAPPDATA%\Marmoset Toolbag <N>\plugins``.
  - `Installer.is_installed(toolbag_exe: Optional[str] = None) -> bool` *(static)* — True if the plugin is present at the resolved user plugin dir.
  - `Installer.install(toolbag_exe: Optional[str] = None, force: bool = False) -> Optional[Path]` *(static)* — Install the plugin into Toolbag's user plugin folder.
  - `Installer.uninstall(toolbag_exe: Optional[str] = None) -> bool` *(static)* — Remove the plugin from the user plugin folder.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--job"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/job.py`

One-shot batch pipeline for the marmoset_rpc bridge.

- **[`class BatchJob`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/job.py#L31)** — BatchJob — module namespace.
  - `BatchJob.run_batch(calls: List[Call], host: str = '127.0.0.1', port: int = 8765, stop_on_error: bool = False) -> List[Result]` *(static)* — Connect to a running Toolbag's marmoset_rpc plugin and fire calls.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--__init__"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/__init__.py`

Marmoset Toolbag RPC plugin -- entry point.

- [`start_server(port=None, host=None)`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/__init__.py#L52) — Start the RPC server (idempotent).
- [`stop_server()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/__init__.py#L57) — Shut the server down (tests / hot-reload).
- [`is_running()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/__init__.py#L62) — True while the server is bound.
- [`autostart()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/__init__.py#L67) — Start on plugin load, gated to the Toolbag host.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--_rpc_core"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/_rpc_core.py`

The in-application half of the RPC pair: registry + marshaller + server.

- **[`class OpRegistry(_OpRegistryInternal)`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/_rpc_core.py#L84)** — The callable surface a host plugin exposes over RPC.
  - `OpRegistry.register(self, name)` — Decorator registering the wrapped function under *name*.
  - `OpRegistry.get(self, name)` — Return the op callable registered under *name*, or ``None``.
  - `OpRegistry.all_ops(self)` — Every registered op name, sorted.
  - `OpRegistry.describe(self, name=None)` — Describe one op (``None`` for all) as ``{name, doc, params}``.
- **[`class MainThreadMarshaller(_MainThreadMarshallerInternal)`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/_rpc_core.py#L200)** — Run a callable on the host's Qt main thread and block for its result.
  - `MainThreadMarshaller.is_active(self)` — True when :meth:`run` will marshal rather than call direct.
  - `MainThreadMarshaller.run(self, fn, *args, timeout=None, **kwargs)` — Call *fn*, on the main thread when one is reachable.
- **[`class RpcPlugin(object)`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/_rpc_core.py#L394)** — One host plugin: a registry, a marshaller, and the server that joins them.
  - `RpcPlugin.import_ops(package)` *(static)* — Import *package* (dotted name), forcing its ``@register`` side effects.
  - `RpcPlugin.port(self)` *(property)* — Configured port: ``<PREFIX>_PORT`` if set and numeric, else the default.
  - `RpcPlugin.is_hosted(self)` — True only inside the real host application.
  - `RpcPlugin.is_running(self)` — True while the HTTP server is bound.
  - `RpcPlugin.address(self)` *(property)* — The bound ``(host, port)``, or ``None`` when not running.
  - `RpcPlugin.start(self, port=None, host=None)` — Bind and serve on a daemon thread.
  - `RpcPlugin.stop(self)` — Shut the server down (host teardown hook, tests, hot-reload).
  - `RpcPlugin.autostart(self)` — Start on plugin load, but only when actually hosted.
  - `RpcPlugin.autostart_safely(self)` — :meth:`autostart`, but a failure is logged instead of raised.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--ops--scene_ops"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/scene_ops.py`

Scene-inspection ops.

- [`summary()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/scene_ops.py#L14) — High-level snapshot of the current Toolbag scene.
- [`list_materials()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/scene_ops.py#L39) — Material names in the current scene.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--ops--system_ops"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py`

Toolbag-specific system ops.

- [`version()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py#L14) — Toolbag build number (e.g.

<a id="mat_utils--marmoset_bridge--parameters"></a>
### `mat_utils/marmoset_bridge/parameters.py`

Registry of user-tunable Marmoset Toolbag parameters exposed to the bridge UI.

- **[`class Parameters`](mayatk/mayatk/mat_utils/marmoset_bridge/parameters.py#L404)** — Parameters — module namespace.
  - `Parameters.referenced_keys(script_text: str) -> 'set[str]'` *(static)* — Registered keys present in *script_text* (delegates to uitk.bridge).
  - `Parameters.defaults() -> 'dict[str, Any]'` *(static)* — Return ``{key: default}`` for every registered parameter.
  - `Parameters.render_context(values: 'dict[str, Any]') -> 'dict[str, str]'` *(static)* — Format *values* for ``StrUtils.replace_delimited`` using Python literals.

<a id="mat_utils--marmoset_bridge--template_params"></a>
### `mat_utils/marmoset_bridge/template_params.py`

Plain default values + literal formatting for Marmoset template tokens.

- **[`class TemplateParams`](mayatk/mayatk/mat_utils/marmoset_bridge/template_params.py#L93)** — TemplateParams — module namespace.
  - `TemplateParams.derive_auto_maps(manifest: Dict[str, Any]) -> Dict[str, bool]` *(static)* — Return the ``{MAP_*: bool}`` roster *manifest*'s textures imply.
  - `TemplateParams.derive_bake_values(values: Dict[str, Any]) -> Dict[str, Any]` *(static)* — Return the managed bake tokens derived from *values*.
  - `TemplateParams.python_literal(value: Any) -> str` *(static)* — Format *value* as a Python source literal for template substitution.
  - `TemplateParams.defaults() -> Dict[str, Any]` *(static)* — Return a copy of :data:`DEFAULTS`.
  - `TemplateParams.to_context(values: Dict[str, Any]) -> Dict[str, str]` *(static)* — Map ``{KEY: value}`` to ``{KEY: python-literal-string}``.

<a id="mat_utils--marmoset_bridge--templates--bake"></a>
### `mat_utils/marmoset_bridge/templates/bake.py`

Bake source detail + surface maps onto the target meshes.

- [`main()`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L642)

<a id="mat_utils--marmoset_bridge--templates--import"></a>
### `mat_utils/marmoset_bridge/templates/import.py`

Open the model in Toolbag and wire materials from the manifest.

- [`main()`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/import.py#L35)

<a id="mat_utils--marmoset_bridge--templates--lookdev"></a>
### `mat_utils/marmoset_bridge/templates/lookdev.py`

Open the model in Toolbag, apply a Sky preset, and frame the model.

- [`main()`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/lookdev.py#L38)

<a id="mat_utils--marmoset_bridge--toolbag_log"></a>
### `mat_utils/marmoset_bridge/toolbag_log.py`

Marmoset Toolbag log-file resolution, classification, and live tailing.

- **[`class ToolbagLog`](mayatk/mayatk/mat_utils/marmoset_bridge/toolbag_log.py#L30)** — ToolbagLog — module namespace.
  - `ToolbagLog.resolve_toolbag_log_path(toolbag_exe: Optional[str]) -> Optional[str]` *(static)* — Return the path to Toolbag's application log, robust to version bumps.
  - `ToolbagLog.classify_log_line(line: str) -> Optional[Tuple[str, str]]` *(static)* — Map a Toolbag log line to ``(level, line)`` for routing into a logger.
  - `ToolbagLog.dispatch_log_lines(lines, logger) -> None` *(static)* — Forward each classified line to *logger* at its routed level.
  - `ToolbagLog.start_toolbag_log_tail(log_path: str, start_offset: int, process, logger, poll_interval: float = 0.4, file_wait_timeout: float = 60.0)` *(static)* — Tail *log_path* from *start_offset* in a daemon thread.

<a id="mat_utils--mat_manifest"></a>
### `mat_utils/mat_manifest.py`

- **[`class MatManifest(ptk.HelpMixin)`](mayatk/mayatk/mat_utils/mat_manifest.py#L18)** — Builds and restores a material-to-texture manifest for bridge workflows.
  - `MatManifest.build(cls, objects: List) -> Dict[str, Any]` *(class)* — Build a manifest from the materials assigned to *objects*.
  - `MatManifest.restore(cls, mat_name: str, manifest: Dict[str, Any], source_mat_name: Optional[str] = None) -> int` *(class)* — Reconnect file textures to *mat_name* from a previously built manifest.

<a id="mat_utils--mat_snapshot"></a>
### `mat_utils/mat_snapshot.py`

Lightweight material state snapshot and restore.

- **[`class MatSnapshot`](mayatk/mayatk/mat_utils/mat_snapshot.py#L37)** — Capture and restore material state across destructive operations.
  - `MatSnapshot.capture(cls, mat_name: str, objects=None) -> Dict[str, Any]` *(class)* — Snapshot textures and scalar values for *mat_name*.
  - `MatSnapshot.restore(cls, mat_name: str, snapshot: Dict[str, Any], source_mat_name: Optional[str] = None) -> Dict[str, int]` *(class)* — Restore textures and scalar values onto *mat_name*.

<a id="mat_utils--mat_updater"></a>
### `mat_utils/mat_updater.py`

- **[`class MatUpdater(ptk.LoggingMixin)`](mayatk/mayatk/mat_utils/mat_updater.py#L22)** — Updates existing materials with processed textures.
  - `MatUpdater.update_materials(cls, materials: List[Any] = None, config: Union[str, Dict[str, Any]] = None, verbose: bool = False, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]` *(class)* — Update materials with processed textures.
  - `MatUpdater.disconnect_associated_attributes(cls, material, file_paths, config=None)` *(class)* — Disconnects PBR attributes if they are driven by the specified files.
  - `MatUpdater.update_network(cls, material, texture_paths, config) -> Dict[str, str]` *(class)* — Connect processed textures to the material.
- **[`class MatUpdaterSlots(MatUpdater)`](mayatk/mayatk/mat_utils/mat_updater.py#L667)**
  - `MatUpdaterSlots.header_init(self, widget)` — Format global options in the header menu.
  - `MatUpdaterSlots.selection_mode(self)` *(property)*
  - `MatUpdaterSlots.move_to_folder(self)` *(property)*
  - `MatUpdaterSlots.max_size(self)` *(property)*
  - `MatUpdaterSlots.mask_map_scale(self)` *(property)*
  - `MatUpdaterSlots.output_extension(self)` *(property)*
  - `MatUpdaterSlots.old_files_folder(self)` *(property)*
  - `MatUpdaterSlots.cmb001_init(self, widget)` — Initialize Presets
  - `MatUpdaterSlots.b001(self, widget)` — Update Materials

<a id="mat_utils--render_opacity--_render_opacity"></a>
### `mat_utils/render_opacity/_render_opacity.py`

- **[`class RenderOpacity(ptk.LoggingMixin)`](mayatk/mayatk/mat_utils/render_opacity/_render_opacity.py#L20)** — Manages per-object opacity for engine-ready transparency control.
  - `RenderOpacity.objects_with_visibility_keys(cls, objects) -> List` *(class)* — Return the subset of *objects* that have keyframes on visibility.
  - `RenderOpacity.create(cls, objects=None, mode: str = 'attribute', delete_visibility_keys: bool = False) -> Dict[str, Dict]` *(class)* — Create the opacity mechanism (Attribute, Material graph, or Remove).
  - `RenderOpacity.ensure_connections(cls, objects=None) -> None` *(class)* — Re-establish opacity driver connections on objects that already
  - `RenderOpacity.sync_visibility_from_opacity(cls, objects=None) -> None` *(class)* — Create visibility keyframes mirroring opacity animation curves.
  - `RenderOpacity.key_fade(cls, objects=None, start: float = 0, end: float = 15, direction: str = 'in', auto_create: bool = True, tangent: str = 'linear') -> List[Tuple[str, str]]` *(class)* — Key an opacity fade and mirror to visibility.
  - `RenderOpacity.prepare_for_export(cls, objects=None) -> List[str]` *(class)* — Sync visibility keyframes for every opacity object before FBX export.
  - `RenderOpacity.remove(cls, objects=None, mode: Optional[str] = None) -> None` *(class)* — Remove attributes or reset material settings.

<a id="mat_utils--render_opacity--attribute_mode"></a>
### `mat_utils/render_opacity/attribute_mode.py`

- **[`class OpacityAttributeMode(ptk.LoggingMixin)`](mayatk/mayatk/mat_utils/render_opacity/attribute_mode.py#L15)** — Implements the 'attribute' mode for RenderOpacity.
  - `OpacityAttributeMode.create(cls, objects) -> Dict[str, Dict]` *(class)* — Add 'opacity' attribute on each transform (no keyframes).
  - `OpacityAttributeMode.key_fade(cls, objects, start: float, end: float, direction: str = 'in', auto_create: bool = True, tangent: str = 'linear') -> List[Tuple[str, str]]` *(class)* — Key an opacity fade and mirror to visibility.
  - `OpacityAttributeMode.sync_visibility_from_opacity(cls, objects) -> None` *(class)* — Create visibility keyframes that mirror the opacity animation curve.
  - `OpacityAttributeMode.ensure_connections(cls, objects) -> None` *(class)* — Ensure opacity → visibility mirroring for objects that already
  - `OpacityAttributeMode.remove(cls, objects)` *(class)*

<a id="mat_utils--render_opacity--material_mode"></a>
### `mat_utils/render_opacity/material_mode.py`

- **[`class OpacityMaterialMode(ptk.LoggingMixin)`](mayatk/mayatk/mat_utils/render_opacity/material_mode.py#L16)** — Implements the 'material' mode for RenderOpacity.
  - `OpacityMaterialMode.get_stingray_mats(cls, objects: Optional[list] = None) -> list` *(class)* — Return unique StingrayPBS materials assigned to *objects*.
  - `OpacityMaterialMode.create(cls, objects) -> Dict[str, Dict]` *(class)* — Expose StingrayPBS transparency (load graph).
  - `OpacityMaterialMode.ensure_connections(cls, objects) -> None` *(class)* — Re-establish ``Transform.opacity → Material.opacity`` proxy
  - `OpacityMaterialMode.remove(cls, objects)` *(class)* — Remove material-mode artifacts from *objects*.

<a id="mat_utils--render_opacity--render_opacity_slots"></a>
### `mat_utils/render_opacity/render_opacity_slots.py`

Switchboard slots for the Render Opacity UI.

- **[`class RenderOpacitySlots`](mayatk/mayatk/mat_utils/render_opacity/render_opacity_slots.py#L19)** — Switchboard slots for the Render Opacity UI.
  - `RenderOpacitySlots.header_init(self, widget)` — Configure header menu.
  - `RenderOpacitySlots.tb000_init(self, widget)` — Key Render Opacity Init — configure option-box menu.
  - `RenderOpacitySlots.tb000(self, widget)` — Key Render Opacity — key a fade on the opacity attribute.

<a id="mat_utils--shader_attribute_map"></a>
### `mat_utils/shader_attribute_map.py`

Logical texture channel -> per-shader (attribute, output plug), and the one

- **[`class ShaderAttributeMap(_ShaderAttributeMapInternal)`](mayatk/mayatk/mat_utils/shader_attribute_map.py#L93)** — Central mapping of logical texture/material channels to per-shader attribute/plug pairs.
  - `ShaderAttributeMap.logical_channels(cls) -> Tuple[str, ...]` *(class)* — Returns the logical channel names as a tuple.
  - `ShaderAttributeMap.get_attr(cls, shader_type: str, logical: str) -> Optional[Tuple[str, str]]` *(class)* — Return (attribute, plug) tuple for shader type and logical channel, or None.
  - `ShaderAttributeMap.get_mapping(cls, src_type: str, dst_type: str) -> Tuple[Tuple[str, str, str], ...]` *(class)* — Returns a tuple of (src_attr, src_plug, dst_attr) for each logical channel present in both shader t…
  - `ShaderAttributeMap.connect_channel(cls, file_node: str, logical: str, shader: str, shader_type: Optional[str] = None) -> bool` *(class)* — Wire *file_node* into *shader*'s *logical* channel as this map declares.
  - `ShaderAttributeMap.resolve_live_slot(cls, shader: str, logical: str, shader_type: Optional[str] = None) -> ShaderAttrSlot` *(class)* — The ``(attribute, plug)`` for *logical* that this NODE actually has.
  - `ShaderAttributeMap.map_toggle_attr(cls, attr: str) -> str` *(class)* — The ``use_*`` companion ShaderFX pairs with slot *attr*.
  - `ShaderAttributeMap.add_shader_type(cls, shader_type: str, attrs: ShaderAttrs) -> None` *(class)* — Add a new shader type mapping.
  - `ShaderAttributeMap.update_attr(cls, shader_type: str, logical: str, value: Optional[Tuple[str, str]]) -> None` *(class)* — Update a logical channel mapping for a shader type.
  - `ShaderAttributeMap.as_dict(cls) -> Dict[str, Dict[str, Any]]` *(class)* — Returns a dict of dicts for all shader mappings.

<a id="mat_utils--shader_converter"></a>
### `mat_utils/shader_converter.py`

Retype a material in place — legacy Maya shaders to an exportable PBR one.

- **[`class ShaderConverter(ptk.LoggingMixin, _ShaderConverterInternal)`](mayatk/mayatk/mat_utils/shader_converter.py#L102)** — Convert materials between shader types, preserving textures and assignments.
  - `ShaderConverter.read_channels(cls, shader: str) -> Dict[str, Dict[str, Any]]` *(class)* — What drives each logical channel of *shader*.
  - `ShaderConverter.convert(cls, materials=None, target: str = 'stingray', opacity_mode: str = None, delete_source: bool = True, name_suffix: str = '', verbose: bool = False) -> Dict[str, Optional[str]]` *(class)* — Retype *materials*, keeping their textures and geometry assignments.

<a id="mat_utils--shader_templates--_shader_templates"></a>
### `mat_utils/shader_templates/_shader_templates.py`

- **[`class GraphCollector`](mayatk/mayatk/mat_utils/shader_templates/_shader_templates.py#L30)** — Walk a shading network and serialize it to placeholder-keyed graph info.
  - `GraphCollector.collect_graph(self, nodes)`
- **[`class GraphSaver(GraphCollector)`](mayatk/mayatk/mat_utils/shader_templates/_shader_templates.py#L174)**
  - `GraphSaver.save_graph(self, nodes: List[str], file_path: str, exclude_types: Optional[List[str]] = None) -> None`
- **[`class GraphRestorer`](mayatk/mayatk/mat_utils/shader_templates/_shader_templates.py#L217)**
  - `GraphRestorer.load_yaml(self)` — Load and return graph configuration from a YAML file.
  - `GraphRestorer.restore_graph(self)` — Restore the graph based on the YAML configuration and textures.
  - `GraphRestorer.restore_connections(self)` — Connect nodes as specified in the graph configuration.
- **[`class ShaderTemplates`](mayatk/mayatk/mat_utils/shader_templates/_shader_templates.py#L460)** — Facade class for managing shader templates.
  - `ShaderTemplates.save_template(nodes, file_path, exclude_types=None, logger=None)` *(static)* — Save the specified nodes as a shader template.
  - `ShaderTemplates.restore_template(file_path, texture_paths=None, name=None, logger=None)` *(static)* — Restore a shader template from a file.
- **[`class ShaderTemplatesSlots(ptk.LoggingMixin)`](mayatk/mayatk/mat_utils/shader_templates/_shader_templates.py#L507)**
  - `ShaderTemplatesSlots.header_init(self, widget)` — Initialize the header widget.
  - `ShaderTemplatesSlots.lbl_graph_material(self)` — Graph the last restored material in the Hypershade.
  - `ShaderTemplatesSlots.lbl_open_templates_dir(self)` — Open the shader templates directory in file explorer.
  - `ShaderTemplatesSlots.cmb002_init(self, widget)` — Initialize the ComboBox for shader templates.
  - `ShaderTemplatesSlots.refresh_templates(self, widget)` — Refresh the list of templates.
  - `ShaderTemplatesSlots.rename_template_safe(self, widget, new_name)` — Safe rename that checks for None.
  - `ShaderTemplatesSlots.lbl000(self)` — Set the ComboBox as editable to allow renaming.
  - `ShaderTemplatesSlots.lbl001(self)` — Delete the selected template.
  - `ShaderTemplatesSlots.lbl002(self)` — Open the selected template in the default editor.
  - `ShaderTemplatesSlots.b000(self)` — Create shader network using selected template.
  - `ShaderTemplatesSlots.b001(self)` — Load texture maps and update GUI.
  - `ShaderTemplatesSlots.b002(self)` — Save current graph as a new shader template.

<a id="mat_utils--substance_bridge--_substance_bridge"></a>
### `mat_utils/substance_bridge/_substance_bridge.py`

Substance 3D Painter bridge -- export Maya selection and hand off to Painter.

- **[`class SubstanceBridge(ptk.HandoffBridge)`](mayatk/mayatk/mat_utils/substance_bridge/_substance_bridge.py#L172)** — Export Maya selection to Substance Painter via a chosen template.
  - `SubstanceBridge.painter_path(self) -> Optional[str]` *(property)* — Resolve the Painter executable path via :func:`find_painter_exe`.
  - `SubstanceBridge.painter_log_path(self) -> Optional[str]` *(property)* — Path to Painter's application ``log.txt``, or *None* if absent.
  - `SubstanceBridge.instances(self) -> List[SubstanceConnection]` *(property)* — Live snapshot of managed connections (oldest -> newest, dead pruned).
  - `SubstanceBridge.find_live_managed(self) -> Optional[SubstanceConnection]` — Return the most-recently-launched managed instance whose RPC pings.
  - `SubstanceBridge.send(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, output_name: Optional[str] = None, painter_exe: Optional[str] = None, fbx_options: Optional[Dict[str, Any]] = None, preset_file: Optional[str] = None, template: str = 'import', mode: str = SEND_TO, target: Union[str, int] = TARGET_AUTO, params: Optional[Dict[str, Any]] = None, **legacy_kwargs: Any) -> Optional[Dict[str, Any]]` — Export *objects*, render *template* in *mode*, hand off to Painter.
  - `SubstanceBridge.ensure_rpc_plugin(self) -> None` — Install -- or refresh -- the Painter-side substance_rpc plugin.
  - `SubstanceBridge.mesh_map_files(cls, paths: List[str]) -> List[str]` *(class)* — The subset of *paths* Painter can actually use as mesh maps.
  - `SubstanceBridge.source_model_path_for(cls, fbx_path: str) -> str` *(class)* — ``.../asset.fbx`` -> ``.../asset_source.fbx``.
  - `SubstanceBridge.list_templates() -> List[Path]` *(static)* — Return user-visible templates in ``templates/`` (skips underscore-prefixed).
  - `SubstanceBridge.parse_template(template_path: Path) -> Dict[str, Any]` *(static)* — Read a template's metadata constants without executing the file.
  - `SubstanceBridge.list_template_modes() -> List[Tuple[str, str]]` *(static)* — Return ``[(stem, mode), ...]`` for every (template, mode) pairing.
  - `SubstanceBridge.resolve_painter_log_path(painter_exe: Optional[str] = None) -> Optional[str]` *(static)* — Return the path to Painter's application log.

<a id="mat_utils--substance_bridge--connection"></a>
### `mat_utils/substance_bridge/connection.py`

Substance 3D Painter connection module.

- **[`class SubstanceConnection(ptk.LoggingMixin)`](mayatk/mayatk/mat_utils/substance_bridge/connection.py#L56)** — Launch Painter and expose its stdio, log, and RPC under one object.
  - `SubstanceConnection.open(self) -> 'SubstanceConnection'` — Launch Painter and start readers, tailer, and RPC client.
  - `SubstanceConnection.close(self, terminate: bool = False, timeout: float = 5.0) -> None` — Stop readers and tailer;
  - `SubstanceConnection.is_alive(self) -> bool` — True if Painter is reachable through this connection.
  - `SubstanceConnection.attach(cls, port: int, host: str = '127.0.0.1', log_path: Optional[str] = None, tail_log_from_start: bool = False, verify_alive: bool = True, verify_timeout: float = 2.0) -> 'SubstanceConnection'` *(class)* — Bind to a running Painter on *port* without launching anything.
  - `SubstanceConnection.find_painter_exe() -> Optional[str]` *(static)* — Single source of truth for Painter executable discovery.
  - `SubstanceConnection.default_log_path() -> Optional[str]` *(static)* — Return the standard Substance Painter log path, or None if absent.

<a id="mat_utils--substance_bridge--parameters"></a>
### `mat_utils/substance_bridge/parameters.py`

Registry of user-tunable Substance Painter parameters exposed to the bridge UI.

- **[`class Parameters`](mayatk/mayatk/mat_utils/substance_bridge/parameters.py#L265)** — Parameters — module namespace.
  - `Parameters.referenced_keys(script_text: str) -> 'set[str]'` *(static)* — Registered keys present in *script_text* (delegates to uitk.bridge).
  - `Parameters.defaults() -> 'dict[str, Any]'` *(static)* — Return ``{key: default}`` for every registered parameter.
  - `Parameters.render_cli_context(values: 'dict[str, Any]') -> 'dict[str, str]'` *(static)* — Format *values* for ``LAUNCH_ARGS`` -- raw, no quoting.
  - `Parameters.render_js_context(values: 'dict[str, Any]') -> 'dict[str, str]'` *(static)* — Format *values* for ``RPC_SCRIPT`` -- JS-literal quoting/escaping.

<a id="mat_utils--substance_bridge--substance_bridge_slots"></a>
### `mat_utils/substance_bridge/substance_bridge_slots.py`

Slots for the Substance Painter bridge panel.

- **[`class SubstanceBridgeSlots(MayaBridgeSlotsBase)`](mayatk/mayatk/mat_utils/substance_bridge/substance_bridge_slots.py#L48)** — Slots wired to ``substance_bridge.ui`` via :class:`MayaBridgeSlotsBase`.
  - `SubstanceBridgeSlots.set_bake_source_from_selection(self) -> None` — Store the current selection as the scene's bake source.
  - `SubstanceBridgeSlots.select_bake_source(self) -> None` — Select the bake-source set's members (hidden ones included).
  - `SubstanceBridgeSlots.clear_bake_source(self) -> None` — Delete the bake-source set node;
  - `SubstanceBridgeSlots.params_module(self)` *(property)*
  - `SubstanceBridgeSlots.template_dir(self) -> Path` *(property)*
  - `SubstanceBridgeSlots.make_bridge(self) -> SubstanceBridge`
  - `SubstanceBridgeSlots.list_template_modes(self)`
  - `SubstanceBridgeSlots.select_initial_template_index(self, pairs)` — Default the panel to ``import (send_to)`` when it's available.
  - `SubstanceBridgeSlots.b000(self)` — Process the selected transforms with the chosen template + mode.

<a id="mat_utils--substance_bridge--substance_rpc--client"></a>
### `mat_utils/substance_bridge/substance_rpc/client.py`

HTTP RPC client for the Painter-side ``substance_rpc`` plugin.

- **[`class PainterRpcClient(RpcClient)`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/client.py#L34)** — RPC client bound to the substance_rpc plugin's defaults.
  - `PainterRpcClient.wait_until_ready(self, timeout: float = 60.0, poll_interval: float = 0.5) -> bool` — Poll ``/health`` until the plugin answers, or *timeout* expires.
  - `PainterRpcClient.invoke(self, op: str, timeout: Optional[float] = None, **kwargs: Any) -> Any` — :meth:`RpcClient.invoke` with this client's default timeout.
  - `PainterRpcClient.eval_js(self, script: str) -> Any` — Evaluate *script* in Painter's JS engine (``alg.*`` API surface).
  - `PainterRpcClient.eval_py(self, script: str) -> Any` — Exec *script* (Python) inside Painter;
  - `PainterRpcClient.reload_mesh(self, mesh_path: str, preserve_strokes: bool = True, import_cameras: bool = False) -> Any` — Ask Painter to reload the open project's mesh from *mesh_path*.
  - `PainterRpcClient.reload_status(self) -> Any` — Outcome of the last reload: ``{"status": ..., "mesh_path": ...}``.
  - `PainterRpcClient.project_info(self) -> Any` — ``{is_open, file_path, mesh_path, needs_saving}`` for the open project.

<a id="mat_utils--substance_bridge--substance_rpc--installer"></a>
### `mat_utils/substance_bridge/substance_rpc/installer.py`

Install the substance_rpc plugin into Painter's user plugin folder.

- **[`class Installer(_InstallerInternal)`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/installer.py#L87)** — Installer — module namespace.
  - `Installer.user_plugin_dir() -> Optional[Path]` *(static)* — Resolve Painter's Python plugins folder.
  - `Installer.is_installed() -> bool` *(static)* — True if the plugin is present at the resolved user plugin dir.
  - `Installer.is_current() -> bool` *(static)* — True if the installed plugin matches the one this package ships.
  - `Installer.install(force: bool = False) -> Optional[Path]` *(static)* — Install the plugin into Painter's user plugin folder.
  - `Installer.uninstall() -> bool` *(static)* — Remove the plugin from the user plugin folder.

<a id="mat_utils--substance_bridge--substance_rpc--plugin_src--substance_rpc--__init__"></a>
### `mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/__init__.py`

Substance 3D Painter RPC plugin -- entry point.

- [`start_server(port=None, host=None)`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/__init__.py#L53) — Start the RPC server (idempotent).
- [`stop_server()`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/__init__.py#L58) — Shut the server down (close_plugin hook / tests / hot-reload).
- [`is_running()`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/__init__.py#L63) — True while the server is bound.
- [`autostart()`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/__init__.py#L68) — Start on plugin load, gated to the Painter host.
- [`start_plugin()`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/__init__.py#L73) — Painter lifecycle hook: start the RPC server (idempotent).
- [`close_plugin()`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/__init__.py#L80) — Painter lifecycle hook: shut the RPC server down.

<a id="mat_utils--substance_bridge--substance_rpc--plugin_src--substance_rpc--_rpc_core"></a>
### `mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/_rpc_core.py`

The in-application half of the RPC pair: registry + marshaller + server.

- **[`class OpRegistry(_OpRegistryInternal)`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/_rpc_core.py#L84)** — The callable surface a host plugin exposes over RPC.
  - `OpRegistry.register(self, name)` — Decorator registering the wrapped function under *name*.
  - `OpRegistry.get(self, name)` — Return the op callable registered under *name*, or ``None``.
  - `OpRegistry.all_ops(self)` — Every registered op name, sorted.
  - `OpRegistry.describe(self, name=None)` — Describe one op (``None`` for all) as ``{name, doc, params}``.
- **[`class MainThreadMarshaller(_MainThreadMarshallerInternal)`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/_rpc_core.py#L200)** — Run a callable on the host's Qt main thread and block for its result.
  - `MainThreadMarshaller.is_active(self)` — True when :meth:`run` will marshal rather than call direct.
  - `MainThreadMarshaller.run(self, fn, *args, timeout=None, **kwargs)` — Call *fn*, on the main thread when one is reachable.
- **[`class RpcPlugin(object)`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/_rpc_core.py#L394)** — One host plugin: a registry, a marshaller, and the server that joins them.
  - `RpcPlugin.import_ops(package)` *(static)* — Import *package* (dotted name), forcing its ``@register`` side effects.
  - `RpcPlugin.port(self)` *(property)* — Configured port: ``<PREFIX>_PORT`` if set and numeric, else the default.
  - `RpcPlugin.is_hosted(self)` — True only inside the real host application.
  - `RpcPlugin.is_running(self)` — True while the HTTP server is bound.
  - `RpcPlugin.address(self)` *(property)* — The bound ``(host, port)``, or ``None`` when not running.
  - `RpcPlugin.start(self, port=None, host=None)` — Bind and serve on a daemon thread.
  - `RpcPlugin.stop(self)` — Shut the server down (host teardown hook, tests, hot-reload).
  - `RpcPlugin.autostart(self)` — Start on plugin load, but only when actually hosted.
  - `RpcPlugin.autostart_safely(self)` — :meth:`autostart`, but a failure is logged instead of raised.

<a id="mat_utils--substance_bridge--substance_rpc--plugin_src--substance_rpc--ops--project_ops"></a>
### `mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/project_ops.py`

Project-level ops: inspect the open project and reload its mesh.

- [`project_info()`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/project_ops.py#L27) — Return ``{is_open, file_path, mesh_path, needs_saving}`` (best-effort).
- [`mesh_reload(mesh_path='', preserve_strokes=True, import_cameras=False)`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/project_ops.py#L60) — Reload the open project's mesh from *mesh_path* (async).
- [`mesh_reload_status()`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/project_ops.py#L102) — Outcome of the last ``mesh.reload``: pending / success / failure.

<a id="mat_utils--substance_bridge--substance_rpc--plugin_src--substance_rpc--ops--setup_ops"></a>
### `mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/setup_ops.py`

Project-setup ops: resolution, the baking high poly, and mesh maps.

- [`teardown()`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/setup_ops.py#L83) — Drop any pending values and unsubscribe (plugin-disable hook).
- [`set_resolution(size=0)`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/setup_ops.py#L157) — Set the document resolution of every texture set to *size* px square.
- [`set_high_poly(mesh_path='')`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/setup_ops.py#L220) — Set the Hipoly Mesh of every texture set's baking parameters.
- [`apply_mesh_maps(manifest_path='')`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/setup_ops.py#L351) — Wire each material's baked maps onto its own texture set.
- [`pending_setup()`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/setup_ops.py#L378) — Return what is queued for the next project-open (diagnostics).

<a id="mat_utils--substance_bridge--substance_rpc--plugin_src--substance_rpc--ops--system_ops"></a>
### `mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/system_ops.py`

Painter-specific system ops: version reporting and script evaluation.

- [`version()`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/system_ops.py#L18) — Return Painter + plugin API version info (best-effort).
- [`eval_python(script='')`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/system_ops.py#L38) — Exec *script* (Python source) inside Painter's interpreter.
- [`js_evaluate(script='')`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/system_ops.py#L60) — Evaluate *script* in Painter's JavaScript engine (``alg.*`` API).

<a id="mat_utils--texture_baker"></a>
### `mat_utils/texture_baker.py`

Bake an object's shaded surface (material under scene lighting) to a texture.

- **[`class TextureBaker(ptk.LoggingMixin)`](mayatk/mayatk/mat_utils/texture_baker.py#L62)** — Bake scene lighting per object to a texture file (PNG, EXR, ...).
  - `TextureBaker.arnold_available() -> bool` *(static)* — True if the ``mtoa`` plugin is loaded AND its bake cmd is registered.
  - `TextureBaker.bake(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, prefix: str = 'bake_', suffix: str = '', backend: str = 'auto', uv_set: Optional[Union[str, Dict[str, str]]] = None, on_progress: Optional[Callable[[int, int, str], bool]] = None, stem: Optional[Union[Callable[[str], str], Dict[str, str]]] = None, shader: Optional[str] = None, batch: bool = False) -> Dict[str, str]` — Bake lighting per object to texture files (EXR on Arnold).
  - `TextureBaker.assign_to_diffuse(self, mapping: Dict[str, str]) -> None` — Wire each baked PNG into the object's material color slot.
  - `TextureBaker.restore_diffuse_connections(self) -> None` — Undo :meth:`assign_to_diffuse` -- reconnects previous drivers.

<a id="mat_utils--texture_path_editor"></a>
### `mat_utils/texture_path_editor.py`

- **[`class TexturePathEditorSlots`](mayatk/mayatk/mat_utils/texture_path_editor.py#L22)**
  - `TexturePathEditorSlots.header_init(self, widget)` — Initialize the header menu.
  - `TexturePathEditorSlots.tb_set_texture_directory_init(self, widget)` — Populate the Set Directory option-box with the relocate-mode combobox.
  - `TexturePathEditorSlots.tb_find_and_copy_textures_init(self, widget)` — Populate the Find & Copy option-box with the copy/move combobox.
  - `TexturePathEditorSlots.tb_normalize_paths_init(self, widget)` — Populate the Normalize Paths option-box with the external-mode combobox.
  - `TexturePathEditorSlots.tb_resolve_missing_textures_init(self, widget)` — Populate the Resolve Missing option-box with the strategy checkboxes.
  - `TexturePathEditorSlots.tbl000_init(self, widget)`
  - `TexturePathEditorSlots.open_source_images(self)` — Open the project's sourceimages directory.
  - `TexturePathEditorSlots.reload_scene_textures(self)` — Force Maya to re-read all scene textures from disk.
  - `TexturePathEditorSlots.tb_set_texture_directory(self, widget=None)` — Repath file nodes (selection or all) under a chosen directory.
  - `TexturePathEditorSlots.tb_find_and_copy_textures(self, widget=None)` — Find textures from a source dir, copy or move to a destination, repath.
  - `TexturePathEditorSlots.tb_normalize_paths(self, widget=None)` — Rewrite paths under sourceimages to relative.
  - `TexturePathEditorSlots.make_paths_absolute(self)` — Rewrite relative paths (selection or all) to absolute.
  - `TexturePathEditorSlots.tb_resolve_missing_textures(self, widget=None)` — Resolve missing textures with configurable cascade strategies.
  - `TexturePathEditorSlots.select_textures_for_objects(self)` — Select table rows whose textures are used by the scene selection.
  - `TexturePathEditorSlots.select_broken_paths(self)` — Select rows whose texture file is missing.
  - `TexturePathEditorSlots.select_absolute_paths(self)` — Select rows whose path is absolute (regardless of validity).
  - `TexturePathEditorSlots.row_browse_for_file(self, selection=None)` — Open a file dialog and repath the selected row's file node.
  - `TexturePathEditorSlots.select_material(self, selection=None)` — Select scene objects assigned to the materials of selected rows.
  - `TexturePathEditorSlots.select_file_node(self, selection=None)` — Select the file nodes from the selected rows.
  - `TexturePathEditorSlots.row_show_in_hypershade(self, selection=None)` — Graph the selected file node(s) in Hypershade.
  - `TexturePathEditorSlots.delete_file_node(self, selection=None)` — Delete the selected file node(s).
  - `TexturePathEditorSlots.refresh_texture_table(self)` — Manual refresh trigger from the header refresh button.
  - `TexturePathEditorSlots.cleanup_scene_callbacks(self)` — Clean up scene-change subscriptions via ScriptJobManager.
  - `TexturePathEditorSlots.setup_formatting(self, widget)`
  - `TexturePathEditorSlots.handle_cell_edit(self, row: int, col: int)`

<a id="node_utils--_node_utils"></a>
### `node_utils/_node_utils.py`

- **[`class NodeUtils(ptk.HelpMixin)`](mayatk/mayatk/node_utils/_node_utils.py#L35)**
  - `NodeUtils.get_type(cls, objects: Union[str, Any, List[Any]]) -> Union[str, List[str]]` *(class)* — Get the object type as a string.
  - `NodeUtils.get_inherited_types(node: str) -> List[str]` *(static)* — Get the inheritance hierarchy for a node type.
  - `NodeUtils.is_mesh(cls, objects, filter: bool = False)` *(class)* — Return True for each object that is a transform node with a mesh shape child.
  - `NodeUtils.is_locator(objects, filter: bool = False)` *(static)* — Determine if each of the given object(s) is a locator.
  - `NodeUtils.is_group(objects, filter: bool = False)` *(static)* — Determine if each of the given object(s) is a group.
  - `NodeUtils.is_geometry(cls, objects, filter: bool = False)` *(class)* — Return True for each object that has a shape node and is not a group.
  - `NodeUtils.is_constraint(objects, filter: bool = False)` *(static)* — Determine if each object inherits from Maya's constraint base type.
  - `NodeUtils.is_expression(objects, filter: bool = False)` *(static)* — Determine if each object is a Maya expression node.
  - `NodeUtils.is_ik_effector(objects, filter: bool = False)` *(static)* — Determine if each object is an IK effector node.
  - `NodeUtils.is_driven_key_curve(objects, filter: bool = False)` *(static)* — Determine if each animCurve is a driven key (has input connection).
  - `NodeUtils.is_muted(objects, filter: bool = False)` *(static)* — Determine if each node is muted/disabled via nodeState attribute.
  - `NodeUtils.is_motion_path(objects, filter: bool = False)` *(static)* — Determine if each object is a motionPath node.
  - `NodeUtils.is_ik_handle(objects, filter: bool = False)` *(static)* — Determine if each object is an ikHandle node.
  - `NodeUtils.get_constraint_targets(constraint: str) -> list` *(static)* — Get the target objects for a constraint node.
  - `NodeUtils.get_groups(cls, empty=False)` *(class)* — Get all groups in the scene.
  - `NodeUtils.get_parent(node, all=False, full_path=False, type='transform')` *(static)* — Return the parent of *node*.
  - `NodeUtils.get_children(node, type='transform', full_path=False)` *(static)* — List the children of *node*.
  - `NodeUtils.get_shapes(cls, node, no_intermediate=True, full_path=True)` *(class)* — Return the shape(s) associated with *node* -- flexible about input.
  - `NodeUtils.get_shape(cls, node, no_intermediate=True, full_path=True)` *(class)* — Return the first shape for a transform / shape / component, or ``None``.
  - `NodeUtils.is_intermediate(shape)` *(static)* — Return True if *shape* is an intermediate (orig) shape.
  - `NodeUtils.node_is(node, type_name)` *(static)* — Return True if ``cmds.objectType(node)`` matches *type_name* exactly.
  - `NodeUtils.list_transforms(objects=None, **ls_kwargs)` *(static)* — Transforms whose shapes match the given ``cmds.ls`` criteria.
  - `NodeUtils.get_unique_children(cls, objects)` *(class)* — Retrieves a unique list of objects' children (if any) in the scene, excluding the groups themselves.
  - `NodeUtils.get_transform_node(nodes, returned_type='obj', attributes=False, inc=[], exc=[])` *(static)* — Get transform node(s) or node attributes.
  - `NodeUtils.get_shape_node(cls, nodes, returned_type='obj', attributes=False, inc=[], exc=[])` *(class)* — Get shape node(s) or node attributes.
  - `NodeUtils.get_history_node(nodes, returned_type='obj', attributes=False, inc=[], exc=[])` *(static)* — Get history node(s) or node attributes.
  - `NodeUtils.get_classification_tokens(node_type: str) -> List[str]` *(static)* — Role classifications of *node_type* — ``shader/surface``, ``utility/math``, …
  - `NodeUtils.create_render_node(cls, node_type, classification=None, category=None, name=None, create_placement_nodes=False, create_shading_group=True, **attributes)` *(class)* — Creates a Maya node of a specified type with enhanced control over the creation process.
  - `NodeUtils.get_connected_nodes(node, node_type=None, direction=None, exact=True, first_match=False)` *(static)* — Finds connected nodes of a given type and direction (incoming/outgoing).
  - `NodeUtils.create_assembly(nodes, assembly_name='assembly#', duplicate=False)` *(static)* — Create an assembly by parenting the input nodes to a new assembly node.
  - `NodeUtils.get_instances(objects=None, return_parent_objects=False)` *(static)* — Get any instances of given object, or if None given, get all instanced objects in the scene.
  - `NodeUtils.replace_with_instances(cls, objects=None, append='', freeze_transforms=False, center_pivot=True, delete_history=True, retain_bbox_scale=False, retain_bbox_per_axis=False)` *(class)* — Replace target objects with instances of the source object.
  - `NodeUtils.instance(cls, *args, **kwargs)` *(class)* — Deprecated: Use replace_with_instances instead.
  - `NodeUtils.get_instanced_shapes(cls, node, intermediate: bool = True) -> List[str]` *(class)* — Every shape under *node* that is shared with another transform.
  - `NodeUtils.uninstance(cls, objects, freeze=False, delete_history=False, quiet=True)` *(class)* — Un-Instance the given objects.
  - `NodeUtils.filter_duplicate_instances(nodes) -> List[str]` *(static)* — Keep only one transform per instance group.

<a id="node_utils--attributes--_attributes"></a>
### `node_utils/attributes/_attributes.py`

Consolidated attribute utilities for Maya.

- **[`class AttributeTemplate`](mayatk/mayatk/node_utils/attributes/_attributes.py#L32)** — Defines the configuration for a Maya attribute.
- **[`class Preset(NamedTuple)`](mayatk/mayatk/node_utils/attributes/_attributes.py#L57)** — A named bundle of attributes loaded from a YAML template.
- **[`class Attributes(ptk.HelpMixin)`](mayatk/mayatk/node_utils/attributes/_attributes.py#L69)** — Consolidated utility for managing Maya node attributes.
  - `Attributes.has_attr(node: str, attr: str) -> bool` *(static)* — Return True if *attr* exists on *node*.
  - `Attributes.set_plug(plug: str, value: Any, force: bool = False) -> None` *(static)* — Write *value* to *plug*, optionally bypassing a lock.
  - `Attributes.attr_short_name(long_name: str, node: str = '') -> str` *(static)* — Return the short attribute name for a long attribute name.
  - `Attributes.abbreviate_attrs(cls, attrs: List[str]) -> str` *(class)* — Return a compact summary string for a list of attribute names.
  - `Attributes.apply_preset(cls, name: str, objects) -> List[str]` *(class)* — Look up a named preset and create its attributes on *objects*.
  - `Attributes.remove_preset(cls, name: str, objects) -> None` *(class)* — Remove a preset's attributes from *objects*.
  - `Attributes.create_attributes(cls, objects, template: AttributeTemplate) -> List[str]` *(class)* — Apply an ``AttributeTemplate`` to a list of objects.
  - `Attributes.ensure_attribute(cls, obj, template: AttributeTemplate) -> bool` *(class)* — Create an attribute on *obj* from *template* if it doesn't already exist.
  - `Attributes.get_attributes(node, inc=None, exc=None, exc_defaults=False, quiet=True, **kwargs) -> dict` *(static)* — Retrieve a node's attributes and their current values.
  - `Attributes.get_type(cls, value) -> str` *(class)* — Determine the Maya attribute type string for a given Python value.
  - `Attributes.get_selected_channels() -> List[str]` *(static)* — Get attributes selected in the channel box.
  - `Attributes.get_channel_box_values(objects, *args, include_locked=False, include_nonkeyable=False, include_object_name=False, as_group=False) -> dict` *(static)* — Retrieve current channel-box attribute values for *objects*.
  - `Attributes.set_attributes(cls, node, create: bool = False, quiet: bool = False, keyable: bool = False, lock: bool = False, **attributes) -> None` *(class)* — Set values on existing node attributes.
  - `Attributes.create_or_set(cls, node, keyable=True, **attributes) -> None` *(class)* — Set attribute values, creating them first if they don't exist.
  - `Attributes.create_switch(node, attr_name: str, weighted: bool = False, min_value: float = 0.0, max_value: float = 1.0) -> str` *(static)* — Create a bool or float (weighted) switch attribute if it doesn't exist.
  - `Attributes.connect(attr: str, place: str, file: str) -> None` *(static)* — Connect a same-named attribute between two nodes.
  - `Attributes.connect_multi(*args, force=True) -> None` *(static)* — Connect multiple attribute pairs at once.
  - `Attributes.trace_upstream(cls, plug: str, passthrough_types: Optional[set] = None, visited: Optional[set] = None) -> Tuple[Optional[str], Optional[str]]` *(class)* — Trace upstream through passthrough nodes to find the true driver.
  - `Attributes.get_lock_state(cls, objects, unlock: bool = False) -> Dict[str, Dict[str, Any]]` *(class)* — Return lock state for standard transform attributes.
  - `Attributes.set_lock_state(cls, objects, lock_state: Optional[Dict[str, Dict[str, bool]]] = None, translate: Optional[bool] = None, rotate: Optional[bool] = None, scale: Optional[bool] = None, **kwargs) -> None` *(class)* — Restore lock state from a saved dict, or bulk lock/unlock.
  - `Attributes.temporarily_unlock(cls, objects, attributes=None)` *(class)* — Context manager: temporarily unlock attributes and restore state on exit.
  - `Attributes.copy_values(cls, objects, attributes: Optional[List[str]] = None) -> Dict[str, Any]` *(class)* — Copy attribute values from the first object into the class clipboard.
  - `Attributes.paste_values(cls, objects, values: Optional[Dict[str, Any]] = None) -> None` *(class)* — Paste attribute values onto *objects*.
  - `Attributes.reset_to_default(objects, attributes: List[str]) -> None` *(static)* — Reset attributes to their default values.
  - `Attributes.mute(objects, attributes: Optional[List[str]] = None) -> None` *(static)* — Mute channels to suppress animation evaluation.
  - `Attributes.unmute(objects, attributes: Optional[List[str]] = None) -> None` *(static)* — Unmute previously muted channels.
  - `Attributes.set_channel_box_visibility(objects, attributes: List[str], visible: bool = True) -> None` *(static)* — Show or hide attributes in the channel box.
  - `Attributes.lock_and_hide(objects, attributes: List[str]) -> None` *(static)* — Lock attributes and hide them from the channel box.
  - `Attributes.filter(attributes: List[str], exclude: Union[str, List[str], None] = None, include: Union[str, List[str], None] = None, case_sensitive: bool = False) -> List[str]` *(static)* — Filter attribute names by inclusion/exclusion patterns.
  - `Attributes.parse_enum_def(node, attr_name)` *(static)* — Return ``[(label, index), ...]`` for an enum attribute.
  - `Attributes.build_enum_string(pairs)` *(static)* — Build an ``enumName`` string from ``[(label, index), ...]``.
  - `Attributes.get_enum_fields(node, attr_name)` *(static)* — Return the list of enum field labels for *attr_name*.
  - `Attributes.get_enum_label(node, attr_name)` *(static)* — Return the current enum label for an enum attribute, or ``None``.
  - `Attributes.enum_label_to_index(node, attr_name, label)` *(static)* — Return the integer index for an enum label, or ``-1`` if not found.
  - `Attributes.rename_enum_field(nodes, attr_name, old_label, new_label)` *(static)* — Rename a single enum field from *old_label* to *new_label*.
  - `Attributes.add_enum_field(nodes, attr_name, new_label)` *(static)* — Append a new enum field *new_label* to *attr_name*.
  - `Attributes.delete_enum_field(nodes, attr_name, label)` *(static)* — Remove the enum field *label* from *attr_name*.

<a id="node_utils--attributes--channels--__init__"></a>
### `node_utils/attributes/channels/__init__.py`

Channels — Switchboard UI for inspecting and editing Maya attributes.

- [`launch(sb=None, targets=None, filter=None, search=None)`](mayatk/mayatk/node_utils/attributes/channels/__init__.py#L14) — Open the Channels UI, optionally pre-targeted.

<a id="node_utils--attributes--channels--_channels"></a>
### `node_utils/attributes/channels/_channels.py`

Channels — Maya attribute query / mutation logic.

- **[`class Channels`](mayatk/mayatk/node_utils/attributes/channels/_channels.py#L16)** — Maya attribute query / mutation logic.
  - `Channels.is_pinned(self)` *(property)*
  - `Channels.single_object_mode(self)` *(property)*
  - `Channels.pin_targets(self, nodes)` — Pin the manager to a fixed node list;
  - `Channels.get_selected_nodes(self)` — Return the target node list.
  - `Channels.resolve_component_targets(nodes)` *(static)* — Collapse component selections to the transform that owns them.
  - `Channels.get_channel_box_selection()` *(static)* — Return all attribute names currently selected in Maya's channel box.
  - `Channels.get_filter_kwargs(filter_key='Custom', invert=False)` *(static)* — Return the ``cmds.listAttr`` kwargs for the given *filter_key*.
  - `Channels.query_connected_attrs(node)` *(static)* — Return set of attribute names on *node* that have incoming connections.
  - `Channels.collect_attr_names(nodes, filter_kwargs)` *(static)* — Return the intersection of attribute names across *nodes*.
  - `Channels.collect_value_strings(cls, nodes, attr_names)` *(class)* — Return ``{attr_name: (value_str, conn_type)}`` for the given attrs.
  - `Channels.get_attr_value(node, attr_name)` *(static)* — Safely get an attribute value, returning ``None`` on failure.
  - `Channels.get_attr_type(node, attr_name)` *(static)* — Return the Maya attribute type string.
  - `Channels.get_incoming_connection(node, attr_name)` *(static)* — Return ``'→ src.attr'`` if there is an incoming connection, else ``''``.
  - `Channels.classify_connection(cls, node, attr_name)` *(class)* — Classify the incoming connection on *node.attr_name*.
  - `Channels.has_key_at_current_time(plug)` *(static)* — Return ``True`` if *plug* has a keyframe set exactly at the current time.
  - `Channels.build_table_data(cls, nodes, filter_kwargs)` *(class)* — Build row data and state tuples for the table.
  - `Channels.format_value(val)` *(static)* — Convert a Maya attribute value to a display string.
  - `Channels.parse_value(text, attr_type)` *(static)* — Convert user-entered text to a Python value for ``cmds.setAttr``.
  - `Channels.toggle_lock(nodes, attr_name)` *(static)* — Toggle the lock state for *attr_name* on *nodes*.
  - `Channels.break_connections(nodes, attr_name)` *(static)* — Break all incoming connections for *attr_name* on *nodes*.
  - `Channels.set_lock(nodes, attr_names, lock)` *(static)* — Lock or unlock *attr_names* across all *nodes*.
  - `Channels.reset_to_default(nodes, attr_names)` *(static)* — Reset *attr_names* to their default values across all *nodes*.
  - `Channels.toggle_keyable(nodes, attr_names)` *(static)* — Toggle the keyable state for *attr_names* across all *nodes*.
  - `Channels.delete_attributes(nodes, attr_names)` *(static)* — Delete custom *attr_names* across all *nodes*.
  - `Channels.set_attribute_value(cls, nodes, attr_name, text)` *(class)* — Parse *text* and set *attr_name* on all *nodes*.
  - `Channels.create_attribute(cls, nodes, name, attr_type, keyable=True, min_val=None, max_val=None, default_val=0.0, enum_names='')` *(class)* — Create a custom attribute on *nodes*.
  - `Channels.copy_attr_values(nodes, attr_names)` *(static)* — Copy attribute values from the primary node to the clipboard.
  - `Channels.paste_attr_values(nodes)` *(static)* — Paste previously copied attribute values onto *nodes*.
  - `Channels.rename_attribute(nodes, old_name, new_name)` *(static)* — Rename a user-defined attribute on *nodes*.
  - `Channels.rename_node(old_name, new_name)` *(static)* — Rename a Maya node and return its new full path.
  - `Channels.get_shape_nodes(nodes)` *(static)* — Return the shape node name(s) for *nodes*.
  - `Channels.get_history_nodes(nodes)` *(static)* — Return the construction-history input node(s) for *nodes*.
  - `Channels.toggle_key_at_current_time(nodes, attr_name)` *(static)* — Set or remove a keyframe on *attr_name* for *nodes* at the current time.
  - `Channels.set_breakdown_key(nodes, attr_names)` *(static)* — Set a breakdown key on *attr_names* for all *nodes* at the current time.
  - `Channels.mute_attrs(nodes, attr_names)` *(static)* — Mute *attr_names* across all *nodes*.
  - `Channels.unmute_attrs(nodes, attr_names)` *(static)* — Unmute *attr_names* across all *nodes*.
  - `Channels.hide_attrs(nodes, attr_names)` *(static)* — Hide *attr_names* from the channel box.
  - `Channels.show_attrs(nodes, attr_names)` *(static)* — Show (unhide) *attr_names* in the channel box.
  - `Channels.lock_and_hide_attrs(nodes, attr_names)` *(static)* — Lock and hide *attr_names*.
  - `Channels.select_connections(nodes, attr_name)` *(static)* — Select the upstream node driving *attr_name* on the primary node.
  - `Channels.can_freeze_selection(cls, attr_names)` *(class)* — Test if *attr_names* maps to a clean group-level freeze.
  - `Channels.freeze_transforms(cls, nodes, attrs=None, store=True)` *(class)* — Freeze transforms on *nodes* under cumulative bake semantics.
  - `Channels.unfreeze_transforms(cls, nodes, attrs=None)` *(class)* — Restore previously stored transforms on *nodes*.
  - `Channels.has_unfreeze_info(nodes)` *(static)* — Return True when at least one of *nodes* has stored unfreeze data.

<a id="node_utils--attributes--channels--channels_slots"></a>
### `node_utils/attributes/channels/channels_slots.py`

UI slots for the Channels UI.

- **[`class ChannelsSlots`](mayatk/mayatk/node_utils/attributes/channels/channels_slots.py#L23)** — Switchboard slots for the Channels UI.
  - `ChannelsSlots.apply_launch_config(self, targets=None, filter=None, search=None)` — Configure the window from a :func:`launch` call.
  - `ChannelsSlots.header_init(self, widget)` — Populate the header menu with global actions.
  - `ChannelsSlots.show_create_menu(self, *args)` — Show the *Create Attribute* popup.
  - `ChannelsSlots.cmb000_init(self, widget)` — Populate filter combobox and wire its option_box invert action.
  - `ChannelsSlots.cmb000(self, index)` — Filter changed — refresh table.
  - `ChannelsSlots.tbl000_init(self, widget)` — One-time table setup: action columns, context menu, scriptJobs.
  - `ChannelsSlots.cleanup_scene_callbacks(self)` — Tear down every event subscription owned by this slots instance.

<a id="node_utils--data_nodes"></a>
### `node_utils/data_nodes.py`

- **[`class DataNodes`](mayatk/mayatk/node_utils/data_nodes.py#L13)** — Manages the two shared scene data nodes.
  - `DataNodes.ensure_internal()` *(static)* — Get or create the shared network node.
  - `DataNodes.ensure_export()` *(static)* — Get or create the shared FBX export transform.
  - `DataNodes.set_internal_string(attr: str, value: str) -> str` *(static)* — Write *value* to a plain string attr on ``data_internal`` (create if needed).
  - `DataNodes.get_internal_string(attr: str) -> Optional[str]` *(static)* — Return the string value of an internal-node channel, or ``None``.
  - `DataNodes.set_export_string(attr: str, value: str) -> Optional[str]` *(static)* — Write *value* to a plain string attr on the export node (create if needed).
  - `DataNodes.get_export_string(attr: str) -> Optional[str]` *(static)* — Return the string value of an export-node channel, or ``None``.
  - `DataNodes.dump(decode: bool = True) -> dict` *(static)* — Return every tool-authored channel on both data nodes.
  - `DataNodes.format_dump(decode: bool = True) -> str` *(static)* — Pretty-printed JSON of :meth:`dump`, or ``""`` when nothing is stored.

<a id="nurbs_utils--_nurbs_utils"></a>
### `nurbs_utils/_nurbs_utils.py`

- **[`class NurbsUtils(ptk.HelpMixin)`](mayatk/mayatk/nurbs_utils/_nurbs_utils.py#L21)**
  - `NurbsUtils.loft(cls, uniform=True, close=False, degree=3, autoReverse=False, sectionSpans=1, range_=False, polygon=True, reverseSurfaceNormals=True, angle_loft_between_two_curves=False, angleLoftSpans=6)` *(class)* — Create a loft between two selections.
  - `NurbsUtils.create_curve_between_two_objs(cls, start, end)` *(class)* — Create a bezier curve between starting and end object(s).
  - `NurbsUtils.duplicate_along_curve(path, start, count=6, geometry='Instancer')` *(static)* — Duplicate objects along a given curve using MASH.
  - `NurbsUtils.angle_loft_between_two_curves(cls, start, end, count=6, cleanup=False, uniform=1, close=0, autoReverse=0, degree=3, sectionSpans=1, range=0, polygon=1, reverseSurfaceNormals=0)` *(class)* — Perform a loft between two nurbs curves or polygon sets of edges (that will be extracted as curves).
  - `NurbsUtils.get_curve_length(cls, curve) -> float` *(class)* — World-space arc length of the given curve (transform or shape).
  - `NurbsUtils.get_arc_lengths(cls, curve, points) -> List[float]` *(class)* — Arc length along *curve* of the closest curve point to each given point.
  - `NurbsUtils.get_closest_cv(x, curves, tolerance=0.0)` *(static)* — Find the closest control vertex between the given vertices, CVs, or objects and each of the given c…
  - `NurbsUtils.get_cv_info(cls, c, returned_type='cv', filter_=[])` *(class)* — Get a dict containing CV's of the given curve(s) and their corresponding point positions (based on…
  - `NurbsUtils.getCrossProductOfCurves(cls, curves, normalize=1, values=False)` *(class)* — Get the cross product of two vectors using points derived from the given curves.

<a id="nurbs_utils--curve_to_tube"></a>
### `nurbs_utils/curve_to_tube.py`

Sweep a circular profile along NURBS curve(s) to build a tube.

- **[`class CurveToTube(ptk.LoggingMixin)`](mayatk/mayatk/nurbs_utils/curve_to_tube.py#L52)** — Extrude a circular profile along NURBS curve(s) to build a tube.
  - `CurveToTube.create(cls, curves, output_type: str = 'nurbs', radius: float = 1.0, sections: int = 8, path_divisions: int = 1, degree: int = 3, caps: bool = True, quads: bool = True, live: bool = False, cleanup: bool = True, name: str = 'tube') -> List[str]` *(class)* — Build a tube along each selected curve.
- **[`class CurveToTubeSlots(ptk.LoggingMixin)`](mayatk/mayatk/nurbs_utils/curve_to_tube.py#L699)** — Switchboard slot wiring for the Curve to Tube UI (hermetic preview).
  - `CurveToTubeSlots.header_init(self, widget)` — Configure header help text.
  - `CurveToTubeSlots.b001(self)` — Reset to Defaults.
  - `CurveToTubeSlots.perform_operation(self, objects, contract)` — Build the tube(s) from the selected curves (Preview entry point).

<a id="nurbs_utils--image_tracer"></a>
### `nurbs_utils/image_tracer.py`

- **[`class BluePencilMixin(object)`](mayatk/mayatk/nurbs_utils/image_tracer.py#L24)** — Mixin for handling Blue Pencil operations.
  - `BluePencilMixin.get_blue_pencil_curves(self)` — Converts active Blue Pencil strokes to NURBS curves.
- **[`class ImageTracer(BluePencilMixin)`](mayatk/mayatk/nurbs_utils/image_tracer.py#L107)** — A class to trace images into Maya NURBS curves and generate geometry.
  - `ImageTracer.trace_curves(self) -> List[str]` — Traces the image and returns a list of created NURBS curves.
  - `ImageTracer.create_mesh(self, curves: Optional[List[str]] = None, combine: bool = True, name: str = 'traced_mesh', group_output: bool = True) -> Union[str, List[str]]` — Creates a polygon mesh from the traced curves (positive space).
  - `ImageTracer.create_negative_space_mesh(self, curves: Optional[List[str]] = None, margin_scale: float = 0.1, name: str = 'negative_space_mesh', group_output: bool = True) -> Optional[str]` — Creates a mesh representing the negative space (plane with holes).
  - `ImageTracer.project_on_plane(self, curves: Optional[List[str]] = None, name: str = 'projected_curves', group_output: bool = True) -> Union[str, List[str], None]` — Projects curves onto a plane.
- **[`class ImageTracerSlots`](mayatk/mayatk/nurbs_utils/image_tracer.py#L395)** — UI slots for the Image Tracer tool.
  - `ImageTracerSlots.header_init(self, widget)` — Initialize the header widget.
  - `ImageTracerSlots.txt000_init(self, widget)`
  - `ImageTracerSlots.browse_image(self)`
  - `ImageTracerSlots.chk000(self, state)` — Use Blue Pencil
  - `ImageTracerSlots.b002(self)` — Trace the source image into curves.
  - `ImageTracerSlots.b003(self)` — Build a mesh from the traced curves.
  - `ImageTracerSlots.b004(self)` — Build a mesh from the traced negative space.
  - `ImageTracerSlots.b005(self)` — Project the traced result onto a plane.

<a id="render_utils--_render_utils"></a>
### `render_utils/_render_utils.py`

Render-control helpers.

- **[`class RenderUtils(ptk.HelpMixin)`](mayatk/mayatk/render_utils/_render_utils.py#L28)** — Renderer enumeration / selection and Render-View control.
  - `RenderUtils.get_available_renderers(cls) -> List[Dict[str, object]]` *(class)* — Renderers the user can pick.
  - `RenderUtils.current_renderer() -> str` *(static)* — The scene's active renderer (``defaultRenderGlobals.currentRenderer``).
  - `RenderUtils.set_renderer(cls, name: str) -> None` *(class)* — Make *name* the active renderer, loading its plugin if required.
  - `RenderUtils.render_camera(camera: str, render_mode: str = 'render') -> None` *(static)* — Render *camera* into the Render View, opening it if needed.
  - `RenderUtils.redo_previous_render(render_mode: str = 'render') -> None` *(static)* — Re-render the last render with its previous settings (fast path).
  - `RenderUtils.supports_ipr(cls, renderer: Optional[str] = None) -> bool` *(class)* — True if *renderer* can start an interactive (IPR) session.
  - `RenderUtils.start_ipr(cls, camera: str, renderer: Optional[str] = None) -> bool` *(class)* — Launch interactive (IPR) realtime rendering for *renderer*.

<a id="rig_utils--_rig_utils"></a>
### `rig_utils/_rig_utils.py`

- **[`class RigUtils(ptk.HelpMixin)`](mayatk/mayatk/rig_utils/_rig_utils.py#L19)**
  - `RigUtils.create_helper(name: str, helper_type: str = 'locator', parent: Optional[str] = None, position: Tuple[float, float, float] = (0.0, 0.0, 0.0), cleanup: bool = False) -> Optional[str]` *(static)* — Create a hidden helper object (e.g., locator, joint) with a consistent naming convention.
  - `RigUtils.create_group(objects=[], name='', zero_translation=False, zero_rotation=False, zero_scale=False)` *(static)* — Create a group containing any given objects.
  - `RigUtils.create_locator(*, scale: float = 1, parent: Optional[str] = None, **kwargs) -> str` *(static)* — Create a locator with the given scale.
  - `RigUtils.create_locator_at_object(cls, objects: Union[str, List[str]], parent: bool = True, freeze_object: bool = True, freeze_locator: bool = True, loc_scale: float = 1.0, lock_translate: bool = False, lock_rotation: bool = False, lock_scale: bool = False, grp_suffix: str = '_GRP', loc_suffix: str = '_LOC', obj_suffix: str = '_GEO', strip_digits: bool = False, strip_trailing_underscores: bool = True, strip_suffix: bool = True) -> None` *(class)* — Rig object under a zeroed locator aligned to its d manip pivot.
  - `RigUtils.remove_locator(cls, objects)` *(class)* — Remove a parented locator from the child object.
  - `RigUtils.restore_rig_anchors(cls, objects, traverse: bool = True, skip_animated: bool = True, pivot_source: str = 'bbox') -> List[str]` *(class)* — Restore the world-space anchor on a GRP > LOC > GEO rig after a freeze.
  - `RigUtils.connect_switch_to_constraint(cls, constraint_node: str, constraint_targets: Optional[List[str]] = None, attr_name: str = 'parent_switch', overwrite_existing: bool = False, node: Optional[str] = None, weighted: bool = False, anchor: Optional[str] = None) -> dict` *(class)* — Create a space switch attribute to drive a constraint node.
  - `RigUtils.create_ik_handle(start_joint: str, end_joint: str, solver: str = 'ikRPsolver', name: str = 'ikHandle', parent: Optional[str] = None, **kwargs) -> str` *(static)* — Create an IK handle.
  - `RigUtils.create_pole_vector(ik_handle: str, mid_joint: str, distance: float = 5.0, name: str = 'poleVector_LOC', parent: Optional[str] = None) -> str` *(static)* — Create a pole vector locator based on the mid joint's position.
  - `RigUtils.get_ik_handles_for_joint(joint: str) -> List[str]` *(static)* — Find IK handles that control a given joint.
  - `RigUtils.joint_in_ik_chain(joint: str, start_joint: str, end_joint: str) -> bool` *(static)* — Check if a joint is part of an IK chain between start and end.
  - `RigUtils.get_joint_chain_from_root(root_joint: Union[str, List[str]], reverse: bool = False) -> List[str]` *(static)* — Get the joint chain from the root joint or the first joint in the list if more than one joint is gi…
  - `RigUtils.invert_joint_chain(root_joint, keep_original=False)` *(static)* — Create a new joint chain with the same positions as the original, but with reversed hierarchy.
  - `RigUtils.rebind_skin_clusters(cls, meshes: Optional[List[str]] = None, temp_dir: Optional[str] = None, inherits_transform: Optional[bool] = None) -> Dict[str, list]` *(class)* — Rebinds skinClusters on the given meshes, preserving weights, bind pose, and transform lock state.

<a id="rig_utils--controls"></a>
### `rig_utils/controls.py`

- **[`class ControlNodes`](mayatk/mayatk/rig_utils/controls.py#L22)**
- **[`class Controls(ptk.HelpMixin)`](mayatk/mayatk/rig_utils/controls.py#L58)** — Factory for creating NURBS animation controls.
  - `Controls.register_preset(cls, name: str, builder: Callable[..., str]) -> None` *(class)* — Register a new control preset.
  - `Controls.shapes(cls) -> List[str]` *(class)* — Sorted names of the registered presets (for a UI combo / validation).
  - `Controls.create(cls, preset: str = 'diamond', name: Optional[str] = None, *, size: float = 1.0, axis: str = 'y', match: Any = None, parent: Optional[str] = None, color: Union[int, Tuple[float, float, float], None] = None, offset_group: bool = True, group_suffix: str = '_GRP', ctrl_suffix: str = '_CTRL', freeze: bool = True, tag_as_controller: bool = True, return_nodes: bool = False, **kwargs) -> Union[str, ControlNodes]` *(class)* — Create a NURBS control.
  - `Controls.combine(cls, controls: Iterable[Any], name: Optional[str] = None, *, parent: Optional[str] = None, match: Any = None, color: Union[int, Tuple[float, float, float], None] = None, delete_sources: bool = True, ctrl_suffix: str = '_CTRL') -> str` *(class)* — Combine multiple control transforms into a single selectable transform.

<a id="rig_utils--shadow_rig"></a>
### `rig_utils/shadow_rig.py`

- **[`class ShadowRig(ptk.LoggingMixin)`](mayatk/mayatk/rig_utils/shadow_rig.py#L21)** — Projected shadow for Unity export.
  - `ShadowRig.create_contact_locator(self)` — Create a locator at the lowest point of the combined objects to act as the shadow anchor.
  - `ShadowRig.get_or_create_shadow_source(self, position=(5, 10, 5), source_name='shadow_source')` — Get existing shadow source or create a new one.
  - `ShadowRig.create_shadow_plane(self)` — Create a simple quad for the shadow with the keyable shadow attrs.
  - `ShadowRig.create_silhouette_texture(self, size=512, axis='auto', recursive=True, *, uniform_alpha=False, falloff_source=None, falloff_power=0.8, vertical_weight=0.3, blur_amount=1.5)` — Create the silhouette texture via ``pythontk.ImgUtils.rasterize_silhouette``.
  - `ShadowRig.create_material(self, shader_type='stingray', stingray_opacity_mode='transparent')` — Create material with the silhouette texture.
  - `ShadowRig.setup_expression(self)` — Create expression to warp shadow based on light position.
  - `ShadowRig.bake(self, start=None, end=None)` — Bake this rig's driven channels to keyframes and remove the live
  - `ShadowRig.refresh_export_metadata(cls)` *(class)* — Republish the ``shadow_metadata`` channel on the ``data_export``
  - `ShadowRig.find_shadow_planes(cls, nodes=None)` *(class)* — Shadow planes = transforms carrying the stamped ``basePlaneSize``
  - `ShadowRig.bake_planes(cls, planes=None, start=None, end=None)` *(class)* — Bake shadow planes' expression-driven channels to keyframes and
  - `ShadowRig.delete(self, delete_textures=False)` — Delete this rig completely.
  - `ShadowRig.delete_rigs(cls, planes=None, delete_textures=False)` *(class)* — Tear down shadow rig(s) completely — live or baked.
  - `ShadowRig.create(cls, targets, light_pos=(5, 10, 5), texture_res=512, axis='auto', source_name='shadow_source', recursive=True, mode='stretch', ground_height=0.0)` *(class)* — Create a projected shadow for Unity export.
- **[`class ShadowRigSlots`](mayatk/mayatk/rig_utils/shadow_rig.py#L1124)**
  - `ShadowRigSlots.header_init(self, widget)` — Configure header help text.
  - `ShadowRigSlots.b001(self)` — Reset to Defaults: Resets all UI widgets to their default values.
  - `ShadowRigSlots.b002(self)` — Bake to Keyframes: bake selected (or all) shadow planes' expressions
  - `ShadowRigSlots.perform_operation(self, objects, contract)` — Build the shadow rig for the given targets.

<a id="rig_utils--skinning"></a>
### `rig_utils/skinning.py`

Skinning utilities: binding, batch weight I/O, transfer, procedural weights.

- **[`class CurveWeights(ptk.HelpMixin)`](mayatk/mayatk/rig_utils/skinning.py#L44)** — Analytic, ring-uniform skin weights for a joint chain along a curve.
  - `CurveWeights.effective_degree(degree: int, num_joints: int) -> int` *(static)* — The basis degree actually solvable: *degree* clamped to [1, num_joints - 1].
  - `CurveWeights.joint_stations(cls, joints: List[str], curve) -> List[float]` *(class)* — Arc length of each joint's closest curve point, in input order.
  - `CurveWeights.solve(cls, mesh, joints: List[str], curve: Optional[str] = None, centerline: Optional[Sequence] = None, profile: Union[str, Callable] = 'smoothstep', degree: int = 3) -> Tuple[List[float], List[str]]` *(class)* — Compute per-vertex weights from arc-length stations along a curve.
- **[`class SkinUtils(ptk.HelpMixin)`](mayatk/mayatk/rig_utils/skinning.py#L189)** — Skinning: binding, batch weight I/O, transfer, falloffs, delta mush.
  - `SkinUtils.get_skin_cluster(mesh) -> Optional[str]` *(static)* — Return the first skinCluster in the mesh's history, or None.
  - `SkinUtils.get_influences(cls, skin_cluster, long_names: bool = False) -> List[str]` *(class)* — Influence names in PHYSICAL order (``MFnSkinCluster.influenceObjects()``).
  - `SkinUtils.bind(cls, mesh, joints, bind_method: str = 'closest', skinning_method: str = 'classic', max_influences: int = 4, dropoff_rate: float = 4.0, weight_distribution: float = 0.5, remove_unused_influences: bool = False, heatmap_falloff: float = 0.68, bind_fallback: bool = True, name: Optional[str] = None) -> str` *(class)* — Smooth-bind *mesh* to *joints* with the full skinCluster arg surface.
  - `SkinUtils.name_bind_pose(skin_cluster, name: str) -> Optional[str]` *(static)* — Rename *skin_cluster*'s dagPose to *name*.
  - `SkinUtils.unbind(cls, mesh) -> bool` *(class)* — Remove the mesh's skinCluster (restores the pre-bind shape).
  - `SkinUtils.get_weights(cls, skin_cluster, vertices: Optional[Sequence[int]] = None) -> Tuple[List[float], List[str]]` *(class)* — Read weights in one batched API call.
  - `SkinUtils.set_weights(cls, skin_cluster, weights: Sequence[float], influences: Optional[List[str]] = None, vertices: Optional[Sequence[int]] = None, normalize: bool = True, undoable: bool = False) -> List[float]` *(class)* — Write weights in one batched call.
  - `SkinUtils.set_vertex_weights(cls, skin_cluster, vertex_weights: Dict[int, Dict[str, float]], undoable: bool = True) -> None` *(class)* — Sparse per-vertex write with skinPercent semantics.
  - `SkinUtils.prune_weights(cls, skin_cluster, below: float = 0.001) -> None` *(class)* — Zero weights below the threshold and renormalize.
  - `SkinUtils.normalize_weights(cls, skin_cluster) -> None` *(class)* — Normalize all weights to sum 1 per vertex.
  - `SkinUtils.set_max_influences(cls, skin_cluster, max_influences: int, enforce: bool = True) -> None` *(class)* — Set the influence cap;
  - `SkinUtils.set_skinning_method(cls, skin_cluster, method: str = 'dqs') -> None` *(class)* — Set the blend method: "classic" | "dqs" | "blended".
  - `SkinUtils.copy_weights(cls, source_mesh, target_mesh, surface_association: str = 'closestPoint', influence_association: Sequence[str] = ('label', 'oneToOne', 'closestJoint'), bind_target_if_needed: bool = True) -> str` *(class)* — Copy skin weights between meshes;
  - `SkinUtils.mirror_weights(cls, mesh, axis: str = 'YZ', positive_to_negative: bool = True, surface_association: str = 'closestPoint', influence_association: Sequence[str] = ('label', 'closestJoint', 'oneToOne')) -> None` *(class)* — Mirror weights across a plane ("YZ" | "XY" | "XZ") on the same mesh.
  - `SkinUtils.export_weights(cls, mesh, file_path: Optional[str] = None) -> str` *(class)* — Export skin weights to XML (cmds.deformerWeights).
  - `SkinUtils.import_weights(cls, mesh, file_path: str, method: str = 'index') -> None` *(class)* — Import skin weights from XML and renormalize.
  - `SkinUtils.apply_falloff(cls, skin_cluster, target_influence, center, radius: float = 5.0, profile: Union[str, Callable] = 'linear', source_influence: Optional[str] = None, add_influence: bool = True, undoable: bool = True) -> int` *(class)* — Distance-based weight falloff around *center*.
  - `SkinUtils.add_delta_mush(cls, mesh, smoothing_iterations: int = 10, smoothing_step: float = 0.5, pin_border_vertices: bool = True, name: Optional[str] = None) -> str` *(class)* — Add a deltaMush finishing pass (softens residual skinning artifacts).
  - `SkinUtils.bind_to_curve(cls, mesh, joints, curve: Optional[str] = None, centerline: Optional[Sequence] = None, profile: Union[str, Callable] = 'smoothstep', degree: int = 3, skinning_method: str = 'dqs', max_influences: Optional[int] = None, name: Optional[str] = None, **bind_kwargs) -> str` *(class)* — One-call precision bind for tube-like meshes.

<a id="rig_utils--telescope_rig"></a>
### `rig_utils/telescope_rig.py`

- **[`class TelescopeRigBundle`](mayatk/mayatk/rig_utils/telescope_rig.py#L20)** — Record of everything one ``setup_telescope_rig`` build created.
  - `TelescopeRigBundle.to_json(self) -> str`
  - `TelescopeRigBundle.from_json(cls, payload: str) -> 'TelescopeRigBundle'` *(class)* — Rebuild a bundle from :meth:`to_json` output, ignoring unknown keys
- **[`class TelescopeRig(ptk.LoggingMixin)`](mayatk/mayatk/rig_utils/telescope_rig.py#L56)** — Telescope Rig
  - `TelescopeRig.setup_telescope_rig(self, base_locator: Optional[Union[str, List[str]]] = None, end_locator: Optional[Union[str, List[str]]] = None, segments: Optional[List[str]] = None, collapsed_distance: Optional[float] = None, aim_axis: str = 'y', world_up_type: str = 'scene', lock_attributes: bool = True, name: str = 'telescope') -> TelescopeRigBundle` — Sets up constraints and driven keys to make a series of segments telescope between two locators.
  - `TelescopeRig.scene_bundles(cls) -> List[TelescopeRigBundle]` *(class)* — Every telescope-rig bundle stamped into the current scene.
  - `TelescopeRig.find_bundles(cls, nodes) -> List[TelescopeRigBundle]` *(class)* — Bundles whose locators or segments intersect *nodes*.
  - `TelescopeRig.teardown(self, bundle: Optional[TelescopeRigBundle] = None) -> bool` — Remove a telescope rig built by this class.
- **[`class TelescopeRigSlots(ptk.LoggingMixin)`](mayatk/mayatk/rig_utils/telescope_rig.py#L778)**
  - `TelescopeRigSlots.header_init(self, widget)` — Configure header help text.
  - `TelescopeRigSlots.build_rig(self)`
  - `TelescopeRigSlots.remove_rig(self)`

<a id="rig_utils--tube_rig"></a>
### `rig_utils/tube_rig.py`

- **[`class TubePath`](mayatk/mayatk/rig_utils/tube_rig.py#L27)** — Pure geometry analysis for tube-like meshes.
  - `TubePath.get_centerline(mesh, num_joints: int = 10, precision: int = 10, edges: list = None, use_surface_normals: bool = True) -> Tuple[List, int]` *(static)* — Unified centerline dispatcher — picks the best algorithm.
  - `TubePath.get_edge_loop_centers(mesh) -> Tuple[List[om.MPoint], int]` *(static)* — Extract centerline by finding all edge loops (cross-sections) of a tube mesh.
  - `TubePath.estimate_radius(mesh, centerline: List) -> Optional[float]` *(static)* — Estimate the tube's radius: median distance from interior
  - `TubePath.get_centerline_using_edges(edge_selection: List[str]) -> List[List[float]]` *(static)* — Derive centerline points from selected edges of the tube.
  - `TubePath.get_centerline_from_surface_normals(mesh, num_points: int = 10, iterations: int = 3) -> List[om.MPoint]` *(static)* — Calculate centerline by iteratively averaging opposing surface hits.
  - `TubePath.get_centerline_from_bounding_box(obj, precision=10, smooth=False, window_size=1)` *(static)* — Calculate the centerline of an object using the cross-section of its largest bounding box axis.
- **[`class TubeRigBundle`](mayatk/mayatk/rig_utils/tube_rig.py#L636)**
- **[`class TubeStrategy(ABC)`](mayatk/mayatk/rig_utils/tube_rig.py#L650)**
  - `TubeStrategy.build(self, rig: 'TubeRig', **kwargs) -> TubeRigBundle`
- **[`class FKChainStrategy(TubeStrategy)`](mayatk/mayatk/rig_utils/tube_rig.py#L656)** — Joints → nested FK controls → parametric skin.
  - `FKChainStrategy.build(self, rig: 'TubeRig', **kwargs) -> TubeRigBundle`
- **[`class SplineIKStrategy(TubeStrategy)`](mayatk/mayatk/rig_utils/tube_rig.py#L683)** — Joints → spline-IK control rig → parametric skin along the IK curve.
  - `SplineIKStrategy.build(self, rig: 'TubeRig', **kwargs) -> TubeRigBundle`
- **[`class AnchorStrategy(TubeStrategy)`](mayatk/mayatk/rig_utils/tube_rig.py#L723)** — Two end joints → anchor controls with distance stretch → parametric skin.
  - `AnchorStrategy.build(self, rig: 'TubeRig', **kwargs) -> TubeRigBundle`
- **[`class TubeRig(ptk.LoggingMixin, _TubeRigInternal)`](mayatk/mayatk/rig_utils/tube_rig.py#L906)** — Rig engine for tube-shaped meshes: joints, IK, controls, skinning.
  - `TubeRig.for_mesh(cls, mesh) -> Optional['TubeRig']` *(class)* — Look up an existing TubeRig instance bound to *mesh*, or return None.
  - `TubeRig.for_node(cls, node) -> Optional['TubeRig']` *(class)* — Find the TubeRig owning *node* — the rigged mesh itself, or
  - `TubeRig.rig_name(self) -> str` *(property)* — Returns the rig name.
  - `TubeRig.rig_group(self) -> str` *(property)*
  - `TubeRig.teardown(self) -> None` — Delete everything a previous ``build`` created — the rig group and
  - `TubeRig.build(self, strategy: str = 'spline', **kwargs)` — Builds the rig using the specified strategy.
  - `TubeRig.resolve_centerline(self, num_joints: int = -1, edges: list = None) -> Tuple[List, int]` — Extract this rig's tube centerline.
  - `TubeRig.estimate_tube_radius(self, centerline: List = None) -> Optional[float]` — Measure the tube's radius from the mesh surface.
  - `TubeRig.resolve_sizes(self, centerline: List = None, joint_radius: float = -1.0) -> Tuple[float, float]` — Resolve (joint_display_radius, control_base_size) from the tube.
  - `TubeRig.generate_joint_chain(self, centerline: List[List[float]], num_joints: int, reverse: bool = False, **kwargs) -> List[str]` — Generates joints along the tube's centerline.
  - `TubeRig.create_anchor_joints(self, centerline: List, radius: float = 1.0) -> List[str]` — Create the anchor rig's two end joints from the tube centerline.
  - `TubeRig.skin_mesh(self, joints: List[str], curve: Optional[str] = None, centerline: Optional[List] = None, skinning_method: str = 'dqs', mesh: Optional[str] = None) -> Optional[str]` — Smooth-bind the mesh to *joints* and record the skinCluster
  - `TubeRig.create_logic_curve(self, centerline: List[List[float]]) -> str` — Creates the logic curve for Spline IK.
  - `TubeRig.create_spline_drivers(self, centerline: List[List[float]], radius: float = 1.0, num_controls: int = 3) -> Tuple[List[str], List[str], List]` — Creates the driver system (controls and joints) for the Spline IK curve.
  - `TubeRig.skin_curve_to_drivers(self, curve, driver_joints) -> Optional[str]` — Bind the IK logic curve to the driver joints.
  - `TubeRig.create_spline_controls(self, joints: List[str], centerline: Optional[List] = None, size: float = 1.0, num_controls: int = 3, enable_stretch: bool = True, enable_squash: bool = True, enable_volume: bool = True, enable_twist: bool = True, enable_auto_bend: bool = False) -> Tuple[List[str], str, str]` — Build the complete spline-IK control rig over an existing joint chain:
  - `TubeRig.create_fk_controls(self, joints: List[str], size: float = 1.0) -> List[str]` — Build a nested FK control hierarchy — one diamond per joint, each
  - `TubeRig.create_anchor_controls(self, joints: List[str], size: float = 1.0, enable_stretch: bool = True) -> List[str]` — Build the anchor/piston controls over the two end joints
  - `TubeRig.setup_spline_twist(self, ik_handle, start_ctrl, end_ctrl, start_up_loc=None, end_up_loc=None)` — Setup advanced twist for IK Spline.
  - `TubeRig.setup_auto_bend(self, start_ctrl, mid_ctrl, end_ctrl)` — Setup automatic bending of the mid control based on compression
  - `TubeRig.setup_spline_stretch(self, curve, joints, enable_stretch=True, enable_squash=True, enable_volume=True, main_control=None)`
  - `TubeRig.create_ik(self, joints: List[str], **kwargs) -> Optional[str]`
  - `TubeRig.create_pole_vector(self, ik_handle, mid_joint: str, offset=(0, 5, 0)) -> str`
  - `TubeRig.bind_joint_chain(self, obj, joints: List[str], curve: Optional[str] = None, centerline: Optional[List] = None) -> Optional[str]` — Bind the joint chain to a polygon tube with smooth skinning.
  - `TubeRig.constrain_end_with_falloff(self, joints: 'List[str]', anchor: str, falloff: float = 5.0, joint_index: int = -1, profile: Union[str, Callable] = 'smoothstep') -> 'Optional[str]'` — Constrains a joint in the chain to an anchor and applies distance-based skin weight falloff.
- **[`class RigModeConfig`](mayatk/mayatk/rig_utils/tube_rig.py#L2671)** — Defines a rig mode's strategy and available options.
- **[`class TubeRigSlots`](mayatk/mayatk/rig_utils/tube_rig.py#L2750)**
  - `TubeRigSlots.txt000_init(self, widget)` — Rig-name field — optional, so clearing back to auto-naming is a state.
  - `TubeRigSlots.header_init(self, widget)` — Configure header help text.
  - `TubeRigSlots.apply_mode(self, index: int)` — Apply mode values and constraints to UI widgets.
  - `TubeRigSlots.get_mode(self) -> RigModeConfig` — Get the current rig mode config.
  - `TubeRigSlots.get_strategy(self) -> str` — Get the current strategy from the mode combobox.
  - `TubeRigSlots.get_tube_rig(self, obj)` — Get the tube rig instance for the given object (the mesh, a joint,
  - `TubeRigSlots.create_joints_from_tube(self, obj)` — Step 1 — create this rig's joints from the tube mesh (mode-aware).
  - `TubeRigSlots.b000(self)` — One-Click Rig — runs Steps 1 → 2 → 3 with the step parameters.
  - `TubeRigSlots.b001(self)` — Step 1: Create Joints from Tube.
  - `TubeRigSlots.b002(self)` — Step 2: Create IK / Controls (mode dependent).
  - `TubeRigSlots.b003(self)` — Step 3: Bind Joint Chain to Tube.
  - `TubeRigSlots.b004(self)` — Utility: Constrain Both Ends of Hose to Anchors.

<a id="rig_utils--wheel_rig"></a>
### `rig_utils/wheel_rig.py`

- **[`class WheelRig(ptk.LoggingMixin)`](mayatk/mayatk/rig_utils/wheel_rig.py#L21)** — Handles basic wheel rigging by linking rotation to linear control movement.
  - `WheelRig.rig_name(self) -> str` *(property)*
  - `WheelRig.get_expressions(self, filter_by_rig: bool = False) -> List[object]` — Return all expression nodes connected to the control.
  - `WheelRig.delete_expressions(self, filter_by_rig: bool = True) -> None` — Delete expression nodes associated with this rig.
  - `WheelRig.rig_rotation(self, movement_axis: str = 'translateZ', rotation_axis: Optional[str] = None, wheel_height: float = 1.0, wheels: Optional[List['object']] = None, use_world_space: bool = False) -> None` — Rig wheels to rotate based on control movement.
- **[`class WheelRigSlots`](mayatk/mayatk/rig_utils/wheel_rig.py#L313)**
  - `WheelRigSlots.header_init(self, widget)` — Configure header menu with mode toggle and instructions.
  - `WheelRigSlots.rig_name(self) -> str` *(property)* — Get the rig name from the text box.
  - `WheelRigSlots.movement_axis(self) -> str` *(property)* — Get the movement axis from the combo box.
  - `WheelRigSlots.rotation_axis(self) -> Optional[str]` *(property)* — Get the rotation axis that corresponds to the selected movement axis.
  - `WheelRigSlots.resolve_selection(self) -> Tuple['object', List['object']]` — Resolve the current selection into control (driver) and wheels.
  - `WheelRigSlots.set_wheel_height(self)` — Get the wheel height from the selected object's bounding box.
  - `WheelRigSlots.txt000_init(self, widget)` — Rig-name field — optional, so clearing back to auto-naming is a state.
  - `WheelRigSlots.s000_init(self, widget)` — Initialize the wheel height slider.
  - `WheelRigSlots.update_rig_name_placeholder(self)` — Update the rig name placeholder based on the driver (last selected).
  - `WheelRigSlots.cleanup(self)` — Unsubscribe from the centralized ScriptJobManager.
  - `WheelRigSlots.wheel_rig(self) -> Optional[WheelRig]` *(property)* — Get or create the wheel rig attached to the selected control.
  - `WheelRigSlots.b000(self)` — Create or update Wheel Rig.

<a id="ui_utils--_ui_utils"></a>
### `ui_utils/_ui_utils.py`

- **[`class UiUtils`](mayatk/mayatk/ui_utils/_ui_utils.py#L8)**
  - `UiUtils.get_main_window()` *(static)* — Get the main Maya window as a QMainWindow instance.
  - `UiUtils.get_menu_name(qt_object_name: str) -> Optional[str]` *(static)* — Retrieve the internal Maya name of a menu given its Qt object name.
  - `UiUtils.get_panel(*args, **kwargs)` *(static)* — Returns panel and panel configuration information.
  - `UiUtils.get_model_panel(with_focus: bool = True) -> Optional[str]` *(static)* — Return a 3D model panel (viewport), suitable for commands like isolateSelect.
  - `UiUtils.main_progress_bar(size, name='progressBar#', step_amount=1)` *(static)* — # add esc key pressed return False
  - `UiUtils.list_ui_objects()` *(static)* — List all UI objects.
  - `UiUtils.clear_scrollfield_reporters()` *(static)* — Clears the contents of all cmdScrollFieldReporter UI objects in the current Maya session.
  - `UiUtils.reveal_in_outliner(objects)` *(static)* — Reveal and select objects in the Outliner panel.
  - `UiUtils.dispatch_log_link(url, logger=None) -> bool` *(static)* — Handle ``action://`` links emitted by ``log_link()`` in a QTextBrowser.

<a id="ui_utils--calculator"></a>
### `ui_utils/calculator.py`

- **[`class CalculatorController`](mayatk/mayatk/ui_utils/calculator.py#L11)**
  - `CalculatorController.calculate(expression)` *(static)* — Safely evaluate a math expression (delegates to the shared engine).
  - `CalculatorController.get_fps_value()` *(static)*
  - `CalculatorController.get_current_time()` *(static)*
  - `CalculatorController.frames_to_sec(cls, frames)` *(class)*
  - `CalculatorController.sec_to_frames(cls, seconds)` *(class)*
  - `CalculatorController.convert_unit(value, from_unit, to_unit)` *(static)* — Convert a length value between units (delegates to the shared engine).
- **[`class CalculatorSlots`](mayatk/mayatk/ui_utils/calculator.py#L71)**
  - `CalculatorSlots.header_init(self, widget)` — Configure header help text.
  - `CalculatorSlots.on_convert_units(self)`
  - `CalculatorSlots.on_input(self, text)`
  - `CalculatorSlots.on_clear(self)`
  - `CalculatorSlots.on_backspace(self)`
  - `CalculatorSlots.on_equal(self)`
  - `CalculatorSlots.get_fps(self)`
  - `CalculatorSlots.get_current_time(self)`
  - `CalculatorSlots.frames_to_sec(self)`
  - `CalculatorSlots.sec_to_frames(self)`

<a id="ui_utils--channel_box"></a>
### `ui_utils/channel_box.py`

Programmatic access to Maya's Channel Box.

- **[`class ChannelBox`](mayatk/mayatk/ui_utils/channel_box.py#L29)** — Query, select, and hook into Maya's Channel Box programmatically.
  - `ChannelBox.connect_selection_changed(cls, callback)` *(class)* — Connect *callback* to the Channel Box's Qt selection signal.
  - `ChannelBox.disconnect_selection_changed(cls, callback)` *(class)* — Disconnect a previously connected *callback*.
  - `ChannelBox.get_selected_attrs(cls, sections='all')` *(class)* — Return attribute names currently selected in the channel box.
  - `ChannelBox.get_selected_objects(cls, sections='all')` *(class)* — Return the object names associated with selected channel box attrs.
  - `ChannelBox.get_selected_plugs(cls, sections='all')` *(class)* — Return fully qualified ``node.attr`` plugs for the current selection.
  - `ChannelBox.select(cls, attr_names)` *(class)* — Select attributes in the channel box by short name.
  - `ChannelBox.select_visual(cls, attr_names)` *(class)* — Select attributes and ensure the highlight is visible in the UI.
  - `ChannelBox.clear_selection(cls)` *(class)* — Deselect all attributes in the channel box.
  - `ChannelBox.get_all_attrs(cls, node=None, section='main')` *(class)* — Return *all* attribute names shown in a channel box section.
  - `ChannelBox.get_attr_properties(cls, node=None, attrs=None)` *(class)* — Get detailed properties for channel box attributes.
  - `ChannelBox.watch_selection(cls, callback)` *(class)* — Register a callback that fires when channel box selection changes.
  - `ChannelBox.unwatch_selection(cls, callback=None)` *(class)* — Remove a selection watcher.
  - `ChannelBox.get_context_menu_actions(cls)` *(class)* — Extract all QAction items from the channel box's context menus.
  - `ChannelBox.snapshot(cls, max_depth=4)` *(class)* — Capture the full Qt state of the channel box widget tree.
  - `ChannelBox.diff(cls, before, after=None)` *(class)* — Compare two channel box snapshots.
  - `ChannelBox.list_mel_procs(cls, pattern='channel[Bb]ox')` *(class)* — Find MEL procedures related to the channel box.
  - `ChannelBox.read_mel_proc(cls, proc_name)` *(class)* — Read the full source of a channel-box-related MEL procedure.
  - `ChannelBox.dump_tree(cls, max_depth=3)` *(class)* — Print the Qt widget tree inside the channel box.
  - `ChannelBox.dump_model(cls, max_rows=50)` *(class)* — Print the item-model contents of the main channel box view.
  - `ChannelBox.list_signals(cls)` *(class)* — List signals on the channel box widget.
  - `ChannelBox.list_item_views(cls)` *(class)* — List all QAbstractItemView children (main, shape, history, output).

<a id="ui_utils--hotkey_collisions"></a>
### `ui_utils/hotkey_collisions.py`

Maya hotkey collision checker for the uitk ShortcutEditor.

- **[`class HotkeyCollisions(_HotkeyCollisionsInternal)`](mayatk/mayatk/ui_utils/hotkey_collisions.py#L222)** — HotkeyCollisions — module namespace.
  - `HotkeyCollisions.parse_qt_sequence(sequence: str) -> Optional[dict]` *(static)* — Convert a Qt key sequence string to ``cmds.hotkey`` query kwargs.
  - `HotkeyCollisions.keystring_to_token(ks: list) -> str` *(static)* — Convert an ``assignCommand`` keyString array to a Maya hotkey token.
  - `HotkeyCollisions.live_hotkey_map() -> dict` *(static)* — Return ``{runtime_command: maya_key_token}`` for the active hotkey set.
  - `HotkeyCollisions.ensure_editable_hotkey_set(name: str = MACRO_HOTKEY_SET) -> str` *(static)* — Make the *current* hotkey set editable;
  - `HotkeyCollisions.maya_collision_checker(sequence, scope, ui_name, method_name, ignore=None)` *(static)* — Check a proposed binding against Maya's active hotkey set.

<a id="ui_utils--maya_bridge_slots_base"></a>
### `ui_utils/maya_bridge_slots_base.py`

Maya-flavored :class:`BridgeSlotsBase` -- adds Maya-side defaults.

- **[`class MayaBridgeSlotsBase(BridgeSlotsBase)`](mayatk/mayatk/ui_utils/maya_bridge_slots_base.py#L24)** — Adds a Maya-flavored ``default_output_dir`` + Scope resolution to
  - `MayaBridgeSlotsBase.default_output_dir(self) -> str` — Scene-dir then workspace fallback for an empty Output Dir field.
  - `MayaBridgeSlotsBase.resolve_scope_objects(self, scope: str)` — Objects to export for the chosen ``SCOPE`` param.

<a id="ui_utils--maya_native_menus"></a>
### `ui_utils/maya_native_menus.py`

- **[`class MayaNativeMenus(ptk.LoggingMixin)`](mayatk/mayatk/ui_utils/maya_native_menus.py#L19)** — Handles Maya's menu retrieval and embedding into UI components.
  - `MayaNativeMenus.get_menu(self, menu_key: str) -> Optional[QtWidgets.QWidget]` — Retrieve a Maya menu, populated synchronously, and return its wrapper.
  - `MayaNativeMenus.display_menu(self, menu_key: str)` — Displays the specified Maya menu in a standalone window.

<a id="ui_utils--maya_ui_handler"></a>
### `ui_utils/maya_ui_handler.py`

- **[`class MayaUiHandler(UiHandler)`](mayatk/mayatk/ui_utils/maya_ui_handler.py#L18)** — UI Handler for Maya applications.
  - `MayaUiHandler.instance(cls, switchboard: Switchboard = None, **kwargs) -> 'MayaUiHandler'` *(class)* — Return the MayaUiHandler singleton, bootstrapping if needed.
  - `MayaUiHandler.can_resolve(self, name: str) -> bool` — Recognise the native Maya menus this handler builds on demand.
  - `MayaUiHandler.get(self, name: str, reload: bool = False, **kwargs) -> 'QtWidgets.QMainWindow'` — Retrieve a UI, checking Maya menus first.
  - `MayaUiHandler.default_persistence(self, ui) -> str` — mayatk-sourced UIs stay open by default (hide button, not pin).

<a id="ui_utils--node_icons"></a>
### `ui_utils/node_icons.py`

Reusable helper for resolving Maya node icons at runtime.

- **[`class NodeIcons`](mayatk/mayatk/ui_utils/node_icons.py#L23)** — Resolve Maya node type icons as Qt QIcons.
  - `NodeIcons.icon_name_for_type(node_type: str) -> str` *(static)* — Return the Maya resource icon filename for a given node type.
  - `NodeIcons.icon_name_for_node(node_name: str) -> Optional[str]` *(static)* — Return the icon filename for a specific node in the scene.
  - `NodeIcons.get_icon(node_name: str, size: int = 20)` *(static)* — Return a ``QIcon`` for a Maya node, or ``None`` if unavailable.
  - `NodeIcons.get_pixmap(node_name: str, size: int = 16)` *(static)* — Return a ``QPixmap`` for a Maya node, scaled to *size*.

<a id="ui_utils--style_setter--_style_setter"></a>
### `ui_utils/style_setter/_style_setter.py`

Match Maya's scriptable viewport colors to another DCC's look.

- **[`class StyleSetter(_StyleSetterInternal)`](mayatk/mayatk/ui_utils/style_setter/_style_setter.py#L106)** — Public namespace for the style-setter helpers (``mtk.StyleSetter.set_style("Blender")`` …).
  - `StyleSetter.list_styles()` *(static)* — Names of the shipped color styles (e.g.
  - `StyleSetter.set_style(name, persist=False)` *(static)* — Switch Maya's viewport colors to the named style — a targeted overlay of just the keys
  - `StyleSetter.list_templates()` *(static)* — Ordered ``{display_name: token}`` of everything a style-selector combo offers: each shipped
  - `StyleSetter.apply_template(name, persist=False)` *(static)* — Apply a selection from :func:`list_templates` by its token — a shipped style name, applied

<a id="uv_utils--_auto_unwrap"></a>
### `uv_utils/_auto_unwrap.py`

External auto-unwrap round-trip: OBJ out, engine, OBJ back, UVs transferred.

- **[`class AutoUnwrapResult`](mayatk/mayatk/uv_utils/_auto_unwrap.py#L26)** — Per-object outcome of an :meth:`auto_unwrap` run.

<a id="uv_utils--_uv_pack"></a>
### `uv_utils/_uv_pack.py`

xatlas pack round-trip: UV arrays out, :class:`pythontk.UvPack`, per-shell

- **[`class PackUvsResult`](mayatk/mayatk/uv_utils/_uv_pack.py#L61)** — Per-object outcome of a :meth:`mayatk.UvUtils.pack_uvs` run.

<a id="uv_utils--_uv_utils"></a>
### `uv_utils/_uv_utils.py`

- **[`class UvUtils(ptk.HelpMixin)`](mayatk/mayatk/uv_utils/_uv_utils.py#L21)**
  - `UvUtils.calculate_uv_padding(map_size: int, normalize: bool = False, factor: int = 256) -> float` *(static)* — The texture gutter for a given map size — Maya-side name for the ecosystem rule.
  - `UvUtils.udim_to_tile(udim: int) -> Tuple[int, int]` *(static)* — UDIM tile number to its (u, v) tile offset — Maya-side name for the
  - `UvUtils.orient_shells(objects)` *(static)* — Rotate UV shells to run parallel with the most adjacent U or V axis of their bounding box.
  - `UvUtils.move_to_uv_space(objects, u, v, relative=True)` *(static)* — Move objects to the given u and v coordinates.
  - `UvUtils.get_uv_bounds(objects) -> Optional[Tuple[float, float, float, float]]` *(static)* — The UV-space bounding box of *objects*, as one box over the whole input.
  - `UvUtils.gather_to_udim(cls, objects, udim: Optional[int] = None, map_size: int = 4096) -> Optional[int]` *(class)* — Move UV shells sitting outside the target UDIM tile into it.
  - `UvUtils.get_neighbor_shell_bounds(objects) -> List[Tuple[float, float, float, float]]` *(static)* — Per-shell UV boxes that share *objects*' UV space, excluding their own.
  - `UvUtils.mirror_uvs(cls, objects, axis: str = 'u', pivot: tuple | None = None, per_shell: bool = True, preserve_position: bool = True)` *(class)* — Mirror UVs across U or V.
  - `UvUtils.flip_uvs(cls, objects, axis: str = 'u', pivot: tuple | None = None, per_shell: bool = True, preserve_position: bool = True)` *(class)* — Backward-compatible alias for :meth:`mirror_uvs`.
  - `UvUtils.get_uv_shell_sets(objects=None, returned_type='shell', whole_shells=False)` *(static)* — Get UV shells and their corresponding sets of faces.
  - `UvUtils.get_uv_shell_border_edges(objects)` *(static)* — Get the edges that make up any UV islands of the given objects.
  - `UvUtils.get_cylinder_seam_edges(cls, mesh, sections=None, invert_seam: bool = False, cap_faces=None)` *(class)* — Identify the UV seam edges for unwrapping a smooth cylinder / tube.
  - `UvUtils.get_auto_seam_edges(cls, mesh, angle: float = 45.0, invert_seam: bool = False)` *(class)* — Seam edges that auto-unwrap a turned / stepped cylinder or tube.
  - `UvUtils.get_topology_seam_edges(cls, mesh, angle: float = 45.0, invert_seam=False)` *(class)* — Seam edges from smooth-region topology — no global axis assumed.
  - `UvUtils.detect_seam_algorithm(cls, mesh) -> str` *(class)* — Pick the seam strategy that suits *mesh*: ``"axis"`` or ``"topology"``.
  - `UvUtils.cut_cylinder_seams(cls, objects=None, angle=45.0, invert_seam=False, history=True, sew=True, algorithm='auto')` *(class)* — Cut auto UV seams for cylinder / tube unwrapping on each mesh.
  - `UvUtils.cut_uv_edges(edges, history: bool = True)` *(static)* — Cut (split) UV shells along the given edges, spanning any number of objects.
  - `UvUtils.auto_unwrap(cls, objects=None, method: str = 'hard', map_size: int = 4096, pack: Optional[bool] = None, orient: bool = True, engine_params: Optional[dict] = None)` *(class)* — Automatically unwrap meshes with an external unwrapping engine.
  - `UvUtils.pack_uvs(cls, objects=None, map_size: int = 1024, udim: int = 1001, coverage: Tuple[float, float] = (1.0, 1.0), rotate: bool = True, brute_force: bool = False, preserve_3d: bool = True, padding: Optional[float] = None)` *(class)* — Pack existing UV shells with the external xatlas engine.
  - `UvUtils.unwrap_cylinder(cls, objects=None, angle=45.0, invert_seam=False, unfold=True, orient=True, map_size=4096, sew=True, algorithm='auto')` *(class)* — Auto-unwrap cylinder / tube / turned meshes: seam, then unfold flat.
  - `UvUtils.get_texel_density(objects, map_size)` *(static)* — Calculate the texel density for the given objects' faces.
  - `UvUtils.set_texel_density(cls, objects=None, density=1.0, map_size=4096)` *(class)* — Set the texel density for the given objects.
  - `UvUtils.snapshot_uv_sets(objects: Sequence[Union[str, object]], prefix: str = '_uv_snap') -> List[UvSnapshot]` *(static)* — Copy each object's active UV set into a uniquely-named backup set.
  - `UvUtils.restore_uv_snapshot(snapshots: Sequence[UvSnapshot]) -> None` *(static)* — Restore UVs captured by ``snapshot_uv_sets``.
  - `UvUtils.discard_uv_snapshot(snapshots: Sequence[UvSnapshot]) -> None` *(static)* — Delete the snapshot UV sets without restoring them.
  - `UvUtils.transfer_uvs(cls, source: Union[str, object, List[Union[str, object]]], target: Union[str, object, List[Union[str, object]]], tolerance: float = 0.1, match_by_similarity: bool = True, sample_space: str = 'auto') -> List[Tuple[str, str, str]]` *(class)* — Transfers UVs from source meshes to target meshes.
  - `UvUtils.transfer_uvs_to_similar(cls, source: Union[str, object], candidates: Optional[List[Union[str, object]]] = None, tolerance: float = 0.9) -> List[str]` *(class)* — Transfer UVs from one source mesh to every geometrically similar mesh.
  - `UvUtils.reorder_uv_sets(obj: str, new_order: list[str]) -> None` *(static)* — Reorder UV sets of the given object to match the specified new order.
  - `UvUtils.apply_uv_layout(layouts: dict, uv_set: str = None, quiet: bool = False) -> dict` *(static)* — Write UV layouts authored in ANOTHER application onto these meshes.
  - `UvUtils.create_lightmap_uvs(cls, objects, uv_set: str = None, map_size: int = 1024, planes: int = 6, force: bool = False, freeze_history: bool = False, quiet: bool = False) -> dict` *(class)* — Ensure each mesh has a packed, non-overlapping lightmap UV set.
  - `UvUtils.remove_empty_uv_sets(objects, quiet: bool = False) -> None` *(static)* — Remove empty UV sets from the given objects.

<a id="uv_utils--rizom_bridge--_rizom_bridge"></a>
### `uv_utils/rizom_bridge/_rizom_bridge.py`

- **[`class RizomUVBridge(ptk.LoggingMixin, _RizomUVBridgeInternal)`](mayatk/mayatk/uv_utils/rizom_bridge/_rizom_bridge.py#L69)**
  - `RizomUVBridge.rizom_path(self)` *(property)* — Resolve the RizomUV executable path.
  - `RizomUVBridge.rizom_version(self) -> 'tuple[int, ...]'` *(property)* — The installed Rizom version, parsed from the install-dir name.
  - `RizomUVBridge.export_path(self)` *(property)* — Lazy initialization of the export path.
  - `RizomUVBridge.script_path(self)` *(property)* — Get the path to the UV script file as a POSIX string.
  - `RizomUVBridge.process_with_rizomuv(self, objects, uv_script=None, preset=None, params=None, select_objects=None, skip_instances=True)` — Run the full export -> RizomUV -> re-import workflow.
  - `RizomUVBridge.expand_by_materials(objects) -> 'tuple[list[str], list[str]]'` *(static)* — Expand *objects* to every mesh sharing their assigned materials.
  - `RizomUVBridge.send_to_rizomuv(self, objects, params=None)` — Export *objects* and open them in a fresh RizomUV session.

<a id="uv_utils--rizom_bridge--parameters"></a>
### `uv_utils/rizom_bridge/parameters.py`

Registry of user-tunable RizomUV parameters exposed to the bridge UI.

- **[`class Parameters`](mayatk/mayatk/uv_utils/rizom_bridge/parameters.py#L447)** — Parameters — module namespace.
  - `Parameters.expand_includes(script_text: str) -> str` *(static)* — Expand ``__PACK_BLOCK__``-style include tokens to their partial's text.
  - `Parameters.preset_min_version(script_text: str) -> 'tuple[int, ...] | None'` *(static)* — Minimum Rizom version a preset declares, or ``None`` if ungated.
  - `Parameters.referenced_keys(script_text: str) -> 'set[str]'` *(static)* — Registered keys present in *script_text* (delegates to uitk.bridge).
  - `Parameters.defaults() -> 'dict[str, Any]'` *(static)* — Return ``{key: default}`` for every registered parameter.
  - `Parameters.derived_values(values: 'dict[str, Any]') -> 'dict[str, float]'` *(static)* — Return the computed pack-gutter tokens (see :data:`DERIVED_KEYS`).
  - `Parameters.render_context(values: 'dict[str, Any]') -> 'dict[str, str]'` *(static)* — Format *values* for ``StrUtils.replace_delimited`` using Lua literals.
  - `Parameters.strip_unsupported(script_text: str, version: 'tuple[int, ...]') -> str` *(static)* — Drop every line that references a placeholder requiring a newer Rizom.

<a id="uv_utils--rizom_bridge--rizom_bridge_slots"></a>
### `uv_utils/rizom_bridge/rizom_bridge_slots.py`

Slots for the RizomUV bridge panel.

- **[`class RizomBridgeSlots(MayaBridgeSlotsBase)`](mayatk/mayatk/uv_utils/rizom_bridge/rizom_bridge_slots.py#L91)** — Slots wired to ``rizom_bridge.ui`` via :class:`MayaBridgeSlotsBase`.
  - `RizomBridgeSlots.params_module(self)` *(property)*
  - `RizomBridgeSlots.template_dir(self) -> Path` *(property)*
  - `RizomBridgeSlots.make_bridge(self) -> RizomUVBridge`
  - `RizomBridgeSlots.list_template_modes(self)` — Return ``[(stem, ""), ...]`` for every bundled ``.lua`` script.
  - `RizomBridgeSlots.b000(self)` — Run the chosen preset: round-trip, or one-way send when ``send`` is picked.
  - `RizomBridgeSlots.open_uv_editor(self)` — Open Maya's UV Editor (TextureViewWindow).

<a id="uv_utils--shell_xform"></a>
### `uv_utils/shell_xform.py`

Dedicated UV shell-transform panel.

- **[`class ShellXformSlots(ptk.LoggingMixin)`](mayatk/mayatk/uv_utils/shell_xform.py#L37)** — Switchboard slots for the Shell Xform panel (``shell_xform.ui``).
  - `ShellXformSlots.header_init(self, widget)` — Header menu — Open UV Editor + panel help.
  - `ShellXformSlots.cmb_move_scope_init(self, widget)` — Move scope — how far one arrow press travels, plus the snap button.
  - `ShellXformSlots.b023(self)` — Move To UV Space: Left
  - `ShellXformSlots.b024(self)` — Move To UV Space: Down
  - `ShellXformSlots.b025(self)` — Move To UV Space: Up
  - `ShellXformSlots.b026(self)` — Move To UV Space: Right
  - `ShellXformSlots.gather_to_udim(self)` — Move shells sitting outside the selection's UDIM tile into it.
  - `ShellXformSlots.b034(self)` — Flip U: mirror the selected UVs horizontally about each shell's center.
  - `ShellXformSlots.b035(self)` — Flip V: mirror the selected UVs vertically about each shell's center.
  - `ShellXformSlots.b036(self)` — Rotate the selected UVs counter-clockwise by the s041 angle.
  - `ShellXformSlots.b037(self)` — Rotate the selected UVs clockwise by the s041 angle.
  - `ShellXformSlots.s041(self, value, widget)` — Rotate Angle — passive input; read by the Rotate buttons (b036/b037).
  - `ShellXformSlots.tb005_init(self, widget)` — Initialize Straighten UV
  - `ShellXformSlots.tb005(self, widget)` — Straighten UV
  - `ShellXformSlots.tb006_init(self, widget)` — Initialize Distribute
  - `ShellXformSlots.tb006(self, widget)` — Distribute: evenly space the selected UV shells horizontally or vertically.
  - `ShellXformSlots.tb008_init(self, widget)` — Initialize Mirror UVs.
  - `ShellXformSlots.tb008(self, widget)` — Mirror UVs (footprint-preserving by default).
  - `ShellXformSlots.align_u_min(self)` — Align the selected UVs to their minimum U (left).
  - `ShellXformSlots.align_u_avg(self)` — Align the selected UVs to their average U (center).
  - `ShellXformSlots.align_u_max(self)` — Align the selected UVs to their maximum U (right).
  - `ShellXformSlots.align_v_min(self)` — Align the selected UVs to their minimum V (bottom).
  - `ShellXformSlots.align_v_avg(self)` — Align the selected UVs to their average V (center).
  - `ShellXformSlots.align_v_max(self)` — Align the selected UVs to their maximum V (top).
  - `ShellXformSlots.linear_align(self)` — Linearly align the selected UVs between their two end points.
  - `ShellXformSlots.orient_shells(self)` — Orient each shell to run parallel with its nearest U/V axis.
  - `ShellXformSlots.orient_edges(self)` — Orient the shell so its selected edge runs along U or V.
  - `ShellXformSlots.gather_shells(self)` — Gather the selected shells together toward the 0-1 UV space.
  - `ShellXformSlots.randomize_shells(self)` — Randomly offset the selected shells.
  - `ShellXformSlots.open_uv_editor(self)` — Open Maya's UV Editor (TextureViewWindow).

<a id="xform_utils--_xform_utils"></a>
### `xform_utils/_xform_utils.py`

- **[`class XformUtils(_XformUtilsInternal, ptk.HelpMixin)`](mayatk/mayatk/xform_utils/_xform_utils.py#L832)** — Transform utilities for Maya objects.
  - `XformUtils.convert_axis(value, invert=False, ortho=False, to_integer=False)` *(static)* — Converts between axis representations and optionally inverts the axis or returns an orthogonal axis.
  - `XformUtils.move_to(cls, source, target, pivot='center', group_move=False)` *(class)* — Move source object(s) to align with the target object(s).
  - `XformUtils.drop_to_grid(cls, objects, align='Mid', origin=False, center_pivot=False, freeze_transforms=False)` *(class)* — Align objects to Y origin on the grid using a helper plane.
  - `XformUtils.match_scale(cls, a, b, scale=True, average=False)` *(class)* — Scale each of the given objects in 'a' to the combined bounding box of the objects in 'b'.
  - `XformUtils.scale_connected_edges(objects, scale_factor=1.1) -> None` *(static)* — Scales each set of connected edges separately, either uniformly or non-uniformly.
  - `XformUtils.store_transforms(objects, prefix='original', accumulate=True, traverse=False, channels=None)` *(static)* — Capture the current local TRS as a cumulative per-channel bake history.
  - `XformUtils.freeze_instanced_group(cls, master: str, translate: bool = True, rotate: bool = True, scale: bool = True, quiet: bool = True) -> bool` *(class)* — Freeze *master* while keeping its instance group intact.
  - `XformUtils.freeze_transforms(cls, objects, center_pivot=0, force=True, delete_history=False, freeze_children=False, unlock_children=True, connection_strategy='preserve', instance_strategy='skip', from_channel_box=False, store=True, **kwargs)` *(class)* — Freezes transformations on the given objects.
  - `XformUtils.freeze_to_opm(objects, reset_rotate_axis: bool = False, reset_joint_orient: bool = False, store: bool = True) -> None` *(static)* — Freeze transforms into offsetParentMatrix while preserving pivot placement.
  - `XformUtils.unfreeze_from_opm(cls, objects, prefix='original', delete_attrs=True) -> List[str]` *(class)* — Inverse of :meth:`freeze_to_opm`: clear ``offsetParentMatrix`` and put
  - `XformUtils.unfreeze_to_parent(objects, traverse: bool = False, preserve_root: bool = True) -> List[str]` *(static)* — Push a child transform's local matrix up into its parent and zero the child.
  - `XformUtils.restore_transforms(cls, objects, prefix='original', delete_attrs=True, channels=None, traverse=False)` *(class)* — Compose stored bake history with current local TRS, per channel.
  - `XformUtils.clear_stored_transforms(objects, prefix='original') -> List[str]` *(static)* — Delete the per-channel bake attrs without restoring.
  - `XformUtils.repair_stored_transforms(cls, objects=None, prefix='original', dry_run=False, clear_stale=False, tolerance=0.0001)` *(class)* — Triage bake history left by earlier tool versions, restore only
  - `XformUtils.has_stored_transforms(objects, prefix='original')` *(static)* — Check if objects have any stored bake history.
  - `XformUtils.channels_at_identity(node, tolerance=0.0001)` *(static)* — True when *node*'s T/R/S channels sit at identity.
  - `XformUtils.get_stored_transforms(node, prefix='original')` *(static)* — Read one node's stored pre-freeze channels back as plain values.
  - `XformUtils.reset_translation(cls, objects)` *(class)* — Reset the translation transformations on the given object(s).
  - `XformUtils.set_translation_to_pivot(cls, node)` *(class)* — Set an object's translation value from its pivot location.
  - `XformUtils.get_manip_pivot_matrix(obj, **kwargs)` *(static)* — Return the object's transform matrix using xform, allowing kwargs override.
  - `XformUtils.set_manip_pivot_matrix(obj, matrix, **kwargs) -> None` *(static)* — Apply a transformation matrix's position and orientation to the manip pivot.
  - `XformUtils.restore_original_axes(cls, objects=None, prefix='original')` *(class)* — Aim the manipulator at an object's PRE-FREEZE axes, without un-freezing it.
  - `XformUtils.get_pivot_options(cls)` *(class)* — Returns a list of supported pivot options.
  - `XformUtils.clear_manip_cache(cls)` *(class)* — Clears the cached manipulator pivot data.
  - `XformUtils.snapshot_manip_pivot(cls, node)` *(class)* — Snapshot the current manipulator pivot state for the given node into the cache.
  - `XformUtils.get_operation_axis_matrix(cls, node, pivot: str)` *(class)* — Determines the pivot matrix (orientation + position) for transformations.
  - `XformUtils.get_operation_axis_pos(cls, node, pivot, axis_index=None)` *(class)* — Determines the pivot position for mirroring/cutting along a specified axis or all axes.
  - `XformUtils.align_pivot_to_selection(align_from=None, align_to=None, translate=True)` *(static)* — Align one object's pivot point to another using 3-point alignment.
  - `XformUtils.reset_pivot_transforms(objects=None) -> None` *(static)* — Reset Pivot Transforms for the specified objects or selected objects.
  - `XformUtils.world_align_pivot(objects=None, pivot_type: str = 'object', mode: str = 'set')` *(static)* — Get or set a world-aligned pivot for the specified objects or components.
  - `XformUtils.bake_pivot(objects, position=False, orientation=False)` *(static)* — Bake the pivot orientation and position of the given object(s).
  - `XformUtils.transfer_pivot(cls, objects, translate: bool = False, rotate: bool = False, scale: bool = False, bake: bool = False, world_space: bool = True, mirror: str = '', select_targets_after_transfer: bool = False)` *(class)* — Transfer the pivot orientation from the first given object to the remaining given objects.
  - `XformUtils.aim_object_at_point(objects, target_pos, aim_vect=(1, 0, 0), up_vect=(0, 1, 0))` *(static)* — Aim the given object(s) at the given world space position.
  - `XformUtils.orient_to_vector(transform, aim_vector=(1, 0, 0), up_vector=(0, 1, 0))` *(static)* — Orients a transform so its local +X aims along the given world-space vector.
  - `XformUtils.rotate_axis(cls, objects, target_pos)` *(class)* — Aim the given object at the given world space position.
  - `XformUtils.get_orientation(objects, returned_type='point')` *(static)* — Get an objects orientation as a point or vector.
  - `XformUtils.get_dist_between_two_objects(a, b)` *(static)* — Get the magnatude of a vector using the center points of two given objects.
  - `XformUtils.get_center_point(objects)` *(static)* — Get the bounding box center point of any given object(s).
  - `XformUtils.get_bounding_box(objects, value='', world_space=True, return_valid_keys=False)` *(static)* — Calculate and retrieve specific properties of the bounding box for the given object(s) or component…
  - `XformUtils.sort_by_bounding_box_value(cls, objects, value='volume', descending=True, also_return_value=False)` *(class)* — Sort the given objects by their bounding box value.
  - `XformUtils.align_using_three_points(vertices)` *(static)* — Move and align the object defined by the first 3 points to the last 3 points.
  - `XformUtils.is_overlapping(a, b, tolerance=0.001)` *(static)* — Check if the vertices in a and b are overlapping within the given tolerance.
  - `XformUtils.check_objects_against_plane(objects, plane_point, plane_normal, return_type: str = 'bool')` *(static)* — General method to check if any object's geometry is below a defined plane.
  - `XformUtils.get_vertex_positions(objects, worldSpace=True)` *(static)* — Get all vertex positions for the given objects.
  - `XformUtils.get_matching_verts(cls, a, b, world_space=False)` *(class)* — Find any vertices which point locations match between two given mesh.
  - `XformUtils.order_by_distance(cls, objects, reference_point=None, reverse=False)` *(class)* — Order the given objects by their distance from the given reference point.
  - `XformUtils.align_vertices(mode, average=False, edgeloop=False)` *(static)* — Align selected vertices along one or more axes.
  - `XformUtils.get_translation(node, world: bool = False)` *(static)* — Translation as ``om.MVector``.
  - `XformUtils.get_object_matrix(node, world: bool = False)` *(static)* — Local or world matrix as ``om.MMatrix``.
  - `XformUtils.set_object_matrix(node, value, world: bool = False) -> None` *(static)* — Apply *value* to *node*'s local or world transformation matrix.

<a id="xform_utils--matrices"></a>
### `xform_utils/matrices.py`

Matrix utilities for Maya rigging and animation.

- **[`class MatricesError(RuntimeError)`](mayatk/mayatk/xform_utils/matrices.py#L72)** — Base exception for matrix utility operations.
- **[`class Matrices(_MatrixMath, _DagTransforms, _NodeBuilders, ptk.HelpMixin, _MatricesInternal)`](mayatk/mayatk/xform_utils/matrices.py#L878)** — Matrix utilities for Maya rigging and animation.
  - `Matrices.get_matrix(node: str, attr: str = 'worldMatrix', index: int = 0) -> List[float]` *(static)* — Return a 16-element flat list for a matrix attribute on *node*.
  - `Matrices.set_matrix(node: str, attr: str, value, index: int = 0) -> None` *(static)* — Set a matrix attribute on *node* from an MMatrix or 16-element iterable.
  - `Matrices.identity() -> 'MMatrix'` *(static)* — Return a 4x4 identity matrix.
  - `Matrices.to_mmatrix(matrix_like: Union[str, 'MMatrix', list]) -> 'MMatrix'` *(static)* — Convert various matrix representations to MMatrix.
  - `Matrices.local_matrix(node: str) -> 'MMatrix'` *(static)* — Get a transform's local matrix as MMatrix.
  - `Matrices.from_srt(translate: Iterable[float] = (0.0, 0.0, 0.0), rotate_euler_deg: Iterable[float] = (0.0, 0.0, 0.0), scale: Iterable[float] = (1.0, 1.0, 1.0), rotate_order: str = 'xyz') -> 'MMatrix'` *(static)* — Compose an MMatrix from separate scale, rotation, and translation components.
  - `Matrices.decompose(m: 'MMatrix', rotate_order: str = 'xyz') -> Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float]]` *(static)* — Decompose an MMatrix into translation, rotation (degrees), and scale components.
  - `Matrices.inverse(m: 'MMatrix') -> 'MMatrix'` *(static)* — Calculate the inverse of a matrix.
  - `Matrices.safe_inverse(m: 'MMatrix', tolerance: float = 1e-12) -> Optional['MMatrix']` *(static)* — Inverse of *m*, or None when it is singular or non-finite.
  - `Matrices.mult(*mats: 'MMatrix') -> 'MMatrix'` *(static)* — Multiply matrices right-to-left.
  - `Matrices.world_to_local(world_matrix: 'MMatrix', parent_world_matrix: 'MMatrix') -> 'MMatrix'` *(static)* — Convert a world-space matrix to local space relative to a parent.
  - `Matrices.local_to_world(local_matrix: 'MMatrix', parent_world_matrix: 'MMatrix') -> 'MMatrix'` *(static)* — Convert a local-space matrix to world space.
  - `Matrices.extract_translation(m: 'MMatrix') -> Tuple[float, float, float]` *(static)* — Extract just the translation component from a matrix.
  - `Matrices.is_identity(m: 'MMatrix', tolerance: float = 1e-09) -> bool` *(static)* — Check if a matrix is approximately equal to the identity matrix.
  - `Matrices.set_offset_parent_matrix(node: str, m: 'MMatrix') -> None` *(static)* — Apply a matrix to a node's offsetParentMatrix attribute.
  - `Matrices.bake_world_matrix_to_transform(node: str, m: Union['MMatrix', list], reset_offset_parent_matrix: bool = True) -> None` *(static)* — Set a node's translate, rotate, and scale so its worldMatrix matches the given matrix.
  - `Matrices.freeze_to_offset_parent_matrix(node: str) -> None` *(static)* — Zero a node's translate, rotate, and scale by baking current world transform into offsetParentMatri…
  - `Matrices.ensure_node(node_type: str, name: Optional[str] = None) -> str` *(static)* — Create a node of the specified type.
  - `Matrices.build_mult_matrix_chain(mats: List[str], name: str = 'mmx_chain') -> Tuple[str, str]` *(static)* — Create a multMatrix node chain that multiplies matrices and decomposes the result.
  - `Matrices.drive_with_offset_parent_matrix(driver_world: str, driven_ctl: str, name: str = 'drive_opm') -> str` *(static)* — Drive a control's offsetParentMatrix from another transform's world matrix.
  - `Matrices.build_space_switch(control: str, space_parents: List[str], attr_owner: Optional[str] = None, attr_name: str = 'space', name: str = 'space_switch') -> str` *(static)* — Create a multi-space switch system using blendMatrix.
  - `Matrices.build_aim_matrix(source: str, target: str, up_object: Optional[str] = None, primary_axis: Tuple[float, float, float] = (1.0, 0.0, 0.0), secondary_axis: Tuple[float, float, float] = (0.0, 1.0, 0.0), secondary_mode: str = 'align', name: str = 'aim_mx') -> str` *(static)* — Create a node-based aim constraint using aimMatrix.
  - `Matrices.build_ikfk_blend(ik_mx_attr: str, fk_mx_attr: str, parent_inv_attr: str, out_target_ctl: str, switch_attr_owner: str, switch_attr: str = 'ikFk', name: str = 'ikfk_blend') -> str` *(static)* — Create an IK/FK blend system using blendMatrix in local space.

<a id="xform_utils--pivot_watcher"></a>
### `xform_utils/pivot_watcher.py`

Real-time pivot-change notifier built on :class:`ScriptJobManager`.

- **[`class PivotWatcher(_PivotWatcherInternal)`](mayatk/mayatk/xform_utils/pivot_watcher.py#L105)** — Fire *callback* on intentional manipulator-pivot drags.
  - `PivotWatcher.owner(self) -> Any` *(property)*
  - `PivotWatcher.started(self) -> bool` *(property)*
  - `PivotWatcher.start(self) -> None` — Subscribe to the watched events (idempotent).
  - `PivotWatcher.stop(self) -> None` — Unsubscribe from all watched events (idempotent).
  - `PivotWatcher.attach_widget(self, widget) -> None` — Auto-:meth:`stop` when *widget* emits ``destroyed``.
