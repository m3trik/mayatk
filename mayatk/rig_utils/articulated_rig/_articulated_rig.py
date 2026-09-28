# !/usr/bin/python
# coding=utf-8
"""Articulated Rig engine -- rigid parts on hinge, swivel, ball and slide joints.

For props that move like a desk lamp or a magnifier arm: every part is rigid,
and it is WHERE the parts meet, and how, that the rig is about. The engine
builds three things from one plan:

- **a skeleton that ships**: one joint per moving part, the part parented
  under it, the rest pose in ``jointOrient`` so every channel reads 0 at rest.
  It is the deliverable -- Unity and the WebXR runtime pose these joints --
  so nothing on it is apparatus, and every joint has a part below it (the
  export's rig-helper sweep keeps an ancestor of content);
- **FK controls**, nested, one per joint, whose channels ARE the joint's: a
  hinge control keys ``rz`` and that value is wired straight into the joint's
  ``rz``. What an animator keys is what an engine plays back and what a grab
  writes (``ptk.ArticulationModel`` works in the same numbers);
- **the ``articulation`` record** (``ptk.SceneRecords.ARTICULATION``, produced
  at export by :meth:`ArticulatedRig.export_record`) that tells the runtimes
  each joint's rest frame, channels and limits, and which parts a hand grabs.

The plan comes from the parts' geometry (:meth:`ArticulatedRig.analyze` over
``ptk.ArticulationAnalysis``) or from the caller; either way it is stored on
the rig group (:attr:`ArticulatedRig.DATA_ATTR`) in the group's own space, and
every edit after the build -- a joint inserted, removed or retyped -- is an
edit of that plan and a rebuild that carries the animation across
(:meth:`ArticulatedRig.rebuild`). A joint inserted at 0 changes no pose, so the
keys already made stay valid (:meth:`ArticulatedRig.insert_joint`).
"""

from __future__ import annotations

import copy
import json
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
from mayatk.rig_utils.controls import Controls


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
    #: (utility nodes, controller tags, the controls set): ownership by
    #: connection, so a teardown deletes exactly those, however renamed.
    NODES_ATTR = "articulatedRigNodes"
    #: The plan's schema.
    VERSION = 1
    JOINT_TYPES = ptk.ArticulationAnalysis.JOINT_TYPES
    #: Control preset and its normal axis per joint type: a hinge's ring lies
    #: in the plane it turns in, a swivel's around the link, a slide's arrow
    #: along the travel.
    CONTROL_SHAPES: Dict[str, Tuple[str, str]] = {
        "hinge": ("circle", "z"),
        "swivel": ("circle", "x"),
        "universal": ("target", "y"),
        "ball": ("ball", "y"),
        "slide": ("two_way_arrow", "y"),
    }
    #: Maya override colours per joint type.
    CONTROL_COLORS: Dict[str, int] = {
        "hinge": 17,
        "swivel": 18,
        "universal": 18,
        "ball": 6,
        "slide": 14,
    }
    #: A control's size over its link's body radius.
    CONTROL_SCALE = 2.5

    def __init__(self, group: str):
        path = self._long(group)
        if not path or not cmds.attributeQuery(self.DATA_ATTR, node=path, exists=True):
            raise ValueError(f"{group!r} is not an articulated rig group.")
        self._group_uuid = self._uuid(path)
        stamped = (self.scene_data(path) or {}).get("group_uuid")
        if stamped and stamped != self._group_uuid:
            # cmds.duplicate copies the record verbatim, parts' uuids and all:
            # acting on it would tear the ORIGINAL's parts out of their rig.
            raise ValueError(
                f"{group!r} carries another rig's record (a duplicate?); rebuild "
                "it from its parts instead."
            )

    # ================================================================ lookup
    @property
    def group(self) -> str:
        path = self._by_uuid(self._group_uuid)
        if not path:
            raise RuntimeError("The rig group no longer exists.")
        return path

    @property
    def spec(self) -> Dict[str, Any]:
        """The stamped plan (a fresh copy): name, links, joints, parts."""
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
        """The rig *node* belongs to -- its group, a joint, a control, a part
        riding a joint, or a part of the fixed root link -- or None."""
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
            "weights": {k: float(v) for k, v in (joint.get("weights") or {}).items()},
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
        frames: Dict[int, "om.MMatrix"] = {}
        index: Dict[int, int] = {}
        out = []
        for i, joint in enumerate(spec["joints"]):
            frame = cls._frame(joint["position"], joint["aim"], joint["normal"])
            parent_frame = frames.get(joint["parent"])
            local = frame if parent_frame is None else frame * parent_frame.inverse()
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
            frames[joint["link"]] = frame
            index[joint["link"]] = i
        return out

    @classmethod
    def _build(cls, spec: Dict[str, Any], home: Optional[str]) -> "ArticulatedRig":
        name = spec["name"]
        links = [[cls._by_uuid(m["uuid"]) for m in link] for link in spec["links"]]
        group = cls._long(
            cmds.createNode(
                "transform", name=f"{name}_RIG", **({"parent": home} if home else {})
            )
        )
        cmds.addAttr(group, longName=cls.DATA_ATTR, dataType="string")
        cmds.addAttr(
            group, longName=cls.NODES_ATTR, attributeType="message", multi=True
        )
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

        owned: List[str] = []
        controls: Dict[int, str] = {}
        for joint, pose in zip(spec["joints"], rest):
            channels, order = cls.JOINT_TYPES[joint["type"]]
            shape, axis = cls.CONTROL_SHAPES[joint["type"]]
            nodes = Controls.create(
                shape,
                name=f"{name}_{joint['id']}",
                size=max(joint.get("size") or 1.0, 1.0e-3),
                axis=axis,
                match=joints[joint["link"]],
                parent=controls.get(joint["parent"], controls_grp),
                color=cls.CONTROL_COLORS[joint["type"]],
                return_nodes=True,
            )
            ctrl = cls._long(nodes.control)
            controls[joint["link"]] = ctrl
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
            owned += cls._wire(ctrl, joints[joint["link"]], channels, pose)
            if joint["parent"] in controls:
                cmds.controller(ctrl, controls[joint["parent"]], parent=True)
            owned += cmds.listConnections(f"{ctrl}.message", type="controller") or []

        owned.append(cmds.sets(list(controls.values()), name=f"{name}_controls_SET"))
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
        cls, ctrl: str, joint: str, channels: Sequence[str], pose: Dict[str, Any]
    ) -> List[str]:
        """The control's channels into the joint's. A rotation is a wire; a
        slide moves the joint along its rest axis in the PARENT's space --
        ``translate = rest + axis * tx`` -- through two utility nodes the
        export bake walks straight through to the control. Returns the nodes
        made."""
        made: List[str] = []
        for channel in channels:
            if channel.startswith("r"):
                cmds.connectAttr(f"{ctrl}.{channel}", f"{joint}.{channel}")
                continue
            leaf = cls._leaf(joint)
            axis = pose["axes"]["xyz".index(channel[1])]
            md = cmds.createNode("multiplyDivide", name=f"{leaf}_{channel}_MD")
            pma = cmds.createNode("plusMinusAverage", name=f"{leaf}_{channel}_PMA")
            for i, component in enumerate("XYZ"):
                cmds.connectAttr(f"{ctrl}.{channel}", f"{md}.input1{component}")
                cmds.setAttr(f"{md}.input2{component}", axis[i])
            cmds.connectAttr(f"{md}.output", f"{pma}.input3D[0]")
            cmds.setAttr(f"{pma}.input3D[1]", *pose["t"], type="double3")
            cmds.connectAttr(f"{pma}.output3D", f"{joint}.translate")
            made += [md, pma]
        return made

    @classmethod
    def _own(cls, group: str, node: str) -> None:
        plug = f"{group}.{cls.NODES_ATTR}"
        index = max(cmds.getAttr(plug, multiIndices=True) or [-1]) + 1
        cmds.connectAttr(f"{node}.message", f"{plug}[{index}]")

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
        the animation across: every control channel's curve -- or its value --
        lands on the rebuilt control of the same joint id and channel. A
        channel the new plan no longer has loses its curve, with a warning.

        This instance follows the new group; it is also returned.
        """
        parked = self._park()
        return self._replace(spec or self.spec, parked)

    def _replace(
        self, spec: Dict[str, Any], parked: Dict[Tuple[str, str], Tuple[str, Any]]
    ) -> "ArticulatedRig":
        """Tear the rig down, build *spec*, and land the *parked* animation on
        it. The plan is checked FIRST, while the rig still stands: a plan the
        build would refuse leaves it standing, its animation put back."""
        home = self._parent(self.group)
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
        self._group_uuid = rig._group_uuid
        self._unpark(parked)
        return self

    def _park(self) -> Dict[Tuple[str, str], Tuple[str, Any]]:
        """Every control channel's animation curve, disconnected and kept, or
        its value -- keyed by (joint id, channel), which a rebuild keeps."""
        parked: Dict[Tuple[str, str], Tuple[str, Any]] = {}
        for joint in self.spec.get("joints", []):
            ctrl = self.control(joint["id"])
            for channel in self.JOINT_TYPES[joint["type"]][0]:
                plug = f"{ctrl}.{channel}"
                source = (
                    cmds.listConnections(
                        plug, source=True, destination=False, plugs=True
                    )
                    or []
                )
                curve = source[0].split(".")[0] if source else None
                if curve and cmds.nodeType(curve).startswith("animCurve"):
                    cmds.disconnectAttr(source[0], plug)
                    parked[(joint["id"], channel)] = ("curve", curve)
                else:
                    parked[(joint["id"], channel)] = ("value", cmds.getAttr(plug))
        return parked

    def _unpark(self, parked: Dict[Tuple[str, str], Tuple[str, Any]]) -> None:
        channels = {
            (j["id"], c)
            for j in self.spec.get("joints", [])
            for c in self.JOINT_TYPES[j["type"]][0]
        }
        dropped = []
        for (joint_id, channel), (kind, value) in parked.items():
            if (joint_id, channel) not in channels:
                if kind == "curve" and cmds.objExists(value):
                    cmds.delete(value)
                    dropped.append(f"{joint_id}.{channel}")
                continue
            plug = f"{self.control(joint_id)}.{channel}"
            if kind == "curve":
                cmds.connectAttr(f"{value}.output", plug, force=True)
            else:
                cmds.setAttr(plug, value)
        if dropped:
            self.logger.warning(
                f"Animation dropped with its channel: {', '.join(dropped)}."
            )

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
        0, which changes no pose: every key already made still holds.

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
            self.set_state([0.0] * len(self._slots()), key=False)  # planned at rest
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
            home = self._parent(self.group)
            links = [[self._by_uuid(m["uuid"]) for m in link] for link in spec["links"]]
            joint = self._plan_joint(
                proposal, links + [split_nodes], self._matrix(home).inverse()
            )

            # The joints beyond the split that the split-off parts now carry.
            near_keep = self._points(keep_nodes)
            near_split = self._points(split_nodes)
            world = self._matrix(home)
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
        world = self._matrix(self._parent(self.group))
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

    @CoreUtils.undoable(name="Articulated Rig: Set Limits")
    def set_limits(
        self,
        joint_id: str,
        channel: str,
        minimum: Optional[float],
        maximum: Optional[float],
    ) -> None:
        """Bound one channel (degrees, or a slide's units in the rig's space);
        None frees that side. No rebuild: the control and the record change
        in place."""
        spec = self.spec
        joint = next((j for j in spec["joints"] if j["id"] == joint_id), None)
        if joint is None or channel not in self.JOINT_TYPES[joint["type"]][0]:
            raise KeyError(f"No channel {channel!r} on joint {joint_id!r}.")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError(f"Limits [{minimum}, {maximum}] are reversed.")
        joint.setdefault("limits", {})[channel] = [minimum, maximum]
        self._stamp(spec)
        self._apply_limits(self.control(joint_id), joint["limits"], [channel])
        self.refresh_export_metadata()

    def set_limit_from_pose(self, joint_id: str, channel: str, side: str) -> float:
        """Take the control's current value as its *side* (``"min"`` or
        ``"max"``) limit -- pose the part to where it physically stops, then
        call this. Returns the value taken."""
        value = cmds.getAttr(f"{self.control(joint_id)}.{channel}")
        spec = self.spec
        joint = next(j for j in spec["joints"] if j["id"] == joint_id)
        lo, hi = (joint.get("limits") or {}).get(channel) or (None, None)
        if side == "min":
            lo = value
        elif side == "max":
            hi = value
        else:
            raise ValueError(f"side must be 'min' or 'max', not {side!r}.")
        self.set_limits(joint_id, channel, lo, hi)
        return value

    # ============================================================== controls
    def joint_ids(self) -> List[str]:
        """The joints' ids -- each its link's first part -- parent first."""
        return [j["id"] for j in self.spec.get("joints", [])]

    def joint_id_of(self, node) -> Optional[str]:
        """The id of the joint *node* is -- its control, its joint -- or rides
        (a part, or anything under one); None for the fixed root link's."""
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
        """The control of *joint_id*."""
        affix = ptk.NamingConvention.affix("control")
        return self._named(f"{self.name}_{joint_id}{affix}", "transform")

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
        """The controls' channel values, in :meth:`model` state order."""
        return [cmds.getAttr(f"{c}.{ch}") for c, ch in (slots or self._slots())]

    def set_state(
        self,
        values: Sequence[float],
        key: Optional[bool] = None,
        slots: Optional[Sequence[Tuple[str, str]]] = None,
    ) -> None:
        """Pose the rig: *values* onto the controls, keyed when *key*
        (default: whatever Maya's auto key says)."""
        if key is None:
            key = bool(cmds.autoKeyframe(query=True, state=True))
        for (ctrl, channel), value in zip(slots or self._slots(), values):
            cmds.setAttr(f"{ctrl}.{channel}", value)
            if key:
                cmds.setKeyframe(ctrl, attribute=channel)

    # ================================================================ record
    def record(self) -> Dict[str, Any]:
        """This rig's entry in the ``articulation`` record: each joint's rest
        (translate and joint orient in its parent's space), rotate order and
        channels with their limits and weights, and the parts a hand grabs.
        Joints are parent first; ``parent`` is an index into them."""
        spec = self.spec
        joints, grab = [], []
        for index, (joint, pose) in enumerate(zip(spec["joints"], self._rest(spec))):
            channels, order = self.JOINT_TYPES[joint["type"]]
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
            # The part's name NOW (a runtime binds by it): a rename after the
            # build must not leave the record naming a node that is gone.
            grab += [
                {
                    "node": self._leaf(self._by_uuid(m["uuid"]) or m["name"]),
                    "joint": index,
                }
                for m in spec["links"][joint["link"]]
            ]
        return {"name": spec["name"], "joints": joints, "grab": grab}

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

        Returns:
            The hold :meth:`grab_to` moves: the joint, the point in its frame,
            the link's rig-space rotation as taken, and the model and control
            slots (read once, not per drag).
        """
        model = self.model()
        slots = self._slots()
        state = self.state(slots)
        joint = self._joint_of(node)
        inverse, _space = self._space()
        local = om.MPoint(*point) * inverse
        return {
            "joint": joint,
            "local": model.to_local(state, joint, (local.x, local.y, local.z)),
            "rotation": model.world(state)[joint][1],
            "model": model,
            "slots": slots,
        }

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
        if key is None:
            key = bool(cmds.autoKeyframe(query=True, state=True))
        if key:
            self.set_state(self.state(hold["slots"]), key=True, slots=hold["slots"])
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
            key: Key the controls (default: Maya's auto key).

        Returns:
            The new state.
        """
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
