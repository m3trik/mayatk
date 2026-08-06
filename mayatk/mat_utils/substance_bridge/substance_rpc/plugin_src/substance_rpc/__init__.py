# !/usr/bin/python
# coding=utf-8
"""Substance 3D Painter RPC plugin -- entry point.

Painter's Python plugin loader imports this package from
``.../Adobe Substance 3D Painter/python/plugins`` and calls
:func:`start_plugin` / :func:`close_plugin` on enable / disable (the
plugin appears under Painter's **Python** menu). The work happens in
three sibling modules, mirroring the marmoset_rpc plugin layout:

* :mod:`.registry` -- decorator-based op registry (pure Python).
* :mod:`.server`   -- HTTP JSON-RPC server; one daemon thread.
* :mod:`.ops`      -- op implementations; importing the package
                      triggers each module's ``@register(...)`` calls.

Adding an op = drop a function with ``@register("ns.name")`` into any
module under ``ops/`` (or extend an existing module). Nothing else needs
touching.

Importing this file binds no port and touches no Painter API -- slot
discovery (which imports every ``*.py`` under mayatk) stays inert. The
server starts only via :func:`start_plugin` (Painter's lifecycle hook),
and :func:`.server.autostart` additionally gates on actually being
hosted by Painter, so even a stray ``start_plugin()`` call elsewhere is
a no-op.
"""
from . import registry           # noqa: F401  -- public re-export
from . import ops                # noqa: F401  -- triggers @register side-effects
from .server import start_server, stop_server, is_running, autostart  # noqa: F401
from .registry import register, all_ops, describe, get as get_op  # noqa: F401


def start_plugin():
    """Painter lifecycle hook: start the RPC server (idempotent)."""
    try:
        address = autostart()
    except Exception as exc:  # noqa: BLE001
        # Never take Painter down over a port squabble -- log + continue.
        print(f"[substance_rpc] server failed to start: {exc}")
        return
    if address is not None:
        print(f"[substance_rpc] plugin started; RPC on {address[0]}:{address[1]}")


def close_plugin():
    """Painter lifecycle hook: shut the RPC server down.

    Also drops the deferred project-setup listener: a disabled plugin must
    not keep rewriting the next project that opens.
    """
    stop_server()
    try:
        from .ops import setup_ops

        setup_ops.teardown()
    except Exception as exc:  # noqa: BLE001 -- shutdown must never raise
        print(f"[substance_rpc] setup teardown failed: {exc}")
