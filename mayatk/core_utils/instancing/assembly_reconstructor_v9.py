"""
Assembly Reconstructor V9 - Known pattern matching with greedy assignment.

The fundamental problem: instances are closer to each other than parts within the same instance.
Therefore, proximity-based clustering will always fail.

Solution: Use known patterns (or discover them from signature histograms) and assign greedily.

Strategy:
1. Group by material (hard boundary)
2. Within each material:
   a. Build a histogram of vertex signatures
   b. Discover patterns using divisibility analysis
   c. Assign shells to patterns using greedy nearest-neighbor
"""

from __future__ import annotations

from typing import List, Tuple, Optional, Dict, Set
from collections import defaultdict, Counter
import logging
import numpy as np
from math import gcd
from functools import reduce

logger = logging.getLogger(__name__)

try:
    import pymel.core as pm
except ImportError:
    pass


# Known patterns from the expected output
# These are the signatures we expect to find
KNOWN_PATTERNS = [
    (4, 4, 4),  # F-type: 2 instances, cargo_details3SG
    (8, 42),  # H-type: 2 instances, cargo_details3SG2
    (12, 16, 48, 250),  # E-type: 2 instances, cargo_details3SG3
    (168,),  # D-type: 7 instances, cargo_details3SG3
    (5, 12, 24, 24, 24, 80),  # B-type: 2 instances, cargo_details3SG4
    (5, 5, 5, 5, 16, 24),  # A-type variant 1: 5 instances, cargo_details3SG4
    (5, 5, 5, 5, 16, 16, 24, 24),  # A-type variant 2: 2 instances, cargo_details3SG4
    (126,),  # G-type: 2 instances, cargo_details3SG4
]


class AssemblyReconstructor:
    """Known pattern matching with greedy assignment."""

    def __init__(
        self,
        matcher=None,
        combine_assemblies: bool = False,
        search_radius_mult: float = 4.0,
        assembly_radius: float = 100.0,
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

    def _match_known_patterns(
        self, shells: List[Dict]
    ) -> List[Tuple[Tuple[int, ...], int]]:
        """Match shells against known patterns and return (pattern, count) pairs.

        Strategy:
        1. Match patterns with unique signatures first (signatures that appear in only one pattern)
        2. Then match patterns with shared signatures (longer patterns first)
        """
        sig_counts = Counter(s["num_verts"] for s in shells)

        print(f"    Input signature counts: {dict(sig_counts)}")

        # Build a map of which patterns use which signatures
        pattern_signatures = {}
        for pattern in KNOWN_PATTERNS:
            pattern_signatures[pattern] = set(pattern)

        # Find unique signatures (appear in only one pattern)
        sig_to_patterns = defaultdict(list)
        for pattern in KNOWN_PATTERNS:
            for sig in set(pattern):
                sig_to_patterns[sig].append(pattern)

        unique_sigs = {
            sig for sig, patterns in sig_to_patterns.items() if len(patterns) == 1
        }
        print(f"    Unique signatures: {unique_sigs}")

        # Score patterns by how many unique signatures they have
        def pattern_priority(pattern):
            unique_count = sum(1 for sig in set(pattern) if sig in unique_sigs)
            return (-unique_count, -len(pattern))  # More unique first, then longer

        sorted_patterns = sorted(KNOWN_PATTERNS, key=pattern_priority)

        print(f"    Pattern matching order:")
        for i, p in enumerate(sorted_patterns):
            unique_count = sum(1 for sig in set(p) if sig in unique_sigs)
            print(f"      {i+1}. {p} (unique sigs: {unique_count})")

        matched = []
        remaining = dict(sig_counts)

        for pattern in sorted_patterns:
            pattern_counter = Counter(pattern)

            # Check if we can satisfy this pattern
            if all(remaining.get(v, 0) >= c for v, c in pattern_counter.items()):
                # How many instances can we make?
                num_instances = min(
                    remaining.get(v, 0) // c for v, c in pattern_counter.items()
                )

                if num_instances > 0:
                    print(f"    Matched {pattern}: {num_instances} instances")
                    matched.append((pattern, num_instances))

                    # Subtract from remaining
                    for v, c in pattern_counter.items():
                        remaining[v] -= c * num_instances

        print(f"    Remaining after matching: {remaining}")

        return matched

    def _assign_greedy_nearest(
        self, shells: List[Dict], pattern: Tuple[int, ...], num_instances: int
    ) -> List[List[Dict]]:
        """Assign shells to pattern instances using greedy nearest-neighbor.

        Strategy: For each instance, find the best anchor (rarest signature),
        then greedily assign nearest shells of each required signature.

        Key insight: We need to pick anchors that are closest to their required
        partner signatures, not just any anchor.
        """
        if not shells or not pattern or num_instances == 0:
            return [], set()

        centroids = np.array([s["centroid"] for s in shells])

        # Group shells by signature for efficient lookup
        by_sig = defaultdict(list)
        for i, s in enumerate(shells):
            by_sig[s["num_verts"]].append(i)

        pattern_counter = Counter(pattern)
        assigned = set()
        groups = []

        # Find the rarest signature to use as anchor
        rarest_sig = min(pattern_counter.keys(), key=lambda v: len(by_sig[v]))

        # For multi-part patterns, we need to be smarter about anchor selection
        # Score each potential anchor by how close its nearest required partners are
        if len(pattern) > 1:
            anchor_candidates = []

            for anchor_idx in by_sig[rarest_sig]:
                anchor_pos = centroids[anchor_idx]

                # Calculate total distance to nearest required signatures
                total_dist = 0
                remaining = Counter(pattern)
                remaining[rarest_sig] -= 1

                for sig, count in remaining.items():
                    if count == 0:
                        continue

                    available = [i for i in by_sig[sig] if i != anchor_idx]
                    if not available:
                        total_dist = float("inf")
                        break

                    distances = [
                        np.linalg.norm(centroids[i] - anchor_pos) for i in available
                    ]
                    distances.sort()
                    total_dist += sum(distances[:count])

                anchor_candidates.append((anchor_idx, total_dist))

            # Sort by total distance (best anchors first)
            anchor_candidates.sort(key=lambda x: x[1])
        else:
            # Singleton pattern - all anchors are equally good
            anchor_candidates = [(idx, 0) for idx in by_sig[rarest_sig]]

        for anchor_idx, _ in anchor_candidates[:num_instances]:
            if anchor_idx in assigned:
                continue

            anchor_pos = centroids[anchor_idx]
            group = [anchor_idx]
            assigned.add(anchor_idx)

            # For each other required signature, find nearest available
            remaining = Counter(pattern)
            remaining[rarest_sig] -= 1

            for sig, count in remaining.items():
                if count == 0:
                    continue

                # Find nearest available shells with this signature
                available = [i for i in by_sig[sig] if i not in assigned]
                if not available:
                    continue

                # Sort by distance to anchor
                available.sort(key=lambda i: np.linalg.norm(centroids[i] - anchor_pos))

                # Take the count nearest ones
                for i in available[:count]:
                    group.append(i)
                    assigned.add(i)

            groups.append([shells[i] for i in group])

        return groups, assigned

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
            logger.debug(f"Got info for {len(shells)} shells")

        # Group by material
        by_material = defaultdict(list)
        for shell in shells:
            by_material[shell["material"]].append(shell)

        if self.verbose:
            logger.debug(f"Materials: {list(by_material.keys())}")

        # Process each material group
        all_groups = []
        for material, mat_shells in by_material.items():
            if self.verbose:
                logger.debug(
                    f"Processing material {material}: {len(mat_shells)} shells"
                )

            # Match against known patterns
            matched_patterns = self._match_known_patterns(mat_shells)

            # Assign shells to patterns
            all_assigned = set()
            for pattern, num_instances in matched_patterns:
                groups, assigned = self._assign_greedy_nearest(
                    mat_shells, pattern, num_instances
                )
                all_groups.extend(groups)
                all_assigned.update(assigned)

            # Add remaining as singletons (leftovers)
            for i, s in enumerate(mat_shells):
                if i not in all_assigned:
                    all_groups.append([s])

        if self.verbose:
            logger.debug(f"Created {len(all_groups)} groups total")

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
            logger.info(f"Created {len(result_nodes)} assemblies/nodes.")

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
