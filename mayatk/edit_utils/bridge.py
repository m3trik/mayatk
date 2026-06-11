# !/usr/bin/python
# coding=utf-8
# From this package:
try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

from uitk.widgets.mixins.tooltip_mixin import fmt
from mayatk.core_utils.preview import Preview, OperationError
from mayatk.core_utils.components import Components


class Bridge:
    @staticmethod
    def bridge(edges, **kwargs):
        """Bridge open edge loops, grouped per owning mesh.

        On failure, raises :class:`OperationError` with a specific,
        human-readable reason instead of Maya's raw multi-line wall.
        """
        mapped_edges = Components.map_components_to_objects(edges)

        for edges in mapped_edges.values():
            try:
                cmds.polyBridgeEdge(edges, **kwargs)
            except RuntimeError as e:
                # polyBridgeEdge rejected the selection. Diagnose *why* and
                # raise a short, specific popup instead of Maya's multi-line
                # wall + help URLs. The original error is chained (``from e``)
                # so the console still gets the full traceback.
                raise Bridge._diagnose_failure(edges) from e

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
            children = cmds.listRelatives(node, children=True, allDescendents=True, type="nurbsCurve"
            )
            if children:
                # Get the transform nodes of the curve shapes
                curve_transforms = [
                    cmds.listRelatives(curve, parent=True)[0] for curve in children
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

            # Delete deformer history on mesh nodes first
            for mesh_node in mesh_nodes:
                try:
                    cmds.delete(mesh_node, constructionHistory=True)
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


class BridgeSlots:
    # polyBridgeEdge mutates the mesh in place with construction history. On a
    # historyless mesh (e.g. a freshly combined cylinder) the preview rollback
    # would bake the bridge in when it deletes the auto-created orig-shape,
    # closing the border so the next refresh/commit fails. PRESERVE_GEOMETRY
    # makes Preview snapshot the owning mesh and restore it on rollback.
    PRESERVE_GEOMETRY = True

    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.bridge

        # Per-field reset buttons (uitk option-box): click resets a field to its
        # default; Alt/Ctrl+click bypasses it to default (greyed, restorable).
        # Must precede connect_multi/Preview — wrapping reparents the widgets and
        # invalidates any already-deferred wrapper (see add_reset_buttons docstring).
        self.sb.add_reset_buttons(self.ui)

        self.preview = Preview(
            self,
            self.ui.chk000,
            self.ui.b000,
            message_func=self.sb.message_box,
            enable_on_show=True,
        )

        self.sb.connect_multi(
            self.ui, "cmb000", "currentIndexChanged", self.preview.refresh
        )
        self.sb.connect_multi(self.ui, "s000-4", "valueChanged", self.preview.refresh)
        self.sb.connect_multi(self.ui, "chk001", "toggled", self.preview.refresh)

    def header_init(self, widget):
        """Configure header help text."""
        widget.set_help_text(
            fmt(
                title="Bridge",
                body="Connect two open edge loops with new polygon faces.",
                steps=[
                    "Select two open edge loops on the same mesh.",
                    "Pick a <b>Curve Type</b> — Linear / Blend / Curve. "
                    "Curve mode creates a NURBS handle you can shape, then "
                    "rebuilds the bridge each preview/apply.",
                    "Adjust <b>Divisions</b>, <b>Smoothing Angle</b>, "
                    "<b>Offset</b>, <b>Taper</b>, and <b>Twist</b>.",
                    "Toggle <b>Preview</b> to iterate, or press <b>Bridge</b> "
                    "to commit.",
                ],
                notes=[
                    "Enable <b>Cleanup curves</b> to delete the temporary "
                    "control curve and construction history left behind by "
                    "Curve-mode bridges on commit.",
                ],
            )
        )

    def perform_operation(self, objects, contract):
        kwargs = {
            "curveType": self.ui.cmb000.currentIndex(),
            "divisions": self.ui.s000.value(),
            "smoothingAngle": self.ui.s001.value(),
            "bridgeOffset": self.ui.s002.value(),
            "taper": self.ui.s003.value(),
            "twist": self.ui.s004.value(),
        }

        # Get mesh nodes before operation for curve cleanup
        mesh_nodes = []
        if self.ui.chk001.isChecked() and kwargs["curveType"] == 2:
            # Get mesh nodes from the selected edges
            selected_edges = cmds.ls(sl=True, flatten=True)
            if selected_edges:
                # Strip component suffix ("pCube1.e[12]" -> "pCube1")
                mesh_nodes = list({edge.split(".")[0] for edge in selected_edges})

        # Perform the bridge operation
        Bridge.bridge(objects, **kwargs)

        # Clean up child curves if option is enabled and curve type is selected
        if self.ui.chk001.isChecked() and kwargs["curveType"] == 2 and mesh_nodes:
            Bridge.cleanup_bridge_curves_and_history(mesh_nodes)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("bridge", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
