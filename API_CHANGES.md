# mayatk — API Changes

_Diff vs prior baseline. Generated 2026-06-05._

## Added (9)

- `nurbs_utils/curve_to_tube.py::CurveToTube(class)`
- `nurbs_utils/curve_to_tube.py::CurveToTube.create(cls, curves, output_type: str = 'nurbs', radius: float = 1.0, sections: int = 8, path_divisions: int = 1, degree: int = 3, caps: bool = True, quads: bool = True, live: bool = False, cleanup: bool = True, name: str = 'tube') -> List[str]`
- `nurbs_utils/curve_to_tube.py::CurveToTubeSlots(class)`
- `nurbs_utils/curve_to_tube.py::CurveToTubeSlots.b001(self)`
- `nurbs_utils/curve_to_tube.py::CurveToTubeSlots.header_init(self, widget)`
- `nurbs_utils/curve_to_tube.py::CurveToTubeSlots.perform_operation(self, objects, contract)`
- `uv_utils/_uv_utils.py::UvUtils.cut_cylinder_seams(cls, objects=None, invert_seam=False, history=True)`
- `uv_utils/_uv_utils.py::UvUtils.get_cylinder_seam_edges(cls, mesh, sections=None, invert_seam: bool = False, cap_faces=None)`
- `uv_utils/_uv_utils.py::UvUtils.unwrap_cylinder(cls, objects=None, invert_seam=False, unfold=True, orient=True, map_size=4096)`
