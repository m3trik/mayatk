# !/usr/bin/python
# coding=utf-8
"""Substance Painter RPC -- client, installer, and Painter-side plugin.

Lives inside :mod:`mayatk.mat_utils.substance_bridge` as its
"talk to a running Painter" subset. Mirrors the layout of
:mod:`mayatk.mat_utils.marmoset_bridge.marmoset_rpc`:

* The parent :mod:`substance_bridge` -- file-based handoff. Exports
  selection to FBX and launches a templated Painter session. Safe by
  default; never reaches into a live session uninvited.
* :mod:`substance_bridge.substance_rpc` (this module) -- targets a
  Painter that is *already running* with the ``substance_rpc`` Python
  plugin loaded (``plugin_src/substance_rpc``, installed into Painter's
  user plugin folder by :class:`Installer`; the bridge installs it
  automatically on send).

Stock Painter binds no RPC port of its own (``--enable-remote-scripting``
is a no-op; verified 2026-05-18) -- the plugin is what makes the
``reimport`` / ``render`` / ``bake_lighting`` templates dispatchable.
"""
from .client import PainterRpcClient, DEFAULT_RPC_PORT  # noqa: F401
from .installer import Installer  # noqa: F401

__all__ = ["PainterRpcClient", "DEFAULT_RPC_PORT", "Installer"]
