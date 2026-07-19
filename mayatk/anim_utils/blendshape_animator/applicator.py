# !/usr/bin/python
# coding=utf-8
"""Applies tween mesh edits back to blendShape in-between targets."""
from enum import Enum
from typing import List, Optional, Tuple

import pythontk as ptk

try:
    from maya import cmds
except ImportError as error:
    print(__file__, error)

from mayatk.anim_utils.blendshape_animator.keyframes import Keyframes
from mayatk.anim_utils.blendshape_animator.target import Target, Targets


class ApplyStatus(Enum):
    APPLIED = "applied"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    ERROR = "error"


class Applicator(ptk.LoggingMixin):
    """Applies tween mesh edits back to blendShape in-between targets."""

    def __init__(self, keyframes: Keyframes):
        super().__init__()
        self.keyframes = keyframes

    def validate_topology(self, tweens: List[Target]) -> List[Target]:
        """Filter ``tweens`` to those matching base mesh vertex count."""
        self.logger.info("Validating tween mesh topology...")

        base_vert_count = cmds.polyEvaluate(self.keyframes.base_mesh, vertex=True)
        valid_tweens: List[Target] = []

        for tween in tweens:
            try:
                tween_vert_count = cmds.polyEvaluate(tween.mesh, vertex=True)
            except RuntimeError as e:
                self.logger.error(f"  {tween.mesh}: Error checking topology - {e}")
                continue
            if tween_vert_count == base_vert_count:
                valid_tweens.append(tween)
                self.logger.info(
                    f"  {tween.mesh}: {tween_vert_count} vertices (valid)"
                )
            else:
                self.logger.error(
                    f"  {tween.mesh}: {tween_vert_count} vs {base_vert_count} vertices (topology mismatch)"
                )

        if len(valid_tweens) != len(tweens):
            self.logger.warning(
                f"Filtered {len(tweens) - len(valid_tweens)} tweens due to topology mismatch"
            )

        return valid_tweens

    def apply_tweens(
        self,
        tweens: Optional[List[Target]] = None,
        skip_duplicates: bool = True,
        validate_topology: bool = False,
    ) -> List[Tuple[Target, ApplyStatus]]:
        """Apply tween mesh edits to blendShape in-between targets.

        ``tweens=None`` applies every tween tagged for THIS setup — never
        tweens belonging to other BlendshapeAnimator setups in the scene.
        ``validate_topology`` defaults to False — topology mismatches surface as
        per-tween errors rather than being silently filtered away.
        """
        if tweens is None:
            tweens = Targets.find_all_targets(
                blendshape=self.keyframes.blendshape,
                base_mesh=self.keyframes.base_mesh,
            )

        if not tweens:
            self.logger.info("No tween meshes found to apply")
            return []

        if validate_topology:
            tweens = self.validate_topology(tweens)
            if not tweens:
                self.logger.warning("No valid tweens found after topology validation")
                return []

        weight_groups = Targets.group_by_weight(tweens)
        applied_results: List[Tuple[Target, ApplyStatus]] = []
        original_weight = cmds.getAttr(f"{self.keyframes.blendshape}.weight[0]")

        try:
            for weight, tween_group in sorted(weight_groups.items()):
                target_tween = tween_group[-1]

                if len(tween_group) > 1:
                    self.logger.info(
                        f"  Found {len(tween_group)} tweens at weight {weight:.3f}, using: {target_tween.mesh}"
                    )

                status = self._apply_single_tween(target_tween, skip_duplicates)
                applied_results.append((target_tween, status))

                if status is ApplyStatus.APPLIED:
                    self.logger.info(f"Applied {target_tween.mesh} at weight {weight:.3f}")
                elif status is ApplyStatus.SKIPPED_DUPLICATE:
                    self.logger.warning(f"Skipped {target_tween.mesh} (duplicate weight)")
                else:
                    self.logger.error(f"Failed to apply {target_tween.mesh}")

        finally:
            cmds.setAttr(f"{self.keyframes.blendshape}.weight[0]", original_weight)

        applied_count = sum(1 for _, s in applied_results if s is ApplyStatus.APPLIED)
        self.logger.info(f"Applied {applied_count}/{len(applied_results)} tween edits")
        return applied_results

    def _apply_single_tween(
        self, tween: Target, skip_duplicates: bool
    ) -> ApplyStatus:
        """Apply a single tween mesh to the blendShape.

        Returns a tristate so callers can distinguish "skipped intentionally"
        from "real error" (Bug 5 — was previously False for both).
        """
        try:
            cmds.blendShape(
                self.keyframes.blendshape,
                edit=True,
                inBetween=True,
                target=(self.keyframes.base_mesh, 0, tween.mesh, tween.weight),
            )
            return ApplyStatus.APPLIED
        except RuntimeError as e:
            if "Weights must be unique" in str(e) and skip_duplicates:
                return ApplyStatus.SKIPPED_DUPLICATE
            self.logger.error(f"    Error applying {tween.mesh}: {e}")
            return ApplyStatus.ERROR
