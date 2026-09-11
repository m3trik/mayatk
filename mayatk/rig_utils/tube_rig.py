#!/usr/bin/env python
# coding=utf-8
import contextlib
import json
import math
import re
from typing import Callable, Dict, List, Tuple, Optional, Type, Union
from abc import ABC, abstractmethod
from dataclasses import dataclass

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError as error:
    cmds = None
    om = None
    print(__file__, error)
import pythontk as ptk

# from this package:
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.display_utils._display_utils import DisplayUtils
from mayatk.node_utils._node_utils import NodeUtils
from mayatk.rig_utils._rig_utils import RigUtils
from mayatk.rig_utils.controls import Controls
from mayatk.rig_utils.skinning import SkinUtils
from mayatk.xform_utils.matrices import Matrices

# TubePath lives in rig_utils.tube_path (pure geometry, no scene objects); the
# engine uses it AND this import keeps existing
# ``from ...tube_rig import TubeRig, TubePath`` callers working.
from mayatk.rig_utils.tube_path import TubePath
from mayatk.edit_utils.naming._naming import Naming


# ======================================================================
# Data Containers
# ======================================================================


@dataclass
class TubeRigBundle:
    rig_group: str
    joints: List[str]
    ik_handle: Optional[str] = None
    curve: Optional[str] = None
    anchors: Optional[List[str]] = None
    controls: Optional[List[str]] = None
    #: Secondary finesse layer (spline builds). Deliberately NOT merged into
    #: ``controls`` — end-control resolution and control-spacing checks read
    #: ``controls[0]/[-1]`` and must keep meaning the DRIVER controls.
    tweak_controls: Optional[List[str]] = None


# ======================================================================
# Build Strategies (orchestrate TubeRig methods)
# ======================================================================


class TubeStrategy(ABC):
    @abstractmethod
    def build(self, rig: "TubeRig", **kwargs) -> TubeRigBundle:
        pass


class FKChainStrategy(TubeStrategy):
    """Joints → nested FK controls → parametric skin.

    Thin composition of the same ``TubeRig`` step methods the UI's
    step-by-step buttons call — one-click and step-by-step run identical
    code with identical parameters by construction.
    """

    def build(self, rig: "TubeRig", **kwargs) -> TubeRigBundle:
        rig._report("Building FK chain: reading the tube's centerline…")
        centerline, num_joints = rig.resolve_centerline(
            kwargs.get("num_joints", -1), edges=kwargs.get("edges")
        )
        if kwargs.get("reverse"):
            centerline = list(centerline)[::-1]
        joint_radius, size = rig.resolve_sizes(centerline, kwargs.get("radius", -1.0))

        rig._report(f"Building FK chain: creating {num_joints} joints…")
        joints = rig.generate_joint_chain(
            centerline, num_joints=num_joints, radius=joint_radius
        )
        rig._report("Building FK chain: creating controls…")
        controls = rig.create_fk_controls(
            joints, size=size, num_controls=kwargs.get("num_controls", 5)
        )
        rig._report("Building FK chain: binding skin…")
        rig.skin_mesh(joints, centerline=centerline)

        rig._report("FK chain build complete.")
        return TubeRigBundle(rig_group=rig.rig_group, joints=joints, controls=controls)


class SplineIKStrategy(TubeStrategy):
    """Joints → spline-IK control rig → parametric skin along the IK curve."""

    def build(self, rig: "TubeRig", **kwargs) -> TubeRigBundle:
        rig._report("Building spline IK: reading the tube's centerline…")
        centerline, num_joints = rig.resolve_centerline(
            kwargs.get("num_joints", -1), edges=kwargs.get("edges")
        )
        if kwargs.get("reverse"):
            # Reverse before anything derives from the path so the joints,
            # the IK curve, and the skin solve all share one direction.
            centerline = list(centerline)[::-1]
        joint_radius, size = rig.resolve_sizes(centerline, kwargs.get("radius", -1.0))

        rig._report(f"Building spline IK: creating {num_joints} joints…")
        joints = rig.generate_joint_chain(
            centerline, num_joints=num_joints, radius=joint_radius
        )
        rig._report("Building spline IK: creating curve, IK and controls…")
        controls, ik_handle, curve = rig.create_spline_controls(
            joints,
            centerline=centerline,
            size=size,
            num_controls=kwargs.get("num_controls", 3),
            enable_stretch=kwargs.get("enable_stretch", True),
            enable_squash=kwargs.get("enable_squash", True),
            enable_volume=kwargs.get("enable_volume", True),
            enable_twist=kwargs.get("enable_twist", True),
            enable_auto_bend=kwargs.get("enable_auto_bend", False),
            enable_tweaks=kwargs.get("enable_tweaks", True),
        )
        rig._report("Building spline IK: binding skin…")
        rig.skin_mesh(joints, curve=curve)

        rig._report("Spline IK build complete.")
        return TubeRigBundle(
            rig_group=rig.rig_group,
            joints=joints,
            ik_handle=ik_handle,
            curve=curve,
            controls=controls,
            tweak_controls=rig.tweak_controls,
        )


class AnchorStrategy(TubeStrategy):
    """Two end joints → anchor controls with distance stretch → parametric skin."""

    def build(self, rig: "TubeRig", **kwargs) -> TubeRigBundle:
        rig._report("Building anchor rig: reading the tube's centerline…")
        centerline, _ = rig.resolve_centerline(2, edges=kwargs.get("edges"))
        if len(centerline) < 2:
            raise ValueError("Could not determine centerline")
        if kwargs.get("reverse"):
            centerline = list(centerline)[::-1]
        joint_radius, size = rig.resolve_sizes(centerline, kwargs.get("radius", -1.0))

        rig._report("Building anchor rig: creating end joints…")
        joints = rig.create_anchor_joints(centerline, radius=joint_radius)
        rig._report("Building anchor rig: creating controls…")
        controls = rig.create_anchor_controls(
            joints, size=size, enable_stretch=kwargs.get("enable_stretch", True)
        )
        rig._report("Building anchor rig: binding skin…")
        rig.skin_mesh(joints, centerline=centerline)

        rig._report("Anchor rig build complete.")
        return TubeRigBundle(
            rig_group=rig.rig_group,
            joints=joints,
            anchors=None,
            controls=controls,
        )


# ======================================================================
# Rig Engine
# ======================================================================


class _TubeRigInternal(object):
    """Internal helpers for TubeRig."""

    #: Phase-report hook for the operation currently in flight, or ``None``.
    #: Deliberately per-operation state (scoped by :meth:`_reporting`) rather
    #: than a constructor argument: ``TubeRig`` caches one instance per mesh
    #: UUID, so a hook set at construction would outlive the UI that owns it
    #: and a later script call would tick a footer that is no longer on screen.
    _progress = None

    @contextlib.contextmanager
    def _reporting(self, progress: Optional[Callable]):
        """Route this operation's phase reports to *progress* for its duration.

        A nested operation without a hook of its own INHERITS the caller's:
        ``build`` over an existing rig calls ``teardown()`` bare, and masking
        the build's hook there would silence the teardown phases exactly when
        a rebuild is running. Outside any operation the ambient hook is
        ``None``, so a standalone bare call still reports nothing.

        Parameters:
            progress: ``callable(current, total, message)`` -- the ecosystem's
                mayatk progress-callback shape, so ``Switchboard.progress_adapter``
                wires a footer straight in. ``None`` inherits (or disables).
        """
        prev = self._progress
        self._progress = progress if progress is not None else prev
        try:
            yield
        finally:
            self._progress = prev

    def _report(self, message: str) -> None:
        """Announce a build/teardown phase: log it, and tick the progress hook.

        One call site for both so a phase can never be logged but not shown
        (or the reverse). The hook is ticked indeterminately -- ``current=None``,
        ``total=0`` -- because a rig's phase count varies with the strategy and
        the options; the message is what tells the user the tool is working.

        A hook that raises is dropped rather than allowed to abort the rig:
        feedback failing is never a reason to leave a half-built rig behind.
        """
        self.logger.info(message)
        cb = self._progress
        if cb is None:
            return
        try:
            cb(None, 0, message)
        except Exception as e:
            self._progress = None
            self.logger.debug(f"Progress hook dropped ({e}).")

    @staticmethod
    def _twist_up_axis(points) -> Tuple[float, float, float]:
        """A world axis for the spline twist's up vector, never along the run.

        ``dWorldUpType=4`` solves the advanced twist against an up VECTOR, and
        the build hard-coded world +Y for every rig. Wherever the tangent runs
        along Y that is degenerate, and the failure is worse than an arbitrary
        roll: measured 2026-09-10, a 0.25-unit nudge sprayed 90 deg of ring
        twist on an axis-Y tube while the roll channel itself went INERT --
        end-control ``rotateX`` of 15, 45 and 90 deg each produced 0.000 roll,
        against 14.33 / 42.99 / 85.98 on the same rig built along X.

        Judged against EVERY segment, not the end-to-end chord. A U-shaped hose
        has both ends at the top, so its chord is horizontal while both legs run
        along Y -- picking the axis least parallel to the chord would hand those
        legs the degenerate vector the whole fix exists to avoid. Scoring the
        WORST segment instead means an axis is only chosen if no part of the run
        is near-parallel to it.

        Y is tried first and ties keep it, so every rig whose run is not
        vertical anywhere keeps the vector it already had and rebuilds
        identically -- this removes the degeneracy without touching the twist
        behaviour of any rig that was working.

        Parameters:
            points: World positions along the run, in order (the controls, or
                the two ends when that is all a caller has).

        Returns:
            A unit world axis as ``(x, y, z)``; ``(0, 1, 0)`` when the points
            coincide and there is no run to be parallel to.
        """
        directions = []
        for here, there in zip(points, list(points)[1:]):
            span = ptk.MathUtils.get_vector_from_two_points(here, there)
            if ptk.MathUtils.get_magnitude(span) > 1e-9:
                directions.append(ptk.MathUtils.normalize(span))
        if not directions:
            return (0.0, 1.0, 0.0)

        axes = ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
        return min(
            axes,
            key=lambda a: max(abs(ptk.MathUtils.dot_product(d, a)) for d in directions),
        )

    @staticmethod
    def _up_vector_of(up_loc, control) -> Tuple[float, float, float]:
        """The world direction a twist up-locator was offset in from *control*.

        Read back rather than passed through, so the vector the solver is told
        about can never disagree with the geometry the locator was built with.
        """
        here = _TubeRigInternal._xform_t_ws(str(up_loc))
        there = _TubeRigInternal._xform_t_ws(str(control))
        direction = ptk.MathUtils.safe_normalize(
            ptk.MathUtils.get_vector_from_two_points(there, here), (0.0, 1.0, 0.0)
        )
        return tuple(float(c) for c in direction)

    @staticmethod
    def _xform_t_ws(node) -> List[float]:
        """World-space translation as a 3-list (replaces ``node.getTranslation(space='world')``)."""
        return cmds.xform(str(node), q=True, ws=True, t=True)

    @staticmethod
    def _set_t_ws(node, pos) -> None:
        """Write world-space translation (replaces ``node.setTranslation(p, space='world')``)."""
        cmds.xform(str(node), ws=True, t=(float(pos[0]), float(pos[1]), float(pos[2])))

    @staticmethod
    def _set_r_ws(node, rot) -> None:
        """Write world-space rotation in degrees (replaces ``node.setRotation(r, space='world')``)."""
        cmds.xform(str(node), ws=True, ro=(float(rot[0]), float(rot[1]), float(rot[2])))

    @staticmethod
    def _uncrossed_end_index(joints: "List[str]", anchor_pos, joint_index: int) -> int:
        """Return the end index the anchor at *anchor_pos* actually belongs to.

        A crossed call — handing the far end's anchor to ``joint_index=0`` (or
        the near end's to ``-1``) — builds a rig that looks right at rest and
        tears itself apart the moment the anchor moves: the anchor joint is
        created at the far end but named for, and wired to, the near end's
        control. Found in a production assembly 2026-08-25 on 2 of 7 tubes.

        Only the two END indices can be crossed; a mid-chain index has no
        opposite end and is returned unchanged. The swap needs a clear margin
        so a tube whose ends nearly coincide is left alone rather than
        flip-flopping on float noise.

        Note: this is a per-call invariant, so it cannot see a caller that
        hands BOTH anchors to the same end — that input is degenerate either
        way. Callers holding both anchors (``TubeRigSlots.b004``) assign them
        pairwise first, which keeps one anchor per end.
        """
        n = len(joints)
        if n < 2:
            return joint_index
        idx = joint_index % n
        if idx not in (0, n - 1):
            return joint_index
        opposite = n - 1 if idx == 0 else 0

        def _d(j):
            p = _TubeRigInternal._xform_t_ws(joints[j])
            return sum((p[i] - anchor_pos[i]) ** 2 for i in range(3)) ** 0.5

        here, there = _d(idx), _d(opposite)
        # 1% of the end-to-end span is the noise floor we require to act.
        span = _TubeRigInternal._xform_t_ws(joints[0])
        span_end = _TubeRigInternal._xform_t_ws(joints[-1])
        margin = (sum((span[i] - span_end[i]) ** 2 for i in range(3)) ** 0.5) * 0.01
        return opposite if there < here - margin else joint_index

    @staticmethod
    def _long_path(node) -> Optional[str]:
        """Return the long DAG path for *node*, or the input unchanged if unresolvable."""
        if node is None:
            return None
        s = str(node)
        if not s:
            return s
        res = cmds.ls(s, long=True) or []
        return res[0] if res else s

    @staticmethod
    def _parent_to(child, parent) -> str:
        """Parent ``child`` under ``parent`` (or world if ``parent is None``) and return
        the new long path. No-op if the child is already under the intended parent.
        Wraps ``cmds.parent`` and returns the new long path.
        """
        child_s = str(child)
        current = NodeUtils.get_parent(
            child_s, type=None, full_path=True
        )  # already a long path or None
        if parent is None:
            if current is None:
                return _TubeRigInternal._long_path(child_s)
            result = cmds.parent(child_s, world=True)
            return result[0] if result else _TubeRigInternal._long_path(child_s)
        parent_long = _TubeRigInternal._long_path(parent)
        if current and parent_long and current == parent_long:
            return _TubeRigInternal._long_path(child_s)
        try:
            result = cmds.parent(child_s, str(parent))
            return result[0] if result else _TubeRigInternal._long_path(child_s)
        except RuntimeError as e:
            cmds.warning(f"_parent_to: could not parent {child_s} under {parent}: {e}")
            return _TubeRigInternal._long_path(child_s)

    @staticmethod
    def _frame_rotation(x_dir: "om.MVector") -> "om.MEulerRotation":
        """Euler rotation of a frame whose X axis is ``x_dir``, with a stable
        Y-up-ish orthogonal basis (falls back to Z-up when ``x_dir`` is near
        vertical). Shared by the anchor and spline driver builders.
        """
        x = om.MVector(x_dir).normal()
        world_up = om.MVector(0, 1, 0)
        if abs(x * world_up) > 0.99:
            world_up = om.MVector(0, 0, 1)
        z = (x ^ world_up).normal()
        y = (z ^ x).normal()
        m = om.MMatrix(
            [
                [x.x, x.y, x.z, 0],
                [y.x, y.y, y.z, 0],
                [z.x, z.y, z.z, 0],
                [0, 0, 0, 1],
            ]
        )
        return om.MTransformationMatrix(m).rotation()

    @staticmethod
    def _euler_deg(euler) -> Tuple[float, float, float]:
        """Convert an ``om.MEulerRotation`` (radians) to a degrees 3-tuple."""
        return (math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z))

    @staticmethod
    def _path_end_directions(points) -> Tuple["om.MVector", "om.MVector"]:
        """Unit tangents at the start and end of a point path (local, not chord —
        correct for curved tubes)."""
        p = [
            om.MVector(pt[0], pt[1], pt[2])
            for pt in (points[0], points[1], points[-2], points[-1])
        ]
        return (p[1] - p[0]).normal(), (p[3] - p[2]).normal()

    @staticmethod
    def _world_x_axis(node) -> "om.MVector":
        """World-space X axis of a transform (a joint's down-the-bone direction)."""
        m = cmds.xform(str(node), q=True, ws=True, matrix=True)
        v = om.MVector(m[0], m[1], m[2])
        return v.normal() if v.length() > 1e-6 else om.MVector(1, 0, 0)

    @staticmethod
    def _shape_width(node) -> float:
        """Widest world-space extent of a node's OWN shapes.

        Deliberately not ``exactWorldBoundingBox(transform)``: FK controls
        nest, so a transform's box spans the entire remaining chain and every
        control measures as enormous.
        """
        widths = []
        for shp in cmds.listRelatives(str(node), shapes=True, fullPath=True) or []:
            b = cmds.exactWorldBoundingBox(shp)
            widths.append(max(b[3] - b[0], b[4] - b[1], b[5] - b[2]))
        return max(widths) if widths else 0.0

    @staticmethod
    def _fit_control_size(preset: str, target_width: float, axis: str = "x") -> float:
        """The ``size`` value that renders *preset* about *target_width* wide.

        ``Controls.create(size=...)`` is a uniform multiplier on whatever
        extent the preset happens to author, not a width — so the ratio is
        measured from a throwaway control rather than assumed. Doing it by
        measurement keeps the fit correct if a preset's shape is ever
        redrawn, and works for presets whose shape isn't a unit primitive.
        """
        nodes = Controls.create(
            preset, name="_tubeRigSizeProbe", size=1.0, axis=axis, return_nodes=True
        )
        root = nodes.group if nodes.group else nodes.control
        try:
            unit = _TubeRigInternal._shape_width(nodes.control)
        finally:
            if cmds.objExists(str(root)):
                cmds.delete(str(root))
        return target_width / unit if unit > 1e-6 else target_width

    @staticmethod
    def _control_path(nodes, group_path: str) -> str:
        """Long path of a control whose offset group was just reparented to
        *group_path*.

        ``ControlNodes.control`` is captured before we move the group, so it goes
        stale the moment the group is reparented (and outright wrong when a
        same-named control exists elsewhere). With an offset group the control is
        the group's direct child (``<group_path>|<control_leaf>``); with none,
        the callers reparent the control itself, so *group_path* already IS the
        control's post-reparent path — the stored ``nodes.control`` would be the
        stale pre-reparent address.
        """
        if nodes.group is None:
            return str(group_path)
        return f"{group_path}|{CoreUtils.leaf_name(nodes.control)}"

    @staticmethod
    def _chain_controller_tags(ordered_controls) -> None:
        """Parent successive controller tags so pick-walking traverses the
        rig in build order.

        ``Controls.create`` already tags every control (``cmds.controller``);
        chaining is the missing half — without ``-parent`` links each tag is
        an island and pick-walk goes nowhere. Tag relationships live on the
        controller NODES, so they survive the DAG reparenting the builders
        do afterwards (follow groups, space groups).
        """
        for parent, child in zip(ordered_controls, ordered_controls[1:]):
            try:
                cmds.controller(str(child), str(parent), p=True)
            except RuntimeError as e:
                cmds.warning(
                    f"_chain_controller_tags: could not link {child} -> {parent}: {e}"
                )


class TubeRig(ptk.LoggingMixin, _TubeRigInternal):
    """Rig engine for tube-shaped meshes: joints, IK, controls, skinning.

    Parameters:
        obj (str/obj): The polygon tube mesh to rig.
        rig_name (str): The name of the rig (auto-generated if omitted).
        rig_group (str): An existing group node to build under — it stays
            yours: a rebuild empties it, ``teardown`` hands it back empty.
            Auto-created as ``<rig_name>_GRP`` beside the mesh (under its
            parent, so the rig rides whatever animates the mesh) if omitted.

    Attributes:
        mesh (str): The tube mesh transform the rig binds to.
        joints (List[str]): The main joint chain (set by ``build``).
        ik_handle (Optional[str]): The IK handle, when the strategy creates one.
        skin_cluster (Optional[str]): The mesh's skinCluster, once bound.
        bundle (Optional[TubeRigBundle]): Full result of the last ``build``.

    Example:
        rig = TubeRig(mesh, rig_name="hose")
        rig.build(strategy="spline", num_joints=-1)  # -1 = joint per edge loop
        rig.bundle.controls  # animation controls

        # Later, look the rig up from the mesh or anything under the rig group:
        rig = TubeRig.for_node(selected_joint_or_mesh)

    Rebuilding on an already-rigged mesh tears the previous build down first
    (``teardown``). Instances are tracked in-session by mesh/group UUID;
    across a restart ``for_node`` falls back to the ``DATA_ATTR`` record the
    build stamps on the rig group (``from_scene``).
    """

    # Class-level back-reference cache. cmds-based code uses plain node-path
    # strings, which can't carry an attached ``.rig`` attribute the way object
    # wrappers did. Keyed by the mesh's Maya UUID — stable across rename and
    # reparent (path strings are not).
    _instances: Dict[str, "TubeRig"] = {}

    #: Build-strategy registry (name -> TubeStrategy subclass). Register new
    #: strategies here instead of editing ``build``.
    STRATEGIES: Dict[str, Type[TubeStrategy]] = {
        "spline": SplineIKStrategy,
        "anchor": AnchorStrategy,
        "fk": FKChainStrategy,
    }

    #: Channel-box contract for every animatable control: translate/rotate
    #: keyable, scale locked AND hidden (a keyed scale on a stretch-driven
    #: control corrupts the rig silently), visibility hidden non-keyable but
    #: left unlocked so the settings control's vis toggle can drive it.
    CONTROL_CHANNEL_POLICY: Dict[str, Tuple[str, ...]] = {
        "keyable": ("t", "r"),
        "lock": ("s",),
        "hide": ("s", "v"),
    }

    #: Scene-persisted rig record: a JSON string attribute on the rig group
    #: (name, mesh UUID, strategy, build options). The in-session registry
    #: dies with Maya; this is how ``from_scene`` / ``for_node`` resolve a rig
    #: after a restart, and how a rebuild knows its own settings.
    DATA_ATTR: str = "tubeRigData"

    def __init__(self, obj, rig_name: str = None, rig_group: str = None):
        self._rig_name = self._clean_rig_name(rig_name) or None
        self._rig_group = rig_group  # Only assigned if explicitly passed (else will be handled by property)
        if rig_group and cmds.objExists(str(rig_group)):
            grp_uuid = TubeRig._uuid(rig_group)
            if grp_uuid:
                TubeRig._instances[grp_uuid] = self
        if isinstance(obj, (set, list, tuple)):
            obj = next(iter(obj), None)
        # Prefer the mesh transform even when a GROUP was picked (common
        # outliner selection) — everything downstream (centerline, skinning)
        # needs the mesh, not its group.
        shape = TubePath._resolve_mesh_shape(obj)
        if shape:
            self.mesh = NodeUtils.get_parent(shape, type=None, full_path=True) or str(
                shape
            )
        else:
            # No mesh anywhere below — keep accepting plain transforms
            # (b002 constructs a TubeRig from a joint after a restart).
            resolved = NodeUtils.get_transform_node(obj)
            if not resolved:
                raise ValueError(f"Invalid object: `{obj}` {type(obj)}")
            if isinstance(resolved, (set, list, tuple)):
                resolved = resolved[0]
            self.mesh = str(resolved)
        self._mesh_uuid = TubeRig._uuid(self.mesh)
        if self._mesh_uuid:
            TubeRig._instances[self._mesh_uuid] = self
        self.joints = None
        self.ik_handle = None
        self.pole_vector = None
        self.skin_cluster = None
        self.start_loc = None
        self.end_loc = None
        self.anchors = None
        self.tweak_controls = None
        self.bundle = None
        self._rings_cache = None

    @staticmethod
    def _clean_rig_name(name: Optional[str]) -> Optional[str]:
        """A rig name Maya will keep verbatim, or None for an empty one.

        The UI's free-text field flows in verbatim. Illegal characters crash
        the stale-sweep ``cmds.ls`` pattern ('hose-01', 'my rig'), and names
        Maya auto-sanitizes on createNode (leading digit, '*') would no longer
        match that pattern, accumulating chains on rerun.
        """
        if not name:
            return None
        name = Naming.strip_illegal_chars(name)
        if name and name[0].isdigit():
            name = f"_{name}"
        return name or None

    @staticmethod
    def _uuid(node) -> Optional[str]:
        """Return the Maya UUID for *node*, or None if it doesn't exist."""
        if node is None:
            return None
        s = str(node)
        if not s or not cmds.objExists(s):
            return None
        res = cmds.ls(s, uuid=True) or []
        return res[0] if res else None

    @classmethod
    def for_mesh(cls, mesh) -> Optional["TubeRig"]:
        """Look up an existing TubeRig instance bound to *mesh*, or return None.

        Resolves *mesh* to a transform node, then to its UUID, before doing the
        cache lookup — so callers can pass a shape, a stale path, or the mesh
        transform interchangeably.
        """
        if mesh is None:
            return None
        target = NodeUtils.get_transform_node(mesh) or mesh
        uuid = cls._uuid(target)
        if not uuid:
            return None
        rig = cls._instances.get(uuid)
        if rig is None:
            return None
        if not rig._live_mesh():  # the mesh is gone, so is the instance
            cls._instances.pop(uuid, None)
            return None
        return rig

    @classmethod
    def for_node(cls, node) -> Optional["TubeRig"]:
        """Find the TubeRig owning *node* — the rigged mesh itself, or
        anything under the rig group (joints, controls, sub-groups).

        ``build`` registers the rig group alongside the mesh, so walking a
        node's ancestors resolves joints/controls back to their rig; on a
        registry miss (a restart) the scene record is read (``from_scene``).
        None when *node* belongs to no tube rig.
        """
        if node is None:
            return None
        rig = cls.for_mesh(node)
        if rig is not None:
            return rig
        # Walk from the raw node — NodeUtils.get_transform_node returns a
        # *list* of related transforms for joints, which is useless here.
        node_s = str(node)
        if cmds.objExists(node_s):
            parent = NodeUtils.get_parent(node_s, type=None, full_path=True)
            while parent:
                rig = cls.for_mesh(parent)
                if rig is not None:
                    return rig
                parent = NodeUtils.get_parent(parent, type=None, full_path=True)
        # Registry miss (a restart, or a rig built by another session): read
        # the rig back from the scene record.
        return cls.from_scene(node)

    # ------------------------------------------------------------------
    # Scene record (survives a restart; see ``DATA_ATTR``)
    # ------------------------------------------------------------------

    @classmethod
    def scene_data(cls, node) -> Optional[dict]:
        """The ``DATA_ATTR`` record on *node* as a dict, or None."""
        node = str(node) if node else ""
        if not node or not cmds.objExists(node):
            return None
        if not cmds.attributeQuery(cls.DATA_ATTR, node=node, exists=True):
            return None
        try:
            data = json.loads(cmds.getAttr(f"{node}.{cls.DATA_ATTR}") or "")
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _stamp(self, **fields) -> None:
        """Merge *fields* into the rig group's ``DATA_ATTR`` record.

        Name and mesh UUID are always (re)written; ``build`` adds the
        strategy and its options. No-op until the group exists — the
        ``rig_group`` property stamps on creation.
        """
        grp = str(self._rig_group) if self._rig_group else ""
        if not grp or not cmds.objExists(grp):
            return
        data = self.scene_data(grp) or {}
        data.update(fields)
        data["name"] = self.rig_name
        # The group's OWN uuid, so a record can tell whether it is still on the
        # group it was written for. cmds.duplicate copies DATA_ATTR verbatim --
        # mesh_uuid included -- so without this a duplicated rig resolved to the
        # ORIGINAL's mesh, and Remove Rig on the copy tore down the original.
        data["group_uuid"] = TubeRig._uuid(grp)
        if self._mesh_uuid and TubePath._resolve_mesh_shape(self.mesh):
            data["mesh_uuid"] = self._mesh_uuid
        if not cmds.attributeQuery(self.DATA_ATTR, node=grp, exists=True):
            cmds.addAttr(grp, longName=self.DATA_ATTR, dataType="string")
        cmds.setAttr(
            f"{grp}.{self.DATA_ATTR}", json.dumps(data, sort_keys=True), type="string"
        )

    @classmethod
    def _rig_group_of(cls, node) -> Optional[str]:
        """Long path of the rig group owning *node*, or None.

        Walks *node*'s ancestors (and, for a skinned mesh, its first
        influence's) for a stamped group. Rigs built before ``DATA_ATTR``
        existed resolve by the one marker only a tube rig makes — a
        ``<X>_GRP`` beside a ``<X>_controls_SET`` object set. Joint or
        skinCluster names are NOT enough: ``<X>_jnt_1`` under ``<X>_GRP`` is
        a convention any hand-built hierarchy may share, and a false
        positive here hands "Remove Rig" a user's group.
        """
        node_s = str(node) if node else ""
        if not node_s or not cmds.objExists(node_s):
            return None
        starts = [node_s]
        shape = TubePath._resolve_mesh_shape(node_s)
        if shape:
            sc = SkinUtils.get_skin_cluster(shape)
            influences = SkinUtils.get_influences(sc, long_names=True) if sc else []
            starts = influences[:1] + starts
        for start in starts:
            path = _TubeRigInternal._long_path(start)
            while path:
                if cmds.attributeQuery(cls.DATA_ATTR, node=path, exists=True):
                    return path
                leaf = CoreUtils.leaf_name(path)
                if leaf.endswith("_GRP"):
                    marker = f"{leaf[: -len('_GRP')]}_controls_SET"
                    if cmds.objExists(marker) and cmds.nodeType(marker) == "objectSet":
                        return path
                path = NodeUtils.get_parent(path, type=None, full_path=True)

        # Last resort, and the one that matters for recovery: a mesh whose
        # BIND was destroyed (a UV round-trip, Delete History) has no
        # influence to walk from and never sits under the rig group, so the
        # ancestor scan above cannot reach its rig at all. The build stamps
        # the mesh UUID into the group's record -- match on that instead, so
        # "select the tube and rebind" still finds the rig it belongs to.
        if shape:
            mesh = NodeUtils.get_parent(shape, type=None, full_path=True) or node_s
            mesh_uuid = cls._uuid(mesh)
            if mesh_uuid:
                stamped = (
                    cmds.ls(f"*.{cls.DATA_ATTR}", objectsOnly=True, long=True) or []
                )
                for grp in stamped:
                    if (cls.scene_data(grp) or {}).get("mesh_uuid") == mesh_uuid:
                        return grp
        return None

    @staticmethod
    def _bound_mesh(grp: str) -> Optional[str]:
        """The mesh transform skinned to the joints under *grp*, or None.

        Found through those joints ONLY, never by name. A ``<name>_skinCluster``
        seed resolved by bare ``cmds.objExists`` belongs to whichever rig owns
        that short name, and for a DUPLICATED group that is the original -- the
        one mesh this must never hand back. A copy's own joints carry no
        skinCluster connection (``cmds.duplicate`` does not copy them), so a copy
        correctly resolves to None rather than to someone else's rig.

        A renamed cluster is covered either way; the IK curve's own skinCluster
        (driver joints) is skipped — only a MESH geometry counts.
        """
        candidates: List[str] = []
        joints = (
            cmds.listRelatives(grp, allDescendents=True, type="joint", fullPath=True)
            or []
        )
        for j in joints:
            candidates += (
                cmds.listConnections(f"{j}.worldMatrix[0]", type="skinCluster") or []
            )
        for sc in dict.fromkeys(candidates):
            for geo in cmds.skinCluster(sc, query=True, geometry=True) or []:
                if cmds.nodeType(geo) == "mesh":
                    return NodeUtils.get_parent(geo, type=None, full_path=True) or geo
        return None

    @classmethod
    def from_scene(cls, node) -> Optional["TubeRig"]:
        """Rebuild a ``TubeRig`` handle from what the scene holds for the rig
        owning *node* (mesh, joint, control, group) — the path ``for_node``
        takes once the in-session registry is gone. Returns None when *node*
        belongs to no tube rig.

        The handle records what a build would have (joints, skinCluster, IK
        handle, group) so teardown / rename / re-anchoring work on it; the
        build's control paths are re-resolved by name where needed.
        """
        grp = cls._rig_group_of(node)
        if grp is None:
            return None
        data = cls.scene_data(grp) or {}
        name = data.get("name") or CoreUtils.leaf_name(grp)[: -len("_GRP")]
        # A record whose group_uuid is not THIS group arrived by cmds.duplicate,
        # which copies the attribute verbatim. Its mesh_uuid still names the
        # ORIGINAL's tube, so trusting it made Remove Rig / Rebind Skin on the
        # copy act on the original. A record with NO group_uuid predates this
        # stamp and stays trusted, so rigs already stamped keep resolving.
        stamped = data.get("group_uuid")
        is_copy = bool(stamped) and stamped != TubeRig._uuid(grp)
        if is_copy:
            name = CoreUtils.leaf_name(grp)[: -len("_GRP")]
        mesh = None
        if not is_copy and data.get("mesh_uuid"):
            mesh = (cmds.ls(data["mesh_uuid"], long=True) or [None])[0]
        if not mesh:
            mesh = cls._bound_mesh(grp)
        rig = cls(mesh or grp, rig_name=name, rig_group=grp)
        root = cmds.ls(f"{name}_jnt_1", type="joint", long=True) or []
        if root:
            rig.joints = [str(j) for j in RigUtils.get_joint_chain_from_root(root[0])]
        else:
            anchors = cmds.ls(
                f"{name}_start_jnt", f"{name}_end_jnt", type="joint", long=True
            )
            rig.joints = anchors or None
        rig.skin_cluster = SkinUtils.get_skin_cluster(mesh) if mesh else None
        rig.ik_handle = (cmds.ls(f"{name}_ikHandle", long=True) or [None])[0]
        return rig

    def _rig_home(self) -> Optional[str]:
        """Long path of the mesh's parent — where a new rig group is created —
        or None (world root) for a mesh at the root, or a rig constructed from
        a bare joint chain (nothing to sit beside)."""
        mesh = self._live_mesh()
        if not (mesh and TubePath._resolve_mesh_shape(mesh)):
            return None
        return NodeUtils.get_parent(mesh, type=None, full_path=True)

    def _owns_group(self, grp: str) -> bool:
        """True when *grp* was created by ``rig_group`` (so ``teardown`` may
        delete the node); a caller-supplied group is emptied and kept instead.
        Records older than the marker were always auto-named."""
        data = self.scene_data(grp) or {}
        return bool(
            data.get("owned_group", CoreUtils.leaf_name(grp) == f"{self.rig_name}_GRP")
        )

    def _group_path(self) -> Optional[str]:
        """Long path of the rig group if it EXISTS — never creates it (the
        ``rig_group`` property does)."""
        grp = self._rig_group or f"{self.rig_name}_GRP"
        grp = str(grp)
        return _TubeRigInternal._long_path(grp) if cmds.objExists(grp) else None

    def _member_nodes(self) -> List[str]:
        """Every node this rig actually owns, by GRAPH REACHABILITY.

        A ``<rig>_*`` NAME GLOB is not ownership. It also matches whatever an
        artist happened to name with the same stem -- an unrigged ``cable_B``
        mesh, a ``cable_rubber_MAT`` shader -- beside a rig called ``cable``, and
        renaming or deleting those is silent collateral damage in someone's scene.

        Ownership is: the rig group and its descendants, the skinCluster (which
        lives OUTSIDE the group, in the bound mesh's history), the control set,
        and the DG utility nodes hanging off the rig's own DAG members.

        That last walk is transitive through DG nodes but NEVER crosses a DAG
        node, and that boundary is what keeps it inside the rig: the stretch and
        volume network is several hops deep (curve -> curveInfo -> ``_norm_MD``
        -> ``_vol_POW``), so one hop misses real members; but every route OUT of
        the rig runs through a DAG node -- the skinCluster reaches the bound mesh
        SHAPE, and only from that shape do the shading engine and its materials
        become reachable. Stopping at DAG keeps a same-stem shader out of the
        work set. Callers apply the name prefix as a FILTER, never as the search.
        """
        members = set()
        grp = self._group_path()
        if grp:
            members.add(grp)
            members.update(
                cmds.listRelatives(grp, allDescendents=True, fullPath=True) or []
            )
        mesh = self._live_mesh()
        if mesh:
            shape = NodeUtils.get_shape(mesh)
            if shape:
                members.update(
                    cmds.ls(cmds.listHistory(shape) or [], type="skinCluster") or []
                )
        ctrl_set = f"{self.rig_name}_controls_SET"
        if cmds.objExists(ctrl_set):
            members.add(ctrl_set)

        # Seed from the rig's DAG members, then close over DG nodes only.
        #
        # A neighbour is always RECORDED, but only a prefixed one is EXPANDED. The
        # rig names everything it creates after itself -- the premise the old name
        # sweep rested on -- so its whole DG network is prefixed and this reaches
        # all of it, while an unprefixed neighbour is a dead end rather than a
        # doorway. Without that bound the walk is only INCIDENTALLY bounded:
        # measured clean today (an animCurveTL has no explicit time input, so time1
        # is never reached), but a driven key or an expression bridges to shared
        # nodes, and from time1 the frontier is every animated node in the shot.
        head = f"{self.rig_name}_"
        frontier = [n for n in members if cmds.ls(n, type="dagNode")]
        seen = set()
        while frontier:
            node = frontier.pop()
            if node in seen:
                continue
            seen.add(node)
            for conn in cmds.listConnections(node, source=True, destination=True) or []:
                if cmds.ls(conn, type="dagNode"):
                    continue  # the boundary of the rig
                members.add(conn)
                if conn not in seen and CoreUtils.leaf_name(conn).startswith(head):
                    frontier.append(conn)

        return [n for n in members if n and cmds.objExists(n)]

    def _owned_members(self, prefix: str) -> List[str]:
        """:meth:`_member_nodes` filtered to the ``<prefix>_`` name family."""
        head = f"{prefix}_"
        return [
            n for n in self._member_nodes() if CoreUtils.leaf_name(n).startswith(head)
        ]

    @staticmethod
    def _rename_clashes(nodes: List[str], old: str, new: str) -> List[str]:
        """The target names a ``<old>_`` -> ``<new>_`` rename of *nodes* would
        collide with — where Maya would silently uniquify to ``<new>...1``:
        a DG node of that name anywhere, or a DAG SIBLING with that leaf.
        A sibling rig whose name merely extends *new* (``Base_03`` beside a
        rig becoming ``Base``) is not a clash, and cannot even reach here: the
        caller scopes by :meth:`_member_nodes`, so another rig's nodes are never
        in *nodes*. *nodes* are long paths for DAG.
        """
        clashes = []
        for n in nodes:
            leaf = CoreUtils.leaf_name(n)
            if not leaf.startswith(f"{old}_"):
                continue
            target = f"{new}{leaf[len(old) :]}"
            if "|" in n:
                parent = NodeUtils.get_parent(n, type=None, full_path=True)
                probe = f"{parent}|{target}" if parent else f"|{target}"
            else:
                probe = target
            if cmds.objExists(probe):
                clashes.append(target)
        return clashes

    def _map_recorded_paths(self, fn: Callable[[str], str]) -> None:
        """Apply *fn* to every node path this handle records (joints, IK
        handle, skinCluster, end controls, the bundle) — the addresses that
        go stale when nodes are renamed."""

        def one(v):
            return fn(v) if isinstance(v, str) else v

        def many(v):
            return [one(x) for x in v] if isinstance(v, (list, tuple)) else one(v)

        for attr in (
            "joints",
            "ik_handle",
            "pole_vector",
            "skin_cluster",
            "start_loc",
            "end_loc",
            "anchors",
            "tweak_controls",
        ):
            setattr(self, attr, many(getattr(self, attr)))
        if self.bundle:
            for field in (
                "rig_group",
                "joints",
                "ik_handle",
                "curve",
                "anchors",
                "controls",
                "tweak_controls",
            ):
                setattr(self.bundle, field, many(getattr(self.bundle, field)))

    @CoreUtils.undoable(name="Tube Rig: Rename", suspend_refresh=True)
    def rename(self, new_name: str) -> str:
        """Rename the rig: every node carrying the ``<rig>_`` prefix — group,
        joints, controls, sets, skinClusters, utility nodes — plus the scene
        record and this handle's recorded paths. Returns the name in effect.

        Raises:
            ValueError: the rig is referenced (Maya forbids renaming
                referenced nodes — rename it in its source scene), or the new
                name is already in use in the scene.
        """
        new_name = self._clean_rig_name(new_name)
        old = self.rig_name
        if not new_name or new_name == old:
            return old
        grp = self._group_path()
        if grp is None:  # nothing built yet: only the handle carries the name
            self._rig_name = new_name
            return new_name
        if cmds.referenceQuery(grp, isNodeReferenced=True):
            raise ValueError(
                f"'{old}' is referenced; rename it in its source scene instead."
            )
        # Membership first, name second: a scene-wide '<old>_*' glob renamed any
        # unrelated node sharing the stem -- an unrigged 'cable_B' mesh became
        # 'hose_B', and 'cable_rubber_MAT' became 'hose_rubber_MAT'.
        owned = self._owned_members(old) + [grp]
        clashes = self._rename_clashes(owned, old, new_name)
        if clashes:
            raise ValueError(
                f"Name '{new_name}' is already in use in this scene "
                f"({', '.join(clashes[:3])}{', ...' if len(clashes) > 3 else ''})."
            )

        # Capture UUIDs first: renaming a parent reshuffles every
        # descendant's path, and the recorded paths go stale the same way.
        recorded: Dict[str, Optional[str]] = {}

        def _snapshot(path: str) -> str:
            recorded[path] = TubeRig._uuid(path)
            return path

        self._map_recorded_paths(_snapshot)
        grp_uuid = TubeRig._uuid(grp)
        for uuid in cmds.ls(owned, uuid=True) or []:
            path = (cmds.ls(uuid, long=True) or [None])[0]
            if not path:
                continue
            leaf = CoreUtils.leaf_name(path)
            if leaf.startswith(f"{old}_"):
                cmds.rename(path, f"{new_name}{leaf[len(old) :]}")

        self._rig_name = new_name
        self._rig_group = (cmds.ls(grp_uuid, long=True) or [self._rig_group])[0]
        self._map_recorded_paths(
            lambda p: (
                (cmds.ls(recorded.get(p), long=True) or [p])[0]
                if recorded.get(p)
                else p
            )
        )
        self._stamp()
        return new_name

    # ------------------------------------------------------------------
    # Properties / Rig Infrastructure
    # ------------------------------------------------------------------

    @property
    def rig_name(self) -> str:
        """Returns the rig name."""
        if not self._rig_name:
            self._rig_name = Naming.generate_unique_name("tube_rig_0")
        return self._rig_name

    @property
    def rig_group(self) -> str:
        # A cached group reference can go stale (undo of a build, manual
        # delete) — drop it and recreate rather than returning a dead path.
        if self._rig_group and not cmds.objExists(str(self._rig_group)):
            self.logger.info(
                f"Rig group '{self._rig_group}' no longer exists; recreating."
            )
            self._rig_group = None
        if not self._rig_group:
            rig_name = f"{self.rig_name}_GRP"
            if cmds.objExists(rig_name):
                self.logger.info(f"Found rig group: {rig_name}")
                self._rig_group = cmds.ls(rig_name)[0]
            else:
                self.logger.info(f"Creating rig group: {rig_name}")
                self._rig_group = cmds.group(empty=True, name=rig_name)
                cmds.makeIdentity(self._rig_group, apply=True, t=1, r=1, s=1, n=0)
                # Beside the mesh, under its parent: the rig then rides
                # whatever animates the mesh (a module locator, a vehicle
                # root) — joints, controls and anchors alike. Built at
                # world root, a rig stays behind the moment the mesh's
                # parent moves, and an end following an anchor that DID
                # move with it double-transforms the skin (the mesh's own
                # transform re-applies the same motion). The mesh's world
                # matrix is pinned at bind (``_pin_mesh``) so only the
                # skin carries it.
                home = self._rig_home()
                if home:
                    self._rig_group = _TubeRigInternal._parent_to(self._rig_group, home)
                self._stamp(owned_group=True)  # created here: ours to delete
            # Register the group so for_node() resolves joints/controls
            # parented under it back to this rig — the step workflow (b001
            # 'Create Joints' → b002) materializes the group here without
            # ever calling build().
            grp_uuid = TubeRig._uuid(self._rig_group)
            if grp_uuid:
                TubeRig._instances[grp_uuid] = self
            self._stamp()
        return str(self._rig_group)

    @rig_group.setter
    def rig_group(self, new_group: "object"):
        """Allows setting a custom rig group."""
        if (
            new_group
            and cmds.objExists(str(new_group))
            and cmds.objectType(str(new_group), isAType="transform")
        ):
            self._rig_group = new_group
            self.logger.debug(f"Rig group set to: {self._rig_group}")
        else:
            self._rig_group = None  # Will trigger auto-create if accessed
            self.logger.debug("Rig group reset (None); will be auto-created on access.")

    @CoreUtils.undoable(name="Tube Rig: Remove", suspend_refresh=True)
    def teardown(self, progress: Callable = None) -> None:
        """Delete everything a previous ``build`` created — the rig group and
        its contents, the mesh's skinCluster, and stray ``<rig_name>_*``
        utility (DG) nodes — so the rig can rebuild cleanly. The mesh is
        handed back as found: viewport display restored, inheriting its
        parent again if the build pinned it.

        One undo step: the whole removal collapses into a single entry on
        Maya's undo queue, so one Ctrl+Z brings the rig back — bind included.

        Parameters:
            progress (Callable): Optional ``callable(current, total, message)``
                ticked at each teardown phase — see :meth:`build`.
        """
        with self._reporting(progress):
            self._report(f"Removing rig {self.rig_name}: unbinding skin…")
            self._teardown_scene()
            self._teardown_reset()

    def _teardown_scene(self) -> None:
        """Delete this rig's scene nodes — the destructive half of ``teardown``."""
        mesh = self._live_mesh()
        if mesh:
            shape = NodeUtils.get_shape(mesh)
            if shape:
                for sc in cmds.ls(cmds.listHistory(shape) or [], type="skinCluster"):
                    cmds.delete(sc)

        grp_long = self._group_path()
        # The pin record dies with the group — release it while it can be read.
        self._release_mesh_pin()

        # Utility nodes (curveInfo, multiplyDivide, blendColors, ...) are DG
        # nodes outside the group; all are prefixed with the rig name. Delete
        # them before the group so e.g. curveInfo doesn't evaluate against an
        # already-deleted curve.
        #
        # Scoped by MEMBERSHIP, then filtered by name -- not found by name. A
        # scene-wide '<rig>_*' sweep also matches DG nodes that merely share the
        # stem, and a material named 'cable_rubber_MAT' beside a rig called
        # 'cable' is a DG node, so tearing the rig down deleted the shader.
        self._report(f"Removing rig {self.rig_name}: deleting utility nodes…")
        mine = [
            n
            for n in self._owned_members(self.rig_name)
            if not cmds.ls(n, type="dagNode")
        ]
        if mine:
            # One command for the whole set: Maya resolves the inter-node
            # dependencies itself (a dagPose going with its skinCluster, ...).
            cmds.delete(mine)

        self._report(f"Removing rig {self.rig_name}: deleting joints and controls…")
        if grp_long and cmds.objExists(grp_long):
            if self._owns_group(grp_long):
                cmds.delete(grp_long)
            else:
                # A caller-supplied group is theirs: hand it back empty and
                # without the record; the next build goes into it again.
                children = (
                    cmds.listRelatives(grp_long, children=True, fullPath=True) or []
                )
                if children:
                    cmds.delete(children)
                if cmds.attributeQuery(self.DATA_ATTR, node=grp_long, exists=True):
                    cmds.deleteAttr(f"{grp_long}.{self.DATA_ATTR}")

        # Restore the mesh's viewport display AFTER the group delete: the
        # settings control's meshDisplay connection dies with the group, so
        # the override attrs are writable again here.
        self._set_mesh_display_locked(False)

        # A kept (caller-supplied) group stays the rig's home for a rebuild.
        self._rig_group = grp_long if grp_long and cmds.objExists(grp_long) else None

    def _teardown_reset(self) -> None:
        """Drop this handle's cached rig members — the bookkeeping half of
        ``teardown``, split from the scene half only to keep either readable."""
        self.joints = None
        self.ik_handle = None
        self.pole_vector = None
        self.skin_cluster = None
        self.start_loc = None
        self.end_loc = None
        self.anchors = None
        self.tweak_controls = None
        self.bundle = None

    @CoreUtils.undoable(name="Tube Rig: Build", suspend_refresh=True)
    def build(self, strategy: str = "spline", progress: Callable = None, **kwargs):
        """Builds the rig using the specified strategy.

        Rebuilding on an already-rigged mesh tears the previous build down
        first (joint names would collide and the re-bind would fail).

        One undo step: the whole build (teardown of a previous rig included)
        collapses into a single entry on Maya's undo queue, so one Ctrl+Z
        reverts it whether it was run from the UI or from a script.

        Args:
            strategy (str): The rigging strategy to use — a key of
                ``STRATEGIES`` ("spline", "anchor", "fk").
            progress (Callable): Optional ``callable(current, total, message)``
                ticked at each build phase — the viewport is held still for the
                operation, so this is what tells the user it is working rather
                than hung. Wire a footer with ``sb.progress_adapter(update)``.
            **kwargs: Additional arguments for the build process.
        """
        strategy_cls = self.STRATEGIES.get(strategy)
        if strategy_cls is None:
            raise NotImplementedError(
                f"Strategy '{strategy}' not implemented. "
                f"Available: {', '.join(sorted(self.STRATEGIES))}."
            )
        strat = strategy_cls()

        with self._reporting(progress):
            existing_grp = self._rig_group or f"{self.rig_name}_GRP"
            # A group carrying a rig record held a previous build (or was created
            # for one); a caller-supplied group without one is simply built into.
            if self.bundle or (
                cmds.objExists(str(existing_grp)) and self.scene_data(existing_grp)
            ):
                self._report(f"Rebuilding {self.rig_name}: removing previous rig…")
                self.teardown()

            self.bundle = strat.build(self, **kwargs)
            # Scene record: the strategy and every plain-valued option, so a
            # later session can rebuild with the same settings (``edges`` is a
            # transient selection and is not recorded).
            self._stamp(
                strategy=strategy,
                **{
                    k: v
                    for k, v in kwargs.items()
                    if isinstance(v, (bool, int, float, str))
                },
            )

            # Populate legacy attributes for backward compatibility
            self.joints = self.bundle.joints

            # Bundle might have ik_handle or controls
            if self.bundle.ik_handle:
                self.ik_handle = self.bundle.ik_handle
            if self.bundle.anchors:
                self.anchors = self.bundle.anchors

            # Add controls to legacy attributes if supported in future or just use bundle
            # However, to be nice to consumers:
            if self.bundle.controls:
                # Just expose the main start/end controls
                self.start_loc = self.bundle.controls[0]
                self.end_loc = self.bundle.controls[-1]

        return self

    # ------------------------------------------------------------------
    # Measurement (shared by strategies and the step-by-step UI)
    # ------------------------------------------------------------------

    def _end_directions(self, centerline: List) -> Tuple["om.MVector", "om.MVector"]:
        """Frame axes for the tube's two ends.

        Prefers the END FACES' own normals (``TubePath.get_end_normals``) so
        an end control lands square to the opening it represents — an
        angle-cut hose end differs from the centerline's last chord by the
        cut angle, and a chord-built control sits visibly skew to the cap.
        Falls back to the local path tangents when the mesh yields no rings
        (sampled centerlines, rigs built from a bare joint chain).
        """
        try:
            start_n, end_n = TubePath.get_end_normals(
                self.mesh, rings=self._cross_sections()
            )
        except Exception as e:
            self.logger.debug(f"End-normal lookup failed ({e}); using path tangents.")
            start_n = end_n = None
        tan_start, tan_end = _TubeRigInternal._path_end_directions(centerline)
        return (start_n or tan_start), (end_n or tan_end)

    def resolve_centerline(
        self, num_joints: int = -1, edges: list = None
    ) -> Tuple[List, int]:
        """Extract this rig's tube centerline.

        Single source for the extraction parameters so the step-by-step UI
        and the one-click strategies stay in lock-step.

        Parameters:
            num_joints: Requested joint count; ``-1`` = one per edge loop.
            edges: Optional user-selected edges to derive the path from.

        Returns:
            Tuple of (centerline_points, resolved_num_joints).
        """
        return TubePath.get_centerline(
            self.mesh,
            num_joints=num_joints,
            precision=50,
            edges=edges,
            rings=None if edges else self._cross_sections(),
        )

    def _cross_sections(self) -> List[List[int]]:
        """This rig's mesh cross-section rings, extracted once per topology.

        Three build steps read the rings — centerline, end frames, skin
        stations — and each re-ran the ``polySelect`` loop walk, the one
        extraction cost that scales with mesh density. Keyed on the shape
        and its vertex/edge/face counts so a re-modelled tube re-extracts.
        Empty without a resolvable mesh (a rig built from a bare joint).
        """
        shape = TubePath._resolve_mesh_shape(self.mesh)
        if not shape:
            return []
        fn = TubePath._mesh_fn(shape)
        key = (str(shape), fn.numVertices, fn.numEdges, fn.numPolygons)
        if self._rings_cache is None or self._rings_cache[0] != key:
            self._rings_cache = (key, TubePath.get_vertex_rings(shape))
        return self._rings_cache[1]

    def estimate_tube_radius(self, centerline: List = None) -> Optional[float]:
        """Measure the tube's radius from the mesh surface.

        Uses the given centerline when provided, else the current joint
        positions, else a fresh centerline extraction. Returns None when the
        rig has no resolvable mesh (e.g. constructed from a bare joint).
        """
        shape = TubePath._resolve_mesh_shape(self.mesh)
        if not shape:
            return None
        pts = list(centerline) if centerline else None
        if not pts:
            joints = [str(j) for j in (self.joints or []) if cmds.objExists(str(j))]
            if len(joints) >= 2:
                pts = [_TubeRigInternal._xform_t_ws(j) for j in joints]
        if not pts:
            try:
                pts, _ = TubePath.get_centerline(shape, num_joints=-1)
            except ValueError:
                return None
        return TubePath.estimate_radius(shape, pts)

    def resolve_sizes(
        self, centerline: List = None, joint_radius: float = -1.0
    ) -> Tuple[float, float]:
        """Resolve (joint_display_radius, control_base_size) from the tube.

        The control base size is the measured tube radius — every control
        multiplier is tuned against it, so rigs stay proportional to the mesh
        regardless of scene scale. ``joint_radius`` <= 0 means Auto (half the
        tube radius). Without a resolvable mesh (step mode from a bare joint
        selection) the tube radius falls back to the joints' own display
        radii, which Auto-sized joints carry at half tube radius.
        """
        tube_radius = self.estimate_tube_radius(centerline)
        if not tube_radius:
            radii = [
                cmds.getAttr(f"{j}.radius")
                for j in (self.joints or [])
                if cmds.objExists(str(j))
            ]
            tube_radius = max(radii) * 2.0 if radii else 1.0
        if joint_radius is None or joint_radius <= 0:
            joint_radius = tube_radius * 0.5
        return joint_radius, tube_radius

    # ------------------------------------------------------------------
    # Joint Creation
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def generate_joint_chain(
        self,
        centerline: List[List[float]],
        num_joints: int,
        reverse: bool = False,
        **kwargs,
    ) -> List[str]:
        """
        Generates joints along the tube's centerline.

        Parameters:
            centerline: List of points defining the tube's path.
            num_joints: Number of joints to create. If -1, creates one joint per
                centerline point (useful when centerline is from edge loops).
            reverse: If True, reverses the joint chain direction.

        Keyword Args:
            radius (float): Joint display radius.
            orientation (List[float]): Explicit jointOrient values for every
                joint. Default ``None`` auto-orients the chain (X aims at the
                child, Y up; the end joint's orient is zeroed).

        Any previous joints matching this rig's ``<rig_name>_jnt_*`` prefix
        are deleted first — stale chains anywhere in the scene otherwise make
        the short names ambiguous or collide on parenting.
        """
        radius: float = kwargs.pop("radius", 1.0)
        orientation: Optional[List[float]] = kwargs.pop("orientation", None)

        # Sweep leftover chains sharing this rig's joint prefix (reruns of
        # "Create Joints", debris from crashed builds, undo remnants) — plus
        # the tweak layer's proxy chain and tweak nodes, which a Step-1
        # rerun would otherwise strand against the deleted bind chain.
        # (Naming rule: the layer uses `proxy_jnt_`/`tweak_` prefixes, never
        # `jnt_proxy_`, so this `jnt_*` pattern can't eat it by accident.)
        stale = cmds.ls(f"{self.rig_name}_jnt_*", type="joint", long=True) or []
        stale += (
            cmds.ls(f"{self.rig_name}_proxy_*", f"{self.rig_name}_tweak_*", long=True)
            or []
        )
        if stale:
            self.logger.info(
                f"Replacing {len(stale)} existing '{self.rig_name}' joint/layer node(s)."
            )
            for n in stale:
                if cmds.objExists(n):
                    cmds.delete(n)

        if num_joints == -1:
            # Use centerline points directly as joint positions
            joint_positions = list(centerline)
            if reverse:
                joint_positions = joint_positions[::-1]
        else:
            # Arc-uniform: "N evenly spaced joints" means evenly spaced along
            # the TUBE, which index-space resampling only matches when the
            # centerline's own points happen to be evenly spaced.
            joint_positions = ptk.Polyline.resample(
                centerline,
                num_joints,
                reverse,
                interpolation=ptk.Polyline.point_at_arc,
            )
        joints = []
        parent_joint = None

        for i, pos in enumerate(joint_positions):
            self.logger.debug(
                f"Generating joint {i + 1}, position: {pos}, radius: {radius}"
            )
            # Always clear selection before joint creation to avoid Maya's implicit parenting
            cmds.select(clear=True)
            jnt = cmds.createNode(
                "joint",
                name=f"{self.rig_name}_jnt_{i + 1}",
            )
            # ``pos`` may be ``om.MPoint``; cmds.xform's ``t=`` flag wants
            # a 3-tuple of plain floats.
            t_xyz = (float(pos[0]), float(pos[1]), float(pos[2]))
            cmds.xform(jnt, ws=True, t=t_xyz)
            cmds.setAttr(f"{jnt}.radius", radius)
            if orientation:
                cmds.setAttr(f"{jnt}.jointOrient", *orientation, type="double3")
            # Parent — track the long path _parent_to returns so later ops
            # can't hit ambiguous short names (parenting renames on clash).
            jnt = _TubeRigInternal._parent_to(
                jnt, self.rig_group if i == 0 else parent_joint
            )
            parent_joint = jnt
            joints.append(jnt)

        # Default orientation: X aims down the chain, Y up; the end joint has
        # no child to aim at, so its orient is zeroed.
        if orientation is None and len(joints) > 1:
            cmds.joint(joints[0], e=True, oj="xyz", sao="yup", ch=True, zso=True)
            cmds.setAttr(f"{joints[-1]}.jointOrient", 0, 0, 0, type="double3")

        self.logger.debug(
            f"Generated joints: {[CoreUtils.leaf_name(j) for j in joints]}"
        )
        self.joints = joints
        # Direct, not @add_to_isolation: get_transform_node resolves a joint to
        # its PARENT, so the decorator would silently drop the chain's last
        # joint and add the group above the first.
        DisplayUtils.add_to_isolation_set(joints)
        return joints

    @CoreUtils.undoable
    def create_anchor_joints(self, centerline: List, radius: float = 1.0) -> List[str]:
        """Create the anchor rig's two end joints from the tube centerline.

        Both joints are oriented X-down-the-tube (baked into jointOrient) —
        the distance-driven stretch scales ``scaleX``, which must run along
        the tube, not world X. They are *siblings* under a joint group, not a
        chain: the start joint's stretch scale must not propagate to the end
        joint, which stays pinned to its anchor.

        Any previous ``<rig_name>_start_jnt`` / ``<rig_name>_end_jnt`` joints
        (and their group) are replaced, mirroring ``generate_joint_chain``'s
        rerun semantics.
        """
        if not centerline or len(centerline) < 2:
            raise ValueError("Anchor joints need a centerline with at least 2 points.")

        start_pos = om.MVector(centerline[0])
        end_pos = om.MVector(centerline[-1])
        dir_start, dir_end = self._end_directions(centerline)

        stale = (
            cmds.ls(
                f"{self.rig_name}_start_jnt*",
                f"{self.rig_name}_end_jnt*",
                type="joint",
                long=True,
            )
            or []
        )
        stale += (
            cmds.ls(f"{self.rig_name}_joints_GRP*", type="transform", long=True) or []
        )
        if stale:
            self.logger.info(
                f"Replacing {len(stale)} existing '{self.rig_name}' anchor node(s)."
            )
            for n in stale:
                if cmds.objExists(n):
                    cmds.delete(n)

        # Joint group (separate from controls for clean export)
        joint_grp = cmds.group(empty=True, name=f"{self.rig_name}_joints_GRP")
        joint_grp = _TubeRigInternal._parent_to(joint_grp, str(self.rig_group))

        def _make_anchor_joint(suffix, pos, x_dir):
            cmds.select(clear=True)
            jnt = cmds.createNode("joint", name=f"{self.rig_name}_{suffix}_jnt")
            cmds.xform(jnt, ws=True, t=(pos.x, pos.y, pos.z))
            cmds.xform(
                jnt,
                ws=True,
                ro=_TubeRigInternal._euler_deg(_TubeRigInternal._frame_rotation(x_dir)),
            )
            cmds.makeIdentity(jnt, apply=True, r=True)  # bake into jointOrient
            jnt = _TubeRigInternal._parent_to(jnt, joint_grp)
            cmds.setAttr(f"{jnt}.radius", radius)
            return jnt

        j1 = _make_anchor_joint("start", start_pos, dir_start)
        j2 = _make_anchor_joint("end", end_pos, dir_end)

        self.joints = [j1, j2]
        # Direct, for the same reason as generate_joint_chain: the decorator
        # resolves both joints to their shared group and adds neither.
        DisplayUtils.add_to_isolation_set([j1, j2])
        return [j1, j2]

    # ------------------------------------------------------------------
    # Curves & IK
    # ------------------------------------------------------------------

    def skin_mesh(
        self,
        joints: List[str],
        curve: Optional[str] = None,
        centerline: Optional[List] = None,
        skinning_method: str = "dqs",
        mesh: Optional[str] = None,
    ) -> Optional[str]:
        """Smooth-bind the mesh to *joints* and record the skinCluster
        (``constrain_end_with_falloff`` and re-builds depend on it).

        With a *curve* or *centerline* (every strategy has one in hand at skin
        time), weights are solved analytically along it — ring-uniform, a
        C2-smooth cubic basis spanning up to 4 joints so bends spread
        smoothly instead of hinging at each joint — via
        ``SkinUtils.bind_to_curve``; if that solve fails (e.g. joints that
        don't order along the path), a geodesic-voxel bind is used instead.
        Both default to dual quaternion skinning for volume-preserving bends
        and twist.

        An existing skinCluster on the mesh is replaced (re-running the bind
        step re-binds, mirroring ``generate_joint_chain``'s rerun semantics).
        ``mesh`` defaults to the rig's own mesh.

        The mesh's world matrix is pinned first (``_pin_mesh``): the rig
        rides the mesh's parent, so the skin alone must carry the mesh —
        a mesh whose transform also inherits that motion is transformed
        twice. ``teardown`` restores inheritance.
        """
        joints = [str(j) for j in joints]
        mesh = str(mesh) if mesh else str(self.mesh)
        name = f"{self.rig_name}_skinCluster"

        existing = SkinUtils.get_skin_cluster(mesh)
        if existing:
            self.logger.info(
                f"Replacing existing skinCluster '{existing}' on {CoreUtils.leaf_name(mesh)}."
            )
            # unbind (not delete): a bare delete leaves orphan bindPose/
            # dagPose nodes and joint lockInfluenceWeights attrs behind —
            # re-running Bind Skin N times accumulated N orphan dagPoses.
            SkinUtils.unbind(mesh)

        self._pin_mesh(mesh)

        if curve or centerline:
            # Topological cross-sections make ring uniformity exact. Without
            # them the solve stations each vertex by its own projection, and
            # on a bend the inside of a ring lands short of the outside — one
            # cross-section then carries a spread of weights (9-18% on tight
            # bends) and shears under pose. Rings are an ENHANCEMENT to the
            # parametric solve, not a precondition: irregular topology (or a
            # traversal that trips on it) degrades to per-vertex projection
            # rather than costing the mesh its parametric weights entirely.
            try:
                own = TubePath._resolve_mesh_shape(
                    mesh
                ) == TubePath._resolve_mesh_shape(self.mesh)
                rings = (
                    self._cross_sections() if own else TubePath.get_vertex_rings(mesh)
                ) or None
            except Exception as e:
                self.logger.debug(
                    f"Cross-section extraction failed ({e}); weighting per-vertex."
                )
                rings = None
            try:
                self.skin_cluster = SkinUtils.bind_to_curve(
                    mesh,
                    joints,
                    curve=str(curve) if curve else None,
                    centerline=centerline,
                    profile="smoothstep",
                    skinning_method=skinning_method,
                    rings=rings,
                    name=name,
                )
                self._set_mesh_display_locked(True)
                return self.skin_cluster
            except Exception as e:
                # bind_to_curve solves before binding and rolls back its own
                # cluster if the weight write fails, so the mesh is always
                # unbound here and the fallback bind can't hit 'already bound'.
                self.logger.warning(
                    f"Parametric skinning failed ({e}); falling back to geodesic bind."
                )
        try:
            self.skin_cluster = SkinUtils.bind(
                mesh,
                joints,
                bind_method="geodesic",
                skinning_method=skinning_method,
                max_influences=4,
                name=name,
            )
        except Exception as e:
            self.logger.warning(f"Failed to skin mesh: {e}")
            self._release_mesh_pin()
            return None
        self._set_mesh_display_locked(True)
        return self.skin_cluster

    @CoreUtils.undoable(name="Tube Rig: Rebind Skin", suspend_refresh=True)
    def rebind_skin(
        self, skinning_method: str = "dqs", mesh: Optional[str] = None
    ) -> str:
        """Re-solve this rig's mesh bind from what is already in the scene.

        Recovery for a rig whose skinCluster was destroyed on its own: a UV
        round-trip, *Delete History*, or *Bake Non-Deformer History* run on
        the tube removes the bind and leaves the joints, controls, IK and rig
        group untouched, so nothing looks missing — the tube just stops
        following (and stays display-locked, so it also can't be picked).

        Nothing has to have been saved. ``skin_mesh`` solves weights
        analytically along the centerline, so re-running it against the same
        joints reproduces the original bind exactly. Re-running on a healthy
        rig re-binds rather than stacking a second cluster.

        Does NOT need a teardown: joints, controls and their animation are
        left alone, which is the whole point of having this beside
        ``build`` (which tears the rig down first).

        Note:
            A bind destroyed while the rig was POSED cannot be fully undone
            here. Maya's Delete History bakes the deformed shape into the
            mesh, so the modeled rest shape is gone before this runs; the
            rebind is then correct for the geometry that survived, not for
            the shape originally modeled. Rebind at the rig's default pose.

        Parameters:
            skinning_method (str): Passed through to ``skin_mesh`` ("dqs",
                "linear", "blended").
            mesh (str): Bind THIS mesh instead of the rig's recorded one. The
                rescue path for a rig built before ``DATA_ATTR`` existed: those
                are reachable from the mesh only through the skinCluster's
                first influence, so once the bind is gone the tube and its rig
                can no longer find each other and the pairing has to come from
                the caller (the UI takes it from the selection). A successful
                rebind stamps the scene record, so this is needed once.

        Returns:
            str: The new skinCluster.

        Raises:
            ValueError: The rig has no resolvable mesh, or fewer than two
                joints to bind to.
            RuntimeError: The bind itself failed (see the Script Editor).
        """
        if mesh is not None:
            candidate = str(mesh)
            if not TubePath._resolve_mesh_shape(candidate):
                raise ValueError(
                    f"'{CoreUtils.leaf_name(candidate)}' is not a polygon mesh."
                )
            self.mesh = candidate
            self._mesh_uuid = TubeRig._uuid(candidate)
            if self._mesh_uuid:
                TubeRig._instances[self._mesh_uuid] = self

        mesh = str(self.mesh) if self.mesh else ""
        if not mesh or not TubePath._resolve_mesh_shape(mesh):
            raise ValueError(
                f"Rig '{self.rig_name}' has no bindable mesh "
                f"({CoreUtils.leaf_name(mesh) if mesh else 'none'}) — the tube was "
                "deleted, or the rig predates the scene record and the bind that "
                "linked them is gone. Select the tube along with the rig, or "
                "rebuild the rig instead."
            )

        joints = [str(j) for j in (self.joints or []) if cmds.objExists(str(j))]
        if len(joints) < 2:
            raise ValueError(
                f"Rig '{self.rig_name}' has no joint chain to bind to "
                f"({len(joints)} found). Rebuild the rig instead."
            )

        # The logic curve every spline build makes is the exact path the
        # original weights were solved against, so it is preferred over
        # re-measuring the mesh (which a posed/edited tube would answer
        # differently). Anchor / FK rigs have none — fall back to the
        # measured centerline.
        curve = f"{self.rig_name}_ik_curve"
        curve = curve if cmds.objExists(curve) else None
        centerline = None
        if not curve:
            try:
                centerline, _ = self.resolve_centerline()
            except Exception as e:
                self.logger.debug(f"Centerline re-measure failed ({e}); geodesic bind.")

        skin_cluster = self.skin_mesh(
            joints,
            curve=curve,
            centerline=centerline,
            skinning_method=skinning_method,
        )
        if not skin_cluster:
            raise RuntimeError(
                f"Rebind failed for '{self.rig_name}' — see the Script Editor."
            )
        # Upgrade a legacy rig on the way out: stamping the record now means
        # the NEXT time this tube's bind is destroyed it resolves back to its
        # rig on its own, so the explicit pairing is a one-time cost.
        self._stamp()
        self.logger.info(
            f"Rebound {CoreUtils.leaf_name(mesh)} to {len(joints)} joints "
            f"({CoreUtils.leaf_name(skin_cluster)})."
        )
        return skin_cluster

    @CoreUtils.undoable
    def create_logic_curve(self, centerline: List[List[float]]) -> str:
        """Creates the logic curve for Spline IK."""
        degree = 3 if len(centerline) >= 4 else 1
        curve_name = f"{self.rig_name}_ik_curve"
        # ``centerline`` may contain ``om.MPoint`` instances; cmds.curve
        # wants flat (x, y, z) tuples. Edit points (not CVs) — the curve must
        # pass through the centerline or spline IK drags joints off-centre.
        points = [(float(p[0]), float(p[1]), float(p[2])) for p in centerline]
        curve = cmds.curve(ep=points, d=degree, name=curve_name)
        # Under the rig group for tidiness only. ``relative`` keeps the
        # transform at identity and inheritsTransform off keeps it there, so
        # the curve stays a WORLD-space object: its CVs are world coordinates
        # driven by the driver joints' world matrices through its own skin.
        # (A world-preserving parent compensates the group's transform into
        # the curve, and switching inheritance off then leaves that in —
        # the curve jumps by the group's inverse world matrix once the rig
        # group lives inside a module rather than at the root.)
        curve = cmds.parent(curve, str(self.rig_group), relative=True)[0]
        cmds.setAttr(f"{curve}.inheritsTransform", False)
        cmds.setAttr(f"{curve}.visibility", False)
        return curve

    # ------------------------------------------------------------------
    # Spline IK Driver System (controls, tangents, up locators)
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def create_spline_drivers(
        self, centerline: List[List[float]], radius: float = 1.0, num_controls: int = 3
    ) -> Tuple[List[str], List[str], List]:
        """Creates the driver system (controls and joints) for the Spline IK curve.

        Control positions are distributed along the centerline itself (not
        its chord), so bent tubes get on-path controls; end frames come from
        the local path tangents.
        """
        # Every offset here scales with the tube's extent ALONG THE PATH, not
        # the start-end chord: a curled tube's chord badly under-measures it
        # (and vanishes on a full loop), which bunched the driver stations
        # toward the ends and cost the weight basis its even support.
        path = [(float(p[0]), float(p[1]), float(p[2])) for p in centerline]
        start_pos = om.MVector(centerline[0])
        end_pos = om.MVector(centerline[-1])
        arc_length = ptk.Polyline.length(path)

        dir_start, dir_end = self._end_directions(centerline)
        start_rot = _TubeRigInternal._frame_rotation(dir_start)
        end_rot = _TubeRigInternal._frame_rotation(
            -dir_end
        )  # X points back into the tube

        rig_grp = str(self.rig_group)

        def _create_ctrl(
            name, pos, rot=None, scale=1.0, color=(1, 1, 0), shape="box", parent=None
        ):
            # ``Controls.create`` accepts ``size=`` for the uniform scale.
            # ``scale=``/``radius=`` are absorbed by preset-builder ``**_`` and silently no-op.
            if shape == "sphere":
                nodes = Controls.sphere(
                    name=name, size=scale, color=color, return_nodes=True
                )
            else:
                nodes = Controls.box(
                    name=name, size=scale, color=color, return_nodes=True
                )

            grp = nodes.group if nodes.group else nodes.control
            _TubeRigInternal._set_t_ws(grp, (pos[0], pos[1], pos[2]))
            if rot is not None:
                _TubeRigInternal._set_r_ws(grp, _TubeRigInternal._euler_deg(rot))

            grp = _TubeRigInternal._parent_to(grp, parent if parent else rig_grp)
            return _TubeRigInternal._control_path(nodes, grp)

        driver_grp = cmds.group(empty=True, name=f"{self.rig_name}_driver_GRP")
        driver_grp = _TubeRigInternal._parent_to(driver_grp, rig_grp)
        cmds.setAttr(f"{driver_grp}.visibility", False)

        controls: List[str] = []
        driver_joints: List[str] = []

        if num_controls == 3:
            # ------------------------------------------------------------------
            # Standard 3-Point System (Start, Mid, End + Tangents)
            # ------------------------------------------------------------------
            mid_pos = ptk.Polyline.point_at_arc(path, 0.5)  # on-path arc midpoint
            start_ctrl = _create_ctrl(
                f"{self.rig_name}_start", start_pos, start_rot, radius * 3
            )
            mid_ctrl = _create_ctrl(f"{self.rig_name}_mid", mid_pos, None, radius * 2.5)
            end_ctrl = _create_ctrl(
                f"{self.rig_name}_end", end_pos, end_rot, radius * 3
            )

            controls = [start_ctrl, mid_ctrl, end_ctrl]

            # Tangent Controls — ON the centerline at 20% arc from each end.
            # (Offsetting along the straight tangent RAY instead put them off
            # the tube entirely once it curved, so their driver joints
            # stationed at ~11%/89% rather than 20%/80% and the cubic basis
            # lost even support across the middle.)
            start_tan_pos = om.MVector(*ptk.Polyline.point_at_arc(path, 0.2))
            start_tan_ctrl = _create_ctrl(
                f"{self.rig_name}_start_tan",
                start_tan_pos,
                rot=start_rot,
                scale=radius * 0.5,
                color=(1, 0.5, 0),
                shape="sphere",
                parent=start_ctrl,
            )

            end_tan_pos = om.MVector(*ptk.Polyline.point_at_arc(path, 0.8))
            end_tan_ctrl = _create_ctrl(
                f"{self.rig_name}_end_tan",
                end_tan_pos,
                rot=end_rot,
                scale=radius * 0.5,
                color=(1, 0.5, 0),
                shape="sphere",
                parent=end_ctrl,
            )

            driver_sources = [
                (start_ctrl, "start"),
                (start_tan_ctrl, "start_tan"),
                (mid_ctrl, "mid"),
                (end_tan_ctrl, "end_tan"),
                (end_ctrl, "end"),
            ]

            for source, suffix in driver_sources:
                cmds.select(clear=True)
                jnt = cmds.createNode(
                    "joint", name=f"{self.rig_name}_driver_{suffix}_jnt"
                )
                _TubeRigInternal._set_t_ws(jnt, _TubeRigInternal._xform_t_ws(source))
                jnt = _TubeRigInternal._parent_to(jnt, driver_grp)
                cmds.setAttr(f"{jnt}.radius", radius * 1.5)
                cmds.parentConstraint(str(source), jnt, mo=True)
                driver_joints.append(jnt)

        else:
            # ------------------------------------------------------------------
            # Distributed N-Point System
            # ------------------------------------------------------------------
            positions = ptk.Polyline.resample(
                path, num_controls, interpolation=ptk.Polyline.point_at_arc
            )

            for i, pos in enumerate(positions):
                name = f"{self.rig_name}_ctrl_{i + 1}"

                rot = None
                if i == 0:
                    rot = start_rot
                elif i == len(positions) - 1:
                    rot = end_rot

                ctrl = _create_ctrl(
                    name,
                    pos,
                    rot=rot,
                    scale=radius * 2.5,
                    color=(1, 1, 0),
                    shape="box",
                )
                controls.append(ctrl)

                cmds.select(clear=True)
                jnt = cmds.createNode(
                    "joint", name=f"{self.rig_name}_driver_{i + 1}_jnt"
                )
                _TubeRigInternal._set_t_ws(jnt, _TubeRigInternal._xform_t_ws(ctrl))
                jnt = _TubeRigInternal._parent_to(jnt, driver_grp)
                cmds.setAttr(f"{jnt}.radius", radius * 1.5)
                cmds.parentConstraint(str(ctrl), jnt, mo=True)
                driver_joints.append(jnt)

        # Natural drape: intermediate controls ride between the end controls
        # via a follow group above their offset group — point-constrained by
        # arc position, offset maintained. Carrying or posing the ends then
        # carries the whole hose (without this, the mid control stays nailed
        # to its build position and the tube body lags its end constraints).
        # The controls themselves stay free for hand-animated offsets on top.
        for i, ctrl in enumerate(controls[1:-1], start=1):
            t = i / (len(controls) - 1)
            offset_grp = NodeUtils.get_parent(
                str(ctrl), type=None, full_path=True
            ) or str(ctrl)
            follow = cmds.group(empty=True, name=f"{self.rig_name}_follow_{i}_GRP")
            cmds.delete(cmds.parentConstraint(offset_grp, follow))
            parent = NodeUtils.get_parent(offset_grp, type=None, full_path=True)
            if parent:
                follow = _TubeRigInternal._parent_to(follow, parent)
            offset_grp = _TubeRigInternal._parent_to(offset_grp, follow)
            # Moving the offset group changed the control's DAG path — refresh
            # the list entry (downstream consumers: auto-bend, the bundle,
            # the end-constraint resolver) to the canonical path.
            moved = f"{offset_grp}|{CoreUtils.leaf_name(ctrl)}"
            controls[i] = (cmds.ls(moved, long=True) or [moved])[0]
            cmds.pointConstraint(str(controls[0]), follow, mo=True, weight=1.0 - t)
            cmds.pointConstraint(str(controls[-1]), follow, mo=True, weight=t)

        # Up Locators (Start/End Twist Anchors). Parented under the controls —
        # the IK handle's "Object Rotation Up" twist reads the locators'
        # *rotation*, so they must inherit it from the controls (a
        # point-constrained locator never rotates and the twist goes dead).
        up_offset = arc_length * 0.1

        s_pos = _TubeRigInternal._xform_t_ws(controls[0])
        e_pos = _TubeRigInternal._xform_t_ws(controls[-1])
        # Offset along an axis that is not the run: world +Y is degenerate on a
        # vertical tube, and both locators must share one axis or the solve is
        # twisted before the rig is even posed. See :meth:`_twist_up_axis`.
        up_axis = _TubeRigInternal._twist_up_axis(
            [_TubeRigInternal._xform_t_ws(c) for c in controls]
        )

        start_up_loc = cmds.spaceLocator(name=f"{self.rig_name}_start_up_loc")[0]
        start_up_loc = _TubeRigInternal._parent_to(start_up_loc, controls[0])
        _TubeRigInternal._set_t_ws(
            start_up_loc, tuple(p + a * up_offset for p, a in zip(s_pos, up_axis))
        )
        cmds.setAttr(f"{start_up_loc}.visibility", False)

        end_up_loc = cmds.spaceLocator(name=f"{self.rig_name}_end_up_loc")[0]
        end_up_loc = _TubeRigInternal._parent_to(end_up_loc, controls[-1])
        _TubeRigInternal._set_t_ws(
            end_up_loc, tuple(p + a * up_offset for p, a in zip(e_pos, up_axis))
        )
        cmds.setAttr(f"{end_up_loc}.visibility", False)

        # Handoff contract: channel policy, pick-walk chains (main run
        # start->mid->end; each tangent hangs off its end), selection set.
        if num_controls == 3:
            self._finalize_controls(
                controls + [start_tan_ctrl, end_tan_ctrl],
                chains=[
                    list(controls),
                    [controls[0], start_tan_ctrl],
                    [controls[-1], end_tan_ctrl],
                ],
            )
        else:
            self._finalize_controls(list(controls))

        return (controls, driver_joints, [start_up_loc, end_up_loc])

    @CoreUtils.undoable
    def skin_curve_to_drivers(self, curve, driver_joints) -> Optional[str]:
        """Bind the IK logic curve to the driver joints.

        Weights are solved along the curve's own arc length
        (``SkinUtils.bind_to_curve``, CV stations by Greville abscissa) —
        the same smooth basis the mesh bind uses, rather than Maya's
        closest-distance default. That default is the wrong tool here: the
        logic curve carries one CV per centerline edge loop, so it hands off
        between drivers within one or two CVs and picks influences by
        Euclidean distance — on a curled tube a CV can take a driver BEHIND
        it as its second influence. Posing an end control then kinks the
        curve at that handoff, and spline IK propagates the kink to every
        joint and on to the mesh. Falls back to the default bind if the solve
        fails (drivers that don't order along the curve).

        Both the skinCluster and its dagPose carry the rig prefix so
        name-based sweeps and multi-rig scenes can attribute them.
        """
        name = f"{self.rig_name}_curve_skinCluster"
        try:
            return SkinUtils.bind_to_curve(curve, driver_joints, curve=curve, name=name)
        except Exception as e:
            # bind_to_curve solves before binding and rolls back its own
            # cluster if the weight write fails, so the curve is always
            # unbound here and the fallback bind can't hit 'already bound'.
            self.logger.warning(
                f"Parametric curve skinning failed ({e}); falling back to default bind."
            )
        try:
            sc = cmds.skinCluster(
                driver_joints,
                str(curve),
                toSelectedBones=True,
                name=name,
            )[0]
        except Exception as e:
            self.logger.warning(f"Failed to skin curve: {e}")
            return None
        SkinUtils.name_bind_pose(sc, f"{self.rig_name}_curve_pose")
        return sc

    # ------------------------------------------------------------------
    # Animator-handoff layer (channel policy, pick-walk, sets, settings)
    # ------------------------------------------------------------------

    def _register_in_control_set(self, controls: List[str]) -> None:
        """Add *controls* to this rig's selection set (created on first use).

        The set is a DG node carrying the rig prefix, so ``teardown``'s
        existing stray sweep reclaims it; set membership tracks the NODE and
        survives the reparenting later build steps do.
        """
        controls = [str(c) for c in controls if c and cmds.objExists(str(c))]
        if not controls:
            return
        set_name = f"{self.rig_name}_controls_SET"
        if cmds.objExists(set_name):
            cmds.sets(controls, add=set_name)
        else:
            cmds.sets(controls, name=set_name)

    def _finalize_controls(
        self, controls: List[str], chains: Optional[List[List[str]]] = None
    ) -> None:
        """Apply the animator-handoff contract to freshly built controls:
        channel policy, pick-walk tag chaining, and selection-set membership.

        Called LAST by every control builder — never before a feature that
        drives a control channel is wired (channel policy leaves T/R keyable
        precisely so later constraints like ``constrain_end_with_falloff``
        keep working).

        Parameters:
            controls: Every control this builder made.
            chains: Pick-walk orders (default: one chain in list order). A
                builder with side branches passes several — e.g. the 3-point
                spline layout walks start->mid->end with each tangent hanging
                off its end control.
        """
        controls = [str(c) for c in controls if c and cmds.objExists(str(c))]
        for ctrl in controls:
            Controls.set_channel_state(ctrl, **self.CONTROL_CHANNEL_POLICY)
        for chain in chains if chains is not None else [controls]:
            _TubeRigInternal._chain_controller_tags([str(c) for c in chain])
        self._register_in_control_set(controls)

    @CoreUtils.undoable
    def create_settings_control(self, size: float = 1.0) -> str:
        """Build the rig's settings control: the single place an animator
        finds every rig-level switch.

        Carries PROXY attributes mirroring whatever masters this rig
        actually built (``stretchFactor``/``volumeFactor``/``autoBend`` on
        the start control, ``roll`` on the end control) — proxies are safe
        here because the masters are user attrs FEEDING the network (source
        plugs); keys on either side land on the master and stay in sync.
        Real (non-proxy) attributes cover what has no master elsewhere:
        ``controlsVis``, ``jointsVis``, and a ``meshDisplay`` enum
        (normal/template/reference — values match Maya's
        ``overrideDisplayType`` 0/1/2, so it wires straight through).

        The control itself is not animatable: TRS locked and hidden. Rerun
        replaces a previous settings control, mirroring every other
        builder's rerun semantics.
        """
        name = f"{self.rig_name}_settings"
        stale = cmds.ls(f"{name}_CTRL*", type="transform", long=True) or []
        stale += cmds.ls(f"{name}_CTRL_GRP*", type="transform", long=True) or []
        for n in sorted(set(stale), key=len, reverse=True):
            if cmds.objExists(n):
                cmds.delete(n)

        nodes = Controls.create(
            "target",
            name=name,
            size=_TubeRigInternal._fit_control_size("target", size * 2.0),
            axis="y",
            color=(0.4, 0.9, 0.4),
            return_nodes=True,
        )
        grp = nodes.group if nodes.group else nodes.control

        # Park it just off the tube's start end so it never overlaps a
        # driver control; position lives on the OFFSET GROUP (the control's
        # own TRS gets locked below).
        anchor = None
        for candidate in (self._end_control(0), (self.joints or [None])[0]):
            if candidate and cmds.objExists(str(candidate)):
                anchor = str(candidate)
                break
        if anchor:
            pos = _TubeRigInternal._xform_t_ws(anchor)
            _TubeRigInternal._set_t_ws(grp, (pos[0], pos[1] + size * 3.0, pos[2]))
        grp = _TubeRigInternal._parent_to(grp, str(self.rig_group))
        ctrl = _TubeRigInternal._control_path(nodes, grp)

        # --- proxies for the masters this build actually made ------------
        start_ctrl, end_ctrl = self._end_control(0), self._end_control(-1)
        masters = [
            (start_ctrl, "stretchFactor"),
            (start_ctrl, "volumeFactor"),
            (start_ctrl, "autoBend"),
            (end_ctrl, "roll"),
        ]
        for node, attr in masters:
            if not node or not cmds.attributeQuery(attr, node=node, exists=True):
                continue
            if not cmds.attributeQuery(attr, node=ctrl, exists=True):
                cmds.addAttr(ctrl, longName=attr, proxy=f"{node}.{attr}")

        # --- rig-level display toggles -----------------------------------
        cmds.addAttr(
            ctrl, longName="controlsVis", attributeType="bool", defaultValue=True
        )
        cmds.setAttr(f"{ctrl}.controlsVis", channelBox=True)
        cmds.addAttr(
            ctrl, longName="jointsVis", attributeType="bool", defaultValue=True
        )
        cmds.setAttr(f"{ctrl}.jointsVis", channelBox=True)
        cmds.addAttr(
            ctrl,
            longName="meshDisplay",
            attributeType="enum",
            enumName="normal:template:reference",
            defaultValue=2,
        )
        cmds.setAttr(f"{ctrl}.meshDisplay", channelBox=True)

        # controlsVis drives every registered control's (hidden, unlocked)
        # visibility — the policy hides ``v`` but deliberately leaves it
        # connectable for exactly this.
        set_name = f"{self.rig_name}_controls_SET"
        members = (
            cmds.sets(set_name, query=True) or [] if cmds.objExists(set_name) else []
        )
        for member in members:
            if CoreUtils.leaf_name(member) == CoreUtils.leaf_name(ctrl):
                continue
            try:
                cmds.connectAttr(
                    f"{ctrl}.controlsVis", f"{member}.visibility", force=True
                )
            except RuntimeError:
                pass

        # jointsVis: drive the joint roots (chains inherit visibility).
        joint_roots = []
        grp_candidate = f"{self.rig_name}_joints_GRP"
        if cmds.objExists(grp_candidate):
            joint_roots = [grp_candidate]
        elif self.joints:
            joint_roots = [str(self.joints[0])]
        for root in joint_roots:
            if cmds.objExists(root):
                try:
                    cmds.connectAttr(
                        f"{ctrl}.jointsVis", f"{root}.visibility", force=True
                    )
                except RuntimeError:
                    pass

        # meshDisplay -> the mesh shape's override (enabled at bind time).
        shape = TubePath._resolve_mesh_shape(self.mesh)
        if shape:
            try:
                cmds.connectAttr(
                    f"{ctrl}.meshDisplay",
                    f"{shape}.overrideDisplayType",
                    force=True,
                )
            except RuntimeError:
                pass

        Controls.set_channel_state(
            ctrl, lock=("t", "r", "s"), hide=("t", "r", "s", "v")
        )
        self._register_in_control_set([ctrl])
        # Pick-walk: the settings control sits ABOVE the chain root, so
        # walking up from the first drive control reaches it.
        first = self._end_control(0)
        if first:
            _TubeRigInternal._chain_controller_tags([ctrl, first])
        return ctrl

    def _live_mesh(self) -> Optional[str]:
        """``self.mesh`` as a path that exists NOW — refreshed by UUID when the
        recorded path went stale (rename, reparent); None once the mesh is gone."""
        mesh = str(self.mesh) if self.mesh else ""
        if mesh and cmds.objExists(mesh):
            return mesh
        refreshed = cmds.ls(self._mesh_uuid, long=True) if self._mesh_uuid else []
        if not refreshed:
            return None
        self.mesh = refreshed[0]
        return self.mesh

    def _rig_rides(self, mesh: str) -> bool:
        """True when the rig group sits under *mesh*'s parent, so every joint
        rides whatever animates the mesh and pinning the mesh is right.

        A group placed elsewhere — a caller-supplied one, or the world-root
        layout of every rig built before rigs were homed beside their mesh —
        rides nothing the mesh rides; freezing the mesh would then stop it
        following its module at all, which is worse than the double transform
        it had. Such rigs keep the legacy behavior until rebuilt.
        """
        grp = self._group_path()
        parent = NodeUtils.get_parent(mesh, type=None, full_path=True)
        return bool(grp and parent and grp.startswith(f"{parent}|"))

    def _pin_mesh(self, mesh: str) -> None:
        """Pin *mesh*'s world matrix so the skin alone carries it.

        The rig rides the mesh's parent (``rig_group``); a mesh whose
        transform ALSO inherits that motion is transformed twice — once by
        the joints that followed the parent, once more by its own transform
        (the plug end of a production wire loom moved 2.01x its module,
        2026-08-30). Pinning is the "deformed geometry must not inherit"
        idiom done without touching the asset's (locked) channels:
        ``Matrices.pin_world_matrix`` — applied only while the rig really
        does ride the mesh's parent (``_rig_rides``).

        A re-bind (``rebind_skin``, a Step 3 rerun) re-pins at the parent's
        CURRENT pose — where the joints are — so a previous pin is released
        first. The record keeps the pinned mesh's UUID, so ``teardown``
        restores inheritance even after a rename, and a mesh the ARTIST set
        to not inherit (the pin no-ops) is left exactly as authored.
        """
        self.rig_group  # resolve the group: the record is written on it
        self._release_mesh_pin()
        pinned = self._rig_rides(mesh) and Matrices.pin_world_matrix(mesh)
        self._stamp(mesh_pinned=TubeRig._uuid(mesh) if pinned else None)

    def _release_mesh_pin(self) -> None:
        """Undo ``_pin_mesh`` for the mesh this rig pinned (no-op otherwise)."""
        key = (self.scene_data(self._group_path()) or {}).get("mesh_pinned")
        if not key:
            return
        for mesh in cmds.ls(key, long=True) or []:
            Matrices.unpin_world_matrix(mesh)
        self._stamp(mesh_pinned=None)

    def _build_pose_world_matrix(self, node, mesh) -> "om.MMatrix":
        """*node*'s world matrix carried back to the rig's BUILD pose.

        *mesh* is pinned at the build pose (``_pin_mesh``) while the joints
        ride the mesh's parent, so an influence that joins the skin later
        must be registered where it WOULD have stood at build: its current
        matrix with the parent's motion since the bind undone — R_b (the
        pinned parent matrix, the mesh's ``offsetParentMatrix``) against
        R_now (the parent's world matrix). Without a pin of this rig's own on
        *mesh* (rig at world root, artist-authored non-inheriting mesh) the
        current matrix IS the build-pose matrix and is returned unchanged.
        """
        world = om.MMatrix(cmds.xform(str(node), q=True, ws=True, m=True))
        mesh = str(mesh) if mesh and cmds.objExists(str(mesh)) else None
        key = (self.scene_data(self._group_path()) or {}).get("mesh_pinned")
        if not (mesh and key and key == TubeRig._uuid(mesh)):
            return world
        parent = NodeUtils.get_parent(mesh, type=None, full_path=True)
        if not parent:
            return world
        r_build = om.MMatrix(cmds.getAttr(f"{mesh}.offsetParentMatrix"))
        r_now = om.MMatrix(cmds.getAttr(f"{parent}.worldMatrix[0]"))
        return world * r_now.inverse() * r_build

    def _set_mesh_display_locked(self, locked: bool) -> None:
        """Reference-lock the mesh's viewport display (or restore it).

        A marquee select on a handed-off rig must grab controls, not the
        tube. The display TYPE stays animator-switchable through the
        settings control's ``meshDisplay`` enum — this only flips
        ``overrideEnabled`` (and resets the type when unlocking, where the
        settings connection is already gone or about to be).
        """
        mesh = self._live_mesh()
        shape = TubePath._resolve_mesh_shape(mesh) if mesh else None
        if not shape:
            return
        try:
            cmds.setAttr(f"{shape}.overrideEnabled", 1 if locked else 0)
            if not locked and not (
                cmds.listConnections(
                    f"{shape}.overrideDisplayType", source=True, destination=False
                )
                or []
            ):
                cmds.setAttr(f"{shape}.overrideDisplayType", 0)
        except Exception as e:
            self.logger.debug(f"Mesh display override skipped: {e}")

    def _space_prefix(self, control) -> str:
        """Node-name prefix for *control*'s space-switch assembly, derived
        from its leaf (``hose_end_CTRL`` -> ``hose_end_space``)."""
        role = CoreUtils.leaf_name(str(control))
        if role.startswith(f"{self.rig_name}_"):
            role = role[len(self.rig_name) + 1 :]
        if role.endswith("_CTRL"):
            role = role[: -len("_CTRL")]
        return f"{self.rig_name}_{role}_space"

    @CoreUtils.undoable
    def setup_space_switching(self, control, attr_name: str = "space") -> str:
        """Give *control* an animator-keyable ``space`` enum: local / world /
        custom.

        A ``<rig>_<role>_space_GRP`` inserts directly above the control's
        offset group — BELOW any follow or auto-bend groups, so those keep
        composing in local mode and are exactly cancelled in world/custom
        mode (a world-pinned mid control stops following the ends, which is
        what pinning means). Local mode is a bit-exact no-op: the switch
        passes identity until a non-local space is selected
        (``Matrices.build_space_switch`` passthrough mode, offsets captured
        at build so engaging a space at the build pose is stationary).

        Offsets are STATIC: switching while posed pops to the new space's
        frame. Assign the custom slot with :meth:`set_custom_space`.

        Returns:
            The inserted space group's long path. The control's DAG path
            changes — callers re-resolve (``_rig_scoped_path``).
        """
        ctrl = _TubeRigInternal._long_path(str(control))
        leaf = CoreUtils.leaf_name(ctrl)
        prefix = self._space_prefix(ctrl)

        wrap = NodeUtils.get_parent(ctrl, type=None, full_path=True) or ctrl
        outer = NodeUtils.get_parent(wrap, type=None, full_path=True)
        space_grp = cmds.group(empty=True, name=f"{prefix}_GRP")
        if outer:
            # relative: the group's LOCAL stays identity, so its frame is
            # exactly the wrapped group's old parent frame.
            space_grp = cmds.parent(space_grp, outer, relative=True)[0]
        space_grp = _TubeRigInternal._long_path(space_grp)
        cmds.parent(wrap, space_grp)
        ctrl = self._rig_scoped_path(leaf)

        Matrices.build_space_switch(
            space_grp,
            ["world", None],
            attr_owner=ctrl,
            attr_name=attr_name,
            name=prefix,
            enum_labels=["local", "world", "custom"],
            capture_offsets=True,
            passthrough_default=True,
        )
        return space_grp

    def set_custom_space(self, control, target: Optional[str]) -> None:
        """Assign (or with ``target=None`` clear) *control*'s custom space.

        Three referencing-friendly edits: one ``connectAttr`` and two
        ``setAttr`` — no constraint-target surgery. The offset is captured
        NOW, so assign with the control at rest (or accept that engaging
        the space reproduces the assignment-time relationship).
        """
        ctrl = _TubeRigInternal._long_path(str(control))
        prefix = self._space_prefix(ctrl)
        mmx = f"{prefix}_01_MMX"
        cond = f"{prefix}_01_COND"
        if not (cmds.objExists(mmx) and cmds.objExists(cond)):
            raise ValueError(
                f"{CoreUtils.leaf_name(ctrl)} has no space switch "
                f"(expected {mmx}); run setup_space_switching first."
            )
        for src in (
            cmds.listConnections(
                f"{mmx}.matrixIn[1]", source=True, destination=False, plugs=True
            )
            or []
        ):
            cmds.disconnectAttr(src, f"{mmx}.matrixIn[1]")
        if target is None:
            cmds.setAttr(f"{mmx}.matrixIn[0]", *list(om.MMatrix()), type="matrix")
            cmds.setAttr(f"{cond}.colorIfTrueR", 0.0)
            return
        target = str(target)
        grp_path = self._rig_scoped_path(f"{prefix}_GRP")
        m_grp = om.MMatrix(cmds.xform(grp_path, q=True, ws=True, matrix=True))
        m_tgt = om.MMatrix(cmds.xform(target, q=True, ws=True, matrix=True))
        cmds.setAttr(
            f"{mmx}.matrixIn[0]", *list(m_grp * m_tgt.inverse()), type="matrix"
        )
        cmds.connectAttr(f"{target}.worldMatrix[0]", f"{mmx}.matrixIn[1]", force=True)
        cmds.setAttr(f"{cond}.colorIfTrueR", 1.0)

    # ------------------------------------------------------------------
    # Control Rigs (shared by strategies and the step-by-step UI)
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def create_spline_controls(
        self,
        joints: List[str],
        centerline: Optional[List] = None,
        size: float = 1.0,
        num_controls: int = 3,
        enable_stretch: bool = True,
        enable_squash: bool = True,
        enable_volume: bool = True,
        enable_twist: bool = True,
        enable_auto_bend: bool = False,
        enable_tweaks: bool = True,
    ) -> Tuple[List[str], str, str]:
        """Build the complete spline-IK control rig over an existing joint chain:
        logic curve, IK handle, driver controls, the optional twist /
        auto-bend / stretch systems, and (default on) the tweak finesse
        layer (``create_tweak_controls``).

        Parameters:
            joints: The joint chain (root first).
            centerline: Path for the IK curve; defaults to the joint positions.
            size: Control base size — pass the tube radius for proportional
                controls (``resolve_sizes``).
            num_controls: Driver control count (3 = start/mid/end + tangents).
            enable_*: Feature toggles, matching the one-click build options.

        Returns:
            Tuple of (controls, ik_handle, curve).
        """
        joints = [str(j) for j in joints]
        if len(joints) < 2:
            raise ValueError("Spline IK needs a chain of at least 2 joints.")
        if centerline is None:
            centerline = [_TubeRigInternal._xform_t_ws(j) for j in joints]

        curve = self.create_logic_curve(centerline)
        ik_handle = self.create_ik(
            joints, solver="ikSplineSolver", curve=curve, createCurve=False
        )
        cmds.setAttr(f"{ik_handle}.visibility", False)

        controls, driver_joints, up_locs = self.create_spline_drivers(
            centerline, size, num_controls
        )
        self.skin_curve_to_drivers(curve, driver_joints)

        start_ctrl = controls[0]
        end_ctrl = controls[-1]
        mid_idx = int(len(controls) / 2)
        mid_ctrl = controls[mid_idx] if len(controls) > 2 else None
        start_up_loc, end_up_loc = up_locs

        if enable_twist:
            self.setup_spline_twist(
                ik_handle, start_ctrl, end_ctrl, start_up_loc, end_up_loc
            )

        if enable_auto_bend:
            if num_controls == 3 and mid_ctrl:
                self.setup_auto_bend(start_ctrl, mid_ctrl, end_ctrl)
            else:
                self.logger.warning(
                    "Auto Bend is only available with 3 controls. Skipping."
                )

        # Animator space switching on the posable stations: the end control
        # and (when the layout has one) the mid. The start control stays the
        # rig-local root. Paths are re-resolved first — the auto-bend insert
        # above already invalidated the mid control's captured path — and the
        # inserts here are absorbed by the canonical re-resolve below.
        self.setup_space_switching(self._rig_scoped_path(end_ctrl))
        if mid_ctrl:
            self.setup_space_switching(self._rig_scoped_path(mid_ctrl))

        # Hierarchy inserts above the intermediate controls (follow groups,
        # auto-bend, space groups) change their absolute DAG paths — resolve
        # the canonical paths once everything is in place, before anything
        # records them.
        controls = [self._rig_scoped_path(c) for c in controls]
        start_ctrl = controls[0]

        if enable_stretch or enable_squash:
            self.setup_spline_stretch(
                curve,
                joints,
                enable_stretch,
                enable_squash,
                enable_volume,
                main_control=start_ctrl,
            )

        # Settings control LAST: its proxy attrs mirror masters the stretch/
        # twist/auto-bend setups above create (a proxy needs its master to
        # exist first).
        self.create_settings_control(size=size)

        self.ik_handle = ik_handle
        if enable_tweaks:
            # Re-homes the solver onto the proxy chain and updates
            # ``self.ik_handle`` — inside this method (not the strategy) so
            # the step-by-step UI path gets the layer too.
            self.create_tweak_controls(joints, size=size)
            ik_handle = self.ik_handle
        return controls, ik_handle, curve

    @CoreUtils.undoable
    def create_fk_controls(
        self, joints: List[str], size: float = 1.0, num_controls: int = 5
    ) -> List[str]:
        """Build a nested FK control hierarchy over the joint chain.

        With *num_controls* fewer than the joint count, each control owns a
        SPAN of joints and spreads its own rotation evenly across them, so
        one key bends its whole section in a smooth arc. A tentacle built at
        one control per joint is technically FK and practically unusable —
        an Auto build makes a joint per edge loop, so posing it means keying
        twenty-odd nested controls in lockstep to get a single curve, and any
        one of them left behind puts a corner in the tube. Distributed
        controls RIDE the deformed chain (each offset group follows the
        previous span's end joint) rather than nesting, so a control always
        sits on the tube it drives and rotation-only posing preserves every
        bone length; FK accumulation still happens, through the chain
        itself. The classic one-per-joint build keeps its nested hierarchy.

        Parameters:
            joints: The joint chain (root first).
            size: Control base size — pass the tube radius for proportional
                controls (``resolve_sizes``).
            num_controls: How many controls to build. ``-1`` (or a count at
                or above the joint count) gives the classic one-per-joint
                chain; anything smaller distributes as described above.
        """
        joints = [str(j) for j in joints]
        if not joints:
            raise ValueError("FK controls need at least 1 joint.")

        n_joints = len(joints)
        if num_controls is None or num_controls <= 0 or num_controls >= n_joints:
            spans = [[i] for i in range(n_joints)]
        else:
            # Contiguous spans covering every joint; the remainder is spread
            # over the leading spans rather than dumped on the last one, so
            # no single control ends up driving a disproportionate stretch.
            base, extra = divmod(n_joints, num_controls)
            spans, start = [], 0
            for k in range(num_controls):
                length = base + (1 if k < extra else 0)
                spans.append(list(range(start, start + length)))
                start += length

        # Fit each control to the gap between the joints it sits BETWEEN --
        # the span stride, not the joint stride, now that one control can
        # cover several joints. A control wider than that gap swallows its
        # neighbour and the chain reads as one solid blob with nothing
        # individually pickable (reported as "this rig has no controls").
        anchors = [joints[s[0]] for s in spans]
        stride = [
            (
                om.MVector(*_TubeRigInternal._xform_t_ws(b))
                - om.MVector(*_TubeRigInternal._xform_t_ws(a))
            ).length()
            for a, b in zip(anchors, anchors[1:])
        ]
        target_width = min(size * 4.0, min(stride) * 0.8) if stride else size * 4.0
        ctrl_size = _TubeRigInternal._fit_control_size("diamond", target_width, "x")

        # Distributed spans must not NEST their controls: a nested control
        # orbits its parent rigidly while the parent's span joints arc
        # gradually, so the span anchor's pointConstraint dragged joints to
        # the control's rigid-orbit position — one 50-degree root key on a
        # pre-bent hose stretched a boundary bone 159%. Instead each
        # control's offset group rides the PREVIOUS span's last joint
        # (parentConstraint), which is exactly the frame its anchor joint
        # lives in: the control stays on the tube it drives at any pose, and
        # FK rotation still accumulates — through the joint chain itself.
        distributed = any(len(s) > 1 for s in spans)
        controls: List[str] = []
        parent_ctrl = str(self.rig_group)
        prev_last_joint: Optional[str] = None

        for i, span in enumerate(spans):
            anchor = joints[span[0]]
            nodes = Controls.create(
                "diamond",
                name=f"{self.rig_name}_{i + 1}_CTRL",
                size=ctrl_size,
                axis="x",
                color=(1, 1, 0),
                return_nodes=True,
            )
            grp = nodes.group if nodes.group else nodes.control

            # Match joint transform via parentConstraint (clean matrix transfer)
            temp_const = cmds.parentConstraint(str(anchor), str(grp))
            cmds.delete(temp_const)

            grp = _TubeRigInternal._parent_to(
                grp, str(self.rig_group) if distributed else parent_ctrl
            )
            ctrl = _TubeRigInternal._control_path(nodes, grp)
            if distributed and prev_last_joint is not None:
                cmds.parentConstraint(str(prev_last_joint), grp, mo=True)

            if len(span) == 1:
                # One joint: a straight constraint is exact and keeps the
                # classic one-per-joint build byte-for-byte as it was.
                cmds.parentConstraint(ctrl, str(joints[span[0]]), mo=True)
            else:
                # The anchor carries this span's translation; the rotation is
                # divided evenly down the span so the section arcs instead of
                # hinging at its first joint. With the offset group riding the
                # previous span's end joint, the control's rest position
                # coincides with the anchor at every pose, so this constraint
                # only acts when the animator actually translates the control.
                cmds.pointConstraint(ctrl, str(anchor), mo=True)
                share = cmds.createNode(
                    "multiplyDivide", name=f"{self.rig_name}_fk{i + 1}_share_MD"
                )
                for axis in "XYZ":
                    cmds.setAttr(f"{share}.input2{axis}", 1.0 / len(span))
                    cmds.connectAttr(
                        f"{ctrl}.rotate{axis}", f"{share}.input1{axis}", force=True
                    )
                for j_index in span:
                    for axis in "XYZ":
                        cmds.connectAttr(
                            f"{share}.output{axis}",
                            f"{str(joints[j_index])}.rotate{axis}",
                            force=True,
                        )

            controls.append(ctrl)
            parent_ctrl = ctrl
            prev_last_joint = joints[span[-1]]

        self._finalize_controls(controls)
        self.create_settings_control(size=size)
        return controls

    def _build_proxy_chain(self, joints: List[str]) -> List[str]:
        """Fresh hidden clone of the bind chain for the solver to drive.

        Built by a createNode loop copying translate/jointOrient/radius —
        never ``cmds.duplicate``, which would drag the ikEffector (a DAG
        child of the second-to-last bind joint) along as debris. The proxy
        group sits under the rig group with identity transform, so copying
        LOCAL values lands the clones world-identical to the bind chain.
        """
        grp = cmds.group(empty=True, name=f"{self.rig_name}_proxy_GRP")
        grp = _TubeRigInternal._parent_to(grp, str(self.rig_group))
        cmds.setAttr(f"{grp}.visibility", False)
        proxies: List[str] = []
        parent = grp
        for i, jnt in enumerate(joints):
            cmds.select(clear=True)
            p = cmds.createNode("joint", name=f"{self.rig_name}_proxy_jnt_{i + 1}")
            p = _TubeRigInternal._parent_to(p, parent)
            for attr in ("translate", "jointOrient", "rotate"):
                cmds.setAttr(
                    f"{p}.{attr}", *cmds.getAttr(f"{jnt}.{attr}")[0], type="double3"
                )
            cmds.setAttr(f"{p}.radius", cmds.getAttr(f"{jnt}.radius"))
            proxies.append(p)
            parent = p
        return proxies

    @CoreUtils.undoable
    def create_tweak_controls(
        self, joints: List[str], size: float = 1.0, every_n: int = 1
    ) -> List[str]:
        """Secondary finesse layer over the spline result — the FK-on-IK
        control an experienced animator expects.

        Dual-chain: the ikSplineSolver is re-homed onto a hidden PROXY clone
        of the bind chain, tweak controls ride the proxies (offset group
        parent-constrained to its proxy joint — the FK ride-the-chain
        pattern), and each bind joint FOLLOWS its tweak through an
        offsetParentMatrix wire::

            OPM = restLocal^-1 x tweakWorld x parentWorldInverse

        The matrix wire, not a parentConstraint, is deliberate: a constraint
        must decompose the local matrix into T/R, which skews under the
        stretch system's non-uniform scale — and with the joints' scale
        compensation off, constrained chains ACCUMULATE scale down the
        hierarchy (s^2 by the second joint). The OPM form cancels the parent
        contribution entirely (nothing accumulates; each joint's world scale
        is exactly its own stretch/volume wiring) and is identity at the
        rest pose by construction, not to within constraint float noise.
        ``segmentScaleCompensate`` turns off on the bind joints (the wire
        assumes plain matrix composition; SSC's inverse-scale term would
        corrupt it) — the proxies keep it on, since they replicate today's
        solver chain, whose scaleX stretch depends on it.

        Zero tweaks is a bit-exact no-op at rest AND under every driver
        pose: the proxy chain is a clone driven by the same solver, curve,
        stretch and twist, so bind joints land where the solver would have
        put them. Rotating a tweak adds LOCAL twist/bend (its joint only —
        children are pinned to their own tweaks); the mesh's cubic weight
        support spreads single-joint deltas smoothly.

        Parameters:
            joints: The bind chain (root first). Stays the export skeleton:
                names, parenting, jointOrients and channel values untouched
                (motion lives in the offsetParentMatrix; bake on export).
            size: Control base size — pass the tube radius.
            every_n: Thin the tweak stations on dense chains (skipped joints
                follow their proxy directly).
        """
        joints = [str(j) for j in joints]
        if len(joints) < 2:
            raise ValueError("Tweak layer needs a chain of at least 2 joints.")

        stale = (
            cmds.ls(f"{self.rig_name}_proxy_*", f"{self.rig_name}_tweak_*", long=True)
            or []
        )
        # Rescue the IK curve first: re-homing the solver below parks it
        # INSIDE `<rig>_proxy_GRP`, which this sweep then deletes -- so a
        # second call took the curve with it, left curveInfo without an
        # input, and reported "build the spline controls first" about
        # controls that WERE built. Its own visibility is False, so the
        # group is not what hides it and the rig group is a safe home (that
        # is where a no-tweak build leaves it).
        rescued = (cmds.ls(f"{self.rig_name}_ik_curve", long=True) or [None])[0]
        if rescued and any(rescued.startswith(f"{n}|") for n in stale):
            _TubeRigInternal._parent_to(rescued, str(self.rig_group))
        for n in sorted(set(stale), key=len, reverse=True):
            if cmds.objExists(n):
                cmds.delete(n)

        proxies = self._build_proxy_chain(joints)

        # --- re-home the ikSplineSolver onto the proxy chain --------------
        curve = (cmds.ls(f"{self.rig_name}_ik_curve", long=True) or [None])[0]
        if not curve:
            raise ValueError(
                f"No '{self.rig_name}_ik_curve' — build the spline controls first."
            )
        old_handle = (
            str(self.ik_handle)
            if self.ik_handle and cmds.objExists(str(self.ik_handle))
            else f"{self.rig_name}_ikHandle"
        )
        had_twist = False
        if cmds.objExists(old_handle):
            had_twist = bool(cmds.getAttr(f"{old_handle}.dTwistControlEnable"))
            cmds.delete(old_handle)
        for eff in (
            cmds.listRelatives(
                joints, allDescendents=True, type="ikEffector", fullPath=True
            )
            or []
        ):
            if cmds.objExists(eff):
                cmds.delete(eff)
        new_handle = self.create_ik(
            proxies, solver="ikSplineSolver", curve=curve, createCurve=False
        )
        cmds.setAttr(f"{new_handle}.visibility", False)
        self.ik_handle = new_handle
        if had_twist:
            start_ctrl, end_ctrl = self._end_control(0), self._end_control(-1)
            ups = [
                (cmds.ls(f"{self.rig_name}_{s}_up_loc", long=True) or [None])[0]
                for s in ("start", "end")
            ]
            if start_ctrl and end_ctrl:
                self.setup_spline_twist(
                    new_handle, start_ctrl, end_ctrl, ups[0], ups[1]
                )

        # --- proxies take the stretch (the solver chain must lengthen) ----
        stretch_src = (
            cmds.listConnections(
                f"{joints[0]}.scaleX", source=True, destination=False, plugs=True
            )
            or []
        )
        if stretch_src:
            for p in proxies:
                cmds.connectAttr(stretch_src[0], f"{p}.scaleX", force=True)

        # --- tweak controls, riding the proxies ---------------------------
        tweak_grp = cmds.group(empty=True, name=f"{self.rig_name}_tweak_GRP")
        tweak_grp = _TubeRigInternal._parent_to(tweak_grp, str(self.rig_group))

        stations = list(range(0, len(joints), max(1, int(every_n))))
        if stations[-1] != len(joints) - 1:
            stations.append(len(joints) - 1)
        gaps = [
            (
                om.MVector(*_TubeRigInternal._xform_t_ws(joints[b]))
                - om.MVector(*_TubeRigInternal._xform_t_ws(joints[a]))
            ).length()
            for a, b in zip(stations, stations[1:])
        ]
        target_width = min(size * 1.6, min(gaps) * 0.8) if gaps else size * 1.6
        ctrl_size = _TubeRigInternal._fit_control_size("sphere", target_width, "x")

        tweaks: List[str] = []
        tweak_by_index: Dict[int, str] = {}
        for i in stations:
            nodes = Controls.create(
                "sphere",
                name=f"{self.rig_name}_tweak_{i + 1}",
                size=ctrl_size,
                axis="x",
                color=(0.85, 0.5, 0.95),
                return_nodes=True,
            )
            grp = nodes.group if nodes.group else nodes.control
            cmds.matchTransform(str(grp), proxies[i], pos=True, rot=True)
            grp = _TubeRigInternal._parent_to(grp, tweak_grp)
            ctrl = _TubeRigInternal._control_path(nodes, grp)
            cmds.parentConstraint(proxies[i], grp, mo=True)
            tweaks.append(ctrl)
            tweak_by_index[i] = ctrl

        # --- bind joints follow their tweak (or proxy) exactly ------------
        for i, jnt in enumerate(joints):
            cmds.setAttr(f"{jnt}.segmentScaleCompensate", 0)
            rest_local = om.MMatrix(cmds.xform(jnt, q=True, matrix=True))
            Matrices.drive_with_offset_parent_matrix(
                tweak_by_index.get(i, proxies[i]),
                jnt,
                name=f"{self.rig_name}_tweak_follow_{i + 1}",
                offset=list(rest_local.inverse()),
            )

        self._finalize_controls(tweaks, chains=[tweaks])
        settings = f"{self.rig_name}_settings_CTRL"
        if cmds.objExists(settings):
            if not cmds.attributeQuery("tweakCtrlsVis", node=settings, exists=True):
                cmds.addAttr(
                    settings,
                    longName="tweakCtrlsVis",
                    attributeType="bool",
                    defaultValue=True,
                )
                cmds.setAttr(f"{settings}.tweakCtrlsVis", channelBox=True)
            cmds.connectAttr(
                f"{settings}.tweakCtrlsVis", f"{tweak_grp}.visibility", force=True
            )
        self.tweak_controls = tweaks
        return tweaks

    @CoreUtils.undoable
    def create_anchor_controls(
        self, joints: List[str], size: float = 1.0, enable_stretch: bool = True
    ) -> List[str]:
        """Build the anchor/piston controls over the two end joints
        (``create_anchor_joints``): a box control per end constraining its
        joint, plus the optional distance-driven stretch network.

        The joints must be independent transforms — if the end joint is
        parented under the start joint, it is moved out first (a chained
        child would inherit the stretch scale and defeat the pinned-end
        behavior).

        Parameters:
            joints: Exactly two end joints, start first.
            size: Control base size — pass the tube radius for proportional
                controls (``resolve_sizes``).
            enable_stretch: Wire the distance-driven ``scaleX`` stretch.
        """
        joints = [str(j) for j in joints]
        if len(joints) != 2:
            raise ValueError(f"Anchor rigs use exactly 2 joints (got {len(joints)}).")
        j1, j2 = joints

        j1_long, j2_long = (
            _TubeRigInternal._long_path(j1),
            _TubeRigInternal._long_path(j2),
        )
        if j1_long and j2_long and j2_long.startswith(f"{j1_long}|"):
            self.logger.info(
                "Anchor end joint was chained under the start joint — "
                "unparenting so stretch scale can't propagate to it."
            )
            j2 = _TubeRigInternal._parent_to(
                j2, NodeUtils.get_parent(j1, type=None, full_path=True)
            )
            self.joints = [j1, j2]

        start_pos = om.MVector(*_TubeRigInternal._xform_t_ws(j1))
        end_pos = om.MVector(*_TubeRigInternal._xform_t_ws(j2))
        # Joint X axes carry the tube-end tangents (baked by
        # create_anchor_joints); the end control's frame is mirrored so its
        # X points back into the tube.
        dir_start = _TubeRigInternal._world_x_axis(j1)
        dir_end = _TubeRigInternal._world_x_axis(j2)
        start_rot = _TubeRigInternal._frame_rotation(dir_start)
        end_rot = _TubeRigInternal._frame_rotation(-dir_end)

        rig_grp = str(self.rig_group)

        # NB: ``Controls.create`` exposes ``size=`` for the uniform scale;
        # ``scale=`` is silently absorbed by the preset builder's ``**_``
        # kwargs and does nothing.
        def _make_anchor_ctrl(suffix, pos, rot):
            nodes = Controls.box(
                name=f"{self.rig_name}_{suffix}",
                size=size * 4,
                color=(0, 1, 1),
                return_nodes=True,
            )
            target = str(nodes.group) if nodes.group else str(nodes.control)
            cmds.xform(
                target,
                ws=True,
                t=(pos.x, pos.y, pos.z),
                ro=_TubeRigInternal._euler_deg(rot),
            )
            target = _TubeRigInternal._parent_to(target, rig_grp)
            return _TubeRigInternal._control_path(nodes, target)

        start_ctrl = _make_anchor_ctrl("start", start_pos, start_rot)
        end_ctrl = _make_anchor_ctrl("end", end_pos, end_rot)

        # Joints follow control position and rotation (rotatable tube ends)
        cmds.pointConstraint(start_ctrl, j1, mo=True)
        cmds.pointConstraint(end_ctrl, j2, mo=True)
        cmds.orientConstraint(start_ctrl, j1, mo=True)
        cmds.orientConstraint(end_ctrl, j2, mo=True)

        if enable_stretch:
            # Measure the control distance in the rig's local space via
            # multMatrix so scaling the rig group can't double-transform.
            start_local_mm = cmds.createNode(
                "multMatrix", name=f"{self.rig_name}_start_local_MM"
            )
            cmds.connectAttr(
                f"{str(start_ctrl)}.worldMatrix[0]",
                f"{start_local_mm}.matrixIn[0]",
                force=True,
            )
            cmds.connectAttr(
                f"{rig_grp}.worldInverseMatrix[0]",
                f"{start_local_mm}.matrixIn[1]",
                force=True,
            )

            end_local_mm = cmds.createNode(
                "multMatrix", name=f"{self.rig_name}_end_local_MM"
            )
            cmds.connectAttr(
                f"{str(end_ctrl)}.worldMatrix[0]",
                f"{end_local_mm}.matrixIn[0]",
                force=True,
            )
            cmds.connectAttr(
                f"{rig_grp}.worldInverseMatrix[0]",
                f"{end_local_mm}.matrixIn[1]",
                force=True,
            )

            dist_node = cmds.createNode("distanceBetween", name=f"{self.rig_name}_dist")
            cmds.connectAttr(
                f"{start_local_mm}.matrixSum", f"{dist_node}.inMatrix1", force=True
            )
            cmds.connectAttr(
                f"{end_local_mm}.matrixSum", f"{dist_node}.inMatrix2", force=True
            )

            initial_dist = (end_pos - start_pos).length()

            norm_md = cmds.createNode(
                "multiplyDivide", name=f"{self.rig_name}_scale_MD"
            )
            cmds.setAttr(f"{norm_md}.operation", 2)  # Divide
            cmds.connectAttr(f"{dist_node}.distance", f"{norm_md}.input1X", force=True)
            cmds.setAttr(f"{norm_md}.input2X", initial_dist)

            # Start joint scales to stretch toward end
            cmds.connectAttr(f"{norm_md}.outputX", f"{j1}.scaleX", force=True)

        # Anchor ends both attach to machinery — both get space switching.
        # The inserts invalidate the captured paths; re-resolve before
        # anything records them.
        self.setup_space_switching(start_ctrl)
        self.setup_space_switching(end_ctrl)
        start_ctrl = self._rig_scoped_path(start_ctrl)
        end_ctrl = self._rig_scoped_path(end_ctrl)

        self._finalize_controls([start_ctrl, end_ctrl])
        self.create_settings_control(size=size)
        return [start_ctrl, end_ctrl]

    @CoreUtils.undoable
    def setup_spline_twist(
        self, ik_handle, start_ctrl, end_ctrl, start_up_loc=None, end_up_loc=None
    ):
        """Setup advanced twist for IK Spline.

        Args:
            ik_handle: The IK Spline handle.
            start_ctrl: Start control transform.
            end_ctrl: End control transform.
            start_up_loc: Optional up locator for start (child of start_ctrl). If None, uses control.
            end_up_loc: Optional up locator for end (child of end_ctrl). If None, uses control.
        """
        ik_handle = str(ik_handle)
        start_ctrl = str(start_ctrl)
        end_ctrl = str(end_ctrl)

        cmds.setAttr(f"{ik_handle}.dTwistControlEnable", True)

        if start_up_loc and end_up_loc:
            # Object Rotation Up (Start/End) — more stable than control matrices
            # when controls translate.
            cmds.setAttr(f"{ik_handle}.dWorldUpType", 4)
            cmds.setAttr(f"{ik_handle}.dWorldUpAxis", 0)  # the JOINT's up is +Y
            # The world up VECTOR is read back off the locators rather than
            # hard-coded, so it can never disagree with where they were placed;
            # on a vertical run that is no longer Y, and the twist stops being
            # degenerate. Start and end are read separately: the controls can be
            # posed apart, and each end solves against its own locator.
            for suffix, loc, ctrl in (
                ("", start_up_loc, start_ctrl),
                ("End", end_up_loc, end_ctrl),
            ):
                vec = _TubeRigInternal._up_vector_of(loc, ctrl)
                for axis, value in zip("XYZ", vec):
                    cmds.setAttr(f"{ik_handle}.dWorldUpVector{suffix}{axis}", value)
            cmds.connectAttr(
                f"{str(start_up_loc)}.worldMatrix[0]",
                f"{ik_handle}.dWorldUpMatrix",
                force=True,
            )
            cmds.connectAttr(
                f"{str(end_up_loc)}.worldMatrix[0]",
                f"{ik_handle}.dWorldUpMatrixEnd",
                force=True,
            )
        else:
            cmds.setAttr(f"{ik_handle}.dWorldUpType", 4)
            # Same degeneracy, same remedy: without locators the only geometry
            # available is the two controls, so the axis is chosen off that
            # chord rather than left on the world-Y default.
            for axis, value in zip(
                "XYZ",
                _TubeRigInternal._twist_up_axis(
                    [
                        _TubeRigInternal._xform_t_ws(start_ctrl),
                        _TubeRigInternal._xform_t_ws(end_ctrl),
                    ]
                ),
            ):
                cmds.setAttr(f"{ik_handle}.dWorldUpVector{axis}", value)
                cmds.setAttr(f"{ik_handle}.dWorldUpVectorEnd{axis}", value)
            cmds.connectAttr(
                f"{start_ctrl}.worldMatrix[0]",
                f"{ik_handle}.dWorldUpMatrix",
                force=True,
            )
            cmds.connectAttr(
                f"{end_ctrl}.worldMatrix[0]",
                f"{ik_handle}.dWorldUpMatrixEnd",
                force=True,
            )

        if not cmds.attributeQuery("roll", node=end_ctrl, exists=True):
            cmds.addAttr(end_ctrl, ln="roll", at="double", k=True)
        cmds.connectAttr(f"{end_ctrl}.roll", f"{ik_handle}.roll", force=True)

    @CoreUtils.undoable
    def setup_auto_bend(self, start_ctrl, mid_ctrl, end_ctrl):
        """Setup automatic bending of the mid control based on compression
        distance. The bow runs perpendicular to the start→end chord (the
        chord frame's Y), so hoses at any orientation bow outward rather
        than sliding along their own axis."""
        start_ctrl = str(start_ctrl)
        mid_ctrl = str(mid_ctrl)
        end_ctrl = str(end_ctrl)
        rig_grp = str(self.rig_group)

        # dv 0.5: a hose compressed by d bows out by d/2 — visible, natural
        # slack out of the box (dv=0 made an "enabled" auto-bend a silent
        # no-op until the animator discovered the attribute).
        if not cmds.attributeQuery("autoBend", node=start_ctrl, exists=True):
            cmds.addAttr(
                start_ctrl, ln="autoBend", at="double", min=0, max=5, dv=0.5, k=True
            )

        # Identify the mid control's offset group (parented to rig_group).
        offset_grp = NodeUtils.get_parent(mid_ctrl, type=None, full_path=True)
        if not offset_grp or CoreUtils.short_name(offset_grp) == CoreUtils.short_name(
            rig_grp
        ):
            offset_grp = mid_ctrl

        start_pos = om.MVector(*_TubeRigInternal._xform_t_ws(start_ctrl))
        end_pos = om.MVector(*_TubeRigInternal._xform_t_ws(end_ctrl))
        chord = end_pos - start_pos
        initial_length = chord.length()

        # The bend group's translateY moves it in PARENT space, so the bow
        # direction is the parent frame's Y — previously world Y, which on a
        # vertical hose slid the mid control ALONG the tube instead of
        # bowing it outward. An orient group aligned to the chord frame
        # (X down the hose, Y its stable perpendicular) makes the driven
        # translateY bow perpendicular to the hose at any orientation.
        orient_grp = cmds.group(
            empty=True, name=f"{self.rig_name}_mid_autoBend_ORIENT_GRP"
        )
        cmds.matchTransform(orient_grp, offset_grp, pos=True, rot=True)
        if initial_length > 1e-6:
            cmds.xform(
                orient_grp,
                ws=True,
                ro=_TubeRigInternal._euler_deg(_TubeRigInternal._frame_rotation(chord)),
            )

        auto_bend_grp = cmds.group(empty=True, name=f"{self.rig_name}_mid_autoBend_GRP")

        # Match transform of the offset group (which is at mid position)
        cmds.matchTransform(auto_bend_grp, offset_grp, pos=True, rot=True)

        # Insert into hierarchy: RigGroup -> Orient -> AutoBend -> Offset -> Control
        current_parent = NodeUtils.get_parent(offset_grp, type=None, full_path=True)
        if current_parent:
            orient_grp = _TubeRigInternal._parent_to(orient_grp, current_parent)
        auto_bend_grp = _TubeRigInternal._parent_to(auto_bend_grp, orient_grp)
        offset_grp = _TubeRigInternal._parent_to(offset_grp, auto_bend_grp)

        # Logic: (Initial_Length - Current_Dist) * autoBend -> translateY
        dist_node = cmds.createNode("distanceBetween", name=f"{self.rig_name}_ab_dist")
        cmds.connectAttr(
            f"{start_ctrl}.worldMatrix[0]", f"{dist_node}.inMatrix1", force=True
        )
        cmds.connectAttr(
            f"{end_ctrl}.worldMatrix[0]", f"{dist_node}.inMatrix2", force=True
        )

        # Calculate compression: initial_length - current_dist
        pma = cmds.createNode("plusMinusAverage", name=f"{self.rig_name}_ab_sub")
        cmds.setAttr(f"{pma}.operation", 2)  # Subtract
        cmds.setAttr(f"{pma}.input1D[0]", initial_length)
        cmds.connectAttr(f"{dist_node}.distance", f"{pma}.input1D[1]", force=True)

        # Clamp min 0 (ignore stretching, only bend on compression)
        clamp = cmds.createNode("clamp", name=f"{self.rig_name}_ab_clamp")
        cmds.setAttr(f"{clamp}.minR", 0)
        cmds.setAttr(f"{clamp}.maxR", 10000)
        cmds.connectAttr(f"{pma}.output1D", f"{clamp}.inputR", force=True)

        # Multiply by autoBend factor
        md = cmds.createNode("multiplyDivide", name=f"{self.rig_name}_ab_mult")
        cmds.connectAttr(f"{clamp}.outputR", f"{md}.input1X", force=True)
        cmds.connectAttr(f"{start_ctrl}.autoBend", f"{md}.input2X", force=True)

        # translateY of the bend group — its parent is the chord-frame orient
        # group, so this bows perpendicular to the hose at any orientation.
        cmds.connectAttr(f"{md}.outputX", f"{auto_bend_grp}.translateY", force=True)

    @CoreUtils.undoable
    def setup_spline_stretch(
        self,
        curve,
        joints,
        enable_stretch=True,
        enable_squash=True,
        enable_volume=True,
        main_control=None,
    ):
        curve = str(curve)
        rig_grp = str(self.rig_group)
        main_control = str(main_control) if main_control else None

        curve_shape = NodeUtils.get_shape(curve)
        if not curve_shape:
            self.logger.warning(f"setup_spline_stretch: no shape under {curve}")
            return

        curve_info = cmds.createNode("curveInfo", name=f"{self.rig_name}_curveInfo")
        cmds.connectAttr(
            f"{curve_shape}.worldSpace[0]", f"{curve_info}.inputCurve", force=True
        )
        initial_length = cmds.getAttr(f"{curve_info}.arcLength")

        scale_comp_md = cmds.createNode(
            "multiplyDivide", name=f"{self.rig_name}_scale_comp_MD"
        )
        cmds.setAttr(f"{scale_comp_md}.operation", 2)  # Divide
        cmds.connectAttr(
            f"{curve_info}.arcLength", f"{scale_comp_md}.input1X", force=True
        )
        cmds.connectAttr(f"{rig_grp}.scaleX", f"{scale_comp_md}.input2X", force=True)

        norm_md = cmds.createNode("multiplyDivide", name=f"{self.rig_name}_norm_MD")
        cmds.setAttr(f"{norm_md}.operation", 2)
        cmds.connectAttr(f"{scale_comp_md}.outputX", f"{norm_md}.input1X", force=True)
        cmds.setAttr(f"{norm_md}.input2X", initial_length)

        # Clamp logic for separate stretch/squash control
        min_limit = 0.001 if enable_squash else 1.0
        max_limit = 10000.0 if enable_stretch else 1.0

        scale_val_src = f"{norm_md}.outputX"
        if not enable_squash or not enable_stretch:
            clamp_node = cmds.createNode("clamp", name=f"{self.rig_name}_scale_clamp")
            cmds.setAttr(f"{clamp_node}.minR", min_limit)
            cmds.setAttr(f"{clamp_node}.maxR", max_limit)
            cmds.connectAttr(f"{norm_md}.outputX", f"{clamp_node}.inputR", force=True)
            scale_val_src = f"{clamp_node}.outputR"

        # ----------------------------------------------------------------------
        # Attribute Setup (User Controls)
        # ----------------------------------------------------------------------
        stretch_output = scale_val_src

        if main_control:
            # Add Separator if not present (shared by vol and stretch)
            if not cmds.attributeQuery("separator_opt", node=main_control, exists=True):
                cmds.addAttr(
                    main_control, ln="separator_opt", at="enum", en="____", k=True
                )
                cmds.setAttr(f"{main_control}.separator_opt", lock=True)

            # 1. Stretch blending (Animator toggles stretch effect)
            if enable_stretch or enable_squash:
                if not cmds.attributeQuery(
                    "stretchFactor", node=main_control, exists=True
                ):
                    cmds.addAttr(
                        main_control,
                        ln="stretchFactor",
                        at="double",
                        min=0,
                        max=1,
                        dv=1.0,
                        k=True,
                    )

                # Blend between Calculated Stretch (Color1) and 1.0 (Color2)
                blend_stretch = cmds.createNode(
                    "blendColors", name=f"{self.rig_name}_stretch_BLEND"
                )
                cmds.connectAttr(
                    f"{main_control}.stretchFactor",
                    f"{blend_stretch}.blender",
                    force=True,
                )
                cmds.connectAttr(scale_val_src, f"{blend_stretch}.color1R", force=True)
                cmds.setAttr(f"{blend_stretch}.color2R", 1.0)

                stretch_output = f"{blend_stretch}.outputR"

        # ----------------------------------------------------------------------
        # Volume Preservation: scaleY = scaleZ = scaleX ^ -0.5
        # ----------------------------------------------------------------------
        vol_output = None

        if enable_volume:
            vol_pow = cmds.createNode("multiplyDivide", name=f"{self.rig_name}_vol_POW")
            cmds.setAttr(f"{vol_pow}.operation", 3)  # Power
            cmds.connectAttr(stretch_output, f"{vol_pow}.input1X", force=True)
            cmds.setAttr(f"{vol_pow}.input2X", -0.5)

            vol_output = f"{vol_pow}.outputX"

            if main_control:
                if not cmds.attributeQuery(
                    "volumeFactor", node=main_control, exists=True
                ):
                    cmds.addAttr(
                        main_control,
                        ln="volumeFactor",
                        at="double",
                        min=0,
                        max=2,
                        dv=1.0,
                        k=True,
                    )

                blend_vol = cmds.createNode(
                    "blendColors", name=f"{self.rig_name}_vol_BLEND"
                )
                cmds.connectAttr(
                    f"{main_control}.volumeFactor",
                    f"{blend_vol}.blender",
                    force=True,
                )
                cmds.connectAttr(
                    f"{vol_pow}.outputX", f"{blend_vol}.color1R", force=True
                )
                cmds.setAttr(f"{blend_vol}.color2R", 1.0)

                vol_output = f"{blend_vol}.outputR"

        for jnt in joints:
            jnt = str(jnt)
            cmds.connectAttr(stretch_output, f"{jnt}.scaleX", force=True)
            if enable_volume and vol_output:
                cmds.connectAttr(vol_output, f"{jnt}.scaleY", force=True)
                cmds.connectAttr(vol_output, f"{jnt}.scaleZ", force=True)

    # ------------------------------------------------------------------
    # RP IK / Legacy Controls (Now delegated to RigUtils)
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def create_ik(self, joints: List[str], **kwargs) -> Optional[str]:
        # Wrapper for RigUtils.create_ik_handle to maintain API compatibility
        joints = cmds.ls(joints, type="joint", flatten=True)
        if len(joints) < 2:
            self.logger.error("Insufficient joints to create IK handle.")
            return None

        name = kwargs.pop("name", f"{self.rig_name}_ikHandle")
        return RigUtils.create_ik_handle(
            start_joint=joints[0],
            end_joint=joints[-1],
            name=name,
            parent=self.rig_group,
            **kwargs,
        )

    @CoreUtils.undoable
    def create_pole_vector(self, ik_handle, mid_joint: str, offset=(0, 5, 0)) -> str:
        # Wrapper for RigUtils.create_pole_vector
        # Note: RigUtils uses 'distance' float while old method used vector offset tuple?
        # Old signature: offset=(0,5,0).
        # RigUtils expects distance.
        # We'll adapt.
        dist = om.MVector(offset).length()
        pv = RigUtils.create_pole_vector(
            ik_handle=ik_handle,
            mid_joint=mid_joint,
            distance=dist,
            name=f"{self.rig_name}_poleVector_LOC",
            parent=self.rig_group,
        )
        self.pole_vector = pv
        return pv

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Skinning
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def bind_joint_chain(
        self,
        obj,
        joints: List[str],
        curve: Optional[str] = None,
        centerline: Optional[List] = None,
    ) -> Optional[str]:
        """Bind the joint chain to a polygon tube with smooth skinning.

        Weights are parametric along the tube — identical to the one-click
        build's bind step. Without an explicit *curve*/*centerline*, the
        centerline is re-extracted from the mesh (falling back to the joint
        positions); if the parametric solve fails, ``skin_mesh`` falls back
        to a geodesic bind.
        """
        self.logger.debug(f"Tube mesh: {obj} ({type(obj).__name__})")
        objs = list(dict.fromkeys(cmds.ls(obj, objectsOnly=True, flatten=True) or []))
        if not objs:
            self.logger.error(f"Invalid tube mesh: {obj}")
            return None
        first = objs[0]

        transform = NodeUtils.get_transform_node(first)
        if not transform:
            self.logger.error(f"Invalid transform node: {transform}")
            return None
        transform = str(transform)

        if not joints or not isinstance(joints, (list, tuple)):
            self.logger.error(f"Invalid joint list: {joints}")
            return None
        joints = [str(j) for j in joints]
        if not all(cmds.objExists(j) and cmds.objectType(j) == "joint" for j in joints):
            self.logger.error(f"Invalid joint list: {joints}")
            return None

        # The rig lives beside the mesh (``rig_group`` creates it there); a
        # group created before the tube was parented is re-homed. The mesh
        # itself is never touched: this step used to unlock its channels and
        # unparent it to world root to dodge the double transform — a module's
        # loom then left the module's hierarchy (and clashed by name with its
        # twin in the next module). The world-matrix pin in ``skin_mesh``
        # handles the double transform in place.
        rig_group = str(self.rig_group)
        tube_parent = NodeUtils.get_parent(transform, type=None, full_path=True)
        if tube_parent:
            rig_group = _TubeRigInternal._parent_to(rig_group, tube_parent)

        for j in joints:
            if not NodeUtils.get_parent(j, type=None, full_path=True):
                _TubeRigInternal._parent_to(j, rig_group)

        self.logger.debug(
            f"Creating skinCluster with {len(joints)} joints on {CoreUtils.leaf_name(transform)}"
        )

        # Parity with the one-click build: parametric ring-uniform weights
        # along the tube centerline (joint positions when extraction fails).
        if not curve and not centerline:
            try:
                centerline, _ = TubePath.get_centerline(transform, num_joints=-1)
            except Exception:
                centerline = None
            if not centerline or len(centerline) < 2:
                centerline = [_TubeRigInternal._xform_t_ws(j) for j in joints]

        skin_cluster = self.skin_mesh(
            joints, curve=curve, centerline=centerline, mesh=transform
        )
        if skin_cluster:
            self.logger.debug(f"SkinCluster created: {skin_cluster}")
        return skin_cluster

    # ------------------------------------------------------------------
    # Anchor Constraints
    # ------------------------------------------------------------------

    def _rig_scoped_path(self, node) -> str:
        """Canonical long path of this rig's node named like *node* (matched
        by leaf name, scoped to the rig group).

        Hierarchy inserts (follow groups, auto-bend) invalidate absolute
        paths captured earlier; leaf names stay unique *within* the rig, so
        a rig-group-scoped leaf lookup recovers the true path even when the
        same leaf exists in other rigs.
        """
        leaf = CoreUtils.leaf_name(node)
        matches = cmds.ls(leaf, long=True) or []
        grp = (
            _TubeRigInternal._long_path(str(self._rig_group))
            if self._rig_group and cmds.objExists(str(self._rig_group))
            else None
        )
        if grp:
            scoped = [m for m in matches if m.startswith(f"{grp}|")]
            matches = scoped or matches
        return matches[0] if matches else str(node)

    def _end_control(self, index: int) -> Optional[str]:
        """Long path of the rig's start (``0``) or end (``-1``) control, or
        None when the rig has no controls (bare chain).

        Prefers the live build's records; falls back to the naming
        conventions of the three control builders so post-restart rigs
        (empty registry) still resolve. Ambiguous short-name matches are
        narrowed to this rig's group.
        """
        recorded = self.start_loc if index == 0 else self.end_loc
        from_bundle = (
            self.bundle.controls[0 if index == 0 else -1]
            if self.bundle and self.bundle.controls
            else None
        )
        for c in (recorded, from_bundle):
            if c and cmds.objExists(str(c)):
                return _TubeRigInternal._long_path(str(c))

        grp = (
            _TubeRigInternal._long_path(self._rig_group)
            if self._rig_group and cmds.objExists(str(self._rig_group))
            else None
        )

        def _narrow(matches):
            if not matches:
                return None
            if len(matches) > 1 and grp:
                scoped = [m for m in matches if m.startswith(f"{grp}|")]
                matches = scoped or matches
            return matches[0]

        suffix = "start" if index == 0 else "end"
        named = _narrow(
            cmds.ls(f"{self.rig_name}_{suffix}_CTRL", type="transform", long=True) or []
        )
        if named:
            return named

        # Numbered controls (FK chains, N-point spline drivers).
        numbered = []
        for m in cmds.ls(f"{self.rig_name}_*_CTRL", type="transform", long=True) or []:
            match = re.fullmatch(
                rf"{re.escape(self.rig_name)}_(?:ctrl_)?(\d+)_CTRL",
                CoreUtils.leaf_name(m),
            )
            if match:
                numbered.append((int(match.group(1)), m))
        if numbered:
            numbered.sort(key=lambda t: t[0])
            wanted = numbered[0][0] if index == 0 else numbered[-1][0]
            return _narrow([m for n, m in numbered if n == wanted])
        return None

    def _end_anchor_prefix(self, joints: "List[str]", joint_index: int) -> str:
        """Node-name prefix identifying ONE end's anchor assembly.

        Per-end (not per-call) so re-anchoring can find and replace the
        previous result: the old ``generate_unique_name`` scheme produced
        ``<rig>_anchor_jnt`` / ``_anchor_jnt_001`` with nothing tying a name
        to the end it served, so the two ends were indistinguishable and a
        rerun could only ever stack. Matches blendertk's
        ``<rig>_anchor_{start,end}`` naming.
        """
        idx = joint_index % len(joints)
        end = "start" if idx == 0 else "end" if idx == len(joints) - 1 else str(idx)
        return f"{self.rig_name}_anchor_{end}"

    def _clear_end_anchor(self, prefix: str) -> None:
        """Delete a previous ``constrain_end_with_falloff`` result for one end.

        Order matters: the anchor joint is removed from every skinCluster it
        influences BEFORE it is deleted. ``removeInfluence`` renormalizes the
        rows the old falloff had redistributed — since that redistribution was
        proportional, this restores the pre-anchor weights exactly. Deleting
        the joint first would instead strand its weights on a dead influence.

        Note: the end control keeps whatever pose the old constraint left it
        in, so the replacement constraint's maintained offset is measured from
        there. Re-anchoring before animating the old anchor is therefore exact;
        re-anchoring after moving it bakes that displacement into the new rest
        offset. Constraining the control's offset group instead of the control
        would remove the caveat — deferred with the other end-anchor design work.
        """
        for con in cmds.ls(f"{prefix}_*", type="constraint", long=True) or []:
            if cmds.objExists(con):
                cmds.delete(con)
        for jnt in cmds.ls(f"{prefix}_jnt*", type="joint", long=True) or []:
            if not cmds.objExists(jnt):
                continue
            for sc in set(
                cmds.listConnections(f"{jnt}.worldMatrix[0]", type="skinCluster") or []
            ):
                if cmds.objExists(sc):
                    cmds.skinCluster(sc, edit=True, removeInfluence=jnt)
            cmds.delete(jnt)

    @CoreUtils.undoable
    def constrain_end_with_falloff(
        self,
        joints: "List[str]",
        anchor: str,
        falloff: float = 5.0,
        joint_index: int = -1,
        profile: Union[str, Callable] = "smoothstep",
    ) -> "Optional[str]":
        """
        Constrains a joint in the chain to an anchor and applies distance-based skin weight falloff.

        Re-anchoring the same end REPLACES its previous anchor (matching the
        rerun semantics every other step advertises) — see ``_clear_end_anchor``.

        Parameters:
            joints (List[str]): The hose joint chain.
            anchor (str): The transform the joint should follow.
            falloff (float): World-space distance over which anchor weight fades.
            joint_index (int): Index of the joint to constrain. Use 0 for start, -1 for end.
            profile (str): Falloff shape. Default ``"smoothstep"`` — C1 at the
                radius, so the blend zone meets the untouched weights without
                the derivative break (a visible crease ring) a ``"linear"``
                falloff leaves.

        Returns:
            str: The newly created anchor joint.
        """
        if not joints:
            self.logger.error("No joints provided.")
            return None

        anchor = str(anchor)
        anchor_pos = _TubeRigInternal._xform_t_ws(anchor)

        # Assign the anchor to the end it actually sits at. A crossed call
        # builds a rig that is correct at rest and tears off BOTH sockets as
        # soon as the anchor moves, so it must not be buildable.
        corrected = _TubeRigInternal._uncrossed_end_index(
            joints, anchor_pos, joint_index
        )
        if corrected != joint_index:
            self.logger.warning(
                f"constrain_end_with_falloff: '{CoreUtils.leaf_name(anchor)}' is "
                f"nearer the opposite end of the chain than joint index "
                f"{joint_index}; anchoring index {corrected} instead. Pass the "
                "anchor that sits at the end you name."
            )
            joint_index = corrected

        constrained_joint = str(joints[joint_index])
        prefix = self._end_anchor_prefix(joints, joint_index)

        # Resolve the skinCluster up front: the replace sweep needs it, and a
        # recorded name can be stale when the rig was rebuilt since.
        skin_cluster = (
            str(self.skin_cluster)
            if self.skin_cluster and cmds.objExists(str(self.skin_cluster))
            else None
        )
        if not skin_cluster:
            connected = (
                cmds.listConnections(
                    f"{constrained_joint}.worldMatrix[0]", type="skinCluster"
                )
                or []
            )
            skin_cluster = connected[0] if connected else None

        self._clear_end_anchor(prefix)

        # Create anchor joint at anchor location
        cmds.select(clear=True)
        anchor_joint = cmds.createNode("joint", name=f"{prefix}_jnt")
        cmds.setAttr(
            f"{anchor_joint}.translate",
            anchor_pos[0],
            anchor_pos[1],
            anchor_pos[2],
            type="double3",
        )
        cmds.setAttr(
            f"{anchor_joint}.radius",
            cmds.getAttr(f"{constrained_joint}.radius"),
        )
        cmds.makeIdentity(anchor_joint, apply=True, t=True, r=True, s=True)
        cmds.xform(anchor_joint, ws=True, t=anchor_pos)

        # Fully constrain anchor_joint to the anchor geo (position + orientation).
        # Named off the end prefix so the replace sweep can find it.
        cmds.parentConstraint(
            anchor, anchor_joint, mo=False, name=f"{prefix}_jnt_parentConstraint"
        )

        # Route the constraint through the rig's end CONTROL when one exists.
        # The chain joints are the wrong target on every built rig
        # (probe-verified): spline joints are IK-driven, so a direct
        # parentConstraint is silently overridden (and translating a spline
        # ikHandle is ignored outright by the solver); anchor joints already
        # carry control constraints, so a second driver raises 'Object is
        # already connected'; FK joints blend 50/50 against their control's
        # constraint. Driving the control moves the whole end assembly
        # (curve drivers, twist locators) coherently. Bare chains with no
        # controls keep the direct joint constraint.
        idx = joint_index % len(joints)
        target_ctrl = None
        if idx == 0:
            target_ctrl = self._end_control(0)
        elif idx == len(joints) - 1:
            target_ctrl = self._end_control(-1)
        if target_ctrl:
            # mo=True: follow the anchor's motion without snapping to it —
            # the falloff weighting below handles the contact region.
            cmds.parentConstraint(
                anchor_joint,
                target_ctrl,
                mo=True,
                name=f"{prefix}_target_parentConstraint",
            )
        else:
            cmds.parentConstraint(
                anchor_joint,
                constrained_joint,
                mo=False,
                name=f"{prefix}_target_parentConstraint",
            )

        # Add falloff skin weighting from anchor_joint to constrained_joint.
        if not skin_cluster:
            self.logger.warning(
                "constrain_end_with_falloff: no skinCluster found for "
                f"{constrained_joint}; skipping falloff weighting."
            )
        else:
            try:
                # No source_influence: the anchor takes w = 1 - d/falloff and
                # the REMAINDER redistributes across the vertex's existing
                # influences (skinPercent semantics). Blending against the
                # end joint alone would zero the solver's neighbor-joint
                # weights inside the radius and snap back to them at the
                # boundary — a visible crease in the constrained end's blend
                # zone. Redistribution is continuous at the boundary (w -> 0
                # leaves the row untouched) and keeps the smooth basis intact.
                #
                # The anchor joins a skin whose mesh is pinned at the BUILD
                # pose while the joints ride the module: register its bind
                # pose where it would have stood at build, or the vertices it
                # drives snap back to the rest frame the moment they are
                # weighted (10 units on a module moved 20, measured).
                geo = (cmds.skinCluster(skin_cluster, q=True, geometry=True) or [None])[
                    0
                ]
                bound_mesh = (
                    NodeUtils.get_parent(geo, type=None, full_path=True) or geo
                    if geo
                    else None
                )
                SkinUtils.add_influence(
                    skin_cluster,
                    anchor_joint,
                    bind_matrix=self._build_pose_world_matrix(anchor_joint, bound_mesh),
                )
                SkinUtils.apply_falloff(
                    skin_cluster,
                    target_influence=anchor_joint,
                    center=anchor_pos,
                    radius=falloff,
                    profile=profile,
                    add_influence=True,
                    undoable=True,
                )
                self.logger.debug(
                    f"Applied falloff weights from {anchor_joint} "
                    f"(joint index {joint_index}) over distance {falloff}"
                )
            except Exception as e:
                self.logger.warning(f"Skin weighting failed: {e}")

        # Ensure anchor joint is parented under the rig group
        rig_grp = str(self.rig_group)
        current_parent = NodeUtils.get_parent(anchor_joint, type=None, full_path=True)
        if not current_parent or CoreUtils.short_name(
            current_parent
        ) != CoreUtils.short_name(rig_grp):
            anchor_joint = _TubeRigInternal._parent_to(anchor_joint, rig_grp)

        DisplayUtils.add_to_isolation_set(anchor_joint)
        return anchor_joint


# ======================================================================
# Rig Configuration (UI Logic)
# ======================================================================


@dataclass
class RigModeConfig:
    """Defines a rig mode's strategy and available options."""

    name: str
    strategy: str

    num_joints: int
    num_controls: int
    enable_stretch: bool
    enable_squash: bool
    enable_volume: bool
    enable_auto_bend: bool
    enable_twist: bool

    # UI State (Default: Editable)
    num_joints_editable: bool = True
    num_controls_editable: bool = True
    stretch_editable: bool = True
    squash_editable: bool = True
    volume_editable: bool = True
    auto_bend_editable: bool = True
    twist_editable: bool = True


# Rig Mode Registry
RIG_MODES: List[RigModeConfig] = [
    RigModeConfig(
        name="Spline (Hose/Cable)",
        strategy="spline",
        num_joints=-1,
        num_controls=3,
        enable_stretch=True,
        enable_squash=True,
        enable_volume=True,
        enable_auto_bend=True,  # hoses bow on compression, not accordion
        enable_twist=True,
    ),
    RigModeConfig(
        name="Anchor (Piston/Hydraulic)",
        strategy="anchor",
        num_joints=2,
        num_controls=2,
        enable_stretch=True,
        enable_squash=False,
        enable_volume=False,
        enable_auto_bend=False,
        enable_twist=False,
        num_joints_editable=False,
        num_controls_editable=False,
        twist_editable=False,
        volume_editable=False,  # AnchorStrategy implements no volume system
        squash_editable=False,  # ...nor squash
        auto_bend_editable=False,  # ...nor a mid control to bend
    ),
    RigModeConfig(
        name="FK Chain (Tail/Tentacle)",
        strategy="fk",
        num_joints=-1,
        # A handful of controls, each spreading its rotation across the
        # joints it owns. One control per joint is technically FK and
        # practically unanimatable: an Auto build puts a joint on every edge
        # loop, so a single curve costs twenty-odd keys in lockstep and one
        # missed control corners the tube.
        num_controls=5,
        enable_stretch=False,
        enable_squash=False,
        enable_volume=False,
        enable_auto_bend=False,
        enable_twist=False,
        stretch_editable=False,
        squash_editable=False,
        volume_editable=False,
        auto_bend_editable=False,
        twist_editable=False,
    ),
]


# ======================================================================
# UI Slots (thin event handlers — delegates to TubeRig / TubePath)
# ======================================================================


class TubeRigSlots:
    def __init__(self, switchboard):
        self.sb = switchboard
        # Bind to the UI that corresponds to this slots class (tube_rig.ui)
        self.ui = self.sb.loaded_ui.tube_rig

        # Configure SpinBox custom display
        # -1 indicates "Auto": joint count derived from edge loops; joint size
        # derived from the measured tube radius.
        self.ui.s000.setCustomDisplayValues(-1, "Auto")
        self.ui.s002.setCustomDisplayValues(-1, "Auto")

        # Populate the mode combobox. The mode names are self-describing under the
        # "Global Options" group, so the combo carries no extra label: the old
        # setTextOverlay("Mode:") floated a translucent QLabel ON TOP of the item
        # text, so the two overlapped into an unreadable smear (reported bug) —
        # removed. (A display-only prefix can't stand in: QStyleSheetStyle paints a
        # themed combo's label from its own currentText, ignoring such adornments.)
        self.ui.cmb_preset.clear()
        for mode in RIG_MODES:
            self.ui.cmb_preset.addItem(mode.name, mode)

        self.ui.cmb_preset.currentIndexChanged.connect(self.apply_mode)
        # Apply initial mode
        if len(RIG_MODES) > 0:
            self.apply_mode(0)

        # Auto-Bend needs the 3-control spline layout — keep the checkbox
        # gated as the control count changes (apply_mode syncs it per mode).
        self.ui.s001.valueChanged.connect(self._sync_auto_bend_gate)

        self._init_tooltips()

        # Keep the window tall enough for the selected step. QToolBox wraps each
        # page in a QScrollArea whose minimum under-reports its content height, so
        # the window's show-time fit (which targets minimumSizeHint) leaves a taller
        # step (e.g. Step 2) clipped behind a scrollbar. Re-fit height on page change
        # to sizeHint, which DOES reflect the current page.
        self.ui.toolbox_steps.currentChanged.connect(self._fit_window_to_step)

    def _fit_window_to_step(self, *_) -> None:
        """Fit the window's height to the newly-selected toolbox page.

        Wired to ``toolbox_steps.currentChanged``. QToolBox scroll areas
        under-report their minimum height, so switching to a taller step would
        otherwise clip the page behind a scrollbar. Resize the height to
        ``sizeHint`` — which reflects the current page, unlike the
        ``minimumSizeHint`` the show-time ``fit_height_to_content`` targets —
        while preserving width, matching the window's own height-only resize
        helpers so a user-widened panel keeps its width (plain ``adjustSize``
        would snap it back). Deferred one event-loop tick so the page-switch
        layout has settled before the window re-measures.
        """
        win = self.ui.window()
        self.sb.QtCore.QTimer.singleShot(
            0, lambda: win.resize(win.width(), win.sizeHint().height())
        )

    def txt000_init(self, widget):
        """Rig-name field — optional, so clearing back to auto-naming is a state."""
        widget.option_box.clear_option = True

    def header_init(self, widget):
        """Configure header help text."""
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Tube Rig",
                body="Generate joint rigs along tube-shaped meshes. The tool "
                "auto-detects the tube's centerline via edge loops or surface "
                "normals, and sizes controls to the measured tube radius.",
                sections=[
                    (
                        "Quick start (One-Click)",
                        [
                            "Select a tube mesh.",
                            "Pick a <b>Mode</b> preset — irrelevant options "
                            "disable per mode.",
                            "Press <b>Full Rig</b> — it runs Steps 1 → 2 → 3 "
                            "with the parameter values set in each step page.",
                        ],
                    ),
                    (
                        "Step-by-step",
                        [
                            "<b>Step 1</b> creates the joints, <b>Step 2</b> "
                            "the controls, <b>Step 3</b> the skin bind — the "
                            "same operations One-Click runs, one at a time.",
                            "Each step's tooltip states exactly what to "
                            "select, and in what order.",
                        ],
                    ),
                    (
                        "Utility",
                        [
                            "<b>Add End Constraints</b> pins the tube ends to "
                            "anchor objects.",
                            "<b>Remove Rig</b> / <b>Rename Rig</b> act on "
                            "whatever rig the selection touches — also on rigs "
                            "built in an earlier session.",
                        ],
                    ),
                ],
                notes=[
                    "Every button is a <b>single undo</b> — one <b>Ctrl+Z</b> "
                    "reverts a whole build, bind or removal, batch runs "
                    "included. The viewport is held still while one runs; the "
                    "footer shows what it is doing.",
                    "<b>Joints = Auto</b> reads the tube's edge loops and "
                    "places one joint per loop.",
                    "Select several tubes to rig them in one go; a typed "
                    "name is suffixed <b>_01</b>, <b>_02</b>, …",
                    "Re-running a step replaces that step's previous result.",
                ],
            )
        )

    def _init_tooltips(self):
        """Set the polished (uitk ``fmt``) tooltips for every option and step."""
        ui = self.ui

        ui.cmb_preset.setToolTip(
            self.sb.tooltip.fmt(
                title="Rig Mode",
                body="Selects the build strategy and presets the step "
                "parameters below. Options a mode doesn't support are "
                "disabled while it is active.",
                sections=[
                    (
                        "Spline (Hose/Cable)",
                        [
                            "IK-spline chain with start / mid / end controls.",
                            "Good for: hoses, cables, organic tubes.",
                            "Supports: stretch, squash, volume, twist, auto-bend.",
                        ],
                    ),
                    (
                        "Anchor (Piston)",
                        [
                            "Two independent end joints with distance-driven stretch.",
                            "Good for: pistons, hydraulics, struts.",
                            "Always exactly 2 joints; stretch only.",
                        ],
                    ),
                    (
                        "FK Chain (Tail/Tentacle)",
                        [
                            "Nested FK controls, one per joint.",
                            "Good for: tails, tentacles, hand-keyed tubes.",
                        ],
                    ),
                ],
            )
        )
        ui.txt000.setToolTip(
            self.sb.tooltip.fmt(
                title="Rig Name",
                body="Base name for every node the rig creates (group, "
                "joints, controls, skinCluster).",
                notes=["Empty = derived from the mesh name."],
            )
        )
        ui.s000.setToolTip(
            self.sb.tooltip.fmt(
                title="Number of Joints",
                body="Joint count along the tube centerline.",
                rows=[
                    ("Auto", "one joint per edge loop (most precise)"),
                    ("N", "evenly resampled along the centerline"),
                ],
                notes=["Anchor rigs always use exactly 2 end joints."],
            )
        )
        ui.s001.setToolTip(
            self.sb.tooltip.fmt(
                title="Number of Controls",
                body="Driver control count for the Spline rig. Increase for "
                "complex shapes.",
                notes=[
                    "<b>Auto-Bend</b> requires exactly 3 controls.",
                    "FK rigs create one control per joint instead.",
                ],
            )
        )
        ui.s002.setToolTip(
            self.sb.tooltip.fmt(
                title="Joint Size",
                body="Joint display radius in the viewport.",
                rows=[("Auto", "half the measured tube radius")],
                notes=[
                    "Display only — control sizes always scale to the "
                    "measured tube radius."
                ],
            )
        )
        ui.chk000.setToolTip(
            self.sb.tooltip.fmt(
                title="Reverse Direction",
                body="Builds the joint chain from the far end (swaps start/end).",
                notes=["Applies to Step 1 and the One-Click build."],
            )
        )
        ui.chk_stretch.setToolTip(
            self.sb.tooltip.fmt(
                title="Stretch",
                bullets=[
                    "<b>Spline:</b> joints scale along the tube to follow "
                    "the curve length.",
                    "<b>Anchor:</b> the start joint stretches toward the end control.",
                ],
            )
        )
        ui.chk_twist.setToolTip(
            self.sb.tooltip.fmt(
                title="Twist",
                body="Advanced spline twist driven by the start/end control rotation.",
                notes=["Spline only. Adds a <b>roll</b> attribute to the end control."],
            )
        )
        ui.chk_squash.setToolTip(
            self.sb.tooltip.fmt(
                title="Squash",
                body="Joints compress when the curve shortens.",
                notes=["Spline only."],
            )
        )
        ui.chk_volume.setToolTip(
            self.sb.tooltip.fmt(
                title="Volume Preservation",
                body="Bulges when squashed, thins when stretched.",
                notes=["Spline only."],
            )
        )
        ui.chk_auto_bend.setToolTip(
            self.sb.tooltip.fmt(
                title="Auto-Bend (Mid)",
                body="The mid control bows outward automatically as the ends "
                "compress toward each other.",
                notes=["Spline only — requires exactly <b>3</b> controls."],
            )
        )
        ui.b001.setToolTip(
            self.sb.tooltip.fmt(
                title="Step 1 — Create Joints",
                body="Places this rig's joints along the tube's centerline.",
                steps=[
                    "Select the tube mesh <i>(or an edge loop running down it)</i>.",
                    "Press <b>Create Joints</b>.",
                ],
                notes=[
                    "Anchor mode creates its 2 end joints instead of a chain.",
                    "Re-running replaces this rig's previous joints.",
                ],
            )
        )
        ui.b002.setToolTip(
            self.sb.tooltip.fmt(
                title="Step 2 — Create Controls",
                body="Builds the active mode's control rig on the joints from Step 1.",
                steps=[
                    "Select the root joint <i>(Anchor: either or both end joints)</i>.",
                    "Press <b>Create IK / Controls</b>.",
                ],
                sections=[
                    (
                        "Creates",
                        [
                            "<b>Spline:</b> IK spline, start/mid/end controls, "
                            "twist, stretch, auto-bend.",
                            "<b>Anchor:</b> two end controls with distance stretch.",
                            "<b>FK:</b> nested FK controls, one per joint.",
                        ],
                    ),
                ],
                notes=["Control size is proportional to the tube's radius."],
            )
        )
        ui.b003.setToolTip(
            self.sb.tooltip.fmt(
                title="Step 3 — Bind Skin",
                body="Smooth-binds the tube mesh to the joints with "
                "ring-uniform parametric weights — the same solver the "
                "One-Click build uses.",
                steps=[
                    "Select the root joint.",
                    "<b>Shift</b>-select the tube mesh <i>last</i>.",
                    "Press <b>Bind Joints to Mesh</b>.",
                ],
                notes=["Re-running replaces the mesh's existing skinCluster."],
            )
        )
        ui.b004.setToolTip(
            self.sb.tooltip.fmt(
                title="Constrain Ends to Anchors",
                body="Constrains one or both tube ends to external anchor "
                "objects (each end's control follows its anchor) with "
                "distance-falloff skin weighting at the contact points.",
                steps=[
                    "Select the root joint.",
                    "<b>Shift</b>-select the anchor object(s) — one per end to "
                    "constrain.",
                    "Press <b>Add End Constraints</b>.",
                ],
                notes=[
                    "Requires a bound tube — run <b>Step 3</b> first.",
                    "Each anchor constrains its nearest tube end — selection "
                    "order doesn't matter. A single anchor leaves the other end "
                    "free (a plugged-in loom whose base is part of the module).",
                    "Falloff spans ≈2× the tube radius.",
                ],
            )
        )
        ui.b005.setToolTip(
            self.sb.tooltip.fmt(
                title="Remove Rig",
                body="Deletes the rig — joints, controls, IK, utility nodes and "
                "the skin bind — and restores the mesh's viewport display. The "
                "tube mesh itself is kept.",
                steps=[
                    "Select the tube mesh, or any joint / control of the rig(s).",
                    "Press <b>Remove Rig</b>.",
                ],
                notes=[
                    "Works on rigs built in an earlier session — the rig is "
                    "read back from the scene.",
                    "Several rigs selected are removed together.",
                    "<b>Ctrl+Z</b> puts the rig back — bind included — in one "
                    "step, however many rigs were removed.",
                ],
            )
        )
        ui.b006.setToolTip(
            self.sb.tooltip.fmt(
                title="Rename Rig",
                body="Renames every node of the rig — group, joints, controls, "
                "sets and utility nodes — to the name in the <b>Rig Name</b> "
                "field.",
                steps=[
                    "Type the new name in <b>Rig Name</b>.",
                    "Select the tube mesh, or any joint / control of the rig.",
                    "Press <b>Rename Rig</b>.",
                ],
                notes=[
                    "One rig at a time.",
                    "Referenced rigs can't be renamed — rename them in their "
                    "source scene.",
                ],
            )
        )
        ui.b007.setToolTip(
            self.sb.tooltip.fmt(
                title="Rebind Skin",
                body="Re-solves the tube's bind from the rig already in the "
                "scene. Use it when the mesh stopped following the controls "
                "but the joints and controls are still there — a UV unwrap, "
                "<b>Delete History</b> or <b>Bake Non-Deformer History</b> on "
                "the tube removes the skinCluster and leaves everything else "
                "standing.",
                steps=[
                    "Select the tube mesh, or any joint / control of the rig.",
                    "Press <b>Rebind Skin</b>.",
                ],
                sections=[
                    (
                        "Older rigs",
                        [
                            "A rig built before the scene record existed is "
                            "linked to its tube only THROUGH the bind, so once "
                            "the bind is gone the tube looks like plain "
                            "geometry.",
                            "Select the tube <b>and</b> one of the rig's joints "
                            "or controls together — one rig, one tube at a time.",
                            "Only needed once: the rebind stamps the record.",
                        ],
                    ),
                ],
                notes=[
                    "Nothing has to have been saved — the weights are solved "
                    "from the centerline, so the rebind reproduces the "
                    "original bind.",
                    "Joints, controls and their animation are untouched "
                    "(unlike <b>One-Click Rig</b>, which rebuilds from "
                    "scratch).",
                    "Rebind at the rig's DEFAULT pose: a bind destroyed while "
                    "the rig was posed has already baked that pose into the "
                    "mesh, and no rebind can undo that.",
                ],
            )
        )
        ui.b000.setToolTip(
            self.sb.tooltip.fmt(
                title="One-Click Rig",
                body="Runs <b>Step 1 → Step 2 → Step 3</b> in order, using "
                "the parameter values set in each step's page.",
                steps=[
                    "Select the tube mesh <i>(or an edge loop running down it)</i>.",
                    "Press <b>Full Rig</b>.",
                ],
                notes=[
                    "Rebuilding on an already-rigged mesh tears the old rig "
                    "down first.",
                    "<b>Ctrl+Z</b> reverts the whole run in one step — the "
                    "teardown of a previous rig included.",
                ],
            )
        )

    def _sync_auto_bend_gate(self, *_):
        """Gate Auto-Bend on the 3-control spline layout."""
        mode = self.get_mode()
        allowed = bool(
            mode and mode.auto_bend_editable and int(self.ui.s001.value()) == 3
        )
        self.ui.chk_auto_bend.setEnabled(allowed)
        if not allowed:
            self.ui.chk_auto_bend.setChecked(False)

    def apply_mode(self, index: int):
        """Apply mode values and constraints to UI widgets."""
        mode = self.ui.cmb_preset.itemData(index)
        if not mode:
            # Fallback if somehow data is missing or index invalid (shouldn't happen with correct usage)
            mode = RIG_MODES[0] if RIG_MODES else None

        if not mode:
            return

        # Step 1: Joints
        self.ui.s000.setValue(mode.num_joints)
        self.ui.s000.setEnabled(mode.num_joints_editable)

        # Step 1.5: Controls Count
        self.ui.s001.setValue(mode.num_controls)
        self.ui.s001.setEnabled(mode.num_controls_editable)

        # Step 2: Controls
        self.ui.chk_stretch.setChecked(mode.enable_stretch)
        self.ui.chk_stretch.setEnabled(mode.stretch_editable)

        self.ui.chk_squash.setChecked(mode.enable_squash)
        self.ui.chk_squash.setEnabled(mode.squash_editable)

        self.ui.chk_volume.setChecked(mode.enable_volume)
        self.ui.chk_volume.setEnabled(mode.volume_editable)

        self.ui.chk_auto_bend.setChecked(mode.enable_auto_bend)
        self.ui.chk_auto_bend.setEnabled(mode.auto_bend_editable)

        self.ui.chk_twist.setChecked(mode.enable_twist)
        self.ui.chk_twist.setEnabled(mode.twist_editable)

        self._sync_auto_bend_gate()

    def get_mode(self) -> RigModeConfig:
        """Get the current rig mode config."""
        mode = self.ui.cmb_preset.currentData()
        return mode if mode else (RIG_MODES[0] if RIG_MODES else None)

    def get_strategy(self) -> str:
        """Get the current strategy from the mode combobox."""
        return self.get_mode().strategy

    @staticmethod
    def _unique_auto_rig_name(leaf: str) -> str:
        """An unused ``<leaf>_RIG`` for a rig the user did not name.

        ``short_name`` drops the DAG path AND the namespace, so two tubes in
        one batch -- ``machineA:hose`` / ``machineB:hose``, or plain duplicates
        under different parents -- derive the same name. Left alone, tube 2's
        ``build()`` finds tube 1's ``<name>_GRP``, tears it down (taking its
        skinCluster, joints and controls) and the summary still reports both as
        built, leaving tube 1's mesh unbound and display-locked.

        Only the DERIVED name is uniquified: a name the artist typed still
        means "rebuild that rig", which is the rerun path.
        """
        base = f"{leaf}_RIG"
        name, i = base, 1
        while cmds.objExists(f"{name}_GRP"):
            name = f"{base}_{i:02d}"
            i += 1
        return name

    def get_tube_rig(self, obj, rig_name: Optional[str] = None):
        """Get the tube rig instance for the given object (the mesh, a joint,
        a control, or anything under the rig group); create one if none exists.

        *rig_name* names a NEW rig (a batch build's per-tube name); default
        is the Rig Name field, else an unused ``<mesh>_RIG``. An existing rig
        keeps its own name.
        """
        if obj is None:
            return None
        # for_node resolves raw nodes itself — pre-resolving a joint through
        # get_transform_node yields a *list*, which defeats the lookup.
        rig = TubeRig.for_node(str(obj))
        if rig is not None:
            return rig

        # New rig: bind to the mesh transform when one resolves (tolerates
        # group picks); otherwise keep the plain transform (b002 constructs
        # from a joint after a restart, when the registry is empty).
        shape = TubePath._resolve_mesh_shape(obj)
        if shape:
            target = NodeUtils.get_parent(shape, type=None, full_path=True) or str(
                shape
            )
        else:
            target = NodeUtils.get_transform_node(str(obj)) or str(obj)
            if isinstance(target, (set, list, tuple)):
                target = next(iter(target), str(obj))
        rig_name = rig_name or self.ui.txt000.text()
        if not rig_name:
            rig_name = self._unique_auto_rig_name(CoreUtils.short_name(target))
        return TubeRig(target, rig_name=rig_name)

    def _batch_targets(self) -> List[str]:
        """The tube meshes a build button acts on.

        Object-mode picks rig in batch (one rig per selected object). An
        edge selection is inherently single-tube — it names the path on ONE
        mesh — so it resolves to just that mesh.
        """
        objs = cmds.ls(selection=True, objectsOnly=True, flatten=True) or []
        if cmds.filterExpand(selectionMask=32):
            return objs[:1]
        return list(dict.fromkeys(objs))

    def _batch_names(self, objs: List[str]) -> List[Optional[str]]:
        """Per-object rig names for a batch: the typed name suffixed ``_01``,
        ``_02``, ... when several tubes share it; None (the per-mesh auto
        name) when the field is empty."""
        typed = self.ui.txt000.text().strip()
        if not typed or len(objs) == 1:
            return [typed or None] * len(objs)
        return [f"{typed}_{i + 1:02d}" for i in range(len(objs))]

    def _selected_rigs(self) -> List[TubeRig]:
        """Distinct rigs the selection touches (mesh, joint, control, group)
        — resolved from the scene record when the session registry has no
        entry (a rig built before a restart)."""
        rigs: List[TubeRig] = []
        for obj in cmds.ls(selection=True, objectsOnly=True, flatten=True) or []:
            rig = TubeRig.for_node(obj)
            if rig is not None and rig not in rigs:
                rigs.append(rig)
        return rigs

    @staticmethod
    def _batch_summary(what: str, done: List[str], failed: List[str]) -> str:
        lines = []
        if done:
            lines.append(f"{what}: {', '.join(done)}")
        if failed:
            lines.append("Failed:\n  " + "\n  ".join(failed))
        return "\n".join(lines)

    @staticmethod
    def _cancel_note(remaining: int) -> str:
        """Say so when the user cancelled a batch — and that undo is still one step.

        The whole run shares one undo chunk, so a half-finished batch is not a
        mess to clean up by hand; saying that is the difference between a
        cancel the user trusts and one they don't.

        Parameters:
            remaining (int): Items the run never reached. ``0`` (a completed
                run) yields no note. Counted from the loop index rather than
                derived from the result lists, so an item that FAILED is not
                also counted as skipped.
        """
        if remaining <= 0:
            return ""
        return (
            f"<br><br>Cancelled with {remaining} left — Ctrl+Z reverts the "
            "whole run in one step."
        )

    def _expand_step_joints(self, joints: List[str]) -> List[str]:
        """Expand a single joint to its full rig joint set (b002/b003/b004).

        A single joint expands to its chain; for Anchor rigs (sibling end
        joints, no chain) it expands to the joints sharing its parent group.
        """
        joints = [str(j) for j in joints]
        if len(joints) != 1:
            return joints
        if self.get_strategy() == "anchor":
            parent = NodeUtils.get_parent(joints[0], type=None, full_path=True)
            siblings = (
                cmds.listRelatives(parent, children=True, type="joint", fullPath=True)
                if parent
                else None
            )
            if siblings and len(siblings) == 2:
                return [str(j) for j in siblings]
            return joints
        return [str(j) for j in RigUtils.get_joint_chain_from_root(joints[0])]

    def _selected_step_joints(self) -> List[str]:
        """The selected joints for a step operation, chain-expanded."""
        sel = cmds.ls(selection=True, flatten=True) or []
        return self._expand_step_joints(cmds.ls(sel, type="joint", flatten=True) or [])

    def _existing_controls(self, tube_rig) -> Optional[str]:
        """First pre-existing control-rig node for *tube_rig*, or None.

        Delegates control lookup to ``TubeRig._end_control`` — the SSoT for
        the builders' naming conventions (a hand-rolled name check here
        previously tested ``<rig>_start``, which never exists: the builders
        suffix ``_CTRL``, so leftover anchor controls went undetected).
        """
        ik = f"{tube_rig.rig_name}_ikHandle"
        if cmds.objExists(ik):
            return ik
        return tube_rig._end_control(0) or tube_rig._end_control(-1)

    def _phase_hook(self, update: Callable, prefix: str = "") -> Callable:
        """A ``TubeRig`` progress hook that writes phase text to the footer.

        Every rig operation runs with the viewport suspended (one undo step,
        no per-command redraw), so the footer is the only thing telling the
        user the tool is working rather than hung. The engine reports phases
        indeterminately, which is why the bar is a marquee: a rig's phase
        count varies with the strategy and the options, so a percentage would
        be a fiction.

        Parameters:
            update: The footer's ``update(value, text)`` callable.
            prefix: Prepended to every phase message — carries a batch run's
                "[2/5] " counter, which the engine cannot know about.
        """
        adapted = self.sb.progress_adapter(update)
        if not prefix:
            return adapted

        def prefixed(current=None, total=0, message=None) -> bool:
            return adapted(current, total, f"{prefix}{message}" if message else message)

        return prefixed

    @staticmethod
    def _batch_prefix(index: int, total: int) -> str:
        """Progress-line prefix: ``[2/5]`` for a multi-item run, empty for one."""
        return f"[{index + 1}/{total}] " if total > 1 else ""

    def create_joints_from_tube(self, obj, rig_name: Optional[str] = None):
        """Step 1 — create this rig's joints from the tube mesh (mode-aware)."""
        strategy = self.get_strategy()
        num_joints = 2 if strategy == "anchor" else self.ui.s000.value()
        edges = cmds.filterExpand(selectionMask=32)  # optional user edge selection

        tube_rig = self.get_tube_rig(obj, rig_name=rig_name)
        try:
            centerline, num_joints = tube_rig.resolve_centerline(
                num_joints, edges=edges
            )
        except ValueError as e:  # e.g. selection resolves to no polygon mesh
            self.sb.message_box(str(e))
            return []

        if not centerline or len(centerline) < 2:
            self.sb.message_box(
                "Failed to extract a valid centerline from the tube mesh."
            )
            return []

        if self.ui.chk000.isChecked():
            centerline = list(centerline)[::-1]

        joint_radius, _ = tube_rig.resolve_sizes(centerline, self.ui.s002.value())
        if strategy == "anchor":
            return tube_rig.create_anchor_joints(centerline, radius=joint_radius)
        return tube_rig.generate_joint_chain(
            centerline=centerline, num_joints=num_joints, radius=joint_radius
        )

    @CoreUtils.undoable(name="Tube Rig: Full Rig", suspend_refresh=True)
    def b000(self):
        """One-Click Rig — runs Steps 1 → 2 → 3 with the step parameters,
        once per selected tube."""
        objs = self._batch_targets()
        if not objs:
            self.sb.message_box("Select one or more polygon tube meshes to rig.")
            return

        strategy = self.get_strategy()
        edges = cmds.filterExpand(selectionMask=32)
        built, failed = [], []
        skipped = 0
        names = self._batch_names(objs)
        with self.ui.footer.progress(text="Tube Rig: building…") as update:
            for i, (obj, rig_name) in enumerate(zip(objs, names)):
                prefix = self._batch_prefix(i, len(objs))
                if not update(None, f"{prefix}Rigging {CoreUtils.leaf_name(obj)}…"):
                    skipped = len(objs) - i
                    break
                tube_rig = self.get_tube_rig(obj, rig_name=rig_name)
                try:
                    tube_rig.build(
                        strategy=strategy,
                        progress=self._phase_hook(update, prefix),
                        num_joints=self.ui.s000.value(),
                        num_controls=self.ui.s001.value(),
                        radius=self.ui.s002.value(),
                        reverse=self.ui.chk000.isChecked(),
                        edges=edges,
                        enable_stretch=self.ui.chk_stretch.isChecked(),
                        enable_squash=self.ui.chk_squash.isChecked(),
                        enable_volume=self.ui.chk_volume.isChecked(),
                        enable_auto_bend=self.ui.chk_auto_bend.isChecked(),
                        enable_twist=self.ui.chk_twist.isChecked(),
                    )
                    built.append(tube_rig.rig_name)
                except Exception as e:
                    failed.append(f"{CoreUtils.leaf_name(obj)}: {e}")
                    self.sb.logger.error(f"Build Error ({obj}): {e}", exc_info=True)
        self.sb.message_box(
            self._batch_summary(f"Tube rig ({strategy}) created", built, failed)
            + self._cancel_note(skipped)
        )

    @CoreUtils.undoable(name="Tube Rig: Create Joints", suspend_refresh=True)
    def b001(self):
        """Step 1: Create Joints from Tube — once per selected tube."""
        objs = self._batch_targets()
        if not objs:
            self.sb.message_box(
                "Select the tube mesh (or an edge loop on it) to create joints."
            )
            return

        done = []
        skipped = 0
        names = self._batch_names(objs)
        with self.ui.footer.progress(text="Tube Rig: creating joints…") as update:
            for i, (obj, rig_name) in enumerate(zip(objs, names)):
                prefix = self._batch_prefix(i, len(objs))
                if not update(
                    None, f"{prefix}Creating joints on {CoreUtils.leaf_name(obj)}…"
                ):
                    skipped = len(objs) - i
                    break
                joints = self.create_joints_from_tube(obj, rig_name=rig_name)
                if joints:  # failures already message-boxed their reason
                    done.append(
                        f"{len(joints)} ({CoreUtils.leaf_name(obj)})"
                        if len(objs) > 1
                        else str(len(joints))
                    )
        if done:
            self.sb.message_box(
                f"Joints created: {', '.join(done)}" + self._cancel_note(skipped)
            )
        elif skipped:  # cancelled before the first tube — silence reads as broken
            self.sb.message_box(f"No joints created.{self._cancel_note(skipped)}")

    @CoreUtils.undoable(name="Tube Rig: Create Controls", suspend_refresh=True)
    def b002(self):
        """Step 2: Create IK / Controls (mode dependent)."""
        strategy = self.get_strategy()

        joints = self._selected_step_joints()
        if not joints:
            self.sb.message_box(
                "Select the root joint created in Step 1.\n"
                "(Anchor: either or both end joints.)"
            )
            return
        if strategy == "anchor" and len(joints) != 2:
            self.sb.message_box(
                f"Anchor rigs use exactly 2 end joints (got {len(joints)}).\n"
                "Run Step 1 in Anchor mode to create them."
            )
            return
        if strategy == "spline" and len(joints) < 2:
            self.sb.message_box(
                "Spline IK needs a chain of at least 2 joints — select its root."
            )
            return

        tube_rig = self.get_tube_rig(joints[0])
        tube_rig.joints = joints  # step-created or manual chains alike

        existing = self._existing_controls(tube_rig)
        if existing:
            self.sb.message_box(
                f"Controls already exist for '{tube_rig.rig_name}' ({existing}).\n"
                "Delete the previous controls or rebuild with One-Click Rig."
            )
            return

        # Control size follows the measured tube radius (falls back to the
        # joints' display radii when the rig has no resolvable mesh).
        _, size = tube_rig.resolve_sizes(joint_radius=self.ui.s002.value())

        try:
            with self.ui.footer.progress(
                text=f"Tube Rig: creating controls on {len(joints)} joints…"
            ) as update:
                update()
                if strategy == "spline":
                    controls, _, _ = tube_rig.create_spline_controls(
                        joints,
                        size=size,
                        num_controls=self.ui.s001.value(),
                        enable_stretch=self.ui.chk_stretch.isChecked(),
                        enable_squash=self.ui.chk_squash.isChecked(),
                        enable_volume=self.ui.chk_volume.isChecked(),
                        enable_twist=self.ui.chk_twist.isChecked(),
                        enable_auto_bend=self.ui.chk_auto_bend.isChecked(),
                    )
                    kind = "Spline IK"
                elif strategy == "anchor":
                    controls = tube_rig.create_anchor_controls(
                        joints,
                        size=size,
                        enable_stretch=self.ui.chk_stretch.isChecked(),
                    )
                    kind = "Anchor"
                else:
                    controls = tube_rig.create_fk_controls(joints, size=size)
                    kind = "FK"
        except ValueError as e:
            self.sb.message_box(str(e))
            return

        ctrl_names = ", ".join(CoreUtils.leaf_name(c) for c in controls)
        self.sb.message_box(
            f"{kind} controls created on {len(joints)} joints.\nControls: {ctrl_names}"
        )

    @CoreUtils.undoable(name="Tube Rig: Bind Skin", suspend_refresh=True)
    def b003(self):
        """Step 3: Bind Joint Chain to Tube."""
        sel = cmds.ls(selection=True, flatten=True) or []
        if len(sel) < 2:
            self.sb.message_box(
                "Select the root joint, then Shift-select the tube mesh last.\n"
                "Usage: [Root Joint] → [Tube Mesh]"
            )
            return
        obj = sel[-1]

        if not TubePath._resolve_mesh_shape(obj):
            self.sb.message_box(
                f"'{CoreUtils.leaf_name(obj)}' is not a polygon mesh — select the tube "
                "mesh last."
            )
            return

        joints = self._selected_step_joints()
        if len(joints) < 2:
            self.sb.message_box(
                "Select the root joint of the chain (at least 2 joints), then "
                "the tube mesh."
            )
            return

        tube_rig = self.get_tube_rig(obj)
        with self.ui.footer.progress(
            text=f"Tube Rig: binding {CoreUtils.leaf_name(obj)} to "
            f"{len(joints)} joints…"
        ) as update:
            update()
            skin_cluster = tube_rig.bind_joint_chain(obj, joints)
        if not skin_cluster:
            self.sb.message_box(
                "Failed to bind the joint chain — see the Script Editor for details."
            )
            return
        self.sb.message_box(
            f"Skinned '{CoreUtils.leaf_name(obj)}' to {len(joints)} joints "
            f"({CoreUtils.leaf_name(skin_cluster)})."
        )

    @CoreUtils.undoable(name="Tube Rig: End Constraints", suspend_refresh=True)
    def b004(self):
        """Utility: Constrain Ends to Anchors — one anchor or both.

        Each anchor constrains its NEAREST tube end. A single anchor leaves
        the other end free (a plugged-in loom whose base is part of the module
        the rig rides). Two anchors must sit at different ends: forcing a pair
        onto both ends and leaving the primitive's own nearest-end guard to
        override one of them silently replaced the first anchor with the
        second (production wire looms — the base object's pivot sat nearer the PLUG
        end), so that case is refused with the reason instead.
        """
        sel = cmds.ls(selection=True, flatten=True) or []
        root = cmds.ls(sel[:1], type="joint", flatten=True) or []
        anchors = [s for s in sel[1:] if not cmds.ls(s, type="joint")]
        if not root or not anchors:
            self.sb.message_box(
                "Select the root joint, then the anchor object for each tube end "
                "to constrain (one or two)."
            )
            return
        if len(anchors) > 2:
            self.sb.message_box(
                f"A tube has two ends — got {len(anchors)} anchors. Select one "
                "anchor per end to constrain."
            )
            return
        joints = self._expand_step_joints([root[0]])
        if len(joints) < 2:
            self.sb.message_box(
                "Could not derive the joint chain from the selection — "
                "select the rig's ROOT joint (the chain start)."
            )
            return

        tube_rig = self.get_tube_rig(joints[0])

        # Falloff weighting needs a bound mesh — fail up front with the fix.
        bound = tube_rig.skin_cluster or (
            cmds.listConnections(f"{joints[0]}.worldMatrix[0]", type="skinCluster")
        )
        if not bound:
            self.sb.message_box(
                "The joints aren't bound to a mesh yet — run Step 3 (Bind Skin) first."
            )
            return

        # Each anchor to its nearest end — selection order can't cross the
        # constraints, and two anchors off one end are refused rather than
        # silently collapsed onto it.
        p_start = om.MVector(*_TubeRigInternal._xform_t_ws(joints[0]))
        p_end = om.MVector(*_TubeRigInternal._xform_t_ws(joints[-1]))
        nearest = []
        for anchor in anchors:
            a = om.MVector(*_TubeRigInternal._xform_t_ws(anchor))
            nearest.append(0 if (a - p_start).length() <= (a - p_end).length() else -1)
        if len(anchors) == 2 and nearest[0] == nearest[1]:
            which = "start" if nearest[0] == 0 else "end"
            self.sb.message_box(
                f"Both anchors sit nearest the tube's {which} end "
                f"({CoreUtils.leaf_name(anchors[0])}, "
                f"{CoreUtils.leaf_name(anchors[1])}).\n"
                "Select one anchor per end — or a single anchor to constrain "
                "just that end."
            )
            return

        # Falloff proportional to the tube: ≈2× its radius.
        _, size = tube_rig.resolve_sizes(joint_radius=self.ui.s002.value())
        falloff = size * 2.0

        lines = []
        with self.ui.footer.progress(text="Tube Rig: constraining ends…") as update:
            for anchor, idx in zip(anchors, nearest):
                end = "start" if idx == 0 else "end"
                update(None, f"Constraining {end} to {CoreUtils.leaf_name(anchor)}…")
                result = tube_rig.constrain_end_with_falloff(
                    joints, anchor, falloff=falloff, joint_index=idx
                )
                lines.append(
                    f"  {end} <- {CoreUtils.leaf_name(anchor)}: "
                    f"{CoreUtils.leaf_name(result) if result else 'failed'}"
                )
        self.sb.message_box("End constraints added:\n" + "\n".join(lines))

    @CoreUtils.undoable(name="Tube Rig: Remove", suspend_refresh=True)
    def b005(self):
        """Utility: Remove Rig — tear down every rig the selection touches."""
        rigs = self._selected_rigs()
        if not rigs:
            self.sb.message_box(
                "Select the tube mesh, or any joint / control of the rig(s) to remove."
            )
            return
        removed, failed = [], []
        skipped = 0
        with self.ui.footer.progress(text="Tube Rig: removing…") as update:
            for i, rig in enumerate(rigs):
                prefix = self._batch_prefix(i, len(rigs))
                if not update(None, f"{prefix}Removing {rig.rig_name}…"):
                    skipped = len(rigs) - i
                    break
                try:
                    rig.teardown(progress=self._phase_hook(update, prefix))
                    removed.append(rig.rig_name)
                except Exception as e:
                    failed.append(f"{rig.rig_name}: {e}")
                    self.sb.logger.error(
                        f"Remove Error ({rig.rig_name}): {e}", exc_info=True
                    )
        self.sb.message_box(
            self._batch_summary("Rig removed", removed, failed)
            + self._cancel_note(skipped)
        )

    @CoreUtils.undoable(name="Tube Rig: Rename", suspend_refresh=True)
    def b006(self):
        """Utility: Rename Rig — to the Rig Name field, every node included."""
        new_name = self.ui.txt000.text().strip()
        if not new_name:
            self.sb.message_box("Type the new name in the Rig Name field first.")
            return
        rigs = self._selected_rigs()
        if len(rigs) != 1:
            self.sb.message_box(
                "Select the tube mesh, or any joint / control of ONE rig to rename."
                if not rigs
                else f"Select one rig at a time (got {len(rigs)})."
            )
            return
        old = rigs[0].rig_name
        try:
            with self.ui.footer.progress(
                text=f"Tube Rig: renaming {old} → {new_name}…"
            ) as update:
                update()
                renamed = rigs[0].rename(new_name)
        except ValueError as e:
            self.sb.message_box(str(e))
            return
        if renamed == old:
            self.sb.message_box(f"Rig name unchanged: {old}")
            return
        self.sb.message_box(f"Rig renamed: {old} -> {renamed}")

    def _orphan_meshes(self) -> List[str]:
        """Selected meshes that resolve to no rig.

        A rig built before the scene record existed is reachable from its mesh
        only through the skinCluster's first influence, so once the bind is
        destroyed — the very thing Rebind Skin repairs — the tube looks like
        plain geometry. Pairing it with a selected joint / control is the only
        way back in.
        """
        orphans = []
        for obj in cmds.ls(selection=True, objectsOnly=True, flatten=True) or []:
            if not TubePath._resolve_mesh_shape(obj):
                continue
            if TubeRig.for_node(obj) is not None:
                continue
            path = NodeUtils.get_transform_node(obj)
            if isinstance(path, (list, tuple, set)):
                path = next(iter(path), None)
            if path and str(path) not in orphans:
                orphans.append(str(path))
        return orphans

    @CoreUtils.undoable(name="Tube Rig: Rebind Skin", suspend_refresh=True)
    def b007(self):
        """Utility: Rebind Skin — re-solve the bind for every rig the selection touches."""
        rigs = self._selected_rigs()
        orphans = self._orphan_meshes()

        if not rigs:
            self.sb.message_box(
                "Select the tube mesh, or any joint / control of the rig(s) to rebind."
                + (
                    "\n\nThe selected mesh isn't linked to any rig — if its bind "
                    "was already destroyed, Shift-select one of the rig's joints "
                    "or controls as well."
                    if orphans
                    else ""
                )
            )
            return

        # Legacy rescue: one rig + one unlinked mesh is an unambiguous pairing.
        # More than one of either is not, and guessing would bind the wrong
        # tube — make the user disambiguate instead.
        pair_mesh = None
        if orphans:
            if len(rigs) == 1 and len(orphans) == 1:
                pair_mesh = orphans[0]
            else:
                self.sb.message_box(
                    f"Can't tell which mesh belongs to which rig "
                    f"({len(rigs)} rig(s), {len(orphans)} unlinked mesh(es)).\n"
                    "Rebind one rig at a time: select its tube plus one of its "
                    "joints or controls."
                )
                return

        rebound, failed = [], []
        skipped = 0
        with self.ui.footer.progress(text="Tube Rig: rebinding skin…") as update:
            for i, rig in enumerate(rigs):
                prefix = self._batch_prefix(i, len(rigs))
                if not update(None, f"{prefix}Rebinding {rig.rig_name}…"):
                    skipped = len(rigs) - i
                    break
                try:
                    rig.rebind_skin(mesh=pair_mesh)
                    rebound.append(rig.rig_name)
                except Exception as e:
                    failed.append(f"{rig.rig_name}: {e}")
                    self.sb.logger.error(
                        f"Rebind Error ({rig.rig_name}): {e}", exc_info=True
                    )
        self.sb.message_box(
            self._batch_summary("Skin rebound", rebound, failed)
            + self._cancel_note(skipped)
        )

    # -----------------------------------------------------------------------------


if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("tube_rig", reload=True)
    ui.header.config_buttons("hide")
    ui.show(pos="screen", app_exec=True)

# -----------------------------------------------------------------------------
# Notes
# -----------------------------------------------------------------------------
