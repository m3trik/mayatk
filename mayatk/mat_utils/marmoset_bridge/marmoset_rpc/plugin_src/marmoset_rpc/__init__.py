# !/usr/bin/python
# coding=utf-8
"""Marmoset Toolbag RPC plugin -- entry point.

Toolbag's plugin loader imports this on startup. The work happens in
three sibling modules:

* :mod:`.registry` -- decorator-based op registry (pure Python).
* :mod:`.server`   -- HTTP JSON-RPC server; one daemon thread.
* :mod:`.ops`      -- op implementations; importing the package
                      triggers each module's ``@register(...)`` calls.

Adding an op = drop a function with ``@register("ns.name")`` into any
module under ``ops/`` (or extend an existing module). Nothing else needs
touching.

Auto-start is gated to the Toolbag host: importing this file outside
Toolbag (e.g. tentacle/mayatk slot discovery, which imports every
``*.py`` under mayatk to introspect classes) is inert and never binds a
port. ``MARMOSET_RPC_AUTOSTART=0`` forces it off even inside Toolbag,
which unit tests rely on. See :func:`.server.autostart`.
"""
from . import registry           # noqa: F401  -- public re-export
from . import ops                # noqa: F401  -- triggers @register side-effects
from .server import start_server, stop_server, is_running, autostart  # noqa: F401
from .registry import register, all_ops, describe, get as get_op  # noqa: F401


# Auto-start on plugin load -- but only when actually hosted by Toolbag;
# the gate lives in server.autostart so importing this file stays
# side-effect-free everywhere else.
try:
    autostart()
except Exception as exc:  # noqa: BLE001
    # Never take Toolbag down if a port is in use -- log + continue.
    print(f"[marmoset_rpc] server failed to start: {exc}")
