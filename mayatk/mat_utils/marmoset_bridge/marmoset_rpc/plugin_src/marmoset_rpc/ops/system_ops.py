# !/usr/bin/python
# coding=utf-8
"""Toolbag-specific system ops.

Liveness and introspection (``system.ping`` / ``system.list_ops`` /
``system.describe``) are registered by the shared core -- they are part of the
:class:`pythontk.RpcClient` contract, so no plugin has to remember them and no
per-plugin copy can drift. Only what genuinely needs ``mset`` lives here.
"""
from .. import register


@register("system.version")
def version():
    """Toolbag build number (e.g. ``5022``). ``None`` outside Toolbag."""
    try:
        import mset  # noqa: PLC0415 -- lazy: keep the module import-safe.

        return mset.getToolbagVersion()
    except Exception:
        return None
