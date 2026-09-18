# !/usr/bin/python
# coding=utf-8
"""World-fitted transform bake: reparent nodes under a chosen target and key the
translate / rotate / scale that reproduce their sampled WORLD matrices.

The one primitive behind two callers: the Scene Exporter's
``flatten_sheared_chains`` (a deliverable export, with a staged restore) and
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
        that keeps the scene reverses it (the Scene Exporter's
        ``_restore_baked_flatten``), one that owns a throwaway scene reports it.
        """

        def __init__(self, message: str, record: dict):
            super().__init__(message)
            self.record = record

    @staticmethod
    def sample_locals(
        plan: Sequence[Tuple[str, str, str, bool]],
        frames: Sequence[float],
        orient: Optional[Dict[str, Sequence[float]]] = None,
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
                the exception undoes what was done.
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

        for attr, _ in cls.CHANNELS:
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
