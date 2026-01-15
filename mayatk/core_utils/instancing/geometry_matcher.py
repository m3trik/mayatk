# !/usr/bin/python
# coding=utf-8
"""Geometry analysis and matching logic for AutoInstancer."""
from __future__ import annotations

from typing import Optional, Tuple, Any, Union, List
import numpy as np
from scipy.spatial import KDTree

try:
    import pymel.core as pm
    import maya.api.OpenMaya as om
except ImportError:
    pass

import pythontk as ptk


def calculate_mesh_volume(node) -> float:
    """Calculate mesh volume using divergence theorem (numpy)."""
    try:
        if isinstance(node, pm.nodetypes.Transform):
            shape = node.getShape()
        else:
            shape = node

        if not shape or not isinstance(shape, pm.nodetypes.Mesh):
            return 0.0

        # getTriangles returns (counts, indices)
        _, indices = shape.getTriangles()
        points = np.array(shape.getPoints(space="world"))

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

    def __init__(self, node: pm.nodetypes.Transform):
        self.node = node
        self.name = node.name()

        # Geometry stats
        shape = node.getShape()
        self.num_verts = shape.numVertices() if shape else 0
        self.num_faces = shape.numPolygons() if shape else 0

        # Spatial data
        self.bbox = node.getBoundingBox(space="world")
        # Handle PyMEL vs OpenMaya differences
        if callable(self.bbox.min):
            self.min_pt = self.bbox.min()
            self.max_pt = self.bbox.max()
            self.centroid = self.bbox.center()
        else:
            self.min_pt = self.bbox.min
            self.max_pt = self.bbox.max
            self.centroid = self.bbox.center

        self.size = self.max_pt - self.min_pt
        self.volume = self.size.x * self.size.y * self.size.z
        self.diagonal = self.size.length()

        # Accurate Volume (Rotation Invariant)
        self.mesh_volume = calculate_mesh_volume(node)

        # Surface Area (Rotation Invariant, fallback for open meshes)
        try:
            area = pm.polyEvaluate(node, area=True)
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

    def get_pca_basis(self, node: pm.nodetypes.Transform) -> Optional[pm.dt.Matrix]:
        """Returns the PCA basis matrix (rotation only) for the node's mesh."""
        shape = node.getShape()
        if not shape or not isinstance(shape, pm.nodetypes.Mesh):
            return None

        try:
            points = np.array(shape.getPoints(space="world"))
            if len(points) < 3:
                return None

            centroid = np.mean(points, axis=0)
            centered = points - centroid
            cov = np.cov(centered, rowvar=False)
            evals, evecs = np.linalg.eigh(cov)

            # evecs columns are the eigenvectors.
            # We use X=evecs[:, 2], Y=evecs[:, 1], Z=evecs[:, 0] (Right handed?)
            x_axis = pm.dt.Vector(evecs[:, 2])
            y_axis = pm.dt.Vector(evecs[:, 1])

            # Ensure right-handed system
            z_axis = x_axis.cross(y_axis)

            # Construct rotation matrix
            mat = pm.dt.Matrix()
            mat[0] = [x_axis.x, x_axis.y, x_axis.z, 0]
            mat[1] = [y_axis.x, y_axis.y, y_axis.z, 0]
            mat[2] = [z_axis.x, z_axis.y, z_axis.z, 0]
            mat[3] = [0, 0, 0, 1]

            return mat

        except Exception:
            return None

    def get_mesh_signature(
        self, transform: pm.nodetypes.Transform, include_area: bool = True
    ) -> Optional[Tuple]:
        """Get a lightweight signature for quick rejection."""
        mesh = transform.getShape()
        if not mesh:
            return None

        num_verts = mesh.numVertices()
        num_edges = mesh.numEdges()
        num_faces = mesh.numPolygons()

        approx_area = 0.0
        # Area is not scale invariant, so we ignore it for signature matching
        # if include_area:
        #     try:
        #         area = pm.polyEvaluate(mesh, area=True)
        #         approx_area = self.quantize(area, 2)
        #     except Exception:
        #         approx_area = 0.0

        # PCA Signature (Eigenvalues)
        pca_sig = ()
        # If scale tolerance is enabled, PCA eigenvalues (shape descriptors) are unreliable
        # because we can't easily normalize for non-uniform scale without full alignment.
        # We rely on topological counts and the detailed check in are_meshes_identical.
        if self.scale_tolerance <= 0:
            try:
                points = np.array(mesh.getPoints(space="object"))
                if len(points) > 3:
                    centroid = np.mean(points, axis=0)
                    centered = points - centroid

                    cov = np.cov(centered, rowvar=False)
                    evals, _ = np.linalg.eigh(cov)

                    # Normalize eigenvalues for scale invariance (Uniform scale only)
                    max_eval = np.max(evals)
                    if max_eval > 1e-6:
                        evals = evals / max_eval

                    pca_sig = tuple(sorted([self.quantize(e, 3) for e in evals]))
            except Exception as e:
                if self.verbose:
                    print(f"PCA failed for {transform}: {e}")

        materials = ()
        if self.require_same_material:
            sgs = mesh.listConnections(type="shadingEngine") or []
            materials = tuple(sorted(list(set([sg.name() for sg in sgs]))))

        uv_signature = ()
        if self.check_uvs:
            uv_sets = mesh.getUVSetNames() or []
            uv_counts = []
            for uv_set in uv_sets:
                try:
                    uv_counts.append(mesh.numUVs(uv_set))
                except TypeError:
                    uv_counts.append(mesh.numUVs())
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
        # Center
        centroid = np.mean(points, axis=0)
        centered = points - centroid

        # PCA
        cov = np.cov(centered, rowvar=False)
        eigenvalues, eigenvectors = np.linalg.eigh(cov)

        # Sort descending
        idx = eigenvalues.argsort()[::-1]
        eigenvalues = eigenvalues[idx]
        eigenvectors = eigenvectors[:, idx]

        # Calculate scales (1 / sqrt(variance))
        # Handle small variance to avoid division by zero
        valid_variance = eigenvalues.copy()
        valid_variance[valid_variance < 1e-9] = 1.0  # Avoid div/0
        scales = 1.0 / np.sqrt(valid_variance)

        # If variance was effectively zero, scale should be 1.0 (no scaling)
        scales[eigenvalues < 1e-9] = 1.0

        # Whiten
        # P_w = P * V * S
        whitened = np.dot(centered, eigenvectors) * scales

        return whitened, eigenvectors, scales, centroid

    def _check_whitened_match(self, p1, p2, tolerance):
        """Check if two whitened point clouds match under axis sign flips."""
        import itertools

        # Build KDTree for p1 once
        tree = KDTree(p1)

        best_dist = float("inf")

        # 3 axes signs
        for signs in itertools.product([1, -1], repeat=3):
            # Apply flip
            p2_flipped = p2 * signs

            # Query tree
            dists, _ = tree.query(p2_flipped, k=1)
            max_dist = np.max(dists)

            if max_dist < best_dist:
                best_dist = max_dist

            if max_dist <= tolerance:
                # Found a match!
                m_w = pm.dt.Matrix()
                m_w[0, 0] = signs[0]
                m_w[1, 1] = signs[1]
                m_w[2, 2] = signs[2]
                return m_w

        if self.verbose:
            print(f"[DEBUG] Best whitened match dist: {best_dist} (Tol: {tolerance})")

        return None

    def are_meshes_identical(
        self, t1: pm.nodetypes.Transform, t2: pm.nodetypes.Transform
    ) -> Tuple[bool, Optional[pm.dt.Matrix]]:
        """Detailed geometric comparison using robust PCA alignment.

        Returns:
            (is_identical, relative_transform_matrix)
        """
        m1 = t1.getShape()
        m2 = t2.getShape()
        if not m1 or not m2:
            return False, None

        pts1 = m1.getPoints(space="object")
        pts2 = m2.getPoints(space="object")
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

        # Intermediate check: Unordered Identity check
        # If vertices are just reordered but geometry is same in local space, Fast Path fails but this succeeds.
        # We prefer Identity over a potentially ambiguous PCA transform.
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
            # Use Whitening (PCA Normalization) for arbitrary scale matching
            p1_w, v1, s1, c1 = self._whiten_points(pts1_array)
            p2_w, v2, s2, c2 = self._whiten_points(pts2_array)

            if self.verbose:
                print(f"[DEBUG] Whitening {t1} vs {t2}")
                print(f"  S1: {s1}")
                print(f"  S2: {s2}")

            # Find transform between whitened clouds (Rotation/Reflection only)
            # We use get_pca_transform on the whitened points
            # Since they are already aligned to axes, this effectively checks sign flips and symmetry
            # Relax tolerance for whitened check as we are comparing normalized shapes
            whitened_tolerance = max(self.tolerance * 100.0, 0.15)

            # Use internal check instead of ptk.MathUtils.get_pca_transform
            m_w = self._check_whitened_match(p1_w, p2_w, whitened_tolerance)

            if not m_w:
                if self.verbose:
                    print(f"[DEBUG] Whitened PCA transform failed for {t1} vs {t2}")
                return False, None

            # Construct Final Matrix
            # M = V1 * S1 * M_w * S2_inv * V2_T

            # We pick the first valid match
            # m_w is already a matrix

            # Convert numpy parts to Maya matrices
            m_v1 = pm.dt.Matrix(v1.tolist())
            m_s1 = pm.dt.Matrix()
            m_s1[0, 0] = s1[0]
            m_s1[1, 1] = s1[1]
            m_s1[2, 2] = s1[2]

            m_s2_inv = pm.dt.Matrix()
            m_s2_inv[0, 0] = 1.0 / s2[0]
            m_s2_inv[1, 1] = 1.0 / s2[1]
            m_s2_inv[2, 2] = 1.0 / s2[2]

            m_v2_T = pm.dt.Matrix(v2.T.tolist())  # Transpose of V2

            # Combine
            m_combined = m_v1 * m_s1 * m_w * m_s2_inv * m_v2_T

            # Calculate Translation
            v_c1 = pm.dt.Vector(c1[0], c1[1], c1[2])
            v_c2 = pm.dt.Vector(c2[0], c2[1], c2[2])
            v_transformed_c1 = v_c1 * m_combined
            v_translation = v_c2 - v_transformed_c1

            m_combined[3, 0] = v_translation.x
            m_combined[3, 1] = v_translation.y
            m_combined[3, 2] = v_translation.z

            return True, m_combined

        # Standard path (Uniform Scale or No Scale)
        # Calculate centroids
        c1 = np.mean(pts1_array, axis=0)
        c2 = np.mean(pts2_array, axis=0)

        # Center points
        centered1 = pts1_array - c1
        centered2 = pts2_array - c2

        # No scale tolerance: use raw centered points
        pts1_array = centered1
        pts2_array = centered2

        tree = KDTree(pts2_array)
        dists, _ = tree.query(pts1_array, k=1)
        if float(dists.max()) <= float(self.tolerance):
            if self.verbose:
                print(f"[DEBUG] KDTree matched for {t1} vs {t2}")

            # Construct full matrix: T = C2 - (C1)
            m = pm.dt.Matrix()

            # Calculate Translation
            v_c1 = pm.dt.Vector(c1[0], c1[1], c1[2])
            v_c2 = pm.dt.Vector(c2[0], c2[1], c2[2])
            v_transformed_c1 = v_c1 * m
            v_translation = v_c2 - v_transformed_c1

            m[3, 0] = v_translation.x
            m[3, 1] = v_translation.y
            m[3, 2] = v_translation.z
            return True, m

        # Robust PCA Alignment (handles baked rotations and symmetry)
        matrix_list = ptk.MathUtils.get_pca_transform(
            pts1_array, pts2_array, tolerance=self.tolerance, robust=True
        )

        if matrix_list:
            m_rot = pm.dt.Matrix(matrix_list)
            m_combined = m_rot

            # Calculate Translation: T = C2 - (M_combined * C1)
            v_c1 = pm.dt.Vector(c1[0], c1[1], c1[2])
            v_c2 = pm.dt.Vector(c2[0], c2[1], c2[2])
            v_transformed_c1 = v_c1 * m_combined
            v_translation = v_c2 - v_transformed_c1

            m_combined[3, 0] = v_translation.x
            m_combined[3, 1] = v_translation.y
            m_combined[3, 2] = v_translation.z
            return True, m_combined

        return False, None

    def _are_uvs_identical(self, m1: pm.nodetypes.Mesh, m2: pm.nodetypes.Mesh) -> bool:
        """Compare UVs of two meshes (assumes identical vertex order)."""
        sets1 = m1.getUVSetNames() or []
        sets2 = m2.getUVSetNames() or []

        if set(sets1) != set(sets2):
            return False

        for uv_set in sets1:
            u1, v1 = m1.getUVs(uv_set)
            u2, v2 = m2.getUVs(uv_set)

            if len(u1) != len(u2):
                return False

            # Compare arrays
            if not np.allclose(u1, u2, atol=self.uv_tolerance) or not np.allclose(
                v1, v2, atol=self.uv_tolerance
            ):
                return False

        return True

    def get_hierarchy_signature(self, node: pm.nodetypes.Transform) -> Tuple:
        """Recursive signature generation for hierarchy comparison."""
        # 1. Check for Shape (Geometry)
        shape = node.getShape()
        geo_sig = None
        if (
            shape
            and isinstance(shape, pm.nodetypes.Mesh)
            and not shape.intermediateObject.get()
        ):
            geo_sig = self.get_mesh_signature(node)

        # 2. Check Children (Hierarchy)
        children = node.getChildren(type="transform")
        child_sigs = []

        for child in children:
            # Get child's signature recursively
            c_sig = self.get_hierarchy_signature(child)

            # Use Distance for rotation invariance
            dist = child.getTranslation(space="object").length()
            dist_sig = self.quantize(dist, 2)

            if self.scale_tolerance > 0:
                dist_sig = 0.0

            child_sigs.append((c_sig, dist_sig))

        # Sort children signatures to ensure order independence
        child_sigs.sort(key=lambda x: str(x))

        return (geo_sig, tuple(child_sigs))

    def _is_matrix_close(self, m1, m2):
        """Checks if two matrices are equivalent within tolerance."""
        if m1 is None and m2 is None:
            return True
        if m1 is None:
            m1 = pm.dt.Matrix()
        if m2 is None:
            m2 = pm.dt.Matrix()

        return m1.isEquivalent(m2, tol=self.tolerance)

    def are_meshes_identical_with_transform(self, t1, t2, matrix):
        """Check if t1 transformed by matrix matches t2."""
        m1 = t1.getShape()
        m2 = t2.getShape()
        if not m1 or not m2:
            return False

        pts1 = np.array(m1.getPoints(space="object"))
        pts2 = np.array(m2.getPoints(space="object"))

        if len(pts1) != len(pts2):
            return False

        # Handle None matrix (Identity)
        if matrix is None:
            matrix = pm.dt.Matrix()

        # Get local matrices to transform points to parent space
        # We use numpy for matrix multiplication
        mat1 = np.array(t1.getMatrix(objectSpace=True))
        mat2 = np.array(t2.getMatrix(objectSpace=True))

        # Transform pts1 to parent space
        # pts1 is (N, 3). Matrix is 4x4.
        ones = np.ones((len(pts1), 1))
        pts1_h = np.hstack([pts1, ones])
        pts1_parent = np.dot(pts1_h, mat1)  # Result is (N, 4)

        # Apply relative transform (parent to parent)
        # matrix is the transform from Parent1 to Parent2
        # So pts1_target = pts1_parent * matrix
        pts1_target = np.dot(pts1_parent, np.array(matrix))[:, :3]

        # Transform pts2 to parent space
        pts2_h = np.hstack([pts2, ones])
        pts2_parent = np.dot(pts2_h, mat2)[:, :3]

        # Compare pts1_target vs pts2_parent
        tree = KDTree(pts2_parent)
        dists, _ = tree.query(pts1_target, k=1)
        max_dist = np.max(dists)

        if max_dist <= self.tolerance:
            return True

        return False

    def are_hierarchies_identical(
        self,
        t1: pm.nodetypes.Transform,
        t2: pm.nodetypes.Transform,
        expected_transform: Optional[pm.dt.Matrix] = None,
        is_root: bool = False,
    ) -> Tuple[bool, Optional[pm.dt.Matrix]]:
        """Detailed hierarchy comparison. Returns (is_identical, relative_transform)."""
        # 1. Check Mesh Identity (if present)
        s1 = t1.getShape()
        s2 = t2.getShape()

        has_mesh1 = (
            s1 and isinstance(s1, pm.nodetypes.Mesh) and not s1.intermediateObject.get()
        )
        has_mesh2 = (
            s2 and isinstance(s2, pm.nodetypes.Mesh) and not s2.intermediateObject.get()
        )

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

                # Verify that the found transform actually aligns the geometry in parent space
                # This catches cases where shapes match but local transforms differ (e.g. scale/rotation)
                # NOTE: If is_root is True, we skip this check because we expect the roots to be at different locations.
                # We trust are_meshes_identical to have verified the shape geometry.
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

        # 2. Check Children
        children1 = t1.getChildren(type="transform")
        children2 = t2.getChildren(type="transform")

        if len(children1) != len(children2):
            return False, None

        # Sort children by distance from parent and mesh complexity
        def get_sort_key(node):
            dist = node.getTranslation(space="object").length()
            mesh_sig = (0, 0, 0)
            shape = node.getShape()
            if shape and isinstance(shape, pm.nodetypes.Mesh):
                mesh_sig = (shape.numVertices(), shape.numEdges(), shape.numPolygons())

            if self.scale_tolerance > 0:
                return (0.0, mesh_sig)
            return (round(dist, 3), mesh_sig)

        children1.sort(key=get_sort_key)
        children2.sort(key=get_sort_key)

        processed_pairs = []

        for c1, c2 in zip(children1, children2):
            # Check Distance to Parent
            if self.scale_tolerance <= 0:
                d1 = c1.getTranslation(space="object").length()
                d2 = c2.getTranslation(space="object").length()
                if abs(d1 - d2) > self.tolerance:
                    return False, None

            # Recursive Check
            is_child_identical, child_rel_mtx = self.are_hierarchies_identical(
                c1, c2, relative_transform
            )

            if is_child_identical:
                # Check Transform Consistency
                if transform_determined:
                    # Case: We thought it was Identity (None), but child says otherwise
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

            # If failed, and we have a transform, maybe it was the wrong transform?
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
                    # Check if this new transform works for all previous pairs
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

        # 3. Check Internal Structure (Pairwise Distances)
        if self.scale_tolerance <= 0 and len(children1) > 1:
            for i in range(len(children1)):
                for j in range(i + 1, len(children1)):
                    p1_i = children1[i].getTranslation(space="object")
                    p1_j = children1[j].getTranslation(space="object")
                    dist1 = p1_i.distanceTo(p1_j)

                    p2_i = children2[i].getTranslation(space="object")
                    p2_j = children2[j].getTranslation(space="object")
                    dist2 = p2_i.distanceTo(p2_j)

                    if abs(dist1 - dist2) > self.tolerance:
                        return False, None

        return True, relative_transform
