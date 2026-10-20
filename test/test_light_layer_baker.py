# !/usr/bin/python
# coding=utf-8
"""Tests for mayatk.light_utils.light_layer_baker -- each light of a rig baked alone."""

import json
import math
import os
import types
import unittest

import maya.cmds as cmds
import pythontk as ptk

from base_test import MayaTkTestCase


def _arnold_loadable() -> bool:
    try:
        if not cmds.pluginInfo("mtoa", q=True, loaded=True):
            cmds.loadPlugin("mtoa")
        return bool(cmds.pluginInfo("mtoa", q=True, loaded=True))
    except Exception:
        return False


KEY = {
    "lights": [
        {
            "name": "KEY",
            "type": "spotLight",
            "pos": [0, 5, 0],
            "rot": [-90, 0, 0],
            "attrs": {"intensity": 2.0, "color": [1.0, 0.5, 0.2]},
            "kelvin": 3200,
        }
    ]
}


class _FakeBaker:
    """Stands in for LightmapBaker: notes the scene's lights as it bakes, writes a map."""

    def __init__(self, seen, refuse=False, **settings):
        self.seen, self.refuse, self.settings = seen, refuse, settings

    def bake(self, objects, packing, suffix, output_dir, on_progress):
        lights = {}
        for shape in cmds.ls(lights=True, long=True) or []:
            lights[shape] = cmds.getAttr(f"{shape}.intensity")
        self.seen.append({"settings": self.settings, "lights": lights})
        if self.refuse:
            return types.SimpleNamespace(refused="no light", maps={}, probe=None)
        path = os.path.join(output_dir, "ROOM_Lightmap.exr")
        with open(path, "wb") as f:
            f.write(b"exr")
        probe = os.path.join(output_dir, "scene_Probe.exr")
        with open(probe, "wb") as f:
            f.write(b"exr")
        return types.SimpleNamespace(refused=None, maps={objects[0]: path}, probe=probe)


class TestLightLayerBaker(MayaTkTestCase):
    def setUp(self):
        super().setUp()
        from mayatk.light_utils.light_layer_baker import LightLayerBaker

        self.Baker = LightLayerBaker
        self.temp = ptk.TempArtifacts("mayatk_light_layer_test", policy="scoped")
        self.dir = str(self.temp.dir_path())
        self.floor = cmds.polyPlane(name="layerFloor", w=4, h=4)[0]
        office = cmds.directionalLight(intensity=1.5)
        self.office = cmds.ls(office, long=True)[0]
        if cmds.nodeType(self.office) != "directionalLight":
            self.office = cmds.listRelatives(office, shapes=True, fullPath=True)[0]
        self.seen = []

    def tearDown(self):
        self.temp.cleanup(force=True)
        super().tearDown()

    def baker(self, refuse=False):
        return self.Baker(baker=lambda **s: _FakeBaker(self.seen, refuse=refuse, **s))

    # ------------------------------------------------------------------ payloads
    def test_a_layer_light_is_white_and_gained_and_the_rest_is_off(self):
        canon = self.Baker().layer_payload(KEY)
        attrs = canon["lights"][0]["attrs"]
        self.assertEqual(attrs["color"], [1.0, 1.0, 1.0])
        self.assertEqual(attrs["aiUseColorTemperature"], 0)
        self.assertNotIn("aiColorTemperature", attrs)
        self.assertAlmostEqual(attrs["intensity"], 2.0 * math.pi)
        self.assertEqual(canon["existing"], {self.office: {"intensity": 0.0}})
        self.assertEqual(canon["group"], self.Baker.GROUP)

    def test_a_whole_rig_bakes_as_it_is(self):
        """Not a layer: its colours and strengths kept, no gain -- what applying
        the rig bakes, to hold the sum of its layers to (portability)."""
        canon = self.Baker().layer_payload(KEY, white=False)
        attrs = canon["lights"][0]["attrs"]
        self.assertEqual(attrs["color"], [1.0, 0.5, 0.2])
        self.assertAlmostEqual(attrs["intensity"], 2.0)
        self.assertEqual(canon["existing"], {self.office: {"intensity": 0.0}})

    def test_shared_overrides_apply_under_the_layers_own(self):
        base = {"materials": {"panels": {"emission": 0.0, "use": 1}}}
        layer = dict(KEY, materials={"panels": {"emission": 3.0}})
        canon = self.Baker().layer_payload(layer, base)
        self.assertEqual(canon["materials"], {"panels": {"emission": 3.0, "use": 1}})

    def test_a_focal_length_gives_its_field_of_view(self):
        focal = self.Baker.focal_length(48.32, 24.0)
        self.assertAlmostEqual(
            math.degrees(2 * math.atan(12.0 / focal)), 48.32, places=6
        )

    def test_a_beauty_renders_from_a_temporary_camera_and_hands_back(self):
        if not _arnold_loadable():
            self.skipTest("mtoa unavailable")
        import mtoa.core

        mtoa.core.createOptions()
        cameras = set(cmds.ls(type="camera"))
        aa = cmds.getAttr("defaultArnoldRenderOptions.AASamples")
        path = os.path.join(self.dir, "beauty.exr")
        out = self.Baker().beauty(
            None,
            {"pos": [0, 6, 6], "target": [0, 0, 0], "up": [0, 1, 0], "vfov": 40},
            path,
            size=(32, 18),
            aa=1,
        )
        self.assertEqual(out, path)
        written = [f for f in os.listdir(self.dir) if f.startswith("beauty")]
        self.assertTrue(written, os.listdir(self.dir))
        self.assertEqual(set(cmds.ls(type="camera")), cameras)
        self.assertEqual(cmds.getAttr("defaultArnoldRenderOptions.AASamples"), aa)

    # ------------------------------------------------------------------ the worker's scene
    def test_a_worker_records_the_save_it_baked_and_reopens_a_newer_one(self):
        """Layers baked before a save are told from layers after it, and a
        worker never bakes an older save than the one on disk."""
        from unittest import mock

        scene = os.path.join(self.dir, "room.ma")
        cmds.file(rename=scene)
        cmds.file(save=True, type="mayaAscii", force=True)
        self.addCleanup(setattr, self.Baker, "_opened", None)
        with mock.patch.object(ptk.Workspace, "for_scene", return_value=None):
            self.Baker._open_scene(scene)  # what the worker does at start
            opened = ptk.LightLayerScene.of(scene)
            out = os.path.join(self.dir, "layers")
            self.baker().bake({"key": KEY}, out, objects=["layerFloor"])
            with open(os.path.join(out, "key", self.Baker.LAYER_FILE)) as f:
                info = json.load(f)
            self.assertEqual(info["scene"], opened)
            self.assertTrue(ptk.LightLayerInfo.validate(info).ok)
            self.assertFalse(self.Baker._reopen_if_saved())  # nothing saved since
            later = opened["mtime"] + 30
            os.utime(scene, (later, later))  # the user saved it
            self.assertTrue(self.Baker._reopen_if_saved())
            self.assertEqual(self.Baker._opened[1], ptk.LightLayerScene.of(scene))
            self.assertFalse(self.Baker._reopen_if_saved())

    def test_a_bake_in_a_users_scene_records_no_save(self):
        """What is in memory need not be any save of the file."""
        out = os.path.join(self.dir, "layers")
        self.baker().bake({"key": KEY}, out, objects=[self.floor])
        with open(os.path.join(out, "key", self.Baker.LAYER_FILE)) as f:
            self.assertNotIn("scene", json.load(f))

    # ------------------------------------------------------------------ baking
    def test_a_layer_bakes_alone_and_hands_the_lights_back(self):
        out = os.path.join(self.dir, "layers")
        results = self.baker().bake({"key": KEY}, out, objects=[self.floor])
        self.assertEqual(
            results["key"]["files"], ["ROOM_Lightmap.exr", "scene_Probe.exr"]
        )
        during = self.seen[0]["lights"]
        self.assertEqual(during[self.office], 0.0)  # the scene's own light off
        layer = [s for s in during if s.startswith(f"|{self.Baker.GROUP}|")]
        self.assertEqual(len(layer), 1)
        self.assertAlmostEqual(during[layer[0]], 2.0 * math.pi, places=4)
        self.assertTrue(self.seen[0]["settings"]["refuse_unlit"])
        # handed back as found
        self.assertAlmostEqual(cmds.getAttr(f"{self.office}.intensity"), 1.5)
        self.assertFalse(cmds.objExists(self.Baker.GROUP))
        # nor a record of the swap: the scene's light rigs as they were (none)
        from mayatk.node_utils.data_nodes import DataNodes

        self.assertIsNone(ptk.SceneRecords.LIGHT_RIGS.load(DataNodes, None))
        with open(os.path.join(out, "key", self.Baker.LAYER_FILE)) as f:
            info = json.load(f)
        checked = ptk.LightLayerInfo.validate(info)  # the contract capture_bake reads
        self.assertEqual((checked.errors, checked.warnings), ([], []))
        self.assertEqual((info["kind"], info["gain"]), ("light", math.pi))
        self.assertEqual(info["spec"], KEY)
        # Its lights, as the lightmap record carries them: a capture lights their
        # highlights into the layer (the scene's own light at 0 is no part of it).
        (light,) = info["lights"]
        self.assertEqual(light["type"], "spot")
        self.assertAlmostEqual(light["position"][1], 5.0, places=4)
        self.assertAlmostEqual(light["intensity"], 2.0 * math.pi, places=4)

    def test_a_set_bakes_its_members(self):
        box = cmds.polyCube(name="layerBox")[0]
        members = cmds.sets([self.floor, box], name="LAYER_BAKE_SET")
        got = []

        class Recorder(_FakeBaker):
            def bake(inner, objects, **kw):
                got.extend(objects)
                return super().bake(objects, **kw)

        baker = self.Baker(baker=lambda **s: Recorder(self.seen, **s))
        baker.bake({"key": KEY}, os.path.join(self.dir, "layers"), objects=[members])
        self.assertEqual(
            sorted(cmds.ls(got, long=True)),
            sorted(cmds.ls([self.floor, box], long=True)),
        )

    def test_an_emission_layer_bakes_with_no_scene_light(self):
        out = os.path.join(self.dir, "layers")
        self.baker().bake({"glow": {"materials": {}}}, out, objects=[self.floor])
        self.assertFalse(self.seen[0]["settings"]["refuse_unlit"])
        with open(os.path.join(out, "glow", self.Baker.LAYER_FILE)) as f:
            self.assertEqual(json.load(f)["kind"], "emission")

    def _glow(self, payload):
        out = os.path.join(self.dir, "layers")
        self.baker().bake({"glow": payload}, out, objects=[self.floor])
        with open(os.path.join(out, "glow", self.Baker.LAYER_FILE)) as f:
            return json.load(f)

    def test_an_emission_layer_names_the_weight_a_mix_scales(self):
        """LookMatch.to_rig builds an emission layer from its target: without
        one the layer could be mixed but never shipped."""
        mat = cmds.shadingNode("standardSurface", asShader=True, name="GLOW_MAT")
        info = self._glow({"materials": {mat: {"emission": 3.0, "base": 0.5}}})
        self.assertEqual(info["target"], {"node": mat, "attr": "emission", "scale": 3.0})

    def test_an_emission_layer_set_on_a_bridge_targets_its_material(self):
        """What the export reads: a mix of the bridge's number would bake right
        and ship wrong."""
        if not _arnold_loadable():
            self.skipTest("mtoa unavailable")
        from mayatk.mat_utils.arnold_bridge import ArnoldBridge

        path = os.path.join(self.dir, "glow_Emissive.png")
        cmds.polyPlane(name="glowPanel")
        from PIL import Image

        Image.new("RGB", (4, 4), (255, 240, 200)).save(path)
        mat = cmds.shadingNode("standardSurface", asShader=True, name="GLOW_MAT")
        tex = cmds.shadingNode("file", asTexture=True)
        cmds.setAttr(tex + ".fileTextureName", path, type="string")
        cmds.connectAttr(tex + ".outColor", mat + ".emissionColor")
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        cmds.connectAttr(mat + ".outColor", sg + ".surfaceShader")
        cmds.sets("glowPanel", edit=True, forceElement=sg)
        (bridge,) = ArnoldBridge().add(materials=mat)
        info = self._glow({"materials": {bridge: {"emission": math.pi}}})
        self.assertEqual(info["target"]["node"], mat)
        self.assertEqual(info["target"]["attr"], "emission")
        self.assertAlmostEqual(info["target"]["scale"], math.pi)

    def test_an_emission_layer_with_two_weights_names_none(self):
        a = cmds.shadingNode("standardSurface", asShader=True, name="GLOW_A")
        b = cmds.shadingNode("standardSurface", asShader=True, name="GLOW_B")
        info = self._glow({"materials": {a: {"emission": 1.0}, b: {"emission": 2.0}}})
        self.assertNotIn("target", info)

    def test_a_refused_layer_writes_nothing_and_still_hands_back(self):
        out = os.path.join(self.dir, "layers")
        results = self.baker(refuse=True).bake({"key": KEY}, out, objects=[self.floor])
        self.assertEqual(results["key"], {"refused": "no light"})
        self.assertFalse(os.path.exists(os.path.join(out, "key")))
        self.assertAlmostEqual(cmds.getAttr(f"{self.office}.intensity"), 1.5)
        self.assertFalse(cmds.objExists(self.Baker.GROUP))

    @unittest.skipUnless(_arnold_loadable(), "mtoa unavailable")
    def test_a_real_layer_is_lit_by_its_own_light_alone(self):
        os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
        import cv2

        out = os.path.join(self.dir, "real")
        layer = {
            "lights": [
                {
                    "name": "TOP",
                    "type": "pointLight",
                    "pos": [0, 2, 0],
                    "attrs": {"intensity": 1.0, "aiExposure": 2.0},
                }
            ]
        }
        results = self.Baker(resolution=32, samples=2, denoise=False).bake(
            {"top": layer, "dark": {"lights": []}}, out, objects=[self.floor]
        )
        maps = [f for f in results["top"]["files"] if f.endswith("_Lightmap.exr")]
        self.assertEqual(len(maps), 1, results)
        lit = ptk.ImgUtils.cv2_read(
            os.path.join(out, "top", maps[0]), cv2.IMREAD_UNCHANGED
        )
        self.assertGreater(float(lit[..., :3].mean()), 0.01)
        # no light at all of its own (an emission layer with no emission): dark,
        # though the scene's own light stands at 1.5 outside the bake
        dark_map = [f for f in results["dark"]["files"] if f.endswith("_Lightmap.exr")][
            0
        ]
        dark = ptk.ImgUtils.cv2_read(
            os.path.join(out, "dark", dark_map), cv2.IMREAD_UNCHANGED
        )
        self.assertLess(float(dark[..., :3].mean()), 1e-3)
        self.assertAlmostEqual(cmds.getAttr(f"{self.office}.intensity"), 1.5)


if __name__ == "__main__":
    unittest.main(exit=False)
