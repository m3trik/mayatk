# !/usr/bin/python
# coding=utf-8
"""System-level ops: heartbeat, introspection, Toolbag version."""
from ..registry import register, all_ops, describe


@register("system.ping")
def ping():
    """Heartbeat -- proves the plugin is alive. Returns ``"pong"``."""
    return "pong"


@register("system.list_ops")
def list_ops():
    """Sorted list of every registered op name."""
    return all_ops()


@register("system.describe")
def describe_op(op=""):
    """Return the JSON-friendly description of *op* or all ops if empty.

    Each entry: ``{"name", "doc", "params": [{"name", "default"}, ...]}``.
    Mirrors substancetk's introspection -- lets a client (agent or human)
    discover the surface without reading source.
    """
    return describe(op or None)


@register("system.version")
def version():
    """Toolbag build number (e.g. ``5022``). ``None`` outside Toolbag."""
    try:
        import mset  # noqa: PLC0415 -- lazy: keep registry import-safe.
        return mset.getToolbagVersion()
    except Exception:
        return None
