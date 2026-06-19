#!/usr/bin/env python
# coding=utf-8
from typing import List, Union

try:
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
except ImportError as error:
    print(__file__, error)

import pythontk as ptk
from uitk.widgets.mixins.tooltip_mixin import fmt
from mayatk.core_utils._core_utils import CoreUtils


class TelescopeRig(ptk.LoggingMixin):
    """
    Telescope Rig
    Configures constraints and driven keys to make a series of segments telescope between two locators.
    """

    def __init__(self, log_level="WARNING"):
        """Initialize telescope rig with logging."""
        super().__init__()
        self.set_log_level(log_level)

    @CoreUtils.undoable
    def setup_telescope_rig(
        self,
        base_locator: Union[str, List[str]],
        end_locator: Union[str, List[str]],
        segments: List[str],
        collapsed_distance: float = 1.0,
    ):
        """Sets up constraints and driven keys to make a series of segments telescope between two locators.

        Parameters:
            base_locator (str/object/list): The base locator.
            end_locator (str/object/list): The end locator.
            segments (List[str]): Ordered list of segment names. Must contain at least two segments.
            collapsed_distance (float): The distance at which the segments are in the collapsed state.

        Raises:
            ValueError: If less than two segments are provided.
        """
        self.logger.info("Setting up Telescope Rig...", preset="header")

        base_locators = cmds.ls(str(base_locator), flatten=True) or []
        if not base_locators:
            self.logger.error("At least one valid base locator must be provided.")
            raise ValueError("At least one valid base locator must be provided.")
        base_locator = base_locators[0]

        end_locators = cmds.ls(str(end_locator), flatten=True) or []
        if not end_locators:
            self.logger.error("At least one valid end locator must be provided.")
            raise ValueError("At least one valid end locator must be provided.")
        end_locator = end_locators[0]

        segments = cmds.ls(
            [str(s) for s in segments] if isinstance(segments, (list, tuple, set)) else [str(segments)],
            flatten=True,
        ) or []
        if len(segments) < 2:
            self.logger.error("At least two segments must be provided.")
            raise ValueError("At least two segments must be provided.")

        def create_distance_node():
            distance_node = cmds.shadingNode(
                "distanceBetween", asUtility=True, name="strut_distance"
            )
            cmds.connectAttr(f"{base_locator}.translate", f"{distance_node}.point1")
            cmds.connectAttr(f"{end_locator}.translate", f"{distance_node}.point2")
            return distance_node

        def create_and_constrain_midpoint_locator(start_locator, end_locator, index):
            midpoint_locator_name = f"segment_locator_{index}_LOC"
            midpoint_locator = cmds.spaceLocator(name=midpoint_locator_name)[0]

            start_ws = cmds.xform(start_locator, q=True, t=True, worldSpace=True)
            end_ws = cmds.xform(end_locator, q=True, t=True, worldSpace=True)
            midpoint_pos = (om.MVector(*start_ws) + om.MVector(*end_ws)) / 2
            cmds.xform(
                midpoint_locator,
                t=[midpoint_pos.x, midpoint_pos.y, midpoint_pos.z],
                worldSpace=True,
            )

            cmds.pointConstraint(start_locator, end_locator, midpoint_locator)
            cmds.aimConstraint(
                end_locator,
                midpoint_locator,
                aimVector=(0, 1, 0),
                upVector=(0, 1, 0),
                worldUpType="scene",
            )
            return midpoint_locator

        def constrain_segments():
            cmds.parentConstraint(base_locator, segments[0], mo=True)
            cmds.parentConstraint(end_locator, segments[-1], mo=True)
            if len(segments) > 2:
                for i, segment in enumerate(segments[1:-1], start=1):
                    midpoint_locator = create_and_constrain_midpoint_locator(
                        segments[i - 1], segments[i + 1], i
                    )
                    cmds.parent(segment, midpoint_locator)
                    cmds.aimConstraint(
                        end_locator,
                        segment,
                        aimVector=(0, 1, 0),
                        upVector=(0, 1, 0),
                        worldUpType="scene",
                    )
            self.logger.info("Segments constrained.")

        def set_driven_keys(distance_node, initial_distance):
            for segment in segments[1:-1]:
                cmds.setDrivenKeyframe(
                    f"{segment}.scaleY",
                    currentDriver=f"{distance_node}.distance",
                    driverValue=initial_distance,
                    value=1,
                )
                cmds.setDrivenKeyframe(
                    f"{segment}.scaleY",
                    currentDriver=f"{distance_node}.distance",
                    driverValue=collapsed_distance,
                    value=collapsed_distance / initial_distance,
                )
            self.logger.info("Driven keys set.")

        def lock_segment_attributes():
            for segment in segments:
                try:
                    for attr in ("translateX", "translateZ", "rotateX", "rotateZ", "scaleX", "scaleZ"):
                        cmds.setAttr(f"{segment}.{attr}", lock=True)
                except Exception:
                    pass

        def constrain_locators():
            cmds.aimConstraint(
                end_locator,
                base_locator,
                aimVector=(0, 1, 0),
                upVector=(0, 1, 0),
                worldUpType="scene",
            )
            cmds.aimConstraint(
                base_locator,
                end_locator,
                aimVector=(0, -1, 0),
                upVector=(0, 1, 0),
                worldUpType="scene",
            )
            self.logger.info("Locators constrained.")

        distance_node = create_distance_node()
        constrain_locators()
        constrain_segments()

        initial_distance = cmds.getAttr(f"{distance_node}.distance")
        set_driven_keys(distance_node, initial_distance)
        lock_segment_attributes()

        self.logger.success("Telescope Rig setup complete.")


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

    def _on_log_link_clicked(self, url) -> None:
        """Dispatch clickable ``action://`` links from the log panel."""
        from mayatk.ui_utils._ui_utils import UiUtils

        UiUtils.dispatch_log_link(url, self.logger)

    def header_init(self, widget):
        """Configure header help text."""
        widget.set_help_text(
            fmt(
                title="Telescope Rig",
                body="Build a telescoping segment rig where segments extend "
                "and retract between a base and end locator, driven by their "
                "distance.",
                steps=[
                    "Place locators / segments and select them <b>in order</b>:",
                    "  &nbsp;1. Base locator (first)",
                    "  &nbsp;2. Segments (min 2, in extension order)",
                    "  &nbsp;3. End locator (last)",
                    "Set <b>Collapsed Distance</b> — the base-to-end distance "
                    "at which segments are fully retracted.",
                    "Press <b>Build</b> to wire driven keys on each segment.",
                ],
                notes=[
                    "Build results stream to the log panel; locator names are "
                    "rendered as clickable <i>action://</i> links that select "
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
            self.logger.info(f"Segments detected: <hl>{len(segments)}</hl>")

            rig.setup_telescope_rig(
                base_locator=base_locator,
                end_locator=end_locator,
                segments=segments,
                collapsed_distance=collapsed_dist,
            )
        except Exception as e:
            self.logger.error(f"Error setting up rig: {str(e)}")
            self.sb.message_box(f"Error setting up rig: {str(e)}")


if __name__ == "__main__":
    from mayatk.ui_utils.maya_ui_handler import MayaUiHandler

    ui = MayaUiHandler.instance().get("telescope_rig", reload=True)
    ui.show(pos="screen", app_exec=True)
