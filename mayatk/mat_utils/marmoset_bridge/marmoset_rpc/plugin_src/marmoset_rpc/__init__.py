# !/usr/bin/python
# coding=utf-8
"""Marmoset Toolbag RPC plugin -- entry point.

Toolbag's plugin loader imports this on startup. The generic half (op registry,
main-thread marshalling, the HTTP routes :class:`pythontk.RpcClient` speaks) is
:mod:`._rpc_core` -- a **staged verbatim copy** of
``pythontk.net_utils.rpc.plugin_core``, because an installed plugin has no
``pythontk`` on Toolbag's ``sys.path``. Never edit it here; edit the pythontk
source and re-run ``m3trik/scripts/sync_rpc_core.py``.

So this package contributes only what is actually Toolbag-specific:

* the :data:`PLUGIN` configuration below (host module, env prefix), and
* :mod:`.ops` -- the op implementations; importing the package triggers each
  module's ``@register(...)`` calls.

Adding an op = drop a function with ``@register("ns.name")`` into any module
under ``ops/``. ``system.ping`` / ``system.list_ops`` / ``system.describe`` come
free from the core, so only Toolbag-specific ops live here.

The plugin **announces** itself (:class:`pythontk.RpcInstances`): it binds a
port the OS picks and writes an instance record (its PID, port and a token every
call must carry) that goes when it stops. A client finds a Toolbag by that
record (``MarmosetConnection.connect`` / ``attach``), never by a fixed port, a
launch by the PID it started, and any number of Toolbags serve at once.

In Toolbag it serves from Toolbag's own update callback, with no thread
(:func:`toolbag_pump`): Toolbag ends (0xC0000409) once a plugin's script
returns with a Python thread still running. Toolbag lists it under its
**Plugins** menu (``__main__.py``) and runs it when a person picks it there; a
bridge launches its own Toolbag with ``-run`` on that file. It stops when
Toolbag shuts the plugin down (``onShutdownPlugin``).

Auto-start is gated to the Toolbag host: importing this file outside Toolbag
(e.g. tentacle/mayatk slot discovery, which imports every ``*.py`` under mayatk
to introspect classes) is inert and never binds a port.
``MARMOSET_RPC_AUTOSTART=0`` forces it off even inside Toolbag, which unit tests
rely on. See :meth:`RpcPlugin.autostart`.
"""

from ._rpc_core import RpcPlugin  # noqa: F401 -- re-export for tests/tooling

#: The one plugin instance. Its registry and marshaller are the module-level
#: `register` / `run_on_main_thread` the op modules import.
PLUGIN = RpcPlugin(
    label="marmoset_rpc",
    host_module="mset",
    env_prefix="MARMOSET_RPC",
    default_port=0,  # the OS picks; the instance record says which
    announce=True,
)

registry = PLUGIN.registry
register = registry.register
run_on_main_thread = PLUGIN.marshaller.run

# Imported AFTER the names above are bound: the op modules import them from this
# partially-initialised package, which is what makes `@register` work at import time.
# Via `import_ops` rather than a plain `from . import ops`, so a host reload --
# which rebuilds PLUGIN with an empty registry but leaves the ops submodules
# cached -- still re-runs the `@register` decorators. A plain import is a cache
# hit there, and the server comes back serving only `system.*`.
ops = PLUGIN.import_ops(f"{__name__}.ops")  # noqa: E402


def start_server(port=None, host=None):
    """Start the RPC server (idempotent). Returns the bound ``(host, port)``."""
    return PLUGIN.start(port=port, host=host)


def stop_server():
    """Shut the server down (tests / hot-reload)."""
    PLUGIN.stop()


def is_running():
    """True while the server is bound."""
    return PLUGIN.is_running()


def autostart():
    """Start on plugin load, gated to the Toolbag host. ``None`` when declined."""
    return PLUGIN.autostart()


def toolbag_pump(tick):
    """Run the server's *tick* from Toolbag's update callback (chained after
    any callback already there), and stop serving when Toolbag shuts the
    plugin down: the :class:`RpcPlugin` ``pump`` for Toolbag."""
    import mset  # noqa: PLC0415 -- Toolbag's own module

    callbacks = mset.callbacks
    update, shutdown = callbacks.onPeriodicUpdate, callbacks.onShutdownPlugin

    def on_update():
        tick()
        if callable(update):
            update()

    def on_shutdown():
        PLUGIN.stop()
        if callable(shutdown):
            shutdown()

    callbacks.onPeriodicUpdate = on_update
    callbacks.onShutdownPlugin = on_shutdown


if PLUGIN.is_hosted():
    PLUGIN.pump = toolbag_pump

# Auto-start on plugin load -- but only when actually hosted by Toolbag, and never
# at the cost of taking Toolbag down over a port squabble.
PLUGIN.autostart_safely()
