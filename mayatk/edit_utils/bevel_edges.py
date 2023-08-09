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
        self.preview = Preview(
            self.sb.ui.chk000,
            self.sb.ui.b000,
            operation_func=self.perform_bevel,
            message_func=self.sb.message_box,
        )
        self.sb.connect_multi(
            self.sb.ui,
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


class BevelEdgesUI:
    @staticmethod
    def launch(move_to_cursor=False, frameless=False):
        """Launch the UI"""
        from PySide2 import QtCore
        from uitk import Switchboard

        parent = CoreUtils.get_main_window()
        sb = Switchboard(
            parent, ui_location="bevel_edges.ui", slots_location=BevelEdgesSlots
        )

        if frameless:
            sb.ui.setWindowFlags(QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint)
            sb.ui.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        else:
            sb.ui.setWindowTitle("Bevel Edges")

        if move_to_cursor:
            sb.center_widget(sb.ui, "cursor")
        else:
            sb.center_widget(sb.ui)

        sb.ui.set_style(theme="dark", style_class="translucentBgWithBorder")
        sb.ui.set_flags("WindowStaysOnTopHint")
        sb.ui.show()


# -----------------------------------------------------------------------------


if __name__ == "__main__":
    BevelEdgesUI.launch(frameless=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
