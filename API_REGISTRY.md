# mayatk — API Registry

_Auto-generated. Do not edit by hand. Refresh via `m3trik/scripts/generate_api_registry.py`._

_Generated: 2026-06-04_

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
- [`anim_utils/blendshape_animator/weights.py`](#anim_utils--blendshape_animator--weights) — Weight calculations and Maya-compatible precision handling for blendShape animation.
- [`anim_utils/playblast_exporter.py`](#anim_utils--playblast_exporter) — Utilities for creating playblasts and alternative preview renders in Maya.
- [`anim_utils/scale_keys.py`](#anim_utils--scale_keys) — Dedicated scale-keys module to keep AnimUtils lean and testable.
- [`anim_utils/segment_keys.py`](#anim_utils--segment_keys)
- [`anim_utils/shots/_detection.py`](#anim_utils--shots--_detection) — Shot-region detection — Maya animation-graph analysis.
- [`anim_utils/shots/_shot_apply.py`](#anim_utils--shots--_shot_apply) — Commit resolved :class:`MovePlan`\ s to the Maya scene.
- [`anim_utils/shots/_shot_plan.py`](#anim_utils--shots--_shot_plan) — Pure planning layer for multi-shot topology transformations.
- [`anim_utils/shots/_shots.py`](#anim_utils--shots--_shots) — Shared shot data model and persistent store.
- [`anim_utils/shots/shot_manifest/_shot_manifest.py`](#anim_utils--shots--shot_manifest--_shot_manifest) — Shot Manifest â€” parse structured CSVs and populate a ShotStore.
- [`anim_utils/shots/shot_manifest/behaviors/__init__.py`](#anim_utils--shots--shot_manifest--behaviors--__init__) — Behaviors — load and apply YAML keying recipes.
- [`anim_utils/shots/shot_manifest/manifest_data.py`](#anim_utils--shots--shot_manifest--manifest_data) — Constants, column layout, and pure helper functions for the Shot Manifest UI.
- [`anim_utils/shots/shot_manifest/mapping/__init__.py`](#anim_utils--shots--shot_manifest--mapping--__init__) — CSV mapping resolver — interprets JSON mapping files.
- [`anim_utils/shots/shot_manifest/range_resolver.py`](#anim_utils--shots--shot_manifest--range_resolver) — Range resolution algorithm for the Shot Manifest.
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
- [`anim_utils/smart_bake.py`](#anim_utils--smart_bake) — Smart bake module for intelligent pre-bake animation processing.
- [`anim_utils/stagger_keys.py`](#anim_utils--stagger_keys) — Dedicated stagger-keys module to keep AnimUtils lean and testable.
- [`anim_utils/unbake_keys.py`](#anim_utils--unbake_keys)
- [`audio_utils/_audio_utils.py`](#audio_utils--_audio_utils) — Unified audio system for Maya scenes.
- [`audio_utils/audio_clips/_audio_clips.py`](#audio_utils--audio_clips--_audio_clips) — Scene-wide audio event manager — thin facade over ``audio_utils``.
- [`audio_utils/audio_clips/audio_clips_slots.py`](#audio_utils--audio_clips--audio_clips_slots) — Switchboard slots for the Audio Clips UI.
- [`audio_utils/audio_clips/callbacks.py`](#audio_utils--audio_clips--callbacks) — Maya event lifecycle and hydration for Audio Clips.
- [`audio_utils/audio_clips/export_ops.py`](#audio_utils--audio_clips--export_ops) — Export operations for Audio Clips.
- [`audio_utils/batch.py`](#audio_utils--batch) — Batch orchestration — undo chunk + dirty-track buffering.
- [`audio_utils/compositor.py`](#audio_utils--compositor) — Compositor — derives DG audio nodes from keyed track events.
- [`audio_utils/migrate.py`](#audio_utils--migrate) — One-shot migration from legacy single-enum carriers to per-track schema.
- [`audio_utils/nodes.py`](#audio_utils--nodes) — Low-level DG audio node primitives.
- [`audio_utils/segments/discovery.py`](#audio_utils--segments--discovery) — Segment discovery from the per-track keyed canonical store.
- [`cam_utils/_cam_utils.py`](#cam_utils--_cam_utils)
- [`core_utils/_core_utils.py`](#core_utils--_core_utils)
- [`core_utils/components.py`](#core_utils--components)
- [`core_utils/diagnostics/animation_diag.py`](#core_utils--diagnostics--animation_diag) — Animation-curve diagnostics and optional repair helpers.
- [`core_utils/diagnostics/mesh_diag.py`](#core_utils--diagnostics--mesh_diag) — Mesh diagnostics and repair helpers.
- [`core_utils/diagnostics/scene_diag.py`](#core_utils--diagnostics--scene_diag) — Scene diagnostics and repair helpers.
- [`core_utils/diagnostics/transform_diag.py`](#core_utils--diagnostics--transform_diag)
- [`core_utils/diagnostics/uv_diag.py`](#core_utils--diagnostics--uv_diag) — UV diagnostics and repair helpers.
- [`core_utils/instancing/assembly_reconstructor.py`](#core_utils--instancing--assembly_reconstructor) — Logic for separating and reassembling mesh assemblies.
- [`core_utils/instancing/auto_instancer.py`](#core_utils--instancing--auto_instancer) — Scene auto-instancer prototype.
- [`core_utils/instancing/geometry_matcher.py`](#core_utils--instancing--geometry_matcher) — Geometry analysis and matching logic for AutoInstancer.
- [`core_utils/instancing/instancing_strategy.py`](#core_utils--instancing--instancing_strategy) — Instancing strategy logic for AutoInstancer.
- [`core_utils/mash.py`](#core_utils--mash)
- [`core_utils/preview.py`](#core_utils--preview) — Hermetic preview with replay-on-commit (H1 design).
- [`core_utils/preview_old.py`](#core_utils--preview_old)
- [`core_utils/script_job_manager.py`](#core_utils--script_job_manager) — Centralized Maya event subscription manager.
- [`display_utils/_display_utils.py`](#display_utils--_display_utils)
- [`display_utils/color_manager.py`](#display_utils--color_manager)
- [`display_utils/exploded_view.py`](#display_utils--exploded_view)
- [`edit_utils/_edit_utils.py`](#edit_utils--_edit_utils)
- [`edit_utils/bevel.py`](#edit_utils--bevel)
- [`edit_utils/bridge.py`](#edit_utils--bridge)
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
- [`edit_utils/selection.py`](#edit_utils--selection)
- [`edit_utils/snap.py`](#edit_utils--snap)
- [`env_utils/_env_utils.py`](#env_utils--_env_utils)
- [`env_utils/devtools.py`](#env_utils--devtools)
- [`env_utils/fbx_utils.py`](#env_utils--fbx_utils)
- [`env_utils/hierarchy_manager/_hierarchy_manager.py`](#env_utils--hierarchy_manager--_hierarchy_manager)
- [`env_utils/hierarchy_manager/hierarchy_manager_slots.py`](#env_utils--hierarchy_manager--hierarchy_manager_slots)
- [`env_utils/hierarchy_manager/hierarchy_sidecar.py`](#env_utils--hierarchy_manager--hierarchy_sidecar) — Hierarchy sidecar manifest management.
- [`env_utils/hierarchy_manager/tree_renderer.py`](#env_utils--hierarchy_manager--tree_renderer) — Tree rendering, formatting, and selection management for the hierarchy manager UI.
- [`env_utils/hierarchy_manager/tree_utils.py`](#env_utils--hierarchy_manager--tree_utils) — Tree widget utilities for hierarchy manager UI operations.
- [`env_utils/maya_connection.py`](#env_utils--maya_connection) — Maya Connection Module
- [`env_utils/namespace_sandbox.py`](#env_utils--namespace_sandbox)
- [`env_utils/reference_manager.py`](#env_utils--reference_manager)
- [`env_utils/scene_exporter/_scene_exporter.py`](#env_utils--scene_exporter--_scene_exporter)
- [`env_utils/scene_exporter/task_factory.py`](#env_utils--scene_exporter--task_factory)
- [`env_utils/scene_exporter/task_manager.py`](#env_utils--scene_exporter--task_manager)
- [`env_utils/script_output.py`](#env_utils--script_output)
- [`env_utils/workspace_manager.py`](#env_utils--workspace_manager)
- [`env_utils/workspace_map.py`](#env_utils--workspace_map)
- [`light_utils/_light_utils.py`](#light_utils--_light_utils)
- [`light_utils/bake_lighting.py`](#light_utils--bake_lighting) — Bake Maya scene lighting into per-object texture files.
- [`light_utils/hdr_manager.py`](#light_utils--hdr_manager) — Arnold HDR environment manager.
- [`mat_utils/_affix_mode.py`](#mat_utils--_affix_mode) — Shared affix-mode option-box helper for mat_utils slot files.
- [`mat_utils/_mat_utils.py`](#mat_utils--_mat_utils)
- [`mat_utils/game_shader.py`](#mat_utils--game_shader)
- [`mat_utils/image_to_plane/_image_to_plane.py`](#mat_utils--image_to_plane--_image_to_plane) — Map image files to textured polygon planes in Maya.
- [`mat_utils/image_to_plane/image_to_plane_slots.py`](#mat_utils--image_to_plane--image_to_plane_slots) — Switchboard slots for the Image to Plane UI.
- [`mat_utils/marmoset_bridge/_marmoset_bridge.py`](#mat_utils--marmoset_bridge--_marmoset_bridge)
- [`mat_utils/marmoset_bridge/_toolbag_helpers.py`](#mat_utils--marmoset_bridge--_toolbag_helpers) — Shared helpers for Marmoset Toolbag template scripts.
- [`mat_utils/marmoset_bridge/marmoset_bridge_slots.py`](#mat_utils--marmoset_bridge--marmoset_bridge_slots) — Slots for the Marmoset Toolbag bridge panel.
- [`mat_utils/marmoset_bridge/marmoset_rpc/connection.py`](#mat_utils--marmoset_bridge--marmoset_rpc--connection) — Maya-side JSON-RPC client for the marmoset_rpc Toolbag plugin.
- [`mat_utils/marmoset_bridge/marmoset_rpc/installer.py`](#mat_utils--marmoset_bridge--marmoset_rpc--installer) — Install the marmoset_rpc plugin into Toolbag's user plugin folder.
- [`mat_utils/marmoset_bridge/marmoset_rpc/job.py`](#mat_utils--marmoset_bridge--marmoset_rpc--job) — One-shot batch pipeline for the marmoset_rpc bridge.
- [`mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/main_thread.py`](#mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--main_thread) — Main-thread marshalling for ops that touch Toolbag's API.
- [`mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/scene_ops.py`](#mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--ops--scene_ops) — Scene-inspection ops.
- [`mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py`](#mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--ops--system_ops) — System-level ops: heartbeat, introspection, Toolbag version.
- [`mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/registry.py`](#mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--registry) — Op registry for the marmoset_rpc plugin.
- [`mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/server.py`](#mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--server) — HTTP JSON-RPC server for the marmoset_rpc plugin.
- [`mat_utils/marmoset_bridge/parameters.py`](#mat_utils--marmoset_bridge--parameters) — Registry of user-tunable Marmoset Toolbag parameters exposed to the bridge UI.
- [`mat_utils/marmoset_bridge/templates/bake.py`](#mat_utils--marmoset_bridge--templates--bake) — Bake high-poly detail into a low-poly target via Marmoset Toolbag.
- [`mat_utils/marmoset_bridge/templates/import.py`](#mat_utils--marmoset_bridge--templates--import) — Open the FBX in Toolbag and wire materials from the Maya manifest.
- [`mat_utils/marmoset_bridge/templates/lookdev.py`](#mat_utils--marmoset_bridge--templates--lookdev) — Open the FBX in Toolbag, apply a Sky preset, and frame the model.
- [`mat_utils/mat_manifest.py`](#mat_utils--mat_manifest)
- [`mat_utils/mat_snapshot.py`](#mat_utils--mat_snapshot) — Lightweight material state snapshot and restore.
- [`mat_utils/mat_transfer.py`](#mat_utils--mat_transfer)
- [`mat_utils/mat_updater.py`](#mat_utils--mat_updater)
- [`mat_utils/render_opacity/_render_opacity.py`](#mat_utils--render_opacity--_render_opacity)
- [`mat_utils/render_opacity/attribute_mode.py`](#mat_utils--render_opacity--attribute_mode)
- [`mat_utils/render_opacity/material_mode.py`](#mat_utils--render_opacity--material_mode)
- [`mat_utils/render_opacity/render_opacity_slots.py`](#mat_utils--render_opacity--render_opacity_slots) — Switchboard slots for the Render Opacity UI.
- [`mat_utils/shader_attribute_map.py`](#mat_utils--shader_attribute_map)
- [`mat_utils/shader_remapper.py`](#mat_utils--shader_remapper)
- [`mat_utils/shader_templates/_shader_templates.py`](#mat_utils--shader_templates--_shader_templates)
- [`mat_utils/substance_bridge/_substance_bridge.py`](#mat_utils--substance_bridge--_substance_bridge) — Substance 3D Painter bridge -- export Maya selection and hand off to Painter.
- [`mat_utils/substance_bridge/connection.py`](#mat_utils--substance_bridge--connection) — Substance 3D Painter connection module.
- [`mat_utils/substance_bridge/parameters.py`](#mat_utils--substance_bridge--parameters) — Registry of user-tunable Substance Painter parameters exposed to the bridge UI.
- [`mat_utils/substance_bridge/substance_bridge_slots.py`](#mat_utils--substance_bridge--substance_bridge_slots) — Slots for the Substance Painter bridge panel.
- [`mat_utils/substance_bridge/substance_rpc/client.py`](#mat_utils--substance_bridge--substance_rpc--client) — JSON-RPC 2.0 client for a Painter-side Python plugin.
- [`mat_utils/texture_path_editor.py`](#mat_utils--texture_path_editor)
- [`node_utils/_node_utils.py`](#node_utils--_node_utils)
- [`node_utils/attributes/_attributes.py`](#node_utils--attributes--_attributes) — Consolidated attribute utilities for Maya.
- [`node_utils/attributes/channels/__init__.py`](#node_utils--attributes--channels--__init__) — Channels — Switchboard UI for inspecting and editing Maya attributes.
- [`node_utils/attributes/channels/_channels.py`](#node_utils--attributes--channels--_channels) — Channels — Maya attribute query / mutation logic.
- [`node_utils/attributes/channels/channels_slots.py`](#node_utils--attributes--channels--channels_slots) — UI slots for the Channels UI.
- [`node_utils/data_nodes.py`](#node_utils--data_nodes)
- [`nurbs_utils/_nurbs_utils.py`](#nurbs_utils--_nurbs_utils)
- [`nurbs_utils/image_tracer.py`](#nurbs_utils--image_tracer)
- [`rig_utils/_rig_utils.py`](#rig_utils--_rig_utils)
- [`rig_utils/controls.py`](#rig_utils--controls)
- [`rig_utils/shadow_rig.py`](#rig_utils--shadow_rig)
- [`rig_utils/telescope_rig.py`](#rig_utils--telescope_rig)
- [`rig_utils/tube_rig.py`](#rig_utils--tube_rig)
- [`rig_utils/wheel_rig.py`](#rig_utils--wheel_rig)
- [`ui_utils/_ui_utils.py`](#ui_utils--_ui_utils)
- [`ui_utils/calculator.py`](#ui_utils--calculator)
- [`ui_utils/channel_box.py`](#ui_utils--channel_box) — Programmatic access to Maya's Channel Box.
- [`ui_utils/hotkey_collisions.py`](#ui_utils--hotkey_collisions) — Maya hotkey collision checker for the uitk HotkeyEditor.
- [`ui_utils/maya_bridge_slots.py`](#ui_utils--maya_bridge_slots) — Maya-flavored :class:`BridgeSlotsBase` -- adds Maya-side defaults.
- [`ui_utils/maya_native_menus.py`](#ui_utils--maya_native_menus)
- [`ui_utils/maya_ui_handler.py`](#ui_utils--maya_ui_handler)
- [`ui_utils/node_icons.py`](#ui_utils--node_icons) — Reusable helper for resolving Maya node icons at runtime.
- [`uv_utils/_uv_utils.py`](#uv_utils--_uv_utils)
- [`uv_utils/rizom_bridge/_rizom_bridge.py`](#uv_utils--rizom_bridge--_rizom_bridge)
- [`uv_utils/rizom_bridge/parameters.py`](#uv_utils--rizom_bridge--parameters) — Registry of user-tunable RizomUV parameters exposed to the bridge UI.
- [`uv_utils/rizom_bridge/rizom_bridge_slots.py`](#uv_utils--rizom_bridge--rizom_bridge_slots) — Slots for the RizomUV bridge panel.
- [`xform_utils/_xform_utils.py`](#xform_utils--_xform_utils)
- [`xform_utils/matrices.py`](#xform_utils--matrices) — Matrix utilities for Maya rigging and animation.
- [`xform_utils/pivot_watcher.py`](#xform_utils--pivot_watcher) — Real-time pivot-change notifier built on :class:`ScriptJobManager`.

---

<a id="anim_utils--_anim_utils"></a>
### `anim_utils/_anim_utils.py`

- **[`class AnimUtils(_AnimUtilsMixin, ptk.HelpMixin)`](mayatk/mayatk/anim_utils/_anim_utils.py#L551)** — Animation utilities for Maya.
  - `AnimUtils.bake(cls, objects: Union[str, List[Union[str, str]]], attributes: Optional[Union[str, List[str]]] = None, time_range: Optional[Tuple[float, float]] = None, sample_by: float = 1.0, preserve_outside_keys: bool = True, simulation: bool = False, destination_layer: Optional[str] = None, remove_baked_attr_from_layer: bool = False, bake_on_override_layer: bool = False, minimize_rotation: bool = True, sparse_anim_curve_bake: bool = False, disable_implicit_control: bool = True, control_points: bool = False, shape: bool = False, only_keyed: bool = False) -> List[str]` *(class)* — Bake animation on specified objects and attributes with smart grouping.
  - `AnimUtils.bake_objects(cls, objects: List[Union[str, str]], attributes: Optional[List[str]] = None, time_range: Optional[Tuple[float, float]] = None, sample_by: float = 1.0, preserve_outside_keys: bool = True, simulation: bool = False, destination_layer: Optional[str] = None, remove_baked_attr_from_layer: bool = False, bake_on_override_layer: bool = False, minimize_rotation: bool = True, sparse_anim_curve_bake: bool = False, disable_implicit_control: bool = True, control_points: bool = False, shape: bool = False) -> List[str]` *(class)* — Legacy alias for bake().
  - `AnimUtils.objects_to_curves(objects: Union[str, str, List[Union[str, str]]], recursive: bool = False, as_strings: bool = False) -> Union[List[str], List[str]]` *(static)* — Converts objects into a list of animation curves.
  - `AnimUtils.get_anim_curves(cls, objects: Optional[List[str]] = None, selected_keys_only: bool = False, recursive: bool = False) -> List[str]` *(class)* — Get animation curves from objects, selected keys, or all scene curves.
  - `AnimUtils.get_static_curves(cls, objects: List[str], value_tolerance: float = 1e-05, recursive: bool = False, as_strings: bool = False) -> Union[List[str], List[str]]` *(class)* — Detects static curves (curves with constant values) that are safe
  - `AnimUtils.get_redundant_flat_keys(cls, objects: List[str], value_tolerance: float = 1e-05, remove: bool = False, recursive: bool = False, as_strings: bool = False) -> List[Tuple[Any, List[float]]]` *(class)* — Detects redundant flat keys in curves and optionally deletes them.
  - `AnimUtils.simplify_curve(cls, objects: List[str], value_tolerance: float = 0.001, time_tolerance: float = 0.001, recursive: bool = False, as_strings: bool = False) -> Union[List[str], List[str]]` *(class)* — Simplify curves by removing keys that don't contribute to shape.
  - `AnimUtils.repair_corrupted_curves(cls, objects: Optional[Union[str, str, List[Union[str, str]]]] = None, recursive: bool = True, delete_corrupted: bool = False, fix_infinite: bool = True, fix_invalid_times: bool = True, time_range_threshold: float = 1000000.0, value_threshold: float = 1000000.0, quiet: bool = False) -> Dict[str, Any]` *(class)* — Legacy wrapper maintained for backwards compatibility.
  - `AnimUtils.optimize_keys(cls, objects: Union[str, str, List[Union[str, str]]], value_tolerance: float = 0.001, time_tolerance: float = 0.001, remove_flat_keys: bool = True, remove_static_curves: bool = True, simplify_keys: bool = False, recursive: bool = True, quiet: bool = False, stats: Optional[dict] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> List[str]` *(class)* — Optimize animation keys for the given objects by removing static curves,
  - `AnimUtils.get_keyframe_times(sources: Union[str, List[str]], mode: str = 'all', from_curves: Optional[bool] = None, as_range: bool = False, time_range: Optional[Tuple[float, float]] = None) -> Union[List[float], Tuple[float, float], None]` *(static)* — Get keyframe times from objects or curves with flexible filtering options.
  - `AnimUtils.get_driver_animation_range(node: str, driver_type: str = 'auto') -> List[float]` *(static)* — Get keyframe times from a driver node's animation or its targets.
  - `AnimUtils.get_tangent_info(attr_name: str, time: float) -> Dict[str, Any]` *(static)* — Get tangent information (types, angles, and weights) for a given attribute at a specific time.
  - `AnimUtils.set_tangent_info(attr_name: str, time: float, tangent_info: Dict[str, Any]) -> None` *(static)* — Restore tangent information on a keyframe.
  - `AnimUtils.step_keys(objects=None, keys=None, tangent: str = 'out', resolution_order: Optional[Tuple[str, ...]] = None) -> dict` *(static)* — Set stepped tangents on animation keys.
  - `AnimUtils.set_current_frame(time: Optional[float] = None, update: bool = True, relative: bool = False, snap_mode: Optional[str] = None, invert_snap: bool = False) -> float` *(static)* — Set the current frame on the timeslider with optional snapping.
  - `AnimUtils.move_keys_to_frame(objects=None, frame=None, time_range=None, selected_keys_only=False, retain_spacing=False, channel_box_attrs_only=False, align: str = 'auto')` *(static)* — Move keyframes to the given frame with comprehensive control options.
  - `AnimUtils.set_keys_for_attributes(objects, target_times=None, refresh_channel_box=False, **kwargs)` *(static)* — Sets keyframes for the specified attributes on given objects at given times.
  - `AnimUtils.filter_objects_with_keys(objects: Optional[Union[str, List[str]]] = None, keys: Optional[List[str]] = None) -> List[object]` *(static)* — Filter the given objects for those with specific keys set.
  - `AnimUtils.adjust_key_spacing(cls, objects: Optional[List[str]] = None, spacing: int = 1, time: Optional[int] = 0, relative: bool = True, preserve_keys: bool = False, selected_keys_only: bool = False, exact_gap: bool = False, prevent_collisions: bool = True)` *(class)* — Adjusts the spacing between keyframes for specified objects at a given time,
  - `AnimUtils.add_intermediate_keys(objects: Union[str, str, List[Union[str, str]]], time_range: Optional[Union[int, Tuple[int, int]]] = None, percent: Optional[float] = None, include_flat: bool = False, ignore: Union[str, List[str], None] = None) -> None` *(static)* — Keys selected or animated attributes on given object(s) within a time range.
  - `AnimUtils.remove_intermediate_keys(objects: Union[str, str, List[Union[str, str]]], time_range: Optional[Union[int, Tuple[int, int]]] = None, ignore: Union[str, List[str], None] = None) -> int` *(static)* — Removes all intermediate keyframes, keeping only the first and last key on each attribute.
  - `AnimUtils.invert_keys(time=None, relative=True, delete_original=False, mode='horizontal', value_pivot=0.0)` *(static)* — Invert keyframes around the last key, preferring selected keys but falling back to all keys.
  - `AnimUtils.align_selected_keyframes(objects: Optional[List[Union[str, str]]] = None, target_frame: Optional[float] = None, use_earliest: bool = True) -> bool` *(static)* — Aligns the starting keyframes of selected keyframes in the graph editor across multiple objects.
  - `AnimUtils.set_visibility_keys(objects: Optional[List[Union[str, str]]] = None, visible: bool = True, when: str = 'start', offset: int = 0, group_overlapping: bool = False) -> int` *(static)* — Sets visibility keyframes for objects with options for timing and grouping.
  - `AnimUtils.snap_keys_to_frames(objects: Optional[List[Union[str, str]]] = None, method: str = 'nearest', selected_only: bool = False, time_range: Optional[Tuple[float, float]] = None) -> int` *(static)* — Snaps keyframes with decimal time values to whole frame numbers.
  - `AnimUtils.transfer_keyframes(cls, objects: List[Union[str, object]], relative: bool = False, transfer_tangents: bool = False, optimize: bool = False)` *(class)* — Transfer keyframes from the first selected object to the subsequent objects.
  - `AnimUtils.parse_time_range(time: Union[None, int, str, Tuple, List], recursive_callback: Optional[callable] = None) -> Union[Tuple[float, float], None, List]` *(static)* — Parse time specification into a time range tuple for keyframe operations.
  - `AnimUtils.delete_keys(objects=None, *attributes, time=None, channel_box_only=False)` *(static)* — Deletes keyframes for specified attributes on given objects, optionally within a time range.
  - `AnimUtils.select_keys(objects: Optional[List[Union[str, str]]] = None, *attributes: str, time: Union[None, int, str, Tuple, List] = None, channel_box_only: bool = False, add_to_selection: bool = False) -> int` *(static)* — Selects keyframes for specified attributes on given objects, optionally within a time range.
  - `AnimUtils.get_frame_ranges(objects: List[str], precision: Optional[int] = None, gap: Optional[int] = None) -> Dict[str, List[Tuple[int, int]]]` *(static)* — Calculate frame ranges for a list of objects based on their keyframes.
  - `AnimUtils.get_tied_keyframes(objects: Optional[List[str]] = None, tolerance: float = 1e-05) -> Dict[str, Dict[str, List[float]]]` *(static)* — Detects tied (bookend) keyframes for given objects.
  - `AnimUtils.tie_keyframes(objects: List[str] = None, absolute: bool = False, padding: int = 0, custom_range: Optional[Tuple[float, float]] = None)` *(static)* — Ties the keyframes of all given objects (or all keyed objects in the scene if none are provided)
  - `AnimUtils.untie_keyframes(objects: List[str] = None, absolute: bool = False)` *(static)* — Removes bookend keyframes added by tie_keyframes, but preserves genuine animation keys.
  - `AnimUtils.create_animation_layer(name: str = 'AnimLayer', override: bool = True, additive: bool = False, attributes: Optional[List[str]] = None, objects: Optional[List[str]] = None, weight: float = 1.0, mute: bool = False, solo: bool = False, lock: bool = False, preferred: bool = True, parent: Optional[str] = None, unique_name: bool = True, timestamp_suffix: bool = False, color: Optional[Tuple[float, float, float]] = None) -> str` *(static)* — Create an animation layer with flexible configuration options.
  - `AnimUtils.get_animation_layers(include_base: bool = False, muted_only: bool = False, active_only: bool = False) -> List[str]` *(static)* — Get all animation layers in the scene.
  - `AnimUtils.copy_keys(objects=None, mode: str = 'auto', resolution_order: Optional[Tuple[str, ...]] = None, tangent_detail: bool = False) -> Dict[str, Dict[str, Any]]` *(static)* — Copy attribute values from objects for later pasting as keys.
  - `AnimUtils.paste_keys(objects=None, copied_data: Optional[Dict[str, Dict[str, Any]]] = None, target_time=None, match_source: bool = True, refresh_channel_box: bool = True, **kwargs) -> int` *(static)* — Paste previously copied attribute values as keyframes.
  - `AnimUtils.delete_animation_layer(layer: str, merge_to_base: bool = False) -> bool` *(static)* — Delete an animation layer.
  - `AnimUtils.fit_playback_range(objects=None, padding: float = 0) -> bool` *(static)* — Set the playback range to encompass keyframes on all (or given) scene objects.

<a id="anim_utils--blendshape_animator--_blendshape_animator"></a>
### `anim_utils/blendshape_animator/_blendshape_animator.py`

Main workflow facade for blendShape morph-animation creation, editing, and export.

- **[`class BlendshapeAnimator(ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/blendshape_animator/_blendshape_animator.py#L23)** — Main workflow facade for blendShape animations.
  - `BlendshapeAnimator.create(self, base_mesh: Optional[str] = None, target_mesh: Optional[str] = None, start_frame: int = 5500, end_frame: int = 5800, name: str = 'morph', test_setup: bool = True) -> bool` — Set up basic morph animation between two meshes.
  - `BlendshapeAnimator.edit_weight_based(self, weights: Optional[List[float]] = None, count: int = 3, weight_range: Tuple[float, float] = (0.0, 1.0)) -> List[Target]` — Create tweens at specific weights or evenly spaced.
  - `BlendshapeAnimator.edit_frame_based(self, frames: Optional[List[int]] = None, target_frame: Optional[int] = None) -> List[Target]` — Create tweens at specific animation frames.
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

- **[`class BlendshapeAnimatorSlots(BlendshapeAnimator)`](mayatk/mayatk/anim_utils/blendshape_animator/blendshape_animator_slots.py#L57)** — Controller wiring blendshape_animator.ui to the BlendshapeAnimator domain class.
  - `BlendshapeAnimatorSlots.header_init(self, widget) -> None` — Configure header buttons + about menu.
  - `BlendshapeAnimatorSlots.b000_init(self, widget) -> None` — Create Setup button — option_box exposes alternative entrypoints.
  - `BlendshapeAnimatorSlots.b000(self, widget) -> None` — Create Setup.
  - `BlendshapeAnimatorSlots.cmb000_init(self, widget) -> None` — Populate the edit-mode combo.
  - `BlendshapeAnimatorSlots.le001_init(self, widget) -> None` — CSV weights field — option_box menu offers preset lists.
  - `BlendshapeAnimatorSlots.b001_init(self, widget) -> None` — Add Tweens — option_box exposes count + group / prefix overrides.
  - `BlendshapeAnimatorSlots.b001(self, widget) -> None` — Add Tweens — dispatches by mode.
  - `BlendshapeAnimatorSlots.b003(self, widget) -> None` — Diagnose Topology.
  - `BlendshapeAnimatorSlots.b004_init(self, widget) -> None` — Cleanup Topology Mismatches — option_box for the two flags.
  - `BlendshapeAnimatorSlots.b004(self, widget) -> None`
  - `BlendshapeAnimatorSlots.b005(self, widget) -> None` — Recover Animation.
  - `BlendshapeAnimatorSlots.b006_init(self, widget) -> None` — Apply All Edits — option_box for skip_duplicates, validate_topology.
  - `BlendshapeAnimatorSlots.b006(self, widget) -> None` — Apply All Edits — bulk apply with optional flags from the option_box.
  - `BlendshapeAnimatorSlots.b007(self, widget) -> None` — Remove Target Mesh.
  - `BlendshapeAnimatorSlots.b008_init(self, widget) -> None` — Finalize for Export — option_box for the four boolean flags.
  - `BlendshapeAnimatorSlots.b008(self, widget) -> None`

<a id="anim_utils--blendshape_animator--creator"></a>
### `anim_utils/blendshape_animator/creator.py`

Creates in-between target meshes for custom blendShape animation curves.

- **[`class Creator(ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/blendshape_animator/creator.py#L20)** — Creates in-between target meshes for custom animation curves.
  - `Creator.create_weight_based_tweens(self, weights: List[float], group_name: str = '_morphInbetweens_GRP', name_prefix: str = 'morph_ib') -> List[Target]` — Create tween meshes at specific weight values.
  - `Creator.create_frame_based_tween(self, target_frame: int) -> Optional[Target]` — Create a tween mesh at a specific animation frame.
  - `Creator.tag_tween_mesh(self, mesh: str, weight: float, target_frame: Optional[int] = None) -> None` — Add metadata attributes to ``mesh``.
  - `Creator.get_existing_weights(self) -> Set[float]` — Return all in-between weights known for the current blendShape.
  - `Creator.find_nearby_weight(self, target_weight: float, existing_weights: Set[float], tolerance: float = 0.01) -> Optional[float]` — Find a nearby weight that doesn't conflict with existing weights.

<a id="anim_utils--blendshape_animator--helpers"></a>
### `anim_utils/blendshape_animator/helpers.py`

Shared helpers internal to the blendshape_animator subpackage.

- [`list_history(node: str, type_filter: Optional[str] = None) -> List[str]`](mayatk/mayatk/anim_utils/blendshape_animator/helpers.py#L12) — List the construction history of a node, optionally filtered by node type.

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

- **[`class Recovery(ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/blendshape_animator/recovery.py#L17)** — Utilities for recovering from corrupted blendShape setups.
  - `Recovery.fix_corrupted_animation(cls, base_mesh: str, target_mesh: str) -> bool` *(class)* — Rebuild corrupted blendShape animation.
  - `Recovery.recover_with_targets(cls, base_mesh: str, target_mesh: str) -> bool` *(class)* — Complete recovery: fix animation AND restore tween customizations.

<a id="anim_utils--blendshape_animator--target"></a>
### `anim_utils/blendshape_animator/target.py`

Tween mesh wrappers and registry for blendShape in-between targets.

- **[`class Target`](mayatk/mayatk/anim_utils/blendshape_animator/target.py#L18)** — Represents a single target/in-between target mesh.
  - `Target.weight(self) -> float` *(property)* — Get the weight value for this tween.
  - `Target.blendshape_name(self) -> str` *(property)* — Get the blendShape node name this tween targets.
  - `Target.base_mesh_name(self) -> str` *(property)* — Get the base mesh name this tween applies to.
  - `Target.target_frame(self) -> Optional[int]` *(property)* — Get target frame if this tween was created from a specific frame.
  - `Target.update_references(self, new_blendshape: str, new_base_mesh: str) -> None` — Update this tween's references to new blendShape/base mesh.
- **[`class Targets(ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/blendshape_animator/target.py#L66)** — Manages collections of tween meshes.
  - `Targets.find_all_targets(cls) -> List[Target]` *(class)* — Find all tween meshes in the scene (deduplicated).
  - `Targets.group_by_weight(cls, tweens: List[Target]) -> Dict[float, List[Target]]` *(class)* — Group tweens by weight value, handling duplicates.
  - `Targets.update_all_references(cls, new_blendshape: str, new_base_mesh: str) -> int` *(class)* — Update all tween mesh references to new nodes.

<a id="anim_utils--blendshape_animator--validator"></a>
### `anim_utils/blendshape_animator/validator.py`

Mesh and blendShape validation for blendShape animation setup.

- **[`class Validator(ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/blendshape_animator/validator.py#L14)** — Handles validation of meshes and blendShape setups.
  - `Validator.validate_meshes(cls, mesh1: str, mesh2: str) -> bool` *(class)* — Validate that both objects are compatible meshes.
  - `Validator.validate_blendshape(cls, blendshape: str) -> bool` *(class)* — Validate blendShape node configuration.

<a id="anim_utils--blendshape_animator--weights"></a>
### `anim_utils/blendshape_animator/weights.py`

Weight calculations and Maya-compatible precision handling for blendShape animation.

- **[`class Weights`](mayatk/mayatk/anim_utils/blendshape_animator/weights.py#L7)** — Handles weight calculations and Maya's precision requirements.
  - `Weights.round_weight(cls, weight: float) -> float` *(class)* — Round weight to Maya-compatible precision.
  - `Weights.frame_to_weight(cls, frame: int, start_frame: int, end_frame: int) -> float` *(class)* — Convert frame number to blendShape weight.
  - `Weights.generate_weights(cls, count: int, weight_range: Tuple[float, float] = (0.0, 1.0), include_endpoints: bool = False) -> List[float]` *(class)* — Generate ``count`` evenly spaced weights within ``weight_range``.

<a id="anim_utils--playblast_exporter"></a>
### `anim_utils/playblast_exporter.py`

Utilities for creating playblasts and alternative preview renders in Maya.

- **[`class PlayblastExporter`](mayatk/mayatk/anim_utils/playblast_exporter.py#L22)** — High-level helper for producing playblast outputs and Arnold previews.
  - `PlayblastExporter.scene_name(self) -> str` *(property)*
  - `PlayblastExporter.create_playblast(self, filepath: Optional[str] = None, start_frame: Optional[int] = None, end_frame: Optional[int] = None, camera_name: Optional[str] = None, **kwargs: Any) -> str` — Create a playblast using Maya's viewport capture.
  - `PlayblastExporter.render_with_arnold(self, output_dir: str, start_frame: Optional[int] = None, end_frame: Optional[int] = None, camera_name: Optional[str] = None, prefix: Optional[str] = None, frame_padding: int = 4, render_layer: Optional[str] = None, **kwargs: Any) -> List[str]` — Render a frame range using Arnold.
  - `PlayblastExporter.export_variations(self, output_path: str, base_kwargs: Optional[Dict[str, Any]] = None, scene_name: Optional[str] = None, variations: Optional[List[Dict[str, Any]]] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> List[Dict[str, Any]]` — Produce multiple playblast outputs (formats, sequences, Arnold).

<a id="anim_utils--scale_keys"></a>
### `anim_utils/scale_keys.py`

Dedicated scale-keys module to keep AnimUtils lean and testable.

- **[`class ScaleKeys`](mayatk/mayatk/anim_utils/scale_keys.py#L19)** — Encapsulates scale_keys logic for clarity and focused testing.
  - `ScaleKeys.execute(self) -> int`
  - `ScaleKeys.scale_keys(cls, **kwargs) -> int` *(class)* — Scale keyframes uniformly or via motion-aware retiming.

<a id="anim_utils--segment_keys"></a>
### `anim_utils/segment_keys.py`

- **[`class SegmentKeysInfo`](mayatk/mayatk/anim_utils/segment_keys.py#L27)** — Mixin for reporting animation segment information.
  - `SegmentKeysInfo.get_time_ranges(segments: List[Dict[str, Any]]) -> List[Tuple[str, float, float]]` *(static)* — Extract time ranges from segment data.
  - `SegmentKeysInfo.print_time_ranges(cls, source: Union[List[Dict[str, Any]], List[Tuple[str, float, float]]], header: Optional[str] = None, per_segment: bool = False, object_fmt: Optional[str] = None, segment_fmt: Optional[str] = None, by_time: bool = False, csv_output: bool = False)` *(class)* — Print formatted time ranges to stdout.
  - `SegmentKeysInfo.format_time_ranges_text(cls, source: Union[List[Dict[str, Any]], List[Tuple[str, float, float]]], **kwargs) -> str` *(class)* — Return the same output as :meth:`print_time_ranges` as a
  - `SegmentKeysInfo.format_time_ranges_html(cls, source: Union[List[Dict[str, Any]], List[Tuple[str, float, float]]], title: Optional[str] = None, **kwargs) -> str` *(class)* — Wrap :meth:`format_time_ranges_text` in styled HTML suitable
- **[`class SegmentKeys(SegmentKeysInfo)`](mayatk/mayatk/anim_utils/segment_keys.py#L269)** — Shared helper for collecting and grouping animation segments.
  - `SegmentKeys.collect_segments(cls, objects: List[Any], ignore: Optional[Union[str, List[str]]] = None, split_static: bool = False, selected_keys_only: bool = False, channel_box_attrs: Optional[List[str]] = None, static_tolerance: float = 0.0001, time_range: Optional[Tuple[Optional[float], Optional[float]]] = None, ignore_visibility_holds: bool = False, ignore_holds: bool = False, exclude_next_start: bool = True, motion_only: bool = False, motion_rate: float = 0.001, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> List[Dict[str, Any]]` *(class)* — Collect animation segments from objects.
  - `SegmentKeys.get_scene_info(cls, objects: Optional[List[str]] = None, detailed: bool = True, ignore_holds: bool = True, traversal: Optional[str] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> List[Dict[str, Any]]` *(class)* — Collect animation segments for the scene info report.
  - `SegmentKeys.format_scene_info_text(cls, objects: Optional[List[str]] = None, detailed: bool = True, csv_output: bool = False, by_time: bool = False, ignore_holds: bool = True, traversal: Optional[str] = None) -> str` *(class)* — Plain-text scene-info report.
  - `SegmentKeys.format_scene_info_html(cls, objects: Optional[List[str]] = None, detailed: bool = True, csv_output: bool = False, by_time: bool = False, ignore_holds: bool = True, traversal: Optional[str] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> str` *(class)* — HTML scene-info report for ``sb.text_view_dialog``.
  - `SegmentKeys.print_scene_info(cls, objects: Optional[List[str]] = None, detailed: bool = True, csv_output: bool = False, by_time: bool = False, ignore_holds: bool = True)` *(class)* — Print animation info to stdout.
  - `SegmentKeys.group_segments(cls, segments: List[Dict[str, Any]], mode: str = 'per_segment', **kwargs) -> List[Dict[str, Any]]` *(class)* — Group segments based on the specified mode.
  - `SegmentKeys.merge_groups_sharing_curves(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]` *(static)* — Merge groups that share any animation curves.
  - `SegmentKeys.shift_curves(curves: List[Any], offset: float, time_range: Optional[Tuple[float, float]] = None, remove_flat_at_dest: bool = False)` *(static)* — Shift keys on curves by offset using a two-pass move to avoid
  - `SegmentKeys.execute_stagger(cls, groups_data: List[dict], start_frame: float, spacing: Union[int, float] = 0, use_intervals: bool = False, avoid_overlap: bool = False, preserve_gaps: bool = False)` *(class)* — Calculate and execute staggering on groups of segments.

<a id="anim_utils--shots--_detection"></a>
### `anim_utils/shots/_detection.py`

Shot-region detection — Maya animation-graph analysis.

- [`detect_shot_regions(objects: Optional[List[str]] = None, gap_threshold: float = 5.0, ignore: Optional[str] = None, motion_rate: float = 0.001, min_duration: float = 2.0) -> List[Dict[str, Any]]`](mayatk/mayatk/anim_utils/shots/_detection.py#L62) — Detect animation regions by clustering per-object segments.
- [`regions_from_selected_keys(gap_threshold: float = 5.0, key_filter: str = 'all') -> List[Dict[str, Any]]`](mayatk/mayatk/anim_utils/shots/_detection.py#L222) — Build shot regions from currently selected keyframes.

<a id="anim_utils--shots--_shot_apply"></a>
### `anim_utils/shots/_shot_apply.py`

Commit resolved :class:`MovePlan`\ s to the Maya scene.

- [`apply(store: ShotStore, plan: MovePlan, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> None`](mayatk/mayatk/anim_utils/shots/_shot_apply.py#L114) — Execute ``plan`` against the scene and ``store``.

<a id="anim_utils--shots--_shot_plan"></a>
### `anim_utils/shots/_shot_plan.py`

Pure planning layer for multi-shot topology transformations.

- [`plan_respace(store: ShotStore, gap: float, start_frame: float) -> MovePlan`](mayatk/mayatk/anim_utils/shots/_shot_plan.py#L164) — Build a plan that lays shots out sequentially with uniform gaps.
- [`plan_ripple_downstream(store: ShotStore, pivot_shot_id: int, after_frame: float, delta: float) -> MovePlan`](mayatk/mayatk/anim_utils/shots/_shot_plan.py#L204) — Build a plan that shifts every shot starting at or after
- [`plan_ripple_upstream(store: ShotStore, pivot_shot_id: int, before_frame: float, delta: float) -> MovePlan`](mayatk/mayatk/anim_utils/shots/_shot_plan.py#L240) — Build a plan that shifts every shot ending at or before
- **[`class ShotMove`](mayatk/mayatk/anim_utils/shots/_shot_plan.py#L41)** — A single shot's source and destination ranges.
  - `ShotMove.delta(self) -> float` *(property)*
  - `ShotMove.moves(self) -> bool` *(property)*
- **[`class MovePlan`](mayatk/mayatk/anim_utils/shots/_shot_plan.py#L69)** — Resolved multi-shot timeline mutation.

<a id="anim_utils--shots--_shots"></a>
### `anim_utils/shots/_shots.py`

Shared shot data model and persistent store.

- **[`class ScenePersistence(Protocol)`](mayatk/mayatk/anim_utils/shots/_shots.py#L86)** — Interface for saving / loading ShotStore data.
  - `ScenePersistence.save(self, data: Dict[str, Any]) -> None`
  - `ScenePersistence.load(self) -> Optional[Dict[str, Any]]`
- **[`class MayaScenePersistence`](mayatk/mayatk/anim_utils/shots/_shots.py#L94)** — Persist ShotStore data to a Maya network-node attribute.
  - `MayaScenePersistence.save(self, data: Dict[str, Any]) -> None`
  - `MayaScenePersistence.load(self) -> Optional[Dict[str, Any]]`
  - `MayaScenePersistence.remove_callbacks(self) -> None` — Tear down every SJM subscription owned by this store.
- **[`class ShotBlock`](mayatk/mayatk/anim_utils/shots/_shots.py#L236)** — Represents a single shot (contiguous animation range).
  - `ShotBlock.duration(self) -> float` *(property)*
  - `ShotBlock.classify_objects(self) -> Dict[str, str]` — Return ``{obj_name: status_key}`` using stored metadata.
- **[`class StoreEvent`](mayatk/mayatk/anim_utils/shots/_shots.py#L299)** — Base class for typed :class:`ShotStore` events.
- **[`class ShotDefined(StoreEvent)`](mayatk/mayatk/anim_utils/shots/_shots.py#L311)** — A new shot was created and added to the store.
- **[`class ShotUpdated(StoreEvent)`](mayatk/mayatk/anim_utils/shots/_shots.py#L319)** — An existing shot's fields were modified.
- **[`class ShotRemoved(StoreEvent)`](mayatk/mayatk/anim_utils/shots/_shots.py#L327)** — A shot was removed from the store.
- **[`class ActiveShotChanged(StoreEvent)`](mayatk/mayatk/anim_utils/shots/_shots.py#L335)** — The active (selected) shot changed.
- **[`class SettingsChanged(StoreEvent)`](mayatk/mayatk/anim_utils/shots/_shots.py#L343)** — Detection-relevant settings were modified.
- **[`class BatchComplete(StoreEvent)`](mayatk/mayatk/anim_utils/shots/_shots.py#L350)** — A :meth:`ShotStore.batch_update` context has exited.
- **[`class StoreInvalidated(StoreEvent)`](mayatk/mayatk/anim_utils/shots/_shots.py#L357)** — The active store was discarded (scene change / new scene).
- **[`class ShotStore`](mayatk/mayatk/anim_utils/shots/_shots.py#L373)** — Central store for shot data with pluggable persistence.
  - `ShotStore.active_shot_id(self) -> Optional[int]` *(property)* — The currently selected shot, or ``None``.
  - `ShotStore.set_active_shot(self, shot_id: Optional[int]) -> None` — Set the active shot and notify listeners.
  - `ShotStore.notify_settings_changed(self) -> None` — Fire a ``"settings_changed"`` event.
  - `ShotStore.add_listener(self, callback: Callable[[StoreEvent], None]) -> None` — Register a listener called on store mutations.
  - `ShotStore.remove_listener(self, callback: Callable[[StoreEvent], None]) -> None` — Remove a previously registered listener.
  - `ShotStore.batch_update(self)` — Defer listener notifications until the block exits.
  - `ShotStore.is_gap_locked(self, left_id: str, right_id: str) -> bool` — Return whether the gap between two adjacent shots is locked.
  - `ShotStore.lock_gap(self, left_id: str, right_id: str) -> None` — Lock a gap so its width is preserved during global respace.
  - `ShotStore.unlock_gap(self, left_id: str, right_id: str) -> None` — Unlock a gap so it follows the global gap value.
  - `ShotStore.lock_all_gaps(self) -> None` — Lock every adjacent gap.
  - `ShotStore.unlock_all_gaps(self) -> None` — Unlock every gap.
  - `ShotStore.set_persistence(cls, backend: Optional[ScenePersistence]) -> None` *(class)* — Set the persistence backend used by :meth:`active` and :meth:`save`.
  - `ShotStore.active(cls) -> 'ShotStore'` *(class)* — Return the current active store, creating one if needed.
  - `ShotStore.set_active(cls, store: 'ShotStore') -> None` *(class)* — Replace the active store instance.
  - `ShotStore.clear_active(cls) -> None` *(class)* — Reset the active store and persistence backend.
  - `ShotStore.add_invalidation_listener(cls, callback: Callable[['StoreInvalidated'], None]) -> None` *(class)* — Register a callback fired when the active store is discarded.
  - `ShotStore.remove_invalidation_listener(cls, callback: Callable[['StoreInvalidated'], None]) -> None` *(class)* — Remove a previously registered invalidation listener.
  - `ShotStore.snap(self, frame: float) -> float` — Return *frame* rounded to the nearest integer when snapping is on.
  - `ShotStore.compute_gap(self) -> float` — Derive the predominant inter-shot gap from current shot positions.
  - `ShotStore.sorted_shots(self) -> List[ShotBlock]` — Return shots ordered by start time.
  - `ShotStore.shot_by_id(self, shot_id: int) -> Optional[ShotBlock]`
  - `ShotStore.shot_by_name(self, name: str) -> Optional[ShotBlock]` — Return the first shot whose name matches *name*, or ``None``.
  - `ShotStore.define_shot(self, name: str, start: float, end: float, objects: Optional[List[str]] = None, metadata: Optional[Dict[str, Any]] = None, locked: bool = False, description: str = '') -> ShotBlock` — Create a new shot and add it to the store.
  - `ShotStore.update_shot(self, shot_id: int, *, start: Optional[float] = None, end: Optional[float] = None, name: Optional[str] = None, objects: Optional[List[str]] = None, description: Optional[str] = None, locked: Optional[bool] = None, metadata: Optional[Dict[str, Any]] = None) -> Optional[ShotBlock]` — Update fields on an existing shot.
  - `ShotStore.ripple_shift(self, after_frame: float, delta: float, exclude_id: Optional[int] = None) -> None` — Shift all shots starting at or after *after_frame* by *delta*.
  - `ShotStore.ripple_shift_upstream(self, before_frame: float, delta: float, exclude_id: Optional[int] = None) -> None` — Shift all shots ending at or before *before_frame* by *delta*.
  - `ShotStore.remove_shot(self, shot_id: int) -> bool` — Remove a shot by ID.
  - `ShotStore.append_shot(self, name: str, duration: float, gap: float = 0, start_frame: Optional[float] = None, objects: Optional[List[str]] = None, metadata: Optional[Dict[str, Any]] = None, locked: bool = False, description: str = '') -> ShotBlock` — Append a shot after the last existing shot, with gap-aware placement.
  - `ShotStore.is_object_hidden(self, obj_name: str) -> bool` — Return True if *obj_name* is hidden in the sequencer UI.
  - `ShotStore.set_object_hidden(self, obj_name: str, hidden: bool = True) -> None` — Show or hide *obj_name* in the sequencer UI.
  - `ShotStore.is_object_pinned(self, obj_name: str) -> bool` — Return True if *obj_name* is pinned (kept even when missing).
  - `ShotStore.set_object_pinned(self, obj_name: str, pinned: bool = True) -> None` — Pin or unpin *obj_name*.
  - `ShotStore.remove_object_from_shots(self, obj_name: str) -> None` — Remove *obj_name* from every shot's object list.
  - `ShotStore.to_dict(self) -> Dict[str, Any]` — Serialise shots and settings to a plain dict.
  - `ShotStore.from_dict(cls, data: Dict[str, Any]) -> 'ShotStore'` *(class)* — Restore from serialised data.
  - `ShotStore.rescale_to_fps(self, new_fps: float) -> None` — Scale all shot timings from the current ``scene_fps`` to *new_fps*.
  - `ShotStore.mark_dirty(self) -> None` — Flag the store as needing a save.
  - `ShotStore.save(self) -> None` — Persist via the configured backend (no-op if none set).
  - `ShotStore.has_animation() -> bool` *(static)* — True if the scene contains animCurves driving transforms.
  - `ShotStore.is_detection_relevant(self) -> bool` *(property)* — True when detection settings are actionable.
  - `ShotStore.detect_regions(self) -> List[Dict[str, Any]]` — Detect shot candidates using the store's detection settings.
  - `ShotStore.detect_and_define(self, overwrite: bool = False) -> List[ShotBlock]` — Detect shot regions and define them in the store.
  - `ShotStore.assess(self) -> Dict[int, str]` — Lightweight assessment: check if shot objects exist in the scene.

<a id="anim_utils--shots--shot_manifest--_shot_manifest"></a>
### `anim_utils/shots/shot_manifest/_shot_manifest.py`

Shot Manifest â€” parse structured CSVs and populate a ShotStore.

- [`resolve_duration(step: BuilderStep, initial_shot_length: float, fit_mode: FitMode, fps: float) -> Tuple[float, float, float]`](mayatk/mayatk/anim_utils/shots/shot_manifest/_shot_manifest.py#L185) — Compute final shot duration for *step* under the given fit policy.
- [`plan_object_keys(obj: BuilderObject, shot_start: float, shot_end: float, fps: float) -> 'PlannedObject'`](mayatk/mayatk/anim_utils/shots/shot_manifest/_shot_manifest.py#L302) — Materialise a :class:`PlannedObject` with absolute keyframes.
- [`detect_behaviors(text: str) -> List[str]`](mayatk/mayatk/anim_utils/shots/shot_manifest/_shot_manifest.py#L499) — Return behavior names inferred from descriptive *text*.
- [`parse_csv(filepath: str, columns: Optional[ColumnMap] = None, post_process: Optional[Callable[[BuilderStep], None]] = None) -> List[BuilderStep]`](mayatk/mayatk/anim_utils/shots/shot_manifest/_shot_manifest.py#L636) — Parse a structured CSV into a list of :class:`BuilderStep`.
- **[`class BuilderObject`](mayatk/mayatk/anim_utils/shots/shot_manifest/_shot_manifest.py#L27)** — One asset within a step.
- **[`class BuilderStep`](mayatk/mayatk/anim_utils/shots/shot_manifest/_shot_manifest.py#L37)** — One step (= one future sequencer shot).
  - `BuilderStep.display_text(self) -> str` *(property)* — Text shown in the tree Description column.
  - `BuilderStep.from_detection(cls, candidates: List[Dict]) -> Tuple[List['BuilderStep'], Dict[str, Tuple[float, float]]]` *(class)* — Convert detection candidates to BuilderSteps + pre-filled ranges.
- **[`class PlannedShot`](mayatk/mayatk/anim_utils/shots/shot_manifest/_shot_manifest.py#L106)** — Immutable build instruction computed before any store mutation.
- **[`class PlannedKey`](mayatk/mayatk/anim_utils/shots/shot_manifest/_shot_manifest.py#L142)** — One keyframe to be written during commit.
- **[`class PlannedObject`](mayatk/mayatk/anim_utils/shots/shot_manifest/_shot_manifest.py#L154)** — Per-object build instruction with fully-resolved keyframes.
- **[`class BuildPlan`](mayatk/mayatk/anim_utils/shots/shot_manifest/_shot_manifest.py#L171)** — Complete plan for a build pass.
- **[`class ObjectStatus`](mayatk/mayatk/anim_utils/shots/shot_manifest/_shot_manifest.py#L437)** — Assessment result for one object within a step.
- **[`class StepStatus`](mayatk/mayatk/anim_utils/shots/shot_manifest/_shot_manifest.py#L453)** — Assessment result for one step.
  - `StepStatus.status(self) -> str` *(property)* — Worst-of-children rollup.
  - `StepStatus.missing_count(self) -> int` *(property)*
  - `StepStatus.total_count(self) -> int` *(property)*
- **[`class ColumnMap`](mayatk/mayatk/anim_utils/shots/shot_manifest/_shot_manifest.py#L518)** — Maps logical fields to CSV header names (case-insensitive).
  - `ColumnMap.to_dict(self) -> Dict[str, Any]` — Serialise to a JSON-safe dict (tuples â†’ lists).
  - `ColumnMap.from_dict(cls, data: Dict[str, Any]) -> 'ColumnMap'` *(class)* — Reconstruct from a dict produced by :meth:`to_dict`.
- **[`class ShotManifest`](mayatk/mayatk/anim_utils/shots/shot_manifest/_shot_manifest.py#L784)** — Creates shot store entries from parsed steps and applies behaviors.
  - `ShotManifest.sync(self, steps: List[BuilderStep], apply_behaviors: bool = True, ranges: Optional[Dict[str, Tuple[float, float]]] = None, remove_missing: bool = True, zero_duration_fallback: bool = False, fit_mode: FitMode = DEFAULT_FIT_MODE, initial_shot_length: float = DEFAULT_INITIAL_SHOT_LENGTH) -> Tuple[Dict[str, str], Dict[str, list], List[StepStatus]]` — Full build pipeline: plan -> commit -> apply behaviors -> assess.
  - `ShotManifest.rewire_audio(tracks: Optional[List[str]] = None) -> Dict[str, List[str]]` *(static)* — Reconcile managed DG audio nodes with keyed track state.
  - `ShotManifest.update(self, steps: List[BuilderStep], ranges: Optional[Dict[str, Tuple[float, float]]] = None, remove_missing: bool = True, zero_duration_fallback: bool = False, fit_mode: FitMode = DEFAULT_FIT_MODE, initial_shot_length: float = DEFAULT_INITIAL_SHOT_LENGTH) -> Dict[str, str]` — Sync parsed steps to the ShotStore (data only, no behaviors).
  - `ShotManifest.build_plan(self, steps: List[BuilderStep], ranges: Optional[Dict[str, Tuple[float, float]]] = None, remove_missing: bool = True, zero_duration_fallback: bool = False, fit_mode: FitMode = DEFAULT_FIT_MODE, initial_shot_length: float = DEFAULT_INITIAL_SHOT_LENGTH) -> BuildPlan` — Return a :class:`BuildPlan` without committing anything.
  - `ShotManifest.assess(self, steps: List[BuilderStep], exists_fn: Optional[Callable[[str], bool]] = None, verify_fn: Optional[Callable] = None, keyframe_range_fn: Optional[Callable[[str], Optional[Tuple[float, float]]]] = None, audio_exists_fn: Optional[Callable[[str], bool]] = None, skip_scene_discovery: bool = False) -> List[StepStatus]` — Compare parsed steps against the current store state.
  - `ShotManifest.from_csv(filepath: str, store: Optional[ShotStore] = None, columns: Optional[ColumnMap] = None, post_process: Optional[Callable[[BuilderStep], None]] = None) -> Tuple['ShotManifest', List[BuilderStep]]` *(static)* — Convenience: parse a CSV and return a ready-to-build engine.

<a id="anim_utils--shots--shot_manifest--behaviors--__init__"></a>
### `anim_utils/shots/shot_manifest/behaviors/__init__.py`

Behaviors — load and apply YAML keying recipes.

- [`load_behavior(name: str, search_path: Optional[Path] = None) -> Dict[str, Any]`](mayatk/mayatk/anim_utils/shots/shot_manifest/behaviors/__init__.py#L28) — Load a YAML behavior template by stem name.
- [`list_behaviors(search_path: Optional[Path] = None, kind: Optional[str] = None) -> List[str]`](mayatk/mayatk/anim_utils/shots/shot_manifest/behaviors/__init__.py#L63) — Return stem names of all available behavior templates.
- [`resolve_keys(block_def: Dict, start: float, end: float) -> List[Dict[str, Any]]`](mayatk/mayatk/anim_utils/shots/shot_manifest/behaviors/__init__.py#L98) — Resolve an ``in`` or ``out`` block to absolute keyframe dicts.
- [`apply_behavior(obj: str, behavior_name: str, start: float, end: float, attrs: Optional[List[str]] = None, search_path: Optional[Path] = None, source_path: str = '', anchor_override: Optional[str] = None) -> None`](mayatk/mayatk/anim_utils/shots/shot_manifest/behaviors/__init__.py#L149) — Apply a named behavior template to an object over a time range.
- [`verify_behavior(obj: str, behavior_name: str, start: float, end: float, search_path: Optional[Path] = None, keyframe_fn: Optional[Any] = None) -> bool`](mayatk/mayatk/anim_utils/shots/shot_manifest/behaviors/__init__.py#L291) — Check whether expected behavior keyframes exist on an object.
- [`apply_audio_clip(obj: str, start: float, end: float, source_path: str = '') -> None`](mayatk/mayatk/anim_utils/shots/shot_manifest/behaviors/__init__.py#L443) — Author start/stop keys for an audio track over *(start, end)*.
- [`compute_duration(behavior_entries: List[Dict[str, str]], fallback: float = 30, fps: Optional[float] = None) -> float`](mayatk/mayatk/anim_utils/shots/shot_manifest/behaviors/__init__.py#L524) — Derive duration from the behavior templates referenced in *behavior_entries*.
- [`apply_to_shots(shots: list, apply_fn, exists_fn=None, has_keys_fn=None, store=None) -> Dict[str, list]`](mayatk/mayatk/anim_utils/shots/shot_manifest/behaviors/__init__.py#L652) — Apply declared behaviors from shot metadata to Maya objects.

<a id="anim_utils--shots--shot_manifest--manifest_data"></a>
### `anim_utils/shots/shot_manifest/manifest_data.py`

Constants, column layout, and pure helper functions for the Shot Manifest UI.

- [`fmt_behavior(name: str) -> str`](mayatk/mayatk/anim_utils/shots/shot_manifest/manifest_data.py#L38) — ``'fade_in'`` → ``'Fade In'``.
- [`unfmt_behavior(display: str) -> str`](mayatk/mayatk/anim_utils/shots/shot_manifest/manifest_data.py#L43) — ``'Fade In'`` → ``'fade_in'``.
- [`short_name(dag_path: str) -> str`](mayatk/mayatk/anim_utils/shots/shot_manifest/manifest_data.py#L48) — Return the leaf node name from a Maya DAG path.
- [`format_behavior_html(behaviors, broken=(), status_color=None) -> str`](mayatk/mayatk/anim_utils/shots/shot_manifest/manifest_data.py#L57) — Return rich-text HTML for a list of behavior names.
- [`parse_range(raw: str) -> Optional[Tuple[float, Optional[float]]]`](mayatk/mayatk/anim_utils/shots/shot_manifest/manifest_data.py#L88) — Parse a range string without storing it.
- [`try_load_maya_icons()`](mayatk/mayatk/anim_utils/shots/shot_manifest/manifest_data.py#L109) — Return the :class:`NodeIcons` class if Maya is available, else ``None``.
- [`prune_to_top_boundaries(region_starts: List[float], n_steps: int) -> List[float]`](mayatk/mayatk/anim_utils/shots/shot_manifest/manifest_data.py#L119) — Keep only *n_steps* region starts by selecting the largest gaps.

<a id="anim_utils--shots--shot_manifest--mapping--__init__"></a>
### `anim_utils/shots/shot_manifest/mapping/__init__.py`

CSV mapping resolver — interprets JSON mapping files.

- [`discover(directory: Optional[str] = None) -> List[str]`](mayatk/mayatk/anim_utils/shots/shot_manifest/mapping/__init__.py#L77) — List available mapping names (without ``.json``) in *directory*.
- [`load_mapping(name: str, directory: Optional[str] = None) -> Dict[str, Any]`](mayatk/mayatk/anim_utils/shots/shot_manifest/mapping/__init__.py#L93) — Read a mapping JSON by *name* and return the parsed dict.
- [`resolve(csv_path: str, mapping: Optional[Dict[str, Any]] = None, *, name: Optional[str] = None, directory: Optional[str] = None) -> List[BuilderStep]`](mayatk/mayatk/anim_utils/shots/shot_manifest/mapping/__init__.py#L126) — Parse a CSV through a mapping and return fully resolved steps.

<a id="anim_utils--shots--shot_manifest--range_resolver"></a>
### `anim_utils/shots/shot_manifest/range_resolver.py`

Range resolution algorithm for the Shot Manifest.

- [`resolve_ranges(steps: List[BuilderStep], user_ranges: Dict[str, Tuple[Optional[float], Optional[float]]], gap_starts: List[float], gap_end_map: Dict[float, float], gap: float, use_selected_keys: bool, last_resolved: List[Tuple[str, float, Optional[float], bool]], from_step_idx: int = 0, default_duration: float = 0) -> List[Tuple[str, float, Optional[float], bool]]`](mayatk/mayatk/anim_utils/shots/shot_manifest/range_resolver.py#L17) — Compute a resolved ``(start, end)`` for every step.

<a id="anim_utils--shots--shot_manifest--shot_manifest_slots"></a>
### `anim_utils/shots/shot_manifest/shot_manifest_slots.py`

Switchboard slots for the Shot Manifest UI.

- **[`class ShotManifestController(ManifestTableMixin, ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/shots/shot_manifest/shot_manifest_slots.py#L48)** — Business logic for the Shot Manifest UI.
  - `ShotManifestController.detect(self, gap: Optional[float] = None) -> None` — Detect animation regions in the scene and populate the table.
  - `ShotManifestController.remove_callbacks(self) -> None` — Remove ShotStore listener and ScriptJobManager subscriptions.
  - `ShotManifestController.browse_csv(self) -> None` — Open a file dialog and load the selected CSV.
  - `ShotManifestController.build(self) -> None` — Build or update shots in the store from loaded steps.
  - `ShotManifestController.assess(self, skip_key_check: bool = False) -> None` — Compare CSV steps against the live Maya shots and color the tree.
- **[`class ShotManifestSlots(ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/shots/shot_manifest/shot_manifest_slots.py#L1665)** — Switchboard slot class â€” routes UI events to the controller.
  - `ShotManifestSlots.header_init(self, widget)` — Header menu is configured once in controller.__init__.
  - `ShotManifestSlots.btn_expand_missing(self)` — Expand all step rows that have missing objects or behaviors.
  - `ShotManifestSlots.btn_expand_extra(self)` — Expand all step rows that have scene-discovered extra objects.
  - `ShotManifestSlots.btn_settings(self)` — Open the shared shots settings panel.
  - `ShotManifestSlots.b002(self)` — Assess shots against live Maya scene.
  - `ShotManifestSlots.b003(self)` — Build shots from loaded steps (or auto-detect from scene).

<a id="anim_utils--shots--shot_manifest--table_presenter"></a>
### `anim_utils/shots/shot_manifest/table_presenter.py`

Tree-widget presentation mixin for the Shot Manifest controller.

- **[`class ManifestTableMixin`](mayatk/mayatk/anim_utils/shots/shot_manifest/table_presenter.py#L35)** — Presentation methods for the manifest tree widget.
  - `ManifestTableMixin.expand_missing(self) -> None` — Expand all step rows that have missing objects, behaviors, or additional objects.
  - `ManifestTableMixin.expand_extra(self) -> None` — Expand all step rows that have scene-discovered extra objects.

<a id="anim_utils--shots--shot_sequencer--_shot_sequencer"></a>
### `anim_utils/shots/shot_sequencer/_shot_sequencer.py`

Shot Sequencer — manages per-shot animation with ripple editing.

- **[`class ShotSequencer`](mayatk/mayatk/anim_utils/shots/shot_sequencer/_shot_sequencer.py#L25)** — Manages a :class:`ShotStore` and provides ripple editing and
  - `ShotSequencer.shots(self) -> List[ShotBlock]` *(property)*
  - `ShotSequencer.shots(self, value: List[ShotBlock])`
  - `ShotSequencer.hidden_objects(self) -> set` *(property)*
  - `ShotSequencer.hidden_objects(self, value: set)`
  - `ShotSequencer.markers(self) -> List[Dict[str, Any]]` *(property)*
  - `ShotSequencer.markers(self, value: List[Dict[str, Any]])`
  - `ShotSequencer.is_object_hidden(self, obj_name: str) -> bool`
  - `ShotSequencer.set_object_hidden(self, obj_name: str, hidden: bool = True) -> None`
  - `ShotSequencer.sorted_shots(self) -> List[ShotBlock]`
  - `ShotSequencer.shot_by_id(self, shot_id: int) -> Optional[ShotBlock]`
  - `ShotSequencer.shot_by_name(self, name: str) -> Optional[ShotBlock]`
  - `ShotSequencer.define_shot(self, name: str, start: float, end: float, objects: Optional[List[str]] = None, metadata: Optional[Dict[str, Any]] = None, locked: bool = False, description: str = '') -> ShotBlock` — Define a shot manually from a name and range.
  - `ShotSequencer.from_current_range(cls, name: str = 'Shot', objects: Optional[List[str]] = None) -> 'ShotSequencer'` *(class)* — Create a ShotSequencer with one shot spanning Maya's current
  - `ShotSequencer.reconcile_all_shots(self) -> bool` — Re-resolve stale DAG paths across every shot and persist changes.
  - `ShotSequencer.collect_object_segments(self, shot_id: int, ignore: Optional[str] = None, motion_rate: float = 0.001, ignore_holds: bool = True) -> List[Dict[str, Any]]` — Collect per-object animation segments within a shot's range.
  - `ShotSequencer.audio_prefetch(self)` — Cache per-track audio events for the duration of the block.
  - `ShotSequencer.collect_shot_sequences(self, shot_id: int, include_audio: bool = True) -> List[Dict[str, Any]]` — Return all sequences (anim + audio) inside a shot's range.
  - `ShotSequencer.move_sequences_to_shot(self, sequences: List[Dict[str, Any]], dest_shot_id: int) -> None` — Move *sequences* (anim and/or audio) into *dest_shot_id*.
  - `ShotSequencer.fit_shot_to_content(self, shot_id: int, mode: str = 'fit') -> tuple[float, float]` — Resize a shot's boundaries to its sequence content, rippling neighbors.
  - `ShotSequencer.trim_shot_to_content(self, shot_id: int) -> tuple[float, float]` — Shrink shot boundaries inward so they exactly enclose content.
  - `ShotSequencer.extend_shot_to_fit(self, shot_id: int) -> tuple[float, float]` — Expand shot boundaries outward to enclose all of its sequences.
  - `ShotSequencer.detect_shots(self, objects: Optional[List[str]] = None, gap_threshold: float = 5.0, ignore: Optional[str] = None, motion_rate: float = 0.001, min_duration: float = 2.0) -> List[Dict[str, Any]]` — Detect shot boundaries from existing animation on *objects*.
  - `ShotSequencer.detect_next_shot(self, gap_threshold: float = 5.0, ignore: Optional[str] = None, motion_rate: float = 0.001) -> Optional[Dict[str, Any]]` — Detect the first animation cluster after all existing shots.
  - `ShotSequencer.move_object_keys(self, obj: str, old_start: float, old_end: float, new_start: float) -> None` — Offset all keyframes of *obj* that fall within [old_start, old_end]
  - `ShotSequencer.move_stepped_keys(self, obj: str, old_time: float, new_time: float, attr_name: str | None = None, eps: float = 0.001) -> None` — Move stepped keys at *old_time* to *new_time* via delete-and-recreate.
  - `ShotSequencer.move_object_in_shot(self, shot_id: int, obj: str, old_start: float, old_end: float, new_start: float, prevent_overlap: bool = False) -> None` — Move one object's keys within a shot, expanding the shot and
  - `ShotSequencer.scale_object_keys(self, obj: str, old_start: float, old_end: float, new_start: float, new_end: float) -> None` — Scale (and optionally shift) keyframes of *obj* from
  - `ShotSequencer.move_shot(self, shot_id: int, new_start: float) -> None` — Move an entire shot (all object keys) to *new_start*, rippling downstream.
  - `ShotSequencer.slide_shot(self, shot_id: int, new_start: float, direction: str = 'downstream', _enforce: bool = True) -> None` — Slide a shot intact to *new_start*, rippling only in *direction*.
  - `ShotSequencer.expand_shot(self, shot_id: int, new_end: float) -> float` — Expand a shot's end frame and ripple downstream shots.
  - `ShotSequencer.resize_object(self, shot_id: int, obj: str, old_start: float, old_end: float, new_start: float, new_end: float) -> None` — Scale one object's keys and ripple-shift all downstream shots.
  - `ShotSequencer.set_shot_duration(self, shot_id: int, new_duration: float) -> None` — Change a shot's duration and ripple-shift all downstream shots.
  - `ShotSequencer.resize_shot(self, shot_id: int, new_start: float, new_end: float, _enforce: bool = True) -> None` — Resize a shot to [new_start, new_end], scaling all keys and rippling.
  - `ShotSequencer.set_shot_start(self, shot_id: int, new_start: float, ripple: bool = True) -> None` — Move a shot to a new start time.
  - `ShotSequencer.reorder_shots(self, shot_id_a: int, shot_id_b: int) -> None` — Swap two shots' timeline positions non-destructively.
  - `ShotSequencer.move_shot_to_position(self, shot_id: int, target_pos: int) -> None` — Move a shot to a new 1-based position in the timeline order.
  - `ShotSequencer.respace(self, gap: float = 0, start_frame: float = 1) -> None` — Redistribute all shots sequentially with uniform gaps.
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

- **[`class MarkerManagerMixin`](mayatk/mayatk/anim_utils/shots/shot_sequencer/marker_manager.py#L15)** — Mixin supplying marker CRUD persistence.
  - `MarkerManagerMixin.on_marker_added(self, marker_id: int, time: float) -> None` — Persist a newly added marker.
  - `MarkerManagerMixin.on_marker_moved(self, marker_id: int, new_time: float) -> None` — Update persisted marker time.
  - `MarkerManagerMixin.on_marker_changed(self, marker_id: int) -> None` — Update persisted marker note/color.
  - `MarkerManagerMixin.on_marker_removed(self, marker_id: int) -> None` — Remove marker from persistent store.

<a id="anim_utils--shots--shot_sequencer--segment_collector"></a>
### `anim_utils/shots/shot_sequencer/segment_collector.py`

Segment collection and attribute extraction for the shot sequencer.

- [`collect_segments(sequencer, shot, visible_shots, segment_cache, shifted_out_keys, logger)`](mayatk/mayatk/anim_utils/shots/shot_sequencer/segment_collector.py#L28) — Collect animation segments for visible shots.
- [`active_object_set(shot, segments_by_shot) -> set`](mayatk/mayatk/anim_utils/shots/shot_sequencer/segment_collector.py#L113) — Return the set of objects that belong to the active shot.
- [`extract_attributes(segments) -> list`](mayatk/mayatk/anim_utils/shots/shot_sequencer/segment_collector.py#L123) — Extract attribute names from animation curves in the given segments.
- [`build_curve_preview(crv, t_start, t_end)`](mayatk/mayatk/anim_utils/shots/shot_sequencer/segment_collector.py#L190) — Extract Bézier curve shape data for a single anim curve.

<a id="anim_utils--shots--shot_sequencer--shot_nav"></a>
### `anim_utils/shots/shot_sequencer/shot_nav.py`

Shot navigation and combobox synchronization.

- **[`class ShotNavMixin`](mayatk/mayatk/anim_utils/shots/shot_sequencer/shot_nav.py#L21)** — Mixin supplying shot selection and navigation.
  - `ShotNavMixin.select_shot(self, shot_id: int) -> None` — Set Maya's playback range to the shot and select its objects.
  - `ShotNavMixin.on_shot_block_clicked(self, shot_name: str) -> None` — Select a shot by name when its block is clicked in the shot lane.

<a id="anim_utils--shots--shot_sequencer--shot_sequencer_slots"></a>
### `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py`

Switchboard slots for the Shot Sequencer UI.

- **[`class ShotSequencerController(GapManagerMixin, ClipMotionMixin, ShotNavMixin, MarkerManagerMixin, ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/shots/shot_sequencer/shot_sequencer_slots.py#L61)** — Business logic controller bridging SequencerWidget ↔ ShotSequencer.
  - `ShotSequencerController.sequencer(self) -> Optional[ShotSequencer]` *(property)* — Return the ShotSequencer, lazily creating one from the active store.
  - `ShotSequencerController.sequencer(self, value: Optional[ShotSequencer]) -> None`
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
- **[`class ShotEditDialog`](mayatk/mayatk/anim_utils/shots/shot_sequencer/shot_sequencer_slots.py#L2281)** — Lightweight dialog for creating or editing a shot.
  - `ShotEditDialog.show(parent=None, name: str = '', start: float = 1.0, end: float = 100.0, description: str = '', title: str = 'Shot')` *(static)* — Show a modal dialog and return the result tuple or ``None``.
- **[`class ShotSequencerSlots(ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/shots/shot_sequencer/shot_sequencer_slots.py#L2345)** — Switchboard slot class — routes UI events to the controller.
  - `ShotSequencerSlots.header_init(self, widget)` — Configure header menu.
  - `ShotSequencerSlots.btn_colors(self)` — Open the attribute color configuration dialog.
  - `ShotSequencerSlots.cmb_shot(self, index)` — Handle direct combobox selection of a shot or marker.
  - `ShotSequencerSlots.spn_snap(self, value)` — Set the snap interval on the sequencer widget.
  - `ShotSequencerSlots.btn_shortcuts(self)` — Open the sequencer shortcut editor.
  - `ShotSequencerSlots.btn_shot_settings(self)` — Open the shared shots settings panel.

<a id="anim_utils--shots--shots_slots"></a>
### `anim_utils/shots/shots_slots.py`

Switchboard slots for the Shots settings UI.

- **[`class ShotsController(ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/shots/shots_slots.py#L27)** — Business logic for the Shots settings panel.
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
- **[`class ShotsSlots(ptk.LoggingMixin)`](mayatk/mayatk/anim_utils/shots/shots_slots.py#L851)** — Switchboard slot class — routes UI events to the controller.
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

<a id="anim_utils--smart_bake"></a>
### `anim_utils/smart_bake.py`

Smart bake module for intelligent pre-bake animation processing.

- **[`class BakeAnalysis`](mayatk/mayatk/anim_utils/smart_bake.py#L30)** — Analysis result for a single object's bake requirements.
  - `BakeAnalysis.requires_bake(self) -> bool` *(property)* — Return True if this object has any driven channels needing bake.
  - `BakeAnalysis.all_driven_channels(self) -> List[str]` *(property)* — Return flat list of all channels that need baking.
- **[`class BakeResult`](mayatk/mayatk/anim_utils/smart_bake.py#L60)** — Result container for SmartBake.bake() operation.
  - `BakeResult.baked_count(self) -> int` *(property)* — Number of objects successfully baked.
  - `BakeResult.success(self) -> bool` *(property)* — Return True if any objects were baked.
- **[`class SmartBake`](mayatk/mayatk/anim_utils/smart_bake.py#L107)** — Intelligent baking with automatic detection of what needs to be baked.
  - `SmartBake.analyze(self) -> Dict[str, BakeAnalysis]` — Analyze objects to determine what needs baking.
  - `SmartBake.get_time_range(self, analysis: Optional[Dict[str, BakeAnalysis]] = None) -> Tuple[int, int]` — Determine optimal bake time range from driver animation.
  - `SmartBake.bake(self, analysis: Optional[Dict[str, BakeAnalysis]] = None, time_range: Optional[Tuple[int, int]] = None) -> BakeResult` — Execute baking on analyzed objects.
  - `SmartBake.execute(self) -> BakeResult` — High-level entry point: analyze and bake in one call.
  - `SmartBake.run(cls, **kwargs) -> BakeResult` *(class)* — Class method for quick smart baking without explicit instantiation.

<a id="anim_utils--stagger_keys"></a>
### `anim_utils/stagger_keys.py`

Dedicated stagger-keys module to keep AnimUtils lean and testable.

- **[`class StaggerKeys`](mayatk/mayatk/anim_utils/stagger_keys.py#L16)** — Class containing keyframe staggering operations.
  - `StaggerKeys.stagger_keys(objects: list, start_frame: int = None, spacing: Union[int, float] = 0, use_intervals: bool = False, avoid_overlap: bool = False, smooth_tangents: bool = False, invert: bool = False, group_overlapping: bool = False, ignore: Union[str, List[str]] = None, channel_box_attrs_only: bool = False, split_static: bool = True, merge_touching: bool = False, ignore_visibility_holds: bool = True, verbose: bool = False, verbose_header: str = None)` *(static)* — Stagger the keyframes of selected objects with various positioning controls.

<a id="anim_utils--unbake_keys"></a>
### `anim_utils/unbake_keys.py`

- [`unbake_animation(objects=None, threshold=0.001)`](mayatk/mayatk/anim_utils/unbake_keys.py#L11) — Unbakes keyframe animation by removing unnecessary keys:
- [`unbake_animation_direction_based(objects=None, threshold=0.01)`](mayatk/mayatk/anim_utils/unbake_keys.py#L110) — Remove keys except where animation changes direction significantly.
- [`unbake_animation_smart(objects=None, threshold=0.001)`](mayatk/mayatk/anim_utils/unbake_keys.py#L175) — Smart unbaking - identifies animation segments and preserves only essential keys:

<a id="audio_utils--_audio_utils"></a>
### `audio_utils/_audio_utils.py`

Unified audio system for Maya scenes.

- **[`class TrackEvent`](mayatk/mayatk/audio_utils/_audio_utils.py#L31)** — One keyed play-event on a track.
- **[`class AudioUtils(ptk.HelpMixin)`](mayatk/mayatk/audio_utils/_audio_utils.py#L81)** — Unified audio system API for Maya scenes.
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
  - `AudioUtils.list_tracks(cls, carrier: Optional[str] = None) -> List[str]` *(class)* — Return all track_ids with attrs on *carrier* (sorted).
  - `AudioUtils.read_keys(cls, track_id: str, carrier: Optional[str] = None) -> List[tuple]` *(class)* — Return ``[(frame, value), ...]`` for *track_id* (time-ordered).
  - `AudioUtils.read_events(cls, track_id: str, carrier: Optional[str] = None) -> List[TrackEvent]` *(class)* — Return :class:`TrackEvent` list for *track_id*.
  - `AudioUtils.write_key(cls, track_id: str, frame: float, value: int = 1, carrier: Optional[str] = None, snap: Optional[bool] = None) -> None` *(class)* — Set a key at *frame* with *value* (0=off, 1=on) on the track attr.
  - `AudioUtils.remove_key(cls, track_id: str, frame: float, carrier: Optional[str] = None) -> bool` *(class)* — Remove the key at *frame* on the track attr.
  - `AudioUtils.clear_keys(cls, track_id: str, carrier: Optional[str] = None) -> bool` *(class)* — Remove every key on *track_id*'s attr.
  - `AudioUtils.shift_keys_in_range(cls, old_start: float, old_end: float, delta: float, track_ids: Optional[List[str]] = None, carrier: Optional[str] = None) -> List[str]` *(class)* — Shift audio keys in ``[old_start, old_end]`` by *delta*.
  - `AudioUtils.tracks_on_at_frame(cls, frame: float, carrier: Optional[str] = None, track_ids: Optional[List[str]] = None) -> List[str]` *(class)* — Return track_ids currently "on" (value=1) at *frame*.
  - `AudioUtils.bake_manifest(cls, carrier: Optional[str] = None, display_map: Optional[dict] = None, frame_offset: float = 0.0) -> str` *(class)* — Return a space-separated ``"<frame>:<label>"`` manifest string.
  - `AudioUtils.delete_track(cls, track_id: str, carrier: Optional[str] = None) -> bool` *(class)* — Remove the per-track attr and its keys.
  - `AudioUtils.rename_track(cls, old_id: str, new_id: str, carrier: Optional[str] = None) -> bool` *(class)* — Rename a track's attr + enum labels + file_map key.
  - `AudioUtils.show_track_attrs(cls, track_id: Optional[str] = None, carrier: Optional[str] = None) -> List[str]` *(class)* — Un-hide track attrs in the Channel Box.
  - `AudioUtils.hide_track_attrs(cls, track_id: Optional[str] = None, carrier: Optional[str] = None) -> List[str]` *(class)* — Hide track attrs from the Channel Box.
  - `AudioUtils.sync(tracks=None, carrier=None)` *(static)* — Reconcile managed DG audio nodes with keyed track state.
  - `AudioUtils.find_dg_node_for_track(track_id)` *(static)* — Return the managed DG audio node for *track_id*, or ``None``.
  - `AudioUtils.is_managed_dg(node)` *(static)* — True if *node* has the ``audio_node_source`` marker attr.
  - `AudioUtils.batch(auto_sync=True, undo=True)` *(static)* — Context manager grouping audio edits into one undo + one sync.
  - `AudioUtils.detect_legacy(obj='data_internal', category='audio')` *(static)* — Return True if *obj* has legacy ``<category>_trigger`` attr.
  - `AudioUtils.migrate_legacy_triggers(obj, category='audio', keep_old_attrs=False)` *(static)* — Migrate legacy trigger keys to per-track attrs.

<a id="audio_utils--audio_clips--_audio_clips"></a>
### `audio_utils/audio_clips/_audio_clips.py`

Scene-wide audio event manager — thin facade over ``audio_utils``.

- **[`class AudioClips(ptk.LoggingMixin)`](mayatk/mayatk/audio_utils/audio_clips/_audio_clips.py#L46)** — Scene-wide audio event facade.
  - `AudioClips.sync(cls, track_ids: Optional[List[str]] = None, composite: bool = True, activate: bool = True) -> Dict[str, list]` *(class)* — Reconcile DG nodes and rebuild the composite WAV.
  - `AudioClips.rebuild_composite(cls) -> Optional[str]` *(class)* — Rebuild the scene-wide composite WAV from keyed start events.
  - `AudioClips.remove(cls) -> int` *(class)* — Delete every managed DG node, the composite, and all tracks.
  - `AudioClips.load_tracks(cls, audio_files: List[str]) -> List[str]` *(class)* — Register audio files as tracks (no keys authored).
  - `AudioClips.prepare_for_export(cls) -> str` *(class)* — Bake the scene-wide audio manifest for FBX export.
  - `AudioClips.list_nodes(cls) -> List[str]` *(class)* — Return names of every managed DG audio node plus the composite.
  - `AudioClips.set_active(cls, node_name: str, time_slider: bool = True) -> None` *(class)* — Set an audio node as the active Time Slider sound.

<a id="audio_utils--audio_clips--audio_clips_slots"></a>
### `audio_utils/audio_clips/audio_clips_slots.py`

Switchboard slots for the Audio Clips UI.

- **[`class AudioClipsSlots(ExportMixin, CallbacksMixin)`](mayatk/mayatk/audio_utils/audio_clips/audio_clips_slots.py#L39)** — Switchboard slots for the Audio Clips UI.
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

- [`batch(auto_sync: bool = True, undo: bool = True) -> _BatchContext`](mayatk/mayatk/audio_utils/batch.py#L101) — Context manager grouping audio edits into one undo + one sync.

<a id="audio_utils--compositor"></a>
### `audio_utils/compositor.py`

Compositor — derives DG audio nodes from keyed track events.

- [`is_managed_dg(node: str) -> bool`](mayatk/mayatk/audio_utils/compositor.py#L37) — True if *node* has the ``audio_node_source`` marker attr.
- [`find_dg_node_for_track(track_id: str) -> Optional[str]`](mayatk/mayatk/audio_utils/compositor.py#L54) — Return the managed DG audio node for *track_id*, or ``None``.
- [`sync(tracks: Optional[List[str]] = None, carrier: Optional[str] = None) -> dict`](mayatk/mayatk/audio_utils/compositor.py#L67) — Reconcile managed DG audio nodes with keyed track state.

<a id="audio_utils--migrate"></a>
### `audio_utils/migrate.py`

One-shot migration from legacy single-enum carriers to per-track schema.

- [`detect_legacy(obj: str, category: str = 'audio') -> bool`](mayatk/mayatk/audio_utils/migrate.py#L32) — Return True if *obj* has the legacy ``<category>_trigger`` attr.
- [`migrate_legacy_triggers(obj: str, category: str = 'audio', keep_old_attrs: bool = False) -> List[str]`](mayatk/mayatk/audio_utils/migrate.py#L58) — Migrate legacy ``<category>_trigger`` keys to per-track attrs.

<a id="audio_utils--nodes"></a>
### `audio_utils/nodes.py`

Low-level DG audio node primitives.

- [`resolve_playable_path(audio_path: str, cache_dir: Optional[str] = None) -> Optional[str]`](mayatk/mayatk/audio_utils/nodes.py#L35) — Return a Maya-playable path, converting to WAV via ``ptk.AudioUtils``.
- [`workspace_sound_dir() -> Optional[str]`](mayatk/mayatk/audio_utils/nodes.py#L49) — Return the Maya workspace ``sound/`` directory, or ``None``.
- [`create_dg(file_path: str, name: Optional[str] = None, offset: float = 0, track_id: Optional[str] = None) -> Optional[str]`](mayatk/mayatk/audio_utils/nodes.py#L69) — Create a new audio DG node configured for playback.
- [`configure_dg(node_name: str, file_path: str, offset: float) -> None`](mayatk/mayatk/audio_utils/nodes.py#L96) — Configure an existing DG audio node for reliable playback.
- [`query_duration(node_name: str) -> float`](mayatk/mayatk/audio_utils/nodes.py#L121) — Return the duration of an audio DG node in frames (0 on failure).

<a id="audio_utils--segments--discovery"></a>
### `audio_utils/segments/discovery.py`

Segment discovery from the per-track keyed canonical store.

- [`collect_all_segments(scene_start: Optional[float] = None, scene_end: Optional[float] = None, include_waveform: bool = True, carrier: Optional[str] = None) -> List[AudioSegment]`](mayatk/mayatk/audio_utils/segments/discovery.py#L101) — Return every :class:`AudioSegment` visible on the canonical carrier.
- [`collect_segments_for_track(track_id: str, include_waveform: bool = True, carrier: Optional[str] = None) -> List[AudioSegment]`](mayatk/mayatk/audio_utils/segments/discovery.py#L147) — Return segments for a single *track_id*.
- **[`class AudioSegment`](mayatk/mayatk/audio_utils/segments/discovery.py#L22)** — A resolved audio segment for sequencer/manifest consumption.
  - `AudioSegment.is_audio(self) -> bool` *(property)*

<a id="cam_utils--_cam_utils"></a>
### `cam_utils/_cam_utils.py`

- **[`class CamUtils(ptk.HelpMixin)`](mayatk/mayatk/cam_utils/_cam_utils.py#L18)**
  - `CamUtils.group_cameras(name='cameras', non_default=True, root_only=False, hide_group=False)` *(static)* — Group cameras in the scene based on the provided parameters.
  - `CamUtils.toggle_safe_frames(cls)` *(class)* — Toggle display of the film gate for the current camera.
  - `CamUtils.get_current_cam()` *(static)* — Get the currently active camera.
  - `CamUtils.create_camera_from_view(name='camera#')` *(static)* — Create a new camera based on the current view.
  - `CamUtils.adjust_camera_clipping(cls, camera=None, near_clip=None, far_clip=None)` *(class)* — Adjusts the near and far clipping planes of one or multiple cameras.
  - `CamUtils.switch_viewport_camera(cls, camera_name)` *(class)* — Unified method to switch to a camera, creating custom ones if needed.

<a id="core_utils--_core_utils"></a>
### `core_utils/_core_utils.py`

- [`as_strings(nodes) -> List[str]`](mayatk/mayatk/core_utils/_core_utils.py#L19) — Coerce a node-or-iterable-of-nodes to a list of plain DAG-path strings.
- [`short_name(node) -> str`](mayatk/mayatk/core_utils/_core_utils.py#L37) — Leaf name with namespace stripped: ``"|grp|ns:obj"`` -> ``"obj"``.
- [`leaf_name(node) -> str`](mayatk/mayatk/core_utils/_core_utils.py#L42) — Leaf name with namespace preserved: ``"|grp|ns:obj"`` -> ``"ns:obj"``.
- [`get_bounding_box(node, world: bool = True) -> BoundingBox`](mayatk/mayatk/core_utils/_core_utils.py#L70) — Return a :class:`BoundingBox` for *node*.
- **[`class BoundingBox`](mayatk/mayatk/core_utils/_core_utils.py#L47)** — Plain-data bounding box with ``MVector`` extents.
- **[`class CoreUtils(ptk.CoreUtils, _CoreUtilsInternal)`](mayatk/mayatk/core_utils/_core_utils.py#L195)**
  - `CoreUtils.undo_chunk(name: str = '')` *(static)* — Group operations into a single Maya undo chunk.
  - `CoreUtils.temporarily_unlock_attributes(objects, attributes=None)` *(static)* — ..
  - `CoreUtils.selected(func: Callable) -> Callable` — A decorator to pass the current selection to the first parameter if None is given.
  - `CoreUtils.undoable(fn)` — A decorator to place a function into Maya's undo chunk.
  - `CoreUtils.reparent(func: Callable) -> Callable` — A decorator to manage reparenting of Maya nodes before and after an operation.
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

<a id="core_utils--components"></a>
### `core_utils/components.py`

- **[`class GetComponentsMixin`](mayatk/mayatk/core_utils/components.py#L45)**
  - `GetComponentsMixin.get_component_type(cls, component, returned_type='abv')` *(class)* — Get the type of a given component.
  - `GetComponentsMixin.convert_alias(cls, component_type, returned_type='abv')` *(class)* — Return an alternate component alias for the given alias.
  - `GetComponentsMixin.convert_component_type(cls, components, component_type, returned_type='str', flatten=False)` *(class)* — Convert component(s) to its sub-components of the given type.
  - `GetComponentsMixin.get_component_index(components)` *(static)* — Extract the numerical index or indices of a component or components from their descriptor strings.
  - `GetComponentsMixin.convert_int_to_component(cls, obj, integers, component_type, returned_type='str', flatten=False)` *(class)* — Convert the given integers to components of the given object.
  - `GetComponentsMixin.filter_components(cls, components, inc=[], exc=[], flatten=False)` *(class)* — Filter the given components.
  - `GetComponentsMixin.get_components(cls, objects, component_type, returned_type='str', inc=[], exc=[], randomize=0, flatten=False)` *(class)* — Get the components of the given type from the given object(s).
- **[`class Components(GetComponentsMixin, ptk.HelpMixin)`](mayatk/mayatk/core_utils/components.py#L258)**
  - `Components.map_components_to_objects(components_list)` *(static)* — Map a list of components to their respective objects.
  - `Components.get_contigious_edges(cls, components)` *(class)* — Get a list containing sets of adjacent edges.
  - `Components.get_contigious_islands(cls, faces)` *(class)* — Get a list containing sets of adjacent polygon faces grouped by islands.
  - `Components.get_islands(obj, returned_type='str', flatten=False)` *(static)* — Get the group of components in each separate island of a combined mesh.
  - `Components.get_border_components(cls, components, returned_type='str', component_border=False)` *(class)* — Get border components from given component(s) or a polygon object based on connectivity.
  - `Components.get_furthest_vertices(vertices_a, vertices_b)` *(static)* — Determine the two furthest apart vertices, one from each of the two provided lists.
  - `Components.get_closest_verts(cls, a, b, tolerance=1000)` *(class)* — Find the two closest vertices between the two sets of vertices.
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
  - `Components.set_edge_hardness(cls, objects, angle_threshold: float, upper_hardness: float = None, lower_hardness: float = None, unlock_normals: bool = False) -> None` *(class)* — Set edge hardness based on normal angle thresholds.
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
  - `AnimCurveDiagnostics.repair_corrupted_curves(cls, objects: Optional[Union[NodeLike, Sequence[NodeLike]]] = None, recursive: bool = True, delete_corrupted: bool = False, fix_infinite: bool = True, fix_invalid_times: bool = True, time_range_threshold: float = 1000000.0, value_threshold: float = 1000000.0, quiet: bool = False) -> Dict[str, Any]` *(class)* — Detect and (optionally) repair corrupted animation curves.
  - `AnimCurveDiagnostics.repair_visibility_tangents(cls, objects: Optional[Union[NodeLike, Sequence[NodeLike]]] = None, recursive: bool = True, quiet: bool = False) -> int` *(class)* — Repair visibility animation curves by forcing 'step' tangents.

<a id="core_utils--diagnostics--mesh_diag"></a>
### `core_utils/diagnostics/mesh_diag.py`

Mesh diagnostics and repair helpers.

- **[`class MeshDiagnostics`](mayatk/mayatk/core_utils/diagnostics/mesh_diag.py#L18)** — Operations for inspecting and fixing common mesh issues.
  - `MeshDiagnostics.clean_geometry(objects: NodeSeq, allMeshes: bool = False, repair: bool = False, quads: bool = False, nsided: bool = False, concave: bool = False, holed: bool = False, nonplanar: bool = False, zeroGeom: bool = False, zeroGeomTol: float = 1e-05, zeroEdge: bool = False, zeroEdgeTol: float = 1e-05, zeroMap: bool = False, zeroMapTol: float = 1e-05, sharedUVs: bool = False, nonmanifold: bool = False, lamina: bool = False, invalidComponents: bool = False, historyOn: bool = True, bakePartialHistory: bool = False) -> None` *(static)* — Select or remove unwanted geometry from a mesh via ``polyCleanupArgList``.
  - `MeshDiagnostics.get_ngons(objects: Optional[NodeSeq], repair: bool = False)` *(static)* — Find N-gons and optionally convert them to quads.

<a id="core_utils--diagnostics--scene_diag"></a>
### `core_utils/diagnostics/scene_diag.py`

Scene diagnostics and repair helpers.

- **[`class SceneDiagnostics`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L21)** — Operations for inspecting and fixing common scene issues.
  - `SceneDiagnostics.fix_ocio(cls, dry_run: bool = False, verbose: bool = True, prefer_env_ocio: bool = True, prefer_aces: bool = True, fix_color_spaces: bool = True) -> dict` *(class)* — Repair Maya OCIO/Color Management preferences.
  - `SceneDiagnostics.fix_missing_color_spaces(cls, fallback_color_space: Optional[str] = None, fallback_raw_space: Optional[str] = None, auto_detect: bool = True, dry_run: bool = False, verbose: bool = True, scan_all: bool = True, force_update: bool = False) -> Dict[str, Any]` *(class)* — Fix missing color space errors on file texture nodes.
  - `SceneDiagnostics.fix_unknown_plugins(dry_run=False, verbose=True)` *(static)* — Fixes the 'Unable to Save Scene' issue by removing unknown nodes and plugins.
  - `SceneDiagnostics.remove_xgen_expressions(quiet: bool = False) -> int` *(static)* — Remove legacy XGen expressions that cause 'Cannot find procedure xgmPreview' errors.
  - `SceneDiagnostics.cleanup_scene(cls, quiet: bool = False) -> None` *(class)* — Run all scene cleanup operations:
- **[`class AuditProfile`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L985)** — Thresholds for scene analysis.
- **[`class MeshRecord`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1012)** — Per-mesh statistics for a single shape node.
- **[`class MaterialRecord`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1030)** — Per-shape material usage summary (aggregated across slots).
- **[`class Finding`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1048)** — An observation about an asset (negative or risk-flagged).
- **[`class FixAction`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1058)** — A recommended remediation step.
- **[`class BudgetDelta`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1069)** — How far an asset exceeds the profile budget along each axis.
  - `BudgetDelta.is_over_budget(self) -> bool`
  - `BudgetDelta.summary(self) -> str` — Pre-rendered ``"tris +N | slots +M | …"`` string used by the
- **[`class AssetRecord`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1104)** — Combined per-asset record produced by analyze().
- **[`class ParetoEntry`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1123)** — One row of a Pareto ranking (top contributor + cumulative %).
- **[`class TextureFile`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1132)** — A texture file referenced by the scene, with usage stats.
- **[`class MissingTexture`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1146)** — A texture referenced by a material but not present on disk.
- **[`class SharedTexture`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1155)** — A texture used by more than one mesh.
- **[`class MaterialSplit`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1163)** — A material correlated with high-slot meshes (draw-call splits).
- **[`class SlotStats`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1173)** — Distribution stats for material slots-per-mesh.
- **[`class InstanceStats`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1184)** — Mesh / instance counts.
- **[`class BudgetBuckets`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1193)** — Histogram of overage severity per dimension.
- **[`class ComplianceStats`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1205)** — Percentage of scene over budget per dimension.
- **[`class MissingTextureImpact`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1213)** — Downstream effect of missing textures on the asset list.
  - `MissingTextureImpact.is_empty(self) -> bool`
- **[`class SummaryStats`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1225)** — High-level scene counters surfaced by the Executive Summary.
- **[`class BudgetStats`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1248)** — Budget / compliance / savings figures.
- **[`class TextureStats`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1266)** — Texture-side aggregates.
- **[`class PipelineStats`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1285)** — Pipeline integrity findings (missing textures + impact).
- **[`class OffenderLists`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1295)** — Top-N rankings across various dimensions.
- **[`class AnalysisManifest`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1320)** — What was analyzed, how, and how long it took.
- **[`class SceneReport`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1342)** — Top-level result of ``SceneAnalyzer.generate_report``.
  - `SceneReport.to_dict(self) -> Dict[str, Any]` — Serialize the report to a nested plain-dict tree.
- **[`class SceneInfoSection`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1374)** — Report-section identifiers used to gate analyze() work and report output.
  - `SceneInfoSection.normalize(cls, sections: Optional[List[str]]) -> List[str]` *(class)* — Coerce a caller-supplied sections argument to a stable,
- **[`class SceneAnalyzer(ptk.LoggingMixin)`](mayatk/mayatk/core_utils/diagnostics/scene_diag.py#L1460)** — Analyzes scene objects for performance expectations in game engines.
  - `SceneAnalyzer.run_audit(cls, adaptive: bool = False, verbose: bool = True) -> None` *(class)* — Run a full scene audit and print the report.
  - `SceneAnalyzer.format_audit_text(cls, adaptive: bool = False, objects: Optional[List[Any]] = None, sections: Optional[List[str]] = None) -> Dict[str, str]` *(class)* — Run the audit and return the formatted report as a
  - `SceneAnalyzer.format_audit_html(cls, adaptive: bool = False, objects: Optional[List[Any]] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None, sections: Optional[List[str]] = None) -> Dict[str, str]` *(class)* — Run the audit and return a section-keyed dict of HTML
  - `SceneAnalyzer.analyze(self, objects: List[Any] = None, fast_mode: bool = True, progress_callback: Optional[Callable[[int, int, str], None]] = None, profile: AuditProfile = None, sections: Optional[List[str]] = None) -> List[AssetRecord]` — Main entry point for analysis.
  - `SceneAnalyzer.generate_report(self, records: List[AssetRecord]) -> SceneReport` — Build a :class:`SceneReport` from per-asset records.
  - `SceneAnalyzer.print_report(self, report: SceneReport, sections: Optional[List[str]] = None)` — Print the formatted scene-audit report to the logger.

<a id="core_utils--diagnostics--transform_diag"></a>
### `core_utils/diagnostics/transform_diag.py`

- **[`class TransformDiagnostics`](mayatk/mayatk/core_utils/diagnostics/transform_diag.py#L17)**
  - `TransformDiagnostics.fix_non_orthogonal_axes(objects=None, dry_run=False)` *(static)* — Fixes non-orthogonal axes on the given objects by freezing their transforms.

<a id="core_utils--diagnostics--uv_diag"></a>
### `core_utils/diagnostics/uv_diag.py`

UV diagnostics and repair helpers.

- **[`class UvSetCleanupResult`](mayatk/mayatk/core_utils/diagnostics/uv_diag.py#L20)** — Result of a UV set cleanup operation for a single mesh.
- **[`class UvDiagnostics`](mayatk/mayatk/core_utils/diagnostics/uv_diag.py#L41)** — Operations for inspecting and fixing common UV issues.
  - `UvDiagnostics.cleanup_uv_sets(cls, objects: NodeSeq, remove_empty: bool = True, keep_only_primary: bool = True, rename_to_map1: bool = True, force_rename: bool = False, prefer_largest_area: bool = False, dry_run: bool = False, quiet: bool = False) -> list[UvSetCleanupResult]` *(class)* — Cleanup UV sets by removing empty/secondary sets and renaming the primary to 'map1'.

<a id="core_utils--instancing--assembly_reconstructor"></a>
### `core_utils/instancing/assembly_reconstructor.py`

Logic for separating and reassembling mesh assemblies.

- **[`class AssemblyReconstructor`](mayatk/mayatk/core_utils/instancing/assembly_reconstructor.py#L33)** — Handles the separation and intelligent reassembly of combined meshes.
  - `AssemblyReconstructor.separate_combined_meshes(self, nodes: List[object]) -> List[object]` — Separate any combined meshes in the list into their shells.
  - `AssemblyReconstructor.center_transform_on_geometry(self, node) -> None` — Moves the transform to the center of its geometry without moving the geometry.
  - `AssemblyReconstructor.canonicalize_transform(self, node) -> None` — Aligns the transform's rotation to the geometry's PCA axes.
  - `AssemblyReconstructor.canonicalize_leaf_meshes(self, nodes: List[object]) -> List[object]` — Canonicalize all leaf mesh transforms for instancing.
  - `AssemblyReconstructor.reassemble_assemblies(self, nodes: List[object]) -> List[object]` — Reassemble separated shells into logical assemblies.
  - `AssemblyReconstructor.combine_reassembled_assemblies(self, nodes: List[object]) -> List[object]` — Combine the 'Core' of each reconstructed assembly into a single mesh.

<a id="core_utils--instancing--auto_instancer"></a>
### `core_utils/instancing/auto_instancer.py`

Scene auto-instancer prototype.

- **[`class InstanceCandidate`](mayatk/mayatk/core_utils/instancing/auto_instancer.py#L30)** — Holds information about a transform candidate for instancing.
  - `InstanceCandidate.transform(self) -> str` *(property)*
  - `InstanceCandidate.exists(self) -> bool`
- **[`class InstanceGroup`](mayatk/mayatk/core_utils/instancing/auto_instancer.py#L72)** — A group of objects that are geometrically identical.
- **[`class AutoInstancer(ptk.LoggingMixin)`](mayatk/mayatk/core_utils/instancing/auto_instancer.py#L83)** — Prototype workflow for converting matching meshes into instances.
  - `AutoInstancer.tolerance(self)` *(property)*
  - `AutoInstancer.tolerance(self, value)`
  - `AutoInstancer.require_same_material(self)` *(property)*
  - `AutoInstancer.require_same_material(self, value)`
  - `AutoInstancer.check_uvs(self)` *(property)*
  - `AutoInstancer.check_uvs(self, value)`
  - `AutoInstancer.verbose(self)` *(property)*
  - `AutoInstancer.verbose(self, value)`
  - `AutoInstancer.run(self, nodes: Optional[Sequence[object]] = None) -> List[object]` — Entry point for discovering and instancing matching meshes.
  - `AutoInstancer.find_instance_groups(self, nodes: Optional[Sequence[object]] = None) -> List[InstanceGroup]` — Finds groups of identical objects in the scene.

<a id="core_utils--instancing--geometry_matcher"></a>
### `core_utils/instancing/geometry_matcher.py`

Geometry analysis and matching logic for AutoInstancer.

- [`mesh_points(shape, world: bool = False)`](mayatk/mayatk/core_utils/instancing/geometry_matcher.py#L27) — ``MPointArray`` for *shape*.
- [`mesh_triangles(shape)`](mayatk/mayatk/core_utils/instancing/geometry_matcher.py#L33) — ``(counts, indices)`` from ``MFnMesh.getTriangles``, as plain lists.
- [`mesh_uv_set_names(shape)`](mayatk/mayatk/core_utils/instancing/geometry_matcher.py#L39)
- [`mesh_get_uvs(shape, uv_set=None)`](mayatk/mayatk/core_utils/instancing/geometry_matcher.py#L43)
- [`mesh_num_uvs(shape, uv_set=None)`](mayatk/mayatk/core_utils/instancing/geometry_matcher.py#L48)
- [`calculate_mesh_volume(node: str) -> float`](mayatk/mayatk/core_utils/instancing/geometry_matcher.py#L72) — Calculate mesh volume using the divergence theorem (numpy).
- **[`class ShellInfo`](mayatk/mayatk/core_utils/instancing/geometry_matcher.py#L103)** — Stores cached analysis data for a single shell.
- **[`class GeometryMatcher`](mayatk/mayatk/core_utils/instancing/geometry_matcher.py#L142)** — Handles geometric analysis and comparison.
  - `GeometryMatcher.quantize(self, value: float, precision: int = 4) -> float` — Round a value to a specific precision to ignore float noise.
  - `GeometryMatcher.get_pca_basis(self, node: str) -> Optional['om.MMatrix']` — Returns the PCA basis matrix (rotation only) for the node's mesh.
  - `GeometryMatcher.get_mesh_signature(self, transform: str, include_area: bool = True) -> Optional[Tuple]` — Get a lightweight signature for quick rejection.
  - `GeometryMatcher.are_meshes_identical(self, t1: str, t2: str) -> Tuple[bool, Optional['om.MMatrix']]` — Detailed geometric comparison using robust PCA alignment.
  - `GeometryMatcher.get_hierarchy_signature(self, node: str) -> Tuple` — Recursive signature generation for hierarchy comparison.
  - `GeometryMatcher.are_meshes_identical_with_transform(self, t1: str, t2: str, matrix) -> bool` — Check if t1 transformed by matrix matches t2.
  - `GeometryMatcher.are_hierarchies_identical(self, t1: str, t2: str, expected_transform: Optional['om.MMatrix'] = None, is_root: bool = False) -> Tuple[bool, Optional['om.MMatrix']]` — Detailed hierarchy comparison.

<a id="core_utils--instancing--instancing_strategy"></a>
### `core_utils/instancing/instancing_strategy.py`

Instancing strategy logic for AutoInstancer.

- **[`class StrategyType(Enum)`](mayatk/mayatk/core_utils/instancing/instancing_strategy.py#L16)**
- **[`class StrategyConfig`](mayatk/mayatk/core_utils/instancing/instancing_strategy.py#L24)**
- **[`class InstancingStrategy`](mayatk/mayatk/core_utils/instancing/instancing_strategy.py#L32)** — Determines the best instancing strategy for a group of objects.
  - `InstancingStrategy.evaluate(self, group_size: int, mesh_node: Optional[object] = None, triangle_count: Optional[int] = None) -> StrategyType` — Evaluate the strategy for a given group.

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

- [`cleanup_all_previews() -> None`](mayatk/mayatk/core_utils/preview.py#L632)
- **[`class CleanupContract`](mayatk/mayatk/core_utils/preview.py#L50)** — Captures and reverses side effects of a previewed operation.
  - `CleanupContract.add_file(self, path) -> None`
  - `CleanupContract.record_modification(self, node: str, attr: str) -> None`
  - `CleanupContract.rollback(self) -> None`
- **[`class Preview`](mayatk/mayatk/core_utils/preview.py#L285)** — Hermetic preview orchestrator (H1).
  - `Preview.cleanup_all_instances(cls) -> None` *(class)*
  - `Preview.init_show_hide_behavior(self, enable_on_show: bool, disable_on_hide: bool) -> None`
  - `Preview.conditionally_enable(self) -> None`
  - `Preview.conditionally_disable(self) -> None`
  - `Preview.toggle(self, state: bool) -> None`
  - `Preview.validate_operation(self, objects: List[Any]) -> bool`
  - `Preview.enable(self) -> None`
  - `Preview.refresh(self, *args) -> None` — Roll back the previous preview and re-run perform_operation.
  - `Preview.disable(self) -> None` — Roll back the preview without committing.
  - `Preview.finalize_changes(self) -> None` — Commit: rollback the hermetic version, then replay under undo.
  - `Preview.cleanup(self) -> None`
  - `Preview.enabled(self) -> bool` *(property)*
  - `Preview.operated_object_count(self) -> int` *(property)*
  - `Preview.get_operated_objects(self) -> List[str]`

<a id="core_utils--preview_old"></a>
### `core_utils/preview_old.py`

- [`cleanup_all_previews() -> None`](mayatk/mayatk/core_utils/preview_old.py#L505) — Clean up all Preview instances - useful for Maya session cleanup.
- **[`class Preview`](mayatk/mayatk/core_utils/preview_old.py#L13)** — Provides an interactive layer for previewing and finalizing operations in a 3D editing environment.
  - `Preview.cleanup_all_instances(cls) -> None` *(class)* — Clean up all Preview instances - useful for Maya session cleanup.
  - `Preview.safe_operation(func: Callable) -> Callable` — Decorator to safely execute operations with proper error handling.
  - `Preview.cleanup(self) -> None` — Clean up resources and remove from tracking.
  - `Preview.disable_on_external_undo(self) -> None` — Disables the preview functionality on external undo operations only.
  - `Preview.disable_on_selection_change(self) -> None` — Disable preview when the user changes selection mid-preview.
  - `Preview.init_show_hide_behavior(self, enable_on_show: bool, disable_on_hide: bool) -> None` — Initialize window show/hide behavior with improved error handling.
  - `Preview.eventFilter(self, obj, event)` — Handle window show/hide events when custom signals aren't available.
  - `Preview.conditionally_enable(self) -> None` — Enable preview if configured to do so on window show.
  - `Preview.conditionally_disable(self) -> None` — Disable preview if configured to do so on window hide.
  - `Preview.toggle(self, state: bool) -> None` — Toggles the preview on or off.
  - `Preview.validate_operation(self, objects: List[Any]) -> bool` — Validate that the operation can be performed on the given objects.
  - `Preview.enable(self) -> None` — Enables the preview and sets up the initial state.
  - `Preview.disable(self) -> None` — Disables the preview and reverts to the initial state.
  - `Preview.undo_if_needed(self) -> None` — Executes undo operation if required.
  - `Preview.refresh(self, *args)` — Refreshes the preview to reflect any changes.
  - `Preview.finalize_changes(self)` — Finalizes the preview changes and calls the finalize_func if provided.
  - `Preview.enabled(self) -> bool` *(property)* — Check if preview is currently enabled.
  - `Preview.has_changes(self) -> bool` *(property)* — Check if there are changes that need to be undone.
  - `Preview.operated_object_count(self) -> int` *(property)* — Get the number of objects being operated on.
  - `Preview.get_operated_objects(self) -> List[str]` — Get a copy of the list of operated objects.

<a id="core_utils--script_job_manager"></a>
### `core_utils/script_job_manager.py`

Centralized Maya event subscription manager.

- **[`class ScriptJobManager`](mayatk/mayatk/core_utils/script_job_manager.py#L71)** — Centralized Maya scriptJob event dispatcher.
  - `ScriptJobManager.instance(cls) -> 'ScriptJobManager'` *(class)* — Return the module-wide singleton, creating it on first access.
  - `ScriptJobManager.reset(cls) -> None` *(class)* — Tear down the singleton and allow a fresh one to be created.
  - `ScriptJobManager.subscribe(self, event: str, callback: Callable, *, owner: Any = None, ephemeral: bool = False) -> int` — Register *callback* for a Maya scriptJob *event*.
  - `ScriptJobManager.add_om_callback(self, register_fn: Callable, *register_args: Any, owner: Any = None) -> Optional[int]` — Register an OpenMaya ``MMessage`` callback under SJM management.
  - `ScriptJobManager.unsubscribe(self, token: int) -> None` — Remove a single subscription by *token* (script job or OM).
  - `ScriptJobManager.unsubscribe_all(self, owner: Any) -> None` — Remove every subscription registered under *owner* (both kinds).
  - `ScriptJobManager.connect_cleanup(self, widget, owner: Any) -> None` — Connect *widget*.destroyed → :meth:`unsubscribe_all` for *owner*.
  - `ScriptJobManager.suppress(self, token: int) -> None` — Temporarily silence a subscription without removing it.
  - `ScriptJobManager.resume(self, token: int) -> None` — Re-enable a previously suppressed subscription.
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

<a id="display_utils--color_manager"></a>
### `display_utils/color_manager.py`

- **[`class ColorUtils`](mayatk/mayatk/display_utils/color_manager.py#L17)**
  - `ColorUtils.assign_material(obj: str, color: Tuple[float, float, float]) -> str` *(static)* — Assigns a material to an object based on the RGB value.
  - `ColorUtils.set_color_attribute(cls, obj: str, color: Tuple[float, float, float], attr_type: str, force: bool = False) -> None` *(class)* — Applies color based on the attribute type specified, optionally overriding attribute locks.
  - `ColorUtils.get_material_color(obj: str) -> Optional[Tuple[float, float, float]]` *(static)* — Gets the color of the object's material.
  - `ColorUtils.get_wireframe_color(obj: str, normalize: bool = False) -> Optional[Tuple[float, float, float]]` *(static)* — Gets the wireframe color of the given object.
  - `ColorUtils.get_vertex_color(obj: str, vertex_id: int) -> Optional[Tuple[float, float, float]]` *(static)* — Gets the color of a specific vertex on the object.
  - `ColorUtils.set_vertex_color(objects: List[str], color: Tuple[float, float, float]) -> None` *(static)* — Applies the specified color to the object's vertices.
  - `ColorUtils.get_color_difference(color1: Tuple[float, float, float], color2: Tuple[float, float, float]) -> float` *(static)* — Calculate the average difference between two RGB colors.
- **[`class ColorManager(ColorUtils)`](mayatk/mayatk/display_utils/color_manager.py#L163)**
  - `ColorManager.apply_color(cls, objects: List[str], color: Optional[Tuple[float, float, float]] = None, apply_to_material: bool = False, apply_to_vertex: bool = False, apply_to_wireframe: bool = False, apply_to_outliner: bool = False) -> None` *(class)* — Applies color based on given criteria to objects.
  - `ColorManager.get_objects_by_color(cls, target_color: Tuple[float, float, float], threshold: float = 0.1, check_material_color: bool = False, check_vertex_color: bool = False, check_wireframe_color: bool = False, check_outliner_color: bool = False) -> List[str]` *(class)* — Select objects by color, with optional checks for material, vertex, wireframe, and outliner colors.
  - `ColorManager.reset_colors(cls, objects: List[str], reset_outliner: bool = True, reset_wireframe: bool = True, reset_vertex: bool = True, reset_material: bool = True) -> None` *(class)* — Resets colors to default for given objects, with options to specify which color types to reset.
  - `ColorManager.reset_vertex_colors(objects: List[str]) -> None` *(static)* — Resets vertex colors for the given object(s), handling potential errors gracefully.
- **[`class ColorManagerSlots(ColorManager)`](mayatk/mayatk/display_utils/color_manager.py#L336)**
  - `ColorManagerSlots.header_init(self, widget)` — Configure header help text and preset combobox.
  - `ColorManagerSlots.selected_objects(self) -> List[str]` *(property)* — Return the currently selected objects, or an empty list if no objects are selected.
  - `ColorManagerSlots.selected_button(self) -> Optional[object]` *(property)* — Return the currently selected button in the button group.
  - `ColorManagerSlots.target_color(self) -> Optional[Tuple[float, float, float]]` *(property)* — Return the color of the selected button, or None if no button is selected.
  - `ColorManagerSlots.b000(self) -> None` — Reset Colors
  - `ColorManagerSlots.b001(self) -> None` — Apply selected color to selected objects.
  - `ColorManagerSlots.b002(self) -> None` — Select objects by the currently selected color.
  - `ColorManagerSlots.b003(self) -> None`

<a id="display_utils--exploded_view"></a>
### `display_utils/exploded_view.py`

- **[`class ExplodedView`](mayatk/mayatk/display_utils/exploded_view.py#L21)**
  - `ExplodedView.objects(self) -> list` *(property)* — Return assigned objects or fallback to current selection.
  - `ExplodedView.objects(self, value: list)`
  - `ExplodedView.calculate_repulsive_force_vectorized(cls, positions, sizes, scale=0.05)` *(class)* — Vectorized calculation of repulsive forces between objects.
  - `ExplodedView.arrange_objects(self, nodes: list, convergence_threshold: float = 0.0001, max_iterations: int = 1000, max_movement: float = 1.0) -> int` — Arranges a list of objects in 3D space to avoid overlap.
  - `ExplodedView.explode(self)` — Explode the objects.
  - `ExplodedView.un_explode(self)` — Un-explode the objects.
  - `ExplodedView.toggle_explode(self)` — Toggle explode state of the objects.
  - `ExplodedView.un_explode_all(self)` — Un-explode all
- **[`class ExplodedViewSlots(ExplodedView)`](mayatk/mayatk/display_utils/exploded_view.py#L231)** — Exploded View Slots
  - `ExplodedViewSlots.header_init(self, widget)` — Configure header help text.
  - `ExplodedViewSlots.b000(self)` — Explode button
  - `ExplodedViewSlots.b001(self)` — Un-explode selected button
  - `ExplodedViewSlots.b002(self)` — Un-explode all button
  - `ExplodedViewSlots.b003(self)` — Toggle Exlode

<a id="edit_utils--_edit_utils"></a>
### `edit_utils/_edit_utils.py`

- **[`class EditUtils(ptk.HelpMixin)`](mayatk/mayatk/edit_utils/_edit_utils.py#L48)**
  - `EditUtils.combine_objects(objects=None, group_by_material=False, cluster_by_distance=False, threshold=10000.0, **kwargs)` *(static)* — Combine multiple meshes.
  - `EditUtils.group_objects(objects=None)` *(static)* — Group the given objects (or selection), center the pivot, and rename the group.
  - `EditUtils.separate_objects(objects=None, by_material: bool = False, group_by_material: bool = False, center_pivots: bool = True, rename: bool = False) -> List` *(static)* — Separate meshes into individual objects.
  - `EditUtils.merge_vertices(objects, tolerance=0.001, selected_only=False)` *(static)* — Merge Vertices on the given objects.
  - `EditUtils.merge_vertex_pairs(vertices)` *(static)* — Merge vertices in pairs by moving them to their center and merging.
  - `EditUtils.detach_components(components=None, duplicate: bool = True, separate: bool = True, offset: bool = False, keep_faces_together: bool = True) -> Optional[List]` *(static)* — Detach mesh components (vertices or faces) from their parent mesh.
  - `EditUtils.get_all_faces_on_axis(obj, axis='x', pivot='center', use_object_axes=True)` *(static)* — Get all faces on the specified axis of an object.
  - `EditUtils.cut_along_axis(cls, objects, axis='x', pivot='center', amount=1, offset=0, invert=False, ortho=False, delete=False, mirror=False, use_object_axes=True)` *(class)* — Cut objects along the specified axis.
  - `EditUtils.delete_along_axis(cls, objects, axis='-x', pivot='center', delete_history=True, mirror=False, use_object_axes=True)` *(class)* — Delete faces along the specified axis and optionally mirror the result.
  - `EditUtils.mirror(cls, objects, axis: str = 'x', pivot: Union[str, tuple] = 'object', mergeMode: int = -1, uninstance: bool = False, use_object_axes: bool = True, delete_original: bool = False, **kwargs)` *(class)* — Mirror geometry across a given axis.
  - `EditUtils.separate_mirrored_mesh(mirror_node: str, preserve_pivot: bool = True, delete_original: bool = False) -> Optional[str]` *(static)* — Separate mirrored geometry and clean up hierarchy, history, and parenting.
  - `EditUtils.get_overlapping_duplicates(objects: Optional[List] = None, retain_given_objects: bool = False, select: bool = False, verbose: bool = False) -> set` *(static)* — Find duplicate, overlapping geometry at the object (transform) level.
  - `EditUtils.find_non_manifold_vertex(objects, select=1)` *(static)* — Locate a connected vertex of non-manifold geometry where the faces share a single vertex.
  - `EditUtils.split_non_manifold_vertex(vertex, select=True)` *(static)* — Separate a connected vertex of non-manifold geometry where the faces share a single vertex.
  - `EditUtils.get_overlapping_vertices(objects, threshold=0.0003)` *(static)* — Query the given objects for overlapping vertices.
  - `EditUtils.get_overlapping_faces(cls, objects, delete_history=False)` *(class)* — Get any duplicate overlapping faces of the given objects.
  - `EditUtils.get_similar_mesh(objects, tolerance=0.0, inc_orig=False, select=False, **kwargs)` *(static)* — Find similar geometry objects using the polyEvaluate command.
  - `EditUtils.get_similar_topo(obj, inc_orig=False, **kwargs)` *(static)* — Find similar geometry objects using the polyCompare command.
  - `EditUtils.invert_geometry(objects: Optional[List] = None, select: bool = False) -> List[str]` *(static)* — Invert selection to unselected mesh transforms.
  - `EditUtils.invert_components(objects: Optional[List] = None, select: bool = False) -> List[Union['pm.MeshVertex', 'pm.MeshEdge', 'pm.MeshFace']]` *(static)* — Invert selection of mesh components (verts, edges, or faces).
  - `EditUtils.delete_selected()` *(static)* — Delete selected components and/or objects in Autodesk Maya.
  - `EditUtils.create_curve_from_edges(edges: Optional[List[str]] = None, **kwargs)` *(static)* — Create a curve from selected polygon edges or a provided list of edges.

<a id="edit_utils--bevel"></a>
### `edit_utils/bevel.py`

- **[`class Bevel`](mayatk/mayatk/edit_utils/bevel.py#L10)**
  - `Bevel.bevel(edges, width=0.5, segments=1, autoFit=True, depth=1, mitering=0, miterAlong=0, chamfer=True, worldSpace=True, smoothingAngle=30, fillNgons=True, mergeVertices=True, mergeVertexTolerance=0.0001, miteringAngle=180, angleTolerance=180)` *(static)* — Bevels the given edges with highly customizable options for topology,
- **[`class BevelSlots`](mayatk/mayatk/edit_utils/bevel.py#L75)**
  - `BevelSlots.header_init(self, widget)` — Configure header help text.
  - `BevelSlots.perform_operation(self, objects, contract)`

<a id="edit_utils--bridge"></a>
### `edit_utils/bridge.py`

- **[`class Bridge`](mayatk/mayatk/edit_utils/bridge.py#L14)**
  - `Bridge.bridge(edges, **kwargs)` *(static)*
  - `Bridge.get_child_curves_from_bridge(mesh_nodes)` *(static)* — Find child curves created by polyBridgeEdge operations on mesh nodes.
  - `Bridge.cleanup_bridge_curves_and_history(mesh_nodes)` *(static)* — Clean up child curves and deformer history from mesh nodes.
- **[`class BridgeSlots`](mayatk/mayatk/edit_utils/bridge.py#L80)**
  - `BridgeSlots.header_init(self, widget)` — Configure header help text.
  - `BridgeSlots.perform_operation(self, objects, contract)`

<a id="edit_utils--cut_on_axis"></a>
### `edit_utils/cut_on_axis.py`

- **[`class CutOnAxis`](mayatk/mayatk/edit_utils/cut_on_axis.py#L13)**
  - `CutOnAxis.perform_cut_on_axis(objects, axis='-x', cuts=0, cut_offset=0, delete=False, mirror=False, pivot='manip', use_object_axes=True)` *(static)* — Iterates over provided objects and performs cut or delete operations based on the axis specified.
- **[`class CutOnAxisSlots`](mayatk/mayatk/edit_utils/cut_on_axis.py#L55)**
  - `CutOnAxisSlots.header_init(self, widget)` — Configure header help text.
  - `CutOnAxisSlots.perform_operation(self, objects, contract)`

<a id="edit_utils--duplicate_grid"></a>
### `edit_utils/duplicate_grid.py`

- **[`class DuplicateGrid(ptk.LoggingMixin)`](mayatk/mayatk/edit_utils/duplicate_grid.py#L17)**
  - `DuplicateGrid.duplicate_grid(cls, objects: List[str], dimensions: Tuple[int, int, int], spacing: float = 0, instance: bool = True, group: bool = True) -> Union[str, List[str]]` *(class)* — Duplicate objects in a grid pattern.
- **[`class DuplicateGridSlots(ptk.LoggingMixin)`](mayatk/mayatk/edit_utils/duplicate_grid.py#L177)**
  - `DuplicateGridSlots.header_init(self, widget)` — Configure header help text.
  - `DuplicateGridSlots.b001(self)` — Reset to Defaults: Resets all UI widgets to their default values.
  - `DuplicateGridSlots.perform_operation(self, objects, contract)`

<a id="edit_utils--duplicate_linear"></a>
### `edit_utils/duplicate_linear.py`

- **[`class DuplicateLinear`](mayatk/mayatk/edit_utils/duplicate_linear.py#L22)**
  - `DuplicateLinear.duplicate_linear(objects, num_copies, translate=(0, 0, 0), rotate=(0, 0, 0), scale=(1, 1, 1), weight_bias=0.5, weight_curve=4, pivot='object', calculation_mode='weighted', instance=True)` *(static)*
- **[`class DuplicateLinearSlots`](mayatk/mayatk/edit_utils/duplicate_linear.py#L130)**
  - `DuplicateLinearSlots.header_init(self, widget)` — Configure header help text.
  - `DuplicateLinearSlots.toggle_weight_ui(self)` — Disable weight UI components if the current calculation mode doesn't use them.
  - `DuplicateLinearSlots.b001(self)` — Reset to Defaults: Resets all UI widgets to their default values.
  - `DuplicateLinearSlots.perform_operation(self, objects, contract)` — Perform the linear duplication operation.

<a id="edit_utils--duplicate_radial"></a>
### `edit_utils/duplicate_radial.py`

- **[`class DuplicateRadial(ptk.LoggingMixin)`](mayatk/mayatk/edit_utils/duplicate_radial.py#L23)**
  - `DuplicateRadial.duplicate_radial(objects: List[str], num_copies: int, start_angle: float = 0, end_angle: float = 360, weight_bias: float = 0.5, weight_curve: float = 0.5, rotate_axis: str = 'y', offset: Tuple[float, float, float] = (0, 0, 0), translate: Tuple[float, float, float] = (0, 0, 0), rotate: Tuple[float, float, float] = (0, 0, 0), scale: Tuple[float, float, float] = (1, 1, 1), pivot: Union[str, Tuple[float, float, float]] = 'object', keep_original: bool = False, instance: bool = False, combine: bool = False, suffix: bool = True) -> Dict[str, List[str]]` *(static)* — Duplicate objects in a radial pattern.
- **[`class DuplicateRadialSlots(ptk.LoggingMixin)`](mayatk/mayatk/edit_utils/duplicate_radial.py#L273)**
  - `DuplicateRadialSlots.header_init(self, widget)` — Configure header help text.
  - `DuplicateRadialSlots.b001(self)` — Reset to Defaults: Resets all UI widgets to their default values.
  - `DuplicateRadialSlots.perform_operation(self, objects, contract)` — Perform the radial duplication operation.
  - `DuplicateRadialSlots.regroup_copies(self)` — Regroup the instances under their original parent group.

<a id="edit_utils--dynamic_pipe"></a>
### `edit_utils/dynamic_pipe.py`

- **[`class DynamicPipe`](mayatk/mayatk/edit_utils/dynamic_pipe.py#L11)** — Build a pipe-style mesh by lofting NURBS circles parented to a chain of locators.
  - `DynamicPipe.create_pipe_geometry(self, segments_to_loft: Optional[Sequence[int]] = None) -> List[str]` — Loft consecutive circle pairs to produce pipe segments.
- **[`class DynamicPipeSlots`](mayatk/mayatk/edit_utils/dynamic_pipe.py#L143)** — Switchboard slot wiring for the dynamic_pipe UI.
  - `DynamicPipeSlots.header_init(self, widget)` — Configure header help text.
  - `DynamicPipeSlots.b000(self)` — Initialize Pipe — build pipe from the current ordered selection.

<a id="edit_utils--macros"></a>
### `edit_utils/macros.py`

- **[`class MacroManager(ptk.HelpMixin)`](mayatk/mayatk/edit_utils/macros.py#L24)** — Assign macro functions to hotkeys.
  - `MacroManager.set_macros(cls, *args)` *(class)* — Extends `set_macro` to accept a list of strings representing positional and keyword arguments.
  - `MacroManager.call_with_input(func, input_string)` *(static)* — Parses an input string into positional and keyword arguments, and
  - `MacroManager.set_macro(cls, name, key=None, cat=None, ann=None, default=False, delete_existing=True)` *(class)* — Sets a default runtime command with a keyboard shortcut.
- **[`class DisplayMacros`](mayatk/mayatk/edit_utils/macros.py#L171)**
  - `DisplayMacros.m_component_id_display()` *(static)* — Toggle Component Id Display through vertices, edges, faces, UVs, and off.
  - `DisplayMacros.m_normals_display()` *(static)* — Toggle face normals, vertex normals, tangents, and off.
  - `DisplayMacros.m_soft_edge_display()` *(static)* — Toggle Soft Edge Display.
  - `DisplayMacros.m_toggle_visibility()` *(static)* — Toggle Visibility
  - `DisplayMacros.m_toggle_uv_border_edges(objects)` *(static)* — Toggle the display of UV border edges for the given objects.
  - `DisplayMacros.m_back_face_culling(objects) -> None` *(static)* — Toggle Back-Face Culling on selected objects, or on all objects if none are selected.
  - `DisplayMacros.m_isolate_selected() -> None` *(static)* — Isolate the current selection.
  - `DisplayMacros.m_cycle_display_state(objects) -> None` *(static)* — Cycle the display state of all selected objects based on the first object's state.
  - `DisplayMacros.m_wireframe_toggle(objects) -> None` *(static)* — Toggle Wireframe Display on selected objects, or on all objects if none are selected.
  - `DisplayMacros.m_grid_and_image_planes() -> None` *(static)* — Toggle grid and image plane visibility.
  - `DisplayMacros.m_frame(objects) -> None` *(static)* — Frame selected by a set amount with three toggle states.
  - `DisplayMacros.m_smooth_preview(cls, objects) -> None` *(class)* — Toggle smooth mesh preview.
  - `DisplayMacros.m_wireframe() -> None` *(static)* — Toggles the wireframe display state.
  - `DisplayMacros.m_material_override()` *(static)* — Toggle Material Override
  - `DisplayMacros.m_shading(cls) -> None` *(class)* — Toggles viewport display mode between wireframe, smooth shaded with textures off,
  - `DisplayMacros.m_lighting(cls) -> None` *(class)* — Toggles viewport lighting between different states: default, all lights, active lights,
- **[`class EditMacros`](mayatk/mayatk/edit_utils/macros.py#L704)**
  - `EditMacros.m_group(objects=None)` *(static)* — Group the given objects (or selection), center the pivot, and rename the group.
  - `EditMacros.m_combine(objects=None, group_by_material=False, cluster_by_distance=False, threshold=10000.0, **kwargs)` *(static)* — Combine multiple meshes.
  - `EditMacros.m_boolean(objects, repair_mesh=True, keep_boolean=True, **kwargs)` *(static)* — Perform a boolean operation on two meshes using cmds, managing shorthand and full parameter names d…
  - `EditMacros.m_lock_vertex_normals(objects)` *(static)* — Toggle lock/unlock vertex normals.
  - `EditMacros.m_paste_and_rename() -> None` *(static)* — Paste and rename by removing 'pasted__' prefix and reference file names,
  - `EditMacros.m_multi_component() -> None` *(static)* — Multi-Component Selection.
  - `EditMacros.m_merge_vertices(objects, tolerance=0.001) -> None` *(static)* — Merge Vertices.
- **[`class SelectionMacros`](mayatk/mayatk/edit_utils/macros.py#L951)**
  - `SelectionMacros.m_object_selection() -> None` *(static)* — Set object selection mask.
  - `SelectionMacros.m_vertex_selection() -> None` *(static)* — Set vertex selection mask.
  - `SelectionMacros.m_edge_selection() -> None` *(static)* — Set edge selection mask.
  - `SelectionMacros.m_face_selection() -> None` *(static)* — Set face selection mask.
  - `SelectionMacros.m_invert_selection() -> None` *(static)* — Invert the current selection of geometry or components.
  - `SelectionMacros.m_toggle_selectability(objects)` *(static)* — Toggle selectability of the given objects.
  - `SelectionMacros.m_toggle_UV_select_type() -> None` *(static)* — Toggles between UV shell and UV component selection.
  - `SelectionMacros.m_invert_component_selection() -> None` *(static)* — Invert the component selection on the currently selected objects.
- **[`class UiMacros`](mayatk/mayatk/edit_utils/macros.py#L1112)**
  - `UiMacros.m_toggle_panels(toggle_menu: bool = True, toggle_panels: bool = True) -> None` *(static)* — Toggle UI toolbars and menu bar in sync.
- **[`class AnimationMacros`](mayatk/mayatk/edit_utils/macros.py#L1148)**
  - `AnimationMacros.m_set_selected_keys(objects) -> None` *(static)* — Set keys for any attributes (channels) that are selected in the channel box.
  - `AnimationMacros.m_unset_selected_keys(objects) -> None` *(static)* — Un-set keys for any attributes (channels) that are selected in the channel box.
- **[`class Macros(MacroManager, DisplayMacros, EditMacros, SelectionMacros, AnimationMacros, UiMacros)`](mayatk/mayatk/edit_utils/macros.py#L1175)**

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

- **[`class MirrorSlots(ptk.LoggingMixin)`](mayatk/mayatk/edit_utils/mirror.py#L12)**
  - `MirrorSlots.header_init(self, widget)` — Configure header help text.
  - `MirrorSlots.perform_operation(self, objects, contract)`

<a id="edit_utils--naming--_naming"></a>
### `edit_utils/naming/_naming.py`

- **[`class Naming(ptk.HelpMixin)`](mayatk/mayatk/edit_utils/naming/_naming.py#L20)**
  - `Naming.rename(cls, objects: Union[str, 'object', List[Union[str, 'object']]], to: str, fltr: str = '', regex: bool = False, ignore_case: bool = False, retain_suffix: bool = False, valid_suffixes: Optional[List[str]] = None) -> List[str]` *(class)* — Rename scene objects based on specified patterns and filters, ensuring compliance with Maya's namin…
  - `Naming.generate_unique_name(cls, base_name, suffix='_', padding=3)` *(class)* — Generate a unique name based on the base_name.
  - `Naming.strip_illegal_chars(input_data, replace_with='_')` *(static)* — Strips illegal characters from a string or a list of strings, replacing them with a specified chara…
  - `Naming.strip_chars(objects: Union[str, object, List[Union[str, object]]], num_chars: int = 1, trailing: bool = False) -> List[str]` *(static)* — Deletes leading or trailing characters from the names of the provided objects,
  - `Naming.set_case(objects=None, case='capitalize')` *(static)* — Rename objects following the given case.
  - `Naming.suffix_by_type(objects: Union[str, object, List[Union[str, object]]], group_suffix: str = '_GRP', locator_suffix: str = '_LOC', joint_suffix: str = '_JNT', mesh_suffix: str = '_GEO', nurbs_curve_suffix: str = '_CRV', camera_suffix: str = '_CAM', light_suffix: str = '_LGT', display_layer_suffix: str = '_LYR', custom_suffixes: Optional[Dict[str, str]] = None, strip: Union[str, List[str]] = None, strip_trailing_ints: bool = False, strip_trailing_underscores: bool = False, strip_trailing_padding: bool = True) -> List[str]` *(static)* — Appends a conventional suffix based on Maya object type, stripping any existing known suffix.
  - `Naming.append_location_based_suffix(objects, first_obj_as_ref=False, alphabetical=False, strip_trailing_ints=True, strip_defined_suffixes=True, valid_suffixes=None, reverse=False, independent_groups=False)` *(static)* — Rename objects with a suffix defined by its location from origin.

<a id="edit_utils--naming--naming_slots"></a>
### `edit_utils/naming/naming_slots.py`

- **[`class NamingSlots(Naming, ptk.LoggingMixin)`](mayatk/mayatk/edit_utils/naming/naming_slots.py#L17)**
  - `NamingSlots.header_init(self, widget)` — Configure header menu with tool description and workflow instructions.
  - `NamingSlots.valid_suffixes(self)` *(property)* — Get current valid suffixes from tb003 widget fields.
  - `NamingSlots.txt000_init(self, widget)` — Initialize Find
  - `NamingSlots.txt000(self, widget)` — Find
  - `NamingSlots.txt001_init(self, widget)` — Initialize Rename
  - `NamingSlots.txt001(self, widget)` — Rename
  - `NamingSlots.tb000_init(self, widget)` — Initialize Convert Case
  - `NamingSlots.tb000(self, widget)` — Convert Case
  - `NamingSlots.tb001_init(self, widget)` — Initialize Suffix By Location
  - `NamingSlots.tb001(self, widget)` — Suffix By Location
  - `NamingSlots.tb002_init(self, widget)` — Initialize Strip Chars
  - `NamingSlots.tb002(self, widget)` — Strip Chars
  - `NamingSlots.tb003_init(self, widget)` — Initialize Suffix By Type
  - `NamingSlots.tb003(self, widget)` — Suffix By Type

<a id="edit_utils--primitives"></a>
### `edit_utils/primitives.py`

Primitive creation utilities for Maya.

- **[`class Primitives`](mayatk/mayatk/edit_utils/primitives.py#L23)** — Utilities for creating primitive objects in Maya.
  - `Primitives.create_default_primitive(cls, baseType, subType, **kwargs)` *(class)* — Create a primitive object with flexible parameters.
  - `Primitives.create_circle(axis='y', numPoints=12, radius=5, center=[0, 0, 0], mode=0, name='pCircle', history=False)` *(static)* — Create a circular polygon plane.

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

- **[`class Snap(ptk.HelpMixin)`](mayatk/mayatk/edit_utils/snap.py#L18)** — Vertex and mesh snapping utilities.
  - `Snap.snap_to_closest_vertex(obj1, obj2, tolerance=10.0, freeze_transforms=False)` *(static)* — Snap the vertices from object one to the closest verts on object two.
  - `Snap.snap_to_surface(source_meshes, target_mesh, offset: float = None, threshold: float = None, invert: bool = False) -> int` *(static)* — Snap source mesh vertices to the closest point on a target surface.
  - `Snap.snap_to_grid(objects=None, grid_size: float = 1.0, axes: str = 'xyz') -> int` *(static)* — Snap object pivots or vertices to the nearest grid point.
- **[`class SnapSlots`](mayatk/mayatk/edit_utils/snap.py#L256)** — UI slots for the Snap tool.
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
  - `EnvUtils.default_artifact_dir(cls) -> str` *(class)* — Return a sensible default directory for exported/baked artifacts.
  - `EnvUtils.append_maya_paths(maya_version=None)` *(static)* — Appends various Maya-related paths to the system's Python environment and sys.path.
  - `EnvUtils.load_plugin(plugin_name)` *(static)* — Loads a specified plugin.
  - `EnvUtils.vray_plugin(load=False, unload=False, query=False)` *(static)* — Load/Unload/Query the Maya Vray Plugin.
  - `EnvUtils.get_recent_files(index=None)` *(static)* — Get a list of recent files sorted by modification time.
  - `EnvUtils.get_recent_projects(index=None, format='standard')` *(static)* — Get a list of recently set projects.
  - `EnvUtils.find_autosave_directories()` *(static)* — Search for and compile a list of existing autosave directories based on
  - `EnvUtils.get_recent_autosave(cls, filter_time=None, timestamp_format='%Y-%m-%d %H:%M:%S')` *(class)* — Retrieves a list of recent autosave files from Maya autosave directories, optionally filtered by ag…
  - `EnvUtils.find_workspaces(root_dir: str, return_type: str = 'dir', ignore_empty: bool = True, recursive: bool = True) -> list` *(static)* — Recursively find Maya workspaces under a root directory.
  - `EnvUtils.get_workspace_scenes(root_dir: Optional[str] = None, full_path: bool = True, recursive: bool = False, omit_autosave: bool = True, file_types=['*.ma', '*.mb']) -> list[str]` *(static)* — Return a list of Maya scene files (.ma/.mb) from the given or current workspace directory.
  - `EnvUtils.find_workspace_using_path(cls, scene_path: Optional[str] = None) -> Optional[str]` *(class)* — Determine the workspace directory for a given scene by moving up directory levels until a workspace…
  - `EnvUtils.reference_scene(file_path)` *(static)* — Reference a Maya scene.
  - `EnvUtils.remove_reference(file_path)` *(static)* — Remove a reference to a Maya scene.
  - `EnvUtils.is_referenced(file_path)` *(static)* — Check if a Maya scene is referenced.
  - `EnvUtils.get_reference_nodes(file_path)` *(static)* — Get the nodes from a referenced Maya scene.
  - `EnvUtils.list_references()` *(static)* — List all references in the current Maya scene.
  - `EnvUtils.export_scene_as_fbx(file_path: str = None, *, selection_only: bool = False, **fbx_options: Any) -> None` *(static)* — Export the Maya scene as an FBX file with flexible MEL command options.
  - `EnvUtils.sanitize_namespace(namespace: str) -> str` *(static)* — Sanitize the namespace by replacing or removing illegal characters.
  - `EnvUtils.resolve_file_path_in_workspaces(selected_file: str, workspace_files: dict) -> Optional[str]` *(static)* — Resolve a file name to its full path by searching in workspace files.
  - `EnvUtils.get_workspace_file_cache(cls, workspaces: list, recursive: bool = True) -> dict` *(class)* — Build a cache of workspace files for multiple workspaces.
  - `EnvUtils.matches_autosave_pattern(filename: str) -> bool` *(static)* — Check if a file matches the Maya autosave pattern.
  - `EnvUtils.save_scene_backup(backup_path: Optional[Union[str, bool]] = True, suffix: str = '_backup', file_type: str = 'mayaAscii', force: bool = True, preserve_scene_name: bool = True) -> Optional[str]` *(static)* — Save a backup copy of the current scene.
  - `EnvUtils.find_original_for_autosave(cls, autosave_path: Optional[str] = None) -> Optional[str]` *(class)* — Resolve the original scene file an autosave was generated from.
  - `EnvUtils.save_autosave_to_original(cls, original_path: Optional[str] = None, backup_existing: bool = True) -> Optional[str]` *(class)* — Save the currently open autosave scene back to its original path.

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

- **[`class FbxUtils(ptk.HelpMixin)`](mayatk/mayatk/env_utils/fbx_utils.py#L18)** — Low-level utilities for FBX export operations in Maya.
  - `FbxUtils.load_plugin()` *(static)* — Ensure the fbxmaya plugin is loaded.
  - `FbxUtils.set_fbx_options(options: Dict[str, Any])` *(static)* — Apply FBX export options via MEL commands.
  - `FbxUtils.load_preset(preset_path: str)` *(static)* — Load an FBX export preset file.
  - `FbxUtils.export(cls, file_path: str, objects: Optional[List] = None, preset_file: Optional[str] = None, options: Optional[Dict[str, Any]] = None, selection_only: bool = True) -> str` *(class)* — Export geometry to an FBX file.

<a id="env_utils--hierarchy_manager--_hierarchy_manager"></a>
### `env_utils/hierarchy_manager/_hierarchy_manager.py`

- [`get_clean_node_name(node) -> str`](mayatk/mayatk/env_utils/hierarchy_manager/_hierarchy_manager.py#L67) — Get a consistent clean node name for matching (strips namespace).
- [`get_clean_node_name_from_string(node_name: str) -> str`](mayatk/mayatk/env_utils/hierarchy_manager/_hierarchy_manager.py#L83) — Get a clean node name from a string path (removes namespace prefix).
- [`clean_hierarchy_path(path: str) -> str`](mayatk/mayatk/env_utils/hierarchy_manager/_hierarchy_manager.py#L91) — Clean namespace prefixes from all components of a hierarchical path.
- [`format_component(name: str, strip_namespaces: bool = False) -> str`](mayatk/mayatk/env_utils/hierarchy_manager/_hierarchy_manager.py#L99) — Format a single component name with optional namespace stripping.
- [`is_default_maya_camera(path: str, node) -> bool`](mayatk/mayatk/env_utils/hierarchy_manager/_hierarchy_manager.py#L111) — Check if *node* represents a Maya default camera.
- [`should_keep_node_by_type(node, node_types: List[str], exclude: bool = True) -> bool`](mayatk/mayatk/env_utils/hierarchy_manager/_hierarchy_manager.py#L127) — Filter nodes by shape types.
- [`filter_path_map_by_cameras(path_map: Dict[str, Any]) -> Dict[str, Any]`](mayatk/mayatk/env_utils/hierarchy_manager/_hierarchy_manager.py#L141) — Remove Maya default cameras from *path_map*.
- [`filter_path_map_by_types(path_map: Dict[str, Any], node_types: List[str], exclude: bool = True) -> Dict[str, Any]`](mayatk/mayatk/env_utils/hierarchy_manager/_hierarchy_manager.py#L150) — Filter *path_map* by shape node types.
- [`select_objects_in_maya(object_names: List[str]) -> int`](mayatk/mayatk/env_utils/hierarchy_manager/_hierarchy_manager.py#L161) — Select objects in Maya scene by name.
- **[`class HierarchyMapBuilder`](mayatk/mayatk/env_utils/hierarchy_manager/_hierarchy_manager.py#L220)** — Builds hierarchy path maps for Maya transforms.
  - `HierarchyMapBuilder.build_path_map(root, exclude_namespace_prefixes: List[str] = None, strip_namespaces: bool = False) -> Dict[str, Any]` *(static)* — Build a mapping of hierarchical paths to transform nodes.
  - `HierarchyMapBuilder.build_path_map_from_nodes(nodes: List[Any], strip_namespaces: bool = False) -> Dict[str, Any]` *(static)* — Build a path map from an arbitrary list of transform node names.
- **[`class MayaObjectMatcher(ptk.LoggingMixin)`](mayatk/mayatk/env_utils/hierarchy_manager/_hierarchy_manager.py#L324)** — Maya-specific object matching with fuzzy logic and container searches.
  - `MayaObjectMatcher.find_matches(self, target_objects: List[str], imported_transforms: List, dry_run: bool = False) -> Tuple[List, Dict]` — Find matching objects using exact and fuzzy matching.
- **[`class HierarchyManager(ptk.LoggingMixin)`](mayatk/mayatk/env_utils/hierarchy_manager/_hierarchy_manager.py#L430)** — Core hierarchy analysis and repair manager.
  - `HierarchyManager.analyze_hierarchies(self, current_tree_root=None, reference_tree_root=None, reference_objects: List = None, filter_meshes: bool = True, filter_cameras: bool = False, filter_lights: bool = False, inc_names: Optional[List[str]] = None, exc_names: Optional[List[str]] = None, inc_types: Optional[List[str]] = None, exc_types: Optional[List[str]] = None) -> Dict[str, Any]` — Analyze differences between current and reference hierarchies.
  - `HierarchyManager.create_stubs(self, paths: Optional[List[str]] = None) -> List[str]` — Create empty transform stubs for missing hierarchy paths.
  - `HierarchyManager.quarantine_extras(self, group: str = '_QUARANTINE', paths: Optional[List[str]] = None, skip_animated: bool = True) -> List[str]` — Move extra (scene-only) items to a root-level quarantine group.
  - `HierarchyManager.fix_fuzzy_renames(self, items: Optional[List[Dict[str, str]]] = None, skip_animated: bool = True) -> List[str]` — Rename nodes identified as fuzzy matches to their reference names.
  - `HierarchyManager.fix_reparented(self, items: Optional[List[Dict[str, str]]] = None) -> List[str]` — Move reparented nodes to match their reference hierarchy position.
- **[`class ObjectSwapper(ptk.LoggingMixin)`](mayatk/mayatk/env_utils/hierarchy_manager/_hierarchy_manager.py#L1820)** — Handles cross-scene object operations like push/pull.
  - `ObjectSwapper.push_objects_to_scene(self, target_objects: List[str], target_file: Union[str, Path], backup: bool = True) -> bool` — Push objects from current scene to target scene.
  - `ObjectSwapper.pull_objects_from_scene(self, target_objects: List[str], source_file: Union[str, Path], backup: bool = True) -> bool` — Pull objects from source scene into current scene.

<a id="env_utils--hierarchy_manager--hierarchy_manager_slots"></a>
### `env_utils/hierarchy_manager/hierarchy_manager_slots.py`

- **[`class HierarchyManagerController(ptk.LoggingMixin)`](mayatk/mayatk/env_utils/hierarchy_manager/hierarchy_manager_slots.py#L29)** — Controller for hierarchy management operations.
  - `HierarchyManagerController.workspace(self) -> Optional[str]` *(property)* — Get the current workspace directory.
  - `HierarchyManagerController.reference_path(self) -> str` *(property)* — The current reference scene path.
  - `HierarchyManagerController.reference_path(self, text: str) -> None` — Set the reference path and update the reference tree accordingly.
  - `HierarchyManagerController.analyze_hierarchies(self, reference_path: str, fuzzy_matching: bool = True, dry_run: bool = True, filter_meshes: bool = False) -> bool` — Analyze hierarchies and perform comparison.
  - `HierarchyManagerController.pull_objects(self, object_names: List[str], reference_path: str, fuzzy_matching: bool = True, dry_run: bool = True, pull_children: bool = False, pull_mode: str = 'Add to Scene') -> bool` — Pull objects from reference scene to current scene.
  - `HierarchyManagerController.repair_hierarchies(self, create_stubs: bool = True, quarantine_extras: bool = True, quarantine_group: str = '_QUARANTINE', skip_animated: bool = True, fix_reparented: bool = True, fix_fuzzy_renames: bool = True, dry_run: bool = True) -> bool` — Run repair operations on the current scene to match reference hierarchy.
  - `HierarchyManagerController.select_objects_in_maya(self, object_names: List[str]) -> int` — Select objects in Maya scene by name.
  - `HierarchyManagerController.populate_reference_tree(self, tree_widget, reference_path: str = None)` — Populate the reference tree — handles cache, import, and rendering.
  - `HierarchyManagerController.refresh_trees(self, restore_selection: bool = True)` — Refresh both tree widgets with current hierarchy data.
  - `HierarchyManagerController.is_path_ignored(self, tree_widget, path)` — Check whether *path* (or any ancestor) is in the ignored set.
  - `HierarchyManagerController.clear_ignored_paths(self)` — Clear all ignored paths for both trees.
  - `HierarchyManagerController.log_diff_results(self)` — Log detailed hierarchy difference analysis results using rich formatting.
  - `HierarchyManagerController.get_recent_reference_scenes(self) -> List[str]` — Get recent reference scenes from settings.
  - `HierarchyManagerController.save_recent_reference_scene(self, scene_path: str)` — Save reference scene to recent list.
- **[`class HierarchyManagerSlots(ptk.LoggingMixin)`](mayatk/mayatk/env_utils/hierarchy_manager/hierarchy_manager_slots.py#L1109)** — Slots class for hierarchy management UI operations.
  - `HierarchyManagerSlots.header_init(self, widget)` — Initialize the header widget.
  - `HierarchyManagerSlots.tree000_init(self, widget)` — Initialize the reference/imported hierarchy tree widget.
  - `HierarchyManagerSlots.tree001_init(self, widget)` — Initialize the current scene hierarchy tree widget.
  - `HierarchyManagerSlots.cmb_diff_options_init(self, widget)` — Populate the diff-options WidgetComboBox below the Diff button.
  - `HierarchyManagerSlots.cmb_pull_options_init(self, widget)` — Populate the pull-options WidgetComboBox below the Pull button.
  - `HierarchyManagerSlots.tb003_init(self, widget)` — Initialize the fix/repair toggle button with options menu.
  - `HierarchyManagerSlots.tb001(self, state=None)` — Run the diff analysis using settings from cmb_diff_options.
  - `HierarchyManagerSlots.tb002(self, state=None)` — Toggle button for pull objects with options menu.
  - `HierarchyManagerSlots.tb003(self, state=None)` — Toggle button for fix/repair operations.
  - `HierarchyManagerSlots.b003(self)` — Browse for reference scene file.
  - `HierarchyManagerSlots.b005(self)` — Refresh current scene hierarchy tree.
  - `HierarchyManagerSlots.b006(self)` — Select checked objects in Maya scene.
  - `HierarchyManagerSlots.b007(self)` — Expand all items in current scene tree.
  - `HierarchyManagerSlots.b008(self)` — Collapse all items in current scene tree.
  - `HierarchyManagerSlots.b009(self)` — Refresh reference hierarchy tree.
  - `HierarchyManagerSlots.b011(self)` — Show differences between hierarchies.
  - `HierarchyManagerSlots.b012(self)` — Analyze hierarchies and perform comparison.
  - `HierarchyManagerSlots.b013(self)` — Ignore selected items in the reference tree.
  - `HierarchyManagerSlots.b014(self)` — Unignore selected items in the reference tree.
  - `HierarchyManagerSlots.b015(self)` — Ignore selected items in the current scene tree.
  - `HierarchyManagerSlots.b016(self)` — Unignore selected items in the current scene tree.
  - `HierarchyManagerSlots.b018(self)` — Delete selected objects from the Maya scene and refresh the tree.
  - `HierarchyManagerSlots.b017(self)` — Rename current-scene items to match reference names.
  - `HierarchyManagerSlots.count_tree_items(self, tree_widget)` — Count total items in a tree widget.

<a id="env_utils--hierarchy_manager--hierarchy_sidecar"></a>
### `env_utils/hierarchy_manager/hierarchy_sidecar.py`

Hierarchy sidecar manifest management.

- **[`class HierarchySidecar`](mayatk/mayatk/env_utils/hierarchy_manager/hierarchy_sidecar.py#L17)** — Manages hierarchy sidecar files stored alongside export files.
  - `HierarchySidecar.base_stem(cls, export_path: str) -> str` *(class)* — Return the export stem with any trailing ``_vNN`` suffix stripped.
  - `HierarchySidecar.manifest_path_for(cls, export_path: str, *, base_stem: bool = False) -> str` *(class)* — Return the sidecar manifest path for an export file.
  - `HierarchySidecar.diff_report_path_for(cls, export_path: str, *, base_stem: bool = False) -> str` *(class)* — Return the sidecar diff report path for an export file.
  - `HierarchySidecar.find_legacy_manifest(cls, export_path: str) -> Optional[str]` *(class)* — Return the path of a legacy per-version sidecar to migrate from.
  - `HierarchySidecar.ensure_base_name(cls, export_path: str) -> Optional[str]` *(class)* — Migrate a legacy per-version manifest to the base-stem name.
  - `HierarchySidecar.rename(cls, old_export_path: str, new_export_path: str) -> list` *(class)* — Rename sidecar files to match a renamed export file.
  - `HierarchySidecar.build_clean_path_set(objects) -> set` *(static)* — Build a set of namespace-stripped hierarchy paths from DAG long paths.
  - `HierarchySidecar.expand_to_descendants(objects) -> list` *(static)* — Return *objects* plus all their DAG descendants (full paths).
  - `HierarchySidecar.get_top_level(paths) -> list` *(static)* — Return only paths whose ancestor is *not* also in the set.
  - `HierarchySidecar.detect_reparenting(missing: list, extra: list) -> list` *(static)* — Detect nodes that were reparented rather than added/removed.
  - `HierarchySidecar.write_manifest(cls, export_path: str, paths, *, base_stem: bool = False) -> Optional[str]` *(class)* — Write *paths* to the sidecar manifest for *export_path*.
  - `HierarchySidecar.read_manifest(cls, export_path: str, *, base_stem: bool = False) -> Optional[Set[str]]` *(class)* — Read the manifest for *export_path*.
  - `HierarchySidecar.count_descendants(top_path: str, all_paths) -> int` *(static)* — Count *top_path* plus its descendants in *all_paths*.
  - `HierarchySidecar.write_diff_report(cls, export_path: str, missing: list, extra: list, reparented: list = None, *, base_stem: bool = False) -> Optional[str]` *(class)* — Write a human-readable diff report to the sidecar text file.
  - `HierarchySidecar.clean_stale_diff(cls, export_path: str, *, base_stem: bool = False) -> None` *(class)* — Remove a stale diff report left over from a previous failure.
  - `HierarchySidecar.build_full_path_set(cls, objects) -> set` *(class)* — Expand *objects* to descendants, then clean and deduplicate.
  - `HierarchySidecar.compare(cls, export_path: str, current_paths: set, *, base_stem: bool = False) -> Tuple[bool, list, list]` *(class)* — Compare *current_paths* against the stored manifest.

<a id="env_utils--hierarchy_manager--tree_renderer"></a>
### `env_utils/hierarchy_manager/tree_renderer.py`

Tree rendering, formatting, and selection management for the hierarchy manager UI.

- **[`class HierarchyTreeRenderer(ptk.LoggingMixin)`](mayatk/mayatk/env_utils/hierarchy_manager/tree_renderer.py#L30)** — Owns all QTreeWidget population, diff-colour formatting, ignore
  - `HierarchyTreeRenderer.populate_current_scene_tree(self, tree_widget)` — Populate the current scene hierarchy tree.
  - `HierarchyTreeRenderer.populate_reference_tree(self, tree_widget, transforms, reference_name='Reference Scene')` — Populate the reference hierarchy tree with pre-fetched transforms.
  - `HierarchyTreeRenderer.show_reference_placeholder(self, tree_widget, reference_name='Reference Scene')` — Show a 'Browse for Reference Scene' placeholder in an empty tree.
  - `HierarchyTreeRenderer.show_reference_error(self, tree_widget, reference_name='Reference Scene', message='File Not Found')` — Show an error or status message in the reference tree.
  - `HierarchyTreeRenderer.populate_tree_with_hierarchy(self, tree_widget, objects, tree_type='current')` — Populate tree widget with proper Maya-style hierarchy.
  - `HierarchyTreeRenderer.apply_difference_formatting(self, tree001, tree000)` — Apply color formatting to tree widgets based on hierarchy differences.
  - `HierarchyTreeRenderer.clear_tree_colors(self, tree_widget)` — Remove foreground/background colors from every item in a tree widget.
  - `HierarchyTreeRenderer.format_tree_differences(self, tree_widget, tree_type, tree_matcher, by_full, by_clean, by_last)` — Format a specific tree widget based on differences.
  - `HierarchyTreeRenderer.apply_ignore_styling(self, tree_widget)` — Apply or remove strikethrough + dim styling for ignored items.
  - `HierarchyTreeRenderer.build_item_path(item)` *(static)* — Build a pipe-separated hierarchy path from a QTreeWidgetItem.
  - `HierarchyTreeRenderer.find_tree_item_by_name(self, tree_widget, object_name)` — Find a tree item by object name (first column).
  - `HierarchyTreeRenderer.get_selected_tree_items(self, tree_widget)` — Get selected items from a tree widget.
  - `HierarchyTreeRenderer.get_selected_object_names(self, tree_widget)` — Extract object names from selected tree widget items.

<a id="env_utils--hierarchy_manager--tree_utils"></a>
### `env_utils/hierarchy_manager/tree_utils.py`

Tree widget utilities for hierarchy manager UI operations.

- [`get_selected_object_names(tree_widget) -> List[str]`](mayatk/mayatk/env_utils/hierarchy_manager/tree_utils.py#L162) — Extract object names from selected tree widget items.
- [`get_selected_tree_items(tree_widget) -> list`](mayatk/mayatk/env_utils/hierarchy_manager/tree_utils.py#L172) — Get all selected items from tree widget.
- [`find_tree_item_by_name(tree_widget, object_name: str)`](mayatk/mayatk/env_utils/hierarchy_manager/tree_utils.py#L199) — Find tree widget item by object name.
- [`build_hierarchy_structure(objects: list) -> Tuple[Dict[str, Dict], List[str]]`](mayatk/mayatk/env_utils/hierarchy_manager/tree_utils.py#L210) — Build hierarchical structure from Maya transform objects.
- **[`class TreePathMatcher(ptk.LoggingMixin)`](mayatk/mayatk/env_utils/hierarchy_manager/tree_utils.py#L17)** — Tree path matching functionality for UI tree widgets.
  - `TreePathMatcher.build_tree_index(self, widget)` — Build tree indices for fast item lookup.
  - `TreePathMatcher.find_path_matches(self, target_path: str, by_full: dict, by_clean_full: dict, by_last: dict, prefer_cleaned: bool = False, strict: bool = False)` — Find tree items matching a target path using multiple strategies.
  - `TreePathMatcher.log_matching_debug(self, path, candidates, strategy, prefix='')` — Log debug information about path matching.
  - `TreePathMatcher.log_tree_index_debug(self, by_full, by_clean_full, by_last, tree_type)` — Log debug information about tree indices.

<a id="env_utils--maya_connection"></a>
### `env_utils/maya_connection.py`

Maya Connection Module

- [`open_command_ports(**kwargs)`](mayatk/mayatk/env_utils/maya_connection.py#L1213) — Wrapper for MayaConnection.open_command_ports.
- [`toggle_command_ports(mel_port=7001, python_port=7002)`](mayatk/mayatk/env_utils/maya_connection.py#L1218) — Wrapper for MayaConnection.toggle_command_ports.
- [`open_available_command_ports(mel_start=7001, python_start=7002, max_offset=50, tag_window=True)`](mayatk/mayatk/env_utils/maya_connection.py#L1223) — Wrapper for MayaConnection.open_available_command_ports.
- **[`class MayaConnection`](mayatk/mayatk/env_utils/maya_connection.py#L27)** — Manages connection to Maya for testing purposes.
  - `MayaConnection.get_instance() -> 'MayaConnection'` *(static)* — Get the global Maya connection instance.
  - `MayaConnection.open_command_ports(**kwargs)` *(static)* — Open command ports for external script editor.
  - `MayaConnection.close_command_ports(ports=None)` *(static)* — Close the specified Maya command ports.
  - `MayaConnection.open_available_command_ports(mel_start: int = 7001, python_start: int = 7002, max_offset: int = 50, tag_window: bool = True) -> dict` *(static)* — Open command ports auto-negotiating around port collisions.
  - `MayaConnection.toggle_command_ports(mel_port: int = 7001, python_port: int = 7002) -> tuple` *(static)* — Toggle Maya command ports on or off.
  - `MayaConnection.reload_modules(modules: Union[str, List[str]], include_submodules: bool = True, verbose: bool = True) -> List[str]` *(static)* — Reload specified modules and their submodules using pythontk.ModuleReloader.
  - `MayaConnection.connect(self, mode: ConnectionMode = 'auto', port: int = 7002, host: str = 'localhost', launch: bool = True, app_path: Optional[str] = None, force_new_instance: bool = True, launch_args: Optional[List[str]] = None, confirm_existing: bool = True, auto_cleanup: bool = False) -> bool` — Connect to Maya using the specified mode.
  - `MayaConnection.get_pid_from_port(port: int) -> Optional[int]` *(static)* — Find the process ID (PID) listening on the given TCP port.
  - `MayaConnection.close_instance(port: Optional[int] = None, pid: Optional[int] = None, force: bool = False) -> bool` *(static)* — Close a Maya instance identified by Port or PID.
  - `MayaConnection.get_available_port(start_port: int = 7002, max_check: int = 100) -> int` *(static)* — Find an available port starting from start_port.
  - `MayaConnection.ensure_connection(self, launch: bool = True, app_path: Optional[str] = None, launch_args: Optional[List[str]] = None) -> bool` — Verify the port is reachable;
  - `MayaConnection.execute(self, code: str, timeout: int = 30, capture_output: bool = False, wait_for_response: bool = False, output_callback: Optional[Callable[[str], None]] = None) -> Optional[str]` — Execute Python code in Maya.
  - `MayaConnection.get_script_editor_output(self, last_n_chars: Optional[int] = None) -> Optional[str]` — Get the full content of the Maya Script Editor history.
  - `MayaConnection.execute_and_capture_editor_output(self, code: str, timeout: int = 30, mirror_to_script_output: bool = False) -> tuple[Optional[str], Optional[str]]` — Execute code and capture the Script Editor output generated by the execution.
  - `MayaConnection.clear_script_editor(self) -> bool` — Clear the Maya Script Editor history.
  - `MayaConnection.shutdown(self, force: bool = False) -> None` — Shut down the connected Maya session and reset state.
  - `MayaConnection.disconnect(self)` — Disconnect from Maya.

<a id="env_utils--namespace_sandbox"></a>
### `env_utils/namespace_sandbox.py`

- **[`class FBXImporter`](mayatk/mayatk/env_utils/namespace_sandbox.py#L90)** — Handles FBX-specific import operations (.fbx files).
  - `FBXImporter.is_supported_file(self, file_path: Union[str, Path]) -> bool` — Check if the file is an FBX file.
  - `FBXImporter.import_with_namespace(self, source_file: Path, namespace: str, temp_namespace_prefix: str, force_complete_import: bool = False) -> Optional[Dict]` — Import FBX file with namespace - handles the complete import process.
  - `FBXImporter.import_for_analysis(self, source_file: Path, namespace: str) -> Optional[List[Any]]` — Import FBX file for analysis purposes.
- **[`class MayaImporter`](mayatk/mayatk/env_utils/namespace_sandbox.py#L1062)** — Handles Maya-specific import operations (.ma/.mb files).
  - `MayaImporter.is_supported_file(self, file_path: Union[str, Path]) -> bool` — Check if the file is a Maya file (.ma or .mb).
  - `MayaImporter.import_with_namespace(self, source_file: Path, namespace: str, temp_namespace_prefix: str, force_complete_import: bool = False) -> Optional[Dict]` — Import Maya file with namespace - original logic.
  - `MayaImporter.import_for_analysis(self, source_file: Path, namespace: str) -> Optional[List[Any]]` — Import Maya file for analysis purposes.
- **[`class CameraTracker(ptk.LoggingMixin)`](mayatk/mayatk/env_utils/namespace_sandbox.py#L1241)** — Tracks cameras before and after import operations for proper cleanup.
  - `CameraTracker.capture_pre_import_state(self)` — Capture camera state before import.
  - `CameraTracker.capture_post_import_state(self)` — Capture camera state after import.
  - `CameraTracker.get_imported_cameras(self, namespace_filter=None)` — Get cameras that were imported (optionally filtered by namespace).
  - `CameraTracker.cleanup_imported_cameras(self, namespace_filter=None, preserve_user_cameras=True)` — Clean up imported cameras with optional preservation of user cameras.
  - `CameraTracker.reset(self)` — Reset tracking state.
- **[`class NamespaceSandbox(ptk.LoggingMixin)`](mayatk/mayatk/env_utils/namespace_sandbox.py#L1358)** — Handles temporary importing and namespace management for Maya scenes.
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

- **[`class AssemblyManager`](mayatk/mayatk/env_utils/reference_manager.py#L71)**
  - `AssemblyManager.current_references(cls)` *(class)* — Get the current scene references.
  - `AssemblyManager.create_assembly_definition(cls, namespace: str, file_path: str) -> str` *(class)* — Create an assembly definition for the given file path.
  - `AssemblyManager.set_active_representation(cls, assembly_node: str, representation_name: str) -> bool` *(class)* — Set the active representation for an assembly.
  - `AssemblyManager.convert_references_to_assemblies(cls)` *(class)* — Convert all current references to assembly definitions and references.
- **[`class ReferenceManager(WorkspaceManager, ptk.HelpMixin, ptk.LoggingMixin)`](mayatk/mayatk/env_utils/reference_manager.py#L158)** — Core Maya scene reference management functionality.
  - `ReferenceManager.current_references(self)` *(property)* — Get the current scene references.
  - `ReferenceManager.sanitize_namespace(namespace: str) -> str` *(static)* — Sanitize the namespace by replacing or removing illegal characters.
  - `ReferenceManager.add_reference(self, namespace: str, file_path: str) -> bool`
  - `ReferenceManager.import_references(self, namespaces=None, remove_namespace=True)` — Import referenced objects into the scene.
  - `ReferenceManager.update_references(self)` — Update all references to reflect the latest changes from the original files.
  - `ReferenceManager.get_reference_top_transforms(self, ref)` — Return top-level (parent-less) transforms belonging to the given reference.
  - `ReferenceManager.get_reference_display_mode(self, ref) -> str` — Return the active display mode for the reference's top-level transforms.
  - `ReferenceManager.set_reference_display_mode(self, ref, mode: str) -> bool` — Set the display override mode on the reference's top-level transforms.
  - `ReferenceManager.remove_references(self, namespaces=None)` — Remove references based on their namespaces.
- **[`class ReferenceManagerController(ReferenceManager, ptk.LoggingMixin)`](mayatk/mayatk/env_utils/reference_manager.py#L548)** — Controller that bridges Maya reference functionality with UI interactions.
  - `ReferenceManagerController.current_working_dir(self)` *(property)*
  - `ReferenceManagerController.current_working_dir(self, value)`
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
  - `ReferenceManagerController.unreference_all(self)`
  - `ReferenceManagerController.unlink_all(self)`
  - `ReferenceManagerController.unlink_references(self, namespaces)` — Unlink specific references.
  - `ReferenceManagerController.convert_to_assembly(self)`
  - `ReferenceManagerController.save_scene(self)` — Save the current scene to the workspace, prompting for a name.
  - `ReferenceManagerController.rename_scene(self)` — Rename the scene file at the right-clicked row.
  - `ReferenceManagerController.delete_scene(self)` — Delete the scene file at the right-clicked row.
- **[`class ReferenceManagerSlots(ptk.HelpMixin, ptk.LoggingMixin)`](mayatk/mayatk/env_utils/reference_manager.py#L1862)** — UI event handlers and widget initialization for the Reference Manager interface.
  - `ReferenceManagerSlots.header_init(self, widget)` — Initialize the header for the reference manager.
  - `ReferenceManagerSlots.tbl000_init(self, widget)`
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
  - `ReferenceManagerSlots.chk_hide_binary(self, checked)` — Handle the hide binary checkbox.
  - `ReferenceManagerSlots.chk_filter_suffix(self, checked)` — Handle the filter by suffix checkbox.
  - `ReferenceManagerSlots.chk_hide_suffix(self, checked)` — Handle the hide suffix checkbox.
  - `ReferenceManagerSlots.chk_hide_extension(self, checked)` — Handle the hide extension checkbox.
  - `ReferenceManagerSlots.txt_suffix(self, text)` — Handle suffix text changes.
  - `ReferenceManagerSlots.chk_filter_folder_structure(self, checked)` — Handle the filter by folder structure checkbox.
  - `ReferenceManagerSlots.b000(self)` — Browse for a root directory.
  - `ReferenceManagerSlots.b006(self)` — Open the current directory in the file explorer.
  - `ReferenceManagerSlots.b001(self)` — Set dir to current workspace.
  - `ReferenceManagerSlots.btn_open_scene(self)` — Open the scene file at the right-clicked row.
  - `ReferenceManagerSlots.btn_toggle_reference(self)` — Toggle reference state for the right-clicked row.
  - `ReferenceManagerSlots.btn_unlink_import(self)` — Unlink and import the reference at the right-clicked row.
  - `ReferenceManagerSlots.btn_save_scene(self)` — Save the current scene to the workspace.
  - `ReferenceManagerSlots.btn_refresh(self)` — Refresh the file list.
  - `ReferenceManagerSlots.btn_convert_assembly(self)` — Convert all references to assemblies.
  - `ReferenceManagerSlots.btn_unlink_import_all(self)` — Unlink and import all references.
  - `ReferenceManagerSlots.btn_unreference_all(self)` — Remove all references from the scene.

<a id="env_utils--scene_exporter--_scene_exporter"></a>
### `env_utils/scene_exporter/_scene_exporter.py`

- **[`class SceneExporter(ptk.LoggingMixin)`](mayatk/mayatk/env_utils/scene_exporter/_scene_exporter.py#L29)**
  - `SceneExporter.perform_export(self, export_dir: str, objects: Optional[Union[List[str], Callable]] = None, preset_file: Optional[str] = None, output_name: Optional[str] = None, export_visible: bool = True, file_format: Optional[str] = 'FBX export', create_log_file: bool = False, timestamp: bool = False, name_regex: Optional[str] = None, log_level: str = 'WARNING', hide_log_file: Optional[bool] = None, log_handler: Optional[object] = None, tasks: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, bool]]` — Perform the export operation, including initialization and task management.
  - `SceneExporter.generate_export_path(self, version_format: str = '') -> str` — Generate the full export file path.
  - `SceneExporter.format_export_name(self, name: str) -> str` — Format the export name using a regex pattern and replacement (e.g.
  - `SceneExporter.generate_log_file_path(self, export_path: str) -> str` — Generate the log file path based on the export path.
  - `SceneExporter.setup_file_logging(self, log_file_path: str)` — Setup file logging to log actions during export.
  - `SceneExporter.close_file_handlers(self)` — Close and remove file handlers after logging is complete.
  - `SceneExporter.load_fbx_export_preset(self, preset_file: str = None, verify: bool = False) -> Optional[dict]` — Load an FBX export preset and optionally verify it.
  - `SceneExporter.verify_fbx_preset(self) -> dict` — Verify a set of predefined FBX export settings and log their values.
- **[`class SceneExporterSlots(SceneExporter)`](mayatk/mayatk/env_utils/scene_exporter/_scene_exporter.py#L566)**
  - `SceneExporterSlots.workspace(self) -> Optional[str]` *(property)*
  - `SceneExporterSlots.presets(self) -> Dict[str, Optional[str]]` *(property)* — Return available presets, using cached values if the preset directory has not changed.
  - `SceneExporterSlots.header_init(self, widget)` — Initialize the header widget.
  - `SceneExporterSlots.cmb000_init(self, widget) -> None` — Init Preset
  - `SceneExporterSlots.txt000_init(self, widget) -> None` — Init Output Directory
  - `SceneExporterSlots.txt001_init(self, widget) -> None` — Init Output Name
  - `SceneExporterSlots.cmb001_init(self, widget) -> None` — Auto-generate Export Settings UI from task definitions using WidgetComboBox.
  - `SceneExporterSlots.cmb002_init(self, widget) -> None` — Auto-generate Check Settings UI from check definitions using WidgetComboBox.
  - `SceneExporterSlots.b000(self) -> None` — Export.
  - `SceneExporterSlots.b010(self) -> None` — Set Output Directory
  - `SceneExporterSlots.b003(self) -> None` — Add Preset.
  - `SceneExporterSlots.b004(self) -> None` — Remove Preset.
  - `SceneExporterSlots.b005(self) -> None` — Set Preset Directory.
  - `SceneExporterSlots.b006(self) -> None` — Open Output Directory
  - `SceneExporterSlots.b007(self) -> None` — Open Preset Directory.
  - `SceneExporterSlots.b008(self) -> None` — Edit Preset
  - `SceneExporterSlots.save_output_dir(self, output_dir: str) -> None` — Record the output directory into the recent values plugin.

<a id="env_utils--scene_exporter--task_factory"></a>
### `env_utils/scene_exporter/task_factory.py`

- **[`class TaskFactory`](mayatk/mayatk/env_utils/scene_exporter/task_factory.py#L9)** — A factory class for managing and executing tasks in a scene export pipeline.
  - `TaskFactory.run_tasks(self, tasks: Dict[str, Any]) -> bool` — Run tasks and checks, returning True if all checks pass, False if any fail.
  - `TaskFactory.run_tasks_by_category(self, task_definitions: Dict[str, Any], check_definitions: Dict[str, Any]) -> bool` — Alternative method to run tasks and checks separately with better organization.

<a id="env_utils--scene_exporter--task_manager"></a>
### `env_utils/scene_exporter/task_manager.py`

- **[`class TaskManager(TaskFactory, _TaskActionsMixin, _TaskChecksMixin)`](mayatk/mayatk/env_utils/scene_exporter/task_manager.py#L1059)** — Contains all task-related UI definitions for the Scene Exporter.
  - `TaskManager.objects(self)` *(property)*
  - `TaskManager.objects(self, value)` — Invalidate the materials cache whenever objects change.
  - `TaskManager.task_definitions(self) -> Dict[str, Dict[str, Any]]` *(property)* — Return the task definitions for the UI.
  - `TaskManager.check_definitions(self) -> Dict[str, Dict[str, Any]]` *(property)* — Return the check definitions for the UI.
  - `TaskManager.definitions(self) -> Dict[str, Dict[str, Any]]` *(property)* — Return all definitions combined for backward compatibility.

<a id="env_utils--script_output"></a>
### `env_utils/script_output.py`

- [`show(*args, **kwargs)`](mayatk/mayatk/env_utils/script_output.py#L408)
- [`toggle(*args, **kwargs)`](mayatk/mayatk/env_utils/script_output.py#L412) — Toggle the Script Output panel.
- **[`class ScriptHighlightRule`](mayatk/mayatk/env_utils/script_output.py#L13)**
- **[`class ScriptHighlighter(QtGui.QSyntaxHighlighter)`](mayatk/mayatk/env_utils/script_output.py#L33)**
  - `ScriptHighlighter.highlightBlock(self, text: str) -> None`
- **[`class ScriptOutput(QtWidgets.QTextEdit)`](mayatk/mayatk/env_utils/script_output.py#L48)**
  - `ScriptOutput.keyPressEvent(self, event: QtGui.QKeyEvent)` — Ensure copy shortcut works reliably in the output widget.
  - `ScriptOutput.event(self, event: QtCore.QEvent)` — Intercept shortcut override so Maya doesn't steal Ctrl+C.
  - `ScriptOutput.eventFilter(self, obj, event: QtCore.QEvent)`
- **[`class ScriptConsole(MayaQWidgetDockableMixin, QtWidgets.QDialog)`](mayatk/mayatk/env_utils/script_output.py#L185)** — Dockable window that live-mirrors Maya's Script Editor output,
  - `ScriptConsole.enterEvent(self, event)`
  - `ScriptConsole.show_console(cls, dock=None, width: int = None, height: int = None, tab_position: str = None, restore: bool = False)` *(class)* — Show the Script Output console.

<a id="env_utils--workspace_manager"></a>
### `env_utils/workspace_manager.py`

- **[`class WorkspaceManager(ptk.HelpMixin)`](mayatk/mayatk/env_utils/workspace_manager.py#L13)** — Shared workspace management utilities for UI components.
  - `WorkspaceManager.current_workspace(self)` *(property)* — Get the current Maya workspace with fallback handling.
  - `WorkspaceManager.current_working_dir(self)` *(property)* — Get the current working directory.
  - `WorkspaceManager.current_working_dir(self, value)` — Set the current working directory and invalidate cache.
  - `WorkspaceManager.recursive_search(self)` *(property)* — Whether to search recursively for files.
  - `WorkspaceManager.recursive_search(self, value)` — Set recursive search and invalidate cache.
  - `WorkspaceManager.ignore_empty_workspaces(self)` *(property)* — Whether to ignore empty workspaces when searching.
  - `WorkspaceManager.ignore_empty_workspaces(self, value)` — Set ignore empty workspaces and invalidate cache.
  - `WorkspaceManager.workspace_files(self) -> dict[str, list[str]]` *(property)* — Get cached workspace file dictionary, rebuilding if needed.
  - `WorkspaceManager.find_available_workspaces(self, root_dir: str = None) -> list` — Find all available workspaces under the given root directory.
  - `WorkspaceManager.invalidate_workspace_files(self)` — Scan for workspaces and rebuild the file cache.
  - `WorkspaceManager.resolve_file_path(self, selected_file: str) -> Optional[str]` — Resolve a file name to its full path by searching in workspace files.

<a id="env_utils--workspace_map"></a>
### `env_utils/workspace_map.py`

- **[`class WorkspaceMap(WorkspaceManager, ptk.HelpMixin, ptk.LoggingMixin)`](mayatk/mayatk/env_utils/workspace_map.py#L16)** — Maps and displays Maya workspaces in a tree structure.
  - `WorkspaceMap.current_working_dir(self)` *(property)* — Get the current working directory for workspace discovery.
  - `WorkspaceMap.current_working_dir(self, value)` — Set the current working directory and invalidate cache.
  - `WorkspaceMap.recursive_search(self)` *(property)* — Whether to search recursively for workspaces.
  - `WorkspaceMap.recursive_search(self, value)` — Set recursive search and invalidate cache.
  - `WorkspaceMap.workspace_data(self) -> Dict[str, Dict]` *(property)* — Get cached workspace data, rebuilding if needed.
  - `WorkspaceMap.invalidate_workspace_data(self)` — Scan for workspaces and build data cache.
  - `WorkspaceMap.get_workspace_tree_data(self, filter_text: str = None) -> Dict` — Get workspace data organized for tree display.
  - `WorkspaceMap.get_filtered_workspaces(self, filter_text: str = None) -> List[Dict]` — Get a filtered list of workspaces.
  - `WorkspaceMap.refresh_workspace_data(self, invalidate: bool = False)` — Refresh the workspace data cache.
- **[`class WorkspaceMapController(WorkspaceMap, ptk.LoggingMixin)`](mayatk/mayatk/env_utils/workspace_map.py#L227)** — Controller for the WorkspaceMap UI components.
  - `WorkspaceMapController.update_current_dir(self, text: Optional[str] = None)` — Update the current working directory from UI input.
  - `WorkspaceMapController.refresh_tree(self, invalidate: bool = False)` — Refresh the workspace tree.
  - `WorkspaceMapController.handle_tree_selection(self)` — Handle tree item selection.
- **[`class WorkspaceMapSlots(ptk.HelpMixin, ptk.LoggingMixin)`](mayatk/mayatk/env_utils/workspace_map.py#L347)** — UI slots for the WorkspaceMap interface.
  - `WorkspaceMapSlots.header_init(self, widget)` — Configure header help text.
  - `WorkspaceMapSlots.txt000_init(self, widget)` — Initialize the directory input widget.
  - `WorkspaceMapSlots.txt001_init(self, widget)` — Initialize the filter input widget.
  - `WorkspaceMapSlots.tree000_init(self, widget)` — Initialize the workspace tree widget.
  - `WorkspaceMapSlots.filter_workspaces(self, text)` — Handle filter text changes.
  - `WorkspaceMapSlots.chk000(self, checked)` — Handle recursive search toggle.
  - `WorkspaceMapSlots.browse_directory(self)` — Browse for a root directory.
  - `WorkspaceMapSlots.set_to_workspace(self)` — Set directory to current Maya workspace.
  - `WorkspaceMapSlots.btn_open_workspace(self)` — Open selected workspace in Maya.
  - `WorkspaceMapSlots.btn_explore_folder(self)` — Open selected workspace folder in file explorer.

<a id="light_utils--_light_utils"></a>
### `light_utils/_light_utils.py`

- **[`class LightUtils(ptk.HelpMixin)`](mayatk/mayatk/light_utils/_light_utils.py#L12)**

<a id="light_utils--bake_lighting"></a>
### `light_utils/bake_lighting.py`

Bake Maya scene lighting into per-object texture files.

- **[`class BakeLighting(ptk.LoggingMixin)`](mayatk/mayatk/light_utils/bake_lighting.py#L51)** — Bakes scene lighting per object to PNG textures.
  - `BakeLighting.arnold_available() -> bool` *(static)* — True if the ``mtoa`` plugin is loaded AND its bake cmd is registered.
  - `BakeLighting.bake(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, prefix: str = 'bake_', backend: str = 'auto') -> Dict[str, str]` — Bake lighting per object to PNG files.
  - `BakeLighting.assign_to_diffuse(self, mapping: Dict[str, str]) -> None` — Wire each baked PNG into the object's material color slot.
  - `BakeLighting.restore_diffuse_connections(self) -> None` — Undo :meth:`assign_to_diffuse` -- reconnects previous drivers.

<a id="light_utils--hdr_manager"></a>
### `light_utils/hdr_manager.py`

Arnold HDR environment manager.

- **[`class HdrManager(ptk.LoggingMixin, ptk.HelpMixin)`](mayatk/mayatk/light_utils/hdr_manager.py#L44)** — Manage a single ``aiSkyDomeLight`` + connected ``file`` texture.
  - `HdrManager.arnold_available() -> bool` *(static)* — True if the ``mtoa`` plugin can be loaded right now.
  - `HdrManager.ensure_plugin_loaded(cls) -> bool` *(class)* — Backward-compat alias for :meth:`arnold_available`.
  - `HdrManager.hdr_env(self) -> Optional[str]` *(property)* — The skydome shape node, or ``None`` if not present.
  - `HdrManager.hdr_env(self, tex: Optional[str]) -> None` — Set (and lazily create) the skydome's HDR file texture.
  - `HdrManager.hdr_env_transform(self) -> Optional[str]` *(property)* — Transform parent of the skydome shape, or ``None``.
  - `HdrManager.hdr_file_node(self) -> Optional[str]` *(property)* — The ``file`` node currently driving ``color`` on the skydome.
  - `HdrManager.hdr_file_path(self) -> Optional[str]` *(property)* — Current HDR file path on disk, or ``None``.
  - `HdrManager.visibility(self) -> bool` *(property)* — Primary-ray visibility of the HDR (skydome as backdrop).
  - `HdrManager.visibility(self, state: bool) -> None`
  - `HdrManager.set_hdr_map_visibility(self, state: bool) -> None` — Backward-compat shim for :attr:`visibility`.
  - `HdrManager.rotation(self) -> float` *(property)* — Y rotation (degrees) of the skydome transform;
  - `HdrManager.rotation(self, degrees: float) -> None`
  - `HdrManager.intensity(self) -> float` *(property)* — Linear light-output multiplier on the skydome;
  - `HdrManager.intensity(self, value: float) -> None`
  - `HdrManager.exposure(self) -> float` *(property)* — Photographic stops (log2) on the skydome's ``aiExposure``.
  - `HdrManager.exposure(self, stops: float) -> None`
  - `HdrManager.create_network(self, hdrMap: str = '', hdrMapVisibility: bool = False, intensity: Optional[float] = None, exposure: Optional[float] = None, rotation: Optional[float] = None) -> Optional[str]` — Apply settings to the (lazily-created) skydome network.
  - `HdrManager.clear(self) -> None` — Remove the skydome and its connected file/place2d nodes.
- **[`class HdrManagerSlots(ptk.LoggingMixin, ptk.HelpMixin)`](mayatk/mayatk/light_utils/hdr_manager.py#L316)** — Switchboard slots for the HDR Manager UI.
  - `HdrManagerSlots.header_init(self, widget) -> None` — Configure header menu and refresh button.
  - `HdrManagerSlots.b001_init(self, widget) -> None` — Attach the Add-HDR mode selector to the button's option box.
  - `HdrManagerSlots.cmb000_init(self, widget) -> None` — Wire right-click context menu + auto-refresh on dropdown.
  - `HdrManagerSlots.hdr_map(self) -> Optional[str]` *(property)* — Selected HDR file path from the combobox.
  - `HdrManagerSlots.hdr_map_visibility(self) -> bool` *(property)*
  - `HdrManagerSlots.cmb000(self, index, widget) -> None` — HDR map selection — apply immediately.
  - `HdrManagerSlots.chk000(self, state, widget) -> None` — Toggle skydome primary-ray visibility.
  - `HdrManagerSlots.slider000(self, value, widget) -> None` — Rotate the HDR around Y.
  - `HdrManagerSlots.spn_intensity(self, value) -> None`
  - `HdrManagerSlots.spn_exposure(self, value) -> None`
  - `HdrManagerSlots.b000(self) -> None` — Create / refresh the skydome network from current UI state.
  - `HdrManagerSlots.b001(self) -> None` — Add an HDR using the mode selected in the option box.
  - `HdrManagerSlots.open_sourceimages(self) -> None` — Open the workspace's sourceimages folder in Explorer.
  - `HdrManagerSlots.clear_network(self) -> None` — Delete the skydome network and reset the UI to defaults.
  - `HdrManagerSlots.ctx_select_skydome(self) -> None`
  - `HdrManagerSlots.ctx_select_transform(self) -> None`
  - `HdrManagerSlots.ctx_select_file_node(self) -> None`
  - `HdrManagerSlots.ctx_reveal_in_explorer(self) -> None`

<a id="mat_utils--_affix_mode"></a>
### `mat_utils/_affix_mode.py`

Shared affix-mode option-box helper for mat_utils slot files.

- [`add_affix_mode_menu(widget, default_mode: str = 'auto', on_change=None)`](mayatk/mayatk/mat_utils/_affix_mode.py#L26) — Wire a 3-option affix-mode combobox onto ``widget.option_box.menu``.
- [`current_affix_mode(widget) -> str`](mayatk/mayatk/mat_utils/_affix_mode.py#L56) — Return the currently selected affix mode ('auto'/'suffix'/'prefix').
- [`resolve_affix(widget, default: str = 'prefix') -> Tuple[str, str]`](mayatk/mayatk/mat_utils/_affix_mode.py#L65) — Read widget text + mode and return ``(prefix, suffix)`` per the picker.

<a id="mat_utils--_mat_utils"></a>
### `mat_utils/_mat_utils.py`

- **[`class MatUtilsInternals(ptk.HelpMixin)`](mayatk/mayatk/mat_utils/_mat_utils.py#L44)** — Internal helper utilities shared across MatUtils operations.
  - `MatUtilsInternals.get_texture_file_node(material, attr_name, _depth=0)` *(static)* — Locate the file texture node feeding a material attribute.
- **[`class MatUtils(MatUtilsInternals)`](mayatk/mayatk/mat_utils/_mat_utils.py#L290)**
  - `MatUtils.resolve_path(path: str) -> Union[str, None]` *(static)* — Resolves a texture path by expanding env vars, checking workspace, and handling UDIMs.
  - `MatUtils.get_mats(objs=None, as_strings=True, mat_type=None) -> List[str]` *(static)* — Returns the set of materials assigned to a given list of objects or components.
  - `MatUtils.group_objects_by_material(objects, cluster_by_distance=False, threshold=10000.0)` *(static)* — Groups objects based on their assigned material(s).
  - `MatUtils.get_texture_paths(cls, objects: Optional[List[Any]] = None, materials: Optional[List[Any]] = None, file_nodes: Optional[List[Any]] = None, texture_names: Optional[List[str]] = None, absolute: bool = True) -> List[str]` *(class)* — Resolve unique texture file paths for the given scope.
  - `MatUtils.get_texture_info(cls, objects=None, materials=None, file_nodes=None, texture_names=None)` *(class)* — Get image metadata (size, mode, format) for texture files in scope.
  - `MatUtils.get_mat_info(cls, materials: Optional[List[Any]] = None, objects: Optional[List[Any]] = None, optimize_check: bool = False, progress_callback: Optional[Callable[[int, int, str], None]] = None, exclude_defaults: bool = False, exclude_unassigned: bool = False, include_textures: bool = True, include_image_metadata: bool = True, **optimize_kwargs) -> List[Dict[str, Any]]` *(class)* — Aggregate per-material info: name, type, textures + image metadata.
  - `MatUtils.format_texture_info_text(cls, info_list: List[Dict[str, Any]]) -> str` *(class)* — Render :meth:`get_texture_info` output as a plain-text report.
  - `MatUtils.format_texture_info_html(cls, info_list: List[Dict[str, Any]]) -> str` *(class)* — Render :meth:`get_texture_info` output as styled HTML.
  - `MatUtils.format_mat_info_text(cls, records: List[Dict[str, Any]]) -> str` *(class)* — Render :meth:`get_mat_info` output as a plain-text report.
  - `MatUtils.format_mat_info_html(cls, records: List[Dict[str, Any]]) -> str` *(class)* — Render :meth:`get_mat_info` output as styled HTML.
  - `MatUtils.get_scene_mats(inc=None, exc=None, node_type=None, sort: bool = False, as_dict: bool = False, exclude_defaults: bool = True, **filter_kwargs)` *(static)* — Retrieves all materials from the current scene, with flexible name/type filtering.
  - `MatUtils.get_connected_shaders(file_nodes) -> List[str]` *(static)* — Return surface shaders connected to one or more file nodes, ignoring intermediates.
  - `MatUtils.get_file_nodes(cls, materials: Optional[List[str]] = None, raw: bool = False, return_type: str = 'fileNode') -> list` *(class)* — Returns file node info in any column order based on return_type.
  - `MatUtils.get_fav_mats()` *(static)* — Retrieves the list of favorite materials in Maya.
  - `MatUtils.is_mat_assigned(mat: object) -> bool` *(static)* — True iff *mat*'s shading engines contain at least one DAG member.
  - `MatUtils.is_connected(mat: object, delete: bool = False) -> bool` *(static)* — Checks if a given material is assigned and optionally deletes it.
  - `MatUtils.create_mat(mat_type, prefix='', name='')` *(static)* — Creates a material based on the provided type or a random material if 'mat_type' is 'random'.
  - `MatUtils.assign_mat(objects, mat_name)` *(static)* — Assigns a material to a list of objects or components.
  - `MatUtils.create_file_node(image_path, name=None, color_space=None)` *(static)* — Create a ``file`` texture node with a wired ``place2dTexture``.
  - `MatUtils.create_shading_group(shader, name=None, assign_to=None)` *(static)* — Create a shading group for *shader* and optionally assign objects.
  - `MatUtils.create_stingray_shader(name, opacity=False, opacity_mode=None)` *(static)* — Create a StingrayPBS shader by loading a ShaderFX preset graph.
  - `MatUtils.find_by_mat_id(cls, material: str, objects: Optional[List[str]] = None, shell: bool = False) -> List[str]` *(class)* — Find objects or faces by the material ID.
  - `MatUtils.collect_material_paths(materials: Optional[List[str]] = None, attributes: Optional[List[str]] = None, inc_mat_name: bool = False, inc_path_type: bool = False, resolve_full_path: bool = False) -> Union[List[str], List[Tuple[str, ...]]]` *(static)* — Collects specified attributes file paths for given materials.
  - `MatUtils.remap_file_nodes(file_paths: List[str], target_dir: str, silent: bool = False, limit_to_nodes: Optional[List[str]] = None, as_strings: bool = True) -> List[str]` *(static)* — Internal helper to remap file nodes to target_dir, preserving relative subfolders inside sourceimag…
  - `MatUtils.remap_texture_paths(cls, materials: Optional[List[str]] = None, new_dir: Optional[str] = None, silent: bool = False, file_nodes: Optional[List[str]] = None, objects: Optional[List[str]] = None, as_strings: bool = True) -> None` *(class)* — Remaps file texture paths for materials to new_dir.
  - `MatUtils.is_duplicate_material(material1: str, material2: str) -> bool` *(static)* — Check if two materials are duplicates based on their textures.
  - `MatUtils.find_materials_with_duplicate_textures(cls, materials: Optional[List[str]] = None, strict: bool = False) -> Dict[str, List[str]]` *(class)* — Find duplicate materials based on their texture file names or full paths.
  - `MatUtils.reassign_duplicate_materials(cls, materials: Optional[List[str]] = None, delete: bool = False, strict: bool = False) -> None` *(class)* — Find duplicate materials, remove duplicates, and reassign them to the original material.
  - `MatUtils.filter_materials_by_objects(objects: List[str], as_strings: bool = True) -> List[str]` *(static)* — Filter materials assigned to the given objects.
  - `MatUtils.reload_textures(materials=None, inc=None, exc=None, log=False, refresh_viewport=False, refresh_hypershade=False, texture_types: Optional[List[str]] = None)` *(static)* — Reloads textures connected to specified materials with inclusion/exclusion filters.
  - `MatUtils.move_texture_files(cls, found_files: List[Union[str, Tuple[str, str]]], new_dir: str, delete_old: bool = False, create_dir: bool = True, per_file_timeout: float = 120.0, max_workers: int = 8, progress_callback: Optional[Callable[[int, int, str], bool]] = None) -> List[Tuple[str, str]]` *(class)* — Move or copy found texture files to a new directory.
  - `MatUtils.find_texture_files(cls, objects: Optional[List[str]] = None, source_dir: str = '', recursive: bool = True, return_dir: bool = False, quiet: bool = False, file_nodes: Optional[List[str]] = None, materials: Optional[List[str]] = None, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> List[Union[str, Tuple[str, str]]]` *(class)* — Find texture files for given objects' materials inside source_dir.
  - `MatUtils.migrate_textures(cls, materials: Optional[List[str]] = None, old_dir: Optional[str] = None, new_dir: Optional[str] = None, silent: bool = False, delete_old: bool = False, objects: Optional[List[str]] = None, file_nodes: Optional[List[str]] = None, progress_callback: Optional[Callable[[int, int, str], bool]] = None) -> None` *(class)* — Copies texture files from an old directory to a new one.
  - `MatUtils.move_unused_textures(source_dir: str = None, output_dir: str = None) -> None` *(static)* — Move unused textures to a specified directory.
  - `MatUtils.get_mat_swatch_icon(mat: Union[str, object], size: List[int] = [20, 20], fallback_to_blank: bool = True) -> object` *(static)* — Get an icon with a color fill matching the given material's RGB value.
  - `MatUtils.convert_bump_to_normal(bump_file_node, output_path: Optional[str] = None, intensity: float = 1.0, format_type: str = 'opengl', filter_type: str = '3x3', wrap_mode: str = 'black', create_file_node: bool = True, node_name: Optional[str] = None) -> Optional[str]` *(static)* — Convert a bump/height map to a normal map using Maya's bump2d node.
  - `MatUtils.validate_normal_map_setup(normal_file_node, material=None) -> Dict[str, Any]` *(static)* — Validate normal map file node setup and provide recommendations.
  - `MatUtils.graph_materials(materials: Union[str, List[str], object], mode: str = 'showUpAndDownstream') -> None` *(static)* — Open the Hypershade and graph the specified materials.

<a id="mat_utils--game_shader"></a>
### `mat_utils/game_shader.py`

- **[`class GameShader(ptk.LoggingMixin)`](mayatk/mayatk/mat_utils/game_shader.py#L34)** — A class to manage the creation of a shader network using StingrayPBS or Standard Surface shaders.
  - `GameShader.create_network(self, textures: List[str], name: str = '', prefix: str = '', suffix: str = '', config: Union[str, Dict[str, Any]] = None, progress_callback: Callable = None, **kwargs) -> Union[Optional[object], List[Optional[object]]]` — Create a PBR shader network with textures.
  - `GameShader.setup_stringray_node(self, name: str, opacity: bool) -> object` — Initializes and sets up a StingrayPBS shader node in Maya.
  - `GameShader.setup_arnold_nodes(self, name: str, shader_node: object) -> Tuple[object, object, object]` — Sets up a basic Arnold shader network for use with a Stingray PBS or Standard Surface shader.
  - `GameShader.setup_standard_surface_node(self, name: str, opacity: bool) -> object` — Creates and sets up a Maya Standard Surface shader node.
  - `GameShader.setup_open_pbr_node(self, name: str, opacity: bool) -> object` — Creates and sets up a Maya OpenPBR Surface shader node.
  - `GameShader.connect_stingray_nodes(self, texture: str, texture_type: str, sr_node: object) -> bool` — Connects texture files to the corresponding slots in the StingrayPBS shader node
  - `GameShader.connect_arnold_nodes(self, texture: str, texture_type: str, ai_node: object, aiMult_node: object, bump_node: object) -> bool` — Connects texture files to the corresponding slots in the Arnold shader nodes based on the texture t…
  - `GameShader.connect_standard_surface_nodes(self, texture: str, texture_type: str, std_node: object) -> bool` — Connects texture files to Maya Standard Surface shader slots.
  - `GameShader.connect_open_pbr_nodes(self, texture: str, texture_type: str, op_node: object) -> bool` — Connects texture files to Maya OpenPBR Surface shader slots.
  - `GameShader.filter_for_correct_normal_map(self, textures: List[str], desired_normal_type: str) -> List[str]` — Filters and ensures only the desired type of normal map is in the textures list.
  - `GameShader.filter_for_correct_metallic_map(self, textures: List[str], use_metallic_smoothness: bool, output_extension: str = 'png') -> List[str]` — Filters textures to ensure the correct handling of metallic maps based on the use_metallic_smoothne…
  - `GameShader.filter_for_mask_map(self, textures: List[str], output_extension: str = 'png') -> List[str]` — Creates Unity HDRP Mask Map (MSAO) by packing Metallic, AO, Detail, and Smoothness.
  - `GameShader.filter_for_correct_base_color_map(self, textures: List[str], use_albedo_transparency: bool) -> List[str]` — Filters textures to ensure the correct handling of albedo maps based on the use_albedo_transparency…
- **[`class GameShaderSlots(GameShader)`](mayatk/mayatk/mat_utils/game_shader.py#L1729)**
  - `GameShaderSlots.header_init(self, widget)` — Initialize the header widget.
  - `GameShaderSlots.lbl_graph_material(self)` — Graph the material in the Hypershade.
  - `GameShaderSlots.mat_name(self) -> str` *(property)* — Get the mat name from the user input text field.
  - `GameShaderSlots.mat_prefix(self) -> str` *(property)* — Return the affix text when it resolves as a prefix, else empty string.
  - `GameShaderSlots.mat_suffix(self) -> str` *(property)* — Return the affix text when it resolves as a suffix, else empty string.
  - `GameShaderSlots.normal_map_type(self) -> str` *(property)* — Get the normal map type from the comboBoxes current text.
  - `GameShaderSlots.output_extension(self) -> str` *(property)* — Get the output map extension from the comboBox current text.
  - `GameShaderSlots.shader_type(self) -> str` *(property)* — Get the shader type selection.
  - `GameShaderSlots.cmb002_init(self, widget)` — Initialize Presets
  - `GameShaderSlots.cmb003_init(self, widget)` — Initialize Output Extension
  - `GameShaderSlots.txt002_init(self, widget)` — Add a prefix/suffix/auto-mode combobox to the affix field's option menu.
  - `GameShaderSlots.b000(self)` — Create network.

<a id="mat_utils--image_to_plane--_image_to_plane"></a>
### `mat_utils/image_to_plane/_image_to_plane.py`

Map image files to textured polygon planes in Maya.

- **[`class ImageToPlane(ptk.LoggingMixin)`](mayatk/mayatk/mat_utils/image_to_plane/_image_to_plane.py#L23)** — Create textured polygon planes from image files.
  - `ImageToPlane.create(cls, image_paths: List[str], mat_type: str = 'stingray', suffix: str = '_MAT', prefix: str = '', plane_height: float = 10.0, axis: Optional[List[float]] = None, group: bool = False, group_name: str = 'imagePlanes_GRP', stingray_opacity_mode: str = 'transparent', mask_threshold: float = 0.5) -> Dict[str, object]` *(class)* — Create textured planes for one or more images.
  - `ImageToPlane.remove(cls, objects=None) -> int` *(class)* — Remove planes and their materials created by this tool.

<a id="mat_utils--image_to_plane--image_to_plane_slots"></a>
### `mat_utils/image_to_plane/image_to_plane_slots.py`

Switchboard slots for the Image to Plane UI.

- **[`class ImageToPlaneSlots`](mayatk/mayatk/mat_utils/image_to_plane/image_to_plane_slots.py#L25)** — Switchboard slots for the Image to Plane UI.
  - `ImageToPlaneSlots.header_init(self, widget)` — Configure header menu.
  - `ImageToPlaneSlots.txt_suffix_init(self, widget)` — Add a prefix/suffix/auto-mode combobox to the affix field's option menu.

<a id="mat_utils--marmoset_bridge--_marmoset_bridge"></a>
### `mat_utils/marmoset_bridge/_marmoset_bridge.py`

- [`resolve_toolbag_log_path(toolbag_exe: Optional[str]) -> Optional[str]`](mayatk/mayatk/mat_utils/marmoset_bridge/_marmoset_bridge.py#L64) — Return the path to Toolbag's application log, robust to version bumps.
- [`classify_log_line(line: str) -> 'Optional[Tuple[str, str]]'`](mayatk/mayatk/mat_utils/marmoset_bridge/_marmoset_bridge.py#L126) — Map a Toolbag log line to ``(level, line)`` for routing into the bridge logger.
- [`dispatch_log_lines(lines, logger) -> None`](mayatk/mayatk/mat_utils/marmoset_bridge/_marmoset_bridge.py#L177) — Forward each classified line to *logger* at its routed level.
- [`list_templates() -> 'list[Path]'`](mayatk/mayatk/mat_utils/marmoset_bridge/_marmoset_bridge.py#L258) — Return user-visible templates in ``templates/`` (skips underscore-prefixed).
- [`template_modes(template_path: Path) -> Tuple[str, ...]`](mayatk/mayatk/mat_utils/marmoset_bridge/_marmoset_bridge.py#L265) — Return the modes declared by *template_path*'s ``BRIDGE_MODES`` constant.
- [`build_bake_pairs_manifest(objects: Sequence[str], high_suffix: str, low_suffix: str) -> Dict[str, str]`](mayatk/mayatk/mat_utils/marmoset_bridge/_marmoset_bridge.py#L314) — Build the ``{mesh_short_name: 'high'|'low'}`` sidecar for the bake.
- [`list_template_modes() -> 'list[tuple[str, str]]'`](mayatk/mayatk/mat_utils/marmoset_bridge/_marmoset_bridge.py#L364) — Return ``[(stem, mode), ...]`` for every (template, mode) pairing.
- **[`class MarmosetBridge(ptk.LoggingMixin)`](mayatk/mayatk/mat_utils/marmoset_bridge/_marmoset_bridge.py#L378)** — Export Maya selection to Marmoset Toolbag with templated automation.
  - `MarmosetBridge.toolbag_path(self) -> Optional[str]` *(property)* — Resolve the Toolbag executable path.
  - `MarmosetBridge.toolbag_path(self, value: Optional[str]) -> None`
  - `MarmosetBridge.toolbag_log_path(self) -> Optional[str]` *(property)* — Resolve Toolbag's application log file (where script prints + tracebacks land).
  - `MarmosetBridge.send(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, output_name: Optional[str] = None, toolbag_exe: Optional[str] = None, fbx_options: Optional[Dict[str, Any]] = None, preset_file: Optional[str] = None, template: str = 'import', mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]` — Export objects, render *template* in *mode*, and hand off to Toolbag.
  - `MarmosetBridge.render_template(self, template: str, fbx_path: str, manifest_path: str, output_dir: str, mode: str = SEND_TO, params: Optional[Dict[str, Any]] = None, headless: Optional[bool] = None, pairs_path: Optional[str] = None) -> Optional[str]` — Return the rendered Toolbag Python script body, or *None* on miss.

<a id="mat_utils--marmoset_bridge--_toolbag_helpers"></a>
### `mat_utils/marmoset_bridge/_toolbag_helpers.py`

Shared helpers for Marmoset Toolbag template scripts.

- [`derive_per_run_log_path(manifest_path)`](mayatk/mayatk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L41) — Return the ``<base>.toolbag.log`` path next to *manifest_path*.
- [`begin_log(reference_path)`](mayatk/mayatk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L55) — Start a fresh log file alongside *reference_path*.
- [`log(msg)`](mayatk/mayatk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L75) — Print *msg* and (best-effort) append it to the active log file.
- [`find_material(name, scene_mats)`](mayatk/mayatk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L127) — Return the Toolbag material whose name matches *name*.
- [`load_manifest(manifest_path)`](mayatk/mayatk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L142) — Return the ``materials`` dict from a MatManifest JSON sidecar.
- [`wire_materials_from_manifest(manifest_path, verbose=True)`](mayatk/mayatk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L159) — Wire every texture slot in *manifest_path* onto matching Toolbag mats.
- [`split_high_low(objects, high_suffix, low_suffix, pre_classified=None)`](mayatk/mayatk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L282) — Group *objects* into ``(highs, lows, others)`` by name suffix.
- [`collect_mesh_objects(root)`](mayatk/mayatk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L364) — Recursively gather ``mset.MeshObject`` descendants of *root*.
- [`apply_sky_preset(preset_path)`](mayatk/mayatk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L404) — Load a ``.tbsky`` preset onto the scene's existing SkyObject.
- [`frame_in_viewport()`](mayatk/mayatk/mat_utils/marmoset_bridge/_toolbag_helpers.py#L428) — Frame the imported scene in the viewport (best-effort).

<a id="mat_utils--marmoset_bridge--marmoset_bridge_slots"></a>
### `mat_utils/marmoset_bridge/marmoset_bridge_slots.py`

Slots for the Marmoset Toolbag bridge panel.

- **[`class MarmosetBridgeSlots(MayaBridgeSlotsBase)`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_bridge_slots.py#L41)** — Slots wired to ``marmoset_bridge.ui`` via :class:`MayaBridgeSlotsBase`.
  - `MarmosetBridgeSlots.params_module(self)` *(property)*
  - `MarmosetBridgeSlots.template_dir(self) -> Path` *(property)*
  - `MarmosetBridgeSlots.make_bridge(self) -> MarmosetBridge`
  - `MarmosetBridgeSlots.list_template_modes(self)`
  - `MarmosetBridgeSlots.select_initial_template_index(self, pairs)` — Prefer 'bake (roundtrip)' then 'bake (send_to)', else first entry.
  - `MarmosetBridgeSlots.header_init(self, widget)` — Configure header menu with utilities (no per-call options).
  - `MarmosetBridgeSlots.b000(self)` — Process selected transforms with the chosen template + mode.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--connection"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/connection.py`

Maya-side JSON-RPC client for the marmoset_rpc Toolbag plugin.

- **[`class MarmosetConnection(RpcClient)`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/connection.py#L46)** — JSON-RPC client bound to Toolbag's default port + finder.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--installer"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/installer.py`

Install the marmoset_rpc plugin into Toolbag's user plugin folder.

- [`user_plugin_dir(toolbag_exe: Optional[str] = None) -> Optional[Path]`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/installer.py#L38) — Resolve ``%LOCALAPPDATA%\Marmoset Toolbag <N>\plugins``.
- [`is_installed(toolbag_exe: Optional[str] = None) -> bool`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/installer.py#L71) — True if the plugin is present at the resolved user plugin dir.
- [`install(toolbag_exe: Optional[str] = None, force: bool = False) -> Optional[Path]`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/installer.py#L79) — Install the plugin into Toolbag's user plugin folder.
- [`uninstall(toolbag_exe: Optional[str] = None) -> bool`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/installer.py#L98) — Remove the plugin from the user plugin folder.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--job"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/job.py`

One-shot batch pipeline for the marmoset_rpc bridge.

- [`run_batch(calls: List[Call], host: str = '127.0.0.1', port: int = 8765, stop_on_error: bool = False) -> List[Result]`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/job.py#L30) — Connect to a running Toolbag's marmoset_rpc plugin and fire calls.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--main_thread"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/main_thread.py`

Main-thread marshalling for ops that touch Toolbag's API.

- [`run_on_main_thread(fn, *args, timeout=_DEFAULT_TIMEOUT, **kwargs)`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/main_thread.py#L50) — Run *fn* on the Qt main thread;
- [`is_main_thread_marshalling_active()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/main_thread.py#L113) — True if :func:`run_on_main_thread` will actually marshal a call.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--ops--scene_ops"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/scene_ops.py`

Scene-inspection ops.

- [`summary()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/scene_ops.py#L14) — High-level snapshot of the current Toolbag scene.
- [`list_materials()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/scene_ops.py#L39) — Material names in the current scene.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--ops--system_ops"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py`

System-level ops: heartbeat, introspection, Toolbag version.

- [`ping()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py#L8) — Heartbeat -- proves the plugin is alive.
- [`list_ops()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py#L14) — Sorted list of every registered op name.
- [`describe_op(op='')`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py#L20) — Return the JSON-friendly description of *op* or all ops if empty.
- [`version()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py#L31) — Toolbag build number (e.g.

<a id="mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--registry"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/registry.py`

Op registry for the marmoset_rpc plugin.

- [`register(name)`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/registry.py#L21) — Decorator: register *fn* under *name*.
- [`get(name)`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/registry.py#L36) — Return the op function for *name*, or None.
- [`all_ops()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/registry.py#L41) — Sorted list of every registered op name.
- [`describe(name=None)`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/registry.py#L46) — Return a JSON-friendly description of one op or all ops.
- [`clear()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/registry.py#L82) — Reset the registry (test-only).

<a id="mat_utils--marmoset_bridge--marmoset_rpc--plugin_src--marmoset_rpc--server"></a>
### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/server.py`

HTTP JSON-RPC server for the marmoset_rpc plugin.

- [`start_server(port=None, host='127.0.0.1')`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/server.py#L96) — Start the HTTP server in a daemon thread.
- [`stop_server()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/server.py#L116) — Shut down the server (mostly useful for tests / hot-reload).
- [`is_running()`](mayatk/mayatk/mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/server.py#L129)

<a id="mat_utils--marmoset_bridge--parameters"></a>
### `mat_utils/marmoset_bridge/parameters.py`

Registry of user-tunable Marmoset Toolbag parameters exposed to the bridge UI.

- [`referenced_keys(script_text: str) -> 'set[str]'`](mayatk/mayatk/mat_utils/marmoset_bridge/parameters.py#L235) — Registered keys present in *script_text* (delegates to uitk.bridge).
- [`defaults() -> 'dict[str, Any]'`](mayatk/mayatk/mat_utils/marmoset_bridge/parameters.py#L240) — Return ``{key: default}`` for every registered parameter.
- [`render_context(values: 'dict[str, Any]') -> 'dict[str, str]'`](mayatk/mayatk/mat_utils/marmoset_bridge/parameters.py#L245) — Format *values* for ``StrUtils.replace_delimited`` using Python literals.

<a id="mat_utils--marmoset_bridge--templates--bake"></a>
### `mat_utils/marmoset_bridge/templates/bake.py`

Bake high-poly detail into a low-poly target via Marmoset Toolbag.

- [`main()`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/bake.py#L123)

<a id="mat_utils--marmoset_bridge--templates--import"></a>
### `mat_utils/marmoset_bridge/templates/import.py`

Open the FBX in Toolbag and wire materials from the Maya manifest.

- [`main()`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/import.py#L31)

<a id="mat_utils--marmoset_bridge--templates--lookdev"></a>
### `mat_utils/marmoset_bridge/templates/lookdev.py`

Open the FBX in Toolbag, apply a Sky preset, and frame the model.

- [`main()`](mayatk/mayatk/mat_utils/marmoset_bridge/templates/lookdev.py#L41)

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

<a id="mat_utils--mat_transfer"></a>
### `mat_utils/mat_transfer.py`

- **[`class MatTransfer(ptk.LoggingMixin)`](mayatk/mayatk/mat_utils/mat_transfer.py#L19)**
  - `MatTransfer.is_material_related_node(self, node) -> bool` — Check if a node is material-related.
  - `MatTransfer.get_material_assignments(self, obj) -> Dict[str, List]` — Get material assignments for an object.
  - `MatTransfer.collect_material_assignments(self, obj)` — Collect material assignments including shaders and textures.
  - `MatTransfer.handle_object_materials(self, target_obj, material_assignments: Dict) -> None` — Simple material handling - let Maya do the heavy lifting.

<a id="mat_utils--mat_updater"></a>
### `mat_utils/mat_updater.py`

- **[`class MatUpdater(ptk.LoggingMixin)`](mayatk/mayatk/mat_utils/mat_updater.py#L24)** — Updates existing materials with processed textures.
  - `MatUpdater.update_materials(cls, materials: List[Any] = None, config: Union[str, Dict[str, Any]] = None, verbose: bool = False, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Any]` *(class)* — Update materials with processed textures.
  - `MatUpdater.disconnect_associated_attributes(cls, material, file_paths, config=None)` *(class)* — Disconnects PBR attributes if they are driven by the specified files.
  - `MatUpdater.update_network(cls, material, texture_paths, config) -> Dict[str, str]` *(class)* — Connect processed textures to the material.
- **[`class MatUpdaterSlots(MatUpdater)`](mayatk/mayatk/mat_utils/mat_updater.py#L613)**
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

- **[`class OpacityMaterialMode(ptk.LoggingMixin)`](mayatk/mayatk/mat_utils/render_opacity/material_mode.py#L18)** — Implements the 'material' mode for RenderOpacity.
  - `OpacityMaterialMode.get_stingray_mats(cls, objects: Optional[list] = None) -> list` *(class)* — Return unique StingrayPBS materials assigned to *objects*.
  - `OpacityMaterialMode.ensure_transparent_graph(cls, mat) -> bool` *(class)* — Load Standard_Transparent.sfx onto a StingrayPBS node if needed.
  - `OpacityMaterialMode.create(cls, objects) -> Dict[str, Dict]` *(class)* — Expose StingrayPBS transparency (load graph).
  - `OpacityMaterialMode.ensure_connections(cls, objects) -> None` *(class)* — Re-establish ``Transform.opacity → Material.opacity`` proxy
  - `OpacityMaterialMode.remove(cls, objects)` *(class)* — Remove material-mode artifacts from *objects*.

<a id="mat_utils--render_opacity--render_opacity_slots"></a>
### `mat_utils/render_opacity/render_opacity_slots.py`

Switchboard slots for the Render Opacity UI.

- **[`class RenderOpacitySlots`](mayatk/mayatk/mat_utils/render_opacity/render_opacity_slots.py#L20)** — Switchboard slots for the Render Opacity UI.
  - `RenderOpacitySlots.header_init(self, widget)` — Configure header menu.
  - `RenderOpacitySlots.tb000_init(self, widget)` — Key Render Opacity Init — configure option-box menu.
  - `RenderOpacitySlots.tb000(self, widget)` — Key Render Opacity — key a fade on the opacity attribute.

<a id="mat_utils--shader_attribute_map"></a>
### `mat_utils/shader_attribute_map.py`

- **[`class ShaderAttributeMap`](mayatk/mayatk/mat_utils/shader_attribute_map.py#L29)** — Central mapping of logical texture/material channels to per-shader attribute/plug pairs.
  - `ShaderAttributeMap.logical_channels(cls) -> Tuple[str, ...]` *(class)* — Returns the logical channel names as a tuple.
  - `ShaderAttributeMap.get_attr(cls, shader_type: str, logical: str) -> Optional[Tuple[str, str]]` *(class)* — Return (attribute, plug) tuple for shader type and logical channel, or None.
  - `ShaderAttributeMap.get_mapping(cls, src_type: str, dst_type: str) -> Tuple[Tuple[str, str, str], ...]` *(class)* — Returns a tuple of (src_attr, src_plug, dst_attr) for each logical channel present in both shader t…
  - `ShaderAttributeMap.add_shader_type(cls, shader_type: str, attrs: ShaderAttrs) -> None` *(class)* — Add a new shader type mapping.
  - `ShaderAttributeMap.update_attr(cls, shader_type: str, logical: str, value: Optional[Tuple[str, str]]) -> None` *(class)* — Update a logical channel mapping for a shader type.
  - `ShaderAttributeMap.as_dict(cls) -> Dict[str, Dict[str, Any]]` *(class)* — Returns a dict of dicts for all shader mappings.

<a id="mat_utils--shader_remapper"></a>
### `mat_utils/shader_remapper.py`

- **[`class ShaderRemapper(ptk.LoggingMixin)`](mayatk/mayatk/mat_utils/shader_remapper.py#L16)**
  - `ShaderRemapper.remap_shaders(self, shaders: List[str], target_type: str) -> Dict[str, str]` — For each shader:

<a id="mat_utils--shader_templates--_shader_templates"></a>
### `mat_utils/shader_templates/_shader_templates.py`

- **[`class GraphCollector`](mayatk/mayatk/mat_utils/shader_templates/_shader_templates.py#L25)**
  - `GraphCollector.collect_graph(self, nodes)`
- **[`class GraphSaver(GraphCollector)`](mayatk/mayatk/mat_utils/shader_templates/_shader_templates.py#L156)**
  - `GraphSaver.save_graph(self, nodes: List[str], file_path: str, exclude_types: Optional[List[str]] = None) -> None`
- **[`class GraphRestorer`](mayatk/mayatk/mat_utils/shader_templates/_shader_templates.py#L197)**
  - `GraphRestorer.load_yaml(self)` — Load and return graph configuration from a YAML file.
  - `GraphRestorer.restore_graph(self)` — Restore the graph based on the YAML configuration and textures.
  - `GraphRestorer.restore_connections(self)` — Connect nodes as specified in the graph configuration.
- **[`class ShaderTemplates`](mayatk/mayatk/mat_utils/shader_templates/_shader_templates.py#L431)** — Facade class for managing shader templates.
  - `ShaderTemplates.save_template(nodes, file_path, exclude_types=None)` *(static)* — Save the specified nodes as a shader template.
  - `ShaderTemplates.restore_template(file_path, texture_paths=None, name=None)` *(static)* — Restore a shader template from a file.
- **[`class ShaderTemplatesSlots(ptk.LoggingMixin)`](mayatk/mayatk/mat_utils/shader_templates/_shader_templates.py#L470)**
  - `ShaderTemplatesSlots.template_name(self)` *(property)*
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

- [`list_templates() -> List[Path]`](mayatk/mayatk/mat_utils/substance_bridge/_substance_bridge.py#L126) — Return user-visible templates in ``templates/`` (skips underscore-prefixed).
- [`parse_template(template_path: Path) -> Dict[str, Any]`](mayatk/mayatk/mat_utils/substance_bridge/_substance_bridge.py#L144) — Read a template's metadata constants without executing the file.
- [`list_template_modes() -> List[Tuple[str, str]]`](mayatk/mayatk/mat_utils/substance_bridge/_substance_bridge.py#L206) — Return ``[(stem, mode), ...]`` for every (template, mode) pairing.
- [`resolve_painter_log_path(painter_exe: Optional[str] = None) -> Optional[str]`](mayatk/mayatk/mat_utils/substance_bridge/_substance_bridge.py#L218) — Return the path to Painter's application log.
- **[`class SubstanceBridge(ptk.LoggingMixin)`](mayatk/mayatk/mat_utils/substance_bridge/_substance_bridge.py#L235)** — Export Maya selection to Substance Painter via a chosen template.
  - `SubstanceBridge.painter_path(self) -> Optional[str]` *(property)* — Resolve the Painter executable path via :func:`find_painter_exe`.
  - `SubstanceBridge.painter_path(self, value: Optional[str]) -> None`
  - `SubstanceBridge.painter_log_path(self) -> Optional[str]` *(property)* — Path to Painter's application ``log.txt``, or *None* if absent.
  - `SubstanceBridge.instances(self) -> List[SubstanceConnection]` *(property)* — Live snapshot of managed connections (oldest -> newest, dead pruned).
  - `SubstanceBridge.find_live_managed(self) -> Optional[SubstanceConnection]` — Return the most-recently-launched managed instance whose RPC pings.
  - `SubstanceBridge.send(self, objects: Optional[List[str]] = None, output_dir: Optional[str] = None, output_name: Optional[str] = None, painter_exe: Optional[str] = None, fbx_options: Optional[Dict[str, Any]] = None, preset_file: Optional[str] = None, template: str = 'import', mode: str = SEND_TO, target: Union[str, int] = TARGET_AUTO, params: Optional[Dict[str, Any]] = None, **legacy_kwargs: Any) -> Optional[Dict[str, Any]]` — Export *objects*, render *template* in *mode*, hand off to Painter.

<a id="mat_utils--substance_bridge--connection"></a>
### `mat_utils/substance_bridge/connection.py`

Substance 3D Painter connection module.

- [`find_painter_exe() -> Optional[str]`](mayatk/mayatk/mat_utils/substance_bridge/connection.py#L59) — Single source of truth for Painter executable discovery.
- [`default_log_path() -> Optional[str]`](mayatk/mayatk/mat_utils/substance_bridge/connection.py#L73) — Return the standard Substance Painter log path, or None if absent.
- **[`class OutputStream`](mayatk/mayatk/mat_utils/substance_bridge/connection.py#L89)** — Thread-safe, multi-consumer text stream with bounded history.
  - `OutputStream.push(self, line: str, source: str = '') -> None` — Append a line.
  - `OutputStream.subscribe(self, callback: Callable[[str, str], None], replay_history: bool = False) -> Callable[[], None]` — Register ``callback(source, line)``.
  - `OutputStream.history(self) -> List[Tuple[str, str]]` — Snapshot the current history buffer.
  - `OutputStream.clear_history(self) -> None` — Drop buffered lines.
  - `OutputStream.wait_for(self, pattern: Union[str, Pattern], timeout: Optional[float] = None, source: Optional[str] = None, include_history: bool = True) -> Optional[Tuple[str, str]]` — Block until a line matches *pattern*, or *timeout* expires.
  - `OutputStream.close(self) -> None` — Mark the stream closed.
  - `OutputStream.closed(self) -> bool` *(property)*
- **[`class SubstanceConnection(ptk.LoggingMixin)`](mayatk/mayatk/mat_utils/substance_bridge/connection.py#L342)** — Launch Painter and expose its stdio, log, and RPC under one object.
  - `SubstanceConnection.open(self) -> 'SubstanceConnection'` — Launch Painter and start readers, tailer, and RPC client.
  - `SubstanceConnection.close(self, terminate: bool = False, timeout: float = 5.0) -> None` — Stop readers and tailer;
  - `SubstanceConnection.is_alive(self) -> bool` — True if Painter is reachable through this connection.
  - `SubstanceConnection.attach(cls, port: int, host: str = '127.0.0.1', log_path: Optional[str] = None, tail_log_from_start: bool = False, verify_alive: bool = True, verify_timeout: float = 2.0) -> 'SubstanceConnection'` *(class)* — Bind to a running Painter on *port* without launching anything.

<a id="mat_utils--substance_bridge--parameters"></a>
### `mat_utils/substance_bridge/parameters.py`

Registry of user-tunable Substance Painter parameters exposed to the bridge UI.

- [`referenced_keys(script_text: str) -> 'set[str]'`](mayatk/mayatk/mat_utils/substance_bridge/parameters.py#L174) — Registered keys present in *script_text* (delegates to uitk.bridge).
- [`defaults() -> 'dict[str, Any]'`](mayatk/mayatk/mat_utils/substance_bridge/parameters.py#L179) — Return ``{key: default}`` for every registered parameter.
- [`render_cli_context(values: 'dict[str, Any]') -> 'dict[str, str]'`](mayatk/mayatk/mat_utils/substance_bridge/parameters.py#L184) — Format *values* for ``LAUNCH_ARGS`` -- raw, no quoting.
- [`render_js_context(values: 'dict[str, Any]') -> 'dict[str, str]'`](mayatk/mayatk/mat_utils/substance_bridge/parameters.py#L189) — Format *values* for ``RPC_SCRIPT`` -- JS-literal quoting/escaping.

<a id="mat_utils--substance_bridge--substance_bridge_slots"></a>
### `mat_utils/substance_bridge/substance_bridge_slots.py`

Slots for the Substance Painter bridge panel.

- **[`class SubstanceBridgeSlots(MayaBridgeSlotsBase)`](mayatk/mayatk/mat_utils/substance_bridge/substance_bridge_slots.py#L50)** — Slots wired to ``substance_bridge.ui`` via :class:`MayaBridgeSlotsBase`.
  - `SubstanceBridgeSlots.params_module(self)` *(property)*
  - `SubstanceBridgeSlots.template_dir(self) -> Path` *(property)*
  - `SubstanceBridgeSlots.make_bridge(self) -> SubstanceBridge`
  - `SubstanceBridgeSlots.list_template_modes(self)`
  - `SubstanceBridgeSlots.select_initial_template_index(self, pairs)` — Default the panel to ``import (send_to)`` when it's available.
  - `SubstanceBridgeSlots.header_init(self, widget)` — Configure the header menu with template / log utilities.
  - `SubstanceBridgeSlots.b000(self)` — Process the selected transforms with the chosen template + mode.

<a id="mat_utils--substance_bridge--substance_rpc--client"></a>
### `mat_utils/substance_bridge/substance_rpc/client.py`

JSON-RPC 2.0 client for a Painter-side Python plugin.

- **[`class PainterRpcClient`](mayatk/mayatk/mat_utils/substance_bridge/substance_rpc/client.py#L24)** — JSON-RPC 2.0 client for a Painter-side JSON server.
  - `PainterRpcClient.url(self) -> str` *(property)*
  - `PainterRpcClient.ping(self, timeout: float = 1.0) -> bool` — Return True if a TCP connection succeeds.
  - `PainterRpcClient.wait_until_ready(self, timeout: float = 60.0, poll_interval: float = 0.5) -> bool` — Poll the port until it accepts connections, or *timeout* expires.
  - `PainterRpcClient.call(self, method: str, params: Optional[dict] = None) -> dict` — Send a JSON-RPC method call.
  - `PainterRpcClient.eval_js(self, script: str) -> dict` — Convenience: execute a JavaScript snippet via ``eval``.

<a id="mat_utils--texture_path_editor"></a>
### `mat_utils/texture_path_editor.py`

- **[`class TexturePathEditorSlots`](mayatk/mayatk/mat_utils/texture_path_editor.py#L23)**
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
  - `NodeUtils.get_shapes(node, no_intermediate=True, full_path=True)` *(static)* — Return the shape children of a transform.
  - `NodeUtils.get_shape(cls, node, no_intermediate=True, full_path=True)` *(class)* — Return the first shape of a transform, or ``None``.
  - `NodeUtils.is_intermediate(shape)` *(static)* — Return True if *shape* is an intermediate (orig) shape.
  - `NodeUtils.node_is(node, type_name)` *(static)* — Return True if ``cmds.objectType(node)`` matches *type_name* exactly.
  - `NodeUtils.list_transforms(objects=None, **ls_kwargs)` *(static)* — Transforms whose shapes match the given ``cmds.ls`` criteria.
  - `NodeUtils.get_unique_children(cls, objects)` *(class)* — Retrieves a unique list of objects' children (if any) in the scene, excluding the groups themselves.
  - `NodeUtils.get_transform_node(nodes, returned_type='obj', attributes=False, inc=[], exc=[])` *(static)* — Get transform node(s) or node attributes.
  - `NodeUtils.get_shape_node(cls, nodes, returned_type='obj', attributes=False, inc=[], exc=[])` *(class)* — Get shape node(s) or node attributes.
  - `NodeUtils.get_history_node(nodes, returned_type='obj', attributes=False, inc=[], exc=[])` *(static)* — Get history node(s) or node attributes.
  - `NodeUtils.create_render_node(cls, node_type, classification=None, category=None, name=None, create_placement_nodes=False, create_shading_group=True, **attributes)` *(class)* — Creates a Maya node of a specified type with enhanced control over the creation process.
  - `NodeUtils.get_connected_nodes(node, node_type=None, direction=None, exact=True, first_match=False)` *(static)* — Finds connected nodes of a given type and direction (incoming/outgoing).
  - `NodeUtils.create_assembly(nodes, assembly_name='assembly#', duplicate=False)` *(static)* — Create an assembly by parenting the input nodes to a new assembly node.
  - `NodeUtils.get_instances(objects=None, return_parent_objects=False)` *(static)* — Get any instances of given object, or if None given, get all instanced objects in the scene.
  - `NodeUtils.replace_with_instances(cls, objects=None, append='', freeze_transforms=False, center_pivot=True, delete_history=True)` *(class)* — Replace target objects with instances of the source object.
  - `NodeUtils.instance(cls, *args, **kwargs)` *(class)* — Deprecated: Use replace_with_instances instead.
  - `NodeUtils.uninstance(cls, objects)` *(class)* — Un-Instance the given objects.
  - `NodeUtils.filter_duplicate_instances(nodes) -> List[str]` *(static)* — Keep only one transform per instance group.
  - `NodeUtils.ensure_data_node(node_name: str, attr_name: str) -> str` *(static)* — Get or create a name-locked network node with a writable string attribute.

<a id="node_utils--attributes--_attributes"></a>
### `node_utils/attributes/_attributes.py`

Consolidated attribute utilities for Maya.

- **[`class AttributeTemplate`](mayatk/mayatk/node_utils/attributes/_attributes.py#L31)** — Defines the configuration for a Maya attribute.
- **[`class Preset(NamedTuple)`](mayatk/mayatk/node_utils/attributes/_attributes.py#L56)** — A named bundle of attributes loaded from a YAML template.
- **[`class Attributes(ptk.HelpMixin)`](mayatk/mayatk/node_utils/attributes/_attributes.py#L68)** — Consolidated utility for managing Maya node attributes.
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

- **[`class Channels`](mayatk/mayatk/node_utils/attributes/channels/_channels.py#L15)** — Maya attribute query / mutation logic.
  - `Channels.is_pinned(self)` *(property)*
  - `Channels.single_object_mode(self)` *(property)*
  - `Channels.single_object_mode(self, value)`
  - `Channels.pin_targets(self, nodes)` — Pin the manager to a fixed node list;
  - `Channels.get_selected_nodes(self)` — Return the target node list.
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

- **[`class ChannelsSlots`](mayatk/mayatk/node_utils/attributes/channels/channels_slots.py#L24)** — Switchboard slots for the Channels UI.
  - `ChannelsSlots.apply_launch_config(self, targets=None, filter=None, search=None)` — Configure the window from a :func:`launch` call.
  - `ChannelsSlots.header_init(self, widget)` — Populate the header menu with global actions.
  - `ChannelsSlots.show_create_menu(self, *args)` — Show the *Create Attribute* popup.
  - `ChannelsSlots.cmb000_init(self, widget)` — Populate filter combobox and wire its option_box invert action.
  - `ChannelsSlots.cmb000(self, index)` — Filter changed — refresh table.
  - `ChannelsSlots.tbl000_init(self, widget)` — One-time table setup: action columns, context menu, scriptJobs.
  - `ChannelsSlots.cleanup_scene_callbacks(self)` — Tear down every event subscription owned by this slots instance.

<a id="node_utils--data_nodes"></a>
### `node_utils/data_nodes.py`

- **[`class DataNodes`](mayatk/mayatk/node_utils/data_nodes.py#L14)** — Manages the two shared scene data nodes.
  - `DataNodes.ensure_internal()` *(static)* — Get or create the shared network node.
  - `DataNodes.ensure_export()` *(static)* — Get or create the shared FBX export transform.
  - `DataNodes.mirror_attr(attr_name, **add_attr_kwargs)` *(static)* — Ensure *attr_name* on ``data_internal`` with a proxy on ``data_export``.
  - `DataNodes.migrate_legacy_carriers()` *(static)* — Migrate old ``audio_events*`` carrier transforms to the new nodes.

<a id="nurbs_utils--_nurbs_utils"></a>
### `nurbs_utils/_nurbs_utils.py`

- **[`class NurbsUtils(ptk.HelpMixin)`](mayatk/mayatk/nurbs_utils/_nurbs_utils.py#L19)**
  - `NurbsUtils.loft(cls, uniform=True, close=False, degree=3, autoReverse=False, sectionSpans=1, range_=False, polygon=True, reverseSurfaceNormals=True, angle_loft_between_two_curves=False, angleLoftSpans=6)` *(class)* — Create a loft between two selections.
  - `NurbsUtils.create_curve_between_two_objs(cls, start, end)` *(class)* — Create a bezier curve between starting and end object(s).
  - `NurbsUtils.duplicate_along_curve(path, start, count=6, geometry='Instancer')` *(static)* — Duplicate objects along a given curve using MASH.
  - `NurbsUtils.angle_loft_between_two_curves(cls, start, end, count=6, cleanup=False, uniform=1, close=0, autoReverse=0, degree=3, sectionSpans=1, range=0, polygon=1, reverseSurfaceNormals=0)` *(class)* — Perform a loft between two nurbs curves or polygon sets of edges (that will be extracted as curves).
  - `NurbsUtils.get_closest_cv(x, curves, tolerance=0.0)` *(static)* — Find the closest control vertex between the given vertices, CVs, or objects and each of the given c…
  - `NurbsUtils.get_cv_info(cls, c, returned_type='cv', filter_=[])` *(class)* — Get a dict containing CV's of the given curve(s) and their corresponding point positions (based on…
  - `NurbsUtils.getCrossProductOfCurves(cls, curves, normalize=1, values=False)` *(class)* — Get the cross product of two vectors using points derived from the given curves.

<a id="nurbs_utils--image_tracer"></a>
### `nurbs_utils/image_tracer.py`

- **[`class BluePencilMixin(object)`](mayatk/mayatk/nurbs_utils/image_tracer.py#L26)** — Mixin for handling Blue Pencil operations.
  - `BluePencilMixin.get_blue_pencil_curves(self)` — Converts active Blue Pencil strokes to NURBS curves.
- **[`class ImageTracer(BluePencilMixin)`](mayatk/mayatk/nurbs_utils/image_tracer.py#L109)** — A class to trace images into Maya NURBS curves and generate geometry.
  - `ImageTracer.trace_curves(self) -> List[str]` — Traces the image and returns a list of created NURBS curves.
  - `ImageTracer.create_mesh(self, curves: Optional[List[str]] = None, combine: bool = True, name: str = 'traced_mesh', group_output: bool = True) -> Union[str, List[str]]` — Creates a polygon mesh from the traced curves (positive space).
  - `ImageTracer.create_negative_space_mesh(self, curves: Optional[List[str]] = None, margin_scale: float = 0.1, name: str = 'negative_space_mesh', group_output: bool = True) -> Optional[str]` — Creates a mesh representing the negative space (plane with holes).
  - `ImageTracer.project_on_plane(self, curves: Optional[List[str]] = None, name: str = 'projected_curves', group_output: bool = True) -> Union[str, List[str], None]` — Projects curves onto a plane.
- **[`class ImageTracerSlots`](mayatk/mayatk/nurbs_utils/image_tracer.py#L396)** — UI slots for the Image Tracer tool.
  - `ImageTracerSlots.header_init(self, widget)` — Initialize the header widget.
  - `ImageTracerSlots.txt000_init(self, widget)`
  - `ImageTracerSlots.browse_image(self)`
  - `ImageTracerSlots.chk000(self, state)` — Use Blue Pencil
  - `ImageTracerSlots.b002(self)`
  - `ImageTracerSlots.b003(self)`
  - `ImageTracerSlots.b004(self)`
  - `ImageTracerSlots.b005(self)`

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
  - `RigUtils.rebind_skin_clusters(cls, meshes: Optional[List[str]] = None, temp_dir: Optional[str] = None, inherits_transform: Optional[bool] = None) -> None` *(class)* — Rebinds skinClusters on the given meshes, preserving weights, bind pose, and transform lock state.

<a id="rig_utils--controls"></a>
### `rig_utils/controls.py`

- **[`class ControlNodes`](mayatk/mayatk/rig_utils/controls.py#L22)**
- **[`class Controls(ptk.HelpMixin)`](mayatk/mayatk/rig_utils/controls.py#L57)** — Factory for creating NURBS animation controls.
  - `Controls.register_preset(cls, name: str, builder: Callable[..., str]) -> None` *(class)* — Register a new control preset.
  - `Controls.create(cls, preset: str = 'diamond', name: Optional[str] = None, *, size: float = 1.0, axis: str = 'y', match: Any = None, parent: Optional[str] = None, color: Union[int, Tuple[float, float, float], None] = None, offset_group: bool = True, group_suffix: str = '_GRP', ctrl_suffix: str = '_CTRL', freeze: bool = True, tag_as_controller: bool = True, return_nodes: bool = False, **kwargs) -> Union[str, ControlNodes]` *(class)* — Create a NURBS control.
  - `Controls.combine(cls, controls: Iterable[Any], name: Optional[str] = None, *, parent: Optional[str] = None, match: Any = None, color: Union[int, Tuple[float, float, float], None] = None, delete_sources: bool = True, ctrl_suffix: str = '_CTRL') -> str` *(class)* — Combine multiple control transforms into a single selectable transform.

<a id="rig_utils--shadow_rig"></a>
### `rig_utils/shadow_rig.py`

- **[`class ShadowRig(ptk.LoggingMixin)`](mayatk/mayatk/rig_utils/shadow_rig.py#L22)** — Projected shadow for Unity export.
  - `ShadowRig.create_contact_locator(self)` — Create a locator at the lowest point of the combined objects to act as the shadow anchor.
  - `ShadowRig.get_or_create_shadow_source(self, position=(5, 10, 5), source_name='shadow_source')` — Get existing shadow source or create a new one.
  - `ShadowRig.create_shadow_plane(self)` — Create a simple quad for the shadow with pivot at near edge.
  - `ShadowRig.create_silhouette_texture(self, size=512, axis='auto', recursive=True, *, uniform_alpha=False, falloff_source=None, falloff_power=0.8, vertical_weight=0.3, blur_amount=1.5)` — Create silhouette texture using Maya API triangle rasterization.
  - `ShadowRig.create_material(self, shader_type='stingray', stingray_opacity_mode='transparent')` — Create material with the silhouette texture.
  - `ShadowRig.setup_expression(self)` — Create expression to warp shadow based on light position.
  - `ShadowRig.create(cls, targets, light_pos=(5, 10, 5), texture_res=512, axis='auto', source_name='shadow_source', recursive=True, mode='stretch')` *(class)* — Create a projected shadow for Unity export.
- **[`class ShadowRigSlots`](mayatk/mayatk/rig_utils/shadow_rig.py#L896)**
  - `ShadowRigSlots.header_init(self, widget)` — Configure header help text.
  - `ShadowRigSlots.b001(self)` — Reset to Defaults: Resets all UI widgets to their default values.
  - `ShadowRigSlots.perform_operation(self, objects, contract)` — Build the shadow rig for the given targets.

<a id="rig_utils--telescope_rig"></a>
### `rig_utils/telescope_rig.py`

- **[`class TelescopeRig(ptk.LoggingMixin)`](mayatk/mayatk/rig_utils/telescope_rig.py#L16)** — Telescope Rig
  - `TelescopeRig.setup_telescope_rig(self, base_locator: Union[str, List[str]], end_locator: Union[str, List[str]], segments: List[str], collapsed_distance: float = 1.0)` — Sets up constraints and driven keys to make a series of segments telescope between two locators.
- **[`class TelescopeRigSlots(ptk.LoggingMixin)`](mayatk/mayatk/rig_utils/telescope_rig.py#L169)**
  - `TelescopeRigSlots.header_init(self, widget)` — Configure header help text.
  - `TelescopeRigSlots.build_rig(self)`

<a id="rig_utils--tube_rig"></a>
### `rig_utils/tube_rig.py`

- **[`class TubePath`](mayatk/mayatk/rig_utils/tube_rig.py#L78)** — Pure geometry analysis for tube-like meshes.
  - `TubePath.get_centerline(mesh, num_joints: int = 10, precision: int = 10, edges: list = None, use_surface_normals: bool = True) -> Tuple[List, int]` *(static)* — Unified centerline dispatcher — picks the best algorithm.
  - `TubePath.get_edge_loop_centers(mesh) -> Tuple[List[om.MPoint], int]` *(static)* — Extract centerline by finding all edge loops (cross-sections) of a tube mesh.
  - `TubePath.get_centerline_using_edges(edge_selection: List[str]) -> List[om.MPoint]` *(static)* — Extracts the centerline points from selected edges of the tube.
  - `TubePath.get_centerline_from_surface_normals(mesh, num_points: int = 10, iterations: int = 3) -> List[om.MPoint]` *(static)* — Calculate centerline by iteratively averaging opposing surface hits.
  - `TubePath.get_centerline_from_bounding_box(obj, precision=10, smooth=False, window_size=1)` *(static)* — Calculate the centerline of an object using the cross-section of its largest bounding box axis.
- **[`class TubeRigBundle`](mayatk/mayatk/rig_utils/tube_rig.py#L465)**
- **[`class TubeStrategy(ABC)`](mayatk/mayatk/rig_utils/tube_rig.py#L479)**
  - `TubeStrategy.build(self, rig: 'TubeRig', **kwargs) -> TubeRigBundle`
- **[`class FKChainStrategy(TubeStrategy)`](mayatk/mayatk/rig_utils/tube_rig.py#L485)**
  - `FKChainStrategy.build(self, rig: 'TubeRig', **kwargs) -> TubeRigBundle`
- **[`class SplineIKStrategy(TubeStrategy)`](mayatk/mayatk/rig_utils/tube_rig.py#L549)**
  - `SplineIKStrategy.build(self, rig: 'TubeRig', **kwargs) -> TubeRigBundle`
- **[`class AnchorStrategy(TubeStrategy)`](mayatk/mayatk/rig_utils/tube_rig.py#L646)**
  - `AnchorStrategy.build(self, rig: 'TubeRig', **kwargs) -> TubeRigBundle`
- **[`class TubeRig(ptk.LoggingMixin)`](mayatk/mayatk/rig_utils/tube_rig.py#L855)** — Handles rigging the tube, creating joints, IK handles, and additional controls.
  - `TubeRig.for_mesh(cls, mesh) -> Optional['TubeRig']` *(class)* — Look up an existing TubeRig instance bound to *mesh*, or return None.
  - `TubeRig.rig_name(self) -> str` *(property)* — Returns the rig name.
  - `TubeRig.rig_group(self) -> str` *(property)*
  - `TubeRig.rig_group(self, new_group: 'object')` — Allows setting a custom rig group.
  - `TubeRig.build(self, strategy: str = 'spline', **kwargs)` — Builds the rig using the specified strategy.
  - `TubeRig.generate_joint_chain(self, centerline: List[List[float]], num_joints: int, reverse: bool = False, **kwargs) -> List[str]` — Generates joints along the tube's centerline.
  - `TubeRig.create_logic_curve(self, centerline: List[List[float]]) -> str` — Creates the logic curve for Spline IK.
  - `TubeRig.create_spline_drivers(self, centerline: List[List[float]], radius: float = 1.0, num_controls: int = 3) -> Tuple[List[str], List[str], List]` — Creates the driver system (controls and joints) for the Spline IK curve.
  - `TubeRig.skin_curve_to_drivers(self, curve, driver_joints)`
  - `TubeRig.setup_spline_twist(self, ik_handle, start_ctrl, end_ctrl, start_up_loc=None, end_up_loc=None)` — Setup advanced twist for IK Spline.
  - `TubeRig.setup_auto_bend(self, start_ctrl, mid_ctrl, end_ctrl)` — Setup automatic bending of the mid control based on compression distance.
  - `TubeRig.setup_spline_stretch(self, curve, joints, enable_stretch=True, enable_squash=True, enable_volume=True, main_control=None)`
  - `TubeRig.create_start_end_locators(self, joints: List[str], ik_handle: Optional[str] = None) -> Tuple[str, str]`
  - `TubeRig.create_ik(self, joints: List[str], **kwargs) -> Optional[str]`
  - `TubeRig.create_pole_vector(self, ik_handle, mid_joint: str, offset=(0, 5, 0)) -> str`
  - `TubeRig.bind_joint_chain(self, obj, joints: List[str]) -> Optional[str]` — Binds the joint chain to a polygon tube with smooth skinning.
  - `TubeRig.constrain_end_with_falloff(self, joints: 'List[str]', anchor: str, falloff: float = 5.0, joint_index: int = -1) -> 'Optional[str]'` — Constrains a joint in the chain to an anchor and applies distance-based skin weight falloff.
- **[`class RigModeConfig`](mayatk/mayatk/rig_utils/tube_rig.py#L1879)** — Defines a rig mode's strategy and available options.
- **[`class TubeRigSlots`](mayatk/mayatk/rig_utils/tube_rig.py#L1956)**
  - `TubeRigSlots.header_init(self, widget)` — Configure header help text.
  - `TubeRigSlots.apply_mode(self, index: int)` — Apply mode values and constraints to UI widgets.
  - `TubeRigSlots.get_mode(self) -> RigModeConfig` — Get the current rig mode config.
  - `TubeRigSlots.get_strategy(self) -> str` — Get the current strategy from the mode combobox.
  - `TubeRigSlots.get_tube_rig(self, obj)` — Get the tube rig instance for the given object, its parent, or mesh ancestor.
  - `TubeRigSlots.create_joints_from_tube(self, obj)` — Creates a joint chain from a tube mesh.
  - `TubeRigSlots.create_rig_from_joints(self, obj, joints)` — Creates a tube rig from an existing joint chain.
  - `TubeRigSlots.b000(self)` — Create Tube Rig (Full Pipeline).
  - `TubeRigSlots.b001(self)` — Create Joints from Tube.
  - `TubeRigSlots.b002(self)` — Create IK / Controls (Preset Dependent).
  - `TubeRigSlots.b003(self)` — Macros: Bind Joint Chain to Tube.
  - `TubeRigSlots.b004(self)` — Macros: Constrain Both Ends of Hose to Anchors.

<a id="rig_utils--wheel_rig"></a>
### `rig_utils/wheel_rig.py`

- **[`class WheelRig(ptk.LoggingMixin)`](mayatk/mayatk/rig_utils/wheel_rig.py#L23)** — Handles basic wheel rigging by linking rotation to linear control movement.
  - `WheelRig.rig_name(self) -> str` *(property)*
  - `WheelRig.rig_name(self, name: str)`
  - `WheelRig.get_expressions(self, filter_by_rig: bool = False) -> List[object]` — Return all expression nodes connected to the control.
  - `WheelRig.delete_expressions(self, filter_by_rig: bool = True) -> None` — Delete expression nodes associated with this rig.
  - `WheelRig.rig_rotation(self, movement_axis: str = 'translateZ', rotation_axis: Optional[str] = None, wheel_height: float = 1.0, wheels: Optional[List['object']] = None, use_world_space: bool = False) -> None` — Rig wheels to rotate based on control movement.
- **[`class WheelRigSlots`](mayatk/mayatk/rig_utils/wheel_rig.py#L297)**
  - `WheelRigSlots.header_init(self, widget)` — Configure header menu with mode toggle and instructions.
  - `WheelRigSlots.rig_name(self) -> str` *(property)* — Get the rig name from the text box.
  - `WheelRigSlots.rig_name(self, name: str)`
  - `WheelRigSlots.movement_axis(self) -> str` *(property)* — Get the movement axis from the combo box.
  - `WheelRigSlots.rotation_axis(self) -> Optional[str]` *(property)* — Get the rotation axis that corresponds to the selected movement axis.
  - `WheelRigSlots.resolve_selection(self) -> Tuple['object', List['object']]` — Resolve the current selection into control (driver) and wheels.
  - `WheelRigSlots.set_wheel_height(self)` — Get the wheel height from the selected object's bounding box.
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
  - `UiUtils.main_progress_bar(size, name='progressBar#', step_amount=1)` *(static)* — # add esc key pressed return False
  - `UiUtils.list_ui_objects()` *(static)* — List all UI objects.
  - `UiUtils.clear_scrollfield_reporters()` *(static)* — Clears the contents of all cmdScrollFieldReporter UI objects in the current Maya session.
  - `UiUtils.reveal_in_outliner(objects)` *(static)* — Reveal and select objects in the Outliner panel.
  - `UiUtils.dispatch_log_link(url, logger=None) -> bool` *(static)* — Handle ``action://`` links emitted by ``log_link()`` in a QTextBrowser.

<a id="ui_utils--calculator"></a>
### `ui_utils/calculator.py`

- **[`class CalculatorController`](mayatk/mayatk/ui_utils/calculator.py#L13)**
  - `CalculatorController.calculate(expression)` *(static)*
  - `CalculatorController.get_fps_value()` *(static)*
  - `CalculatorController.get_current_time()` *(static)*
  - `CalculatorController.frames_to_sec(cls, frames)` *(class)*
  - `CalculatorController.sec_to_frames(cls, seconds)` *(class)*
  - `CalculatorController.convert_unit(value, from_unit, to_unit)` *(static)*
- **[`class CalculatorSlots`](mayatk/mayatk/ui_utils/calculator.py#L114)**
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

Maya hotkey collision checker for the uitk HotkeyEditor.

- [`parse_qt_sequence(sequence: str) -> Optional[dict]`](mayatk/mayatk/ui_utils/hotkey_collisions.py#L41) — Convert a Qt key sequence string to ``cmds.hotkey`` query kwargs.
- [`maya_collision_checker(sequence, scope, ui_name, method_name)`](mayatk/mayatk/ui_utils/hotkey_collisions.py#L170) — Check a proposed binding against Maya's active hotkey set.

<a id="ui_utils--maya_bridge_slots"></a>
### `ui_utils/maya_bridge_slots.py`

Maya-flavored :class:`BridgeSlotsBase` -- adds Maya-side defaults.

- **[`class MayaBridgeSlotsBase(BridgeSlotsBase)`](mayatk/mayatk/ui_utils/maya_bridge_slots.py#L22)** — Adds a Maya-flavored ``default_output_dir`` to :class:`BridgeSlotsBase`.
  - `MayaBridgeSlotsBase.default_output_dir(self) -> str` — Scene-dir then workspace fallback for an empty Output Dir field.

<a id="ui_utils--maya_native_menus"></a>
### `ui_utils/maya_native_menus.py`

- **[`class PersistentMenu(QtWidgets.QMenu)`](mayatk/mayatk/ui_utils/maya_native_menus.py#L14)** — A QMenu that ignores attempts to hide it (e.g.
  - `PersistentMenu.setVisible(self, visible)`
- **[`class EmbeddedMenuWidget(QtWidgets.QWidget)`](mayatk/mayatk/ui_utils/maya_native_menus.py#L23)** — Embeds a Maya QMenu into a sizeable widget that fits content exactly.
  - `EmbeddedMenuWidget.init_ui(self)`
  - `EmbeddedMenuWidget.content_size(self)` — Exact size needed for header + populated menu, no dead space.
  - `EmbeddedMenuWidget.sizeHint(self)`
  - `EmbeddedMenuWidget.minimumSizeHint(self)`
  - `EmbeddedMenuWidget.resizeEvent(self, event)`
  - `EmbeddedMenuWidget.showEvent(self, event)`
  - `EmbeddedMenuWidget.fit_to_window(self)` — Resize and lock the parent window to exact content size.
- **[`class MayaNativeMenus(ptk.LoggingMixin)`](mayatk/mayatk/ui_utils/maya_native_menus.py#L204)** — Handles Maya's menu retrieval and embedding into UI components.
  - `MayaNativeMenus.get_menu(self, menu_key: str) -> Optional[QtWidgets.QWidget]` — Retrieve a Maya menu, populated synchronously, and return its wrapper.
  - `MayaNativeMenus.display_menu(self, menu_key: str)` — Displays the specified Maya menu in a standalone window.

<a id="ui_utils--maya_ui_handler"></a>
### `ui_utils/maya_ui_handler.py`

- **[`class MayaUiHandler(UiHandler)`](mayatk/mayatk/ui_utils/maya_ui_handler.py#L18)** — UI Handler for Maya applications.
  - `MayaUiHandler.instance(cls, switchboard: Switchboard = None, **kwargs) -> 'MayaUiHandler'` *(class)* — Return the MayaUiHandler singleton, bootstrapping if needed.
  - `MayaUiHandler.get(self, name: str, reload: bool = False, **kwargs) -> 'QtWidgets.QMainWindow'` — Retrieve a UI, checking Maya menus first.
  - `MayaUiHandler.apply_styles(self, ui, style=None)` — Override to give mayatk-sourced UIs a hide button instead of pin.

<a id="ui_utils--node_icons"></a>
### `ui_utils/node_icons.py`

Reusable helper for resolving Maya node icons at runtime.

- **[`class NodeIcons`](mayatk/mayatk/ui_utils/node_icons.py#L23)** — Resolve Maya node type icons as Qt QIcons.
  - `NodeIcons.icon_name_for_type(node_type: str) -> str` *(static)* — Return the Maya resource icon filename for a given node type.
  - `NodeIcons.icon_name_for_node(node_name: str) -> Optional[str]` *(static)* — Return the icon filename for a specific node in the scene.
  - `NodeIcons.get_icon(node_name: str, size: int = 20)` *(static)* — Return a ``QIcon`` for a Maya node, or ``None`` if unavailable.
  - `NodeIcons.get_pixmap(node_name: str, size: int = 16)` *(static)* — Return a ``QPixmap`` for a Maya node, scaled to *size*.

<a id="uv_utils--_uv_utils"></a>
### `uv_utils/_uv_utils.py`

- **[`class UvUtils(ptk.HelpMixin)`](mayatk/mayatk/uv_utils/_uv_utils.py#L22)**
  - `UvUtils.calculate_uv_padding(map_size: int, normalize: bool = False, factor: int = 256) -> float` *(static)* — Calculate the UV padding for a given map size to ensure consistent texture padding across different…
  - `UvUtils.orient_shells(objects)` *(static)* — Rotate UV shells to run parallel with the most adjacent U or V axis of their bounding box.
  - `UvUtils.move_to_uv_space(objects, u, v, relative=True)` *(static)* — Move objects to the given u and v coordinates.
  - `UvUtils.mirror_uvs(cls, objects, axis: str = 'u', pivot: tuple | None = None, per_shell: bool = True, preserve_position: bool = True)` *(class)* — Mirror UVs across U or V.
  - `UvUtils.flip_uvs(cls, objects, axis: str = 'u', pivot: tuple | None = None, per_shell: bool = True, preserve_position: bool = True)` *(class)* — Backward-compatible alias for :meth:`mirror_uvs`.
  - `UvUtils.get_uv_shell_sets(objects=None, returned_type='shell')` *(static)* — Get UV shells and their corresponding sets of faces.
  - `UvUtils.get_uv_shell_border_edges(objects)` *(static)* — Get the edges that make up any UV islands of the given objects.
  - `UvUtils.get_texel_density(objects, map_size)` *(static)* — Calculate the texel density for the given objects' faces.
  - `UvUtils.set_texel_density(cls, objects=None, density=1.0, map_size=4096)` *(class)* — Set the texel density for the given objects.
  - `UvUtils.snapshot_uv_sets(objects: Sequence[Union[str, object]], prefix: str = '_uv_snap') -> List[UvSnapshot]` *(static)* — Copy each object's active UV set into a uniquely-named backup set.
  - `UvUtils.restore_uv_snapshot(snapshots: Sequence[UvSnapshot]) -> None` *(static)* — Restore UVs captured by ``snapshot_uv_sets``.
  - `UvUtils.discard_uv_snapshot(snapshots: Sequence[UvSnapshot]) -> None` *(static)* — Delete the snapshot UV sets without restoring them.
  - `UvUtils.transfer_uvs(source: Union[str, object, List[Union[str, object]]], target: Union[str, object, List[Union[str, object]]], tolerance: float = 0.1) -> None` *(static)* — Transfers UVs from source meshes to target meshes based on geometric similarity.
  - `UvUtils.reorder_uv_sets(obj: str, new_order: list[str]) -> None` *(static)* — Reorder UV sets of the given object to match the specified new order.
  - `UvUtils.remove_empty_uv_sets(objects, quiet: bool = False) -> None` *(static)* — Remove empty UV sets from the given objects.

<a id="uv_utils--rizom_bridge--_rizom_bridge"></a>
### `uv_utils/rizom_bridge/_rizom_bridge.py`

- **[`class RizomUVBridge(ptk.LoggingMixin)`](mayatk/mayatk/uv_utils/rizom_bridge/_rizom_bridge.py#L31)**
  - `RizomUVBridge.rizom_path(self)` *(property)* — Resolve the RizomUV executable path.
  - `RizomUVBridge.rizom_path(self, value)` — Set the path to the RizomUV executable (bypasses auto-discovery).
  - `RizomUVBridge.rizom_version(self) -> 'tuple[int, ...]'` *(property)* — Parse the Rizom version from the install directory name.
  - `RizomUVBridge.export_path(self)` *(property)* — Lazy initialization of the export path.
  - `RizomUVBridge.export_path(self, value)`
  - `RizomUVBridge.script_path(self)` *(property)* — Get the path to the UV script file as a POSIX string.
  - `RizomUVBridge.script_path(self, value)` — Set the UV script, loading from a file if a path is provided, or saving the content to a file.
  - `RizomUVBridge.process_with_rizomuv(self, objects, uv_script=None, preset=None, params=None)` — Run the full export -> RizomUV -> re-import workflow.
  - `RizomUVBridge.send_to_rizomuv(self, objects, params=None)` — Export *objects* and open them in a fresh RizomUV session.

<a id="uv_utils--rizom_bridge--parameters"></a>
### `uv_utils/rizom_bridge/parameters.py`

Registry of user-tunable RizomUV parameters exposed to the bridge UI.

- [`referenced_keys(script_text: str) -> 'set[str]'`](mayatk/mayatk/uv_utils/rizom_bridge/parameters.py#L296) — Registered keys present in *script_text* (delegates to uitk.bridge).
- [`defaults() -> 'dict[str, Any]'`](mayatk/mayatk/uv_utils/rizom_bridge/parameters.py#L301) — Return ``{key: default}`` for every registered parameter.
- [`render_context(values: 'dict[str, Any]') -> 'dict[str, str]'`](mayatk/mayatk/uv_utils/rizom_bridge/parameters.py#L306) — Format *values* for ``StrUtils.replace_delimited`` using Lua literals.
- [`strip_unsupported(script_text: str, version: 'tuple[int, ...]') -> str`](mayatk/mayatk/uv_utils/rizom_bridge/parameters.py#L351) — Drop every line that references a placeholder requiring a newer Rizom.

<a id="uv_utils--rizom_bridge--rizom_bridge_slots"></a>
### `uv_utils/rizom_bridge/rizom_bridge_slots.py`

Slots for the RizomUV bridge panel.

- **[`class RizomBridgeSlots(MayaBridgeSlotsBase)`](mayatk/mayatk/uv_utils/rizom_bridge/rizom_bridge_slots.py#L80)** — Slots wired to ``rizom_bridge.ui`` via :class:`MayaBridgeSlotsBase`.
  - `RizomBridgeSlots.params_module(self)` *(property)*
  - `RizomBridgeSlots.template_dir(self) -> Path` *(property)*
  - `RizomBridgeSlots.make_bridge(self) -> RizomUVBridge`
  - `RizomBridgeSlots.list_template_modes(self)` — Return ``[(stem, ""), ...]`` for every bundled ``.lua`` script.
  - `RizomBridgeSlots.header_init(self, widget)` — Configure header menu with Rizom-specific utilities.
  - `RizomBridgeSlots.b000(self)` — Run the chosen preset: round-trip, or one-way send when ``send`` is picked.
  - `RizomBridgeSlots.open_uv_editor(self)` — Open Maya's UV Editor (TextureViewWindow).

<a id="xform_utils--_xform_utils"></a>
### `xform_utils/_xform_utils.py`

- [`get_translation(node, world: bool = False)`](mayatk/mayatk/xform_utils/_xform_utils.py#L28) — Translation as ``om.MVector``.
- [`get_object_matrix(node, world: bool = False)`](mayatk/mayatk/xform_utils/_xform_utils.py#L39) — Local or world matrix as ``om.MMatrix``.
- [`set_object_matrix(node, value, world: bool = False) -> None`](mayatk/mayatk/xform_utils/_xform_utils.py#L46) — Apply *value* to *node*'s local or world transformation matrix.
- **[`class XformUtilsInternals`](mayatk/mayatk/xform_utils/_xform_utils.py#L327)** — Internal helper methods for XformUtils.
- **[`class XformUtils(XformUtilsInternals, ptk.HelpMixin)`](mayatk/mayatk/xform_utils/_xform_utils.py#L377)** — Transform utilities for Maya objects.
  - `XformUtils.convert_axis(value, invert=False, ortho=False, to_integer=False)` *(static)* — Converts between axis representations and optionally inverts the axis or returns an orthogonal axis.
  - `XformUtils.move_to(cls, source, target, group_move=False)` *(class)* — Move source object(s) to align with the target object(s).
  - `XformUtils.drop_to_grid(objects, align='Mid', origin=False, center_pivot=False, freeze_transforms=False)` *(static)* — Align objects to Y origin on the grid using a helper plane.
  - `XformUtils.match_scale(cls, a, b, scale=True, average=False)` *(class)* — Scale each of the given objects in 'a' to the combined bounding box of the objects in 'b'.
  - `XformUtils.scale_connected_edges(objects, scale_factor=1.1) -> None` *(static)* — Scales each set of connected edges separately, either uniformly or non-uniformly.
  - `XformUtils.store_transforms(objects, prefix='original', accumulate=True, traverse=False, channels=None)` *(static)* — Capture the current local TRS as a cumulative per-channel bake history.
  - `XformUtils.freeze_transforms(cls, objects, center_pivot=0, force=True, delete_history=False, freeze_children=False, unlock_children=True, connection_strategy='preserve', from_channel_box=False, **kwargs)` *(class)* — Freezes transformations on the given objects.
  - `XformUtils.freeze_to_opm(objects, reset_rotate_axis: bool = False, reset_joint_orient: bool = False) -> None` *(static)* — Freeze transforms into offsetParentMatrix while preserving pivot placement.
  - `XformUtils.unfreeze_to_parent(objects, traverse: bool = False, preserve_root: bool = True) -> List[str]` *(static)* — Push a child transform's local matrix up into its parent and zero the child.
  - `XformUtils.restore_transforms(objects, prefix='original', delete_attrs=True, channels=None)` *(static)* — Compose stored bake history with current local TRS, per channel.
  - `XformUtils.clear_stored_transforms(objects, prefix='original') -> List[str]` *(static)* — Delete the per-channel bake attrs without restoring.
  - `XformUtils.has_stored_transforms(objects, prefix='original')` *(static)* — Check if objects have any stored bake history.
  - `XformUtils.reset_translation(cls, objects)` *(class)* — Reset the translation transformations on the given object(s).
  - `XformUtils.set_translation_to_pivot(node)` *(static)* — Set an object's translation value from its pivot location.
  - `XformUtils.get_manip_pivot_matrix(obj, **kwargs)` *(static)* — Return the object's transform matrix using xform, allowing kwargs override.
  - `XformUtils.set_manip_pivot_matrix(obj, matrix, **kwargs) -> None` *(static)* — Apply a transformation matrix's position and orientation to the manip pivot.
  - `XformUtils.get_pivot_options(cls)` *(class)* — Returns a list of supported pivot options.
  - `XformUtils.clear_manip_cache(cls)` *(class)* — Clears the cached manipulator pivot data.
  - `XformUtils.snapshot_manip_pivot(cls, node)` *(class)* — Snapshot the current manipulator pivot state for the given node into the cache.
  - `XformUtils.get_operation_axis_matrix(cls, node, pivot: str)` *(class)* — Determines the pivot matrix (orientation + position) for transformations.
  - `XformUtils.get_operation_axis_pos(cls, node, pivot, axis_index=None)` *(class)* — Determines the pivot position for mirroring/cutting along a specified axis or all axes.
  - `XformUtils.align_pivot_to_selection(align_from=None, align_to=None, translate=True)` *(static)* — Align one object's pivot point to another using 3-point alignment.
  - `XformUtils.reset_pivot_transforms(objects=None) -> None` *(static)* — Reset Pivot Transforms for the specified objects or selected objects.
  - `XformUtils.world_align_pivot(objects=None, pivot_type: str = 'object', mode: str = 'set')` *(static)* — Get or set a world-aligned pivot for the specified objects.
  - `XformUtils.bake_pivot(objects, position=False, orientation=False)` *(static)* — Bake the pivot orientation and position of the given object(s).
  - `XformUtils.transfer_pivot(objects, translate: bool = False, rotate: bool = False, scale: bool = False, bake: bool = False, world_space: bool = True, select_targets_after_transfer: bool = False)` *(static)* — Transfer the pivot orientation from the first given object to the remaining given objects.
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

<a id="xform_utils--matrices"></a>
### `xform_utils/matrices.py`

Matrix utilities for Maya rigging and animation.

- [`get_matrix(node: str, attr: str = 'worldMatrix', index: int = 0) -> List[float]`](mayatk/mayatk/xform_utils/matrices.py#L67) — Return a 16-element flat list for a matrix attribute on *node*.
- [`set_matrix(node: str, attr: str, value, index: int = 0) -> None`](mayatk/mayatk/xform_utils/matrices.py#L83) — Set a matrix attribute on *node* from an MMatrix or 16-element iterable.
- **[`class MatricesError(RuntimeError)`](mayatk/mayatk/xform_utils/matrices.py#L150)** — Base exception for matrix utility operations.
- **[`class Matrices(_MatrixMath, _DagTransforms, _NodeBuilders, ptk.HelpMixin)`](mayatk/mayatk/xform_utils/matrices.py#L832)** — Matrix utilities for Maya rigging and animation.

<a id="xform_utils--pivot_watcher"></a>
### `xform_utils/pivot_watcher.py`

Real-time pivot-change notifier built on :class:`ScriptJobManager`.

- **[`class PivotWatcher`](mayatk/mayatk/xform_utils/pivot_watcher.py#L100)** — Fire *callback* on intentional manipulator-pivot drags.
  - `PivotWatcher.owner(self) -> Any` *(property)*
  - `PivotWatcher.started(self) -> bool` *(property)*
  - `PivotWatcher.start(self) -> None` — Subscribe to the watched events (idempotent).
  - `PivotWatcher.stop(self) -> None` — Unsubscribe from all watched events (idempotent).
  - `PivotWatcher.attach_widget(self, widget) -> None` — Auto-:meth:`stop` when *widget* emits ``destroyed``.
