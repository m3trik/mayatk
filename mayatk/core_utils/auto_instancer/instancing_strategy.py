# !/usr/bin/python
# coding=utf-8
"""Instancing strategy logic for AutoInstancer."""
from __future__ import annotations

from enum import Enum
from dataclasses import dataclass
from typing import Optional

try:
    import maya.cmds as cmds
except ImportError:
    pass


class StrategyType(Enum):
    BAKE = "BAKE"
    COMBINE = "COMBINE"
    GPU_INSTANCE = "GPU_INSTANCE"
    KEEP_SEPARATE = "KEEP_SEPARATE"


@dataclass
class StrategyConfig:
    is_static: bool = True
    needs_individual: bool = False
    will_be_lightmapped: bool = False
    can_gpu_instance: bool = True
    # expected_on_screen_size: float = 1.0 # Optional, skipping for now


class InstancingStrategy:
    """Determines the best instancing strategy for a group of objects.

    Threshold class attributes may be overridden per-instance to tune the
    decision tree without editing this module.
    """

    # Below this triangle count a mesh is "micro" — cheaper combined than
    # instanced (per-draw-call overhead dominates).
    MICRO_TRI_THRESHOLD = 300
    # Minimum repeats before instancing pays for itself.
    MIN_INSTANCE_GROUP_SIZE = 10
    # Standard / lightmapped triangle thresholds for GPU instancing.
    INSTANCE_TRI_THRESHOLD = 800
    INSTANCE_TRI_THRESHOLD_LIGHTMAPPED = 1500
    # Heavy meshes are worth instancing even with few repeats.
    HEAVY_TRI_THRESHOLD = 5000
    HEAVY_MIN_GROUP_SIZE = 3

    def __init__(self, config: StrategyConfig):
        self.config = config

    def evaluate(
        self,
        group_size: int,
        mesh_node: Optional[object] = None,
        triangle_count: Optional[int] = None,
    ) -> StrategyType:
        """
        Evaluate the strategy for a given group.

        Args:
            group_size: Number of items in the group (including prototype).
            mesh_node: The mesh node of the prototype to analyze (optional if triangle_count provided).
            triangle_count: Explicit triangle count (optional, overrides mesh_node).
        """
        # 0) Hard constraints
        if self.config.needs_individual:
            return StrategyType.KEEP_SEPARATE

        if not self.config.is_static:
            # Dynamic objects: prefer GPU_INSTANCE if eligible, else KEEP_SEPARATE
            if self.config.can_gpu_instance:
                return StrategyType.GPU_INSTANCE
            else:
                return StrategyType.KEEP_SEPARATE

        # Get triangle count
        if triangle_count is None:
            if mesh_node:
                tri_count = self._get_triangle_count(mesh_node)
            else:
                tri_count = 0
        else:
            tri_count = triangle_count

        # 1) Micro-geometry gate
        if tri_count < self.MICRO_TRI_THRESHOLD:
            # Repeated micro meshes combine; a lone unique prop stays separate.
            if group_size > 1:
                return StrategyType.COMBINE
            return StrategyType.KEEP_SEPARATE

        # 2) Instancing eligibility gate
        if not self.config.can_gpu_instance:
            return StrategyType.COMBINE  # Static

        # 3) Worth-instancing gate (repeat + triangle thresholds)
        tri_threshold = (
            self.INSTANCE_TRI_THRESHOLD_LIGHTMAPPED
            if self.config.will_be_lightmapped
            else self.INSTANCE_TRI_THRESHOLD
        )
        if tri_count >= tri_threshold and group_size >= self.MIN_INSTANCE_GROUP_SIZE:
            return StrategyType.GPU_INSTANCE

        # Heavy-mesh exception
        if (
            tri_count >= self.HEAVY_TRI_THRESHOLD
            and group_size >= self.HEAVY_MIN_GROUP_SIZE
        ):
            return StrategyType.GPU_INSTANCE

        # 4) Default fallback
        # Static -> COMBINE
        return StrategyType.COMBINE

    def _get_triangle_count(self, mesh_node: object) -> int:
        try:
            # polyEvaluate returns a dict or int depending on args
            # -t returns triangle count
            count = cmds.polyEvaluate(mesh_node, triangle=True)
            return int(count)
        except Exception:
            return 0
