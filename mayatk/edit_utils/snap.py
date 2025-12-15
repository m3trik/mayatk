# !/usr/bin/python
# coding=utf-8
from typing import List, Union, Optional

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# From this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.components import Components


class Snap(ptk.HelpMixin):
    """Vertex and mesh snapping utilities."""

    @staticmethod
    @CoreUtils.undoable
    def snap_to_closest_vertex(obj1, obj2, tolerance=10.0, freeze_transforms=False):
        """Snap the vertices from object one to the closest verts on object two.

        Parameters:
            obj1 (obj): The object in which the vertices are moved from.
            obj2 (obj): The object in which the vertices are moved to.
            tolerance (float): Maximum search distance.
            freeze_transforms (bool): Reset the selected transform and all of its
                children down to the shape level.

        Returns:
            int: Number of vertices that were snapped.
        """
        vertices = Components.get_components(obj1, "vertices")
        closestVerts = Components.get_closest_vertex(
            vertices, obj2, tolerance=tolerance, freeze_transforms=freeze_transforms
        )

        progressBar = "mainProgressBar"
        pm.progressBar(
            progressBar,
            edit=True,
            beginProgress=True,
            isInterruptable=True,
            status="Snapping Vertices ...",
            maxValue=len(closestVerts),
        )

        moved_count = 0
        for v1, v2 in closestVerts.items():
            if pm.progressBar(progressBar, query=True, isCancelled=True):
                break

            v2Pos = pm.pointPosition(v2, world=True)
            pm.xform(v1, translation=v2Pos, worldSpace=True)
            moved_count += 1

            pm.progressBar(progressBar, edit=True, step=1)

        pm.progressBar(progressBar, edit=True, endProgress=True)
        return moved_count

    @staticmethod
    @CoreUtils.undoable
    def snap_to_surface(
        source_meshes,
        target_mesh,
        offset: float = None,
        threshold: float = None,
        invert: bool = False,
    ) -> int:
        """Snap source mesh vertices to the closest point on a target surface.

        Vertices are projected onto the target surface at the specified offset
        distance. Vertices that are "inside" the target (poking through) are
        pushed out to the offset distance.

        Parameters:
            source_meshes: Single mesh or list of meshes to snap (vertices will be moved).
            target_mesh: The reference surface mesh to snap to.
            offset: Distance from the surface to place affected vertices.
                   None or 0 means vertices are placed exactly on the surface.
            threshold: Only process vertices within this distance of the target.
                      Vertices farther away are untouched. None = no limit.
            invert: If True, invert the direction (flip what is "inside" vs "outside").
                   Use this if target mesh normals point inward.

        Returns:
            int: Number of vertices that were moved.
        """
        import maya.api.OpenMaya as om

        # Handle None for offset (treat as 0)
        if offset is None:
            offset = 0.0

        # Ensure source_meshes is a list
        if not isinstance(source_meshes, (list, tuple)):
            source_meshes = [source_meshes]

        target_fn_mesh = CoreUtils.get_mfn_mesh(target_mesh)

        total_moved = 0

        for source in source_meshes:
            source_fn_mesh = CoreUtils.get_mfn_mesh(source)

            # Get vertex positions in world space
            points = source_fn_mesh.getPoints(om.MSpace.kWorld)
            new_points = om.MPointArray()
            moved_count = 0

            for i in range(len(points)):
                point = points[i]
                mpoint = om.MPoint(point.x, point.y, point.z)

                # Find closest point on target mesh
                closest_point, face_id = target_fn_mesh.getClosestPoint(
                    mpoint, om.MSpace.kWorld
                )

                # Get the surface normal at closest point
                target_normal = target_fn_mesh.getPolygonNormal(
                    face_id, om.MSpace.kWorld
                )

                # Vector from closest point to vertex
                to_vertex = om.MVector(
                    point.x - closest_point.x,
                    point.y - closest_point.y,
                    point.z - closest_point.z,
                )

                # Unsigned distance from surface
                unsigned_dist = to_vertex.length()

                # Signed distance: positive = outside (same side as normal),
                # negative = inside (poking through)
                if unsigned_dist > 0.0001:
                    dot = (
                        to_vertex.x * target_normal.x
                        + to_vertex.y * target_normal.y
                        + to_vertex.z * target_normal.z
                    )
                    signed_dist = unsigned_dist if dot >= 0 else -unsigned_dist
                else:
                    signed_dist = 0.0

                # Skip vertices beyond threshold (use unsigned for threshold check)
                if threshold is not None and unsigned_dist > threshold:
                    new_points.append(point)
                    continue

                # Direction: use surface normal, optionally inverted
                if invert:
                    direction = om.MVector(
                        -target_normal.x, -target_normal.y, -target_normal.z
                    )
                    signed_dist = -signed_dist  # Flip the signed distance too
                else:
                    direction = target_normal

                # Move vertices that are closer than offset to exactly offset distance
                if signed_dist < offset:
                    new_pos = om.MPoint(
                        closest_point.x + direction.x * offset,
                        closest_point.y + direction.y * offset,
                        closest_point.z + direction.z * offset,
                    )
                    new_points.append(new_pos)
                    moved_count += 1
                else:
                    # Already far enough - keep original
                    new_points.append(point)

            # Apply new positions using PyMEL (supports undo)
            if moved_count > 0:
                # Get the transform node for vertex access
                transform = pm.PyNode(source)
                if transform.type() == "mesh":
                    transform = transform.getParent()

                for i in range(len(new_points)):
                    old_pt = points[i]
                    new_pt = new_points[i]
                    # Only move if position changed
                    if (
                        abs(new_pt.x - old_pt.x) > 0.0001
                        or abs(new_pt.y - old_pt.y) > 0.0001
                        or abs(new_pt.z - old_pt.z) > 0.0001
                    ):
                        vtx = f"{transform}.vtx[{i}]"
                        pm.xform(vtx, ws=True, t=(new_pt.x, new_pt.y, new_pt.z))

                total_moved += moved_count
                print(f"Moved {moved_count} vertices on {source}")

        return total_moved

    @staticmethod
    @CoreUtils.undoable
    def snap_to_grid(
        objects=None,
        grid_size: float = 1.0,
        axes: str = "xyz",
    ) -> int:
        """Snap object pivots or vertices to the nearest grid point.

        Parameters:
            objects: Objects or components to snap. If None, uses selection.
            grid_size: The grid spacing to snap to.
            axes: Which axes to snap ('x', 'y', 'z', or combinations like 'xy').

        Returns:
            int: Number of items that were snapped.
        """
        if objects is None:
            objects = pm.selected(flatten=True)
        else:
            objects = pm.ls(objects, flatten=True)

        if not objects:
            pm.warning("No objects selected for grid snapping.")
            return 0

        axes = axes.lower()
        snap_count = 0

        for obj in objects:
            # Check if it's a component (vertex, etc.)
            if hasattr(obj, "getPosition"):
                pos = obj.getPosition(space="world")
                new_pos = list(pos)
                for i, axis in enumerate(["x", "y", "z"]):
                    if axis in axes:
                        new_pos[i] = round(pos[i] / grid_size) * grid_size
                obj.setPosition(new_pos, space="world")
                snap_count += 1
            else:
                # It's a transform - snap the pivot
                pos = pm.xform(obj, q=True, ws=True, rp=True)
                new_pos = list(pos)
                for i, axis in enumerate(["x", "y", "z"]):
                    if axis in axes:
                        new_pos[i] = round(pos[i] / grid_size) * grid_size
                # Calculate the delta and move
                delta = [new_pos[i] - pos[i] for i in range(3)]
                pm.move(obj, delta, relative=True, worldSpace=True)
                snap_count += 1

        return snap_count


class SnapSlots:
    """UI slots for the Snap tool."""

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.snap

    def b000_init(self, widget):
        """Initialize Snap to Surface button option box."""
        widget.option_box.menu.setTitle("Snap to Surface")
        widget.option_box.menu.add(
            "QDoubleSpinBox",
            setPrefix="Offset: ",
            setObjectName="s000",
            set_limits=[0, 100, 0.01, 1],
            setValue=0.0,
            setToolTip="Distance from surface to place affected vertices.",
        )
        widget.option_box.menu.add(
            "QDoubleSpinBox",
            setPrefix="Threshold: ",
            setObjectName="s001",
            set_limits=[0, 1000, 0.1, 1],
            setValue=0.0,
            setToolTip="Only process vertices within this distance. 0 = no limit.",
        )
        widget.option_box.menu.add(
            "QCheckBox",
            setText="Invert",
            setObjectName="chk000",
            setChecked=False,
            setToolTip="Invert direction (use if target normals point inward).",
        )

    def b000(self):
        """Snap to Surface button."""
        sel = pm.selected()
        if len(sel) < 2:
            pm.warning("Select source mesh(es) first, then the target mesh last.")
            return

        source_meshes = sel[:-1]
        target_mesh = sel[-1]

        offset = self.ui.b000.menu.s000.value()
        threshold = self.ui.b000.menu.s001.value() or None  # 0 means no limit
        invert = self.ui.b000.menu.chk000.isChecked()

        count = Snap.snap_to_surface(
            source_meshes,
            target_mesh,
            offset=offset,
            threshold=threshold,
            invert=invert,
        )
        self.sb.message_box(f"<hl>Snapped {count} vertices to surface.</hl>")

    def b001_init(self, widget):
        """Initialize Snap to Closest Vertex button option box."""
        widget.option_box.menu.setTitle("Snap to Closest Vertex")
        widget.option_box.menu.add(
            "QDoubleSpinBox",
            setPrefix="Tolerance: ",
            setObjectName="s002",
            set_limits=[0, 1000, 0.1, 1],
            setValue=10.0,
            setToolTip="Maximum search distance for matching vertices.",
        )

    def b001(self):
        """Snap to Closest Vertex button."""
        sel = pm.selected()
        if len(sel) != 2:
            pm.warning("Select exactly two meshes: source first, then target.")
            return

        obj1, obj2 = sel
        tolerance = self.ui.b001.menu.s002.value()

        count = Snap.snap_to_closest_vertex(obj1, obj2, tolerance=tolerance)
        self.sb.message_box(f"<hl>Snapped {count} vertices.</hl>")

    def b002_init(self, widget):
        """Initialize Snap to Grid button option box."""
        widget.option_box.menu.setTitle("Snap to Grid")
        widget.option_box.menu.add(
            "QDoubleSpinBox",
            setPrefix="Grid Size: ",
            setObjectName="s003",
            set_limits=[0.001, 1000, 0.1, 3],
            setValue=1.0,
            setToolTip="Grid spacing to snap to.",
        )
        widget.option_box.menu.add(
            "QLineEdit",
            setPlaceholderText="Axes (xyz)...",
            setObjectName="txt000",
            setText="xyz",
            setToolTip="Which axes to snap: x, y, z, or combinations like xy.",
        )

    def b002(self):
        """Snap to Grid button."""
        grid_size = self.ui.b002.menu.s003.value()
        axes = self.ui.b002.menu.txt000.text() or "xyz"

        count = Snap.snap_to_grid(grid_size=grid_size, axes=axes)
        self.sb.message_box(f"<hl>Snapped {count} items to grid.</hl>")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
