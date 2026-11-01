# !/usr/bin/python
# coding=utf-8
"""
Test Suite for mayatk.env_utils.reference_manager -- the engine.

Its panel is tentacle's shared Reference Manager; the panel's own behavior is
tentacle's ``test/test_reference_manager_panel.py``.
"""

import contextlib
import unittest
import os
import shutil
import tempfile
from unittest.mock import patch, call, MagicMock, PropertyMock

import pythontk as ptk
import mayatk.env_utils.reference_manager as ref_mgr
from mayatk.env_utils.reference_manager import _reference_manager as ref_engine


class MockLogger:
    def debug(self, msg):
        pass

    def warning(self, msg):
        pass

    def info(self, msg):
        pass

    def error(self, msg):
        pass

    def setLevel(self, level):
        pass


class TestBakeBackedReferences(unittest.TestCase):
    """The engine's host facts, and its bake-backed references: an FBX no one take
    reads whole is referenced through a cached ``.ma`` bake (run through the
    ``bake_for_reference`` collaborator, a panel's progress-and-prompt bake), and every
    bake-backed reference follows its source as it is NOW (``follow_bake_sources``).
    Moved from the panel controller's suite with the panel; the panel's half is
    tentacle's ``test_reference_manager_panel.py``."""

    def setUp(self):
        self.engine = ref_engine.ReferenceManager()
        patcher = patch.object(ref_engine.cmds, "file", create=True, return_value="")
        self._mock_cmds_file = patcher.start()
        self.addCleanup(patcher.stop)

    def test_is_foreign_classifies_blend_only(self):
        """.blend is the cross-DCC row and nothing else -- .fbx and USD are NATIVE on
        this side (Maya references both directly, through their translators)."""
        C = ref_engine.ReferenceManager
        self.assertTrue(C.is_foreign("C:/proj/mesh.blend"))
        self.assertFalse(C.is_foreign("C:/proj/scene.ma"))
        self.assertFalse(C.is_foreign("C:/proj/scene.mb"))
        self.assertFalse(C.is_foreign("C:/proj/prop.fbx"))
        for ext in ptk.USD_EXTENSIONS:
            self.assertFalse(C.is_foreign(f"C:/proj/set{ext}"), ext)
        self.assertFalse(C.is_foreign(""))

    def test_the_host_facts_are_the_inverse_of_blendertk(self):
        """This side's natives are .ma/.mb/.fbx plus every USD spelling, its only
        foreign type .blend; a USD row imports with no reference (and no conversion,
        so nothing to ask), and a rig travels transferred or baked, never raw."""
        C = ref_engine.ReferenceManager
        self.assertEqual(
            C.NATIVE_EXTENSIONS, (".ma", ".mb", ".fbx", *ptk.USD_EXTENSIONS)
        )
        self.assertEqual(C.FOREIGN_EXTENSIONS, (".blend",))
        self.assertEqual(C.INCLUDE_DEFAULTS, (".ma", ".mb"))
        self.assertEqual(C.IMPORT_EXTENSIONS, (".blend", *ptk.USD_EXTENSIONS))
        self.assertEqual(C.SCENE_SAVE_EXTENSION, ".ma")
        self.assertEqual(C.RIG_MODES, ("rig", "bake"))
        self.assertEqual(C.NAMESPACE_MODES, ("remove", "keep", "root"))
        self.assertTrue(C.needs_conversion("C:/proj/mesh.blend"))
        self.assertFalse(C.needs_conversion("C:/proj/set.usda"))

    def test_workspace_scan_covers_every_native_type(self):
        """The workspace-file cache scans the natively referenceable superset, so toggling
        an Include Type only re-filters -- it never has to re-scan disk."""
        self.assertEqual(
            set(ref_engine.ReferenceManager.SCENE_FILE_TYPES),
            {f"*{e}" for e in ref_engine.ReferenceManager.NATIVE_EXTENSIONS},
        )

    def _no_references(self):
        return patch.object(
            ref_engine.ReferenceManager,
            "current_references",
            new_callable=PropertyMock,
            return_value=[],
        )

    def _recording_progress(self):
        """A ``progress=`` collaborator recording each scope it opens (by its text)."""
        opened = []

        def progress(text):
            opened.append(text)
            return contextlib.nullcontext(lambda *a: True)

        return progress, opened

    def test_a_multi_take_fbx_bakes_through_the_bake_collaborator(self):
        """An FBX no one take reads whole (``FbxUtils._native_read_gap``) is baked,
        through the panel's bake (``bake_for_reference=``: footer progress, Esc-hold
        stop); its first take read runs in the panel's progress (``progress=``); a
        stopped bake references nothing."""
        from mayatk.env_utils.fbx_utils import FbxUtils

        store = ptk.TempArtifacts("mtk_rm_fbx_route", policy="scoped")
        self.addCleanup(store.cleanup)
        path = os.path.join(store.dir_path(), "shots.fbx")
        with open(path, "wb") as fh:
            fh.write(b"fbx")
        baked = []
        progress, busy = self._recording_progress()
        engine = ref_engine.ReferenceManager(
            progress=progress,
            bake_for_reference=baked.append,  # None: the user stopped it
        )
        gap = "merged: 2 takes merged into one (early, late)"
        with (
            self._no_references(),
            patch.object(FbxUtils, "_native_read_gap", return_value=gap),
        ):
            self.assertFalse(engine.add_reference("shots", path))
        self.assertEqual(baked, [path])
        self.assertEqual(busy, ["Reading the takes of shots.fbx"])
        self.assertFalse(
            [
                c
                for c in self._mock_cmds_file.call_args_list
                if c.kwargs.get("reference")
            ]
        )

    def test_a_file_s_takes_are_read_once_per_version(self):
        """A large FBX's take layout takes seconds (BACKLOG review: ~19 s on a
        133 MB, 13-take file), and it was read again on every add and every
        Open, on the UI thread before any progress showed. A version read before
        answers at once; a first read runs in the panel's progress."""
        from mayatk.env_utils.fbx_utils import FbxUtils

        store = ptk.TempArtifacts("mtk_rm_fbx_memo", policy="scoped")
        self.addCleanup(store.cleanup)
        path = os.path.join(store.dir_path(), "single.fbx")
        with open(path, "wb") as fh:
            fh.write(b"fbx")
        progress, busy = self._recording_progress()
        engine = ref_engine.ReferenceManager(progress=progress)
        with patch.object(FbxUtils, "_native_read_gap", return_value=None) as read:
            self.assertIsNone(engine._read_fbx_takes(path))
            self.assertIsNone(engine._read_fbx_takes(path))
            self.assertEqual((read.call_count, len(busy)), (1, 1))
            stat = os.stat(path)  # a re-export: a new version of the file
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
            engine._read_fbx_takes(path)
            self.assertEqual((read.call_count, len(busy)), (2, 2))

    def _bake_files(self, in_temp=False):
        """A source FBX in a project and its bake: in the project's
        ``cache/bridge`` (or, *in_temp*, where a bake made before 2026-10-13
        lives)."""
        store = ptk.TempArtifacts("mtk_rm_fbx_follow", policy="scoped")
        self.addCleanup(store.cleanup)
        root = tempfile.mkdtemp(prefix="mtk_rm_follow_proj_")
        self.addCleanup(shutil.rmtree, root, True)
        source = os.path.join(root, "scenes", "shots.fbx")
        home = store.dir_path() if in_temp else os.path.join(root, "cache", "bridge")
        bake = os.path.join(home, "mtk_bake_cache_shots_0123abcd.ma")
        for path in (source, bake):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write("x")
        ref = MagicMock()
        ref.path, ref.namespace, ref._ref_node = bake, "shots", "shotsRN"
        return source, bake, ref

    def _follow(
        self, ref, source, current, loaded=True, bake=None, key="v1", rehome=None
    ):
        """Run the follow with the project's bake of *source* holding it
        (*current*: the slot) or not (None) at version *key*. One engine across
        calls: it remembers the source versions a re-bake failed on."""
        from mayatk.env_utils.blender_bridge import _scene_import as si

        rebaked = []
        slot = ref.path
        # What ``bake_for_reference=`` installs: the panel's bake.
        self.engine._bake_for_reference = bake or (
            lambda p, project=None: rebaked.append(p) or slot
        )
        with (
            patch.object(
                ref_engine.cmds, "referenceQuery", create=True, return_value=loaded
            ),
            patch.object(ref_engine.cmds, "workspace", create=True, return_value=""),
            patch.object(
                ref_engine.ReferenceManager,
                "current_references",
                new_callable=PropertyMock,
                return_value=[ref],
            ),
            patch.object(
                ref_engine.ReferenceManager,
                "bake_source",
                staticmethod(lambda p: source if p == ref.path else None),
            ),
            patch.object(
                si.BlenderSceneImport,
                "_current_bake",
                classmethod(lambda cls, src, *a, **k: current),
            ),
            patch.object(
                si.BlenderSceneImport,
                "_bake_key",
                staticmethod(lambda src, *a, **k: key),
            ),
            patch.object(
                si.BlenderSceneImport,
                "rehome_bake",
                rehome or (lambda self, path, project=None: None),
            ),
        ):
            moved = self.engine.follow_bake_sources()
        return moved, rebaked

    def test_a_current_bake_is_left_alone(self):
        source, bake, ref = self._bake_files()

        moved, rebaked = self._follow(ref, source, current=bake)

        self.assertEqual((moved, rebaked), ([], []))

    def test_a_bake_its_source_moved_past_is_baked_again_and_reloaded(self):
        """The re-bake replaces the one slot the reference reads: the reference
        is reloaded in place (unloaded first, so the load reads the new file)."""
        source, bake, ref = self._bake_files()

        moved, rebaked = self._follow(ref, source, current=None)

        self.assertEqual((moved, rebaked), ([ref], [source]))
        self.assertEqual(
            self._mock_cmds_file.call_args_list[-2:],
            [call(unloadReference="shotsRN"), call(bake, loadReference="shotsRN")],
        )

    def test_a_bake_that_failed_is_not_retried_until_its_source_moves(self):
        """The follow runs on every list rebuild: a bake that failed or was
        stopped (Esc-hold) must not run again at each one -- minutes of child
        mayapy, or the prompt, every time. A newer source tries again."""
        source, bake, ref = self._bake_files()
        attempts = []

        for key in ("v2", "v2", "v3"):
            moved, _ = self._follow(
                ref,
                source,
                current=None,
                key=key,
                bake=lambda p, project=None: attempts.append(p),
            )
            self.assertEqual(moved, [])

        self.assertEqual(attempts, [source, source])

    def test_an_unloaded_reference_stays_unloaded_when_repointed(self):
        source, bake, ref = self._bake_files()

        moved, _ = self._follow(ref, source, current=None, loaded=False)

        self.assertEqual(moved, [ref])
        self.assertEqual(
            self._mock_cmds_file.call_args_list[-2:],
            [
                call(bake, loadReference="shotsRN"),
                call(unloadReference="shotsRN"),
            ],
        )

    def test_a_reference_into_temp_is_rehomed_into_the_project(self):
        """A scene saved before 2026-10-13 references a bake in temp, which the
        age sweep reclaims: the follow repoints it onto the project's bake."""
        source, old, ref = self._bake_files(in_temp=True)
        home = os.path.join(os.path.dirname(os.path.dirname(source)), "cache")
        slot = os.path.join(home, "bridge", os.path.basename(old))
        seen = []

        def rehome(engine, path, project=None):
            seen.append(path)
            return slot

        moved, rebaked = self._follow(ref, source, current=slot, rehome=rehome)

        self.assertEqual((seen, moved, rebaked), ([old], [ref], []))
        self._mock_cmds_file.assert_any_call(slot, loadReference="shotsRN")

    def test_a_rehome_that_cannot_encode_the_bake_skips_it(self):
        """Pre-release review 2026-10-13: a re-home's UnicodeEncodeError (a
        ValueError) escaped the follow, which caught only OSError and
        RuntimeError -- the whole panel refresh failed on one old bake."""
        source, old, ref = self._bake_files(in_temp=True)

        def rehome(engine, path, project=None):
            raise UnicodeEncodeError("utf-8", "\udce9", 0, 1, "surrogates")

        moved, rebaked = self._follow(ref, source, current=old, rehome=rehome)

        self.assertEqual((moved, rebaked), ([], []))

    def test_a_bake_of_another_project_is_named_in_full(self):
        """Pre-release review 2026-10-13: the spelling was relative to the
        ACTIVE project whenever the bake lay under it, so a bake of a nested
        project (the scene's own, inside a broad active one) was named from the
        wrong root -- and resolved to nothing once that project was active."""
        root = os.path.join(tempfile.gettempdir(), "mtk_rm_rel_broad")
        nested = os.path.join(root, "room")
        bake = os.path.join(nested, "cache", "bridge", "mtk_bake_cache_x_0123abcd.ma")
        with (
            patch.object(ref_engine.cmds, "workspace", create=True, return_value=root),
            patch.object(
                ref_engine.DataNodes, "scene_project_root", return_value=nested
            ),
        ):
            self.assertEqual(self.engine._reference_spelling(bake), bake)

    def test_a_bake_is_named_relative_to_the_project(self):
        """Referenced through the project, so the scene keeps its bake when the
        project moves or syncs elsewhere (Maya resolves it via the project)."""
        root = os.path.join(tempfile.gettempdir(), "mtk_rm_rel_proj")
        bake = os.path.join(root, "cache", "bridge", "mtk_bake_cache_x_0123abcd.ma")
        with (
            patch.object(ref_engine.cmds, "workspace", create=True, return_value=root),
            patch.object(ref_engine.DataNodes, "scene_project_root", return_value=root),
        ):
            self.assertEqual(
                self.engine._reference_spelling(bake),
                "cache/bridge/mtk_bake_cache_x_0123abcd.ma",
            )
            outside = os.path.join(tempfile.gettempdir(), "elsewhere", "b.ma")
            self.assertEqual(self.engine._reference_spelling(outside), outside)


class TestOpenSceneClearsModifiedFlag(unittest.TestCase):
    """open_scene must not leave Maya's load-time 'modified' flag set.

    Regression: opening a reference-bearing scene via the Open icon leaves
    ``cmds.file(q=True, modified=True)`` True (reference edits are applied during
    load), so an immediate close/reference toggle falsely prompted "unsaved
    changes — close anyway?" even though the user made no edits.
    """

    def test_open_scene_resets_modified_flag(self):
        engine = ref_engine.ReferenceManager()
        with (
            patch.object(ref_engine.os.path, "exists", return_value=True),
            patch.object(ref_engine.cmds, "file", create=True) as mock_file,
        ):
            result = engine.open_scene("/proj/scenes/shot.ma", set_workspace=False)

        self.assertTrue(result)
        # The open call, then an explicit clear of the load-time dirty flag.
        mock_file.assert_any_call("/proj/scenes/shot.ma", open=True, force=True)
        mock_file.assert_any_call(modified=False)


class TestForeignScratch(unittest.TestCase):
    """Foreign rows open from ``<temp root>/mtk_opened/mtk_opened_<hash>/<stem>_<ext>.ma`` and the
    untouched scratch is discarded on close (mirror of blendertk's tests)."""

    def test_scratch_name_carries_the_source_type_and_is_per_source(self):
        path_for = ref_engine.ReferenceManager.foreign_scratch_path
        a = path_for("/projA/scenes/shot.blend")
        b = path_for("/projB/scenes/shot.blend")
        self.assertEqual(os.path.basename(a), "shot_blend.ma")
        self.assertTrue(os.path.basename(os.path.dirname(a)).startswith("mtk_opened_"))
        # The twins' store owns its own folder under the ecosystem temp root.
        self.assertEqual(
            os.path.normcase(os.path.dirname(os.path.dirname(a))),
            os.path.normcase(os.path.join(ptk.TempArtifacts.root(), "mtk_opened")),
        )
        self.assertNotEqual(os.path.dirname(a), os.path.dirname(b))
        self.assertEqual(path_for("/projA/scenes/shot.blend"), a)

    def test_store_is_one_process_wide_scratch_twins(self):
        # The engine delegates naming + untouched-vs-saved discard to ptk.ScratchTwins
        # (pinned in pythontk's own tests); here only the wiring is checked.
        twins = ref_engine._scratch_twins()
        self.assertIs(twins, ref_engine._scratch_twins())
        self.assertIsInstance(twins, ref_engine.ptk.ScratchTwins)
        self.assertEqual(twins.extension, ".ma")


class TestForeignOpenStandsInForItsSource(unittest.TestCase):
    """A foreign row opens through a scratch twin in temp that stands in for its
    SOURCE (``DataNodes.set_scene_source``), so the scene's project -- the
    boundary a lightmap bake, Keep Names In Sync and the Texture Path Editor
    keep -- is the source's, not the temp folder (BACKLOG 2026-10-04, decided
    2026-10-05). The bake comes through the engine's ``bake_for_reference``
    collaborator (a panel's progress-and-prompt bake). Mirror of blendertk's
    open-as-new check."""

    def test_open_scene_records_the_scratch_as_standing_in_for_its_source(self):
        engine = ref_engine.ReferenceManager(
            bake_for_reference=lambda path, project=None: "/cache/room.ma"
        )
        source = "/proj/scenes/room.blend"
        scratch = "/temp/mtk_opened_0123456789/room_blend.ma"
        twins = MagicMock()
        twins.create.return_value = scratch
        self.addCleanup(ref_engine.DataNodes.set_scene_source, scratch, None)
        with (
            patch.object(ref_engine, "_scratch_twins", return_value=twins),
            patch.object(ref_engine.os.path, "exists", return_value=True),
            patch.object(ref_engine.cmds, "file", create=True) as mock_file,
        ):
            self.assertTrue(engine.open_scene(source, set_workspace=False))
        twins.create.assert_called_once_with(source, "/cache/room.ma")
        mock_file.assert_any_call(scratch, open=True, force=True)
        self.assertEqual(
            ref_engine.DataNodes.scene_source(scratch), os.path.abspath(source)
        )

    def test_a_foreign_row_bakes_into_its_sources_project(self):
        """Pre-release review 2026-10-13: Open baked a foreign row into the
        OPEN scene's project (the bake's default), so opening another
        project's .blend left its bake -- a file the opened scene references --
        in a project that scene never sees."""
        seen = []
        engine = ref_engine.ReferenceManager(
            bake_for_reference=lambda path, project=None: seen.append(project)
        )
        proj = tempfile.mkdtemp(prefix="mtk_rm_open_proj_")
        self.addCleanup(shutil.rmtree, proj, True)
        ref_engine.ptk.Workspace.create(proj, create_dirs=False)
        source = os.path.join(proj, "scenes", "room.blend")
        os.makedirs(os.path.dirname(source))
        with open(source, "wb") as fh:
            fh.write(b"BLENDER")
        # No bake came back: nothing is opened.
        self.assertFalse(engine.open_scene(source, set_workspace=False))
        self.assertEqual(
            [os.path.normcase(os.path.normpath(p or "")) for p in seen],
            [os.path.normcase(os.path.normpath(proj))],
        )

    def test_a_scratch_copy_that_cannot_be_written_refuses_the_open(self):
        """Pre-release review 2026-10-13: when the scratch copy failed, the
        project's bake itself was opened -- and a save then wrote over the
        file every scene referencing the row reads. Now nothing opens and the
        error names why (a panel shows it)."""
        engine = ref_engine.ReferenceManager(
            bake_for_reference=lambda path, project=None: "/cache/room.ma"
        )
        twins = MagicMock()
        twins.create.side_effect = OSError("disk full")
        with (
            patch.object(ref_engine, "_scratch_twins", return_value=twins),
            patch.object(ref_engine.os.path, "exists", return_value=True),
            patch.object(ref_engine.cmds, "file", create=True) as mock_file,
        ):
            with self.assertRaises(OSError) as raised:
                engine.open_scene("/proj/scenes/room.blend", set_workspace=False)
        self.assertIn("disk full", str(raised.exception))
        opened = [c for c in mock_file.call_args_list if c.kwargs.get("open")]
        self.assertEqual(opened, [], "nothing was opened")


class _OnDiskRename:
    """Fixture for renames against REAL files: a scoped temp workspace ``proj``
    holding its ``scenes`` folder, and an engine browsing it. What a panel's Rename
    prompt makes of a typed name is tentacle's; here the engine gets the arguments
    the panel passes under a ``{scenes}/{name}`` structure with suffix ``_v01``."""

    SUFFIX = "_v01"

    def setUp(self):
        self._store = ptk.TempArtifacts("mtk_rm_rename_disk_test", policy="scoped")
        self.workspace = os.path.join(self._store.dir_path(), "proj")
        self.scenes = os.path.join(self.workspace, "scenes")
        os.makedirs(self.scenes)
        self.boxes = []
        self.engine = ref_engine.ReferenceManager(report=self.boxes.append)
        # The browsed workspace (its scenes folder resolves under it): the guards
        # that keep Rename / Delete off the project's own structure read both.
        self.engine._current_working_dir = self.workspace
        self.engine.is_current = lambda path, current=None: False

    def tearDown(self):
        self._store.cleanup()

    def _touch(self, *parts):
        path = os.path.join(self.scenes, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
        return path

    def _listing(self, *parts):
        return sorted(os.listdir(os.path.join(self.scenes, *parts)))

    def _rename(self, base):
        """Rename the scene to *base* as the panel's prompt does: the file gets the
        suffix, its per-scene folder the bare name."""
        new = os.path.join(os.path.dirname(self.old), base + self.SUFFIX + ".ma")
        return self.engine.rename_scene_file(self.old, new, folder=base)


class TestCaseOnlyRename(_OnDiskRename, unittest.TestCase):
    """A rename that changes only the name's case goes through -- real files.

    Regression: on a case-insensitive file system (Windows) ``os.path.exists``
    reports ``Hero_v01.ma`` present while ``hero_v01.ma`` -- the very file being
    renamed -- is, so a rename refused it ("Target file exists") and the
    per-scene folder refused ``hero`` -> ``Hero`` the same way; the folder's
    ownership test was also case-sensitive, unlike Delete's. ``os.rename`` does
    case-only renames fine; the guards now let a target through when it IS the
    source (``ptk.FileUtils.is_same_file``). Real files, so the tests hold on
    either kind of file system. Added: 2026-09-23.
    """

    def setUp(self):
        super().setUp()
        self.old = self._touch("hero", "hero_v01.ma")

    def test_a_rename_changes_only_the_case(self):
        self._rename("Hero")
        self.assertEqual(self.boxes, [])
        self.assertEqual(self._listing(), ["Hero"])  # the folder too
        self.assertEqual(self._listing("Hero"), ["Hero_v01.ma"])

    def test_a_folder_differing_only_in_case_is_still_the_scenes_own(self):
        os.rename(os.path.join(self.scenes, "hero"), os.path.join(self.scenes, "Hero"))
        self.old = os.path.join(self.scenes, "Hero", "hero_v01.ma")
        self._rename("villain")
        self.assertEqual(self._listing(), ["villain"])
        self.assertEqual(self._listing("villain"), ["villain_v01.ma"])


class TestPerSceneFolderMovesOnlyWithItsOwnScene(_OnDiskRename, unittest.TestCase):
    """A rename takes the scene's folder along only when the folder is its alone.

    Regression: ownership was a bare name-prefix test, so a scene loose in the
    scenes root whose name starts with the root's -- ``scenes/scenes_final.ma``
    -- "owned" ``scenes/``, and renaming it renamed the whole scenes folder with
    every other scene in it (Delete guards the same coincidence). A folder
    shared with other scenes (``hero_v01`` beside ``hero_v02``) moved with one of
    them the same way, filing the rest under a name no longer theirs. A folder
    now moves only when it holds no other Maya scene at any depth -- the scene's
    own incremental saves excepted. Added: 2026-09-23.
    """

    def test_a_loose_scene_never_moves_the_scenes_root(self):
        self.old = self._touch("scenes_final.ma")
        self._touch("hero", "hero_v01.ma")
        self._rename("final")
        self.assertTrue(os.path.isdir(self.scenes), "the scenes root was renamed")
        self.assertEqual(self._listing(), ["final_v01.ma", "hero"])

    def test_a_folder_shared_with_other_scenes_stays(self):
        self.old = self._touch("hero", "hero_v01.ma")
        self._touch("hero", "hero_v02.ma")
        self._rename("villain")
        self.assertEqual(self._listing(), ["hero"])
        self.assertEqual(self._listing("hero"), ["hero_v02.ma", "villain_v01.ma"])

    def test_the_scenes_own_incremental_saves_do_not_hold_it_back(self):
        self.old = self._touch("hero", "hero_v01.ma")
        self._touch("hero", "incrementalSave", "hero_v01.ma", "hero_v01.0001.ma")
        self._rename("villain")
        self.assertEqual(self._listing(), ["villain"])
        self.assertEqual(
            self._listing("villain", "incrementalSave", "villain_v01.ma"),
            ["hero_v01.0001.ma"],
        )

    def test_a_folder_that_cannot_be_read_is_left_alone(self):
        def _walk(top, onerror=None):
            onerror(PermissionError(13, "Access is denied", top))
            return iter(())

        holds = ref_engine._ReferenceManagerInternal._holds_other_scenes
        with patch.object(ref_engine.os, "walk", side_effect=_walk):
            self.assertTrue(holds(self.scenes, os.path.join(self.scenes, "a.ma")))

    def test_a_missing_folder_holds_nothing(self):
        holds = ref_engine._ReferenceManagerInternal._holds_other_scenes
        gone = os.path.join(self.scenes, "gone")
        self.assertFalse(holds(gone, os.path.join(gone, "a.ma")))

    def test_a_scene_alone_in_the_scenes_root_never_moves_it(self):
        # No OTHER Maya scene anywhere below (a fresh project; its other rows are
        # FBX), so "holds other scenes" cannot protect the root: the structure
        # guard has to. Regression: the scenes root was renamed to "final".
        self.old = self._touch("scenes_final.ma")
        self._touch("props", "chair.fbx")
        self._rename("final")
        self.assertTrue(os.path.isdir(self.scenes), "the scenes root was renamed")
        self.assertEqual(self._listing(), ["final_v01.ma", "props"])

    def test_a_scene_at_the_workspace_root_never_moves_the_workspace(self):
        # "proj/proj_v01.ma" is named for the project folder itself; with no other
        # Maya scene in the project, Rename renamed the whole project to "final".
        self.old = os.path.join(self.workspace, "proj_v01.ma")
        open(self.old, "w").close()
        self._rename("final")
        self.assertTrue(os.path.isdir(self.workspace), "the workspace was renamed")
        self.assertEqual(sorted(os.listdir(self.workspace)), ["final_v01.ma", "scenes"])


class TestDeleteRemovesOnlyWhatItNames(_OnDiskRename, unittest.TestCase):
    """Delete removes the scene (and its sidecar), and its per-scene folder only
    once nothing else is left in it -- real files.

    Regression: with a ``{name}`` structure, deleting a folder's last Maya scene
    ``rmtree``'d the folder, and with it every file that was not a Maya scene --
    an FBX or USD listed as a row of the same panel, a playblast, another file's
    notes -- none of them named in the "Delete hero_v01.ma?" prompt, none
    recoverable. And a scene alone in the scenes root took the root with it.
    Added: 2026-09-23.
    """

    def _delete(self):
        # Never the machine's Recycle Bin (Delete trashes since 2026-09-27): a
        # permanent delete is all the folder rule needs (TestDeleteGoesToTheTrash
        # covers the trash itself).
        with patch.object(
            ptk.FileUtils, "move_to_trash", side_effect=AssertionError("real trash")
        ):
            return self.engine.remove_scene_file(
                self.old, permanent=True, use_folder=True
            )

    def test_the_last_scene_takes_its_emptied_folder_along(self):
        self.old = self._touch("hero", "hero_v01.ma")
        self._touch("hero", "hero_v01.ma.metadata.json")
        self.assertTrue(self._delete())
        self.assertEqual(self._listing(), [])

    def test_a_file_that_is_not_a_scene_keeps_the_folder_and_survives(self):
        self.old = self._touch("hero", "hero_v01.ma")
        self._touch("hero", "hero_v01.fbx")
        self._delete()
        self.assertEqual(self._listing("hero"), ["hero_v01.fbx"])

    def test_a_scene_alone_in_the_scenes_root_never_removes_it(self):
        self.old = self._touch("scenes_final.ma")
        self._delete()
        self.assertTrue(os.path.isdir(self.scenes), "the scenes root was removed")

    def test_without_the_folder_structure_the_emptied_folder_stays(self):
        self.old = self._touch("hero", "hero_v01.ma")
        with patch.object(
            ptk.FileUtils, "move_to_trash", side_effect=AssertionError("real trash")
        ):
            self.engine.remove_scene_file(self.old, permanent=True, use_folder=False)
        self.assertEqual(self._listing(), ["hero"])
        self.assertEqual(self._listing("hero"), [])


class TestDeleteGoesToTheTrash(_OnDiskRename, unittest.TestCase):
    """Delete sends the scene -- and its sidecar -- to the Recycle Bin
    (2026-09-27, the maintainer's Recycle Bin decision): it removed them for
    good. A drive with no trash deletes permanently when asked to; a trash that
    refuses leaves the file untouched (``None``) so the caller can ask again, as
    permanent, before anything is lost (the prompts are the panel's). The trash
    here is a scratch folder, never the machine's.
    """

    def setUp(self):
        super().setUp()
        self.bin = os.path.join(self._store.dir_path(), "bin")
        os.makedirs(self.bin)

    def _to_bin(self, path):
        target = os.path.join(self.bin, os.path.basename(path))
        os.replace(path, target)
        return target

    def _remove(self, permanent=False, trash=None):
        with patch.object(
            ptk.FileUtils, "move_to_trash", side_effect=trash or self._to_bin
        ) as moved:
            result = self.engine.remove_scene_file(
                self.old, permanent=permanent, use_folder=True
            )
        return result, moved

    def test_the_scene_and_its_sidecar_go_to_the_trash(self):
        self.old = self._touch("hero", "hero_v01.ma")
        self._touch("hero", "hero_v01.ma.metadata.json")
        result, _moved = self._remove()
        self.assertTrue(result)
        self.assertEqual(
            sorted(os.listdir(self.bin)), ["hero_v01.ma", "hero_v01.ma.metadata.json"]
        )
        self.assertEqual(self._listing(), [], "its emptied folder goes, as before")

    def test_a_permanent_delete_never_touches_the_trash(self):
        self.old = self._touch("hero", "hero_v01.ma")
        result, moved = self._remove(permanent=True)
        self.assertTrue(result)
        self.assertFalse(os.path.exists(self.old))
        moved.assert_not_called()

    def test_a_trash_that_refuses_leaves_the_file_untouched(self):
        self.old = self._touch("hero", "hero_v01.ma")
        result, _moved = self._remove(trash=lambda path: None)
        self.assertIsNone(result)
        self.assertTrue(os.path.isfile(self.old), "untouched until asked again")

    def test_a_refused_file_removed_permanently_is_gone(self):
        self.old = self._touch("hero", "hero_v01.ma")
        self._remove(trash=lambda path: None)
        result, _moved = self._remove(permanent=True)
        self.assertTrue(result)
        self.assertFalse(os.path.exists(self.old))


class TestRenameOpenSceneSavesAndReopens(unittest.TestCase):
    """Renaming the scene that is currently open must save it first, then re-open the new file.

    Regression: rename only touched disk, so the Maya session kept pointing at the pre-rename
    filename — the user's unsaved edits went nowhere and the next save silently re-created the
    old file beside the renamed one (two scenes where the user renamed one).
    """

    def _make_engine(self, is_current=True, report=None):
        engine = ref_engine.ReferenceManager(report=report)
        engine.is_current = lambda path, current=None: is_current
        # A browsed workspace unrelated to the C:proj paths below, so the
        # per-scene folders they rename are never the project's own structure.
        engine._current_working_dir = tempfile.gettempdir()
        return engine

    @staticmethod
    def _fake_cmds_file(calls, fail_save=False):
        """Stand-in for ``cmds.file``: reports the scene modified, records saves."""

        def _file(*args, **kwargs):
            if kwargs.get("q") or kwargs.get("query"):
                return True  # scene has unsaved edits
            if kwargs.get("save"):
                if fail_save:
                    raise RuntimeError("disk full")
                calls.append(("save", kwargs.get("type")))

        return _file

    def _run_rename(self, engine, old, new, folder=None, fail_save=False):
        """Run the rename with disk + scene ops stubbed; returns the final path. The
        ordered calls land in ``self.calls`` (read them after a raise too)."""
        self.calls = calls = []
        engine.current_scene = lambda: old  # the open scene is the one renamed
        engine.open_scene = lambda p, **kw: calls.append(("open", p))
        with (
            patch.object(
                ref_engine.cmds,
                "file",
                create=True,
                side_effect=self._fake_cmds_file(calls, fail_save),
            ),
            patch.object(ref_engine.os.path, "exists", return_value=False),
            patch.object(
                ref_engine.os,
                "rename",
                side_effect=lambda a, b: calls.append(("rename", a, b)),
            ),
        ):
            return engine.rename_scene_file(old, new, folder=folder)

    def test_open_scene_is_saved_before_the_rename_and_reopened_after(self):
        engine = self._make_engine(is_current=True)
        old = os.path.join("C:", "proj", "scenes", "shot.ma")
        new = os.path.join("C:", "proj", "scenes", "hero.ma")

        final = self._run_rename(engine, old, new)

        self.assertEqual(final, new)
        # Save must precede the rename (a save after it would re-create the old name),
        # and the re-open must follow it.
        self.assertEqual([c[0] for c in self.calls], ["save", "rename", "open"])
        self.assertEqual(self.calls[0][1], "mayaAscii")  # .ma keeps its type
        self.assertEqual(self.calls[-1][1], new)  # session lands on the new path

    def test_binary_scene_saves_as_mayabinary(self):
        engine = self._make_engine(is_current=True)
        old = os.path.join("C:", "proj", "scenes", "shot.mb")
        new = os.path.join("C:", "proj", "scenes", "hero.mb")

        self._run_rename(engine, old, new)

        self.assertEqual(self.calls[0], ("save", "mayaBinary"))

    def test_open_fbx_row_is_renamed_on_disk_only(self):
        """Maya opens an .fbx as a scene, but it is not one to save over — no save, no re-open."""
        engine = self._make_engine(is_current=True)
        old = os.path.join("C:", "proj", "scenes", "kit.fbx")
        new = os.path.join("C:", "proj", "scenes", "kit_v2.fbx")

        final = self._run_rename(engine, old, new)

        self.assertEqual(final, new)
        self.assertEqual([c[0] for c in self.calls], ["rename"])

    def test_a_folder_that_cant_be_renamed_is_reported_not_just_logged(self):
        """The scene itself WAS renamed, so a silently un-renamed folder leaves the file under
        the old scene's folder with nothing to explain it: the engine reports it
        (``report=``, a panel's message box)."""
        messages = []
        engine = self._make_engine(is_current=False, report=messages.append)
        old = os.path.join("C:", "proj", "scenes", "Hero", "Hero_v01.ma")
        new = os.path.join("C:", "proj", "scenes", "Hero", "Villain_v01.ma")
        taken = os.path.join("C:", "proj", "scenes", "Villain")
        calls = []

        with (
            patch.object(
                ref_engine.cmds,
                "file",
                create=True,
                side_effect=self._fake_cmds_file(calls),
            ),
            patch.object(
                ref_engine.os.path, "exists", side_effect=lambda p: p == taken
            ),
            patch.object(
                ref_engine.os,
                "rename",
                side_effect=lambda a, b: calls.append(("rename", a, b)),
            ),
        ):
            final = engine.rename_scene_file(old, new, folder="Villain")

        self.assertEqual(final, new)  # the file rename stands
        self.assertEqual(len(calls), 1)  # only the file moved
        self.assertTrue(messages, "the skipped folder rename must reach the user")
        self.assertIn("kept the old name", messages[0])

    def test_renaming_a_closed_scene_leaves_the_session_alone(self):
        engine = self._make_engine(is_current=False)
        old = os.path.join("C:", "proj", "scenes", "other.ma")
        new = os.path.join("C:", "proj", "scenes", "renamed.ma")

        final = self._run_rename(engine, old, new)

        self.assertEqual(final, new)
        self.assertEqual([c[0] for c in self.calls], ["rename"])  # no save, no re-open

    def test_failed_save_aborts_the_rename(self):
        """A scene that could not be saved must stay put — renaming it would strand the
        edits. The save's error reaches the caller (a panel shows it)."""
        engine = self._make_engine(is_current=True)
        old = os.path.join("C:", "proj", "scenes", "shot.ma")
        new = os.path.join("C:", "proj", "scenes", "hero.ma")

        with self.assertRaises(RuntimeError):
            self._run_rename(engine, old, new, fail_save=True)

        self.assertEqual(self.calls, [])  # nothing renamed, nothing re-opened

    def test_reopens_the_path_the_folder_move_landed_on(self):
        """With a {name} per-scene folder, the re-open must use the post-move path."""
        engine = self._make_engine(is_current=True)
        old = os.path.join("C:", "proj", "scenes", "Hero", "Hero_v01.ma")
        new = os.path.join("C:", "proj", "scenes", "Hero", "Villain_v01.ma")

        final = self._run_rename(engine, old, new, folder="Villain")

        moved = os.path.join("C:", "proj", "scenes", "Villain", "Villain_v01.ma")
        self.assertEqual(final, moved)
        self.assertEqual(
            [c[0] for c in self.calls], ["save", "rename", "rename", "open"]
        )
        self.assertEqual(self.calls[-1][1], moved)  # not the pre-move path


class TestRenameOpenSceneAgainstRealMaya(unittest.TestCase):
    """The same rename, driven against a REAL Maya scene on disk.

    The mocked cases above prove the ORDER of save / rename / re-open; only this one proves the
    thing the feature rests on — that Maya tolerates its open scene file being renamed out from
    under it, and that the edits flushed by the save survive the re-open (blendertk's suite has
    covered its side live from the start; this is the Maya twin).
    """

    def setUp(self):
        self._store = ptk.TempArtifacts("mtk_rm_rename_test", policy="scoped")
        self.root = self._store.dir_path()
        self.engine = self._make_engine()
        # The panel browses the project these scenes live in (root/scenes/...).
        self.engine._current_working_dir = self.root

    def tearDown(self):
        ref_engine.cmds.file(
            new=True, force=True
        )  # leave no scene open for the next test
        self._store.cleanup()

    @staticmethod
    def _make_engine(report=None):
        """The real engine with no panel: no progress, the default bake, *report*
        receiving what a panel would show."""
        return ref_engine.ReferenceManager(report=report)

    def _save_scene_as(self, path, *objects):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ref_engine.cmds.file(new=True, force=True)
        for name in objects:
            ref_engine.cmds.polySphere(name=name)
        ref_engine.cmds.file(rename=path)
        ref_engine.cmds.file(save=True, type="mayaAscii")

    @staticmethod
    def _open_scene_path():
        scene = ref_engine.cmds.file(q=True, sceneName=True) or ""
        # normpath("") is "." — guard it.
        return os.path.normcase(os.path.normpath(scene)) if scene else ""

    def _assert_session_on(self, path):
        self.assertEqual(
            self._open_scene_path(), os.path.normcase(os.path.normpath(path))
        )

    def test_open_scene_rename_carries_unsaved_edits_and_moves_the_session(self):
        old = os.path.join(self.root, "scenes", "shot_v01.ma")
        new = os.path.join(self.root, "scenes", "hero_v01.ma")
        self._save_scene_as(old, "keeper")
        ref_engine.cmds.polyCube(name="unsaved_edit")  # authored AFTER the save

        final = self.engine.rename_scene_file(old, new)

        self.assertEqual(final, new)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.isfile(new))
        self._assert_session_on(new)
        self.assertTrue(ref_engine.cmds.objExists("keeper"))
        # The edit made since the last save was flushed into the file that got renamed.
        self.assertTrue(ref_engine.cmds.objExists("unsaved_edit"))
        # And a save now writes the new name — it does not resurrect the old one.
        ref_engine.cmds.file(save=True, type="mayaAscii")
        self.assertFalse(os.path.exists(old))

    def test_open_scene_rename_reopens_the_path_the_folder_move_landed_on(self):
        old = os.path.join(self.root, "scenes", "Hero", "Hero_v01.ma")
        new = os.path.join(self.root, "scenes", "Hero", "Villain_v01.ma")
        self._save_scene_as(old, "folder_probe")
        ref_engine.cmds.polyCube(name="folder_edit")
        # Increments live INSIDE the per-scene folder, so the two moves compose: they are
        # re-keyed to the new filename first, then ride along with the folder rename.
        increments = os.path.join(
            self.root, "scenes", "Hero", "incrementalSave", "Hero_v01.ma"
        )
        os.makedirs(increments)
        with open(os.path.join(increments, "Hero_v01.0001.ma"), "w") as f:
            f.write("an older increment")

        final = self.engine.rename_scene_file(old, new, folder="Villain")

        moved = os.path.join(self.root, "scenes", "Villain", "Villain_v01.ma")
        self.assertEqual(final, moved)
        self.assertTrue(os.path.isfile(moved))
        self.assertFalse(os.path.isdir(os.path.dirname(old)))
        self._assert_session_on(moved)
        self.assertTrue(ref_engine.cmds.objExists("folder_edit"))
        self.assertTrue(
            os.path.isfile(
                os.path.join(
                    self.root,
                    "scenes",
                    "Villain",
                    "incrementalSave",
                    "Villain_v01.ma",
                    "Hero_v01.0001.ma",
                )
            )
        )

    def test_sidecar_metadata_follows_the_rename(self):
        old = os.path.join(self.root, "scenes", "with_notes.ma")
        new = os.path.join(self.root, "scenes", "with_notes_renamed.ma")
        self._save_scene_as(old, "note_probe")
        with open(old + ".metadata.json", "w", encoding="utf-8") as f:
            f.write('{"Comments": "hello"}')

        self.engine.rename_scene_file(old, new)

        self.assertTrue(os.path.isfile(new + ".metadata.json"))
        self.assertFalse(os.path.exists(old + ".metadata.json"))

    def test_incremental_save_folder_follows_the_rename(self):
        """Maya keys its Incremental Save folder to the scene FILENAME. Left behind, the history
        detaches from its scene — and a later scene reusing the old name inherits it."""
        old = os.path.join(self.root, "scenes", "shot_v01.ma")
        new = os.path.join(self.root, "scenes", "hero_v01.ma")
        self._save_scene_as(old, "inc_probe")
        # The layout Maya's own incrementalSaveProcessPath.mel builds.
        increments = os.path.join(self.root, "scenes", "incrementalSave", "shot_v01.ma")
        os.makedirs(increments)
        with open(os.path.join(increments, "shot_v01.0001.ma"), "w") as f:
            f.write("an older increment")

        self.engine.rename_scene_file(old, new)

        moved = os.path.join(self.root, "scenes", "incrementalSave", "hero_v01.ma")
        self.assertTrue(os.path.isfile(os.path.join(moved, "shot_v01.0001.ma")))
        self.assertFalse(os.path.exists(increments))

    def test_renaming_a_closed_scene_opens_nothing(self):
        path = os.path.join(self.root, "scenes", "untouched.ma")
        self._save_scene_as(path, "other_probe")
        ref_engine.cmds.file(new=True, force=True)  # nothing open now

        renamed = os.path.join(self.root, "scenes", "untouched_renamed.ma")
        self.engine.rename_scene_file(path, renamed)

        self.assertTrue(os.path.isfile(renamed))
        self.assertEqual(self._open_scene_path(), "")


class TestImportReferencesNamespaceModes(unittest.TestCase):
    """The three namespace modes, driven against real referenced scenes.

    'root' is the reason this runs live: it has no mock-able surface — it rests on
    Maya's own namespace-merge clash handling plus the fact that an MObject survives
    the renames that merge performs.
    """

    def setUp(self):
        self._store = ptk.TempArtifacts("mtk_rm_namespace_test", policy="scoped")
        self.root = self._store.dir_path()
        self.asset = os.path.join(self.root, "asset.ma")
        self._author_asset(self.asset)
        self.manager = self._make_manager()

    def tearDown(self):
        ref_engine.cmds.file(new=True, force=True)
        self._store.cleanup()

    @staticmethod
    def _make_manager():
        manager = ref_mgr.ReferenceManager.__new__(ref_mgr.ReferenceManager)
        manager.logger = MockLogger()
        return manager

    @staticmethod
    def _author_asset(path):
        """A two-level asset: ``asset_root`` > ``asset_child`` (+ their shapes)."""
        ref_engine.cmds.file(new=True, force=True)
        root = ref_engine.cmds.polyCube(name="asset_root")[0]
        child = ref_engine.cmds.polyCube(name="asset_child")[0]
        ref_engine.cmds.parent(child, root)
        ref_engine.cmds.file(rename=path)
        ref_engine.cmds.file(save=True, type="mayaAscii")
        ref_engine.cmds.file(new=True, force=True)

    def _reference(self, namespace="ASSET"):
        ref_engine.cmds.file(self.asset, reference=True, namespace=namespace)

    @staticmethod
    def _transforms():
        """Short names of the scene's non-default transforms."""
        default = {"persp", "top", "front", "side"}
        return sorted(
            n.split("|")[-1]
            for n in ref_engine.cmds.ls(type="transform", long=True) or []
            if n.split("|")[-1].split(":")[-1] not in default
        )

    def test_remove_mode_strips_the_namespace_from_every_node(self):
        self._reference()
        self.manager.import_references(namespace_mode="remove")
        self.assertEqual(self._transforms(), ["asset_child", "asset_root"])
        self.assertFalse(ref_engine.cmds.namespace(exists="ASSET"))

    def test_keep_mode_leaves_every_node_namespaced(self):
        self._reference()
        self.manager.import_references(namespace_mode="keep")
        self.assertEqual(self._transforms(), ["ASSET:asset_child", "ASSET:asset_root"])
        self.assertTrue(ref_engine.cmds.namespace(exists="ASSET"))
        # The reference link itself is gone — this is an import, not a load.
        self.assertEqual(self.manager.current_references, [])

    def test_root_mode_namespaces_the_top_transform_only(self):
        self._reference()
        self.manager.import_references(namespace_mode="root")
        self.assertEqual(self._transforms(), ["ASSET:asset_root", "asset_child"])
        # The namespace survives holding the root and nothing below it. The root's OWN
        # shape rides along: Maya keeps a shape's name in step with its transform, so
        # re-namespacing the root re-namespaces its shape too (documented, not a leak).
        self.assertEqual(
            sorted(
                n.split("|")[-1]
                for n in ref_engine.cmds.namespaceInfo(
                    "ASSET", listOnlyDependencyNodes=True
                )
                or []
            ),
            ["ASSET:asset_root", "ASSET:asset_rootShape"],
        )
        # The child hierarchy and its shape are merged into the scene, unprefixed.
        self.assertTrue(ref_engine.cmds.objExists("|ASSET:asset_root|asset_child"))
        self.assertEqual(ref_engine.cmds.ls("ASSET:asset_child"), [])
        self.assertEqual(self.manager.current_references, [])

    def _point_current_namespace_elsewhere(self):
        """Leave the session's CURRENT namespace on something other than the root.

        Routine in a real session — the Namespace Editor sets it, and so does any tool
        that imports into a sandbox namespace (see ``namespace_sandbox``).
        """
        ref_engine.cmds.namespace(add=":BYSTANDER")
        ref_engine.cmds.namespace(set=":BYSTANDER")
        self.addCleanup(lambda: ref_engine.cmds.namespace(set=":"))

    def test_remove_mode_strips_under_a_non_root_current_namespace(self):
        """``cmds.namespace`` resolves a BARE name against the CURRENT namespace, so a
        session pointing anywhere but root made the existence guard report False and the
        strip silently no-op — 'remove' quietly behaved as 'keep'."""
        self._reference()
        self._point_current_namespace_elsewhere()

        self.manager.import_references(namespace_mode="remove")

        ref_engine.cmds.namespace(set=":")
        self.assertEqual(self._transforms(), ["asset_child", "asset_root"])
        self.assertFalse(ref_engine.cmds.namespace(exists=":ASSET"))

    def test_root_mode_works_under_a_non_root_current_namespace(self):
        self._reference()
        self._point_current_namespace_elsewhere()

        self.manager.import_references(namespace_mode="root")

        ref_engine.cmds.namespace(set=":")
        self.assertEqual(self._transforms(), ["ASSET:asset_root", "asset_child"])
        # Re-created at the ROOT, not nested under whatever was current.
        self.assertTrue(ref_engine.cmds.namespace(exists=":ASSET"))
        self.assertFalse(ref_engine.cmds.namespace(exists=":BYSTANDER:ASSET"))

    def test_root_mode_survives_the_asset_being_grouped(self):
        """Top-level is relative to the REFERENCE, not the world. Parenting a referenced
        asset under a scene group is routine; if that made the reference look rootless,
        'root' would silently degrade into 'remove'."""
        self._reference()
        group = ref_engine.cmds.group(empty=True, name="SET_DRESSING")
        ref_engine.cmds.parent("ASSET:asset_root", group)

        self.manager.import_references(namespace_mode="root")

        self.assertTrue(ref_engine.cmds.objExists("|SET_DRESSING|ASSET:asset_root"))
        self.assertTrue(
            ref_engine.cmds.objExists("|SET_DRESSING|ASSET:asset_root|asset_child")
        )

    def test_top_transforms_are_reference_relative_not_world_relative(self):
        """The same rule read directly off the query the modes rest on."""
        self._reference()
        group = ref_engine.cmds.group(empty=True, name="SET_DRESSING")
        ref_engine.cmds.parent("ASSET:asset_root", group)

        (ref,) = self.manager.current_references
        self.assertEqual(
            [t.split("|")[-1] for t in self.manager.get_reference_top_transforms(ref)],
            ["ASSET:asset_root"],
        )

    def test_root_mode_keeps_each_reference_under_its_own_namespace(self):
        """Two references of the SAME file: Maya uniquifies the merged child names, and
        each root must still land back under its own namespace rather than collide."""
        self._reference("ASSET_A")
        self._reference("ASSET_B")
        self.manager.import_references(namespace_mode="root")

        roots = [t for t in self._transforms() if "asset_root" in t]
        self.assertEqual(roots, ["ASSET_A:asset_root", "ASSET_B:asset_root"])
        for ns in ("ASSET_A", "ASSET_B"):
            self.assertEqual(
                sorted(ref_engine.cmds.namespaceInfo(ns, listOnlyDependencyNodes=True)),
                [f"{ns}:asset_root", f"{ns}:asset_rootShape"],
            )

    def test_root_mode_scoped_to_one_namespace_leaves_the_other_referenced(self):
        self._reference("ASSET_A")
        self._reference("ASSET_B")
        self.manager.import_references(namespaces="ASSET_A", namespace_mode="root")

        self.assertEqual(
            [r.namespace for r in self.manager.current_references], ["ASSET_B"]
        )
        self.assertTrue(ref_engine.cmds.objExists("ASSET_A:asset_root"))
        self.assertTrue(ref_engine.cmds.objExists("asset_child"))

    def test_invalid_mode_raises_rather_than_silently_removing(self):
        self._reference()
        with self.assertRaises(ValueError):
            self.manager.import_references(namespace_mode="strip")
        # Nothing was imported — the reference is untouched.
        self.assertEqual(
            [r.namespace for r in self.manager.current_references], ["ASSET"]
        )

    def test_the_retired_bool_form_is_gone(self):
        """``remove_namespace`` (the bool the modes replaced 2026-08-11) had no
        caller left and was retired 2026-09-21: ``namespace_mode="keep"`` is
        the old ``False``, ``"remove"`` the old ``True``."""
        self._reference()
        with self.assertRaises(TypeError):
            self.manager.import_references(remove_namespace=False)
        self.manager.import_references(namespace_mode="keep")
        self.assertEqual(self._transforms(), ["ASSET:asset_child", "ASSET:asset_root"])


class TestImportReferencesClearsWhatItPromoted(unittest.TestCase):
    """An import leaves no broken reference behind (BACKLOG 2026-08-29).

    A reference Maya could not form -- a scene referencing the file that is
    ALREADY open -- survives inside its parent as a node with no file.
    Importing the parent promoted it to a top-level file-less node that the
    scene then saved and Maya's Reference Editor listed as broken. It is
    debris the import itself made, so the import removes it inside its own
    undo chunk (the maintainer's call of 2026-09-10); nothing else is touched.
    """

    def setUp(self):
        cmds = ref_engine.cmds
        self._store = ptk.TempArtifacts("mtk_rm_file_less_test", policy="scoped")
        root = self._store.dir_path()
        self.child = os.path.join(root, "fl_child.ma")
        self.parent = os.path.join(root, "fl_parent.ma")
        self.healthy = os.path.join(root, "fl_healthy.ma")
        for path, build in (
            (self.child, lambda: cmds.polyCube(name="child_cube")),
            (self.healthy, lambda: cmds.polyCube(name="healthy_cube")),
            (
                self.parent,
                lambda: cmds.file(self.child, reference=True, namespace="CHILD"),
            ),
        ):
            cmds.file(new=True, force=True)
            build()
            cmds.file(rename=path)
            cmds.file(save=True, type="mayaAscii", force=True)
        # The child IS the open scene, so the parent's reference to it cannot form.
        cmds.file(self.child, open=True, force=True)
        self.manager = TestImportReferencesNamespaceModes._make_manager()

    def tearDown(self):
        ref_engine.cmds.file(new=True, force=True)
        self._store.cleanup()

    @staticmethod
    def _file_less():
        return ref_engine.EnvUtils.list_reference_nodes(file_less=True)

    def test_the_node_the_import_promoted_is_removed(self):
        cmds = ref_engine.cmds
        cmds.file(self.parent, reference=True, namespace="PARENT")
        cmds.file(self.healthy, reference=True, namespace="KEEP")

        self.manager.import_references(namespaces="PARENT")

        self.assertEqual(self._file_less(), [], "no broken reference is left")
        self.assertEqual(
            [ref.namespace for ref in self.manager.current_references],
            ["KEEP"],
            "a healthy reference is never caught by the probe",
        )

    def test_debris_an_earlier_import_left_is_not_this_one_s(self):
        cmds = ref_engine.cmds
        cmds.file(self.parent, reference=True, namespace="EARLIER")
        cmds.file(referenceNode="EARLIERRN", importReference=True)  # a raw import
        earlier = self._file_less()
        self.assertEqual(len(earlier), 1, "the fixture must hold older debris")
        cmds.file(self.parent, reference=True, namespace="PARENT")

        self.manager.import_references(namespaces="PARENT")

        self.assertEqual(self._file_less(), earlier)

    def test_one_undo_brings_the_removed_node_back(self):
        cmds = ref_engine.cmds
        cmds.file(self.parent, reference=True, namespace="PARENT")
        self.manager.import_references(namespaces="PARENT")
        self.assertEqual(self._file_less(), [])
        cmds.undo()
        self.assertEqual(len(self._file_less()), 1)


class TestUsdRowsAgainstRealMaya(unittest.TestCase):
    """USD rows reference and open NATIVELY, through mayaUsd's translator -- the way an
    .fbx row goes through the FBX plugin -- driven against a real Maya.

    Measured first (Maya 2025 / mayaUsd 0.30): the translator is a real reference
    reader -- reference node, namespace, unload/reload and importReference all work,
    and the saved scene records ``-typ`` / ``-op`` so a reopen reads the stage the same
    way. Left to pick the translator by extension, though, Maya reads with
    ``readAnimData`` at its OFF default (the keyed stage arrived static), and a
    dual-quaternion skin crashes the reader outright (an access violation) -- so a live
    read of one is refused rather than attempted.
    """

    def setUp(self):
        from mayatk.env_utils.usd import UsdUtils

        UsdUtils.load_plugin()
        self._store = ptk.TempArtifacts("mtk_rm_usd_test", policy="scoped")
        self.root = self._store.dir_path()
        self.usd = os.path.join(self.root, "crate.usda")
        cmds = ref_engine.cmds
        cmds.file(new=True, force=True)
        cube = cmds.polyCube(name="crate")[0]
        cmds.setKeyframe(cube, attribute="translateX", t=1, v=0)
        cmds.setKeyframe(cube, attribute="translateX", t=10, v=5)
        cmds.select(cube)
        cmds.mayaUSDExport(file=self.usd, selection=True, frameRange=(1, 10))
        cmds.file(new=True, force=True)

    def tearDown(self):
        ref_engine.cmds.file(new=True, force=True)
        self._store.cleanup()

    def _dual_quaternion_stage(self):
        """A stage authoring the one skinning method mayaUsd 0.30's reader crashes on."""
        from pxr import Usd, UsdGeom, UsdSkel

        path = os.path.join(self.root, "limb_dq.usda")
        stage = Usd.Stage.CreateNew(path)
        prim = UsdGeom.Mesh.Define(stage, "/rig/limb").GetPrim()
        UsdSkel.BindingAPI.Apply(prim)
        UsdSkel.BindingAPI(prim).CreateSkinningMethodAttr().Set("dualQuaternion")
        stage.GetRootLayer().Save()
        return path

    @staticmethod
    def _norm(path):
        return os.path.normcase(os.path.normpath(path))

    def _metre_z_up_stage(self):
        """Blender's default USD export shape: metres, Z up -- a 0.2 x 0.2 x 1 m
        box standing on Z. mayaUsd 0.30 converts neither on read."""
        from pxr import Gf, Usd, UsdGeom

        path = os.path.join(self.root, "tall_box_m_zup.usda")
        stage = Usd.Stage.CreateNew(path)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        mesh = UsdGeom.Mesh.Define(stage, "/tall_box")
        corners = [(-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (-0.1, 0.1)]
        points = [Gf.Vec3f(x, y, z) for z in (0.0, 1.0) for x, y in corners]
        mesh.CreatePointsAttr(points)
        mesh.CreateFaceVertexCountsAttr([4] * 6)
        mesh.CreateFaceVertexIndicesAttr(
            [0, 3, 2, 1, 4, 5, 6, 7, 0, 1, 5, 4, 1, 2, 6, 5, 2, 3, 7, 6, 3, 0, 4, 7]
        )
        stage.SetDefaultPrim(mesh.GetPrim())
        stage.GetRootLayer().Save()
        return path

    def _world_box(self, nodes):
        box = ref_engine.cmds.exactWorldBoundingBox(nodes)
        return [round(v, 3) for v in box]

    def test_a_metre_z_up_reference_lands_at_size_and_upright(self):
        """BACKLOG 2026-09-21 (decided 2026-09-23: auto-conform): measured, this
        layer referenced at 0.2 x 0.2 x 1.0 cm standing along Z. Its top nodes
        now sit under a host-side group that scales and turns it into the
        scene's cm / Y-up -- a parent edit, so an unload/reload keeps it."""
        cmds = ref_engine.cmds
        manager = TestImportReferencesNamespaceModes._make_manager()
        self.assertTrue(manager.add_reference("tall", self._metre_z_up_stage()))
        ref = manager.current_references[0]
        geo = cmds.ls(f"{ref.namespace}:tall_box", long=True)
        want = [-10.0, 0.0, -10.0, 10.0, 100.0, 10.0]
        self.assertEqual(self._world_box(geo), want)
        self.assertTrue(cmds.objExists("tall_conform"))

        rn = cmds.referenceQuery(geo[0], referenceNode=True)
        cmds.file(unloadReference=rn)
        cmds.file(loadReference=rn)
        geo = cmds.ls(f"{ref.namespace}:tall_box", long=True)
        self.assertEqual(self._world_box(geo), want, "the conform survives a reload")

        self.assertEqual(manager.remove_references(["tall"]), [])
        self.assertFalse(cmds.objExists("tall_conform"), "an empty group goes too")

    def test_a_metre_z_up_scene_opens_at_size_and_upright(self):
        engine = TestRenameOpenSceneAgainstRealMaya._make_engine()
        self.assertTrue(
            engine.open_scene(self._metre_z_up_stage(), set_workspace=False)
        )
        self.assertEqual(
            self._world_box(ref_engine.cmds.ls("tall_box", long=True)),
            [-10.0, 0.0, -10.0, 10.0, 100.0, 10.0],
        )

    def test_a_scene_unit_layer_gets_no_conform_group(self):
        """The bridge writes Maya-bound layers in cm / Y-up; so does mayaUsd."""
        manager = TestImportReferencesNamespaceModes._make_manager()
        self.assertTrue(manager.add_reference("crate", self.usd))
        self.assertFalse(ref_engine.cmds.ls("*_conform"))

    def test_a_metre_z_up_import_lands_at_size_and_upright(self):
        """The native import path (the Reference Manager's Import of a USD row,
        through ``BlenderSceneImport.import_scene``'s USD fast path) conforms
        the same way; the bridge's own payload does not ask to."""
        from mayatk.env_utils.blender_bridge._scene_import import BlenderSceneImport

        cmds = ref_engine.cmds
        BlenderSceneImport().import_scene(self._metre_z_up_stage())
        self.assertEqual(
            self._world_box(cmds.ls("tall_box", long=True)),
            [-10.0, 0.0, -10.0, 10.0, 100.0, 10.0],
        )

    def test_add_reference_reads_a_usd_through_its_translator(self):
        manager = TestImportReferencesNamespaceModes._make_manager()
        self.assertTrue(manager.add_reference("crate", self.usd))

        refs = manager.current_references
        self.assertEqual([self._norm(r.path) for r in refs], [self._norm(self.usd)])
        self.assertTrue(
            ref_engine.cmds.keyframe(
                f"{refs[0].namespace}:crate", q=True, timeChange=True
            ),
            "the referenced stage arrived static",
        )
        # Stored on the reference itself, so a reopen reads the stage the same way.
        host = os.path.join(self.root, "host.ma")
        ref_engine.cmds.file(rename=host)
        ref_engine.cmds.file(save=True, type="mayaAscii")
        with open(host, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
        self.assertIn('-typ "USD Import"', text)
        self.assertIn("readAnimData=1", text)

    def test_add_reference_refuses_a_usd_the_reader_crashes_on(self):
        manager = TestImportReferencesNamespaceModes._make_manager()
        with patch.object(ref_engine.cmds, "warning") as warned:
            self.assertFalse(
                manager.add_reference("limb", self._dual_quaternion_stage())
            )
        self.assertEqual(manager.current_references, [])
        self.assertIn("/rig/limb", warned.call_args[0][0])

    def test_open_scene_opens_a_usd_through_its_translator(self):
        engine = TestRenameOpenSceneAgainstRealMaya._make_engine()
        self.assertTrue(engine.open_scene(self.usd, set_workspace=False))
        self.assertEqual(
            self._norm(ref_engine.cmds.file(q=True, sceneName=True)),
            self._norm(self.usd),
        )
        self.assertTrue(
            ref_engine.cmds.keyframe("crate", q=True, timeChange=True),
            "the opened stage arrived static",
        )

    def test_open_scene_refuses_a_usd_the_reader_crashes_on(self):
        """Refused, and reported (``report=``: a click did nothing, the user must
        learn why) -- naming Unlink and Import, which CAN read it."""
        shown = []
        engine = TestRenameOpenSceneAgainstRealMaya._make_engine(report=shown.append)
        dq = self._dual_quaternion_stage()
        with patch.object(ref_engine.cmds, "warning"):
            self.assertFalse(engine.open_scene(dq, set_workspace=False))
        self.assertNotEqual(
            self._norm(ref_engine.cmds.file(q=True, sceneName=True) or "x"),
            self._norm(dq),
        )
        self.assertTrue(shown and "/rig/limb" in shown[0], shown)
        self.assertIn("Unlink and Import", shown[0])

    def _garbage_layer(self):
        """A layer pxr cannot parse -- its error quotes the prim path ``</>``."""
        path = os.path.join(self.root, "damaged.usda")
        with open(path, "wb") as fh:
            fh.write(b"#usda 1.0\n(\n this is not usd {{{\n")
        return path

    def test_add_reference_refuses_an_unreadable_usd(self):
        """Measured: the translator does not fail on a layer pxr cannot read -- it
        leaves an EMPTY reference node behind, which reads as referenced in the table
        while holding nothing."""
        manager = TestImportReferencesNamespaceModes._make_manager()
        with patch.object(ref_engine.cmds, "warning") as warned:
            self.assertFalse(manager.add_reference("damaged", self._garbage_layer()))
        self.assertEqual(manager.current_references, [])
        self.assertEqual(
            [
                r
                for r in ref_engine.cmds.ls(type="reference") or []
                if "shared" not in r
            ],
            [],
        )
        self.assertIn("not a readable USD layer", warned.call_args[0][0])

    def test_open_scene_refuses_an_unreadable_usd_without_offering_the_import(self):
        """A damaged layer reads for neither path, so the report must not send the
        user to Unlink and Import. (The report is plain text: escaping pxr's ``</>``
        for a rich-text box is the panel's.)"""
        shown = []
        engine = TestRenameOpenSceneAgainstRealMaya._make_engine(report=shown.append)
        with patch.object(ref_engine.cmds, "warning"):
            self.assertFalse(
                engine.open_scene(self._garbage_layer(), set_workspace=False)
            )
        self.assertTrue(shown and "not a readable USD layer" in shown[0], shown)
        self.assertNotIn("Unlink and Import", shown[0])


class TestFbxRowsAgainstRealMaya(unittest.TestCase):
    """An .fbx row read through the FBX plugin plays the ONE take the session's
    sticky ``FBXImportSetTake`` names -- at every load, reload and reopen.

    BACKLOG 2026-10-10, measured (fresh mayapy): a Shots Only file (shots 1-30 /
    50-70) referenced as 21 keys, 50-70 -- its last take, 30 of 51 frames off the
    source -- a reload under ``-ti 1`` played the other shot alone, and a sticky
    index past the file's takes loaded 0 nodes while ``add_reference`` returned
    True. No one take reads such a file whole, so the reference reads a cached
    ``.ma`` bake of it (``FbxUtils.import_scene``'s take rules in a fresh mayapy);
    a file its last take reads whole stays native, under the per-file ``-1``.
    """

    KEYS = ((1, 0.0), (10, 5.0), (40, -3.0), (60, 8.0), (90, 2.0), (100, 10.0))
    SHOTS = (
        {"name": "early", "start": 1, "end": 30},
        {"name": "late", "start": 50, "end": 70},
    )

    def setUp(self):
        import maya.mel as mel
        from mayatk.env_utils.fbx_utils import FbxUtils

        self.mel, self.fbx = mel, FbxUtils
        FbxUtils.load_plugin()
        self._store = ptk.TempArtifacts("mtk_rm_fbx_test", policy="scoped")
        self.root = self._store.dir_path()
        self.manager = TestImportReferencesNamespaceModes._make_manager()
        self.addCleanup(self._store.cleanup)
        # The bakes are project files: this test's project, never the user's.
        cmds = ref_engine.cmds
        self.addCleanup(cmds.workspace, cmds.workspace(q=True, rd=True), o=True)
        cmds.workspace(self.root, openWorkspace=True)
        self.addCleanup(mel.eval, "FBXImportSetTake -ti -1")
        self.addCleanup(ref_engine.cmds.file, new=True, force=True)

    def _write(self, name, takes, keys=None):
        """CUBE keyed 1-100 (*keys*, default :attr:`KEYS`), written as the Scene
        Exporter writes a Shots Only file: one take per entry of *takes*, the
        whole-timeline take dropped. Returns the path and the source's value on
        every frame a take holds."""
        cmds = ref_engine.cmds
        cmds.file(new=True, force=True)
        cube = cmds.polyCube(name="CUBE")[0]
        for t, v in keys or self.KEYS:
            cmds.setKeyframe(f"{cube}.translateX", t=t, v=v)
        expected = {
            f: cmds.getAttr(f"{cube}.translateX", time=f)
            for take in takes
            for f in range(take["start"], take["end"] + 1)
        }
        path = os.path.join(self.root, f"{name}.fbx")
        self.fbx.apply_takes(takes)
        try:
            self.fbx.export(path, objects=[cube])
        finally:
            self.fbx.reset_takes()
        ptk.FbxMedia.drop_takes(path, names=["Take 001"])
        cmds.file(new=True, force=True)
        return path, expected

    def _forget_bake(self, ref_path):
        """Delete the cached bake *ref_path* (and its source sidecar) after the test."""
        from mayatk.env_utils.blender_bridge import _scene_import as si

        if not si.BlenderSceneImport.bake_source(ref_path):
            return

        def forget():
            ref_engine.cmds.file(new=True, force=True)
            for f in (ref_path, ref_path + si.BAKE_SOURCE_SUFFIX):
                if os.path.isfile(f):
                    os.remove(f)

        self.addCleanup(forget)

    def _assert_plays(self, plug, expected):
        cmds = ref_engine.cmds
        self.assertTrue(cmds.objExists(plug), f"{plug}: nothing was loaded")
        wrong = {
            f: (v, cmds.getAttr(plug, time=f))
            for f, v in expected.items()
            if abs(cmds.getAttr(plug, time=f) - v) > 1e-3
        }
        self.assertEqual(wrong, {}, f"{len(wrong)} of {len(expected)} frames off")

    @staticmethod
    def _norm(path):
        return os.path.normcase(os.path.normpath(path))

    def test_a_shots_only_reference_plays_every_shot_at_every_load(self):
        cmds = ref_engine.cmds
        path, expected = self._write("shots", self.SHOTS)
        self.mel.eval("FBXImportSetTake -ti -1")  # a fresh session's selector

        self.assertTrue(self.manager.add_reference("shots", path))

        refs = self.manager.current_references
        self.assertEqual(len(refs), 1)
        ref = refs[0]
        self._forget_bake(ref.path)
        plug = f"{ref.namespace}:CUBE.translateX"
        self._assert_plays(plug, expected)
        # A reload re-reads the file under the selector the session holds THEN.
        self.mel.eval("FBXImportSetTake -ti 9")
        cmds.file(unloadReference=ref._ref_node)
        cmds.file(loadReference=ref._ref_node)
        self._assert_plays(plug, expected)
        # The bake is a project file, named through the project.
        unresolved = cmds.referenceQuery(ref._ref_node, filename=True, un=True)
        self.assertRegex(
            unresolved, r"^cache/bridge/mtk_bake_cache_shots_[0-9a-f]{8}\.ma$"
        )
        # The reference stands for the FBX: it maps back to the row, and adding
        # the FBX again adds nothing.
        self.assertEqual(self.manager._bake_source_key(ref.path), self._norm(path))
        self.assertTrue(self.manager.add_reference("shots", path))
        self.assertEqual(len(self.manager.current_references), 1)

    def test_a_re_exported_fbx_is_played_as_it_is_now(self):
        """A native reference re-read its FBX at every load; a baked one read
        its cached .ma, so a re-exported Shots Only file kept playing the old
        bake. Its reference is now baked again and repointed in place, its
        reference edits kept."""
        cmds = ref_engine.cmds
        later = tuple((t, v * 2 + 1) for t, v in self.KEYS)
        v2, expected_v2 = self._write("shots_v2", self.SHOTS, keys=later)
        path, expected = self._write("shots", self.SHOTS)
        self.assertTrue(self.manager.add_reference("shots", path))
        ref = self.manager.current_references[0]
        self._forget_bake(ref.path)
        node, plug = ref._ref_node, f"{ref.namespace}:CUBE.translateX"
        self._assert_plays(plug, expected)
        cmds.setAttr(f"{ref.namespace}:CUBE.scaleY", 3)  # a reference edit
        shutil.copyfile(v2, path)  # the re-export

        self.manager.update_references()

        ref = self.manager.current_references[0]
        self._forget_bake(ref.path)
        self.assertEqual(ref._ref_node, node, "repointed in place")
        self._assert_plays(plug, expected_v2)
        self.assertEqual(cmds.getAttr(f"{ref.namespace}:CUBE.scaleY"), 3)

    def test_a_static_fbx_stays_native_under_any_selector(self):
        cmds = ref_engine.cmds
        cmds.file(new=True, force=True)
        cube = cmds.polyCube(name="CRATE")[0]
        path = os.path.join(self.root, "static.fbx")
        self.fbx.export(path, objects=[cube])
        cmds.file(new=True, force=True)
        self.mel.eval("FBXImportSetTake -ti 5")

        self.assertTrue(self.manager.add_reference("static", path))

        ref = self.manager.current_references[0]
        self.assertEqual(self._norm(ref.path), self._norm(path), "read natively")
        self.assertTrue(cmds.objExists(f"{ref.namespace}:CRATE"), "nothing loaded")

    def test_a_scene_referencing_an_fbx_opens_it_under_the_per_file_take(self):
        """Opening a scene reads the FBX it references through the same
        plug-in: under a stale index that reference loaded nothing."""
        cmds = ref_engine.cmds
        path, expected = self._write(
            "single", ({"name": "all", "start": 1, "end": 100},)
        )
        self.mel.eval("FBXImportSetTake -ti -1")
        cmds.file(path, reference=True, namespace="single")
        host = os.path.join(self.root, "host.ma")
        cmds.file(rename=host)
        cmds.file(save=True, type="mayaAscii")
        cmds.file(new=True, force=True)
        self.mel.eval("FBXImportSetTake -ti 5")
        engine = TestRenameOpenSceneAgainstRealMaya._make_engine()

        self.assertTrue(engine.open_scene(host, set_workspace=False))

        self._assert_plays("single:CUBE.translateX", expected)

    def test_a_file_its_last_take_reads_whole_stays_native_under_any_selector(self):
        path, expected = self._write(
            "single", ({"name": "all", "start": 1, "end": 100},)
        )
        self.mel.eval("FBXImportSetTake -ti 5")  # an index the file does not have

        self.assertTrue(self.manager.add_reference("single", path))

        ref = self.manager.current_references[0]
        self.assertEqual(self._norm(ref.path), self._norm(path), "read natively")
        plug = f"{ref.namespace}:CUBE.translateX"
        self._assert_plays(plug, expected)
        self.mel.eval("FBXImportSetTake -ti 5")
        ref_engine.cmds.file(unloadReference=ref._ref_node)
        self.manager.update_references()
        self._assert_plays(plug, expected)

    def test_an_fbx_row_opens_under_the_per_file_take(self):
        """Open reads through the same plugin: under a stale index it opened an
        empty scene (measured, fresh mayapy)."""
        path, expected = self._write(
            "single", ({"name": "all", "start": 1, "end": 100},)
        )
        self.mel.eval("FBXImportSetTake -ti 5")
        engine = TestRenameOpenSceneAgainstRealMaya._make_engine()

        self.assertTrue(engine.open_scene(path, set_workspace=False))

        self._assert_plays("CUBE.translateX", expected)


class TestImportReferencesSceneData(unittest.TestCase):
    """An imported reference's own data nodes never stay behind, read by nothing:
    its records merge into this scene's -- respelled to where the import put its
    nodes -- or go with them, and a decider is asked only when a merge would keep
    something. Live: the renames are Maya's own namespace-merge clash handling."""

    def setUp(self):
        self._store = ptk.TempArtifacts("mtk_rm_scene_data_test", policy="scoped")
        self.module = os.path.join(self._store.dir_path(), "module.ma")
        self.manager = TestImportReferencesNamespaceModes._make_manager()

    def tearDown(self):
        from mayatk.anim_utils.shots._shots import ShotStore

        ref_engine.cmds.file(new=True, force=True)
        ShotStore.clear_active()
        self._store.cleanup()

    def _author_module(self, **records):
        """A module with a cube named like the host's (``door``) and *records*
        saved on its own data nodes."""
        from mayatk.node_utils.data_nodes import DataNodes

        cmds = ref_engine.cmds
        cmds.file(new=True, force=True)
        cmds.polyCube(name="door")
        for spec, payload in records.items():
            getattr(ptk.SceneRecords, spec).save(DataNodes, payload)
        cmds.file(rename=self.module)
        cmds.file(save=True, type="mayaAscii")
        cmds.file(new=True, force=True)

    def _host_referencing_it(self):
        """A host with its own ``door`` -- the module's arrives as ``door1`` -- and
        its own audio clips."""
        from mayatk.node_utils.data_nodes import DataNodes

        ref_engine.cmds.polyCube(name="door")
        ptk.SceneRecords.AUDIO_FILE_MAP.save(DataNodes, {"1": "a.wav"})
        ref_engine.cmds.file(self.module, reference=True, namespace="MOD")

    def _module_with_a_shot(self):
        self._author_module(
            SHOT_STORE={
                "shots": [
                    {
                        "shot_id": 1,
                        "name": "Intro",
                        "start": 0,
                        "end": 5,
                        "objects": ["|door"],
                    }
                ]
            },
            AUDIO_FILE_MAP={"2": "b.wav"},
        )
        self._host_referencing_it()

    @staticmethod
    def _load(spec):
        from mayatk.node_utils.data_nodes import DataNodes

        return getattr(ptk.SceneRecords, spec).load(DataNodes)

    @staticmethod
    def _carriers():
        return sorted(
            ref_engine.cmds.ls("*data_internal*", "*:*data_internal*", type="network")
            or []
        )

    def test_a_merge_respells_the_records_and_the_carriers_go(self):
        self._module_with_a_shot()
        self.manager.import_references(namespace_mode="remove")
        audio = self._load("AUDIO_FILE_MAP")
        self.assertEqual(audio["1"], "a.wav")
        # The module's path is spelled from ITS project (the folder it was saved
        # in) and lands spelled from this scene's -- absolute while this one is
        # unsaved (the path rule, 2026-09-23).
        self.assertEqual(
            os.path.normcase(audio["2"]),
            os.path.normcase(os.path.join(os.path.dirname(self.module), "b.wav")),
        )
        (shot,) = self._load("SHOT_STORE")["shots"]
        self.assertEqual(shot["objects"], ["|door1"], "where the clash put it")
        self.assertEqual(self._carriers(), ["data_internal"])

    def test_a_merged_path_is_spelled_from_the_host_project(self):
        """The path rule (2026-09-23): the module's audio path is spelled from
        ITS project; merged into a host saved in another project, it arrives
        re-spelled from the host's (``merge_carriers(source_path_base=)``) --
        a relative spelling naming the same file."""
        self._module_with_a_shot()
        host_root = self._store.dir_path(name="host_project")
        os.makedirs(os.path.join(host_root, "scenes"), exist_ok=True)
        with open(os.path.join(host_root, "workspace.mel"), "w") as fh:
            fh.write("//Maya 2025 Project Definition\n")
        ref_engine.cmds.file(rename=os.path.join(host_root, "scenes", "host.ma"))
        ref_engine.cmds.file(save=True, type="mayaAscii")
        self.manager.import_references(namespace_mode="remove")
        landed = self._load("AUDIO_FILE_MAP")["2"]
        module_wav = os.path.join(os.path.dirname(self.module), "b.wav")
        self.assertFalse(os.path.isabs(landed), landed)
        self.assertEqual(landed, ptk.FileUtils.portable_path(module_wav, host_root))
        self.assertEqual(
            os.path.normcase(os.path.normpath(os.path.join(host_root, landed))),
            os.path.normcase(os.path.normpath(module_wav)),
        )

    def test_a_kept_namespace_is_respelled_too(self):
        self._module_with_a_shot()
        self.manager.import_references(namespace_mode="keep")
        (shot,) = self._load("SHOT_STORE")["shots"]
        self.assertEqual(shot["objects"], ["|MOD:door"])
        self.assertEqual(self._carriers(), ["data_internal"])

    def test_discard_drops_the_records_with_the_carriers(self):
        self._module_with_a_shot()
        self.manager.import_references(scene_data="discard")
        self.assertEqual(self._load("AUDIO_FILE_MAP"), {"1": "a.wav"})
        self.assertIsNone(self._load("SHOT_STORE"))
        self.assertEqual(self._carriers(), ["data_internal"])

    def test_a_shot_the_host_has_not_written_yet_survives_either_answer(self):
        """A GUI session writes the shot store on idle, so a script that adds
        a shot and imports in one go still holds it unwritten -- in a scene
        with no carrier yet, whose import therefore adopts the module's.  The
        importer stores it first: merged, both scenes' shots are kept;
        discarded, the host's stays and the module's goes."""
        from mayatk.anim_utils.shots._shots import ShotStore

        intro = {"shot_id": 1, "name": "Intro", "start": 0, "end": 5, "objects": []}
        for scene_data, expected in (
            ("merge", ["HostShot", "Intro"]),
            ("discard", ["HostShot"]),
        ):
            with self.subTest(scene_data=scene_data):
                self._author_module(SHOT_STORE={"shots": [intro]})
                ShotStore.clear_active()
                with patch.object(ShotStore, "_schedule_flush", lambda self: None):
                    ShotStore.active().define_shot("HostShot", 10.0, 20.0)
                    ref_engine.cmds.file(self.module, reference=True, namespace="MOD")
                    self.manager.import_references(
                        namespace_mode="remove", scene_data=scene_data
                    )
                ShotStore.flush_pending()  # the idle write the session held
                ShotStore.clear_active()
                self.assertEqual(
                    sorted(s.name for s in ShotStore.active().shots), expected
                )

    def test_the_decider_is_asked_with_what_arrives(self):
        self._module_with_a_shot()
        asked = []
        self.manager.import_references(
            scene_data=lambda summary, ns: asked.append((summary, ns)) or "merge"
        )
        ((summary, namespace),) = asked
        self.assertEqual(namespace, "MOD")
        self.assertIn("Audio Clips: 1 entry", summary)

    def test_declining_leaves_the_reference_linked(self):
        self._module_with_a_shot()
        self.manager.import_references(scene_data=lambda summary, ns: None)
        self.assertEqual(
            [r.namespace for r in self.manager.current_references], ["MOD"]
        )
        self.assertEqual(self._load("AUDIO_FILE_MAP"), {"1": "a.wav"})

    def test_a_module_holding_nothing_a_merge_keeps_is_not_asked_about(self):
        """Its own baseline describes itself: no question, and no carrier left."""
        self._author_module(HIERARCHY_BASELINE={"format": 1, "paths": ["door"]})
        self._host_referencing_it()
        self.manager.import_references(
            scene_data=lambda *_: self.fail("nothing to ask about")
        )
        self.assertEqual(self._carriers(), ["data_internal"])
        self.assertIsNone(self._load("HIERARCHY_BASELINE"))

    def test_an_invalid_choice_raises_before_anything_is_imported(self):
        self._module_with_a_shot()
        with self.assertRaises(ValueError):
            self.manager.import_references(scene_data="keep")
        self.assertEqual(
            [r.namespace for r in self.manager.current_references], ["MOD"]
        )


class _StubManager:
    """Minimal stand-in so remove_references can be exercised without a live scene."""

    remove_references = ref_mgr.ReferenceManager.remove_references

    def __init__(self, refs):
        self.current_references = refs
        self.logger = MagicMock()


class TestReferenceRemoval(unittest.TestCase):
    """The engine side of the file-less reference node found in the PROPS assembly.

    Opening ROOM_ENV and then referencing PROPS_ASSEMBLY — which references
    ROOM_ENV itself — leaves Maya a reference node with no file behind it, which
    threw out of every consumer of .path and killed Unreference All on its first
    removal. The screen itself lives in EnvUtils.list_reference_nodes (covered live
    in test_env_utils.py); what is checked here is that the engine goes through it and
    that a removal Maya refuses is handed back rather than silently swallowed (a
    panel names it: tentacle's test_reference_manager_panel.py).
    """

    def test_list_file_refs_wraps_the_shared_screen(self):
        """It must not re-implement the screen — a second copy is what let the two
        diverge in the first place."""
        with patch.object(
            ref_engine.EnvUtils,
            "list_reference_nodes",
            return_value=["ARN", "BRN"],
        ) as screen:
            refs = ref_engine._ReferenceManagerInternal._list_file_refs()

        screen.assert_called_once_with()  # default = top level only
        self.assertEqual([r._ref_node for r in refs], ["ARN", "BRN"])
        self.assertTrue(all(isinstance(r, ref_engine._FileRef) for r in refs))

    def test_one_unremovable_reference_does_not_strand_the_rest(self):
        """Unreference All must keep going past a reference Maya refuses to remove,
        and hand the failures back rather than report a silent partial success."""
        bad, good = MagicMock(), MagicMock()
        bad._ref_node, good._ref_node = "BAD_RN", "GOOD_RN"
        bad.remove.side_effect = RuntimeError('File not found: ""')
        stub = _StubManager([bad, good])

        failed = stub.remove_references()

        good.remove.assert_called_once_with()
        self.assertEqual(failed, [bad])
        self.assertTrue(stub.logger.warning.called)

    def test_removal_returns_empty_when_every_reference_went(self):
        stub = _StubManager([MagicMock(), MagicMock()])
        self.assertEqual(stub.remove_references(), [])

    def test_namespace_filter_still_scopes_the_removal(self):
        keep, drop = MagicMock(), MagicMock()
        keep.namespace, drop.namespace = "KEEP", "DROP"
        stub = _StubManager([keep, drop])

        stub.remove_references("DROP")

        drop.remove.assert_called_once_with()
        keep.remove.assert_not_called()

    def test_a_broken_reference_still_has_a_name_to_report(self):
        """.namespace raises on a file-less reference — the one kind most likely to be
        in a failure message — so the label must fall back to the node name."""
        ref = ref_engine._FileRef("PROPS_ASSEMBLY:ROOM_ENVRN")
        with patch.object(
            ref_engine.cmds,
            "referenceQuery",
            create=True,
            side_effect=RuntimeError("is not associated with a reference file"),
        ):
            self.assertEqual(ref.label, "PROPS_ASSEMBLY:ROOM_ENVRN")


class TestRemovalReleasesFosteredNodes(unittest.TestCase):
    """Unreferencing left ``ROOM_ENVRNfosterParent1`` in production: a
    local node parented under a referenced one is parked by Maya under a foster
    parent that outlives the reference. Live: it is Maya's removal behaviour."""

    def setUp(self):
        self._store = ptk.TempArtifacts("mtk_rm_foster_test", policy="scoped")
        self.asset = os.path.join(self._store.dir_path(), "asset.ma")
        TestImportReferencesNamespaceModes._author_asset(self.asset)
        self.manager = TestImportReferencesNamespaceModes._make_manager()

    def tearDown(self):
        ref_engine.cmds.file(new=True, force=True)
        self._store.cleanup()

    def test_unreferencing_leaves_no_foster_parent_and_keeps_local_nodes(self):
        cmds = ref_engine.cmds
        cmds.file(self.asset, reference=True, namespace="ENV")
        loc = cmds.spaceLocator(name="localLoc")[0]
        cmds.parent(loc, "ENV:asset_root")

        self.assertEqual(self.manager.remove_references(["ENV"]), [])

        self.assertEqual(cmds.ls(type="fosterParent"), [])
        self.assertEqual(cmds.ls("localLoc", long=True), ["|localLoc"])


class TestConvertReferencesToAssemblies(unittest.TestCase):
    """``AssemblyManager.convert_references_to_assemblies`` against a real file."""

    def setUp(self):
        cmds = ref_engine.cmds
        self._store = ptk.TempArtifacts("mtk_rm_assembly_test", policy="scoped")
        self.prop = os.path.join(self._store.dir_path(), "prop.ma")
        cmds.file(new=True, force=True)
        cmds.polyCube(name="box")
        cmds.file(rename=self.prop)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)
        cmds.file(self.prop, reference=True, namespace="prop")

    def tearDown(self):
        ref_engine.cmds.file(new=True, force=True)
        self._store.cleanup()

    def _converted(self):
        """``{definition: active representation}`` after the conversion."""
        cmds = ref_engine.cmds
        ref_engine.AssemblyManager.convert_references_to_assemblies()
        return {
            d: cmds.assembly(d, query=True, active=True)
            for d in cmds.ls(type="assemblyDefinition")
        }

    def test_a_reference_becomes_an_active_assembly(self):
        self.assertEqual(self._converted(), {"prop_assembly": "prop.ma"})
        self.assertEqual(ref_engine.cmds.ls(type="reference"), [])

    def test_a_taken_name_activates_the_new_definition(self):
        """Bug: the conversion re-derived the node name, so when Maya had to
        rename the new definition (``prop_assembly1``) it activated the OLD
        ``prop_assembly``, failed, and left the reference in place. Fixed:
        2026-10-02."""
        ref_engine.Plugins.load("sceneAssembly")
        ref_engine.cmds.assembly(name="prop_assembly", type="assemblyDefinition")

        self.assertEqual(
            self._converted(), {"prop_assembly": "", "prop_assembly1": "prop.ma"}
        )
        self.assertEqual(ref_engine.cmds.ls(type="reference"), [])


class TestSelectedReferencePaths(unittest.TestCase):
    """``selected_reference_paths``: the files behind the selected references -- what
    the Mesh Converter's "From FBX references" toggle reads."""

    def setUp(self):
        self._store = ptk.TempArtifacts("mtk_rm_selected_refs", policy="scoped")
        self.asset = os.path.join(self._store.dir_path(), "asset.ma")
        TestImportReferencesNamespaceModes._author_asset(self.asset)
        ref_engine.cmds.file(self.asset, reference=True, namespace="ASSET")

    def tearDown(self):
        ref_engine.cmds.file(new=True, force=True)
        self._store.cleanup()

    @staticmethod
    def _norm(paths):
        return [os.path.normcase(os.path.normpath(p)) for p in paths]

    def test_a_selected_referenced_node_names_its_file(self):
        ref_engine.cmds.select("ASSET:asset_child")
        self.assertEqual(
            self._norm(ref_mgr.ReferenceManager.selected_reference_paths()),
            self._norm([self.asset]),
        )
        self.assertEqual(
            ref_mgr.ReferenceManager.selected_reference_paths((".fbx",)), []
        )

    def test_local_nodes_and_an_empty_selection_name_nothing(self):
        ref_engine.cmds.select(ref_engine.cmds.polyCube(name="local_cube")[0])
        self.assertEqual(ref_mgr.ReferenceManager.selected_reference_paths(), [])
        ref_engine.cmds.select(clear=True)
        self.assertEqual(ref_mgr.ReferenceManager.selected_reference_paths(), [])


if __name__ == "__main__":
    unittest.main()
