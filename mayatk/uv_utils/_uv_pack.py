# !/usr/bin/python
# coding=utf-8
"""The two engines behind :meth:`mayatk.UvUtils.pack_uvs`.

- :class:`_UvPackInternal` -- xatlas round-trip: UV arrays out,
  :class:`pythontk.UvPack`, per-shell similarity transforms back. Drives the
  pack-only external engine from Maya.
- :class:`_U3dPackInternal` -- Maya's native ``u3dLayout`` (the Pack tool's
  Standard method): per-mesh failure isolation and an area-balanced deal of
  shells onto a UDIM tile grid.

Reached through :meth:`mayatk.UvUtils.pack_uvs`; nothing here is called directly.
The rest of this docstring describes the xatlas path.

Pack scope — objects *or* components:

The input is resolved to a per-mesh scope (:meth:`_UvPackInternal._resolve_scope`):
a mesh named at object level packs whole, while a component entry (faces, UVs,
verts, edges) packs only the region it covers. Component entries are widened to
the faces they touch, because the engine's unit of input is a triangle, and the
arrays handed over are then *compacted* to just the UVs those faces reference —
so the engine never sees, and no write-back can move, the rest of the map. This
mirrors the native ``u3dLayout`` path, which packs exactly the UVs it is given.

Write-back strategy — verified mechanics (Maya 2025):

xatlas moves each island rigidly (translate, optional rotation, and — like
u3dLayout — a mirror where that packs tighter) under one global uniform
scale, so instead of rewriting the UV table through the API
(``MFnMesh.setUVs`` bypasses undo), each shell's least-squares similarity
transform (scale, rotation, optional reflection, translation) is solved from
its before/after coordinates and applied with ``cmds.polyEditUV`` — which is
fully undo-captured and takes ``-angle`` + pivot rotation and negative-scale
flips. Every shell's solve is validated against a residual tolerance
*before* any shell of that mesh is touched, so a mesh either packs whole or
reports and stays put.
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError:
    pass
import numpy as np
import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.core_utils.plugins._plugins import Plugins

# Solve tolerance in UV units, calibrated against the engine (measured over
# cube/cylinder/sphere/torus/cone/pipe/prism/helix at padding 0-8): a shell the
# engine moved rigidly lands within 2.1e-04, while a shell it could NOT move
# rigidly misses by 1e-01 or more — three orders apart, so 1e-03 separates them
# with a 5x margin over noise and a 100x margin under a real failure.
#
# That failure is real and has one cause: Maya UV shells are VERTEX-connected
# while xatlas charts are EDGE-connected, so a shell pinched to another at a
# single shared UV point is two charts to the engine. It packs them apart
# without duplicating the shared vertex, and no rigid per-shell move can
# reproduce that. Such a mesh is rejected and restored rather than half-applied.
RESIDUAL_TOLERANCE = 1e-3


@dataclass
class PackUvsResult:
    """Per-object outcome of a :meth:`mayatk.UvUtils.pack_uvs` run."""

    engine: str
    succeeded: List[str] = field(default_factory=list)
    failed: List[Tuple[str, str]] = field(default_factory=list)
    atlas_width: int = 0
    atlas_height: int = 0
    # What was actually packed, for the meshes that succeeded: face components
    # under a component-scoped pack, else the mesh itself. Measuring the
    # resulting texel density against ``succeeded`` would read the WHOLE mesh
    # after a faces-only pack and report a number the run never produced.
    targets: List[str] = field(default_factory=list)
    # The UDIM tile grid actually packed into, (tiles_u, tiles_v): the u3d
    # engine clamps a grid that would run past the end of the UDIM row, and a
    # caller reporting the run needs to say so. Always (1, 1) under xatlas.
    tiles: Tuple[int, int] = (1, 1)

    def __bool__(self) -> bool:
        return bool(self.succeeded)


class _UvPackInternal:
    """Round-trip mechanics for :meth:`mayatk.UvUtils.pack_uvs`."""

    # Engine calls funnel through here so tests can substitute a stub.
    @staticmethod
    def _engine_pack(meshes: Sequence, **params):
        return ptk.UvPack.pack_islands(meshes, **params)

    @staticmethod
    def _check_engine() -> None:
        """Fail with the install note up front, before the scene is touched."""
        ptk.UvPack.resolve(required=True)

    @staticmethod
    def _fn_mesh(mesh: str) -> "om.MFnMesh":
        sel = om.MSelectionList()
        sel.add(str(mesh))
        dag = sel.getDagPath(0)
        dag.extendToShape()
        return om.MFnMesh(dag)

    @classmethod
    def _uv_positions(cls, mesh: str) -> np.ndarray:
        """Just the current UV-set coordinates, ``(N, 2)`` float64.

        The snapshot path needs positions only; going through
        :meth:`_uv_arrays` would also run its per-face triangulation loop —
        pure waste on a heavy mesh, and it runs once per mesh per pack.
        """
        us, vs = cls._fn_mesh(mesh).getUVs()
        if not len(us):
            raise ValueError("no UVs on the current UV set")
        return np.column_stack([np.asarray(us), np.asarray(vs)]).astype(np.float64)

    @classmethod
    def _resolve_scope(cls, objects) -> List[Tuple[str, Optional[List[str]]]]:
        """Input to ``[(mesh, faces or None), ...]`` — the pack's per-mesh scope.

        ``None`` means "pack the whole mesh"; a list means "pack only these
        faces". Component entries are widened to the faces they touch (the
        engine packs triangles, so a partial face can't be an input) and kept
        as unflattened ranges — expanding ``pCube1.f[0:5000]`` into individual
        strings costs more than the pack itself on a dense selection.

        A mesh named at object level packs whole even if components of it were
        also given: the wider request wins, so mixing a stray leftover
        component selection into an object selection can't silently narrow the
        pack. Whole-mesh entries keep the caller's order; component-scoped
        meshes are ordered by Maya's own component ordering and report first.

        Instances need no explicit dedupe, and must not be packed twice — they
        share one shape, so one UV set, and two entries would make the engine
        reserve space for the same UVs twice and then write two transforms onto
        them. `_resolve_meshes`' ``cmds.ls(..., dag=True, type="mesh")`` reports
        an instanced shape by its FIRST path only, so *every* sibling — whether
        named as an object or reached through a component — canonicalizes to the
        same transform and merges into one entry (pinned by test; the same
        `cmds.ls` quirk that `get_neighbor_shell_bounds` guards *against*).
        """
        from mayatk.uv_utils._auto_unwrap import _AutoUnwrapInternal

        if objects is None:
            objects = cmds.ls(selection=True) or []
        entries = CoreUtils.as_strings(objects)
        components = [e for e in entries if "." in e]
        wholes = [e for e in entries if "." not in e]

        # dict, not a separate order list: insertion order is preserved, and
        # re-assigning an existing key (component scope -> whole mesh) keeps it.
        scope: dict = {}
        if components:
            # One batched conversion: a call per entry is the same work split
            # into N round trips through the component parser. Non-mesh
            # components (curve CVs, lattice points) simply drop out of it.
            resolved: dict = {}  # node string -> mesh transform (memoized)
            for comp in cmds.polyListComponentConversion(components, toFace=True) or []:
                node = comp.rsplit(".", 1)[0]
                if node not in resolved:
                    found = _AutoUnwrapInternal._resolve_meshes([node])
                    resolved[node] = found[0] if found else None
                mesh = resolved[node]
                if mesh is None:
                    continue
                scope.setdefault(mesh, []).append(comp)
        # Second, so a mesh named both ways ends up whole rather than narrowed.
        for mesh in _AutoUnwrapInternal._resolve_meshes(wholes):
            scope[mesh] = None
        return list(scope.items())

    @staticmethod
    def _face_ids(faces: Sequence[str], face_count: int) -> List[int]:
        """Sorted unique polygon indices named by the *faces* components.

        Read through the API rather than by parsing ``cmds.ls(flatten=True)``
        strings: a shell selection routinely covers tens of thousands of faces,
        and the component object hands over its element array directly.
        """
        sel = om.MSelectionList()
        for comp in faces:
            try:
                sel.add(str(comp))
            except RuntimeError:  # stale/renamed component — nothing to pack
                continue
        ids: List[int] = []
        for i in range(sel.length()):
            _, component = sel.getComponent(i)
            if component.isNull() or not component.hasFn(
                om.MFn.kSingleIndexedComponent
            ):
                continue
            ids.extend(om.MFnSingleIndexedComponent(component).getElements())
        if not ids:
            return []
        found = np.unique(np.asarray(ids, dtype=np.int64))
        return found[(found >= 0) & (found < face_count)].tolist()

    @classmethod
    def _uv_arrays(cls, mesh: str, faces: Optional[Sequence[str]] = None):
        """Current-UV-set geometry of *mesh* as plain arrays, optionally scoped.

        Returns ``(uvs (N,2) float64, triangles (M,3) uint32, shell_ids (N,),
        uv_ids (N,))``, where ``uv_ids`` maps each returned row back to the
        mesh's own UV index. Triangles are fan-triangulated per polygon over
        the assigned UV ids — exact for convex faces, and only
        chart-connectivity/coverage input for the packer, so concave n-gons
        cost at most slight pack looseness.

        *faces* restricts the triangles to those polygons. Rows are then
        compacted to just the UVs those triangles reference, so the engine's
        arrays carry no pass-through coordinates and the write-back cannot
        reach a UV outside the scope.
        """
        uvs = cls._uv_positions(mesh)
        fn = cls._fn_mesh(mesh)
        counts, uv_ids = fn.getAssignedUVs()
        # `starts` is what gives a scoped pack random access to a face's UV ids
        # (``getAssignedUVs`` carries one count per polygon, 0 for an unmapped
        # one, so the offsets stay aligned with face indices). Both are
        # materialized as plain Python ints: the loop below touches every
        # face-vertex, and indexing numpy arrays there boxes each element as an
        # np.int64. Timing the LOOP alone over a 102k-face plane: 198ms with
        # plain ints, 296ms indexing numpy arrays, 300ms for the sequential
        # walk this replaced. (Whole-mesh `_uv_arrays` is ~283ms end to end;
        # the rest is the two whole-mesh API reads and the compaction. A scope
        # of 100 faces on that mesh still costs ~103ms, since UV positions and
        # shell ids are whole-mesh reads either way — proportional-to-scope
        # extraction would need a branch here and buys nothing next to the
        # engine's own scale search.)
        starts = [0, *np.cumsum(np.asarray(counts, dtype=np.int64)).tolist()]
        uv_ids = list(uv_ids)

        face_ids = (
            range(len(counts)) if faces is None else cls._face_ids(faces, len(counts))
        )
        tris = []
        for face in face_ids:
            ids = uv_ids[starts[face] : starts[face + 1]]
            for k in range(1, len(ids) - 1):
                tris.append((ids[0], ids[k], ids[k + 1]))
        if not tris:
            raise ValueError(
                "no UV-mapped faces in the pack scope"
                if faces is not None
                else "no UV-mapped faces"
            )
        tris = np.asarray(tris, dtype=np.int64)

        used = np.unique(tris.reshape(-1))
        # -1 for anything outside the scope: every triangle row is in `used` by
        # construction, so a row that isn't overflows the uint32 cast below into
        # an out-of-range index ``ptk.UvPack.pack_islands`` refuses
        # (ValueError), rather than silently aliasing onto UV 0.
        remap = np.full(len(uvs), -1, dtype=np.int64)
        remap[used] = np.arange(len(used))
        _, shell_ids = fn.getUvShellsIds()
        return (
            uvs[used],
            remap[tris].astype(np.uint32),
            np.asarray(shell_ids)[used],
            used,
        )

    @staticmethod
    def _solve_similarity(old: np.ndarray, new: np.ndarray):
        """Least-squares 2D similarity (reflection-aware) mapping *old* -> *new*.

        xatlas mirrors charts where that packs tighter (verified: a mirrored
        chart comes back as a perfect *reflected* similarity, det < 0 — the
        same liberty u3dLayout takes), so both the proper and the U-flipped
        solve are tried and the better fit wins.

        Returns ``(scale, angle_degrees, mirrored, c0, c1, residual)``: flip U
        about centroid ``c0`` when *mirrored*, rotate+scale about ``c0``, then
        translate ``c1 - c0``. Residual is the max abs error over the shell.
        """
        c0, c1 = old.mean(axis=0), new.mean(axis=0)
        d0, d1 = old - c0, new - c1
        denom = float((d0 * d0).sum())
        if denom < 1e-20:  # single-point / degenerate shell: translate only
            return 1.0, 0.0, False, c0, c1, float(np.abs(d1).max(initial=0.0))

        def _solve(source: np.ndarray):
            a = float((source * d1).sum())
            b = float((source[:, 0] * d1[:, 1] - source[:, 1] * d1[:, 0]).sum())
            scale = math.hypot(a, b) / denom
            angle = math.atan2(b, a)
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            rebuilt = np.column_stack(
                [
                    scale * (source[:, 0] * cos_a - source[:, 1] * sin_a),
                    scale * (source[:, 0] * sin_a + source[:, 1] * cos_a),
                ]
            )
            return scale, angle, float(np.abs(rebuilt - d1).max())

        scale, angle, residual = _solve(d0)
        scale_m, angle_m, residual_m = _solve(d0 * np.array([-1.0, 1.0]))
        if residual_m < residual:
            return scale_m, math.degrees(angle_m), True, c0, c1, residual_m
        return scale, math.degrees(angle), False, c0, c1, residual

    @staticmethod
    def _component_ranges(mesh: str, indices: np.ndarray) -> List[str]:
        """Sorted UV indices to compact ``mesh.map[a:b]`` component strings."""
        comps = []
        run_start = prev = int(indices[0])
        for idx in indices[1:]:
            idx = int(idx)
            if idx == prev + 1:
                prev = idx
                continue
            comps.append(f"{mesh}.map[{run_start}:{prev}]")
            run_start = prev = idx
        comps.append(f"{mesh}.map[{run_start}:{prev}]")
        return comps

    @classmethod
    def _apply_shell_transforms(
        cls,
        mesh: str,
        old_uvs: np.ndarray,
        new_uvs: np.ndarray,
        shell_ids: np.ndarray,
        written: Optional[np.ndarray] = None,
        uv_ids: Optional[np.ndarray] = None,
    ) -> None:
        """Move *mesh*'s shells from *old_uvs* to *new_uvs* via polyEditUV.

        Solves and validates every shell first, then applies — a bad solve
        raises before anything moves, keeping the mesh's pack atomic. Uniform
        scale and rotation share the shell's input centroid as pivot (they
        commute about a common pivot), then the centroid delta translates.

        *written* lists the indices the engine actually repositioned; each
        shell is fitted on those rows only (the rest hold pass-through input
        coordinates, which would corrupt the fit) and the solved transform is
        then applied to the whole shell so it moves as one piece.

        *uv_ids* maps the array rows onto the mesh's own UV indices (None =
        the rows *are* the UV indices), which is what lets a component-scoped
        pack address its compacted subset.
        """
        fit_mask = None
        if written is not None:
            fit_mask = np.zeros(len(old_uvs), dtype=bool)
            fit_mask[written] = True

        solved = []
        for shell in np.unique(shell_ids):
            indices = np.flatnonzero(shell_ids == shell)
            fit_on = indices if fit_mask is None else indices[fit_mask[indices]]
            if not len(fit_on):
                # The engine placed none of this shell's UVs, so there is no
                # transform to recover — leave it where it is. (A single placed
                # UV is still usable: _solve_similarity degenerates to a pure
                # translation, which is exactly right for a lone point.)
                continue
            scale, angle, mirrored, c0, c1, residual = cls._solve_similarity(
                old_uvs[fit_on], new_uvs[fit_on]
            )
            if residual > RESIDUAL_TOLERANCE:
                raise RuntimeError(
                    f"shell {int(shell)} is pinched to another shell at a single "
                    f"UV point, so the engine could not move it as one piece "
                    f"(residual {residual:.2e}). Split or merge the shells at "
                    f"that point, or pack this mesh with the Standard method"
                )
            solved.append((indices, scale, angle, mirrored, c0, c1))

        from mayatk.uv_utils._uv_utils import UvUtils

        moves = [
            (
                cls._component_ranges(
                    mesh, np.sort(indices if uv_ids is None else uv_ids[indices])
                ),
                scale,
                angle,
                mirrored,
                c0,
                c1,
            )
            for indices, scale, angle, mirrored, c0, c1 in solved
        ]
        # A pinned UV refuses polyEditUV, which tore a shell carrying pins: its
        # pinned UVs stayed where they were while the rest were packed.
        with UvUtils.pins_lifted([c for comps, *_ in moves for c in comps]):
            for comps, scale, angle, mirrored, c0, c1 in moves:
                if mirrored:
                    # Maya's own Flip-U mechanism; centroid pivot keeps c0 fixed.
                    cmds.polyEditUV(
                        comps, pivotU=c0[0], pivotV=c0[1], scaleU=-1, scaleV=1
                    )
                if abs(angle) > 1e-6:
                    cmds.polyEditUV(
                        comps, pivotU=c0[0], pivotV=c0[1], angle=angle, relative=True
                    )
                if abs(scale - 1.0) > 1e-9:
                    cmds.polyEditUV(
                        comps,
                        pivotU=c0[0],
                        pivotV=c0[1],
                        scaleU=scale,
                        scaleV=scale,
                        relative=True,
                    )
                du, dv = float(c1[0] - c0[0]), float(c1[1] - c0[1])
                if abs(du) > 1e-9 or abs(dv) > 1e-9:
                    cmds.polyEditUV(comps, uValue=du, vValue=dv, relative=True)

    @classmethod
    def run(
        cls,
        uv_utils,
        objects=None,
        map_size: int = 1024,
        udim: int = 1001,
        coverage: Tuple[float, float] = (1.0, 1.0),
        rotate: bool = True,
        brute_force: bool = False,
        preserve_3d: bool = True,
        padding: Optional[float] = None,
    ) -> PackUvsResult:
        """Full pack round-trip. See :meth:`mayatk.UvUtils.pack_uvs`."""
        cls._check_engine()

        scope = cls._resolve_scope(objects)
        if not scope:
            raise ValueError("No mesh objects to pack.")

        result = PackUvsResult(engine="xatlas")
        # Per mesh, what the density pre-pass and the caller's density readout
        # measure: the scoped faces, or the mesh itself when it packs whole.
        targets = {mesh: faces or [mesh] for mesh, faces in scope}

        # Snapshot BEFORE the density pre-pass: that pass already rewrites the
        # scene's UVs, so a mesh rejected further down is not "untouched" unless
        # it is explicitly put back. Without this a rejected mesh keeps its
        # equalized-but-unpacked layout and lands on top of the packed ones.
        pre_pass = {}
        if preserve_3d:
            for mesh, _ in scope:
                try:
                    pre_pass[mesh] = cls._uv_positions(mesh)
                except (RuntimeError, ValueError):
                    pass  # unreadable here fails again below, with its reason

            # Equalize per-shell texel density (u3dLayout -preScaleMode 1
            # equivalent). xatlas preserves relative input scale, so equal
            # density in = equal density out. Runs against the pack scope, so a
            # faces-only pack neither measures nor rescales the rest of the map.
            in_scope = [t for group in targets.values() for t in group]
            density = uv_utils.get_texel_density(in_scope, map_size)
            if density:
                uv_utils.set_texel_density(in_scope, density=density, map_size=map_size)

        arrays, per_mesh = [], []
        for mesh, faces in scope:
            try:
                uvs, tris, shell_ids, uv_ids = cls._uv_arrays(mesh, faces)
            except (RuntimeError, ValueError) as error:
                result.failed.append((CoreUtils.short_name(mesh), str(error)))
                cls._restore(mesh, pre_pass.get(mesh))
                continue
            arrays.append((uvs, tris))
            per_mesh.append((mesh, uvs, shell_ids, uv_ids))
        if not arrays:
            return result

        # Fixed-page pack (measured rationale): in content-driven mode the
        # engine picks the atlas aspect freely, and uniform-fitting that atlas
        # into the box wasted the mismatch — a 6-shell cube filled only 0.50 of
        # a Full tile, 0.25 of Half-V. Instead the box is tiled with square
        # cells (Full/Quarter = 1, halves = 2 stacked) and the engine packs
        # square pages of exactly the cell's real pixel size, scale-searched to
        # fill them edge-to-edge. Padding is then exact pixels, since a page
        # texel is a texture pixel.
        cov_u, cov_v = coverage
        short_side, long_side = min(cov_u, cov_v), max(cov_u, cov_v)
        if short_side <= 0:
            raise ValueError(f"coverage must be positive, got {coverage}")
        pages = max(1, int(round(long_side / short_side)))
        resolution = max(64, int(round(map_size * short_side)))
        if padding is None:
            padding = uv_utils.calculate_uv_padding(map_size)  # pixels
        packed = cls._engine_pack(
            arrays,
            padding=int(round(padding)),
            rotate=rotate,
            brute_force=brute_force,
            resolution=resolution,
            pages=pages,
        )
        result.atlas_width, result.atlas_height = packed.width, packed.height

        # Cells: the margin-inset box split into `pages` near-square cells
        # along its long axis (same margin rule as the native u3dLayout path).
        u_tile, v_tile = ptk.MathUtils.udim_to_tile(udim)
        margin = ptk.MathUtils.uv_tile_margin(map_size)
        inner_origin = np.array([u_tile + margin, v_tile + margin])
        inner_size = np.array(
            [max(cov_u - 2 * margin, 1e-6), max(cov_v - 2 * margin, 1e-6)]
        )
        axis = 0 if cov_u >= cov_v else 1  # the long axis the cells stack along
        step = np.zeros(2)
        step[axis] = inner_size[axis] / pages
        cell_size = inner_size.copy()
        cell_size[axis] = step[axis]
        fit = float(cell_size.min())  # square page -> uniform fit into the cell

        for (mesh, old_uvs, shell_ids, uv_ids), unit_uvs, written, page_arr in zip(
            per_mesh, packed.uvs, packed.written, packed.pages
        ):
            origins = inner_origin + np.asarray(page_arr).reshape(-1, 1) * step
            try:
                cls._apply_shell_transforms(
                    mesh,
                    old_uvs,
                    unit_uvs * fit + origins,
                    shell_ids,
                    written,
                    uv_ids,
                )
                result.succeeded.append(mesh)
                result.targets.extend(targets[mesh])
            except RuntimeError as error:
                result.failed.append((CoreUtils.short_name(mesh), str(error)))
                cls._restore(mesh, pre_pass.get(mesh))
        return result

    @classmethod
    def _restore(cls, mesh: str, original_uvs: Optional[np.ndarray]) -> None:
        """Undo the density pre-pass on a mesh that failed to pack.

        That pass scales each shell about its own bounding-box center, so the
        move back is a per-shell similarity — the same machinery the pack
        write-back uses, run in reverse. Best-effort: a mesh that can't be
        restored is left as-is rather than aborting the surviving packs.

        Always whole-mesh, even after a component-scoped pack: the snapshot is
        whole-mesh, and a shell the pre-pass left alone solves to the identity
        and is skipped, so the untouched part of the map costs nothing.
        """
        if original_uvs is None:
            return
        try:
            current = cls._uv_positions(mesh)
            _, shell_ids = cls._fn_mesh(mesh).getUvShellsIds()
            shell_ids = np.asarray(shell_ids)
            if len(current) != len(original_uvs):
                # UV count changed under us — the snapshot no longer describes
                # this mesh, so restoring from it would corrupt the layout.
                raise RuntimeError(
                    f"UV count changed ({len(original_uvs)} -> {len(current)})"
                )
            cls._apply_shell_transforms(mesh, current, original_uvs, shell_ids)
        except (RuntimeError, ValueError) as error:
            print(f"# pack_uvs: could not restore {mesh}: {error} #")


class _U3dPackInternal:
    """Native ``u3dLayout`` pack for :meth:`mayatk.UvUtils.pack_uvs`
    (``engine="u3d"``) -- the Pack tool's Standard method.

    Gutters (verified): ``-shellSpacing`` is per-shell padding in UV units --
    adjacent shells land 2x spacing apart -- and it rescales with the post-pack
    fit; ``-tileMargin`` is an absolute inset from the region edges.
    """

    @staticmethod
    def classify_error(error) -> str:
        """Condense an Unfold3D RuntimeError (u3dLayout / u3dUnfold / u3dOptimize)
        into a short, human-readable reason for display in a message box.
        """
        msg = str(error)
        low = msg.lower()
        if "non-manifold" in low:
            return "non-manifold vertices"
        if "overlapping" in low:
            return "overlapping UVs"
        return msg.split("\n")[0][:50]

    @staticmethod
    def _resolve(objects) -> Tuple[list, list]:
        """``(meshes, uvs)``: what the layout packs, and its UVs.

        Both scopes pack exactly what is given: whole objects pack their full
        maps, while faces / UVs / edges / vertices pack only that region -- a
        component entry is widened to the faces it touches, since a packer's
        unit of input is a face (a shell chosen in the UV editor is a UV
        selection). The UVs stay unflattened ranges (``pCube1.map[0:23]``), so
        a dense mesh never expands into millions of index strings.
        """
        from mayatk.core_utils.components import Components

        if objects is None:
            objects = cmds.ls(selection=True) or []
        selection = CoreUtils.as_strings(objects)
        meshes = Components.get_components(selection, "mesh", flatten=False)
        if not meshes:
            meshes = cmds.ls(selection, type="transform", dag=True) or selection
        if any("." in str(m) for m in meshes):
            meshes = cmds.polyListComponentConversion(meshes, toFace=True) or []
        uvs = cmds.polyListComponentConversion(meshes, fromFace=True, toUV=True) or []
        return meshes, uvs

    @classmethod
    def _pack(cls, all_uvs, meshes, pack_kwargs, result: PackUvsResult) -> None:
        """u3dLayout with per-mesh failure isolation.

        Batches all meshes into one call; on failure with several meshes,
        probes each to isolate the bad one(s) and re-packs the survivors
        together so they share the tile. A single mesh reports its failure
        directly -- a probe pass would just re-run the same failing call.
        Appends to *result*'s ``succeeded`` / ``failed``.
        """
        try:
            cmds.u3dLayout(all_uvs, **pack_kwargs)
            result.succeeded.extend(str(m) for m in meshes)
            return
        except RuntimeError as batch_error:
            if len(meshes) == 1:
                result.failed.append((str(meshes[0]), cls.classify_error(batch_error)))
                return

        good = []
        for mesh in meshes:
            uvs = cmds.polyListComponentConversion(mesh, fromFace=True, toUV=True) or []
            if not uvs:
                continue
            try:
                cmds.u3dLayout(uvs, **pack_kwargs)
                good.extend(uvs)
                result.succeeded.append(str(mesh))
            except RuntimeError as mesh_error:
                result.failed.append((str(mesh), cls.classify_error(mesh_error)))
        if good:
            try:
                cmds.u3dLayout(good, **pack_kwargs)
            except RuntimeError as combine_error:
                # Survivors packed individually (each filling the tile);
                # combine failed, so leave them as-is and surface the cause.
                result.failed.append(
                    ("<combined re-pack>", cls.classify_error(combine_error))
                )

    @staticmethod
    def distribute_to_grid(uv_utils, uvs, u_tile, v_tile, tiles_u, tiles_v) -> None:
        """Assign the shells of *uvs* to grid tiles, balanced by UV area.

        u3dLayout's own Distribute mode (-tileAssignMode 0) deals shells to the
        tiles by count and drops some on top of already-packed ones (measured:
        2-400 stacked faces on mixed content, varying run to run). Its Center
        mode (-tileAssignMode 1) instead packs each shell inside the tile its
        center already occupies, overlap-free -- so the distribution is done
        here: largest shell first into the least-loaded tile, each moved by a
        whole-tile offset (shells sharing an offset move in one call). A pinned
        UV moves with its shell and keeps its pin weight.
        """
        sel = om.MSelectionList()
        for comp in uvs:
            sel.add(comp)
        shells = []  # (area, mesh path, uv ids of the shell in scope, center)
        for i in range(sel.length()):
            dag, component = sel.getComponent(i)
            fn = om.MFnMesh(dag)
            us, vs = fn.getUVs()
            pos = np.column_stack([us, vs])
            _, shell_ids = fn.getUvShellsIds()
            shell_ids = np.asarray(shell_ids)
            # Sorted + unique, so each shell's ids below stay ascending.
            scope = np.unique(
                np.asarray(
                    om.MFnSingleIndexedComponent(component).getElements()
                    if not component.isNull()
                    else range(len(us)),
                    dtype=np.int64,
                )
            )
            if not len(scope):
                continue
            # Per-shell area (shoelace over each polygon's assigned UVs).
            counts, uv_ids = fn.getAssignedUVs()
            counts = np.asarray(counts, dtype=np.int64)
            uv_ids = np.asarray(uv_ids, dtype=np.int64)
            face = np.repeat(np.arange(len(counts)), counts)
            nxt = np.arange(len(uv_ids)) + 1
            ends = np.cumsum(counts)
            nxt[ends[counts > 0] - 1] = (ends - counts)[counts > 0]
            a, b = pos[uv_ids], pos[uv_ids[nxt]]
            twice = np.zeros(len(counts))
            np.add.at(twice, face, a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0])
            area = np.zeros(shell_ids.max(initial=-1) + 1)
            mapped = counts > 0
            np.add.at(
                area, shell_ids[uv_ids[ends[mapped] - 1]], np.abs(twice[mapped]) / 2
            )
            path = dag.fullPathName()
            # One stable sort groups the scope by shell (a mask per shell is
            # shells x UVs on a dense mesh).
            order = np.argsort(shell_ids[scope], kind="stable")
            scope = scope[order]
            cuts = np.flatnonzero(np.diff(shell_ids[scope])) + 1
            for ids in np.split(scope, cuts):
                shell = shell_ids[ids[0]]
                shells.append((area[shell], path, ids, pos[ids].mean(axis=0)))

        load = [0.0] * (tiles_u * tiles_v)
        moves = {}  # (du, dv) -> component strings
        for area, path, ids, center in sorted(shells, key=lambda s: -s[0]):
            tile = min(range(len(load)), key=load.__getitem__)
            load[tile] += area
            du = u_tile + tile % tiles_u - int(np.floor(center[0]))
            dv = v_tile + tile // tiles_u - int(np.floor(center[1]))
            if du or dv:
                runs = np.split(ids, np.flatnonzero(np.diff(ids) != 1) + 1)
                moves.setdefault((du, dv), []).extend(
                    f"{path}.map[{int(r[0])}:{int(r[-1])}]" for r in runs
                )
        if not moves:
            return
        # polyEditUV honours pin weights: a pinned UV refuses to move, tearing
        # its shell across tiles (the Pack panel's Pin and Stack leave pins
        # behind). Lift them for the moves and put the exact weights back.
        comps = [c for group in moves.values() for c in group]
        pinned = []
        if any(cmds.polyPinUV(comps, query=True, value=True) or []):
            flat = cmds.ls(comps, flatten=True) or []
            weights = uv_utils.get_uv_pin_weights(flat)
            pinned = [(uv, w) for uv, w in zip(flat, weights) if w]
        if pinned:
            cmds.polyPinUV([uv for uv, _ in pinned], value=0.0)
        for (du, dv), group in moves.items():
            cmds.polyEditUV(group, uValue=du, vValue=dv, relative=True)
        if pinned:
            uv_utils.set_uv_pin_weights(*zip(*pinned))

    @classmethod
    def run(
        cls,
        uv_utils,
        objects=None,
        map_size: int = 1024,
        udim: int = 1001,
        coverage: Tuple[float, float] = (1.0, 1.0),
        preserve_3d: bool = True,
        padding: Optional[float] = None,
        pre_rotate: int = 0,
        rotate_step: int = 0,
        rotate_min: int = 0,
        rotate_max: int = 0,
        mutations: int = 1,
        scale_mode: int = 2,
        tiles: Tuple[int, int] = (1, 1),
    ) -> PackUvsResult:
        """Full native pack. See :meth:`mayatk.UvUtils.pack_uvs`."""
        meshes, all_uvs = cls._resolve(objects)
        if not all_uvs:
            raise ValueError("No UVs found on selection.")
        Plugins.load("Unfold3D")

        # packBox is [umin, umax, vmin, vmax], anchored at the UDIM's tile corner.
        u_tile, v_tile = uv_utils.udim_to_tile(udim)
        tiles_u, tiles_v = (max(1, int(t)) for t in tiles)
        # A UDIM row is 10 tiles wide and u wraps to the next row at 10 -- the
        # tile at u=10 is NOT the next UDIM -- so shells packed past the row
        # end would be unaddressable by any UDIM texture. Clamp the grid to the
        # columns remaining from the anchor (reported back through ``tiles``).
        tiles_u = min(tiles_u, 10 - u_tile)
        shell_padding = (
            uv_utils.calculate_uv_padding(map_size, normalize=True)
            if padding is None
            else padding / float(map_size)
        )
        # Fractional tile coverage shrinks the pack box from the tile's
        # bottom-left corner; u3dLayout accepts fractional -packBox extents.
        # A tile grid repurposes the box as its cell template (verified), so
        # coverage is forced Full then.
        grid = tiles_u > 1 or tiles_v > 1
        cov_u, cov_v = (1.0, 1.0) if grid else coverage

        pack_kwargs = dict(
            # -res is the packer's raster, not the texture size: Maya's own
            # dialog caps it at 4096, and the 16k map size packed ~9x slower
            # than 4096 (measured 86s vs 9s on 24 meshes) for no overlap gain.
            resolution=min(map_size, 4096),
            shellSpacing=shell_padding,
            tileMargin=shell_padding / 2,
            preScaleMode=1 if preserve_3d else 0,
            preRotateMode=pre_rotate,
            packBox=[u_tile, u_tile + cov_u, v_tile, v_tile + cov_v],
            multiObject=True,  # -m off causes all shells to stack at the tile center
        )
        # Rotate flags only when the search is asked for (max > min). Maya's
        # stock dialog follows the same pattern: passing them with the default
        # range (0..180) silently rotates shells even with Pre-Rotate Off.
        if rotate_max > rotate_min:
            pack_kwargs["rotateStep"] = rotate_step
            pack_kwargs["rotateMin"] = rotate_min
            pack_kwargs["rotateMax"] = rotate_max
        if mutations > 1:
            pack_kwargs["mutations"] = mutations
        # Omitted -layoutScaleMode == Uniform (verified), so only emit overrides.
        if scale_mode != 2:
            pack_kwargs["layoutScaleMode"] = scale_mode
        if grid:
            pack_kwargs["tileU"] = tiles_u
            pack_kwargs["tileV"] = tiles_v
        # Distribute here, then let u3dLayout pack each tile in place: its own
        # Distribute mode stacks shells (see distribute_to_grid). Not under
        # Scale Mode Off -- shells keep their size there and spill past the
        # grid, so a tile-local pack has nothing to fit into.
        distribute = grid and scale_mode != 1
        if distribute:
            pack_kwargs["tileAssignMode"] = 1

        result = PackUvsResult(engine="u3d", tiles=(tiles_u, tiles_v))
        if distribute:
            cls.distribute_to_grid(uv_utils, all_uvs, u_tile, v_tile, tiles_u, tiles_v)
        cls._pack(all_uvs, meshes, pack_kwargs, result)
        # u3dLayout packs exactly the components it was given, so what packed
        # is what succeeded (a face range under a component-scoped pack).
        result.targets = list(result.succeeded)
        return result
