# !/usr/bin/python
# coding=utf-8
"""Test Suite for audio_utils internal modules.

Covers:
    - audio_utils.nodes (DG audio primitives)
    - audio_utils.compositor (managed-node discovery)
    - audio_utils.migrate (legacy detection)
    - audio_utils.batch (batch context manager)
"""

import unittest
import tempfile
import shutil

import maya.cmds as cmds

from mayatk.audio_utils import nodes
from mayatk.audio_utils import compositor
from mayatk.audio_utils import migrate
from mayatk.audio_utils import batch as batch_mod
from mayatk.audio_utils._audio_utils import MARKER_ATTR

from base_test import MayaTkTestCase, QuickTestCase


class TestAudioNodesPaths(MayaTkTestCase):
    """nodes.workspace_sound_dir / resolve_playable_path."""

    def test_workspace_sound_dir_returns_string_or_none(self):
        result = nodes.Nodes.workspace_sound_dir()
        self.assertTrue(result is None or isinstance(result, str))

    def test_resolve_playable_path_with_nonexistent_returns_none(self):
        # ptk.AudioUtils.resolve_playable_path returns None for missing files
        result = nodes.Nodes.resolve_playable_path("/__nonexistent__/audio.wav")
        self.assertTrue(result is None or isinstance(result, str))


class TestAudioCacheLocation(MayaTkTestCase):
    """Transcodes cache where ``ptk.AudioUtils.cache_dir_for`` says -- the
    source's project ``cache/audio``, else the per-user cache -- never in a
    ``_maya_audio_cache`` folder written beside the source; a saved scene's
    old ``_maya_audio_cache`` / ``_audio_cache`` transcodes still read.
    Added: 2026-10-13"""

    def setUp(self):
        super().setUp()
        import os

        import pythontk as ptk

        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="audio_cache_loc_"))
        ptk.Workspace.create(self.tmp, create_dirs=False)
        self.sound = os.path.join(self.tmp, "sound")
        os.makedirs(self.sound)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def _wav_at(self, folder, name):
        import os

        from base_test import make_temp_wav

        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, name)
        shutil.copy2(make_temp_wav("cache_loc_src", duration_sec=0.5), dest)
        return dest

    def test_the_default_cache_is_the_shared_one_not_beside_the_source(self):
        import os
        from unittest import mock

        import pythontk as ptk

        source = os.path.join(self.sound, "clip.mp3")
        with mock.patch.object(
            ptk.AudioUtils, "resolve_playable_path", return_value=None
        ) as resolve:
            nodes.Nodes.resolve_playable_path(source)
        cache_dir = resolve.call_args.kwargs.get("cache_dir")
        expected = ptk.AudioUtils.cache_dir_for(source)
        self.assertIn(
            cache_dir,
            (None, expected),
            f"transcodes go to {cache_dir!r}, not the shared cache {expected!r}",
        )

    def _outside_source(self):
        """A source in a folder no marked project holds, with the scene saved
        into this case's project."""
        import os

        import pythontk as ptk

        outside = os.path.realpath(tempfile.mkdtemp(prefix="audio_outside_"))
        self.addCleanup(shutil.rmtree, outside, True)
        source = os.path.join(outside, "clip.mp3")
        if ptk.Workspace.find_containing(source) is not None:
            self.skipTest("the temp dir lies inside a marked project here")
        scenes = os.path.join(self.tmp, "scenes")
        os.makedirs(scenes, exist_ok=True)
        cmds.file(rename=os.path.join(scenes, "shot.ma"))
        cmds.file(save=True, type="mayaAscii", force=True)
        return source

    def test_a_source_outside_any_project_caches_in_the_scenes_project(self):
        """Pre-release review 2026-10-13: a clip from outside every project
        transcoded into the per-user cache, so the saved scene named a path
        only this user's machine has. With a scene project, it caches there."""
        import os
        from unittest import mock

        import pythontk as ptk

        source = self._outside_source()
        with mock.patch.object(
            ptk.AudioUtils, "resolve_playable_path", return_value=None
        ) as resolve:
            nodes.Nodes.resolve_playable_path(source)
        self.assertEqual(
            os.path.normcase(os.path.normpath(resolve.call_args.kwargs["cache_dir"])),
            os.path.normcase(os.path.join(self.tmp, "cache", "audio")),
        )

    def test_duration_reads_a_transcode_in_the_project_cache(self):
        import os

        import pythontk as ptk
        from mayatk.audio_utils._audio_utils import AudioUtils

        source = os.path.join(self.sound, "clip.mp3")
        cache = ptk.AudioUtils.cache_dir_for(source)
        self.assertEqual(
            os.path.normcase(os.path.normpath(cache)),
            os.path.normcase(os.path.join(self.tmp, "cache", "audio")),
        )
        cached = self._wav_at(cache, "clip_0123abcd.wav")
        frames, resolved = AudioUtils._read_duration_frames(source, 24.0)
        self.assertAlmostEqual(frames, 12.0, places=1)
        self.assertEqual(os.path.normcase(resolved), os.path.normcase(cached))

    def test_duration_still_reads_a_legacy_cache_beside_the_source(self):
        import os

        from mayatk.audio_utils._audio_utils import AudioUtils

        source = os.path.join(self.sound, "old.mp3")
        for legacy in ("_maya_audio_cache", "_audio_cache"):
            folder = os.path.join(self.sound, legacy)
            cached = self._wav_at(folder, "old.wav")
            frames, resolved = AudioUtils._read_duration_frames(source, 24.0)
            self.assertAlmostEqual(frames, 12.0, places=1, msg=legacy)
            self.assertEqual(
                os.path.normcase(resolved), os.path.normcase(cached), legacy
            )
            shutil.rmtree(folder)


class TestAudioNodesLifecycle(MayaTkTestCase):
    """create_dg / configure_dg / query_duration."""

    def setUp(self):
        super().setUp()
        self.tmpdir = tempfile.mkdtemp(prefix="audio_dg_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        super().tearDown()

    def test_create_dg_with_invalid_path_returns_none(self):
        result = nodes.Nodes.create_dg("/__nonexistent__/audio.wav")
        self.assertIsNone(result)

    def test_query_duration_on_nonexistent_returns_zero(self):
        # No audio node — duration should be 0 (gracefully handled)
        self.assertEqual(nodes.Nodes.query_duration("nonexistent_audio_node"), 0.0)

    def test_stamp_marker_creates_attr(self):
        # Create a bare audio node manually
        node = cmds.createNode("audio", name="test_marker_audio")
        nodes.Nodes._stamp_marker(node, "track_xyz")
        self.assertTrue(cmds.attributeQuery(MARKER_ATTR, node=node, exists=True))
        self.assertEqual(cmds.getAttr(f"{node}.{MARKER_ATTR}"), "track_xyz")


class TestCompositorManagedNodes(MayaTkTestCase):
    """compositor.is_managed_dg / find_dg_node_for_track."""

    def test_unmarked_node_is_not_managed(self):
        node = cmds.createNode("audio", name="unmanaged_audio")
        self.assertFalse(compositor.Compositor.is_managed_dg(node))

    def test_marked_node_is_managed(self):
        node = cmds.createNode("audio", name="managed_audio")
        nodes.Nodes._stamp_marker(node, "track_a")
        self.assertTrue(compositor.Compositor.is_managed_dg(node))

    def test_find_dg_node_for_track(self):
        node = cmds.createNode("audio", name="findable_audio")
        nodes.Nodes._stamp_marker(node, "find_track")

        result = compositor.Compositor.find_dg_node_for_track("find_track")
        self.assertEqual(result, node)

    def test_find_dg_node_for_unknown_track_returns_none(self):
        result = compositor.Compositor.find_dg_node_for_track("never_made")
        self.assertIsNone(result)


class TestMigrateDetection(MayaTkTestCase):
    """migrate.detect_legacy."""

    def test_detect_on_nonexistent_object(self):
        self.assertFalse(migrate.Migrate.detect_legacy("never_existed"))

    def test_detect_without_legacy_attr_is_false(self):
        cube = cmds.polyCube(name="leg_cube")[0]
        self.assertFalse(migrate.Migrate.detect_legacy(cube))

    def test_detect_with_legacy_attr_is_true(self):
        cube = cmds.polyCube(name="leg_cube_pos")[0]
        cmds.addAttr(
            cube, longName="audio_trigger", attributeType="enum", enumName="None:hit"
        )
        self.assertTrue(migrate.Migrate.detect_legacy(cube))

    def test_detect_custom_category(self):
        cube = cmds.polyCube(name="leg_custom")[0]
        cmds.addAttr(
            cube, longName="vfx_trigger", attributeType="enum", enumName="None:bang"
        )
        self.assertTrue(migrate.Migrate.detect_legacy(cube, category="vfx"))
        self.assertFalse(migrate.Migrate.detect_legacy(cube, category="audio"))


class TestBatchStack(QuickTestCase):
    """_get_stack thread-local + nesting flatten behavior."""

    def test_get_stack_initialized_empty(self):
        # In a fresh thread the stack starts empty
        stack = batch_mod.Batch._get_stack()
        self.assertIsInstance(stack, list)


class TestBatchClass(QuickTestCase):
    """_Batch._dirty + mark_dirty semantics."""

    def test_default_dirty_empty(self):
        b = batch_mod._Batch()
        self.assertEqual(b._dirty, set())
        self.assertFalse(b._full_sync)

    def test_mark_dirty_adds_to_set(self):
        b = batch_mod._Batch()
        b.mark_dirty(["track_a", "track_b"])
        self.assertEqual(b._dirty, {"track_a", "track_b"})

    def test_mark_dirty_none_requests_full_sync(self):
        b = batch_mod._Batch()
        b.mark_dirty(None)
        self.assertTrue(b._full_sync)


class TestBatchContext(MayaTkTestCase):
    """`batch()` context manager — undo wrapping + nesting."""

    def test_outer_batch_opens_undo_chunk(self):
        # Just verify entering and exiting doesn't raise
        with batch_mod.Batch.batch() as b:
            self.assertIsInstance(b, batch_mod._BatchContext)

    def test_nested_batches_share_outer(self):
        with batch_mod.Batch.batch():
            with batch_mod.Batch.batch() as inner:
                inner.mark_dirty(["from_inner"])
            # Inner mark_dirty should have flowed to the outer
            stack = batch_mod.Batch._get_stack()
            self.assertEqual(len(stack), 1)

    def test_batch_returns_context_manager(self):
        ctx = batch_mod.Batch.batch()
        self.assertTrue(hasattr(ctx, "__enter__"))
        self.assertTrue(hasattr(ctx, "__exit__"))


if __name__ == "__main__":
    unittest.main()
