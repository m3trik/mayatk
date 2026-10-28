# !/usr/bin/python
# coding=utf-8
"""Bridge: connect open edge loops with new faces (``polyBridgeEdge``).

``Bridge`` is the API tentacle's Bridge panel calls, and blendertk's ``Bridge``
mirrors it: one host-neutral keyword set, the curve types it builds, and the
options it cannot honor (none in Maya).
"""

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from mayatk.core_utils.preview import OperationError
from mayatk.core_utils.components import Components
from mayatk.node_utils._node_utils import NodeUtils


class Bridge:
    """Bridge open edge loops with new faces: the API tentacle's Bridge panel
    calls, mirrored by blendertk's ``Bridge``."""

    #: The curve types :meth:`bridge` builds, in ``curve_type`` order: the index
    #: is ``polyBridgeEdge``'s ``curveType`` (and the panel's Type combo order).
    CURVE_TYPES = ("linear", "blend", "curve")

    #: :meth:`bridge` options (or curve types) this host cannot honor, by name,
    #: with the reason; the panel disables their controls. Maya honors every one.
    UNSUPPORTED_OPTIONS = {}

    @staticmethod
    def bridge(
        edges,
        *,
        curve_type=None,
        divisions=None,
        smoothing_angle=None,
        offset=None,
        taper=None,
        twist=None,
        cleanup_curves=False,
        **kwargs,
    ):
        """Bridge open edge loops, grouped per owning mesh.

        On failure, raises :class:`OperationError` with a specific,
        human-readable reason instead of Maya's raw multi-line wall.

        Parameters:
            edges (str/list): The open border edges: two loops per mesh.
            curve_type (str): ``"linear"``, ``"blend"`` or ``"curve"``
                (:attr:`CURVE_TYPES`); a curve bridge leaves a NURBS handle to
                shape. ``None`` keeps Maya's default (linear).
            divisions (int): Edge loops added across the bridge.
            smoothing_angle (float): Degrees; new edges below it are smoothed.
            offset (int): Shifts which vertices of the two loops pair up.
            taper (float): Scale along the bridge (1.0: none).
            twist (float): Rotation along the bridge, in degrees.
            cleanup_curves (bool): After a ``"curve"`` bridge, delete the control
                curve it leaves and the meshes' construction history
                (:meth:`cleanup_bridge_curves_and_history`).
            **kwargs: Further ``polyBridgeEdge`` flags, passed through as given
                (a raw flag wins over its keyword above).

        Raises:
            ValueError: An unknown *curve_type*.
            OperationError: ``polyBridgeEdge`` rejected the edges.
        """
        if curve_type is not None and curve_type not in Bridge.CURVE_TYPES:
            raise ValueError(
                f"curve_type must be one of {Bridge.CURVE_TYPES}, not {curve_type!r}."
            )
        flags = {
            flag: value
            for flag, value in (
                (
                    "curveType",
                    None if curve_type is None else Bridge.CURVE_TYPES.index(curve_type),
                ),
                ("divisions", divisions),
                ("smoothingAngle", smoothing_angle),
                ("bridgeOffset", offset),
                ("taper", taper),
                ("twist", twist),
            )
            if value is not None
        }
        flags.update(kwargs)

        mapped_edges = Components.map_components_to_objects(edges)
        # The meshes a curve bridge parents its control curve under, by long
        # name, read before the bridge: the cleanup addresses them after it.
        cleanup = cleanup_curves and flags.get("curveType") == Bridge.CURVE_TYPES.index(
            "curve"
        )
        meshes = [cmds.ls(node, long=True)[0] for node in mapped_edges] if cleanup else []

        for mesh_edges in mapped_edges.values():
            try:
                cmds.polyBridgeEdge(mesh_edges, **flags)
            except RuntimeError as e:
                # polyBridgeEdge rejected the selection. Diagnose *why* and
                # raise a short, specific popup instead of Maya's multi-line
                # wall + help URLs. The original error is chained (``from e``)
                # so the console still gets the full traceback.
                raise Bridge._diagnose_failure(mesh_edges) from e

        if meshes:
            Bridge.cleanup_bridge_curves_and_history(meshes)

    @staticmethod
    def _diagnose_failure(edges) -> OperationError:
        """Build the most specific :class:`OperationError` for a rejected bridge.

        ``polyBridgeEdge`` fails for three real reasons: the edges aren't on an
        open border, only one border loop is selected, or the two loops have
        different edge counts. Inspect the selection and report the exact one;
        fall back to the full cause list if it can't be determined.
        """
        title = "Bridge failed"

        def _is_border_edge(edge):
            # Open-border edges bound exactly one face. Compute per-edge on the
            # given strings so we don't trip over transform/shape prefix forms.
            faces = (
                cmds.ls(
                    cmds.polyListComponentConversion(edge, fromEdge=True, toFace=True)
                    or [],
                    flatten=True,
                )
                or []
            )
            return len(faces) == 1

        try:
            flat = cmds.ls(edges, flatten=True) or []
            non_border = [e for e in flat if not _is_border_edge(e)]
            if non_border:
                return OperationError(
                    "Some selected edges aren't on an open border (a hole).",
                    causes=[
                        "Select only edges on an <b>open border</b>, not "
                        "interior edges.",
                        "Bridge connects <b>two complete border loops</b> across "
                        "a gap.",
                    ],
                    title=title,
                )

            loops = Components.get_contiguous_edges(flat) or []
            sizes = sorted(len(loop) for loop in loops)
            if len(loops) < 2:
                return OperationError(
                    "Only one border loop is selected — bridging needs two.",
                    causes=[
                        "Select <b>two separate open border loops</b> to connect.",
                    ],
                    title=title,
                )
            if len(loops) == 2 and sizes[0] != sizes[1]:
                return OperationError(
                    f"The two border loops have <b>{sizes[0]}</b> and "
                    f"<b>{sizes[1]}</b> edges — the counts must match.",
                    causes=[
                        "Both loops need the <b>same number of edges</b> to "
                        "bridge one-to-one.",
                        "Add or remove edges so the counts match.",
                    ],
                    title=title,
                )
        except Exception:  # diagnosis is best-effort
            pass

        return OperationError(
            "Maya couldn't bridge the selected edges.",
            causes=[
                "All edges must belong to a <b>single combined mesh</b> "
                "(combine the meshes first).",
                "Both edge loops must have the <b>same number of edges</b>.",
                "The edges must lie on an <b>open border</b> (a hole), not the "
                "interior of the mesh.",
            ],
            title=title,
        )

    @staticmethod
    def get_child_curves_from_bridge(mesh_nodes):
        """Find child curves created by polyBridgeEdge operations on mesh nodes.

        Parameters:
            mesh_nodes (list): List of mesh transform nodes to check for child curves

        Returns:
            list: List of curve nodes that are children of the mesh nodes
        """
        child_curves = []
        for node in mesh_nodes:
            # Get all children of the mesh node
            children = cmds.listRelatives(
                node,
                children=True,
                allDescendents=True,
                type="nurbsCurve",
                fullPath=True,
            )
            if children:
                # Get the transform nodes of the curve shapes
                curve_transforms = [
                    cmds.listRelatives(curve, parent=True, fullPath=True)[0]
                    for curve in children
                ]
                child_curves.extend(curve_transforms)
        return child_curves

    @staticmethod
    def cleanup_bridge_curves_and_history(mesh_nodes):
        """Clean up child curves and deformer history from mesh nodes.

        Parameters:
            mesh_nodes (list): List of mesh transform nodes to clean up
        """
        # Find child curves first
        child_curves = Bridge.get_child_curves_from_bridge(mesh_nodes)

        if child_curves:
            print(
                f"Found {len(child_curves)} child curves to delete: {[str(curve) for curve in child_curves]}"
            )

            # Clear the bridge's construction history so the curves can go.
            # Deformer-safe: bridging into a skinned mesh must not unbind it.
            for mesh_node in mesh_nodes:
                try:
                    NodeUtils.delete_history(mesh_node)
                    print(f"Deleted construction history on: {mesh_node}")
                except Exception as e:
                    cmds.warning(f"Failed to delete history on {mesh_node}: {e}")

            # Then delete the child curves
            for curve in child_curves:
                try:
                    cmds.delete(curve)
                    print(f"Deleted child curve: {curve}")
                except Exception as e:
                    cmds.warning(f"Failed to delete curve {curve}: {e}")
        else:
            print("No child curves found to delete.")


# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
