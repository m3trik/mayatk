# !/usr/bin/python
# coding=utf-8
"""Deprecated import path -- ``RenderOpacity`` is :class:`RenderEffects`.

The per-object opacity tool grew into the per-object render-effects tool
(``opacity`` is its first channel, ``highlight`` its second), and the class
moved to :mod:`mayatk.mat_utils.render_opacity.render_effects`. This alias
holds for ONE release so existing call sites keep working; import
``RenderEffects`` for new code.
"""

from mayatk.mat_utils.render_opacity.render_effects import RenderEffects

RenderOpacity = RenderEffects

__all__ = ["RenderOpacity", "RenderEffects"]
