# !/usr/bin/python
# coding=utf-8
"""Test Suite for mayatk.env_utils.fbx_utils module.

Covers FbxUtils — plugin loading, preset application, option setting,
and the ``export`` driver.
"""
import os
import unittest
import tempfile

import maya.cmds as cmds

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
    """Every ``_KNOWN_PRODUCERS`` entry must resolve to a real callable.

    The registry names module/class/method as strings resolved lazily, and
    ``run_export_preparers`` skips an unresolvable producer with only a
    debug log — so a rename anywhere in those modules would silently stop
    that producer's metadata shipping. This pins each entry to the code.
    """

    def test_all_entries_resolve(self):
        import importlib

        for name, (module_path, class_name, method_name) in (
            FbxUtils._KNOWN_PRODUCERS.items()
        ):
            with self.subTest(producer=name):
                module = importlib.import_module(module_path)
                cls = getattr(module, class_name)
                self.assertTrue(
                    callable(getattr(cls, method_name)),
                    f"{name}: {module_path}.{class_name}.{method_name} "
                    "is not callable",
                )


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


if __name__ == "__main__":
    unittest.main()
