# !/usr/bin/python
# coding=utf-8
"""Articulated Rig engine -- rigid parts on hinge, swivel, ball and slide joints.

For props that move like a desk lamp or a magnifier arm: every part is rigid,
and it is WHERE the parts meet, and how, that the rig is about. The engine
builds four things from one plan:

- **a skeleton that ships**: one joint per moving part, the part parented
  under it, the rest pose in ``jointOrient`` so every channel reads 0 at rest.
  It is the deliverable -- Unity and the WebXR runtime pose these joints --
  so nothing on it is apparatus, and every joint has a part below it (the
  export's rig-helper sweep keeps an ancestor of content);
- **FK controls**, nested, one per joint, whose channels ARE the joint's: a
  hinge control keys ``rz`` and that value reaches the joint's ``rz``. What an
  animator keys is what an engine plays back and what a grab writes
  (``ptk.ArticulationModel`` works in the same numbers);
- **an end control** on the end link (the magnifier's lens): move it and the
  stand follows, solved by the same model the Grab Tool and the runtimes run
  -- written out as a MEL expression (:class:`SolverExpression`), so it works
  in any Maya that opens the scene, with nothing to install. The solve ADDS
  to the FK values: FK is its seed and the rig's base layer;
- **the ``articulation`` record** (``ptk.SceneRecords.ARTICULATION``, produced
  at export by :meth:`ArticulatedRig.export_record`) that tells the runtimes
  each joint's rest frame, channels and limits, and which parts a hand grabs.

The plan comes from the parts' geometry (:meth:`ArticulatedRig.analyze` over
``ptk.ArticulationAnalysis``) or from the caller; either way it is stored on
the rig group (:attr:`ArticulatedRig.DATA_ATTR`) in the group's own space, and
every edit after the build -- a joint inserted, removed or retyped, the end
control added or dropped -- is an edit of that plan and a rebuild that carries
the animation across (:meth:`ArticulatedRig.rebuild`). A joint inserted at 0
changes no pose, so the keys already made stay valid
(:meth:`ArticulatedRig.insert_joint`).
"""

from __future__ import annotations

import copy
import json
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import maya.api.OpenMaya as om
    import maya.cmds as cmds
except ImportError:  # the surface imports without Maya (registry, docs, mock tests)
    cmds = om = None

import pythontk as ptk

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.node_utils.attributes._attributes import Attributes
from mayatk.rig_utils.articulated_rig._solver_expression import SolverExpression
from mayatk.rig_utils.controls import Controls
from mayatk.xform_utils.matrices import Matrices


class _ArticulatedRigInternal:
    """Scene plumbing behind :class:`ArticulatedRig`: node lookup, shells,
    frames and the part records."""

    #: A part's own channels, captured before the build reparents it and put
    #: back exactly by the teardown.
    PART_ATTRS: Tuple[str, ...] = (
        "translate",
        "rotate",
        "scale",
        "shear",
        "rotateAxis",
        "rotatePivot",
        "scalePivot",
        "rotatePivotTranslate",
        "scalePivotTranslate",
        "rotateOrder",
    )
    #: The channels a reparent rewrites (unlocked around it, relocked after).
    MOVED_ATTRS: Tuple[str, ...] = (
        "tx",
        "ty",
        "tz",
        "rx",
        "ry",
        "rz",
        "sx",
        "sy",
        "sz",
    )
    #: Maya's rotate orders, in ``rotateOrder`` enum order.
    ROTATE_ORDERS: Tuple[str, ...] = ("xyz", "yzx", "zxy", "xzy", "yxz", "zyx")

    @staticmethod
    def _long(node) -> Optional[str]:
        found = cmds.ls(str(node), long=True) if node else []
        return found[0] if found else None

    @staticmethod
    def _uuid(node) -> Optional[str]:
        found = cmds.ls(str(node), uuid=True) if node else []
        return found[0] if found else None

    @staticmethod
    def _by_uuid(uuid: Optional[str]) -> Optional[str]:
        found = cmds.ls(uuid, long=True) if uuid else []
        return found[0] if found else None

    @staticmethod
    def _leaf(node: str) -> str:
        return CoreUtils.leaf_name(node)

    @staticmethod
    def _parent(node: str) -> Optional[str]:
        return NodeUtils.get_parent(node, type=None, full_path=True)

    @staticmethod
    def _matrix(node: Optional[str]) -> "om.MMatrix":
        """*node*'s world matrix; identity for the world itself (None)."""
        if not node:
            return om.MMatrix()
        return om.MMatrix(cmds.getAttr(f"{node}.worldMatrix[0]"))

    @staticmethod
    def _frame(position, aim, normal) -> "om.MMatrix":
        """A rest frame: X along *aim*, Z the *normal* made perpendicular to
        it, Y completing a right-handed set, at *position* (Maya's row
        vectors: the rows are the axes, then the position).

        Raises:
            ValueError: *normal* runs along *aim* (no hinge axis left).
        """
        x = om.MVector(*aim)
        if x.length() < 1.0e-9:
            raise ValueError(f"Joint at {list(position)} has no aim.")
        x.normalize()
        z = om.MVector(*normal)
        z = z - x * (z * x)
        if z.length() < 1.0e-9:
            raise ValueError(
                f"Joint normal {list(normal)} runs along its aim {list(aim)}."
            )
        z.normalize()
        y = z ^ x
        return om.MMatrix(
            [
                x.x,
                x.y,
                x.z,
                0.0,
                y.x,
                y.y,
                y.z,
                0.0,
                z.x,
                z.y,
                z.z,
                0.0,
                float(position[0]),
                float(position[1]),
                float(position[2]),
                1.0,
            ]
        )

    @staticmethod
    def _decompose(m: "om.MMatrix") -> Tuple[List[float], List[float], List[float]]:
        """``(translation, joint orient xyz degrees, rotation xyzw)`` of a
        rigid matrix."""
        xf = om.MTransformationMatrix(m)
        t = xf.translation(om.MSpace.kTransform)
        e = xf.rotation(asQuaternion=False)
        e.reorderIt(om.MEulerRotation.kXYZ)
        q = xf.rotation(asQuaternion=True)
        return (
            [t.x, t.y, t.z],
            [om.MAngle(v).asDegrees() for v in (e.x, e.y, e.z)],
            [q.x, q.y, q.z, q.w],
        )

    @staticmethod
    def _shells(node: str) -> List[List[Tuple[float, float, float]]]:
        """World-space points of every connected piece of geometry under
        *node* (its own mesh and its descendants'), one list per shell.

        Joined through the faces' vertex lists -- two vertices of one face
        are one shell -- so a shell is Maya's own face island, read without
        touching the selection.
        """
        out: List[List[Tuple[float, float, float]]] = []
        shapes = (
            cmds.listRelatives(node, allDescendents=True, type="mesh", fullPath=True)
            or []
        )
        for shape in shapes:
            if cmds.getAttr(f"{shape}.intermediateObject"):
                continue
            sel = om.MSelectionList()
            sel.add(shape)
            fn = om.MFnMesh(sel.getDagPath(0))
            points = fn.getPoints(om.MSpace.kWorld)
            counts, vertices = fn.getVertices()
            parent = list(range(len(points)))

            def find(v: int) -> int:
                while parent[v] != v:
                    parent[v] = parent[parent[v]]
                    v = parent[v]
                return v

            offset = 0
            for count in counts:
                root = find(vertices[offset])
                for v in vertices[offset + 1 : offset + count]:
                    other = find(v)
                    if other != root:
                        parent[other] = root
                offset += count
            groups: Dict[int, List[Tuple[float, float, float]]] = {}
            for v, p in enumerate(points):
                groups.setdefault(find(v), []).append((p.x, p.y, p.z))
            out.extend(groups.values())
        return out

    @classmethod
    def _expand(cls, nodes) -> List[str]:
        """The parts *nodes* names: as given or -- one group picked -- the
        transforms under it that carry geometry, descending through
        single-child wrappers (``GRP > LOC > asset``) to the level holding
        several."""
        parts = [p for p in (cls._long(n) for n in CoreUtils.as_strings(nodes)) if p]
        while len(parts) == 1:
            children = [
                c
                for c in cmds.listRelatives(
                    parts[0], children=True, type="transform", fullPath=True
                )
                or []
                if cmds.listRelatives(
                    c, allDescendents=True, type="mesh", fullPath=True
                )
            ]
            if not children:
                break
            parts = children
        return parts

    @staticmethod
    def _common_parent(nodes: Sequence[str]) -> Optional[str]:
        """The deepest node every one of *nodes* sits under; None: the world."""
        paths = [n.split("|")[1:] for n in nodes]
        shared: List[str] = []
        for level in zip(*paths):
            if len(set(level)) != 1:
                break
            shared.append(level[0])
        # A node is not its own parent: stop above the shallowest one.
        shared = shared[: min(len(p) for p in paths) - 1]
        return "|" + "|".join(shared) if shared else None

    @classmethod
    def _capture(cls, node: str) -> Dict[str, Any]:
        values = {}
        for attr in cls.PART_ATTRS:
            value = cmds.getAttr(f"{node}.{attr}")
            values[attr] = list(value[0]) if isinstance(value, list) else value
        return values

    @classmethod
    def _restore(cls, node: str, values: Dict[str, Any]) -> None:
        with Attributes.temporarily_unlock(node, list(cls.MOVED_ATTRS)):
            for attr, value in values.items():
                if isinstance(value, list):
                    cmds.setAttr(f"{node}.{attr}", *value, type="double3")
                else:
                    cmds.setAttr(f"{node}.{attr}", value)

    @staticmethod
    def _driven(node: str) -> List[str]:
        """The transform channels of *node* something drives -- keys, a
        constraint, an expression. Such a part cannot be rigged: its own
        channels move it, and a rig reparents across them."""
        driven = []
        for attr in (
            "translate",
            "rotate",
            "scale",
            "tx",
            "ty",
            "tz",
            "rx",
            "ry",
            "rz",
            "sx",
            "sy",
            "sz",
        ):
            if cmds.listConnections(f"{node}.{attr}", source=True, destination=False):
                driven.append(attr)
        return driven

    @staticmethod
    def _points(nodes: Sequence[str]) -> List[Tuple[float, float, float]]:
        return [p for n in nodes for s in _ArticulatedRigInternal._shells(n) for p in s]

    @staticmethod
    def _cvs(ctrl: str) -> List[Tuple[str, List[Tuple[float, float, float]]]]:
        """``(shape, object-space CV positions)`` per curve shape of *ctrl*."""
        out = []
        for shape in (
            cmds.listRelatives(ctrl, shapes=True, type="nurbsCurve", fullPath=True)
            or []
        ):
            out.append((shape, [tuple(p) for p in cmds.getAttr(f"{shape}.cv[*]")]))
        return out

    @classmethod
    def _on_top(cls, ctrl: str, width: Optional[float] = None) -> None:
        """Draw *ctrl* over the geometry -- a control posed into a part stays
        in sight and in reach -- optionally heavier."""
        for shape, _cvs in cls._cvs(ctrl):
            cmds.setAttr(f"{shape}.alwaysDrawOnTop", True)
            if width is not None:
                cmds.setAttr(f"{shape}.lineWidth", width)

    @classmethod
    def _reshape(cls, ctrl: str, fn) -> None:
        """Move every CV of *ctrl*'s shapes to ``fn(x, y, z)`` (object space)."""
        for shape, points in cls._cvs(ctrl):
            for index, point in enumerate(points):
                cmds.xform(
                    f"{shape}.cv[{index}]",
                    objectSpace=True,
                    translation=fn(*point),
                )

    @staticmethod
    def _place(node: str, world: "om.MMatrix") -> None:
        """Put *node* at the *world* matrix through its translate and the
        rotate channels it leaves free -- unlike
        ``Matrices.bake_world_matrix_to_transform``, a locked channel (the
        scale, a hinged end's turn) is left as it is."""
        local = world * om.MMatrix(cmds.getAttr(f"{node}.parentInverseMatrix[0]"))
        t, rotation, _scale = Matrices.decompose(
            local, rotate_order=cmds.getAttr(f"{node}.rotateOrder", asString=True)
        )
        cmds.setAttr(f"{node}.translate", *t, type="double3")
        for axis, value in zip("XYZ", rotation):
            plug = f"{node}.rotate{axis}"
            if cmds.getAttr(plug, settable=True):
                cmds.setAttr(plug, value)


class ArticulatedRig(ptk.LoggingMixin, _ArticulatedRigInternal):
    """One articulated rig in the scene, bound to its rig group.

    Build one with :meth:`create` (from the parts, joints proposed by
    :meth:`analyze` unless given); find one again with :meth:`for_node` or
    :meth:`scene_rigs`.

    Parameters:
        group: The rig group (``<name>_RIG``) carrying :attr:`DATA_ATTR`.

    Raises:
        ValueError: *group* is not an articulated rig group, or carries a
            record copied from another rig (a duplicate).
    """

    #: The plan the rig was built from, JSON on the rig group, in its space.
    DATA_ATTR = "articulatedRigData"
    #: Message links from the rig group to every DG node the build made
    #: (utility nodes, the solver, controller tags, the controls set):
    #: ownership by connection, so a teardown deletes exactly those, however
    #: renamed.
    NODES_ATTR = "articulatedRigNodes"
    #: Message links from the rig group to its end control and its solver.
    END_ATTR = "articulatedRigEnd"
    SOLVER_ATTR = "articulatedRigSolver"
    #: The end control's solver is an expression node: core Maya, so the
    #: scene needs nothing installed to move.
    SOLVER_TYPE = "expression"
    #: The plug-in an earlier build solved with; a scene still requiring it
    #: is repaired (:meth:`ensure_solver`) and the requirement dropped.
    LEGACY_SOLVER_PLUGIN = "mtk_articulation_solver"
    #: The end control's switches: how much the solve adds over FK (0 = FK
    #: only), and whether its rotation turns a ball-mounted end link.
    BLEND_ATTR = "ikBlend"
    FOLLOW_ATTR = "followRotation"
    #: The parked-animation key of the end control's channels (a joint id is
    #: a node name, which never holds ``<``).
    END_KEY = "<end>"
    #: The plan's schema.
    VERSION = 1
    JOINT_TYPES = ptk.ArticulationAnalysis.JOINT_TYPES
    #: Control preset and its normal axis per joint type: a hinge's ring lies
    #: in the plane it turns in, a swivel's around the link, a slide's arrow
    #: in the arm's plane, along the travel.
    CONTROL_SHAPES: Dict[str, Tuple[str, str]] = {
        "hinge": ("circle", "z"),
        "swivel": ("circle", "x"),
        "universal": ("target", "y"),
        "ball": ("ball", "y"),
        "slide": ("two_way_arrow", "z"),
    }
    #: Maya override colours per joint type, and the end control's.
    CONTROL_COLORS: Dict[str, int] = {
        "hinge": 17,
        "swivel": 18,
        "universal": 18,
        "ball": 6,
        "slide": 14,
    }
    END_COLOR = 13
    #: Each FK control of a rig with an end control sits under a group (its
    #: name this suffix on the control's) that the solver turns, or slides,
    #: by that joint's IK offset: the controls ride their joints when the end
    #: control moves the arm, instead of staying where FK left them.
    IK_GROUP_SUFFIX = "_IK"
    #: A control's size over its link's body radius.
    CONTROL_SCALE = 2.5
    #: How far a control riding a link's axis keeps clear of the geometry
    #: there, over that geometry's reach from the axis: a slide's arrow floats
    #: above its tubes, a swivel's ring around its housing (measured on the
    #: production magnifier: the telescope's arrow, sized from the inner rod
    #: and centred on the axis, sat 87% inside the outer tube).
    CLEARANCE = 1.25
    #: The end control's box keeps this margin around the end link (a
    #: fraction of the link's largest extent) and draws this much heavier.
    END_MARGIN = 0.08
    END_LINE_WIDTH = 2.0
    #: Passes a drag of the end control may take to land the held point
    #: (:meth:`_settle_end`): a ball-mounted end lands in none, a hinged end
    #: settles in two or three.
    END_DRAG_PASSES = 4
    #: Adjusting the pivots (:meth:`begin_adjust`): the message link from the
    #: rig group to the group holding the handles, the string each handle
    #: carries naming what it places (a joint id, or :attr:`END_KEY`), and a
    #: handle's name ending.
    ADJUST_ATTR = "articulatedRigAdjust"
    PIVOT_ATTR = "articulatedRigPivot"
    HANDLE_SUFFIX = "_pivot"
    #: A handle's axes in Maya's own colours (X red, Y green, Z blue), and
    #: their length over its joint's control size.
    AXIS_COLORS = (13, 14, 6)
    HANDLE_SCALE = 2.0
    #: A handle closer than this to where it started moved nothing (a length
    #: in the rig's units, or an axis component).
    ADJUST_EPS = 1.0e-6

    def __init__(self, group: str):
        path = self._long(group)
        if not path or not cmds.attributeQuery(self.DATA_ATTR, node=path, exists=True):
            raise ValueError(f"{group!r} is not an articulated rig group.")
        data = self.scene_data(path) or {}
        self._group_uuid = self._uuid(path)
        stamped = data.get("group_uuid")
        if stamped and stamped != self._group_uuid:
            # cmds.duplicate copies the record verbatim, parts' uuids and all:
            # acting on it would tear the ORIGINAL's parts out of their rig.
            raise ValueError(
                f"{group!r} carries another rig's record (a duplicate?); rebuild "
                "it from its parts instead."
            )
        #: The plan's name: unique in the scene, and kept by every rebuild.
        self._name = data.get("name")

    # ================================================================ lookup
    @property
    def group(self) -> str:
        """The rig group. A rebuild makes a new one: rebuilt through another
        instance (the panel, a second handle), this one finds it again by the
        plan's name.

        Raises:
            RuntimeError: The rig is gone (torn down, or deleted).
        """
        path = self._by_uuid(self._group_uuid)
        if not path and self._name:
            rebuilt = next(
                (r for r in self.scene_rigs() if r.spec.get("name") == self._name),
                None,
            )
            if rebuilt is not None:
                self._group_uuid = rebuilt._group_uuid
                path = self._by_uuid(self._group_uuid)
        if not path:
            raise RuntimeError("The rig group no longer exists.")
        return path

    @property
    def spec(self) -> Dict[str, Any]:
        """The stamped plan (a fresh copy): name, links, joints, parts, and
        the end control's placement (``"end"``) when it has one."""
        return self.scene_data(self.group) or {}

    @property
    def name(self) -> str:
        return self.spec.get("name") or self._leaf(self.group)

    @classmethod
    def scene_data(cls, node) -> Optional[Dict[str, Any]]:
        """The :attr:`DATA_ATTR` plan on *node*, or None."""
        node = cls._long(node)
        if not node or not cmds.attributeQuery(cls.DATA_ATTR, node=node, exists=True):
            return None
        try:
            data = json.loads(cmds.getAttr(f"{node}.{cls.DATA_ATTR}") or "")
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _stamp(self, spec: Dict[str, Any]) -> None:
        cmds.setAttr(
            f"{self.group}.{self.DATA_ATTR}",
            json.dumps(spec, sort_keys=True),
            type="string",
        )

    @classmethod
    def scene_rigs(cls) -> List["ArticulatedRig"]:
        """Every articulated rig in the scene (duplicated records skipped)."""
        rigs = []
        stamped = cmds.ls(
            f"*.{cls.DATA_ATTR}", objectsOnly=True, long=True, recursive=True
        )
        for group in stamped or []:
            try:
                rigs.append(cls(group))
            except ValueError:
                continue
        return rigs

    @classmethod
    def for_node(cls, node) -> Optional["ArticulatedRig"]:
        """The rig *node* belongs to -- its group, a joint, a control, the end
        control, a part riding a joint, or a part of the fixed root link -- or
        None."""
        path = cls._long(node)
        while path:
            if cmds.attributeQuery(cls.DATA_ATTR, node=path, exists=True):
                try:
                    return cls(path)
                except ValueError:
                    return None
            path = cls._parent(path)
        uuid = cls._uuid(node)
        for rig in cls.scene_rigs():
            if any(
                m.get("uuid") == uuid
                for link in rig.spec.get("links", [])
                for m in link
            ):
                return rig
        return None

    def _linked(self, attr: str) -> Optional[str]:
        """The node wired into the group's message *attr*, or None."""
        group = self.group
        if not cmds.attributeQuery(attr, node=group, exists=True):
            return None  # a rig built before the end control existed
        found = cmds.listConnections(f"{group}.{attr}", source=True, destination=False)
        return self._long(found[0]) if found else None

    @property
    def end_control(self) -> Optional[str]:
        """The end control, or None (built without one)."""
        return self._linked(self.END_ATTR)

    @property
    def solver(self) -> Optional[str]:
        """The end control's solver (its expression node), or None -- no end
        control, or a rig an earlier build solved with a plug-in node, until
        :meth:`ensure_solver` replaces it."""
        node = self._linked(self.SOLVER_ATTR)
        return node if node and cmds.nodeType(node) == self.SOLVER_TYPE else None

    def ik_blend(self) -> float:
        """How much the end control's solve adds over FK: 1 places the end
        link on the end control, 0 leaves the FK pose (no end control: 0)."""
        end = self.end_control
        if not end or not self.solver:
            return 0.0
        return float(cmds.getAttr(f"{end}.{self.BLEND_ATTR}"))

    # ============================================================== analysis
    @classmethod
    def analyze(cls, nodes, root=None, ordered: bool = False) -> Dict[str, Any]:
        """Propose a rig for *nodes*: one link per part, and how and where
        each joins the next.

        Parameters:
            nodes: The parts (transforms carrying geometry), or one group
                holding them.
            root: The part that stays put; default the lowest.
            ordered: Take *nodes* as the chain, root first (a selection
                order), instead of walking the parts' contacts.

        Returns:
            A plan :meth:`create` builds: ``{"links": [[part], ...],
            "joints": [{"link", "parent", "type", "position", "aim",
            "normal", "limits", "size", "reason"}, ...], "unreached":
            [part, ...]}`` -- world space, link 0 the root.
        """
        parts = cls._expand(nodes)
        if len(parts) < 2:
            raise ValueError("An articulated rig needs at least two parts.")
        return cls._propose([[p] for p in parts], root=root, ordered=ordered)

    @classmethod
    def _propose(
        cls, links: Sequence[Sequence[str]], root=None, ordered: bool = False
    ) -> Dict[str, Any]:
        facts = [
            {
                "name": cls._leaf(link[0]),
                "shells": [s for n in link for s in cls._shells(n)],
            }
            for link in links
        ]
        root_index = None
        if root is not None:
            root_path = cls._long(root)
            root_index = next(
                (i for i, link in enumerate(links) if root_path in link), None
            )
        proposal = ptk.ArticulationAnalysis.propose(
            facts,
            root=root_index,
            order=list(range(len(links))) if ordered else None,
            up=(0.0, 0.0, 1.0)
            if cmds.upAxis(query=True, axis=True) == "z"
            else (0.0, 1.0, 0.0),
        )
        at = {part: i for i, part in enumerate(proposal["order"])}
        joints = []
        for joint in proposal["joints"]:
            joints.append(
                {
                    "link": at[joint["part"]],
                    "parent": at[joint["parent"]],
                    "type": joint["type"],
                    "position": joint["position"],
                    "aim": joint["aim"],
                    "normal": joint["normal"],
                    "limits": joint["limits"],
                    "size": cls.CONTROL_SCALE * joint["radius"],
                    "reason": joint["reason"],
                }
            )
        return {
            "links": [list(links[i]) for i in proposal["order"]],
            "joints": joints,
            "unreached": [links[i][0] for i in proposal["unreached"]],
        }

    # ================================================================= build
    @classmethod
    @CoreUtils.undoable(name="Articulated Rig: Build", suspend_refresh=True)
    def create(
        cls,
        links: Sequence[Any],
        joints: Optional[Sequence[Dict[str, Any]]] = None,
        name: Optional[str] = None,
        parent: Optional[str] = None,
        end_control: bool = True,
    ) -> "ArticulatedRig":
        """Build a rig.

        Parameters:
            links: The rigid links, root first: each a node or a list of nodes
                that move together. Link 0 stays put.
            joints: One per non-root link, in WORLD space, the shape
                :meth:`analyze` returns: ``{"link": i, "parent": j, "type":
                "hinge" | "swivel" | "universal" | "ball" | "slide",
                "position": [x, y, z], "aim": [...], "normal": [...],
                "limits": {"rz": [min, max], ...}, "weights": {...}, "size":
                float}`` -- degrees for a rotation's limits, world units for a
                slide's; ``None`` for an unbounded side. None proposes them
                from the links' geometry.
            name: The rig's name (default: the parts' parent's, lowercase).
            parent: Where the rig group goes (default: the parts' common
                parent, so the rig rides whatever carries the prop).
            end_control: Give the rig an end control on the link at the end
                of its longest chain (see :meth:`set_end_control`).

        Returns:
            The new rig.

        Raises:
            ValueError: A missing, repeated or driven part; fewer than two
                links; joints that do not join every link to the root.
        """
        links = [
            [cls._long(n) or str(n) for n in CoreUtils.as_strings(link)]
            for link in links
        ]
        missing = [n for link in links for n in link if not cmds.objExists(n)]
        if missing:
            raise ValueError(f"Parts not found: {missing}.")
        if len(links) < 2 or not all(links):
            raise ValueError("An articulated rig needs at least two non-empty links.")
        if joints is None:
            joints = cls._propose(links, ordered=True)["joints"]
        members = [n for link in links for n in link]
        home = cls._long(parent) if parent else cls._common_parent(members)
        name = cls._unique_name(name or cls._leaf(home or members[0]).lower())
        inverse = cls._matrix(home).inverse()
        spec = {
            "version": cls.VERSION,
            "name": name,
            "links": [
                [{"uuid": cls._uuid(n), "name": cls._leaf(n)} for n in link]
                for link in links
            ],
            "joints": [cls._plan_joint(j, links, inverse) for j in joints],
        }
        cls._check(spec)
        if end_control:
            end = cls._default_end(spec, links, inverse)
            if end is not None:
                spec["end"] = end
        return cls._build(spec, home)

    @staticmethod
    def _unique_name(name: str) -> str:
        """*name*, numbered past any rig already using it: every node a rig
        makes is named after it, and a clash would have Maya rename them."""
        base, n = name, 1
        while cmds.ls(f"{name}_RIG", recursive=True):
            n += 1
            name = f"{base}{n}"
        return name

    @classmethod
    def _plan_joint(
        cls,
        joint: Dict[str, Any],
        links: Sequence[Sequence[str]],
        inverse: "om.MMatrix",
    ) -> Dict[str, Any]:
        """One world-space joint moved into the rig group's space (*inverse*:
        world to group). A slide's travel and a control's size scale by what
        the group's space does to a length (the prop's own scale)."""
        kind = joint.get("type", "hinge")
        if kind not in cls.JOINT_TYPES or kind == "fixed":
            raise ValueError(f"Unknown or fixed joint type {kind!r}.")
        channels, _order = cls.JOINT_TYPES[kind]
        position = om.MPoint(*joint["position"]) * inverse
        aim = om.MVector(*joint["aim"]).normal() * inverse
        normal = om.MVector(*joint["normal"]) * inverse
        scale = aim.length()
        limits = {}
        for channel in channels:
            bounds = (joint.get("limits") or {}).get(channel)
            if bounds is None:
                continue
            lo, hi = bounds
            if channel.startswith("t"):
                lo = None if lo is None else lo * scale
                hi = None if hi is None else hi * scale
            limits[channel] = [lo, hi]
        return {
            "id": cls._leaf(links[joint["link"]][0]),
            "link": int(joint["link"]),
            "parent": int(joint["parent"]),
            "type": kind,
            "position": [position.x, position.y, position.z],
            "aim": list(aim.normal()),
            "normal": list(normal.normal()),
            "limits": limits,
            "weights": {
                k: float(v)
                for k, v in (joint.get("weights") or {}).items()
                if k in channels
            },
            "size": float(joint.get("size") or 0.0) * scale,
            "reason": str(joint.get("reason") or "as planned"),
        }

    @classmethod
    def _check(cls, spec: Dict[str, Any]) -> None:
        """Refuse a plan the build cannot keep: a part named twice or gone, a
        part something already drives, a link without exactly one joint, a
        joint the root does not reach. Orders the joints parent first."""
        uuids = [m["uuid"] for link in spec["links"] for m in link]
        if len(set(uuids)) != len(uuids):
            raise ValueError("A part is named in two links.")
        lost = [
            m["name"]
            for link in spec["links"]
            for m in link
            if not cls._by_uuid(m["uuid"])
        ]
        if lost:
            raise ValueError(f"Parts no longer in the scene: {lost}.")
        for link in spec["links"]:
            for member in link:
                driven = cls._driven(cls._by_uuid(member["uuid"]))
                if driven:
                    raise ValueError(
                        f"{member['name']} is driven ({', '.join(driven)}): a rigged part "
                        "is moved by its joint alone. Bake or remove its animation first."
                    )
        # Ids unique in the caller's order: a joint already stamped (listed
        # first by every edit) keeps its id -- the key its parked animation is
        # filed under -- and a newcomer repeating a part name is numbered.
        seen: set = set()
        for joint in spec["joints"]:
            stem, n = joint["id"], joint["link"]
            while joint["id"] in seen:
                joint["id"] = f"{stem}_{n}"
                n += 1
            seen.add(joint["id"])
        count = len(spec["links"])
        by_link: Dict[int, Dict[str, Any]] = {}
        for joint in spec["joints"]:
            if not 0 < joint["link"] < count or joint["link"] in by_link:
                raise ValueError(f"Link {joint['link']} has no single joint slot.")
            by_link[joint["link"]] = joint
        missing = [i for i in range(1, count) if i not in by_link]
        if missing:
            raise ValueError(f"Links {missing} have no joint.")
        ordered, placed = [], {0}
        while len(ordered) < len(by_link):
            ready = sorted(
                (
                    j
                    for j in by_link.values()
                    if j not in ordered and j["parent"] in placed
                ),
                key=lambda j: j["link"],
            )
            if not ready:
                raise ValueError("The joints do not join every link to the root.")
            for joint in ready:
                ordered.append(joint)
                placed.add(joint["link"])
        spec["joints"] = ordered
        cls._rest(spec)  # a frame with no aim, or a normal along it, fails HERE

    @classmethod
    def _rest(cls, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Each planned joint's rest in its parent's space, parent first:
        ``{"t", "jo" (degrees, xyz), "q" (xyzw), "parent" (index or None),
        "axes" (its X, Y and Z in the parent's space -- the lines a slide
        travels)}`` -- what the build sets and the record ships, from one
        computation."""
        frames = cls._frames(spec)
        index: Dict[int, int] = {}
        out = []
        for i, joint in enumerate(spec["joints"]):
            local = frames[joint["link"]] * frames[joint["parent"]].inverse()
            t, jo, q = cls._decompose(local)
            axes = [
                list(om.MVector(*unit) * local)
                for unit in ((1, 0, 0), (0, 1, 0), (0, 0, 1))
            ]
            out.append(
                {
                    "t": t,
                    "jo": jo,
                    "q": q,
                    "parent": index.get(joint["parent"]),
                    "axes": axes,
                }
            )
            index[joint["link"]] = i
        return out

    @classmethod
    def _frames(cls, spec: Dict[str, Any]) -> Dict[int, "om.MMatrix"]:
        """Each link's rest frame in the group's space: its joint's (the fixed
        root link's is the group's own)."""
        frames = {0: om.MMatrix()}
        for joint in spec["joints"]:
            frames[joint["link"]] = cls._frame(
                joint["position"], joint["aim"], joint["normal"]
            )
        return frames

    @classmethod
    def _default_end(
        cls,
        spec: Dict[str, Any],
        links: Sequence[Sequence[str]],
        inverse: "om.MMatrix",
        part=None,
    ) -> Optional[Dict[str, Any]]:
        """Where the end control goes on a plan AT REST: on *part*'s link --
        default the link at the end of the longest chain -- aligned with that
        link's joint and centred on its geometry (the magnifier's lens).
        ``{"part": uuid, "frame": [16 floats]}``, the frame in group space;
        None for a part of the fixed root link (nothing to solve).

        Parameters:
            links: The plan's nodes, per link.
            inverse: World to group space.
            part: A part on the link to take; None picks the end link.

        Raises:
            ValueError: *part* is not in the plan.
        """
        joints = {j["link"]: j for j in spec["joints"]}
        if part is not None:
            uuid = cls._uuid(part)
            link = next(
                (
                    i
                    for i, members in enumerate(spec["links"])
                    if any(m["uuid"] == uuid for m in members)
                ),
                None,
            )
            if link is None:
                raise ValueError(f"{part} is not a part of rig {spec['name']!r}.")
        else:

            def depth(i: int) -> int:
                return 0 if i not in joints else 1 + depth(joints[i]["parent"])

            parents = {j["parent"] for j in spec["joints"]}
            leaves = [i for i in joints if i not in parents]
            link = max(leaves, key=lambda i: (depth(i), i)) if leaves else None
        joint = joints.get(link)
        if joint is None:
            return None
        frame = cls._frame(joint["position"], joint["aim"], joint["normal"])
        to_frame = inverse * frame.inverse()
        points = [om.MPoint(*p) * to_frame for p in cls._points(links[link])]
        centre = om.MPoint(
            *[
                0.5 * (min(p[i] for p in points) + max(p[i] for p in points))
                for i in range(3)
            ]
        )
        at = centre * frame
        placed = [frame.getElement(r, c) for r in range(4) for c in range(4)]
        placed[12:15] = [at.x, at.y, at.z]
        return {"part": spec["links"][link][0]["uuid"], "frame": placed}

    @staticmethod
    def _end_of(spec: Dict[str, Any]) -> Optional[int]:
        """The model index of the joint the end control holds -- the joint of
        the link its part rides -- or None (no end control, or its part is
        in the fixed root link)."""
        end = spec.get("end")
        if not end:
            return None
        for index, joint in enumerate(spec.get("joints", [])):
            if any(m["uuid"] == end["part"] for m in spec["links"][joint["link"]]):
                return index
        return None

    @classmethod
    def _link_nodes(cls, spec: Dict[str, Any], link: int) -> List[str]:
        """The parts of *link* by their paths NOW (a build reparents them)."""
        return [cls._by_uuid(m["uuid"]) for m in spec["links"][link]]

    @classmethod
    def _build(cls, spec: Dict[str, Any], home: Optional[str]) -> "ArticulatedRig":
        name = spec["name"]
        links = [cls._link_nodes(spec, i) for i in range(len(spec["links"]))]
        group = cls._long(
            cmds.createNode(
                "transform", name=f"{name}_RIG", **({"parent": home} if home else {})
            )
        )
        cmds.addAttr(group, longName=cls.DATA_ATTR, dataType="string")
        cmds.addAttr(
            group, longName=cls.NODES_ATTR, attributeType="message", multi=True
        )
        cmds.addAttr(group, longName=cls.END_ATTR, attributeType="message")
        cmds.addAttr(group, longName=cls.SOLVER_ATTR, attributeType="message")
        controls_grp = cls._long(
            cmds.createNode("transform", name=f"{name}_controls_GRP", parent=group)
        )
        rest = cls._rest(spec)

        joints: Dict[int, str] = {}
        for joint, pose in zip(spec["joints"], rest):
            _channels, order = cls.JOINT_TYPES[joint["type"]]
            node = cls._long(
                cmds.createNode(
                    "joint",
                    name=f"{name}_{joint['id']}_jnt",
                    parent=joints.get(joint["parent"], group),
                )
            )
            cmds.setAttr(f"{node}.translate", *pose["t"], type="double3")
            cmds.setAttr(f"{node}.jointOrient", *pose["jo"], type="double3")
            cmds.setAttr(f"{node}.rotateOrder", cls.ROTATE_ORDERS.index(order))
            # A joint never scales here, and FBX drops the compensation anyway.
            cmds.setAttr(f"{node}.segmentScaleCompensate", False)
            cmds.setAttr(
                f"{node}.radius", max((joint.get("size") or 1.0) * 0.2, 1.0e-3)
            )
            joints[joint["link"]] = node

        # The parts under their joints, their own channels kept for the
        # teardown: a joint at rest sits where the part's link is, so the
        # world-preserving parent only re-expresses the part's transform.
        parts: List[Dict[str, Any]] = []
        for joint in spec["joints"]:
            for node in links[joint["link"]]:
                parts.append(
                    {
                        "uuid": cls._uuid(node),
                        "name": cls._leaf(node),
                        "parent": cls._uuid(cls._parent(node)),
                        "attrs": cls._capture(node),
                    }
                )
                with Attributes.temporarily_unlock(node, list(cls.MOVED_ATTRS)):
                    cmds.parent(node, joints[joint["link"]])

        end_index = cls._end_of(spec)
        if spec.get("end") and end_index is None:
            cls.logger.warning(
                "The end control's part rides no joint (the fixed root link?): "
                "built without it."
            )
        solved = end_index is not None

        owned: List[str] = []
        controls: Dict[int, str] = {}
        slots: List[Tuple[str, str, Optional[str]]] = []
        for joint, pose in zip(spec["joints"], rest):
            channels, order = cls.JOINT_TYPES[joint["type"]]
            shape, axis = cls.CONTROL_SHAPES[joint["type"]]
            node = joints[joint["link"]]
            nodes = Controls.create(
                shape,
                name=f"{name}_{joint['id']}",
                size=max(joint.get("size") or 1.0, 1.0e-3),
                axis=axis,
                match=node,
                parent=controls.get(joint["parent"], controls_grp),
                color=cls.CONTROL_COLORS[joint["type"]],
                return_nodes=True,
            )
            ctrl = cls._long(nodes.control)
            if solved:
                ctrl = cls._ik_group(ctrl, order)
            controls[joint["link"]] = ctrl
            cls._on_top(ctrl)
            cls._clear(
                ctrl,
                joint["type"],
                node,
                cls._link_nodes(spec, joint["link"])
                + cls._link_nodes(spec, joint["parent"]),
            )
            cmds.setAttr(f"{ctrl}.rotateOrder", cls.ROTATE_ORDERS.index(order))
            others = [
                c for c in ("tx", "ty", "tz", "rx", "ry", "rz") if c not in channels
            ]
            Controls.set_channel_state(
                ctrl,
                keyable=list(channels),
                lock=others + ["s"],
                hide=others + ["s", "v"],
            )
            cls._apply_limits(ctrl, joint["limits"], channels)
            made, adders = cls._wire(ctrl, node, channels, pose, solved)
            owned += made
            slots += [(ctrl, c, a) for c, a in zip(channels, adders)]
            if joint["parent"] in controls:
                cmds.controller(ctrl, controls[joint["parent"]], parent=True)
            owned += cmds.listConnections(f"{ctrl}.message", type="controller") or []

        members = list(controls.values())
        if solved:
            end = cls._build_end(spec, end_index, controls_grp)
            cmds.connectAttr(f"{end}.message", f"{group}.{cls.END_ATTR}")
            owned += cmds.listConnections(f"{end}.message", type="controller") or []
            members.append(end)
            owned += cls._make_solver(spec, group, end, end_index, slots)

        owned.append(cmds.sets(members, name=f"{name}_controls_SET"))
        for node in dict.fromkeys(owned):
            cls._own(group, node)

        spec = copy.deepcopy(spec)
        spec["group_uuid"] = cls._uuid(group)
        spec["parts"] = parts
        cmds.setAttr(
            f"{group}.{cls.DATA_ATTR}", json.dumps(spec, sort_keys=True), type="string"
        )
        rig = cls(group)
        cls.refresh_export_metadata()
        return rig

    @classmethod
    def _ik_group(cls, ctrl: str, order: str) -> str:
        """Slip the IK group (:attr:`IK_GROUP_SUFFIX`) in above *ctrl*, at
        rest and in the joint's rotate order, so the solver's offset composes
        with the control's FK value as the joint's channels do (exactly for a
        one-channel joint, and for any joint while FK is at rest). Returns
        the control's new path."""
        leaf = cls._leaf(ctrl)
        group = cls._long(
            cmds.createNode(
                "transform",
                name=f"{leaf}{cls.IK_GROUP_SUFFIX}",
                parent=cls._parent(ctrl),
            )
        )
        cmds.setAttr(f"{group}.rotateOrder", cls.ROTATE_ORDERS.index(order))
        cmds.parent(ctrl, group, relative=True)
        return f"{group}|{leaf}"

    @classmethod
    def _ik_group_of(cls, ctrl: str) -> Optional[str]:
        """The IK group above *ctrl*, or None (a rig built without one)."""
        parent = cls._parent(ctrl)
        if parent and cls._leaf(parent) == cls._leaf(ctrl) + cls.IK_GROUP_SUFFIX:
            return parent
        return None

    @classmethod
    def _clear(cls, ctrl: str, kind: str, joint: str, nodes: Sequence[str]) -> None:
        """Keep a control that rides its link's axis out of the geometry
        there (*nodes*: the two links it joins, at rest): a slide's arrow
        moves up off the axis until it floats above the tubes, a swivel's
        ring widens around its housing. A hinge's ring, a universal's gimbal
        and a ball's sphere already stand around their joint."""
        if kind not in ("slide", "swivel"):
            return
        points = [p for _shape, cvs in cls._cvs(ctrl) for p in cvs]
        span = max(abs(p[0]) for p in points)
        to_joint = cls._matrix(joint).inverse()
        reach = 0.0
        for p in cls._points(nodes):
            q = om.MPoint(*p) * to_joint
            if abs(q.x) <= span:
                reach = max(reach, math.hypot(q.y, q.z))
        clear = cls.CLEARANCE * reach
        if kind == "slide":
            low = min(p[1] for p in points)
            if clear > low:
                cls._reshape(ctrl, lambda x, y, z: (x, y + clear - low, z))
            return
        radius = max(math.hypot(p[1], p[2]) for p in points)
        if clear > radius > 0.0:
            k = clear / radius
            cls._reshape(ctrl, lambda x, y, z: (x, y * k, z * k))

    @staticmethod
    def _apply_limits(
        ctrl: str, limits: Dict[str, Any], channels: Sequence[str]
    ) -> None:
        """The control's limits: *limits*' bounds on, every other side off."""
        for channel in channels:
            lo, hi = (limits or {}).get(channel) or (None, None)
            cmds.transformLimits(
                ctrl,
                **{
                    channel: (
                        lo if lo is not None else 0.0,
                        hi if hi is not None else 0.0,
                    ),
                    f"e{channel}": (lo is not None, hi is not None),
                },
            )

    @classmethod
    def _wire(
        cls,
        ctrl: str,
        joint: str,
        channels: Sequence[str],
        pose: Dict[str, Any],
        solved: bool,
    ) -> Tuple[List[str], List[Optional[str]]]:
        """The control's channels into the joint's. A rotation is a wire; a
        slide moves the joint along its rest axis in the PARENT's space --
        ``translate = rest + axis * tx`` -- through two utility nodes the
        export bake walks straight through to the control. With an end
        control (*solved*) each channel first passes an ``addDoubleLinear``
        whose second input the solver drives, so the joint takes FK plus
        what the solve adds.

        Returns:
            ``(nodes made, the adder per channel or None)``.
        """
        made: List[str] = []
        adders: List[Optional[str]] = []
        leaf = cls._leaf(joint)
        for channel in channels:
            source = f"{ctrl}.{channel}"
            adder = None
            if solved:
                adder = cmds.createNode("addDoubleLinear", name=f"{leaf}_{channel}_IK")
                cmds.connectAttr(source, f"{adder}.input1")
                source = f"{adder}.output"
                made.append(adder)
            adders.append(adder)
            if channel.startswith("r"):
                cmds.connectAttr(source, f"{joint}.{channel}")
                continue
            axis = pose["axes"]["xyz".index(channel[1])]
            md = cmds.createNode("multiplyDivide", name=f"{leaf}_{channel}_MD")
            pma = cmds.createNode("plusMinusAverage", name=f"{leaf}_{channel}_PMA")
            for i, component in enumerate("XYZ"):
                cmds.connectAttr(source, f"{md}.input1{component}")
                cmds.setAttr(f"{md}.input2{component}", axis[i])
            cmds.connectAttr(f"{md}.output", f"{pma}.input3D[0]")
            cmds.setAttr(f"{pma}.input3D[1]", *pose["t"], type="double3")
            cmds.connectAttr(f"{pma}.output3D", f"{joint}.translate")
            made += [md, pma]
        return made, adders

    @classmethod
    def _build_end(cls, spec: Dict[str, Any], end_index: int, parent: str) -> str:
        """The end control: a box around the end link at its stamped rest
        frame, keyable translate (and rotate when the link hangs off a ball,
        whose turn it then drives), with the IK blend and the follow switch."""
        name = spec["name"]
        joint = spec["joints"][end_index]
        ball = joint["type"] == "ball"
        nodes = Controls.create(
            "box",
            name=f"{name}_end",
            parent=parent,
            color=cls.END_COLOR,
            return_nodes=True,
        )
        ctrl, grp = cls._long(nodes.control), cls._long(nodes.group)
        frame = om.MMatrix(spec["end"]["frame"])
        cmds.xform(grp, matrix=list(frame), objectSpace=True)
        # The box hugs the end link as it stands at rest, in the control's
        # frame; the pivot is its centre (the default frame's origin).
        to_frame = cls._matrix(grp).inverse()
        points = [
            om.MPoint(*p) * to_frame
            for p in cls._points(cls._link_nodes(spec, joint["link"]))
        ]
        lo = [min(p[i] for p in points) for i in range(3)]
        hi = [max(p[i] for p in points) for i in range(3)]
        margin = cls.END_MARGIN * max(b - a for a, b in zip(lo, hi))
        lo = [v - margin for v in lo]
        hi = [v + margin for v in hi]
        cls._reshape(
            ctrl,
            lambda x, y, z: tuple(
                (a if v < 0.0 else b) for v, a, b in zip((x, y, z), lo, hi)
            ),
        )
        cls._on_top(ctrl, width=cls.END_LINE_WIDTH)
        cmds.addAttr(
            ctrl,
            longName=cls.BLEND_ATTR,
            niceName="IK Blend",
            attributeType="double",
            minValue=0.0,
            maxValue=1.0,
            defaultValue=1.0,
            keyable=True,
        )
        if ball:
            cmds.addAttr(
                ctrl,
                longName=cls.FOLLOW_ATTR,
                niceName="Follow Rotation",
                attributeType="bool",
                defaultValue=True,
                keyable=True,
            )
        Controls.set_channel_state(
            ctrl,
            keyable=["t", "r"] if ball else ["t"],
            lock=["s"] if ball else ["r", "s"],
            hide=["s", "v"] if ball else ["r", "s", "v"],
        )
        return ctrl

    @classmethod
    def _end_offset(cls, spec: Dict[str, Any], end_index: int) -> "om.MMatrix":
        """The end link's rest frame in the end control's rest frame: the
        link rides the control as it did at rest (row vectors: ``link =
        offset * control``)."""
        joint = spec["joints"][end_index]
        link = cls._frame(joint["position"], joint["aim"], joint["normal"])
        return link * om.MMatrix(spec["end"]["frame"]).inverse()

    @classmethod
    def _make_solver(
        cls,
        spec: Dict[str, Any],
        group: str,
        end: str,
        end_index: int,
        slots: Sequence[Tuple[str, str, Optional[str]]],
    ) -> List[str]:
        """The end control's solve: the end control in rig space (a
        ``multMatrix`` into a ``decomposeMatrix``) read by a MEL expression
        (:class:`SolverExpression`) that writes each channel's IK offset into
        its adder and its control's IK group (:meth:`_ik_group`). *slots*:
        ``(control, channel, adder)`` per model slot, in state order.

        Returns:
            The nodes made, for the group to own.
        """
        name = spec["name"]
        space = cmds.createNode("multMatrix", name=f"{name}_end_space_MM")
        cmds.connectAttr(f"{end}.worldMatrix[0]", f"{space}.matrixIn[0]")
        cmds.connectAttr(f"{group}.worldInverseMatrix[0]", f"{space}.matrixIn[1]")
        local = cmds.createNode("decomposeMatrix", name=f"{name}_end_space_DM")
        cmds.connectAttr(f"{space}.matrixSum", f"{local}.inputMatrix")
        solver = cmds.expression(
            name=f"{name}_ik_solver",
            string=cls._solver_text(spec, end, end_index, local, slots),
            alwaysEvaluate=False,
            unitConversion="all",
        )
        cmds.connectAttr(f"{solver}.message", f"{group}.{cls.SOLVER_ATTR}")
        return [space, local, solver]

    @classmethod
    def _solver_text(
        cls,
        spec: Dict[str, Any],
        end: str,
        end_index: int,
        local: str,
        slots: Sequence[Tuple[str, str, Optional[str]]],
    ) -> str:
        """The solver's MEL: the rig's joints as the record ships them, the
        end control's pivot on the end link and the link's turn in the control
        (:meth:`_end_offset`), the end control in rig space (*local*, its
        ``decomposeMatrix``), and each slot's FK channel and offset plugs."""
        offset = cls._end_offset(spec, end_index)
        turn = om.MTransformationMatrix(offset).rotation(asQuaternion=True)
        pivot = om.MTransformationMatrix(offset.inverse()).translation(
            om.MSpace.kTransform
        )

        def plug(node: str, attr: str) -> str:
            # The shortest unique name: an expression re-spells it on a rename.
            return f"{cmds.ls(node)[0]}.{attr}"

        seeds, outputs = [], []
        for ctrl, channel, adder in slots:
            seeds.append(plug(ctrl, channel))
            group = cls._ik_group_of(ctrl)
            outputs.append(
                ([plug(adder, "input2")] if adder else [])
                + ([plug(group, channel)] if group else [])
            )
        follow = cmds.attributeQuery(cls.FOLLOW_ATTR, node=end, exists=True)
        return SolverExpression.text_for(
            {"name": spec["name"], "joints": cls._joint_records(spec)},
            end_index,
            (pivot.x, pivot.y, pivot.z),
            (turn.x, turn.y, turn.z, turn.w),
            {
                "translate": [plug(local, f"outputTranslate{a}") for a in "XYZ"],
                "quat": [plug(local, f"outputQuat{a}") for a in "XYZW"],
                "blend": plug(end, cls.BLEND_ATTR),
                "follow": plug(end, cls.FOLLOW_ATTR) if follow else None,
                "seeds": seeds,
                "outputs": outputs,
            },
        )

    @classmethod
    def _own(cls, group: str, node: str) -> None:
        plug = f"{group}.{cls.NODES_ATTR}"
        index = max(cmds.getAttr(plug, multiIndices=True) or [-1]) + 1
        cmds.connectAttr(f"{node}.message", f"{plug}[{index}]")

    # ================================================================ solver
    @classmethod
    def repair_scene(cls) -> int:
        """Give every rig whose end control has no solve -- built by an earlier
        mayatk, whose plug-in node this replaces -- its solve back (see
        :meth:`ensure_solver`). Not an undo step: a scene just opened has
        nothing to undo to. Returns how many were repaired."""
        broken = [rig for rig in cls.scene_rigs() if rig.end_control and not rig.solver]
        if not broken:
            return 0
        with CoreUtils.undo_disabled():
            return sum(1 for rig in broken if rig.ensure_solver())

    def ensure_solver(self) -> bool:
        """Make the end control live: build its solve from the plan when the
        rig has none -- an earlier build solved it with a plug-in node
        (:attr:`LEGACY_SOLVER_PLUGIN`, read back as ``unknown`` where it never
        loaded), which this replaces. The animation lives on the controls, so
        nothing is lost.

        Returns:
            Whether the rig has a working end control.
        """
        spec = self.spec
        index = self._end_of(spec)
        end = self.end_control
        if index is None or end is None:
            return False
        if self.solver:
            return True
        slots = self._adder_slots()
        if any(adder is None for _c, _ch, adder in slots):
            self.logger.warning(
                f"{self.name}: the end control's wiring is incomplete; rebuild the rig."
            )
            return False
        stale = self._linked(self.SOLVER_ATTR)
        if stale:
            cmds.delete(stale)
        self._drop_legacy_plugin()
        group = self.group
        for node in self._make_solver(spec, group, end, index, slots):
            self._own(group, node)
        return True

    @classmethod
    def _drop_legacy_plugin(cls) -> None:
        """Forget the scene's requirement of the old solver plug-in once none
        of its nodes is left, so the next save no longer asks for it."""
        name = cls.LEGACY_SOLVER_PLUGIN
        if name not in (cmds.unknownPlugin(query=True, list=True) or []):
            return
        try:
            cmds.unknownPlugin(name, remove=True)
        except RuntimeError:
            pass  # another rig still holds one of its nodes

    def _adder_slots(self) -> List[Tuple[str, str, Optional[str]]]:
        """``(control, channel, adder)`` per model slot, the adder found by
        its wire from the control (whatever it was renamed to)."""
        owned = self._owned()
        out = []
        for ctrl, channel in self._slots():
            adders = [
                n
                for n in cmds.listConnections(
                    f"{ctrl}.{channel}",
                    source=False,
                    destination=True,
                    type="addDoubleLinear",
                    skipConversionNodes=True,
                )
                or []
                if self._long(n) in owned
            ]
            out.append((ctrl, channel, adders[0] if adders else None))
        return out

    def _owned(self) -> set:
        """The nodes a control's channels feed by the build's own wiring: the
        joints, and the DG nodes the group owns (utility nodes, the solver,
        controller tags, the controls set) -- long names. A part is not one:
        a wire from a control into a part is the user's, and is kept."""
        group = self.group
        nodes = (
            cmds.listConnections(
                f"{group}.{self.NODES_ATTR}", source=True, destination=False
            )
            or []
        )
        nodes += (
            cmds.listRelatives(group, allDescendents=True, type="joint", fullPath=True)
            or []
        )
        return {self._long(n) for n in nodes if cmds.objExists(n)}

    def _sync_solver(self, spec: Dict[str, Any]) -> None:
        """Write the solver again after an in-place edit -- limits, weights
        -- whose values its text inlines (no rebuild)."""
        solver = self.solver
        end = self.end_control
        index = self._end_of(spec)
        if not solver or not end or index is None:
            return
        local = cmds.listConnections(
            solver, source=True, destination=False, type="decomposeMatrix"
        )
        if not local:
            return
        text = self._solver_text(spec, end, index, local[0], self._adder_slots())
        cmds.expression(solver, edit=True, string=text)

    # ============================================================= lifecycle
    @CoreUtils.undoable(name="Articulated Rig: Remove", suspend_refresh=True)
    def teardown(self) -> None:
        """Remove the rig and hand every part back exactly as it was: its
        original parent and its own channel values (captured at the build).
        The animation on the controls goes with them."""
        self._teardown_scene()
        self.refresh_export_metadata()

    def _teardown_scene(self) -> None:
        group = self.group
        spec = self.spec
        # Parts FIRST: they sit under the joints the group's delete takes.
        for part in reversed(spec.get("parts", [])):
            node = self._by_uuid(part["uuid"])
            if not node:
                self.logger.warning(f"Part {part['name']} is gone; nothing to restore.")
                continue
            parent = self._by_uuid(part.get("parent"))
            with Attributes.temporarily_unlock(node, list(self.MOVED_ATTRS)):
                if parent:
                    cmds.parent(node, parent, relative=True)
                else:
                    cmds.parent(node, world=True, relative=True)
            self._restore(self._by_uuid(part["uuid"]), part["attrs"])
        owned = (
            cmds.listConnections(
                f"{group}.{self.NODES_ATTR}", source=True, destination=False
            )
            or []
        )
        owned = [n for n in owned if cmds.objExists(n)]
        if owned:
            cmds.delete(owned)
        if cmds.objExists(group):
            cmds.delete(group)

    @CoreUtils.undoable(name="Articulated Rig: Rebuild", suspend_refresh=True)
    def rebuild(self, spec: Optional[Dict[str, Any]] = None) -> "ArticulatedRig":
        """Build the rig again from *spec* (default: its own plan), carrying
        the animation across: whatever drove each control channel -- a curve,
        an animation layer, a constraint -- or its value lands on the rebuilt
        control of the same joint id and channel, and the end control's on
        the new end control. A channel the new plan no longer has loses its
        curve, with a warning.

        This instance follows the new group; it is also returned.
        """
        parked = self._park()
        return self._replace(spec or self.spec, parked)

    def _replace(
        self, spec: Dict[str, Any], parked: Dict[Tuple[str, str], Tuple[Any, ...]]
    ) -> "ArticulatedRig":
        """Tear the rig down, build *spec*, and land the *parked* animation on
        it. The plan is checked FIRST, while the rig still stands: a plan the
        build would refuse leaves it standing, its animation put back. The
        group's own transform (a rig moved as a whole) is kept."""
        group = self.group
        home = self._parent(group)
        placed = cmds.xform(group, query=True, matrix=True, objectSpace=True)
        plan = {
            k: copy.deepcopy(v)
            for k, v in spec.items()
            if k not in ("parts", "group_uuid")
        }
        try:
            self._check(plan)
        except ValueError:
            self._unpark(parked)
            raise
        self._teardown_scene()
        rig = self._build(plan, home)
        cmds.xform(rig.group, matrix=placed, objectSpace=True)
        self._group_uuid = rig._group_uuid
        self._unpark(parked)
        return self

    def _plugs(self) -> List[Tuple[Tuple[str, str], str]]:
        """``((owner, attr), plug)`` for every plug the rig's animation lives
        on: each FK control's channels and the end control's keyable
        attributes, plus their ``translate`` / ``rotate`` compounds (an
        animation layer drives the compound). The owner is the joint id, or
        :attr:`END_KEY`."""
        out = []
        for joint in self.spec.get("joints", []):
            ctrl = self.control(joint["id"])
            for attr in self.JOINT_TYPES[joint["type"]][0] + ("translate", "rotate"):
                out.append(((joint["id"], attr), f"{ctrl}.{attr}"))
        end = self.end_control
        if end:
            for attr in (cmds.listAttr(end, keyable=True) or []) + [
                "translate",
                "rotate",
            ]:
                out.append(((self.END_KEY, attr), f"{end}.{attr}"))
        return out

    def _park(self) -> Dict[Tuple[str, str], Tuple[Any, ...]]:
        """What drives every animated plug (:meth:`_plugs`) -- disconnected
        and kept, whatever it is (a curve, a layer's blend node, a pairBlend)
        -- or its value, and where its value goes outside the rig (an
        animation layer's membership): ``(kind, source or value, [outside
        destinations])`` per ``(owner, attr)``, keys a rebuild keeps."""
        owned = self._owned()
        parked: Dict[Tuple[str, str], Tuple[Any, ...]] = {}
        for key, plug in self._plugs():
            # Exact per plug (a compound's wire is the compound's alone), and
            # read past a unit conversion: disconnecting by the true ends
            # takes the conversion with it, and a reconnect makes a new one.
            sources = (
                cmds.listConnections(
                    plug,
                    source=True,
                    destination=False,
                    plugs=True,
                    connections=True,
                    skipConversionNodes=True,
                )
                or []
            )
            outs = [
                dest
                for dest in cmds.listConnections(
                    plug,
                    source=False,
                    destination=True,
                    plugs=True,
                    skipConversionNodes=True,
                )
                or []
                if self._long(dest.split(".")[0]) not in owned
            ]
            if sources:
                own, source = sources[0], sources[1]
                cmds.disconnectAttr(source, own)
                parked[key] = ("plug", source, outs)
            elif key[1] in ("translate", "rotate"):
                if outs:
                    parked[key] = ("value", None, outs)
            else:
                parked[key] = ("value", cmds.getAttr(plug), outs)
        return parked

    def _unpark(self, parked: Dict[Tuple[str, str], Tuple[Any, ...]]) -> None:
        plugs = dict(self._plugs())
        dropped = []
        for key, (kind, value, outs) in parked.items():
            plug = plugs.get(key)
            if plug is None:
                if kind == "plug":
                    node = value.split(".")[0]
                    if cmds.objExists(node) and cmds.nodeType(node).startswith(
                        "animCurve"
                    ):
                        cmds.delete(node)
                    dropped.append(f"{key[0]}.{key[1]}")
                continue
            try:
                if kind == "plug":
                    cmds.connectAttr(value, plug, force=True)
                elif value is not None and cmds.getAttr(plug, settable=True):
                    cmds.setAttr(plug, value)
                for dest in outs:
                    cmds.connectAttr(plug, dest, force=True)
            except RuntimeError as error:
                self.logger.warning(f"Could not carry {plug} across: {error}")
        if dropped:
            self.logger.warning(
                f"Animation dropped with its channel: {', '.join(dropped)}."
            )

    def _to_rest(self) -> None:
        """Every control at rest, unkeyed: FK at 0 and the end control on its
        rest frame -- the pose a plan is measured in."""
        self.set_state([0.0] * len(self._slots()), key=False)
        end = self.end_control
        if end:
            for attr in ("translate", "rotate"):
                for axis in "XYZ":
                    plug = f"{end}.{attr}{axis}"
                    if cmds.getAttr(plug, settable=True):
                        cmds.setAttr(plug, 0.0)

    # ============================================================ post-rig
    @CoreUtils.undoable(name="Articulated Rig: Insert Joint", suspend_refresh=True)
    def insert_joint(
        self, members, joint_type: Optional[str] = None, **fields
    ) -> "ArticulatedRig":
        """Split a link: *members* leave the link they ride and get a joint of
        their own, hung off that link. What the new joint is -- a slide for a
        tube inside a tube -- comes from the geometry unless *joint_type* (and
        any of :meth:`create`'s joint fields, world space) says otherwise.

        The joints that hung off the split link and sit nearer *members* move
        onto the new joint, so the chain beyond rides it. The new joint is at
        0, which changes no pose: every key already made still holds. With an
        end control it joins the solve at weight 0 (unless *weights* says
        otherwise), so a keyed IK pose holds too.

        Returns:
            This rig, rebuilt.

        Raises:
            ValueError: *members* are not all in one link, or are the whole of
                it.
        """
        spec = self.spec
        names = CoreUtils.as_strings(members)
        missing = [n for n in names if not cmds.objExists(n)]
        if missing:
            raise ValueError(f"Not in the scene: {missing}.")
        moved = {self._uuid(m) for m in names}
        host = next(
            (
                i
                for i, link in enumerate(spec["links"])
                if moved <= {m["uuid"] for m in link}
            ),
            None,
        )
        if host is None or not moved:
            raise ValueError("The members to split off are not all in one link.")
        keep = [m for m in spec["links"][host] if m["uuid"] not in moved]
        split = [m for m in spec["links"][host] if m["uuid"] in moved]
        if not keep:
            raise ValueError("Splitting off a whole link adds no joint.")

        parked = self._park()
        try:
            self._to_rest()  # planned at rest
            keep_nodes = [self._by_uuid(m["uuid"]) for m in keep]
            split_nodes = [self._by_uuid(m["uuid"]) for m in split]
            proposal = self._propose([keep_nodes, split_nodes], ordered=True)["joints"][
                0
            ]
            proposal.update({k: v for k, v in fields.items() if v is not None})
            if joint_type:
                proposal["type"] = joint_type
            new_link = len(spec["links"])
            proposal.update(link=new_link, parent=host)
            world = self._matrix(self.group)
            links = [[self._by_uuid(m["uuid"]) for m in link] for link in spec["links"]]
            joint = self._plan_joint(proposal, links + [split_nodes], world.inverse())
            if spec.get("end") and not fields.get("weights"):
                # Free to move, the end control's solve would use the new
                # joint and reshape every keyed IK pose: it joins the solve
                # still, until it is given some give.
                joint["weights"] = {c: 0.0 for c in self.JOINT_TYPES[joint["type"]][0]}
                self.logger.info(
                    f"{joint['id']} joins the end control's solve at give 0, so "
                    "the IK animation holds; raise its give to let the end "
                    "control move it."
                )

            # The joints beyond the split that the split-off parts now carry.
            near_keep = self._points(keep_nodes)
            near_split = self._points(split_nodes)
            for child in spec["joints"]:
                if child["parent"] != host:
                    continue
                p = om.MPoint(*child["position"]) * world
                d_keep = min((p - om.MPoint(*q)).length() for q in near_keep)
                d_split = min((p - om.MPoint(*q)).length() for q in near_split)
                if d_split < d_keep:
                    child["parent"] = new_link
        except Exception:
            # Nothing torn down yet: the rig takes its animation back.
            self._unpark(parked)
            raise

        spec["links"][host] = keep
        spec["links"].append(split)
        spec["joints"].append(joint)
        return self._replace(spec, parked)

    @CoreUtils.undoable(name="Articulated Rig: Remove Joint", suspend_refresh=True)
    def remove_joint(self, joint_id: str) -> "ArticulatedRig":
        """Fold *joint_id*'s link back into the link it hangs off: its parts
        ride that joint again, the joints beyond it move up with them, and its
        own animation is dropped (with a warning).

        Returns:
            This rig, rebuilt.
        """
        spec = self.spec
        joint = next((j for j in spec["joints"] if j["id"] == joint_id), None)
        if joint is None:
            raise KeyError(f"No joint {joint_id!r} in rig {self.name!r}.")
        gone, into = joint["link"], joint["parent"]
        spec["links"][into] += spec["links"][gone]
        del spec["links"][gone]

        def renumber(link: int) -> int:
            link = into if link == gone else link
            return link - 1 if link > gone else link

        joints = []
        for other in spec["joints"]:
            if other is joint:
                continue
            other["parent"] = renumber(other["parent"])
            other["link"] = renumber(other["link"])
            joints.append(other)
        spec["joints"] = joints
        return self.rebuild(spec)

    @CoreUtils.undoable(name="Articulated Rig: Edit Joint", suspend_refresh=True)
    def edit_joint(self, joint_id: str, **fields) -> "ArticulatedRig":
        """Change a joint's ``type``, ``position``, ``aim`` or ``normal``
        (world space), or its ``limits`` / ``weights``, and rebuild with the
        animation carried across. A retyped joint keeps the curves of the
        channels both types share."""
        spec = self.spec
        joint = next((j for j in spec["joints"] if j["id"] == joint_id), None)
        if joint is None:
            raise KeyError(f"No joint {joint_id!r} in rig {self.name!r}.")
        world = self._matrix(self.group)
        current = {
            "type": joint["type"],
            "link": joint["link"],
            "parent": joint["parent"],
            "position": list(om.MPoint(*joint["position"]) * world)[:3],
            "aim": list(om.MVector(*joint["aim"]) * world),
            "normal": list(om.MVector(*joint["normal"]) * world),
            "limits": self._world_limits(joint, world),
            "weights": joint.get("weights"),
            "size": joint.get("size", 0.0)
            * (om.MVector(*joint["aim"]) * world).length(),
            "reason": joint.get("reason"),
        }
        current.update({k: v for k, v in fields.items() if v is not None})
        links = [[m["name"] for m in link] for link in spec["links"]]
        planned = self._plan_joint(current, links, world.inverse())
        planned["id"] = joint["id"]
        spec["joints"][spec["joints"].index(joint)] = planned
        return self.rebuild(spec)

    @staticmethod
    def _world_limits(joint: Dict[str, Any], world: "om.MMatrix") -> Dict[str, Any]:
        """A planned joint's limits back in world units (the inverse of
        :meth:`_plan_joint`'s): a slide's travel grows by the prop's scale."""
        scale = (om.MVector(*joint["aim"]) * world).length()
        out = {}
        for channel, (lo, hi) in (joint.get("limits") or {}).items():
            if channel.startswith("t"):
                lo = None if lo is None else lo * scale
                hi = None if hi is None else hi * scale
            out[channel] = [lo, hi]
        return out

    @CoreUtils.undoable(name="Articulated Rig: End Control", suspend_refresh=True)
    def set_end_control(
        self, enabled: bool = True, part: Optional[str] = None
    ) -> "ArticulatedRig":
        """Give the rig an end control, move it to another link, or drop it.

        The end control is an IK target: translate it and the chain from the
        root moves the end link there; rotate it and a ball-mounted end link
        turns with it. Its ``ikBlend`` fades the solve over FK (0 = the FK
        pose) and ``followRotation`` lets the end link keep its own turn
        instead. Placed at rest on *part*'s link (default: the link at the
        end of the longest chain), aligned with that link's joint and centred
        on its geometry. Dropping it drops its animation (with a warning).

        Returns:
            This rig, rebuilt with the animation carried across.

        Raises:
            ValueError: *part* is not a part of this rig, or rides the fixed
                root link.
        """
        spec = self.spec
        if not enabled:
            spec.pop("end", None)
            return self.rebuild(spec)
        parked = self._park()
        try:
            self._to_rest()
            links = [[self._by_uuid(m["uuid"]) for m in link] for link in spec["links"]]
            end = self._default_end(
                spec, links, self._matrix(self.group).inverse(), part=part
            )
            if end is None:
                raise ValueError(f"{part} rides the fixed root link: nothing to move.")
        except Exception:
            self._unpark(parked)
            raise
        spec["end"] = end
        return self._replace(spec, parked)

    @CoreUtils.undoable(name="Articulated Rig: Set Limits")
    def set_limits(
        self,
        joint_id: str,
        channel: str,
        minimum: Optional[float],
        maximum: Optional[float],
    ) -> None:
        """Bound one channel (degrees, or a slide's units in the rig's space);
        None frees that side. No rebuild: the control, the solver and the
        record change in place."""
        spec = self.spec
        joint = self._joint_entry(spec, joint_id)
        if channel not in self.JOINT_TYPES[joint["type"]][0]:
            raise KeyError(f"No channel {channel!r} on joint {joint_id!r}.")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError(f"Limits [{minimum}, {maximum}] are reversed.")
        joint.setdefault("limits", {})[channel] = [minimum, maximum]
        self._stamp(spec)
        self._apply_limits(self.control(joint_id), joint["limits"], [channel])
        self._sync_solver(spec)
        self.refresh_export_metadata()

    def set_limit_from_pose(self, joint_id: str, channel: str, side: str) -> float:
        """Take the channel's current value -- the pose as it stands, the end
        control's solve included -- as its *side* (``"min"`` or ``"max"``)
        limit: pose the part to where it physically stops, then call this.
        Returns the value taken."""
        value = self.channel_value(joint_id, channel)
        joint = self._joint_entry(self.spec, joint_id)
        lo, hi = (joint.get("limits") or {}).get(channel) or (None, None)
        if side == "min":
            lo = value
        elif side == "max":
            hi = value
        else:
            raise ValueError(f"side must be 'min' or 'max', not {side!r}.")
        self.set_limits(joint_id, channel, lo, hi)
        return value

    @CoreUtils.undoable(name="Articulated Rig: Set Weight")
    def set_weight(
        self, joint_id: str, weight: float, channel: Optional[str] = None
    ) -> None:
        """How readily *joint_id* moves when the end control or a grab pulls
        the rig, against the other joints: 2 twice as readily as 1, 0 not at
        all -- every channel of the joint, or just *channel*. The runtimes'
        hand grab reads the same weights. No rebuild."""
        spec = self.spec
        joint = self._joint_entry(spec, joint_id)
        channels = self.JOINT_TYPES[joint["type"]][0]
        if channel is not None and channel not in channels:
            raise KeyError(f"No channel {channel!r} on joint {joint_id!r}.")
        if weight < 0.0:
            raise ValueError(f"A weight is never negative (got {weight}).")
        weights = joint.setdefault("weights", {})
        for c in [channel] if channel else channels:
            weights[c] = float(weight)
        self._stamp(spec)
        self._sync_solver(spec)
        self.refresh_export_metadata()

    def _joint_entry(self, spec: Dict[str, Any], joint_id: str) -> Dict[str, Any]:
        joint = next((j for j in spec.get("joints", []) if j["id"] == joint_id), None)
        if joint is None:
            raise KeyError(f"No joint {joint_id!r} in rig {self.name!r}.")
        return joint

    # ================================================================ adjust
    @property
    def adjusting(self) -> bool:
        """Whether the rig's pivots are out for adjusting (:meth:`begin_adjust`)."""
        return self._linked(self.ADJUST_ATTR) is not None

    def adjust_handles(self) -> Dict[str, str]:
        """The pivot handles while adjusting, by what each places: a joint id,
        or :attr:`END_KEY` for the end control's."""
        holder = self._linked(self.ADJUST_ATTR)
        out: Dict[str, str] = {}
        for child in (
            cmds.listRelatives(holder, children=True, type="transform", fullPath=True)
            if holder
            else None
        ) or []:
            if cmds.attributeQuery(self.PIVOT_ATTR, node=child, exists=True):
                out[cmds.getAttr(f"{child}.{self.PIVOT_ATTR}")] = child
        return out

    @CoreUtils.undoable(name="Articulated Rig: Adjust Pivots")
    def begin_adjust(self) -> Dict[str, str]:
        """Put the rig's pivots out for adjusting: a handle where every joint
        turns -- its rest frame, X along the part, Z a hinge's axis -- riding
        the part the joint hangs off, and one where the end control sits,
        riding the end part. Move and turn them, then :meth:`end_adjust`;
        until then nothing changes, and the animation plays on.

        Returns:
            The handles (:meth:`adjust_handles`).
        """
        if self.adjusting:
            return self.adjust_handles()
        group = self.group
        spec = self.spec
        if not cmds.attributeQuery(self.ADJUST_ATTR, node=group, exists=True):
            cmds.addAttr(group, longName=self.ADJUST_ATTR, attributeType="message")
        holder = self._long(
            cmds.createNode(
                "transform", name=f"{spec['name']}_adjust_GRP", parent=group
            )
        )
        cmds.connectAttr(f"{holder}.message", f"{group}.{self.ADJUST_ATTR}")
        frames = self._frames(spec)
        joints = {j["link"]: j for j in spec["joints"]}
        for joint in spec["joints"]:
            above = joints.get(joint["parent"])
            self._make_handle(
                holder,
                joint["id"],
                frames[joint["link"]] * frames[joint["parent"]].inverse(),
                self.joint(above["id"]) if above else None,
                self.HANDLE_SCALE * max(joint.get("size") or 1.0, 1.0e-3),
            )
        index = self._end_of(spec)
        end = self.end_control
        if index is not None and end:
            joint = spec["joints"][index]
            box = cmds.exactWorldBoundingBox(end)
            self._make_handle(
                holder,
                self.END_KEY,
                om.MMatrix(spec["end"]["frame"]) * frames[joint["link"]].inverse(),
                self.joint(joint["id"]),
                0.35 * math.dist(box[:3], box[3:]),
            )
        return self.adjust_handles()

    def _make_handle(
        self,
        holder: str,
        key: str,
        local: "om.MMatrix",
        carrier: Optional[str],
        size: float,
    ) -> str:
        """One pivot handle under *holder*: three axes, drawn over the
        geometry, at *local* in the space of *carrier* (a joint it rides, or
        the rig group), its translate and rotate free to move but never keyed
        (Auto Key leaves a handle alone)."""
        group = self.group
        label = "end" if key == self.END_KEY else key
        handle = self._long(
            cmds.createNode(
                "transform",
                name=f"{self.name}_{label}{self.HANDLE_SUFFIX}",
                parent=holder,
            )
        )
        cmds.addAttr(handle, longName=self.PIVOT_ATTR, dataType="string")
        cmds.setAttr(f"{handle}.{self.PIVOT_ATTR}", key, type="string", lock=True)
        for axis, color in zip(((1, 0, 0), (0, 1, 0), (0, 0, 1)), self.AXIS_COLORS):
            line = cmds.curve(degree=1, point=[(0, 0, 0), [c * size for c in axis]])
            shape = cmds.listRelatives(line, shapes=True, fullPath=True)[0]
            cmds.setAttr(f"{shape}.overrideEnabled", True)
            cmds.setAttr(f"{shape}.overrideColor", color)
            cmds.parent(shape, handle, shape=True, relative=True)
            cmds.delete(line)
        self._on_top(handle, width=self.END_LINE_WIDTH)
        if carrier:
            ride = cmds.createNode("multMatrix", name=f"{self._leaf(handle)}_MM")
            cmds.connectAttr(f"{carrier}.worldMatrix[0]", f"{ride}.matrixIn[0]")
            cmds.connectAttr(f"{holder}.worldInverseMatrix[0]", f"{ride}.matrixIn[1]")
            cmds.connectAttr(f"{ride}.matrixSum", f"{handle}.offsetParentMatrix")
            self._own(group, ride)
        t, rotation, _scale = Matrices.decompose(local, rotate_order="xyz")
        cmds.setAttr(f"{handle}.translate", *t, type="double3")
        cmds.setAttr(f"{handle}.rotate", *rotation, type="double3")
        for attr in ("translate", "rotate"):
            for axis in "XYZ":
                cmds.setAttr(f"{handle}.{attr}{axis}", keyable=False, channelBox=True)
        Controls.set_channel_state(handle, lock=["s"], hide=["s", "v"])
        return handle

    @CoreUtils.undoable(name="Articulated Rig: Apply Pivots", suspend_refresh=True)
    def end_adjust(self, apply: bool = True) -> bool:
        """Lock the pivots again: the rig rebuilt with every joint turning
        where its handle stands and the end control where its handle put it,
        the animation carried across (each channel's keys now turn the part
        about its new pivot) -- or, *apply* False, the handles dropped and
        nothing changed.

        Returns:
            Whether the rig was rebuilt (False: no handle moved, or not
            applied).
        """
        holder = self._linked(self.ADJUST_ATTR)
        if holder is None:
            return False
        spec = self.spec
        adjusted = self._adjusted(spec) if apply else None
        for handle in self.adjust_handles().values():
            rides = cmds.listConnections(
                f"{handle}.offsetParentMatrix",
                source=True,
                destination=False,
                type="multMatrix",
            )
            if rides:
                cmds.delete(rides)
        cmds.delete(holder)
        if adjusted is None:
            return False
        end = self.end_control
        before = (spec.get("end") or {}).get("frame")
        after = (adjusted.get("end") or {}).get("frame")
        moved_end = bool(before and after) and any(
            abs(a - b) > self.ADJUST_EPS for a, b in zip(before, after)
        )
        if moved_end and cmds.keyframe(end, query=True, keyframeCount=True):
            self.logger.warning(
                f"{self.name}: the end control's keys are relative to its frame; "
                "they now play about the adjusted one."
            )
        self.rebuild(adjusted)
        return True

    def _adjusted(self, spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """*spec* with each joint's rest frame and the end control's where
        the handles put them, or None when none moved. A handle rides what its
        joint hangs off, so its local matrix is that joint's rest frame there
        -- read against the frames as they WERE, so a child's pivot stays put
        when its parent's moves."""
        handles = self.adjust_handles()
        frames = self._frames(spec)
        out = copy.deepcopy(spec)
        moved = False

        def placed(key: str, under: "om.MMatrix") -> Optional["om.MMatrix"]:
            handle = handles.get(key)
            if not handle:
                return None
            return om.MMatrix(cmds.getAttr(f"{handle}.matrix")) * under

        for joint in out["joints"]:
            frame = placed(joint["id"], frames[joint["parent"]])
            if frame is None:
                continue
            rows = [[frame.getElement(r, c) for c in range(3)] for r in range(4)]
            before = frames[joint["link"]]
            if any(
                abs(frame.getElement(r, c) - before.getElement(r, c)) > self.ADJUST_EPS
                for r in range(4)
                for c in range(3)
            ):
                moved = True
            joint.update(position=rows[3], aim=rows[0], normal=rows[2])
        index = self._end_of(spec)
        if index is not None and spec.get("end"):
            frame = placed(self.END_KEY, frames[spec["joints"][index]["link"]])
            if frame is not None:
                values = [frame.getElement(r, c) for r in range(4) for c in range(4)]
                if any(
                    abs(a - b) > self.ADJUST_EPS
                    for a, b in zip(values, spec["end"]["frame"])
                ):
                    moved = True
                out["end"]["frame"] = values
        return out if moved else None

    # ============================================================== controls
    def joint_ids(self) -> List[str]:
        """The joints' ids -- each its link's first part -- parent first."""
        return [j["id"] for j in self.spec.get("joints", [])]

    def joint_id_of(self, node) -> Optional[str]:
        """The id of the joint *node* is -- its control, its joint -- or rides
        (a part, or anything under one); None for the fixed root link's, and
        for the end control."""
        spec = self.spec
        leaf = self._leaf(self._long(node) or str(node))
        affix = ptk.NamingConvention.affix("control")
        for joint in spec.get("joints", []):
            base = f"{spec['name']}_{joint['id']}"
            if leaf in (f"{base}_jnt", f"{base}{affix}"):
                return joint["id"]
        try:
            return spec["joints"][self._joint_of(node)]["id"]
        except ValueError:
            return None

    def joint(self, joint_id: str) -> str:
        """The joint node of *joint_id*."""
        return self._named(f"{self.name}_{joint_id}_jnt", "joint")

    def control(self, joint_id: str) -> str:
        """The FK control of *joint_id*."""
        affix = ptk.NamingConvention.affix("control")
        return self._named(f"{self.name}_{joint_id}{affix}", "transform")

    def controls(self) -> List[str]:
        """Every control of the rig: the FK controls parent first, then the
        end control."""
        out = [self.control(joint_id) for joint_id in self.joint_ids()]
        end = self.end_control
        return out + [end] if end else out

    def _named(self, leaf: str, kind: str) -> str:
        found = [
            n
            for n in cmds.listRelatives(
                self.group, allDescendents=True, type=kind, fullPath=True
            )
            or []
            if self._leaf(n) == leaf
        ]
        if not found:
            raise KeyError(f"No {kind} {leaf!r} in rig {self.name!r}.")
        return found[0]

    def _slots(self) -> List[Tuple[str, str]]:
        """``(control, channel)`` per state slot, in the model's order."""
        return [
            (self.control(joint["id"]), channel)
            for joint in self.spec.get("joints", [])
            for channel in self.JOINT_TYPES[joint["type"]][0]
        ]

    def state(self, slots: Optional[Sequence[Tuple[str, str]]] = None) -> List[float]:
        """The rig's pose, in :meth:`model` state order: each channel's FK
        value plus what the end control's solve adds (the offset its
        control's IK group carries) -- what the joints show."""
        slots = slots or self._slots()
        values = self.fk_state(slots)
        if not self.solver:
            return values
        out = []
        for (ctrl, channel), value in zip(slots, values):
            group = self._ik_group_of(ctrl)
            out.append(value + (cmds.getAttr(f"{group}.{channel}") if group else 0.0))
        return out

    def fk_state(
        self, slots: Optional[Sequence[Tuple[str, str]]] = None
    ) -> List[float]:
        """The FK controls' channel values, in :meth:`model` state order."""
        return [cmds.getAttr(f"{c}.{ch}") for c, ch in (slots or self._slots())]

    def channel_value(self, joint_id: str, channel: str) -> float:
        """One channel as the pose stands (FK plus the end control's solve)."""
        slots = self._slots()
        index = slots.index((self.control(joint_id), channel))
        return self.state(slots)[index]

    def set_state(
        self,
        values: Sequence[float],
        key: Optional[bool] = None,
        slots: Optional[Sequence[Tuple[str, str]]] = None,
    ) -> None:
        """Set the FK controls to *values* (model state order), keyed when
        *key* (default: whatever Maya's auto key says). With the end control
        on, FK is the solve's seed: the end link stays on the end control and
        the chain reshapes under it."""
        if key is None:
            key = bool(cmds.autoKeyframe(query=True, state=True))
        for (ctrl, channel), value in zip(slots or self._slots(), values):
            cmds.setAttr(f"{ctrl}.{channel}", value)
            if key:
                cmds.setKeyframe(ctrl, attribute=channel)

    def _key_end(self, key: Optional[bool]) -> None:
        """Key the end control's transform channels when *key* (default:
        Maya's auto key)."""
        if key is None:
            key = bool(cmds.autoKeyframe(query=True, state=True))
        end = self.end_control
        if key and end:
            attrs = [
                a
                for a in cmds.listAttr(end, keyable=True) or []
                if a.startswith(("translate", "rotate"))
            ]
            cmds.setKeyframe(end, attribute=attrs)

    @CoreUtils.undoable(name="Articulated Rig: Match End Control")
    def match_end_control(self, key: Optional[bool] = None) -> None:
        """Put the end control where the end link is now -- after FK posing,
        or when it was left out of reach -- so nothing moves when the IK blend
        comes up. Keyed when *key* (default: Maya's auto key)."""
        end = self.end_control
        spec = self.spec
        index = self._end_of(spec)
        if not end or index is None:
            raise RuntimeError(f"Rig {self.name!r} has no end control.")
        current = self._matrix(self.joint(spec["joints"][index]["id"]))
        self._place(end, self._end_offset(spec, index).inverse() * current)
        self._key_end(key)

    @CoreUtils.undoable(name="Articulated Rig: Switch IK/FK")
    def switch_ik(self, on: Optional[bool] = None, key: Optional[bool] = None) -> bool:
        """Switch the end control's solve on or off (default: flip it)
        without the pose jumping: on, the end control first moves to the end
        link; off, the FK controls first take the pose the solve made. Keyed
        when *key* (default: Maya's auto key).

        Returns:
            Whether the solve is now on.
        """
        end = self.end_control
        if not end:
            raise RuntimeError(f"Rig {self.name!r} has no end control.")
        if key is None:
            key = bool(cmds.autoKeyframe(query=True, state=True))
        if on is None:
            # The switch's own setting, not :meth:`ik_blend`: an end control
            # whose solver is not live reads 0 there, and would never flip off.
            on = cmds.getAttr(f"{end}.{self.BLEND_ATTR}") < 0.5
        on = bool(on)
        if on:
            self.match_end_control(key=key)
        else:
            slots = self._slots()
            self.set_state(self.state(slots), key=key, slots=slots)
        cmds.setAttr(f"{end}.{self.BLEND_ATTR}", 1.0 if on else 0.0)
        if key:
            cmds.setKeyframe(end, attribute=self.BLEND_ATTR)
        return on

    @CoreUtils.undoable(name="Articulated Rig: Rest Pose")
    def reset_pose(self, key: Optional[bool] = None) -> None:
        """Every control back to rest -- FK at 0, the end control on its rest
        frame -- keyed when *key* (default: Maya's auto key)."""
        self._to_rest()
        if key is None:
            key = bool(cmds.autoKeyframe(query=True, state=True))
        if key:
            self.set_state([0.0] * len(self._slots()), key=True)
            self._key_end(True)

    # ================================================================ record
    @classmethod
    def _joint_records(cls, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The record's joints: each one's rest (translate and joint orient in
        its parent's space), rotate order and channels with their limits and
        weights, parent first; ``parent`` an index into them."""
        joints = []
        for joint, pose in zip(spec.get("joints", []), cls._rest(spec)):
            channels, order = cls.JOINT_TYPES[joint["type"]]
            limits = joint.get("limits") or {}
            weights = joint.get("weights") or {}
            joints.append(
                {
                    "name": f"{spec['name']}_{joint['id']}_jnt",
                    "parent": pose["parent"],
                    "t": pose["t"],
                    "q": pose["q"],
                    "rotate_order": order,
                    "channels": [
                        {
                            "channel": channel,
                            "min": (limits.get(channel) or (None, None))[0],
                            "max": (limits.get(channel) or (None, None))[1],
                            "weight": float(weights.get(channel, 1.0)),
                        }
                        for channel in channels
                    ],
                }
            )
        return joints

    def record(self) -> Dict[str, Any]:
        """This rig's entry in the ``articulation`` record: its joints
        (:meth:`_joint_records`) and the parts a hand grabs."""
        spec = self.spec
        grab = []
        for index, joint in enumerate(spec["joints"]):
            # The part's name NOW (a runtime binds by it): a rename after the
            # build must not leave the record naming a node that is gone.
            grab += [
                {
                    "node": self._leaf(self._by_uuid(m["uuid"]) or m["name"]),
                    "joint": index,
                }
                for m in spec["links"][joint["link"]]
            ]
        return {"name": spec["name"], "joints": self._joint_records(spec), "grab": grab}

    def model(self) -> "ptk.ArticulationModel":
        """The runtime model of this rig -- what the engines pose."""
        return ptk.ArticulationModel(self.record())

    @classmethod
    def export_record(cls, ctx) -> Optional["ptk.Record"]:
        """The ``articulation`` record for this scene, or None when it has no
        rig -- the ``ptk.SceneRecords.ARTICULATION`` producer
        (``FbxUtils.PRODUCERS``). Pure: reads the rigs' plans, never writes.

        ``{"version": 1, "rigs": [...]}``, one :meth:`record` per rig. No
        ``unit_scale``: a runtime measures its own units against the rest
        pose (``ptk.ArticulationModel.scale_of``), which covers a scaled prop
        and an FBX unit conversion alike.

        Parameters:
            ctx (ptk.ExportContext): The export's decisions (unused: the
                record is a function of the rigs alone).
        """
        rigs = [rig.record() for rig in cls.scene_rigs()]
        if not rigs:
            return None
        return ptk.SceneRecords.ARTICULATION.make({"rigs": rigs})

    @classmethod
    def refresh_export_metadata(cls) -> Optional[str]:
        """Publish the ``articulation`` record now -- the authoring-time half
        of :meth:`export_record` (an export pipeline runs the producer itself)
        -- and opt it into every FBX export this session. Clears the channel
        when the scene has no rig."""
        from mayatk.env_utils.fbx_utils import FbxUtils

        record = cls.export_record(ptk.ExportContext(mode=ptk.ExportContext.AUTHORING))
        FbxUtils.publish_authored({ptk.SceneRecords.ARTICULATION: record})
        if record is not None:
            FbxUtils.enable_export_producer(ptk.SceneRecords.ARTICULATION)
        return record.text if record is not None else None

    # ================================================================== grab
    def _space(self) -> Tuple["om.MMatrix", "om.MQuaternion"]:
        """The world-to-rig matrix, and the rig space's rotation in the world."""
        world = self._matrix(self.group)
        return world.inverse(), om.MTransformationMatrix(world).rotation(
            asQuaternion=True
        )

    def _joint_of(self, node) -> int:
        """The model index of the joint *node* rides -- a part, or anything
        under one."""
        spec = self.spec
        riders = {
            m["uuid"]: i
            for i, joint in enumerate(spec["joints"])
            for m in spec["links"][joint["link"]]
        }
        path = self._long(node)
        while path:
            index = riders.get(self._uuid(path))
            if index is not None:
                return index
            path = self._parent(path)
        raise ValueError(
            f"{node} rides no joint of rig {self.name!r} (the root link stays put)."
        )

    def grab_begin(self, node, point: Sequence[float]) -> Dict[str, Any]:
        """Take hold of *node* (a part, or anything under one) at the world
        *point*.

        With the end control on and *node* on the end link, the hold drags
        the end control (the solve does the rest, as when the end control is
        moved by hand); otherwise it poses the FK controls.

        Returns:
            The hold :meth:`grab_to` moves: the joint, the point in its frame,
            the link's rig-space rotation as taken, and the model and control
            slots (read once, not per drag) -- plus, dragging the end control,
            the point in its frame and its matrix as taken.
        """
        model = self.model()
        slots = self._slots()
        state = self.state(slots)
        joint = self._joint_of(node)
        inverse, _space = self._space()
        local = om.MPoint(*point) * inverse
        hold = {
            "joint": joint,
            "local": model.to_local(state, joint, (local.x, local.y, local.z)),
            "rotation": model.world(state)[joint][1],
            "model": model,
            "slots": slots,
        }
        end = self.end_control
        if end and self.ik_blend() >= 0.5 and joint == self._end_of(self.spec):
            taken = self._matrix(end)
            hold["end"] = {
                "control": end,
                "matrix": list(taken),
                "point": list(point)[:3],
            }
        return hold

    def held_point(self, hold: Dict[str, Any]) -> Tuple[float, float, float]:
        """Where a hold's point is now, in world space."""
        state = self.state(hold["slots"])
        p = om.MPoint(
            *hold["model"].point(state, hold["joint"], hold["local"])
        ) * self._matrix(self.group)
        return (p.x, p.y, p.z)

    @CoreUtils.undoable(name="Articulated Rig: Pose To")
    def pose_to(
        self,
        node,
        target: Sequence[float],
        point: Optional[Sequence[float]] = None,
        key: Optional[bool] = None,
        attempts: int = 8,
    ) -> float:
        """Bring a part's *point* (default its rotate pivot) to the world
        *target* -- the lens over a locator, a hook onto a peg -- solving a
        grab's step again until it lands or stops closing in.

        Returns:
            The distance left (0 within the solver's tolerance; more when the
            target is out of reach or behind a limit).
        """
        part = self._long(node)
        if point is None:
            point = cmds.xform(part, query=True, worldSpace=True, rotatePivot=True)
        hold = self.grab_begin(part, point)
        left = float("inf")
        for _ in range(max(1, attempts)):
            self.grab_to(hold, target, key=False)
            now = (om.MPoint(*self.held_point(hold)) - om.MPoint(*target)).length()
            if now >= left - 1.0e-9:
                left = now
                break
            left = now
        self.key_hold(hold, key)
        return left

    def grab_to(
        self,
        hold: Dict[str, Any],
        target: Sequence[float],
        turn: Optional[Sequence[float]] = None,
        key: Optional[bool] = None,
    ) -> List[float]:
        """Move a hold's point to the world *target* and pose the rig there.

        Parameters:
            hold: From :meth:`grab_begin`.
            target: Where the held point goes, world space.
            turn: The hand's rotation since the hold began, a world-space
                ``(x, y, z, w)``, applied to the held link when it hangs off a
                ball. None keeps the link as it was taken (a mouse).
            key: Key what moved (default: Maya's auto key).

        Returns:
            The new state (the pose, as :meth:`state` reads it).
        """
        if "end" in hold:
            self._drag_end(hold, target, turn)
            self._settle_end(hold, target)
            self.key_hold(hold, key)
            return self.state(hold["slots"])
        model = hold["model"]
        inverse, space = self._space()
        goal = om.MPoint(*target) * inverse
        held = om.MQuaternion(*hold["rotation"])
        if turn is not None:
            # MQuaternion composes in Maya's row order -- ``a * b`` applies a,
            # then b. Seen from the rig space a world turn is: into the world,
            # turn, back out.
            turn_rig = space * om.MQuaternion(*turn) * space.conjugate()
            held = held * turn_rig
        state = model.solve(
            self.state(hold["slots"]),
            hold["joint"],
            hold["local"],
            (goal.x, goal.y, goal.z),
            (held.x, held.y, held.z, held.w),
        )
        self.set_state(state, key=key, slots=hold["slots"])
        return state

    def _drag_end(
        self,
        hold: Dict[str, Any],
        target: Sequence[float],
        turn: Optional[Sequence[float]],
    ) -> None:
        """Carry the end control rigidly with the held point: turned by the
        hand's *turn* about the point (a ball-mounted end link only -- a hinged
        one has no turn of its own to take, and its control's rotation is
        locked), then moved so the point is on *target*."""
        end = hold["end"]
        taken = om.MMatrix(end["matrix"])
        point = om.MVector(*end["point"])
        moved = taken
        ball = cmds.attributeQuery(self.FOLLOW_ATTR, node=end["control"], exists=True)
        if turn is not None and ball:
            about = om.MTransformationMatrix()
            about.setTranslation(-point, om.MSpace.kTransform)
            spin = om.MTransformationMatrix()
            spin.setRotation(om.MQuaternion(*turn))
            moved = moved * about.asMatrix() * spin.asMatrix()
            moved = moved * about.asMatrix().inverse()
        shift = om.MTransformationMatrix()
        shift.setTranslation(om.MVector(*target) - point, om.MSpace.kTransform)
        self._place(end["control"], moved * shift.asMatrix())

    def _settle_end(self, hold: Dict[str, Any], target: Sequence[float]) -> None:
        """Nudge a dragged end control until the held point is on *target*.
        Carried rigidly, the control lands the point only when the end link
        keeps its turn to the control (a ball following it); a hinged end
        turns with the solve, so a point off the pivot drifts -- the next
        pass takes up what is left. A pass that gains nothing (the target out
        of reach) is undone, so the control never runs off ahead of the arm.
        """
        ctrl = hold["end"]["control"]
        box = cmds.exactWorldBoundingBox(ctrl)
        tolerance = 1.0e-4 * math.dist(box[:3], box[3:])
        goal = om.MPoint(*target)
        miss = goal - om.MPoint(*self.held_point(hold))
        for _ in range(self.END_DRAG_PASSES):
            if miss.length() <= tolerance:
                return
            cmds.move(miss.x, miss.y, miss.z, ctrl, relative=True, worldSpace=True)
            after = goal - om.MPoint(*self.held_point(hold))
            if after.length() >= miss.length():
                cmds.move(
                    -miss.x, -miss.y, -miss.z, ctrl, relative=True, worldSpace=True
                )
                return
            miss = after

    def key_hold(self, hold: Dict[str, Any], key: Optional[bool] = None) -> None:
        """Key what a hold moved -- the end control it dragged, else the FK
        controls -- when *key* (default: Maya's auto key)."""
        if key is None:
            key = bool(cmds.autoKeyframe(query=True, state=True))
        if not key:
            return
        if "end" in hold:
            self._key_end(True)
            return
        for ctrl, channel in hold["slots"]:
            cmds.setKeyframe(ctrl, attribute=channel)
