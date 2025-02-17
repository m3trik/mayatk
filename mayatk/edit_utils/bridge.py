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


class BridgeUi:
    def __new__(self):
        """Get the Bridge UI."""
        import os
        from mayatk.ui_utils.ui_manager import UiManager

        ui_file = os.path.join(os.path.dirname(__file__), "bridge.ui")
        ui = UiManager.get_ui(ui_source=ui_file, slot_source=BridgeSlots)
        return ui


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    BridgeUi().show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
