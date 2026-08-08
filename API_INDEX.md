# mayatk — API Index

_Auto-generated. Do not edit by hand. Compact symbol index — grep this for a name; for full signatures/docs, slice [API_REGISTRY.md](API_REGISTRY.md) (never Read it whole)._

_Generated: 2026-08-08_

### `anim_utils/_anim_utils.py`
- `class AnimUtils(_AnimUtilsInternal, ptk.HelpMixin)`
  - methods: bake, objects_to_curves, get_anim_curves, get_static_curves, get_redundant_flat_keys, simplify_curve, repair_corrupted_curves, optimize_keys, get_keyframe_times, get_driver_animation_range, get_tangent_info, set_tangent_info, step_keys, set_current_frame, move_keys_to_frame, set_keys_for_attributes, filter_objects_with_keys, scene_has_animation, adjust_key_spacing, add_intermediate_keys, remove_intermediate_keys, invert_keys, align_selected_keyframes, set_visibility_keys, snap_keys_to_frames, transfer_keyframes, parse_time_range, delete_keys, select_keys, get_frame_ranges, get_tied_keyframes, tie_keyframes, untie_keyframes, create_animation_layer, get_animation_layers, copy_keys, paste_keys, delete_animation_layer, fit_playback_range

### `anim_utils/blendshape_animator/_blendshape_animator.py` — Main workflow facade for blendShape morph-animation creation, editing, and export.
- `class BlendshapeAnimator(ptk.LoggingMixin)`
  - methods: create, edit_weight_based, edit_frame_based, edit_apply_tweens, basic_workflow, apply_all_edits, finalize_for_export, from_existing, recover_animation, diagnose_topology_issues, cleanup_topology_mismatches, remove_target_for_export, recover_setup

### `anim_utils/blendshape_animator/applicator.py` — Applies tween mesh edits back to blendShape in-between targets.
- `class ApplyStatus(Enum)`
- `class Applicator(ptk.LoggingMixin)`
  - methods: validate_topology, apply_tweens

### `anim_utils/blendshape_animator/blendshape_animator_slots.py` — Switchboard slots controller for blendshape_animator.ui.
- `class BlendshapeAnimatorSlots(BlendshapeAnimator)`
  - methods: header_init, b000_init, b000, cmb000_init, le000_init, le001_init, b001_init, b001, b003, b004_init, b004, b005, b006_init, b006, b007, b008_init, b008

### `anim_utils/blendshape_animator/creator.py` — Creates in-between target meshes for custom blendShape animation curves.
- `class Creator(ptk.LoggingMixin)`
  - methods: create_weight_based_tweens, create_frame_based_tween, tag_tween_mesh, get_existing_weights, find_nearby_weight

### `anim_utils/blendshape_animator/helpers.py` — Shared helpers internal to the blendshape_animator subpackage.
- `class BlendshapeHelpers`
  - methods: list_history

### `anim_utils/blendshape_animator/keyframes.py` — Core blendShape keyframe animation operations.
- `class Keyframes(ptk.LoggingMixin)`
  - methods: create_keyframes, test_morph, get_frame_range

### `anim_utils/blendshape_animator/recovery.py` — Recovery utilities for corrupted blendShape setups.
- `class Recovery(ptk.LoggingMixin)`
  - methods: fix_corrupted_animation, recover_with_targets

### `anim_utils/blendshape_animator/target.py` — Tween mesh wrappers and registry for blendShape in-between targets.
- `class Target`
  - methods: weight, blendshape_name, base_mesh_name, target_frame, update_references
- `class Targets(ptk.LoggingMixin)`
  - methods: find_all_targets, group_by_weight, update_all_references

### `anim_utils/blendshape_animator/validator.py` — Mesh and blendShape validation for blendShape animation setup.
- `class Validator(ptk.LoggingMixin)`
  - methods: validate_meshes, validate_blendshape

### `anim_utils/playblast_exporter.py` — Playblast capture, encoding, and preview-render exports for Maya.
- `class ExportTarget`
- `class CaptureResult`
  - methods: pattern
- `class ExportResult`
  - methods: ok
- `class PlayblastExporter(ptk.LoggingMixin)`
  - methods: available_targets, scene_name, scene_fps, resolve_frame_range, resolve_sound_node, capture_sequence, capture_still, capture_movie, encode_sequence, export, render_with_arnold

### `anim_utils/scale_keys.py` — Dedicated scale-keys module to keep AnimUtils lean and testable.
- `class ScaleKeys`
  - methods: execute, scale_keys

### `anim_utils/segment_keys.py`
- `class SegmentKeysInfo`
  - methods: get_time_ranges, print_time_ranges, format_time_ranges_text, format_time_ranges_html
- `class SegmentKeys(SegmentKeysInfo)`
  - methods: collect_segments, get_scene_info, format_scene_info_text, format_scene_info_html, print_scene_info, group_segments, merge_groups_sharing_curves, shift_curves, execute_stagger

### `anim_utils/shots/_detection.py` — Shot-region detection — Maya scene acquisition over the pure engine math.
- `class Detection(_DetectionInternal)`
  - methods: resolve_to_transform, detect_shot_regions, regions_from_selected_keys

### `anim_utils/shots/_shot_apply.py` — Commit resolved :class:`MovePlan`\ s to the Maya scene.
- `class ShotApply(_ShotApplyInternal)`
  - methods: apply

### `anim_utils/shots/_shots.py` — Maya shot-store adapter — the DCC layer over ``pythontk``'s shots engine.
- `class MayaScenePersistence`
  - methods: save, load, remove_callbacks
- `class ShotStore(ptk.ShotStore, _ShotStoreInternal)`
  - methods: active, has_animation, detect_regions, assess, publish_export_view

### `anim_utils/shots/shot_manifest/_shot_manifest.py` — Maya Shot Manifest adapter — the DCC layer over pythontk's manifest engine.
- `class ShotManifest(_EngineShotManifest, _ShotManifestInternal)`
  - methods: apply_behaviors, rewire_audio, from_csv

### `anim_utils/shots/shot_manifest/behaviors/_behaviors.py` — Behaviors — Maya appliers over the engine's pure keying-recipe core.
- `class Behaviors(_BehaviorsInternal)`
  - methods: apply_behavior, verify_behavior, apply_audio_clip, compute_duration, apply_to_shots

### `anim_utils/shots/shot_manifest/manifest_data.py` — Constants, column layout, and pure helper functions for the Shot Manifest UI.
- `class ManifestData`
  - methods: fmt_behavior, format_behavior_html, try_load_maya_icons

### `anim_utils/shots/shot_manifest/range_resolver.py` — Range resolution for the Shot Manifest build pipeline (Maya-bound facade).
- `class RangeResolver`
  - methods: resolve_ranges

### `anim_utils/shots/shot_manifest/shot_manifest_slots.py` — Switchboard slots for the Shot Manifest UI.
- `class ShotManifestController(ManifestTableMixin, ptk.LoggingMixin)`
  - methods: detect, remove_callbacks, build, assess
- `class ShotManifestSlots(ptk.LoggingMixin)`
  - methods: header_init, btn_expand_missing, btn_expand_extra, btn_settings, b002, b003

### `anim_utils/shots/shot_manifest/table_presenter.py` — Tree-widget presentation mixin for the Shot Manifest controller.
- `class ManifestTableMixin`
  - methods: expand_missing, expand_extra

### `anim_utils/shots/shot_sequencer/_shot_sequencer.py` — Shot Sequencer — manages per-shot animation with ripple editing.
- `class ShotSequencer`
  - methods: shots, hidden_objects, markers, is_object_hidden, set_object_hidden, sorted_shots, shot_by_id, shot_by_name, define_shot, reconcile_all_shots, collect_object_segments, collect_shot_sequences, move_sequences_to_shot, fit_shot_to_content, trim_shot_to_content, extend_shot_to_fit, detect_shots, detect_next_shot, move_object_keys, move_stepped_keys, move_object_in_shot, scale_object_keys, move_shot, slide_shot, ripple_downstream, ripple_upstream, expand_shot, resize_object, set_shot_duration, resize_shot, set_shot_start, reorder_shots, move_shot_to_position, respace, apply_gap, to_dict, from_dict

### `anim_utils/shots/shot_sequencer/clip_motion.py` — Clip motion, resize, and key-scaling logic for the shot sequencer.
- `curves_for_attr(obj_name: str, attr_name: str) -> list`
- `scale_attribute_keys(obj_name: str, attr_name: str, old_start: float, old_end: float, new_start: float, new_end: float) -> None`
- `class ClipMotionMixin`
  - methods: on_clip_resized, on_clip_moved, on_clips_batch_moved, on_keys_moved, on_keys_deleted

### `anim_utils/shots/shot_sequencer/gap_manager.py` — Gap and range-highlight handlers for the shot sequencer controller.
- `class GapManagerMixin`
  - methods: on_range_highlight_changed, on_gap_resized, on_gap_left_resized, on_gap_moved, on_gap_lock_changed, on_gap_lock_all, on_gap_unlock_all

### `anim_utils/shots/shot_sequencer/marker_manager.py` — Marker persistence for the shot sequencer controller.
- `class MarkerManagerMixin(_MarkerManagerMixinInternal)`
  - methods: on_marker_added, on_marker_moved, on_marker_changed, on_marker_removed

### `anim_utils/shots/shot_sequencer/segment_collector.py` — Segment collection and attribute extraction for the shot sequencer.
- `class SegmentCollector`
  - methods: collect_segments, active_object_set, extract_attributes, build_curve_preview

### `anim_utils/shots/shot_sequencer/shot_nav.py` — Shot navigation and combobox synchronization.
- `class ShotNavMixin`
  - methods: select_shot, on_shot_block_clicked

### `anim_utils/shots/shot_sequencer/shot_sequencer_slots.py` — Switchboard slots for the Shot Sequencer UI.
- `class ShotSequencerController(GapManagerMixin, ClipMotionMixin, ShotNavMixin, MarkerManagerMixin, ptk.LoggingMixin)`
  - methods: sequencer, remove_callbacks, on_zone_context_menu, active_shot_id, on_undo, on_redo, on_clip_menu, on_gap_menu, refresh, hide_track, show_track, delete_track, on_selection_changed, on_track_selected, on_clip_locked, on_track_menu, on_header_menu, on_key_selection_changed, on_clip_renamed, on_playhead_moved
- `class ShotEditDialog`
  - methods: show
- `class ShotSequencerSlots(ptk.LoggingMixin)`
  - methods: header_init, btn_colors, cmb_shot, spn_snap, btn_shortcuts, btn_shot_settings

### `anim_utils/shots/shots_slots.py` — Switchboard slots for the Shots settings UI.
- `class ShotsController(ptk.LoggingMixin)`
  - methods: remove_callbacks, refresh_state, on_detection_changed, on_detection_mode_changed, on_initial_length_changed, on_snap_whole_frames_changed, on_fit_mode_changed, on_gap_changed, on_shot_selected, on_shot_name_changed, on_shot_start_changed, on_shot_end_changed, on_shot_desc_changed, on_delete_shot, on_delete_all_shots, on_move_shot, on_trim_empty, on_trim_all_shots
- `class ShotsSlots(ptk.LoggingMixin)`
  - methods: header_init, spn_detection, cmb_detection_mode, spn_initial_length, cmb_fit_mode, chk_snap_whole_frames, cmb_shot_select, txt_shot_name, spn_shot_start, spn_shot_end, txt_shot_desc, b000, btn_delete_all_shots, btn_move_shot, btn_apply_gap, btn_trim_empty, btn_trim_all_shots

### `anim_utils/smart_bake/_smart_bake.py` — Smart bake module for intelligent pre-bake animation processing.
- `class BakeAnalysis`
  - methods: requires_bake, all_driven_channels
- `class BakeResult`
  - methods: baked_count, success
- `class SmartBake`
  - methods: analyze, get_time_range, bake, execute, list_sessions, restore, session, run

### `anim_utils/smart_bake/bake_session.py` — Persistence and restore engine for SmartBake's nondestructive manifest.
- `class BakeSessionStore(_BakeSessionStoreInternal)`
  - methods: load, save, push, peek, pop, list_ids, new_session_id, node_ref, resolve_ref, plug_ref, resolve_plug, stash_curve, unstash_curve, discard_stash, collect_upstream_curves, snapshot_connections, restore_session
- `class RestoreResult`

### `anim_utils/smart_bake/smart_bake_slots.py` — Slots for the Smart Bake tool panel (smart_bake.ui).
- `class SmartBakeSlots(ptk.LoggingMixin, ptk.HelpMixin)`
  - methods: cmb_scope_init, cmb_backup_init, header_init, reset_defaults, b000, b001

### `anim_utils/stagger_keys.py` — Dedicated stagger-keys module to keep AnimUtils lean and testable.
- `class StaggerKeys`
  - methods: stagger_keys

### `audio_utils/_audio_utils.py` — Unified audio system for Maya scenes.
- `class TrackEvent`
- `class AudioUtils(ptk.HelpMixin)`
  - methods: get_snap_frames, set_snap_frames, validate_track_id, normalize_track_id, attr_for, track_id_from_attr, find_carriers, list_track_attrs, load_file_map, set_path, get_path, remove_path, get_fps, cached_waveform, clear_waveform_cache, audio_duration_frames, ensure_track_attr, has_track, is_registered, list_tracks, read_keys, pair_on_off_events, read_events, write_key, remove_key, clear_keys, shift_keys_in_range, tracks_on_at_frame, bake_events, delete_track, rename_track, show_track_attrs, hide_track_attrs, sync, find_dg_node_for_track, is_managed_dg, batch, detect_legacy, migrate_legacy_triggers

### `audio_utils/audio_clips/_audio_clips.py` — Scene-wide audio event manager — thin facade over ``audio_utils``.
- `class AudioClips(ptk.LoggingMixin)`
  - methods: sync, rebuild_composite, remove, load_tracks, prepare_for_export, enable_auto_export, disable_auto_export, list_nodes, set_active

### `audio_utils/audio_clips/audio_clips_slots.py` — Switchboard slots for the Audio Clips UI.
- `class AudioClipsSlots(ExportMixin, CallbacksMixin)`
  - methods: header_init, cmb000_init, cmb000, tb000, tb001_init, tb001, b002, b004, b005, b006

### `audio_utils/audio_clips/callbacks.py` — Maya event lifecycle and hydration for Audio Clips.
- `class CallbacksMixin`
  - methods: remove_callbacks

### `audio_utils/audio_clips/export_ops.py` — Export operations for Audio Clips.
- `class ExportMixin`

### `audio_utils/batch.py` — Batch orchestration — undo chunk + dirty-track buffering.
- `class Batch(_BatchInternal)`
  - methods: batch

### `audio_utils/compositor.py` — Compositor — derives DG audio nodes from keyed track events.
- `class Compositor(_CompositorInternal)`
  - methods: is_managed_dg, find_dg_node_for_track, sync

### `audio_utils/migrate.py` — One-shot migration from legacy single-enum carriers to per-track schema.
- `class Migrate(_MigrateInternal)`
  - methods: detect_legacy, migrate_legacy_triggers

### `audio_utils/nodes.py` — Low-level DG audio node primitives.
- `class Nodes(_NodesInternal)`
  - methods: resolve_playable_path, workspace_sound_dir, create_dg, configure_dg, query_duration

### `audio_utils/segments.py` — Consumer-facing segment discovery for sequencer + manifest.
- `class AudioSegment(_AudioSegmentInternal)`
  - methods: is_audio, collect_all_segments, collect_segments_for_track

### `cam_utils/_cam_utils.py`
- `class CamUtils(ptk.HelpMixin)`
  - methods: group_cameras, toggle_safe_frames, get_current_cam, create_camera_from_view, get_view_state, set_view_state, fit_camera_clipping, adjust_camera_clipping, switch_viewport_camera

### `core_utils/_core_utils.py`
- `class BoundingBox`
  - methods: corners
- `class CoreUtils(ptk.CoreUtils, _CoreUtilsInternal)`
  - methods: undo_chunk, undo_disabled, suspended_refresh, selected, undoable, reparent, wrap_control, confirm_existence, get_mfn_mesh, get_array_type, convert_array_type, get_parameter_mapping, set_parameter_mapping, build_mesh_similarity_mapping, get_mel_globals, reorder_objects, as_strings, short_name, leaf_name, get_bounding_box

### `core_utils/auto_instancer/_auto_instancer.py` — Scene auto-instancer: convert geometrically identical meshes to instances.
- `class InstanceCandidate`
  - methods: transform, exists
- `class InstanceGroup`
- `class AutoInstancer(ptk.LoggingMixin, _AutoInstancerInternal)`
  - methods: default_summary, format_summary, tolerance, scale_tolerance, require_same_material, check_uvs, combine_assemblies, search_radius_mult, verbose, run, find_instance_groups, run_once

### `core_utils/auto_instancer/assembly_reconstructor.py` — Logic for separating and reassembling mesh assemblies.
- `class AssemblyReconstructor`
  - methods: separate_combined_meshes, cleanup_empty_sources, cleanup_empty_assembly_groups, center_transform_on_geometry, canonicalize_transform, canonicalize_leaf_meshes, reassemble_assemblies, combine_reassembled_assemblies

### `core_utils/auto_instancer/geometry_matcher.py` — Geometry analysis and matching logic for AutoInstancer.
- `class ShellInfo`
- `class GeometryMatcher(_GeometryMatcherInternal)`
  - methods: clear_cache, quantize, get_pca_basis, get_mesh_signature, are_meshes_identical, get_hierarchy_signature, are_meshes_identical_with_transform, are_hierarchies_identical, mesh_points, mesh_triangles, mesh_uv_set_names, mesh_get_uvs, mesh_num_uvs, calculate_mesh_volume

### `core_utils/auto_instancer/instancing_strategy.py` — Instancing strategy logic for AutoInstancer.
- `class StrategyType(Enum)`
- `class StrategyConfig`
- `class InstancingStrategy`
  - methods: evaluate

### `core_utils/components.py`
- `class GetComponentsMixin`
  - methods: get_component_type, convert_alias, convert_component_type, get_component_index, convert_int_to_component, filter_components, get_components
- `class Components(GetComponentsMixin, ptk.HelpMixin, _ComponentsInternal)`
  - methods: get_mesh_transforms, get_standoff_distances, map_components_to_objects, get_contiguous_edges, get_contiguous_islands, get_islands, get_border_components, get_furthest_vertices, get_closest_verts, closest_point_probe, get_closest_vertex, get_vertices_within_threshold, adjusted_distance_between_vertices, bridge_connected_edges, get_edge_path, get_shortest_path, get_normal, get_normal_vector, get_normal_angle, get_edges_by_normal_angle, set_edge_hardness, get_faces_with_similar_normals, average_normals, transfer_normals, filter_components_by_connection_count, get_vertex_normal, get_vector_from_components, crease_edges, get_creased_edges, transfer_creased_edges

### `core_utils/diagnostics/animation_diag.py` — Animation-curve diagnostics and optional repair helpers.
- `class AnimCurveDiagnostics`
  - methods: repair_visibility_tangents, repair_corrupted_curves

### `core_utils/diagnostics/audit_records.py` — Scene-audit data contract: profiles, per-asset records, and the SceneReport tree.
- `class AuditProfile`
- `class MeshRecord`
- `class MaterialRecord`
- `class Finding`
- `class FixAction`
- `class BudgetDelta`
  - methods: is_over_budget, summary
- `class AssetRecord`
- `class ParetoEntry`
- `class TextureFile`
- `class MissingTexture`
- `class SharedTexture`
- `class MaterialSplit`
- `class SlotStats`
- `class InstanceStats`
- `class BudgetBuckets`
- `class ComplianceStats`
- `class MissingTextureImpact`
  - methods: is_empty
- `class SummaryStats`
- `class BudgetStats`
- `class TextureStats`
- `class PipelineStats`
- `class OffenderLists`
- `class AnalysisManifest`
- `class SceneReport`
  - methods: to_dict
- `class SceneInfoSection`
  - methods: normalize

### `core_utils/diagnostics/mesh_diag.py` — Mesh diagnostics and repair helpers.
- `class MeshDiagnostics`
  - methods: clean_geometry, get_ngons

### `core_utils/diagnostics/scene_audit.py` — Scene audit engine — game-readiness analysis over meshes, materials, and textures.
- `class SceneAnalyzer(ptk.LoggingMixin)`
  - methods: run_audit, format_audit_text, format_audit_html, analyze, generate_report, print_report

### `core_utils/diagnostics/scene_diag.py` — Scene repair helpers: OCIO / color management, unknown nodes and plugins,
- `class SceneDiagnostics(_SceneDiagnosticsInternal)`
  - methods: fix_ocio, fix_missing_color_spaces, fix_unknown_plugins, remove_xgen_expressions, cleanup_scene, repair_mangled_names

### `core_utils/diagnostics/transform_diag.py` — Transform diagnostics and repair helpers.
- `class TransformDiagnostics(_TransformDiagnosticsInternal)`
  - methods: get_sheared, get_non_orthogonal, fix_non_orthogonal_axes

### `core_utils/diagnostics/uv_diag.py` — UV diagnostics and repair helpers.
- `class UvSetCleanupResult`
- `class UvDiagnostics`
  - methods: find_non_manifold_uvs, repair_non_manifold_uvs, find_lightmap_uv_set, is_bakeable_lightmap, cleanup_uv_sets

### `core_utils/mash.py`
- `class MashNetworkNodes(object)`
  - methods: as_tuple
- `class MashToolkit(object)`
  - methods: ensure_plugin_loaded, create_network, bake_instancer

### `core_utils/preview.py` — Hermetic preview with replay-on-commit (H1 design).
- `class OperationError(Exception)`
- `class CleanupContract`
  - methods: add_file, record_modification, rollback
- `class Preview(_PreviewInternal)`
  - methods: cleanup_all_instances, init_show_hide_behavior, conditionally_enable, conditionally_disable, toggle, validate_operation, enable, refresh, disable, finalize_changes, cleanup, enabled, operated_object_count, get_operated_objects, cleanup_all_previews, apply_result_selection

### `core_utils/script_job_manager.py` — Centralized Maya event subscription manager.
- `class ScriptJobManager`
  - methods: instance, reset, subscribe, add_om_callback, unsubscribe, unsubscribe_all, connect_cleanup, suppress, resume, suppressed, status, print_status, teardown

### `display_utils/_display_utils.py`
- `class DisplayUtils(ptk.HelpMixin)`
  - methods: add_to_isolation, is_templated, set_visibility, get_visible_geometry, add_to_isolation_set, reset_viewport

### `display_utils/color_id.py`
- `class ColorUtils`
  - methods: assign_material, set_color_attribute, get_material_color, get_wireframe_color, get_vertex_color, set_vertex_color, get_color_difference, add_to_color_set, get_color_set_color, remove_from_color_sets
- `class ColorId(ColorUtils)`
  - methods: apply_color, get_objects_by_color, reset_colors, reset_vertex_colors
- `class ColorIdSlots(ColorId)`
  - methods: header_init, selected_objects, selected_button, target_color, b000, b001, b002, b003

### `display_utils/exploded_view.py`
- `class ExplodedView`
  - methods: objects, calculate_repulsive_force_vectorized, arrange_objects, explode, un_explode, toggle_explode, un_explode_all
- `class ExplodedViewSlots(ExplodedView)`
  - methods: header_init, b000, b001, b002, b003

### `edit_utils/_curtain_drape.py` — Procedural draped-cloth (curtain) drape engine — pure geometry, no DCC.
- `class CurtainDrape(_CurtainDrapeInternal)`
  - methods: prepare, grid_points, drape

### `edit_utils/_edit_utils.py`
- `class EditUtils(ptk.HelpMixin, _EditUtilsInternal)`
  - methods: combine_objects, group_objects, ungroup_objects, separate_objects, merge_vertices, merge_vertex_pairs, detach_components, decimate, dissolve_coplanar, get_all_faces_on_axis, cut_along_axis, delete_along_axis, mirror, mirror_instance, separate_mirrored_mesh, get_overlapping_duplicates, find_non_manifold_vertex, split_non_manifold_vertex, get_overlapping_vertices, get_overlapping_faces, get_similar_mesh, get_similar_topo, invert_geometry, invert_components, delete_selected, create_curve_from_edges

### `edit_utils/bevel.py`
- `class Bevel`
  - methods: bevel
- `class BevelSlots`
  - methods: header_init, perform_operation

### `edit_utils/bridge.py`
- `class Bridge`
  - methods: bridge, get_child_curves_from_bridge, cleanup_bridge_curves_and_history
- `class BridgeSlots`
  - methods: header_init, perform_operation

### `edit_utils/curtain.py` — Procedural draped-cloth (curtain) generator for Maya.
- `class Rail(ptk.Polyline)`
  - methods: from_selection, sample_curve
- `class CurtainMesh(CurtainDrape)`
  - methods: create, build
- `class CurtainRig`
  - methods: attach
- `class CurtainSlots(ptk.LoggingMixin)`
  - methods: header_init, cmb000_init, b001, b002, perform_operation

### `edit_utils/cut_on_axis.py`
- `class CutOnAxis`
  - methods: perform_cut_on_axis
- `class CutOnAxisSlots`
  - methods: header_init, toggle_weight_ui, perform_operation

### `edit_utils/duplicate_grid.py`
- `class DuplicateGrid(ptk.LoggingMixin)`
  - methods: duplicate_grid
- `class DuplicateGridSlots(ptk.LoggingMixin)`
  - methods: header_init, b001, perform_operation

### `edit_utils/duplicate_linear.py`
- `class DuplicateLinear`
  - methods: duplicate_linear
- `class DuplicateLinearSlots`
  - methods: header_init, toggle_weight_ui, b001, perform_operation

### `edit_utils/duplicate_radial.py`
- `class DuplicateRadial(ptk.LoggingMixin)`
  - methods: duplicate_radial
- `class DuplicateRadialSlots(ptk.LoggingMixin)`
  - methods: header_init, s015_init, s016_init, b001, perform_operation, regroup_copies

### `edit_utils/dynamic_pipe.py`
- `class DynamicPipe`
  - methods: create_pipe_geometry
- `class DynamicPipeSlots`
  - methods: header_init, b000

### `edit_utils/macros.py`
- `class MacroManager(ptk.HelpMixin)`
  - methods: set_macros, call_with_input, set_macro, list_available_macros, macro_label, macro_category, list_categories, macro_help, get_current_bindings, apply_bindings, clear_hotkey, unset_macro, find_conflicts, qt_sequence_to_maya_key, maya_key_to_qt_sequence, list_presets, load_preset, save_preset, delete_preset, get_active_preset, set_active_preset, apply_saved_macros, editor_categories, get_editor_registry, apply_editor_binding, export_bindings, import_bindings, show_editor
- `class DisplayMacros`
  - methods: m_component_id_display, m_normals_display, m_soft_edge_display, m_toggle_visibility, m_toggle_uv_border_edges, m_back_face_culling, m_isolate_selected, m_cycle_display_state, m_wireframe_toggle, m_grid, m_grid_and_image_planes, m_frame, m_smooth_preview, m_wireframe, m_material_override, m_shading, m_lighting
- `class EditMacros`
  - methods: m_group, m_ungroup, m_combine, m_boolean, m_lock_vertex_normals, m_paste_and_rename, m_multi_component, m_merge_vertices
- `class SelectionMacros`
  - methods: m_object_selection, m_vertex_selection, m_edge_selection, m_face_selection, m_invert_selection, m_toggle_selectability, m_toggle_UV_select_type, m_invert_component_selection
- `class UiMacros`
  - methods: m_toggle_panels
- `class AnimationMacros`
  - methods: m_set_selected_keys, m_unset_selected_keys
- `class Macros(MacroManager, DisplayMacros, EditMacros, SelectionMacros, AnimationMacros, UiMacros)`

### `edit_utils/mesh_graph.py`
- `class Graph`
  - methods: add_node, add_edge, heuristic, find_path, a_star, dijkstra
- `class MeshGraph(Graph)`
  - methods: build_graph, heuristic

### `edit_utils/mirror.py`
- `class MirrorSlots(ptk.LoggingMixin)`
  - methods: header_init, prepare_operation, perform_operation

### `edit_utils/naming/_naming.py`
- `class Naming(ptk.HelpMixin)`
  - methods: rename, generate_unique_name, conform_shape_names, strip_illegal_chars, strip_chars, set_case, suffix_by_type, append_location_based_suffix

### `edit_utils/naming/naming_slots.py`
- `class NamingSlots(Naming, ptk.LoggingMixin)`
  - methods: header_init, valid_suffixes, txt000_init, txt000, txt001_init, txt001, tb000_init, tb000, tb001_init, tb001, tb002_init, tb002, tb003_init, tb003

### `edit_utils/primitives.py` — Primitive creation utilities for Maya.
- `class Primitives`
  - methods: create_default_primitive, create_circle

### `edit_utils/rack_builder.py` — Parametric EIA-310 (19-inch) equipment-rack generator.
- `class EIA310`
  - methods: u_to_mm
- `class OccupantSpec(ptk.SchemaSpec)`
- `class BaySpec(ptk.SchemaSpec)`
- `class RackSpec(ptk.SchemaSpec)`
- `class RackBuilder(ptk.LoggingMixin)`
  - methods: build, from_dict

### `edit_utils/selection.py`
- `class Selection(ptk.LoggingMixin, ptk.HelpMixin)`
  - methods: select_by_type, select_children, select_hierarchy_above, select_hierarchy_below, get_available_selection_types, get_selection_categories

### `edit_utils/snap.py`
- `class Snap(ptk.HelpMixin)`
  - methods: snap_to_closest_vertex, snap_to_surface, snap_to_grid
- `class SnapSlots`
  - methods: header_init, b000_init, b000, b001_init, b001, b002_init, b002

### `env_utils/_env_utils.py`
- `class EnvUtils(ptk.HelpMixin)`
  - methods: get_env_info, saved_scene_path, default_artifact_dir, append_maya_paths, load_plugin, vray_plugin, get_recent_files, get_recent_projects, find_autosave_directories, get_recent_autosave, find_workspaces, get_workspace_scenes, find_workspace_using_path, current_workspace, set_current_workspace, workspace_root, scenes_dir, source_images_dir, list_workspace_templates, workspace_template_rules, save_workspace_template, delete_workspace_template, create_workspace, promote_workspace, reference_scene, remove_reference, is_referenced, get_reference_nodes, list_references, export_scene_as_fbx, export_scene_as_obj, sanitize_namespace, resolve_file_path_in_workspaces, get_workspace_file_cache, matches_autosave_pattern, save_scene_backup, find_original_for_autosave, save_autosave_to_original

### `env_utils/blender_bridge/_blender_bridge.py` — Blender bridge engine -- export the Maya selection and run a chosen import template in Blender.
- `class BlenderBridge(MayaExportMixin, ptk.ScriptLaunchBridge)`
  - methods: blender_path, params_defaults, render_context, bake_lightmaps, reassemble_lightmaps, list_templates, template_modes, list_template_modes, template_path, template_output_ext, template_timeout

### `env_utils/blender_bridge/_scene_import.py` — Import a Blender scene (.blend) into Maya via a headless-Blender round-trip
- `class BlenderSceneImport(ptk.LoggingMixin, _BlenderSceneImportInternal)`
  - methods: blender_path, require_blender, find_scenes, render_script, convert, import_scene, mayapy_path, require_mayapy, render_bake_script, bake, bake_scene, bake_source

### `env_utils/blender_bridge/blender_bridge_slots.py` — Slots for the Blender bridge panel.
- `class BlenderBridgeSlots(MayaBridgeSlotsBase)`
  - methods: params_module, template_dir, make_bridge, list_template_modes, b000

### `env_utils/blender_bridge/parameters.py` — Registry of user-tunable Blender-bridge parameters exposed to the panel.
- `class Parameters`
  - methods: referenced_keys, defaults, render_context

### `env_utils/blender_bridge/templates/_bake_scene.py` — Import a converted intermediate (USD or FBX) headlessly (mayapy) and save it as a ``.ma``
- `import_source(cmds, engine)`
- `apply_manifest(engine, new_nodes)`
- `restore_empty_groups(engine, new_nodes)`
- `apply_instances(engine, new_nodes)`
- `main()`

### `env_utils/blender_bridge/templates/_import_scene.py` — Open a .blend headlessly (blender --background) and export it as FBX for a Maya import.
- `collect_texture_manifest(bpy)`
- `collect_empties(bpy)`
- `write_texture_manifest(entries, scene_materials, empties, path)`
- `export_fbx(bpy)`
- `main()`

### `env_utils/blender_bridge/templates/_import_scene_usd.py` — Open a .blend headlessly (blender --background) and export it as USD for a Maya import.
- `export_usd(bpy)`
- `collect_instance_groups(bpy)`
- `write_manifest(bpy)`
- `main()`

### `env_utils/blender_bridge/templates/_save_scene.py` — Import the bridged FBX into a headless Blender and save it as a ``.blend``.
- `apply_texture_manifest(new_objects)`
- `main()`

### `env_utils/blender_bridge/templates/bake_lightmaps.py` — Bake the bridged Maya selection's lightmaps in a headless Blender and write a WebXR GLB.
- `apply_texture_manifest(new_objects)`
- `light_scene(web, meshes)`
- `write_return_manifest(result)`
- `main()`

### `env_utils/blender_bridge/templates/import.py` — Import the bridged FBX into Blender, with optional clean-slate and frame-on-import behaviors.
- `apply_texture_manifest(new_objects)`
- `tag_node_types(new_objects)`
- `main()`

### `env_utils/devtools.py`
- `class DevTools(CoreUtils)`
  - methods: echo_all, find_mel, find_python, find, grep_maya_dir, grep_mel_procs, read_mel_proc, find_all, list_mel_globals, get_mel_global, source_mel
- `class WidgetInspector(CoreUtils)`
  - methods: from_maya_control, from_mel_global, main_window, walk, find_children_by_type, find_child_by_name, dump_tree, dump_properties, list_signals, list_slots, find_by_property, snapshot, diff_snapshots, connect_signal_logger, dump_actions, find_item_views, dump_model, get_selection_model

### `env_utils/fbx_utils.py`
- `class FbxUtils(ptk.HelpMixin)`
  - methods: load_plugin, embed_media_write_cwd, reset_import, set_fbx_options, load_preset, export, import_scene, reset_takes, apply_takes, apply_takes_from_node, run_export_preparers, register_export_preparer, unregister_export_preparer, enable_auto_takes, disable_auto_takes, is_auto_takes_enabled

### `env_utils/handoff_export.py` — Maya-side selection + FBX-export hooks shared by the hand-off bridge engines.
- `class MayaExportMixin`

### `env_utils/hierarchy_sync/_hierarchy_sync.py`
- `class HierarchyMapBuilder`
  - methods: build_path_map, build_path_map_from_nodes
- `class MayaObjectMatcher(ptk.LoggingMixin)`
  - methods: find_matches
- `class HierarchySync(ptk.LoggingMixin, _HierarchySyncInternal)`
  - methods: analyze_hierarchies, create_stubs, quarantine_extras, fix_fuzzy_renames, fix_reparented, get_clean_node_name, get_clean_node_name_from_string, clean_hierarchy_path, format_component, is_default_maya_camera, should_keep_node_by_type, filter_path_map_by_cameras, filter_path_map_by_types, select_objects_in_maya
- `class ObjectSwapper(ptk.LoggingMixin)`
  - methods: pull_objects_from_scene

### `env_utils/hierarchy_sync/hierarchy_sync_slots.py`
- `class HierarchySyncController(ptk.LoggingMixin)`
  - methods: workspace, reference_path, analyze_hierarchies, pull_objects, repair_hierarchies, select_objects_in_maya, populate_reference_tree, refresh_trees, is_path_ignored, clear_ignored_paths, log_diff_results, get_recent_reference_scenes, save_recent_reference_scene
- `class HierarchySyncSlots(ptk.LoggingMixin)`
  - methods: header_init, tree000_init, tree001_init, cmb_diff_options_init, cmb_pull_options_init, tb003_init, tb001, tb002, tb003, b003, b005, b006, b007, b008, b009, b011, b012, b013, b014, b015, b016, b018, b017, count_tree_items

### `env_utils/hierarchy_sync/scene_data_sidecar.py` — Scene-data sidecar manifest management.
- `class SceneDataSidecar`
  - methods: base_stem, manifest_path_for, diff_report_path_for, find_legacy_manifest, ensure_base_name, migrate_legacy, rename, build_clean_path_set, expand_to_descendants, get_top_level, detect_reparenting, write_manifest, read_manifest, read_data, count_descendants, write_diff_report, clean_stale_diff, build_full_path_set, compare

### `env_utils/hierarchy_sync/tree_renderer.py` — Tree rendering, formatting, and selection management for the hierarchy sync UI.
- `class HierarchyTreeRenderer(ptk.LoggingMixin)`
  - methods: populate_current_scene_tree, populate_reference_tree, show_reference_placeholder, show_reference_error, populate_tree_with_hierarchy, apply_difference_formatting, clear_tree_colors, format_tree_differences, apply_ignore_styling, build_item_path, find_tree_item_by_name, get_selected_tree_items, get_selected_object_names

### `env_utils/hierarchy_sync/tree_utils.py` — Tree widget utilities for hierarchy sync UI operations.
- `class TreePathMatcher(ptk.LoggingMixin, _TreePathMatcherInternal)`
  - methods: build_tree_index, find_path_matches, log_matching_debug, log_tree_index_debug, get_selected_object_names, get_selected_tree_items, find_tree_item_by_name, build_hierarchy_structure

### `env_utils/maya_connection.py` — Maya Connection Module
- `class MayaConnection`
  - methods: get_instance, open_command_ports, close_command_ports, open_available_command_ports, toggle_command_ports, reload_modules, connect, get_pid_from_port, get_port_from_pid, close_instance, get_available_port, ensure_connection, execute, get_script_editor_output, execute_and_capture_editor_output, clear_script_editor, shutdown, disconnect

### `env_utils/namespace_sandbox.py`
- `class FBXImporter`
  - methods: is_supported_file, import_with_namespace, import_for_analysis
- `class MayaImporter`
  - methods: is_supported_file, import_with_namespace, import_for_analysis
- `class CameraTracker(ptk.LoggingMixin)`
  - methods: capture_pre_import_state, capture_post_import_state, get_imported_cameras, cleanup_imported_cameras, reset
- `class NamespaceSandbox(ptk.LoggingMixin, _NamespaceSandboxInternal)`
  - methods: import_with_namespace, import_for_analysis, get_supported_formats, find_objects_in_namespace, find_objects_with_hierarchy_matching, get_namespace_hierarchy, cleanup_import, cleanup_namespace, cleanup_all_namespaces, get_imported_cameras, cleanup_imported_cameras, cleanup_all_temp_namespaces_force, export_objects_to_temp, import_objects_for_swapping, import_to_target_scene, cleanup_analysis_namespace

### `env_utils/reference_manager.py`
- `class AssemblyManager`
  - methods: current_references, create_assembly_definition, set_active_representation, convert_references_to_assemblies
- `class ReferenceManager(WorkspaceManager, ptk.HelpMixin, ptk.LoggingMixin, _ReferenceManagerInternal)`
  - methods: current_references, sanitize_namespace, add_reference, import_references, update_references, get_reference_top_transforms, get_reference_display_mode, set_reference_display_mode, remove_references
- `class ReferenceManagerController(ReferenceManager, ptk.LoggingMixin)`
  - methods: current_working_dir, block_table_selection_method, prepare_item_for_edit, restore_item_display, is_item_being_edited, handle_item_selection, sync_selection_to_references, update_current_dir, set_workspace, refresh_file_list, update_table, open_scene, new_scene, unreference_all, unlink_all, unlink_references, convert_to_assembly, save_scene, rename_scene, delete_scene
- `class ReferenceManagerSlots(ptk.HelpMixin, ptk.LoggingMixin)`
  - methods: header_init, tbl000_init, tbl000_item_double_clicked, tbl000_item_changed, tbl000_editor_closed, btn_open_file_location, txt000_init, txt001_init, txt001, cmb000_init, cmb000, chk000, chk003, chk_ignore_case, chk_filter_suffix, chk_hide_suffix, chk_hide_extension, chk_show_notes_column, txt_suffix, chk_filter_folder_structure, b000, b006, b001, btn_open_scene, btn_toggle_reference, btn_unlink_import, btn_save_scene, btn_refresh, btn_convert_assembly, btn_unlink_import_all, btn_unreference_all

### `env_utils/scene_exporter/_scene_exporter.py`
- `class SceneExporter(ptk.LoggingMixin)`
  - methods: perform_export, generate_export_path, format_export_name, generate_log_file_path, setup_file_logging, close_file_handlers, load_fbx_export_preset, verify_fbx_preset
- `class SceneExporterSlots(SceneExporter)`
  - methods: workspace, presets, header_init, cmb000_init, txt000_init, txt001_init, cmb001_init, cmb002_init, cmb004_init, b000, b010, b005, b006, b007, b008, save_output_dir, save_output_name

### `env_utils/scene_exporter/task_manager.py`
- `class TaskManager(TaskFactory, _TaskActionsMixin, _TaskChecksMixin)`
  - methods: objects, task_definitions, check_definitions, definitions, set_workspace, set_linear_unit, conform_shape_names, convert_to_relative_paths, reassign_duplicate_materials, resolve_invalid_texture_paths, smart_bake, optimize_keys, set_bake_animation_range, tie_all_keyframes, snap_keys_to_frame, create_glb, export_data_node, apply_declared_takes, check_geometry_lod_suffix, ignore_groups, exclude_hdr, check_root_default_transforms, check_absolute_paths, check_valid_paths, check_texture_file_size, check_mangled_names, check_duplicate_locator_names, check_duplicate_materials, check_referenced_objects, check_framerate, check_objects_below_floor, check_overlapping_duplicate_mesh, check_hidden_geometry, check_untied_keyframes, check_floating_point_keys, write_scene_data_sidecar, check_hierarchy_vs_existing_fbx

### `env_utils/scene_state.py` — Read named sections of live-scene state for transport.
- `class SceneState`
  - methods: source, read

### `env_utils/script_output.py`
- `class ScriptConsole(MayaQWidgetDockableMixin, QtWidgets.QDialog)`
  - methods: toggle, show_console

### `env_utils/unity_bridge/_unity_bridge.py` — Unity bridge engine -- export the Maya selection into a Unity project's Assets/.
- `class UnityBridge(MayaExportMixin, ptk.HandoffBridge)`
  - methods: list_template_modes, params_defaults, list_delivery_modes

### `env_utils/unity_bridge/parameters.py` — User-tunable parameters for the Maya->Unity bridge panel.
- `class Parameters`
  - methods: referenced_keys, defaults, render_context

### `env_utils/unity_bridge/unity_bridge_slots.py` — Slots for the Unity bridge panel.
- `class UnityBridgeSlots(MayaBridgeSlotsBase)`
  - methods: params_module, template_dir, make_bridge, list_template_modes, default_output_dir, b000

### `env_utils/usd.py` — USD import / export over Maya's native ``mayaUsd`` runtime.
- `class UsdUtils(ptk.HelpMixin)`
  - methods: load_plugin, is_usd_file, export, import_scene

### `env_utils/webxr_preview.py` — Push the Maya selection to a live browser / WebXR preview.
- `class WebXrPreview(MayaExportMixin, ptk.PreviewBridge)`

### `env_utils/workspace_manager.py`
- `class WorkspaceManager(ptk.HelpMixin, ptk.LoggingMixin)`
  - methods: current_workspace, current_working_dir, recursive_search, ignore_empty_workspaces, workspace_files, find_available_workspaces, invalidate_workspace_files, resolve_file_path

### `env_utils/workspace_map.py`
- `class WorkspaceMap(WorkspaceManager, ptk.HelpMixin, ptk.LoggingMixin)`
  - methods: current_working_dir, recursive_search, workspace_data, invalidate_workspace_data, get_workspace_tree_data, get_filtered_workspaces, create_project, mark_root_as_project
- `class WorkspaceMapController(WorkspaceMap, ptk.LoggingMixin)`
  - methods: update_current_dir, refresh_tree, selected_workspace, open_selected_workspace
- `class WorkspaceMapSlots(ptk.HelpMixin, ptk.LoggingMixin)`
  - methods: header_init, txt000_init, txt001_init, tree000_init, filter_workspaces, chk000, browse_directory, set_to_workspace, btn_open_workspace, btn_explore_folder, new_project, mark_root, save_template

### `light_utils/_light_utils.py`
- `class LightUtils(ptk.HelpMixin)`

### `light_utils/hdr_manager.py` — Arnold HDR environment manager.
- `class HdrManager(ptk.LoggingMixin, ptk.HelpMixin)`
  - methods: arnold_loaded, arnold_available, ensure_plugin_loaded, hdr_env, hdr_env_transform, hdr_file_node, hdr_file_path, visibility, set_hdr_map_visibility, sky_radius, preview, rotation, intensity, exposure, resolution, samples, diffuse, specular, create_network, clear
- `class HdrManagerSlots(ptk.LoggingMixin, ptk.HelpMixin)`
  - methods: header_init, cmb000_init, hdr_map, hdr_map_visibility, hdr_map_preview, cmb000, slider000, spn_intensity, spn_exposure, spn_resolution, spn_samples, spn_diffuse, spn_specular, add_hdr, open_sourceimages, clear_network, ctx_select_skydome, ctx_select_transform, ctx_select_file_node, ctx_reveal_in_explorer

### `light_utils/lightmap_baker/lightmap_baker.py` — High-level lightmap baking workflow for Maya -> game engines (Unity-first).
- `class LightmapBaker(ptk.LoggingMixin)`
  - methods: preset_store, from_preset, bake_fused, bake_separated, commit_unlit, revert_unlit, pack_atlas, commit_lightmap, refresh_export_metadata, revert_lightmap, revert
- `class LightmapBakerSlots(ptk.LoggingMixin, ptk.HelpMixin)`
  - methods: header_init, cmb000_init, cmb000, cmb001_init, cmb002_init, cmb_scope_init, cmb_resolution_init, txt000_init, b000, revert_to_source, open_sourceimages

### `mat_utils/_mat_utils.py`
- `class MatUtils(_MatUtilsInternal)`
  - methods: resolve_path, get_mats, group_objects_by_material, is_bundled_texture, get_texture_paths, get_texture_info, get_mat_info, format_texture_info_text, format_texture_info_html, format_mat_info_text, format_mat_info_html, get_scene_mats, get_connected_shaders, connect_to_channels, get_mats_by_scope, find_opacity_source, enable_viewport_opacity, set_transparency_algorithm, ensure_transparent_graph, get_file_nodes, get_fav_mats, is_mat_assigned, is_connected, create_mat, assign_mat, claim_material_name, get_shading_assignments, apply_shading_assignments, create_file_node, create_shading_group, resolve_opacity_mode, resolve_stingray_graph, load_stingray_graph, create_stingray_shader, find_by_mat_id, find_unassigned, collect_material_paths, remap_file_nodes, remap_texture_paths, stage_textures_relative, is_duplicate_material, find_materials_with_duplicate_textures, reassign_duplicate_materials, filter_materials_by_objects, reload_textures, move_texture_files, copy_textures_to_sourceimages, find_texture_files, migrate_textures, move_unused_textures, get_mat_swatch_icon, convert_bump_to_normal, validate_normal_map_setup, graph_materials, get_texture_file_node

### `mat_utils/arnold_bridge.py` — Arnold render-bridge management.
- `class ArnoldBridge(ptk.LoggingMixin, _ArnoldBridgeInternal)`
  - methods: add, remove, rebuild, get_bridge, has_bridge
- `class ArnoldBridgeSlots(ptk.LoggingMixin, ptk.HelpMixin)`
  - methods: header_init, cmb000_init, b000, b001, select_bridged

### `mat_utils/bake_sets.py` — Scene-stored bake-source set shared by the hand-off bridges.
- `class BakeSourceSet`
  - methods: companion_path, exists, members, define, clear

### `mat_utils/emissive_groups.py` — Emissive groups — named face sets that gate emissive regions at runtime.
- `class EmissiveGroups(_EmissiveGroupsInternal, ptk.LoggingMixin, ptk.HelpMixin)`
  - methods: add_group, remove_group, list_groups, select_group, set_default, make_weights_keyable, remove_keyable_weights, key_weight, compact_slots, validate, bake_vertex_colors, bake_mask, refresh_export_metadata
- `class EmissiveGroupsSlots(ptk.LoggingMixin, ptk.HelpMixin)`
  - methods: header_init, txt000_init, tbl000_init, b000, b001, b002, b003, tb000_init, tb000, select_members, remove_group, weights_all_on, weights_all_off, make_weights_keyable, key_weights, remove_keyable_weights, compact_slots, republish_export

### `mat_utils/game_shader.py`
- `class GameShader(ptk.LoggingMixin, _GameShaderInternal)`
  - methods: create_network, setup_stringray_node, setup_standard_surface_node, setup_open_pbr_node, connect_stingray_nodes, connect_standard_surface_nodes, connect_open_pbr_nodes, filter_for_correct_metallic_map, filter_for_mask_map, filter_for_correct_base_color_map
- `class GameShaderSlots(GameShader)`
  - methods: header_init, lbl_graph_material, mat_name, mat_prefix, mat_suffix, normal_map_type, output_extension, shader_type, cmb002_init, cmb003_init, txt002_init, b000

### `mat_utils/image_to_plane/_image_to_plane.py` — Map image files to textured polygon planes in Maya.
- `class ImageToPlane(ptk.LoggingMixin)`
  - methods: create, remove

### `mat_utils/image_to_plane/image_to_plane_slots.py` — Switchboard slots for the Image to Plane UI.
- `class ImageToPlaneSlots`
  - methods: header_init, txt_suffix_init

### `mat_utils/marmoset_bridge/_marmoset_bridge.py` — Maya-side glue for the Marmoset Toolbag engine.
- `class MarmosetBridge(ptk.HandoffBridge, _MarmosetBridgeInternal)`
  - methods: toolbag_path, params_defaults, render_template, source_model_path_for, baked_material_name, build_bake_pairs_manifest

### `mat_utils/marmoset_bridge/_marmoset_engine.py` — Drive Marmoset Toolbag from the outside -- launch + templated automation.
- `class MarmosetEngine(ptk.Deliverer, ptk.LoggingMixin)`
  - methods: toolbag_path, toolbag_log_path, preflight, deliver, send, render_template, list_templates, template_modes, list_template_modes

### `mat_utils/marmoset_bridge/_toolbag_helpers.py` — Shared helpers for Marmoset Toolbag template scripts.
- `class ToolbagHelpers(_ToolbagHelpersInternal)`
  - methods: derive_per_run_log_path, begin_log, log, find_material, load_manifest, wire_materials_from_manifest, split_source_target, collect_mesh_objects, apply_sky_preset, frame_in_viewport

### `mat_utils/marmoset_bridge/marmoset_bridge_slots.py` — Slots for the Marmoset Toolbag bridge panel.
- `class MarmosetBridgeSlots(MayaBridgeSlotsBase)`
  - methods: set_bake_source_from_selection, select_bake_source, clear_bake_source, params_module, template_dir, make_bridge, list_template_modes, select_initial_template_index, b000

### `mat_utils/marmoset_bridge/marmoset_rpc/connection.py` — JSON-RPC client bound to the marmoset_rpc Toolbag plugin.
- `class MarmosetConnection(RpcClient, _MarmosetConnectionInternal)`

### `mat_utils/marmoset_bridge/marmoset_rpc/installer.py` — Install the marmoset_rpc plugin into Toolbag's user plugin folder.
- `class Installer(_InstallerInternal)`
  - methods: user_plugin_dir, is_installed, install, uninstall

### `mat_utils/marmoset_bridge/marmoset_rpc/job.py` — One-shot batch pipeline for the marmoset_rpc bridge.
- `class BatchJob`
  - methods: run_batch

### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/__init__.py` — Marmoset Toolbag RPC plugin -- entry point.
- `start_server(port=None, host=None)`
- `stop_server()`
- `is_running()`
- `autostart()`

### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/_rpc_core.py` — The in-application half of the RPC pair: registry + marshaller + server.
- `class OpRegistry(_OpRegistryInternal)`
  - methods: register, get, all_ops, describe
- `class MainThreadMarshaller(_MainThreadMarshallerInternal)`
  - methods: is_active, run
- `class RpcPlugin(object)`
  - methods: import_ops, port, is_hosted, is_running, address, start, stop, autostart, autostart_safely

### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/scene_ops.py` — Scene-inspection ops.
- `summary()`
- `list_materials()`

### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py` — Toolbag-specific system ops.
- `version()`

### `mat_utils/marmoset_bridge/parameters.py` — Registry of user-tunable Marmoset Toolbag parameters exposed to the bridge UI.
- `class Parameters`
  - methods: referenced_keys, defaults, render_context

### `mat_utils/marmoset_bridge/template_params.py` — Plain default values + literal formatting for Marmoset template tokens.
- `class TemplateParams`
  - methods: derive_auto_maps, derive_bake_values, python_literal, defaults, to_context

### `mat_utils/marmoset_bridge/templates/bake.py` — Bake source detail + surface maps onto the target meshes.
- `main()`

### `mat_utils/marmoset_bridge/templates/import.py` — Open the model in Toolbag and wire materials from the manifest.
- `main()`

### `mat_utils/marmoset_bridge/templates/lookdev.py` — Open the model in Toolbag, apply a Sky preset, and frame the model.
- `main()`

### `mat_utils/marmoset_bridge/toolbag_log.py` — Marmoset Toolbag log-file resolution, classification, and live tailing.
- `class ToolbagLog`
  - methods: resolve_toolbag_log_path, classify_log_line, dispatch_log_lines, start_toolbag_log_tail

### `mat_utils/mat_manifest.py`
- `class MatManifest(ptk.HelpMixin)`
  - methods: build, restore

### `mat_utils/mat_snapshot.py` — Lightweight material state snapshot and restore.
- `class MatSnapshot`
  - methods: capture, restore

### `mat_utils/mat_updater.py`
- `class MatUpdater(ptk.LoggingMixin)`
  - methods: update_materials, disconnect_associated_attributes, update_network
- `class MatUpdaterSlots(MatUpdater)`
  - methods: header_init, selection_mode, move_to_folder, max_size, mask_map_scale, output_extension, old_files_folder, cmb001_init, b001

### `mat_utils/render_opacity/_render_opacity.py`
- `class RenderOpacity(ptk.LoggingMixin)`
  - methods: objects_with_visibility_keys, create, ensure_connections, sync_visibility_from_opacity, key_fade, prepare_for_export, remove

### `mat_utils/render_opacity/attribute_mode.py`
- `class OpacityAttributeMode(ptk.LoggingMixin)`
  - methods: create, key_fade, sync_visibility_from_opacity, ensure_connections, remove

### `mat_utils/render_opacity/material_mode.py`
- `class OpacityMaterialMode(ptk.LoggingMixin)`
  - methods: get_stingray_mats, create, ensure_connections, remove

### `mat_utils/render_opacity/render_opacity_slots.py` — Switchboard slots for the Render Opacity UI.
- `class RenderOpacitySlots`
  - methods: header_init, tb000_init, tb000

### `mat_utils/shader_attribute_map.py` — Logical texture channel -> per-shader (attribute, output plug), and the one
- `class ShaderAttributeMap(_ShaderAttributeMapInternal)`
  - methods: logical_channels, get_attr, get_mapping, connect_channel, resolve_live_slot, map_toggle_attr, add_shader_type, update_attr, as_dict

### `mat_utils/shader_converter.py` — Retype a material in place — legacy Maya shaders to an exportable PBR one.
- `class ShaderConverter(ptk.LoggingMixin, _ShaderConverterInternal)`
  - methods: read_channels, convert

### `mat_utils/shader_templates/_shader_templates.py`
- `class GraphCollector`
  - methods: collect_graph
- `class GraphSaver(GraphCollector)`
  - methods: save_graph
- `class GraphRestorer`
  - methods: load_yaml, restore_graph, restore_connections
- `class ShaderTemplates`
  - methods: save_template, restore_template
- `class ShaderTemplatesSlots(ptk.LoggingMixin)`
  - methods: header_init, lbl_graph_material, lbl_open_templates_dir, cmb002_init, refresh_templates, rename_template_safe, lbl000, lbl001, lbl002, b000, b001, b002

### `mat_utils/substance_bridge/_substance_bridge.py` — Substance 3D Painter bridge -- export Maya selection and hand off to Painter.
- `class SubstanceBridge(ptk.HandoffBridge)`
  - methods: painter_path, painter_log_path, instances, find_live_managed, send, ensure_rpc_plugin, mesh_map_files, source_model_path_for, list_templates, parse_template, list_template_modes, resolve_painter_log_path

### `mat_utils/substance_bridge/connection.py` — Substance 3D Painter connection module.
- `class SubstanceConnection(ptk.LoggingMixin)`
  - methods: open, close, is_alive, attach, find_painter_exe, default_log_path

### `mat_utils/substance_bridge/parameters.py` — Registry of user-tunable Substance Painter parameters exposed to the bridge UI.
- `class Parameters`
  - methods: referenced_keys, defaults, render_cli_context, render_js_context

### `mat_utils/substance_bridge/substance_bridge_slots.py` — Slots for the Substance Painter bridge panel.
- `class SubstanceBridgeSlots(MayaBridgeSlotsBase)`
  - methods: set_bake_source_from_selection, select_bake_source, clear_bake_source, params_module, template_dir, make_bridge, list_template_modes, select_initial_template_index, b000

### `mat_utils/substance_bridge/substance_rpc/client.py` — HTTP RPC client for the Painter-side ``substance_rpc`` plugin.
- `class PainterRpcClient(RpcClient)`
  - methods: wait_until_ready, invoke, eval_js, eval_py, reload_mesh, reload_status, project_info

### `mat_utils/substance_bridge/substance_rpc/installer.py` — Install the substance_rpc plugin into Painter's user plugin folder.
- `class Installer(_InstallerInternal)`
  - methods: user_plugin_dir, is_installed, is_current, install, uninstall

### `mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/__init__.py` — Substance 3D Painter RPC plugin -- entry point.
- `start_server(port=None, host=None)`
- `stop_server()`
- `is_running()`
- `autostart()`
- `start_plugin()`
- `close_plugin()`

### `mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/_rpc_core.py` — The in-application half of the RPC pair: registry + marshaller + server.
- `class OpRegistry(_OpRegistryInternal)`
  - methods: register, get, all_ops, describe
- `class MainThreadMarshaller(_MainThreadMarshallerInternal)`
  - methods: is_active, run
- `class RpcPlugin(object)`
  - methods: import_ops, port, is_hosted, is_running, address, start, stop, autostart, autostart_safely

### `mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/project_ops.py` — Project-level ops: inspect the open project and reload its mesh.
- `project_info()`
- `mesh_reload(mesh_path='', preserve_strokes=True, import_cameras=False)`
- `mesh_reload_status()`

### `mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/setup_ops.py` — Project-setup ops: resolution, the baking high poly, and mesh maps.
- `teardown()`
- `set_resolution(size=0)`
- `set_high_poly(mesh_path='')`
- `apply_mesh_maps(manifest_path='')`
- `pending_setup()`

### `mat_utils/substance_bridge/substance_rpc/plugin_src/substance_rpc/ops/system_ops.py` — Painter-specific system ops: version reporting and script evaluation.
- `version()`
- `eval_python(script='')`
- `js_evaluate(script='')`

### `mat_utils/texture_baker.py` — Bake an object's shaded surface (material under scene lighting) to a texture.
- `class TextureBaker(ptk.LoggingMixin)`
  - methods: arnold_available, bake, assign_to_diffuse, restore_diffuse_connections

### `mat_utils/texture_path_editor.py`
- `class TexturePathEditorSlots`
  - methods: header_init, tb_set_texture_directory_init, tb_find_and_copy_textures_init, tb_normalize_paths_init, tb_resolve_missing_textures_init, tbl000_init, open_source_images, reload_scene_textures, tb_set_texture_directory, tb_find_and_copy_textures, tb_normalize_paths, make_paths_absolute, tb_resolve_missing_textures, select_textures_for_objects, select_broken_paths, select_absolute_paths, row_browse_for_file, select_material, select_file_node, row_show_in_hypershade, delete_file_node, refresh_texture_table, cleanup_scene_callbacks, setup_formatting, handle_cell_edit

### `node_utils/_node_utils.py`
- `class NodeUtils(ptk.HelpMixin)`
  - methods: get_type, get_inherited_types, is_mesh, is_locator, is_group, is_geometry, is_constraint, is_expression, is_ik_effector, is_driven_key_curve, is_muted, is_motion_path, is_ik_handle, get_constraint_targets, get_groups, get_parent, get_children, get_shapes, get_shape, is_intermediate, node_is, list_transforms, get_unique_children, get_transform_node, get_shape_node, get_history_node, get_classification_tokens, create_render_node, get_connected_nodes, create_assembly, get_instances, replace_with_instances, instance, get_instanced_shapes, uninstance, filter_duplicate_instances

### `node_utils/attributes/_attributes.py` — Consolidated attribute utilities for Maya.
- `class AttributeTemplate`
- `class Preset(NamedTuple)`
- `class Attributes(ptk.HelpMixin)`
  - methods: has_attr, set_plug, attr_short_name, abbreviate_attrs, apply_preset, remove_preset, create_attributes, ensure_attribute, get_attributes, get_type, get_selected_channels, get_channel_box_values, set_attributes, create_or_set, create_switch, connect, connect_multi, trace_upstream, get_lock_state, set_lock_state, temporarily_unlock, copy_values, paste_values, reset_to_default, mute, unmute, set_channel_box_visibility, lock_and_hide, filter, parse_enum_def, build_enum_string, get_enum_fields, get_enum_label, enum_label_to_index, rename_enum_field, add_enum_field, delete_enum_field

### `node_utils/attributes/channels/__init__.py` — Channels — Switchboard UI for inspecting and editing Maya attributes.
- `launch(sb=None, targets=None, filter=None, search=None)`

### `node_utils/attributes/channels/_channels.py` — Channels — Maya attribute query / mutation logic.
- `class Channels`
  - methods: is_pinned, single_object_mode, pin_targets, get_selected_nodes, resolve_component_targets, get_channel_box_selection, get_filter_kwargs, query_connected_attrs, collect_attr_names, collect_value_strings, get_attr_value, get_attr_type, get_incoming_connection, classify_connection, has_key_at_current_time, build_table_data, format_value, parse_value, toggle_lock, break_connections, set_lock, reset_to_default, toggle_keyable, delete_attributes, set_attribute_value, create_attribute, copy_attr_values, paste_attr_values, rename_attribute, rename_node, get_shape_nodes, get_history_nodes, toggle_key_at_current_time, set_breakdown_key, mute_attrs, unmute_attrs, hide_attrs, show_attrs, lock_and_hide_attrs, select_connections, can_freeze_selection, freeze_transforms, unfreeze_transforms, has_unfreeze_info

### `node_utils/attributes/channels/channels_slots.py` — UI slots for the Channels UI.
- `class ChannelsSlots`
  - methods: apply_launch_config, header_init, show_create_menu, cmb000_init, cmb000, tbl000_init, cleanup_scene_callbacks

### `node_utils/data_nodes.py`
- `class DataNodes`
  - methods: ensure_internal, ensure_export, set_internal_string, get_internal_string, set_export_string, get_export_string, dump, format_dump

### `nurbs_utils/_nurbs_utils.py`
- `class NurbsUtils(ptk.HelpMixin)`
  - methods: loft, create_curve_between_two_objs, duplicate_along_curve, angle_loft_between_two_curves, get_curve_length, get_arc_lengths, get_closest_cv, get_cv_info, getCrossProductOfCurves

### `nurbs_utils/curve_to_tube.py` — Sweep a circular profile along NURBS curve(s) to build a tube.
- `class CurveToTube(ptk.LoggingMixin)`
  - methods: create
- `class CurveToTubeSlots(ptk.LoggingMixin)`
  - methods: header_init, b001, perform_operation

### `nurbs_utils/image_tracer.py`
- `class BluePencilMixin(object)`
  - methods: get_blue_pencil_curves
- `class ImageTracer(BluePencilMixin)`
  - methods: trace_curves, create_mesh, create_negative_space_mesh, project_on_plane
- `class ImageTracerSlots`
  - methods: header_init, txt000_init, browse_image, chk000, b002, b003, b004, b005

### `render_utils/_render_utils.py` — Render-control helpers.
- `class RenderUtils(ptk.HelpMixin)`
  - methods: get_available_renderers, current_renderer, set_renderer, render_camera, redo_previous_render, supports_ipr, start_ipr

### `rig_utils/_rig_utils.py`
- `class RigUtils(ptk.HelpMixin)`
  - methods: create_helper, create_group, create_locator, create_locator_at_object, remove_locator, restore_rig_anchors, connect_switch_to_constraint, create_ik_handle, create_pole_vector, get_ik_handles_for_joint, joint_in_ik_chain, get_joint_chain_from_root, invert_joint_chain, rebind_skin_clusters

### `rig_utils/controls.py`
- `class ControlNodes`
- `class Controls(ptk.HelpMixin)`
  - methods: register_preset, shapes, create, combine

### `rig_utils/shadow_rig.py`
- `class ShadowRig(ptk.LoggingMixin)`
  - methods: create_contact_locator, get_or_create_shadow_source, create_shadow_plane, create_silhouette_texture, create_material, setup_expression, bake, refresh_export_metadata, find_shadow_planes, bake_planes, delete, delete_rigs, create
- `class ShadowRigSlots`
  - methods: header_init, b001, b002, perform_operation

### `rig_utils/skinning.py` — Skinning utilities: binding, batch weight I/O, transfer, procedural weights.
- `class CurveWeights(ptk.HelpMixin)`
  - methods: effective_degree, joint_stations, solve
- `class SkinUtils(ptk.HelpMixin)`
  - methods: get_skin_cluster, get_influences, bind, name_bind_pose, unbind, get_weights, set_weights, set_vertex_weights, prune_weights, normalize_weights, set_max_influences, set_skinning_method, copy_weights, mirror_weights, export_weights, import_weights, apply_falloff, add_delta_mush, bind_to_curve

### `rig_utils/telescope_rig.py`
- `class TelescopeRigBundle`
  - methods: to_json, from_json
- `class TelescopeRig(ptk.LoggingMixin)`
  - methods: setup_telescope_rig, scene_bundles, find_bundles, teardown
- `class TelescopeRigSlots(ptk.LoggingMixin)`
  - methods: header_init, build_rig, remove_rig

### `rig_utils/tube_rig.py`
- `class TubePath`
  - methods: get_centerline, get_edge_loop_centers, estimate_radius, get_centerline_using_edges, get_centerline_from_surface_normals, get_centerline_from_bounding_box
- `class TubeRigBundle`
- `class TubeStrategy(ABC)`
  - methods: build
- `class FKChainStrategy(TubeStrategy)`
  - methods: build
- `class SplineIKStrategy(TubeStrategy)`
  - methods: build
- `class AnchorStrategy(TubeStrategy)`
  - methods: build
- `class TubeRig(ptk.LoggingMixin, _TubeRigInternal)`
  - methods: for_mesh, for_node, rig_name, rig_group, teardown, build, resolve_centerline, estimate_tube_radius, resolve_sizes, generate_joint_chain, create_anchor_joints, skin_mesh, create_logic_curve, create_spline_drivers, skin_curve_to_drivers, create_spline_controls, create_fk_controls, create_anchor_controls, setup_spline_twist, setup_auto_bend, setup_spline_stretch, create_ik, create_pole_vector, bind_joint_chain, constrain_end_with_falloff
- `class RigModeConfig`
- `class TubeRigSlots`
  - methods: txt000_init, header_init, apply_mode, get_mode, get_strategy, get_tube_rig, create_joints_from_tube, b000, b001, b002, b003, b004

### `rig_utils/wheel_rig.py`
- `class WheelRig(ptk.LoggingMixin)`
  - methods: rig_name, get_expressions, delete_expressions, rig_rotation
- `class WheelRigSlots`
  - methods: header_init, rig_name, movement_axis, rotation_axis, resolve_selection, set_wheel_height, txt000_init, s000_init, update_rig_name_placeholder, cleanup, wheel_rig, b000

### `ui_utils/_ui_utils.py`
- `class UiUtils`
  - methods: get_main_window, get_menu_name, get_panel, get_model_panel, main_progress_bar, list_ui_objects, clear_scrollfield_reporters, reveal_in_outliner, dispatch_log_link

### `ui_utils/calculator.py`
- `class CalculatorController`
  - methods: calculate, get_fps_value, get_current_time, frames_to_sec, sec_to_frames, convert_unit
- `class CalculatorSlots`
  - methods: header_init, on_convert_units, on_input, on_clear, on_backspace, on_equal, get_fps, get_current_time, frames_to_sec, sec_to_frames

### `ui_utils/channel_box.py` — Programmatic access to Maya's Channel Box.
- `class ChannelBox`
  - methods: connect_selection_changed, disconnect_selection_changed, get_selected_attrs, get_selected_objects, get_selected_plugs, select, select_visual, clear_selection, get_all_attrs, get_attr_properties, watch_selection, unwatch_selection, get_context_menu_actions, snapshot, diff, list_mel_procs, read_mel_proc, dump_tree, dump_model, list_signals, list_item_views

### `ui_utils/hotkey_collisions.py` — Maya hotkey collision checker for the uitk ShortcutEditor.
- `class HotkeyCollisions(_HotkeyCollisionsInternal)`
  - methods: parse_qt_sequence, keystring_to_token, live_hotkey_map, ensure_editable_hotkey_set, maya_collision_checker

### `ui_utils/maya_bridge_slots_base.py` — Maya-flavored :class:`BridgeSlotsBase` -- adds Maya-side defaults.
- `class MayaBridgeSlotsBase(BridgeSlotsBase)`
  - methods: default_output_dir, resolve_scope_objects

### `ui_utils/maya_native_menus.py`
- `class MayaNativeMenus(ptk.LoggingMixin)`
  - methods: get_menu, display_menu

### `ui_utils/maya_ui_handler.py`
- `class MayaUiHandler(UiHandler)`
  - methods: instance, can_resolve, get, default_persistence

### `ui_utils/node_icons.py` — Reusable helper for resolving Maya node icons at runtime.
- `class NodeIcons`
  - methods: icon_name_for_type, icon_name_for_node, get_icon, get_pixmap

### `ui_utils/style_setter/_style_setter.py` — Match Maya's scriptable viewport colors to another DCC's look.
- `class StyleSetter(_StyleSetterInternal)`
  - methods: list_styles, set_style, list_templates, apply_template

### `uv_utils/_auto_unwrap.py` — External auto-unwrap round-trip: OBJ out, engine, OBJ back, UVs transferred.
- `class AutoUnwrapResult`

### `uv_utils/_uv_pack.py` — xatlas pack round-trip: UV arrays out, :class:`pythontk.UvPack`, per-shell
- `class PackUvsResult`

### `uv_utils/_uv_utils.py`
- `class UvUtils(ptk.HelpMixin)`
  - methods: calculate_uv_padding, udim_to_tile, orient_shells, move_to_uv_space, get_uv_bounds, gather_to_udim, get_neighbor_shell_bounds, mirror_uvs, flip_uvs, get_uv_shell_sets, get_uv_shell_border_edges, get_cylinder_seam_edges, get_auto_seam_edges, get_topology_seam_edges, detect_seam_algorithm, cut_cylinder_seams, cut_uv_edges, auto_unwrap, pack_uvs, unwrap_cylinder, get_texel_density, set_texel_density, snapshot_uv_sets, restore_uv_snapshot, discard_uv_snapshot, transfer_uvs, transfer_uvs_to_similar, reorder_uv_sets, apply_uv_layout, create_lightmap_uvs, remove_empty_uv_sets

### `uv_utils/rizom_bridge/_rizom_bridge.py`
- `class RizomUVBridge(ptk.LoggingMixin, _RizomUVBridgeInternal)`
  - methods: rizom_path, rizom_version, export_path, script_path, process_with_rizomuv, expand_by_materials, send_to_rizomuv

### `uv_utils/rizom_bridge/parameters.py` — Registry of user-tunable RizomUV parameters exposed to the bridge UI.
- `class Parameters`
  - methods: expand_includes, preset_min_version, referenced_keys, defaults, derived_values, render_context, strip_unsupported

### `uv_utils/rizom_bridge/rizom_bridge_slots.py` — Slots for the RizomUV bridge panel.
- `class RizomBridgeSlots(MayaBridgeSlotsBase)`
  - methods: params_module, template_dir, make_bridge, list_template_modes, b000, open_uv_editor

### `uv_utils/shell_xform.py` — Dedicated UV shell-transform panel.
- `class ShellXformSlots(ptk.LoggingMixin)`
  - methods: header_init, cmb_move_scope_init, b023, b024, b025, b026, gather_to_udim, b034, b035, b036, b037, s041, tb005_init, tb005, tb006_init, tb006, tb008_init, tb008, align_u_min, align_u_avg, align_u_max, align_v_min, align_v_avg, align_v_max, linear_align, orient_shells, orient_edges, gather_shells, randomize_shells, open_uv_editor

### `xform_utils/_xform_utils.py`
- `class XformUtils(_XformUtilsInternal, ptk.HelpMixin)`
  - methods: convert_axis, move_to, drop_to_grid, match_scale, scale_connected_edges, store_transforms, freeze_instanced_group, freeze_transforms, freeze_to_opm, unfreeze_from_opm, unfreeze_to_parent, restore_transforms, clear_stored_transforms, repair_stored_transforms, has_stored_transforms, channels_at_identity, get_stored_transforms, reset_translation, set_translation_to_pivot, get_manip_pivot_matrix, set_manip_pivot_matrix, restore_original_axes, get_pivot_options, clear_manip_cache, snapshot_manip_pivot, get_operation_axis_matrix, get_operation_axis_pos, align_pivot_to_selection, reset_pivot_transforms, world_align_pivot, bake_pivot, transfer_pivot, aim_object_at_point, orient_to_vector, rotate_axis, get_orientation, get_dist_between_two_objects, get_center_point, get_bounding_box, sort_by_bounding_box_value, align_using_three_points, is_overlapping, check_objects_against_plane, get_vertex_positions, get_matching_verts, order_by_distance, align_vertices, get_translation, get_object_matrix, set_object_matrix

### `xform_utils/matrices.py` — Matrix utilities for Maya rigging and animation.
- `class MatricesError(RuntimeError)`
- `class Matrices(_MatrixMath, _DagTransforms, _NodeBuilders, ptk.HelpMixin, _MatricesInternal)`
  - methods: get_matrix, set_matrix, identity, to_mmatrix, local_matrix, from_srt, decompose, inverse, safe_inverse, mult, world_to_local, local_to_world, extract_translation, is_identity, set_offset_parent_matrix, bake_world_matrix_to_transform, freeze_to_offset_parent_matrix, ensure_node, build_mult_matrix_chain, drive_with_offset_parent_matrix, build_space_switch, build_aim_matrix, build_ikfk_blend

### `xform_utils/pivot_watcher.py` — Real-time pivot-change notifier built on :class:`ScriptJobManager`.
- `class PivotWatcher(_PivotWatcherInternal)`
  - methods: owner, started, start, stop, attach_widget
