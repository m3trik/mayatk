# !/usr/bin/python
# coding=utf-8
"""Recovery utilities for corrupted blendShape setups."""
import pythontk as ptk

try:
    from maya import cmds
except ImportError as error:
    print(__file__, error)

from mayatk.anim_utils.blendshape_animator.applicator import Applicator, ApplyStatus
from mayatk.anim_utils.blendshape_animator.helpers import list_history
from mayatk.anim_utils.blendshape_animator.keyframes import Keyframes
from mayatk.anim_utils.blendshape_animator.target import Targets


class Recovery(ptk.LoggingMixin):
    """Utilities for recovering from corrupted blendShape setups."""

    @classmethod
    def fix_corrupted_animation(cls, base_mesh: str, target_mesh: str) -> bool:
        """Rebuild corrupted blendShape animation."""
        cls.logger.info("=== RECOVERY: Fixing corrupted animation ===")

        history = list_history(base_mesh, type_filter="blendShape")
        if not history:
            cls.logger.error("No blendShape found to fix")
            return False

        old_blendshape = history[0]

        keyframes = []
        try:
            times = cmds.keyframe(
                f"{old_blendshape}.weight[0]", query=True, timeChange=True
            )
            values = cmds.keyframe(
                f"{old_blendshape}.weight[0]", query=True, valueChange=True
            )
            if times and values:
                keyframes = list(zip(times, values))
                cls.logger.info(f"  Saved {len(keyframes)} keyframes")
        except RuntimeError:
            cls.logger.warning("No keyframes found to preserve")

        cmds.delete(old_blendshape)
        cls.logger.info("Removed corrupted blendShape")

        # Use the short (leaf) name when synthesising a node name — full DAG
        # paths contain "|" which is illegal in Maya node names (Bug 14).
        base_short = base_mesh.rsplit("|", 1)[-1]
        new_name = f"{base_short}_BS_fixed"
        new_blendshape = cmds.blendShape(
            target_mesh, base_mesh, name=new_name, frontOfChain=True, origin="world"
        )[0]
        cls.logger.info(f"Created fresh blendShape: {new_blendshape}")

        if keyframes:
            for time_val, weight_val in keyframes:
                cmds.setKeyframe(
                    new_blendshape,
                    attribute="weight[0]",
                    time=time_val,
                    value=weight_val,
                )

            start_time, end_time = keyframes[0][0], keyframes[-1][0]
            cmds.keyTangent(
                new_blendshape,
                attribute="weight[0]",
                time=(start_time, end_time),
                inTangentType="linear",
                outTangentType="linear",
            )

            cls.logger.info(
                f"Restored {len(keyframes)} keyframes with linear tangents"
            )

        cls.logger.info("Animation fixed! Test by scrubbing timeline.")
        return True

    @classmethod
    def recover_with_targets(cls, base_mesh: str, target_mesh: str) -> bool:
        """Complete recovery: fix animation AND restore tween customizations."""
        cls.logger.info("=== COMPLETE RECOVERY ===")

        if not cls.fix_corrupted_animation(base_mesh, target_mesh):
            return False

        history = list_history(base_mesh, type_filter="blendShape")
        if history:
            new_blendshape = history[0]
            count = Targets.update_all_references(new_blendshape, base_mesh)

            if count > 0:
                keyframes = Keyframes(base_mesh, target_mesh, new_blendshape)
                applicator = Applicator(keyframes)
                results = applicator.apply_tweens()

                successful = sum(
                    1 for _, status in results if status is ApplyStatus.APPLIED
                )
                cls.logger.info("Complete recovery successful!")
                cls.logger.info("Basic animation: Working")
                cls.logger.info(f"Tween customizations: {successful} applied")
                return True

        cls.logger.warning("Basic animation fixed, but no tweens found to restore")
        return False
