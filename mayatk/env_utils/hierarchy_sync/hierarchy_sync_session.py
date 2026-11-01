# !/usr/bin/python
# coding=utf-8
"""Hierarchy Sync's host half: the reference a diff runs against, held in the
scene, and every scene operation the Hierarchy Sync panel performs.

The panel is tentacle's shared tool (``slots/tools/hierarchy_sync.py``), one
class for both hosts: it holds a :class:`HierarchySyncSession`
(``mtk.HierarchySyncSession``) and calls only this API; blendertk's
``HierarchySyncSession`` answers the same calls for Blender. In Maya the
reference is imported into a temporary namespace
(:class:`~mayatk.env_utils.namespace_sandbox.NamespaceSandbox`) and kept there,
cached by path and file time, until it is released; a tree node is named by its
DAG path (a string).
"""

import contextlib
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import maya.cmds as cmds
except Exception:
    cmds = None
import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.env_utils.namespace_sandbox import NamespaceSandbox
from mayatk.env_utils.hierarchy_sync._hierarchy_sync import (
    HierarchySync,
    MayaObjectMatcher,
    ObjectSwapper,
)


class HierarchySyncSession(ptk.LoggingMixin):
    """The reference held for a diff, the last analysis, and the scene calls
    the Hierarchy Sync panel makes.

    Attributes:
        hierarchy_sync: The engine of the last analysis (``None`` before one).
        diff_result: The last analysis' differences (``None`` before one).
    """

    #: Globs the current scene's Open Scene dialog offers: Maya's own scene
    #: formats plus FBX, which Maya opens as a scene. The reference browser is
    #: a different contract: :meth:`reference_formats`.
    SCENE_FILE_TYPES = (*EnvUtils.SCENE_FILE_TYPES, "*.fbx")

    def __init__(self):
        super().__init__()
        self.hierarchy_sync = None
        self.diff_result = None
        # {"path", "mtime", "sandbox", "transforms"} of the held reference.
        self._cached_reference_import = None
        # The namespaces the held reference came in under (kept out of the
        # current scene's nodes).
        self._reference_namespaces: List[str] = []

    @classmethod
    def loggers(cls) -> list:
        """The loggers this session's work reports through: its own and its
        engines' (a panel redirects them into its log)."""
        return [
            cls.logger,
            HierarchySync.logger,
            ObjectSwapper.logger,
            MayaObjectMatcher.logger,
            NamespaceSandbox.logger,
        ]

    @staticmethod
    @contextlib.contextmanager
    def _keeping_sandbox_log_level():
        """Keep ``NamespaceSandbox``'s log level across a construction: its
        ``__init__`` resets the class logger's level, which would quiet the
        level a panel redirected it at (:meth:`loggers`)."""
        level = NamespaceSandbox.logger.level
        try:
            yield
        finally:
            NamespaceSandbox.logger.setLevel(level)

    @property
    def fuzzy_matching(self) -> bool:
        """Whether the last analysis matched names fuzzily."""
        return bool(self.hierarchy_sync and self.hierarchy_sync.fuzzy_matching)

    # ------------------------------------------------------------------ reference
    def is_loaded(self, reference_path: str) -> bool:
        """Whether *reference_path* is the reference this session holds."""
        if not reference_path or self._cached_reference_import is None:
            return False
        resolved = str(Path(reference_path).resolve())
        return self._cached_reference_import.get("path") == resolved

    def load_reference(self, reference_path: str) -> Optional[List[str]]:
        """Import *reference_path* into a temporary namespace, or reuse the
        import already held for it.

        The import is kept (path, file time, sandbox, transforms) so the tree
        and the diff share one import; a file changed on disk, or an import
        whose nodes are gone, is imported again.

        Returns:
            The reference's transforms (Maya's default cameras left out), or
            ``None`` on failure.
        """
        resolved = str(Path(reference_path).resolve())

        if self._cached_reference_import is not None:
            cached_path = self._cached_reference_import.get("path")
            cached_transforms = self._cached_reference_import.get("transforms", [])
            if cached_path == resolved and cached_transforms:
                try:
                    cached_mtime = self._cached_reference_import.get("mtime")
                    current_mtime = os.path.getmtime(resolved)
                    if cached_mtime is not None and current_mtime != cached_mtime:
                        self.logger.notice(
                            "Reference file changed on disk, re-importing"
                        )
                    elif cmds.objExists(str(cached_transforms[0])):
                        self.logger.debug(
                            f"Reusing cached reference import ({len(cached_transforms)} transforms)"
                        )
                        return cached_transforms
                except Exception:
                    pass
                # Stale cache -- fall through to a fresh import.
                self.logger.debug("Cached reference import is stale, re-importing")

        self.release_reference()

        self.logger.progress(f"Importing reference: {os.path.basename(reference_path)}")

        with self._keeping_sandbox_log_level():
            sandbox = NamespaceSandbox(dry_run=False)
        import_info = sandbox.import_with_namespace(
            reference_path, force_complete_import=True
        )
        if not import_info or not import_info.get("transforms"):
            self.logger.error("Failed to import reference file or no transforms found")
            return None

        all_transforms = import_info.get("transforms", [])
        filtered = [t for t in all_transforms if not self._is_default_camera(t)]
        self.logger.result(
            f"Imported {len(all_transforms)} transforms from reference "
            f"({len(all_transforms) - len(filtered)} default cameras excluded)"
        )

        self._cached_reference_import = {
            "path": resolved,
            "mtime": os.path.getmtime(resolved),
            "sandbox": sandbox,
            "transforms": filtered,
        }
        self._reference_namespaces = sorted(
            {
                str(t).split("|")[-1].split(":")[0]
                for t in all_transforms
                if ":" in str(t).split("|")[-1]
            }
        )
        if self._reference_namespaces:
            self.logger.debug(
                "Tracking reference namespaces: "
                + ", ".join(self._reference_namespaces)
            )
        return filtered

    def release_reference(self) -> None:
        """Remove the held reference import (its namespace and nodes)."""
        if self._cached_reference_import is None:
            return
        sandbox = self._cached_reference_import.get("sandbox")
        if sandbox is not None:
            try:
                sandbox.cleanup_all_namespaces()
            except Exception as e:
                self.logger.debug(f"Cached reference cleanup failed: {e}")
        self._cached_reference_import = None
        self._sweep_temp_namespaces()

    def _sweep_temp_namespaces(self) -> None:
        """Delete every remaining temporary import namespace and its nodes."""
        if cmds is None:
            return
        try:
            all_namespaces = (
                cmds.namespaceInfo(listOnlyNamespaces=True, recurse=True) or []
            )
            temp = [
                ns
                for ns in all_namespaces
                if NamespaceSandbox.TEMP_NAMESPACE_PREFIX in ns
            ]
            for namespace in temp:
                try:
                    nodes = cmds.ls(f"{namespace}:*")
                    if nodes:
                        cmds.delete(nodes)
                    if cmds.namespace(exists=namespace):
                        cmds.namespace(
                            removeNamespace=namespace, mergeNamespaceWithRoot=True
                        )
                        self.logger.debug(f"Removed temp namespace: {namespace}")
                except Exception as ns_error:
                    self.logger.debug(
                        f"Could not remove namespace {namespace}: {ns_error}"
                    )
            self._reference_namespaces = []
        except Exception as cleanup_error:
            self.logger.debug(f"Temp namespace cleanup failed: {cleanup_error}")

    @staticmethod
    def _is_default_camera(transform) -> bool:
        """Whether *transform* is one of Maya's default cameras."""
        try:
            node_name = str(transform).split("|")[-1]
            return HierarchySync.is_default_maya_camera(node_name, transform)
        except Exception:
            return False

    # ------------------------------------------------------------------ analysis
    def analyze(
        self,
        reference_path: str,
        fuzzy_matching: bool = True,
        dry_run: bool = True,
        filter_meshes: bool = False,
        filter_cameras: bool = False,
        filter_lights: bool = False,
    ) -> bool:
        """Diff the whole scene against *reference_path* (loaded or reused).

        Parameters:
            reference_path: The reference scene.
            fuzzy_matching: Match renamed nodes by name similarity.
            dry_run: The repairs this analysis feeds report without editing.
            filter_meshes: Leave mesh-bearing transforms out of the diff.
            filter_cameras: Leave every camera out (the default cameras are
                always left out).
            filter_lights: Leave every light out.

        Returns:
            True if the analysis produced a result (:attr:`diff_result`).
        """
        if not reference_path:
            self.logger.error("Please specify a reference scene path.")
            return False
        if not os.path.exists(reference_path):
            self.logger.error(f"Reference scene does not exist: {reference_path}")
            return False

        try:
            self.logger.progress(
                f"Analyzing hierarchy differences with: {os.path.basename(reference_path)}"
            )
            reference_nodes = self.load_reference(reference_path)
            if not reference_nodes:
                self.logger.error(
                    "Failed to import reference file or no transforms found"
                )
                return False

            sandbox = (self._cached_reference_import or {}).get("sandbox")
            self.hierarchy_sync = HierarchySync(
                import_manager=sandbox,
                fuzzy_matching=fuzzy_matching,
                dry_run=dry_run,
            )
            self.diff_result = self.hierarchy_sync.analyze_hierarchies(
                current_tree_root="SCENE_WIDE_MODE",
                reference_objects=reference_nodes,
                filter_meshes=filter_meshes,
                filter_cameras=filter_cameras,
                filter_lights=filter_lights,
            )
            if not self.diff_result:
                self.logger.warning("Analysis returned no results")
                return False
            return True

        except Exception as e:
            self.logger.error(f"Error during hierarchy analysis: {e}")
            if "Failed to resolve source or target roots" in str(e):
                self.logger.error(
                    "Analysis failed because no valid objects could be found for comparison."
                )
            self.diff_result = None
            return False

    def clear_analysis(self) -> None:
        """Drop the last analysis and release the reference."""
        self.hierarchy_sync = None
        self.diff_result = None
        self._reference_namespaces = []
        self.release_reference()
        self.logger.debug("Analysis cache cleared")

    def repair(
        self,
        differences: Optional[Dict[str, list]] = None,
        create_stubs: bool = True,
        quarantine_extras: bool = True,
        quarantine_group: str = "_QUARANTINE",
        skip_animated: bool = True,
        fix_reparented: bool = True,
        fix_fuzzy_renames: bool = True,
        dry_run: bool = True,
    ) -> bool:
        """Repair the scene toward the reference, from the last analysis.

        Renames run first (a renamed parent makes its children resolvable, so a
        stub never claims the name and Maya never auto-suffixes it), then
        stubs, then reparents (before quarantine: quarantining an extra parent
        moves its subtree and would strand a reparented child inside it), then
        quarantine. A live run is one undo chunk, and it leaves the analysis
        stale, so it clears it (:meth:`clear_analysis`).

        Parameters:
            differences: The differences to repair -- the panel's, its ignored
                paths left out; defaults to the whole :attr:`diff_result`.
            create_stubs: Create empty placeholders for missing nodes.
            quarantine_extras: Move extra nodes under *quarantine_group*.
            quarantine_group: The quarantine group's name.
            skip_animated: Leave a node whose motion would change.
            fix_reparented: Move reparented nodes to their reference parent.
            fix_fuzzy_renames: Rename fuzzy-matched nodes to their reference names.
            dry_run: Report what would change, without changing it.

        Returns:
            True if anything was (or, dry, would be) repaired.
        """
        if not self.hierarchy_sync or not self.diff_result:
            self.logger.error("Please run a diff analysis first.")
            return False

        source = differences if differences is not None else self.diff_result
        effective = {
            key: list(source.get(key, []))
            for key in ("missing", "extra", "reparented", "fuzzy_matches")
        }

        prev_dry_run = self.hierarchy_sync.dry_run
        self.hierarchy_sync.dry_run = dry_run
        chunk = (
            contextlib.nullcontext()
            if dry_run
            else CoreUtils.undo_chunk("Repair Hierarchies")
        )
        results = {}
        try:
            with chunk:
                if fix_fuzzy_renames and effective["fuzzy_matches"]:
                    self.logger.progress(
                        f"Renaming {len(effective['fuzzy_matches'])} fuzzy-matched items..."
                    )
                    results["renamed"] = self.hierarchy_sync.fix_fuzzy_renames(
                        effective["fuzzy_matches"], skip_animated=skip_animated
                    )
                    if results["renamed"]:
                        self._prune_resolved_by_renames(effective)

                if create_stubs and effective["missing"]:
                    self.logger.progress(
                        f"Creating stubs for {len(effective['missing'])} missing items..."
                    )
                    results["stubs"] = self.hierarchy_sync.create_stubs(
                        effective["missing"]
                    )

                if fix_reparented and effective["reparented"]:
                    self.logger.progress(
                        f"Fixing {len(effective['reparented'])} reparented items..."
                    )
                    results["reparented"] = self.hierarchy_sync.fix_reparented(
                        effective["reparented"], skip_animated=skip_animated
                    )

                if quarantine_extras and effective["extra"]:
                    self.logger.progress(
                        f"Quarantining {len(effective['extra'])} extra items..."
                    )
                    results["quarantined"] = self.hierarchy_sync.quarantine_extras(
                        group=quarantine_group,
                        paths=effective["extra"],
                        skip_animated=skip_animated,
                    )
        finally:
            self.hierarchy_sync.dry_run = prev_dry_run

        if not sum(len(v) for v in results.values()):
            self.logger.notice("No repairs needed or no applicable items found.")
            return False

        mode = "DRY-RUN" if dry_run else "APPLIED"
        parts = [f"{key}: {len(items)}" for key, items in results.items() if items]
        self.logger.result(f"[{mode}] Repairs — {', '.join(parts)}")

        if not dry_run:
            self.clear_analysis()
        return True

    @staticmethod
    def _prune_resolved_by_renames(effective: Dict[str, list]) -> None:
        """Drop the extras and missing nodes a fuzzy rename resolved: the
        renamed parents' children now sit under the right name."""
        fuzzy = effective["fuzzy_matches"]
        cur_prefixes = [f["current_name"] for f in fuzzy if f.get("current_name")]
        ref_prefixes = [f["target_name"] for f in fuzzy if f.get("target_name")]
        effective["extra"] = [
            p
            for p in effective["extra"]
            if not any(p.startswith(prefix + "|") for prefix in cur_prefixes)
        ]
        effective["missing"] = [
            p
            for p in effective["missing"]
            if not any(p.startswith(prefix + "|") for prefix in ref_prefixes)
        ]

    def pull(
        self,
        targets: Sequence[str],
        reference_path: str,
        fuzzy_matching: bool = True,
        dry_run: bool = True,
        pull_children: bool = False,
        pull_mode: str = "Add to Scene",
    ) -> bool:
        """Bring reference nodes into the current scene.

        The reference is imported again into its own namespace, the matched
        nodes are integrated (``ObjectSwapper``) and every temporary namespace
        is swept after. A live run is one undo chunk, and it leaves the
        analysis stale, so it clears it (:meth:`clear_analysis`).

        Parameters:
            targets: The reference nodes to pull, by the name the panel's tree
                gives them (:meth:`node_path`).
            reference_path: The reference scene.
            fuzzy_matching: Match a target by name similarity when its exact
                name is not found.
            dry_run: Report what would be pulled, without pulling it.
            pull_children: Pull each target's whole subtree.
            pull_mode: ``"Add to Scene"`` or ``"Merge Hierarchies"`` (replace
                the node already at the target's path).

        Returns:
            True if the pull succeeded.
        """
        targets = [t for t in targets if t]
        if not targets:
            self.logger.error("No objects specified for pulling.")
            return False
        if not reference_path or not os.path.exists(reference_path):
            self.logger.error("Please specify a valid reference scene path.")
            return False

        chunk = (
            contextlib.nullcontext()
            if dry_run
            else CoreUtils.undo_chunk("Pull Objects")
        )
        try:
            with chunk:
                with self._keeping_sandbox_log_level():
                    swapper = ObjectSwapper(
                        dry_run=dry_run,
                        fuzzy_matching=fuzzy_matching,
                        pull_mode=pull_mode,
                        pull_children=pull_children,
                    )
                success = swapper.pull_objects_from_scene(
                    target_objects=targets, source_file=reference_path, backup=True
                )
                self._log_pull(
                    targets, reference_path, success, pull_mode, fuzzy_matching, dry_run
                )
        except Exception as e:
            self.logger.error(f"Error pulling objects: {e}")
            return False
        if not success:
            self.logger.error("Failed to pull objects from reference scene.")
            return False

        try:
            cmds.refresh()
        except Exception:
            pass
        self._verify_pull(targets, pull_children)
        self._sweep_temp_namespaces()
        if not dry_run:
            self.clear_analysis()
        return True

    def _log_pull(
        self,
        targets: List[str],
        reference_path: str,
        success: bool,
        pull_mode: str,
        fuzzy_matching: bool,
        dry_run: bool,
    ) -> None:
        """Log a pull's summary table and the nodes it asked for."""
        self.logger.log_divider()
        self.log_table(
            data=[
                ["Source", Path(reference_path).name],
                ["Requested", f"{len(targets)} objects"],
                ["Pull Mode", pull_mode],
                ["Fuzzy Matching", "Enabled" if fuzzy_matching else "Disabled"],
                ["Dry Run", "Yes" if dry_run else "No"],
                ["Status", "SUCCESS" if success else "FAILED"],
            ],
            headers=["Setting", "Value"],
            title="PULL OPERATION",
        )
        shown = list(targets[:10])
        if len(targets) > 10:
            shown.append(f"... and {len(targets) - 10} more")
        self.logger.log_box("REQUESTED OBJECTS", shown)
        if not success:
            self.logger.error("Pull operation failed — check logs for details")
        elif dry_run:
            self.logger.notice("Dry run - no actual changes made")
        else:
            self.logger.result(f"Objects integrated using '{pull_mode}' mode")
        self.logger.log_divider()

    def _verify_pull(self, targets: List[str], pull_children: bool) -> None:
        """Log which pulled targets now exist in the scene."""
        self.logger.progress("Verifying pulled objects exist in the scene...")
        if pull_children:
            found = {
                HierarchySync.get_clean_node_name_from_string(t)
                for t in targets
                if cmds.objExists(HierarchySync.get_clean_node_name_from_string(t))
            }
            self.logger.debug(
                f"Pull verification: {len(found)}/{len(targets)} requested objects now exist"
            )
            return
        rows = []
        for target in targets:
            clean = HierarchySync.get_clean_node_name_from_string(target)
            node = clean if cmds.objExists(clean) else target
            if not cmds.objExists(node):
                rows.append([target, "MISSING", "-"])
                self.logger.error(f"{target} (or {clean}) does not exist after pull!")
                continue
            children = cmds.listRelatives(node, children=True, type="transform") or []
            rows.append([node, "OK", str(len(children))])
        if rows:
            self.log_table(
                data=rows,
                headers=["Object", "Status", "Children"],
                title="PULL VERIFICATION",
            )

    # ------------------------------------------------------------------ the scene
    def current_scene_nodes(self) -> List[str]:
        """The current scene's transforms the panel's tree shows: no default
        camera, no temporary import, nothing from the held reference."""
        if cmds is None:
            return []
        nodes = []
        skipped = 0
        for transform in cmds.ls(type="transform", long=True) or []:
            name = transform.split("|")[-1]
            if (
                self._is_default_camera(transform)
                or (NamespaceSandbox.TEMP_NAMESPACE_PREFIX in name and ":" in name)
                or (":" in name and name.split(":")[0] in self._reference_namespaces)
            ):
                skipped += 1
                continue
            nodes.append(transform)
        self.logger.debug(
            f"Current scene: {len(nodes)} transforms ({skipped} cameras/imports left out)"
        )
        return nodes

    @staticmethod
    def hierarchy_structure(nodes) -> Tuple[Dict[str, dict], List[str]]:
        """The tree a panel draws from *nodes*.

        Returns:
            ``({key: info}, root keys)``: each key is the node's DAG long path;
            ``info`` holds ``object`` (the node, what a tree item carries),
            ``short_name`` (its name, namespace kept), ``display_name`` (its
            name without the namespace), ``node_name`` (what ``NodeIcons``
            resolves it by), ``type`` and ``parent`` (the parent's key, or
            ``None``).
        """
        items: Dict[str, dict] = {}
        roots: List[str] = []
        if cmds is None:
            return items, roots
        for node in nodes:
            try:
                key = cmds.ls(str(node), long=True)[0]
                name = key.rsplit("|", 1)[-1]
                parent = cmds.listRelatives(key, parent=True, fullPath=True)
                items[key] = {
                    "object": node,
                    "short_name": name,
                    "display_name": name.split(":")[-1],
                    "node_name": key,
                    "type": cmds.nodeType(key),
                    "parent": parent[0] if parent else None,
                }
                if not parent:
                    roots.append(key)
            except Exception:
                continue
        return items, roots

    @staticmethod
    def clean_path(path: str) -> str:
        """*path* as the diff compares it: each component's namespace dropped."""
        return HierarchySync.clean_hierarchy_path(path)

    @staticmethod
    def node_path(node) -> str:
        """The name a pull or a selection takes *node* by: its DAG path."""
        return str(node) if node else ""

    @staticmethod
    def node_name(node) -> str:
        """*node*'s name: its DAG path's leaf, namespace dropped."""
        return str(node).split("|")[-1].split(":")[-1] if node else ""

    @staticmethod
    def node_alive(node) -> bool:
        """Whether *node* (a tree item's node) names a node in the scene."""
        if cmds is None or not isinstance(node, str) or not node:
            return False
        try:
            return bool(cmds.objExists(node))
        except Exception:
            return False

    def rename_nodes(self, pairs: Sequence[Tuple[Any, str]]) -> List[Optional[str]]:
        """Rename each ``(node, new_name)`` pair, as one undo chunk.

        Deepest first, so a parent's rename never stales the path a child is
        held by. A dead node, an empty name or the name it already has is
        skipped; a failure is logged.

        Returns:
            Per pair, in order: the renamed node (its new DAG path), or
            ``None`` when it was not renamed.
        """
        results: List[Optional[str]] = [None] * len(pairs)
        order = sorted(
            range(len(pairs)),
            key=lambda i: self.node_path(pairs[i][0]).count("|"),
            reverse=True,
        )
        with CoreUtils.undo_chunk("Hierarchy Sync: Rename"):
            for i in order:
                node, new_name = pairs[i]
                if not new_name or not self.node_alive(node):
                    continue
                old_name = self.node_name(node)
                if new_name == old_name:
                    continue
                try:
                    new_path = cmds.rename(node, new_name)
                except Exception as e:
                    self.logger.error(f"Failed to rename '{node}': {e}")
                    continue
                self.logger.info(f"Renamed '{old_name}' → '{self.node_name(new_path)}'")
                results[i] = new_path
        return results

    def reparent_nodes(self, moves: Sequence[Tuple[Any, Any]]) -> int:
        """Reparent each ``(node, parent)`` move (``parent=None``: to the
        world), as one undo chunk.

        Moves are resolved by UUID up front, since moving one node stales the
        DAG path of any other moved node beneath it. A move already in place
        (a same-parent reorder) is skipped; a node with animation is warned
        about (its motion may change), not refused; a locked stub is unlocked
        for the move and locked again.

        Returns:
            How many moves were attempted (a failed one is logged).
        """
        if cmds is None:
            return 0
        pending = []  # (node uuid, parent uuid or None for the world, label)
        for node, parent in moves:
            if not self.node_alive(node):
                continue
            node_uuid = (cmds.ls(node, uuid=True) or [None])[0]
            if not node_uuid:
                continue
            cur_parent = (
                cmds.listRelatives(node, parent=True, fullPath=True) or [None]
            )[0]
            if parent is None:
                if cur_parent is not None:
                    pending.append((node_uuid, None, node))
                continue
            if not self.node_alive(parent):
                self.logger.warning("Drop target has no scene node — reparent skipped.")
                continue
            if (
                cur_parent is not None
                and cur_parent == (cmds.ls(parent, long=True) or [None])[0]
            ):
                continue
            parent_uuid = (cmds.ls(parent, uuid=True) or [None])[0]
            pending.append((node_uuid, parent_uuid, node))
        if not pending:
            return 0

        with CoreUtils.undo_chunk("Drag Reparent"):
            for node_uuid, parent_uuid, label in pending:
                node = (cmds.ls(node_uuid, long=True) or [None])[0]
                if not node:
                    continue
                try:
                    if HierarchySync._has_animation_data(node):
                        self.logger.warning(
                            f"'{node.split('|')[-1]}' has animation — its "
                            "motion may change under the new parent."
                        )
                    HierarchySync._unlock_if_stub(node)
                    if parent_uuid is None:
                        new_path = (cmds.parent(node, world=True) or [node])[0]
                        self.logger.info(f"Reparented '{label}' to world")
                    else:
                        parent = (cmds.ls(parent_uuid, long=True) or [None])[0]
                        if not parent:
                            continue
                        new_path = (cmds.parent(node, parent) or [node])[0]
                        self.logger.info(
                            f"Reparented '{label}' under '{parent.split('|')[-1]}'"
                        )
                    HierarchySync._relock_if_stub(new_path)
                except Exception as e:
                    self.logger.error(f"Reparent failed for '{label}': {e}")
        return len(pending)

    def delete_nodes(self, nodes: Sequence[Any]) -> List[str]:
        """Delete *nodes* (and so their subtrees), as one undo chunk.

        Deepest first; a locked stub is unlocked so it cannot fail the batch.

        Returns:
            The deleted nodes. Raises what the delete raises.
        """
        live = [n for n in nodes if self.node_alive(n)]
        if not live:
            return []

        def _depth(node):
            try:
                return len((cmds.ls(node, long=True) or [node])[0].split("|"))
            except Exception:
                return 0

        live.sort(key=_depth, reverse=True)
        with CoreUtils.undo_chunk("Delete Selected Objects"):
            for node in live:
                HierarchySync._unlock_if_stub(node)
            cmds.delete(live)
        return live

    @staticmethod
    def select_nodes(nodes: Sequence[Any]) -> int:
        """Select *nodes* in the scene (an ambiguous name is skipped).

        Returns:
            How many were selected.
        """
        return HierarchySync.select_objects_in_maya([str(n) for n in nodes if n])

    @staticmethod
    def scene_path() -> str:
        """The open scene's file, or ``""`` (unsaved, or no Maya)."""
        if cmds is None:
            return ""
        return cmds.file(q=True, sceneName=True) or ""

    @staticmethod
    def workspace() -> str:
        """The current project's root, or ``""``."""
        if cmds is None:
            return ""
        try:
            return EnvUtils.get_env_info("workspace") or ""
        except Exception:
            return ""

    @staticmethod
    def recent_scenes() -> List[str]:
        """The recently opened scenes, most recent first."""
        if cmds is None:
            return []
        return EnvUtils.get_recent_files() or []

    @staticmethod
    def open_scene(path: str) -> None:
        """Open *path* as the current scene (unsaved changes discarded, as
        Maya's own Open does when forced). Raises ``RuntimeError`` on failure."""
        cmds.file(path, open=True, force=True)

    @staticmethod
    def reference_formats() -> List[str]:
        """The extensions a reference may have (what the sandbox imports)."""
        return NamespaceSandbox.get_supported_formats()


# --------------------------------------------------------------------------------------------

if __name__ == "__main__":
    ...

# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------
