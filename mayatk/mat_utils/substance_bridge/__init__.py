# !/usr/bin/python
# coding=utf-8
"""Substance 3D Painter bridge subpackage.

Direct usage::

    from mayatk.mat_utils.substance_bridge import SubstanceBridge
    SubstanceBridge().send(template="import")

The bridge mirrors :mod:`mayatk.mat_utils.marmoset_bridge`:

* :mod:`_substance_engine` -- ``SubstanceEngine``, the DCC-free Painter half
  (template parsing, launch / attach + RPC dispatch, texture staging); vendored
  byte-identical into blendertk.
* :mod:`_substance_bridge` -- :class:`SubstanceBridge`: the engine plus Maya's
  scene I/O (mesh export, selection, manifest, bake source, export record).
* :mod:`connection` -- live process I/O: stdio capture, log tail.
* :mod:`substance_rpc` -- RPC client + installer + Painter-side plugin
  (``plugin_src/substance_rpc``) for a running Painter.
* :mod:`parameters` -- registry of tunable knobs surfaced in the UI.
* :mod:`manifest` -- ``MatManifest`` re-export shim.
* ``templates/`` -- declarative Painter handoffs (``__KEY__`` placeholders).
"""

from pythontk.core_utils.module_resolver import lazy_exports

# The RPC client lives under :mod:`substance_bridge.substance_rpc` for clear
# bridge vs. live-RPC separation; import it from there.
lazy_exports(
    globals(),
    {
        "_substance_bridge": (
            "SubstanceBridge",
            "SEND_TO",
            "ROUND_TRIP",
            "TARGET_AUTO",
            "TARGET_NEW",
            "TARGET_CURRENT",
        ),
        "connection": ("OutputStream", "SubstanceConnection"),
    },
)
