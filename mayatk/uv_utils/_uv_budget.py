# !/usr/bin/python
# coding=utf-8
"""UV texture-budget analysis: measure the scene, plan with :class:`pythontk.UvBudget`.

The read-only counterpart of :mod:`mayatk.uv_utils._uv_pack`. That module hands
UV arrays to an engine and writes a new layout back; this one measures the same
geometry and answers "how many maps would that layout need, and at what texel
density?" without touching a single UV. Reached through
:meth:`mayatk.UvUtils.analyze_uv_budget`; nothing here is called directly.

Why this is worth a module rather than a texel-density call
-----------------------------------------------------------
:meth:`UvUtils.get_texel_density` answers a per-selection question with two
``polyEvaluate`` sums. Budgeting a whole assembly is a different problem,
because three separate multiplicities inflate those sums and every one of them
must be divided out before the arithmetic means anything:

1. **Instances.** N instance paths are one shape, one UV array, one claim on
   map space -- but a naive area sum over the paths counts the geometry N
   times. (Measured on a production assembly: 1.40x overall, 3.16x worst.)
   ``_resolve_scope`` canonicalizes every path to one entry per shape, which is
   the same dedupe ``pack_uvs`` relies on.
2. **Stacked shells.** Deliberately overlapped shells -- repeated trim, mirrored
   halves, decals -- share one region of the map by design. Their 3D area sums
   but their map claim does not. (Measured: 1.33x overall, 2.00x on decals.)
   :meth:`_UvBudgetInternal._shell_metrics` collapses them by signature.
3. **Source map size.** Texel density is only meaningful against the map it is
   measured on, and a set whose textures are 2048 is not the same problem as
   one whose textures are 4096. The size is *read from the textures*
   (:meth:`mayatk.MatUtils.get_mat_info`) rather than assumed, so a mixed-
   resolution scene measures correctly instead of uniformly wrongly.

Get any of the three wrong and the plan is confidently off by an integer number
of maps -- which is exactly the error that makes a planning tool worse than no
tool, because it is invisible in the output.

Preserving density, not flattening it
-------------------------------------
The default target is the density each group *already* has against its *own*
source maps, with the solve scaling all of them together. Packing tighter than
the source resolution does not add detail -- it magnifies the same texels -- so
density above the source is map area spent to store nothing.

Flattening every group onto one number is the tempting simplification and it is
wrong on real content. Measured on a production assembly, the seven texture sets
span 11.2 to 126.2 px/unit: budgeting them all at the highest would cost the
lowest sets (126.2/11.2)^2 = 127x their UV area, and budgeting at the lowest
throws away the detail the decals were authored for. So ``density_from``
defaults to ``"preserve"``, and the answer it reports is a SCALE -- 1.0 meaning
"exactly as authored", 0.62 meaning "62% of authored density, which is what
your map budget buys". The flattening modes remain for content that really is
uniform.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except Exception:
    cmds = om = None
import numpy as np
import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils

# UV-space rounding used to decide that two shells are stacked. Coarse enough
# to survive the float noise of a shell that was moved and moved back (a
# round-tripped shell drifts in the last few ULPs), fine enough that two
# genuinely different shells never collide: at 1e-5 of UV space, a 4096 map
# resolves the difference to a twentieth of a texel.
STACK_PRECISION = 5


@dataclass
class MeshMetrics:
    """One mesh's claim on map space, with stacked shells already collapsed."""

    mesh: str
    area_3d: float = 0.0
    area_uv: float = 0.0
    perimeter: float = 0.0
    charts: int = 0
    stack_factor: float = 1.0
    """Its texture SET's stacking multiplicity, not this mesh's own.

    Stacking is collapsed per set (see :meth:`_UvBudgetInternal._collapse`), so
    the figure describes the set every mesh in it shares. A per-mesh number
    would be meaningless for the case that matters -- one chart shared by two
    hundred meshes.
    """

    @property
    def density(self) -> float:
        """Texels per world unit this mesh would have on a 1x1 map."""
        return (self.area_uv / self.area_3d) ** 0.5 if self.area_3d > 0 else 0.0


@dataclass
class TextureSetInfo:
    """What one texture set is, measured -- before any planning happens."""

    key: str
    materials: List[str] = field(default_factory=list)
    meshes: List[str] = field(default_factory=list)
    map_size: int = 0
    density: float = 0.0
    area_3d: float = 0.0
    area_uv: float = 0.0
    charts: int = 0
    perimeter: float = 0.0
    stack_factor: float = 1.0
    surface_maps: int = 0
    measured_maps: int = 0
    map_size_source: str = "assumed"
    note: str = ""

    @property
    def map_size_measured(self) -> bool:
        """Whether ``map_size`` came from the textures or from the caller's default.

        Worth checking before trusting :attr:`density`: an assumed size makes
        the density a statement about the *default*, not about this set.
        """
        return self.map_size_source == "textures"

    def __str__(self) -> str:
        return (
            f"{self.key}: {len(self.meshes)} mesh, {self.charts} charts, "
            f"{self.density:.1f} px/unit @ {self.map_size or '?'}"
            f"{'' if self.map_size_measured else ' (assumed)'}"
        )


@dataclass
class UvBudgetResult:
    """A measured scene plus the plan it supports.

    The measurement half is as much of the answer as the plan: a set whose
    ``stack_factor`` is 2.0 or whose density sits ten times below its
    neighbours explains the plan far better than the page count does.
    """

    plan: Optional[ptk.BudgetPlan] = None
    sets: List[TextureSetInfo] = field(default_factory=list)
    items: List[ptk.BudgetItem] = field(default_factory=list)
    target_density: float = 0.0
    density_source: str = ""
    map_size: int = 0
    group_by: str = ""
    seconds: float = 0.0
    warnings: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.plan and self.plan.chosen.feasible)

    @property
    def density_is_scale(self) -> bool:
        """Whether :attr:`target_density` is a multiplier, not texels per unit.

        Read from the plan rather than recorded twice: the row already knows,
        because it knows whether its items carried their own densities.
        """
        return bool(self.plan and self.plan.chosen.scaled)

    @property
    def density_spread(self) -> Tuple[float, float]:
        """Lowest and highest measured per-set density.

        A wide spread is the finding, not a detail: consolidating sets that
        differ by an order of magnitude forces every one of them onto the
        target, so the plan either wastes area on the low sets or starves the
        high ones. Splitting the job by density band beats one global answer.
        """
        d = [s.density for s in self.sets if s.density > 0]
        return (min(d), max(d)) if d else (0.0, 0.0)

    @property
    def measured_sets(self) -> int:
        """How many sets got their map size from real textures rather than the default.

        Anything short of all of them qualifies the density figures: an assumed
        size makes a set's density a statement about the assumption. Unresolved
        texture paths are the usual cause, and the matching warning says so.
        """
        return sum(1 for s in self.sets if s.map_size_measured)

    def pages(self) -> List[Tuple[int, List[str]]]:
        """``(page index, item keys)`` for the chosen row -- what goes where."""
        if not self.plan:
            return []
        return [(p.index, list(p.keys)) for p in self.plan.chosen.page_list]

    def report(self) -> str:
        """Human-readable summary: the measurement, the plan, and the alternates."""
        if not self.plan:
            return "no plan: " + ("; ".join(self.warnings) or "nothing measured")
        lo, hi = self.density_spread
        out = [
            f"Measured {len(self.sets)} texture sets, "
            f"{sum(len(s.meshes) for s in self.sets)} meshes, "
            f"{sum(s.charts for s in self.sets)} charts in {self.seconds:.2f}s",
            (
                f"Density {lo:.1f}-{hi:.1f} px/unit ({hi / lo:.1f}x spread)"
                if lo > 0
                else "Density unmeasurable"
            ),
            (
                f"Target {self.target_density:.3f}x authored density "
                f"(from {self.density_source})"
                if self.density_is_scale
                else f"Target {self.target_density:.1f} px/unit "
                f"(from {self.density_source})"
            ),
            f"Map size read from textures for {self.measured_sets} of "
            f"{len(self.sets)} sets",
            f"Placing {len(self.items)} indivisible group(s) by {self.group_by}, "
            f"assuming {self.plan.chosen.fill:.0%} of each page is usable",
            "",
            f"PLAN  {self.plan.chosen}",
        ]
        out += [f"  alt  {row}" for row in self.plan.alternates]
        if self.plan.chosen.feasible and self.plan.chosen.note:
            out.append(f"  {self.plan.chosen.note}")
        thin = self.plan.chosen.underfilled()
        if thin:
            out.append(
                f"  {len(thin)} page(s) under half full -- pages {thin}; "
                "a near-empty map still costs full memory and streaming budget"
            )
        out += [f"  ! {w}" for w in self.warnings]
        return "\n".join(out)


class _UvBudgetInternal:
    """Measurement and grouping behind :meth:`mayatk.UvUtils.analyze_uv_budget`."""

    # ------------------------------------------------------------------ #
    # Per-mesh measurement
    # ------------------------------------------------------------------ #
    @staticmethod
    def _triangles(mesh: str):
        """Fan-triangulate *mesh* once into matched UV-space and world-space arrays.

        Returns ``(uvs (N,2), uv_idx (M,3), world_tris (M,3,3), shell (M,))``:
        row *i* of ``uv_idx`` and ``world_tris`` describe the same triangle, so
        a per-triangle UV area and its world area are directly comparable and
        the 3D-to-UV scale of a shell falls out of the pair.

        Deliberately *not* :meth:`_UvPackInternal._uv_arrays`, which compacts
        and re-indexes its output for an engine that only wants UV topology.
        Only the fan rule is shared, and a fan rule is arithmetic, not logic
        that can drift. ``getAssignedUVs`` reports 0 for an unmapped polygon
        while ``getVertices`` reports its real count, so the two offset tables
        are walked separately -- sharing one would silently shear the UV and
        world arrays apart on any mesh carrying an unmapped face.
        """
        sel = om.MSelectionList()
        sel.add(str(mesh))
        fn = om.MFnMesh(sel.getDagPath(0))

        us, vs = fn.getUVs()
        if not len(us):
            raise ValueError("no UVs on the current UV set")
        uvs = np.column_stack([np.asarray(us), np.asarray(vs)]).astype(np.float64)
        pts = np.asarray(fn.getPoints(om.MSpace.kWorld))[:, :3].astype(np.float64)
        _, shell_ids = fn.getUvShellsIds()
        shell_ids = np.asarray(shell_ids)

        uv_counts, uv_ids = fn.getAssignedUVs()
        v_counts, v_ids = fn.getVertices()
        uv_ids, v_ids = list(uv_ids), list(v_ids)
        uv_at = [0, *np.cumsum(np.asarray(uv_counts, dtype=np.int64)).tolist()]
        v_at = [0, *np.cumsum(np.asarray(v_counts, dtype=np.int64)).tolist()]

        u_tri: List[Tuple[int, int, int]] = []
        w_tri: List[Tuple[int, int, int]] = []
        for face, n in enumerate(uv_counts):
            if n < 3:  # unmapped or degenerate -- claims no map space
                continue
            u = uv_ids[uv_at[face] : uv_at[face] + n]
            w = v_ids[v_at[face] : v_at[face] + n]
            for k in range(1, n - 1):
                u_tri.append((u[0], u[k], u[k + 1]))
                w_tri.append((w[0], w[k], w[k + 1]))
        if not u_tri:
            raise ValueError("no UV-mapped faces")

        u_idx = np.asarray(u_tri, dtype=np.int64)
        w_idx = np.asarray(w_tri, dtype=np.int64)
        return uvs, u_idx, pts[w_idx], shell_ids[u_idx[:, 0]]

    @staticmethod
    def _areas(uv_tris: np.ndarray, world_tris: np.ndarray):
        """Per-triangle ``(uv_area, world_area)``. Both are half a cross product."""
        a = uv_tris[:, 1] - uv_tris[:, 0]
        b = uv_tris[:, 2] - uv_tris[:, 0]
        uv = 0.5 * np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0])
        p = world_tris[:, 1] - world_tris[:, 0]
        q = world_tris[:, 2] - world_tris[:, 0]
        return uv, 0.5 * np.linalg.norm(np.cross(p, q), axis=1)

    @staticmethod
    def _perimeters(uvs: np.ndarray, u_idx: np.ndarray, shell: np.ndarray, groups: int):
        """UV border length per shell, indexed by shell id.

        A chart's border is exactly the edges used by one triangle. Both
        windings are normalized to a sorted index pair so an edge shared by two
        triangles cancels regardless of orientation, and what survives with a
        count of one is the boundary.

        *groups* is the shell-id upper bound, so the result is a dense array
        rather than a dict -- ``bincount`` sums per shell in one pass, where a
        Python accumulation loop over border edges measured 70-94x slower on
        20k-200k edges.
        """
        edges = np.concatenate(
            [u_idx[:, [0, 1]], u_idx[:, [1, 2]], u_idx[:, [2, 0]]], axis=0
        )
        owner = np.concatenate([shell, shell, shell])
        keyed = np.sort(edges, axis=1)
        _, inverse, counts = np.unique(
            keyed, axis=0, return_inverse=True, return_counts=True
        )
        # reshape: numpy 1.x returns a flat inverse, some 2.x versions shape it
        # like the input. Maya 2025 ships 1.24 and the dev interpreter 2.4.
        border = counts[inverse.reshape(-1)] == 1
        out = np.zeros(groups, dtype=np.float64)
        if not border.any():
            return out
        pts = uvs[keyed[border]]
        length = np.linalg.norm(pts[:, 1] - pts[:, 0], axis=1)
        return np.bincount(owner[border], weights=length, minlength=groups)

    @classmethod
    def _shell_records(cls, mesh: str) -> List[Dict[str, object]]:
        """Every UV shell of *mesh* as an independent record, nothing collapsed yet.

        Collapsing has to happen a level up (see :meth:`_collapse`), because
        stacking is not a per-mesh phenomenon: 226 decal meshes carrying one
        chart each, all sharing one region of the map, look unstacked to any
        mesh-local test and are the single biggest overcount on real content.

        Each record carries a ``sig`` -- rounded UV bounding box, triangle
        count, rounded UV area -- which is what identifies two shells as
        occupying the same map region.

        The gutter term needs a border length in world units, but a border is
        measured in UV space. The two are related by the shell's own
        parametrization: ``sqrt(area_3d / area_uv)`` is its world-units-per-UV-
        unit scale, so ``perimeter_uv * that`` is the border in world units --
        exact for a conformal shell, and the right magnitude for any real one.
        """
        uvs, u_idx, world_tris, shell = cls._triangles(mesh)
        tris = uvs[u_idx]
        uv_area, world_area = cls._areas(tris, world_tris)

        # Group by shell with one sort instead of a boolean mask per shell: the
        # masked form is O(shells x triangles) and measured 30-47x slower on
        # meshes with hundreds to thousands of islands -- which is exactly the
        # fragmented content this analysis exists to price.
        order = np.argsort(shell, kind="stable")
        ordered = shell[order]
        ids = np.unique(ordered)
        starts = np.searchsorted(ordered, ids)
        counts = np.diff(np.append(starts, len(ordered)))
        perim_uv = cls._perimeters(uvs, u_idx, shell, int(ids.max()) + 1)

        uv_sum = np.add.reduceat(uv_area[order], starts)
        world_sum = np.add.reduceat(world_area[order], starts)
        u_lo = np.minimum.reduceat(tris[:, :, 0].min(axis=1)[order], starts)
        u_hi = np.maximum.reduceat(tris[:, :, 0].max(axis=1)[order], starts)
        v_lo = np.minimum.reduceat(tris[:, :, 1].min(axis=1)[order], starts)
        v_hi = np.maximum.reduceat(tris[:, :, 1].max(axis=1)[order], starts)
        bbox = np.round(
            np.column_stack([u_lo, v_lo, u_hi, v_hi]), STACK_PRECISION
        ).tolist()

        out: List[Dict[str, object]] = []
        for i, shell_id in enumerate(ids.tolist()):
            area_uv = float(uv_sum[i])
            area_3d = float(world_sum[i])
            out.append(
                {
                    "mesh": mesh,
                    "uv": area_uv,
                    "3d": area_3d,
                    "perimeter": (
                        float(perim_uv[shell_id]) * ((area_3d / area_uv) ** 0.5)
                        if area_uv > 0
                        else 0.0
                    ),
                    "sig": (
                        tuple(bbox[i]),
                        int(counts[i]),
                        round(area_uv, 8),
                    ),
                }
            )
        return out

    @staticmethod
    def _collapse(
        records: Sequence[Dict[str, object]], collapse_stacked: bool
    ) -> Tuple[Dict[str, MeshMetrics], float]:
        """Reduce one texture set's shell records to per-mesh metrics.

        Shells sharing a signature share a region of the map, so the map holds
        one of them. The largest 3D area in each group is the one kept, which
        preserves the *lowest*-density member and therefore never under-budgets.

        The surviving copy is charged to ONE mesh -- the one carrying the
        largest 3D area of the group. Totals are exact either way; what this
        settles is which mesh pays. It matters only for ``group_by="mesh"``,
        and there it carries a constraint the plan itself does not express: a
        mesh whose charts are ALL duplicates of another's produces no budget
        item at all, so it costs nothing and is implicitly bound to whichever
        page its twin lands on. **Anything that acts on such a plan has to keep
        stacked meshes together** -- separating them would need a second copy
        of a chart the budget only paid for once. ``group_by="material"``
        cannot hit this, since a stacked group is by definition inside one set.

        Scope is the texture set, deliberately, and it is NOT global: one shared
        signature table across sets makes the result depend on the order sets
        are visited, so a later set can silently lose a chart to an earlier one.
        Stacking is a within-set phenomenon anyway -- two materials cannot share
        a texel.

        Returns:
            tuple: per-mesh metrics, and the set's stack factor (raw 3D area
            over collapsed 3D area -- 2.0 meaning half the surface is stacked).
        """
        raw_3d = sum(float(r["3d"]) for r in records)
        if collapse_stacked:
            # Ties are the NORM, not the exception: stacked shells are usually
            # duplicated geometry, so their 3D areas are equal and a bare `>`
            # leaves the winner to whichever sum happened to land an ULP
            # higher. That is float noise deciding which mesh owns a chart --
            # it moved a mesh between owners when an unrelated summation order
            # changed. Ranking on (area, mesh, bbox) instead makes the choice
            # stable across runs and re-implementations; totals never depended
            # on it, only which mesh pays.
            def rank(record):
                return (float(record["3d"]), str(record["mesh"]), record["sig"][0])

            kept: Dict[object, Dict[str, object]] = {}
            for record in records:
                prior = kept.get(record["sig"])
                if prior is None or rank(record) > rank(prior):
                    kept[record["sig"]] = record
            survivors = list(kept.values())
        else:
            survivors = list(records)

        metrics: Dict[str, MeshMetrics] = {}
        for record in survivors:
            mesh = str(record["mesh"])
            entry = metrics.get(mesh)
            if entry is None:
                entry = metrics[mesh] = MeshMetrics(mesh=mesh)
            entry.area_3d += float(record["3d"])
            entry.area_uv += float(record["uv"])
            entry.perimeter += float(record["perimeter"])
            entry.charts += 1
        collapsed = sum(m.area_3d for m in metrics.values())
        factor = raw_3d / collapsed if collapsed > 0 else 1.0
        for entry in metrics.values():
            entry.stack_factor = factor
        return metrics, factor

    @classmethod
    def _shell_metrics(cls, mesh: str, collapse_stacked: bool = True) -> MeshMetrics:
        """One mesh measured on its own -- the single-mesh case of :meth:`_collapse`.

        The analysis proper never calls this: it collapses per texture set, so
        that stacking spread across meshes is seen. This is for measuring one
        mesh in isolation, which is what a per-mesh caller and the test suite
        want, and it is the same code path with a scope of one.
        """
        metrics, _ = cls._collapse(cls._shell_records(mesh), collapse_stacked)
        return metrics.get(mesh, MeshMetrics(mesh=mesh))

    # ------------------------------------------------------------------ #
    # Scene grouping
    # ------------------------------------------------------------------ #
    @staticmethod
    def _source_map_size(
        materials: Sequence[str], default: int
    ) -> Tuple[int, int, int]:
        """Authored map size across *materials*: ``(size, surface maps, measured)``.

        Read from the images themselves rather than assumed, because the whole
        density target hangs on it: measuring a 2048-textured set as if it were
        4096 reports exactly double the density it has, and a plan built on that
        number asks for four times the map area the content justifies.

        Only maps that actually clothe the surface are counted, classified by
        :meth:`pythontk.MapFactory.resolve_map_type` -- the ecosystem's own
        filename taxonomy, so this agrees with what the transfer and packaging
        steps consider a map. Everything it cannot name is dropped, and that
        exclusion is load-bearing rather than tidy-minded: a Stingray shader
        carries Maya's own IBL presets on every material
        (``specular_cube.dds`` at 256, ``ibl_brdf_lut.png`` at 128), those
        files resolve on any machine while the project's real textures may not,
        and taking the largest readable size over everything therefore reported
        a 4096-textured set as 256 -- a density 16x too low, on the one input
        the entire plan is built from. (Measured on a production assembly: it
        dragged three of seven sets to 1.4 px/unit and the median target from
        ~35 to 11.2.)

        The most common size among the surface maps wins, ties going to the
        larger. A set's maps are normally all one size; a mode tolerates one
        odd mask without letting it decide the answer.

        Returns:
            tuple[int, int, int]: The size (falling back to *default*), how
            many surface maps were found, and how many of those had a readable
            size. ``measured == 0`` with ``surface > 0`` is the diagnostic case
            -- the set has real textures whose files did not resolve, which is
            a very different situation from a set with no textures at all, and
            the caller reports it as such rather than quietly assuming.
        """
        from mayatk.mat_utils._mat_utils import MatUtils

        try:
            entries = MatUtils.get_mat_info(
                materials=list(materials),
                include_textures=True,
                include_image_metadata=True,
            )
        except Exception:
            return default, 0, 0

        surface = 0
        sizes: List[int] = []
        for entry in entries or []:
            for tex in entry.get("textures") or []:
                path = tex.get("path") or tex.get("name") or ""
                try:
                    if not path or not ptk.MapFactory.resolve_map_type(path):
                        continue
                except Exception:
                    continue
                surface += 1
                size = max(tex.get("width") or 0, tex.get("height") or 0)
                if size > 0:
                    sizes.append(size)
        if not sizes:
            return default, surface, 0
        ranked = sorted(set(sizes), key=lambda v: (-sizes.count(v), -v))
        return ranked[0], surface, len(sizes)

    @classmethod
    def _measure_sets(
        cls,
        records: Dict[str, List[Dict[str, object]]],
        default_map_size: int,
        read_textures: bool,
        collapse_stacked: bool,
        warnings: List[str],
    ) -> Tuple[List[TextureSetInfo], Dict[str, MeshMetrics]]:
        """Group meshes by assigned material, then collapse stacking per set.

        The order matters and is the whole point of doing it here: shells are
        deduplicated across every mesh of a set at once, so decals or trim
        pieces spread over hundreds of meshes collapse to the one region of the
        map they actually share. Collapsing per mesh first would have missed
        every one of them.
        """
        from mayatk.mat_utils._mat_utils import MatUtils

        groups = MatUtils.group_objects_by_material(list(records)) or {}
        sets: List[TextureSetInfo] = []
        metrics: Dict[str, MeshMetrics] = {}
        for key, members in groups.items():
            mats = list(key) if isinstance(key, tuple) else [key]
            mine = [m for m in members if m in records]
            grouped, factor = cls._collapse(
                [r for m in mine for r in records[m]], collapse_stacked
            )
            metrics.update(grouped)
            info = TextureSetInfo(
                key=" + ".join(CoreUtils.short_name(m) for m in mats),
                materials=mats,
                meshes=mine,
                stack_factor=factor,
            )
            for mesh in mine:
                m = grouped.get(mesh)
                if m is None:  # every chart of it was a duplicate of another's
                    continue
                info.area_3d += m.area_3d
                info.area_uv += m.area_uv
                info.perimeter += m.perimeter
                info.charts += m.charts
            if info.area_3d <= 0 or info.area_uv <= 0:
                info.note = "no measurable UV or surface area"
                warnings.append(f"{info.key}: {info.note}")
                sets.append(info)
                continue
            if read_textures and key != "None":
                (
                    info.map_size,
                    info.surface_maps,
                    info.measured_maps,
                ) = cls._source_map_size(mats, default_map_size)
                info.map_size_source = "textures" if info.measured_maps else "assumed"
                if not info.measured_maps:
                    warnings.append(
                        f"{info.key}: map size assumed {default_map_size} - "
                        + (
                            f"{info.surface_maps} surface map(s) found but no file "
                            "resolved (set the project, or pass density= directly)"
                            if info.surface_maps
                            else "no surface maps connected"
                        )
                    )
            else:
                info.map_size = default_map_size
            # Same ratio-of-sums form as UvUtils.get_texel_density, so a set
            # measured here and a selection measured there agree exactly.
            info.density = ((info.area_uv / info.area_3d) ** 0.5) * info.map_size
            sets.append(info)
        return sets, metrics

    @staticmethod
    def _target_density(
        sets: Sequence[TextureSetInfo], how: str, warnings: List[str]
    ) -> float:
        """Reduce the measured per-set densities to one target.

        ``min`` never magnifies any set beyond its source resolution, so it is
        the only choice that guarantees no wasted texels -- at the cost of
        softening every set above it. ``max`` preserves the sharpest set and
        buys the maps for it. ``median`` and ``mean`` trade both ways. There is
        no universally right answer, which is why this is a parameter and why
        :attr:`UvBudgetResult.density_spread` is reported beside it.
        """
        d = sorted(s.density for s in sets if s.density > 0)
        if not d:
            return 0.0
        if how == "min":
            return d[0]
        if how == "max":
            return d[-1]
        if how == "mean":
            return sum(d) / len(d)
        if how != "median":
            warnings.append(f"unknown density_from '{how}' - using 'median'")
        mid = len(d) // 2
        return d[mid] if len(d) % 2 else 0.5 * (d[mid - 1] + d[mid])

    @staticmethod
    def _items(
        sets: Sequence[TextureSetInfo],
        metrics: Dict[str, MeshMetrics],
        group_by: str,
        warnings: List[str],
        preserve: bool = False,
    ) -> List[ptk.BudgetItem]:
        """Turn measured sets into the indivisible groups the solver places.

        ``group_by`` is the consequential choice, and it is a pipeline decision
        rather than a technical one:

        - ``"material"`` keeps every existing texture set whole. Nothing has to
          be re-authored, but a set larger than one page forces a bigger map
          rather than a second one.
        - ``"mesh"`` lets one material's meshes spread across maps. That is what
          makes even fills possible on lumpy content, and it is only usable
          downstream if the transfer step can write a mesh's textures to a
          different map than its neighbours -- which is exactly what
          :class:`mayatk.TextureTransfer` does.

        With *preserve*, each item carries the density it was authored at and
        the solve scales all of them together, so a scene whose sets differ by
        11x in density is budgeted at what it actually is rather than flattened
        onto one number that over-serves half of it and starves the rest.
        """
        if group_by == "material":
            return [
                ptk.BudgetItem(
                    key=s.key,
                    area=s.area_3d,
                    perimeter=s.perimeter,
                    charts=s.charts,
                    density=s.density if preserve else 1.0,
                    payload=s,
                )
                for s in sets
            ]
        if group_by != "mesh":
            warnings.append(f"unknown group_by '{group_by}' - using 'mesh'")
        return [
            ptk.BudgetItem(
                key=CoreUtils.short_name(mesh),
                area=metrics[mesh].area_3d,
                perimeter=metrics[mesh].perimeter,
                charts=metrics[mesh].charts,
                # A mesh's own authored density, against its set's map size --
                # not the set average, so one dense mesh in a coarse set is
                # budgeted as the dense thing it is.
                density=(metrics[mesh].density * s.map_size) if preserve else 1.0,
                payload=s.key,
            )
            for s in sets
            for mesh in s.meshes
            if mesh in metrics and metrics[mesh].area_3d > 0
        ]

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #
    @classmethod
    def run(
        cls,
        objects=None,
        map_size: int = 4096,
        density: Optional[float] = None,
        scale: Optional[float] = None,
        pages: Optional[int] = None,
        density_from: str = "preserve",
        group_by: str = "mesh",
        collapse_stacked: bool = True,
        read_textures: bool = True,
        mip_levels: int = 0,
        padding_factor: int = 256,
        fill: Optional[float] = None,
        level: bool = False,
        alternates: bool = True,
    ) -> UvBudgetResult:
        """Measure, then plan. See :meth:`mayatk.UvUtils.analyze_uv_budget`."""
        from mayatk.uv_utils._uv_pack import _UvPackInternal

        start = time.perf_counter()
        result = UvBudgetResult(map_size=map_size, group_by=group_by)

        # Instances collapse here exactly as they do for a real pack: one entry
        # per shape, so N paths onto one UV array claim map space once.
        meshes = [mesh for mesh, _ in _UvPackInternal._resolve_scope(objects)]
        if not meshes:
            result.warnings.append("no meshes in scope")
            result.seconds = time.perf_counter() - start
            return result

        records: Dict[str, List[Dict[str, object]]] = {}
        for mesh in meshes:
            try:
                records[mesh] = cls._shell_records(mesh)
            except Exception as error:
                result.warnings.append(f"{CoreUtils.short_name(mesh)}: {error}")
        if not records:
            result.seconds = time.perf_counter() - start
            return result

        result.sets, metrics = cls._measure_sets(
            records, map_size, read_textures, collapse_stacked, result.warnings
        )
        usable = [s for s in result.sets if s.area_3d > 0 and s.area_uv > 0]
        if not usable:
            result.seconds = time.perf_counter() - start
            return result

        # `density` and `scale` are different questions and must not collapse
        # into one argument: with per-group densities in play, a bare number
        # meaning "64" is either 64 texels per unit or 64 TIMES what the
        # content already has, and the two differ by orders of magnitude with
        # nothing in the result to say which was meant.
        given = [density is not None, scale is not None, pages is not None]
        if sum(given) > 1:
            raise ValueError("pass at most one of 'density', 'scale' or 'pages'")

        if density is not None:  # an absolute texel density flattens the groups
            preserve, target = False, density
            result.density_source = "caller (absolute density)"
        elif scale is not None:  # a multiplier only means anything per group
            preserve, target = True, scale
            result.density_source = "caller (scale on authored density)"
        elif pages is not None:
            preserve, target = density_from == "preserve", None
            result.density_source = f"solved for {pages} map(s)"
        else:
            preserve = density_from == "preserve"
            target = (
                1.0
                if preserve
                else cls._target_density(usable, density_from, result.warnings)
            )
            result.density_source = (
                "each group's authored density (scale 1.0)"
                if preserve
                else f"{density_from} of measured per-set density"
            )
        density = target
        result.target_density = target or 0.0

        result.items = cls._items(
            usable, metrics, group_by, result.warnings, preserve=preserve
        )
        result.plan = ptk.UvBudget.plan(
            result.items,
            map_size=map_size,
            density=density,
            pages=pages,
            factor=padding_factor,
            mip_levels=mip_levels,
            fill=fill,
            level=level,
            alternates=alternates,
        )
        if pages is not None:
            result.target_density = result.plan.chosen.density
        result.seconds = time.perf_counter() - start
        return result
