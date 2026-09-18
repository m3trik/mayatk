# !/usr/bin/python
# coding=utf-8
"""Read a Maya rig into a RigGraph -- phase 2 of the rig-transfer stack.

The schema (``.claude/RIG_GRAPH_SCHEMA.md``) carries what a rigger MEANT, not
Maya's node graph: a target receives a value computed by an operator from some
sources. This module is the Maya reader for that. Solver-level intent (a
constraint, an IK handle, a set-driven key) is read by one small method each;
single-node math is table-driven; and everything else that DRIVES the scene is
emitted as ``opaque`` rather than skipped.

That last rule is the one that matters. An omitted driver is indistinguishable
from "this node is free", so the consumer leaves its target STATIC instead of
baking it and the report says everything is fine -- a first cut of this exact
extractor read 43% of a production rig and the planner called it clean. So
the census is taken first (``source.census``: driver node type -> count, from
ONE ``ls`` per type), every record names what it was read FROM
(``provenance``), and ``RigGraph.coverage()`` -- not the plan's severity -- is
the acceptance test: zero unaccounted, on the production module, before this
is called done.

Identity is the payload's prim path: the DAG path with every segment spelled
through ``UsdUtils.sanitize_prim_name`` (``|rig|ns:ctrl`` -> ``/rig/ns_ctrl``),
the same rule the USD exporter applies and the FBX consumer tolerates by leaf.
No leaf-name matching, no namespace rewriting, no ``.001`` guessing: the
production module carries the same leaf twice with different vertex counts,
and paths are what keep them apart.

Two Maya facts this reader is shaped by, both measured on the fixture:
``ls -type`` returns SUBTYPES (a poleVectorConstraint answers to
pointConstraint and would be read twice, once as a phantom blend), so the
census filters on the exact ``nodeType``; and a set-driven key landing on an
already-constrained channel makes Maya insert a ``pairBlend``, so a driver's
destination is walked through Maya's own plumbing before it is called absent.

``import maya.cmds`` is try-guarded like every mayatk module, so the surface
resolves without Maya.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import maya.cmds as cmds
except ImportError as error:
    print(__file__, error)

import pythontk as ptk


class _RigGraphExtractorInternal:
    """Readers: one per driver shape, plus identity and census helpers."""

    #: Constraint node types and the channels each writes. Those the schema
    #: has no op for yet are still CENSUSED and emitted opaque (section 7.5).
    _BLEND_CONSTRAINTS: Dict[str, Tuple[str, ...]] = {
        "parentConstraint": ("translate", "rotate"),
        "pointConstraint": ("translate",),
        "orientConstraint": ("rotate",),
        "scaleConstraint": ("scale",),
    }
    _OPAQUE_CONSTRAINTS: Tuple[str, ...] = (
        "geometryConstraint",
        "normalConstraint",
        "tangentConstraint",
    )
    #: The ``offsetParentMatrix`` idiom -- the LARGEST driver population on the
    #: production module (196 multMatrix). Opaque in this cut: no target builds
    #: a matrix expression yet, and reported-and-baked beats unbuildable.
    _MATRIX_TYPES: Tuple[str, ...] = (
        "multMatrix",
        "blendMatrix",
        "decomposeMatrix",
        "composeMatrix",
        "aimMatrix",
        "wtAddMatrix",
        "pickMatrix",
        "inverseMatrix",
    )
    #: Single-node math the walk can state as a ``channel`` op.
    _MATH_TYPES: Tuple[str, ...] = (
        "multiplyDivide",
        "plusMinusAverage",
        "addDoubleLinear",
        "multDoubleLinear",
        "reverse",
        "condition",
        "clamp",
        "remapValue",
        "blendTwoAttr",
        "setRange",
        "blendColors",  # the spline-IK stretch idiom's mixer (21 on the production module)
    )
    #: Maya's own plumbing between a driver and the plug it moves. Neither a
    #: driver nor described, so never censused -- but always looked THROUGH.
    _PLUMBING: Tuple[str, ...] = ("unitConversion", "pairBlend", "blendWeighted")
    _SDK_TYPES: Tuple[str, ...] = (
        "animCurveUL",
        "animCurveUA",
        "animCurveUU",
        "animCurveUT",
    )
    #: SOLVER networks that drive a skeleton without a single constraint.
    #: HumanIK is Maya's OWN bundled character system, and a stock 63-joint
    #: character wires 567 joint plugs through ``HIKState2SK`` alone -- none of
    #: them a constraint, an ikHandle or a driven curve. Without these types the
    #: whole rig was invisible AND ``coverage()`` answered ``0 unaccounted``: a
    #: graph claiming completeness with the entire rig missing, which is the one
    #: failure section 7.5 exists to prevent. Opaque (reported, then baked)
    #: until some target can rebuild a HIK character.
    _SOLVER_TYPES: Tuple[str, ...] = (
        "HIKState2SK",
        "HIKState2FK",
        "HIKState2Effector",
        "HIKSolverNode",
        "HIKEffector2State",
        "HIKFK2State",
        "HIKPinning2State",
        "HIKProperty2State",
        "HIKEffectorFromCharacter",
    )
    #: Deformer BINDINGS, stated as ``points/skin`` records: which joints deform
    #: which geometry, never the weights (those travel with the carrier).
    #: Censused so a skin in the carrier's blind spot -- a skinned CURVE: on the
    #: production module the IK curve every loom's driver joints deform -- is an
    #: edge the component rule can see (schema 15.6).
    _DEFORMER_TYPES: Tuple[str, ...] = ("skinCluster",)
    _GEOMETRY_KIND: Dict[str, str] = {
        "mesh": "mesh",
        "nurbsCurve": "curve",
        "nurbsSurface": "surface",
        "lattice": "lattice",
    }
    _IK_SOLVERS: Dict[str, str] = {
        "ikRPsolver": "rotate_plane",
        "ikSCsolver": "single_chain",
        "ik2Bsolver": "two_bone",
    }
    _CHANNEL_OF: Dict[str, str] = {
        "translateX": "translate.x",
        "translateY": "translate.y",
        "translateZ": "translate.z",
        "rotateX": "rotate.x",
        "rotateY": "rotate.y",
        "rotateZ": "rotate.z",
        "scaleX": "scale.x",
        "scaleY": "scale.y",
        "scaleZ": "scale.z",
        "translate": "translate",
        "rotate": "rotate",
        "scale": "scale",
        "visibility": "visibility",
    }

    def __init__(self) -> None:
        self._nodes: Dict[str, Dict[str, Any]] = {}
        self._records: List[Dict[str, Any]] = []
        self._long: Dict[str, str] = {}
        self._inert: Dict[str, List[str]] = {}

    # ------------------------------------------------------------ identity
    def _long_name(self, node: str) -> str:
        if node not in self._long:
            found = cmds.ls(node, long=True) or [node]
            self._long[node] = found[0]
        return self._long[node]

    def _id(self, node: str) -> str:
        """The prim path the payload's exporter writes for *node* (section 3)."""
        from mayatk.env_utils.usd import UsdUtils

        segments = [s for s in self._long_name(node).split("|") if s]
        return "/" + "/".join(UsdUtils.sanitize_prim_name(s) for s in segments)

    def _plug(self, plug: str) -> str:
        """``node.attr`` -> ``<id>.<channel>`` in the neutral channel grammar."""
        node, _, attr = plug.partition(".")
        attr = attr.split("[")[0]
        return f"{self._id(node)}.{self._CHANNEL_OF.get(attr, attr)}"

    def _dag_owner(self, node: str) -> Optional[str]:
        """The transform a constraint drives (it is parented under it), or the
        node itself when it is a plain transform; None for a DG node."""
        if not cmds.objectType(node, isAType="transform"):
            return None
        if cmds.objectType(node, isAType="constraint"):
            # The constraint reads its driven node's parentInverseMatrix, so that
            # wire names the driven node whatever the constraint is parented under;
            # the DAG parent is the fallback for a constraint wired by hand.
            driven = cmds.listConnections(
                f"{node}.constraintParentInverseMatrix", source=True, destination=False
            )
            if driven:
                return self._long_name(driven[0])
            parent = cmds.listRelatives(node, parent=True, fullPath=True)
            return parent[0] if parent else None
        return self._long_name(node)

    # ---------------------------------------------------------------- nodes
    def _ensure_node(self, node: str) -> str:
        """Register *node* and every ancestor (section 6). Returns the id."""
        long_name = self._long_name(node)
        node_id = self._id(long_name)
        if node_id in self._nodes:
            return node_id
        parent = cmds.listRelatives(long_name, parent=True, fullPath=True)
        if parent:
            self._ensure_node(parent[0])
        self._nodes[node_id] = self._node_record(long_name, node_id)
        return node_id

    def _node_record(self, long_name: str, node_id: str) -> Dict[str, Any]:
        kind = "transform"
        if cmds.objectType(long_name, isAType="joint"):
            kind = "joint"
        elif cmds.objectType(long_name, isAType="ikHandle"):
            kind = "ik_handle"
        else:
            shapes = cmds.listRelatives(long_name, shapes=True, fullPath=True) or []
            if shapes:
                kind = {
                    "locator": "locator",
                    "nurbsCurve": "curve",
                    "mesh": "mesh",
                    "lattice": "lattice",
                }.get(cmds.nodeType(shapes[0]), "transform")
        rest: Dict[str, Any] = {}
        for attr in ("translate", "rotate", "scale"):
            try:
                rest[attr] = [float(v) for v in cmds.getAttr(f"{long_name}.{attr}")[0]]
            except (RuntimeError, ValueError, TypeError):
                pass
        try:
            orders = ("xyz", "yzx", "zxy", "xzy", "yxz", "zyx")
            rest["rotate_order"] = orders[cmds.getAttr(f"{long_name}.rotateOrder")]
        except (RuntimeError, ValueError, TypeError, IndexError):
            pass
        if kind == "joint":
            try:
                rest["joint_orient"] = [
                    float(v) for v in cmds.getAttr(f"{long_name}.jointOrient")[0]
                ]
            except (RuntimeError, ValueError, TypeError):
                pass
        return {"id": node_id, "path": long_name, "kind": kind, "rest": rest}

    def _ensure_attr(self, node: str, attr: str) -> None:
        """Record a custom attribute's UI (type, default, range) on its node."""
        node_id = self._ensure_node(node)
        if attr in self._CHANNEL_OF or not cmds.attributeQuery(
            attr, node=node, exists=True
        ):
            return
        entry: Dict[str, Any] = {
            "type": cmds.attributeQuery(attr, node=node, attributeType=True),
            "keyable": bool(cmds.getAttr(f"{node}.{attr}", keyable=True)),
        }
        for exists, query, key in (
            ("minExists", "minimum", "min"),
            ("maxExists", "maximum", "max"),
        ):
            try:
                if cmds.attributeQuery(attr, node=node, **{exists: True}):
                    entry[key] = cmds.attributeQuery(attr, node=node, **{query: True})[
                        0
                    ]
            except (RuntimeError, TypeError, IndexError):
                pass
        try:
            entry["default"] = cmds.attributeQuery(attr, node=node, listDefault=True)[0]
        except (RuntimeError, TypeError, IndexError):
            pass
        self._nodes[node_id].setdefault("attrs", {})[attr] = entry

    # -------------------------------------------------------------- records
    def _emit(
        self,
        shape: str,
        op: str,
        target: Any,
        sources: List[Dict[str, Any]],
        params: Dict[str, Any],
        provenance: List[str],
    ) -> Dict[str, Any]:
        record = {
            "id": f"rec_{len(self._records) + 1:03d}",
            "shape": shape,
            "op": op,
            "target": target,
            "sources": sources,
            "params": params,
            "provenance": provenance,
        }
        self._records.append(record)
        return record

    def _opaque(self, node: str, node_type: str, targets: Iterable[str]) -> None:
        ids = [self._ensure_node(t) for t in dict.fromkeys(t for t in targets if t)]
        if not ids:
            # Nothing in the DAG moves because of it: not a driver of the rig,
            # so it leaves the census rather than shipping a record the
            # validator refuses. ``source.inert`` keeps it visible, so a reader
            # can tell "unread" from "inert".
            self._inert.setdefault(node_type, []).append(node)
            return
        self._emit(
            "opaque",
            "opaque",
            {"ids": ids},
            [],
            {"origin": {"app": "maya", "node_type": node_type, "node": node}},
            [f"{node_type}:{node}"],
        )

    def _value_or_plug(self, plug: str) -> Any:
        """A literal, or ``{"plug": ...}`` when the attribute is driven (rule 9)."""
        sources = cmds.listConnections(plug, source=True, destination=False, plugs=True)
        if sources:
            node, _, attr = sources[0].partition(".")
            if cmds.objectType(node, isAType="dagNode"):
                self._ensure_attr(node, attr.split("[")[0])
                return {"plug": self._plug(sources[0])}
        value = cmds.getAttr(plug)
        if isinstance(value, list) and value and isinstance(value[0], tuple):
            return [float(v) for v in value[0]]
        return value

    def _dag_source(self, plug: str) -> Optional[str]:
        src = cmds.listConnections(plug, source=True, destination=False, plugs=True)
        if src and cmds.objectType(src[0].split(".")[0], isAType="dagNode"):
            return src[0]
        return None

    def _final_destination(self, plug: str) -> Optional[str]:
        """The ONE DAG plug *plug* feeds directly (through unit conversion only).

        A pairBlend or blendWeighted in the way means the plug is SHARED with
        another driver, so no single op states it: None, and the caller goes
        opaque with :meth:`_dag_destinations` naming what is frozen.
        """
        seen = set()
        while plug not in seen:
            seen.add(plug)
            dests = cmds.listConnections(
                plug, source=False, destination=True, plugs=True
            )
            if not dests:
                return None
            node = dests[0].split(".")[0]
            if cmds.nodeType(node) == "unitConversion":
                plug = f"{node}.output"
                continue
            return dests[0] if cmds.objectType(node, isAType="dagNode") else None
        return None

    def _dag_destinations(self, node: str) -> List[str]:
        """Every DAG node *node* reaches, looking through every DG driver and
        passthrough type -- a driver two utility nodes upstream still drives."""
        from mayatk.node_utils.attributes._attributes import Attributes

        through = (
            set(self._PLUMBING)
            | set(self._MATRIX_TYPES)
            | set(self._MATH_TYPES)
            | set(Attributes.PASSTHROUGH_TYPES)
        )
        out, frontier, seen = [], [node], set()
        while frontier:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            for dest in (
                cmds.listConnections(current, source=False, destination=True) or []
            ):
                if cmds.objectType(dest, isAType="dagNode"):
                    out.append(dest)
                elif cmds.nodeType(dest) in through:
                    # Through every DG driver type, not just plumbing: a
                    # multiplyDivide feeding a condition feeding the rig DRIVES
                    # it -- 35 such nodes on the production module read as
                    # inert when the walk stopped one node short.
                    frontier.append(dest)
        return list(dict.fromkeys(out))

    # ---- constraints ------------------------------------------------------
    def _read_blend(self, constraint: str, node_type: str) -> None:
        from mayatk.node_utils._node_utils import NodeUtils

        driven = self._dag_owner(constraint)
        targets = NodeUtils.get_constraint_targets(constraint)
        if not driven or not targets:
            self._opaque(constraint, node_type, [driven])
            return
        aliases = getattr(cmds, node_type)(constraint, query=True, weightAliasList=True)
        sources, offset_seen = [], False
        for index, (target, alias) in enumerate(zip(targets, aliases or [])):
            source: Dict[str, Any] = {
                "id": self._ensure_node(target),
                "role": "space",
                "weight": self._value_or_plug(f"{constraint}.{alias}"),
            }
            offset = self._target_offset(constraint, node_type, index)
            if offset:
                source["offset"], offset_seen = offset, True
            sources.append(source)
        skip = self._skipped(constraint, node_type, driven)
        self._emit(
            "transform",
            "blend",
            {
                "id": self._ensure_node(driven),
                "channels": list(self._BLEND_CONSTRAINTS[node_type]),
            },
            sources,
            {
                "compose": "matrix"
                if node_type == "parentConstraint"
                else "independent",
                "maintain_offset": offset_seen,
                **({"skip": skip} if skip else {}),
            },
            [f"{node_type}:{constraint}"],
        )

    def _target_offset(
        self, constraint: str, node_type: str, index: int
    ) -> Dict[str, Any]:
        offset: Dict[str, Any] = {}
        for attr, key in (
            ("targetOffsetTranslate", "translate"),
            ("targetOffsetRotate", "rotate"),
        ):
            plug = f"{constraint}.target[{index}].{attr}"
            if cmds.objExists(plug):
                values = [float(v) for v in cmds.getAttr(plug)[0]]
                if any(abs(v) > 1e-9 for v in values):
                    offset[key] = values
        neutral = {
            "pointConstraint": 0.0,
            "orientConstraint": 0.0,
            "scaleConstraint": 1.0,
        }
        if node_type in neutral and cmds.objExists(f"{constraint}.offset"):
            values = [float(v) for v in cmds.getAttr(f"{constraint}.offset")[0]]
            if any(abs(v - neutral[node_type]) > 1e-9 for v in values):
                offset[self._BLEND_CONSTRAINTS[node_type][0]] = values
        return offset

    def _skipped(
        self, constraint: str, node_type: str, driven: str
    ) -> Dict[str, List[str]]:
        """Channels the constraint does NOT write: an unconnected output axis."""
        skip: Dict[str, List[str]] = {}
        for channel in self._BLEND_CONSTRAINTS[node_type]:
            axes = []
            for axis in "XYZ":
                out = f"{constraint}.constraint{channel.capitalize()}{axis}"
                dest = (
                    cmds.listConnections(out, source=False, destination=True)
                    if cmds.objExists(out)
                    else None
                )
                if not dest or self._long_name(dest[0]) != driven:
                    axes.append(axis.lower())
            if axes and len(axes) < 3:
                skip[channel] = axes
        return skip

    def _read_aim(self, constraint: str) -> None:
        from mayatk.node_utils._node_utils import NodeUtils

        driven = self._dag_owner(constraint)
        targets = NodeUtils.get_constraint_targets(constraint)
        if not driven or not targets:
            self._opaque(constraint, "aimConstraint", [driven])
            return
        aliases = cmds.aimConstraint(constraint, query=True, weightAliasList=True) or []
        sources = [
            {
                "id": self._ensure_node(t),
                "role": "target",
                "weight": self._value_or_plug(f"{constraint}.{alias}"),
            }
            for t, alias in zip(targets, aliases)
        ]
        # worldUpType: 0 scene, 1 object, 2 objectrotation, 3 vector, 4 none.
        mode = cmds.getAttr(f"{constraint}.worldUpType")
        params: Dict[str, Any] = {
            "aim_axis": [float(v) for v in cmds.getAttr(f"{constraint}.aimVector")[0]],
            "up_axis": [float(v) for v in cmds.getAttr(f"{constraint}.upVector")[0]],
        }
        world_up = [float(v) for v in cmds.getAttr(f"{constraint}.worldUpVector")[0]]
        up_obj = cmds.listConnections(
            f"{constraint}.worldUpMatrix", source=True, destination=False
        )
        if mode in (1, 2) and up_obj:
            sources.append({"id": self._ensure_node(up_obj[0]), "role": "up"})
            params["up_ref"] = (
                {"kind": "object", "role": "up"}
                if mode == 1
                else {"kind": "object_axis", "role": "up", "axis": world_up}
            )
        elif mode == 0:
            params["up_ref"] = {"kind": "axis", "vector": [0.0, 1.0, 0.0]}
        elif mode == 3:
            params["up_ref"] = {"kind": "axis", "vector": world_up}
        self._emit(
            "transform",
            "aim",
            {"id": self._ensure_node(driven), "channels": ["rotate"]},
            sources,
            params,
            [f"aimConstraint:{constraint}"],
        )

    # ---- IK -----------------------------------------------------------------
    def _read_ik(self, handle: str) -> None:
        from mayatk.node_utils._node_utils import NodeUtils

        solver = cmds.listConnections(
            f"{handle}.ikSolver", source=True, destination=False
        )
        solver_type = cmds.nodeType(solver[0]) if solver else ""
        start = cmds.listConnections(
            f"{handle}.startJoint", source=True, destination=False
        )
        effector = cmds.listConnections(
            f"{handle}.endEffector", source=True, destination=False
        )
        end = (
            cmds.listConnections(
                f"{effector[0]}.translateX", source=True, destination=False
            )
            if effector
            else None
        )
        if not start or not end:
            self._opaque(handle, "ikHandle", [handle])
            return
        chain = self._chain(start[0], end[0])
        provenance = [f"ikHandle:{handle}"]
        sources: List[Dict[str, Any]] = []
        params: Dict[str, Any] = {}
        if solver_type == "ikSplineSolver":
            # No goal: a spline handle's own position is inert, the curve
            # drives. Emitting one made every consumer reject the record
            # (`unsupported_role`) before it could be built and measured.
            curve = cmds.listConnections(
                f"{handle}.inCurve", source=True, destination=False, shapes=True
            )
            if not curve:  # nothing drives the chain: not a relationship
                self._opaque(handle, "ikHandle", chain)
                return
            xf = cmds.listRelatives(curve[0], parent=True, fullPath=True) or curve
            sources.append({"id": self._ensure_node(xf[0]), "role": "curve"})
            params["twist"] = {
                "distribution": "linear",
                "start": self._value_or_plug(f"{handle}.roll"),
                "end": self._value_or_plug(f"{handle}.twist"),
            }
            op = "spline_ik"
        else:
            sources.append({"id": self._ensure_node(handle), "role": "goal"})
            params["solver"] = self._IK_SOLVERS.get(
                solver_type, solver_type or "unknown"
            )
            params["twist"] = self._value_or_plug(f"{handle}.twist")
            for pvc in (
                cmds.listRelatives(handle, type="poleVectorConstraint", fullPath=True)
                or []
            ):
                for pole in NodeUtils.get_constraint_targets(pvc):
                    sources.append({"id": self._ensure_node(pole), "role": "pole"})
                provenance.append(f"poleVectorConstraint:{pvc.rsplit('|', 1)[-1]}")
            op = "ik"
        self._emit(
            "transform",
            op,
            {"chain": [self._ensure_node(j) for j in chain], "channels": ["rotate"]},
            sources,
            params,
            provenance,
        )

    def _chain(self, start: str, end: str) -> List[str]:
        start_long, current, chain = self._long_name(start), self._long_name(end), []
        while current:
            chain.append(current)
            if current == start_long:
                break
            parent = cmds.listRelatives(
                current, parent=True, type="joint", fullPath=True
            )
            current = parent[0] if parent else None
        return list(reversed(chain))

    # ---- set-driven keys ----------------------------------------------------
    def _read_sdk(self, curve: str, node_type: str) -> None:
        driver = self._dag_source(f"{curve}.input")
        dest = self._final_destination(f"{curve}.output")
        if not driver or not dest:
            self._opaque(curve, node_type, self._dag_destinations(curve))
            return
        dnode, _, dattr = driver.partition(".")
        self._ensure_attr(dnode, dattr.split("[")[0])
        self._ensure_node(dest.split(".")[0])
        keys = cmds.keyframe(curve, query=True, floatChange=True) or []
        values = cmds.keyframe(curve, query=True, valueChange=True) or []
        tan_in = cmds.keyTangent(curve, query=True, inAngle=True) or []
        tan_out = cmds.keyTangent(curve, query=True, outAngle=True) or []
        points = [
            [float(k), float(v), float(ti), float(to)]
            for k, v, ti, to in zip(keys, values, tan_in, tan_out)
        ]
        self._emit(
            "channel",
            "curve",
            self._plug(dest),
            [{"plug": self._plug(driver), "role": "a"}],
            {"points": points, "interp": "bezier"},
            [f"{node_type}:{curve}"],
        )

    # ---- single-node math ---------------------------------------------------
    _MATH_OUT: Dict[str, str] = {
        "multiplyDivide": "outputX",
        "addDoubleLinear": "output",
        "multDoubleLinear": "output",
        "reverse": "outputX",
        "plusMinusAverage": "output1D",
    }

    def _read_math(self, node: str, node_type: str) -> None:
        """One math node -> the SIMPLEST channel op that states it (section
        7.2); anything richer is opaque in this cut, never silent."""
        out = self._MATH_OUT.get(node_type)
        dest = self._final_destination(f"{node}.{out}") if out else None
        params = self._linear_params(node, node_type)
        if dest is None or params is None:
            self._opaque(node, node_type, self._dag_destinations(node))
            return
        src, scale, offset = params
        snode, _, sattr = src.partition(".")
        self._ensure_attr(snode, sattr.split("[")[0])
        self._ensure_node(dest.split(".")[0])
        self._emit(
            "channel",
            "linear",
            self._plug(dest),
            [{"plug": self._plug(src), "role": "a"}],
            {"scale": scale, "offset": offset},
            [f"{node_type}:{node}"],
        )

    def _linear_params(
        self, node: str, node_type: str
    ) -> Optional[Tuple[str, float, float]]:
        """``(driving plug, scale, offset)`` when *node* is ``a * scale + offset``
        with exactly one DAG-driven input, else None."""
        pairs = {
            "multiplyDivide": ("input1X", "input2X", "mul"),
            "multDoubleLinear": ("input1", "input2", "mul"),
            "addDoubleLinear": ("input1", "input2", "add"),
        }
        if node_type == "reverse":
            a = self._dag_source(f"{node}.inputX")
            return (a, -1.0, 1.0) if a else None
        if node_type not in pairs:
            return None
        if node_type == "multiplyDivide" and cmds.getAttr(f"{node}.operation") != 1:
            return None
        first, second, kind = pairs[node_type]
        for driven_attr, const_attr in ((first, second), (second, first)):
            a = self._dag_source(f"{node}.{driven_attr}")
            wired = cmds.listConnections(
                f"{node}.{const_attr}", source=True, destination=False
            )
            if a and not wired:
                constant = float(cmds.getAttr(f"{node}.{const_attr}"))
                return (a, constant, 0.0) if kind == "mul" else (a, 1.0, constant)
        return None

    # ---- matrix graphs, expressions, motion paths ---------------------------
    def _read_matrix(self, node: str, node_type: str) -> None:
        self._opaque(node, node_type, self._dag_destinations(node))

    def _read_expression(self, node: str) -> None:
        self._opaque(node, "expression", self._dag_destinations(node))

    def _read_motion_path(self, node: str) -> None:
        dests = self._dag_destinations(node)
        curve = cmds.listConnections(
            f"{node}.geometryPath", source=True, destination=False, shapes=True
        )
        if not dests or not curve:
            self._opaque(node, "motionPath", dests)
            return
        xf = cmds.listRelatives(curve[0], parent=True, fullPath=True) or curve
        sources = [{"id": self._ensure_node(xf[0]), "role": "curve"}]
        params: Dict[str, Any] = {
            "u": self._value_or_plug(f"{node}.uValue"),
            "follow": bool(cmds.getAttr(f"{node}.follow")),
            "front_axis": "xyz"[cmds.getAttr(f"{node}.frontAxis")],
            "up_axis": "xyz"[cmds.getAttr(f"{node}.upAxis")],
            "bank": bool(cmds.getAttr(f"{node}.bank")),
        }
        mode = cmds.getAttr(f"{node}.worldUpType")
        up_obj = cmds.listConnections(
            f"{node}.worldUpMatrix", source=True, destination=False
        )
        if mode in (1, 2) and up_obj:
            sources.append({"id": self._ensure_node(up_obj[0]), "role": "up"})
            params["up_ref"] = {"kind": "object", "role": "up"}
        self._emit(
            "transform",
            "path",
            {"id": self._ensure_node(dests[0]), "channels": ["translate", "rotate"]},
            sources,
            params,
            [f"motionPath:{node}"],
        )

    # ---- deformer bindings --------------------------------------------------
    def _read_skin(self, node: str) -> None:
        """A skinCluster -> one ``points/skin`` record per bound geometry: the
        BINDING (which joints deform which shape), not the weights -- those
        travel with the carrier. It exists so the deformer edge is a graph
        edge: on the production module every loom's driver joints reach the
        deform chain only through the skin on its IK curve, and without this
        record their controls planned as a complete rig that drove a bake
        (schema 15.6)."""
        geometry = cmds.skinCluster(node, query=True, geometry=True) or []
        influences = cmds.skinCluster(node, query=True, influence=True) or []
        owners: List[Tuple[str, str]] = []
        for shape in geometry:
            parent = cmds.listRelatives(shape, parent=True, fullPath=True)
            owners.append((parent[0] if parent else shape, cmds.nodeType(shape)))
        if not owners or not influences:
            self._opaque(node, "skinCluster", [o for o, _kind in owners])
            return
        sources = [
            {"id": self._ensure_node(joint), "role": "influence"}
            for joint in influences
        ]
        for owner, shape_type in owners:
            self._emit(
                "points",
                "skin",
                {"id": self._ensure_node(owner), "points": "all"},
                [dict(src) for src in sources],
                {"geometry": self._GEOMETRY_KIND.get(shape_type, shape_type)},
                [f"skinCluster:{node}"],
            )

    # ------------------------------------------------------------- census
    def _census_types(self) -> Tuple[str, ...]:
        return (
            tuple(self._BLEND_CONSTRAINTS)
            + ("aimConstraint", "poleVectorConstraint")
            + self._OPAQUE_CONSTRAINTS
            + ("ikHandle", "motionPath", "expression")
            + self._SDK_TYPES
            + self._MATH_TYPES
            + self._MATRIX_TYPES
            + self._DEFORMER_TYPES
            + self._SOLVER_TYPES
        )

    def _census(self) -> Dict[str, List[str]]:
        """``{node_type: [nodes]}`` for every driver type present -- ONE ``ls``
        per type, filtered to the EXACT type (``ls -type`` returns subtypes).
        SDK curves are those with an INPUT wire; a keyframe curve of the same
        type is time-driven, not a driver."""
        found: Dict[str, List[str]] = {}
        for node_type in self._census_types():
            # A type this Maya does not know (an unloaded plugin's, and every
            # `_SOLVER_TYPES` entry is one) is not an error: `ls -type` answers
            # EMPTY for an unregistered type rather than raising -- measured
            # against a bogus name with no plugin loaded -- so a census entry
            # costs nothing on a scene that cannot contain it.
            nodes = [
                n
                for n in cmds.ls(type=node_type) or []
                if cmds.nodeType(n) == node_type
            ]
            if node_type in self._SDK_TYPES:
                nodes = [
                    n
                    for n in nodes
                    if cmds.listConnections(
                        f"{n}.input", source=True, destination=False
                    )
                ]
            if nodes:
                found[node_type] = nodes
        return found


class RigGraphExtractor(_RigGraphExtractorInternal, ptk.HelpMixin):
    """Extract a Maya scene's rig logic into a RigGraph document (plain dict).

    Example:
        >>> data = RigGraphExtractor().extract()
        >>> graph = ptk.RigGraph.from_dict(data)
        >>> graph.validate(), graph.coverage()["unaccounted"]
        ([], 0)
    """

    def extract(self, objects: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """Read the scene's drivers into a RigGraph envelope.

        Parameters:
            objects: Reserved for scoping to a selection; the whole scene is
                read today, because a driver outside a selection still moves
                what is inside it and a partial census cannot promise coverage.

        Returns:
            dict: A ``RigGraph`` document -- ``version``, ``source`` (with the
                ``census`` and any ``inert`` utility nodes), ``nodes`` and
                ``records``. Every censused driver appears in some record's
                ``provenance``.
        """
        self._nodes.clear()
        self._records.clear()
        self._long.clear()
        self._inert.clear()
        census = self._census()
        readers = {
            **{
                t: (lambda n, t=t: self._read_blend(n, t))
                for t in self._BLEND_CONSTRAINTS
            },
            "aimConstraint": self._read_aim,
            "ikHandle": self._read_ik,
            "motionPath": self._read_motion_path,
            "expression": self._read_expression,
            "skinCluster": self._read_skin,
            **{t: (lambda n, t=t: self._read_sdk(n, t)) for t in self._SDK_TYPES},
            **{t: (lambda n, t=t: self._read_math(n, t)) for t in self._MATH_TYPES},
            **{t: (lambda n, t=t: self._read_matrix(n, t)) for t in self._MATRIX_TYPES},
            **{
                t: (lambda n, t=t: self._opaque(n, t, [self._dag_owner(n)]))
                for t in self._OPAQUE_CONSTRAINTS
            },
            # A solver states what it MOVES; `_opaque` drops the ones that move
            # nothing to `source.inert`, so the state network's bookkeeping
            # nodes never ship a record while the writer that drives the
            # skeleton always does.
            **{
                t: (lambda n, t=t: self._opaque(n, t, self._dag_destinations(n)))
                for t in self._SOLVER_TYPES
            },
        }
        for node_type, nodes in census.items():
            if node_type == "poleVectorConstraint":
                continue  # read by the IK handle it serves (section 7.1: no pole op)
            for node in nodes:
                readers[node_type](node)
        # A pole constraint no IK reader reached is still a driver.
        accounted = {p for r in self._records for p in r["provenance"]}
        for pvc in census.get("poleVectorConstraint", []):
            if f"poleVectorConstraint:{pvc}" not in accounted:
                self._opaque(pvc, "poleVectorConstraint", [self._dag_owner(pvc)])
        start = cmds.playbackOptions(query=True, animationStartTime=True)
        end = cmds.playbackOptions(query=True, animationEndTime=True)
        counts = {t: len(n) - len(self._inert.get(t, ())) for t, n in census.items()}
        return {
            "version": 1,
            "source": {
                "app": "maya",
                "app_version": str(cmds.about(version=True)),
                "up_axis": str(cmds.upAxis(query=True, axis=True)),
                "linear_unit": cmds.currentUnit(query=True, linear=True),
                "angular_unit": "deg",
                "time_unit": cmds.currentUnit(query=True, time=True),
                "frame_range": [float(start), float(end)],
                "rest_frame": float(cmds.currentTime(query=True)),
                "census": {t: c for t, c in counts.items() if c > 0},
                "inert": dict(self._inert),
            },
            "nodes": list(self._nodes.values()),
            "records": list(self._records),
        }
