# !/usr/bin/python
# coding=utf-8
"""AutoInstancer's instancing strategy: the Maya binding of the ptk engine.

The decision tree, its config and the strategy enum live once in pythontk's
instancing engine; this module keeps the three names at their mayatk path and
supplies the one scene read, the prototype's triangle count (``polyEvaluate``).
"""

from __future__ import annotations

import pythontk as ptk
from pythontk import StrategyConfig, StrategyType

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None

__all__ = ["InstancingStrategy", "StrategyConfig", "StrategyType"]


class InstancingStrategy(ptk.InstancingStrategy):
    """:class:`pythontk.InstancingStrategy` counting triangles in the Maya scene."""

    def _get_triangle_count(self, mesh_node: object) -> int:
        try:
            # -t returns the triangle count
            return int(cmds.polyEvaluate(mesh_node, triangle=True))
        except Exception:
            return 0
