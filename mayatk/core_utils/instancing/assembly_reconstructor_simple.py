"""
Simple Assembly Reconstructor - Built on solid fundamentals.

Core principles:
1. Material boundaries are absolute
2. Parts that touch/overlap belong together
3. No hard-coded thresholds or pattern knowledge

Algorithm:
1. Group shells by material (hard boundary)
2. Within each material, find connected components using bbox overlap
3. Each connected component is an assembly
"""

from __future__ import annotations

from typing import List, Tuple, Optional, Dict, Set
from collections import defaultdict
import logging
import numpy as np

logger = logging.getLogger(__name__)

try:
    import pymel.core as pm
except ImportError:
    pass


class AssemblyReconstructor:
    """Simple, robust assembly reconstruction using bbox connectivity."""

    def __init__(
        self,
        matcher=None,
        combine_assemblies: bool = False,
        overlap_tolerance: float = 0.5,  # Max gap between touching bboxes
        verbose: bool = False,
        **kwargs,  # Accept and ignore other params for compatibility
    ):
        self.matcher = matcher
        self.combine_assemblies = combine_assemblies
        self.overlap_tolerance = overlap_tolerance
        self.verbose = verbose
        self.combine_targets: List[Tuple[Optional[pm.nodetypes.Transform], str]] = []

    def separate_combined_meshes(
        self, nodes: List[pm.nodetypes.Transform]
    ) -> List[pm.nodetypes.Transform]:
        """Separate any combined meshes into individual shells."""
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
                logger.info(f"Mesh {node} has {num_shells} shells")

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
        """Get shell info including bbox and material."""
        shape = node.getShape()
        if not shape or not isinstance(shape, pm.nodetypes.Mesh):
            return None

        try:
            bbox = node.getBoundingBox(space="world")
            min_pt = np.array([bbox.min().x, bbox.min().y, bbox.min().z])
            max_pt = np.array([bbox.max().x, bbox.max().y, bbox.max().z])

            # Get material
            sgs = shape.listConnections(type="shadingEngine") or []
            material = sgs[0].name() if sgs else "unknown"

            return {
                "node": node,
                "bbox_min": min_pt,
                "bbox_max": max_pt,
                "num_verts": shape.numVertices(),
                "material": material,
            }
        except Exception as e:
            logger.warning(f"Failed to get shell info for {node}: {e}")
            return None

    def _bboxes_touch(self, info1: Dict, info2: Dict) -> bool:
        """Check if two bboxes touch or overlap within tolerance."""
        min1, max1 = info1["bbox_min"], info1["bbox_max"]
        min2, max2 = info2["bbox_min"], info2["bbox_max"]

        # Check if separated in any axis by more than tolerance
        for i in range(3):
            if max1[i] + self.overlap_tolerance < min2[i]:
                return False
            if max2[i] + self.overlap_tolerance < min1[i]:
                return False
        return True

    def _find_connected_components(self, shells: List[Dict]) -> List[List[Dict]]:
        """Find connected components using bbox overlap within same material."""
        if not shells:
            return []

        n = len(shells)
        visited = [False] * n
        components = []

        # Build adjacency based on bbox touching AND same material
        def get_neighbors(idx: int) -> List[int]:
            neighbors = []
            for j in range(n):
                if j != idx and not visited[j]:
                    if shells[idx]["material"] == shells[j]["material"]:
                        if self._bboxes_touch(shells[idx], shells[j]):
                            neighbors.append(j)
            return neighbors

        # BFS to find connected components
        for i in range(n):
            if visited[i]:
                continue

            component = []
            queue = [i]
            visited[i] = True

            while queue:
                current = queue.pop(0)
                component.append(current)

                for neighbor in get_neighbors(current):
                    if not visited[neighbor]:
                        visited[neighbor] = True
                        queue.append(neighbor)

            components.append([shells[idx] for idx in component])

        return components

    def reassemble_assemblies(
        self, nodes: List[pm.nodetypes.Transform]
    ) -> List[pm.nodetypes.Transform]:
        """Reassemble shells into assemblies using connected components."""
        if self.verbose:
            logger.info(f"reassemble_assemblies: {len(nodes)} nodes")

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

        # Find connected components
        components = self._find_connected_components(shells)

        if self.verbose:
            logger.debug(f"Found {len(components)} connected components")

        # Create Maya groups for multi-shell assemblies
        result_nodes = []
        for i, component in enumerate(components):
            if len(component) == 1:
                result_nodes.append(component[0]["node"])
            else:
                grp = pm.group(empty=True, name=f"Assembly_{i+1}")
                grp.addAttr("isAssembly", at="bool", dv=True)

                # Position at component centroid
                centers = [(s["bbox_min"] + s["bbox_max"]) / 2 for s in component]
                centroid = np.mean(centers, axis=0)
                grp.setTranslation(centroid, space="world")

                for s in component:
                    pm.parent(s["node"], grp)

                result_nodes.append(grp)

        if self.verbose:
            logger.info(f"Created {len(result_nodes)} assemblies/nodes")

        return result_nodes

    def combine_reassembled_assemblies(
        self, nodes: List[pm.nodetypes.Transform]
    ) -> List[pm.nodetypes.Transform]:
        """Combine assembly children into single meshes."""
        if not nodes:
            return []

        combined = []
        for node in nodes:
            is_assembly = (hasattr(node, "hasAttr") and node.hasAttr("isAssembly")) or (
                hasattr(node, "name") and node.name().startswith("Assembly_")
            )

            if not is_assembly:
                combined.append(node)
                continue

            children = node.getChildren(type="transform")
            mesh_children = [c for c in children if self._is_mesh_transform(c)]

            if not mesh_children:
                continue

            if len(mesh_children) == 1:
                pm.parent(mesh_children[0], world=True)
                combined.append(mesh_children[0])
            else:
                try:
                    result = pm.polyUnite(
                        mesh_children,
                        name=f"{node.name()}_combined",
                        ch=False,
                        mergeUVSets=True,
                    )[0]
                    result = pm.PyNode(result)
                    self._center_transform(result)
                    combined.append(result)
                except Exception as e:
                    logger.warning(f"PolyUnite failed: {e}")
                    for c in mesh_children:
                        pm.parent(c, world=True)
                    combined.extend(mesh_children)

            try:
                pm.delete(node)
            except Exception:
                pass

        return combined

    @staticmethod
    def _is_mesh_transform(n) -> bool:
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
