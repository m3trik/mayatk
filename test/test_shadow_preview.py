# !/usr/bin/python
# coding=utf-8
"""``ShadowPreview`` (``rig_utils/shadow_preview.py``), the device-free half.

A ``GLSLShader`` / ``dx11Shader`` only compiles on a Viewport 2.0 device, and
mayapy has none, so what runs here is everything AROUND the effect: the
device classifier against real ``ogs`` report shapes, the assembled effect
text for both languages (the shared body inside Maya's prologue, the texel
hook declared before it), the headless refusal, and the one rule the whole
design rests on -- a plane wearing a preview still reports its real
material, so the export record never changes.

The preview itself is simulated: a plain ``surfaceShader`` swapped in exactly
the way ``attach`` swaps a compiled effect (membership only, the real
assignment snapshotted). The compiled effect, the live uniforms and the
rendered pixels are ``shadow_preview_device_check.py``'s, which launches a
fresh GUI Maya per device and cannot run in this harness.
"""

import json
import os
import struct
import unittest
import zlib

import maya.cmds as cmds
import numpy as np

try:
    from mayatk.rig_utils.shadow_preview import ShadowPreview
    from mayatk.rig_utils.shadow_rig import ShadowRig
except ImportError:
    import sys

    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from mayatk.rig_utils.shadow_preview import ShadowPreview
    from mayatk.rig_utils.shadow_rig import ShadowRig

import pythontk as ptk
from mayatk.env_utils.fbx_utils import FbxUtils
from mayatk.mat_utils._mat_utils import MatUtils
from base_test import MayaTkTestCase


class TestDeviceClassifier(unittest.TestCase):
    """``classify_device`` over the report shapes ``cmds.ogs`` produces."""

    def test_directx_11(self):
        info = ["Adapter : NVIDIA GeForce RTX 3070 Ti", "API : DirectX 11.0"]
        self.assertEqual(ShadowPreview.classify_device(info), "dx11")

    def test_opengl_core_profile_is_pixel_shader_5(self):
        info = ["API : OpenGL V.4.6", "Shader versions : Vertex 5, Pixel 5"]
        self.assertEqual(ShadowPreview.classify_device(info), "glcore")

    def test_legacy_opengl_is_pixel_shader_4(self):
        """The device the effect fails on SILENTLY (techniques == []), so it
        must be told apart from Core Profile up front."""
        info = ["API : OpenGL V.4.6", "Shader versions : Vertex 4, Pixel 4"]
        self.assertEqual(ShadowPreview.classify_device(info), "gl")

    def test_nothing_is_none(self):
        self.assertIsNone(ShadowPreview.classify_device([]))
        self.assertIsNone(ShadowPreview.classify_device(None))
        self.assertIsNone(ShadowPreview.classify_device(["Adapter : software"]))

    def test_each_device_names_its_language_or_its_refusal(self):
        self.assertEqual(ShadowPreview.LANGUAGE_BY_DEVICE["dx11"], "hlsl")
        self.assertEqual(ShadowPreview.LANGUAGE_BY_DEVICE["glcore"], "glsl")
        self.assertEqual(ShadowPreview.refusal("dx11"), "")
        self.assertIn("legacy OpenGL", ShadowPreview.refusal("gl"))
        self.assertIn("Viewport 2.0", ShadowPreview.refusal(None))


class TestEffectText(unittest.TestCase):
    """The assembled effects: Maya's prologue around the shared body."""

    def test_both_languages_carry_the_shared_body_after_the_hook(self):
        for language in ("glsl", "hlsl"):
            text = ShadowPreview.effect_text(language)
            body = ptk.ShadowHorizon.shader_source(language)
            self.assertIn(body, text, language)
            # SH_Fetch is the host's and the body calls it: declared first.
            self.assertLess(
                text.index("SH_Fetch(int col"), text.index("float ShAlpha("), language
            )
            self.assertIn("technique", text)
            self.assertIn('Transparency = "Transparent"', text)

    def test_the_ogsfx_speaks_mayas_dialect(self):
        """The two traps that each cost a compile cycle: an ``attribute``
        block takes NO trailing semicolon, and only known semantics."""
        text = ShadowPreview.effect_text("glsl")
        self.assertTrue(text.startswith("#version 410"))
        for block in (
            "attribute vs_input",
            "attribute vs_to_ps",
            "attribute ps_output",
        ):
            start = text.index(block)
            close = text.index("}", start)
            self.assertNotEqual(text[close : close + 2], "};", block)
        self.assertNotIn(": Position", text)

    def test_the_fx_is_hlsl_spelled(self):
        text = ShadowPreview.effect_text("hlsl")
        self.assertIn("#define SH_HLSL 1", text)
        self.assertIn("technique11 Main", text)
        self.assertIn("gHorizonTex.Load(", text)

    def test_every_uniform_the_binder_writes_is_declared(self):
        """``_bind`` sets and connects these by name; a rename on one side
        would surface as a connectAttr error in a live Maya only."""
        names = (
            "gOrigin",
            "gAxisA",
            "gAxisB",
            "gAxisUp",
            "gSource",
            "gSourceW",
            "gSourceDiameter",
            "gSourceAngle",
            "gGround",
            "gBins",
            "gCols",
            "gLayers",
            "gTileW",
            "gTileH",
            "gRMin",
            "gRMax",
            "gMaxStretch",
            "gRectSX",
            "gRectSY",
            "gRectOX",
            "gRectOY",
            "gOpacity",
            "gIntensity",
            "gHorizonTex",
        )
        for language in ("glsl", "hlsl"):
            text = ShadowPreview.effect_text(language)
            for name in names:
                self.assertIn(f" {name}", text, f"{language}: {name}")


class TestPreviewLifecycle(MayaTkTestCase):
    """The plane-side contract, with a stand-in shader for the effect."""

    def setUp(self):
        super().setUp()
        self.cube = cmds.polyCube(name="Box", width=2, height=2, depth=2)[0]
        self.rig = ShadowRig.create(
            [self.cube],
            light_pos=(5, 10, 5),
            texture_res=64,
            rig_type="horizon",
            horizon_bins=8,
            horizon_size=(32, 16),
        )
        self.plane = self.rig.shadow_plane
        self._paths = [self.rig.texture_path, self.rig.horizon_path]

    def tearDown(self):
        FbxUtils.unregister_export_preparer("shadow")
        for path in self._paths:
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
        super().tearDown()

    def _simulate_attach(self):
        """What ``attach`` does once the effect has compiled, with a
        ``surfaceShader`` standing in for the ``GLSLShader``."""
        snapshot = MatUtils.get_shading_assignments(self.plane)
        fx = cmds.shadingNode(
            "surfaceShader", asShader=True, name=f"Box_shadow{ShadowPreview.INFIX}fx"
        )
        sg = MatUtils.create_shading_group(
            fx, name=f"Box_shadow{ShadowPreview.INFIX}SG"
        )
        ShadowPreview._ensure_string_attr(
            self.plane, ShadowPreview.RESTORE_ATTR, json.dumps(snapshot)
        )
        cmds.addAttr(self.plane, ln=ShadowPreview.SHADER_ATTR, at="message")
        cmds.connectAttr(f"{fx}.message", f"{self.plane}.{ShadowPreview.SHADER_ATTR}")
        cmds.sets(self.plane, edit=True, forceElement=sg)
        return fx, sg, snapshot

    def test_headless_has_no_device_and_attach_refuses_with_the_reason(self):
        self.assertIsNone(ShadowPreview.device())
        language, refusal = ShadowPreview.language()
        self.assertIsNone(language)
        self.assertIn("Viewport 2.0", refusal)
        with self.assertRaises(ValueError) as caught:
            ShadowPreview.attach(self.plane)
        self.assertIn("Viewport 2.0", str(caught.exception))
        self.assertFalse(ShadowPreview.is_attached(self.plane))

    def test_a_projected_plane_is_refused_before_the_device_is_asked(self):
        projected = ShadowRig.create(
            [self.cube], light_pos=(5, 10, 5), texture_res=64, source_name="other"
        )
        self._paths.append(projected.texture_path)
        with self.assertRaises(ValueError) as caught:
            ShadowPreview.attach(projected.shadow_plane, language="glsl")
        self.assertIn("not a horizon", str(caught.exception))

    def test_the_effect_file_lands_beside_the_map_and_is_rewritten_only_on_change(self):
        folder = os.path.dirname(self.rig.horizon_path)
        path = ShadowPreview._write_effect(folder, "glsl")
        self._paths.append(path)
        self.assertEqual(os.path.dirname(path), folder.replace("\\", "/"))
        self.assertTrue(path.endswith("shadow_horizon_preview.ogsfx"))
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), ShadowPreview.effect_text("glsl"))
        mtime = os.path.getmtime(path)
        self.assertEqual(ShadowPreview._write_effect(folder, "glsl"), path)
        self.assertEqual(os.path.getmtime(path), mtime, "rewritten unchanged")

    def test_the_bound_texture_is_the_maps_16_bit_promotion_beside_it(self):
        """VP2's OpenGL path sRGB-decodes an 8-bit texture a GLSLShader
        samples (measured by the device check: R came back as the sRGB decode
        of its byte, A untouched, and the floating layer vanished), so the
        preview binds a 16-bit promotion of the map instead -- every sample
        the map's byte times 257 -- written once, refreshed after a re-bake."""
        png = self.rig.horizon_path
        path = ShadowPreview._write_texture(png)
        self._paths.append(path)
        self.assertEqual(path, os.path.splitext(png)[0] + ShadowPreview.TEXTURE_SUFFIX)
        with open(path, "rb") as fh:
            data = fh.read()
        width, height, depth, colour = struct.unpack(">IIBB", data[16:26])
        self.assertEqual((depth, colour), (16, 6), "16-bit RGBA")
        idat, pos = b"", 8
        while pos < len(data):
            length, kind = struct.unpack(">I4s", data[pos : pos + 8])
            if kind == b"IDAT":
                idat += data[pos + 8 : pos + 8 + length]
            pos += 12 + length
        rows = np.frombuffer(zlib.decompress(idat), np.uint8)
        rows = rows.reshape(height, 1 + width * 8)
        promoted = rows[:, 1:].copy().view(">u2").reshape(height, width, 4)
        original = np.asarray(
            ptk.ImgUtils.load_image(png).convert("RGBA"), dtype=np.uint16
        )
        np.testing.assert_array_equal(promoted.astype(np.uint16), original * 257)

        mtime = os.path.getmtime(path)
        self.assertEqual(ShadowPreview._write_texture(png), path)
        self.assertEqual(os.path.getmtime(path), mtime, "rewritten while current")
        os.utime(path, (mtime - 10, mtime - 10))  # now older than the map
        ShadowPreview._write_texture(png)
        self.assertGreater(
            os.path.getmtime(path), mtime - 10, "refreshed after a re-bake"
        )

    def test_the_record_and_the_accessors_see_through_a_preview(self):
        """THE guard: a plane wearing a preview reports its real material.

        Without it ``export_record`` published ``"texture": ""`` -- the
        silhouette's file node is found by walking the plane's shading
        groups, which the preview's membership swap replaces -- and the R6
        fallback silently vanished from ``shadow_metadata``.
        """
        before = ShadowRig.export_record(self.plane)
        node_before = ShadowRig._plane_texture_node(self.plane)
        shading_before = ShadowRig._plane_shading(self.plane)
        self.assertTrue(before["texture"])
        self.assertIsNotNone(node_before)

        fx, sg, snapshot = self._simulate_attach()
        self.assertTrue(ShadowPreview.is_attached(self.plane))
        self.assertEqual(ShadowPreview.shader_node(self.plane), fx)
        self.assertEqual(ShadowPreview.restore_snapshot(self.plane), snapshot)
        self.assertEqual(ShadowPreview.attached_planes(), [self.plane])
        # The LIVE membership is the preview's...
        shape = cmds.listRelatives(self.plane, shapes=True, fullPath=True)[0]
        self.assertEqual(cmds.listConnections(shape, type="shadingEngine"), [sg])
        # ...and every accessor still answers from the real network.
        self.assertEqual(ShadowRig._plane_shading_groups(self.plane), list(snapshot))
        self.assertEqual(ShadowRig._plane_texture_node(self.plane), node_before)
        self.assertEqual(ShadowRig._plane_shading(self.plane), shading_before)
        self.assertEqual(ShadowRig.export_record(self.plane), before)

        self.assertTrue(ShadowPreview.detach(self.plane))
        self.assertFalse(ShadowPreview.is_attached(self.plane))
        self.assertEqual(
            sorted(cmds.listConnections(shape, type="shadingEngine")), sorted(snapshot)
        )
        self.assertFalse(cmds.objExists(fx))
        self.assertFalse(cmds.objExists(sg))
        for attr in (ShadowPreview.SHADER_ATTR, ShadowPreview.RESTORE_ATTR):
            self.assertFalse(cmds.attributeQuery(attr, node=self.plane, exists=True))
        self.assertEqual(ShadowRig.export_record(self.plane), before)
        self.assertFalse(ShadowPreview.detach(self.plane), "nothing left to detach")

    def test_the_export_preparer_detaches_and_republishes_under_the_producers_name(
        self,
    ):
        """Registered as ``"shadow"``: it REPLACES the known producer and so
        must republish itself. An unknown name would sort after every known
        producer and detach only once the record was already out."""
        from mayatk.node_utils.data_nodes import DataNodes

        self._simulate_attach()
        ShadowPreview._register_export_preparer()
        self.assertIn("shadow", FbxUtils._export_preparers)
        FbxUtils.run_export_preparers(only=["shadow"])
        self.assertFalse(ShadowPreview.is_attached(self.plane))
        payload = json.loads(
            cmds.getAttr(f"{DataNodes.EXPORT}.{ShadowRig.SHADOW_METADATA}")
        )
        (record,) = [p for p in payload["planes"] if p["name"] == "Box_shadow"]
        self.assertTrue(record["texture"], "the silhouette must be in the record")
        self.assertEqual(record["type"], "horizon")

    def test_toggle_reports_per_plane_and_never_stops_at_a_failure(self):
        done, failed = ShadowPreview.toggle([self.plane, "no_such_plane"], on=False)
        self.assertEqual(done, [self.plane])
        self.assertEqual(len(failed), 1)
        self.assertIn("no_such_plane", failed[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
