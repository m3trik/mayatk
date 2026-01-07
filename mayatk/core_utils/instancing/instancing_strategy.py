# !/usr/bin/python
# coding=utf-8
"""Instancing strategy logic for AutoInstancer."""
from __future__ import annotations

from enum import Enum
from dataclasses import dataclass
from typing import Optional

try:
    import pymel.core as pm
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
    """Determines the best instancing strategy for a group of objects."""

    def __init__(self, config: StrategyConfig):
        self.config = config

    def evaluate(
        self,
        group_size: int,
        mesh_node: Optional[pm.nodetypes.Mesh] = None,
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
        is_micro = tri_count < 300  # or expected_on_screen_size is tiny

        if is_micro:
            if group_size >= 10:
                return StrategyType.COMBINE  # or BAKE if purely decorative
            else:
                # If micro and repeat_count < 10 -> COMBINE unless it's a lone unique prop
                if group_size > 1:
                    return StrategyType.COMBINE
                else:
                    return StrategyType.KEEP_SEPARATE

        # 2) Instancing eligibility gate
        if not self.config.can_gpu_instance:
            return StrategyType.COMBINE  # Static

        # 3) Worth-instancing gate (repeat + triangle thresholds)
        if self.config.will_be_lightmapped:
            # Stricter thresholds for lightmapped objects
            if tri_count >= 1500 and group_size >= 10:
                return StrategyType.GPU_INSTANCE
        else:
            # Standard thresholds
            if tri_count >= 800 and group_size >= 10:
                return StrategyType.GPU_INSTANCE

        # Heavy-mesh exception
        if tri_count >= 5000 and group_size >= 3:
            return StrategyType.GPU_INSTANCE

        # 4) Default fallback
        # Static -> COMBINE
        return StrategyType.COMBINE

    def _get_triangle_count(self, mesh_node: pm.nodetypes.Mesh) -> int:
        try:
            # polyEvaluate returns a dict or int depending on args
            # -t returns triangle count
            count = pm.polyEvaluate(mesh_node, triangle=True)
            return int(count)
        except Exception:
            return 0
