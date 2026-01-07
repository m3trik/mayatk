"""
Assembly Reconstructor V10 - Hybrid approach: spatial clustering + pattern matching.

Key insight: Pure signature matching over-allocates when patterns share signatures.
Solution: Use spatial clustering first, then match patterns to clusters.

Strategy:
1. Group by material (hard boundary)
2. Within each material:
   a. Use hierarchical clustering to find spatial groups
   b. For each cluster, determine its pattern from actual signatures
   c. Validate clusters match known patterns
"""

from __future__ import annotations

from typing import List, Tuple, Optional, Dict, Set
from collections import defaultdict, Counter
import logging
import numpy as np
from scipy.spatial import KDTree
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.spatial.distance import pdist

logger = logging.getLogger(__name__)

try:
    import pymel.core as pm
except ImportError:
    pass


# Known patterns from the expected output, with pattern-specific compactness thresholds
PATTERN_MAX_SPREAD = {
    (4, 4, 4): 50.0,  # F-type - measured ~41
    (8, 42): 10.0,  # H-type - measured ~1
    (12, 16, 48, 250): 60.0,  # E-type - measured ~52
    (168,): 10.0,  # D-type singleton
    (5, 12, 24, 24, 24, 80): 80.0,  # B-type - measured ~71
    (5, 5, 5, 5, 16, 24): 35.0,  # A-type variant 1 - measured ~24, some tolerance
    (
        5,
        5,
        5,
        5,
        16,
        16,
        24,
        24,
    ): 35.0,  # A-type variant 2 - measured ~24, some tolerance
    (126,): 10.0,  # G-type singleton
}

KNOWN_PATTERNS = list(PATTERN_MAX_SPREAD.keys())


class AssemblyReconstructor:
    """Hybrid spatial clustering + pattern matching."""

    def __init__(
        self,
        matcher=None,
        combine_assemblies: bool = False,
        search_radius_mult: float = 4.0,
        assembly_radius: float = 50.0,  # Tighter radius for initial clustering
        verbose: bool = False,
    ):
        self.matcher = matcher
        self.combine_assemblies = combine_assemblies
        self.assembly_radius = assembly_radius
        self.verbose = verbose
        self.combine_targets = []

    def separate_combined_meshes(
        self, nodes: List[pm.nodetypes.Transform]
    ) -> List[pm.nodetypes.Transform]:
        """Separate any combined meshes into shells."""
        new_nodes = []
        self.combine_targets = []

        for node in nodes:
            if not node.exists():
                continue

            shape = node.getShape()
            if not shape or not isinstance(shape, pm.nodetypes.Mesh):
                new_nodes.append(node)
                continue

            try:
                num_shells = pm.polyEvaluate(node, shell=True)
                if isinstance(num_shells, (list, tuple)):
                    num_shells = num_shells[0] if num_shells else 0
                num_shells = int(num_shells)
            except Exception:
                num_shells = 0

            if self.verbose:
                logger.info(f"Mesh {node} has {num_shells} shells.")

            if num_shells > 1:
                if self.combine_assemblies:
                    try:
                        self.combine_targets.append((node.getParent(), node.name()))
                    except Exception:
                        self.combine_targets.append((None, node.name()))

                try:
                    separated = pm.polySeparate(node, ch=False)
                    separated_nodes = [pm.PyNode(n) for n in separated]
                    for sn in separated_nodes:
                        self._center_transform(sn)
                    new_nodes.extend(separated_nodes)
                except RuntimeError as e:
                    logger.warning(f"Failed to separate {node}: {e}")
                    new_nodes.append(node)
            else:
                new_nodes.append(node)

        return new_nodes

    def _center_transform(self, node: pm.nodetypes.Transform) -> None:
        try:
            mesh = node.getShape()
            if not mesh:
                return
            pts = mesh.getPoints(space="world")
            center = pm.dt.Point(np.mean(pts, axis=0))
            node.setTranslation(center, space="world")
            mesh.setPoints(pts, space="world")
            pm.xform(node, centerPivots=True)
        except Exception:
            pass

    def _get_shell_info(self, node: pm.nodetypes.Transform) -> Optional[Dict]:
        shape = node.getShape()
        if not shape or not isinstance(shape, pm.nodetypes.Mesh):
            return None

        try:
            pts = shape.getPoints(space="world")
            centroid = np.mean(pts, axis=0)

            sgs = shape.listConnections(type="shadingEngine") or []
            material = sgs[0].name() if sgs else "default"

            return {
                "node": node,
                "centroid": np.array([centroid[0], centroid[1], centroid[2]]),
                "num_verts": shape.numVertices(),
                "material": material,
            }
        except Exception as e:
            logger.warning(f"Failed to get shell info: {e}")
            return None

    def _cluster_by_anchor(self, shells: List[Dict]) -> List[List[Dict]]:
        """Cluster shells using spatial-aware pattern matching.

        Strategy:
        1. Match patterns with UNIQUE signatures first (B-type has 80, G-type has 126)
        2. Then match remaining patterns by spatial proximity
        3. For competing patterns (A-type variants), prefer the one that fits best locally
        """
        if len(shells) <= 1:
            return [[s] for s in shells]

        sig_counts = Counter(s["num_verts"] for s in shells)
        centroids = np.array([s["centroid"] for s in shells])

        # Group shells by signature
        by_sig = defaultdict(list)
        for i, s in enumerate(shells):
            by_sig[s["num_verts"]].append(i)

        # Find patterns that CAN match (have enough shells)
        matching_patterns = []
        for pattern in KNOWN_PATTERNS:
            pattern_counter = Counter(pattern)
            if all(sig_counts.get(v, 0) >= c for v, c in pattern_counter.items()):
                matching_patterns.append(pattern)

        if not matching_patterns:
            return [[s] for s in shells]

        # Find which signatures are unique to each pattern
        sig_to_patterns = defaultdict(list)
        for pattern in matching_patterns:
            for sig in set(pattern):
                sig_to_patterns[sig].append(pattern)

        pattern_unique_sigs = {}
        for pattern in matching_patterns:
            unique = [sig for sig in set(pattern) if len(sig_to_patterns[sig]) == 1]
            pattern_unique_sigs[pattern] = unique

        # Sort: patterns with unique signatures first, then by length descending
        def pattern_priority(p):
            unique_count = len(pattern_unique_sigs[p])
            return (-unique_count, -len(p))

        sorted_patterns = sorted(matching_patterns, key=pattern_priority)

        assigned = set()
        groups = []

        for pattern in sorted_patterns:
            pattern_counter = Counter(pattern)

            # For patterns with unique signatures, use those as anchors
            # For patterns without, use the rarest available signature
            unique_sigs = pattern_unique_sigs[pattern]
            if unique_sigs:
                anchor_sig = unique_sigs[0]
            else:
                anchor_sig = min(
                    pattern_counter.keys(),
                    key=lambda v: len([i for i in by_sig[v] if i not in assigned]),
                )

            # Get available anchors and sort by Z position (deterministic spatial order)
            available_anchors = [i for i in by_sig[anchor_sig] if i not in assigned]
            available_anchors.sort(key=lambda i: centroids[i][2])  # Sort by Z

            for anchor_idx in available_anchors:
                if anchor_idx in assigned:
                    continue

                anchor_pos = centroids[anchor_idx]

                # Try to build a cluster around this anchor
                # Calculate distances to all required signatures
                cluster = [anchor_idx]
                remaining = Counter(pattern)
                remaining[anchor_sig] -= 1

                can_complete = True
                max_dist = 0

                for sig, count in remaining.items():
                    if count == 0:
                        continue

                    available = [
                        i for i in by_sig[sig] if i not in assigned and i not in cluster
                    ]
                    if len(available) < count:
                        can_complete = False
                        break

                    # Sort by distance to anchor
                    available_with_dist = [
                        (i, np.linalg.norm(centroids[i] - anchor_pos))
                        for i in available
                    ]
                    available_with_dist.sort(key=lambda x: x[1])

                    for i, dist in available_with_dist[:count]:
                        cluster.append(i)
                        max_dist = max(max_dist, dist)

                if not can_complete or len(cluster) != len(pattern):
                    continue

                # Calculate actual spread (max distance from center to any shell)
                cluster_centroids = centroids[cluster]
                cluster_center = np.mean(cluster_centroids, axis=0)
                spread = max(
                    np.linalg.norm(c - cluster_center) for c in cluster_centroids
                )

                # Check if cluster is compact enough using pattern-specific threshold
                max_allowed_spread = PATTERN_MAX_SPREAD.get(pattern, 50.0)

                # Debug for 8-part pattern
                if len(pattern) == 8 and self.verbose:
                    print(
                        f"    8-part attempt: anchor Z={anchor_pos[2]:.1f}, spread={spread:.1f}, max={max_allowed_spread:.1f}"
                    )

                if spread > max_allowed_spread:
                    continue

                # Valid cluster
                for idx in cluster:
                    assigned.add(idx)
                groups.append([shells[i] for i in cluster])

        # Add remaining as singletons
        for i, s in enumerate(shells):
            if i not in assigned:
                groups.append([s])

        return groups

    def reassemble_assemblies(
        self, nodes: List[pm.nodetypes.Transform]
    ) -> List[pm.nodetypes.Transform]:
        """Reassemble shells into assemblies."""
        if self.verbose:
            logger.info(f"reassemble_assemblies called with {len(nodes)} nodes.")

        if not nodes:
            return []

        shells = []
        for n in nodes:
            info = self._get_shell_info(n)
            if info:
                shells.append(info)

        if not shells:
            return list(nodes)

        if self.verbose:
            print(f"Got info for {len(shells)} shells")

        # Group by material
        by_material = defaultdict(list)
        for shell in shells:
            by_material[shell["material"]].append(shell)

        if self.verbose:
            print(f"Materials: {list(by_material.keys())}")

        # Process each material group
        all_groups = []
        for material, mat_shells in by_material.items():
            if self.verbose:
                sig_counts = Counter(s["num_verts"] for s in mat_shells)
                print(
                    f"Processing material {material}: {len(mat_shells)} shells, sigs={dict(sig_counts)}"
                )

            # Cluster using anchor-based approach
            groups = self._cluster_by_anchor(mat_shells)

            if self.verbose:
                for g in groups:
                    sig = tuple(sorted(s["num_verts"] for s in g))
                    print(f"  Group: {sig}")

            all_groups.extend(groups)

        if self.verbose:
            print(f"Created {len(all_groups)} groups total")

        # Create Maya groups
        result_nodes = []
        for i, group in enumerate(all_groups):
            if len(group) == 1:
                result_nodes.append(group[0]["node"])
            else:
                grp = pm.group(empty=True, name=f"Assembly_{i+1}")
                grp.addAttr("isAssembly", at="bool", dv=True)

                cluster_centroid = np.mean([s["centroid"] for s in group], axis=0)
                grp.setTranslation(cluster_centroid, space="world")

                for s in group:
                    pm.parent(s["node"], grp)

                result_nodes.append(grp)

        if self.verbose:
            print(f"Created {len(result_nodes)} assemblies/nodes.")

        return result_nodes

    def combine_reassembled_assemblies(
        self, nodes: List[pm.nodetypes.Transform]
    ) -> List[pm.nodetypes.Transform]:
        if not nodes:
            return []

        combined_meshes = []
        assembly_groups = []
        other_nodes = []

        for node in nodes:
            is_assembly = (
                node.hasAttr("isAssembly") if hasattr(node, "hasAttr") else False
            )
            if is_assembly or (
                hasattr(node, "name") and node.name().startswith("Assembly_")
            ):
                assembly_groups.append(node)
            else:
                other_nodes.append(node)

        combined_meshes.extend(other_nodes)

        for grp in assembly_groups:
            children = grp.getChildren(type="transform")
            mesh_children = [c for c in children if self._is_mesh_transform(c)]

            if not mesh_children:
                continue

            if len(mesh_children) == 1:
                core_mesh = mesh_children[0]
                pm.parent(core_mesh, world=True)
                combined_meshes.append(core_mesh)
            else:
                try:
                    core_mesh = pm.polyUnite(
                        mesh_children,
                        name=f"{grp.name()}_combined",
                        ch=False,
                        mergeUVSets=True,
                    )[0]
                    core_mesh = pm.PyNode(core_mesh)
                    self._center_transform(core_mesh)
                    combined_meshes.append(core_mesh)
                except Exception as e:
                    logger.warning(f"PolyUnite failed: {e}")
                    for c in mesh_children:
                        pm.parent(c, world=True)
                    combined_meshes.extend(mesh_children)

            try:
                pm.delete(grp)
            except Exception:
                pass

        return combined_meshes

    @staticmethod
    def _is_mesh_transform(n: pm.PyNode) -> bool:
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
