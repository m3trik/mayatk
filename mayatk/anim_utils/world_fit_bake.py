# !/usr/bin/python
# coding=utf-8
"""World-fitted transform bake: reparent nodes under a chosen target and key the
translate / rotate / scale that reproduce their sampled WORLD matrices.

The one primitive behind three callers: the Scene Exporter's
``flatten_sheared_chains`` (a deliverable export, with a staged restore),
``SmartBake``'s sheared matrix drives (a bake, reversed by its session) -- both
through the reversible :meth:`WorldFitBake.flatten` -- and
``SkinUtils.flatten_influences`` (a conversion scene, where each skin needs one
skeleton). FBX and USD store an animated node as translate / rotate / scale, so a
parent-relative matrix that shears has no representation and the residual
compounds down a chain; relative to a similarity ancestor every orthogonal world
matrix decomposes to TRS exactly.

Sample BEFORE any mutation: an IK solver computes its joints' locals from the
chain's parent structure, so a post-reparent evaluation answers a different rig
(measured: 15.9 cm exactly during an IK-active shot).
"""

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import maya.cmds as cmds
except ImportError as error:  # pragma: no cover - Maya environment required
    cmds = None
    print(__file__, error)


class WorldFitBake:
    """Sample world matrices from the untouched scene, then reparent and key.

    A *plan* is a list of ``(path, uuid, target, reparent)`` tuples: the node's
    long DAG path in the untouched scene, its UUID (paths go stale once the first
    node moves), the transform it will be keyed relative to (and parented under
    when *reparent* is true), and that flag.
    """

    #: The channels a fitted key set writes, with their animCurve kinds.
    CHANNELS: Tuple[Tuple[str, str], ...] = (
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

    class Failed(RuntimeError):
        """:meth:`WorldFitBake.bake_node` raised AFTER mutating its node.

        ``record`` is the node's restore record as far as the bake got; a caller
        that keeps the scene reverses it (:meth:`WorldFitBake.restore_node`,
        which :meth:`WorldFitBake.flatten` does itself), one that owns a
        throwaway scene reports it.
        """

        def __init__(self, message: str, record: dict):
            super().__init__(message)
            self.record = record

    @staticmethod
    def sample_locals(
        plan: Sequence[Tuple[str, str, str, bool]],
        frames: Sequence[float],
        orient: Optional[Dict[str, Sequence[float]]] = None,
        residuals: Optional[Dict[Tuple[str, str], float]] = None,
    ) -> Dict[Tuple[str, str], List[List[float]]]:
        """``{(node, target): [[tx..sz] per frame]}`` from the UNTOUCHED scene.

        One timeline pass for the whole plan (``currentTime`` is the expensive
        step). Sampling BEFORE any mutation is the load-bearing choice: an IK
        solver computes the joints' locals from the chain's parent structure, so
        any post-reparent evaluation answers a different rig. Rotations are unwound
        against the previous frame so the keyed euler curves stay continuous.

        *orient* turns a node's FRAME without moving it: ``{node: 16 row-major
        floats}``, pre-multiplied onto its world matrix so the keys describe
        ``R * world`` -- the node's axes relabelled, its position and the shape of
        its motion untouched. Only a caller that also re-bases whatever reads the
        node's world matrix may use it (``SkinUtils.flatten_influences`` moves each
        skinCluster's ``bindPreMatrix`` by ``R^-1``); an AXIS-ALIGNED rotation
        keeps the result TRS-exact, since it permutes a diagonal scale where an
        arbitrary one would shear it.

        *residuals*, when given, receives ``{(node, target): worst shear}`` --
        the shear each fitted local still carries, which its TRS keys cannot:
        0 (to float noise) when the node's world is orthogonal, the drift left
        over when it is not.
        """
        import maya.api.OpenMaya as om2

        targets = sorted({t for _, _, t, _ in plan})
        out: Dict[Tuple[str, str], List[List[float]]] = {
            (p, t): [] for p, _, t, _ in plan
        }
        turns = {node: om2.MMatrix(m) for node, m in (orient or {}).items()}
        prev_euler: Dict[str, List[float]] = {}
        orders: Dict[str, int] = {}
        for path, _, _, _ in plan:
            orders[path] = cmds.getAttr(f"{path}.rotateOrder")
        # DAG paths resolved once: ``inclusiveMatrix`` is the same ``worldMatrix[0]``
        # evaluation, without a getAttr + 16-float list per node per frame (that
        # read was ~100 s of a 3.4k-frame production flatten). Nothing moves until
        # every sample is taken, so the paths stay valid for the whole pass.
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
                    turn = turns.get(path)
                    if turn is not None:
                        world = turn * world
                    local = world * inverses[target]
                    xf = om2.MTransformationMatrix(local)
                    if residuals is not None:
                        worst = max(abs(v) for v in xf.shear(om2.MSpace.kTransform))
                        key = (path, target)
                        if worst > residuals.get(key, 0.0):
                            residuals[key] = worst
                    t3 = xf.translation(om2.MSpace.kWorld)
                    euler = xf.rotation(asQuaternion=True).asEulerRotation()
                    euler = euler.reorder(orders[path])
                    cur = [euler.x, euler.y, euler.z]
                    prev = prev_euler.get(path)
                    if prev is not None:
                        for i in range(3):
                            while cur[i] - prev[i] > math.pi:
                                cur[i] -= 2.0 * math.pi
                            while prev[i] - cur[i] > math.pi:
                                cur[i] += 2.0 * math.pi
                    prev_euler[path] = cur
                    s3 = xf.scale(om2.MSpace.kWorld)
                    out[(path, target)].append(
                        [t3.x, t3.y, t3.z, cur[0], cur[1], cur[2], *s3]
                    )
        finally:
            cmds.currentTime(restore_time)
        return out

    @classmethod
    def bake_node(
        cls,
        node: str,
        target: str,
        frames: Sequence[float],
        rows: Sequence[Sequence[float]],
        reparent: bool = True,
    ) -> dict:
        """Reparent *node* under *target* and key the pre-sampled locals *rows*.

        Neutralises everything that would fight the keys: TRS driver connections
        are cut (recorded), offsetParentMatrix is reset to identity (source /
        value recorded), and on joints the orient / axis / segmentScaleCompensate
        are zeroed so the keyed rotate IS the local rotation. Keys land through
        one ``MFnAnimCurve.addKeys`` per channel.

        Returns:
            The restore record (UUIDs, cut plugs, original values, curves), what
            the Scene Exporter's deferred restore reverses.

        Raises:
            RuntimeError: The reparent refused (a locked or referenced node);
                nothing was changed for this node.
            WorldFitBake.Failed: A step AFTER the reparent raised; ``record`` on
                the exception undoes what was done (:meth:`restore_node`).
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

        # The driver BEHIND any unitConversion, with its factor: the cut orphans
        # the conversion node, so its name would dangle by restore time, and a
        # re-inserted conversion is sized from the working unit at connect
        # time (metres during an export) -- the session store's answer.
        from mayatk.anim_utils.smart_bake.bake_session import BakeSessionStore

        for attr, _ in cls.CHANNELS:
            plug = f"{node}.{attr}"
            src_plug, factor = BakeSessionStore.trace_source(plug)
            if src_plug:
                record["cut"].append([src_plug, attr, factor])
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
        # everything before it was read-only, so a raise there leaves the scene
        # untouched for this node. Anything failing AFTER it is reported with the
        # record -- a moved node without a record would be invisible to a restore.
        # relative=True: absolute parenting inserts a compensating 'transform1'
        # buffer above a joint whenever jointOrient cannot absorb the move -- and
        # the fitted keys are relative to TARGET, not target x buffer. Local
        # values are overwritten by the keys.
        if reparent:
            moved = cmds.parent(node, target, relative=True)[0]
            node = (cmds.ls(moved, long=True) or [moved])[0]
        try:
            for _src_plug, attr, _factor in record["cut"]:
                # Cut at the plug's own input -- the conversion node when there
                # is one -- while the record names the driver behind it.
                plug = f"{node}.{attr}"
                for direct in (
                    cmds.listConnections(
                        plug, source=True, destination=False, plugs=True
                    )
                    or []
                ):
                    try:
                        cmds.disconnectAttr(direct, plug)
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
            for column, (attr, kind) in enumerate(cls.CHANNELS):
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
            raise cls.Failed(f"flatten bake failed mid-mutation: {e}", record) from e
        return record

    @staticmethod
    def similarity_ancestors(
        nodes: Iterable[str], frames: Sequence[float], tolerance: float = 0.05
    ) -> Dict[str, bool]:
        """``{ancestor path: qualifies}`` for every ancestor of *nodes*.

        A qualifying flatten target is a similarity transform at every sampled
        frame: orthogonal world axes AND uniform axis lengths -- relative to
        such a node, an orthogonal world matrix decomposes to TRS exactly. A
        zero-scale sample frame disqualifies a candidate: the fit references
        its world inverse, which a degenerate matrix cannot supply. One time
        pass over all candidates, so a flatten loop never touches the timeline.

        Parameters:
            nodes: Long DAG paths of the nodes to be flattened.
            frames: The frames to judge at (empty = the current frame).
            tolerance: Cosine skew a qualifying world may show
                (``TransformDiagnostics._matrix_skew``).
        """
        import maya.api.OpenMaya as om2

        from mayatk.core_utils.diagnostics.transform_diag import (
            TransformDiagnostics,
        )

        candidates = set()
        for node in nodes:
            parts = node.split("|")
            for i in range(2, len(parts)):
                candidates.add("|".join(parts[:i]))
        verdict = {c: True for c in candidates}
        if not candidates:
            return verdict

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
                        # fit can't invert it -- disqualify outright. (Maya
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
    def flatten_target(node: str, qualifies: Dict[str, bool]) -> Optional[str]:
        """Deepest qualifying ancestor of *node* (by its pre-flatten path),
        from :meth:`similarity_ancestors`.

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

    @classmethod
    def flatten(
        cls,
        plan: Sequence[Tuple[str, str, str, bool]],
        frames: Sequence[float],
        records: Optional[List[dict]] = None,
        max_residual: Optional[float] = None,
    ) -> dict:
        """The reversible flatten, in one call: :meth:`prepare` then
        :meth:`apply`. A caller that mutates anything else in between -- and
        must see the untouched hierarchy while it does -- calls the two
        itself (``SmartBake``: every other pass runs first, the reparents
        last).

        Parameters:
            plan, frames, max_residual: See :meth:`prepare`.
            records: See :meth:`apply`.

        Returns:
            :meth:`apply`'s outcome.
        """
        return cls.apply(cls.prepare(plan, frames, max_residual), records)

    @classmethod
    def prepare(
        cls,
        plan: Sequence[Tuple[str, str, str, bool]],
        frames: Sequence[float],
        max_residual: Optional[float] = None,
    ) -> dict:
        """Everything the flatten reads, taken before anything moves: the IK
        census and each node's fitted locals (see the module docstring for why
        sampling must precede every mutation).

        Parameters:
            plan: ``(path, uuid, target, reparent)`` per node, parents first.
            frames: The frames to sample (and later key).
            max_residual: Leave in place any node whose fitted local still
                shears past this at some frame (its WORLD is not orthogonal,
                so no TRS under any parent holds it): moving it would
                restructure the rig and still drift. None keeps every node.

        Returns:
            ``{"plan", "frames", "samples", "handles", "targets", "kept",
            "unfit"}`` for :meth:`apply` -- ``plan`` holds the nodes that
            will move; ``handles`` is ``{handle uuid: its chain}`` and
            ``targets`` ``{target path: uuid}``, both by UUID because a path
            does not survive the reparents (a target can itself be a chain
            node that moves first); ``unfit`` ``[(path, residual)]`` the
            nodes left out, and ``kept`` what :meth:`apply` needs of each --
            its uuid, its parent and whether it compensates that parent's
            scale.
        """
        prepared: dict = {
            "plan": [],
            "frames": list(frames),
            "samples": {},
            "handles": {},
            "targets": {},
            "kept": {},
            "unfit": [],
        }
        if not plan:
            return prepared
        # Keyed by UUID, not by the path the census returns: :meth:`apply` reads
        # these AFTER the reparents, and a handle parented under a node that
        # moves is renamed by the move.
        prepared["handles"] = {
            (cmds.ls(handle, uuid=True) or [handle])[0]: chain
            for handle, chain in cls.ik_handles_touching(
                p for p, _, _, _ in plan
            ).items()
        }
        residuals: Optional[Dict[Tuple[str, str], float]] = (
            {} if max_residual is not None else None
        )
        prepared["samples"] = cls.sample_locals(plan, frames, residuals=residuals)
        for entry in plan:
            path, uuid, target, _ = entry
            residual = residuals.get((path, target), 0.0) if residuals else 0.0
            if max_residual is not None and residual > max_residual:
                prepared["unfit"].append((path, residual))
                # Left in place under a parent that may move: Maya's scale
                # compensation divides the parent's .scale out of a child, and
                # the fitted keys put the stretch a drive delivered INTO that
                # .scale -- see :meth:`apply`.
                prepared["kept"][path] = {
                    "uuid": uuid,
                    "parent": (
                        cmds.listRelatives(path, parent=True, fullPath=True)
                        or [None]
                    )[0],
                    "ssc": bool(
                        cmds.attributeQuery(
                            "segmentScaleCompensate", node=path, exists=True
                        )
                        and cmds.getAttr(f"{path}.segmentScaleCompensate")
                    ),
                }
            else:
                prepared["plan"].append(entry)
                prepared["targets"].setdefault(
                    target, (cmds.ls(target, uuid=True) or [None])[0]
                )
        return prepared

    @classmethod
    def apply(cls, prepared: dict, records: Optional[List[dict]] = None) -> dict:
        """Reparent and key every node :meth:`prepare` sampled, and park the IK
        handles the moves would break.

        A node whose bake raises after its reparent is put back through its
        own record; one that refuses the reparent (locked, referenced) is left
        where it was. An IK handle whose PRE-move chain met a node that
        actually baked is disabled (``ikBlend`` 0, recorded): its solver writes
        the joints' locals with no plug connection, so it would keep solving
        the old rig over the fitted keys.  A node :meth:`prepare` left in
        place whose parent moved has its scale compensation released
        (recorded): the parent's stretch used to arrive through a drive with
        ``.scale`` at 1, so the compensation did nothing; the fitted keys
        carry that stretch IN ``.scale``, where the compensation would divide
        it back out of the child.  Released, the child's world is exactly
        what it was, and the restore puts the flag back.

        Parameters:
            prepared: :meth:`prepare`'s result.
            records: The list to append restore records to -- pass one a
                restore is already staged over, so an exception part-way
                still leaves every node moved so far reversible.

        Returns:
            ``{"records": [...], "baked": [(path, uuid, target)],
            "failed": [(path, reason)], "unfit": [(path, residual)],
            "warnings": [str]}`` -- the records in the order :meth:`restore`
            reverses.
        """
        records = [] if records is None else records
        outcome: dict = {
            "records": records,
            "baked": [],
            "failed": [],
            "unfit": list(prepared.get("unfit") or []),
            "warnings": [],
        }
        samples, frames = prepared["samples"], prepared["frames"]
        targets = prepared.get("targets") or {}
        for path, uuid, target, reparent in prepared["plan"]:
            node = (cmds.ls(uuid, long=True) or [None])[0]
            # The target by its UUID: a target that is itself a chain node has
            # already moved (parents first), and its planned path with it.
            live_target = (cmds.ls(targets.get(target) or target, long=True) or [None])[0]
            if not node or not live_target or (path, target) not in samples:
                continue
            try:
                records.append(
                    cls.bake_node(
                        node,
                        live_target,
                        frames,
                        samples[(path, target)],
                        reparent=reparent,
                    )
                )
            except cls.Failed as error:
                cls.restore_node(error.record)
                outcome["failed"].append((path, str(error)))
                continue
            # ValueError is Maya's for a name it cannot resolve (a stale path),
            # RuntimeError for a write it refuses (locked, referenced).
            except (RuntimeError, ValueError) as error:
                outcome["failed"].append((path, str(error)))
                continue
            outcome["baked"].append((path, uuid, target))

        baked_paths = {path for path, _, _ in outcome["baked"]}
        for path, kept in (prepared.get("kept") or {}).items():
            if not kept.get("ssc") or kept.get("parent") not in baked_paths:
                continue
            node = (cmds.ls(kept["uuid"], long=True) or [None])[0]
            if not node:
                continue
            try:
                cmds.setAttr(f"{node}.segmentScaleCompensate", False)
            except (RuntimeError, ValueError) as error:
                outcome["warnings"].append(
                    f"Could not release scale compensation on '{node}' (its "
                    f"parent's fitted scale will be divided out of it): {error}"
                )
                continue
            records.append({"mode": "ssc", "node": kept["uuid"], "value": True})
        for uuid, chain in prepared["handles"].items():
            if not chain & baked_paths:
                continue  # a handle serving only unbaked nodes keeps solving
            handle = (cmds.ls(uuid, long=True) or [None])[0]
            if not handle:
                outcome["warnings"].append(
                    f"Could not disable IK handle '{uuid}': it is gone from the scene."
                )
                continue
            try:
                prior = cmds.getAttr(f"{handle}.ikBlend")
                cmds.setAttr(f"{handle}.ikBlend", 0.0)
            # Maya raises ValueError for a name it cannot resolve and
            # RuntimeError for a plug it will not write (locked, referenced).
            except (RuntimeError, ValueError) as error:
                outcome["warnings"].append(
                    f"Could not disable IK handle '{handle}': {error}"
                )
                continue
            records.append({"mode": "ikblend", "handle": uuid, "value": prior})
        return outcome

    @staticmethod
    def restore_node(record: dict) -> bool:
        """Reverse one :meth:`bake_node` (or ``ikblend``) record: delete the
        fitted curves, reparent back, and put the joint orients, values and
        wiring the bake neutralised back. True when the node was found."""

        def _resolve(uuid):
            return (cmds.ls(uuid, long=True) or [None])[0] if uuid else None

        if record.get("mode") in ("ikblend", "ssc"):
            attr, key = (
                ("ikBlend", "handle")
                if record["mode"] == "ikblend"
                else ("segmentScaleCompensate", "node")
            )
            target = _resolve(record.get(key))
            if target:
                try:
                    cmds.setAttr(f"{target}.{attr}", record.get("value", 1.0))
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
        # Each write on its own: a refused one (a locked attribute on a
        # referenced joint) must not skip the values and wiring after it.
        joint = record.get("joint") or {}
        for attr, value in joint.items():
            try:
                if isinstance(value, (list, tuple)):
                    cmds.setAttr(f"{node}.{attr}", *value)
                else:
                    cmds.setAttr(f"{node}.{attr}", value)
            except RuntimeError:
                pass
        for attr, value in (record.get("originals") or {}).items():
            try:
                cmds.setAttr(f"{node}.{attr}", value)
            except RuntimeError:
                pass
        from mayatk.anim_utils.smart_bake.bake_session import BakeSessionStore

        for src_plug, attr, *factor in record.get("cut", []):
            try:
                # A record written before the factor was kept leaves Maya's
                # choice of conversion alone.
                BakeSessionStore.reconnect(
                    src_plug, f"{node}.{attr}", factor[0] if factor else None
                )
            except (RuntimeError, ValueError):
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

    @classmethod
    def restore(cls, records: Sequence[dict]) -> Tuple[int, List[str]]:
        """Reverse *records* (LIFO) with :meth:`restore_node`.

        Returns:
            ``(restored, errors)`` -- how many records found their node, and a
            message per record whose reversal raised; the rest still run.
        """
        restored, errors = 0, []
        for record in reversed(list(records)):
            try:
                if cls.restore_node(record):
                    restored += 1
            except (RuntimeError, ValueError) as error:
                errors.append(str(error))
        return restored, errors

    @staticmethod
    def ik_handles_touching(paths: Iterable[str]) -> Dict[str, set]:
        """``{ikHandle: its joint chain}`` for every handle whose chain meets *paths*.

        Take it BEFORE any reparent: a handle whose chain loses members reports an
        empty ``jointList`` afterwards. An IK solver writes its joints' locals with
        NO plug connections, so cutting connections does not detach it -- a handle
        whose chain lost members to a reparent now solves a different rig, and the
        caller disables it (``ikBlend`` 0) once its joints are keyed.
        """
        planned = set(paths)
        out: Dict[str, set] = {}
        for handle in cmds.ls(type="ikHandle", long=True) or []:
            chain = set(
                cmds.ls(
                    cmds.ikHandle(handle, query=True, jointList=True) or [], long=True
                )
            )
            if chain & planned:
                out[handle] = chain
        return out
