# !/usr/bin/python
# coding=utf-8
"""Smart bake module for intelligent pre-bake animation processing.

Analyzes scene objects to detect what requires baking:
- Constraints (parent, point, orient, scale, aim)
- Set Driven Keys (animCurveU* with input connections)
- Expressions
- IK chains (joints driven by ikHandle/ikEffector)
- Motion paths
- Animation layers (anim blend nodes)
- Blend shape weights driven by SDKs/expressions

Auto-detects optimal time range from driver animation.
Designed for Unity/game engine export workflows.
"""

import math
import re
import collections
from contextlib import contextmanager
from typing import Dict, Iterable, List, Optional, Set, Tuple, Union, TYPE_CHECKING
from dataclasses import dataclass, field

try:
    from maya import cmds
except ImportError as error:
    print(__file__, error)

if TYPE_CHECKING:
    import maya.api.OpenMaya as om2  # annotations only; imported per method

    # Resolves the ``restore()`` return annotation for type-checkers only; the
    # real import is done lazily inside the method, keeping bake_session out of
    # module load like the other deferred imports here.
    from mayatk.anim_utils.smart_bake.bake_session import RestoreResult

import pythontk as ptk
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.anim_utils._anim_utils import STANDARD_TRANSFORM_ATTRS
from mayatk.node_utils.attributes._attributes import Attributes


@dataclass
class BakeAnalysis:
    """Analysis result for a single object's bake requirements."""

    object: str
    """The object name being analyzed."""

    driven_channels: Dict[str, List[str]] = field(default_factory=dict)
    """Channels driven by non-keyframe sources. {source_type: [channel_names]}"""

    source_nodes: Dict[str, List[str]] = field(default_factory=dict)
    """Source nodes driving this object. {source_type: [node_names]}"""

    already_keyed: List[str] = field(default_factory=list)
    """Channels that already have direct time-based keyframes."""

    @property
    def requires_bake(self) -> bool:
        """Return True if this object has any driven channels needing bake."""
        return bool(self.driven_channels)

    @property
    def all_driven_channels(self) -> List[str]:
        """Return flat list of all channels that need baking."""
        channels = []
        for ch_list in self.driven_channels.values():
            channels.extend(ch_list)
        return list(set(channels))


@dataclass
class BakeResult:
    """Result container for SmartBake.bake() operation."""

    baked: Dict[str, List[str]] = field(default_factory=dict)
    """Objects that were baked. {object: [channels]}"""

    skipped: List[str] = field(default_factory=list)
    """Objects skipped (no driven channels or bake failed)."""

    time_range: Tuple[int, int] = (0, 0)
    """Time range used for baking (start, end): the union over every baked
    object, and the range any object with an unknowable driver was baked
    over (see ``object_time_ranges``)."""

    object_time_ranges: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    """The range each baked object was actually sampled over. Narrower than
    ``time_range`` for an object whose drivers all resolve to keyed curves
    (their extent), and a single frame ``(start, start)`` for an object
    whose drivers are provably static -- see ``SmartBake.get_object_time_ranges``."""

    deleted: List[str] = field(default_factory=list)
    """Source nodes deleted (if delete_inputs=True)."""

    optimized: List[str] = field(default_factory=list)
    """Objects that had keys optimized (if optimize_keys=True)."""

    override_layer: Optional[str] = None
    """Name of override layer created (if use_override_layer=True)."""

    visibility_curves: Dict[str, str] = field(default_factory=dict)
    """Base-layer visibility animCurves **created** by the inherited-vis bake.
    Maps ``{object_long_name: animCurve_node}`` so the caller can
    delete them after export to restore the scene.

    Deliberately excludes objects that already had their own ``.visibility``
    curve: there the baked keys MERGED into the artist's curve, so deleting
    it would destroy authored animation.  Those objects still appear in
    ``baked``; reverse them with ``SmartBake.restore()``, never by deletion
    (and a non-restorable session refuses to bake them at all)."""

    visibility_originals: Dict[str, float] = field(default_factory=dict)
    """Original ``.visibility`` values before bake, for cleanup restoration.
    Maps ``{object_long_name: original_value}``."""

    backup_path: Optional[str] = None
    """Path to backup file saved (if backup_file was used)."""

    muted_drivers: List[str] = field(default_factory=list)
    """Driver nodes that were muted (if mute_drivers=True)."""

    session_id: Optional[str] = None
    """Id of the restore-manifest session recorded for this bake (if
    restorable=True). Pass to ``SmartBake.restore()`` to reverse the bake —
    the manifest persists on the ``data_internal`` node, so restore works
    even after scene save/reopen."""

    @property
    def baked_count(self) -> int:
        """Number of objects successfully baked."""
        return len(self.baked)

    @property
    def success(self) -> bool:
        """Return True if any objects were baked."""
        return bool(self.baked)


class _SmartBakeInternal:
    """Internal helpers for SmartBake."""

    @staticmethod
    def _long_names(names: List[str]) -> Dict[str, str]:
        """``{name: long DAG path}`` for *names*, each resolved as given.

        One ``ls -long`` over the batch; only a shortfall (a name that no
        longer resolves, or resolves to several) falls back to one query per
        name, so a stale path costs its own lookup and nothing else.
        """
        names = list(names)
        longs = cmds.ls(names, long=True) or []
        if len(longs) == len(names):
            return dict(zip(names, longs))
        return {n: (cmds.ls(n, long=True) or [n])[0] for n in names}

    @staticmethod
    def _node_types(nodes: List[str]) -> Dict[str, str]:
        """``{node: nodeType}`` from one ``ls -showType`` over *nodes*."""
        flat = cmds.ls(list(nodes), showType=True) or []
        return dict(zip(flat[0::2], flat[1::2]))

    @staticmethod
    def _curves_with_input(curves: List[str]) -> Set[str]:
        """The animCurves among *curves* whose ``.input`` is connected -- the
        set-driven keys -- from one ``listConnections`` over the batch."""
        if not curves:
            return set()
        pairs = (
            cmds.listConnections(
                [f"{curve}.input" for curve in curves],
                source=True,
                destination=False,
                connections=True,
                plugs=True,
            )
            or []
        )
        return {dest.split(".")[0] for dest in pairs[0::2]}

    @staticmethod
    def _blend_shapes_of(objects: List[str]) -> Set[str]:
        """blendShape deformers feeding the shapes under *objects* -- one
        ``listRelatives`` and one ``listConnections`` over the batch."""
        try:
            shapes = (
                cmds.listRelatives(
                    objects, shapes=True, noIntermediate=True, fullPath=True
                )
                or []
            )
        except (RuntimeError, ValueError):
            # A name that no longer resolves fails the batch inside Maya.
            shapes = []
            for obj in objects:
                try:
                    shapes += (
                        cmds.listRelatives(
                            obj, shapes=True, noIntermediate=True, fullPath=True
                        )
                        or []
                    )
                except (RuntimeError, ValueError):
                    continue
        if not shapes:
            return set()
        return set(
            cmds.listConnections(
                shapes, type="blendShape", source=True, destination=False
            )
            or []
        )

    @staticmethod
    def _nearest_euler(euler, previous):
        """*euler* (or its alternate solution), unwrapped to sit nearest *previous*.

        The Euler filter a per-frame decomposition needs: each frame's split
        is independent, so without this a rotation passing 180 degrees flips
        to the equivalent (x+180, 180-y, z+180) triple and the curve jumps.
        """
        import maya.api.OpenMaya as om2

        if previous is None:
            return euler
        best = None
        best_cost = None
        for candidate in (euler, euler.alternateSolution()):
            comps = []
            for value, prior in zip(
                (candidate.x, candidate.y, candidate.z),
                (previous.x, previous.y, previous.z),
            ):
                value += 2.0 * math.pi * round((prior - value) / (2.0 * math.pi))
                comps.append(value)
            cost = sum(
                abs(v - p) for v, p in zip(comps, (previous.x, previous.y, previous.z))
            )
            if best_cost is None or cost < best_cost:
                best_cost = cost
                best = om2.MEulerRotation(comps[0], comps[1], comps[2], euler.order)
        return best

    @classmethod
    def _write_matrix_keys(
        cls,
        obj: str,
        channels: List[str],
        frames: List[int],
        samples: Dict[int, "om2.MMatrix"],
    ) -> bool:
        """Key *channels* of *obj* from per-frame local matrices through om2.

        Per frame: ``MTransformationMatrix`` (Maya's own decomposition) gives
        scale and the full rotation; ``rotateAxis`` and ``jointOrient`` are
        factored back out, the result expressed in the node's rotate order
        and kept Euler-continuous with the previous frame. Scale and rotation
        are then set on the node's plugs with translation zeroed, so the
        node ITSELF resolves whatever else shapes its matrix -- pivots, pivot
        translates, orient -- and the translation is the difference between
        the sampled matrix and that partial one. The node's ``matrix`` plug
        must then reproduce the sample exactly, every frame, or nothing is
        written and the caller takes the cmds path (a locked plug refuses
        the set the same way; shear is not modelled, so a sheared sample
        fails the check by construction). Keys land through one
        ``MFnAnimCurve.addKeys`` per channel: no ``currentTime``, ``xform``
        or ``setKeyframe`` in the loop -- measured 11-14x the cmds pair,
        which was 69 s of a 118 s production-scale bake.

        The cmds pair cannot do this for a pivoted transform at all:
        ``xform -matrix`` folds the pivot compensation into
        ``rotatePivotTranslate``, which is never keyed, so every frame
        inherits the last frame's value (pinned by
        ``test_matrix_bake_on_pivoted_transform_matches_worlds``).

        Returns:
            True when the keys were written; False when nothing was touched.
        """
        import maya.api.OpenMaya as om2
        import maya.api.OpenMayaAnim as oma2

        sel = om2.MSelectionList()
        sel.add(obj)
        dep = om2.MFnDependencyNode(sel.getDependNode(0))
        order = cmds.getAttr(f"{obj}.rotateOrder")
        ra_inv = (
            om2.MEulerRotation(
                *[math.radians(v) for v in cmds.getAttr(f"{obj}.rotateAxis")[0]]
            )
            .asQuaternion()
            .inverse()
        )
        jo_inv = om2.MQuaternion()
        if cmds.attributeQuery("jointOrient", node=obj, exists=True):
            jo_inv = (
                om2.MEulerRotation(
                    *[math.radians(v) for v in cmds.getAttr(f"{obj}.jointOrient")[0]]
                )
                .asQuaternion()
                .inverse()
            )
        plugs = [dep.findPlug(name, False) for name in cls.MATRIX_BAKE_CHANNELS]
        matrix_plug = dep.findPlug("matrix", False)

        # matrix = [S][RA][R][JO][T] (pivot terms aside): the full rotation
        # Maya reports is RA * R * JO, so R = RA^-1 * (RA R JO) * JO^-1 in
        # Maya's row order.
        rows: List[List[float]] = []
        previous = None
        try:
            for frame in frames:
                target = samples[frame]
                mt = om2.MTransformationMatrix(target)
                s = mt.scale(om2.MSpace.kTransform)
                q = ra_inv * mt.rotation(asQuaternion=True) * jo_inv
                euler = _SmartBakeInternal._nearest_euler(
                    q.asEulerRotation().reorder(order), previous
                )
                previous = euler
                partial_values = (0.0, 0.0, 0.0, euler.x, euler.y, euler.z, *s)
                for plug, value in zip(plugs, partial_values):
                    plug.setDouble(value)
                partial = om2.MFnMatrixData(matrix_plug.asMObject()).matrix()
                t = [
                    target.getElement(3, axis) - partial.getElement(3, axis)
                    for axis in range(3)
                ]
                for plug, value in zip(plugs[:3], t):
                    plug.setDouble(value)
                rebuilt = om2.MFnMatrixData(matrix_plug.asMObject()).matrix()
                if not rebuilt.isEquivalent(target, 1e-5):
                    return False
                rows.append([*t, euler.x, euler.y, euler.z, *s])
        except RuntimeError:
            return False  # a locked or connected plug refused the set

        unit = om2.MTime.uiUnit()
        times = om2.MTimeArray([om2.MTime(frame, unit) for frame in frames])
        for index, (name, plug) in enumerate(zip(cls.MATRIX_BAKE_CHANNELS, plugs)):
            if name not in channels:
                continue
            values = [row[index] for row in rows]
            existing = (
                cmds.listConnections(
                    f"{obj}.{name}", type="animCurve", source=True, destination=False
                )
                or []
            )
            if existing:
                # setKeyframe semantics on the artist's curve: replace or
                # insert at each sampled time, every other key kept.
                sel.clear()
                sel.add(existing[0])
                curve = oma2.MFnAnimCurve(sel.getDependNode(0))
                for time_value, value in zip(times, values):
                    curve.addKey(time_value, value)
            else:
                curve = oma2.MFnAnimCurve()
                curve.create(plug)
                curve.addKeys(times, values)
        return True


class _TimeDependency:
    """Over which frames can a plug change?

    The walk behind :meth:`SmartBake.get_object_time_ranges`, run upstream
    from whatever drives an object until every wire reaches one of three
    answers:

    - **known** + key times -- the wire reduces to time-driven animCurves
      with constant infinity, so its value can only change between their
      first and last key.
    - **static** -- nothing time-dependent anywhere upstream.
    - **unknown** -- a node the walk cannot reason about.

    Combining is pessimistic: one unknown makes the whole answer unknown and
    the caller falls back to the global range, which is what every object got
    before this existed. An omission here therefore costs frames, never
    motion -- the only error that matters is a range too narrow for the
    motion in it.

    Results are memoised per plug and per node; a cycle (a constraint reading
    the node it constrains, a target-weight alias feeding its own compound,
    an IK chain joint reporting the handle that solves it) resolves as
    unknown rather than recursing.
    """

    #: Nodes whose output is a pure function of their incoming connections.
    #: The walk continues THROUGH these to whatever animates them; a type not
    #: listed ends the walk as unknown. Deliberately a whitelist -- a node
    #: nobody vetted stays conservative. This asks what a driver DEPENDS ON,
    #: a different question from ``Attributes.PASSTHROUGH_TYPES`` (what a
    #: driver IS: a matrix network is reported as a matrix drive, not as
    #: whatever feeds it).
    DERIVED_TYPES: Set[str] = set(Attributes.PASSTHROUGH_TYPES) | {
        # scalar math beyond the passthrough set
        "remapColor",
        "remapHsv",
        "choice",
        "gammaCorrect",
        # matrix
        "multMatrix",
        "addMatrix",
        "wtAddMatrix",
        "composeMatrix",
        "decomposeMatrix",
        "inverseMatrix",
        "transposeMatrix",
        "blendMatrix",
        "pickMatrix",
        "aimMatrix",
        "parentMatrix",
        "holdMatrix",
        "passMatrix",
        "fourByFourMatrix",
        "pointMatrixMult",
        "rowFromMatrix",
        "columnFromMatrix",
        # measurement
        "distanceBetween",
        "curveInfo",
        "arcLengthDimension",
        "angleBetween",
        "vectorProduct",
        "pointOnCurveInfo",
        "pointOnSurfaceInfo",
        "closestPointOnMesh",
        "closestPointOnSurface",
        "nearestPointOnCurve",
        "uvPin",
        "proximityPin",
        # deformers -- pure functions of their influences and input geometry
        "skinCluster",
        "blendShape",
        "cluster",
        "ffd",
        "lattice",
        "wire",
        "deltaMush",
        "softMod",
        "sculpt",
        "nonLinear",
        "tweak",
        "groupParts",
        "groupId",
        "transformGeometry",
    }

    #: A DAG node's WORLD placement: its own locals and every ancestor.
    _WORLD_MATRIX_ATTRS: Set[str] = {"worldMatrix", "worldInverseMatrix"}

    #: Its PARENT's placement -- the ancestors WITHOUT the node itself, which
    #: is what lets a constraint read ``constraintParentInverseMatrix``
    #: without walking back into the node it drives.
    _PARENT_MATRIX_ATTRS: Set[str] = {"parentMatrix", "parentInverseMatrix"}

    #: Its LOCAL matrix only.
    _LOCAL_MATRIX_OUT_ATTRS: Set[str] = {"matrix", "inverseMatrix", "xformMatrix"}

    #: Wiring that describes an IK handle's STRUCTURE rather than its input;
    #: :meth:`ik` follows these explicitly and skips them in its generic walk.
    _IK_STRUCTURAL_ATTRS: Set[str] = {
        "ikSolver",
        "startJoint",
        "endEffector",
        "inCurve",
    }

    #: Transform attributes whose input can move a node's LOCAL matrix. An
    #: incoming wire on any of them decides the node's state.
    _LOCAL_MATRIX_ATTRS: Set[str] = {
        "translate",
        "translateX",
        "translateY",
        "translateZ",
        "rotate",
        "rotateX",
        "rotateY",
        "rotateZ",
        "scale",
        "scaleX",
        "scaleY",
        "scaleZ",
        "shear",
        "shearXY",
        "shearXZ",
        "shearYZ",
        "rotatePivot",
        "rotatePivotTranslate",
        "scalePivot",
        "scalePivotTranslate",
        "rotateAxis",
        "rotateOrder",
        "jointOrient",
        "inheritsTransform",
        "offsetParentMatrix",
        "inverseScale",
        "segmentScaleCompensate",
    }

    #: Expression text that makes its output time-dependent or
    #: non-deterministic however static its inputs are.
    _DYNAMIC_EXPRESSION = re.compile(
        r"\b(time|frame|rand|noise|gauss|sphrand|seed|dnoise)\b"
    )

    #: Wires deep past which the walk gives up. A rig does not nest this
    #: far, and unknown is the safe answer for a graph that does. Kept well
    #: under Python's own recursion limit: each wire costs about six frames,
    #: and a RecursionError here would abort the bake rather than widen a
    #: range.
    _MAX_DEPTH: int = 100

    UNKNOWN: Tuple[str, List[float]] = ("unknown", [])
    STATIC: Tuple[str, List[float]] = ("static", [])

    def __init__(self):
        from mayatk.anim_utils._anim_utils import AnimUtils

        self._time_curve_types = set(AnimUtils.TIME_CURVE_TYPES)
        self._wired_cache: Dict[str, Tuple[str, List[float]]] = {}
        self._local_cache: Dict[str, Tuple[str, List[float]]] = {}
        self._plug_cache: Dict[str, Tuple[str, List[float]]] = {}
        self._source_cache: Dict[str, Tuple[str, List[float]]] = {}
        self._driver_cache: Dict[Tuple[str, str], Tuple[str, List[float]]] = {}
        self._curve_cache: Dict[str, Optional[List[float]]] = {}
        self._type_cache: Dict[str, str] = {}
        self._constraint_cache: Dict[str, bool] = {}
        self._long_cache: Dict[str, str] = {}
        self._ik_handles: Optional[Dict[str, List[str]]] = None
        self._visiting: Set[str] = set()

    # -- primitives ---------------------------------------------------------

    def combine(
        self, parts: Iterable[Tuple[str, List[float]]]
    ) -> Tuple[str, List[float]]:
        """Every dependency of one thing, folded into a single answer.

        Collapsed to ``[first, last]`` at every step, not accumulated: only
        the extremes are ever read, and a rig reaches the same curve down
        hundreds of paths -- keeping each path's times ran a production
        scene out of memory before it finished resolving.
        """
        first = last = None
        state = "static"
        for part_state, part_times in parts:
            if part_state == "unknown":
                return self.UNKNOWN
            if part_state == "known":
                state = "known"
                if part_times:
                    low, high = min(part_times), max(part_times)
                    first = low if first is None else min(first, low)
                    last = high if last is None else max(last, high)
        return (state, [] if first is None else [first, last])

    def node_type(self, node: str) -> str:
        if node not in self._type_cache:
            try:
                self._type_cache[node] = cmds.nodeType(node)
            except Exception:  # a name that no longer resolves
                self._type_cache[node] = ""
        return self._type_cache[node]

    def is_constraint(self, node: str) -> bool:
        from mayatk.node_utils._node_utils import NodeUtils

        if node not in self._constraint_cache:
            self._constraint_cache[node] = bool(NodeUtils.is_constraint(node))
        return self._constraint_cache[node]

    def long_name(self, node: str) -> str:
        if node not in self._long_cache:
            self._long_cache[node] = (cmds.ls(node, long=True) or [node])[0]
        return self._long_cache[node]

    def ik_handles_of(self, node: str) -> List[str]:
        """The IK handles whose chain spans *node*, whose solutions move it
        without any connection to say so."""
        if self._ik_handles is None:
            from mayatk.rig_utils._rig_utils import RigUtils

            self._ik_handles = RigUtils.ik_handles_by_joint()
        return self._ik_handles.get(self.long_name(node), [])

    @staticmethod
    def _split(plug: str) -> Tuple[str, str]:
        """``(node, root attribute)`` of a plug, indices and children dropped."""
        node, _, attr = plug.partition(".")
        return node, attr.split(".")[0].split("[")[0]

    @staticmethod
    def _sources(node_or_plug: str) -> List[str]:
        return (
            cmds.listConnections(
                node_or_plug, source=True, destination=False, plugs=True
            )
            or []
        )

    @staticmethod
    def ancestors_of(node: str) -> List[str]:
        parts = (cmds.ls(node, long=True) or [node])[0].split("|")[1:]
        return ["|" + "|".join(parts[:depth]) for depth in range(1, len(parts))]

    def curve_extent(self, curve: str) -> Optional[List[float]]:
        """[first, last] key time of a time curve, or None when its infinity
        keeps it moving past its keys."""
        if curve not in self._curve_cache:
            # 0 = constant; linear / cycle / cycleRelative / oscillate keep
            # the curve moving past its keys.
            if cmds.getAttr(f"{curve}.preInfinity") or cmds.getAttr(
                f"{curve}.postInfinity"
            ):
                self._curve_cache[curve] = None
            else:
                first = cmds.findKeyframe(curve, which="first")
                last = cmds.findKeyframe(curve, which="last")
                self._curve_cache[curve] = [float(first), float(last)]
        return self._curve_cache[curve]

    # -- the walk -----------------------------------------------------------

    def source(self, src_plug: str) -> Tuple[str, List[float]]:
        """State of one incoming wire, traced to its animated leaves.

        The dispatch every other method funnels through: an animCurve ends
        the walk with its key extent, a driver kind defers to :meth:`driver`,
        a DAG node's plug asks what that attribute (or that placement)
        depends on, a derived node recurses into everything feeding it, and
        anything else is unknown.
        """
        cached = self._source_cache.get(src_plug)
        if cached is not None:
            return cached
        if src_plug in self._visiting or len(self._visiting) > self._MAX_DEPTH:
            return self.UNKNOWN  # a cycle, or a graph too deep to be real
        self._visiting.add(src_plug)
        try:
            found = self._resolve_source(src_plug)
        finally:
            self._visiting.discard(src_plug)
        self._source_cache[src_plug] = found
        return found

    def _resolve_source(self, src_plug: str) -> Tuple[str, List[float]]:
        node, attr = self._split(src_plug)
        if attr == "message":
            # An identity reference, not data -- a skinCluster naming its
            # bindPose, a shader naming its material info. Nothing can
            # change through it, so it says nothing about time.
            return self.STATIC
        node_type = self.node_type(node)
        if not node_type:
            return self.UNKNOWN
        if node_type in self._time_curve_types:
            extent = self.curve_extent(node)
            return ("known", extent) if extent is not None else self.UNKNOWN
        if node_type == "expression":
            return self.driver("expression", node)
        if node_type == "motionPath":
            return self.driver("motion_path", node)
        if node_type == "ikHandle":
            return self.driver("ik", node)
        if node_type == "ikEffector":
            # A solver writes an effector's translation with no wire to
            # follow; the handle above it carries the chain's timing.
            return self.UNKNOWN
        if self.is_constraint(node):
            return self.driver("constraint", node)
        if cmds.objectType(node, isAType="dagNode"):
            return self._dag_source(node, attr)
        return self.node_output(node)

    def _dag_source(self, node: str, attr: str) -> Tuple[str, List[float]]:
        """What a plug ON a DAG node depends on.

        Which question to ask is the attribute's business: a world matrix
        carries the whole lineage, a parent matrix carries the ancestors
        WITHOUT the node (so a constraint reading it cannot walk back into
        the node it drives), a local matrix carries only that node, geometry
        carries its deformer chain, and anything else is just that attribute.
        """
        if attr in self._WORLD_MATRIX_ATTRS:
            return self.lineage(node)
        if attr in self._PARENT_MATRIX_ATTRS:
            return self.combine(self.local(a) for a in self.ancestors_of(node))
        if attr in self._LOCAL_MATRIX_OUT_ATTRS:
            return self.local(node)
        if cmds.objectType(node, isAType="geometryShape"):
            return self.geometry(node, attr)
        return self.plug(f"{node}.{attr}")

    def node_output(self, node: str) -> Tuple[str, List[float]]:
        """What a whole node's output can depend on: everything wired into
        it, for the node types whose output is a pure function of that."""
        if self.node_type(node) not in self.DERIVED_TYPES:
            return self.UNKNOWN
        sources = self._sources(node)
        return self.combine(self.source(s) for s in sources) if sources else self.STATIC

    def geometry(self, shape: str, attr: str) -> Tuple[str, List[float]]:
        """A shape's points: its deformer chain, plus -- for a world-space
        attribute -- where the shape is placed."""
        parts = [self.source(s) for s in self._sources(shape)]
        if attr.startswith("world"):
            parent = cmds.listRelatives(shape, parent=True, fullPath=True) or []
            if parent:
                parts.append(self.lineage(parent[0]))
        return self.combine(parts) if parts else self.STATIC

    def plug(self, plug: str) -> Tuple[str, List[float]]:
        """State of one attribute: what feeds it, or static when nothing does."""
        if plug not in self._plug_cache:
            sources = self._sources(plug)
            self._plug_cache[plug] = (
                self.combine(self.source(s) for s in sources)
                if sources
                else self.STATIC
            )
        return self._plug_cache[plug]

    def wired(self, node: str) -> Tuple[str, List[float]]:
        """Every wire into a node's matrix-shaping attributes, combined.

        The connections only -- :meth:`local` adds what solves the node
        without one. Kept separate so :meth:`ik` can ask about a chain joint
        without asking about the handle it is in the middle of resolving.
        """
        from mayatk.node_utils._node_utils import NodeUtils

        if node not in self._wired_cache:
            parts = []
            for dest, src in NodeUtils.incoming_connections([node]):
                attr = dest.split(".")[-1].split("[")[0]
                if attr in self._LOCAL_MATRIX_ATTRS:
                    parts.append(self.source(src))
            self._wired_cache[node] = self.combine(parts) if parts else self.STATIC
        return self._wired_cache[node]

    def local(self, node: str) -> Tuple[str, List[float]]:
        """A DAG node's own local matrix: its wiring, plus any IK handle
        whose solver writes its rotation with no connection to show for it."""
        if node not in self._local_cache:
            parts = [self.wired(node)]
            parts.extend(self.driver("ik", h) for h in self.ik_handles_of(node))
            self._local_cache[node] = self.combine(parts)
        return self._local_cache[node]

    def lineage(self, node: str) -> Tuple[str, List[float]]:
        """A node and every ancestor: what its WORLD matrix depends on."""
        if not cmds.objExists(node):
            return self.UNKNOWN
        return self.combine(self.local(n) for n in [node] + self.ancestors_of(node))

    def constraint(self, node: str) -> Tuple[str, List[float]]:
        """Everything a constraint solves from: its targets in world space
        and every other wire into it -- an aim constraint's up object, a
        keyed target weight, the constrained node's parent space.

        Its own weight aliases feed its own target compound; those are
        skipped rather than resolved as a cycle.
        """
        from mayatk.node_utils._node_utils import NodeUtils

        targets = NodeUtils.get_constraint_targets(node)
        if not targets:
            return self.UNKNOWN  # nothing to follow: nothing can be proven
        parts = [self.lineage(t) for t in targets]
        own = self.long_name(node)
        for src in self._sources(node):
            if self.long_name(self._split(src)[0]) != own:
                parts.append(self.source(src))
        return self.combine(parts)

    def ik(self, handle: str) -> Tuple[str, List[float]]:
        """An IK handle's solution: the handle's own placement and wiring
        (goal, twist, pole vector, up matrix), the curve a spline solver
        follows, the chain's root, and every chain joint's own locals -- a
        keyed bone length re-solves the chain.

        The chain is read through :meth:`wired`, never :meth:`local`: those
        joints name this handle, and asking them about it again would only
        find the answer being computed here.
        """
        from mayatk.node_utils._node_utils import NodeUtils

        parts = [self.lineage(handle)]
        for dest, src in NodeUtils.incoming_connections([handle]):
            attr = dest.split(".")[-1].split("[")[0]
            if attr not in self._IK_STRUCTURAL_ATTRS:
                parts.append(self.source(src))
        # A spline solver's chain follows the SHAPE of its curve, which is
        # usually skinned to driver joints animated somewhere else entirely.
        parts.extend(self.source(s) for s in self._sources(f"{handle}.inCurve"))
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
            return self.UNKNOWN
        parts.append(self.wired(start[0]))
        above = self.ancestors_of(start[0])
        if above:
            parts.append(self.lineage(above[-1]))  # the parent covers the rest
        start_long = self.long_name(start[0])
        current = self.long_name(end[0])
        while current and current != start_long:
            parts.append(self.wired(current))
            parent = cmds.listRelatives(
                current, parent=True, type="joint", fullPath=True
            )
            current = parent[0] if parent else None
        return self.combine(parts)

    def driver(self, source_type: str, node: str) -> Tuple[str, List[float]]:
        """State of one driver, dispatched on the kind ``analyze()`` gave it."""
        from mayatk.anim_utils._anim_utils import AnimUtils
        from mayatk.node_utils._node_utils import NodeUtils

        key = (source_type, node)
        if key in self._driver_cache:
            return self._driver_cache[key]
        self._driver_cache[key] = self.UNKNOWN  # breaks a walk back to itself
        if not cmds.objExists(node):
            found = self.UNKNOWN
        elif source_type == "constraint":
            found = self.constraint(node)
        elif source_type == "driven_key":
            inputs = (
                cmds.listConnections(
                    f"{node}.input", source=True, destination=False, plugs=True
                )
                or []
            )
            found = (
                self.combine(self.source(p) for p in inputs) if inputs else self.UNKNOWN
            )
        elif source_type == "expression":
            text = cmds.expression(node, query=True, string=True) or ""
            if self._DYNAMIC_EXPRESSION.search(text):
                found = self.UNKNOWN
            else:
                pairs = NodeUtils.incoming_connections([node])
                found = self.combine(
                    self.source(src)
                    for dest, src in pairs
                    if dest.split(".")[-1].split("[")[0] != "time"
                )
        elif source_type == "ik":
            found = self.ik(node)
        elif source_type == "motion_path":
            path_nodes = (
                cmds.listConnections(
                    f"{node}.geometryPath", source=True, destination=False
                )
                or []
            )
            shape_live = any(NodeUtils.incoming_connections([p]) for p in path_nodes)
            found = self.UNKNOWN if shape_live else self.plug(f"{node}.uValue")
            if found[0] == "static":
                found = self.UNKNOWN  # an unkeyed uValue still sits ON the path
        elif source_type.startswith("inherited_visibility"):
            times = AnimUtils.get_driver_animation_range(node, driver_type=source_type)
            found = ("known", [min(times), max(times)]) if times else self.UNKNOWN
        else:
            # A kind named after the node type itself: a matrix or utility
            # network, a measurement node. Ask what feeds it.
            found = self.node_output(node)
        self._driver_cache[key] = found
        return found

    def object_state(self, obj: str, data: "BakeAnalysis") -> Tuple[str, List[float]]:
        """Everything one analysed object's baked channels depend on."""
        parts = [
            self.driver(source_type, node)
            for source_type, nodes in data.source_nodes.items()
            for node in nodes
        ]
        parts.append(self.combine(self.local(a) for a in self.ancestors_of(obj)))
        return self.combine(parts)


class SmartBake(_SmartBakeInternal):
    """Intelligent baking with automatic detection of what needs to be baked.

    Analyzes objects to find:
    - Constraint-driven channels (parentConstraint, pointConstraint, etc.)
    - Set Driven Key channels (animCurveU* with input connections)
    - Expression-driven channels
    - IK-driven joint rotations

    Only bakes the specific channels that are driven, leaving already-keyed
    channels untouched. Auto-detects optimal time range from driver animation.

    Example:
        >>> baker = SmartBake()
        >>> result = baker.execute()
        >>> print(result.baked)  # Objects that were baked
        >>> print(result.time_range)  # Time range used
    """

    # Attributes considered for baking (override in subclass to extend).
    # Extends the shared per-axis constant with compound names so that
    # compound plugs like ".translate" are also recognised.
    TRANSFORM_ATTRS: Set[str] = set(STANDARD_TRANSFORM_ATTRS) | {
        "translate",
        "rotate",
        "scale",
    }

    #: Matrix inputs that displace a transform WITHOUT touching its scalar
    #: t/r/s plugs. A rig that places joints through ``offsetParentMatrix``
    #: (a multMatrix network -- standard since Maya 2020) leaves every t/r/s
    #: plug unconnected, so a TRANSFORM_ATTRS-only scan sees nothing and the
    #: object reports ``requires_bake=False`` while moving tens of units.
    MATRIX_ATTRS: Set[str] = {"offsetParentMatrix"}

    #: Scalar channels a matrix drive resolves onto once baked.
    MATRIX_BAKE_CHANNELS: List[str] = [
        "tx",
        "ty",
        "tz",
        "rx",
        "ry",
        "rz",
        "sx",
        "sy",
        "sz",
    ]

    #: Shear past which a folded local is reported as unbakeable. The
    #: exporter's ``check_sheared_local_transforms`` / ``flatten_sheared_chains``
    #: pair uses 0.05 as its cosine tolerance; this is the same order, on the
    #: shear factors a bake is about to discard.
    SHEAR_TOLERANCE: float = 1e-4

    #: Neutralises a baked-away ``offsetParentMatrix``.
    IDENTITY_MATRIX: List[float] = [
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
    ]

    # Intermediate node types to trace through when finding drivers
    # These are utility nodes that pass values through without being true "drivers"
    PASSTHROUGH_TYPES: Set[str] = set(Attributes.PASSTHROUGH_TYPES)

    def __init__(
        self,
        objects: Optional[List[str]] = None,
        sample_by: int = 1,
        preserve_outside_keys: bool = True,
        delete_inputs: bool = False,
        optimize_keys: Union[bool, str, None] = False,
        bake_blend_shapes: bool = True,
        bake_inherited_visibility: bool = False,
        use_override_layer: bool = True,
        mute_drivers: bool = False,
        backup_file: Union[bool, str, None] = None,
        restorable: bool = True,
    ):
        """Initialize SmartBake with configuration.

        Parameters:
            objects: Objects to analyze/bake. If None, uses all DAG transforms.
            sample_by: Keyframe sample interval (1 = every frame).
            preserve_outside_keys: Keep existing keys outside bake range.
            delete_inputs: Delete constraint/expression nodes after baking.
                Destructive — the restore manifest cannot rebuild deleted
                drivers, so the session is marked non-restorable and a scene
                backup is saved by default (see backup_file).
                Ignored when use_override_layer=True (use mute_drivers instead).
            optimize_keys: Optimization level for the baked output, run
                through ``AnimUtils.optimize_keys()``. A key of
                ``AnimUtils.OPTIMIZE_LEVELS`` (``"static"``, ``"flat"``,
                ``"simplify"``, ``"extremes"``); ``True`` selects the default
                level and anything falsy is OFF. An unknown level raises
                here, before the scene is touched, rather than mid-bake.
                ``"extremes"`` is the one worth knowing about: this bake writes
                a key per frame, which is exactly the input the other levels
                have nothing to delete from.
            bake_blend_shapes: Analyze and bake driven blend shape weights.
                Required for Unity if blend shapes are driven by SDKs/expressions.
            bake_inherited_visibility: Walk ancestor transforms to detect
                inherited ``.visibility`` animation and bake it onto child
                mesh transforms.  API-only — deliberately NOT exposed in the
                Smart Bake panel; see the CAUTION below before enabling it.

                NOT needed for FBX/Unity.  Measured live (Unity 6000.3.10f1):
                Maya's FBX exporter already RESOLVES ancestor visibility onto
                renderable descendants, and Unity binds the result to
                ``m_Enabled@Renderer`` on the child.  A production asset with
                27 keyed ``_LOC`` parents exported 78 visibility curves, every
                one of them on a ``_GEO`` child and none on a ``_LOC``.  A
                statically hidden ancestor resolves at import too.  The only
                gap is a child carrying its OWN ``.visibility`` keys under a
                keyed ancestor: there the exporter writes the child's curve
                alone and drops the ancestor's contribution.

                CAUTION — that gap is also the shape a RenderOpacity fade has.
                ``RenderOpacity.key_fade`` encodes a fade as the GAP between
                two opposite-value ``.visibility`` keys; a key written inside
                it splits one ramp into several.  Measured: an authored 10f
                fade-in + 60f fade-out under an ancestor keyed mid-fade
                reconstructed in Unity as FOUR ramps instead of two — the
                object flickered.  The bake now refuses any object carrying an
                ``opacity`` attribute, no longer keys the bake-range
                boundaries, and refuses a child with its own ``.visibility``
                curve when ``restorable=False`` (nothing could reverse the
                merge).  Ancestor key times can still land inside an
                UNMARKED fade gap, so keep ``opacity`` on faded objects.

                Its one real consumer is the Maya->Blender bridge, which needs
                it for an unrelated reason: Blender's FBX importer drops
                visibility animation entirely, so the values travel in the
                conversion manifest instead.
            use_override_layer: Bake to a new override animation layer instead
                of the base layer (default: True — nondestructive). Original
                constraints/expressions remain connected on base but are
                overridden by the baked layer. Toggle layer mute to compare
                baked vs. live results. FBX export will flatten layers when
                FBXExportBakeComplexAnimation=True. Base-layer mode
                (use_override_layer=False) converts SDK curves in place and
                disconnects drivers — recoverable only via the restore
                manifest (restorable=True) or a backup.
            mute_drivers: Mute (disable) driver nodes after baking instead of
                deleting them. Useful with use_override_layer for better playback
                performance while keeping drivers recoverable. Sets nodeState=2;
                prior states are recorded in the restore manifest.
            backup_file: Save scene backup before any destructive operations.
                - None (default): auto — backup only when delete_inputs=True
                  in base-layer mode (the one non-restorable path).
                - False: never back up.
                - True: Save to scene directory as 'scenename_prebake.ma'.
                - str: Custom file path for backup.
            restorable: Record a restore-manifest session for this bake
                (default: True). The manifest persists on the data_internal
                node; ``SmartBake.restore()`` reverses the bake — deletes the
                override layer, unmutes drivers, re-enables IK handles,
                restores visibility, and rebuilds base-layer driver networks
                from stashed curves. Costs a few small nodes/attrs per bake.
        """
        self.objects = objects
        self.sample_by = sample_by
        self.preserve_outside_keys = preserve_outside_keys
        self.delete_inputs = delete_inputs
        self.optimize_keys = optimize_keys
        # Resolve NOW, not at the call site: an unknown level is a config
        # error and must fail before the first scene mutation, not after N
        # objects have been baked.  Falsy resolves to None, which is what
        # the optimization pass tests to decide whether to run at all.
        from mayatk.anim_utils._anim_utils import AnimUtils

        self._optimize_kwargs = AnimUtils.resolve_optimize_level(optimize_keys)
        self.bake_blend_shapes = bake_blend_shapes
        self.bake_inherited_visibility = bake_inherited_visibility
        self.use_override_layer = use_override_layer
        self.mute_drivers = mute_drivers
        if backup_file is None:
            backup_file = bool(delete_inputs and not use_override_layer)
        self.backup_file = backup_file
        self.restorable = restorable

    # -------------------------------------------------------------------------
    # Connection Tracing
    # -------------------------------------------------------------------------

    def _trace_upstream_driver(
        self, plug: str, visited: Optional[Set[str]] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """Trace upstream through passthrough nodes to find the true driver.

        Delegates to Attributes.trace_upstream() for the actual
        tracing logic.

        Returns:
            Tuple of (driver_node, driver_type) or (None, None) if not found.
        """
        return Attributes.trace_upstream(
            plug, passthrough_types=self.PASSTHROUGH_TYPES, visited=visited
        )

    # -------------------------------------------------------------------------
    # Analysis
    # -------------------------------------------------------------------------

    def _get_objects(self) -> List[str]:
        """Get objects to analyze, defaulting to all transforms and joints.

        ``ls(type="transform")`` already includes joints (joint derives from
        transform); the explicit joint query is kept as a safety net for any
        Maya version where it doesn't, with duplicates removed.
        """
        if self.objects:
            return list(self.objects)
        transforms = cmds.ls(type="transform", long=True) or []
        joints = cmds.ls(type="joint", long=True) or []
        return ptk.remove_duplicates(transforms + joints)

    def analyze(self) -> Dict[str, BakeAnalysis]:
        """Analyze objects to determine what needs baking.

        Returns:
            Dict mapping object names to their BakeAnalysis results.
        """
        results: Dict[str, BakeAnalysis] = {}
        objects = self._get_objects()

        if not objects:
            return results

        for obj, analysis in self._analyze_objects(objects).items():
            if analysis.requires_bake or analysis.already_keyed:
                results[obj] = analysis

        # Detect inherited visibility from ancestor transforms.
        # NOTE: the FBX exporter already resolves ancestor visibility onto
        # renderable descendants on its own (measured — see the CAUTION on
        # bake_inherited_visibility in __init__), so this pass is NOT needed
        # for a Maya->Unity export. It exists for the Maya->Blender bridge,
        # and to cover the one case the exporter drops: a child carrying its
        # OWN .visibility keys under a keyed ancestor.
        if self.bake_inherited_visibility:
            inherited = self._analyze_inherited_visibility(objects, results)
            for obj, analysis in inherited.items():
                if obj in results:
                    # Merge into existing analysis
                    existing = results[obj]
                    for k, v in analysis.driven_channels.items():
                        if k not in existing.driven_channels:
                            existing.driven_channels[k] = v
                    for k, v in analysis.source_nodes.items():
                        if k not in existing.source_nodes:
                            existing.source_nodes[k] = v
                else:
                    results[obj] = analysis

        # Analyze blend shapes separately (they're on deformers, not transforms)
        if self.bake_blend_shapes:
            blendshape_results = self._analyze_blend_shapes(objects)
            for bs, analysis in blendshape_results.items():
                if analysis.requires_bake:
                    results[bs] = analysis

        return results

    def _analyze_blend_shapes(self, objects: List[str]) -> Dict[str, BakeAnalysis]:
        """Analyze blend shape deformers for driven weights.

        Unity can import blend shapes (morph targets) but needs the weights
        baked if driven by expressions or SDKs.

        Returns:
            Dict mapping blendShape node names to their BakeAnalysis.
        """
        results: Dict[str, BakeAnalysis] = {}

        # Find blend shapes connected to our objects: two batched queries,
        # not a listRelatives + listConnections per object.
        blend_shapes = self._blend_shapes_of(objects)

        for bs in blend_shapes:
            analysis = BakeAnalysis(object=bs)

            # Get weight aliases (target names)
            aliases = cmds.aliasAttr(bs, query=True) or []
            weight_attrs = [aliases[i] for i in range(0, len(aliases), 2)]

            for weight_attr in weight_attrs:
                plug = f"{bs}.{weight_attr}"
                driver_node, driver_type = self._trace_upstream_driver(plug)

                if driver_type and driver_type != "keyframe":
                    if driver_type not in analysis.driven_channels:
                        analysis.driven_channels[driver_type] = []
                    analysis.driven_channels[driver_type].append(weight_attr)

                    if driver_type not in analysis.source_nodes:
                        analysis.source_nodes[driver_type] = []
                    if driver_node not in analysis.source_nodes[driver_type]:
                        analysis.source_nodes[driver_type].append(driver_node)
                elif driver_type == "keyframe":
                    analysis.already_keyed.append(weight_attr)

            if analysis.requires_bake:
                results[bs] = analysis

        return results

    def _analyze_inherited_visibility(
        self,
        objects: List[str],
        existing_results: Dict[str, BakeAnalysis],
    ) -> Dict[str, BakeAnalysis]:
        """Detect visibility animation on ancestor transforms.

        For each export object, walk up the DAG hierarchy. If any
        ancestor's ``.visibility`` plug has incoming animation (animCurve,
        expression, constraint, driven key, etc.) the effective visibility
        of the export object depends on something outside itself.

        Maya evaluates inherited visibility at runtime. The FBX exporter
        resolves it onto renderable descendants by itself EXCEPT when the
        child carries its own ``.visibility`` keys — then the child's curve
        is written alone and the ancestor's contribution is lost. Flagging
        such objects here lets ``bake()`` sample the effective (ancestor x
        self) visibility and key it directly on the mesh transform.

        The analysis stores **all** ancestor ``.visibility`` plugs on
        ``source_nodes["inherited_visibility_plugs"]`` — including
        statically-set parents — so the bake phase can reuse them
        without re-walking the hierarchy.

        Flagging is not a promise to bake: ``_bake_inherited_visibility``
        refuses objects carrying an ``opacity`` attribute, and (in a
        non-restorable session) objects with their own ``.visibility``
        curve.  Those land in ``BakeResult.skipped``.

        Parameters:
            objects: The list of export objects (typically mesh transforms).
            existing_results: Already-analysed results from ``_analyze_object``.

        Returns:
            Dict of *new* ``BakeAnalysis`` entries for objects that need
            inherited-visibility baking. Does not include objects whose
            own ``.visibility`` is already keyed or driven (handled by
            the normal analysis path).
        """
        results: Dict[str, BakeAnalysis] = {}
        driver_cache: Dict[str, List[str]] = {}

        def ancestor_drivers(parent: str) -> List[str]:
            """Nodes feeding *parent*.visibility: animCurves first, else any
            driver (an expression, say). Ancestors are shared across a subtree,
            so each is asked once per analysis."""
            if parent not in driver_cache:
                plug = f"{parent}.visibility"
                found = (
                    cmds.listConnections(
                        plug, source=True, destination=False, type="animCurve"
                    )
                    or []
                )
                if not found:
                    found = (
                        cmds.listConnections(plug, source=True, destination=False) or []
                    )
                driver_cache[parent] = found
            return driver_cache[parent]

        for obj in objects:
            # Skip only if visibility is already driven by a non-keyframe
            # source (constraint, expression, etc.) — those are handled
            # by the normal bake path.  Do NOT skip objects whose own
            # .visibility is merely keyed: their keys may represent only
            # the object's *own* show/hide state and not account for an
            # ancestor being hidden.  We need to multiply ancestor
            # visibility into the bake.
            if obj in existing_results:
                existing = existing_results[obj]
                vis_driven = any(
                    "v" in ch_list for ch_list in existing.driven_channels.values()
                )
                if vis_driven:
                    continue

            # Walk up the DAG hierarchy collecting ALL ancestor vis plugs
            # and any driver nodes. The ancestors are read off the long path
            # (immediate parent first), no listRelatives per level.
            ancestor_curves: List[str] = []
            ancestor_plugs: List[str] = []
            parts = (cmds.ls(obj, long=True) or [obj])[0].split("|")[1:]
            for depth in range(len(parts) - 1, 0, -1):
                parent = "|" + "|".join(parts[:depth])
                # Always track the plug — even statically-set parents
                # affect inherited visibility.
                ancestor_plugs.append(f"{parent}.visibility")
                ancestor_curves.extend(ancestor_drivers(parent))

            if ancestor_curves:
                analysis = BakeAnalysis(object=obj)
                analysis.driven_channels["inherited_visibility"] = ["v"]
                analysis.source_nodes["inherited_visibility"] = ancestor_curves
                # Store all ancestor plugs for the bake phase to reuse.
                analysis.source_nodes["inherited_visibility_plugs"] = ancestor_plugs
                results[obj] = analysis

        return results

    #: Driver types the taxonomy names. Anything else is a raw ``nodeType``
    #: string returned by ``trace_upstream`` as a last-resort fallback.
    SEMANTIC_DRIVER_TYPES: Set[str] = {
        "constraint",
        "expression",
        "driven_key",
        "keyframe",
        "ik",
        "motion_path",
    }

    def _plug_has_upstream_animation(self, plug: str, depth: int = 6) -> bool:
        """Return True if any animCurve feeds *plug*, however indirectly.

        Plug-precise by necessity: a node-level walk conflates every attribute
        on a shared node, so a rig's ``settings_CTRL`` -- keyless display
        switches sitting beside a keyed transform -- would report
        ``controlsVis`` as animated because some *other* attribute has a curve.
        """
        return bool(
            Attributes.upstream_anim_curves(plug, plug_precise=True, depth=depth)
        )

    def _analyze_object(self, obj: str, check_ik: bool = True) -> BakeAnalysis:
        """Analyze a single object for bake requirements.

        The one-object form of :meth:`_analyze_objects`; ``analyze()`` runs
        the batched form over the whole set.
        """
        return self._analyze_objects([obj], check_ik=check_ik).get(
            obj, BakeAnalysis(object=obj)
        )

    def _analyze_objects(
        self, objects: List[str], check_ik: Optional[bool] = None
    ) -> Dict[str, BakeAnalysis]:
        """One :class:`BakeAnalysis` per object, from a handful of batched queries.

        The per-object form asked Maya ~9 questions per object -- its incoming
        wires, then per plug a trace, four type tests and a mute check, plus a
        full IK-chain scan for every object -- 33k calls and 2.4 s on a
        4189-transform scene, on every export and every panel Bake. Here the
        wires come from ONE ``listConnections`` over the set, node types from
        one ``ls -showType``, set-driven-key inputs from one more
        ``listConnections``, and IK membership from one walk per handle
        (:meth:`RigUtils.ik_handles_by_joint`); only passthrough networks are
        still traced one at a time, and each driver node is classified once
        however many plugs it feeds.

        Parameters:
            objects: Transforms/joints to analyze, in the caller's spelling
                (the keys of the result).
            check_ik: Scan IK-chain membership. None (default) scans only
                when the scene has an ikHandle.

        Returns:
            ``{object: BakeAnalysis}`` for every object given, driven or not.
        """
        from mayatk.node_utils._node_utils import NodeUtils
        from mayatk.rig_utils._rig_utils import RigUtils

        results: Dict[str, BakeAnalysis] = {
            obj: BakeAnalysis(object=obj) for obj in objects
        }
        if not objects:
            return results
        long_of = self._long_names(objects)
        obj_of_long = {long: obj for obj, long in long_of.items()}

        # An ikEffector is IK plumbing, never an animation target. Maya wires
        # ``effector.translate`` straight from the chain's last joint, which
        # the trace below would otherwise report as a joint-driven channel and
        # queue for bake -- keying an effector accomplishes nothing and writes
        # onto a node no exporter reads.
        effectors = set(cmds.ls(objects, type="ikEffector", long=True) or [])
        targets = [obj for obj in objects if long_of[obj] not in effectors]
        if not targets:
            return results

        # IK chain membership (joints in IK chains need rotation baking).
        if check_ik is None:
            check_ik = bool(cmds.ls(type="ikHandle"))
        if check_ik:
            joints = set(cmds.ls(targets, type="joint", long=True) or [])
            if joints:
                by_joint = RigUtils.ik_handles_by_joint()
                for obj in targets:
                    handles = (
                        by_joint.get(long_of[obj]) if long_of[obj] in joints else None
                    )
                    if handles:
                        results[obj].driven_channels["ik"] = ["rx", "ry", "rz"]
                        results[obj].source_nodes["ik"] = list(handles)

        # Every incoming wire of the whole set, then per object.
        pairs = NodeUtils.incoming_connections(targets)
        if not pairs:
            return results
        dest_long = self._long_names(
            list(dict.fromkeys(dest.split(".")[0] for dest, _ in pairs))
        )
        by_object: Dict[str, List[Tuple[str, str]]] = {}
        for dest_plug, src_plug in pairs:
            obj = obj_of_long.get(dest_long.get(dest_plug.split(".")[0]))
            if obj is not None:
                by_object.setdefault(obj, []).append((dest_plug, src_plug))

        # Classify each driver node once, from batched facts.
        src_nodes = list(dict.fromkeys(src.split(".")[0] for _, src in pairs))
        node_types = self._node_types(src_nodes)
        driven_curves = self._curves_with_input(
            [n for n, t in node_types.items() if t.startswith("animCurve")]
        )
        constraints = set(cmds.ls(src_nodes, type="constraint") or [])
        classified: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
        muted: Dict[str, bool] = {}
        short_names: Dict[str, str] = {}

        def classify(node: str) -> Tuple[Optional[str], Optional[str]]:
            # The one taxonomy (Attributes.classify_driver), handed the two
            # facts the batches above already answered.
            if node not in classified:
                node_type = node_types.get(node)
                classified[node] = Attributes.classify_driver(
                    node,
                    node_type=node_type,
                    passthrough_types=self.PASSTHROUGH_TYPES,
                    is_constraint=node in constraints,
                    is_driven=(node in driven_curves)
                    if node_type and node_type.startswith("animCurve")
                    else None,
                )
            return classified[node]

        for obj, obj_pairs in by_object.items():
            analysis = results[obj]
            for dest_plug, src_plug in obj_pairs:
                attr_long = dest_plug.split(".")[-1]
                # Handle compound attrs like .translate -> .translateX, ...
                base_attr = attr_long.split("[")[0]  # Handle indexed attrs

                # A matrix input displaces the object without ever touching a
                # scalar t/r/s plug, so it needs its own detection pass and its
                # own bake (see _bake_matrix_drivers).
                if base_attr in self.MATRIX_ATTRS:
                    driver_node = src_plug.split(".")[0]
                    channels = analysis.driven_channels.setdefault("matrix", [])
                    for channel in self.MATRIX_BAKE_CHANNELS:
                        if channel not in channels:
                            channels.append(channel)
                    sources = analysis.source_nodes.setdefault("matrix", [])
                    if driver_node not in sources:
                        sources.append(driver_node)
                    continue

                if base_attr not in self.TRANSFORM_ATTRS:
                    continue

                driver_node, driver_type = classify(src_plug.split(".")[0])
                if not driver_node or not driver_type:
                    continue

                # Skip muted nodes
                if driver_type in ("constraint", "expression"):
                    if driver_node not in muted:
                        muted[driver_node] = bool(NodeUtils.is_muted(driver_node))
                    if muted[driver_node]:
                        continue

                if attr_long not in short_names:
                    short_names[attr_long] = Attributes.attr_short_name(attr_long)
                attr_short = short_names[attr_long]

                # A .visibility wired straight off another node's attribute -- a
                # rig's ``settings_CTRL.controlsVis`` display switch -- is a plain
                # scalar copy with no parent contribution. When nothing upstream
                # carries a key it is a CONSTANT, and baking it writes a flat value
                # across the whole range onto controls that never export.
                #
                # Deliberately narrow: it does NOT generalise to constraints. A
                # constraint whose targets own no curves can still move, because
                # the targets are driven by animated PARENTS -- measured on a
                # production scene, 217 constraint drivers reported no animation
                # while the rig they drive travelled tens of units. "Driver owns no
                # animCurve" is not a proxy for "produces no motion" anywhere but
                # this direct-connect case.
                if (
                    attr_short == "v"
                    and driver_type not in self.SEMANTIC_DRIVER_TYPES
                    and not self._plug_has_upstream_animation(dest_plug)
                ):
                    continue

                if driver_type == "keyframe":
                    # Already has time-based keyframes
                    if attr_short not in analysis.already_keyed:
                        analysis.already_keyed.append(attr_short)
                else:
                    # Needs baking - constraint, driven key, expression, or IK
                    if driver_type not in analysis.driven_channels:
                        analysis.driven_channels[driver_type] = []
                    if attr_short not in analysis.driven_channels[driver_type]:
                        analysis.driven_channels[driver_type].append(attr_short)

                    if driver_type not in analysis.source_nodes:
                        analysis.source_nodes[driver_type] = []
                    if driver_node not in analysis.source_nodes[driver_type]:
                        analysis.source_nodes[driver_type].append(driver_node)

        return results

    # -------------------------------------------------------------------------
    # Time Range Detection
    # -------------------------------------------------------------------------

    def get_time_range(
        self, analysis: Optional[Dict[str, BakeAnalysis]] = None
    ) -> Tuple[int, int]:
        """Determine optimal bake time range from driver animation.

        Traces constraint targets and driven key drivers to find their
        animation range. Falls back to playback range if no animation found.

        Parameters:
            analysis: Pre-computed analysis dict. If None, runs analyze().

        Returns:
            Tuple of (start_frame, end_frame) as integers.
        """
        if analysis is None:
            analysis = self.analyze()

        all_times: List[float] = []

        for obj, data in analysis.items():
            for source_type, nodes in data.source_nodes.items():
                for node in nodes:
                    times = self._get_driver_time_range(node, source_type)
                    all_times.extend(times)

        if all_times:
            # floor/ceil — int() truncates toward zero and would drop
            # fractional driver keys at the range boundaries.
            return math.floor(min(all_times)), math.ceil(max(all_times))

        # Fallback to playback range
        start = cmds.playbackOptions(query=True, minTime=True)
        end = cmds.playbackOptions(query=True, maxTime=True)
        return math.floor(start), math.ceil(end)

    def _get_driver_time_range(self, node: str, source_type: str) -> List[float]:
        """Get keyframe times from a driver node's animation curves.

        Delegates to AnimUtils.get_driver_animation_range() for the actual logic.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        return AnimUtils.get_driver_animation_range(node, driver_type=source_type)

    def get_object_time_ranges(
        self,
        analysis: Dict[str, BakeAnalysis],
        fallback: Tuple[int, int],
    ) -> Dict[str, Tuple[int, int]]:
        """The frames each driven object in *analysis* actually needs sampled.

        ``bakeResults`` cost tracks FRAMES (measured: halving the range halves
        it, halving the channels saves 17%, regrouping saves nothing), and one
        global range bakes every object over the union of every driver's keys.
        :class:`_TimeDependency` walks upstream from each object's drivers --
        through constraints to their targets, through matrix, math and
        measurement networks, through a deformed curve to the joints that
        shape it -- and this turns the answer into frames:

        - **known** -- every wire reaches time-driven animCurves with
          constant infinity: the union of their key extents, plus the
          object's animated ancestors (a constraint keeps a child pinned in
          world space, so a moving parent changes the child's locals).
        - **static** -- nothing time-dependent anywhere: a constraint to an
          unkeyed target under unkeyed parents, a driven key off an unkeyed
          attribute. Sampled at ONE frame; the layer holds it everywhere.
        - **unknown** -- anything the walk cannot prove: an expression that
          reads ``time``, a node type outside
          :attr:`_TimeDependency.DERIVED_TYPES`, a cycling or linear
          infinity, a constraint with no targets. These get *fallback*, the
          global range -- exactly what every object got before this existed,
          so a wrong guess can only cost frames, never motion.

        Parameters:
            analysis: The analysis to resolve, as ``analyze()`` returns it.
            fallback: The global range (``get_time_range``); unknown objects
                bake over it and a static object is keyed at its start.

        Returns:
            ``{object: (start, end)}`` for every object that requires a bake.
        """
        resolver = _TimeDependency()
        ranges: Dict[str, Tuple[int, int]] = {}
        for obj, data in analysis.items():
            if not data.requires_bake:
                continue
            state, times = resolver.object_state(obj, data)
            if state == "unknown":
                ranges[obj] = fallback
            elif state == "static":
                ranges[obj] = (fallback[0], fallback[0])
            else:
                ranges[obj] = (math.floor(min(times)), math.ceil(max(times)))
        return ranges

    # -------------------------------------------------------------------------
    # Baking
    # -------------------------------------------------------------------------

    def _save_backup(self) -> Optional[str]:
        """Save a backup of the current scene before baking.

        Delegates to EnvUtils.save_scene_backup() for the actual operation.

        Returns:
            Path to the saved backup file, or None if backup was skipped/failed.
        """
        if not self.backup_file:
            return None

        from mayatk.env_utils._env_utils import EnvUtils

        # Determine suffix based on backup type
        if isinstance(self.backup_file, str):
            return EnvUtils.save_scene_backup(backup_path=self.backup_file)
        else:
            return EnvUtils.save_scene_backup(
                backup_path=True,
                suffix="_prebake",
            )

    def _bake_inherited_visibility(
        self,
        objects: Dict[str, "BakeAnalysis"],
        start: int,
        end: int,
        result: "BakeResult",
        session: Optional[dict] = None,
    ) -> Set[str]:
        """Sample effective ancestor visibility and key it on each object.

        Keys are written directly on the **base layer** (no animation
        layer) because FBX ``BakeComplexAnimation`` does not evaluate
        visibility through animation-layer blend nodes — it only reads
        direct animCurve connections.  The caller is responsible for
        deleting the curves listed in ``result.visibility_curves`` after
        export to restore the scene — that dict therefore lists only
        curves this bake CREATED (see below).

        The effective visibility is the product of **all** ancestor
        ``.visibility`` values (including statically-set parents) **and**
        the child's own ``.visibility`` at each frame.  This ensures
        that:

        - A child under a statically-hidden parent is never made visible.
        - A child with its own independent show/hide keys retains them
          (merged with ancestor state) rather than being overwritten.

        Ancestor plugs are reused from the analysis phase stored on
        ``data.source_nodes["inherited_visibility_plugs"]`` to avoid
        re-walking the hierarchy.

        Uses stepped tangents since visibility is boolean.

        Two refusals protect authored data (BACKLOG 2026-08-02):

        - **``opacity`` attribute** — ``RenderOpacity`` encodes an opacity
          fade as the GAP between two opposite-value ``.visibility`` keys.
          Any key written inside that gap splits one ramp into several
          (measured in Unity: two authored ramps reconstructed as four).
          Objects carrying the attribute are warned about and skipped.
        - **Own visibility curve in a non-restorable session** — keying
          merges into the child's ORIGINAL curve.  Without a session there
          is no pristine stash to reverse it, and the curve would be listed
          for deletion, destroying the artist's keys.  Such objects are
          skipped; run with ``restorable=True`` to bake them reversibly.

        Sampling is limited to the child's own key times and the ancestor
        key times.  The bake-range boundaries are deliberately NOT keyed:
        step tangents already hold the first/last sampled value outward, so
        a boundary key only invents a transition — inside a fade gap, a
        wrong one.  An ancestor driven by something other than an animCurve
        (an expression, say) therefore contributes no sample times; such an
        object is skipped rather than keyed from two boundary samples that
        never described its motion.

        Parameters:
            objects: ``{obj: BakeAnalysis}`` for objects needing bake.
            start: First frame of the bake range.
            end: Last frame of the bake range (inclusive).
            result: Live ``BakeResult`` to update with baked/skipped info.
            session: Restorable-session manifest to record visibility
                stashes into, or ``None`` to skip stash bookkeeping (the
                caller passes None for non-restorable sessions — restore()
                refuses those, so their stash nodes could never be
                reclaimed).

        Returns:
            The objects whose baked keys were MERGED into a pre-existing
            (artist-authored) visibility curve — never listed in
            ``result.visibility_curves`` and never optimized.
        """
        merged: Set[str] = set()

        for obj, data in objects.items():
            # Reuse plugs from analysis; fall back to source_nodes curves.
            ancestor_plugs: List[str] = data.source_nodes.get(
                "inherited_visibility_plugs", []
            )
            ancestor_curves: List[str] = data.source_nodes.get(
                "inherited_visibility", []
            )

            if not ancestor_plugs:
                result.skipped.append(obj)
                continue

            try:
                if cmds.attributeQuery("opacity", node=obj, exists=True):
                    result.skipped.append(obj)
                    cmds.warning(
                        f"SmartBake: {obj} carries an 'opacity' attribute - "
                        f"its .visibility keys encode a RenderOpacity fade as "
                        f"the gap between them. Refusing to bake inherited "
                        f"visibility onto it (any inserted key splits the fade)."
                    )
                    continue

                child_vis_curves = (
                    cmds.listConnections(
                        f"{obj}.visibility",
                        source=True,
                        destination=False,
                        type="animCurve",
                    )
                    or []
                )

                if child_vis_curves and session is None:
                    result.skipped.append(obj)
                    cmds.warning(
                        f"SmartBake: {obj} has its own .visibility animation and "
                        f"this session is not restorable - baking would merge "
                        f"into the artist's curve irreversibly. Skipped; use "
                        f"restorable=True to bake it."
                    )
                    continue

                # Snapshot original visibility for cleanup restoration.
                original_vis = cmds.getAttr(f"{obj}.visibility")
                result.visibility_originals[obj] = float(original_vis)

                # Sample ONLY at the ancestor key times and the child's own
                # key times — never at the bake-range boundaries.
                sample_times: Set[float] = set()
                for curve in ancestor_curves:
                    if cmds.objExists(curve):
                        times = cmds.keyframe(curve, query=True, timeChange=True) or []
                        for t in times:
                            if start <= t <= end:
                                sample_times.add(t)

                # Include the child's own vis key times.
                for cvc in child_vis_curves:
                    times = cmds.keyframe(cvc, query=True, timeChange=True) or []
                    for t in times:
                        if start <= t <= end:
                            sample_times.add(t)

                if not sample_times:
                    result.visibility_originals.pop(obj, None)
                    result.skipped.append(obj)
                    cmds.warning(
                        f"SmartBake: nothing to sample for {obj} - no ancestor "
                        f"visibility keys fall inside {start}-{end} (an ancestor "
                        f"driven by something other than an animCurve "
                        f"contributes no key times)."
                    )
                    continue

                # Keying below mutates the child's OWN vis curve in place —
                # stash a pristine duplicate first so restore can bring the
                # original animation back instead of deleting it with the
                # baked keys.
                if session is not None:
                    from mayatk.anim_utils.smart_bake import bake_session

                    vis_stash = (
                        bake_session.BakeSessionStore.stash_curve(child_vis_curves[0])
                        if child_vis_curves
                        else None
                    )
                    session["visibility"].append(
                        {
                            "object": bake_session.BakeSessionStore.node_ref(obj),
                            "had_curve": bool(child_vis_curves),
                            "stash": vis_stash,
                            "original_value": float(original_vis),
                        }
                    )

                sorted_times = sorted(sample_times)

                # Snapshot the child's own visibility at ALL sample
                # times BEFORE writing any keys.  Once we start keying
                # the curve, later getAttr reads would return values
                # from the modified curve rather than the original.
                child_vis_snapshot = {
                    frame: float(cmds.getAttr(f"{obj}.visibility", time=frame))
                    for frame in sorted_times
                }

                for frame in sorted_times:
                    # Start with the child's original visibility.
                    effective = child_vis_snapshot[frame]
                    if effective == 0:
                        pass  # Already 0, skip ancestor evaluation.
                    else:
                        for plug in ancestor_plugs:
                            val = cmds.getAttr(plug, time=frame)
                            if val == 0:
                                effective = 0.0
                                break
                            effective *= val

                    cmds.setKeyframe(
                        obj,
                        attribute="visibility",
                        time=frame,
                        value=effective,
                        shape=False,
                    )

                # Track the created base-layer curve for cleanup.  A curve
                # that pre-existed this bake is the ARTIST's — the baked keys
                # merged into it, so it must NOT be advertised under the
                # delete-after-export contract; ``restore()`` reverses that
                # merge from the pristine stash instead.
                vis_curve = cmds.listConnections(
                    f"{obj}.visibility",
                    source=True,
                    destination=False,
                    type="animCurve",
                )
                if vis_curve:
                    if child_vis_curves:
                        merged.add(obj)
                    else:
                        result.visibility_curves[obj] = vis_curve[0]
                    cmds.keyTangent(vis_curve[0], outTangentType="step")
                    result.baked[obj] = ["v"]
                else:
                    cmds.warning(
                        f"SmartBake: No animCurve found on "
                        f"{obj}.visibility after keying — "
                        f"curve may have been renamed."
                    )
                    result.skipped.append(obj)
            except Exception as e:
                result.skipped.append(obj)
                cmds.warning(
                    f"SmartBake: Failed to bake inherited visibility for {obj}: {e}"
                )

        return merged

    def _writable_matrix_channels(self, obj: str) -> List[str]:
        """Return the t/r/s channels of *obj* the matrix bake will key.

        Only LOCKED channels are excluded. Channels driven by a live
        network are included on purpose: the folded local
        (``TRS x offsetParentMatrix``) is only consistent when EVERY
        channel lands, so the bake severs those inputs (recorded;
        restore reconnects). The previous design left network-driven
        channels to their drivers -- the production _01 wire looms'
        curveInfo-driven scale then dropped the OPM's own scale content
        entirely, and the folded-R/T-beside-unfolded-S hybrid drifted
        worlds up to 3.1 cm at the chain tip (probe-pinned).
        """
        writable: List[str] = []
        for channel in self.MATRIX_BAKE_CHANNELS:
            plug = f"{obj}.{channel}"
            try:
                if cmds.getAttr(plug, lock=True):
                    continue
            except (RuntimeError, ValueError):
                continue
            writable.append(channel)
        return writable

    def _bake_matrix_drivers(
        self,
        objects: Dict[str, BakeAnalysis],
        start: int,
        end: int,
        result: BakeResult,
        session: Optional[dict] = None,
        object_ranges: Optional[Dict[str, Tuple[int, int]]] = None,
    ) -> None:
        """Bake ``offsetParentMatrix``-driven objects onto their t/r/s channels.

        ``bakeResults`` cannot do this. It samples the scalar t/r/s plugs, and
        a matrix-driven object's local TRS is identity, so it writes zeros --
        the animation reads correct only while the matrix network is still
        connected, and is lost the instant it isn't.

        Samples the EFFECTIVE local matrix (``localTRS * offsetParentMatrix``)
        at every frame first, then disconnects the network, resets the plug to
        identity, and writes the sampled transforms as keys. Verified against a
        matrix-driven joint to reproduce the driven motion exactly.

        Both passes go through om2: the sample pass reads the two matrix
        plugs directly (16x ``getAttr`` + ``xform -q``), and the write pass
        decomposes and keys in bulk (:meth:`_write_matrix_keys`; 11-14x
        ``xform`` + ``setKeyframe``), falling back to that cmds pair per
        object when the closed-form split cannot be trusted. The cmds pair
        was 69 s of a 118 s production-scale bake. Keys written through
        ``MFnAnimCurve`` are not on the undo queue -- reverse a bake with
        ``SmartBake.restore()``, which the session manifest records for.

        Runs in BOTH layer and base modes. A layer cannot hold it (no matrix
        blend node exists), and leaving the network live for the FBX exporter
        ships FROZEN motion whenever the matrix upstream does not translate
        to FBX -- BakeComplexAnimation then samples the plug once at the
        export-time frame (``TestFbxMatrixOpmExport`` pins this).

        Parameters:
            objects: ``{object: BakeAnalysis}`` carrying a "matrix" drive.
            start: First frame to sample.
            end: Last frame to sample.
            result: Mutated in place -- baked/skipped are recorded here.
            session: Restore manifest to append to, or None when the bake is
                not restorable.
            object_ranges: Per-object ``(start, end)`` to key over, within
                *start*..*end*; anything absent gets the whole span. The
                timeline is still walked ONCE -- every ``currentTime`` is a
                full DG evaluation, so splitting the set into one pass per
                range measured 3.8x the evaluations on a production scene to
                save a third of the plug reads. Only the reads and the keys
                are narrowed.
        """
        import maya.api.OpenMaya as om2

        from mayatk.anim_utils.smart_bake.bake_session import BakeSessionStore

        step = max(1, int(self.sample_by))
        frames = list(range(int(start), int(end) + 1, step))
        if frames and frames[-1] != int(end):
            frames.append(int(end))
        ranges = object_ranges or {}

        def window(obj: str) -> Tuple[int, int]:
            low, high = ranges.get(obj, (start, end))
            low, high = max(int(start), int(low)), min(int(end), int(high))
            # A range clamped to nothing would key nothing, and the drive is
            # disconnected either way -- that is motion silently lost, so a
            # window that does not overlap falls back to the whole span.
            return (low, high) if low <= high else (int(start), int(end))

        # Resolve the bakeable set up front:
        # (object, matrix plug, source plug, writable channels).
        targets: List[Tuple[str, str, str, List[str]]] = []
        for obj in objects:
            if not cmds.objExists(obj):
                result.skipped.append(obj)
                continue
            plug = f"{obj}.offsetParentMatrix"
            sources = (
                cmds.listConnections(plug, source=True, destination=False, plugs=True)
                or []
            )
            if not sources:  # disconnected between analyze() and bake()
                result.skipped.append(obj)
                continue
            channels = self._writable_matrix_channels(obj)
            if not channels:
                result.skipped.append(obj)
                continue
            targets.append((obj, plug, sources[0], channels))

        if not targets:
            return

        # Snapshot what the t/r/s channels held BEFORE the bake overwrites
        # them, so restore can put it back: an existing curve is stashed, a
        # static value recorded verbatim, and any non-curve driver captured as
        # a connection (writing keys below would sever it).
        pending: Dict[str, dict] = {}
        if session is not None:
            for obj, _, source_plug, channels in targets:
                originals: Dict[str, float] = {}
                stashes: List[dict] = []
                connections: List[List[dict]] = []
                for channel in channels:
                    channel_plug = f"{obj}.{channel}"
                    curves = (
                        cmds.listConnections(
                            channel_plug,
                            type="animCurve",
                            source=True,
                            destination=False,
                        )
                        or []
                    )
                    if curves:
                        stashes.append(BakeSessionStore.stash_curve(curves[0]))
                    else:
                        originals[channel] = cmds.getAttr(channel_plug)
                    connections.extend(
                        BakeSessionStore.snapshot_connections(channel_plug)
                    )
                pending[obj] = {
                    "object": BakeSessionStore.node_ref(obj),
                    "source": BakeSessionStore.plug_ref(source_plug),
                    "channels": list(channels),
                    "originals": originals,
                    "stashes": stashes,
                    "_connections": connections,
                }

        restore_time = cmds.currentTime(query=True)

        # Sample with the TIMELINE outermost: one scene evaluation per frame
        # for the whole set, not one per object per frame. Every currentTime
        # forces a full DG evaluation, so the object-outer form cost
        # objects x frames of them -- on the production rig that found this bug
        # (182 matrix-driven joints over 1134 frames) roughly 206,000
        # evaluations instead of 1,134.
        readers: Dict[str, Tuple["om2.MPlug", "om2.MPlug"]] = {}
        selection = om2.MSelectionList()
        for obj, _, _, _ in targets:
            selection.clear()
            selection.add(obj)
            dep = om2.MFnDependencyNode(selection.getDependNode(0))
            readers[obj] = (
                dep.findPlug("offsetParentMatrix", False),
                dep.findPlug("matrix", False),
            )
        sampled: Dict[str, Dict[int, om2.MMatrix]] = {
            obj: {} for obj, _, _, _ in targets
        }
        windows = {obj: window(obj) for obj, _, _, _ in targets}
        object_frames = {
            obj: [f for f in frames if low <= f <= high]
            for obj, (low, high) in windows.items()
        }
        for frame in frames:
            cmds.currentTime(frame)
            for obj, _, _, _ in targets:
                low, high = windows[obj]
                if not low <= frame <= high:
                    continue  # outside this object's own range
                opm_plug, matrix_plug = readers[obj]
                offset = om2.MFnMatrixData(opm_plug.asMObject()).matrix()
                local = om2.MFnMatrixData(matrix_plug.asMObject()).matrix()
                sampled[obj][frame] = local * offset

        # A folded local that SHEARS has no translate/rotate/scale form: the
        # write below sets the shear, nothing keys it, and it is zeroed at the
        # end -- real transform content dropped, compounding down a chain
        # (measured on a production wire loom: 0.32 per link, 7.8 cm at the
        # tip of 22 joints, translate and rotate exact). The authored local of
        # such a node is clean TRS, so only the FOLD says so. Sampled at a few
        # frames per object rather than all of them: a chain that shears does
        # so throughout, and a decomposition per object per frame would cost
        # more than the pass it warns about.
        sheared: List[str] = []
        for obj, _, _, _ in targets:
            probe_frames = object_frames[obj]
            if not probe_frames:
                continue
            step = max(1, len(probe_frames) // 4)
            for frame in probe_frames[::step][:5]:
                shear = om2.MTransformationMatrix(sampled[obj][frame]).shear(
                    om2.MSpace.kTransform
                )
                if max(abs(v) for v in shear) > self.SHEAR_TOLERANCE:
                    sheared.append(obj)
                    break

        # Neutralise every drive before writing any keys -- a half-disconnected
        # set would sample-and-write against a moving target.
        surviving: List[Tuple[str, str, str, List[str]]] = []
        for obj, plug, source_plug, channels in targets:
            try:
                cmds.disconnectAttr(source_plug, plug)
                cmds.setAttr(plug, self.IDENTITY_MATRIX, type="matrix")
                # The folded local is the COMPLETE transform: any channel a
                # live network keeps driving would stay unfolded beside it.
                # Sever every non-animCurve input on the bake channels (the
                # pairs are already in the session via snapshot_connections;
                # restore reconnects them). animCurve inputs stay -- the
                # stash mechanism owns those.
                cut_pairs = set()
                for channel in channels:
                    child = f"{obj}.{channel}"
                    probe_plugs = [child]
                    try:
                        parents = (
                            cmds.attributeQuery(channel, node=obj, listParent=True)
                            or []
                        )
                    except RuntimeError:
                        parents = []
                    if parents:
                        probe_plugs.append(f"{obj}.{parents[0]}")
                    for probe in probe_plugs:
                        conns = (
                            cmds.listConnections(
                                probe,
                                source=True,
                                destination=False,
                                plugs=True,
                                connections=True,
                            )
                            or []
                        )
                        for k in range(0, len(conns), 2):
                            dst, src = conns[k], conns[k + 1]
                            if (dst, src) in cut_pairs:
                                continue
                            if cmds.nodeType(src.partition(".")[0]).startswith(
                                "animCurve"
                            ):
                                continue
                            try:
                                cmds.disconnectAttr(src, dst)
                                cut_pairs.add((dst, src))
                            except RuntimeError:
                                pass  # locked/refused: setKeyframe will skip it
                # segmentScaleCompensate is part of the drive being
                # neutralised: with SSC live, xform(matrix=) folds the
                # inverseScale compensation into the SHEAR channel -- which
                # is never keyed, so the last-written value sticks and
                # shears the local at every other frame (0.10-0.35 residue
                # on the production wire looms, re-blocking the export the
                # flatten had just fixed). The sampled effective local
                # already contains the compensation, so keys written with
                # SSC off reproduce the same worlds exactly.
                if cmds.attributeQuery("segmentScaleCompensate", node=obj, exists=True):
                    prior_ssc = cmds.getAttr(f"{obj}.segmentScaleCompensate")
                    if prior_ssc:
                        cmds.setAttr(f"{obj}.segmentScaleCompensate", False)
                        record = pending.get(obj)
                        if record is not None:
                            record["ssc"] = int(prior_ssc)
                if cmds.attributeQuery("shear", node=obj, exists=True):
                    prior_shear = cmds.getAttr(f"{obj}.shear")[0]
                    if any(abs(v) > 1e-9 for v in prior_shear):
                        record = pending.get(obj)
                        if record is not None:
                            record["shear_was"] = list(prior_shear)
                surviving.append((obj, plug, source_plug, channels))
            except RuntimeError as e:
                cmds.warning(f"SmartBake: could not neutralise '{plug}': {e}")
                for record in pending.pop(obj, {}).get("stashes", []):
                    BakeSessionStore.discard_stash(record)
                result.skipped.append(obj)

        if session is not None:
            for obj, _, _, _ in surviving:
                record = pending.get(obj)
                if record is None:
                    continue
                session["connections"].extend(record.pop("_connections", []))
                session["matrix"].append(record)

        # Every bake channel's live input was severed above, so the complete
        # folded local lands on the plugs whichever writer keys it.
        through_cmds: List[Tuple[str, str, str, List[str]]] = []
        for entry in surviving:
            obj, _, _, channels = entry
            if not self._write_matrix_keys(
                obj, channels, object_frames[obj], sampled[obj]
            ):
                through_cmds.append(entry)
        # xform -matrix parks a pivoted node's compensation in the pivot
        # translates, which are not keyed: only the last frame's value would
        # survive (as with shear below). Put the pre-bake values back.
        pivot_translates = {
            obj: (
                cmds.getAttr(f"{obj}.rotatePivotTranslate")[0],
                cmds.getAttr(f"{obj}.scalePivotTranslate")[0],
            )
            for obj, _, _, _ in through_cmds
        }
        for frame in frames if through_cmds else []:
            cmds.currentTime(frame)
            for obj, _, _, channels in through_cmds:
                low, high = windows[obj]
                if not low <= frame <= high:
                    continue
                # xform applies the whole matrix (jointOrient/rotateAxis
                # included -- probe-verified identity on orient-carrying
                # joints). A LOCKED channel still refuses and keeps its
                # value.
                cmds.xform(obj, matrix=list(sampled[obj][frame]))
                cmds.setKeyframe(obj, attribute=channels, time=frame)
        for obj, (rpt, spt) in pivot_translates.items():
            try:
                cmds.setAttr(f"{obj}.rotatePivotTranslate", *rpt)
                cmds.setAttr(f"{obj}.scalePivotTranslate", *spt)
            except RuntimeError:
                pass  # locked/connected: keeps whatever it holds

        for obj, _, _, channels in surviving:
            prior = result.baked.get(obj, [])
            result.baked[obj] = sorted(set(prior) | set(channels))
            # Whatever shear the per-frame xform writes, only the LAST value
            # survives (shear is not keyed) -- and FBX/glTF drop shear
            # anyway. Zero it so the static leftover cannot skew the local
            # at other frames; the pre-bake value is in the session record.
            # A sample that SHEARS cannot be reproduced by any t/r/s bake;
            # `sheared` (measured from the samples above) reports that.
            try:
                cmds.setAttr(f"{obj}.shear", 0.0, 0.0, 0.0)
            except RuntimeError:
                pass  # locked/connected shear keeps its own value

        written = {obj for obj, _, _, _ in surviving}
        sheared = [obj for obj in sheared if obj in written]
        if sheared:
            cmds.warning(
                f"SmartBake: {len(sheared)} matrix-driven object(s) fold to a "
                "SHEARED local, which no translate/rotate/scale bake can hold "
                "(FBX and glTF drop shear too) -- their worlds will drift, and "
                "the drift compounds down a chain. Run the Scene Exporter's "
                "flatten_sheared_chains first (it world-fits them onto a "
                f"shear-free parent). First: {', '.join(sheared[:3])}"
            )
        cmds.currentTime(restore_time)

    def _create_override_layer(self) -> str:
        """Create an empty override animation layer for baking.

        Delegates to AnimUtils.create_animation_layer() for layer creation.
        Deliberately does NOT pre-register attributes onto the layer (no
        ``attributes=`` kwarg): pre-registering via ``cmds.animLayer(edit=True,
        attribute=...)`` and then ``bakeResults(destinationLayer=...)`` onto
        the SAME freshly-created layer corrupts the bake — every sampled key
        comes back as one flat constant (whatever value was live at
        registration time) instead of the true per-frame curve. Proven live:
        a locator animated 0->5 baked through a pre-registered layer read
        back flat at every frame; handing bakeResults the empty layer and
        letting it wire the attributes itself reproduces the original motion
        exactly, including non-linear ("auto") tangent shape.

        Returns:
            Name of the created (empty) animation layer.
        """
        from mayatk.anim_utils._anim_utils import AnimUtils

        return AnimUtils.create_animation_layer(
            name="SmartBake_Override",
            override=True,
            preferred=True,
            timestamp_suffix=True,
            unique_name=True,
        )

    def _mute_driver_nodes(
        self, to_bake: Dict[str, BakeAnalysis]
    ) -> List[Tuple[str, int]]:
        """Mute driver nodes by setting nodeState=2 (Blocking).

        Parameters:
            to_bake: Dict of {object: BakeAnalysis} for objects being baked.

        Returns:
            List of ``(node, prior_nodeState)`` tuples for the restore manifest.
        """
        muted: List[Tuple[str, int]] = []
        seen: Set[str] = set()
        for obj, data in to_bake.items():
            for source_type, nodes in data.source_nodes.items():
                if source_type.startswith("inherited_visibility"):
                    continue  # ancestor curves/plugs, not driver nodes
                if source_type == "matrix":
                    # The direct matrix bake (both modes) has already
                    # disconnected this object's offsetParentMatrix, so there
                    # is nothing left to mute -- and the multMatrix may still
                    # feed OTHER consumers, which muting would freeze.
                    continue
                for node in nodes:
                    if node in seen or not cmds.objExists(node):
                        continue
                    seen.add(node)
                    try:
                        if cmds.attributeQuery("nodeState", node=node, exists=True):
                            prior = cmds.getAttr(f"{node}.nodeState")
                            cmds.setAttr(f"{node}.nodeState", 2)  # Blocking
                            muted.append((node, int(prior)))
                    except RuntimeError:
                        pass
        return muted

    @CoreUtils.undoable
    def bake(
        self,
        analysis: Optional[Dict[str, BakeAnalysis]] = None,
        time_range: Optional[Tuple[int, int]] = None,
    ) -> BakeResult:
        """Execute baking on analyzed objects.

        Parameters:
            analysis: Pre-computed analysis. If None, runs analyze().
            time_range: Custom time range. If None, auto-detects from drivers.

        Returns:
            BakeResult dataclass with baked, skipped, time_range, deleted,
            override_layer, backup_path, and muted_drivers.
        """
        if analysis is None:
            analysis = self.analyze()

        # An explicit range is honored for every object; auto resolves one per
        # object (a keyed driver's extent, one frame for a provably static
        # one) and widens the global range to cover them all.
        object_ranges: Dict[str, Tuple[int, int]] = {}
        if time_range is None:
            time_range = self.get_time_range(analysis)
            object_ranges = self.get_object_time_ranges(analysis, time_range)
            if object_ranges:
                time_range = (
                    min([time_range[0]] + [r[0] for r in object_ranges.values()]),
                    max([time_range[1]] + [r[1] for r in object_ranges.values()]),
                )

        result = BakeResult(time_range=time_range)

        # Collect objects that need baking
        to_bake = {obj: data for obj, data in analysis.items() if data.requires_bake}

        if not to_bake:
            result.skipped = list(analysis.keys())
            return result

        # Warn about conflicting options
        if self.use_override_layer and self.delete_inputs:
            cmds.warning(
                "SmartBake: delete_inputs is ignored when use_override_layer=True. "
                "Use mute_drivers=True instead to disable drivers without deleting."
            )

        # Save backup before any destructive operations
        result.backup_path = self._save_backup()

        # Restore-manifest session: records everything this bake changes so
        # SmartBake.restore() can reverse it (persisted on data_internal).
        from mayatk.anim_utils.smart_bake import bake_session

        session: Optional[dict] = None
        if self.restorable:
            # delete_inputs removes the driver nodes themselves — nothing to
            # reconnect afterwards, so the session is recorded but flagged
            # non-restorable (restore() then points at the backup instead).
            # mute_drivers takes precedence over delete_inputs at cleanup
            # time, so drivers survive (and the session stays restorable).
            will_delete = (
                self.delete_inputs
                and not self.use_override_layer
                and not self.mute_drivers
            )
            session = {
                "version": bake_session.BakeSessionStore.SCHEMA_VERSION,
                "id": bake_session.BakeSessionStore.new_session_id(),
                "restorable": not will_delete,
                "time_range": list(time_range),
                "override_layer": None,
                "baked_plugs": [],
                "layer_conversions": [],
                "connections": [],
                "stashed_curves": [],
                "visibility": [],
                "matrix": [],
                "ik_handles": [],
                "muted_drivers": [],
                "backup_path": result.backup_path,
            }

            # bakeResults(disableImplicitControl=True) zeroes ikBlend on the
            # handles EVEN when baking to an override layer — record the
            # pre-bake state so restore can re-enable IK.
            if session["restorable"]:
                seen_handles: Set[str] = set()
                for obj, data in to_bake.items():
                    for handle in data.source_nodes.get("ik", []):
                        if handle in seen_handles or not cmds.objExists(handle):
                            continue
                        seen_handles.add(handle)
                        if not cmds.attributeQuery("ikBlend", node=handle, exists=True):
                            continue
                        had_incoming = bool(
                            cmds.listConnections(
                                f"{handle}.ikBlend",
                                source=True,
                                destination=False,
                                type="animCurve",
                            )
                        )
                        session["ik_handles"].append(
                            {
                                "ref": bake_session.BakeSessionStore.node_ref(handle),
                                "ik_blend": float(cmds.getAttr(f"{handle}.ikBlend")),
                                "had_incoming": had_incoming,
                            }
                        )

        # Split inherited-visibility objects from standard driven channels.
        # These get their own dedicated layer and frame-by-frame sampling.
        inherited_vis_objects = {}
        remaining_to_bake = {}

        for obj, data in to_bake.items():
            if "inherited_visibility" in data.driven_channels:
                inherited_vis_objects[obj] = data
                # If the object also has other driven channels, include
                # it in the standard bake pass for those channels.
                other_channels = {
                    k: v
                    for k, v in data.driven_channels.items()
                    if k != "inherited_visibility"
                }
                if other_channels:
                    other_analysis = BakeAnalysis(object=obj)
                    other_analysis.driven_channels = other_channels
                    other_analysis.source_nodes = {
                        k: v
                        for k, v in data.source_nodes.items()
                        if not k.startswith("inherited_visibility")
                    }
                    other_analysis.already_keyed = list(data.already_keyed)
                    remaining_to_bake[obj] = other_analysis
            else:
                remaining_to_bake[obj] = data

        # Split matrix-driven objects out of the standard pass. bakeResults
        # samples the scalar t/r/s plugs, and for a matrix drive those are
        # identity -- it would write nine channels of zeros and the motion
        # would vanish the moment the matrix network was disconnected. Only an
        # explicit effective-local-matrix sample can bake these, and that
        # requires neutralising offsetParentMatrix (see _bake_matrix_drivers).
        matrix_objects: Dict[str, BakeAnalysis] = {}
        for obj in list(remaining_to_bake):
            data = remaining_to_bake[obj]
            if "matrix" not in data.driven_channels:
                continue
            matrix_objects[obj] = data
            # The object may ALSO be constraint- or IK-driven; those channels
            # still belong in the standard bakeResults pass.
            other_channels = {
                k: v for k, v in data.driven_channels.items() if k != "matrix"
            }
            if other_channels:
                other_analysis = BakeAnalysis(object=obj)
                other_analysis.driven_channels = other_channels
                other_analysis.source_nodes = {
                    k: v for k, v in data.source_nodes.items() if k != "matrix"
                }
                other_analysis.already_keyed = list(data.already_keyed)
                remaining_to_bake[obj] = other_analysis
            else:
                del remaining_to_bake[obj]

        # Create override layer for standard channels (excludes visibility)
        override_layer = None
        if self.use_override_layer and remaining_to_bake:
            override_layer = self._create_override_layer()
            result.override_layer = override_layer

        start, end = time_range

        from mayatk.anim_utils._anim_utils import AnimUtils

        # -----------------------------------------------------------
        # Phase 1: Bake inherited visibility via frame-by-frame sampling.
        #
        # bakeResults cannot resolve ancestor-inherited visibility; it
        # only evaluates the attribute's own value at each time.  We
        # manually sample the effective visibility (product of all
        # ancestor .visibility values) and key it on the mesh transform.
        # Keys are written on the BASE LAYER (not an override layer)
        # because FBX BakeComplexAnimation does not evaluate visibility
        # through animation-layer blend nodes.
        # -----------------------------------------------------------
        merged_vis_objects: Set[str] = set()
        if inherited_vis_objects:
            # Only record/stash for restorable sessions: restore() refuses
            # non-restorable (delete_inputs) sessions outright, so any stash
            # created for one could never be reclaimed and would leak locked
            # nodes into the scene.
            merged_vis_objects = self._bake_inherited_visibility(
                inherited_vis_objects,
                start,
                end,
                result,
                session=session if session and session["restorable"] else None,
            )

        # -----------------------------------------------------------
        # Phase 1b: Matrix drives (offsetParentMatrix).
        #
        # Baked DIRECTLY (base level) in BOTH modes. An animation layer
        # blends keyable scalars -- Maya has no matrix blend node -- so a
        # layer can never neutralise a matrix plug. And leaving the network
        # live for FBX does NOT work: FBXExportBakeComplexAnimation samples
        # the t/r/s plugs per frame but evaluates a CONNECTED
        # offsetParentMatrix only when its whole upstream translates to FBX.
        # A plain animCurve network bakes; anything constraint- or IK-driven
        # upstream (constraints are stripped on export) is sampled ONCE at
        # the export-time frame. Verified: a minimal repro shipped worldX
        # 0/0 for a live 0/25 (test_unbaked_opm_freezes_through_fbx), and
        # the production wire looms shipped 15.9 cm off exactly while their shot
        # animated. The direct bake is recorded in the session manifest and
        # reversed with the rest of the restore.
        # -----------------------------------------------------------
        if matrix_objects:
            # This pass reads two matrix plugs per object per FRAME, so it
            # takes the same per-object ranges bakeResults does -- but over
            # ONE timeline pass, since the scene evaluation each frame costs
            # is shared by the whole set.
            for obj in matrix_objects:
                result.object_time_ranges.setdefault(
                    obj, object_ranges.get(obj, (start, end))
                )
            self._bake_matrix_drivers(
                matrix_objects,
                start,
                end,
                result,
                session=session if session and session["restorable"] else None,
                object_ranges=object_ranges,
            )

        # -----------------------------------------------------------
        # Phase 2: Standard channel bake via bakeResults.
        # -----------------------------------------------------------

        # Bake each object with its specific channels over its own range:
        # one bakeResults per (channels, range) group. Merging groups buys
        # nothing (measured 0.99x: the cost is per frame evaluated, not per
        # timeline pass), so the groups only exist to hand each object the
        # narrowest range it needs.
        grouped_by_channels = collections.defaultdict(
            list
        )  # (tuple(channels), (start, end)) -> list[objects]

        for obj, data in remaining_to_bake.items():
            channels = data.all_driven_channels
            if not channels:
                result.skipped.append(obj)
                continue

            # SmartBake logic: explicit channel lists derived from analysis
            obj_range = object_ranges.get(obj, (start, end))
            result.object_time_ranges[obj] = obj_range
            grouped_by_channels[(tuple(sorted(channels)), obj_range)].append(obj)

        # Base-layer mode is destructive: bakeResults converts SDK curves in
        # place (the original animCurveU node is DELETED and replaced by a
        # same-named animCurveT) and disconnects driver networks.  Before
        # baking, snapshot each plug's incoming connections and stash a
        # locked duplicate of every animCurve feeding it (directly or
        # through passthrough nodes) so restore can rebuild the network.
        # Layer mode keeps the original connections live under the layer's
        # blend node, so there is nothing to reconnect — but DELETING that layer
        # makes Maya rebuild the direct link itself and re-derive any implicit
        # unitConversion from the working unit in force at that moment. The
        # exporter's is metres while the scene authored them in centimetres, so
        # record each plug's factor for restore_session to re-pin (this is what
        # scaled the wire-loom auto-bend channels by 100).
        if session is not None and session["restorable"] and self.use_override_layer:
            for obj, data in remaining_to_bake.items():
                if not data.all_driven_channels:
                    continue
                session["layer_conversions"].append(
                    bake_session.BakeSessionStore.snapshot_conversions(
                        obj, data.all_driven_channels
                    )
                )

        if (
            session is not None
            and session["restorable"]
            and not self.use_override_layer
        ):
            stashed_curve_nodes: Set[str] = set()
            for obj, data in remaining_to_bake.items():
                channels = data.all_driven_channels
                if not channels:
                    continue
                session["baked_plugs"].append(
                    {
                        "ref": bake_session.BakeSessionStore.node_ref(obj),
                        "channels": channels,
                    }
                )
                for channel in channels:
                    plug = f"{obj}.{channel}"
                    session["connections"].extend(
                        bake_session.BakeSessionStore.snapshot_connections(plug)
                    )
                    for curve in bake_session.BakeSessionStore.collect_upstream_curves(
                        plug, self.PASSTHROUGH_TYPES
                    ):
                        if curve not in stashed_curve_nodes:
                            stashed_curve_nodes.add(curve)
                            session["stashed_curves"].append(
                                bake_session.BakeSessionStore.stash_curve(curve)
                            )

        for (channels, obj_range), objects in grouped_by_channels.items():
            try:
                dest_layer = None
                if self.use_override_layer and override_layer:
                    dest_layer = override_layer

                # Using the unified bake command
                baked = AnimUtils.bake(
                    objects,
                    attributes=list(channels),
                    time_range=obj_range,
                    sample_by=self.sample_by,
                    preserve_outside_keys=self.preserve_outside_keys,
                    simulation=False,
                    destination_layer=dest_layer,
                    remove_baked_attr_from_layer=False,
                    bake_on_override_layer=False,
                    sparse_anim_curve_bake=False,
                    minimize_rotation=True,
                    disable_implicit_control=True,
                    control_points=False,
                    shape=False,
                    only_keyed=False,  # SmartBake analysis already determined driven channels
                )

                if baked:
                    for obj in objects:
                        # Merge, don't assign: the inherited-visibility pass
                        # may already have recorded ["v"] for this object.
                        prior = result.baked.get(obj, [])
                        result.baked[obj] = sorted(set(prior) | set(channels))
                else:
                    for obj in objects:
                        result.skipped.append(obj)

            except Exception as e:
                for obj in objects:
                    result.skipped.append(obj)
                cmds.warning(f"SmartBake: Failed to batch bake {channels}: {e}")

        # Handle driver node cleanup after all baking is complete
        if result.baked:
            if self.mute_drivers:
                # Mute drivers (set nodeState=2) - keeps them recoverable
                muted_with_states = self._mute_driver_nodes(to_bake)
                result.muted_drivers = [node for node, _ in muted_with_states]
                if session is not None:
                    session["muted_drivers"] = [
                        {
                            "ref": bake_session.BakeSessionStore.node_ref(node),
                            "prior_state": prior,
                        }
                        for node, prior in muted_with_states
                    ]
            elif self.delete_inputs and not self.use_override_layer:
                # Delete drivers (destructive).
                # IMPORTANT: bakeResults converts SDK curves (animCurveU*)
                # in-place to time-based curves (animCurveT*), reusing the
                # same node.  We must NOT delete nodes that are now the
                # baked result.  Check the current nodeType before deleting.
                for obj, data in to_bake.items():
                    if obj not in result.baked:
                        continue
                    for source_type, nodes in data.source_nodes.items():
                        # Ancestor vis curves/plugs are NOT driver inputs to
                        # this object — the parent's own animation was never
                        # baked away and must survive.
                        if source_type.startswith("inherited_visibility"):
                            continue
                        # The matrix bake already disconnected
                        # offsetParentMatrix; the multMatrix may still feed
                        # other consumers, so leave the network standing.
                        if source_type == "matrix":
                            continue
                        for node in nodes:
                            if not cmds.objExists(node):
                                continue
                            # Skip SDK curves that bakeResults converted
                            # in-place from animCurveU* to animCurveT*.
                            if source_type == "driven_key":
                                node_type = cmds.nodeType(node)
                                if node_type.startswith("animCurveT"):
                                    # bakeResults converted this SDK
                                    # curve — it's now the baked result.
                                    continue
                            try:
                                cmds.delete(node)
                                result.deleted.append(node)
                            except RuntimeError:
                                pass  # Node already deleted or protected

        # Optimize keys if requested — only on baked channels, not the
        # entire object.  Passing whole objects would let optimize_keys
        # delete pre-existing curves (e.g. stepped keys the user placed
        # manually) that happen to be constant-valued.
        if self._optimize_kwargs and result.baked:
            baked_curves = []
            # When an override layer exists, query its curves directly.
            # listConnections(plug) won't traverse animBlendNodes.
            if override_layer and cmds.objExists(override_layer):
                layer_curves = (
                    cmds.animLayer(override_layer, query=True, animCurves=True) or []
                )
                baked_curves = list(set(layer_curves))

            # Also include base-layer visibility curves.
            if result.visibility_curves:
                baked_curves.extend(result.visibility_curves.values())
                baked_curves = list(set(baked_curves))

            if not baked_curves:
                for obj, channels in result.baked.items():
                    for ch in channels:
                        if ch == "v" and obj in merged_vis_objects:
                            # The artist's own curve, merged into — optimizing
                            # it would delete keys this bake never wrote.
                            continue
                        plug = f"{obj}.{ch}"
                        curves = cmds.listConnections(
                            plug,
                            type="animCurve",
                            source=True,
                            destination=False,
                        )
                        if curves:
                            baked_curves.extend(curves)
                baked_curves = list(set(baked_curves))

            if baked_curves:
                AnimUtils.optimize_keys(
                    baked_curves,
                    recursive=False,
                    quiet=True,
                    **self._optimize_kwargs,
                )
            result.optimized = list(result.baked.keys())

        # Persist the restore manifest — only when the bake actually
        # changed something worth reversing.
        if session is not None:
            if result.baked or result.visibility_curves:
                if override_layer and cmds.objExists(override_layer):
                    session["override_layer"] = bake_session.BakeSessionStore.node_ref(
                        override_layer
                    )
                bake_session.BakeSessionStore.push(session)
                result.session_id = session["id"]
            else:
                # Bake was a no-op — discard any stashes created for it.
                for record in session["stashed_curves"]:
                    bake_session.BakeSessionStore.discard_stash(record)
                for entry in session["visibility"]:
                    if entry.get("stash"):
                        bake_session.BakeSessionStore.discard_stash(entry["stash"])

        # An object can be skipped by more than one phase — report it once.
        result.skipped = ptk.remove_duplicates(result.skipped)

        return result

    def execute(self) -> BakeResult:
        """High-level entry point: analyze and bake in one call.

        Returns:
            BakeResult dataclass with bake operation results.
        """
        analysis = self.analyze()
        return self.bake(analysis)

    # -------------------------------------------------------------------------
    # Restore
    # -------------------------------------------------------------------------

    @classmethod
    def list_sessions(cls) -> List[str]:
        """Return ids of restorable bake sessions recorded in this scene,
        oldest first."""
        from mayatk.anim_utils.smart_bake.bake_session import BakeSessionStore

        return BakeSessionStore.list_ids()

    @classmethod
    @CoreUtils.undoable
    def restore(cls, session_id: Optional[str] = None) -> "RestoreResult":
        """Reverse a bake session recorded by ``bake(restorable=True)``.

        Restores from the manifest persisted on the ``data_internal`` node,
        so this works in a later Maya session after scene save/reopen:

        - Deletes the override animation layer (drivers resume).
        - Unmutes drivers to their recorded nodeState values.
        - Re-enables IK handles (``disableImplicitControl`` zeroes ikBlend
          even when baking to a layer).
        - Restores visibility: deletes baked curves, reconnects the stashed
          original curve or resets the recorded static value.
        - Base-layer bakes: deletes the baked curves and rebuilds the driver
          network — reconnects recorded constraint/expression/motion-path
          plugs and unstashes SDK / blended key curves.

        Known limitation: if bake deleted an intermediate blend node (e.g. a
        pairBlend), its wiring cannot be rebuilt — the stashed curve is
        reconnected directly to the channel where possible and a warning is
        reported. Restore never raises on missing nodes; per-item issues are
        collected in ``RestoreResult.warnings``.

        Parameters:
            session_id: Session to restore. None (default) restores the most
                recent session (LIFO). The session is removed from the
                manifest either way — including non-restorable
                (delete_inputs) sessions, so older sessions stay reachable.

        Returns:
            RestoreResult with success flag, per-category restore lists, and
            warnings. ``success=False`` means the session was missing or
            recorded as non-restorable.
        """
        from mayatk.anim_utils.smart_bake.bake_session import (
            BakeSessionStore,
            RestoreResult,
        )

        session = BakeSessionStore.peek(session_id)
        if session is None:
            result = RestoreResult(session_id=session_id)
            result.warnings.append(
                "No bake session found to restore."
                if session_id is None
                else f"Bake session '{session_id}' not found."
            )
            cmds.warning(f"SmartBake: {result.warnings[0]}")
            return result

        result = BakeSessionStore.restore_session(session)
        # Pop only after the restore pass completes — an unexpected failure
        # mid-restore leaves the session in place so it can be retried.
        BakeSessionStore.pop(session.get("id"))
        for warning in result.warnings:
            cmds.warning(f"SmartBake restore: {warning}")
        return result

    @classmethod
    def restore_matrix_wiring(cls, session_id: Optional[str] = None) -> "RestoreResult":
        """Restore ONLY the matrix (offsetParentMatrix) bakes of a session.

        The keep-bake consumer: the scene exporter's "Scene Keys (In Place)"
        mode keeps the override layer and every scalar bake -- but baked
        matrix channels cannot stay. Their keys were written in whatever
        parent space the flatten task staged, and the deferred flatten
        restore reinstates the original offsetParentMatrix wiring, which
        would then compose ON TOP of the baked keys (a double transform).
        This hands exactly those channels back to their live drivers:
        deletes the baked t/r/s curves, unstashes what the channels held,
        and reconnects the recorded matrix source and driver plugs.

        The session manifest is left in place (not popped) and unmodified: a
        later full :meth:`restore` re-applies these sections harmlessly --
        curve deletion is a no-op, an already-made connection is skipped,
        and a since-deleted matrix source only logs a warning while the
        plug keeps the wiring the flatten restore gave it.

        Parameters:
            session_id: Session to slice. None restores from the most recent.

        Returns:
            RestoreResult for the slice; ``success=False`` when the session
            was missing or recorded as non-restorable.
        """
        from mayatk.anim_utils.smart_bake.bake_session import (
            BakeSessionStore,
            RestoreResult,
        )

        session = BakeSessionStore.peek(session_id)
        if session is None:
            result = RestoreResult(session_id=session_id)
            result.warnings.append(
                "No bake session found to restore."
                if session_id is None
                else f"Bake session '{session_id}' not found."
            )
            cmds.warning(f"SmartBake: {result.warnings[0]}")
            return result

        result = BakeSessionStore.restore_session(
            {
                "version": session.get("version"),
                "id": session.get("id"),
                "restorable": session.get("restorable", True),
                "matrix": session.get("matrix", []),
                "connections": session.get("connections", []),
            }
        )
        for warning in result.warnings:
            cmds.warning(f"SmartBake matrix-wiring restore: {warning}")
        return result

    @classmethod
    @contextmanager
    def session(cls, **kwargs):
        """Context manager: bake on enter, restore on exit.

        The scene is returned to its pre-bake state even if the body raises —
        made for export workflows::

            with SmartBake.session(objects=meshes) as result:
                export_fbx(...)
            # layer deleted, drivers unmuted, IK re-enabled

        Parameters:
            **kwargs: Forwarded to SmartBake.__init__ (restorable is forced
                True — the exit restore depends on the manifest).

        Yields:
            BakeResult from the enter-time bake.
        """
        kwargs["restorable"] = True
        result = cls(**kwargs).execute()
        try:
            yield result
        finally:
            if result.session_id:
                cls.restore(result.session_id)

    @classmethod
    def run(cls, **kwargs) -> BakeResult:
        """Class method for quick smart baking without explicit instantiation.

        Parameters:
            **kwargs: Forwarded to SmartBake.__init__:
                - objects: Objects to analyze/bake (default: all transforms/joints)
                - sample_by: Keyframe sample interval (default: 1)
                - preserve_outside_keys: Keep keys outside range (default: True)
                - delete_inputs: Delete driver nodes after bake (default: False)
                - optimize_keys: Optimization level for the baked output —
                  an AnimUtils.OPTIMIZE_LEVELS key, True for the default
                  level, falsy for OFF (default: False)
                - bake_blend_shapes: Bake driven blend shape weights (default: True)
                - use_override_layer: Bake to override layer (default: True)
                - mute_drivers: Mute drivers instead of deleting (default: False)
                - backup_file: Save backup before baking (default: None = auto)
                - restorable: Record a restore-manifest session (default: True)

        Returns:
            BakeResult dataclass with bake operation results.

        Example:
            >>> result = SmartBake.run()
            >>> result = SmartBake.run(objects=["pCube1"], delete_inputs=True)
            >>> # Non-destructive bake to layer with backup:
            >>> result = SmartBake.run(use_override_layer=True, backup_file=True)
            >>> if result.success:
            ...     print(f"Baked {result.baked_count} objects")
            ...     if result.override_layer:
            ...         print(f"Baked to layer: {result.override_layer}")
        """
        return cls(**kwargs).execute()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    pass
