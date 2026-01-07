"""
Assembly Reconstructor V6 - Material-first, Pattern-based.

Derived from reverse-engineering the expected output:
1. Material is a HARD BOUNDARY - parts never cross materials
2. Within each material, find recurring PATTERNS (sets of vertex counts)
3. Assign shells to patterns based on proximity
4. Single shells that don't fit patterns remain as singletons
"""

from __future__ import annotations

from typing import List, Tuple, Optional, Dict, Set, FrozenSet
from collections import defaultdict, Counter
import logging
import numpy as np
from scipy.spatial import KDTree

logger = logging.getLogger(__name__)

try:
    import pymel.core as pm
except ImportError:
    pass


class AssemblyReconstructor:
    """Material-first, pattern-based assembly reconstruction."""

    def __init__(
        self,
        matcher=None,
        combine_assemblies: bool = False,
        search_radius_mult: float = 4.0,  # Not used
        assembly_radius: float = 50.0,  # Max distance between parts of same assembly
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
        """Center transform on geometry."""
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
        """Get shell metadata."""
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

    def _discover_patterns(self, shells: List[Dict]) -> List[Tuple[int, ...]]:
        """Discover recurring assembly patterns within a material group.

        A pattern is a sorted tuple of vertex counts that appears together
        multiple times in the scene.
        """
        if len(shells) < 2:
            return []

        # Build spatial index
        centroids = np.array([s["centroid"] for s in shells])
        tree = KDTree(centroids)

        # For each shell, find what patterns exist nearby
        neighborhoods = []
        for i, shell in enumerate(shells):
            nearby = tree.query_ball_point(shell["centroid"], self.assembly_radius)
            nearby_verts = tuple(sorted(shells[j]["num_verts"] for j in nearby))
            neighborhoods.append(nearby_verts)

        # Count pattern occurrences
        pattern_counts = Counter(neighborhoods)

        # Keep patterns that appear at least twice and have multiple parts
        valid_patterns = [
            p for p, count in pattern_counts.items() if count >= 2 and len(p) > 1
        ]

        # Sort by size (larger patterns first) then by frequency
        valid_patterns.sort(key=lambda p: (len(p), pattern_counts[p]), reverse=True)

        if self.verbose:
            logger.debug(f"Discovered {len(valid_patterns)} patterns")
            for p in valid_patterns[:5]:
                logger.debug(f"  {p}: {pattern_counts[p]}x")

        return valid_patterns

    def _assign_to_patterns(
        self, shells: List[Dict], patterns: List[Tuple[int, ...]]
    ) -> List[List[Dict]]:
        """Assign shells to patterns greedily.

        For each pattern, find all instances where the required shells
        exist nearby and haven't been assigned yet.
        """
        if not patterns:
            return [[s] for s in shells]  # All singletons

        centroids = np.array([s["centroid"] for s in shells])
        tree = KDTree(centroids)

        assigned = set()
        groups = []

        for pattern in patterns:
            # Find all shells that could anchor this pattern
            # (shells with vertex count matching any element)
            pattern_set = set(pattern)
            pattern_counter = Counter(pattern)

            for i, shell in enumerate(shells):
                if i in assigned:
                    continue
                if shell["num_verts"] not in pattern_set:
                    continue

                # Try to build this pattern from this anchor
                nearby = tree.query_ball_point(shell["centroid"], self.assembly_radius)
                available = [j for j in nearby if j not in assigned]

                # Check if we can satisfy the pattern
                available_verts = Counter(shells[j]["num_verts"] for j in available)

                can_satisfy = all(
                    available_verts.get(v, 0) >= c for v, c in pattern_counter.items()
                )

                if can_satisfy:
                    # Greedily assign shells to this pattern
                    group_indices = []
                    remaining_pattern = Counter(pattern)

                    # Sort available by distance from anchor
                    available_sorted = sorted(
                        available,
                        key=lambda j: np.linalg.norm(centroids[j] - centroids[i]),
                    )

                    for j in available_sorted:
                        v = shells[j]["num_verts"]
                        if remaining_pattern.get(v, 0) > 0:
                            group_indices.append(j)
                            remaining_pattern[v] -= 1
                            if sum(remaining_pattern.values()) == 0:
                                break

                    if sum(remaining_pattern.values()) == 0:
                        # Successfully assigned all parts of pattern
                        for j in group_indices:
                            assigned.add(j)
                        groups.append([shells[j] for j in group_indices])

        # Add remaining shells as singletons
        for i, shell in enumerate(shells):
            if i not in assigned:
                groups.append([shell])

        return groups

    def reassemble_assemblies(
        self, nodes: List[pm.nodetypes.Transform]
    ) -> List[pm.nodetypes.Transform]:
        """Reassemble shells into assemblies.

        Algorithm:
        1. Get shell info
        2. Group by material (hard boundary)
        3. Within each material, discover patterns
        4. Assign shells to patterns
        5. Create Maya groups
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

            # Discover patterns
            patterns = self._discover_patterns(mat_shells)

            # Assign to patterns
            groups = self._assign_to_patterns(mat_shells, patterns)
            all_groups.extend(groups)

        if self.verbose:
            logger.debug(f"Created {len(all_groups)} groups")

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
