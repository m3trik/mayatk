import os
import logging
import yaml
from typing import Any, List, Optional, Dict, Callable

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)
import pythontk as ptk
from pythontk.img_utils.texture_map_factory import (
    ConversionRegistry,
    ProcessingContext,
    TextureMapFactory,
)

# from this package:
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.env_utils._env_utils import EnvUtils


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

        # Ensure fileTextureName is captured for file nodes
        if node_type == "file":
            image_name = pm.getAttr(f"{node}.fileTextureName")
            if image_name:
                attributes["fileTextureName"] = str(image_name)

        # Dynamic filtering: Remove attributes that are connected or are message types
        filtered_attributes = {}
        for attr_name, value in attributes.items():
            if value is None:
                continue

            try:
                attr = node.attr(attr_name)
                # Skip if connected (driven by another node) or is a message attribute
                if attr.isDestination() or attr.type() == "message":
                    continue
            except Exception:
                pass

            filtered_attributes[attr_name] = value

        graph_info[placeholder_name] = {
            "type": node_type,
            "attributes": filtered_attributes,
            "connections": [],
            "metadata": {
                "connected_to_shading_engine": self._is_connected_to_shading_engine(
                    node
                )
            },
        }

        # Adding metadata for file nodes regarding their map type
        if node_type == "file":
            image_name = attributes.get("fileTextureName", "")
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
            "source": f"{src_node_placeholder}.{src_attr.longName()}",
            "target": f"{dest_node_placeholder}.{dest_attr.longName()}",
        }
        graph_info[placeholder_name]["connections"].append(connection_info)


class GraphSaver(GraphCollector):
    def save_graph(
        self,
        nodes: List[object],
        file_path: str,
        exclude_types: Optional[List[str]] = None,  # Accept list of strings directly
    ) -> None:
        if not nodes:
            pm.warning("No nodes selected or provided for template saving.")
            return

        # Expand nodes to include upstream history to capture the full network
        nodes = pm.listHistory(nodes)

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
        self.registry = ConversionRegistry()

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

        logger = logging.getLogger("ShaderTemplateManager")

        # Check if file nodes are present in the template
        file_nodes_found = any(
            info["type"] == "file" for info in self.graph_config.values()
        )
        if self.texture_paths and not file_nodes_found:
            logger.warning(
                "Texture paths provided but no file nodes found in template. The template might be incomplete or saved with an older version."
            )

        # Dictionary to hold available map types and their paths
        available_map_types = {}
        for path in self.texture_paths:
            map_type = ptk.resolve_map_type(path)
            if map_type:
                available_map_types[map_type] = path
                logger.info(f"Resolved '{os.path.basename(path)}' as '{map_type}'")
            else:
                logger.warning(
                    f"Could not resolve map type for '{os.path.basename(path)}'"
                )

        for placeholder, node_info in self.graph_config.items():
            self._restore_node(placeholder, node_info, available_map_types)

        self.restore_connections()

    def _restore_node(self, placeholder, node_info, available_map_types):
        logger = logging.getLogger("ShaderTemplateManager")
        node_type = node_info["type"]
        attributes = node_info.get("attributes", {})
        metadata = node_info.get("metadata", {})
        required_map_type = metadata.get("map_type", "")

        # Determine output directory and base name from available textures
        output_dir = ""
        base_name = "generated_map"
        ext = "png"

        if available_map_types:
            first_path = next(iter(available_map_types.values()))
            output_dir = os.path.dirname(first_path)
            base_name = ptk.get_base_texture_name(first_path)
            ext = os.path.splitext(first_path)[1].lstrip(".")

        context = ProcessingContext(
            inventory=available_map_types,
            config={},
            output_dir=output_dir,
            base_name=base_name,
            ext=ext,
            callback=lambda msg: logger.info(
                msg.replace("<br>", "").replace("<hl>", "").replace("</hl>", "")
            ),
            conversion_registry=self.registry,
        )

        file_path = None
        if required_map_type:
            fallbacks = TextureMapFactory.get_map_fallbacks(required_map_type)
            candidates = [required_map_type] + list(fallbacks)
            file_path = context.resolve_map(*candidates, allow_conversion=True)

        # Set the file path if available
        if file_path:
            attributes["fileTextureName"] = file_path
            logger.info(
                f"Node '{placeholder}': Assigned '{os.path.basename(file_path)}' to '{required_map_type}'"
            )
        elif required_map_type:
            logger.warning(
                f"Node '{placeholder}': Missing texture for '{required_map_type}'"
            )

        # Determine the name for shaders and shading groups
        node_name = self._determine_node_name(node_type)

        # Ensure fileTextureName is passed explicitly if present in attributes
        # This handles cases where attributes might be filtered or processed differently
        ftn = attributes.get("fileTextureName")

        node = NodeUtils.create_render_node(
            node_type,
            name=node_name,
            create_shading_group=metadata.get("connected_to_shading_engine", False),
            **attributes,
        )

        if node and ftn:
            try:
                pm.setAttr(f"{node}.fileTextureName", ftn, type="string")
            except Exception as e:
                logger.warning(f"Failed to set fileTextureName on {node}: {e}")

        if node:
            self.nodes[placeholder] = node
            self._handle_shading_group(node, node_name, node_type)
        else:
            logging.error(f"Failed to create node: {placeholder}")

    def _determine_node_name(self, node_type):
        classification_string = pm.getClassification(node_type)
        if any("shader/surface" in c for c in classification_string):
            # Use provided name or derive from texture if not given
            if self.name:
                return self.name
            elif self.texture_paths:
                return ptk.get_base_texture_name(self.texture_paths[0])
        return None

    def _handle_shading_group(self, node, node_name, node_type):
        if node_name and "shader" in node_type:
            # Optionally name the shading group linked to this shader
            shading_group = pm.sets(
                renderable=True,
                noSurfaceShader=True,
                empty=True,
                name=f"{node_name}SG",
            )
            pm.connectAttr(f"{node}.outColor", f"{shading_group}.surfaceShader")

    def restore_connections(self):
        """Connect nodes as specified in the graph configuration."""
        for placeholder, node_info in self.graph_config.items():
            node = self.nodes.get(placeholder)
            if not node:
                logging.error(f"Node for placeholder {placeholder} not found.")
                continue

            for connection in node_info.get("connections", []):
                try:
                    src_str = connection["source"]
                    tgt_str = connection["target"]

                    src_placeholder, src_attr = src_str.split(".", 1)
                    tgt_placeholder, tgt_attr = tgt_str.split(".", 1)

                    src_node = self.nodes.get(src_placeholder)
                    tgt_node = self.nodes.get(tgt_placeholder)

                    if src_node and tgt_node:
                        pm.connectAttr(
                            f"{src_node}.{src_attr}",
                            f"{tgt_node}.{tgt_attr}",
                            force=True,
                        )
                    else:
                        if not src_node:
                            logging.warning(
                                f"Source node {src_placeholder} not found for connection {src_str} -> {tgt_str}"
                            )
                        if not tgt_node:
                            logging.warning(
                                f"Target node {tgt_placeholder} not found for connection {src_str} -> {tgt_str}"
                            )
                except Exception as e:
                    logging.error(
                        f"Failed to connect {connection.get('source')} to {connection.get('target')}: {str(e)}"
                    )


class ShaderTemplates:
    """
    Facade class for managing shader templates.
    Provides high-level methods to save and restore shader graphs.
    """

    @staticmethod
    def save_template(nodes, file_path, exclude_types=None):
        """
        Save the specified nodes as a shader template.

        Args:
            nodes (list): List of Maya nodes to save.
            file_path (str): Path to the output YAML file.
            exclude_types (list, optional): List of node types to exclude.
        """
        saver = GraphSaver()
        saver.save_graph(nodes, file_path, exclude_types=exclude_types)

    @staticmethod
    def restore_template(file_path, texture_paths=None, name=None):
        """
        Restore a shader template from a file.

        Args:
            file_path (str): Path to the YAML template file.
            texture_paths (list, optional): List of texture paths to use.
            name (str, optional): Name for the restored shader.

        Returns:
            dict: Mapping of placeholder names to created Maya nodes.
        """
        if texture_paths is None:
            texture_paths = []
        restorer = GraphRestorer(file_path, texture_paths, name)
        restorer.restore_graph()
        return restorer.nodes


class ShaderTemplatesSlots(ptk.LoggingMixin):
    def __init__(self, switchboard, log_level="DEBUG"):
        super().__init__()

        self.sb = switchboard
        self.ui = self.sb.loaded_ui.shader_templates

        self.workspace_dir = EnvUtils.get_env_info("workspace_dir")
        self.source_images_dir = os.path.join(self.workspace_dir, "sourceimages")
        self.image_files = None
        self.last_restored_nodes = None

        # Setup logging
        self.logger.setLevel(log_level)
        self.logger.hide_logger_name(False)
        self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        self.logger.setup_logging_redirect(self.ui.txt001)

        # Load plugins
        EnvUtils.load_plugin("shaderFXPlugin")  # Load Stingray plugin
        EnvUtils.load_plugin("mtoa")  # Load Arnold plugin

    @property
    def template_name(self):
        return "test"

    def header_init(self, widget):
        """Initialize the header widget."""
        widget.setTitle("Shader Templates")
        # Open the templates directory
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setObjectName="lbl_open_templates_dir",
            setText="Open Templates Directory",
            setToolTip="Open the directory containing shader templates.",
        )
        widget.menu.add(
            self.sb.registered_widgets.Label,
            setObjectName="lbl_graph_material",
            setText="Graph Material",
            setToolTip="Graph the selected material in the Hypershade.",
        )

    def lbl_graph_material(self):
        """Graph the last restored material in the Hypershade."""
        if self.last_restored_nodes:
            MatUtils.graph_materials(self.last_restored_nodes)
        else:
            pm.warning("No material has been restored yet.")

    def lbl_open_templates_dir(self):
        """Open the shader templates directory in file explorer."""
        template_directory = os.path.join(os.path.dirname(__file__), "templates")
        ptk.open_explorer(template_directory, create_dir=True)

    def cmb002_init(self, widget):
        """Initialize the ComboBox for shader templates."""
        if not widget.is_initialized:
            widget.restore_state = True  # Enable state restore
            widget.refresh_on_show = True  # Call this method on show
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
            widget.menu.add(
                self.sb.registered_widgets.Label,
                setObjectName="lbl002",
                setText="Open Template File",
                setToolTip="Open the selected template YAML file in the default editor.",
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
            self.logger.error("No template selected or data is missing.")
            return

        new_path = os.path.join(os.path.dirname(current_path), new_name + ".yaml")
        if os.path.exists(new_path):
            self.logger.error("File with new name already exists.")
            return

        os.rename(current_path, new_path)
        self.logger.info(f"Template renamed to: {new_path}")
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
            self.logger.info(f"Template deleted: {template_path}")
        self.ui.cmb002.init_slot()  # Refresh ComboBox

    def lbl002(self):
        """Open the selected template in the default editor."""
        template_path = self.ui.cmb002.currentData()
        ptk.open_explorer(template_path)

    def b000(self):
        """Create shader network using selected template."""
        self.ui.txt001.clear()
        self.logger.info("Creating network based on template...")

        yaml_file_path = self.ui.cmb002.currentData()
        if not yaml_file_path:
            self.logger.error("No template selected.")
            return

        # Use the facade instead of direct instantiation
        restored_nodes = ShaderTemplates.restore_template(
            yaml_file_path, self.image_files or []
        )
        self.last_restored_nodes = list(restored_nodes.values())

        self.logger.info("COMPLETED.")

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
                self.logger.info(ptk.truncate(img, 60))

    def b002(self):
        """Save current graph as a new shader template."""
        selected_nodes = pm.selected()
        script_directory = os.path.dirname(__file__)
        template_directory = os.path.join(script_directory, "templates")

        if not os.path.exists(template_directory):
            os.makedirs(template_directory)

        file_path = os.path.join(template_directory, f"{self.template_name}.yaml")

        if os.path.exists(file_path):
            self.logger.error("File already exists.")
            return

        # Use the facade
        ShaderTemplates.save_template(
            selected_nodes,
            file_path,
            exclude_types=[
                "shadingEngine",
                "transform",
                "mesh",
                "nurbsCurve",
                "camera",
                "light",
            ],
        )
        self.logger.info(f"Shader template saved as: {file_path}")
        self.ui.cmb002.init_slot()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    from mayatk.ui_utils.ui_manager import UiManager

    ui = UiManager.instance().get("shader_templates", reload=True)
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
