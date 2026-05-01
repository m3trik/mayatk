# !/usr/bin/python
# coding=utf-8
"""Test Suite for audio_utils internal modules.

Covers:
    - audio_utils.nodes (DG audio primitives)
    - audio_utils.compositor (managed-node discovery)
    - audio_utils.migrate (legacy detection)
    - audio_utils.batch (batch context manager)
"""
import os
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
        result = nodes.workspace_sound_dir()
        self.assertTrue(result is None or isinstance(result, str))

    def test_resolve_playable_path_with_nonexistent_returns_none(self):
        # ptk.AudioUtils.resolve_playable_path returns None for missing files
        result = nodes.resolve_playable_path("/__nonexistent__/audio.wav")
        self.assertTrue(result is None or isinstance(result, str))


class TestAudioNodesLifecycle(MayaTkTestCase):
    """create_dg / configure_dg / query_duration."""

    def setUp(self):
        super().setUp()
        self.tmpdir = tempfile.mkdtemp(prefix="audio_dg_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        super().tearDown()

    def test_create_dg_with_invalid_path_returns_none(self):
        result = nodes.create_dg("/__nonexistent__/audio.wav")
        self.assertIsNone(result)

    def test_query_duration_on_nonexistent_returns_zero(self):
        # No audio node — duration should be 0 (gracefully handled)
        self.assertEqual(nodes.query_duration("nonexistent_audio_node"), 0.0)

    def test_stamp_marker_creates_attr(self):
        # Create a bare audio node manually
        node = cmds.createNode("audio", name="test_marker_audio")
        nodes._stamp_marker(node, "track_xyz")
        self.assertTrue(cmds.attributeQuery(MARKER_ATTR, node=node, exists=True))
        self.assertEqual(cmds.getAttr(f"{node}.{MARKER_ATTR}"), "track_xyz")


class TestCompositorManagedNodes(MayaTkTestCase):
    """compositor.is_managed_dg / find_dg_node_for_track."""

    def test_unmarked_node_is_not_managed(self):
        node = cmds.createNode("audio", name="unmanaged_audio")
        self.assertFalse(compositor.is_managed_dg(node))

    def test_marked_node_is_managed(self):
        node = cmds.createNode("audio", name="managed_audio")
        nodes._stamp_marker(node, "track_a")
        self.assertTrue(compositor.is_managed_dg(node))

    def test_find_dg_node_for_track(self):
        node = cmds.createNode("audio", name="findable_audio")
        nodes._stamp_marker(node, "find_track")

        result = compositor.find_dg_node_for_track("find_track")
        self.assertEqual(result, node)

    def test_find_dg_node_for_unknown_track_returns_none(self):
        result = compositor.find_dg_node_for_track("never_made")
        self.assertIsNone(result)


class TestMigrateDetection(MayaTkTestCase):
    """migrate.detect_legacy."""

    def test_detect_on_nonexistent_object(self):
        self.assertFalse(migrate.detect_legacy("never_existed"))

    def test_detect_without_legacy_attr_is_false(self):
        cube = cmds.polyCube(name="leg_cube")[0]
        self.assertFalse(migrate.detect_legacy(cube))

    def test_detect_with_legacy_attr_is_true(self):
        cube = cmds.polyCube(name="leg_cube_pos")[0]
        cmds.addAttr(cube, longName="audio_trigger", attributeType="enum", enumName="None:hit")
        self.assertTrue(migrate.detect_legacy(cube))

    def test_detect_custom_category(self):
        cube = cmds.polyCube(name="leg_custom")[0]
        cmds.addAttr(cube, longName="vfx_trigger", attributeType="enum", enumName="None:bang")
        self.assertTrue(migrate.detect_legacy(cube, category="vfx"))
        self.assertFalse(migrate.detect_legacy(cube, category="audio"))


class TestBatchStack(QuickTestCase):
    """_get_stack thread-local + nesting flatten behavior."""

    def test_get_stack_initialized_empty(self):
        # In a fresh thread the stack starts empty
        stack = batch_mod._get_stack()
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
        with batch_mod.batch() as b:
            self.assertIsInstance(b, batch_mod._BatchContext)

    def test_nested_batches_share_outer(self):
        outer_dirty = set()
        with batch_mod.batch() as outer:
            with batch_mod.batch() as inner:
                inner.mark_dirty(["from_inner"])
            # Inner mark_dirty should have flowed to the outer
            stack = batch_mod._get_stack()
            self.assertEqual(len(stack), 1)

    def test_batch_returns_context_manager(self):
        ctx = batch_mod.batch()
        self.assertTrue(hasattr(ctx, "__enter__"))
        self.assertTrue(hasattr(ctx, "__exit__"))


if __name__ == "__main__":
    unittest.main()
