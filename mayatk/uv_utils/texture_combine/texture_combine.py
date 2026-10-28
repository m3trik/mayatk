# !/usr/bin/python
# coding=utf-8
"""Several materials into ONE: one atlas, one material, in place -- no duplicated meshes.

Maya adapter over :class:`pythontk.UvCombine` (the plan and the pack) and
:class:`~mayatk.TextureTransfer` (the texel remap). The selected meshes keep
their geometry; what changes is their texture UV set, repacked so every source
material's islands share one 0-1 layout, and the material they wear -- one
copy of the dominant source material, wired to maps resampled from every
source into that layout.

The run, per :meth:`TextureCombine.combine`:

1. **Gather** -- per unique shape (instances collapse; every instance still
   receives the result) its texture UV set (the set the maps are ``uvLink``-ed
   to), its UV islands, the material each face wears and each island's world
   area (:meth:`TextureCombine.gather`).
2. **Plan + pack** -- :class:`pythontk.UvCombine`: islands of one material that
   overlap (stacked copies, mirrored halves) move as one rigid piece, sized by
   world texel density or by each material's own proportions, times the
   overrides the table sets; then packed into one square atlas.
3. **Write** -- the original layout is copied to a transient UV set, the packed
   one written into the texture set through the undoable per-shell
   ``polyEditUV`` write-back the UV pack uses, and
   :meth:`TextureTransfer.transfer` reads every source map through the
   original layout into the packed one, assigning the result. The transient
   set never outlives the call.
4. **Lightmaps** -- committed lightmaps are bound per object, not per material,
   so the combine never breaks them; but the baker atlases by material, so the
   combined objects' lightmaps are consolidated under the new material
   (:meth:`TextureCombine.consolidate_lightmaps`).
5. **Record** -- ``ptk.SceneRecords.TEXTURE_COMBINE`` on the private
   ``data_internal`` node (never exported): the original UVs and face
   materials, the source maps read (with their file stamps), the settings and
   overrides. Nothing is destroyed: :meth:`TextureCombine.restore` puts the
   originals back exactly, :meth:`TextureCombine.recombine` re-reads revised
   source maps into the SAME layout, :meth:`TextureCombine.update` re-packs
   with new settings.

Why the combined layout goes INTO the texture UV set rather than beside it:
an exporter ships every UV set, and a second one is ``TEXCOORD_1`` -- the
lightmap channel. The original layout lives in the record instead, which is
lossless (Maya stores UVs as float32, and so does the record).
"""

import base64
import os
import shutil
import time
import zlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except Exception:  # pragma: no cover - registry / docs tooling without Maya
    cmds = om = None

import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.node_utils.data_nodes import DataNodes
from mayatk.uv_utils._uv_pack import _UvPackInternal
from mayatk.uv_utils.texture_transfer import TextureTransfer

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


@dataclass
class CombineInputs:
    """What :meth:`TextureCombine.gather` read: the scene side of a combine.

    ``shapes`` maps each unique shape's UUID to its read (``transforms``,
    ``uv_set``, ``uvs``, ``shell_ids``, ``materials`` / ``face_material``,
    ``counts`` / ``uv_ids`` for the fingerprint); ``shells`` are the islands
    as :class:`pythontk.CombineShell` (key ``(uuid, shell id)``), each with
    the faces it covers in ``shell_faces`` and its UV ids in ``shell_points``;
    ``sources`` maps each source material to ``{"maps", "resolution"}``;
    ``clusters`` caches :meth:`pythontk.UvCombine.clusters` (UV-only, so a
    re-plan reuses it).
    """

    transforms: List[str] = field(default_factory=list)
    shapes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    shells: List[Any] = field(default_factory=list)
    shell_faces: List[Any] = field(default_factory=list)
    shell_points: List[Any] = field(default_factory=list)
    sources: Dict[Optional[str], Dict[str, Any]] = field(default_factory=dict)
    clusters: Optional[List[List[int]]] = None


class _TextureCombineInternal:
    """Scene reads, the record codec and the lightmap re-atlas."""

    #: Where the original layout sits while the transfer reads it -- created
    #: and deleted inside one call, never saved.
    SOURCE_UV_SET = "_texture_combine_src"
    #: The record entry's schema.
    SCHEMA = 1

    # ------------------------------------------------------------ meshes
    @staticmethod
    def _fn(transform: str) -> "om.MFnMesh":
        sel = om.MSelectionList()
        sel.add(str(transform))
        dag = sel.getDagPath(0)
        dag.extendToShape()
        return om.MFnMesh(dag)

    @staticmethod
    def resolve_meshes(objects=None) -> List[str]:
        """Mesh transforms (long names) under *objects*, order kept.

        ``None`` reads the selection; groups expand to the meshes beneath them
        and components to their object. EVERY instance of a shape found is
        included, picked or not: the instances share the UVs a combine moves,
        so each must also receive the material made for them.
        """
        if objects is None:
            objects = cmds.ls(selection=True, long=True, objectsOnly=True) or []
        out: List[str] = []
        for obj in ptk.make_iterable(objects):
            for node in cmds.ls(str(obj), long=True, objectsOnly=True) or []:
                if cmds.nodeType(node) == "mesh":
                    node = (
                        cmds.listRelatives(node, parent=True, fullPath=True) or [node]
                    )[0]
                shapes = (
                    cmds.listRelatives(
                        node, allDescendents=True, type="mesh", fullPath=True, ni=True
                    )
                    or []
                )
                for shape in shapes:
                    for xf in (
                        cmds.listRelatives(shape, allParents=True, fullPath=True) or []
                    ):
                        if xf not in out:
                            out.append(xf)
        return out

    @staticmethod
    def _uuid(transform: str) -> str:
        shape = NodeUtils.get_shape(transform, no_intermediate=True, full_path=True)
        shape = shape[0] if isinstance(shape, (list, tuple)) else shape
        return (cmds.ls(str(shape), uuid=True) or [str(shape)])[0]

    @classmethod
    def _read_shape(cls, transform: str) -> Dict[str, Any]:
        """One shape's texture layout, islands, face materials and areas."""
        uv_set = TextureTransfer.auto_source_uv_set(transform)
        fn = cls._fn(transform)
        us, vs = fn.getUVs(uv_set)
        if not len(us):
            raise ValueError(f"{CoreUtils.leaf_name(transform)}: no UVs in {uv_set!r}")
        uvs = np.column_stack([np.asarray(us), np.asarray(vs)]).astype(np.float64)
        counts, vids = fn.getVertices()
        counts = np.asarray(counts, dtype=np.int64)
        vids = np.asarray(vids, dtype=np.int64)
        corner_uv = TextureTransfer._face_vertex_uv_ids(fn, uv_set)
        _, shell_ids = fn.getUvShellsIds(uv_set)
        shell_ids = np.asarray(shell_ids, dtype=np.int64)
        # Fan triangles per face, over corners -- vertices and UVs alike.
        starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
        ntri = np.maximum(counts - 2, 0)
        face_of_tri = np.repeat(np.arange(len(counts)), ntri)
        k = np.arange(int(ntri.sum())) - np.repeat(np.cumsum(ntri) - ntri, ntri) + 1
        c0 = starts[face_of_tri]
        corners = np.stack([c0, c0 + k, c0 + k + 1], axis=1)
        tri_uv = corner_uv[corners]
        points = np.asarray(fn.getPoints(om.MSpace.kWorld))[:, :3]
        p = points[vids[corners]]
        tri_area = 0.5 * np.linalg.norm(
            np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1
        )
        mapped = (tri_uv >= 0).all(axis=1)
        names, face_mat = TextureTransfer.face_materials(transform)
        uc, uids = fn.getAssignedUVs(uv_set)
        return {
            "uv_set": uv_set,
            "uvs": uvs,
            "shell_ids": shell_ids,
            "tri_uv": tri_uv[mapped],
            "tri_face": face_of_tri[mapped],
            "tri_area": tri_area[mapped],
            "materials": list(names),
            "face_material": np.asarray(face_mat, dtype=np.int64),
            "faces": int(len(counts)),
            "uv_counts": np.asarray(uc, dtype=np.int32),
            "uv_ids": np.asarray(uids, dtype=np.int32),
        }

    @staticmethod
    def _fingerprint(read: Dict[str, Any]) -> List[int]:
        """``[faces, uvs, crc of the face-vertex UV ids]`` -- what must still
        hold for a recorded layout to describe the mesh."""
        crc = zlib.crc32(read["uv_counts"].tobytes())
        crc = zlib.crc32(read["uv_ids"].tobytes(), crc)
        return [int(read["faces"]), int(len(read["uvs"])), int(crc)]

    @staticmethod
    def _material_maps(material: Optional[str]) -> Dict[str, Any]:
        """``{"maps", "resolution"}`` of a source material."""
        if not material:
            return {"maps": {}, "resolution": 0}
        # Maya's spelling (forward slashes): the record keys files by path.
        maps = {
            ch: str(p).replace("\\", "/")
            for ch, p in (TextureTransfer.material_maps(material) or {}).items()
            if p
        }
        sizes = [ptk.ImgUtils.get_image_size(p) for p in maps.values() if p]
        widths = [max(s) for s in sizes if s]
        return {"maps": dict(maps), "resolution": max(widths) if widths else 0}

    # ------------------------------------------------------------ record
    @staticmethod
    def _encode(array) -> str:
        return base64.b64encode(
            zlib.compress(np.ascontiguousarray(array).tobytes(), 6)
        ).decode("ascii")

    @staticmethod
    def _decode(text: str, dtype) -> "np.ndarray":
        return np.frombuffer(
            zlib.decompress(base64.b64decode(text)), dtype=dtype
        ).copy()

    @classmethod
    def _load(cls) -> Dict[str, Dict[str, Any]]:
        data = ptk.SceneRecords.TEXTURE_COMBINE.load(DataNodes, {})
        return {
            name: entry
            for name, entry in (data or {}).items()
            if isinstance(entry, dict) and int(entry.get("schema", 0)) <= cls.SCHEMA
        }

    @staticmethod
    def _save(data: Dict[str, Dict[str, Any]]) -> None:
        ptk.SceneRecords.TEXTURE_COMBINE.save(DataNodes, data)

    @staticmethod
    def _stamp(path: str) -> Optional[List[float]]:
        try:
            st = os.stat(path)
        except OSError:
            return None
        return [round(st.st_mtime, 3), int(st.st_size)]

    @staticmethod
    def _resolve_entry_shape(uuid: str, mesh: Dict[str, Any]) -> Optional[str]:
        """A recorded mesh's first live transform: by UUID, else by path."""
        nodes = cmds.ls(uuid, long=True) or []
        if nodes:
            parents = cmds.listRelatives(nodes[0], allParents=True, fullPath=True) or []
            if parents:
                return parents[0]
        for xf in mesh.get("transforms") or []:
            if cmds.objExists(xf):
                return xf
        return None

    # ------------------------------------------------------------ UV sets
    @classmethod
    def _drop_source_set(cls, transform: str) -> None:
        sets = cmds.polyUVSet(transform, query=True, allUVSets=True) or []
        if cls.SOURCE_UV_SET in sets:
            cmds.polyUVSet(transform, delete=True, uvSet=cls.SOURCE_UV_SET)

    @classmethod
    def _stage_source_set(cls, transform: str, uv_set: str) -> None:
        """Copy *uv_set* to the transient source set (a leftover is replaced)."""
        cls._drop_source_set(transform)
        cmds.polyUVSet(transform, copy=True, uvSet=uv_set, newUVSet=cls.SOURCE_UV_SET)
        cmds.polyUVSet(transform, currentUVSet=True, uvSet=uv_set)

    @staticmethod
    def _map_key(map_name: Any) -> str:
        """A lightmap's identity: its lower-case file name (how markers,
        claims and folder records key a map)."""
        return os.path.basename(str(map_name or "")).lower()

    @staticmethod
    def _move_layout(transform: str, uv_set: str, old, new, shell_ids) -> None:
        """Move *uv_set* from *old* to *new* coordinates, shell by shell
        (undoable ``polyEditUV``); a shell that is not a rigid move of its old
        self is written point by point."""
        cmds.polyUVSet(transform, currentUVSet=True, uvSet=uv_set)
        try:
            _UvPackInternal._apply_shell_transforms(transform, old, new, shell_ids)
        except RuntimeError:
            for i in np.flatnonzero(np.abs(new - old).max(axis=1) > 1e-7):
                cmds.polyEditUV(
                    f"{transform}.map[{int(i)}]",
                    uValue=float(new[i, 0]),
                    vValue=float(new[i, 1]),
                    relative=False,
                )

    # ------------------------------------------------------------ lightmaps
    @staticmethod
    def _lightmap_crop(
        info: Dict[str, Any], transform: str, staging
    ) -> Optional[Tuple[str, tuple]]:
        """*transform*'s cell cut out of its atlas, pixel-exact, as
        ``(path, region)`` -- the region in the object's own lightmap layout
        that the crop covers (what :meth:`LightmapBaker.pack_atlas` reads a
        partial map in)."""
        from mayatk.core_utils.diagnostics.uv_diag import UvDiagnostics

        path = info.get("path")
        image = ptk.ImgUtils.cv2_read(path, -1) if path else None  # IMREAD_UNCHANGED
        if image is None:
            return None
        shape = NodeUtils.get_shape(transform, no_intermediate=True, full_path=True)
        shape = shape[0] if isinstance(shape, (list, tuple)) else shape
        uv_set = info.get("uv_set") or UvDiagnostics.find_lightmap_uv_set(shape)
        # A lightmap set removed since the bake: no cell to cut (getUVs raises).
        if not uv_set or uv_set not in (
            cmds.polyUVSet(shape, query=True, allUVSets=True) or []
        ):
            return None
        us, vs = _TextureCombineInternal._fn(transform).getUVs(uv_set)
        if not len(us):
            return None
        sx, sy, ox, oy = [float(v) for v in info.get("scaleOffset") or (1, 1, 0, 0)]
        h, w = image.shape[:2]
        pad = 2.0  # texels of gutter kept around the island
        u0, u1 = min(us) * sx + ox, max(us) * sx + ox
        v0, v1 = min(vs) * sy + oy, max(vs) * sy + oy
        col0 = max(0, int(np.floor(u0 * w - pad)))
        col1 = min(w, int(np.ceil(u1 * w + pad)))
        row0 = max(0, int(np.floor((1.0 - v1) * h - pad)))  # row 0 is V = 1
        row1 = min(h, int(np.ceil((1.0 - v0) * h + pad)))
        if col1 <= col0 or row1 <= row0:
            return None
        crop = image[row0:row1, col0:col1]
        out = staging.path(extension=os.path.splitext(path)[1] or ".exr")
        if not ptk.ImgUtils.cv2_write(out, crop):
            return None
        # The crop's window back in the object's layout frame.
        region = (
            (col0 / w - ox) / sx,
            ((1.0 - row1 / h) - oy) / sy,
            (col1 / w - ox) / sx,
            ((1.0 - row0 / h) - oy) / sy,
        )
        return out, region


class TextureCombine(ptk.LoggingMixin, _TextureCombineInternal):
    """Combine the selected meshes' materials into one atlas and one material.

    Every action is undoable as one step in the scene (the written maps stay
    on disk) and recorded, so it can be restored or re-combined later.
    """

    SIZING = ptk.UvCombine.SIZING
    NORMALIZE = ptk.UvCombine.NORMALIZE
    #: Override row kinds, most specific last (:meth:`apply_rows`).
    ROW_KINDS = ("material", "mesh", "faces")

    def __init__(self, log_level="INFO"):
        super().__init__()
        self.logger.setLevel(log_level)

    # ------------------------------------------------------------ queries
    @classmethod
    def names(cls) -> List[str]:
        """The combines this scene holds, oldest first."""
        data = cls._load()
        return sorted(data, key=lambda n: data[n].get("created", ""))

    @classmethod
    def entry(cls, name: str) -> Optional[Dict[str, Any]]:
        """A combine's record entry (settings, rows, meshes, sources), or ``None``."""
        return cls._load().get(name)

    @classmethod
    def members(cls, name: str) -> List[str]:
        """The live transforms a combine moved (every instance)."""
        entry = cls.entry(name) or {}
        out: List[str] = []
        for uuid, mesh in (entry.get("meshes") or {}).items():
            first = cls._resolve_entry_shape(uuid, mesh)
            if first is None:
                continue
            shape = NodeUtils.get_shape(first, no_intermediate=True, full_path=True)
            shape = shape[0] if isinstance(shape, (list, tuple)) else shape
            for xf in cmds.listRelatives(shape, allParents=True, fullPath=True) or []:
                if xf not in out:
                    out.append(xf)
        return out

    @classmethod
    def owner(cls, objects) -> Optional[str]:
        """The combine any of *objects* belongs to, or ``None``."""
        uuids = {cls._uuid(t) for t in cls.resolve_meshes(objects)}
        for name, entry in cls._load().items():
            if uuids & set((entry.get("meshes") or {}).keys()):
                return name
        return None

    @classmethod
    def stale(cls, name: str) -> List[str]:
        """The source maps of *name* revised (or gone) since it was combined."""
        entry = cls.entry(name) or {}
        out = []
        for source in (entry.get("sources") or {}).values():
            for path, stamp in (source.get("stamps") or {}).items():
                if cls._stamp(path) != (list(stamp) if stamp else None):
                    out.append(path)
        return sorted(set(out))

    # ------------------------------------------------------------ gather + plan
    def gather(self, objects=None) -> CombineInputs:
        """Read the islands, materials and maps of *objects* (default: selection).

        Raises:
            ValueError: No meshes, or meshes whose texture UV sets are named
                differently (rename them to match -- the transfer reads one
                set name across the run).
        """
        if np is None:
            raise RuntimeError("numpy is required")
        transforms = self.resolve_meshes(objects)
        if not transforms:
            raise ValueError(
                "no meshes to combine -- select the meshes (or their group)"
            )
        inputs = CombineInputs(transforms=transforms)
        for transform in transforms:
            uuid = self._uuid(transform)
            if uuid in inputs.shapes:
                inputs.shapes[uuid]["transforms"].append(transform)
                continue
            read = self._read_shape(transform)
            read["transforms"] = [transform]
            inputs.shapes[uuid] = read
        sets = {r["uv_set"] for r in inputs.shapes.values()}
        if len(sets) > 1:
            raise ValueError(
                "the meshes' texture UV sets are named differently "
                f"({', '.join(sorted(sets))}) -- rename them to one name first"
            )
        self._build_shells(inputs)
        for shell in inputs.shells:
            if shell.group not in inputs.sources:
                inputs.sources[shell.group] = self._material_maps(shell.group)
        return inputs

    def inputs_for(self, name: str) -> CombineInputs:
        """A stored combine's inputs AS THEY WERE before it ran.

        The live meshes' topology with the recorded original UVs and face
        materials laid back over it, and the recorded source maps -- what a
        panel re-plans an update against, and what :meth:`update` combines
        (islands are UV-connected sets, which a rigid repack never changes).
        """
        entry, live = self._recorded(name)
        if not live:
            raise ValueError(f"{name}: none of its meshes are in the scene unchanged")
        inputs = CombineInputs()
        for uuid, (first, mesh, read) in live.items():
            shape = NodeUtils.get_shape(first, no_intermediate=True, full_path=True)
            shape = shape[0] if isinstance(shape, (list, tuple)) else shape
            read["transforms"] = cmds.listRelatives(
                shape, allParents=True, fullPath=True
            ) or [first]
            read["uvs"] = (
                self._decode(mesh["uvs"], np.float32).reshape(-1, 2).astype(np.float64)
            )
            read["materials"] = list(mesh.get("materials") or [])
            read["face_material"] = self._decode(
                mesh["face_material"], np.int32
            ).astype(np.int64)
            inputs.shapes[uuid] = read
            inputs.transforms.extend(read["transforms"])
        self._build_shells(inputs)
        recorded = entry.get("sources") or {}
        for shell in inputs.shells:
            if shell.group in inputs.sources:
                continue
            src = recorded.get(shell.group)
            inputs.sources[shell.group] = (
                {
                    "maps": dict(src.get("maps") or {}),
                    "resolution": int(src.get("resolution") or 0),
                }
                if src
                else self._material_maps(shell.group)
            )
        return inputs

    @staticmethod
    def _build_shells(inputs: CombineInputs) -> None:
        """One :class:`pythontk.CombineShell` per UV island of every read
        (:meth:`pythontk.UvCombine.island_shells`)."""
        for uuid, read in inputs.shapes.items():
            shells, faces, points = ptk.UvCombine.island_shells(
                uuid,
                read["uvs"],
                read["shell_ids"],
                read["tri_uv"],
                read["tri_face"],
                read["tri_area"],
                read["materials"],
                read["face_material"],
            )
            inputs.shells.extend(shells)
            inputs.shell_faces.extend(faces)
            inputs.shell_points.extend(points)

    def apply_rows(
        self, inputs: CombineInputs, rows: Sequence[Dict[str, Any]] = ()
    ) -> None:
        """Set every island's scale from the override *rows* -- the most
        specific row wins: a ``"faces"`` row (the islands those faces lie in)
        over a ``"mesh"`` row (every island of that mesh, any instance named)
        over a ``"material"`` row (every island reading it).

        A row is ``{"kind": one of ROW_KINDS, "target": str | [str], "scale":
        float}``; faces are component strings (``|path|mesh.f[0:12]``).
        """
        by_material, by_shape, by_faces = {}, {}, {}
        for row in rows or ():
            kind, scale = row.get("kind"), float(row.get("scale", 1.0) or 1.0)
            targets = ptk.make_iterable(row.get("target"))
            if kind == "material":
                for t in targets:
                    by_material[t] = scale
            elif kind == "mesh":
                for t in self.resolve_meshes(targets):
                    by_shape[self._uuid(t)] = scale
            elif kind == "faces":
                for comp in cmds.ls(list(targets), flatten=False, long=True) or []:
                    node = comp.split(".")[0]
                    uuid = self._uuid(node)
                    ids = by_faces.setdefault(uuid, {})
                    for f in self._face_indices(comp):
                        ids[f] = scale
        for shell, faces in zip(inputs.shells, inputs.shell_faces):
            uuid = shell.key[0]
            scale = by_material.get(shell.group, 1.0)
            scale = by_shape.get(uuid, scale)
            picked = by_faces.get(uuid)
            if picked:
                hits = [picked[f] for f in faces.tolist() if f in picked]
                if hits:
                    scale = max(hits)
            shell.scale = scale

    @staticmethod
    def _face_indices(component: str) -> List[int]:
        """Face indices a component string names (``.f[3]``, ``.f[0:9]``)."""
        out: List[int] = []
        for comp in cmds.ls(component, flatten=True) or []:
            if ".f[" not in comp:
                continue
            try:
                out.append(int(comp.rsplit("[", 1)[1].rstrip("]")))
            except ValueError:
                continue
        return out

    def plan(
        self,
        inputs: CombineInputs,
        *,
        sizing: str = "proportional",
        normalize: str = "resolution",
    ):
        """Estimate the atlas for *inputs* (nothing packed) -- a
        :class:`pythontk.CombinePlan`; cheap after the first call, which
        caches the islands' clusters on *inputs*."""
        if inputs.clusters is None:
            inputs.clusters = ptk.UvCombine.clusters(inputs.shells)
        return ptk.UvCombine.plan(
            inputs.shells,
            sizing=sizing,
            normalize=normalize,
            resolutions={g: s["resolution"] for g, s in inputs.sources.items()},
            clusters=inputs.clusters,
        )

    # ------------------------------------------------------------ actions
    def default_name(
        self, transforms: Sequence[str], fallback: str = "combined"
    ) -> str:
        """What a blank name stands for: the meshes' common name, else
        *fallback* (the Texture Combine panel's Transfer mode names its maps
        after its source the same way, falling back to ``"transfer"``).
        Twin of blendertk's."""
        name = ptk.StrUtils.common_name(
            list(transforms), strip=ptk.NamingConvention.all_affixes()
        )
        return name or fallback

    def combine(
        self,
        objects=None,
        *,
        name: Optional[str] = None,
        rows: Sequence[Dict[str, Any]] = (),
        sizing: str = "proportional",
        normalize: str = "resolution",
        map_size: int = 4096,
        padding: Optional[int] = None,
        rotate: bool = True,
        supersample: int = 2,
        texture_padding: int = -1,
        output_dir: Optional[str] = None,
        assign_prefix: str = "",
        assign_suffix: Optional[str] = None,
        assign_shader_type: Optional[str] = None,
        lightmaps: bool = True,
        inputs: Optional[CombineInputs] = None,
    ) -> Dict[str, Any]:
        """Combine *objects*' materials into one atlas and one material.

        Parameters:
            objects: Meshes or groups; ``None`` uses the selection.
            name: The combine's name -- its record key, its maps'
                (``<name>_<Channel>.png``) and its material's core. Blank:
                :meth:`default_name`.
            rows: Scale overrides (:meth:`apply_rows`).
            sizing / normalize: See :class:`pythontk.UvCombine`.
            map_size: The atlas size.
            padding: Gutter between islands in texels; default
                ``ptk.MathUtils.calculate_uv_padding(map_size)``.
            rotate: Let the packer turn islands where that packs tighter.
            supersample / texture_padding / output_dir / assign_*: As
                :meth:`TextureTransfer.transfer` (*texture_padding* is its
                ``padding``: the maps' gutter fill, ``-1`` fills every empty
                texel).
            lightmaps: Consolidate the combined objects' committed lightmaps
                (:meth:`consolidate_lightmaps`).
            inputs: A :meth:`gather` already made for *objects* (a panel that
                showed the plan passes it; its rows are re-applied).

        Returns:
            ``{"name", "material", "maps": {channel: path}, "meshes": n,
            "groups": {material: CombineGroup}, "utilization", "lightmaps"}``.

        Raises:
            ValueError: Nothing to combine, a name another combine holds, or a
                mesh already in another combine (restore that one first).
        """
        inputs = inputs or self.gather(objects)
        self.apply_rows(inputs, rows)
        name = (name or "").strip() or self.default_name(inputs.transforms)
        data = self._load()
        if name in data:
            raise ValueError(
                f"a combine named {name!r} exists -- update or restore it, or "
                "pick another name"
            )
        for other, entry in data.items():
            taken = set(entry.get("meshes") or {}) & set(inputs.shapes)
            if taken:
                raise ValueError(
                    f"{len(taken)} of these meshes are already in the combine "
                    f"{other!r} -- restore it first"
                )
        if not any(s["maps"] for s in inputs.sources.values()):
            raise ValueError("no source material carries a texture map to combine")

        plan = self.plan(inputs, sizing=sizing, normalize=normalize)
        # Recorded as asked: Auto (None) re-derives from the atlas size on an
        # update, where the resolved count would keep the old atlas's gutter.
        spacing = None if padding is None else int(padding)
        padding = (
            int(round(ptk.MathUtils.calculate_uv_padding(map_size)))
            if spacing is None
            else spacing
        )
        layout = ptk.UvCombine.pack(
            inputs.shells,
            plan,
            map_size=int(map_size),
            padding=padding,
            margin=ptk.MathUtils.uv_tile_margin(map_size),
            rotate=rotate,
        )
        settings = {
            "sizing": sizing,
            "normalize": normalize,
            "map_size": int(map_size),
            "padding": spacing,
            "rotate": bool(rotate),
            "supersample": int(supersample),
            "texture_padding": int(texture_padding),
            "output_dir": output_dir or "",
            "assign_prefix": assign_prefix or "",
            "assign_suffix": assign_suffix,
            "assign_shader_type": assign_shader_type,
            "lightmaps": bool(lightmaps),
        }
        # Per shape, the packed coordinates: the original ones moved island by
        # island (a UV no face references stays where it is).
        packed: Dict[str, Any] = {
            uuid: read["uvs"].copy() for uuid, read in inputs.shapes.items()
        }
        for shell, points, new in zip(inputs.shells, inputs.shell_points, layout.uvs):
            packed[shell.key[0]][points] = new

        with CoreUtils.undo_chunk(f"Texture Combine: {name}"):
            maps, material = self._write_and_transfer(
                inputs,
                packed,
                name=name,
                settings=settings,
                source_materials=None,
            )
            entry = self._entry(inputs, name, settings, rows, material, maps)
            entry["groups"] = {
                str(g): {
                    "share": round(stats.share, 5),
                    "scale": round(stats.scale, 6),
                    "shells": stats.shells,
                    "stacks": stats.stacks,
                }
                for g, stats in layout.plan.groups.items()
            }
            entry["utilization"] = round(layout.utilization, 4)
            data[name] = entry
            self._save(data)
            lm = (
                self.consolidate_lightmaps(inputs.transforms, name) if lightmaps else {}
            )
        return {
            "name": name,
            "material": material,
            "maps": maps,
            "meshes": len(inputs.transforms),
            "groups": layout.plan.groups,
            "utilization": layout.utilization,
            "map_size": int(map_size),
            "lightmaps": lm,
        }

    def _write_and_transfer(
        self,
        inputs: CombineInputs,
        packed: Dict[str, Any],
        *,
        name: str,
        settings: Dict[str, Any],
        source_materials: Optional[Dict[str, Tuple[List, Any]]],
        original: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, str], Optional[str]]:
        """Stage the source layout, write the packed one, transfer, clean up.

        *original* (per shape) is the layout the maps were painted for when it
        is NOT what the texture set holds now (a re-combine: the set already
        holds the packed layout); ``None`` -- the set holds it.
        """
        staged: List[Tuple[str, Dict[str, Any]]] = []
        moved: List[Tuple[str, Dict[str, Any]]] = []
        try:
            for uuid, read in inputs.shapes.items():
                first = read["transforms"][0]
                self._stage_source_set(first, read["uv_set"])
                staged.append((first, read))
                if original is not None:
                    # The source set is a copy of the PACKED layout here: move
                    # it back onto the layout the maps were painted for.
                    self._move_layout(
                        first,
                        self.SOURCE_UV_SET,
                        read["uvs"],
                        original[uuid],
                        read["shell_ids"],
                    )
                    cmds.polyUVSet(first, currentUVSet=True, uvSet=read["uv_set"])
                if not np.array_equal(packed[uuid], read["uvs"]):
                    self._move_layout(
                        first,
                        read["uv_set"],
                        read["uvs"],
                        packed[uuid],
                        read["shell_ids"],
                    )
                    moved.append((first, read))
            uv_set = next(iter(inputs.shapes.values()))["uv_set"]
            results = TextureTransfer(log_level=self.logger.level).transfer(
                inputs.transforms,
                None,
                source_uv_set=self.SOURCE_UV_SET,
                target_uv_set=uv_set,
                size=settings["map_size"],
                supersample=settings["supersample"],
                padding=settings["texture_padding"],
                output_dir=settings["output_dir"] or None,
                output_name=name,
                assign=True,
                assign_prefix=settings["assign_prefix"],
                assign_suffix=settings["assign_suffix"],
                assign_shader_type=settings["assign_shader_type"],
                assign_from="source",
                source_materials=source_materials,
            )
        except BaseException:
            # Put every moved layout back before the error leaves: a failed
            # transfer must not leave the scene on a layout no map was made for.
            for first, read in moved:
                try:
                    current = self._fn(first).getUVs(read["uv_set"])
                    now = np.column_stack(
                        [np.asarray(current[0]), np.asarray(current[1])]
                    )
                    self._move_layout(
                        first, read["uv_set"], now, read["uvs"], read["shell_ids"]
                    )
                except Exception as error:  # noqa: BLE001 - best effort, reported
                    self.logger.error(f"Could not restore {first}'s UVs: {error}")
            raise
        finally:
            for first, read in staged:
                self._drop_source_set(first)
                cmds.polyUVSet(first, currentUVSet=True, uvSet=read["uv_set"])
        maps = next(iter(results.values()), {}) if results else {}
        material = None
        first = inputs.transforms[0]
        try:
            names, _ = TextureTransfer.face_materials(first)
            material = names[0] if names else None
        except ValueError:
            pass
        return dict(maps), material

    def _entry(self, inputs, name, settings, rows, material, maps) -> Dict[str, Any]:
        meshes = {}
        for uuid, read in inputs.shapes.items():
            meshes[uuid] = {
                "transforms": list(read["transforms"]),
                "uv_set": read["uv_set"],
                "fingerprint": self._fingerprint(read),
                "uvs": self._encode(read["uvs"].astype(np.float32)),
                "materials": list(read["materials"]),
                "face_material": self._encode(read["face_material"].astype(np.int32)),
            }
        sources = {}
        for mat, src in inputs.sources.items():
            if not mat:
                continue
            sources[mat] = {
                "maps": dict(src["maps"]),
                "resolution": int(src["resolution"]),
                "stamps": {p: self._stamp(p) for p in src["maps"].values() if p},
            }
        return {
            "schema": self.SCHEMA,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "material": material,
            "maps": dict(maps),
            "settings": dict(settings),
            "rows": [dict(r) for r in rows or ()],
            "meshes": meshes,
            "sources": sources,
        }

    # ------------------------------------------------------------ revisions
    def _recorded(self, name: str):
        """``(entry, live)`` -- *live* ``{uuid: (first transform, mesh, read)}``
        for every recorded mesh that still matches its fingerprint."""
        entry = self.entry(name)
        if entry is None:
            raise ValueError(f"no combine named {name!r} in this scene")
        live = {}
        for uuid, mesh in (entry.get("meshes") or {}).items():
            first = self._resolve_entry_shape(uuid, mesh)
            if first is None:
                self.logger.warning(f"{name}: a recorded mesh is gone; skipped.")
                continue
            read = self._read_shape(first)
            if self._fingerprint(read) != list(mesh.get("fingerprint") or []):
                self.logger.warning(
                    f"{name}: {CoreUtils.leaf_name(first)}'s topology changed "
                    "since the combine; skipped."
                )
                continue
            live[uuid] = (first, mesh, read)
        return entry, live

    def recombine(self, name: str) -> Dict[str, Any]:
        """Re-read *name*'s source maps into its CURRENT layout -- a revision.

        The layout stays exactly where it is (no re-pack): each face is read
        through the material it wore and the layout its maps were painted for,
        both from the record. The combined material and maps are replaced in
        place, and the source stamps refreshed.
        """
        entry, live = self._recorded(name)
        if not live:
            raise ValueError(f"{name}: none of its meshes can be re-combined")
        inputs = CombineInputs()
        original, given = {}, {}
        for uuid, (first, mesh, read) in live.items():
            shape = NodeUtils.get_shape(first, no_intermediate=True, full_path=True)
            shape = shape[0] if isinstance(shape, (list, tuple)) else shape
            read["transforms"] = cmds.listRelatives(
                shape, allParents=True, fullPath=True
            ) or [first]
            inputs.shapes[uuid] = read
            inputs.transforms.extend(read["transforms"])
            original[uuid] = (
                self._decode(mesh["uvs"], np.float32).reshape(-1, 2).astype(np.float64)
            )
            face_mat = self._decode(mesh["face_material"], np.int32)
            for xf in read["transforms"]:
                given[xf] = (list(mesh["materials"]), face_mat)
        settings = dict(entry.get("settings") or {})
        with CoreUtils.undo_chunk(f"Re-combine: {name}"):
            maps, material = self._write_and_transfer(
                inputs,
                {uuid: r["uvs"] for uuid, r in inputs.shapes.items()},
                name=name,
                settings=settings,
                source_materials=given,
                original=original,
            )
            data = self._load()
            stored = data.get(name, entry)
            for mat, src in (stored.get("sources") or {}).items():
                src["stamps"] = {
                    p: self._stamp(p) for p in (src.get("maps") or {}).values() if p
                }
            stored["maps"] = dict(maps)
            stored["material"] = material or stored.get("material")
            data[name] = stored
            self._save(data)
        return {
            "name": name,
            "material": material,
            "maps": maps,
            "meshes": len(inputs.transforms),
        }

    def restore(self, name: str, forget: bool = True) -> List[str]:
        """Put *name*'s meshes back: original UVs and materials, exactly.

        The combined material and maps are left in the scene and on disk, the
        consolidated lightmaps bound as they are (lighting does not depend on
        the material). With *forget* the record entry goes too.

        Returns:
            The transforms restored.
        """
        entry, live = self._recorded(name)
        restored: List[str] = []
        with CoreUtils.undo_chunk(f"Restore Combine: {name}"):
            for uuid, (first, mesh, read) in live.items():
                original = (
                    self._decode(mesh["uvs"], np.float32)
                    .reshape(-1, 2)
                    .astype(np.float64)
                )
                if len(original) != len(read["uvs"]):
                    self.logger.warning(f"{first}: UV count changed; skipped.")
                    continue
                self._move_layout(
                    first, read["uv_set"], read["uvs"], original, read["shell_ids"]
                )
                shape = NodeUtils.get_shape(first, no_intermediate=True, full_path=True)
                shape = shape[0] if isinstance(shape, (list, tuple)) else shape
                transforms = cmds.listRelatives(
                    shape, allParents=True, fullPath=True
                ) or [first]
                face_mat = self._decode(mesh["face_material"], np.int32)
                for index, material in enumerate(mesh.get("materials") or []):
                    faces = np.flatnonzero(face_mat == index)
                    if not len(faces) or not cmds.objExists(material):
                        if len(faces):
                            self.logger.warning(
                                f"{material} is gone; {CoreUtils.leaf_name(first)} keeps "
                                "the combined material on its faces."
                            )
                        continue
                    sgs = cmds.listConnections(material, type="shadingEngine") or []
                    if not sgs:
                        continue
                    for xf in transforms:
                        cmds.sets(
                            _UvPackInternal._component_ranges(
                                xf, np.sort(faces), kind="f"
                            ),
                            edit=True,
                            forceElement=sgs[0],
                        )
                restored.extend(transforms)
            if forget:
                data = self._load()
                data.pop(name, None)
                self._save(data)
        return restored

    def update(self, name: str, **changes) -> Dict[str, Any]:
        """Re-pack *name* with changed settings or rows, keeping its name.

        Restores the originals, then combines the same meshes again with the
        recorded settings overlaid by *changes* (any :meth:`combine` keyword).
        One undo step: Ctrl+Z returns the combine as it was, not the
        restored originals.
        """
        entry = self.entry(name)
        if entry is None:
            raise ValueError(f"no combine named {name!r} in this scene")
        settings = dict(entry.get("settings") or {})
        settings["rows"] = entry.get("rows") or []
        settings.update(changes)
        objects = settings.pop("objects", None)
        with CoreUtils.undo_chunk(f"Update Combine: {name}"):
            members = self.restore(name, forget=True)
            return self.combine(objects or members, name=name, **settings)

    @classmethod
    def forget(cls, name: str) -> bool:
        """Drop *name*'s record entry, leaving the scene as it is."""
        data = cls._load()
        if data.pop(name, None) is None:
            return False
        cls._save(data)
        return True

    # ------------------------------------------------------------ selection
    # What the Texture Combine panel (tentacle's shared ``slots/tools/
    # texture_combine.py``) sets and reads beside the combine itself: rows of
    # its Sources table name meshes and face components, in Maya's spelling.

    @staticmethod
    def leaf_name(node) -> str:
        """*node*'s own name, for a label: no DAG path, no component
        (``|grp|pCube1.f[0:3]`` -> ``pCube1``). Twin of blendertk's."""
        return str(node).split(".")[0].rsplit("|", 1)[-1]

    @staticmethod
    def select(nodes) -> int:
        """Replace the selection with those of *nodes* that exist --
        transforms and/or face components (``<mesh>.f[a:b]``, the spelling
        :meth:`material_faces` and an island override row use). With none
        that exists, the selection stays as it is. Twin of blendertk's
        (objects there, faces in Edit Mode).

        Returns:
            How many of *nodes* were selected.
        """
        found = [n for n in ptk.make_iterable(nodes) if n and cmds.ls(n)]
        if found:
            cmds.select(found, replace=True)
        return len(found)

    @staticmethod
    def material_faces(inputs: CombineInputs, material) -> List[str]:
        """The faces of *inputs*' meshes that wear *material*, as
        ``<mesh>.f[a:b]`` strings on every instance -- what a Sources table
        row covers. Twin of blendertk's."""
        faces: List[str] = []
        for read in inputs.shapes.values():
            try:
                index = read["materials"].index(material)
            except ValueError:
                continue
            ids = [
                i for i, m in enumerate(read["face_material"].tolist()) if m == index
            ]
            for xf in read["transforms"]:
                faces += _UvPackInternal._component_ranges(xf, sorted(ids), kind="f")
        return faces

    @staticmethod
    def selected_faces() -> List[str]:
        """The selected faces (long ``<mesh>.f[a:b]`` strings), for an island
        override row: the faces picked, else the selection converted to faces
        (UVs, edges, vertices -- or a whole mesh); ``[]`` when that yields
        none. Twin of blendertk's (the faces selected in Edit Mode)."""
        selection = cmds.ls(selection=True, long=True) or []
        faces = cmds.filterExpand(selection, selectionMask=34) or []
        if not faces:
            faces = cmds.polyListComponentConversion(selection, toFace=True) or []
        faces = cmds.ls(faces, long=True) or []
        return faces if faces and "." in faces[0] else []

    # ------------------------------------------------------------ lightmaps
    def consolidate_lightmaps(
        self, transforms: Sequence[str], name: str
    ) -> Dict[str, Any]:
        """Bring the combined objects' committed lightmaps under the new material.

        A lightmap is bound per object (map + atlas rect), so a combine never
        breaks one; but the baker atlases BY MATERIAL, and the combined objects
        now share one. So:

        * the objects own their one atlas outright -- it becomes
          ``<name>_Lightmap`` (a lossless copy, re-committed);
        * they read several atlases, or share one with other objects -- their
          cells are cut out pixel-exact and re-packed into one
          ``<name>_Lightmap`` atlas (:meth:`LightmapBaker.pack_atlas`), the
          maps nobody reads any more set aside.

        Either way the old map is set aside only by the re-bake guard
        (:meth:`LightmapRecords.superseding`): written by THIS scene, inside
        its project, and read by nothing here -- never a file another scene
        may still read.

        Objects with no lightmap are listed: the next bake gives them one, in
        this same atlas (it groups by the combined material).

        Returns:
            ``{"how": None | "kept" | "renamed" | "repacked", "map", "objects",
            "unbaked"}``.
        """
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        infos = {t: LightmapRecords.lightmap_info(t) for t in transforms}
        baked = {t: i for t, i in infos.items() if i}
        unbaked = [t for t in transforms if t not in baked]
        result: Dict[str, Any] = {
            "how": None,
            "map": None,
            "objects": sorted(baked),
            "unbaked": unbaked,
        }
        if not baked:
            return result
        stem = ptk.StrUtils.sanitize(name, preserve_case=True)
        maps = {self._map_key(i["map"]): i for i in baked.values()}
        claims = LightmapRecords.claims()
        mine = set(baked)
        if len(maps) == 1:
            key, info = next(iter(maps.items()))
            ext = os.path.splitext(info["map"])[1] or ".exr"
            target = f"{stem}_Lightmap{ext}"
            readers = set(claims.get(key, ()))
            if readers <= mine:
                result["map"] = target
                if self._map_key(target) == key:
                    result["how"] = "kept"
                    return result
                path = info.get("path")
                if not path or not os.path.isfile(path):
                    self.logger.warning(
                        f"{info['map']} is not on disk; lightmaps left as they are."
                    )
                    result["how"] = None
                    return result
                # Named in the map's home folder; written to the session
                # until the scene is saved (LightmapRecords._stage).
                folder = LightmapRecords._home(os.path.dirname(path))
                new_path = os.path.join(folder, target)
                taken = set(claims.get(self._map_key(target), ())) - mine
                # Nor another scene's file of that name, which no claim here
                # names (LightmapRecords.strangers).
                strangers = LightmapRecords.strangers(
                    folder, f"{stem}_Lightmap", ext, claims
                )
                if os.path.exists(new_path) and (taken or strangers):
                    new_path = ptk.FileUtils.unique_path(
                        folder,
                        f"{stem}_Lightmap",
                        ext,
                        claims=claims,
                        owners=mine,
                        avoid=strangers,
                    )
                    target = os.path.basename(new_path)
                    result["map"] = target
                # Copied and re-committed, never moved: another scene file
                # (a Save As copy, a host referencing this module) may read the
                # old name, which this scene's markers cannot see. The re-bake
                # guard sets it aside only when THIS scene wrote it, inside its
                # project, and nothing in the scene reads it any more.
                new_path = LightmapRecords._stage(new_path)
                shutil.copy2(path, new_path)
                with LightmapRecords.superseding(list(baked)):
                    LightmapRecords.commit(
                        {t: new_path for t in baked},
                        scale_offsets={
                            t: i.get("scaleOffset") for t, i in baked.items()
                        },
                        intensity=float(info.get("intensity", 1.0)),
                        written=True,
                    )
                result["how"] = "renamed"
                return result
        return self._repack_lightmaps(baked, stem, result)

    def _repack_lightmaps(self, baked: Dict[str, Dict[str, Any]], stem: str, result):
        """Cut every object's cell out of its atlas and pack them into one."""
        from mayatk.light_utils.lightmap_baker.lightmap_baker import LightmapBaker
        from mayatk.light_utils.lightmap_baker.lightmap_records import LightmapRecords

        located = [i["path"] for i in baked.values() if i.get("path")]
        if not located:
            self.logger.warning("No lightmap is on disk; lightmaps left as they are.")
            return result
        sizes = [ptk.ImgUtils.get_image_size(p) for p in located]
        resolution = max((max(s) for s in sizes if s), default=1024)
        # Into the session, home at the save (LightmapRecords._stage).
        out_dir = LightmapRecords._pending().folder(os.path.dirname(located[0]))
        with ptk.TempArtifacts.scoped("texture_combine_lm") as staging:
            crops, regions = {}, {}
            for transform, info in baked.items():
                cut = self._lightmap_crop(info, transform, staging)
                if cut is None:
                    self.logger.warning(
                        f"{transform}: its lightmap could not be read; left as it is."
                    )
                    continue
                crops[transform], regions[transform] = cut
            if not crops:
                return result
            baker = LightmapBaker(resolution=int(resolution), reflection_probe=False)
            with LightmapRecords.superseding(list(crops)):
                packed = baker.pack_atlas(
                    crops,
                    output_dir=out_dir,
                    keep_sources=True,
                    regions=regions,
                )
                baker.commit_lightmap(
                    {o: p for o, (p, _) in packed.items()},
                    scale_offsets={o: so for o, (_, so) in packed.items()},
                )
        result["how"] = "repacked"
        result["map"] = sorted({os.path.basename(p) for p, _ in packed.values()})
        return result
