# !/usr/bin/python
# coding=utf-8
"""Creates in-between target meshes for custom blendShape animation curves."""
from typing import List, Optional, Set

import pythontk as ptk

try:
    from maya import cmds
except ImportError as error:
    print(__file__, error)

from mayatk.node_utils.attributes._attributes import Attributes

from mayatk.anim_utils.blendshape_animator.keyframes import Keyframes
from mayatk.anim_utils.blendshape_animator.target import Target, Targets
from mayatk.anim_utils.blendshape_animator.weights import Weights


class Creator(ptk.LoggingMixin):
    """Creates in-between target meshes for custom animation curves."""

    def __init__(self, keyframes: Keyframes):
        super().__init__()
        self.keyframes = keyframes

    def create_weight_based_tweens(
        self,
        weights: List[float],
        group_name: str = "_morphInbetweens_GRP",
        name_prefix: str = "morph_ib",
    ) -> List[Target]:
        """Create tween meshes at specific weight values.

        Skips or offsets weights that already exist (Bug 3 — previously crashed
        mid-batch on the first duplicate).
        """
        original_weight = cmds.getAttr(f"{self.keyframes.blendshape}.weight[0]")
        created_tweens: List[Target] = []
        existing_weights = self.get_existing_weights()

        if not cmds.objExists(group_name):
            group = cmds.group(empty=True, name=group_name)
        else:
            group = group_name

        try:
            for raw_weight in weights:
                weight = Weights.round_weight(raw_weight)

                if weight in existing_weights:
                    offset = self.find_nearby_weight(weight, existing_weights)
                    if offset is None:
                        self.logger.warning(
                            f"Skipping weight {weight:.3f}: already exists, no nearby slot free"
                        )
                        continue
                    self.logger.info(
                        f"Weight {weight:.3f} exists, using nearby weight {offset:.3f}"
                    )
                    weight = offset

                cmds.setAttr(f"{self.keyframes.blendshape}.weight[0]", weight)
                cmds.refresh()

                tween_name = f"{name_prefix}_w{int(weight * 1000):03d}"
                dup = cmds.duplicate(
                    self.keyframes.base_mesh, name=tween_name, returnRootsOnly=True
                )[0]
                cmds.delete(dup, constructionHistory=True)

                cmds.setAttr(f"{self.keyframes.blendshape}.weight[0]", 0.0)
                cmds.refresh()

                cmds.parent(dup, group)

                cmds.blendShape(
                    self.keyframes.blendshape,
                    edit=True,
                    inBetween=True,
                    target=(self.keyframes.base_mesh, 0, dup, weight),
                )

                self.tag_tween_mesh(dup, weight)
                created_tweens.append(Target(dup))
                existing_weights.add(weight)

        finally:
            cmds.setAttr(f"{self.keyframes.blendshape}.weight[0]", original_weight)

        self.logger.info(
            f"Created {len(created_tweens)} tween meshes at weights: {[t.weight for t in created_tweens]}"
        )
        return created_tweens

    def create_frame_based_tween(self, target_frame: int) -> Optional[Target]:
        """Create a tween mesh at a specific animation frame."""
        try:
            start_frame, end_frame = self.keyframes.get_frame_range()
        except ValueError as e:
            self.logger.error(str(e))
            return None

        if not (start_frame < target_frame < end_frame):
            self.logger.error(
                f"Frame {target_frame} must be between {start_frame} and {end_frame}"
            )
            return None

        weight = Weights.frame_to_weight(target_frame, start_frame, end_frame)

        existing_weights = self.get_existing_weights()
        if weight in existing_weights:
            self.logger.warning(
                f"Weight {weight:.3f} already exists for frame {target_frame}"
            )
            self.logger.info(f"Existing in-between weights: {sorted(existing_weights)}")

            offset_weight = self.find_nearby_weight(weight, existing_weights)
            if offset_weight:
                self.logger.info(
                    f"Creating tween at nearby weight {offset_weight:.3f} instead"
                )
                weight = offset_weight
            else:
                self.logger.error("Cannot find suitable alternative weight")
                return None

        original_weight = cmds.getAttr(f"{self.keyframes.blendshape}.weight[0]")
        original_time = cmds.currentTime(query=True)

        try:
            cmds.currentTime(target_frame)
            cmds.setAttr(f"{self.keyframes.blendshape}.weight[0]", weight)
            cmds.refresh()

            tween_name = f"tween_f{target_frame}_w{int(weight * 1000):03d}"
            dup = cmds.duplicate(
                self.keyframes.base_mesh, name=tween_name, returnRootsOnly=True
            )[0]
            cmds.delete(dup, constructionHistory=True)

            cmds.setAttr(f"{self.keyframes.blendshape}.weight[0]", 0.0)
            cmds.refresh()

            try:
                cmds.blendShape(
                    self.keyframes.blendshape,
                    edit=True,
                    inBetween=True,
                    target=(self.keyframes.base_mesh, 0, dup, weight),
                )
            except RuntimeError as e:
                if "Weights must be unique" in str(e):
                    self.logger.error(
                        f"Weight {weight:.3f} already exists in blendShape"
                    )
                    cmds.delete(dup)
                    return None
                raise

            self.tag_tween_mesh(dup, weight, target_frame)

            group_name = "_morphInbetweens_GRP"
            if not cmds.objExists(group_name):
                group = cmds.group(empty=True, name=group_name)
            else:
                group = group_name
            cmds.parent(dup, group)

            self.logger.info(
                f"Created frame-based tween: {tween_name} (frame {target_frame}, weight {weight:.3f})"
            )
            return Target(dup)

        finally:
            cmds.setAttr(f"{self.keyframes.blendshape}.weight[0]", original_weight)
            cmds.currentTime(original_time)

    def tag_tween_mesh(
        self, mesh: str, weight: float, target_frame: Optional[int] = None
    ) -> None:
        """Add metadata attributes to ``mesh``. Idempotent (safe to re-tag)."""
        if not Attributes.has_attr(mesh, "isInbetweenTarget"):
            cmds.addAttr(
                mesh, longName="isInbetweenTarget", attributeType="bool", keyable=False
            )
        cmds.setAttr(f"{mesh}.isInbetweenTarget", True)

        if not Attributes.has_attr(mesh, "inbetweenWeight"):
            cmds.addAttr(
                mesh, longName="inbetweenWeight", attributeType="double", keyable=False
            )
        cmds.setAttr(f"{mesh}.inbetweenWeight", weight)

        if not Attributes.has_attr(mesh, "blendShapeNode"):
            cmds.addAttr(mesh, longName="blendShapeNode", dataType="string")
        cmds.setAttr(
            f"{mesh}.blendShapeNode", str(self.keyframes.blendshape), type="string"
        )

        if not Attributes.has_attr(mesh, "baseMesh"):
            cmds.addAttr(mesh, longName="baseMesh", dataType="string")
        cmds.setAttr(f"{mesh}.baseMesh", str(self.keyframes.base_mesh), type="string")

        if target_frame is not None:
            if not Attributes.has_attr(mesh, "targetFrame"):
                cmds.addAttr(
                    mesh, longName="targetFrame", attributeType="long", keyable=False
                )
            cmds.setAttr(f"{mesh}.targetFrame", target_frame)

    def get_existing_weights(self) -> Set[float]:
        """Return all in-between weights known for the current blendShape.

        Sourced from tagged tween meshes (the SSoT — see ``tag_tween_mesh``).

        The original implementation also queried Maya via
        ``cmds.blendShape(query=True, inBetween=True, target=(mesh, 0))`` — but
        Maya rejects that form (``target`` must be bool when query is set), so
        the call always raised ``TypeError`` and the bare except returned an
        empty set. Bug 8 exposed it; the tag-based source already covers what
        we need for duplicate detection.
        """
        return {tween.weight for tween in Targets.find_all_targets()}

    def find_nearby_weight(
        self,
        target_weight: float,
        existing_weights: Set[float],
        tolerance: float = 0.01,
    ) -> Optional[float]:
        """Find a nearby weight that doesn't conflict with existing weights."""
        for offset in [0.001, -0.001, 0.002, -0.002, 0.005, -0.005, 0.01, -0.01]:
            candidate = Weights.round_weight(target_weight + offset)
            if 0.0 < candidate < 1.0 and candidate not in existing_weights:
                return candidate
        return None
