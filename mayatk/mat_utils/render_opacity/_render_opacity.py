# !/usr/bin/python
# coding=utf-8
"""Deprecated import path -- ``RenderOpacity`` is :class:`RenderEffects`.

The per-object opacity tool grew into the per-object render-effects tool
(``opacity`` is its first channel, ``highlight`` its second), and the class
moved to :mod:`mayatk.mat_utils.render_opacity.render_effects`. Reaching
``RenderOpacity`` here -- or as ``mtk.RenderOpacity`` -- still returns that
class, but warns through ``ptk.Deprecation.attributes`` and stops working in
mayatk 0.20.0 (it resolved silently from 2026-09-05 to 2026-09-21). Import
``RenderEffects``.
"""

import pythontk as ptk

ptk.Deprecation.attributes(
    globals(),
    {"RenderOpacity": "mayatk.mat_utils.render_opacity.render_effects.RenderEffects"},
    remove_in="0.20.0",
    since="2026-09-23",
)
