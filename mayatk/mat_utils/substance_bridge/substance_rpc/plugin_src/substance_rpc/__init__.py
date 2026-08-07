# !/usr/bin/python
# coding=utf-8
"""Substance 3D Painter RPC plugin -- entry point.

Painter's Python plugin loader imports this package from
``.../Adobe Substance 3D Painter/python/plugins`` and calls :func:`start_plugin` /
:func:`close_plugin` on enable / disable (the plugin appears under Painter's
**Python** menu).

The generic half (op registry, main-thread marshalling, the HTTP routes
:class:`pythontk.RpcClient` speaks) is :mod:`._rpc_core` -- a **staged verbatim
copy** of ``pythontk.net_utils.rpc.plugin_core``, because an installed plugin has
no ``pythontk`` on Painter's ``sys.path``. Never edit it here; edit the pythontk
source and re-run ``m3trik/scripts/sync_rpc_core.py``.

So this package contributes only what is actually Painter-specific: the
:data:`PLUGIN` configuration below, and :mod:`.ops` -- the op implementations,
whose ``@register(...)`` calls fire when the package is imported.

Adding an op = drop a function with ``@register("ns.name")`` into any module
under ``ops/``. ``system.ping`` / ``system.list_ops`` / ``system.describe`` come
free from the core, so only Painter-specific ops live here.

Importing this file binds no port and touches no Painter API -- slot discovery
(which imports every ``*.py`` under mayatk) stays inert. The server starts only
via :func:`start_plugin`, and the core additionally gates on actually being hosted
by Painter, so even a stray ``start_plugin()`` call elsewhere is a no-op.
"""
from ._rpc_core import RpcPlugin  # noqa: F401 -- re-export for tests/tooling

#: The one plugin instance. Its registry and marshaller are the module-level
#: `register` / `run_on_main_thread` the op modules import.
PLUGIN = RpcPlugin(
    label="substance_rpc",
    host_module="substance_painter",
    env_prefix="SUBSTANCE_RPC",
    default_port=8090,
)

registry = PLUGIN.registry
register = registry.register
run_on_main_thread = PLUGIN.marshaller.run

# Imported AFTER the names above are bound: the op modules import them from this
# partially-initialised package, which is what makes `@register` work at import time.
from . import ops  # noqa: E402,F401 -- triggers the @register side effects


def start_server(port=None, host=None):
    """Start the RPC server (idempotent). Returns the bound ``(host, port)``."""
    return PLUGIN.start(port=port, host=host)


def stop_server():
    """Shut the server down (close_plugin hook / tests / hot-reload)."""
    PLUGIN.stop()


def is_running():
    """True while the server is bound."""
    return PLUGIN.is_running()


def autostart():
    """Start on plugin load, gated to the Painter host. ``None`` when declined."""
    return PLUGIN.autostart()


def start_plugin():
    """Painter lifecycle hook: start the RPC server (idempotent)."""
    address = PLUGIN.autostart_safely()
    if address is not None:
        print(f"[substance_rpc] plugin started; RPC on {address[0]}:{address[1]}")


def close_plugin():
    """Painter lifecycle hook: shut the RPC server down.

    Also drops the deferred project-setup listener: a disabled plugin must not
    keep rewriting the next project that opens.
    """
    PLUGIN.stop()
    try:
        from .ops import setup_ops

        setup_ops.teardown()
    except Exception as exc:  # noqa: BLE001 -- shutdown must never raise
        print(f"[substance_rpc] setup teardown failed: {exc}")
