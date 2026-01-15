"""
Simplified Assembly Reconstructor for matching expected sorting.

This version uses a simpler spatial clustering approach that groups
shells based on proximity and consistent patterns.
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

import pythontk as ptk

from mayatk.core_utils.instancing.geometry_matcher import GeometryMatcher, ShellInfo


class AssemblyReconstructor:
    """Handles the separation and intelligent reassembly of combined meshes.

    This simplified version focuses on:
    1. Grouping shells that are spatially close
    2. Respecting material boundaries (require_same_material)
    3. Creating assemblies only when there's a consistent pattern
    """

    def __init__(
        self,
        matcher: GeometryMatcher,
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
                "centroid": centroid,
                "diagonal": diagonal,
                "num_verts": shape.numVertices(),
                "materials": materials,
            }
        except Exception as e:
            logger.warning(f"Failed to get shell info for {node}: {e}")
            return None

    def reassemble_assemblies(
        self, nodes: List[pm.nodetypes.Transform]
    ) -> List[pm.nodetypes.Transform]:
        """Reassemble separated shells into logical assemblies.

        Algorithm:
        1. Compute shell info (centroid, size, verts, material) for all nodes
        2. Sort by size (largest first) - these are potential "bodies"
        3. For each body, find nearby smaller shells that could be parts
        4. Group parts with consistent patterns across multiple bodies
        5. Create assembly groups
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

        # Sort by size (diagonal) descending
        shells.sort(key=lambda s: s["diagonal"], reverse=True)

        if self.verbose:
            logger.debug(f"Sorted {len(shells)} shells by size.")

        # Find natural size clusters to identify bodies vs parts
        diagonals = [s["diagonal"] for s in shells]
        median_diag = np.median(diagonals)

        # Identify bodies: objects significantly larger than median
        # Bodies are typically the main structural elements
        body_threshold = median_diag * 0.5  # Bodies are at least 50% of median

        bodies = []
        parts = []
        for s in shells:
            if s["diagonal"] >= body_threshold:
                bodies.append(s)
            else:
                parts.append(s)

        if self.verbose:
            logger.debug(f"Identified {len(bodies)} bodies and {len(parts)} parts.")

        # For each body, find nearby parts
        # Use KDTree for efficient spatial lookup
        if not parts:
            # No parts to assign, just return bodies as-is
            return [s["node"] for s in shells]

        part_centroids = np.array([p["centroid"] for p in parts])
        tree = KDTree(part_centroids)

        # Track which parts are assigned to which body
        body_to_parts: Dict[int, List[int]] = defaultdict(list)

        for i, body in enumerate(bodies):
            body_centroid = body["centroid"]
            # Search radius based on body size
            search_radius = body["diagonal"] * self.search_radius_mult

            # Find all parts within radius
            nearby_indices = tree.query_ball_point(body_centroid, search_radius)

            for pi in nearby_indices:
                part = parts[pi]
                # Check material compatibility if required
                if self.matcher.require_same_material:
                    if part["materials"] != body["materials"]:
                        continue

                body_to_parts[i].append(pi)

        # Now we need to validate: only keep parts that appear consistently
        # across multiple bodies (if there are multiple bodies of same type)

        # Group bodies by signature (diagonal + verts rounded)
        def body_sig(b):
            return (round(b["diagonal"], 1), b["num_verts"])

        body_groups = defaultdict(list)
        for i, body in enumerate(bodies):
            sig = body_sig(body)
            body_groups[sig].append(i)

        # For each body group, validate part patterns
        final_assignments: Dict[int, List[int]] = defaultdict(list)
        assigned_parts: Set[int] = set()

        for sig, body_indices in body_groups.items():
            if len(body_indices) < 2:
                # Single body of this type - keep all nearby parts
                for bi in body_indices:
                    for pi in body_to_parts[bi]:
                        if pi not in assigned_parts:
                            final_assignments[bi].append(pi)
                            assigned_parts.add(pi)
            else:
                # Multiple bodies - use consensus filtering
                # A part type should appear for most bodies of this type
                part_sig_counts = defaultdict(int)
                part_sig_to_indices = defaultdict(list)

                for bi in body_indices:
                    seen_sigs = set()
                    for pi in body_to_parts[bi]:
                        part = parts[pi]
                        part_s = (part["num_verts"], part["materials"])
                        if part_s not in seen_sigs:
                            part_sig_counts[part_s] += 1
                            seen_sigs.add(part_s)
                        part_sig_to_indices[(bi, part_s)].append(pi)

                # Accept parts that appear in at least 50% of bodies
                min_count = max(1, len(body_indices) // 2)
                valid_sigs = {s for s, c in part_sig_counts.items() if c >= min_count}

                for bi in body_indices:
                    for pi in body_to_parts[bi]:
                        part = parts[pi]
                        part_s = (part["num_verts"], part["materials"])
                        if part_s in valid_sigs and pi not in assigned_parts:
                            final_assignments[bi].append(pi)
                            assigned_parts.add(pi)

        # Create assembly groups
        result_nodes = []

        for i, body in enumerate(bodies):
            part_indices = final_assignments.get(i, [])

            if not part_indices:
                # Body with no parts - return as single node
                result_nodes.append(body["node"])
            else:
                # Create assembly group
                grp = pm.group(empty=True, name=f"Assembly_{i+1}")
                grp.addAttr("isAssembly", at="bool", dv=True)

                # Position group at body centroid
                body_node = body["node"]
                grp.setTranslation(
                    body_node.getTranslation(space="world"), space="world"
                )

                # Parent body to group
                pm.parent(body_node, grp)

                # Parent parts to group
                for pi in part_indices:
                    pm.parent(parts[pi]["node"], grp)

                result_nodes.append(grp)

        # Add unassigned parts to result
        for pi, part in enumerate(parts):
            if pi not in assigned_parts:
                result_nodes.append(part["node"])

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
