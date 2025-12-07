# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
# from this package:
from mayatk.core_utils.preview import Preview
from mayatk.core_utils.components import Components


class Bevel:
    @staticmethod
    def bevel(
        edges,
        width=0.5,
        segments=1,
        autoFit=True,
        depth=1,
        mitering=0,
        miterAlong=0,
        chamfer=True,
        worldSpace=True,
        smoothingAngle=30,
        fillNgons=True,
        mergeVertices=True,
        mergeVertexTolerance=0.0001,
        miteringAngle=180,
        angleTolerance=180,
    ):
        """Bevels the given edges with highly customizable options for topology,
        bevel width, segments, and other attributes. Designed for production use,
        offering fine-grained control over the bevel operation.

        Parameters:
            edges (str/obj/list): List of edges to bevel
            width (float): Bevel width as a fraction between 0 and 1
            segments (int): Number of segments for the bevel
            autoFit (bool): Whether to compute a smooth roundness for new facets
            depth (): Depth of the bevel
            mitering (): Controls the topology at corners
            miterAlong (): Direction to offset new vertices
            chamfer (bool): Whether to smooth out the surface at bevels
            worldSpace (bool): Whether to use world space or object space for geometrical values
            smoothingAngle (): Angle for creating new hard edges
            fillNgons (bool): Whether to subdivide new faces with more than 4 edges
            mergeVertices (bool): Whether to merge vertices within a tolerance
            mergeVertexTolerance (float): Tolerance within which to merge vertices
            miteringAngle (): Miter faces that have angles less than this value
            angleTolerance (): Angular tolerance for creation of extra triangles
        """

        mapped_edges = Components.map_components_to_objects(edges)

        for edges in mapped_edges.values():
            pm.polyBevel3(
                edges,
                fraction=width,
                segments=segments,
                autoFit=autoFit,
                depth=depth,
                mitering=mitering,
                miterAlong=miterAlong,
                chamfer=chamfer,
                worldSpace=worldSpace,
                smoothingAngle=smoothingAngle,
                fillNgons=fillNgons,
                mergeVertices=mergeVertices,
                mergeVertexTolerance=mergeVertexTolerance,
                miteringAngle=miteringAngle,
                angleTolerance=angleTolerance,
                offsetAsFraction=True,
                constructionHistory=True,
            )


class BevelSlots:
    def __init__(self, switchboard):
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.bevel

        self.preview = Preview(
            self, self.ui.chk000, self.ui.b000, message_func=self.sb.message_box
        )

        self.sb.connect_multi(self.ui, "s000-1", "valueChanged", self.preview.refresh)

    def perform_operation(self, objects):
        width = self.ui.s000.value()
        segments = self.ui.s001.value()
        Bevel.bevel(objects, width, segments)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("bevel", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
