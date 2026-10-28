# mayatk — API Registry

_Auto-generated. Do not edit by hand. Refresh via `m3trik/scripts/generate_api_registry.py`._

## Index

- [`__init__.py`](#__init__)
- [`anim_utils/_anim_utils.py`](#anim_utils--_anim_utils) — ``AnimUtils`` -- the public index of mayatk's key and curve operations.
- [`anim_utils/_tied_keys.py`](#anim_utils--_tied_keys) — Bookend (tied) keys behind :class:`mayatk.AnimUtils`.
- [`anim_utils/blendshape_animator/_blendshape_animator.py`](#anim_utils--blendshape_animator--_blendshape_animator) — Main workflow facade for blendShape morph-animation creation, editing, and export.
- [`anim_utils/blendshape_animator/applicator.py`](#anim_utils--blendshape_animator--applicator) — Applies tween mesh edits back to blendShape in-between targets.
- [`anim_utils/blendshape_animator/creator.py`](#anim_utils--blendshape_animator--creator) — Creates in-between target meshes for custom blendShape animation curves.
- [`anim_utils/blendshape_animator/history.py`](#anim_utils--blendshape_animator--history) — Construction-history queries shared by the blendshape_animator modules.
- [`anim_utils/blendshape_animator/keyframes.py`](#anim_utils--blendshape_animator--keyframes) — Core blendShape keyframe animation operations.
- [`anim_utils/blendshape_animator/recovery.py`](#anim_utils--blendshape_animator--recovery) — Recovery utilities for corrupted blendShape setups.
- [`anim_utils/blendshape_animator/target.py`](#anim_utils--blendshape_animator--target) — Tween mesh wrappers and registry for blendShape in-between targets.
- [`anim_utils/blendshape_animator/validator.py`](#anim_utils--blendshape_animator--validator) — Mesh and blendShape validation for blendShape animation setup.
- [`anim_utils/key_stash/_key_stash.py`](#anim_utils--key_stash--_key_stash) — Key Stash — park keyframes outside the working animation, retrieve later (Maya).
- [`anim_utils/playblast_exporter.py`](#anim_utils--playblast_exporter) — Playblast capture, encoding, and preview-render exports for Maya.
- [`anim_utils/scale_keys.py`](#anim_utils--scale_keys) — Dedicated scale-keys module to keep AnimUtils lean and testable.
- [`anim_utils/segment_keys.py`](#anim_utils--segment_keys)
- [`anim_utils/shots/_detection.py`](#anim_utils--shots--_detection) — Shot-region detection — Maya scene acquisition over the pure engine math.
- [`anim_utils/shots/_shot_apply.py`](#anim_utils--shots--_shot_apply) — Commit resolved :class:`MovePlan`\ s to the Maya scene.
- [`anim_utils/shots/_shots.py`](#anim_utils--shots--_shots) — Maya shot-store adapter — the DCC layer over ``pythontk``'s shots engine.
- [`anim_utils/shots/shot_manifest/_shot_manifest.py`](#anim_utils--shots--shot_manifest--_shot_manifest) — Maya Shot Manifest adapter — the DCC layer over pythontk's manifest engine.
- [`anim_utils/shots/shot_manifest/behaviors/_behaviors.py`](#anim_utils--shots--shot_manifest--behaviors--_behaviors) — Behaviors — Maya appliers over the engine's pure keying-recipe core.
- [`anim_utils/shots/shot_manifest/manifest_host.py`](#anim_utils--shots--shot_manifest--manifest_host) — What the Shot Manifest panel needs from Maya: its store, engine, detection,
- [`anim_utils/shots/shot_manifest/range_resolver.py`](#anim_utils--shots--shot_manifest--range_resolver) — Range resolution for the Shot Manifest build pipeline (Maya-bound facade).
- [`anim_utils/shots/shot_sequencer/_shot_sequencer.py`](#anim_utils--shots--shot_sequencer--_shot_sequencer) — Shot Sequencer — manages per-shot animation with ripple editing.
- [`anim_utils/shots/shot_sequencer/graph_sync.py`](#anim_utils--shots--shot_sequencer--graph_sync) — Graph editor glue: the shot sequencer's curves in uitk's graph editor (Maya).
- [`anim_utils/shots/shot_sequencer/segment_collector.py`](#anim_utils--shots--shot_sequencer--segment_collector) — Segment collection and attribute extraction for the shot sequencer.
- [`anim_utils/shots/shot_sequencer/sequencer_host.py`](#anim_utils--shots--shot_sequencer--sequencer_host) — What the Shot Sequencer panel needs from Maya: :class:`SequencerHost`.
- [`anim_utils/smart_bake/_smart_bake.py`](#anim_utils--smart_bake--_smart_bake) — Smart bake module for intelligent pre-bake animation processing.
- [`anim_utils/smart_bake/bake_session.py`](#anim_utils--smart_bake--bake_session) — Persistence and restore engine for SmartBake's nondestructive manifest.
- [`anim_utils/stagger_keys.py`](#anim_utils--stagger_keys) — Dedicated stagger-keys module to keep AnimUtils lean and testable.
- [`anim_utils/world_fit_bake.py`](#anim_utils--world_fit_bake) — World-fitted transform bake: reparent nodes under a chosen target and key the
- [`audio_utils/_audio_utils.py`](#audio_utils--_audio_utils) — Unified audio system for Maya scenes.
- [`audio_utils/audio_clips/_audio_clips.py`](#audio_utils--audio_clips--_audio_clips) — Scene-wide audio event manager — thin facade over ``audio_utils``.
- [`audio_utils/batch.py`](#audio_utils--batch) — Batch orchestration — undo chunk + dirty-track buffering.
- [`audio_utils/compositor.py`](#audio_utils--compositor) — Compositor — derives DG audio nodes from keyed track events.
- [`audio_utils/migrate.py`](#audio_utils--migrate) — One-shot migration from legacy single-enum carriers to per-track schema.
- [`audio_utils/nodes.py`](#audio_utils--nodes) — Low-level DG audio node primitives.
- [`audio_utils/segments.py`](#audio_utils--segments) — Consumer-facing segment discovery for sequencer + manifest.
- [`cam_utils/_cam_utils.py`](#cam_utils--_cam_utils)
- [`cam_utils/camera_visibility.py`](#cam_utils--camera_visibility) — Per-camera visibility: Maya's Display > Per Camera Visibility
- [`core_utils/_core_utils.py`](#core_utils--_core_utils)
- [`core_utils/auto_instancer/_auto_instancer.py`](#core_utils--auto_instancer--_auto_instancer) — Scene auto-instancer: convert geometrically identical meshes to instances.
- [`core_utils/auto_instancer/assembly_reconstructor.py`](#core_utils--auto_instancer--assembly_reconstructor) — Logic for separating and reassembling mesh assemblies.
- [`core_utils/auto_instancer/geometry_matcher.py`](#core_utils--auto_instancer--geometry_matcher) — Geometry analysis and matching logic for AutoInstancer.
- [`core_utils/auto_instancer/instancing_strategy.py`](#core_utils--auto_instancer--instancing_strategy) — AutoInstancer's instancing strategy: the Maya binding of the ptk engine.
- [`core_utils/components.py`](#core_utils--components)
- [`core_utils/diagnostics/animation_diag.py`](#core_utils--diagnostics--animation_diag) — Animation-curve diagnostics and optional repair helpers.
- [`core_utils/diagnostics/audit_records.py`](#core_utils--diagnostics--audit_records) — Scene-audit data contract: profiles, per-asset records, and the SceneReport tree.
- [`core_utils/diagnostics/mesh_diag.py`](#core_utils--diagnostics--mesh_diag) — Mesh diagnostics and repair helpers.
- [`core_utils/diagnostics/scene_audit.py`](#core_utils--diagnostics--scene_audit) — Scene audit engine — game-readiness analysis over meshes, materials, and textures.
- [`core_utils/diagnostics/scene_diag.py`](#core_utils--diagnostics--scene_diag) — Scene repair helpers: OCIO / color management, unknown nodes and plugins,
- [`core_utils/diagnostics/transform_diag.py`](#core_utils--diagnostics--transform_diag) — Transform diagnostics and repair helpers.
- [`core_utils/diagnostics/uv_diag.py`](#core_utils--diagnostics--uv_diag) — UV diagnostics and repair helpers.
- [`core_utils/mash.py`](#core_utils--mash)
- [`core_utils/plugins/_plugins.py`](#core_utils--plugins--_plugins) — Every Maya plug-in mayatk loads goes through one door: :class:`Plugins`.
- [`core_utils/preview.py`](#core_utils--preview) — Hermetic preview with replay-on-commit (H1 design).
- [`core_utils/script_job_manager.py`](#core_utils--script_job_manager) — Centralized Maya event subscription manager.
- [`core_utils/undo_recorder.py`](#core_utils--undo_recorder) — Put OpenMaya edits on Maya's undo queue, as one ordinary undo step.
- [`core_utils/units.py`](#core_utils--units) — Maya's lengths and angles, converted by the kind of query that read them.
- [`display_utils/_display_utils.py`](#display_utils--_display_utils)
- [`display_utils/color_id.py`](#display_utils--color_id)
- [`display_utils/exploded_view.py`](#display_utils--exploded_view)
- [`edit_utils/_edit_utils.py`](#edit_utils--_edit_utils)
- [`edit_utils/bevel.py`](#edit_utils--bevel)
- [`edit_utils/bridge.py`](#edit_utils--bridge) — Bridge: connect open edge loops with new faces (``polyBridgeEdge``).
- [`edit_utils/curtain/_curtain.py`](#edit_utils--curtain--_curtain) — Procedural draped-cloth (curtain) generator for Maya.
- [`edit_utils/curtain/_curtain_drape.py`](#edit_utils--curtain--_curtain_drape) — Procedural draped-cloth (curtain) drape engine — pure geometry, no DCC.
- [`edit_utils/cut_on_axis.py`](#edit_utils--cut_on_axis)
- [`edit_utils/duplicate_grid.py`](#edit_utils--duplicate_grid)
- [`edit_utils/duplicate_linear.py`](#edit_utils--duplicate_linear)
- [`edit_utils/duplicate_radial.py`](#edit_utils--duplicate_radial)
- [`edit_utils/dynamic_pipe.py`](#edit_utils--dynamic_pipe)
- [`edit_utils/macros/_macros.py`](#edit_utils--macros--_macros)
- [`edit_utils/naming/_naming.py`](#edit_utils--naming--_naming)
- [`edit_utils/primitives.py`](#edit_utils--primitives) — Primitive creation utilities for Maya.
- [`edit_utils/rack_builder.py`](#edit_utils--rack_builder) — Parametric EIA-310 (19-inch) equipment-rack generator.
- [`edit_utils/selection.py`](#edit_utils--selection)
- [`edit_utils/snap.py`](#edit_utils--snap)
- [`env_utils/_env_utils.py`](#env_utils--_env_utils)
- [`env_utils/blender_bridge/_blender_bridge.py`](#env_utils--blender_bridge--_blender_bridge) — Blender bridge engine -- export the Maya selection and run a chosen import template in Blender.
- [`env_utils/blender_bridge/_scene_import.py`](#env_utils--blender_bridge--_scene_import) — Import a Blender scene (.blend) or a glTF container (.glb/.gltf) into Maya via a
- [`env_utils/blender_bridge/parameters.py`](#env_utils--blender_bridge--parameters) — Registry of user-tunable Blender-bridge parameters exposed to the panel.
- [`env_utils/blender_bridge/templates/_bake_scene.py`](#env_utils--blender_bridge--templates--_bake_scene) — Import a converted intermediate (USD or FBX) headlessly (mayapy) and save it as a ``.ma``
- [`env_utils/blender_bridge/templates/_import_scene.py`](#env_utils--blender_bridge--templates--_import_scene) — Open a .blend / glTF headlessly (blender --background) and export it as FBX for a Maya import.
- [`env_utils/blender_bridge/templates/_import_scene_usd.py`](#env_utils--blender_bridge--templates--_import_scene_usd) — Open a .blend headlessly (blender --background) and export it as USD for a Maya import.
- [`env_utils/blender_bridge/templates/_save_scene.py`](#env_utils--blender_bridge--templates--_save_scene) — Import the bridged FBX into a headless Blender and save it as a ``.blend``.
- [`env_utils/blender_bridge/templates/bake_lightmaps.py`](#env_utils--blender_bridge--templates--bake_lightmaps) — Bake the bridged Maya selection's lightmaps in a headless Blender;
- [`env_utils/blender_bridge/templates/import.py`](#env_utils--blender_bridge--templates--import) — Import the bridged payload (FBX or USD) into Blender, with optional clean-slate and
- [`env_utils/devtools.py`](#env_utils--devtools)
- [`env_utils/fbx_utils.py`](#env_utils--fbx_utils)
- [`env_utils/handoff_export.py`](#env_utils--handoff_export) — Maya-side selection + export hooks shared by the hand-off bridge engines.
- [`env_utils/hierarchy_sync/_hierarchy_sync.py`](#env_utils--hierarchy_sync--_hierarchy_sync)
- [`env_utils/hierarchy_sync/hierarchy_baseline.py`](#env_utils--hierarchy_sync--hierarchy_baseline) — The scene's hierarchy baseline, stored in the scene.
- [`env_utils/hierarchy_sync/hierarchy_sync_session.py`](#env_utils--hierarchy_sync--hierarchy_sync_session) — Hierarchy Sync's host half: the reference a diff runs against, held in the
- [`env_utils/hierarchy_sync/scene_data_sidecar.py`](#env_utils--hierarchy_sync--scene_data_sidecar) — Scene-data sidecar -- Maya's scene hooks over ``ptk.SceneDataSidecarBase``.
- [`env_utils/material_records.py`](#env_utils--material_records) — The ``materials`` record's Maya producer.
- [`env_utils/maya_connection.py`](#env_utils--maya_connection) — Maya Connection Module
- [`env_utils/namespace_sandbox.py`](#env_utils--namespace_sandbox)
- [`env_utils/pm_doctor.py`](#env_utils--pm_doctor) — Shadow doctor for embedded-DCC installs (companion of package-manager.bat).
- [`env_utils/reference_manager/_reference_manager.py`](#env_utils--reference_manager--_reference_manager) — Reference Manager engine — Maya file references, assemblies and workspaces.
- [`env_utils/scene_exporter/_scene_exporter.py`](#env_utils--scene_exporter--_scene_exporter)
- [`env_utils/scene_exporter/task_manager.py`](#env_utils--scene_exporter--task_manager) — The Scene Exporter's task/check manager -- what ``perform_export`` drives.
- [`env_utils/scene_state.py`](#env_utils--scene_state) — Read named sections of live-scene state for transport.
- [`env_utils/script_output.py`](#env_utils--script_output)
- [`env_utils/unity_bridge/_unity_bridge.py`](#env_utils--unity_bridge--_unity_bridge) — Unity bridge engine -- export the Maya selection into a Unity project's Assets/.
- [`env_utils/unity_bridge/parameters.py`](#env_utils--unity_bridge--parameters) — User-tunable parameters for the Maya->Unity bridge panel.
- [`env_utils/usd.py`](#env_utils--usd) — USD import / export over Maya's native ``mayaUsd`` runtime.
- [`env_utils/webxr_preview.py`](#env_utils--webxr_preview) — Push the Maya selection to a live browser / WebXR preview.
- [`env_utils/workspace_manager.py`](#env_utils--workspace_manager)
- [`env_utils/workspace_map.py`](#env_utils--workspace_map)
- [`light_utils/_light_utils.py`](#light_utils--_light_utils) — Light utilities — building real scene lights from the geometry that represents them.
- [`light_utils/hdr_manager.py`](#light_utils--hdr_manager) — Arnold HDR environment manager.
- [`light_utils/light_layer_baker.py`](#light_utils--light_layer_baker) — Light layers: each light of a rig baked alone, the raw material of a look match.
- [`light_utils/light_rig.py`](#light_utils--light_rig) — Light Rig manager: save, manage, swap and restore named light rigs.
- [`light_utils/lightmap_baker/_probe_placement.py`](#light_utils--lightmap_baker--_probe_placement) — Where a lightmap bake's reflection probe stands -- Maya's scene for the placement.
- [`light_utils/lightmap_baker/lightmap_baker.py`](#light_utils--lightmap_baker--lightmap_baker) — High-level lightmap baking workflow for Maya -> game engines (Unity-first).
- [`light_utils/lightmap_baker/lightmap_records.py`](#light_utils--lightmap_baker--lightmap_records) — The scene record a lightmap bake leaves in Maya: markers, manifest, and the files they name.
- [`mat_utils/_mat_utils.py`](#mat_utils--_mat_utils) — ``MatUtils`` -- the public index of mayatk's material and texture operations.
- [`mat_utils/arnold_bridge.py`](#mat_utils--arnold_bridge) — Arnold render-bridge management.
- [`mat_utils/bake_sets.py`](#mat_utils--bake_sets) — Scene-stored bake sets: named object sets the bake tools read.
- [`mat_utils/emissive_groups.py`](#mat_utils--emissive_groups) — Emissive Groups' Maya half: one ``objectSet`` of faces per group, keyable
- [`mat_utils/game_shader.py`](#mat_utils--game_shader)
- [`mat_utils/image_to_plane/_image_to_plane.py`](#mat_utils--image_to_plane--_image_to_plane) — Map image files to textured polygon planes in Maya.
- [`mat_utils/marmoset_bridge/_marmoset_bridge.py`](#mat_utils--marmoset_bridge--_marmoset_bridge) — Maya-side glue for the Marmoset Toolbag engine.
- [`mat_utils/marmoset_bridge/_marmoset_engine.py`](#mat_utils--marmoset_bridge--_marmoset_engine) — Drive Marmoset Toolbag from the outside -- launch + templated automation.
- [`mat_utils/marmoset_bridge/_toolbag_helpers.py`](#mat_utils--marmoset_bridge--_toolbag_helpers) — Shared helpers for Marmoset Toolbag template scripts.
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
- [`mat_utils/render_opacity/attribute_mode.py`](#mat_utils--render_opacity--attribute_mode)
- [`mat_utils/render_opacity/channels.py`](#mat_utils--render_opacity--channels) — The per-object render-effect channel table.
- [`mat_utils/render_opacity/material_mode.py`](#mat_utils--render_opacity--material_mode) — Clean up what the retired viewport "material mode" left in a scene.
- [`mat_utils/render_opacity/render_effects.py`](#mat_utils--render_opacity--render_effects)
- [`mat_utils/shader_attribute_map.py`](#mat_utils--shader_attribute_map) — Logical texture channel -> per-shader (attribute, output plug), and the one
- [`mat_utils/shader_converter.py`](#mat_utils--shader_converter) — Retype a material in place — legacy Maya shaders to an exportable PBR one.
- [`mat_utils/shader_templates/_shader_templates.py`](#mat_utils--shader_templates--_shader_templates)
- [`mat_utils/substance_bridge/_substance_bridge.py`](#mat_utils--substance_bridge--_substance_bridge) — Substance 3D Painter bridge -- export Maya selection and hand off to Painter.
- [`mat_utils/substance_bridge/_substance_engine.py`](#mat_utils--substance_bridge--_substance_engine) — Drive Substance 3D Painter from the outside -- the DCC-free half of the bridge.
- [`mat_utils/substance_bridge/connection.py`](#mat_utils--substance_bridge--connection) — Substance 3D Painter connection module.
- [`mat_utils/substance_bridge/parameters.py`](#mat_utils--substance_bridge--parameters) — Registry of user-tunable Substance Painter parameters exposed to the bridge UI.
- [`mat_utils/substance_bridge/substance_rpc/client.py`](#mat_utils--substance_bridge--substance_rpc--client) — HTTP RPC client for the Painter-side ``substance_rpc`` plugin.
- [`mat_utils/substance_bridge/substance_rpc/installer.py`](#mat_utils--substance_bridge--substance_rpc--installer) — Install the substance_rpc plugin into Painter's user plugin folder.
- [`mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/__init__.py`](#mat_utils--substance_bridge--substance_rpc--plugin_src--substance_rpc--__init__) — Substance 3D Painter RPC plugin -- entry point.
- [`mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/_rpc_core.py`](#mat_utils--substance_bridge--substance_rpc--plugin_src--substance_rpc--_rpc_core) — The in-application half of the RPC pair: registry + marshaller + server.
- [`mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/project_ops.py`](#mat_utils--substance_bridge--substance_rpc--plugin_src--substance_rpc--ops--project_ops) — Project-level ops: inspect the open project and reload its mesh.
- [`mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/setup_ops.py`](#mat_utils--substance_bridge--substance_rpc--plugin_src--substance_rpc--ops--setup_ops) — Project-setup ops: resolution, the baking high poly, and mesh maps.
- [`mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/system_ops.py`](#mat_utils--substance_bridge--substance_rpc--plugin_src--substance_rpc--ops--system_ops) — Painter-specific system ops: version reporting and script evaluation.
- [`mat_utils/substance_bridge/templates/bake_lighting.py`](#mat_utils--substance_bridge--templates--bake_lighting) — Import the FBX as a new project, then bake Iray lighting into diffuse.
- [`mat_utils/substance_bridge/templates/import.py`](#mat_utils--substance_bridge--templates--import) — Send the FBX to Painter as a new project.
- [`mat_utils/substance_bridge/templates/reimport.py`](#mat_utils--substance_bridge--templates--reimport) — Update the running Painter's open project from a fresh FBX export.
- [`mat_utils/substance_bridge/templates/render.py`](#mat_utils--substance_bridge--templates--render) — Render the current Painter project via Iray (JS body unverified).
- [`mat_utils/texture_baker.py`](#mat_utils--texture_baker) — Bake an object's shaded surface (material under scene lighting) to a texture.
- [`mat_utils/texture_path_editor.py`](#mat_utils--texture_path_editor) — Texture Path Editor engine: the scene's texture paths and the commands that fix them.
- [`node_utils/_node_utils.py`](#node_utils--_node_utils)
- [`node_utils/attributes/_attributes.py`](#node_utils--attributes--_attributes) — Consolidated attribute utilities for Maya.
- [`node_utils/attributes/channels/_channels.py`](#node_utils--attributes--channels--_channels) — Channels — Maya attribute query / mutation logic.
- [`node_utils/data_nodes.py`](#node_utils--data_nodes) — The Maya scene store: two carrier nodes behind ``ptk.SceneStoreBase``.
- [`nurbs_utils/_nurbs_utils.py`](#nurbs_utils--_nurbs_utils)
- [`nurbs_utils/curve_to_tube.py`](#nurbs_utils--curve_to_tube) — Sweep a circular profile along NURBS curve(s) to build a tube.
- [`nurbs_utils/image_tracer.py`](#nurbs_utils--image_tracer)
- [`render_utils/_render_utils.py`](#render_utils--_render_utils) — Render-control helpers.
- [`rig_utils/_rig_utils.py`](#rig_utils--_rig_utils)
- [`rig_utils/articulated_rig/_articulated_rig.py`](#rig_utils--articulated_rig--_articulated_rig) — Articulated Rig engine -- rigid parts on hinge, swivel, ball and slide joints.
- [`rig_utils/articulated_rig/_solver_expression.py`](#rig_utils--articulated_rig--_solver_expression) — The end control's solve as a Maya expression: a MEL port of
- [`rig_utils/articulated_rig/grab_tool.py`](#rig_utils--articulated_rig--grab_tool) — The viewport grab for articulated rigs: press on a rigged part, drag, let go.
- [`rig_utils/controls.py`](#rig_utils--controls)
- [`rig_utils/rig_graph_build.py`](#rig_utils--rig_graph_build) — Build a RigGraph in Maya -- Maya as the TARGET of the rig-transfer stack.
- [`rig_utils/rig_graph_extract.py`](#rig_utils--rig_graph_extract) — Read a Maya rig into a RigGraph -- phase 2 of the rig-transfer stack.
- [`rig_utils/shadow_rig/_shadow_rig.py`](#rig_utils--shadow_rig--_shadow_rig) — Shadow Rig engine — a projected-shadow rig for engine export.
- [`rig_utils/shadow_rig/shadow_preview.py`](#rig_utils--shadow_rig--shadow_preview) — A live Viewport 2.0 preview of a horizon rig: the artist drags the light
- [`rig_utils/skinning.py`](#rig_utils--skinning) — Skinning utilities: binding, batch weight I/O, transfer, procedural weights.
- [`rig_utils/telescope_rig.py`](#rig_utils--telescope_rig)
- [`rig_utils/tube_rig/_tube_rig.py`](#rig_utils--tube_rig--_tube_rig) — Tube Rig engine — rigs a tube-shaped mesh (hose, cable, piston, tail).
- [`rig_utils/tube_rig/strategies.py`](#rig_utils--tube_rig--strategies) — Tube Rig build strategies and the bundle they return.
- [`rig_utils/tube_rig/tube_path.py`](#rig_utils--tube_rig--tube_path) — Tube-mesh centerline extraction — pure geometry analysis, no scene objects.
- [`rig_utils/wheel_rig.py`](#rig_utils--wheel_rig)
- [`ui_utils/_ui_utils.py`](#ui_utils--_ui_utils)
- [`ui_utils/calculator.py`](#ui_utils--calculator)
- [`ui_utils/cancel_provider.py`](#ui_utils--cancel_provider) — Maya's answers to uitk's cancellation contract.
- [`ui_utils/channel_box.py`](#ui_utils--channel_box) — Programmatic access to Maya's Channel Box.
- [`ui_utils/hotkey_collisions.py`](#ui_utils--hotkey_collisions) — Maya hotkey collision checker for the uitk ShortcutEditor.
- [`ui_utils/maya_native_menus.py`](#ui_utils--maya_native_menus)
- [`ui_utils/maya_ui_handler.py`](#ui_utils--maya_ui_handler)
- [`ui_utils/node_icons.py`](#ui_utils--node_icons) — Reusable helper for resolving Maya node icons at runtime.
- [`ui_utils/style_setter/_style_setter.py`](#ui_utils--style_setter--_style_setter) — Match Maya's scriptable viewport colors to another DCC's look.
- [`uv_utils/_auto_unwrap.py`](#uv_utils--_auto_unwrap) — External auto-unwrap round-trip: OBJ out, engine, OBJ back, UVs transferred.
- [`uv_utils/_uv_budget.py`](#uv_utils--_uv_budget) — UV texture-budget analysis: measure the scene, plan with :class:`pythontk.UvBudget`.
- [`uv_utils/_uv_pack.py`](#uv_utils--_uv_pack) — The two engines behind :meth:`mayatk.UvUtils.pack_uvs`.
- [`uv_utils/_uv_utils.py`](#uv_utils--_uv_utils)
- [`uv_utils/rizom_bridge/_rizom_bridge.py`](#uv_utils--rizom_bridge--_rizom_bridge)
- [`uv_utils/rizom_bridge/parameters.py`](#uv_utils--rizom_bridge--parameters) — Registry of user-tunable RizomUV parameters exposed to the bridge UI.
- [`uv_utils/texture_combine/texture_combine.py`](#uv_utils--texture_combine--texture_combine) — Several materials into ONE: one atlas, one material, in place -- no duplicated meshes.
- [`uv_utils/texture_transfer.py`](#uv_utils--texture_transfer) — Transfer a mesh's textures from one UV layout to another -- no rays, no bake.
- [`xform_utils/_xform_utils.py`](#xform_utils--_xform_utils) — ``XformUtils`` -- the public index of mayatk's transform operations.
- [`xform_utils/matrices.py`](#xform_utils--matrices) — Matrix utilities for Maya rigging and animation.
- [`xform_utils/pivot_watcher.py`](#xform_utils--pivot_watcher) — Real-time pivot-change notifier built on :class:`ScriptJobManager`.

---

<a id="__init__"></a>
### `__init__.py`

- [`DEFAULT_INCLUDE`](mayatk/mayatk/__init__.py#L17) — constant
- [`MOVED_NAMES`](mayatk/mayatk/__init__.py#L268) — constant

<a id="anim_utils--_anim_utils"></a>
### `anim_utils/_anim_utils.py`

``AnimUtils`` -- the public index of mayatk's key and curve operations.

- [`STANDARD_TRANSFORM_ATTRS`](mayatk/mayatk/anim_utils/_anim_utils.py#L56) — constant
- **[`class AnimUtils(_AnimUtilsInternal, pythontk.HelpMixin)`](mayatk/mayatk/anim_utils/_anim_utils.py#L86)** — Animation utilities for Maya.
  - `AnimUtils.scene_animation_range() -> Tuple[float, float]` *(static)* — The scene's AUTHORED animation range, as ``(start, end)``.
  - `AnimUtils.normalize_optimize_level(cls, level)` *(class)* — The canonical :attr:`OPTIMIZE_LEVELS` key *level* names, or None for OFF.
  - `AnimUtils.resolve_optimize_level(cls, level: Union[bool, str, None]) -> Optional[Dict[str, Any]]` *(class)* — Resolve an optimization level into :meth:`optimize_keys` kwargs.
  - `AnimUtils.bake(cls, objects: Union[str, List[str]], attributes: Optional[Union[str, List[str]]] = None, time_range: Optional[Tuple[float, float]] = None, sample_by: float = 1.0, preserve_outside_keys: bool = True, simulation: bool = False, destination_layer: Optional[str] = None, remove_baked_attr_from_layer: bool = False, bake_on_override_layer: bool = False, minimize_rotation: bool = True, sparse_anim_curve_bake: bool = False, disable_implicit_control: bool = True, control_points: bool = False, shape: bool = False, only_keyed: bool = False) -> List[str]` *(class)* — Bake animation on specified objects and attributes with smart grouping.
  - `AnimUtils.objects_to_curves(objects: Union[str, List[str]], recursive: bool = False, *, through_blends: bool = True) -> List[str]` *(static)* — Converts objects into a list of animation curves.
  - `AnimUtils.get_anim_curves(cls, objects: Optional[List[str]] = None, selected_keys_only: bool = False, recursive: bool = False) -> List[str]` *(class)* — Get animation curves from objects, selected keys, or all scene curves.
  - `AnimUtils.snapshot_curves(cls, objects: Union[str, List[str]], recursive: bool = True) -> Dict[str, Any]` *(class)* — Stash every animation curve driving *objects*, so it can be put back.
  - `AnimUtils.restore_curves(cls, snapshot: Optional[Dict[str, Any]]) -> int` *(class)* — Put the animation captured by :meth:`snapshot_curves` back, exactly.
  - `AnimUtils.get_static_curves(cls, objects: List[str], value_tolerance: float = 1e-05, recursive: bool = False) -> List[str]` *(class)* — Detects static curves (curves with constant values) that are safe
  - `AnimUtils.get_redundant_flat_keys(cls, objects: List[str], value_tolerance: float = 1e-05, remove: bool = False, recursive: bool = False, *, time_range: Optional[Tuple[float, float]] = None, selected_only: bool = False) -> List[Tuple[Any, List[float]]]` *(class)* — Detects redundant flat keys in curves and optionally deletes them.
  - `AnimUtils.simplify_curve(cls, objects: List[str], value_tolerance: float = 0.001, *, recursive: bool = False, time_range: Optional[Tuple[float, float]] = None, selected_only: bool = False, on_delete: Optional[Callable[[str, List[float]], Any]] = None) -> List[str]` *(class)* — Simplify curves by removing keys that don't contribute to shape.
  - `AnimUtils.repair_corrupted_curves(cls, objects: Optional[Union[str, List[str]]] = None, recursive: bool = True, delete_corrupted: bool = False, fix_infinite: bool = True, fix_invalid_times: bool = True, time_range_threshold: float = 1000000.0, value_threshold: float = 1000000.0, quiet: bool = False, on_delete: Optional[Callable[[str, Optional[List[float]]], Any]] = None) -> Dict[str, Any]` *(class)* — Legacy wrapper maintained for backwards compatibility.
  - `AnimUtils.reduce_to_extremes(cls, objects: Optional[Union[str, List[str]]] = None, value_tolerance: float = 0.001, recursive: bool = True, quiet: bool = False, stats: Optional[dict] = None, max_error: Optional[float] = None, on_delete: Optional[Callable[[str, List[float]], Any]] = None) -> List[str]` *(class)* — Reduce baked curves to their shape-defining keys and refit the tangents.
  - `AnimUtils.optimize_keys(cls, objects: Union[str, List[str]], value_tolerance: float = 0.001, *, remove_flat_keys: bool = True, remove_static_curves: bool = True, simplify_keys: bool = False, recursive: bool = True, quiet: bool = False, stats: Optional[dict] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None, through_blends: bool = True, on_delete: Optional[Callable[[str, Optional[List[float]]], Any]] = None) -> List[str]` *(class)* — Optimize animation keys for the given objects by removing static curves,
  - `AnimUtils.keyed_nodes(objects: Union[str, List[str]]) -> List[str]` *(static)* — The subset of *objects* ``cmds.keyframe`` can find keys on.
  - `AnimUtils.has_keyframes(sources: Union[str, List[str]]) -> bool` *(static)* — Whether *sources* (objects or curves) carry any key at all.
  - `AnimUtils.keyframe_range(sources: Union[str, List[str]]) -> Optional[Tuple[float, float]]` *(static)* — ``(first, last)`` key time over *sources*, or ``None`` with no keys.
  - `AnimUtils.curve_key_spans(curves: List[str], windows: Sequence[Tuple[Optional[float], Optional[float]]]) -> List[Optional[Tuple[float, float]]]` *(static)* — Per window, the first and last key time over *curves* inside it.
  - `AnimUtils.get_key_curves(curves: List[str]) -> List['ptk.KeyCurve']` *(static)* — Read animation curves as host-free :class:`pythontk.KeyCurve` s.
  - `AnimUtils.get_keyframe_times(sources: Union[str, List[str]], mode: str = 'all', from_curves: Optional[bool] = None, as_range: bool = False, time_range: Optional[Tuple[float, float]] = None) -> Union[List[float], Tuple[float, float], None]` *(static)* — Get keyframe times from objects or curves with flexible filtering options.
  - `AnimUtils.get_driver_animation_range(node: str, driver_type: str = 'auto') -> List[float]` *(static)* — Get keyframe times from a driver node's animation or its targets.
  - `AnimUtils.get_tangent_info(attr_name: str, time: float) -> Dict[str, Any]` *(static)* — Get tangent information (types, angles, weights and lock) for a given attribute at a specific time.
  - `AnimUtils.set_tangent_info(attr_name: str, time: float, tangent_info: Dict[str, Any]) -> None` *(static)* — Restore tangent information on a keyframe.
  - `AnimUtils.step_keys(objects=None, keys=None, tangent: str = 'out', resolution_order: Optional[Tuple[str, ...]] = None) -> dict` *(static)* — Set stepped tangents on animation keys.
  - `AnimUtils.get_current_frame() -> float` *(static)* — The time slider's current frame: the read :meth:`set_current_frame`
  - `AnimUtils.set_current_frame(time: Optional[float] = None, update: bool = True, relative: bool = False, snap_mode: Optional[str] = None, invert_snap: bool = False) -> float` *(static)* — Set the current frame on the timeslider with optional snapping.
  - `AnimUtils.move_keys_to_frame(objects=None, frame=None, time_range=None, selected_keys_only=False, retain_spacing=False, channel_box_attrs_only=False, align: str = 'auto', on_replace: Optional[Callable[[str, List[float]], Any]] = None, on_move: Optional[Callable[[str, List[Tuple[float, float]]], Any]] = None)` *(static)* — Move keyframes to the given frame with comprehensive control options.
  - `AnimUtils.set_keys_for_attributes(objects, target_times=None, refresh_channel_box=False, **kwargs)` *(static)* — Sets keyframes for the specified attributes on given objects at given times.
  - `AnimUtils.filter_objects_with_keys(objects: Optional[Union[str, List[str]]] = None, keys: Optional[List[str]] = None) -> List[str]` *(static)* — Filter the given objects for those with specific keys set.
  - `AnimUtils.scene_has_animation() -> bool` *(static)* — True if the scene contains any time-based animation a playblast would capture.
  - `AnimUtils.adjust_key_spacing(cls, objects: Optional[List[str]] = None, spacing: int = 1, time: Optional[int] = 0, relative: bool = True, preserve_keys: bool = False, selected_keys_only: bool = False, exact_gap: bool = False, prevent_collisions: bool = True, on_replace: Optional[Callable[[str, List[float]], Any]] = None, on_move: Optional[Callable[[str, List[Tuple[float, float]]], Any]] = None)` *(class)* — Adjusts the spacing between keyframes for specified objects at a given time,
  - `AnimUtils.add_intermediate_keys(objects: Union[str, List[str]], time_range: Optional[Union[int, Tuple[int, int]]] = None, percent: Optional[float] = None, include_flat: bool = False, ignore: Union[str, List[str], None] = None) -> None` *(static)* — Keys selected or animated attributes on given object(s) within a time range.
  - `AnimUtils.remove_intermediate_keys(objects: Union[str, List[str]], time_range: Optional[Union[int, Tuple[int, int]]] = None, ignore: Union[str, List[str], None] = None, attributes: Union[str, List[str], None] = None, on_delete: Optional[Callable[[str, List[float]], Any]] = None) -> int` *(static)* — Removes all intermediate keyframes, keeping only the first and last key on each attribute.
  - `AnimUtils.invert_keys(objects=None, time=None, relative=True, delete_original=False, mode='horizontal', value_pivot=0.0, on_replace: Optional[Callable[[str, List[float]], Any]] = None, on_move: Optional[Callable[[str, List[Tuple[float, float]]], Any]] = None)` *(static)* — Invert keyframes, preferring selected keys over all keys.
  - `AnimUtils.align_selected_keyframes(objects: Optional[List[str]] = None, target_frame: Optional[float] = None, use_earliest: bool = True, on_replace: Optional[Callable[[str, List[float]], Any]] = None, on_move: Optional[Callable[[str, List[Tuple[float, float]]], Any]] = None) -> bool` *(static)* — Aligns the starting keyframes of selected keyframes in the graph editor across multiple objects.
  - `AnimUtils.set_visibility_keys(objects: Optional[List[str]] = None, visible: bool = True, when: str = 'start', offset: int = 0, group_overlapping: bool = False, on_replace: Optional[Callable[[str, List[float]], Any]] = None) -> int` *(static)* — Sets visibility keyframes for objects with options for timing and grouping.
  - `AnimUtils.snap_keys_to_frames(objects: Optional[List[str]] = None, method: str = 'nearest', selected_only: bool = False, time_range: Optional[Tuple[float, float]] = None, include_driven: bool = False, through_blends: bool = True, on_move: Optional[Callable[[str, List[Tuple[float, float]]], Any]] = None) -> int` *(static)* — Snaps keyframes with decimal time values to whole frame numbers.
  - `AnimUtils.transfer_keyframes(cls, objects: List[str], relative: bool = False, transfer_tangents: bool = False, optimize: bool = False, on_replace: Optional[Callable[[str, List[float]], Any]] = None, on_delete: Optional[Callable[[str, Optional[List[float]]], Any]] = None)` *(class)* — Transfer keyframes from the first selected object to the subsequent objects.
  - `AnimUtils.parse_time_range(time: Union[None, int, str, Tuple, List]) -> Union[Tuple[float, float], None, List]` *(static)* — Parse time specification into a time range tuple for keyframe operations.
  - `AnimUtils.delete_keys(objects=None, *attributes, time=None, channel_box_only=False, on_delete: Optional[Callable[[str, List[float]], Any]] = None)` *(static)* — Deletes keyframes for specified attributes on given objects, optionally within a time range.
  - `AnimUtils.select_keys(objects: Optional[List[str]] = None, *attributes: str, time: Union[None, int, str, Tuple, List] = None, channel_box_only: bool = False, add_to_selection: bool = False) -> int` *(static)* — Selects keyframes for specified attributes on given objects, optionally within a time range.
  - `AnimUtils.get_frame_ranges(objects: List[str], precision: Optional[int] = None, gap: Optional[int] = None) -> Dict[str, List[Tuple[int, int]]]` *(static)* — Calculate frame ranges for a list of objects based on their keyframes.
  - `AnimUtils.get_tied_keyframes(objects: Optional[List[str]] = None, tolerance: float = 1e-05) -> Dict[str, Dict[str, List[float]]]` *(static)* — Detects tied (bookend) keyframes for given objects.
  - `AnimUtils.insert_keys(objects: Union[str, List[str]], times: Iterable[float], tolerance: float = 0.0001, report: bool = False)` *(static)* — Insert keys at *times* WITHOUT changing what any curve evaluates to.
  - `AnimUtils.tie_keyframes(objects: List[str] = None, absolute: bool = False, padding: int = 0, custom_range: Optional[Tuple[float, float]] = None)` *(static)* — Ties the keyframes of all given objects (or all keyed objects in the scene if none are provided)
  - `AnimUtils.untie_keyframes(objects: List[str] = None, on_delete: Optional[Callable[[str, List[float]], Any]] = None) -> Dict[str, Dict[str, List[float]]]` *(static)* — Removes bookend keyframes added by tie_keyframes, but preserves genuine animation keys.
  - `AnimUtils.create_animation_layer(name: str = 'AnimLayer', override: bool = True, additive: bool = False, attributes: Optional[List[str]] = None, objects: Optional[List[str]] = None, weight: float = 1.0, mute: bool = False, solo: bool = False, lock: bool = False, preferred: bool = True, parent: Optional[str] = None, unique_name: bool = True, timestamp_suffix: bool = False, color: Optional[Tuple[float, float, float]] = None) -> str` *(static)* — Create an animation layer with flexible configuration options.
  - `AnimUtils.get_animation_layers(include_base: bool = False, muted_only: bool = False, active_only: bool = False) -> List[str]` *(static)* — Get all animation layers in the scene.
  - `AnimUtils.copy_keys(objects=None, mode: str = 'auto', resolution_order: Optional[Tuple[str, ...]] = None, tangent_detail: bool = False) -> Dict[str, Dict[str, Any]]` *(static)* — Copy attribute values from objects for later pasting as keys.
  - `AnimUtils.paste_keys(objects=None, copied_data: Optional[Dict[str, Dict[str, Any]]] = None, target_time=None, match_source: bool = True, refresh_channel_box: bool = True, on_replace: Optional[Callable[[str, List[float]], Any]] = None, **kwargs) -> int` *(static)* — Paste previously copied attribute values as keyframes.
  - `AnimUtils.delete_animation_layer(layer: str, merge_to_base: bool = False) -> bool` *(static)* — Delete an animation layer.
  - `AnimUtils.fit_playback_range(objects=None, padding: float = 0) -> bool` *(static)* — Set the playback range to encompass keyframes on all (or given) scene objects.
  - `AnimUtils.euler_filter(objects: Union[str, List[str]]) -> List[str]` *(static)* — Unflip rotation curves: Maya's Euler filter, as the Graph Editor's
  - `AnimUtils.shift_key_values(edits: Dict[str, List[Tuple[float, float]]]) -> int` *(static)* — Offset key VALUES in place: ``{curve: [(time, delta), ...]}``.
  - `AnimUtils.clear_key_selection() -> None` *(static)* — Deselect every key, whatever is or is not selected.
  - `AnimUtils.get_selected_key_times(curves: Optional[List[str]] = None) -> Dict[str, List[float]]` *(static)* — Graph Editor key selection as ``{curve: [times]}``.
  - `AnimUtils.get_timeline_selection() -> Optional[Tuple[float, float]]` *(static)* — The time slider's drag-selected range, or ``None`` when nothing is selected.
  - `AnimUtils.create_preview_layer(sources: Dict[str, str], gate: Optional[Tuple[float, float]] = None, name: str = 'previewLayer') -> str` *(static)* — Play foreign curves on an object's plugs through a throwaway override layer.
  - `AnimUtils.remove_preview_layer(layer: Optional[str]) -> bool` *(static)* — Delete a layer made by :meth:`create_preview_layer`;
  - `AnimUtils.resolve_keys(cls, objects=None, mode: str = 'auto', resolution_order: Optional[Tuple[str, ...]] = None) -> Dict[str, Any]` *(class)* — Resolve which animation keys to operate on.
  - `AnimUtils.tangent_half(crv: str, t: Optional[float], side: str) -> Optional[Tuple[str, float, float]]` *(static)* — ``(type, angle, weight)`` of the *side* (``"in"``/``"out"``) half of
  - `AnimUtils.exact_scale(cls)` *(class)* — ``scaleKey`` lands every key where it computes it, inside the block.
  - `AnimUtils.move_curve_keys(cls, curve: str, time_pairs: List[Tuple[float, ...]], tolerance: float = 0.0001, allow_merge: bool = False, on_replace=None, on_move=None) -> int` *(class)* — Move keys on a curve to new times, preserving value and tangents.
  - `AnimUtils.hold_segments(curve: str, times: List[float]) -> List[bool]` *(static)* — Per key segment of *curve* (``times[i]`` to ``times[i + 1]``), True

<a id="anim_utils--_tied_keys"></a>
### `anim_utils/_tied_keys.py`

Bookend (tied) keys behind :class:`mayatk.AnimUtils`.

- [`TIED_KEYS_ATTR`](mayatk/mayatk/anim_utils/_tied_keys.py#L21) — constant

<a id="anim_utils--blendshape_animator--_blendshape_animator"></a>
### `anim_utils/blendshape_animator/_blendshape_animator.py`

Main workflow facade for blendShape morph-animation creation, editing, and export.

- **[`class BlendshapeAnimator(pythontk.LoggingMixin)`](mayatk/mayatk/anim_utils/blendshape_animator/_blendshape_animator.py#L32)** — Main workflow facade for blendShape animations.
  - `BlendshapeAnimator.create(self, base_mesh: Optional[str] = None, target_mesh: Optional[str] = None, start_frame: Optional[int] = None, end_frame: Optional[int] = None, name: str = 'morph', test_setup: bool = True) -> bool` — Set up basic morph animation between two meshes.
  - `BlendshapeAnimator.edit_weight_based(self, weights: Optional[List[float]] = None, count: int = 3, weight_range: Tuple[float, float] = (0.0, 1.0), group_name: Optional[str] = None, name_prefix: Optional[str] = None) -> List[Target]` — Create tweens at specific weights or evenly spaced.
  - `BlendshapeAnimator.edit_frame_based(self, frames: Optional[List[int]] = None, target_frame: Optional[int] = None, group_name: Optional[str] = None, name_prefix: Optional[str] = None) -> List[Target]` — Create tweens at specific animation frames.
  - `BlendshapeAnimator.edit_apply_tweens(self, tweens: Optional[List[Target]] = None) -> List[Target]` — Apply tween mesh edits back to blendShape.
  - `BlendshapeAnimator.has_setup(self) -> bool` — True while a setup is bound and its base mesh and blendShape still
  - `BlendshapeAnimator.selected_base(cls) -> Optional[str]` *(class)* — The first selected mesh that carries a blendShape -- the setup a
  - `BlendshapeAnimator.tween_topology(self) -> Tuple[Optional[int], List[Tuple[Target, Optional[int]]]]` — This setup's tweens with their vertex counts, beside the base mesh's
  - `BlendshapeAnimator.apply_tweens(self, tweens: Optional[List[Target]] = None, skip_duplicates: bool = True, validate_topology: bool = False) -> List[Tuple[Target, ApplyStatus]]` — Apply tween edits back to the blendShape as ONE undo step and report
  - `BlendshapeAnimator.delete_tweens(self, tweens: List[Target]) -> List[str]` — Delete tween meshes, and any default tween group they leave empty, as
  - `BlendshapeAnimator.select_tweens(tweens: List[Target]) -> List[str]` *(static)* — Select the tweens' meshes that still exist, replacing the selection.
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

- **[`class ApplyStatus(Enum)`](mayatk/mayatk/anim_utils/blendshape_animator/applicator.py#L19)**
- **[`class Applicator(pythontk.LoggingMixin)`](mayatk/mayatk/anim_utils/blendshape_animator/applicator.py#L25)** — Applies tween mesh edits back to blendShape in-between targets.
  - `Applicator.validate_topology(self, tweens: List[Target]) -> List[Target]` — Filter ``tweens`` to those matching base mesh vertex count.
  - `Applicator.apply_tweens(self, tweens: Optional[List[Target]] = None, skip_duplicates: bool = True, validate_topology: bool = False) -> List[Tuple[Target, ApplyStatus]]` — Apply tween mesh edits to blendShape in-between targets.

<a id="anim_utils--blendshape_animator--creator"></a>
### `anim_utils/blendshape_animator/creator.py`

Creates in-between target meshes for custom blendShape animation curves.

- **[`class Creator(pythontk.LoggingMixin)`](mayatk/mayatk/anim_utils/blendshape_animator/creator.py#L21)** — Creates in-between target meshes for custom animation curves.
  - `Creator.create_weight_based_tweens(self, weights: List[float], group_name: str = Targets.DEFAULT_GROUPS[0], name_prefix: str = 'morph_ib') -> List[Target]` — Create tween meshes at specific weight values.
  - `Creator.create_frame_based_tween(self, target_frame: int, group_name: str = Targets.DEFAULT_GROUPS[0], name_prefix: str = 'tween') -> Optional[Target]` — Create a tween mesh at a specific animation frame.
  - `Creator.tag_tween_mesh(self, mesh: str, weight: float, target_frame: Optional[int] = None) -> None` — Add metadata attributes to ``mesh``.
  - `Creator.get_existing_weights(self) -> Set[float]` — Return the in-between weights already taken by THIS setup.
  - `Creator.find_nearby_weight(self, target_weight: float, existing_weights: Set[float], tolerance: float = 0.01) -> Optional[float]` — Find a free weight within ``tolerance`` of ``target_weight``.

<a id="anim_utils--blendshape_animator--history"></a>
### `anim_utils/blendshape_animator/history.py`

Construction-history queries shared by the blendshape_animator modules.

- **[`class BlendshapeHistory`](mayatk/mayatk/anim_utils/blendshape_animator/history.py#L13)** — BlendshapeHistory — construction-history lookups (the ``blendShape``
  - `BlendshapeHistory.list_history(node: str, type_filter: Optional[str] = None) -> List[str]` *(static)* — List the construction history of a node, optionally filtered by node type.

<a id="anim_utils--blendshape_animator--keyframes"></a>
### `anim_utils/blendshape_animator/keyframes.py`

Core blendShape keyframe animation operations.

- **[`class Keyframes(pythontk.LoggingMixin)`](mayatk/mayatk/anim_utils/blendshape_animator/keyframes.py#L17)** — Core blendShape animation functionality.
  - `Keyframes.weight_attr(self) -> str` *(property)* — The blendShape weight attribute this setup animates (e.g.
  - `Keyframes.create_keyframes(self, start_frame: int, end_frame: int) -> bool` — Create linear keyframe animation from weight 0.0 -> 1.0.
  - `Keyframes.test_morph(self) -> bool` — Test the blendShape by temporarily setting weight to 0.5.
  - `Keyframes.get_frame_range(self) -> Tuple[int, int]` — Return (start, end) frame range from keyframes on the weight attr.

<a id="anim_utils--blendshape_animator--recovery"></a>
### `anim_utils/blendshape_animator/recovery.py`

Recovery utilities for corrupted blendShape setups.

- **[`class Recovery(pythontk.LoggingMixin)`](mayatk/mayatk/anim_utils/blendshape_animator/recovery.py#L19)** — Utilities for recovering from corrupted blendShape setups.
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
- **[`class Targets(pythontk.LoggingMixin)`](mayatk/mayatk/anim_utils/blendshape_animator/target.py#L68)** — Manages collections of tween meshes.
  - `Targets.find_all_targets(cls, blendshape: Optional[str] = None, base_mesh: Optional[str] = None) -> List[Target]` *(class)* — Find all tween meshes in the scene (deduplicated).
  - `Targets.group_by_weight(cls, tweens: List[Target]) -> Dict[float, List[Target]]` *(class)* — Group tweens by weight value, handling duplicates.
  - `Targets.update_all_references(cls, new_blendshape: str, new_base_mesh: str) -> int` *(class)* — Rebind tween references after a blendShape rebuild.

<a id="anim_utils--blendshape_animator--validator"></a>
### `anim_utils/blendshape_animator/validator.py`

Mesh and blendShape validation for blendShape animation setup.

- **[`class Validator(pythontk.LoggingMixin)`](mayatk/mayatk/anim_utils/blendshape_animator/validator.py#L15)** — Handles validation of meshes and blendShape setups.
  - `Validator.validate_meshes(cls, mesh1: str, mesh2: str) -> bool` *(class)* — Validate that both objects are compatible meshes.
  - `Validator.validate_blendshape(cls, blendshape: str, weight_index: int = 0) -> bool` *(class)* — Validate blendShape node configuration.

<a id="anim_utils--key_stash--_key_stash"></a>
### `anim_utils/key_stash/_key_stash.py`

Key Stash — park keyframes outside the working animation, retrieve later (Maya).

- **[`class KeyStash(pythontk.core_utils.engines.key_stash.key_stash_model.KeyStash, _KeyStashInternal)`](mayatk/mayatk/anim_utils/key_stash/_key_stash.py#L238)** — :class:`pythontk.KeyStash` with the scene side bound to Maya.
  - `KeyStash.active(cls) -> 'KeyStash'` *(class)* — The active store, auto-installing the Maya backend once.
  - `KeyStash.watch_undo(cls, owner, widget=None) -> List[int]` *(class)* — Keep the loaded store in step with undo and redo, for *owner*.
  - `KeyStash.reconcile(self) -> List[int]` — Bring the record in line with the scene.
  - `KeyStash.merge_carrier(cls, carriers, other, ctx) -> None` *(class)* — Another scene's parked clips merged into the record: reload the
  - `KeyStash.discard_carrier(cls, carriers, other, ctx) -> None` *(class)* — Another scene's parked clips were dropped: delete their parked
  - `KeyStash.stash(self, objects: Optional[Sequence[str]] = None, time_range: Optional[Tuple[float, float]] = None, selected_keys: bool = False, attributes: Optional[Sequence[str]] = None, label: Optional[str] = None, source_shot_id: Optional[int] = None, metadata: Optional[Dict[str, Any]] = None, targets: Optional[Sequence[Tuple[str, Any, float, float]]] = None) -> Optional[StashedClip]` — Move keys off the working animation into a stored clip.
  - `KeyStash.retrieve(self, clip_id: int, at: Optional[float] = None, mode: str = 'merge', target: Optional[str] = None) -> int` — Put a stored clip's keys back and forget the clip.
  - `KeyStash.drop(self, clip_id: int) -> None` — Discard a stored clip and delete its stash nodes.
  - `KeyStash.stash_from(self, source: str) -> Optional[StashedClip]` — :meth:`stash` the keys one of :attr:`STASH_SOURCES` names, read
  - `KeyStash.select_clip_objects(self, clip_id: int) -> List[str]` — Select a stored clip's objects that still exist, replacing the
  - `KeyStash.preview(self, clip_id: int, in_context: bool = True, set_playback_range: bool = True) -> str` — Play a stored clip on its objects without retrieving it.
  - `KeyStash.end_preview(self) -> bool` — Tear the preview layer down and restore the time slider.

<a id="anim_utils--playblast_exporter"></a>
### `anim_utils/playblast_exporter.py`

Playblast capture, encoding, and preview-render exports for Maya.

- **[`class PlayblastExporter(pythontk.SequenceExporter)`](mayatk/mayatk/anim_utils/playblast_exporter.py#L60)** — Viewport capture and preview-render exports.
  - `PlayblastExporter.scene_name() -> str` *(static)* — Basename of the current scene without extension;
  - `PlayblastExporter.scene_fps() -> float` *(static)* — The scene frame rate as a float.
  - `PlayblastExporter.sequence_name(self) -> str`
  - `PlayblastExporter.sequence_fps(self) -> float`
  - `PlayblastExporter.resolve_sound_node() -> Optional[str]` *(static)* — The timeline's active audio node, or the scene's sole audio node.
  - `PlayblastExporter.capture_sequence(self, directory: str, prefix: Optional[str] = None, start: Optional[int] = None, end: Optional[int] = None, camera: Optional[str] = None, image_format: str = 'png', **overrides: Any) -> CaptureResult` — Capture the frame range as a numbered image sequence.
  - `PlayblastExporter.capture_still(self, filepath: str, frame: Optional[int] = None, camera: Optional[str] = None, image_format: str = 'png', **overrides: Any) -> str` — Capture a single frame to an exact filepath (default: current frame).
  - `PlayblastExporter.capture_movie(self, filepath: str, fmt: str = 'avi', compression: str = 'none', start: Optional[int] = None, end: Optional[int] = None, camera: Optional[str] = None, sound: Optional[str] = None, **overrides: Any) -> str` — Capture with Maya's native movie playblast (``avi``/``movie``).
  - `PlayblastExporter.render_with_arnold(self, output_dir: str, start: Optional[int] = None, end: Optional[int] = None, camera: Optional[str] = None, prefix: Optional[str] = None, frame_padding: Optional[int] = None, render_layer: Optional[str] = None, **kwargs: Any) -> List[str]` — Render a frame range with Arnold;

<a id="anim_utils--scale_keys"></a>
### `anim_utils/scale_keys.py`

Dedicated scale-keys module to keep AnimUtils lean and testable.

- **[`class ScaleKeys`](mayatk/mayatk/anim_utils/scale_keys.py#L20)** — Encapsulates scale_keys logic for clarity and focused testing.
  - `ScaleKeys.execute(self) -> int`
  - `ScaleKeys.scale_keys(cls, **kwargs) -> int` *(class)* — Scale keyframes uniformly or via motion-aware retiming.

<a id="anim_utils--segment_keys"></a>
### `anim_utils/segment_keys.py`

- **[`class SegmentKeysInfo`](mayatk/mayatk/anim_utils/segment_keys.py#L23)** — Mixin for reporting animation segment information.
  - `SegmentKeysInfo.get_time_ranges(segments: List[Dict[str, Any]]) -> List[Tuple[str, float, float]]` *(static)* — Extract time ranges from segment data.
  - `SegmentKeysInfo.print_time_ranges(cls, source: Union[List[Dict[str, Any]], List[Tuple[str, float, float]]], header: Optional[str] = None, per_segment: bool = False, object_fmt: Optional[str] = None, segment_fmt: Optional[str] = None, by_time: bool = False, csv_output: bool = False)` *(class)* — Print formatted time ranges to stdout.
  - `SegmentKeysInfo.format_time_ranges_text(cls, source: Union[List[Dict[str, Any]], List[Tuple[str, float, float]]], **kwargs) -> str` *(class)* — Return the same output as :meth:`print_time_ranges` as a
  - `SegmentKeysInfo.format_time_ranges_html(cls, source: Union[List[Dict[str, Any]], List[Tuple[str, float, float]]], title: Optional[str] = None, **kwargs) -> str` *(class)* — Wrap :meth:`format_time_ranges_text` in styled HTML suitable
- **[`class SegmentKeys(SegmentKeysInfo)`](mayatk/mayatk/anim_utils/segment_keys.py#L270)** — Shared helper for collecting and grouping animation segments.
  - `SegmentKeys.collect_segments(cls, objects: List[Any], ignore: Optional[Union[str, List[str]]] = None, split_static: bool = False, selected_keys_only: bool = False, channel_box_attrs: Optional[List[str]] = None, static_tolerance: float = 0.0001, time_range: Optional[Tuple[Optional[float], Optional[float]]] = None, ignore_visibility_holds: bool = False, ignore_holds: bool = False, exclude_next_start: bool = True, motion_only: bool = False, motion_rate: float = 0.001, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> List[Dict[str, Any]]` *(class)* — Collect animation segments from objects.
  - `SegmentKeys.get_scene_info(cls, objects: Optional[List[str]] = None, detailed: bool = True, ignore_holds: bool = True, traversal: Optional[str] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> List[Dict[str, Any]]` *(class)* — Collect animation segments for the scene info report.
  - `SegmentKeys.format_scene_info_text(cls, objects: Optional[List[str]] = None, detailed: bool = True, csv_output: bool = False, by_time: bool = False, ignore_holds: bool = True, traversal: Optional[str] = None) -> str` *(class)* — Plain-text scene-info report.
  - `SegmentKeys.format_scene_info_html(cls, objects: Optional[List[str]] = None, detailed: bool = True, csv_output: bool = False, by_time: bool = False, ignore_holds: bool = True, traversal: Optional[str] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> str` *(class)* — HTML scene-info report for ``sb.text_view_dialog``.
  - `SegmentKeys.print_scene_info(cls, objects: Optional[List[str]] = None, detailed: bool = True, csv_output: bool = False, by_time: bool = False, ignore_holds: bool = True)` *(class)* — Print animation info to stdout.
  - `SegmentKeys.group_segments(cls, segments: List[Dict[str, Any]], mode: str = 'per_segment', **kwargs) -> List[Dict[str, Any]]` *(class)* — Group segments based on the specified mode.
  - `SegmentKeys.merge_groups_sharing_curves(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]` *(static)* — Merge groups that share any animation curves.
  - `SegmentKeys.shift_curves(curves: List[Any], offset: float, time_range: Optional[Tuple[float, float]] = None, remove_flat_at_dest: bool = False, on_replace: Optional[Callable[[str, List[float]], Any]] = None, on_move: Optional[Callable[[str, List[Tuple[float, float]]], Any]] = None)` *(static)* — Shift keys on curves by offset in a single relative move,
  - `SegmentKeys.execute_stagger(cls, groups_data: List[dict], start_frame: float, spacing: Union[int, float] = 0, use_intervals: bool = False, avoid_overlap: bool = False, preserve_gaps: bool = False, on_replace: Optional[Callable[[str, List[float]], Any]] = None, on_move: Optional[Callable[[str, List[Tuple[float, float]]], Any]] = None)` *(class)* — Calculate and execute staggering on groups of segments.

<a id="anim_utils--shots--_detection"></a>
### `anim_utils/shots/_detection.py`

Shot-region detection — Maya scene acquisition over the pure engine math.

- [`CONTENT_ATTRS`](mayatk/mayatk/anim_utils/shots/_detection.py#L32) — constant
- **[`class Detection(_DetectionInternal)`](mayatk/mayatk/anim_utils/shots/_detection.py#L130)** — Detection — module namespace.
  - `Detection.curve_moves_in(crv: str, start: float, end: float, value_tolerance: float = 0.0001, evaluated: bool = False) -> bool` *(static)* — True when *crv* has keys inside ``[start, end]`` whose values vary
  - `Detection.resolve_to_transform(node, cache=None, _depth=0)` *(static)* — Resolve a curve-destination node to its owning transform.
  - `Detection.terminal_destinations(cls, node, _depth=0)` *(class)* — Yield ``(attr, node)`` for the terminal plugs downstream of *node*.
  - `Detection.transform_from_curve_names(cls, leaf_name, curves=None)` *(class)* — Resolve a VANISHED node name through the curves Maya named after it.
  - `Detection.first_standard_destination(cls, crv)` *(class)* — First ``(attr, node)`` of *crv* landing on a content attribute.
  - `Detection.detect_shot_regions(objects: Optional[List[str]] = None, gap_threshold: float = 5.0, ignore: Optional[str] = None, motion_rate: float = 0.001, min_duration: float = 2.0) -> List[Dict[str, Any]]` *(static)* — Detect animation regions by clustering per-object segments.
  - `Detection.regions_from_selected_keys(gap_threshold: float = 5.0, key_filter: str = 'all') -> List[Dict[str, Any]]` *(static)* — Build shot regions from currently selected keyframes.

<a id="anim_utils--shots--_shot_apply"></a>
### `anim_utils/shots/_shot_apply.py`

Commit resolved :class:`MovePlan`\ s to the Maya scene.

- **[`class ShotApply(_ShotApplyInternal)`](mayatk/mayatk/anim_utils/shots/_shot_apply.py#L329)** — ShotApply — module namespace.
  - `ShotApply.pin_shot_bounds(store: ShotStore, objects: Iterable[str], report: bool = False)` *(static)* — Give every shot a key on both of its bounds, changing nothing.
  - `ShotApply.retime_gaps(retimes: Iterable[Any], objects: Iterable[str], after_move: bool, ledger: Optional[Any] = None) -> int` *(static)* — Scale each gap's content into the width the plan gives that gap.
  - `ShotApply.apply(store: ShotStore, plan: MovePlan, progress_callback: Optional[Callable[[int, int, str], None]] = None, objects: Optional[Iterable[str]] = None) -> None` *(static)* — Execute ``plan`` against the scene and ``store``.

<a id="anim_utils--shots--_shots"></a>
### `anim_utils/shots/_shots.py`

Maya shot-store adapter — the DCC layer over ``pythontk``'s shots engine.

- **[`class MayaScenePersistence`](mayatk/mayatk/anim_utils/shots/_shots.py#L103)** — Persist ShotStore data to a string channel on ``data_internal``.
  - `MayaScenePersistence.store_cls(self)` *(property)* — The store class this backend serves (resolved lazily: ``ShotStore``
  - `MayaScenePersistence.save(self, data: Dict[str, Any], undoable: bool = False) -> None` — Write *data* to the channel.
  - `MayaScenePersistence.load(self) -> Optional[Dict[str, Any]]` — The stored record, or ``None`` when the scene holds none.
  - `MayaScenePersistence.record_changed(self) -> bool` — Whether the channel differs from what this backend last wrote or read.
  - `MayaScenePersistence.remove_callbacks(self) -> None` — Tear down every SJM subscription owned by this store.
- **[`class ShotStore(pythontk.ShotStore, _ShotStoreInternal)`](mayatk/mayatk/anim_utils/shots/_shots.py#L382)** — :class:`pythontk.ShotStore` with the scene hooks bound to Maya.
  - `ShotStore.active(cls) -> 'ShotStore'` *(class)* — Return the active store, auto-installing the Maya backend once.
  - `ShotStore.undo_queue_top(redo: bool = False) -> str` *(static)* — Name of the entry Maya's undo (or redo) would consume next.
  - `ShotStore.scene_edit(self, label: str = 'edit', snapshot: bool = True)` — Run a boundary-mutating edit as ONE named, undo-paired step.
  - `ShotStore.has_animation() -> bool` *(static)* — True if the scene contains animCurves driving transforms.
  - `ShotStore.detect_regions(self) -> List[Dict[str, Any]]` — Detect shot candidates using the store's detection settings.
  - `ShotStore.assess(self) -> Dict[int, str]` — Lightweight assessment: check if shot objects exist in the scene.
  - `ShotStore.member_nodes(self, name: str) -> List[str]` — Long names of every transform answering to a doc object *name*.
  - `ShotStore.publish_export_view(self, strategy: Optional[str] = None) -> Optional[str]` — Publish this store's shot records onto the shared ``data_export`` node.
  - `ShotStore.export_transfer(cls, spell=None, objects: Optional[List[str]] = None) -> Optional[Dict[str, Any]]` *(class)* — The active store as a hand-off ``shots`` section (``None`` when empty).
  - `ShotStore.apply_transfer(cls, section: Dict[str, Any], *, resolve=None, frame_offset: float = 0.0, replace: bool = False, converted=None, ctx: Optional['ptk.TransferContext'] = None) -> Optional['ShotStore']` *(class)* — Rebuild the scene's shots from a hand-off ``shots`` section.
  - `ShotStore.transfer_out(cls, ctx: 'ptk.TransferContext') -> Optional[Dict[str, Any]]` *(class)* — The ``shot_store`` record's hand-off payload: :meth:`export_transfer`
  - `ShotStore.transfer_in(cls, payload: Dict[str, Any], ctx: 'ptk.TransferContext') -> None` *(class)* — Land a received ``shots`` section: :meth:`apply_transfer`, names
  - `ShotStore.merge_carrier(cls, carriers, other, ctx) -> None` *(class)* — Another scene's shots merged into the record: reload the active
  - `ShotStore.discard_carrier(cls, carriers, other, ctx) -> None` *(class)* — Another scene's shots were dropped with their carrier: the same

<a id="anim_utils--shots--shot_manifest--_shot_manifest"></a>
### `anim_utils/shots/shot_manifest/_shot_manifest.py`

Maya Shot Manifest adapter — the DCC layer over pythontk's manifest engine.

- **[`class ShotManifest(pythontk.core_utils.engines.shots.manifest.manifest_engine.ShotManifest, _ShotManifestInternal)`](mayatk/mayatk/anim_utils/shots/shot_manifest/_shot_manifest.py#L118)** — :class:`pythontk.ShotManifest` with the scene hooks bound to Maya.
  - `ShotManifest.apply_behaviors(self) -> Dict[str, list]` — Apply detected behaviors to Maya objects (fades, highlights, audio
  - `ShotManifest.rewire_audio(tracks: Optional[List[str]] = None) -> Dict[str, List[str]]` *(static)* — Reconcile managed DG audio nodes with keyed track state.
  - `ShotManifest.from_csv(cls, filepath: str, store: Optional[ShotStore] = None, columns: Optional[ColumnMap] = None, post_process: Optional[Callable[[BuilderStep], None]] = None) -> Tuple['ShotManifest', List[BuilderStep]]` *(class)* — Convenience: parse a CSV and return a ready-to-build engine.

<a id="anim_utils--shots--shot_manifest--behaviors--_behaviors"></a>
### `anim_utils/shots/shot_manifest/behaviors/_behaviors.py`

Behaviors — Maya appliers over the engine's pure keying-recipe core.

- **[`class Behaviors(pythontk.core_utils.engines.shots.manifest.behaviors.Behaviors, _BehaviorsInternal)`](mayatk/mayatk/anim_utils/shots/shot_manifest/behaviors/_behaviors.py#L157)** — Behaviors — module namespace.
  - `Behaviors.apply_behavior(obj: str, behavior_name: str, start: float, end: float, attrs: Optional[List[str]] = None, search_path: Optional[Path] = None, source_path: str = '', anchor_override: Optional[str] = None, recipe: Optional[Any] = None, fps: Optional[float] = None) -> List[Tuple[str, float]]` *(static)* — Apply a named behavior template to an object over a time range.
  - `Behaviors.verify_behavior(obj: str, behavior_name: str, start: float, end: float, search_path: Optional[Path] = None, keyframe_fn: Optional[Any] = None, anchor_override: Optional[Any] = None, recipe: Optional[Any] = None, fps: Optional[float] = None) -> bool` *(static)* — Check whether expected behavior keyframes exist on an object.
  - `Behaviors.apply_audio_clip(obj: str, start: float, end: float, source_path: str = '') -> List[Tuple[str, float]]` *(static)* — Place an audio track's clip at *start* -- the Audio Clips panel's
  - `Behaviors.compute_duration(behavior_entries: List[Dict[str, str]], fallback: float = 30, fps: Optional[float] = None) -> float` *(static)* — Derive duration from the behavior templates in *behavior_entries*.
  - `Behaviors.apply_to_shots(shots: list, apply_fn, exists_fn=None, has_keys_fn=None, store=None, resolve_fn=None, conflict_fn=None, release_fn=None, skip_behaviors=()) -> Dict[str, list]` *(static)* — Apply declared behaviors from shot metadata to Maya objects.

<a id="anim_utils--shots--shot_manifest--manifest_host"></a>
### `anim_utils/shots/shot_manifest/manifest_host.py`

What the Shot Manifest panel needs from Maya: its store, engine, detection,

- **[`class ManifestHost`](mayatk/mayatk/anim_utils/shots/shot_manifest/manifest_host.py#L22)** — Maya's answers to the Shot Manifest panel's host calls.
  - `ManifestHost.store_cls()` *(static)* — The host's ``ShotStore`` class.
  - `ManifestHost.manifest_cls()` *(static)* — The host's manifest engine class.
  - `ManifestHost.detection()` *(static)* — The host's animation-region detection (``Detection``).
  - `ManifestHost.behaviors_cls()` *(static)* — The host's ``Behaviors`` (the recipes a manifest object's behaviors key).
  - `ManifestHost.range_resolver_cls()` *(static)* — The host's ``RangeResolver``: pythontk's, sizing audio steps to their clips.
  - `ManifestHost.available() -> bool` *(static)* — Whether the scene can be reached (``maya.cmds`` imports).
  - `ManifestHost.current_frame() -> Optional[float]` *(static)* — The playhead's frame, or ``None`` without a scene.
  - `ManifestHost.find_nodes(name: str, store) -> List[str]` *(static)* — The scene nodes doc object *name* answers to: the store's members,
  - `ManifestHost.reveal(nodes: List[str]) -> List[str]` *(static)* — Select *nodes* and reveal them in the Outliner.
  - `ManifestHost.leaf_name(name: str) -> str` *(static)* — *name*'s leaf: its DAG path dropped.
  - `ManifestHost.object_icon(obj, name: str)` *(static)* — The icon of a table row's object, or ``None``: *obj* is its

<a id="anim_utils--shots--shot_manifest--range_resolver"></a>
### `anim_utils/shots/shot_manifest/range_resolver.py`

Range resolution for the Shot Manifest build pipeline (Maya-bound facade).

- **[`class RangeResolver`](mayatk/mayatk/anim_utils/shots/shot_manifest/range_resolver.py#L28)** — RangeResolver — module namespace.
  - `RangeResolver.resolve_ranges(steps: List[BuilderStep], user_ranges: Dict[str, Tuple[Optional[float], Optional[float]]], gap_starts: List[float], gap_end_map: Dict[float, float], gap: float, use_selected_keys: bool, last_resolved: List[Tuple[str, float, Optional[float], bool]], from_step_idx: int = 0, default_duration: float = 0, duration_fn: Optional[Callable[..., float]] = None) -> List[Tuple[str, float, Optional[float], bool]]` *(static)* — Compute a resolved ``(start, end)`` for every step.

<a id="anim_utils--shots--shot_sequencer--_shot_sequencer"></a>
### `anim_utils/shots/shot_sequencer/_shot_sequencer.py`

Shot Sequencer — manages per-shot animation with ripple editing.

- **[`class ShotSequencer(pythontk.ShotSequencer)`](mayatk/mayatk/anim_utils/shots/shot_sequencer/_shot_sequencer.py#L62)** — Manages a :class:`ShotStore` and provides ripple editing and
  - `ShotSequencer.reconcile_all_shots(self) -> bool` — Point every name the store holds an object by at the node it is now.
  - `ShotSequencer.collect_object_segments(self, shot_id: int, ignore: Optional[str] = None, motion_rate: float = 0.001, ignore_holds: bool = True) -> List[Dict[str, Any]]` — Collect per-object animation segments within a shot's range.
  - `ShotSequencer.detect_shots(self, objects: Optional[List[str]] = None, gap_threshold: float = 5.0, ignore: Optional[str] = None, motion_rate: float = 0.001, min_duration: float = 2.0) -> List[Dict[str, Any]]` — Detect shot boundaries from existing animation on *objects*.
  - `ShotSequencer.move_attribute_keys(self, obj: str, attr: Optional[str], delta: float, times: Optional[List[float]] = None, window: Optional[tuple] = None) -> int` — Shift keys of *obj* by *delta* frames -- one attribute's, or all.
  - `ShotSequencer.copy_attribute_keys(self, obj: str, attr: Optional[str], delta: float, times: Optional[List[float]] = None, window: Optional[tuple] = None) -> int` — Copy keys of *obj* *delta* frames on -- one attribute's, or all --
  - `ShotSequencer.move_curve_keys(cls, crv: str, times: list, delta: float, plug: Optional[str] = None, eps: float = 0.001, ledger=None) -> None` *(class)* — Shift the keys of *crv* at *times* by *delta*, tangents intact.
  - `ShotSequencer.recreate_curve_keys(cls, crv: str, pairs: list, plug: Optional[str] = None, eps: float = 0.001, ledger=None) -> None` *(class)* — Cut the keys named by *pairs* and rebuild them at their new times.
  - `ShotSequencer.scale_object_keys(self, obj: str, old_start: float, old_end: float, new_start: float, new_end: float, _scaled: Optional[set] = None) -> None` — Scale (and optionally shift) keyframes of *obj* from
  - `ShotSequencer.scale_shot_keys(self, old_start: float, old_end: float, new_start: float, new_end: float) -> None` — Retime every key inside ``[old_start, old_end]`` into the new span.

<a id="anim_utils--shots--shot_sequencer--graph_sync"></a>
### `anim_utils/shots/shot_sequencer/graph_sync.py`

Graph editor glue: the shot sequencer's curves in uitk's graph editor (Maya).

- **[`class GraphSync`](mayatk/mayatk/anim_utils/shots/shot_sequencer/graph_sync.py#L39)** — Feeds the sequencer's graph editor and commits its edits (module docstring).
  - `GraphSync.detach(self) -> None` — Disconnect everything (a panel re-init builds a new one).
  - `GraphSync.sync(self, full: bool = False) -> None` — Bring the graph in line with the scene.
  - `GraphSync.outline(self) -> List[dict]` — The visible shots' objects keyed inside their window, and their curves.
  - `GraphSync.on_keys_edited(self, edits) -> None` — Commit a move / scale / typed stat: ``[(curve, [(t, dt, dv), ...])]``.
  - `GraphSync.on_tangents_edited(self, rows) -> None` — Commit dragged handles: ``[(curve, [(t, side, dt, dv, broken), ...])]``,
  - `GraphSync.on_keys_deleted(self, groups) -> None` — Cut keys: ``[(curve, [time, ...])]``;
  - `GraphSync.on_keys_inserted(self, items) -> None` — Key ``[(curve, time), ...]`` without changing the curves' shape.
  - `GraphSync.on_tangent_type(self, type_id: str, groups) -> None` — Set tangent type *type_id* on ``[(curve, [times])]`` *groups* (an
  - `GraphSync.on_tangents_broken(self, broken: bool, groups) -> None` — Break (or unify) the tangents of ``[(curve, [times])]`` *groups*.
  - `GraphSync.on_weighted(self, weighted: bool, curves) -> None` — Make *curves* weighted-tangent curves, or not.
  - `GraphSync.on_infinity(self, side: str, mode: str, curves) -> None` — Set *curves*' ``pre`` / ``post`` (else both) infinity to *mode*,
  - `GraphSync.key_targets(self, groups=None) -> list` — ``[(obj, attr, [times], shot_id), ...]`` for the graph's key
  - `GraphSync.on_key_menu(self, menu, groups) -> None` — The track view's key edits and Move / Copy to Shot, on the graph's
  - `GraphSync.on_key_selection(self, groups) -> None` — Mirror the graph's key selection into Maya's (not an undo step).

<a id="anim_utils--shots--shot_sequencer--segment_collector"></a>
### `anim_utils/shots/shot_sequencer/segment_collector.py`

Segment collection and attribute extraction for the shot sequencer.

- [`KEY_PROXIMITY_EPS`](mayatk/mayatk/anim_utils/shots/shot_sequencer/segment_collector.py#L19) — constant
- **[`class SegmentCollector`](mayatk/mayatk/anim_utils/shots/shot_sequencer/segment_collector.py#L26)** — SegmentCollector — module namespace.
  - `SegmentCollector.collect_segments(sequencer, shot, visible_shots, segment_cache, shifted_out_keys, logger)` *(static)* — Collect animation segments for visible shots.
  - `SegmentCollector.active_object_set(shot, segments_by_shot) -> set` *(static)* — Return the set of objects that belong to the active shot.
  - `SegmentCollector.abbreviate_attrs(attrs) -> str` *(static)* — Compact, sorted summary of attribute names for a clip's centre
  - `SegmentCollector.extract_attributes(segments) -> list` *(static)* — Extract attribute names from animation curves in the given segments.
  - `SegmentCollector.summary_times(segments, claimed_times) -> list` *(static)* — Every key time an object row ticks in the flat key style.
  - `SegmentCollector.build_curve_preview(crv, t_start, t_end, hidden_times=())` *(static)* — Bezier shape data for one anim curve, clipped to ``[t_start, t_end]``.

<a id="anim_utils--shots--shot_sequencer--sequencer_host"></a>
### `anim_utils/shots/shot_sequencer/sequencer_host.py`

What the Shot Sequencer panel needs from Maya: :class:`SequencerHost`.

- **[`class SequencerHost`](mayatk/mayatk/anim_utils/shots/shot_sequencer/sequencer_host.py#L253)** — Maya's answers to the Shot Sequencer panel's host calls.
  - `SequencerHost.current_time() -> Optional[float]` *(static)* — The playhead's frame, or ``None`` without a scene.
  - `SequencerHost.set_current_time(frame: float) -> None` *(static)* — Move the playhead to *frame*, recording no undo entry.
  - `SequencerHost.playback_range() -> Optional[Tuple[float, float]]` *(static)* — Maya's playback range ``(min, max)``, or ``None`` without a scene.
  - `SequencerHost.set_playback_range(start: float, end: float) -> None` *(static)* — Set Maya's playback range, recording no undo entry: a view mirror
  - `SequencerHost.watch_scene(*, busy: Callable[[], bool], on_keys_edited: Callable[[set], None], on_time_changed: Callable[[float], None], on_scene_changed: Callable[[], None], cap: int = 400) -> _SceneWatch` *(static)* — Attach the panel's scene hooks;
  - `SequencerHost.newly_keyed_members(edited: Iterable[str], shot, sequencer) -> List[str]` *(static)* — The objects keyed in *shot*'s frames that are not its members yet.
  - `SequencerHost.step_undo(cls, store, redo: bool = False) -> bool` *(class)* — One step of Maya's undo (*redo*: redo) for the panel's Undo / Redo,
  - `SequencerHost.play_controller(before_play: Optional[Callable[[], None]] = None)` *(static)* — The transport row's ``PlayController``: Maya's timeline through
  - `SequencerHost.bind_timeline_audio(cls, current: str = '') -> str` *(class)* — Bind the scene's audio to Maya's Time Slider;
  - `SequencerHost.timeline_audio_wav(source: str) -> str` *(static)* — The WAV path stored on *source* (the composite or a per-track node).
  - `SequencerHost.scene_fps() -> float` *(static)* — The scene's frame rate (24.0 without Maya).
  - `SequencerHost.segment_collector() -> type` *(static)* — The library's ``SegmentCollector``: the shots' motion segments, a
  - `SequencerHost.existing(names: Iterable[str]) -> set` *(static)* — The subset of *names* that resolve to a scene node.
  - `SequencerHost.leaf_name(name: str) -> str` *(static)* — *name* without its DAG path (``|grp|cube`` -> ``cube``).
  - `SequencerHost.node_icon(cls, name: str)` *(class)* — The node's type icon (a ``QIcon``), or ``None`` without Maya or for
  - `SequencerHost.select_objects(names: Iterable[str], add: bool = False) -> List[str]` *(static)* — Select *names* as a view mirror: no undo step.
  - `SequencerHost.claimed_key_times(sequencer, obj: str, curve) -> List[float]` *(static)* — The samples the shot system created on *curve* (its ledger claims).
  - `SequencerHost.motion_segments(objects: Iterable[str], time_range: Tuple[float, float], attributes: Optional[Iterable[str]] = None, ignore_holds: bool = True) -> List[Dict[str, Any]]` *(static)* — The motion segments of *objects* inside *time_range*.
  - `SequencerHost.attribute_curves(name: str) -> Tuple[Optional[str], Dict[str, Any]]` *(static)* — *name*'s long DAG path and its animated attributes.
  - `SequencerHost.audio_segments(start: float, end: float) -> list` *(static)* — The audio segments overlapping ``[start, end]``, waveforms included
  - `SequencerHost.audio_track_icon(cls, track_id: str)` *(class)* — The icon of an audio track: its DG audio node's, when it has one.
  - `SequencerHost.delete_span_keys(cls, obj: str, attrs: Sequence[str], start: float, end: float, release: Optional[Callable] = None) -> int` *(class)* — Cut *obj*'s keys on *attrs* inside ``[start, end]``.
  - `SequencerHost.stash_keys(cls, targets: Sequence[Tuple[str, Sequence[str], float, float]], source_shot_id: Optional[int] = None)` *(class)* — Park the keys of *targets* in the scene's key stash as ONE clip.
  - `SequencerHost.curves_for_attr(obj_name: str, attr_name: str) -> list` *(static)* — The anim curves (node names) connected to ``obj_name.attr_name``;
  - `SequencerHost.curves_for_attrs(cls, obj_name: str, attr_names: Iterable[str]) -> list` *(class)* — :meth:`curves_for_attr` over several attributes: every curve behind
  - `SequencerHost.curve_id(curve) -> str` *(static)* — A curve's identity across reads (its node name): what merged key
  - `SequencerHost.object_curves(obj_name: str) -> list` *(static)* — Every anim curve keying *obj_name*.
  - `SequencerHost.first_key(curves) -> Optional[float]` *(static)* — The earliest key time across *curves*, or ``None`` with no keys.
  - `SequencerHost.keyed_at(curve, times: Iterable[float], eps: float = 0.001) -> List[bool]` *(static)* — Whether *curve* holds a key within *eps* of each of *times*.
  - `SequencerHost.any_key_at(curves, frame: float) -> bool` *(static)* — Whether any curve of *curves* holds a key on *frame*: the
  - `SequencerHost.scale_attribute_keys(cls, obj_name: str, attr_name: str, old_start: float, old_end: float, new_start: float, new_end: float, ledger=None) -> bool` *(class)* — Scale only the curves driving *attr_name* on *obj_name*.
  - `SequencerHost.commit_curve_moves(curve_moves: Dict, ledger=None, eps: float = 0.001) -> None` *(static)* — Land merged key moves, one primitive call per curve.
  - `SequencerHost.delete_keys_at(curves, times: Iterable[float], release: Optional[Callable] = None) -> int` *(static)* — Cut the keys of *curves* on each of *times*.
  - `SequencerHost.moving_audio_track(track_id: str, ledger=None)` *(static)* — Yield ``(move, lowest)`` for moving an audio track's content.
  - `SequencerHost.undo_chunk(label: str)` *(static)* — One named undo chunk (``CoreUtils.undo_chunk``) for an edit made
  - `SequencerHost.tangent_from_handle(side: str, dt: float, dv: float) -> Tuple[float, float]` *(static)* — ``(angle_degrees, weight)`` of the tangent a dragged handle stands for.
  - `SequencerHost.tangent_type_edit(tangent: str, sides=('in', 'out')) -> dict` *(static)* — The edit that sets *tangent* (a :attr:`TANGENT_TYPES` type) on the
  - `SequencerHost.interpolation_edit(mode: str) -> dict` *(static)* — The edit for a Blender interpolation mode (``CONSTANT`` / ``LINEAR``
  - `SequencerHost.tangent_lock_edit(lock: bool) -> dict` *(static)* — The edit that unifies (``True``) or breaks the keys' tangents.
  - `SequencerHost.handle_drag_edit(cls, vectors: Dict, side: str, broken: bool) -> Callable` *(class)* — The edit a dragged tangent handle asks for.
  - `SequencerHost.edit_key_tangents(cls, targets: list, edit) -> int` *(class)* — Apply a tangent *edit* to *targets*' keys in one undo chunk.
  - `SequencerHost.target_curves(cls, targets: list) -> list` *(class)* — The anim curves behind *targets*' (object, attribute) pairs, each
  - `SequencerHost.simplify_keys(cls, targets: list, span: Tuple[float, float], on_delete=None) -> Tuple[int, int]` *(class)* — Drop the selected keys that carry neither a value nor a shape.
  - `SequencerHost.thin_keys(cls, targets: list, span: Tuple[float, float], on_delete=None) -> int` *(class)* — Keep only the first and last key of each selected attribute.
  - `SequencerHost.snap_keys(cls, targets: list, span: Tuple[float, float], on_move=None) -> int` *(class)* — Pull the selected keys (the Graph Editor selection the panel
  - `SequencerHost.euler_filter_keys(cls, targets: list) -> list` *(class)* — Unflip the rotation curves behind *targets* (whole curves).
  - `SequencerHost.invert_keys(cls, targets: list, on_replace=None, on_move=None) -> int` *(class)* — Mirror the selected keys in time, in place.
  - `SequencerHost.align_keys(cls, targets: list, on_replace=None, on_move=None) -> int` *(class)* — Line the selected keys up on the earliest one's frame.
  - `SequencerHost.copy_keys(cls, sequencer, targets: list) -> dict` *(class)* — The panel's key clipboard for *targets*' keys.
  - `SequencerHost.paste_keys(clipboard: dict, pairs: list, now: float, on_replace=None) -> list` *(static)* — Paste :meth:`copy_keys` rows at the playhead.
  - `SequencerHost.graph_sync_cls() -> type` *(static)* — The library's ``GraphSync`` (the graph view's host half).
  - `SequencerHost.object_exists(name: str) -> bool` *(static)* — Whether *name* names a scene node.
  - `SequencerHost.full_name(name: str) -> str` *(static)* — *name* as the scene's unique long DAG path (an audio track's source
  - `SequencerHost.undo_disabled()` *(static)* — A context in which scene changes record no undo step (a view
  - `SequencerHost.mirror_selection(names) -> None` *(static)* — Put *names* on the scene selection as a view mirror.
  - `SequencerHost.select_channels(objects, attrs=()) -> None` *(static)* — Put *attrs* on the Channel Box highlight;
  - `SequencerHost.select_keys(cls, rows) -> int` *(class)* — Select exactly the ``(obj, attr, times)`` *rows*' keys in the Graph
  - `SequencerHost.open_attribute_spreadsheet() -> bool` *(static)* — Open Maya's Attribute Spread Sheet over the selection;
  - `SequencerHost.set_dragged_handles(cls, curve, items) -> int` *(class)* — Write dragged tangent handles on *curve*;

<a id="anim_utils--smart_bake--_smart_bake"></a>
### `anim_utils/smart_bake/_smart_bake.py`

Smart bake module for intelligent pre-bake animation processing.

- **[`class BakeAnalysis`](mayatk/mayatk/anim_utils/smart_bake/_smart_bake.py#L55)** — Analysis result for a single object's bake requirements.
  - `BakeAnalysis.requires_bake(self) -> bool` *(property)* — Return True if this object has any driven channels needing bake.
  - `BakeAnalysis.all_driven_channels(self) -> List[str]` *(property)* — Return flat list of all channels that need baking.
- **[`class BakeResult`](mayatk/mayatk/anim_utils/smart_bake/_smart_bake.py#L85)** — Result container for SmartBake.bake() operation.
  - `BakeResult.skip(self, obj: str, reason: str, declined: bool = True) -> None` — Record *obj* as skipped, and why -- the one way into ``skipped``.
  - `BakeResult.declined(self) -> Dict[str, str]` *(property)* — ``{object: reason}`` for the objects SmartBake REFUSED or failed to
  - `BakeResult.baked_count(self) -> int` *(property)* — Number of objects successfully baked.
  - `BakeResult.success(self) -> bool` *(property)* — Return True if any objects were baked.
  - `BakeResult.describe(self) -> List[str]` — What the bake did, one line per fact, for a report (the Smart Bake
- **[`class SmartBake(_SmartBakeInternal)`](mayatk/mayatk/anim_utils/smart_bake/_smart_bake.py#L1081)** — Intelligent baking with automatic detection of what needs to be baked.
  - `SmartBake.analyze(self) -> Dict[str, BakeAnalysis]` — Analyze objects to determine what needs baking.
  - `SmartBake.get_time_range(self, analysis: Optional[Dict[str, BakeAnalysis]] = None) -> Tuple[int, int]` — Determine optimal bake time range from driver animation.
  - `SmartBake.get_object_time_ranges(self, analysis: Dict[str, BakeAnalysis], fallback: Tuple[int, int]) -> Dict[str, Tuple[int, int]]` — The frames each driven object in *analysis* actually needs sampled.
  - `SmartBake.bake(self, analysis: Optional[Dict[str, BakeAnalysis]] = None, time_range: Optional[Tuple[int, int]] = None) -> BakeResult` — Execute baking on analyzed objects.
  - `SmartBake.execute(self) -> BakeResult` — High-level entry point: analyze and bake in one call.
  - `SmartBake.list_sessions(cls) -> List[str]` *(class)* — Return ids of restorable bake sessions recorded in this scene,
  - `SmartBake.restore(cls, session_id: Optional[str] = None) -> 'RestoreResult'` *(class)* — Reverse a bake session recorded by ``bake(restorable=True)``.
  - `SmartBake.restore_matrix_wiring(cls, session_id: Optional[str] = None) -> 'RestoreResult'` *(class)* — Restore ONLY the matrix (offsetParentMatrix) bakes of a session.
  - `SmartBake.fold_offsets(cls, objects: List[str], bake_range: Optional[Tuple[float, float]] = None) -> Tuple[BakeResult, Callable[[], List[str]]]` *(class)* — Put each node among *objects* where an FBX write would not, for
  - `SmartBake.session(cls, **kwargs)` *(class)* — Context manager: bake on enter, restore on exit.
  - `SmartBake.run(cls, **kwargs) -> BakeResult` *(class)* — Class method for quick smart baking without explicit instantiation.

<a id="anim_utils--smart_bake--bake_session"></a>
### `anim_utils/smart_bake/bake_session.py`

Persistence and restore engine for SmartBake's nondestructive manifest.

- **[`class BakeSessionStore(_BakeSessionStoreInternal)`](mayatk/mayatk/anim_utils/smart_bake/bake_session.py#L391)** — LIFO stack of bake-session manifests on the ``data_internal`` node.
  - `BakeSessionStore.load(cls) -> List[dict]` *(class)* — Return all persisted sessions (oldest first).
  - `BakeSessionStore.save(cls, sessions: List[dict]) -> None` *(class)*
  - `BakeSessionStore.push(cls, session: dict) -> None` *(class)*
  - `BakeSessionStore.peek(cls, session_id: Optional[str] = None) -> Optional[dict]` *(class)* — Return the latest session (or the one matching *session_id*).
  - `BakeSessionStore.pop(cls, session_id: Optional[str] = None) -> Optional[dict]` *(class)* — Remove and return the latest session (or the matching one).
  - `BakeSessionStore.list_ids(cls) -> List[str]` *(class)*
  - `BakeSessionStore.merge_carrier(cls, carriers, other, ctx) -> None` *(class)* — Another scene's carriers merged here: the stashes a scene saved
  - `BakeSessionStore.discard_carrier(cls, carriers, other, ctx) -> None` *(class)* — Another scene's bake sessions were dropped: delete the curves they
  - `BakeSessionStore.new_session_id(cls) -> str` *(class)*
  - `BakeSessionStore.node_ref(node: str) -> Dict[str, Optional[str]]` *(static)* — Return a rename-safe reference ``{"name", "uuid"}`` for *node*.
  - `BakeSessionStore.resolve_ref(ref: Optional[Dict[str, Optional[str]]]) -> Optional[str]` *(static)* — Resolve a :func:`node_ref` back to a live node name, or ``None``.
  - `BakeSessionStore.plug_ref(plug: str) -> Dict[str, Optional[str]]` *(static)* — Return a rename-safe reference ``{"name", "uuid", "attr"}`` for *plug*.
  - `BakeSessionStore.resolve_plug(ref: Optional[Dict[str, Optional[str]]]) -> Optional[str]` *(static)* — Resolve a :func:`plug_ref` back to ``"node.attr"``, or ``None``.
  - `BakeSessionStore.stash_curve(curve: str) -> dict` *(static)* — Duplicate *curve* into a locked, registered stash node.
  - `BakeSessionStore.unstash_curve(record: dict, warnings: Optional[List[str]] = None, fallback_dst: Optional[str] = None) -> Optional[str]` *(static)* — Reconnect a stashed curve into its recorded network.
  - `BakeSessionStore.discard_stash(record: dict) -> None` *(static)* — Delete a stash node that is no longer needed (bake was a no-op).
  - `BakeSessionStore.collect_upstream_curves(plug: str, passthrough_types: Set[str]) -> List[str]` *(static)* — Return all animCurves feeding *plug*, traced through passthrough nodes.
  - `BakeSessionStore.trace_source(plug: str) -> Tuple[Optional[str], Optional[float]]` *(static)* — The driver feeding *plug* as ``(source plug, conversion factor)``,
  - `BakeSessionStore.reconnect(src: str, dst: str, factor: Optional[float] = None) -> None` *(static)* — Connect *src* to *dst* and put the recorded conversion *factor*
  - `BakeSessionStore.snapshot_connections(plug: str) -> List[List[dict]]` *(static)* — Record incoming connection pairs for *plug* (and its parent compound).
  - `BakeSessionStore.snapshot_conversions(node: str, channels: Iterable[str]) -> Dict[str, Any]` *(static)* — Record the unit-conversion factor on each of *node*'s *channels*.
  - `BakeSessionStore.remove_override_layer(session: dict, layer: Optional[str] = None, warnings: Optional[List[str]] = None) -> Optional[str]` *(static)* — Delete a bake's override layer and put back the conversions it hid.
  - `BakeSessionStore.restore_session(session: dict) -> 'RestoreResult'` *(static)* — Reverse everything recorded in *session*.
- **[`class RestoreResult`](mayatk/mayatk/anim_utils/smart_bake/bake_session.py#L1110)** — Result container for ``SmartBake.restore()``.

<a id="anim_utils--stagger_keys"></a>
### `anim_utils/stagger_keys.py`

Dedicated stagger-keys module to keep AnimUtils lean and testable.

- **[`class StaggerKeys`](mayatk/mayatk/anim_utils/stagger_keys.py#L16)** — Class containing keyframe staggering operations.
  - `StaggerKeys.stagger_keys(objects: list, start_frame: int = None, spacing: Union[int, float] = 0, use_intervals: bool = False, avoid_overlap: bool = False, smooth_tangents: bool = False, invert: bool = False, group_overlapping: bool = False, ignore: Union[str, List[str]] = None, channel_box_attrs_only: bool = False, split_static: bool = True, merge_touching: bool = False, ignore_visibility_holds: bool = True, verbose: bool = False, verbose_header: str = None, on_replace: Optional[Callable[[str, List[float]], Any]] = None, on_move: Optional[Callable[[str, List[Tuple[float, float]]], Any]] = None)` *(static)* — Stagger the keyframes of selected objects with various positioning controls.

<a id="anim_utils--world_fit_bake"></a>
### `anim_utils/world_fit_bake.py`

World-fitted transform bake: reparent nodes under a chosen target and key the

- **[`class WorldFitBake`](mayatk/mayatk/anim_utils/world_fit_bake.py#L31)** — Sample world matrices from the untouched scene, then reparent and key.
  - `WorldFitBake.sample_locals(plan: Sequence[Tuple[str, str, str, bool]], frames: Sequence[float], orient: Optional[Dict[str, Sequence[float]]] = None, residuals: Optional[Dict[Tuple[str, str], float]] = None) -> Dict[Tuple[str, str], List[List[float]]]` *(static)* — ``{(node, target): [[tx..sz] per frame]}`` from the UNTOUCHED scene.
  - `WorldFitBake.bake_node(cls, node: str, target: str, frames: Sequence[float], rows: Sequence[Sequence[float]], reparent: bool = True, keys: bool = True) -> dict` *(class)* — Reparent *node* under *target* and key the pre-sampled locals *rows*.
  - `WorldFitBake.non_inheriting(nodes: Iterable[str], frames: Sequence[float] = ()) -> Dict[str, str]` *(static)* — ``{node: parent}`` for each transform among *nodes* that ignores its
  - `WorldFitBake.similarity_ancestors(nodes: Iterable[str], frames: Sequence[float], tolerance: float = 0.05) -> Dict[str, bool]` *(static)* — ``{ancestor path: qualifies}`` for every ancestor of *nodes*.
  - `WorldFitBake.flatten_target(node: str, qualifies: Dict[str, bool]) -> Optional[str]` *(static)* — Deepest qualifying ancestor of *node* (by its pre-flatten path),
  - `WorldFitBake.flatten(cls, plan: Sequence[Tuple[str, str, str, bool]], frames: Sequence[float], records: Optional[List[dict]] = None, max_residual: Optional[float] = None) -> dict` *(class)* — The reversible flatten, in one call: :meth:`prepare` then
  - `WorldFitBake.prepare(cls, plan: Sequence[Tuple[str, str, str, bool]], frames: Sequence[float], max_residual: Optional[float] = None) -> dict` *(class)* — Everything the flatten reads, taken before anything moves: the IK
  - `WorldFitBake.apply(cls, prepared: dict, records: Optional[List[dict]] = None, static: Iterable[str] = ()) -> dict` *(class)* — Reparent and key every node :meth:`prepare` sampled, and park the IK
  - `WorldFitBake.restore_node(record: dict) -> bool` *(static)* — Reverse one :meth:`bake_node` (or ``ikblend``) record: delete the
  - `WorldFitBake.restore(cls, records: Sequence[dict]) -> Tuple[int, List[str]]` *(class)* — Reverse *records* (LIFO) with :meth:`restore_node`.
  - `WorldFitBake.ik_handles_touching(paths: Iterable[str]) -> Dict[str, set]` *(static)* — ``{ikHandle: its joint chain}`` for every handle whose chain meets *paths*.

<a id="audio_utils--_audio_utils"></a>
### `audio_utils/_audio_utils.py`

Unified audio system for Maya scenes.

- [`CARRIER_NODE`](mayatk/mayatk/audio_utils/_audio_utils.py#L61) — constant
- [`ATTR_PREFIX`](mayatk/mayatk/audio_utils/_audio_utils.py#L65) — constant
- [`FILE_MAP_ATTR`](mayatk/mayatk/audio_utils/_audio_utils.py#L68) — constant
- [`MARKER_ATTR`](mayatk/mayatk/audio_utils/_audio_utils.py#L72) — constant
- [`LEGACY_CACHE_DIR_NAMES`](mayatk/mayatk/audio_utils/_audio_utils.py#L75) — constant
- [`RESERVED_TRACK_IDS`](mayatk/mayatk/audio_utils/_audio_utils.py#L80) — constant
- **[`class TrackEvent`](mayatk/mayatk/audio_utils/_audio_utils.py#L33)** — One keyed play-event on a track.
- **[`class AudioUtils(pythontk.HelpMixin)`](mayatk/mayatk/audio_utils/_audio_utils.py#L88)** — Unified audio system API for Maya scenes.
  - `AudioUtils.get_snap_frames() -> bool` *(static)* — Whether audio key writes snap to whole frames -- the scene's effect
  - `AudioUtils.set_snap_frames(value: bool) -> None` *(static)* — Set the scene recipe's whole-frame snap for audio key writes.
  - `AudioUtils.validate_track_id(track_id: str) -> None` *(static)* — Raise ``ValueError`` if *track_id* violates schema rules.
  - `AudioUtils.normalize_track_id(cls, raw: str) -> str` *(class)* — Derive a canonical ``track_id`` from arbitrary text.
  - `AudioUtils.attr_for(cls, track_id: str) -> str` *(class)* — Return the attr name for *track_id* (e.g.
  - `AudioUtils.track_id_from_attr(cls, attr_name: str) -> str` *(class)* — Inverse of :meth:`attr_for`.
  - `AudioUtils.find_carriers() -> List[str]` *(static)* — Return carriers holding audio data (``[CARRIER_NODE]`` or ``[]``).
  - `AudioUtils.list_track_attrs(carrier: str) -> List[str]` *(static)* — List all per-track audio attrs on *carrier* (sorted).
  - `AudioUtils.load_file_map(carrier: Optional[str] = None) -> Dict[str, str]` *(static)* — Return the ``{track_id: path}`` dict from the carrier's JSON attr.
  - `AudioUtils.track_files(track_ids: List[str], carrier: Optional[str] = None) -> List[Tuple[str, str]]` *(static)* — ``(file name, path)`` of each track in *track_ids* whose file exists.
  - `AudioUtils.add_clip(cls, path: str, frame_start: float, name: Optional[str] = None, frame_end: Optional[float] = None, carrier: Optional[str] = None) -> str` *(class)* — Register *path* as a track that plays from *frame_start* (to *frame_end*).
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
  - `AudioUtils.read_all_events(cls, carrier: Optional[str] = None) -> Dict[str, List[Tuple[float, Optional[float]]]]` *(class)* — Every track's on/off spans: ``{track_id: [(start, stop), ...]}``.
  - `AudioUtils.write_key(cls, track_id: str, frame: float, value: int = 1, carrier: Optional[str] = None, snap: Optional[bool] = None) -> None` *(class)* — Set a key at *frame* with *value* (0=off, 1=on) on the track attr.
  - `AudioUtils.key_clip(cls, track_id: str, start: float, end: Optional[float] = None, duration: Optional[float] = None, auto_end: bool = True, carrier: Optional[str] = None) -> List[Tuple[str, float]]` *(class)* — Key one clip of *track_id*: ON at *start* and, with *auto_end*, OFF
  - `AudioUtils.track_curve(cls, track_id: str, carrier: Optional[str] = None) -> Optional[str]` *(class)* — The anim curve keying *track_id*, or ``None`` before its first key.
  - `AudioUtils.clip_length_frames(cls, track_id: str, carrier: Optional[str] = None) -> float` *(class)* — *track_id*'s clip length in timeline frames, from its source file;
  - `AudioUtils.remove_key(cls, track_id: str, frame: float, carrier: Optional[str] = None) -> bool` *(class)* — Remove the key at *frame* on the track attr.
  - `AudioUtils.clear_keys(cls, track_id: str, carrier: Optional[str] = None) -> bool` *(class)* — Remove every key on *track_id*'s attr.
  - `AudioUtils.shift_keys_in_range(cls, old_start: float, old_end: float, delta: float, track_ids: Optional[List[str]] = None, carrier: Optional[str] = None, ledger=None) -> List[str]` *(class)* — Shift audio keys in ``[old_start, old_end]`` by *delta*.
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

- **[`class AudioClips(pythontk.LoggingMixin)`](mayatk/mayatk/audio_utils/audio_clips/_audio_clips.py#L51)** — Scene-wide audio event facade.
  - `AudioClips.sync(cls, track_ids: Optional[List[str]] = None, composite: bool = True, activate: bool = True) -> Dict[str, list]` *(class)* — Reconcile DG nodes and rebuild the composite WAV.
  - `AudioClips.rebuild_composite(cls) -> Optional[str]` *(class)* — Rebuild the scene-wide composite WAV from keyed start events.
  - `AudioClips.remove(cls) -> int` *(class)* — Delete every managed DG node, the composite, and all tracks.
  - `AudioClips.load_tracks(cls, audio_files: List[str]) -> List[str]` *(class)* — Register audio files as tracks (no keys authored).
  - `AudioClips.list_tracks() -> List[str]` *(static)* — Every track on the carrier, sorted (``[]`` outside Maya).
  - `AudioClips.find_track(name: str) -> Optional[str]` *(static)* — The track answering to *name* -- the id it normalizes to -- or
  - `AudioClips.tracks_at(frame: float) -> List[str]` *(static)* — The tracks playing at *frame* (``AudioUtils.tracks_on_at_frame``):
  - `AudioClips.next_track() -> Optional[str]` *(static)* — The track Next Event keys: the one after the most recently started
  - `AudioClips.add_tracks(cls, audio_files: List[str]) -> List[str]` *(class)* — Register *audio_files* as tracks (:meth:`load_tracks`;
  - `AudioClips.rename_track(cls, track_id: str, new_name: str) -> str` *(class)* — Rename *track_id* to the id *new_name* normalizes to;
  - `AudioClips.replace_track(cls, track_id: str, path: str) -> str` *(class)* — Point *track_id* at the audio file *path*, keeping its keys.
  - `AudioClips.remove_track(cls, track_id: str) -> bool` *(class)* — Delete *track_id*: its attribute and keys, its file-map entry and
  - `AudioClips.cleanup_unused(cls) -> List[str]` *(class)* — Delete every track that holds no key (registered, never placed),
  - `AudioClips.key_track(cls, track_id: str, frame: Optional[float] = None, auto_end: bool = False) -> float` *(class)* — Key an audio event: *track_id* ON at *frame* (the playhead when
  - `AudioClips.key_all(cls, frame: Optional[float] = None, auto_end: bool = False, stagger: int = 0) -> List[str]` *(class)* — Key every track once, end to end from *frame* (the playhead when
  - `AudioClips.activate_track(cls, track_id: str) -> Optional[str]` *(class)* — Make *track_id*'s own sound node the Time Slider's (it writes no
  - `AudioClips.attach_composite(cls) -> Optional[str]` *(class)* — Reconcile the sound nodes without rebuilding the mix, then put an
  - `AudioClips.reveal(track_id: Optional[str] = None) -> str` *(static)* — Select the carrier node every track's attribute lives on, so the
  - `AudioClips.channels_config() -> Dict[str, object]` *(static)* — How the Channels panel shows the tracks: pinned to the carrier and
  - `AudioClips.has_legacy_data() -> bool` *(static)* — Whether the carrier still holds the legacy ``audio_trigger`` schema.
  - `AudioClips.migrate_legacy(cls) -> List[str]` *(class)* — Move the carrier's legacy ``audio_trigger`` keys and file map onto
  - `AudioClips.get_snap_frames() -> bool` *(static)* — Whether audio keys snap to whole frames: the scene's effect recipe
  - `AudioClips.set_snap_frames(value: bool) -> None` *(static)* — Set the recipe's whole-frame snap (``AudioUtils.set_snap_frames``).
  - `AudioClips.watch_snap(callback, widget=None) -> Optional[Callable[[], None]]` *(static)* — Call *callback* whenever the scene's recipe changes (another panel,
  - `AudioClips.export_composite(cls, choose_path: Callable[[str], Optional[str]], trim_silence: bool = True) -> Tuple[Optional[str], bool]` *(class)* — Write the scene's composite WAV where *choose_path* says, rebuilt
  - `AudioClips.export_tracks(cls, choose_dir: Callable[[str], Optional[str]], keyed_only: bool = True, suffix_range: bool = False) -> Tuple[int, List[str], Optional[str]]` *(class)* — Copy the tracks' source files into the folder *choose_dir* names.
  - `AudioClips.watch(cls, owner, on_scene=None, on_tracks=None, on_time=None, widget=None) -> List[int]` *(class)* — Tell *owner* what happens to the scene's audio, through the
  - `AudioClips.export_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]` *(class)* — The ``audio`` record for this scene, or ``None`` when there
  - `AudioClips.prepare_for_export(cls) -> str` *(class)* — Bake the scene-wide audio manifest for FBX export.
  - `AudioClips.enable_auto_export(cls) -> None` *(class)* — Bake the audio manifest onto ``data_export`` before **every** FBX export.
  - `AudioClips.disable_auto_export(cls) -> None` *(class)* — Opt the producer out of the before-export hook for the rest of the
  - `AudioClips.list_nodes(cls) -> List[str]` *(class)* — Return names of every managed DG audio node plus the composite.
  - `AudioClips.set_active(cls, node_name: str, time_slider: bool = True) -> None` *(class)* — Set an audio node as the active Time Slider sound.

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

- **[`class Nodes(_NodesInternal)`](mayatk/mayatk/audio_utils/nodes.py#L45)** — Nodes — module namespace.
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

- **[`class CamUtils(pythontk.HelpMixin)`](mayatk/mayatk/cam_utils/_cam_utils.py#L19)**
  - `CamUtils.group_cameras(name='cameras', non_default=True, root_only=False, hide_group=False)` *(static)* — Group cameras in the scene based on the provided parameters.
  - `CamUtils.toggle_safe_frames(cls)` *(class)* — Toggle display of the film gate for the current camera.
  - `CamUtils.get_current_cam()` *(static)* — Get the currently active camera.
  - `CamUtils.create_camera_from_view(name='camera#')` *(static)* — Create a new camera based on the current view.
  - `CamUtils.get_view_state(cls, camera=None)` *(class)* — Snapshot a camera's placement and lens clipping, for a later restore.
  - `CamUtils.set_view_state(cls, state)` *(class)* — Restore a snapshot taken by :meth:`get_view_state`.
  - `CamUtils.zoom_view(cls, camera=None, factor=2.0)` *(class)* — Magnify the view about its center of interest by ``factor``.
  - `CamUtils.fit_camera_clipping(cls, objects=None, camera=None, buffer=0.25)` *(class)* — Widen a camera's clip planes until `objects` can't be clipped by them.
  - `CamUtils.adjust_camera_clipping(cls, camera=None, near_clip=None, far_clip=None)` *(class)* — Adjusts the near and far clipping planes of one or multiple cameras.
  - `CamUtils.switch_viewport_camera(cls, camera_name)` *(class)* — Unified method to switch to a camera, creating custom ones if needed.
  - `CamUtils.get_cameras() -> list` *(static)* — The cameras a user picks from: each camera's transform, named as
  - `CamUtils.create_camera(focal_length: float = 35.0) -> str` *(static)* — Create a camera with *focal_length* (mm), selected.
  - `CamUtils.look_through(camera: str, select: bool = False) -> str` *(static)* — Look through *camera* in the active viewport (``lookThru``), selecting
  - `CamUtils.align_view_to_selection() -> list` *(static)* — Look along the selected polygon's normal and frame the selection
  - `CamUtils.navigate_view(cls, mode: str = 'ORBIT') -> bool` *(class)* — Arm Maya's interactive camera tool for *mode*: drag in a view to orbit

<a id="cam_utils--camera_visibility"></a>
### `cam_utils/camera_visibility.py`

Per-camera visibility: Maya's Display > Per Camera Visibility

- **[`class CameraVisibility(_CameraVisibilityInternal)`](mayatk/mayatk/cam_utils/camera_visibility.py#L74)** — Per-camera exclusive / hidden visibility (see the module docstring).
  - `CameraVisibility.set_exclusive(cls, camera=None, objects=None) -> List[str]` *(class)* — Make *objects* exclusive to *camera*: they show only in a view looking
  - `CameraVisibility.set_hidden(cls, camera=None, objects=None) -> List[str]` *(class)* — Hide *objects* from *camera* (Maya ``SetHiddenFromCamera``).
  - `CameraVisibility.remove_from_exclusive(cls, camera=None, objects=None) -> List[str]` *(class)* — Take *objects* out of *camera*'s exclusive set (Maya
  - `CameraVisibility.remove_from_hidden(cls, camera=None, objects=None) -> List[str]` *(class)* — Take *objects* out of *camera*'s hidden set (Maya
  - `CameraVisibility.remove_all(cls, camera=None) -> None` *(class)* — Clear both sets of *camera* (Maya ``CameraRemoveAll``).
  - `CameraVisibility.remove_all_for_all() -> None` *(static)* — Clear both sets of every camera (Maya ``CameraRemoveAllForAll``).

<a id="core_utils--_core_utils"></a>
### `core_utils/_core_utils.py`

- **[`class BoundingBox`](mayatk/mayatk/core_utils/_core_utils.py#L20)** — Plain-data bounding box with ``MVector`` extents.
  - `BoundingBox.corners(self)` *(property)* — The box's 8 corner ``MVector``s (every min/max combination per axis).
- **[`class CoreUtils(pythontk.CoreUtils, _CoreUtilsInternal)`](mayatk/mayatk/core_utils/_core_utils.py#L291)**
  - `CoreUtils.undo_chunk(name: str = '')` *(static)* — Group operations into a single Maya undo chunk.
  - `CoreUtils.undo_disabled()` *(static)* — Run a block without recording anything into the undo queue.
  - `CoreUtils.suspended_refresh()` *(static)* — Suspend viewport refresh for the duration of a bulk operation.
  - `CoreUtils.selected(func: Callable) -> Callable` *(static)* — Pass the selection (:meth:`selected_objects`) to *func*'s target parameter
  - `CoreUtils.selected_objects(**kwargs) -> List[str]` *(static)* — The current selection, ``[]`` when it is empty (``cmds.ls(selection=True)``).
  - `CoreUtils.active_object() -> Optional[str]` *(static)* — The selection's lead object: the last one selected, which Maya draws
  - `CoreUtils.ensure_component_mode(object_type: str = 'mesh', component: Optional[str] = None) -> Optional[str]` *(static)* — Enter component mode on the selected (or hilited) objects of
  - `CoreUtils.ensure_object_mode() -> bool` *(static)* — Leave component mode for object mode;
  - `CoreUtils.preserved_selection()` *(static)* — Restore the viewport selection after a scene-mutating block.
  - `CoreUtils.temp_option_vars(values: Dict[str, Union[int, float, str]])` *(static)* — Set Maya optionVars for a block, then put the user's values back.
  - `CoreUtils.undoable(fn=None, *, name: str = '', suspend_refresh: bool = False)` *(static)* — A decorator to place a function into Maya's undo chunk.
  - `CoreUtils.reparent(func: Callable) -> Callable` *(static)* — A decorator to manage reparenting of Maya nodes before and after an operation.
  - `CoreUtils.wrap_control(control_name, container)` *(static)* — Embed a Maya Native UI Object.
  - `CoreUtils.confirm_existence(objects: List[str]) -> Tuple[List[str], List[str]]` *(static)* — Confirms the existence of each object in the provided list in Maya.
  - `CoreUtils.node_handles(nodes) -> list` *(static)* — Rename-proof references to *nodes*, to be resolved after a renaming operation.
  - `CoreUtils.resolve_handles(handles, drop_dead: bool = True) -> List[str]` *(static)* — Current full path (DAG) / name (DG) for each :meth:`node_handles` handle.
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
  - `CoreUtils.get_bounding_box(node, world: bool = True) -> BoundingBox` *(static)* **DEPRECATED (remove in 0.30.0)** — Moved to ``XformUtils.get_bounding_box(node, "box", world_space=world)``,

<a id="core_utils--auto_instancer--_auto_instancer"></a>
### `core_utils/auto_instancer/_auto_instancer.py`

Scene auto-instancer: convert geometrically identical meshes to instances.

- **[`class InstanceCandidate`](mayatk/mayatk/core_utils/auto_instancer/_auto_instancer.py#L47)** — Holds information about a transform candidate for instancing.
  - `InstanceCandidate.transform(self) -> str` *(property)*
  - `InstanceCandidate.exists(self) -> bool`
- **[`class InstanceGroup`](mayatk/mayatk/core_utils/auto_instancer/_auto_instancer.py#L80)** — A group of objects that are geometrically identical.
- **[`class AutoInstancer(pythontk.LoggingMixin, _AutoInstancerInternal)`](mayatk/mayatk/core_utils/auto_instancer/_auto_instancer.py#L132)** — Convert matching meshes into instances.
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
  - `AutoInstancer.run_once(cls, nodes: Optional[Sequence[object]] = None, *, return_summary: bool = False, select: bool = False, **config) -> Union[List[str], Tuple[List[str], Dict[str, object]]]` *(class)* — One-shot: build an ``AutoInstancer`` from ``config`` and run it.

<a id="core_utils--auto_instancer--assembly_reconstructor"></a>
### `core_utils/auto_instancer/assembly_reconstructor.py`

Logic for separating and reassembling mesh assemblies.

- [`CANONICAL_BAKE_PREFIX`](mayatk/mayatk/core_utils/auto_instancer/assembly_reconstructor.py#L32) — constant
- [`ASSEMBLY_TAG_ATTR`](mayatk/mayatk/core_utils/auto_instancer/assembly_reconstructor.py#L36) — constant
- **[`class AssemblyReconstructor`](mayatk/mayatk/core_utils/auto_instancer/assembly_reconstructor.py#L39)** — Handles the separation and intelligent reassembly of combined meshes.
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

AutoInstancer's instancing strategy: the Maya binding of the ptk engine.

- **[`class InstancingStrategy(pythontk.InstancingStrategy)`](mayatk/mayatk/core_utils/auto_instancer/instancing_strategy.py#L23)** — :class:`pythontk.InstancingStrategy` counting triangles in the Maya scene.

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
- **[`class Components(GetComponentsMixin, pythontk.HelpMixin, _ComponentsInternal)`](mayatk/mayatk/core_utils/components.py#L471)**
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
  - `Components.average_normals(cls, objects, by_uv_shell=False)` *(class)* — Average the normals of the given objects (``None``: the selection).
  - `Components.set_edge_softness(cls, objects, soft: bool = True) -> None` *(class)* — Soften (``soft=True``) or harden the edge normals of *objects*.
  - `Components.set_to_face_normals(objects) -> None` *(static)* — Set the vertex normals of *objects* to their faces' (faceted shading).
  - `Components.reverse_normals(cls, objects, mode: int = 0) -> None` *(class)* — Reverse the normals of *objects* (meshes, or just the given faces).
  - `Components.toggle_normal_lock(objects) -> Optional[bool]` *(static)* — Lock the vertex normals of *objects*, or unlock them when every one
  - `Components.transfer_normals(objects, space: str = 'world')` *(static)* — Transfer vertex normals from the first mesh of *objects* to the rest
  - `Components.get_transfer_types(cls) -> Dict[str, str]` *(class)* — The Transfer menu: ``{label: what it does}`` in menu order, each a
  - `Components.transfer(cls, objects, transfer_type: str)` *(class)* — Run a :meth:`get_transfer_types` transfer from the first surface of
  - `Components.filter_components_by_connection_count(cls, components, num_of_connected=(0, 2), connected_type='', returned_type='str')` *(class)* — Get a list of components filtered by the number of their connected components.
  - `Components.get_vertex_normal(cls, vertex, angle_weighted=False)` *(class)* — Return the normal at the given vertex.
  - `Components.get_vector_from_components(components)` *(static)* — Get a vector representing the averaged and normalized vertex-face normals.
  - `Components.crease_edges(edges=None, amount=None, angle=None)` *(static)* — Crease *edges* (subdivision sharpness), optionally softening or hardening
  - `Components.get_creased_edges(edges)` *(static)* — Return any creased edges from a list of edges.
  - `Components.transfer_creased_edges(frm=None, to=None)` *(static)* — Copy the edge creases of *frm* to the same edge ids on each of *to*

<a id="core_utils--diagnostics--animation_diag"></a>
### `core_utils/diagnostics/animation_diag.py`

Animation-curve diagnostics and optional repair helpers.

- **[`class AnimCurveDiagnostics`](mayatk/mayatk/core_utils/diagnostics/animation_diag.py#L20)** — Utilities for detecting and resolving common animation-curve issues.
  - `AnimCurveDiagnostics.repair_visibility_tangents(cls, objects: Optional[Union[NodeLike, Sequence[NodeLike]]] = None, recursive: bool = True, quiet: bool = False) -> int` *(class)* — Repair visibility animation curves by forcing 'step' tangents.
  - `AnimCurveDiagnostics.repair_corrupted_curves(cls, objects: Optional[Union[NodeLike, Sequence[NodeLike]]] = None, recursive: bool = True, delete_corrupted: bool = False, fix_infinite: bool = True, fix_invalid_times: bool = True, time_range_threshold: float = 1000000.0, value_threshold: float = 1000000.0, quiet: bool = False, on_delete=None) -> Dict[str, Any]` *(class)* — Detect and (optionally) repair corrupted animation curves.

<a id="core_utils--diagnostics--audit_records"></a>
### `core_utils/diagnostics/audit_records.py`

Scene-audit data contract: profiles, per-asset records, and the SceneReport tree.

- [`SEVERITY_LOW`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L49) — constant
- [`SEVERITY_MEDIUM`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L50) — constant
- [`SEVERITY_HIGH`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L51) — constant
- [`TRANSPARENCY_OPAQUE`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L55) — constant
- [`TRANSPARENCY_MASKED`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L56) — constant
- [`TRANSPARENCY_BLEND`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L57) — constant
- **[`class AuditProfile`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L21)** — Thresholds for scene analysis.
- **[`class MeshRecord`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L61)** — Per-mesh statistics for a single shape node.
- **[`class MaterialRecord`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L85)** — Per-shape material usage summary (aggregated across slots).
- **[`class Finding`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L113)** — An observation about an asset (negative or risk-flagged).
- **[`class FixAction`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L123)** — A recommended remediation step.
- **[`class BudgetDelta`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L134)** — How far an asset exceeds the profile budget along each axis.
  - `BudgetDelta.is_over_budget(self) -> bool`
  - `BudgetDelta.summary(self) -> str` — Pre-rendered ``"tris +N | slots +M | …"`` string used by the
- **[`class AssetRecord`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L169)** — Combined per-asset record produced by analyze().
- **[`class ParetoEntry`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L193)** — One row of a Pareto ranking (top contributor + cumulative %).
- **[`class TextureFile`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L202)** — A texture file referenced by the scene, with usage stats.
- **[`class MissingTexture`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L227)** — A texture referenced by a material but not present on disk.
- **[`class SharedTexture`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L236)** — A texture used by more than one mesh.
- **[`class MaterialSplit`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L244)** — A material correlated with high-slot meshes (draw-call splits).
- **[`class MaterialAudit`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L254)** — One material as the scope uses it: who wears it and what it costs.
- **[`class SceneOverview`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L278)** — Scene-wide facts, independent of the audit scope: the file, its units
- **[`class SlotStats`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L299)** — Distribution stats for material slots-per-mesh.
- **[`class InstanceStats`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L310)** — Mesh / instance counts.
- **[`class BudgetBuckets`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L319)** — Histogram of overage severity per dimension.
- **[`class ComplianceStats`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L331)** — Percentage of scene over budget per dimension.
- **[`class MissingTextureImpact`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L339)** — Downstream effect of missing textures on the asset list.
  - `MissingTextureImpact.is_empty(self) -> bool`
- **[`class SummaryStats`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L351)** — High-level scene counters surfaced by the Executive Summary.
- **[`class BudgetStats`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L380)** — Budget / compliance / savings figures.
- **[`class TextureStats`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L398)** — Texture-side aggregates (surface maps only;
- **[`class PipelineStats`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L425)** — Pipeline integrity findings (missing textures + impact).
- **[`class OffenderLists`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L436)** — Top-N rankings across various dimensions.
- **[`class AnalysisManifest`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L461)** — What was analyzed, how, and how long it took.
- **[`class SceneReport`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L485)** — Top-level result of ``SceneAnalyzer.generate_report``.
  - `SceneReport.to_dict(self) -> Dict[str, Any]` — Serialize the report to a nested plain-dict tree.
- **[`class SceneInfoSection`](mayatk/mayatk/core_utils/diagnostics/audit_records.py#L519)** — Report-section identifiers used to gate analyze() work and report output.
  - `SceneInfoSection.normalize(cls, sections: Optional[List[str]]) -> List[str]` *(class)* — Coerce a caller-supplied sections argument to a stable,

<a id="core_utils--diagnostics--mesh_diag"></a>
### `core_utils/diagnostics/mesh_diag.py`

Mesh diagnostics and repair helpers.

- **[`class MeshDiagnostics(_MeshDiagnosticsInternal)`](mayatk/mayatk/core_utils/diagnostics/mesh_diag.py#L77)** — Operations for inspecting and fixing common mesh issues.
  - `MeshDiagnostics.clean_geometry(objects: NodeSeq, allMeshes: bool = False, repair: bool = False, quads: bool = False, nsided: bool = False, concave: bool = False, holed: bool = False, nonplanar: bool = False, zeroGeom: bool = False, zeroGeomTol: float = 1e-05, zeroEdge: bool = False, zeroEdgeTol: float = 1e-05, zeroMap: bool = False, zeroMapTol: float = 1e-05, sharedUVs: bool = False, nonmanifold: bool = False, lamina: bool = False, invalidComponents: bool = False, historyOn: bool = True, bakePartialHistory: bool = False) -> list` *(static)* — Select or remove unwanted geometry from a mesh via ``polyCleanupArgList``.
  - `MeshDiagnostics.cleanup_meshes(cls, scope: str = 'selected', repair: bool = False, *, checks=(), area_tolerance: float = 1e-05, edge_length_tolerance: float = 1e-05, uv_area_tolerance: float = 1e-05, merge_vertices: bool = False, merge_distance: float = 0.0001, delete_history: bool = False, delete_loose: bool = False, dissolve_degenerate: bool = False, recalculate_normals: bool = False, fill_holes: bool = False, overlapping_faces: bool = False, overlapping_objects: bool = False, omit_selected: bool = False) -> dict` *(class)* — Mesh Cleanup: select, or repair, common topology problems on the meshes
  - `MeshDiagnostics.get_ngons(objects: Optional[NodeSeq] = None, repair: bool = False) -> list` *(static)* — Find N-gons and optionally convert them to quads.
  - `MeshDiagnostics.find_non_manifold_vertices(objects: NodeSeq) -> dict` *(static)* — Map each mesh in *objects* to its non-manifold vertices, via ``polyInfo``.
  - `MeshDiagnostics.select_non_manifold(cls, objects: NodeSeq) -> tuple` *(class)* — Select what makes *objects* non-manifold: its vertices, else its UVs.
  - `MeshDiagnostics.repair_non_manifold(cls, objects: NodeSeq, quiet: bool = False) -> dict` *(class)* — Auto-repair non-manifold geometry AND UVs on *objects*.

<a id="core_utils--diagnostics--scene_audit"></a>
### `core_utils/diagnostics/scene_audit.py`

Scene audit engine — game-readiness analysis over meshes, materials, and textures.

- **[`class SceneAnalyzer(pythontk.LoggingMixin)`](mayatk/mayatk/core_utils/diagnostics/scene_audit.py#L73)** — Analyzes scene objects for performance expectations in game engines.
  - `SceneAnalyzer.run_audit(cls, adaptive: bool = False, verbose: bool = True) -> None` *(class)* — Run a full scene audit and print the report.
  - `SceneAnalyzer.format_audit_text(cls, adaptive: bool = False, objects: Optional[List[Any]] = None, sections: Optional[List[str]] = None, scope: Optional[str] = None) -> Dict[str, str]` *(class)* — Run the audit and return the formatted report as a
  - `SceneAnalyzer.format_audit_html(cls, adaptive: bool = False, objects: Optional[List[Any]] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None, sections: Optional[List[str]] = None, scope: Optional[str] = None) -> Dict[str, str]` *(class)* — Run the audit and return a section-keyed dict of HTML
  - `SceneAnalyzer.analyze(self, objects: List[Any] = None, fast_mode: bool = True, progress_callback: Optional[Callable[[int, int, str], None]] = None, profile: AuditProfile = None, sections: Optional[List[str]] = None, scope: Optional[str] = None) -> List[AssetRecord]` — Main entry point for analysis.
  - `SceneAnalyzer.generate_report(self, records: List[AssetRecord]) -> SceneReport` — Build a :class:`SceneReport` from per-asset records.
  - `SceneAnalyzer.print_report(self, report: SceneReport, sections: Optional[List[str]] = None)` — Print the formatted scene-audit report to the logger.

<a id="core_utils--diagnostics--scene_diag"></a>
### `core_utils/diagnostics/scene_diag.py`

Scene repair helpers: OCIO / color management, unknown nodes and plugins,

- **[`class SceneDiagnostics(_SceneDiagnosticsInternal)`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L68)** — Operations for inspecting and fixing common scene issues.
  - `SceneDiagnostics.fix_ocio(cls, dry_run: bool = False, verbose: bool = True, prefer_env_ocio: bool = True, prefer_aces: bool = True, fix_color_spaces: bool = True) -> dict` *(class)* — Repair Maya OCIO/Color Management preferences.
  - `SceneDiagnostics.fix_missing_color_spaces(cls, fallback_color_space: Optional[str] = None, fallback_raw_space: Optional[str] = None, auto_detect: bool = True, dry_run: bool = False, verbose: bool = True, scan_all: bool = True, force_update: bool = False) -> Dict[str, Any]` *(class)* — Fix missing color space errors on file texture nodes.
  - `SceneDiagnostics.fix_unknown_plugins(dry_run: bool = False, verbose: bool = True) -> Dict[str, List[str]]` *(static)* — Fixes the 'Unable to Save Scene' issue by removing unknown nodes and plugins.
  - `SceneDiagnostics.remove_xgen_expressions(quiet: bool = False) -> int` *(static)* — Remove legacy XGen expressions that cause 'Cannot find procedure xgmPreview' errors.
  - `SceneDiagnostics.cleanup_scene(cls, quiet: bool = False) -> Dict[str, Any]` *(class)* — Run all scene cleanup operations:
  - `SceneDiagnostics.repair_mangled_names(cls, objects: Optional[List[str]] = None, dry_run: bool = False, descend: bool = True, decode_only: bool = False) -> Dict[str, Any]` *(class)* — Repair scratch/mangled node names, then conform shape names.

<a id="core_utils--diagnostics--transform_diag"></a>
### `core_utils/diagnostics/transform_diag.py`

Transform diagnostics and repair helpers.

- **[`class TransformDiagnostics(_TransformDiagnosticsInternal)`](mayatk/mayatk/core_utils/diagnostics/transform_diag.py#L107)** — Operations for inspecting and fixing common transform issues.
  - `TransformDiagnostics.get_sheared(cls, objects: Optional[NodeSeq] = None, tolerance: Optional[float] = None) -> List[str]` *(class)* — Return the transforms carrying their own shear.
  - `TransformDiagnostics.get_non_orthogonal(cls, objects: Optional[NodeSeq] = None, tolerance: Optional[float] = None, detailed: bool = False) -> Union[List[str], Dict[str, dict]]` *(class)* — Return the transforms whose evaluated (world) axes are not perpendicular.
  - `TransformDiagnostics.get_non_orthogonal_local(cls, objects: Optional[NodeSeq] = None, tolerance: Optional[float] = None, frames: Optional[Sequence[float]] = None) -> Dict[str, float]` *(class)* — Return ``{transform: worst skew}`` for PARENT-RELATIVE axes that are
  - `TransformDiagnostics.fix_non_orthogonal_axes(cls, objects: Optional[NodeSeq] = None, dry_run: bool = False, tolerance: Optional[float] = None, quiet: bool = False, break_connections: bool = False, instance_strategy: str = 'preserve', delete_history: bool = False) -> List[str]` *(class)* — Fix non-orthogonal axes by freezing the offending transforms.

<a id="core_utils--diagnostics--uv_diag"></a>
### `core_utils/diagnostics/uv_diag.py`

UV diagnostics and repair helpers.

- **[`class UvSetCleanupResult`](mayatk/mayatk/core_utils/diagnostics/uv_diag.py#L21)** — Result of a UV set cleanup operation for a single mesh.
- **[`class UvDiagnostics`](mayatk/mayatk/core_utils/diagnostics/uv_diag.py#L43)** — Operations for inspecting and fixing common UV issues.
  - `UvDiagnostics.find_non_manifold_uvs(objects: NodeSeq) -> dict` *(static)* — Map each mesh in *objects* to its non-manifold UVs, via ``polyInfo``.
  - `UvDiagnostics.repair_non_manifold_uvs(cls, objects: NodeSeq) -> dict` *(class)* — Repair non-manifold UVs on *objects* by re-mapping the affected faces.
  - `UvDiagnostics.find_lightmap_uv_set(cls, shape, all_sets=None, names=None)` *(class)* — Detect a lightmap UV set on *shape*, or ``None``.
  - `UvDiagnostics.is_bakeable_lightmap(cls, shape, uv_set) -> bool` *(class)* — True if *uv_set* is usable as a lightmap: has UVs, non-overlapping,
  - `UvDiagnostics.cleanup_uv_sets(cls, objects: NodeSeq, remove_empty: bool = True, keep_only_primary: bool = True, rename_to_map1: bool = True, force_rename: bool = False, prefer_largest_area: bool = False, protect: Sequence[str] = (), protect_lightmaps: bool = True, dry_run: bool = False, quiet: bool = False) -> list[UvSetCleanupResult]` *(class)* — Cleanup UV sets by removing empty/secondary sets and renaming the primary to 'map1'.

<a id="core_utils--mash"></a>
### `core_utils/mash.py`

- **[`class MashNetworkNodes(object)`](mayatk/mayatk/core_utils/mash.py#L42)** — Lightweight container for the core nodes created per network.
  - `MashNetworkNodes.as_tuple(self)`
- **[`class MashToolkit(object)`](mayatk/mayatk/core_utils/mash.py#L56)** — Thin wrapper around MASH API for building and baking networks.
  - `MashToolkit.ensure_plugin_loaded()` *(static)*
  - `MashToolkit.create_network(cls, network=None, objects=None, networkName='MASH#', geometry='Mesh', distType='linearNetwork', hideOnCreate=True)` *(class)* — Create (or populate) a MASH network and return it with its core nodes.
  - `MashToolkit.bake_instancer(cls, network, instancer, bakeTranslate=True, bakeRotation=True, bakeScale=True, bakeAnimation=False, bakeVisibility=True, bakeToInstances=False, upstreamNodes=False, _getMObjectFromName=None)` *(class)* — Convert an instancer's points to real geometry.

<a id="core_utils--plugins--_plugins"></a>
### `core_utils/plugins/_plugins.py`

Every Maya plug-in mayatk loads goes through one door: :class:`Plugins`.

- **[`class Plugins`](mayatk/mayatk/core_utils/plugins/_plugins.py#L34)** — The one door every Maya plug-in load in mayatk goes through (see the
  - `Plugins.is_loaded(name: str) -> bool` *(static)* — Whether the plug-in *name* is loaded.
  - `Plugins.available(cls, name: str) -> bool` *(class)* — Whether the plug-in *name* is loaded or loads by name: its file is in
  - `Plugins.load(cls, name: str) -> None` *(class)* — Load the plug-in *name* unless it is loaded -- the one way mayatk

<a id="core_utils--preview"></a>
### `core_utils/preview.py`

Hermetic preview with replay-on-commit (H1 design).

- **[`class OperationError(Exception)`](mayatk/mayatk/core_utils/preview.py#L78)** — User-facing operation failure for the Preview message box.
- **[`class CleanupContract`](mayatk/mayatk/core_utils/preview.py#L95)** — Captures and reverses side effects of a previewed operation.
  - `CleanupContract.snapshot_created(self) -> Set[str]` — Diff the scene against the entry snapshot: what exists now that
  - `CleanupContract.add_file(self, path) -> None`
  - `CleanupContract.record_modification(self, node: str, attr: str) -> None`
  - `CleanupContract.rollback(self) -> None`
- **[`class Preview(_PreviewInternal)`](mayatk/mayatk/core_utils/preview.py#L533)** — Hermetic preview orchestrator (H1).
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

- **[`class ScriptJobManager`](mayatk/mayatk/core_utils/script_job_manager.py#L88)** — Centralized Maya scriptJob event dispatcher.
  - `ScriptJobManager.instance(cls) -> 'ScriptJobManager'` *(class)* — Return the module-wide singleton, creating it on first access.
  - `ScriptJobManager.reset(cls) -> None` *(class)* — Tear down the singleton and allow a fresh one to be created.
  - `ScriptJobManager.is_reading_file() -> bool` *(static)* — Return True while Maya reads a file (an open, import or reference
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

<a id="core_utils--undo_recorder"></a>
### `core_utils/undo_recorder.py`

Put OpenMaya edits on Maya's undo queue, as one ordinary undo step.

- **[`class UndoRecorder`](mayatk/mayatk/core_utils/undo_recorder.py#L221)** — Record OpenMaya edits as one undo step (see the module docstring).
  - `UndoRecorder.record(cls) -> Iterator[_Recorder]` *(class)* — Collect a block's OpenMaya undo objects and commit them as ONE undo step.

<a id="core_utils--units"></a>
### `core_utils/units.py`

Maya's lengths and angles, converted by the kind of query that read them.

- **[`class Units(pythontk.HelpMixin)`](mayatk/mayatk/core_utils/units.py#L37)** — Lengths and angles between Maya's units and the ones a deliverable ships.
  - `Units.working_as(linear: Optional[str] = 'cm', angle: Optional[str] = 'deg') -> '_WorkingAs'` *(static)* — Maya's working units set to *linear* and *angle* for a block or a
  - `Units.working(kind: str = 'linear') -> str` *(static)* — The working unit now: ``"cm"``, ``"m"`` ...
  - `Units.metres_per(cls, unit: str = 'internal') -> float` *(class)* — Metres in one *unit* (``"internal"``, ``"working"`` or a unit name).
  - `Units.convert(cls, value: Any, source: str = 'internal', target: str = 'm') -> Any` *(class)* — A length (or lengths) from *source* units to *target* units.
  - `Units.angle(cls, value: Any, source: str = 'working', target: str = 'deg') -> Any` *(class)* — An angle (or angles) from *source* units to *target* units.

<a id="display_utils--_display_utils"></a>
### `display_utils/_display_utils.py`

- **[`class DisplayUtils(pythontk.HelpMixin)`](mayatk/mayatk/display_utils/_display_utils.py#L22)**
  - `DisplayUtils.add_to_isolation(func: Callable) -> Callable` *(static)* — Decorator: add a function's newly created node(s) to every isolated viewport.
  - `DisplayUtils.is_templated(obj: Union[str, object]) -> bool` *(static)* — Check if a given object is templated.
  - `DisplayUtils.set_visibility(cls, elements: Union[str, object, List], visibility: bool = True, include_ancestors: bool = True, affect_layers: bool = True) -> None` *(class)* — Sets the visibility of specified elements in the Maya scene.
  - `DisplayUtils.set_hidden_in_outliner(elements: Union[str, object, List], state: bool = True, shapes: bool = True, refresh: bool = True) -> List[str]` *(static)* — Hide (or restore) DAG nodes in the Outliner via ``hiddenInOutliner``.
  - `DisplayUtils.is_visible(cls, node: str, consider_templated_visible: bool = False, consider_animated_visible: bool = False) -> bool` *(class)* — Whether *node* renders -- its own visibility AND every parent's.
  - `DisplayUtils.get_visible_geometry(cls, shapes: bool = False, consider_templated_visible: bool = False, inherit_parent_visibility: bool = False, consider_animated_visible: bool = False) -> List[str]` *(class)* — Get a list of visible geometry.
  - `DisplayUtils.get_isolated_panels() -> List[str]` *(static)* — Every model panel that currently has Isolate Select turned on.
  - `DisplayUtils.add_to_isolation_set(cls, objects: Union[str, object, List[Union[str, object]]]) -> List[str]` *(class)* — Add transform(s) to the isolation set of every isolated viewport.
  - `DisplayUtils.set_smooth_preview(cls, objects, display: int = None, level: int = None, adaptive_level: int = None, subd_comps: bool = None) -> List[str]` *(class)* — Configure smooth-mesh preview on the mesh shapes under *objects*.
  - `DisplayUtils.get_surface_shapes(objects: Union[str, object, List]) -> List[str]` *(static)* — Visible (non-intermediate) surface shapes at or under the given nodes.
  - `DisplayUtils.is_xray(cls, objects: Union[str, object, List]) -> bool` *(class)* — True when EVERY surface shape at or under *objects* is x-rayed.
  - `DisplayUtils.set_xray(cls, objects: Union[str, object, List], state: bool = True, resync: bool = True) -> List[str]` *(class)* — Set the x-ray flag on every surface shape at or under *objects*.
  - `DisplayUtils.toggle_xray(cls, objects: Union[str, object, List] = None) -> Optional[Tuple[bool, int]]` *(class)* — Uniform x-ray toggle: if ANY shape is off, turn them ALL on;
  - `DisplayUtils.resync_viewport_xray() -> None` *(static)* — Force VP2 to re-apply every object's per-object x-ray flag.
  - `DisplayUtils.clear_xray(cls) -> List[str]` *(class)* — Turn x-ray off on every surface shape in the scene ("Un-Xray All").
  - `DisplayUtils.toggle_xray_others(cls, objects=None) -> Optional[Tuple[bool, int]]` *(class)* — Uniform x-ray toggle (:meth:`toggle_xray`) over every surface shape in
  - `DisplayUtils.hide_objects(objects=None) -> List[str]` *(static)* — Hide *objects* (``cmds.hide``).
  - `DisplayUtils.show_objects(objects=None) -> List[str]` *(static)* — Show hidden *objects* (``cmds.showHidden``) -- a selection made in the
  - `DisplayUtils.cycle_component_ids() -> Optional[str]` *(static)* — Step the selected meshes' component-ID display: vertex, edge, face,
  - `DisplayUtils.cycle_normals_display() -> Optional[str]` *(static)* — Step the selected meshes' normals display: face, vertex, tangent, off.
  - `DisplayUtils.toggle_soft_edge_display() -> Optional[bool]` *(static)* — Toggle the selected meshes between soft edges only and all edges.
  - `DisplayUtils.toggle_uv_border_edges(objects=None) -> Optional[bool]` *(static)* — Uniform toggle of the UV-border-edge display on *objects*' meshes, at
  - `DisplayUtils.toggle_material_override() -> Optional[bool]` *(static)* — Toggle the focused viewport's default-material override: every
  - `DisplayUtils.set_wireframe(objects=None, state: bool = True) -> List[str]` *(static)* — Draw *objects* as wireframe, or shaded again (*state* False), through
  - `DisplayUtils.toggle_wireframe(cls, objects=None)` *(class)* — Toggle *objects* between wireframe and shaded, together: the first
  - `DisplayUtils.toggle_wireframe_inactive() -> Optional[bool]` *(static)* — Toggle the focused viewport's wireframe-on-inactive: only the selected
  - `DisplayUtils.toggle_template(cls, objects=None)` *(class)* — Template *objects* (drawn dimmed, not selectable in the viewport), or
  - `DisplayUtils.toggle_uv_checker(cls) -> Optional[bool]` *(class)* — Toggle the UV Editor's checker display.
  - `DisplayUtils.toggle_uv_distortion(cls) -> Optional[str]` *(class)* — Toggle the UV Editor's distortion shading.
  - `DisplayUtils.reset_viewport(max_res=4096)` *(static)* — Resets Viewport 2.0 to fix graphical glitches (e.g.

<a id="display_utils--color_id"></a>
### `display_utils/color_id.py`

- **[`class ColorUtils`](mayatk/mayatk/display_utils/color_id.py#L18)**
  - `ColorUtils.assign_material(obj: str, color: Tuple[float, float, float]) -> str` *(static)* — Assigns a material to an object based on the RGB value.
  - `ColorUtils.set_color_attribute(cls, obj: str, color: Tuple[float, float, float], attr_type: str, force: bool = False) -> None` *(class)* — Applies color based on the attribute type specified, optionally overriding attribute locks.
  - `ColorUtils.get_material_color(cls, obj: str) -> Optional[Tuple[float, float, float]]` *(class)* — Gets the color of the object's material (the first, on a multi-shader mesh).
  - `ColorUtils.get_wireframe_color(obj: str, normalize: bool = False) -> Optional[Tuple[float, float, float]]` *(static)* — Gets the wireframe color of the given object.
  - `ColorUtils.get_vertex_color(obj: str, vertex_id: int) -> Optional[Tuple[float, float, float]]` *(static)* — Gets the color of a specific vertex on the object.
  - `ColorUtils.get_average_vertex_color(obj: str) -> Optional[Tuple[float, float, float]]` *(static)* — Average of the object's vertex colors, or None when its mesh has none
  - `ColorUtils.get_outliner_color(obj: str) -> Optional[Tuple[float, float, float]]` *(static)* — The object's outliner color while ``useOutlinerColor`` is on, else None
  - `ColorUtils.set_vertex_color(objects: List[str], color: Tuple[float, float, float]) -> None` *(static)* — Applies the specified color to the object's vertices.
  - `ColorUtils.get_color_difference(color1: Tuple[float, float, float], color2: Tuple[float, float, float]) -> float` *(static)* — Calculate the average difference between two RGB colors.
  - `ColorUtils.add_to_color_set(cls, objects: List[str], color: Tuple[float, float, float]) -> Optional[str]` *(class)* — Group ``objects`` into a stamped ``ID_<HEX>`` objectSet;
  - `ColorUtils.get_color_set_color(cls, obj: str) -> Optional[Tuple[float, float, float]]` *(class)* — The exact color stamped on the object's ID set, or None.
  - `ColorUtils.remove_from_color_sets(cls, objects: List[str]) -> None` *(class)* — Drop ``objects`` from every stamped ID set;
- **[`class ColorId(ColorUtils)`](mayatk/mayatk/display_utils/color_id.py#L295)**
  - `ColorId.apply_color(cls, objects: List[str], color: Optional[Tuple[float, float, float]] = None, apply_to_material: bool = False, apply_to_vertex: bool = False, apply_to_wireframe: bool = False, apply_to_outliner: bool = False, set_per_color: bool = False) -> None` *(class)* — Applies color based on given criteria to objects, as one undo step.
  - `ColorId.get_objects_by_color(cls, target_color: Tuple[float, float, float], threshold: float = 0.1, check_material_color: bool = False, check_vertex_color: bool = False, check_wireframe_color: bool = False, check_outliner_color: bool = False, check_set: bool = False) -> List[str]` *(class)* — Select objects by color, with optional checks for material, vertex, wireframe, and outliner colors.
  - `ColorId.select_by_color(cls, target_color: Tuple[float, float, float], threshold: float = 0.1, **checks) -> List[str]` *(class)* — Select the objects :meth:`get_objects_by_color` finds, replacing the
  - `ColorId.get_color(cls, obj: str, check_material_color: bool = False, check_vertex_color: bool = False, check_wireframe_color: bool = False, check_outliner_color: bool = False, check_set: bool = False) -> Optional[Tuple[float, float, float]]` *(class)* — The first color ``obj`` carries on the enabled channels (0-1 RGB), or None.
  - `ColorId.lead_object() -> Optional[str]` *(static)* — The object Get Color reads: the last selected transform (Maya's lead
  - `ColorId.scene_objects() -> List[str]` *(static)* — Every object a scene-wide Color ID action covers (the panel's Ctrl+click
  - `ColorId.reset_colors(cls, objects: List[str], reset_outliner: bool = True, reset_wireframe: bool = True, reset_vertex: bool = True, reset_material: bool = True, reset_sets: bool = True) -> None` *(class)* — Resets colors to default for given objects, with options to specify which color types to reset (one…
  - `ColorId.reset_vertex_colors(objects: List[str]) -> None` *(static)* — Resets vertex colors for the given object(s), handling potential errors gracefully.

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

<a id="edit_utils--_edit_utils"></a>
### `edit_utils/_edit_utils.py`

- **[`class EditUtils(pythontk.HelpMixin, _EditUtilsInternal)`](mayatk/mayatk/edit_utils/_edit_utils.py#L674)**
  - `EditUtils.get_symmetry() -> dict` *(static)* — Maya's modeling symmetry (``symmetricModelling``): what tentacle's
  - `EditUtils.set_symmetry(axis: Optional[str] = None, state: bool = True, space: Optional[str] = None) -> dict` *(static)* — Set Maya's modeling symmetry: an axis on or off, and/or its space.
  - `EditUtils.combine_objects(objects=None, group_by_material=False, cluster_by_distance=False, threshold=10000.0, uninstance=False, **kwargs)` *(static)* — Combine multiple meshes.
  - `EditUtils.group_objects(objects=None)` *(static)* — Group the given objects (or selection), center the pivot, and rename the group.
  - `EditUtils.ungroup_objects(objects=None) -> List[str]` *(static)* — Inverse of `group_objects` — dissolve the given group(s) (or selection).
  - `EditUtils.separate_objects(objects=None, by_material: bool = False, group_by_material: bool = False, center_pivots: bool = True, rename: bool = False, uninstance: bool = False) -> List` *(static)* — Separate meshes into individual objects.
  - `EditUtils.merge_vertices(objects, tolerance=0.001, selected_only=False)` *(static)* — Merge Vertices on the given objects.
  - `EditUtils.merge_vertex_pairs(vertices)` *(static)* — Merge vertices in pairs by moving them to their center and merging.
  - `EditUtils.detach_components(components=None, duplicate: bool = True, separate: bool = True, offset: bool = False, keep_faces_together: bool = True, center_pivot: bool = True) -> Optional[List]` *(static)* — Detach mesh components (vertices or faces) from their parent mesh.
  - `EditUtils.decimate(objects=None, percentage: float = 50.0, preserve_borders: bool = True, preserve_hard_edges: bool = True, preserve_uv_borders: bool = True, preserve_quads: bool = True, symmetry: bool = False, symmetry_tolerance: float = 0.01, delete_history: bool = True) -> List[str]` *(static)* — Decimate (``polyReduce``) meshes toward a target reduction percentage.
  - `EditUtils.dissolve_coplanar(objects=None, angle_tolerance: float = 1.0, delete_history: bool = True) -> List[str]` *(static)* — Planar decimation (limited dissolve) — merge faces across near-coplanar edges.
  - `EditUtils.get_all_faces_on_axis(obj, axis='x', pivot='center', axis_frame=None)` *(static)* — Get all faces on the specified axis of an object.
  - `EditUtils.cut_along_axis(cls, objects, axis='x', pivot='center', amount=1, offset=0, spacing=0.0, distribution='linear', weight_bias=0.5, weight_curve=2.0, invert=False, ortho=False, delete=False, mirror=False, axis_frame=None)` *(class)* — Cut objects along the specified axis.
  - `EditUtils.delete_along_axis(cls, objects, axis='-x', pivot='center', delete_history=True, mirror=False, axis_frame=None)` *(class)* — Delete faces along the specified axis and optionally mirror the result.
  - `EditUtils.mirror(cls, objects, axis: str = 'x', pivot: Union[str, tuple] = 'object', mergeMode: int = -1, axis_frame: Optional[str] = None, delete_original: bool = False, center_pivot: bool = True, **kwargs)` *(class)* — Mirror geometry across a given axis.
  - `EditUtils.mirror_instance(cls, objects=None, axis: str = 'x', pivot: Union[str, tuple] = 'object', axis_frame: Optional[str] = None) -> list` *(class)* — Mirror as **instances**: each object gets a linked copy reflected
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
  - `EditUtils.get_conversion_types(cls) -> Dict[str, str]` *(class)* — The Convert menu: ``{label: MEL}`` in menu order, each a conversion
  - `EditUtils.convert_objects(cls, objects, conversion: str) -> List[str]` *(class)* — Convert *objects* (``None``: the selection) by a :meth:`get_conversion_types`
  - `EditUtils.create_curve_from_edges(edges: Optional[List[str]] = None, **kwargs)` *(static)* — Create a curve from selected polygon edges or a provided list of edges.

<a id="edit_utils--bevel"></a>
### `edit_utils/bevel.py`

- **[`class Bevel`](mayatk/mayatk/edit_utils/bevel.py#L12)**
  - `Bevel.bevel(edges, width=0.5, segments=1, autoFit=True, depth=1, mitering=0, miterAlong=0, chamfer=True, worldSpace=True, smoothingAngle=30, fillNgons=True, mergeVertices=True, mergeVertexTolerance=0.0001, miteringAngle=180, angleTolerance=180)` *(static)* — Bevels the given edges with highly customizable options for topology,

<a id="edit_utils--bridge"></a>
### `edit_utils/bridge.py`

Bridge: connect open edge loops with new faces (``polyBridgeEdge``).

- **[`class Bridge`](mayatk/mayatk/edit_utils/bridge.py#L20)** — Bridge open edge loops with new faces: the API tentacle's Bridge panel
  - `Bridge.bridge(edges, *, curve_type=None, divisions=None, smoothing_angle=None, offset=None, taper=None, twist=None, cleanup_curves=False, **kwargs)` *(static)* — Bridge open edge loops, grouped per owning mesh.
  - `Bridge.get_child_curves_from_bridge(mesh_nodes)` *(static)* — Find child curves created by polyBridgeEdge operations on mesh nodes.
  - `Bridge.cleanup_bridge_curves_and_history(mesh_nodes)` *(static)* — Clean up child curves and deformer history from mesh nodes.

<a id="edit_utils--curtain--_curtain"></a>
### `edit_utils/curtain/_curtain.py`

Procedural draped-cloth (curtain) generator for Maya.

- **[`class Rail(pythontk.Polyline)`](mayatk/mayatk/edit_utils/curtain/_curtain.py#L70)** — Rail-polyline geometry — the line a curtain hangs from.
  - `Rail.from_selection(objects) -> Optional[Tuple[List[Vec], bool]]` *(static)* — Resolve a rail polyline from a Maya selection.
  - `Rail.sample_curve(shape: str, count: int = 200) -> Tuple[List[Vec], bool]` *(static)* — Sample a NURBS curve into a dense polyline (resampled later by length).
- **[`class CurtainMesh(CurtainDrape)`](mayatk/mayatk/edit_utils/curtain/_curtain.py#L159)** — Generate a pleated, gravity-draped curtain mesh from a rail polyline.
  - `CurtainMesh.create(cls, rail: Sequence[Vec], **opts) -> str` *(class)*
  - `CurtainMesh.build(self) -> str` — Create the curtain mesh and return its transform name.
- **[`class CurtainRig`](mayatk/mayatk/edit_utils/curtain/_curtain.py#L375)** — Make a curve drive a finished curtain.
  - `CurtainRig.attach(curtain: str, curve: str, dropoff: float, cluster: bool = True) -> str` *(static)* — Wire-deform *curtain* with *curve* and add per-CV cluster controls.
- **[`class Curtain`](mayatk/mayatk/edit_utils/curtain/_curtain.py#L437)** — The Curtain tool's API: drape a curtain from a rail, and keep the
  - `Curtain.rail_from_selection(objects) -> Optional[Tuple[List[Vec], bool]]` *(static)* — The rail ``(points, closed)`` *objects* describe, or None: polygon
  - `Curtain.create(rail: Sequence[Vec], **opts) -> str` *(static)* — Drape a curtain from the *rail* points.
  - `Curtain.rail_curve(points: Sequence[Vec], closed: bool = False, curve: Optional[str] = None, select: bool = False) -> str` *(static)* — A curve with its CVs on *points*: a visible stand-in for a rail.
  - `Curtain.delete_rail_curve(curve: Optional[str]) -> None` *(static)* — Delete a :meth:`rail_curve` curve;
  - `Curtain.triangle_count(curtain: Optional[str]) -> Optional[int]` *(static)* — The *curtain* mesh's triangle count, or None when it is gone.

<a id="edit_utils--curtain--_curtain_drape"></a>
### `edit_utils/curtain/_curtain_drape.py`

Procedural draped-cloth (curtain) drape engine — pure geometry, no DCC.

- **[`class CurtainDrape(_CurtainDrapeInternal)`](mayatk/mayatk/edit_utils/curtain/_curtain_drape.py#L70)** — Drape a grid into a pleated, gravity-sagged curtain — pure math.
  - `CurtainDrape.prepare(self) -> Tuple[int, int, List[Tuple[Vec, Vec, Vec]]]` — Precompute the per-build state and return ``(u_segs, v_segs, frames)``.
  - `CurtainDrape.grid_points(self) -> Tuple[int, int, List[Vec]]` — The full draped grid: ``(u_segs, v_segs, points)``.
  - `CurtainDrape.drape(self, u, v, pos, tan, normal) -> Vec` — Place one cloth vertex.

<a id="edit_utils--cut_on_axis"></a>
### `edit_utils/cut_on_axis.py`

- **[`class CutOnAxis`](mayatk/mayatk/edit_utils/cut_on_axis.py#L12)**
  - `CutOnAxis.perform_cut_on_axis(objects, axis='-x', cuts=0, cut_offset=0, cut_spacing=0.0, distribution='linear', weight_bias=0.5, weight_curve=2.0, delete=False, mirror=False, pivot='manip', axis_frame=None)` *(static)* — Iterates over provided objects and performs cut or delete operations based on the axis specified.

<a id="edit_utils--duplicate_grid"></a>
### `edit_utils/duplicate_grid.py`

- **[`class DuplicateGrid(pythontk.LoggingMixin)`](mayatk/mayatk/edit_utils/duplicate_grid.py#L16)**
  - `DuplicateGrid.duplicate_grid(cls, objects: List[str], dimensions: Tuple[int, int, int], spacing: Union[float, Tuple[float, float, float]] = 0, mode: str = 'instance') -> Union[str, List[str]]` *(class)* — Duplicate objects in a grid pattern.

<a id="edit_utils--duplicate_linear"></a>
### `edit_utils/duplicate_linear.py`

- **[`class DuplicateLinear`](mayatk/mayatk/edit_utils/duplicate_linear.py#L17)**
  - `DuplicateLinear.duplicate_linear(objects, num_copies, translate=(0, 0, 0), rotate=(0, 0, 0), scale=(1, 1, 1), weight_bias=0.5, weight_curve=4, pivot='object', calculation_mode='weighted', instance=True)` *(static)*

<a id="edit_utils--duplicate_radial"></a>
### `edit_utils/duplicate_radial.py`

- **[`class DuplicateRadial(pythontk.LoggingMixin)`](mayatk/mayatk/edit_utils/duplicate_radial.py#L21)**
  - `DuplicateRadial.duplicate_radial(objects: List[str], num_copies: int, start_angle: float = 0, end_angle: float = 360, weight_bias: float = 0.5, weight_curve: float = 0.5, rotate_axis: str = 'y', offset: Tuple[float, float, float] = (0, 0, 0), translate: Tuple[float, float, float] = (0, 0, 0), rotate: Tuple[float, float, float] = (0, 0, 0), scale: Tuple[float, float, float] = (1, 1, 1), pivot: Union[str, Tuple[float, float, float]] = 'object', keep_original: bool = False, instance: bool = False, combine: bool = False, suffix: bool = True) -> Dict[str, List[str]]` *(static)* — Duplicate objects in a radial pattern.
  - `DuplicateRadial.regroup_copies(copies: Dict[str, List[str]]) -> None` *(static)* — Regroup each source's committed copies under a fresh ``*_array`` group.

<a id="edit_utils--dynamic_pipe"></a>
### `edit_utils/dynamic_pipe.py`

- **[`class DynamicPipe`](mayatk/mayatk/edit_utils/dynamic_pipe.py#L9)** — Build a pipe-style mesh by lofting NURBS circles parented to a chain of locators.
  - `DynamicPipe.from_selection(cls, **kwargs) -> 'DynamicPipe'` *(class)* — A pipe through the selected transforms in the order they were
  - `DynamicPipe.create_pipe_geometry(self, segments_to_loft: Optional[Sequence[int]] = None) -> List[str]` — Loft consecutive circle pairs to produce pipe segments.

<a id="edit_utils--macros--_macros"></a>
### `edit_utils/macros/_macros.py`

- **[`class MacroManager(pythontk.HelpMixin)`](mayatk/mayatk/edit_utils/macros/_macros.py#L25)** — Assign macro functions to hotkeys.
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
- **[`class DisplayMacros`](mayatk/mayatk/edit_utils/macros/_macros.py#L872)**
  - `DisplayMacros.m_component_id_display()` *(static)* — Toggle Component Id Display through vertices, edges, faces, UVs, and off.
  - `DisplayMacros.m_normals_display()` *(static)* — Toggle face normals, vertex normals, tangents, and off.
  - `DisplayMacros.m_soft_edge_display()` *(static)* — Toggle Soft Edge Display.
  - `DisplayMacros.m_toggle_visibility()` *(static)* — Toggle visibility of the selected objects, keeping them selected.
  - `DisplayMacros.m_toggle_uv_border_edges(objects)` *(static)* — Toggle the display of UV border edges for the given objects.
  - `DisplayMacros.m_back_face_culling(objects) -> None` *(static)* — Toggle Back-Face Culling on selected objects, or on all objects if none are selected.
  - `DisplayMacros.m_isolate_selected() -> None` *(static)* — Isolate the current selection in the active 3D viewport.
  - `DisplayMacros.m_cycle_display_state(objects) -> None` *(static)* — Cycle the display state of the selection: Visible -> XRay -> Templated -> Hidden.
  - `DisplayMacros.m_wireframe_toggle(objects) -> None` *(static)* — Toggle the selected objects between wireframe and shaded, together.
  - `DisplayMacros.m_grid() -> bool` *(static)* — Toggle the grid.
  - `DisplayMacros.m_grid_and_image_planes() -> None` *(static)* — Toggle grid and image plane visibility together.
  - `DisplayMacros.m_frame(cls, steps: int = 2, adjust_clipping: bool = True) -> None` *(class)* — Frame the selection at the ideal working distance;
  - `DisplayMacros.m_smooth_preview(cls, objects) -> None` *(class)* — Cycle smooth mesh preview: off -> on / no wireframe -> on / full wireframe.
  - `DisplayMacros.m_wireframe() -> None` *(static)* — Toggles the wireframe display state.
  - `DisplayMacros.m_material_override()` *(static)* — Toggle the viewport's default-material override.
  - `DisplayMacros.m_shading(cls) -> None` *(class)* — Toggles viewport display mode between wireframe, smooth shaded with textures off,
  - `DisplayMacros.m_lighting(cls) -> None` *(class)* — Toggles viewport lighting between different states: default, all lights, active lights,
  - `DisplayMacros.m_cycle_background(cls) -> str` *(class)* — Cycle the viewport background: Maya's Alt+B cycle plus Mid Gray and White.
- **[`class EditMacros`](mayatk/mayatk/edit_utils/macros/_macros.py#L1560)**
  - `EditMacros.m_group(objects=None)` *(static)* — Group the given objects (or selection), center the pivot, and rename the group.
  - `EditMacros.m_ungroup(objects=None)` *(static)* — Ungroup the selected group(s) — children keep their world transforms.
  - `EditMacros.m_combine(objects=None, group_by_material=False, cluster_by_distance=False, threshold=10000.0, **kwargs)` *(static)* — Combine multiple meshes.
  - `EditMacros.m_boolean(objects, repair_mesh=True, keep_boolean=True, **kwargs)` *(static)* — Perform a boolean operation on two meshes using cmds, managing shorthand and full parameter names d…
  - `EditMacros.m_lock_vertex_normals(objects)` *(static)* — Toggle lock/unlock vertex normals.
  - `EditMacros.m_paste_and_rename() -> None` *(static)* — Paste and rename by removing 'pasted__' prefix and reference file names,
  - `EditMacros.m_multi_component() -> None` *(static)* — Enable the multi-component selection mask.
  - `EditMacros.m_merge_vertices(objects, tolerance=0.001) -> None` *(static)* — Merge vertices within a small distance tolerance.
- **[`class SelectionMacros`](mayatk/mayatk/edit_utils/macros/_macros.py#L1819)**
  - `SelectionMacros.m_object_selection() -> None` *(static)* — Set object selection mask.
  - `SelectionMacros.m_vertex_selection() -> None` *(static)* — Set vertex selection mask.
  - `SelectionMacros.m_edge_selection() -> None` *(static)* — Set edge selection mask.
  - `SelectionMacros.m_face_selection() -> None` *(static)* — Set face selection mask.
  - `SelectionMacros.m_invert_selection() -> None` *(static)* — Invert the current selection of geometry or components.
  - `SelectionMacros.m_toggle_selectability(objects)` *(static)* — Toggle selectability of the given objects.
  - `SelectionMacros.m_toggle_UV_select_type() -> None` *(static)* — Toggles between UV shell and UV component selection.
  - `SelectionMacros.m_invert_component_selection() -> None` *(static)* — Invert the component selection on the currently selected objects.
- **[`class UiMacros`](mayatk/mayatk/edit_utils/macros/_macros.py#L1982)**
  - `UiMacros.m_toggle_panels(toggle_menu: bool = True, toggle_panels: bool = True) -> None` *(static)* — Toggle UI toolbars and menu bar in sync.
- **[`class AnimationMacros`](mayatk/mayatk/edit_utils/macros/_macros.py#L2018)**
  - `AnimationMacros.m_set_selected_keys(objects) -> None` *(static)* — Set keys for any attributes (channels) that are selected in the channel box.
  - `AnimationMacros.m_unset_selected_keys(objects) -> None` *(static)* — Un-set keys for any attributes (channels) that are selected in the channel box.
- **[`class Macros(MacroManager, DisplayMacros, EditMacros, SelectionMacros, AnimationMacros, UiMacros)`](mayatk/mayatk/edit_utils/macros/_macros.py#L2045)**

<a id="edit_utils--naming--_naming"></a>
### `edit_utils/naming/_naming.py`

- **[`class Naming(pythontk.HelpMixin, pythontk.LoggingMixin)`](mayatk/mayatk/edit_utils/naming/_naming.py#L20)** — Batch find / rename / suffix scene nodes.
  - `Naming.SUFFIX_TYPES(cls) -> Tuple[Tuple[str, str, str, str], ...]` — ``(keyword, affix, label, type key)`` -- the live convention, joined
  - `Naming.affix_rules(cls, overrides: Optional[Dict[str, str]] = None, modes: Optional[Dict[str, str]] = None) -> Dict[str, 'ptk.AffixRule']` *(class)* — ``{Maya type key: AffixRule}`` -- the convention bound to this host.
  - `Naming.rename(cls, objects: Union[str, 'object', List[Union[str, 'object']]], to: str, fltr: str = '', regex: bool = False, ignore_case: bool = False, retain_suffix: bool = False, valid_suffixes: Optional[List[str]] = None, collapse_padding: bool = True, dry_run: bool = False) -> List[str]` *(class)* — Rename scene objects based on specified patterns and filters, ensuring compliance with Maya's namin…
  - `Naming.scene_objects(cls) -> List[str]` *(class)* — Every renameable node in the scene — the naming tools' "Scene" scope.
  - `Naming.scope_objects(cls, scene: bool = False) -> List[str]` *(class)* — What a naming operation acts on in a scene scope: every renameable
  - `Naming.select_by_name(cls, pattern: str, regex: bool = False, ignore_case: bool = False, locators_only: bool = False) -> Tuple[List[str], int]` *(class)* — Select the scene nodes whose name matches *pattern* (Find).
  - `Naming.select_link(cls, node: str) -> str` *(class)* — A report link labeled with *node*'s leaf name that selects it.
  - `Naming.generate_unique_name(cls, base_name, suffix='_', padding=3)` *(class)* — Generate a unique name based on the base_name.
  - `Naming.conform_shape_names(cls, objects: Union[str, 'object', List[Union[str, 'object']], None] = None, force: bool = False) -> List[Tuple[str, str]]` *(class)* — Rename shape nodes to Maya's conventional ``<transform>Shape`` form.
  - `Naming.strip_illegal_chars(input_data, replace_with='_')` *(static)* — Strips illegal characters from a string or a list of strings, replacing them with a specified chara…
  - `Naming.strip_chars(cls, objects: Union[str, object, List[Union[str, object]]], num_chars: int = 1, trailing: bool = False, dry_run: bool = False) -> List[str]` *(class)* — Deletes leading or trailing characters from the names of the provided objects,
  - `Naming.set_case(cls, objects=None, case='capitalize', dry_run: bool = False)` *(class)* — Rename objects following the given case.
  - `Naming.type_key(cls, obj: str) -> str` *(class)* — Resolve a node to its suffix-by-type key (see ``SUFFIX_TYPES``).
  - `Naming.affix_for(cls, obj: str, overrides: Optional[Dict[str, str]] = None, modes: Optional[Dict[str, str]] = None) -> 'ptk.AffixRule'` *(class)* — The convention's affix rule for *obj*'s OWN type.
  - `Naming.suffix_by_type(cls, objects: Union[str, object, List[Union[str, object]]], group_suffix: Optional[str] = None, locator_suffix: Optional[str] = None, joint_suffix: Optional[str] = None, mesh_suffix: Optional[str] = None, nurbs_curve_suffix: Optional[str] = None, camera_suffix: Optional[str] = None, light_suffix: Optional[str] = None, display_layer_suffix: Optional[str] = None, ik_handle_suffix: Optional[str] = None, nurbs_surface_suffix: Optional[str] = None, cluster_suffix: Optional[str] = None, lattice_suffix: Optional[str] = None, skin_cluster_suffix: Optional[str] = None, blend_shape_suffix: Optional[str] = None, constraint_suffix: Optional[str] = None, material_suffix: Optional[str] = None, shading_group_suffix: Optional[str] = None, texture_suffix: Optional[str] = None, set_suffix: Optional[str] = None, custom_suffixes: Optional[Dict[str, str]] = None, affix_mode: Optional[str] = None, affix_modes: Optional[Dict[str, str]] = None, strip: Union[str, List[str]] = None, strip_trailing_ints: bool = False, strip_trailing_underscores: bool = False, strip_trailing_padding: bool = True, dry_run: bool = False) -> List[str]` *(class)* — Apply each object's conventional type **affix**, stripping any other it carries.
  - `Naming.append_location_based_suffix(cls, objects, first_obj_as_ref=False, alphabetical=False, strip_trailing_ints=True, strip_defined_suffixes=True, valid_suffixes=None, reverse=False, independent_groups=False, dry_run: bool = False)` *(class)* — Rename objects with a suffix defined by its location from origin.

<a id="edit_utils--primitives"></a>
### `edit_utils/primitives.py`

Primitive creation utilities for Maya.

- **[`class Primitives`](mayatk/mayatk/edit_utils/primitives.py#L26)** — Utilities for creating primitive objects in Maya.
  - `Primitives.create_default_primitive(cls, baseType, subType, **kwargs)` *(class)* — Create a primitive object with flexible parameters.
  - `Primitives.get_primitive_types(cls) -> dict` *(class)* — The Create menu: ``{category: [label, ...]}`` in menu order, each label
  - `Primitives.create(cls, category: str, name: str)` *(class)* — Make one Create-menu item (:meth:`get_primitive_types`): a primitive or
  - `Primitives.create_circle(axis='y', numPoints=12, radius=5, center=[0, 0, 0], mode=0, name='pCircle', history=False)` *(static)* — Create a circular polygon plane.

<a id="edit_utils--rack_builder"></a>
### `edit_utils/rack_builder.py`

Parametric EIA-310 (19-inch) equipment-rack generator.

- **[`class EIA310`](mayatk/mayatk/edit_utils/rack_builder.py#L52)** — EIA-310-D / IEC 60297-3-100 rack dimensions, in millimetres.
  - `EIA310.u_to_mm(cls, u: float) -> float` *(class)* — Rack units → millimetres.
- **[`class OccupantSpec(pythontk.SchemaSpec)`](mayatk/mayatk/edit_utils/rack_builder.py#L77)** — One piece of installed rack gear, described by its front panel.
- **[`class BaySpec(pythontk.SchemaSpec)`](mayatk/mayatk/edit_utils/rack_builder.py#L100)** — A single rack bay: a mounting frame holding an ordered occupant stack.
- **[`class RackSpec(pythontk.SchemaSpec)`](mayatk/mayatk/edit_utils/rack_builder.py#L118)** — A complete equipment cabinet — one or more bays inside an enclosure.
- **[`class RackBuilder(pythontk.LoggingMixin)`](mayatk/mayatk/edit_utils/rack_builder.py#L149)** — Emit Maya geometry for a :class:`RackSpec`.
  - `RackBuilder.build(self, group: bool = True) -> str` — Build the whole rack;
  - `RackBuilder.from_dict(cls, data: dict) -> 'RackBuilder'` *(class)* — Build directly from a raw spec ``dict`` (validated by RackSpec).

<a id="edit_utils--selection"></a>
### `edit_utils/selection.py`

- **[`class Selection(pythontk.LoggingMixin, pythontk.HelpMixin)`](mayatk/mayatk/edit_utils/selection.py#L20)** — Utilities for advanced Maya selection operations.
  - `Selection.select_by_type(selection_type: str, objects: List[Union[str, object]] = None, mode: str = 'replace') -> List[object]` *(static)* — Select objects by type with comprehensive type support.
  - `Selection.select_children(objects: List[Union[str, object]]) -> Set[object]` *(static)* — Select the immediate children of the given objects.
  - `Selection.select_hierarchy_above(objects: List[Union[str, object]]) -> Set[object]` *(static)* — Select all parent objects in the hierarchy above the given objects.
  - `Selection.select_hierarchy_below(objects: List[Union[str, object]]) -> Set[object]` *(static)* — Select all child objects in the hierarchy below the given objects.
  - `Selection.rig_locked_meshes() -> List[str]` *(static)* — Mesh shapes a rig makes unpickable: their display type is DRIVEN
  - `Selection.get_rig_selection_mode(cls) -> str` *(class)* — The live mode (see :meth:`set_rig_selection_mode`);
  - `Selection.set_rig_selection_mode(cls, mode: str) -> List[str]` *(class)* — How object picking treats rigged parts.
  - `Selection.get_available_selection_types() -> List[str]` *(static)* — Get a list of all available selection types.
  - `Selection.get_selection_categories() -> dict` *(static)* — Get a dictionary of selection types organized by category.

<a id="edit_utils--snap"></a>
### `edit_utils/snap.py`

- **[`class Snap(pythontk.HelpMixin)`](mayatk/mayatk/edit_utils/snap.py#L16)** — Vertex and mesh snapping utilities.
  - `Snap.source_and_target()` *(static)* — The selection as ``(sources, target)``: the last selected node is the
  - `Snap.snap_to_closest_vertex(obj1, obj2, tolerance=10.0, freeze_transforms=False)` *(static)* — Snap the vertices from object one to the closest verts on object two.
  - `Snap.snap_to_surface(source_meshes, target_mesh, offset: float = None, threshold: float = None, invert: bool = False) -> int` *(static)* — Snap source mesh vertices to the closest point on a target surface.
  - `Snap.snap_to_grid(objects=None, grid_size: float = 1.0, axes: str = 'xyz') -> int` *(static)* — Snap object pivots or vertices to the nearest grid point.

<a id="env_utils--_env_utils"></a>
### `env_utils/_env_utils.py`

- **[`class EnvUtils(pythontk.HelpMixin)`](mayatk/mayatk/env_utils/_env_utils.py#L18)**
  - `EnvUtils.get_env_info(key=None)` *(static)* — Fetch information about the current Maya environment.
  - `EnvUtils.saved_scene_path() -> str` *(static)* — The open scene's path, or ``""`` when it has never been saved.
  - `EnvUtils.get_linear_unit() -> str` *(static)* — The working linear unit's full name (``"centimeter"``), a key of
  - `EnvUtils.set_linear_unit(unit: str) -> str` *(static)* — Set the working linear unit: *unit* a key of :attr:`SCENE_UNIT_VALUES`,
  - `EnvUtils.frame_rates() -> Dict[str, float]` *(static)* — ``{time unit: fps}`` for the rates the working time unit takes: Maya's
  - `EnvUtils.autosave_settings() -> Dict[str, float]` *(static)* — Maya's autosave as ``{"max_backups": int, "interval": minutes}``;
  - `EnvUtils.set_autosave_settings(max_backups: Optional[int] = None, interval: Optional[float] = None) -> None` *(static)* — Set Maya's autosave; ``None`` leaves a setting as it is.
  - `EnvUtils.default_artifact_dir(cls) -> str` *(class)* — Return a sensible default directory for exported/baked artifacts.
  - `EnvUtils.scene_artifact_path(cls, suffix: str) -> str` *(class)* — A file named for the open scene, beside it: ``<scene dir>/<stem><suffix>``.
  - `EnvUtils.append_maya_paths(maya_version=None)` *(static)* — Appends various Maya-related paths to the system's Python environment and sys.path.
  - `EnvUtils.is_plugin_loaded(plugin_name) -> bool` *(static)* **DEPRECATED (remove in 0.23.0)** — Moved to :meth:`Plugins.is_loaded`, beside the one plug-in door.
  - `EnvUtils.load_plugin(cls, plugin_name)` *(class)* **DEPRECATED (remove in 0.23.0)** — Moved to :meth:`Plugins.load`, the one door every plug-in load goes
  - `EnvUtils.vray_plugin(load=False, unload=False, query=False)` *(static)* — Load/Unload/Query the Maya Vray Plugin.
  - `EnvUtils.python_executable() -> Optional[str]` *(static)* — This Maya's own interpreter (``bin/mayapy``): the one pip must drive to
  - `EnvUtils.get_recent_files(index=None)` *(static)* — Get a list of recent files sorted by modification time.
  - `EnvUtils.get_recent_projects(index=None, format='standard')` *(static)* — Get a list of recently set projects.
  - `EnvUtils.find_autosave_directories()` *(static)* — Search for and compile a list of existing autosave directories based on
  - `EnvUtils.get_recent_autosave(cls, filter_time=None, timestamp_format='%Y-%m-%d %H:%M:%S')` *(class)* — Retrieves a list of recent autosave files from Maya autosave directories, optionally filtered by ag…
  - `EnvUtils.find_workspaces(root_dir: str, return_type: str = 'dir', ignore_empty: bool = True, recursive: bool = True, file_types: Optional[tuple] = None) -> list` *(static)* — Find Maya workspaces under a root directory.
  - `EnvUtils.get_workspace_scenes(root_dir: Optional[str] = None, full_path: bool = True, recursive: bool = False, omit_autosave: bool = True, file_types=None) -> list[str]` *(static)* — Return a list of Maya scene files (.ma/.mb) from the given or current workspace directory.
  - `EnvUtils.scene_has_content() -> bool` *(static)* — True if the open scene holds authored data — any transform beyond Maya's
  - `EnvUtils.scene_status() -> Dict[str, Any]` *(static)* — The scene's modelling and session state as plain values: what a
  - `EnvUtils.scene_settings() -> Dict[str, float]` *(static)* — The live scene's time setup as the bridges' ``scene`` record (see
  - `EnvUtils.apply_scene_settings(settings: Dict[str, Any]) -> list` *(static)* — Apply a ``scene`` record (any subset of :attr:`SCENE_SETTINGS_KEYS`) to the
  - `EnvUtils.find_workspace_using_path(cls, scene_path: Optional[str] = None) -> Optional[str]` *(class)* — Determine the workspace directory for a given scene by moving up directory levels until a workspace…
  - `EnvUtils.is_workspace(path: str) -> bool` *(static)* — True if *path* is a Maya workspace root: a folder holding a
  - `EnvUtils.current_workspace(path: Optional[str] = None) -> Optional[ptk.Workspace]` *(static)* — The active project as a ``pythontk.Workspace`` (root + parsed file rules), or None.
  - `EnvUtils.set_current_workspace(root: str) -> str` *(static)* — Make *root* Maya's active project the way File > Set Project does:
  - `EnvUtils.workspace_root(path: Optional[str] = None) -> str` *(static)* — Absolute root of the current workspace, or ''.
  - `EnvUtils.set_scene_source(scene: str, source: Optional[str] = None) -> Optional[str]` *(static)* **DEPRECATED (remove in 0.30.0)** — Moved to :meth:`DataNodes.set_scene_source`, which records the stand-in
  - `EnvUtils.twin_save_target(scene: Optional[str] = None) -> Optional[str]` *(static)* **DEPRECATED (remove in 0.30.0)** — Moved to :meth:`DataNodes.twin_save_target`.
  - `EnvUtils.scene_source(scene: Optional[str] = None) -> Optional[str]` *(static)* **DEPRECATED (remove in 0.30.0)** — Moved to :meth:`DataNodes.scene_source`.
  - `EnvUtils.scene_project_root() -> Optional[str]` *(static)* **DEPRECATED (remove in 0.30.0)** — Moved to :meth:`DataNodes.scene_project_root`.
  - `EnvUtils.scene_workspace() -> Optional[ptk.Workspace]` *(static)* **DEPRECATED (remove in 0.30.0)** — Moved to :meth:`DataNodes.scene_workspace`.
  - `EnvUtils.scene_source_images_dir(subfolder: str = '', create: bool = False) -> str` *(static)* **DEPRECATED (remove in 0.30.0)** — Moved to :meth:`DataNodes.scene_source_images_dir`.
  - `EnvUtils.scenes_dir(path: Optional[str] = None) -> str` *(static)* — The workspace's scene folder — its ``scene`` rule → an existing ``scenes/`` →
  - `EnvUtils.source_images_dir(path: Optional[str] = None) -> str` *(static)* — The workspace's texture folder — its ``sourceImages`` rule → an existing
  - `EnvUtils.texture_search_dirs(path: Optional[str] = None) -> List[str]` *(static)* — Where this scene's map files can be found NOW, most specific first.
  - `EnvUtils.list_workspace_templates() -> list` *(static)* — Saved workspace-template names.
  - `EnvUtils.workspace_template_rules(name: Optional[str] = None) -> dict` *(static)* — File rules for building a NEW workspace: the *name*d (default: active /
  - `EnvUtils.save_workspace_template(name: str, rules: Optional[dict] = None) -> str` *(static)* — Save *rules* as workspace template *name* and make it the active default for new
  - `EnvUtils.delete_workspace_template(name: str) -> bool` *(static)* — Delete the user template *name*.
  - `EnvUtils.create_workspace(root: str, rules: Optional[dict] = None, create_dirs: bool = True) -> Optional[ptk.Workspace]` *(static)* — Create a marked workspace at *root* — File ▸ Project Window ▸ New, scripted.
  - `EnvUtils.promote_workspace(root: Optional[str] = None) -> Optional[ptk.Workspace]` *(static)* — Mark *root* (default: the active project's folder) as a shared Maya/Blender
  - `EnvUtils.reference_scene(file_path)` *(static)* — Reference a scene file — a Maya scene, an FBX, or a USD layer.
  - `EnvUtils.remove_reference(file_path)` *(static)* — Remove a reference to a Maya scene.
  - `EnvUtils.clear_orphan_foster_parents() -> list` *(static)* — Dissolve the foster parents no reference owns any more.
  - `EnvUtils.is_referenced(file_path)` *(static)* — Check if a Maya scene is referenced.
  - `EnvUtils.get_reference_nodes(file_path)` *(static)* — Get the nodes from a referenced Maya scene.
  - `EnvUtils.list_reference_nodes(top_level: bool = True, file_less: bool = False) -> list` *(static)* — The scene's reference nodes, screened to the ones Maya can answer for.
  - `EnvUtils.list_references(cls, top_level: bool = True) -> list` *(class)* — The file behind each reference in the current Maya scene.
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

- [`DEFAULTS`](mayatk/mayatk/env_utils/blender_bridge/_blender_bridge.py#L67) — constant
- **[`class BlenderBridge(MayaExportMixin, pythontk.ScriptLaunchBridge)`](mayatk/mayatk/env_utils/blender_bridge/_blender_bridge.py#L196)** — Export the Maya selection and run a chosen Blender import template.
  - `BlenderBridge.blender_path(self) -> Optional[str]` *(property)*
  - `BlenderBridge.params_registry(cls)` *(class)* — The parameter registry (``parameters.Parameters``, a
  - `BlenderBridge.params_defaults(self) -> Dict[str, Any]`
  - `BlenderBridge.render_context(self, params: Dict[str, Any]) -> Dict[str, str]`
  - `BlenderBridge.bake_lightmaps(self, out: Optional[str] = None, objects: Optional[List[Any]] = None, *, environment_hdr: Optional[str] = None, quality: Optional[str] = None, resolution: Optional[int] = None, samples: Optional[int] = None, bounces: Optional[int] = None, packing: Optional[str] = None, scene_lights: Optional[bool] = None, light_strength: Optional[float] = None, timeout: Optional[float] = None, reassemble: bool = True, **params: Any) -> Optional[Dict[str, Any]]` — Bake the scene's lightmaps (or *objects*') in a headless Blender;
  - `BlenderBridge.default_output_path(cls, template: str) -> str` *(class)* — Derive where a ``BRIDGE_OUTPUT = ("auto",)`` template's artifact goes.
  - `BlenderBridge.reassemble_lightmaps(self, manifest_path: str, objects: Optional[List[Any]] = None) -> Dict[str, str]` — Wire a finished Blender bake back into this Maya scene.
  - `BlenderBridge.list_templates() -> List[Path]` *(static)* — User-visible templates in ``templates/`` (skips underscore-prefixed).
  - `BlenderBridge.template_modes(cls, template_path: Path) -> Tuple[str, ...]` *(class)* — Modes a template declares via ``BRIDGE_MODES``;
  - `BlenderBridge.list_template_modes(cls) -> List[Tuple[str, str]]` *(class)* — ``[(stem, mode), ...]`` for every (template, mode) pairing.
  - `BlenderBridge.template_path(stem: str) -> Path` *(static)* — The template file for a combo entry's *stem*.
  - `BlenderBridge.template_output_ext(cls, template_path) -> str` *(class)* — Artifact extension a template declares via ``BRIDGE_OUTPUT_EXT``.
  - `BlenderBridge.template_output_mode(cls, template_path) -> str` *(class)* — ``"auto"`` or ``"prompt"`` -- how a ``save_as`` template's output path is chosen.
  - `BlenderBridge.template_timeout(cls, template_path) -> Optional[float]` *(class)* — Seconds a template declares via ``BRIDGE_TIMEOUT``, else ``None`` (spec default).

<a id="env_utils--blender_bridge--_scene_import"></a>
### `env_utils/blender_bridge/_scene_import.py`

Import a Blender scene (.blend) or a glTF container (.glb/.gltf) into Maya via a

- [`BAKE_CACHE_KIND`](mayatk/mayatk/env_utils/blender_bridge/_scene_import.py#L82) — constant
- [`BAKE_INFO_SOURCE`](mayatk/mayatk/env_utils/blender_bridge/_scene_import.py#L87) — constant
- [`BAKE_INFO_KEY`](mayatk/mayatk/env_utils/blender_bridge/_scene_import.py#L88) — constant
- [`SUPPORTED_EXTENSIONS`](mayatk/mayatk/env_utils/blender_bridge/_scene_import.py#L98) — constant
- [`GLTF_EXTENSIONS`](mayatk/mayatk/env_utils/blender_bridge/_scene_import.py#L109) — constant
- [`CONVERTIBLE_EXTENSIONS`](mayatk/mayatk/env_utils/blender_bridge/_scene_import.py#L112) — constant
- [`BAKE_SOURCE_EXTENSIONS`](mayatk/mayatk/env_utils/blender_bridge/_scene_import.py#L122) — constant
- [`BAKE_SOURCE_SUFFIX`](mayatk/mayatk/env_utils/blender_bridge/_scene_import.py#L127) — constant
- [`USD_EXTENSIONS`](mayatk/mayatk/env_utils/blender_bridge/_scene_import.py#L131) — constant
- **[`class BlenderSceneImport(pythontk.LoggingMixin, _BlenderSceneImportInternal)`](mayatk/mayatk/env_utils/blender_bridge/_scene_import.py#L404)** — Engine: convert a .blend to FBX via headless Blender, then import it.
  - `BlenderSceneImport.blender_path(self) -> Optional[str]` *(property)* — The Blender executable (explicit, or discovered via the bridge's AppSpec).
  - `BlenderSceneImport.require_blender(self) -> str` — Return :attr:`blender_path` or raise the spec's not-found error.
  - `BlenderSceneImport.find_scenes(root_dir: str, recursive: bool = False, extensions: Optional[Sequence[str]] = None) -> List[str]` *(static)* — Every importable Blender scene (``.blend``) under *root_dir* — sorted abs paths.
  - `BlenderSceneImport.scene_has_complex_animation(cls, src_path: str) -> bool` *(class)* — Cheap pre-conversion probe: does the ``.blend`` carry rig logic
  - `BlenderSceneImport.render_script(self, src_path: str, out_path: str, *, via: str = 'fbx', embed_textures: bool = False, include_animation: bool = True, texture_dir: str = '', rig_mode: str = 'auto') -> str` — Render the Blender-side conversion script (exposed for tests/preview).
  - `BlenderSceneImport.convert(self, src_path: str, out_path: str, *, via: str = 'fbx', timeout: Optional[float] = None, texture_dir: Optional[str] = None, on_output: Optional[Callable[[Optional[str]], Optional[bool]]] = None, **script_opts: Any) -> 'ptk.ScriptRunResult'` — Convert *src_path* to *out_path* in a fresh headless Blender (blocking).
  - `BlenderSceneImport.import_scene(self, src_path: str, *, via: str = 'fbx', cleanup: bool = True, use_cache: bool = True, timeout: Optional[float] = None, fbx_options: Optional[Dict[str, Any]] = None, shader_type: str = 'stingray', scene_settings: Any = 'auto', scene_data: bool = True, progress: Optional[Callable[..., Optional[bool]]] = None, rig_mode: str = 'auto', **script_opts: Any) -> List[str]` — Import the Blender scene at *src_path*;
  - `BlenderSceneImport.import_payload(self, payload_path: str, *, via: str = 'fbx', fbx_options: Optional[Dict[str, Any]] = None, shader_type: str = 'stingray', adopt_scene: bool = False, scene_data: bool = True, step: Optional[Callable[[int, int, str], Any]] = None, media_home: Optional[str] = None) -> List[str]` — Import the conversion payload at *payload_path* -- the FBX or USD plus
  - `BlenderSceneImport.mayapy_path(self) -> Optional[str]` *(property)* — The headless ``mayapy`` used for the bake — this host's own interpreter.
  - `BlenderSceneImport.require_mayapy(self) -> str` — Return :attr:`mayapy_path` or raise an error naming what's missing.
  - `BlenderSceneImport.render_bake_script(self, src_path: str, out_path: str, info: Optional[Mapping[str, str]] = None, media_home: Optional[str] = None, project: Optional[str] = None) -> str` — Render the Maya-side intermediate->.ma bake script (exposed for
  - `BlenderSceneImport.bake(self, src_path: str, out_path: str, *, timeout: Optional[float] = None, on_output: Optional[Callable[[Optional[str]], Optional[bool]]] = None, info: Optional[Mapping[str, str]] = None, media_home: Optional[str] = None, project: Optional[str] = None) -> Any` — Bake the USD/FBX intermediate *src_path* into the .ma at *out_path* in a
  - `BlenderSceneImport.bake_scene(self, src_path: str, *, via: str = 'fbx', use_cache: bool = True, timeout: Optional[float] = None, progress: Optional[Callable[..., Optional[bool]]] = None, rig_mode: str = 'auto', project: Optional[str] = None, **script_opts: Any) -> str` — Bake *src_path* to a ``.ma`` in the project's bake cache and return its
  - `BlenderSceneImport.bake_dir(cls, project: Optional[str] = None, create: bool = False) -> Optional[str]` *(class)* — Where the bakes of *project* (default: the open scene's) live: its
  - `BlenderSceneImport.bake_slot(cls, src_path: str, project: Optional[str] = None) -> Optional[str]` *(class)* — Where *project*'s (default: the open scene's) bake of *src_path* lives
  - `BlenderSceneImport.bake_info(cls, baked_path: str) -> Dict[str, str]` *(class)* — The ``fileInfo`` records of the ``.ma`` at *baked_path*, read from its
  - `BlenderSceneImport.bake_source(cls, baked_path: str) -> Optional[str]` *(class)* — The foreign scene *baked_path* was baked from, or None if it is not a bake.
  - `BlenderSceneImport.stamp_bake_info(cls, baked_path: str, info: Mapping[str, str]) -> None` *(class)* — Write *info* into the ``.ma`` *baked_path*'s header as ``fileInfo``
  - `BlenderSceneImport.rehome_bake(self, baked_path: str, project: Optional[str] = None) -> Optional[str]` — The project bake standing in for the bake *baked_path* that lives in
  - `BlenderSceneImport.purge_unused_bakes(self, project: Optional[str] = None, dry_run: bool = False) -> List[str]` — Remove the bakes in *project*'s bake cache that no scene references.
  - `BlenderSceneImport.collapse_nested_levels(cls, nodes: List[str], joints: bool = False) -> int` *(class)* — Remove the redundant level a round trip nests around a node;

<a id="env_utils--blender_bridge--parameters"></a>
### `env_utils/blender_bridge/parameters.py`

Registry of user-tunable Blender-bridge parameters exposed to the panel.

- [`PARAMS`](mayatk/mayatk/env_utils/blender_bridge/parameters.py#L34) — constant
- **[`class Parameters(ParamRegistry)`](mayatk/mayatk/env_utils/blender_bridge/parameters.py#L508)** — Parameters — module namespace.
  - `Parameters.affix_parts(value: 'Any', *, default: str = 'suffix') -> 'tuple[str, str]'` *(static)* — ``(prefix, suffix)`` for an ``affix`` param value (delegates to uitk).
  - `Parameters.render_context(cls, values: 'dict[str, Any]') -> 'dict[str, str]'` *(class)* — Format *values* for ``StrUtils.replace_delimited`` using Python literals.

<a id="env_utils--blender_bridge--templates--_bake_scene"></a>
### `env_utils/blender_bridge/templates/_bake_scene.py`

Import a converted intermediate (USD or FBX) headlessly (mayapy) and save it as a ``.ma``

- [`covering_take(mel, path)`](mayatk/mayatk/env_utils/blender_bridge/templates/_bake_scene.py#L94) — ``(index, names)``: the 1-based index of the take whose span covers every
- [`import_source(cmds)`](mayatk/mayatk/env_utils/blender_bridge/templates/_bake_scene.py#L120) — Import *SRC_FILE* into the empty standalone scene without mayatk;
- [`restore_usd_locators(cmds, new_nodes)`](mayatk/mayatk/env_utils/blender_bridge/templates/_bake_scene.py#L172) — Give point-marker Empties their locator shapes back (USD source): every
- [`apply_instances()`](mayatk/mayatk/env_utils/blender_bridge/templates/_bake_scene.py#L193) — Refuse a USD sidecar the child cannot replay (no mayatk): GUARANTEED-OR-FAIL.
- [`main()`](mayatk/mayatk/env_utils/blender_bridge/templates/_bake_scene.py#L222)
- [`SRC_FILE`](mayatk/mayatk/env_utils/blender_bridge/templates/_bake_scene.py#L46) — constant
- [`OUT_MA`](mayatk/mayatk/env_utils/blender_bridge/templates/_bake_scene.py#L47) — constant
- [`EXTRA_SYS_PATH`](mayatk/mayatk/env_utils/blender_bridge/templates/_bake_scene.py#L48) — constant
- [`BAKE_INFO`](mayatk/mayatk/env_utils/blender_bridge/templates/_bake_scene.py#L52) — constant
- [`MEDIA_HOME`](mayatk/mayatk/env_utils/blender_bridge/templates/_bake_scene.py#L54) — constant
- [`PROJECT`](mayatk/mayatk/env_utils/blender_bridge/templates/_bake_scene.py#L58) — constant
- [`USD_EXTENSIONS`](mayatk/mayatk/env_utils/blender_bridge/templates/_bake_scene.py#L60) — constant
- [`USD_IMPORT_OPTIONS`](mayatk/mayatk/env_utils/blender_bridge/templates/_bake_scene.py#L68) — constant

<a id="env_utils--blender_bridge--templates--_import_scene"></a>
### `env_utils/blender_bridge/templates/_import_scene.py`

Open a .blend / glTF headlessly (blender --background) and export it as FBX for a Maya import.

- [`open_source(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L333) — Load SRC_PATH into the conversion session.
- [`collect_texture_manifest(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L554) — Manifest entries for every textured material on an exportable object,
- [`sanitize_names(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L638) — Rename objects and materials to names Maya can hold;
- [`collect_instance_groups(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L719) — Blender linked duplicates -> ``[[sanitized prim names sharing one mesh], ...]``.
- [`collect_visibility(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L803) — ``{object name: [[frame, visibility], ...]}`` -- the manifest's ``visibility``
- [`collect_empties(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L881) — ``[{name, display_type}, ...]`` for the scene's Empties (node-type sidecar).
- [`scene_settings(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L904) — The scene's time setup -- the manifest's ``scene`` section, the one part
- [`write_texture_manifest(entries, scene_materials, empties, scene, path, scene_data=None, rig=None, visibility=None, instances=None)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L932) — Sidecar for what FBX cannot carry, consumed by BlenderSceneImport:
- [`stand_in_dropped_objects(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L987) — Give every object the FBX exporter would drop an Empty that ships instead.
- [`export_fbx(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L1037) — Full-fidelity FBX export with per-flag tolerance across Blender versions.
- [`scene_data_sections(bpy, spell)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L1084) — The scene's portable records as manifest sections (``shots``,
- [`main()`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L1251)
- [`SRC_PATH`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L43) — constant
- [`OUT_FBX`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L44) — constant
- [`EMBED_TEXTURES`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L45) — constant
- [`INCLUDE_ANIMATION`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L46) — constant
- [`TEX_DIR`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L47) — constant
- [`EXTRA_SYS_PATH`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L51) — constant
- [`RIG_MODE`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L54) — constant
- [`RIG_CAPABILITY`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L55) — constant
- [`FBX_DROPPED_TYPES`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene.py#L984) — constant

<a id="env_utils--blender_bridge--templates--_import_scene_usd"></a>
### `env_utils/blender_bridge/templates/_import_scene_usd.py`

Open a .blend headlessly (blender --background) and export it as USD for a Maya import.

- [`open_source(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L331) — Load SRC_PATH into the conversion session.
- [`export_usd(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L395) — Whole-scene USD export with per-kwarg tolerance across Blender versions.
- [`hidden_objects(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L516) — The viewport-hidden objects: ``hide_viewport`` (the monitor toggle) or
- [`sanitize_prim_name(name)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L531) — *name* as Blender's USD exporter spells the prim (``Chair.001`` ->
- [`export_prim_path(obj, root_prim_path='')`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L543) — The prim path the exporter writes for *obj* (one prim per object): its
- [`pin_primvar_indices(filepath)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L554) — Give every indexed primvar a DEFAULT index array, copied from its lone
- [`mark_skinning_methods(bpy, filepath, objects=None, root_prim_path='')`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L598) — Stamp each skinned mesh's ``skinningMethod`` from its Armature modifier's
- [`mark_container_skeletons(filepath)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L639) — Mark each Skeleton prim that is only a container for its bones with
- [`mark_invisible(filepath, objects, root_prim_path='')`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L677) — Stamp ``visibility = invisible`` on the prims *objects* exported to;
- [`collapse_static_xforms(filepath, tolerance=0.0001, distance=1e-05)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L715) — Hold every prim whose LOCAL transform never changes at one static value
- [`mark_orthographic(filepath, cameras, root_prim_path='')`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L768) — Author each of *cameras* (ORTHO camera objects, exported as perspective) as
- [`fold_single_mesh_xforms(filepath)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L809) — Fold every Xform whose only child is a Mesh into one Mesh prim carrying the
- [`collect_visibility(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L887) — ``{object name: [[frame, visibility], ...]}`` -- the manifest's ``visibility``
- [`hide_is_animated(obj)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L965) — True when *obj*'s show/hide is keyed.
- [`collect_empties(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L981) — ``[{name, display_type}, ...]`` for the scene's Empties (node-type sidecar).
- [`collect_texture_manifest(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L1206) — Manifest entries for every textured material on an exportable object,
- [`collect_instance_groups(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L1283) — Blender linked duplicates -> ``[[sanitized prim names sharing one mesh], ...]``.
- [`scene_settings(bpy)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L1340) — The scene's time setup -- the manifest's ``scene`` section, the one part
- [`write_manifest(bpy, scene, materials=None, scene_materials=None, scene_data=None, rig=None, visibility=None)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L1368) — Sidecar beside the USD carrying what the flat export cannot: instance
- [`scene_data_sections(bpy, spell)`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L1431) — The scene's portable records as manifest sections (``shots``,
- [`main()`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L1598)
- [`SRC_PATH`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L42) — constant
- [`OUT_USD`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L43) — constant
- [`INCLUDE_ANIMATION`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L44) — constant
- [`TEX_DIR`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L45) — constant
- [`EXTRA_SYS_PATH`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L49) — constant
- [`RIG_MODE`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L52) — constant
- [`RIG_CAPABILITY`](mayatk/mayatk/env_utils/blender_bridge/templates/_import_scene_usd.py#L53) — constant

<a id="env_utils--blender_bridge--templates--_save_scene"></a>
### `env_utils/blender_bridge/templates/_save_scene.py`

Import the bridged FBX into a headless Blender and save it as a ``.blend``.

- [`import_payload()`](mayatk/mayatk/env_utils/blender_bridge/templates/_save_scene.py#L85) — Import the payload (FBX or USD) and apply its manifest;
- [`pack_payload_images()`](mayatk/mayatk/env_utils/blender_bridge/templates/_save_scene.py#L117) — Pack every image still read from beside the temp payload into the .blend.
- [`main()`](mayatk/mayatk/env_utils/blender_bridge/templates/_save_scene.py#L138)
- [`BRIDGE_MODES`](mayatk/mayatk/env_utils/blender_bridge/templates/_save_scene.py#L41) — constant
- [`FBX_PATH`](mayatk/mayatk/env_utils/blender_bridge/templates/_save_scene.py#L53) — constant
- [`USD_EXTENSIONS`](mayatk/mayatk/env_utils/blender_bridge/templates/_save_scene.py#L54) — constant
- [`OUT_FILE`](mayatk/mayatk/env_utils/blender_bridge/templates/_save_scene.py#L55) — constant
- [`EXTRA_SYS_PATH`](mayatk/mayatk/env_utils/blender_bridge/templates/_save_scene.py#L58) — constant
- [`APPLY_UNIT_SCALE`](mayatk/mayatk/env_utils/blender_bridge/templates/_save_scene.py#L59) — constant
- [`INCLUDE_ANIMATION`](mayatk/mayatk/env_utils/blender_bridge/templates/_save_scene.py#L60) — constant
- [`SEND_FBX_OPTIONS`](mayatk/mayatk/env_utils/blender_bridge/templates/_save_scene.py#L73) — constant

<a id="env_utils--blender_bridge--templates--bake_lightmaps"></a>
### `env_utils/blender_bridge/templates/bake_lightmaps.py`

Bake the bridged Maya selection's lightmaps in a headless Blender;

- [`apply_texture_manifest(new_objects)`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L221) — Replay the sidecar through blendertk's applier.
- [`rebuild_scene_lights()`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L252) — Replay the manifest's light records through blendertk's applier.
- [`lightmap_records(meshes)`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L287) — ``(excluded meshes, claims)`` -- the Maya scene's lightmap records, as they arrived.
- [`hidden_in_maya(obj)`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L316) — Whether *obj* arrived hidden: its own Maya visibility flag or an ancestor's.
- [`emissive_material_count()`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L333) — How many materials emit light on their own -- what lights a fixture-lit room.
- [`light_scene()`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L367) — Apply the world and rebuild the scene's lights;
- [`check_bake_level(packed)`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L495) — Warn when the finished maps are blown out;
- [`make_baker()`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L540) — A LightmapBaker at the requested quality tier, with explicit overrides on top.
- [`capture_probe(baker, packed)`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L595) — The room this bake lit as a reflection probe, for the return manifest, or ``None``.
- [`bake_atlased(baker, meshes, out_dir, prefix, suffix, claims)`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L650) — ``(packed, cells)``: the atlas bake, and where in its atlas each object went.
- [`write_return_manifest(packed, lighting, probe=None, cells=None)`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L674) — Write the artifact Maya reads to reassemble this bake into its own scene.
- [`main()`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L770)
- [`BRIDGE_MODES`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L119) — constant
- [`BRIDGE_OUTPUT_EXT`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L125) — constant
- [`BRIDGE_OUTPUT`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L129) — constant
- [`BRIDGE_TIMEOUT`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L134) — constant
- [`FBX_PATH`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L150) — constant
- [`OUT_FILE`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L151) — constant
- [`EXTRA_SYS_PATH`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L154) — constant
- [`APPLY_UNIT_SCALE`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L155) — constant
- [`LIGHTMAP_QUALITY`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L160) — constant
- [`LIGHTMAP_RESOLUTION`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L161) — constant
- [`LIGHTMAP_SAMPLES`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L162) — constant
- [`LIGHTMAP_BOUNCES`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L163) — constant
- [`LIGHTMAP_DENOISE`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L166) — constant
- [`LIGHTMAP_ADAPTIVE`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L167) — constant
- [`LIGHTMAP_BESIDE_TEXTURES`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L168) — constant
- [`LIGHTMAP_REFLECTION_PROBE`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L172) — constant
- [`LIGHTMAP_DEVICE`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L173) — constant
- [`LIGHTMAP_PACKING`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L177) — constant
- [`LIGHTMAP_AFFIX`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L184) — constant
- [`LIGHTMAP_PREFIX`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L185) — constant
- [`LIGHTMAP_SUFFIX`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L186) — constant
- [`ENVIRONMENT_HDR`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L190) — constant
- [`WORLD_STRENGTH`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L191) — constant
- [`EMISSION_STRENGTH`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L192) — constant
- [`SCENE_LIGHT_STRENGTH`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L195) — constant
- [`INCLUDE_SCENE_LIGHTS`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L199) — constant
- [`INCLUDE_HIDDEN_LIGHTS`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L200) — constant
- [`LIGHTMAP_DIR`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L207) — constant
- [`RETURN_MANIFEST_VERSION`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L211) — constant
- [`PROBE_ANCHOR_OBJECTS`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L592) — constant
- [`PACKING_MODES`](mayatk/mayatk/env_utils/blender_bridge/templates/bake_lightmaps.py#L767) — constant

<a id="env_utils--blender_bridge--templates--import"></a>
### `env_utils/blender_bridge/templates/import.py`

Import the bridged payload (FBX or USD) into Blender, with optional clean-slate and

- [`import_payload()`](mayatk/mayatk/env_utils/blender_bridge/templates/import.py#L88) — Import the payload (FBX or USD) and apply its manifest;
- [`main()`](mayatk/mayatk/env_utils/blender_bridge/templates/import.py#L120)
- [`BRIDGE_MODES`](mayatk/mayatk/env_utils/blender_bridge/templates/import.py#L38) — constant
- [`FBX_PATH`](mayatk/mayatk/env_utils/blender_bridge/templates/import.py#L55) — constant
- [`USD_EXTENSIONS`](mayatk/mayatk/env_utils/blender_bridge/templates/import.py#L56) — constant
- [`EXTRA_SYS_PATH`](mayatk/mayatk/env_utils/blender_bridge/templates/import.py#L59) — constant
- [`APPLY_UNIT_SCALE`](mayatk/mayatk/env_utils/blender_bridge/templates/import.py#L60) — constant
- [`INCLUDE_ANIMATION`](mayatk/mayatk/env_utils/blender_bridge/templates/import.py#L61) — constant
- [`CLEAR_SCENE`](mayatk/mayatk/env_utils/blender_bridge/templates/import.py#L62) — constant
- [`FRAME_VIEW`](mayatk/mayatk/env_utils/blender_bridge/templates/import.py#L63) — constant
- [`SEND_FBX_OPTIONS`](mayatk/mayatk/env_utils/blender_bridge/templates/import.py#L76) — constant

<a id="env_utils--devtools"></a>
### `env_utils/devtools.py`

- **[`class DevTools(CoreUtils)`](mayatk/mayatk/env_utils/devtools.py#L25)** — Tools for inspecting Maya's environment and debugging.
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
- **[`class WidgetInspector(CoreUtils)`](mayatk/mayatk/env_utils/devtools.py#L479)** — Deep PyQt/PySide inspection tools for reverse-engineering Maya widgets.
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

- **[`class FbxUtils(pythontk.HelpMixin)`](mayatk/mayatk/env_utils/fbx_utils.py#L67)** — Low-level utilities for FBX import/export operations in Maya.
  - `FbxUtils.hook(cls, role: str) -> Optional[Callable]` *(class)* — The callable :attr:`HOOKS` names for *role*, else the one an installed
  - `FbxUtils.load_plugin()` *(static)* — Ensure the fbxmaya plugin is loaded.
  - `FbxUtils.embed_media_write_cwd()` *(static)* — Yield with the process CWD at the workspace root when the live FBX
  - `FbxUtils.reset_import()` *(static)* — Reset the FBX plugin's global IMPORT options to factory defaults.
  - `FbxUtils.reset_export()` *(static)* — Reset the FBX plugin's global EXPORT options to factory defaults.
  - `FbxUtils.set_fbx_options(options: Dict[str, Any])` *(static)* — Apply FBX export options via MEL commands.
  - `FbxUtils.load_preset(preset_path: str)` *(static)* — Load an FBX export preset file.
  - `FbxUtils.export(cls, file_path: str, objects: Optional[List] = None, preset_file: Optional[str] = None, options: Optional[Dict[str, Any]] = None, selection_only: bool = True, plan: Optional['FbxUtils.WritePlan'] = None) -> str` *(class)* — Export geometry to an FBX file.
  - `FbxUtils.drop_rig_apparatus(file_path: str, scope: Optional[Iterable[str]] = None, logger: Optional[logging.Logger] = None) -> Optional[Dict[str, Any]]` *(static)* — Remove the apparatus of the rigs a written FBX baked, in place.
  - `FbxUtils.embed_dependencies(file_path: str, search_dirs: Optional[Iterable[str]] = None, files: Optional[Iterable[Any]] = None, logger: Optional[logging.Logger] = None, walk_root: Optional[str] = None) -> Optional[Dict[str, Any]]` *(static)* — Embed every file a written FBX's scene records name, in place.
  - `FbxUtils.finish_deliverable(file_path: str, glb: Optional[str] = None, search_dirs: Optional[Iterable[str]] = None, logger: Optional[logging.Logger] = None, walk_root: Optional[str] = None, files: Optional[Iterable[Any]] = None) -> Optional[Dict[str, Any]]` *(static)* — The passes a written FBX deliverable takes, in place.
  - `FbxUtils.import_scene(cls, file_path: str, namespace: Optional[str] = None, options: Optional[Dict[str, Any]] = None, return_new_nodes: bool = True, *, stash: bool = True, file_options: Optional[Dict[str, Any]] = None, logger: Optional[logging.Logger] = None) -> List[str]` *(class)* — Import an FBX file -- ALL of its animation -- optionally into a namespace.
  - `FbxUtils.set_bake_range_from_scene() -> Tuple[float, float]` *(static)* — Point the bake range at the scene's ANIMATION range.
  - `FbxUtils.baking_enabled() -> bool` *(static)* — Whether the next write will BAKE complex animation.
  - `FbxUtils.export_flag(command: str) -> bool` *(static)* — An ``FBXExport*`` switch's current value, read the one safe way.
  - `FbxUtils.bake_range() -> Optional[Tuple[float, float]]` *(static)* — The ``(start, end)`` frames the next write will actually BAKE.
  - `FbxUtils.write_plan(bakes: Optional[bool] = None, reason: str = '', takes: Optional[Tuple[float, float]] = None, span: Optional[Tuple[float, float]] = None, resample_all: Optional[bool] = None) -> 'FbxUtils.WritePlan'` *(static)* — THE plan of one FBX write: whether it bakes, why, over what, resampled.
  - `FbxUtils.apply_write_plan(plan: 'FbxUtils.WritePlan') -> None` *(static)* — Put *plan* into the FBX settings: the bake flag FIRST, then its range.
  - `FbxUtils.planned_bake_range() -> Optional[Tuple[float, float]]` *(static)* — The range the write in progress bakes over: the open bracket's plan's
  - `FbxUtils.offsets_folded(cls, objects: Optional[Iterable[str]] = None, plan: Optional['FbxUtils.WritePlan'] = None, logger: Optional[logging.Logger] = None)` *(class)* — Around ONE FBX write: ship every node where Maya draws it.
  - `FbxUtils.animation_export_enabled() -> bool` *(static)* — Whether this export will carry animation at all.
  - `FbxUtils.set_animation_export(enabled: bool) -> None` *(static)* — Turn the Animation include group on or off (see :attr:`ANIMATION_INCLUDE_PROPERTY`).
  - `FbxUtils.reset_takes() -> None` *(static)* — Clear FBX take definitions and restore pre-takes bake-complex state.
  - `FbxUtils.apply_takes(takes: Iterable[Any], resample: bool = True) -> int` *(static)* — Configure FBX export to emit one AnimStack (Unity clip) per take.
  - `FbxUtils.takes_span(takes: Iterable[Any]) -> Optional[Tuple[int, int]]` *(static)* — The ``(start, end)`` union of *takes* -- the bake range
  - `FbxUtils.declared_takes() -> list` *(static)* — The takes the scene's records declare -- each clip's range in
  - `FbxUtils.apply_takes_from_node(node: Optional[str] = None, attr: Optional[str] = None, resample: bool = True) -> int` *(static)* — Realize the takes the scene declares into FBX export state.
  - `FbxUtils.producers(cls, only: Optional[Iterable[Any]] = None) -> Dict[Any, Callable]` *(class)* — :attr:`PRODUCERS` resolved to callables, unimportable ones skipped;
  - `FbxUtils.export_context(cls, mode: str = ptk.ExportContext.PIPELINE, **decisions) -> ptk.ExportContext` *(class)* — A context for this scene: provenance filled in, *decisions*
  - `FbxUtils.publish(cls, ctx: Optional[ptk.ExportContext] = None, only: Optional[Iterable[Any]] = None) -> ptk.ExportSnapshot` *(class)* — Assemble every producer's record and commit the carrier ONCE.
  - `FbxUtils.publish_authored(cls, records: Dict[Any, Any]) -> ptk.ExportSnapshot` *(class)* — Commit a tool's own records at AUTHORING time, with this scene's
  - `FbxUtils.stagers(cls, names: Optional[Iterable[str]] = None) -> Dict[str, Tuple[Optional[Callable], Optional[Callable]]]` *(class)* — The stager table for a bracket: the known stagers (*names* narrows
  - `FbxUtils.stage(cls, names: Optional[Iterable[str]] = None) -> Dict[str, Tuple[Optional[Callable], Optional[Callable]]]` *(class)* — Run every stager's ``prepare`` now and return the table that ran.
  - `FbxUtils.begin_export(cls, ctx: Optional[ptk.ExportContext] = None, only: Optional[Iterable[Any]] = None, stagers: Optional[Iterable[str]] = None, plan: Optional['FbxUtils.WritePlan'] = None) -> Optional[ptk.ExportSnapshot]` *(class)* — Open an export bracket (outermost only): stage the scene, then --
  - `FbxUtils.end_export(cls) -> None` *(class)* — Close an export bracket (outermost only): put back the scene's own
  - `FbxUtils.export_prepared(cls, ctx: Optional[ptk.ExportContext] = None, only: Optional[Iterable[Any]] = None, stagers: Optional[Iterable[str]] = None, plan: Optional['FbxUtils.WritePlan'] = None)` *(class)* — Stage the scene (and publish, given a *ctx* or *only* -- see
  - `FbxUtils.scratch_export()` *(static)* — Bracket for a THROWAWAY FBX write: the session hooks stand down.
  - `FbxUtils.enable_export_producer(cls, spec) -> None` *(class)* — Run *spec*'s producer before every FBX export this session
  - `FbxUtils.disable_export_producer(cls, spec) -> None` *(class)* — Opt *spec* out of the session hook;
  - `FbxUtils.register_export_stager(cls, name: str, prepare: Optional[Callable[[], Any]] = None, finish: Optional[Callable[[], Any]] = None) -> None` *(class)* — Run *prepare* before and *finish* after every FBX export this
  - `FbxUtils.unregister_export_stager(cls, name: str) -> None` *(class)*
  - `FbxUtils.enable_auto_takes() -> None` *(static)* — Realize declared takes on **every** FBX export -- shot-agnostic.
  - `FbxUtils.disable_auto_takes() -> None` *(static)* — Clear the explicit enable;
  - `FbxUtils.is_auto_takes_enabled() -> bool` *(static)* — Return whether the auto-takes export hook is currently registered.

<a id="env_utils--handoff_export"></a>
### `env_utils/handoff_export.py`

Maya-side selection + export hooks shared by the hand-off bridge engines.

- **[`class MayaExportMixin`](mayatk/mayatk/env_utils/handoff_export.py#L40)** — The Maya producer hooks for hand-off bridges (``_resolve_objects`` + ``_produce``).
  - `MayaExportMixin.lightmap_search_dirs(self) -> List[str]` — Where Maya's map files live now (:class:`pythontk.PreviewBridge` hook).
  - `MayaExportMixin.stepped_keys(nodes: Optional[List[str]] = None, spell: Optional[Callable] = None) -> Dict[str, Dict[str, List[float]]]` *(static)* — The hand-off manifest's ``stepped_keys`` (``ptk.HandoffManifest.
- **[`class BridgeHost`](mayatk/mayatk/env_utils/handoff_export.py#L862)** — What a bridge panel asks of Maya: a Scope's objects, what a send of them
  - `BridgeHost.resolve_scope(scope: str, bridge=None) -> List[str]` *(static)* — The objects a ``SCOPE`` word names (``uitk.bridge.Parameters.scope_spec``).
  - `BridgeHost.scope_closure(objects, params: Optional[Dict[str, Any]] = None) -> List[str]` *(static)* — What a send of the scoped *objects* ships: the objects as they are.
  - `BridgeHost.install_package(spec: str) -> None` *(static)* — Install the pip requirement *spec* where this session imports from.

<a id="env_utils--hierarchy_sync--_hierarchy_sync"></a>
### `env_utils/hierarchy_sync/_hierarchy_sync.py`

- [`MAYA_DEFAULT_CAMERAS`](mayatk/mayatk/env_utils/hierarchy_sync/_hierarchy_sync.py#L34) — constant
- **[`class HierarchyMapBuilder`](mayatk/mayatk/env_utils/hierarchy_sync/_hierarchy_sync.py#L52)** — Builds hierarchy path maps for Maya transforms.
  - `HierarchyMapBuilder.build_path_map(root, exclude_namespace_prefixes: List[str] = None, strip_namespaces: bool = False) -> Dict[str, Any]` *(static)* — Build a mapping of hierarchical paths to transform nodes.
  - `HierarchyMapBuilder.build_path_map_from_nodes(nodes: List[Any], strip_namespaces: bool = False) -> Dict[str, Any]` *(static)* — Build a path map from an arbitrary list of transform node names.
- **[`class MayaObjectMatcher(pythontk.LoggingMixin)`](mayatk/mayatk/env_utils/hierarchy_sync/_hierarchy_sync.py#L167)** — Maya-specific object matching with fuzzy logic and container searches.
  - `MayaObjectMatcher.find_matches(self, target_objects: List[str], imported_transforms: List, dry_run: bool = False) -> Tuple[List, Dict]` — Find matching objects using exact and fuzzy matching.
- **[`class HierarchySync(pythontk.LoggingMixin, _HierarchySyncInternal)`](mayatk/mayatk/env_utils/hierarchy_sync/_hierarchy_sync.py#L349)** — Core hierarchy analysis and repair manager.
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
- **[`class ObjectSwapper(pythontk.LoggingMixin)`](mayatk/mayatk/env_utils/hierarchy_sync/_hierarchy_sync.py#L2160)** — Handles cross-scene object operations like push/pull.
  - `ObjectSwapper.pull_objects_from_scene(self, target_objects: List[str], source_file: Union[str, Path], backup: bool = True) -> bool` — Pull objects from source scene into current scene.

<a id="env_utils--hierarchy_sync--hierarchy_baseline"></a>
### `env_utils/hierarchy_sync/hierarchy_baseline.py`

The scene's hierarchy baseline, stored in the scene.

- **[`class HierarchyBaseline(pythontk.HierarchyBaselineStore)`](mayatk/mayatk/env_utils/hierarchy_sync/hierarchy_baseline.py#L50)** — Read, compare and roll forward the scene's hierarchy baseline.

<a id="env_utils--hierarchy_sync--hierarchy_sync_session"></a>
### `env_utils/hierarchy_sync/hierarchy_sync_session.py`

Hierarchy Sync's host half: the reference a diff runs against, held in the

- **[`class HierarchySyncSession(pythontk.LoggingMixin)`](mayatk/mayatk/env_utils/hierarchy_sync/hierarchy_sync_session.py#L37)** — The reference held for a diff, the last analysis, and the scene calls
  - `HierarchySyncSession.loggers(cls) -> list` *(class)* — The loggers this session's work reports through: its own and its
  - `HierarchySyncSession.fuzzy_matching(self) -> bool` *(property)* — Whether the last analysis matched names fuzzily.
  - `HierarchySyncSession.is_loaded(self, reference_path: str) -> bool` — Whether *reference_path* is the reference this session holds.
  - `HierarchySyncSession.load_reference(self, reference_path: str) -> Optional[List[str]]` — Import *reference_path* into a temporary namespace, or reuse the
  - `HierarchySyncSession.release_reference(self) -> None` — Remove the held reference import (its namespace and nodes).
  - `HierarchySyncSession.analyze(self, reference_path: str, fuzzy_matching: bool = True, dry_run: bool = True, filter_meshes: bool = False, filter_cameras: bool = False, filter_lights: bool = False) -> bool` — Diff the whole scene against *reference_path* (loaded or reused).
  - `HierarchySyncSession.clear_analysis(self) -> None` — Drop the last analysis and release the reference.
  - `HierarchySyncSession.repair(self, differences: Optional[Dict[str, list]] = None, create_stubs: bool = True, quarantine_extras: bool = True, quarantine_group: str = '_QUARANTINE', skip_animated: bool = True, fix_reparented: bool = True, fix_fuzzy_renames: bool = True, dry_run: bool = True) -> bool` — Repair the scene toward the reference, from the last analysis.
  - `HierarchySyncSession.pull(self, targets: Sequence[str], reference_path: str, fuzzy_matching: bool = True, dry_run: bool = True, pull_children: bool = False, pull_mode: str = 'Add to Scene') -> bool` — Bring reference nodes into the current scene.
  - `HierarchySyncSession.current_scene_nodes(self) -> List[str]` — The current scene's transforms the panel's tree shows: no default
  - `HierarchySyncSession.hierarchy_structure(nodes) -> Tuple[Dict[str, dict], List[str]]` *(static)* — The tree a panel draws from *nodes*.
  - `HierarchySyncSession.clean_path(path: str) -> str` *(static)* — *path* as the diff compares it: each component's namespace dropped.
  - `HierarchySyncSession.node_path(node) -> str` *(static)* — The name a pull or a selection takes *node* by: its DAG path.
  - `HierarchySyncSession.node_name(node) -> str` *(static)* — *node*'s name: its DAG path's leaf, namespace dropped.
  - `HierarchySyncSession.node_alive(node) -> bool` *(static)* — Whether *node* (a tree item's node) names a node in the scene.
  - `HierarchySyncSession.rename_nodes(self, pairs: Sequence[Tuple[Any, str]]) -> List[Optional[str]]` — Rename each ``(node, new_name)`` pair, as one undo chunk.
  - `HierarchySyncSession.reparent_nodes(self, moves: Sequence[Tuple[Any, Any]]) -> int` — Reparent each ``(node, parent)`` move (``parent=None``: to the
  - `HierarchySyncSession.delete_nodes(self, nodes: Sequence[Any]) -> List[str]` — Delete *nodes* (and so their subtrees), as one undo chunk.
  - `HierarchySyncSession.select_nodes(nodes: Sequence[Any]) -> int` *(static)* — Select *nodes* in the scene (an ambiguous name is skipped).
  - `HierarchySyncSession.scene_path() -> str` *(static)* — The open scene's file, or ``""`` (unsaved, or no Maya).
  - `HierarchySyncSession.workspace() -> str` *(static)* — The current project's root, or ``""``.
  - `HierarchySyncSession.recent_scenes() -> List[str]` *(static)* — The recently opened scenes, most recent first.
  - `HierarchySyncSession.open_scene(path: str) -> None` *(static)* — Open *path* as the current scene (unsaved changes discarded, as
  - `HierarchySyncSession.reference_formats() -> List[str]` *(static)* — The extensions a reference may have (what the sandbox imports).

<a id="env_utils--hierarchy_sync--scene_data_sidecar"></a>
### `env_utils/hierarchy_sync/scene_data_sidecar.py`

Scene-data sidecar -- Maya's scene hooks over ``ptk.SceneDataSidecarBase``.

- **[`class SceneDataSidecar(pythontk.SceneDataSidecarBase)`](mayatk/mayatk/env_utils/hierarchy_sync/scene_data_sidecar.py#L24)** — The scene-data sidecar stored alongside Maya exports.
  - `SceneDataSidecar.build_clean_path_set(cls, objects) -> set` *(class)* — Build a set of namespace-stripped hierarchy paths from DAG long paths.
  - `SceneDataSidecar.expand_to_descendants(objects) -> list` *(static)* — Return *objects* plus all their DAG descendants (full paths).
  - `SceneDataSidecar.drop_intermediate(nodes) -> list` *(static)* — *nodes* minus intermediate shapes (``…ShapeOrig`` and kin).

<a id="env_utils--material_records"></a>
### `env_utils/material_records.py`

The ``materials`` record's Maya producer.

- **[`class MaterialRecords(pythontk.HelpMixin)`](mayatk/mayatk/env_utils/material_records.py#L38)** — Maya's materials, stated for the ``materials`` record.
  - `MaterialRecords.blinn_shininess(eccentricity: float) -> float` *(static)* — The Phong shininess Maya's FBX exporter writes for a Blinn of
  - `MaterialRecords.export_record(cls, ctx: 'ptk.ExportContext') -> Optional['ptk.Record']` *(class)* — The ``materials`` record for this write, or ``None`` when no mesh
  - `MaterialRecords.entries(cls, objects: Iterable[str], notes: Optional[List[str]] = None) -> List[Dict[str, Any]]` *(class)* — The record entry of every material *objects* wear, in scene order.

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
  - `MayaConnection.close_launched(self, force: bool = False) -> bool` — Close the Maya THIS connection launched -- only it, and only while
  - `MayaConnection.close_instance(port: Optional[int] = None, pid: Optional[int] = None, force: bool = False) -> bool` *(static)* — Close a Maya instance identified by Port or PID.
  - `MayaConnection.get_available_port(cls, start_port: int = 7002, max_check: int = 100) -> int` *(class)* — Find an available port starting from start_port.
  - `MayaConnection.ensure_connection(self, launch: bool = True, app_path: Optional[str] = None, launch_args: Optional[List[str]] = None) -> bool` — Verify the port is reachable;
  - `MayaConnection.execute(self, code: str, timeout: int = 30, capture_output: bool = False, wait_for_response: bool = False, output_callback: Optional[Callable[[str], None]] = None) -> Optional[str]` — Execute Python code in Maya.
  - `MayaConnection.get_script_editor_output(self, last_n_chars: Optional[int] = None) -> Optional[str]` — Get the full content of the Maya Script Editor history.
  - `MayaConnection.execute_and_capture_editor_output(self, code: str, timeout: int = 30, mirror_to_script_output: bool = False) -> tuple[Optional[str], Optional[str]]` — Execute code and capture the Script Editor output generated by the execution.
  - `MayaConnection.clear_script_editor(self) -> bool` — Clear the Maya Script Editor history.
  - `MayaConnection.shutdown(self, force: bool = False) -> None` — Shut down the connected Maya session and reset state.

<a id="env_utils--namespace_sandbox"></a>
### `env_utils/namespace_sandbox.py`

- **[`class FBXImporter`](mayatk/mayatk/env_utils/namespace_sandbox.py#L14)** — Handles FBX-specific import operations (.fbx files).
  - `FBXImporter.is_supported_file(self, file_path: Union[str, Path]) -> bool` — Check if the file is an FBX file.
  - `FBXImporter.import_with_namespace(self, source_file: Path, namespace: str, temp_namespace_prefix: str, force_complete_import: bool = False) -> Optional[Dict]` — Import an FBX file isolated into *namespace*.
  - `FBXImporter.import_for_analysis(self, source_file: Path, namespace: str) -> Optional[List[Any]]` — Import FBX file into a fresh namespace for analysis.
- **[`class MayaImporter`](mayatk/mayatk/env_utils/namespace_sandbox.py#L117)** — Handles Maya-specific import operations (.ma/.mb files).
  - `MayaImporter.is_supported_file(self, file_path: Union[str, Path]) -> bool` — Check if the file is a Maya file (.ma or .mb).
  - `MayaImporter.import_with_namespace(self, source_file: Path, namespace: str, temp_namespace_prefix: str, force_complete_import: bool = False) -> Optional[Dict]` — Import Maya file with namespace - original logic.
  - `MayaImporter.import_for_analysis(self, source_file: Path, namespace: str) -> Optional[List[Any]]` — Import Maya file for analysis purposes.
- **[`class CameraTracker(pythontk.LoggingMixin)`](mayatk/mayatk/env_utils/namespace_sandbox.py#L300)** — Tracks cameras before and after import operations for proper cleanup.
  - `CameraTracker.capture_pre_import_state(self)` — Capture camera state before import.
  - `CameraTracker.capture_post_import_state(self)` — Capture camera state after import.
  - `CameraTracker.get_imported_cameras(self, namespace_filter=None)` — Get cameras that were imported (optionally filtered by namespace).
  - `CameraTracker.cleanup_imported_cameras(self, namespace_filter=None, preserve_user_cameras=True)` — Clean up imported cameras with optional preservation of user cameras.
  - `CameraTracker.reset(self)` — Reset tracking state.
- **[`class NamespaceSandbox(pythontk.LoggingMixin, _NamespaceSandboxInternal)`](mayatk/mayatk/env_utils/namespace_sandbox.py#L462)** — Handles temporary importing and namespace management for Maya scenes.
  - `NamespaceSandbox.import_with_namespace(self, source_file: Union[str, Path], namespace_prefix: str = None, force_complete_import: bool = False) -> Optional[Dict]` — Import file and return import information.
  - `NamespaceSandbox.import_for_analysis(self, source_file: Union[str, Path], namespace: str = None) -> Optional[List[Any]]` — Import file into temporary namespace for analysis (dry-run mode).
  - `NamespaceSandbox.get_supported_formats(cls) -> List[str]` *(class)* — Extensions (``.ma``, ``.mb``, ``.fbx``) the importers accept.
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

<a id="env_utils--pm_doctor"></a>
### `env_utils/pm_doctor.py`

Shadow doctor for embedded-DCC installs (companion of package-manager.bat).

- [`find_shadows()`](mayatk/mayatk/env_utils/pm_doctor.py#L31) — ``[(name, user_site_version, bundled_version)]`` for shadowed dists.
- [`main()`](mayatk/mayatk/env_utils/pm_doctor.py#L56)

<a id="env_utils--reference_manager--_reference_manager"></a>
### `env_utils/reference_manager/_reference_manager.py`

Reference Manager engine — Maya file references, assemblies and workspaces.

- **[`class AssemblyManager`](mayatk/mayatk/env_utils/reference_manager/_reference_manager.py#L105)**
  - `AssemblyManager.current_references(cls)` *(class)* — Get the current scene references.
  - `AssemblyManager.create_assembly_definition(cls, namespace: str, file_path: str) -> str` *(class)* — Create an assembly definition for the given file path.
  - `AssemblyManager.set_active_representation(cls, assembly_node: str, representation_name: str) -> bool` *(class)* — Set the active representation for an assembly.
  - `AssemblyManager.convert_references_to_assemblies(cls)` *(class)* — Convert all current references to assembly definitions and references.
- **[`class ReferenceManager(WorkspaceManager, pythontk.HelpMixin, pythontk.LoggingMixin, _ReferenceManagerInternal)`](mayatk/mayatk/env_utils/reference_manager/_reference_manager.py#L393)** — Maya's Reference Manager engine: the workspace's scene files, the scene's
  - `ReferenceManager.current_references(self)` *(property)* — Get the current scene references.
  - `ReferenceManager.sanitize_namespace(namespace: str) -> str` *(static)* — Sanitize the namespace by replacing or removing illegal characters.
  - `ReferenceManager.add_reference(self, namespace: str, file_path: str) -> bool` — Reference *file_path* under *namespace* (sanitized;
  - `ReferenceManager.follow_bake_sources(self) -> list` — Keep every bake-backed reference in its project, on its source as the
  - `ReferenceManager.purge_unused_bakes(self, dry_run: bool = False) -> list` — Remove the bakes in the open scene's project that no scene references
  - `ReferenceManager.import_references(self, namespaces=None, namespace_mode='remove', scene_data='merge')` — Import referenced objects into the scene, making their data local.
  - `ReferenceManager.update_references(self)` — Update all references to reflect the latest changes from the original files
  - `ReferenceManager.get_reference_top_transforms(self, ref)` — Return the reference's top-level transforms — those whose parent is outside it.
  - `ReferenceManager.get_reference_display_mode(self, ref) -> str` — Return the active display mode for the reference's top-level transforms.
  - `ReferenceManager.set_reference_display_mode(self, ref, mode: str) -> bool` — Set the display override mode on the reference's top-level transforms.
  - `ReferenceManager.remove_references(self, namespaces=None)` — Remove references based on their namespaces.
  - `ReferenceManager.is_foreign(cls, path) -> bool` *(class)* — True if *path* is a foreign (Blender) scene -- a cross-DCC row baked through
  - `ReferenceManager.needs_conversion(cls, path) -> bool` *(class)* — True if bringing *path* in runs a conversion in the other DCC (a headless
  - `ReferenceManager.scene_has_complex_animation(path: str) -> bool` *(static)* — True if the foreign scene *path* carries rig logic (constraints, IK,
  - `ReferenceManager.find_foreign_scenes(root_dir: str, recursive: bool = False, extensions=None) -> List[str]` *(static)* — The foreign scenes under *root_dir* (``BlenderSceneImport.find_scenes``).
  - `ReferenceManager.bake_scene(self, path: str, **kwargs) -> str` — Bake the foreign scene (or an FBX) *path* to a cached ``.ma`` and return its
  - `ReferenceManager.import_scene(self, path: str, **kwargs) -> list` — Import *path* as local data with no reference behind it
  - `ReferenceManager.bake_project_root(self) -> Optional[str]` — The project a reference's bake goes in: the open scene's
  - `ReferenceManager.foreign_scratch_path(path: str) -> str` *(static)* — The deterministic scratch ``.ma`` a foreign row opens as
  - `ReferenceManager.discard_stale_scratches(self)` — Discard every untouched scratch twin that is no longer the open scene (one
  - `ReferenceManager.current_scene(self) -> str` — The open scene's path;
  - `ReferenceManager.scene_modified(self) -> bool` — True if the open scene has unsaved changes (``file -q -modified``).
  - `ReferenceManager.save_open_scene(self) -> Optional[bool]` — Flush the open scene to its own file so its unsaved edits reach disk.
  - `ReferenceManager.save_scene(self, path: str) -> str` — Save the open scene as *path* in Maya's own format, by its extension (an
  - `ReferenceManager.new_scene(self) -> bool` — Discard the current file and start an empty scene (Maya's ``file -new``).
  - `ReferenceManager.is_current(self, path, current=None) -> bool` — True if *path*'s scene is the one open: the file itself, or -- a foreign row
  - `ReferenceManager.open_scene(self, file_path: str, set_workspace: bool = True) -> bool` — Open a scene file, optionally setting the workspace to match the file.
  - `ReferenceManager.rename_scene_file(self, old_path: str, new_path: str, folder=None)` — Rename a scene file on disk, carrying everything keyed to its old name: the
  - `ReferenceManager.remove_scene_file(self, path: str, permanent: bool = False, use_folder: bool = False) -> Optional[bool]` — Remove one scene with its metadata sidecar -- to the trash, or *permanent*
  - `ReferenceManager.bake_source(referenced_path) -> Optional[str]` *(static)* — The scene *referenced_path* was baked from, or None.
  - `ReferenceManager.row_key(cls, referenced_path) -> str` *(class)* — Normalized path of the row a reference to *referenced_path* stands

<a id="env_utils--scene_exporter--_scene_exporter"></a>
### `env_utils/scene_exporter/_scene_exporter.py`

- **[`class SceneExporter(pythontk.SceneExporterBase)`](mayatk/mayatk/env_utils/scene_exporter/_scene_exporter.py#L26)** — Maya's Scene Exporter: the export write, its scope and its FBX /
  - `SceneExporter.presets(self) -> Dict[str, Optional[str]]` *(property)* — Return available presets ({name: filepath}, plus a leading "None" entry).
  - `SceneExporter.refresh_fbx_presets(self) -> Dict[str, Optional[str]]` — Re-scan the FBX preset directory now and return :attr:`presets` -- the
  - `SceneExporter.fbx_preset_dir(self) -> Optional[str]` — Maya's own FBX preset folder (``FBX/Presets`` under the user app
  - `SceneExporter.fbx_preset_metadata(self, preset: Optional[str]) -> Dict[str, str]` — *preset* -- a value of :attr:`presets`, its file path -- as metadata an
  - `SceneExporter.restore_fbx_preset(self, metadata: Optional[Dict[str, str]]) -> Optional[str]` — Write the FBX preset an export preset embedded (:meth:`fbx_preset_metadata`)
  - `SceneExporter.edit_fbx_preset(self, preset: Optional[str] = None) -> None` — Open the FBX plug-in's preset editor on *preset* -- a value of
  - `SceneExporter.objects_to_export(export_mode: str = 'visible') -> List[str]` *(static)* — The objects an export scope names -- what the panel's Scope row hands
  - `SceneExporter.perform_export(self, export_dir: str, objects: Optional[Union[List[str], Callable]] = None, preset_file: Optional[str] = None, output_name: Optional[str] = None, export_visible: bool = True, file_format: Optional[str] = 'FBX export', create_log_file: bool = False, *, log_level: Optional[str] = None, log_handler: Optional[object] = None, tasks: Optional[Dict[str, Any]] = None, usd_options: Optional[Dict[str, Any]] = None, progress_callback: Optional[Callable[[int, int, Optional[str]], Any]] = None) -> bool` — Perform the export operation, including initialization and task management.
  - `SceneExporter.name_context(self) -> Dict[str, str]` — Live value for every token in :attr:`NAME_TOKENS` but the counter.
  - `SceneExporter.resolve_export_path(self, pattern: Optional[str] = None, export_dir: Optional[str] = None, output_format: str = 'fbx', *, report: bool = True) -> Dict[str, Any]` — Resolve the Output Filename field into the file(s) an export writes.
  - `SceneExporter.generate_export_path(self, *, extension: str = '.fbx', output_format: Optional[str] = None) -> str` — The full export path, from the fields :meth:`perform_export` stamps.
  - `SceneExporter.load_fbx_export_preset(self, preset_file: str = None, verify: bool = False) -> Optional[dict]` — Load an FBX export preset and optionally verify it.
  - `SceneExporter.verify_fbx_preset(self) -> dict` — Verify a set of predefined FBX export settings and log their values.

<a id="env_utils--scene_exporter--task_manager"></a>
### `env_utils/scene_exporter/task_manager.py`

The Scene Exporter's task/check manager -- what ``perform_export`` drives.

- **[`class TaskManager(TaskFactory, _SceneTasksMixin, _TextureTasksMixin, _AnimationTasksMixin, _TaskChecksMixin, _TaskDefinitionsMixin)`](mayatk/mayatk/env_utils/scene_exporter/task_manager.py#L33)** — The export pipeline's tasks and checks, run in the shared order.
  - `TaskManager.run_tasks(self, tasks: Dict[str, Any]) -> bool` — Run *tasks*, first adopting the two modes derived from them.
  - `TaskManager.objects(self)` *(property)*
  - `TaskManager.create_glb(self, fbx_path: Optional[str] = None, announce: bool = True)` — Convert an exported FBX to a GLB through the shared build.
  - `TaskManager.write_scene_data_sidecar(self, glb_path: Optional[str] = None) -> None` — Write the sidecar JSON recording what shipped in the export.
  - `TaskManager.verify_deliverables(self, *paths: str, max_fbx_bytes: Optional[int] = None, max_image_bytes: Optional[int] = None) -> Optional[Any]` — Read the shipped files back and run pythontk's file-level gates.
  - `TaskManager.set_workspace(self, enable=True)` — Switch to the workspace matching the scene path, and align the
  - `TaskManager.set_linear_unit(self, linear_unit)` — Set Maya's working linear unit for the export.
  - `TaskManager.conform_shape_names(self)` — Repair scratch/mangled names in the export set, then conform shapes.
  - `TaskManager.flatten_sheared_chains(self, tolerance: float = 0.05) -> tuple` — Flatten transforms whose parent-relative matrices shear -- the
  - `TaskManager.ignore_groups(self, names: str, case_sensitive: bool = False) -> None` — Exclude top-level groups matching *names* and all their descendants
  - `TaskManager.exclude_hdr(self) -> None` — Remove Arnold HDR environment lights (``aiSkyDomeLight``) from the export set.
  - `TaskManager.export_path(self) -> str` *(property)* — The deliverable this run writes (``run.export_path``).
  - `TaskManager.begin_run(self, run: ptk.ExportRun) -> None` — Adopt *run*'s modes and reset every per-run marker -- the ONE reset.
  - `TaskManager.convert_to_relative_paths(self)` — Convert texture paths under ``sourceimages`` to project-relative form.
  - `TaskManager.optimize_textures(self, template)` — Optimize the maps shipping with this export, by map type.
  - `TaskManager.reassign_duplicate_materials(self)` — Reassign duplicate materials in the scene.
  - `TaskManager.resolve_invalid_texture_paths(self)` — Attempt to resolve missing texture paths via a gated sourceimages hunt.
  - `TaskManager.convert_textures(self, template) -> None` — Convert the export materials' textures to *template* via the Map Updater.
  - `TaskManager.smart_bake(self)` — Pre-bake constrained and driven channels before export.
  - `TaskManager.capture_write_baseline(self) -> None` — Read the FBX settings once, as the preset (or the default reset) left
  - `TaskManager.write_plan(self)` — THIS run's FBX write: whether it bakes, why, over what, resampled
  - `TaskManager.optimize_keys(self, level: Union[bool, str, None] = True)` — Optimize baked animation keys at the requested level.
  - `TaskManager.set_bake_animation_range(self, mode: Union[bool, str, None] = 'auto')` — Set the FBX bake range from the selected source, if baking is on.
  - `TaskManager.tie_all_keyframes(self)` — Use AnimUtils to tie all keyframes for the specified objects.
  - `TaskManager.snap_keys_to_frame(self)` — Snap all keyframes to the nearest whole frame.
  - `TaskManager.export_data_node(self)` — Publish the scene records and ship the carrier (default on).
  - `TaskManager.ensure_scene_records_published(self)` — Publish once if no task did: the bracket's fallback for a run with
  - `TaskManager.apply_declared_takes(self, mode: Union[bool, str, None] = 'both')` — Ship the declared shots, the whole sequence, or both.
  - `TaskManager.ship_declared_takes(self, fbx_path: str) -> Optional[dict]` — Give the written FBX the clips its Animation Clips mode names.
  - `TaskManager.check_geometry_lod_suffix(self) -> tuple` — Check for geometry whose names end with '_LOD' or '_LOD' followed by digits.
  - `TaskManager.check_root_default_transforms(self) -> tuple` — Check if all root group nodes have default transforms.
  - `TaskManager.check_material_compatibility(self, template) -> tuple` — Every mask map matches the chosen texture template (post-conversion).
  - `TaskManager.check_texture_optimization(self, template) -> tuple` — Every shipping texture is optimized for its map type (post-task).
  - `TaskManager.check_path_length(self, max_length: Optional[int] = None) -> tuple` — Check that no export path exceeds the OS path-length limit.
  - `TaskManager.check_output_writable(self) -> tuple` — Check that every file this run will write can actually be replaced.
  - `TaskManager.check_valid_paths(self) -> tuple` — Check that every export texture and scene reference resolves on disk
  - `TaskManager.check_texture_file_size(self, max_size_mb: Optional[float] = 16.0) -> tuple` — Check that no export texture exceeds a maximum on-disk file size.
  - `TaskManager.check_mangled_names(self) -> tuple` — Check the export set (including shapes) for scratch/mangled names.
  - `TaskManager.check_duplicate_names(self, scope: Optional[str] = None) -> tuple` — Check for duplicate short names within the export set.
  - `TaskManager.check_duplicate_materials(self) -> tuple` — Check if any duplicate materials are present in the scene.
  - `TaskManager.check_default_materials(self) -> tuple` — Geometry shipping on Maya's fallback shader instead of an authored one.
  - `TaskManager.check_referenced_objects(self) -> tuple` — Check if any referenced objects are present in the scene.
  - `TaskManager.check_framerate(self, target_framerate: Optional[str]) -> tuple` — Check if the scene's current framerate matches the target framerate.
  - `TaskManager.check_objects_below_floor(self, tolerance: float = _DEFAULT_FLOOR_TOLERANCE) -> tuple` — Fail when surface geometry reaches deeper than *tolerance* below Y=0.
  - `TaskManager.check_overlapping_duplicate_mesh(self) -> tuple` — Check for duplicate overlapping geometry among the export objects.
  - `TaskManager.check_hidden_geometry(self) -> tuple` — Check for geometry that will ship in the FBX while hidden.
  - `TaskManager.check_uv_snapshots(self) -> tuple` **DEPRECATED (remove in 0.24.0)** — Retired export check: report ``_uv_snap_*`` backup sets on the meshes.
  - `TaskManager.check_untied_keyframes(self) -> tuple` — Check if there are any untied keyframes on the specified objects.
  - `TaskManager.check_sheared_local_transforms(self, tolerance: float = 0.05) -> tuple` — Fail when a node's LOCAL matrix is sheared past *tolerance*.
  - `TaskManager.check_floating_point_keys(self) -> tuple` — Check if there are any floating point keyframes on the specified objects.
  - `TaskManager.check_hierarchy_vs_existing_fbx(self) -> tuple` — Check export objects against the hierarchy manifest of the previous export.
  - `TaskManager.task_definitions(self) -> Dict[str, Dict[str, Any]]` *(property)* — Return the task definitions for the UI.
  - `TaskManager.check_definitions(self) -> Dict[str, Dict[str, Any]]` *(property)* — Return the check definitions for the UI.
  - `TaskManager.definitions(self) -> Dict[str, Dict[str, Any]]` *(property)* — Return all definitions combined for backward compatibility.

<a id="env_utils--scene_state"></a>
### `env_utils/scene_state.py`

Read named sections of live-scene state for transport.

- **[`class SceneState`](mayatk/mayatk/env_utils/scene_state.py#L47)** — Section-registry reader of scene state the FBX cannot express --
  - `SceneState.source() -> Dict[str, str]` *(static)* **DEPRECATED (remove in 0.29.0)** — This host's identity for the envelope's ``source`` key.
  - `SceneState.read(cls, objects: List[str], include_textures: bool = True, sections: Optional[List[str]] = None) -> Dict[str, Any]` *(class)* **DEPRECATED (remove in 0.29.0)** — Scene state the FBX cannot express, one key per requested section.
  - `SceneState.emission_weight(cls, mat: str) -> float` *(class)* — The shader's separate emission scalar, or 1.0 when it has none.

<a id="env_utils--script_output"></a>
### `env_utils/script_output.py`

- **[`class ScriptConsole(MayaQWidgetDockableMixin, QtWidgets.QDialog)`](mayatk/mayatk/env_utils/script_output.py#L14)** — Dockable window that live-mirrors Maya's Script Editor output,
  - `ScriptConsole.toggle(cls, *args, **kwargs)` *(class)* — Toggle the Script Output panel.
  - `ScriptConsole.show_console(cls, dock=None, width: int = None, height: int = None, tab_position: str = None, restore: bool = False)` *(class)* — Show the Script Output console.

<a id="env_utils--unity_bridge--_unity_bridge"></a>
### `env_utils/unity_bridge/_unity_bridge.py`

Unity bridge engine -- export the Maya selection into a Unity project's Assets/.

- **[`class UnityBridge(MayaExportMixin, pythontk.HandoffBridge)`](mayatk/mayatk/env_utils/unity_bridge/_unity_bridge.py#L37)** — Export the Maya selection and copy it into a Unity project's ``Assets/``.
  - `UnityBridge.list_template_modes(self)`
  - `UnityBridge.params_registry(cls)` *(class)* — The panel's parameter registry (``parameters.Parameters``, a
  - `UnityBridge.params_defaults(self)`
  - `UnityBridge.list_delivery_modes(cls) -> List[Tuple[str, str]]` *(class)* — ``[(mode_stem, ""), ...]`` for the panel's delivery combo.

<a id="env_utils--unity_bridge--parameters"></a>
### `env_utils/unity_bridge/parameters.py`

User-tunable parameters for the Maya->Unity bridge panel.

- [`PARAMS`](mayatk/mayatk/env_utils/unity_bridge/parameters.py#L24) — constant
- **[`class Parameters(ParamRegistry)`](mayatk/mayatk/env_utils/unity_bridge/parameters.py#L165)** — Parameters — module namespace.

<a id="env_utils--usd"></a>
### `env_utils/usd.py`

USD import / export over Maya's native ``mayaUsd`` runtime.

- **[`class UsdReadRefused(RuntimeError)`](mayatk/mayatk/env_utils/usd.py#L41)** — A stage refused for a LIVE read -- a reference or an open -- because its
- **[`class UsdUtils(pythontk.HelpMixin)`](mayatk/mayatk/env_utils/usd.py#L56)** — Low-level USD import/export utilities over the ``mayaUsd`` plugin.
  - `UsdUtils.load_plugin()` *(static)* — Ensure the ``mayaUsdPlugin`` plugin is loaded.
  - `UsdUtils.is_usd_file(file_path: str) -> bool` *(static)* — True when *file_path* is a USD layer/package (delegates to pythontk).
  - `UsdUtils.sanitize_prim_name(name: str) -> str` *(static)* — *name* as ``mayaUSDExport`` spells the prim (probe-verified: ``ref:nsCube``
  - `UsdUtils.export(cls, file_path: str, objects: Optional[List] = None, options: Optional[Dict[str, Any]] = None, selection_only: bool = True, material_names: str = 'shader', prune_static: bool = False) -> str` *(class)* — Export to a USD file (``.usd``/``.usda``/``.usdc``/``.usdz``).
  - `UsdUtils.name_materials_after_shaders(cls, file_path: str, mapping: Optional[Dict[str, str]] = None) -> int` *(class)* — Rename the layer's ``Material`` prims from their SHADING GROUP to their
  - `UsdUtils.sampling_frame_range(cls, objects: Optional[List[str]] = None) -> Optional[Tuple[float, float]]` *(class)* — The frames a USD export is worth sampling, or ``None`` for a static one.
  - `UsdUtils.options_string(options: Dict[str, Any]) -> str` *(static)* — *options* as a ``cmds.file`` translator options string: ``key=value``
  - `UsdUtils.file_options(cls, options: Optional[Dict[str, Any]] = None, read_animation: bool = True) -> Dict[str, str]` *(class)* — The ``cmds.file`` keywords that read a USD layer the interchange way --
  - `UsdUtils.skinning_methods(usd_path: str) -> Dict[str, str]` *(static)* — ``{prim path: skinning method}`` for every prim of the stage that authors
  - `UsdUtils.crashing_skins(cls, usd_path: str) -> List[str]` *(class)* — Prim paths of the stage whose skin mayaUsd 0.30's reader crashes on
  - `UsdUtils.live_read_options(cls, file_path: str, options: Optional[Dict[str, Any]] = None, read_animation: bool = True) -> Dict[str, str]` *(class)* — :meth:`file_options` for reading *file_path* LIVE -- a reference or an
  - `UsdUtils.dq_safe_source(cls, usd_path: str) -> Tuple[str, Dict[str, str]]` *(class)* — ``(path safe to hand mayaUsd, {prim path: real skinning method})``.
  - `UsdUtils.apply_skinning_methods(nodes: List[str], methods: Dict[str, str]) -> int` *(static)* — Set each imported skinCluster's ``skinningMethod`` from *methods*
  - `UsdUtils.stage_conform(cls, usd_path: str) -> Optional[Tuple[float, float]]` *(class)* — ``(scale, rotate_x)`` that brings *usd_path*'s stage into this scene's
  - `UsdUtils.conform_roots(roots: List[str], conform: Optional[Tuple[float, float]], name: str) -> Optional[str]` *(static)* — Parent *roots* under a new world-level group carrying *conform*
  - `UsdUtils.top_transforms(nodes: List[str]) -> List[str]` *(static)* — The world-level transforms among *nodes* (long names).
  - `UsdUtils.import_scene(cls, file_path: str, namespace: Optional[str] = None, options: Optional[Dict[str, Any]] = None, return_new_nodes: bool = True, read_animation: bool = True, conform: bool = False) -> List[str]` *(class)* — Import a USD file, optionally isolated into a namespace.

<a id="env_utils--webxr_preview"></a>
### `env_utils/webxr_preview.py`

Push the Maya selection to a live browser / WebXR preview.

- **[`class WebXrPreview(MayaExportMixin, pythontk.PreviewBridge)`](mayatk/mayatk/env_utils/webxr_preview.py#L48)** — Live browser / WebXR preview of the Maya selection.

<a id="env_utils--workspace_manager"></a>
### `env_utils/workspace_manager.py`

- **[`class WorkspaceManager(pythontk.HelpMixin, pythontk.LoggingMixin)`](mayatk/mayatk/env_utils/workspace_manager.py#L16)** — Shared workspace management utilities for UI components.
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

- **[`class WorkspaceMap(WorkspaceManager, pythontk.HelpMixin, pythontk.LoggingMixin)`](mayatk/mayatk/env_utils/workspace_map.py#L13)** — Maps Maya workspaces for a tree view (the Workspace Map panel: tentacle's
  - `WorkspaceMap.current_working_dir(self)` *(property)* — Get the current working directory for workspace discovery.
  - `WorkspaceMap.recursive_search(self)` *(property)* — Whether to search recursively for workspaces.
  - `WorkspaceMap.workspace_data(self) -> Dict[str, Dict]` *(property)* — Get cached workspace data, rebuilding if needed.
  - `WorkspaceMap.invalidate_workspace_data(self)` — Scan for workspaces and build data cache.
  - `WorkspaceMap.get_workspace_tree_data(self, filter_text: str = None) -> Dict` — Get workspace data organized for tree display.
  - `WorkspaceMap.get_filtered_workspaces(self, filter_text: str = None) -> List[Dict]` — Get a filtered list of workspaces.
  - `WorkspaceMap.create_project(self, name: str) -> Optional[ptk.Workspace]` — Create a project named *name* under the current root, built from the
  - `WorkspaceMap.mark_root_as_project(self) -> Optional[ptk.Workspace]` — Mark the current ROOT directory as a shared Maya/Blender project —

<a id="light_utils--_light_utils"></a>
### `light_utils/_light_utils.py`

Light utilities — building real scene lights from the geometry that represents them.

- **[`class LightUtils(_LightUtilsInternal, pythontk.HelpMixin)`](mayatk/mayatk/light_utils/_light_utils.py#L234)** — Scene-light authoring (mirror of ``btk.LightUtils``).
  - `LightUtils.environment_lights(cls) -> List[str]` *(class)* — Every environment (image-based) light SHAPE in the scene.
  - `LightUtils.all_lights(cls) -> List[str]` *(class)* — Every light SHAPE in the scene -- Maya's and Arnold's.
  - `LightUtils.contributing_lights(cls) -> List[str]` *(class)* — The scene's light SHAPES that can actually light a render.
  - `LightUtils.light_contributes(shape: str) -> bool` *(static)* — Can this one light SHAPE light a render -- visible and non-zero?
  - `LightUtils.specular_lights(cls, shapes: Optional[Sequence[str]] = None, limit: Optional[int] = None, near: Optional[Sequence[float]] = None, lit: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]` *(class)* — The lights, as the lightmap manifest carries them for their highlights.
  - `LightUtils.lights_from_geometry(cls, objects: Optional[Sequence[Any]] = None, intensity: float = 100.0, kelvin: Optional[float] = None, color: Optional[Sequence[float]] = None, offset: float = 1.0, toward: Optional[Sequence[float]] = None, prefix: str = '', emit_specular: bool = False, cluster: str = 'shell') -> List[str]` *(class)* — Create a real area light matched to each selected fixture, or face set.
  - `LightUtils.generated_lights(cls) -> List[str]` *(class)* — Every light this tool generated.
  - `LightUtils.upgrade_authored_lights(cls) -> List[str]` *(class)* — Bring generated lights up to per-area emission;
  - `LightUtils.remove_lights(cls) -> List[str]` *(class)* — Delete this tool's generated lights;
  - `LightUtils.sync_lights_from_geometry(cls, lights: Optional[Sequence[Any]] = None, offset: float = 1.0) -> List[str]` *(class)* — Re-fit generated lights to their source geometry;

<a id="light_utils--hdr_manager"></a>
### `light_utils/hdr_manager.py`

Arnold HDR environment manager.

- **[`class HdrManager(pythontk.LoggingMixin, pythontk.HelpMixin)`](mayatk/mayatk/light_utils/hdr_manager.py#L55)** — Manage a single ``aiSkyDomeLight`` + connected ``file`` texture.
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
  - `HdrManager.select(self, part: str = 'skydome') -> Optional[str]` — Select the environment's *part*, replacing the selection.
  - `HdrManager.defer_apply(callback: Callable[[], None]) -> None` *(static)* — Run *callback*, a scene edit a dropdown pick drives, on Maya's idle

<a id="light_utils--light_layer_baker"></a>
### `light_utils/light_layer_baker.py`

Light layers: each light of a rig baked alone, the raw material of a look match.

- **[`class LightLayerBaker(pythontk.LoggingMixin)`](mayatk/mayatk/light_utils/light_layer_baker.py#L67)** — Bake light layers: each layer's lights alone, white, at one gain.
  - `LightLayerBaker.layer_payload(self, payload: Mapping[str, Any], base: Optional[Mapping[str, Any]] = None, others: Optional[List[str]] = None, white: bool = True) -> Dict[str, Any]` — The rig that lights the scene with *payload*'s lights alone, as a layer.
  - `LightLayerBaker.emission_target(payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]` *(static)* — The emission an emission layer lights the room with, as the
  - `LightLayerBaker.bake(self, layers: Mapping[str, Mapping[str, Any]], output_dir: str, objects: Optional[List[str]] = None, base: Optional[Mapping[str, Any]] = None, white: bool = True) -> Dict[str, Dict[str, Any]]` — Bake each layer alone into ``<output_dir>/<name>/``.
  - `LightLayerBaker.panoramas(self, rig: Union[str, Mapping[str, Any]], positions: Mapping[str, List[float]], output_dir: str, width: int = 1024) -> Dict[str, Optional[str]]` — The room under *rig*, seen from each point: equirectangular HDRs.
  - `LightLayerBaker.focal_length(vfov: float, aperture_mm: float) -> float` *(static)* — The focal length (mm) giving a vertical field of view of *vfov* degrees
  - `LightLayerBaker.beauty(self, rig: Optional[Union[str, Mapping[str, Any]]], camera: Mapping[str, Any], path: str, size: Tuple[int, int] = (819, 480), aa: int = 4) -> str` — An Arnold render of the scene under *rig*: what a mix should look like.
  - `LightLayerBaker.serve(cls, queue: str, scene: str) -> int` *(class)* — Be the worker in ``mayapy``: open *scene* once, then run every job *queue* gets.
  - `LightLayerBaker.serve_gui(cls, queue: str, scene: str, poll_ms: int = 1000) -> None` *(class)* — Be the worker in a fresh GUI Maya (where Arnold renders licensed).
  - `LightLayerBaker.launch(cls, queue: str, scene: str, host: str = 'gui', executable: Optional[str] = None) -> None` *(class)* — Start a fresh worker on *scene* for *queue*;
  - `LightLayerBaker.cli(cls, argv: Optional[List[str]] = None) -> int` *(class)* — The command line (``python -m mayatk.light_utils.light_layer_baker``).

<a id="light_utils--light_rig"></a>
### `light_utils/light_rig.py`

Light Rig manager: save, manage, swap and restore named light rigs.

- **[`class LightRig(_LightRigInternal, pythontk.LoggingMixin)`](mayatk/mayatk/light_utils/light_rig.py#L640)** — Save, manage, swap and restore named light rigs (see the module docstring).
  - `LightRig.store(self) -> 'ptk.PresetStore'` *(property)* — The rig library.
  - `LightRig.aim_rotation(pos, aim) -> List[float]` *(static)* — World rotation (xyz, degrees) pointing a light's -Z from *pos* at *aim*.
  - `LightRig.normalize(cls, payload: Dict[str, Any]) -> Dict[str, Any]` *(class)* — *payload* in the canonical form (module docstring);
  - `LightRig.fixture_overrides(cls, fixtures: Optional[List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]` *(class)* — The overrides that put each fixture in its mode (``ptk.LightRigFixture``):
  - `LightRig.diff(cls, a: Dict[str, Any], b: Dict[str, Any], tolerance: float = 0.0001) -> List[str]` *(class)* — What changes from rig *a* to rig *b*, one line each (empty when they match).
  - `LightRig.fingerprint(cls, payload: Dict[str, Any]) -> str` *(class)* — A short hash of a rig's lights, blind to float noise (what "edited" compares).
  - `LightRig.capture(self, group: Optional[str] = None) -> Dict[str, Any]` — The lights under *group* as a payload (their overrides are not included:
  - `LightRig.applied(self, group: Optional[str] = None) -> Tuple[Optional[str], bool]` — ``(rig name, edited since)`` for the rig in *group*;
  - `LightRig.compare(self, rig: Union[str, Dict[str, Any]], group: Optional[str] = None) -> List[str]` — What applying *rig* would change in the scene, one line each (empty: nothing).
  - `LightRig.scene_rigs(self) -> List[Tuple[str, Optional[str]]]` — ``(group, rig name)`` for every rig group in the scene.
  - `LightRig.can_swap_back(self, group: Optional[str] = None) -> bool` — Whether :meth:`swap_back` has a rig to return to (in *group*, or any group).
  - `LightRig.remove(self, group: Optional[str] = None) -> List[str]` — Delete *group*, its lights, and the filters and textures only they used.
  - `LightRig.apply(self, rig: Union[str, Dict[str, Any]], group: Optional[str] = None, snapshot: bool = True, name: Optional[str] = None) -> Dict[str, Any]` — Swap the scene's rig for *rig* (a library name or a payload).
  - `LightRig.swap_back(self, group: Optional[str] = None) -> Dict[str, Any]` — Return to the rig the last :meth:`apply` replaced in *group* (again to toggle back).
  - `LightRig.names(self) -> List[str]` — The saved rigs, sorted.
  - `LightRig.load(self, name: str) -> Dict[str, Any]` — A saved rig's payload.
  - `LightRig.save(self, name: str, description: Optional[str] = None, group: Optional[str] = None, force: bool = False) -> str` — Save the rig in *group* as *name*;
  - `LightRig.delete(self, name: str) -> bool` — Delete a saved rig (a locked one stays).
  - `LightRig.rename(self, old: str, new: str) -> bool` — Rename a saved rig;
  - `LightRig.landed_overrides(cls, payload: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Dict[str, Any]]], List[str]]` *(class)* — *payload*'s overrides as they land: ``({section: {node: {attr:

<a id="light_utils--lightmap_baker--_probe_placement"></a>
### `light_utils/lightmap_baker/_probe_placement.py`

Where a lightmap bake's reflection probe stands -- Maya's scene for the placement.

- **[`class ProbePlacement(pythontk.ProbePlacement)`](mayatk/mayatk/light_utils/lightmap_baker/_probe_placement.py#L95)** — Place a lightmap bake's reflection probe in the Maya scene

<a id="light_utils--lightmap_baker--lightmap_baker"></a>
### `light_utils/lightmap_baker/lightmap_baker.py`

High-level lightmap baking workflow for Maya -> game engines (Unity-first).

- **[`class LightmapBakeResult`](mayatk/mayatk/light_utils/lightmap_baker/lightmap_baker.py#L73)** — What one :meth:`LightmapBaker.bake` did -- the same shape in mayatk and blendertk.
  - `LightmapBakeResult.files(self) -> List[str]` *(property)* — The distinct map files, sorted: an atlas 40 objects share counts once.
  - `LightmapBakeResult.folders(self) -> List[str]` *(property)* — The distinct folders the maps go in, compared the way the disk does
- **[`class LightmapBaker(pythontk.LoggingMixin)`](mayatk/mayatk/light_utils/lightmap_baker/lightmap_baker.py#L257)** — Orchestrate the lightmap workflow: check -> bake -> dilate -> record.
  - `LightmapBaker.device(self) -> Optional[str]` *(property)* — Which device Arnold bakes on -- ``"AUTO"`` (the default: the GPU
  - `LightmapBaker.adaptive(self) -> bool` *(property)* — Whether a GPU bake spends its samples adaptively: the preset's AA on
  - `LightmapBaker.preset_store() -> 'ptk.PresetStore'` *(static)* — Shared store of lightmap quality presets (built-in + user tiers).
  - `LightmapBaker.preset_name(cls, name: str) -> str` *(class)* — *name* as the preset store knows it now: itself when the store has
  - `LightmapBaker.from_preset(cls, name: str, **overrides) -> 'LightmapBaker'` *(class)* — Construct a baker from a named quality preset.
  - `LightmapBaker.bake_targets(cls, objects: Optional[List[str]] = None) -> List[str]` *(class)* — The meshes a bake of *objects* acts on (default: the selection).
  - `LightmapBaker.bake(self, objects: Optional[List[str]] = None, packing: str = 'atlas', output_dir: Optional[str] = None, prefix: str = '', suffix: str = '_Lightmap', on_progress: Optional[Callable[[int, int, str], bool]] = None, intensity: float = 1.0, **kwargs) -> LightmapBakeResult` — Bake *objects*' lightmaps and record them: the whole workflow, as the panel runs it.
  - `LightmapBaker.stale_report(self, objects: Optional[List[str]] = None, reach: Optional[float] = None, output_dir: Optional[str] = None, relocate: bool = False) -> 'ptk.StaleReport'` — What a re-bake of the changed objects would redo, and why -- the dry run.
  - `LightmapBaker.rebake_changed(self, packing: Optional[str] = None, reach: Optional[float] = None, lighting: bool = True, relocate: bool = False, **bake_kwargs) -> LightmapBakeResult` — Re-bake what changed since the last bake, and what that change shades.
  - `LightmapBaker.rebake_report(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, relocate: bool = False, intensity: Optional[float] = None) -> 'ptk.StaleReport'` — What :meth:`rebake` of *objects* would redo -- its dry run.
  - `LightmapBaker.rebake(self, objects: Optional[List[str]] = None, packing: Optional[str] = None, relocate: bool = False, **bake_kwargs) -> LightmapBakeResult` — Re-bake *objects* the way each was baked, changed or not.
  - `LightmapBaker.map_folders(self, objects: Optional[List[str]] = None) -> Dict[str, List[str]]` — ``{folder: [map file names]}`` -- where the maps *objects* read are
  - `LightmapBaker.bake_breakdown(self, output_dir: Optional[str] = None, relocate: bool = False) -> 'ptk.ReportDoc'` — The scene's bake state, map by map -- the Bake State viewer.
  - `LightmapBaker.bake_maps(self, report: Optional['ptk.StaleReport'] = None, output_dir: Optional[str] = None, relocate: bool = False) -> List[Dict[str, Any]]` — The lightmaps the scene reads, each with its state -- the Lightmap
  - `LightmapBaker.bake_probe(self, maps: Optional[Dict[str, str]] = None) -> Optional[str]` — Capture the room the scene's bake lit as a reflection probe, and record it.
  - `LightmapBaker.preflight(self, objects: Optional[List[str]] = None) -> Optional[str]` — Why this scene cannot bake *objects* now, or ``None``;
  - `LightmapBaker.map_levels(cls, paths) -> Dict[str, Tuple[float, float]]` *(class)* — ``{path: (mean RGB, fraction of channels at the half-float ceiling)}``.
  - `LightmapBaker.peak_level(cls, paths) -> Optional[Tuple[str, float, float]]` *(class)* — ``(path, mean, saturated)`` for the BRIGHTEST map, or ``None`` if none reads.
  - `LightmapBaker.bake_verdict(self, paths) -> Optional[str]` — A warning about a finished bake's level, or ``None`` when it is plausible.
  - `LightmapBaker.bake_separated(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, prefix: str = 'lightmap_irr_', batch: bool = True, **kwargs) -> Dict[str, str]` — Bake a **lighting-only** (white-card) irradiance lightmap per object.
  - `LightmapBaker.bake_atlas(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, prefix: str = '', suffix: str = '_Lightmap', **kwargs) -> Dict[str, Tuple[str, List[float]]]` — Bake a material-atlased lighting-only lightmap set -- plan first, then bake to plan.
  - `LightmapBaker.atlas_plan(self, objects: Optional[List[str]] = None) -> Dict[str, List[Tuple[str, List[float]]]]` — ``{material: [(object, rect), ...]}`` -- the atlas layout, decided before baking.
  - `LightmapBaker.plan_sizes(self, plan: Dict[str, List[Tuple[str, List[float]]]]) -> Dict[str, Tuple[int, int]]` — ``{object: (width, height)}`` -- the pixel footprint each object occupies.
  - `LightmapBaker.pack_atlas(self, mapping: Dict[str, str], output_dir: Optional[str] = None, prefix: str = '', suffix: str = '_Lightmap', keep_sources: bool = False, plan: Optional[Dict[str, List[Tuple[str, List[float]]]]] = None, claims: Optional[Dict[str, Any]] = None, regions: Optional[Dict[str, Tuple[float, float, float, float]]] = None) -> Dict[str, Tuple[str, List[float]]]` — Consolidate per-object lightmaps into one atlas EXR per primary material.
  - `LightmapBaker.commit_lightmap(self, mapping: Dict[str, str], *, scale_offsets: Optional[Dict[str, List[float]]] = None) -> Dict[str, str]` — Record maps baked elsewhere: :meth:`LightmapRecords.commit`.
  - `LightmapBaker.revert(self, objects: Optional[List[str]] = None) -> List[str]` — Take the lightmaps off *objects*, or off every baked object for ``None``.
  - `LightmapBaker.revert_lightmap(self, objects: Optional[List[str]] = None) -> List[str]` — :meth:`revert`, under its original name.
  - `LightmapBaker.baked_objects(self, objects: Optional[List[str]] = None) -> List[str]` — The objects :meth:`revert` would take the lightmap from.
  - `LightmapBaker.track(self, objects: Optional[List[str]] = None) -> List[str]` — Start tracking *objects*' untracked maps as they stand -- no render.
  - `LightmapBaker.combine_sizes(self, maps: Optional[List[str]] = None) -> List[Tuple[int, float]]` — ``[(size, kept)]`` -- the atlas sizes :meth:`combine` of *maps* can
  - `LightmapBaker.combine(self, maps: Optional[List[str]] = None, size: Optional[int] = None, merge_materials: bool = True, on_progress: Optional[Callable[[int, int, str], Any]] = None) -> LightmapBakeResult` — Pack each split group *maps* belong to into one atlas -- no render.
  - `LightmapBaker.scope_meshes(scope: str = 'selected') -> List[str]` *(static)* — The meshes a Scope word names, before the Exclude set: what the
  - `LightmapBaker.scope_lights(cls, scope: str = 'selected') -> List[str]` *(class)* — The light shapes a Scope word holds: what lights a bake of
  - `LightmapBaker.select(objects) -> List[str]` *(static)* — Make *objects* the selection -- those that exist, by name or DAG

<a id="light_utils--lightmap_baker--lightmap_records"></a>
### `light_utils/lightmap_baker/lightmap_records.py`

The scene record a lightmap bake leaves in Maya: markers, manifest, and the files they name.

- **[`class LightmapRecords(pythontk.LoggingMixin)`](mayatk/mayatk/light_utils/lightmap_baker/lightmap_records.py#L80)** — Maya's lightmap markers and manifest, and the files they name.
  - `LightmapRecords.baked_objects(cls, objects: Optional[List[str]] = None) -> List[str]` *(class)* — The objects :meth:`revert` would take the lightmap from.
  - `LightmapRecords.lightmap_info(cls, obj: str) -> Dict[str, Any]` *(class)* — *obj*'s committed lightmap -- its marker plus where the map is NOW.
  - `LightmapRecords.commit(cls, mapping: Dict[str, str], scale_offsets: Optional[Dict[str, List[float]]] = None, intensity: float = 1.0, written: bool = True, cells: Optional[Dict[str, Dict[str, Any]]] = None, lighting: Optional[Dict[str, Any]] = None) -> Dict[str, str]` *(class)* — Record a lighting-only bake for the engine (fully non-destructive).
  - `LightmapRecords.bake_state(cls) -> Dict[str, Any]` *(class)* — This scene's bake baseline, decoded (``ptk.BakeBaseline.decode``).
  - `LightmapRecords.scene_state(cls, objects: Optional[List[str]] = None) -> Dict[str, Any]` *(class)* — The scene as a bake sees it, for ``ptk.BakeBaseline``: every static
  - `LightmapRecords.untracked(cls, objects: Optional[List[str]] = None) -> List[str]` *(class)* — The marked objects the bake baseline has never seen, sorted.
  - `LightmapRecords.track(cls, objects: Optional[List[str]] = None, lighting: Optional[Dict[str, Any]] = None) -> List[str]` *(class)* — Start tracking the :meth:`untracked` among *objects* -- no render.
  - `LightmapRecords.revert(cls, objects: Optional[List[str]] = None) -> List[str]` *(class)* — Undo :meth:`commit` -- drop the markers and republish.
  - `LightmapRecords.probe(cls) -> Optional[Dict[str, Any]]` *(class)* — This scene's reflection probe, or ``None``.
  - `LightmapRecords.commit_probe(cls, path: str, position: Sequence[float], box: Optional[Sequence[Sequence[float]]] = None) -> Dict[str, Any]` *(class)* — Record the reflection probe the bake wrote to *path*, and republish.
  - `LightmapRecords.project_root() -> Optional[str]` *(static)* — The project this scene's lightmaps belong to;
  - `LightmapRecords.superseding(cls, objects: List[str]) -> Iterator[List[str]]` *(class)* — Around a re-bake's :meth:`commit`: set aside the maps *objects* stop reading.
  - `LightmapRecords.migrate_legacy(cls, objects: Optional[List[str]] = None) -> List[str]` *(class)* — Bring markers older than the rect-binding contract up to date, losslessly.
  - `LightmapRecords.migrate_folder_hints(cls, objects: Optional[List[str]] = None) -> List[str]` *(class)* — Lift a legacy marker's ``dir`` into the private folder record.
  - `LightmapRecords.export_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]` *(class)* — The ``lightmaps`` record for this scene, or ``None`` when no
  - `LightmapRecords.refresh_export_metadata(cls) -> Optional[str]` *(class)* — Rebuild the ``lightmaps`` export channel from the scene's markers.
  - `LightmapRecords.claims(cls, objects: Optional[List[str]] = None) -> Dict[str, FrozenSet[str]]` *(class)* — ``{file name: transforms}`` -- which objects read which map, by lower-case name.
  - `LightmapRecords.wrote(cls, map_name: str, writers: Optional[Dict[str, str]] = None) -> bool` *(class)* — Whether this scene wrote *map_name*: its writer record says so
  - `LightmapRecords.strangers(cls, folder: str, stem: str, ext: str = '.exr', claims: Optional[Dict[str, FrozenSet[str]]] = None) -> Set[str]` *(class)* — The files in *folder* a bake naming ``<stem>`` must not replace:
  - `LightmapRecords.foreign_writer(cls, map_name: str, writers: Optional[Dict[str, str]] = None) -> Optional[str]` *(class)* — The other scene file that wrote *map_name* (its writer record's
  - `LightmapRecords.lightmap_dependencies(cls, objects: Optional[List[str]] = None, search_dirs: Optional[List[str]] = None, walk: bool = True) -> List[Dict[str, Any]]` *(class)* — Every lightmap the scene's markers name, resolved on disk NOW.
  - `LightmapRecords.search_dirs(cls, objects: Optional[List[str]] = None) -> List[str]` *(class)* — Where this scene's lightmaps can be found NOW, for a consumer that joins.
  - `LightmapRecords.heal_lightmap_paths(cls, objects: Optional[List[str]] = None) -> Dict[str, Any]` *(class)* — Rewrite stale marker hints to where the maps actually are;
  - `LightmapRecords.normalize_lightmap_paths(cls, objects: Optional[List[str]] = None, relative: bool = True) -> int` *(class)* — Rewrite every in-scope marker's folder to its portable (or absolute) spelling.
  - `LightmapRecords.relocate_lightmaps(cls, dest_dir: str, source_dir: str = '', mode: str = 'copy', objects: Optional[List[str]] = None, dry_run: bool = False, maps: Optional[Iterable[str]] = None) -> Dict[str, Any]` *(class)* — Gather the scene's lightmaps into *dest_dir* and repoint the markers.
  - `LightmapRecords.repath_lightmaps(cls, dirs_by_map: Dict[str, str], objects: Optional[List[str]] = None, relative: bool = True) -> int` *(class)* — Point every in-scope marker naming a map in *dirs_by_map* at its new folder.
  - `LightmapRecords.rename_lightmap(cls, old_name: str, new_name: str) -> int` *(class)* — Re-stamp every record naming map *old_name* with *new_name*.
  - `LightmapRecords.fork_lightmap(cls, old_name: str, new_name: str) -> int` *(class)* — Make *new_name* -- this scene's copy of a map another scene file
  - `LightmapRecords.transfer_lightmaps(cls, targets, source, *, output_dir: Optional[str] = None, output_name: Optional[str] = None, size: Optional[int] = None, supersample: int = 2, padding: int = -1) -> Dict[str, Dict[str, str]]` *(class)* — Carry each source mesh's committed lightmap onto its paired target.

<a id="mat_utils--_mat_utils"></a>
### `mat_utils/_mat_utils.py`

``MatUtils`` -- the public index of mayatk's material and texture operations.

- **[`class MatUtils(_MatUtilsInternal)`](mayatk/mayatk/mat_utils/_mat_utils.py#L65)**
  - `MatUtils.resolve_path(path: str, search: bool = True) -> Union[str, None]` *(static)* — Resolve a texture path, expanding env vars and tile/frame tokens.
  - `MatUtils.get_mats(objs=None, *, mat_type=None, include_displacement=False) -> List[str]` *(static)* — Returns the materials assigned to a given list of objects or components.
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
  - `MatUtils.is_mat_assigned(mat: object) -> bool` *(static)* — True iff a shading engine downstream of *mat* has a DAG member.
  - `MatUtils.is_connected(mat: object, delete: bool = False) -> bool` *(static)* — True when *mat* reaches no shading engine -- its own or one it feeds
  - `MatUtils.create_mat(mat_type, prefix='', name='')` *(static)* — Creates a material based on the provided type or a random material if 'mat_type' is 'random'.
  - `MatUtils.assign_mat(objects, mat_name)` *(static)* — Assigns a material to a list of objects or components.
  - `MatUtils.claim_material_name(shading_group: str, desired: str) -> str` *(static)* — Rename a rebuilt network to *desired* once that name is free.
  - `MatUtils.get_shading_assignments(obj) -> Dict[str, Optional[List[int]]]` *(static)* — Snapshot a mesh's shading-group membership as plain data.
  - `MatUtils.apply_shading_assignments(obj, assignments: Dict[str, Optional[List[int]]])` *(static)* — Apply a :meth:`get_shading_assignments` snapshot onto *obj*.
  - `MatUtils.create_file_node(image_path, name=None, color_space=None)` *(static)* — Create a ``file`` texture node with a wired ``place2dTexture``.
  - `MatUtils.create_shading_group(shader, name=None, assign_to=None)` *(static)* — Create a shading group for *shader* and optionally assign objects.
  - `MatUtils.resolve_opacity_mode(cls, opacity_mode=None) -> str` *(class)* — Normalize an opacity-mode argument to a :attr:`STINGRAY_GRAPHS` key.
  - `MatUtils.get_stingray_opacity_mode(cls, mat) -> Optional[str]` *(class)* — The :attr:`STINGRAY_GRAPHS` key of the graph loaded on *mat*.
  - `MatUtils.resolve_stingray_graph(cls, opacity_mode=None)` *(class)* — Absolute path to the ShaderFX preset for *opacity_mode*.
  - `MatUtils.load_stingray_graph(cls, mat, opacity_mode=None) -> bool` *(class)* — Load the ShaderFX preset for *opacity_mode* onto a StingrayPBS node.
  - `MatUtils.create_stingray_shader(cls, name, *, opacity_mode=None)` *(class)* — Create a StingrayPBS shader by loading a ShaderFX preset graph.
  - `MatUtils.find_by_mat_id(cls, material: str, objects: Optional[List[str]] = None, shell: bool = False) -> List[str]` *(class)* — Find objects or faces by the material ID.
  - `MatUtils.find_unassigned(cls, objects: Optional[List[str]] = None, include_default: bool = True) -> List[str]` *(class)* — Objects carrying no material — the complement of :meth:`find_by_mat_id`.
  - `MatUtils.collect_material_paths(materials: Optional[List[str]] = None, attributes: Optional[List[str]] = None, inc_mat_name: bool = False, inc_path_type: bool = False, resolve_full_path: bool = False) -> Union[List[str], List[Tuple[str, ...]]]` *(static)* — Collects specified attributes file paths for given materials.
  - `MatUtils.remap_file_nodes(file_paths: List[str], target_dir: str, silent: bool = False, limit_to_nodes: Optional[List[str]] = None, as_strings: bool = True) -> List[str]` *(static)* — Internal helper to remap file nodes to target_dir, preserving relative subfolders inside sourceimag…
  - `MatUtils.remap_texture_paths(cls, materials: Optional[List[str]] = None, new_dir: Optional[str] = None, silent: bool = False, file_nodes: Optional[List[str]] = None, objects: Optional[List[str]] = None, as_strings: bool = True) -> None` *(class)* — Remaps file texture paths for materials to new_dir.
  - `MatUtils.to_absolute(cls, path: str, workspace: Optional[str] = None, sourceimages: Optional[str] = None) -> str` *(class)* — Resolve a stored texture path to an absolute, forward-slashed path.
  - `MatUtils.to_project_relative(cls, path: str, workspace: Optional[str] = None, sourceimages: Optional[str] = None) -> str` *(class)* — *path* as a project-relative form, or unchanged when none exists.
  - `MatUtils.stage_textures_relative(cls, file_nodes: List[str], sourceimages: Optional[str] = None, external_mode: str = 'copy', scope: str = 'sourceimages') -> Dict[str, str]` *(class)* — Stage textures under sourceimages and store project-relative paths.
  - `MatUtils.rename_texture_file(cls, path: str, new_name: str, file_nodes: Optional[List[str]] = None) -> Dict[str, Any]` *(class)* — Rename a texture file on disk and repoint every file node reading it.
  - `MatUtils.sync_material_names(cls, material: Optional[str], base: str, material_affix: Tuple[str, str] = ('', ''), file_node_affix: Tuple[str, str] = ('', ''), file_nodes: Optional[List[str]] = None, dry_run: bool = False, lightmaps: Any = None) -> Dict[str, Any]` *(class)* — Name a material, its texture set, lightmap and nodes for ONE base.
  - `MatUtils.is_duplicate_material(material1: str, material2: str) -> bool` *(static)* — Check if two materials are duplicates based on their textures.
  - `MatUtils.find_materials_with_duplicate_textures(cls, materials: Optional[List[str]] = None, strict: bool = False, verify: bool = True) -> Dict[str, List[str]]` *(class)* — Find duplicate materials based on their texture file names or full paths.
  - `MatUtils.reassign_duplicate_materials(cls, materials: Optional[List[str]] = None, delete: bool = False, strict: bool = False, verify: bool = True) -> None` *(class)* — Find duplicate materials, remove duplicates, and reassign them to the original material.
  - `MatUtils.filter_materials_by_objects(objects: List[str], *, include_displacement: bool = False) -> List[str]` *(static)* — Filter materials assigned to the given objects.
  - `MatUtils.reload_textures(materials=None, inc=None, exc=None, log=False, refresh_viewport=False, refresh_hypershade=False, texture_types: Optional[List[str]] = None)` *(static)* — Reloads textures connected to specified materials with inclusion/exclusion filters.
  - `MatUtils.move_texture_files(cls, found_files: List[Union[str, Tuple[str, str]]], new_dir: str, delete_old: bool = False, create_dir: bool = True, per_file_timeout: float = 120.0, max_workers: int = 8, progress_callback: Optional[Callable[[int, int, str], bool]] = None) -> List[Tuple[str, str]]` *(class)* — Move or copy found texture files to a new directory.
  - `MatUtils.copy_textures_to_sourceimages(cls, objects: Optional[List[str]] = None, materials: Optional[List[str]] = None, file_nodes: Optional[List[str]] = None, sourceimages_dir: Optional[str] = None, delete_old: bool = False) -> List[Tuple[str, str]]` *(class)* — Copy referenced textures that live outside ``sourceimages`` into it.
  - `MatUtils.find_texture_files(cls, objects: Optional[List[str]] = None, source_dir: str = '', recursive: bool = True, return_dir: bool = False, quiet: bool = False, file_nodes: Optional[List[str]] = None, materials: Optional[List[str]] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None, filenames: Optional[List[str]] = None) -> List[Union[str, Tuple[str, str]]]` *(class)* — Find texture files for given objects' materials inside source_dir.
  - `MatUtils.migrate_textures(cls, materials: Optional[List[str]] = None, old_dir: Optional[str] = None, new_dir: Optional[str] = None, silent: bool = False, delete_old: bool = False, objects: Optional[List[str]] = None, file_nodes: Optional[List[str]] = None, progress_callback: Optional[Callable[[int, int, str], bool]] = None) -> None` *(class)* — Copies texture files from an old directory to a new one.
  - `MatUtils.move_unused_textures(source_dir: str = None, output_dir: str = None) -> None` *(static)* — Move unused textures to a specified directory.
  - `MatUtils.get_mat_swatch_icon(mat: Union[str, object], size: List[int] = [20, 20], fallback_to_blank: bool = True) -> object` *(static)* — Get an icon with a color fill matching the given material's RGB value.
  - `MatUtils.convert_bump_to_normal(bump_file_node, output_path: Optional[str] = None, intensity: float = 1.0, format_type: str = 'opengl', create_file_node: bool = True, node_name: Optional[str] = None) -> Optional[str]` *(static)* — Convert a bump/height file node's texture to a normal map on disk.
  - `MatUtils.validate_normal_map_setup(normal_file_node, material=None) -> Dict[str, Any]` *(static)* — Validate normal map file node setup and provide recommendations.
  - `MatUtils.graph_materials(materials: Union[str, List[str], object], mode: str = 'showUpAndDownstream') -> None` *(static)* — Open the Hypershade and graph the specified materials.
  - `MatUtils.get_texture_file_node(material, attr_name, _depth=0)` *(static)* — Locate the file texture node feeding a material attribute.
  - `MatUtils.probe_texture_path(cls, path: str) -> Optional[str]` *(class)* — The one concrete file *path*'s tile/frame pattern denotes, or None.
  - `MatUtils.has_path_token(cls, path: str) -> bool` *(class)* — Does *path* carry a tile/frame token — i.e.
  - `MatUtils.is_frame_sequence(cls, path: str) -> bool` *(class)* — Does *path*'s token count FRAMES (``<f>`` / ``<frame>``), not tiles?
  - `MatUtils.token_wildcard(cls, path: str, wildcard: Optional[str] = '*') -> str` *(class)* — *path* with every tile/frame token replaced by *wildcard*.
  - `MatUtils.texture_tiles(cls, path: str) -> List[str]` *(class)* — Every file on disk *path*'s tile/frame pattern denotes, sorted.
  - `MatUtils.apply_uv_tiling(cls, file_nodes) -> List[str]` *(class)* — Switch each file node reading ONE tile of a set to that set's tiling mode.

<a id="mat_utils--arnold_bridge"></a>
### `mat_utils/arnold_bridge.py`

Arnold render-bridge management.

- **[`class ArnoldBridge(pythontk.LoggingMixin, _ArnoldBridgeInternal)`](mayatk/mayatk/mat_utils/arnold_bridge.py#L55)** — Add, remove, query, and rebuild Arnold ``aiStandardSurface`` bridges.
  - `ArnoldBridge.add(self, materials: Optional[Union[str, List[str]]] = None, objects: Optional[Union[str, List[str]]] = None, force: bool = False) -> List[str]` — Attach an Arnold bridge to every base material in scope.
  - `ArnoldBridge.remove(self, materials: Optional[Union[str, List[str]]] = None, objects: Optional[Union[str, List[str]]] = None) -> List[str]` — Delete the Arnold bridge from every base material in scope.
  - `ArnoldBridge.rebuild(self, materials: Optional[Union[str, List[str]]] = None, objects: Optional[Union[str, List[str]]] = None) -> List[str]` — Remove and re-add the bridge — resyncs it to the base material's
  - `ArnoldBridge.temporary(self, materials: Union[str, List[str]]) -> Iterator[List[str]]` — Bridge *materials* for the duration of the block, then put every
  - `ArnoldBridge.get_bridge(self, material: str) -> Optional[str]` — Return the Arnold shader bridging *material* -- the first of its
  - `ArnoldBridge.has_bridge(self, material: str) -> bool` — True if any of *material*'s shading groups has an Arnold bridge.
  - `ArnoldBridge.authored_plug(plug: str) -> str` *(static)* — The plug *plug* follows: a bridge's ``emission`` resolves to the
  - `ArnoldBridge.unrenderable_materials(cls) -> List[str]` *(class)* — The materials Arnold would render as error magenta as the scene
  - `ArnoldBridge.base_materials() -> List[str]` *(static)* — Every scene material a bridge can serve: the scene's materials minus
  - `ArnoldBridge.select_bridged(self) -> List[str]` — Select every base material that has a bridge (never the bridge

<a id="mat_utils--bake_sets"></a>
### `mat_utils/bake_sets.py`

Scene-stored bake sets: named object sets the bake tools read.

- **[`class BakeSet`](mayatk/mayatk/mat_utils/bake_sets.py#L53)** — A scene's named object set, stored as a plain ``objectSet``.
  - `BakeSet.conventional_name(cls) -> str` *(class)* — :attr:`SET_NAME` spelled by the naming convention (``lightmapBaker_baked_SET``).
  - `BakeSet.name(cls) -> Optional[str]` *(class)* — The name the set is stored under in this scene, or ``None``.
  - `BakeSet.exists(cls) -> bool` *(class)* — Whether the set node (canonical or legacy) is present.
  - `BakeSet.members(cls) -> List[str]` *(class)* — Long names of the set's surviving members (deleted nodes drop out).
  - `BakeSet.member_names(cls) -> List[str]` *(class)* — The members' short names, for display (a live tooltip, a report).
  - `BakeSet.select(cls) -> List[str]` *(class)* — Select the set's members, hidden ones too;
  - `BakeSet.meshes(cls) -> List[str]` *(class)* — The mesh transforms the set covers -- a group member counts its descendants.
  - `BakeSet.define(cls, objects: Optional[List[str]] = None, conventional: bool = False) -> List[str]` *(class)* — Replace the set's contents with *objects* (default: the selection).
  - `BakeSet.respell(cls, conventional: bool) -> Optional[str]` *(class)* — Rename an existing set to its plain or conventional spelling;
  - `BakeSet.clear(cls) -> None` *(class)* — Delete the set node under every spelling (members themselves are untouched).
- **[`class BakeSourceSet(BakeSet)`](mayatk/mayatk/mat_utils/bake_sets.py#L220)** — The scene's bake source, shared by the Substance and Marmoset bridges.
  - `BakeSourceSet.companion_path(cls, export_path: str) -> str` *(class)* — ``.../asset.fbx`` -> ``.../asset_source.fbx``.
- **[`class LightmapExcludeSet(BakeSet)`](mayatk/mayatk/mat_utils/bake_sets.py#L248)** — Objects every lightmap bake of the scene gives no map of their own.
- **[`class LightmapIsolateSet(BakeSet)`](mayatk/mayatk/mat_utils/bake_sets.py#L265)** — Objects that bake in a pass of their own, out of every other bake's render.
  - `LightmapIsolateSet.groups(cls) -> List[List[str]]` *(class)* — The set's meshes by member: what lies under each one moves together.
  - `LightmapIsolateSet.meshes(cls) -> List[str]` *(class)* — Every mesh :meth:`groups` holds: each member with all it carries.

<a id="mat_utils--emissive_groups"></a>
### `mat_utils/emissive_groups.py`

Emissive Groups' Maya half: one ``objectSet`` of faces per group, keyable

- **[`class MayaEmissiveGroups(_MayaEmissiveGroupsInternal)`](mayatk/mayatk/mat_utils/emissive_groups.py#L96)** — The Maya scene operations of the Emissive Groups model
  - `MayaEmissiveGroups.faces(self, faces=None) -> FaceMap`
  - `MayaEmissiveGroups.add_members(self, store: str, face_map: FaceMap) -> None`
  - `MayaEmissiveGroups.remove_members(self, store: str) -> None`
  - `MayaEmissiveGroups.members(self, store: str) -> Optional[FaceMap]`
  - `MayaEmissiveGroups.select(self, face_map: FaceMap) -> None`
  - `MayaEmissiveGroups.membership_stores(self, prefix: str)`
  - `MayaEmissiveGroups.carrier_weights(self, prefix: str) -> List[str]`
  - `MayaEmissiveGroups.weight_exists(self, attr: str) -> bool`
  - `MayaEmissiveGroups.ensure_weight(self, attr: str, default: float) -> str`
  - `MayaEmissiveGroups.delete_weight(self, attr: str) -> bool`
  - `MayaEmissiveGroups.sync_weight(self, attr: str, value: float) -> None`
  - `MayaEmissiveGroups.key_weight(self, attr: str, value: Optional[float], frame: Optional[float]) -> str`
  - `MayaEmissiveGroups.foreign_color_sets(self, objects: List[str], color_set: str) -> Dict[str, List[str]]`
  - `MayaEmissiveGroups.write_colors(self, obj: str, channels, color_set: str) -> None`
  - `MayaEmissiveGroups.uv_triangles(self, obj: str, indices: List[int], uv_set: Optional[str]) -> list`
  - `MayaEmissiveGroups.mask_path(self, suffix: str) -> str`
  - `MayaEmissiveGroups.object_names(self, objects) -> List[str]`
  - `MayaEmissiveGroups.face_count(self, obj: str) -> int`
  - `MayaEmissiveGroups.mesh(self, name: str) -> Optional[str]`
  - `MayaEmissiveGroups.merge_stores(self, stores: List[str], ctx) -> None`
  - `MayaEmissiveGroups.discard_stores(self, stores: List[str], ctx) -> None`
  - `MayaEmissiveGroups.publish(self, records) -> None`
  - `MayaEmissiveGroups.extra_warnings(self) -> List[str]`
- **[`class EmissiveGroups(pythontk.EmissiveGroupsModel)`](mayatk/mayatk/mat_utils/emissive_groups.py#L301)** — The Emissive Groups API in Maya: pythontk's model over the Maya half.

<a id="mat_utils--game_shader"></a>
### `mat_utils/game_shader.py`

- **[`class GameShader(pythontk.LoggingMixin, _GameShaderInternal)`](mayatk/mayatk/mat_utils/game_shader.py#L795)** — A class to manage the creation of a shader network using StingrayPBS, Standard Surface or OpenPBR S…
  - `GameShader.shader_type_choices() -> List[Tuple[str, str]]` *(static)* — The surface shaders :meth:`create_network` builds (its ``shader_type``).
  - `GameShader.create_network(self, textures: List[str], name: str = '', prefix: str = '', suffix: str = '', config: Union[str, Dict[str, Any]] = None, progress_callback: Callable = None, **kwargs) -> Union[Optional[object], List[Optional[object]]]` — Create a PBR shader network with textures.
  - `GameShader.assign_network(self, network, objects) -> bool` — Assign the one material a :meth:`create_network` call built to *objects*.
  - `GameShader.resolve_opacity_sources(self, textures: List[str], type_cache: Dict[str, Optional[str]], config: Dict[str, Any] = None, shader_type: str = 'stingray', name: str = '') -> Tuple[List[str], List[str], List[tuple]]` — Settle where this set's opacity lives, before anything is wired.
  - `GameShader.setup_stringray_node(self, name: str, *, opacity_mode: str = None) -> object` — Create a StingrayPBS shader node with the right ShaderFX graph loaded.
  - `GameShader.setup_standard_surface_node(self, name: str, opacity: bool) -> object` — Creates and sets up a Maya Standard Surface shader node.
  - `GameShader.setup_open_pbr_node(self, name: str, opacity: bool) -> object` — Creates and sets up a Maya OpenPBR Surface shader node.
  - `GameShader.connect_stingray_nodes(self, texture: str, texture_type: str, sr_node: object) -> bool` — Connects texture files to the corresponding slots in the StingrayPBS shader node
  - `GameShader.connect_standard_surface_nodes(self, texture: str, texture_type: str, std_node: object) -> bool` — Connects texture files to Maya Standard Surface shader slots.
  - `GameShader.connect_open_pbr_nodes(self, texture: str, texture_type: str, op_node: object) -> bool` — Connects texture files to Maya OpenPBR Surface shader slots.
  - `GameShader.filter_for_correct_metallic_map(self, textures: List[str], use_metallic_smoothness: bool, output_extension: str = 'png') -> List[str]` — Filters textures to ensure the correct handling of metallic maps based on the use_metallic_smoothne…
  - `GameShader.filter_for_mask_map(self, textures: List[str], output_extension: str = 'png') -> List[str]` — Creates Unity HDRP Mask Map (MSAO) by packing Metallic, AO, Detail, and Smoothness.
  - `GameShader.filter_for_correct_base_color_map(self, textures: List[str], use_albedo_transparency: bool) -> List[str]` — Filters textures to ensure the correct handling of albedo maps based on the use_albedo_transparency…

<a id="mat_utils--image_to_plane--_image_to_plane"></a>
### `mat_utils/image_to_plane/_image_to_plane.py`

Map image files to textured polygon planes in Maya.

- **[`class ImageToPlane(pythontk.LoggingMixin)`](mayatk/mayatk/mat_utils/image_to_plane/_image_to_plane.py#L25)** — Create textured polygon planes from image files.
  - `ImageToPlane.create(cls, image_paths: List[str], mat_type: str = 'stingray', suffix: Optional[str] = None, prefix: str = '', plane_height: float = 10.0, axis: Optional[List[float]] = None, group: bool = False, group_name: str = 'imagePlanes_GRP', stingray_opacity_mode: str = 'transparent', mask_threshold: float = 0.5, roughness: float = 0.0) -> Dict[str, object]` *(class)* — Create textured planes for one or more images.
  - `ImageToPlane.remove(cls, objects=None) -> int` *(class)* — Remove planes and their materials created by this tool.

<a id="mat_utils--marmoset_bridge--_marmoset_bridge"></a>
### `mat_utils/marmoset_bridge/_marmoset_bridge.py`

Maya-side glue for the Marmoset Toolbag engine.

- **[`class MarmosetBridge(pythontk.HandoffBridge, _MarmosetBridgeInternal)`](mayatk/mayatk/mat_utils/marmoset_bridge/_marmoset_bridge.py#L243)** — Export the Maya selection to Marmoset Toolbag with templated automation.
  - `MarmosetBridge.toolbag_path(self) -> Optional[str]` *(property)*
  - `MarmosetBridge.params_registry(cls)` *(class)* — The panel's parameter registry (``parameters.Parameters``, a
  - `MarmosetBridge.list_template_modes() -> List[Tuple[str, str]]` *(static)* — ``[(template, mode), ...]`` for every bundled template, off the class
  - `MarmosetBridge.params_defaults(self) -> Dict[str, Any]`
  - `MarmosetBridge.render_template(self, *args, **kwargs) -> Optional[str]` — Render a Toolbag script body (delegates to the engine deliverer).
  - `MarmosetBridge.source_model_path_for(cls, fbx_path: str) -> str` *(class)* — ``.../asset.fbx`` -> ``.../asset_source.fbx`` (shared convention).
  - `MarmosetBridge.baked_texture_dir(cls) -> str` *(class)* — ``<scene project>/sourceimages/baked`` -- where a roundtrip's maps land.
  - `MarmosetBridge.toolbag_scene_dir(cls) -> str` *(class)* — ``<scene project>/data/marmoset`` -- where a roundtrip's ``.tbscene``
  - `MarmosetBridge.source_material_name(cls, mat_name: str) -> str` *(class)* — *mat_name* with every trailing ``_BAKED`` removed.
  - `MarmosetBridge.baked_material_name(cls, mat_name: str) -> str` *(class)* — ``<source material>_BAKED``, idempotent across re-bakes.
  - `MarmosetBridge.texture_set_aliases(cls, materials, log=None) -> Dict[str, str]` *(class)* — ``{material: name its map files carry}`` for every renamed material.
  - `MarmosetBridge.build_bake_pairs_manifest(objects: Sequence[str], high_suffix: str, low_suffix: str, include_children: bool = True, log=None) -> Dict[str, str]` *(static)* — Build the ``{mesh_short_name: 'source'|'target'}`` sidecar for the bake.

<a id="mat_utils--marmoset_bridge--_marmoset_engine"></a>
### `mat_utils/marmoset_bridge/_marmoset_engine.py`

Drive Marmoset Toolbag from the outside -- launch + templated automation.

- [`APP`](mayatk/mayatk/mat_utils/marmoset_bridge/_marmoset_engine.py#L45) — constant
- [`SEND_TO`](mayatk/mayatk/mat_utils/marmoset_bridge/_marmoset_engine.py#L64) — constant
- [`ROUND_TRIP`](mayatk/mayatk/mat_utils/marmoset_bridge/_marmoset_engine.py#L65) — constant
- **[`class MarmosetEngine(pythontk.Deliverer, pythontk.LoggingMixin)`](mayatk/mayatk/mat_utils/marmoset_bridge/_marmoset_engine.py#L76)** — Export-agnostic Marmoset Toolbag automation -- a hand-off :class:`pythontk.Deliverer`.
  - `MarmosetEngine.toolbag_path(self) -> Optional[str]` *(property)* — Resolve the Toolbag executable path.
  - `MarmosetEngine.toolbag_log_path(self) -> Optional[str]` *(property)* — Resolve Toolbag's application log file (script prints + tracebacks).
  - `MarmosetEngine.preflight(self, bridge, request) -> bool` — Validate the (template, mode) before the bridge produces its payload.
  - `MarmosetEngine.deliver(self, bridge, payload, request) -> Optional[Dict[str, Any]]` — Hand the produced model + manifests to Toolbag via :meth:`send`.
  - `MarmosetEngine.send(self, model_path: str, manifest_path: Optional[str] = None, pairs_path: Optional[str] = None, source_model_path: Optional[str] = None, output_dir: Optional[str] = None, texture_dir: Optional[str] = None, texture_set_aliases: Optional[Dict[str, str]] = None, output_name: Optional[str] = None, toolbag_exe: Optional[str] = None, template: str = 'import', mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None, scene_dir: Optional[str] = None) -> Optional[Dict[str, Any]]` — Render *template* in *mode* against *model_path* and hand off to Toolbag.
  - `MarmosetEngine.render_template(self, template: str, model_path: str, manifest_path: str, output_dir: str, mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None, headless: Optional[bool] = None, pairs_path: Optional[str] = None, source_model_path: Optional[str] = None, save_dir: Optional[str] = None) -> Optional[str]` — Return the rendered Toolbag Python script body, or *None* on miss.
  - `MarmosetEngine.list_templates() -> List[Path]` *(static)* — Return user-visible templates in ``templates/`` (skips underscore-prefixed).
  - `MarmosetEngine.template_modes(template_path: Path) -> Tuple[str, ...]` *(static)* — Return the modes declared by *template_path*'s ``BRIDGE_MODES`` constant.
  - `MarmosetEngine.list_template_modes() -> List[Tuple[str, str]]` *(static)* — Return ``[(stem, mode), ...]`` for every (template, mode) pairing.

<a id="mat_utils--marmoset_bridge--_toolbag_helpers"></a>
### `mat_utils/marmoset_bridge/_toolbag_helpers.py`

Shared helpers for Marmoset Toolbag template scripts.

- [`SLOT_MAP`](mayatk/mayatk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L656) — constant
- [`MODULE_NEUTRAL_FIELDS`](mayatk/mayatk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L679) — constant
- [`SUBROUTINE_FIXES`](mayatk/mayatk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L692) — constant
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

<a id="mat_utils--marmoset_bridge--marmoset_rpc--connection"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/connection.py`

JSON-RPC client bound to the marmoset_rpc Toolbag plugin.

- [`DEFAULT_HOST`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/connection.py#L37) — constant
- [`DEFAULT_PORT`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/connection.py#L38) — constant
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

- [`start_server(port=None, host=None)`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/__init__.py#L53) — Start the RPC server (idempotent).
- [`stop_server()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/__init__.py#L58) — Shut the server down (tests / hot-reload).
- [`is_running()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/__init__.py#L63) — True while the server is bound.
- [`autostart()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/__init__.py#L68) — Start on plugin load, gated to the Toolbag host.
- [`PLUGIN`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/__init__.py#L33) — constant

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
  - `MainThreadMarshaller.post(self, fn, *args, **kwargs)` — Queue *fn* for the main thread and return at once -- never blocks.
  - `MainThreadMarshaller.run(self, fn, *args, timeout=None, **kwargs)` — Call *fn*, on the main thread when one is reachable.
- **[`class RpcPlugin(object)`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/_rpc_core.py#L451)** — One host plugin: a registry, a marshaller, and the server that joins them.
  - `RpcPlugin.import_ops(package)` *(static)* — Import *package* (dotted name), forcing its ``@register`` side effects.
  - `RpcPlugin.port(self)` *(property)* — Configured port: ``<PREFIX>_PORT`` if set and numeric, else the default.
  - `RpcPlugin.is_hosted(self)` — True only inside the real host application.
  - `RpcPlugin.is_running(self)` — True while the HTTP server is bound.
  - `RpcPlugin.address(self)` *(property)* — The bound ``(host, port)``, or ``None`` when not running.
  - `RpcPlugin.start(self, port=None, host=None)` — Bind and serve on a daemon thread.
  - `RpcPlugin.serve(self, port=None, host=None)` — Bind and serve on the CALLING thread until :meth:`stop`;
  - `RpcPlugin.stop(self)` — Shut the server down (host teardown hook, tests, hot-reload, an op).
  - `RpcPlugin.autostart(self)` — Start on plugin load, but only when actually hosted.
  - `RpcPlugin.autostart_safely(self)` — :meth:`autostart`, but a failure is logged instead of raised.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--ops--scene_ops"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/scene_ops.py`

Scene-inspection ops.

- [`summary()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/scene_ops.py#L15) — High-level snapshot of the current Toolbag scene.
- [`list_materials()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/scene_ops.py#L39) — Material names in the current scene.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--ops--system_ops"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py`

Toolbag-specific system ops.

- [`version()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py#L15) — Toolbag build number (e.g.

<a id="mat_utils--marmoset_bridge--parameters"></a>
### `mat_utils/marmoset_bridge/parameters.py`

Registry of user-tunable Marmoset Toolbag parameters exposed to the bridge UI.

- [`PARAMS`](mayatk/mayatk/mat_utils/marmoset_bridge/parameters.py#L24) — constant
- [`SUPERSESSIONS`](mayatk/mayatk/mat_utils/marmoset_bridge/parameters.py#L399) — constant
- **[`class Parameters(ParamRegistry)`](mayatk/mayatk/mat_utils/marmoset_bridge/parameters.py#L418)** — Parameters — module namespace.

<a id="mat_utils--marmoset_bridge--template_params"></a>
### `mat_utils/marmoset_bridge/template_params.py`

Plain default values + literal formatting for Marmoset template tokens.

- [`DEFAULTS`](mayatk/mayatk/mat_utils/marmoset_bridge/template_params.py#L26) — constant
- **[`class TemplateParams`](mayatk/mayatk/mat_utils/marmoset_bridge/template_params.py#L101)** — TemplateParams — module namespace.
  - `TemplateParams.derive_auto_maps(manifest: Dict[str, Any]) -> Dict[str, bool]` *(static)* — Return the ``{MAP_*: bool}`` roster *manifest*'s textures imply.
  - `TemplateParams.derive_bake_values(values: Dict[str, Any]) -> Dict[str, Any]` *(static)* — Return the managed bake tokens derived from *values*.
  - `TemplateParams.python_literal(value: Any) -> str` *(static)* — Format *value* as a Python source literal for template substitution.
  - `TemplateParams.defaults() -> Dict[str, Any]` *(static)* — Return a copy of :data:`DEFAULTS`.
  - `TemplateParams.to_context(values: Dict[str, Any]) -> Dict[str, str]` *(static)* — Map ``{KEY: value}`` to ``{KEY: python-literal-string}``.

<a id="mat_utils--marmoset_bridge--templates--bake"></a>
### `mat_utils/marmoset_bridge/templates/bake.py`

Bake source detail + surface maps onto the target meshes.

- [`main()`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L662)
- [`BRIDGE_MODES`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L22) — constant
- [`MODEL_FILE`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L42) — constant
- [`SOURCE_MODEL_FILE`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L43) — constant
- [`PAIRS_FILE`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L44) — constant
- [`MANIFEST_FILE`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L45) — constant
- [`OUTPUT_DIR`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L46) — constant
- [`SAVE_PATH`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L47) — constant
- [`SHOULD_QUIT`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L48) — constant
- [`BAKE_SIZE`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L58) — constant
- [`BAKE_SAMPLES`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L59) — constant
- [`BAKE_PADDING`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L60) — constant
- [`BAKE_BITS`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L61) — constant
- [`OUTPUT_FORMAT`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L62) — constant
- [`MAP_NORMAL`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L65) — constant
- [`MAP_AO`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L66) — constant
- [`MAP_CURVATURE`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L67) — constant
- [`MAP_THICKNESS`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L68) — constant
- [`MAP_POSITION`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L69) — constant
- [`MAP_MATID`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L70) — constant
- [`MAP_ALBEDO`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L71) — constant
- [`MAP_ROUGHNESS`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L72) — constant
- [`MAP_METALNESS`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L73) — constant
- [`MAP_EMISSIVE`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L74) — constant
- [`HIGH_SUFFIX`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L77) — constant
- [`LOW_SUFFIX`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L78) — constant
- [`SUFFIX_INCLUDE_CHILDREN`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L79) — constant
- [`CAGE_OFFSET`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L80) — constant
- [`AUTO_CAGE`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L81) — constant
- [`IGNORE_BACKFACES`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L82) — constant
- [`CAGE_STANDOFFS`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L93) — constant
- [`CAGE_HOST_DIAGONAL`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L94) — constant
- [`CAGE_REACH_FACTOR`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L103) — constant
- [`AUTO_CAGE_MARGIN`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L108) — constant
- [`AUTO_CAGE_FLOOR`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L126) — constant
- [`AUTO_CAGE_BOUNDS_FLOOR`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L127) — constant
- [`AUTO_CAGE_CEILING`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L128) — constant
- [`AUTO_CAGE_GAP_MARGIN`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L132) — constant

<a id="mat_utils--marmoset_bridge--templates--import"></a>
### `mat_utils/marmoset_bridge/templates/import.py`

Open the model in Toolbag and wire materials from the manifest.

- [`main()`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/import.py#L35)
- [`BRIDGE_MODES`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/import.py#L12) — constant
- [`MODEL_FILE`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/import.py#L25) — constant
- [`MANIFEST_FILE`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/import.py#L26) — constant
- [`SAVE_PATH`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/import.py#L27) — constant
- [`SHOULD_QUIT`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/import.py#L28) — constant

<a id="mat_utils--marmoset_bridge--templates--lookdev"></a>
### `mat_utils/marmoset_bridge/templates/lookdev.py`

Open the model in Toolbag, apply a Sky preset, and frame the model.

- [`main()`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/lookdev.py#L38)
- [`BRIDGE_MODES`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/lookdev.py#L12) — constant
- [`MODEL_FILE`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/lookdev.py#L25) — constant
- [`MANIFEST_FILE`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/lookdev.py#L26) — constant
- [`SAVE_PATH`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/lookdev.py#L27) — constant
- [`SHOULD_QUIT`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/lookdev.py#L28) — constant
- [`SKY_PRESET`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/lookdev.py#L30) — constant
- [`FRAME_SELECTION`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/lookdev.py#L31) — constant

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

- **[`class MatManifest(pythontk.HelpMixin)`](mayatk/mayatk/mat_utils/mat_manifest.py#L18)** — Builds and restores a material-to-texture manifest for bridge workflows.
  - `MatManifest.build(cls, objects: List) -> Dict[str, Any]` *(class)* — Build a manifest from the materials assigned to *objects*.
  - `MatManifest.restore(cls, mat_name: str, manifest: Dict[str, Any], source_mat_name: Optional[str] = None) -> int` *(class)* — Reconnect file textures to *mat_name* from a previously built manifest.

<a id="mat_utils--mat_snapshot"></a>
### `mat_utils/mat_snapshot.py`

Lightweight material state snapshot and restore.

- **[`class MatSnapshot(_MatSnapshotInternal)`](mayatk/mayatk/mat_utils/mat_snapshot.py#L394)** — Capture and restore material state across destructive operations.
  - `MatSnapshot.capture(cls, mat_name: str, objects=None) -> Dict[str, Any]` *(class)* — Snapshot textures, scalar values and wiring for *mat_name*.
  - `MatSnapshot.restore(cls, mat_name: str, snapshot: Dict[str, Any], source_mat_name: Optional[str] = None) -> Dict[str, int]` *(class)* — Restore textures, scalar values and wiring onto *mat_name*.
  - `MatSnapshot.restored(cls, mat_name: str, objects=None)` *(class)* — Scope form of :meth:`capture` / :meth:`restore` (manifest + scalars).
  - `MatSnapshot.network_scope(cls, materials)` *(class)* — Scope form of :meth:`capture_network` / :meth:`restore_network`.
  - `MatSnapshot.capture_network(cls, materials) -> Dict[str, Any]` *(class)* — Record the exact upstream wiring of *materials* so it can be undone.
  - `MatSnapshot.restore_network(cls, snapshot: Dict[str, Any]) -> Dict[str, int]` *(class)* — Reverse everything a graph rewrite did since :meth:`capture_network`.
  - `MatSnapshot.surviving_node(cls, snapshot: Dict[str, Any], node: str) -> Optional[str]` *(class)* — The node that stands for *node* once :meth:`restore_network` has run.

<a id="mat_utils--mat_updater"></a>
### `mat_utils/mat_updater.py`

- **[`class MatUpdater(pythontk.LoggingMixin)`](mayatk/mayatk/mat_utils/mat_updater.py#L23)** — Updates existing materials with processed textures.
  - `MatUpdater.update_materials(cls, materials: List[Any] = None, config: Union[str, Dict[str, Any]] = None, verbose: bool = False, progress_callback: Optional[Callable[[int, int, str], None]] = None, shader_type: Optional[str] = None) -> Dict[str, Any]` *(class)* — Update materials with processed textures.
  - `MatUpdater.disconnect_associated_attributes(cls, material, file_paths, config=None)` *(class)* — Disconnects PBR attributes if they are driven by the specified files.
  - `MatUpdater.update_network(cls, material, texture_paths, config) -> Dict[str, str]` *(class)* — Connect processed textures to the material.
  - `MatUpdater.shader_type_choices() -> List[Tuple[str, str]]` *(static)* — The retype targets ``update_materials(shader_type=...)`` takes.
  - `MatUpdater.texture_root() -> str` *(static)* — Where a run resolves a relative ``move_to_folder`` and discovers
  - `MatUpdater.selected_materials(cls, shader_type: Optional[str] = None) -> List[str]` *(class)* — The materials a run over the selection acts on.
  - `MatUpdater.materials_using_textures(cls, paths: List[str], shader_type: Optional[str] = None) -> List[str]` *(class)* — The scene materials whose file nodes read any of *paths*, kept to the

<a id="mat_utils--render_opacity--attribute_mode"></a>
### `mat_utils/render_opacity/attribute_mode.py`

- **[`class OpacityAttributeMode(pythontk.LoggingMixin)`](mayatk/mayatk/mat_utils/render_opacity/attribute_mode.py#L23)** — Implements the 'attribute' mode for the render-effect channels.
  - `OpacityAttributeMode.create(cls, objects, spec: ChannelSpec = OPACITY) -> Dict[str, Dict]` *(class)* — Add the channel's attribute(s) on each transform (no keyframes).
  - `OpacityAttributeMode.has_channel(cls, obj, spec: ChannelSpec = OPACITY) -> bool` *(class)* — Whether *obj* carries the channel's attribute.
  - `OpacityAttributeMode.channels_on(cls, obj) -> List[ChannelSpec]` *(class)* — Every channel *obj* carries.
  - `OpacityAttributeMode.set_color(cls, objects, color, spec: ChannelSpec = HIGHLIGHT, stop: str = 'hi') -> List[str]` *(class)* — Write the channel's colour attribute on *objects*, leaving keys alone.
  - `OpacityAttributeMode.get_color(cls, obj, spec: ChannelSpec = HIGHLIGHT, stop: str = 'hi') -> Optional[Tuple[float, float, float]]` *(class)* — One end of the channel's authored colour ramp on *obj*.
  - `OpacityAttributeMode.get_color_stops(cls, obj, spec: ChannelSpec = HIGHLIGHT) -> Tuple` *(class)* — Every end of the channel's colour ramp on *obj*, high first.
  - `OpacityAttributeMode.key_fade(cls, objects, start: float, end: float, direction: str = 'in', auto_create: bool = True, tangent: str = 'linear', spec: ChannelSpec = OPACITY, whole_frames: bool = True) -> List[Tuple[str, str]]` *(class)* — Key a two-key ramp on the channel;
  - `OpacityAttributeMode.key_pulse(cls, objects, start: float, end: float, period: float, bright_fraction: float = 0.59, ramp_fraction: float = 0.25, lead_in: Optional[float] = None, lead_out: Optional[float] = None, color: Optional[Sequence[float]] = None, dim_color: Optional[Sequence[float]] = None, auto_create: bool = True, spec: ChannelSpec = HIGHLIGHT, whole_frames: bool = True) -> List[str]` *(class)* — Key a repeating bright/dim pulse on the channel over ``start..end``.
  - `OpacityAttributeMode.write_keys(cls, obj, keys, spec: ChannelSpec = OPACITY, tangent: str = 'linear', mirror: Optional[bool] = None) -> List[Tuple[str, float]]` *(class)* — Key a planned ``[(frame, value), ...]`` on *obj*'s channel -- the one
  - `OpacityAttributeMode.fade_windows(keys, eps: float = 0.001) -> List[Tuple[float, float]]` *(static)* — Reduce a DENSE opacity curve to the sparse visibility keys that
  - `OpacityAttributeMode.sync_visibility_from_opacity(cls, objects, windows: bool = False) -> None` *(class)* — Create visibility keyframes that mirror the opacity animation curve.
  - `OpacityAttributeMode.ensure_connections(cls, objects) -> None` *(class)* — Repair opacity → visibility mirroring on objects that already have
  - `OpacityAttributeMode.remove(cls, objects, spec: Optional[ChannelSpec] = None)` *(class)* — Delete the channel's attribute(s) and their curves from *objects*.

<a id="mat_utils--render_opacity--channels"></a>
### `mat_utils/render_opacity/channels.py`

The per-object render-effect channel table.

- [`spec_for(channel) -> ChannelSpec`](mayatk/mayatk/mat_utils/render_opacity/channels.py#L118) — Resolve a name or spec to a :class:`ChannelSpec`.
- [`CHANNELS`](mayatk/mayatk/mat_utils/render_opacity/channels.py#L93) — constant
- [`OPACITY`](mayatk/mayatk/mat_utils/render_opacity/channels.py#L111) — constant
- [`HIGHLIGHT`](mayatk/mayatk/mat_utils/render_opacity/channels.py#L112) — constant
- [`PRESENCE`](mayatk/mayatk/mat_utils/render_opacity/channels.py#L115) — constant
- **[`class ChannelSpec`](mayatk/mayatk/mat_utils/render_opacity/channels.py#L28)** — One per-object render-effect channel.
  - `ChannelSpec.stop_attr(self, stop: str = 'hi') -> Optional[str]` — The Maya attribute holding one end of this channel's colour ramp.
  - `ChannelSpec.track_color_stops(self) -> Optional[ColorStops]` *(property)* — The published ``render_effects`` keys, one per stop.
  - `ChannelSpec.attrs(self) -> Tuple[str, ...]` *(property)* — The transform attributes this channel owns: the keyable channel and,

<a id="mat_utils--render_opacity--material_mode"></a>
### `mat_utils/render_opacity/material_mode.py`

Clean up what the retired viewport "material mode" left in a scene.

- **[`class OpacityMaterialMode(pythontk.LoggingMixin)`](mayatk/mayatk/mat_utils/render_opacity/material_mode.py#L48)** — Migration for the retired viewport material mode: :meth:`remove` only.
  - `OpacityMaterialMode.remove(cls, objects, spec: Optional[ChannelSpec] = None) -> List[str]` *(class)* — Undo the old mode's work on *objects*' materials, for *spec* or every channel.

<a id="mat_utils--render_opacity--render_effects"></a>
### `mat_utils/render_opacity/render_effects.py`

- **[`class RenderEffects(pythontk.LoggingMixin)`](mayatk/mayatk/mat_utils/render_opacity/render_effects.py#L30)** — Per-object render-effect channels for engine-ready control.
  - `RenderEffects.channel_records(cls, objects=None) -> Dict[str, Dict[str, Dict]]` *(class)* — ``{long transform: {attribute: {"value", "keys"}}}`` -- every
  - `RenderEffects.apply_channel_records(cls, node: str, records: Dict[str, Dict]) -> int` *(class)* — Land :meth:`channel_records` records on *node*;
  - `RenderEffects.objects_with_visibility_keys(cls, objects) -> List` *(class)* — Return the subset of *objects* that have keyframes on visibility.
  - `RenderEffects.create(cls, objects=None, mode: str = 'attribute', delete_visibility_keys: bool = False, channel='opacity') -> Dict[str, Dict]` *(class)* — Create a channel's attribute on *objects* (or remove it).
  - `RenderEffects.ensure_connections(cls, objects=None) -> None` *(class)* — Re-establish opacity driver connections on objects that already
  - `RenderEffects.sync_visibility_from_opacity(cls, objects=None) -> None` *(class)* — Create visibility keyframes mirroring opacity animation curves.
  - `RenderEffects.key_fade(cls, objects=None, start: float = 0, end: Optional[float] = None, direction: str = 'in', auto_create: bool = True, tangent: str = 'linear', delete_visibility_keys: bool = False, channel='opacity', whole_frames: bool = True, recipe: Optional[ptk.EffectRecipe] = None) -> List[Tuple[str, str]]` *(class)* — Key a two-key ramp on a channel;
  - `RenderEffects.prepare_for_export(cls) -> List[str]` *(class)* — Stage the curve-proxy transport for an FBX write;
  - `RenderEffects.finish_export(cls) -> None` *(class)* — Undo :meth:`prepare_for_export`'s staging (the ``finish`` half of the
  - `RenderEffects.stage_export_proxies(cls) -> List[str]` *(class)* — Stage one transient child transform per keyed channel, for the FBX write.
  - `RenderEffects.remove_export_proxies(cls) -> List[str]` *(class)* — Delete every staged curve proxy (marker-matched).
  - `RenderEffects.key_pulse(cls, objects=None, start: float = 0, end: float = 100, period: Optional[float] = None, bright_fraction: Optional[float] = None, ramp_fraction: Optional[float] = None, lead_in: Optional[float] = None, lead_out: Optional[float] = None, color=None, dim_color=None, auto_create: bool = True, channel='highlight', delete_visibility_keys: bool = False, whole_frames: bool = True, recipe: Optional[ptk.EffectRecipe] = None) -> List[str]` *(class)* — Key a repeating bright/dim pulse on a channel over ``start..end``.
  - `RenderEffects.scene_store()` *(static)* — The class holding the scene's effect recipe -- the shot store.
  - `RenderEffects.scene_recipe(cls) -> ptk.EffectRecipe` *(class)* — The scene's effect recipe -- the shot store's, which the Render
  - `RenderEffects.apply_effect(cls, obj, effect: str, start: float, end: float, recipe: Optional[ptk.EffectRecipe] = None, fps: Optional[float] = None, place=None, anchor: Optional[float] = None) -> List[Tuple[str, float]]` *(class)* — Key one recipe effect on *obj*, placed in the range ``start..end``.
  - `RenderEffects.preview_channels(cls, objects, channel='highlight', keys=(), colors=None, fps: Optional[float] = None) -> Dict` *(class)* — The WebXR-push overlay that previews one effect on *objects* at *keys*.
  - `RenderEffects.selected_objects(last_only: bool = False) -> List[str]` *(static)* — The objects the Render Effects actions act on: the selection, in
  - `RenderEffects.has_channel(cls, obj, channel='opacity') -> bool` *(class)* — Whether *obj* carries *channel*'s attribute (twin of blendertk's).
  - `RenderEffects.select_objects(objects) -> List[str]` *(static)* — Make *objects* the selection without recording an undo step -- a
  - `RenderEffects.objects_with_channel(cls, channel='highlight') -> List[str]` *(class)* — Every transform in the scene carrying the channel's attribute.
  - `RenderEffects.channel_colors(cls, objects=None, channel='highlight', stop: str = 'hi') -> Dict[str, Tuple]` *(class)* — What each object's channel colour is authored as right now.
  - `RenderEffects.channel_color_stops(cls, objects=None, channel='highlight') -> Dict[str, Tuple]` *(class)* — Both ends of each object's colour ramp, high first.
  - `RenderEffects.set_channel_color(cls, objects=None, color=None, channel='highlight', stop: str = 'hi') -> List[str]` *(class)* — Restate an already-authored channel colour, leaving its keys alone.
  - `RenderEffects.visibility_tracks(cls) -> List[Dict]` *(class)* — Every keyed-visibility transform in the scene, as stepped on/off tracks.
  - `RenderEffects.export_record(cls, ctx: ptk.ExportContext) -> Optional[ptk.Record]` *(class)* — The ``render_effects`` record for this scene, or ``None`` when it
  - `RenderEffects.refresh_export_metadata(cls) -> Optional[str]` *(class)* — Republish the ``render_effects`` channel on the ``data_export`` carrier.
  - `RenderEffects.remove(cls, objects=None, mode: Optional[str] = None, channel=None) -> None` *(class)* — Remove the channel attributes, and heal the retired preview's leftovers.

<a id="mat_utils--shader_attribute_map"></a>
### `mat_utils/shader_attribute_map.py`

Logical texture channel -> per-shader (attribute, output plug), and the one

- [`LOBE_CHANNELS`](mayatk/mayatk/mat_utils/shader_attribute_map.py#L36) — constant
- **[`class ShaderAttributeMap(_ShaderAttributeMapInternal)`](mayatk/mayatk/mat_utils/shader_attribute_map.py#L129)** — Central mapping of logical texture/material channels to per-shader attribute/plug pairs.
  - `ShaderAttributeMap.texture_scale(cls, shader: str, logical: str, shader_type: Optional[str] = None) -> Optional[float]` *(class)* — Factor that brings the texels driving *logical* into the units of
  - `ShaderAttributeMap.set_thin_walled(cls, shader: str, shader_type: Optional[str] = None) -> bool` *(class)* — Make *shader* thin-walled, if its type has the switch.
  - `ShaderAttributeMap.open_lobe_gate(cls, shader: str, logical: str, shader_type: Optional[str] = None) -> bool` *(class)* — Switch on the lobe a just-wired *logical* map belongs to.
  - `ShaderAttributeMap.slot_attrs(cls, shader_type: str) -> Tuple[str, ...]` *(class)* — Every attribute a texture may drive on *shader_type*.
  - `ShaderAttributeMap.logical_channels(cls) -> Tuple[str, ...]` *(class)* — Returns the logical channel names as a tuple.
  - `ShaderAttributeMap.get_attr(cls, shader_type: str, logical: str) -> Optional[Tuple[str, str]]` *(class)* — Return (attribute, plug) tuple for shader type and logical channel, or None.
  - `ShaderAttributeMap.constant_attr(cls, shader_type: str, logical: str) -> Optional[str]` *(class)* — The attribute holding *logical*'s value on an unmapped *shader_type*.
  - `ShaderAttributeMap.emission_weight(cls, shader: str, shader_type: Optional[str] = None) -> float` *(class)* — The shader's separate emission scalar, or 1.0 when it has none.
  - `ShaderAttributeMap.open_emission_gate(cls, shader: str, shader_type: Optional[str] = None) -> bool` *(class)* — Switch on the emission weight of a shader that was just given a map.
  - `ShaderAttributeMap.base_weight(cls, shader: str, shader_type: Optional[str] = None) -> float` *(class)* — The shader's base-colour weight, or 1.0 when it has none.
  - `ShaderAttributeMap.read_constant(cls, shader: str, logical: str, shader_type: Optional[str] = None, texture_units: bool = False) -> Optional[Tuple[float, ...]]` *(class)* — *logical*'s literal on *shader*, in the channel's own terms.
  - `ShaderAttributeMap.get_mapping(cls, src_type: str, dst_type: str) -> Tuple[Tuple[str, str, str], ...]` *(class)* — Returns a tuple of (src_attr, src_plug, dst_attr) for each logical channel present in both shader t…
  - `ShaderAttributeMap.connect_channel(cls, file_node: str, logical: str, shader: str, shader_type: Optional[str] = None) -> bool` *(class)* — Wire *file_node* into *shader*'s *logical* channel as this map declares.
  - `ShaderAttributeMap.resolve_live_slot(cls, shader: str, logical: str, shader_type: Optional[str] = None) -> ShaderAttrSlot` *(class)* — The ``(attribute, plug)`` for *logical* that this NODE actually has.
  - `ShaderAttributeMap.map_toggle_attr(cls, attr: str) -> str` *(class)* — The ``use_*`` companion ShaderFX pairs with slot *attr*.
  - `ShaderAttributeMap.map_toggle_state(cls, attr: str) -> Tuple[str, int]` *(class)* — The ``use_*`` companion of slot *attr* AND the value that reads it.
  - `ShaderAttributeMap.select_color_alpha(cls, shader: str, file_node: str) -> bool` *(class)* — Read the opacity from the colour map's alpha, if *file_node* IS it.
  - `ShaderAttributeMap.add_shader_type(cls, shader_type: str, attrs: ShaderAttrs) -> None` *(class)* — Add a new shader type mapping.
  - `ShaderAttributeMap.update_attr(cls, shader_type: str, logical: str, value: Optional[Tuple[str, str]]) -> None` *(class)* — Update a logical channel mapping for a shader type.
  - `ShaderAttributeMap.as_dict(cls) -> Dict[str, Dict[str, Any]]` *(class)* — Returns a dict of dicts for all shader mappings.

<a id="mat_utils--shader_converter"></a>
### `mat_utils/shader_converter.py`

Retype a material in place — legacy Maya shaders to an exportable PBR one.

- **[`class ShaderConverter(pythontk.LoggingMixin, _ShaderConverterInternal)`](mayatk/mayatk/mat_utils/shader_converter.py#L137)** — Convert materials between shader types, preserving textures and assignments.
  - `ShaderConverter.read_channels(cls, shader: str) -> Dict[str, Dict[str, Any]]` *(class)* — What drives each logical channel of *shader*.
  - `ShaderConverter.convert(cls, materials=None, target: str = 'stingray', opacity_mode: str = None, delete_source: bool = True, name_suffix: str = '', verbose: bool = False) -> Dict[str, Optional[str]]` *(class)* — Retype *materials*, keeping their textures and geometry assignments.

<a id="mat_utils--shader_templates--_shader_templates"></a>
### `mat_utils/shader_templates/_shader_templates.py`

- **[`class GraphCollector(_ShaderTemplatesInternal)`](mayatk/mayatk/mat_utils/shader_templates/_shader_templates.py#L56)** — Walk a shading network and serialize it to placeholder-keyed graph info.
  - `GraphCollector.collect_graph(self, nodes)`
- **[`class GraphSaver(GraphCollector)`](mayatk/mayatk/mat_utils/shader_templates/_shader_templates.py#L217)**
  - `GraphSaver.save_graph(self, nodes: List[str], file_path: str, exclude_types: Optional[List[str]] = None) -> None`
- **[`class GraphRestorer(_ShaderTemplatesInternal)`](mayatk/mayatk/mat_utils/shader_templates/_shader_templates.py#L267)**
  - `GraphRestorer.load_yaml(self)` — Load and return graph configuration from a YAML file.
  - `GraphRestorer.restore_graph(self)` — Restore the graph based on the YAML configuration and textures.
  - `GraphRestorer.restore_connections(self)` — Connect nodes as specified in the graph configuration.
- **[`class ShaderTemplates`](mayatk/mayatk/mat_utils/shader_templates/_shader_templates.py#L531)** — Facade class for managing shader templates.
  - `ShaderTemplates.store(cls) -> 'ptk.PresetStore'` *(class)* — The template store: shipped templates read-only, the user's in
  - `ShaderTemplates.migrate_package_templates(cls, store: 'ptk.PresetStore') -> List[str]` *(class)* — Move user templates out of *store*'s built-in folder into its user
  - `ShaderTemplates.suggested_name(nodes) -> str` *(static)* — The name Save Template offers for *nodes*: the first node's leaf name,
  - `ShaderTemplates.save_template(nodes, file_path, exclude_types=None, logger=None)` *(static)* — Save the specified nodes as a shader template.
  - `ShaderTemplates.restore_template(cls, file_path, texture_paths=None, name=None, logger=None)` *(class)* — Restore a shader template from a file.

<a id="mat_utils--substance_bridge--_substance_bridge"></a>
### `mat_utils/substance_bridge/_substance_bridge.py`

Substance 3D Painter bridge -- export Maya selection and hand off to Painter.

- [`SEND_TO`](mayatk/mayatk/mat_utils/substance_bridge/_substance_bridge.py#L60) — constant
- [`ROUND_TRIP`](mayatk/mayatk/mat_utils/substance_bridge/_substance_bridge.py#L61) — constant
- [`TARGET_AUTO`](mayatk/mayatk/mat_utils/substance_bridge/_substance_bridge.py#L62) — constant
- [`TARGET_NEW`](mayatk/mayatk/mat_utils/substance_bridge/_substance_bridge.py#L63) — constant
- [`TARGET_CURRENT`](mayatk/mayatk/mat_utils/substance_bridge/_substance_bridge.py#L64) — constant
- **[`class SubstanceBridge(SubstanceEngine)`](mayatk/mayatk/mat_utils/substance_bridge/_substance_bridge.py#L112)** — Export Maya selection to Substance Painter via a chosen template.
  - `SubstanceBridge.params_registry(cls)` *(class)* — The panel's parameter registry (``parameters.Parameters``, a
  - `SubstanceBridge.send(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, output_name: Optional[str] = None, painter_exe: Optional[str] = None, fbx_options: Optional[Dict[str, Any]] = None, preset_file: Optional[str] = None, template: str = 'import', mode: str = SEND_TO, target: Union[str, int] = TARGET_AUTO, params: Optional[Dict[str, Any]] = None, **legacy_kwargs: Any) -> Optional[Dict[str, Any]]` — Export *objects*, render *template* in *mode*, hand off to Painter.
  - `SubstanceBridge.source_model_path_for(cls, fbx_path: str) -> str` *(class)* — ``.../asset.fbx`` -> ``.../asset_source.fbx``.

<a id="mat_utils--substance_bridge--_substance_engine"></a>
### `mat_utils/substance_bridge/_substance_engine.py`

Drive Substance 3D Painter from the outside -- the DCC-free half of the bridge.

- [`SEND_TO`](mayatk/mayatk/mat_utils/substance_bridge/_substance_engine.py#L61) — constant
- [`ROUND_TRIP`](mayatk/mayatk/mat_utils/substance_bridge/_substance_engine.py#L62) — constant
- [`TARGET_AUTO`](mayatk/mayatk/mat_utils/substance_bridge/_substance_engine.py#L74) — constant
- [`TARGET_NEW`](mayatk/mayatk/mat_utils/substance_bridge/_substance_engine.py#L75) — constant
- [`TARGET_CURRENT`](mayatk/mayatk/mat_utils/substance_bridge/_substance_engine.py#L76) — constant
- **[`class SubstanceEngine(pythontk.HandoffBridge)`](mayatk/mayatk/mat_utils/substance_bridge/_substance_engine.py#L143)** — The DCC-free Substance Painter hand-off;
  - `SubstanceEngine.painter_path(self) -> Optional[str]` *(property)* — Resolve the Painter executable path via :func:`find_painter_exe`.
  - `SubstanceEngine.painter_log_path(self) -> Optional[str]` *(property)* — Path to Painter's application ``log.txt``, or *None* if absent.
  - `SubstanceEngine.instances(self) -> List[SubstanceConnection]` *(property)* — Live snapshot of managed connections (oldest -> newest, dead pruned).
  - `SubstanceEngine.find_live_managed(self) -> Optional[SubstanceConnection]` — Return the most-recently-launched managed instance whose RPC pings.
  - `SubstanceEngine.ensure_rpc_plugin(self) -> None` — Install -- or refresh -- the Painter-side substance_rpc plugin.
  - `SubstanceEngine.mesh_map_files(cls, paths: List[str]) -> List[str]` *(class)* — The subset of *paths* Painter can actually use as mesh maps.
  - `SubstanceEngine.list_templates() -> List[Path]` *(static)* — Return user-visible templates in ``templates/`` (skips underscore-prefixed).
  - `SubstanceEngine.parse_template(template_path: Path) -> Dict[str, Any]` *(static)* — Read a template's metadata constants without executing the file.
  - `SubstanceEngine.list_template_modes() -> List[Tuple[str, str]]` *(static)* — Return ``[(stem, mode), ...]`` for every (template, mode) pairing.
  - `SubstanceEngine.resolve_painter_log_path(painter_exe: Optional[str] = None) -> Optional[str]` *(static)* — Return the path to Painter's application log.

<a id="mat_utils--substance_bridge--connection"></a>
### `mat_utils/substance_bridge/connection.py`

Substance 3D Painter connection module.

- [`APP`](mayatk/mayatk/mat_utils/substance_bridge/connection.py#L55) — constant
- **[`class SubstanceConnection(pythontk.LoggingMixin)`](mayatk/mayatk/mat_utils/substance_bridge/connection.py#L75)** — Launch Painter and expose its stdio, log, and RPC under one object.
  - `SubstanceConnection.open(self) -> 'SubstanceConnection'` — Launch Painter and start readers, tailer, and RPC client.
  - `SubstanceConnection.close(self, terminate: bool = False, timeout: float = 5.0) -> None` — Stop readers and tailer;
  - `SubstanceConnection.is_alive(self) -> bool` — True if Painter is reachable through this connection.
  - `SubstanceConnection.attach(cls, port: int, host: str = '127.0.0.1', log_path: Optional[str] = None, tail_log_from_start: bool = False, verify_alive: bool = True, verify_timeout: float = 2.0) -> 'SubstanceConnection'` *(class)* — Bind to a running Painter on *port* without launching anything.
  - `SubstanceConnection.find_painter_exe() -> Optional[str]` *(static)* — Single source of truth for Painter executable discovery.
  - `SubstanceConnection.default_log_path() -> Optional[str]` *(static)* — Return the standard Substance Painter log path, or None if absent.

<a id="mat_utils--substance_bridge--parameters"></a>
### `mat_utils/substance_bridge/parameters.py`

Registry of user-tunable Substance Painter parameters exposed to the bridge UI.

- [`PARAMS`](mayatk/mayatk/mat_utils/substance_bridge/parameters.py#L86) — constant
- **[`class Parameters(ParamRegistry)`](mayatk/mayatk/mat_utils/substance_bridge/parameters.py#L266)** — Parameters — module namespace.
  - `Parameters.render_cli_context(cls, values: 'dict[str, Any]') -> 'dict[str, str]'` *(class)* — Format *values* for ``LAUNCH_ARGS`` -- raw, no quoting.
  - `Parameters.render_js_context(cls, values: 'dict[str, Any]') -> 'dict[str, str]'` *(class)* — Format *values* for ``RPC_SCRIPT`` -- JS-literal quoting/escaping.

<a id="mat_utils--substance_bridge--substance_rpc--client"></a>
### `mat_utils/substance_bridge/substance_rpc/client.py`

HTTP RPC client for the Painter-side ``substance_rpc`` plugin.

- [`DEFAULT_RPC_PORT`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/client.py#L37) — constant
- **[`class PainterRpcClient(RpcClient)`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/client.py#L40)** — RPC client bound to the substance_rpc plugin's defaults.
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

- [`PLUGIN_NAME`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/installer.py#L33) — constant
- **[`class Installer(_InstallerInternal)`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/installer.py#L110)** — Installer — module namespace.
  - `Installer.user_plugin_dir() -> Optional[Path]` *(static)* — Resolve Painter's Python plugins folder.
  - `Installer.is_installed() -> bool` *(static)* — True if the plugin is present at the resolved user plugin dir.
  - `Installer.is_current() -> bool` *(static)* — True if the installed plugin matches the one this package ships.
  - `Installer.install(force: bool = False) -> Optional[Path]` *(static)* — Install the plugin into Painter's user plugin folder.
  - `Installer.uninstall() -> bool` *(static)* — Remove the plugin from the user plugin folder.

<a id="mat_utils--substance_bridge--substance_rpc--plugin_src--substance_rpc--__init__"></a>
### `mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/__init__.py`

Substance 3D Painter RPC plugin -- entry point.

- [`start_server(port=None, host=None)`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/__init__.py#L54) — Start the RPC server (idempotent).
- [`stop_server()`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/__init__.py#L59) — Shut the server down (close_plugin hook / tests / hot-reload).
- [`is_running()`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/__init__.py#L64) — True while the server is bound.
- [`autostart()`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/__init__.py#L69) — Start on plugin load, gated to the Painter host.
- [`start_plugin()`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/__init__.py#L74) — Painter lifecycle hook: start the RPC server (idempotent).
- [`close_plugin()`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/__init__.py#L81) — Painter lifecycle hook: shut the RPC server down.
- [`PLUGIN`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/__init__.py#L34) — constant

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
  - `MainThreadMarshaller.post(self, fn, *args, **kwargs)` — Queue *fn* for the main thread and return at once -- never blocks.
  - `MainThreadMarshaller.run(self, fn, *args, timeout=None, **kwargs)` — Call *fn*, on the main thread when one is reachable.
- **[`class RpcPlugin(object)`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/_rpc_core.py#L451)** — One host plugin: a registry, a marshaller, and the server that joins them.
  - `RpcPlugin.import_ops(package)` *(static)* — Import *package* (dotted name), forcing its ``@register`` side effects.
  - `RpcPlugin.port(self)` *(property)* — Configured port: ``<PREFIX>_PORT`` if set and numeric, else the default.
  - `RpcPlugin.is_hosted(self)` — True only inside the real host application.
  - `RpcPlugin.is_running(self)` — True while the HTTP server is bound.
  - `RpcPlugin.address(self)` *(property)* — The bound ``(host, port)``, or ``None`` when not running.
  - `RpcPlugin.start(self, port=None, host=None)` — Bind and serve on a daemon thread.
  - `RpcPlugin.serve(self, port=None, host=None)` — Bind and serve on the CALLING thread until :meth:`stop`;
  - `RpcPlugin.stop(self)` — Shut the server down (host teardown hook, tests, hot-reload, an op).
  - `RpcPlugin.autostart(self)` — Start on plugin load, but only when actually hosted.
  - `RpcPlugin.autostart_safely(self)` — :meth:`autostart`, but a failure is logged instead of raised.

<a id="mat_utils--substance_bridge--substance_rpc--plugin_src--substance_rpc--ops--project_ops"></a>
### `mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/project_ops.py`

Project-level ops: inspect the open project and reload its mesh.

- [`project_info()`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/project_ops.py#L28) — Return ``{is_open, file_path, mesh_path, needs_saving}`` (best-effort).
- [`mesh_reload(mesh_path='', preserve_strokes=True, import_cameras=False)`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/project_ops.py#L61) — Reload the open project's mesh from *mesh_path* (async).
- [`mesh_reload_status()`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/project_ops.py#L103) — Outcome of the last ``mesh.reload``: pending / success / failure.

<a id="mat_utils--substance_bridge--substance_rpc--plugin_src--substance_rpc--ops--setup_ops"></a>
### `mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/setup_ops.py`

Project-setup ops: resolution, the baking high poly, and mesh maps.

- [`teardown()`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/setup_ops.py#L84) — Drop any pending values and unsubscribe (plugin-disable hook).
- [`set_resolution(size=0)`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/setup_ops.py#L169) — Set the document resolution of every texture set to *size* px square.
- [`set_high_poly(mesh_path='')`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/setup_ops.py#L232) — Set the Hipoly Mesh of every texture set's baking parameters.
- [`apply_mesh_maps(manifest_path='')`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/setup_ops.py#L363) — Wire each material's baked maps onto its own texture set.
- [`pending_setup()`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/setup_ops.py#L390) — Return what is queued for the next project-open (diagnostics).

<a id="mat_utils--substance_bridge--substance_rpc--plugin_src--substance_rpc--ops--system_ops"></a>
### `mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/system_ops.py`

Painter-specific system ops: version reporting and script evaluation.

- [`version()`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/system_ops.py#L19) — Return Painter + plugin API version info (best-effort).
- [`eval_python(script='')`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/system_ops.py#L39) — Exec *script* (Python source) inside Painter's interpreter.
- [`js_evaluate(script='')`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/system_ops.py#L61) — Evaluate *script* in Painter's JavaScript engine (``alg.*`` API).

<a id="mat_utils--substance_bridge--templates--bake_lighting"></a>
### `mat_utils/substance_bridge/templates/bake_lighting.py`

Import the FBX as a new project, then bake Iray lighting into diffuse.

- [`BRIDGE_MODES`](mayatk/mayatk/mat_utils/substance_bridge/templates/bake_lighting.py#L52) — constant
- [`LAUNCH_ARGS`](mayatk/mayatk/mat_utils/substance_bridge/templates/bake_lighting.py#L66) — constant
- [`RPC_SCRIPT`](mayatk/mayatk/mat_utils/substance_bridge/templates/bake_lighting.py#L76) — constant
- [`BUILD_MANIFEST`](mayatk/mayatk/mat_utils/substance_bridge/templates/bake_lighting.py#L103) — constant
- [`FBX_OPTIONS`](mayatk/mayatk/mat_utils/substance_bridge/templates/bake_lighting.py#L108) — constant
- [`TARGET_INSTANCE`](mayatk/mayatk/mat_utils/substance_bridge/templates/bake_lighting.py#L113) — constant

<a id="mat_utils--substance_bridge--templates--import"></a>
### `mat_utils/substance_bridge/templates/import.py`

Send the FBX to Painter as a new project.

- [`BRIDGE_MODES`](mayatk/mayatk/mat_utils/substance_bridge/templates/import.py#L14) — constant
- [`LAUNCH_ARGS`](mayatk/mayatk/mat_utils/substance_bridge/templates/import.py#L47) — constant
- [`RPC_SCRIPT`](mayatk/mayatk/mat_utils/substance_bridge/templates/import.py#L53) — constant
- [`BUILD_MANIFEST`](mayatk/mayatk/mat_utils/substance_bridge/templates/import.py#L60) — constant
- [`FBX_OPTIONS`](mayatk/mayatk/mat_utils/substance_bridge/templates/import.py#L64) — constant
- [`TARGET_INSTANCE`](mayatk/mayatk/mat_utils/substance_bridge/templates/import.py#L70) — constant

<a id="mat_utils--substance_bridge--templates--reimport"></a>
### `mat_utils/substance_bridge/templates/reimport.py`

Update the running Painter's open project from a fresh FBX export.

- [`BRIDGE_MODES`](mayatk/mayatk/mat_utils/substance_bridge/templates/reimport.py#L29) — constant
- [`LAUNCH_ARGS`](mayatk/mayatk/mat_utils/substance_bridge/templates/reimport.py#L45) — constant
- [`RPC_OPS`](mayatk/mayatk/mat_utils/substance_bridge/templates/reimport.py#L50) — constant
- [`RPC_SCRIPT`](mayatk/mayatk/mat_utils/substance_bridge/templates/reimport.py#L61) — constant
- [`BUILD_MANIFEST`](mayatk/mayatk/mat_utils/substance_bridge/templates/reimport.py#L63) — constant
- [`REUSE_RECORDED_EXPORT`](mayatk/mayatk/mat_utils/substance_bridge/templates/reimport.py#L68) — constant
- [`TARGET_INSTANCE`](mayatk/mayatk/mat_utils/substance_bridge/templates/reimport.py#L74) — constant
- [`NO_CONNECTION_HINT`](mayatk/mayatk/mat_utils/substance_bridge/templates/reimport.py#L76) — constant

<a id="mat_utils--substance_bridge--templates--render"></a>
### `mat_utils/substance_bridge/templates/render.py`

Render the current Painter project via Iray (JS body unverified).

- [`BRIDGE_MODES`](mayatk/mayatk/mat_utils/substance_bridge/templates/render.py#L25) — constant
- [`LAUNCH_ARGS`](mayatk/mayatk/mat_utils/substance_bridge/templates/render.py#L28) — constant
- [`EXPORT_FBX`](mayatk/mayatk/mat_utils/substance_bridge/templates/render.py#L33) — constant
- [`FBX_OPTIONS`](mayatk/mayatk/mat_utils/substance_bridge/templates/render.py#L34) — constant
- [`RPC_SCRIPT`](mayatk/mayatk/mat_utils/substance_bridge/templates/render.py#L51) — constant
- [`BUILD_MANIFEST`](mayatk/mayatk/mat_utils/substance_bridge/templates/render.py#L60) — constant
- [`TARGET_INSTANCE`](mayatk/mayatk/mat_utils/substance_bridge/templates/render.py#L63) — constant

<a id="mat_utils--texture_baker"></a>
### `mat_utils/texture_baker.py`

Bake an object's shaded surface (material under scene lighting) to a texture.

- **[`class TextureBaker(pythontk.LoggingMixin)`](mayatk/mayatk/mat_utils/texture_baker.py#L67)** — Bake scene lighting per object to a texture file (PNG, EXR, ...).
  - `TextureBaker.arnold_available() -> bool` *(static)* — True if the ``mtoa`` plugin is loaded AND its bake cmd is registered.
  - `TextureBaker.ensure_arnold(cls) -> bool` *(class)* — Load mtoa if it isn't loaded, then answer :meth:`arnold_available`.
  - `TextureBaker.render_panorama(self, position: Sequence[float], path: str, width: int = 1024, hide: Optional[Sequence[str]] = None, lights: Optional[Sequence[str]] = None) -> Optional[str]` — Render an equirectangular HDR of the scene seen from *position* to *path*.
  - `TextureBaker.default_output_dir(subdir: str = 'baked') -> str` *(static)* — Where a bake writes when no ``output_dir`` is given: *subdir* in the
  - `TextureBaker.resolve_meshes(objects=None, descendants: bool = False) -> List[str]` *(static)* — Normalize *objects* (names / components / ``None`` = selection) to mesh transforms.
  - `TextureBaker.bake(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, prefix: str = 'bake_', suffix: str = '', backend: str = 'auto', uv_set: Optional[Union[str, Dict[str, str]]] = None, on_progress: Optional[Callable[[int, int, str], bool]] = None, stem: Optional[Union[Callable[[str], str], Dict[str, str]]] = None, size: Optional[Any] = None, shader: Optional[str] = None, batch: bool = False, claims: Optional[Any] = None, region: Optional[Any] = None, camera_shader: Optional[str] = None) -> Dict[str, str]` — Bake lighting per object to texture files (EXR on Arnold).
  - `TextureBaker.arnold_translation_guard(self)` — Bridge untranslatable (game/ShaderFX) materials for the bake.
  - `TextureBaker.gpu_available() -> bool` *(static)* — True when Arnold has a GPU it can render on in this session.
  - `TextureBaker.assign_to_diffuse(self, mapping: Dict[str, str]) -> None` — Wire each baked PNG into the object's material color slot.
  - `TextureBaker.restore_diffuse_connections(self) -> None` — Undo :meth:`assign_to_diffuse` -- reconnects previous drivers.

<a id="mat_utils--texture_path_editor"></a>
### `mat_utils/texture_path_editor.py`

Texture Path Editor engine: the scene's texture paths and the commands that fix them.

- **[`class TexturePathEditor`](mayatk/mayatk/mat_utils/texture_path_editor.py#L43)** — The Texture Path Editor's scene half: file nodes, their paths, and the
  - `TexturePathEditor.texture_dir(self) -> str` — The project's texture folder (the ``sourceImages`` rule), ``""`` if none.
  - `TexturePathEditor.resolve_path(self, path: str, file_node=None)` — ``(exists, absolute path)`` for a stored or typed *path*.
  - `TexturePathEditor.is_absolute_path(path: str) -> bool` *(static)* — Whether *path* is stored absolute -- a Normalize Paths candidate.
  - `TexturePathEditor.rows(self, exclude_arnold: bool = False)` — One row per (shader, file node): what the editor's table lists.
  - `TexturePathEditor.file_nodes(self, exclude_arnold: bool = False)` — Every file node in the scene -- the "all" scope of a path command.
  - `TexturePathEditor.node_exists(node) -> bool` *(static)* — Whether *node* (a file node or shader name) is still in the scene.
  - `TexturePathEditor.stored_path(node) -> str` *(static)* — The node's stored ``.ftn``, or "" when the plug cannot be read.
  - `TexturePathEditor.selected_file_nodes(self)` — The file nodes the scene selection's materials read.
  - `TexturePathEditor.reload_textures() -> None` *(static)* — Force Maya to re-read every scene texture from disk.
  - `TexturePathEditor.set_texture_directory(self, file_nodes, target_dir: str, relocate_mode: str = 'rewrite', allow_missing: bool = False) -> int` — Repath each file node so its texture lives directly under *target_dir*.
  - `TexturePathEditor.make_paths_absolute(self, file_nodes) -> int` — Rewrite relative texture paths to absolute -- inverse of Normalize Paths.
  - `TexturePathEditor.normalize_paths(self, file_nodes, external_mode: str = 'rewrite') -> int` — Rewrite (selected) paths inside the project to relative.
  - `TexturePathEditor.resolve_missing_textures(self, modes, file_nodes=None) -> int` — Rebind missing textures to files found under sourceimages.
  - `TexturePathEditor.unresolved_file_nodes(self, file_nodes)` — The nodes whose stored path does not resolve -- what a Find & Copy
  - `TexturePathEditor.find_and_copy_textures(self, file_nodes, answers, lightmaps=(), dry_run=False, progress=None) -> bool` — Collect sources, relocate them into one directory, repath the nodes.
  - `TexturePathEditor.set_texture_path(self, file_node, path: str, relative: bool = False) -> str` — Point *file_node* at *path* -- Browse for File, or a typed path.
  - `TexturePathEditor.is_file_rename(self, stored: str, typed: str, file_node=None) -> bool` — Whether a typed path renames *stored*'s file rather than repointing.
  - `TexturePathEditor.apply_path_edit(self, file_node, typed: str, shader=None, sync=None) -> str` — A path typed into *file_node*'s row: rename its file, or repoint it.
  - `TexturePathEditor.rename_texture(self, context, new_name: str, sync=None) -> bool` — Rename the texture file *context* names to *new_name*.
  - `TexturePathEditor.can_rename_shader(self, shader) -> bool` — Whether a shader cell may rename *shader* (any shader in the scene).
  - `TexturePathEditor.rename_shader(self, shader, new_name: str, sync=None) -> str` — Rename *shader*;
  - `TexturePathEditor.rename_file_node(self, file_node, new_name: str, shader=None, sync=None) -> str` — Rename *file_node*;
  - `TexturePathEditor.select_objects(self, shaders=(), lightmaps=()) -> int` — Select the objects wearing *shaders* and those *lightmaps* bind.
  - `TexturePathEditor.select_file_nodes(self, file_nodes) -> int` — Select *file_nodes* (replacing the selection);
  - `TexturePathEditor.graph_file_nodes(file_nodes) -> None` *(static)* — Graph *file_nodes* in the Hypershade.
  - `TexturePathEditor.delete_consequences(self, file_nodes)` — What deleting *file_nodes* would unwire, as sentences for a confirm.
  - `TexturePathEditor.delete_file_nodes(self, file_nodes) -> int` — Delete *file_nodes* (one undo step);
  - `TexturePathEditor.lightmap_dependencies(self)` — The scene's lightmap dependency records (``LightmapRecords``):
  - `TexturePathEditor.lightmap_labels(self, dep)` — ``(shader, node)`` labels for a lightmap row, read like a texture row.
  - `TexturePathEditor.normalize_lightmaps(self, lightmaps, relative: bool) -> int` — Re-spell the scoped lightmap markers' folders (Normalize / Make Absolute).
  - `TexturePathEditor.repath_lightmap(self, dep, folder: str) -> bool` — Point every marker naming *dep*'s map at *folder*.
  - `TexturePathEditor.rename_lightmap(self, dep, new_name: str) -> None` — Rename a lightmap's file and re-stamp the markers that bind it by name.
  - `TexturePathEditor.lightmap_revert_scope(self, deps)` — ``(objects, kept)``: the objects reverting *deps* would clear, and those
  - `TexturePathEditor.revert_lightmaps(self, objects)` — The Lightmap Baker's Revert Selected on *objects*: their markers are

<a id="node_utils--_node_utils"></a>
### `node_utils/_node_utils.py`

- **[`class NodeUtils(pythontk.HelpMixin)`](mayatk/mayatk/node_utils/_node_utils.py#L37)**
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
  - `NodeUtils.get_constraint_targets(constraint: str) -> list` *(static)* — The transforms a constraint FOLLOWS -- its ``targetList``.
  - `NodeUtils.get_groups(cls, empty=False)` *(class)* — Get all groups in the scene.
  - `NodeUtils.get_parent(node, all=False, full_path=False, type='transform')` *(static)* — Return the parent of *node*.
  - `NodeUtils.get_children(node, type='transform', full_path=False)` *(static)* — List the children of *node*.
  - `NodeUtils.get_shapes(cls, node, no_intermediate=True, full_path=True, descend=False, type=None)` *(class)* — Return the shape(s) associated with *node* -- flexible about input.
  - `NodeUtils.get_shape(cls, node, no_intermediate=True, full_path=True, descend=False)` *(class)* — Return the first shape for a transform / shape / component, or ``None``.
  - `NodeUtils.is_intermediate(shape)` *(static)* — Return True if *shape* is an intermediate (orig) shape.
  - `NodeUtils.node_is(node, type_name)` *(static)* — Return True if ``cmds.objectType(node)`` matches *type_name* exactly.
  - `NodeUtils.list_transforms(objects=None, **ls_kwargs)` *(static)* — Transforms whose shapes match the given ``cmds.ls`` criteria.
  - `NodeUtils.expand_sets(objects: Union[str, object, List[Union[str, object]]]) -> List[str]` *(static)* — *objects* with every object set replaced by its members.
  - `NodeUtils.get_unique_children(cls, objects)` *(class)* — Retrieves a unique list of objects' children (if any) in the scene, excluding the groups themselves.
  - `NodeUtils.get_transform_node(nodes, returned_type='obj', attributes=False, inc=[], exc=[])` *(static)* — Get transform node(s) or node attributes.
  - `NodeUtils.get_shape_node(cls, nodes, returned_type='obj', attributes=False, inc=[], exc=[], no_intermediate=True)` *(class)* — Get shape node(s) or node attributes.
  - `NodeUtils.get_history_node(nodes, returned_type='obj', attributes=False, inc=[], exc=[])` *(static)* — Get history node(s) or node attributes.
  - `NodeUtils.get_deformers(cls, obj) -> List[str]` *(class)* — Deformers driving *obj* (skinCluster, blendShape, lattice, …).
  - `NodeUtils.get_input_shape(cls, mesh) -> Optional[str]` *(class)* — The INPUT (intermediate) shape of a deformed mesh, else its renderable one.
  - `NodeUtils.delete_history(cls, objects, preserve_deformers: bool = True) -> List[str]` *(class)* — Delete construction history — keeping the deformer stack by default.
  - `NodeUtils.delete_history_in_scope(cls, scope: str = 'selected', preserve_deformers: bool = True, delete_unused: bool = False, optimize_scene: bool = False) -> List[str]` *(class)* — Delete History on the objects *scope* names, with the viewport held.
  - `NodeUtils.lock_nodes(objects=None, lock: bool = True) -> List[str]` *(static)* — Lock nodes against deletion, renaming and reparenting (``lockNode``), or
  - `NodeUtils.bake_onto_input_shape(cls, target, transfer_nodes, capture, apply, label: str = 'transfer') -> bool` *(class)* — Move a just-sampled result off the live shape onto the input shape,
  - `NodeUtils.static_copy(cls, obj, name: Optional[str] = None, strip_children: bool = True) -> str` *(class)* — A throwaway copy of *obj* that shares NOTHING with its source.
  - `NodeUtils.deformers_preserved(cls, objects, label: str = '')` *(class)* — Fail loudly if the block unbinds or re-topologizes *objects*.
  - `NodeUtils.get_classification_tokens(node_type: str) -> List[str]` *(static)* — Role classifications of *node_type* — ``shader/surface``, ``utility/math``, …
  - `NodeUtils.create_render_node(cls, node_type, classification=None, category=None, name=None, create_placement_nodes=False, create_shading_group=True, **attributes)` *(class)* — Creates a Maya node of a specified type with enhanced control over the creation process.
  - `NodeUtils.incoming_connections(sources: List[str]) -> List[Tuple[str, str]]` *(static)* — ``(destination plug, source plug)`` for every incoming wire on *sources*.
  - `NodeUtils.get_connected_nodes(node, node_type=None, direction=None, exact=True, first_match=False)` *(static)* — Finds connected nodes of a given type and direction (incoming/outgoing).
  - `NodeUtils.create_assembly(nodes, assembly_name='assembly#', duplicate=False)` *(static)* — Create an assembly by parenting the input nodes to a new assembly node.
  - `NodeUtils.selection_summary(limit: int = 10) -> Optional[Dict[str, Any]]` *(static)* — What is selected, as plain values: what a status readout (tentacle's
  - `NodeUtils.get_instances(objects=None, return_parent_objects=False)` *(static)* — Get any instances of given object, or if None given, get all instanced objects in the scene.
  - `NodeUtils.select_instances(cls, objects=None, whole_scene: bool = False) -> List[str]` *(class)* — Select *objects* together with their instances, or every instanced
  - `NodeUtils.replace_with_instances(cls, objects=None, append='', freeze_transforms=False, center_pivot=True, delete_history=True, retain_bbox_scale=False, retain_bbox_per_axis=False)` *(class)* — Replace target objects with instances of the source object.
  - `NodeUtils.get_instanced_shapes(cls, node, intermediate: bool = True, descendants: bool = False) -> List[str]` *(class)* — Every shape under *node* that is shared with another transform.
  - `NodeUtils.uninstance(cls, objects=None, freeze=False, delete_history=False, quiet=True)` *(class)* — Un-Instance the given objects (``None``: the selection, ``[]`` with
  - `NodeUtils.preserve_instancing(cls, objects, delete_history: bool = False, quiet: bool = True)` *(class)* — Run a shape-editing operation on instanced objects without dragging their siblings along.
  - `NodeUtils.filter_duplicate_instances(nodes) -> List[str]` *(static)* — Keep only one transform per instance group.

<a id="node_utils--attributes--_attributes"></a>
### `node_utils/attributes/_attributes.py`

Consolidated attribute utilities for Maya.

- **[`class AttributeTemplate`](mayatk/mayatk/node_utils/attributes/_attributes.py#L36)** — Defines the configuration for a Maya attribute.
- **[`class Preset(NamedTuple)`](mayatk/mayatk/node_utils/attributes/_attributes.py#L63)** — A named bundle of attributes loaded from a YAML template.
- **[`class Attributes(pythontk.HelpMixin)`](mayatk/mayatk/node_utils/attributes/_attributes.py#L78)** — Consolidated utility for managing Maya node attributes.
  - `Attributes.has_attr(node: str, attr: str) -> bool` *(static)* — Return True if *attr* exists on *node*.
  - `Attributes.set_plug_literal(plug: str, value: str) -> None` *(static)* — Write *value* to a string *plug* VERBATIM, bypassing DG expansion.
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
  - `Attributes.classify_driver(cls, node: str, node_type: Optional[str] = None, passthrough_types: Optional[Set[str]] = None, *, is_constraint: Optional[bool] = None, is_driven: Optional[bool] = None, visited: Optional[set] = None) -> Tuple[Optional[str], Optional[str]]` *(class)* — ``(driver_node, driver_type)`` for a source NODE.
  - `Attributes.trace_upstream(cls, plug: str, passthrough_types: Optional[set] = None, visited: Optional[set] = None) -> Tuple[Optional[str], Optional[str]]` *(class)* — Trace upstream through passthrough nodes to find the true driver.
  - `Attributes.upstream_anim_curves(cls, plug: str, passthrough_types: Optional[Set[str]] = None, plug_precise: bool = True, depth: Optional[int] = None) -> List[str]` *(class)* — Return the animCurves feeding *plug*, walking source connections.
  - `Attributes.get_lock_state(cls, objects, unlock: bool = False) -> Dict[str, Dict[str, Any]]` *(class)* — Return lock state for standard transform attributes.
  - `Attributes.set_lock_state(cls, objects, lock_state: Optional[Dict[str, Dict[str, bool]]] = None, translate: Optional[bool] = None, rotate: Optional[bool] = None, scale: Optional[bool] = None, **kwargs) -> None` *(class)* — Restore lock state from a saved dict, or bulk lock/unlock.
  - `Attributes.pinned(cls, node: str, _logger=None, **attrs)` *(class)* — Context manager: set *attrs* on *node* for the block, restore on exit.
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

<a id="node_utils--attributes--channels--_channels"></a>
### `node_utils/attributes/channels/_channels.py`

Channels — Maya attribute query / mutation logic.

- **[`class Channels`](mayatk/mayatk/node_utils/attributes/channels/_channels.py#L23)** — Maya attribute query / mutation logic.
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
  - `Channels.is_off_curve(plug, tolerance=1e-06)` *(static)* — Return ``True`` if keyed *plug* holds a value its curve does not.
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
  - `Channels.set_key_at_current_time(nodes, attr_name, keyed=True)` *(static)* — Set (``keyed=True``) or remove (``keyed=False``) the key on
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
  - `Channels.node_name(node, full=False)` *(static)* — *node*'s display name: its leaf name, or with *full* its long path.
  - `Channels.get_custom_attrs(node)` *(static)* — *node*'s user-defined attribute names (``[]`` when it has none or is gone).
  - `Channels.is_locked(node, attr_name)` *(static)* — Whether *attr_name* is locked on *node* (``False`` when it is not there).
  - `Channels.select_nodes(nodes)` *(static)* — Replace the selection with *nodes* (clear it for none);
  - `Channels.get_numeric_values(nodes, attr_name)` *(static)* — ``{node: value}`` for the *nodes* whose *attr_name* exists, is unlocked
  - `Channels.set_numeric_values(values, attr_name)` *(static)* — Set *attr_name* on each node of *values* (``{node: value}``) as one
  - `Channels.defer(fn)` *(static)* — Run *fn* once Maya is idle (``evalDeferred``);
  - `Channels.open_editor(cls, key)` *(class)* — Open the :attr:`EDITORS` editor *key*;
  - `Channels.watch_scene(cls, owner, on_rebuild, on_values, widget=None)` *(class)* — Call a panel back on the scene events that change what it shows.
  - `Channels.watch_nodes(cls, owner, nodes, on_rebuild, on_attr, on_values)` *(class)* — Call a panel back on changes to *nodes* (redo it per selection).
  - `Channels.unwatch(owner=None, tokens=())` *(static)* — End subscriptions: the *tokens* given, and every one *owner* holds.
  - `Channels.link_channel_box(self, on_selection_changed, selected_rows)` — Keep a panel's channel selection and Maya's channel box in step.
  - `Channels.unlink_channel_box(self)` — Undo :meth:`link_channel_box` (a no-op when not linked).
  - `Channels.channel_box_selection(self)` — The long names of the attributes selected in the channel box.
  - `Channels.select_in_channel_box(attr_names)` *(static)* — Select *attr_names* in the channel box (best effort: it may be hidden).

<a id="node_utils--data_nodes"></a>
### `node_utils/data_nodes.py`

The Maya scene store: two carrier nodes behind ``ptk.SceneStoreBase``.

- **[`class DataNodes(pythontk.SceneStoreBase)`](mayatk/mayatk/node_utils/data_nodes.py#L47)** — The two shared scene data nodes, as a ``ptk.SceneStoreBase``.
  - `DataNodes.ensure_internal(cls) -> str` *(class)* — Get or create the shared network node.
  - `DataNodes.ensure_export(cls) -> str` *(class)* — Get or create the shared FBX export transform.
  - `DataNodes.get_internal_node(cls, create: bool = True) -> Optional[str]` *(class)* — The ``data_internal`` node (created when *create*), else ``None``.
  - `DataNodes.get_export_node(cls, create: bool = True) -> Optional[str]` *(class)* — The ``data_export`` node (created when *create*), else ``None``.
  - `DataNodes.get_export_nodes(cls) -> List[str]` *(class)* — Every ``data_export`` carrier in the scene, canonical first (long paths).
  - `DataNodes.read(cls, scope: ptk.Scope, key: str) -> Optional[str]` *(class)* — The string channel *key* in *scope*, or ``None`` when the carrier,
  - `DataNodes.write(cls, scope: ptk.Scope, key: str, text: Optional[str]) -> Optional[str]` *(class)* — Store *text* on *key* in *scope*.
  - `DataNodes.remove(cls, scope: ptk.Scope, key: str) -> bool` *(class)* — Delete channel *key* from *scope*'s carrier outright -- the
  - `DataNodes.values(cls, scope: ptk.Scope) -> Dict[str, object]` *(class)* — Every user-defined attribute value on the carrier of *scope* --
  - `DataNodes.dump_export_nodes(cls, decode: bool = True) -> Dict[str, Dict[str, object]]` *(class)* — Every ``data_export`` carrier's channels, keyed by node (long path).
  - `DataNodes.carriers_in(cls, namespace: str) -> Dict[ptk.Scope, str]` *(class)* — The carriers a referenced module keeps under *namespace*
  - `DataNodes.discard_if_empty(cls, scope: ptk.Scope) -> bool` *(class)* — Delete the ``data_export`` carrier when every attribute it adds is
  - `DataNodes.scene_path(cls) -> str` *(class)* — The open scene's file, ``""`` while unsaved
  - `DataNodes.set_scene_source(cls, scene: str, source: Optional[str] = None) -> Optional[str]` *(class)* — Record that the scene file *scene* stands in for the scene *source*;
  - `DataNodes.scene_source(cls, scene: Optional[str] = None) -> Optional[str]` *(class)* — The scene the scene file *scene* (default: the open one) stands in
  - `DataNodes.twin_save_target(cls, scene: Optional[str] = None) -> Optional[str]` *(class)* — Where a save of the stand-in *scene* (default: the open one) lands
  - `DataNodes.scene_workspace(cls) -> Optional[ptk.Workspace]` *(class)* — The project the open scene's files belong to, as a ``ptk.Workspace``;
  - `DataNodes.scene_project_root(cls) -> Optional[str]` *(class)* — The root of :meth:`scene_workspace`, or ``None`` without a project.
  - `DataNodes.scene_source_images_dir(cls, subfolder: str = '', create: bool = False) -> str` *(class)* — Where a tool's generated maps for the open scene go by default.
  - `DataNodes.install_path_rebase(cls) -> bool` *(class)* — Keep the path records spelled from the project of whichever file
  - `DataNodes.ensure_path_rebase(cls) -> bool` *(class)* — :meth:`install_path_rebase` unless a copy of it already is.
  - `DataNodes.remove_path_rebase(cls) -> None` *(class)* — Remove the re-base hook's callbacks (every copy's, by id).

<a id="nurbs_utils--_nurbs_utils"></a>
### `nurbs_utils/_nurbs_utils.py`

- **[`class NurbsUtils(pythontk.HelpMixin)`](mayatk/mayatk/nurbs_utils/_nurbs_utils.py#L22)**
  - `NurbsUtils.loft(cls, uniform=True, close=False, degree=3, autoReverse=False, sectionSpans=1, range_=False, polygon=True, reverseSurfaceNormals=True, angle_loft_between_two_curves=False, angleLoftSpans=6)` *(class)* — Create a loft between two selections.
  - `NurbsUtils.create_curve_between_two_objs(cls, start, end)` *(class)* — Create a bezier curve between starting and end object(s).
  - `NurbsUtils.duplicate_along_curve(path, start, count=6, geometry='Instancer')` *(static)* — Duplicate objects along a given curve using MASH.
  - `NurbsUtils.angle_loft_between_two_curves(cls, start, end, count=6, cleanup=False, uniform=1, close=0, autoReverse=0, degree=3, sectionSpans=1, range=0, polygon=1, reverseSurfaceNormals=0)` *(class)* — Perform a loft between two nurbs curves or polygon sets of edges (that will be extracted as curves).
  - `NurbsUtils.get_curve_length(cls, curve) -> float` *(class)* — World-space arc length of the given curve (transform or shape).
  - `NurbsUtils.get_arc_lengths(cls, curve, points) -> List[float]` *(class)* — Arc length along *curve* of the closest curve point to each given point.
  - `NurbsUtils.get_greville_arc_lengths(cls, curve) -> List[float]` *(class)* — Arc length of each CV's Greville abscissa, in CV index order.
  - `NurbsUtils.get_closest_cv(x, curves, tolerance=0.0)` *(static)* — Find the closest control vertex between the given vertices, CVs, or objects and each of the given c…
  - `NurbsUtils.get_cv_info(cls, c, returned_type='cv', filter_=[])` *(class)* — Get a dict containing CV's of the given curve(s) and their corresponding point positions (based on…
  - `NurbsUtils.getCrossProductOfCurves(cls, curves, normalize=1, values=False)` *(class)* — Get the cross product of two vectors using points derived from the given curves.

<a id="nurbs_utils--curve_to_tube"></a>
### `nurbs_utils/curve_to_tube.py`

Sweep a circular profile along NURBS curve(s) to build a tube.

- **[`class CurveToTube(pythontk.LoggingMixin)`](mayatk/mayatk/nurbs_utils/curve_to_tube.py#L50)** — Extrude a circular profile along NURBS curve(s) to build a tube.
  - `CurveToTube.create(cls, curves, output_type: str = 'nurbs', radius: float = 1.0, sections: int = 8, path_divisions: int = 1, degree: int = 3, caps: bool = True, quads: bool = True, live: bool = False, cleanup: bool = True, name: str = 'tube') -> List[str]` *(class)* — Build a tube along each selected curve.
  - `CurveToTube.describe(tubes) -> str` *(static)* — One line of stats for built tubes: a polygon tube's triangle count,

<a id="nurbs_utils--image_tracer"></a>
### `nurbs_utils/image_tracer.py`

- **[`class BluePencilMixin(object)`](mayatk/mayatk/nurbs_utils/image_tracer.py#L29)** — Mixin for handling Blue Pencil operations.
  - `BluePencilMixin.open_blue_pencil() -> None` *(static)* — Open Maya's Blue Pencil tool, whose strokes ``use_blue_pencil``
  - `BluePencilMixin.get_blue_pencil_curves(self)` — Converts active Blue Pencil strokes to NURBS curves.
- **[`class ImageTracer(BluePencilMixin)`](mayatk/mayatk/nurbs_utils/image_tracer.py#L115)** — A class to trace images into Maya NURBS curves and generate geometry.
  - `ImageTracer.trace_curves(self) -> List[str]` — Traces the image and returns a list of created NURBS curves.
  - `ImageTracer.create_mesh(self, curves: Optional[List[str]] = None, combine: bool = True, name: str = 'traced_mesh', group_output: bool = True) -> Union[str, List[str]]` — Creates a polygon mesh from the traced curves (positive space).
  - `ImageTracer.create_negative_space_mesh(self, curves: Optional[List[str]] = None, margin_scale: float = 0.1, name: str = 'negative_space_mesh', group_output: bool = True) -> Optional[str]` — Creates a mesh representing the negative space (plane with holes).
  - `ImageTracer.project_on_plane(self, curves: Optional[List[str]] = None, name: str = 'projected_curves', group_output: bool = True) -> Union[str, List[str], None]` — Projects curves onto a plane.

<a id="render_utils--_render_utils"></a>
### `render_utils/_render_utils.py`

Render-control helpers.

- **[`class RenderUtils(pythontk.HelpMixin)`](mayatk/mayatk/render_utils/_render_utils.py#L37)** — Renderer enumeration / selection and Render-View control.
  - `RenderUtils.get_available_renderers(cls) -> List[Dict[str, object]]` *(class)* — Renderers the user can pick.
  - `RenderUtils.current_renderer() -> str` *(static)* — The scene's active renderer (``defaultRenderGlobals.currentRenderer``).
  - `RenderUtils.set_renderer(cls, name: str) -> None` *(class)* — Make *name* the active renderer, loading its plugin if required.
  - `RenderUtils.ensure_arnold_options(cls) -> str` *(class)* — Load mtoa and create the scene's Arnold options if missing.
  - `RenderUtils.render_settings(cls) -> Dict[str, Any]` *(class)* — The scene's current value of every :attr:`RENDER_SETTINGS` key.
  - `RenderUtils.set_render_settings(cls, values: Dict[str, Any]) -> int` *(class)* — Write render settings to the scene.
  - `RenderUtils.render_preset_store() -> 'ptk.PresetStore'` *(static)* — Named render-setting presets: shipped Arnold tiers + user presets.
  - `RenderUtils.render_camera(camera: str, render_mode: str = 'render') -> None` *(static)* — Render *camera* into the Render View, opening it if needed.
  - `RenderUtils.redo_previous_render(render_mode: str = 'render') -> None` *(static)* — Re-render the last render with its previous settings (fast path).
  - `RenderUtils.supports_ipr(cls, renderer: Optional[str] = None) -> bool` *(class)* — True if *renderer* can start an interactive (IPR) session.
  - `RenderUtils.start_ipr(cls, camera: str, renderer: Optional[str] = None) -> bool` *(class)* — Launch interactive (IPR) realtime rendering for *renderer*.
  - `RenderUtils.prepare_arnold_preview(cls) -> Dict[str, List[str]]` *(class)* — Give Arnold what it needs to render what the viewport shows.
  - `RenderUtils.sync_arnold_backface_culling(cls) -> List[str]` *(class)* — Make Arnold camera rays cull the backfaces Viewport 2.0 culls.

<a id="rig_utils--_rig_utils"></a>
### `rig_utils/_rig_utils.py`

- **[`class RigUtils(pythontk.HelpMixin)`](mayatk/mayatk/rig_utils/_rig_utils.py#L25)**
  - `RigUtils.create_helper(name: str, helper_type: str = 'locator', parent: Optional[str] = None, position: Tuple[float, float, float] = (0.0, 0.0, 0.0), cleanup: bool = False) -> Optional[str]` *(static)* — Create a hidden helper object (e.g., locator, joint) with a consistent naming convention.
  - `RigUtils.create_group(objects=[], name='', zero_translation=False, zero_rotation=False, zero_scale=False)` *(static)* — Create a group containing any given objects.
  - `RigUtils.create_locator(*, scale: float = 1, size: float = 1, parent: Optional[str] = None, **kwargs) -> str` *(static)* — Create a locator with the given scale.
  - `RigUtils.create_locator_at_object(cls, objects: Union[str, List[str]], parent: bool = True, freeze_object: bool = True, freeze_locator: bool = True, loc_scale: float = 1.0, lock_translate: bool = False, lock_rotation: bool = False, lock_scale: bool = False, grp_suffix: Optional[str] = None, grp_affix_mode: str = 'auto', loc_suffix: Optional[str] = None, loc_affix_mode: str = 'auto', obj_suffix: Optional[str] = None, obj_affix_mode: str = 'auto', strip_digits: bool = False, strip_trailing_underscores: bool = True, strip_suffix: bool = True, rotate_order: str = 'xyz') -> List[str]` *(class)* — Rig object under a zeroed locator aligned to its manip pivot.
  - `RigUtils.remove_locator(cls, objects)` *(class)* — Remove a parented locator from the child object.
  - `RigUtils.restore_rig_anchors(cls, objects, traverse: bool = True, skip_animated: bool = True, pivot_source: str = 'bbox') -> List[str]` *(class)* — Restore the world-space anchor on a GRP > LOC > GEO rig after a freeze.
  - `RigUtils.connect_switch_to_constraint(cls, constraint_node: str, constraint_targets: Optional[List[str]] = None, attr_name: str = 'parent_switch', overwrite_existing: bool = False, node: Optional[str] = None, weighted: bool = False, anchor: Optional[str] = None) -> dict` *(class)* — Create a space switch attribute to drive a constraint node.
  - `RigUtils.create_ik_handle(start_joint: str, end_joint: str, solver: str = 'ikRPsolver', name: str = 'ikHandle', parent: Optional[str] = None, **kwargs) -> str` *(static)* — Create an IK handle.
  - `RigUtils.create_pole_vector(ik_handle: str, mid_joint: str, distance: float = 5.0, name: str = 'poleVector_LOC', parent: Optional[str] = None) -> str` *(static)* — Create a pole vector locator based on the mid joint's position.
  - `RigUtils.get_ik_handles_for_joint(joint: str) -> List[str]` *(static)* — Find IK handles that control a given joint.
  - `RigUtils.ik_handles_by_joint(handles: Optional[Iterable[str]] = None) -> Dict[str, List[str]]` *(static)* — ``{joint (long path): [ikHandle, ...]}`` for every joint an IK chain spans.
  - `RigUtils.joint_in_ik_chain(joint: str, start_joint: str, end_joint: str) -> bool` *(static)* — Check if a joint is part of an IK chain between start and end.
  - `RigUtils.get_joint_chain_from_root(root_joint: Union[str, List[str]], reverse: bool = False) -> List[str]` *(static)* — Get the joint chain from the root joint or the first joint in the list if more than one joint is gi…
  - `RigUtils.invert_joint_chain(root_joint, keep_original=False)` *(static)* — Create a new joint chain with the same positions as the original, but with reversed hierarchy.
  - `RigUtils.rebind_skin_clusters(cls, meshes: Optional[List[str]] = None, temp_dir: Optional[str] = None, inherits_transform: Optional[bool] = None) -> Dict[str, list]` *(class)* — Rebinds skinClusters on the given meshes, preserving weights, bind pose, and transform lock state.

<a id="rig_utils--articulated_rig--_articulated_rig"></a>
### `rig_utils/articulated_rig/_articulated_rig.py`

Articulated Rig engine -- rigid parts on hinge, swivel, ball and slide joints.

- **[`class ArticulatedRig(pythontk.LoggingMixin, _ArticulatedRigInternal)`](mayatk/mayatk/rig_utils/articulated_rig/_articulated_rig.py#L299)** — One articulated rig in the scene, bound to its rig group.
  - `ArticulatedRig.group(self) -> str` *(property)* — The rig group.
  - `ArticulatedRig.spec(self) -> Dict[str, Any]` *(property)* — The stamped plan (a fresh copy): name, links, joints, parts, and
  - `ArticulatedRig.name(self) -> str` *(property)*
  - `ArticulatedRig.group_uuid(self) -> Optional[str]` *(property)* — The rig's identity: two instances bound to one rig share it (what
  - `ArticulatedRig.scene_data(cls, node) -> Optional[Dict[str, Any]]` *(class)* — The :attr:`DATA_ATTR` plan on *node*, or None.
  - `ArticulatedRig.scene_rigs(cls) -> List['ArticulatedRig']` *(class)* — Every articulated rig in the scene (duplicated records skipped).
  - `ArticulatedRig.for_node(cls, node) -> Optional['ArticulatedRig']` *(class)* — The rig *node* belongs to -- its group, a joint, a control, the end
  - `ArticulatedRig.selection() -> List[str]` *(static)* — What the Articulated Rig tools act on (tentacle's panel reads it):
  - `ArticulatedRig.select(nodes: Sequence[str]) -> None` *(static)* — Replace the selection with those of *nodes* that exist -- parts,
  - `ArticulatedRig.parts_of(self, nodes: Sequence[str]) -> List[str]` — The *nodes* that are parts of this rig -- never a control, a joint
  - `ArticulatedRig.end_control(self) -> Optional[str]` *(property)* — The end control, or None (built without one).
  - `ArticulatedRig.solver(self) -> Optional[str]` *(property)* — The end control's solver (its expression node), or None -- no end
  - `ArticulatedRig.ik_blend(self) -> float` — How much the end control's solve adds over FK: 1 places the end
  - `ArticulatedRig.analyze(cls, nodes, root=None, ordered: bool = False) -> Dict[str, Any]` *(class)* — Propose a rig for *nodes*: one link per part, and how and where
  - `ArticulatedRig.create(cls, links: Sequence[Any], joints: Optional[Sequence[Dict[str, Any]]] = None, name: Optional[str] = None, parent: Optional[str] = None, end_control: bool = True) -> 'ArticulatedRig'` *(class)* — Build a rig.
  - `ArticulatedRig.repair_scene(cls) -> int` *(class)* — Give every rig whose end control has no solve -- built by an earlier
  - `ArticulatedRig.ensure_solver(self) -> bool` — Make the end control live: build its solve from the plan when the
  - `ArticulatedRig.teardown(self) -> None` — Remove the rig and hand every part back exactly as it was: its
  - `ArticulatedRig.rebuild(self, spec: Optional[Dict[str, Any]] = None) -> 'ArticulatedRig'` — Build the rig again from *spec* (default: its own plan), carrying
  - `ArticulatedRig.insert_joint(self, members, joint_type: Optional[str] = None, **fields) -> 'ArticulatedRig'` — Split a link: *members* leave the link they ride and get a joint of
  - `ArticulatedRig.remove_joint(self, joint_id: str) -> 'ArticulatedRig'` — Fold *joint_id*'s link back into the link it hangs off: its parts
  - `ArticulatedRig.edit_joint(self, joint_id: str, **fields) -> 'ArticulatedRig'` — Change a joint's ``type``, ``position``, ``aim`` or ``normal``
  - `ArticulatedRig.set_end_control(self, enabled: bool = True, part: Optional[str] = None) -> 'ArticulatedRig'` — Give the rig an end control, move it to another link, or drop it.
  - `ArticulatedRig.set_limits(self, joint_id: str, channel: str, minimum: Optional[float], maximum: Optional[float]) -> None` — Bound one channel (degrees, or a slide's units in the rig's space);
  - `ArticulatedRig.set_limit_from_pose(self, joint_id: str, channel: str, side: str) -> float` — Take the channel's current value -- the pose as it stands, the end
  - `ArticulatedRig.set_weight(self, joint_id: str, weight: float, channel: Optional[str] = None) -> None` — How readily *joint_id* moves when the end control or a grab pulls
  - `ArticulatedRig.adjusting(self) -> bool` *(property)* — Whether the rig's pivots are out for adjusting (:meth:`begin_adjust`).
  - `ArticulatedRig.adjust_handles(self) -> Dict[str, str]` — The pivot handles while adjusting, by what each places: a joint id,
  - `ArticulatedRig.begin_adjust(self) -> Dict[str, str]` — Put the rig's pivots out for adjusting: a handle where every joint
  - `ArticulatedRig.end_adjust(self, apply: bool = True) -> bool` — Lock the pivots again: the rig rebuilt with every joint turning
  - `ArticulatedRig.joint_ids(self) -> List[str]` — The joints' ids -- each its link's first part -- parent first.
  - `ArticulatedRig.joint_id_of(self, node) -> Optional[str]` — The id of the joint *node* is -- its control, its joint -- or rides
  - `ArticulatedRig.joint(self, joint_id: str) -> str` — The joint node of *joint_id*.
  - `ArticulatedRig.control(self, joint_id: str) -> str` — The FK control of *joint_id*.
  - `ArticulatedRig.controls(self) -> List[str]` — Every control of the rig: the FK controls parent first, then the
  - `ArticulatedRig.state(self, slots: Optional[Sequence[Tuple[str, str]]] = None) -> List[float]` — The rig's pose, in :meth:`model` state order: each channel's FK
  - `ArticulatedRig.fk_state(self, slots: Optional[Sequence[Tuple[str, str]]] = None) -> List[float]` — The FK controls' channel values, in :meth:`model` state order.
  - `ArticulatedRig.channel_value(self, joint_id: str, channel: str) -> float` — One channel as the pose stands (FK plus the end control's solve).
  - `ArticulatedRig.set_state(self, values: Sequence[float], key: Optional[bool] = None, slots: Optional[Sequence[Tuple[str, str]]] = None) -> None` — Set the FK controls to *values* (model state order), keyed when
  - `ArticulatedRig.match_end_control(self, key: Optional[bool] = None) -> None` — Put the end control where the end link is now -- after FK posing,
  - `ArticulatedRig.switch_ik(self, on: Optional[bool] = None, key: Optional[bool] = None) -> bool` — Switch the end control's solve on or off (default: flip it)
  - `ArticulatedRig.reset_pose(self, key: Optional[bool] = None) -> None` — Every control back to rest -- FK at 0, the end control on its rest
  - `ArticulatedRig.record(self) -> Dict[str, Any]` — This rig's entry in the ``articulation`` record: its joints
  - `ArticulatedRig.model(self) -> 'ptk.ArticulationModel'` — The runtime model of this rig -- what the engines pose.
  - `ArticulatedRig.export_record(cls, ctx) -> Optional['ptk.Record']` *(class)* — The ``articulation`` record for this scene, or None when it has no
  - `ArticulatedRig.refresh_export_metadata(cls) -> Optional[str]` *(class)* — Publish the ``articulation`` record now -- the authoring-time half
  - `ArticulatedRig.grab_begin(self, node, point: Sequence[float]) -> Dict[str, Any]` — Take hold of *node* (a part, or anything under one) at the world
  - `ArticulatedRig.held_point(self, hold: Dict[str, Any]) -> Tuple[float, float, float]` — Where a hold's point is now, in world space.
  - `ArticulatedRig.pose_to(self, node, target: Sequence[float], point: Optional[Sequence[float]] = None, key: Optional[bool] = None, attempts: int = 8) -> float` — Bring a part's *point* (default its rotate pivot) to the world
  - `ArticulatedRig.grab_to(self, hold: Dict[str, Any], target: Sequence[float], turn: Optional[Sequence[float]] = None, key: Optional[bool] = None) -> List[float]` — Move a hold's point to the world *target* and pose the rig there.
  - `ArticulatedRig.key_hold(self, hold: Dict[str, Any], key: Optional[bool] = None) -> None` — Key what a hold moved -- the end control it dragged, else the FK

<a id="rig_utils--articulated_rig--_solver_expression"></a>
### `rig_utils/articulated_rig/_solver_expression.py`

The end control's solve as a Maya expression: a MEL port of

- **[`class SolverExpression(_SolverExpressionInternal)`](mayatk/mayatk/rig_utils/articulated_rig/_solver_expression.py#L195)** — The MEL text of one rig's end-control solve.
  - `SolverExpression.text_for(cls, rig: Mapping[str, Any], end: int, pivot: Sequence[float], turn: Sequence[float], plugs: Mapping[str, Any]) -> str` *(class)* — The expression text (see the class for the parameters).
  - `SolverExpression.text(self) -> str`

<a id="rig_utils--articulated_rig--grab_tool"></a>
### `rig_utils/articulated_rig/grab_tool.py`

The viewport grab for articulated rigs: press on a rigged part, drag, let go.

- **[`class ArticulatedRigGrab(pythontk.LoggingMixin)`](mayatk/mayatk/rig_utils/articulated_rig/grab_tool.py#L36)** — Grab and drag the parts of articulated rigs in the viewport.
  - `ArticulatedRigGrab.activate(cls) -> str` *(class)* — Make the grab the current tool -- first giving any end control
  - `ArticulatedRigGrab.pick(origin: Sequence[float], direction: Sequence[float]) -> Optional[Tuple[ArticulatedRig, str, Tuple[float, float, float]]]` *(static)* — The rigged part a world ray hits first: ``(rig, part, point)``, or
  - `ArticulatedRigGrab.press(self, origin: Sequence[float], direction: Sequence[float]) -> bool` — Take hold of whatever rigged part the ray hits.
  - `ArticulatedRigGrab.drag(self, origin: Sequence[float], direction: Sequence[float]) -> Optional[list]` — Move the held point to where the ray crosses the camera-facing plane
  - `ArticulatedRigGrab.release(self) -> None` — Let go: key what the drag moved (the end control, or the FK
  - `ArticulatedRigGrab.ray(cls, point: Sequence[float]) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]` *(class)* — The world pick ray through a dragger point, from the active view's

<a id="rig_utils--controls"></a>
### `rig_utils/controls.py`

- **[`class ControlNodes`](mayatk/mayatk/rig_utils/controls.py#L23)**
- **[`class Controls(pythontk.HelpMixin)`](mayatk/mayatk/rig_utils/controls.py#L59)** — Factory for creating NURBS animation controls.
  - `Controls.register_preset(cls, name: str, builder: Callable[..., str]) -> None` *(class)* — Register a new control preset.
  - `Controls.shapes(cls) -> List[str]` *(class)* — Sorted names of the registered presets (for a UI combo / validation).
  - `Controls.create(cls, preset: str = 'diamond', name: Optional[str] = None, *, size: float = 1.0, axis: str = 'y', match: Any = None, parent: Optional[str] = None, color: Union[int, Tuple[float, float, float], None] = None, offset_group: bool = True, group_suffix: Optional[str] = None, ctrl_suffix: Optional[str] = None, freeze: bool = True, tag_as_controller: bool = True, return_nodes: bool = False, select: bool = False, **kwargs) -> Union[str, ControlNodes]` *(class)* — Create a NURBS control.
  - `Controls.set_channel_state(cls, node, *, keyable=None, lock=None, hide=None) -> None` *(class)* — Declare a control's channel-box contract in one call.
  - `Controls.combine(cls, controls: Iterable[Any], name: Optional[str] = None, *, parent: Optional[str] = None, match: Any = None, color: Union[int, Tuple[float, float, float], None] = None, delete_sources: bool = True, ctrl_suffix: Optional[str] = None) -> str` *(class)* — Combine multiple control transforms into a single selectable transform.

<a id="rig_utils--rig_graph_build"></a>
### `rig_utils/rig_graph_build.py`

Build a RigGraph in Maya -- Maya as the TARGET of the rig-transfer stack.

- **[`class RigGraphBuilder(_RigGraphBuilderInternal, pythontk.HelpMixin)`](mayatk/mayatk/rig_utils/rig_graph_build.py#L368)** — Plan a RigGraph against Maya and build what the plan allows.
  - `RigGraphBuilder.capability(cls) -> Dict[str, Any]` *(class)* — What Maya can build, as data the planner reads (section 9.2).
  - `RigGraphBuilder.scope(self)` — Yield with the scene's current time restored afterwards.
  - `RigGraphBuilder.linear_unit() -> str` *(static)* — The scene's linear unit, as the verifier's vocabulary spells it.
  - `RigGraphBuilder.up_axis() -> str` *(static)* — The scene's up axis (``"y"`` / ``"z"``).
  - `RigGraphBuilder.sample_world(self, node_id: str, frame: int) -> Optional[Tuple[float, float, float]]` — The world position of the node *node_id* names at *frame*, or ``None``.
  - `RigGraphBuilder.commit(self, record_id: str) -> int` — A verified record owns its channels: delete the payload's detached keys there.
  - `RigGraphBuilder.remove(self, record_id: str) -> int` — Take back everything :meth:`build` created for *record_id* and
  - `RigGraphBuilder.build(self, graph: Dict[str, Any], nodes: Sequence[str], is_usd: bool = False) -> Dict[str, Any]` — Plan *graph* against :meth:`capability` and build what it allows onto

<a id="rig_utils--rig_graph_extract"></a>
### `rig_utils/rig_graph_extract.py`

Read a Maya rig into a RigGraph -- phase 2 of the rig-transfer stack.

- **[`class RigGraphExtractor(_RigGraphExtractorInternal, pythontk.HelpMixin)`](mayatk/mayatk/rig_utils/rig_graph_extract.py#L803)** — Extract a Maya scene's rig logic into a RigGraph document (plain dict).
  - `RigGraphExtractor.extract(self, objects: Optional[Sequence[str]] = None) -> Dict[str, Any]` — Read the scene's drivers into a RigGraph envelope.
  - `RigGraphExtractor.machinery(self, rig: Optional[Dict[str, Any]] = None, scope: Optional[Sequence[str]] = None) -> Tuple[Dict[str, str], Tuple[str, ...]]` — Name the rig apparatus a bake leaves inert: ``({dag path: kind}, kept)``.

<a id="rig_utils--shadow_rig--_shadow_rig"></a>
### `rig_utils/shadow_rig/_shadow_rig.py`

Shadow Rig engine — a projected-shadow rig for engine export.

- **[`class ShadowRig(pythontk.LoggingMixin)`](mayatk/mayatk/rig_utils/shadow_rig/_shadow_rig.py#L33)** — Projected shadow for engine export (Unity, WebXR).
  - `ShadowRig.has_mesh_geometry(node, recursive=True)` *(static)* — Does *node* carry (or, with *recursive*, contain) mesh geometry a
  - `ShadowRig.create_contact_locator(self)` — Create a locator at the lowest point of the combined objects to act
  - `ShadowRig.ensure_source(cls, source_name=DEFAULT_SOURCE_NAME, position=(5, 10, 5))` *(class)* — The transform named *source_name*, created as a locator if absent.
  - `ShadowRig.get_or_create_shadow_source(self, position=(5, 10, 5), source_name=DEFAULT_SOURCE_NAME)` — Bind this rig to :meth:`ensure_source`'s transform for *source_name*.
  - `ShadowRig.source_is_directional(cls, source)` *(class)* — Is *source* projected along its direction (a ``directionalLight``)
  - `ShadowRig.source_size(cls, source)` *(class)* — The size the shadow gives *source*, the penumbra's cause: its
  - `ShadowRig.source_softness(cls, source)` *(class)* — The Softness set on *source* -- world units, degrees for a
  - `ShadowRig.set_source_softness(cls, source, value)` *(class)* — Give *source* a Softness (added on first use;
  - `ShadowRig.planes_lit_by(cls, source)` *(class)* — The shadow planes whose stamped source is *source*.
  - `ShadowRig.find_source(cls, name)` *(class)* — The existing source named *name* -- its transform's long name (a
  - `ShadowRig.shadow_targets(cls, objects, sources=None, recursive=True)` *(class)* — The shadow casters among *objects* -- what a build of them casts
  - `ShadowRig.source_selection()` *(static)* — The selection read as shadow sources (the panel's Source From
  - `ShadowRig.delete_orphaned_textures(cls, paths)` *(class)* — Delete each of *paths* -- a shadow plane's own silhouette or
  - `ShadowRig.create_shadow_plane(self)` — Create the unit quad for the shadow with the keyable shadow attrs
  - `ShadowRig.current_model(self)` — The projection model at the source's CURRENT position, for the
  - `ShadowRig.create_silhouette_texture(self, size=512, *, recursive=True, uniform_alpha=True, falloff_power=0.8, vertical_weight=0.3, blur_amount=1.0, path=None, refit=True, source_size=None)` — Rasterize the targets' shadow — their geometry projected onto the
  - `ShadowRig.create_material(self, shader_type='standard', stingray_opacity_mode='transparent')` — Create material with the silhouette texture.
  - `ShadowRig.setup_expression(self)` — (Re)create the expression that places the plane from the source
  - `ShadowRig.bake(self, start=None, end=None)` — Bake this rig's driven channels to keyframes and remove the live
  - `ShadowRig.plane_is_live(cls, plane)` *(class)* — Does *plane* still carry its expression (not baked)?
  - `ShadowRig.plane_is_baked(cls, plane)` *(class)* — Does *plane* carry baked keys on its driven channels?
  - `ShadowRig.from_plane(cls, plane)` *(class)* — A rig instance re-attached to an existing *plane* via its stamps,
  - `ShadowRig.planes_for_nodes(cls, nodes)` *(class)* — Shadow planes the given nodes touch: the planes themselves (or
  - `ShadowRig.for_node(cls, node)` *(class)* — The rig *node* belongs to (a plane, its group, a target, the
  - `ShadowRig.for_nodes(cls, nodes)` *(class)* — Distinct rigs the *nodes* touch (see :meth:`for_node`).
  - `ShadowRig.set_source(self, source_name, position=(5, 10, 5), size=None)` — Re-point this rig at another source — an existing transform or
  - `ShadowRig.unbake_planes(cls, planes=None)` *(class)* — Restore the live expression on baked shadow planes — the reverse
  - `ShadowRig.rebuild(cls, plane, texture_res=None, recursive=None, shader_type=None)` *(class)* — Tear a rig down and build it again from its own stamps — the same
  - `ShadowRig.silhouette_is_stale(cls, plane, *, degrees=None, distance=None)` *(class)* — Has the source moved past *degrees* (default
  - `ShadowRig.auto_recalculate(cls, on=True)` *(class)* — Follow Source: re-render a silhouette as soon as its source -- or
  - `ShadowRig.auto_recalculate_enabled(cls)` *(class)* — Is Follow Source on?
  - `ShadowRig.recalculate_stale(cls, planes=None)` *(class)* — Recalculate the silhouettes (of *planes*, default all) whose
  - `ShadowRig.refresh_silhouette(cls, planes=None, size=None, refit=None)` *(class)* — Re-rasterize shadow planes' silhouettes from their source's CURRENT
  - `ShadowRig.export_record(cls, ctx)` *(class)* — The ``shadows`` record for this scene, or ``None`` when it
  - `ShadowRig.refresh_export_metadata(cls)` *(class)* — Republish the ``shadows`` channel on the ``data_export``
  - `ShadowRig.unit_scale()` *(static)* — Metres per unit of the record (its ``unit_scale``): the working
  - `ShadowRig.rig_unit(cls, plane)` *(class)* — The unit *plane*'s length stamps are in: the working unit its
  - `ShadowRig.plane_record(cls, plane)` *(class)* — One plane's ``shadows`` v2 record (the engine contract in
  - `ShadowRig.plane_type(cls, plane)` *(class)* — The rig type stamped on *plane* (``projected`` for a rig built
  - `ShadowRig.horizon_output_path(self)` — ``<base>_horizon.png`` beside the silhouette.
  - `ShadowRig.bake_horizon(self, size=None, spans=None, path=None, *, only_if_changed=False)` — Bake the target's height-field shadow map
  - `ShadowRig.plane_is_atlased(cls, plane)` *(class)* — True while the plane samples the shared silhouette atlas.
  - `ShadowRig.default_texture_dir() -> str` *(static)* — Where a rig's maps go when no path names one: the open SCENE's
  - `ShadowRig.pack_atlas(cls, planes=None, *, gutter=None)` *(class)* — Pack the scene's shadow tiles into one atlas per kind — every
  - `ShadowRig.unpack_atlas(cls, planes=None)` *(class)* — Undo :meth:`pack_atlas` for *planes* (all when None): unit UVs,
  - `ShadowRig.find_shadow_planes(cls, nodes=None)` *(class)* — Shadow planes = transforms carrying the stamped ``basePlaneSize``
  - `ShadowRig.bake_planes(cls, planes=None, start=None, end=None)` *(class)* — Bake shadow planes' expression-driven channels to keyframes and
  - `ShadowRig.delete(self, delete_textures=False)` — Delete this rig completely.
  - `ShadowRig.delete_rigs(cls, planes=None, delete_textures=False)` *(class)* — Tear down shadow rig(s) completely — live or baked.
  - `ShadowRig.plane_materials(cls, planes=None)` *(class)* — The shaders shadow *planes* wear (every plane when None), once each.
  - `ShadowRig.set_intensity(cls, planes, value)` *(class)* — Overall darkness of *planes*' shadows, 0-1: the ``shadowIntensity``
  - `ShadowRig.set_ground_height(cls, planes, value)` *(class)* — World Y of the floor under *planes*: the ``groundHeight`` stamp (the
  - `ShadowRig.detach_planes(cls, planes=None, suffix='_plane')` *(class)* — Turn shadow rig(s) into plain textured planes: no rig, nothing extra.
  - `ShadowRig.create(cls, targets, light_pos=(5, 10, 5), texture_res=512, *, source_name=DEFAULT_SOURCE_NAME, recursive=True, mode='orbit', ground_height=0.0, shader_type='standard', rig_type='projected', horizon_size=None, horizon_spans=None)` *(class)* — Create a projected shadow for engine export.
  - `ShadowRig.create_for_sources(cls, targets, sources, **kwargs)` *(class)* — One shadow rig per source — N lights cast N shadows.
  - `ShadowRig.create_horizon_for_sources(cls, targets, sources, **kwargs)` *(class)* — :meth:`create_for_sources` for the ``horizon`` rig type.
  - `ShadowRig.create_per_object(cls, targets, sources, **kwargs)` *(class)* — One rig per target per source — the panel's *Per object* planes.

<a id="rig_utils--shadow_rig--shadow_preview"></a>
### `rig_utils/shadow_rig/shadow_preview.py`

A live Viewport 2.0 preview of a horizon rig: the artist drags the light

- **[`class ShadowPreview(_ShadowPreviewInternal, pythontk.LoggingMixin)`](mayatk/mayatk/rig_utils/shadow_rig/shadow_preview.py#L383)** — Attach / detach the live horizon preview on shadow planes (module doc).
  - `ShadowPreview.device(cls) -> Optional[str]` *(class)* — The live Viewport 2.0 device, or None headless.
  - `ShadowPreview.language(cls) -> Tuple[Optional[str], str]` *(class)* — ``(language, refusal)``: the shader language this session can
  - `ShadowPreview.shader_node(cls, plane) -> Optional[str]` *(class)* — The plane's preview shader node, or None.
  - `ShadowPreview.is_attached(cls, plane) -> bool` *(class)*
  - `ShadowPreview.attached_planes(cls) -> List[str]` *(class)* — Every plane in the scene wearing a preview.
  - `ShadowPreview.restore_snapshot(cls, plane) -> Dict[str, Optional[List[int]]]` *(class)* — The real shading assignment a preview stands in for (``{}`` when
  - `ShadowPreview.attach(cls, plane, *, language: Optional[str] = None) -> str` *(class)* — Show the live preview on *plane* (a horizon rig's plane).
  - `ShadowPreview.detach(cls, plane) -> bool` *(class)* — Restore the plane's real material and delete the preview's nodes.
  - `ShadowPreview.toggle(cls, planes: Sequence[str], on: bool) -> Tuple[List[str], List[str]]` *(class)* — Attach (*on*) or detach the preview on *planes*;
  - `ShadowPreview.detach_all(cls) -> List[str]` *(class)* — Detach every preview in the scene;
  - `ShadowPreview.prepare_for_export(cls) -> None` *(class)* — Stand every preview down, then republish the shadow record: no
  - `ShadowPreview.preview_dir(png: Optional[str] = None) -> str` *(static)* — Where the preview's viewport-only files go: the open scene's
  - `ShadowPreview.texture_path(cls, png: str) -> str` *(class)* — Where :meth:`_write_texture` keeps the 16-bit promotion of *png*:
  - `ShadowPreview.classify_device(info: Iterable[str]) -> Optional[str]` *(static)* — ``"dx11"``, ``"glcore"``, ``"gl"`` (legacy) or None, from the lines
  - `ShadowPreview.effect_text(cls, language: str) -> str` *(class)* — The complete effect for *language*: Maya's prologue around the
  - `ShadowPreview.refusal(cls, device: Optional[str]) -> str` *(class)* — Why a preview cannot be shown on *device* (empty when it can).

<a id="rig_utils--skinning"></a>
### `rig_utils/skinning.py`

Skinning utilities: binding, batch weight I/O, transfer, procedural weights.

- **[`class CurveWeights(pythontk.HelpMixin)`](mayatk/mayatk/rig_utils/skinning.py#L51)** — Analytic, ring-uniform skin weights for a joint chain along a curve.
  - `CurveWeights.effective_degree(degree: int, num_joints: int) -> int` *(static)* — The basis degree actually solvable: *degree* clamped to [1, num_joints - 1].
  - `CurveWeights.joint_stations(cls, joints: List[str], curve) -> List[float]` *(class)* — Arc length of each joint's closest curve point, in input order.
  - `CurveWeights.solve(cls, geometry, joints: List[str], curve: Optional[str] = None, centerline: Optional[Sequence] = None, profile: Union[str, Callable] = 'smoothstep', degree: int = 3, rings: Optional[Sequence[Sequence[int]]] = None) -> Tuple[List[float], List[str]]` *(class)* — Compute per-component weights from arc-length stations along a curve.
- **[`class SkinUtils(pythontk.HelpMixin)`](mayatk/mayatk/rig_utils/skinning.py#L442)** — Skinning: binding, batch weight I/O, transfer, falloffs, delta mush.
  - `SkinUtils.get_skin_cluster(mesh) -> Optional[str]` *(static)* — Return the first skinCluster in the mesh's history, or None.
  - `SkinUtils.get_influences(cls, skin_cluster, long_names: bool = False) -> List[str]` *(class)* — Influence names in PHYSICAL order (``MFnSkinCluster.influenceObjects()``).
  - `SkinUtils.bind(cls, mesh, joints, bind_method: str = 'closest', skinning_method: str = 'classic', max_influences: int = 4, dropoff_rate: float = 4.0, weight_distribution: float = 0.5, remove_unused_influences: bool = False, heatmap_falloff: float = 0.68, bind_fallback: bool = True, support_non_rigid: bool = True, name: Optional[str] = None) -> str` *(class)* — Smooth-bind *mesh* to *joints* with the full skinCluster arg surface.
  - `SkinUtils.name_bind_pose(skin_cluster, name: str) -> Optional[str]` *(static)* — Rename *skin_cluster*'s dagPose to *name*.
  - `SkinUtils.unbind(cls, mesh) -> bool` *(class)* — Remove the mesh's skinCluster (restores the pre-bind shape).
  - `SkinUtils.get_weights(cls, skin_cluster, vertices: Optional[Sequence[int]] = None) -> Tuple[List[float], List[str]]` *(class)* — Read weights in one batched API call.
  - `SkinUtils.set_weights(cls, skin_cluster, weights: Sequence[float], influences: Optional[List[str]] = None, vertices: Optional[Sequence[int]] = None, normalize: bool = True, undoable: bool = False) -> List[float]` *(class)* — Write weights in one batched call.
  - `SkinUtils.set_vertex_weights(cls, skin_cluster, vertex_weights: Dict[int, Dict[str, float]], undoable: bool = True) -> None` *(class)* — Sparse per-vertex write with skinPercent semantics.
  - `SkinUtils.prune_weights(cls, skin_cluster, below: float = 0.001) -> None` *(class)* — Zero weights below the threshold and renormalize.
  - `SkinUtils.normalize_weights(cls, skin_cluster) -> None` *(class)* — Normalize all weights to sum 1 per component.
  - `SkinUtils.set_max_influences(cls, skin_cluster, max_influences: int, enforce: bool = True) -> None` *(class)* — Set the influence cap;
  - `SkinUtils.set_skinning_method(cls, skin_cluster, method: str = 'dqs', support_non_rigid: bool = True) -> None` *(class)* — Set the blend method: "classic" | "dqs" | "blended".
  - `SkinUtils.set_dqs_support_non_rigid(skin_cluster, enabled: bool = True) -> bool` *(static)* — Enable ``dqsSupportNonRigid`` — required whenever DQS meets SCALE.
  - `SkinUtils.copy_weights(cls, source_mesh, target_mesh, surface_association: str = 'closestPoint', influence_association: Sequence[str] = ('label', 'oneToOne', 'closestJoint'), bind_target_if_needed: bool = True) -> str` *(class)* — Copy skin weights between meshes;
  - `SkinUtils.mirror_weights(cls, mesh, axis: str = 'YZ', positive_to_negative: bool = True, surface_association: str = 'closestPoint', influence_association: Sequence[str] = ('label', 'closestJoint', 'oneToOne')) -> None` *(class)* — Mirror weights across a plane ("YZ" | "XY" | "XZ") on the same mesh.
  - `SkinUtils.add_to_skin(cls, target, meshes, combine: bool = True, rigid: bool = False, delete_sources: bool = True, tolerance: float = 0.0001, max_iterations: int = 10) -> List[str]` *(class)* — Skin extra geometry to *target*'s skin, exactly where it sits now.
  - `SkinUtils.export_weights(cls, mesh, file_path: Optional[str] = None) -> str` *(class)* — Export skin weights to XML (cmds.deformerWeights).
  - `SkinUtils.import_weights(cls, mesh, file_path: str, method: str = 'index') -> None` *(class)* — Import skin weights from XML and renormalize.
  - `SkinUtils.add_influence(cls, skin_cluster: str, influence: str, bind_matrix: Optional[Union['om.MMatrix', Sequence[float]]] = None, weight: float = 0.0) -> int` *(class)* — Add *influence* to *skin_cluster* and return its physical index
  - `SkinUtils.apply_falloff(cls, skin_cluster, target_influence, center, radius: float = 5.0, profile: Union[str, Callable] = 'linear', source_influence: Optional[str] = None, add_influence: bool = True, undoable: bool = True) -> int` *(class)* — Distance-based weight falloff around *center*.
  - `SkinUtils.add_delta_mush(cls, mesh, smoothing_iterations: int = 10, smoothing_step: float = 0.5, pin_border_vertices: bool = True, name: Optional[str] = None) -> str` *(class)* — Add a deltaMush finishing pass (softens residual skinning artifacts).
  - `SkinUtils.bind_to_curve(cls, geometry, joints, curve: Optional[str] = None, centerline: Optional[Sequence] = None, profile: Union[str, Callable] = 'smoothstep', degree: int = 3, skinning_method: str = 'dqs', max_influences: Optional[int] = None, rings: Optional[Sequence[Sequence[int]]] = None, name: Optional[str] = None, **bind_kwargs) -> str` *(class)* — One-call precision bind for tube-like geometry.
  - `SkinUtils.flatten_influences(cls, skin_clusters: Optional[Sequence[str]] = None, frames: Optional[Sequence[float]] = None, unpin_geometry: bool = True, root_suffix: str = '_skeleton', root_parent: str = 'ancestor', orient_bones: bool = False) -> Dict[str, Dict[str, str]]` *(class)* — Give every mesh skin ONE shear-free skeleton for export.

<a id="rig_utils--telescope_rig"></a>
### `rig_utils/telescope_rig.py`

- **[`class TelescopeRigBundle`](mayatk/mayatk/rig_utils/telescope_rig.py#L22)** — Record of everything one ``setup_telescope_rig`` build created.
  - `TelescopeRigBundle.to_json(self) -> str`
  - `TelescopeRigBundle.from_json(cls, payload: str) -> 'TelescopeRigBundle'` *(class)* — Rebuild a bundle from :meth:`to_json` output, ignoring unknown keys
- **[`class TelescopeRig(pythontk.LoggingMixin)`](mayatk/mayatk/rig_utils/telescope_rig.py#L58)** — Telescope Rig
  - `TelescopeRig.partition_selection(cls, selection: Optional[List[str]] = None) -> Tuple[Optional[str], List[str], Optional[str]]` *(class)* — Split an ordered selection into ``(base_locator, segments, end_locator)``.
  - `TelescopeRig.setup_telescope_rig(self, base_locator: Optional[Union[str, List[str]]] = None, end_locator: Optional[Union[str, List[str]]] = None, segments: Optional[List[str]] = None, collapsed_distance: Optional[float] = None, aim_axis: str = 'y', world_up_type: str = 'scene', lock_attributes: bool = True, name: str = 'telescope') -> TelescopeRigBundle` — Sets up constraints and driven keys to make a series of segments telescope between two locators.
  - `TelescopeRig.scene_bundles(cls) -> List[TelescopeRigBundle]` *(class)* — Every telescope-rig bundle stamped into the current scene.
  - `TelescopeRig.find_bundles(cls, nodes) -> List[TelescopeRigBundle]` *(class)* — Bundles whose locators or segments intersect *nodes*.
  - `TelescopeRig.teardown(self, bundle: Union[TelescopeRigBundle, List[TelescopeRigBundle], None] = None) -> bool` — Remove a telescope rig built by this class.

<a id="rig_utils--tube_rig--_tube_rig"></a>
### `rig_utils/tube_rig/_tube_rig.py`

Tube Rig engine — rigs a tube-shaped mesh (hose, cable, piston, tail).

- **[`class TubeRig(pythontk.LoggingMixin, _TubeRigInternal)`](mayatk/mayatk/rig_utils/tube_rig/_tube_rig.py#L397)** — Rig engine for tube-shaped meshes: joints, IK, controls, skinning.
  - `TubeRig.for_mesh(cls, mesh) -> Optional['TubeRig']` *(class)* — Look up an existing TubeRig instance bound to *mesh*, or return None.
  - `TubeRig.for_node(cls, node) -> Optional['TubeRig']` *(class)* — Find the TubeRig owning *node* — the rigged mesh itself, or
  - `TubeRig.scene_data(cls, node) -> Optional[dict]` *(class)* — The ``DATA_ATTR`` record on *node* as a dict, or None.
  - `TubeRig.from_scene(cls, node) -> Optional['TubeRig']` *(class)* — Rebuild a ``TubeRig`` handle from what the scene holds for the rig
  - `TubeRig.for_build(cls, obj, rig_name: Optional[str] = None) -> Optional['TubeRig']` *(class)* — The rig an operation on *obj* acts on: the rig that already owns it
  - `TubeRig.selected_tubes() -> List[str]` *(static)* — The tube meshes a build acts on: every selected object (one rig
  - `TubeRig.selected_edges() -> Optional[List[str]]` *(static)* — The selected polygon edges -- the path a build or Step 1 reads the
  - `TubeRig.selected_rigs(cls) -> List['TubeRig']` *(class)* — The distinct rigs the selection touches (mesh, joint, control,
  - `TubeRig.selected_rebind_targets(cls) -> Tuple[List['TubeRig'], Optional[str]]` *(class)* — The rigs Rebind Skin acts on, and the mesh to bind them to.
  - `TubeRig.undo_step(name: str = 'Tube Rig')` *(static)* — ONE undo step for everything the block does, the viewport held still
  - `TubeRig.create_joints(self, strategy: str = 'spline', num_joints: int = -1, radius: float = -1.0, reverse: bool = False, edges: Optional[List[str]] = None) -> List[str]` — Step 1 of a step-by-step build: this rig's joints along the tube's
  - `TubeRig.create_controls_from_selection(cls, strategy: str = 'spline', num_controls: int = 3, rig_name: Optional[str] = None, enable_stretch: bool = True, enable_squash: bool = True, enable_volume: bool = True, enable_twist: bool = True, enable_auto_bend: bool = False) -> Tuple[str, List[str], List[str]]` *(class)* — Step 2 of a step-by-step build: the mode's controls on the selected
  - `TubeRig.bind_from_selection(cls, strategy: str = 'spline', rig_name: Optional[str] = None) -> Tuple[str, int, str]` *(class)* — Step 3 of a step-by-step build: bind the tube mesh selected LAST to
  - `TubeRig.constrain_ends_from_selection(cls, strategy: str = 'spline', rig_name: Optional[str] = None, progress: Callable = None) -> List[Tuple[str, str, Optional[str]]]` *(class)* — Constrain one or both ends of the selected bound rig to anchors:
  - `TubeRig.rename(self, new_name: str) -> str` — Rename the rig: every node carrying the ``<rig>_`` prefix — group,
  - `TubeRig.rig_name(self) -> str` *(property)* — Returns the rig name.
  - `TubeRig.rig_group(self) -> str` *(property)*
  - `TubeRig.teardown(self, progress: Callable = None) -> None` — Delete everything a previous ``build`` created — the rig group and
  - `TubeRig.build(self, strategy: str = 'spline', progress: Callable = None, **kwargs)` — Builds the rig using the specified strategy.
  - `TubeRig.resolve_centerline(self, num_joints: int = -1, edges: list = None) -> Tuple[List, int]` — Extract this rig's tube centerline.
  - `TubeRig.estimate_tube_radius(self, centerline: List = None) -> Optional[float]` — Measure the tube's radius from the mesh surface.
  - `TubeRig.resolve_sizes(self, centerline: List = None, joint_radius: float = -1.0) -> Tuple[float, float]` — Resolve (joint_display_radius, control_base_size) from the tube.
  - `TubeRig.generate_joint_chain(self, centerline: List[List[float]], num_joints: int, reverse: bool = False, **kwargs) -> List[str]` — Generates joints along the tube's centerline.
  - `TubeRig.create_anchor_joints(self, centerline: List, radius: float = 1.0) -> List[str]` — Create the anchor rig's two end joints from the tube centerline.
  - `TubeRig.skin_mesh(self, joints: List[str], curve: Optional[str] = None, centerline: Optional[List] = None, skinning_method: str = 'dqs', mesh: Optional[str] = None) -> Optional[str]` — Smooth-bind the mesh to *joints* and record the skinCluster
  - `TubeRig.rebind_skin(self, skinning_method: str = 'dqs', mesh: Optional[str] = None) -> str` — Re-solve this rig's mesh bind from what is already in the scene.
  - `TubeRig.create_logic_curve(self, centerline: List[List[float]]) -> str` — Creates the logic curve for Spline IK.
  - `TubeRig.create_spline_drivers(self, centerline: List[List[float]], radius: float = 1.0, num_controls: int = 3) -> Tuple[List[str], List[str], List]` — Creates the driver system (controls and joints) for the Spline IK curve.
  - `TubeRig.skin_curve_to_drivers(self, curve, driver_joints) -> Optional[str]` — Bind the IK logic curve to the driver joints.
  - `TubeRig.create_settings_control(self, size: float = 1.0) -> str` — Build the rig's settings control: the single place an animator
  - `TubeRig.setup_space_switching(self, control, attr_name: str = 'space') -> str` — Give *control* an animator-keyable ``space`` enum: local / world /
  - `TubeRig.set_custom_space(self, control, target: Optional[str]) -> None` — Assign (or with ``target=None`` clear) *control*'s custom space.
  - `TubeRig.create_spline_controls(self, joints: List[str], centerline: Optional[List] = None, size: float = 1.0, num_controls: int = 3, enable_stretch: bool = True, enable_squash: bool = True, enable_volume: bool = True, enable_twist: bool = True, enable_auto_bend: bool = False, enable_tweaks: bool = True) -> Tuple[List[str], str, str]` — Build the complete spline-IK control rig over an existing joint chain:
  - `TubeRig.create_fk_controls(self, joints: List[str], size: float = 1.0, num_controls: int = 5) -> List[str]` — Build a nested FK control hierarchy over the joint chain.
  - `TubeRig.create_tweak_controls(self, joints: List[str], size: float = 1.0, every_n: int = 1) -> List[str]` — Secondary finesse layer over the spline result — the FK-on-IK
  - `TubeRig.create_anchor_controls(self, joints: List[str], size: float = 1.0, enable_stretch: bool = True) -> List[str]` — Build the anchor/piston controls over the two end joints
  - `TubeRig.setup_spline_twist(self, ik_handle, start_ctrl, end_ctrl, start_up_loc=None, end_up_loc=None)` — Setup advanced twist for IK Spline.
  - `TubeRig.setup_auto_bend(self, start_ctrl, mid_ctrl, end_ctrl)` — Setup automatic bending of the mid control based on compression
  - `TubeRig.setup_spline_stretch(self, curve, joints, enable_stretch=True, enable_squash=True, enable_volume=True, main_control=None)`
  - `TubeRig.create_ik(self, joints: List[str], **kwargs) -> Optional[str]`
  - `TubeRig.create_pole_vector(self, ik_handle, mid_joint: str, offset=(0, 5, 0)) -> str`
  - `TubeRig.bind_joint_chain(self, obj, joints: List[str], curve: Optional[str] = None, centerline: Optional[List] = None) -> Optional[str]` — Bind the joint chain to a polygon tube with smooth skinning.
  - `TubeRig.constrain_end_with_falloff(self, joints: 'List[str]', anchor: str, falloff: float = 5.0, joint_index: int = -1, profile: Union[str, Callable] = 'smoothstep') -> 'Optional[str]'` — Constrains a joint in the chain to an anchor and applies distance-based skin weight falloff.

<a id="rig_utils--tube_rig--strategies"></a>
### `rig_utils/tube_rig/strategies.py`

Tube Rig build strategies and the bundle they return.

- [`RIG_MODES`](mayatk/mayatk/rig_utils/tube_rig/strategies.py#L72) — constant
- **[`class TubeRigBundle`](mayatk/mayatk/rig_utils/tube_rig/strategies.py#L26)**
- **[`class RigModeConfig`](mayatk/mayatk/rig_utils/tube_rig/strategies.py#L45)** — A rig mode: its strategy, its option defaults, and which options it
- **[`class TubeStrategy(ABC)`](mayatk/mayatk/rig_utils/tube_rig/strategies.py#L130)**
  - `TubeStrategy.build(self, rig: 'TubeRig', **kwargs) -> TubeRigBundle`
- **[`class FKChainStrategy(TubeStrategy)`](mayatk/mayatk/rig_utils/tube_rig/strategies.py#L136)** — Joints → nested FK controls → parametric skin.
  - `FKChainStrategy.build(self, rig: 'TubeRig', **kwargs) -> TubeRigBundle`
- **[`class SplineIKStrategy(TubeStrategy)`](mayatk/mayatk/rig_utils/tube_rig/strategies.py#L168)** — Joints → spline-IK control rig → parametric skin along the IK curve.
  - `SplineIKStrategy.build(self, rig: 'TubeRig', **kwargs) -> TubeRigBundle`
- **[`class AnchorStrategy(TubeStrategy)`](mayatk/mayatk/rig_utils/tube_rig/strategies.py#L213)** — Two end joints → anchor controls with distance stretch → parametric skin.
  - `AnchorStrategy.build(self, rig: 'TubeRig', **kwargs) -> TubeRigBundle`

<a id="rig_utils--tube_rig--tube_path"></a>
### `rig_utils/tube_rig/tube_path.py`

Tube-mesh centerline extraction — pure geometry analysis, no scene objects.

- **[`class TubePath(_TubePathInternal)`](mayatk/mayatk/rig_utils/tube_rig/tube_path.py#L73)** — Pure geometry analysis for tube-like meshes.
  - `TubePath.get_centerline(mesh, num_joints: int = 10, precision: int = 10, edges: list = None, use_surface_normals: bool = True, rings: Optional[List[List[int]]] = None) -> Tuple[List, int]` *(static)* — Unified centerline dispatcher — picks the best algorithm.
  - `TubePath.order_cycle(edge_pairs) -> List[int]` *(static)* — Walk ``edge_pairs`` into cyclic vertex order, or ``[]``.
  - `TubePath.get_vertex_rings(mesh) -> List[List[int]]` *(static)* — Vertex-index groups, one per circumferential edge loop.
  - `TubePath.get_edge_loop_centers(mesh, rings: Optional[List[List[int]]] = None) -> Tuple[List[om.MPoint], int]` *(static)* — Extract centerline by finding all edge loops (cross-sections) of a tube mesh.
  - `TubePath.get_end_normals(mesh, rings: Optional[List[List[int]]] = None, start_point=None) -> Tuple[Optional['om.MVector'], Optional['om.MVector']]` *(static)* — Unit normals of the tube's two end cross-sections, pointing along
  - `TubePath.estimate_radius(mesh, centerline: List) -> Optional[float]` *(static)* — Estimate the tube's radius: median distance from interior
  - `TubePath.get_centerline_using_edges(edge_selection: List[str]) -> List[List[float]]` *(static)* — Derive centerline points from selected edges of the tube.
  - `TubePath.get_centerline_from_surface_normals(mesh, num_points: int = 10, iterations: int = 3) -> List[om.MPoint]` *(static)* — Calculate centerline by iteratively averaging opposing surface hits.
  - `TubePath.get_centerline_from_bounding_box(obj, precision=10, smooth=False, window_size=1)` *(static)* — Calculate the centerline of an object using the cross-section of its largest bounding box axis.

<a id="rig_utils--wheel_rig"></a>
### `rig_utils/wheel_rig.py`

- **[`class WheelRig(pythontk.LoggingMixin)`](mayatk/mayatk/rig_utils/wheel_rig.py#L21)** — Handles basic wheel rigging by linking rotation to linear control movement.
  - `WheelRig.rig_name(self) -> str` *(property)*
  - `WheelRig.default_rig_name(cls, control=None) -> Optional[str]` *(class)* — The name a rig on *control* takes when none is given: the rig ID an
  - `WheelRig.from_selection(cls, rig_name: Optional[str] = None, freeze_transforms: bool = True, **kwargs) -> 'WheelRig'` *(class)* — Rig the selected wheels to the selected driver as ONE undo step: what
  - `WheelRig.wheel_size(obj=None, movement_axis: str = 'translateZ') -> Optional[float]` *(static)* — A wheel's diameter from *obj*'s bounding box: its larger extent
  - `WheelRig.get_expressions(self, filter_by_rig: bool = False) -> List[object]` — Return all expression nodes connected to the control.
  - `WheelRig.delete_expressions(self, filter_by_rig: bool = True) -> None` — Delete expression nodes associated with this rig.
  - `WheelRig.rig_rotation(self, movement_axis: str = 'translateZ', rotation_axis: Optional[str] = None, wheel_height: float = 1.0, wheels: Optional[List['object']] = None, use_world_space: bool = False) -> None` — Rig wheels to rotate based on control movement.

<a id="ui_utils--_ui_utils"></a>
### `ui_utils/_ui_utils.py`

- **[`class UiUtils`](mayatk/mayatk/ui_utils/_ui_utils.py#L12)**
  - `UiUtils.get_editor_types(cls) -> dict` *(class)* — The friendly-name -> runtime-command map understood by :meth:`open_editor`.
  - `UiUtils.get_editor_categories(cls) -> dict` *(class)* — ``{category: [editor name, ...]}``: the editors a browsing UI lists,
  - `UiUtils.toggle_editor(cls, editor: str) -> bool` *(class)* — Show or hide a main-window UI component: a key of :attr:`_UI_COMPONENTS`,
  - `UiUtils.open_editor(cls, editor: str) -> Optional[str]` *(class)* — Open ``editor`` -- a friendly name from :meth:`get_editor_types`, or a
  - `UiUtils.annotate() -> Optional[str]` *(static)* — Annotate the host's way: Maya's Create > Annotation
  - `UiUtils.open_pencil() -> str` *(static)* — Open Blue Pencil, Maya's 2D sketching over the viewport
  - `UiUtils.get_tool_types(cls) -> dict` *(class)* — The friendly-name -> MEL map understood by :meth:`set_tool`.
  - `UiUtils.set_tool(cls, tool: str) -> str` *(class)* — Activate a viewport tool: a friendly name from :meth:`get_tool_types`,
  - `UiUtils.current_tool(cls) -> Optional[str]` *(class)* — The active tool: its friendly name from :meth:`get_tool_types` when
  - `UiUtils.browse_directory(start: str = '', caption: str = 'Select Folder', ok_caption: str = 'Select') -> str` *(static)* — Pick a directory in Maya's own browser (``fileDialog2 -fileMode 3``).
  - `UiUtils.open_preferences(cls, section: Optional[str] = None) -> str` *(class)* — Open Maya's own preferences: ``"general"`` (the default) the Preferences
  - `UiUtils.get_main_window()` *(static)* — Get the main Maya window as a QMainWindow instance.
  - `UiUtils.get_menu_name(qt_object_name: str) -> Optional[str]` *(static)* — Retrieve the internal Maya name of a menu given its Qt object name.
  - `UiUtils.get_panel(*args, **kwargs)` *(static)* — Returns panel and panel configuration information.
  - `UiUtils.get_model_panel(with_focus: bool = True) -> Optional[str]` *(static)* — Return a 3D model panel (viewport), suitable for commands like isolateSelect.
  - `UiUtils.main_progress_bar(size, name='progressBar#', step_amount=1)` *(static)* — # add esc key pressed return False
  - `UiUtils.list_ui_objects()` *(static)* — List all UI objects.
  - `UiUtils.clear_scrollfield_reporters()` *(static)* — Clears the contents of all cmdScrollFieldReporter UI objects in the current Maya session.
  - `UiUtils.reveal_in_outliner(objects)` *(static)* — Select *objects* and scroll the Outliner to them.
  - `UiUtils.refresh_outliners() -> int` *(static)* — Redraw every Outliner panel.
  - `UiUtils.dispatch_log_link(url, logger=None) -> bool` *(static)* — Handle ``action://`` links emitted by ``log_link()`` in a QTextBrowser.

<a id="ui_utils--calculator"></a>
### `ui_utils/calculator.py`

- **[`class CalculatorController`](mayatk/mayatk/ui_utils/calculator.py#L13)**
  - `CalculatorController.calculate(expression)` *(static)* — Safely evaluate a math expression (delegates to the shared engine).
  - `CalculatorController.get_fps_value()` *(static)*
  - `CalculatorController.get_current_time()` *(static)*
  - `CalculatorController.frames_to_sec(cls, frames)` *(class)*
  - `CalculatorController.sec_to_frames(cls, seconds)` *(class)*
  - `CalculatorController.convert_unit(value, from_unit, to_unit)` *(static)* — Convert a length value between units (delegates to the shared engine).

<a id="ui_utils--cancel_provider"></a>
### `ui_utils/cancel_provider.py`

Maya's answers to uitk's cancellation contract.

- **[`class MayaCancelProvider(CancelProvider)`](mayatk/mayatk/ui_utils/cancel_provider.py#L54)** — Maya host strategy for :class:`uitk.CancelManager`.
  - `MayaCancelProvider.report_warning(cls, message: str) -> None` *(class)*
  - `MayaCancelProvider.report_info(cls, message: str) -> None` *(class)*
  - `MayaCancelProvider.create_sources(self, scope, label: str = '')` — Maya's Esc peek first, then uitk's key-hold fallback.
  - `MayaCancelProvider.begin(self, scope, label: str = '', rollback: bool = False) -> Any` — Open the undo chunk (only when rollback was requested) and computation.
  - `MayaCancelProvider.tick(self, value: Optional[int] = None, total: Optional[int] = None, text: Optional[str] = None) -> None` — Mirror progress into Maya's own progress bar.
  - `MayaCancelProvider.end(self, token: Any, cancelled: bool = False, rollback: bool = False) -> None` — Close the computation and chunk, rolling back a cancelled run.

<a id="ui_utils--channel_box"></a>
### `ui_utils/channel_box.py`

Programmatic access to Maya's Channel Box.

- **[`class ChannelBox`](mayatk/mayatk/ui_utils/channel_box.py#L32)** — Query, select, and hook into Maya's Channel Box programmatically.
  - `ChannelBox.connect_selection_changed(cls, callback)` *(class)* — Connect *callback* to the Channel Box's Qt selection signal.
  - `ChannelBox.disconnect_selection_changed(cls, callback)` *(class)* — Disconnect a previously connected *callback*.
  - `ChannelBox.get_selected_attrs(cls, sections='all')` *(class)* — Return attribute names currently selected in the channel box.
  - `ChannelBox.get_selected_objects(cls, sections='all')` *(class)* — Return the object names associated with selected channel box attrs.
  - `ChannelBox.get_selected_plugs(cls, sections='all')` *(class)* — Return fully qualified ``node.attr`` plugs for the current selection.
  - `ChannelBox.select(cls, attr_names)` *(class)* — Select attributes in the channel box through ``channelBox -select``.
  - `ChannelBox.select_visual(cls, attr_names)` *(class)* — Select attributes and ensure the highlight is visible in the UI.
  - `ChannelBox.clear_selection(cls)` *(class)* — Deselect all attributes in the channel box.
  - `ChannelBox.add_stand_in(cls, source)` *(class)* — Register a panel that lists channels in place of the box.
  - `ChannelBox.remove_stand_in(cls, source)` *(class)* — Unregister a :meth:`add_stand_in` source;
  - `ChannelBox.stand_in_selection(cls)` *(class)* — Return the channels selected in the first stand-in that has any.
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

- [`MACRO_HOTKEY_SET`](mayatk/mayatk/ui_utils/hotkey_collisions.py#L56) — constant
- **[`class HotkeyCollisions(_HotkeyCollisionsInternal)`](mayatk/mayatk/ui_utils/hotkey_collisions.py#L225)** — HotkeyCollisions — module namespace.
  - `HotkeyCollisions.parse_qt_sequence(sequence: str) -> Optional[dict]` *(static)* — Convert a Qt key sequence string to ``cmds.hotkey`` query kwargs.
  - `HotkeyCollisions.keystring_to_token(ks: list) -> str` *(static)* — Convert an ``assignCommand`` keyString array to a Maya hotkey token.
  - `HotkeyCollisions.live_hotkey_map() -> dict` *(static)* — Return ``{runtime_command: maya_key_token}`` for the active hotkey set.
  - `HotkeyCollisions.ensure_editable_hotkey_set(name: str = MACRO_HOTKEY_SET) -> str` *(static)* — Make the *current* hotkey set editable;
  - `HotkeyCollisions.maya_collision_checker(sequence, scope, ui_name, method_name, ignore=None)` *(static)* — Check a proposed binding against Maya's active hotkey set.

<a id="ui_utils--maya_native_menus"></a>
### `ui_utils/maya_native_menus.py`

- **[`class MayaNativeMenus(pythontk.LoggingMixin)`](mayatk/mayatk/ui_utils/maya_native_menus.py#L23)** — Handles Maya's menu retrieval and embedding into UI components.
  - `MayaNativeMenus.get_menu(self, menu_key: str) -> Optional[QtWidgets.QWidget]` — Retrieve a Maya menu, populated synchronously, and return its wrapper.
  - `MayaNativeMenus.display_menu(self, menu_key: str)` — Displays the specified Maya menu in a standalone window.

<a id="ui_utils--maya_ui_handler"></a>
### `ui_utils/maya_ui_handler.py`

- **[`class MayaUiHandler(UiHandler)`](mayatk/mayatk/ui_utils/maya_ui_handler.py#L17)** — UI Handler for Maya applications.
  - `MayaUiHandler.instance(cls, switchboard: Switchboard = None, **kwargs) -> 'MayaUiHandler'` *(class)* — Return the MayaUiHandler singleton, bootstrapping if needed.
  - `MayaUiHandler.can_resolve(self, name: str) -> bool` — Recognise the native Maya menus this handler builds on demand.
  - `MayaUiHandler.get(self, name: str, reload: bool = False, **kwargs) -> 'QtWidgets.QMainWindow'` — Retrieve a UI, checking Maya menus first.

<a id="ui_utils--node_icons"></a>
### `ui_utils/node_icons.py`

Reusable helper for resolving Maya node icons at runtime.

- **[`class NodeIcons`](mayatk/mayatk/ui_utils/node_icons.py#L24)** — Resolve Maya node type icons as Qt QIcons.
  - `NodeIcons.icon_name_for_type(node_type: str) -> str` *(static)* — Return the Maya resource icon filename for a given node type.
  - `NodeIcons.icon_name_for_node(node_name: str) -> Optional[str]` *(static)* — Return the icon filename for a specific node in the scene.
  - `NodeIcons.get_icon(node_name: str, size: int = 20)` *(static)* — Return a ``QIcon`` for a Maya node, or ``None`` if unavailable.
  - `NodeIcons.audio_icon(size: int = 16)` *(static)* — The ``QIcon`` of an audio item, or ``None``: Maya's ``audio`` node
  - `NodeIcons.get_pixmap(node_name: str, size: int = 16)` *(static)* — Return a ``QPixmap`` for a Maya node, scaled to *size*.

<a id="ui_utils--style_setter--_style_setter"></a>
### `ui_utils/style_setter/_style_setter.py`

Match Maya's scriptable viewport colors to another DCC's look.

- [`STYLES_DIR`](mayatk/mayatk/ui_utils/style_setter/_style_setter.py#L62) — constant
- **[`class StyleSetter(_StyleSetterInternal)`](mayatk/mayatk/ui_utils/style_setter/_style_setter.py#L109)** — Public namespace for the style-setter helpers (``mtk.StyleSetter.set_style("Blender")`` …).
  - `StyleSetter.list_styles()` *(static)* — Names of the shipped color styles (e.g.
  - `StyleSetter.set_style(name, persist=False)` *(static)* — Switch Maya's viewport colors to the named style — a targeted overlay of just the keys
  - `StyleSetter.list_templates()` *(static)* — Ordered ``{display_name: token}`` of everything a style-selector combo offers: each shipped
  - `StyleSetter.apply_template(name, persist=False)` *(static)* — Apply a selection from :func:`list_templates` by its token — a shipped style name, applied

<a id="uv_utils--_auto_unwrap"></a>
### `uv_utils/_auto_unwrap.py`

External auto-unwrap round-trip: OBJ out, engine, OBJ back, UVs transferred.

- [`IMPORT_NAMESPACE`](mayatk/mayatk/uv_utils/_auto_unwrap.py#L24) — constant
- **[`class AutoUnwrapResult`](mayatk/mayatk/uv_utils/_auto_unwrap.py#L28)** — Per-object outcome of an :meth:`auto_unwrap` run.

<a id="uv_utils--_uv_budget"></a>
### `uv_utils/_uv_budget.py`

UV texture-budget analysis: measure the scene, plan with :class:`pythontk.UvBudget`.

- [`STACK_PRECISION`](mayatk/mayatk/uv_utils/_uv_budget.py#L76) — constant
- **[`class MeshMetrics`](mayatk/mayatk/uv_utils/_uv_budget.py#L80)** — One mesh's claim on map space, with stacked shells already collapsed.
  - `MeshMetrics.density(self) -> float` *(property)* — Texels per world unit this mesh would have on a 1x1 map.
- **[`class TextureSetInfo`](mayatk/mayatk/uv_utils/_uv_budget.py#L104)** — What one texture set is, measured -- before any planning happens.
  - `TextureSetInfo.map_size_measured(self) -> bool` *(property)* — Whether ``map_size`` came from the textures or from the caller's default.
- **[`class UvBudgetResult`](mayatk/mayatk/uv_utils/_uv_budget.py#L140)** — A measured scene plus the plan it supports.
  - `UvBudgetResult.density_is_scale(self) -> bool` *(property)* — Whether :attr:`target_density` is a multiplier, not texels per unit.
  - `UvBudgetResult.density_spread(self) -> Tuple[float, float]` *(property)* — Lowest and highest measured per-set density.
  - `UvBudgetResult.measured_sets(self) -> int` *(property)* — How many sets got their map size from real textures rather than the default.
  - `UvBudgetResult.pages(self) -> List[Tuple[int, List[str]]]` — ``(page index, item keys)`` for the chosen row -- what goes where.
  - `UvBudgetResult.report(self) -> str` — Human-readable summary: the measurement, the plan, and the alternates.

<a id="uv_utils--_uv_pack"></a>
### `uv_utils/_uv_pack.py`

The two engines behind :meth:`mayatk.UvUtils.pack_uvs`.

- [`RESIDUAL_TOLERANCE`](mayatk/mayatk/uv_utils/_uv_pack.py#L64) — constant
- **[`class PackUvsResult`](mayatk/mayatk/uv_utils/_uv_pack.py#L68)** — Per-object outcome of a :meth:`mayatk.UvUtils.pack_uvs` run.

<a id="uv_utils--_uv_utils"></a>
### `uv_utils/_uv_utils.py`

- **[`class UvUtils(pythontk.HelpMixin)`](mayatk/mayatk/uv_utils/_uv_utils.py#L24)**
  - `UvUtils.calculate_uv_padding(map_size: int, normalize: bool = False, factor: int = 256) -> float` *(static)* — The texture gutter for a given map size — Maya-side name for the ecosystem rule.
  - `UvUtils.udim_to_tile(udim: int) -> Tuple[int, int]` *(static)* — UDIM tile number to its (u, v) tile offset — Maya-side name for the
  - `UvUtils.orient_shells(objects, to_edge: bool = False) -> int` *(static)* — Rotate UV shells to run parallel with the most adjacent U or V axis of their bounding box.
  - `UvUtils.move_to_uv_space(objects, u, v, relative=True)` *(static)* — Move objects to the given u and v coordinates.
  - `UvUtils.rotate_uvs(cls, objects, angle: float) -> int` *(class)* — Rotate the UVs of *objects* by *angle* degrees, counter-clockwise, about
  - `UvUtils.straighten_uvs(cls, objects, u: bool = True, v: bool = True, angle: float = 30.0) -> int` *(class)* — Straighten the UV edges of *objects* lying within *angle* degrees of
  - `UvUtils.straighten_uv_shells(cls, objects) -> int` *(class)* — Straighten each UV shell of *objects* into a grid by unfolding it around
  - `UvUtils.distribute_uv_shells(cls, objects, axis: str = 'u', spacing: float = 0.0) -> int` *(class)* — Lay the UV shells of *objects* out in a row -- Maya's
  - `UvUtils.align_uvs(cls, objects, axis: str = 'u', mode: str = 'avg') -> int` *(class)* — Align the UVs of *objects* -- Maya's Align UVs: ``"min"`` / ``"max"`` /
  - `UvUtils.gather_uv_shells(cls, objects) -> int` *(class)* — Gather the UV shells of *objects* back into the 0-1 tile -- Maya's
  - `UvUtils.randomize_uv_shells(cls, objects, seed: Optional[int] = None) -> int` *(class)* — Randomly offset the UV shells of *objects* -- Maya's Randomize Shells,
  - `UvUtils.get_uv_bounds(objects) -> Optional[Tuple[float, float, float, float]]` *(static)* — The UV-space bounding box of *objects*, as one box over the whole input.
  - `UvUtils.get_uv_triangles(shape: str, uv_set: Optional[str] = None)` *(static)* — ``(N, 3, 2)`` array of *shape*'s UV-space triangles for *uv_set*.
  - `UvUtils.get_uv_world_triangles(shape: str, uv_set: Optional[str] = None)` *(static)* — *shape*'s UV-space triangles paired with the world-space corners they map.
  - `UvUtils.gather_to_udim(cls, objects, udim: Optional[int] = None, map_size: int = 4096) -> Optional[int]` *(class)* — Move UV shells sitting outside the target UDIM tile into it.
  - `UvUtils.get_neighbor_shell_bounds(objects) -> List[Tuple[float, float, float, float]]` *(static)* — Per-shell UV boxes that share *objects*' UV space, excluding their own.
  - `UvUtils.mirror_uvs(cls, objects, axis: str = 'u', pivot: tuple | None = None, per_shell: bool = True, preserve_position: bool = True)` *(class)* — Mirror UVs across U or V.
  - `UvUtils.get_uv_shell_sets(objects=None, returned_type='shell', whole_shells=False)` *(static)* — Get UV shells and their corresponding sets of faces.
  - `UvUtils.get_uv_pin_weights(uvs) -> List[float]` *(static)* — Pin weight of each UV in *uvs* (flat component names), in argument order.
  - `UvUtils.set_uv_pin_weights(uvs, weights) -> None` *(static)* — Set per-UV pin weights (``zip(uvs, weights)``) with one ``polyPinUV``
  - `UvUtils.pins_lifted(uvs)` *(static)* — Zero the pin weights of *uvs* inside the block, then put each one back.
  - `UvUtils.stack_similar_uv_shells(items, tolerance: float = 1.0) -> List[str]` *(static)* — Stack shells of the same topology and shape onto the first matching
  - `UvUtils.get_similar_uv_shells(reference, candidates=None, tolerance: float = 1.0, include_reference: bool = False) -> List[List[str]]` *(static)* — The UV shells that Stack Similar would stack together with *reference*'s
  - `UvUtils.get_uv_shell_border_edges(objects)` *(static)* — Get the edges that make up any UV islands of the given objects.
  - `UvUtils.get_cylinder_seam_edges(cls, mesh, sections=None, invert_seam: bool = False, cap_faces=None)` *(class)* — Identify the UV seam edges for unwrapping a smooth cylinder / tube.
  - `UvUtils.get_auto_seam_edges(cls, mesh, angle: float = 45.0, invert_seam: bool = False, taper_angle: float = 20.0, camera=None, flat_angle: float = 60.0, trim_ratio: float = 0.12)` *(class)* — Seam edges that auto-unwrap a cylinder / tube / turned mesh -- clean
  - `UvUtils.cut_cylinder_seams(cls, objects=None, angle=45.0, invert_seam=False, history=True, sew=True, taper_angle=20.0, camera=None, flat_angle=60.0, trim_ratio=0.12)` *(class)* — Cut auto UV seams for cylinder / tube unwrapping on each mesh.
  - `UvUtils.cut_uv_edges(edges, history: bool = True)` *(static)* — Cut (split) UV shells along the given edges, spanning any number of objects.
  - `UvUtils.auto_unwrap(cls, objects=None, method: str = 'hard', map_size: int = 4096, pack: Optional[bool] = None, orient: bool = True, engine_params: Optional[dict] = None)` *(class)* — Automatically unwrap meshes with an external unwrapping engine.
  - `UvUtils.pack_uvs(cls, objects=None, map_size: int = 1024, udim: int = 1001, coverage: Tuple[float, float] = (1.0, 1.0), rotate: bool = True, brute_force: bool = False, preserve_3d: bool = True, padding: Optional[float] = None, engine: str = 'xatlas', pre_rotate: int = 0, rotate_step: int = 0, rotate_min: int = 0, rotate_max: int = 0, mutations: int = 1, scale_mode: int = 2, tiles: Tuple[int, int] = (1, 1))` *(class)* — Pack existing UV shells into a UDIM tile (or a grid of tiles).
  - `UvUtils.classify_unfold3d_error(error) -> str` *(static)* — A short, human-readable reason for an Unfold3D failure.
  - `UvUtils.analyze_uv_budget(cls, objects=None, map_size: int = 4096, density: Optional[float] = None, scale: Optional[float] = None, pages: Optional[int] = None, density_from: str = 'preserve', group_by: str = 'mesh', collapse_stacked: bool = True, read_textures: bool = True, mip_levels: int = 0, padding_factor: int = 256, fill: Optional[float] = None, level: bool = False, alternates: bool = True)` *(class)* — How many texture maps these surfaces need, and at what texel density.
  - `UvUtils.unwrap_cylinder(cls, objects=None, angle=45.0, invert_seam=False, unfold=True, orient=True, map_size=4096, sew=True, taper_angle=20.0, camera=None, flat_angle=60.0, trim_ratio=0.12)` *(class)* — Auto-unwrap cylinder / tube / turned meshes: seam, then unfold flat.
  - `UvUtils.get_texel_density(objects, map_size)` *(static)* — Calculate the texel density for the given objects' faces.
  - `UvUtils.set_texel_density(cls, objects=None, density=1.0, map_size=4096)` *(class)* — Set the texel density for the given objects.
  - `UvUtils.snapshot_uv_sets(objects: Sequence[Union[str, object]], prefix: str = '_uv_snap') -> List[UvSnapshot]` *(static)* — Copy each object's active UV set into a uniquely-named backup set.
  - `UvUtils.uv_snapshot(objects: Sequence[Union[str, object]], prefix: str = '_uv_snap') -> Iterator[List[UvSnapshot]]` *(static)* — Back up each object's active UV set for the length of a block.
  - `UvUtils.restore_uv_snapshot(snapshots: Sequence[UvSnapshot]) -> None` *(static)* — Restore UVs captured by ``snapshot_uv_sets``.
  - `UvUtils.discard_uv_snapshot(snapshots: Sequence[UvSnapshot]) -> None` *(static)* — Delete the snapshot UV sets without restoring them.
  - `UvUtils.find_uv_snapshots(objects: Sequence[Union[str, object]], prefix: str = '_uv_snap', stale_only: bool = False) -> List[UvSnapshot]` *(static)* — The snapshot UV sets ``snapshot_uv_sets`` left on *objects*.
  - `UvUtils.transfer_uvs(cls, source: Union[str, object, List[Union[str, object]]], target: Union[str, object, List[Union[str, object]]], tolerance: float = 0.1, match_by_similarity: bool = True, sample_space: str = 'auto', preserve_uv_ids: bool = False) -> List[Tuple[str, str, str]]` *(class)* — Transfers UVs from source meshes to target meshes.
  - `UvUtils.transfer_uvs_to_similar(cls, source: Union[str, object], candidates: Optional[List[Union[str, object]]] = None, tolerance: float = 0.9) -> List[str]` *(class)* — Transfer UVs from one source mesh to every geometrically similar mesh.
  - `UvUtils.reorder_uv_sets(obj: str, new_order: list[str]) -> None` *(static)* — Reorder UV sets of the given object to match the specified new order.
  - `UvUtils.export_uv_layout(objects, uv_set: str = None) -> dict` *(static)* — Read each mesh's *uv_set* as a per-loop layout, for :meth:`apply_uv_layout`.
  - `UvUtils.apply_uv_layout(layouts: dict, uv_set: str = None, quiet: bool = False) -> dict` *(static)* — Write UV layouts authored in ANOTHER application onto these meshes.
  - `UvUtils.create_lightmap_uvs(cls, objects, uv_set: str = None, map_size: int = 1024, planes: int = 6, force: bool = False, freeze_history: bool = False, quiet: bool = False) -> dict` *(class)* — Ensure each mesh has a packed, non-overlapping lightmap UV set.
  - `UvUtils.remove_empty_uv_sets(objects, quiet: bool = False) -> None` *(static)* — Remove empty UV sets from the given objects.

<a id="uv_utils--rizom_bridge--_rizom_bridge"></a>
### `uv_utils/rizom_bridge/_rizom_bridge.py`

- [`APP`](mayatk/mayatk/uv_utils/rizom_bridge/_rizom_bridge.py#L37) — constant
- **[`class RizomUVBridge(pythontk.LoggingMixin, _RizomUVBridgeInternal)`](mayatk/mayatk/uv_utils/rizom_bridge/_rizom_bridge.py#L83)**
  - `RizomUVBridge.rizom_path(self)` *(property)* — Resolve the RizomUV executable path.
  - `RizomUVBridge.rizom_version(self) -> 'tuple[int, ...]'` *(property)* — The installed Rizom version, parsed from the install-dir name.
  - `RizomUVBridge.export_path(self)` *(property)* — Lazy initialization of the export path.
  - `RizomUVBridge.script_path(self)` *(property)* — Get the path to the UV script file as a POSIX string.
  - `RizomUVBridge.process_with_rizomuv(self, objects, uv_script=None, preset=None, params=None, select_objects=None, skip_instances=True)` — Run the full export -> RizomUV -> re-import workflow.
  - `RizomUVBridge.params_registry(cls)` *(class)* — The parameter registry (``parameters.Parameters``, a
  - `RizomUVBridge.referenced_keys(self, script_text: str) -> 'set[str]'` — The parameter keys *script_text* exposes on the installed RizomUV.
  - `RizomUVBridge.list_template_modes(self) -> 'list[tuple[str, str]]'` — ``[(preset, ""), ...]`` for every bundled preset the installed RizomUV runs.
  - `RizomUVBridge.run_preset(self, objects, preset: str, params=None)` — Run *preset* on *objects*: what the RizomUV Bridge's Process does.
  - `RizomUVBridge.pack_into_existing(self, objects, params=None)` — Pack *objects* into the empty space of the layout their material(s) define.
  - `RizomUVBridge.expand_by_materials(objects) -> 'tuple[list[str], list[str]]'` *(static)* — Expand *objects* to every mesh sharing their assigned materials.
  - `RizomUVBridge.send_to_rizomuv(self, objects, params=None)` — Export *objects* and open them in a fresh RizomUV session.

<a id="uv_utils--rizom_bridge--parameters"></a>
### `uv_utils/rizom_bridge/parameters.py`

Registry of user-tunable RizomUV parameters exposed to the bridge UI.

- [`PARAMS`](mayatk/mayatk/uv_utils/rizom_bridge/parameters.py#L40) — constant
- [`DERIVED_KEYS`](mayatk/mayatk/uv_utils/rizom_bridge/parameters.py#L501) — constant
- [`HOST_TOKEN_DEFAULTS`](mayatk/mayatk/uv_utils/rizom_bridge/parameters.py#L511) — constant
- [`MIN_VERSIONS`](mayatk/mayatk/uv_utils/rizom_bridge/parameters.py#L720) — constant
- [`FBX_USE_UV_SET_NAMES_MIN_VERSION`](mayatk/mayatk/uv_utils/rizom_bridge/parameters.py#L730) — constant
- **[`class Parameters(ParamRegistry)`](mayatk/mayatk/uv_utils/rizom_bridge/parameters.py#L520)** — Parameters — module namespace.
  - `Parameters.expand_includes(script_text: str) -> str` *(static)* — Expand ``__PACK_BLOCK__``-style include tokens to their partial's text.
  - `Parameters.preset_min_version(script_text: str) -> 'tuple[int, ...] | None'` *(static)* — Minimum Rizom version a preset declares, or ``None`` if ungated.
  - `Parameters.referenced_keys(cls, script_text: str) -> 'set[str]'` *(class)* — Registered keys present in *script_text* (delegates to uitk.bridge).
  - `Parameters.derived_values(values: 'dict[str, Any]') -> 'dict[str, float]'` *(static)* — Return the computed pack-gutter tokens (see :data:`DERIVED_KEYS`).
  - `Parameters.render_context(cls, values: 'dict[str, Any]') -> 'dict[str, str]'` *(class)* — Format *values* for ``StrUtils.replace_delimited`` using Lua literals.
  - `Parameters.strip_unsupported(script_text: str, version: 'tuple[int, ...]') -> str` *(static)* — Drop every line that references a placeholder requiring a newer Rizom.

<a id="uv_utils--texture_combine--texture_combine"></a>
### `uv_utils/texture_combine/texture_combine.py`

Several materials into ONE: one atlas, one material, in place -- no duplicated meshes.

- **[`class CombineInputs`](mayatk/mayatk/uv_utils/texture_combine/texture_combine.py#L75)** — What :meth:`TextureCombine.gather` read: the scene side of a combine.
- **[`class TextureCombine(pythontk.LoggingMixin, _TextureCombineInternal)`](mayatk/mayatk/uv_utils/texture_combine/texture_combine.py#L356)** — Combine the selected meshes' materials into one atlas and one material.
  - `TextureCombine.names(cls) -> List[str]` *(class)* — The combines this scene holds, oldest first.
  - `TextureCombine.entry(cls, name: str) -> Optional[Dict[str, Any]]` *(class)* — A combine's record entry (settings, rows, meshes, sources), or ``None``.
  - `TextureCombine.members(cls, name: str) -> List[str]` *(class)* — The live transforms a combine moved (every instance).
  - `TextureCombine.owner(cls, objects) -> Optional[str]` *(class)* — The combine any of *objects* belongs to, or ``None``.
  - `TextureCombine.stale(cls, name: str) -> List[str]` *(class)* — The source maps of *name* revised (or gone) since it was combined.
  - `TextureCombine.gather(self, objects=None) -> CombineInputs` — Read the islands, materials and maps of *objects* (default: selection).
  - `TextureCombine.inputs_for(self, name: str) -> CombineInputs` — A stored combine's inputs AS THEY WERE before it ran.
  - `TextureCombine.apply_rows(self, inputs: CombineInputs, rows: Sequence[Dict[str, Any]] = ()) -> None` — Set every island's scale from the override *rows* -- the most
  - `TextureCombine.plan(self, inputs: CombineInputs, *, sizing: str = 'proportional', normalize: str = 'resolution')` — Estimate the atlas for *inputs* (nothing packed) -- a
  - `TextureCombine.default_name(self, transforms: Sequence[str], fallback: str = 'combined') -> str` — What a blank name stands for: the meshes' common name, else
  - `TextureCombine.combine(self, objects=None, *, name: Optional[str] = None, rows: Sequence[Dict[str, Any]] = (), sizing: str = 'proportional', normalize: str = 'resolution', map_size: int = 4096, padding: Optional[int] = None, rotate: bool = True, supersample: int = 2, texture_padding: int = -1, output_dir: Optional[str] = None, assign_prefix: str = '', assign_suffix: Optional[str] = None, assign_shader_type: Optional[str] = None, lightmaps: bool = True, inputs: Optional[CombineInputs] = None) -> Dict[str, Any]` — Combine *objects*' materials into one atlas and one material.
  - `TextureCombine.recombine(self, name: str) -> Dict[str, Any]` — Re-read *name*'s source maps into its CURRENT layout -- a revision.
  - `TextureCombine.restore(self, name: str, forget: bool = True) -> List[str]` — Put *name*'s meshes back: original UVs and materials, exactly.
  - `TextureCombine.update(self, name: str, **changes) -> Dict[str, Any]` — Re-pack *name* with changed settings or rows, keeping its name.
  - `TextureCombine.forget(cls, name: str) -> bool` *(class)* — Drop *name*'s record entry, leaving the scene as it is.
  - `TextureCombine.leaf_name(node) -> str` *(static)* — *node*'s own name, for a label: no DAG path, no component
  - `TextureCombine.select(nodes) -> int` *(static)* — Replace the selection with those of *nodes* that exist --
  - `TextureCombine.material_faces(inputs: CombineInputs, material) -> List[str]` *(static)* — The faces of *inputs*' meshes that wear *material*, as
  - `TextureCombine.selected_faces() -> List[str]` *(static)* — The selected faces (long ``<mesh>.f[a:b]`` strings), for an island
  - `TextureCombine.consolidate_lightmaps(self, transforms: Sequence[str], name: str) -> Dict[str, Any]` — Bring the combined objects' committed lightmaps under the new material.
  - `TextureCombine.resolve_meshes(objects=None) -> List[str]` *(static)* — Mesh transforms (long names) under *objects*, order kept.

<a id="uv_utils--texture_transfer"></a>
### `uv_utils/texture_transfer.py`

Transfer a mesh's textures from one UV layout to another -- no rays, no bake.

- **[`class TextureTransfer(pythontk.LoggingMixin, _TextureTransferInternal)`](mayatk/mayatk/uv_utils/texture_transfer.py#L666)** — Move textures between UV layouts of the same mesh(es) -- see module doc.
  - `TextureTransfer.CONSTANT_ATTRS(cls) -> Dict[str, Dict[str, str]]` **DEPRECATED (remove in 0.23.0)** — The table moved to :attr:`ShaderAttributeMap.CONSTANT_ATTRS`, read
  - `TextureTransfer.shader_types() -> Dict[str, str]` *(static)* — ``{assign_shader_type: label}``: the shader types a transfer (or a
  - `TextureTransfer.meshes(objects) -> List[str]` *(static)* — Mesh transforms (long names) under *objects*: groups expand,
  - `TextureTransfer.capture_sources(cls) -> List[str]` *(class)* — The selected meshes (groups expanded) as what a caller stores to
  - `TextureTransfer.live_sources(names) -> List[str]` *(static)* — Those of the stored *names* still in the scene, order kept.
  - `TextureTransfer.auto_source(cls, meshes, stored=()) -> Tuple[str, Optional[str]]` *(class)* — Source: Auto's reading of a selection's *meshes*: ``(mode, target)``.
  - `TextureTransfer.selection_sides(cls, mode: str = 'auto', stored=()) -> Tuple[str, Optional[List[str]], List[str]]` *(class)* — Read the selection into a transfer's sides: ``(mode, source, targets)``.
  - `TextureTransfer.transfer(self, targets, source=None, *, source_uv_set: Optional[str] = None, target_uv_set: Optional[str] = None, channels: Optional[Sequence[str]] = None, size: Optional[int] = None, supersample: int = 2, padding: int = -1, output_dir: Optional[str] = None, name_format: str = '{material}_{channel}', output_name: Optional[str] = None, normal_convention: Optional[str] = None, source_mask_from_uvs: bool = True, assign: bool = False, assign_prefix: str = '', assign_suffix: Optional[str] = None, assign_shader_type: Optional[str] = None, assign_from: str = 'target', source_materials: Optional[Dict[str, Tuple[Sequence, Sequence[int]]]] = None) -> Dict[str, Dict[str, str]]` — Transfer the source material(s)' maps onto the target UV layout.
  - `TextureTransfer.default_output_dir(cls) -> str` *(class)* — Where the maps go when the caller names no directory: the scene
  - `TextureTransfer.output_base_dir() -> Optional[str]` *(static)* — The directory a RELATIVE output entry is resolved against.
  - `TextureTransfer.resolve_output_dir(cls, entry: Optional[str] = None) -> str` *(class)* — The absolute output directory for a user-typed *entry*.
  - `TextureTransfer.assign_results(self, results: Dict[str, Dict[str, str]], jobs: Dict[str, Dict[str, Any]], suffix: str = '_TRANSFER', base_name: Optional[str] = None, prefix: str = '', assign_from: str = 'target') -> Dict[str, str]` — One ``<prefix><layout><suffix>`` material per output, on its faces.
  - `TextureTransfer.topology_matches(cls, a, b) -> Tuple[bool, str]` *(class)* — ``(ok, why)`` -- same polygon vertex lists on both meshes.
  - `TextureTransfer.positions_match(cls, a, b, tolerance: float = 0.0001) -> bool` *(class)* — World-space vertices coincide (either side may be a tuple of parts).
  - `TextureTransfer.auto_source_uv_set(cls, obj) -> str` *(class)* — The UV set *obj*'s materials actually sample their textures through.
  - `TextureTransfer.correspondence(cls, target, source=None, *, source_uv_set: Optional[str] = None, target_uv_set: Optional[str] = None) -> Dict[str, Any]` *(class)* — Per-triangle ``(src_uv, dst_uv, face)`` for *target* vs *source*.
  - `TextureTransfer.face_materials(cls, obj) -> Tuple[List[str], 'np.ndarray']` *(class)* — ``(materials, per-face index into materials)`` for *obj*.
  - `TextureTransfer.material_maps(material: str) -> Dict[str, str]` *(static)* — ``{channel: absolute texture path}`` for the material's mapped slots.
  - `TextureTransfer.new_material_from(material: str) -> str` *(static)* — A fresh, editable shader modelled on *material*.
  - `TextureTransfer.material_constant(material: str, channel: str) -> Optional[Tuple[float, ...]]` *(static)* — The channel's scalar/colour value on *material*, or None.
  - `TextureTransfer.pair_by_name(targets: Sequence[str], sources: Sequence[str]) -> Dict[str, str]` *(static)* — Target -> source, by the longest matching TAIL of their DAG paths --
  - `TextureTransfer.pair_sources(cls, targets: Sequence[str], sources: Sequence[str]) -> Dict` *(class)* — Target -> its source: one mesh, or the TUPLE it was combined from.
  - `TextureTransfer.find_combined(cls, meshes: Sequence[str]) -> Optional[Tuple[str, Tuple]]` *(class)* — The mesh among *meshes* combined from ALL the others, if any.

<a id="xform_utils--_xform_utils"></a>
### `xform_utils/_xform_utils.py`

``XformUtils`` -- the public index of mayatk's transform operations.

- **[`class XformUtils(_XformUtilsInternal, pythontk.HelpMixin)`](mayatk/mayatk/xform_utils/_xform_utils.py#L54)** — Transform utilities for Maya objects.
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
  - `XformUtils.reset_pivot(objects=None, position: bool = True, orientation: bool = True) -> list` *(static)* — Reset the manipulator pivot's position and/or orientation
  - `XformUtils.center_pivot(objects=None, mode: str = 'object') -> list` *(static)* — Center the pivots -- Maya's Center Pivot -- after resetting the
  - `XformUtils.world_align_pivot(objects=None, pivot_type: str = 'object', mode: str = 'set')` *(static)* — Get or set a world-aligned pivot for the specified objects or components.
  - `XformUtils.bake_pivot(objects, position=False, orientation=False, preserve_instancing=True)` *(static)* — Bake the pivot orientation and position of the given object(s).
  - `XformUtils.transfer_pivot(cls, objects, translate: bool = False, rotate: bool = False, scale: bool = False, bake: bool = True, world_space: bool = True, mirror: str = '', select_targets_after_transfer: bool = False, preserve_instancing: bool = True)` *(class)* — Transfer the pivot from the first given object to the remaining given objects.
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
  - `XformUtils.set_rotate_order(cls, objects, order, preserve: bool = True) -> List[str]` *(class)* — Change the rotate order of transforms, keeping their pose and animation.

<a id="xform_utils--matrices"></a>
### `xform_utils/matrices.py`

Matrix utilities for Maya rigging and animation.

- [`SPACE_OBJECT`](mayatk/mayatk/xform_utils/matrices.py#L44) — constant
- [`SPACE_WORLD`](mayatk/mayatk/xform_utils/matrices.py#L45) — constant
- **[`class MatricesError(RuntimeError)`](mayatk/mayatk/xform_utils/matrices.py#L72)** — Base exception for matrix utility operations.
- **[`class Matrices(_MatrixMath, _DagTransforms, _NodeBuilders, pythontk.HelpMixin, _MatricesInternal)`](mayatk/mayatk/xform_utils/matrices.py#L1180)** — Matrix utilities for Maya rigging and animation.
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
  - `Matrices.reparent_preserving_world(node: str, new_parent: str) -> Dict[str, Any]` *(static)* — Reparent *node* under *new_parent* preserving its evaluated world
  - `Matrices.restore_reparent(record: Dict[str, Any]) -> bool` *(static)* — Reverse one :meth:`reparent_preserving_world`.
  - `Matrices.bake_world_matrix_to_transform(node: str, m: Union['MMatrix', list], reset_offset_parent_matrix: bool = True) -> None` *(static)* — Set a node's translate, rotate, and scale so its worldMatrix matches the given matrix.
  - `Matrices.freeze_to_offset_parent_matrix(node: str) -> None` *(static)* — Zero a node's translate, rotate, and scale by baking current world transform into offsetParentMatri…
  - `Matrices.pin_world_matrix(node: str) -> bool` *(static)* — Freeze *node*'s world matrix at its current value.
  - `Matrices.unpin_world_matrix(node: str) -> None` *(static)* — Undo :meth:`pin_world_matrix`: the node inherits its parent again
  - `Matrices.ensure_node(node_type: str, name: Optional[str] = None) -> str` *(static)* — Create a node of the specified type.
  - `Matrices.build_mult_matrix_chain(mats: List[str], name: str = 'mmx_chain') -> Tuple[str, str]` *(static)* — Create a multMatrix node chain that multiplies matrices and decomposes the result.
  - `Matrices.drive_with_offset_parent_matrix(driver_world: str, driven_ctl: str, name: str = 'drive_opm', offset: Optional[List[float]] = None) -> str` *(static)* — Drive a control's offsetParentMatrix from another transform's world matrix.
  - `Matrices.build_space_switch(control: str, space_parents: List[Optional[str]], attr_owner: Optional[str] = None, attr_name: str = 'space', name: str = 'space_switch', enum_labels: Optional[List[str]] = None, capture_offsets: bool = False, passthrough_default: bool = False) -> str` *(static)* — Create a multi-space switch system using blendMatrix.
  - `Matrices.build_aim_matrix(source: str, target: str, up_object: Optional[str] = None, primary_axis: Tuple[float, float, float] = (1.0, 0.0, 0.0), secondary_axis: Tuple[float, float, float] = (0.0, 1.0, 0.0), secondary_mode: str = 'align', name: str = 'aim_mx') -> str` *(static)* — Create a node-based aim constraint using aimMatrix.
  - `Matrices.build_ikfk_blend(ik_mx_attr: str, fk_mx_attr: str, parent_inv_attr: str, out_target_ctl: str, switch_attr_owner: str, switch_attr: str = 'ikFk', name: str = 'ikfk_blend') -> str` *(static)* — Create an IK/FK blend system using blendMatrix in local space.

<a id="xform_utils--pivot_watcher"></a>
### `xform_utils/pivot_watcher.py`

Real-time pivot-change notifier built on :class:`ScriptJobManager`.

- **[`class PivotWatcher(_PivotWatcherInternal)`](mayatk/mayatk/xform_utils/pivot_watcher.py#L108)** — Fire *callback* on intentional manipulator-pivot drags.
  - `PivotWatcher.owner(self) -> Any` *(property)*
  - `PivotWatcher.started(self) -> bool` *(property)*
  - `PivotWatcher.start(self) -> None` — Subscribe to the watched events (idempotent).
  - `PivotWatcher.stop(self) -> None` — Unsubscribe from all watched events (idempotent).
  - `PivotWatcher.attach_widget(self, widget) -> None` — Auto-:meth:`stop` when *widget* emits ``destroyed``.
