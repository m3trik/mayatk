#!/usr/bin/env python
# coding=utf-8
import json
from dataclasses import dataclass, field, fields, asdict
from typing import Dict, List, Optional, Tuple, Union

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError as error:
    print(__file__, error)

import pythontk as ptk
from mayatk.core_utils._core_utils import CoreUtils
from mayatk.edit_utils.naming._naming import Naming
from mayatk.xform_utils._xform_utils import XformUtils


@dataclass
class TelescopeRigBundle:
    """Record of everything one ``setup_telescope_rig`` build created.

    Returned by ``setup_telescope_rig`` and consumed by ``teardown`` — the
    exact node names are captured at creation time, so removal never has to
    guess by name pattern. Also round-trips through JSON so the record can be
    stamped onto the base locator and recovered in a later session.
    """

    name: str
    base_locator: str
    end_locator: str
    segments: List[str]
    scale_attr: str
    initial_distance: float
    collapsed_distance: float
    distance_node: Optional[str] = None
    constraints: List[str] = field(default_factory=list)
    driven_plugs: List[str] = field(default_factory=list)
    anim_curves: List[str] = field(default_factory=list)
    locked_plugs: List[str] = field(default_factory=list)
    original_scales: Dict[str, float] = field(default_factory=dict)
    created_locators: List[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, payload: str) -> "TelescopeRigBundle":
        """Rebuild a bundle from :meth:`to_json` output, ignoring unknown keys
        (a scene stamped by an older/newer build still reads back)."""
        data = json.loads(payload)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


class TelescopeRig(ptk.LoggingMixin):
    """Telescope Rig
    Configures constraints and driven keys to make a series of segments
    telescope between two locators.

    The base and end segments ride their locators (parent constraints); every
    interior segment is point-constrained directly to BOTH locators with
    graded weights (segment ``i`` of ``n`` at fraction ``i/(n-1)``), so the
    stack spreads evenly at any length. Constraining interiors to the
    locators — never to neighboring segments — keeps the graph cycle-free for
    any segment count. Interior segments also carry distance-driven scale
    keys along the strut axis so they bridge the gaps as the rig extends.

    Two segments is a first-class build (the common hydraulic/strut case):
    the two halves simply ride their locators and slide, so no distance node
    and no driven keys are created — nothing to scale between them.

    Either locator may be omitted, in which case it is created at the outer
    end of the segment chain (measured along the aim axis) and recorded for
    teardown.
    """

    _AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
    _DATA_ATTR = "telescopeRigData"

    def __init__(self, log_level="WARNING"):
        """Initialize telescope rig with logging."""
        super().__init__()
        self.set_log_level(log_level)
        self.bundle: Optional[TelescopeRigBundle] = None

    # ------------------------------------------------------------------
    # Input resolution / validation (no scene mutation in this section)
    # ------------------------------------------------------------------

    @classmethod
    def _resolve_axis(
        cls, aim_axis: str
    ) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], str, List[str]]:
        """Resolve an axis token ("y", "-z", ...) to rig vectors and attrs.

        Returns:
            (aim_vector, up_vector, scale_attr, off_axis_scale_attrs) — the up
            vector is a world axis orthogonal to the aim axis (a parallel up
            makes the aim solve degenerate), and ``scale_attr`` is the
            along-strut scale channel the driven keys animate.
        """
        token = str(aim_axis).strip().lower()
        sign = -1.0 if token.startswith("-") else 1.0
        letter = token.lstrip("+-")
        if letter not in cls._AXES:
            raise ValueError(
                f"aim_axis must be one of x, y, z (optionally signed); got {aim_axis!r}."
            )
        aim_vector = tuple(sign * c for c in cls._AXES[letter])
        up_vector = cls._AXES["x" if letter != "x" else "y"]
        scale_attr = f"scale{letter.upper()}"
        off_axis = [f"scale{c.upper()}" for c in "xyz" if c != letter]
        return aim_vector, up_vector, scale_attr, off_axis

    def _resolve_node(self, node, role: str) -> str:
        """Resolve a single node input (str/object/one-element list) or raise."""
        resolved = cmds.ls(CoreUtils.as_strings(node), flatten=True, long=True) or []
        if not resolved:
            msg = f"At least one valid {role} must be provided."
            self.logger.error(msg)
            raise ValueError(msg)
        if len(resolved) > 1:
            self.logger.warning(
                f"Ambiguous {role} {node!r} matched {len(resolved)} nodes; "
                f"using {resolved[0]}."
            )
        return str(resolved[0])

    def _unsettable_plugs(self, node: str, attrs: List[str]) -> List[str]:
        """Plugs on *node* that a constraint or driven key could not drive."""
        return [
            f"{node}.{a}"
            for a in attrs
            if not cmds.getAttr(f"{node}.{a}", settable=True)
        ]

    # ------------------------------------------------------------------
    # Geometry probes (used to place auto locators / derive the collapse)
    # ------------------------------------------------------------------

    @staticmethod
    def _has_geometry(node: str) -> bool:
        """True when *node* has any shape in its DAG subtree.

        The probe must reach the whole subtree, not the node's own children:
        an imported segment is routinely a group whose mesh sits a level or two
        down. And the answer is load-bearing — ``exactWorldBoundingBox`` on a
        genuinely shapeless transform returns an INVERTED-INFINITY box
        (``1e20 … -1e20``, probed in mayapy), not a degenerate one, so measuring
        without this guard poisons every number derived from it.
        """
        return bool(cmds.ls(node, dag=True, shapes=True, noIntermediate=True) or [])

    @staticmethod
    def _project_size(size: "om.MVector", direction: "om.MVector") -> float:
        """Support width of an axis-aligned box of *size* along *direction*."""
        return (
            abs(direction.x) * size.x
            + abs(direction.y) * size.y
            + abs(direction.z) * size.z
        )

    @classmethod
    def _world_aim_direction(
        cls, node: str, aim_vector: Tuple[float, float, float]
    ) -> "om.MVector":
        """The node's LOCAL aim axis as a unit world vector.

        Used instead of a pivot-to-pivot direction because a fully nested
        strut has all its segments sitting on top of one another — the modeled
        long axis is the only reliable read of which way the strut points.
        """
        matrix = om.MMatrix(cmds.xform(node, q=True, ws=True, matrix=True))
        direction = om.MVector(*aim_vector) * matrix
        if direction.length() <= 1e-9:  # degenerate (zero-scaled) transform
            return om.MVector(*aim_vector)
        return direction.normal()

    @classmethod
    def _axis_extent(cls, node: str, direction: "om.MVector") -> float:
        """Width of *node*'s world bounding box measured along *direction*.

        A world AABB (what ``exactWorldBoundingBox`` gives, and what the
        blendertk twin measures) — exact for a segment modeled on axis, and an
        overestimate by roughly its cross-section for one modeled at an angle.
        """
        if not cls._has_geometry(node):
            return 0.0
        return cls._project_size(
            CoreUtils.get_bounding_box(node, world=True).size, direction
        )

    @classmethod
    def _support_point(
        cls, node: str, direction: "om.MVector", sign: float
    ) -> "om.MVector":
        """The point on *node*'s world bbox furthest along ``sign * direction``.

        Taken through the box CENTER (not a corner) so an auto locator lands on
        the strut's centerline rather than off on an edge.
        """
        if not cls._has_geometry(node):
            return XformUtils.get_translation(node, world=True)
        bbox = CoreUtils.get_bounding_box(node, world=True)
        half = 0.5 * cls._project_size(bbox.size, direction)
        return bbox.center + direction * (sign * half)

    @classmethod
    def _chain_direction(
        cls, segments: List[str], aim_vector: Tuple[float, float, float]
    ) -> "om.MVector":
        """Unit world direction that points base-segment → end-segment."""
        direction = cls._world_aim_direction(segments[0], aim_vector)
        chain = XformUtils.get_translation(
            segments[-1], world=True
        ) - XformUtils.get_translation(segments[0], world=True)
        # Selection order is the authority on which end is which; only trust it
        # when the segments are actually spread out (a nested strut is not).
        if chain.length() > 1e-6 and (chain * direction) < 0.0:
            direction = -direction
        return direction

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def setup_telescope_rig(
        self,
        base_locator: Optional[Union[str, List[str]]] = None,
        end_locator: Optional[Union[str, List[str]]] = None,
        segments: Optional[List[str]] = None,
        collapsed_distance: Optional[float] = None,
        aim_axis: str = "y",
        world_up_type: str = "scene",
        lock_attributes: bool = True,
        name: str = "telescope",
    ) -> TelescopeRigBundle:
        """Sets up constraints and driven keys to make a series of segments telescope between two locators.

        All inputs are validated (and every driven channel checked for
        lock/connection conflicts) BEFORE the first node is created — a
        refused build leaves the scene untouched, and an unexpected mid-build
        failure rolls back the nodes it had created.

        Parameters:
            base_locator (str/object/list/None): The base locator. ``None``
                creates one at the outer end of the first segment.
            end_locator (str/object/list/None): The end locator. ``None``
                creates one at the outer end of the last segment.
            segments (List[str]): Ordered list of segment names, base to end.
                Must contain at least two segments. Exactly two builds a
                sliding strut (no scaling — there is nothing between them).
            collapsed_distance (float/None): The base-to-end distance at which
                the segments are fully retracted. Must be greater than zero and
                less than the current (build-pose) distance. Below it the
                driven scales clamp; beyond the build pose they keep
                stretching linearly. ``None`` (the default) derives it from the
                longest segment's length along the aim axis — fully nested, the
                assembly is as long as its longest tube. Ignored for two-segment
                builds.
            aim_axis (str): The segments' long axis — "x", "y", or "z",
                optionally signed ("-y"). Drives the aim vectors, the driven
                scale channel, which channels get locked, and the direction
                auto-created locators are placed along.
            world_up_type (str): ``aimConstraint`` worldUpType. "scene"
                (default) gives predictable roll; use "none" for struts that
                travel through scene-vertical (roll-symmetric segments).
            lock_attributes (bool): Lock the off-axis scale channels on every
                segment (the only rig-breaking channels the constraints leave
                free). Previously-locked plugs are left as-is.
            name (str): Prefix for the nodes this build creates.

        Returns:
            TelescopeRigBundle: Names of everything created (also stored on
            ``self.bundle`` for ``teardown``, and stamped onto the base locator
            so a later session can recover it).

        Raises:
            ValueError: On missing/duplicate/overlapping nodes, fewer than
                two segments, nodes that already carry a telescope rig,
                undrivable (locked or already-connected) channels, coincident
                locators, or an out-of-range ``collapsed_distance``.
        """
        self.logger.info("Setting up Telescope Rig...", preset="header")

        # Resolve each segment input on its own: ls can never fold duplicate
        # entries together before the duplicate check below sees them, and a
        # nonexistent entry raises instead of silently building a shorter rig.
        resolved_segments: List[str] = []
        for entry in CoreUtils.as_strings(segments or []):
            matches = cmds.ls(entry, flatten=True) or []
            if not matches:
                msg = f"Segment not found: {entry!r}."
                self.logger.error(msg)
                raise ValueError(msg)
            resolved_segments.extend(str(m) for m in matches)
        segments = resolved_segments
        if len(segments) < 2:
            self.logger.error("At least two segments must be provided.")
            raise ValueError("At least two segments must be provided.")

        aim_vector, up_vector, scale_attr, off_axis_attrs = self._resolve_axis(aim_axis)

        base_locator = (
            self._resolve_node(base_locator, "base locator")
            if base_locator is not None
            else None
        )
        end_locator = (
            self._resolve_node(end_locator, "end locator")
            if end_locator is not None
            else None
        )

        # Role integrity: distinct locators, unique segments, no overlap.
        segment_longs = [str((cmds.ls(s, long=True) or [s])[0]) for s in segments]
        if len(set(segment_longs)) != len(segment_longs):
            raise ValueError("Duplicate segments provided.")
        supplied = [n for n in (base_locator, end_locator) if n is not None]
        if len(supplied) == 2 and supplied[0] == supplied[1]:
            raise ValueError("Base and end locators must be different nodes.")
        if any(n in segment_longs for n in supplied):
            raise ValueError("Base/end locators cannot also be segments.")

        # Refuse to re-rig nodes that already carry one. The pre-flight below
        # would also stop it (a constrained channel is not settable), but only
        # with a channel list the user has to decode — and a rebuild would
        # overwrite the previous bundle's record, stranding its nodes.
        existing = self.find_bundles(supplied + segments)
        if existing:
            names = ", ".join(sorted({b.name for b in existing}))
            msg = (
                f"These nodes already carry a telescope rig ({names}); "
                f"remove it before building a new one."
            )
            self.logger.error(msg)
            raise ValueError(msg)

        # Where the missing locators WOULD go — computed before anything is
        # created so the distance checks below can still refuse cleanly. The
        # strut direction is only probed when something actually needs it
        # (an auto locator, or an auto collapse distance).
        has_interiors = len(segments) > 2
        needs_direction = (
            base_locator is None
            or end_locator is None
            or (has_interiors and collapsed_distance is None)
        )
        direction = (
            self._chain_direction(segments, aim_vector) if needs_direction else None
        )
        base_position = (
            XformUtils.get_translation(base_locator, world=True)
            if base_locator
            else self._support_point(segments[0], direction, -1.0)
        )
        end_position = (
            XformUtils.get_translation(end_locator, world=True)
            if end_locator
            else self._support_point(segments[-1], direction, 1.0)
        )

        initial_distance = (end_position - base_position).length()
        if initial_distance <= 1e-6:
            msg = "Base and end locators must be separated before building the telescope rig."
            self.logger.error(msg)
            raise ValueError(msg)

        # Two segments slide against each other — there is no interior to
        # stretch, so the collapse distance never enters the build.
        if not has_interiors:
            collapsed_distance = 0.0
        elif collapsed_distance is None:
            collapsed_distance = self._auto_collapsed_distance(
                segments, direction, initial_distance
            )
            self.logger.info(
                f"Collapsed distance (auto): <hl>{collapsed_distance:.4f}</hl>"
            )
        if has_interiors and not 0.0 < collapsed_distance < initial_distance:
            msg = (
                f"collapsed_distance must be between 0 and the current "
                f"base-to-end distance ({initial_distance:.4f}); got {collapsed_distance}."
            )
            self.logger.error(msg)
            raise ValueError(msg)

        # Pre-flight: every channel the rig will drive must be drivable now,
        # so nothing is created for a build that would die halfway through.
        # (Locators this build creates are new and always clean.)
        t_attrs = ["translateX", "translateY", "translateZ"]
        r_attrs = ["rotateX", "rotateY", "rotateZ"]
        blocked: List[str] = []
        for locator in (base_locator, end_locator):
            if locator:
                blocked += self._unsettable_plugs(locator, r_attrs)
        for seg in (segments[0], segments[-1]):
            blocked += self._unsettable_plugs(seg, t_attrs + r_attrs)
        for seg in segments[1:-1]:
            blocked += self._unsettable_plugs(seg, t_attrs + r_attrs + [scale_attr])
        if blocked:
            msg = (
                "Cannot build: these channels are locked or already connected: "
                + ", ".join(blocked)
            )
            self.logger.error(msg)
            raise ValueError(msg)

        name = Naming.strip_illegal_chars(str(name)) or "telescope"
        bundle = TelescopeRigBundle(
            name=name,
            base_locator=base_locator or "",
            end_locator=end_locator or "",
            segments=list(segments),
            scale_attr=scale_attr,
            initial_distance=initial_distance,
            collapsed_distance=collapsed_distance,
        )

        try:
            # Handle creation is the first mutation; recorded immediately so a
            # rollback takes the new locators with it.
            handle_size = max(initial_distance * 0.05, 1e-3)
            if not bundle.base_locator:
                bundle.base_locator = self._create_handle(
                    f"{name}_base_LOC", base_position, handle_size, bundle
                )
            if not bundle.end_locator:
                bundle.end_locator = self._create_handle(
                    f"{name}_end_LOC", end_position, handle_size, bundle
                )
            self._build(
                bundle,
                aim_vector,
                up_vector,
                world_up_type,
                off_axis_attrs,
                lock_attributes,
                has_interiors,
            )
        except Exception:
            # A validated build can still die on exotic scene state — never
            # leave a half-wired rig behind.
            self.logger.error(
                "Build failed — rolling back partially created rig nodes."
            )
            self._delete_bundle_nodes(bundle, restore=True)
            raise

        self._stamp(bundle)
        self.bundle = bundle
        self.logger.success("Telescope Rig setup complete.")
        return bundle

    @classmethod
    def _auto_collapsed_distance(
        cls, segments: List[str], direction: "om.MVector", initial_distance: float
    ) -> float:
        """Collapse distance inferred from the segments themselves.

        Fully nested, a telescope is as long as its longest tube — so the
        longest segment's extent along the strut axis is the base-to-end
        distance at full retraction. Falls back to an even split of the build
        pose when the segments carry no geometry to measure.
        """
        longest = max((cls._axis_extent(s, direction) for s in segments), default=0.0)
        if longest <= 1e-6:
            longest = initial_distance / len(segments)
        # Keep it strictly inside the valid range even for odd poses.
        return min(max(longest, initial_distance * 1e-3), initial_distance * 0.999)

    def _create_handle(
        self,
        name: str,
        position: "om.MVector",
        size: float,
        bundle: TelescopeRigBundle,
    ) -> str:
        """Create one auto locator, recording it on *bundle* for teardown."""
        locator = str(cmds.spaceLocator(name=name)[0])
        bundle.created_locators.append(locator)
        cmds.xform(locator, ws=True, t=(position.x, position.y, position.z))
        # localScale is a SHAPE attribute — sizing the transform would scale the
        # segments that ride it.
        for shape in cmds.listRelatives(locator, shapes=True, path=True) or []:
            for axis in "XYZ":
                cmds.setAttr(f"{shape}.localScale{axis}", size)
        self.logger.info(f"Created handle: <hl>{locator}</hl>")
        return locator

    def _build(
        self,
        bundle: TelescopeRigBundle,
        aim_vector: Tuple[float, float, float],
        up_vector: Tuple[float, float, float],
        world_up_type: str,
        off_axis_attrs: List[str],
        lock_attributes: bool,
        has_interiors: bool,
    ) -> None:
        """Create the rig nodes, recording each into *bundle* as it appears."""
        base_locator = bundle.base_locator
        end_locator = bundle.end_locator
        segments = bundle.segments
        scale_attr = bundle.scale_attr
        neg_aim_vector = tuple(-c for c in aim_vector)

        # World-space distance driver. worldMatrix (not .translate) so parented
        # locators still measure true world distance; Maya uniquifies the node
        # name on collision and the bundle records whatever it returns. Only
        # interiors read it, so a two-segment strut never creates one.
        if has_interiors:
            distance_node = cmds.shadingNode(
                "distanceBetween", asUtility=True, name=f"{bundle.name}_distance"
            )
            bundle.distance_node = distance_node
            cmds.connectAttr(
                f"{base_locator}.worldMatrix[0]", f"{distance_node}.inMatrix1"
            )
            cmds.connectAttr(
                f"{end_locator}.worldMatrix[0]", f"{distance_node}.inMatrix2"
            )

        # Locators aim at each other (position-only inputs — no cycle), so the
        # end segments they carry stay oriented along the strut.
        bundle.constraints.append(
            cmds.aimConstraint(
                end_locator,
                base_locator,
                aimVector=aim_vector,
                upVector=up_vector,
                worldUpType=world_up_type,
                name=f"{bundle.name}_base_AIM",
            )[0]
        )
        bundle.constraints.append(
            cmds.aimConstraint(
                base_locator,
                end_locator,
                aimVector=neg_aim_vector,
                upVector=up_vector,
                worldUpType=world_up_type,
                name=f"{bundle.name}_end_AIM",
            )[0]
        )
        self.logger.info("Locators constrained.")

        # End segments ride their locators; interiors hang between BOTH
        # locators at graded weights — never off neighboring segments, which
        # is what made the old build cyclic beyond three segments.
        bundle.constraints.append(
            cmds.parentConstraint(
                base_locator, segments[0], mo=True, name=f"{bundle.name}_base_PAR"
            )[0]
        )
        bundle.constraints.append(
            cmds.parentConstraint(
                end_locator, segments[-1], mo=True, name=f"{bundle.name}_end_PAR"
            )[0]
        )
        last_index = len(segments) - 1
        for i, segment in enumerate(segments[1:-1], start=1):
            fraction = i / last_index
            # Two create-mode calls so each target carries its own weight from
            # the start — editing weights after creation would invalidate the
            # maintained offset and pop the segment off its build pose.
            point = cmds.pointConstraint(
                base_locator,
                segment,
                mo=True,
                weight=1.0 - fraction,
                name=f"{bundle.name}_seg{i}_PNT",
            )[0]
            cmds.pointConstraint(end_locator, segment, mo=True, weight=fraction)
            bundle.constraints.append(point)
            bundle.constraints.append(
                cmds.aimConstraint(
                    end_locator,
                    segment,
                    aimVector=aim_vector,
                    upVector=up_vector,
                    worldUpType=world_up_type,
                    name=f"{bundle.name}_seg{i}_AIM",
                )[0]
            )
        self.logger.info("Segments constrained.")

        # Distance-driven scale on the interiors. Keys through (initial, s0)
        # and (collapsed, s0*ratio) make the scale track s0 * distance/initial
        # exactly; post-infinity keeps stretching past the build pose (the old
        # constant infinity tore the rig open there), pre-infinity clamps once
        # fully collapsed. SPLINE tangents, not linear: a "linear" tangent on
        # an END key has no neighbor to aim at and degenerates to flat, which
        # makes the linear post-infinity extend horizontally; spline aligns
        # end-key tangents to the chord (and with two keys the in-between IS
        # the exact line).
        if has_interiors:
            ratio = bundle.collapsed_distance / bundle.initial_distance
            driver = f"{bundle.distance_node}.distance"
            for segment in segments[1:-1]:
                plug = f"{segment}.{scale_attr}"
                build_scale = cmds.getAttr(plug)
                bundle.original_scales[plug] = build_scale
                cmds.setDrivenKeyframe(
                    plug,
                    currentDriver=driver,
                    driverValue=bundle.initial_distance,
                    value=build_scale,
                    inTangentType="spline",
                    outTangentType="spline",
                )
                cmds.setDrivenKeyframe(
                    plug,
                    currentDriver=driver,
                    driverValue=bundle.collapsed_distance,
                    value=build_scale * ratio,
                    inTangentType="spline",
                    outTangentType="spline",
                )
                cmds.setInfinity(
                    segment,
                    attribute=scale_attr,
                    preInfinite="constant",
                    postInfinite="linear",
                )
                bundle.driven_plugs.append(plug)
                # Record the exact curves this build created so teardown never
                # touches keys the user adds to these plugs later.
                bundle.anim_curves.extend(
                    cmds.listConnections(
                        plug, source=True, destination=False, type="animCurve"
                    )
                    or []
                )
            self.logger.info("Driven keys set.")
        else:
            self.logger.info(
                "Two segments — sliding strut (no interior to scale)."
            )

        # The constraints and driven keys claim every channel that matters
        # except the off-axis scales — lock those so a stray manipulator drag
        # can't shear the stack. (Already-locked plugs stay untouched and are
        # NOT recorded, so teardown restores exactly the locks it added.)
        if lock_attributes:
            for segment in segments:
                for attr in off_axis_attrs:
                    plug = f"{segment}.{attr}"
                    if not cmds.getAttr(plug, lock=True):
                        cmds.setAttr(plug, lock=True)
                        bundle.locked_plugs.append(plug)

    # ------------------------------------------------------------------
    # Scene persistence — recover a bundle in a later session
    # ------------------------------------------------------------------

    def _stamp(self, bundle: TelescopeRigBundle) -> None:
        """Record *bundle* as JSON on its base locator.

        Without this the build record only lives on the Python instance, so
        reopening the panel (or reopening the scene) makes ``teardown``
        unreachable and the rig has to be picked apart by hand.
        """
        plug = f"{bundle.base_locator}.{self._DATA_ATTR}"
        try:
            if not cmds.objExists(plug):
                cmds.addAttr(
                    bundle.base_locator, longName=self._DATA_ATTR, dataType="string"
                )
            cmds.setAttr(plug, bundle.to_json(), type="string")
        except RuntimeError as error:  # referenced/locked node — not fatal
            self.logger.warning(f"Could not record the rig data on {plug}: {error}")

    @classmethod
    def scene_bundles(cls) -> List[TelescopeRigBundle]:
        """Every telescope-rig bundle stamped into the current scene."""
        found: List[TelescopeRigBundle] = []
        for plug in cmds.ls(f"*.{cls._DATA_ATTR}", recursive=True) or []:
            try:
                payload = cmds.getAttr(plug)
            except (RuntimeError, ValueError):
                continue
            if not payload:
                continue
            try:
                found.append(TelescopeRigBundle.from_json(payload))
            except (ValueError, TypeError):
                continue
        return found

    @classmethod
    def find_bundles(cls, nodes) -> List[TelescopeRigBundle]:
        """Bundles whose locators or segments intersect *nodes*."""
        wanted = set(cmds.ls(CoreUtils.as_strings(nodes), long=True) or [])
        if not wanted:
            return []

        def _long(name):
            return set(cmds.ls(name, long=True) or [])

        matches = []
        for bundle in cls.scene_bundles():
            members = set()
            for name in [bundle.base_locator, bundle.end_locator, *bundle.segments]:
                members |= _long(name)
            if members & wanted:
                matches.append(bundle)
        return matches

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    def _delete_bundle_nodes(
        self, bundle: TelescopeRigBundle, restore: bool = True
    ) -> None:
        """Delete every node *bundle* records; optionally restore locks/scales."""
        nodes = list(bundle.anim_curves) + list(bundle.constraints)
        nodes.append(bundle.distance_node)
        for node in nodes:
            if node and cmds.objExists(node):
                cmds.delete(node)

        if restore:
            for plug in bundle.locked_plugs:
                if cmds.objExists(plug):
                    cmds.setAttr(plug, lock=False)
            for plug, value in bundle.original_scales.items():
                if cmds.objExists(plug):
                    cmds.setAttr(plug, value)

        # Drop the stamp before the locator goes, so a surviving user-supplied
        # locator isn't left advertising a rig that no longer exists.
        stamp = f"{bundle.base_locator}.{self._DATA_ATTR}"
        if bundle.base_locator and cmds.objExists(stamp):
            try:
                cmds.setAttr(stamp, lock=False)
                cmds.deleteAttr(stamp)
            except RuntimeError:
                pass

        # Locators the BUILD created are rig nodes; user handles are not.
        for locator in bundle.created_locators:
            if locator and cmds.objExists(locator):
                cmds.delete(locator)

    @CoreUtils.undoable
    def teardown(self, bundle: Optional[TelescopeRigBundle] = None) -> bool:
        """Remove a telescope rig built by this class.

        Deletes the distance node, constraints, driven-key curves, and any
        locators the build itself created; unlocks the channels it locked and
        restores the segments' build-pose scales. User-supplied
        locators/segments are left in place (the build never re-parents them).

        Parameters:
            bundle (TelescopeRigBundle): The build record to remove. Defaults
                to the most recent build on this instance.

        Returns:
            bool: True when a bundle was torn down, False when there was
            nothing to do.
        """
        bundle = bundle or self.bundle
        if bundle is None:
            self.logger.warning("No telescope rig bundle to tear down.")
            return False
        self.logger.info("Removing Telescope Rig...", preset="header")
        self._delete_bundle_nodes(bundle, restore=True)
        if bundle is self.bundle:
            self.bundle = None
        self.logger.success("Telescope Rig removed.")
        return True


class TelescopeRigSlots(ptk.LoggingMixin):
    def __init__(self, switchboard, log_level="WARNING"):
        super().__init__()
        self.set_log_level(log_level)
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.telescope_rig
        self.bundle = None  # most recent build, for a selection-less Remove

        # Setup Logging Redirect
        self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        self.logger.setup_logging_redirect(self.ui.txt003)
        self.logger.info("Telescope Rig Tool initialized.", preset="italic")

        # Connect clickable log links (action:// URIs in QTextBrowser)
        if hasattr(self.ui.txt003, "anchorClicked"):
            self.ui.txt003.anchorClicked.connect(self._on_log_link_clicked)

        # Connect Signals
        self.ui.btn_build.clicked.connect(self.build_rig)
        self.ui.btn_remove.clicked.connect(self.remove_rig)

        self._init_tooltips()

    def _on_log_link_clicked(self, url) -> None:
        """Dispatch clickable ``action://`` links from the log panel."""
        from mayatk.ui_utils._ui_utils import UiUtils

        UiUtils.dispatch_log_link(url, self.logger)

    def header_init(self, widget):
        """Configure header help text."""
        widget.set_help_text(
            self.sb.tooltip.fmt(
                title="Telescope Rig",
                body="Build a telescoping segment rig where nested segments "
                "extend and retract between a base and end locator, driven by "
                "the distance between them.",
                sections=[
                    (
                        "Selection order",
                        [
                            "<b>Segments</b> — min 2, in extension order.",
                            "<b>Locators</b> — optional: a locator selected "
                            "first becomes the base, one selected last becomes "
                            "the end. Whichever you leave out is created for "
                            "you at the end of the strut.",
                        ],
                    ),
                ],
                steps=[
                    "Select the segments in order (base end first). Add your "
                    "own base/end locators around them if you want to control "
                    "where the handles sit.",
                    "Set <b>Aim Axis</b> to the segments' long axis.",
                    "Leave <b>Collapsed Distance</b> on <i>Auto</i>, or enter "
                    "the base-to-end distance at which the segments are fully "
                    "retracted.",
                    "Press <b>Build</b> to wire driven keys on each segment.",
                ],
                notes=[
                    "Two segments builds a sliding strut — the halves ride "
                    "their handles and never stretch.",
                    "<b>Remove</b> tears down the rig on the selection (or the "
                    "last one built), including any locators it created.",
                    "Build results stream to the log panel; locator names are "
                    "rendered as clickable <i>action://</i> links that select "
                    "the node in Maya.",
                    "The whole build is one undo step.",
                ],
            )
        )

    def _init_tooltips(self):
        """Set the polished (uitk ``fmt``) tooltips for every option and action."""
        ui = self.ui

        ui.cmb_axis.setToolTip(
            self.sb.tooltip.fmt(
                title="Aim Axis",
                body="The segments' long axis — the local axis that points "
                "from the base toward the end locator. The rig aims every "
                "segment along it and drives that axis' scale.",
                notes=[
                    "The other two scale channels are locked at build time "
                    "so the stack can't shear.",
                    "Auto-created locators are placed along this axis, at the "
                    "outer ends of the first and last segment.",
                ],
            )
        )
        ui.spin_collapsed.setToolTip(
            self.sb.tooltip.fmt(
                title="Collapsed Distance",
                body="Base-to-end distance at which the segments are fully "
                "retracted (nested). As the end locator pulls farther than "
                "this, the segments slide apart to bridge the gap.",
                notes=[
                    "<b>Auto</b> (0) measures the longest segment along the "
                    "aim axis — fully nested, the strut is as long as its "
                    "longest tube.",
                    "Must be greater than 0 and less than the current "
                    "base-to-end distance.",
                    "Pushing closer than this distance clamps the segments "
                    "at their fully-nested size.",
                    "Ignored for a two-segment strut — there is no interior "
                    "segment to scale.",
                ],
            )
        )
        ui.btn_build.setToolTip(
            self.sb.tooltip.fmt(
                title="Build Telescope Rig",
                body="Wires distance-driven keys onto each segment so they "
                "extend and retract as the gap between the base and end "
                "locators changes.",
                steps=[
                    "Select the <b>segments</b> in extension order "
                    "<i>(min 2)</i>.",
                    "Optionally select a <b>base</b> locator first and an "
                    "<b>end</b> locator last — either one you omit is created "
                    "at the outer end of the strut.",
                    "Press <b>Build Telescope Rig</b>.",
                ],
                notes=[
                    "Needs at least 2 objects: two segments is a sliding "
                    "strut, three or more telescope.",
                    "Node names in the log are clickable links that select "
                    "the node in Maya.",
                ],
            )
        )
        ui.btn_remove.setToolTip(
            self.sb.tooltip.fmt(
                title="Remove Telescope Rig",
                body="Deletes the constraints, distance node, and driven-key "
                "curves the build created, unlocks the channels it locked, and "
                "restores the segments' build-pose scale.",
                steps=[
                    "Select any part of the rig — a locator or a segment.",
                    "Press <b>Remove Telescope Rig</b>.",
                ],
                notes=[
                    "Locators the build created are deleted; locators you "
                    "supplied are kept.",
                    "With nothing selected, the most recent build in this "
                    "session is removed.",
                    "The build record is stamped on the base locator, so a rig "
                    "from an earlier session still tears down cleanly.",
                ],
            )
        )

    @staticmethod
    def _is_handle(node: str) -> bool:
        """True for a rig handle (locator / bare transform), False for geometry.

        Lets one ordered selection carry both roles: leading/trailing handles
        are the base/end locators, everything between them is a segment.

        Its OWN shape decides first, so a locator that happens to parent
        geometry still reads as a handle. Only when it has no shape of its own
        does the whole DAG subtree matter — an imported segment is routinely a
        group whose mesh sits a level or two down, and calling that a handle
        would swallow it out of the segment list. (Deliberately not routed
        through ``TelescopeRig._has_geometry``, which asks the same question of
        the scene for a different reason: role classification must not depend
        on the engine class.)
        """
        own = cmds.listRelatives(node, shapes=True, noIntermediate=True) or []
        if own:
            return all(cmds.nodeType(s) == "locator" for s in own)
        return not (cmds.ls(node, dag=True, shapes=True, noIntermediate=True) or [])

    def _partition_selection(self, sel: List[str]):
        """Split an ordered selection into (base_locator, segments, end_locator).

        A leading handle is the base and a trailing handle is the end; either
        may be absent, in which case the engine creates it.
        """
        base = sel[0] if self._is_handle(sel[0]) else None
        end = sel[-1] if len(sel) > 1 and self._is_handle(sel[-1]) else None
        start = 1 if base is not None else 0
        stop = len(sel) - 1 if end is not None else len(sel)
        return base, sel[start:stop], end

    def _mirror_engine_log(self, rig) -> None:
        """Stream an engine instance's log into this panel's browser."""
        rig.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        rig.logger.setup_logging_redirect(self.ui.txt003)

    @CoreUtils.undoable
    def build_rig(self):
        self.logger.log_divider()

        sel = cmds.ls(selection=True, transforms=True, flatten=True) or []
        base_locator, segments, end_locator = (
            self._partition_selection(sel) if sel else (None, [], None)
        )
        if len(segments) < 2:
            self.logger.error("Insufficient selection.")
            self.sb.message_box(
                "Selection Error:\n"
                "Select at least 2 segments, in order:\n"
                "1. Base locator (optional — created if omitted)\n"
                "2. Segments (min 2, in order)\n"
                "3. End locator (optional — created if omitted)"
            )
            return

        collapsed_dist = self.ui.spin_collapsed.value() or None
        aim_axis = ("x", "y", "z")[self.ui.cmb_axis.currentIndex()]

        try:
            rig = TelescopeRig()
            self._mirror_engine_log(rig)

            for role, node in (("Base", base_locator), ("End", end_locator)):
                if node is None:
                    self.logger.info(f"{role} detected: <hl>auto</hl>")
                else:
                    link = self.logger.log_link(str(node), "select", node=str(node))
                    self.logger.info(f"{role} detected: {link}")
            self.logger.info(
                f"Segments detected: <hl>{len(segments)}</hl> "
                f"(aim axis: <hl>{aim_axis.upper()}</hl>)"
            )

            self.bundle = rig.setup_telescope_rig(
                base_locator=base_locator,
                end_locator=end_locator,
                segments=segments,
                collapsed_distance=collapsed_dist,
                aim_axis=aim_axis,
            )
        except Exception as e:
            self.logger.error(f"Error setting up rig: {str(e)}")
            self.sb.message_box(f"Error setting up rig: {str(e)}")

    @CoreUtils.undoable
    def remove_rig(self):
        self.logger.log_divider()

        sel = cmds.ls(selection=True, transforms=True, flatten=True) or []
        if sel:
            bundles = TelescopeRig.find_bundles(sel)
            empty_msg = "No telescope rig found on the selected nodes."
        else:
            bundles = [self.bundle] if self.bundle else []
            empty_msg = (
                "Nothing selected and no rig built this session.\n"
                "Select a rig locator or segment and try again."
            )
        if not bundles:
            self.logger.error(empty_msg.splitlines()[0])
            self.sb.message_box(empty_msg)
            return

        try:
            rig = TelescopeRig()
            self._mirror_engine_log(rig)
            for bundle in bundles:
                rig.teardown(bundle)
                if bundle is self.bundle:
                    self.bundle = None
        except Exception as e:
            self.logger.error(f"Error removing rig: {str(e)}")
            self.sb.message_box(f"Error removing rig: {str(e)}")


if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("telescope_rig", reload=True)
    ui.show(pos="screen", app_exec=True)
