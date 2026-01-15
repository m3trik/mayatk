"""
Assembly Reconstructor V7 - Global pattern discovery with greedy nearest-neighbor assignment.

Key insight: We can't rely on proximity for pattern discovery because assemblies overlap.
Instead:
1. Discover patterns by analyzing signature co-occurrences GLOBALLY
2. Assign using greedy nearest-neighbor within each pattern
"""

from __future__ import annotations

from typing import List, Tuple, Optional, Dict, Set, FrozenSet
from collections import defaultdict, Counter
import logging
import numpy as np
from scipy.spatial import KDTree
from itertools import combinations

logger = logging.getLogger(__name__)

try:
    import pymel.core as pm
except ImportError:
    pass


class AssemblyReconstructor:
    """Global pattern discovery with greedy nearest-neighbor assignment."""

    def __init__(
        self,
        matcher=None,
        combine_assemblies: bool = False,
        search_radius_mult: float = 4.0,
        assembly_radius: float = 50.0,
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

    def _infer_patterns_from_counts(self, shells: List[Dict]) -> List[Tuple[int, ...]]:
        """Infer assembly patterns from global signature counts.

        The idea: if we have N shells of type A and N shells of type B,
        they probably form N assemblies of pattern (A, B).

        This works when each signature appears exactly once per assembly.
        """
        # Count occurrences of each vertex signature
        sig_counts = Counter(s["num_verts"] for s in shells)

        if self.verbose:
            logger.debug(f"Signature counts: {sig_counts}")

        # Find the GCD of all counts - this is likely the number of instances
        counts = list(sig_counts.values())
        from math import gcd
        from functools import reduce

        num_instances = reduce(gcd, counts)

        if self.verbose:
            logger.debug(f"Inferred {num_instances} instances")

        if num_instances == 0:
            return []

        # Build the pattern - each signature appears count/num_instances times per assembly
        pattern = []
        for sig, count in sig_counts.items():
            times_per_assembly = count // num_instances
            pattern.extend([sig] * times_per_assembly)

        pattern = tuple(sorted(pattern))

        if self.verbose:
            logger.debug(f"Inferred pattern: {pattern}")

        return [pattern] if len(pattern) > 1 else []

    def _assign_greedy_nearest(
        self, shells: List[Dict], pattern: Tuple[int, ...]
    ) -> List[List[Dict]]:
        """Assign shells to pattern instances using greedy nearest-neighbor.

        For each required signature in the pattern, find the nearest available
        shell with that signature.
        """
        if not pattern:
            return [[s] for s in shells]

        centroids = np.array([s["centroid"] for s in shells])

        # Group shells by signature for efficient lookup
        by_sig = defaultdict(list)
        for i, s in enumerate(shells):
            by_sig[s["num_verts"]].append(i)

        # Check if pattern can be satisfied
        pattern_counter = Counter(pattern)
        can_satisfy = all(
            len(by_sig.get(v, [])) >= c for v, c in pattern_counter.items()
        )

        if not can_satisfy:
            logger.warning(f"Cannot satisfy pattern {pattern}")
            return [[s] for s in shells]

        # Calculate how many instances we can make
        num_instances = min(
            len(by_sig.get(v, [])) // c for v, c in pattern_counter.items()
        )

        if self.verbose:
            logger.debug(f"Can create {num_instances} instances of pattern {pattern}")

        assigned = set()
        groups = []

        # Find the rarest signature to use as anchor
        rarest_sig = min(pattern_counter.keys(), key=lambda v: len(by_sig[v]))

        for _ in range(num_instances):
            # Find an unassigned anchor shell
            anchor_idx = None
            for idx in by_sig[rarest_sig]:
                if idx not in assigned:
                    anchor_idx = idx
                    break

            if anchor_idx is None:
                break

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

        # Add remaining unassigned as singletons
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

            # Infer pattern from signature counts
            patterns = self._infer_patterns_from_counts(mat_shells)

            if patterns:
                # Use the first (only) pattern
                groups = self._assign_greedy_nearest(mat_shells, patterns[0])
            else:
                # No pattern found - all singletons
                groups = [[s] for s in mat_shells]

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
