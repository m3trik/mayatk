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

    def __init__(
        self,
        base_mesh: str,
        target_mesh: str,
        blendshape: str,
        weight_index: int = 0,
    ):
        super().__init__()
        self.base_mesh = base_mesh
        self.target_mesh = target_mesh
        self.blendshape = blendshape
        self.weight_index = weight_index
        self.validator = Validator()

    @property
    def weight_attr(self) -> str:
        """The blendShape weight attribute this setup animates (e.g. ``weight[0]``)."""
        return f"weight[{self.weight_index}]"

    def create_keyframes(self, start_frame: int, end_frame: int) -> bool:
        """Create linear keyframe animation from weight 0.0 -> 1.0."""
        try:
            cmds.cutKey(self.blendshape, attribute=self.weight_attr, clear=True)

            cmds.setKeyframe(
                self.blendshape,
                attribute=self.weight_attr,
                value=0.0,
                time=start_frame,
            )
            cmds.setKeyframe(
                self.blendshape,
                attribute=self.weight_attr,
                value=1.0,
                time=end_frame,
            )

            cmds.keyTangent(
                self.blendshape,
                attribute=self.weight_attr,
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
        if not self.validator.validate_blendshape(self.blendshape, self.weight_index):
            return False

        original_weight = cmds.getAttr(f"{self.blendshape}.{self.weight_attr}")
        try:
            cmds.setAttr(f"{self.blendshape}.{self.weight_attr}", 0.5)
            cmds.refresh()
            self.logger.info("BlendShape test: weight set to 0.5")
            self.logger.info(
                f"Check if {self.base_mesh} changed shape (should morph, not move)"
            )
            return True
        finally:
            cmds.setAttr(f"{self.blendshape}.{self.weight_attr}", original_weight)

    def get_frame_range(self) -> Tuple[int, int]:
        """Return (start, end) frame range from keyframes on the weight attr."""
        keys = cmds.keyframe(f"{self.blendshape}.{self.weight_attr}", query=True)
        if not keys or len(keys) < 2:
            raise ValueError("No valid keyframe range found")
        return int(min(keys)), int(max(keys))
