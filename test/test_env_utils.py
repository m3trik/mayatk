# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.env_utils module

Tests for EnvUtils class functionality including:
- Maya environment queries
- Workspace management
- Command port operations
- Path utilities
- Maya version detection
- Plugin management
- Recent files/projects
"""
import os
import unittest
import unittest.mock
import mayatk as mtk
from mayatk.env_utils._env_utils import EnvUtils

from base_test import MayaTkTestCase
import maya.cmds as cmds



# --- pymel migration shims (auto-injected by _convert_pm_to_cmds.py) ---
from contextlib import contextmanager as _contextmanager


def _pm_open_file(*args, **kw):
    kw.setdefault("open", True)
    return cmds.file(*args, **kw)


def _pm_new_file(**kw):
    kw.setdefault("new", True)
    return cmds.file(**kw)


def _pm_rename_file(path):
    return cmds.file(rename=path)


@_contextmanager
def _pm_undo_chunk():
    cmds.undoInfo(openChunk=True)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)
# --- end shims ---
class TestEnvUtils(MayaTkTestCase):
    """Comprehensive tests for EnvUtils class."""

    def setUp(self):
        """Set up test environment."""
        super().setUp()
        # Ensure we have a known state for some tests
        self.original_workspace = cmds.workspace(q=True, rd=True)

    def tearDown(self):
        """Restore test environment."""
        # Restore workspace if changed
        if cmds.workspace(q=True, rd=True) != self.original_workspace:
            cmds.workspace(self.original_workspace, openWorkspace=True)
        super().tearDown()

    # -------------------------------------------------------------------------
    # Environment Info Tests
    # -------------------------------------------------------------------------

    def test_get_env_info_basic(self):
        """Test getting basic environment info keys."""
        keys_to_test = [
            "version",
            "workspace",
            "scene",
            "user_name",
            "ui_language",
            "os_type",
            "api_version",
            "application",
        ]

        for key in keys_to_test:
            val = EnvUtils.get_env_info(key)
            self.assertIsNotNone(val, f"Failed to get {key}")

    def test_get_env_info_paths(self):
        """Test getting path-related environment info."""
        # Test workspace paths
        ws_dir = EnvUtils.get_env_info("workspace_dir")
        ws_path = EnvUtils.get_env_info("workspace_path")
        self.assertTrue(os.path.isdir(ws_dir) or os.path.isdir(ws_path))

        # Test sourceimages
        src_imgs = EnvUtils.get_env_info("sourceimages")
        # Note: sourceimages might not exist in a temp test environment, but the path string should be valid
        self.assertIsInstance(src_imgs, str)
        self.assertTrue(len(src_imgs) > 0)

    def test_get_env_info_scene(self):
        """Test scene-related info."""
        # Save a temp scene to ensure we have a valid scene name
        temp_file = os.path.join(cmds.internalVar(userTmpDir=True), "test_env_utils.ma")
        _pm_rename_file(temp_file)

        scene_name = EnvUtils.get_env_info("scene_name")
        self.assertEqual(scene_name, "test_env_utils")

        scene_path = EnvUtils.get_env_info("scene_path")
        # ptk.format_path(..., "path") returns the directory path, not the full file path
        self.assertTrue(os.path.isdir(scene_path))
        self.assertTrue(temp_file.replace("\\", "/").startswith(scene_path))

        # Test modified flag
        cmds.polyCube()  # Modify scene
        is_mod = EnvUtils.get_env_info("scene_modified")
        self.assertTrue(is_mod)

    def test_get_env_info_units(self):
        """Test unit queries."""
        linear = EnvUtils.get_env_info("linear_units")
        time = EnvUtils.get_env_info("time_units")
        self.assertIsInstance(linear, str)
        self.assertIsInstance(time, str)

    def test_get_env_info_multiple(self):
        """Test getting multiple keys at once."""
        res = EnvUtils.get_env_info("version|workspace")
        self.assertIsInstance(res, list)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0], EnvUtils.get_env_info("version"))

    def test_get_env_info_invalid(self):
        """Test error handling for invalid keys."""
        with self.assertRaises(KeyError):
            EnvUtils.get_env_info("non_existent_key_12345")

    def test_get_env_info_with_no_key_returns_every_key(self):
        """The whole-host form, mirroring btk.CoreUtils.get_env_info().

        What a hand-off, a bug report, or an agent triaging a remote failure
        wants: the host as it stands, without having to know the key list to
        ask for it one fact at a time.
        """
        info = EnvUtils.get_env_info()

        self.assertIsInstance(info, dict)
        for key in ("version", "os_type", "api_version", "application"):
            self.assertIn(key, info)
            self.assertEqual(info[key], EnvUtils.get_env_info(key))

    def test_get_env_info_sweep_survives_a_key_that_cannot_answer(self):
        """One unavailable value must not cost the other twenty-nine.

        Several keys are scene- or panel-dependent and raise when what they
        query is absent (a headless session has no `modelPanel4`). In the dict
        form that is reported as None -- itself part of the picture -- rather
        than failing the sweep, which is the whole reason to offer the form.
        """
        # `modelEditor` backs viewport_renderer/current_camera and is exactly
        # the panel-dependent shape this guards: it raises outright when
        # `modelPanel4` does not exist.
        with unittest.mock.patch.object(
            cmds, "modelEditor", side_effect=RuntimeError("no such panel")
        ):
            info = EnvUtils.get_env_info()

        self.assertIsInstance(info, dict)
        self.assertGreater(len(info), 20)
        self.assertIsNone(info["viewport_renderer"])
        self.assertIsNone(info["current_camera"])
        # The rest of the sweep is unaffected -- that is the point.
        self.assertEqual(info["version"], EnvUtils.get_env_info("version"))

    # -------------------------------------------------------------------------
    # Plugin Management Tests
    # -------------------------------------------------------------------------

    def test_load_plugin(self):
        """Test loading a standard Maya plugin."""
        # 'objExport' is a standard plugin usually available
        plugin_name = "objExport"

        # Ensure it's unloaded first (if possible/safe)
        if cmds.pluginInfo(plugin_name, q=True, loaded=True):
            cmds.unloadPlugin(plugin_name)

        EnvUtils.load_plugin(plugin_name)
        self.assertTrue(cmds.pluginInfo(plugin_name, q=True, loaded=True))

        # Test loading already loaded plugin (should not error)
        EnvUtils.load_plugin(plugin_name)
        self.assertTrue(cmds.pluginInfo(plugin_name, q=True, loaded=True))

    def test_load_plugin_invalid(self):
        """Test loading a non-existent plugin."""
        with self.assertRaises(ValueError):
            EnvUtils.load_plugin("non_existent_plugin_xyz")

    # -------------------------------------------------------------------------
    # Recent Files & Projects Tests
    # -------------------------------------------------------------------------

    def test_get_recent_files(self):
        """Test retrieving recent files."""
        # We can't easily populate recent files list in a test, but we can check the return type
        recent = EnvUtils.get_recent_files()
        self.assertIsInstance(recent, list)

        # If there are recent files, check structure
        if recent:
            self.assertIsInstance(recent[0], str)

        # Test index access
        if recent:
            first = EnvUtils.get_recent_files(0)
            self.assertEqual(first, recent[0])

    def test_get_recent_projects(self):
        """Test retrieving recent projects."""
        recent = EnvUtils.get_recent_projects()
        self.assertIsInstance(recent, list)

        # Test formats
        recent_ts = EnvUtils.get_recent_projects(format="timestamp")
        self.assertIsInstance(recent_ts, list)

    # -------------------------------------------------------------------------
    # Path & System Tests
    # -------------------------------------------------------------------------

    def test_append_maya_paths(self):
        """Test appending Maya paths to sys.path."""
        # This modifies global state, so we should be careful
        # Just verify it runs without error and adds something to path
        import sys

        original_len = len(sys.path)

        try:
            EnvUtils.append_maya_paths()
        except EnvironmentError:
            # MAYA_LOCATION might not be set in some test envs
            pass

        # We can't strictly assert length changed because paths might already be there
        # But we can assert it didn't crash

    def test_scene_unit_values(self):
        """Test the SCENE_UNIT_VALUES constant."""
        self.assertIsInstance(EnvUtils.SCENE_UNIT_VALUES, dict)
        self.assertIn("centimeter", EnvUtils.SCENE_UNIT_VALUES)
        self.assertEqual(EnvUtils.SCENE_UNIT_VALUES["centimeter"], "cm")


class TestExportSceneAsFbxDefaults(MayaTkTestCase):
    """export_scene_as_fbx default FBX options.

    Regression: ``FBXExportHardEdges`` ("Split per-vertex Normals") used to
    default ``True``, which hangs for 90+ minutes on a fully-faceted dense
    mesh (e.g. a photogrammetry scan) — the FBX SDK's vertex-split pass is
    pathologically super-linear when nearly every edge is hard. It must
    default OFF, while still honoring an explicit override for the rare
    caller that genuinely needs split-normal output.
    """

    def setUp(self):
        super().setUp()
        import tempfile

        cmds.loadPlugin("fbxmaya", quiet=True)
        self.tmp = tempfile.mkdtemp(prefix="fbx_env_test_")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def test_hard_edges_default_off(self):
        """No override ⇒ the applied FBXExportHardEdges is False."""
        import maya.mel as mel

        cube = cmds.polyCube(name="hard_edges_cube")[0]
        cmds.select(cube)
        # os.path.join → backslashes on Windows; keep it that way so this also
        # guards the MEL path-normalize fix (a raw backslash path errors the
        # FBXExport MEL string). Don't "simplify" to a forward-slash literal.
        out = os.path.join(self.tmp, "default.fbx")
        EnvUtils.export_scene_as_fbx(file_path=out, selection_only=True)
        self.assertTrue(os.path.isfile(out))
        self.assertFalse(bool(mel.eval("FBXExportHardEdges -q")))

    def test_hard_edges_override_honored(self):
        """An explicit FBXExportHardEdges=True still wins."""
        import maya.mel as mel

        cube = cmds.polyCube(name="hard_edges_cube2")[0]
        cmds.select(cube)
        out = os.path.join(self.tmp, "override.fbx")
        EnvUtils.export_scene_as_fbx(
            file_path=out, selection_only=True, FBXExportHardEdges=True
        )
        self.assertTrue(os.path.isfile(out))
        self.assertTrue(bool(mel.eval("FBXExportHardEdges -q")))

    def test_embed_media_resolves_relative_textures_despite_foreign_cwd(self):
        """The default FBXExportEmbeddedTextures=True must embed a
        project-relative texture even when the process CWD is foreign.

        The fbxmaya plugin locates embed sources with plain OS path
        resolution — relative fileTextureName values against the process CWD,
        never the workspace (probe-proven 2026-08-04).  GUI Maya doesn't chdir
        on Set Project, so without CWD alignment around the write the plugin
        silently drops every relative texture from the FBX.
        Added: 2026-08-04
        """
        import maya.api.OpenMaya as om

        # Temp project with a 1 MB incompressible texture under sourceimages.
        proj = os.path.join(self.tmp, "proj")
        si = os.path.join(proj, "sourceimages")
        os.makedirs(si, exist_ok=True)
        payload = os.urandom(1024 * 1024)
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
        # Store the relative path VERBATIM — cmds.setAttr auto-expands a
        # resolvable relative fileTextureName to absolute.
        sel = om.MSelectionList()
        sel.add(file_node)
        om.MFnDependencyNode(sel.getDependNode(0)).findPlug(
            "fileTextureName", False
        ).setString("sourceimages/emb.png")

        cmds.select(cube)
        out = os.path.join(self.tmp, "embed_cwd.fbx")

        original_cwd = os.getcwd()
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere, exist_ok=True)
        os.chdir(elsewhere)  # foreign CWD — the failure state
        try:
            EnvUtils.export_scene_as_fbx(file_path=out, selection_only=True)
            self.assertEqual(
                os.getcwd(), elsewhere, "export must restore the caller's CWD"
            )
        finally:
            os.chdir(original_cwd)

        self.assertTrue(os.path.isfile(out))
        self.assertGreater(
            os.path.getsize(out),
            len(payload),
            "embedded texture payload missing from the FBX — the plugin could "
            "not locate the project-relative path",
        )


if __name__ == "__main__":
    unittest.main()


class TestExportSceneAsObj(MayaTkTestCase):
    """``export_scene_as_obj`` — the OBJ sibling of ``export_scene_as_fbx``.

    The translator is named ``OBJexport``, its options are a semicolon string and
    the plugin needs loading first — three details a caller should not have to
    know, which is the whole reason the helper exists. ``blendertk`` ships the twin
    under the same name and parameters, so the Scene panel's format combo needs no
    per-DCC branch.
    """

    def setUp(self):
        super().setUp()
        import tempfile

        self.tmp = tempfile.mkdtemp(prefix="obj_env_test_")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def _shaded_cube(self, name):
        cube = cmds.polyCube(name=name)[0]
        shader = cmds.shadingNode("lambert", asShader=True, name=f"{name}_mat")
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True, name=f"{name}_matSG"
        )
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(cube, edit=True, forceElement=sg)
        return cube

    def test_whole_scene_writes_geometry_and_the_mtl_sidecar(self):
        self._shaded_cube("obj_cube")
        cmds.polySphere(name="obj_sphere")
        out = os.path.join(self.tmp, "scene.obj")
        self.assertEqual(EnvUtils.export_scene_as_obj(file_path=out), out)
        self.assertTrue(os.path.isfile(out))
        body = open(out, encoding="utf-8", errors="replace").read()
        # Both objects, their group records, normals and UVs.
        self.assertIn("g obj_cube", body)
        self.assertIn("g obj_sphere", body)
        self.assertIn("\nvn ", body)
        self.assertIn("\nvt ", body)
        self.assertTrue(os.path.isfile(os.path.splitext(out)[0] + ".mtl"))

    def test_selection_only_exports_just_the_selection(self):
        """The translator has no ``-s`` flag — scope is exportSelected vs exportAll,
        which is exactly the detail that makes this worth a helper."""
        cube = self._shaded_cube("obj_sel_cube")
        cmds.polySphere(name="obj_sel_sphere")
        cmds.select(cube, replace=True)
        out = os.path.join(self.tmp, "sel.obj")
        EnvUtils.export_scene_as_obj(file_path=out, selection_only=True)
        verts = [
            line
            for line in open(out, encoding="utf-8", errors="replace")
            if line.startswith("v ")
        ]
        self.assertEqual(len(verts), 8, "a cube alone is 8 verts")

    def test_materials_off_writes_no_mtl(self):
        self._shaded_cube("obj_nomat_cube")
        out = os.path.join(self.tmp, "nomat.obj")
        EnvUtils.export_scene_as_obj(file_path=out, materials=False)
        self.assertTrue(os.path.isfile(out))
        self.assertFalse(os.path.isfile(os.path.splitext(out)[0] + ".mtl"))

    def test_unsaved_scene_with_no_path_raises(self):
        """Deriving the name needs a saved scene; the error says so rather than
        writing an ``.obj`` somewhere surprising."""
        cmds.file(new=True, force=True)
        with self.assertRaises(ValueError):
            EnvUtils.export_scene_as_obj()


class TestSavedScenePath(MayaTkTestCase):
    """``saved_scene_path`` — the "has this scene ever been saved?" answer.

    Regression: ``cmds.file(q=True, sceneName=True)`` is documented as "" for an
    unsaved scene, and IS in the GUI — but batch/standalone returns a phantom
    extensionless ``<project>/untitled`` instead (measured in mayapy). Every
    ``if not scene_path`` guard written against the documented behavior therefore
    passed in batch, so ``export_scene_as_fbx()`` with no path silently wrote into
    the default project rather than raising, and the Scene panel's "Alongside Scene
    File" mode did the same.
    """

    def test_a_new_scene_reads_as_unsaved(self):
        cmds.file(new=True, force=True)
        raw = cmds.file(query=True, sceneName=True) or ""
        self.assertEqual(EnvUtils.saved_scene_path(), "")
        # If Maya ever starts returning "" here, this test still passes and the
        # helper is simply redundant — but say which behavior was seen.
        if raw:
            self.assertFalse(
                os.path.splitext(raw)[1],
                f"phantom path unexpectedly carries an extension: {raw}",
            )

    def setUp(self):
        super().setUp()
        import tempfile

        self.tmp_dir = tempfile.mkdtemp(prefix="saved_scene_test_")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        super().tearDown()

    def test_a_named_scene_reads_as_saved(self):
        """A rename without a save still yields a real, extensioned name — enough to
        derive an export path beside it."""
        cmds.file(new=True, force=True)
        cmds.file(rename=os.path.join(self.tmp_dir, "real_scene.ma"))
        self.assertTrue(EnvUtils.saved_scene_path().endswith("real_scene.ma"))

    def test_a_stray_file_at_the_phantom_path_does_not_legitimize_it(self):
        """Deliberately no disk probe: the answer must depend on the scene, not on
        whatever happens to sit at the phantom location."""
        cmds.file(new=True, force=True)
        raw = cmds.file(query=True, sceneName=True) or ""
        if not raw:
            self.skipTest("this Maya reports '' for an unsaved scene")
        os.makedirs(os.path.dirname(raw), exist_ok=True)
        with open(raw, "w", encoding="utf-8") as fh:
            fh.write("")
        try:
            self.assertEqual(EnvUtils.saved_scene_path(), "")
        finally:
            os.remove(raw)
