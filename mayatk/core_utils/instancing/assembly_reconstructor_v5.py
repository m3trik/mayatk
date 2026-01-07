"""
Anchor-based Assembly Reconstructor V5.

Key insight: Different assembly types are spatially interleaved (as close as 1 unit).
BUT: Most shell types are UNIQUE to one assembly type based on (verts, material).

Strategy:
1. Compute signature (verts, material) for each shell
2. Find all possible assembly "patterns" - groups of shells that form valid assemblies
3. For each unique/large shell (anchor), find the nearest shell of each companion type
4. Greedily assign shells to assemblies starting from most unique anchors
"""

from __future__ import annotations

from typing import List, Tuple, Optional, Dict, Set, FrozenSet
from collections import defaultdict
import logging
import numpy as np
from scipy.spatial import KDTree

logger = logging.getLogger(__name__)

try:
    import pymel.core as pm
except ImportError:
    pass


class AssemblyReconstructor:
    """Anchor-based assembly reconstruction.

    Uses unique shells as "anchors" to identify assembly instances,
    then finds companion shells nearby.
    """

    def __init__(
        self,
        matcher=None,
        combine_assemblies: bool = False,
        search_radius_mult: float = 4.0,  # Not used
        assembly_radius: float = 100.0,  # Max distance between parts
        verbose: bool = False,
    ):
        self.matcher = matcher
        self.combine_assemblies = combine_assemblies
        self.assembly_radius = assembly_radius
        self.verbose = verbose
        self.combine_targets: List[Tuple[Optional[pm.nodetypes.Transform], str]] = []

    def separate_combined_meshes(
        self, nodes: List[pm.nodetypes.Transform]
    ) -> List[pm.nodetypes.Transform]:
        """Separate any combined meshes in the list into their shells."""
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
            except RuntimeError:
                num_shells = 0

            try:
                if isinstance(num_shells, (list, tuple)) and num_shells:
                    num_shells = num_shells[0]
                if isinstance(num_shells, str):
                    num_shells = float(num_shells)
                num_shells = int(num_shells)
            except Exception:
                num_shells = 0

            if self.verbose:
                logger.info(f"Mesh {node} has {num_shells} shells.")

            if num_shells > 1:
                if self.verbose:
                    logger.debug(f"Separating: {node} ({num_shells} shells)")

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
        """Center the transform on its geometry."""
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
        """Get spatial and topological info for a shell."""
        shape = node.getShape()
        if not shape or not isinstance(shape, pm.nodetypes.Mesh):
            return None

        try:
            pts = shape.getPoints(space="world")
            centroid = np.mean(pts, axis=0)

            bbox = node.getBoundingBox(space="world")
            min_pt = bbox.min() if callable(bbox.min) else bbox.min
            max_pt = bbox.max() if callable(bbox.max) else bbox.max
            size = max_pt - min_pt
            diagonal = size.length()

            # Get material
            sgs = shape.listConnections(type="shadingEngine") or []
            materials = tuple(sorted([sg.name() for sg in sgs]))
            material = materials[0] if materials else "unknown"

            return {
                "node": node,
                "centroid": np.array([centroid[0], centroid[1], centroid[2]]),
                "diagonal": diagonal,
                "num_verts": shape.numVertices(),
                "material": material,
                "signature": (shape.numVertices(), material),
            }
        except Exception as e:
            logger.warning(f"Failed to get shell info for {node}: {e}")
            return None

    def _discover_assembly_patterns(
        self, shells: List[Dict]
    ) -> List[FrozenSet[Tuple[int, str]]]:
        """Discover recurring assembly patterns by analyzing nearby shells.

        For each shell, look at what OTHER shells are nearby.
        Group shells that tend to appear together.
        """
        if self.verbose:
            logger.debug(f"Discovering assembly patterns from {len(shells)} shells")

        centroids = np.array([s["centroid"] for s in shells])
        tree = KDTree(centroids)

        # For each shell, find what signatures are nearby
        shell_neighborhoods = []
        for i, shell in enumerate(shells):
            nearby_indices = tree.query_ball_point(
                shell["centroid"], self.assembly_radius
            )
            nearby_sigs = frozenset(shells[j]["signature"] for j in nearby_indices)
            shell_neighborhoods.append(nearby_sigs)

        # Count pattern occurrences
        pattern_counts = defaultdict(int)
        for neighborhood in shell_neighborhoods:
            pattern_counts[neighborhood] += 1

        # Filter to patterns that appear multiple times
        valid_patterns = [p for p, count in pattern_counts.items() if count >= 2]

        if self.verbose:
            logger.debug(f"Found {len(valid_patterns)} recurring patterns")

        return valid_patterns

    def _find_unique_signatures(self, shells: List[Dict]) -> Dict[Tuple[int, str], int]:
        """Count how many shells have each signature.

        Unique signatures are good anchors for identifying assemblies.
        """
        sig_counts = defaultdict(int)
        for shell in shells:
            sig_counts[shell["signature"]] += 1
        return dict(sig_counts)

    def reassemble_assemblies(
        self, nodes: List[pm.nodetypes.Transform]
    ) -> List[pm.nodetypes.Transform]:
        """Reassemble separated shells into logical assemblies.

        Algorithm:
        1. Get signature (verts, material) for each shell
        2. Identify shells that are "unique enough" to be anchors
        3. For each anchor, find nearby companions using pattern matching
        4. Greedily assign shells, preferring larger/unique anchors first
        """
        if self.verbose:
            logger.info(f"reassemble_assemblies called with {len(nodes)} nodes.")

        if not nodes:
            return []

        # Get shell info
        shells = []
        for n in nodes:
            info = self._get_shell_info(n)
            if info:
                shells.append(info)

        if not shells:
            return list(nodes)

        if self.verbose:
            logger.debug(f"Got info for {len(shells)} shells")

        # Build spatial index
        centroids = np.array([s["centroid"] for s in shells])
        tree = KDTree(centroids)

        # Count signature occurrences
        sig_counts = self._find_unique_signatures(shells)

        # Discover assembly patterns
        patterns = self._discover_assembly_patterns(shells)

        # Sort patterns by size (larger assemblies first) then by uniqueness
        def pattern_score(pattern):
            # Prefer patterns with more unique signatures
            uniqueness = sum(1.0 / sig_counts.get(sig, 1) for sig in pattern)
            return (len(pattern), uniqueness)

        patterns.sort(key=pattern_score, reverse=True)

        if self.verbose:
            logger.debug(f"Sorted patterns: {[len(p) for p in patterns[:10]]}")

        # Track assigned shells
        assigned = set()
        result_groups = []

        # For each pattern, find all instances
        for pattern in patterns:
            if len(pattern) <= 1:
                continue  # Skip single-shell patterns for now

            # Find all shells that could be part of this pattern
            pattern_shells = [s for s in shells if s["signature"] in pattern]

            # Group by proximity to form assembly instances
            used_in_this_pattern = set()

            for shell in pattern_shells:
                shell_idx = shells.index(shell)
                if shell_idx in assigned:
                    continue

                # Find all nearby shells that match the pattern
                nearby_indices = tree.query_ball_point(
                    shell["centroid"], self.assembly_radius
                )

                group = []
                for ni in nearby_indices:
                    if ni in assigned:
                        continue
                    if shells[ni]["signature"] in pattern:
                        group.append(ni)

                # Check if this group matches the pattern exactly
                group_sigs = frozenset(shells[gi]["signature"] for gi in group)

                if group_sigs == pattern:
                    # Perfect match - create assembly
                    for gi in group:
                        assigned.add(gi)
                    result_groups.append([shells[gi] for gi in group])

        # Handle remaining unassigned shells
        remaining = [shells[i] for i in range(len(shells)) if i not in assigned]

        # Group remaining by proximity
        remaining_groups = self._group_remaining_by_proximity(remaining)
        result_groups.extend(remaining_groups)

        if self.verbose:
            logger.debug(
                f"Created {len(result_groups)} groups, {len(remaining)} remaining"
            )

        # Create Maya groups
        result_nodes = []
        for i, group in enumerate(result_groups):
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

    def _group_remaining_by_proximity(self, shells: List[Dict]) -> List[List[Dict]]:
        """Group remaining shells by spatial proximity."""
        if not shells:
            return []

        centroids = np.array([s["centroid"] for s in shells])
        tree = KDTree(centroids)

        assigned = set()
        groups = []

        for i, shell in enumerate(shells):
            if i in assigned:
                continue

            # BFS to find connected component
            group = [i]
            assigned.add(i)
            frontier = [i]

            while frontier:
                current = frontier.pop(0)
                neighbors = tree.query_ball_point(
                    centroids[current], self.assembly_radius
                )
                for ni in neighbors:
                    if ni not in assigned:
                        assigned.add(ni)
                        group.append(ni)
                        frontier.append(ni)

            groups.append([shells[gi] for gi in group])

        return groups

    def combine_reassembled_assemblies(
        self, nodes: List[pm.nodetypes.Transform]
    ) -> List[pm.nodetypes.Transform]:
        """Combine assemblies into single meshes."""
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
