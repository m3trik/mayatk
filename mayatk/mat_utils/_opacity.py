# !/usr/bin/python
# coding=utf-8
"""Opacity behind :class:`mayatk.MatUtils`.

The StingrayPBS opacity graphs -- opaque, masked and transparent, mayatk's own
``shaderfx/`` presets first -- resolved, identified and loaded by opacity
mode; and wiring a material's opacity map (in its network, or beside its other
textures on disk) into the slot its shader type reads, so it shows in the
viewport, plus the Viewport 2.0 transparency mode.
Reached through :class:`mayatk.MatUtils`; nothing here is called directly.
"""

import os
from typing import List, Dict, Optional

try:
    import maya.cmds as cmds
except Exception:
    cmds = None

import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.env_utils._env_utils import EnvUtils


class _OpacityInternal:
    """Private helpers and ``MatUtils`` method bodies; see the module docstring."""

    @classmethod
    def _find_opacity_map_on_disk(
        cls, mat: str, dir_cache: Optional[Dict[str, Dict[str, str]]] = None
    ) -> Optional[str]:
        """Path to an Opacity map sitting beside the material's other textures.

        Covers the common case where the set ships an opacity map that never
        made it into the shading network — the material was built before the
        map existed, or from a shader graph with no opacity slot.

        Parameters:
            mat (str): Material node.
            dir_cache (dict): Scan cache, ``{directory: {base name: path}}``.
                Scene-wide callers should pass one in — every material in a set
                shares a texture folder, and the scan is a full directory walk.
        """
        bases_by_dir: Dict[str, set] = {}
        for file_node in cmds.ls(cmds.listHistory(mat) or [], type="file") or []:
            raw = cmds.getAttr(f"{file_node}.fileTextureName") or ""
            # search=False: we want the folder Maya actually reads this texture
            # from — the basename hunt could point us at an unrelated same-named
            # file in some other set's folder.
            path = cls.resolve_path(raw, search=False) or raw
            directory = os.path.dirname(path)
            if not directory or not os.path.isdir(directory):
                continue
            base = ptk.MapFactory.get_base_texture_name(path)
            if base:
                bases_by_dir.setdefault(directory, set()).add(base)

        cache = {} if dir_cache is None else dir_cache
        for directory, bases in bases_by_dir.items():
            if directory not in cache:
                # base name → the set's Opacity map, one scan per folder.
                found: Dict[str, str] = {}
                for entry in sorted(os.listdir(directory)):
                    full = os.path.join(directory, entry)
                    if not os.path.isfile(full):
                        continue
                    if ptk.MapFactory.resolve_map_type(full) != "Opacity":
                        continue
                    base = ptk.MapFactory.get_base_texture_name(full)
                    if base:
                        found.setdefault(base, full)
                cache[directory] = found

            for base in bases:
                if base in cache[directory]:
                    return cache[directory][base]
        return None

    @staticmethod
    def _slot_inputs(node: str, attr: str) -> List[str]:
        """Source plugs driving `attr` or any of its channel children."""
        found: List[str] = []
        for plug in [attr] + [f"{attr}{s}" for s in ("R", "G", "B", "X", "Y", "Z")]:
            if not cmds.attributeQuery(plug, node=node, exists=True):
                continue
            found += (
                cmds.listConnections(
                    f"{node}.{plug}", source=True, destination=False, plugs=True
                )
                or []
            )
        return found

    @classmethod
    def _find_opacity_source(cls, mat):
        """Body of :meth:`MatUtils.find_opacity_source`."""
        file_nodes = cmds.ls(cmds.listHistory(mat) or [], type="file") or []
        by_type: Dict[str, str] = {}
        for file_node in file_nodes:
            path = cmds.getAttr(f"{file_node}.fileTextureName") or ""
            map_type = ptk.MapFactory.resolve_map_type(path) if path else None
            if map_type in cls.OPACITY_MAP_TYPES:
                by_type.setdefault(map_type, file_node)

        for map_type in cls.OPACITY_MAP_TYPES:
            if map_type in by_type:
                return by_type[map_type]
        return None

    @classmethod
    def _enable_viewport_opacity(cls, materials, transparency_algorithm, search_disk):
        """Body of :meth:`MatUtils.enable_viewport_opacity`."""
        from mayatk.mat_utils.mat_snapshot import MatSnapshot

        results: Dict[str, str] = {}

        # Materials in a set share a texture folder; scan each folder once.
        dir_cache: Dict[str, Dict[str, str]] = {}

        for mat in cls.get_mats(materials):
            mat = str(mat)
            name = CoreUtils.short_name(mat)
            source = cls.find_opacity_source(mat)
            if not source and search_disk:
                path = cls._find_opacity_map_on_disk(mat, dir_cache)
                if path:
                    source = NodeUtils.create_render_node(
                        "file",
                        fileTextureName=path,
                        name=ptk.format_path(path, section="name"),
                    )
            if not source:
                results[mat] = "no opacity map"
                continue

            node_type = cmds.nodeType(mat)
            attr, sense = cls.OPACITY_INPUTS.get(
                node_type, ("transparency", "transparency")
            )

            # StingrayPBS: the opacity slots live on the transparent graph only,
            # and loading it wipes the network — snapshot, swap, restore.
            if node_type == "StingrayPBS" and not cmds.attributeQuery(
                attr, node=mat, exists=True
            ):
                with MatSnapshot.restored(name):
                    loaded = cls.ensure_transparent_graph(mat)
                if not loaded:
                    results[mat] = "unsupported: no transparent graph available"
                    continue
                source = cls.find_opacity_source(mat) or source

            if not cmds.attributeQuery(attr, node=mat, exists=True):
                results[mat] = f"unsupported: {node_type} has no '{attr}' slot"
                continue

            # Already driven by this very map (possibly through a reverse) —
            # leave it be, so a re-run can't stack duplicate conversion nodes.
            if any(
                plug.split(".")[0] == source
                or source in (cmds.listHistory(plug.split(".")[0]) or [])
                for plug in cls._slot_inputs(mat, attr)
            ):
                results[mat] = "already enabled"
                continue

            # Where the mask lives decides how the file node must read alpha: a
            # dedicated Opacity map is grayscale (luminance yields a usable mask
            # even with no alpha channel), while a packed Albedo_Transparency
            # map carries the real thing — reading luminance there would drive
            # opacity from the albedo's brightness.
            if cmds.attributeQuery("alphaIsLuminance", node=source, exists=True):
                path = cmds.getAttr(f"{source}.fileTextureName") or ""
                map_type = ptk.MapFactory.resolve_map_type(path)
                if map_type in cls.OPACITY_MAP_TYPES:
                    cmds.setAttr(
                        f"{source}.alphaIsLuminance", int(map_type == "Opacity")
                    )

            plug = f"{source}.outAlpha"
            if sense == "transparency":  # classic shaders: 1 - alpha
                reverse = cmds.shadingNode(
                    "reverse", asUtility=True, name=f"{name}_invertOpacity"
                )
                cmds.connectAttr(plug, f"{reverse}.inputX", force=True)
                plug = f"{reverse}.outputX"

            if not cls.connect_to_channels(plug, mat, attr):
                results[mat] = f"unsupported: could not drive '{attr}'"
                continue

            # StingrayPBS gates the opacity input behind its own toggle.
            if cmds.attributeQuery("use_opacity_map", node=mat, exists=True):
                use_plug = f"{mat}.use_opacity_map"
                if not cmds.getAttr(use_plug, lock=True):
                    cmds.setAttr(use_plug, 1)

            results[mat] = "enabled"

        if transparency_algorithm:
            cls.set_transparency_algorithm(transparency_algorithm)

        return results

    @classmethod
    def _set_transparency_algorithm(cls, algorithm):
        """Body of :meth:`MatUtils.set_transparency_algorithm`."""
        key = str(algorithm).strip().lower().replace(" ", "_")
        if key not in cls.TRANSPARENCY_ALGORITHMS:
            return False
        cmds.setAttr(
            "hardwareRenderingGlobals.transparencyAlgorithm",
            cls.TRANSPARENCY_ALGORITHMS.index(key),
        )
        return True

    @classmethod
    def _ensure_transparent_graph(cls, mat):
        """Body of :meth:`MatUtils.ensure_transparent_graph`."""
        # The scalar `opacity` slot is the test -- `use_opacity_map` is NOT:
        # the masked graph exposes that toggle too, and keying on it left a
        # masked material claiming a slot it does not have.
        if cls.get_stingray_opacity_mode(mat) == "transparent":
            return True
        return cls.load_stingray_graph(mat, "transparent")

    # Read-compat: manifests written before the graphs were renamed still
    # name them this way, so the old spellings keep resolving.
    _STINGRAY_GRAPH_ALIASES = {
        "transparent_graph": "transparent",
        "lightweight": "transparent",
    }

    @classmethod
    def _resolve_opacity_mode(cls, opacity_mode, opacity):
        """Body of :meth:`MatUtils.resolve_opacity_mode`."""
        if opacity_mode is None:
            opacity_mode = "transparent" if opacity else "none"
        opacity_mode = cls._STINGRAY_GRAPH_ALIASES.get(opacity_mode, opacity_mode)
        return opacity_mode if opacity_mode in cls.STINGRAY_GRAPHS else "none"

    @classmethod
    def _get_stingray_opacity_mode(cls, mat):
        """Body of :meth:`MatUtils.get_stingray_opacity_mode`."""
        mat = str(mat)
        # Type-gated: `opacity` is also a standardSurface attribute, and the
        # probe is only meaningful for a node whose slots come from a graph.
        if cmds.nodeType(mat) != "StingrayPBS":
            return None
        for attr, mode in (
            ("opacity", "transparent"),
            ("TEX_mask_map", "masked"),
            ("TEX_color_map", "none"),
        ):
            if cmds.attributeQuery(attr, node=mat, exists=True):
                return mode
        return None

    @classmethod
    def _resolve_stingray_graph(cls, opacity_mode, opacity):
        """Body of :meth:`MatUtils.resolve_stingray_graph`."""
        name = cls.STINGRAY_GRAPHS[cls.resolve_opacity_mode(opacity_mode, opacity)]
        for graph in (
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "shaderfx", name),
            os.path.join(
                EnvUtils.get_env_info("install_path"),
                "presets",
                "ShaderFX",
                "Scenes",
                "StingrayPBS",
                name,
            ),
        ):
            if os.path.exists(graph):
                return graph
        return None

    @classmethod
    def _load_stingray_graph(cls, mat, opacity_mode, opacity):
        """Body of :meth:`MatUtils.load_stingray_graph`."""
        graph = cls.resolve_stingray_graph(opacity_mode, opacity)
        if not graph:
            return False
        EnvUtils.load_plugin("shaderFXPlugin")
        cmds.shaderfx(sfxnode=CoreUtils.short_name(mat), loadGraph=graph)
        return True

    @classmethod
    def _create_stingray_shader(cls, name, opacity, opacity_mode):
        """Body of :meth:`MatUtils.create_stingray_shader`."""
        EnvUtils.load_plugin("shaderFXPlugin")
        shader = NodeUtils.create_render_node(
            "StingrayPBS", name=name, create_shading_group=False
        )
        cls.load_stingray_graph(shader, opacity_mode, opacity)
        return shader
