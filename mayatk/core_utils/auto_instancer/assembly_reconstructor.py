# !/usr/bin/python
# coding=utf-8
"""Logic for separating and reassembling mesh assemblies."""
from __future__ import annotations

import math
import logging
from typing import List, Tuple, Optional, Dict, Any, Set
from collections import defaultdict
import numpy as np

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError:
    pass

import pythontk as ptk

# From this package:
from mayatk.core_utils.auto_instancer.geometry_matcher import GeometryMatcher

logger = logging.getLogger(__name__)

# Attribute stamped on assembly groups this tool creates, so later passes
# never mistake a user's own ``Assembly_*``-named node for one of ours.
ASSEMBLY_TAG_ATTR = "autoInstancerAssembly"


class AssemblyReconstructor:
    """Handles the separation and intelligent reassembly of combined meshes."""

    def __init__(
        self,
        matcher: GeometryMatcher,
        combine_assemblies: bool = True,
        search_radius_mult: float = 1.5,
        verbose: bool = False,
    ):
        self.matcher = matcher
        self.combine_assemblies = combine_assemblies
        self.search_radius_mult = search_radius_mult
        self.verbose = verbose
        # UUIDs of transforms whose shells were separated out; once empty
        # they are leftover junk and can be deleted (see cleanup_empty_sources).
        self._separated_source_uuids: List[str] = []
        # UUIDs of assembly groups created by this run; ones emptied by later
        # combining are deleted (see cleanup_empty_assembly_groups).
        self._created_assembly_uuids: List[str] = []
        # UUIDs of the combined per-copy assembly meshes this run produced.
        # A combined copy that fails to instance is still a semantic unit —
        # the remainder-combine must not dissolve it into a material blob.
        self._combined_assembly_uuids: List[str] = []

    def separate_combined_meshes(self, nodes: List[object]) -> List[object]:
        """Separate any combined meshes in the list into their shells."""
        new_nodes = []
        self._separated_source_uuids = []

        for node in nodes:
            node_str = str(node)
            if not cmds.objExists(node_str):
                continue

            shapes = (
                cmds.listRelatives(
                    node_str, shapes=True, noIntermediate=True, fullPath=True
                )
                or []
            )
            shape = shapes[0] if shapes else None
            if not shape or cmds.objectType(shape) != "mesh":
                new_nodes.append(node_str)
                continue

            # Never split an already-instanced shape — separation would
            # collapse the existing sharing the user (or a prior run) set up.
            if len(cmds.listRelatives(shape, allParents=True) or []) > 1:
                new_nodes.append(node_str)
                continue

            # Check shell count
            try:
                num_shells = cmds.polyEvaluate(node_str, shell=True)
            except RuntimeError:
                num_shells = 0

            # Normalize return types
            try:
                if isinstance(num_shells, (list, tuple)) and num_shells:
                    num_shells = num_shells[0]
                if isinstance(num_shells, str):
                    num_shells = float(num_shells)
                num_shells = int(num_shells)
            except Exception:
                num_shells = 0

            if num_shells > 1:
                if self.verbose:
                    logger.info(
                        "Separating combined mesh: %s (%s shells)",
                        node_str,
                        num_shells,
                    )
                try:
                    separated = cmds.polySeparate(node_str, ch=False) or []
                    # NOTE: Do NOT canonicalize here - it expands bounding boxes
                    # and breaks BFS grouping. Canonicalization is done after
                    # reassemble_assemblies for instancing purposes.
                    new_nodes.extend(separated)
                    # polySeparate leaves the (now shapeless) source transform
                    # behind as the shells' parent; remember it for cleanup.
                    self._separated_source_uuids.extend(
                        cmds.ls(node_str, uuid=True) or []
                    )
                except RuntimeError as e:
                    logger.warning("Failed to separate %s: %s", node_str, e)
                    new_nodes.append(node_str)
            else:
                new_nodes.append(node_str)

        return new_nodes

    def cleanup_empty_sources(self) -> None:
        """Delete leftover source transforms whose shells were all moved out."""
        self._delete_if_childless(self._separated_source_uuids)
        self._separated_source_uuids = []

    def cleanup_empty_assembly_groups(self) -> None:
        """Delete assembly groups this run created that have since emptied.

        Combining the non-instanced remainder polyUnites a kept group's
        children into world-level meshes, leaving the group shell behind.
        Scoped to this run's own groups via UUID — never touches groups from
        earlier runs the user may have kept.
        """
        self._delete_if_childless(self._created_assembly_uuids)
        self._created_assembly_uuids = []

    @staticmethod
    def _delete_if_childless(uuids: List[str]) -> None:
        for uuid in uuids:
            for node in cmds.ls(uuid, long=True) or []:
                if not (cmds.listRelatives(node, children=True) or []):
                    try:
                        cmds.delete(node)
                    except Exception as e:
                        logger.debug("Could not delete empty node %s: %s", node, e)

    @staticmethod
    def _shape_is_instanced(node_str: str) -> bool:
        """True when the node's mesh shape is shared by multiple transforms."""
        shapes = (
            cmds.listRelatives(
                node_str, shapes=True, noIntermediate=True, fullPath=True
            )
            or []
        )
        if not shapes:
            return False
        return len(cmds.listRelatives(shapes[0], allParents=True) or []) > 1

    def center_transform_on_geometry(self, node) -> None:
        """Moves the transform to the center of its geometry without moving the geometry."""
        node_str = str(node)
        try:
            shapes = (
                cmds.listRelatives(
                    node_str, shapes=True, noIntermediate=True, fullPath=True
                )
                or []
            )
            if not shapes:
                return
            sel = om.MSelectionList()
            sel.add(shapes[0])
            fn = om.MFnMesh(sel.getDagPath(0))
            pts = fn.getPoints(om.MSpace.kWorld)
        except Exception:
            return

        pts_np = np.array([[p.x, p.y, p.z] for p in pts])
        center = pts_np.mean(axis=0).tolist()
        cmds.xform(node_str, translation=center, worldSpace=True)
        fn.setPoints(pts, om.MSpace.kWorld)
        cmds.xform(node_str, centerPivots=True)

    @staticmethod
    def _capture_locked_normals(fn) -> Optional[Tuple[list, list, list]]:
        """World-space vectors of every LOCKED face-vertex normal.

        Returns (faceIds, vertexIds, worldVectors), or ``None`` when the mesh
        has no locked normals. Unlocked normals recompute from geometry and
        need no compensation.
        """
        locked = [fn.isNormalLocked(i) for i in range(fn.numNormals)]
        if not any(locked):
            return None
        counts, norm_ids = fn.getNormalIds()
        _, verts = fn.getVertices()
        normals_ws = fn.getNormals(om.MSpace.kWorld)
        faces, vertices, vectors = [], [], []
        fv = 0
        for face, c in enumerate(counts):
            for _ in range(c):
                nid = norm_ids[fv]
                if locked[nid]:
                    faces.append(face)
                    vertices.append(verts[fv])
                    vectors.append(om.MVector(normals_ws[nid]))
                fv += 1
        return faces, vertices, vectors

    def canonicalize_transform(self, node) -> None:
        """Aligns the transform's rotation to the geometry's PCA axes."""
        # Editing points through one instance path would counter-rotate the
        # shared shape for every OTHER path — never canonicalize instanced
        # geometry (the robust matcher handles un-canonicalized transforms).
        if self._shape_is_instanced(str(node)):
            return
        self.center_transform_on_geometry(node)

        basis_matrix = self.matcher.get_pca_basis(node)
        if not basis_matrix:
            return

        node_str = str(node)
        try:
            shapes = (
                cmds.listRelatives(
                    node_str, shapes=True, noIntermediate=True, fullPath=True
                )
                or []
            )
            if not shapes:
                return

            sel = om.MSelectionList()
            sel.add(shapes[0])
            fn = om.MFnMesh(sel.getDagPath(0))
            pts = fn.getPoints(om.MSpace.kWorld)
            # Locked normals live in object space and do NOT follow the
            # world-space point compensation below — without an explicit
            # restore, the custom shading of CAD/FBX imports rotates with
            # the transform.
            locked_normals = self._capture_locked_normals(fn)

            # ``geometry_matcher.get_pca_basis`` now returns ``om.MMatrix``;
            # use it directly. Fall back to row/col indexing for legacy
            # ``object`` returns where ``__getitem__`` yields a row.
            if isinstance(basis_matrix, om.MMatrix):
                tm = om.MTransformationMatrix(basis_matrix)
            else:
                flat = [basis_matrix[i][j] for i in range(4) for j in range(4)]
                tm = om.MTransformationMatrix(om.MMatrix(flat))
            euler = tm.rotation(asQuaternion=False)
            rot_deg = [
                math.degrees(euler.x),
                math.degrees(euler.y),
                math.degrees(euler.z),
            ]

            cmds.xform(node_str, rotation=rot_deg, worldSpace=True)
            fn.setPoints(pts, om.MSpace.kWorld)
            if locked_normals is not None:
                faces, vertices, vectors = locked_normals
                fn.setFaceVertexNormals(vectors, faces, vertices, om.MSpace.kWorld)

        except Exception as e:
            if self.verbose:
                logger.warning("Canonicalization failed for %s: %s", node_str, e)

    def canonicalize_leaf_meshes(self, nodes: List[object]) -> List[object]:
        """Canonicalize all leaf mesh transforms for instancing.

        This should be called AFTER reassemble_assemblies to prepare
        individual meshes for geometry comparison. It centers each mesh's
        transform at its geometric center and aligns rotation to PCA axes.

        NOTE: This is separate from BFS grouping (which needs original bboxes)
        because canonicalization expands bounding boxes and breaks touch detection.
        """
        for node in nodes:
            node_str = str(node)
            if not cmds.objExists(node_str):
                logger.debug("canonicalize_leaf_meshes: skipping stale %s", node_str)
                continue
            shapes = (
                cmds.listRelatives(
                    node_str, shapes=True, noIntermediate=True, fullPath=True
                )
                or []
            )
            if shapes and cmds.objectType(shapes[0]) == "mesh":
                self.canonicalize_transform(node_str)
            else:
                # It's a group - canonicalize children
                children = (
                    cmds.listRelatives(
                        node_str, children=True, type="transform", fullPath=True
                    )
                    or []
                )
                for child in children:
                    child_shapes = (
                        cmds.listRelatives(
                            child, shapes=True, noIntermediate=True, fullPath=True
                        )
                        or []
                    )
                    if child_shapes and cmds.objectType(child_shapes[0]) == "mesh":
                        self.canonicalize_transform(child)
        return nodes

    def reassemble_assemblies(self, nodes: List[object]) -> List[object]:
        """Reassemble separated shells into logical assemblies.

        Algorithm:
        1. Group shells into connected components of the SAME-MATERIAL
           bbox-touch graph. Restricting edges to same-material pairs matters:
           two assembly copies that only connect through a different-material
           bridge part (a deck, a mounting plate) must not fuse into one
           component — splitting a pure-touch component by material afterwards
           left such cliques glued together with no edges between them.
        2. Split genuinely fused components (copies that touch each other) by
           GCD of topology counts, assigning parts touch-first, then by
           internal-distance consistency, then by proximity.
        3. Recover orphaned copies (air gaps) from counts and exemplar
           distances.
        4. Keep a multi-part group only when its part multiset repeats in at
           least one other group (cross-copy support) — an assembly group
           exists to instance copies, so a one-off cluster of touching parts
           is returned as loose singles instead of a speculative assembly.
        5. Create Maya group nodes for each surviving assembly.
        """
        if not nodes:
            return []
        self._created_assembly_uuids = []

        # Filter to valid mesh transforms. Already-instanced meshes are
        # passed through untouched: they are deduplicated already, and baking
        # them into per-copy combined assemblies would re-duplicate their
        # data (and polyUnite on a shared shape destroys the sibling
        # instance paths).
        valid_nodes = []
        passthrough: List[str] = []
        for n in nodes:
            n_str = str(n)
            shapes = (
                cmds.listRelatives(
                    n_str, shapes=True, noIntermediate=True, fullPath=True
                )
                or []
            )
            if shapes and cmds.objectType(shapes[0]) == "mesh":
                if self._shape_is_instanced(n_str):
                    passthrough.append(n_str)
                else:
                    valid_nodes.append(n_str)

        if not valid_nodes:
            return [str(n) for n in nodes]

        # Build part info
        parts: List[Dict[str, Any]] = []
        for node in valid_nodes:
            try:
                shapes = (
                    cmds.listRelatives(
                        node, shapes=True, noIntermediate=True, fullPath=True
                    )
                    or []
                )
                shape = shapes[0] if shapes else None
                if not shape:
                    continue
                bbox = cmds.exactWorldBoundingBox(node)
                if not isinstance(bbox, list) or len(bbox) != 6:
                    continue

                nverts = cmds.polyEvaluate(shape, vertex=True)
                nfaces = cmds.polyEvaluate(shape, face=True)
                area = float(cmds.polyEvaluate(shape, worldArea=True))
                center = np.array(
                    [
                        (bbox[0] + bbox[3]) / 2,
                        (bbox[1] + bbox[4]) / 2,
                        (bbox[2] + bbox[5]) / 2,
                    ]
                )
                vol = (bbox[3] - bbox[0]) * (bbox[4] - bbox[1]) * (bbox[5] - bbox[2])
                mat = self._get_material(node)

                parts.append(
                    {
                        "idx": len(parts),
                        "node": node,
                        "bbox": bbox,
                        "topo": (nverts, nfaces),
                        "area": area,
                        "center": center,
                        "volume": vol,
                        "material": mat,
                    }
                )
            except Exception:
                pass

        if not parts:
            return list(nodes)

        # Sort parts into assembly copies via the shared DCC-neutral
        # clustering (pythontk AssemblySorter — same-material touch graph,
        # GCD count splits, distance-consistency assignment, orphan
        # recovery, cross-copy support gate; blendertk consumes the same
        # implementation). It mutates each part's ``topo`` in place,
        # appending the surface-area class id.
        sorter = ptk.AssemblySorter(
            search_radius_mult=self.search_radius_mult, verbose=self.verbose
        )
        final_groups = sorter.sort(parts)

        if self.verbose:
            logger.info("Final assembly count: %s", len(final_groups))

        return self._create_assembly_groups(parts, final_groups) + passthrough

    def _get_material(self, node) -> Optional[str]:
        """Material identity key for a node, or ``None``.

        Always material-aware regardless of the matcher's
        ``require_same_material``: a material boundary is physical evidence
        that parts belong to different objects, and measured on real CAD
        data, material-blind reconstruction lets different-material bridge
        parts fuse unrelated clusters (pair precision collapsed from 1.0 to
        0.26). ``require_same_material`` stays a MATCHING concern — whether
        two already-sorted candidates may instance.
        Multi-SG shells produce a sorted composite key — ``listConnections``
        order is not deterministic and a first-hit key would flip groupings
        between runs.
        """
        try:
            node_str = str(node)
            shapes = (
                cmds.listRelatives(
                    node_str, shapes=True, noIntermediate=True, fullPath=True
                )
                or []
            )
            if not shapes:
                return None
            sgs = sorted(set(cmds.listConnections(shapes[0], type="shadingEngine") or []))
            if sgs:
                return ",".join(sgs)
        except Exception:
            pass
        return None

    def _create_assembly_groups(
        self, parts: List[Dict], groups: List[List[int]]
    ) -> List[object]:
        """Create Maya group nodes for each assembly."""
        result = []
        used_nodes: Set[str] = set()

        for group in groups:
            if len(group) <= 1:
                # Single part - no assembly needed
                for idx in group:
                    node = str(parts[idx]["node"])
                    if node not in used_nodes:
                        result.append(node)
                        used_nodes.add(node)
                continue

            # Find the root (largest volume)
            root_idx = max(group, key=lambda i: parts[i]["volume"])
            root = str(parts[root_idx]["node"])
            children = [str(parts[idx]["node"]) for idx in group if idx != root_idx]

            # Check for already-used nodes
            if root in used_nodes or any(c in used_nodes for c in children):
                for idx in group:
                    node = str(parts[idx]["node"])
                    if node not in used_nodes:
                        result.append(node)
                        used_nodes.add(node)
                continue

            # Create assembly group
            try:
                assembly_grp = cmds.group(empty=True, name="Assembly_1")
                cmds.addAttr(
                    assembly_grp, longName=ASSEMBLY_TAG_ATTR, attributeType="bool"
                )
                cmds.setAttr(f"{assembly_grp}.{ASSEMBLY_TAG_ATTR}", True)
                self._created_assembly_uuids.extend(
                    cmds.ls(assembly_grp, uuid=True) or []
                )

                # Position at centroid
                points = [parts[idx]["center"] for idx in group]
                centroid = np.mean(points, axis=0).tolist()
                cmds.xform(assembly_grp, translation=centroid, worldSpace=True)
                root_rot = cmds.xform(root, q=True, rotation=True, worldSpace=True)
                cmds.xform(assembly_grp, rotation=root_rot, worldSpace=True)

                cmds.parent(root, assembly_grp)
                used_nodes.add(root)

                for child in children:
                    cmds.parent(child, assembly_grp)
                    used_nodes.add(child)

                result.append(assembly_grp)

            except Exception as e:
                logger.error(f"Error creating assembly for {root}: {e}")
                for idx in group:
                    node = str(parts[idx]["node"])
                    if node not in used_nodes:
                        result.append(node)
                        used_nodes.add(node)

        return result

    @staticmethod
    def _is_assembly_group(node: str) -> bool:
        """True if *node* is an assembly group created by this tool."""
        try:
            return cmds.objectType(node) == "transform" and cmds.attributeQuery(
                ASSEMBLY_TAG_ATTR, node=node, exists=True
            )
        except Exception:
            return False

    def combine_reassembled_assemblies(self, nodes: List[object]) -> List[object]:
        """Combine each copy of a repeated assembly type into a single mesh.

        Assembly groups are clustered by their part-signature multiset (the
        assembly "type"); every copy of a type with >= 2 copies is combined
        into one mesh so the copies instance at assembly level. A previous
        version selected "core" parts by a scene-wide majority threshold —
        with several assembly types in one scene no signature can reach a
        majority, so nothing ever combined and the copies degraded to micro
        part instances.

        Unique (single-copy) assembly types are left as reconstructed groups:
        combining them gains no instancing, and their parts stay eligible for
        leaf-level matching.
        """
        if not nodes:
            return []
        self._combined_assembly_uuids = []

        combined_meshes = []
        assembly_groups = []
        other_nodes = []

        for node in nodes:
            node_str = str(node)
            if self._is_assembly_group(node_str):
                assembly_groups.append(node_str)
            else:
                other_nodes.append(node_str)

        combined_meshes.extend(other_nodes)
        if not assembly_groups:
            return combined_meshes

        # Cluster groups by assembly type: the multiset of relaxed part
        # signatures. Copies of one assembly hold the same parts in the same
        # counts; the relaxed (topology-only) signature is enough here — the
        # combined results are still verified by full geometric matching
        # before any instancing happens.
        grp_children: Dict[str, List[str]] = {}
        by_type: Dict[frozenset, List[str]] = defaultdict(list)
        for grp in assembly_groups:
            children = (
                cmds.listRelatives(grp, children=True, type="transform", fullPath=True)
                or []
            )
            mesh_children = [c for c in children if self._is_mesh_transform(c)]
            grp_children[grp] = mesh_children
            sig_counts: Dict[Tuple, int] = defaultdict(int)
            for c in mesh_children:
                s = self.matcher.get_mesh_signature(c)
                if s:
                    sig_counts[s[:3]] += 1
            by_type[frozenset(sig_counts.items())].append(grp)

        for type_key, grps in by_type.items():
            if len(grps) < 2 or not type_key:
                # Unique type (or no signable parts) — keep the group intact;
                # its parts stay individually eligible for leaf matching.
                for grp in grps:
                    combined_meshes.extend(grp_children[grp])
                continue

            for grp in grps:
                parts = [p for p in grp_children[grp] if cmds.objExists(p)]
                if not parts:
                    combined_meshes.append(grp)
                    continue
                grp_short = grp.split("|")[-1]
                if len(parts) == 1:
                    core_mesh = parts[0]
                else:
                    try:
                        result_list = (
                            cmds.polyUnite(
                                parts,
                                name=f"{grp_short}_core",
                                ch=False,
                                mergeUVSets=True,
                            )
                            or []
                        )
                        core_mesh = result_list[0] if result_list else None
                    except Exception as e:
                        logger.warning("polyUnite failed for %s: %s", grp, e)
                        core_mesh = None

                if core_mesh:
                    try:
                        core_mesh = cmds.rename(core_mesh, f"{grp_short}_combined")
                        self.canonicalize_transform(core_mesh)
                        # Re-resolve after rename/canonicalize; single-part
                        # cores may still live under the (soon-deleted) group.
                        if cmds.listRelatives(core_mesh, parent=True):
                            reparented = cmds.parent(core_mesh, world=True)
                            core_mesh = (cmds.ls(reparented, long=True) or [core_mesh])[0]
                    except Exception:
                        pass
                    self._combined_assembly_uuids.extend(
                        cmds.ls(core_mesh, uuid=True) or []
                    )
                    combined_meshes.append(core_mesh)
                else:
                    # Union failed — keep the group so nothing is lost.
                    combined_meshes.append(grp)
                    continue

                try:
                    if not (
                        cmds.listRelatives(grp, children=True, fullPath=True) or []
                    ):
                        cmds.delete(grp)
                except Exception:
                    pass

        return combined_meshes

    @staticmethod
    def _is_mesh_transform(n) -> bool:
        """Check if a node is a transform with a valid mesh shape."""
        try:
            n_str = str(n)
            if not cmds.objExists(n_str) or cmds.objectType(n_str) != "transform":
                return False
            # noIntermediate=True already excludes intermediate objects
            shapes = (
                cmds.listRelatives(
                    n_str, shapes=True, noIntermediate=True, fullPath=True
                )
                or []
            )
            return bool(shapes) and cmds.objectType(shapes[0]) == "mesh"
        except Exception:
            return False
