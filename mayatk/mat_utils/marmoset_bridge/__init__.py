# !/usr/bin/python
# coding=utf-8
"""Marmoset Toolbag Bridge subpackage -- Maya glue + the bundled Toolbag engine.

Direct usage::

    from mayatk.mat_utils.marmoset_bridge import MarmosetBridge
    MarmosetBridge().send(template="bake")

The DCC-agnostic Toolbag engine (discovery/launch, log handling, template
rendering, the in-Toolbag helpers + ``templates/``, and the live-session
RPC client) is bundled in this subpackage rather than in pythontk: it is
Toolbag SDK glue, not a generic utility, so it lives with its consumer
(mirroring ``substance_bridge``). Layout:

* :mod:`_marmoset_engine` -- ``MarmosetEngine``, the DCC-agnostic core
  (launch + templated automation). Identical in shape to the copy the
  standalone extapps ``marmoset_workflow`` panel keeps -- the panel cannot
  import mayatk, so the engine is vendored into each consumer.
* :mod:`_marmoset_bridge` -- ``MarmosetBridge`` (a :class:`MarmosetEngine`
  that exports the Maya selection to FBX + sidecars, then delegates).
* :mod:`_toolbag_helpers`, :mod:`toolbag_log`, :mod:`template_params`,
  ``templates/`` -- engine support: Toolbag-side helpers, log handling,
  default token values, and the rendered scripts.
* :mod:`marmoset_rpc` -- JSON-RPC client + installer for a *running*
  Toolbag (the in-Toolbag plugin lives under ``marmoset_rpc/plugin_src``).
* :mod:`parameters` -- ``AttributeSpec`` registry of tunable knobs
  surfaced in the Maya panel (Qt/uitk-coupled, so it stays here).
* :mod:`marmoset_bridge_slots` + ``marmoset_bridge.ui`` -- Switchboard UI.

To talk to an already-running Toolbag, import the RPC client directly::

    from mayatk.mat_utils.marmoset_bridge.marmoset_rpc import MarmosetConnection
"""
from mayatk.mat_utils.marmoset_bridge._marmoset_bridge import MarmosetBridge  # noqa: F401

__all__ = ["MarmosetBridge"]
