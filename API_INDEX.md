# mayatk — API Index

_Auto-generated. Do not edit by hand. Compact symbol index — grep this for a name; for full signatures/docs, slice [API_REGISTRY.md](API_REGISTRY.md) (never Read it whole)._

_Generated: 2026-07-10_

### `anim_utils/_anim_utils.py`
- `class AnimUtils(_AnimUtilsMixin, ptk.HelpMixin)`
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
  - methods: header_init, b000_init, b000, cmb000_init, le001_init, b001_init, b001, b003, b004_init, b004, b005, b006_init, b006, b007, b008_init, b008

### `anim_utils/blendshape_animator/creator.py` — Creates in-between target meshes for custom blendShape animation curves.
- `class Creator(ptk.LoggingMixin)`
  - methods: create_weight_based_tweens, create_frame_based_tween, tag_tween_mesh, get_existing_weights, find_nearby_weight

### `anim_utils/blendshape_animator/helpers.py` — Shared helpers internal to the blendshape_animator subpackage.
- `list_history(node: str, type_filter: Optional[str] = None) -> List[str]`

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

### `anim_utils/blendshape_animator/weights.py` — Weight calculations and Maya-compatible precision handling for blendShape animation.
- `class Weights`
  - methods: round_weight, frame_to_weight, generate_weights

### `anim_utils/playblast_exporter.py` — Utilities for creating playblasts and alternative preview renders in Maya.
- `class PlayblastExporter`
  - methods: scene_name, create_playblast, render_with_arnold, export_variations

### `anim_utils/scale_keys.py` — Dedicated scale-keys module to keep AnimUtils lean and testable.
- `class ScaleKeys`
  - methods: execute, scale_keys

### `anim_utils/segment_keys.py`
- `class SegmentKeysInfo`
  - methods: get_time_ranges, print_time_ranges, format_time_ranges_text, format_time_ranges_html
- `class SegmentKeys(SegmentKeysInfo)`
  - methods: collect_segments, get_scene_info, format_scene_info_text, format_scene_info_html, print_scene_info, group_segments, merge_groups_sharing_curves, shift_curves, execute_stagger

### `anim_utils/shots/_detection.py` — Shot-region detection — Maya animation-graph analysis.
- `resolve_to_transform(node, cache=None, _depth=0)`
- `detect_shot_regions(objects: Optional[List[str]] = None, gap_threshold: float = 5.0, ignore: Optional[str] = None, motion_rate: float = 0.001, min_duration: float = 2.0) -> List[Dict[str, Any]]`
- `regions_from_selected_keys(gap_threshold: float = 5.0, key_filter: str = 'all') -> List[Dict[str, Any]]`

### `anim_utils/shots/_shot_apply.py` — Commit resolved :class:`MovePlan`\ s to the Maya scene.
- `apply(store: ShotStore, plan: MovePlan, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> None`

### `anim_utils/shots/_shot_plan.py` — Pure planning layer for multi-shot topology transformations.
- `plan_respace(store: ShotStore, gap: float, start_frame: float) -> MovePlan`
- `plan_ripple_downstream(store: ShotStore, pivot_shot_id: int, after_frame: float, delta: float) -> MovePlan`
- `plan_ripple_upstream(store: ShotStore, pivot_shot_id: int, before_frame: float, delta: float) -> MovePlan`
- `class ShotMove`
  - methods: delta, moves
- `class MovePlan`

### `anim_utils/shots/_shots.py` — Shared shot data model and persistent store.
- `resolve_clip_specs(shots: List['ShotBlock'], strategy: str = 'name') -> List[Tuple[str, int, int]]`
- `class ScenePersistence(Protocol)`
  - methods: save, load
- `class MayaScenePersistence`
  - methods: save, load, remove_callbacks
- `class ShotBlock`
  - methods: duration, classify_objects
- `class StoreEvent`
- `class ShotDefined(StoreEvent)`
- `class ShotUpdated(StoreEvent)`
- `class ShotRemoved(StoreEvent)`
- `class ActiveShotChanged(StoreEvent)`
- `class SettingsChanged(StoreEvent)`
- `class BatchComplete(StoreEvent)`
- `class StoreInvalidated(StoreEvent)`
- `class ShotStore`
  - methods: active_shot_id, set_active_shot, notify_settings_changed, add_listener, remove_listener, batch_update, is_gap_locked, lock_gap, unlock_gap, lock_all_gaps, unlock_all_gaps, set_persistence, active, set_active, clear_active, add_invalidation_listener, remove_invalidation_listener, snap, compute_gap, sorted_shots, shot_by_id, shot_by_name, define_shot, update_shot, remove_shot, append_shot, is_object_hidden, set_object_hidden, is_object_pinned, set_object_pinned, remove_object_from_shots, to_dict, to_export_view, publish_export_view, refresh_export_view, enable_auto_export, disable_auto_export, from_dict, rescale_to_fps, mark_dirty, save, has_animation, is_detection_relevant, detect_regions, detect_and_define, assess

### `anim_utils/shots/shot_manifest/_shot_manifest.py` — Shot Manifest — parse structured CSVs and populate a ShotStore.
- `resolve_duration(step: BuilderStep, initial_shot_length: float, fit_mode: FitMode, fps: float) -> Tuple[float, float, float]`
- `detect_behaviors(text: str) -> List[str]`
- `parse_csv(filepath: str, columns: Optional[ColumnMap] = None, post_process: Optional[Callable[[BuilderStep], None]] = None) -> List[BuilderStep]`
- `class BuilderObject`
- `class BuilderStep`
  - methods: display_text, from_detection
- `class PlannedShot`
- `class ObjectStatus`
- `class StepStatus`
  - methods: status, missing_count, total_count
- `class ColumnMap(SchemaSpec)`
  - methods: to_dict, from_dict
- `class ShotManifest`
  - methods: sync, rewire_audio, update, assess, from_csv

### `anim_utils/shots/shot_manifest/behaviors/_behaviors.py` — Behaviors — load and apply YAML keying recipes.
- `templates() -> TemplateSet`
- `load_behavior(name: str, search_path: Optional[Path] = None) -> Dict[str, Any]`
- `list_behaviors(search_path: Optional[Path] = None, kind: Optional[str] = None) -> List[str]`
- `resolve_keys(block_def: Dict, start: float, end: float) -> List[Dict[str, Any]]`
- `apply_behavior(obj: str, behavior_name: str, start: float, end: float, attrs: Optional[List[str]] = None, search_path: Optional[Path] = None, source_path: str = '', anchor_override: Optional[str] = None) -> None`
- `verify_behavior(obj: str, behavior_name: str, start: float, end: float, search_path: Optional[Path] = None, keyframe_fn: Optional[Any] = None, anchor_override: Optional[Any] = None) -> bool`
- `apply_audio_clip(obj: str, start: float, end: float, source_path: str = '') -> None`
- `compute_duration(behavior_entries: List[Dict[str, str]], fallback: float = 30, fps: Optional[float] = None) -> float`
- `apply_to_shots(shots: list, apply_fn, exists_fn=None, has_keys_fn=None, store=None) -> Dict[str, list]`

### `anim_utils/shots/shot_manifest/behaviors/_spec.py` — Schema for a *behavior* template file, defined as a dataclass.
- `validate_duration(value: Any) -> List[str]`
- `validate_verify(value: Any) -> List[str]`
- `validate_attributes(value: Any) -> List[str]`
- `format_markdown() -> str`
- `class BehaviorSpec(SchemaSpec)`

### `anim_utils/shots/shot_manifest/manifest_data.py` — Constants, column layout, and pure helper functions for the Shot Manifest UI.
- `fmt_behavior(name: str) -> str`
- `format_behavior_html(behaviors, broken=(), status_color=None) -> str`
- `try_load_maya_icons()`
- `prune_to_top_boundaries(region_starts: List[float], n_steps: int) -> List[float]`

### `anim_utils/shots/shot_manifest/mapping/_mapping.py` — CSV mapping resolver — interprets JSON mapping files.
- `templates() -> TemplateSet`
- `discover(directory: Optional[str] = None) -> List[str]`
- `load_mapping(name: str, directory: Optional[str] = None) -> Dict[str, Any]`
- `resolve(csv_path: str, mapping: Optional[Dict[str, Any]] = None, *, name: Optional[str] = None, directory: Optional[str] = None) -> List[BuilderStep]`

### `anim_utils/shots/shot_manifest/mapping/_spec.py` — Schema for a CSV *mapping* file, defined as a dataclass.
- `validate_audio_resolve(value: Any) -> List[str]`
- `validate_default_behaviors(value: Any) -> List[str]`
- `format_markdown() -> str`
- `class AudioMethod`
- `class MappingSpec(SchemaSpec)`

### `anim_utils/shots/shot_manifest/range_resolver.py` — Range resolution algorithm for the Shot Manifest.
- `resolve_ranges(steps: List[BuilderStep], user_ranges: Dict[str, Tuple[Optional[float], Optional[float]]], gap_starts: List[float], gap_end_map: Dict[float, float], gap: float, use_selected_keys: bool, last_resolved: List[Tuple[str, float, Optional[float], bool]], from_step_idx: int = 0, default_duration: float = 0) -> List[Tuple[str, float, Optional[float], bool]]`

### `anim_utils/shots/shot_manifest/shot_manifest_slots.py` — Switchboard slots for the Shot Manifest UI.
- `class ShotManifestController(ManifestTableMixin, ptk.LoggingMixin)`
  - methods: detect, remove_callbacks, browse_csv, build, assess
- `class ShotManifestSlots(ptk.LoggingMixin)`
  - methods: header_init, btn_expand_missing, btn_expand_extra, btn_settings, b002, b003

### `anim_utils/shots/shot_manifest/table_presenter.py` — Tree-widget presentation mixin for the Shot Manifest controller.
- `class ManifestTableMixin`
  - methods: expand_missing, expand_extra

### `anim_utils/shots/shot_sequencer/_shot_sequencer.py` — Shot Sequencer — manages per-shot animation with ripple editing.
- `class ShotSequencer`
  - methods: shots, hidden_objects, markers, is_object_hidden, set_object_hidden, sorted_shots, shot_by_id, shot_by_name, define_shot, from_current_range, reconcile_all_shots, collect_object_segments, collect_shot_sequences, move_sequences_to_shot, fit_shot_to_content, trim_shot_to_content, extend_shot_to_fit, detect_shots, detect_next_shot, move_object_keys, move_stepped_keys, move_object_in_shot, scale_object_keys, move_shot, slide_shot, ripple_downstream, ripple_upstream, expand_shot, resize_object, set_shot_duration, resize_shot, set_shot_start, reorder_shots, move_shot_to_position, respace, apply_gap, to_dict, from_dict

### `anim_utils/shots/shot_sequencer/clip_motion.py` — Clip motion, resize, and key-scaling logic for the shot sequencer.
- `curves_for_attr(obj_name: str, attr_name: str) -> list`
- `scale_attribute_keys(obj_name: str, attr_name: str, old_start: float, old_end: float, new_start: float, new_end: float) -> None`
- `class ClipMotionMixin`
  - methods: on_clip_resized, on_clip_moved, on_clips_batch_moved, on_keys_moved, on_keys_deleted

### `anim_utils/shots/shot_sequencer/gap_manager.py` — Gap and range-highlight handlers for the shot sequencer controller.
- `class GapManagerMixin`
  - methods: on_range_highlight_changed, on_gap_resized, on_gap_left_resized, on_gap_moved, on_gap_lock_changed, on_gap_lock_all, on_gap_unlock_all

### `anim_utils/shots/shot_sequencer/marker_manager.py` — Marker persistence for the shot sequencer controller.
- `class MarkerManagerMixin`
  - methods: on_marker_added, on_marker_moved, on_marker_changed, on_marker_removed

### `anim_utils/shots/shot_sequencer/segment_collector.py` — Segment collection and attribute extraction for the shot sequencer.
- `collect_segments(sequencer, shot, visible_shots, segment_cache, shifted_out_keys, logger)`
- `active_object_set(shot, segments_by_shot) -> set`
- `extract_attributes(segments) -> list`
- `build_curve_preview(crv, t_start, t_end)`

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
- `node_ref(node: str) -> Dict[str, Optional[str]]`
- `resolve_ref(ref: Optional[Dict[str, Optional[str]]]) -> Optional[str]`
- `plug_ref(plug: str) -> Dict[str, Optional[str]]`
- `resolve_plug(ref: Optional[Dict[str, Optional[str]]]) -> Optional[str]`
- `stash_curve(curve: str) -> dict`
- `unstash_curve(record: dict, warnings: Optional[List[str]] = None, fallback_dst: Optional[str] = None) -> Optional[str]`
- `discard_stash(record: dict) -> None`
- `collect_upstream_curves(plug: str, passthrough_types: Set[str]) -> List[str]`
- `snapshot_connections(plug: str) -> List[List[dict]]`
- `restore_session(session: dict) -> RestoreResult`
- `class BakeSessionStore`
  - methods: load, save, push, peek, pop, list_ids, new_session_id
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
  - methods: get_snap_frames, set_snap_frames, validate_track_id, normalize_track_id, attr_for, track_id_from_attr, find_carriers, list_track_attrs, load_file_map, set_path, get_path, remove_path, get_fps, cached_waveform, clear_waveform_cache, audio_duration_frames, ensure_track_attr, has_track, list_tracks, read_keys, read_events, write_key, remove_key, clear_keys, shift_keys_in_range, tracks_on_at_frame, bake_manifest, delete_track, rename_track, show_track_attrs, hide_track_attrs, sync, find_dg_node_for_track, is_managed_dg, batch, detect_legacy, migrate_legacy_triggers

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
- `batch(auto_sync: bool = True, undo: bool = True) -> _BatchContext`

### `audio_utils/compositor.py` — Compositor — derives DG audio nodes from keyed track events.
- `is_managed_dg(node: str) -> bool`
- `find_dg_node_for_track(track_id: str) -> Optional[str]`
- `sync(tracks: Optional[List[str]] = None, carrier: Optional[str] = None) -> dict`

### `audio_utils/migrate.py` — One-shot migration from legacy single-enum carriers to per-track schema.
- `detect_legacy(obj: str, category: str = 'audio') -> bool`
- `migrate_legacy_triggers(obj: str, category: str = 'audio', keep_old_attrs: bool = False) -> List[str]`

### `audio_utils/nodes.py` — Low-level DG audio node primitives.
- `resolve_playable_path(audio_path: str, cache_dir: Optional[str] = None) -> Optional[str]`
- `workspace_sound_dir() -> Optional[str]`
- `create_dg(file_path: str, name: Optional[str] = None, offset: float = 0, track_id: Optional[str] = None) -> Optional[str]`
- `configure_dg(node_name: str, file_path: str, offset: float) -> None`
- `query_duration(node_name: str) -> float`

### `audio_utils/segments/discovery.py` — Segment discovery from the per-track keyed canonical store.
- `collect_all_segments(scene_start: Optional[float] = None, scene_end: Optional[float] = None, include_waveform: bool = True, carrier: Optional[str] = None) -> List[AudioSegment]`
- `collect_segments_for_track(track_id: str, include_waveform: bool = True, carrier: Optional[str] = None) -> List[AudioSegment]`
- `class AudioSegment`
  - methods: is_audio

### `cam_utils/_cam_utils.py`
- `class CamUtils(ptk.HelpMixin)`
  - methods: group_cameras, toggle_safe_frames, get_current_cam, create_camera_from_view, adjust_camera_clipping, switch_viewport_camera

### `core_utils/_core_utils.py`
- `as_strings(nodes) -> List[str]`
- `short_name(node) -> str`
- `leaf_name(node) -> str`
- `get_bounding_box(node, world: bool = True) -> BoundingBox`
- `class BoundingBox`
- `class CoreUtils(ptk.CoreUtils, _CoreUtilsInternal)`
  - methods: undo_chunk, suspended_refresh, selected, undoable, reparent, wrap_control, confirm_existence, get_mfn_mesh, get_array_type, convert_array_type, get_parameter_mapping, set_parameter_mapping, build_mesh_similarity_mapping, get_mel_globals, reorder_objects

### `core_utils/auto_instancer/_auto_instancer.py` — Scene auto-instancer: convert geometrically identical meshes to instances.
- `auto_instance(nodes: Optional[Sequence[object]] = None, tolerance: float = 0.001, scale_tolerance: Optional[float] = None, require_same_material: Union[bool, int] = True, check_uvs: bool = False, check_hierarchy: bool = False, separate_combined: bool = False, combine_assemblies: bool = True, combine_non_instanced: bool = True, combine_by_material: bool = True, combine_by_distance: bool = True, combine_distance_threshold: float = 10000.0, search_radius_mult: float = 1.5, is_static: bool = True, needs_individual: bool = False, will_be_lightmapped: bool = False, can_gpu_instance: bool = True, verbose: bool = True, log_level: str = 'WARNING') -> List[str]`
- `class InstanceCandidate`
  - methods: transform, exists
- `class InstanceGroup`
- `class AutoInstancer(ptk.LoggingMixin)`
  - methods: tolerance, scale_tolerance, require_same_material, check_uvs, combine_assemblies, search_radius_mult, verbose, run, find_instance_groups

### `core_utils/auto_instancer/assembly_reconstructor.py` — Logic for separating and reassembling mesh assemblies.
- `class AssemblyReconstructor`
  - methods: separate_combined_meshes, cleanup_empty_sources, cleanup_empty_assembly_groups, center_transform_on_geometry, canonicalize_transform, canonicalize_leaf_meshes, reassemble_assemblies, combine_reassembled_assemblies

### `core_utils/auto_instancer/geometry_matcher.py` — Geometry analysis and matching logic for AutoInstancer.
- `mesh_points(shape, world: bool = False)`
- `mesh_triangles(shape)`
- `mesh_uv_set_names(shape)`
- `mesh_get_uvs(shape, uv_set=None)`
- `mesh_num_uvs(shape, uv_set=None)`
- `calculate_mesh_volume(node: str) -> float`
- `class ShellInfo`
- `class GeometryMatcher`
  - methods: clear_cache, quantize, get_pca_basis, get_mesh_signature, are_meshes_identical, get_hierarchy_signature, are_meshes_identical_with_transform, are_hierarchies_identical

### `core_utils/auto_instancer/instancing_strategy.py` — Instancing strategy logic for AutoInstancer.
- `class StrategyType(Enum)`
- `class StrategyConfig`
- `class InstancingStrategy`
  - methods: evaluate

### `core_utils/components.py`
- `class GetComponentsMixin`
  - methods: get_component_type, convert_alias, convert_component_type, get_component_index, convert_int_to_component, filter_components, get_components
- `class Components(GetComponentsMixin, ptk.HelpMixin)`
  - methods: map_components_to_objects, get_contiguous_edges, get_contiguous_islands, get_islands, get_border_components, get_furthest_vertices, get_closest_verts, get_closest_vertex, get_vertices_within_threshold, adjusted_distance_between_vertices, bridge_connected_edges, get_edge_path, get_shortest_path, get_normal, get_normal_vector, get_normal_angle, get_edges_by_normal_angle, set_edge_hardness, get_faces_with_similar_normals, average_normals, transfer_normals, filter_components_by_connection_count, get_vertex_normal, get_vector_from_components, crease_edges, get_creased_edges, transfer_creased_edges

### `core_utils/diagnostics/animation_diag.py` — Animation-curve diagnostics and optional repair helpers.
- `class AnimCurveDiagnostics`
  - methods: repair_corrupted_curves, repair_visibility_tangents

### `core_utils/diagnostics/mesh_diag.py` — Mesh diagnostics and repair helpers.
- `class MeshDiagnostics`
  - methods: clean_geometry, get_ngons

### `core_utils/diagnostics/scene_diag.py` — Scene diagnostics and repair helpers.
- `class SceneDiagnostics`
  - methods: fix_ocio, fix_missing_color_spaces, fix_unknown_plugins, remove_xgen_expressions, cleanup_scene
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
- `class SceneAnalyzer(ptk.LoggingMixin)`
  - methods: run_audit, format_audit_text, format_audit_html, analyze, generate_report, print_report

### `core_utils/diagnostics/transform_diag.py`
- `class TransformDiagnostics`
  - methods: fix_non_orthogonal_axes

### `core_utils/diagnostics/uv_diag.py` — UV diagnostics and repair helpers.
- `class UvSetCleanupResult`
- `class UvDiagnostics`
  - methods: find_lightmap_uv_set, is_bakeable_lightmap, cleanup_uv_sets

### `core_utils/mash.py`
- `class MashNetworkNodes(object)`
  - methods: as_tuple
- `class MashToolkit(object)`
  - methods: ensure_plugin_loaded, create_network, bake_instancer

### `core_utils/preview.py` — Hermetic preview with replay-on-commit (H1 design).
- `cleanup_all_previews() -> None`
- `apply_result_selection(widget, results, *, object_mode: bool = False, defer: bool = False) -> None`
- `class OperationError(Exception)`
- `class CleanupContract`
  - methods: add_file, record_modification, rollback
- `class Preview`
  - methods: cleanup_all_instances, init_show_hide_behavior, conditionally_enable, conditionally_disable, toggle, validate_operation, enable, refresh, disable, finalize_changes, cleanup, enabled, operated_object_count, get_operated_objects

### `core_utils/script_job_manager.py` — Centralized Maya event subscription manager.
- `class ScriptJobManager`
  - methods: instance, reset, subscribe, add_om_callback, unsubscribe, unsubscribe_all, connect_cleanup, suppress, resume, status, print_status, teardown

### `display_utils/_display_utils.py`
- `class DisplayUtils(ptk.HelpMixin)`
  - methods: add_to_isolation, is_templated, set_visibility, get_visible_geometry, add_to_isolation_set, reset_viewport

### `display_utils/color_id.py`
- `class ColorUtils`
  - methods: assign_material, set_color_attribute, get_material_color, get_wireframe_color, get_vertex_color, set_vertex_color, get_color_difference
- `class ColorId(ColorUtils)`
  - methods: apply_color, get_objects_by_color, reset_colors, reset_vertex_colors
- `class ColorIdSlots(ColorId)`
  - methods: header_init, selected_objects, selected_button, target_color, b000, b001, b002, b003

### `display_utils/exploded_view.py`
- `class ExplodedView`
  - methods: objects, calculate_repulsive_force_vectorized, arrange_objects, explode, un_explode, toggle_explode, un_explode_all
- `class ExplodedViewSlots(ExplodedView)`
  - methods: header_init, b000, b001, b002, b003

### `edit_utils/_edit_utils.py`
- `class EditUtils(ptk.HelpMixin)`
  - methods: combine_objects, group_objects, separate_objects, merge_vertices, merge_vertex_pairs, detach_components, decimate, dissolve_coplanar, get_all_faces_on_axis, cut_along_axis, delete_along_axis, mirror, separate_mirrored_mesh, get_overlapping_duplicates, find_non_manifold_vertex, split_non_manifold_vertex, get_overlapping_vertices, get_overlapping_faces, get_similar_mesh, get_similar_topo, invert_geometry, invert_components, delete_selected, create_curve_from_edges

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
- `class CurtainMesh(ptk.CurtainDrape)`
  - methods: create, build
- `class CurtainRig`
  - methods: attach
- `class CurtainSlots(ptk.LoggingMixin)`
  - methods: header_init, cmb000_init, b001, b002, perform_operation

### `edit_utils/cut_on_axis.py`
- `class CutOnAxis`
  - methods: perform_cut_on_axis
- `class CutOnAxisSlots`
  - methods: header_init, perform_operation

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
  - methods: header_init, b001, perform_operation, regroup_copies

### `edit_utils/dynamic_pipe.py`
- `class DynamicPipe`
  - methods: create_pipe_geometry
- `class DynamicPipeSlots`
  - methods: header_init, b000

### `edit_utils/macro_manager/macro_manager_slots.py` — UI slots for the Macro Manager panel.
- `class MacroManagerSlots`
  - methods: header_init, cmb000_init, cmb000, tbl000_init

### `edit_utils/macros.py`
- `class MacroManager(ptk.HelpMixin)`
  - methods: set_macros, call_with_input, set_macro, list_available_macros, macro_label, macro_category, list_categories, macro_help, get_current_bindings, apply_bindings, clear_hotkey, unset_macro, find_conflicts, qt_sequence_to_maya_key, maya_key_to_qt_sequence, list_presets, load_preset, save_preset, delete_preset, get_active_preset, set_active_preset, apply_saved_macros
- `class DisplayMacros`
  - methods: m_component_id_display, m_normals_display, m_soft_edge_display, m_toggle_visibility, m_toggle_uv_border_edges, m_back_face_culling, m_isolate_selected, m_cycle_display_state, m_wireframe_toggle, m_grid_and_image_planes, m_frame, m_smooth_preview, m_wireframe, m_material_override, m_shading, m_lighting
- `class EditMacros`
  - methods: m_group, m_combine, m_boolean, m_lock_vertex_normals, m_paste_and_rename, m_multi_component, m_merge_vertices
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
  - methods: header_init, perform_operation

### `edit_utils/naming/_naming.py`
- `class Naming(ptk.HelpMixin)`
  - methods: rename, generate_unique_name, strip_illegal_chars, strip_chars, set_case, suffix_by_type, append_location_based_suffix

### `edit_utils/naming/naming_slots.py`
- `class NamingSlots(Naming, ptk.LoggingMixin)`
  - methods: header_init, valid_suffixes, txt000_init, txt000, txt001_init, txt001, tb000_init, tb000, tb001_init, tb001, tb002_init, tb002, tb003_init, tb003

### `edit_utils/primitives.py` — Primitive creation utilities for Maya.
- `class Primitives`
  - methods: create_default_primitive, create_circle

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
  - methods: get_env_info, default_artifact_dir, append_maya_paths, load_plugin, vray_plugin, get_recent_files, get_recent_projects, find_autosave_directories, get_recent_autosave, find_workspaces, get_workspace_scenes, find_workspace_using_path, reference_scene, remove_reference, is_referenced, get_reference_nodes, list_references, export_scene_as_fbx, sanitize_namespace, resolve_file_path_in_workspaces, get_workspace_file_cache, matches_autosave_pattern, save_scene_backup, find_original_for_autosave, save_autosave_to_original

### `env_utils/blender_bridge/_blender_bridge.py` — Blender bridge engine -- export the Maya selection and run a chosen import template in Blender.
- `list_templates() -> List[Path]`
- `template_modes(template_path: Path) -> Tuple[str, ...]`
- `list_template_modes() -> List[Tuple[str, str]]`
- `class BlenderBridge(MayaExportMixin, ptk.ScriptLaunchBridge)`
  - methods: blender_path, params_defaults, render_context

### `env_utils/blender_bridge/blender_bridge_slots.py` — Slots for the Blender bridge panel.
- `class BlenderBridgeSlots(MayaBridgeSlotsBase)`
  - methods: params_module, template_dir, make_bridge, list_template_modes, b000

### `env_utils/blender_bridge/parameters.py` — Registry of user-tunable Blender-bridge parameters exposed to the panel.
- `referenced_keys(script_text: str) -> 'set[str]'`
- `defaults() -> 'dict[str, Any]'`
- `render_context(values: 'dict[str, Any]') -> 'dict[str, str]'`

### `env_utils/blender_bridge/templates/import.py` — Import the bridged FBX into Blender, with optional clean-slate and frame-on-import behaviors.
- `main()`

### `env_utils/devtools.py`
- `class DevTools(CoreUtils)`
  - methods: echo_all, find_mel, find_python, find, grep_maya_dir, grep_mel_procs, read_mel_proc, find_all, list_mel_globals, get_mel_global, source_mel
- `class WidgetInspector(CoreUtils)`
  - methods: from_maya_control, from_mel_global, main_window, walk, find_children_by_type, find_child_by_name, dump_tree, dump_properties, list_signals, list_slots, find_by_property, snapshot, diff_snapshots, connect_signal_logger, dump_actions, find_item_views, dump_model, get_selection_model

### `env_utils/fbx_utils.py`
- `class FbxUtils(ptk.HelpMixin)`
  - methods: load_plugin, set_fbx_options, load_preset, export, reset_takes, apply_takes, apply_takes_from_node, run_export_preparers, register_export_preparer, unregister_export_preparer, enable_auto_takes, disable_auto_takes, is_auto_takes_enabled

### `env_utils/handoff_export.py` — Maya-side selection + FBX-export hooks shared by the hand-off bridge engines.
- `class MayaExportMixin`

### `env_utils/hierarchy_manager/_hierarchy_manager.py`
- `get_clean_node_name(node) -> str`
- `get_clean_node_name_from_string(node_name: str) -> str`
- `clean_hierarchy_path(path: str) -> str`
- `format_component(name: str, strip_namespaces: bool = False) -> str`
- `is_default_maya_camera(path: str, node) -> bool`
- `should_keep_node_by_type(node, node_types: List[str], exclude: bool = True) -> bool`
- `filter_path_map_by_cameras(path_map: Dict[str, Any]) -> Dict[str, Any]`
- `filter_path_map_by_types(path_map: Dict[str, Any], node_types: List[str], exclude: bool = True) -> Dict[str, Any]`
- `select_objects_in_maya(object_names: List[str]) -> int`
- `class HierarchyMapBuilder`
  - methods: build_path_map, build_path_map_from_nodes
- `class MayaObjectMatcher(ptk.LoggingMixin)`
  - methods: find_matches
- `class HierarchyManager(ptk.LoggingMixin)`
  - methods: analyze_hierarchies, create_stubs, quarantine_extras, fix_fuzzy_renames, fix_reparented
- `class ObjectSwapper(ptk.LoggingMixin)`
  - methods: push_objects_to_scene, pull_objects_from_scene

### `env_utils/hierarchy_manager/hierarchy_manager_slots.py`
- `class HierarchyManagerController(ptk.LoggingMixin)`
  - methods: workspace, reference_path, analyze_hierarchies, pull_objects, repair_hierarchies, select_objects_in_maya, populate_reference_tree, refresh_trees, is_path_ignored, clear_ignored_paths, log_diff_results, get_recent_reference_scenes, save_recent_reference_scene
- `class HierarchyManagerSlots(ptk.LoggingMixin)`
  - methods: header_init, tree000_init, tree001_init, cmb_diff_options_init, cmb_pull_options_init, tb003_init, tb001, tb002, tb003, b003, b005, b006, b007, b008, b009, b011, b012, b013, b014, b015, b016, b018, b017, count_tree_items

### `env_utils/hierarchy_manager/hierarchy_sidecar.py` — Hierarchy sidecar manifest management.
- `class HierarchySidecar`
  - methods: base_stem, manifest_path_for, diff_report_path_for, find_legacy_manifest, ensure_base_name, rename, build_clean_path_set, expand_to_descendants, get_top_level, detect_reparenting, write_manifest, read_manifest, count_descendants, write_diff_report, clean_stale_diff, build_full_path_set, compare

### `env_utils/hierarchy_manager/tree_renderer.py` — Tree rendering, formatting, and selection management for the hierarchy manager UI.
- `class HierarchyTreeRenderer(ptk.LoggingMixin)`
  - methods: populate_current_scene_tree, populate_reference_tree, show_reference_placeholder, show_reference_error, populate_tree_with_hierarchy, apply_difference_formatting, clear_tree_colors, format_tree_differences, apply_ignore_styling, build_item_path, find_tree_item_by_name, get_selected_tree_items, get_selected_object_names

### `env_utils/hierarchy_manager/tree_utils.py` — Tree widget utilities for hierarchy manager UI operations.
- `get_selected_object_names(tree_widget) -> List[str]`
- `get_selected_tree_items(tree_widget) -> list`
- `find_tree_item_by_name(tree_widget, object_name: str)`
- `build_hierarchy_structure(objects: list) -> Tuple[Dict[str, Dict], List[str]]`
- `class TreePathMatcher(ptk.LoggingMixin)`
  - methods: build_tree_index, find_path_matches, log_matching_debug, log_tree_index_debug

### `env_utils/maya_connection.py` — Maya Connection Module
- `open_command_ports(**kwargs)`
- `toggle_command_ports(mel_port=7001, python_port=7002)`
- `open_available_command_ports(mel_start=7001, python_start=7002, max_offset=50, tag_window=True)`
- `class MayaConnection`
  - methods: get_instance, open_command_ports, close_command_ports, open_available_command_ports, toggle_command_ports, reload_modules, connect, get_pid_from_port, close_instance, get_available_port, ensure_connection, execute, get_script_editor_output, execute_and_capture_editor_output, clear_script_editor, shutdown, disconnect

### `env_utils/namespace_sandbox.py`
- `class FBXImporter`
  - methods: is_supported_file, import_with_namespace, import_for_analysis
- `class MayaImporter`
  - methods: is_supported_file, import_with_namespace, import_for_analysis
- `class CameraTracker(ptk.LoggingMixin)`
  - methods: capture_pre_import_state, capture_post_import_state, get_imported_cameras, cleanup_imported_cameras, reset
- `class NamespaceSandbox(ptk.LoggingMixin)`
  - methods: import_with_namespace, import_for_analysis, get_supported_formats, find_objects_in_namespace, find_objects_with_hierarchy_matching, get_namespace_hierarchy, cleanup_import, cleanup_namespace, cleanup_all_namespaces, get_imported_cameras, cleanup_imported_cameras, cleanup_all_temp_namespaces_force, export_objects_to_temp, import_objects_for_swapping, import_to_target_scene, cleanup_analysis_namespace

### `env_utils/reference_manager.py`
- `class AssemblyManager`
  - methods: current_references, create_assembly_definition, set_active_representation, convert_references_to_assemblies
- `class ReferenceManager(WorkspaceManager, ptk.HelpMixin, ptk.LoggingMixin)`
  - methods: current_references, sanitize_namespace, add_reference, import_references, update_references, get_reference_top_transforms, get_reference_display_mode, set_reference_display_mode, remove_references
- `class ReferenceManagerController(ReferenceManager, ptk.LoggingMixin)`
  - methods: current_working_dir, block_table_selection_method, prepare_item_for_edit, restore_item_display, is_item_being_edited, handle_item_selection, sync_selection_to_references, update_current_dir, set_workspace, refresh_file_list, update_table, open_scene, unreference_all, unlink_all, unlink_references, convert_to_assembly, save_scene, rename_scene, delete_scene
- `class ReferenceManagerSlots(ptk.HelpMixin, ptk.LoggingMixin)`
  - methods: header_init, tbl000_init, tbl000_item_double_clicked, tbl000_item_changed, tbl000_editor_closed, btn_open_file_location, txt000_init, txt001_init, txt001, cmb000_init, cmb000, chk000, chk003, chk_ignore_case, chk_hide_binary, chk_filter_suffix, chk_hide_suffix, chk_hide_extension, chk_show_notes_column, txt_suffix, chk_filter_folder_structure, b000, b006, b001, btn_open_scene, btn_toggle_reference, btn_unlink_import, btn_save_scene, btn_refresh, btn_convert_assembly, btn_unlink_import_all, btn_unreference_all

### `env_utils/scene_exporter/_scene_exporter.py`
- `class SceneExporter(ptk.LoggingMixin)`
  - methods: perform_export, generate_export_path, format_export_name, generate_log_file_path, setup_file_logging, close_file_handlers, load_fbx_export_preset, verify_fbx_preset
- `class SceneExporterSlots(SceneExporter)`
  - methods: workspace, presets, header_init, cmb000_init, txt000_init, txt001_init, cmb001_init, cmb002_init, cmb004_init, b000, b010, b003, b004, b005, b006, b007, b008, save_output_dir, save_output_name

### `env_utils/scene_exporter/task_factory.py`
- `class TaskFactory`
  - methods: run_tasks, run_tasks_by_category

### `env_utils/scene_exporter/task_manager.py`
- `class TaskManager(TaskFactory, _TaskActionsMixin, _TaskChecksMixin)`
  - methods: objects, task_definitions, check_definitions, definitions

### `env_utils/script_output.py`
- `show(*args, **kwargs)`
- `toggle(*args, **kwargs)`
- `class ScriptConsole(MayaQWidgetDockableMixin, QtWidgets.QDialog)`
  - methods: enterEvent, show_console

### `env_utils/unity_bridge/_unity_bridge.py` — Unity bridge engine -- export the Maya selection into a Unity project's Assets/.
- `list_delivery_modes() -> List[Tuple[str, str]]`
- `class UnityBridge(MayaExportMixin, ptk.HandoffBridge)`
  - methods: list_template_modes, params_defaults

### `env_utils/unity_bridge/parameters.py` — User-tunable parameters for the Maya->Unity bridge panel.
- `referenced_keys(script_text: str) -> 'set[str]'`
- `defaults() -> 'dict[str, Any]'`
- `render_context(values: 'dict[str, Any]') -> 'dict[str, str]'`

### `env_utils/unity_bridge/unity_bridge_slots.py` — Slots for the Unity bridge panel.
- `class UnityBridgeSlots(MayaBridgeSlotsBase)`
  - methods: params_module, template_dir, make_bridge, list_template_modes, default_output_dir, b000

### `env_utils/workspace_manager.py`
- `class WorkspaceManager(ptk.HelpMixin)`
  - methods: current_workspace, current_working_dir, recursive_search, ignore_empty_workspaces, workspace_files, find_available_workspaces, invalidate_workspace_files, resolve_file_path

### `env_utils/workspace_map.py`
- `class WorkspaceMap(WorkspaceManager, ptk.HelpMixin, ptk.LoggingMixin)`
  - methods: current_working_dir, recursive_search, workspace_data, invalidate_workspace_data, get_workspace_tree_data, get_filtered_workspaces, refresh_workspace_data
- `class WorkspaceMapController(WorkspaceMap, ptk.LoggingMixin)`
  - methods: update_current_dir, refresh_tree, handle_tree_selection
- `class WorkspaceMapSlots(ptk.HelpMixin, ptk.LoggingMixin)`
  - methods: header_init, txt000_init, txt001_init, tree000_init, filter_workspaces, chk000, browse_directory, set_to_workspace, btn_open_workspace, btn_explore_folder

### `light_utils/_light_utils.py`
- `class LightUtils(ptk.HelpMixin)`

### `light_utils/hdr_manager.py` — Arnold HDR environment manager.
- `class HdrManager(ptk.LoggingMixin, ptk.HelpMixin)`
  - methods: arnold_loaded, arnold_available, ensure_plugin_loaded, hdr_env, hdr_env_transform, hdr_file_node, hdr_file_path, visibility, set_hdr_map_visibility, sky_radius, preview, rotation, intensity, exposure, resolution, samples, diffuse, specular, create_network, clear
- `class HdrManagerSlots(ptk.LoggingMixin, ptk.HelpMixin)`
  - methods: header_init, cmb000_init, hdr_map, hdr_map_visibility, hdr_map_preview, cmb000, slider000, spn_intensity, spn_exposure, spn_resolution, spn_samples, spn_diffuse, spn_specular, add_hdr, open_sourceimages, clear_network, ctx_select_skydome, ctx_select_transform, ctx_select_file_node, ctx_reveal_in_explorer

### `light_utils/lightmap_baker/lightmap_baker.py` — High-level lightmap baking workflow for Maya -> game engines (Unity-first).
- `class LightmapBaker(ptk.LoggingMixin)`
  - methods: preset_store, from_preset, bake_fused, bake_separated, commit_unlit, revert_unlit, pack_atlas, commit_lightmap, revert_lightmap, revert
- `class LightmapBakerSlots(ptk.LoggingMixin, ptk.HelpMixin)`
  - methods: header_init, cmb000_init, cmb000, cmb001_init, cmb002_init, cmb_scope_init, cmb_resolution_init, txt000_init, b000, revert_to_source, open_sourceimages

### `mat_utils/_mat_utils.py`
- `class MatUtilsInternals(ptk.HelpMixin)`
  - methods: get_texture_file_node
- `class MatUtils(MatUtilsInternals)`
  - methods: resolve_path, get_mats, group_objects_by_material, get_texture_paths, get_texture_info, get_mat_info, format_texture_info_text, format_texture_info_html, format_mat_info_text, format_mat_info_html, get_scene_mats, get_connected_shaders, get_file_nodes, get_fav_mats, is_mat_assigned, is_connected, create_mat, assign_mat, get_shading_assignments, apply_shading_assignments, create_file_node, create_shading_group, create_stingray_shader, find_by_mat_id, collect_material_paths, remap_file_nodes, remap_texture_paths, is_duplicate_material, find_materials_with_duplicate_textures, reassign_duplicate_materials, filter_materials_by_objects, reload_textures, move_texture_files, copy_textures_to_sourceimages, find_texture_files, migrate_textures, move_unused_textures, get_mat_swatch_icon, convert_bump_to_normal, validate_normal_map_setup, graph_materials

### `mat_utils/arnold_bridge.py` — Arnold render-bridge management.
- `class ArnoldBridge(ptk.LoggingMixin)`
  - methods: add, remove, rebuild, get_bridge, has_bridge
- `class ArnoldBridgeSlots(ptk.LoggingMixin, ptk.HelpMixin)`
  - methods: header_init, cmb000_init, b000, b001, select_bridged

### `mat_utils/game_shader.py`
- `class GameShader(ptk.LoggingMixin)`
  - methods: create_network, setup_stringray_node, setup_standard_surface_node, setup_open_pbr_node, connect_stingray_nodes, connect_standard_surface_nodes, connect_open_pbr_nodes, filter_for_correct_normal_map, filter_for_correct_metallic_map, filter_for_mask_map, filter_for_correct_base_color_map
- `class GameShaderSlots(GameShader)`
  - methods: header_init, lbl_graph_material, mat_name, mat_prefix, mat_suffix, normal_map_type, output_extension, shader_type, cmb002_init, cmb003_init, txt002_init, b000

### `mat_utils/image_to_plane/_image_to_plane.py` — Map image files to textured polygon planes in Maya.
- `class ImageToPlane(ptk.LoggingMixin)`
  - methods: create, remove

### `mat_utils/image_to_plane/image_to_plane_slots.py` — Switchboard slots for the Image to Plane UI.
- `class ImageToPlaneSlots`
  - methods: header_init, txt_suffix_init

### `mat_utils/marmoset_bridge/_marmoset_bridge.py` — Maya-side glue for the Marmoset Toolbag engine.
- `build_bake_pairs_manifest(objects: Sequence[str], high_suffix: str, low_suffix: str) -> Dict[str, str]`
- `class MarmosetBridge(ptk.HandoffBridge)`
  - methods: toolbag_path, params_defaults, render_template

### `mat_utils/marmoset_bridge/_marmoset_engine.py` — Drive Marmoset Toolbag from the outside -- launch + templated automation.
- `list_templates() -> List[Path]`
- `template_modes(template_path: Path) -> Tuple[str, ...]`
- `list_template_modes() -> List[Tuple[str, str]]`
- `class MarmosetEngine(ptk.Deliverer, ptk.LoggingMixin)`
  - methods: toolbag_path, toolbag_log_path, preflight, deliver, send, render_template

### `mat_utils/marmoset_bridge/_toolbag_helpers.py` — Shared helpers for Marmoset Toolbag template scripts.
- `derive_per_run_log_path(manifest_path)`
- `begin_log(reference_path)`
- `log(msg)`
- `find_material(name, scene_mats)`
- `load_manifest(manifest_path)`
- `wire_materials_from_manifest(manifest_path, verbose=True)`
- `split_high_low(objects, high_suffix, low_suffix, pre_classified=None)`
- `collect_mesh_objects(root)`
- `apply_sky_preset(preset_path)`
- `frame_in_viewport()`

### `mat_utils/marmoset_bridge/marmoset_bridge_slots.py` — Slots for the Marmoset Toolbag bridge panel.
- `class MarmosetBridgeSlots(MayaBridgeSlotsBase)`
  - methods: params_module, template_dir, make_bridge, list_template_modes, select_initial_template_index, b000

### `mat_utils/marmoset_bridge/marmoset_rpc/connection.py` — JSON-RPC client bound to the marmoset_rpc Toolbag plugin.
- `class MarmosetConnection(RpcClient)`

### `mat_utils/marmoset_bridge/marmoset_rpc/installer.py` — Install the marmoset_rpc plugin into Toolbag's user plugin folder.
- `user_plugin_dir(toolbag_exe: Optional[str] = None) -> Optional[Path]`
- `is_installed(toolbag_exe: Optional[str] = None) -> bool`
- `install(toolbag_exe: Optional[str] = None, force: bool = False) -> Optional[Path]`
- `uninstall(toolbag_exe: Optional[str] = None) -> bool`

### `mat_utils/marmoset_bridge/marmoset_rpc/job.py` — One-shot batch pipeline for the marmoset_rpc bridge.
- `run_batch(calls: List[Call], host: str = '127.0.0.1', port: int = 8765, stop_on_error: bool = False) -> List[Result]`

### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/main_thread.py` — Main-thread marshalling for ops that touch Toolbag's API.
- `run_on_main_thread(fn, *args, timeout=_DEFAULT_TIMEOUT, **kwargs)`
- `is_main_thread_marshalling_active()`

### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/scene_ops.py` — Scene-inspection ops.
- `summary()`
- `list_materials()`

### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/ops/system_ops.py` — System-level ops: heartbeat, introspection, Toolbag version.
- `ping()`
- `list_ops()`
- `describe_op(op='')`
- `version()`

### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/registry.py` — Op registry for the marmoset_rpc plugin.
- `register(name)`
- `get(name)`
- `all_ops()`
- `describe(name=None)`
- `clear()`

### `mat_utils/marmoset_bridge/marmoset_rpc/plugin_src/marmoset_rpc/server.py` — HTTP JSON-RPC server for the marmoset_rpc plugin.
- `start_server(port=None, host='127.0.0.1')`
- `stop_server()`
- `is_running()`
- `autostart()`

### `mat_utils/marmoset_bridge/parameters.py` — Registry of user-tunable Marmoset Toolbag parameters exposed to the bridge UI.
- `referenced_keys(script_text: str) -> 'set[str]'`
- `defaults() -> 'dict[str, Any]'`
- `render_context(values: 'dict[str, Any]') -> 'dict[str, str]'`

### `mat_utils/marmoset_bridge/template_params.py` — Plain default values + literal formatting for Marmoset template tokens.
- `python_literal(value: Any) -> str`
- `defaults() -> Dict[str, Any]`
- `to_context(values: Dict[str, Any]) -> Dict[str, str]`

### `mat_utils/marmoset_bridge/templates/bake.py` — Bake high-poly detail into a low-poly target via Marmoset Toolbag.
- `main()`

### `mat_utils/marmoset_bridge/templates/import.py` — Open the model in Toolbag and wire materials from the manifest.
- `main()`

### `mat_utils/marmoset_bridge/templates/lookdev.py` — Open the model in Toolbag, apply a Sky preset, and frame the model.
- `main()`

### `mat_utils/marmoset_bridge/toolbag_log.py` — Marmoset Toolbag log-file resolution, classification, and live tailing.
- `resolve_toolbag_log_path(toolbag_exe: Optional[str]) -> Optional[str]`
- `classify_log_line(line: str) -> Optional[Tuple[str, str]]`
- `dispatch_log_lines(lines, logger) -> None`
- `start_toolbag_log_tail(log_path: str, start_offset: int, process, logger, poll_interval: float = 0.4, file_wait_timeout: float = 60.0)`

### `mat_utils/mat_manifest.py`
- `class MatManifest(ptk.HelpMixin)`
  - methods: build, restore

### `mat_utils/mat_snapshot.py` — Lightweight material state snapshot and restore.
- `class MatSnapshot`
  - methods: capture, restore

### `mat_utils/mat_transfer.py`
- `class MatTransfer(ptk.LoggingMixin)`
  - methods: is_material_related_node, get_material_assignments, collect_material_assignments, handle_object_materials

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
  - methods: get_stingray_mats, ensure_transparent_graph, create, ensure_connections, remove

### `mat_utils/render_opacity/render_opacity_slots.py` — Switchboard slots for the Render Opacity UI.
- `class RenderOpacitySlots`
  - methods: header_init, tb000_init, tb000

### `mat_utils/shader_attribute_map.py`
- `class ShaderAttributeMap`
  - methods: logical_channels, get_attr, get_mapping, add_shader_type, update_attr, as_dict

### `mat_utils/shader_remapper.py`
- `class ShaderRemapper(ptk.LoggingMixin)`
  - methods: remap_shaders

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
- `list_templates() -> List[Path]`
- `parse_template(template_path: Path) -> Dict[str, Any]`
- `list_template_modes() -> List[Tuple[str, str]]`
- `resolve_painter_log_path(painter_exe: Optional[str] = None) -> Optional[str]`
- `class SubstanceBridge(ptk.HandoffBridge)`
  - methods: painter_path, painter_log_path, instances, find_live_managed, send

### `mat_utils/substance_bridge/connection.py` — Substance 3D Painter connection module.
- `find_painter_exe() -> Optional[str]`
- `default_log_path() -> Optional[str]`
- `class OutputStream`
  - methods: push, subscribe, history, clear_history, wait_for, close, closed
- `class SubstanceConnection(ptk.LoggingMixin)`
  - methods: open, close, is_alive, attach

### `mat_utils/substance_bridge/parameters.py` — Registry of user-tunable Substance Painter parameters exposed to the bridge UI.
- `referenced_keys(script_text: str) -> 'set[str]'`
- `defaults() -> 'dict[str, Any]'`
- `render_cli_context(values: 'dict[str, Any]') -> 'dict[str, str]'`
- `render_js_context(values: 'dict[str, Any]') -> 'dict[str, str]'`

### `mat_utils/substance_bridge/substance_bridge_slots.py` — Slots for the Substance Painter bridge panel.
- `class SubstanceBridgeSlots(MayaBridgeSlotsBase)`
  - methods: params_module, template_dir, make_bridge, list_template_modes, select_initial_template_index, b000

### `mat_utils/substance_bridge/substance_rpc/client.py` — JSON-RPC 2.0 client for a Painter-side Python plugin.
- `class PainterRpcClient`
  - methods: url, ping, wait_until_ready, call, eval_js

### `mat_utils/texture_baker.py` — Bake an object's shaded surface (material under scene lighting) to a texture.
- `class TextureBaker(ptk.LoggingMixin)`
  - methods: arnold_available, bake, assign_to_diffuse, restore_diffuse_connections

### `mat_utils/texture_path_editor.py`
- `class TexturePathEditorSlots`
  - methods: header_init, tb_set_texture_directory_init, tb_find_and_copy_textures_init, tb_normalize_paths_init, tb_resolve_missing_textures_init, tbl000_init, open_source_images, reload_scene_textures, tb_set_texture_directory, tb_find_and_copy_textures, tb_normalize_paths, tb_resolve_missing_textures, select_textures_for_objects, select_broken_paths, select_absolute_paths, row_browse_for_file, select_material, select_file_node, row_show_in_hypershade, delete_file_node, refresh_texture_table, cleanup_scene_callbacks, setup_formatting, handle_cell_edit

### `node_utils/_node_utils.py`
- `class NodeUtils(ptk.HelpMixin)`
  - methods: get_type, get_inherited_types, is_mesh, is_locator, is_group, is_geometry, is_constraint, is_expression, is_ik_effector, is_driven_key_curve, is_muted, is_motion_path, is_ik_handle, get_constraint_targets, get_groups, get_parent, get_children, get_shapes, get_shape, is_intermediate, node_is, list_transforms, get_unique_children, get_transform_node, get_shape_node, get_history_node, create_render_node, get_connected_nodes, create_assembly, get_instances, replace_with_instances, instance, uninstance, filter_duplicate_instances

### `node_utils/attributes/_attributes.py` — Consolidated attribute utilities for Maya.
- `class AttributeTemplate`
- `class Preset(NamedTuple)`
- `class Attributes(ptk.HelpMixin)`
  - methods: has_attr, set_plug, attr_short_name, abbreviate_attrs, apply_preset, remove_preset, create_attributes, ensure_attribute, get_attributes, get_type, get_selected_channels, get_channel_box_values, set_attributes, create_or_set, create_switch, connect, connect_multi, trace_upstream, get_lock_state, set_lock_state, temporarily_unlock, copy_values, paste_values, reset_to_default, mute, unmute, set_channel_box_visibility, lock_and_hide, filter, parse_enum_def, build_enum_string, get_enum_fields, get_enum_label, enum_label_to_index, rename_enum_field, add_enum_field, delete_enum_field

### `node_utils/attributes/channels/__init__.py` — Channels — Switchboard UI for inspecting and editing Maya attributes.
- `launch(sb=None, targets=None, filter=None, search=None)`

### `node_utils/attributes/channels/_channels.py` — Channels — Maya attribute query / mutation logic.
- `class Channels`
  - methods: is_pinned, single_object_mode, pin_targets, get_selected_nodes, get_channel_box_selection, get_filter_kwargs, query_connected_attrs, collect_attr_names, collect_value_strings, get_attr_value, get_attr_type, get_incoming_connection, classify_connection, has_key_at_current_time, build_table_data, format_value, parse_value, toggle_lock, break_connections, set_lock, reset_to_default, toggle_keyable, delete_attributes, set_attribute_value, create_attribute, copy_attr_values, paste_attr_values, rename_attribute, rename_node, get_shape_nodes, get_history_nodes, toggle_key_at_current_time, set_breakdown_key, mute_attrs, unmute_attrs, hide_attrs, show_attrs, lock_and_hide_attrs, select_connections, can_freeze_selection, freeze_transforms, unfreeze_transforms, has_unfreeze_info

### `node_utils/attributes/channels/channels_slots.py` — UI slots for the Channels UI.
- `class ChannelsSlots`
  - methods: apply_launch_config, header_init, show_create_menu, cmb000_init, cmb000, tbl000_init, cleanup_scene_callbacks

### `node_utils/data_nodes.py`
- `class DataNodes`
  - methods: ensure_internal, ensure_export, set_internal_string, get_internal_string, set_export_string, get_export_string

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
  - methods: register_preset, create, combine

### `rig_utils/shadow_rig.py`
- `class ShadowRig(ptk.LoggingMixin)`
  - methods: create_contact_locator, get_or_create_shadow_source, create_shadow_plane, create_silhouette_texture, create_material, setup_expression, bake, refresh_export_metadata, find_shadow_planes, bake_planes, create
- `class ShadowRigSlots`
  - methods: header_init, b001, b002, perform_operation

### `rig_utils/skinning.py` — Skinning utilities: binding, batch weight I/O, transfer, procedural weights.
- `class CurveWeights(ptk.HelpMixin)`
  - methods: effective_degree, joint_stations, solve
- `class SkinUtils(ptk.HelpMixin)`
  - methods: get_skin_cluster, get_influences, bind, unbind, get_weights, set_weights, set_vertex_weights, prune_weights, normalize_weights, set_max_influences, set_skinning_method, copy_weights, mirror_weights, export_weights, import_weights, apply_falloff, add_delta_mush, bind_to_curve

### `rig_utils/telescope_rig.py`
- `class TelescopeRig(ptk.LoggingMixin)`
  - methods: setup_telescope_rig
- `class TelescopeRigSlots(ptk.LoggingMixin)`
  - methods: header_init, build_rig

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
- `class TubeRig(ptk.LoggingMixin)`
  - methods: for_mesh, for_node, rig_name, rig_group, teardown, build, resolve_centerline, estimate_tube_radius, resolve_sizes, generate_joint_chain, create_anchor_joints, skin_mesh, create_logic_curve, create_spline_drivers, skin_curve_to_drivers, create_spline_controls, create_fk_controls, create_anchor_controls, setup_spline_twist, setup_auto_bend, setup_spline_stretch, create_ik, create_pole_vector, bind_joint_chain, constrain_end_with_falloff
- `class RigModeConfig`
- `class TubeRigSlots`
  - methods: header_init, apply_mode, get_mode, get_strategy, get_tube_rig, create_joints_from_tube, b000, b001, b002, b003, b004

### `rig_utils/wheel_rig.py`
- `class WheelRig(ptk.LoggingMixin)`
  - methods: rig_name, get_expressions, delete_expressions, rig_rotation
- `class WheelRigSlots`
  - methods: header_init, rig_name, movement_axis, rotation_axis, resolve_selection, set_wheel_height, s000_init, update_rig_name_placeholder, cleanup, wheel_rig, b000

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
- `parse_qt_sequence(sequence: str) -> Optional[dict]`
- `keystring_to_token(ks: list) -> str`
- `live_hotkey_map() -> dict`
- `ensure_editable_hotkey_set(name: str = MACRO_HOTKEY_SET) -> str`
- `maya_collision_checker(sequence, scope, ui_name, method_name)`

### `ui_utils/maya_bridge_slots.py` — Maya-flavored :class:`BridgeSlotsBase` -- adds Maya-side defaults.
- `class MayaBridgeSlotsBase(BridgeSlotsBase)`
  - methods: default_output_dir

### `ui_utils/maya_native_menus.py`
- `class PersistentMenu(QtWidgets.QMenu)`
  - methods: setVisible
- `class EmbeddedMenuWidget(QtWidgets.QWidget)`
  - methods: init_ui, content_size, sizeHint, minimumSizeHint, resizeEvent, showEvent, fit_to_window
- `class MayaNativeMenus(ptk.LoggingMixin)`
  - methods: get_menu, display_menu

### `ui_utils/maya_ui_handler.py`
- `class MayaUiHandler(UiHandler)`
  - methods: instance, can_resolve, get, apply_styles

### `ui_utils/node_icons.py` — Reusable helper for resolving Maya node icons at runtime.
- `class NodeIcons`
  - methods: icon_name_for_type, icon_name_for_node, get_icon, get_pixmap

### `ui_utils/style_setter/_style_setter.py` — Match Maya's scriptable viewport colors to another DCC's look.
- `list_styles()`
- `set_style(name, persist=False)`
- `list_templates()`
- `apply_template(name, persist=False)`
- `class StyleSetter`

### `uv_utils/_uv_utils.py`
- `class UvUtils(ptk.HelpMixin)`
  - methods: calculate_uv_padding, orient_shells, move_to_uv_space, mirror_uvs, flip_uvs, get_uv_shell_sets, get_uv_shell_border_edges, get_cylinder_seam_edges, get_auto_seam_edges, cut_cylinder_seams, unwrap_cylinder, get_texel_density, set_texel_density, snapshot_uv_sets, restore_uv_snapshot, discard_uv_snapshot, transfer_uvs, reorder_uv_sets, create_lightmap_uvs, remove_empty_uv_sets

### `uv_utils/rizom_bridge/_rizom_bridge.py`
- `class RizomUVBridge(ptk.LoggingMixin)`
  - methods: rizom_path, rizom_version, export_path, script_path, process_with_rizomuv, send_to_rizomuv

### `uv_utils/rizom_bridge/parameters.py` — Registry of user-tunable RizomUV parameters exposed to the bridge UI.
- `referenced_keys(script_text: str) -> 'set[str]'`
- `defaults() -> 'dict[str, Any]'`
- `render_context(values: 'dict[str, Any]') -> 'dict[str, str]'`
- `strip_unsupported(script_text: str, version: 'tuple[int, ...]') -> str`

### `uv_utils/rizom_bridge/rizom_bridge_slots.py` — Slots for the RizomUV bridge panel.
- `class RizomBridgeSlots(MayaBridgeSlotsBase)`
  - methods: params_module, template_dir, make_bridge, list_template_modes, b000, open_uv_editor

### `uv_utils/shell_xform.py` — Dedicated UV shell-transform panel.
- `class ShellXformSlots(ptk.LoggingMixin)`
  - methods: header_init, b023, b024, b025, b026, b034, b035, b036, b037, s041, tb005_init, tb005, tb006_init, tb006, tb008_init, tb008, align_u_min, align_u_avg, align_u_max, align_v_min, align_v_avg, align_v_max, linear_align, orient_shells, orient_edges, gather_shells, randomize_shells, open_uv_editor

### `xform_utils/_xform_utils.py`
- `get_translation(node, world: bool = False)`
- `get_object_matrix(node, world: bool = False)`
- `set_object_matrix(node, value, world: bool = False) -> None`
- `class XformUtilsInternals`
- `class XformUtils(XformUtilsInternals, ptk.HelpMixin)`
  - methods: convert_axis, move_to, drop_to_grid, match_scale, scale_connected_edges, store_transforms, freeze_transforms, freeze_to_opm, unfreeze_to_parent, restore_transforms, clear_stored_transforms, has_stored_transforms, reset_translation, set_translation_to_pivot, get_manip_pivot_matrix, set_manip_pivot_matrix, get_pivot_options, clear_manip_cache, snapshot_manip_pivot, get_operation_axis_matrix, get_operation_axis_pos, align_pivot_to_selection, reset_pivot_transforms, world_align_pivot, bake_pivot, transfer_pivot, aim_object_at_point, orient_to_vector, rotate_axis, get_orientation, get_dist_between_two_objects, get_center_point, get_bounding_box, sort_by_bounding_box_value, align_using_three_points, is_overlapping, check_objects_against_plane, get_vertex_positions, get_matching_verts, order_by_distance, align_vertices

### `xform_utils/matrices.py` — Matrix utilities for Maya rigging and animation.
- `get_matrix(node: str, attr: str = 'worldMatrix', index: int = 0) -> List[float]`
- `set_matrix(node: str, attr: str, value, index: int = 0) -> None`
- `class MatricesError(RuntimeError)`
- `class Matrices(_MatrixMath, _DagTransforms, _NodeBuilders, ptk.HelpMixin)`

### `xform_utils/pivot_watcher.py` — Real-time pivot-change notifier built on :class:`ScriptJobManager`.
- `class PivotWatcher`
  - methods: owner, started, start, stop, attach_widget
