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
  is by matching leaf name, else by order.
* **UV set -> UV set** on ONE mesh (``source=None``, ``source_uv_set=...``).

Outputs are written per TARGET material -- one image per channel, sampled
from whichever source material each triangle wears (a consolidation reads
N source materials into one atlas; a source that has no map for a channel
contributes its constant). Normal maps are re-encoded into the target
island's tangent frame (see :meth:`pythontk.UvTransfer.transfer_normals`).

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

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


class _TextureTransferInternal:
    """Host-side helpers: correspondence, material lookup, IO."""

    #: Per shader type, the SCALAR attribute that holds a channel's value when
    #: no map is wired. StingrayPBS keeps them apart from its ``TEX_*`` slots;
    #: every other mapped shader stores the constant on the slot attribute
    #: itself (the one :class:`ShaderAttributeMap` names).
    CONSTANT_ATTRS: Dict[str, Dict[str, str]] = {
        "StingrayPBS": {
            "baseColor": "base_color",
            "emission": "emissive",
            "roughness": "roughness",
            "metallic": "metallic",
            "opacity": "opacity",
        },
    }

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

    @classmethod
    def topology_matches(cls, a, b) -> Tuple[bool, str]:
        """``(ok, why)`` -- same polygon vertex lists on both meshes."""
        fa, fb = cls._mesh_fn(a), cls._mesh_fn(b)
        if fa.numPolygons != fb.numPolygons or fa.numVertices != fb.numVertices:
            return False, (
                f"{fa.numPolygons} faces / {fa.numVertices} verts vs "
                f"{fb.numPolygons} / {fb.numVertices}"
            )
        ca, va = fa.getVertices()
        cb, vb = fb.getVertices()
        if list(ca) != list(cb):
            return False, "per-face vertex counts differ"
        if not np.array_equal(np.asarray(va), np.asarray(vb)):
            return False, "face vertex order differs"
        return True, ""

    @classmethod
    def positions_match(cls, a, b, tolerance: float = 1e-4) -> bool:
        fa, fb = cls._mesh_fn(a), cls._mesh_fn(b)
        pa = np.asarray(fa.getPoints(om.MSpace.kWorld))[:, :3]
        pb = np.asarray(fb.getPoints(om.MSpace.kWorld))[:, :3]
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

        Returns:
            ``{"src_tris": (N,3,2), "dst_tris": (N,3,2), "faces": (N,),
            "dropped": int, "target_uv_set": str}`` -- *dropped* counts
            triangles whose face has no UVs in one of the two sets.
        """
        tgt = cls._mesh_fn(target)
        src = cls._mesh_fn(source) if source is not None else tgt
        if source is not None:
            src_set = source_uv_set or src.currentUVSetName()
            dst_set = target_uv_set or tgt.currentUVSetName()
        else:
            # Same mesh: the SOURCE is whichever set the textures are bound to
            # (that is what "where the maps were painted" means), and the
            # target, when unnamed, is the other set.
            src_set = source_uv_set or cls.auto_source_uv_set(target)
            dst_set = target_uv_set or next(
                (n for n in tgt.getUVSetNames() if n != src_set), src_set
            )
        if source is None and src_set == dst_set:
            raise ValueError(
                "UV set -> UV set transfer needs two different sets "
                f"(both are {dst_set!r})"
            )
        if src_set not in src.getUVSetNames():
            raise ValueError(f"source has no UV set {src_set!r}")
        if dst_set not in tgt.getUVSetNames():
            raise ValueError(f"target has no UV set {dst_set!r}")

        tc, tfv = tgt.getTriangleOffsets()
        tc = np.asarray(tc, dtype=np.int64)
        tri_fv = np.asarray(tfv, dtype=np.int64).reshape(-1, 3)
        tri_face = np.repeat(np.arange(len(tc), dtype=np.int64), tc)

        dst_slot = cls._face_vertex_uv_ids(tgt, dst_set)
        src_slot = cls._face_vertex_uv_ids(src, src_set)
        if len(src_slot) != len(dst_slot):
            raise ValueError("source and target face-vertex counts differ")
        d_ids = dst_slot[tri_fv]
        s_ids = src_slot[tri_fv]
        ok = (d_ids >= 0).all(axis=1) & (s_ids >= 0).all(axis=1)

        du, dv = tgt.getUVs(dst_set)
        su, sv = src.getUVs(src_set)
        d_uv = np.stack([np.asarray(du, float), np.asarray(dv, float)], axis=1)
        s_uv = np.stack([np.asarray(su, float), np.asarray(sv, float)], axis=1)
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
        """``(materials, per-face index into materials)`` for *obj*."""
        mesh = cls._mesh_fn(obj)
        per_face = np.full(mesh.numPolygons, -1, dtype=np.int64)
        mats: List[str] = []
        for sg, faces in MatUtils.get_shading_assignments(obj).items():
            mat = cls._surface_shader(sg)
            if not mat:
                continue
            if mat not in mats:
                mats.append(mat)
            idx = mats.index(mat)
            if faces is None:
                per_face[:] = idx
            else:
                per_face[np.asarray(faces, dtype=np.int64)] = idx
        return mats, per_face

    @staticmethod
    def material_maps(material: str) -> Dict[str, str]:
        """``{channel: absolute texture path}`` for the material's mapped slots."""
        return dict(MatManifest._process_material(material))

    @classmethod
    def material_constant(
        cls, material: str, channel: str
    ) -> Optional[Tuple[float, ...]]:
        """The channel's scalar/colour value on *material*, or None."""
        try:
            ntype = cmds.nodeType(material)
        except Exception:  # noqa: BLE001
            return None
        attr = cls.CONSTANT_ATTRS.get(ntype, {}).get(channel)
        if attr is None:
            slot = ShaderAttributeMap.get_attr(ntype, channel)
            attr = slot[0] if slot else None
        if not attr or not cmds.objExists(f"{material}.{attr}"):
            return None
        try:
            value = cmds.getAttr(f"{material}.{attr}")
        except Exception:  # noqa: BLE001
            return None
        if isinstance(value, (list, tuple)):
            value = value[0] if isinstance(value[0], (list, tuple)) else value
            return tuple(float(v) for v in value)
        return (float(value),)

    @staticmethod
    def pair_by_name(targets: Sequence[str], sources: Sequence[str]) -> Dict[str, str]:
        """Target -> source, by matching leaf name; leftovers by order."""
        by_leaf = {CoreUtils.leaf_name(s): s for s in sources}
        pairs: Dict[str, str] = {}
        rest_t: List[str] = []
        used = set()
        for t in targets:
            leaf = CoreUtils.leaf_name(t)
            s = by_leaf.get(leaf)
            if s and s not in used:
                pairs[t] = s
                used.add(s)
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


class TextureTransfer(ptk.LoggingMixin, _TextureTransferInternal):
    """Move textures between UV layouts of the same mesh(es) -- see module doc."""

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
        assign_suffix: str = "_TRANSFER",
    ) -> Dict[str, Dict[str, str]]:
        """Transfer the source material(s)' maps onto the target UV layout.

        Parameters:
            targets: Target mesh(es) -- the layout being baked TO.
            source: Source mesh(es) of identical topology (paired by leaf
                name, else by order), or ``None`` for a UV-set transfer on
                the target mesh itself (then *source_uv_set* is required).
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
            size: Output resolution per target material; default = the
                largest source map feeding it (2048 if none).
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
                layouts apart appends the layout label to each. Also
                suppresses *assign_suffix*: the user named the material, so
                nothing is appended to it.
            normal_convention: ``"opengl"`` / ``"directx"``; default sniffs
                the source normal map's filename and assumes OpenGL otherwise.
            source_mask_from_uvs: Rasterize each source layout to a coverage
                mask and pre-fill the source's gutter from it before
                sampling, so hard-edged source maps cannot fringe.
            assign: Build ``<target material><assign_suffix>`` materials
                wired to the outputs and assign them to the target faces.
                The originals are never modified.

        Returns:
            ``{output label: {channel: written path}}`` -- one label per target
            LAYOUT: a target UV set's materials merge into one output named
            after the set when their islands do not overlap, and stay one
            output per material (named after it) when they do.
        """
        if np is None:
            raise RuntimeError("numpy is required")
        targets = [str(t) for t in ptk.make_iterable(targets)]
        if not targets:
            raise ValueError("no target meshes")
        sources = (
            [str(s) for s in ptk.make_iterable(source)] if source is not None else []
        )
        pairs = (
            self.pair_by_name(targets, sources)
            if sources
            else {t: None for t in targets}
        )

        out_dir = self.resolve_output_dir(output_dir)
        results: Dict[str, Dict[str, str]] = {}

        # Gather correspondence + materials over every pair, bucketed by
        # target UV set then target material: the unit of a transfer is a
        # LAYOUT, so per-material buckets that share a set and do not overlap
        # merge into one output (see ptk.UvTransfer.merge_layouts).
        by_set: Dict[str, Dict[str, Dict[str, Any]]] = {}
        src_mat_registry: List[str] = []
        for tgt, src in pairs.items():
            if src is not None:
                ok, why = self.topology_matches(tgt, src)
                if not ok:
                    raise ValueError(f"{tgt} / {src}: topology differs ({why})")
                if not self.positions_match(tgt, src):
                    self.logger.warning(
                        f"{CoreUtils.leaf_name(tgt)}: source and target vertex "
                        "positions differ; colour maps transfer fine, but the "
                        "normal-map tangent frames are only exact for coincident "
                        "geometry."
                    )
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
            tri_src = np.where(
                s_face[faces] >= 0, s_ids[np.maximum(s_face[faces], 0)], -1
            )
            tri_tgt = t_face[faces]
            for ti, t_mat in enumerate(t_mats):
                pick = (tri_tgt == ti) & (tri_src >= 0)
                if not pick.any():
                    continue
                bucket = by_set.setdefault(corr["target_uv_set"], {}).setdefault(
                    t_mat, {"src": [], "dst": [], "ids": [], "members": []}
                )
                bucket["src"].append(corr["src_tris"][pick])
                bucket["dst"].append(corr["dst_tris"][pick])
                bucket["ids"].append(tri_src[pick])
                bucket["members"].append((tgt, t_mat))

        if not by_set:
            raise ValueError("nothing to transfer: no shaded, UV-mapped faces found")

        # Source material maps / constants, once; then hand the DCC-agnostic
        # half (sizing, table, per-channel remap, padding, naming, saving) to
        # pythontk.
        source_specs = [
            {
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
        jobs: Dict[str, Dict[str, Any]] = {}
        for uv_set, per_mat in by_set.items():
            per_mat_jobs = {
                t_mat: {
                    "src": np.concatenate(b["src"]),
                    "dst": np.concatenate(b["dst"]),
                    "ids": np.concatenate(b["ids"]).astype(np.int32),
                    "sources": source_specs,
                    "members": list(b["members"]),
                }
                for t_mat, b in per_mat.items()
            }
            merged = ptk.UvTransfer.merge_layouts(per_mat_jobs, uv_set)
            if len(per_mat) > 1:
                self.logger.info(
                    f"UV set {uv_set!r}: {len(per_mat)} target material(s) -> "
                    + (
                        f"one layout ({uv_set})"
                        if len(merged) == 1
                        else f"{len(merged)} overlapping layouts, kept apart"
                    )
                )
            for key, job in merged.items():
                label = key if key not in jobs else f"{uv_set}_{key}"
                jobs[label] = job
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
        if stem:
            name_format = (
                f"{stem}_{{channel}}"
                if len(jobs) == 1
                else f"{stem}_{{material}}_{{channel}}"
            )
        results = ptk.UvTransfer.transfer_materials(
            jobs,
            output_dir=out_dir,
            channels=channels,
            size=size,
            supersample=supersample,
            padding=padding,
            name_format=name_format,
            normal_convention=normal_convention,
            source_mask_from_uvs=source_mask_from_uvs,
            log=self.logger.info,
        )

        if assign:
            self.assign_results(
                results,
                jobs,
                suffix="" if stem else assign_suffix,
                base_name=stem or None,
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

    def assign_results(
        self,
        results: Dict[str, Dict[str, str]],
        jobs: Dict[str, Dict[str, Any]],
        suffix: str = "_TRANSFER",
        base_name: Optional[str] = None,
    ) -> Dict[str, str]:
        """One ``<layout><suffix>`` material per output, assigned to its faces.

        *base_name* replaces the layout-derived name (see ``transfer``'s
        ``output_name``): the material becomes ``<base_name>``, or
        ``<base_name>_<layout>`` when the run produced more than one layout and
        one name cannot cover them.

        *jobs* carries each output's ``members`` -- ``(object, target material)``
        pairs -- so every face that was transferred INTO this layout, across
        objects and across the materials the layout merged, lands on the one
        new material. It is duplicated from the first member's material (so it
        keeps that shader type) and wired to the outputs; the originals keep
        their textures, so a same-mesh UV-set transfer cannot clobber itself.

        Returns ``{output label: new material}``.
        """
        created: Dict[str, str] = {}
        for label, channels in results.items():
            members = jobs.get(label, {}).get("members") or []
            if not channels or not members:
                continue
            base_mat = members[0][1]
            label_name = ptk.StrUtils.sanitize(label, preserve_case=True)
            if base_name:
                new_name = base_name if len(jobs) == 1 else f"{base_name}_{label_name}"
            else:
                new_name = f"{label_name}{suffix}"
            # Resolve the target faces BEFORE anything is replaced. With an
            # explicit output_name a second run's target material IS the one
            # the previous run assigned, so the delete below removes the very
            # material these members are matched on: resolving afterwards finds
            # nothing and silently leaves the meshes unassigned.
            faces: List[str] = []
            for obj, t_mat in dict.fromkeys(members):
                mats, per_face = self.face_materials(obj)
                if t_mat not in mats:
                    continue
                ids = np.nonzero(per_face == mats.index(t_mat))[0]
                if len(ids) == len(per_face):
                    faces.append(obj)
                else:
                    faces.extend(f"{obj}.f[{int(i)}]" for i in ids)
            # Duplicate BEFORE clearing the previous run's node, and rename
            # after -- for the same reason: deleting by name first destroys the
            # very node being duplicated ("No object(s) to duplicate"). The old
            # node's shading groups go with it: the shader they render is being
            # deleted either way, and a surviving `<mat>SG` makes the new one
            # come back uniquified as `<mat>SG1` on every re-run.
            new_mat = cmds.duplicate(base_mat, inputConnections=False)[0]
            if cmds.objExists(new_name):
                for old_sg in (
                    cmds.listConnections(new_name, type="shadingEngine") or []
                ):
                    if cmds.objExists(old_sg):
                        cmds.delete(old_sg)
                cmds.delete(new_name)
            new_mat = cmds.rename(new_mat, new_name)
            MatManifest.restore(new_mat, {"materials": {new_mat: channels}})
            sg = cmds.sets(
                name=f"{new_mat}SG", renderable=True, noSurfaceShader=True, empty=True
            )
            cmds.connectAttr(f"{new_mat}.outColor", f"{sg}.surfaceShader", force=True)
            if faces:
                cmds.sets(faces, e=True, forceElement=sg)
            created[label] = new_mat
            self.logger.info(f"Assigned {new_mat} ({len(channels)} map(s)).")
        return created
