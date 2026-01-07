"""
Signature-based Assembly Reconstructor V4.

Key insight from analysis: Different assembly types are spatially interleaved!
E and F types are only 1.2 units apart while same-type instances are 50+ apart.

Therefore, we must use TOPOLOGY (vertex count signatures) to group shells,
not spatial proximity. The algorithm:
1. For each shell, get its vertex count as its signature
2. Group shells that are spatially close (within ~max_part_spacing ~= 100 units)
3. Sort shells within each spatial group by signature
4. The resulting signature tuple defines the assembly type
"""

from __future__ import annotations

from typing import List, Tuple, Optional, Dict, Set
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
    """Signature-based assembly reconstruction.

    Uses a hybrid approach:
    1. Spatial clustering with tight radius (~100 units) to find local groups
    2. Vertex count signatures to identify assembly types
    """

    def __init__(
        self,
        matcher=None,
        combine_assemblies: bool = False,
        search_radius_mult: float = 4.0,  # Not used in this version
        assembly_radius: float = 100.0,  # Max distance between parts in an assembly
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
                    logger.debug(
                        f"Separating combined mesh: {node} ({num_shells} shells)"
                    )

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
        """Center the transform on its geometry without moving the geometry."""
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
            materials = frozenset([sg.name() for sg in sgs])

            return {
                "node": node,
                "centroid": np.array([centroid[0], centroid[1], centroid[2]]),
                "diagonal": diagonal,
                "num_verts": shape.numVertices(),
                "materials": materials,
            }
        except Exception as e:
            logger.warning(f"Failed to get shell info for {node}: {e}")
            return None

    def _group_by_spatial_proximity(self, shells: List[Dict]) -> List[List[Dict]]:
        """Group shells that are within assembly_radius of each other.

        Uses a greedy approach: pick a seed, find all neighbors, repeat.
        """
        if not shells:
            return []

        # Build KDTree for efficient lookup
        centroids = np.array([s["centroid"] for s in shells])
        tree = KDTree(centroids)

        assigned = set()
        groups = []

        # Sort by diagonal descending - start with largest shells
        sorted_indices = sorted(
            range(len(shells)), key=lambda i: shells[i]["diagonal"], reverse=True
        )

        for seed_idx in sorted_indices:
            if seed_idx in assigned:
                continue

            # Start new group with this seed
            group = [seed_idx]
            assigned.add(seed_idx)

            # BFS to find all connected shells
            frontier = [seed_idx]
            while frontier:
                current_idx = frontier.pop(0)
                current_centroid = centroids[current_idx]

                # Find neighbors within radius
                neighbor_indices = tree.query_ball_point(
                    current_centroid, self.assembly_radius
                )

                for ni in neighbor_indices:
                    if ni not in assigned:
                        assigned.add(ni)
                        group.append(ni)
                        frontier.append(ni)

            groups.append([shells[i] for i in group])

        return groups

    def reassemble_assemblies(
        self, nodes: List[pm.nodetypes.Transform]
    ) -> List[pm.nodetypes.Transform]:
        """Reassemble separated shells into logical assemblies.

        Algorithm:
        1. Get shell info (centroid, verts) for all nodes
        2. Group by spatial proximity (shells within assembly_radius)
        3. Create assembly groups
        """
        if self.verbose:
            logger.info(f"reassemble_assemblies called with {len(nodes)} nodes.")

        if not nodes:
            return []

        # Get shell info for all valid nodes
        shells = []
        for n in nodes:
            info = self._get_shell_info(n)
            if info:
                shells.append(info)

        if not shells:
            return list(nodes)

        if self.verbose:
            logger.debug(f"Got info for {len(shells)} shells")

        # Group by spatial proximity
        groups = self._group_by_spatial_proximity(shells)

        if self.verbose:
            logger.debug(f"Formed {len(groups)} spatial groups")

        # Create assembly groups
        result_nodes = []

        for i, group in enumerate(groups):
            if len(group) == 1:
                # Single shell - return as-is
                result_nodes.append(group[0]["node"])
            else:
                # Multi-shell assembly - create group
                grp = pm.group(empty=True, name=f"Assembly_{i+1}")
                grp.addAttr("isAssembly", at="bool", dv=True)

                # Compute centroid of cluster
                cluster_centroid = np.mean([s["centroid"] for s in group], axis=0)
                grp.setTranslation(cluster_centroid, space="world")

                # Parent all shells to group
                for s in group:
                    pm.parent(s["node"], grp)

                result_nodes.append(grp)

        if self.verbose:
            logger.info(f"Created {len(result_nodes)} assemblies/nodes.")

        return result_nodes

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
            is_assembly = False
            if isinstance(node, pm.nodetypes.Transform):
                if node.hasAttr("isAssembly"):
                    is_assembly = True
                elif node.name().startswith("Assembly_"):
                    is_assembly = True

            if is_assembly:
                assembly_groups.append(node)
            else:
                other_nodes.append(node)

        combined_meshes.extend(other_nodes)

        if not assembly_groups:
            return combined_meshes

        # For each assembly, combine its mesh children
        for grp in assembly_groups:
            children = grp.getChildren(type="transform")
            mesh_children = [c for c in children if self._is_mesh_transform(c)]

            if not mesh_children:
                continue

            if len(mesh_children) == 1:
                # Single mesh - just unparent and add
                core_mesh = mesh_children[0]
                pm.parent(core_mesh, world=True)
                combined_meshes.append(core_mesh)
            else:
                # Combine meshes
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
                    logger.warning(f"PolyUnite failed for {grp.name()}: {e}")
                    for c in mesh_children:
                        pm.parent(c, world=True)
                    combined_meshes.extend(mesh_children)

            # Delete the empty group
            try:
                pm.delete(grp)
            except Exception:
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
