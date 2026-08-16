# mayatk — API Changes

_Diff vs the last release (origin/main @ 76b6184). Generated 2026-08-16._

## Removed (10)

- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.write_diff_report` — was `(cls, export_path: str, missing: list, extra: list, reparented: list = None, *, base_stem: bool = False) -> Optional[str]`
- `rig_utils/tube_rig.py::TubePath` — was `(class)`
- `rig_utils/tube_rig.py::TubePath.estimate_radius` — was `(mesh, centerline: List) -> Optional[float]`
- `rig_utils/tube_rig.py::TubePath.get_centerline` — was `(mesh, num_joints: int = 10, precision: int = 10, edges: list = None, use_surface_normals: bool = True) -> Tuple[List, int]`
- `rig_utils/tube_rig.py::TubePath.get_centerline_from_bounding_box` — was `(obj, precision=10, smooth=False, window_size=1)`
- `rig_utils/tube_rig.py::TubePath.get_centerline_from_surface_normals` — was `(mesh, num_points: int = 10, iterations: int = 3) -> List[om.MPoint]`
- `rig_utils/tube_rig.py::TubePath.get_centerline_using_edges` — was `(edge_selection: List[str]) -> List[List[float]]`
- `rig_utils/tube_rig.py::TubePath.get_edge_loop_centers` — was `(mesh) -> Tuple[List[om.MPoint], int]`
- `uv_utils/_uv_utils.py::UvUtils.detect_seam_algorithm` — was `(cls, mesh) -> str`
- `uv_utils/_uv_utils.py::UvUtils.get_topology_seam_edges` — was `(cls, mesh, angle: float = 45.0, invert_seam=False)`

## Added (28)

- `anim_utils/blendshape_animator/keyframes.py::Keyframes.weight_attr(self) -> str`
- `display_utils/_display_utils.py::DisplayUtils.set_smooth_preview(cls, objects, display: int = None, level: int = None, adaptive_level: int = None, subd_comps: bool = None) -> List[str]`
- `env_utils/_env_utils.py::EnvUtils.is_plugin_loaded(plugin_name) -> bool`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.format_diff_report(cls, missing: list, extra: list, reparented: list = None) -> str`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.cmb004(self, index, widget) -> None`
- `env_utils/scene_exporter/_scene_exporter.py::SceneExporterSlots.cmb006_init(self, widget) -> None`
- `env_utils/scene_exporter/task_manager.py::TaskManager.check_texture_optimization(self, template) -> tuple`
- `env_utils/scene_exporter/task_manager.py::TaskManager.optimize_textures(self, template)`
- `node_utils/data_nodes.py::DataNodes.get_export_node(create: bool = True) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.get_internal_node(create: bool = True) -> Optional[str]`
- `node_utils/data_nodes.py::DataNodes.set_export_json(attr: str, payload) -> Optional[str]`
- `nurbs_utils/_nurbs_utils.py::NurbsUtils.get_greville_arc_lengths(cls, curve) -> List[float]`
- `rig_utils/controls.py::Controls.set_channel_state(cls, node, *, keyable=None, lock=None, hide=None) -> None`
- `rig_utils/skinning.py::SkinUtils.set_dqs_support_non_rigid(skin_cluster, enabled: bool = True) -> bool`
- `rig_utils/tube_path.py::TubePath(class)`
- `rig_utils/tube_path.py::TubePath.estimate_radius(mesh, centerline: List) -> Optional[float]`
- `rig_utils/tube_path.py::TubePath.get_centerline(mesh, num_joints: int = 10, precision: int = 10, edges: list = None, use_surface_normals: bool = True) -> Tuple[List, int]`
- `rig_utils/tube_path.py::TubePath.get_centerline_from_bounding_box(obj, precision=10, smooth=False, window_size=1)`
- `rig_utils/tube_path.py::TubePath.get_centerline_from_surface_normals(mesh, num_points: int = 10, iterations: int = 3) -> List[om.MPoint]`
- `rig_utils/tube_path.py::TubePath.get_centerline_using_edges(edge_selection: List[str]) -> List[List[float]]`
- `rig_utils/tube_path.py::TubePath.get_edge_loop_centers(mesh) -> Tuple[List[om.MPoint], int]`
- `rig_utils/tube_path.py::TubePath.get_end_normals(mesh) -> Tuple[Optional['om.MVector'], Optional['om.MVector']]`
- `rig_utils/tube_path.py::TubePath.get_vertex_rings(mesh) -> List[List[int]]`
- `rig_utils/tube_path.py::TubePath.order_cycle(edge_pairs) -> List[int]`
- `rig_utils/tube_rig.py::TubeRig.create_settings_control(self, size: float = 1.0) -> str`
- `rig_utils/tube_rig.py::TubeRig.create_tweak_controls(self, joints: List[str], size: float = 1.0, every_n: int = 1) -> List[str]`
- `rig_utils/tube_rig.py::TubeRig.set_custom_space(self, control, target: Optional[str]) -> None`
- `rig_utils/tube_rig.py::TubeRig.setup_space_switching(self, control, attr_name: str = 'space') -> str`

## Signature changed (17)

- `anim_utils/blendshape_animator/validator.py::Validator.validate_blendshape`
  - was: `(cls, blendshape: str) -> bool`
  - now: `(cls, blendshape: str, weight_index: int = 0) -> bool`
- `env_utils/_env_utils.py::EnvUtils.load_plugin`
  - was: `(plugin_name)`
  - now: `(cls, plugin_name)`
- `env_utils/hierarchy_sync/scene_data_sidecar.py::SceneDataSidecar.write_manifest`
  - was: `(cls, export_path: str, paths, *, data: Optional[dict] = None, base_stem: bool = False) -> Optional[str]`
  - now: `(cls, export_path: str, paths, *, data: Optional[dict] = None, last_diff: Optional[dict] = None, base_stem: bool = False) -> Optional[str]`
- `node_utils/_node_utils.py::NodeUtils.get_shapes`
  - was: `(cls, node, no_intermediate=True, full_path=True, descend=False)`
  - now: `(cls, node, no_intermediate=True, full_path=True, descend=False, type=None)`
- `node_utils/data_nodes.py::DataNodes.set_internal_string`
  - was: `(attr: str, value: str) -> str`
  - now: `(attr: str, value: str) -> Optional[str]`
- `rig_utils/skinning.py::CurveWeights.solve`
  - was: `(cls, mesh, joints: List[str], curve: Optional[str] = None, centerline: Optional[Sequence] = None, profile: Union[str, Callable] = 'smoothstep', degree: int = 3) -> Tuple[List[float], List[str]]`
  - now: `(cls, geometry, joints: List[str], curve: Optional[str] = None, centerline: Optional[Sequence] = None, profile: Union[str, Callable] = 'smoothstep', degree: int = 3, rings: Optional[Sequence[Sequence[int]]] = None) -> Tuple[List[float], List[str]]`
- `rig_utils/skinning.py::SkinUtils.bind`
  - was: `(cls, mesh, joints, bind_method: str = 'closest', skinning_method: str = 'classic', max_influences: int = 4, dropoff_rate: float = 4.0, weight_distribution: float = 0.5, remove_unused_influences: bool = False, heatmap_falloff: float = 0.68, bind_fallback: bool = True, name: Optional[str] = None) -> str`
  - now: `(cls, mesh, joints, bind_method: str = 'closest', skinning_method: str = 'classic', max_influences: int = 4, dropoff_rate: float = 4.0, weight_distribution: float = 0.5, remove_unused_influences: bool = False, heatmap_falloff: float = 0.68, bind_fallback: bool = True, support_non_rigid: bool = True, name: Optional[str] = None) -> str`
- `rig_utils/skinning.py::SkinUtils.bind_to_curve`
  - was: `(cls, mesh, joints, curve: Optional[str] = None, centerline: Optional[Sequence] = None, profile: Union[str, Callable] = 'smoothstep', degree: int = 3, skinning_method: str = 'dqs', max_influences: Optional[int] = None, name: Optional[str] = None, **bind_kwargs) -> str`
  - now: `(cls, geometry, joints, curve: Optional[str] = None, centerline: Optional[Sequence] = None, profile: Union[str, Callable] = 'smoothstep', degree: int = 3, skinning_method: str = 'dqs', max_influences: Optional[int] = None, rings: Optional[Sequence[Sequence[int]]] = None, name: Optional[str] = None, **bind_kwargs) -> str`
- `rig_utils/skinning.py::SkinUtils.set_skinning_method`
  - was: `(cls, skin_cluster, method: str = 'dqs') -> None`
  - now: `(cls, skin_cluster, method: str = 'dqs', support_non_rigid: bool = True) -> None`
- `rig_utils/tube_rig.py::TubeRig.create_fk_controls`
  - was: `(self, joints: List[str], size: float = 1.0) -> List[str]`
  - now: `(self, joints: List[str], size: float = 1.0, num_controls: int = 5) -> List[str]`
- `rig_utils/tube_rig.py::TubeRig.create_spline_controls`
  - was: `(self, joints: List[str], centerline: Optional[List] = None, size: float = 1.0, num_controls: int = 3, enable_stretch: bool = True, enable_squash: bool = True, enable_volume: bool = True, enable_twist: bool = True, enable_auto_bend: bool = False) -> Tuple[List[str], str, str]`
  - now: `(self, joints: List[str], centerline: Optional[List] = None, size: float = 1.0, num_controls: int = 3, enable_stretch: bool = True, enable_squash: bool = True, enable_volume: bool = True, enable_twist: bool = True, enable_auto_bend: bool = False, enable_tweaks: bool = True) -> Tuple[List[str], str, str]`
- `uv_utils/_uv_utils.py::UvUtils.cut_cylinder_seams`
  - was: `(cls, objects=None, angle=45.0, invert_seam=False, history=True, sew=True, algorithm='auto')`
  - now: `(cls, objects=None, angle=45.0, invert_seam=False, history=True, sew=True, taper_angle=20.0, camera=None, flat_angle=60.0, trim_ratio=0.12)`
- `uv_utils/_uv_utils.py::UvUtils.get_auto_seam_edges`
  - was: `(cls, mesh, angle: float = 45.0, invert_seam: bool = False)`
  - now: `(cls, mesh, angle: float = 45.0, invert_seam: bool = False, taper_angle: float = 20.0, camera=None, flat_angle: float = 60.0, trim_ratio: float = 0.12)`
- `uv_utils/_uv_utils.py::UvUtils.unwrap_cylinder`
  - was: `(cls, objects=None, angle=45.0, invert_seam=False, unfold=True, orient=True, map_size=4096, sew=True, algorithm='auto')`
  - now: `(cls, objects=None, angle=45.0, invert_seam=False, unfold=True, orient=True, map_size=4096, sew=True, taper_angle=20.0, camera=None, flat_angle=60.0, trim_ratio=0.12)`
- `xform_utils/_xform_utils.py::XformUtils.transfer_pivot`
  - was: `(cls, objects, translate: bool = False, rotate: bool = False, scale: bool = False, bake: bool = False, world_space: bool = True, mirror: str = '', select_targets_after_transfer: bool = False, preserve_instancing: bool = True)`
  - now: `(cls, objects, translate: bool = False, rotate: bool = False, scale: bool = False, bake: bool = True, world_space: bool = True, mirror: str = '', select_targets_after_transfer: bool = False, preserve_instancing: bool = True)`
- `xform_utils/matrices.py::Matrices.build_space_switch`
  - was: `(control: str, space_parents: List[str], attr_owner: Optional[str] = None, attr_name: str = 'space', name: str = 'space_switch') -> str`
  - now: `(control: str, space_parents: List[Optional[str]], attr_owner: Optional[str] = None, attr_name: str = 'space', name: str = 'space_switch', enum_labels: Optional[List[str]] = None, capture_offsets: bool = False, passthrough_default: bool = False) -> str`
- `xform_utils/matrices.py::Matrices.drive_with_offset_parent_matrix`
  - was: `(driver_world: str, driven_ctl: str, name: str = 'drive_opm') -> str`
  - now: `(driver_world: str, driven_ctl: str, name: str = 'drive_opm', offset: Optional[List[float]] = None) -> str`
