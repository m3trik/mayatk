# !/usr/bin/python
# coding=utf-8
"""The ``materials`` record's Maya producer.

Every material an export ships, read from the scene as staged for the write
-- a texture template's converted copies, the optimized maps, the shadow
planes' fades -- into the host-free model that states it
(``ptk.MaterialFamilies``): the record the FBX carries
(``ptk.SceneRecords.MATERIALS``, literal glTF 2.0), which the GLB built from
that FBX copies as its own materials and unitytk shades from. A transient
record: the export's restore clears it, so the scene never holds a statement
of maps it has since put back. Its one write is glTF's own cost: each
material's metal and roughness maps packed into one image, a delivery file
written into the pack store and never into a source folder
(``ptk.MaterialFamilies.pack_metallic_roughness``), which the delivery
embeds with the other maps (``ptk.FbxMedia.embed_dependencies``).

This module only GATHERS: each shader type's values in its family's
vocabulary (:attr:`MaterialRecords.FAMILIES`) and its maps by logical
channel (:class:`~mayatk.mat_utils.mat_manifest.MatManifest`). What they mean
in glTF's terms is the model's, shared with the FBX reader that states a
record-less file -- so a Maya file read back states what this produced. It is
the one reader of the scene's materials: the export's compatibility check
asks it too.

Mirror of ``blendertk.env_utils.material_records`` (name + behavior; each
reads its own host's shaders).
"""

import logging
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

try:
    import maya.cmds as cmds
except Exception:
    cmds = None

import pythontk as ptk

logger = logging.getLogger(__name__)


class MaterialRecords(ptk.HelpMixin):
    """Maya's materials, stated for the ``materials`` record.

    >>> record = MaterialRecords.export_record(ptk.ExportContext())
    >>> record.payload["materials"][0]["name"]
    'MAT_Body'
    """

    #: Maya shader type -> (``ptk.MaterialFamilies`` family, the method that
    #: reads its values in that family's vocabulary). A shader type no row
    #: names is left out of the record and read from the file's own material
    #: by every reader -- named once in the export's notes.
    FAMILIES: Dict[str, Tuple[str, str]] = {
        "StingrayPBS": ("stingray_pbs", "_stingray_values"),
        "standardSurface": ("pbr", "_surface_values"),
        "aiStandardSurface": ("pbr", "_surface_values"),
        "openPBRSurface": ("pbr", "_surface_values"),
        "lambert": ("legacy", "_legacy_values"),
        "phong": ("legacy", "_legacy_values"),
        "phongE": ("legacy", "_legacy_values"),
        "blinn": ("legacy", "_legacy_values"),
        "surfaceShader": ("unlit", "_unlit_values"),
    }

    #: Maya's unlit shading model: the FBX makes it lit PBR like any other
    #: shader, and its ``outTransparency`` is a transparency channel like the
    #: legacy models'.
    UNLIT_SHADERS = frozenset({"surfaceShader"})

    #: Shaders whose transparency is a plain ``opacity`` colour rather than a
    #: graph: a driven or non-opaque channel IS alpha blend, and the FBX
    #: cannot say so (measured 2026-09-02: a standardSurface whose opacity
    #: came from a texture's alpha reached the GLB OPAQUE). openPBR's is the
    #: scalar ``geometryOpacity`` (``ShaderAttributeMap`` names each one).
    SURFACE_OPACITY_SHADERS = frozenset(
        {"standardSurface", "aiStandardSurface", "openPBRSurface"}
    )

    #: What else shades a material unlit whatever it wears, as ``(module,
    #: class, method)`` rows resolved on use (``FbxUtils._resolve_row``, as
    #: its producers are): the method returns the shaders. The shadow rig
    #: ranks above this part, so it is named here, never imported.
    UNLIT_SOURCES: Tuple[Tuple[str, str, str], ...] = (
        ("mayatk.rig_utils.shadow_rig._shadow_rig", "ShadowRig", "plane_materials"),
    )

    #: A legacy shader's FBX model, as Maya's FBX exporter writes it
    #: (``ShadingModel``) -- what selects the legacy family's specular rule.
    LEGACY_MODELS: Dict[str, str] = {
        "lambert": "lambert",
        "phong": "phong",
        "phongE": "phong",
        "blinn": "phong",
    }

    @staticmethod
    def blinn_shininess(eccentricity: float) -> float:
        """The Phong shininess Maya's FBX exporter writes for a Blinn of
        *eccentricity*: ``0.5 * e ** -2.106`` with the eccentricity held to
        0.5 at most and the shininess to 300 (fitted to 16 exports, exact to
        the exporter's own float; Maya 2025, FBX 2020.3.6, 2026-10-20). A
        Phong E writes none -- FBX's default 20 -- nor does this record."""
        e = min(float(eccentricity), 0.5)
        return 300.0 if e <= 0.0 else min(300.0, 0.5 * e ** -2.106)

    # ---------------------------------------------------------------- record
    @classmethod
    def export_record(cls, ctx: "ptk.ExportContext") -> Optional["ptk.Record"]:
        """The ``materials`` record for this write, or ``None`` when no mesh
        wears a material -- the ``ptk.SceneRecords.MATERIALS`` producer
        (``FbxUtils.PRODUCERS``). It reads the staged scene and changes
        nothing in it; its one write is each material's metal and roughness
        maps packed into one image, glTF's layout -- a delivery file in the
        pack store, never beside the maps
        (``ptk.MaterialFamilies.pack_metallic_roughness``: a re-export of the
        same maps reuses it, a changed map packs anew).

        Parameters:
            ctx: The export's decisions; ``ctx.scope`` narrows the materials
                to those the write ships (the whole scene's meshes when
                ``None``), and what a material carries that the record cannot
                state is noted on it.

        Raises:
            OSError: A material's metal and roughness maps could not be packed
                (a map missing or unreadable, the pack store not writable): the
                record is not produced, and the message says what to fix --
                never a material read dielectric and matte unsaid.

        A material only lightmapped objects wear states no occlusion map --
        the bake holds their occlusion -- and one that lightmapped and
        unbaked objects share keeps it, noted with the fix: decided against
        the ``lightmaps`` record this write ships, by mesh shape
        (``ptk.MaterialFamilies.export_record``, the producer both hosts
        share; this reads which shapes wear which materials, :meth:`_worn`).
        """
        from mayatk.node_utils.data_nodes import DataNodes

        objects = cls._export_objects(ctx.scope)
        if not objects:
            return None
        notes: List[str] = []
        return ptk.MaterialFamilies.export_record(
            ctx,
            cls.entries(objects, notes=notes),
            lambda: cls._worn(objects),
            store=DataNodes,
            pack=ptk.MaterialFamilies.pack_metallic_roughness,
            notes=notes,
        )

    @classmethod
    def entries(
        cls, objects: Iterable[str], notes: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """The record entry of every material *objects* wear, in scene order.

        Parameters:
            objects: Transforms or shapes; a transform counts with its
                descendants.
            notes: Where a sentence about what the record cannot state goes.
        """
        from mayatk.mat_utils._mat_utils import MatUtils
        from mayatk.mat_utils.mat_manifest import MatManifest

        objects = cls._expand(list(objects))
        materials = (MatUtils.get_mats(objects) or []) if objects else []
        types = cls._node_types(materials)
        unlit = cls._unlit_materials(materials, types)
        found: List[Dict[str, Any]] = []
        skipped: Dict[str, List[str]] = {}
        for mat in materials:
            node_type = types.get(mat)
            if node_type is None:  # gone since get_mats named it
                continue
            row = cls.FAMILIES.get(node_type)
            if row is None:
                skipped.setdefault(node_type, []).append(mat)
                continue
            family, method = row
            values = getattr(cls, method)(mat, node_type)
            if family != "stingray_pbs":  # its graph carries its own uv_*
                values.update(cls._uv_values(mat, notes))
            found.append(
                ptk.MaterialFamilies.entry(
                    family,
                    mat,
                    values,
                    # Its maps by channel, read off this material alone: the
                    # objects are expanded once, above (``MatManifest.build``
                    # would expand and resolve them again).
                    MatManifest._process_material(mat),
                    unlit=mat in unlit,
                    notes=notes,
                )
            )
        if notes is not None:
            for node_type, names in skipped.items():
                notes.append(
                    f"Materials: no family states a {node_type} ({', '.join(names)}); "
                    "every reader takes the file's own material for it."
                )
        return found

    # ---------------------------------------------------------------- values
    #: A ``place2dTexture``'s attributes the record has no term for, and the
    #: value at which each changes nothing.
    UNSTATED_PLACEMENT: Dict[str, Any] = {
        "rotateUV": 0.0,
        "rotateFrame": 0.0,
        "coverage": (1.0, 1.0),
        "translateFrame": (0.0, 0.0),
    }

    @classmethod
    def _uv_values(cls, mat: str, notes: Optional[List[str]] = None) -> Dict[str, Any]:
        """The UV transform *mat*'s maps share, in the family's vocabulary
        (``uv_offset``, ``uv_scale``: Maya's UV space, which the model turns
        into glTF's): the ``place2dTexture`` feeding its file nodes, its
        ``offset`` and ``repeatUV``. Empty with none, or one that changes
        nothing. Maps placed differently state the first one's, and a rotation
        or a frame the record has no term for is said in *notes*."""
        from mayatk.mat_utils.mat_manifest import MatManifest

        found = None
        for file_node in MatManifest._channel_file_nodes(mat).values():
            placed = (
                cmds.listConnections(
                    f"{file_node}.uvCoord",
                    source=True,
                    destination=False,
                    type="place2dTexture",
                )
                or []
            )
            if not placed:
                continue
            node = placed[0]
            value = (
                tuple(round(v, 6) for v in cls._value(node, "offset") or (0.0, 0.0)),
                tuple(round(v, 6) for v in cls._value(node, "repeatUV") or (1.0, 1.0)),
            )
            if found is None:
                found = value
            elif value != found and notes is not None:
                notes.append(
                    f"Material {mat}: its maps are placed differently; the record "
                    f"states the first map's placement for all of them."
                )
            for attr, rest in cls.UNSTATED_PLACEMENT.items():
                current = cls._value(node, attr)
                if current is None or notes is None:
                    continue
                current = tuple(current) if isinstance(current, (list, tuple)) else current
                if current != rest:
                    notes.append(
                        f"Material {mat}: its {node}.{attr} is not stated (the record "
                        "carries a UV offset and scale alone)."
                    )
        if found is None or found == ((0.0, 0.0), (1.0, 1.0)):
            return {}
        return {"uv_offset": found[0], "uv_scale": found[1]}

    @classmethod
    def _stingray_values(cls, mat: str, node_type: str) -> Dict[str, Any]:
        """A StingrayPBS's own attributes -- the family's vocabulary is the
        ShaderFX graph's, as Maya's FBX exporter writes them too. One the
        graph does not carry is left out: which of ``opacity`` and
        ``mask_threshold`` exists is the graph's alpha mode."""
        values: Dict[str, Any] = {}
        for attr in ptk.MaterialFamilies.PROPS["stingray_pbs"]:
            if cmds.attributeQuery(attr, node=mat, exists=True):
                values[attr] = cls._value(mat, attr)
        return values

    @classmethod
    def _surface_values(cls, mat: str, node_type: str) -> Dict[str, Any]:
        """A Standard Surface, aiStandardSurface or openPBR, in the ``pbr``
        vocabulary: each value read off the attribute that HOLDS it
        (``ShaderAttributeMap.constant_attr`` -- never a sampler's literal),
        the base and emission weights as the shader applies them, and the
        opacity's scalar part (a shadow plane's fade through its
        ``multiplyDivide``) with whether anything drives it."""
        from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap

        def held(logical: str) -> Any:
            attr = ShaderAttributeMap.constant_attr(node_type, logical)
            return cls._value(mat, attr) if attr else None

        factor = cls._opacity_factor(mat, node_type)
        transmission = ShaderAttributeMap.get_attr(node_type, "transmission")
        return {
            "base_color": held("baseColor"),
            "base_weight": ShaderAttributeMap.base_weight(mat, node_type),
            "metallic": held("metallic"),
            "roughness": held("roughness"),
            "emission_color": held("emission"),
            "emission_weight": ShaderAttributeMap.emission_weight(mat, node_type),
            "opacity": 1.0 if factor is None else factor,
            "opacity_driven": cls._surface_opacity_is_driven(mat, node_type),
            # The record has no field for it; the family says so.
            "transmission": cls._value(mat, transmission[0]) if transmission else None,
        }

    @classmethod
    def _legacy_values(cls, mat: str, node_type: str) -> Dict[str, Any]:
        """One of Maya's own models in FBX's terms, as its FBX exporter writes
        them: the colour and its ``diffuse`` factor, ``transparency`` (factor
        1), ``incandescence`` (factor 1), a Phong's ``cosinePower`` as its
        shininess and a Blinn's eccentricity converted
        (:meth:`blinn_shininess`)."""
        values = {
            "ShadingModel": cls.LEGACY_MODELS.get(node_type, "lambert"),
            "DiffuseColor": cls._value(mat, "color"),
            "DiffuseFactor": cls._value(mat, "diffuse"),
            "TransparentColor": cls._value(mat, "transparency"),
            "TransparencyFactor": 1.0,
            "EmissiveColor": cls._value(mat, "incandescence"),
            "EmissiveFactor": 1.0,
        }
        if node_type == "phong":
            values["Shininess"] = cls._value(mat, "cosinePower")
        elif node_type == "blinn":
            values["Shininess"] = cls.blinn_shininess(
                ptk.MaterialFamilies.as_float(cls._value(mat, "eccentricity"), 0.3)
            )
        return values

    @classmethod
    def _unlit_values(cls, mat: str, node_type: str) -> Dict[str, Any]:
        """Maya's unlit model: its colour, and its ``outTransparency`` as the
        opacity, blending wherever it is driven or above zero."""
        clear = ptk.MaterialFamilies.as_floats(
            cls._value(mat, "outTransparency"), 3, 0.0
        )
        return {
            "color": cls._value(mat, "outColor"),
            "opacity": 1.0 - sum(clear) / 3.0,
            "opacity_driven": cls._channel_is_driven(mat, "outTransparency", clear=0.0),
        }

    @classmethod
    def _opacity_factor(cls, mat: str, node_type: str) -> Optional[float]:
        """The scalar part of a :attr:`SURFACE_OPACITY_SHADERS` opacity --
        what glTF states as ``baseColorFactor[3]`` -- or None at 1.0.

        Two shapes carry one: a bare constant (a tint), and a texture's alpha
        times a scalar through a ``multiplyDivide`` -- the shadow rig's
        silhouette x its plane's ``opacity`` fade, read at the current frame
        (a keyed fade also ships as a fade track, which drives the same
        factor). A texture alone states nothing: its alpha rides the base
        colour texture. The FBX carries neither: a shadow plane held at 0.836
        reached WebXR at alpha 1.0, solid black where VP2 drew it
        half-transparent (2026-10-15).
        """
        from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap

        if node_type not in cls.SURFACE_OPACITY_SHADERS:
            return None
        slot = ShaderAttributeMap.get_attr(node_type, "opacity")
        plug = f"{mat}.{slot[0]}" if slot else None
        if not plug or not cmds.objExists(plug):
            return None
        sources = (
            cmds.listConnections(plug, source=True, destination=False, plugs=True) or []
        )
        try:
            if not sources:
                value = ShaderAttributeMap.read_constant(mat, "opacity", node_type)
                if not value:
                    return None
                factor = ptk.MaterialFamilies.opacity_scalar(value)
            else:
                node = sources[0].split(".")[0]
                if cmds.nodeType(node) != "multiplyDivide" or (
                    cmds.getAttr(f"{node}.operation") != 1
                ):
                    return None
                factor = None
                for texture_in, scalar_in in (
                    ("input1X", "input2X"),
                    ("input2X", "input1X"),
                ):
                    upstream = (
                        cmds.listConnections(
                            f"{node}.{texture_in}", source=True, destination=False
                        )
                        or []
                    )
                    if (
                        upstream
                        and "texture"
                        in (cmds.getClassification(cmds.nodeType(upstream[0])) or [""])[
                            0
                        ]
                    ):
                        factor = float(cmds.getAttr(f"{node}.{scalar_in}"))
                        break
                if factor is None:
                    return None
        except (RuntimeError, ValueError, TypeError, IndexError):
            return None
        factor = min(1.0, max(0.0, factor))
        return factor if factor < 1.0 - 1e-4 else None

    @classmethod
    def _surface_opacity_is_driven(cls, mat: str, node_type: str) -> bool:
        """Is *mat*'s opacity connected or below 1.0 on any channel?"""
        from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap

        slot = ShaderAttributeMap.get_attr(node_type, "opacity")
        return bool(slot) and cls._channel_is_driven(mat, slot[0], clear=1.0)

    @staticmethod
    def _channel_is_driven(mat: str, channel: str, clear: float) -> bool:
        """Is *mat*'s *channel* connected (on the compound or a child) or away
        from *clear* -- the value that means fully opaque -- on any component?
        Maya allows a compound and its children to be wired independently, so
        both are checked. A scalar channel (openPBR's ``geometryOpacity``) is
        its own single component; read as a colour it raised, and the reader
        took the failure for "opaque"."""
        plug = f"{mat}.{channel}"
        if not cmds.objExists(plug):
            return False
        for candidate in [plug] + [f"{plug}{c}" for c in "RGB"]:
            if cmds.objExists(candidate) and cmds.listConnections(
                candidate, source=True, destination=False
            ):
                return True
        try:
            value = cmds.getAttr(plug)
            components = list(value[0])[:3] if isinstance(value, list) else [value]
        except (RuntimeError, ValueError, TypeError, IndexError):
            return False
        return max(abs(float(c) - clear) for c in components) > 1e-4

    # ----------------------------------------------------------------- scene
    @classmethod
    def _unlit_materials(
        cls, materials: Iterable[str], types: Optional[Dict[str, str]] = None
    ) -> Set[str]:
        """The *materials* the scene shades unlit whatever they wear (*types*:
        their node types when already read, :meth:`_node_types`): Maya's
        unlit model (:attr:`UNLIT_SHADERS`), and the material of every
        projected shadow plane (``ShadowRig``) -- a shadow is darkness laid
        over what is under it, never a surface the environment lights. The
        FBX can say neither: FBX2glTF made each lit PBR, and a shadow plane
        in WebXR took the environment's specular and read BRIGHTER than the
        floor it lay on (2026-10-15)."""
        from mayatk.env_utils.fbx_utils import FbxUtils

        materials = list(materials)
        if types is None:
            types = cls._node_types(materials)
        unlit = {m for m in materials if types.get(m) in cls.UNLIT_SHADERS}
        wanted = set(materials)
        for row in cls.UNLIT_SOURCES:
            resolved = FbxUtils._resolve_row("Unlit source", *row)
            if not resolved or resolved[0] is None:
                continue
            try:
                unlit.update(s for s in resolved[0]() if s in wanted)
            except Exception as error:  # noqa: BLE001 -- the record states the rest
                logger.debug("Unlit source %s unread: %s", row[1], error)
        return unlit

    @classmethod
    def _worn(cls, objects: Iterable[str]) -> List[Tuple[List[List[str]], List[str]]]:
        """Per mesh shape *objects* ship (as :meth:`entries` reads them, a
        transform with its descendants): the hierarchy of each transform it
        is instanced under -- its DAG path's names, root first, as a
        ``lightmaps`` entry's ``hierarchy`` -- and the materials the shape
        wears, per-face assignment included (``MatUtils.get_mats`` of the
        shape). Shapes, never transforms: a group counts as its contents,
        so a group root read as an object put every baked material among
        the unbaked ones too (``ptk.MaterialFamilies.export_record``)."""
        from mayatk.mat_utils._mat_utils import MatUtils

        nodes = cls._expand(list(objects))
        shapes = cmds.ls(nodes, type="mesh", noIntermediate=True, long=True) or []
        transforms = cmds.ls(nodes, type="transform", long=True) or []
        if transforms:
            shapes += (
                cmds.listRelatives(
                    transforms,
                    shapes=True,
                    type="mesh",
                    noIntermediate=True,
                    fullPath=True,
                )
                or []
            )
        worn: List[Tuple[List[List[str]], List[str]]] = []
        for shape in dict.fromkeys(shapes):
            parents = cmds.listRelatives(shape, allParents=True, fullPath=True) or []
            worn.append(
                (
                    [
                        [part for part in parent.split("|") if part]
                        for parent in parents
                    ],
                    list(MatUtils.get_mats(shape) or []),
                )
            )
        return worn

    @staticmethod
    def _expand(objects: List[str]) -> List[str]:
        """*objects* plus their descendant transforms, de-duplicated, in two
        queries -- ``MatUtils.get_mats`` reads direct shapes only, so a
        selected hierarchy root would otherwise resolve to no materials at
        all. A component or a shape passes through as itself."""
        objects = [str(obj) for obj in objects]
        roots = cmds.ls(objects, type="transform", long=True) if objects else []
        below = (
            cmds.listRelatives(
                roots, allDescendents=True, type="transform", fullPath=True
            )
            if roots
            else None
        )
        return list(dict.fromkeys(objects + (below or [])))

    @staticmethod
    def _export_objects(scope: Optional[Iterable[str]]) -> List[str]:
        """*scope* (the export set; an empty one ships nothing, as every
        export-scoped record reads it), else every mesh in the scene -- the
        nodes it names that exist, in one query."""
        if scope is not None:
            names = [str(node) for node in scope]
            return (cmds.ls(names, long=True) or []) if names else []
        return cmds.ls(type="mesh", noIntermediate=True, long=True) or []

    @staticmethod
    def _node_types(nodes: List[str]) -> Dict[str, str]:
        """``{node: node type}`` for the *nodes* that exist, in one query
        (shading nodes are DG nodes: ``ls`` names them as they were given)."""
        if not nodes:
            return {}
        typed = cmds.ls(nodes, showType=True) or []
        return dict(zip(typed[::2], typed[1::2]))

    @staticmethod
    def _value(mat: str, attr: str) -> Any:
        """``mat.attr``'s value: a compound's tuple, a scalar as itself;
        ``None`` when the attribute cannot be read."""
        try:
            value = cmds.getAttr(f"{mat}.{attr}")
        except (RuntimeError, ValueError, TypeError):
            return None
        if isinstance(value, list) and value and isinstance(value[0], tuple):
            return value[0]
        return value
