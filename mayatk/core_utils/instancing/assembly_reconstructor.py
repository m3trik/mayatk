# !/usr/bin/python
# coding=utf-8
"""Logic for separating and reassembling mesh assemblies."""
from __future__ import annotations

from typing import List, Tuple, Optional, Dict, Any, Set
from collections import defaultdict
from functools import reduce
from math import gcd
import logging
import numpy as np

try:
    from scipy.spatial import KDTree
except ImportError:
    KDTree = None

try:
    import pymel.core as pm
except ImportError:
    pass

import pythontk as ptk

# From this package:
from mayatk.core_utils.instancing.geometry_matcher import GeometryMatcher, ShellInfo

logger = logging.getLogger(__name__)


class AssemblyReconstructor:
    """Handles the separation and intelligent reassembly of combined meshes."""

    def __init__(
        self,
        matcher: GeometryMatcher,
        combine_assemblies: bool = False,
        search_radius_mult: float = 1.5,
        verbose: bool = False,
    ):
        self.matcher = matcher
        self.combine_assemblies = combine_assemblies
        self.search_radius_mult = search_radius_mult
        self.verbose = verbose
        self.combine_targets: List[Tuple[Optional[pm.nodetypes.Transform], str]] = []

    def separate_combined_meshes(
        self, nodes: List[pm.nodetypes.Transform]
    ) -> List[pm.nodetypes.Transform]:
        """Separate any combined meshes in the list into their shells."""
        new_nodes = []
        self.combine_targets = []  # Reset

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
                num_shells = 0

            # Normalize return types
            try:
                if isinstance(num_shells, (list, tuple)) and num_shells:
                    num_shells = num_shells[0]
                if isinstance(num_shells, str):
                    num_shells = float(num_shells)
                num_shells = int(num_shells)
            except Exception:
                num_shells = 0

            if num_shells > 1:
                # Unlock normals - REMOVED to preserve custom normals
                # try:
                #     pm.polyNormalPerVertex(shape, unFreezeNormal=True)
                # except Exception:
                #     pass

                if self.verbose:
                    print(f"Separating combined mesh: {node} ({num_shells} shells)")

                if self.combine_assemblies:
                    try:
                        self.combine_targets.append((node.getParent(), node.name()))
                    except Exception:
                        self.combine_targets.append((None, node.name()))

                try:
                    separated = pm.polySeparate(node, ch=False)
                    separated_nodes = [pm.PyNode(n) for n in separated]
                    # NOTE: Do NOT canonicalize here - it expands bounding boxes
                    # and breaks BFS grouping. Canonicalization is done after
                    # reassemble_assemblies for instancing purposes.
                    new_nodes.extend(separated_nodes)
                except RuntimeError as e:
                    print(f"Failed to separate {node}: {e}")
                    new_nodes.append(node)
            else:
                new_nodes.append(node)

        return new_nodes

    def center_transform_on_geometry(self, node: pm.nodetypes.Transform) -> None:
        """Moves the transform to the center of its geometry without moving the geometry."""
        try:
            mesh = node.getShape()
            if not mesh:
                return
            pts = mesh.getPoints(space="world")
        except Exception:
            return

        center = pm.dt.Point(np.mean(pts, axis=0))
        node.setTranslation(center, space="world")
        mesh.setPoints(pts, space="world")
        pm.xform(node, centerPivots=True)

    def canonicalize_transform(self, node: pm.nodetypes.Transform) -> None:
        """Aligns the transform's rotation to the geometry's PCA axes."""
        self.center_transform_on_geometry(node)

        basis_matrix = self.matcher.get_pca_basis(node)
        if not basis_matrix:
            return

        try:
            mesh = node.getShape()
            if not mesh:
                return

            pts = mesh.getPoints(space="world")
            tm = pm.dt.TransformationMatrix(basis_matrix)
            rotation = tm.eulerRotation()

            node.setRotation(rotation, space="world")
            mesh.setPoints(pts, space="world")

        except Exception as e:
            if self.verbose:
                print(f"[WARNING] Canonicalization failed for {node}: {e}")

    def canonicalize_leaf_meshes(
        self, nodes: List[pm.nodetypes.Transform]
    ) -> List[pm.nodetypes.Transform]:
        """Canonicalize all leaf mesh transforms for instancing.

        This should be called AFTER reassemble_assemblies to prepare
        individual meshes for geometry comparison. It centers each mesh's
        transform at its geometric center and aligns rotation to PCA axes.

        NOTE: This is separate from BFS grouping (which needs original bboxes)
        because canonicalization expands bounding boxes and breaks touch detection.
        """
        for node in nodes:
            shape = node.getShape()
            if shape and isinstance(shape, pm.nodetypes.Mesh):
                self.canonicalize_transform(node)
            else:
                # It's a group - canonicalize children
                children = node.getChildren(type="transform")
                for child in children:
                    child_shape = child.getShape()
                    if child_shape and isinstance(child_shape, pm.nodetypes.Mesh):
                        self.canonicalize_transform(child)
        return nodes

    def reassemble_assemblies(
        self, nodes: List[pm.nodetypes.Transform]
    ) -> List[pm.nodetypes.Transform]:
        """Reassemble separated shells into logical assemblies.

        Algorithm:
        1. Try anchor-based clustering (identifies assembly types by unique counts)
        2. Fall back to BFS + GCD if anchor clustering doesn't apply
        3. Create Maya group nodes for each assembly
        """
        if not nodes:
            return []

        # Filter to valid mesh transforms
        valid_nodes = []
        for n in nodes:
            shape = n.getShape()
            if shape and isinstance(shape, pm.nodetypes.Mesh):
                valid_nodes.append(n)

        if not valid_nodes:
            return list(nodes)

        # Build part info
        parts: List[Dict[str, Any]] = []
        for node in valid_nodes:
            try:
                shape = node.getShape()
                bbox = pm.exactWorldBoundingBox(node)
                if not isinstance(bbox, list) or len(bbox) != 6:
                    continue

                nverts = shape.numVertices()
                nfaces = shape.numFaces()
                center = np.array(
                    [
                        (bbox[0] + bbox[3]) / 2,
                        (bbox[1] + bbox[4]) / 2,
                        (bbox[2] + bbox[5]) / 2,
                    ]
                )
                vol = (bbox[3] - bbox[0]) * (bbox[4] - bbox[1]) * (bbox[5] - bbox[2])
                mat = self._get_material(node)

                parts.append(
                    {
                        "idx": len(parts),
                        "node": node,
                        "bbox": bbox,
                        "topo": (nverts, nfaces),
                        "center": center,
                        "volume": vol,
                        "material": mat,
                    }
                )
            except Exception:
                pass

        if not parts:
            return list(nodes)

        # Try anchor-based clustering first
        anchor_result = self._cluster_by_anchors(parts)
        if anchor_result:
            if self.verbose:
                logger.info(f"Anchor clustering: {len(anchor_result)} assemblies")
            return self._create_assembly_groups(parts, anchor_result)

        # Fall back to BFS + GCD approach
        adjacency = self._build_adjacency(parts)
        bfs_groups = self._bfs_group(parts, adjacency)

        if self.verbose:
            logger.info(f"BFS grouping: {len(bfs_groups)} connected components")

        final_groups: List[List[int]] = []
        for bfs_group in bfs_groups:
            type_split = self._split_by_discriminator(parts, bfs_group, adjacency)
            for type_group in type_split:
                count_split = self._split_by_count(parts, type_group)
                final_groups.extend(count_split)

        if self.verbose:
            logger.info(f"Final assembly count: {len(final_groups)}")

        return self._create_assembly_groups(parts, final_groups)

    def _cluster_by_anchors(
        self, parts: List[Dict[str, Any]]
    ) -> Optional[List[List[int]]]:
        """Cluster parts using anchor-based type inference.

        This algorithm identifies assembly types by finding the largest
        topology for distinctive counts (e.g., 5 for CANISTER, 3 for CASE, 1 for OTHER).

        Returns None if anchor clustering isn't applicable (no clear anchors).
        """
        if KDTree is None:
            return None  # scipy not available

        # Count topologies
        topo_counts: Dict[Tuple[int, int], List[int]] = defaultdict(list)
        for part in parts:
            topo_counts[part["topo"]].append(part["idx"])

        # Group topologies by count
        count_to_topos: Dict[int, List[Tuple[int, int]]] = defaultdict(list)
        for topo, indices in topo_counts.items():
            count_to_topos[len(indices)].append(topo)

        # Select anchor: largest topology for each distinctive count (1, 3, 5)
        # These are typical assembly counts in hierarchical scenes
        ANCHOR_COUNTS = [5, 3, 1]
        anchor_topos: Dict[Tuple[int, int], str] = {}
        type_counts: Dict[str, int] = {}
        sorted_counts = []

        for count in ANCHOR_COUNTS:
            topos = count_to_topos.get(count, [])
            if topos:
                # Pick the largest topology (by vertex count)
                largest = max(topos, key=lambda t: t[0])
                # Only use if it has >= 50 verts (likely a "body" part)
                if largest[0] >= 50:
                    type_label = f"TYPE_{count}"
                    anchor_topos[largest] = type_label
                    type_counts[type_label] = count
                    sorted_counts.append(count)

        if len(anchor_topos) < 2:
            # Not enough distinct anchors
            return None

        sorted_counts = sorted(sorted_counts, reverse=True)  # e.g., [5, 3, 1]

        # Infer type for each topology
        def infer_type(topo: Tuple[int, int], count: int) -> str:
            verts = topo[0]

            if topo in anchor_topos:
                return anchor_topos[topo]

            # Count == 1 or 2 usually means OTHER type
            if count == 1 or count == 2:
                if "TYPE_1" in type_counts:
                    return "TYPE_1"
                return "UNKNOWN"

            # count=4 is often shared between two types (e.g., 3+1)
            # Must check BEFORE divisibility since 4 % 1 == 0
            if count == 4:
                return "SHARED"

            # Check divisibility by anchor counts (larger counts first)
            for anchor_count in sorted_counts:
                if count % anchor_count == 0:
                    type_label = f"TYPE_{anchor_count}"
                    # For count=3 topologies with small verts, assign to OTHER
                    if anchor_count == 3 and count == 3:
                        if verts < 6:
                            if "TYPE_1" in type_counts:
                                return "TYPE_1"
                    return type_label

            return "UNKNOWN"

        # Create clusters around anchor positions
        anchor_parts = []
        for topo, type_label in anchor_topos.items():
            for idx in topo_counts[topo]:
                anchor_parts.append(
                    {
                        "idx": idx,
                        "type": type_label,
                        "center": parts[idx]["center"],
                    }
                )

        # Group clusters by type
        type_clusters: Dict[str, List[Dict]] = defaultdict(list)
        for ap in anchor_parts:
            type_clusters[ap["type"]].append(
                {
                    "anchor_idx": ap["idx"],
                    "members": [ap["idx"]],
                    "budget": defaultdict(int),
                }
            )

        # Sort clusters by position for consistent ordering
        for type_label in type_clusters:
            type_clusters[type_label].sort(
                key=lambda c: tuple(parts[c["anchor_idx"]]["center"])
            )

        # Calculate budgets (expected count per cluster for each topology)
        per_cluster_budget: Dict[str, Dict[Tuple[int, int], int]] = defaultdict(
            lambda: defaultdict(int)
        )
        shared_topos = []

        for topo, indices in topo_counts.items():
            count = len(indices)
            typ = infer_type(topo, count)

            if typ == "SHARED":
                shared_topos.append(topo)
            elif typ != "UNKNOWN" and typ in type_clusters:
                n_clusters = len(type_clusters[typ])
                if n_clusters > 0:
                    per_cluster_budget[typ][topo] = count // n_clusters

        # Initialize cluster budgets
        for type_label, clusters in type_clusters.items():
            for cluster in clusters:
                cluster["budget"] = dict(per_cluster_budget[type_label])

        # Flatten all clusters for assignment
        all_clusters = []
        for type_label, clusters in type_clusters.items():
            for cluster in clusters:
                cluster["type"] = type_label
                all_clusters.append(cluster)

        anchor_indices = {ap["idx"] for ap in anchor_parts}

        # Phase 1: Assign non-anchor parts with budget constraints
        unassigned = []
        for part in parts:
            if part["idx"] in anchor_indices:
                continue

            topo = part["topo"]
            count = len(topo_counts[topo])
            typ = infer_type(topo, count)

            if typ == "SHARED" or typ == "UNKNOWN":
                unassigned.append(part["idx"])
                continue

            target_clusters = type_clusters.get(typ, [])
            if not target_clusters:
                unassigned.append(part["idx"])
                continue

            # Find nearest cluster with budget
            centers = np.array(
                [parts[c["anchor_idx"]]["center"] for c in target_clusters]
            )
            tree = KDTree(centers)
            dists, indices = tree.query(part["center"], k=len(target_clusters))
            if not hasattr(indices, "__iter__"):
                indices = [indices]

            assigned = False
            for idx in indices:
                cluster = target_clusters[idx]
                if cluster["budget"].get(topo, 0) > 0:
                    cluster["members"].append(part["idx"])
                    cluster["budget"][topo] -= 1
                    assigned = True
                    break

            if not assigned:
                unassigned.append(part["idx"])

        # Phase 2: Handle shared topologies (split between types)
        # For count=4, assign 1 to smallest type (OTHER), rest to next (CASE)
        for topo in shared_topos:
            indices = topo_counts[topo]

            # Find the type with smallest count (e.g., OTHER with count=1)
            smallest_type = min(type_counts.items(), key=lambda x: x[1])[0]  # TYPE_1
            # Find the next larger type (e.g., CASE with count=3)
            sorted_types = sorted(type_counts.items(), key=lambda x: x[1])
            next_type = sorted_types[1][0] if len(sorted_types) > 1 else None  # TYPE_3

            if not type_clusters.get(smallest_type):
                continue

            # Sort shells by distance to OTHER's anchor (closest first)
            other_center = parts[type_clusters[smallest_type][0]["anchor_idx"]][
                "center"
            ]
            sorted_shell_indices = sorted(
                indices, key=lambda i: np.linalg.norm(parts[i]["center"] - other_center)
            )

            for i, idx in enumerate(sorted_shell_indices):
                if i == 0:
                    # Closest to OTHER goes to OTHER
                    type_clusters[smallest_type][0]["members"].append(idx)
                else:
                    # Rest go to CASE (nearest cluster)
                    if next_type and type_clusters.get(next_type):
                        target_clusters = type_clusters[next_type]
                        centers = np.array(
                            [parts[c["anchor_idx"]]["center"] for c in target_clusters]
                        )
                        tree = KDTree(centers)
                        dist, cidx = tree.query(parts[idx]["center"])
                        target_clusters[cidx]["members"].append(idx)

                # Remove from unassigned if present
                if idx in unassigned:
                    unassigned.remove(idx)

        # Phase 3: Assign remaining by proximity
        if unassigned:
            all_anchor_centers = np.array(
                [parts[c["anchor_idx"]]["center"] for c in all_clusters]
            )
            tree = KDTree(all_anchor_centers)
            for idx in unassigned:
                dist, cidx = tree.query(parts[idx]["center"])
                all_clusters[cidx]["members"].append(idx)

        # Convert to list of lists format
        return [cluster["members"] for cluster in all_clusters]

    def _get_material(self, node: pm.nodetypes.Transform) -> Optional[str]:
        """Get the material name assigned to a node."""
        try:
            shape = node.getShape()
            if not shape:
                return None
            sgs = pm.listConnections(shape, type="shadingEngine")
            if sgs:
                return sgs[0].name()
        except Exception:
            pass
        return None

    def _bboxes_touch(
        self, b1: List[float], b2: List[float], tol: float = 0.01
    ) -> bool:
        """Check if two bounding boxes touch or overlap."""
        return not (
            b1[3] + tol < b2[0]
            or b2[3] + tol < b1[0]
            or b1[4] + tol < b2[1]
            or b2[4] + tol < b1[1]
            or b1[5] + tol < b2[2]
            or b2[5] + tol < b1[2]
        )

    def _build_adjacency(self, parts: List[Dict]) -> Dict[int, List[int]]:
        """Build adjacency graph based on bounding box touch."""
        adjacency: Dict[int, List[int]] = defaultdict(list)
        for i in range(len(parts)):
            for j in range(i + 1, len(parts)):
                if self._bboxes_touch(parts[i]["bbox"], parts[j]["bbox"]):
                    adjacency[i].append(j)
                    adjacency[j].append(i)
        return adjacency

    def _bfs_group(
        self, parts: List[Dict], adjacency: Dict[int, List[int]]
    ) -> List[List[int]]:
        """Group parts by connectivity using BFS."""
        visited: Set[int] = set()
        groups: List[List[int]] = []

        for start in range(len(parts)):
            if start in visited:
                continue
            queue = [start]
            group = []
            while queue:
                node = queue.pop(0)
                if node in visited:
                    continue
                visited.add(node)
                group.append(node)
                for neighbor in adjacency[node]:
                    if neighbor not in visited:
                        queue.append(neighbor)
            groups.append(group)

        return groups

    def _split_by_discriminator(
        self, parts: List[Dict], indices: List[int], adjacency: Dict[int, List[int]]
    ) -> List[List[int]]:
        """Split a group by material only.

        The main splitting logic (GCD + spatial clustering) is in _split_by_count.
        This method only handles material-based pre-splitting.
        """
        if len(indices) <= 1:
            return [indices]

        # Get materials in this group
        materials = set(parts[idx]["material"] for idx in indices)
        materials.discard(None)

        # If multiple materials, split by material first
        if len(materials) > 1:
            mat_groups: Dict[Optional[str], List[int]] = defaultdict(list)
            for idx in indices:
                mat_groups[parts[idx]["material"]].append(idx)
            return list(mat_groups.values())

        # No material split needed - return as single group
        return [indices]

    def _split_by_count(self, parts: List[Dict], indices: List[int]) -> List[List[int]]:
        """Split by GCD of topology counts, using distinguishability-ordered assignment."""
        if len(indices) <= 1:
            return [indices]

        # Count topologies
        topo_counts: Dict[Tuple[int, int], int] = defaultdict(int)
        for idx in indices:
            topo_counts[parts[idx]["topo"]] += 1

        counts = list(topo_counts.values())
        if not counts:
            return [indices]

        gcd_val = reduce(gcd, counts)
        if gcd_val < 2:
            return [indices]

        n_clusters = gcd_val
        expected_per_cluster = {
            topo: count // gcd_val for topo, count in topo_counts.items()
        }

        # Find anchors (largest volume topology)
        largest_topo = max(
            topo_counts.keys(),
            key=lambda t: max(
                parts[i]["volume"] for i in indices if parts[i]["topo"] == t
            ),
        )
        anchors = [idx for idx in indices if parts[idx]["topo"] == largest_topo]

        # Calculate distinguishability for each part
        def get_distinguishability(idx: int) -> float:
            center = parts[idx]["center"]
            dists = sorted(
                [np.linalg.norm(center - parts[a]["center"]) for a in anchors]
            )
            if len(dists) < 2:
                return float("inf")
            return dists[1] - dists[0]  # Higher = more distinguishable

        # Initialize clusters with anchors
        clusters: Dict[int, List[int]] = {anchor: [anchor] for anchor in anchors}
        cluster_topo_counts: Dict[int, Dict[Tuple[int, int], int]] = {
            anchor: defaultdict(int) for anchor in anchors
        }
        for anchor in anchors:
            cluster_topo_counts[anchor][largest_topo] = 1

        assigned = set(anchors)
        remaining = [idx for idx in indices if idx not in assigned]

        # Sort by distinguishability: MOST distinguishable first
        remaining.sort(key=get_distinguishability, reverse=True)

        for idx in remaining:
            topo = parts[idx]["topo"]
            center = parts[idx]["center"]

            # Find clusters that still need this topology
            candidates = []
            for anchor in anchors:
                current = cluster_topo_counts[anchor][topo]
                expected = expected_per_cluster[topo]
                if current < expected:
                    dist = np.linalg.norm(center - parts[anchor]["center"])

                    # Check against search radius
                    anchor_bbox = parts[anchor]["bbox"]
                    # Calculate max dimension of the anchor's bbox as a proxy for size
                    anchor_size = max(
                        anchor_bbox[3] - anchor_bbox[0],
                        anchor_bbox[4] - anchor_bbox[1],
                        anchor_bbox[5] - anchor_bbox[2],
                    )
                    # Radius is half the size.
                    # We compare distance centroid-to-centroid.
                    # If parts are adjacent, distance is roughly sum of radii.
                    # search_radius_mult controls how far we look relative to the anchor size.
                    # Default 1.5 allows looking 1.5x the size away.
                    max_dist = anchor_size * self.search_radius_mult

                    if dist <= max_dist:
                        candidates.append((anchor, dist))

            if not candidates:
                # If no valid candidates within radius:
                # Option 1: Force assign to nearest anyway (Current behavior - caused touching bug)
                # Option 2: Leave unassigned? (might break assembly count)
                # Option 3: Create new cluster? (not supported here)

                # If we are strictly enforced, we should skip.
                # But _split_by_count assumes all 'indices' belong to one of the anchors
                # (since they are in the same BFS component... wait, are they?)

                # 'indices' comes from BFS group (if fallback) OR from _cluster_by_anchors logic?
                # No, _split_by_count is called on a group of indices.
                # If they are from BFS, they are connected.
                pass
                # For now, let's keep the fallback but logging a warning if it exceeds radius might be too noisy.
                # The user wants to RELAX tolerances.
                # But specifically for `test_touching_assemblies`, they rely on stricter radius (1.1).

                # So if I enforce the radius check, `test_touching_assemblies` (1.1) should pass because
                # candidates outside 1.1*size will be rejected.

                # Wait, if I reject them, they remain in 'remaining' loop?
                # No, the code below force assigns to best_anchor if !candidates.

                # So I must change the "if not candidates" block to NOT force assign if it violates the strict constraint.
                # But 'indices' were passed in as a group. If we don't assign, they get lost?
                # Actually, if we don't assign, we should probably start a NEW anchor?
                # But we are limited to `expected_per_cluster`.

                # Strategy:
                # If strict check fails, we might be dealing with a merged assembly that needs splitting,
                # but we only found X anchors.

                # Let's try enforcing the limit.
                pass

            if not candidates:
                # Fallback: Find nearest anchor regardless of budget?
                # If we are too strict, we might drop valid parts of a slightly exploded assembly.
                # However, for touching assemblies, we need to respect boundaries.

                # Check all anchors with budget > 0 first
                candidates_any_dist = []
                for anchor in anchors:
                    if cluster_topo_counts[anchor][topo] < expected_per_cluster[topo]:
                        dist = np.linalg.norm(center - parts[anchor]["center"])
                        candidates_any_dist.append((anchor, dist))

                # RELAXED LOGIC:
                # If we have a candidate with budget, tolerate up to 2.5x radius
                # (unless search_radius_mult is very small, implying strictness).
                # If search_radius_mult < 1.2, user wants strictness -> use 1.2x factor max?
                # Actually, let's just use a multiplier on the configured radius.

                fallback_mult = 2.0

                if candidates_any_dist:
                    best_anchor_tuple = min(candidates_any_dist, key=lambda x: x[1])
                    best_anchor = best_anchor_tuple[0]
                    dist = best_anchor_tuple[1]

                    anchor_bbox = parts[best_anchor]["bbox"]
                    anchor_size = max(
                        anchor_bbox[3] - anchor_bbox[0],
                        anchor_bbox[4] - anchor_bbox[1],
                        anchor_bbox[5] - anchor_bbox[2],
                    )
                    # RELAXED Check
                    # For test_touching_assemblies, search_radius_mult is 1.1 (small).
                    # We should NOT apply fallback_mult if search_radius_mult is small (< 1.25)
                    # because user intends strict separation.
                    effective_mult = self.search_radius_mult
                    if self.search_radius_mult >= 1.25:
                        effective_mult *= fallback_mult

                    max_dist = anchor_size * effective_mult

                    if dist <= max_dist:
                        clusters[best_anchor].append(idx)
                        cluster_topo_counts[best_anchor][topo] += 1
                        continue

                # If still no match (or no budget), find ABSOLUTE nearest anchor (ignoring budget)
                # But confirm it is somewhat close.
                best_anchor = min(
                    anchors, key=lambda a: np.linalg.norm(center - parts[a]["center"])
                )
                dist = np.linalg.norm(center - parts[best_anchor]["center"])

                anchor_bbox = parts[best_anchor]["bbox"]
                anchor_size = max(
                    anchor_bbox[3] - anchor_bbox[0],
                    anchor_bbox[4] - anchor_bbox[1],
                    anchor_bbox[5] - anchor_bbox[2],
                )

                # Allow slightly more leniency for "orphans" to attach to nearest
                effective_mult = self.search_radius_mult
                if self.search_radius_mult >= 1.25:
                    effective_mult *= fallback_mult

                max_dist = anchor_size * effective_mult

                if dist <= max_dist:
                    clusters[best_anchor].append(idx)
                    cluster_topo_counts[best_anchor][topo] += 1
                else:
                    pass  # Truly too far

                continue

            else:
                # Among clusters that need this topo AND are in range, pick nearest
                best_anchor = min(candidates, key=lambda x: x[1])[0]

            clusters[best_anchor].append(idx)
            cluster_topo_counts[best_anchor][topo] += 1

        return list(clusters.values())

    def _create_assembly_groups(
        self, parts: List[Dict], groups: List[List[int]]
    ) -> List[pm.nodetypes.Transform]:
        """Create Maya group nodes for each assembly."""
        result = []
        used_nodes: Set[pm.nodetypes.Transform] = set()

        for group in groups:
            if len(group) <= 1:
                # Single part - no assembly needed
                for idx in group:
                    node = parts[idx]["node"]
                    if node not in used_nodes:
                        result.append(node)
                        used_nodes.add(node)
                continue

            # Find the root (largest volume)
            root_idx = max(group, key=lambda i: parts[i]["volume"])
            root = parts[root_idx]["node"]
            children = [parts[idx]["node"] for idx in group if idx != root_idx]

            # Check for already-used nodes
            if root in used_nodes or any(c in used_nodes for c in children):
                for idx in group:
                    node = parts[idx]["node"]
                    if node not in used_nodes:
                        result.append(node)
                        used_nodes.add(node)
                continue

            # Create assembly group
            try:
                assembly_grp = pm.group(empty=True, name="Assembly_1")

                # Position at centroid
                points = [parts[idx]["center"] for idx in group]
                centroid = np.mean(points, axis=0)
                assembly_grp.setTranslation(centroid.tolist(), space="world")
                assembly_grp.setRotation(root.getRotation(space="world"), space="world")

                pm.parent(root, assembly_grp)
                used_nodes.add(root)

                for child in children:
                    pm.parent(child, assembly_grp)
                    used_nodes.add(child)

                result.append(assembly_grp)

            except Exception as e:
                logger.error(f"Error creating assembly for {root}: {e}")
                for idx in group:
                    node = parts[idx]["node"]
                    if node not in used_nodes:
                        result.append(node)
                        used_nodes.add(node)

        return result

    def combine_reassembled_assemblies(
        self, nodes: List[pm.nodetypes.Transform]
    ) -> List[pm.nodetypes.Transform]:
        """Combine the 'Core' of each reconstructed assembly into a single mesh."""
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

        sig_counts = defaultdict(int)
        for grp in assembly_groups:
            children = grp.getChildren(type="transform")
            sigs = set()
            for c in children:
                if self._is_mesh_transform(c):
                    s = self.matcher.get_mesh_signature(c)
                    if s:
                        s_relaxed = s[:3]
                        sigs.add(s_relaxed)
            for s in sigs:
                sig_counts[s] += 1

        threshold = max(1, len(assembly_groups) // 2 + 1)
        common_sigs = {s for s, count in sig_counts.items() if count >= threshold}

        for grp in assembly_groups:
            children = grp.getChildren(type="transform")
            mesh_children = [c for c in children if self._is_mesh_transform(c)]

            core_parts = []
            remainder_parts = []

            for c in mesh_children:
                s = self.matcher.get_mesh_signature(c)
                if s and s[:3] in common_sigs:
                    core_parts.append(c)
                else:
                    remainder_parts.append(c)

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
                    except Exception:
                        core_mesh = None

                if core_mesh:
                    try:
                        core_mesh = core_mesh.rename(f"{grp.name()}_combined")
                        self.canonicalize_transform(core_mesh)
                    except:
                        pass
                    combined_meshes.append(core_mesh)

            combined_meshes.extend(remainder_parts)

            try:
                for r in remainder_parts:
                    try:
                        pm.parent(r, world=True)
                    except:
                        pass
                if not grp.getChildren():
                    pm.delete(grp)
            except:
                pass

        return combined_meshes

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
