# !/usr/bin/python
# coding=utf-8
"""Geometry analysis and matching logic for AutoInstancer."""
from __future__ import annotations

from typing import Optional, Tuple, Union, List
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

    def quantize(self, value: float, precision: int = 4) -> float:
        """Round a value to a specific precision to ignore float noise."""
        if value == 0.0:
            return 0.0
        return round(value, precision)

    def get_pca_basis(self, node: str) -> Optional["om.MMatrix"]:
        """Returns the PCA basis matrix (rotation only) for the node's mesh."""
        shape = NodeUtils.get_shape(node)
        if not _is_mesh_shape(shape):
            return None

        try:
            pts = mesh_points(shape, world=True)
            points = np.array([(p.x, p.y, p.z) for p in pts])
            if len(points) < 3:
                return None

            centroid = np.mean(points, axis=0)
            centered = points - centroid
            cov = np.cov(centered, rowvar=False)
            evals, evecs = np.linalg.eigh(cov)

            # X=evecs[:, 2], Y=evecs[:, 1] (largest two eigenvectors)
            x_axis = evecs[:, 2]
            y_axis = evecs[:, 1]
            # Right-handed
            z_axis = np.cross(x_axis, y_axis)

            mat = np.eye(4)
            mat[0, :3] = x_axis
            mat[1, :3] = y_axis
            mat[2, :3] = z_axis
            return _np_to_mmatrix(mat)

        except Exception:
            return None

    def get_mesh_signature(
        self, transform: str, include_area: bool = True
    ) -> Optional[Tuple]:
        """Get a lightweight signature for quick rejection."""
        mesh = NodeUtils.get_shape(transform)
        if not mesh:
            return None

        num_verts = cmds.polyEvaluate(mesh, vertex=True) or 0
        num_edges = cmds.polyEvaluate(mesh, edge=True) or 0
        num_faces = cmds.polyEvaluate(mesh, face=True) or 0

        approx_area = 0.0
        # Area is not scale invariant, so we ignore it for signature matching

        # PCA Signature (Eigenvalues)
        pca_sig = ()
        # If scale tolerance is enabled, PCA eigenvalues (shape descriptors) are unreliable
        # because we can't easily normalize for non-uniform scale without full alignment.
        # We rely on topological counts and the detailed check in are_meshes_identical.
        if self.scale_tolerance <= 0:
            try:
                pts = mesh_points(mesh, world=False)
                points = np.array([(p.x, p.y, p.z) for p in pts])
                if len(points) > 3:
                    centroid = np.mean(points, axis=0)
                    centered = points - centroid

                    cov = np.cov(centered, rowvar=False)
                    evals, _ = np.linalg.eigh(cov)

                    # Normalize for scale invariance (uniform scale only)
                    max_eval = np.max(evals)
                    if max_eval > 1e-6:
                        evals = evals / max_eval

                    pca_sig = tuple(sorted([self.quantize(e, 3) for e in evals]))
            except Exception as e:
                if self.verbose:
                    print(f"PCA failed for {transform}: {e}")

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
            approx_area,
            pca_sig,
            materials,
            uv_signature,
        )

    def _whiten_points(self, points):
        """
        Whiten points (center, align to PCA, normalize variance).
        Returns:
            whitened_points: (N, 3) array
            vectors: (3, 3) eigenvectors (rotation)
            scales: (3,) scale factors (1/sqrt(eigenvalues))
            centroid: (3,) centroid
        """
        centroid = np.mean(points, axis=0)
        centered = points - centroid

        cov = np.cov(centered, rowvar=False)
        eigenvalues, eigenvectors = np.linalg.eigh(cov)

        # Sort descending
        idx = eigenvalues.argsort()[::-1]
        eigenvalues = eigenvalues[idx]
        eigenvectors = eigenvectors[:, idx]

        # Calculate scales (1 / sqrt(variance))
        valid_variance = eigenvalues.copy()
        valid_variance[valid_variance < 1e-9] = 1.0
        scales = 1.0 / np.sqrt(valid_variance)
        scales[eigenvalues < 1e-9] = 1.0

        whitened = np.dot(centered, eigenvectors) * scales

        return whitened, eigenvectors, scales, centroid

    def _check_whitened_match(self, p1, p2, tolerance) -> Optional[np.ndarray]:
        """Check if two whitened point clouds match under axis sign flips.

        Returns a 4x4 numpy diagonal-sign matrix on success (caller-assembled
        into the final relative transform).
        """
        import itertools

        tree = KDTree(p1)
        best_dist = float("inf")

        for signs in itertools.product([1, -1], repeat=3):
            p2_flipped = p2 * signs

            dists, _ = tree.query(p2_flipped, k=1)
            max_dist = np.max(dists)

            if max_dist < best_dist:
                best_dist = max_dist

            if max_dist <= tolerance:
                m_w = np.eye(4)
                m_w[0, 0] = signs[0]
                m_w[1, 1] = signs[1]
                m_w[2, 2] = signs[2]
                return m_w

        if self.verbose:
            print(f"[DEBUG] Best whitened match dist: {best_dist} (Tol: {tolerance})")

        return None

    def are_meshes_identical(
        self, t1: str, t2: str
    ) -> Tuple[bool, Optional["om.MMatrix"]]:
        """Detailed geometric comparison using robust PCA alignment.

        Returns:
            (is_identical, relative_transform_matrix)
        """
        m1 = NodeUtils.get_shape(t1)
        m2 = NodeUtils.get_shape(t2)
        if not m1 or not m2:
            return False, None

        pts1 = mesh_points(m1, world=False)
        pts2 = mesh_points(m2, world=False)
        if len(pts1) != len(pts2):
            return False, None

        # Fast path: ordered compare
        for p1, p2 in zip(pts1, pts2):
            if p1.distanceTo(p2) > self.tolerance:
                break
        else:
            if self.check_uvs:
                if not self._are_uvs_identical(m1, m2):
                    if self.verbose:
                        print(f"[DEBUG] UV mismatch for {t1} vs {t2}")
                    return False, None

            if self.verbose:
                print(f"[DEBUG] Fast path matched for {t1} vs {t2}")
            return True, None

        # Robust path: order-invariant nearest-neighbor check
        pts1_array = np.asarray(
            [(float(p.x), float(p.y), float(p.z)) for p in pts1], dtype=float
        )
        pts2_array = np.asarray(
            [(float(p.x), float(p.y), float(p.z)) for p in pts2], dtype=float
        )

        # Intermediate check: unordered identity
        # If vertices are reordered but geometry matches in local space, the
        # fast path fails but this succeeds. Prefer identity over a possibly
        # ambiguous PCA transform.
        tree = KDTree(pts2_array)
        dists, _ = tree.query(pts1_array, k=1)
        if np.max(dists) <= self.tolerance:
            if self.check_uvs:
                if not self._are_uvs_identical(m1, m2):
                    if self.verbose:
                        print(f"[DEBUG] UV mismatch for {t1} vs {t2}")
                    return False, None

            if self.verbose:
                print(f"[DEBUG] Unordered Identity match for {t1} vs {t2}")
            return True, None

        if self.scale_tolerance > 0:
            # Whitening (PCA Normalization) for arbitrary scale matching
            p1_w, v1, s1, c1 = self._whiten_points(pts1_array)
            p2_w, v2, s2, c2 = self._whiten_points(pts2_array)

            if self.verbose:
                print(f"[DEBUG] Whitening {t1} vs {t2}")
                print(f"  S1: {s1}")
                print(f"  S2: {s2}")

            whitened_tolerance = max(self.tolerance * 100.0, 0.15)

            m_w = self._check_whitened_match(p1_w, p2_w, whitened_tolerance)

            if m_w is None:
                if self.verbose:
                    print(f"[DEBUG] Whitened PCA transform failed for {t1} vs {t2}")
                return False, None

            # Construct final matrix: M = V1 * S1 * M_w * S2_inv * V2_T
            m_v1 = np.eye(4)
            m_v1[:3, :3] = v1
            m_s1 = np.eye(4)
            m_s1[0, 0] = s1[0]
            m_s1[1, 1] = s1[1]
            m_s1[2, 2] = s1[2]
            m_s2_inv = np.eye(4)
            m_s2_inv[0, 0] = 1.0 / s2[0]
            m_s2_inv[1, 1] = 1.0 / s2[1]
            m_s2_inv[2, 2] = 1.0 / s2[2]
            m_v2_T = np.eye(4)
            m_v2_T[:3, :3] = v2.T

            m_combined = m_v1 @ m_s1 @ m_w @ m_s2_inv @ m_v2_T

            # Translation: T = C2 - (C1 transformed by m_combined rotation)
            v_c1 = np.array([c1[0], c1[1], c1[2], 1.0])
            transformed = v_c1 @ m_combined
            m_combined[3, 0] = c2[0] - transformed[0]
            m_combined[3, 1] = c2[1] - transformed[1]
            m_combined[3, 2] = c2[2] - transformed[2]

            return True, _np_to_mmatrix(m_combined)

        # Standard path (uniform scale or no scale).  Center both vert
        # clouds and compare — detects same-shape / different-translation
        # cases (e.g. one mesh frozen at an offset, another not). The
        # match itself is desirable for assembly reconstruction but the
        # auto-translation acceptance is too lax for strict leaf-
        # instancing where a frozen-at-offset cube should remain distinct
        # from a transform-translated one.  Gate the *acceptance* on
        # ``scale_tolerance > 0`` (the user's opt-in for geometric-
        # equivalence matching) but always center for the downstream PCA
        # path (``c1`` / ``c2`` are referenced below).
        c1 = np.mean(pts1_array, axis=0)
        c2 = np.mean(pts2_array, axis=0)

        centered1 = pts1_array - c1
        centered2 = pts2_array - c2

        if self.scale_tolerance > 0:
            tree = KDTree(centered2)
            dists, _ = tree.query(centered1, k=1)
            if float(dists.max()) <= float(self.tolerance):
                if self.verbose:
                    print(f"[DEBUG] KDTree matched for {t1} vs {t2}")

                # Pure-translation match (rotation = identity)
                m = np.eye(4)
                m[3, 0] = c2[0] - c1[0]
                m[3, 1] = c2[1] - c1[1]
                m[3, 2] = c2[2] - c1[2]
                return True, _np_to_mmatrix(m)

        pts1_array = centered1
        pts2_array = centered2

        # Robust PCA Alignment (handles baked rotations and symmetry)
        matrix_list = ptk.MathUtils.get_pca_transform(
            pts1_array, pts2_array, tolerance=self.tolerance, robust=True
        )

        if matrix_list:
            # matrix_list is a flat 16-element list (row-major)
            m_combined = np.array(matrix_list, dtype=float).reshape(4, 4)

            # Translation: T = C2 - (M_combined * C1)
            v_c1 = np.array([c1[0], c1[1], c1[2], 1.0])
            transformed = v_c1 @ m_combined
            m_combined[3, 0] = c2[0] - transformed[0]
            m_combined[3, 1] = c2[1] - transformed[1]
            m_combined[3, 2] = c2[2] - transformed[2]
            return True, _np_to_mmatrix(m_combined)

        return False, None

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

        pts_a = mesh_points(m1, world=False)
        pts_b = mesh_points(m2, world=False)

        if len(pts_a) != len(pts_b):
            return False

        pts1 = np.array([(p.x, p.y, p.z) for p in pts_a])
        pts2 = np.array([(p.x, p.y, p.z) for p in pts_b])

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
                        print(
                            f"[DEBUG] Mesh mismatch with expected transform for {t1} vs {t2}"
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
                        print(
                            f"[DEBUG] Transform found by shape match does not align parent-space geometry for {t1} vs {t2}"
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
                    print(
                        f"[DEBUG] Transform mismatch. Retrying {c1} vs {c2} independently..."
                    )

                is_indep_match, indep_mtx = self.are_hierarchies_identical(c1, c2, None)

                if is_indep_match and indep_mtx is not None:
                    if self.verbose:
                        print(
                            f"[DEBUG] Independent match found. Checking compatibility with {len(processed_pairs)} pairs."
                        )
                    all_compatible = True
                    for p1, p2 in processed_pairs:
                        if self.verbose:
                            print(f"[DEBUG] Checking compatibility with {p1} vs {p2}")
                        ok, _ = self.are_hierarchies_identical(p1, p2, indep_mtx)
                        if not ok:
                            if self.verbose:
                                print(
                                    f"[DEBUG] New transform incompatible with previous pair {p1} vs {p2}"
                                )
                            all_compatible = False
                            break

                    if all_compatible:
                        if self.verbose:
                            print(
                                f"[DEBUG] Updated relative transform to robust candidate"
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
