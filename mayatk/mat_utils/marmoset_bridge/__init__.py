# !/usr/bin/python
# coding=utf-8
"""Marmoset Toolbag Bridge subpackage.

Direct usage::

    from mayatk.mat_utils.marmoset_bridge import MarmosetBridge
    MarmosetBridge().send(template="bake")

The bridge mirrors :mod:`mayatk.uv_utils.rizom_bridge`:

* :mod:`_marmoset_bridge` -- export/launch logic (``MarmosetBridge``).
* :mod:`marmoset_rpc` -- JSON-RPC client for a running Toolbag (live
  session). Import explicitly when you want it -- not re-exported here
  to keep the file-handoff vs. live-RPC concerns separate.
* :mod:`parameters` -- registry of tunable knobs surfaced in the UI.
* :mod:`marmoset_bridge_slots` + ``marmoset_bridge.ui`` -- Switchboard UI.
* ``templates/`` -- Toolbag Python scripts with ``__KEY__`` substitution.
"""
from mayatk.mat_utils.marmoset_bridge._marmoset_bridge import MarmosetBridge  # noqa: F401

__all__ = ["MarmosetBridge"]
