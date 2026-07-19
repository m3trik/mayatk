#!/usr/bin/env python
# coding=utf-8
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError as error:
    print(__file__, error)

import pythontk as ptk
from uitk.widgets.mixins.tooltip_mixin import fmt
from mayatk.core_utils._core_utils import CoreUtils, as_strings
from mayatk.edit_utils.naming._naming import Naming


@dataclass
class TelescopeRigBundle:
    """Record of everything one ``setup_telescope_rig`` build created.

    Returned by ``setup_telescope_rig`` and consumed by ``teardown`` — the
    exact node names are captured at creation time, so removal never has to
    guess by name pattern.
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
    """

    _AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}

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
        resolved = cmds.ls(as_strings(node), flatten=True) or []
        if not resolved:
            msg = f"At least one valid {role} must be provided."
            self.logger.error(msg)
            raise ValueError(msg)
        return str(resolved[0])

    def _unsettable_plugs(self, node: str, attrs: List[str]) -> List[str]:
        """Plugs on *node* that a constraint or driven key could not drive."""
        return [
            f"{node}.{a}"
            for a in attrs
            if not cmds.getAttr(f"{node}.{a}", settable=True)
        ]

    @staticmethod
    def _world_distance(node_a: str, node_b: str) -> float:
        a = cmds.xform(node_a, q=True, ws=True, t=True)
        b = cmds.xform(node_b, q=True, ws=True, t=True)
        return (om.MVector(*a) - om.MVector(*b)).length()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    @CoreUtils.undoable
    def setup_telescope_rig(
        self,
        base_locator: Union[str, List[str]],
        end_locator: Union[str, List[str]],
        segments: List[str],
        collapsed_distance: float = 1.0,
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
            base_locator (str/object/list): The base locator.
            end_locator (str/object/list): The end locator.
            segments (List[str]): Ordered list of segment names, base to end.
                Must contain at least two segments.
            collapsed_distance (float): The base-to-end distance at which the
                segments are fully retracted. Must be greater than zero and
                less than the current (build-pose) distance. Below it the
                driven scales clamp; beyond the build pose they keep
                stretching linearly.
            aim_axis (str): The segments' long axis — "x", "y", or "z",
                optionally signed ("-y"). Drives the aim vectors, the driven
                scale channel, and which channels get locked.
            world_up_type (str): ``aimConstraint`` worldUpType. "scene"
                (default) gives predictable roll; use "none" for struts that
                travel through scene-vertical (roll-symmetric segments).
            lock_attributes (bool): Lock the off-axis scale channels on every
                segment (the only rig-breaking channels the constraints leave
                free). Previously-locked plugs are left as-is.
            name (str): Prefix for the nodes this build creates.

        Returns:
            TelescopeRigBundle: Names of everything created (also stored on
            ``self.bundle`` for ``teardown``).

        Raises:
            ValueError: On missing/duplicate/overlapping nodes, fewer than
                two segments, undrivable (locked or already-connected)
                channels, coincident locators, or an out-of-range
                ``collapsed_distance``.
        """
        self.logger.info("Setting up Telescope Rig...", preset="header")

        base_locator = self._resolve_node(base_locator, "base locator")
        end_locator = self._resolve_node(end_locator, "end locator")

        # Resolve each segment input on its own: ls can never fold duplicate
        # entries together before the duplicate check below sees them, and a
        # nonexistent entry raises instead of silently building a shorter rig.
        resolved_segments: List[str] = []
        for entry in as_strings(segments):
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

        # Role integrity: distinct locators, unique segments, no overlap.
        base_long = str((cmds.ls(base_locator, long=True) or [base_locator])[0])
        end_long = str((cmds.ls(end_locator, long=True) or [end_locator])[0])
        segment_longs = [
            str((cmds.ls(s, long=True) or [s])[0]) for s in segments
        ]
        if base_long == end_long:
            raise ValueError("Base and end locators must be different nodes.")
        if len(set(segment_longs)) != len(segment_longs):
            raise ValueError("Duplicate segments provided.")
        if base_long in segment_longs or end_long in segment_longs:
            raise ValueError("Base/end locators cannot also be segments.")

        aim_vector, up_vector, scale_attr, off_axis_attrs = self._resolve_axis(aim_axis)

        initial_distance = self._world_distance(base_locator, end_locator)
        if initial_distance <= 1e-6:
            msg = "Base and end locators must be separated before building the telescope rig."
            self.logger.error(msg)
            raise ValueError(msg)
        if not 0.0 < collapsed_distance < initial_distance:
            msg = (
                f"collapsed_distance must be between 0 and the current "
                f"base-to-end distance ({initial_distance:.4f}); got {collapsed_distance}."
            )
            self.logger.error(msg)
            raise ValueError(msg)

        # Pre-flight: every channel the rig will drive must be drivable now,
        # so nothing is created for a build that would die halfway through.
        t_attrs = ["translateX", "translateY", "translateZ"]
        r_attrs = ["rotateX", "rotateY", "rotateZ"]
        blocked: List[str] = []
        for locator in (base_locator, end_locator):
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
            base_locator=base_locator,
            end_locator=end_locator,
            segments=list(segments),
            scale_attr=scale_attr,
            initial_distance=initial_distance,
            collapsed_distance=collapsed_distance,
        )

        try:
            self._build(bundle, aim_vector, up_vector,
                        world_up_type, off_axis_attrs, lock_attributes)
        except Exception:
            # A validated build can still die on exotic scene state — never
            # leave a half-wired rig behind.
            self.logger.error("Build failed — rolling back partially created rig nodes.")
            self._delete_bundle_nodes(bundle, restore=True)
            raise

        self.bundle = bundle
        self.logger.success("Telescope Rig setup complete.")
        return bundle

    def _build(
        self,
        bundle: TelescopeRigBundle,
        aim_vector: Tuple[float, float, float],
        up_vector: Tuple[float, float, float],
        world_up_type: str,
        off_axis_attrs: List[str],
        lock_attributes: bool,
    ) -> None:
        """Create the rig nodes, recording each into *bundle* as it appears."""
        base_locator = bundle.base_locator
        end_locator = bundle.end_locator
        segments = bundle.segments
        scale_attr = bundle.scale_attr
        neg_aim_vector = tuple(-c for c in aim_vector)

        # World-space distance driver. worldMatrix (not .translate) so parented
        # locators still measure true world distance; Maya uniquifies the node
        # name on collision and the bundle records whatever it returns.
        distance_node = cmds.shadingNode(
            "distanceBetween", asUtility=True, name=f"{bundle.name}_distance"
        )
        bundle.distance_node = distance_node
        cmds.connectAttr(f"{base_locator}.worldMatrix[0]", f"{distance_node}.inMatrix1")
        cmds.connectAttr(f"{end_locator}.worldMatrix[0]", f"{distance_node}.inMatrix2")

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

    @CoreUtils.undoable
    def teardown(self, bundle: Optional[TelescopeRigBundle] = None) -> bool:
        """Remove a telescope rig built by this class.

        Deletes the distance node, constraints, and driven-key curves the
        build created; unlocks the channels it locked and restores the
        segments' build-pose scales. User locators/segments are left in place
        (the build never re-parents or creates transforms).

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

        # Setup Logging Redirect
        self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        self.logger.setup_logging_redirect(self.ui.txt003)
        self.logger.info("Telescope Rig Tool initialized.", preset="italic")

        # Connect clickable log links (action:// URIs in QTextBrowser)
        if hasattr(self.ui.txt003, "anchorClicked"):
            self.ui.txt003.anchorClicked.connect(self._on_log_link_clicked)

        # Connect Signals
        self.ui.btn_build.clicked.connect(self.build_rig)

        self._init_tooltips()

    def _on_log_link_clicked(self, url) -> None:
        """Dispatch clickable ``action://`` links from the log panel."""
        from mayatk.ui_utils._ui_utils import UiUtils

        UiUtils.dispatch_log_link(url, self.logger)

    def header_init(self, widget):
        """Configure header help text."""
        widget.set_help_text(
            fmt(
                title="Telescope Rig",
                body="Build a telescoping segment rig where nested segments "
                "extend and retract between a base and end locator, driven by "
                "the distance between them.",
                sections=[
                    (
                        "Selection order",
                        [
                            "<b>Base</b> locator — selected first.",
                            "<b>Segments</b> — min 2, in extension order.",
                            "<b>End</b> locator — selected last.",
                        ],
                    ),
                ],
                steps=[
                    "Select the base, the segments, and the end locator in "
                    "that order.",
                    "Set <b>Aim Axis</b> to the segments' long axis.",
                    "Set <b>Collapsed Distance</b> — the base-to-end distance "
                    "at which the segments are fully retracted.",
                    "Press <b>Build</b> to wire driven keys on each segment.",
                ],
                notes=[
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
            fmt(
                title="Aim Axis",
                body="The segments' long axis — the local axis that points "
                "from the base toward the end locator. The rig aims every "
                "segment along it and drives that axis' scale.",
                notes=[
                    "The other two scale channels are locked at build time "
                    "so the stack can't shear.",
                ],
            )
        )
        ui.spin_collapsed.setToolTip(
            fmt(
                title="Collapsed Distance",
                body="Base-to-end distance at which the segments are fully "
                "retracted (nested). As the end locator pulls farther than "
                "this, the segments slide apart to bridge the gap.",
                notes=[
                    "Pose the rig fully collapsed first, then enter that "
                    "base-to-end distance here.",
                    "Must be greater than 0 and less than the current "
                    "base-to-end distance.",
                    "Pushing closer than this distance clamps the segments "
                    "at their fully-nested size.",
                ],
            )
        )
        ui.btn_build.setToolTip(
            fmt(
                title="Build Telescope Rig",
                body="Wires distance-driven keys onto each segment so they "
                "extend and retract as the gap between the base and end "
                "locators changes.",
                steps=[
                    "Select the <b>base</b> locator first.",
                    "<b>Shift</b>-select the <b>segments</b> in extension "
                    "order <i>(min 2)</i>.",
                    "<b>Shift</b>-select the <b>end</b> locator last.",
                    "Press <b>Build Telescope Rig</b>.",
                ],
                notes=[
                    "Needs at least 4 objects: base + 2 segments + end.",
                    "Node names in the log are clickable links that select "
                    "the node in Maya.",
                ],
            )
        )

    @CoreUtils.undoable
    def build_rig(self):
        self.logger.log_divider()

        sel = cmds.ls(selection=True, transforms=True, flatten=True) or []
        if len(sel) < 4:
            self.logger.error("Insufficient selection.")
            self.sb.message_box(
                "Selection Error:\n"
                "Please select at least 4 objects in order:\n"
                "1. Base Locator\n"
                "2. Segments (min 2, in order)\n"
                "3. End Locator"
            )
            return

        base_locator = sel[0]
        end_locator = sel[-1]
        segments = sel[1:-1]

        collapsed_dist = self.ui.spin_collapsed.value()
        aim_axis = ("x", "y", "z")[self.ui.cmb_axis.currentIndex()]

        try:
            rig = TelescopeRig()
            rig.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
            rig.logger.setup_logging_redirect(self.ui.txt003)

            base_link = self.logger.log_link(
                str(base_locator), "select", node=str(base_locator)
            )
            end_link = self.logger.log_link(
                str(end_locator), "select", node=str(end_locator)
            )
            self.logger.info(f"Base detected: {base_link}")
            self.logger.info(f"End detected: {end_link}")
            self.logger.info(
                f"Segments detected: <hl>{len(segments)}</hl> "
                f"(aim axis: <hl>{aim_axis.upper()}</hl>)"
            )

            rig.setup_telescope_rig(
                base_locator=base_locator,
                end_locator=end_locator,
                segments=segments,
                collapsed_distance=collapsed_dist,
                aim_axis=aim_axis,
            )
        except Exception as e:
            self.logger.error(f"Error setting up rig: {str(e)}")
            self.sb.message_box(f"Error setting up rig: {str(e)}")


if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("telescope_rig", reload=True)
    ui.show(pos="screen", app_exec=True)
