# !/usr/bin/python
# coding=utf-8
"""AutoInstancer's instancing strategy: pythontk's engine, at its mayatk path.

The decision tree, its config and the strategy enum live once in pythontk's
instancing engine and are re-exported here. Its one scene read, the
prototype's triangle count, is an adapter the AutoInstancer injects
(``count_triangles=AutoInstancer._triangle_count``, Maya's ``polyEvaluate``).
"""

from pythontk import InstancingStrategy, StrategyConfig, StrategyType

__all__ = ["InstancingStrategy", "StrategyConfig", "StrategyType"]
