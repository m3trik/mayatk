# !/usr/bin/python
# coding=utf-8
"""Marmoset Toolbag JSON-RPC bridge -- target a running Toolbag instance.

The "talk to a running Toolbag" half of :mod:`marmoset_bridge`. Both
share the same parent subpackage so the two trade-offs sit side by side:

* :mod:`marmoset_bridge`              -- launches a fresh Toolbag and
  runs a templated script. Safe by default; never touches a live session.
* :mod:`marmoset_bridge.marmoset_rpc` -- talks to a Toolbag that's
  already running with the plugin loaded. Faster, but caller must
  confirm before mutating the open scene.

Quickstart::

    from mayatk.mat_utils.marmoset_bridge.marmoset_rpc import (
        MarmosetConnection, install,
    )

    install()                                # one-time per Toolbag major
    # ... user starts Toolbag manually ...

    conn = MarmosetConnection()
    if conn.ping():
        print(conn.invoke("system.version"))  # -> 5022
        print(conn.list_ops())                # -> ['system.list_ops', ...]

Adding an op is one decorator + function in the plugin file
(``plugin_src/marmoset_rpc/__init__.py``). See that module's docstring.
"""
from .connection import (  # noqa: F401
    MarmosetConnection,
    DEFAULT_HOST,
    DEFAULT_PORT,
)
from .installer import (  # noqa: F401
    install,
    uninstall,
    is_installed,
    user_plugin_dir,
)
from .job import (  # noqa: F401
    Call,
    Result,
    run_batch,
)

__all__ = [
    "MarmosetConnection",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "install",
    "uninstall",
    "is_installed",
    "user_plugin_dir",
    "Call",
    "Result",
    "run_batch",
]
