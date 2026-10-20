# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.env_utils.fbx_utils module.

Covers FbxUtils — plugin loading, preset application, option setting,
and the ``export`` driver.
"""

import os
import logging
import shutil
import struct
import unittest
import tempfile
from unittest.mock import patch

import maya.cmds as cmds
import maya.mel as mel

import pythontk as ptk
from mayatk.env_utils.fbx_utils import FbxUtils

from base_test import MayaTkTestCase, QuickTestCase


class TestFbxUtilsPlugin(MayaTkTestCase):
    """load_plugin should be idempotent."""

    def test_load_plugin_idempotent(self):
        # First call may or may not have loaded already
        FbxUtils.load_plugin()
        self.assertTrue(cmds.pluginInfo("fbxmaya", query=True, loaded=True))

        # Second call should not raise
        FbxUtils.load_plugin()
        self.assertTrue(cmds.pluginInfo("fbxmaya", query=True, loaded=True))


class TestFbxUtilsLoadPreset(MayaTkTestCase):
    """load_preset validates path existence."""

    def test_missing_preset_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            FbxUtils.load_preset(r"C:/__nonexistent__/no.fbxexportpreset")


class TestKnownProducers(MayaTkTestCase):
    """Every ``FbxUtils.PRODUCERS`` / ``STAGERS`` entry must resolve to a real
    callable.

    The tables name module/class/method as strings resolved lazily, and
    ``FbxUtils.producers`` skips an unresolvable entry with only a debug log
    -- so a rename anywhere in those modules would silently stop that
    record shipping. This pins each entry to the code.
    """

    def test_all_entries_resolve(self):
        """Every METHOD column, not only the first: a stager's ``finish`` is
        the column a misspelling would silently drop."""
        import importlib

        entries = {spec.key: row for spec, row in FbxUtils.PRODUCERS.items()}
        entries.update(FbxUtils.STAGERS)
        for name, (module_path, class_name, *methods) in entries.items():
            cls = getattr(importlib.import_module(module_path), class_name)
            # An empty column is declared, not misspelt: a one-way stager.
            for method_name in filter(None, methods):
                with self.subTest(entry=name, method=method_name):
                    self.assertTrue(
                        callable(getattr(cls, method_name, None)),
                        f"{name}: {module_path}.{class_name}.{method_name} "
                        "is not callable",
                    )

    def test_a_misspelt_method_warns_rather_than_vanishing(self):
        """``stagers()`` used to resolve a misspelt ``finish`` to None with no
        word, so the stager staged and never finished.
        Added: 2026-09-18
        """
        from unittest import mock

        row = ("mayatk.mat_utils.render_opacity.render_effects", "RenderEffects")
        with mock.patch.dict(
            FbxUtils.STAGERS,
            {"probe": (*row, "prepare_for_export", "finsh_export")},
            clear=True,
        ):
            with self.assertLogs("mayatk.env_utils.fbx_utils", "WARNING") as logs:
                prepare, finish = FbxUtils.stagers(["probe"])["probe"]
        self.assertTrue(callable(prepare))
        self.assertIsNone(finish)
        self.assertIn("finsh_export", "\n".join(logs.output))

    def test_a_one_way_stager_declares_its_empty_finish_quietly(self):
        """A ``None`` finish is DECLARED, not misspelt -- the lightmap folder
        lift has nothing to undo -- so it resolves to None without the
        misspelling warning, and its prepare still resolves.
        Added: 2026-09-23
        """
        from unittest import mock

        row = ("mayatk.light_utils.lightmap_baker.lightmap_records", "LightmapRecords")
        with mock.patch.dict(
            FbxUtils.STAGERS,
            {"probe": (*row, "migrate_folder_hints", None)},
            clear=True,
        ):
            with self.assertNoLogs("mayatk.env_utils.fbx_utils", "WARNING"):
                prepare, finish = FbxUtils.stagers(["probe"])["probe"]
        self.assertTrue(callable(prepare))
        self.assertIsNone(finish)


class TestRecordUnits(MayaTkTestCase):
    """Every record ships its lengths in the file's unit -- the export's
    target, which the Scene Exporter makes the working unit before it runs the
    producers (``set_linear_unit``) -- with ``unit_scale`` beside them, whatever
    unit the scene's tools ran in.

    A value read in one unit and labelled with another shipped a metre
    export's lights 100x too far (2026-10-17). Each measured producer's
    fixture builds one physical scene, runs its tool under (cm, deg) and under
    (m, rad), and produces the record under both: each record's
    ``unit_scale`` is its export's unit, and its metres (:meth:`_in_metres`)
    are the same in all four.
    Added: 2026-10-17
    """

    #: The producers whose records carry no length or angle, and what they
    #: carry instead. A new producer belongs here or in :meth:`_fixtures`.
    UNITLESS = {
        ptk.SceneRecords.SHOTS.key: "frames, with their fps",
        ptk.SceneRecords.VISIBILITY.key: "frames with their fps, and colours",
        ptk.SceneRecords.AUDIO.key: "frames, with their fps",
        ptk.SceneRecords.EMISSIVE_GROUPS.key: "weights",
    }
    UNITS = (("cm", "deg"), ("m", "rad"))

    def setUp(self):
        super().setUp()
        self.addCleanup(cmds.currentUnit, linear="cm", angle="deg")
        self.tmp = ptk.TempArtifacts("record_units", policy="session").dir_path()

    def _fixtures(self):
        return {
            ptk.SceneRecords.LIGHTMAPS.key: self._lightmaps,
            ptk.SceneRecords.SHADOWS.key: self._shadows,
            ptk.SceneRecords.ARTICULATION.key: self._articulation,
        }

    def test_every_producer_is_measured_or_declared_unitless(self):
        produced = {spec.key for spec in FbxUtils.PRODUCERS}
        self.assertEqual(produced, set(self.UNITLESS) | set(self._fixtures()))

    def test_the_lightmap_record_is_in_the_file_unit_whatever_the_units(self):
        self._assert_invariant(ptk.SceneRecords.LIGHTMAPS.key)

    def test_the_shadow_record_is_in_the_file_unit_whatever_the_units(self):
        self._assert_invariant(ptk.SceneRecords.SHADOWS.key)

    def test_the_articulation_record_is_in_the_file_unit_whatever_the_units(self):
        self._assert_invariant(ptk.SceneRecords.ARTICULATION.key)

    # ------------------------------------------------------------- harness
    def _assert_invariant(self, key):
        import json

        payloads = {}
        for authored in self.UNITS:
            cmds.file(new=True, force=True)
            cmds.currentUnit(linear="cm", angle="deg")
            self._fixtures()[key](authored)
            for produced in self.UNITS:
                cmds.currentUnit(linear=produced[0], angle=produced[1])
                (producer,) = FbxUtils.producers([key]).values()
                record = producer(FbxUtils.export_context())
                self.assertIsNotNone(record, f"{key}: the fixture made no record")
                payload = json.loads(record.text)
                unit = ptk.MathUtils.metres_per_unit(produced[0])
                self._assert_unit_scale(payload, unit, f"{key} {authored + produced}")
                payloads[authored + produced] = self._in_metres(key, payload, unit)
        reference = payloads[("cm", "deg", "cm", "deg")]
        for units, metres in payloads.items():
            self._assert_same(reference, metres, f"{key} {units}")
        return reference

    def _assert_unit_scale(self, payload, unit, where):
        """Every ``unit_scale`` in *payload* is the file's: *unit* metres."""
        if isinstance(payload, dict):
            if "unit_scale" in payload:
                self.assertAlmostEqual(payload["unit_scale"], unit, msg=where)
            for k, v in payload.items():
                self._assert_unit_scale(v, unit, f"{where}.{k}")
        elif isinstance(payload, list):
            for i, v in enumerate(payload):
                self._assert_unit_scale(v, unit, f"{where}[{i}]")

    @staticmethod
    def _in_metres(key, payload, unit):
        """*payload* as its readers take it, in metres: the lights through the
        GLB's own conversion, every other length times its ``unit_scale`` --
        or, for the articulation record (it carries none), the file's unit."""
        import copy

        out = copy.deepcopy(payload)
        if key == ptk.SceneRecords.LIGHTMAPS.key:
            out["lights"] = ptk.MeshConvert.lights_in_metres(out.get("lights"))
            probe = out.get("probe") or {}
            scale = probe.pop("unit_scale", 1.0)
            probe["position"] = [c * scale for c in probe.get("position") or []]
            probe["box"] = [[c * scale for c in v] for v in probe.get("box") or []]
        elif key == ptk.SceneRecords.SHADOWS.key:
            scale = out.pop("unit_scale")
            for plane in out["planes"]:
                for name in ("ground", "radius", "height", "source_size"):
                    plane[name] *= scale
                if plane.get("horizon"):
                    plane["horizon"]["bounds"] = [
                        c * scale for c in plane["horizon"]["bounds"]
                    ]
                    plane["horizon"]["height_scale"] *= scale
        elif key == ptk.SceneRecords.ARTICULATION.key:
            for rig in out["rigs"]:
                for joint in rig["joints"]:
                    joint["t"] = [c * unit for c in joint["t"]]
                    for channel in joint["channels"]:
                        if channel["channel"].startswith("t"):
                            for side in ("min", "max"):
                                if channel[side] is not None:
                                    channel[side] *= unit
        return out

    def _assert_same(self, want, got, where):
        if isinstance(want, dict):
            self.assertIsInstance(got, dict, where)
            self.assertEqual(sorted(want), sorted(got), where)
            for k in want:
                self._assert_same(want[k], got[k], f"{where}.{k}")
        elif isinstance(want, list):
            self.assertIsInstance(got, list, where)
            self.assertEqual(len(want), len(got), where)
            for i, (a, b) in enumerate(zip(want, got)):
                self._assert_same(a, b, f"{where}[{i}]")
        elif isinstance(want, float) or isinstance(got, float):
            self.assertLessEqual(
                abs(float(want) - float(got)),
                2e-6 + 1e-6 * abs(float(want)),
                f"{where}: {want} vs {got}",
            )
        else:
            self.assertEqual(want, got, where)

    @staticmethod
    def _here(metres):
        """A position given in metres, in the working unit cmds takes."""
        from mayatk.core_utils.units import Units

        return Units.convert(list(metres), "m", "working")

    @staticmethod
    def _deg(degrees):
        from mayatk.core_utils.units import Units

        return Units.angle(degrees, "deg", "working")

    def _set_units(self, units):
        cmds.currentUnit(linear=units[0], angle=units[1])

    # ------------------------------------------------------------ fixtures
    def _lightmaps(self, units):
        """A marked bench, a recorded probe, and a spot and an area light."""
        import os

        from mayatk.light_utils.lightmap_baker.lightmap_records import (
            LightmapRecords,
        )

        _arnold = __import__("mayatk").Plugins.available("mtoa")
        if _arnold:
            __import__("mayatk").Plugins.load("mtoa")
        bench = cmds.polyCube(name="Bench", width=200, height=90, depth=80)[0]
        self._set_units(units)
        lightmap = os.path.join(self.tmp, "Bench_Lightmap.exr")
        open(lightmap, "wb").close()
        LightmapRecords.commit({bench: lightmap})
        probe = os.path.join(self.tmp, "Bench_Probe.exr")
        open(probe, "wb").close()
        LightmapRecords.commit_probe(
            probe,
            self._here((0.0, 1.2, 0.5)),
            [self._here((-2.0, 0.0, -1.0)), self._here((2.0, 2.5, 1.5))],
        )
        spot = cmds.shadingNode("spotLight", asLight=True)
        cmds.xform(
            spot, worldSpace=True, translation=self._here((0.4, 3.0, -0.2))
        )
        cmds.xform(spot, worldSpace=True, rotation=[self._deg(-90.0), 0.0, 0.0])
        cmds.setAttr(f"{spot}.coneAngle", self._deg(40.0))
        cmds.setAttr(f"{spot}.penumbraAngle", self._deg(10.0))
        cmds.setAttr(f"{spot}.intensity", 2.0)
        shape = cmds.listRelatives(spot, shapes=True, fullPath=True)[0]
        if cmds.attributeQuery("aiRadius", node=shape, exists=True):
            cmds.setAttr(f"{shape}.aiRadius", 5.0)  # a plain float: centimetres
        area = cmds.shadingNode("areaLight", asLight=True)
        cmds.xform(area, worldSpace=True, translation=self._here((0.1, 1.2, 2.5)))
        cmds.xform(area, scale=(125, 8, 1))
        cmds.setAttr(f"{area}.intensity", 3.0)

    def _shadows(self, units):
        """A shadow rig on a box, lit from above and to the side."""
        from mayatk.rig_utils.shadow_rig import ShadowRig

        box = cmds.polyCube(name="Box", width=40, height=60, depth=30)[0]
        cmds.move(0, 30, 0, box)
        self._set_units(units)
        rig = ShadowRig.create(
            [box], light_pos=self._here((0.6, 2.0, 0.4)), texture_res=64
        )
        if rig.texture_path:
            self.addCleanup(
                lambda p=rig.texture_path: os.path.exists(p) and os.remove(p)
            )

    def _articulation(self, units):
        """The test arm, rigged: a turn and a slide limit typed in (the plan's
        degrees and centimetres, as the panel passes them), then each taken
        from a pose the user made in the scene's own units."""
        from test_articulated_rig import build_arm
        from mayatk.core_utils.units import Units
        from mayatk.rig_utils.articulated_rig._articulated_rig import ArticulatedRig

        arm = build_arm()
        self._set_units(units)
        plan = ArticulatedRig.analyze(arm)
        rig = ArticulatedRig.create(plan["links"], plan["joints"])
        posed = set()
        for joint, entry in zip(rig.spec["joints"], rig.record()["joints"]):
            channels = [c["channel"] for c in entry["channels"]]
            turn = next((c for c in channels if c.startswith("r")), None)
            slide = next((c for c in channels if c.startswith("t")), None)
            ctrl = rig.control(joint["id"])
            if turn and "turn" not in posed:
                rig.set_limits(joint["id"], turn, -30.0, 45.0)
                cmds.setAttr(f"{ctrl}.{turn}", self._deg(20.0))
                rig.set_limit_from_pose(joint["id"], turn, "max")
                posed.add("turn")
            if slide and "slide" not in posed:
                rig.set_limits(joint["id"], slide, -1.0, 2.0)
                cmds.setAttr(f"{ctrl}.{slide}", Units.convert(0.015, "m", "working"))
                rig.set_limit_from_pose(joint["id"], slide, "max")
                posed.add("slide")
        self.assertEqual(posed, {"turn", "slide"}, "the fixture posed no limit")


class TestFbxUtilsExport(MayaTkTestCase):
    """End-to-end export path."""

    def setUp(self):
        super().setUp()
        FbxUtils.load_plugin()
        self.tempdir = tempfile.mkdtemp(prefix="fbx_test_")

    def tearDown(self):
        # Best-effort cleanup
        for f in os.listdir(self.tempdir):
            try:
                os.remove(os.path.join(self.tempdir, f))
            except Exception:
                pass
        try:
            os.rmdir(self.tempdir)
        except Exception:
            pass
        super().tearDown()

    def test_export_selection_with_no_selection_raises(self):
        cmds.select(clear=True)
        with self.assertRaises(RuntimeError):
            FbxUtils.export(
                os.path.join(self.tempdir, "noselection.fbx"),
                selection_only=True,
            )

    def test_export_appends_fbx_extension(self):
        cube = cmds.polyCube(name="fbx_export_cube")[0]
        cmds.select(cube)
        out = os.path.join(self.tempdir, "noext")
        result = FbxUtils.export(out, objects=[cube], selection_only=True)
        self.assertTrue(result.lower().endswith(".fbx"))
        self.assertTrue(os.path.isfile(result))

    def test_export_creates_intermediate_directories(self):
        cube = cmds.polyCube(name="fbx_dir_cube")[0]
        cmds.select(cube)
        nested = os.path.join(self.tempdir, "a", "b", "c", "out.fbx")
        result = FbxUtils.export(nested, objects=[cube], selection_only=True)
        self.assertTrue(os.path.isfile(result))

    def test_export_all_does_not_require_selection(self):
        cmds.polyCube(name="fbx_all_cube")
        cmds.select(clear=True)

        out = os.path.join(self.tempdir, "all.fbx")
        # Should not raise even though selection is empty
        result = FbxUtils.export(out, selection_only=False)
        self.assertTrue(os.path.isfile(result))

    def test_export_embeds_relative_texture_despite_foreign_cwd(self):
        """Embed media + project-relative texture + foreign CWD must embed.

        FbxUtils.export is the bridges' shared write path (handoff_export
        defaults FBXExportEmbeddedTextures=True); the fbxmaya plugin locates
        embed sources against the process CWD, never the workspace — the
        export must write from the workspace root (embed_media_write_cwd)
        or every relative texture silently drops from the FBX.
        Added: 2026-08-04
        """
        import shutil
        import maya.api.OpenMaya as om

        proj = os.path.join(self.tempdir, "proj")
        si = os.path.join(proj, "sourceimages")
        os.makedirs(si, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(proj, ignore_errors=True))
        payload = os.urandom(1024 * 1024)  # incompressible — size is the tell
        with open(os.path.join(si, "emb.png"), "wb") as f:
            f.write(payload)

        original_ws = cmds.workspace(q=True, rd=True)
        self.addCleanup(lambda: cmds.workspace(original_ws, openWorkspace=True))
        cmds.workspace(proj, openWorkspace=True)

        cube = cmds.polyCube(name="embed_cwd_cube")[0]
        shader = cmds.shadingNode("lambert", asShader=True)
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{shader}SG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)
        file_node = cmds.shadingNode("file", asTexture=True)
        cmds.connectAttr(f"{file_node}.outColor", f"{shader}.color")
        # Verbatim relative path — cmds.setAttr auto-expands a resolvable one.
        sel = om.MSelectionList()
        sel.add(file_node)
        om.MFnDependencyNode(sel.getDependNode(0)).findPlug(
            "fileTextureName", False
        ).setString("sourceimages/emb.png")

        out = os.path.join(self.tempdir, "embed_cwd.fbx")
        original_cwd = os.getcwd()
        elsewhere = os.path.join(self.tempdir, "elsewhere")
        os.makedirs(elsewhere, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(elsewhere, ignore_errors=True))
        os.chdir(elsewhere)  # foreign CWD — the failure state
        try:
            FbxUtils.export(
                out,
                objects=[cube],
                options={"FBXExportEmbeddedTextures": True},
                selection_only=True,
            )
            self.assertEqual(os.getcwd(), elsewhere, "export must restore the CWD")
        finally:
            os.chdir(original_cwd)

        self.assertGreater(
            os.path.getsize(out),
            len(payload),
            "embedded texture payload missing — the plugin could not locate "
            "the project-relative path",
        )


class TestDropRigApparatus(MayaTkTestCase):
    """``FbxUtils.drop_rig_apparatus`` -- the census named in the scene, dropped
    from the written file, the scene untouched."""

    def setUp(self):
        super().setUp()
        from test_rig_graph_extract import _apparatus_scene

        FbxUtils.load_plugin()
        _apparatus_scene()
        self.tempdir = tempfile.mkdtemp(prefix="fbx_rig_test_")
        self.addCleanup(self._clean)

    def _clean(self):
        for name in os.listdir(self.tempdir):
            try:
                os.remove(os.path.join(self.tempdir, name))
            except OSError:
                pass
        try:
            os.rmdir(self.tempdir)
        except OSError:
            pass

    def _models(self, path):
        import pythontk as ptk

        return set(ptk.FbxFile.load(path, raw_payloads=False).object_names("Model"))

    def test_the_written_file_loses_the_rig_and_keeps_what_it_drove(self):
        roots = ["skel_root", "body", "rig", "artist_null", "marker_loc"]
        path = FbxUtils.export(
            os.path.join(self.tempdir, "rigged.fbx"),
            objects=roots,
            options={"FBXExportCameras": False},
        )
        before = self._models(path)
        self.assertLessEqual({"rig", "ctrl", "driver_jnt", "up_loc"}, before)

        report = FbxUtils.drop_rig_apparatus(path, cmds.ls(roots, long=True))

        after = self._models(path)
        self.assertTrue(
            {
                "rig",
                "ctrl_GRP",
                "ctrl",
                "driver_jnt",
                "ik_curve",
                "up_loc",
                "aimed",
            }.isdisjoint(after),
            sorted(after),
        )
        self.assertLessEqual(
            {"skel_root", "skel_tip", "body", "artist_null", "marker_loc"}, after
        )
        self.assertEqual(report["models"], len(before) - len(after))
        # Named, never deleted: the scene still has the rig that drove the bake.
        self.assertTrue(cmds.objExists("|rig|ctrl_GRP|ctrl"))

    def test_a_failure_keeps_the_file_and_never_raises(self):
        junk = os.path.join(self.tempdir, "not_an.fbx")
        with open(junk, "wb") as fh:
            fh.write(b"not an fbx" * 40)
        with self.assertLogs("mayatk.env_utils.fbx_utils", level="WARNING"):
            self.assertIsNone(FbxUtils.drop_rig_apparatus(junk))
        with open(junk, "rb") as fh:
            self.assertEqual(fh.read(), b"not an fbx" * 40)


class TestFbxUtilsSetOptions(MayaTkTestCase):
    """set_fbx_options should accept bool/int/float/str types via the ``-v`` flag."""

    def test_set_bool_options_do_not_raise(self):
        FbxUtils.load_plugin()
        FbxUtils.set_fbx_options(
            {
                "FBXExportSmoothingGroups": True,
                "FBXExportSmoothMesh": False,
                "FBXExportInAscii": True,
            }
        )

    def test_set_string_option_does_not_raise(self):
        FbxUtils.load_plugin()
        FbxUtils.set_fbx_options({"FBXExportUpAxis": "y"})

    def test_set_quaternion_string_does_not_raise(self):
        # FBXExportQuaternion strictly requires the ``-v`` flag.
        FbxUtils.load_plugin()
        FbxUtils.set_fbx_options({"FBXExportQuaternion": "euler"})

    def test_set_int_option_does_not_raise(self):
        FbxUtils.load_plugin()
        FbxUtils.set_fbx_options({"FBXExportBakeComplexStart": 1})

    def test_set_float_option_does_not_raise(self):
        FbxUtils.load_plugin()
        FbxUtils.set_fbx_options({"FBXExportScaleFactor": 1.0})


class TestFbxUtilsExportWithOptions(MayaTkTestCase):
    """Combined preset/options/objects export path."""

    def setUp(self):
        super().setUp()
        FbxUtils.load_plugin()
        self.tempdir = tempfile.mkdtemp(prefix="fbx_opts_test_")

    def tearDown(self):
        for f in os.listdir(self.tempdir):
            try:
                os.remove(os.path.join(self.tempdir, f))
            except Exception:
                pass
        try:
            os.rmdir(self.tempdir)
        except Exception:
            pass
        super().tearDown()

    def test_export_with_options_applied(self):
        """export() should accept inline options without preset."""
        cube = cmds.polyCube(name="fbx_opts_cube")[0]
        cmds.select(cube)
        out = os.path.join(self.tempdir, "with_options.fbx")
        result = FbxUtils.export(
            out,
            objects=[cube],
            options={"FBXExportSmoothingGroups": True, "FBXExportInAscii": True},
            selection_only=True,
        )
        self.assertTrue(os.path.isfile(result))

    def test_export_with_nonexistent_preset_raises(self):
        """preset_file pointing to a missing path should raise FileNotFoundError."""
        cube = cmds.polyCube(name="fbx_bad_preset_cube")[0]
        cmds.select(cube)
        out = os.path.join(self.tempdir, "with_bad_preset.fbx")
        with self.assertRaises(FileNotFoundError):
            FbxUtils.export(
                out,
                objects=[cube],
                preset_file=r"C:/__nonexistent__/bad.fbxexportpreset",
                selection_only=True,
            )


class TestFbxUtilsImport(MayaTkTestCase):
    """import_scene — round-trip and native namespace isolation."""

    def setUp(self):
        super().setUp()
        FbxUtils.load_plugin()
        self.tempdir = tempfile.mkdtemp(prefix="fbx_import_test_")
        # Build and export a small hierarchy, then start from an empty scene.
        root = cmds.group(em=True, name="IMP_ROOT")
        child = cmds.polyCube(name="IMP_BOX")[0]
        cmds.parent(child, root)
        self.fbx_path = os.path.join(self.tempdir, "roundtrip.fbx")
        FbxUtils.export(self.fbx_path, objects=["IMP_ROOT"], selection_only=True)
        cmds.file(new=True, force=True)

    def tearDown(self):
        for f in os.listdir(self.tempdir):
            try:
                os.remove(os.path.join(self.tempdir, f))
            except Exception:
                pass
        try:
            os.rmdir(self.tempdir)
        except Exception:
            pass
        super().tearDown()

    def test_import_into_namespace_isolates(self):
        new_nodes = FbxUtils.import_scene(self.fbx_path, namespace="imp_ns")
        self.assertTrue(new_nodes)
        leaves = {
            t.split(":")[-1] for t in (cmds.ls("imp_ns:*", type="transform") or [])
        }
        self.assertIn("IMP_ROOT", leaves)
        self.assertIn("IMP_BOX", leaves)
        # Nothing leaked to the root namespace.
        self.assertFalse(cmds.ls("IMP_ROOT") or cmds.ls("IMP_BOX"))

    def test_import_without_namespace_at_root(self):
        FbxUtils.import_scene(self.fbx_path)
        self.assertTrue(cmds.ls("IMP_ROOT"))

    def test_import_restores_active_namespace(self):
        before = cmds.namespaceInfo(currentNamespace=True, absoluteName=True)
        FbxUtils.import_scene(self.fbx_path, namespace="imp_ns2")
        after = cmds.namespaceInfo(currentNamespace=True, absoluteName=True)
        self.assertEqual(before, after)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            FbxUtils.import_scene(r"C:/__nonexistent__/missing.fbx")

    def test_import_resets_sticky_plugin_state(self):
        """Import options are global + sticky: a poisoned mode persists across
        calls (probe-verified) and would silently shape this import. The
        reset must run even with caller options -- ``options={}`` applies
        nothing, so only ``reset_import`` explains the mode changing."""
        import maya.mel as mel

        mel.eval("FBXImportMode -v exmerge")  # sticky poison
        FbxUtils.import_scene(self.fbx_path, options={})
        self.assertNotEqual(mel.eval("FBXImportMode -q"), "exmerge")

    def test_reset_export_returns_sticky_flags_to_the_factory_state(self):
        """Export options are global + sticky exactly as import options are, and
        the flags that decide a deliverable's STRUCTURE are among them.

        ``FBXExportInstances`` is the measured case: the substance bridge turns
        it off for its own exports, and the hand-off mixin PINS it back for
        that reason. A writer that pins nothing inherits whatever ran before
        it, so the same scene exported twice in one session ships a different
        mesh count. This only makes the state KNOWN -- probed 2026-08-29, the
        factory values are instancing OFF, smoothing groups OFF and embedded
        media OFF, so a caller relying on the reset alone gets a determinstic
        but degraded export; see the Scene Exporter, which pins over it.
        """
        import maya.mel as mel

        mel.eval("FBXExportInstances -v true")
        mel.eval("FBXExportSmoothingGroups -v true")
        FbxUtils.reset_export()
        self.assertFalse(mel.eval("FBXExportInstances -q"))
        self.assertFalse(mel.eval("FBXExportSmoothingGroups -q"))

    def test_reset_export_is_idempotent(self):
        """Called on an already-clean session it must still be a no-op, since
        the caller cannot know whether anything poisoned the state."""
        FbxUtils.reset_export()
        FbxUtils.reset_export()

    def test_reset_import_keeps_animation_takes(self):
        """``FBXResetImport``'s Maya-2025 factory state selects the "No
        Animation" import take (``Import|IncludeGrp|Animation|ExtraGrp|Take``,
        probed 2026-08-14) — every later raw ``cmds.file(i=True)`` FBX import
        then silently drops its animCurves while the attrs themselves land.
        ``reset_import`` must repair the selector, or any ``import_scene``
        call poisons the whole session's raw imports (the mechanism behind a
        cross-module keyed-curve round-trip failure)."""
        cube = cmds.polyCube(name="anim_take_cube")[0]
        cmds.setKeyframe(f"{cube}.translateX", t=1, v=0)
        cmds.setKeyframe(f"{cube}.translateX", t=10, v=5)
        out = os.path.join(self.tempdir, "anim_take.fbx")
        FbxUtils.export(out, objects=[cube], selection_only=True)

        cmds.file(new=True, force=True)
        FbxUtils.reset_import()  # must leave animation import enabled
        cmds.file(out, i=True, type="FBX", ignoreVersion=True)
        self.assertEqual(
            cmds.keyframe(f"{cube}.translateX", q=True, keyframeCount=True), 2
        )

    def _export_takeless(self, name: str) -> str:
        """Export *name* as a static FBX with NO animation takes.

        ``FBXExportBakeComplexAnimation`` etc. still leave the exporter
        writing a default "Take 001"; only the ``Animation`` include group
        yields a truly takeless file (the shape every static-asset export
        from a preset with animation disabled has). The exporter property is
        sticky, so it is restored afterward.
        """
        import maya.mel as mel

        out = os.path.join(self.tempdir, f"{name}.fbx")
        mel.eval("FBXProperty Export|IncludeGrp|Animation -v false")
        try:
            FbxUtils.export(out, objects=[name], selection_only=True)
        finally:
            mel.eval("FBXProperty Export|IncludeGrp|Animation -v true")
        # Prove the fixture is what the test needs: zero takes.
        mel.eval('FBXRead -f "{}"'.format(out.replace("\\", "/")))
        try:
            self.assertEqual(mel.eval("FBXGetTakeCount"), 0)
        finally:
            mel.eval("FBXClose")
        return out

    def test_import_scene_takeless_fbx(self):
        """A takeless FBX (any static-asset export) must still import.

        Regression (2026-08-17): ``reset_import`` re-selected the animation
        take by index (``FBXImportSetTake -ti 1``) to undo the factory "No
        Animation" state, but a fixed index is a hard requirement at import
        time — a file with zero takes aborts with ``FBXImport error: take
        not found`` and ``cmds.file`` returns NO nodes without raising, so
        every ``import_scene`` of a static FBX (the hierarchy-sync reference
        path, NamespaceSandbox) came back empty."""
        cube = cmds.polyCube(name="static_takeless_cube")[0]
        out = self._export_takeless(cube)
        cmds.file(new=True, force=True)

        new_nodes = FbxUtils.import_scene(out)
        self.assertTrue(new_nodes, "takeless FBX imported no nodes")
        self.assertTrue(cmds.objExists(cube))

    def test_reset_import_does_not_poison_takeless_raw_import(self):
        """Twin of the animation-take test: after ``reset_import`` a raw
        ``cmds.file(i=True)`` of a takeless FBX must import too — the take
        selector it leaves behind must fit ANY file, with or without takes."""
        cube = cmds.polyCube(name="static_raw_cube")[0]
        out = self._export_takeless(cube)
        cmds.file(new=True, force=True)

        FbxUtils.reset_import()
        cmds.file(out, i=True, type="FBX", ignoreVersion=True)
        self.assertTrue(cmds.objExists(cube), "takeless FBX dropped after reset_import")

    def test_reset_import_keeps_animation_of_multi_take_file(self):
        """With several takes, the selector ``reset_import`` leaves must
        still land animation (fresh-session parity: Maya imports the LAST
        take of the file, probed 2026-08-17 on Maya 2025 / FBX 2020.3.6)."""
        import maya.mel as mel

        cube = cmds.polyCube(name="multi_take_cube")[0]
        for t, v in ((1, 0), (10, 5), (20, 0), (30, -5)):
            cmds.setKeyframe(f"{cube}.translateX", t=t, v=v)
        out = os.path.join(self.tempdir, "multi_take.fbx")
        mel.eval('FBXExportSplitAnimationIntoTakes -v "TakeA" 1 10')
        mel.eval('FBXExportSplitAnimationIntoTakes -v "TakeB" 20 30')
        try:
            FbxUtils.export(out, objects=[cube], selection_only=True)
        finally:
            mel.eval("FBXExportSplitAnimationIntoTakes -c")
        cmds.file(new=True, force=True)

        FbxUtils.reset_import()
        cmds.file(out, i=True, type="FBX", ignoreVersion=True)
        times = cmds.keyframe(f"{cube}.translateX", q=True, timeChange=True) or []
        # Last take (TakeB, 20-30) — the same take a fresh session imports.
        self.assertEqual((min(times), max(times)), (20.0, 30.0), times)


class TestBakeRange(MayaTkTestCase):
    """``bake_range`` reads what the write will actually bake.

    Whoever describes the exported stack's ORIGIN needs this rather than
    ``playbackOptions``: the bake writes a key on every frame of the range, so
    its start is the stack's first key -- the frame a glTF converter rebases
    onto. Reading the playback range instead published an origin the file
    never had and slid every shot in a production deliverable by 33 frames.
    """

    def setUp(self):
        super().setUp()
        FbxUtils.load_plugin()
        self.addCleanup(mel.eval, "FBXResetExport")

    def test_it_reports_the_bake_range_not_the_playback_range(self):
        cmds.playbackOptions(
            animationStartTime=0, animationEndTime=500, minTime=0, maxTime=500
        )
        mel.eval("FBXExportBakeComplexAnimation -v true")
        mel.eval("FBXExportBakeComplexStart -v 33")
        mel.eval("FBXExportBakeComplexEnd -v 1989")

        self.assertEqual(FbxUtils.bake_range(), (33.0, 1989.0))

    def test_the_bake_flag_is_parsed_not_truth_tested(self):
        """The plugin answers this query with a STRING, and every non-empty
        string is truthy in Python.

        ``bool(mel.eval("FBXExportBakeComplexAnimation -q"))`` therefore read
        as True even when baking was OFF, which made
        ``set_bake_animation_range``'s skip branch unreachable and made the
        take-apply capture restore a bake flag the user had turned off as ON.
        """
        mel.eval("FBXExportBakeComplexAnimation -v false")
        raw = mel.eval("FBXExportBakeComplexAnimation -q")
        self.assertTrue(bool(raw), "the trap: the raw answer is truthy")
        self.assertFalse(FbxUtils.baking_enabled())

        mel.eval("FBXExportBakeComplexAnimation -v true")
        self.assertTrue(FbxUtils.baking_enabled())

    def test_no_range_when_the_write_will_not_bake(self):
        """Without a bake the file carries the scene's own keys instead."""
        mel.eval("FBXExportBakeComplexAnimation -v false")

        self.assertIsNone(FbxUtils.bake_range())

    def test_it_reads_back_what_the_scene_setter_wrote(self):
        """The getter's counterpart, so the pair cannot drift apart."""
        cmds.playbackOptions(
            animationStartTime=12, animationEndTime=340, minTime=12, maxTime=340
        )
        mel.eval("FBXExportBakeComplexAnimation -v true")
        wrote = FbxUtils.set_bake_range_from_scene()

        self.assertEqual(FbxUtils.bake_range(), wrote)


class TestApplyTakesGuaranteesAnimation(MayaTkTestCase):
    """``apply_takes`` must make its own bake override MEAN something.

    Measured on a production assembly (2026-08-30): the panel's FBX preset
    (``game_export``) carries ``Export|IncludeGrp|Animation`` OFF, a preset
    bypasses the exporter's default-pinning entirely, and with that include
    group off the plugin writes ZERO AnimationStacks no matter what the bake
    flags say. So ``apply_takes`` configured 12 takes, logged that it had, and
    the 70 MB deliverable shipped with no animation while its own handoff
    declared 12 shots. Proven by export, not by reading the flag back: the
    flag being on is not the claim -- the file carrying takes is.
    """

    def setUp(self):
        super().setUp()
        FbxUtils.load_plugin()
        self.tempdir = tempfile.mkdtemp(prefix="fbx_takes_test_")
        self.cube = cmds.polyCube(name="TAKES_CUBE")[0]
        cmds.setKeyframe(f"{self.cube}.translateX", t=1, v=0)
        cmds.setKeyframe(f"{self.cube}.translateX", t=30, v=10)

    def tearDown(self):
        import maya.mel as mel

        FbxUtils.reset_takes()
        FbxUtils.set_animation_export(True)
        mel.eval("FBXExportSplitAnimationIntoTakes -c")
        for f in os.listdir(self.tempdir):
            try:
                os.remove(os.path.join(self.tempdir, f))
            except Exception:
                pass
        try:
            os.rmdir(self.tempdir)
        except Exception:
            pass
        super().tearDown()

    def _stacks(self, name):
        """AnimationStack count of an export of the keyed cube."""
        out = os.path.join(self.tempdir, f"{name}.fbx")
        cmds.select(self.cube, replace=True)
        with FbxUtils.embed_media_write_cwd():
            cmds.file(out, force=True, options="v=0;", type="FBX export", es=True)
        return open(out, "rb").read().count(b"AnimationStack")

    def test_a_preset_that_excludes_animation_cannot_void_the_declared_takes(self):
        """The production failure, end to end."""
        FbxUtils.set_animation_export(False)  # what the preset did
        self.assertEqual(self._stacks("before"), 0, "fixture is not the failing state")

        FbxUtils.apply_takes([{"name": "T1", "start": 1, "end": 10}])

        self.assertTrue(FbxUtils.animation_export_enabled())
        self.assertGreater(self._stacks("after"), 0, "takes still ship no animation")

    def test_the_users_setting_is_restored_afterwards(self):
        """It is sticky global state: the repair may not outlive the export."""
        FbxUtils.set_animation_export(False)
        FbxUtils.apply_takes([{"name": "T1", "start": 1, "end": 10}])
        self.assertTrue(FbxUtils.animation_export_enabled())

        FbxUtils.reset_takes()

        self.assertFalse(
            FbxUtils.animation_export_enabled(),
            "reset_takes left the include group flipped for every later export",
        )

    def test_an_export_that_already_includes_animation_is_left_alone(self):
        """No flip, no warning, and the user's ON stays ON through the restore."""
        FbxUtils.set_animation_export(True)
        FbxUtils.apply_takes([{"name": "T1", "start": 1, "end": 10}])
        FbxUtils.reset_takes()
        self.assertTrue(FbxUtils.animation_export_enabled())


class TestApplyTakesShipsEveryChannel(MayaTkTestCase):
    """Every declared take carries every animated channel across its window.

    Maya's ``FBXExportSplitAnimationIntoTakes`` restricts each curve to a
    take's window before writing it, so a curve with no key inside a take
    contributes NO channel to it -- the node plays its rest pose for that whole
    shot -- and a take's keys stop at its last in-window key rather than at its
    end. Unity plays those takes as the shot clips: measured in Unity 6000.3, a
    50-frame shot imported 40 frames long with two of its three nodes frozen,
    and the shot after it as a clip with no curves at all. Only the
    whole-timeline take was right. With every curve resampled
    (``FBXExportBakeResampleAnimation``) each take is an exact slice, which is
    what :meth:`FbxUtils.apply_takes` arms. Proven on the written file.
    """

    def setUp(self):
        super().setUp()
        FbxUtils.load_plugin()
        self.artifacts = ptk.TempArtifacts("fbx_take_channels", policy="scoped")
        self.addCleanup(self.artifacts.cleanup)
        door = cmds.polyCube(name="DOOR")[0]
        cmds.setKeyframe(f"{door}.translateY", t=1, v=0)
        cmds.setKeyframe(f"{door}.translateY", t=100, v=5)
        # Keyed only inside A01, so A02 is where a lossy split shows.
        lift = cmds.polyCube(name="LIFT")[0]
        cmds.setKeyframe(f"{lift}.translateY", t=10, v=0)
        cmds.setKeyframe(f"{lift}.translateY", t=40, v=10)
        blinker = cmds.polyCube(name="BLINKER")[0]
        cmds.setKeyframe(f"{blinker}.visibility", t=20, v=1)
        cmds.setKeyframe(f"{blinker}.visibility", t=30, v=0)
        self.nodes = [door, lift, blinker]
        self.takes = [
            {"name": "A01", "start": 1, "end": 50},
            {"name": "A02", "start": 51, "end": 100},
        ]

    def tearDown(self):
        FbxUtils.reset_takes()
        super().tearDown()

    def _export(self, resample=True):
        path = os.path.join(self.artifacts.dir_path(), "takes.fbx")
        mel.eval("FBXExportInAscii -v false")  # read back by the binary reader
        FbxUtils.apply_takes(self.takes, resample=resample)
        cmds.select(self.nodes, replace=True)
        cmds.file(path, force=True, options="v=0;", type="FBX export", es=True)
        return ptk.FbxFile.load(path, decode_arrays=("KeyTime",), raw_payloads=False)

    def test_every_take_carries_every_channel_over_its_window(self):
        curves = self._export().take_curves()
        tick = ptk.FbxFile.TICKS_PER_SECOND / 24.0
        whole = set(curves["Take 001"])
        self.assertTrue(whole, "the whole-timeline take animates nothing")
        compared = 0
        for take in self.takes:
            name = take["name"]
            missing = sorted(whole - set(curves.get(name, {})))
            self.assertEqual(missing, [], f"{name} lost these channels to the split")
            for key, (first, last, count) in curves[name].items():
                self.assertEqual(
                    (round(first / tick), round(last / tick)),
                    (take["start"], take["end"]),
                    f"{name} {key} does not span the take",
                )
                compared += 1
        # 2 takes x (3 nodes' animated channels): nothing compared is no pass.
        self.assertGreaterEqual(compared, 2 * len(self.nodes))

    def test_reset_takes_restores_the_resample_flag(self):
        """Sticky global state: the export's resample may not outlive it."""
        mel.eval("FBXExportBakeResampleAnimation -v false")
        FbxUtils.apply_takes(self.takes)
        self.assertTrue(FbxUtils.export_flag("FBXExportBakeResampleAnimation"))

        FbxUtils.reset_takes()

        self.assertFalse(
            FbxUtils.export_flag("FBXExportBakeResampleAnimation"),
            "reset_takes left Resample All on for every later export",
        )

    def test_resample_off_overrules_a_preset_that_resamples(self):
        """``resample=False`` is the write's decision, not a request: a loaded
        preset with Resample All on kept it on, so a GLB-only run's
        intermediate opened its whole-timeline take on the bake range while
        the published clip origin stayed on the keys. Off for the write -- the
        authored keys ship -- and the preset's ON back after it."""
        prior = FbxUtils.export_flag(FbxUtils.RESAMPLE_ALL)
        self.addCleanup(
            mel.eval, f"{FbxUtils.RESAMPLE_ALL} -v {'true' if prior else 'false'}"
        )
        mel.eval(f"{FbxUtils.RESAMPLE_ALL} -v true")  # what the preset did

        curves = self._export(resample=False).take_curves()["Take 001"]

        tick = ptk.FbxFile.TICKS_PER_SECOND / 24.0
        lift = [span[0] for key, span in curves.items() if key[0] == "LIFT"]
        self.assertTrue(lift, "the whole-timeline take does not animate LIFT")
        self.assertEqual(
            min(round(first / tick) for first in lift),
            10,
            "LIFT's curve was resampled from the bake range, not shipped as keyed",
        )
        FbxUtils.reset_takes()
        self.assertTrue(
            FbxUtils.export_flag(FbxUtils.RESAMPLE_ALL),
            "the preset's Resample All was not given back after the write",
        )


class TestSessionHookWarnsOfAnAsciiCarrier(MayaTkTestCase):
    """A File > Export writes in the format its dialog chose; the Scene
    Exporter's binary pin (``SceneExporter._pin_carrier_requirements``) never
    reaches it. Unity's ASCII FBX reader loses a carrier channel past ~6 KB,
    so a write in ASCII from a scene holding ``data_export`` -- the carrier
    the hook publishes -- is warned about (not overruled: the hook cannot tell
    whether this write ships the carrier at all)."""

    LOGGER = "mayatk.env_utils.fbx_utils"

    def setUp(self):
        super().setUp()
        from mayatk.node_utils.data_nodes import DataNodes

        self.data_nodes = DataNodes
        FbxUtils.load_plugin()
        ascii_ = FbxUtils.export_flag("FBXExportInAscii")
        self.addCleanup(
            mel.eval, f"FBXExportInAscii -v {'true' if ascii_ else 'false'}"
        )
        self.addCleanup(FbxUtils.reset_takes)

    def _before_export(self, ascii_):
        mel.eval(f"FBXExportInAscii -v {'true' if ascii_ else 'false'}")
        FbxUtils._on_before_export()

    def test_an_ascii_write_of_a_scene_with_the_carrier_is_warned(self):
        self.data_nodes.get_export_node(create=True)
        with self.assertLogs(self.LOGGER, "WARNING") as logs:
            self._before_export(ascii_=True)
        said = " ".join(logs.output)
        for word in ("ASCII", self.data_nodes.EXPORT, "Unity", "binary"):
            self.assertIn(word, said)

    def test_a_binary_write_says_nothing(self):
        self.data_nodes.get_export_node(create=True)
        with self.assertNoLogs(self.LOGGER, "WARNING"):
            self._before_export(ascii_=False)


class _TakeFilesCase(MayaTkTestCase):
    """FBX files whose animation Maya's own exporter splits across takes.

    Each is written as the Scene Exporter writes one -- ``FbxUtils.apply_takes``
    then ``FbxUtils.export`` -- and a Shots Only file is that file less its
    whole-timeline take (``ptk.FbxMedia.drop_takes``, what the exporter's
    ``ship_declared_takes`` does to it).
    """

    #: A cube's translateX keys; Maya's default (spline) tangents.
    KEYS = ((1, 0.0), (10, 5.0), (40, -3.0), (60, 8.0), (90, 2.0), (100, 10.0))
    EARLY_LATE = (
        {"name": "early", "start": 1, "end": 30},
        {"name": "late", "start": 50, "end": 70},
    )
    TEXTURE = os.path.join(
        os.path.dirname(__file__), "test_assets", "model_BaseColor.png"
    )
    LOGGER = "mayatk.env_utils.fbx_utils"

    def setUp(self):
        super().setUp()
        FbxUtils.load_plugin()
        ascii_ = FbxUtils.export_flag("FBXExportInAscii")
        self.addCleanup(
            mel.eval, f"FBXExportInAscii -v {'true' if ascii_ else 'false'}"
        )
        mel.eval("FBXExportInAscii -v false")
        embed = FbxUtils.export_flag("FBXExportEmbeddedTextures")  # sticky
        self.addCleanup(
            mel.eval, f"FBXExportEmbeddedTextures -v {'true' if embed else 'false'}"
        )
        self.artifacts = ptk.TempArtifacts("fbx_import_takes", policy="scoped")
        self.addCleanup(self.artifacts.cleanup)
        self.folder = self.artifacts.dir_path()

    def tearDown(self):
        FbxUtils.reset_takes()
        super().tearDown()

    def _cube(self, name="CUBE", keys=KEYS):
        cube = cmds.polyCube(name=name)[0]
        for t, v in keys:
            cmds.setKeyframe(f"{cube}.translateX", t=t, v=v)
        return cube

    def _write(
        self,
        name,
        nodes,
        takes,
        resample=True,
        drop=("Take 001",),
        folder=None,
        **options,
    ):
        path = os.path.join(folder or self.folder, f"{name}.fbx")
        FbxUtils.apply_takes(takes, resample=resample)
        try:
            FbxUtils.export(path, objects=nodes, options=options or None)
        finally:
            FbxUtils.reset_takes()
        if drop:
            ptk.FbxMedia.drop_takes(path, names=list(drop))
        return path

    @staticmethod
    def _sample(plug, frames):
        return {f: cmds.getAttr(plug, time=f) for f in frames}

    @staticmethod
    def _keys(plug):
        return cmds.keyframe(plug, query=True, timeChange=True) or []

    @staticmethod
    def _curve_keys(curve_or_plug):
        times = cmds.keyframe(curve_or_plug, query=True, timeChange=True) or []
        values = cmds.keyframe(curve_or_plug, query=True, valueChange=True) or []
        return [(round(t, 4), round(v, 4)) for t, v in zip(times, values)]

    def _assert_plays(self, plug, expected, tolerance=1e-3):
        self.assertTrue(expected, "nothing sampled is no pass")
        wrong = {}
        for frame, value in expected.items():
            got = cmds.getAttr(plug, time=frame)
            if abs(got - value) > tolerance:
                wrong[frame] = (value, got)
        self.assertEqual(
            wrong, {}, f"{plug}: {len(wrong)} of {len(expected)} frames off the source"
        )

    @staticmethod
    def _textured_cube(texture, name="TEX_CUBE"):
        cube = cmds.polyCube(name=name)[0]
        for t, v in ((1, 0.0), (10, 5.0), (40, -3.0), (60, 8.0)):
            cmds.setKeyframe(f"{cube}.translateX", t=t, v=v)
        shader = cmds.shadingNode("lambert", asShader=True, name="texMat")
        tex = cmds.shadingNode("file", asTexture=True, name="texFile")
        cmds.setAttr(f"{tex}.fileTextureName", texture, type="string")
        cmds.connectAttr(f"{tex}.outColor", f"{shader}.color")
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="texSG")
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader")
        cmds.sets(cube, edit=True, forceElement=sg)
        return cube

    @staticmethod
    def _texture_paths():
        return [
            os.path.normcase(os.path.normpath(cmds.getAttr(f"{n}.fileTextureName")))
            for n in cmds.ls(type="file")
        ]


class TestImportSceneImportsEveryTake(_TakeFilesCase):
    """``import_scene`` lands ALL of a multi-take file's animation.

    Maya plays the one take ``FBXImportSetTake`` names (``reset_import``'s
    ``-1``: the LAST), so a Shots Only file imported its last shot alone -- a
    cube keyed 1-100 and split 1-30 / 50-70 came back as 21 keys, 50-70 -- and
    a file whose whole timeline is its FIRST take (Maya's ``Take 001``, a
    Blender push's scene stack) imported one slice of it. No importer flag
    merges takes. ``ptk.FbxFile.take_layout`` sorts them: a covering take is
    imported alone, takes that agree are merged into one
    (``ptk.FbxMedia.merge_takes``, written to a scoped copy), and a take that
    disagrees is parked in Key Stash (:class:`TestImportSceneParksAConflictingTake`).
    """

    def test_a_shots_only_file_imports_every_shot(self):
        cube = self._cube()
        expected = self._sample(f"{cube}.translateX", [*range(1, 31), *range(50, 71)])
        path = self._write("shots", [cube], self.EARLY_LATE)
        cmds.file(new=True, force=True)

        FbxUtils.import_scene(path)

        self._assert_plays("CUBE.translateX", expected)
        self.assertEqual(len(self._keys("CUBE.translateX")), 51)

    def test_a_seam_two_shots_share_is_imported_once(self):
        cube = self._cube()
        expected = self._sample(f"{cube}.translateX", range(1, 61))
        takes = (
            {"name": "early", "start": 1, "end": 30},
            {"name": "late", "start": 30, "end": 60},
        )
        path = self._write("seam", [cube], takes)
        cmds.file(new=True, force=True)

        FbxUtils.import_scene(path)

        self._assert_plays("CUBE.translateX", expected)
        self.assertEqual(len(self._keys("CUBE.translateX")), 60)

    def test_a_whole_timeline_take_is_imported_whole(self):
        """Maya writes ``Take 001`` first and its shot slices after it; the
        last take -- what a plain import plays -- is one slice. The authored
        keys past the shots (90, 100) are only in ``Take 001``."""
        cube = self._cube()
        expected = self._sample(f"{cube}.translateX", [*range(1, 71), 90, 100])
        path = self._write("both", [cube], self.EARLY_LATE, drop=())
        cmds.file(new=True, force=True)

        FbxUtils.import_scene(path)

        self._assert_plays("CUBE.translateX", expected)
        self.assertIn(100.0, self._keys("CUBE.translateX"))

    def test_a_node_only_a_later_take_animates_keeps_its_keys(self):
        """Without Resample All a take carries only the curves keyed inside
        it, so each cube here is animated in ONE take: the merge re-wires the
        later take's curve onto the take it keeps."""
        early = self._cube("EARLY_CUBE", ((1, 0.0), (10, 5.0)))
        late = self._cube("LATE_CUBE", ((55, 0.0), (65, 4.0)))
        path = self._write("split", [early, late], self.EARLY_LATE, resample=False)
        cmds.file(new=True, force=True)

        FbxUtils.import_scene(path)

        self.assertEqual(self._curve_keys("EARLY_CUBE.translateX"), [(1, 0), (10, 5)])
        self.assertEqual(self._curve_keys("LATE_CUBE.translateX"), [(55, 0), (65, 4)])

    def test_an_ascii_file_imports_its_covering_take(self):
        """pythontk reads binary FBX only, so an ASCII file is judged by the
        spans the importer itself reports: the take covering every other is
        imported, not the last slice."""
        cube = self._cube()
        expected = self._sample(f"{cube}.translateX", [*range(1, 71), 90, 100])
        mel.eval("FBXExportInAscii -v true")
        try:
            path = self._write("both_ascii", [cube], self.EARLY_LATE, drop=())
        finally:
            mel.eval("FBXExportInAscii -v false")
        self.assertFalse(ptk.FbxFile.is_fbx(path), "the fixture must be ASCII")
        cmds.file(new=True, force=True)

        FbxUtils.import_scene(path)

        self._assert_plays("CUBE.translateX", expected)

    def test_the_take_selector_is_left_per_file(self):
        """The importer's take selector is sticky: the take an import pins
        would shape every later raw import. ``reset_import``'s per-file ``-1``
        must be back afterwards (pin: a raw import of a two-take file plays
        its last take, as a fresh session does)."""
        cube = self._cube()
        both = self._write("both", [cube], self.EARLY_LATE, drop=())
        shots = self._write("shots", [cube], self.EARLY_LATE)
        cmds.file(new=True, force=True)
        FbxUtils.import_scene(both)  # covered: its FIRST take is pinned
        cmds.file(new=True, force=True)

        cmds.file(shots, i=True, type="FBX", ignoreVersion=True)

        times = self._keys("CUBE.translateX")
        self.assertEqual((min(times), max(times)), (50.0, 70.0), times)


class TestImportSceneKeepsAMergedImportsFiles(_TakeFilesCase):
    """A merged file is imported from a scoped COPY, and Maya's FBX importer
    places and finds files relative to the file it reads: embedded media is
    extracted to ``<fbx folder>/<fbx name>.fbm`` and a texture whose recorded
    path is gone resolves its relative path against the FBX's folder
    (measured, Maya 2025: a copy elsewhere left the texture nodes on a folder
    that is deleted, or on the stale absolute path). The import of the copy
    must leave the scene as an import of the file itself does."""

    def test_embedded_media_lands_beside_the_source(self):
        cube = self._textured_cube(self.TEXTURE)
        src = os.path.join(self.folder, "src")
        os.makedirs(src)
        path = self._write(
            "textured",
            [cube],
            self.EARLY_LATE,
            folder=src,
            FBXExportEmbeddedTextures=True,
        )
        self.assertTrue(ptk.FbxMedia.embedded(path), "the fixture must embed")
        cmds.file(new=True, force=True)

        FbxUtils.import_scene(path)

        expected = os.path.join(src, "textured.fbm", os.path.basename(self.TEXTURE))
        self.assertEqual(self._texture_paths(), [os.path.normcase(expected)])
        self.assertTrue(os.path.isfile(expected))

    def test_media_that_cannot_land_beside_the_source_goes_to_the_project(self):
        """A read-only delivery folder: the media went to a temp folder the
        sweep reclaims, and a saved scene's file nodes named it (TEMP-REF,
        file-handling audit 2026-10-13). It goes to the open scene's project,
        ``<sourceimages>/<source stem>`` -- the one folder a source's media
        takes (``FbxUtils._source_media_dir``)."""
        from unittest import mock

        from mayatk.node_utils.data_nodes import DataNodes

        cube = self._textured_cube(self.TEXTURE)
        src = os.path.join(self.folder, "src")
        os.makedirs(src)
        path = self._write(
            "textured",
            [cube],
            self.EARLY_LATE,
            folder=src,
            FBXExportEmbeddedTextures=True,
        )
        # A FILE where the media folder would go: it cannot be written there.
        with open(os.path.join(src, "textured.fbm"), "w") as fh:
            fh.write("in the way")
        project = os.path.join(self.folder, "project")
        os.makedirs(project)
        cmds.file(new=True, force=True)

        with mock.patch.object(
            DataNodes, "scene_workspace", return_value=ptk.Workspace.load(project)
        ):
            FbxUtils.import_scene(path)

        expected = os.path.join(
            project, "sourceimages", "textured", os.path.basename(self.TEXTURE)
        )
        self.assertEqual(self._texture_paths(), [os.path.normcase(expected)])
        self.assertTrue(os.path.isfile(expected))

    def test_a_relative_texture_resolves_against_the_source_folder(self):
        src = os.path.join(self.folder, "src")
        os.makedirs(os.path.join(src, "textures"))
        texture = os.path.join(src, "textures", "a.png")
        shutil.copy(self.TEXTURE, texture)
        cube = self._textured_cube(texture)
        self._write(
            "relative",
            [cube],
            self.EARLY_LATE,
            folder=src,
            FBXExportEmbeddedTextures=False,
        )
        moved = os.path.join(self.folder, "moved")
        # The recorded absolute path is gone; the relative one is not.
        shutil.move(src, moved)
        cmds.file(new=True, force=True)

        FbxUtils.import_scene(os.path.join(moved, "relative.fbx"))

        expected = os.path.join(moved, "textures", "a.png")
        self.assertEqual(self._texture_paths(), [os.path.normcase(expected)])


def _reset_key_stash():
    """Forget the active Key Stash AND its backend (``test_key_stash``'s reset)."""
    from mayatk.anim_utils.key_stash._key_stash import KeyStash

    backend = KeyStash._persistence
    if backend is not None and hasattr(backend, "remove_callbacks"):
        backend.remove_callbacks()
    KeyStash._active = None
    KeyStash.set_persistence(None)


class TestImportSceneParksAConflictingTake(_TakeFilesCase):
    """Takes that key the same channel over the same frames differently are
    alternatives -- a clip library -- and no single curve holds both. The
    file's current take plays; every other is parked WHOLE as a Key Stash
    clip named after it: export-inert, previewable, retrievable onto the
    imported objects.
    """

    def setUp(self):
        super().setUp()
        _reset_key_stash()
        self.addCleanup(_reset_key_stash)

    def _clip_library(self):
        """``walk`` and ``run`` keying CLIP.translateX over frames 1-30.

        Maya's exporter cannot write one -- every take it writes is a window
        of one timeline -- so ``run`` is keyed at 101-130, written without
        Resample All (two keys a take, stored uncompressed) and its times are
        moved onto 1-30 in place: same-size int64 swaps, every offset kept.
        """
        clip = cmds.polyCube(name="CLIP")[0]
        for t, v in ((1, 0.0), (30, 10.0), (101, 5.0), (130, -5.0)):
            cmds.setKeyframe(f"{clip}.translateX", t=t, v=v)
        cmds.keyTangent(
            f"{clip}.translateX", inTangentType="linear", outTangentType="linear"
        )
        takes = (
            {"name": "walk", "start": 1, "end": 30},
            {"name": "run", "start": 101, "end": 130},
        )
        path = self._write("clips", [clip], takes, resample=False)
        tick = ptk.FbxFile.TICKS_PER_SECOND // 24
        with open(path, "rb") as fh:
            data = fh.read()
        for old, new in ((101, 1), (130, 30)):
            old, new = struct.pack("<q", old * tick), struct.pack("<q", new * tick)
            self.assertTrue(data.count(old), "no key time to move")
            data = data.replace(old, new)
        with open(path, "wb") as fh:
            fh.write(data)
        layout = ptk.FbxFile.load(
            path, decode_arrays=ptk.FbxFile.CURVE_ARRAYS, raw_payloads=False
        ).take_layout()
        self.assertEqual(
            (layout["kind"], layout["live"], layout["stash"]),
            ("conflicted", ["walk"], ["run"]),
        )
        return path

    def _take_alone(self, path, index):
        """What the importer makes of take *index* on its own (the oracle)."""
        cmds.file(new=True, force=True)
        FbxUtils.reset_import()
        mel.eval("FBXImportMode -v add")
        mel.eval(f"FBXImportSetTake -ti {index}")
        try:
            cmds.file(path, i=True, type="FBX", ignoreVersion=True)
        finally:
            mel.eval("FBXImportSetTake -ti -1")
        keys = self._curve_keys("CLIP.translateX")
        cmds.file(new=True, force=True)
        _reset_key_stash()
        return keys

    def test_the_current_take_plays_and_the_other_is_parked_whole(self):
        from mayatk.anim_utils.key_stash._key_stash import KeyStash
        from mayatk.anim_utils.smart_bake.bake_session import BakeSessionStore

        path = self._clip_library()
        walk, run = self._take_alone(path, 1), self._take_alone(path, 2)
        self.assertEqual(len(run), 2)
        self.assertNotEqual(walk, run)

        FbxUtils.import_scene(path)

        self.assertEqual(self._curve_keys("CLIP.translateX"), walk)
        store = KeyStash.active()
        self.assertEqual([c.label for c in store.clips], ["run"])
        clip = store.clips[0]
        self.assertEqual(
            clip.metadata, {"source": "fbx", "path": "clips.fbx", "take": "run"}
        )
        parked = [BakeSessionStore.resolve_ref(rec["stash"]) for rec in clip.curves]
        self.assertEqual([self._curve_keys(n) for n in parked], [run])
        # The take's own import is gone: no namespace, no second cube.
        self.assertEqual(cmds.ls("CLIP*", type="transform"), ["CLIP"])
        self.assertEqual(
            [
                n
                for n in cmds.namespaceInfo(":", listOnlyNamespaces=True) or []
                if "take" in n.lower()
            ],
            [],
        )
        # The record names the live curve, as a stash of the live animation
        # would: the retrieve pastes onto it and leaves its name alone.
        live = cmds.listConnections("CLIP.translateX", source=True, type="animCurve")
        store.retrieve(clip.clip_id, mode="replace")
        self.assertEqual(self._curve_keys("CLIP.translateX"), run)
        self.assertEqual(
            cmds.listConnections("CLIP.translateX", source=True, type="animCurve"),
            live,
        )

    def test_a_failed_merge_plays_a_live_take_never_a_parked_one(self):
        """Bug: when the live takes could not be merged the import fell back
        to the importer's own pick, the file's LAST take -- here the one the
        layout parks -- so the alternative played live and was parked too.
        Fixed: 2026-10-12"""
        from unittest.mock import patch

        path = self._clip_library()
        walk = self._take_alone(path, 1)
        fbx, layout = FbxUtils._take_layout(path, logging.getLogger(__name__))
        layout = dict(layout, live=["walk", "walk_too"])  # two takes to merge
        with (
            patch.object(FbxUtils, "_take_layout", return_value=(fbx, layout)),
            patch.object(ptk.FbxMedia, "merge_takes", side_effect=ValueError("no")),
        ):
            FbxUtils.import_scene(path)

        self.assertEqual(self._curve_keys("CLIP.translateX"), walk)

    def test_an_import_that_opts_out_names_the_takes_it_left_out(self):
        """A sandbox import that is deleted afterwards (hierarchy sync's
        compare) must not write Key Stash records into the user's scene: it
        passes ``stash=False`` and is told what it did not get."""
        from mayatk.anim_utils.key_stash._key_stash import KeyStash

        path = self._clip_library()
        walk = self._take_alone(path, 1)

        logger = logging.getLogger("fbx_take_opt_out")  # the caller's own log
        with self.assertLogs(logger, "WARNING") as logs:
            FbxUtils.import_scene(path, stash=False, logger=logger)

        self.assertIn("'run'", " ".join(logs.output))
        self.assertEqual(self._curve_keys("CLIP.translateX"), walk)
        self.assertTrue(KeyStash.active().is_empty())


class TestNativeReadGap(_TakeFilesCase):
    """What Maya's OWN reader of a file misses that ``import_scene`` imports.

    A reference re-reads its file through the FBX plugin at every load, reload
    and scene open, and plays the ONE take the session's sticky
    ``FBXImportSetTake`` names -- never a merge. ``-1`` (the last take) is the
    selector a session keeps, so a file reads whole natively only when its last
    take is the one the import plays and nothing is parked; the Reference
    Manager bakes any other (BACKLOG 2026-10-10: a Shots Only reference played
    its last shot alone).
    """

    LOG = logging.getLogger(__name__)

    def test_a_file_its_last_take_reads_whole_has_no_gap(self):
        cube = self._cube()
        whole = {"name": "whole", "start": 1, "end": 100}
        cases = {
            "one take": self._write("single", [cube], (whole,)),
            "a cover listed last": self._write(
                "cover_last", [cube], (*self.EARLY_LATE, whole)
            ),
        }
        for case, path in cases.items():
            with self.subTest(case=case):
                self.assertIsNone(FbxUtils._native_read_gap(path, self.LOG))

    def test_a_file_no_one_take_reads_whole_names_what_is_missed(self):
        cube = self._cube()
        cases = {
            "merged": self._write("shots", [cube], self.EARLY_LATE),
            "covered": self._write("both", [cube], self.EARLY_LATE, drop=()),
        }
        for kind, path in cases.items():
            with self.subTest(kind=kind):
                gap = FbxUtils._native_read_gap(path, self.LOG)
                self.assertTrue(gap and gap.startswith(f"{kind}:"), gap)

    def test_a_parked_take_is_a_gap(self):
        path = TestImportSceneParksAConflictingTake._clip_library(self)

        gap = FbxUtils._native_read_gap(path, self.LOG)

        self.assertTrue(gap and gap.startswith("conflicted:"), gap)
        self.assertIn("'run'", gap)

    def test_an_ascii_file_is_judged_by_the_importer_s_own_spans(self):
        """pythontk reads binary FBX only, so an ASCII file is judged by the
        spans the importer reports: its whole-timeline take, listed FIRST, is
        not the last take a native read plays."""
        cube = self._cube()
        mel.eval("FBXExportInAscii -v true")
        try:
            path = self._write("both_ascii", [cube], self.EARLY_LATE, drop=())
        finally:
            mel.eval("FBXExportInAscii -v false")
        self.assertFalse(ptk.FbxFile.is_fbx(path), "the fixture must be ASCII")

        gap = FbxUtils._native_read_gap(path, self.LOG)

        self.assertIn("'Take 001'", gap or "")


class TestNativeReadMemo(_TakeFilesCase):
    """``_native_read`` reads a version of a file's takes once: a large FBX's
    take layout takes seconds (~19 s measured on a 133 MB, 13-take file), and
    every add, Open and ``reference_scene`` read it again."""

    LOG = logging.getLogger(__name__)

    def test_a_version_of_a_file_is_read_once(self):
        from unittest.mock import patch

        path = self._write("shots", [self._cube()], self.EARLY_LATE)
        real = ptk.FbxFile.load
        with patch.object(ptk.FbxFile, "load", side_effect=real) as load:
            first = FbxUtils._native_read(path, self.LOG)
            reads = load.call_count
            self.assertEqual(FbxUtils._native_read(path, self.LOG), first)
            self.assertEqual(load.call_count, reads, "an unchanged file was read again")
            stat = os.stat(path)  # a re-export: a new version of the file
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
            self.assertEqual(FbxUtils._native_read(path, self.LOG), first)
            self.assertGreater(load.call_count, reads, "a changed file was not read")
        self.assertTrue(first and first.startswith("merged:"), first)


class TestWritePlan(MayaTkTestCase):
    """``FbxUtils.write_plan`` -- THE plan of one write: bakes, why, over what,
    resampled -- and its application, the bake flag FIRST: any write of
    ``FBXExportBakeComplexAnimation`` (on, off, or the value it holds) resets
    the bake range to the scene's animation range (measured on Maya 2025 /
    FBX 2020.3.6), so a range set before the flag is lost.
    Added: 2026-10-17
    """

    def setUp(self):
        super().setUp()
        FbxUtils.reset_export()
        self.addCleanup(FbxUtils.reset_export)
        cmds.playbackOptions(min=5, max=60, ast=2, aet=310)

    def test_a_flag_write_resets_the_range_so_the_plan_writes_it_second(self):
        mel.eval("FBXExportBakeComplexAnimation -v true")
        mel.eval("FBXExportBakeComplexStart -v 7")
        mel.eval("FBXExportBakeComplexEnd -v 9")
        mel.eval("FBXExportBakeComplexAnimation -v true")
        self.assertEqual(FbxUtils.bake_range(), (2.0, 310.0), "the trap is gone?")

        FbxUtils.apply_write_plan(FbxUtils.write_plan(takes=(7, 9)))

        self.assertEqual(FbxUtils.bake_range(), (7.0, 9.0))
        self.assertTrue(FbxUtils.export_flag(FbxUtils.RESAMPLE_ALL))

    def test_the_settings_decide_when_the_writer_does_not(self):
        plan = FbxUtils.write_plan()
        self.assertFalse(plan.bakes)
        self.assertIsNone(plan.range)

        mel.eval("FBXExportBakeComplexAnimation -v true")
        mel.eval("FBXExportBakeComplexStart -v 11")
        mel.eval("FBXExportBakeComplexEnd -v 22")
        plan = FbxUtils.write_plan()
        self.assertEqual(
            (plan.bakes, plan.range, plan.resample_all), (True, (11.0, 22.0), False)
        )

    def test_a_split_bakes_over_at_least_its_takes(self):
        plan = FbxUtils.write_plan(bakes=False, takes=(11, 40), span=(1, 30))
        self.assertEqual(
            (plan.bakes, plan.range, plan.resample_all), (True, (1, 40), True)
        )
        self.assertFalse(
            FbxUtils.write_plan(takes=(11, 40), resample_all=False).resample_all
        )

    def test_a_bracket_publishes_against_its_plan(self):
        """A producer inside the bracket reads the range the write will bake,
        not what the settings held when the bracket opened."""
        with FbxUtils.export_prepared(
            plan=FbxUtils.write_plan(bakes=True, span=(4, 44))
        ):
            self.assertEqual(FbxUtils.planned_bake_range(), (4, 44))
        with FbxUtils.export_prepared(plan=FbxUtils.write_plan(bakes=False)):
            self.assertIsNone(FbxUtils.planned_bake_range())
        mel.eval("FBXExportBakeComplexAnimation -v true")
        self.assertEqual(FbxUtils.planned_bake_range(), FbxUtils.bake_range())


class TestOffsetsFolded(MayaTkTestCase):
    """An FBX write that does not bake ships each offsetParentMatrix folded into
    its node's own channels (``FbxUtils.offsets_folded``, inside
    ``FbxUtils.export``) -- a static node as plain values, no key -- and hands
    the scene back. Unfolded, the plug-in drops the offset: a static
    translate-3 offset shipped at world 0 (Maya 2025 / FBX 2020.3.6).
    Added: 2026-10-17
    """

    def setUp(self):
        super().setUp()
        store = ptk.TempArtifacts("fbx_offsets_folded", policy="scoped")
        self.addCleanup(store.cleanup)
        self.out = store.dir_path()
        FbxUtils.reset_export()
        self.addCleanup(FbxUtils.reset_export)

    def _offset_cube(self, name="offset_cube"):
        import math
        import maya.api.OpenMaya as om2

        cube = cmds.ls(cmds.polyCube(name=name)[0], long=True)[0]
        xform = om2.MTransformationMatrix()
        xform.setTranslation(om2.MVector(3, 0, 1), om2.MSpace.kTransform)
        xform.setRotation(om2.MEulerRotation(0, math.radians(30), 0))
        cmds.setAttr(
            f"{cube}.offsetParentMatrix", list(xform.asMatrix()), type="matrix"
        )
        return cube

    def test_an_unbaked_write_ships_a_static_offset_exactly_and_keyless(self):
        cube = self._offset_cube()
        world = cmds.getAttr(f"{cube}.worldMatrix[0]")
        offset = cmds.getAttr(f"{cube}.offsetParentMatrix")
        path = os.path.join(self.out, "static_offset.fbx")

        FbxUtils.export(path, [cube])

        self.assertEqual(cmds.getAttr(f"{cube}.offsetParentMatrix"), offset)
        cmds.file(new=True, force=True)
        cmds.file(path, i=True, type="FBX", ignoreVersion=True)
        (shipped,) = cmds.ls("offset_cube", long=True, type="transform")
        got = cmds.getAttr(f"{shipped}.worldMatrix[0]")
        self.assertLess(max(abs(a - b) for a, b in zip(world, got)), 1e-4)
        self.assertFalse(cmds.keyframe(shipped, query=True, keyframeCount=True))

    def test_a_write_that_bakes_folds_nothing(self):
        cube = self._offset_cube()
        offset = cmds.getAttr(f"{cube}.offsetParentMatrix")

        with FbxUtils.offsets_folded([cube], FbxUtils.write_plan(bakes=True)) as done:
            self.assertEqual(done, [])
            self.assertEqual(cmds.getAttr(f"{cube}.offsetParentMatrix"), offset)

    def test_the_fold_is_undone_when_the_write_raises(self):
        cube = self._offset_cube()
        offset = cmds.getAttr(f"{cube}.offsetParentMatrix")
        matrix = cmds.getAttr(f"{cube}.matrix")

        with self.assertRaises(RuntimeError):
            with FbxUtils.offsets_folded(
                [cube], FbxUtils.write_plan(bakes=False)
            ) as done:
                self.assertEqual(done, [cube])
                self.assertNotEqual(cmds.getAttr(f"{cube}.offsetParentMatrix"), offset)
                raise RuntimeError("the write failed")

        self.assertEqual(cmds.getAttr(f"{cube}.offsetParentMatrix"), offset)
        self.assertEqual(cmds.getAttr(f"{cube}.matrix"), matrix)

    def test_what_cannot_fold_is_named(self):
        """A locked channel cannot take the fold: the write ships that node
        displaced, and says so by name."""
        cube = self._offset_cube("locked_cube")
        cmds.setAttr(f"{cube}.rotateX", lock=True)

        with self.assertLogs("mayatk.env_utils.fbx_utils", level="WARNING") as caught:
            with FbxUtils.offsets_folded([cube], FbxUtils.write_plan(bakes=False)):
                pass

        (message,) = [m for m in caught.output if "offsetParentMatrix" in m]
        self.assertIn("locked_cube", message)
        self.assertIn("locked", message)

    def test_an_ancestor_the_write_carries_along_is_folded_too(self):
        """The plug-in writes the parent chain of what it is handed: a child
        exported alone ships its group, and the group's offset with it."""
        group = cmds.group(empty=True, name="offset_group")
        cmds.xform(group, translation=(3, 0, 1), rotation=(0, 30, 0))
        from mayatk.xform_utils._xform_utils import XformUtils

        XformUtils.freeze_to_opm([group])
        cube = cmds.polyCube(name="carried_cube")[0]
        cube = cmds.ls(cmds.parent(cube, group)[0], long=True)[0]
        cmds.setAttr(f"{cube}.translateY", 2)
        world = cmds.getAttr(f"{cube}.worldMatrix[0]")
        path = os.path.join(self.out, "carried.fbx")

        FbxUtils.export(path, [cube])

        cmds.file(new=True, force=True)
        cmds.file(path, i=True, type="FBX", ignoreVersion=True)
        (shipped,) = cmds.ls("carried_cube", long=True, type="transform")
        got = cmds.getAttr(f"{shipped}.worldMatrix[0]")
        self.assertLess(max(abs(a - b) for a, b in zip(world, got)), 1e-4)


class TestNonInheritingWrite(MayaTkTestCase):
    """FBX has no "do not inherit": Maya's writer ships a node with
    ``inheritsTransform`` off as though it inherited -- a baking write keys its
    WORLD matrix as its local, a plain one its TRS alone -- so it lands its
    parent's world away. Measured 2026-10-17 through the shared write the
    Unity, Blender and WebXR hand-offs use: a pinned, locked cube under an
    animated module 121.6 cm off baked and 98.7 plain, a pinned skin 160.8 and
    105.4. Every FBX write re-anchors such a node under its parent, inheriting,
    for the write only (``FbxUtils.offsets_folded`` ->
    ``SmartBake.fold_offsets``), and hands the scene back.
    Added: 2026-10-17
    """

    FRAMES = (1, 9, 16, 24)
    LOCKED = tuple(f"{a}{x}" for a in ("translate", "rotate", "scale") for x in "XYZ")

    def setUp(self):
        super().setUp()
        store = ptk.TempArtifacts("fbx_non_inheriting", policy="scoped")
        self.addCleanup(store.cleanup)
        self.out = store.dir_path()
        FbxUtils.reset_export()
        self.addCleanup(FbxUtils.reset_export)

    def _pinned(self, animated=True, skinned=False, parent_scale=None):
        """A module group and a cube pinned under it
        (``Matrices.pin_world_matrix``), every t/r/s locked; *skinned* adds a
        tube pinned beside a joint chain that rides the module. The module
        moves AFTER the pin -- keyed, or once -- so even a static one holds the
        cube away from where its parent would put it. Returns
        ``(module, cube, tube or None)``."""
        from mayatk.xform_utils.matrices import Matrices

        cmds.playbackOptions(min=1, max=24, animationStartTime=1, animationEndTime=24)
        cmds.currentTime(1)
        root = cmds.group(empty=True, name="module_GRP")
        cmds.xform(root, translation=(20, 4, 0), rotation=(0, 10, 0))
        cube = cmds.polyCube(name="PIN_CUBE")[0]
        cube = cmds.ls(cmds.parent(cube, root)[0], long=True)[0]
        cmds.xform(cube, translation=(3, 2, 1), rotation=(0, 20, 0))
        self.assertTrue(Matrices.pin_world_matrix(cube))
        tube = None
        if skinned:
            tube = cmds.polyCylinder(
                name="LOOM", axis=(1, 0, 0), height=20, radius=1, subdivisionsHeight=10
            )[0]
            tube = cmds.ls(cmds.parent(tube, root)[0], long=True)[0]
            cmds.xform(tube, translation=(0, 5, 0), rotation=(0, -90, 0))
            matrix = cmds.xform(tube, query=True, worldSpace=True, matrix=True)
            rig = cmds.group(empty=True, name="rig_GRP", parent=root)
            cmds.select(rig)
            joints = [
                cmds.joint(
                    name=f"loom_jnt_{i + 1}",
                    position=[matrix[12 + k] + matrix[k] * along for k in range(3)],
                )
                for i, along in enumerate((-10, 0, 10))
            ]
            cmds.select(clear=True)
            self.assertTrue(Matrices.pin_world_matrix(tube))
            cmds.skinCluster(joints, tube, toSelectedBones=True)
            cmds.setKeyframe(joints[1], attribute="rotateZ", time=1, value=0)
            cmds.setKeyframe(joints[1], attribute="rotateZ", time=24, value=40)
        if animated:
            for attr, (first, last) in (
                ("translateX", (50, 150)),
                ("translateY", (10, 10)),
                ("rotateY", (30, 75)),
            ):
                cmds.setKeyframe(root, attribute=attr, time=1, value=first)
                cmds.setKeyframe(root, attribute=attr, time=24, value=last)
        else:
            cmds.xform(root, translation=(50, 10, 0), rotation=(0, 30, 0))
        if parent_scale:
            cmds.xform(root, scale=parent_scale)
        for node in [cube] + ([tube] if tube else []):
            for attr in self.LOCKED:
                cmds.setAttr(f"{node}.{attr}", lock=True)
        return cmds.ls(root, long=True)[0], cube, tube

    def _worlds(self, node):
        out = []
        for frame in self.FRAMES:
            cmds.currentTime(frame)
            out.append(cmds.getAttr(f"{node}.worldMatrix[0]"))
        cmds.currentTime(1)
        return out

    def _points(self, node):
        out = []
        for frame in self.FRAMES:
            cmds.currentTime(frame)
            out.append(
                cmds.xform(
                    f"{node}.vtx[*]", query=True, worldSpace=True, translation=True
                )
            )
        cmds.currentTime(1)
        return out

    @staticmethod
    def _matrix_miss(source, shipped):
        return max(
            max(abs(a - b) for a, b in zip(m, n)) for m, n in zip(source, shipped)
        )

    def _point_miss(self, source, shipped):
        worst = 0.0
        for a, b in zip(source, shipped):
            self.assertEqual(len(a), len(b), "the shipped mesh lost vertices")
            for i in range(0, len(a), 3):
                worst = max(
                    worst, sum((a[i + k] - b[i + k]) ** 2 for k in range(3)) ** 0.5
                )
        return worst

    def _state(self, nodes):
        """Everything the write may touch, to the bit: inheritance, offset and
        its input, every t/r/s value, lock and curve, the node set, the frame."""
        state = {"nodes": sorted(cmds.ls(long=True)), "time": cmds.currentTime(q=True)}
        for node in nodes:
            state[node] = {
                "inherits": cmds.getAttr(f"{node}.inheritsTransform"),
                "opm": cmds.getAttr(f"{node}.offsetParentMatrix"),
                "opm_in": cmds.listConnections(
                    f"{node}.offsetParentMatrix", source=True, destination=False
                ),
                "shear": cmds.getAttr(f"{node}.shear"),
            }
            for attr in self.LOCKED:
                plug = f"{node}.{attr}"
                state[node][attr] = (
                    cmds.getAttr(plug),
                    cmds.getAttr(plug, lock=True),
                    cmds.listConnections(plug, source=True, destination=False),
                )
        return state

    def _write(self, root, name, bakes):
        path = os.path.join(self.out, f"{name}.fbx")
        FbxUtils.reset_export()
        plan = FbxUtils.write_plan(bakes=bakes, span=(1, 24))
        FbxUtils.apply_write_plan(plan)
        FbxUtils.export(path, [root], plan=plan)
        return path, plan

    def _reimport(self, path, *leaves):
        cmds.file(new=True, force=True)
        FbxUtils.import_scene(path)
        return [cmds.ls(leaf, long=True, type="transform")[0] for leaf in leaves]

    def test_a_pinned_node_ships_where_maya_draws_it_through_either_write(self):
        """A pinned, locked cube and a pinned skin under an animated module
        re-import where Maya draws them at every frame, through a baking write
        and a plain one -- and the scene is handed back to the bit."""
        for bakes in (True, False):
            with self.subTest(bakes=bakes):
                cmds.file(new=True, force=True)
                root, cube, tube = self._pinned(skinned=True)
                worlds, points = self._worlds(cube), self._points(tube)
                state = self._state([cube, tube])

                path, _ = self._write(root, f"pinned_{int(bakes)}", bakes)

                self.assertEqual(self._state([cube, tube]), state)
                cube, tube = self._reimport(path, "PIN_CUBE", "LOOM")
                self.assertLess(self._matrix_miss(worlds, self._worlds(cube)), 1e-3)
                self.assertLess(self._point_miss(points, self._points(tube)), 1e-3)

    def test_a_static_pinned_node_ships_static(self):
        """Nothing moves: the write sets plain values, no key -- in the scene
        while it writes, and in the file a plain write leaves."""
        real = cmds.file
        for bakes in (True, False):
            with self.subTest(bakes=bakes):
                cmds.file(new=True, force=True)
                root, cube, _ = self._pinned(animated=False)
                world = cmds.getAttr(f"{cube}.worldMatrix[0]")
                seen = {}

                def spy(*args, **kwargs):
                    if kwargs.get("exportSelected"):
                        seen["inherits"] = cmds.getAttr(f"{cube}.inheritsTransform")
                        seen["keys"] = cmds.keyframe(
                            cube, query=True, keyframeCount=True
                        )
                    return real(*args, **kwargs)

                with patch.object(cmds, "file", side_effect=spy):
                    path, _ = self._write(root, f"static_{int(bakes)}", bakes)

                self.assertEqual(seen, {"inherits": True, "keys": 0})
                (cube,) = self._reimport(path, "PIN_CUBE")
                got = cmds.getAttr(f"{cube}.worldMatrix[0]")
                self.assertLess(max(abs(a - b) for a, b in zip(world, got)), 1e-3)
                if not bakes:
                    self.assertFalse(
                        cmds.keyframe(cube, query=True, keyframeCount=True)
                    )

    def test_a_static_pinned_skin_ships_where_maya_draws_it(self):
        """A static SKINNED node takes one key per channel, not plain values:
        Maya's FBX importer places an unanimated skinned mesh from its bind
        pose (its bind world relative to its parent's), so plain values --
        right in the file -- re-imported 30 cm off here, and 78.4 cm on four
        production wire looms whose module moved after the bind."""
        for bakes in (True, False):
            with self.subTest(bakes=bakes):
                cmds.file(new=True, force=True)
                root, cube, tube = self._pinned(animated=False, skinned=True)
                worlds = self._worlds(tube)
                state = self._state([cube, tube])
                seen = {}
                real = cmds.file

                def spy(*args, **kwargs):
                    if kwargs.get("exportSelected"):
                        seen["keys"] = cmds.keyframe(
                            tube, query=True, keyframeCount=True
                        )
                    return real(*args, **kwargs)

                with patch.object(cmds, "file", side_effect=spy):
                    path, _ = self._write(root, f"static_skin_{int(bakes)}", bakes)

                self.assertEqual(self._state([cube, tube]), state)
                (tube,) = self._reimport(path, "LOOM")
                self.assertLess(self._matrix_miss(worlds, self._worlds(tube)), 1e-3)
                self.assertEqual(seen, {"keys": 9}, "one key per channel")

    def test_the_re_anchor_is_undone_when_the_write_raises(self):
        root, cube, _ = self._pinned()
        state = self._state([cube])

        with self.assertRaises(RuntimeError):
            with FbxUtils.offsets_folded(
                [root], FbxUtils.write_plan(bakes=True, span=(1, 24))
            ) as done:
                self.assertEqual(done, [cube])
                self.assertTrue(cmds.getAttr(f"{cube}.inheritsTransform"))
                raise RuntimeError("the write failed")

        self.assertEqual(self._state([cube]), state)

    def test_a_re_anchor_that_cannot_be_exact_is_named(self):
        """Under a stretched, turned parent the pinned cube's local shears: no
        t/r/s holds it, so it ships displaced and the write says so by name."""
        root, cube, _ = self._pinned(parent_scale=(2.5, 1, 1))
        state = self._state([cube])

        with self.assertLogs("mayatk.env_utils.fbx_utils", level="WARNING") as caught:
            with FbxUtils.offsets_folded(
                [root], FbxUtils.write_plan(bakes=True, span=(1, 24))
            ) as done:
                self.assertEqual(done, [])
                self.assertEqual(self._state([cube]), state)

        (message,) = [m for m in caught.output if "inheritsTransform" in m]
        self.assertIn("PIN_CUBE", message)
        self.assertIn("shears", message)
        self.assertEqual(self._state([cube]), state)


class TestFinishDeliverable(QuickTestCase):
    """``finish_deliverable``: every pass a written FBX deliverable takes."""

    def test_a_scene_whose_files_are_not_resolved_still_finishes(self):
        """The scene's folders and audio are the embed's alone: a failure to
        resolve them skipped the pivot split, the highlight copies and the
        host-lighting drop as well, and the FBX shipped unfinished.
        Added: 2026-10-19"""
        from pythontk.file_utils.mesh_convert.fbx_file import FBX_MAGIC

        store = ptk.TempArtifacts("test_fbx_utils_finish", policy="scoped")
        self.addCleanup(store.cleanup)
        path = os.path.join(store.dir_path(), "room.fbx")
        with open(path, "wb") as fh:
            fh.write(FBX_MAGIC)
        seen = {}

        def finish(fbx, glb=None, **kwargs):
            seen.update(kwargs, fbx=fbx)
            return {}

        with (
            patch.object(
                FbxUtils, "_dependency_inputs", side_effect=RuntimeError("no scene")
            ),
            patch.object(
                ptk.MeshConvert, "finish_fbx_deliverable", side_effect=finish
            ),
            self.assertLogs("mayatk.env_utils.fbx_utils", level="WARNING"),
        ):
            report = FbxUtils.finish_deliverable(path, search_dirs=["D:/maps"])
        self.assertEqual(report, {})
        self.assertEqual(seen["fbx"], path)
        self.assertEqual(
            (seen["search_dirs"], seen["walk_root"], seen["files"]),
            (["D:/maps"], "", ()),
        )


if __name__ == "__main__":
    unittest.main()
