# !/usr/bin/python
# coding=utf-8
"""Scene auto-instancer prototype."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple, Any, Union
from collections import defaultdict

try:
    import pymel.core as pm
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


class InstanceCandidate:
    """Holds information about a transform candidate for instancing."""

    def __init__(self, transform: pm.nodetypes.Transform):
        self.transform = transform
        self.matrix = transform.getMatrix(worldSpace=True)
        self.parent = transform.getParent()
        self.visibility = transform.visibility.get()
        # Transform required to align prototype to this candidate (if instanced)
        self.relative_transform: Optional[pm.dt.Matrix] = None

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
    ) -> None:
        super().__init__()
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
        nodes: Optional[Sequence[pm.nodetypes.Transform]] = None,
    ) -> List[pm.nodetypes.Transform]:
        """Entry point for discovering and instancing matching meshes."""
        if nodes is None:
            nodes = pm.ls(selection=True, type="transform")
            if not nodes:
                nodes = pm.ls(type="transform")

        # Handle separation if requested
        if self.separate_combined:
            nodes = self.reconstructor.separate_combined_meshes(nodes)
            nodes = self.reconstructor.reassemble_assemblies(nodes)

            if self.combine_assemblies:
                nodes = self.reconstructor.combine_reassembled_assemblies(nodes)
                self.check_hierarchy = False
            else:
                self.check_hierarchy = True

            # Canonicalize leaf meshes AFTER reassembly so instancing can match them
            nodes = self.reconstructor.canonicalize_leaf_meshes(nodes)

        groups = self.find_instance_groups(nodes)

        # Sort groups by hierarchy depth of prototype (shallowest first)
        groups.sort(key=lambda g: len(g.prototype.transform.getAllParents()))

        report: List[Dict[str, object]] = []
        all_instances: List[pm.nodetypes.Transform] = []

        for group in groups:
            if not group.members:
                continue

            # Apply Instancing Strategy Rules (First Pass)
            prototype_transform = group.prototype.transform
            prototype_shape = prototype_transform.getShape()

            group_size = len(group.members) + 1
            strategy = StrategyType.COMBINE  # Default

            if prototype_shape and isinstance(prototype_shape, pm.nodetypes.Mesh):
                strategy = self.strategy_analyzer.evaluate(
                    group_size, mesh_node=prototype_shape
                )
            else:
                # It's an assembly/group - calculate total triangles
                tri_count = 0
                meshes = prototype_transform.listRelatives(
                    allDescendents=True, type="mesh"
                )
                for m in meshes:
                    if not m.intermediateObject.get():
                        try:
                            tri_count += int(pm.polyEvaluate(m, triangle=True))
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
            all_transforms = pm.ls(type="transform")
            for t in all_transforms:
                if t in processed_nodes:
                    continue

                shape = t.getShape()
                if shape and not shape.intermediateObject.get():
                    if isinstance(shape, pm.nodetypes.Mesh):
                        leaf_candidates.append(t)

            original_check = self.check_hierarchy
            self.check_hierarchy = False

            leaf_groups = self.find_instance_groups(leaf_candidates)

            for group in leaf_groups:
                if not group.members:
                    continue

                # Apply Instancing Strategy Rules
                # We check the prototype mesh for triangle count
                prototype_mesh = group.prototype.transform.getShape()
                if not prototype_mesh or not isinstance(
                    prototype_mesh, pm.nodetypes.Mesh
                ):
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
        self, nodes: Optional[Sequence[pm.nodetypes.Transform]] = None
    ) -> List[InstanceGroup]:
        """Finds groups of identical objects in the scene."""
        if nodes is None:
            nodes = pm.ls(selection=True, type="transform")
            if not nodes:
                nodes = pm.ls(type="transform")

        candidates = []
        if self.check_hierarchy:
            for n in nodes:
                if n.isReadOnly():
                    continue
                if n.name() in ["persp", "top", "front", "side"]:
                    continue
                candidates.append(InstanceCandidate(n))
        else:
            for n in nodes:
                shape = n.getShape()
                if (
                    shape
                    and isinstance(shape, pm.nodetypes.Mesh)
                    and not shape.intermediateObject.get()
                ):
                    candidates.append(InstanceCandidate(n))

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

        for sig, potential_matches in signature_map.items():
            potential_matches.sort(
                key=lambda x: (
                    not (
                        x.transform.getShape() and x.transform.getShape().isInstanced()
                    ),
                    x.transform.name(),
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
    ) -> List[pm.nodetypes.Transform]:
        """Convert all members of a group to instances of the prototype."""
        if not group.prototype.transform.exists():
            return []

        if not group.members:
            return [group.prototype.transform]

        prototype_transform = group.prototype.transform
        instances = []

        for member in group.members:
            target = member.transform
            if not target.exists():
                continue

            target_name = target.name()

            # 1. Duplicate target transform
            new_instance = pm.duplicate(target, parentOnly=True)[0]

            # Apply relative transform if it exists
            rel_mtx = member.relative_transform
            if not rel_mtx and hasattr(target, "relative_transform"):
                rel_mtx = target.relative_transform

            if rel_mtx:
                # Apply relative transform (rotation/scale) to the new instance
                # We combine it with the existing transform (from target)
                # Order: rel_mtx (shape correction) * target_matrix (world placement)
                target_matrix = new_instance.getMatrix(worldSpace=True)
                final_matrix = rel_mtx * target_matrix
                new_instance.setMatrix(final_matrix, worldSpace=True)

            # 2. Create temp instance of prototype
            temp_instance = pm.instance(prototype_transform, leaf=True)[0]

            # 3. Move contents of temp_instance to new_instance
            children = temp_instance.getChildren()
            for child in children:
                if not isinstance(child, pm.nodetypes.Shape):
                    if new_instance == child or new_instance.hasParent(child):
                        continue

                if isinstance(child, pm.nodetypes.Shape):
                    try:
                        pm.parent(child, new_instance, shape=True, relative=True)
                    except RuntimeError as e:
                        self.logger.warning("Failed to parent shape %s: %s", child, e)
                else:
                    try:
                        pm.parent(child, new_instance, relative=True)
                    except RuntimeError as e:
                        self.logger.warning(
                            "Failed to parent transform %s: %s", child, e
                        )

            # 4. Cleanup temp_instance
            pm.delete(temp_instance)

            # 5. Preserve children of target
            if not self.check_hierarchy:
                target_children = target.getChildren(type="transform")
                if target_children:
                    try:
                        pm.parent(target_children, world=True)
                    except Exception:
                        pass

            # 6. Delete original and rename instance
            pm.delete(target)
            new_instance.rename(target_name)

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
    sel = pm.selected()

    instancer = AutoInstancer(
        separate_combined=True,
        combine_assemblies=True,
        check_hierarchy=False,
        require_same_material=False,
        verbose=True,
    )
    instancer.run(sel)
