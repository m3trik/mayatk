# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
# from this package:
from mayatk.core_utils import CoreUtils, Preview
from mayatk.component_utils import ComponentUtils


class BevelEdges:
    @staticmethod
    def bevel_edges(
        edges,
        width=5,
        segments=1,
    ):
        mapped_edges = ComponentUtils.map_components_to_objects(edges)

        for edges in mapped_edges.values():
            pm.polyBevel3(
                edges,
                offsetAsFraction=True,
                fraction=width,
                segments=segments,
                mergeVertices=True,
                mergeVertexTolerance=0.0001,
                worldSpace=False,
                constructionHistory=True,
            )


class BevelEdgesSlots:
    def __init__(self):
        self.sb = self.switchboard()
        ui = self.sb.bevel_edges

        self.preview = Preview(
            ui.chk000,
            ui.b000,
            operation_func=self.perform_bevel,
            message_func=self.sb.message_box,
        )
        self.sb.connect_multi(
            ui,
            "s000-1",
            "valueChanged",
            self.preview.refresh,
        )

    def perform_bevel(self):
        """Perform the linear duplication operation."""
        objects = pm.ls(sl=True)

        width = self.sb.ui.s000.value()
        segments = self.sb.ui.s001.value()

        BevelEdges.bevel_edges(
            objects,
            width,
            segments,
        )


def get_ui_file():
    import os

    return os.path.join(os.path.dirname(__file__), "bevel_edges.ui")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from uitk import Switchboard

    parent = CoreUtils.get_main_window()
    sb = Switchboard(parent, ui_location=get_ui_file(), slot_location=BevelEdgesSlots)

    sb.ui.set_attributes(WA_TranslucentBackground=True)
    sb.ui.set_flags(Tool=True, FramelessWindowHint=True, WindowStaysOnTopHint=True)
    sb.ui.set_style(theme="dark", style_class="translucentBgWithBorder")

    sb.ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
