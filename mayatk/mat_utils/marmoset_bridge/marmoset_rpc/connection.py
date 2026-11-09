# !/usr/bin/python
# coding=utf-8
"""JSON-RPC client bound to the marmoset_rpc Toolbag plugin.

Thin Toolbag-specific binding around :class:`pythontk.RpcClient` --
pre-fills the plugin's label and the exe finder so callers can just say
``MarmosetConnection()`` and have it Just Work. A Toolbag is found by what it
is, never by a port: each one serving the plugin writes an instance record
(:meth:`MarmosetConnection.toolbags`: its PID, port and token), and a client
binds one (:meth:`~pythontk.RpcClient.attach`, :meth:`~pythontk.RpcClient.connect`).

This module sits next to its sibling
:mod:`mayatk.mat_utils.marmoset_bridge._marmoset_engine` inside the same
parent subpackage. The two share Toolbag knowledge but have opposite
trade-offs:

* :class:`MarmosetEngine` -- launches a fresh Toolbag and feeds it a
  rendered Python script via ``toolbag.exe -run``. Fire-and-forget,
  safe by default.
* :mod:`marmoset_rpc` (this module) -- talks to a Toolbag that is
  *already running* with the plugin loaded. Targets the live scene.
  Caller is responsible for asking the user before mutating that scene.

Usage::

    from mayatk.mat_utils.marmoset_bridge.marmoset_rpc import (
        MarmosetConnection,
    )

    conn = MarmosetConnection()
    if conn.attach():  # a running Toolbag serving the plugin, by its record
        print(conn.invoke("system.version"))
    else:
        print("No Toolbag with marmoset_rpc plugin reachable.")
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from pythontk.net_utils.rpc.client import RpcClient


DEFAULT_HOST = "127.0.0.1"
#: No fixed port: the plugin binds one the OS picks, and its record says which.
DEFAULT_PORT = 0


class _MarmosetConnectionInternal(object):
    """Internal helpers for MarmosetConnection."""

    @staticmethod
    def _find_toolbag():
        """AppLauncher hook bound at construction time for the default finder."""
        from pythontk import AppLauncher

        return AppLauncher.find_app("toolbag")


class MarmosetConnection(RpcClient, _MarmosetConnectionInternal):
    """JSON-RPC client of a Toolbag serving the ``marmoset_rpc`` plugin.

    By default, :meth:`connect` reuses an already-running Toolbag serving
    the plugin (its instance record). Pass ``force_new=True`` to always
    launch a fresh instance instead -- it binds the record of the process
    it started, never another Toolbag's -- the safer default for scripts
    that don't want to mutate someone's open scene.

    To pass an explicit Toolbag executable path, use the base class
    kwarg name: ``conn.connect(exe=r"C:\\Path\\To\\toolbag.exe")``.
    """

    #: The plugin's label: the folder its instance records are under.
    LABEL = "marmoset_rpc"
    #: A Toolbag :meth:`connect` launches runs the plugin from this package's
    #: source (Toolbag starts a plugin only when it is picked, or ``-run``).
    LAUNCH_ARGS = ("-run", str(Path(__file__).resolve().parent / "plugin_src" / "marmoset_rpc" / "__main__.py"))

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, token: Optional[str] = None):
        super().__init__(
            host=host,
            port=port,
            app_label="Marmoset Toolbag",
            find_exe=_MarmosetConnectionInternal._find_toolbag,
            token=token,
            label=MarmosetConnection.LABEL,
        )

    @classmethod
    def toolbags(cls) -> List[Dict[str, Any]]:
        """Every running Toolbag serving the plugin: its instance record
        (``pid``, ``port``, ``token``), oldest first."""
        return cls.instances(cls.LABEL)
