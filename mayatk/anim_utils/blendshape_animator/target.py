# !/usr/bin/python
# coding=utf-8
"""Tween mesh wrappers and registry for blendShape in-between targets."""
from typing import Dict, List, Optional

import pythontk as ptk

try:
    from maya import cmds
except ImportError as error:
    print(__file__, error)

from mayatk.node_utils.attributes._attributes import Attributes

from mayatk.anim_utils.blendshape_animator.weights import Weights


class Target:
    """Represents a single target/in-between target mesh."""

    def __init__(self, mesh: str):
        self.mesh = mesh
        self._validate_target_mesh()

    def _validate_target_mesh(self) -> None:
        """Validate this is a proper target mesh."""
        required_attrs = [
            "isInbetweenTarget",
            "inbetweenWeight",
            "blendShapeNode",
            "baseMesh",
        ]
        for attr in required_attrs:
            if not Attributes.has_attr(self.mesh, attr):
                raise ValueError(f"Mesh {self.mesh} missing required attribute: {attr}")

    @property
    def weight(self) -> float:
        """Get the weight value for this tween."""
        return Weights.round_weight(cmds.getAttr(f"{self.mesh}.inbetweenWeight"))

    @property
    def blendshape_name(self) -> str:
        """Get the blendShape node name this tween targets."""
        return str(cmds.getAttr(f"{self.mesh}.blendShapeNode"))

    @property
    def base_mesh_name(self) -> str:
        """Get the base mesh name this tween applies to."""
        return str(cmds.getAttr(f"{self.mesh}.baseMesh"))

    @property
    def target_frame(self) -> Optional[int]:
        """Get target frame if this tween was created from a specific frame."""
        if Attributes.has_attr(self.mesh, "targetFrame"):
            return int(cmds.getAttr(f"{self.mesh}.targetFrame"))
        return None

    def update_references(self, new_blendshape: str, new_base_mesh: str) -> None:
        """Update this tween's references to new blendShape/base mesh."""
        cmds.setAttr(f"{self.mesh}.blendShapeNode", str(new_blendshape), type="string")
        cmds.setAttr(f"{self.mesh}.baseMesh", str(new_base_mesh), type="string")
        Targets.logger.info(f"  Updated {self.mesh} references")


class Targets(ptk.LoggingMixin):
    """Manages collections of tween meshes."""

    DEFAULT_GROUPS = ["_morphInbetweens_GRP", "_preciseTweens_GRP"]

    @classmethod
    def find_all_targets(cls) -> List[Target]:
        """Find all tween meshes in the scene (deduplicated)."""
        seen = set()
        candidates = []

        for group_name in cls.DEFAULT_GROUPS:
            if cmds.objExists(group_name):
                children = (
                    cmds.listRelatives(group_name, children=True, type="transform") or []
                )
                for child in children:
                    if child not in seen:
                        seen.add(child)
                        candidates.append(child)

        # Loose tween meshes outside known groups: scan transforms once and skip
        # anything we've already collected. Slow on huge scenes — acceptable
        # for the workflows this tool supports.
        for n in cmds.ls(type="transform") or []:
            if n in seen:
                continue
            if Attributes.has_attr(n, "isInbetweenTarget"):
                seen.add(n)
                candidates.append(n)

        tweens = []
        for mesh in candidates:
            try:
                if cmds.getAttr(f"{mesh}.isInbetweenTarget"):
                    tweens.append(Target(mesh))
            except ValueError:
                cls.logger.warning(f"Skipping invalid tween mesh: {mesh}")
                continue

        return sorted(tweens, key=lambda t: t.weight)

    @classmethod
    def group_by_weight(cls, tweens: List[Target]) -> Dict[float, List[Target]]:
        """Group tweens by weight value, handling duplicates."""
        weight_groups: Dict[float, List[Target]] = {}
        for tween in tweens:
            weight_groups.setdefault(tween.weight, []).append(tween)
        return weight_groups

    @classmethod
    def update_all_references(
        cls, new_blendshape: str, new_base_mesh: str
    ) -> int:
        """Update all tween mesh references to new nodes."""
        tweens = cls.find_all_targets()
        for tween in tweens:
            tween.update_references(new_blendshape, new_base_mesh)
        cls.logger.info(f"Updated {len(tweens)} tween references")
        return len(tweens)
