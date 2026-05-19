# !/usr/bin/python
# coding=utf-8
"""Substance Painter JSON-RPC client -- target a running Painter instance.

Lives inside :mod:`mayatk.mat_utils.substance_bridge` as its
"talk to a running Painter" subset. Mirrors the layout of
:mod:`mayatk.mat_utils.marmoset_bridge.marmoset_rpc`:

* The parent :mod:`substance_bridge` -- file-based handoff. Exports
  selection to FBX and launches a templated Painter session. Safe by
  default; never reaches into a live session.
* :mod:`substance_bridge.substance_rpc` (this module) -- targets a
  Painter that is *already running* with a Python plugin that exposes
  an HTTP JSON-RPC endpoint.

.. warning::

   Stock Painter does **not** auto-bind an RPC port. Standing up a
   Painter Python plugin to host the endpoint is a prerequisite; the
   plugin work itself lives outside this module today (BLOCKED on
   plugin scaffolding -- see :class:`PainterRpcClient` docstring).
"""
from .client import PainterRpcClient, DEFAULT_RPC_PORT  # noqa: F401

__all__ = ["PainterRpcClient", "DEFAULT_RPC_PORT"]
