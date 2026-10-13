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

import maya.cmds as cmds
import maya.mel as mel

import pythontk as ptk
from mayatk.env_utils.fbx_utils import FbxUtils

from base_test import MayaTkTestCase


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


if __name__ == "__main__":
    unittest.main()
