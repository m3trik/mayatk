# !/usr/bin/python
# coding=utf-8
"""Painter-specific system ops: version reporting.

Liveness and introspection (``system.ping`` / ``system.list_ops`` /
``system.describe`` / ``system.identity``) are registered by the shared core --
they are part of the :class:`pythontk.RpcClient` contract, so no plugin has to
remember them and no per-plugin copy can drift.

There is no op that evaluates code: a script runs through ``script.start``
(:mod:`.script_ops`), the one way in, which waits for Painter to be idle and
puts the run's edits in one undo entry.

``substance_painter`` is imported lazily inside each op so this module stays
import-safe outside Painter (tests exercise the server + registry against these
ops with no DCC present).
"""

from .. import register


@register("system.version")
def version():
    """Return Painter + plugin API version info (best-effort)."""
    info = {"plugin": "substance_rpc"}
    try:
        import substance_painter  # noqa: PLC0415

        info["api_version"] = getattr(substance_painter, "__version__", None)
        try:
            import substance_painter.application  # noqa: PLC0415

            info["painter_version"] = substance_painter.application.version()
        except Exception:  # noqa: BLE001
            info["painter_version"] = None
    except ImportError:
        info["api_version"] = None
        info["painter_version"] = None
    return info
