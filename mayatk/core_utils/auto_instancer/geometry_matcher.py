# !/usr/bin/python
# coding=utf-8
"""Geometry analysis and matching logic for AutoInstancer."""
from __future__ import annotations

import logging
from typing import Optional, Tuple, Union
import numpy as np
from scipy.spatial import KDTree

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError:
    pass

import pythontk as ptk
from mayatk.core_utils._core_utils import CoreUtils, leaf_name, get_bounding_box
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.xform_utils._xform_utils import get_translation, get_object_matrix

logger = logging.getLogger(__name__)


def _mfn(shape):
    """Return an ``MFnMesh`` for *shape* via the canonical CoreUtils helper."""
    return CoreUtils.get_mfn_mesh(shape, api_version=2)


def mesh_points(shape, world: bool = False):
    """``MPointArray`` for *shape*. Object space by default."""
    space = om.MSpace.kWorld if world else om.MSpace.kObject
    return _mfn(shape).getPoints(space)


def mesh_triangles(shape):
    """``(counts, indices)`` from ``MFnMesh.getTriangles``, as plain lists."""
    counts, indices = _mfn(shape).getTriangles()
    return list(counts), list(indices)


def mesh_uv_set_names(shape):
    return list(_mfn(shape).getUVSetNames())


def mesh_get_uvs(shape, uv_set=None):
    fn = _mfn(shape)
    return fn.getUVs(uv_set) if uv_set is not None else fn.getUVs()


def mesh_num_uvs(shape, uv_set=None):
    fn = _mfn(shape)
    return fn.numUVs(uv_set) if uv_set is not None else fn.numUVs()


def _np_to_mmatrix(m: np.ndarray) -> "om.MMatrix":
    """Build an ``om.MMatrix`` from a 4x4 numpy array.

    Maya stores matrices in row-major order with translation in row 3,
    matching Maya's native layout. ``om.MMatrix(iterable)`` consumes 16
    floats in the same order, so ``ndarray.flatten()`` is a direct mapping.
    """
    return om.MMatrix(m.flatten().tolist())


def _is_mesh_shape(shape: Optional[str]) -> bool:
    if not shape:
        return False
    try:
        return cmds.objectType(shape) == "mesh"
    except Exception:
        return False


def calculate_mesh_volume(node: str) -> float:
    """Calculate mesh volume using the divergence theorem (numpy)."""
    try:
        if cmds.objectType(node) == "transform":
            shape = NodeUtils.get_shape(node)
        else:
            shape = node

        if not _is_mesh_shape(shape):
            return 0.0

        # MFnMesh.getTriangles -> (counts, indices). Indices are flat triplets.
        _, indices = mesh_triangles(shape)
        pts = mesh_points(shape, world=True)
        points = np.array([(p.x, p.y, p.z) for p in pts])

        if not indices or len(indices) % 3 != 0:
            return 0.0

        tris = np.array(indices).reshape(-1, 3)
        v1 = points[tris[:, 0]]
        v2 = points[tris[:, 1]]
        v3 = points[tris[:, 2]]

        cross = np.cross(v2, v3)
        dot = np.sum(v1 * cross, axis=1)
        return float(np.abs(np.sum(dot)) / 6.0)
    except Exception:
        return 0.0


class ShellInfo:
    """Stores cached analysis data for a single shell."""

    def __init__(self, node: str):
        self.node = node
        self.name = leaf_name(node)

        shape = NodeUtils.get_shape(node)
        if shape and _is_mesh_shape(shape):
            self.num_verts = cmds.polyEvaluate(shape, vertex=True) or 0
            self.num_faces = cmds.polyEvaluate(shape, face=True) or 0
        else:
            self.num_verts = 0
            self.num_faces = 0

        self.bbox = get_bounding_box(node, world=True)
        self.min_pt = self.bbox.min
        self.max_pt = self.bbox.max
        self.centroid = self.bbox.center
        self.size = self.bbox.size
        self.volume = self.size.x * self.size.y * self.size.z
        self.diagonal = self.bbox.diagonal

        # Accurate Volume (Rotation Invariant)
        self.mesh_volume = calculate_mesh_volume(node)

        # Surface Area (Rotation Invariant, fallback for open meshes)
        try:
            area = cmds.polyEvaluate(node, area=True)
            if isinstance(area, (list, tuple)):
                area = area[0]
            self.mesh_area = float(area)
        except Exception:
            self.mesh_area = 0.0

    def __repr__(self):
        return f"<Shell {self.name} v={self.num_verts} vol={self.volume:.2f}>"


class GeometryMatcher:
    """Handles geometric analysis and comparison."""

    def __init__(
        self,
        tolerance: float = 0.001,
        scale_tolerance: float = 0.0,
        uv_tolerance: float = 0.001,
        require_same_material: Union[bool, int] = True,
        check_uvs: bool = False,
        verbose: bool = False,
    ):
        self.tolerance = tolerance
        self.scale_tolerance = scale_tolerance
        self.uv_tolerance = uv_tolerance
        self.require_same_material = require_same_material
        self.check_uvs = check_uvs
        self.verbose = verbose
        # Discovery-phase caches — valid while the scene is static. Callers
        # that mutate geometry between comparison batches must clear_cache().
        self._points_cache: dict = {}
        self._normals_cache: dict = {}
        self._pair_cache: dict = {}

    def clear_cache(self) -> None:
        """Drop cached point arrays and pair results (call after scene edits).

        Hierarchy comparison re-visits the same shape pairs many times
        (child pairing retries, transform-compatibility re-checks), and each
        uncached robust comparison is a full PCA alignment — the caches turn
        that repeated work into dict lookups.
        """
        self._points_cache.clear()
        self._normals_cache.clear()
        self._pair_cache.clear()

    def _object_points(self, shape: str) -> np.ndarray:
        """Cached object-space points for *shape* as an (N, 3) array."""
        pts = self._points_cache.get(shape)
        if pts is None:
            mpts = mesh_points(shape, world=False)
            pts = np.array([(p.x, p.y, p.z) for p in mpts])
            self._points_cache[shape] = pts
        return pts

    def _object_normals(self, shape: str) -> Optional[np.ndarray]:
        """Cached object-space per-vertex averaged normals as an (N, 3) array.

        Used to verify that a geometric match also aligns shading — point
        positions alone cannot distinguish a symmetric shape from its flipped
        twin (a flat plate maps onto itself under a 180° flip while its
        normals invert).
        """
        if shape in self._normals_cache:
            return self._normals_cache[shape]
        normals = None
        try:
            sel = om.MSelectionList()
            sel.add(shape)
            fn = om.MFnMesh(sel.getDagPath(0))
            # getNormals() is the SHADING truth (includes locked/user
            # normals); getVertexNormals() averages geometric face normals
            # and silently ignores custom shading. Average the face-vertex
            # shading normals per vertex.
            _, norm_ids = fn.getNormalIds()
            _, verts = fn.getVertices()
            arr = np.array(
                [(n.x, n.y, n.z) for n in fn.getNormals(om.MSpace.kObject)]
            )
            acc = np.zeros((fn.numVertices, 3))
            np.add.at(acc, np.array(list(verts)), arr[np.array(list(norm_ids))])
            lengths = np.linalg.norm(acc, axis=1)
            nz = lengths > 1e-9
            acc[nz] /= lengths[nz, None]
            # Fully-cancelling vertices stay zero — their dot contributes
            # rejection, which is correct (contradictory shading).
            normals = acc
        except Exception:
            pass
        self._normals_cache[shape] = normals
        return normals

    def quantize(self, value: float, precision: int = 4) -> float:
        """Round a value to a specific precision to ignore float noise."""
        if value == 0.0:
            return 0.0
        return round(value, precision)

    def get_pca_basis(self, node: str) -> Optional["om.MMatrix"]:
        """Returns the PCA basis matrix (rotation only) for the node's mesh.

        The frame is stabilized so identical geometry always yields the same
        basis (see ``_stabilize_axes``) — without this, copies canonicalize
        to different local point sets and never match cheaply.
        """
        shape = NodeUtils.get_shape(node)
        if not _is_mesh_shape(shape):
            return None

        try:
            pts = mesh_points(shape, world=True)
            points = np.array([(p.x, p.y, p.z) for p in pts])
            flat = ptk.PointCloud.pca_basis(points)
            if flat is None:
                return None
            return _np_to_mmatrix(np.array(flat, dtype=float).reshape(4, 4))
        except Exception:
            return None

    def get_mesh_signature(self, transform: str) -> Optional[Tuple]:
        """Lightweight signature for quick rejection.

        Returns ``(verts, edges, faces, pca_sig, materials, uv_signature)``,
        or ``None`` when *transform* has no shape. Surface area is
        deliberately absent — it is not scale invariant.
        """
        mesh = NodeUtils.get_shape(transform)
        if not mesh:
            return None

        num_verts = cmds.polyEvaluate(mesh, vertex=True) or 0
        num_edges = cmds.polyEvaluate(mesh, edge=True) or 0
        num_faces = cmds.polyEvaluate(mesh, face=True) or 0

        # PCA Signature (Eigenvalues)
        pca_sig = ()
        # If scale tolerance is enabled, PCA eigenvalues (shape descriptors) are unreliable
        # because we can't easily normalize for non-uniform scale without full alignment.
        # We rely on topological counts and the detailed check in are_meshes_identical.
        if self.scale_tolerance <= 0:
            try:
                pts = mesh_points(mesh, world=False)
                points = np.array([(p.x, p.y, p.z) for p in pts])
                pca_sig = ptk.PointCloud.pca_eigenvalue_signature(points, 3)
            except Exception as e:
                if self.verbose:
                    logger.debug(f"PCA failed for {transform}: {e}")

        materials = ()
        if self.require_same_material:
            sgs = cmds.listConnections(mesh, type="shadingEngine") or []
            materials = tuple(sorted(set(leaf_name(sg) for sg in sgs)))

        uv_signature = ()
        if self.check_uvs:
            uv_sets = mesh_uv_set_names(mesh)
            uv_counts = []
            for uv_set in uv_sets:
                try:
                    uv_counts.append(mesh_num_uvs(mesh, uv_set))
                except TypeError:
                    uv_counts.append(mesh_num_uvs(mesh))
            uv_signature = (tuple(uv_sets), tuple(uv_counts))

        return (
            num_verts,
            num_edges,
            num_faces,
            pca_sig,
            materials,
            uv_signature,
        )

    def are_meshes_identical(
        self, t1: str, t2: str
    ) -> Tuple[bool, Optional["om.MMatrix"]]:
        """Detailed geometric comparison using robust PCA alignment.

        Results are memoized per shape pair (see ``clear_cache``) — hierarchy
        comparison re-visits the same pairs many times.

        Returns:
            (is_identical, relative_transform_matrix)
        """
        m1 = NodeUtils.get_shape(t1)
        m2 = NodeUtils.get_shape(t2)
        if not m1 or not m2:
            return False, None

        key = (m1, m2)
        cached = self._pair_cache.get(key)
        if cached is not None:
            return cached

        result = self._are_meshes_identical_uncached(m1, m2, t1, t2)
        self._pair_cache[key] = result
        return result

    # Minimum mean normal dot product for a match to count as shading-
    # compatible. Identical copies score ~1.0; a flipped symmetric twin
    # scores ~-1.0.
    NORMAL_AGREEMENT_THRESHOLD = 0.8

    def _are_meshes_identical_uncached(
        self, m1: str, m2: str, t1: str, t2: str
    ) -> Tuple[bool, Optional["om.MMatrix"]]:
        """Delegate the 3-stage compare to ``ptk.PointCloud.match_clouds``.

        The whole verification pipeline (fast ordered → unordered KDTree
        identity with K-twin normal gating → RMS-uniform-scale + robust PCA
        with the flip-free normal gate) is the shared DCC-neutral
        implementation; this adapter only extracts Maya data and converts
        the returned row-major matrix to ``om.MMatrix``. The UV check is
        injected as a lazy callback so it fires exactly where it used to
        (on a stage-1/2 positional success, hard-rejecting on mismatch).
        """
        uvs_identical = None
        if self.check_uvs:
            uvs_identical = lambda: self._are_uvs_identical(m1, m2)  # noqa: E731

        matched, matrix_list = ptk.PointCloud.match_clouds(
            self._object_points(m1),
            self._object_points(m2),
            tolerance=self.tolerance,
            scale_tolerance=self.scale_tolerance,
            normals_a=self._object_normals(m1),
            normals_b=self._object_normals(m2),
            normal_threshold=self.NORMAL_AGREEMENT_THRESHOLD,
            uvs_identical=uvs_identical,
        )
        if not matched:
            if self.verbose:
                logger.debug(f"No geometric match for {t1} vs {t2}")
            return False, None
        if matrix_list is None:
            return True, None
        return True, _np_to_mmatrix(np.array(matrix_list, dtype=float).reshape(4, 4))

    def _are_uvs_identical(self, m1: str, m2: str) -> bool:
        """Compare UVs of two meshes (assumes identical vertex order)."""
        sets1 = mesh_uv_set_names(m1)
        sets2 = mesh_uv_set_names(m2)

        if set(sets1) != set(sets2):
            return False

        for uv_set in sets1:
            u1, v1 = mesh_get_uvs(m1, uv_set)
            u2, v2 = mesh_get_uvs(m2, uv_set)

            if len(u1) != len(u2):
                return False

            if not np.allclose(u1, u2, atol=self.uv_tolerance) or not np.allclose(
                v1, v2, atol=self.uv_tolerance
            ):
                return False

        return True

    def get_hierarchy_signature(self, node: str) -> Tuple:
        """Recursive signature generation for hierarchy comparison."""
        # 1. Geometry signature (if a mesh shape is present)
        shape = NodeUtils.get_shape(node)
        geo_sig = None
        if shape and _is_mesh_shape(shape) and not NodeUtils.is_intermediate(shape):
            geo_sig = self.get_mesh_signature(node)

        # 2. Children
        children = NodeUtils.get_children(node, type="transform")
        child_sigs = []

        for child in children:
            c_sig = self.get_hierarchy_signature(child)

            # Use distance for rotation invariance
            dist = get_translation(child, world=False).length()
            dist_sig = self.quantize(dist, 2)

            if self.scale_tolerance > 0:
                dist_sig = 0.0

            child_sigs.append((c_sig, dist_sig))

        child_sigs.sort(key=lambda x: str(x))

        return (geo_sig, tuple(child_sigs))

    def _is_matrix_close(self, m1, m2):
        """Checks if two matrices are equivalent within tolerance."""
        if m1 is None and m2 is None:
            return True
        if m1 is None:
            m1 = om.MMatrix()
        if m2 is None:
            m2 = om.MMatrix()
        return m1.isEquivalent(m2, self.tolerance)

    def are_meshes_identical_with_transform(self, t1: str, t2: str, matrix) -> bool:
        """Check if t1 transformed by matrix matches t2."""
        m1 = NodeUtils.get_shape(t1)
        m2 = NodeUtils.get_shape(t2)
        if not m1 or not m2:
            return False

        pts1 = self._object_points(m1)
        pts2 = self._object_points(m2)

        if len(pts1) != len(pts2):
            return False
        if len(pts1) == 0:
            # match_clouds declares two empty clouds identical (stage-1
            # short-circuit) — honor the same convention instead of crashing
            # the KDTree query on a zero-size array.
            return True

        # Identity if matrix is None or an MMatrix-equivalent identity
        if matrix is None:
            mat_arr = np.eye(4)
        elif isinstance(matrix, np.ndarray):
            mat_arr = matrix
        else:
            # om.MMatrix supports getElement(r, c)
            mat_arr = np.array(
                [[matrix.getElement(r, c) for c in range(4)] for r in range(4)]
            )

        # Local matrices to transform points to parent space
        m1_om = get_object_matrix(t1, world=False)
        m2_om = get_object_matrix(t2, world=False)
        mat1 = np.array(
            [[m1_om.getElement(r, c) for c in range(4)] for r in range(4)]
        )
        mat2 = np.array(
            [[m2_om.getElement(r, c) for c in range(4)] for r in range(4)]
        )

        ones = np.ones((len(pts1), 1))
        pts1_h = np.hstack([pts1, ones])
        pts1_parent = np.dot(pts1_h, mat1)  # (N, 4)

        pts1_target = np.dot(pts1_parent, mat_arr)[:, :3]

        pts2_h = np.hstack([pts2, ones])
        pts2_parent = np.dot(pts2_h, mat2)[:, :3]

        tree = KDTree(pts2_parent)
        dists, _ = tree.query(pts1_target, k=1)
        max_dist = np.max(dists)

        return max_dist <= self.tolerance

    def are_hierarchies_identical(
        self,
        t1: str,
        t2: str,
        expected_transform: Optional["om.MMatrix"] = None,
        is_root: bool = False,
    ) -> Tuple[bool, Optional["om.MMatrix"]]:
        """Detailed hierarchy comparison. Returns (is_identical, relative_transform)."""
        # 1. Mesh identity (if present)
        s1 = NodeUtils.get_shape(t1)
        s2 = NodeUtils.get_shape(t2)

        has_mesh1 = s1 and _is_mesh_shape(s1) and not NodeUtils.is_intermediate(s1)
        has_mesh2 = s2 and _is_mesh_shape(s2) and not NodeUtils.is_intermediate(s2)

        if has_mesh1 != has_mesh2:
            return False, None

        relative_transform = expected_transform
        transform_determined = expected_transform is not None

        if has_mesh1:
            if transform_determined:
                if not self.are_meshes_identical_with_transform(
                    t1, t2, relative_transform
                ):
                    if self.verbose:
                        logger.debug(
                            f"Mesh mismatch with expected transform for {t1} vs {t2}"
                        )
                    return False, None
            else:
                is_identical, rel_mtx = self.are_meshes_identical(t1, t2)
                if not is_identical:
                    return False, None

                # Verify the found transform actually aligns geometry in parent
                # space. Catches cases where shapes match but local transforms
                # differ (scale/rotation). For roots we expect different
                # locations, so skip — trust the shape-level check.
                if not is_root and not self.are_meshes_identical_with_transform(
                    t1, t2, rel_mtx
                ):
                    if self.verbose:
                        logger.debug(
                            f"Transform found by shape match does not align parent-space geometry for {t1} vs {t2}"
                        )
                    return False, None

                relative_transform = rel_mtx
                transform_determined = True

        # 2. Children
        children1 = NodeUtils.get_children(t1, type="transform")
        children2 = NodeUtils.get_children(t2, type="transform")

        if len(children1) != len(children2):
            return False, None

        # Sort children by distance from parent and mesh complexity
        def get_sort_key(node: str):
            dist = get_translation(node, world=False).length()
            mesh_sig = (0, 0, 0)
            shape = NodeUtils.get_shape(node)
            if shape and _is_mesh_shape(shape):
                mesh_sig = (
                    cmds.polyEvaluate(shape, vertex=True) or 0,
                    cmds.polyEvaluate(shape, edge=True) or 0,
                    cmds.polyEvaluate(shape, face=True) or 0,
                )
            if self.scale_tolerance > 0:
                return (0.0, mesh_sig)
            return (round(dist, 3), mesh_sig)

        children1.sort(key=get_sort_key)
        children2.sort(key=get_sort_key)

        processed_pairs = []

        for c1, c2 in zip(children1, children2):
            # Distance to parent
            if self.scale_tolerance <= 0:
                d1 = get_translation(c1, world=False).length()
                d2 = get_translation(c2, world=False).length()
                if abs(d1 - d2) > self.tolerance:
                    return False, None

            is_child_identical, child_rel_mtx = self.are_hierarchies_identical(
                c1, c2, relative_transform
            )

            if is_child_identical:
                if transform_determined:
                    # Thought it was identity (None), but child says otherwise
                    if relative_transform is None and child_rel_mtx is not None:
                        all_compatible = True
                        for p1, p2 in processed_pairs:
                            ok, _ = self.are_hierarchies_identical(
                                p1, p2, child_rel_mtx
                            )
                            if not ok:
                                all_compatible = False
                                break

                        if not all_compatible:
                            return False, None

                        relative_transform = child_rel_mtx

                if not transform_determined:
                    relative_transform = child_rel_mtx
                    transform_determined = True

                processed_pairs.append((c1, c2))
                continue

            # If failed and we have a transform, maybe it was the wrong one
            if transform_determined:
                if self.verbose:
                    logger.debug(
                        f"Transform mismatch. Retrying {c1} vs {c2} independently..."
                    )

                is_indep_match, indep_mtx = self.are_hierarchies_identical(c1, c2, None)

                if is_indep_match and indep_mtx is not None:
                    if self.verbose:
                        logger.debug(
                            f"Independent match found. Checking compatibility with {len(processed_pairs)} pairs."
                        )
                    all_compatible = True
                    for p1, p2 in processed_pairs:
                        if self.verbose:
                            logger.debug(f"Checking compatibility with {p1} vs {p2}")
                        ok, _ = self.are_hierarchies_identical(p1, p2, indep_mtx)
                        if not ok:
                            if self.verbose:
                                logger.debug(
                                    f"New transform incompatible with previous pair {p1} vs {p2}"
                                )
                            all_compatible = False
                            break

                    if all_compatible:
                        if self.verbose:
                            logger.debug(
                                "Updated relative transform to robust candidate"
                            )
                        relative_transform = indep_mtx
                        processed_pairs.append((c1, c2))
                        continue

            return False, None

        # 3. Internal structure (pairwise distances)
        if self.scale_tolerance <= 0 and len(children1) > 1:
            for i in range(len(children1)):
                for j in range(i + 1, len(children1)):
                    p1_i = get_translation(children1[i], world=False)
                    p1_j = get_translation(children1[j], world=False)
                    dist1 = (p1_i - p1_j).length()

                    p2_i = get_translation(children2[i], world=False)
                    p2_j = get_translation(children2[j], world=False)
                    dist2 = (p2_i - p2_j).length()

                    if abs(dist1 - dist2) > self.tolerance:
                        return False, None

        return True, relative_transform
