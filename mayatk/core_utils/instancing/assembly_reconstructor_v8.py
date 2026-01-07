"""
Assembly Reconstructor V8 - Pattern detection using unique signature analysis.

Key insights from reverse engineering:
1. Material is a hard boundary
2. Within a material, singletons are shells that appear 1:1 with assemblies
3. Multi-part assemblies have shells that co-occur in fixed patterns

Strategy:
1. Group by material
2. Within each material:
   a. Find signatures that appear only once globally (or match 1:1 with expected assemblies)
   b. These are singletons
   c. Find patterns by looking at shells that are always close together
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


class AssemblyReconstructor:
    """Pattern detection using unique signature analysis."""

    def __init__(
        self,
        matcher=None,
        combine_assemblies: bool = False,
        search_radius_mult: float = 4.0,
        assembly_radius: float = 100.0,  # Max distance within an assembly
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

    def _cluster_shells(self, shells: List[Dict]) -> List[List[Dict]]:
        """Cluster shells using hierarchical clustering with the assembly_radius as cutoff."""
        if len(shells) <= 1:
            return [[s] for s in shells]

        centroids = np.array([s["centroid"] for s in shells])

        # Use hierarchical clustering with 'single' linkage (nearest neighbor)
        distances = pdist(centroids)
        Z = linkage(distances, method="single")

        # Cut the tree at the assembly_radius
        labels = fcluster(Z, t=self.assembly_radius, criterion="distance")

        # Group shells by cluster
        clusters = defaultdict(list)
        for i, label in enumerate(labels):
            clusters[label].append(shells[i])

        return list(clusters.values())

    def _find_best_pattern_for_cluster(self, cluster: List[Dict]) -> Tuple[int, ...]:
        """Determine the signature pattern for a cluster."""
        sigs = sorted([s["num_verts"] for s in cluster])
        return tuple(sigs)

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

        # Process each material group using hierarchical clustering
        all_groups = []
        for material, mat_shells in by_material.items():
            if self.verbose:
                logger.debug(
                    f"Processing material {material}: {len(mat_shells)} shells"
                )

            # Cluster within material
            clusters = self._cluster_shells(mat_shells)

            if self.verbose:
                for i, c in enumerate(clusters):
                    sig = self._find_best_pattern_for_cluster(c)
                    logger.debug(f"  Cluster {i}: {len(c)} shells, signature {sig}")

            all_groups.extend(clusters)

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
