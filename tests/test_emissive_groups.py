# !/usr/bin/python
# coding=utf-8
"""mayatk's Emissive Groups (``mat_utils/emissive_groups.py``): pythontk's model
over the Maya half, in a fresh mayapy. A group is materials: its faces wear a
copy of their shader made for the group, sharing the base's maps, its emission
weight keyed by the group's weight. The model alone is pythontk's
``test_emissive_groups.py``.
"""

import json
import os
import unittest

import pythontk as ptk

import maya.cmds as cmds
import maya.mel as mel

from base_test import MayaTkTestCase
from mayatk.mat_utils._mat_utils import MatUtils
from mayatk.mat_utils.emissive_groups import EmissiveGroups
from mayatk.node_utils.data_nodes import DataNodes


class _GroupsCase(MayaTkTestCase):
    """A hull (Standard Surface, its emission colour mapped, weight 2) and a
    panel (Standard Surface, a uniform blue emission, weight 1)."""

    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)  # drops the data nodes with the scene
        cmds.currentUnit(time="film")
        self.hull = cmds.polyCube(name="hull", subdivisionsX=2)[0]
        self.panel = cmds.polyCube(name="panel")[0]
        cmds.move(3, 0, 0, self.panel)
        self.hull_mat = cmds.shadingNode("standardSurface", asShader=True, name="hullMat")
        cmds.setAttr(f"{self.hull_mat}.emission", 2.0)
        self.tex = cmds.shadingNode("file", asTexture=True, name="hullGlow")
        cmds.connectAttr(f"{self.tex}.outColor", f"{self.hull_mat}.emissionColor")
        self.panel_mat = cmds.shadingNode("standardSurface", asShader=True, name="panelMat")
        cmds.setAttr(f"{self.panel_mat}.emission", 1.0)
        cmds.setAttr(f"{self.panel_mat}.emissionColor", 0.2, 0.6, 1.0, type="double3")
        MatUtils.assign_mat([self.hull], self.hull_mat)
        MatUtils.assign_mat([self.panel], self.panel_mat)

    def wears(self, obj, index):
        """The shader face *index* of *obj* wears (whole-object membership too)."""
        long = cmds.ls(obj, long=True)[0]
        found = EmissiveGroups.HOST.face_materials({long: [index]})
        return next(iter(found), None)


class TestGroupsAreMaterials(_GroupsCase):
    def test_a_group_gives_its_faces_a_copy_sharing_the_maps(self):
        copies = EmissiveGroups.add_group(
            "signal", [f"{self.hull}.f[0:1]", f"{self.panel}.f[0]"], weight=0.5
        )
        self.assertEqual(copies, ["hullMat_signal", "panelMat_signal"])
        self.assertEqual(self.wears(self.hull, 0), "hullMat_signal")
        self.assertEqual(self.wears(self.hull, 2), "hullMat")
        self.assertEqual(self.wears(self.panel, 0), "panelMat_signal")
        self.assertEqual(
            cmds.listConnections("hullMat_signal.emissionColor", source=True), [self.tex],
            "the copy shares its base's map",
        )
        self.assertAlmostEqual(cmds.getAttr("hullMat_signal.emission"), 1.0)
        self.assertAlmostEqual(cmds.getAttr("panelMat_signal.emission"), 0.5)
        self.assertEqual(
            cmds.listConnections("hullMat_signal.emissiveGroupBase", source=True), ["hullMat"]
        )

    def test_selection_in_and_one_undo_chunk(self):
        cmds.select(f"{self.panel}.f[1:2]", replace=True)
        EmissiveGroups.add_group("sel")
        self.assertEqual(self.wears(self.panel, 2), "panelMat_sel")
        cmds.undo()
        self.assertEqual(self.wears(self.panel, 2), "panelMat", "one Ctrl+Z undoes the whole add")
        self.assertFalse(cmds.objExists("panelMat_sel"))

    def test_a_shader_without_an_emission_weight_is_refused(self):
        lambert = cmds.shadingNode("lambert", asShader=True, name="plain")
        MatUtils.assign_mat([self.panel], lambert)
        with self.assertRaises(ValueError) as raised:
            EmissiveGroups.add_group("x", [f"{self.panel}.f[0]"])
        self.assertIn("plain", str(raised.exception))

    def test_removing_restores_the_base_and_deletes_the_copy(self):
        EmissiveGroups.add_group("signal", [f"{self.hull}.f[0:3]"])
        sg = cmds.listConnections("hullMat_signal.outColor", type="shadingEngine")[0]
        EmissiveGroups.remove_group("signal")
        self.assertEqual([self.wears(self.hull, i) for i in range(4)], ["hullMat"] * 4)
        self.assertFalse(cmds.objExists("hullMat_signal"))
        self.assertFalse(cmds.objExists(sg))
        self.assertEqual(EmissiveGroups.list_groups(), {})

    def test_moving_faces_between_groups(self):
        EmissiveGroups.add_group("a", [f"{self.hull}.f[0]"])
        EmissiveGroups.add_group("b", [f"{self.hull}.f[0]"])
        self.assertEqual(self.wears(self.hull, 0), "hullMat_b")
        self.assertFalse(cmds.objExists("hullMat_a"), "an emptied copy goes")

    def test_the_viewport_plays_the_keyed_weight(self):
        EmissiveGroups.add_group("signal", [f"{self.hull}.f[0]"])
        EmissiveGroups.key_weight("signal", 0.0, frame=1)
        EmissiveGroups.key_weight("signal", 1.0, frame=24)
        self.assertAlmostEqual(cmds.getAttr("hullMat_signal.emission", time=1), 0.0)
        self.assertAlmostEqual(cmds.getAttr("hullMat_signal.emission", time=24), 2.0)
        self.assertTrue(EmissiveGroups.list_groups()["signal"]["keyed"])
        self.assertEqual(cmds.keyframe("hullMat.emission", query=True, keyframeCount=True), 0, "the base is not keyed")


class TestInStep(_GroupsCase):
    def test_a_changed_base_value_and_map_reach_the_copy_on_sync(self):
        EmissiveGroups.add_group("signal", [f"{self.hull}.f[0]"])
        cmds.setAttr(f"{self.hull_mat}.baseColor", 0.9, 0.1, 0.1, type="double3")
        other = cmds.shadingNode("file", asTexture=True, name="roughMap")
        cmds.connectAttr(f"{other}.outAlpha", f"{self.hull_mat}.specularRoughness")
        found = EmissiveGroups.sync(check_only=True)
        self.assertEqual(len(found), 1, found)
        self.assertIn("baseColorR", found[0])
        self.assertIn("specularRoughness", found[0])
        EmissiveGroups.sync()
        self.assertAlmostEqual(cmds.getAttr("hullMat_signal.baseColorR"), 0.9)
        self.assertEqual(cmds.listConnections("hullMat_signal.specularRoughness"), [other])
        self.assertEqual(EmissiveGroups.sync(check_only=True), [])

    def test_a_changed_base_strength_rescales_the_copy(self):
        EmissiveGroups.add_group("signal", [f"{self.hull}.f[0]"], weight=0.5)
        cmds.setAttr(f"{self.hull_mat}.emission", 4.0)
        EmissiveGroups.sync()
        self.assertAlmostEqual(cmds.getAttr("hullMat_signal.emission"), 2.0)
        self.assertAlmostEqual(EmissiveGroups.list_groups()["signal"]["weight"], 0.5)


class TestConvertLegacy(_GroupsCase):
    """A scene the old tool authored: objectSets of faces, a slot registry on
    the internal carrier, a keyed weight on the export carrier, the
    ``emissiveGroups`` colour set and the ``emissive_groups`` record."""

    def _old_scene(self):
        hull = cmds.ls(self.hull, long=True)[0]
        cmds.sets([f"{self.hull}.f[0:2]"], name="emissiveGroup_headlights")
        cmds.sets([f"{self.hull}.f[2:3]", f"{self.panel}.f[0:1]"], name="emissiveGroup_signal")
        registry = {
            "schema": 1,
            "encoding": "vertex-color",
            "groups": {
                "headlights": {"slot": 0, "default": 1.0},
                "signal": {"slot": 1, "default": 0.5, "attr": "emissiveGroup_signal"},
            },
            "retired_slots": [],
        }
        DataNodes.write(ptk.Scope.PRIVATE, "emissive_registry", json.dumps(registry))
        export = str(DataNodes.ensure_export())
        DataNodes.write(ptk.Scope.DELIVERABLE, "emissive_groups", json.dumps({"version": 2, "groups": []}))
        cmds.addAttr(export, longName="emissiveGroup_signal", attributeType="float", keyable=True)
        cmds.setKeyframe(f"{export}.emissiveGroup_signal", time=1, value=0.0)
        cmds.setKeyframe(f"{export}.emissiveGroup_signal", time=24, value=1.0)
        cmds.polyColorSet(self.hull, create=True, colorSet="emissiveGroups", representation="RGBA")
        return hull

    def test_an_old_scene_converts_in_one_action(self):
        self._old_scene()
        report = EmissiveGroups.convert_legacy()
        self.assertEqual(report["converted"], {"headlights": 3, "signal": 3})
        self.assertEqual([self.wears(self.hull, i) for i in range(4)], ["hullMat_headlights"] * 3 + ["hullMat_signal"])
        self.assertEqual(self.wears(self.panel, 1), "panelMat_signal")
        self.assertAlmostEqual(cmds.getAttr("hullMat_signal.emission", time=24), 2.0)
        self.assertAlmostEqual(cmds.getAttr("panelMat_signal.emission", time=24), 1.0)
        self.assertTrue(any("headlights" in r and "signal" in r for r in report["reported"]), report)
        self.assertFalse(cmds.ls("emissiveGroup_*", type="objectSet"))
        self.assertFalse(cmds.attributeQuery("emissiveGroup_signal", node=DataNodes.EXPORT, exists=True))
        self.assertNotIn("emissiveGroups", cmds.polyColorSet(self.hull, query=True, allColorSets=True) or [])
        self.assertIsNone(DataNodes.read(ptk.Scope.PRIVATE, "emissive_registry"))
        self.assertIsNone(DataNodes.read(ptk.Scope.DELIVERABLE, "emissive_groups"))
        cmds.undo()
        self.assertTrue(cmds.ls("emissiveGroup_*", type="objectSet"), "one undo step puts the old scene back")


class _ExportCase(_GroupsCase):
    """The FBX plug-in and an output folder of the run's own; the readback."""

    def setUp(self):
        super().setUp()
        if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            try:
                cmds.loadPlugin("fbxmaya")
            except Exception:
                self.skipTest("fbxmaya plugin not available")
        cmds.playbackOptions(minTime=1, maxTime=48)
        self.artifacts = ptk.TempArtifacts("emissive_groups_export", policy="scoped")
        self.addCleanup(self.artifacts.cleanup)
        self.out = self.artifacts.dir_path()

    def _read(self, fbx):
        file = ptk.FbxFile.load(fbx, decode_arrays=ptk.FbxFile.CURVE_ARRAYS)
        (raw,) = [v for _o, key, v in file.user_property_items() if key == ptk.SceneRecords.VISIBILITY.key]
        record = json.loads(bytes(raw).decode("utf-8"))
        curves = ptk.MeshConvert.render_effect_curves(file)
        targets = {target for by_take in curves.values() for target, _channel in by_take}
        glb = fbx[:-4] + ".glb"
        if not os.path.isfile(glb):
            ptk.MeshConvert.fbx_to_glb(fbx, dst=glb, overwrite=True, prompt=False, lightmaps=False)
        with ptk.MeshConvert.open_glb(glb) as edit:
            gltf = edit.gltf
        names = [m.get("name") for m in gltf["materials"]]
        played = {
            note["material"]
            for animation in gltf.get("animations", [])
            for channel in animation["channels"]
            for note in [(channel.get("extras") or {}).get("render_effects") or {}]
            if note.get("material")
        }
        verdict = ptk.ExportVerifier(fbx=fbx, sidecar=None).run(["check_fbx_render_effects"])
        return record, targets, names, played, verdict


class TestRealExport(_ExportCase):
    """A group keyed over two shots, exported for real (the WebXR preview's
    write: the export bracket, its stagers and producers), read back with
    ``ptk.FbxFile`` and in the GLB the conversion builds. Added: 2026-11-07"""

    def _export(self):
        from mayatk.env_utils.fbx_utils import FbxUtils

        EmissiveGroups.add_group("signal", [f"{self.hull}.f[0:1]", f"{self.panel}.f[0]"])
        for frame, value in ((1, 0.0), (24, 1.0), (25, 1.0), (48, 0.0)):
            EmissiveGroups.key_weight("signal", value, frame=frame)
        fbx = os.path.join(self.out, "groups.fbx")
        self.addCleanup(mel.eval, "FBXResetExport")
        # The bracket every writer opens with the render_effects stager (the
        # WebXR preview's and the hand-offs'): it publishes the records and
        # stages the copies' proxies, and the write ships them with the carrier.
        with FbxUtils.export_prepared(FbxUtils.export_context(), stagers=("render_effects",)):
            self.assertEqual(
                sorted(n.split("|")[-1] for n in cmds.ls("*.emissiveGroupProxy", objectsOnly=True, long=True)),
                ["hullMat_signal__emission", "panelMat_signal__emission"],
            )
            FbxUtils.apply_takes([("ShotA", 1, 24), ("ShotB", 25, 48)])
            try:
                cmds.select([self.hull, self.panel, DataNodes.EXPORT], replace=True)
                mel.eval("FBXExportInAscii -v false")
                mel.eval(f'FBXExport -f "{fbx.replace(chr(92), "/")}" -s')
            finally:
                FbxUtils.reset_takes()
        return fbx

    def test_each_animated_copy_rides_the_record_and_its_proxy_per_take(self):
        fbx = self._export()
        self.assertFalse(cmds.ls("*.emissiveGroupProxy", objectsOnly=True), "the proxies leave with the write")
        file = ptk.FbxFile.load(fbx, decode_arrays=ptk.FbxFile.CURVE_ARRAYS)
        (raw,) = [v for _o, key, v in file.user_property_items() if key == ptk.SceneRecords.VISIBILITY.key]
        record = json.loads(bytes(raw).decode("utf-8"))
        self.assertEqual(
            record["materials"],
            [
                {"material": "hullMat_signal", "channels": ["emission"], "emission_color": [1.0, 1.0, 1.0], "emission_strength": 2.0},
                {"material": "panelMat_signal", "channels": ["emission"], "emission_color": [0.2, 0.6, 1.0], "emission_strength": 1.0},
            ],
        )
        self.assertNotIn("emissive_groups", [k for _o, k, _v in file.user_property_items()])
        curves = ptk.MeshConvert.render_effect_curves(file)
        for take in ("ShotA", "ShotB"):
            self.assertLessEqual({("hullMat_signal", "emission"), ("panelMat_signal", "emission")}, set(curves[take]), take)
        peak = max(v for _t, v, _i in curves["ShotA"][("hullMat_signal", "emission")])
        self.assertAlmostEqual(peak, 1.0, places=4, msg="the proxy carries the weight, 0..1")
        verdict = ptk.ExportVerifier(fbx=fbx, sidecar=None).run(["check_fbx_render_effects", "check_fbx_records"])
        self.assertTrue(verdict.ok, verdict.summary())

        glb = os.path.join(self.out, "groups.glb")
        ptk.MeshConvert.fbx_to_glb(fbx, dst=glb, overwrite=True, prompt=False, lightmaps=False)
        with ptk.MeshConvert.open_glb(glb) as edit:
            gltf = edit.gltf
        names = [m.get("name") for m in gltf["materials"]]
        self.assertLessEqual({"hullMat", "hullMat_signal", "panelMat", "panelMat_signal"}, set(names))
        index = names.index("hullMat_signal")
        for animation in gltf["animations"]:
            pointers = [
                c["target"]["extensions"]["KHR_animation_pointer"]["pointer"]
                for c in animation["channels"]
                if c["target"].get("path") == "pointer"
            ]
            self.assertIn(f"/materials/{index}/emissiveFactor", pointers, animation["name"])
        self.assertEqual(
            gltf["materials"][index]["extensions"]["KHR_materials_emissive_strength"]["emissiveStrength"], 2.0
        )
        self.assertFalse([n for n in gltf.get("nodes", []) if "__emission" in (n.get("name") or "")])
        # Parity: the old vertex-colour mask reached the GLB as COLOR_0, which
        # the WebXR page multiplied into the base colour (LOST); the mask
        # texture never reached it (SAME).
        attributes = {a for m in gltf["meshes"] for p in m["primitives"] for a in p["attributes"]}
        self.assertNotIn("COLOR_0", attributes)
        self.assertFalse([i for i in gltf.get("images", []) if "EMask" in json.dumps(i)])


class TestScopedPush(_ExportCase):
    """A two-prop scene with a group on each: a push of one prop carries the
    copies its faces wear and no other -- the record's entries and the
    proxies alike -- through both shapes a writer takes: a bracket opened
    with the write's context (the hand-offs, the WebXR preview), and the
    Scene Exporter, which stages before it publishes its scoped records.
    Read back with ``ptk.FbxFile`` and the GLB. Added: 2026-11-07"""

    def _two_props(self):
        EmissiveGroups.add_group("a", [f"{self.hull}.f[0:1]"])
        EmissiveGroups.add_group("b", [f"{self.panel}.f[0]"])
        for name in ("a", "b"):
            for frame, value in ((1, 0.0), (24, 1.0), (25, 1.0), (48, 0.0)):
                EmissiveGroups.key_weight(name, value, frame=frame)

    def assertOnlyThePanelsCopy(self, fbx):
        record, targets, names, played, verdict = self._read(fbx)
        self.assertEqual([e["material"] for e in record["materials"]], ["panelMat_b"])
        self.assertIn("panelMat_b", targets)
        self.assertNotIn("hullMat_a", targets, "the unshipped copy's proxy stayed out")
        self.assertNotIn("hullMat_a", names)
        self.assertEqual(played, {"panelMat_b"})
        self.assertTrue(verdict.ok, verdict.summary())

    def test_a_bracket_opened_with_the_writes_scope(self):
        from mayatk.env_utils.fbx_utils import FbxUtils

        self._two_props()
        fbx = os.path.join(self.out, "bracket.fbx")
        self.addCleanup(mel.eval, "FBXResetExport")
        panel = cmds.ls(self.panel, long=True)[0]
        ctx = FbxUtils.export_context(mode=ptk.ExportContext.HANDOFF, scope=(panel,))
        with FbxUtils.export_prepared(ctx, stagers=("render_effects",)):
            self.assertEqual(
                [n.split("|")[-1] for n in cmds.ls("*.emissiveGroupProxy", objectsOnly=True, long=True)],
                ["panelMat_b__emission"],
            )
            FbxUtils.apply_takes([("ShotA", 1, 24), ("ShotB", 25, 48)])
            try:
                cmds.select([panel, DataNodes.EXPORT], replace=True)
                mel.eval("FBXExportInAscii -v false")
                mel.eval(f'FBXExport -f "{fbx.replace(chr(92), "/")}" -s')
            finally:
                FbxUtils.reset_takes()
        self.assertOnlyThePanelsCopy(fbx)

    def test_the_scene_exporter_which_stages_before_it_publishes(self):
        from mayatk.anim_utils.shots._shots import ShotStore
        from mayatk.env_utils.scene_exporter._scene_exporter import SceneExporter

        self._two_props()
        store = ShotStore()
        ShotStore.set_active(store)
        self.addCleanup(ShotStore.clear_active)
        store.define_shot("ShotA", 1, 24, objects=[self.hull, self.panel])
        store.define_shot("ShotB", 25, 48, objects=[self.hull, self.panel])
        exporter = SceneExporter(log_level="WARNING")
        exporter.confirm = lambda question: False
        written = exporter.perform_export(
            export_dir=self.out,
            objects=[self.panel],
            output_name="scoped",
            tasks={"output_format": "fbx_glb", "export_data_node": True},
        )
        self.assertTrue(written)
        self.assertOnlyThePanelsCopy(os.path.join(self.out, "scoped.fbx"))


class TestColourEmission(_GroupsCase):
    """Lambert, Blinn and Phong glow by their incandescence (the materials
    record exports it as the glTF emissive colour): a copy's incandescence is
    its base's times the weight, keyed per channel, and its entry states the
    base's colour at strength 1. Added: 2026-11-07"""

    BASE = (0.2, 0.4, 0.8)

    def _base(self, kind="lambert", colour=BASE):
        shader = cmds.shadingNode(kind, asShader=True, name=f"{kind}Base")
        cmds.setAttr(f"{shader}.incandescence", *colour, type="double3")
        MatUtils.assign_mat([self.panel], shader)
        return shader

    def test_each_colour_shader_groups_and_keys_its_incandescence(self):
        for kind in ("lambert", "blinn", "phong"):
            with self.subTest(kind=kind):
                base = self._base(kind)
                (copy,) = EmissiveGroups.add_group(f"g_{kind}", [f"{self.panel}.f[0]"], weight=0.5)
                self.assertEqual(copy, f"{base}_g_{kind}")
                self.assertEqual(cmds.nodeType(copy), kind)
                got = cmds.getAttr(f"{copy}.incandescence")[0]
                for have, want in zip(got, self.BASE):
                    self.assertAlmostEqual(have, want * 0.5, places=5)
                EmissiveGroups.key_weight(f"g_{kind}", 0.0, frame=1)
                EmissiveGroups.key_weight(f"g_{kind}", 1.0, frame=24)
                for channel, want in zip("RGB", self.BASE):
                    plug = f"{copy}.incandescence{channel}"
                    self.assertAlmostEqual(cmds.getAttr(plug, time=1), 0.0, places=5)
                    self.assertAlmostEqual(cmds.getAttr(plug, time=24), want, places=5)
                self.assertTrue(EmissiveGroups.list_groups()[f"g_{kind}"]["keyed"])
                (entry,) = [e for e in EmissiveGroups.render_effect_materials() if e["material"] == copy]
                self.assertEqual(entry["emission_strength"], 1.0)
                for have, want in zip(entry["emission_color"], self.BASE):
                    self.assertAlmostEqual(have, want, places=5)
                EmissiveGroups.remove_group(f"g_{kind}")

    def test_a_push_with_animation_off_states_the_weight(self):
        """The curves a write with no take states for its build
        (``RenderEffects.effect_curves``) carry a colour copy's weight, 0..1,
        as its proxy does -- never the colour's channel values."""
        from mayatk.mat_utils.render_opacity.render_effects import RenderEffects

        self._base()
        (copy,) = EmissiveGroups.add_group("led", [f"{self.panel}.f[0]"])
        EmissiveGroups.key_weight("led", 0.0, frame=1)
        EmissiveGroups.key_weight("led", 1.0, frame=24)
        curves = RenderEffects.effect_curves().get("curves") or []
        (emission,) = [c for c in curves if c["target"] == copy and c["channel"] == "emission"]
        self.assertAlmostEqual(max(k[1] for k in emission["keys"]), 1.0, places=5)

    def test_a_black_incandescence_is_refused_by_name(self):
        self._base(colour=(0.0, 0.0, 0.0))
        with self.assertRaises(ValueError) as raised:
            EmissiveGroups.add_group("x", [f"{self.panel}.f[0]"])
        self.assertIn("lambertBase", str(raised.exception))

    def test_a_changed_base_colour_reaches_the_copy_on_sync(self):
        base = self._base()
        (copy,) = EmissiveGroups.add_group("led", [f"{self.panel}.f[0]"])
        EmissiveGroups.key_weight("led", 1.0, frame=10)
        cmds.setAttr(f"{base}.incandescence", 1.0, 0.0, 0.0, type="double3")
        self.assertTrue(EmissiveGroups.sync(check_only=True))
        EmissiveGroups.sync()
        self.assertAlmostEqual(cmds.getAttr(f"{copy}.incandescenceR", time=10), 1.0, places=5)
        self.assertAlmostEqual(cmds.getAttr(f"{copy}.incandescenceB", time=10), 0.0, places=5)
        self.assertEqual(EmissiveGroups.sync(check_only=True), [])

    def test_an_old_group_on_a_lambert_converts(self):
        self._base()
        cmds.sets([f"{self.panel}.f[0:1]"], name="emissiveGroup_old")
        DataNodes.write(
            ptk.Scope.PRIVATE,
            "emissive_registry",
            json.dumps({"schema": 1, "groups": {"old": {"slot": 0, "default": 0.5}}}),
        )
        report = EmissiveGroups.convert_legacy()
        self.assertEqual(report["converted"], {"old": 2}, report)
        self.assertAlmostEqual(EmissiveGroups.list_groups()["old"]["weight"], 0.5, places=5)


class TestColourExport(_ExportCase):
    """A Lambert copy delivered: its entry states the base's incandescence at
    strength 1, its proxy the weight, its GLB channel the colour times it."""

    def test_a_lambert_copy_rides_the_record_and_the_glb(self):
        from mayatk.env_utils.fbx_utils import FbxUtils

        lamb = cmds.shadingNode("lambert", asShader=True, name="lambBase")
        cmds.setAttr(f"{lamb}.incandescence", 0.2, 0.4, 0.8, type="double3")
        MatUtils.assign_mat([self.panel], lamb)
        EmissiveGroups.add_group("led", [f"{self.panel}.f[0]"])
        for frame, value in ((1, 0.0), (24, 1.0), (25, 1.0), (48, 0.0)):
            EmissiveGroups.key_weight("led", value, frame=frame)
        fbx = os.path.join(self.out, "lambert.fbx")
        self.addCleanup(mel.eval, "FBXResetExport")
        with FbxUtils.export_prepared(FbxUtils.export_context(), stagers=("render_effects",)):
            FbxUtils.apply_takes([("ShotA", 1, 24), ("ShotB", 25, 48)])
            try:
                cmds.select([self.panel, DataNodes.EXPORT], replace=True)
                mel.eval("FBXExportInAscii -v false")
                mel.eval(f'FBXExport -f "{fbx.replace(chr(92), "/")}" -s')
            finally:
                FbxUtils.reset_takes()
        record, targets, names, played, verdict = self._read(fbx)
        (entry,) = record["materials"]
        self.assertEqual(entry["material"], "lambBase_led")
        self.assertEqual(entry["emission_strength"], 1.0)
        for have, want in zip(entry["emission_color"], (0.2, 0.4, 0.8)):
            self.assertAlmostEqual(have, want, places=4)
        file = ptk.FbxFile.load(fbx, decode_arrays=ptk.FbxFile.CURVE_ARRAYS)
        curve = ptk.MeshConvert.render_effect_curves(file)["ShotA"][("lambBase_led", "emission")]
        self.assertAlmostEqual(max(v for _t, v, _i in curve), 1.0, places=4, msg="the weight, 0..1")
        self.assertEqual(played, {"lambBase_led"})
        self.assertIn("lambBase_led", names)
        self.assertTrue(verdict.ok, verdict.summary())


class TestPublishedStrength(_GroupsCase):
    """Weight 1 is the base's own glow as the delivery states it: for every
    shader in ``ShaderAttributeMap.EMISSION_WEIGHT_ATTRS`` this instance can
    make, a copy's entry colour times its strength is its base's emissive
    factor in the ``materials`` record. Bug: an openPBR copy stated its
    luminance (1000 nits) as its strength, so the GLB gave it
    KHR_materials_emissive_strength 1000 beside a base the record states at
    weight 1 -- glowing 1000 times brighter. Fixed: 2026-11-07
    (``MayaEmissiveGroups.published_strength``)."""

    PLUGINS = {"aiStandardSurface": "mtoa", "StingrayPBS": "shaderFXPlugin"}
    #: Each family's emission scalar, far from 1 so a wrong rule shows.
    SCALARS = {"standardSurface": 3.0, "aiStandardSurface": 3.0, "openPBRSurface": 1000.0, "StingrayPBS": 3.0}

    def _available(self, node_type):
        plugin = self.PLUGINS.get(node_type)
        if plugin and not cmds.pluginInfo(plugin, query=True, loaded=True):
            try:
                cmds.loadPlugin(plugin, quiet=True)
            except Exception:
                return False
        return node_type in (cmds.allNodeTypes() or [])

    def test_every_weight_family_ships_its_base_glow_at_weight_1(self):
        from mayatk.env_utils.material_records import MaterialRecords
        from mayatk.mat_utils.shader_attribute_map import ShaderAttributeMap

        ran = []
        for node_type, (attr, mode) in sorted(ShaderAttributeMap.EMISSION_WEIGHT_ATTRS.items()):
            if not self._available(node_type):
                continue
            with self.subTest(shader=node_type, mode=mode):
                obj = cmds.polyCube(name=f"obj_{node_type}")[0]
                if node_type == "StingrayPBS":  # its attributes are its graph's
                    base = MatUtils.create_stingray_shader(f"base_{node_type}")
                    if not cmds.attributeQuery(attr, node=base, exists=True):
                        continue
                else:
                    base = cmds.shadingNode(node_type, asShader=True, name=f"base_{node_type}")
                colour_attr = ShaderAttributeMap.constant_attr(node_type, "emission")
                cmds.setAttr(f"{base}.{colour_attr}", 1.0, 0.5, 0.25, type="double3")
                cmds.setAttr(f"{base}.{attr}", self.SCALARS[node_type])
                MatUtils.assign_mat([obj], base)
                (copy,) = EmissiveGroups.add_group(f"g_{node_type}", [f"{obj}.f[0]"])
                EmissiveGroups.key_weight(f"g_{node_type}", 0.0, frame=1)
                EmissiveGroups.key_weight(f"g_{node_type}", 1.0, frame=24)
                (entry,) = [e for e in EmissiveGroups.render_effect_materials() if e["material"] == copy]
                (stated,) = [e for e in MaterialRecords.entries([obj]) if e["name"] == base]
                delivered = [c * entry["emission_strength"] for c in entry["emission_color"]]
                for have, want in zip(delivered, stated["emissiveFactor"]):
                    self.assertAlmostEqual(have, want, places=4, msg=f"{node_type}: weight 1 is the base's glow")
                if mode == "gate":
                    self.assertEqual(entry["emission_strength"], 1.0)
                    peak = max(v for _f, v in EmissiveGroups.HOST.emission_keys(copy))
                    self.assertAlmostEqual(peak, self.SCALARS[node_type], places=3, msg="the copy keys its nits")
                    curves = EmissiveGroups.weight_curves(
                        lambda c: [[f, v, "linear"] for f, v in EmissiveGroups.HOST.emission_keys(c)]
                    )
                    (curve,) = [c for c in curves if c["target"] == copy]
                    self.assertAlmostEqual(max(k[1] for k in curve["keys"]), 1.0, places=5, msg="its curve the weight")
                ran.append(node_type)
        self.assertIn("openPBRSurface", ran, "the gate family must be exercised")
        self.assertIn("standardSurface", ran)


class TestNamespacedExport(_ExportCase):
    """A group's copy under a namespace, exported for real: the FBX and the
    GLB carry the material as ``ns:panelMat_signal`` (measured 2026-11-07),
    the record names it so, its proxy and curve go by the transport's name
    (``panelMat_signal``), and every reader joins the two. Bug: the GLB pass
    found no curve for ``ns:panelMat_signal`` -- the weight animation was
    dropped without a word -- and the verifier failed a pair that was there.
    Fixed: 2026-11-07 (``SceneRecords.TRANSPORT.target_name``)."""

    def test_a_namespaced_copy_plays_in_the_glb(self):
        from mayatk.env_utils.fbx_utils import FbxUtils

        EmissiveGroups.add_group("signal", [f"{self.panel}.f[0]"])
        for frame, value in ((1, 0.0), (24, 1.0)):
            EmissiveGroups.key_weight("signal", value, frame=frame)
        src = os.path.join(self.out, "source.ma")
        cmds.file(rename=src)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)
        cmds.currentUnit(time="film")
        cmds.playbackOptions(minTime=1, maxTime=24)
        cmds.file(src, i=True, namespace="ns")
        self.assertEqual(sorted(EmissiveGroups.HOST.copies()), ["ns:panelMat_signal"])
        fbx = os.path.join(self.out, "namespaced.fbx")
        self.addCleanup(mel.eval, "FBXResetExport")
        with FbxUtils.export_prepared(FbxUtils.export_context(), stagers=("render_effects",)):
            cmds.select(["ns:panel", DataNodes.EXPORT], replace=True)
            mel.eval("FBXExportInAscii -v false")
            mel.eval(f'FBXExport -f "{fbx.replace(chr(92), "/")}" -s')
        record, targets, names, played, verdict = self._read(fbx)
        self.assertEqual([e["material"] for e in record["materials"]], ["ns:panelMat_signal"])
        self.assertEqual(targets, {"panelMat_signal"}, "the curve, by the transport's name")
        self.assertIn("ns:panelMat_signal", names, "the GLB names it as the FBX does")
        self.assertEqual(played, {"ns:panelMat_signal"}, "its weight plays")
        self.assertTrue(verdict.ok, verdict.summary())


if __name__ == "__main__":
    unittest.main()
