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

Set ``MARMOSET_RPC_AUTOSTART=0`` to disable the auto-start (useful for
unit tests that want to import without binding a port).
"""
import os

from . import registry           # noqa: F401  -- public re-export
from . import ops                # noqa: F401  -- triggers @register side-effects
from .server import start_server, stop_server, is_running  # noqa: F401
from .registry import register, all_ops, describe, get as get_op  # noqa: F401


# Auto-start on plugin load. Suppressed when MARMOSET_RPC_AUTOSTART=0
# so tests can import the module without binding a port.
if os.environ.get("MARMOSET_RPC_AUTOSTART", "1") == "1":
    try:
        start_server()
    except Exception as exc:  # noqa: BLE001
        # Never take Toolbag down if a port is in use -- log + continue.
        print(f"[marmoset_rpc] server failed to start: {exc}")
