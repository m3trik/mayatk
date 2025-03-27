# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
# from this package:
from mayatk.core_utils import preview
from mayatk.core_utils import components


class Bridge:
    @staticmethod
    def bridge(edges, **kwargs):
        """ """
        mapped_edges = components.Components.map_components_to_objects(edges)

        for edges in mapped_edges.values():
            pm.polyBridgeEdge(edges, **kwargs)


class BridgeSlots:
    def __init__(self, **kwargs):
        self.sb = kwargs.get("switchboard")
        self.ui = self.sb.loaded_ui.bridge

        self.preview = preview.Preview(
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

    def perform_operation(self, objects):
        kwargs = {
            "curveType": self.ui.cmb000.currentIndex(),
            "divisions": self.ui.s000.value(),
            "smoothingAngle": self.ui.s001.value(),
            "bridgeOffset": self.ui.s002.value(),
            "taper": self.ui.s003.value(),
            "twist": self.ui.s004.value(),
        }

        Bridge.bridge(objects, **kwargs)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.default().get("bridge", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
