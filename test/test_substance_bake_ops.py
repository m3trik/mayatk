# !/usr/bin/python
# coding=utf-8
"""Tests for the Painter plugin's bake ops (``substance_rpc.ops.bake_ops``).

No Painter required: the real plugin package (``substance_bridge/substance_rpc/
plugin_src/substance_rpc``) is imported off sys.path as Painter's plugin loader
would, and ``substance_painter`` is faked per case. Moved here with the op from
the standalone app's own Painter plugin when the two became one; a real Painter
bake is the standalone app's live pass.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
_PLUGIN_SRC = (
    Path(__file__).resolve().parent.parent / "mayatk" / "mat_utils" / "substance_bridge" / "substance_rpc" / "plugin_src"
)
sys.path.insert(0, str(_PLUGIN_SRC))

import substance_rpc  # noqa: E402  -- the plugin package itself


class TestBakeOpsRegistration(unittest.TestCase):
    """Standalone unit tests — verify the op is registered correctly."""

    def setUp(self) -> None:
        super().setUp()
        from substance_rpc.ops import bake_ops  # noqa: F401

    def test_lighting_to_diffuse_registered(self) -> None:
        registry = substance_rpc.PLUGIN.registry

        fn = registry.get("bake.lighting_to_diffuse")
        self.assertIsNotNone(fn)
        self.assertEqual(fn.__name__, "lighting_to_diffuse")

    def test_lighting_to_diffuse_signature(self) -> None:
        """Every documented knob must be on the signature so agents can discover it."""
        registry = substance_rpc.PLUGIN.registry

        d = registry.describe("bake.lighting_to_diffuse")
        params = [p["name"] for p in d["params"]]
        for expected in (
            "texture_set",
            "bake_resolution",
            "ao_intensity",
            "ao_secondary_rays",
            "ao_max_distance",
            "ao_min_distance",
            "ao_spread_angle",
            "ao_subsample",
            "include_curvature",
            "curvature_intensity",
            "layer_name",
            "blend_mode",
            "skip_existing_bakes",
            "save_project",
        ):
            self.assertIn(expected, params, f"missing parameter: {expected}")

    def test_lighting_to_diffuse_defaults(self) -> None:
        """Default values match the intent documented in the docstring."""
        registry = substance_rpc.PLUGIN.registry

        d = registry.describe("bake.lighting_to_diffuse")
        p = {entry["name"]: entry for entry in d["params"]}
        self.assertEqual(p["bake_resolution"]["default"], "1024")
        self.assertEqual(p["blend_mode"]["default"], "'Multiply'")
        self.assertEqual(p["include_curvature"]["default"], "False")
        self.assertEqual(p["save_project"]["default"], "False")


class TestLightingLayerRerun(unittest.TestCase):
    def test_a_rerun_replaces_its_lighting_layer_and_leaves_the_rest(self):
        import types

        from substance_rpc.ops import bake_ops

        deleted = []
        node = lambda name: types.SimpleNamespace(get_name=lambda: name)  # noqa: E731
        layerstack = types.SimpleNamespace(
            get_root_layer_nodes=lambda stack: [node("iray AO Lighting"), node("Paint"), node("iray AO Lighting (Curvature)")],
            delete_node=deleted.append,
        )
        bake_ops._remove_previous_layers(layerstack, "stack", ("iray AO Lighting", "iray AO Lighting (Curvature)"))
        self.assertEqual([d.get_name() for d in deleted], ["iray AO Lighting", "iray AO Lighting (Curvature)"])


class TestLightingLayerIntensity(unittest.TestCase):
    """Regression: AO / Curvature Intensity must actually reach the layer stack.

    They used to be accepted by ``_add_lighting_layer`` and silently dropped,
    so the sliders had zero effect on output. These tests inject a fake
    ``substance_painter`` API (the real module only exists inside Painter) and
    assert the intensity is applied via the layer-opacity setter.
    """

    def _install_fake_painter(self, opacity_mode="method", with_opacity=True):
        """Install a fake ``substance_painter`` modeling the REAL API surface
        (verified against the installed Painter's layerstack module):
        ``insert_fill(position)`` takes no name kwarg, and opacity / naming /
        blending are METHODS on the layer node (``Node.set_opacity(opacity,
        channel)``, ``Node.set_name``, ``Node.set_blending_mode``) — there are
        no module-level setters. A fake that invents module functions would
        validate an API Painter never shipped.

        opacity_mode:
            "method"     -- Node.set_opacity(opacity, channel)   (current API)
            "layer_wide" -- Node.set_opacity(opacity)            (older builds)
        with_opacity=False omits set_opacity entirely (graceful-degrade path).
        """
        import types

        calls = {"opacity": [], "mesh_map": [], "blend": [], "names": []}

        baking = types.ModuleType("substance_painter.baking")

        class MeshMapUsage:
            AO = "AO"
            Curvature = "Curvature"

        baking.MeshMapUsage = MeshMapUsage

        textureset = types.ModuleType("substance_painter.textureset")

        class ChannelType:
            BaseColor = "BaseColor"

        class Stack:
            @staticmethod
            def from_name(name):
                return ("stack", name)

        textureset.ChannelType = ChannelType
        textureset.Stack = Stack

        layerstack = types.ModuleType("substance_painter.layerstack")

        class InsertPosition:
            @staticmethod
            def from_textureset_stack(stack):
                return ("pos", stack)

        class BlendingMode:
            Multiply = "Multiply"
            Overlay = "Overlay"

        class _Layer:
            def __init__(self):
                self.name = None
                self.uid = "uid::unnamed"

            def set_name(self, name):
                self.name = name
                self.uid = f"uid::{name}"
                calls["names"].append(name)

            def set_blending_mode(self, mode, channel=None):
                calls["blend"].append((self.name, mode, channel))

        if with_opacity:
            if opacity_mode == "method":

                def _set_opacity(self, opacity, channel=None):
                    calls["opacity"].append((self.name, channel, opacity))

            else:  # layer-wide: no channel parameter (TypeError fallback path)

                def _set_opacity(self, opacity):
                    calls["opacity"].append((self.name, None, opacity))

            _Layer.set_opacity = _set_opacity

        def insert_fill(pos):  # real signature: position only, no name kwarg
            return _Layer()

        def set_source_from_mesh_map(layer, channel, usage):
            calls["mesh_map"].append((getattr(layer, "name", layer), channel, usage))

        layerstack.InsertPosition = InsertPosition
        layerstack.BlendingMode = BlendingMode
        layerstack.insert_fill = insert_fill
        layerstack.set_source_from_mesh_map = set_source_from_mesh_map
        layerstack._Layer = _Layer  # exposed for direct-layer tests

        parent = types.ModuleType("substance_painter")
        parent.layerstack = layerstack
        parent.textureset = textureset
        parent.baking = baking

        keys = (
            "substance_painter",
            "substance_painter.layerstack",
            "substance_painter.textureset",
            "substance_painter.baking",
        )
        saved = {k: sys.modules.get(k) for k in keys}
        sys.modules["substance_painter"] = parent
        sys.modules["substance_painter.layerstack"] = layerstack
        sys.modules["substance_painter.textureset"] = textureset
        sys.modules["substance_painter.baking"] = baking

        def restore():
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

        self.addCleanup(restore)
        return calls, layerstack

    def test_add_lighting_layer_applies_both_intensities(self):
        """End-to-end: AO and curvature intensities reach the opacity setter."""
        import types

        from substance_rpc.ops import bake_ops as bake_utils

        calls, _ = self._install_fake_painter()
        ts = types.SimpleNamespace(name="TS")

        uid = bake_utils._add_lighting_layer(
            ts,
            layer_name="AO",
            blend_mode="Multiply",
            intensity=0.5,
            include_curvature=True,
            curvature_intensity=0.3,
        )

        self.assertTrue(uid)
        # Both the AO and the curvature fill layer were sourced from a mesh map.
        self.assertEqual(len(calls["mesh_map"]), 2)
        applied = {name: op for (name, _ch, op) in calls["opacity"]}
        self.assertAlmostEqual(applied["AO"], 0.5)
        self.assertAlmostEqual(applied["AO (Curvature)"], 0.3)

    def test_the_layer_id_is_painters_uid_value_not_its_method(self):
        """Painter's ``uid`` is a method (``ReadOnlyUid.uid``): the op reported
        ``'<bound method ReadOnlyUid.uid of FillLayerNode(89)>'`` live."""
        import types

        from substance_rpc.ops import bake_ops as bake_utils

        _, layerstack = self._install_fake_painter()

        class _PainterLayer(layerstack._Layer):
            def __setattr__(self, key, value):  # Painter's uid is read-only
                if key != "uid":
                    super().__setattr__(key, value)

            def uid(self):
                return 89

        layerstack.insert_fill = lambda pos: _PainterLayer()
        uid = bake_utils._add_lighting_layer(
            types.SimpleNamespace(name="TS"),
            layer_name="AO",
            blend_mode="Multiply",
            intensity=1.0,
            include_curvature=False,
            curvature_intensity=0.3,
        )
        self.assertEqual(uid, 89)

    def test_apply_layer_intensity_partial_applies_clamped(self):
        from substance_rpc.ops import bake_ops as bake_utils

        calls, layerstack = self._install_fake_painter()
        layer = layerstack.insert_fill(None)
        layer.set_name("L")
        bake_utils._apply_layer_intensity(layer, "BaseColor", 0.25)

        self.assertEqual(len(calls["opacity"]), 1)
        name, channel, opacity = calls["opacity"][0]
        self.assertEqual((name, channel), ("L", "BaseColor"))
        self.assertAlmostEqual(opacity, 0.25)

    def test_apply_layer_intensity_neutral_is_noop(self):
        """intensity == 1.0 is Painter's default opacity: no setter call."""
        from substance_rpc.ops import bake_ops as bake_utils

        calls, layerstack = self._install_fake_painter()
        bake_utils._apply_layer_intensity(
            layerstack.insert_fill(None), "BaseColor", 1.0
        )
        self.assertEqual(calls["opacity"], [])

    def test_apply_layer_intensity_above_one_saturates(self):
        """intensity > 1.0 clamps to full opacity (a no-op), never crashes."""
        from substance_rpc.ops import bake_ops as bake_utils

        calls, layerstack = self._install_fake_painter()
        bake_utils._apply_layer_intensity(
            layerstack.insert_fill(None), "BaseColor", 4.0
        )
        self.assertEqual(calls["opacity"], [])

    def test_apply_layer_intensity_layer_wide_setter_fallback(self):
        """An older layer-wide set_opacity(opacity) is reached via the
        TypeError fallback when the channel arg is rejected."""
        from substance_rpc.ops import bake_ops as bake_utils

        calls, layerstack = self._install_fake_painter(opacity_mode="layer_wide")
        layer = layerstack.insert_fill(None)
        layer.set_name("L")
        bake_utils._apply_layer_intensity(layer, "BaseColor", 0.4)

        self.assertEqual(len(calls["opacity"]), 1)
        self.assertEqual(calls["opacity"][0][0], "L")
        self.assertAlmostEqual(calls["opacity"][0][-1], 0.4)

    def test_apply_layer_intensity_missing_setter_is_graceful(self):
        """No set_opacity on this Painter build: warn, don't crash, no-op."""
        from substance_rpc.ops import bake_ops as bake_utils

        calls, layerstack = self._install_fake_painter(with_opacity=False)
        layer = layerstack.insert_fill(None)
        layer.set_name("L")
        bake_utils._apply_layer_intensity(layer, "BaseColor", 0.5)
        self.assertEqual(calls["opacity"], [])


class TestLightingToDiffuseBakes(unittest.TestCase):
    """The bake step against a fake Painter that bakes the way the real one does.

    Painter's ``baking.bake_async`` takes ONE texture set, returns a
    ``StopSource`` (no ``result()``), and reports the end as a
    ``BakingProcessEnded`` event on the main thread some time later -- the
    thread the op itself runs on. Measured 2026-10-19 in a fresh Painter: the
    op passed a list and called ``.result()``, so every bake raised
    ``AttributeError: 'list' object has no attribute 'material_id'``.
    """

    #: Painter's own AO baker properties and their published ranges (read from a
    #: fresh Painter 2026-10-19 -- the op's names were none of these).
    AO_RANGES = {
        "NbSecondary": ("1", "256"),
        "MinDistance": ("9.999999747378752e-06", "1"),
        "MaxDistance": ("9.999999747378752e-06", "1"),
        "Spread": ("0", "180"),
        "Distribution": (None, None),
    }
    AO_KEYS = tuple(AO_RANGES)

    def _painter(self, status="Success", ao_keys=AO_KEYS, enabled=("Normal", "AO", "ID"), maps=(), end_after=30):
        """Install the fake Painter modules; returns the call record."""
        import types

        from qtpy import QtCore, QtWidgets

        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        calls = {"bake": [], "ended": [], "enabled": [], "set": [], "layer_after_end": []}
        state = {"enabled": list(enabled), "maps": set(maps)}

        event = types.ModuleType("substance_painter.event")

        class BakingProcessEnded:
            def __init__(self, status):
                self.status = status

        class _Dispatcher:
            def __init__(self):
                self.callbacks = []

            def connect(self, cls, callback):
                self.callbacks.append((cls, callback))

            def disconnect(self, cls, callback):
                self.callbacks.remove((cls, callback))

        event.BakingProcessEnded = BakingProcessEnded
        event.DISPATCHER = _Dispatcher()

        baking = types.ModuleType("substance_painter.baking")

        class MeshMapUsage:
            AO, Curvature, Normal, ID = "AO", "Curvature", "Normal", "ID"

        class BakingStatus:
            Success, Cancel, Fail = "Success", "Cancel", "Fail"

        ranges = self.AO_RANGES

        class _Prop:
            def __init__(self, key):
                self.key = key

            def properties(self):
                lo, hi = ranges.get(self.key, (None, None))
                return {k: v for k, v in (("min", lo), ("max", hi)) if v is not None}

            def __repr__(self):
                return self.key

        class _Params:
            def __init__(self, ts):
                self.ts = ts

            def common(self):
                return {key: _Prop(key) for key in ("OutputSize", "SubSampling", "DilationWidth")}

            def baker(self, usage):
                return {key: _Prop(key) for key in ao_keys}

            def get_enabled_bakers(self):
                return list(state["enabled"])

            def set_enabled_bakers(self, usages):
                state["enabled"] = list(usages)
                calls["enabled"].append(list(usages))

        class BakingParameters:
            from_texture_set = staticmethod(_Params)

            @staticmethod
            def set(settings):
                calls["set"].append({prop.key: value for prop, value in settings.items()})

        def bake_async(texture_set):
            calls["bake"].append((texture_set, list(state["enabled"])))

            def end():
                calls["ended"].append(texture_set)
                for cls, callback in list(event.DISPATCHER.callbacks):
                    if cls is BakingProcessEnded:
                        callback(BakingProcessEnded(getattr(BakingStatus, status)))

            QtCore.QTimer.singleShot(end_after, end)  # later, on the main loop -- like Painter
            return types.SimpleNamespace(request_stop=lambda: calls.setdefault("stopped", []).append(texture_set))

        baking.MeshMapUsage, baking.BakingStatus = MeshMapUsage, BakingStatus
        baking.BakingParameters, baking.bake_async = BakingParameters, bake_async

        textureset = types.ModuleType("substance_painter.textureset")

        class TextureSet:
            def __init__(self, name):
                self._name = name

            def name(self):
                return self._name

            def get_mesh_map_resource(self, usage):
                return f"res:{self._name}.{usage}" if (self._name, usage) in state["maps"] else None

        textureset.all_texture_sets = lambda: [TextureSet("Body"), TextureSet("Trim")]

        parent = types.ModuleType("substance_painter")
        parent.baking, parent.event, parent.textureset = baking, event, textureset
        modules = {
            "substance_painter": parent,
            "substance_painter.baking": baking,
            "substance_painter.event": event,
            "substance_painter.textureset": textureset,
        }
        saved = {k: sys.modules.get(k) for k in modules}
        sys.modules.update(modules)

        def restore():
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

        self.addCleanup(restore)

        from unittest import mock

        from substance_rpc.ops import bake_ops as bake_utils

        def add_layer(ts, **_):
            calls["layer_after_end"].append(ts in calls["ended"])
            return f"uid::{ts.name()}"

        patcher = mock.patch.object(bake_utils, "_add_lighting_layer", side_effect=add_layer)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls, state, bake_utils

    def test_a_cancel_stops_the_bake_under_way_and_no_other_set_starts(self) -> None:
        """A running script's cancel is served by the bake's own wait (the main
        thread's nested loop), so the bake watches for it: the set under way is
        stopped, the job ends cancelled, and the next set never bakes."""
        calls, _, bake_utils = self._painter(end_after=5000)
        with self.assertRaises(bake_utils.BakeCancelled):
            bake_utils.lighting_to_diffuse(bake_resolution=256, cancelled=lambda: True)
        self.assertEqual(len(calls["bake"]), 1)
        self.assertEqual(len(calls["stopped"]), 1)
        from substance_rpc.ops.script_ops import ScriptCancelled

        self.assertTrue(issubclass(bake_utils.BakeCancelled, ScriptCancelled), "the runner ends the job cancelled")

    def test_each_texture_set_bakes_alone_and_the_layer_waits_for_its_end(self) -> None:
        calls, _, bake_utils = self._painter()
        result = bake_utils.lighting_to_diffuse(bake_resolution=256)
        self.assertEqual(result["errors"], [])
        self.assertEqual([type(ts).__name__ for ts, _ in calls["bake"]], ["TextureSet", "TextureSet"])
        self.assertEqual(calls["layer_after_end"], [True, True])
        self.assertEqual(result["baked_maps"], [{"Body": ["AO"]}, {"Trim": ["AO"]}])
        self.assertEqual(result["layers_created"], [{"Body": "uid::Body"}, {"Trim": "uid::Trim"}])

    def test_only_the_ops_maps_bake_and_the_project_keeps_its_own_choice(self) -> None:
        calls, state, bake_utils = self._painter()
        bake_utils.lighting_to_diffuse(include_curvature=True)
        self.assertEqual([enabled for _, enabled in calls["bake"]], [["AO", "Curvature"]] * 2)
        self.assertEqual(state["enabled"], ["Normal", "AO", "ID"])

    def test_a_failed_bake_is_an_error_and_adds_no_layer(self) -> None:
        calls, state, bake_utils = self._painter(status="Fail")
        result = bake_utils.lighting_to_diffuse()
        self.assertEqual(result["baked_maps"], [])
        self.assertEqual(calls["layer_after_end"], [])
        self.assertEqual(len(result["errors"]), 2)
        self.assertIn("Fail", result["errors"][0])
        self.assertEqual(state["enabled"], ["Normal", "AO", "ID"])

    def test_a_set_that_has_its_maps_is_skipped_when_asked(self) -> None:
        """Painter has no ``baking.has_mesh_map``: the set's own mesh-map lookup
        answers, so ``skip_existing_bakes`` really skips (it re-baked every set)."""
        calls, _, bake_utils = self._painter(maps={("Body", "AO")})
        result = bake_utils.lighting_to_diffuse(skip_existing_bakes=True)
        self.assertEqual([ts.name() for ts, _ in calls["bake"]], ["Trim"])
        self.assertEqual(result["baked_maps"], [{"Body": "skipped"}, {"Trim": ["AO"]}])
        self.assertEqual(result["errors"], [])

    def test_a_skipped_bake_still_composites_the_maps_it_has(self) -> None:
        """The switch skips the BAKE ("only bake when the AO mesh map is
        missing"): a set that already has its maps still gets its layer."""
        _, _, bake_utils = self._painter(maps={("Body", "AO")})
        result = bake_utils.lighting_to_diffuse(skip_existing_bakes=True)
        self.assertEqual(
            [next(iter(d)) for d in result["layers_created"]], ["Body", "Trim"]
        )

    def test_the_ops_settings_reach_painters_own_properties(self) -> None:
        calls, _, bake_utils = self._painter()
        result = bake_utils.lighting_to_diffuse(
            bake_resolution=512,
            ao_secondary_rays=32,
            ao_max_distance=0.5,
            ao_min_distance=0.001,
            ao_spread_angle=150.0,
            ao_subsample=4,
        )
        self.assertEqual(result["errors"], [])
        self.assertEqual(
            calls["set"][0],
            {
                "OutputSize": (9, 9),
                "SubSampling": 2,  # Painter's Antialiasing index: 4x4
                "NbSecondary": 32,
                "MaxDistance": 0.5,
                "MinDistance": 0.001,
                "Spread": 150.0,
            },
        )

    def test_a_value_outside_painters_range_is_held_to_it(self) -> None:
        """The panel offered rays to 1024 and distances to 100 world units;
        Painter's sliders stop at 256 rays and a bounding-box fraction of 1."""
        calls, _, bake_utils = self._painter()
        result = bake_utils.lighting_to_diffuse(
            ao_secondary_rays=1024, ao_max_distance=40.0, ao_min_distance=0.0, ao_subsample=3
        )
        written = calls["set"][0]
        self.assertEqual(
            (written["NbSecondary"], written["MaxDistance"], written["SubSampling"]), (256, 1.0, 2)
        )
        self.assertAlmostEqual(written["MinDistance"], 1e-05)
        self.assertIn("AO.NbSecondary=256", result["parameters"][0]["Body"])

    def test_a_parameter_painter_does_not_have_is_reported_with_its_names(self) -> None:
        _, _, bake_utils = self._painter(ao_keys=("Distance", "Rays"))
        result = bake_utils.lighting_to_diffuse(texture_set=None)
        configure = [e for e in result["errors"] if "/configure" in e]
        self.assertEqual(len(configure), 2)
        self.assertIn("NbSecondary", configure[0])
        self.assertIn("Distance", configure[0])  # what this Painter calls them


if __name__ == "__main__":
    unittest.main()
