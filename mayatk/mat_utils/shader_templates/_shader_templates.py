import os
import logging
import yaml
from typing import Any, List, Optional

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.node_utils import NodeUtils
from mayatk.env_utils import EnvUtils


class GraphCollector:
    def __init__(self):
        self.placeholder_counter = {}
        self.node_name_map = {}

    def collect_graph(self, nodes):
        visited_nodes = set()
        acceptable_nodes = {str(node) for node in nodes}

        graph_info = {}
        for node in nodes:
            self._process_node(node, graph_info, visited_nodes, acceptable_nodes)
        return graph_info

    def _process_node(self, node, graph_info, visited_nodes, acceptable_nodes):
        node_name = str(node)
        if node_name not in acceptable_nodes or node_name in visited_nodes:
            return
        visited_nodes.add(node_name)

        placeholder_name = self._get_placeholder_name(node)
        node_type = pm.nodeType(node)
        self._create_node_entry(graph_info, placeholder_name, node, node_type)
        self._process_connections(node, graph_info, placeholder_name, acceptable_nodes)

    def _get_placeholder_name(self, node):
        node_name = str(node)
        if node_name in self.node_name_map:
            return self.node_name_map[node_name]

        node_type = pm.nodeType(node)
        self.placeholder_counter[node_type] = (
            self.placeholder_counter.get(node_type, 0) + 1
        )
        placeholder_name = (
            f"{{{{NODE_{node_type}_{self.placeholder_counter[node_type]}}}}}"
        )
        self.node_name_map[node_name] = placeholder_name
        return placeholder_name

    def _create_node_entry(self, graph_info, placeholder_name, node, node_type):
        attributes = NodeUtils.get_node_attributes(node, exc_defaults=True)
        graph_info[placeholder_name] = {
            "type": node_type,
            "attributes": attributes,
            "connections": [],
            "metadata": {
                "connected_to_shading_engine": self._is_connected_to_shading_engine(
                    node
                )
            },
        }

        # Adding metadata for file nodes regarding their map type
        if node_type == "file":
            image_name = pm.getAttr(f"{node}.fileTextureName", "")
            map_type = ptk.resolve_map_type(image_name)
            graph_info[placeholder_name]["metadata"]["map_type"] = map_type

    def _is_connected_to_shading_engine(self, node):
        # Check connections for links to ShadingEngine nodes
        for connection in node.connections():
            if isinstance(connection, pm.nt.ShadingEngine):
                return True
        return False

    def _process_connections(
        self, node, graph_info, placeholder_name, acceptable_nodes
    ):
        for connection in node.connections(c=True, p=True, scn=True):
            src_attr, dest_attr = connection
            if src_attr.isSource() and dest_attr.isDestination():
                if (
                    str(src_attr.node()) in acceptable_nodes
                    and str(dest_attr.node()) in acceptable_nodes
                ):
                    self._create_connection_entry(
                        graph_info, placeholder_name, src_attr, dest_attr
                    )

    def _create_connection_entry(
        self, graph_info, placeholder_name, src_attr, dest_attr
    ):
        src_node_placeholder = self._get_placeholder_name(src_attr.node())
        dest_node_placeholder = self._get_placeholder_name(dest_attr.node())
        connection_info = {
            "source": f"{src_node_placeholder}.{src_attr.attrName()}",
            "target": f"{dest_node_placeholder}.{dest_attr.attrName()}",
        }
        graph_info[placeholder_name]["connections"].append(connection_info)


class GraphSaver(GraphCollector):
    def save_graph(
        self,
        nodes: List[object],
        file_path: str,
        exclude_types: Optional[List[str]] = None,  # Accept list of strings directly
    ) -> None:
        # Convert exclude_types to lowercase for case-insensitive comparison
        exclude_types_lower = [t.lower() for t in ptk.make_iterable(exclude_types)]

        # Filter nodes to exclude specified types using their type name, converted to lowercase
        filtered_nodes = [
            node for node in nodes if node.nodeType().lower() not in exclude_types_lower
        ]

        graph_info = self.collect_graph(filtered_nodes)
        graph_info_basic = self._convert_to_basic_types(graph_info)

        try:
            with open(file_path, "w") as file:
                yaml.dump(graph_info_basic, file, default_flow_style=False)
            print(f"Graph information saved to {file_path}")
        except IOError as e:
            print(f"Failed to save graph to {file_path}. Error: {e}")

    @staticmethod
    def _convert_to_basic_types(data: Any) -> Any:
        if isinstance(data, dict):
            return {
                key: GraphSaver._convert_to_basic_types(value)
                for key, value in data.items()
            }
        elif isinstance(data, (list, tuple)):
            return [GraphSaver._convert_to_basic_types(item) for item in data]
        return data


class GraphRestorer:
    def __init__(self, yaml_file_path, texture_paths, name=None):
        self.yaml_file_path = yaml_file_path
        self.texture_paths = texture_paths  # List of texture paths
        self.name = name  # Custom name for the shader if provided
        self.graph_config = self.load_yaml()
        self.nodes = {}  # Dictionary to map placeholders to PyNode objects

    def load_yaml(self):
        """Load and return graph configuration from a YAML file."""
        try:
            with open(self.yaml_file_path, "r") as file:
                return yaml.safe_load(file)
        except Exception as e:
            logging.error(
                f"Failed to load YAML file: {self.yaml_file_path}. Error: {e}"
            )
            return {}

    def restore_graph(self):
        """Restore the graph based on the YAML configuration and textures."""
        if not self.graph_config:
            logging.warning("Graph configuration is empty. Nothing to restore.")
            return

        # Dictionary to hold available map types and their paths
        available_map_types = {
            ptk.resolve_map_type(path): path for path in self.texture_paths
        }

        for placeholder, node_info in self.graph_config.items():
            node_type = node_info["type"]
            attributes = node_info.get("attributes", {})
            metadata = node_info.get("metadata", {})
            required_map_type = metadata.get("map_type", "")

            file_path = available_map_types.get(required_map_type)

            # If the required texture is not available, try to generate it
            if not file_path and required_map_type:
                file_path = self.generate_missing_map(
                    required_map_type, available_map_types
                )

            # Set the file path if available
            if file_path:
                attributes["fileTextureName"] = file_path

            # Determine the name for shaders and shading groups
            node_name = None  # Only name shaders
            classification_string = pm.getClassification(node_type)
            if any("shader/surface" in c for c in classification_string):
                # Use provided name or derive from texture if not given
                node_name = (
                    self.name
                    if self.name
                    else ptk.get_base_texture_name(self.texture_paths[0])
                )

            node = NodeUtils.create_render_node(
                node_type,
                name=node_name,
                create_shading_group=metadata.get("connected_to_shading_engine", False),
                **attributes,
            )

            if node:
                self.nodes[placeholder] = node
                if node_name and "shader" in node_type:
                    # Optionally name the shading group linked to this shader
                    shading_group = pm.sets(
                        renderable=True,
                        noSurfaceShader=True,
                        empty=True,
                        name=f"{node_name}SG",
                    )
                    pm.connectAttr(f"{node}.outColor", f"{shading_group}.surfaceShader")
            else:
                logging.error(f"Failed to create node: {placeholder}")

        self.restore_connections()

    @staticmethod
    def generate_missing_map(required_map, provided_map_types):
        """Attempts to generate a missing map based on available ones. Supports generating:
        - Normal_OpenGL from Normal_DirectX and vice versa.
        - MetallicSmoothness from separate Metallic and Smoothness/Roughness maps.
        - AlbedoTransparency from separate Albedo and Transparency maps.

        Parameters:
            required_map (str): The type of the map that needs to be generated.
            provided_map_types (Dict[str, str]): A dictionary with available map types as keys and paths to texture files as values.

        Returns:
            str or None: The path to the generated map file if successful, None otherwise.
        """
        # Handle Normal map conversion
        if required_map == "Normal_OpenGL" and "Normal_DirectX" in provided_map_types:
            return ptk.create_gl_from_dx(provided_map_types["Normal_DirectX"])
        elif required_map == "Normal_DirectX" and "Normal_OpenGL" in provided_map_types:
            return ptk.create_dx_from_gl(provided_map_types["Normal_OpenGL"])

        # Generate MetallicSmoothness from separate Metallic and Smoothness/Roughness maps
        if required_map == "Metallic_Smoothness":
            metallic_path = provided_map_types.get("Metallic")
            smoothness_path = provided_map_types.get("Smoothness")
            roughness_path = provided_map_types.get("Roughness")

            # Prefer smoothness over roughness; if both are missing, return None
            alpha_map_path = (
                smoothness_path
                if smoothness_path
                else (roughness_path if roughness_path else None)
            )
            if metallic_path and alpha_map_path:
                # Use roughness_path if smoothness is not available and invert the map as roughness is inverse of smoothness
                invert_alpha = True if not smoothness_path and roughness_path else False
                return ptk.pack_smoothness_into_metallic(
                    metallic_map_path=metallic_path,
                    alpha_map_path=alpha_map_path,
                    invert_alpha=invert_alpha,
                )

        # Generate AlbedoTransparency from separate Albedo and Transparency maps
        if required_map == "Albedo_Transparency":
            albedo_path = provided_map_types.get("Base_Color")
            transparency_path = provided_map_types.get("Opacity")

            if albedo_path and transparency_path:
                return ptk.pack_transparency_into_albedo(
                    albedo_map_path=albedo_path, alpha_map_path=transparency_path
                )

        return None

    def restore_connections(self):
        """Connect nodes as specified in the graph configuration."""
        for placeholder, node_info in self.graph_config.items():
            node = self.nodes.get(placeholder)
            if not node:
                logging.error(f"Node for placeholder {placeholder} not found.")
                continue

            for connection in node_info.get("connections", []):
                src_placeholder, src_attr = connection["source"].split(".")
                tgt_placeholder, tgt_attr = connection["target"].split(".")

                src_node = self.nodes.get(src_placeholder)
                tgt_node = self.nodes.get(tgt_placeholder)

                if src_node and tgt_node:
                    try:
                        pm.connectAttr(
                            f"{src_node}.{src_attr}",
                            f"{tgt_node}.{tgt_attr}",
                            force=True,
                        )
                    except Exception as e:
                        logging.error(
                            f"Failed to connect {src_placeholder}.{src_attr} to {tgt_placeholder}.{tgt_attr}: {str(e)}"
                        )
                else:
                    if not src_node:
                        logging.error(f"Source node {src_placeholder} not found.")
                    if not tgt_node:
                        logging.error(f"Target node {tgt_placeholder} not found.")


class QTextEditLogger(logging.Handler):
    def __init__(self, widget):
        super().__init__()
        self.widget = widget
        self.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )

    def emit(self, record):
        msg = self.format(record)
        self.widget.append(msg)


class ShaderTemplatesSlots:
    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.shader_templates

        self.workspace_dir = EnvUtils.get_env_info("workspace_dir")
        self.source_images_dir = os.path.join(self.workspace_dir, "sourceimages")
        self.image_files = None

        # Setup logging
        self.logger = logging.getLogger("ShaderTemplateManager")
        self.logger.setLevel(log_level)
        log_handler = QTextEditLogger(self.ui.txt001)
        self.logger.addHandler(log_handler)

        # Load plugins
        EnvUtils.load_plugin("shaderFXPlugin")  # Load Stingray plugin
        EnvUtils.load_plugin("mtoa")  # Load Arnold plugin

    @property
    def template_name(self):
        return "test"

    def cmb002_init(self, widget):
        """Initialize the ComboBox for shader templates."""
        if not widget.is_initialized:
            widget.restore_state = True  # Enable state restore
            widget.refresh_on_show = True  # Call this method on show
            widget.menu.mode = "context"
            widget.menu.setTitle("Template Options")
            widget.menu.add(
                self.sb.registered_widgets.Label,
                setObjectName="lbl000",
                setText="Rename",
                setToolTip="Rename the current template.",
            )
            widget.menu.add(
                self.sb.registered_widgets.Label,
                setObjectName="lbl001",
                setText="Delete",
                setToolTip="Delete the current template.",
            )
            widget.on_editing_finished.connect(
                lambda text: self.rename_template_safe(widget, text)
            )
        self.refresh_templates(widget)

    def refresh_templates(self, widget):
        """Refresh the list of templates."""
        template_directory = os.path.join(os.path.dirname(__file__), "templates")
        if not os.path.exists(template_directory):
            os.makedirs(template_directory)

        yaml_files = [f for f in os.listdir(template_directory) if f.endswith(".yaml")]
        items = {
            os.path.splitext(f)[0]: os.path.join(template_directory, f)
            for f in yaml_files
        }
        widget.clear()
        for label, path in items.items():
            widget.addItem(label, path)  # Make sure to set the item data here

    def rename_template_safe(self, widget, new_name):
        """Safe rename that checks for None."""
        current_path = widget.currentData()
        if current_path is None:
            self.log.error("No template selected or data is missing.")
            return

        new_path = os.path.join(os.path.dirname(current_path), new_name + ".yaml")
        if os.path.exists(new_path):
            self.log.error("File with new name already exists.")
            return

        os.rename(current_path, new_path)
        self.log.info(f"Template renamed to: {new_path}")
        widget.init_slot()  # Refresh ComboBox

    def lbl000(self):
        """Set the ComboBox as editable to allow renaming."""
        self.ui.cmb002.setEditable(True)
        self.ui.cmb002.menu.hide()

    def lbl001(self):
        """Delete the selected template."""
        template_path = self.ui.cmb002.currentData()
        if os.path.exists(template_path):
            os.remove(template_path)
            self.log.info(f"Template deleted: {template_path}")
        self.ui.cmb002.init_slot()  # Refresh ComboBox

    def b000(self):
        """Create shader network using selected template."""
        if self.image_files:
            self.ui.txt001.clear()
            self.log.info("Creating network based on template...")

            yaml_file_path = self.ui.cmb002.currentData()
            self.graph_restorer = GraphRestorer(yaml_file_path, self.image_files)
            self.graph_restorer.restore_graph()

            self.log.info("COMPLETED.")

    def b001(self):
        """Load texture maps and update GUI."""
        image_files = self.sb.file_dialog(
            file_types=["*.png", "*.jpg", "*.bmp", "*.tga", "*.tiff", "*.gif"],
            title="Select one or more image files to open.",
            start_dir=self.source_images_dir,
        )

        if image_files:
            self.image_files = image_files
            self.ui.txt001.clear()
            for img in image_files:
                self.log.info(ptk.truncate(img, 60))

    def b002(self):
        """Save current graph as a new shader template."""
        selected_nodes = pm.selected()
        script_directory = os.path.dirname(__file__)
        template_directory = os.path.join(script_directory, "templates")

        if not os.path.exists(template_directory):
            os.makedirs(template_directory)

        file_path = os.path.join(template_directory, f"{self.template_name}.yaml")

        if os.path.exists(file_path):
            self.log.error("File already exists.")
            return

        self.graph_saver = GraphSaver()
        self.graph_saver.save_graph(
            selected_nodes, file_path, exclude_types="shadingEngine"
        )
        self.log.info(f"Shader template saved as: {file_path}")
        self.ui.cmb002.init_slot()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("shader_templates", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
