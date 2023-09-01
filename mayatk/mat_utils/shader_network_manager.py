# !/usr/bin/python
# coding=utf-8
try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
try:
    import MaterialX as mx
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils import CoreUtils
from mayatk.node_utils import NodeUtils


__version__ = "0.0.0"


class ShaderNetworkManager:
    def __init__(self):
        self.doc = mx.createDocument()

    def get_selected_network(self):
        # Get the selected materials
        selected_materials = pm.ls(selection=True, materials=True)
        if selected_materials:
            # Return the first selected material
            return selected_materials[0]
        else:
            raise Exception("No shader network selected.")

    def list_shader_networks(self):
        # Get all shading engine nodes
        shading_engines = pm.ls(type="shadingEngine")

        # Retrieve the materials connected to the shading engines
        shader_networks = [
            pm.listConnections(se + ".surfaceShader") for se in shading_engines
        ]
        shader_networks = [sn[0] for sn in shader_networks if sn]

        return shader_networks

    def import_network(self, path: str):
        # Read the MaterialX document from a file
        mx.readFromXmlFile(self.doc, path)

        # Translate the MaterialX nodes into a shader network
        node_graph = self.doc.getNodeGraph("NodeGraph")
        shader_network = self._convert_from_materialx(node_graph)

        return shader_network

    def export_network(self, shader_network, path: str):
        # Create a MaterialX node graph to represent the shader network
        node_graph = self.doc.addNodeGraph("NodeGraph")

        # Convert the shader network into MaterialX nodes
        self._convert_to_materialx(shader_network, node_graph)

        # Write the MaterialX document to a file
        mx.writeToXmlFile(self.doc, path)

    def _convert_to_materialx(self, shader_network, node_graph):
        # Logic to translate the shader network into MaterialX nodes
        # This can be customized for different types of shaders
        pass

    def _convert_from_materialx(self, node_graph):
        # Logic to translate MaterialX nodes into a shader network
        # This can be customized for different types of shaders
        pass


class ShaderNetworkManagerSlots(ShaderNetworkManager):
    def __init__(self):
        super().__init__()
        self.sb = self.switchboard()
        self.ui = self.sb.shader_network_manager

    def b000(self):
        """Import Network"""
        file = self.sb.file_dialog(
            file_types=["*.mtlx"],  # MaterialX file extension
            title="Select a MaterialX file to import.",
        )
        if file:
            self.import_network(file)

    def b001(self):
        """Export Network"""
        network = self.get_selected_network()
        file = self.sb.file_dialog(
            save=True,
            file_types=["*.mtlx"],  # MaterialX file extension
            title="Save the MaterialX file.",
        )
        if file and network:
            self.export_network(network, file)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    from uitk import Switchboard

    parent = CoreUtils.get_main_window()
    ui_file = os.path.join(os.path.dirname(__file__), "shader_network_manager.ui")
    sb = Switchboard(
        parent, ui_location=ui_file, slot_location=ShaderNetworkManagerSlots
    )

    sb.current_ui.set_attributes(WA_TranslucentBackground=True)
    sb.current_ui.set_flags(FramelessWindowHint=True, WindowStaysOnTopHint=True)
    sb.current_ui.set_style(theme="dark", style_class="translucentBgWithBorder")
    sb.current_ui.header.configureButtons(minimize_button=True, hide_button=True)
    sb.current_ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
