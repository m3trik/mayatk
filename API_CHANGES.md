# mayatk — API Changes

_Diff vs prior baseline. Generated 2026-07-09._

## Removed (2)

- `rig_utils/tube_rig.py::TubeRig.create_start_end_locators` — was `(self, joints: List[str], ik_handle: Optional[str] = None) -> Tuple[str, str]`
- `rig_utils/tube_rig.py::TubeRigSlots.create_rig_from_joints` — was `(self, obj, joints)`

## Added (3)

- `rig_utils/tube_rig.py::TubeRig.for_node(cls, node) -> Optional['TubeRig']`
- `rig_utils/tube_rig.py::TubeRig.skin_mesh(self, joints: List[str]) -> Optional[str]`
- `rig_utils/tube_rig.py::TubeRig.teardown(self) -> None`

## Signature changed (1)

- `rig_utils/tube_rig.py::TubePath.get_centerline_using_edges`
  - was: `(edge_selection: List[str]) -> List[om.MPoint]`
  - now: `(edge_selection: List[str]) -> List[List[float]]`
