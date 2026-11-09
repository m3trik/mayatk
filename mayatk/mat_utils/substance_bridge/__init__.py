# !/usr/bin/python
# coding=utf-8
"""Substance 3D Painter bridge subpackage.

Direct usage::

    from mayatk.mat_utils.substance_bridge import SubstanceBridge
    SubstanceBridge().send(template="new_project")

The bridge mirrors :mod:`mayatk.mat_utils.marmoset_bridge`:

* :mod:`_substance_engine` -- ``SubstanceEngine``, the DCC-free Painter half
  (script headers, finding the Painter a script runs in, texture staging);
  vendored byte-identical into the Blender library and the standalone app.
* :mod:`_substance_bridge` -- :class:`SubstanceBridge`: the engine plus Maya's
  scene I/O (mesh export, selection, manifest, bake source, export record).
* :mod:`connection` -- one Painter, launched or attached by its instance record.
* :mod:`substance_rpc` -- RPC client + installer + Painter-side plugin
  (``plugin_src/substance_rpc``): the script runner in Painter.
* :mod:`parameters` -- registry of tunable knobs
  (``SubstanceBridge.params_registry()``): the rows of tentacle's shared
  Substance Bridge panel (``slots/tools/substance_bridge.py``).
* :mod:`manifest` -- ``MatManifest`` re-export shim.
* ``templates/`` -- the built-in scripts, run in Painter (``__KEY__`` rows).
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
            "PAINTER_AUTO",
            "PAINTER_NEW",
        ),
        "connection": ("OutputStream", "SubstanceConnection"),
    },
)
