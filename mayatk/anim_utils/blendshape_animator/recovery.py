# !/usr/bin/python
# coding=utf-8
"""Recovery utilities for corrupted blendShape setups."""

import pythontk as ptk

try:
    from maya import cmds
except ImportError as error:
    print(__file__, error)

from mayatk.core_utils._core_utils import CoreUtils
from mayatk.anim_utils.blendshape_animator.applicator import Applicator, ApplyStatus
from mayatk.anim_utils.blendshape_animator.helpers import BlendshapeHelpers
from mayatk.anim_utils.blendshape_animator.keyframes import Keyframes
from mayatk.anim_utils.blendshape_animator.target import Targets


class Recovery(ptk.LoggingMixin):
    """Utilities for recovering from corrupted blendShape setups."""

    @classmethod
    @CoreUtils.undoable
    def fix_corrupted_animation(cls, base_mesh: str, target_mesh: str) -> bool:
        """Rebuild corrupted blendShape animation."""
        cls.logger.info("=== RECOVERY: Fixing corrupted animation ===")

        history = BlendshapeHelpers.list_history(base_mesh, type_filter="blendShape")
        if not history:
            cls.logger.error("No blendShape found to fix")
            return False

        old_blendshape = history[0]

        # Guard: this recovery hardcodes weight[0] and rebuilds with only
        # ONE target (target_mesh), then deletes the ENTIRE old node — on a
        # multi-target blendShape that destroys every other target and its
        # animation. Recovery only supports single-target nodes; refuse
        # rather than silently drop the rest. (Full index-aware recovery is
        # a larger change — not attempted here.)
        existing_targets = (
            cmds.blendShape(old_blendshape, query=True, target=True) or []
        )
        if len(existing_targets) > 1:
            cls.logger.error(
                f"{old_blendshape} has {len(existing_targets)} targets "
                f"({existing_targets}) - recovery only supports single-target "
                "blendShape setups. Rebuilding would destroy the other "
                "targets and their animation. Aborting; no changes made."
            )
            return False

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

        # Build the replacement BEFORE deleting the corrupted node — a failed
        # rebuild (dead/mismatched target) must leave the original setup
        # intact. A temporary name avoids colliding while both nodes exist.
        # Use the short (leaf) name when synthesising a node name — full DAG
        # paths contain "|" which is illegal in Maya node names (Bug 14).
        base_short = base_mesh.rsplit("|", 1)[-1]
        try:
            new_blendshape = cmds.blendShape(
                target_mesh,
                base_mesh,
                name=f"{base_short}_BS_fixed",
                frontOfChain=True,
                origin="world",
            )[0]
        except (RuntimeError, ValueError) as e:
            cls.logger.error(
                f"Rebuild failed — original blendShape left intact: {e}"
            )
            return False

        old_name = old_blendshape
        cmds.delete(old_blendshape)
        cls.logger.info("Removed corrupted blendShape")
        new_blendshape = cmds.rename(new_blendshape, old_name)
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

            cls.logger.info(f"Restored {len(keyframes)} keyframes with linear tangents")

        cls.logger.info("Animation fixed! Test by scrubbing timeline.")
        return True

    @classmethod
    @CoreUtils.undoable
    def recover_with_targets(cls, base_mesh: str, target_mesh: str) -> bool:
        """Complete recovery: fix animation AND restore tween customizations."""
        cls.logger.info("=== COMPLETE RECOVERY ===")

        if not cls.fix_corrupted_animation(base_mesh, target_mesh):
            return False

        history = BlendshapeHelpers.list_history(base_mesh, type_filter="blendShape")
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

        # The rebuild itself succeeded — a scene without tween meshes to
        # restore is a successful recovery, not a failure.
        cls.logger.warning("Basic animation fixed, but no tweens found to restore")
        return True
