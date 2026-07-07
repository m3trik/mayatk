# !/usr/bin/python
# coding=utf-8
"""Scene auto-instancer: convert geometrically identical meshes to instances."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple, Union
from collections import defaultdict

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError as error:
    print(__file__, error)

import pythontk as ptk

# From this package:
from mayatk.core_utils.auto_instancer.geometry_matcher import GeometryMatcher
from mayatk.core_utils.auto_instancer.assembly_reconstructor import (
    AssemblyReconstructor,
    ASSEMBLY_TAG_ATTR,
)
from mayatk.core_utils.auto_instancer.instancing_strategy import (
    InstancingStrategy,
    StrategyConfig,
    StrategyType,
)
from mayatk.core_utils._core_utils import short_name
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.xform_utils._xform_utils import get_object_matrix, set_object_matrix

# Default cameras cannot be deleted; skip them regardless of other filters.
_DEFAULT_CAMERAS = frozenset(("persp", "top", "front", "side"))

# Strategies that convert to Maya instances. Both GPU_INSTANCE and COMBINE
# convert: sharing shape data in Maya saves memory and keeps duplicates
# editable-as-one regardless of the engine-side draw-call decision the
# strategy encodes (GPU_INSTANCE's repeat/triangle thresholds are export
# advice, not a reason to silently leave duplicates un-instanced — gating on
# them skipped whole assembly groups and let the leaf pass shred them into
# micro instances instead). KEEP_SEPARATE (needs_individual, or non-static
# non-GPU-instanceable) still blocks conversion.
_CONVERTIBLE_STRATEGIES = (StrategyType.GPU_INSTANCE, StrategyType.COMBINE)


def _natural_key(name: str) -> Tuple:
    """Sort key ordering embedded integers numerically (``Cube2`` < ``Cube10``)."""
    return tuple(
        int(token) if token.isdigit() else token
        for token in re.split(r"(\d+)", name)
    )


def _long_path(node: str) -> str:
    """Best-effort full DAG path for *node* (falls back to the input string)."""
    resolved = cmds.ls(str(node), long=True) or []
    return resolved[0] if resolved else str(node)


def _mesh_shape(transform: str) -> Optional[str]:
    """The transform's first non-intermediate MESH shape, or ``None``."""
    shape = NodeUtils.get_shape(transform)
    if shape and cmds.objectType(shape) == "mesh":
        return shape
    return None


def _is_instanced(transform_path: str) -> bool:
    """True if the transform's shape has more than one parent transform."""
    shape = NodeUtils.get_shape(transform_path)
    if not shape:
        return False
    parents = cmds.listRelatives(shape, allParents=True) or []
    return len(parents) > 1


def _prototype_preference_key(candidate: "InstanceCandidate") -> Tuple:
    """Sort key for prototype selection within a group.

    Already-instanced first (extends the existing instance set), then
    natural name order, then full path — deterministic across runs.
    """
    transform = candidate.transform
    return (
        not _is_instanced(transform),
        _natural_key(short_name(transform)),
        transform,
    )


class InstanceCandidate:
    """Holds information about a transform candidate for instancing.

    ``transform`` is a property that re-resolves the node's *current* DAG
    path from its UUID on each access, so candidates survive the renaming
    and reparenting that instancing performs on the scene.
    """

    def __init__(self, transform):
        path = _long_path(transform)
        uuids = cmds.ls(path, uuid=True) or []
        self.uuid: Optional[str] = uuids[0] if uuids else None
        self._path: str = path  # fallback if uuid lookup fails
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
    """Convert matching meshes into instances.

    Destructive operations (deleting originals) only happen after the
    replacement instance has been fully assembled; a failure on one member
    leaves that member untouched and continues with the rest. The whole run
    is wrapped in a single undo chunk.
    """

    def __init__(
        self,
        tolerance: float = 0.001,
        scale_tolerance: Optional[float] = None,
        require_same_material: Union[bool, int] = True,
        check_uvs: bool = False,
        check_hierarchy: bool = False,
        separate_combined: bool = False,
        combine_assemblies: bool = True,
        combine_non_instanced: bool = True,
        combine_by_material: bool = True,
        combine_by_distance: bool = True,
        combine_distance_threshold: float = 10000.0,
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
        # None = flow-dependent default. The assembly flow instances
        # uniformly SCALED copies (Maya carries scale on the instance
        # transform; the reference results demand it), while plain leaf
        # instancing stays strict so a resized prop is kept distinct.
        # Pass an explicit value to override either way (any value > 0
        # enables whitened arbitrary-scale matching).
        if scale_tolerance is None:
            scale_tolerance = 1.0 if separate_combined else 0.0
        self._scale_tolerance = scale_tolerance
        self._require_same_material = require_same_material
        self._check_uvs = check_uvs
        self.check_hierarchy = check_hierarchy
        self.separate_combined = separate_combined
        self._combine_assemblies = combine_assemblies
        # Game-ready remainder: polyUnite whatever did not instance (see
        # _combine_non_instanced). Skipped for non-static / needs_individual.
        self.combine_non_instanced = combine_non_instanced
        self.combine_by_material = combine_by_material
        self.combine_by_distance = combine_by_distance
        self.combine_distance_threshold = combine_distance_threshold
        self._verbose = verbose
        self._search_radius_mult = search_radius_mult

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

    # ------------------------------------------------------------------
    # Configuration properties — forwarded to collaborators so post-init
    # changes stay in sync.
    # ------------------------------------------------------------------
    @property
    def tolerance(self):
        return self._tolerance

    @tolerance.setter
    def tolerance(self, value):
        self._tolerance = value
        if hasattr(self, "matcher"):
            self.matcher.tolerance = value

    @property
    def scale_tolerance(self):
        return self._scale_tolerance

    @scale_tolerance.setter
    def scale_tolerance(self, value):
        self._scale_tolerance = value
        if hasattr(self, "matcher"):
            self.matcher.scale_tolerance = value

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
    def combine_assemblies(self):
        return self._combine_assemblies

    @combine_assemblies.setter
    def combine_assemblies(self, value):
        self._combine_assemblies = value
        if hasattr(self, "reconstructor"):
            self.reconstructor.combine_assemblies = value

    @property
    def search_radius_mult(self):
        return self._search_radius_mult

    @search_radius_mult.setter
    def search_radius_mult(self, value):
        self._search_radius_mult = value
        if hasattr(self, "reconstructor"):
            self.reconstructor.search_radius_mult = value

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

    # ------------------------------------------------------------------
    # Node filters
    # ------------------------------------------------------------------
    @staticmethod
    def _hierarchy_contains_mesh(node: str) -> bool:
        """True if *node* has at least one non-intermediate mesh below it.

        Meshless transforms (cameras, lights, locators, empty groups) all
        produce identical empty hierarchy signatures and would otherwise be
        "instanced" into each other — i.e. deleted and replaced with empty
        transforms.
        """
        meshes = (
            cmds.listRelatives(node, allDescendents=True, type="mesh", fullPath=True)
            or []
        )
        return any(not NodeUtils.is_intermediate(m) for m in meshes)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    def run(self, nodes: Optional[Sequence[object]] = None) -> List[str]:
        """Discover and instance matching meshes.

        Operates on *nodes*, or the selection, or every transform in the
        scene (in that order of fallback). Returns the flat list of
        prototypes + created instances, plus the combined remainder meshes
        when ``combine_non_instanced`` is enabled. The entire run is one
        undo chunk.
        """
        if nodes is None:
            nodes = cmds.ls(selection=True, type="transform", long=True)
            if not nodes:
                nodes = cmds.ls(type="transform", long=True)

        cmds.undoInfo(openChunk=True, chunkName="AutoInstancer")
        try:
            return self._run([str(n) for n in nodes])
        finally:
            cmds.undoInfo(closeChunk=True)

    @staticmethod
    def _expand_transform_descendants(nodes: List[str]) -> List[str]:
        """Each node plus all its transform descendants, deduped, full paths."""
        out: List[str] = []
        seen = set()
        for node in nodes:
            if not cmds.objExists(node):
                continue
            root = _long_path(node)
            descendants = (
                cmds.listRelatives(
                    root, allDescendents=True, type="transform", fullPath=True
                )
                or []
            )
            for path in [root] + descendants:
                if path not in seen:
                    seen.add(path)
                    out.append(path)
        return out

    def _run(self, nodes: List[str]) -> List[str]:
        # ``check_hierarchy`` is derived per-run from the flow flags; the
        # instance attribute is user configuration and is never mutated here.
        check_hierarchy = self.check_hierarchy

        if self.separate_combined:
            # Combined meshes nested under selected groups must separate too —
            # the scene-wide default (no selection) already includes every
            # transform, so an explicit selection should behave the same.
            nodes = self._expand_transform_descendants(nodes)
            nodes = self.reconstructor.separate_combined_meshes(nodes)
            nodes = self.reconstructor.reassemble_assemblies(nodes)

            if self.combine_assemblies:
                nodes = self.reconstructor.combine_reassembled_assemblies(nodes)
                check_hierarchy = False
            else:
                check_hierarchy = True

            # Canonicalize leaf meshes AFTER reassembly so instancing can match
            # them.  Canonicalization absorbs translation/rotation into the
            # transform so PCA-aligned geometry compares equal — required for
            # the reassembly flow.  For plain leaf-instancing it's too
            # aggressive: it collapses frozen-vs-non-frozen and pivot-shifted
            # cubes into the same signature, instancing objects that the user
            # wants kept distinct.  Gate on the assembly flow only.
            nodes = self.reconstructor.canonicalize_leaf_meshes(nodes)

        groups = self.find_instance_groups(nodes, check_hierarchy=check_hierarchy)

        # Process groups whose MEMBERS sit shallowest first: members are what
        # get deleted, so an ancestor replacement (e.g. a whole assembly) must
        # run before groups matching its descendants — otherwise we build leaf
        # instances only for the ancestor pass to delete them. Keyed on
        # members, not the prototype: prototype selection (instanced-first)
        # can pick a prototype at a different depth than the members. Ties
        # break on natural name order, then full path, for a fully
        # deterministic processing order.
        def _group_depth(group: InstanceGroup) -> int:
            # ``InstanceCandidate.transform`` is already a full DAG path, so
            # the separator count IS the depth — no extra scene query needed.
            if group.members:
                return min(m.transform.count("|") for m in group.members)
            return group.prototype.transform.count("|")

        groups.sort(
            key=lambda g: (
                _group_depth(g),
                _natural_key(short_name(g.prototype.transform)),
                g.prototype.transform,
            )
        )

        # When the remainder-combine will run, micro duplicates defer to it
        # (merged beats instanced below the micro threshold) — EXCEPT
        # combined-assembly copies, which instance regardless of size.
        combine_will_run = (
            self.combine_non_instanced
            and self.strategy_config.is_static
            and not self.strategy_config.needs_individual
        )
        defer_micro_except: Optional[set] = None
        if combine_will_run:
            defer_micro_except = set()
            for uuid in getattr(self.reconstructor, "_combined_assembly_uuids", []):
                defer_micro_except.update(cmds.ls(uuid, long=True) or [])

        all_instances, report = self._process_groups(
            groups,
            allowed_strategies=_CONVERTIBLE_STRATEGIES,
            check_hierarchy=check_hierarchy,
            defer_micro_except=defer_micro_except,
        )

        # SECOND PASS: instance leaf geometry inside reconstructed assemblies
        # that did not match as whole hierarchies.
        if self.separate_combined and not self.combine_assemblies:
            self.logger.info("Running second pass: leaf geometry instancing")
            # Contents of freshly created instances are excluded — their leaf
            # shapes are already shared with the prototype's, so re-converting
            # them would only churn the just-built nodes. The prototypes'
            # descendants stay eligible so unmatched assemblies can still
            # pair against them.
            created = [n for entry in report for n in entry["instances"][1:]]
            leaf_candidates = self._collect_leaf_candidates(
                nodes, all_instances, created
            )
            leaf_groups = self.find_instance_groups(
                leaf_candidates, check_hierarchy=False
            )
            created, leaf_report = self._process_groups(
                leaf_groups,
                allowed_strategies=_CONVERTIBLE_STRATEGIES,
                check_hierarchy=False,
                defer_micro_except=defer_micro_except,
            )
            all_instances.extend(created)
            report.extend(leaf_report)

        self.reconstructor.cleanup_empty_sources()

        # Game-ready remainder: combine whatever did not become an instance.
        # Gated on the strategy flags — combining animated or individually
        # needed objects would destroy their independence.
        if combine_will_run:
            combined = self._combine_non_instanced(nodes)
            all_instances.extend(combined)
            self.reconstructor.cleanup_empty_assembly_groups()

        if self.verbose:
            self._log_report(report, len(groups))

        return all_instances

    def _combine_non_instanced(self, nodes: List[str]) -> List[str]:
        """polyUnite the non-instanced remainder into per-material clusters.

        Instances share their shape data and stay untouched; loose leftovers
        are combined via ``EditUtils.combine_objects`` — by material and by
        spatial cluster per the ``combine_by_material`` /
        ``combine_by_distance`` settings — to cut draw calls for a
        game-ready result. Assembly PRODUCTS are protected: a combined copy
        that merely failed to instance (a scaled variant, an imperfect CAD
        duplicate) and parts still parented under an assembly group are
        semantic units the user sorted for, not remainder.
        """
        protected = set()
        for uuid in getattr(self.reconstructor, "_combined_assembly_uuids", []):
            protected.update(cmds.ls(uuid, long=True) or [])
        assembly_roots = (
            cmds.ls(f"*.{ASSEMBLY_TAG_ATTR}", objectsOnly=True, long=True) or []
        )

        def under_assembly(path: str) -> bool:
            return any(path.startswith(root + "|") for root in assembly_roots)

        candidates = [
            t
            for t in self._collect_leaf_candidates(nodes, [])
            if not _is_instanced(t)
            and t not in protected
            and not under_assembly(t)
        ]
        # Locked/referenced nodes cannot be deleted by polyUnite's cleanup.
        uneditable = set(cmds.ls(candidates, lockedNodes=True, long=True) or [])
        uneditable.update(cmds.ls(candidates, referencedNodes=True, long=True) or [])
        candidates = [t for t in candidates if t not in uneditable]
        if len(candidates) < 2:
            return []

        from mayatk.edit_utils._edit_utils import EditUtils

        result = EditUtils.combine_objects(
            candidates,
            group_by_material=self.combine_by_material,
            cluster_by_distance=self.combine_by_distance,
            threshold=self.combine_distance_threshold,
        )
        if not result:
            return []
        combined = [_long_path(m) for m in ptk.make_iterable(result)]
        self.logger.info(
            "Combined %s non-instanced meshes into %s", len(candidates), len(combined)
        )
        return combined

    def _collect_leaf_candidates(
        self,
        nodes: List[str],
        processed: List[str],
        created: Sequence[str] = (),
    ) -> List[str]:
        """Mesh transforms derived from *nodes*, excluding *processed*.

        Scope is limited to the input node set and its descendants — the
        second pass must never touch scene content the caller didn't hand in.
        Nodes in *created* are excluded together with their descendants.
        All comparisons use full DAG paths.
        """
        processed_paths = set()
        for name in processed:
            processed_paths.update(cmds.ls(name, long=True) or [])
        for name in created:
            for path in cmds.ls(name, long=True) or []:
                processed_paths.update(
                    cmds.listRelatives(
                        path, allDescendents=True, type="transform", fullPath=True
                    )
                    or []
                )

        candidates: List[str] = []
        seen = set()
        for node in nodes:
            if not cmds.objExists(node):
                continue
            root = _long_path(node)
            descendants = (
                cmds.listRelatives(
                    root, allDescendents=True, type="transform", fullPath=True
                )
                or []
            )
            for transform in [root] + descendants:
                if transform in seen or transform in processed_paths:
                    continue
                seen.add(transform)
                if _mesh_shape(transform):
                    candidates.append(transform)
        return candidates

    # ------------------------------------------------------------------
    # Group discovery
    # ------------------------------------------------------------------
    def find_instance_groups(
        self,
        nodes: Optional[Sequence[object]] = None,
        check_hierarchy: Optional[bool] = None,
    ) -> List[InstanceGroup]:
        """Find groups of identical objects.

        ``check_hierarchy`` overrides the instance setting for this call
        (used internally to keep ``run()`` re-entrant).
        """
        if check_hierarchy is None:
            check_hierarchy = self.check_hierarchy

        # Geometry may have changed since the last discovery (separation,
        # canonicalization, prior conversions) — start from fresh caches.
        # The caches stay valid through the subsequent processing pass:
        # conversions delete/create nodes but never move surviving geometry.
        self.matcher.clear_cache()

        if nodes is None:
            nodes = cmds.ls(selection=True, type="transform", long=True)
            if not nodes:
                nodes = cmds.ls(type="transform", long=True)

        # Batch-resolve the locked/referenced exclusion sets in two ls calls
        # instead of two queries per node — deleting/reparenting such nodes
        # would fail mid-run.
        node_list = [str(n) for n in nodes]
        uneditable = set(cmds.ls(node_list, lockedNodes=True, long=True) or [])
        uneditable.update(cmds.ls(node_list, referencedNodes=True, long=True) or [])

        candidates = []
        seen_paths = set()
        for n in node_list:
            resolved = cmds.ls(n, long=True) or []
            if not resolved:  # vanished or unresolvable
                continue
            node_str = resolved[0]
            if node_str in seen_paths or node_str in uneditable:
                continue
            seen_paths.add(node_str)

            if check_hierarchy:
                if short_name(node_str) in _DEFAULT_CAMERAS:
                    continue
                if not self._hierarchy_contains_mesh(node_str):
                    continue
                candidates.append(InstanceCandidate(node_str))
            else:
                if _mesh_shape(node_str):
                    candidates.append(InstanceCandidate(node_str))

        # Group by signature
        signature_map = defaultdict(list)
        for candidate in candidates:
            if check_hierarchy:
                sig = self.matcher.get_hierarchy_signature(candidate.transform)
            else:
                sig = self.matcher.get_mesh_signature(candidate.transform)

            if sig:
                signature_map[sig].append(candidate)

        # Merge similar signatures if we are in combine mode
        if not check_hierarchy and self.combine_assemblies:
            signature_map = self._merge_similar_signatures(signature_map)

        if self.verbose:
            self.logger.debug(
                "Signature map: %s unique signatures", len(signature_map)
            )
            for sig, items in signature_map.items():
                self.logger.debug("  Sig %s: %s items", sig, len(items))

        # Every member is verified via _match_pair regardless of mode —
        # instancing replaces the member's geometry with the prototype's, so
        # exact identity (and the correct relative transform) is required.
        # Combine mode previously accepted whole signature buckets unverified
        # with relative_transform=None; copies whose canonical frames differed
        # by a symmetry spin/flip were blindly swapped, visibly rotating or
        # flip-shading the replaced geometry. Merged (near-identical) buckets
        # only widen the candidate pool a prototype is TRIED against.
        groups = []

        for sig, potential_matches in signature_map.items():
            potential_matches.sort(key=_prototype_preference_key)

            while potential_matches:
                prototype = potential_matches.pop(0)
                current_group = InstanceGroup(prototype)

                remaining_candidates = []
                for candidate in potential_matches:
                    if self._match_pair(prototype, candidate, check_hierarchy):
                        current_group.members.append(candidate)
                    else:
                        remaining_candidates.append(candidate)

                groups.append(current_group)
                potential_matches = remaining_candidates

        return groups

    def _match_pair(
        self,
        prototype: InstanceCandidate,
        candidate: InstanceCandidate,
        check_hierarchy: bool,
    ) -> bool:
        """Match *candidate* against *prototype*; stores the relative
        transform on the candidate when identical."""
        if check_hierarchy:
            is_identical, rel_mtx = self.matcher.are_hierarchies_identical(
                prototype.transform, candidate.transform, is_root=True
            )
        else:
            is_identical, rel_mtx = self.matcher.are_meshes_identical(
                prototype.transform, candidate.transform
            )
        if is_identical:
            # Overwrite even with None: a survivor re-matched against a
            # promoted prototype must not keep the transform it had relative
            # to the old, deleted prototype.
            candidate.relative_transform = rel_mtx
        return is_identical

    def _merge_similar_signatures(self, signature_map):
        """Merge signature buckets that are similar enough.

        Only buckets with identical material and UV signature components are
        merged — geometric similarity must never override
        ``require_same_material`` / ``check_uvs``.
        """
        sorted_keys = sorted(signature_map.keys(), key=lambda x: x[:3])

        merged_map = defaultdict(list)
        processed_sigs = set()

        for i, sig in enumerate(sorted_keys):
            if sig in processed_sigs:
                continue

            merged_map[sig].extend(signature_map[sig])
            processed_sigs.add(sig)

            topo = sig[:3]
            pca = sig[3]

            for j in range(i + 1, len(sorted_keys)):
                other_sig = sorted_keys[j]
                if other_sig in processed_sigs:
                    continue
                if other_sig[4:] != sig[4:]:  # materials / UV sets must match
                    continue

                o_pca = other_sig[3]

                if other_sig[:3] == topo:
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
                        self.logger.debug(
                            "Merging near-identical signature %s into %s "
                            "(topology differs; combine mode)",
                            other_sig[:3],
                            sig[:3],
                        )
                        merged_map[sig].extend(signature_map[other_sig])
                        processed_sigs.add(other_sig)

        return merged_map

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------
    def _process_groups(
        self,
        groups: List[InstanceGroup],
        allowed_strategies: Tuple[StrategyType, ...],
        check_hierarchy: bool,
        defer_micro_except: Optional[set] = None,
    ) -> Tuple[List[str], List[Dict[str, object]]]:
        """Strategy-gate and convert each group; returns (instances, report).

        ``defer_micro_except`` (a set of protected prototype paths, or
        ``None`` to disable) hands MICRO duplicates to the remainder-combine
        instead of instancing them: below ``MICRO_TRI_THRESHOLD`` the
        per-draw-call overhead of an instance costs more than the merged
        triangles (the reference results merge repeated micro tabs into the
        leftovers mesh). Combined-assembly prototypes are exempt — an
        assembly is a unit the user sorted for and instances regardless of
        size.
        """
        # In leaf mode a target's own children are not part of the matched
        # geometry and must be preserved; in hierarchy mode they ARE the
        # matched content and are replaced wholesale.
        preserve_children = not check_hierarchy
        all_instances: List[str] = []
        report: List[Dict[str, object]] = []

        for group in groups:
            if not group.members:
                continue

            # An earlier (shallower) group's processing may have deleted this
            # group's prototype or members (ancestor replacements).
            survivors = [m for m in group.members if m.exists()]
            if group.prototype.exists():
                group.members = survivors
            else:
                # The prototype sat inside a replaced ancestor, but copies
                # may survive elsewhere (e.g. inside the ancestor group's own
                # prototype, or standalone). Promote a survivor and re-match
                # the rest rather than losing the group.
                if len(survivors) < 2:
                    continue
                group = self._rebuild_group_from_survivors(
                    survivors, check_hierarchy
                )
            if not group.members:
                continue

            group_size = len(group.members) + 1
            strategy, tri_count = self._evaluate_group_strategy(group, group_size)

            if strategy not in allowed_strategies:
                if self.verbose:
                    self.logger.info(
                        "Skipping instancing for %s (Strategy: %s, Count: %s)",
                        group.prototype.transform,
                        strategy.name,
                        group_size,
                    )
                continue

            if (
                defer_micro_except is not None
                and strategy is StrategyType.COMBINE
                and tri_count < self.strategy_analyzer.MICRO_TRI_THRESHOLD
                and group.prototype.transform not in defer_micro_except
            ):
                if self.verbose:
                    self.logger.info(
                        "Deferring %s micro duplicates of %s (%s tris) to the "
                        "remainder combine",
                        group_size,
                        group.prototype.transform,
                        tri_count,
                    )
                continue

            created = self._convert_group_to_instances(group, preserve_children)
            if len(created) <= 1:
                continue
            all_instances.extend(created)
            report.append(
                {
                    "prototype": group.prototype.transform,
                    "instance_count": len(created) - 1,
                    "instances": created,
                }
            )

        return all_instances, report

    def _rebuild_group_from_survivors(
        self, survivors: List[InstanceCandidate], check_hierarchy: bool
    ) -> InstanceGroup:
        """Form a new group from *survivors* with a promoted prototype.

        Survivors matched the old (now deleted) prototype, so their stored
        relative transforms are stale — each is re-matched against the
        promoted prototype (mutual identity is expected but re-verified;
        tolerance is not exactly transitive).
        """
        survivors = sorted(survivors, key=_prototype_preference_key)
        prototype = survivors[0]
        group = InstanceGroup(prototype)
        self.logger.debug(
            "Prototype gone; promoted survivor %s", prototype.transform
        )
        for candidate in survivors[1:]:
            if self._match_pair(prototype, candidate, check_hierarchy):
                group.members.append(candidate)
        return group

    def _evaluate_group_strategy(
        self, group: InstanceGroup, group_size: int
    ) -> Tuple[StrategyType, int]:
        """Strategy + triangle count for a group's prototype."""
        prototype_transform = group.prototype.transform
        prototype_mesh = _mesh_shape(prototype_transform)

        tri_count = 0
        if prototype_mesh:
            meshes = [prototype_mesh]
        else:
            # It's an assembly/group — total the descendant triangle counts.
            meshes = (
                cmds.listRelatives(
                    prototype_transform,
                    allDescendents=True,
                    type="mesh",
                    fullPath=True,
                )
                or []
            )
        for m in meshes:
            if not NodeUtils.is_intermediate(m):
                try:
                    tri_count += int(cmds.polyEvaluate(m, triangle=True))
                except Exception:
                    pass
        return (
            self.strategy_analyzer.evaluate(group_size, triangle_count=tri_count),
            tri_count,
        )

    def _convert_group_to_instances(
        self, group: InstanceGroup, preserve_children: bool = True
    ) -> List[str]:
        """Convert all members of a group to instances of the prototype.

        A member that fails to convert is left untouched (its original node
        is only deleted after the replacement is fully assembled) and the
        remaining members are still processed.
        """
        if not group.prototype.exists():
            return []

        prototype_transform = group.prototype.transform
        if not group.members:
            return [prototype_transform]

        instances: List[str] = []
        for member in group.members:
            try:
                new_instance = self._replace_member_with_instance(
                    group.prototype, member, preserve_children
                )
            except Exception as e:
                self.logger.error(
                    "Failed to instance %s from prototype %s — original kept: %s",
                    member.transform,
                    prototype_transform,
                    e,
                )
                continue
            if new_instance:
                instances.append(new_instance)

        return [group.prototype.transform] + instances

    def _replace_member_with_instance(
        self,
        prototype: InstanceCandidate,
        member: InstanceCandidate,
        preserve_children: bool,
    ) -> Optional[str]:
        """Replace *member* with an instance of *prototype*.

        Order of operations is deliberate: the replacement is fully built and
        verified before the original is deleted. On any failure the partial
        replacement nodes are removed and the original is left untouched.
        """
        if not member.exists():
            return None

        target = member.transform
        proto_path = prototype.transform
        target_name = short_name(target)

        # Refuse overlapping hierarchies — instancing a node into its own
        # ancestor/descendant chain creates DAG cycles.
        if (
            target == proto_path
            or target.startswith(proto_path + "|")
            or proto_path.startswith(target + "|")
        ):
            self.logger.warning(
                "Skipping %s: overlaps prototype hierarchy %s", target, proto_path
            )
            return None

        new_instance = None
        temp_instance = None
        try:
            # 1. Duplicate the target transform (world placement carrier).
            new_instance = _long_path(cmds.duplicate(target, parentOnly=True)[0])

            # Apply the shape-correction transform found by the matcher.
            rel_mtx = member.relative_transform
            if rel_mtx is not None:
                # Order: rel_mtx (shape correction) * target_matrix (placement)
                target_matrix = get_object_matrix(new_instance, world=True)
                set_object_matrix(new_instance, rel_mtx * target_matrix, world=True)

            # 2. Instance the prototype and move its contents across.
            temp_instance = _long_path(cmds.instance(proto_path, leaf=True)[0])
            children = (
                cmds.listRelatives(temp_instance, children=True, fullPath=True) or []
            )
            for child in children:
                if cmds.ls(child, shapes=True):
                    cmds.parent(child, new_instance, shape=True, relative=True)
                else:
                    cmds.parent(child, new_instance, relative=True)
        except Exception:
            # Discard the partial replacement; the original is untouched.
            for node in (temp_instance, new_instance):
                if node and cmds.objExists(node):
                    try:
                        cmds.delete(node)
                    except Exception:
                        pass
            raise

        # 3. Replacement verified — now it is safe to swap.
        if cmds.objExists(temp_instance):
            cmds.delete(temp_instance)

        # Preserve the target's own children in leaf mode (they are not part
        # of the matched geometry). If they can't be moved out, abort the
        # swap — deleting the target would take them with it.
        if preserve_children:
            target_children = NodeUtils.get_children(
                target, type="transform", full_path=True
            )
            if target_children:
                try:
                    cmds.parent(target_children, world=True)
                except Exception:
                    if cmds.objExists(new_instance):
                        cmds.delete(new_instance)
                    raise

        new_uuid = (cmds.ls(new_instance, uuid=True) or [None])[0]
        cmds.delete(target)
        renamed = cmds.rename(new_instance, target_name)
        if new_uuid:
            return (cmds.ls(new_uuid, long=True) or [renamed])[0]
        return renamed

    def _log_report(self, report: List[Dict[str, object]], group_count: int) -> None:
        total_instances = sum(entry["instance_count"] for entry in report)
        self.logger.info(
            "AutoInstancer processed %s groups and created %s instances",
            group_count,
            total_instances,
        )
        for entry in report:
            self.logger.info(
                " - %s → %s instances", entry["prototype"], entry["instance_count"]
            )


def auto_instance(
    nodes: Optional[Sequence[object]] = None,
    tolerance: float = 0.001,
    scale_tolerance: Optional[float] = None,
    require_same_material: Union[bool, int] = True,
    check_uvs: bool = False,
    check_hierarchy: bool = False,
    separate_combined: bool = False,
    combine_assemblies: bool = True,
    combine_non_instanced: bool = True,
    combine_by_material: bool = True,
    combine_by_distance: bool = True,
    combine_distance_threshold: float = 10000.0,
    search_radius_mult: float = 1.5,
    is_static: bool = True,
    needs_individual: bool = False,
    will_be_lightmapped: bool = False,
    can_gpu_instance: bool = True,
    verbose: bool = True,
    log_level: str = "WARNING",
) -> List[str]:
    """Find and convert geometrically identical meshes into instances.

    One-shot convenience wrapper around :class:`AutoInstancer` — mirrors
    ``replace_with_instances``/``get_instances``/``uninstance``. See
    ``AutoInstancer.__init__``/``run`` for parameter details.
    """
    instancer = AutoInstancer(
        tolerance=tolerance,
        scale_tolerance=scale_tolerance,
        require_same_material=require_same_material,
        check_uvs=check_uvs,
        check_hierarchy=check_hierarchy,
        separate_combined=separate_combined,
        combine_assemblies=combine_assemblies,
        combine_non_instanced=combine_non_instanced,
        combine_by_material=combine_by_material,
        combine_by_distance=combine_by_distance,
        combine_distance_threshold=combine_distance_threshold,
        search_radius_mult=search_radius_mult,
        is_static=is_static,
        needs_individual=needs_individual,
        will_be_lightmapped=will_be_lightmapped,
        can_gpu_instance=can_gpu_instance,
        verbose=verbose,
        log_level=log_level,
    )
    return instancer.run(nodes)


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
