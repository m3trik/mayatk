# !/usr/bin/python
# coding=utf-8
"""Band-based UV seam placement for cylinder / tube / turned meshes.

Reached through :meth:`mayatk.UvUtils.get_auto_seam_edges` (and from there
``cut_cylinder_seams`` / ``unwrap_cylinder``). The core works on plain arrays
and imports nothing from Maya -- :meth:`_CylinderSeamsInternal.from_mesh` is
the one Maya-facing door -- so the same code is exercised offline against
hand-seamed reference meshes.

The seams reproduce what a texture artist does by hand on a turned or swept
part -- *clean and minimal*:

- The mesh is read as **rings** (edge chains that run around the tube) and
  the quad **bands** between consecutive rings. Non-quad faces (n-gon caps,
  triangle fans) form **cap** regions.
- Every band is classified by how its lengthwise edges sit against the
  band's own axis (the line through its two ring centres, so a bent or
  mitered tube is read locally): a **wall** band runs along the axis (a
  cylinder, a slight taper), everything else -- flat washers / steps,
  chamfers, flares, dome and fillet bands, caps -- is a **flat** band that
  will lay out as a disc or a closed annulus. Two refinements: a *tall*
  steep cone (a funnel, a needle) is a wall, since an annulus can't carry it,
  and a *trim* band (a fillet or bevel a few percent of the radius tall)
  simply follows the neighbour it is most smoothly joined to, so a rounded
  collar rides its strip and a bead ring stays a ring.
- A ring is cut where a wall meets a flat band (a step rim, a chamfer edge,
  a cap rim) -- regardless of how shallow the angle -- and, between two bands
  of the same kind, only at a genuine crease: an authored hard edge or a
  dihedral past ``angle`` (flat-to-flat needs a clearly folded profile, so a
  chamfer running into a step, or a domed cap with its fillet, stays one
  annulus / disc).
- Each connected run of wall bands is opened with **one** lengthwise cut,
  taken from a single edge chain (a *column*) that is followed through the
  whole tube, so every strip's seam lines up (a straight column on a turned
  part; the loop that follows the surface on a bent hose). Flat bands never
  get a lengthwise cut: an annulus / disc unfolds as-is. A run with no open
  end at all (a torus) also gets one crossing ring so it can unroll.
- The column is chosen to hide the seam: the column facing away from the
  viewer (``camera`` -- Maya's default perspective direction when none is
  given, so the pick is deterministic) is used; ``invert_seam`` takes the
  opposite side.

3D boundary edges (an open tube's rims) are already UV borders and are never
cut. Faces that don't fit the band structure (a non-tube appendage, a
region where the quad grid breaks down) fall back to plain crease cuts.
"""
import math
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np

Vec = Tuple[float, float, float]

#: Default generator threshold (degrees): a band whose lengthwise edges tilt
#: from its own axis by more than this is a flat / conical band (annulus),
#: not a wall (strip). Calibrated on hand-seamed references: a ~28 degree
#: chamfer or flare between two cylinders is left as a ring while a slight
#: taper still rides the wall's strip.
DEFAULT_TAPER_ANGLE = 20.0

#: A hard edge whose faces are (near) coplanar carries no crease -- an
#: authored-hard but flat edge (a triangulated cap) must not split anything.
COPLANAR_EPS_DEG = 0.5

#: Same-kind rings between two flat bands need a clearly folded profile before
#: they split (a V-groove, a countersink wall); a 45 degree fillet-to-fillet
#: ring stays merged. Applied on top of ``angle`` -- whichever is larger.
FLAT_FLAT_MIN_CREASE = 60.0

#: A band shorter than this fraction of its ring radius is *trim* (a fillet,
#: a bevel, a bead) and joins a neighbour instead of counting on its own.
TRIM_RATIO = 0.12

#: A conical band taller than this many radii is a strip however steep it is
#: (up to ``TALL_MAX_ANGLE``): a funnel, a needle, a tall flare unrolls as a
#: sector, an annulus can't carry it. From the references: a chamfer 0.65
#: radii tall stays a ring, a flare 1.1 radii tall is cut open.
TALL_RATIO = 1.0
TALL_MAX_ANGLE = 60.0

#: Viewing direction assumed when no camera is given: Maya's default
#: perspective camera looks down (-1, -1, -1), so the seam lands on the
#: (-X, -Y, -Z) side of the mesh in a fresh scene.
DEFAULT_VIEW_DIR = (1.0, 1.0, 1.0)

WALL, CONE, FLAT = 0, 1, 2
RING, LENGTH = 0, 1


class _CylinderSeamsInternal:
    """Band decomposition + seam selection on plain arrays.

    Parameters:
        points: Vertex positions ``[(x, y, z), ...]`` (world space).
        faces: Per-face vertex id lists, in winding order.
        edges: ``[(a, b), ...]`` -- the mesh's edge table. Seam ids returned
            index into this list, so passing Maya's own edge order makes the
            result directly usable as ``mesh.e[i]`` components.
        hard: Edge ids flagged hard (authored shading). Only consulted when
            the mesh also has soft edges -- an all-hard import carries no
            authoring signal and falls back to dihedral angles.
    """

    def __init__(
        self,
        points: Sequence[Vec],
        faces: Sequence[Sequence[int]],
        edges: Sequence[Tuple[int, int]],
        hard: Optional[Iterable[int]] = None,
    ):
        self.pts = np.asarray(points, dtype=float).reshape(-1, 3)
        self.faces = [list(f) for f in faces]
        self.edges = [tuple(e) for e in edges]
        self.hard = set(hard or ())
        n_edges = len(self.edges)
        self.authored = 0 < len(self.hard) < n_edges  # both hard and soft present

        # --- adjacency ------------------------------------------------------
        key = {}
        for i, (a, b) in enumerate(self.edges):
            key[(a, b) if a < b else (b, a)] = i
        self.edge_faces: Dict[int, List[int]] = defaultdict(list)
        self.face_edges: List[List[int]] = []
        for fi, f in enumerate(self.faces):
            fe = []
            for k in range(len(f)):
                a, b = f[k], f[(k + 1) % len(f)]
                e = key[(a, b) if a < b else (b, a)]
                fe.append(e)
                self.edge_faces[e].append(fi)
            self.face_edges.append(fe)
        self.vert_edges: Dict[int, List[int]] = defaultdict(list)
        for i, (a, b) in enumerate(self.edges):
            self.vert_edges[a].append(i)
            self.vert_edges[b].append(i)
        self.boundary = {e for e in range(n_edges) if len(self.edge_faces[e]) < 2}

        # --- face normals (Newell) ---------------------------------------
        self.normals = np.zeros((len(self.faces), 3))
        for fi, f in enumerate(self.faces):
            p = self.pts[f]
            n = np.zeros(3)
            for k in range(len(f)):
                n += np.cross(p[k], p[(k + 1) % len(f)])
            l = np.linalg.norm(n)
            self.normals[fi] = n / l if l > 1e-12 else n

    @classmethod
    def from_mesh(cls, mesh) -> "_CylinderSeamsInternal":
        """Read a Maya mesh (transform or shape name / path) into a seamer.

        Points are world space; the edge table is Maya's own, so the seam ids
        returned by :meth:`seams` are the mesh's ``e[i]`` indices.
        """
        import maya.api.OpenMaya as om

        sel = om.MSelectionList()
        sel.add(str(mesh))
        dag = sel.getDagPath(0)
        dag.extendToShape()
        fn = om.MFnMesh(dag)
        points = [(p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kWorld)]
        faces = [list(fn.getPolygonVertices(f)) for f in range(fn.numPolygons)]
        edges = [tuple(fn.getEdgeVertices(e)) for e in range(fn.numEdges)]
        hard = []
        it = om.MItMeshEdge(dag)
        while not it.isDone():
            if not it.isSmooth:
                hard.append(it.index())
            it.next()
        return cls(points, faces, edges, hard)

    # ------------------------------------------------------------ geometry
    def dihedral(self, e: int) -> Optional[float]:
        """Angle (degrees) between the two face normals at edge ``e``; ``None``
        on a boundary / non-manifold edge."""
        fs = self.edge_faces[e]
        if len(fs) != 2:
            return None
        d = float(np.clip(np.dot(self.normals[fs[0]], self.normals[fs[1]]), -1, 1))
        return math.degrees(math.acos(d))

    def edge_vec(self, e: int) -> np.ndarray:
        a, b = self.edges[e]
        return self.pts[b] - self.pts[a]

    def edge_mid(self, e: int) -> np.ndarray:
        a, b = self.edges[e]
        return (self.pts[a] + self.pts[b]) * 0.5

    def _edge_is_crease(self, e: int, angle: float) -> bool:
        d = self.dihedral(e)
        if d is None:
            return False
        if self.authored and e in self.hard and d > COPLANAR_EPS_DEG:
            return True
        return d >= angle

    # ---------------------------------------------------- ring / column class
    # Each quad has two opposite-edge pairs; on a tube one pair runs around
    # (ring edges), the other along (lengthwise edges). The class is seeded
    # from edges that can only be ring edges -- a cap's rim (quad edges shared
    # with a non-quad face) and an open tube's rim (boundary quad edges) --
    # and propagated across the quad grid: opposite edges of a quad share a
    # class, adjacent edges differ.
    def _classify_edges(self) -> Tuple[Dict[int, int], Set[int]]:
        """Return ``(edge -> RING/LENGTH, irregular quad ids)``."""
        quads = [fi for fi, f in enumerate(self.faces) if len(f) == 4]
        quad_set = set(quads)
        seeds: List[int] = []
        for e, fs in self.edge_faces.items():
            if not any(f in quad_set for f in fs):
                continue
            if any(f not in quad_set for f in fs):  # cap rim
                seeds.append(e)
            elif e in self.boundary:  # open rim
                seeds.append(e)

        assign: Dict[int, int] = {}  # quad -> which pair (0 / 1) is RING
        conflict: Set[int] = set()
        edge_class: Dict[int, int] = {}

        def pair_of(fi: int, e: int) -> int:
            return self.face_edges[fi].index(e) % 2

        def set_quad(fi: int, ring_pair: int) -> None:
            assign[fi] = ring_pair
            fe = self.face_edges[fi]
            for k in range(4):
                cls = RING if k % 2 == ring_pair else LENGTH
                prev = edge_class.get(fe[k])
                if prev is not None and prev != cls:
                    conflict.add(fi)
                    conflict.update(g for g in self.edge_faces[fe[k]] if g in quad_set)
                edge_class[fe[k]] = cls

        def propagate(stack: List[int]) -> None:
            while stack:
                fi = stack.pop()
                for k, e in enumerate(self.face_edges[fi]):
                    cls = RING if k % 2 == assign[fi] else LENGTH
                    for g in self.edge_faces[e]:
                        if g not in quad_set or g == fi:
                            continue
                        want = pair_of(g, e) if cls == RING else (pair_of(g, e) + 1) % 2
                        if g in assign:
                            if assign[g] != want:
                                conflict.add(g)
                                conflict.add(fi)
                            continue
                        set_quad(g, want)
                        stack.append(g)

        def seed(edge_ids: Iterable[int]) -> None:
            stack: List[int] = []
            for e in edge_ids:
                if edge_class.get(e, RING) != RING:
                    continue
                edge_class[e] = RING
                for fi in self.edge_faces[e]:
                    if fi in quad_set and fi not in assign:
                        set_quad(fi, pair_of(fi, e))
                        stack.append(fi)
            propagate(stack)

        seed(seeds)
        # Quads no cap / rim seed reaches (a closed all-quad island such as a
        # torus): seed geometrically, island by island.
        while True:
            unreached = [fi for fi in quads if fi not in assign]
            if not unreached:
                break
            seed(self._geometric_seeds(unreached))
        irregular = set(conflict) | {fi for fi in quads if fi not in assign}
        return edge_class, irregular

    def _geometric_seeds(self, quads: List[int]) -> List[int]:
        """Ring seeds for an all-quad closed island (a torus, a closed
        revolved shape with quad-only poles).

        Both quad-edge families are tried as the ring family; the one whose
        chains are geometrically shorter wins (a torus's minor circles), which
        makes such a shape open with one lengthwise loop plus one crossing
        ring rather than being sliced as a stack of annuli.
        """
        fi = quads[0]
        best: Optional[Tuple[float, int]] = None
        for pair in (0, 1):
            e0 = self.face_edges[fi][pair]
            length = self._loop_length(e0)
            if best is None or length < best[0]:
                best = (length, e0)
        return [best[1]] if best else []

    def _loop_length(self, e0: int) -> float:
        """Geometric length of the edge loop through ``e0`` (walking straight
        through 4-valent vertices)."""
        seen = {e0}
        total = float(np.linalg.norm(self.edge_vec(e0)))
        for start in self.edges[e0]:
            e, v = e0, start
            while True:
                nxt = self._straight_through(e, v)
                if nxt is None or nxt in seen:
                    break
                seen.add(nxt)
                total += float(np.linalg.norm(self.edge_vec(nxt)))
                a, b = self.edges[nxt]
                v = b if a == v else a
                e = nxt
        return total

    def _straight_through(self, e: int, v: int) -> Optional[int]:
        """The edge continuing ``e`` straight through vertex ``v`` (the one
        sharing no face with ``e``), or ``None`` off a 4-valent vertex."""
        cand = [x for x in self.vert_edges[v] if x != e]
        if len(cand) != 3:
            return None
        fs = set(self.edge_faces[e])
        nxt = [x for x in cand if not (set(self.edge_faces[x]) & fs)]
        return nxt[0] if len(nxt) == 1 else None

    # ------------------------------------------------------ chains (rings)
    def _chains(self, edge_ids: Iterable[int]) -> List[List[int]]:
        """Split a set of edges into vertex-connected chains, following each
        chain through vertices that carry exactly two of the set's edges.
        Vertices with more than two (a junction) end the chain there."""
        ids = set(edge_ids)
        at: Dict[int, List[int]] = defaultdict(list)
        for e in ids:
            for v in self.edges[e]:
                at[v].append(e)
        seen: Set[int] = set()
        chains: List[List[int]] = []
        for e0 in sorted(ids):
            if e0 in seen:
                continue
            chain = [e0]
            seen.add(e0)
            for start in self.edges[e0]:
                e, v = e0, start
                while True:
                    here = at[v]
                    if len(here) != 2:
                        break
                    nxt = here[0] if here[1] == e else here[1]
                    if nxt in seen:
                        break
                    seen.add(nxt)
                    chain.append(nxt)
                    a, b = self.edges[nxt]
                    v = b if a == v else a
                    e = nxt
            chains.append(chain)
        return chains

    def _chain_verts(self, chain: List[int]) -> List[int]:
        vs: List[int] = []
        for e in chain:
            for v in self.edges[e]:
                if v not in vs:
                    vs.append(v)
        return vs

    def _chain_closed(self, chain: List[int]) -> bool:
        deg: Dict[int, int] = defaultdict(int)
        for e in chain:
            for v in self.edges[e]:
                deg[v] += 1
        return len(chain) > 2 and all(d == 2 for d in deg.values())

    def _plane_normal(self, vids: List[int]) -> np.ndarray:
        """Best-fit plane normal of a vertex set (smallest covariance axis)."""
        p = self.pts[vids]
        if len(vids) < 3:
            return np.zeros(3)
        c = p - p.mean(axis=0)
        _, vecs = np.linalg.eigh(c.T @ c)
        return vecs[:, 0]

    # ------------------------------------------------------------ main
    def seams(
        self,
        angle: float = 45.0,
        taper_angle: float = DEFAULT_TAPER_ANGLE,
        invert_seam: bool = False,
        camera: Optional[Vec] = None,
    ) -> Set[int]:
        """Edge ids to cut. See the module docstring for the rules.

        Parameters:
            angle: Crease threshold (degrees) for rings between two bands of
                the same kind (and for edges of irregular regions).
            taper_angle: Generator threshold (degrees) separating wall bands
                (strips) from flat / conical bands (annuli).
            invert_seam: Put the lengthwise seam on the side facing the
                viewer instead of away from it.
            camera: Viewer eye position; ``None`` assumes Maya's default
                perspective direction.
        """
        edge_class, irregular = self._classify_edges()
        quads = [fi for fi, f in enumerate(self.faces) if len(f) == 4]

        ring_edges = {e for e, c in edge_class.items() if c == RING}
        length_edges = {e for e, c in edge_class.items() if c == LENGTH}
        rings = self._chains(ring_edges)
        ring_of: Dict[int, int] = {}
        for ri, ch in enumerate(rings):
            for e in ch:
                ring_of[e] = ri

        # --- bands: regular quads grouped by their two rings ---------------
        bands: Dict[Tuple[int, int], List[int]] = defaultdict(list)
        for fi in quads:
            if fi in irregular:
                continue
            rs = sorted({ring_of[e] for e in self.face_edges[fi] if e in ring_of})
            if len(rs) != 2:
                irregular.add(fi)
                continue
            bands[(rs[0], rs[1])].append(fi)
        band_keys = sorted(bands)
        band_of_face: Dict[int, int] = {}
        for bi, k in enumerate(band_keys):
            for fi in bands[k]:
                band_of_face[fi] = bi

        # --- cap regions: connected non-quad faces ------------------------
        cap_of_face: Dict[int, int] = {}
        caps: List[List[int]] = []
        seen: Set[int] = set()
        for f0 in (fi for fi, f in enumerate(self.faces) if len(f) != 4):
            if f0 in seen:
                continue
            comp, stack = [], [f0]
            seen.add(f0)
            while stack:
                fi = stack.pop()
                comp.append(fi)
                for e in self.face_edges[fi]:
                    for g in self.edge_faces[e]:
                        if g not in seen and len(self.faces[g]) != 4:
                            seen.add(g)
                            stack.append(g)
            for fi in comp:
                cap_of_face[fi] = len(caps)
            caps.append(comp)

        # --- band geometry: generator angle, height, radius ----------------
        ring_verts = [self._chain_verts(ch) for ch in rings]
        ring_center = [self.pts[vs].mean(axis=0) for vs in ring_verts]
        ring_radius = [
            float(np.linalg.norm(self.pts[vs] - c, axis=1).mean())
            for vs, c in zip(ring_verts, ring_center)
        ]
        band_length_edges: List[Set[int]] = []
        band_gen: List[float] = []
        band_size: List[float] = []  # height over ring radius
        for k in band_keys:
            r1, r2 = k
            ledges = {
                e
                for fi in bands[k]
                for e in self.face_edges[fi]
                if edge_class.get(e) == LENGTH
            }
            band_length_edges.append(ledges)
            vecs = [self.edge_vec(e) for e in ledges]
            height = float(np.mean([np.linalg.norm(v) for v in vecs])) if vecs else 0.0
            radius = 0.5 * (ring_radius[r1] + ring_radius[r2])
            band_size.append(height / radius if radius > 1e-9 else float("inf"))
            # Band axis: the line through the two ring centres; a washer's
            # centres coincide, so fall back to its (shared) ring plane normal.
            axis = ring_center[r2] - ring_center[r1]
            if np.linalg.norm(axis) < 0.2 * height:
                n1 = self._plane_normal(ring_verts[r1])
                n2 = self._plane_normal(ring_verts[r2])
                axis = n1 + (n2 if np.dot(n1, n2) >= 0 else -n2)
            l = np.linalg.norm(axis)
            if l < 1e-12 or not vecs:
                band_gen.append(90.0)
                continue
            axis = axis / l
            cos_mean = float(
                np.mean(
                    [abs(np.dot(v, axis)) / (np.linalg.norm(v) or 1.0) for v in vecs]
                )
            )
            band_gen.append(math.degrees(math.acos(min(1.0, cos_mean))))

        # --- band kinds ------------------------------------------------------
        # WALL: cylinder-like, merges with neighbouring walls across smooth
        # rings into one strip. CONE: a tall steep cone -- a strip too, but a
        # sector on its own (it never merges). FLAT: annulus / disc. Trim
        # bands (None) join a neighbour below.
        band_kind: List[Optional[int]] = []
        for gen, size in zip(band_gen, band_size):
            if size < TRIM_RATIO:
                band_kind.append(None)
            elif gen <= taper_angle:
                band_kind.append(WALL)
            elif size > TALL_RATIO and gen < TALL_MAX_ANGLE:
                band_kind.append(CONE)
            else:
                band_kind.append(FLAT)

        bands_at_ring: Dict[int, List[int]] = defaultdict(list)
        for bi, (r1, r2) in enumerate(band_keys):
            bands_at_ring[r1].append(bi)
            bands_at_ring[r2].append(bi)
        caps_at_ring: Dict[int, Set[int]] = defaultdict(set)
        for ci, cap in enumerate(caps):
            for fi in cap:
                for e in self.face_edges[fi]:
                    if e in ring_of:
                        caps_at_ring[ring_of[e]].add(ci)

        def ring_dihedral(ri: int) -> float:
            dih = [d for d in (self.dihedral(e) for e in rings[ri]) if d is not None]
            return sum(dih) / len(dih) if dih else 180.0

        def ring_is_crease(ch: List[int], min_angle: float) -> bool:
            dih = [d for d in (self.dihedral(e) for e in ch) if d is not None]
            if not dih:
                return False
            mean = sum(dih) / len(dih)
            if self.authored:
                n_hard = sum(1 for e in ch if e in self.hard)
                if n_hard * 2 > len(ch) and mean > COPLANAR_EPS_DEG:
                    return True
            return mean >= min_angle

        # Trim bands join a neighbour, taking its kind; the ring between them
        # is never cut. A wall neighbour is preferred (a fillet is the
        # rounding of the wall's edge -- it rides the strip and the seam hides
        # in the crease beyond it), the smoother one if both sides are walls;
        # otherwise the neighbour across the smoothest ring. A join never
        # crosses a sharp crease (a bead between two hard rings stays its own
        # ring), and a trim band waits for its trim neighbours to resolve
        # before settling for a flat side, so a fillet chain reaches its wall.
        joined: Set[Tuple[int, int]] = set()  # (ring, band) pairs never cut
        sharp = max(angle, FLAT_FLAT_MIN_CREASE)
        pending = [bi for bi, kind in enumerate(band_kind) if kind is None]
        while pending:
            progressed = False
            for bi in list(pending):
                options: List[Tuple[int, float, int, int]] = []
                unresolved = False
                for r in band_keys[bi]:
                    if ring_is_crease(rings[r], sharp):
                        continue
                    for nb in bands_at_ring[r]:
                        if nb == bi:
                            continue
                        if band_kind[nb] is None:
                            unresolved = True
                        else:
                            rank = 0 if band_kind[nb] == WALL else 1
                            options.append((rank, ring_dihedral(r), band_kind[nb], r))
                    if caps_at_ring.get(r):
                        options.append((1, ring_dihedral(r), FLAT, r))
                if not options:
                    continue
                best = min(options)
                if best[0] != 0 and unresolved:
                    continue  # a trim neighbour may still bring a wall
                _, _, kind, r = best
                band_kind[bi] = kind
                joined.add((r, bi))
                pending.remove(bi)
                progressed = True
            if not progressed:
                # Only trim (or crease-locked) neighbours left: settle for the
                # smoothest resolved side, else stand alone as a ring.
                for bi in list(pending):
                    options = []
                    for r in band_keys[bi]:
                        if ring_is_crease(rings[r], sharp):
                            continue
                        for nb in bands_at_ring[r]:
                            if nb != bi and band_kind[nb] is not None:
                                options.append((ring_dihedral(r), band_kind[nb], r))
                        if caps_at_ring.get(r):
                            options.append((ring_dihedral(r), FLAT, r))
                    if options:
                        _, kind, r = min(options)
                        band_kind[bi] = kind
                        joined.add((r, bi))
                    else:
                        band_kind[bi] = FLAT
                    pending.remove(bi)

        # --- ring cut decisions -------------------------------------------
        cuts: Set[int] = set()

        def region(fi: int):
            if fi in band_of_face:
                return ("b", band_of_face[fi]), band_kind[band_of_face[fi]]
            if fi in cap_of_face:
                return ("c", cap_of_face[fi]), FLAT
            return ("i", fi), None

        for ri, ch in enumerate(rings):
            sides, kinds = set(), set()
            for e in ch:
                for fi in self.edge_faces[e]:
                    rid, kind = region(fi)
                    sides.add(rid)
                    kinds.add(kind)
            if len(sides) < 2:
                continue  # rim of an open tube, or inside one region
            if None in kinds or len(sides) > 2:
                # Touches an irregular region, or a junction of 3+ regions:
                # plain crease rule per edge.
                cuts.update(e for e in ch if self._edge_is_crease(e, angle))
                continue
            if any((ri, bi) in joined for bi in bands_at_ring[ri]):
                continue  # a trim band and the neighbour it joined
            if len(kinds) == 2 or CONE in kinds:
                cuts.update(ch)  # kinds differ, or a cone: always a seam
                continue
            kind = next(iter(kinds))
            min_angle = angle if kind == WALL else max(angle, FLAT_FLAT_MIN_CREASE)
            if ring_is_crease(ch, min_angle):
                cuts.update(ch)

        # --- irregular faces: crease rule on every edge they touch ---------
        for fi in irregular:
            for e in self.face_edges[fi]:
                if e not in cuts and self._edge_is_crease(e, angle):
                    cuts.add(e)
        # Inside a cap (non-quad next to non-quad) only a folded profile splits.
        for cap in caps:
            cap_set = set(cap)
            for fi in cap:
                for e in self.face_edges[fi]:
                    fs = self.edge_faces[e]
                    if len(fs) == 2 and all(g in cap_set for g in fs):
                        if self._edge_is_crease(e, max(angle, FLAT_FLAT_MIN_CREASE)):
                            cuts.add(e)

        # --- strip runs + one lengthwise column -----------------------------
        # A run is a set of wall / cone bands connected through uncut rings
        # (a cone only ever connects to the trim it absorbed).
        parent = list(range(len(band_keys)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for ri, bis in bands_at_ring.items():
            if rings[ri][0] in cuts:
                continue
            strips = [b for b in bis if band_kind[b] in (WALL, CONE)]
            for b in strips[1:]:
                ra, rb = find(strips[0]), find(b)
                if ra != rb:
                    parent[ra] = rb
        runs: Dict[int, List[int]] = defaultdict(list)
        for bi in range(len(band_keys)):
            if band_kind[bi] in (WALL, CONE):
                runs[find(bi)].append(bi)

        columns = self._chains(length_edges)
        col_of: Dict[int, int] = {}
        for ci, ch in enumerate(columns):
            for e in ch:
                col_of[e] = ci
        col_score = self._column_scores(columns, camera, invert_seam)

        for run_bands in sorted(runs.values(), key=min):
            run_rings = {r for bi in run_bands for r in band_keys[bi]}
            # A run with an open ring (a half pipe) already has a lengthwise
            # border and needs no cut.
            if any(not self._chain_closed(rings[r]) for r in run_rings):
                continue
            edges_in_run = set().union(*(band_length_edges[bi] for bi in run_bands))
            cols = {col_of[e] for e in edges_in_run if e in col_of}
            if not cols:
                continue
            best = min(cols, key=lambda c: (col_score[c], c))
            cuts.update(set(columns[best]) & edges_in_run)
            # No cut ring and no rim anywhere along the run (a torus): one
            # crossing ring lets it unroll.
            if not any(
                rings[r][0] in cuts or len(bands_at_ring[r]) < 2 for r in run_rings
            ):
                cuts.update(rings[min(run_rings)])
        return cuts

    def _column_scores(self, columns, camera, invert_seam) -> List[float]:
        """Per column, how much it faces the viewer (lower = more hidden).

        The mean, over the column's edges, of the edge normal dotted with the
        direction to the eye. ``camera=None`` places the eye infinitely far
        along :data:`DEFAULT_VIEW_DIR`; ``invert_seam`` negates the score so
        the column facing the viewer wins instead.
        """
        if camera is None:
            far = 1e6 * (1.0 + float(np.abs(self.pts).max()))
            cam = self.pts.mean(axis=0) + np.asarray(DEFAULT_VIEW_DIR) * far
        else:
            cam = np.asarray(camera, dtype=float)
        scores: List[float] = []
        for ch in columns:
            s = 0.0
            for e in ch:
                n = self.normals[self.edge_faces[e]].mean(axis=0)
                to_cam = cam - self.edge_mid(e)
                l = np.linalg.norm(to_cam)
                if l > 1e-12:
                    s += float(np.dot(n, to_cam / l))
            scores.append(s / max(1, len(ch)))
        return [-s for s in scores] if invert_seam else scores
