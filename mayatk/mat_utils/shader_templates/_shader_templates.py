import os
import logging
import yaml
from typing import Any, List, Optional

try:
    import maya.cmds as cmds
except ImportError:
    pass
import pythontk as ptk
from pythontk.core_utils.engines.textures.map_factory import (
    ConversionRegistry,
    TextureProcessor,
    MapFactory,
)

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.plugins._plugins import Plugins
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.node_utils.attributes._attributes import Attributes

# Default logger for library (non-panel) use. The slots pass their own
# LoggingMixin logger in so save/restore feedback lands in the panel's
# text widget — a stdlib logger here would never reach that handler.
_LOGGER = logging.getLogger("ShaderTemplateManager")


class _ShaderTemplatesInternal(ptk.TableMixin):
    """Shared logging helpers for the graph collector/saver/restorer.

    ``TableMixin`` supplies ``log_group`` / ``log_table``, which emit one
    record on a LoggerExt-patched logger and degrade to plain lines on the
    arbitrary ``logger=`` a library caller may pass to ``save_template`` /
    ``restore_template``. Every log record renders as its own paragraph in a
    text-widget handler, so per-item logging reads as N blank-line-separated
    sections rather than a list.
    """

    @staticmethod
    def _log_path(logger, path: str) -> str:
        """A clickable ``action://open`` label for *path*, or the bare path.

        ``log_link`` markup is only meaningful to a handler that renders HTML,
        so a plain injected logger (console, file) gets the raw path instead of
        an ``<a>`` tag it would print verbatim. The *label* is truncated rather
        than the href — ``TextLayout.wrap_text`` never hard-wraps a word containing a
        tag, so a full path inside a box would force it wider than the panel
        instead of wrapping.
        """
        if hasattr(logger, "log_link"):
            return logger.log_link(ptk.truncate(path, 60), "open", path=path)
        return path


class GraphCollector(_ShaderTemplatesInternal):
    """Walk a shading network and serialize it to placeholder-keyed graph info."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or _LOGGER
        self.placeholder_counter = {}
        self.node_name_map = {}
        self.image_types = {}

    def collect_graph(self, nodes):
        visited_nodes = set()
        acceptable_nodes = {str(node) for node in nodes}
        # The graph's textures are one set, read together (a set named for a
        # lobe, `Hero_Coat`, records its roughness as Roughness) -- the same
        # reading `GraphRestorer` gives the paths it rebinds by type.
        self.image_types = ptk.MapFactory.resolve_map_types(
            [
                image
                for image in (
                    cmds.getAttr(f"{node}.fileTextureName")
                    for node in acceptable_nodes
                    if cmds.nodeType(node) == "file"
                )
                if image
            ]
        )

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
        node_type = cmds.nodeType(str(node))
        self._create_node_entry(graph_info, placeholder_name, node, node_type)
        self._process_connections(node, graph_info, placeholder_name, acceptable_nodes)

    def _get_placeholder_name(self, node):
        node_name = str(node)
        if node_name in self.node_name_map:
            return self.node_name_map[node_name]

        node_type = cmds.nodeType(str(node))
        self.placeholder_counter[node_type] = (
            self.placeholder_counter.get(node_type, 0) + 1
        )
        placeholder_name = (
            f"{{{{NODE_{node_type}_{self.placeholder_counter[node_type]}}}}}"
        )
        self.node_name_map[node_name] = placeholder_name
        return placeholder_name

    def _create_node_entry(self, graph_info, placeholder_name, node, node_type):
        attributes = Attributes.get_attributes(node, exc_defaults=True)

        map_type = None
        if node_type == "file":
            image_name = cmds.getAttr(f"{node}.fileTextureName") or ""
            map_type = self.image_types.get(image_name) or MapFactory.resolve_map_type(
                image_name
            )
            # A file node whose texture resolves to a known map_type is a
            # user-supplied slot: the path is provided by the caller and
            # re-resolved at restore time from ``texture_paths``. Persisting it
            # would bake a machine-specific (often project-relative
            # ``.../foo/../../../bar``) absolute path into the shared template.
            # Only keep the path for nodes with no resolved map_type — e.g. the
            # StingrayPBS environment cube maps, which are fixed Maya-install
            # defaults that every machine shares.
            if image_name and not map_type:
                attributes["fileTextureName"] = str(image_name)
            else:
                attributes.pop("fileTextureName", None)

        # Dynamic filtering: Remove attributes that are connected or are message types
        filtered_attributes = {}
        for attr_name, value in attributes.items():
            if value is None:
                continue

            try:
                plug = f"{node}.{attr_name}"
                # Skip if connected (driven by another node) or is a message attribute
                is_dest = bool(
                    cmds.listConnections(plug, source=True, destination=False)
                )
                attr_type = cmds.getAttr(plug, type=True)
                if is_dest or attr_type == "message":
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

        if node_type == "file":
            graph_info[placeholder_name]["metadata"]["map_type"] = map_type

    def _is_connected_to_shading_engine(self, node):
        for connection in cmds.listConnections(str(node)) or []:
            if cmds.nodeType(connection) == "shadingEngine":
                return True
        return False

    def _process_connections(
        self, node, graph_info, placeholder_name, acceptable_nodes
    ):
        # Get outgoing connections: node is source → destination=True
        plug_pairs = (
            cmds.listConnections(
                str(node),
                connections=True,
                plugs=True,
                source=False,
                destination=True,
                skipConversionNodes=True,
            )
            or []
        )
        for i in range(0, len(plug_pairs), 2):
            src_attr = plug_pairs[i]  # plug on queried node (output)
            dest_attr = plug_pairs[i + 1]  # plug on connected node (input)
            src_node = src_attr.split(".")[0]
            dest_node = dest_attr.split(".")[0]
            if src_node in acceptable_nodes and dest_node in acceptable_nodes:
                self._create_connection_entry(
                    graph_info, placeholder_name, src_attr, dest_attr
                )

    def _create_connection_entry(
        self, graph_info, placeholder_name, src_attr, dest_attr
    ):
        src_node = src_attr.split(".")[0]
        dest_node = dest_attr.split(".")[0]
        src_attr_name = src_attr.split(".", 1)[1]
        dest_attr_name = dest_attr.split(".", 1)[1]
        src_node_placeholder = self._get_placeholder_name(src_node)
        dest_node_placeholder = self._get_placeholder_name(dest_node)
        connection_info = {
            "source": f"{src_node_placeholder}.{src_attr_name}",
            "target": f"{dest_node_placeholder}.{dest_attr_name}",
        }
        graph_info[placeholder_name]["connections"].append(connection_info)


class GraphSaver(GraphCollector):
    def save_graph(
        self,
        nodes: List[str],
        file_path: str,
        exclude_types: Optional[List[str]] = None,
    ) -> None:
        if not nodes:
            self.logger.warning("No nodes selected or provided for template saving.")
            return

        nodes = cmds.listHistory(nodes) or []

        exclude_types_lower = [t.lower() for t in ptk.make_iterable(exclude_types)]

        filtered_nodes = [
            node
            for node in nodes
            if cmds.nodeType(str(node)).lower() not in exclude_types_lower
        ]

        graph_info = self.collect_graph(filtered_nodes)
        graph_info_basic = self._convert_to_basic_types(graph_info)

        try:
            with open(file_path, "w") as file:
                yaml.dump(graph_info_basic, file, default_flow_style=False)
            # The ONE announcement of the save — the panel deliberately adds
            # nothing on top, so this event occupies a single paragraph rather
            # than two saying the same thing. Carries the node count and a
            # clickable path when the caller's logger supports links.
            self.logger.info(
                f"Graph information saved to {self._log_path(self.logger, file_path)} "
                f"({len(graph_info_basic)} node(s))"
            )
        except IOError as e:
            self.logger.error(f"Failed to save graph to {file_path}. Error: {e}")

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


class GraphRestorer(_ShaderTemplatesInternal):
    def __init__(
        self,
        yaml_file_path: str,
        texture_paths: List[str],
        name: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.logger = logger or _LOGGER
        self.yaml_file_path = yaml_file_path
        self.texture_paths = texture_paths  # List of texture paths
        self.name = name  # Custom name for the shader if provided
        self.graph_config = self.load_yaml()
        self.nodes = {}  # Dictionary to map placeholders to node names
        self.registry = ConversionRegistry()
        # Per-node detail accrues here and is emitted by ``restore_graph`` as
        # ONE grouped record. Logging it line-by-line puts a paragraph break
        # between every node in the panel's output (see ``log_group``).
        self._node_log: List[str] = []

    def load_yaml(self):
        """Load and return graph configuration from a YAML file."""
        try:
            with open(self.yaml_file_path, "r") as file:
                data = yaml.safe_load(file)
        except Exception as e:
            self.logger.error(
                f"Failed to load YAML file: {self.yaml_file_path}. Error: {e}"
            )
            return {}
        if data and not isinstance(data, dict):
            self.logger.error(
                f"Malformed template (expected a mapping): {self.yaml_file_path}"
            )
            return {}
        return data or {}

    def restore_graph(self):
        """Restore the graph based on the YAML configuration and textures."""
        if not self.graph_config:
            self.logger.warning("Graph configuration is empty. Nothing to restore.")
            return

        # ``log_group`` writes through ``log_raw``, which bypasses level
        # filtering BY DESIGN — so the report has to gate itself, or a caller
        # that passed a quiet logger gets the whole per-node dump anyway.
        report = self.logger.isEnabledFor(logging.INFO)

        file_nodes_found = any(
            info["type"] == "file" for info in self.graph_config.values()
        )
        if self.texture_paths and not file_nodes_found:
            self.logger.warning(
                "Texture paths provided but no file nodes found in template. The template might be incomplete or saved with an older version."
            )

        available_map_types = {}
        resolved_log = []
        # The paths are one set, read together: a set named for a lobe
        # (`Hero_Coat`) rebinds its roughness to the template's Roughness slot.
        readings = ptk.MapFactory.classify_textures(self.texture_paths)
        for path in self.texture_paths:
            map_type = readings[path][0]
            if map_type:
                available_map_types[map_type] = path
                resolved_log.append(f"{map_type:<24} {os.path.basename(path)}")
            else:
                # Unresolved maps stay individual records: they're the
                # actionable ones, and there are rarely more than a couple.
                self.logger.warning(
                    f"Could not resolve map type for '{os.path.basename(path)}'"
                )
        if report and resolved_log:
            self.log_group(f"Resolved {len(resolved_log)} texture(s)", resolved_log)

        # ONE processor for the whole restore: its conversion cache is
        # per-instance, so a derived map shared by several slots (e.g. an
        # ORM pack feeding metallic/roughness/AO) is computed once, not
        # once per file node.
        output_dir = ""
        base_name = "generated_map"
        ext = "png"
        if available_map_types:
            first_path = next(iter(available_map_types.values()))
            # abspath: a bare relative filename would yield dirname "" and
            # send generated maps to whatever the CWD happens to be.
            output_dir = os.path.dirname(os.path.abspath(first_path))
            base_name = readings[first_path][1]
            ext = os.path.splitext(first_path)[1].lstrip(".")

        context = TextureProcessor(
            inventory=available_map_types,
            config={},
            output_dir=output_dir,
            base_name=base_name,
            ext=ext,
            logger=self.logger,
            conversion_registry=self.registry,
        )

        self._node_log = []
        for placeholder, node_info in self.graph_config.items():
            self._restore_node(placeholder, node_info, context)
        if report and self._node_log:
            self.log_group(
                f"Texture slots filled ({len(self._node_log)}"
                f" of {len(self.nodes)} nodes)",
                self._node_log,
            )

        self.restore_connections()

    def _restore_node(self, placeholder, node_info, context: TextureProcessor):
        logger = self.logger
        node_type = node_info["type"]
        # Copy so texture-path fixups below never mutate the loaded config.
        attributes = dict(node_info.get("attributes", {}))
        metadata = node_info.get("metadata", {})
        required_map_type = metadata.get("map_type", "")

        file_path = None
        if required_map_type:
            fallbacks = MapFactory.get_map_fallbacks(required_map_type)
            candidates = [required_map_type] + list(fallbacks)
            file_path = context.resolve_map(*candidates, allow_conversion=True)

        # A conversion can return an in-memory PIL image; persist it beside
        # the source textures with the processor's own naming/optimization.
        if file_path and not isinstance(file_path, (str, bytes, os.PathLike)):
            try:
                file_path = context.save_map(file_path, required_map_type)
            except Exception as e:
                logger.error(f"Failed to save generated '{required_map_type}' map: {e}")
                file_path = None

        if file_path:
            attributes["fileTextureName"] = file_path
            self._node_log.append(
                f"{placeholder:<24} {required_map_type} → {os.path.basename(file_path)}"
            )
        elif required_map_type:
            # A map_type node is a texture slot meant to be filled from the
            # caller's textures. With nothing resolved, drop any path the
            # template still carries: for a slot it is stale, machine-specific
            # data (legacy templates baked absolute, project-relative paths
            # here) that would otherwise be applied and trigger a spurious
            # "texture doesn't exist" warning. Leaving it empty lets Maya show
            # its missing-texture placeholder instead.
            attributes.pop("fileTextureName", None)
            logger.warning(
                f"Node '{placeholder}': Missing texture for '{required_map_type}'"
            )

        node_name = self._determine_node_name(node_type)

        ftn = attributes.get("fileTextureName")

        node = NodeUtils.create_render_node(
            node_type,
            name=node_name,
            create_shading_group=metadata.get("connected_to_shading_engine", False),
            **attributes,
        )

        # StingrayPBS nodes need the Standard.sfx graph loaded before any
        # ``TEX_color_map`` / ``TEX_normal_map`` etc. attributes exist.
        # Without this, ``restore_connections`` can't wire textures into
        # the shader.  ``loadGraph`` *resets* all node attributes, so we
        # load the graph FIRST (after node creation, before applying the
        # snapshot ``attributes``) so the saved values aren't wiped.
        if node and node_type == "StingrayPBS":
            try:
                Plugins.load("shaderFXPlugin")
                from mayatk.mat_utils._mat_utils import MatUtils

                graph = MatUtils.resolve_stingray_graph("none")
                if graph:
                    cmds.shaderfx(sfxnode=str(node), loadGraph=graph)
                    # Re-apply the snapshot attributes that loadGraph wiped.
                    for k, v in (attributes or {}).items():
                        if k in ("fileTextureName",):
                            continue
                        plug = f"{node}.{k}"
                        if not cmds.attributeQuery(k, node=node, exists=True):
                            continue
                        try:
                            if isinstance(v, (list, tuple)) and len(v) == 3:
                                cmds.setAttr(plug, *v, type="double3")
                            elif isinstance(v, (list, tuple)) and len(v) == 16:
                                cmds.setAttr(plug, *v, type="matrix")
                            elif isinstance(v, str):
                                cmds.setAttr(plug, v, type="string")
                            else:
                                cmds.setAttr(plug, v)
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(
                    f"Failed to load Standard.sfx into StingrayPBS '{node}': {e}"
                )

        if node and ftn:
            try:
                cmds.setAttr(f"{node}.fileTextureName", ftn, type="string")
            except Exception as e:
                logger.warning(f"Failed to set fileTextureName on {node}: {e}")

        if node:
            self.nodes[placeholder] = node
        else:
            logger.error(f"Failed to create node: {placeholder}")

    def _determine_node_name(self, node_type):
        classification_string = cmds.getClassification(node_type)
        if any("shader/surface" in c for c in classification_string):
            if self.name:
                return self.name
            elif self.texture_paths:
                # Read against the set: its own name may end in a lobe word.
                return ptk.MapFactory.classify_textures(self.texture_paths)[
                    self.texture_paths[0]
                ][1]
        return None

    def restore_connections(self):
        """Connect nodes as specified in the graph configuration."""
        for placeholder, node_info in self.graph_config.items():
            node = self.nodes.get(placeholder)
            if not node:
                self.logger.error(f"Node for placeholder {placeholder} not found.")
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
                        cmds.connectAttr(
                            f"{src_node}.{src_attr}",
                            f"{tgt_node}.{tgt_attr}",
                            force=True,
                        )
                    else:
                        if not src_node:
                            self.logger.warning(
                                f"Source node {src_placeholder} not found for connection {src_str} -> {tgt_str}"
                            )
                        if not tgt_node:
                            self.logger.warning(
                                f"Target node {tgt_placeholder} not found for connection {src_str} -> {tgt_str}"
                            )
                except Exception as e:
                    self.logger.error(
                        f"Failed to connect {connection.get('source')} to {connection.get('target')}: {str(e)}"
                    )


class ShaderTemplates:
    """
    Facade class for managing shader templates.
    Provides high-level methods to save and restore shader graphs.

    Templates live in a ``ptk.PresetStore`` (:meth:`store`, as blendertk's
    do): the shipped ones in the package's read-only ``templates/`` folder,
    the user's in the per-user config, where an upgrade cannot remove them.
    """

    #: The shipped, read-only templates (the store's built-in tier).
    BUILTIN_DIR: str = os.path.join(os.path.dirname(__file__), "templates")

    #: The names mayatk ships in :attr:`BUILTIN_DIR`. Any other ``.yaml`` there
    #: was saved into the installed package by an older version, and
    #: :meth:`migrate_package_templates` moves it to the user tier -- so a new
    #: built-in must be added here (a test holds the folder to this list).
    SHIPPED_TEMPLATES = (
        "Godot",
        "PBR Metallic Roughness",
        "Unity HDRP Lit",
        "Unity URP Lit",
        "Unreal Engine ORM",
        "glTF 2.0",
    )

    #: Templates are YAML (``GraphSaver.save_graph``'s format).
    YAML_CODEC = ptk.Codec(
        ext=".yaml",
        load=yaml.safe_load,
        dump=lambda data: yaml.dump(data, default_flow_style=False),
    )

    #: The node types Save Template leaves out of a captured network: the scene
    #: around the material (its shading group, the objects it is on), not the
    #: material. blendertk's twin excludes none (a material's tree holds shader
    #: nodes only).
    EXCLUDE_TYPES: tuple = (
        "shadingEngine",
        "transform",
        "mesh",
        "nurbsCurve",
        "camera",
        "light",
    )

    #: Plug-ins whose node types a template may use (ShaderFX's StingrayPBS,
    #: Arnold's ``ai*`` shaders). :meth:`restore_template` loads them only when a
    #: template names a type Maya does not know yet: mtoa boots the whole Arnold
    #: renderer.
    SHADER_PLUGINS: tuple = ("shaderFXPlugin", "mtoa")

    @classmethod
    def store(cls) -> "ptk.PresetStore":
        """The template store: shipped templates read-only, the user's in
        ``<user config>/mayatk/shader_templates/``. A template an older
        version saved into the package moves there first
        (:meth:`migrate_package_templates`)."""
        store = ptk.PresetStore(
            "shader_templates",
            package="mayatk",
            builtin_dir=cls.BUILTIN_DIR,
            codec=cls.YAML_CODEC,
        )
        cls.migrate_package_templates(store)
        return store

    @classmethod
    def migrate_package_templates(cls, store: "ptk.PresetStore") -> List[str]:
        """Move user templates out of *store*'s built-in folder into its user
        tier: every ``.yaml`` not in :attr:`SHIPPED_TEMPLATES` (one an older
        version saved into the installed package). A name the user tier holds
        already is left where it is. Copies instead when the package folder
        is read-only (the install dir of a system-wide Maya).

        Returns:
            The names moved (or copied).
        """
        import shutil

        package = store.builtin_dir
        if not package or not os.path.isdir(package):
            return []
        moved = []
        for name in sorted(store.list(tier="builtin")):
            if name in cls.SHIPPED_TEMPLATES or store.source(name) == "user":
                continue
            src = str(store.path(name, "builtin"))
            dst = str(store.path(name, "user"))
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                try:
                    shutil.move(src, dst)
                except PermissionError:
                    shutil.copy2(src, dst)
            except OSError as e:
                _LOGGER.warning(f"Could not move template {src!r} to {dst!r}: {e}")
                continue
            moved.append(name)
        if moved:
            _LOGGER.info(
                f"Moved {len(moved)} saved template(s) to {store.user_dir}: "
                + ", ".join(moved)
            )
        return moved

    @staticmethod
    def suggested_name(nodes) -> str:
        """The name Save Template offers for *nodes*: the first node's leaf name,
        its namespace colons made underscores (a template name is a file name);
        ``""`` for none. Twin of blendertk's, which offers the captured material's.
        """
        nodes = [nodes] if isinstance(nodes, str) else list(nodes or [])
        return CoreUtils.leaf_name(nodes[0]).replace(":", "_") if nodes else ""

    @staticmethod
    def save_template(nodes, file_path, exclude_types=None, logger=None):
        """
        Save the specified nodes as a shader template.

        Args:
            nodes (list): List of Maya nodes to save.
            file_path (str): Path to the output YAML file.
            exclude_types (list, optional): List of node types to exclude
                (:attr:`EXCLUDE_TYPES`: what the panel leaves out).
            logger (logging.Logger, optional): Destination for progress and
                error messages (e.g. a panel-redirected logger).
        """
        saver = GraphSaver(logger=logger)
        saver.save_graph(nodes, file_path, exclude_types=exclude_types)

    @classmethod
    def restore_template(cls, file_path, texture_paths=None, name=None, logger=None):
        """
        Restore a shader template from a file.

        Loads the plug-ins its node types need first (:attr:`SHADER_PLUGINS`, only
        for a type Maya lacks, never fatally), then rebuilds the network in ONE
        undo chunk -- a partial failure must not leave half-built nodes needing N
        undos -- and announces the result on *logger*: the node count and the
        surface shader, a link that selects it where the logger renders links
        (as ``GraphSaver.save_graph`` announces a save).

        Args:
            file_path (str): Path to the YAML template file.
            texture_paths (list, optional): List of texture paths to use.
            name (str, optional): Name for the restored shader.
            logger (logging.Logger, optional): Destination for progress and
                error messages (e.g. a panel-redirected logger).

        Returns:
            dict: Mapping of placeholder names to created Maya nodes.
        """
        restorer = GraphRestorer(file_path, texture_paths or [], name, logger=logger)
        # Outside the undo chunk: a plug-in load is no undoable edit.
        cls._load_plugins_for(restorer.graph_config, restorer.logger)
        cls._restore(restorer)
        cls._announce_restore(restorer.nodes, restorer.logger)
        return restorer.nodes

    @staticmethod
    @CoreUtils.undoable
    def _restore(restorer: "GraphRestorer") -> None:
        """Rebuild *restorer*'s network as one undo chunk."""
        restorer.restore_graph()

    @classmethod
    def _load_plugins_for(cls, graph_config: dict, logger) -> None:
        """Load :attr:`SHADER_PLUGINS` when *graph_config* names a node type Maya
        does not know yet. A plug-in that fails to load is logged, never raised: a
        template that doesn't use its types still restores, and one that does
        reports the nodes it cannot make."""
        wanted = {
            info.get("type")
            for info in (graph_config or {}).values()
            if isinstance(info, dict) and info.get("type")
        }
        if not wanted or wanted <= set(cmds.allNodeTypes() or []):
            return
        for plugin in cls.SHADER_PLUGINS:
            try:
                Plugins.load(plugin)
            except Plugins.LoadError as e:
                logger.debug(str(e))

    @staticmethod
    def _announce_restore(nodes: dict, logger) -> None:
        """The ONE announcement of a restore: the node count and the surface
        shader, as a link that selects it on a logger that renders links. A
        quiet logger (below INFO) gets nothing."""
        if not logger.isEnabledFor(logging.INFO):
            return
        shader = None
        for node in nodes.values():
            # Never let the summary raise: nodeType() throws on a name the
            # restore renamed or failed to create.
            try:
                if node and cmds.objExists(node):
                    classes = cmds.getClassification(cmds.nodeType(node)) or []
                    if any("shader/surface" in c for c in classes):
                        shader = node
                        break
            except Exception:  # noqa: BLE001 - a cosmetic lookup only
                continue
        lines = [f"Nodes created : {len(nodes)}"]
        if shader:
            label = CoreUtils.short_name(shader)
            if hasattr(logger, "log_link"):
                label = logger.log_link(label, "select", node=str(shader))
            lines.append(f"Shader        : {label}")
        if hasattr(logger, "log_box"):
            logger.log_box("NETWORK CREATED", lines, level="SUCCESS")
        else:
            logger.info(
                "Network created: "
                + "; ".join(" ".join(line.split()) for line in lines)
            )


# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
