# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
# from this package:
from mayatk.core_utils import _core_utils
from mayatk.core_utils import preview
from mayatk.core_utils import components


class BevelEdges:
    @staticmethod
    def bevel_edges(
        edges,
        width=5,
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
        - edges: List of edges to bevel
        - width: Bevel width as a fraction between 0 and 1
        - segments: Number of segments for the bevel
        - autoFit: Whether to compute a smooth roundness for new facets
        - depth: Depth of the bevel
        - mitering: Controls the topology at corners
        - miterAlong: Direction to offset new vertices
        - chamfer: Whether to smooth out the surface at bevels
        - worldSpace: Whether to use world space or object space for geometrical values
        - smoothingAngle: Angle for creating new hard edges
        - fillNgons: Whether to subdivide new faces with more than 4 edges
        - mergeVertices: Whether to merge vertices within a tolerance
        - mergeVertexTolerance: Tolerance within which to merge vertices
        - miteringAngle: Miter faces that have angles less than this value
        - angleTolerance: Angular tolerance for creation of extra triangles
        """

        mapped_edges = components.Components.map_components_to_objects(edges)

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


class BevelEdgesSlots:
    def __init__(self):
        self.sb = self.switchboard()
        self.ui = self.sb.bevel_edges
        self.preview = preview.Preview(
            self, self.ui.chk000, self.ui.b000, message_func=self.sb.message_box
        )

        self.sb.connect_multi(self.ui, "s000-1", "valueChanged", self.preview.refresh)

    def perform_operation(self, objects):
        width = self.ui.s000.value()
        segments = self.ui.s001.value()
        BevelEdges.bevel_edges(objects, width, segments)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    from uitk import Switchboard

    parent = _core_utils.CoreUtils.get_main_window()
    ui_file = os.path.join(os.path.dirname(__file__), "bevel_edges.ui")
    sb = Switchboard(parent, ui_location=ui_file, slot_location=BevelEdgesSlots)

    sb.current_ui.set_attributes(WA_TranslucentBackground=True)
    sb.current_ui.set_flags(
        Tool=True, FramelessWindowHint=True, WindowStaysOnTopHint=True
    )
    sb.current_ui.set_style(theme="dark", style_class="translucentBgWithBorder")

    sb.current_ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
