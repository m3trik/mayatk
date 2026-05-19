# !/usr/bin/python
# coding=utf-8
"""Op modules for the marmoset_rpc plugin.

Importing this package imports each op module, which triggers their
``@register(...)`` decorators -- one-shot side effect that populates the
registry. Add a new op module here and to one of the existing files;
nothing else needs touching.

Op modules MUST lazy-import ``mset`` (inside function bodies, not at
module top) so the registry stays import-safe in environments where
``mset`` is unavailable (tests, agent inspection, etc.).
"""
from . import system_ops  # noqa: F401
from . import scene_ops   # noqa: F401
