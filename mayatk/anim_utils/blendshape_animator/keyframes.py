# !/usr/bin/python
# coding=utf-8
"""Core blendShape keyframe animation operations."""
from typing import Tuple

import pythontk as ptk

try:
    from maya import cmds
except ImportError as error:
    print(__file__, error)

from mayatk.anim_utils.blendshape_animator.validator import Validator


class Keyframes(ptk.LoggingMixin):
    """Core blendShape animation functionality."""

    def __init__(self, base_mesh: str, target_mesh: str, blendshape: str):
        super().__init__()
        self.base_mesh = base_mesh
        self.target_mesh = target_mesh
        self.blendshape = blendshape
        self.validator = Validator()

    def create_keyframes(self, start_frame: int, end_frame: int) -> bool:
        """Create linear keyframe animation from weight 0.0 -> 1.0."""
        try:
            cmds.cutKey(self.blendshape, attribute="weight[0]", clear=True)

            cmds.currentTime(start_frame)
            cmds.setKeyframe(
                self.blendshape, attribute="weight[0]", value=0.0, time=start_frame
            )

            cmds.currentTime(end_frame)
            cmds.setKeyframe(
                self.blendshape, attribute="weight[0]", value=1.0, time=end_frame
            )

            cmds.keyTangent(
                self.blendshape,
                attribute="weight[0]",
                time=(start_frame, end_frame),
                inTangentType="linear",
                outTangentType="linear",
            )

            self.logger.info(f"Created keyframes: {start_frame} to {end_frame}")
            return True
        except RuntimeError as e:
            self.logger.error(f"Creating keyframes: {e}")
            return False

    def test_morph(self) -> bool:
        """Test the blendShape by temporarily setting weight to 0.5."""
        if not self.validator.validate_blendshape(self.blendshape):
            return False

        original_weight = cmds.getAttr(f"{self.blendshape}.weight[0]")
        try:
            cmds.setAttr(f"{self.blendshape}.weight[0]", 0.5)
            cmds.refresh()
            self.logger.info("BlendShape test: weight set to 0.5")
            self.logger.info(
                f"Check if {self.base_mesh} changed shape (should morph, not move)"
            )
            return True
        finally:
            cmds.setAttr(f"{self.blendshape}.weight[0]", original_weight)

    def get_frame_range(self) -> Tuple[int, int]:
        """Return (start, end) frame range from keyframes on weight[0]."""
        keys = cmds.keyframe(f"{self.blendshape}.weight[0]", query=True)
        if not keys or len(keys) < 2:
            raise ValueError("No valid keyframe range found")
        return int(min(keys)), int(max(keys))
