# !/usr/bin/python
# coding=utf-8
"""``MatUtils`` -- the public index of mayatk's material and texture operations.

Every public method keeps its signature and docstring here and delegates its
body to the concept module that owns the job (the wildcard ``DEFAULT_INCLUDE``
entry registers the flat ``mtk.<method>`` names from this class body, so the
methods themselves never move):

- ``_material_query`` -- which materials a scene, selection or scope holds:
  shading-engine lookups, classification, the file-node inventory and the
  material / texture info report.
- ``_assignment`` -- which geometry carries which material: assigning,
  snapshotting and restoring per-face membership, finding objects by material.
- ``_shading_network`` -- building and reading shading graphs: materials, file
  nodes, shading groups, channel wiring, name reclaiming, bump-to-normal.
- ``_opacity`` -- the StingrayPBS opacity graphs and wiring an opacity map so
  it shows in the viewport.
- ``_duplicates`` -- materials that are verified duplicates, and merging them.
- ``_texture_paths`` -- what a stored ``fileTextureName`` denotes: env /
  workspace / tile-token resolution (``ptk.TiledPath``), UV tiling, and the
  absolute and project-relative forms.
- ``_texture_files`` -- texture files on disk: finding, staging, copying,
  moving, remapping and reloading them, and telling two apart by content.
"""

from typing import List, Tuple, Union, Dict, Any, Optional, Callable

import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils

# The concept bases of the helper class below: needed at class-definition
# time, so imported eagerly (they import nothing from this module).
from mayatk.mat_utils._material_query import _MaterialQueryInternal
from mayatk.mat_utils._assignment import _AssignmentInternal
from mayatk.mat_utils._shading_network import _ShadingNetworkInternal
from mayatk.mat_utils._opacity import _OpacityInternal
from mayatk.mat_utils._duplicates import _DuplicatesInternal
from mayatk.mat_utils._texture_paths import _TexturePathsInternal
from mayatk.mat_utils._texture_files import _TextureFilesInternal


# Why the StingrayPBS helpers' ``opacity`` bool warns: it restated what
# ``opacity_mode`` says (True was "transparent"), so callers pass the mode alone.
_OPACITY_IS_A_MODE = (
    "Pass opacity_mode='transparent' (or 'masked' / 'none') instead; "
    "opacity=True meant 'transparent'."
)


class _MatUtilsInternal(
    _MaterialQueryInternal,
    _AssignmentInternal,
    _ShadingNetworkInternal,
    _OpacityInternal,
    _DuplicatesInternal,
    _TexturePathsInternal,
    _TextureFilesInternal,
    ptk.HelpMixin,
):
    """The helper base of :class:`MatUtils`, composed from its concept modules.

    Each ``_<concept>.py`` sibling holds one job's private helpers and the
    bodies of its public methods (``MatUtils.<name>`` keeps the signature and
    docstring and returns ``cls._<name>(...)``). Composing them here keeps every
    helper reachable as ``MatUtils._<helper>``, the spelling ``mat_manifest``,
    ``image_to_plane``, ``edit_utils`` and the tests use.
    """


class MatUtils(_MatUtilsInternal):
    @staticmethod
    def resolve_path(path: str, search: bool = True) -> Union[str, None]:
        """Resolve a texture path, expanding env vars and tile/frame tokens.

        The returned value keeps its TOKEN — callers that need a concrete file
        collapse it themselves (``TaskManager._tiled_representative``,
        :meth:`probe_texture_path`); only the existence verdict behind this
        resolution is token-aware, via :meth:`_texture_exists`.

        Parameters:
            path: The stored ``fileTextureName`` value.
            search: When True (default) fall back to *hunting* for the texture
                under the project's ``sourceimages`` — by relative path, then by
                bare basename. That is what makes this a repair primitive
                (``resolve_invalid_texture_paths`` writes the result back).

                Pass ``search=False`` to answer the narrower question "does this
                path resolve the way **Maya** will resolve it" — env-var
                expansion plus the project ROOT and then the ``sourceImages``
                file rule, which is :meth:`to_absolute`'s order. Validity
                checks must use this: the basename hunt happily matches a
                same-named file the node does not point at, so a genuinely
                broken link would read as valid and ship broken.

        Returns:
            str|None: The resolved path, or None when it does not resolve.
        """
        return MatUtils._resolve_path(path=path, search=search)

    @staticmethod
    @ptk.Deprecation.parameter(
        "as_strings", drop=True, remove_in="0.20.0", since="2026-09-23"
    )
    def get_mats(
        objs=None,
        as_strings=True,
        mat_type=None,
        include_displacement=False,
    ) -> List[str]:
        """Returns the materials assigned to a given list of objects or components.

        Parameters:
            objs (list): The objects or components to retrieve the material from.
                If None, the current selection is used.
            as_strings (bool): DEPRECATED and ignored (warns; removed in
                0.20.0) -- the result is always strings.
            mat_type (str, optional): Maya node type to filter by
                (e.g. ``"StingrayPBS"``, ``"lambert"``, ``"aiStandardSurface"``).
                If None, all material types are returned.
            include_displacement (bool): Also follow each shading engine's
                ``displacementShader`` / ``volumeShader`` / ``aiSurfaceShader``
                connections.  Default False keeps the historical
                surface-shader-only contract; the scene exporter opts in so
                displacement/volume textures are validated and staged like
                every other map.

        Returns:
            list[str]: Materials assigned to the objects or components, duplicates
                removed, in the order the objects reach them (stable run to run).
        """
        return MatUtils._get_mats(
            objs=objs,
            as_strings=as_strings,
            mat_type=mat_type,
            include_displacement=include_displacement,
        )

    @staticmethod
    def group_objects_by_material(
        objects, cluster_by_distance=False, threshold=10000.0
    ):
        """Groups objects based on their assigned material(s)."""
        return MatUtils._group_objects_by_material(
            objects=objects,
            cluster_by_distance=cluster_by_distance,
            threshold=threshold,
        )

    @staticmethod
    def is_bundled_texture(path: str) -> bool:
        """Does *path* live inside Maya's own installation?

        Those are the images Autodesk ships — StingrayPBS' ``diffuse_cube.dds`` /
        ``specular_cube.dds`` environment maps and the rest of
        ``presets/ShaderFX/Images`` — wired onto real file nodes, so every
        material-scoped query returns them alongside the user's maps. They are
        not project assets: the install tree is read-only, so a tool that writes
        (optimize, repath, repack) can only fail on them.

        Path-only and side-effect free, so callers can filter a list without
        touching the scene. False when ``MAYA_LOCATION`` is unset.
        """
        return MatUtils._is_bundled_texture(path=path)

    @classmethod
    def get_texture_paths(
        cls,
        objects: Optional[List[Any]] = None,
        materials: Optional[List[Any]] = None,
        file_nodes: Optional[List[Any]] = None,
        texture_names: Optional[List[str]] = None,
        absolute: bool = True,
        exclude_bundled: bool = False,
    ) -> List[str]:
        """Resolve unique texture file paths for the given scope.

        Lightweight counterpart to :meth:`get_texture_info` — reads only the
        ``fileTextureName`` attribute from each resolved ``file`` node, so it
        is safe to call from interactive UI providers on selections with many
        high-resolution textures (no PIL decoding).

        Parameters:
            objects: Scene objects (transforms / shapes / face components).
                Materials are resolved from their assigned shading engines.
            materials: Materials to scope by directly.
            file_nodes: Pre-resolved ``file`` nodes to read paths from.
            texture_names: Extra raw texture paths to include verbatim.
            absolute: If True (default), paths are made absolute against the
                project ``sourceimages`` directory; if False, relative when
                the texture lives under ``sourceimages``.
            exclude_bundled: Drop textures shipped with Maya itself (see
                :meth:`is_bundled_texture`). Off by default — a query that
                inventories the scene wants every wired map. Tools that
                *write* should turn it on: the install tree is read-only, so
                a StingrayPBS material's preset cube maps can only fail them.

        Returns:
            list[str]: Unique non-empty paths in resolution order.
        """
        return cls._get_texture_paths(
            objects=objects,
            materials=materials,
            file_nodes=file_nodes,
            texture_names=texture_names,
            absolute=absolute,
            exclude_bundled=exclude_bundled,
        )

    @classmethod
    def get_texture_info(
        cls,
        objects=None,
        materials=None,
        file_nodes=None,
        texture_names=None,
    ):
        """Get image metadata (size, mode, format) for texture files in scope.

        Heavy: opens every texture with PIL. For path-only callers, use
        :meth:`get_texture_paths` instead.
        """
        return cls._get_texture_info(
            objects=objects,
            materials=materials,
            file_nodes=file_nodes,
            texture_names=texture_names,
        )

    @classmethod
    def get_mat_info(
        cls,
        materials: Optional[List[Any]] = None,
        objects: Optional[List[Any]] = None,
        optimize_check: bool = False,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        exclude_defaults: bool = False,
        exclude_unassigned: bool = False,
        include_textures: bool = True,
        include_image_metadata: bool = True,
        **optimize_kwargs,
    ) -> List[Dict[str, Any]]:
        """Aggregate per-material info: name, type, textures + image metadata.

        Each entry bundles the material's identity with one record per file
        node it drives. When ``optimize_check`` is True, each texture record
        also gets an ``optimization`` block from
        :meth:`ptk.MapOptimizer.assess` — useful for spotting oversized /
        wrong-mode textures from a UI report.

        Parameters:
            materials: Materials to scope by. None falls back to the entire
                scene unless ``objects`` is supplied.
            objects: Scene nodes whose assigned materials should be scoped.
            optimize_check: If True, run optimization analysis per texture.
                Opens each texture once and reuses the loaded PIL image for
                both the metadata and the assessment.
            exclude_defaults: Drop Maya's built-in default materials
                (``lambert1``, ``standardSurface1``, etc.) from the result.
            exclude_unassigned: Drop materials whose shading engines have
                no DAG members (see :meth:`is_mat_assigned`).
            include_textures: If False, omit the per-file-node texture work
                entirely and emit each material with ``textures: []``.
            include_image_metadata: If False, omit width/height/mode/format/
                bit_depth from texture records. PIL is only opened when this
                or ``optimize_check`` requires it.
            **optimize_kwargs: Forwarded to
                ``ptk.MapOptimizer.assess`` (``max_size``, ``force_pot``,
                ``optimize_bit_depth``, ``map_type``, ``allow_palette``).

        Returns:
            list[dict]: Per material:
                {
                    "material": str,        # material node name
                    "type": str,            # cmds.nodeType
                    "textures": [           # one entry per file node
                        {
                            "file_node": str,
                            "path": str,
                            "name": str,
                            "size": int,    # bytes
                            "width": int,
                            "height": int,
                            "mode": str,
                            "format": str,
                            "bit_depth": str,   # e.g. "32bit (8x4)"
                            "optimization": {...}  # only when optimize_check
                        },
                        ...
                    ],
                }
        """
        return cls._get_mat_info(
            materials=materials,
            objects=objects,
            optimize_check=optimize_check,
            progress_callback=progress_callback,
            exclude_defaults=exclude_defaults,
            exclude_unassigned=exclude_unassigned,
            include_textures=include_textures,
            include_image_metadata=include_image_metadata,
            **optimize_kwargs,
        )

    # ---- Formatters ---------------------------------------------------

    # The pure record→text/HTML formatting lives in ``pythontk.MatReport`` (DCC-agnostic SSoT,
    # shared with blendertk); these classmethods stay for back-compat and delegate to it.
    @classmethod
    def format_texture_info_text(cls, info_list: List[Dict[str, Any]]) -> str:
        """Render :meth:`get_texture_info` output as a plain-text report (``pythontk.MatReport``)."""
        return cls._format_texture_info_text(info_list=info_list)

    @classmethod
    def format_texture_info_html(cls, info_list: List[Dict[str, Any]]) -> str:
        """Render :meth:`get_texture_info` output as styled HTML (``pythontk.MatReport``)."""
        return cls._format_texture_info_html(info_list=info_list)

    @classmethod
    def format_mat_info_text(cls, records: List[Dict[str, Any]]) -> str:
        """Render :meth:`get_mat_info` output as a plain-text report (``pythontk.MatReport``)."""
        return cls._format_mat_info_text(records=records)

    @classmethod
    def format_mat_info_html(cls, records: List[Dict[str, Any]]) -> str:
        """Render :meth:`get_mat_info` output as styled HTML (``pythontk.MatReport``)."""
        return cls._format_mat_info_html(records=records)

    @staticmethod
    def get_scene_mats(
        inc=None,
        exc=None,
        node_type=None,
        sort: bool = False,
        as_dict: bool = False,
        exclude_defaults: bool = True,
        exclude_utility_nodes: bool = True,
        exc_classification=None,
        **filter_kwargs,
    ):
        """Retrieves all materials from the current scene, with flexible name/type filtering.

        The source is ``cmds.ls(materials=True)`` UNIONED with the shaders wired
        into the scene's shading engines (:meth:`_shading_engine_shaders`), because
        Maya's query only reports ``defaultShaderList1`` — a shader built with
        ``createNode``, or wired up directly by an importer/plugin, is assigned to
        geometry yet absent from it.

        Parameters:
            inc/exc (str/list): Name patterns to keep / drop (shell wildcards,
                matched against the short name).
            node_type (str/list): Keep only these node types.
            sort (bool): Sort by short name.
            as_dict (bool): Return ``{display_name: material}`` instead of a list.
                The key is the short name, except where several materials share
                one (across namespaces) — then every member of that group is keyed
                on its namespace-qualified name, so none is dropped.
            exclude_defaults (bool): Drop Maya's built-in defaults (``lambert1``,
                ``particleCloud1``, ``shaderGlow1``, ``standardSurface1``, plus
                anything reported by ``cmds.ls(defaultNodes=True)``). Default True.
            exclude_utility_nodes (bool): Drop nodes that Maya reports as materials
                only because something registered them with ``shadingNode -asShader``
                while their classification claims a ``utility/`` / ``texture/`` /
                ``math/`` role — e.g. an ``aiMultiply`` or ``bump2d`` helper from
                inside a shading network.
                Default True; pass False for Maya's raw ``ls(materials=True)`` view.
            exc_classification (str/list): Drop materials whose classification
                matches these patterns (shell wildcards, matched per classification
                token). ``"rendernode/arnold*"`` hides Arnold shaders,
                ``"rendernode/redshift*"`` Redshift's, and so on.
            filter_kwargs: Forwarded to ``ptk.filter_list`` alongside inc/exc
                (``ignore_case``, ``match_all``, ``negate_prefix``, ...).
        """
        return MatUtils._get_scene_mats(
            inc=inc,
            exc=exc,
            node_type=node_type,
            sort=sort,
            as_dict=as_dict,
            exclude_defaults=exclude_defaults,
            exclude_utility_nodes=exclude_utility_nodes,
            exc_classification=exc_classification,
            **filter_kwargs,
        )

    @classmethod
    def get_connected_shaders(cls, file_nodes) -> List[str]:
        """Return surface shaders connected to one or more file nodes, ignoring intermediates."""
        return cls._get_connected_shaders(file_nodes=file_nodes)

    @staticmethod
    def connect_to_channels(source_plug: str, node: str, attr: str) -> bool:
        """Connect a single-channel `source_plug` into a (possibly compound) slot.

        Color/vector slots (``TEX_ao_map``, ``opacity``, ``transparency``, …)
        must be driven per channel when a scalar source feeds them. Any existing
        input on the parent is broken first, so the parent and its children can
        never end up driven by two different textures.

        Parameters:
            source_plug (str): Source plug, e.g. ``"file1.outAlpha"``.
            node (str): Target node.
            attr (str): Target attribute (parent name).

        Returns:
            bool: True if the connection was made.
        """
        return MatUtils._connect_to_channels(
            source_plug=source_plug, node=node, attr=attr
        )

    @classmethod
    def get_mats_by_scope(
        cls, scope: str = "selected", mat_type: Optional[str] = None
    ) -> List[str]:
        """Materials in the given scope.

        The scope primitive behind material tools that offer a Selected /
        Visible / Scene choice, so each one resolves the same way.

        Parameters:
            scope (str): ``"selected"`` — materials on the current selection.
                ``"visible"`` — materials on visible geometry.
                ``"scene"`` — every scene material, assigned or not.
            mat_type (str, optional): Maya node type filter (e.g.
                ``"StingrayPBS"``).

        Returns:
            list[str]: Material names (duplicates removed).
        """
        return cls._get_mats_by_scope(scope=scope, mat_type=mat_type)

    # Shader type → (slot that drives cutout/blend in the viewport, sense).
    # ``"opacity"`` slots take the alpha straight; anything not listed here
    # (lambert, blinn, phong, …) drives ``transparency``, which is inverted.
    OPACITY_INPUTS = {
        "StingrayPBS": ("opacity", "opacity"),
        "standardSurface": ("opacity", "opacity"),
        "openPBRSurface": ("geometryOpacity", "opacity"),
        "aiStandardSurface": ("opacity", "opacity"),
        "usdPreviewSurface": ("opacity", "opacity"),
    }

    # Map types accepted as an opacity source, best first.
    OPACITY_MAP_TYPES = ("Opacity", "Albedo_Transparency")

    @classmethod
    def find_opacity_source(cls, mat: str) -> Optional[str]:
        """The file node in `mat`'s network that carries its opacity.

        Recognizes a dedicated Opacity map, or the packed alpha of an
        Albedo_Transparency map. Returns None when the material has neither —
        the test for "is this an opacity material".

        Parameters:
            mat (str): Material node.

        Returns:
            str | None: The file node, or None.
        """
        return cls._find_opacity_source(mat=mat)

    @classmethod
    @CoreUtils.undoable
    def enable_viewport_opacity(
        cls,
        materials=None,
        transparency_algorithm: Optional[str] = None,
        search_disk: bool = True,
    ) -> Dict[str, str]:
        """Wire every opacity map in `materials` so it shows in the viewport.

        A texture set can carry an opacity map that never reaches the shader —
        the material was built before the map existed, or from a shader graph
        with no opacity slot. This finds each material's opacity map and drives
        the right slot for its shader type: alpha into ``opacity`` for the PBR
        shaders, inverted into ``transparency`` for the classic ones. StingrayPBS
        materials are switched to the transparent ShaderFX graph first (their
        opacity slots don't otherwise exist), preserving existing textures.

        Parameters:
            materials: Materials **or** objects (objects are resolved to their
                materials). If None, the current selection is used.
            transparency_algorithm (str, optional): Viewport 2.0 transparency
                mode to apply — ``"simple"``, ``"object_sorting"``,
                ``"weighted_average"`` or ``"depth_peeling"``. Left alone when
                None. Depth peeling sorts overlapping transparent faces
                correctly (decals, foliage) at some viewport cost.
            search_disk (bool): When the network holds no opacity map, look for
                one beside the material's other textures and import it.

        Returns:
            dict: ``{material: status}`` where status is ``"enabled"``,
            ``"already enabled"``, ``"no opacity map"`` or ``"unsupported: …"``.
        """
        return cls._enable_viewport_opacity(
            materials=materials,
            transparency_algorithm=transparency_algorithm,
            search_disk=search_disk,
        )

    # Viewport 2.0 transparency modes, in ``transparencyAlgorithm`` order.
    TRANSPARENCY_ALGORITHMS = (
        "simple",
        "object_sorting",
        "weighted_average",
        "depth_peeling",
    )

    @classmethod
    def set_transparency_algorithm(cls, algorithm: str) -> bool:
        """Set the Viewport 2.0 transparency mode.

        Parameters:
            algorithm (str): One of :attr:`TRANSPARENCY_ALGORITHMS`.

        Returns:
            bool: True if the mode was applied.
        """
        return cls._set_transparency_algorithm(algorithm=algorithm)

    @classmethod
    def ensure_transparent_graph(cls, mat: str) -> bool:
        """Load ``Standard_Transparent.sfx`` onto a StingrayPBS node if needed.

        The scalar ``opacity`` slot only exists on the transparent ShaderFX
        graph — a StingrayPBS built from the standard graph has nowhere to
        plug an opacity map, and the masked graph spends its alpha through
        ``TEX_mask_map`` instead.

        .. note:: ``loadGraph`` drops the node's existing connections; callers
           that need them preserved must snapshot first (see ``MatSnapshot``).

        Parameters:
            mat (str): StingrayPBS material.

        Returns:
            bool: True if the material now carries the transparent graph.
        """
        return cls._ensure_transparent_graph(mat=mat)

    @classmethod
    def get_file_nodes(
        cls,
        materials: Optional[List[str]] = None,
        raw: bool = False,
        return_type: str = "fileNode",
        exc_classification=None,
    ) -> list:
        """Returns file node info in any column order based on return_type.

        ``exc_classification`` (str/list, shell wildcards) drops file nodes used
        *exclusively* by shaders whose classification matches — e.g.
        ``"rendernode/arnold*"`` hides the duplicate rows an Arnold preview
        shader contributes (it owns dedicated file nodes per texture). A file
        node shared with a non-matching shader is kept, so hiding a renderer
        never hides a texture something else still uses.
        """
        return cls._get_file_nodes(
            materials=materials,
            raw=raw,
            return_type=return_type,
            exc_classification=exc_classification,
        )

    @staticmethod
    def get_fav_mats():
        """Retrieves the list of favorite materials in Maya."""
        return MatUtils._get_fav_mats()

    @staticmethod
    def is_mat_assigned(mat: object) -> bool:
        """True iff *mat*'s shading engines contain at least one DAG member.

        A material is considered "assigned" when geometry is bound to one of
        its shading engines (the same condition Maya's *Delete Unused
        Materials* targets). Orphan shading engines and unconnected shaders
        both return False.

        Works for surface, displacement, and volume shaders alike — follows
        all connections instead of probing a specific output attribute,
        which only exists on surface shaders.
        """
        return MatUtils._is_mat_assigned(mat=mat)

    @staticmethod
    def is_connected(mat: object, delete: bool = False) -> bool:
        """Checks if a given material is assigned and optionally deletes it."""
        return MatUtils._is_connected(mat=mat, delete=delete)

    @staticmethod
    @CoreUtils.undoable
    def create_mat(mat_type, prefix="", name=""):
        """Creates a material based on the provided type or a random material if 'mat_type' is 'random'."""
        return MatUtils._create_mat(mat_type=mat_type, prefix=prefix, name=name)

    @staticmethod
    @CoreUtils.undoable
    def assign_mat(objects, mat_name):
        """Assigns a material to a list of objects or components."""
        return MatUtils._assign_mat(objects=objects, mat_name=mat_name)

    @staticmethod
    def claim_material_name(shading_group: str, desired: str) -> str:
        """Rename a rebuilt network to *desired* once that name is free.

        A rebuild is necessarily created while the material it replaces still
        owns the name, so Maya hands it the clash spelling (``M_x`` ->
        ``M_x1``); the old one is retired moments later and the name falls
        free. Reclaiming it is what keeps a repeated hand-off non-destructive
        -- downstream (Unity, a shader library, an FBX round-trip) binds by
        material NAME, and the digit compounds on every re-send.

        Shared by every path that swaps a material in under an existing name:
        the Blender scene import (rebuilding an FBX-carried material) and the
        Marmoset bake roundtrip (replacing the previous bake's material).

        Yields silently whenever the name is still taken -- the replaced
        material may still be assigned elsewhere and keeps its claim. Cosmetic
        and best-effort; the caller's material is already correctly assigned.

        Parameters:
            shading_group: The rebuilt network's shading engine.
            desired: The name its surface shader should carry.

        Returns:
            The shading group's name, which the rename may have changed.
        """
        return MatUtils._claim_material_name(
            shading_group=shading_group, desired=desired
        )

    @staticmethod
    def get_shading_assignments(obj) -> Dict[str, Optional[List[int]]]:
        """Snapshot a mesh's shading-group membership as plain data.

        Returns a mapping ``{shading_group: faces}`` where *faces* is ``None``
        for a whole-object (single-material) assignment or a list of int face
        indices for a per-face (multi-material) assignment. The data form is
        decoupled from the live node graph, so it survives operations that
        corrupt or strip the in-scene component groups (see
        :meth:`apply_shading_assignments`).
        """
        return MatUtils._get_shading_assignments(obj=obj)

    @staticmethod
    def apply_shading_assignments(obj, assignments: Dict[str, Optional[List[int]]]):
        """Apply a :meth:`get_shading_assignments` snapshot onto *obj*.

        *obj* must share the snapshot's face indexing (same topology, or an op
        like ``polyBevel3`` that keeps the original faces' indices and only
        appends new ones — those new faces are base-coated with the dominant
        material). Restores per-face material after an in-place rebuild (e.g.
        ``delete(ch=True)``) or a hermetic-preview op drops it, which otherwise
        leaves a multi-material mesh unshaded (renders bright green).

        Single-material snapshots are applied as a whole-object assignment;
        multi-material snapshots are applied entirely through face components
        (see the body for why a whole-object base coat corrupts the result).
        """
        return MatUtils._apply_shading_assignments(obj=obj, assignments=assignments)

    # ------------------------------------------------------------------
    # Shared material-graph helpers
    # ------------------------------------------------------------------

    @staticmethod
    def create_file_node(image_path, name=None, color_space=None):
        """Create a ``file`` texture node with a wired ``place2dTexture``.

        Returns:
            tuple[str, str]: ``(file_node_name, place2d_node_name)``.
        """
        return MatUtils._create_file_node(
            image_path=image_path, name=name, color_space=color_space
        )

    @staticmethod
    def create_shading_group(shader, name=None, assign_to=None):
        """Create a shading group for *shader* and optionally assign objects."""
        return MatUtils._create_shading_group(
            shader=shader, name=name, assign_to=assign_to
        )

    # The opacity graphs are mayatk's own presets (``mat_utils/shaderfx/``):
    # Autodesk's ``Standard_Masked.sfx`` / ``Standard_Transparent.sfx`` plus the
    # AO chain (``use_ao_map`` / ``ao_map`` / its switch) that only
    # ``Standard.sfx`` ships, spliced in as text by
    # ``m3trik/scripts/build_stingray_ao_presets.py`` (ShaderFX has no
    # ``saveGraph``). Slot names are Autodesk's, so exporters and importers
    # treat them as on the opaque graph. Their ``preset_path`` is ``Custom``:
    # on scene open the plugin re-loads the preset a graph names and drops
    # nodes that preset lacks; a name no install carries keeps the stored
    # graph, so scenes built with these reopen complete without the file.
    STINGRAY_GRAPHS = {
        "none": "Standard.sfx",  # opaque (Maya's own)
        "masked": "Standard_Masked_AO.sfx",  # alpha test / cutout, with AO
        "transparent": "Standard_Transparent_AO.sfx",  # alpha blend, with AO
    }

    @classmethod
    @ptk.Deprecation.parameter(
        "opacity", remove_in="0.20.0", since="2026-09-23", reason=_OPACITY_IS_A_MODE
    )
    def resolve_opacity_mode(cls, opacity_mode=None, opacity: bool = False) -> str:
        """Normalize an opacity-mode argument to a :attr:`STINGRAY_GRAPHS` key.

        Parameters:
            opacity_mode: ``None`` / ``"none"`` / ``"masked"`` / ``"transparent"``
                (legacy aliases accepted). Unknown values fall back to
                ``"none"``.
            opacity (bool): DEPRECATED (warns; removed in 0.20.0) -- used
                only when *opacity_mode* is None, ``True`` meaning
                ``"transparent"``. Pass the mode.

        Returns:
            str: One of ``"none"``, ``"masked"``, ``"transparent"``.
        """
        return cls._resolve_opacity_mode(opacity_mode=opacity_mode, opacity=opacity)

    @classmethod
    def get_stingray_opacity_mode(cls, mat) -> Optional[str]:
        """The :attr:`STINGRAY_GRAPHS` key of the graph loaded on *mat*.

        A StingrayPBS node's attributes come from its ShaderFX graph, so the
        graph is identified by the slot only it exposes: ``opacity`` is the
        transparent graph's, ``TEX_mask_map`` the masked graph's, and a node
        with neither but a ``TEX_color_map`` carries the opaque graph. Every
        route that has to NAME the graph -- a slot-miss report, a
        transparency check -- reads it here rather than probing an attribute
        of its own.

        Parameters:
            mat: StingrayPBS node.

        Returns:
            str | None: ``"transparent"`` / ``"masked"`` / ``"none"``, or None
            for a bare node (no graph loaded yet) or a non-StingrayPBS node.
        """
        return cls._get_stingray_opacity_mode(mat=mat)

    @classmethod
    @ptk.Deprecation.parameter(
        "opacity", remove_in="0.20.0", since="2026-09-23", reason=_OPACITY_IS_A_MODE
    )
    def resolve_stingray_graph(cls, opacity_mode=None, opacity: bool = False):
        """Absolute path to the ShaderFX preset for *opacity_mode*.

        mayatk's own presets (``mat_utils/shaderfx/``, see
        :attr:`STINGRAY_GRAPHS`) are looked up first, then Maya's install.

        Returns:
            str | None: The ``.sfx`` path, or None when neither has it.
        """
        return cls._resolve_stingray_graph(opacity_mode=opacity_mode, opacity=opacity)

    @classmethod
    @ptk.Deprecation.parameter(
        "opacity", remove_in="0.20.0", since="2026-09-23", reason=_OPACITY_IS_A_MODE
    )
    def load_stingray_graph(cls, mat, opacity_mode=None, opacity: bool = False) -> bool:
        """Load the ShaderFX preset for *opacity_mode* onto a StingrayPBS node.

        The one place a ``.sfx`` reaches ``cmds.shaderfx`` — a StingrayPBS
        node's attributes come from its loaded graph, so every route that needs
        a particular slot set (network build, opacity enable, shader
        conversion) resolves the graph the same way.

        .. note:: ``loadGraph`` DROPS the node's existing connections; callers
           that need them preserved must snapshot first (see ``MatSnapshot``).

        Parameters:
            mat: StingrayPBS node.
            opacity_mode: See :meth:`resolve_opacity_mode`.
            opacity (bool): DEPRECATED (warns; removed in 0.20.0) -- the
                boolean form of *opacity_mode*.

        Returns:
            bool: True if a graph was loaded.
        """
        return cls._load_stingray_graph(
            mat=mat, opacity_mode=opacity_mode, opacity=opacity
        )

    @classmethod
    @ptk.Deprecation.parameter(
        "opacity", remove_in="0.20.0", since="2026-09-23", reason=_OPACITY_IS_A_MODE
    )
    def create_stingray_shader(cls, name, opacity=False, opacity_mode=None):
        """Create a StingrayPBS shader by loading a ShaderFX preset graph.

        StingrayPBS node attrs are graph-dependent — a bare ``StingrayPBS``
        node has none of ``base_color`` / ``TEX_color_map`` / ``opacity`` etc.,
        so a graph must be loaded.

        Parameters:
            name: Shader node name.
            opacity: DEPRECATED (warns; removed in 0.20.0) -- ``True`` meant
                ``opacity_mode="transparent"``; pass the mode.
            opacity_mode: One of:
                * ``None`` / ``"none"``: opaque, ``Standard.sfx``.
                * ``"masked"``: alpha cutout, ``Standard_Masked.sfx``.
                  Caller wires alpha to ``TEX_mask_map`` and tunes
                  ``mask_threshold``; clean VP2.0 preview, hard edges.
                * ``"transparent"``: alpha blend, ``Standard_Transparent.sfx``.
                  Caller wires alpha to scalar ``opacity``; soft edges,
                  but VP2.0 preview shows a faint tint over the quad.
        """
        return cls._create_stingray_shader(
            name=name, opacity=opacity, opacity_mode=opacity_mode
        )

    @classmethod
    def find_by_mat_id(
        cls, material: str, objects: Optional[List[str]] = None, shell: bool = False
    ) -> List[str]:
        """Find objects or faces by the material ID."""
        return cls._find_by_mat_id(material=material, objects=objects, shell=shell)

    @classmethod
    def find_unassigned(
        cls, objects: Optional[List[str]] = None, include_default: bool = True
    ) -> List[str]:
        """Objects carrying no material — the complement of :meth:`find_by_mat_id`.

        Maya has no true "no material" state for renderable geometry: new meshes
        join ``initialShadingGroup``, so they report the default shader
        (``standardSurface1`` on 2025+, ``lambert1`` before it) rather than
        nothing. Two distinct states therefore read as "unassigned" to a user:

        - **Default-shaded** — every shading engine the shape belongs to carries
          only a default material (``include_default``, the common case: geometry
          nobody has shaded yet).
        - **Orphaned** — the shape belongs to no shading engine at all (import
          artifacts, or an explicit ``sets -remove``). Always included; such
          geometry renders black and is otherwise hard to find.

        Object-level by design: a *partially* assigned mesh (some faces on a real
        material, the rest default) counts as assigned — its unshaded faces are a
        different question than "which objects did I forget to shade".

        Parameters:
            objects: Transforms, groups, shapes, or components to test. None (or
                empty) tests every renderable mesh in the scene — same convention
                as :meth:`find_by_mat_id`.
            include_default: Count default-shaded geometry as unassigned.
                False restricts the result to orphaned shapes.

        Returns:
            list[str]: Full-path transforms, in scene order.
        """
        return cls._find_unassigned(objects=objects, include_default=include_default)

    @staticmethod
    @ptk.filter_results
    def collect_material_paths(
        materials: Optional[List[str]] = None,
        attributes: Optional[List[str]] = None,
        inc_mat_name: bool = False,
        inc_path_type: bool = False,
        resolve_full_path: bool = False,
    ) -> Union[List[str], List[Tuple[str, ...]]]:
        """Collects specified attributes file paths for given materials."""
        return MatUtils._collect_material_paths(
            materials=materials,
            attributes=attributes,
            inc_mat_name=inc_mat_name,
            inc_path_type=inc_path_type,
            resolve_full_path=resolve_full_path,
        )

    @staticmethod
    def remap_file_nodes(
        file_paths: List[str],
        target_dir: str,
        silent: bool = False,
        limit_to_nodes: Optional[List[str]] = None,
        as_strings: bool = True,
    ) -> List[str]:
        """Internal helper to remap file nodes to target_dir, preserving relative subfolders inside sourceimages.

        Returns a list of remapped file-node names (strings).  ``as_strings``
        is retained for API compatibility — strings are always returned.
        """
        return MatUtils._remap_file_nodes(
            file_paths=file_paths,
            target_dir=target_dir,
            silent=silent,
            limit_to_nodes=limit_to_nodes,
            as_strings=as_strings,
        )

    @classmethod
    @CoreUtils.undoable
    def remap_texture_paths(
        cls,
        materials: Optional[List[str]] = None,
        new_dir: Optional[str] = None,
        silent: bool = False,
        file_nodes: Optional[List[str]] = None,
        objects: Optional[List[str]] = None,
        as_strings: bool = True,
    ) -> None:
        """Remaps file texture paths for materials to new_dir."""
        return cls._remap_texture_paths(
            materials=materials,
            new_dir=new_dir,
            silent=silent,
            file_nodes=file_nodes,
            objects=objects,
            as_strings=as_strings,
        )

    @classmethod
    def to_absolute(
        cls,
        path: str,
        workspace: Optional[str] = None,
        sourceimages: Optional[str] = None,
    ) -> str:
        """Resolve a stored texture path to an absolute, forward-slashed path.

        Inverse of :meth:`to_project_relative`, the round-trip that method
        validates against, and a mirror of the order Maya's own loader
        resolves a relative ``.ftn`` in: the project ROOT first, then the
        ``sourceImages`` file rule. Both halves are needed — the
        ``sourceimages/foo.png`` :meth:`to_project_relative` emits is
        root-relative (joining it onto sourceimages would double the folder),
        while a legacy rule-relative ``foo.png`` — the form emitted between
        2026-08-18 and 2026-08-25, still in every scene normalized in that
        window — is found only by
        the second. Measured against Maya 2025, including which candidate
        wins when both exist (the root — see the shadow guard there).

        Existence is asked through :meth:`texture_tiles`, so a UDIM/frame
        token resolves by its TILES rather than by a literal name that is
        never on disk. Nothing on disk under either candidate falls back to
        the root form: the honest guess, and the value the panel then paints
        as missing.

        Environment variables expand FIRST, and the relative/absolute
        classification is made on the expanded value — Maya resolves a ``$VAR``
        in a ``.ftn`` and :meth:`stage_textures_relative` has always read them
        that way. Left unexpanded, ``$TEXDIR/foo.png`` fails ``isabs`` and gets
        the workspace pasted in front (``<proj>/$TEXDIR/foo.png``, resolving
        nowhere), which is what made the Texture Path Editor paint a present
        file red and Make Paths Absolute write that value back. An UNDEFINED
        name is left intact, so the honest stored value survives.

        Parameters:
            path: Stored texture path (absolute, root- or rule-relative).
            workspace: Project root to resolve against. None resolves the
                current workspace per call — pass it in when looping.
            sourceimages: The ``sourceImages`` rule directory. None resolves
                it per call — pass it in when looping, and from the MAIN
                thread: the lookup goes through ``cmds.workspace``. An
                explicit ``workspace=""`` suppresses this lookup too, since a
                caller who ruled the project out cannot mean "but use its
                sourceimages".
        """
        return cls._to_absolute(
            path=path, workspace=workspace, sourceimages=sourceimages
        )

    @classmethod
    def to_project_relative(
        cls,
        path: str,
        workspace: Optional[str] = None,
        sourceimages: Optional[str] = None,
    ) -> str:
        """*path* as a project-relative form, or unchanged when none exists.

        The emitted form is relative to the project ROOT
        (``sourceimages/foo.png``, ``assets/sourceimages/sub/foo.png``) —
        Maya's own spelling for a relative texture path, the first thing its
        loader tries, and the form a hand-typed ``.ftn`` uses. It is also the
        only relative form the **FBX plug-in** can locate when it writes:
        that resolver is plain OS resolution against the process working
        directory (never the workspace), which the exporter's
        ``set_workspace`` task aligns with the project root — probe-proven
        2026-08-25, where a rule-relative name was left OUT of the embed and
        the root-relative one was embedded.

        Known cost, accepted deliberately: Maya EXPANDS a root-relative
        ``.ftn`` back to absolute as soon as it resolves on scene load, and
        the next save writes that absolute path back (measured across three
        save/open generations,
        ``test/temp_tests/probe_root_relative_reopen.py``). So the relative
        form survives exactly one save; a scene reopened and re-saved carries
        absolute paths until Normalize Paths is run again — which is
        idempotent, and is what the exporter's ``convert_to_relative_paths``
        does for every export, so what SHIPS is unaffected. The rule-relative
        ``foo.png`` form is the one that survives verbatim, but it is
        unconventional, invisible to the FBX writer, and hides which folder
        the texture is in — the reason this method emitted it between
        2026-08-18 and 2026-08-25.

        A path outside the ROOT but under an out-of-root ``sourceImages``
        rule (the rule may be absolute — ``Workspace.resolve``,
        "workspace-relative unless absolute") has no root-relative form that
        finds it, and falls back to the RULE-relative one, which does. Maya
        tries the root BEFORE the rule, so that fallback carries a shadow
        guard: when a DIFFERENT file of the same relative name sits at the
        project root, the form would silently bind to that one, and the
        texture keeps its absolute path instead.

        Anything under neither is returned absolute (normalized, forward
        slashes). Inverse of :meth:`to_absolute`, whose root-then-rule order
        this mirrors and whose round trip guards the emitted form.

        Parameters:
            path: Absolute path to relativize.
            workspace: Project root — what the emitted form is relative to.
                None resolves the current workspace per call — pass it in
                when looping.
            sourceimages: The ``sourceImages`` rule directory, used for the
                out-of-root fallback and the round-trip check. None resolves
                it per call — pass it in when looping, and from the MAIN
                thread: the lookup goes through ``cmds.workspace``.
        """
        return cls._to_project_relative(
            path=path, workspace=workspace, sourceimages=sourceimages
        )

    @classmethod
    @CoreUtils.undoable
    def stage_textures_relative(
        cls,
        file_nodes: List[str],
        sourceimages: Optional[str] = None,
        external_mode: str = "copy",
        scope: str = "sourceimages",
    ) -> Dict[str, str]:
        """Stage textures under sourceimages and store project-relative paths.

        Single per-node pass replacing the old copy-then-remap pair, whose
        basename-keyed handshake could rebind a node to an unrelated
        same-named file the copy step had refused, flattened valid
        ``sourceimages/sub/…`` paths, and remapped UDIM sets whose tiles were
        never copied.  Here every decision is made per node, atomically:

        - already-relative paths are left untouched, EXCEPT one in the legacy
          RULE-relative spelling (``foo.png``, emitted 2026-08-18 to
          2026-08-25), which names no folder and which the FBX writer cannot
          locate — that one is upgraded in place to the root-relative form;
        - absolute paths under sourceimages are relativized IN PLACE, with
          subfolders preserved;
        - external files (UDIM tile sets included, via token glob) are copied
          into sourceimages first — a destination collision is only reused
          when the CONTENT matches (size + partial hash); a different file
          with the same name is staged alongside it under an ``_N`` index on
          its BASE name (``rock_Base_Color.png`` → ``rock_1_Base_Color.png``,
          never ``rock_Base_Color_1.png``, which would hide the map type from
          the resolver), loudly, so the node is neither rebound to the wrong
          texture nor abandoned on an absolute path (which used to leak a
          cross-project path into both the scene and the export).  ``_N`` is
          reused when it already holds the same content, so repeat exports
          converge instead of stacking variants.

        The relative form is written with ``Attributes.set_plug_literal``
        (``MPlug.setString``) — plain ``cmds.setAttr`` auto-expands a
        resolvable relative path back to absolute (verified in mayapy), which
        is why the old remap never actually stored relative paths.  A ``cmds.setAttr`` of the original
        value runs first as the undo anchor, so undo still restores the
        pre-conversion path.

        Parameters:
            file_nodes: ``file`` nodes to process.
            sourceimages: Override the project's sourceimages directory.
            external_mode: What happens to a file stored outside the *scope*:

                - ``"copy"`` (default) — consolidate it into sourceimages
                  (the asset-consolidation behavior), then store the relative
                  form.
                - ``"move"`` — as ``"copy"``, but the external original (each
                  tile of a token set) is removed once its staged twin is in
                  place — including when an identical-content resident made
                  the copy itself unnecessary. A later node in the batch
                  storing the SAME path rebinds to that staged twin (the
                  source is gone by then, and "shared" must not read as
                  "missing").
                - ``"skip"`` — the node keeps its absolute path and is
                  reported ``skipped:external``, so a deliberate link to a
                  shared library is neither relocated nor rewritten.
                  Relativizing it without the copy is never an option — the
                  path would point at a file that isn't there.
            scope: Which stored paths count as "already in the project" and
                are relativized in place:

                - ``"sourceimages"`` (default) — only files under
                  sourceimages, subfolders included.
                - ``"project"`` — anything under the project ROOT, which also
                  takes in a texture sitting outside sourceimages. The Texture
                  Path Editor's Normalize Paths semantics.

                The scopes differ in WHAT they relativize, not in how: both
                write through :meth:`to_project_relative`, so both get the
                same root-relative form (this used to paste a literal
                ``sourceimages/`` prefix on the first — which names nothing
                under a nested rule — and the converter's form on the second).
                A path already relative but in the legacy rule-relative
                spelling is upgraded in place rather than reported
                ``already-relative``.

            Either scope relativizes only a path with a FILE behind it
            (tile/frame tokens resolved): a rewrite whose result names
            nothing buys no portability, and reporting it as ``relativized``
            put a batch of successes over rows that stayed red. It reports
            ``skipped:missing-source``, the same verdict the external branch
            has always returned for one.

        Returns:
            {file_node: status} where status is one of ``relativized``,
            ``copied+relativized``, ``moved+relativized``,
            ``variant+relativized``, ``already-relative``, or
            ``skipped:<reason>`` (``skipped:external`` under
            ``external_mode="skip"``).
        """
        return cls._stage_textures_relative(
            file_nodes=file_nodes,
            sourceimages=sourceimages,
            external_mode=external_mode,
            scope=scope,
        )

    @classmethod
    def rename_texture_file(
        cls,
        path: str,
        new_name: str,
        file_nodes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Rename a texture file on disk and repoint every file node reading it.

        *path* is a stored or absolute texture path; a tile / frame token
        renames every tile (``ptk.TiledPath.rename`` -- *new_name* keeps the
        tokens, each tile keeps its number). Nothing is renamed over another
        file, and a rename failing part-way is put back. The nodes keep their
        path's spelling -- relative stays relative -- with the new name.

        One undo step with the edits around it: undo renames the files back
        along with the paths that name them.

        Parameters:
            path: The texture (stored spelling or absolute).
            new_name: The new file NAME, no folder.
            file_nodes: The nodes to repoint. ``None`` (default): every file
                node in the scene that reads the file -- one left on the old
                name would read nothing.

        Returns:
            ``{"renamed": [(old, new)], "nodes": [repointed file node]}``;
            empty lists when the name is unchanged.

        Raises:
            ValueError: See ``ptk.TiledPath.rename`` (bad name, token mismatch,
                nothing on disk).
            FileExistsError: A target is another existing file.
        """
        return cls._rename_texture_file(
            path=path, new_name=new_name, file_nodes=file_nodes
        )

    @classmethod
    def sync_material_names(
        cls,
        material: Optional[str],
        base: str,
        material_affix: Tuple[str, str] = ("", ""),
        file_node_affix: Tuple[str, str] = ("", ""),
        file_nodes: Optional[List[str]] = None,
        dry_run: bool = False,
        lightmaps: Any = None,
    ) -> Dict[str, Any]:
        """Name a material, its texture set, lightmap and nodes for ONE base.

        One name drives the set: the material becomes *base* with
        *material_affix*, and the nodes named after it follow (its shading
        group ``<material>SG``, an Arnold ``<material>_ai`` on the same
        group). The texture set is the material's dominant one
        (``ptk.MapFactory.dominant_texture_set`` -- what a bake names its
        lightmap after); each of its textures keeps everything after the base
        (map type, tile token, extension), so ``rock_Base_Color.<UDIM>.png``
        follows ``stone`` as ``stone_Base_Color.<UDIM>.png``, and its file node
        is named after it (no token, no extension) with *file_node_affix*. The
        set's lightmap (``rock_Lightmap.exr``) follows too, its bake markers
        re-stamped. Left alone, and reported in ``skipped``: another set's map
        (an environment cube), and a file outside the scene's project, which
        another project may read -- its node named after the file it still
        reads.

        Planned whole first: a rename that would collide with another file
        refuses the lot before anything changes. Textures go through
        :meth:`rename_texture_file`, so every node reading them follows. One
        undo chunk.

        Parameters:
            material: The material (``None``: only *file_nodes*).
            base: The shared base name.
            material_affix, file_node_affix: ``(prefix, suffix)`` each.
            file_nodes: The file nodes to sync. ``None``: those in the
                material's history.
            dry_run: Return the plan; change nothing.
            lightmaps: The lightmap records the set's lightmap follows through
                -- ``lightmap_dependencies()`` and ``rename_lightmap(old,
                new)``, as ``LightmapRecords`` has them (the Texture Path
                Editor passes the ones it holds). ``None``: the lightmap is
                not followed. Passed in, not imported: mat_utils sits below
                light_utils (``[tool.m3trik.layers]``).

        Returns:
            ``{"material": (old, new) | None, "companions": [(old, new)],
            "textures": [(old path, new name)], "file_nodes": [(old, new)],
            "lightmaps": [(path, old map, new map)], "skipped": [reason]}``.

        Raises:
            ValueError: *base* is unusable, or a rename would collide.
        """
        return cls._sync_material_names(
            material=material,
            file_nodes=file_nodes,
            base=base,
            material_affix=tuple(material_affix),
            file_node_affix=tuple(file_node_affix),
            dry_run=dry_run,
            lightmaps=lightmaps,
        )

    @staticmethod
    def is_duplicate_material(material1: str, material2: str) -> bool:
        """Check if two materials are duplicates based on their textures."""
        return MatUtils._is_duplicate_material(material1=material1, material2=material2)

    @classmethod
    def find_materials_with_duplicate_textures(
        cls,
        materials: Optional[List[str]] = None,
        strict: bool = False,
        verify: bool = True,
    ) -> Dict[str, List[str]]:
        """Find duplicate materials based on their texture file names or full paths.

        Two-phase.  Phase 1 groups CANDIDATES by a cheap fingerprint —
        ``(node type, {(attribute, texture id)})`` where the non-strict
        texture id is the lowercased basename stem, so same-named textures
        from different folders (``brick/albedo.png`` vs ``wood/albedo.png``)
        and same-texture-different-tiling setups land in one group.  Phase 2
        (``verify=True``, the default) pairwise-verifies every candidate
        against its group's keeper before it is reported: equal unconnected
        scalar attribute values, identical place2d placement and color space
        per texture slot, and texture CONTENT identity (size + partial hash)
        when the stored paths differ.  Only verified duplicates are returned
        — the verification is what makes the result safe to feed
        :meth:`reassign_duplicate_materials`' destructive merge.

        Parameters:
            materials: Materials to scan.  None scans every scene material.
            strict: Fingerprint on the full path (case-folded where the
                filesystem folds case, ``os.path.normcase``) instead of the
                basename stem (narrows phase 1's candidate net).
            verify: Skip phase 2 when False — candidates are returned
                unverified (the pre-2026-08 behavior; false-positive-prone).
        """
        return cls._find_materials_with_duplicate_textures(
            materials=materials, strict=strict, verify=verify
        )

    @classmethod
    @CoreUtils.undoable
    def reassign_duplicate_materials(
        cls,
        materials: Optional[List[str]] = None,
        delete: bool = False,
        strict: bool = False,
        verify: bool = True,
    ) -> None:
        """Find duplicate materials, remove duplicates, and reassign them to the original material.

        ``verify`` (default True) gates the merge on the pairwise
        verification pass — see :meth:`find_materials_with_duplicate_textures`.
        Only pass False deliberately: unverified candidates include
        same-basename-different-content and same-texture-different-tiling
        near-misses, and this method deletes what it merges.
        """
        return cls._reassign_duplicate_materials(
            materials=materials, delete=delete, strict=strict, verify=verify
        )

    @staticmethod
    @ptk.Deprecation.parameter(
        "as_strings", drop=True, remove_in="0.20.0", since="2026-09-23"
    )
    def filter_materials_by_objects(
        objects: List[str],
        as_strings: bool = True,
        include_displacement: bool = False,
    ) -> List[str]:
        """Filter materials assigned to the given objects.

        *as_strings* is DEPRECATED and ignored (warns; removed in 0.20.0).
        """
        return MatUtils._filter_materials_by_objects(
            objects=objects,
            as_strings=as_strings,
            include_displacement=include_displacement,
        )

    @staticmethod
    def reload_textures(
        materials=None,
        inc=None,
        exc=None,
        log=False,
        refresh_viewport=False,
        refresh_hypershade=False,
        texture_types: Optional[List[str]] = None,
    ):
        """Reloads textures connected to specified materials with inclusion/exclusion filters."""
        return MatUtils._reload_textures(
            materials=materials,
            inc=inc,
            exc=exc,
            log=log,
            refresh_viewport=refresh_viewport,
            refresh_hypershade=refresh_hypershade,
            texture_types=texture_types,
        )

    @classmethod
    def move_texture_files(
        cls,
        found_files: List[Union[str, Tuple[str, str]]],
        new_dir: str,
        delete_old: bool = False,
        create_dir: bool = True,
        per_file_timeout: float = 120.0,
        max_workers: int = 8,
        progress_callback: Optional[Callable[[int, int, str], bool]] = None,
    ) -> List[Tuple[str, str]]:
        """Move or copy found texture files to a new directory.

        Returns the list of (src, dst) pairs that completed successfully
        (including those skipped as already up-to-date when delete_old is
        False). Failed/timed-out files are omitted.

        per_file_timeout: max seconds to wait for any single copy before
            abandoning the pool. Python cannot kill a worker thread blocked
            inside shutil.copy2, so on timeout we stop dispatching, cancel
            pending futures, and shutdown(wait=False) — in-flight workers
            leak until the OS unblocks them (or Maya exits). The win is
            that Maya gets the UI back instead of hanging forever.
        progress_callback: optional fn(done, total, last_filename) called
            from the main thread after each future completes. Return False
            to request early termination. Exceptions raised from the
            callback are swallowed and treated as "keep going".
        """
        return cls._move_texture_files(
            found_files=found_files,
            new_dir=new_dir,
            delete_old=delete_old,
            create_dir=create_dir,
            per_file_timeout=per_file_timeout,
            max_workers=max_workers,
            progress_callback=progress_callback,
        )

    @classmethod
    def copy_textures_to_sourceimages(
        cls,
        objects: Optional[List[str]] = None,
        materials: Optional[List[str]] = None,
        file_nodes: Optional[List[str]] = None,
        sourceimages_dir: Optional[str] = None,
        delete_old: bool = False,
    ) -> List[Tuple[str, str]]:
        """Copy referenced textures that live outside ``sourceimages`` into it.

        This is the prerequisite for converting texture paths to relative: a
        project-relative path only resolves if the file physically lives under
        ``sourceimages``.  Remapping an external texture to a relative path
        *without* first copying the file in silently breaks the link — the
        exported asset then points at a texture that isn't there.  Use this
        before :meth:`remap_texture_paths` whenever the destination is
        ``sourceimages`` and inputs may be stored elsewhere.

        Only files that exist on disk and are not already under
        ``sourceimages`` are copied.  A file whose basename already exists in
        ``sourceimages`` is left untouched: identical size is treated as the
        same asset (the relative path will resolve to it), while a different
        size is a name collision — skipped with a warning rather than
        clobbering a different texture or silently rebinding to the wrong one.
        Tile/frame token patterns (:attr:`_PATH_TOKENS`) are skipped (no
        single file to copy); the token is preserved by the subsequent remap.

        Parameters:
            objects/materials/file_nodes: Scope to resolve textures from. When
                all are None, every ``file`` node in the scene is considered.
            sourceimages_dir: Destination; defaults to the project's
                ``sourceimages`` directory.
            delete_old: Forwarded to :meth:`move_texture_files` — True moves
                the external file in instead of copying it.

        Returns:
            The (src, dst) pairs that were copied/moved (empty when nothing
            needed copying).
        """
        return cls._copy_textures_to_sourceimages(
            objects=objects,
            materials=materials,
            file_nodes=file_nodes,
            sourceimages_dir=sourceimages_dir,
            delete_old=delete_old,
        )

    @classmethod
    def find_texture_files(
        cls,
        objects: Optional[List[str]] = None,
        source_dir: str = "",
        recursive: bool = True,
        return_dir: bool = False,
        quiet: bool = False,
        file_nodes: Optional[List[str]] = None,
        materials: Optional[List[str]] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        filenames: Optional[List[str]] = None,
    ) -> List[Union[str, Tuple[str, str]]]:
        """Find texture files for given objects' materials inside source_dir.

        ``filenames`` widens the lookup to names that belong to no file node
        -- a lightmap the bake markers reference, a map a manifest names -- so
        a caller holding only basenames searches through this one walk (and
        its skip list) rather than a second one. With ``filenames`` alone the
        scope may be empty; the two sources simply add up.

        Names match case-insensitively. A tile/frame token (``<UDIM>``,
        ``<uvtile>``, ``<u>_<v>``, ``<f>`` ...) matches every file of that
        set through the one token table (:meth:`token_wildcard`); the walk
        used to expand ``<udim>`` alone, so a ``<uvtile>`` or ``<f>`` node
        searched for a literal file that cannot exist and was never found.
        """
        return cls._find_texture_files(
            objects=objects,
            source_dir=source_dir,
            recursive=recursive,
            return_dir=return_dir,
            quiet=quiet,
            file_nodes=file_nodes,
            materials=materials,
            progress_callback=progress_callback,
            filenames=filenames,
        )

    @classmethod
    @CoreUtils.undoable
    def migrate_textures(
        cls,
        materials: Optional[List[str]] = None,
        old_dir: Optional[str] = None,
        new_dir: Optional[str] = None,
        silent: bool = False,
        delete_old: bool = False,
        objects: Optional[List[str]] = None,
        file_nodes: Optional[List[str]] = None,
        progress_callback: Optional[Callable[[int, int, str], bool]] = None,
    ) -> None:
        """Copies texture files from an old directory to a new one."""
        return cls._migrate_textures(
            materials=materials,
            old_dir=old_dir,
            new_dir=new_dir,
            silent=silent,
            delete_old=delete_old,
            objects=objects,
            file_nodes=file_nodes,
            progress_callback=progress_callback,
        )

    @staticmethod
    def move_unused_textures(source_dir: str = None, output_dir: str = None) -> None:
        """Move unused textures to a specified directory."""
        return MatUtils._move_unused_textures(
            source_dir=source_dir, output_dir=output_dir
        )

    @staticmethod
    def get_mat_swatch_icon(
        mat: Union[str, object],
        size: List[int] = [20, 20],
        fallback_to_blank: bool = True,
    ) -> object:
        """Get an icon with a color fill matching the given material's RGB value."""
        return MatUtils._get_mat_swatch_icon(
            mat=mat, size=size, fallback_to_blank=fallback_to_blank
        )

    @staticmethod
    @CoreUtils.undoable
    def convert_bump_to_normal(
        bump_file_node,
        output_path: Optional[str] = None,
        intensity: float = 1.0,
        format_type: str = "opengl",
        create_file_node: bool = True,
        node_name: Optional[str] = None,
    ) -> Optional[str]:
        """Convert a bump/height file node's texture to a normal map on disk.

        The image conversion is delegated to
        :meth:`pythontk.MapFactory.convert_bump_to_normal` (Sobel-based,
        writes a real normal map next to the source unless ``output_path``
        is given). The previous implementation built a bump2d/reverse
        shading network that never produced a file and inverted all three
        channels for "directx" — it was unused and has been replaced.

        Parameters:
            bump_file_node: A ``file`` node whose ``fileTextureName`` points
                at the bump/height texture.
            output_path: Explicit output file path. Defaults to a
                ``_Normal_<Format>`` sibling of the source.
            intensity: Height depth multiplier passed to the converter.
            format_type: ``"opengl"`` or ``"directx"``.
            create_file_node: When True (default), also create a wired
                ``file``/``place2dTexture`` pair for the result with
                colorSpace ``Raw``.

        Returns:
            Optional[str]: The created file-node name, or the written image
            path when ``create_file_node=False``; ``None`` on failure.
        """
        return MatUtils._convert_bump_to_normal(
            bump_file_node=bump_file_node,
            output_path=output_path,
            intensity=intensity,
            format_type=format_type,
            create_file_node=create_file_node,
            node_name=node_name,
        )

    @staticmethod
    def validate_normal_map_setup(
        normal_file_node,
        material=None,
    ) -> Dict[str, Any]:
        """Validate normal map file node setup and provide recommendations."""
        return MatUtils._validate_normal_map_setup(
            normal_file_node=normal_file_node, material=material
        )

    @staticmethod
    def graph_materials(
        materials: Union[str, List[str], object], mode: str = "showUpAndDownstream"
    ) -> None:
        """Open the Hypershade and graph the specified materials."""
        return MatUtils._graph_materials(materials=materials, mode=mode)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    ...

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
