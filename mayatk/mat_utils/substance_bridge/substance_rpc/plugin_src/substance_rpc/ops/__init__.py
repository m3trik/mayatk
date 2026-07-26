# !/usr/bin/python
# coding=utf-8
"""Op implementations for the substance_rpc plugin.

Importing this package imports every op module, which triggers their
``@register(...)`` side effects. Keep the modules import-safe outside
Painter: import ``substance_painter`` lazily inside each op body, never
at module top level.
"""
from . import system_ops   # noqa: F401
from . import project_ops  # noqa: F401
