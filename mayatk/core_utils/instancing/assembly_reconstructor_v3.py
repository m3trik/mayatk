"""
Simplified Assembly Reconstructor V3 - Pure spatial clustering.

This version uses agglomerative clustering to group shells that are
close together into assemblies. The key insight is that shells
belonging to the same assembly are spatially proximate.
"""

from __future__ import annotations

from typing import List, Tuple, Optional, Dict, Set
from collections import defaultdict
import logging
import numpy as np
from scipy.spatial.distance import pdist
from scipy.cluster.hierarchy import linkage, fcluster

logger = logging.getLogger(__name__)

try:
    import pymel.core as pm
except ImportError:
    pass


class AssemblyReconstructor:
    """Handles the separation and intelligent reassembly of combined meshes.

    This version uses pure spatial clustering - shells that are close
    together form an assembly.
    """

    def __init__(
        self,
        matcher=None,
        combine_assemblies: bool = False,
        search_radius_mult: float = 4.0,
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

    def _find_optimal_distance_threshold(self, shells: List[Dict]) -> float:
        """Find the optimal distance threshold for clustering.

        We look for a natural gap in the distance distribution.
        Shells within the same assembly should be much closer than
        shells from different assemblies.
        """
        if len(shells) < 2:
            return 10.0

        centroids = np.array([s["centroid"] for s in shells])

        # Compute all pairwise distances
        distances = pdist(centroids)

        if len(distances) == 0:
            return 10.0

        # Sort distances
        sorted_dists = np.sort(distances)

        # Look for the biggest gap in the first portion of distances
        # This separates "within-assembly" from "between-assembly" distances
        n = len(sorted_dists)

        # Consider only the first third of distances (assuming assemblies are local)
        end_idx = max(5, n // 3)
        gaps = np.diff(sorted_dists[:end_idx])

        if len(gaps) == 0:
            return sorted_dists[-1] * 0.5

        # Find largest gap
        gap_idx = np.argmax(gaps)
        threshold = sorted_dists[gap_idx] + gaps[gap_idx] * 0.5

        if self.verbose:
            logger.debug(f"Computed distance threshold: {threshold:.2f}")

        return threshold

    def reassemble_assemblies(
        self, nodes: List[pm.nodetypes.Transform]
    ) -> List[pm.nodetypes.Transform]:
        """Reassemble separated shells into logical assemblies using spatial clustering.

        Algorithm:
        1. Compute centroid for each shell
        2. Use agglomerative clustering with a distance threshold
        3. The threshold is determined by finding natural gaps in the distance distribution
        4. Create assembly groups from clusters
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

        if len(shells) == 1:
            return [shells[0]["node"]]

        # Compute centroids
        centroids = np.array([s["centroid"] for s in shells])

        # Perform hierarchical clustering
        linkage_matrix = linkage(centroids, method="average")

        # Find optimal threshold
        threshold = self._find_optimal_distance_threshold(shells)

        # Apply threshold to get cluster labels
        labels = fcluster(linkage_matrix, threshold, criterion="distance")

        if self.verbose:
            unique_clusters = set(labels)
            logger.debug(
                f"Clustering produced {len(unique_clusters)} clusters with threshold {threshold:.2f}"
            )

        # Group shells by cluster label
        clusters = defaultdict(list)
        for i, label in enumerate(labels):
            clusters[label].append(shells[i])

        # Create assembly groups
        result_nodes = []

        for label, cluster_shells in clusters.items():
            if len(cluster_shells) == 1:
                # Single shell - no grouping needed
                result_nodes.append(cluster_shells[0]["node"])
            else:
                # Multi-shell assembly - create group
                grp = pm.group(empty=True, name=f"Assembly_{label}")
                grp.addAttr("isAssembly", at="bool", dv=True)

                # Compute centroid of cluster
                cluster_centroid = np.mean(
                    [s["centroid"] for s in cluster_shells], axis=0
                )
                grp.setTranslation(cluster_centroid, space="world")

                # Parent all shells to group
                for s in cluster_shells:
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
