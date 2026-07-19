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
from pythontk import Weights


class Creator(ptk.LoggingMixin):
    """Creates in-between target meshes for custom animation curves."""

    def __init__(self, keyframes: Keyframes):
        super().__init__()
        self.keyframes = keyframes

    # =========================================================================
    # Shared building blocks
    # =========================================================================

    def _ensure_group(self, group_name: str = Targets.DEFAULT_GROUPS[0]) -> str:
        """Return ``group_name``, creating the (empty) group if missing."""
        if not cmds.objExists(group_name):
            return cmds.group(empty=True, name=group_name)
        return group_name

    def _duplicate_at_weight(self, name: str, weight: float) -> str:
        """Duplicate the base mesh frozen at ``weight``, history-free.

        Leaves the blendShape weight at 0.0; callers restore the original
        weight when their batch completes.
        """
        cmds.setAttr(f"{self.keyframes.blendshape}.weight[0]", weight)
        cmds.refresh()
        dup = cmds.duplicate(
            self.keyframes.base_mesh, name=name, returnRootsOnly=True
        )[0]
        cmds.delete(dup, constructionHistory=True)
        cmds.setAttr(f"{self.keyframes.blendshape}.weight[0]", 0.0)
        cmds.refresh()
        return dup

    def _add_inbetween(
        self, mesh: str, weight: float, target_frame: Optional[int] = None
    ) -> None:
        """Register ``mesh`` as an in-between at ``weight`` and tag it."""
        cmds.blendShape(
            self.keyframes.blendshape,
            edit=True,
            inBetween=True,
            target=(self.keyframes.base_mesh, 0, mesh, weight),
        )
        self.tag_tween_mesh(mesh, weight, target_frame)

    # =========================================================================
    # Public API
    # =========================================================================

    def create_weight_based_tweens(
        self,
        weights: List[float],
        group_name: str = Targets.DEFAULT_GROUPS[0],
        name_prefix: str = "morph_ib",
    ) -> List[Target]:
        """Create tween meshes at specific weight values.

        Weights outside the open interval (0, 1) are skipped with a warning
        (0 is the base shape, 1 the full target — neither is an in-between).
        Weights already taken by this setup are offset to a nearby free slot
        or skipped (Bug 3 — previously crashed mid-batch on the first
        duplicate).
        """
        original_weight = cmds.getAttr(f"{self.keyframes.blendshape}.weight[0]")
        created_tweens: List[Target] = []
        existing_weights = self.get_existing_weights()

        try:
            for raw_weight in weights:
                weight = Weights.round_weight(raw_weight)

                if not 0.0 < weight < 1.0:
                    self.logger.warning(
                        f"Skipping weight {weight:.3f}: in-betweens must lie "
                        "strictly between 0 and 1"
                    )
                    continue

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

                tween_name = f"{name_prefix}_w{int(weight * 1000):03d}"
                dup = self._duplicate_at_weight(tween_name, weight)
                self._add_inbetween(dup, weight)
                cmds.parent(dup, self._ensure_group(group_name))

                created_tweens.append(Target(dup))
                existing_weights.add(weight)

        finally:
            cmds.setAttr(f"{self.keyframes.blendshape}.weight[0]", original_weight)

        self.logger.info(
            f"Created {len(created_tweens)} tween meshes at weights: {[t.weight for t in created_tweens]}"
        )
        return created_tweens

    def create_frame_based_tween(
        self,
        target_frame: int,
        group_name: str = Targets.DEFAULT_GROUPS[0],
        name_prefix: str = "tween",
    ) -> Optional[Target]:
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
            if offset_weight is None:
                self.logger.error("Cannot find suitable alternative weight")
                return None
            self.logger.info(
                f"Creating tween at nearby weight {offset_weight:.3f} instead"
            )
            weight = offset_weight

        original_weight = cmds.getAttr(f"{self.keyframes.blendshape}.weight[0]")
        original_time = cmds.currentTime(query=True)

        try:
            cmds.currentTime(target_frame)

            tween_name = f"{name_prefix}_f{target_frame}_w{int(weight * 1000):03d}"
            dup = self._duplicate_at_weight(tween_name, weight)

            # Attach before parenting: if the defensive duplicate-weight
            # discard fires (older Maya raises here; 2025 silently replaces
            # the occupied slot), no tween group has been created yet.
            try:
                self._add_inbetween(dup, weight, target_frame)
            except RuntimeError as e:
                if "Weights must be unique" in str(e):
                    self.logger.error(
                        f"Weight {weight:.3f} already exists in blendShape"
                    )
                    cmds.delete(dup)
                    return None
                raise

            cmds.parent(dup, self._ensure_group(group_name))

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
        specs = [
            ("isInbetweenTarget", {"attributeType": "bool", "keyable": False}, True, {}),
            ("inbetweenWeight", {"attributeType": "double", "keyable": False}, weight, {}),
            (
                "blendShapeNode",
                {"dataType": "string"},
                str(self.keyframes.blendshape),
                {"type": "string"},
            ),
            (
                "baseMesh",
                {"dataType": "string"},
                str(self.keyframes.base_mesh),
                {"type": "string"},
            ),
        ]
        if target_frame is not None:
            specs.append(
                (
                    "targetFrame",
                    {"attributeType": "long", "keyable": False},
                    int(target_frame),
                    {},
                )
            )

        for attr, add_kwargs, value, set_kwargs in specs:
            if not Attributes.has_attr(mesh, attr):
                cmds.addAttr(mesh, longName=attr, **add_kwargs)
            cmds.setAttr(f"{mesh}.{attr}", value, **set_kwargs)

    def get_existing_weights(self) -> Set[float]:
        """Return the in-between weights already taken by THIS setup.

        Sourced from tagged tween meshes (the SSoT — see ``tag_tween_mesh``),
        scoped to this blendShape + base mesh so sibling setups in the same
        scene can't collide with (or get silently offset by) weights that
        aren't actually taken on this blendShape. The tags are authoritative;
        Maya's ``blendShape -q`` target queries are not used (they don't
        round-trip in-between weights reliably).
        """
        return {
            tween.weight
            for tween in Targets.find_all_targets(
                blendshape=self.keyframes.blendshape,
                base_mesh=self.keyframes.base_mesh,
            )
        }

    def find_nearby_weight(
        self,
        target_weight: float,
        existing_weights: Set[float],
        tolerance: float = 0.01,
    ) -> Optional[float]:
        """Find a free weight within ``tolerance`` of ``target_weight``."""
        for offset in (0.001, -0.001, 0.002, -0.002, 0.005, -0.005, 0.01, -0.01):
            if abs(offset) > tolerance + 1e-9:
                continue
            candidate = Weights.round_weight(target_weight + offset)
            if 0.0 < candidate < 1.0 and candidate not in existing_weights:
                return candidate
        return None
