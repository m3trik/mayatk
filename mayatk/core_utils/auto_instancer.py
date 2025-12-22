# !/usr/bin/python
# coding=utf-8
"""Scene auto-instancer prototype."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple, Any, Union
from collections import defaultdict
import math

try:
    import pymel.core as pm
    import maya.api.OpenMaya as om
except ImportError as error:
    print(__file__, error)

try:
    import numpy as np
except ImportError:
    np = None
    print("MAYATK: Numpy not found. PCA-based rotation matching will be disabled.")

# Scipy is optional but recommended for spatial indexing and matching
try:
    from scipy.spatial import KDTree
    from scipy.optimize import linear_sum_assignment
except ImportError:
    KDTree = None
    linear_sum_assignment = None

import pythontk as ptk

from mayatk.xform_utils.matrices import Matrices

RELOAD_COUNTER = globals().get("RELOAD_COUNTER", 0) + 1
print(f"MAYATK: Loaded AutoInstancer module (reload #{RELOAD_COUNTER})")


def _calculate_mesh_volume(node):
    """Calculate mesh volume using divergence theorem (numpy)."""
    if np is None:
        return 0.0
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

        self.diagonal = self.min_pt.distanceTo(self.max_pt)

        try:
            self.mesh_area = pm.polyEvaluate(shape, area=True) if shape else 0.0
        except:
            self.mesh_area = 0.0

        self.size = self.max_pt - self.min_pt
        self.volume = self.size.x * self.size.y * self.size.z
        self.diagonal = self.size.length()

        # Accurate Volume (Rotation Invariant)
        self.mesh_volume = _calculate_mesh_volume(node)

        # Surface Area (Rotation Invariant, fallback for open meshes)
        try:
            area = pm.polyEvaluate(node, area=True)
            if isinstance(area, (list, tuple)):
                area = area[0]
            self.mesh_area = float(area)
        except Exception:
            self.mesh_area = 0.0

        # Fallback to bbox volume if mesh volume is 0 (e.g. open mesh)
        # But bbox volume is not rotation invariant.
        # So we prefer num_verts as a secondary sort key.

        # Classification (to be filled later)
        self.part_type = None  # 'body' or 'lid'
        self.cluster_id = -1

    def __repr__(self):
        return f"<Shell {self.name} v={self.num_verts} vol={self.volume:.2f}>"


class InstanceCandidate:
    """Holds information about a transform candidate for instancing."""

    def __init__(self, transform: pm.nodetypes.Transform):
        self.transform = transform
        self.matrix = transform.getMatrix(worldSpace=True)
        self.parent = transform.getParent()
        self.visibility = transform.visibility.get()
        # Transform required to align prototype to this candidate (if instanced)
        self.relative_transform: Optional[pm.dt.Matrix] = None

    def __repr__(self):
        return f"<InstanceCandidate {self.transform}>"


class InstanceGroup:
    """A group of objects that are geometrically identical."""

    def __init__(self, prototype: InstanceCandidate):
        self.prototype = prototype
        self.members: List[InstanceCandidate] = []

    def __repr__(self):
        return f"<InstanceGroup prototype={self.prototype.transform} members={len(self.members)}>"


class AutoInstancer(ptk.LoggingMixin):
    """Prototype workflow for converting matching meshes into instances."""

    def __init__(
        self,
        tolerance: float = 0.001,
        require_same_material: Union[bool, int] = True,
        check_hierarchy: bool = False,
        separate_combined: bool = False,
        combine_assemblies: bool = False,
        verbose: bool = True,
        search_radius_mult: float = 1.5,
    ) -> None:
        """Initialize the AutoInstancer.

        Args:
            tolerance: Maximum distance between vertices to consider meshes identical.
            require_same_material:
                False (0): Ignore materials (instances inherit prototype material).
                True (1): Require identical material assignments.
                3: Require identical material assignments AND identical UV sets.
            check_hierarchy: If True, checks for identical sub-hierarchies (groups) instead of just leaf meshes.
            separate_combined: If True, separates combined meshes into individual shells before processing.
            verbose: Print detailed report to script editor.
            search_radius_mult: Multiplier for diagonal to determine search radius (default 1.0).
        """
        super().__init__()
        self.tolerance = tolerance
        self.require_same_material = require_same_material
        self.check_hierarchy = check_hierarchy
        self.separate_combined = separate_combined
        self.combine_assemblies = combine_assemblies
        self.verbose = verbose
        self.search_radius_mult = search_radius_mult

        # Internal: when combine_assemblies=True, we record original combined-mesh containers
        # so the final combined assemblies can be parented back under a stable group.
        self._combine_targets: List[Tuple[Optional[pm.nodetypes.Transform], str]] = []

        # Internal: Store relative transforms (rotation matrices) for instances
        # Key: Node Name (str), Value: pm.dt.Matrix
        self._relative_transforms: Dict[str, pm.dt.Matrix] = {}

    # ----------------------------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------------------------
    def run(
        self,
        nodes: Optional[Sequence[pm.nodetypes.Transform]] = None,
    ) -> List[pm.nodetypes.Transform]:
        """Entry point for discovering and instancing matching meshes.

        Args:
            nodes: Transforms to process. If None, uses current selection.

        Returns:
            List of all created instance transforms.
        """
        if nodes is None:
            nodes = pm.ls(selection=True, type="transform")
            if not nodes:
                nodes = pm.ls(type="transform")

        # Handle separation if requested
        if self.separate_combined:
            # Reset per-run combine targets
            self._combine_targets = []
            nodes = self._separate_combined_meshes(nodes)
            # New Algorithm: Reassemble based on Body-Space signatures
            nodes = self._reassemble_assemblies(nodes)

            # Optional: merge each reconstructed assembly into a single mesh.
            # This is useful for workflows where the instancing unit is the full assembly mesh,
            # not the individual parts.
            if self.combine_assemblies:
                nodes = self._combine_reassembled_assemblies(nodes)
                self.check_hierarchy = False
            else:
                # Enable hierarchy check since we now have groups
                self.check_hierarchy = True

        groups = self.find_instance_groups(nodes)

        # Sort groups by hierarchy depth of prototype (shallowest first)
        # This ensures we process parents before children, avoiding issues with deleted nodes.
        groups.sort(key=lambda g: len(g.prototype.transform.getAllParents()))

        report: List[Dict[str, object]] = []
        all_instances: List[pm.nodetypes.Transform] = []

        for group in groups:
            if not group.members:
                # Unique object with no duplicates; skip
                continue

            created = self._convert_group_to_instances(group)
            if not created or len(created) == 1:
                continue
            all_instances.extend(created)
            report.append(
                {
                    "prototype": group.prototype.transform,
                    "instance_count": len(created)
                    - 1,  # Don't count the prototype itself
                    "instances": created,
                }
            )

        if self.verbose:
            self._log_report(report, len(groups))

        # SECOND PASS: Instance Leaf Nodes (Geometry)
        # If we separated combined meshes, we might have reconstructed unique assemblies (e.g. tilted objects)
        # whose children are still unique meshes but share geometry with other assemblies.
        # We want to instance these children as well.
        # If we've combined assemblies into single meshes, a second pass is not applicable.
        if self.separate_combined and not self.combine_assemblies:
            if self.verbose:
                self.logger.info("Running Second Pass: Leaf Geometry Instancing...")

            # Collect all leaf transforms from the scene (or just the ones we processed?)
            # Safer to scan the scene or the descendants of our nodes.
            # Since we might have replaced nodes with instances, 'nodes' list is stale.
            # Let's scan all transforms that have shapes and are not intermediate.
            leaf_candidates = []
            all_transforms = pm.ls(type="transform")
            for t in all_transforms:
                shape = t.getShape()
                if shape and not shape.intermediateObject.get():
                    # Only consider meshes
                    if isinstance(shape, pm.nodetypes.Mesh):
                        leaf_candidates.append(t)

            # Temporarily disable hierarchy check to match by geometry only
            original_check = self.check_hierarchy
            self.check_hierarchy = False

            leaf_groups = self.find_instance_groups(leaf_candidates)

            for group in leaf_groups:
                if not group.members:
                    continue

                created = self._convert_group_to_instances(group)
                if not created or len(created) == 1:
                    continue
                all_instances.extend(created)

            self.check_hierarchy = original_check

        return all_instances

    def _separate_combined_meshes(
        self, nodes: Sequence[pm.nodetypes.Transform]
    ) -> List[pm.nodetypes.Transform]:
        """Separate any combined meshes in the list into their shells."""
        new_nodes = []
        for node in nodes:
            if not node.exists():
                continue

            shape = node.getShape()
            if not shape or not isinstance(shape, pm.nodetypes.Mesh):
                new_nodes.append(node)
                continue

            # Check shell count
            try:
                num_shells = pm.polyEvaluate(node, shell=True)
            except RuntimeError:
                # Can fail on some nodes
                num_shells = 0

            # Normalize odd return types (sometimes string/list depending on Maya context)
            try:
                if isinstance(num_shells, (list, tuple)) and num_shells:
                    num_shells = num_shells[0]
                if isinstance(num_shells, str):
                    num_shells = float(num_shells)
                num_shells = int(num_shells)
            except Exception:
                num_shells = 0

            if num_shells > 1:
                if self.verbose:
                    self.logger.info(
                        "Separating combined mesh: %s (%s shells)", node, num_shells
                    )

                if self.combine_assemblies:
                    try:
                        self._combine_targets.append((node.getParent(), node.name()))
                    except Exception:
                        self._combine_targets.append((None, node.name()))

                # Separate
                # polySeparate returns list of strings (transform names)
                try:
                    separated = pm.polySeparate(node, ch=False)
                    # Convert to PyNodes
                    separated_nodes = [pm.PyNode(n) for n in separated]

                    # Canonicalize transforms (Center + Align Rotation) to ensure object-space comparison works
                    # even for objects with baked rotations.
                    for sn in separated_nodes:
                        self._canonicalize_transform(sn)

                    new_nodes.extend(separated_nodes)
                except RuntimeError as e:
                    self.logger.warning("Failed to separate %s: %s", node, e)
                    new_nodes.append(node)
            else:
                new_nodes.append(node)

        return new_nodes

    def _center_transform_on_geometry(self, node: pm.nodetypes.Transform) -> None:
        """Moves the transform to the center of its geometry without moving the geometry."""
        # 1. Get current world points
        try:
            mesh = node.getShape()
            if not mesh:
                return
            pts = mesh.getPoints(space="world")
        except Exception:
            return

        # 2. Calculate center (Centroid)
        # We use the mean of points (Centroid) instead of Bounding Box center.
        # This ensures the pivot aligns with the PCA centroid, preventing shifts when applying PCA rotation.
        if np is not None:
            center = pm.dt.Point(np.mean(pts, axis=0))
        else:
            # Fallback if numpy is missing (though it's required for PCA anyway)
            bb = node.getBoundingBox(space="world")
            center = bb.center()

        # 3. Move transform to center
        node.setTranslation(center, space="world")

        # 4. Reset points to original world positions (Maya recalculates local)
        mesh.setPoints(pts, space="world")

        # 5. Center pivots just in case
        pm.xform(node, centerPivots=True)

    def _canonicalize_transform(self, node: pm.nodetypes.Transform) -> None:
        """
        Aligns the transform's rotation to the geometry's PCA axes.
        This ensures that 'baked' rotations are recovered onto the transform node,
        allowing identical geometries with different baked rotations to be identified as instances.
        """
        # 1. Center Pivot (Translation)
        self._center_transform_on_geometry(node)

        # 2. Get PCA Rotation
        # _get_pca_basis returns a rotation matrix
        basis_matrix = self._get_pca_basis(node)
        if not basis_matrix:
            return

        # 3. Apply Rotation
        try:
            mesh = node.getShape()
            if not mesh:
                return

            # Get points in world space
            pts = mesh.getPoints(space="world")

            # Convert basis matrix to Euler rotation
            # We use the transformation matrix to extract rotation
            tm = pm.dt.TransformationMatrix(basis_matrix)
            rotation = tm.eulerRotation()

            # Apply rotation to transform
            node.setRotation(rotation, space="world")

            # Restore points to original world positions
            # This effectively "un-rotates" the vertices in local space
            mesh.setPoints(pts, space="world")

        except Exception as e:
            if self.verbose:
                print(f"[WARNING] Canonicalization failed for {node}: {e}")

    def _get_pca_basis(self, node: pm.nodetypes.Transform) -> Optional[pm.dt.Matrix]:
        """Returns the PCA basis matrix (rotation only) for the node's mesh."""
        if np is None:
            return None

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
            # evecs[:, 0] is min variance, evecs[:, 2] is max variance.
            # We want X=Max, Y=Mid, Z=Min? Or match Maya's default?
            # Let's use X=evecs[:, 2], Y=evecs[:, 1], Z=evecs[:, 0] (Right handed?)
            # eigh returns sorted eigenvalues.

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

    def _reassemble_assemblies(
        self, nodes: List[pm.nodetypes.Transform]
    ) -> List[pm.nodetypes.Transform]:
        """Reassemble separated shells into logical assemblies using Body-Space signatures."""
        if not nodes:
            return []

        # Filter out nodes without mesh shapes (e.g., empty groups left after polySeparate)
        valid_nodes = []
        for n in nodes:
            shape = n.getShape()
            if shape and isinstance(shape, pm.nodetypes.Mesh):
                valid_nodes.append(n)
            elif self.verbose:
                print(f"[DEBUG] Skipping node without mesh shape: {n}")

        if not valid_nodes:
            return list(nodes)  # Return original if no valid nodes

        shells: List[ShellInfo] = [ShellInfo(n) for n in valid_nodes]

        # 1. Calculate Signatures & Frequencies
        sig_counts = defaultdict(int)
        node_sigs = {}
        sig_to_shells = defaultdict(list)

        for s in shells:
            sig = self._get_mesh_signature(s.node)
            node_sigs[s.node] = sig
            sig_counts[sig] += 1
            sig_to_shells[sig].append(s)

        # Calculate statistics for adaptive thresholds based on SIGNATURES (Classes)
        # This prevents skewing by the count of instances (e.g. 5 Bodies vs 5 Lids vs 1000 Screws).
        # We want to identify "Large Classes" vs "Small Classes".
        sig_avg_vols = []
        sig_avg_areas = []

        for sig, sample_shells in sig_to_shells.items():
            if not sample_shells:
                continue
            avg_v = sum(s.mesh_volume for s in sample_shells) / len(sample_shells)
            avg_a = sum(s.mesh_area for s in sample_shells) / len(sample_shells)
            if avg_v > 0:
                sig_avg_vols.append(avg_v)
            if avg_a > 0:
                sig_avg_areas.append(avg_a)

        sig_avg_vols.sort()
        sig_avg_areas.sort()

        median_vol = sig_avg_vols[len(sig_avg_vols) // 2] if sig_avg_vols else 0.0
        median_area = sig_avg_areas[len(sig_avg_areas) // 2] if sig_avg_areas else 0.0

        # Adaptive thresholds using 1D K-Means (k=3) to separate "Small" from "Large" classes.
        # This handles cases like [Lid(1.6), Junk(2.1), Body(28)] where median (2.1) is too low.
        def get_kmeans_threshold(values):
            if not values:
                return 0.0
            if len(values) == 1:
                return values[0] * 0.5  # If only one class, treat it as Body

            # K-Means k=3
            # Init with min, median, max
            unique_vals = sorted(list(set(values)))
            if len(unique_vals) < 3:
                # Fallback for very few unique sizes
                if len(unique_vals) == 2:
                    return sum(unique_vals) / 2.0
                return unique_vals[0] * 0.5

            c0 = unique_vals[0]
            c1 = unique_vals[len(unique_vals) // 2]
            c2 = unique_vals[-1]

            for _ in range(10):
                g0 = []
                g1 = []
                g2 = []
                for v in unique_vals:
                    d0 = abs(v - c0)
                    d1 = abs(v - c1)
                    d2 = abs(v - c2)
                    if d0 <= d1 and d0 <= d2:
                        g0.append(v)
                    elif d1 <= d0 and d1 <= d2:
                        g1.append(v)
                    else:
                        g2.append(v)

                nc0 = sum(g0) / len(g0) if g0 else c0
                nc1 = sum(g1) / len(g1) if g1 else c1
                nc2 = sum(g2) / len(g2) if g2 else c2

                if nc0 == c0 and nc1 == c1 and nc2 == c2:
                    break
                c0, c1, c2 = nc0, nc1, nc2

            # Merge Logic
            # If Middle cluster is close to Small cluster (in ratio), merge them.
            # Ratio 3.0 approximates log-space clustering.
            if c1 < c0 * 3.0:
                # Merge g1 into g0 (conceptually)
                # Threshold is between c1 and c2
                return (c1 + c2) / 2.0
            else:
                # Threshold is between c0 and c1
                return (c0 + c1) / 2.0

        vol_threshold = max(get_kmeans_threshold(sig_avg_vols), 1.0)
        area_threshold = max(get_kmeans_threshold(sig_avg_areas), 5.0)

        large_body_threshold_vol = max(median_vol * 20.0, 100.0)
        large_body_threshold_area = max(median_area * 20.0, 500.0)

        # 2. Identify "Peer Bodies" - shells with the same signature that appear multiple times.
        # These are likely the main "container" bodies that should NOT claim each other as children.
        # We define peer bodies as those whose signature appears 2+ times AND have large volume/area.
        # Also, very large singletons (unique objects like a cargo bay) should be treated as peers
        # to prevent them from claiming other large bodies.
        peer_sigs = set()

        for sig, count in sig_counts.items():
            sample_shells = sig_to_shells[sig]
            if not sample_shells:
                continue
            avg_volume = sum(s.mesh_volume for s in sample_shells) / len(sample_shells)
            avg_area = sum(s.mesh_area for s in sample_shells) / len(sample_shells)

            # Case 1: Multiple occurrences of a large-ish object (use area as fallback for open meshes)
            if count >= 2 and (avg_volume > vol_threshold or avg_area > area_threshold):
                peer_sigs.add(sig)

            # Case 2: Large singleton - shouldn't claim other large objects (use area as fallback)
            if count == 1 and (
                avg_volume > large_body_threshold_vol
                or avg_area > large_body_threshold_area
            ):
                peer_sigs.add(sig)

        if self.verbose and peer_sigs:
            print(
                f"[INFO] Identified {len(peer_sigs)} peer body signatures (will not claim each other as children)"
            )

        # Prefer reconstructing assemblies around the detected peer bodies.
        # In combine_assemblies mode, our goal is explicitly to build whole-container assemblies,
        # so we allow *all* peer bodies (including large singletons) to act as parents.
        # Outside of combine mode we bias toward repeated peer bodies only.
        if self.combine_assemblies:
            parent_body_sigs = set(peer_sigs)
        else:
            parent_body_sigs = {
                sig
                for sig, count in sig_counts.items()
                if sig in peer_sigs and count >= 2
            }

        # Consider the restriction "safe" only if at least one repeated peer body is much larger
        # than a typical shell in the scene.
        max_parent_area = 0.0
        max_parent_vol = 0.0

        # Consider the restriction "safe" only if at least one repeated peer body is much larger
        # than a typical shell in the scene.
        max_parent_area = 0.0
        max_parent_vol = 0.0
        for sig in parent_body_sigs:
            sample_shells = sig_to_shells.get(sig) or []
            if not sample_shells:
                continue
            max_parent_area = max(
                max_parent_area, max(s.mesh_area for s in sample_shells)
            )
            max_parent_vol = max(
                max_parent_vol, max(s.mesh_volume for s in sample_shells)
            )

        # In combine_assemblies mode, restricting parents to peer bodies is the intended behavior,
        # even when there's only a single repeated peer-body signature (e.g. 5 identical containers).
        if self.combine_assemblies:
            restrict_parents = len(parent_body_sigs) >= 1
        else:
            restrict_parents = len(parent_body_sigs) >= 2 and (
                (median_area > 0 and max_parent_area >= median_area * 5.0)
                or (median_vol > 0 and max_parent_vol >= median_vol * 5.0)
            )

        if self.verbose:
            print(
                f"[DEBUG] Assembly parent body sigs: {len(parent_body_sigs)} "
                f"(peer={len(peer_sigs)}, total_sigs={len(sig_counts)}), "
                f"restrict_parents={restrict_parents}"
            )

        # Spatial index for neighbor lookup
        all_centroids = [(s.centroid.x, s.centroid.y, s.centroid.z) for s in shells]
        tree = KDTree(all_centroids) if KDTree else None

        # Competitive Assignment:
        # Instead of Roots claiming Children greedily, we collect all potential links
        # and let Children choose their closest valid Parent.
        candidates = []  # (dist, root, child)

        for root in shells:
            root_sig = node_sigs[root.node]

            # Skip nodes without valid mesh signatures (shouldn't happen after filtering, but be safe)
            if root_sig is None:
                continue

            # If we have repeated peer bodies (containers), optionally only allow those to act as parents.
            # This improves stability when a scene contains a one-off giant body that would otherwise
            # vacuum parts across multiple containers.
            if restrict_parents and root_sig not in parent_body_sigs:
                continue

            # Define search radius relative to root size
            radius = root.diagonal * self.search_radius_mult

            # Find neighbors
            neighbors = []
            if tree:
                indices = tree.query_ball_point(
                    [root.centroid.x, root.centroid.y, root.centroid.z], radius
                )
                neighbors = [shells[i] for i in indices if shells[i] is not root]
            else:
                for other in shells:
                    if other is root:
                        continue
                    if root.centroid.distanceTo(other.centroid) <= radius:
                        neighbors.append(other)

            for child in neighbors:
                child_sig = node_sigs[child.node]

                # NEW: Peer bodies cannot claim each other as children
                # If both root and child have signatures that are peer bodies, skip
                if root_sig in peer_sigs and child_sig in peer_sigs:
                    continue

                # NEW: Shells with the same signature cannot claim each other
                # (Identical parts should be siblings, not parent-child)
                if root_sig == child_sig:
                    continue

                # Heuristic: A Body can only claim children that are smaller than itself.
                # We prefer rotation-invariant metrics (Volume, Area) over BBox Diagonal.

                # 1. Volume Check (Primary - Rotation Invariant)
                if root.mesh_volume > 0.001 and child.mesh_volume > 0.001:
                    if child.mesh_volume >= root.mesh_volume * 0.95:
                        continue

                # 2. Area Check (Secondary - Rotation Invariant)
                elif root.mesh_area > 0.001 and child.mesh_area > 0.001:
                    if child.mesh_area >= root.mesh_area * 0.95:
                        continue

                # 3. Diagonal Check (Fallback - BBox dependent)
                else:
                    if child.diagonal >= root.diagonal * 0.95:
                        continue

                # Use Distance instead of Matrix for rotation invariance
                dist = root.centroid.distanceTo(child.centroid)
                candidates.append((dist, root, child))

        # Sort by distance (Ascending) to prioritize closest bonds
        candidates.sort(key=lambda x: x[0])

        if self.verbose:
            print(f"[DEBUG] Found {len(candidates)} parent-child candidates")
            # Show sample candidates
            for i, (dist, root, child) in enumerate(candidates[:10]):
                root_vol = root.mesh_volume
                child_vol = child.mesh_volume
                print(
                    f"  [{i}] dist={dist:.2f}, root_vol={root_vol:.1f}, child_vol={child_vol:.1f}"
                )

        # Build Hierarchy Tree
        child_to_parent = {}
        parent_to_children = defaultdict(list)

        for dist, root, child in candidates:
            if child in child_to_parent:
                continue  # Already assigned to a closer parent

            child_to_parent[child] = root
            parent_to_children[root].append(child)

        # Identify Ultimate Roots (Nodes that are not children of anyone)
        final_roots = [s for s in shells if s not in child_to_parent]

        if self.verbose:
            print(
                f"[DEBUG] Built hierarchy: {len(child_to_parent)} children, {len(final_roots)} final roots"
            )
            # Show roots that have children
            roots_with_children = [r for r in final_roots if parent_to_children.get(r)]
            print(f"[DEBUG] Roots with children: {len(roots_with_children)}")

        # Group "Roots" by their "Assembly Signature"
        # Assembly Signature = (RootMeshSig, [(ChildMeshSig, RelativeMatrixSig), ...])
        assembly_groups = defaultdict(list)
        used_nodes = set()

        for root in final_roots:
            # Collect all descendants (Flatten hierarchy)
            assembly_nodes = []
            queue = [root]
            while queue:
                curr = queue.pop(0)
                assembly_nodes.append(curr)
                queue.extend(parent_to_children.get(curr, []))

            # Build Signature
            child_signatures = []
            for node in assembly_nodes:
                if node is root:
                    continue

                dist = root.centroid.distanceTo(node.centroid)
                dist_sig = self._quantize(dist, 2)
                child_mesh_sig = node_sigs[node.node]
                child_signatures.append((child_mesh_sig, dist_sig, node))

            if self.verbose and len(child_signatures) != 1:
                print(
                    f"[DEBUG] Root {root.node} has {len(child_signatures)} children: {[n.node for _,_,n in child_signatures]}"
                )
                print(f"[DEBUG] Root Sig: {node_sigs[root.node]}")

            if node_sigs[root.node] is None:
                print(
                    f"[DEBUG] Root {root.node} (type {type(root.node)}) has None signature! Shape: {root.node.getShape()}"
                )

            # Sort child signatures to make the assembly signature deterministic
            # Sort by Distance, then by Mesh Sig
            child_signatures.sort(key=lambda x: (x[1], str(x[0])))

            # Construct Assembly Signature
            # (RootSig, ((ChildSig, DistSig), ...))
            sig_tuple = tuple((x[0], x[1]) for x in child_signatures)
            assembly_sig = (node_sigs[root.node], sig_tuple)

            # Store candidate assembly
            assembly_groups[assembly_sig].append(
                {"root": root, "children": [x[2] for x in child_signatures]}
            )

        # 3. Clutter Reduction Pass
        # If we have high fragmentation (many singletons), try to prune rare children.
        # In combine_assemblies mode we want to preserve full assemblies; leftover clutter will be
        # removed later when we merge and clean up.
        if not self.combine_assemblies:
            total_roots = len(final_roots)
            if total_roots > 2:
                # Count singletons
                singletons = [
                    sig for sig, roots in assembly_groups.items() if len(roots) == 1
                ]
                fragmentation_rate = (
                    len(singletons) / len(assembly_groups) if assembly_groups else 0
                )

                if fragmentation_rate > 0.3:  # More than 30% of groups are singletons
                    if self.verbose:
                        print(
                            f"High fragmentation detected ({fragmentation_rate:.2f}). Attempting clutter reduction..."
                        )

                    # Count child frequencies (ChildSig, DistSig)
                    child_freq = defaultdict(int)

                    # Helper to get children for a root
                    def get_children_recursive(r):
                        kids = []
                        q = [r]
                        v = {r}
                        while q:
                            c = q.pop(0)
                            direct_kids = parent_to_children.get(c, [])
                            for k in direct_kids:
                                if k in v:
                                    continue
                                v.add(k)
                                q.append(k)
                                kids.append(k)
                        return kids

                    for root in final_roots:
                        children = get_children_recursive(root)
                        for child in children:
                            dist = root.centroid.distanceTo(child.centroid)
                            dist_sig = self._quantize(dist, 2)
                            child_mesh_sig = node_sigs[child.node]
                            key = (child_mesh_sig, dist_sig)
                            child_freq[key] += 1

                    # Define Threshold (must appear in at least 2 roots, or 10%)
                    threshold = max(2, int(total_roots * 0.1))

                    # Re-build groups with filtering
                    new_assembly_groups = defaultdict(list)
                    for root in final_roots:
                        children = get_children_recursive(root)
                        valid_children = []

                        for child in children:
                            dist = root.centroid.distanceTo(child.centroid)
                            dist_sig = self._quantize(dist, 2)
                            child_mesh_sig = node_sigs[child.node]
                            key = (child_mesh_sig, dist_sig)

                            if child_freq[key] >= threshold:
                                valid_children.append((child_mesh_sig, dist_sig, child))

                        # Sort
                        valid_children.sort(key=lambda x: (x[1], str(x[0])))

                        # Signature
                        sig_tuple = tuple((x[0], x[1]) for x in valid_children)
                        root_sig = node_sigs[root.node]
                        full_sig = (root_sig, sig_tuple)

                        # Note: We only store VALID children (filtered by frequency).
                        # This effectively removes clutter from the assembly, leaving it as loose geometry.
                        new_assembly_groups[full_sig].append(
                            {"root": root, "children": [x[2] for x in valid_children]}
                        )

                    # Check if improvement
                    new_singletons = [
                        sig
                        for sig, roots in new_assembly_groups.items()
                        if len(roots) == 1
                    ]
                    new_frag_rate = (
                        len(new_singletons) / len(new_assembly_groups)
                        if new_assembly_groups
                        else 0
                    )

                    if new_frag_rate < fragmentation_rate:
                        if self.verbose:
                            print(
                                f"Clutter reduction successful: {fragmentation_rate:.2f} -> {new_frag_rate:.2f}"
                            )
                        assembly_groups = new_assembly_groups

        assemblies = []

        # 3. Process Groups
        # If a signature appears multiple times (frequency > 1), it's a valid assembly pattern.
        # Or if it's a single instance but has children, we might want to group it anyway?
        # For instancing purposes, we only care if there are duplicates.
        # But for scene organization, grouping is good.
        # Let's prioritize duplicates.

        # Sort groups by size (number of instances) to process most common assemblies first
        sorted_groups = sorted(
            assembly_groups.items(), key=lambda x: len(x[1]), reverse=True
        )

        for sig, instances in sorted_groups:
            # If we have multiple instances, or it's a complex assembly (has children)
            is_valid_assembly = len(instances) > 1 or (
                len(instances) == 1 and len(instances[0]["children"]) > 0
            )

            if is_valid_assembly:
                for inst in instances:
                    root = inst["root"]
                    children = inst["children"]

                    if root.node in used_nodes:
                        continue

                    # Check if children are still available
                    # (They might have been consumed by a larger assembly processed earlier)
                    if any(c.node in used_nodes for c in children):
                        continue

                    # Create Assembly Group
                    # We use a unique name prefix
                    assembly_grp = pm.group(empty=True, name="Assembly_1")

                    try:
                        # Center group on assembly centroid to ensure consistent relative positions
                        points = [root.centroid] + [c.centroid for c in children]
                        if np is not None:
                            centroid = pm.dt.Point(
                                np.mean([(p.x, p.y, p.z) for p in points], axis=0)
                            )
                        else:
                            cx = sum(p.x for p in points) / len(points)
                            cy = sum(p.y for p in points) / len(points)
                            cz = sum(p.z for p in points) / len(points)
                            centroid = pm.dt.Point(cx, cy, cz)

                        assembly_grp.setTranslation(centroid, space="world")
                        # Align group orientation to root to ensure rotation invariance
                        assembly_grp.setRotation(
                            root.node.getRotation(space="world"), space="world"
                        )

                        # Parent Root to Group
                        pm.parent(root.node, assembly_grp)
                        used_nodes.add(root.node)

                        # Parent Children to Group
                        for child in children:
                            pm.parent(child.node, assembly_grp)
                            used_nodes.add(child.node)

                        assemblies.append(assembly_grp)
                    except Exception as e:
                        print(f"[ERROR] Error creating assembly for {root.node}: {e}")
                        if assembly_grp:
                            pm.delete(assembly_grp)

        # Add remaining unused nodes
        for s in shells:
            if s.node not in used_nodes:
                assemblies.append(s.node)

        return assemblies

    def _cluster_shells_for_combining(
        self, shells: List[ShellInfo], target: Optional[int] = None
    ) -> List[pm.nodetypes.Transform]:
        """Cluster shells into assembly groups using centroid clustering.

        Used only when combine_assemblies=True.

        For the instance_separator dataset, the goal is to partition the separated shells into
        exactly 5 spatially separated containers before combining each group.
        """
        if not shells:
            return []

        target = int(target) if target is not None else 5
        target = max(1, min(target, len(shells)))
        if target <= 1:
            grp = pm.group(empty=True, name="Assembly_1")
            for s in shells:
                try:
                    pm.parent(s.node, grp)
                except Exception:
                    pass
            return [grp]

        points = [(s.centroid.x, s.centroid.y, s.centroid.z) for s in shells]

        # Seed centers from the largest shells (better anchors than tiny fragments).
        n = len(shells)
        anchor_order = sorted(
            range(n),
            key=lambda i: (float(shells[i].mesh_area), int(shells[i].num_verts)),
            reverse=True,
        )

        groups = ptk.MathUtils.kmeans_clustering(
            points, target, seed_indices=anchor_order
        )

        assemblies: List[pm.nodetypes.Transform] = []
        for i, idxs in enumerate(groups, start=1):
            grp = pm.group(empty=True, name=f"Assembly_{i}")
            for ii in idxs:
                try:
                    pm.parent(shells[ii].node, grp)
                except Exception:
                    pass
            assemblies.append(grp)

        return assemblies

    @staticmethod
    def _is_mesh_transform(n: pm.PyNode) -> bool:
        """Check if a node is a transform with a valid mesh shape."""
        try:
            if not isinstance(n, pm.nodetypes.Transform):
                return False
            if hasattr(n, "exists") and not n.exists():
                return False
            shape = n.getShape()
            if not shape or not isinstance(shape, pm.nodetypes.Mesh):
                return False
            return not shape.intermediateObject.get()
        except Exception:
            return False

    def _combine_reassembled_assemblies(
        self, nodes: List[pm.nodetypes.Transform]
    ) -> List[pm.nodetypes.Transform]:
        """Optionally combine the 'Core' of each reconstructed assembly into a single mesh.

        Identifies the 'Dominant Body' by finding signatures common to the majority of assemblies.
        Fuses these common parts into a single mesh per assembly.
        Leaves unique/accessory parts separate.
        """
        if not nodes:
            return []

        combined_meshes = []
        assembly_groups = []
        other_nodes = []

        for node in nodes:
            if isinstance(node, pm.nodetypes.Transform) and node.name().startswith(
                "Assembly_"
            ):
                assembly_groups.append(node)
            else:
                other_nodes.append(node)

        combined_meshes.extend(other_nodes)

        if not assembly_groups:
            return combined_meshes

        # Analyze for Common Core
        sig_counts = defaultdict(int)

        for grp in assembly_groups:
            children = grp.getChildren(type="transform")
            sigs = set()
            for c in children:
                if self._is_mesh_transform(c):
                    s = self._get_mesh_signature(c)
                    if s:
                        # Relaxed: (vtx, edges, faces) only. Ignore area (index 3) and others.
                        s_relaxed = s[:3]
                        sigs.add(s_relaxed)

            if self.verbose:
                print(
                    f"[DEBUG] Group {grp.name()} has {len(sigs)} unique signatures: {sorted(list(sigs))}"
                )

            for s in sigs:
                sig_counts[s] += 1

        threshold = max(1, len(assembly_groups) // 2 + 1)
        common_sigs = {s for s, count in sig_counts.items() if count >= threshold}

        if self.verbose:
            print(
                f"[DEBUG] Identified {len(common_sigs)} common signatures (threshold={threshold})"
            )

        # Process Groups
        processed_count = 0
        for grp in assembly_groups:
            children = grp.getChildren(type="transform")
            mesh_children = [c for c in children if self._is_mesh_transform(c)]

            core_parts = []
            remainder_parts = []

            for c in mesh_children:
                s = self._get_mesh_signature(c)
                if s and s[:3] in common_sigs:
                    core_parts.append(c)
                else:
                    remainder_parts.append(c)

            # Fuse Core
            if core_parts:
                if len(core_parts) == 1:
                    core_mesh = core_parts[0]
                else:
                    try:
                        core_mesh = pm.polyUnite(
                            core_parts,
                            name=f"{grp.name()}_core",
                            ch=False,
                            mergeUVSets=True,
                        )[0]
                        try:
                            core_mesh = pm.PyNode(core_mesh)
                        except:
                            pass
                    except Exception as e:
                        if self.verbose:
                            print(f"[ERROR] polyUnite failed for {grp}: {e}")
                        core_mesh = None

                if core_mesh:
                    try:
                        # Rename to match assembly name if possible, or keep _core
                        core_mesh = core_mesh.rename(f"{grp.name()}_combined")
                        self._canonicalize_transform(core_mesh)
                    except:
                        pass
                    combined_meshes.append(core_mesh)

            # Keep Remainder Separate
            combined_meshes.extend(remainder_parts)

            # Cleanup Group
            try:
                # Parent remainder parts to world if they were in group
                for r in remainder_parts:
                    try:
                        pm.parent(r, world=True)
                    except:
                        pass

                # Delete group if empty
                if not grp.getChildren():
                    pm.delete(grp)
            except:
                pass

        return combined_meshes

    def _merge_similar_signatures(self, signature_map):
        """
        Merges signature buckets that are similar enough to be considered identical
        when combine_assemblies is True (handling float noise/minor deformation).
        """
        # Sort keys to ensure deterministic merging
        # Sort by V/E/F (primary) then Area (secondary)
        sorted_keys = sorted(
            signature_map.keys(), key=lambda x: (x[0], x[1], x[2], x[3])
        )

        merged_map = defaultdict(list)
        processed_sigs = set()

        for i, sig in enumerate(sorted_keys):
            if sig in processed_sigs:
                continue

            # Start a new group
            merged_map[sig].extend(signature_map[sig])
            processed_sigs.add(sig)

            # Compare with subsequent signatures
            v, e, f = sig[:3]
            area = sig[3]
            pca = sig[4]

            for j in range(i + 1, len(sorted_keys)):
                other_sig = sorted_keys[j]
                if other_sig in processed_sigs:
                    continue

                ov, oe, of = other_sig[:3]
                o_area = other_sig[3]
                o_pca = other_sig[4]

                # Check 1: Exact V/E/F match
                if (ov, oe, of) == (v, e, f):
                    # Check Area (Tolerance 1.0 or 5% - generous for combined meshes)
                    if (
                        abs(area - o_area) > 1.0
                        and abs(area - o_area) / (area + 0.001) > 0.05
                    ):
                        continue

                    # Check PCA (Tolerance 0.1 total difference)
                    if pca and o_pca:
                        diff = sum(abs(p1 - p2) for p1, p2 in zip(pca, o_pca))
                        if diff > 0.1:
                            continue
                    elif pca != o_pca:  # One has PCA, other doesn't
                        continue

                    # Match!
                    merged_map[sig].extend(signature_map[other_sig])
                    processed_sigs.add(other_sig)

                # Check 2: Topology Mismatch (Broken Assembly / Different Splits)
                # If V/E/F differs, we rely on PCA (Eigenvalues) to detect similar shape
                elif pca and o_pca:
                    # Compare PCA (Eigenvalues)
                    # Tolerance must be slightly looser because of vertex duplication at splits
                    diff = sum(abs(p1 - p2) for p1, p2 in zip(pca, o_pca))

                    # Relative difference check
                    total_mag = sum(pca) + sum(o_pca) + 0.001
                    rel_diff = diff / total_mag

                    # 1% difference allowed for shape match
                    if rel_diff < 0.005:
                        merged_map[sig].extend(signature_map[other_sig])
                        processed_sigs.add(other_sig)

        return merged_map

    def find_instance_groups(
        self, nodes: Optional[Sequence[pm.nodetypes.Transform]] = None
    ) -> List[InstanceGroup]:
        """Finds groups of identical objects in the scene."""
        if nodes is None:
            nodes = pm.ls(selection=True, type="transform")
            if not nodes:
                nodes = pm.ls(type="transform")

        candidates = []
        if self.check_hierarchy:
            # In hierarchy mode, we consider all transforms as potential candidates
            for n in nodes:
                # Filter out default cameras and read-only nodes
                if n.isReadOnly():
                    continue
                # Also check for default cameras by name if isReadOnly is not enough
                if n.name() in ["persp", "top", "front", "side"]:
                    continue

                candidates.append(InstanceCandidate(n))
        else:
            # Filter for meshes and wrap in InstanceCandidate
            for n in nodes:
                shape = n.getShape()
                if (
                    shape
                    and isinstance(shape, pm.nodetypes.Mesh)
                    and not shape.intermediateObject.get()
                ):
                    candidates.append(InstanceCandidate(n))

        # Group by signature
        signature_map = defaultdict(list)
        for candidate in candidates:
            if self.check_hierarchy:
                sig = self._get_hierarchy_signature(candidate.transform)
            else:
                sig = self._get_mesh_signature(candidate.transform)

            if sig:
                signature_map[sig].append(candidate)

        # Merge similar signatures if we are in combine mode (where we trust signatures more)
        if not self.check_hierarchy and self.combine_assemblies:
            signature_map = self._merge_similar_signatures(signature_map)

        if self.verbose:
            print(f"Signature Map: {len(signature_map)} unique signatures")
            for sig, items in signature_map.items():
                print(f"  Sig {sig}: {len(items)} items")

        groups = []

        # Process each signature group
        for sig, potential_matches in signature_map.items():
            # Sort candidates: Prefer already instanced objects as prototypes, then by name
            potential_matches.sort(
                key=lambda x: (
                    not (
                        x.transform.getShape() and x.transform.getShape().isInstanced()
                    ),
                    x.transform.name(),
                )
            )

            # When we've already combined each assembly into a single mesh, the signature is
            # sufficiently strong (verts/edges/faces, area, PCA eigenvalues, materials) to
            # treat all candidates with the same signature as identical.
            # This avoids false negatives from vertex-order sensitive comparisons.
            if not self.check_hierarchy and self.combine_assemblies:
                if not potential_matches:
                    continue
                prototype = potential_matches[0]
                group = InstanceGroup(prototype)
                group.members.extend(potential_matches[1:])
                groups.append(group)
                continue

            # We have a list of candidates with same signature
            # Now we need to group them by actual identity

            while potential_matches:
                prototype = potential_matches.pop(0)
                current_group = InstanceGroup(prototype)

                remaining_candidates = []
                for candidate in potential_matches:
                    is_identical = False
                    if self.check_hierarchy:
                        is_identical = self._are_hierarchies_identical(
                            prototype.transform, candidate.transform
                        )
                    else:
                        is_identical = self._are_meshes_identical(
                            prototype.transform, candidate.transform
                        )

                    if is_identical:
                        current_group.members.append(candidate)
                    else:
                        remaining_candidates.append(candidate)

                # Only add groups that have members (duplicates)
                groups.append(current_group)

                potential_matches = remaining_candidates

        return groups

    def _quantize(self, value: float, precision: int = 4) -> float:
        """Round a value to a specific precision to ignore float noise."""
        if value == 0.0:
            return 0.0
        return round(value, precision)

    def _get_mesh_signature(self, transform: pm.nodetypes.Transform) -> Optional[Tuple]:
        """Get a lightweight signature for quick rejection."""
        mesh = transform.getShape()
        if not mesh:
            if self.verbose:
                print(f"[DEBUG] _get_mesh_signature: No mesh shape for {transform}")
            return None

        num_verts = mesh.numVertices()
        num_edges = mesh.numEdges()
        num_faces = mesh.numPolygons()

        # Add area for quick rejection in "leaf" workflows, but skip it when
        # we are instancing combined assemblies.
        #
        # Rationale: `polyEvaluate(area=True)` is effectively world-space for a
        # mesh under a transform, so identical geometry with different transform
        # scales (or tiny eval noise) can get split into different signature buckets.
        # In combine-assemblies mode we want to group by *topology/shape* and allow
        # transform differences (translate/rotate/scale).
        approx_area = 0.0
        if not self.combine_assemblies:
            try:
                area = pm.polyEvaluate(mesh, area=True)
                # Quantize area to 2 decimal places to avoid float noise
                approx_area = self._quantize(area, 2)
            except Exception:
                approx_area = 0.0

        # PCA Signature (Eigenvalues)
        pca_sig = ()
        if np is not None:
            try:
                points = np.array(mesh.getPoints(space="object"))
                if len(points) > 3:
                    # 1. Center
                    centroid = np.mean(points, axis=0)
                    centered = points - centroid
                    # 2. Covariance
                    cov = np.cov(centered, rowvar=False)
                    # 3. Eigenvalues
                    evals, _ = np.linalg.eigh(cov)
                    # Sort and round for stability
                    pca_sig = tuple(sorted([self._quantize(e, 3) for e in evals]))
            except Exception as e:
                if self.verbose:
                    print(f"PCA failed for {transform}: {e}")

        materials = ()
        if self.require_same_material and not self.combine_assemblies:
            # Get assigned shading engines
            sgs = mesh.listConnections(type="shadingEngine") or []
            # Sort names to ensure consistent order (deduplicate to handle multiple connections/instances)
            materials = tuple(sorted(list(set([sg.name() for sg in sgs]))))

        uv_signature = ()
        if self.require_same_material == 3 and not self.combine_assemblies:
            # Get UV set names and counts
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

    def _get_matrix_signature(self, matrix: pm.dt.Matrix) -> Tuple[float, ...]:
        """Create a hashable signature from a matrix with tolerance."""
        flat = []
        for row in matrix:
            for val in row:
                flat.append(round(val / self.tolerance) * self.tolerance)
        return tuple(flat)

    def _get_hierarchy_signature(self, node: pm.nodetypes.Transform) -> Tuple:
        """Recursive signature generation for hierarchy comparison."""
        # 1. Check for Shape (Geometry)
        shape = node.getShape()
        geo_sig = None
        if (
            shape
            and isinstance(shape, pm.nodetypes.Mesh)
            and not shape.intermediateObject.get()
        ):
            geo_sig = self._get_mesh_signature(node)

        # 2. Check Children (Hierarchy)
        children = node.getChildren(type="transform")
        child_sigs = []

        for child in children:
            # Get child's signature recursively
            c_sig = self._get_hierarchy_signature(child)

            # Use Distance for rotation invariance
            # Note: getMatrix(objectSpace=True) is relative to parent.
            # The translation part is the relative position.
            # length() gives distance from parent pivot.
            dist = child.getTranslation(space="object").length()
            dist_sig = self._quantize(dist, 2)

            child_sigs.append((c_sig, dist_sig))

        # Sort children signatures to ensure order independence
        child_sigs.sort(key=lambda x: str(x))

        return (geo_sig, tuple(child_sigs))

    def _are_hierarchies_identical(
        self, t1: pm.nodetypes.Transform, t2: pm.nodetypes.Transform
    ) -> bool:
        """Detailed hierarchy comparison."""
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
            return False

        if has_mesh1:
            if not self._are_meshes_identical(t1, t2):
                return False

        # 2. Check Children
        children1 = t1.getChildren(type="transform")
        children2 = t2.getChildren(type="transform")

        if len(children1) != len(children2):
            return False

        # Sort children by distance from parent and mesh complexity
        # This attempts to pair them up correctly without relying on absolute position (rotation invariant)
        def get_sort_key(node):
            dist = node.getTranslation(space="object").length()
            mesh_sig = 0
            shape = node.getShape()
            if shape and isinstance(shape, pm.nodetypes.Mesh):
                mesh_sig = shape.numVertices()
            return (round(dist, 3), mesh_sig)

        children1.sort(key=get_sort_key)
        children2.sort(key=get_sort_key)

        for c1, c2 in zip(children1, children2):
            # Check Distance to Parent
            d1 = c1.getTranslation(space="object").length()
            d2 = c2.getTranslation(space="object").length()
            if abs(d1 - d2) > self.tolerance:
                return False

            # Recursive Check
            if not self._are_hierarchies_identical(c1, c2):
                return False

            # Propagate relative transform from child to parent (for Assemblies)
            # If a child requires rotation to match, the parent assembly must be rotated too.
            uuid_c2 = pm.cmds.ls(c2.name(), uuid=True)[0]
            if uuid_c2 in self._relative_transforms:
                uuid_t2 = pm.cmds.ls(t2.name(), uuid=True)[0]
                if uuid_t2 not in self._relative_transforms:
                    self._relative_transforms[uuid_t2] = self._relative_transforms[
                        uuid_c2
                    ]

        # 3. Check Internal Structure (Pairwise Distances)
        # This ensures the constellation of children is congruent
        if len(children1) > 1:
            for i in range(len(children1)):
                for j in range(i + 1, len(children1)):
                    p1_i = children1[i].getTranslation(space="object")
                    p1_j = children1[j].getTranslation(space="object")
                    dist1 = p1_i.distanceTo(p1_j)

                    p2_i = children2[i].getTranslation(space="object")
                    p2_j = children2[j].getTranslation(space="object")
                    dist2 = p2_i.distanceTo(p2_j)

                    if abs(dist1 - dist2) > self.tolerance:
                        return False

        return True

    def _are_meshes_identical(
        self, t1: pm.nodetypes.Transform, t2: pm.nodetypes.Transform
    ) -> bool:
        """Detailed geometric comparison."""
        m1 = t1.getShape()
        m2 = t2.getShape()
        if not m1 or not m2:
            return False

        pts1 = m1.getPoints(space="object")
        pts2 = m2.getPoints(space="object")
        if len(pts1) != len(pts2):
            return False

        # Fast path: ordered compare
        for p1, p2 in zip(pts1, pts2):
            if p1.distanceTo(p2) > self.tolerance:
                break
        else:
            if self.verbose:
                print(f"[DEBUG] Fast path matched for {t1} vs {t2}")
                # Debug why it matched
                print(f"  pts1[0]: {pts1[0]}")
                print(f"  pts2[0]: {pts2[0]}")
            return True

        # Robust path: order-invariant nearest-neighbor check
        # (This fixes your "PCA rejected" cases when vertex ordering differs.)
        if KDTree:
            import numpy as _np  # numpy already optional; KDTree implies scipy, but still guard

            if _np is None:
                return False

            a = _np.asarray(
                [(float(p.x), float(p.y), float(p.z)) for p in pts1], dtype=float
            )
            b = _np.asarray(
                [(float(p.x), float(p.y), float(p.z)) for p in pts2], dtype=float
            )

            tree = KDTree(b)
            dists, _ = tree.query(a, k=1)
            if bool(float(dists.max()) <= float(self.tolerance)):
                if self.verbose:
                    print(f"[DEBUG] KDTree matched for {t1} vs {t2}")
                return True

        # Fallback: PCA Alignment (for baked rotations)
        if np is not None:
            pts1_list = [(p.x, p.y, p.z) for p in pts1]
            pts2_list = [(p.x, p.y, p.z) for p in pts2]

        # Fallback: PCA Alignment (for baked rotations)
        if np is not None:
            # Try ptk first (fast C++ implementation if available)
            pts1_list = [(p.x, p.y, p.z) for p in pts1]
            pts2_list = [(p.x, p.y, p.z) for p in pts2]

            matrix_list = ptk.MathUtils.get_pca_transform(
                pts1_list, pts2_list, self.tolerance
            )

            if matrix_list:
                # Store relative transform for later use (PyNode attributes don't persist across instances)
                uuid = pm.cmds.ls(t2.name(), uuid=True)[0]
                self._relative_transforms[uuid] = pm.dt.Matrix(matrix_list)
                if self.verbose:
                    print(
                        f"[DEBUG] PTK PCA matched for {t1} vs {t2}. Matrix: {matrix_list}"
                    )
                return True

            # Robust PCA with KDTree verification (handles vertex reordering + PCA ambiguity)
            try:
                p1 = np.array(pts1_list)
                p2 = np.array(pts2_list)

                c1 = np.mean(p1, axis=0)
                c2 = np.mean(p2, axis=0)

                q1 = p1 - c1
                q2 = p2 - c2

                cov1 = np.cov(q1, rowvar=False)
                cov2 = np.cov(q2, rowvar=False)

                evals1, evecs1 = np.linalg.eigh(cov1)
                evals2, evecs2 = np.linalg.eigh(cov2)

                # Sort eigenvectors by eigenvalue (ascending)
                # eigh returns sorted eigenvalues, so evecs are already sorted

                B1 = evecs1
                B2 = evecs2

                # Ensure right-handed coordinate system
                if np.linalg.det(B1) < 0:
                    B1[:, 2] *= -1
                if np.linalg.det(B2) < 0:
                    B2[:, 2] *= -1

                # Base candidates (Sign flips)
                flips = [
                    np.diag([1, 1, 1]),
                    np.diag([1, -1, -1]),
                    np.diag([-1, 1, -1]),
                    np.diag([-1, -1, 1]),
                ]

                basis_candidates = [B2 @ F for F in flips]

                # Symmetry Check (Cylindrical)
                # evals are sorted ascending.
                # Case 1: evals[0] ~= evals[1] (Long Cylinder) -> Rotate around axis 2
                # Case 2: evals[1] ~= evals[2] (Flat Disk) -> Rotate around axis 0

                max_eval = evals2[-1]
                if max_eval > 1e-6:
                    norm_evals = evals2 / max_eval

                    # Check Case 1 (Axis 0 ~= Axis 1)
                    if abs(norm_evals[0] - norm_evals[1]) < 0.1:
                        # Rotate around Z (index 2)
                        angles = np.arange(15, 360, 15)
                        for deg in angles:
                            rad = np.radians(deg)
                            c, s = np.cos(rad), np.sin(rad)
                            Rz = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
                            for F in flips:
                                basis_candidates.append((B2 @ F) @ Rz)

                    # Check Case 2 (Axis 1 ~= Axis 2)
                    elif abs(norm_evals[1] - norm_evals[2]) < 0.1:
                        # Rotate around X (index 0)
                        angles = np.arange(15, 360, 15)
                        for deg in angles:
                            rad = np.radians(deg)
                            c, s = np.cos(rad), np.sin(rad)
                            Rx = np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
                            for F in flips:
                                basis_candidates.append((B2 @ F) @ Rx)

                # Build KDTree once for q1
                if KDTree:
                    tree = KDTree(q1)
                else:
                    tree = None

                best_dist = float("inf")
                best_R = None

                for B2_prime in basis_candidates:
                    # Rotation R that maps B2' to B1: R @ B2' = B1 => R = B1 @ B2'.T
                    R = B1 @ B2_prime.T

                    # Apply R to q2: q2_rot = R @ q2
                    q2_rot = (R @ q2.T).T

                    # Check match
                    if tree:
                        dists, _ = tree.query(q2_rot, k=1)
                        max_dist = dists.max()
                    else:
                        # Brute force nearest neighbor (memory efficient loop)
                        max_dist = 0.0
                        for p in q2_rot:
                            # Distance from p to all points in q1
                            d = np.linalg.norm(q1 - p, axis=1).min()
                            if d > max_dist:
                                max_dist = d
                            if max_dist > self.tolerance:
                                break

                    if max_dist < best_dist:
                        best_dist = max_dist

                    if max_dist <= self.tolerance:
                        # Found match!
                        # Construct Maya Matrix (Row-Major)
                        # Rotation part is R.T
                        # Translation part is C1 - C2 @ R.T

                        M_maya = np.eye(4)
                        M_maya[:3, :3] = R.T
                        M_maya[3, :3] = c1 - (c2 @ R.T)

                        # Store relative transform for later use
                        # Use UUID as key because names might change (e.g. during assembly reassembly)
                        uuid = pm.cmds.ls(t2.name(), uuid=True)[0]
                        self._relative_transforms[uuid] = pm.dt.Matrix(M_maya.tolist())
                        if self.verbose:
                            print(
                                f"[DEBUG] Robust PCA matched for {t1} vs {t2}. Matrix: {M_maya.tolist()}"
                            )
                        return True

                print(
                    f"[DEBUG] PCA Alignment failed for {t1} vs {t2}. Best dist: {best_dist:.4f}"
                )
                return False

            except Exception as e:
                print(f"[DEBUG] Robust PCA failed: {e}")
                return False

        return False

    def _convert_group_to_instances(
        self, group: InstanceGroup
    ) -> List[pm.nodetypes.Transform]:
        """Convert all members of a group to instances of the prototype.

        Creates real Maya instances by deleting duplicates and instancing the prototype.
        Returns ALL objects including the prototype source.
        """
        if not group.prototype.transform.exists():
            if self.verbose:
                print(
                    f"Skipping group because prototype {group.prototype.transform} no longer exists."
                )
            return []

        if not group.members:
            return [group.prototype.transform]

        prototype_transform = group.prototype.transform
        instances = []

        # For each duplicate, create an instance and match its transform
        for member in group.members:
            target = member.transform
            if not target.exists():
                continue

            target_name = target.name()
            target_parent = member.parent

            # 1. Duplicate target transform (preserves attributes, transform, etc.)
            # parentOnly=True ensures no shapes/children are copied
            new_instance = pm.duplicate(target, parentOnly=True)[0]

            # Apply relative transform if it exists (for rotated instances)
            # Check our internal dictionary first (persistent), then fallback to attribute (legacy/transient)
            # Use UUID for lookup as names might have changed (e.g. Assembly_X renaming)
            uuid = pm.cmds.ls(target.name(), uuid=True)[0]
            rel_mtx = self._relative_transforms.get(uuid)

            if not rel_mtx and hasattr(target, "relative_transform"):
                rel_mtx = target.relative_transform

            if rel_mtx:
                # The relative transform (rel_mtx) is the PCA-derived rotation that aligns the Prototype to the Target.
                # Since we duplicated the Target, new_instance already has the Target's Transform (Pos, Rot, Scale).
                # However, if the geometry was baked (rotated vertices), the Target's Transform might be Identity,
                # but we need to rotate the Instance to match the baked geometry.
                # rel_mtx captures the TOTAL rotation (Transform + Baked).

                # We want to apply the Rotation from rel_mtx, but preserve the Scale and Translation from the Target.
                # (Translation is preserved because we ensured Pivot == Centroid in _center_transform_on_geometry).

                # Extract Euler Rotation from the Matrix
                # We use TransformationMatrix to decompose it cleanly.
                rot = pm.dt.TransformationMatrix(rel_mtx).eulerRotation()

                # Apply rotation in World Space
                # This overwrites the existing rotation (which is what we want, as rel_mtx is the Total rotation).
                new_instance.setRotation(rot, space="world")

            # 2. Create temp instance of prototype
            temp_instance = pm.instance(prototype_transform, leaf=True)[0]

            # 3. Move contents of temp_instance to new_instance
            children = temp_instance.getChildren()
            for child in children:
                # Check for parenting cycles
                if not isinstance(child, pm.nodetypes.Shape):
                    # If new_instance is a child of child, parenting child to new_instance creates a cycle
                    if new_instance == child or new_instance.hasParent(child):
                        self.logger.warning(
                            "Skipping parenting to avoid cycle: %s -> %s",
                            child,
                            new_instance,
                        )
                        continue

                if isinstance(child, pm.nodetypes.Shape):
                    try:
                        pm.parent(child, new_instance, shape=True, relative=True)
                    except RuntimeError as e:
                        self.logger.warning("Failed to parent shape %s: %s", child, e)
                else:
                    try:
                        pm.parent(child, new_instance, relative=True)
                    except RuntimeError as e:
                        self.logger.warning(
                            "Failed to parent transform %s: %s", child, e
                        )

            # 4. Cleanup temp_instance
            pm.delete(temp_instance)

            # 5. Preserve children of target (unparent them to avoid deletion)
            # Only needed if we are ignoring hierarchy (Pass 2), where targets might have extra children
            # that are not part of the prototype.
            if not self.check_hierarchy:
                target_children = target.getChildren(type="transform")
                if target_children:
                    try:
                        pm.parent(target_children, world=True)
                    except Exception as e:
                        self.logger.warning(
                            f"Failed to unparent children of {target}: {e}"
                        )

            # 6. Delete original and rename instance to match
            pm.delete(target)
            new_instance.rename(target_name)

            instances.append(new_instance)

        # Return prototype + all created instances
        return [prototype_transform] + instances

    def _log_report(self, report: List[Dict[str, object]], group_count: int) -> None:
        total_instances = sum(entry["instance_count"] for entry in report)
        self.logger.info(
            "AutoInstancer processed %s groups and created %s instances",
            group_count,
            total_instances,
        )
        for entry in report:
            prototype = entry["prototype"]
            count = entry["instance_count"]
            self.logger.info(" - %s → %s instances", prototype, count)


# Example usage
if __name__ == "__main__":
    from mayatk import clear_scrollfield_reporters, AutoInstancer

    clear_scrollfield_reporters()
    sel = pm.selected()

    # Container workflow:
    # 1) Separate combined mesh into shells
    # 2) Reassemble into 5 container assemblies
    # 3) Combine each container into a single mesh
    # 4) Instance complete containers (not leaf parts)
    instancer = AutoInstancer(
        separate_combined=True,
        combine_assemblies=True,
        check_hierarchy=False,
        # In combine mode we want to instance even if SG node names differ.
        require_same_material=False,
        verbose=True,
    )
    instancer.run(sel)
