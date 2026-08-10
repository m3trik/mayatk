# !/usr/bin/python
# coding=utf-8
"""Substance 3D Painter bridge subpackage.

Direct usage::

    from mayatk.mat_utils.substance_bridge import SubstanceBridge
    SubstanceBridge().send(template="import")

The bridge mirrors :mod:`mayatk.mat_utils.marmoset_bridge`:

* :mod:`_substance_bridge` -- export/launch logic (:class:`SubstanceBridge`).
* :mod:`connection` -- live process I/O: stdio capture, log tail.
* :mod:`substance_rpc` -- RPC client + installer + Painter-side plugin
  (``plugin_src/substance_rpc``) for a running Painter.
* :mod:`parameters` -- registry of tunable knobs surfaced in the UI.
* :mod:`manifest` -- ``MatManifest`` re-export shim.
* ``templates/`` -- declarative Painter handoffs (``__KEY__`` placeholders).
"""

from mayatk.mat_utils.substance_bridge._substance_bridge import (
    SubstanceBridge,
    SEND_TO,
    ROUND_TRIP,
    ROUNDTRIP,
    TARGET_AUTO,
    TARGET_NEW,
    TARGET_CURRENT,
)
from mayatk.mat_utils.substance_bridge.connection import (
    OutputStream,
    SubstanceConnection,
)

# RPC client lives under :mod:`substance_bridge.substance_rpc` for clear
# bridge vs. live-RPC separation. Import it from there explicitly:
#     from mayatk.mat_utils.substance_bridge.substance_rpc import (
#         PainterRpcClient,
#     )

__all__ = [
    "SubstanceBridge",
    "SEND_TO",
    "ROUND_TRIP",
    "ROUNDTRIP",  # deprecated alias for ROUND_TRIP
    "TARGET_AUTO",
    "TARGET_NEW",
    "TARGET_CURRENT",
    "OutputStream",
    "SubstanceConnection",
]
