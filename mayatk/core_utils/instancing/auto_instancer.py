# !/usr/bin/python
# coding=utf-8
"""Scene auto-instancer prototype."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple, Any, Union
from collections import defaultdict

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError as error:
    print(__file__, error)

import pythontk as ptk

# From this package:
from mayatk.core_utils.instancing.geometry_matcher import GeometryMatcher
from mayatk.core_utils.instancing.assembly_reconstructor import AssemblyReconstructor
from mayatk.core_utils.instancing.instancing_strategy import (
    InstancingStrategy,
    StrategyConfig,
    StrategyType,
)
from mayatk.core_utils._core_utils import short_name
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.xform_utils._xform_utils import get_object_matrix, set_object_matrix


class InstanceCandidate:
    """Holds information about a transform candidate for instancing.

    ``transform`` is a property that re-resolves the node's *current* DAG
    path from its UUID on each access. Plain string paths do not survive
    reparenting/renaming transparently because it held an ``MObject``;
    bare path strings do not. We mirror that behavior with a UUID lookup.
    """

    def __init__(self, transform):
        path = str(transform)
        uuids = cmds.ls(path, uuid=True) or []
        self.uuid: Optional[str] = uuids[0] if uuids else None
        self._path: str = path  # fallback if uuid lookup fails
        self.matrix = get_object_matrix(path, world=True)
        self.parent = (
            cmds.listRelatives(path, parent=True, fullPath=True) or [None]
        )[0]
        try:
            self.visibility = bool(cmds.getAttr(f"{path}.visibility"))
        except Exception:
            self.visibility = True
        # Transform required to align prototype to this candidate (if instanced)
        self.relative_transform: Optional[om.MMatrix] = None

    @property
    def transform(self) -> str:
        if self.uuid:
            current = cmds.ls(self.uuid, long=True) or []
            if current:
                return current[0]
        return self._path

    def exists(self) -> bool:
        if self.uuid:
            return bool(cmds.ls(self.uuid))
        return cmds.objExists(self._path)

    def __repr__(self):
        return f"<InstanceCandidate {self.transform}>"


class InstanceGroup:
    """A group of objects that are geometrically identical."""

    def __init__(self, prototype: InstanceCandidate):
        self.prototype = prototype
        self.members: List[InstanceCandidate] = []

    def __repr__(self):
        return f"<InstanceGroup prototype={self.prototype.transform} members={len(self.members)}>"


class AutoInstancer(ptk.LoggingMixin):
    """Prototype workflow for converting matching meshes into instances."""

    def __init__(
        self,
        tolerance: float = 0.001,
        scale_tolerance: float = 0.0,
        require_same_material: Union[bool, int] = True,
        check_uvs: bool = False,
        check_hierarchy: bool = False,
        separate_combined: bool = False,
        combine_assemblies: bool = False,
        verbose: bool = True,
        search_radius_mult: float = 1.5,
        # Strategy Config
        is_static: bool = True,
        needs_individual: bool = False,
        will_be_lightmapped: bool = False,
        can_gpu_instance: bool = True,
        log_level: str = "WARNING",
    ) -> None:
        super().__init__()
        self.set_log_level(log_level)
        self._tolerance = tolerance
        self._scale_tolerance = scale_tolerance
        self._require_same_material = require_same_material
        self._check_uvs = check_uvs
        self.check_hierarchy = check_hierarchy
        self.separate_combined = separate_combined
        self.combine_assemblies = combine_assemblies
        self._verbose = verbose
        self.search_radius_mult = search_radius_mult

        # Strategy Config
        self.strategy_config = StrategyConfig(
            is_static=is_static,
            needs_individual=needs_individual,
            will_be_lightmapped=will_be_lightmapped,
            can_gpu_instance=can_gpu_instance,
        )
        self.strategy_analyzer = InstancingStrategy(self.strategy_config)

        # Initialize components
        self.matcher = GeometryMatcher(
            tolerance=tolerance,
            scale_tolerance=scale_tolerance,
            require_same_material=require_same_material,
            check_uvs=check_uvs,
            verbose=verbose,
        )
        self.reconstructor = AssemblyReconstructor(
            matcher=self.matcher,
            combine_assemblies=combine_assemblies,
            search_radius_mult=search_radius_mult,
            verbose=verbose,
        )

    @property
    def tolerance(self):
        return self._tolerance

    @tolerance.setter
    def tolerance(self, value):
        self._tolerance = value
        if hasattr(self, "matcher"):
            self.matcher.tolerance = value

    @property
    def require_same_material(self):
        return self._require_same_material

    @require_same_material.setter
    def require_same_material(self, value):
        self._require_same_material = value
        if hasattr(self, "matcher"):
            self.matcher.require_same_material = value

    @property
    def check_uvs(self):
        return self._check_uvs

    @check_uvs.setter
    def check_uvs(self, value):
        self._check_uvs = value
        if hasattr(self, "matcher"):
            self.matcher.check_uvs = value

    @property
    def verbose(self):
        return self._verbose

    @verbose.setter
    def verbose(self, value):
        self._verbose = value
        if hasattr(self, "matcher"):
            self.matcher.verbose = value
        if hasattr(self, "reconstructor"):
            self.reconstructor.verbose = value

    def run(
        self,
        nodes: Optional[Sequence[object]] = None,
    ) -> List[object]:
        """Entry point for discovering and instancing matching meshes."""
        if nodes is None:
            nodes = cmds.ls(selection=True, type="transform")
            if not nodes:
                nodes = cmds.ls(type="transform")

        # Handle separation if requested
        if self.separate_combined:
            nodes = self.reconstructor.separate_combined_meshes(nodes)
            nodes = self.reconstructor.reassemble_assemblies(nodes)

            if self.combine_assemblies:
                nodes = self.reconstructor.combine_reassembled_assemblies(nodes)
                self.check_hierarchy = False
            else:
                self.check_hierarchy = True

        # Canonicalize leaf meshes AFTER reassembly so instancing can match
        # them.  Canonicalization absorbs translation/rotation into the
        # transform so PCA-aligned geometry compares equal — required for the
        # reassembly flow.  For plain leaf-instancing it's too aggressive: it
        # collapses frozen-vs-non-frozen and pivot-shifted cubes into the
        # same signature, instancing objects that the user wants kept
        # distinct.  Gate on ``separate_combined`` (the assembly flow) only.
        if self.separate_combined:
            nodes = self.reconstructor.canonicalize_leaf_meshes(nodes)

        groups = self.find_instance_groups(nodes)

        # Sort groups by hierarchy depth of prototype (shallowest first).
        # Walk the ancestor chain — equivalent to
        # the count of ``|`` separators in a full DAG path.
        def _depth(transform: str) -> int:
            full = cmds.ls(transform, long=True) or [transform]
            return full[0].count("|")

        groups.sort(key=lambda g: _depth(g.prototype.transform))

        report: List[Dict[str, object]] = []
        all_instances: List[object] = []

        for group in groups:
            if not group.members:
                continue

            # An earlier (shallower) group's processing may have deleted or
            # re-parented this group's prototype. Skip if it's gone — its
            # contents were already instanced by the ancestor pass.
            if not group.prototype.exists():
                continue
            group.members = [m for m in group.members if m.exists()]
            if not group.members:
                continue

            # Apply Instancing Strategy Rules (First Pass)
            prototype_transform = group.prototype.transform
            prototype_shape = (cmds.listRelatives(prototype_transform, shapes=True, fullPath=True) or [None])[0]

            group_size = len(group.members) + 1
            strategy = StrategyType.COMBINE  # Default

            if prototype_shape and cmds.objectType(prototype_shape) == 'mesh':
                strategy = self.strategy_analyzer.evaluate(
                    group_size, mesh_node=prototype_shape
                )
            else:
                # It's an assembly/group - calculate total triangles
                tri_count = 0
                meshes = cmds.listRelatives(
                    prototype_transform,
                    allDescendents=True,
                    type="mesh",
                    fullPath=True,
                ) or []
                for m in meshes:
                    if not NodeUtils.is_intermediate(m):
                        try:
                            tri_count += int(cmds.polyEvaluate(m, triangle=True))
                        except Exception:
                            pass
                strategy = self.strategy_analyzer.evaluate(
                    group_size, triangle_count=tri_count
                )

            # Allow COMBINE strategy to proceed with instancing (workflow benefit)
            if strategy not in (StrategyType.GPU_INSTANCE,):
                if self.verbose:
                    self.logger.info(
                        "Skipping instancing for %s (Strategy: %s, Count: %s)",
                        group.prototype.transform,
                        strategy.name,
                        group_size,
                    )
                continue

            created = self._convert_group_to_instances(group)
            if not created or len(created) == 1:
                continue
            all_instances.extend(created)
            report.append(
                {
                    "prototype": group.prototype.transform,
                    "instance_count": len(created) - 1,
                    "instances": created,
                }
            )

        if self.verbose:
            self._log_report(report, len(groups))

        # SECOND PASS: Instance Leaf Nodes (Geometry)
        if self.separate_combined and not self.combine_assemblies:
            if self.verbose:
                self.logger.info("Running Second Pass: Leaf Geometry Instancing...")

            # Track processed nodes to avoid re-instancing
            processed_nodes = set(all_instances)

            leaf_candidates = []
            all_transforms = cmds.ls(type="transform")
            for t in all_transforms:
                if t in processed_nodes:
                    continue

                shape = NodeUtils.get_shape(t)
                if shape and not NodeUtils.is_intermediate(shape):
                    if cmds.objectType(shape) == 'mesh':
                        leaf_candidates.append(t)

            original_check = self.check_hierarchy
            self.check_hierarchy = False

            leaf_groups = self.find_instance_groups(leaf_candidates)

            for group in leaf_groups:
                if not group.members:
                    continue

                # Apply Instancing Strategy Rules
                # We check the prototype mesh for triangle count
                prototype_mesh = NodeUtils.get_shape(group.prototype.transform)
                if not prototype_mesh or not cmds.objectType(prototype_mesh) == 'mesh':
                    continue

                # Group size is members + prototype (1)
                group_size = len(group.members) + 1
                strategy = self.strategy_analyzer.evaluate(group_size, prototype_mesh)

                # Allow COMBINE strategy to proceed with instancing (workflow benefit)
                if strategy not in (StrategyType.GPU_INSTANCE, StrategyType.COMBINE):
                    if self.verbose:
                        self.logger.info(
                            "Skipping instancing for %s (Strategy: %s, Count: %s)",
                            group.prototype.transform,
                            strategy.name,
                            group_size,
                        )
                    continue

                created = self._convert_group_to_instances(group)
                if not created or len(created) == 1:
                    continue
                all_instances.extend(created)

            self.check_hierarchy = original_check

        return all_instances

    def find_instance_groups(
        self, nodes: Optional[Sequence[object]] = None
    ) -> List[InstanceGroup]:
        """Finds groups of identical objects in the scene."""
        if nodes is None:
            nodes = cmds.ls(selection=True, type="transform")
            if not nodes:
                nodes = cmds.ls(type="transform")

        candidates = []
        if self.check_hierarchy:
            for n in nodes:
                node_str = str(n)
                # Skip locked / referenced read-only nodes.
                try:
                    if cmds.lockNode(node_str, q=True, lock=True)[0]:
                        continue
                except Exception:
                    pass
                if node_str.split('|')[-1].split(':')[-1] in ["persp", "top", "front", "side"]:
                    continue
                candidates.append(InstanceCandidate(node_str))
        else:
            for n in nodes:
                node_str = str(n)
                shape = NodeUtils.get_shape(node_str, no_intermediate=False)
                if (
                    shape
                    and cmds.objectType(shape) == 'mesh'
                    and not NodeUtils.is_intermediate(shape)
                ):
                    candidates.append(InstanceCandidate(node_str))

        # Group by signature
        signature_map = defaultdict(list)
        for candidate in candidates:
            if self.check_hierarchy:
                sig = self.matcher.get_hierarchy_signature(candidate.transform)
            else:
                sig = self.matcher.get_mesh_signature(candidate.transform)

            if sig:
                signature_map[sig].append(candidate)

        # Merge similar signatures if we are in combine mode
        if not self.check_hierarchy and self.combine_assemblies:
            signature_map = self._merge_similar_signatures(signature_map)

        if self.verbose:
            print(f"Signature Map: {len(signature_map)} unique signatures")
            for sig, items in signature_map.items():
                print(f"  Sig {sig}: {len(items)} items")

        groups = []

        def _is_instanced(transform_path: str) -> bool:
            shape = NodeUtils.get_shape(transform_path)
            if not shape:
                return False
            # In Maya, an instanced shape has more than one parent transform.
            parents = cmds.listRelatives(shape, allParents=True) or []
            return len(parents) > 1

        for sig, potential_matches in signature_map.items():
            potential_matches.sort(
                key=lambda x: (
                    not _is_instanced(x.transform),
                    short_name(x.transform),
                )
            )

            if not self.check_hierarchy and self.combine_assemblies:
                if not potential_matches:
                    continue
                prototype = potential_matches[0]
                group = InstanceGroup(prototype)
                group.members.extend(potential_matches[1:])
                groups.append(group)
                continue

            while potential_matches:
                prototype = potential_matches.pop(0)
                current_group = InstanceGroup(prototype)

                remaining_candidates = []
                for candidate in potential_matches:
                    is_identical = False
                    rel_mtx = None

                    if self.check_hierarchy:
                        is_identical, rel_mtx = self.matcher.are_hierarchies_identical(
                            prototype.transform, candidate.transform, is_root=True
                        )
                    else:
                        is_identical, rel_mtx = self.matcher.are_meshes_identical(
                            prototype.transform, candidate.transform
                        )

                    if is_identical:
                        if rel_mtx:
                            candidate.relative_transform = rel_mtx
                        current_group.members.append(candidate)
                    else:
                        remaining_candidates.append(candidate)

                groups.append(current_group)
                potential_matches = remaining_candidates

        return groups

    def _merge_similar_signatures(self, signature_map):
        """Merges signature buckets that are similar enough."""
        sorted_keys = sorted(
            signature_map.keys(), key=lambda x: (x[0], x[1], x[2], x[3])
        )

        merged_map = defaultdict(list)
        processed_sigs = set()

        for i, sig in enumerate(sorted_keys):
            if sig in processed_sigs:
                continue

            merged_map[sig].extend(signature_map[sig])
            processed_sigs.add(sig)

            v, e, f = sig[:3]
            area = sig[3]
            pca = sig[4]

            for j in range(i + 1, len(sorted_keys)):
                other_sig = sorted_keys[j]
                if other_sig in processed_sigs:
                    continue

                ov, oe, of = other_sig[:3]
                o_area = other_sig[3]
                o_pca = other_sig[4]

                if (ov, oe, of) == (v, e, f):
                    if (
                        abs(area - o_area) > 1.0
                        and abs(area - o_area) / (area + 0.001) > 0.05
                    ):
                        continue

                    if pca and o_pca:
                        diff = sum(abs(p1 - p2) for p1, p2 in zip(pca, o_pca))
                        if diff > 0.1:
                            continue
                    elif pca != o_pca:
                        continue

                    merged_map[sig].extend(signature_map[other_sig])
                    processed_sigs.add(other_sig)

                elif pca and o_pca:
                    diff = sum(abs(p1 - p2) for p1, p2 in zip(pca, o_pca))
                    total_mag = sum(pca) + sum(o_pca) + 0.001
                    rel_diff = diff / total_mag

                    if rel_diff < 0.005:
                        merged_map[sig].extend(signature_map[other_sig])
                        processed_sigs.add(other_sig)

        return merged_map

    def _convert_group_to_instances(
        self, group: InstanceGroup
    ) -> List[str]:
        """Convert all members of a group to instances of the prototype."""
        if not cmds.objExists(group.prototype.transform):
            return []

        if not group.members:
            return [group.prototype.transform]

        prototype_transform = group.prototype.transform
        instances: List[str] = []

        for member in group.members:
            target = member.transform
            if not cmds.objExists(target):
                continue

            target_name = target.split('|')[-1].split(':')[-1]

            # 1. Duplicate target transform
            new_instance = cmds.duplicate(target, parentOnly=True)[0]

            # Apply relative transform if it exists
            rel_mtx = member.relative_transform

            if rel_mtx:
                # Combine with existing transform (from target).
                # Order: rel_mtx (shape correction) * target_matrix (world placement)
                target_matrix = get_object_matrix(new_instance, world=True)
                final_matrix = rel_mtx * target_matrix
                set_object_matrix(new_instance, final_matrix, world=True)

            # 2. Create temp instance of prototype
            temp_instance = cmds.instance(prototype_transform, leaf=True)[0]

            # 3. Move contents of temp_instance to new_instance
            children = (
                cmds.listRelatives(temp_instance, children=True, fullPath=True) or []
            )
            for child in children:
                is_shape = cmds.objectType(child) == 'shape' or cmds.ls(
                    child, shapes=True
                )
                if not is_shape:
                    # Skip self-parenting and ancestor cycles.
                    if new_instance == child or child in (
                        cmds.listRelatives(new_instance, allParents=True, fullPath=True)
                        or []
                    ):
                        continue

                if is_shape:
                    try:
                        cmds.parent(child, new_instance, shape=True, relative=True)
                    except RuntimeError as e:
                        self.logger.warning("Failed to parent shape %s: %s", child, e)
                else:
                    try:
                        cmds.parent(child, new_instance, relative=True)
                    except RuntimeError as e:
                        self.logger.warning(
                            "Failed to parent transform %s: %s", child, e
                        )

            # 4. Cleanup temp_instance
            cmds.delete(temp_instance)

            # 5. Preserve children of target
            if not self.check_hierarchy:
                target_children = NodeUtils.get_children(target, type="transform")
                if target_children:
                    try:
                        cmds.parent(target_children, world=True)
                    except Exception:
                        pass

            # 6. Delete original and rename instance
            cmds.delete(target)
            new_instance = cmds.rename(new_instance, target_name)

            instances.append(new_instance)

        return [prototype_transform] + instances

    def _log_report(self, report: List[Dict[str, object]], group_count: int) -> None:
        total_instances = sum(entry["instance_count"] for entry in report)
        self.logger.info(
            "AutoInstancer processed %s groups and created %s instances",
            group_count,
            total_instances,
        )
        for entry in report:
            prototype = entry["prototype"]
            count = entry["instance_count"]
            self.logger.info(" - %s → %s instances", prototype, count)


if __name__ == "__main__":
    from mayatk import clear_scrollfield_reporters, AutoInstancer

    clear_scrollfield_reporters()
    sel = cmds.ls(selection=True) or []

    instancer = AutoInstancer(
        separate_combined=True,
        combine_assemblies=True,
        check_hierarchy=False,
        require_same_material=False,
        verbose=True,
    )
    instancer.run(sel)
