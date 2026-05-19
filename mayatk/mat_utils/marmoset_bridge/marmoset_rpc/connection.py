# !/usr/bin/python
# coding=utf-8
"""Maya-side JSON-RPC client for the marmoset_rpc Toolbag plugin.

Thin DCC-specific binding around :class:`pythontk.RpcClient` -- pre-fills
the Toolbag port and exe finder so callers can just say
``MarmosetConnection()`` and have it Just Work.

This module sits next to its sibling :mod:`mayatk.mat_utils.marmoset_bridge`
inside the same parent subpackage. The two share Toolbag knowledge but
have opposite trade-offs:

* :mod:`marmoset_bridge` (parent) -- launches a fresh Toolbag and feeds
  it a rendered Python script via ``toolbag.exe -run``. Fire-and-forget,
  safe by default.
* :mod:`marmoset_bridge.marmoset_rpc` (this module) -- talks to a Toolbag
  that is *already running* with the plugin loaded. Targets the live
  scene. Caller is responsible for asking the user before mutating that
  scene.

Usage::

    from mayatk.mat_utils.marmoset_bridge.marmoset_rpc import (
        MarmosetConnection,
    )

    conn = MarmosetConnection()
    if conn.ping():
        print(conn.invoke("system.version"))
    else:
        print("No Toolbag with marmoset_rpc plugin reachable.")
"""
from pythontk.net_utils.rpc.client import RpcClient


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _find_toolbag():
    """AppLauncher hook bound at construction time for the default finder."""
    from pythontk import AppLauncher
    return AppLauncher.find_app("toolbag")


class MarmosetConnection(RpcClient):
    """JSON-RPC client bound to Toolbag's default port + finder.

    By default, :meth:`connect` reuses an already-running Toolbag if it
    answers on the configured port. Pass ``force_new=True`` to always
    launch a fresh instance instead -- the safer default for scripts
    that don't want to mutate someone's open scene.

    To pass an explicit Toolbag executable path, use the base class
    kwarg name: ``conn.connect(exe=r"C:\\Path\\To\\toolbag.exe")``.
    """

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        super().__init__(
            host=host,
            port=port,
            app_label="Marmoset Toolbag",
            find_exe=_find_toolbag,
        )
