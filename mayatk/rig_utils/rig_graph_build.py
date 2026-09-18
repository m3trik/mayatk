# !/usr/bin/python
# coding=utf-8
"""Build a RigGraph in Maya -- Maya as the TARGET of the rig-transfer stack.

Mirror of blendertk's ``RigGraphBuilder`` (name + behavior). The planner
(``pythontk.RigPlanner``) decides what this target can build from a graph and
what must be baked; this module builds it from Maya's own constraint commands
-- a Blender ``CHILD_OF`` becomes a ``parentConstraint``, a ``TRACK_TO`` an
``aimConstraint``, a driver a ``multDoubleLinear`` / ``addDoubleLinear`` pair
or a set-driven key, a ``SPLINE_IK`` an ``ikSplineSolver`` handle -- and
declares what it built as data the planner reads (:meth:`capability`).

Fidelity is EARNED (schema section 9.1): no conformance fixture vouches for
any op yet, so every entry grades ``approximate`` and the planner attaches
``verify`` to every record it builds. ``pythontk.RigVerify.verify_plan``
measures each one against the source's sampled points through
:meth:`sample_world` and demotes a miss through :meth:`remove`.

Identity is the payload's prim path, spelled the way the DAG sanitises: every
segment through ``UsdUtils.sanitize_prim_name``, namespaces included. On the
FBX route only the leaf survives, so ids fall back to a leaf lookup scored by
how many of the id's ancestor segments the node's parents match; joints are
transforms here, so a Blender bone resolves like any other node.

The carrier bakes constrained motion too (a built constraint on top of baked
keys is a double transform), so a built record MUTES the payload's keys on the
channels it drives for the verify step; :meth:`commit` deletes them on a pass
and :meth:`remove` unmutes them on a miss, so no motion is ever lost.

``import maya.cmds`` is try-guarded like every mayatk module, so the surface
resolves without Maya.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)

import pythontk as ptk


class _RigGraphBuilderInternal:
    """Resolution and the per-op builders."""

    _CHANNEL_ATTR: Dict[str, str] = {
        "translate.x": "translateX",
        "translate.y": "translateY",
        "translate.z": "translateZ",
        "rotate.x": "rotateX",
        "rotate.y": "rotateY",
        "rotate.z": "rotateZ",
        "scale.x": "scaleX",
        "scale.y": "scaleY",
        "scale.z": "scaleZ",
        "visibility": "visibility",
    }
    _GROUP_ATTRS: Dict[str, Tuple[str, ...]] = {
        "translate": ("translateX", "translateY", "translateZ"),
        "rotate": ("rotateX", "rotateY", "rotateZ"),
        "scale": ("scaleX", "scaleY", "scaleZ"),
    }
    # Time-driven anim curves (the payload's keys); a driven curve (``animCurveU*``)
    # is rig logic of its own and stays.
    _TIME_CURVES = ("animCurveTL", "animCurveTA", "animCurveTU", "animCurveTT")
    # Maya's FBX importer spells an illegal character as ``FBXASC`` + its ASCII
    # code (``dotted.001`` -> ``dottedFBXASC046001``); the ids are prim-sanitised
    # Blender names, so a segment is decoded before it is sanitised.
    _FBX_ESCAPE = re.compile(r"FBXASC(\d{3})")
    _SOLVERS: Dict[str, str] = {
        "rotate_plane": "ikRPsolver",
        "single_chain": "ikSCsolver",
        "two_bone": "ikRPsolver",
    }

    def __init__(self) -> None:
        self._by_path: Dict[str, str] = {}
        self._by_leaf: Dict[str, List[str]] = {}
        self._created: Dict[str, List[str]] = {}
        self._muted: Dict[str, List[Tuple[str, str]]] = {}
        self._current: Optional[str] = None

    # ------------------------------------------------------------ resolution
    def _index(self, nodes: Sequence[str]) -> None:

        self._by_path.clear()
        self._by_leaf.clear()
        for node in cmds.ls(nodes, long=True, type="transform") or []:
            segments = [self._spell(s) for s in node.split("|") if s]
            self._by_path.setdefault("/" + "/".join(segments), node)
            self._by_leaf.setdefault(segments[-1], []).append(node)

    @classmethod
    def _spell(cls, segment: str) -> str:
        """A DAG path segment as the payload's id spells it: namespace dropped,
        the FBX importer's escapes decoded, then prim-sanitised."""
        from mayatk.env_utils.usd import UsdUtils

        bare = segment.rsplit(":", 1)[-1]
        decoded = cls._FBX_ESCAPE.sub(lambda m: chr(int(m.group(1))), bare)
        return UsdUtils.sanitize_prim_name(decoded)

    def _node(self, node_id: str) -> Optional[str]:
        """The DAG path a node id names: the full sanitised path, else the leaf
        -- and among several nodes sharing a leaf, the one whose ancestors match
        the id's best."""
        found = self._by_path.get(node_id)
        if found is not None:
            return found
        segments = [s for s in node_id.split("/") if s]
        candidates = self._by_leaf.get(segments[-1] if segments else "", [])
        if len(candidates) <= 1:
            return candidates[0] if candidates else None

        def score(node: str) -> int:
            parents = [self._spell(s) for s in node.split("|") if s][:-1]
            matched = 0
            for want, have in zip(reversed(segments[:-1]), reversed(parents)):
                if want != have:
                    break
                matched += 1
            return matched

        return max(candidates, key=score)

    def _require(self, node_id: str) -> str:
        node = self._node(node_id)
        if node is None:
            raise LookupError(f"no imported node for {node_id!r}")
        return node

    def _plug(self, plug: str) -> str:
        from pythontk.core_utils.engines.rig_graph.rig_model import RigGraph

        node_id, channel = RigGraph.split_plug(plug)
        node = self._require(node_id)
        attr = self._CHANNEL_ATTR.get(channel, channel)
        if attr not in self._CHANNEL_ATTR.values() and not cmds.attributeQuery(
            attr, node=node, exists=True
        ):
            cmds.addAttr(node, longName=attr, attributeType="double", keyable=True)
        return f"{node}.{attr}"

    def _track(self, *created: str) -> None:
        if self._current is not None:
            self._created.setdefault(self._current, []).extend(c for c in created if c)

    @staticmethod
    def _literal(value: Any, default: float = 1.0) -> float:
        return default if isinstance(value, dict) else float(value)

    # ------------------------------------------------------------ builders
    def _build_blend(self, record: Any, params: Dict[str, Any]) -> None:
        driven = self._require(record.target["id"])
        channels = set(record.target.get("channels") or ("translate", "rotate"))
        command = {
            frozenset({"translate"}): cmds.pointConstraint,
            frozenset({"rotate"}): cmds.orientConstraint,
            frozenset({"scale"}): cmds.scaleConstraint,
        }.get(frozenset(channels), cmds.parentConstraint)
        offset = bool(params.get("maintain_offset", True))
        constraint = None
        for source in record.sources:
            target = self._require(source["id"])
            constraint = command(
                target,
                driven,
                maintainOffset=offset,
                weight=self._literal(source.get("weight", 1.0)),
            )[0]
        if constraint:
            self._track(constraint)

    def _build_aim(self, record: Any, params: Dict[str, Any]) -> None:
        driven = self._require(record.target["id"])
        aim_at = next((s for s in record.sources if s.get("role") == "target"), None)
        if aim_at is None:
            raise LookupError("aim needs a 'target' source")
        kwargs: Dict[str, Any] = {
            "aimVector": tuple(params.get("aim_axis") or (1.0, 0.0, 0.0)),
            "upVector": tuple(params.get("up_axis") or (0.0, 1.0, 0.0)),
            "weight": self._literal(aim_at.get("weight", 1.0)),
        }
        up_ref = params.get("up_ref") or {}
        up = next((s for s in record.sources if s.get("role") == "up"), None)
        if up_ref.get("kind") in ("object", "object_axis") and up is not None:
            kwargs["worldUpType"] = (
                "object" if up_ref["kind"] == "object" else "objectrotation"
            )
            kwargs["worldUpObject"] = self._require(up["id"])
            if up_ref.get("axis"):
                kwargs["worldUpVector"] = tuple(up_ref["axis"])
        elif up_ref.get("kind") == "axis":
            kwargs["worldUpType"] = "vector"
            kwargs["worldUpVector"] = tuple(up_ref.get("vector") or (0.0, 1.0, 0.0))
        else:
            kwargs["worldUpType"] = "none"
        self._track(
            cmds.aimConstraint(self._require(aim_at["id"]), driven, **kwargs)[0]
        )

    def _build_skin(self, record: Any, params: Dict[str, Any]) -> None:
        """``points/skin``: nothing to build -- the carrier ships a mesh skin as
        a skinCluster -- but the binding must be THERE: the geometry needs a
        skinCluster whose influences include every joint of the record this
        scene resolves, else the deformer edge did not travel and the record
        fails (its whole component goes with it)."""
        node = self._require(record.target["id"])
        wanted = [self._node(s["id"]) for s in record.sources if s.get("id")]
        wanted = [w for w in wanted if w]
        history = cmds.listHistory(node, pruneDagObjects=True) or []
        for skin in (h for h in history if cmds.nodeType(h) == "skinCluster"):
            influences = set(
                cmds.ls(
                    cmds.skinCluster(skin, query=True, influence=True) or [], long=True
                )
            )
            if wanted and all(w in influences for w in wanted):
                return
        raise LookupError(
            f"skin binding did not travel: {node} has no skinCluster over "
            f"{len(wanted)} resolvable influence(s)"
        )

    def _build_linear(self, record: Any, params: Dict[str, Any]) -> None:
        source, target = (
            self._plug(record.sources[0]["plug"]),
            self._plug(record.target),
        )
        mult = cmds.createNode("multDoubleLinear", name="rigGraph_linear_mul#")
        add = cmds.createNode("addDoubleLinear", name="rigGraph_linear_add#")
        cmds.setAttr(f"{mult}.input2", float(params.get("scale", 1.0)))
        cmds.setAttr(f"{add}.input2", float(params.get("offset", 0.0)))
        cmds.connectAttr(source, f"{mult}.input1")
        cmds.connectAttr(f"{mult}.output", f"{add}.input1")
        cmds.connectAttr(f"{add}.output", target, force=True)
        self._track(mult, add)

    def _build_curve(self, record: Any, params: Dict[str, Any]) -> None:
        source, target = (
            self._plug(record.sources[0]["plug"]),
            self._plug(record.target),
        )
        for point in params.get("points") or []:
            cmds.setDrivenKeyframe(
                target,
                currentDriver=source,
                driverValue=float(point[0]),
                value=float(point[1]),
            )
        curves = (
            cmds.listConnections(
                target, source=True, destination=False, type="animCurve"
            )
            or []
        )
        self._track(*curves)

    def _chain(self, record: Any) -> Tuple[str, str]:
        chain = [self._require(i) for i in (record.target.get("chain") or [])]
        if len(chain) < 2:
            raise LookupError("an IK record needs a chain of at least two joints")
        return chain[0], chain[-1]

    def _build_ik(self, record: Any, params: Dict[str, Any]) -> None:
        from mayatk.rig_utils._rig_utils import RigUtils

        start, end = self._chain(record)
        handle = RigUtils.create_ik_handle(
            start,
            end,
            solver=self._SOLVERS.get(str(params.get("solver")), "ikRPsolver"),
        )
        created = [handle]
        goal = next((s for s in record.sources if s.get("role") == "goal"), None)
        if goal is not None and self._node(goal["id"]) not in (None, handle):
            created.append(
                cmds.parentConstraint(
                    self._require(goal["id"]), handle, maintainOffset=True
                )[0]
            )
        pole = next((s for s in record.sources if s.get("role") == "pole"), None)
        if pole is not None:
            created.append(
                cmds.poleVectorConstraint(self._require(pole["id"]), handle)[0]
            )
        self._track(*created)

    def _build_spline_ik(self, record: Any, params: Dict[str, Any]) -> None:
        start, end = self._chain(record)
        curve = next((s for s in record.sources if s.get("role") == "curve"), None)
        if curve is None:
            raise LookupError("spline_ik needs a 'curve' source")
        curve_xf = self._require(curve["id"])
        shapes = (
            cmds.listRelatives(curve_xf, shapes=True, type="nurbsCurve", fullPath=True)
            or []
        )
        if not shapes:
            raise LookupError(f"{curve_xf} carries no NURBS curve")
        handle = cmds.ikHandle(
            startJoint=start,
            endEffector=end,
            solver="ikSplineSolver",
            curve=shapes[0],
            createCurve=False,
            parentCurve=False,
        )[0]
        self._track(handle)

    def _build_path(self, record: Any, params: Dict[str, Any]) -> None:
        driven = self._require(record.target["id"])
        curve = next((s for s in record.sources if s.get("role") == "curve"), None)
        if curve is None:
            raise LookupError("path needs a 'curve' source")
        path = cmds.pathAnimation(
            driven,
            curve=self._require(curve["id"]),
            fractionMode=True,
            follow=bool(params.get("follow", True)),
            followAxis=str(params.get("front_axis", "x")),
            upAxis=str(params.get("up_axis", "y")),
        )
        cmds.cutKey(f"{path}.uValue", clear=True)
        cmds.setAttr(f"{path}.uValue", self._literal(params.get("u", 0.0), 0.0))
        self._track(path)

    # ------------------------------------------------------------ keys
    def _detach_target_keys(self, record: Any) -> None:
        """Disconnect the payload's baked TIME curves from every channel *record*
        is about to drive, keeping them for :meth:`commit` / :meth:`remove`.
        Before the build, so the constraint or driver lands on a free plug (on a
        keyed one Maya inserts a ``pairBlend`` -- and ``mute`` after the fact
        would silence the built set-driven curve along with the payload's)."""
        plugs: List[str] = []
        if record.shape == "transform":
            target = record.target
            for node_id in target.get("chain") or [target.get("id")]:
                node = self._node(node_id)
                if node is None:
                    continue
                for channel in target.get("channels") or ():
                    plugs.extend(
                        f"{node}.{a}" for a in self._GROUP_ATTRS.get(channel, ())
                    )
        elif record.shape == "channel":
            plugs.append(self._plug(record.target))
        for plug in plugs:
            for curve_plug in (
                cmds.listConnections(
                    plug, source=True, destination=False, plugs=True, type="animCurve"
                )
                or []
            ):
                if cmds.nodeType(curve_plug.split(".")[0]) not in self._TIME_CURVES:
                    continue
                cmds.disconnectAttr(curve_plug, plug)
                self._muted.setdefault(self._current or "", []).append(
                    (curve_plug, plug)
                )


class RigGraphBuilder(_RigGraphBuilderInternal, ptk.HelpMixin):
    """Plan a RigGraph against Maya and build what the plan allows.

    Example:
        >>> result = RigGraphBuilder().build(graph_dict, cmds.ls(type="transform"))
        >>> result["built"], result["baked"]
        (['rec_001', ...], ['/rig/ghost', ...])
    """

    REGISTRY: Dict[str, str] = {
        "transform/blend": "_build_blend",
        "transform/aim": "_build_aim",
        "transform/ik": "_build_ik",
        "transform/spline_ik": "_build_spline_ik",
        "transform/path": "_build_path",
        "channel/linear": "_build_linear",
        "channel/curve": "_build_curve",
        "points/skin": "_build_skin",
    }

    @classmethod
    def capability(cls) -> Dict[str, Any]:
        """What Maya can build, as data the planner reads (section 9.2). Every
        op is ``approximate`` until a conformance fixture vouches for it; ``plugs``
        is empty because a driven weight is honestly baked, not guessed at."""
        approx = "approximate"
        return {
            "target": "maya",
            "target_version": str(cmds.about(version=True))
            if "cmds" in globals()
            else "",
            "schema_version": 1,
            "shapes": ["transform", "channel", "points"],
            "ops": {
                "transform/blend": {
                    "fidelity": approx,
                    "channels": ["translate", "rotate", "scale"],
                    "roles": ["space"],
                    "params": {
                        "compose": ["matrix", "independent"],
                        "maintain_offset": True,
                        "skip": True,
                    },
                    "plugs": [],
                },
                "transform/aim": {
                    "fidelity": approx,
                    "channels": ["rotate"],
                    "roles": ["target", "up"],
                    "params": {
                        "aim_axis": True,
                        "up_axis": True,
                        "up_ref.kind": ["axis", "object", "object_axis"],
                        "up_ref.role": True,
                        "up_ref.vector": True,
                        "up_ref.axis": True,
                    },
                    "plugs": [],
                },
                "transform/ik": {
                    "fidelity": approx,
                    "channels": ["rotate"],
                    "roles": ["goal", "pole"],
                    "params": {
                        "solver": ["rotate_plane", "single_chain", "two_bone"],
                        "twist": True,
                    },
                    "plugs": [],
                },
                "transform/spline_ik": {
                    "fidelity": approx,
                    "channels": ["rotate"],
                    "roles": ["curve", "up_start", "up_end"],
                    "params": {
                        "twist.distribution": ["linear"],
                        "twist.start": True,
                        "twist.end": True,
                    },
                    "plugs": [],
                },
                "transform/path": {
                    "fidelity": approx,
                    "channels": ["translate", "rotate"],
                    "roles": ["curve", "up"],
                    "params": {
                        "u": True,
                        "follow": True,
                        "front_axis": True,
                        "up_axis": True,
                        "bank": True,
                        "up_ref.kind": ["object"],
                        "up_ref.role": True,
                    },
                    "plugs": [],
                },
                "channel/linear": {
                    "fidelity": approx,
                    "params": {"scale": True, "offset": True},
                    "plugs": [],
                },
                "points/skin": {
                    "fidelity": approx,
                    "channels": [],
                    "roles": ["influence"],
                    # A MESH skin travels natively (USD skel / FBX skin -> a
                    # skinCluster) and is only CHECKED here; a curve, surface
                    # or lattice skin does not travel, so it is refused and its
                    # component bakes rather than driving a bake (schema 15.6).
                    "params": {"geometry": ["mesh"]},
                    "plugs": [],
                },
                "channel/curve": {
                    "fidelity": approx,
                    "params": {"points": True, "interp": ["linear", "bezier"]},
                    "plugs": [],
                },
            },
        }

    @contextmanager
    def scope(self):
        """Yield with the scene's current time restored afterwards.

        Verification samples the target at the source's frames, and a
        conversion that leaves the time slider moved has edited the user's
        scene for no reason. Maya evaluates a hidden node, so unlike Blender's
        there is nothing else to arrange here (schema 15.5).
        """
        current = cmds.currentTime(query=True)
        try:
            yield
        finally:
            cmds.currentTime(current)

    @staticmethod
    def linear_unit() -> str:
        """The scene's linear unit, as the verifier's vocabulary spells it."""
        return str(cmds.currentUnit(query=True, linear=True))

    @staticmethod
    def up_axis() -> str:
        """The scene's up axis (``"y"`` / ``"z"``)."""
        return str(cmds.upAxis(query=True, axis=True))

    def sample_world(
        self, node_id: str, frame: int
    ) -> Optional[Tuple[float, float, float]]:
        """The world position of the node *node_id* names at *frame*, or ``None``."""
        node = self._node(node_id)
        if node is None:
            return None
        if cmds.currentTime(query=True) != frame:
            cmds.currentTime(frame)
        x, y, z = cmds.xform(node, query=True, worldSpace=True, translation=True)
        return (float(x), float(y), float(z))

    def commit(self, record_id: str) -> int:
        """A verified record owns its channels: delete the payload's detached keys there."""
        curves = sorted(
            {cp.split(".")[0] for cp, _plug in self._muted.pop(record_id, [])}
        )
        curves = [c for c in curves if cmds.objExists(c)]
        if curves:
            cmds.delete(curves)
        return len(curves)

    def remove(self, record_id: str) -> int:
        """Take back everything :meth:`build` created for *record_id* and
        reconnect the keys it detached -- the demotion a failed verification
        applies; no motion is lost."""
        created = [n for n in self._created.pop(record_id, []) if cmds.objExists(n)]
        if created:
            cmds.delete(created)
        for curve_plug, plug in self._muted.pop(record_id, []):
            try:
                cmds.connectAttr(curve_plug, plug, force=True)
            except RuntimeError:
                continue
        return len(created)

    def build(
        self, graph: Dict[str, Any], nodes: Sequence[str], is_usd: bool = False
    ) -> Dict[str, Any]:
        """Plan *graph* against :meth:`capability` and build what it allows onto
        *nodes* (the transforms the payload import created). Returns ``built`` /
        ``baked`` / ``verify`` / ``report`` / ``plan`` like blendertk's."""
        from pythontk import RigCapability, RigGraph, RigPlanner

        rig = RigGraph.from_dict(graph)
        plan = RigPlanner.plan(rig, RigCapability.from_dict(self.capability()))
        self._index(nodes)
        self._created, self._muted = {}, {}
        built: List[str] = []
        baked: List[str] = list(plan.bake)
        report: List[Dict[str, Any]] = [e.to_dict() for e in plan.report]
        for record_id in plan.build:
            record = rig.record(record_id)
            if record is None:
                continue
            method = self.REGISTRY.get(f"{record.shape}/{record.op}")
            self._current = record_id
            try:
                if method is None:
                    raise LookupError(
                        f"no builder registered for {record.shape}/{record.op}"
                    )
                self._detach_target_keys(record)
                getattr(self, method)(record, dict(record.params))
            except Exception as error:  # noqa: BLE001 -- a build that fails is DATA
                self.remove(record_id)
                node_ids = list(record.target_ids())
                baked.extend(n for n in node_ids if n not in baked)
                report.append(
                    {
                        "kind": "failed",
                        "severity": "error",
                        "record": record_id,
                        "nodes": node_ids,
                        "reason": "builder_raised",
                        "detail": {"error": f"{type(error).__name__}: {error}"},
                        "recoverable": False,
                    }
                )
                continue
            built.append(record_id)
        self._current = None
        return {
            "built": built,
            "baked": baked,
            "verify": {rid: plan.verify[rid] for rid in built if rid in plan.verify},
            "report": report,
            "plan": plan.to_dict(),
            # Nothing this builder does to the scene outlives a demotion: the
            # detached key curves are reconnected by `remove`.
            "edits": [],
        }
