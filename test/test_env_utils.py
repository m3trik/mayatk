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
import sys
import unittest
import unittest.mock
from mayatk.env_utils._env_utils import EnvUtils
from mayatk.node_utils.data_nodes import DataNodes

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
    # Autosave
    # -------------------------------------------------------------------------

    def test_autosave_directories_include_the_folder_maya_writes_to(self):
        """Maya's autosave preference can name a folder (destination 1) -- a
        local temp folder keeps backups out of a synced project -- and that is
        where it writes, so recovery must look there: the lookup only knew the
        workspace, MAYA_AUTOSAVE_FOLDER and the home folder. Each directory is
        reported once. Added: 2026-09-19"""
        import shutil

        named = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "temp_tests", "autosave_named"
        )
        os.makedirs(named, exist_ok=True)
        self.addCleanup(shutil.rmtree, named, ignore_errors=True)
        destination = cmds.autoSave(q=True, destination=True)
        folder = cmds.autoSave(q=True, folder=True)
        self.addCleanup(cmds.autoSave, destination=destination, folder=folder or "")

        cmds.autoSave(destination=1, folder=named)
        found = EnvUtils.find_autosave_directories()
        key = os.path.normcase(os.path.normpath(named))
        self.assertEqual(
            [d for d in found if os.path.normcase(os.path.normpath(d)) == key],
            [named],
        )
        self.assertEqual(
            len(found), len({os.path.normcase(os.path.normpath(d)) for d in found})
        )

        cmds.autoSave(destination=0)
        self.assertNotIn(named, EnvUtils.find_autosave_directories())

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

    def test_get_env_info_mayapy_is_this_mayas_interpreter(self):
        """``bin/mayapy.exe`` on Windows, ``bin/mayapy`` on Linux: tentacle's
        updater and version check hard-coded the ``.exe``, so on Linux both ran
        an interpreter that does not exist."""
        mayapy = EnvUtils.get_env_info("mayapy")
        self.assertTrue(mayapy and os.path.isfile(mayapy), mayapy)
        expected = "mayapy.exe" if sys.platform == "win32" else "mayapy"
        self.assertEqual(os.path.basename(mayapy).lower(), expected)

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

    def test_load_plugin_is_a_deprecated_alias_of_the_plugin_door(self):
        """Moved to ``Plugins.load`` / ``Plugins.is_loaded`` (the one door,
        ``test_plugins``): the aliases warn and still load and answer."""
        plugin_name = "objExport"
        if cmds.pluginInfo(plugin_name, q=True, loaded=True):
            cmds.unloadPlugin(plugin_name)

        with self.assertWarns(DeprecationWarning):
            EnvUtils.load_plugin(plugin_name)
        self.assertTrue(cmds.pluginInfo(plugin_name, q=True, loaded=True))
        with self.assertWarns(DeprecationWarning):
            self.assertTrue(EnvUtils.is_plugin_loaded(plugin_name))

    def test_load_plugin_invalid(self):
        """The alias keeps its ValueError for a plugin Maya cannot find."""
        with self.assertWarns(DeprecationWarning), self.assertRaises(ValueError):
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
        # It extends sys.path and sets env vars -- process-global state -- so
        # both are restored: the run only has to not crash (the paths may
        # already be there, so no length assertion).
        import sys

        saved_path = list(sys.path)
        try:
            with unittest.mock.patch.dict(os.environ):
                EnvUtils.append_maya_paths()
        except EnvironmentError:
            pass  # MAYA_LOCATION might not be set in some test envs
        finally:
            sys.path[:] = saved_path

    def test_scene_unit_values(self):
        """Test the SCENE_UNIT_VALUES constant."""
        self.assertIsInstance(EnvUtils.SCENE_UNIT_VALUES, dict)
        self.assertIn("centimeter", EnvUtils.SCENE_UNIT_VALUES)
        self.assertEqual(EnvUtils.SCENE_UNIT_VALUES["centimeter"], "cm")


class TestSceneSettings(MayaTkTestCase):
    """``scene_settings`` / ``apply_scene_settings`` — the bridges' ``scene`` record.

    Twin of the blendertk block in ``test_env_utils.py``: same keys, same mapping
    (playback = min/max, anim = ast/aet). Measured before this existed: a Blender
    scene pulled into Maya arrived on the default clock (film, 1-120).
    """

    def setUp(self):
        super().setUp()
        self._unit = cmds.currentUnit(q=True, time=True)

    def tearDown(self):
        cmds.currentUnit(time=self._unit)
        super().tearDown()

    def test_round_trip(self):
        record = {
            "fps": 30.0,
            "frame_start": 10,
            "frame_end": 90,
            "anim_start": 5,
            "anim_end": 100,
            "frame_current": 42,
        }
        applied = EnvUtils.apply_scene_settings(record)
        self.assertEqual(set(applied), set(EnvUtils.SCENE_SETTINGS_KEYS))
        self.assertEqual(cmds.currentUnit(q=True, time=True), "ntsc")
        q = lambda **k: cmds.playbackOptions(q=True, **k)  # noqa: E731
        self.assertEqual((q(min=True), q(max=True)), (10.0, 90.0))
        self.assertEqual((q(ast=True), q(aet=True)), (5.0, 100.0))
        self.assertEqual(cmds.currentTime(q=True), 42.0)
        back = EnvUtils.scene_settings()
        self.assertEqual(back, {k: float(v) for k, v in record.items()})

    def test_unit_change_keeps_real_time_of_existing_keys(self):
        # Importers place keys seconds-correct in the unit the scene HAD; the
        # record's unit must remap them (frame 8 @ film == frame 10 @ ntsc).
        cmds.currentUnit(time="film")
        cube = cmds.polyCube(name="clock_cube")[0]
        cmds.setKeyframe(cube, attribute="translateX", t=8, v=1.0)
        EnvUtils.apply_scene_settings({"fps": 30.0})
        self.assertEqual(
            cmds.keyframe(cube, q=True, timeChange=True, attribute="translateX"),
            [10.0],
        )

    def test_custom_rate_and_partial_records(self):
        EnvUtils.apply_scene_settings({"fps": 29.97})
        self.assertEqual(cmds.currentUnit(q=True, time=True), "29.97fps")
        self.assertAlmostEqual(EnvUtils.scene_settings()["fps"], 29.97, places=3)
        # Playback only -> the animation range follows it; nothing else touched.
        cmds.currentTime(7)
        applied = EnvUtils.apply_scene_settings({"frame_start": 3, "frame_end": 30})
        self.assertNotIn("frame_current", applied)
        self.assertEqual(cmds.currentTime(q=True), 7.0)
        self.assertEqual(cmds.playbackOptions(q=True, ast=True), 3.0)
        self.assertEqual(EnvUtils.apply_scene_settings({}), [])
        self.assertEqual(EnvUtils.apply_scene_settings({"fps": 0}), [])

    def test_scene_has_content(self):
        self.assertFalse(EnvUtils.scene_has_content())  # startup cameras only
        cmds.group(empty=True, name="content_grp")
        self.assertTrue(EnvUtils.scene_has_content())


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


class TestExportSceneAsFbxShipsWhereMayaDraws(MayaTkTestCase):
    """``export_scene_as_fbx`` -- tentacle's Export Scene, FBX and GLB -- runs
    the one fold every FBX write runs (``FbxUtils.offsets_folded``). It wrote
    through ``FBXExport`` around it: an offsetParentMatrix was dropped (the
    write does not bake) and a pinned node shipped its TRS under a parent
    that had moved. Measured 2026-10-17 in the selection and whole-scene
    modes: both nodes off by their offsets.
    Added: 2026-10-17
    """

    def setUp(self):
        super().setUp()
        import pythontk as ptk

        cmds.loadPlugin("fbxmaya", quiet=True)
        store = ptk.TempArtifacts("env_export_scene_fold", policy="scoped")
        self.addCleanup(store.cleanup)
        self.out = store.dir_path()

    def _scene(self):
        """A cube frozen into its offsetParentMatrix, and one pinned
        (``Matrices.pin_world_matrix``, every t/r/s locked) under a group that
        moved after the pin."""
        from mayatk.xform_utils._xform_utils import XformUtils
        from mayatk.xform_utils.matrices import Matrices

        frozen = cmds.polyCube(name="frozen_cube")[0]
        cmds.xform(frozen, translation=(3, 0, 1), rotation=(0, 30, 0))
        XformUtils.freeze_to_opm([frozen])
        group = cmds.group(empty=True, name="moved_GRP")
        pinned = cmds.polyCube(name="pinned_cube")[0]
        pinned = cmds.parent(pinned, group)[0]
        cmds.xform(pinned, translation=(3, 2, 1), rotation=(0, 20, 0))
        self.assertTrue(Matrices.pin_world_matrix(pinned))
        cmds.xform(group, translation=(50, 10, 0), rotation=(0, 30, 0))
        for attr in ("translate", "rotate", "scale"):
            for axis in "XYZ":
                cmds.setAttr(f"{pinned}.{attr}{axis}", lock=True)
        return {
            leaf: cmds.getAttr(f"{leaf}.worldMatrix[0]")
            for leaf in ("frozen_cube", "pinned_cube")
        }, ["|frozen_cube", "|moved_GRP"]

    def test_each_node_ships_where_maya_draws_it(self):
        for selection_only in (True, False):
            with self.subTest(selection_only=selection_only):
                cmds.file(new=True, force=True)
                worlds, roots = self._scene()
                cmds.select(roots)
                out = os.path.join(self.out, f"scene_{int(selection_only)}.fbx")

                EnvUtils.export_scene_as_fbx(
                    file_path=out, selection_only=selection_only
                )

                self.assertFalse(cmds.getAttr("pinned_cube.inheritsTransform"))
                self.assertTrue(cmds.getAttr("pinned_cube.translateX", lock=True))
                cmds.file(new=True, force=True)
                cmds.file(out, i=True, type="FBX", ignoreVersion=True)
                misses = {}
                for leaf, world in worlds.items():
                    (node,) = cmds.ls(leaf, long=True, type="transform")
                    got = cmds.getAttr(f"{node}.worldMatrix[0]")
                    misses[leaf] = max(abs(a - b) for a, b in zip(world, got))
                self.assertLess(max(misses.values()), 1e-4, misses)


class TestListReferenceNodes(MayaTkTestCase):
    """Screening of the reference nodes Maya cannot answer for.

    Built live because the whole point is Maya's own behaviour: a scene that
    references a file which is ALREADY open cannot form that reference, and what it
    leaves behind is a reference node with no file. Found in production — ROOM_ENV
    open, PROPS_ASSEMBLY (which references ROOM_ENV) referenced in — where it threw
    out of every Reference Manager path that read a reference's filename.
    """

    def setUp(self):
        super().setUp()
        self.child = self.temp_path("_ref_screen_child.ma")
        self.parent = self.temp_path("_ref_screen_parent.ma")
        self._save_scene(lambda: cmds.polyCube(name="child_cube"), self.child)
        self._save_scene(
            lambda: cmds.file(self.child, reference=True, namespace="CHILD"),
            self.parent,
        )
        cmds.file(new=True, force=True)

    def tearDown(self):
        cmds.file(new=True, force=True)  # release the file handles before deleting
        for path in (self.child, self.parent):
            try:
                os.remove(path)
            except OSError:
                pass

    @staticmethod
    def _save_scene(build, path):
        cmds.file(new=True, force=True)
        build()
        cmds.file(rename=path)
        cmds.file(save=True, type="mayaAscii", force=True)

    def test_nested_reference_is_excluded_by_default(self):
        """Maya refuses file -removeReference on a nested reference ("its parent is a
        reference"), so listing one gives a caller something it can never act on."""
        cmds.file(self.parent, reference=True, namespace="PARENT")

        self.assertIn("PARENT:CHILDRN", cmds.ls(type="reference") or [])
        self.assertEqual(EnvUtils.list_reference_nodes(), ["PARENTRN"])

    def test_nested_reference_is_included_on_request(self):
        cmds.file(self.parent, reference=True, namespace="PARENT")

        self.assertEqual(
            sorted(EnvUtils.list_reference_nodes(top_level=False)),
            ["PARENT:CHILDRN", "PARENTRN"],
        )

    def test_a_reference_maya_could_not_resolve_is_excluded(self):
        """The production repro, in miniature: with the child open as the scene, the
        parent's reference to it cannot form, and its node is left with no file."""
        cmds.file(self.child, open=True, force=True)
        cmds.file(self.parent, reference=True, namespace="PARENT")

        hollow = "PARENT:CHILDRN"
        self.assertIn(hollow, cmds.ls(type="reference") or [])
        with self.assertRaises(RuntimeError):  # every flag but -isNodeReferenced
            cmds.referenceQuery(hollow, filename=True)

        self.assertEqual(EnvUtils.list_reference_nodes(), ["PARENTRN"])
        # Excluded whether or not nested references are wanted: it has no file either way.
        self.assertEqual(EnvUtils.list_reference_nodes(top_level=False), ["PARENTRN"])

    def test_a_file_less_node_promoted_to_top_level_by_an_import_is_excluded(self):
        """Importing the parent strips the namespace off the file-less node, leaving it
        top-level — so nesting alone cannot screen it, and the -filename probe must."""
        cmds.file(self.child, open=True, force=True)
        cmds.file(self.parent, reference=True, namespace="PARENT")
        cmds.file(referenceNode="PARENTRN", importReference=True)

        remaining = cmds.ls(type="reference") or []
        self.assertTrue(remaining, "expected the file-less node to survive the import")
        self.assertFalse(
            cmds.referenceQuery(remaining[0], isNodeReferenced=True),
            "the promoted node is top-level, so only -filename can screen it",
        )
        self.assertEqual(EnvUtils.list_reference_nodes(), [])

    def test_file_less_lists_exactly_what_the_filename_probe_rejects(self):
        """The same screen inverted, for a caller that clears the debris up:
        nesting still screens (a nested node goes with its parent), and a
        healthy reference is never listed."""
        cmds.file(self.child, open=True, force=True)
        cmds.file(self.parent, reference=True, namespace="PARENT")
        self.assertEqual(EnvUtils.list_reference_nodes(file_less=True), [])
        self.assertEqual(
            EnvUtils.list_reference_nodes(top_level=False, file_less=True),
            ["PARENT:CHILDRN"],
        )
        cmds.file(referenceNode="PARENTRN", importReference=True)
        self.assertEqual(
            EnvUtils.list_reference_nodes(file_less=True), ["PARENT:CHILDRN"]
        )

    def test_list_references_returns_openable_paths(self):
        """Maya tags the second reference to a file with a copy number — 'scene.ma{1}'
        is not a path anything can open, so the query has to drop it."""
        cmds.file(self.child, reference=True, namespace="CHILD_A")
        cmds.file(self.child, reference=True, namespace="CHILD_B")

        paths = EnvUtils.list_references()

        self.assertEqual(len(paths), 2)  # both references, distinct nodes
        for path in paths:
            self.assertNotIn("{", path)
            self.assertTrue(os.path.exists(path), path)


class TestClearOrphanFosterParents(MayaTkTestCase):
    """Removing a reference left ``<RN>fosterParent1`` behind in production
    (``ROOM_ENVRNfosterParent1``): Maya parks the local nodes that were
    parented under referenced ones there, and removing the reference only drops the
    ``<RN>.fosterParent`` link. Built live because the whole point is Maya's own
    removal behaviour.
    """

    def setUp(self):
        super().setUp()
        self.child = self.temp_path("_foster_child.ma")
        cmds.file(new=True, force=True)
        grp = cmds.group(empty=True, name="grp")
        cmds.parent(cmds.polyCube(name="cube")[0], grp)
        cmds.file(rename=self.child)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)

    def tearDown(self):
        cmds.file(new=True, force=True)  # release the file handle before deleting
        try:
            os.remove(self.child)
        except OSError:
            pass

    def _reference_with_local_child(self):
        """Reference the child and parent a local locator under its group."""
        cmds.file(self.child, reference=True, namespace="ENV")
        loc = cmds.spaceLocator(name="localLoc")[0]
        cmds.parent(loc, "ENV:grp")
        return cmds.referenceQuery(self.child, referenceNode=True)

    def test_removing_a_reference_returns_its_local_children_to_the_world(self):
        self._reference_with_local_child()

        EnvUtils.remove_reference(self.child)

        self.assertEqual(cmds.ls(type="fosterParent"), [])
        self.assertEqual(cmds.ls("localLoc", long=True), ["|localLoc"])

    def test_an_orphaned_deformed_shape_goes_with_its_foster_parent(self):
        """Deforming a referenced mesh makes a LOCAL shape under the referenced
        transform; with that transform gone it has nothing to hang from."""
        cmds.file(self.child, reference=True, namespace="ENV")
        cmds.nonLinear("ENV:cube", type="bend")

        EnvUtils.remove_reference(self.child)

        self.assertEqual(cmds.ls(type="fosterParent"), [])
        self.assertEqual(cmds.ls("cubeShapeDeformed*"), [])

    def test_an_unloaded_reference_keeps_its_foster_parent(self):
        """Reloading reparents the fostered nodes back, so the link must survive."""
        rn = self._reference_with_local_child()
        cmds.file(unloadReference=rn)
        fosters = cmds.ls(type="fosterParent")
        self.assertTrue(fosters, "Maya fosters local children on unload")

        self.assertEqual(EnvUtils.clear_orphan_foster_parents(), [])
        self.assertEqual(cmds.ls(type="fosterParent"), fosters)

    def test_a_leftover_from_an_earlier_removal_is_swept(self):
        """A scene already carrying an orphan (removed outside the toolkit)."""
        rn = self._reference_with_local_child()
        cmds.xform("localLoc", translation=(1, 2, 3), worldSpace=True)
        cmds.file(removeReference=True, referenceNode=rn)  # raw Maya: orphans it
        self.assertTrue(cmds.ls(type="fosterParent"))

        self.assertEqual(EnvUtils.clear_orphan_foster_parents(), ["localLoc"])
        self.assertEqual(cmds.ls(type="fosterParent"), [])
        self.assertEqual(
            [round(v, 3) for v in cmds.xform("localLoc", q=True, t=True, ws=True)],
            [1.0, 2.0, 3.0],
        )

    def test_a_foster_parent_it_cannot_empty_is_kept_not_raised(self):
        """A locked child cannot be reparented. Raising would make the removal
        that came before it look failed, and deleting would take the user's node
        with it, so that foster parent stays."""
        rn = self._reference_with_local_child()
        cmds.file(removeReference=True, referenceNode=rn)
        fosters = cmds.ls(type="fosterParent")
        cmds.lockNode("localLoc", lock=True)
        try:
            self.assertEqual(EnvUtils.clear_orphan_foster_parents(), [])
            self.assertEqual(cmds.ls(type="fosterParent"), fosters)
            self.assertTrue(cmds.objExists("localLoc"))
        finally:
            cmds.lockNode("localLoc", lock=False)


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

    def test_scene_artifact_path_sits_beside_the_scene(self):
        cmds.file(new=True, force=True)
        cmds.file(rename=os.path.join(self.tmp_dir, "real_scene.ma"))
        self.assertEqual(
            os.path.normpath(EnvUtils.scene_artifact_path("_meta.json")),
            os.path.normpath(os.path.join(self.tmp_dir, "real_scene_meta.json")),
        )

    def test_scene_artifact_path_of_an_unsaved_scene_is_untitled_in_the_workspace(
        self,
    ):
        """Built on ``saved_scene_path``, so batch's phantom ``untitled`` path
        never passes for a folder the scene lives in."""
        cmds.file(new=True, force=True)
        path = EnvUtils.scene_artifact_path("_meta.json")
        self.assertEqual(os.path.basename(path), "untitled_meta.json")
        self.assertEqual(  # the workspace root carries a trailing slash
            os.path.normpath(os.path.dirname(path)),
            os.path.normpath(EnvUtils.default_artifact_dir()),
        )

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


class TestSceneWorkspace(MayaTkTestCase):
    """``scene_workspace`` / ``scene_source_images_dir``: the folders a tool's
    permanent output for the open scene resolves through -- the SCENE's
    project, not merely the active one. Twin of blendertk's. Added: 2026-10-13
    """

    def test_a_scene_in_another_project_resolves_that_projects_folders(self):
        import shutil
        import tempfile

        import pythontk as ptk

        root = os.path.realpath(tempfile.mkdtemp(prefix="scene_ws_test_"))
        self.addCleanup(shutil.rmtree, root, True)
        ptk.Workspace.create(root, create_dirs=False)
        os.makedirs(os.path.join(root, "scenes"))
        cmds.file(rename=os.path.join(root, "scenes", "shot.ma"))
        active = os.path.normcase(os.path.normpath(cmds.workspace(q=True, rd=True)))
        self.assertNotEqual(active, os.path.normcase(root))

        ws = DataNodes.scene_workspace()
        self.assertEqual(os.path.normcase(ws.root), os.path.normcase(root))
        folder = DataNodes.scene_source_images_dir("baked", create=True)
        self.assertEqual(
            os.path.normcase(folder),
            os.path.normcase(os.path.join(root, "sourceimages", "baked")),
        )
        self.assertTrue(os.path.isdir(folder))


class TestSceneBackups(MayaTkTestCase):
    """Backups land in the scene's own incremental-save folder
    (``ptk.Workspace.backup_dir``: ``<scene dir>/incrementalSave/<file>/``),
    where Maya's Increment & Save keeps them and the Reference Manager moves
    them with the scene -- never beside the scene, never in the project root.

    Regression (2026-10-13): ``save_scene_backup`` read the raw
    ``cmds.file(q=True, sceneName=True)``, which in batch is a phantom
    ``<active project>/untitled`` for an unsaved scene, so a pre-bake backup
    of an unsaved scene was written as ``untitled_prebake.ma`` into the user's
    default Maya project.
    """

    def setUp(self):
        super().setUp()
        import tempfile

        self.tmp_dir = os.path.realpath(tempfile.mkdtemp(prefix="scene_backup_test_"))

    def tearDown(self):
        import shutil

        cmds.file(new=True, force=True)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        super().tearDown()

    def _save_scene(self, name="shot.ma"):
        cmds.polyCube(name="backup_cube")
        path = os.path.join(self.tmp_dir, name)
        cmds.file(rename=path)
        cmds.file(save=True, type="mayaAscii", force=True)
        return path

    def test_an_unsaved_scene_writes_no_backup(self):
        """Batch's phantom name, reproduced: an EXTENSIONLESS ``untitled``
        in a project folder is what mayapy reports for a never-saved scene."""
        cmds.file(new=True, force=True)
        cmds.file(rename=os.path.join(self.tmp_dir, "untitled"))
        raw = cmds.file(query=True, sceneName=True) or ""
        self.assertFalse(os.path.splitext(raw)[1], raw)  # the phantom shape
        self.assertIsNone(EnvUtils.save_scene_backup(suffix="_prebake"))
        self.assertEqual(os.listdir(self.tmp_dir), [], "a backup was written")

    def test_a_backup_lands_in_the_scenes_incremental_save_folder(self):
        scene = self._save_scene()
        backup = EnvUtils.save_scene_backup(suffix="_prebake")
        expected = os.path.join(
            self.tmp_dir, "incrementalSave", "shot.ma", "shot_prebake.ma"
        )
        self.assertEqual(os.path.normcase(backup), os.path.normcase(expected))
        self.assertTrue(os.path.isfile(expected))
        self.assertFalse(
            os.path.exists(os.path.join(self.tmp_dir, "shot_prebake.ma")),
            "a backup still lands beside the scene",
        )
        self.assertEqual(
            os.path.normcase(EnvUtils.saved_scene_path()),
            os.path.normcase(scene.replace("\\", "/")),
            "the open scene keeps its own name",
        )

    def test_recovering_an_autosave_backs_the_original_up_beside_its_increments(
        self,
    ):
        original = self._save_scene()
        autosave = os.path.join(self.tmp_dir, "shot.0001.ma")
        cmds.file(rename=autosave)
        cmds.file(save=True, type="mayaAscii", force=True)

        EnvUtils.save_autosave_to_original(original_path=original)

        folder = os.path.join(self.tmp_dir, "incrementalSave", "shot.ma")
        self.assertTrue(os.path.isfile(os.path.join(folder, "shot.ma.bak")))
        self.assertFalse(os.path.exists(original + ".bak"))
        # A second recovery never overwrites the first backup.
        cmds.file(rename=autosave)
        cmds.file(save=True, type="mayaAscii", force=True)
        EnvUtils.save_autosave_to_original(original_path=original)
        self.assertEqual(
            len([f for f in os.listdir(folder) if f.endswith(".bak")]), 2
        )


class TestReferenceScene(MayaTkTestCase):
    """``reference_scene`` reads a USD layer the way the Reference Manager does:
    through mayaUsd's translator, NAMED -- Maya's extension guess references a keyed
    stage static -- and never a stage whose skins crash the reader."""

    def setUp(self):
        super().setUp()
        from mayatk.env_utils.usd import UsdUtils

        UsdUtils.load_plugin()
        self.paths = []

    def tearDown(self):
        cmds.file(new=True, force=True)  # release the layers before deleting
        for path in self.paths:
            try:
                os.remove(path)
            except OSError:
                pass
        super().tearDown()

    def _path(self, name):
        path = self.temp_path(name)
        if os.path.exists(path):  # a killed run's leftover
            os.remove(path)
        self.paths.append(path)
        return path

    def test_a_usd_layer_references_with_its_animation(self):
        path = self._path("_reference_scene_keyed.usda")
        cube = cmds.polyCube(name="ref_scene_cube")[0]
        cmds.setKeyframe(cube, attribute="translateX", t=1, v=0)
        cmds.setKeyframe(cube, attribute="translateX", t=10, v=4)
        cmds.select(cube)
        cmds.mayaUSDExport(file=path, selection=True, frameRange=(1, 10))
        cmds.file(new=True, force=True)

        EnvUtils.reference_scene(path)

        # Through the reference itself: with no namespace given, Maya PREFIXES the
        # node names rather than namespacing them (measured: ``<file>_ref_scene_cube``).
        ref_node = cmds.file(path, q=True, referenceNode=True)
        nodes = cmds.ls(
            cmds.referenceQuery(ref_node, nodes=True) or [], type="transform"
        )
        self.assertTrue(nodes, cmds.ls(type="transform"))
        self.assertTrue(
            cmds.keyframe(nodes[0], q=True, timeChange=True),
            "the referenced stage arrived static",
        )

    def test_a_usd_stage_the_reader_crashes_on_is_refused(self):
        from pxr import Usd, UsdGeom, UsdSkel

        from mayatk.env_utils.usd import UsdReadRefused

        path = self._path("_reference_scene_dq.usda")
        stage = Usd.Stage.CreateNew(path)
        prim = UsdGeom.Mesh.Define(stage, "/rig/limb").GetPrim()
        UsdSkel.BindingAPI.Apply(prim)
        UsdSkel.BindingAPI(prim).CreateSkinningMethodAttr().Set("dualQuaternion")
        stage.GetRootLayer().Save()
        del stage

        with self.assertRaises(UsdReadRefused):
            EnvUtils.reference_scene(path)
        self.assertEqual(EnvUtils.list_reference_nodes(), [])

    def _fbx(self, name, takes):
        """A keyed cube written with one take per entry of *takes* (Shots Only:
        the whole-timeline take dropped)."""
        import pythontk as ptk
        import maya.mel as mel

        from mayatk.env_utils.fbx_utils import FbxUtils

        self.addCleanup(mel.eval, "FBXImportSetTake -ti -1")
        path = self._path(name)
        cube = cmds.polyCube(name="ref_scene_cube")[0]
        for t, v in ((1, 0.0), (10, 4.0), (20, -2.0)):
            cmds.setKeyframe(cube, attribute="translateX", t=t, v=v)
        FbxUtils.apply_takes(takes)
        try:
            FbxUtils.export(path, objects=[cube])
        finally:
            FbxUtils.reset_takes()
        ptk.FbxMedia.drop_takes(path, names=["Take 001"])
        cmds.file(new=True, force=True)
        return path

    def test_an_fbx_reads_under_the_per_file_take_whatever_the_session_holds(self):
        """The plugin plays the take the session's sticky selector names: an
        index past the file's takes referenced NOTHING, silently (BACKLOG
        2026-10-10)."""
        import maya.mel as mel

        path = self._fbx(
            "_reference_scene_one_take.fbx", ({"name": "all", "start": 1, "end": 20},)
        )
        mel.eval("FBXImportSetTake -ti 5")

        EnvUtils.reference_scene(path)

        ref_node = cmds.file(path, q=True, referenceNode=True)
        nodes = cmds.ls(
            cmds.referenceQuery(ref_node, nodes=True) or [], type="transform"
        )
        self.assertTrue(nodes, "nothing was loaded")
        self.assertEqual(
            len(cmds.keyframe(nodes[0], attribute="translateX", q=True) or []), 20
        )

    def test_an_fbx_no_one_take_reads_whole_is_warned_about(self):
        path = self._fbx(
            "_reference_scene_shots.fbx",
            (
                {"name": "early", "start": 1, "end": 8},
                {"name": "late", "start": 12, "end": 20},
            ),
        )

        with self.assertLogs("mayatk.env_utils._env_utils", "WARNING") as logs:
            EnvUtils.reference_scene(path)

        self.assertIn("merged", " ".join(logs.output))


class TestSceneStatus(MayaTkTestCase):
    """``EnvUtils.scene_status``: the facts tentacle's HUD shows and warns on
    (blendertk's twin answers the same keys in Blender)."""

    def test_a_new_scene(self):
        cmds.file(new=True, force=True)
        status = EnvUtils.scene_status()
        self.assertEqual(
            set(status),
            {"symmetry", "xform_constraint", "workspace", "units", "fps",
             "time_unit", "default_fps", "autosave", "autosave_scene", "saved"},
        )
        self.assertFalse(status["saved"])
        self.assertFalse(status["autosave_scene"])
        self.assertEqual(status["units"], cmds.currentUnit(query=True, fullName=True))
        self.assertEqual(status["time_unit"], cmds.currentUnit(query=True, time=True))
        self.assertGreater(status["fps"], 0)
        self.assertIsInstance(status["autosave"], bool)
        self.assertIsNone(status["xform_constraint"])

    def test_the_default_rate_is_named(self):
        cmds.file(new=True, force=True)
        default = cmds.optionVar(query="workingUnitTimeDefault") or "film"
        unit = cmds.currentUnit(query=True, time=True)
        try:
            cmds.currentUnit(time=default)
            self.assertTrue(EnvUtils.scene_status()["default_fps"])
            cmds.currentUnit(time="ntsc" if default != "ntsc" else "pal")
            self.assertFalse(EnvUtils.scene_status()["default_fps"])
        finally:
            cmds.currentUnit(time=unit)


class TestWorkspaceSwitching(MayaTkTestCase):
    """``is_workspace`` / ``set_current_workspace``: what tentacle's shared
    Workspace tab calls through ``self.tk`` (blendertk's twins answer in Blender).
    """

    def setUp(self):
        super().setUp()
        import tempfile

        self.root = os.path.realpath(tempfile.mkdtemp(prefix="ws_switch_test_"))
        self._before = cmds.workspace(query=True, rd=True)

    def tearDown(self):
        import shutil

        cmds.workspace(self._before, openWorkspace=True)  # before the folder goes
        shutil.rmtree(self.root, ignore_errors=True)
        super().tearDown()

    def test_is_workspace_needs_a_workspace_mel(self):
        self.assertFalse(EnvUtils.is_workspace(self.root))
        self.assertFalse(EnvUtils.is_workspace(""))
        with open(os.path.join(self.root, "workspace.mel"), "w") as f:
            f.write("//Maya workspace stub\n")
        self.assertTrue(EnvUtils.is_workspace(self.root))

    def test_batch_opens_with_workspace_open(self):
        from mayatk.env_utils import _env_utils

        with unittest.mock.patch.object(_env_utils.cmds, "about", return_value=True):
            opened = EnvUtils.set_current_workspace(self.root + os.sep)
        self.assertEqual(opened, os.path.normpath(self.root))
        self.assertEqual(
            os.path.normcase(os.path.normpath(cmds.workspace(query=True, rd=True))),
            os.path.normcase(self.root),
        )

    def test_the_gui_opens_a_workspace_through_set_project(self):
        """File > Set Project's own MEL, so Maya's recent projects follow."""
        from mayatk.env_utils import _env_utils

        with open(os.path.join(self.root, "workspace.mel"), "w") as f:
            f.write("//Maya workspace stub\n")
        with unittest.mock.patch.object(
            _env_utils.cmds, "about", return_value=False
        ), unittest.mock.patch.object(_env_utils.mel, "eval") as ev:
            EnvUtils.set_current_workspace(self.root)
        ev.assert_called_once_with(f'setProject "{self.root.replace(os.sep, "/")}"')

    def test_the_gui_opens_an_unmarked_folder_without_set_project(self):
        """``setProject`` would write Maya's bare default marker into it."""
        from mayatk.env_utils import _env_utils

        with unittest.mock.patch.object(
            _env_utils.cmds, "about", return_value=False
        ), unittest.mock.patch.object(_env_utils.mel, "eval") as ev:
            EnvUtils.set_current_workspace(self.root)
        ev.assert_not_called()
        self.assertFalse(os.path.isfile(os.path.join(self.root, "workspace.mel")))
        self.assertEqual(
            os.path.normcase(os.path.normpath(cmds.workspace(query=True, rd=True))),
            os.path.normcase(self.root),
        )

    def test_no_folder_opens_nothing(self):
        self.assertEqual(
            EnvUtils.set_current_workspace(os.path.join(self.root, "missing")), ""
        )


class TestHostPreferences(MayaTkTestCase):
    """Working units and autosave: what tentacle's shared Preferences panel reads
    and writes through ``self.tk`` (blendertk's twins answer in Blender)."""

    def setUp(self):
        super().setUp()
        self._unit = cmds.currentUnit(query=True, linear=True)
        self._autosave = (
            cmds.autoSave(query=True, enable=True),
            cmds.autoSave(query=True, maxBackups=True),
            cmds.autoSave(query=True, int=True),
        )

    def tearDown(self):
        cmds.currentUnit(linear=self._unit)
        enabled, backups, interval = self._autosave
        cmds.autoSave(enable=enabled, maxBackups=backups, int=interval)
        super().tearDown()

    def test_the_app_names_itself(self):
        self.assertEqual(EnvUtils.APP_NAME, "Maya")

    def test_linear_unit_round_trip(self):
        self.assertEqual(EnvUtils.set_linear_unit("Meter"), "meter")
        self.assertEqual(EnvUtils.get_linear_unit(), "meter")
        self.assertIn(EnvUtils.get_linear_unit(), EnvUtils.SCENE_UNIT_VALUES)
        with self.assertRaises(ValueError):
            EnvUtils.set_linear_unit("parsec")

    def test_frame_rates_are_mayas_named_units(self):
        import pythontk as ptk

        rates = EnvUtils.frame_rates()
        self.assertEqual(rates, dict(ptk.VidUtils.FRAME_RATES))
        self.assertEqual(rates["film"], 24.0)

    def test_autosave_round_trip(self):
        EnvUtils.set_autosave_settings(max_backups=0)
        self.assertEqual(EnvUtils.autosave_settings()["max_backups"], 0)
        self.assertFalse(cmds.autoSave(query=True, enable=True))
        EnvUtils.set_autosave_settings(max_backups=5, interval=7)
        settings = EnvUtils.autosave_settings()
        self.assertEqual(settings["max_backups"], 5)
        self.assertAlmostEqual(settings["interval"], 7.0)
        self.assertTrue(cmds.autoSave(query=True, limitBackups=True))


class TestPythonExecutable(MayaTkTestCase):
    """``python_executable``: the interpreter tentacle's updater drives pip with
    (blendertk's twin answers Blender's bundled Python)."""

    def test_it_is_this_mayas_mayapy(self):
        path = EnvUtils.python_executable()
        self.assertEqual(path, EnvUtils.get_env_info("mayapy"))
        self.assertTrue(os.path.isfile(path), path)


class TestOpenScene(MayaTkTestCase):
    """``open_scene``: the open the Scene panel's Recent Files and Autosave rows run."""

    def test_a_saved_scene_opens_and_a_missing_one_does_not(self):
        import pythontk as ptk

        store = ptk.TempArtifacts.scoped("mtk_open_scene_test")
        self.addCleanup(store.cleanup)
        path = os.path.join(store.dir_path(), "opened.ma")
        cmds.polyCube(name="open_scene_cube")
        cmds.file(rename=path)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)
        self.assertTrue(EnvUtils.open_scene(path))
        self.assertTrue(cmds.objExists("open_scene_cube"))
        self.assertFalse(EnvUtils.open_scene(os.path.join(store.dir_path(), "gone.ma")))
        self.assertFalse(EnvUtils.open_scene(""))


class TestSceneFileCommands(MayaTkTestCase):
    """``get_file_commands`` / ``run_file_command``: Maya's own import / export
    commands, by the names the Scene panel lists them under."""

    def test_maya_s_lists_and_the_windows_only_sends(self):
        self.assertEqual(
            EnvUtils.get_file_commands("import")[:2], ["Import File", "Import Options"]
        )
        with unittest.mock.patch.object(sys, "platform", "linux"):
            linux = EnvUtils.get_file_commands("export")
        with unittest.mock.patch.object(sys, "platform", "win32"):
            windows = EnvUtils.get_file_commands("export")
        self.assertIn("Export Selection", linux)
        self.assertNotIn("GoZ", linux)
        self.assertIn("GoZ", windows)

    def test_an_unknown_direction_or_name_is_a_value_error(self):
        with self.assertRaises(ValueError):
            EnvUtils.get_file_commands("sideways")
        with self.assertRaises(ValueError):
            EnvUtils.run_file_command("Import Nothing")

    def test_a_command_runs_its_mel(self):
        with unittest.mock.patch("mayatk.env_utils._env_utils.mel") as mel:
            EnvUtils.run_file_command("Export All")
        mel.eval.assert_called_once_with("Export")

    def test_a_preset_editor_whose_plugin_will_not_load_says_so(self):
        with unittest.mock.patch(
            "mayatk.env_utils._env_utils.Plugins.load",
            side_effect=RuntimeError("not installed"),
        ), unittest.mock.patch("mayatk.env_utils._env_utils.mel") as mel:
            with self.assertRaises(RuntimeError) as caught:
                EnvUtils.run_file_command("FBX Import Presets")
            mel.eval.assert_not_called()
        self.assertIn("fbxmaya", str(caught.exception))

    def test_export_selection_opens_without_the_fbx_plugin(self):
        with unittest.mock.patch(
            "mayatk.env_utils._env_utils.Plugins.load",
            side_effect=RuntimeError("not installed"),
        ), unittest.mock.patch("mayatk.env_utils._env_utils.mel") as mel:
            EnvUtils.run_file_command("Export Selection")
        mel.eval.assert_called_once_with("ExportSelection")


class TestSceneTriangleCount(MayaTkTestCase):
    """``scene_triangle_count``: what the Scene panel's dense-export warning weighs."""

    def test_the_scene_and_the_selection(self):
        cube = cmds.polyCube(name="tri_cube")[0]
        cmds.polyCube(name="tri_other")
        cmds.select(cube)
        self.assertEqual(EnvUtils.scene_triangle_count(selection_only=True), 12)
        self.assertEqual(EnvUtils.scene_triangle_count(), 24)
        cmds.select(clear=True)
        self.assertEqual(EnvUtils.scene_triangle_count(selection_only=True), 0)


class TestForeignScene(MayaTkTestCase):
    """``foreign_scene_bridge`` / ``foreign_scene_import``: the other DCC is Blender."""

    def test_the_bridge_and_the_pull_engine(self):
        from mayatk.env_utils.blender_bridge._blender_bridge import BlenderBridge
        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        self.assertIs(EnvUtils.foreign_scene_bridge(), BlenderBridge)
        self.assertEqual(BlenderBridge.spec.app.name, "Blender")
        self.assertIs(EnvUtils.foreign_scene_import(), BlenderSceneImport)
        self.assertEqual(BlenderSceneImport.SCENE_EXTENSIONS, (".blend",))


class TestExportScene(MayaTkTestCase):
    """``export_scene``: Export Scene's writer, one route per format."""

    OPTIONS = dict(
        include_cameras=True,
        include_lights=True,
        include_skins=True,
        include_tangents=True,
        embed_textures=False,
    )

    def setUp(self):
        super().setUp()
        import pythontk as ptk

        self.ptk = ptk
        self.store = ptk.TempArtifacts.scoped("mtk_export_scene_test")
        self.addCleanup(self.store.cleanup)
        self.out = os.path.join(self.store.dir_path(), "asset.glb")
        self.fbx = []

    def _write_fbx(self, file_path, **_):
        self.fbx.append(file_path)
        with open(file_path, "w") as fh:
            fh.write("fbx")

    @staticmethod
    def _convert(src, **_):
        glb = os.path.splitext(src)[0] + ".glb"
        with open(glb, "w") as fh:
            fh.write("glb")
        return glb

    def _glb_patches(self, stack, write=None):
        """Stand in for the FBX write, the conversion and the record staging."""
        import contextlib

        from mayatk.env_utils.fbx_utils import FbxUtils

        stack.enter_context(
            unittest.mock.patch.object(
                EnvUtils, "export_scene_as_fbx", new=write or self._write_fbx
            )
        )
        stack.enter_context(
            unittest.mock.patch.object(
                self.ptk.MeshConvert, "fbx_to_glb", new=self._convert
            )
        )
        return stack.enter_context(
            unittest.mock.patch.object(
                FbxUtils,
                "export_prepared",
                return_value=contextlib.nullcontext(),
            )
        )

    def test_an_unknown_format_is_a_value_error(self):
        with self.assertRaises(ValueError):
            EnvUtils.export_scene(self.out, "abc")

    def test_obj_writes_the_file(self):
        cmds.polyCube(name="obj_scene_cube")
        out = os.path.join(self.store.dir_path(), "scene.obj")
        self.assertEqual(EnvUtils.export_scene(out, "obj"), out)
        self.assertTrue(os.path.isfile(out))

    def test_the_glb_intermediate_fbx_folder_is_gone_after_a_good_export(self):
        """Regression (2026-10-13): the intermediate came from a detached store that
        nothing released, so every GLB export left a full-scene FBX in temp."""
        import contextlib

        with contextlib.ExitStack() as stack:
            prepared = self._glb_patches(stack)
            EnvUtils.export_scene(self.out, "glb", **self.OPTIONS)
        prepared.assert_called_once_with(only=[self.ptk.SceneRecords.MATERIALS])
        self.assertTrue(os.path.isfile(self.out))
        self.assertEqual(len(self.fbx), 1)
        self.assertNotEqual(os.path.dirname(self.fbx[0]), os.path.dirname(self.out))
        self.assertFalse(os.path.exists(os.path.dirname(self.fbx[0])))

    def test_a_selection_glb_gives_the_selection_back(self):
        """The carrier rides the write, then the user's selection is theirs again.
        Regression (2026-10-24): ``data_export`` stayed selected after the export."""
        import contextlib

        cube = cmds.polyCube(name="glb_sel_geo")[0]
        carrier = cmds.createNode("transform", name="glb_sel_carrier")
        cmds.select(cube, replace=True)
        selected_at_write = []

        def write(file_path, **_):
            selected_at_write.extend(cmds.ls(selection=True))
            self._write_fbx(file_path)

        with contextlib.ExitStack() as stack:
            self._glb_patches(stack, write=write)
            stack.enter_context(
                unittest.mock.patch.object(
                    DataNodes, "get_export_nodes", return_value=[carrier]
                )
            )
            EnvUtils.export_scene(self.out, "glb", selection_only=True, **self.OPTIONS)
        self.assertEqual(sorted(selected_at_write), sorted([cube, carrier]))
        self.assertEqual(cmds.ls(selection=True), [cube])


if __name__ == "__main__":
    unittest.main()
