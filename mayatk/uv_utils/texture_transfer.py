# !/usr/bin/python
# coding=utf-8
"""Transfer a mesh's textures from one UV layout to another -- no rays, no bake.

Maya adapter over :class:`pythontk.UvTransfer`. The engine does the texel
remap; this module supplies what only the host knows -- the triangle
correspondence between the two layouts (one triangulation, face-vertex UVs on
both sides, so seams and concave faces are handled), which source material
each triangle reads from, the maps (or constants) those materials carry, and
where the results go.

Two forms, one code path:

* **mesh -> mesh** -- a source mesh and a target mesh of identical topology
  (the same model re-unwrapped / re-packed, a material consolidation). Pairing
  is by matching leaf name, else by order. A target COMBINED from several
  sources (Mesh > Combine, then re-unwrapped) reads them all: the sources join
  end to end in the order the combine left them (:meth:`pair_sources`).
* **UV set -> UV set** on ONE mesh (``source=None``, ``source_uv_set=...``).

Outputs are written per target LAYOUT -- the targets' faces grouped by
overlap (:meth:`pythontk.UvTransfer.layout_jobs`), whatever their UV sets are
called or the targets wear -- one image per channel, sampled from whichever
source material each triangle wears (a consolidation reads N source materials
into one atlas; a source that has no map for a channel contributes its
constant). Every map is the same resample. A normal map's XY also turn with
any island the target layout rotates or mirrors
(:meth:`pythontk.UvTransfer.transfer_normals`), read off the two layouts
alone: where a target stands or how it is shaped never enters it, and this is
never a normal-map BAKE -- re-deriving normals from geometry is a ray-cast
baker's job (the Marmoset bridge), a separate operation.

A committed LIGHTMAP is not a material map and travels on its own pass,
:meth:`LightmapRecords.transfer_lightmaps` (built on this module's pairing):
rebound to the same map when the target's lightmap layout is the source's,
resampled into the target's layout otherwise, and committed on the target
either way.

This is deliberately NOT part of the Marmoset bridge: that bridge is a
high->low ray-cast bake. The one thing they share is the diagnosis -- the
bridge warns when its source and target are coincident, because that job
belongs here.
"""

import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except Exception:  # pragma: no cover - registry / docs tooling without Maya
    cmds = om = None

import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.mat_utils.mat_manifest import MatManifest
from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap
from mayatk.mat_utils.shader_converter import ShaderConverter

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


class _TextureTransferInternal:
    """Host-side helpers: correspondence, material lookup, IO."""

    # ------------------------------------------------------------ meshes
    @staticmethod
    def _mesh_fn(obj) -> "om.MFnMesh":
        shape = NodeUtils.get_shape(obj, no_intermediate=True, full_path=True)
        if not shape:
            raise ValueError(f"{obj!r} has no mesh shape")
        sel = om.MSelectionList()
        sel.add(str(shape))
        return om.MFnMesh(sel.getDagPath(0))

    @staticmethod
    def _face_vertex_uv_ids(mesh: "om.MFnMesh", uv_set: str) -> "np.ndarray":
        """``uv id`` per face-vertex slot (``-1`` where the face has no UVs)."""
        vc, _ = mesh.getVertices()
        uc, uids = mesh.getAssignedUVs(uv_set)
        vc = np.asarray(vc, dtype=np.int64)
        uc = np.asarray(uc, dtype=np.int64)
        total = int(vc.sum())
        out = np.full(total, -1, dtype=np.int64)
        mapped = uc == vc  # a face carries all of its UVs or none
        if not mapped.any():
            return out
        slot_mask = np.repeat(mapped, vc)
        out[slot_mask] = np.asarray(uids, dtype=np.int64)
        return out

    @staticmethod
    def _parts(mesh) -> list:
        """*mesh* as its parts: a tuple / list is several meshes that together
        form ONE mesh, in concatenation order (see :meth:`pair_sources`)."""
        return list(mesh) if isinstance(mesh, (list, tuple)) else [mesh]

    @classmethod
    def _topology(cls, mesh) -> Tuple["np.ndarray", "np.ndarray", "np.ndarray"]:
        """``(counts, verts, world points)`` of *mesh* -- or of its parts joined
        end to end, the way Combine joins them (vertex indices offset)."""
        counts, verts, points = [], [], []
        for part in cls._parts(mesh):
            fn = cls._mesh_fn(part)
            c, v = fn.getVertices()
            counts.append(np.asarray(c, dtype=np.int64))
            verts.append(np.asarray(v, dtype=np.int64) + sum(map(len, points)))
            points.append(np.asarray(fn.getPoints(om.MSpace.kWorld))[:, :3])
        return np.concatenate(counts), np.concatenate(verts), np.concatenate(points)

    @classmethod
    def topology_matches(cls, a, b) -> Tuple[bool, str]:
        """``(ok, why)`` -- same polygon vertex lists on both meshes.

        Either side may be a tuple of meshes: their parts joined in order.
        """
        ca, va, pa = cls._topology(a)
        cb, vb, pb = cls._topology(b)
        if len(ca) != len(cb) or len(pa) != len(pb):
            return False, (
                f"{len(ca)} faces / {len(pa)} verts vs {len(cb)} / {len(pb)}"
            )
        if not np.array_equal(ca, cb):
            return False, "per-face vertex counts differ"
        if not np.array_equal(va, vb):
            return False, "face vertex order differs"
        return True, ""

    @classmethod
    def positions_match(cls, a, b, tolerance: float = 1e-4) -> bool:
        """World-space vertices coincide (either side may be a tuple of parts)."""
        pa, pb = cls._topology(a)[2], cls._topology(b)[2]
        if pa.shape != pb.shape:
            return False
        return float(np.abs(pa - pb).max()) <= tolerance

    @classmethod
    def auto_source_uv_set(cls, obj) -> str:
        """The UV set *obj*'s materials actually sample their textures through.

        Maya binds a file texture to a UV set per mesh via ``uvLink``; that
        binding is the ground truth for "which layout were these maps painted
        for", so Auto reads it. Falls back to the mesh's current UV set.
        """
        mesh = cls._mesh_fn(obj)
        shape = NodeUtils.get_shape(obj, no_intermediate=True, full_path=True)
        candidates = list(mesh.getUVSetNames())
        if not candidates:
            raise ValueError(f"{CoreUtils.leaf_name(obj)} has no UV sets")
        owners = set(cmds.ls(shape, long=True) or [])
        linked: List[str] = []
        for sg in set(cmds.listConnections(shape, type="shadingEngine") or []):
            mat = cls._surface_shader(sg)
            if not mat:
                continue
            for node in cmds.ls(cmds.listHistory(mat) or [], type="file") or []:
                try:
                    for plug in cmds.uvLink(query=True, texture=node) or []:
                        owner = plug.split(".uvSet[")[0]
                        if (cmds.ls(owner, long=True) or [None])[0] not in owners:
                            continue
                        name = cmds.getAttr(plug)
                        if name in candidates and name not in linked:
                            linked.append(name)
                except Exception:  # noqa: BLE001 -- uvLink is best-effort
                    continue
        return linked[0] if linked else mesh.currentUVSetName()

    @classmethod
    def correspondence(
        cls,
        target,
        source=None,
        *,
        source_uv_set: Optional[str] = None,
        target_uv_set: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Per-triangle ``(src_uv, dst_uv, face)`` for *target* vs *source*.

        Triangulates the TARGET once (``MFnMesh.getTriangleOffsets`` -- Maya's
        own triangulation, indexed by face-vertex slot, so it is purely
        topological) and reads both layouts through it: seams are honoured
        because UVs are read per face-vertex, and the same triangulation is
        applied to both meshes so the two arrays correspond row for row.

        *source* may be a tuple of meshes the target was combined from, in
        combine order (:meth:`pair_sources`): their face-vertex slots join end
        to end exactly as the target's do, each read through its own UV set.

        Returns:
            ``{"src_tris": (N,3,2), "dst_tris": (N,3,2), "faces": (N,),
            "dropped": int, "target_uv_set": str}`` -- *dropped* counts
            triangles whose face has no UVs in one of the two sets.
        """
        tgt = cls._mesh_fn(target)
        srcs = (
            [cls._mesh_fn(p) for p in cls._parts(source)]
            if source is not None
            else [tgt]
        )
        if source is not None:
            src_sets = [source_uv_set or s.currentUVSetName() for s in srcs]
            dst_set = target_uv_set or tgt.currentUVSetName()
        else:
            # Same mesh: the SOURCE is whichever set the textures are bound to
            # (that is what "where the maps were painted" means), and the
            # target, when unnamed, is the other set.
            src_set = source_uv_set or cls.auto_source_uv_set(target)
            dst_set = target_uv_set or next(
                (n for n in tgt.getUVSetNames() if n != src_set), src_set
            )
            src_sets = [src_set]
            if src_set == dst_set:
                raise ValueError(
                    "UV set -> UV set transfer needs two different sets "
                    f"(both are {dst_set!r})"
                )
        for src, src_set in zip(srcs, src_sets):
            if src_set not in src.getUVSetNames():
                raise ValueError(f"source has no UV set {src_set!r}")
        if dst_set not in tgt.getUVSetNames():
            raise ValueError(f"target has no UV set {dst_set!r}")

        tc, tfv = tgt.getTriangleOffsets()
        tc = np.asarray(tc, dtype=np.int64)
        tri_fv = np.asarray(tfv, dtype=np.int64).reshape(-1, 3)
        tri_face = np.repeat(np.arange(len(tc), dtype=np.int64), tc)

        dst_slot = cls._face_vertex_uv_ids(tgt, dst_set)
        # Each part's slots and UVs joined end to end; a part's uv ids are
        # offset by the UVs before it (-1, "no UVs", stays -1).
        src_slot, s_uv = [], []
        for src, src_set in zip(srcs, src_sets):
            slot = cls._face_vertex_uv_ids(src, src_set)
            n_prev = sum(map(len, s_uv))
            src_slot.append(np.where(slot >= 0, slot + n_prev, -1))
            su, sv = src.getUVs(src_set)
            s_uv.append(np.stack([np.asarray(su, float), np.asarray(sv, float)], 1))
        src_slot, s_uv = np.concatenate(src_slot), np.concatenate(s_uv)
        if len(src_slot) != len(dst_slot):
            raise ValueError("source and target face-vertex counts differ")
        d_ids = dst_slot[tri_fv]
        s_ids = src_slot[tri_fv]
        ok = (d_ids >= 0).all(axis=1) & (s_ids >= 0).all(axis=1)

        du, dv = tgt.getUVs(dst_set)
        d_uv = np.stack([np.asarray(du, float), np.asarray(dv, float)], axis=1)
        return {
            "src_tris": s_uv[s_ids[ok]],
            "dst_tris": d_uv[d_ids[ok]],
            "faces": tri_face[ok],
            "dropped": int((~ok).sum()),
            "target_uv_set": dst_set,
        }

    # --------------------------------------------------------- materials
    @staticmethod
    def _surface_shader(sg: str) -> Optional[str]:
        con = cmds.listConnections(
            f"{sg}.surfaceShader", source=True, destination=False
        )
        return con[0] if con else None

    @classmethod
    def face_materials(cls, obj) -> Tuple[List[str], "np.ndarray"]:
        """``(materials, per-face index into materials)`` for *obj*.

        *obj* may be a tuple of parts (see :meth:`_parts`): their faces join
        end to end, and a material two parts share is listed once.
        """
        mats: List[str] = []
        per_face = []
        for part in cls._parts(obj):
            part_face = np.full(cls._mesh_fn(part).numPolygons, -1, dtype=np.int64)
            for sg, faces in MatUtils.get_shading_assignments(part).items():
                mat = cls._surface_shader(sg)
                if not mat:
                    continue
                if mat not in mats:
                    mats.append(mat)
                idx = mats.index(mat)
                if faces is None:
                    part_face[:] = idx
                else:
                    part_face[np.asarray(faces, dtype=np.int64)] = idx
            per_face.append(part_face)
        return mats, np.concatenate(per_face)

    @staticmethod
    def material_maps(material: str) -> Dict[str, str]:
        """``{channel: absolute texture path}`` for the material's mapped slots."""
        return dict(MatManifest._process_material(material))

    @staticmethod
    def _shaders_named(name: str) -> List[str]:
        """Surface shaders called *name*, by classification.

        Not ``ls(materials=True)``: that lists only what is registered in
        ``defaultShaderList1``, and a production scene carried transfer
        results that were not -- each re-run then stacked ``<name>1``.
        """
        return [
            n
            for n in cmds.ls(name) or []
            if any(
                t.startswith("shader/surface")
                for t in NodeUtils.get_classification_tokens(cmds.nodeType(n))
            )
        ]

    @classmethod
    def _wearers(cls, material: str) -> set:
        """Long paths of the transforms *material* is assigned to."""
        return {
            node
            for sg in cmds.listConnections(material, type="shadingEngine") or []
            for node in cls._members(sg)
        }

    @staticmethod
    def _transforms(nodes) -> set:
        """Long paths of the transforms *nodes* name -- transforms, shapes, or
        components of either, however the caller spelled them."""
        out = set()
        for member in nodes:
            for node in cmds.ls(str(member).split(".")[0], long=True) or []:
                if cmds.nodeType(node) != "transform":
                    node = (
                        cmds.listRelatives(node, parent=True, fullPath=True) or [node]
                    )[0]
                out.add(node)
        return out

    @classmethod
    def _members(cls, sg: str) -> set:
        """Long paths of the transforms with faces in shading group *sg*."""
        return cls._transforms(cmds.sets(sg, query=True) or [])

    # --------------------------------------------------------- ownership
    #: String attribute naming the output a result material IS -- what finds
    #: it again however it is called now: beside a mesh or group of the
    #: output's name Maya calls it ``<name>1``, and a user may rename it.
    OUTPUT_STAMP = "transferOutput"

    @classmethod
    def _stamp(cls, material: str) -> Optional[str]:
        """The output *material* is stamped as, or None."""
        plug = f"{material}.{cls.OUTPUT_STAMP}"
        return cmds.getAttr(plug) if cmds.objExists(plug) else None

    @classmethod
    def _set_stamp(cls, material: str, name: str) -> None:
        """Stamp *material* as output *name* (:attr:`OUTPUT_STAMP`)."""
        if not cmds.attributeQuery(cls.OUTPUT_STAMP, node=material, exists=True):
            cmds.addAttr(material, longName=cls.OUTPUT_STAMP, dataType="string")
        cmds.setAttr(f"{material}.{cls.OUTPUT_STAMP}", name, type="string")

    @classmethod
    def _holders(cls, name: str, material_name: str) -> List[str]:
        """What holds output *name*: the surface shaders called
        *material_name* (the name its material takes), and every material
        stamped *name*, whatever it is called now. Stamps compare without case:
        the maps of ``Seat`` and ``seat`` are one file on Windows."""
        stamped = [
            node
            for node in cmds.ls(
                f"*.{cls.OUTPUT_STAMP}", objectsOnly=True, recursive=True
            )
            or []
            if (cls._stamp(node) or "").lower() == name.lower()
        ]
        return list(dict.fromkeys(cls._shaders_named(material_name) + stamped))

    @classmethod
    def _replaceable(cls, material: str, name: str, owners: set) -> bool:
        """Whether *material* is output *name*'s own previous result -- this
        run's to replace: stamped *name*, and worn by nothing outside *owners*
        (the run's targets, long transform paths).

        Anything else keeps the name: a material another object or a source of
        this run wears, the run's own original (a same-mesh run READS it), and
        every unstamped material, which may be anyone's.
        """
        stamp = cls._stamp(material)
        return (
            bool(stamp)
            and stamp.lower() == name.lower()
            and not cls._wearers(material) - owners
        )

    @staticmethod
    def new_material_from(material: str) -> str:
        """A fresh, editable shader modelled on *material*.

        A copy, so the new shader keeps *material*'s look for every channel
        the transfer does not write -- its values AND the inputs no texture
        channel owns (a StingrayPBS's IBL cubes: copied without them it renders
        with no ambient light, visibly darker). The channel slots are left
        undriven for the transfer to wire; their old maps belong to another
        layout. Maya's own default shaders (``lambert1`` /
        ``standardSurface1`` -- what geometry with nothing assigned wears, and
        a perfectly ordinary transfer target) are internal nodes that
        ``duplicate`` refuses outright, so those are re-created as a bare node
        of the same type: a default shader is at its default values anyway,
        which is exactly what the bare node has.
        """
        node_type = cmds.nodeType(material)
        if cmds.ls(material, defaultNodes=True):
            return cmds.shadingNode(node_type, asShader=True)
        copy = cmds.duplicate(material, inputConnections=False)[0]
        slots = {
            slot[0]
            for logical in ShaderAttributeMap.logical_channels()
            for slot in [
                ShaderAttributeMap.resolve_live_slot(material, logical, node_type)
            ]
            if slot
        }
        conns = (
            cmds.listConnections(
                material, source=True, destination=False, plugs=True, connections=True
            )
            or []
        )
        for dst, src in zip(conns[::2], conns[1::2]):
            attr = dst.partition(".")[2]
            # A slot is a top-level attribute; the plug may be one of its
            # children (``baseColorR``) or sit on an array element
            # (``inputs[0].color``), whose indexed name attributeQuery rejects.
            names = {attr.split(".")[0].split("[")[0]}
            leaf = attr.split(".")[-1].split("[")[0]
            try:
                names.update(
                    cmds.attributeQuery(leaf, node=material, listParent=True) or []
                )
            except RuntimeError:
                pass
            if names & slots:
                continue
            try:
                cmds.connectAttr(src, f"{copy}.{attr}", force=True)
            except RuntimeError:
                pass  # a plug the copy cannot take (locked, or not on its graph)
        return copy

    @staticmethod
    def material_constant(material: str, channel: str) -> Optional[Tuple[float, ...]]:
        """The channel's scalar/colour value on *material*, or None.

        :meth:`ShaderAttributeMap.read_constant`: None where no attribute holds
        a value (a StingrayPBS sampler, an undriven normal), so a source with
        no map there gets the neutral fill rather than a black AO or a
        (1, 1, 1) "normal"; opacity in opacity terms.
        """
        return ShaderAttributeMap.read_constant(material, channel)

    @staticmethod
    def pair_by_name(targets: Sequence[str], sources: Sequence[str]) -> Dict[str, str]:
        """Target -> source, by the longest matching TAIL of their DAG paths --
        the leaf name, then its parents -- so ``|tgt|chairA|seat_GEO`` pairs
        with ``|src|chairA|seat_GEO``, never ``|src|chairB|seat_GEO``. A
        best match two sources tie for (one leaf under parents that match
        nothing) and no shared leaf at all go by order."""

        def tail(node) -> List[str]:
            return [part for part in str(node).split("|") if part][::-1]

        def shared(a, b) -> int:
            n = 0
            for x, y in zip(tail(a), tail(b)):
                if x != y:
                    break
                n += 1
            return n

        pairs: Dict[str, str] = {}
        rest_t: List[str] = []
        used = set()
        for t in targets:
            scored = [(shared(t, s), s) for s in sources if s not in used]
            best = max((n for n, _s in scored), default=0)
            hits = [s for n, s in scored if n == best]
            if best and len(hits) == 1:
                pairs[t] = hits[0]
                used.add(hits[0])
            else:
                rest_t.append(t)
        rest_s = [s for s in sources if s not in used]
        if len(rest_t) != len(rest_s):
            raise ValueError(
                f"cannot pair {len(rest_t)} target(s) with {len(rest_s)} "
                "source(s): give them matching names or equal counts"
            )
        pairs.update(zip(rest_t, rest_s))
        return pairs

    @classmethod
    def pair_sources(cls, targets: Sequence[str], sources: Sequence[str]) -> Dict:
        """Target -> its source: one mesh, or the TUPLE it was combined from.

        * **One source** feeds every target (re-unwrapped copies of one mesh).
        * **As many sources as targets** (or fewer) pair one to one,
          :meth:`pair_by_name`.
        * **More sources than targets** means a target was combined from
          several (Mesh > Combine): each target takes the sources whose
          topologies, joined in some order, are exactly its own
          (:meth:`pythontk.UvTransfer.concatenation_order` -- positions tell
          identical pieces apart), as a tuple in that order. Sources no
          target was built from are left out.

        Raises:
            ValueError: A target no ordering of the remaining sources builds.
        """
        if len(sources) == 1:
            return {t: sources[0] for t in targets}
        if len(sources) <= len(targets):
            return cls.pair_by_name(targets, sources)
        topo = [cls._topology(s) for s in sources]
        free = list(range(len(sources)))
        pairs: Dict = {}
        for t in targets:
            order = ptk.UvTransfer.concatenation_order(
                cls._topology(t), [topo[i] for i in free]
            )
            if order is None:
                raise ValueError(
                    f"{CoreUtils.leaf_name(t)}: no combination of the "
                    f"{len(free)} source(s) has its topology -- a combined "
                    "target must be its sources combined, faces unedited"
                )
            picked = [free[i] for i in order]
            pairs[t] = (
                sources[picked[0]]
                if len(picked) == 1
                else tuple(sources[i] for i in picked)
            )
            free = [i for i in free if i not in picked]
        return pairs

    @classmethod
    def find_combined(cls, meshes: Sequence[str]) -> Optional[Tuple[str, Tuple]]:
        """The mesh among *meshes* combined from ALL the others, if any.

        Reads a selection without being told which mesh is which
        (:meth:`pythontk.UvTransfer.find_combined`): face counts first, and a
        full topology read only when they allow a combined mesh. Needs three
        or more meshes -- of two copies, neither is more "combined" than the
        other.

        Returns:
            ``(target, sources in combine order)``, or ``None``.
        """
        meshes = list(meshes)
        found = ptk.UvTransfer.find_combined(
            [cls._mesh_fn(m).numPolygons for m in meshes],
            lambda i: cls._topology(meshes[i]),
        )
        if found is None:
            return None
        i, order = found
        return meshes[i], tuple(meshes[j] for j in order)


class TextureTransfer(ptk.LoggingMixin, _TextureTransferInternal):
    """Move textures between UV layouts of the same mesh(es) -- see module doc."""

    @ptk.ClassProperty
    @ptk.Deprecation.symbol(
        "ShaderAttributeMap.CONSTANT_ATTRS", remove_in="0.23.0", since="2026-10-04"
    )
    def CONSTANT_ATTRS(cls) -> Dict[str, Dict[str, str]]:
        """The table moved to :attr:`ShaderAttributeMap.CONSTANT_ATTRS`, read
        through :meth:`ShaderAttributeMap.read_constant`."""
        return ShaderAttributeMap.CONSTANT_ATTRS

    def __init__(self, log_level="INFO"):
        super().__init__()
        self.logger.setLevel(log_level)

    # -------------------------------------------------------------- main
    def transfer(
        self,
        targets,
        source=None,
        *,
        source_uv_set: Optional[str] = None,
        target_uv_set: Optional[str] = None,
        channels: Optional[Sequence[str]] = None,
        size: Optional[int] = None,
        supersample: int = 2,
        padding: int = -1,
        output_dir: Optional[str] = None,
        name_format: str = "{material}_{channel}",
        output_name: Optional[str] = None,
        normal_convention: Optional[str] = None,
        source_mask_from_uvs: bool = True,
        assign: bool = False,
        assign_prefix: str = "",
        assign_suffix: Optional[str] = None,
        assign_shader_type: Optional[str] = None,
        assign_from: str = "target",
    ) -> Dict[str, Dict[str, str]]:
        """Transfer the source material(s)' maps onto the target UV layout.

        Parameters:
            targets: Target mesh(es) -- the layout being baked TO.
            source: Source mesh(es) of identical topology (paired by leaf
                name, else by order; more sources than targets = targets
                combined from them, see :meth:`pair_sources`), or ``None``
                for a UV-set transfer on the target mesh itself (then
                *source_uv_set* is required).
            source_uv_set / target_uv_set: UV set names; either may be
                omitted (Auto). Mesh -> mesh: each side's current set.
                Same mesh: the source is the set the mesh's textures are
                ``uvLink``-bound to (else its current set -- see
                :meth:`auto_source_uv_set`) and the target is the first
                OTHER set, so a two-set mesh needs neither named.
            channels: Logical channels to transfer (``baseColor``,
                ``roughness``, ``metallic``, ``normal``, ``emission``,
                ``ambientOcclusion``, ``opacity``, ``specular``). Default:
                every channel some source material has a map for.
            size: Output resolution per target material; default = the largest
                source map feeding it (2048 if none). When several texture sets
                consolidate into one layout, a set that lands on a smaller
                share of the target than it owned at source keeps less of its
                detail than that number suggests -- the squeeze is computed and
                named per source by :meth:`pythontk.UvTransfer._auto_size`, so
                raising this (or repacking the layout) is an informed call.
            supersample: See :meth:`pythontk.UvTransfer.build`.
            padding: Gutter in texels; ``-1`` fills all background.
            output_dir: Where the maps go, absolute or relative to the
                project's ``sourceimages`` (see :meth:`resolve_output_dir`).
                Default
                ``<project>/sourceimages/uv_transfer``.
            name_format: Filename stem; ``{material}`` / ``{channel}``.
                Ignored when *output_name* is given.
            output_name: Base name for the whole result -- the assigned
                material AND every map wired to it
                (``<output_name>_<Channel>.png``). Without it each output is
                named after the target layout it came from, which is the
                right default for a re-bake in place but not for a deliverable
                the user has a name for. Two layouts cannot share one name
                without overwriting each other's maps, so a run that keeps
                layouts apart appends the layout label to each. Also decides
                what Auto *assign_suffix* does: the user named the material,
                so nothing is appended to it. A name held outside this run --
                by a material another object or a source wears (any material
                the run did not stamp as this output's), or by a map a kept
                material reads -- is never taken: the output, maps and
                material alike, becomes ``<name>_1`` (``_2``, ...), with a
                warning. A re-run replaces its own previous result in place.
            normal_convention: ``"opengl"`` / ``"directx"`` to force one
                convention on every source normal map. Default: each map's own,
                read off its content, then its filename
                (:meth:`pythontk.UvTransfer.normal_convention`); sources that
                disagree are converted to the convention covering most of the
                layout.
            source_mask_from_uvs: Rasterize each source layout to a coverage
                mask and pre-fill the source's gutter from it before
                sampling, so hard-edged source maps cannot fringe.
            assign: Build one material per output, wired to the maps and
                assigned to the target faces. The originals are never
                modified.
            assign_prefix / assign_suffix: The affix applied to the assigned
                material's name -- the deliverable's *material* naming
                convention (``MAT_hero`` / ``hero_MAT``), which the maps
                deliberately do not follow. Applied idempotently
                (:meth:`pythontk.StrUtils.apply_affix`), so a re-run over a
                previous result does not stack a second copy of it.
                *assign_suffix* is Auto by default (``None``): ``_TRANSFER``
                on the layout-derived name, nothing when *output_name* already
                names the material. Pass ``""`` to force no suffix.
            assign_shader_type: Retype the assigned material -- one of
                :attr:`ShaderConverter.TARGETS` (``"stingray"``,
                ``"standard_surface"``, ``"open_pbr"``). ``None`` (default)
                keeps the target material's own type, which is what a re-bake
                in place wants; naming one is for the deliverable case, where
                the target may be wearing Maya's default shader and the result
                has to land on the pipeline's.
            assign_from: Which material the assigned one is a copy of --
                ``"target"`` (default: the target's own, right for a re-bake
                in place) or ``"source"``: the source material covering the
                most of each output layout, so the result keeps the look being
                transferred. A target that wears an import placeholder gets
                the placeholder's shader otherwise -- a StingrayPBS source's
                maps on a standardSurface render visibly darker. Either way the
                copy keeps the inputs no channel owns (see
                :meth:`new_material_from`).

        Returns:
            ``{output label: {channel: written path}}`` -- one label per target
            LAYOUT (:meth:`pythontk.UvTransfer.layout_jobs`): every target face
            merges into one output named after the UV set(s) when no islands
            overlap, and overlapping groups stay one output per target
            material. Faces that wear nothing are transferred too.
        """
        if np is None:
            raise RuntimeError("numpy is required")
        if assign_from not in ("target", "source"):
            raise ValueError(
                f"assign_from must be 'target' or 'source', not {assign_from!r}"
            )
        targets = [str(t) for t in ptk.make_iterable(targets)]
        if not targets:
            raise ValueError("no target meshes")
        sources = (
            [str(s) for s in ptk.make_iterable(source)] if source is not None else []
        )
        pairs = (
            self.pair_sources(targets, sources)
            if sources
            else {t: None for t in targets}
        )
        unused = set(sources) - {
            s for src in pairs.values() if src is not None for s in self._parts(src)
        }
        if unused:
            self.logger.warning(
                f"{len(unused)} source(s) are part of no target and were not "
                "read: " + ", ".join(sorted(CoreUtils.leaf_name(s) for s in unused))
            )

        out_dir = self.resolve_output_dir(output_dir)
        results: Dict[str, Dict[str, str]] = {}

        # What each target contributes, per target material: the unit of a
        # transfer is a LAYOUT, which ptk.UvTransfer.layout_jobs groups them
        # into by overlap. Where the target stands never enters it -- the
        # correspondence is topological and the maps are read through the UV
        # layouts alone, a normal map's XY included.
        parts: List[Dict[str, Any]] = []
        src_mat_registry: List[str] = []
        for tgt, src in pairs.items():
            if src is not None:
                ok, why = self.topology_matches(tgt, src)
                if not ok:
                    names = " + ".join(self._parts(src))
                    raise ValueError(f"{tgt} / {names}: topology differs ({why})")
            corr = self.correspondence(
                tgt, src, source_uv_set=source_uv_set, target_uv_set=target_uv_set
            )
            if corr["dropped"]:
                self.logger.warning(
                    f"{CoreUtils.leaf_name(tgt)}: {corr['dropped']} triangle(s) "
                    "have no UVs in one of the two sets and were skipped."
                )
            t_mats, t_face = self.face_materials(tgt)
            s_mats, s_face = self.face_materials(src if src is not None else tgt)
            faces = corr["faces"]
            for name in s_mats:
                if name not in src_mat_registry:
                    src_mat_registry.append(name)
            s_ids = np.array(
                [src_mat_registry.index(m) for m in s_mats], dtype=np.int64
            )
            # A source that wears nothing has no ids to index (np.where reads
            # both branches): every triangle reads -1, "nothing to transfer".
            tri_src = (
                np.where(s_face[faces] >= 0, s_ids[np.maximum(s_face[faces], 0)], -1)
                if len(s_ids)
                else np.full(len(faces), -1, dtype=np.int64)
            )
            tri_tgt = t_face[faces]
            # -1: faces that wear nothing -- no shading group, or one whose
            # shader is gone. Where texels go is the layout's business, not the
            # material's, so they transfer like any other.
            for ti in np.unique(tri_tgt).tolist():
                pick = (tri_tgt == ti) & (tri_src >= 0)
                if not pick.any():
                    continue
                t_mat = t_mats[ti] if ti >= 0 else None
                parts.append(
                    {
                        "material": t_mat,
                        "uv_set": corr["target_uv_set"],
                        "src": corr["src_tris"][pick],
                        "dst": corr["dst_tris"][pick],
                        "ids": tri_src[pick],
                        "members": [(tgt, t_mat)],
                    }
                )

        if not parts:
            raise ValueError(
                "nothing to transfer: no UV-mapped target face reads a shaded "
                "source face"
            )

        # Source material maps / constants, once; then hand the DCC-agnostic
        # half (sizing, table, per-channel remap, padding, naming, saving) to
        # pythontk.
        source_specs = [
            {
                "name": m,
                "maps": self.material_maps(m),
                "constants": {
                    ch: const
                    for ch in ptk.UvTransfer.CHANNEL_TOKENS
                    for const in [self.material_constant(m, ch)]
                    if const is not None
                },
            }
            for m in src_mat_registry
        ]
        if not any(spec["maps"] for spec in source_specs):
            raise ValueError("no source material carries a texture map to transfer")
        jobs = ptk.UvTransfer.layout_jobs(parts, source_specs, log=self.logger.info)
        # An explicit output name renames BOTH halves of the result -- the
        # maps and the material assigned from them -- so the user names the
        # deliverable once instead of hunting for `<target material>_TRANSFER`.
        # Two layouts cannot share one stem without their maps overwriting each
        # other, so a run that kept layouts apart keeps the label as well.
        stem = (
            ptk.StrUtils.sanitize(output_name, preserve_case=True)
            if output_name
            else ""
        )
        # Auto (None): the `_TRANSFER` tag exists to keep a layout-derived
        # name apart from the material it was derived FROM -- an explicit
        # output_name already did that, so it adds nothing there. An affix
        # the caller actually asked for is a naming convention, and applies
        # either way.
        suffix = assign_suffix
        if suffix is None:
            suffix = "" if stem else "_TRANSFER"
        if stem and len(jobs) > 1:
            # A layout is labelled by its target material, which on a re-run
            # is this run's own previous result: name by what it was derived
            # from, or every run stacks another `<stem>_`.
            relabel = ptk.UvTransfer.output_labels(
                list(jobs), stem, prefix=assign_prefix, suffix=suffix
            )
            jobs = {relabel[label]: job for label, job in jobs.items()}
        # Each output's name -- its maps' stem AND its material's core -- is
        # decided here, before anything is written (_output_names): a name a
        # material outside this run holds is named beside, never taken.
        if stem:
            name_format = "{material}_{channel}"
        names, replaced = self._output_names(
            jobs, stem, assign_prefix, suffix, out_dir, name_format, source_specs
        )
        named = {names[label]: job for label, job in jobs.items()}
        written = ptk.UvTransfer.transfer_materials(
            named,
            output_dir=out_dir,
            channels=channels,
            size=size,
            supersample=supersample,
            padding=padding,
            name_format=name_format,
            normal_convention=normal_convention,
            source_mask_from_uvs=source_mask_from_uvs,
            # A map a material this run keeps reads is never written; the maps
            # of the previous results it replaces are rewritten in place (a
            # same-mesh re-run reads its own previous result).
            avoid=[
                path
                for spec in source_specs
                if spec["name"] not in replaced
                for path in spec["maps"].values()
            ],
            log=self.logger.info,
        )
        results = {label: written[names[label]] for label in jobs}

        if assign:
            created = self.assign_results(
                written,
                named,
                prefix=assign_prefix,
                suffix=suffix,
                assign_from=assign_from,
            )
            if assign_shader_type and created:
                # Retyped AFTER the maps are wired, not built on the target type
                # from the start: only ShaderConverter knows how to stand up a
                # properly graphed target (a bare shadingNode StingrayPBS has no
                # ShaderFX graph, so its TEX_* attributes do not exist yet), and
                # keeping it here would be a second shader builder.
                #
                # The cost of that order is that the intermediate is a copy of
                # the TARGET's shader type, and MatManifest.restore SKIPS every
                # channel that type has no slot for -- lambert declares no
                # normal/roughness/metallic/AO at all -- so on a legacy target
                # those maps were dropped before the retype could carry them
                # across, and the log still counted them. Re-wire from the
                # ORIGINAL results once the real slots exist.
                retyped = (
                    ShaderConverter.convert(
                        list(created.values()), target=assign_shader_type
                    )
                    or {}
                )
                for name, old_mat in list(created.items()):
                    # convert keys its result by the SOURCE material and maps a
                    # SKIPPED one to None -- already the target type, or no
                    # channel declaration to read. A skip means nothing was
                    # dropped that re-wiring could recover, so leave it alone
                    # rather than reporting a retype that did not happen.
                    new_mat = retyped.get(old_mat)
                    if not new_mat or not cmds.objExists(new_mat):
                        continue
                    created[name] = new_mat
                    # The retype is a NEW node: the stamp crosses with the name.
                    self._set_stamp(new_mat, name)
                    channels = written.get(name) or {}
                    if not channels:
                        continue
                    wired = MatManifest.restore(
                        new_mat, {"materials": {new_mat: channels}}
                    )
                    self.logger.info(
                        f"Retyped {new_mat} to {assign_shader_type} "
                        f"({wired} of {len(channels)} map(s) wired)."
                    )
        return results

    # ----------------------------------------------------------- helpers
    @classmethod
    def default_output_dir(cls) -> str:
        """Where the maps go when the caller names no directory."""
        base = cls.output_base_dir()
        if base:
            return os.path.join(base, "uv_transfer").replace("\\", "/")
        return ptk.TempArtifacts("uv_transfer", policy="detached").dir_path()

    @staticmethod
    def output_base_dir() -> Optional[str]:
        """The directory a RELATIVE output entry is resolved against.

        The project's ``sourceimages``: the conventional home for
        material-referenced textures, and the base that makes a stored setting
        portable -- it survives the project being moved or copied. None when
        there is no project.
        """
        from mayatk.env_utils._env_utils import EnvUtils

        return EnvUtils.get_env_info("sourceimages") or None

    @classmethod
    def resolve_output_dir(cls, entry: Optional[str] = None) -> str:
        """The absolute output directory for a user-typed *entry*.

        Blank -> :meth:`default_output_dir`. A rooted path wins outright;
        anything else is a subdirectory of :meth:`output_base_dir`, which is
        the portable spelling a UI should store (its inverse is
        ``ptk.FileUtils.relativize_output_dir``). Falls back to the default
        when a relative entry has no project to resolve against -- a relative
        path handed to ``os.makedirs`` would land against the process CWD,
        which in a DCC is wherever the app was launched from.
        """
        if not (entry or "").strip():
            return cls.default_output_dir()
        resolved = ptk.FileUtils.resolve_output_dir(entry, cls.output_base_dir())
        return resolved or cls.default_output_dir()

    @staticmethod
    def _source_name(job: Dict[str, Any]) -> Optional[str]:
        """Name of the source material covering the most of *job*'s layout
        (:meth:`pythontk.UvTransfer.dominant_source`), or None."""
        idx = ptk.UvTransfer.dominant_source(job)
        return None if idx is None else job["sources"][idx].get("name")

    def _member_faces(self, members: Sequence[Tuple[str, Optional[str]]]) -> List[str]:
        """The faces *members* name -- each ``(object, target material)`` pair's
        faces wearing that material (``None``: wearing nothing), a whole object
        where that is all of it."""
        faces: List[str] = []
        for obj, t_mat in dict.fromkeys(members):
            mats, per_face = self.face_materials(obj)
            if t_mat is not None and t_mat not in mats:
                continue
            ids = np.nonzero(per_face == (mats.index(t_mat) if t_mat else -1))[0]
            if len(ids) == len(per_face):
                faces.append(obj)
            else:
                faces.extend(f"{obj}.f[{int(i)}]" for i in ids)
        return faces

    def _output_names(
        self,
        jobs: Dict[str, Dict[str, Any]],
        stem: str,
        prefix: str,
        suffix: str,
        out_dir: str,
        name_format: str,
        source_specs: List[Dict[str, Any]],
    ) -> Tuple[Dict[str, str], set]:
        """``({label: output name}, the previous results those names replace)``.

        Decided before anything is written: an output's name is its maps' stem
        AND its material's core. A layout is named *stem* (``<stem>_<label>``
        when there are several), else after its label -- or, where that name
        is held, the first free ``<name>_1``, ``<name>_2``, ... It is held
        while another output of this run took it, while any material holding
        it (:meth:`_holders`) is not this output's own previous result
        (:meth:`_replaceable`), or while a file it would write is a map a
        material this run keeps reads (a source's own maps, with the output
        folder set to theirs). ``|chairA|seat_GEO`` then ``|chairB|seat_GEO``
        under one name -- tentacle's blank Output Name derives ``seat`` for
        both -- deleted chairA's material and wrote its maps over chairA's.
        """
        owners = self._transforms(
            obj for job in jobs.values() for obj, _mat in job.get("members") or []
        )

        def key(path: str) -> str:
            return os.path.normcase(os.path.abspath(path))

        def writes(name: str) -> set:
            return {
                key(os.path.join(out_dir, f"{file_stem}.png"))
                for token in ptk.UvTransfer.CHANNEL_TOKENS.values()
                for file_stem in [name_format.format(material=name, channel=token)]
            }

        # A format that never names the material writes the same files under
        # any name, so renaming cannot steer it (the engine's avoid= still does).
        steerable = writes("a") != writes("b")
        names: Dict[str, str] = {}
        replaced: set = set()
        taken: set = set()
        for label in jobs:
            label_name = ptk.StrUtils.sanitize(label, preserve_case=True)
            if stem:
                base = stem if len(jobs) == 1 else f"{stem}_{label_name}"
            else:
                # A re-run's label is the result the last run assigned
                # (`wood_TRANSFER`): named by what that was derived from, so
                # the material, its stamp and its maps stay `wood`.
                base = ptk.StrUtils.strip_known_affix(
                    label_name, prefix=prefix, suffix=suffix
                ).strip("_")
            # Spelled as the engine writes it, so maps and material agree.
            base = ptk.StrUtils.sanitize(base or label_name, preserve_case=True)
            base = base.strip("_") or "material"
            name, k = base, 0
            while True:
                holders = self._holders(
                    name, ptk.StrUtils.apply_affix(name, prefix=prefix, suffix=suffix)
                )
                kept_reads = {
                    key(path)
                    for spec in source_specs
                    if spec["name"] not in holders
                    for path in spec["maps"].values()
                }
                if (
                    name.lower() not in taken
                    and all(self._replaceable(m, name, owners) for m in holders)
                    and not (steerable and writes(name) & kept_reads)
                ):
                    break
                k += 1
                name = f"{base}_{k}"
            if k:
                self.logger.warning(
                    f"{base} is held outside this run -- a material another "
                    "object or this run's source wears, or a map one reads: "
                    f"named {name}."
                )
            taken.add(name.lower())
            names[label] = name
            replaced.update(holders)
        return names, replaced

    def assign_results(
        self,
        results: Dict[str, Dict[str, str]],
        jobs: Dict[str, Dict[str, Any]],
        suffix: str = "_TRANSFER",
        base_name: Optional[str] = None,
        prefix: str = "",
        assign_from: str = "target",
    ) -> Dict[str, str]:
        """One ``<prefix><layout><suffix>`` material per output, on its faces.

        *base_name* replaces the layout-derived name (see ``transfer``'s
        ``output_name``): the material becomes ``<base_name>``, or
        ``<base_name>_<layout>`` when the run produced more than one layout and
        one name cannot cover them. Either way the affixes are applied to the
        result idempotently (:meth:`pythontk.StrUtils.apply_affix`) -- a re-run
        over a previous result does not stack a second copy of them.

        *jobs* carries each output's ``members`` -- ``(object, target material)``
        pairs, the material ``None`` for faces that wore nothing -- so every
        face that was transferred INTO this layout, across objects and across
        the materials the layout merged, lands on the one new material. It is
        a copy (:meth:`new_material_from`) of the first member's material --
        or, with *assign_from* ``"source"`` (or no member wearing one), of the
        source material covering the most of the layout -- wired to the
        outputs; the originals keep their textures, so a same-mesh UV-set
        transfer cannot clobber itself.

        A material holding the result's name -- called it, or stamped as this
        output (:attr:`OUTPUT_STAMP`) whatever it is called now -- is replaced
        only when it is this output's own previous result (:meth:`_replaceable`:
        stamped, and worn by nothing outside the *jobs*' members). Anything
        else keeps it -- a material another object or a source of the run
        wears, the run's own original, any unstamped one -- and the new one is
        named beside it. :meth:`transfer` names every output past such a holder
        before it writes (:meth:`_output_names`), so only a direct call meets
        one here. Every result is stamped with its name.

        Returns ``{output label: new material}``.
        """
        owners = self._transforms(
            obj for job in jobs.values() for obj, _mat in job.get("members") or []
        )
        # Resolve EVERY output's faces and build every copy -- neither touches
        # an assignment -- before anything is replaced. A replaced material can
        # be one another output's targets wear (on a re-run, the one its own
        # targets wear): resolved after the delete, those faces were found
        # wearing nothing and the shader to copy was gone, so the meshes ended
        # up wearing nothing at all. Building before clearing is also why a
        # copy never reads a node the clear destroyed ("No object(s) to
        # duplicate"); the rename comes after.
        planned: List[Tuple[str, Dict[str, str], str, str, List[str], str]] = []
        for label, channels in results.items():
            members = jobs.get(label, {}).get("members") or []
            if not channels or not members:
                continue
            base_mat = next((m for _obj, m in members if m), None)
            if assign_from == "source" or base_mat is None:
                base_mat = self._source_name(jobs[label]) or base_mat
            label_name = ptk.StrUtils.sanitize(label, preserve_case=True)
            if base_name:
                core = base_name if len(jobs) == 1 else f"{base_name}_{label_name}"
            else:
                core = label_name
            new_name = ptk.StrUtils.apply_affix(core, prefix=prefix, suffix=suffix)
            faces = self._member_faces(members)
            planned.append(
                (
                    label,
                    channels,
                    core,
                    new_name,
                    faces,
                    self.new_material_from(base_mat),
                )
            )
        # A copy of a previous result carries its stamp until it is restamped.
        fresh = {new_mat for *_rest, new_mat in planned}
        created: Dict[str, str] = {}
        for label, channels, core, new_name, faces, new_mat in planned:
            # The previous result goes, and its shading groups with it: the
            # shader they render is being deleted either way, and a surviving
            # `<mat>SG` makes the new one come back uniquified as `<mat>SG1`.
            #
            # Only a SHADER holds the name: a mesh or group may carry it too
            # (the name is the user's, or derived from the source mesh), and
            # the rename below uniquifies past such a namesake -- which is why
            # a result is found again by its stamp, not by its name.
            for old_mat in self._holders(core, new_name):
                if old_mat in fresh:  # another output's copy, not a previous run's
                    continue
                if not self._replaceable(old_mat, core, owners):
                    self.logger.warning(
                        f"{old_mat} is not this output's previous result, so it "
                        "is kept; the result is named beside it."
                    )
                    continue
                for old_sg in cmds.listConnections(old_mat, type="shadingEngine") or []:
                    if cmds.objExists(old_sg):
                        cmds.delete(old_sg)
                # An unregistered shader (nothing in defaultShaderList1 holds
                # it) goes WITH its shading group.
                if cmds.objExists(old_mat):
                    cmds.delete(old_mat)
            new_mat = cmds.rename(new_mat, new_name)
            self._set_stamp(new_mat, core)
            # A shading group of the name whose shader is gone (deleted outside
            # this tool) and that holds only these targets is that material's
            # husk: cleared, or the result comes back as `<mat>SG1` beside it.
            husk = f"{new_mat}SG"
            if (
                cmds.ls(husk, type="shadingEngine")
                and not self._surface_shader(husk)
                and self._members(husk) <= self._transforms(faces)
            ):
                cmds.delete(husk)
            wired = MatManifest.restore(new_mat, {"materials": {new_mat: channels}})
            # Keep the `<mat>SG` spelling this tool has always written, rather
            # than the helper's `<mat>_SG` default.
            MatUtils.create_shading_group(
                new_mat, name=f"{new_mat}SG", assign_to=faces or None
            )
            created[label] = new_mat
            # Report what LANDED, not what was written: restore silently skips
            # any channel this shader type has no slot for, and logging
            # len(channels) is how a partial wire ships unnoticed.
            if wired < len(channels):
                self.logger.warning(
                    f"Assigned {new_mat} ({wired} of {len(channels)} map(s) wired; "
                    f"{cmds.nodeType(new_mat)} has no slot for the rest)."
                )
            else:
                self.logger.info(f"Assigned {new_mat} ({wired} map(s)).")
        return created
