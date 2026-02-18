#!/usr/bin/env python
# coding=utf-8
from typing import List, Union

try:
    import pymel.core as pm
except ImportError as error:
    print(__file__, error)

import pythontk as ptk
from mayatk.core_utils._core_utils import CoreUtils


class TelescopeRig(ptk.LoggingMixin):
    """
    Telescope Rig
    Configures constraints and driven keys to make a series of segments telescope between two locators.
    """

    def __init__(self):
        """Initialize telescope rig with logging."""
        super().__init__()

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

        base_locators = pm.ls(base_locator, flatten=True)
        if not base_locators:
            self.logger.error("At least one valid base locator must be provided.")
            raise ValueError("At least one valid base locator must be provided.")
        base_locator = base_locators[0]

        end_locators = pm.ls(end_locator, flatten=True)
        if not end_locators:
            self.logger.error("At least one valid end locator must be provided.")
            raise ValueError("At least one valid end locator must be provided.")
        end_locator = end_locators[0]

        segments = pm.ls(segments, flatten=True)
        if len(segments) < 2:
            self.logger.error("At least two segments must be provided.")
            raise ValueError("At least two segments must be provided.")

        def create_distance_node():
            distance_node = pm.shadingNode(
                "distanceBetween", asUtility=True, name="strut_distance"
            )
            pm.connectAttr(base_locator.translate, distance_node.point1)
            pm.connectAttr(end_locator.translate, distance_node.point2)
            return distance_node

        def create_and_constrain_midpoint_locator(start_locator, end_locator, index):
            midpoint_locator_name = f"segment_locator_{index}_LOC"
            # Let Maya handle unique naming if collision occurs
            midpoint_locator = pm.spaceLocator(name=midpoint_locator_name)

            # Simple average position start
            midpoint_pos = (
                pm.datatypes.Vector(start_locator.getTranslation(space="world"))
                + pm.datatypes.Vector(end_locator.getTranslation(space="world"))
            ) / 2
            midpoint_locator.setTranslation(midpoint_pos, space="world")

            pm.pointConstraint(start_locator, end_locator, midpoint_locator)
            pm.aimConstraint(
                end_locator,
                midpoint_locator,
                aimVector=(0, 1, 0),
                upVector=(0, 1, 0),
                worldUpType="scene",
            )
            return midpoint_locator

        def constrain_segments():
            pm.parentConstraint(base_locator, segments[0], mo=True)
            pm.parentConstraint(end_locator, segments[-1], mo=True)
            if len(segments) > 2:
                for i, segment in enumerate(segments[1:-1], start=1):
                    # segments[i - 1] is the previous one (starting with base-linked)
                    # segments[i + 1] is the next one
                    midpoint_locator = create_and_constrain_midpoint_locator(
                        segments[i - 1], segments[i + 1], i
                    )
                    pm.parent(segment, midpoint_locator)
                    pm.aimConstraint(
                        end_locator,
                        segment,
                        aimVector=(0, 1, 0),
                        upVector=(0, 1, 0),
                        worldUpType="scene",
                    )
            self.logger.info("Segments constrained.", "result")

        def set_driven_keys(distance_node, initial_distance):
            for segment in segments[1:-1]:
                # Use attribute objects directly instead of string concat
                pm.setDrivenKeyframe(
                    segment.scaleY,
                    currentDriver=distance_node.distance,
                    driverValue=initial_distance,
                    value=1,
                )
                pm.setDrivenKeyframe(
                    segment.scaleY,
                    currentDriver=distance_node.distance,
                    driverValue=collapsed_distance,
                    value=collapsed_distance / initial_distance,
                )
            self.logger.info("Driven keys set.", "result")

        def lock_segment_attributes():
            for segment in segments:
                try:
                    # Lock standard transforms
                    segment.translateX.set(lock=True)
                    segment.translateZ.set(lock=True)
                    segment.rotateX.set(lock=True)
                    segment.rotateZ.set(lock=True)
                    segment.scaleX.set(lock=True)
                    segment.scaleZ.set(lock=True)
                except Exception:
                    pass

        def constrain_locators():
            pm.aimConstraint(
                end_locator,
                base_locator,
                aimVector=(0, 1, 0),
                upVector=(0, 1, 0),
                worldUpType="scene",
            )
            pm.aimConstraint(
                base_locator,
                end_locator,
                aimVector=(0, -1, 0),
                upVector=(0, 1, 0),
                worldUpType="scene",
            )
            self.logger.info("Locators constrained.", "result")

        distance_node = create_distance_node()
        constrain_locators()
        constrain_segments()

        initial_distance = pm.getAttr(distance_node.distance)
        set_driven_keys(distance_node, initial_distance)
        lock_segment_attributes()

        self.logger.success("Telescope Rig setup complete.")


class TelescopeRigSlots(ptk.LoggingMixin):
    def __init__(self, switchboard):
        super().__init__()
        self.sb = switchboard
        self.ui = self.sb.loaded_ui.telescope_rig

        # Setup Logging Redirect
        self.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
        self.logger.setup_logging_redirect(self.ui.txt003)
        self.logger.info("Telescope Rig Tool initialized.", preset="italic")

        # Connect Signals
        self.ui.btn_build.clicked.connect(self.build_rig)

    @CoreUtils.undoable
    def build_rig(self):
        self.logger.log_divider()

        # Parse Selection: Base -> Segments... -> End
        sel = pm.selected(transforms=True, flatten=True)
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

        base_locator = sel[0].name()
        end_locator = sel[-1].name()
        segments = [s.name() for s in sel[1:-1]]

        collapsed_dist = self.ui.spin_collapsed.value()

        try:
            # Instantiate Rig class to get logging
            rig = TelescopeRig()
            # Redirect rig logger too if we want
            rig.logger.set_text_handler(self.sb.registered_widgets.TextEditLogHandler)
            rig.logger.setup_logging_redirect(self.ui.txt003)

            self.logger.info(f"Base detected: <hl>{base_locator}</hl>")
            self.logger.info(f"End detected: <hl>{end_locator}</hl>")
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
