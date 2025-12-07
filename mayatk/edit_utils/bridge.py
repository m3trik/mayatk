# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
# From this package:
from mayatk.core_utils.preview import Preview
from mayatk.core_utils.components import Components


class Bridge:
    @staticmethod
    def bridge(edges, **kwargs):
        """ """
        mapped_edges = Components.map_components_to_objects(edges)

        for edges in mapped_edges.values():
            pm.polyBridgeEdge(edges, **kwargs)

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
            children = pm.listRelatives(
                node, children=True, allDescendents=True, type="nurbsCurve"
            )
            if children:
                # Get the transform nodes of the curve shapes
                curve_transforms = [
                    pm.listRelatives(curve, parent=True)[0] for curve in children
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
                    pm.delete(mesh_node, constructionHistory=True)
                    print(f"Deleted construction history on: {mesh_node}")
                except Exception as e:
                    pm.warning(f"Failed to delete history on {mesh_node}: {e}")

            # Then delete the child curves
            for curve in child_curves:
                try:
                    pm.delete(curve)
                    print(f"Deleted child curve: {curve}")
                except Exception as e:
                    pm.warning(f"Failed to delete curve {curve}: {e}")
        else:
            print("No child curves found to delete.")


class BridgeSlots:
    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.bridge

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

    def perform_operation(self, objects):
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
            selected_edges = pm.ls(sl=True, flatten=True)
            if selected_edges:
                mesh_nodes = list(
                    set(
                        [
                            edge.node()
                            for edge in selected_edges
                            if hasattr(edge, "node")
                        ]
                    )
                )
                # Convert mesh shapes to transform nodes
                mesh_nodes = [
                    (
                        pm.listRelatives(mesh, parent=True)[0]
                        if pm.objectType(mesh) == "mesh"
                        else mesh
                    )
                    for mesh in mesh_nodes
                ]

        # Perform the bridge operation
        Bridge.bridge(objects, **kwargs)

        # Clean up child curves if option is enabled and curve type is selected
        if self.ui.chk001.isChecked() and kwargs["curveType"] == 2 and mesh_nodes:
            Bridge.cleanup_bridge_curves_and_history(mesh_nodes)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("bridge", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
