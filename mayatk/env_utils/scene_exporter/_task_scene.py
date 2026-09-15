# !/usr/bin/python
# coding=utf-8
"""Scene-level export tasks: the workspace and working unit, the export-set
filters, name hygiene and the sheared-chain flatten (with the shear scan its
paired check shares).
"""

import os
import math
import logging
from typing import Any, Optional, Dict, List, Tuple

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:  # the surface must import without Maya (registry, docs tooling)
    cmds = mel = None
import pythontk as ptk

# From this package:
from mayatk.core_utils.diagnostics.scene_diag import SceneDiagnostics
from mayatk.anim_utils._anim_utils import AnimUtils
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.env_utils.scene_exporter._task_data import _TaskDataMixin


class _SceneTasksMixin(_TaskDataMixin):
    """Scene-level export tasks: the workspace and working unit, the export-set
    filters, name hygiene and the sheared-chain flatten (with the shear scan its paired check shares)."""

    def set_workspace(self, enable=True):
        """Switch to the workspace matching the scene path, and align the
        process working directory with it, for the export write.

        Two staged mutations, one purpose \u2014 make write-time path resolution
        match Maya's:

        - **Workspace**: how Maya (and the checks) resolve project-relative
          texture paths.
        - **Process CWD**: how the fbxmaya plugin locates those textures when
          it WRITES \u2014 plain OS resolution against the working directory; the
          workspace is never consulted (probe-proven 2026-08-04: with a
          correct workspace and a foreign CWD the plugin silently drops every
          relative texture from the embed \u2014 "The following texture(s) will
          not be embedded" \u2014 while with a foreign workspace and the CWD at
          the project root, embedding succeeds).

        **Staged**: both mutations must still be applied when the FBX is
        written, so the restore rides ``TaskFactory.stage_deferred_restore``
        and the exporter unwinds it after the write.
        """
        original_workspace = cmds.workspace(query=True, rootDirectory=True)

        if enable:
            new_workspace = EnvUtils.find_workspace_using_path()
            if new_workspace and new_workspace != original_workspace:
                self.stage_deferred_restore(
                    "workspace", lambda: self._restore_workspace(original_workspace)
                )
                cmds.workspace(new_workspace, openWorkspace=True)
                self.logger.debug(
                    f"Changed workspace from {original_workspace} to {new_workspace}"
                )
            elif not new_workspace:
                self.logger.warning(
                    "No workspace.mel found in scene path hierarchy "
                    f"\u2014 using current workspace: {original_workspace}"
                )
            else:
                self.logger.debug("Workspace already matches scene path.")

            # Align the process CWD with the (now) active workspace root \u2014
            # even when the workspace itself needed no switch, the CWD can
            # still be foreign (GUI Maya never chdirs on Set Project).
            ws_root = cmds.workspace(query=True, rootDirectory=True)
            original_cwd = os.getcwd()
            if (
                ws_root
                and os.path.isdir(ws_root)
                and os.path.normcase(os.path.normpath(original_cwd))
                != os.path.normcase(os.path.normpath(ws_root))
            ):
                self.stage_deferred_restore("cwd", lambda: os.chdir(original_cwd))
                os.chdir(ws_root)
                self.logger.debug(
                    "Aligned process working directory with the workspace root "
                    f"for the FBX write: {original_cwd} -> {ws_root}"
                )

        return None

    def _restore_workspace(self, original):
        cmds.workspace(original, openWorkspace=True)
        self.logger.debug(f"Reverted workspace to: {original}")

    def set_linear_unit(self, linear_unit):
        """Set Maya's working linear unit for the export.

        **Staged** -- same reason as :meth:`set_workspace`: the FBX plugin
        stamps the file's unit from the working unit at WRITE time (proven:
        exporting the same cube under ``cm`` vs ``m`` yields different files),
        so the restore is deferred past the write.
        """
        original_linear_unit = cmds.currentUnit(query=True, linear=True)

        if linear_unit and linear_unit != "OFF":
            self.stage_deferred_restore(
                "linear_unit", lambda: self._restore_linear_unit(original_linear_unit)
            )
            cmds.currentUnit(linear=linear_unit)
            self.logger.debug(
                f"Changed linear unit from {original_linear_unit} to {linear_unit}"
            )
        else:
            self.logger.debug(f"Linear unit change skipped (value: {linear_unit})")

        return None

    def _restore_linear_unit(self, original):
        cmds.currentUnit(linear=original)
        self.logger.debug(f"Reverted linear unit to: {original}")

    def conform_shape_names(self):
        """Repair scratch/mangled names in the export set, then conform shapes.

        Delegates to ``SceneDiagnostics.repair_mangled_names``: cleans
        mangled TRANSFORM (and other non-shape) names — accumulated
        ``__uninst_tmp`` tokens, ``__RZTMP`` suffixes, FBXASC escapes,
        underscore runs — then conforms shapes to ``<transform>Shape``.
        Shape-only conforming could never clear ``check_mangled_names``
        (which scans all descendants), leaving the check failing after
        every repair pass.  Permanent scene improvement (deliberately not
        reverted after export).
        """
        # `or []` — a None/empty export set must stay a no-op; passing None
        # through would repair the WHOLE scene.
        objects = [str(o) for o in (self.objects or [])]
        # Snapshot UUIDs FIRST: a rename invalidates the stored DAG path, and
        # nothing else in the pipeline re-derives it.  Every later cmds call
        # over self.objects — and cmds.select() for the export itself — would
        # then die on the stale path (or, if smart_bake's cmds.ls refresh ran
        # first, silently drop the renamed node from the FBX).  Resolved one
        # at a time so the snapshot stays positionally aligned with `objects`
        # (a bulk cmds.ls silently drops an unresolvable name and expands an
        # ambiguous one, which would shift every pairing after it).
        uuids = [(cmds.ls(o, uuid=True) or [None])[0] for o in objects]

        result = SceneDiagnostics.repair_mangled_names(objects)
        if result["renamed"]:
            self.logger.info(f"Repaired {len(result['renamed'])} mangled node name(s).")
        if result["shapes_conformed"]:
            self.logger.info(f"Conformed {result['shapes_conformed']} shape name(s).")
        # Re-derive on EITHER outcome. Conforming a shape to `<transform>Shape`
        # renames it just as surely as repairing a mangled transform does, and
        # a shape sitting in the export set then holds a path that no longer
        # resolves -- with nothing else re-deriving it. Measured in a
        # production export: `..._settings_CTRL_xzShape` was conformed here and
        # `smart_bake` died on the stale path eleven tasks later, aborting the
        # run after two minutes of texture work.
        if result["renamed"] or result["shapes_conformed"]:
            self.objects = self._repath_renamed(objects, uuids)
            self.record_kept_edit("repaired node and shape names")

    def _shear_scan_nodes(self) -> List[str]:
        """Export-set transforms plus every joint under them.

        The scan set shared by the shear check and the flatten task: skin
        influences carry the geometry, but any animated transform loses the
        same way, so the whole export set is included.
        """
        objects = self._live_objects()
        if not objects:
            return []
        nodes = set(cmds.ls(objects, type="transform", long=True) or [])
        if nodes:  # one descendant walk for the whole set, not one per node
            nodes.update(
                cmds.listRelatives(
                    list(nodes), allDescendents=True, type="joint", fullPath=True
                )
                or []
            )
        return sorted(nodes)

    def flatten_sheared_chains(self, tolerance: float = 0.05) -> tuple:
        """Flatten transforms whose parent-relative matrices shear -- the
        auto-fix for what :meth:`check_sheared_local_transforms` detects.

        FBX and glTF store an animated node as translate/rotate/scale; a
        sheared parent-relative matrix has no TRS form, so the shear is
        dropped and the residual compounds down a chain. The flagged nodes'
        WORLD matrices are orthogonal (that is the squash/stretch shape --
        see the check), so relative to a nearest *similarity* ancestor each
        local is exactly TRS-representable. Detection is the shared
        :meth:`_sheared_offenders` scan (coarse grid everywhere plus every
        frame over scale-dynamic candidates) and a flagged joint pulls in
        its whole chain -- half-flattened chains leak sub-tolerance shear
        from every remaining link.

        The transform is a WORLD-FITTED BAKE, not a live rewrap: every
        planned node's world matrix is sampled per frame from the UNTOUCHED
        scene first, then the node is reparented under its target with
        fitted TRS keys written and its offsetParentMatrix, TRS drivers,
        segmentScaleCompensate and joint orients neutralised (all recorded).
        Two disproven alternatives, both shipped and measured on the production
        wire looms: leaving the live offsetParentMatrix rewrap for FBX
        freezes it whenever its upstream does not translate to FBX; and
        keeping it for smart_bake to sample later still fails because an IK
        solver writes the joints' locals FROM the chain's parent structure
        -- the reparent changes the solve itself (15.9 cm exactly during
        the IK-active shot, with direct jumps equal to sequential
        evaluation). Sampling before any mutation is correct by
        construction. A staged deferred restore puts hierarchy, wiring and
        values back after the write.

        Returns:
            tuple: (True, messages) -- a task, not a check: it repairs
            rather than blocks. Nodes with no clean ancestor are left in
            place for the check to report.
        """
        if tolerance is True:
            # The pre-dial QCheckBox hands this its RAW value, and
            # True == 1.0 — the loosest possible cosine tolerance, which
            # would make the task silently fix nothing (same idiom as
            # check_duplicate_names' pre-dial True).
            tolerance = 0.05
        log_messages: List[str] = []
        if not tolerance:
            return True, log_messages
        offenders = self._sheared_offenders(tolerance)
        # What this run's scan found and what became of it, for the shear
        # check to read instead of scanning again (_sheared_offenders_after_flatten).
        verdict: Dict[str, Any] = {
            "tolerance": tolerance,
            "offenders": {},
            "flattened": [],
            "unplaced": [],
            "failed": [],
        }
        self._shear_verdict = verdict
        if not offenders:
            return True, log_messages
        offenders = self._expand_chain_offenders(offenders)
        verdict["offenders"] = dict(offenders)

        frames = self._shear_dense_frames() or self._shear_sample_frames()
        if not frames:
            # The set-scoped key query can answer empty while the members
            # still MOVE -- their driver keys live outside the export set
            # (measured: an ikHandle parented at world level). The playback
            # animation range is the outermost statement of intent.
            start, end = (int(f) for f in AnimUtils.scene_animation_range())
            if end > start:
                frames = [float(f) for f in range(start, end + 1)]
            else:
                frames = [float(cmds.currentTime(query=True))]
        qualifies = self._similarity_ancestors(offenders, frames, tolerance)
        # The scan above may stride past its sample cap; the BAKE below may
        # not. A world-fitted key every OTHER frame leaves the frames between
        # to interpolation, and a fast-moving basis does not interpolate:
        # measured on PROPS_ASSEMBLY (3436 frames, so stride 2), the flattened
        # looms were exact to 1e-13 on the frames sampled and up to 2.0 of
        # world-basis error on the frames between -- the whole of the residual
        # a smart bake was being blamed for.
        bake_frames = self._shear_dense_frames(max_samples=None) or frames
        # Paths go stale the moment the first offender moves, so resolve
        # every later one by UUID; same for the export set itself.
        object_uuids = [(cmds.ls(o, uuid=True) or [None])[0] for o in self.objects]

        plan: List[Tuple[str, str, str, bool]] = []  # (path, uuid, target, reparent)
        unplaced: List[str] = []
        for path in sorted(offenders, key=lambda p: p.count("|")):
            uuid = (cmds.ls(path, uuid=True) or [None])[0]
            if not uuid:
                continue
            target = self._flatten_target(path, qualifies)
            current_parent = (
                cmds.listRelatives(path, parent=True, fullPath=True) or [None]
            )[0]
            if not target:
                unplaced.append(path)
                continue
            if target == current_parent:
                # Chain-complete expansion reaches members already sitting
                # under the target (chain roots). They still need the bake:
                # a solver keeps writing their locals live, against a chain
                # whose other members are about to leave -- so key them in
                # place from the same pristine samples, without reparenting.
                plan.append((path, uuid, target, False))
                continue
            plan.append((path, uuid, target, True))

        if plan:
            # The flatten edits animation -- fitted curves, cut TRS drivers --
            # so like every key task it protects the scene's curves FIRST, and
            # before its own restore is staged: that restore then runs first
            # (LIFO) and deletes its fitted curves while they are still the
            # nodes it made. Left to the first key task after it, the snapshot
            # stashed those dense curves too (17 s of a production export,
            # 2026-09-14) for a restore that deletes them anyway -- in
            # write-back mode too, so the flatten keeps no key edit.
            self._protect_scene_animation(keeps_edits=False)
        records: List[dict] = []
        # Staged BEFORE the first node moves: the lambda closes over the live
        # list, so an exception mid-loop still restores every node already
        # flattened when the export's finally runs the deferred restores.
        self.stage_deferred_restore(
            "flatten_sheared_chains",
            lambda recs=records: self._restore_flattened(recs),
        )
        per_target: Dict[str, int] = {}
        failed: List[str] = []
        # IK handle census BEFORE any mutation: a handle whose chain loses
        # members to the reparent reports an empty jointList afterwards.
        planned_paths = {path for path, _, _, _ in plan}
        handle_chains: Dict[str, set] = {}
        if plan:
            for handle in cmds.ls(type="ikHandle", long=True) or []:
                chain = set(
                    cmds.ls(
                        cmds.ikHandle(handle, query=True, jointList=True) or [],
                        long=True,
                    )
                )
                if chain.intersection(planned_paths):
                    handle_chains[handle] = chain

        baked_uuids: List[str] = []
        baked_paths: set = set()
        if plan:
            samples = self._sample_flatten_locals(plan, bake_frames)
            for path, uuid, target, reparent in plan:
                node = (cmds.ls(uuid, long=True) or [None])[0]
                if not node or (path, target) not in samples:
                    continue
                try:
                    records.append(
                        self._flatten_bake_node(
                            node,
                            target,
                            bake_frames,
                            samples[(path, target)],
                            reparent=reparent,
                        )
                    )
                    baked_uuids.append(uuid)
                    baked_paths.add(path)
                except RuntimeError as e:
                    # A locked or referenced node refuses the reparent --
                    # leave it for the check to report rather than aborting
                    # the export.
                    failed.append(path)
                    self.logger.warning(f"Could not flatten '{node}': {e}")
                    continue
                per_target[target] = per_target.get(target, 0) + 1

        if baked_uuids:
            # An IK solver writes its joints' locals with NO plug
            # connections, so cutting connections does not detach it -- and
            # a handle whose chain lost members to the reparent now solves
            # a different rig. Disable every handle whose PRE-move chain
            # touched a node that actually BAKED (a handle serving only
            # failed/skipped nodes keeps solving; recorded; the restore
            # re-enables it).
            for handle, chain in handle_chains.items():
                if not chain.intersection(baked_paths):
                    continue
                try:
                    prior = cmds.getAttr(f"{handle}.ikBlend")
                    cmds.setAttr(f"{handle}.ikBlend", 0.0)
                except RuntimeError as e:
                    self.logger.warning(f"Could not disable IK handle '{handle}': {e}")
                    continue
                records.append(
                    {
                        "mode": "ikblend",
                        "handle": (cmds.ls(handle, uuid=True) or [None])[0],
                        "value": prior,
                    }
                )

        verdict["flattened"] = list(baked_uuids)
        verdict["unplaced"] = list(unplaced)
        verdict["failed"] = list(failed)
        if records:
            self.objects = self._repath_renamed(self.objects, object_uuids)
            log_messages.append(
                f"Flattened {len(records)} transform(s) whose parent-relative "
                f"matrices shear (> {tolerance:g}): reparented under a clean "
                f"ancestor with world-fitted TRS keys baked at {len(bake_frames)} "
                "frame(s) sampled from the untouched scene. The hierarchy, "
                "wiring and values are restored after the write:"
            )
            for target, count in sorted(per_target.items(), key=lambda kv: -kv[1]):
                log_messages.append(
                    f"    {count} node(s) -> {target.rsplit('|', 1)[-1]}"
                )
        if unplaced:
            log_messages.append(
                f"    {len(unplaced)} sheared node(s) have no similarity "
                "ancestor to flatten under and were left in place (the shear "
                "check will report them):"
            )
            for path in unplaced[:5]:
                log_messages.append(f"        {path.rsplit('|', 1)[-1]}")
        if failed:
            log_messages.append(
                f"    {len(failed)} sheared node(s) refused the reparent "
                "(locked or referenced) and were left in place:"
            )
            for path in failed[:5]:
                log_messages.append(f"        {path.rsplit('|', 1)[-1]}")
        return True, log_messages

    def _similarity_ancestors(
        self, offenders, frames, tolerance: float
    ) -> Dict[str, bool]:
        """``{ancestor path: qualifies}`` for every ancestor of *offenders*.

        A qualifying flatten target is a similarity transform at every
        sampled frame: orthogonal world axes AND uniform axis lengths --
        relative to such a node, an orthogonal world matrix decomposes to
        TRS exactly. A zero-scale sample frame disqualifies a candidate:
        the rewrap references its worldInverseMatrix, which a degenerate
        matrix cannot supply. Precomputed in one time pass over all
        candidates so the flatten loop never touches the timeline.
        """
        from mayatk.core_utils.diagnostics.transform_diag import (
            TransformDiagnostics,
        )

        candidates = set()
        for node in offenders:
            parts = node.split("|")
            for i in range(2, len(parts)):
                candidates.add("|".join(parts[:i]))
        verdict = {c: True for c in candidates}
        if not candidates:
            return verdict

        import maya.api.OpenMaya as om2

        # DAG paths resolved ONCE; the per-frame read is the same world matrix
        # ``xform -q -ws -m`` returns, without a command per node per frame. A
        # path that no longer resolves cannot be a flatten target.
        dag_paths = {}
        selection = om2.MSelectionList()
        for candidate in candidates:
            try:
                selection.clear()
                selection.add(candidate)
                dag_paths[candidate] = selection.getDagPath(0)
            except RuntimeError:
                verdict[candidate] = False

        restore_time = cmds.currentTime(query=True)
        try:
            for frame in list(frames) if frames else [None]:
                if frame is not None:
                    cmds.currentTime(frame)
                for candidate, ok in verdict.items():
                    if not ok:
                        continue
                    world = dag_paths[candidate].inclusiveMatrix()
                    m = [world[i] for i in range(16)]
                    axes = (m[0:3], m[4:7], m[8:11])
                    lengths = [math.sqrt(sum(v * v for v in a)) for a in axes]
                    longest = max(lengths)
                    if longest < 1e-6:
                        # Degenerate at this frame: it can't be judged AND the
                        # rewrap can't invert it -- disqualify outright. (Maya
                        # hides via .visibility, which never touches scale; a
                        # zero here is a scale-keyed pop-in.)
                        verdict[candidate] = False
                        continue
                    if (longest - min(lengths)) / longest > 0.01:
                        verdict[candidate] = False
                        continue
                    if TransformDiagnostics._matrix_skew(m) > tolerance:
                        verdict[candidate] = False
        finally:
            cmds.currentTime(restore_time)
        return verdict

    @staticmethod
    def _flatten_target(node: str, qualifies: Dict[str, bool]) -> Optional[str]:
        """Deepest qualifying ancestor of *node* (by its pre-flatten path).

        Nearest-first keeps the node inside its own rig group -- and inside
        any visibility-toggled subtree above it, so an animated hide keeps
        applying to it exactly as before.
        """
        parts = node.split("|")
        for i in range(len(parts) - 1, 1, -1):
            candidate = "|".join(parts[:i])
            if qualifies.get(candidate):
                return candidate
        return None

    #: TRS channels the baked flatten writes, in xform order.
    _FLATTEN_CHANNELS = (
        ("translateX", "TL"),
        ("translateY", "TL"),
        ("translateZ", "TL"),
        ("rotateX", "TA"),
        ("rotateY", "TA"),
        ("rotateZ", "TA"),
        ("scaleX", "TU"),
        ("scaleY", "TU"),
        ("scaleZ", "TU"),
    )

    def _sample_flatten_locals(
        self, plan: List[Tuple[str, str, str, bool]], frames: List[float]
    ) -> Dict[Tuple[str, str], List[List[float]]]:
        """``{(node, target): [[tx..sz] per frame]}`` from the UNTOUCHED scene.

        One timeline pass for the whole plan (currentTime is the expensive
        step). Sampling BEFORE any mutation is the load-bearing choice: an
        IK solver computes the joints' locals from the chain's parent
        structure, so any post-reparent evaluation answers a different rig.
        Rotations are unwound against the previous frame so the keyed euler
        curves stay continuous.
        """
        import math as _math

        import maya.api.OpenMaya as om2

        targets = sorted({t for _, _, t, _ in plan})
        out: Dict[Tuple[str, str], List[List[float]]] = {
            (p, t): [] for p, _, t, _ in plan
        }
        prev_euler: Dict[str, List[float]] = {}
        orders: Dict[str, int] = {}
        for path, _, _, _ in plan:
            orders[path] = cmds.getAttr(f"{path}.rotateOrder")
        # DAG paths resolved once: ``inclusiveMatrix`` is the same
        # ``worldMatrix[0]`` evaluation, without a getAttr + 16-float list per
        # node per frame (that read was ~100 s of a 3.4k-frame production
        # flatten). Nothing moves until every sample is taken, so the paths
        # stay valid for the whole pass.
        dag_paths: Dict[str, "om2.MDagPath"] = {}
        selection = om2.MSelectionList()
        for node in set(targets) | {p for p, _, _, _ in plan}:
            selection.clear()
            selection.add(node)
            dag_paths[node] = selection.getDagPath(0)
        restore_time = cmds.currentTime(query=True)
        try:
            for frame in frames:
                cmds.currentTime(frame)
                inverses = {t: dag_paths[t].inclusiveMatrixInverse() for t in targets}
                for path, _, target, _ in plan:
                    world = dag_paths[path].inclusiveMatrix()
                    local = world * inverses[target]
                    xf = om2.MTransformationMatrix(local)
                    t3 = xf.translation(om2.MSpace.kWorld)
                    euler = xf.rotation(asQuaternion=True).asEulerRotation()
                    euler = euler.reorder(orders[path])
                    cur = [euler.x, euler.y, euler.z]
                    prev = prev_euler.get(path)
                    if prev is not None:
                        for i in range(3):
                            while cur[i] - prev[i] > _math.pi:
                                cur[i] -= 2.0 * _math.pi
                            while prev[i] - cur[i] > _math.pi:
                                cur[i] += 2.0 * _math.pi
                    prev_euler[path] = cur
                    s3 = xf.scale(om2.MSpace.kWorld)
                    out[(path, target)].append(
                        [t3.x, t3.y, t3.z, cur[0], cur[1], cur[2], *s3]
                    )
        finally:
            cmds.currentTime(restore_time)
        return out

    def _flatten_bake_node(
        self,
        node: str,
        target: str,
        frames: List[float],
        rows: List[List[float]],
        reparent: bool = True,
    ) -> dict:
        """Reparent *node* under *target* and key the pre-sampled locals.

        Neutralises everything that would fight the keys: TRS driver
        connections are cut (recorded), offsetParentMatrix is reset to
        identity (source/value recorded), and on joints the orient/axis/
        segmentScaleCompensate are zeroed so the keyed rotate IS the local
        rotation. Returns the restore record for :meth:`_restore_flattened`.
        """
        import maya.api.OpenMaya as om2
        import maya.api.OpenMayaAnim as oma2

        record: dict = {
            "mode": "baked",
            "reparented": reparent,
            "node": (cmds.ls(node, uuid=True) or [None])[0],
            "old_parent": None,
            "opm_source": None,
            "opm_value": None,
            "cut": [],
            "originals": {},
            "curves": [],
            "joint": {},
        }
        parent = (cmds.listRelatives(node, parent=True, fullPath=True) or [None])[0]
        record["old_parent"] = (cmds.ls(parent, uuid=True) or [None])[0]

        opm_plug = f"{node}.offsetParentMatrix"
        record["opm_source"] = (
            cmds.listConnections(opm_plug, source=True, destination=False, plugs=True)
            or [None]
        )[0]
        record["opm_value"] = cmds.getAttr(opm_plug)

        for attr, _ in self._FLATTEN_CHANNELS:
            plug = f"{node}.{attr}"
            src_plug = (
                cmds.listConnections(plug, source=True, destination=False, plugs=True)
                or [None]
            )[0]
            if src_plug:
                record["cut"].append([src_plug, attr])
            else:
                record["originals"][attr] = cmds.getAttr(plug)
        for attr in ("shearXY", "shearXZ", "shearYZ"):
            record["originals"][attr] = cmds.getAttr(f"{node}.{attr}")
        if cmds.attributeQuery("jointOrient", node=node, exists=True):
            record["joint"] = {
                "jointOrient": list(cmds.getAttr(f"{node}.jointOrient")[0]),
                "rotateAxis": list(cmds.getAttr(f"{node}.rotateAxis")[0]),
                "segmentScaleCompensate": cmds.getAttr(
                    f"{node}.segmentScaleCompensate"
                ),
            }

        # The reparent is the one call expected to refuse (locked/referenced);
        # everything before it was read-only, so a raise there leaves the
        # scene untouched for this node. Anything failing AFTER it rolls the
        # node back through its own record -- a moved node without a record
        # would be invisible to the deferred restore.
        # relative=True: absolute parenting inserts a compensating
        # 'transform1' buffer above a joint whenever jointOrient cannot
        # absorb the move -- and the fitted keys are relative to TARGET,
        # not target x buffer. Local values are overwritten by the keys.
        if reparent:
            moved = cmds.parent(node, target, relative=True)[0]
            node = (cmds.ls(moved, long=True) or [moved])[0]
        try:
            for src_plug, attr in record["cut"]:
                try:
                    cmds.disconnectAttr(src_plug, f"{node}.{attr}")
                except RuntimeError:
                    pass
            if record["opm_source"]:
                cmds.disconnectAttr(record["opm_source"], f"{node}.offsetParentMatrix")
            cmds.setAttr(
                f"{node}.offsetParentMatrix",
                [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                type="matrix",
            )
            if record["joint"]:
                cmds.setAttr(f"{node}.jointOrient", 0, 0, 0)
                cmds.setAttr(f"{node}.rotateAxis", 0, 0, 0)
                cmds.setAttr(f"{node}.segmentScaleCompensate", False)
            cmds.setAttr(f"{node}.shear", 0, 0, 0)

            sel = om2.MSelectionList()
            sel.add(node)
            dep = om2.MFnDependencyNode(sel.getDependNode(0))
            time_array = om2.MTimeArray()
            unit = om2.MTime.uiUnit()
            for frame in frames:
                time_array.append(om2.MTime(frame, unit))
            kinds = {
                "TL": oma2.MFnAnimCurve.kAnimCurveTL,
                "TA": oma2.MFnAnimCurve.kAnimCurveTA,
                "TU": oma2.MFnAnimCurve.kAnimCurveTU,
            }
            for column, (attr, kind) in enumerate(self._FLATTEN_CHANNELS):
                values = om2.MDoubleArray()
                for row in rows:
                    values.append(row[column])
                fn = oma2.MFnAnimCurve()
                fn.create(dep.findPlug(attr, False), kinds[kind])
                fn.addKeys(
                    time_array,
                    values,
                    oma2.MFnAnimCurve.kTangentLinear,
                    oma2.MFnAnimCurve.kTangentLinear,
                )
                record["curves"].append((cmds.ls(fn.name(), uuid=True) or [None])[0])
        except Exception as e:
            self._restore_baked_flatten(record)
            raise RuntimeError(f"flatten bake failed mid-mutation: {e}")
        return record

    def _restore_flattened(self, records: List[dict]) -> None:
        """Deferred restore: reverse every flatten record (LIFO)."""
        restored = 0
        for record in reversed(records):
            try:
                if self._restore_baked_flatten(record):
                    restored += 1
            except RuntimeError as e:
                self.logger.warning(f"Flatten restore failed for one node: {e}")
        if restored:
            self.logger.info(
                f"Restored {restored} flattened transform(s) to their original "
                "parents -- the flatten was staged for the write only."
            )

    def _restore_baked_flatten(self, record: dict) -> bool:
        """Reverse one :meth:`_flatten_bake_node` record."""

        def _resolve(uuid):
            return (cmds.ls(uuid, long=True) or [None])[0] if uuid else None

        if record.get("mode") == "ikblend":
            handle = _resolve(record.get("handle"))
            if handle:
                try:
                    cmds.setAttr(f"{handle}.ikBlend", record.get("value", 1.0))
                except RuntimeError:
                    return False
                return True
            return False
        node = _resolve(record.get("node"))
        old_parent = _resolve(record.get("old_parent"))
        for curve_uuid in record.get("curves", []):
            curve = _resolve(curve_uuid)
            if curve:
                cmds.delete(curve)
        if not node or not old_parent:
            return False
        if record.get("reparented", True):
            # relative=True for the same buffer-insertion reason as the
            # bake; the recorded values/wiring restore the true local.
            moved = cmds.parent(node, old_parent, relative=True)[0]
            node = (cmds.ls(moved, long=True) or [moved])[0]
        joint = record.get("joint") or {}
        if joint:
            cmds.setAttr(f"{node}.jointOrient", *joint["jointOrient"])
            cmds.setAttr(f"{node}.rotateAxis", *joint["rotateAxis"])
            cmds.setAttr(
                f"{node}.segmentScaleCompensate",
                joint["segmentScaleCompensate"],
            )
        for attr, value in (record.get("originals") or {}).items():
            try:
                cmds.setAttr(f"{node}.{attr}", value)
            except RuntimeError:
                pass
        for src_plug, attr in record.get("cut", []):
            try:
                cmds.connectAttr(src_plug, f"{node}.{attr}", force=True)
            except RuntimeError:
                pass
        opm_plug = f"{node}.offsetParentMatrix"
        if record.get("opm_source"):
            try:
                cmds.connectAttr(record["opm_source"], opm_plug, force=True)
            except RuntimeError:
                pass
        elif record.get("opm_value") is not None:
            cmds.setAttr(opm_plug, record["opm_value"], type="matrix")
        return True

    def ignore_groups(self, names: str, case_sensitive: bool = False) -> None:
        """Exclude top-level groups matching *names* and all their descendants
        from the export object list.

        Parameters:
            names: Comma-separated group name patterns to exclude (e.g.
                ``"temp, proxy"``). Each entry is a shell-style glob, so
                ``"temp*"`` catches ``temp_01``/``tempRig`` and ``"*_proxy"``
                catches ``hull_proxy``. A pattern with no wildcard character
                still matches only that exact name, as before.
            case_sensitive: Match names exactly. Off by default, so ``"temp"``
                catches ``TEMP``. The UI arms it from the Ignore row's option-box
                toggle; a headless caller passes the pair as the dict the task
                dispatcher unpacks -- ``{"names": "Temp", "case_sensitive": True}``
                -- while a bare string still selects the insensitive default.
        """
        if not self.objects or not names:
            return

        # Parse comma-separated patterns. The parse stays here rather than being
        # handed to ``filter_list`` as a raw string because an all-whitespace
        # field must return early: ``filter_list`` with no patterns is a no-op
        # that returns the list unfiltered, which here would mean matching --
        # and so excluding -- every root.
        patterns = ptk.split_delimited_string(
            str(names), delimiter=",", strip_whitespace=True, remove_empty=True
        )
        if not patterns:
            return

        # self.objects contains only geometry transforms (never assemblies),
        # so derive each object's root ancestor from its long DAG path. Unlike
        # check_root_default_transforms (which requires len(parts) > 2 AND gates
        # on NodeUtils.is_group), this uses len(parts) > 1 and no is_group gate,
        # so it also matches a top-level *ungrouped* node whose short name equals
        # a target — intentional here (a target need not be a group), but note
        # the boundaries deliberately differ.
        root_groups = set()
        for obj in self.objects:
            parts = obj.split("|")  # "|root|...|geo" — root is segment [1]
            if len(parts) > 1:
                root_groups.add("|" + parts[1])

        # Find top-level groups whose short name matches any pattern. The glob,
        # the case fold and the pattern list all live in ``filter_list``, so the
        # match rules stay identical here, in blendertk's mirror of this task,
        # and in every other filter field in the ecosystem. ``map_func`` reduces
        # the long DAG path to its short name for matching while the filter
        # still returns the full paths.
        root_nodes = cmds.ls(list(root_groups), long=True) or []
        matched_roots = ptk.filter_list(
            root_nodes,
            inc=patterns,
            map_func=lambda n: n.split("|")[-1],
            ignore_case=not case_sensitive,
        )

        if not matched_roots:
            self.logger.debug(f"No top-level groups matching {patterns} found.")
            return

        # Gather the matched roots and all their descendants
        exclude = set(matched_roots)
        for root in matched_roots:
            descendants = (
                cmds.listRelatives(root, allDescendents=True, fullPath=True) or []
            )
            exclude.update(descendants)

        original_count = len(self.objects)
        self.objects = [obj for obj in self.objects if obj not in exclude]
        removed = original_count - len(self.objects)

        # ONE grouped record with the summary as its title — a line per group
        # plus a separate total rendered as N+1 blank-line-separated sections.
        # (``matched_roots`` is non-empty here: the early return above covers
        # the no-match case, which logs at debug.)
        if self.logger.isEnabledFor(logging.INFO):
            self.logger.log_group(
                f"Excluded {removed} object(s) under "
                f"{len(matched_roots)} group(s) from export",
                list(matched_roots),
            )

    def exclude_hdr(self) -> None:
        """Remove Arnold HDR environment lights (``aiSkyDomeLight``) from the export set.

        The HDR skydome is image-based scene lighting, not deliverable
        geometry, so it should not ride into a game-engine FBX. In the
        'All Scene Objects' mode the skydome transform is otherwise picked up
        by ``cmds.ls(transforms=True)``; this strips the skydome transform(s)
        and their shapes back out of ``self.objects``.

        A no-op when mtoa is unloaded (no skydome can exist) or the export set
        contains none.
        """
        if not self.objects:
            return

        # Guard the plugin first: querying ``cmds.ls(type="aiSkyDomeLight")``
        # for an unregistered type emits an "Unknown object type" warning, and
        # without mtoa loaded no skydome can exist anyway.
        if not EnvUtils.is_plugin_loaded("mtoa"):
            return

        skydomes = cmds.ls(type="aiSkyDomeLight", long=True) or []
        if not skydomes:
            return

        exclude = set()
        for shape in skydomes:
            exclude.add(shape)
            exclude.update(cmds.listRelatives(shape, parent=True, fullPath=True) or [])

        original_count = len(self.objects)
        self.objects = [obj for obj in self.objects if obj not in exclude]
        removed = original_count - len(self.objects)
        if removed:
            self.logger.info(
                f"Excluded {removed} HDR environment node(s) (aiSkyDomeLight) from export."
            )
        else:
            self.logger.debug("No HDR skydome in the export set — nothing to exclude.")

    def _shear_sample_frames(self, limit: int = 5) -> List[float]:
        """COARSE grid: *limit* frames spread across the scene's keyed range.

        Catches static shear and anything sheared most of the time. It is NOT
        sufficient alone: production wire looms sheared only inside Shot_2/3
        (f146-250 of 1818) and this grid never landed there -- the dense pass
        over :meth:`_shear_candidates` covers the gaps. Returns an empty list
        for a static scene, which the diagnostic reads as "current frame
        only".
        """
        span = self._keyframe_range()
        if not span:
            return []
        start, end = float(span[0]), float(span[1])
        if end <= start:
            return [start]
        step = (end - start) / (limit - 1)
        return [start + step * i for i in range(limit)]

    def _shear_dense_frames(self, max_samples: Optional[int] = 2000) -> List[float]:
        """Every integer frame across the keyed range (strided past the cap).

        The coarse grid ships broken rigs -- a shear spike between its
        samples is invisible (measured: wire looms sheared only during their
        own shot). Dense scanning is affordable because it only runs over
        :meth:`_shear_candidates`, the small set whose scale can actually
        change over time.

        Parameters:
            max_samples: Cap on the number of frames; the range is strided to
                fit. None asks for EVERY integer frame -- what a bake needs,
                as opposed to a scan, since the frames a strided bake skips
                are left to interpolation.
        """
        span = self._keyframe_range()
        if not span:
            return []
        start = int(math.floor(span[0]))
        end = int(math.ceil(span[1]))
        if end <= start:
            return [float(start)]
        span = end - start
        stride = 1 if not max_samples else max(1, -(-span // max_samples))
        frames = [float(f) for f in range(start, end + 1, stride)]
        if frames[-1] != float(end):
            frames.append(float(end))
        return frames

    def _shear_candidates(self, nodes: List[str]) -> List[str]:
        """The subset of *nodes* whose parent-relative matrix can shear over
        TIME: nodes with a connection-driven scale or offsetParentMatrix on
        themselves or any ancestor.

        Static non-uniform scale shears identically at every frame -- the
        coarse grid already sees it. Only a scale that CHANGES (driven scale
        channels, or a matrix input, which can carry scale) produces the
        frame-local shear the coarse grid misses, and such a drive marks the
        node and every descendant as candidates.
        """
        paths = set(nodes)
        for node in nodes:
            parts = node.split("|")
            for i in range(2, len(parts)):
                paths.add("|".join(parts[:i]))
        # ONE connection query over every plug, read back through the
        # destination side. Per-path attributeQuery + listConnections cost
        # ~0.13 ms x 2 per node (measured 0.32 s over a 2400-path scan, run
        # twice per export -- the flatten task and the check share this);
        # a single batched call is ~10 ms. Every DAG transform carries
        # offsetParentMatrix (Maya 2020+), so the existence probe is gone
        # too; ``cmds.ls`` drops the paths an earlier task has deleted.
        plugs = [
            f"{path}.{attr}"
            for path in (cmds.ls(list(paths), long=True) or [])
            for attr in ("scale", "scaleX", "scaleY", "scaleZ", "offsetParentMatrix")
        ]
        # Destination nodes come back shortest-unique; the keys must be the
        # full paths the caller walks, so map them back through ls.
        dynamic = set(
            cmds.ls(
                [
                    dest.rsplit(".", 1)[0]
                    for dest, _ in NodeUtils.incoming_connections(plugs)
                ],
                long=True,
            )
            or []
        )
        if not dynamic:
            return []
        out = []
        for node in nodes:
            parts = node.split("|")
            if any("|".join(parts[:i]) in dynamic for i in range(2, len(parts) + 1)):
                out.append(node)
        return out

    def _ssc_offenders(
        self, tolerance: float, nodes: Optional[List[str]] = None
    ) -> Dict[str, float]:
        """``{joint: worst |parent scale - 1|}`` for compensated joints whose
        parent actually scales.

        Maya's segmentScaleCompensate cancels the parent joint's scale
        before the child's transform; FBX and glTF recompose plain per-node
        TRS, so the parent scale compounds down the chain instead (measured
        +17.8% bone stretch by the 11th link of a production wire loom).
        The loss needs no shear at all -- a straight compensated chain under
        a scaling parent still ships wrong -- so this scan complements the
        skew metric instead of extending it: active compensation (an
        actually-wired ``inverseScale``) under a parent whose scale leaves
        1.0 beyond *tolerance* -- statically, on a key, or through a
        connection-driven value at any dense-scan frame -- flags the joint
        for the same flatten.
        """
        out: Dict[str, float] = {}
        driven: Dict[str, List[str]] = {}
        scan = self._shear_scan_nodes() if nodes is None else nodes
        # Only joints carry segmentScaleCompensate: one typed ``ls`` instead
        # of a ``nodeType`` per node of the whole export set.
        for node in cmds.ls(scan, type="joint", long=True) or []:
            if not cmds.getAttr(f"{node}.segmentScaleCompensate"):
                continue
            if not cmds.listConnections(
                f"{node}.inverseScale", source=True, destination=False
            ):
                # An UNWIRED inverseScale holds its default (1,1,1) and
                # compensates nothing (non-joint parents are never wired).
                # A hand-set static non-unit value on an unwired plug is out
                # of scope here -- when its shear shows, the skew scan has it.
                continue
            parent = (cmds.listRelatives(node, parent=True, fullPath=True) or [None])[0]
            if not parent:
                continue
            worst = max(abs(v - 1.0) for v in cmds.getAttr(f"{parent}.scale")[0])
            keyed = cmds.keyframe(
                parent,
                attribute=["scaleX", "scaleY", "scaleZ"],
                query=True,
                valueChange=True,
            )
            if keyed:
                worst = max(worst, max(abs(v - 1.0) for v in keyed))
            sources = (
                cmds.listConnections(
                    f"{parent}.scale",
                    f"{parent}.scaleX",
                    f"{parent}.scaleY",
                    f"{parent}.scaleZ",
                    source=True,
                    destination=False,
                )
                or []
            )
            if any(not cmds.nodeType(src).startswith("animCurve") for src in sources):
                # Keys are covered by the valueChange query above; anything
                # else driving scale needs evaluation over time.
                driven.setdefault(parent, []).append(node)
            if worst > tolerance:
                out[node] = max(out.get(node, 0.0), worst)
        if driven:
            frames = self._shear_dense_frames()
            if frames:
                import maya.api.OpenMaya as om2

                # Scale plugs resolved once (a parent that cannot be resolved
                # is skipped, as the per-frame objExists used to skip it).
                scale_plugs: Dict[str, list] = {}
                selection = om2.MSelectionList()
                for parent in driven:
                    try:
                        selection.clear()
                        selection.add(parent)
                        dep = om2.MFnDependencyNode(selection.getDependNode(0))
                    except RuntimeError:
                        continue
                    scale_plugs[parent] = [
                        dep.findPlug(a, False) for a in ("scaleX", "scaleY", "scaleZ")
                    ]
                restore = cmds.currentTime(query=True)
                try:
                    for frame in frames:
                        cmds.currentTime(frame, edit=True)
                        for parent, children in driven.items():
                            plugs = scale_plugs.get(parent)
                            if plugs is None:
                                continue
                            worst = max(abs(p.asDouble() - 1.0) for p in plugs)
                            if worst <= tolerance:
                                continue
                            for node in children:
                                if worst > out.get(node, 0.0):
                                    out[node] = worst
                finally:
                    cmds.currentTime(restore, edit=True)
        return out

    def _sheared_offenders(self, tolerance: float) -> Dict[str, float]:
        """``{node: worst skew}`` -- the one scan the check and the flatten
        task share: the coarse grid over everything, plus every integer frame
        over the scale-dynamic candidates.
        """
        from mayatk.core_utils.diagnostics.transform_diag import (
            TransformDiagnostics,
        )

        nodes = self._shear_scan_nodes()
        if not nodes:
            return {}
        offenders = TransformDiagnostics.get_non_orthogonal_local(
            nodes, tolerance=tolerance, frames=self._shear_sample_frames()
        )
        candidates = self._shear_candidates(nodes)
        if candidates:
            dense = self._shear_dense_frames()
            if dense:
                for node, skew in TransformDiagnostics.get_non_orthogonal_local(
                    candidates, tolerance=tolerance, frames=dense
                ).items():
                    if skew > offenders.get(node, 0.0):
                        offenders[node] = skew
        for node, severity in self._ssc_offenders(tolerance, nodes).items():
            if severity > offenders.get(node, 0.0):
                offenders[node] = severity
        for node, severity in self._opm_offenders(tolerance, nodes).items():
            if severity > offenders.get(node, 0.0):
                offenders[node] = severity
        return offenders

    def _sheared_offenders_after_flatten(self, tolerance: float) -> Dict[str, float]:
        """The shear CHECK's offenders, without re-running the flatten's scan.

        The flatten task and the check share one scan, and the check runs
        after the flatten in every default run -- so it re-measured every
        node the flatten had just measured (31 s of a production export,
        2026-09-13) to learn what the flatten already knew. When the flatten
        ran this run at the same tolerance its verdict stands: the nodes it
        could not place or that refused the reparent are reported with the
        skew it measured, and the nodes it re-anchored are re-checked on the
        coarse grid (they were world-fitted under a similarity ancestor, so a
        residual there is a failed bake, not a missed frame). Any other
        tolerance, or no flatten, means the full scan.
        """
        verdict = self._shear_verdict
        if not verdict or verdict.get("tolerance") != tolerance:
            return self._sheared_offenders(tolerance)
        measured = verdict["offenders"]
        out = {
            path: measured[path]
            for path in verdict["unplaced"] + verdict["failed"]
            if path in measured
        }
        # One query for the lot: ``ls`` skips a UUID no node holds any more.
        flattened = cmds.ls(verdict["flattened"], long=True) or []
        if flattened:
            from mayatk.core_utils.diagnostics.transform_diag import (
                TransformDiagnostics,
            )

            frames = self._shear_sample_frames()
            for node, skew in TransformDiagnostics.get_non_orthogonal_local(
                flattened, tolerance=tolerance, frames=frames
            ).items():
                if skew > out.get(node, 0.0):
                    out[node] = skew
        self.logger.info(
            f"Sheared-transform check: reused the flatten's scan ({len(measured)} "
            f"offender(s) measured; {len(flattened)} re-anchored node(s) "
            f"re-verified on the coarse grid; {len(out)} left to report)."
        )
        return out

    def _opm_offenders(
        self, tolerance: float, nodes: Optional[List[str]] = None
    ) -> Dict[str, float]:
        """``{node: cumulative OPM non-similarity}`` for connected
        ``offsetParentMatrix`` networks a plain TRS cannot carry.

        A CONNECTED offsetParentMatrix never reaches FBX -- the export
        folds ``TRS x OPM`` onto the plugs -- and that product is
        TRS-representable only while the OPM is a SIMILARITY. Non-uniform
        scale (the production _01 wire looms' tweak-follow networks:
        ~3% per link) leaves shear in the folded local that FBX/glTF
        drop. Per link the loss sits under any sane tolerance; down a
        27-joint chain it compounded to 0.65 cm at the tip even after a
        COMPLETE fold -- so severity ACCUMULATES from the nearest
        OPM-connected ancestor (parent's cumulative + this node's
        non-uniformity + shear), evaluated over the dense frames (the
        plug is connection-driven by definition). A flagged node joins
        the same world-fitted flatten, whose similarity-ancestor refit
        is exact.
        """
        import maya.api.OpenMaya as om2

        scan = self._shear_scan_nodes() if nodes is None else nodes
        # One connection query over every OPM plug (a per-node call cost
        # 0.2 s over a 1200-node export set, twice per export); the
        # destination side comes back shortest-unique, so it is mapped to
        # the scan's long paths through ``ls``.
        pairs = NodeUtils.incoming_connections(
            [f"{node}.offsetParentMatrix" for node in scan]
        )
        connected = set(
            cmds.ls([dest.rsplit(".", 1)[0] for dest, _ in pairs], long=True) or []
        )
        targets: List[str] = [node for node in scan if node in connected]
        if not targets:
            return {}
        parent_of = {
            n: (cmds.listRelatives(n, parent=True, fullPath=True) or [None])[0]
            for n in targets
        }
        target_set = set(targets)
        order = sorted(targets, key=lambda p: p.count("|"))
        frames = self._shear_dense_frames() or self._shear_sample_frames()
        if not frames:
            # The set-scoped key query can answer empty while the OPM still
            # ANIMATES -- its driver keys live outside the export set (the
            # unit fixture keys a composeMatrix; production tweak rigs key
            # utility nodes). Same fallback as the flatten task: the
            # playback range is the outermost statement of intent.
            start, end = (int(f) for f in AnimUtils.scene_animation_range())
            frames = (
                [float(f) for f in range(start, end + 1)]
                if end > start
                else [float(cmds.currentTime(query=True))]
            )
        # The OPM plugs resolved once and read through MFnMatrixData, as
        # SmartBake's matrix pass does: a getAttr + objExists per node per
        # frame was ~45 s of a production scan.
        opm_plugs: Dict[str, "om2.MPlug"] = {}
        selection = om2.MSelectionList()
        for node in order:
            try:
                selection.clear()
                selection.add(node)
                dep = om2.MFnDependencyNode(selection.getDependNode(0))
            except RuntimeError:
                continue
            opm_plugs[node] = dep.findPlug("offsetParentMatrix", False)
        out: Dict[str, float] = {}
        restore = cmds.currentTime(query=True)
        try:
            for frame in frames:
                cmds.currentTime(frame, edit=True)
                cum: Dict[str, float] = {}
                for node in order:
                    plug = opm_plugs.get(node)
                    if plug is None:
                        continue
                    xf = om2.MTransformationMatrix(
                        om2.MFnMatrixData(plug.asMObject()).matrix()
                    )
                    s = xf.scale(om2.MSpace.kWorld)
                    sh = xf.shear(om2.MSpace.kWorld)
                    dev = max(
                        abs(s[0] - s[1]),
                        abs(s[1] - s[2]),
                        abs(s[0] - s[2]),
                        abs(sh[0]),
                        abs(sh[1]),
                        abs(sh[2]),
                    )
                    parent = parent_of[node]
                    total = dev + (
                        cum.get(parent, 0.0) if parent in target_set else 0.0
                    )
                    cum[node] = total
                    if total > tolerance and total > out.get(node, 0.0):
                        out[node] = total
        finally:
            cmds.currentTime(restore, edit=True)
        return out

    def _expand_chain_offenders(self, offenders: Dict[str, float]) -> Dict[str, float]:
        """Flagged joints pull in their ENTIRE chain.

        Sub-tolerance members of a flagged chain each leak up to the
        tolerance in dropped shear, and twenty of them compound to visible
        drift -- the mixed half-flattened chains the first production fix
        shipped. Expansion walks joint-to-joint links both ways; a member
        already sitting under the flatten target is skipped quietly later.
        """
        expanded = dict(offenders)
        for path in list(offenders):
            if not cmds.objExists(path):
                continue
            parts = path.split("|")
            for i in range(len(parts) - 1, 2, -1):
                ancestor = "|".join(parts[:i])
                if not cmds.objExists(ancestor):
                    break
                if cmds.nodeType(ancestor) != "joint":
                    break
                expanded.setdefault(ancestor, 0.0)
            for descendant in (
                cmds.listRelatives(
                    path, allDescendents=True, type="joint", fullPath=True
                )
                or []
            ):
                expanded.setdefault(descendant, 0.0)
        return expanded
